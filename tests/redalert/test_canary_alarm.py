"""The canary alarm: the money turned to defenses while the enemy's army heads for our base, and when it ends."""

from __future__ import annotations

import dataclasses

import situations
from situations import ask, base_state
from ucla_agent.games.redalert import base_defense, canary_alarm
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.batch import Priority
from ucla_agent.games.redalert.canary_alarm import CanaryAlarm
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MAJOR_THREAT, NO_THREAT
from ucla_agent.games.redalert.tuning import Tuning, UpkeepTuning
from ucla_agent.games.redalert.upkeep import UpkeepManager

ALLIED_BASE = (("fact", 22, 48), ("powr", 19, 52), ("tent", 18, 44), ("proc", 26, 52), ("weap", 26, 44))
SOVIET_BASE = (("fact", 22, 48), ("powr", 19, 52), ("barr", 18, 44), ("proc", 26, 52), ("weap", 26, 44))
# 8 cells from our base at (22, 48) toward the enemy's start at (74, 48).
GOAL = (30, 48)
OFF = dataclasses.replace(Tuning(), upkeep=dataclasses.replace(Tuning().upkeep, canary_alarm=False))
SECOND = TICKS_PER_SECOND
# Two harvesters for the one refinery: none is owed one.
HARVESTERS = [
    {"id": 350, "type": "harv", "x": 28, "y": 52, "hp": 100, "act": None, "idle": True},
    {"id": 351, "type": "harv", "x": 29, "y": 52, "hp": 100, "act": None, "idle": True},
]


def own(unit_id: int, unit_type: str, x: int, y: int) -> dict:
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": 100, "act": None, "idle": True}


def foe(enemy_id: int, enemy_type: str, x: int, y: int) -> dict:
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


