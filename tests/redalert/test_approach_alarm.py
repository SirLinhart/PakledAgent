"""The approach alarm: defenses ordered when an enemy army is seen coming for our base, before it arrives."""

from __future__ import annotations

import dataclasses

import situations
from situations import ask, base_state
from ucla_agent.games.redalert import approach_alarm, base_defense
from ucla_agent.games.redalert.approach_alarm import ApproachAlarm
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.batch import Priority
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.tuning import BaseDefenseTuning, Tuning

ALLIED_BASE = (("fact", 22, 48), ("powr", 19, 52), ("tent", 18, 44), ("proc", 26, 52), ("weap", 26, 44))
SECOND = TICKS_PER_SECOND
# 8 cells from our base at (22, 48) toward a group on the row y = 48.
GOAL = (30, 48)
OFF = dataclasses.replace(Tuning(), base_defense=dataclasses.replace(Tuning().base_defense, approach_alarm=False))


def foe(enemy_id: int, enemy_type: str, x: int, y: int) -> dict:
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


def rifles(x: int, count: int = 6, first: int = 500) -> list[dict]:
    return [foe(first + n, "e1", x + n % 2, 48 + n // 2) for n in range(count)]


def guard() -> list[dict]:
    return [{"id": 300 + n, "type": "e1", "x": 23 + n, "y": 47, "hp": 100, "act": None, "idle": True} for n in range(2)]


class Scene:
    """An agent with base defense alone (or no manager), one cycle at a time."""

    def __init__(self, tuning: Tuning = Tuning(), *, defends: bool = True) -> None:
        managers = (BaseDefenseManager(),) if defends else ()
        self.agent = situations.new_agent(*managers, tuning=tuning)

    def cycle(self, tick: int, enemies=(), units=None) -> list[dict]:
        units = guard() if units is None else units
        return ask(
            self.agent, base_state("england", *ALLIED_BASE, tick=tick, units=units, visible_enemies=list(enemies))
        )

    @property
    def ctx(self):
        return self.agent.ctx

    @property
    def world(self):
        return self.agent.world


def alarm_events(match_record, phase: str) -> list[dict]:
    return [
        event
        for event in match_record("base_defense")
        if event.get("reason") == approach_alarm.APPROACH_ALARM and event["phase"] == phase
    ]


def builds(orders: list[dict]) -> list[str]:
    return [order["item"] for order in orders if order["cmd"] == "build"]


# -- start ----------------------------------------------------------------------------------------------


def test_rifles_marching_on_the_base_start_the_alarm_and_a_pillbox(match_record):
    scene = Scene()
    first = scene.cycle(1000, rifles(60))
    orders = scene.cycle(1000 + 2 * SECOND, rifles(57))

    assert builds(first) == [] and scene.ctx.approach_alarm is True
    assert builds(orders) == ["pbox"] and scene.ctx.defense_goals["pbox"] == GOAL
    (start,) = alarm_events(match_record, approach_alarm.START)
    assert start["value"] == 600 and start["composition"] == {"e1": 6}
    assert 34 <= start["distance"] <= 37


def test_a_group_standing_still_on_their_side_raises_nothing():
    scene = Scene()
    scene.cycle(1000, rifles(60))
    scene.cycle(1000 + 2 * SECOND, rifles(60))

    assert scene.ctx.approach_alarm is False


def test_a_group_on_our_side_of_the_map_raises_it_on_first_sight():
    scene = Scene()
    scene.cycle(1000, rifles(44))

    assert scene.ctx.approach_alarm is True and scene.ctx.approach_alarm_infantry is True


def test_a_small_or_far_group_raises_nothing():
    small, far = Scene(), Scene()
    small.cycle(1000, rifles(44, count=3))
    far.cycle(1000, rifles(70))
    far.cycle(1000 + 2 * SECOND, rifles(68))

    assert small.ctx.approach_alarm is False and far.ctx.approach_alarm is False


def test_tanks_worth_our_army_raise_it_with_a_gun():
    scene = Scene()
    tanks = [foe(600 + n, "2tnk", 44, 48 + n) for n in range(2)]
    orders = scene.cycle(1000, tanks)

    assert scene.ctx.approach_alarm is True and scene.ctx.approach_alarm_infantry is False
    assert builds(orders) == ["gun"]


def test_tanks_far_below_our_army_raise_nothing():
    scene = Scene()
    ours = [
        {"id": 300 + n, "type": "2tnk", "x": 24, "y": 46 + n, "hp": 100, "act": None, "idle": True} for n in range(6)
    ]
    scene.cycle(1000, [foe(600, "2tnk", 44, 48)], units=ours)

    assert scene.ctx.approach_alarm is False


# -- in force -------------------------------------------------------------------------------------------


def test_defense_is_an_emergency_waits_for_a_busy_queue_and_stops_at_the_groups_worth():
    scene = Scene(defends=False)
    scene.cycle(1000, rifles(44))
    alarm = ApproachAlarm()
    alarm.follow(scene.world, scene.ctx)

    (intent,) = base_defense._approach_defense(scene.world, scene.ctx, alarm)
    busy = base_defense._approach_defense(scene.world, scene.ctx, alarm)

    assert intent.order == {"cmd": "build", "item": "pbox"} and intent.priority == Priority.EMERGENCY
    # 600 worth of rifles calls for one defense, 1,500 a defense.
    assert busy == [] and alarm.is_defense_due() is False


def test_a_large_group_calls_for_up_to_three_defenses():
    scene = Scene()
    horde = [foe(500 + n, "e1", 40 + n % 5, 44 + n // 5) for n in range(40)]
    scene.cycle(1000, horde)
    scene.ctx.approach_alarm = False
    alarm = ApproachAlarm()
    alarm.follow(scene.world, scene.ctx)

    for _ in range(3):
        assert alarm.is_defense_due()
        alarm.ordered += 1
    assert alarm.is_defense_due() is False


def test_canary_alarm_in_force_leaves_the_defense_to_it():
    scene = Scene()
    scene.cycle(1000, rifles(44))
    scene.ctx.canary_alarm = True

    assert base_defense._approach_defense(scene.world, scene.ctx, ApproachAlarm()) == []


# -- end ------------------------------------------------------------------------------------------------


def test_alarm_ends_gone_after_30s_unseen(match_record):
    scene = Scene()
    scene.cycle(1000, rifles(44))
    scene.cycle(1000 + 29 * SECOND)
    held = scene.ctx.approach_alarm
    scene.cycle(1000 + 30 * SECOND)

    assert held is True and scene.ctx.approach_alarm is False
    (end,) = alarm_events(match_record, approach_alarm.END)
    assert end["why"] == approach_alarm.GONE and end["since"] == 1000


def test_alarm_ends_destroyed_when_nothing_of_the_group_is_remembered():
    scene = Scene()
    scene.cycle(1000, rifles(44))
    scene.ctx.approach_alarm = False
    alarm = ApproachAlarm()
    alarm.follow(scene.world, scene.ctx)
    scene.world.enemy_memory.clear()
    scene.world.tick += SECOND

    alarm.follow(scene.world, scene.ctx)

    assert scene.ctx.approach_alarm is False


def test_alarm_ends_timeout_after_120s():
    scene = Scene()
    for second in range(0, 121, 10):
        scene.cycle(1000 + second * SECOND, rifles(44))

    assert scene.ctx.approach_alarm is False


# -- switch ---------------------------------------------------------------------------------------------


def test_switch_off_raises_nothing():
    scene = Scene(tuning=OFF)
    orders = scene.cycle(1000, rifles(44))

    assert scene.ctx.approach_alarm is False and builds(orders) == []


def test_tuning_approach_alarm_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).base_defense, BaseDefenseTuning()):
        assert tuning.approach_alarm is True
        assert (tuning.approach_alarm_cells, tuning.approach_min_infantry, tuning.approach_min_value) == (45, 5, 700)
        assert (tuning.approach_value_per_defense, tuning.approach_defenses_max) == (1500, 3)
        assert (tuning.approach_gone_seconds, tuning.approach_max_seconds) == (30, 120)
