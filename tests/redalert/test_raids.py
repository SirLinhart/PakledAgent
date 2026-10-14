"""Raids: the raid group's quota and readiness, its targets, its route, and when it breaks off."""

from __future__ import annotations

import dataclasses
import math

import pytest

from fake_game import SECONDS_PER_MINUTE, STEPS_PER_SECOND, FakeRedAlert
from situations import (
    REPO_ROOT,
    OpeningThen,
    ScriptedManager,
    StubChooser,
    ask,
    base_state,
    event,
    tracked_items,
    with_command,
)
from switches_off import (
    SWITCHES_OFF,
    mix_off,
    new_agent,
    raids_off,
    scouting_off,
    settings_models_off,
    tuning_off,
    upkeep_off,
)
from ucla_agent.core.config import ConfigError
from ucla_agent.games.redalert import cells, plans, raid_routes, raid_targets
from ucla_agent.games.redalert.agent import RedAlertAgent
from ucla_agent.games.redalert.apc_raid import unload_route
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.batch import Priority
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.composition import (
    BASE_STAGE,
    DEPOT_STAGE,
    FINISH,
    HOLD,
    OPENING,
    PRESERVE,
    PRESS,
    RAID,
    STRIKE,
)
from ucla_agent.games.redalert.exposed_trucks import ExposedTrucks
from ucla_agent.games.redalert.fight import estimate
from ucla_agent.games.redalert.groups import GUARD, MAIN, SCOUT, Groups
from ucla_agent.games.redalert.groups import RAID as RAID_GROUP
from ucla_agent.games.redalert.raids import RaidManager, launch_blocker, quota, quota_units
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.items import TICKS_PER_SECOND, ItemTable
from ucla_agent.games.redalert.tuning import TUNING_FILE, CompositionTuning, RaidTuning, Tuning
from ucla_agent.games.redalert.upkeep import UpkeepManager
from ucla_agent.games.redalert.world import World

# 10 cells from the construction yard at (22, 48) toward the enemy's start at (74, 48).
STAGING = [32, 48]
# A whole Allied base: the opening's build, with a service depot, so that the mix's vehicle is the 2tnk.
ALLIED_BASE = (
    ("fact", 22, 48),
    ("powr", 19, 52),
    ("powr", 16, 52),
    ("tent", 18, 44),
    ("proc", 26, 44),
    ("proc", 26, 52),
    ("weap", 18, 56),
    ("fix", 14, 56),
)
SOVIET_BARRACKS = (("fact", 22, 48), ("powr", 19, 52), ("powr", 16, 52), ("barr", 18, 44))


class FixedPlan:
    """A plan selector that keeps the agent in one plan, as a test sets it."""

    def __init__(self, plan: str) -> None:
        self.plan = plan

    def select(self, world, ctx, choice) -> str:
        return self.plan


def own(unit_id: int, unit_type: str, x: int, y: int, hp: int = 100, *, idle: bool = True) -> dict:
    """One own unit as an observation lists it."""
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": hp, "act": None if idle else "move", "idle": idle}


def foe(enemy_id: int, enemy_type: str, x: int, y: int, hp: int = 100) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": hp}


def raid_agent(plan: str = RAID, *more) -> tuple:
    """An agent in a fixed plan with the raid manager and the managers given, and the raid manager itself."""
    manager = RaidManager()
    return new_agent(*more, manager, plan_selector=FixedPlan(plan)), manager


def allied(tick: int = 1000, units=(), enemies=(), buildings=(), base=ALLIED_BASE, **observation) -> dict:
    """A state of a whole Allied base with these own units and these enemies in sight."""
    shown = {"units": list(units), "visible_enemies": list(enemies), "visible_enemy_buildings": list(buildings)}
    return base_state("england", *base, tick=tick, **shown, **observation)


def harvesters() -> list[dict]:
    """Two harvesters for each refinery of the Allied base, so that upkeep makes no more."""
    return [own(200 + number, "harv", 30, 40 + number, idle=False) for number in range(4)]


def light_tanks(count: int, gathered: int | None = None, first_id: int = 300) -> list[dict]:
    """`count` light tanks, the first `gathered` (all by default) within 6 cells of the staging point."""
    gathered = count if gathered is None else gathered
    return [
        own(first_id + number, "1tnk", *((32, 46 + number) if number < gathered else (45, 40 + number)))
        for number in range(count)
    ]


def trained(orders: list[dict]) -> list[tuple[str, int]]:
    return [(order["item"], order["count"]) for order in with_command(orders, "train")]


# -- the [raids] keys --------------------------------------------------------------------------------


def test_the_tracked_raids_table_holds_the_toggle_the_refinery_gate_and_the_break_off_ratio_at_their_defaults():
    raids = Tuning.from_file(REPO_ROOT).raids

    assert (raids.enabled, raids.refinery_after_ticks, raids.refinery_kill_seconds) == (True, 1500, 20)
    assert raids.raid_break_off_ratio == 0.7 and raids == RaidTuning()


# -- the raid group: its quota and when it is ready ------------------------------------------------


@pytest.mark.parametrize("light_tanks_made, vehicle", [(0, "1tnk"), (3, "1tnk"), (4, "2tnk")])
def test_an_allied_agent_raiding_trains_light_tanks_until_four_exist(light_tanks_made, vehicle):
    agent, _ = raid_agent(RAID, UpkeepManager())

    orders = ask(agent, allied(units=[*harvesters(), *light_tanks(light_tanks_made)]))

    assert (vehicle, 1) in trained(orders) and ("1tnk" if vehicle == "2tnk" else "2tnk", 1) not in trained(orders)


def tanks_30_cells_forward() -> list[dict]:
    """Four fit light tanks 30 cells forward of the staging point (32, 48), toward the enemy's start."""
    return [own(300 + number, "1tnk", 62, 46 + number) for number in range(4)]


@pytest.mark.parametrize(
    "tanks, army, vehicle",
    [
        (light_tanks(4), frozenset(), "2tnk"),
        (tanks_30_cells_forward(), frozenset(), "1tnk"),
        ([own(300 + number, "1tnk", 32, 46 + number, hp=59) for number in range(4)], frozenset(), "1tnk"),
        (light_tanks(4), frozenset({300, 301, 302, 303}), "1tnk"),
    ],
    ids=["4 fit at the staging point", "4 in main 30 cells forward", "4 at 59% health", "4 in main's army"],
)
def test_the_allied_raid_quota_counts_only_the_light_tanks_of_main_the_raid_could_take_at_once(tanks, army, vehicle):
    # No raid manager: no light tank is in the raid group.
    agent = new_agent(UpkeepManager(), plan_selector=FixedPlan(RAID))
    agent.ctx.army = army

    orders = ask(agent, allied(units=[*harvesters(), *tanks]))

    assert (vehicle, 1) in trained(orders) and agent.ctx.groups.members(RAID_GROUP) == []


def test_the_raid_group_s_own_units_count_toward_the_quota_wherever_they_stand_and_whatever_their_health():
    agent = new_agent()
    forward_and_damaged = [own(300 + number, "1tnk", 62, 46 + number, hp=40) for number in range(2)]
    at_the_staging_point = [own(302, "1tnk", 32, 47), own(303, "1tnk", 32, 48), own(304, "1tnk", 32, 49, hp=59)]
    ask(agent, allied(units=[*forward_and_damaged, *at_the_staging_point, own(305, "1tnk", 62, 50)]))
    for unit_id in (300, 301):
        agent.ctx.groups.assign(unit_id, RAID_GROUP)

    # The two raiders, and the two fit light tanks of main at the staging point; not the damaged one nor the forward.
    assert quota_units(agent.ctx, agent.world, "1tnk") == 4 and quota_units(agent.ctx, agent.world, "jeep") == 0


def test_the_allied_raid_group_is_ready_only_when_all_four_light_tanks_are_at_the_staging_point():
    agent, manager = raid_agent()

    orders = ask(agent, allied(units=light_tanks(4, gathered=3)))
    three_there = (manager.ready, sorted(agent.ctx.groups.members(RAID_GROUP)), orders)
    ask(agent, allied(1005, units=light_tanks(4)))

    assert three_there == (False, [300, 301, 302, 303], [{"cmd": "move", "units": [303], "to": STAGING}])
    assert manager.ready


def test_three_light_tanks_at_the_staging_point_are_no_ready_raid_group():
    agent, manager = raid_agent()

    ask(agent, allied(units=light_tanks(3)))

    assert not manager.ready and len(agent.ctx.groups.members(RAID_GROUP)) == 3


# With the quota of six met, the mix (e1, e2, e3) is trained, and the army of grenadiers is furthest short of rifles.
@pytest.mark.parametrize("grenadiers, infantry", [(0, "e2"), (3, "e2"), (6, "e1")])
def test_a_soviet_agent_raiding_trains_grenadiers_until_six_exist(grenadiers, infantry):
    agent, _ = raid_agent(RAID, UpkeepManager())
    units = [own(300 + number, "e2", 32, 46 + number) for number in range(grenadiers)]

    orders = ask(agent, base_state("russia", *SOVIET_BARRACKS, units=units))

    assert trained(orders)[0][0] == infantry


def test_a_soviet_raid_group_without_a_war_factory_is_six_grenadiers():
    agent, manager = raid_agent()
    grenadiers = [own(300 + number, "e2", 32, 45 + number) for number in range(7)]

    ask(agent, base_state("russia", *SOVIET_BARRACKS, units=grenadiers))

    assert len(agent.ctx.groups.members(RAID_GROUP)) == 6 and manager.ready


@pytest.mark.parametrize(
    "faction, base, withheld, wanted",
    [
        ("england", ALLIED_BASE, (), {"1tnk": 4}),
        ("england", ALLIED_BASE, ("1tnk",), {"jeep": 4}),
        ("russia", SOVIET_BARRACKS, (), {"e2": 6}),
        ("russia", (*SOVIET_BARRACKS, ("weap", 18, 56)), (), {"ftrk": 0, "e2": 3}),
    ],
    ids=["allies", "allies without the light tank", "soviets on foot", "soviets with a war factory"],
)
def test_the_raid_quota_is_by_family_and_by_what_can_be_had(faction, base, withheld, wanted):
    agent, _ = raid_agent()
    ask(agent, base_state(faction, *base, withheld=withheld))

    assert quota(agent.world, CompositionTuning()) == wanted


def test_when_a_war_factory_stands_the_soviet_raid_gives_three_grenadiers_back_to_main():
    agent, _ = raid_agent()
    grenadiers = [own(300 + number, "e2", 32, 45 + number) for number in range(6)]
    ask(agent, base_state("russia", *SOVIET_BARRACKS, units=grenadiers))

    ask(agent, base_state("russia", *SOVIET_BARRACKS, ("weap", 18, 56), tick=1005, units=grenadiers))

    raiding, main = agent.ctx.groups.members(RAID_GROUP), agent.ctx.groups.members(MAIN)
    assert (len(raiding), len(main), sorted(raiding + main)) == (3, 3, list(range(300, 306)))


def test_no_unit_of_the_guard_the_scout_or_main_s_army_and_no_damaged_unit_is_taken_into_the_raid():
    agent, _ = raid_agent()
    groups = agent.ctx.groups
    groups.set_guard_quota("1tnk", 1)
    agent.ctx.army = frozenset({302})
    units = [*light_tanks(4), own(304, "1tnk", 33, 48, hp=59), own(305, "jeep", 32, 50)]
    ask(agent, allied(units=units, withheld=("1tnk",)))
    groups.assign(305, SCOUT)

    ask(agent, allied(1005, units=units))

    assert groups.group_of(300) == GUARD and groups.members(RAID_GROUP) == [301, 303]


@pytest.mark.parametrize("plan", [STRIKE, PRESERVE, OPENING])
def test_out_of_a_raiding_plan_the_raid_group_goes_back_to_main_at_the_staging_point(plan):
    agent, manager = raid_agent()
    ask(agent, allied(units=light_tanks(4)))
    agent._plan_selector.plan = plan

    orders = ask(agent, allied(1005, units=[*light_tanks(3), own(303, "1tnk", 50, 40, idle=False)]))

    assert agent.ctx.groups.members(RAID_GROUP) == [] and not manager.ready
    assert orders == [{"cmd": "move", "units": [303], "to": STAGING}]


@pytest.mark.parametrize("plan", [FINISH, HOLD])
def test_a_raid_group_of_4_is_still_the_raid_group_after_a_200_tick_finish_or_hold_and_waits_at_the_staging_point(
    plan,
):
    agent, manager = raid_agent()
    ask(agent, allied(units=light_tanks(4)))
    agent._plan_selector.plan = plan

    waiting = [ask(agent, allied(tick, units=[*light_tanks(3), own(303, "1tnk", 50, 40)])) for tick in (1005, 1200)]
    agent._plan_selector.plan = RAID
    back = ask(agent, allied(1205, units=light_tanks(4)))

    assert waiting == [[{"cmd": "move", "units": [303], "to": STAGING}]] * 2
    assert sorted(agent.ctx.groups.members(RAID_GROUP)) == [300, 301, 302, 303] and manager.ready
    assert back == []


@pytest.mark.parametrize("plan", [HOLD, FINISH])
def test_a_raid_group_of_3_of_4_held_through_a_2000_tick_hold_or_finish_is_still_the_raid_group_afterwards(plan):
    agent, manager = raid_agent()
    ask(agent, allied(units=light_tanks(3)))
    agent._plan_selector.plan = plan

    kept = [ask(agent, allied(tick, units=light_tanks(3))) for tick in (1005, 2000, 3005)]
    members = sorted(agent.ctx.groups.members(RAID_GROUP))
    agent._plan_selector.plan = RAID
    ask(agent, allied(3010, units=light_tanks(3)))

    assert kept == [[], [], []] and members == [300, 301, 302]
    assert sorted(agent.ctx.groups.members(RAID_GROUP)) == [300, 301, 302] and not manager.ready


@pytest.mark.parametrize("plan", [PRESERVE, STRIKE])
def test_a_raid_group_kept_through_a_long_hold_is_disbanded_once_the_plan_turns_to_preserve_or_strike(plan):
    agent, _ = raid_agent()
    ask(agent, allied(units=light_tanks(4)))
    agent._plan_selector.plan = HOLD
    ask(agent, allied(3005, units=light_tanks(4)))
    kept = sorted(agent.ctx.groups.members(RAID_GROUP))
    agent._plan_selector.plan = plan

    ask(agent, allied(3010, units=light_tanks(4)))

    assert kept == [300, 301, 302, 303] and agent.ctx.groups.members(RAID_GROUP) == []
    assert sorted(agent.ctx.groups.members(MAIN)) == [300, 301, 302, 303]


def test_a_raid_under_way_when_the_plan_turns_to_finish_comes_back_to_the_staging_point_as_the_raid_group():
    agent, manager, _ = ready_raid(1000, [FAR_HARVESTER], [NORTH_PILLBOX])
    ask(agent, allied(1005, units=tanks_at(36, 54, idle=False), buildings=[NORTH_PILLBOX]))
    agent._plan_selector.plan = FINISH

    orders = ask(agent, allied(1010, units=tanks_at(40, 57, idle=False)))

    assert manager.mission is None and sorted(agent.ctx.groups.members(RAID_GROUP)) == [300, 301, 302, 303]
    assert orders == [{"cmd": "move", "units": [300, 301, 302, 303], "to": STAGING}]


def test_a_kept_raid_group_base_defense_has_gets_no_order_from_the_raid_manager():
    agent, manager = raid_agent()
    ask(agent, allied(units=light_tanks(4)))
    agent._plan_selector.plan = HOLD
    agent.ctx.groups.recall(RAID_GROUP)

    orders = ask(agent, allied(1005, units=[*light_tanks(3), own(303, "1tnk", 50, 40)]))

    assert orders == [] and sorted(agent.ctx.groups.members(RAID_GROUP)) == [300, 301, 302, 303]