def tanks(x: int, y: int = 48, count: int = 6) -> list[dict]:
    return [own(300 + n, "2tnk", x + n % 3, y + n // 3) for n in range(count)]


class FixedPlan:
    """A plan selector that always sets one plan."""

    def __init__(self, plan: str) -> None:
        self.plan = plan

    def select(self, world, ctx, choice) -> str:
        return self.plan


class Scene:
    """An agent with the managers given under a fixed plan, one cycle at a time."""

    def __init__(self, *managers, tuning: Tuning = Tuning(), faction: str = "england", base=ALLIED_BASE) -> None:
        self.agent = situations.new_agent(*managers, tuning=tuning, plan_selector=FixedPlan("PRESS"))
        self.faction, self.base = faction, base

    def cycle(self, tick: int, units, **seen) -> list[dict]:
        return ask(self.agent, base_state(self.faction, *self.base, tick=tick, units=list(units), **seen))

    @property
    def ctx(self):
        return self.agent.ctx

    @property
    def world(self):
        return self.agent.world


class Watched:
    """The alarm alone, followed on an agent's world after each cycle, the match context set as a test says."""

    def __init__(self, tuning: Tuning = Tuning()) -> None:
        self.scene = Scene(tuning=tuning)
        self.alarm = CanaryAlarm()

    def cycle(self, tick: int, units=(), *, threat: str = NO_THREAT, out: bool = True, **seen) -> bool:
        scene = self.scene
        scene.cycle(tick, units or tanks(60), **seen)
        scene.ctx.threat_level, scene.ctx.attack_out = threat, out
        self.alarm.follow(scene.world, scene.ctx)
        return scene.ctx.canary_alarm

    def lose_canary(self, tick: int, *, while_out: bool = True) -> None:
        self.scene.ctx.canary_lost_tick, self.scene.ctx.canary_lost_while_out = tick, while_out

    @property
    def ctx(self):
        return self.scene.ctx


def alarm_events(match_record, phase: str) -> list[dict]:
    return [
        event
        for event in match_record("base_defense")
        if event.get("reason") == canary_alarm.CANARY_ALARM and event["phase"] == phase
    ]


# -- start ----------------------------------------------------------------------------------------------


def test_canary_lost_while_out_starts_alarm(match_record):
    watched = Watched()
    watched.cycle(1000)
    watched.lose_canary(1005)

    started = watched.cycle(1005)
    watched.lose_canary(1100)
    watched.cycle(1100)

    assert started and watched.ctx.canary_alarm_since == 1005
    assert watched.ctx.canary_alarm_reason == canary_alarm.CANARY_LOST
    # An alarm in force is not started again: it keeps its tick.
    assert len(alarm_events(match_record, canary_alarm.START)) == 1


def test_canary_lost_at_home_starts_nothing():
    watched = Watched()
    watched.lose_canary(1005, while_out=False)

    assert watched.cycle(1005) is False


def test_a_new_crossing_starts_the_alarm():
    watched = Watched()
    watched.ctx.armies_crossed_tick = 1005

    assert watched.cycle(1005) is True and watched.ctx.canary_alarm_reason == canary_alarm.ARMIES_CROSSED


# -- in force -------------------------------------------------------------------------------------------


def test_alarm_orders_defense_when_queue_free():
    allied = Scene(BaseDefenseManager())
    allied.ctx.canary_lost_tick, allied.ctx.canary_lost_while_out = 1000, True
    allied_orders = allied.cycle(1000, tanks(60), visible_enemies=[foe(500, "2tnk", 70, 20)])
    soviet = Scene(BaseDefenseManager(), faction="russia", base=SOVIET_BASE)
    soviet.ctx.canary_lost_tick, soviet.ctx.canary_lost_while_out = 1000, True
    soviet_orders = soviet.cycle(1000, tanks(60), visible_enemies=[foe(500 + n, "e1", 70, 20 + n) for n in range(4)])

    assert {"cmd": "build", "item": "gun"} in allied_orders and allied.ctx.defense_goals["gun"] == GOAL
    assert {"cmd": "build", "item": "ftur"} in soviet_orders and soviet.ctx.defense_goals["ftur"] == GOAL


def test_alarm_defense_is_an_emergency_and_waits_for_a_busy_defense_queue():
    scene = Scene()
    scene.cycle(1000, tanks(60))
    scene.ctx.canary_alarm = True

    (intent,) = base_defense._alarm_defense(scene.world, scene.ctx)
    busy = base_defense._alarm_defense(scene.world, scene.ctx)

    assert intent.order == {"cmd": "build", "item": "gun"} and intent.priority == Priority.EMERGENCY
    assert busy == []


def test_alarm_pauses_unit_queues():
    scene = Scene(UpkeepManager())
    units = [*tanks(60), *HARVESTERS]
    # The first cycle orders the power plant the base is short of.
    scene.cycle(1000, units, cash=5000)

    scene.ctx.canary_alarm = True
    during = [scene.cycle(1005 + 5 * n, units, cash=5000) for n in range(3)]
    scene.ctx.canary_alarm = False
    after = scene.cycle(1020, units, cash=5000)

    def training(orders):
        return [order for order in orders if order["cmd"] == "train"]

    assert all(training(orders) == [] for orders in during) and training(after)


def test_alarm_trains_a_harvester_for_a_refinery_that_has_none():
    scene = Scene()
    scene.cycle(1000, tanks(60))
    scene.ctx.canary_alarm = True
    production = scene.ctx.production

    kept = canary_alarm.held([production.train("harv")], scene.ctx)
    scene.cycle(1005, [*tanks(60), own(350, "harv", 28, 52)])
    scene.ctx.canary_alarm = True
    held = canary_alarm.held([scene.ctx.production.train("harv")], scene.ctx)

    assert len(kept) == 1 and held == []


def test_alarm_holds_mcv_and_extra_war_factory():
    scene = Scene()
    scene.cycle(1000, tanks(60))
    scene.ctx.canary_alarm = True
    production = scene.ctx.production
    held_off = [production.train("mcv"), production.build("weap"), production.build("dome")]
    going_on = [production.build("powr"), production.build("proc"), production.build("pbox")]

    assert canary_alarm.held([*held_off, *going_on], scene.ctx) == going_on
    scene.ctx.canary_alarm = False
    assert canary_alarm.held(held_off, scene.ctx) == held_off


# -- end ------------------------------------------------------------------------------------------------


def test_alarm_ends_beaten(match_record):
    watched = Watched()
    watched.lose_canary(1000)
    watched.cycle(1000, threat=MAJOR_THREAT)

    held = watched.cycle(1005, threat=MAJOR_THREAT)
    ended = watched.cycle(1010)

    assert held is True and ended is False and watched.ctx.canary_alarm_ended == canary_alarm.BEATEN
    assert alarm_events(match_record, canary_alarm.END)[0]["why"] == canary_alarm.BEATEN


def test_alarm_ends_gone_after_quiet():
    watched = Watched()
    watched.lose_canary(1000)
    watched.cycle(1000)
    closing = [foe(500 + n, "2tnk", 45, 46 + n) for n in range(2)]

    before = watched.cycle(1000 + 75 * SECOND - 1)
    kept_by_army = watched.cycle(1000 + 75 * SECOND, visible_enemies=closing)
    gone = watched.cycle(1000 + 120 * SECOND)

    assert before is True and kept_by_army is True
    assert gone is False and watched.ctx.canary_alarm_ended == canary_alarm.GONE


def test_alarm_ends_army_home():
    watched = Watched()
    watched.lose_canary(1000)
    watched.cycle(1000, threat=MAJOR_THREAT)

    away = watched.cycle(1005, tanks(60), threat=MAJOR_THREAT, out=False)
    home = watched.cycle(1010, tanks(26), threat=MAJOR_THREAT, out=False)

    assert away is True and home is False and watched.ctx.canary_alarm_ended == canary_alarm.ARMY_HOME


def test_alarm_ends_timeout_180s():
    watched = Watched()
    watched.lose_canary(1000)
    watched.cycle(1000, threat=MAJOR_THREAT)

    before = watched.cycle(1000 + 180 * SECOND - 1, threat=MAJOR_THREAT)
    after = watched.cycle(1000 + 180 * SECOND, threat=MAJOR_THREAT)

    assert before is True and after is False and watched.ctx.canary_alarm_ended == canary_alarm.TIMEOUT


def test_alarm_never_orders_main():
    scene = Scene(BaseDefenseManager(), UpkeepManager())
    scene.ctx.canary_lost_tick, scene.ctx.canary_lost_while_out = 1000, True

    orders = scene.cycle(1000, tanks(60), cash=5000)

    assert scene.ctx.canary_alarm is True
    assert not [order for order in orders if set(order.get("units", ())) & set(range(300, 306))]


def test_switch_off_no_alarm():
    scene = Scene(BaseDefenseManager(), UpkeepManager(), tuning=OFF)
    units = [*tanks(60), *HARVESTERS]
    scene.cycle(1000, units, cash=5000)
    scene.ctx.canary_lost_tick, scene.ctx.canary_lost_while_out = 1005, True

    orders = scene.cycle(1005, units, cash=5000)

    assert scene.ctx.canary_alarm is False
    assert [order for order in orders if order["cmd"] == "train"]


def test_tuning_canary_alarm_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).upkeep, UpkeepTuning()):
        assert tuning.canary_alarm is True
        assert (tuning.canary_defense_goal_cells, tuning.canary_alarm_quiet_seconds) == (8, 75)
        assert tuning.canary_alarm_max_seconds == 180
