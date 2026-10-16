"""The armies crossing: main commits to the base trade, sieges, or goes home by a clear reverse route."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import situations
from situations import StubChooser, ask, base_state
from ucla_agent.games.redalert import cross_commit
from ucla_agent.games.redalert.attack_approach import Approach, AttackWatch
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.canary_alarm import ARMIES_CROSSED, CanaryAlarm
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.command_army import GATHER
from ucla_agent.games.redalert.command_prompt import Action
from ucla_agent.games.redalert.commander import CROSS_COMMIT, MAIN_ROLE, Commander
from ucla_agent.games.redalert.cross_commit import CrossCommit
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.tuning import CombatTuning, CommanderTuning, Tuning

YARD, POWER = ("fact", 22, 48), ("powr", 19, 52)
SHIPPED = dataclasses.replace(Tuning(), combat=dataclasses.replace(Tuning().combat, vehicle_repair=False))
OFF = dataclasses.replace(SHIPPED, combat=dataclasses.replace(SHIPPED.combat, commit_on_cross=False))
# Our base at (22, 48), its staging point at (32, 48), the enemy's start at (74, 48) with its yard there.
STAGING, ENEMY_YARD = (32, 48), (74, 48)
DEEP = (60, 48)


def own(unit_id: int, unit_type: str, x: int, y: int, hp: int = 100) -> dict:
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": hp, "act": None, "idle": True}


def foe(enemy_id: int, enemy_type: str, x: int, y: int) -> dict:
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


def tanks(x: int, y: int = 48, count: int = 6, hp: int = 100, unit_type: str = "2tnk") -> list[dict]:
    return [own(300 + n, unit_type, x + n % 3, y + n // 3, hp) for n in range(count)]


def artillery(x: int, y: int = 48, count: int = 3, hp: int = 100) -> list[dict]:
    return [own(320 + n, "arty", x, y + n, hp) for n in range(count)]


YARD_SEEN = foe(900, "fact", *ENEMY_YARD)


class Scene:
    """An agent with the managers given, the crossing followed on its world after each cycle by a commit of its own."""

    def __init__(self, *managers, tuning: Tuning = SHIPPED) -> None:
        self.agent = situations.new_agent(*managers, tuning=tuning)
        self.watch = AttackWatch()
        self.commit = CrossCommit()

    def cycle(self, tick: int, units, enemies=(), buildings=(YARD_SEEN,)) -> list[dict]:
        state = base_state(
            "england",
            YARD,
            POWER,
            tick=tick,
            units=list(units),
            visible_enemies=list(enemies),
            visible_enemy_buildings=list(buildings),
        )
        return ask(self.agent, state)

    def main(self) -> list:
        return [self.world.combat_units[unit_id] for unit_id in self.ctx.groups.members(MAIN)]

    def set_out(self, tick: int, units, **seen) -> None:
        self.cycle(tick, units, **seen)
        self.watch.set_out(ENEMY_YARD, self.main(), self.world, self.ctx)

    def follow(self, tick: int, units, enemies=(), buildings=(YARD_SEEN,)) -> str | None:
        self.cycle(tick, units, enemies, buildings)
        self.commit.follow(self.main(), self.watch, STAGING, self.world, self.ctx)
        return self.commit.decision

    @property
    def ctx(self):
        return self.agent.ctx

    @property
    def world(self):
        return self.agent.world


def decision_of(match_record) -> dict:
    (event,) = [event for event in match_record("combat") if event["reason"] == cross_commit.CROSS_DECISION]
    return event


def crossed_at(scene: Scene, units, enemies=(), buildings=(YARD_SEEN,), lost: int = 1100) -> str | None:
    """main set out at 1000 and followed at 1100 with the canary lost at `lost`."""
    scene.set_out(1000, tanks(31))
    scene.ctx.canary_lost_tick = lost
    return scene.follow(1100, units, enemies, buildings)


# -- the crossing ---------------------------------------------------------------------------------------


def test_crossing_needs_ours_nearer_their_base(match_record):
    scene = Scene()

    decision = crossed_at(scene, tanks(40))

    assert decision is None and scene.ctx.armies_crossed is False
    assert not [event for event in match_record("combat") if event["reason"] == cross_commit.ARMIES_CROSSED]


def test_canary_loss_while_out_is_a_crossing_signal(match_record):
    before = Scene()
    since = crossed_at(Scene(), tanks(*DEEP))

    assert crossed_at(before, tanks(*DEEP), lost=900) is None
    assert since is not None
    (event,) = [event for event in match_record("combat") if event["reason"] == cross_commit.ARMIES_CROSSED]
    assert event["signals"] == [cross_commit.CANARY_LOST]


def test_closing_army_is_a_crossing_signal():
    scene = Scene()
    scene.set_out(1000, tanks(31))
    scene.cycle(1100, tanks(*DEEP))
    closing = [foe(500 + n, "2tnk", 40, 46 + n) for n in range(3)]
    scene.cycle(1200, tanks(*DEEP), closing)

    signals = scene.commit.signals(scene.main(), scene.watch.since, scene.world, scene.ctx)

    assert cross_commit.CLOSING in signals and cross_commit.ARMY_SEEN in signals


# -- the decision ---------------------------------------------------------------------------------------

# Four pillboxes and an army around their yard: their base is costly for six medium tanks.
GUARDED = [
    YARD_SEEN,
    *[foe(910 + n, "pbox", 72 + 4 * (n % 2), 45 + 6 * (n // 2)) for n in range(4)],
]
GUARD_ARMY = [foe(600 + n, "3tnk", 75, 44 + n) for n in range(4)]


def test_forces_on_reverse_route_mean_commit(match_record):
    scene = Scene()
    on_route = [foe(500 + n, "2tnk", 46, 52 + n) for n in range(2)]

    decision = crossed_at(scene, tanks(*DEEP), [*on_route, *GUARD_ARMY], GUARDED)

    assert decision == cross_commit.COMMIT
    event = decision_of(match_record)
    assert (event["why"], event["route_clear"]) == (cross_commit.FORCES_BETWEEN, False) and event["ratio"] < 1.2


def test_static_defenses_with_enough_artillery_siege(match_record):
    scene = Scene()
    pillboxes = [YARD_SEEN, foe(910, "pbox", 72, 46), foe(911, "pbox", 72, 50)]
    # Worn down to a quarter, six tanks and three artillery pieces are worth under 1.2 times the two pillboxes.
    worn = [*tanks(*DEEP, hp=25), *artillery(57, hp=25)]

    decision = crossed_at(scene, worn, buildings=pillboxes)

    assert decision == cross_commit.COMMIT_SIEGE and scene.commit.siege_defenses == {910, 911}
    assert decision_of(match_record)["why"] == cross_commit.ARTILLERY_SIEGE


def test_beatable_base_commits(match_record):
    scene = Scene()
    lone = [YARD_SEEN, foe(910, "pbox", 72, 46)]

    decision = crossed_at(scene, tanks(*DEEP), buildings=lone)

    event = decision_of(match_record)
    assert decision == cross_commit.COMMIT and event["why"] == cross_commit.BASE_BEATABLE and event["ratio"] >= 1.2
    assert event["route_clear"] is True


def test_clear_route_costly_base_short_artillery_goes_home(match_record):
    scene = Scene()
    scene.set_out(1000, tanks(31))
    scene.watch.approach = Approach("right", (32, 48), ENEMY_YARD, (62, 62))
    scene.watch.has_passed = True
    scene.ctx.canary_lost_tick = 1100

    decision = scene.follow(1100, [*tanks(*DEEP, hp=40), *artillery(57, count=1)], GUARD_ARMY, GUARDED)

    event = decision_of(match_record)
    assert decision == cross_commit.HOME and event["why"] == cross_commit.CLEAR_EXIT and event["ratio"] < 1.2
    assert event["route"] == [[62, 62], list(STAGING)]


def test_the_decision_is_taken_once_per_attack():
    scene = Scene()
    lone = [YARD_SEEN, foe(910, "pbox", 72, 46)]
    crossed_at(scene, tanks(*DEEP), buildings=lone)

    later = scene.follow(1200, tanks(*DEEP), GUARD_ARMY, GUARDED)

    assert later == cross_commit.COMMIT and scene.ctx.main_committed is True


# -- the commit -----------------------------------------------------------------------------------------


def test_committed_main_not_recalled_by_base_defense():
    raiders = [foe(500 + n, "3tnk", 24 + n, 50) for n in range(8)]

    def recalled(committed: bool) -> bool:
        agent = situations.new_agent(BaseDefenseManager(), tuning=SHIPPED)
        agent.ctx.main_committed = committed
        orders = ask(agent, base_state("england", YARD, POWER, tick=1000, units=tanks(*DEEP), visible_enemies=raiders))
        sent = any(set(order.get("units", ())) & set(range(300, 306)) for order in orders)
        return agent.ctx.groups.is_recalled(MAIN) or sent

    assert recalled(False) is True
    assert recalled(True) is False


def test_committed_main_ignores_retreat_override_and_model():
    commander = Commander(StubChooser(), CommanderTuning())
    commander.role = MAIN_ROLE
    gather = Action(GATHER, (GATHER,), "gather", "gather", cell=STAGING)
    scene = Scene()
    scene.cycle(1000, tanks(*DEEP))
    losing = SimpleNamespace(fight=0.2, centroid=DEEP)

    def taken(committed: bool) -> bool:
        commander.is_committed = committed
        for tick in range(1000, 1200, 5):
            scene.world.tick = tick
            commander._follow_retreat(losing, scene.world, scene.ctx)
        return commander.retreat.is_active

    assert commander._withheld_because(gather) is None
    assert taken(True) is False
    commander.is_committed = True
    assert commander._withheld_because(gather) == CROSS_COMMIT
    assert taken(False) is True


def test_committed_main_takes_the_commit_s_orders_not_the_standing_gather():
    manager = CombatManager()
    agent = situations.new_agent(manager, tuning=SHIPPED)
    state = base_state("england", YARD, POWER, tick=1000, units=tanks(*DEEP), visible_enemy_buildings=[YARD_SEEN])
    ask(agent, state)
    main = [agent.world.combat_units[unit_id] for unit_id in agent.ctx.groups.members(MAIN)]
    manager._attack.set_out(ENEMY_YARD, main, agent.world, agent.ctx)
    manager.command(Action(GATHER, (GATHER,), "gather", "gather", cell=STAGING))
    manager._cross.decision, manager._cross.target = cross_commit.COMMIT, ENEMY_YARD

    orders = ask(agent, {**state, "tick": 1005})

    assert orders == [{"cmd": "attack_move", "units": list(range(300, 306)), "to": list(ENEMY_YARD)}]
    assert agent.ctx.main_committed is True


def test_commit_ends_when_base_destroyed(match_record):
    scene = Scene()
    lone = [YARD_SEEN, foe(910, "pbox", 72, 46)]
    crossed_at(scene, tanks(*DEEP), buildings=lone)
    gone = [{**YARD_SEEN, "wreck": True}]

    scene.cycle(1200, tanks(70), buildings=gone)
    scene.world.enemy_memory.clear()
    scene.commit.follow(scene.main(), scene.watch, STAGING, scene.world, scene.ctx)

    assert scene.commit.decision is None and scene.ctx.main_committed is False
    ends = [event for event in match_record("combat") if event["reason"] == cross_commit.CROSS_END]
    assert [event["why"] for event in ends] == [cross_commit.BASE_DESTROYED]


def test_crossing_starts_canary_alarm():
    scene = Scene()
    crossed_at(scene, tanks(*DEEP))
    alarm = CanaryAlarm()
    scene.ctx.canary_lost_while_out = False

    alarm.follow(scene.world, scene.ctx)

    assert scene.ctx.armies_crossed is True and scene.ctx.canary_alarm is True
    assert scene.ctx.canary_alarm_reason in (ARMIES_CROSSED, "canary_lost")


def test_switch_off_no_commit(match_record):
    scene = Scene(tuning=OFF)

    decision = crossed_at(scene, tanks(*DEEP))

    assert decision is None and scene.ctx.armies_crossed is False and scene.ctx.main_committed is False
    assert not [event for event in match_record("combat") if event["reason"] == cross_commit.ARMIES_CROSSED]


def test_tuning_commit_on_cross_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).combat, CombatTuning()):
        assert tuning.commit_on_cross is True
        assert (tuning.cross_seen_seconds, tuning.cross_army_value, tuning.cross_route_clear_cells) == (10, 1000, 6)
        assert (tuning.cross_siege_min_long_range, tuning.cross_siege_per_defense) == (3, 0.5)