@pytest.mark.parametrize("mark", ["recall", "set_defense_override"])
def test_a_raid_group_base_defense_has_gets_no_order_from_the_raid_manager(mark):
    agent, manager = raid_agent()
    getattr(agent.ctx.groups, mark)(RAID_GROUP)

    assert ask(agent, allied(units=light_tanks(4, gathered=0))) == [] and not manager.ready


@pytest.mark.parametrize(
    "marked, gathering",
    [((), [("move", [300, 301, 302, 303], STAGING)]), ((301,), [])],
    ids=["none marked", "301 marked"],
)
def test_unit_override_a_raid_group_one_member_of_which_base_defense_took_is_left_to_base_defense(marked, gathering):
    agent, manager = raid_agent()
    ask(agent, allied(units=light_tanks(4)))
    members = sorted(agent.ctx.groups.members(RAID_GROUP))
    agent.ctx.groups.set_unit_override(marked)

    orders = ask(agent, allied(1005, units=light_tanks(4, gathered=0)))

    assert members == [300, 301, 302, 303] and not manager.ready
    assert [(order["cmd"], sorted(order["units"]), order["to"]) for order in orders] == gathering


def test_press_raids_too_and_the_agent_s_own_raid_manager_takes_its_slot():
    agent, manager = raid_agent(PRESS)

    ask(agent, allied(units=light_tanks(4)))

    assert manager.ready


def test_while_the_staging_point_is_not_known_the_raid_manager_orders_nothing():
    agent, manager = raid_agent()

    assert ask(agent, allied(units=light_tanks(4, gathered=0), base_center=None)) == [] and not manager.ready
    # With no staging point no unit of main stands at it: the quota counts the raid group alone.
    assert quota_units(agent.ctx, agent.world, "1tnk") == 0


# -- raids switched off ----------------------------------------------------------------------------

RAIDS_OFF = tuning_off(raids=raids_off(enabled=False))


@pytest.mark.parametrize(
    "tuning, eligible, plan", [(tuning_off(), [HOLD, RAID], RAID), (RAIDS_OFF, [HOLD], HOLD)], ids=["raids on", "off"]
)
def test_with_raids_switched_off_raid_is_never_open_though_an_uncovered_harvester_is_in_sight(tuning, eligible, plan):
    # The script's own plan rules, with the war factory standing and nothing else open at 40 s.
    agent = new_agent(tuning=tuning)

    ask(agent, allied(units=light_tanks(4), enemies=[HARVESTER]))

    situation = plans.observe(agent.world, agent.ctx, agent.ctx.tuning, None)
    assert plans.open_plans(situation, agent.ctx.tuning.plan_rules) == eligible and agent.current_plan == plan


@pytest.mark.parametrize("plan", [RAID, PRESS])
def test_with_raids_switched_off_no_unit_is_trained_for_the_raid_quota(plan):
    agent = new_agent(UpkeepManager(), RaidManager(), tuning=RAIDS_OFF, plan_selector=FixedPlan(plan))

    orders = ask(agent, allied(units=harvesters()))

    # No light tank stands: with raids on the quota's light tank would take the Vehicle queue.
    assert ("2tnk", 1) in trained(orders) and ("1tnk", 1) not in trained(orders)


@pytest.mark.parametrize("plan", [RAID, PRESS, HOLD, FINISH])
def test_with_raids_switched_off_the_raid_group_is_disbanded_under_every_plan(plan):
    manager = RaidManager()
    agent = new_agent(manager, tuning=RAIDS_OFF, plan_selector=FixedPlan(plan))
    ask(agent, allied(units=light_tanks(4), enemies=[HARVESTER]))
    formed = agent.ctx.groups.members(RAID_GROUP)
    for unit_id in (300, 301, 302, 303):
        agent.ctx.groups.assign(unit_id, RAID_GROUP)

    orders = ask(agent, allied(1005, units=[*light_tanks(3), own(303, "1tnk", 50, 40)], enemies=[HARVESTER]))

    assert formed == [] and agent.ctx.groups.members(RAID_GROUP) == []
    assert (manager.ready, manager.target) == (False, None)
    assert sorted(agent.ctx.groups.members(MAIN)) == [300, 301, 302, 303]
    assert orders == [{"cmd": "move", "units": [303], "to": STAGING}]


# -- the raid's target -----------------------------------------------------------------------------

PILLBOX = foe(600, "pbox", 63, 36)
# The requirement's refinery, 2.2 cells from the pillbox and so within its range (6) and the margin (2).
COVERED_REFINERY = foe(601, "proc", 62, 34)
# A harvester the pillbox does not cover: 9.5 cells from it. The requirement's harvester at (60, 30) lies 6.7
# cells from the pillbox, inside the 8 cells it covers, so the uncovered harvester of its case stands 3 cells
# further north on the same column.
HARVESTER = foe(500, "harv", 60, 27)


def ready_raid(tick: int, enemies=(), buildings=()) -> tuple:
    """A new raid agent after a state with a ready Allied raid group and these enemies, its manager and its orders."""
    agent, manager = raid_agent()
    orders = ask(agent, allied(tick, units=light_tanks(4), enemies=enemies, buildings=buildings))
    return agent, manager, orders


def test_an_uncovered_harvester_is_chosen_over_a_refinery_a_pillbox_covers():
    _, manager, _ = ready_raid(1000, [HARVESTER], [PILLBOX, COVERED_REFINERY])

    assert manager.ready and manager.target == 500


def test_with_no_uncovered_harvester_a_covered_refinery_is_not_chosen_and_the_group_stays():
    agent, manager, orders = ready_raid(1000, buildings=[PILLBOX, COVERED_REFINERY])

    assert manager.ready and manager.target is None and orders == []


def test_the_requirement_s_harvester_at_60_30_is_within_the_pillbox_s_coverage():
    _, manager, _ = ready_raid(1000, [foe(500, "harv", 60, 30)], [PILLBOX])

    assert manager.target is None


def test_a_visible_harvester_is_chosen_over_a_nearer_refinery_both_reachable():
    _, manager, _ = ready_raid(1000, [foe(500, "harv", 70, 70)], [foe(612, "proc", 42, 40)])

    assert manager.target == 500


def test_power_plants_and_other_buildings_are_no_raid_targets():
    _, manager, orders = ready_raid(1000, buildings=[foe(610, "barr", 40, 40), foe(611, "powr", 41, 40)])

    assert manager.ready and manager.target is None and orders == []


def test_of_two_targets_of_a_kind_the_one_nearest_to_the_group_is_chosen():
    _, manager, _ = ready_raid(1000, [foe(500, "harv", 60, 40), foe(501, "harv", 50, 50)])

    assert manager.target == 501


SILO = foe(700, "mslo", 70, 70)


def test_nuke_spread_an_uncovered_silo_is_chosen_before_a_nearer_uncovered_harvester():
    _, manager, _ = ready_raid(1000, [foe(500, "harv", 50, 50)], [SILO])

    assert manager.ready and manager.target == 700


def test_nuke_spread_a_silo_a_pillbox_covers_is_no_raid_target():
    _, manager, _ = ready_raid(1000, [HARVESTER], [PILLBOX, foe(700, "mslo", 62, 34)])

    assert manager.target == 500


@pytest.mark.parametrize("nuke_spread, ranked", [(True, [700, 500]), (False, [500])], ids=["on", "off"])
def test_nuke_spread_a_silo_ranks_first_among_the_raid_targets_only_with_the_switch_on(nuke_spread, ranked):
    world = World(tracked_items())
    observation = {"tick": 1000, "units": light_tanks(4), "visible_enemies": [foe(500, "harv", 50, 50)]}
    world.update({"phase": "playing", "observation": {**observation, "visible_enemy_buildings": [SILO]}})
    tuning = tuning_off(combat=dataclasses.replace(SWITCHES_OFF.combat, nuke_spread=nuke_spread))

    raiders = list(world.combat_units.values())
    assert [record.id for record in raid_targets.ranked(world, (32, 48), raiders, tuning)] == ranked


REFINERY = foe(610, "proc", 60, 40)


def raiders(unit_type: str) -> list[dict]:
    """Four fit units of a type at the staging point (32, 48)."""
    return [own(300 + number, unit_type, 32, 46 + number) for number in range(4)]


def test_a_harvester_unseen_for_half_a_minute_is_no_target_and_for_1500_ticks_lets_the_refinery_be_one():
    agent, manager = raid_agent()
    ask(agent, allied(1000, units=light_tanks(3), enemies=[foe(500, "harv", 50, 50)], buildings=[REFINERY]))

    ask(agent, allied(2499, units=light_tanks(4)))
    unseen_for_1499 = (agent.world.enemy_memory[500].stale, manager.ready, manager.target)
    ask(agent, allied(2500, units=light_tanks(4)))

    assert unseen_for_1499 == (True, True, None) and manager.target == 610


# Four light tanks do 4 x 1,548 a second to a refinery's wood, 123,840 in 20 s; four jeeps 4 x 250, 20,000 in 20 s.
# A refinery has 90,000 hit points.
@pytest.mark.parametrize(
    "unit_type, withheld, refinery_hp, chosen",
    [
        ("1tnk", (), 100, 610),
        ("jeep", ("1tnk",), 100, None),
        ("jeep", ("1tnk",), 23, None),
        ("jeep", ("1tnk",), 22, 610),
    ],
    ids=["light tanks, a whole refinery", "jeeps, a whole refinery", "jeeps, 20,700 left", "jeeps, 19,800 left"],
)
def test_with_no_harvester_ever_seen_a_refinery_is_chosen_only_when_20_s_of_the_group_s_damage_covers_it(
    unit_type, withheld, refinery_hp, chosen
):
    agent, manager = raid_agent()

    ask(agent, allied(units=raiders(unit_type), buildings=[{**REFINERY, "hp": refinery_hp}], withheld=withheld))

    assert manager.ready and manager.target == chosen


# 4 x 1,548 x 15 = 92,880 covers the refinery's 90,000; 4 x 1,548 x 14 = 86,688 does not.
@pytest.mark.parametrize("kill_seconds, chosen", [(15, 610), (14, None)])
def test_the_refinery_gate_is_the_tuning_s(kill_seconds, chosen):
    manager = RaidManager()
    tuning = tuning_off(raids=raids_off(refinery_after_ticks=800, refinery_kill_seconds=kill_seconds))
    agent = new_agent(manager, tuning=tuning, plan_selector=FixedPlan(RAID))
    ask(agent, allied(1000, units=light_tanks(3), enemies=[foe(500, "harv", 50, 50)], buildings=[REFINERY]))

    ask(agent, allied(1799, units=light_tanks(4)))
    unseen_for_799 = manager.target
    ask(agent, allied(1800, units=light_tanks(4)))

    assert unseen_for_799 is None and manager.target == chosen


def test_a_refinery_the_item_table_gives_no_hit_points_for_is_never_known_to_be_within_a_raid_s_reach():
    tracked = tracked_items()
    without_hp = ItemTable([dataclasses.replace(tracked["proc"], hp=None), tracked["1tnk"]], tracked.damage)

    def targets(items: ItemTable) -> list[int]:
        world = World(items)
        observation = {"tick": 2000, "units": raiders("1tnk"), "visible_enemy_buildings": [REFINERY]}
        world.update({"phase": "playing", "observation": observation})
        group = list(world.combat_units.values())
        return [record.id for record in raid_targets.ranked(world, (32, 48), group, tuning_off())]

    assert (targets(tracked), targets(without_hp)) == ([610], [])


def raid_open(agent, state: dict) -> bool:
    """Whether RAID is open in the situation an agent with the script's plan rules sees once it has taken a state in."""
    ask(agent, state)
    situation = plans.observe(agent.world, agent.ctx, agent.ctx.tuning, None)
    return RAID in plans.open_plans(situation, agent.ctx.tuning.plan_rules)


def test_raid_opens_on_a_refinery_only_once_no_harvester_has_been_seen_for_1500_ticks():
    agent = new_agent()

    harvester_in_sight = raid_open(agent, allied(1000, enemies=[foe(500, "harv", 50, 50)], buildings=[REFINERY]))
    harvester_stale_seen_800_ago = raid_open(agent, allied(1800))
    harvester_seen_1500_ago = raid_open(agent, allied(2500))

    assert (harvester_in_sight, harvester_stale_seen_800_ago, harvester_seen_1500_ago) == (True, False, True)


# No raid unit stands: the refinery is weighed against the full quota's damage, as `ranked` weighs the ready group.
@pytest.mark.parametrize(
    "withheld, refinery_hp, is_open",
    [((), 100, True), (("1tnk",), 100, False), (("1tnk",), 22, True)],
    ids=["4 light tanks, a whole refinery", "4 jeeps, a whole refinery", "4 jeeps, 19,800 left"],
)
def test_raid_opens_on_a_refinery_only_when_20_s_of_the_full_raid_group_s_damage_covers_it(
    withheld, refinery_hp, is_open
):
    state = allied(buildings=[{**REFINERY, "hp": refinery_hp}], withheld=withheld)

    assert raid_open(new_agent(), state) is is_open


def test_no_target_is_chosen_for_a_group_that_is_not_ready():
    agent, manager = raid_agent()

    ask(agent, allied(units=light_tanks(3), enemies=[HARVESTER]))

    assert (manager.ready, manager.target) == (False, None)


# -- the way to the target -------------------------------------------------------------------------

MAP = (0, 0, 96, 96)
FAR_HARVESTER = foe(500, "harv", 60, 48)
# A pillbox whose coverage (6 + 2 cells) reaches the straight line from the staging point to (52, 48).
NORTH_PILLBOX = foe(600, "pbox", 42, 40)


def covered_by(*defenses: dict):
    """Whether a cell lies within the coverage of pillboxes at the given actors' cells."""
    return lambda cell: any(cells.is_within(cell, (defense["x"], defense["y"]), 6 + 2) for defense in defenses)


@pytest.mark.parametrize("target", [(60, 48), (60, 30), (20, 80)], ids=["east", "north-east", "south-west"])
def test_the_approach_cell_is_8_cells_short_of_the_target_on_the_line_from_the_staging_point(target):
    way = raid_routes.route((32, 48), (32, 48), target, MAP, lambda cell: False, raids_off())

    (x, y), staging = way.approach, (32, 48)
    assert cells.distance(way.approach, target) == pytest.approx(8, abs=0.75)
    assert cells.distance(staging, way.approach) + 8 == pytest.approx(cells.distance(staging, target), abs=0.75)
    assert way.legs == (way.approach,) and way.path == tuple(cells.line(staging, way.approach))


def test_a_target_within_8_cells_of_the_staging_point_is_attacked_from_the_staging_point():
    assert raid_routes.approach_cell((32, 48), (36, 50), raids_off()) == (32, 48)


def test_a_way_whose_straight_line_crosses_covered_ground_goes_round_by_the_clear_side_waypoint():
    is_covered = covered_by(NORTH_PILLBOX)

    way = raid_routes.route((32, 48), (32, 48), (60, 48), MAP, is_covered, raids_off())

    assert any(map(is_covered, cells.line((32, 48), (52, 48))))
    assert way.legs == ((42, 60), (52, 48)) and not any(map(is_covered, way.path))
    assert way.path[0] == (32, 48) and way.path[-1] == (52, 48) and (42, 60) in way.path


def test_with_both_side_waypoints_covered_there_is_no_way():
    is_covered = covered_by(NORTH_PILLBOX, foe(601, "pbox", 42, 56))

    assert raid_routes.route((32, 48), (32, 48), (60, 48), MAP, is_covered, raids_off()) is None


def test_a_side_waypoint_off_the_map_is_taken_at_the_map_s_edge():
    way = raid_routes.route((2, 48), (2, 48), (30, 48), MAP, covered_by(foe(600, "pbox", 12, 56)), raids_off())

    assert way.legs[0] == (12, 36)


def test_a_group_standing_on_covered_ground_at_its_approach_cell_has_no_way():
    assert raid_routes.route((32, 48), (32, 48), (36, 48), MAP, lambda cell: True, raids_off()) is None


def test_a_ready_raid_is_sent_to_the_approach_cell_and_round_covered_ground():
    straight = ready_raid(1000, [FAR_HARVESTER])
    round_about = ready_raid(1000, [FAR_HARVESTER], [NORTH_PILLBOX])

    assert straight[2] == [{"cmd": "move", "units": [300, 301, 302, 303], "to": [52, 48]}]
    assert round_about[2] == [{"cmd": "move", "units": [300, 301, 302, 303], "to": [42, 60]}]


def test_a_target_no_safe_way_leads_to_is_passed_over_for_the_next():
    pillboxes = [NORTH_PILLBOX, foe(601, "pbox", 42, 56)]
    # 35 cells from the group, farther than the harvester at (60, 48) behind the pillboxes, and on the open side.
    open_side = foe(501, "harv", 10, 20)

    _, manager, orders = ready_raid(1000, [FAR_HARVESTER, open_side], pillboxes)

    assert manager.target == 501 and orders[0]["cmd"] == "move"


def tanks_at(x: int, y: int, *, idle: bool = True) -> list[dict]:
    return [own(300 + number, "1tnk", x, y + number - 1, idle=idle) for number in range(4)]


def test_a_raid_goes_on_from_cell_to_cell_and_attacks_its_target_in_sight_from_the_approach_cell():
    agent, manager, _ = ready_raid(1000, [FAR_HARVESTER], [NORTH_PILLBOX])
    moving = ask(agent, allied(1005, units=tanks_at(36, 54, idle=False), buildings=[NORTH_PILLBOX]))
    at_waypoint = ask(agent, allied(1100, units=tanks_at(42, 59, idle=False)))

    at_approach = ask(agent, allied(1200, units=tanks_at(52, 48), enemies=[FAR_HARVESTER]))

    assert moving == [] and at_waypoint == [{"cmd": "move", "units": [300, 301, 302, 303], "to": [52, 48]}]
    assert at_approach == [{"cmd": "attack", "units": [300, 301, 302, 303], "target": 500}]


@pytest.mark.parametrize(
    "near, priority",
    [([], Priority.MOVE), ([foe(510, "e1", 57, 48)], Priority.CONTACT)],
    ids=["its target 8 cells off", "an enemy rifle 5 cells off"],
)
def test_a_raid_s_attack_is_a_contact_order_only_once_a_raider_is_within_its_range_and_2_cells_of_an_enemy(
    near, priority
):
    agent, _, _ = ready_raid(1000, [FAR_HARVESTER])

    at_approach = ask(agent, allied(1200, units=tanks_at(52, 48), enemies=[FAR_HARVESTER, *near]))

    assert at_approach == [{"cmd": "attack", "units": [300, 301, 302, 303], "target": 500}]
    assert agent.ledger.entries[-1].priority == priority


def test_a_raid_at_its_approach_cell_with_the_target_out_of_sight_attack_moves_on_its_last_known_cell():
    agent, _, _ = ready_raid(1000, [FAR_HARVESTER])

    orders = ask(agent, allied(1100, units=tanks_at(52, 48)))

    assert orders == [{"cmd": "attack_move", "units": [300, 301, 302, 303], "to": [60, 48]}]


def test_a_raid_that_stands_still_on_its_way_for_50_ticks_goes_on_to_the_next_cell():
    agent, _, _ = ready_raid(1000, [FAR_HARVESTER], [NORTH_PILLBOX])

    early = ask(agent, allied(1049, units=tanks_at(36, 54)))
    late = ask(agent, allied(1050, units=tanks_at(36, 54)))

    assert early == [{"cmd": "move", "units": [300, 301, 302, 303], "to": [42, 60]}]
    assert late == [{"cmd": "move", "units": [300, 301, 302, 303], "to": [52, 48]}]


# -- breaking off, and on to the next target -------------------------------------------------------

LAUNCH = [{"cmd": "move", "units": [300, 301, 302, 303], "to": [52, 48]}]
HOME = [{"cmd": "move", "units": [300, 301, 302, 303], "to": STAGING}]


def launched() -> tuple:
    """A raid agent whose raid was sent at the harvester at (60, 48) at tick 1000, and its manager."""
    agent, manager, orders = ready_raid(1000, [FAR_HARVESTER])
    assert orders == LAUNCH
    return agent, manager


def on_the_way(tick: int = 1005, hp: int = 100, count: int = 4, **observation) -> dict:
    """A state with the raid's light tanks on their way, at (45, 47..50)."""
    tanks = [own(300 + number, "1tnk", 45, 47 + number, hp, idle=False) for number in range(count)]
    return allied(tick, units=tanks, **observation)


# Four enemy light tanks in sight, 2,800 at full health, 5 cells from the raid.
ENEMY_TANKS = [foe(510 + number, "1tnk", 50, 45 + number) for number in range(4)]


@pytest.mark.parametrize(
    "raiders_hp, ratio, orders, target",
    [(80, 0.8, [], 500), (65, 0.65, HOME, None)],
    ids=["ratio 0.8 raids on", "ratio 0.65 breaks off"],
)
def test_a_raid_breaks_off_on_its_estimate_below_0_7_and_raids_on_at_0_8(raiders_hp, ratio, orders, target):
    agent, manager = launched()

    sent = ask(agent, on_the_way(hp=raiders_hp, enemies=ENEMY_TANKS))

    # The raid's four light tanks at their health against the enemy's four at full health.
    group = [agent.world.combat_units[unit_id] for unit_id in (300, 301, 302, 303)]
    assert estimate(group, agent.world, agent.ctx.tuning).ratio == pytest.approx(ratio)
    assert (sent, manager.target) == (orders, target)


def test_a_raid_outmatched_at_0_65_is_sent_back_and_not_out_again_for_500_ticks():
    agent, manager = launched()

    broke_off = (ask(agent, on_the_way(hp=65, enemies=ENEMY_TANKS)), manager.target)
    waiting = [ask(agent, allied(tick, units=light_tanks(4))) for tick in (1010, 1250, 1504)]
    again = ask(agent, allied(1505, units=light_tanks(4)))

    assert broke_off == (HOME, None)
    assert waiting == [[], [], []] and again == LAUNCH


def test_the_raid_break_off_ratio_is_the_tuning_s():
    manager = RaidManager()
    tuning = tuning_off(raids=raids_off(raid_break_off_ratio=0.85))
    agent = new_agent(manager, tuning=tuning, plan_selector=FixedPlan(RAID))
    assert ask(agent, allied(1000, units=light_tanks(4), enemies=[FAR_HARVESTER])) == LAUNCH

    assert ask(agent, on_the_way(hp=80, enemies=ENEMY_TANKS)) == HOME and manager.target is None


@pytest.mark.parametrize(
    "state, raiders_left",
    [
        (on_the_way(hp=45), []),
        (on_the_way(buildings=[foe(600, "pbox", 47, 54)]), [300, 301, 302, 303]),
        (on_the_way(count=2), [300, 301]),
    ],
    ids=["mean hp 45%", "a unit in a covered cell", "from 4 units to 2"],
)
def test_a_raid_breaks_off_on_low_health_covered_ground_or_half_its_units_lost(state, raiders_left):
    agent, manager = launched()

    orders = ask(agent, state)

    assert orders == [{**HOME[0], "units": [unit["id"] for unit in state["observation"]["units"]]}]
    assert manager.target is None and sorted(agent.ctx.groups.members(RAID_GROUP)) == raiders_left


def test_three_units_left_of_four_raid_on():
    agent, manager = launched()

    assert ask(agent, on_the_way(1060, count=3)) == [] and manager.target == 500


def at_the_approach(tick: int, enemies=(), events=()) -> dict:
    """A state with the raid's light tanks at their approach cell (52, 48)."""
    return allied(tick, units=tanks_at(52, 48), enemies=list(enemies), events=list(events))


def test_after_destroying_its_harvester_the_raid_goes_on_to_another_without_going_back():
    agent, manager = launched()
    other = foe(501, "harv", 58, 30)
    ask(agent, at_the_approach(1100, [FAR_HARVESTER, other]))

    orders = ask(agent, at_the_approach(1105, [other], events=[event(1, "enemy_killed", count=1)]))

    assert manager.target == 501 and orders == [{"cmd": "move", "units": [300, 301, 302, 303], "to": [51, 35]}]


def test_a_harvester_that_drives_out_of_sight_as_another_enemy_is_killed_is_still_the_raid_s_target():
    agent, manager = launched()
    ask(agent, at_the_approach(1100, [FAR_HARVESTER, foe(510, "e1", 58, 52)]))

    orders = ask(agent, at_the_approach(1105, events=[event(1, "enemy_killed", count=1)]))

    assert agent.world.killed_in_sight == frozenset() and manager.target == 500
    assert orders == [{"cmd": "attack_move", "units": [300, 301, 302, 303], "to": [60, 48]}]
    again = ask(agent, at_the_approach(1110, [foe(500, "harv", 64, 44)]))
    assert again == [{"cmd": "attack", "units": [300, 301, 302, 303], "target": 500}]


def test_after_destroying_its_last_target_the_raid_goes_back_and_waits():
    agent, manager = launched()
    ask(agent, at_the_approach(1100, [FAR_HARVESTER]))

    orders = ask(agent, at_the_approach(1105, events=[event(1, "enemy_killed", count=1)]))

    assert (orders, manager.target) == (HOME, None)
    assert ask(agent, allied(1110, units=light_tanks(4), enemies=[foe(502, "harv", 60, 48)])) == []


def test_a_target_out_of_sight_for_250_ticks_at_the_approach_cell_ends_the_raid():
    agent, manager = launched()
    ask(agent, at_the_approach(1100))

    ask(agent, at_the_approach(1349))
    kept = manager.target
    orders = ask(agent, at_the_approach(1350))

    assert kept == 500 and (orders, manager.target) == (HOME, None)


def test_a_kill_reported_while_the_target_was_out_of_sight_is_not_its_end():
    agent, manager = launched()
    ask(agent, at_the_approach(1100))

    orders = ask(agent, at_the_approach(1105, events=[event(1, "enemy_killed", count=1)]))

    assert manager.target == 500 and orders == [{"cmd": "attack_move", "units": [300, 301, 302, 303], "to": [60, 48]}]


def test_a_raid_that_must_break_off_does_so_even_as_its_target_dies():
    agent, manager = launched()
    ask(agent, at_the_approach(1100, [FAR_HARVESTER]))

    orders = ask(agent, allied(1105, units=tanks_at(52, 48)[:2], events=[event(1, "enemy_killed", count=1)]))

    assert orders == [{**HOME[0], "units": [300, 301]}] and manager.target is None


def test_a_raid_group_with_no_unit_left_breaks_off_without_an_order():
    agent, manager = launched()

    assert ask(agent, allied(1005)) == [] and manager.target is None


# -- the raid events of the match record -----------------------------------------------------------

# The raid `launched` sets out at tick 1000: four light tanks, 700 each, at the harvester 500.
START_EVENT = {
    "phase": "start",
    "tick": 1000,
    "game_seconds": 40.0,
    "target": 500,
    "target_type": "harv",
    "group_size": 4,
    "value": 2800,
}


def end_event(tick: int, reason: str, kills: int = 0, units_lost: int = 0, value_lost: int = 0) -> dict:
    """The `raid` end event of the raid `launched` sets out on, at its harvester 500."""
    return {
        "phase": "end",
        "tick": tick,
        "game_seconds": tick / TICKS_PER_SECOND,
        "target": 500,
        "reason": reason,
        "kills": kills,
        "units_lost": units_lost,
        "value_lost": value_lost,
    }


@pytest.mark.parametrize(
    "state, ended",
    [
        (on_the_way(count=2), end_event(1005, "losses", units_lost=2, value_lost=1400)),
        (on_the_way(hp=45), end_event(1005, "health")),
        (on_the_way(hp=65, enemies=ENEMY_TANKS), end_event(1005, "outmatched")),
        (on_the_way(buildings=[foe(600, "pbox", 47, 54)]), end_event(1005, "covered")),
    ],
    ids=["half its units lost", "mean hp 45%", "estimate 0.65", "a unit in a covered cell"],
)
def test_a_raid_logs_a_start_event_and_on_breaking_off_an_end_event_with_why_its_kills_and_its_losses(
    match_record, state, ended
):
    agent, _ = launched()

    ask(agent, state)

    assert match_record("raid") == [START_EVENT, ended]


@pytest.mark.parametrize(
    "apc_stands, ended",
    [(True, end_event(1010, "plan_changed")), (False, end_event(1010, "plan_changed", units_lost=1, value_lost=700))],
    ids=["aboard: not lost", "lost with its transport"],
)
def test_a_crew_member_aboard_a_transport_is_no_loss_of_the_raid_unless_lost_with_its_transport(
    match_record, apc_stands, ended
):
    agent, _ = launched()
    apc = own(400, "apc", 45, 46)
    # As if the agent had ordered the fourth light tank into the APC with the state the raid set out from.
    agent.world.note_orders([{"cmd": "enter_transport", "units": [303], "target": 400}])
    three_tanks = [own(300 + number, "1tnk", 45, 47 + number, idle=False) for number in range(3)]
    boarding = event(1, "unit_lost", id=303, type="1tnk", x=45, y=50)
    ask(agent, allied(1005, units=[*three_tanks, apc], events=[boarding]))
    agent._plan_selector.plan = HOLD

    ask(agent, allied(1010, units=[*three_tanks, apc] if apc_stands else three_tanks, events=[boarding]))

    assert agent.world.events_new == [] and match_record("raid") == [START_EVENT, ended]


def test_a_raid_whose_target_stays_out_of_sight_at_the_approach_cell_ends_with_target_unseen(match_record):
    agent, _ = launched()
    ask(agent, at_the_approach(1100))

    ask(agent, at_the_approach(1350))

    assert match_record("raid") == [START_EVENT, end_event(1350, "target_unseen")]


def test_a_raid_that_destroys_two_harvesters_and_finds_no_third_logs_one_end_with_two_kills(match_record):
    agent, _ = launched()
    other = foe(501, "harv", 58, 30)
    ask(agent, at_the_approach(1100, [FAR_HARVESTER, other]))
    ask(agent, at_the_approach(1105, [other], events=[event(1, "enemy_killed", count=1)]))
    ask(agent, at_the_approach(1110, [other]))

    ask(agent, at_the_approach(1115, events=[event(2, "enemy_killed", count=1)]))

    assert match_record("raid") == [START_EVENT, {**end_event(1115, "no_target", kills=2), "target": 501}]


def test_a_raid_under_way_when_the_plan_stops_raiding_ends_with_plan_changed(match_record):
    agent, _ = launched()
    agent._plan_selector.plan = HOLD

    ask(agent, on_the_way())

    assert match_record("raid") == [START_EVENT, end_event(1005, "plan_changed")]


def test_a_raid_group_that_never_set_out_logs_no_raid_event(match_record):
    agent, _ = raid_agent()
    ask(agent, allied(units=light_tanks(3), enemies=[HARVESTER]))
    agent._plan_selector.plan = STRIKE

    ask(agent, allied(1005, units=light_tanks(3)))

    assert match_record("raid") == []


# -- what is made for the raid goes to it ------------------------------------------------------------


def test_new_units_join_the_raid_group_while_it_is_short_of_their_type_after_the_guard_and_never_once_unpublished():
    groups = Groups()
    groups.set_guard_quota("e1", 1)
    groups.set_raid_quota({"1tnk": 2, "e2": 1})
    world = World(tracked_items())
    units = [own(300, "e1", 30, 40), *[own(301 + number, "1tnk", 30, 41 + number) for number in range(3)]]
    world.update({"phase": "playing", "observation": {"tick": 1000, "units": [*units, own(304, "e1", 30, 44)]}})
    groups.update(world)
    first = (groups.members(GUARD), groups.members(RAID_GROUP), groups.members(MAIN))
    groups.set_raid_quota({})
    world.update({"phase": "playing", "observation": {"tick": 1005, "units": [*units[:2], own(305, "1tnk", 30, 45)]}})

    groups.update(world)

    # The guard first; the raid group up to its quota of each type; the rest, and everything once no quota is set,
    # to main.
    assert first == ([300], [301, 302], [303, 304])
    assert (groups.members(RAID_GROUP), groups.members(MAIN)) == ([301], [305])


def combat_slot_watch(unit_id: int, seen: list) -> ScriptedManager:
    """A manager in the combat slot that notes the group a unit is in when combat's turn comes."""
    return ScriptedManager("combat", lambda world, ctx: seen.append(ctx.groups.group_of(unit_id)) or [])


@pytest.mark.parametrize("plan, group", [(RAID, RAID_GROUP), (PRESS, RAID_GROUP), (HOLD, MAIN)])
def test_a_light_tank_made_while_the_raid_group_is_short_joins_it_before_combat_s_turn_under_a_raiding_plan(
    plan, group
):
    seen: list = []
    manager = RaidManager()
    agent = new_agent(combat_slot_watch(303, seen), manager, plan_selector=FixedPlan(RAID))
    ask(agent, allied(units=light_tanks(3)))
    agent._plan_selector.plan = plan
    ask(agent, allied(1005, units=light_tanks(3)))

    # Made by the war factory at (18, 56) while the group holds 3 of its 4.
    ask(agent, allied(1010, units=[*light_tanks(3), own(303, "1tnk", 18, 58)]))

    assert seen[-1] == group and agent.ctx.groups.group_of(303) == group


def test_a_light_tank_that_joins_while_a_raid_is_under_way_waits_at_the_staging_point_and_is_not_sent_after_it():
    agent, manager = launched()
    ask(agent, on_the_way(1005, count=3))
    crew = [own(300 + number, "1tnk", 45, 47 + number, idle=False) for number in range(3)]

    orders = ask(agent, allied(1010, units=[*crew, own(304, "1tnk", 18, 58)]))

    assert agent.ctx.groups.group_of(304) == RAID_GROUP and manager.target == 500
    assert sorted(manager.mission.crew) == [300, 301, 302, 303]
    assert [order for order in orders if 304 in order["units"]] == [{"cmd": "move", "units": [304], "to": STAGING}]


# Main goes forward under PRESS and stays there, the fake game having no combat; the raid loses a vehicle at most
# once a minute while it does, twice in all.
RAID_LOSSES = 2
RAID_LOSS_GAP_TICKS = 60 * TICKS_PER_SECOND
FORWARD_MATCH_SECONDS = 900
MOST_RAID_VEHICLES = 8


class RaidLosses:
    """A per-step callback that kills one of the raid group's vehicles while main is forward, as a raid would lose it."""

    def __init__(self, agent, vehicle: str) -> None:
        self.agent = agent
        self.vehicle = vehicle
        # The tick main was first seen forward; None until it was.
        self.forward_tick: int | None = None
        self.killed: list[tuple[int, int]] = []

    def __call__(self, game: FakeRedAlert) -> None:
        if not self.agent.ctx.army:
            return
        self.forward_tick = self.forward_tick or game.tick
        raiders = self._raiders()
        if raiders and self._is_loss_due(game.tick):
            self._kill(game, raiders[0])

    def _raiders(self) -> list[int]:
        """The raid group's vehicles, by id."""
        ctx, world = self.agent.ctx, self.agent.world
        return [
            unit_id for unit_id in ctx.groups.members(RAID_GROUP) if world.combat_units[unit_id].type == self.vehicle
        ]

    def _is_loss_due(self, tick: int) -> bool:
        """Whether a loss is due: fewer than RAID_LOSSES so far, and the last RAID_LOSS_GAP_TICKS ago or more."""
        if len(self.killed) >= RAID_LOSSES:
            return False
        return not self.killed or tick - self.killed[-1][0] >= RAID_LOSS_GAP_TICKS

    def _kill(self, game: FakeRedAlert, unit_id: int) -> None:
        """Kill one of the raid's vehicles, and note when."""
        game.kill_own(unit_id)
        self.killed.append((game.tick, unit_id))


def mix_without_raid_vehicles() -> Tuning:
    """The switches-off thresholds with each family's base-stage row of the mix replaced by its service-depot row.

    The mix then trains no light tank and no flak truck at any stage: each one trained is the raid quota's.
    """
    rows = mix_off().rows
    return tuning_off(
        mix=mix_off(rows={family: {**rows[family], BASE_STAGE: rows[family][DEPOT_STAGE]} for family in rows})
    )


def forward_match(family: str, enemy: str, vehicle: str, tuning: Tuning) -> tuple:
    """A 900-s fake match under PRESS with the raid's vehicles lost now and then: the agent, the losses, and how many
    of the raid's vehicle were trained."""
    game = FakeRedAlert(family, enemy)
    game.place_enemy("harv", 70, 30, visible=True)
    for actor, x, y in (("fact", 74, 48), ("powr", 78, 48), ("pbox", 74, 51), ("proc", 72, 42)):
        game.place_enemy(actor, x, y)
    agent = RedAlertAgent(
        items=tracked_items(),
        tuning=tuning,
        chooser=StubChooser(),
        plan_selector=OpeningThen(PRESS),
        settings=settings_models_off(),
    )
    losses = RaidLosses(agent, vehicle)
    game.on_step(losses)
    game.run(agent, FORWARD_MATCH_SECONDS * STEPS_PER_SECOND)
    trained = sum(
        entry.order.get("count", 1)
        for entry in agent.ledger.entries
        if entry.order["cmd"] == "train" and entry.order["item"] == vehicle
    )
    return agent, losses, trained


# The tracked thresholds give the Soviet raid no flak truck; these matches give it three, so that it has vehicles to
# lose and to make again.
SOVIET_RAID_FLAK_TRUCKS = 3
FORWARD_MATCHES = [("russia", "england", "ftrk", SOVIET_RAID_FLAK_TRUCKS), ("england", "russia", "1tnk", 4)]


def with_flak_truck_raids(tuning: Tuning) -> Tuning:
    """The thresholds given, with SOVIET_RAID_FLAK_TRUCKS flak trucks in the Soviet raid once a war factory stands."""
    composition = dataclasses.replace(tuning.composition, raid_mixed_vehicles=SOVIET_RAID_FLAK_TRUCKS)
    return dataclasses.replace(tuning, composition=composition)


@pytest.mark.parametrize("family, enemy, vehicle, wanted", FORWARD_MATCHES)
def test_with_main_forward_and_the_raid_group_short_the_raid_vehicles_trained_by_900_s_are_its_quota_and_losses(
    family, enemy, vehicle, wanted
):
    agent, losses, trained = forward_match(family, enemy, vehicle, with_flak_truck_raids(mix_without_raid_vehicles()))

    assert losses.forward_tick is not None and len(losses.killed) == RAID_LOSSES
    assert trained <= min(wanted + RAID_LOSSES, MOST_RAID_VEHICLES)
    assert agent.ctx.groups.count(RAID_GROUP, vehicle) == wanted


# The tracked thresholds without the Soviet Tesla coils: a base that keeps them stands its service depot minutes later,
# and until it does the base row trains flak trucks of its own, past any bound of the raid's.
TRACKED_WITHOUT_COILS = dataclasses.replace(
    tuning_off(), upkeep=dataclasses.replace(upkeep_off(), soviet_tesla_coils=0)
)


@pytest.mark.parametrize("family, enemy, vehicle, wanted", FORWARD_MATCHES)
def test_with_the_tracked_mix_main_forward_and_the_raid_short_at_most_8_raid_vehicles_are_trained_by_900_s(
    family, enemy, vehicle, wanted
):
    # The tracked mix trains the raid's vehicle itself at the base stage: only the bound of 8 is the raid's.
    agent, losses, trained = forward_match(family, enemy, vehicle, with_flak_truck_raids(TRACKED_WITHOUT_COILS))

    assert losses.forward_tick is not None and len(losses.killed) == RAID_LOSSES
    assert trained <= MOST_RAID_VEHICLES and agent.ctx.groups.count(RAID_GROUP, vehicle) == wanted


def test_with_the_tesla_coils_kept_main_forward_and_the_soviet_raid_short_it_holds_its_flak_trucks_again_by_900_s():
    agent, losses, _ = forward_match("russia", "england", "ftrk", with_flak_truck_raids(tuning_off()))

    assert tuning_off().upkeep.soviet_tesla_coils == 2
    assert losses.forward_tick is not None and len(losses.killed) == RAID_LOSSES
    assert agent.ctx.groups.count(RAID_GROUP, "ftrk") == SOVIET_RAID_FLAK_TRUCKS


# -- a whole fake match ----------------------------------------------------------------------------

MATCH_SECONDS = 600


class RaidWatch:
    """An agent wrapped so that after every state the raid group's types and the units ever in the guard are noted."""

    def __init__(self, agent) -> None:
        self.agent = agent
        self.raid_types: set[str] = set()
        self.ever_guard: set[int] = set()
        self.guards_raiding: set[int] = set()

    def choose_action(self, state, match_context):
        decision = self.agent.choose_action(state, match_context)
        groups, world = self.agent.ctx.groups, self.agent.world
        raiding = set(groups.members(RAID_GROUP))
        self.ever_guard |= set(groups.members(GUARD))
        self.raid_types |= {world.combat_units[unit_id].type for unit_id in raiding}
        self.guards_raiding |= raiding & self.ever_guard
        return decision

    def on_action_result(self, result, match_context) -> None:
        self.agent.on_action_result(result, match_context)


def soviet_raid_match(tuning: Tuning) -> RaidWatch:
    """The watch over a Soviet agent that raids all along a 600-second fake match, played with the thresholds given."""
    game = FakeRedAlert("russia", "england")
    game.place_enemy("harv", 70, 30, visible=True)
    game.place_enemy("powr", 76, 40)
    game.place_enemy("pbox", 72, 52)
    agent = RedAlertAgent(
        items=tracked_items(),
        tuning=tuning,
        chooser=StubChooser(),
        plan_selector=OpeningThen(RAID),
        settings=settings_models_off(),
    )
    watch = RaidWatch(agent)

    game.run(watch, MATCH_SECONDS * STEPS_PER_SECOND)

    assert game.tick == MATCH_SECONDS * TICKS_PER_SECOND
    return watch


def test_across_a_600_second_soviet_fake_match_with_flak_trucks_in_the_raid_quota_no_heavy_tank_or_guard_unit_raids():
    watch = soviet_raid_match(with_flak_truck_raids(tuning_off()))

    assert watch.raid_types and "3tnk" not in watch.raid_types and watch.raid_types <= {"e2", "ftrk"}
    assert watch.ever_guard and watch.guards_raiding == set()


def test_with_three_flak_trucks_in_the_mixed_raid_quota_the_soviet_raid_with_a_war_factory_is_three_of_each():
    agent, _ = raid_agent()
    ask(agent, base_state("russia", *SOVIET_BARRACKS, ("weap", 18, 56)))

    assert quota(agent.world, CompositionTuning(raid_mixed_vehicles=3)) == {"ftrk": 3, "e2": 3}


def test_across_a_600_second_soviet_fake_match_with_the_tracked_thresholds_the_raid_is_grenadiers_only():
    watch = soviet_raid_match(tuning_off())

    # With a war factory standing the raid quota takes no flak truck, though the mix makes some.
    world = watch.agent.world
    assert "weap" in {building.type for building in world.buildings.values()}
    assert "ftrk" in {unit.type for unit in world.combat_units.values()}
    assert watch.raid_types == {"e2"}
    assert watch.ever_guard and watch.guards_raiding == set()


def test_a_building_destroyed_in_sight_is_forgotten_and_the_raid_goes_on_to_the_next_target():
    # No harvester was ever seen, and four light tanks destroy a refinery in 20 s: both refineries are targets.
    refineries = [foe(610, "proc", 60, 48), foe(611, "proc", 60, 36)]
    agent, manager, _ = ready_raid(1000, buildings=refineries)
    ask(agent, allied(1100, units=tanks_at(52, 48), buildings=refineries))

    orders = ask(
        agent,
        allied(1105, units=tanks_at(52, 48), buildings=refineries[1:], events=[event(1, "enemy_killed")]),
    )

    assert 610 not in agent.world.enemy_memory and manager.target == 611
    assert orders == [{"cmd": "move", "units": [300, 301, 302, 303], "to": [53, 39]}]


# -- a standing raid group -------------------------------------------------------------------------

STANDING = tuning_off(raids=raids_off(standing=True))
# Four medium tanks of main beside the staging point, worth 3,200: enough for FINISH's attack.
MAIN_TANKS = [own(400 + number, "2tnk", 31, 46 + number) for number in range(4)]
# Four enemy medium tanks 6 cells from the construction yard: a major threat to the base.
TANKS_AT_THE_BASE = [foe(700 + number, "2tnk", 28, 46 + number) for number in range(4)]


def standing_agent(plan: str, *more, tuning: Tuning = STANDING) -> tuple:
    """An agent with a standing raid group in a fixed plan, with the managers given and the raid manager, and the
    raid manager itself."""
    manager = RaidManager()
    return new_agent(*more, manager, tuning=tuning, plan_selector=FixedPlan(plan)), manager


@pytest.mark.parametrize("plan", [HOLD, RAID, PRESS, STRIKE, FINISH])
def test_standing_a_ready_raid_group_with_a_target_sets_out_under_every_plan_after_the_opening_but_preserve(
    match_record, plan
):
    agent, manager = standing_agent(plan)

    orders = ask(agent, allied(units=light_tanks(4), enemies=[FAR_HARVESTER]))

    assert orders == LAUNCH and manager.target == 500 and match_record("raid") == [START_EVENT]


@pytest.mark.parametrize("plan", [HOLD, FINISH])
def test_standing_under_hold_and_finish_a_raid_sets_out_and_main_gets_the_orders_it_would_have_without_it(
    match_record, plan
):
    raiding, manager = standing_agent(HOLD, CombatManager())
    alone, _ = standing_agent(HOLD, CombatManager())
    ask(raiding, allied(units=light_tanks(4)))
    ask(alone, allied())
    raiding._plan_selector.plan = alone._plan_selector.plan = plan

    with_raid = ask(raiding, allied(1005, units=[*MAIN_TANKS, *light_tanks(4)], enemies=[FAR_HARVESTER]))
    without_raid = ask(alone, allied(1005, units=MAIN_TANKS, enemies=[FAR_HARVESTER]))

    assert [order for order in with_raid if order not in without_raid] == LAUNCH and manager.target == 500
    assert [order for order in with_raid if order in without_raid] == without_raid
    assert [data["phase"] for data in match_record("raid")] == ["start"]
    assert sorted(raiding.ctx.groups.members(MAIN)) == [400, 401, 402, 403]


def test_finish_s_attack_is_what_main_gets_in_the_standing_raid_group_s_cycle():
    alone, _ = standing_agent(HOLD, CombatManager())
    ask(alone, allied())
    alone._plan_selector.plan = FINISH

    orders = ask(alone, allied(1005, units=MAIN_TANKS, enemies=[FAR_HARVESTER]))

    # The comparison above is of an attack, not of two empty answers.
    assert [(order["cmd"], order["units"]) for order in orders] == [("attack_move", [400, 401, 402, 403])]


def test_standing_under_preserve_a_raid_under_way_is_called_back_with_plan_changed_and_none_starts(match_record):
    agent, manager = standing_agent(HOLD)
    assert ask(agent, allied(units=light_tanks(4), enemies=[FAR_HARVESTER])) == LAUNCH
    agent._plan_selector.plan = PRESERVE

    called_back = ask(agent, on_the_way(enemies=[FAR_HARVESTER]))
    waiting = [ask(agent, allied(tick, units=light_tanks(4), enemies=[FAR_HARVESTER])) for tick in (1010, 1600)]
    kept = sorted(agent.ctx.groups.members(RAID_GROUP))
    agent._plan_selector.plan = HOLD
    again = ask(agent, allied(1605, units=light_tanks(4), enemies=[FAR_HARVESTER]))

    assert called_back == HOME and waiting == [[], []] and kept == [300, 301, 302, 303]
    assert again == LAUNCH and manager.target == 500
    raids = match_record("raid")
    assert raids[:2] == [START_EVENT, end_event(1005, "plan_changed")] and raids[2]["tick"] == 1605


@pytest.mark.parametrize("formed_first", [False, True], ids=["never formed", "formed under HOLD"])
def test_standing_under_opening_there_is_no_raid_group(match_record, formed_first):
    agent, manager = standing_agent(HOLD if formed_first else OPENING)
    if formed_first:
        ask(agent, allied(units=light_tanks(4)))
        agent._plan_selector.plan = OPENING

    ask(agent, allied(1005, units=light_tanks(4), enemies=[FAR_HARVESTER]))

    assert agent.ctx.groups.members(RAID_GROUP) == [] and manager.target is None and match_record("raid") == []
    assert sorted(agent.ctx.groups.members(MAIN)) == [300, 301, 302, 303]


@pytest.mark.parametrize("plan", [RAID, HOLD])
def test_standing_a_major_threat_recalls_the_raid_group_exactly_as_with_the_switch_off(plan):
    def recalled(tuning: Tuning) -> tuple:
        agent, _ = standing_agent(RAID, BaseDefenseManager(), tuning=tuning)
        assert ask(agent, allied(units=light_tanks(4), enemies=[FAR_HARVESTER])) == LAUNCH
        agent._plan_selector.plan = plan
        orders = ask(agent, allied(1005, units=tanks_at(52, 48, idle=False), enemies=TANKS_AT_THE_BASE))
        groups = agent.ctx.groups
        return orders, groups.is_recalled(RAID_GROUP), sorted(groups.members(RAID_GROUP))

    switched_on, switched_off = recalled(STANDING), recalled(tuning_off())

    assert switched_on == switched_off
    orders, is_recalled, members = switched_on
    assert is_recalled and members == [300, 301, 302, 303]
    assert {"cmd": "move", "units": [300, 301, 302, 303], "to": STAGING} in orders


@pytest.mark.parametrize(
    "tuning, vehicle", [(STANDING, "1tnk"), (tuning_off(), "2tnk")], ids=["standing", "switch off"]
)
def test_standing_under_hold_the_agent_trains_the_raid_quota_s_light_tank_first_and_without_the_switch_the_mix(
    tuning, vehicle
):
    agent, _ = standing_agent(HOLD, UpkeepManager(), tuning=tuning)

    orders = ask(agent, allied(units=harvesters()))

    assert (vehicle, 1) in trained(orders) and ({"1tnk", "2tnk"} - {vehicle}).isdisjoint(dict(trained(orders)))


# -- an Allied raid of light tanks only ------------------------------------------------------------


@pytest.mark.parametrize("size", [0, 13, -1])
def test_light_tank_raid_vehicles_below_1_or_above_12_raise_a_config_error_naming_the_key(tmp_path, size):
    tuning_file = tmp_path / TUNING_FILE
    tuning_file.parent.mkdir(parents=True)
    text = (REPO_ROOT / TUNING_FILE).read_text(encoding="utf-8")
    tuning_file.write_text(text.replace("raid_vehicles = 4\n", f"raid_vehicles = {size}\n"), encoding="utf-8")

    with pytest.raises(ConfigError, match=r"raid_vehicles in \[composition\] must be a whole number of at least 1"):
        Tuning.from_file(tmp_path)


def light_tanks_only(size: int = 6) -> CompositionTuning:
    """The built-in raid quotas with the Allied raid of `size` light tanks only."""
    return CompositionTuning(allied_raid_light_tanks=True, raid_vehicles=size)


# The Allied base before its war factory: the opening's build without the war factory and the service depot.
ALLIED_BASE_WITHOUT_FACTORY = ALLIED_BASE[:5]


@pytest.mark.parametrize("size", [4, 6, 8])
@pytest.mark.parametrize("withheld", [(), ("1tnk",)], ids=["light tank on offer", "only the jeep on offer"])
def test_light_tank_the_allied_quota_is_raid_vehicles_light_tanks_and_never_a_jeep(size, withheld):
    agent, _ = raid_agent()
    ask(agent, allied(withheld=withheld))

    assert quota(agent.world, light_tanks_only(size)) == {"1tnk": size}


@pytest.mark.parametrize(
    "composition, base, withheld, wanted",
    [
        (light_tanks_only(), ALLIED_BASE_WITHOUT_FACTORY, (), {}),
        (CompositionTuning(), ALLIED_BASE_WITHOUT_FACTORY, (), {"1tnk": 4}),
        (CompositionTuning(), ALLIED_BASE, ("1tnk",), {"jeep": 4}),
        (CompositionTuning(), ALLIED_BASE, (), {"1tnk": 4}),
    ],
    ids=["light tanks only, no war factory", "switch off, no war factory", "switch off, no light tank", "switch off"],
)
def test_light_tank_the_quota_exists_once_a_war_factory_stands_and_with_the_switch_off_is_today_s(
    composition, base, withheld, wanted
):
    agent, _ = raid_agent()
    ask(agent, allied(base=base, withheld=withheld))

    assert quota(agent.world, composition) == wanted


@pytest.mark.parametrize("base", [SOVIET_BARRACKS, (*SOVIET_BARRACKS, ("weap", 18, 56))], ids=["on foot", "factory"])
def test_light_tank_the_switch_leaves_a_soviet_agent_s_quota_as_it_was(base):
    agent, _ = raid_agent()
    ask(agent, base_state("russia", *base))

    assert quota(agent.world, light_tanks_only()) == quota(agent.world, CompositionTuning())


def light_tank_agent(plan: str = RAID, *more, size: int = 6) -> tuple:
    """An agent with the Allied raid of `size` light tanks only in a fixed plan, with the managers given and the raid
    manager, and the raid manager itself."""
    manager = RaidManager()
    tuning = tuning_off(composition=light_tanks_only(size))
    return new_agent(*more, manager, tuning=tuning, plan_selector=FixedPlan(plan)), manager


@pytest.mark.parametrize("plan", [RAID, PRESS])
@pytest.mark.parametrize("made, vehicle", [(0, "1tnk"), (5, "1tnk"), (6, "2tnk")])
def test_light_tank_an_allied_agent_raiding_trains_light_tanks_while_fewer_than_six_count(plan, made, vehicle):
    agent, _ = light_tank_agent(plan, UpkeepManager())

    orders = ask(agent, allied(units=[*harvesters(), *light_tanks(made)]))

    assert (vehicle, 1) in trained(orders) and ("jeep", 1) not in trained(orders)


def test_light_tank_no_jeep_is_trained_for_the_raid_when_no_light_tank_can_be_had():
    agent, _ = light_tank_agent(RAID, UpkeepManager())

    orders = ask(agent, allied(units=harvesters(), withheld=("1tnk",)))

    assert ("jeep", 1) not in trained(orders) and ("2tnk", 1) in trained(orders)


def test_light_tank_a_jeep_in_the_raid_group_goes_back_to_main_and_none_is_taken_into_it():
    agent, _ = light_tank_agent()
    jeeps = [own(310, "jeep", 33, 47), own(311, "jeep", 33, 48)]
    ask(agent, allied(units=[*light_tanks(4), *jeeps]))
    agent.ctx.groups.assign(310, RAID_GROUP)

    ask(agent, allied(1005, units=[*light_tanks(4), *jeeps]))

    groups = agent.ctx.groups
    assert sorted(groups.members(RAID_GROUP)) == [300, 301, 302, 303] and groups.members(MAIN) == [311, 310]


@pytest.mark.parametrize(
    "units, ready",
    [
        (light_tanks(6), True),
        (light_tanks(6, gathered=5), False),
        (light_tanks(5), False),
        ([*light_tanks(5), own(310, "jeep", 33, 48)], False),
    ],
    ids=["six gathered", "five of six gathered", "five", "five and a jeep"],
)
def test_light_tank_the_group_is_ready_only_with_six_light_tanks_gathered(units, ready):
    agent, manager = light_tank_agent()

    ask(agent, allied(units=units))

    assert manager.ready is ready and 310 not in agent.ctx.groups.members(RAID_GROUP)


def test_light_tank_before_a_war_factory_stands_there_is_no_raid_group():
    agent, manager = light_tank_agent()

    orders = ask(agent, allied(units=light_tanks(6), enemies=[FAR_HARVESTER], base=ALLIED_BASE_WITHOUT_FACTORY))

    assert orders == [] and agent.ctx.groups.members(RAID_GROUP) == [] and manager.target is None


# -- the Soviet raid the composition picks ------------------------------------------------------------

SOVIET_FACTORY = (*SOVIET_BARRACKS, ("weap", 18, 56))


def soviet_raid(raid: str) -> CompositionTuning:
    """The built-in raid quotas with the Soviet raid `soviet_raid` names."""
    return CompositionTuning(soviet_raid=raid)


@pytest.mark.parametrize("base", [SOVIET_BARRACKS, SOVIET_FACTORY], ids=["on foot", "factory"])
def test_apc_raid_with_none_a_soviet_agent_has_no_raid_quota_and_its_grenadiers_stay_in_main(base):
    manager = RaidManager()
    agent = new_agent(manager, tuning=tuning_off(composition=soviet_raid("none")), plan_selector=FixedPlan(RAID))
    grenadiers = [own(300 + number, "e2", 32, 45 + number) for number in range(6)]

    ask(agent, base_state("russia", *base, units=grenadiers, visible_enemies=[HARVESTER]))

    assert quota(agent.world, soviet_raid("none")) == {} and agent.ctx.groups.members(RAID_GROUP) == []
    assert (manager.ready, manager.target) == (False, None)


@pytest.mark.parametrize("raid, eligible", [("infantry", [HOLD, RAID]), ("none", [HOLD])])
def test_apc_raid_with_none_raid_is_never_open_for_a_soviet_agent(raid, eligible):
    agent = new_agent(tuning=tuning_off(composition=soviet_raid(raid)))
    grenadiers = [own(300 + number, "e2", 32, 45 + number) for number in range(6)]

    ask(agent, base_state("russia", *SOVIET_FACTORY, units=grenadiers, visible_enemies=[HARVESTER]))

    situation = plans.observe(agent.world, agent.ctx, agent.ctx.tuning, None)
    assert plans.open_plans(situation, agent.ctx.tuning.plan_rules) == eligible
    assert situation.can_raid is (raid == "infantry")


@pytest.mark.parametrize(
    "base, wanted", [(SOVIET_BARRACKS, {"e2": 6}), (SOVIET_FACTORY, {"ftrk": 0, "e2": 3})], ids=["on foot", "factory"]
)
def test_apc_raid_with_infantry_the_soviet_quota_is_today_s(base, wanted):
    agent, _ = raid_agent()
    ask(agent, base_state("russia", *base))

    assert quota(agent.world, soviet_raid("infantry")) == quota(agent.world, CompositionTuning()) == wanted


@pytest.mark.parametrize("raid", ["infantry", "apc_rockets", "none"])
def test_apc_raid_an_allied_agent_is_unaffected_by_the_soviet_raid(raid):
    agent = new_agent(tuning=tuning_off(composition=soviet_raid(raid)))

    ask(agent, allied(units=light_tanks(4), enemies=[HARVESTER]))

    situation = plans.observe(agent.world, agent.ctx, agent.ctx.tuning, None)
    assert quota(agent.world, soviet_raid(raid)) == {"1tnk": 4}
    assert plans.open_plans(situation, agent.ctx.tuning.plan_rules) == [HOLD, RAID]


# -- the Soviet raid of rockets carried by APCs -------------------------------------------------------

APC_ROCKETS = tuning_off(composition=soviet_raid("apc_rockets"))
# A Soviet base with its war factory and two refineries, so that upkeep saves for no refinery.
SOVIET_WHOLE_BASE = (*SOVIET_FACTORY, ("proc", 26, 44), ("proc", 26, 52))


@pytest.mark.parametrize(
    "base, composition, wanted",
    [
        (SOVIET_FACTORY, soviet_raid("apc_rockets"), {"apc": 2, "e3": 10}),
        (
            SOVIET_FACTORY,
            CompositionTuning(soviet_raid="apc_rockets", raid_apcs=3, raid_apc_rockets=4),
            {"apc": 3, "e3": 12},
        ),
        (SOVIET_BARRACKS, soviet_raid("apc_rockets"), {}),
        ((*SOVIET_BARRACKS[:3], ("weap", 18, 56)), soviet_raid("apc_rockets"), {}),
    ],
    ids=["two of five", "three of four", "no war factory", "no barracks"],
)
def test_apc_raid_the_quota_is_raid_apcs_apcs_and_their_rockets_once_a_war_factory_and_a_barracks_stand(
    base, composition, wanted
):
    agent, _ = raid_agent()
    ask(agent, base_state("russia", *base))

    assert quota(agent.world, composition) == wanted


def apc_agent(plan: str = RAID, *more, tuning: Tuning = APC_ROCKETS) -> tuple:
    """An agent with the Soviet raid of rockets carried by APCs in a fixed plan, with the managers given and the raid
    manager, and the raid manager itself."""
    manager = RaidManager()
    return new_agent(*more, manager, tuning=tuning, plan_selector=FixedPlan(plan)), manager


@pytest.mark.parametrize("plan", [RAID, PRESS])
def test_apc_raid_a_soviet_base_with_a_war_factory_trains_the_apcs_and_the_rockets_under_a_raiding_plan(plan):
    agent, _ = apc_agent(plan, UpkeepManager())

    orders = ask(agent, base_state("russia", *SOVIET_WHOLE_BASE, units=harvesters()))

    assert {"apc", "e3"} <= {item for item, _ in trained(orders)}


def test_apc_raid_no_apc_is_trained_outside_a_raiding_plan():
    agent, _ = apc_agent(HOLD, UpkeepManager())

    orders = ask(agent, base_state("russia", *SOVIET_WHOLE_BASE, units=harvesters()))

    assert ("apc", 1) not in trained(orders) and trained(orders)


# Two APCs at the staging point (32, 48), and ten rocket soldiers beside them.
APCS = [own(400, "apc", 32, 46), own(401, "apc", 32, 50)]
ROCKETS = [own(300 + number, "e3", 31 + number % 2, 45 + number // 2) for number in range(10)]


def loaded_apc_agent(plan: str = RAID, *more) -> tuple:
    """An APC raid agent whose raid group holds both APCs with five rockets aboard each, as of tick 1005."""
    agent, manager = apc_agent(plan, *more)
    ask(agent, base_state("russia", *SOVIET_FACTORY, units=[*APCS, *ROCKETS]))
    # As if the agent had ordered the rockets into the APCs with the state of tick 1000.
    agent.world.note_orders(
        [
            {"cmd": "enter_transport", "units": list(range(300, 305)), "target": 400},
            {"cmd": "enter_transport", "units": list(range(305, 310)), "target": 401},
        ]
    )
    ask(agent, base_state("russia", *SOVIET_FACTORY, tick=1005, units=APCS))
    return agent, manager


def test_apc_raid_ten_rockets_aboard_count_toward_the_quota_and_none_is_trained_for_it():
    agent, _ = loaded_apc_agent()
    ctx, world = agent.ctx, agent.world

    answer = ctx.composition(RAID, "soviet", world)

    assert {apc: len(cargo) for apc, cargo in world.cargo.items()} == {400: 5, 401: 5}
    assert (quota_units(ctx, world, "e3"), quota_units(ctx, world, "apc")) == (10, 2)
    assert answer == ctx.composition(HOLD, "soviet", world) and ctx.groups.count(RAID_GROUP, "e3") == 10


@pytest.mark.parametrize(
    "base, eligible", [(SOVIET_BARRACKS, [HOLD]), (SOVIET_FACTORY, [HOLD, RAID])], ids=["barracks only", "war factory"]
)
def test_apc_raid_raid_opens_only_once_the_apc_quota_exists(base, eligible):
    agent = new_agent(tuning=APC_ROCKETS)

    ask(agent, base_state("russia", *base, visible_enemies=[HARVESTER]))

    situation = plans.observe(agent.world, agent.ctx, agent.ctx.tuning, None)
    assert plans.open_plans(situation, agent.ctx.tuning.plan_rules) == eligible


def boarding_orders(orders: list[dict]) -> dict[int, list[int]]:
    """The `enter_transport` orders of a batch: transport -> the units ordered into it."""
    return {order["target"]: order["units"] for order in with_command(orders, "enter_transport")}


def test_apc_raid_rockets_at_the_staging_point_are_ordered_into_the_apcs_at_most_five_to_each():
    agent, manager = apc_agent()
    # Eleven rockets stand by the APCs; the quota takes ten of them.
    eleven = [*ROCKETS, own(310, "e3", 33, 48)]

    orders = ask(agent, base_state("russia", *SOVIET_FACTORY, units=[*APCS, *eleven]))

    ordered = boarding_orders(orders)
    raiding = agent.ctx.groups.members(RAID_GROUP)
    assert sorted(ordered) == [400, 401] and [len(units) for units in ordered.values()] == [5, 5]
    assert sorted(ordered[400] + ordered[401]) == sorted(set(raiding) - {400, 401}) and len(raiding) == 12
    assert not manager.ready and len(agent.ctx.groups.members(MAIN)) == 1


def partly_loaded_apc_agent(aboard_401: int) -> tuple:
    """An APC raid agent whose APCs hold five rockets and `aboard_401` as of tick 1005, the rest standing beside them."""
    agent, manager = apc_agent()
    ask(agent, base_state("russia", *SOVIET_FACTORY, units=[*APCS, *ROCKETS]))
    boarded = list(range(305, 305 + aboard_401))
    agent.world.note_orders(
        [
            {"cmd": "enter_transport", "units": list(range(300, 305)), "target": 400},
            {"cmd": "enter_transport", "units": boarded, "target": 401},
        ]
    )
    outside = [rocket for rocket in ROCKETS[5:] if rocket["id"] not in boarded]
    orders = ask(agent, base_state("russia", *SOVIET_FACTORY, tick=1005, units=[*APCS, *outside]))
    return agent, manager, orders


@pytest.mark.parametrize(
    "aboard_401, ready, ordered", [(5, True, {}), (4, False, {401: [309]}), (2, False, {401: [307, 308, 309]})]
)
def test_apc_raid_the_group_is_ready_only_when_both_apcs_hold_five(aboard_401, ready, ordered):
    agent, manager, orders = partly_loaded_apc_agent(aboard_401)

    assert len(agent.world.cargo[400]) == 5 and len(agent.world.cargo[401]) == aboard_401
    assert manager.ready is ready and boarding_orders(orders) == ordered


def test_apc_raid_a_rocket_on_its_way_to_an_apc_is_not_ordered_again_and_one_idle_again_is():
    agent, _ = apc_agent()
    first = boarding_orders(ask(agent, base_state("russia", *SOVIET_FACTORY, units=[*APCS, *ROCKETS])))
    walking = [{**rocket, "idle": False, "act": "enter_transport"} for rocket in ROCKETS]

    on_their_way = ask(agent, base_state("russia", *SOVIET_FACTORY, tick=1005, units=[*APCS, *walking]))
    idle_again = ask(agent, base_state("russia", *SOVIET_FACTORY, tick=1010, units=[*APCS, *walking[1:], ROCKETS[0]]))

    # The one APC with room is the one rocket 300 was first ordered into.
    (into,) = [apc for apc, units in first.items() if 300 in units]
    assert boarding_orders(on_their_way) == {} and boarding_orders(idle_again) == {into: [300]}


def test_apc_raid_rockets_away_from_the_staging_point_gather_and_no_rocket_boards_an_apc_away_from_it():
    agent, _ = apc_agent()
    away_apc = own(401, "apc", 45, 40)
    rockets = [*ROCKETS[:5], own(305, "e3", 45, 42)]

    orders = ask(agent, base_state("russia", *SOVIET_FACTORY, units=[APCS[0], away_apc, *rockets]))

    assert boarding_orders(orders) == {400: [300, 301, 302, 303, 304]}
    assert {"cmd": "move", "units": [401, 305], "to": STAGING} in orders


def test_apc_raid_a_damaged_apc_at_the_staging_point_unloads_once_and_leaves_the_group_empty():
    agent, _ = loaded_apc_agent()
    damaged = [{**APCS[0], "hp": 50}, APCS[1]]

    unloading = ask(agent, base_state("russia", *SOVIET_FACTORY, tick=1010, units=damaged))
    still = ask(agent, base_state("russia", *SOVIET_FACTORY, tick=1015, units=damaged))
    out = ask(agent, base_state("russia", *SOVIET_FACTORY, tick=1020, units=[*damaged, *ROCKETS[:5]]))

    assert with_command(unloading, "unload") == [{"cmd": "unload", "units": [400]}]
    assert with_command(still, "unload") == [] and boarding_orders(out) == {}
    assert agent.ctx.groups.group_of(400) == MAIN and agent.ctx.groups.group_of(300) == RAID_GROUP


def test_apc_raid_a_rocket_below_fit_hp_at_the_staging_point_leaves_the_group_and_does_not_board():
    agent, _ = apc_agent()
    rockets = [*ROCKETS[:9], {**ROCKETS[9], "hp": 40}]
    ask(agent, base_state("russia", *SOVIET_FACTORY, units=[*APCS, *ROCKETS]))

    orders = ask(agent, base_state("russia", *SOVIET_FACTORY, tick=1005, units=[*APCS, *rockets]))

    assert agent.ctx.groups.group_of(309) == MAIN and 309 not in boarding_orders(orders).get(401, [])


def test_apc_raid_a_group_disbanded_with_rockets_aboard_has_its_apcs_unload_and_all_of_it_goes_to_main():
    agent, _ = loaded_apc_agent()
    agent._plan_selector.plan = STRIKE

    orders = ask(agent, base_state("russia", *SOVIET_FACTORY, tick=1010, units=APCS))

    assert orders == [{"cmd": "unload", "units": [400, 401]}] and agent.ctx.groups.roster(RAID_GROUP) == []
    assert agent.ctx.groups.count(MAIN, "e3") == 10


def build_and_place(game: FakeRedAlert, item: str) -> None:
    """Have the fake game build an item and place it as soon as it is ready, within a minute of game time."""
    game.play_action({"type": "orders", "orders": [{"cmd": "build", "item": item}]})
    for _ in range(SECONDS_PER_MINUTE * STEPS_PER_SECOND):
        if item in game.state()["legal_actions"]["place"]:
            break
        game.step()
    game.play_action({"type": "orders", "orders": [{"cmd": "place", "item": item}]})
    game.step()


def fake_soviet_base() -> FakeRedAlert:
    """A fake Soviet match whose MCV is deployed and which has built two power plants, a barracks, a refinery and a war
    factory."""
    game = FakeRedAlert("russia", "england")
    (mcv,) = game.own_units()
    game.play_action({"type": "orders", "orders": [{"cmd": "deploy", "units": [mcv["id"]]}]})
    game.step()
    for item in ("powr", "powr", "barr", "proc", "weap"):
        build_and_place(game, item)
    return game


def test_apc_raid_in_the_fake_game_the_rockets_board_both_apcs_five_each_and_the_group_is_ready():
    game = fake_soviet_base()
    apcs = [game.add_own("apc", 32, 46), game.add_own("apc", 32, 50)]
    rockets = [game.add_own("e3", 30 + number % 3, 45 + number // 3) for number in range(10)]
    agent, manager = apc_agent()

    game.run(agent, 20 * STEPS_PER_SECOND)

    assert [len(game.aboard(apc)) for apc in apcs] == [5, 5] and sorted(sum(map(game.aboard, apcs), [])) == rockets
    assert {apc: len(cargo) for apc, cargo in agent.world.cargo.items()} == dict.fromkeys(apcs, 5) and manager.ready


# The loaded APC raid sets out at tick 1010 at the harvester at (60, 48): its APCs drive to (57, 48), 3 cells short.
APC_LAUNCH = [{"cmd": "move", "units": [400, 401], "to": [57, 48]}]
APC_START_EVENT = {
    "phase": "start",
    "tick": 1010,
    "game_seconds": 40.4,
    "target": 500,
    "target_type": "harv",
    "group_size": 12,
    "value": 2 * 850 + 10 * 300,
    "apcs": 2,
    "rockets": 10,
    "rockets_lost": 0,
}


def soviet_state(tick: int, units, enemies=(FAR_HARVESTER,), **observation) -> dict:
    """A state of the Soviet base with a war factory, these own units and these enemies in sight."""
    return base_state(
        "russia", *SOVIET_FACTORY, tick=tick, units=list(units), visible_enemies=list(enemies), **observation
    )


def apcs_at(x: int, *, hp: int = 100, idle: bool = True) -> list[dict]:
    """The raid's two APCs at column x, two cells above and below row 48."""
    return [own(400, "apc", x, 46, hp, idle=idle), own(401, "apc", x, 50, hp, idle=idle)]


def rockets_at(x: int) -> list[dict]:
    """The raid's ten rockets out of their APCs around column x, rows 45 to 51."""
    return [own(300 + number, "e3", x + number % 2, 45 + number % 7) for number in range(10)]


def launched_apc_raid() -> tuple:
    """An APC raid agent whose loaded raid set out at tick 1010, its manager and its orders then."""
    agent, manager = loaded_apc_agent()
    return (agent, manager, ask(agent, soviet_state(1010, APCS)))


def test_apc_raid_a_loaded_group_sets_out_with_a_crew_of_twelve_and_its_apcs_drive_to_3_cells_short(match_record):
    agent, manager, orders = launched_apc_raid()

    assert orders == APC_LAUNCH and manager.target == 500 and len(manager.mission.crew) == 12
    assert match_record("raid") == [APC_START_EVENT]


@pytest.mark.parametrize("apc_hp", [100, 10], ids=["whole", "the APCs at 10%"])
def test_apc_raid_a_loaded_raid_on_its_way_does_not_break_off_for_losses_or_health_and_no_rocket_walks(
    match_record, apc_hp
):
    agent, manager, _ = launched_apc_raid()

    orders = ask(agent, soviet_state(1015, apcs_at(45, hp=apc_hp, idle=False)))

    assert orders == [] and manager.target == 500 and match_record("raid") == [APC_START_EVENT]


def test_apc_raid_while_the_rockets_ride_the_estimate_is_of_the_apcs_and_four_tanks_make_it_break_off(match_record):
    agent, manager, _ = launched_apc_raid()
    # Four enemy light tanks, 2,800 against the APCs' 1,700: 0.61; with the rockets aboard counted it would be 1.68.
    tanks = [foe(510 + number, "1tnk", 49, 46 + number) for number in range(4)]

    orders = ask(agent, soviet_state(1015, apcs_at(45, idle=False), enemies=[FAR_HARVESTER, *tanks]))

    assert orders == [{"cmd": "move", "units": [400, 401], "to": STAGING}] and manager.target is None
    assert match_record("raid")[-1]["reason"] == "outmatched"


def test_apc_raid_at_3_cells_from_its_target_the_apcs_unload_and_the_rockets_out_attack_it():
    agent, _, _ = launched_apc_raid()

    unloading = ask(agent, soviet_state(1100, apcs_at(57)))
    out = ask(agent, soviet_state(1105, [*apcs_at(57), *rockets_at(57)]))

    assert unloading == [{"cmd": "unload", "units": [400, 401]}] and agent.world.cargo == {}
    assert out == [{"cmd": "attack", "units": list(range(300, 310)), "target": 500}]


def test_apc_raid_an_apc_more_than_3_cells_from_the_rockets_moves_to_them_and_an_unload_is_sent_again_in_75_ticks():
    agent, _, _ = launched_apc_raid()
    ask(agent, soviet_state(1100, apcs_at(57)))
    loaded = [own(400, "apc", 57, 46), own(401, "apc", 53, 50)]

    first = ask(agent, soviet_state(1105, [*loaded, *rockets_at(59)[:5]]))
    again = ask(agent, soviet_state(1175, [*loaded, *rockets_at(59)[:5]]))

    assert with_command(first, "unload") == [] and with_command(again, "unload") == [{"cmd": "unload", "units": [401]}]
    assert {"cmd": "move", "units": [401], "to": [59, 47]} in first


def test_apc_raid_losing_an_apc_with_five_aboard_is_six_losses_of_twelve_and_ends_the_raid(match_record):
    agent, manager, _ = launched_apc_raid()

    orders = ask(agent, soviet_state(1015, apcs_at(45, idle=False)[1:]))

    assert orders == [{"cmd": "move", "units": [401], "to": STAGING}] and manager.target is None
    lost = {key: match_record("raid")[-1][key] for key in ("reason", "units_lost", "value_lost", "rockets_lost")}
    assert lost == {"reason": "losses", "units_lost": 6, "value_lost": 850 + 5 * 300, "rockets_lost": 5}


def apc_raid_in_the_fake_game(seconds: int) -> tuple:
    """A fake Soviet match with two APCs and ten rockets at the staging point and an enemy harvester in sight at
    (60, 48), played by an APC raid agent for so many seconds: the game, the harvester's id and the agent."""
    game = fake_soviet_base()
    harvester = game.place_enemy("harv", 60, 48, visible=True)
    for x, y in ((32, 46), (32, 50)):
        game.add_own("apc", x, y)
    for number in range(10):
        game.add_own("e3", 30 + number % 3, 45 + number // 3)
    agent, _ = apc_agent()
    game.run(agent, seconds * STEPS_PER_SECOND)
    return game, harvester, agent


def own_cells(game: FakeRedAlert, unit_type: str) -> dict[int, tuple[int, int]]:
    """The cell of each of the player's units of a type in the fake game, by id."""
    return {unit["id"]: (unit["x"], unit["y"]) for unit in game.own_units() if unit["type"] == unit_type}


def sent(agent, cmd: str) -> list[dict]:
    """The orders of a command the agent sent, in the order it sent them."""
    return [entry.order for entry in agent.ledger.entries if entry.order["cmd"] == cmd]


def test_apc_raid_in_the_fake_game_the_apcs_unload_by_the_harvester_and_the_rockets_attack_it():
    game, harvester, agent = apc_raid_in_the_fake_game(30)

    rockets = own_cells(game, "e3")

    assert sent(agent, "unload") and len(rockets) == 10
    assert max(math.dist((60, 48), cell) for cell in rockets.values()) <= 5
    assert {"cmd": "attack", "units": sorted(rockets), "target": harvester} in sent(agent, "attack")


# A second harvester, 18 cells north of the first.
SECOND_HARVESTER = foe(501, "harv", 58, 30)
# A pillbox whose coverage (6 + 2 cells) takes in the rockets out at (57..58, 45..51).
PILLBOX_BY_THE_ROCKETS = foe(600, "pbox", 63, 48)
HOME_APCS = {"cmd": "move", "units": [400, 401], "to": STAGING}


def out_at_the_target(*more, enemies=()) -> tuple:
    """An APC raid agent, with the managers given, whose raid unloaded by its harvester at tick 1100 and has its ten
    rockets out at tick 1105, these other enemies in sight; and the raid manager."""
    agent, manager = loaded_apc_agent(RAID, *more)
    ask(agent, soviet_state(1010, APCS, enemies=[FAR_HARVESTER, *enemies]))
    ask(agent, soviet_state(1100, apcs_at(57), enemies=[FAR_HARVESTER, *enemies]))
    ask(agent, soviet_state(1105, [*apcs_at(57), *rockets_at(57)], enemies=[FAR_HARVESTER, *enemies]))
    return agent, manager


def all_ten_ordered_in(orders: list[dict]) -> bool:
    """Whether the batch orders the ten rockets into the two APCs, five into each."""
    ordered = boarding_orders(orders)
    return sorted(ordered) == [400, 401] and sorted(sum(ordered.values(), [])) == list(range(300, 310))


def test_apc_raid_on_its_target_destroyed_the_rockets_board_again_and_the_apcs_head_for_the_next_target(match_record):
    agent, manager = out_at_the_target(enemies=[SECOND_HARVESTER])
    killed = [event(1, "enemy_killed", count=1)]

    boarding = ask(agent, soviet_state(1110, [*apcs_at(57), *rockets_at(57)], [SECOND_HARVESTER], events=killed))
    aboard = ask(agent, soviet_state(1115, apcs_at(57), [SECOND_HARVESTER]))

    approach = raid_routes.approach_cell((32, 48), (58, 30), unload_route(raids_off()))
    assert all_ten_ordered_in(boarding) and (manager.target, manager.mission.kills) == (501, 1)
    assert (
        aboard == [{"cmd": "move", "units": [400, 401], "to": list(approach)}]
        and manager.mission.ride.phase == "riding"
    )
    assert [raid["phase"] for raid in match_record("raid")] == ["start"]


def test_apc_raid_on_its_last_target_destroyed_the_rockets_board_again_and_the_apcs_go_home(match_record):
    agent, manager = out_at_the_target()
    killed = [event(1, "enemy_killed", count=1)]

    boarding = ask(agent, soviet_state(1110, [*apcs_at(57), *rockets_at(57)], [], events=killed))
    aboard = ask(agent, soviet_state(1115, apcs_at(57), []))

    assert all_ten_ordered_in(boarding) and aboard == [HOME_APCS] and manager.target is None
    ended = match_record("raid")[-1]
    assert (ended["tick"], ended["reason"], ended["kills"], ended["rockets_lost"]) == (1115, "no_target", 1, 0)


def test_apc_raid_on_a_break_off_the_rockets_board_again_and_the_apcs_go_home_once_all_are_aboard(match_record):
    agent, manager = out_at_the_target()
    pillbox = {"visible_enemy_buildings": [PILLBOX_BY_THE_ROCKETS]}

    boarding = ask(agent, soviet_state(1110, [*apcs_at(57), *rockets_at(57)], **pillbox))
    aboard = ask(agent, soviet_state(1115, apcs_at(57), **pillbox))

    assert all_ten_ordered_in(boarding) and aboard == [HOME_APCS] and manager.target is None
    ended = match_record("raid")[-1]
    assert (ended["tick"], ended["reason"], ended["units_lost"], ended["apcs"], ended["rockets"]) == (
        1115,
        "covered",
        0,
        2,
        10,
    )


def test_apc_raid_a_rocket_still_out_75_ticks_after_the_reload_began_is_sent_home_alone_and_leaves_the_group():
    agent, manager = out_at_the_target()
    pillbox = {"visible_enemy_buildings": [PILLBOX_BY_THE_ROCKETS]}
    straggler = [{**rockets_at(57)[9], "idle": False}]
    ask(agent, soviet_state(1110, [*apcs_at(57), *rockets_at(57)], **pillbox))

    waiting = [ask(agent, soviet_state(tick, [*apcs_at(57), *straggler], **pillbox)) for tick in (1115, 1184)]
    leaving = ask(agent, soviet_state(1185, [*apcs_at(57), *straggler], **pillbox))

    assert waiting == [[], []] and manager.target is None
    assert leaving == [{"cmd": "move", "units": [309], "to": STAGING}, HOME_APCS]
    aboard = sorted(agent.ctx.groups.aboard(RAID_GROUP))
    assert agent.ctx.groups.group_of(309) == MAIN and aboard == list(range(300, 309))


def test_apc_raid_when_the_plan_stops_raiding_the_rockets_board_again_and_the_raid_ends_with_plan_changed(match_record):
    agent, manager = out_at_the_target()
    agent._plan_selector.plan = HOLD

    boarding = ask(agent, soviet_state(1110, [*apcs_at(57), *rockets_at(57)]))
    aboard = ask(agent, soviet_state(1115, apcs_at(57)))

    assert all_ten_ordered_in(boarding) and aboard == [HOME_APCS] and manager.target is None
    assert match_record("raid")[-1]["reason"] == "plan_changed" and agent.ctx.groups.count(RAID_GROUP, "e3") == 10


def test_apc_raid_a_major_threat_has_the_rockets_board_again_and_the_apcs_come_home_base_defense_leaving_them_be(
    match_record,
):
    agent, manager = out_at_the_target(BaseDefenseManager())
    threat = [FAR_HARVESTER, *TANKS_AT_THE_BASE]

    boarding = ask(agent, soviet_state(1110, [*apcs_at(57), *rockets_at(57)], threat))
    aboard = ask(agent, soviet_state(1115, apcs_at(57), threat))

    raid_units = set(range(300, 310)) | {400, 401}
    assert agent.ctx.groups.is_recalled(RAID_GROUP) and all_ten_ordered_in(boarding)
    assert not [order for order in with_command(boarding, "move", "attack_move") if raid_units & set(order["units"])]
    assert HOME_APCS in aboard and match_record("raid")[-1]["reason"] == "recalled"


def test_apc_raid_with_the_war_factory_lost_the_rockets_board_again_and_the_raid_ends_with_plan_changed(match_record):
    agent, manager = out_at_the_target()
    no_factory = base_state("russia", *SOVIET_BARRACKS, tick=1110, units=[*apcs_at(57), *rockets_at(57)])

    boarding = ask(agent, no_factory)

    assert all_ten_ordered_in(boarding) and manager.mission.ride.ending == "plan_changed"
    assert match_record("raid")[-1]["phase"] == "start"


# -- RAID only while a raid could set out (launchable) -----------------------------------------------

LAUNCHABLE = tuning_off(raids=raids_off(launchable_only=True))
# Two light tanks of main 30 cells forward of the staging point: not on hand.
TWO_FORWARD = [own(302, "1tnk", 62, 46), own(303, "1tnk", 62, 47)]
# Both side waypoints of the way from the staging point to the harvester at (60, 48) are covered, and its line too.
EVERY_WAY_COVERED = [NORTH_PILLBOX, foe(601, "pbox", 42, 56)]


def launch_blocker_after(
    state: dict,
    tuning: Tuning = LAUNCHABLE,
    *,
    raid_group=(),
    guard=(),
    army: frozenset[int] = frozenset(),
    destroyed: frozenset[int] = frozenset(),
) -> str | None:
    """Why a raid could not set out as an agent with no manager sees it once it has taken a state in, with the units
    given moved into the raid group or the home guard, those given in main's army and the targets given destroyed."""
    agent = new_agent(tuning=tuning, plan_selector=FixedPlan(HOLD))
    ask(agent, state)
    ctx, world = agent.ctx, agent.world
    for unit_id in raid_group:
        ctx.groups.assign(unit_id, RAID_GROUP)
    for unit_id in guard:
        ctx.groups.assign(unit_id, GUARD)
    ctx.army, ctx.raids_destroyed = army, destroyed
    known = bool(raid_targets.targets(world, plans.raid_group_types(world, tuning), tuning))
    return launch_blocker(world, ctx, known)


@pytest.mark.parametrize(
    "enemies, buildings", [((), ()), ([foe(500, "harv", 60, 30)], [PILLBOX])], ids=["none seen", "covered"]
)
def test_launchable_with_no_raid_target_known_a_raid_could_not_set_out_for_that_reason(enemies, buildings):
    state = allied(units=light_tanks(4), enemies=enemies, buildings=buildings)

    assert launch_blocker_after(state) == "no raid target known"


def test_launchable_four_fit_light_tanks_of_main_at_the_staging_point_and_a_target_let_a_raid_set_out():
    assert launch_blocker_after(allied(units=light_tanks(4), enemies=[FAR_HARVESTER])) is None


def test_launchable_two_of_four_light_tanks_on_hand_give_need_4_1tnk_have_2_1tnk():
    state = allied(units=[*light_tanks(2), *TWO_FORWARD], enemies=[FAR_HARVESTER])

    assert launch_blocker_after(state) == "no units for a raid on hand: need 4 1tnk, have 2 1tnk"


# A fourth light tank 10 cells from the base's center at (22, 48), 17 from the staging point: on hand unless it fights.
BY_THE_BASE = own(303, "1tnk", 22, 58)
# An enemy rifle 4 cells from it, within its range (4.75) and 2 cells; 17 cells from the three at the staging point.
RIFLE_BY_THE_BASE = foe(520, "e1", 22, 62)


@pytest.mark.parametrize(
    "units, enemies, moved, blocker",
    [
        ([*light_tanks(3), BY_THE_BASE], [], {}, None),
        ([*light_tanks(3), BY_THE_BASE], [RIFLE_BY_THE_BASE], {}, "have 3 1tnk"),
        (light_tanks(4), [], {"guard": (300,)}, "have 3 1tnk"),
        (light_tanks(4), [], {"army": frozenset({300})}, "have 3 1tnk"),
        ([*light_tanks(3), own(303, "1tnk", 32, 49, hp=59)], [], {}, "have 3 1tnk"),
        ([*light_tanks(3), own(303, "1tnk", 48, 48)], [], {}, "have 3 1tnk"),
        ([*light_tanks(3), own(303, "1tnk", 62, 48, hp=40)], [], {"raid_group": (303,)}, None),
        ([*light_tanks(4), *[own(304 + number, "1tnk", 33, 46 + number) for number in range(3)]], [], {}, None),
    ],
    ids=[
        "within 15 cells of the base's center",
        "in a fight",
        "in the home guard",
        "in main's army",
        "at 59% health",
        "16 cells from the staging point",
        "the raid group's own, forward and damaged",
        "seven on hand for a quota of four",
    ],
)
def test_launchable_the_units_on_hand_are_the_raid_group_s_and_main_s_fit_ones_near_home_in_no_fight(
    units, enemies, moved, blocker
):
    found = launch_blocker_after(allied(units=units, enemies=[FAR_HARVESTER, *enemies]), **moved)

    assert found == (None if blocker is None else f"no units for a raid on hand: need 4 1tnk, {blocker}")


def test_launchable_with_every_way_to_every_target_covered_no_safe_route_leads_to_a_raid_target():
    state = allied(units=light_tanks(4), enemies=[FAR_HARVESTER], buildings=EVERY_WAY_COVERED)

    assert launch_blocker_after(state) == "no safe route to a raid target"


def test_launchable_a_target_the_raids_destroyed_is_passed_over_and_the_check_comes_before_the_units():
    on_hand = allied(units=light_tanks(4), enemies=[FAR_HARVESTER])
    short = allied(units=light_tanks(2), enemies=[FAR_HARVESTER])

    assert launch_blocker_after(on_hand, destroyed=frozenset({500})) == "no safe route to a raid target"
    assert launch_blocker_after(short, destroyed=frozenset({500})) == "no safe route to a raid target"


LAUNCHABLE_APCS = dataclasses.replace(APC_ROCKETS, raids=LAUNCHABLE.raids)


def test_launchable_a_soviet_apc_raid_short_of_one_apc_gives_need_2_apc_10_e3_have_1_apc_10_e3():
    state = soviet_state(1000, [APCS[0], *ROCKETS])

    assert (
        launch_blocker_after(state, LAUNCHABLE_APCS)
        == "no units for a raid on hand: need 2 apc + 10 e3, have 1 apc + 10 e3"
    )


def test_launchable_the_rockets_aboard_the_apcs_of_a_kept_raid_group_are_on_hand():
    agent, _ = loaded_apc_agent()
    agent._plan_selector.plan = HOLD
    ask(agent, soviet_state(1010, APCS))

    assert agent.ctx.groups.count(RAID_GROUP, "e3") == 10 and launch_blocker(agent.world, agent.ctx, True) is None


def test_launchable_a_mixed_quota_counts_each_type_up_to_its_own_quota_joined_by_plus():
    tuning = dataclasses.replace(LAUNCHABLE, composition=CompositionTuning(raid_mixed_vehicles=3))
    flak = [own(300 + number, "ftrk", 32, 45 + number) for number in range(5)]

    found = launch_blocker_after(soviet_state(1000, [*flak, own(310, "e2", 31, 47)]), tuning)

    assert found == "no units for a raid on hand: need 3 ftrk + 3 e2, have 3 ftrk + 1 e2"


# The four light tanks 8 to 11 cells south of the base's center at (22, 48): on hand, but not at the staging point.
BY_THE_BASE_FOUR = [own(300 + number, "1tnk", 22, 56 + number) for number in range(4)]


def test_launchable_four_light_tanks_at_the_staging_point_open_raid_join_the_raid_group_and_set_out_that_cycle(
    match_record,
):
    agent = new_agent(RaidManager(), tuning=LAUNCHABLE)

    orders = ask(agent, allied(units=light_tanks(4), enemies=[FAR_HARVESTER]))

    assert (agent.current_plan, sorted(agent.ctx.groups.members(RAID_GROUP)), orders) == (
        RAID,
        [300, 301, 302, 303],
        LAUNCH,
    )
    assert [(raid["phase"], raid["tick"]) for raid in match_record("raid")] == [("start", 1000)]


def test_launchable_when_raid_is_chosen_the_four_on_hand_join_that_cycle_and_the_raid_sets_out_once_gathered(
    match_record,
):
    manager = RaidManager()
    agent = new_agent(manager, tuning=LAUNCHABLE)

    chosen = ask(agent, allied(units=BY_THE_BASE_FOUR, enemies=[FAR_HARVESTER]))
    joined = (agent.current_plan, sorted(agent.ctx.groups.members(RAID_GROUP)), manager.mission)
    gathered = ask(agent, allied(1005, units=light_tanks(4), enemies=[FAR_HARVESTER]))

    assert joined == (RAID, [300, 301, 302, 303], None)
    assert chosen == [{"cmd": "move", "units": [300, 301, 302, 303], "to": STAGING}] and gathered == LAUNCH
    assert [(raid["phase"], raid["tick"]) for raid in match_record("raid")] == [("start", 1005)]


@pytest.mark.parametrize(
    "tuning, joined", [(LAUNCHABLE, [300, 301]), (tuning_off(), [300, 301, 302, 303])], ids=["launchable", "switch off"]
)
def test_launchable_the_raid_borrows_only_the_units_on_hand_and_with_the_switch_off_any_fit_one_of_main(tuning, joined):
    agent = new_agent(RaidManager(), tuning=tuning, plan_selector=FixedPlan(RAID))

    ask(agent, allied(units=[*light_tanks(2), *TWO_FORWARD], enemies=[FAR_HARVESTER]))

    assert sorted(agent.ctx.groups.members(RAID_GROUP)) == joined


def test_launchable_the_raid_borrows_no_guard_unit_and_no_unit_in_a_fight_and_the_nearest_on_hand_first():
    agent = new_agent(RaidManager(), tuning=LAUNCHABLE, plan_selector=FixedPlan(HOLD))
    # 302 and 301 at the staging point, 304 2 cells from it, 305 6 and 306 11; 300 goes to the guard, 303 fights.
    farther = [own(304, "1tnk", 33, 50), own(305, "1tnk", 36, 52), own(306, "1tnk", 40, 56)]
    state = allied(units=[*light_tanks(3), BY_THE_BASE, *farther], enemies=[FAR_HARVESTER, RIFLE_BY_THE_BASE])
    ask(agent, state)
    agent.ctx.groups.assign(300, GUARD)
    agent._plan_selector.plan = RAID

    ask(agent, {**state, "state_version": 1005, "observation": {**state["observation"], "tick": 1005}})

    assert sorted(agent.ctx.groups.members(RAID_GROUP)) == [301, 302, 304, 305]
    assert (agent.ctx.groups.group_of(300), agent.ctx.groups.group_of(303)) == (GUARD, MAIN)


def held_from_gathering(tuning: Tuning, gathered_at: int | None = None) -> tuple:
    """An agent of the script's plan rules and the raid manager, and what it saw, through states from tick 1000 to
    3005 in which RAID is chosen with four light tanks on hand by the base, which stay there, held from gathering,
    unless `gathered_at` puts them at the staging point from that tick on, the uncovered harvester in sight all along.

    What it saw: tick -> (the plan, the situation's launch blocker, whether RAID is open, the raid group).
    """
    agent = new_agent(RaidManager(), tuning=tuning)
    seen = {}
    for tick in (1000, 1005, 2500, 2505, 2510, 3004, 3005):
        is_gathered = gathered_at is not None and tick >= gathered_at
        tanks = light_tanks(4) if is_gathered else BY_THE_BASE_FOUR
        ask(agent, allied(tick, units=tanks, enemies=[FAR_HARVESTER]))
        situation = agent._plan_selector.situation
        is_open = RAID in plans.open_plans(situation, agent.ctx.tuning.plan_rules)
        group = sorted(agent.ctx.groups.members(RAID_GROUP))
        seen[tick] = (agent.current_plan, situation.raid_launch_blocker, is_open, group)
    return agent, seen


FOUR = [300, 301, 302, 303]


def test_launchable_with_no_raid_start_within_1500_ticks_of_raid_chosen_raid_closes_for_resend_after_ticks(
    match_record,
):
    agent, seen = held_from_gathering(LAUNCHABLE)

    assert seen[1000] == seen[2500] == (RAID, None, True, FOUR)
    # 1,505 ticks after RAID was chosen: the raid group goes back to main, and RAID is shut until 2,505 + 500.
    assert seen[2505][3] == [] and agent.ctx.raid_shut_until == 3005
    assert seen[2510] == seen[3004] == (HOLD, "raid did not set out", False, [])
    # Open again once the 500 ticks are over; HOLD, set at 2,510, is still held.
    assert seen[3005] == (HOLD, None, True, []) and sorted(agent.ctx.groups.members(MAIN)) == FOUR
    events = match_record()
    changes = [event["data"] for event in events if event["kind"] == "plan_change"]
    assert [(change["old"], change["new"], change["tick"]) for change in changes] == [
        (None, RAID, 1000),
        (RAID, HOLD, 2510),
    ]
    assert changes[-1]["inputs"]["raid_launch_blocker"] == "raid did not set out"
    assert [event for event in events if event["kind"] == "raid"] == []


def test_launchable_a_raid_that_sets_out_within_1500_ticks_ends_the_wait_and_raid_stays_in_force(match_record):
    agent, seen = held_from_gathering(LAUNCHABLE, gathered_at=2500)

    assert agent.ctx.raid_shut_until == 0 and {seen[tick][0] for tick in seen} == {RAID}
    assert [(raid["phase"], raid["tick"]) for raid in match_record("raid")] == [("start", 2500)]


def test_launchable_with_the_switch_off_raid_waits_for_its_group_however_long_it_takes():
    agent, seen = held_from_gathering(tuning_off())

    assert [seen[tick] for tick in (2505, 2510, 3004, 3005)] == [(RAID, None, True, FOUR)] * 4
    assert agent.ctx.raid_shut_until == 0


def test_launchable_while_the_staging_point_is_not_known_no_safe_route_leads_from_it():
    state = allied(units=light_tanks(4), enemies=[FAR_HARVESTER], base_center=None)

    assert launch_blocker_after(state) == "no safe route to a raid target"


# -- exposed trucks (prefer_exposed_trucks) ------------------------------------------------------------------------

# An ore field 30 cells from the enemy's start at (74, 48), a harvester at work on it, and one on no field nearer the
# staging point.
TRUCK_FIELD = OrePatch((50, 30), frozenset((50 + dx, 30 + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)))
EXPOSED_TRUCK, NEARER_TRUCK = foe(700, "harv", 50, 30), foe(750, "harv", 45, 50)


@pytest.mark.parametrize("prefer, target", [(True, 700), (False, 750)], ids=["prefer on", "prefer off"])
def test_exposed_trucks_a_ready_raid_goes_for_the_exposed_harvester_with_prefer_exposed_trucks_on(prefer, target):
    manager = RaidManager()
    tuning = tuning_off(scouting=scouting_off(exposed_trucks=True), raids=raids_off(prefer_exposed_trucks=prefer))
    agent = new_agent(ScoutingManager(), manager, tuning=tuning, plan_selector=FixedPlan(RAID))
    ask(agent, allied(995, units=light_tanks(4)))
    agent.ctx.terrain = Terrain([TRUCK_FIELD])

    ask(agent, allied(1000, units=light_tanks(4), enemies=[EXPOSED_TRUCK, NEARER_TRUCK]))

    assert manager.ready and manager.target == target


# -- exposed trucks launch a raid (raid_exposed_trucks) ----------------------------------------------------------------

# The raids as they ship, launchable_only and raid_exposed_trucks on, and with the switch off; the rest switched off.
EXPOSED_RAID = tuning_off(raids=RaidTuning())
EXPOSED_RAID_OFF = tuning_off(raids=RaidTuning(raid_exposed_trucks=False))
# Two harvesters seen at the truck field, the one at (50, 30) nearest our base, and one at another field.
EXPOSED_PAIR = [EXPOSED_TRUCK, foe(701, "harv", 52, 31)]
OTHER_FIELD_TRUCK = foe(710, "harv", 66, 60)


def opportunity(tick: int, *, guard: int = 0, seen_seconds_ago: int = 5, ids=(700, 701), nearest=(50, 30)):
    """An exposed-trucks opportunity at the truck field as scouting publishes it, its harvesters seen that long before
    `tick`."""
    seen_tick = tick - seen_seconds_ago * TICKS_PER_SECOND
    return ExposedTrucks((50, 30), len(ids), tuple(ids), guard, seen_tick, nearest)


def exposed_raid_agent(plan: str, published, tuning: Tuning = EXPOSED_RAID) -> tuple:
    """An agent in a fixed plan with the raid manager, whose scouting slot publishes the opportunities
    `published(tick)` gives in each cycle, and the raid manager."""
    manager = RaidManager()

    def publish(world, ctx) -> list:
        ctx.exposed_trucks = tuple(published(world.tick))
        return []

    scouting = ScriptedManager("scouting", publish)
    return new_agent(scouting, manager, tuning=tuning, plan_selector=FixedPlan(plan)), manager


def starts(events: list[dict]) -> list[tuple]:
    """The target, its type and the reason of every `raid` start event, None for one that gives no reason."""
    return [(raid["target"], raid["target_type"], raid.get("reason")) for raid in events if raid["phase"] == "start"]


@pytest.mark.parametrize("plan", [RAID, HOLD, FINISH])
def test_exposed_raid_a_fresh_unguarded_opportunity_launches_the_raid_at_its_nearest_harvester(match_record, plan):
    agent, manager = exposed_raid_agent(plan, lambda tick: [opportunity(tick)])

    orders = ask(agent, allied(1000, units=light_tanks(4), enemies=[*EXPOSED_PAIR, NEARER_TRUCK]))

    approach = raid_routes.approach_cell(tuple(STAGING), (50, 30), RaidTuning())
    assert sorted(agent.ctx.groups.members(RAID_GROUP)) == FOUR and manager.target == 700
    assert orders == [{"cmd": "move", "units": FOUR, "to": list(approach)}]
    assert starts(match_record("raid")) == [(700, "harv", "exposed_trucks")]


@pytest.mark.parametrize(
    "guard, seen_seconds_ago", [(300, 5), (0, 30), (201, 5), (0, 21)], ids=["guard 300", "30 s ago", "201", "21 s"]
)
def test_exposed_raid_an_opportunity_guarded_by_more_than_200_or_seen_more_than_20_s_ago_launches_nothing_under_hold(
    match_record, guard, seen_seconds_ago
):
    agent, manager = exposed_raid_agent(
        HOLD, lambda tick: [opportunity(tick, guard=guard, seen_seconds_ago=seen_seconds_ago)]
    )

    orders = ask(agent, allied(1000, units=light_tanks(4), enemies=EXPOSED_PAIR))

    assert (orders, agent.ctx.groups.members(RAID_GROUP), manager.target) == ([], [], None)
    assert match_record("raid") == []


@pytest.mark.parametrize("guard, seen_seconds_ago", [(200, 5), (0, 20)], ids=["guard 200", "20 s ago"])
def test_exposed_raid_a_guard_of_200_and_a_sighting_20_s_ago_still_launch(guard, seen_seconds_ago):
    agent, manager = exposed_raid_agent(
        HOLD, lambda tick: [opportunity(tick, guard=guard, seen_seconds_ago=seen_seconds_ago)]
    )

    ask(agent, allied(1000, units=light_tanks(4), enemies=EXPOSED_PAIR))

    assert manager.target == 700 and manager.mission.exposed


def test_exposed_raid_under_raid_an_opportunity_guarded_by_300_leaves_the_ranking_s_choice(match_record):
    agent, manager = exposed_raid_agent(RAID, lambda tick: [opportunity(tick, guard=300)])

    ask(agent, allied(1000, units=light_tanks(4), enemies=[*EXPOSED_PAIR, NEARER_TRUCK]))

    assert manager.target == 750 and not manager.mission.exposed
    assert starts(match_record("raid")) == [(750, "harv", None)]


@pytest.mark.parametrize("plan", [RAID, HOLD])
def test_exposed_raid_with_a_raid_already_out_nothing_launches(match_record, plan):
    published = [opportunity(1000, ids=(710,), nearest=(66, 60))]
    agent, manager = exposed_raid_agent(plan, lambda tick: list(published))
    ask(agent, allied(1000, units=light_tanks(4), enemies=[*EXPOSED_PAIR, OTHER_FIELD_TRUCK]))
    published.insert(0, opportunity(1005))

    ask(agent, on_the_way(1005, enemies=[*EXPOSED_PAIR, OTHER_FIELD_TRUCK]))

    assert manager.target == 710
    assert starts(match_record("raid")) == [(710, "harv", "exposed_trucks")]


def test_exposed_raid_the_freshest_opportunity_goes_first_and_one_with_no_harvester_remembered_is_passed_over():
    agent, manager = exposed_raid_agent(
        HOLD,
        lambda tick: [
            opportunity(tick, seen_seconds_ago=1, ids=(720, 721), nearest=(40, 30)),
            opportunity(tick, seen_seconds_ago=3, ids=(710,), nearest=(66, 60)),
            opportunity(tick, seen_seconds_ago=2),
        ],
    )

    ask(agent, allied(1000, units=light_tanks(4), enemies=[*EXPOSED_PAIR, OTHER_FIELD_TRUCK]))

    assert manager.target == 700


@pytest.mark.parametrize(
    "enemies, nearest, target",
    [(EXPOSED_PAIR, (50, 30), 700), (EXPOSED_PAIR, (53, 31), 701), ([EXPOSED_PAIR[1]], (50, 30), 701)],
    ids=["nearest the cell", "the other nearest it", "the nearest not remembered"],
)
def test_exposed_raid_the_target_is_the_remembered_harvester_of_the_opportunity_nearest_its_cell(
    enemies, nearest, target
):
    agent, manager = exposed_raid_agent(HOLD, lambda tick: [opportunity(tick, nearest=nearest)])

    ask(agent, allied(1000, units=light_tanks(4), enemies=enemies))

    assert manager.target == target


# A pillbox 7.1 cells from the harvester at (50, 30) and 8.5 from the one at (52, 31), and one the other way round.
PILLBOX_OVER_700, PILLBOX_OVER_701 = foe(600, "pbox", 49, 23), foe(600, "pbox", 58, 33)


@pytest.mark.parametrize(
    "destroyed, buildings, target",
    [({700}, [], 701), (set(), [PILLBOX_OVER_700], 701), ({700}, [PILLBOX_OVER_701], None)],
    ids=["destroyed", "covered", "destroyed and the other covered"],
)
def test_exposed_raid_a_harvester_a_raid_destroyed_or_a_defense_covers_is_passed_over(destroyed, buildings, target):
    agent, manager = exposed_raid_agent(HOLD, lambda tick: [opportunity(tick)])
    manager._destroyed |= destroyed

    ask(agent, allied(1000, units=light_tanks(4), enemies=EXPOSED_PAIR, buildings=buildings))

    assert manager.target == target
    assert sorted(agent.ctx.groups.members(RAID_GROUP)) == ([] if target is None else FOUR)


def test_exposed_raid_under_hold_the_units_on_hand_join_and_set_out_once_gathered(match_record):
    agent, manager = exposed_raid_agent(HOLD, lambda tick: [opportunity(tick)])

    gathering = ask(agent, allied(1000, units=BY_THE_BASE_FOUR, enemies=EXPOSED_PAIR))
    joined = (sorted(agent.ctx.groups.members(RAID_GROUP)), manager.mission)
    ask(agent, allied(1005, units=light_tanks(4), enemies=EXPOSED_PAIR))

    assert joined == (FOUR, None) and gathering == [{"cmd": "move", "units": FOUR, "to": STAGING}]
    assert manager.target == 700 and starts(match_record("raid")) == [(700, "harv", "exposed_trucks")]


def test_exposed_raid_under_hold_too_few_units_on_hand_launch_nothing(match_record):
    agent, manager = exposed_raid_agent(HOLD, lambda tick: [opportunity(tick)])

    orders = ask(agent, allied(1000, units=[*light_tanks(2), *TWO_FORWARD], enemies=EXPOSED_PAIR))

    assert (orders, agent.ctx.groups.members(RAID_GROUP), manager.target) == ([], [], None)
    assert match_record("raid") == []


def test_exposed_raid_under_hold_a_raid_on_exposed_trucks_goes_on_and_breaks_off_by_today_s_rules(match_record):
    agent, manager = exposed_raid_agent(HOLD, lambda tick: [opportunity(1000)] if tick < 1010 else [])
    ask(agent, allied(1000, units=light_tanks(4), enemies=EXPOSED_PAIR))

    ask(agent, on_the_way(1005, enemies=EXPOSED_PAIR))
    going = (manager.target, manager.mission.exposed)
    broken = ask(agent, on_the_way(1010, hp=45, enemies=EXPOSED_PAIR))

    assert going == (700, True) and broken == HOME and manager.mission is None
    assert [(raid["phase"], raid.get("reason")) for raid in match_record("raid")] == [
        ("start", "exposed_trucks"),
        ("end", "health"),
    ]


@pytest.mark.parametrize("plan", [PRESERVE, STRIKE])
def test_exposed_raid_a_raid_on_exposed_trucks_ends_with_plan_changed_under_a_plan_that_keeps_no_raid_group(
    match_record, plan
):
    agent, _ = exposed_raid_agent(RAID, lambda tick: [opportunity(tick)])
    ask(agent, allied(1000, units=light_tanks(4), enemies=EXPOSED_PAIR))
    agent._plan_selector.plan = plan

    ask(agent, on_the_way(1005, enemies=EXPOSED_PAIR))

    assert [(raid["phase"], raid["reason"]) for raid in match_record("raid")] == [
        ("start", "exposed_trucks"),
        ("end", "plan_changed"),
    ]


@pytest.mark.parametrize("plan, target", [(RAID, 750), (HOLD, None)])
def test_exposed_raid_with_the_switch_off_an_opportunity_launches_nothing_and_raid_takes_the_ranking_s_target(
    plan, target
):
    agent, manager = exposed_raid_agent(plan, lambda tick: [opportunity(tick)], EXPOSED_RAID_OFF)

    ask(agent, allied(1000, units=light_tanks(4), enemies=[*EXPOSED_PAIR, NEARER_TRUCK]))

    assert manager.target == target


def test_exposed_raid_the_tracked_tuning_ships_the_switch_on_and_the_switches_off_tuning_sets_it_off():
    assert Tuning.from_file(REPO_ROOT).raids.raid_exposed_trucks and not SWITCHES_OFF.raids.raid_exposed_trucks
