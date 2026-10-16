"""Combat: the fight estimate, engage / hold / retreat, main's advance and reinforcement, targets and retreat."""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from fake_game import FakeRedAlert, at_seconds
import situations
from situations import ScriptedManager, ask, base_state, event, fixture_state, tracked_items, with_command
from switches_off import SWITCHES_OFF, crush_wave_off, safe_staging_off, screen_place_off, stepping_off
from ucla_agent.games.redalert import cells, finish_target, forward_staging, nuke, reinforce_hold, scouting
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.combat import ENGAGE, HOLD, RETREAT, CombatManager, distance_to_path, sweep_points
from ucla_agent.games.redalert.command_gate import REGROUP, ForwardGate, Measure
from ucla_agent.games.redalert.coverage import is_covered
from ucla_agent.games.redalert.crush import (
    far_side,
    infantry_in_sight,
    is_crusher,
    largest_cluster,
    local_ratio,
    order_without,
    qualifying_cluster,
    run_cells,
    run_first,
    without,
)
from ucla_agent.games.redalert.fight import Estimate, centroid, estimate, ratio_against
from ucla_agent.games.redalert.forward_hold import cover_point, forward_point
from ucla_agent.games.redalert.groups import GUARD, MAIN, RAID
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.placement import CreepTarget
from ucla_agent.games.redalert.production import INFANTRY_QUEUE, VEHICLE_QUEUE
from ucla_agent.games.redalert.strike import must_kill_targets
from ucla_agent.games.redalert.targeting import (
    CORE,
    FIGHTERS,
    HARVESTERS,
    WEAK_THREAT,
    assign,
    dominant_class,
    focus_weight,
    priority_class,
    targets,
)
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning
from ucla_agent.games.redalert.world import Unit, World
from whole_match import COMPUTE_P95_MS, WHOLE_MATCH_STEPS, WholeMatch, play_whole_match


def new_agent(*managers, tuning: Tuning | None = None, **more):
    """`situations.new_agent` with main's stepped movement, the screened place, the crush wave and the safe staging off,
    as these tests were worked out."""
    tuning = safe_staging_off(crush_wave_off(screen_place_off(stepping_off(tuning))))
    return situations.new_agent(*managers, tuning=tuning, **more)


# A group of four light tanks around (40.5, 48.5).

TANK_CELLS = ((40, 48), (41, 48), (40, 49), (41, 49))


def own(unit_id: int, unit_type: str, x: int, y: int, hp: int = 100, *, idle: bool = True) -> dict:
    """One own unit as an observation lists it."""
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": hp, "act": None if idle else "move", "idle": idle}


def foe(enemy_id: int, enemy_type: str, x: int, y: int, hp: int = 100) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": hp}


def seen(world: World, tick: int, units=(), enemies=(), buildings=()) -> World:
    """The world after one more state of that tick that shows exactly these own units and enemies."""
    world.update(
        fixture_state(
            "opening_mcv",
            tick,
            units=list(units),
            visible_enemies=list(enemies),
            visible_enemy_buildings=list(buildings),
        )
    )
    return world


def tanks(unit_type: str = "1tnk", first_id: int = 300, hp: int = 100) -> list[dict]:
    return [own(first_id + number, unit_type, x, y, hp) for number, (x, y) in enumerate(TANK_CELLS)]


def estimate_of(world: World, unit_ids=None) -> Estimate | None:
    """The estimate of the group of the given own units, all of them when none are named."""
    units = [unit for unit_id, unit in world.units.items() if unit_ids is None or unit_id in unit_ids]
    return estimate(units, world, Tuning())


@pytest.fixture
def world() -> World:
    return World(tracked_items())


# -- the fight estimate ----------------------------------------------------------------------------


def test_four_light_tanks_against_six_rifles_in_sight_are_2800_against_600(world):
    rifles = [foe(500 + number, "e1", 46, 45 + number) for number in range(6)]

    found = estimate_of(seen(world, 100, tanks(), rifles))

    assert (found.own, found.enemy, found.ratio) == (2800, 600, pytest.approx(4.67, abs=0.01))
    assert found.centroid == (40.5, 48.5) and sorted(found.counted) == [300, 301, 302, 303]


@pytest.mark.parametrize("guns, ratio", [(1, 1.46), (2, 0.73)], ids=["one gun", "two guns"])
def test_a_remembered_gun_out_of_sight_counts_twice_its_cost_raised_by_a_fifth(world, guns, ratio):
    remembered = [foe(600 + number, "gun", 46, 47 + 2 * number) for number in range(guns)]
    seen(world, 100, tanks(), buildings=remembered)

    found = estimate_of(seen(world, 105, tanks()))

    assert found.enemy == pytest.approx(guns * 2.0 * 800 * 1.2) and found.ratio == pytest.approx(ratio, abs=0.01)


@pytest.mark.parametrize(
    "defense, enemy", [("agun", 600), ("sam", 600), ("gun", 600 + 2 * 800)], ids=["AA gun", "SAM site", "gun turret"]
)
def test_aa_march_an_air_only_defense_in_reach_of_the_centroid_does_not_lower_the_estimate_s_ratio(
    world, defense, enemy
):
    rifles = [foe(500 + number, "e1", 46, 45 + number) for number in range(6)]

    found = estimate_of(seen(world, 100, tanks(), rifles, [foe(600, defense, 44, 48)]))

    assert (found.enemy, found.ratio) == (enemy, pytest.approx(2800 / enemy))


@pytest.mark.parametrize("defense", ["agun", "sam"])
def test_aa_march_no_estimate_is_made_with_only_an_air_only_defense_near(world, defense):
    assert estimate_of(seen(world, 100, tanks(), buildings=[foe(600, defense, 44, 48)])) is None


def test_ten_rifles_against_three_heavy_tanks_count_half_their_strength(world):
    rifles = [own(300 + number, "e1", 40 + number % 5, 48 + number // 5) for number in range(10)]
    heavy = [foe(500 + number, "2tnk", 48, 46 + 2 * number) for number in range(3)]

    found = estimate_of(seen(world, 100, rifles, heavy))

    assert (found.own, found.enemy) == (500, 2550)


@pytest.mark.parametrize(
    "group, factor",
    [("e3", 0.5), ("1tnk", 1.0), ("jeep", 1.0), ("e1", 1.0)],
    ids=["rockets", "light tanks", "jeeps", "rifles"],
)
def test_against_mostly_infantry_only_a_group_with_nothing_that_hurts_infantry_is_halved(world, group, factor):
    found = estimate_of(seen(world, 100, tanks(group), [foe(500, "e1", 46, 48), foe(501, "e1", 46, 49)]))

    full = 4 * tracked_items()[group].cost
    assert found.own == pytest.approx(full * factor)


def test_rifles_with_a_rocket_soldier_are_not_halved_against_tanks(world):
    group = [own(300, "e1", 40, 48), own(301, "e1", 41, 48), own(302, "e3", 40, 49)]

    found = estimate_of(seen(world, 100, group, [foe(500, "2tnk", 46, 48)]))

    assert found.own == 100 + 100 + 300


def test_a_force_split_evenly_between_infantry_and_vehicles_halves_no_group(world):
    rifles = [own(300 + number, "e1", 40, 48 + number) for number in range(4)]
    mixed = [foe(500, "1tnk", 46, 48), *[foe(501 + number, "e1", 46, 49 + number) for number in range(7)]]

    found = estimate_of(seen(world, 100, rifles, mixed))

    assert (found.own, found.enemy) == (400, 1400)


def test_own_units_beyond_8_cells_of_the_centroid_are_not_counted(world):
    group = [*tanks(), own(310, "2tnk", 60, 48)]

    found = estimate_of(seen(world, 100, group, [foe(500, "e1", 46, 48)]))

    assert found.centroid == pytest.approx((44.4, 48.4)) and 310 not in found.counted and found.own == 2800


def test_only_enemy_units_within_12_cells_count_and_a_stale_one_counts_not_at_all(world):
    seen(world, 100, tanks(), [foe(500, "e1", 30, 48)])
    later = seen(world, 900, tanks(), [foe(501, "e1", 52, 48), foe(502, "e3", 53, 48)])

    found = estimate_of(later)

    assert later.enemy_memory[500].stale
    assert found.enemy == 100 and [record.id for record in found.enemies] == [501]


def test_a_recent_enemy_unit_out_of_sight_counts_and_raises_the_enemy_by_a_fifth(world):
    seen(world, 100, tanks(), [foe(500, "e1", 46, 48)])

    found = estimate_of(seen(world, 600, tanks()))

    assert found.enemy == pytest.approx(120)


def test_a_paratrooper_counts_as_the_rifle_it_is_a_variant_of(world):
    found = estimate_of(seen(world, 100, tanks(), [foe(500, "e1r1", 46, 48)]))

    assert found.enemy == 100


def weighed(world: World) -> float:
    """The ratio of every own unit of the world against every enemy it remembers, wherever they stand."""
    return ratio_against(list(world.units.values()), list(world.enemy_memory.values()), world, Tuning())


def test_ratio_against_four_light_tanks_weigh_2800_against_three_medium_tanks_30_cells_off_worth_2550(world):
    far = [foe(500 + number, "2tnk", 70, 46 + number) for number in range(3)]

    ratio = weighed(seen(world, 100, tanks(), far))

    assert estimate_of(world) is None and ratio == pytest.approx(2800 / 2550)


def test_ratio_against_rifles_count_half_against_a_tank_and_one_out_of_sight_counts_a_fifth_more(world):
    rifles = [own(300 + number, "e1", 40, 48 + number) for number in range(10)]

    in_sight = weighed(seen(world, 100, rifles, [foe(500, "2tnk", 70, 48)]))
    out_of_sight = weighed(seen(world, 600, rifles))

    assert (in_sight, out_of_sight) == (pytest.approx(500 / 850), pytest.approx(500 / (850 * 1.2)))


def test_ratio_against_no_enemy_worth_anything_is_the_ratio_without_enemy(world):
    seen(world, 100, tanks())

    assert ratio_against(list(world.units.values()), [], world, Tuning()) == Tuning().combat.ratio_without_enemy


def test_no_estimate_is_made_with_no_enemy_that_counts_near(world):
    far = [foe(500, "e1", 60, 48), foe(501, "harv", 45, 48)]
    seen(world, 100, tanks(), far, [foe(600, "powr", 44, 48)])

    assert estimate_of(world) is None


def test_a_defense_near_but_out_of_its_range_starts_an_estimate_worth_nothing(world):
    found = estimate_of(seen(world, 100, tanks(), buildings=[foe(600, "pbox", 50, 48)]))

    assert (found.enemy, found.ratio) == (0, 99)


def test_the_centroid_is_the_mean_cell(world):
    assert centroid(seen(world, 100, tanks()).units.values()) == (40.5, 48.5)


# Four helicopters 6 cells ahead of a group gathered around (42, 48).
HELIS = [foe(500 + number, "heli", 48, 46 + number) for number in range(4)]
AIR_OFF = replace(Tuning(), combat=replace(Tuning().combat, air_targeting=False))


def infantry(types: list[str]) -> list[dict]:
    """Own infantry of these types, ids from 300, five to a column from (40, 46)."""
    return [own(300 + number, unit_type, 40 + number // 5, 46 + number % 5) for number, unit_type in enumerate(types)]


def test_the_estimate_against_4_helis_is_higher_for_10_rifles_and_4_rockets_than_for_22_rifles_of_the_same_cost(world):
    rifles = estimate_of(seen(world, 100, infantry(["e1"] * 22), HELIS))
    mixed = estimate_of(seen(World(tracked_items()), 100, infantry(["e1"] * 10 + ["e3"] * 4), HELIS))

    # Each heli costs 2,000. With no anti-air the 22 rifles face them at twice that; with 1,200 of its 2,200 in
    # rockets the mixed group faces them at 1 + 1,000 / 2,200 times.
    assert (rifles.own, rifles.enemy) == (2200, 16000)
    assert (mixed.own, mixed.enemy) == (2200, pytest.approx(8000 * (1 + 1000 / 2200)))
    assert mixed.ratio > rifles.ratio
    assert (rifles.ratio, mixed.ratio) == (pytest.approx(0.1375), pytest.approx(0.189, abs=0.001))


def test_the_estimate_counts_an_armed_aircraft_at_its_cost_for_a_group_all_of_anti_air_and_with_air_targeting_off():
    flak = estimate_of(seen(World(tracked_items()), 100, tanks("ftrk"), HELIS[:1]))
    rifles = seen(World(tracked_items()), 100, infantry(["e1"] * 4), HELIS[:1])

    assert flak.enemy == 2000
    assert estimate(list(rifles.units.values()), rifles, AIR_OFF).enemy == 2000


@pytest.mark.parametrize("plane", ["u2", "badr", "tran"])
def test_no_estimate_is_made_against_a_lone_unarmed_aircraft_which_counts_no_enemy_value(world, plane):
    seen(world, 100, infantry(["e1"] * 4), [foe(500, plane, 46, 48)])

    assert estimate_of(world) is None


def test_an_unarmed_aircraft_adds_nothing_to_the_estimate_and_with_air_targeting_off_a_transport_counts_its_cost(world):
    seen(world, 100, infantry(["e1"] * 4), [foe(500, "tran", 46, 48), foe(501, "e1", 46, 49)])
    units = list(world.units.values())

    found, before = estimate(units, world, Tuning()), estimate(units, world, AIR_OFF)

    assert (found.enemy, [record.id for record in found.enemies]) == (100, [501])
    assert before.enemy == 100 + 900


# -- engage, hold and retreat ----------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
YARD = ("fact", 22, 48)
# 10 cells from the construction yard toward the enemy's start at (74, 48).
STAGING = [32, 48]
ENEMY_TANKS = [foe(500 + number, "1tnk", 46, 46 + number) for number in range(4)]


# The shipped thresholds with vehicle_repair off, for the tests of a fight whose vehicles would otherwise go into
# repair mode: a badly damaged vehicle is then withdrawn on its own as infantry is.
NO_VEHICLE_REPAIR = replace(Tuning(), combat=replace(Tuning().combat, vehicle_repair=False))


def combat_agent(*more, tuning: Tuning | None = None) -> tuple:
    """An agent with the combat manager and the managers given, under a tuning when one is given, and the combat
    manager itself."""
    manager = CombatManager()
    return new_agent(manager, *more, tuning=tuning), manager


def at(tick: int, units=(), enemies=(), buildings=(), **observation) -> dict:
    """A state of an Allied base at that tick with these own units and these enemies in sight."""
    shown = {"units": list(units), "visible_enemies": list(enemies), "visible_enemy_buildings": list(buildings)}
    return base_state("england", YARD, tick=tick, **shown, **observation)


def back_at_staging() -> list[dict]:
    """The four light tanks of `tanks`, back at the staging point."""
    return [own(300 + number, "1tnk", 32 + number % 2, 48 + number // 2) for number in range(4)]


def retreats(orders: list[dict]) -> list[dict]:
    return [order for order in orders if order == {**order, "cmd": "move", "to": STAGING}]


def test_main_of_four_light_tanks_engages_six_rifles_in_sight():
    agent, manager = combat_agent()

    orders = ask(agent, at(1000, tanks(), [foe(500 + number, "e1", 46, 45 + number) for number in range(6)]))

    assert manager.mode == ENGAGE and manager.estimate.ratio == pytest.approx(4.67, abs=0.01)
    assert retreats(orders) == []


@pytest.mark.parametrize("guns, mode", [(1, ENGAGE), (2, RETREAT)], ids=["one gun", "two guns"])
def test_main_near_remembered_guns_out_of_sight_engages_one_and_retreats_from_two(guns, mode):
    agent, manager = combat_agent()
    remembered = [foe(600 + number, "gun", 46, 47 + 2 * number) for number in range(guns)]
    ask(agent, at(995, tanks(), buildings=remembered))

    orders = ask(agent, at(1000, tanks()))

    assert manager.mode == mode
    assert retreats(orders) == (
        [] if mode == ENGAGE else [{"cmd": "move", "units": [300, 301, 302, 303], "to": STAGING}]
    )


def test_ten_rifles_retreat_from_three_heavy_tanks():
    agent, manager = combat_agent()
    agent.ctx.groups.set_guard_quota("e1", 0)
    rifles = [own(300 + number, "e1", 40 + number % 5, 48 + number // 5) for number in range(10)]

    orders = ask(agent, at(1000, rifles, [foe(500 + number, "2tnk", 48, 46 + 2 * number) for number in range(3)]))

    assert (manager.estimate.own, manager.estimate.enemy, manager.mode) == (500, 2550, RETREAT)
    assert orders == [{"cmd": "move", "units": list(range(300, 310)), "to": STAGING}]


def test_an_engaged_group_keeps_engaging_at_1_0_and_at_0_85_until_125_ticks_have_passed():
    agent, manager = combat_agent()
    ask(agent, at(1000, tanks(), [foe(500, "e1", 46, 48)]))
    modes = [manager.mode]

    for tick, hp in ((1050, 100), (1100, 85), (1124, 85), (1125, 85)):
        ask(agent, at(tick, tanks(hp=hp), ENEMY_TANKS))
        modes.append((round(manager.estimate.ratio, 2), manager.mode))

    assert modes == [ENGAGE, (1.0, ENGAGE), (0.85, ENGAGE), (0.85, ENGAGE), (0.85, RETREAT)]


def test_an_engaged_group_whose_ratio_falls_to_0_4_retreats_at_once():
    agent, manager = combat_agent(tuning=NO_VEHICLE_REPAIR)
    ask(agent, at(1000, tanks(), [foe(500, "e1", 46, 48)]))

    orders = ask(agent, at(1005, tanks(hp=40), ENEMY_TANKS))

    assert (round(manager.estimate.ratio, 2), manager.mode) == (0.4, RETREAT) and retreats(orders)


def test_a_retreating_group_engages_again_only_125_ticks_after_it_turned():
    agent, manager = combat_agent()
    ask(agent, at(1000, tanks(hp=40), ENEMY_TANKS))

    modes = []
    for tick in (1100, 1125):
        # Back at the staging point, out of reach of the tanks it fled, with an enemy rifle near.
        ask(agent, at(tick, back_at_staging(), [foe(510, "e1", 38, 48)]))
        modes.append(manager.mode)

    assert modes == [RETREAT, ENGAGE]


def test_a_retreated_group_with_no_enemy_near_holds_once_its_mode_has_been_kept_long_enough():
    agent, manager = combat_agent()
    ask(agent, at(1000, tanks(hp=40), ENEMY_TANKS))

    modes = []
    for tick in (1100, 1125):
        ask(agent, at(tick, back_at_staging()))
        modes.append((manager.estimate, manager.mode))

    assert modes == [(None, RETREAT), (None, HOLD)]


def test_a_group_starts_out_holding_and_an_even_fight_keeps_it_so():
    agent, manager = combat_agent()

    ask(agent, at(1000, tanks(), ENEMY_TANKS))

    assert (manager.estimate.ratio, manager.mode) == (1.0, HOLD)


def test_units_already_at_the_staging_point_are_not_sent_there_on_a_retreat():
    agent, manager = combat_agent()
    group = [own(300, "1tnk", 33, 49, hp=40), own(301, "1tnk", 39, 48, hp=40)]

    orders = ask(agent, at(1000, group, [foe(500, "2tnk", 40, 44), foe(501, "2tnk", 41, 44)]))

    assert manager.mode == RETREAT and retreats(orders) == [{"cmd": "move", "units": [301], "to": STAGING}]


def test_a_group_under_base_defense_s_override_engages_whatever_the_ratio_and_is_not_sent_back():
    agent, manager = combat_agent()
    agent.ctx.groups.set_defense_override(MAIN)

    orders = ask(agent, at(1000, tanks(hp=40), ENEMY_TANKS))

    assert (round(manager.estimate.ratio, 2), manager.mode, retreats(orders)) == (0.4, ENGAGE, [])


def test_a_group_base_defense_has_called_home_gets_no_order_from_combat():
    agent, manager = combat_agent()
    agent.ctx.groups.recall(MAIN)

    orders = ask(agent, at(1000, tanks(hp=40), ENEMY_TANKS))

    assert (orders, manager.estimate, manager.mode) == ([], None, HOLD)


def test_with_no_main_there_is_no_estimate_and_no_order():
    agent, manager = combat_agent()

    assert (ask(agent, at(1000, enemies=ENEMY_TANKS)), manager.estimate) == ([], None)


# -- gathering, advancing and reinforcing ----------------------------------------------------------

ENEMY_START = [74, 48]


def five_tanks(gathered: int, first_id: int = 300) -> list[dict]:
    """Five light tanks (3,500 strength), the first `gathered` of them within 6 cells of the staging point."""
    at_staging = [(32, 47), (33, 48), (32, 49), (31, 48), (33, 50)]
    return [
        own(first_id + number, "1tnk", *(at_staging[number] if number < gathered else (45 + number, 40)))
        for number in range(5)
    ]


def advances(orders: list[dict]) -> list[dict]:
    return [order for order in orders if order["cmd"] == "attack_move"]


# The built-in tuning with forward staging off: main goes straight on to its objective, as the tests of its choice
# were worked out.
UNSTAGED = Tuning(combat=CombatTuning(forward_staging=False))


def strong_main(minimum: int = 3000, tuning: Tuning | None = None) -> tuple:
    """A combat agent whose main advances at `minimum` strength, under a tuning when one is given, and its combat
    manager."""
    agent, manager = combat_agent(tuning=tuning)
    agent.ctx.min_attack_strength = lambda plan: minimum
    return agent, manager


def test_under_a_plan_that_does_not_attack_main_needs_more_strength_to_advance_than_any_army_has():
    agent, _ = combat_agent()

    assert agent.ctx.min_attack_strength("HOLD") == agent.ctx.min_attack_strength("OPENING") == 99_999
    assert agent.ctx.min_attack_strength("PRESS") == 3000
    assert agent.current_plan == "OPENING" and advances(ask(agent, at(1000, five_tanks(5)))) == []


def test_main_weaker_than_its_attack_strength_is_not_sent_forward():
    agent, manager = strong_main(minimum=4000)

    assert advances(ask(agent, at(1000, five_tanks(5)))) == [] and agent.ctx.army == frozenset()


def test_main_strong_enough_with_only_60_percent_gathered_is_not_sent_forward_and_the_rest_are_gathered():
    agent, _ = strong_main()

    orders = ask(agent, at(1000, five_tanks(3)))

    assert advances(orders) == [] and orders == [{"cmd": "move", "units": [303, 304], "to": STAGING}]


def test_main_strong_enough_with_80_percent_gathered_advances_on_the_enemy_s_start():
    agent, _ = strong_main()

    orders = ask(agent, at(1000, five_tanks(4)))

    assert orders == [{"cmd": "attack_move", "units": [300, 301, 302, 303, 304], "to": ENEMY_START}]
    assert agent.ctx.army == frozenset({300, 301, 302, 303, 304})


def test_main_advances_on_the_remembered_enemy_building_nearest_to_it():
    agent, _ = strong_main()
    ask(agent, at(995, five_tanks(5), buildings=[foe(600, "powr", 70, 30), foe(601, "proc", 50, 60)]))

    assert advances(ask(agent, at(1000, five_tanks(5))))[0]["to"] == [50, 60]


def test_main_advances_on_the_nearest_building_no_remembered_defense_covers():
    agent, _ = strong_main()
    covered = [foe(601, "proc", 50, 60), foe(602, "pbox", 52, 60)]
    ask(agent, at(995, five_tanks(5), buildings=[foe(600, "powr", 70, 30), *covered]))

    assert advances(ask(agent, at(1000, five_tanks(5))))[0]["to"] == [70, 30]


# An enemy yard 28 cells from main at the staging point, and two defenses of a kind 4 to 6 cells from it, farther
# from main: an AA gun covers 12 cells (range 10 and 2), a gun turret 8.5.
AA_YARD = foe(600, "fact", 60, 44)


def guarded_by(defense: str) -> list[dict]:
    """The yard and two defenses of a kind beside it, all in sight."""
    return [AA_YARD, foe(601, defense, 64, 40), foe(602, defense, 64, 46)]


@pytest.mark.parametrize("defense, advance", [("agun", [[60, 44]]), ("gun", [])], ids=["AA guns", "gun turrets"])
def test_aa_march_aa_guns_covering_the_yard_leave_it_main_s_objective_and_gun_turrets_do_not(defense, advance):
    # Five light tanks are worth 3,500; two gun turrets 2 x 2 x 800, more than 3,500 / 1.3.
    agent, _ = strong_main()

    orders = ask(agent, at(1000, five_tanks(5), buildings=guarded_by(defense)))

    assert [order["to"] for order in advances(orders)] == advance


@pytest.mark.parametrize("beside, advance", [("agun", [[60, 44]]), ("gun", [])], ids=["an AA gun", "a gun turret"])
def test_aa_march_an_aa_gun_beside_a_pillbox_main_can_take_is_not_weighed_with_it(beside, advance):
    # The pillbox covers every building; on its own it is worth 2 x 600 against main's 3,500, with a gun turret
    # 2 x 1,400, more than 3,500 / 1.3.
    agent, _ = strong_main(tuning=UNSTAGED)
    buildings = [AA_YARD, foe(601, "pbox", 61, 44), foe(602, beside, 64, 40)]

    orders = ask(agent, at(1000, five_tanks(5), buildings=buildings))

    assert [order["to"] for order in advances(orders)] == advance


def test_an_army_gone_forward_goes_back_to_the_staging_point_when_every_building_turns_out_covered_by_too_much():
    agent, _ = strong_main()
    ask(agent, at(1000, five_tanks(5), buildings=[foe(600, "powr", 70, 30)]))
    guns = [foe(610, "gun", 68, 32), foe(611, "gun", 72, 28)]

    orders = ask(agent, at(1005, forward(), buildings=[foe(600, "powr", 70, 30), *guns]))

    assert agent.ctx.army == frozenset() and orders == [
        {"cmd": "move", "units": [300, 301, 302, 303, 304], "to": STAGING}
    ]


@pytest.mark.parametrize("in_sight, advances_on", [(True, [[68, 32]]), (False, [])], ids=["in sight", "out of sight"])
def test_a_covering_defense_out_of_sight_counts_a_fifth_more(in_sight, advances_on):
    # Three light tanks (2,100) against one gun (2 x 800): 1.3 x 1,600 = 2,080 is within reach, 1.2 x 2,080 = 2,496
    # is not. The gun, which covers itself, is the nearest building.
    agent, _ = strong_main(minimum=2000, tuning=UNSTAGED)
    three = five_tanks(5)[:3]
    building, gun = foe(600, "powr", 70, 30), foe(610, "gun", 68, 32)
    ask(agent, at(995, three, buildings=[building, gun]))

    orders = ask(agent, at(1000, three, buildings=[building, gun] if in_sight else []))

    assert [order["to"] for order in advances(orders)] == advances_on


def test_an_army_gone_forward_that_meets_a_weaker_enemy_fights_it_where_it_is():
    agent, manager = strong_main()
    ask(agent, at(1000, five_tanks(5)))

    orders = ask(agent, at(1005, forward(), [foe(500, "e1", 55, 48), foe(501, "e1", 55, 49)]))

    assert manager.mode == ENGAGE and "attack" in {order["cmd"] for order in orders}


def test_only_units_standing_idle_away_from_the_staging_point_are_gathered():
    agent, _ = combat_agent()
    group = [own(300, "1tnk", 45, 40), own(301, "1tnk", 46, 40, idle=False), own(302, "1tnk", 33, 48)]

    assert ask(agent, at(1000, group)) == [{"cmd": "move", "units": [300], "to": STAGING}]


def sent_classes(agent) -> list[tuple[str, Priority]]:
    """The command and priority class of every order an agent has sent, oldest first."""
    return [(entry.order["cmd"], entry.priority) for entry in agent.ledger.entries]


def test_gathering_and_advancing_are_move_orders_and_attacks_in_contact_contact_orders():
    gathering, _ = combat_agent()
    ask(gathering, at(1000, [own(300, "1tnk", 45, 40)]))
    advancing, _ = strong_main()
    ask(advancing, at(1000, five_tanks(4)))
    fighting, _ = combat_agent()
    ask(fighting, at(1000, tanks(), [foe(500, "e1", 46, 48)]))

    assert sent_classes(gathering) == [("move", Priority.MOVE)]
    assert sent_classes(advancing) == [("attack_move", Priority.MOVE)]
    assert ("attack", Priority.CONTACT) in sent_classes(fighting)
    assert {priority for cmd, priority in sent_classes(fighting) if cmd == "attack_move"} <= {Priority.MOVE}


# -- the focus fire in contact while main gathers or marches ----------------------------------------

# Two light tanks at the staging point and two 18 cells south of it, main's centroid at (34.25, 53): an enemy rifle 4
# cells from the tank at (32, 62), in its range of 4.75, and one 6 cells from the tank at (40, 62), in contact with it
# but out of range, both over 12 cells from the centroid.
SPREAD_MAIN = [own(300, "1tnk", 32, 44), own(301, "1tnk", 33, 44), own(302, "1tnk", 32, 62), own(303, "1tnk", 40, 62)]
SOUTH_RIFLES = [foe(500, "e1", 32, 66), foe(501, "e1", 46, 62)]


def test_contact_focus_main_gathering_fires_at_an_enemy_in_range_and_sends_no_unit_at_one_out_of_range():
    agent, manager = combat_agent()

    orders = ask(agent, at(1000, SPREAD_MAIN, SOUTH_RIFLES))

    assert manager.estimate is None and manager.mode == HOLD
    assert orders == [
        {"cmd": "attack", "units": [302], "target": 500},
        {"cmd": "move", "units": [303], "to": STAGING},
    ]


def test_contact_focus_off_main_gathering_moves_every_idle_unit_away_from_the_staging_point():
    agent = new_agent(CombatManager(), tuning=SWITCHES_OFF)

    assert ask(agent, at(1000, SPREAD_MAIN, SOUTH_RIFLES)) == [{"cmd": "move", "units": [302, 303], "to": STAGING}]


def spread_forward(far: tuple[int, int]) -> list[dict]:
    """The five light tanks of `five_tanks` gone forward, four around (50, 47) and one at `far`."""
    return [*forward()[:4], own(304, "1tnk", *far)]


@pytest.mark.parametrize(
    "plan, far, enemy_cell, fires",
    [
        ("PRESS", (50, 62), (50, 66), True),
        ("FINISH", (50, 62), (50, 66), False),
        ("FINISH", (66, 48), (70, 48), True),
    ],
    ids=["press", "finish, off the way", "finish, on the way"],
)
def test_contact_focus_an_army_on_its_way_fires_with_its_units_in_contact_though_no_enemy_is_near_its_centroid(
    plan, far, enemy_cell, fires
):
    # The tank 304 is 4 cells from the enemy rifle, which is 16 cells or more from main's centroid. Under FINISH the
    # army goes on the enemy's start (74, 48): the rifle south of main is 15 cells off its way, the one east on it.
    manager = CombatManager()
    agent = new_agent(manager, plan_selector=Always(plan))
    agent.ctx.min_attack_strength = lambda plan: 3000
    ask(agent, at(1000, five_tanks(4)))

    orders = ask(agent, at(1005, spread_forward(far), [foe(500, "e1", *enemy_cell)]))

    attacks = [order for order in orders if order["cmd"] == "attack"]
    assert manager.estimate is None
    assert sorted(advances(orders)[0]["units"]) == ([300, 301, 302, 303] if fires else [300, 301, 302, 303, 304])
    assert attacks == ([{"cmd": "attack", "units": [304], "target": 500}] if fires else [])


def test_contact_focus_a_unit_whose_attack_is_held_as_still_in_force_is_not_taken_off_it_by_the_march():
    manager = CombatManager()
    agent = new_agent(manager, plan_selector=Always("PRESS"))
    agent.ctx.min_attack_strength = lambda plan: 3000
    ask(agent, at(1000, five_tanks(4)))
    rifle = [foe(500, "e1", 50, 66)]
    ask(agent, at(1005, spread_forward((50, 62)), rifle))
    attacking = [*forward()[:4], own(304, "1tnk", 50, 62, idle=False)]

    orders = ask(agent, at(1010, attacking, rifle))

    # The attack sent 5 ticks before is still in force and not sent again; the march leaves its tank out all the same.
    assert all(304 not in order["units"] for order in orders)


def forward(first_id: int = 300) -> list[dict]:
    """The five light tanks of `five_tanks`, gone forward to around (50, 48)."""
    return [own(first_id + number, "1tnk", 50 + number % 3, 47 + number // 3) for number in range(5)]


def test_a_unit_made_after_main_left_waits_at_the_staging_point_alone():
    agent, _ = strong_main()
    ask(agent, at(1000, five_tanks(5)))

    orders = ask(agent, at(1005, [*forward(), own(400, "2tnk", 32, 48)]))

    assert all(400 not in order["units"] for order in orders) and 400 not in agent.ctx.army


def test_units_made_after_main_left_are_sent_together_once_they_total_1000():
    agent, _ = strong_main()
    agent.ctx.groups.set_guard_quota("e1", 0)
    ask(agent, at(1000, five_tanks(5)))
    newcomers = [own(400, "ftrk", 32, 48), own(401, "e3", 33, 48), own(402, "e1", 32, 49)]

    sent = []
    for tick, count in ((1005, 1), (1010, 2), (1015, 3)):
        orders = ask(agent, at(tick, [*forward(), *newcomers[:count]]))
        sent.append([order for order in orders if set(order["units"]) & {400, 401, 402}])

    assert sent[:2] == [[], []]
    # The rifle is given a cell of its own beside the point the flak truck and the rocket soldier go to.
    assert sent[2][0] == {"cmd": "attack_move", "units": [400, 401], "to": [51, 47]}
    assert [(order["cmd"], order["units"]) for order in sent[2][1:]] == [("attack_move", [402])]
    assert sent[2][1]["to"] != [51, 47]
    assert {400, 401, 402} <= agent.ctx.army


def test_an_army_that_retreats_is_main_at_the_staging_point_again():
    agent, manager = strong_main()
    ask(agent, at(1000, five_tanks(5)))

    orders = ask(
        agent, at(1005, [*forward(), own(400, "2tnk", 32, 48)], [foe(500 + n, "3tnk", 56, 46 + n) for n in range(6)])
    )

    assert manager.mode == RETREAT and agent.ctx.army == frozenset()
    assert orders == [{"cmd": "move", "units": [300, 301, 302, 303, 304], "to": STAGING}]


# -- targets while engaged -------------------------------------------------------------------------


def mixed_group() -> list[dict]:
    """One heavy tank (850) and seven rifles (700): a group mostly of vehicles."""
    return [own(300, "2tnk", 36, 48), *[own(301 + number, "e1", 40, 45 + number) for number in range(7)]]


def harvester_rockets_and_power() -> tuple[list[dict], list[dict]]:
    """A harvester at 20% hp and a rocket soldier at 30% in sight, and a power plant."""
    return [foe(500, "harv", 46, 50, hp=20), foe(501, "e3", 45, 48, hp=30)], [foe(600, "powr", 47, 46)]


def assigned(world: World, tuning: Tuning) -> dict[str, list[int]]:
    """The units the assignment gives each enemy around all of the world's own units, by the enemy's type."""
    units = list(world.units.values())
    ranked = targets(units, centroid(units), world, tuning)
    return {assignment.target.type: list(assignment.attackers) for assignment in assign(units, ranked, world, tuning)}


def test_each_attacker_goes_where_it_does_most_for_its_weight_and_the_weak_rocket_soldier_gets_one_rifle_focus_off(
    world,
):
    enemies, buildings = harvester_rockets_and_power()
    seen(world, 100, mixed_group(), enemies, buildings)

    given = assigned(world, SWITCHES_OFF)

    # The heavy tank is handed out first, the vehicles before the infantry, and takes the rocket soldier, a weak threat
    # to a group mostly of vehicles: its 600 a second over six seconds is more than 1.2 x the 1,350 hp left, so it
    # covers it alone. A rifle's 125 to the harvester's heavy armour is under a quarter of its mean of 600, so the
    # rifles are never paired with it and go to the power plant.
    assert given == {"e3": [300], "powr": [304, 301, 302, 303, 305, 306, 307]}


def test_main_engaged_with_them_attacks_each_target_in_one_order_the_target_worth_more_first_with_contact_focus_off():
    manager = CombatManager()
    agent = new_agent(manager, tuning=replace(Tuning(), combat=replace(Tuning().combat, contact_focus=False)))
    agent.ctx.groups.set_guard_quota("e1", 0)
    enemies, buildings = harvester_rockets_and_power()

    orders = ask(agent, at(1000, mixed_group(), enemies, buildings))

    # Both attacks are in contact, and packed by what their target is still worth: the power plant 300, the rocket
    # soldier at 30% 90. The tank takes the rocket soldier and the rifles, which hardly scratch the harvester's heavy
    # armour, the power plant.
    assert manager.mode == ENGAGE
    assert orders == [
        {"cmd": "attack", "units": [304, 301, 302, 303, 305, 306, 307], "target": 600},
        {"cmd": "attack", "units": [300], "target": 501},
    ]


@pytest.mark.parametrize(
    "group, rocket_class",
    [(mixed_group(), WEAK_THREAT), ([own(300 + number, "e1", 40, 45 + number) for number in range(4)], FIGHTERS)],
    ids=["mostly vehicles", "rifles"],
)
def test_a_rocket_soldier_at_30_percent_is_a_weak_threat_only_to_a_group_mostly_of_vehicles(world, group, rocket_class):
    enemies, buildings = harvester_rockets_and_power()
    seen(world, 100, group, enemies, buildings)
    group_class = dominant_class(list(world.units.values()), world)

    classes = {
        record.type: priority_class(record, group_class, world, Tuning()) for record in world.enemy_memory.values()
    }

    assert classes == {"e3": rocket_class, "harv": HARVESTERS, "powr": CORE}


def test_the_classes_rank_weak_threats_harvesters_fighters_core_buildings_then_the_rest_and_weigh_their_pairings(world):
    enemies = [foe(500, "e1", 44, 48, hp=90), foe(501, "e1", 44, 49, hp=50), foe(502, "1tnk", 45, 47, hp=35)]
    enemies += [foe(503, "harv", 45, 50), foe(504, "mcv", 45, 51), foe(505, "u2", 44, 46)]
    buildings = [foe(600, "fact", 47, 44), foe(601, "proc", 48, 52), foe(602, "pbox", 47, 50), foe(603, "barr", 46, 53)]
    seen(world, 100, tanks(), enemies, buildings)
    units, tuning = list(world.units.values()), Tuning()

    ranked = targets(units, centroid(units), world, tuning)

    weights = [
        focus_weight(priority_class(record, VEHICLE_QUEUE, world, tuning, units), tuning.combat) for record in ranked
    ]
    # The pillbox, whose gun hurts the tanks within its range and a cell, comes first as a threat defense.
    assert [record.id for record in ranked] == [602, 502, 503, 501, 500, 601, 603, 600, 504]
    assert weights == [2.0, 1.0, 1.2, 1.0, 1.0, 0.6, 0.6, 0.6, 0.4]


def test_with_no_enemy_in_sight_the_group_attack_moves_on_the_best_one_s_last_known_cell():
    agent, _ = combat_agent()
    ask(agent, at(995, tanks(), [foe(500, "e1", 46, 48), foe(501, "harv", 47, 50)]))

    orders = ask(agent, at(1000, tanks()))

    assert orders == [{"cmd": "attack_move", "units": [300, 301, 302, 303], "to": [47, 50]}]


def test_a_unit_that_can_hurt_no_enemy_in_sight_attack_moves_on_the_first_target_given_out():
    manager = CombatManager()
    agent = new_agent(manager)
    group = [own(300, "1tnk", 40, 48), own(301, "e3", 44, 48)]

    orders = ask(agent, at(1000, group, [foe(500, "e1", 46, 48)]))

    # The rocket soldier's 241 to a rifle is under a quarter of its mean of 1,338: it is never paired with it.
    assert orders == [
        {"cmd": "attack", "units": [300], "target": 500},
        {"cmd": "attack_move", "units": [301], "to": [46, 48]},
    ]


def test_units_whose_only_target_is_covered_join_it():
    agent, _ = combat_agent()

    orders = ask(agent, at(1000, tanks(), [foe(500, "e1", 46, 48)]))

    assert orders == [{"cmd": "attack", "units": [300, 301, 302, 303], "target": 500}]


def test_a_group_under_base_defense_s_override_only_attacks_the_enemies_around_it_whatever_the_ratio():
    agent, manager = combat_agent()
    agent.ctx.groups.set_defense_override(MAIN)

    orders = ask(agent, at(1000, tanks(hp=40), ENEMY_TANKS))

    assert manager.mode == ENGAGE and {order["cmd"] for order in orders} == {"attack"}
    assert sorted(unit for order in orders for unit in order["units"]) == [300, 301, 302, 303]


@pytest.mark.parametrize(
    "group, dominant",
    [(("1tnk", "e1", "e1"), VEHICLE_QUEUE), (("e1",) * 8, INFANTRY_QUEUE), (("e3", "jeep", "zzz"), VEHICLE_QUEUE)],
    ids=["one tank and two rifles", "eight rifles", "a rocket, a jeep and an unknown type"],
)
def test_a_group_is_mostly_what_most_of_its_strength_is_of(world, group, dominant):
    seen(world, 100, [own(300 + number, unit_type, 40, 45 + number) for number, unit_type in enumerate(group)])

    assert dominant_class(list(world.units.values()), world) == dominant


# -- a badly damaged tank goes back on its own -----------------------------------------------------

# Two light tanks and two rocket soldiers in sight: 2,000, mostly vehicles.
EVEN_ENEMY = [foe(500, "1tnk", 47, 46), foe(501, "1tnk", 47, 47), foe(502, "e3", 47, 49), foe(503, "e3", 47, 50)]
# One light tank, a rocket soldier and two rifles: 1,200.
WEAK_ENEMY = [foe(500, "1tnk", 47, 46), foe(502, "e3", 47, 49), foe(504, "e1", 47, 51), foe(505, "e1", 47, 52)]


def with_heavy_tank(hp: int, x: int = 40, y: int = 47) -> list[dict]:
    """A heavy tank (850 at full health) with the four light tanks of `tanks`."""
    return [own(310, "2tnk", x, y, hp), *tanks()]


def test_a_heavy_tank_at_30_percent_with_ratio_1_5_is_sent_back_alone_and_rejoins_at_60_percent():
    agent, manager = combat_agent(tuning=NO_VEHICLE_REPAIR)

    orders = ask(agent, at(1000, with_heavy_tank(30), EVEN_ENEMY))
    sent_back = (round(manager.estimate.ratio, 2), orders[0], manager.withdrawn)
    later = ask(agent, at(1005, with_heavy_tank(50, 36, 48), EVEN_ENEMY))
    still_away = (manager.withdrawn, [order for order in later if 310 in order["units"]])
    ask(agent, at(1010, with_heavy_tank(60, 33, 48), EVEN_ENEMY))

    assert sent_back == (1.53, {"cmd": "move", "units": [310], "to": STAGING}, frozenset({310}))
    assert still_away == (frozenset({310}), [])
    assert manager.withdrawn == frozenset()


def test_a_heavy_tank_at_30_percent_with_ratio_2_5_stays_in_the_fight():
    agent, manager = combat_agent(tuning=NO_VEHICLE_REPAIR)

    orders = ask(agent, at(1000, with_heavy_tank(30), WEAK_ENEMY))

    assert round(manager.estimate.ratio, 2) == 2.55 and manager.withdrawn == frozenset()
    assert 310 in {unit for order in orders for unit in order["units"]}


def test_a_unit_sent_back_rejoins_after_750_ticks_whatever_its_health():
    agent, manager = combat_agent(tuning=NO_VEHICLE_REPAIR)
    ask(agent, at(1000, with_heavy_tank(30), EVEN_ENEMY))

    ask(agent, at(1749, with_heavy_tank(30, 33, 48)))
    away_at_749 = manager.withdrawn
    ask(agent, at(1750, with_heavy_tank(30, 33, 48)))

    assert away_at_749 == frozenset({310}) and manager.withdrawn == frozenset()


def test_a_unit_whose_way_back_passes_an_enemy_stays():
    agent, manager = combat_agent()

    ask(agent, at(1000, with_heavy_tank(30), [*EVEN_ENEMY, foe(506, "e1", 37, 45)]))

    assert manager.estimate.ratio < 2.0 and manager.withdrawn == frozenset()


def test_a_unit_that_cost_less_than_700_is_never_sent_back_alone():
    agent, manager = combat_agent()

    ask(agent, at(1000, [own(310, "e3", 40, 47, hp=20), *tanks()], EVEN_ENEMY))

    assert manager.estimate.ratio < 2.0 and manager.withdrawn == frozenset()


def test_the_only_unit_of_main_sent_back_leaves_no_other_order():
    agent, manager = combat_agent(tuning=NO_VEHICLE_REPAIR)

    orders = ask(agent, at(1000, [own(310, "2tnk", 40, 47, hp=30)], [foe(502, "e3", 47, 49, hp=50)]))

    assert orders == [{"cmd": "move", "units": [310], "to": STAGING}] and manager.withdrawn == frozenset({310})


# -- the FINISH sweep's geometry -------------------------------------------------------------------


@pytest.mark.parametrize(
    "point, distance",
    [((5, 3), 3.0), ((-4, 0), 4.0), ((13, 4), 5.0), ((0, 2), 2.0)],
    ids=["beside the way", "behind its start", "past its end", "a way of no length"],
)
def test_the_distance_to_a_path_is_to_its_nearest_point_between_its_ends(point, distance):
    end = (0, 0) if point == (0, 2) else (10, 0)

    assert distance_to_path(point, (0, 0), end) == pytest.approx(distance)


def test_the_sweep_points_are_the_enemy_start_the_other_starts_but_the_own_then_the_grid_row_by_row(world):
    world.update(fixture_state("opening_mcv", 100))
    world.spawns = ((22, 48), (74, 48), (48, 10))

    points = sweep_points(world, 2)

    assert points == [(74, 48), (48, 10), (24, 24), (72, 24), (24, 72), (72, 72)]


# -- the forward hold: where main holds ------------------------------------------------------------

FORWARD_HOLD = Tuning(combat=CombatTuning(forward_hold=True))
# The forward hold at 30 percent of the way, so that a point 6 cells beyond an outpost farther out can lie short of
# halfway on the 52 cells from the base to the enemy's start.
FORWARD_HOLD_AT_30 = Tuning(combat=CombatTuning(forward_hold=True, forward_hold_share=0.3))
# 40 percent of the 52 cells from the base at (22, 48) to the enemy's start at (74, 48) is 20.8 cells; halfway is 26.
FORWARD = (43, 48)
HALFWAY = (48, 48)


def hold_world(*buildings, enemy_buildings=()) -> World:
    """The world of an Allied base of the construction yard and these buildings, with these enemy buildings in sight."""
    world = World(tracked_items())
    world.update(base_state("england", YARD, *buildings, visible_enemy_buildings=list(enemy_buildings)))
    return world


def creep_field(x: int, y: int) -> CreepTarget:
    """A creep target whose ore field has its center at (x, y)."""
    return CreepTarget(OrePatch((x, y), frozenset({(x, y)})), 1)


def test_forward_hold_the_point_is_40_percent_of_the_way_from_the_base_to_the_enemy_s_start():
    assert forward_point(hold_world(), None, FORWARD_HOLD) == FORWARD


@pytest.mark.parametrize(
    "yard, point",
    [((45, 48), HALFWAY), ((38, 48), FORWARD)],
    ids=["a yard 45 percent out: 6 beyond, capped at halfway", "a yard 30 percent out: the default 40 percent"],
)
def test_forward_hold_a_second_yard_farther_out_than_40_percent_moves_the_point_6_cells_beyond_it(yard, point):
    assert forward_point(hold_world(("fact", *yard)), None, FORWARD_HOLD) == point


@pytest.mark.parametrize(
    "yard, field, point",
    [((40, 48), None, (46, 48)), (None, (40, 44), (46, 45)), ((40, 48), (44, 48), (46, 48))],
    ids=["a yard", "a creep target field off the line", "the nearer of a yard and a field"],
)
def test_forward_hold_the_point_is_6_cells_beyond_the_nearest_outpost_toward_the_enemy(yard, field, point):
    world = hold_world(*([] if yard is None else [("fact", *yard)]))

    assert forward_point(world, None if field is None else creep_field(*field), FORWARD_HOLD_AT_30) == point


@pytest.mark.parametrize(
    "yard, point",
    [(None, (39, 48)), ((45, 48), (45, 48))],
    ids=["the 40 percent point", "the point beyond a yard"],
)
def test_forward_hold_a_remembered_gun_reaching_the_point_moves_it_back_outside_its_range_and_4(yard, point):
    gun = foe(600, "gun", 50 if yard is None else 56, 48)
    world = hold_world(*([] if yard is None else [("fact", *yard)]), enemy_buildings=[gun])

    found = forward_point(world, None, FORWARD_HOLD)

    # The gun's range, 6.5 cells, and the 4 cells of the forward hold's margin.
    assert found == point and math.dist(found, (gun["x"], gun["y"])) > 6.5 + 4


def test_forward_hold_there_is_no_point_when_a_defense_reaches_every_cell_ahead_of_the_staging_point():
    world = hold_world(enemy_buildings=[foe(600, "gun", 36, 48)])

    assert forward_point(world, None, FORWARD_HOLD) is None


def test_forward_hold_there_is_no_point_before_the_base_is_known():
    world = World(tracked_items())

    assert forward_point(world, None, FORWARD_HOLD) is None


# -- the forward hold: where main gathers ----------------------------------------------------------

STAGING_CELL = tuple(STAGING)


class Always:
    """A plan rule that keeps one plan."""

    def __init__(self, plan: str) -> None:
        self.plan = plan

    def select(self, world, ctx, choice) -> str:
        return self.plan


def hold_agent(*more, plan: str = "HOLD", tuning: Tuning = FORWARD_HOLD) -> tuple:
    """An agent under a plan, HOLD unless told, with the combat manager and the managers given and the forward hold on,
    and its combat manager."""
    manager = CombatManager()
    return new_agent(manager, *more, tuning=tuning, plan_selector=Always(plan)), manager


def worth_2000(x: int = 32, y: int = 48, *, idle: bool = True) -> list[dict]:
    """Two medium tanks and a rocket soldier, 2,000 in a fight together, at a cell and beside it."""
    return [
        own(300, "2tnk", x, y, idle=idle),
        own(301, "2tnk", x + 1, y, idle=idle),
        own(302, "e3", x, y + 1, idle=idle),
    ]


def worth_1200(x: int = 32, y: int = 48) -> list[dict]:
    """A light tank and a jeep, 1,200 in a fight together, at a cell and beside it."""
    return [own(310, "1tnk", x, y), own(311, "jeep", x + 1, y)]


def moved_to(orders: list[dict], cell) -> set[int]:
    """The units the orders move to a cell."""
    return {
        unit_id for order in orders if order == {**order, "cmd": "move", "to": list(cell)} for unit_id in order["units"]
    }


@pytest.mark.parametrize(
    "units, gather, moved",
    [(worth_2000(), FORWARD, {300, 301, 302}), (worth_1200(), STAGING_CELL, set())],
    ids=["worth 2,000", "worth 1,200"],
)
def test_forward_hold_main_under_hold_gathers_at_the_40_percent_point_from_1500_and_else_at_the_staging_point(
    units, gather, moved
):
    agent, _ = hold_agent()

    sent = ask(agent, at(1000, units))

    assert agent.current_plan == "HOLD" and agent.ctx.gather_point == gather
    assert moved_to(sent, FORWARD) == moved and len(sent) == len([order for order in sent if order["cmd"] == "move"])


@pytest.mark.parametrize("plan", ["OPENING", "PRESS", "PRESERVE"])
def test_forward_hold_main_under_any_other_plan_gathers_at_the_staging_point_whatever_its_strength(plan):
    agent, _ = hold_agent(plan=plan)

    sent = ask(agent, at(1000, worth_2000()))

    assert agent.current_plan == plan and agent.ctx.gather_point == STAGING_CELL
    assert [order for order in sent if order.get("to") == list(FORWARD)] == []


def test_forward_hold_each_change_of_the_gather_point_is_one_hold_position_event(match_record):
    agent, _ = hold_agent()

    for tick, units in ((1000, worth_1200()), (1005, worth_2000()), (1010, worth_2000(43, 48)), (1015, worth_1200())):
        ask(agent, at(tick, units))

    assert match_record("hold_position") == [
        {"tick": 1005, "cell": list(FORWARD), "reason": "forward", "strength": 2000},
        {"tick": 1015, "cell": STAGING, "reason": "staging", "strength": 1200},
    ]


def test_forward_hold_with_the_switch_off_main_under_hold_gathers_at_the_staging_point_and_none_is_published(
    match_record,
):
    agent, _ = hold_agent(tuning=Tuning())

    sent = ask(agent, at(1000, worth_2000(43, 48)))

    assert sent == [{"cmd": "move", "units": [300, 301, 302], "to": STAGING}] and agent.ctx.gather_point is None
    assert match_record("hold_position") == []


# -- the forward hold: fighting at the forward point -----------------------------------------------


def test_forward_hold_an_enemy_rifle_scout_8_cells_from_the_point_is_attacked_and_main_returns_after_chasing_10_cells():
    agent, manager = hold_agent()
    ask(agent, at(1000, worth_2000(*FORWARD)))

    attacked = ask(agent, at(1005, worth_2000(*FORWARD, idle=False), [foe(500, "e1", 51, 48)]))
    chasing = agent.ctx.army
    # The scout ran on, and main after it to 11 cells from the point.
    back = ask(agent, at(1010, worth_2000(54, 48, idle=False), [foe(500, "e1", 58, 48)]))

    assert manager.mode == ENGAGE and {order["target"] for order in with_command(attacked, "attack")} == {500}
    assert {unit_id for order in attacked for unit_id in order["units"]} == chasing == {300, 301, 302}
    assert back == [{"cmd": "move", "units": [300, 301, 302], "to": list(FORWARD)}] and agent.ctx.army == frozenset()


def test_forward_hold_a_unit_sent_back_fights_again_only_once_it_is_within_6_cells_of_the_point():
    agent, _ = hold_agent()
    ask(agent, at(1000, worth_2000(*FORWARD)))
    ask(agent, at(1005, worth_2000(54, 48, idle=False), [foe(500, "e1", 58, 48)]))

    on_the_way = ask(agent, at(1010, worth_2000(51, 48, idle=False), [foe(500, "e1", 56, 48)]))
    back_home = ask(agent, at(1015, worth_2000(*FORWARD, idle=False), [foe(500, "e1", 50, 48)]))

    assert with_command(on_the_way, "attack", "attack_move") == []
    assert {order["target"] for order in with_command(back_home, "attack")} == {500}


def test_forward_hold_main_at_the_point_attacks_no_building_and_no_unit_a_remembered_defense_covers():
    agent, manager = hold_agent()
    # A pillbox 12 cells out, which does not reach the point, covers the rifle 9 cells out; the refinery stands bare.
    buildings = [foe(600, "pbox", 55, 48), foe(601, "proc", 50, 45)]

    orders = ask(agent, at(1000, worth_2000(*FORWARD), [foe(500, "e1", 52, 48)], buildings))

    assert agent.ctx.gather_point == FORWARD and manager.mode == ENGAGE
    assert with_command(orders, "attack", "attack_move") == [] and agent.ctx.army == frozenset()


def test_forward_hold_main_units_standing_at_the_point_wait_for_a_scout_and_units_chasing_do_not():
    agent, _ = hold_agent()
    ask(agent, at(1000, worth_2000(*FORWARD, idle=False)))
    standing = [scouting._is_waiting(unit, agent.ctx) for unit in agent.world.combat_units.values()]

    ask(agent, at(1005, worth_2000(*FORWARD, idle=False), [foe(500, "e1", 51, 48)]))
    chasing = [scouting._is_waiting(unit, agent.ctx) for unit in agent.world.combat_units.values()]

    assert standing == [True, True, True] and chasing == [False, False, False]


# -- the forward hold: falling back ----------------------------------------------------------------


def heavy_tanks(x: int, count: int = 4) -> list[dict]:
    """`count` enemy heavy tanks, 1,150 each, in a column at `x` from y = 46 down."""
    return [foe(500 + number, "3tnk", x, 46 + number) for number in range(count)]


def hold_reasons(match_record) -> list[str]:
    return [event["reason"] for event in match_record("hold_position")]


def test_forward_hold_an_enemy_army_stronger_than_main_coming_at_the_point_sends_main_back_as_fell_back(match_record):
    agent, manager = hold_agent()
    # Four heavy tanks, 4,600 against main's 2,000, first seen 21 cells from the point, beyond the contact distance.
    ask(agent, at(1000, worth_2000(*FORWARD), heavy_tanks(64)))

    orders = ask(agent, at(1005, worth_2000(*FORWARD), heavy_tanks(60)))

    assert manager.estimate is None and manager.mode == RETREAT and agent.ctx.gather_point == STAGING_CELL
    assert orders == [{"cmd": "move", "units": [300, 301, 302], "to": STAGING}]
    assert sent_classes(agent)[-1] == ("move", Priority.EMERGENCY)
    assert match_record("hold_position")[-1] == {"tick": 1005, "cell": STAGING, "reason": "fell_back", "strength": 2000}


def test_forward_hold_an_enemy_army_weaker_than_main_coming_at_the_point_leaves_main_there(match_record):
    agent, manager = hold_agent()
    ask(agent, at(1000, worth_2000(*FORWARD), heavy_tanks(64, count=1)))

    orders = ask(agent, at(1005, worth_2000(*FORWARD), heavy_tanks(60, count=1)))

    assert manager.mode == HOLD and agent.ctx.gather_point == FORWARD and orders == []
    assert hold_reasons(match_record) == ["forward"]


def test_forward_hold_main_falls_back_at_0_69_at_once_though_it_engaged_only_50_ticks_before(match_record):
    agent, manager = hold_agent()
    ask(agent, at(1000, worth_2000(*FORWARD), [foe(510, "e1", 51, 48)]))
    engaged = manager.mode

    # Two heavy tanks 8 cells out and the rifle, unseen now: 1.2 x 2,400 against 2,000, a ratio of 0.69.
    orders = ask(agent, at(1050, worth_2000(*FORWARD), heavy_tanks(51, count=2)))

    assert engaged == ENGAGE and round(manager.estimate.ratio, 2) == 0.69 and manager.mode == RETREAT
    assert orders == [{"cmd": "move", "units": [300, 301, 302], "to": STAGING}]
    assert hold_reasons(match_record) == ["forward", "fell_back"]


def test_forward_hold_main_fallen_back_goes_forward_again_once_its_ratio_has_stayed_at_1_3_for_125_ticks(match_record):
    agent, _ = hold_agent()
    ask(agent, at(1000, worth_2000(*FORWARD), heavy_tanks(64)))
    ask(agent, at(1005, worth_2000(*FORWARD), heavy_tanks(60)))

    gathered = []
    for tick in (1010, 1100, 1134, 1135):
        # Back at the staging point, the enemy army out of sight and far from it.
        ask(agent, at(tick, worth_2000()))
        gathered.append(agent.ctx.gather_point)

    assert gathered == [STAGING_CELL, STAGING_CELL, STAGING_CELL, FORWARD]
    assert hold_reasons(match_record) == ["forward", "fell_back", "forward"]


def test_forward_hold_main_at_the_staging_point_stays_there_without_a_retreat_when_a_stronger_army_comes(match_record):
    agent, manager = hold_agent()
    ask(agent, at(1000, worth_1200(), heavy_tanks(64)))

    orders = ask(agent, at(1005, worth_2000(), heavy_tanks(60)))

    assert agent.ctx.gather_point == STAGING_CELL and manager.mode == HOLD and orders == []
    assert match_record("hold_position") == []


def test_forward_hold_a_major_threat_at_the_base_recalls_main_from_the_point_as_without_it():
    agent, manager = hold_agent(BaseDefenseManager())
    ask(agent, at(1000, worth_2000(*FORWARD)))

    # Four heavy tanks 10 cells from the construction yard, 4,600 against main's 2,000.
    orders = ask(agent, at(1005, worth_2000(*FORWARD), heavy_tanks(32)))

    assert agent.ctx.groups.is_recalled(MAIN) and manager.estimate is None
    assert [order for order in orders if order.get("to") == list(FORWARD)] == []
    assert {unit_id for order in orders if order["cmd"] == "attack_move" for unit_id in order["units"]} >= {
        300,
        301,
        302,
    }


def test_forward_hold_the_125_ticks_before_main_goes_forward_again_start_over_when_its_ratio_drops_below_1_3():
    agent, _ = hold_agent()
    ask(agent, at(1000, worth_2000(*FORWARD), heavy_tanks(64)))
    ask(agent, at(1005, worth_2000(*FORWARD), heavy_tanks(60)))

    gathered = []
    # Two heavy tanks come within reach of main at the staging point (2,000 against 2,300), then are seen far off.
    for tick, enemies in ((1010, []), (1050, heavy_tanks(36, count=2)), (1060, heavy_tanks(70, count=2))):
        ask(agent, at(tick, worth_2000(), enemies))
    for tick in (1135, 1184, 1185):
        ask(agent, at(tick, worth_2000()))
        gathered.append(agent.ctx.gather_point)

    assert gathered == [STAGING_CELL, STAGING_CELL, FORWARD]


# -- the forward hold: never sent forward under HOLD -----------------------------------------------


def test_forward_hold_an_army_gone_forward_under_press_comes_back_to_the_forward_point_when_hold_comes_in():
    rule = Always("PRESS")
    agent = new_agent(CombatManager(), tuning=FORWARD_HOLD, plan_selector=rule)
    agent.ctx.min_attack_strength = lambda plan: 3000
    advanced = ask(agent, at(1000, five_tanks(5)))

    rule.plan = "HOLD"
    orders = ask(agent, at(1005, forward()))

    assert advances(advanced) == [{"cmd": "attack_move", "units": [300, 301, 302, 303, 304], "to": ENEMY_START}]
    assert orders == [{"cmd": "move", "units": [300, 301, 302, 303, 304], "to": list(FORWARD)}]
    assert agent.ctx.army == frozenset() and agent.ctx.gather_point == FORWARD


def test_forward_hold_main_gathered_at_the_forward_point_does_not_advance_whatever_strength_the_plan_asks():
    agent, _ = hold_agent()
    agent.ctx.min_attack_strength = lambda plan: 1000

    orders = ask(agent, at(1000, worth_2000(*FORWARD)))

    assert orders == [] and agent.ctx.army == frozenset() and agent.ctx.gather_point == FORWARD


def test_forward_hold_with_the_switch_off_an_army_gone_forward_under_press_keeps_on_under_hold():
    rule = Always("PRESS")
    agent = new_agent(CombatManager(), plan_selector=rule)
    agent.ctx.min_attack_strength = lambda plan: 3000
    ask(agent, at(1000, five_tanks(5)))

    rule.plan = "HOLD"
    orders = ask(agent, at(1005, forward()))

    assert agent.ctx.army == frozenset({300, 301, 302, 303, 304}) and advances(orders)


# -- the forward hold: a whole match ---------------------------------------------------------------

# Main stands forward of the staging point once its centroid lies farther along the line to the enemy's start than the
# staging point's 10 cells and the 6 within which a unit has reached a point.
FORWARD_OF_STAGING_CELLS = 10 + 6
HOLD_TIME_FROM_SECONDS = 240


@dataclass(frozen=True)
class HoldSample:
    """Where main stood after one cycle of a whole match."""

    seconds: float
    plan: str
    # How far along the line from the base to the enemy's start main's centroid lies; None with no main.
    depth: float | None


def depth_toward_the_enemy(units: list[Unit], world: World) -> float:
    """How far along the line from the base to the enemy's start the centroid of some units lies, in cells."""
    (base_x, base_y), (enemy_x, enemy_y) = world.base_center, world.enemy_base
    x, y = centroid(units)
    span_x, span_y = enemy_x - base_x, enemy_y - base_y
    return ((x - base_x) * span_x + (y - base_y) * span_y) / math.hypot(span_x, span_y)


@pytest.fixture(scope="module", params=["england", "russia"])
def forward_hold_match(request) -> tuple[WholeMatch, list[HoldSample]]:
    """A whole fake match for a family's start with the forward hold on, and where main stood after each cycle."""
    samples: list[HoldSample] = []

    def watch(agent) -> None:
        world = agent.world
        main = [world.combat_units[unit_id] for unit_id in agent.ctx.groups.members(MAIN)]
        depth = depth_toward_the_enemy(main, world) if main else None
        samples.append(HoldSample(world.game_seconds, agent.current_plan, depth))

    return play_whole_match(FORWARD_HOLD, request.param, watch=watch), samples


def test_forward_hold_a_whole_fake_match_with_it_on_runs_without_an_error_or_a_refused_batch_within_the_budget(
    forward_hold_match,
):
    match, _ = forward_hold_match

    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.run.invalid_actions == 0 and match.game.invalid_actions == 0
    assert match.compute_p95_ms <= COMPUTE_P95_MS


def test_forward_hold_main_stands_forward_of_the_staging_point_for_most_of_the_hold_time_after_240_s(
    forward_hold_match,
):
    match, samples = forward_hold_match
    holding = [sample for sample in samples if sample.plan == "HOLD" and sample.seconds >= HOLD_TIME_FROM_SECONDS]
    forward = [sample for sample in holding if sample.depth is not None and sample.depth > FORWARD_OF_STAGING_CELLS]

    assert holding and len(forward) > len(holding) / 2
    assert "forward" in {event["reason"] for event in match.data_of("hold_position")}


# -- the sticky attack target: choosing ------------------------------------------------------------

# The strike on an exposed expansion off: the targets 20 cells and more from the enemy's start would draw it.
STICKY = Tuning(combat=CombatTuning(sticky_attack_target=True, strike_exposed_expansion=False))
# The same with forward staging off, for a test whose main goes straight on to its target.
STICKY_UNSTAGED = Tuning(
    combat=CombatTuning(sticky_attack_target=True, forward_staging=False, strike_exposed_expansion=False)
)
# main's centroid, and the tick of the latest state.
MIDDLE = (40.0, 48.0)
NOW_TICK = 6000


def ago(seconds: int) -> int:
    """The tick that many seconds before NOW_TICK."""
    return NOW_TICK - seconds * TICKS_PER_SECOND


def chosen(world: World, *sightings: tuple[int, list[dict]]) -> tuple[int, str] | None:
    """The id and type of the target chosen from MIDDLE after states that each show, at their tick, some buildings."""
    for tick, buildings in sightings:
        seen(world, tick, buildings=buildings)
    record = finish_target.choose(MIDDLE, world, STICKY.combat)
    return None if record is None else (record.id, record.type)


def test_sticky_attack_target_a_fresh_construction_yard_is_chosen_over_a_nearer_fresh_tesla_coil(world):
    assert chosen(world, (NOW_TICK, [foe(600, "fact", 70, 48), foe(601, "tsla", 48, 48)])) == (600, "fact")


@pytest.mark.parametrize("production", ["fact", "weap", "barr", "tent", "afld", "hpad"])
def test_sticky_attack_target_each_production_building_comes_before_a_nearer_one_of_another_kind(world, production):
    assert chosen(world, (NOW_TICK, [foe(600, production, 70, 48), foe(601, "powr", 44, 48)])) == (600, production)


def test_sticky_attack_target_among_fresh_buildings_of_one_rank_the_nearest_to_main_is_chosen(world):
    buildings = [foe(600, "powr", 70, 48), foe(601, "proc", 50, 50), foe(602, "tsla", 60, 40)]

    assert chosen(world, (NOW_TICK, buildings)) == (601, "proc")


def test_sticky_attack_target_a_building_seen_100_s_ago_is_not_chosen_over_one_seen_10_s_ago(world):
    # Neither is production: the power plant was seen 100 s ago and is nearer; the tesla coil was seen 10 s ago.
    old, recent = (ago(100), [foe(600, "powr", 44, 48)]), (ago(10), [foe(601, "tsla", 70, 30)])

    assert chosen(world, old, recent, (NOW_TICK, [])) == (601, "tsla")


def test_finish_production_a_yard_seen_100_s_ago_is_chosen_over_a_nearer_tesla_coil_seen_10_s_ago(world):
    old, recent = (ago(100), [foe(600, "fact", 70, 48)]), (ago(10), [foe(601, "tsla", 44, 48)])

    assert chosen(world, old, recent, (NOW_TICK, [])) == (600, "fact")


def test_finish_production_among_production_a_fresh_sighting_comes_before_a_nearer_older_one(world):
    old, recent = (ago(100), [foe(600, "fact", 44, 48)]), (ago(10), [foe(601, "weap", 70, 48)])

    assert chosen(world, old, recent, (NOW_TICK, [])) == (601, "weap")


@pytest.mark.parametrize(
    "older, newer, expected",
    [("fact", "powr", (600, "fact")), ("powr", "tsla", (601, "tsla"))],
    ids=["production first", "then the one seen last"],
)
def test_sticky_attack_target_with_none_fresh_production_comes_first_then_the_one_seen_last(
    world, older, newer, expected
):
    sightings = [(ago(200), [foe(600, older, 70, 48)]), (ago(100), [foe(601, newer, 44, 48)]), (NOW_TICK, [])]

    assert chosen(world, *sightings) == expected


def test_sticky_attack_target_a_sighting_exactly_60_s_old_is_still_fresh(world):
    # Both fresh, the nearer is chosen; were the power plant's sighting older, the refinery seen since would be.
    sightings = [(ago(60), [foe(600, "powr", 44, 48)]), (NOW_TICK - 1, [foe(601, "proc", 70, 48)])]

    assert chosen(world, *sightings, (NOW_TICK, [])) == (600, "powr")


def test_sticky_attack_target_with_no_building_remembered_the_nearest_mcv_is_chosen_and_with_nothing_none(world):
    seen(world, NOW_TICK, enemies=[foe(800, "mcv", 70, 48), foe(801, "mcv", 50, 48), foe(802, "1tnk", 41, 48)])
    with_mcv = finish_target.choose(MIDDLE, world, STICKY.combat)

    assert with_mcv.id == 801 and finish_target.choose(MIDDLE, World(tracked_items()), STICKY.combat) is None


# -- the sticky attack target: kept under FINISH ---------------------------------------------------

POWER_PLANT, REFINERY = foe(600, "powr", 62, 48), foe(601, "proc", 36, 62)


def finish_agent(tuning: Tuning = STICKY, plan: str = "FINISH") -> tuple:
    """An agent with the combat manager under a plan, FINISH unless another is given, whose main advances at 3,000
    under FINISH only, and its plan rule."""
    rule = Always(plan)
    agent = new_agent(CombatManager(), tuning=tuning, plan_selector=rule)
    agent.ctx.min_attack_strength = lambda plan: 3000 if plan == "FINISH" else 99_999
    return agent, rule


def aim(orders: list[dict]) -> list[int] | None:
    """Where main's attack_move sends it in a cycle; None with none."""
    return next((order["to"] for order in orders if order["cmd"] == "attack_move"), None)


def moved_back(cells_back: int) -> list[dict]:
    """The five tanks of `forward`, that many cells nearer the base."""
    return [{**tank, "x": tank["x"] - cells_back} for tank in forward()]


def gone_forward(tuning: Tuning = STICKY, buildings=(POWER_PLANT, REFINERY)):
    """A FINISH agent whose main has advanced from the staging point and, gone forward, has seen these buildings."""
    agent, _ = finish_agent(tuning)
    ask(agent, at(1000, five_tanks(5)))
    first = aim(ask(agent, at(1005, forward(), buildings=list(buildings))))
    return agent, first


@pytest.mark.parametrize("tuning, target", [(STICKY_UNSTAGED, [70, 48]), (SWITCHES_OFF, [55, 40])], ids=["on", "off"])
def test_sticky_attack_target_main_under_finish_goes_on_a_fresh_yard_before_a_nearer_fresh_tesla_coil(tuning, target):
    agent, _ = finish_agent(tuning)

    orders = ask(agent, at(1000, five_tanks(5), buildings=[foe(600, "fact", 70, 48), foe(601, "tsla", 55, 40)]))

    assert aim(orders) == target


@pytest.mark.parametrize(
    "tuning, after, events", [(STICKY, [62, 48], 1), (SWITCHES_OFF, [36, 62], 0)], ids=["on", "off"]
)
def test_sticky_attack_target_main_pulled_back_10_cells_keeps_its_target(match_record, tuning, after, events):
    # Gone forward, the power plant is the nearer, 11 cells off; 10 cells back, the refinery is, 15 cells off.
    agent, before = gone_forward(tuning)

    pulled_back = aim(ask(agent, at(1010, moved_back(10), buildings=[POWER_PLANT, REFINERY])))

    assert (before, pulled_back) == ([62, 48], after) and len(match_record("attack_target")) == events


def test_sticky_attack_target_a_target_removed_from_the_enemy_memory_is_replaced_with_reason_lost(match_record):
    agent, _ = gone_forward()

    # The power plant, in sight in the last state, is destroyed: it leaves sight with one kill reported.
    orders = ask(agent, at(1010, forward(), buildings=[REFINERY], events=[event(1, "enemy_killed")]))

    assert aim(orders) == [36, 62]
    assert match_record("attack_target") == [
        {"tick": 1005, "old": None, "new": {"id": 600, "type": "powr", "cell": [62, 48]}, "reason": "none"},
        {
            "tick": 1010,
            "old": {"id": 600, "type": "powr", "cell": [62, 48]},
            "new": {"id": 601, "type": "proc", "cell": [36, 62]},
            "reason": "lost",
        },
    ]


def test_sticky_attack_target_with_the_last_building_lost_main_sweeps_from_the_enemy_s_start(match_record):
    agent, _ = gone_forward(buildings=[POWER_PLANT])

    orders = ask(agent, at(1010, forward(), events=[event(1, "enemy_killed")]))

    assert aim(orders) == ENEMY_START
    assert match_record("attack_target")[-1] == {
        "tick": 1010,
        "old": {"id": 600, "type": "powr", "cell": [62, 48]},
        "new": None,
        "reason": "lost",
    }


def test_sticky_attack_target_a_war_factory_coming_within_12_cells_replaces_a_power_plant_target(match_record):
    agent, _ = gone_forward(buildings=[POWER_PLANT])
    far, near = foe(602, "weap", 74, 70), foe(603, "weap", 60, 52)

    # A war factory seen 32 cells off leaves the target as it is; one 10 cells from main's centroid takes its place.
    kept = aim(ask(agent, at(1010, forward(), buildings=[POWER_PLANT, far])))
    taken = aim(ask(agent, at(1015, forward(), buildings=[POWER_PLANT, far, near])))

    assert (kept, taken) == ([62, 48], [60, 52])
    assert [logged["reason"] for logged in match_record("attack_target")] == ["none", "production_in_contact"]


def test_sticky_attack_target_a_production_target_is_kept_when_another_production_building_comes_into_contact():
    agent, first = gone_forward(buildings=[foe(600, "fact", 70, 30)])

    orders = ask(agent, at(1010, forward(), buildings=[foe(600, "fact", 70, 30), foe(603, "weap", 60, 52)]))

    assert first == aim(orders) == [70, 30]


def turned_to_finish(tuning: Tuning, older: dict, newer: dict) -> list[dict]:
    """The orders of main turning to FINISH 100 s after it saw `older`, and 10 s after it saw `newer`."""
    agent, rule = finish_agent(tuning, plan="HOLD")
    ask(agent, at(1000, five_tanks(5), buildings=[older]))
    ask(agent, at(1000 + 90 * TICKS_PER_SECOND, five_tanks(5), buildings=[newer]))

    rule.plan = "FINISH"
    return ask(agent, at(1000 + 100 * TICKS_PER_SECOND, five_tanks(5)))


@pytest.mark.parametrize("tuning, target", [(STICKY, [70, 30]), (SWITCHES_OFF, [60, 60])], ids=["on", "off"])
def test_sticky_attack_target_on_turning_to_finish_a_building_seen_10_s_ago_comes_before_one_seen_100_s_ago(
    tuning, target
):
    # Neither is production: the refinery seen 100 s ago is the nearer.
    orders = turned_to_finish(tuning, foe(600, "proc", 60, 60), foe(601, "powr", 70, 30))

    assert aim(orders) == target


@pytest.mark.parametrize("tuning, target", [(STICKY, [70, 30]), (SWITCHES_OFF, [60, 60])], ids=["on", "off"])
def test_finish_production_main_turning_to_finish_goes_on_a_yard_seen_100_s_ago_before_a_tesla_coil_seen_10_s_ago(
    tuning, target
):
    # The yard is the farther: without the switch main goes on the nearer tesla coil.
    orders = turned_to_finish(tuning, foe(600, "fact", 70, 30), foe(601, "tsla", 60, 60))

    assert aim(orders) == target


# -- the sticky attack target: a whole match -------------------------------------------------------

FINISH_FROM_SECONDS = 60
# The enemy buildings a scenario puts on the map, always in sight: a construction yard on the enemy's start, a war
# factory nearer the agent's base, a power plant and a tesla coil. The war factory is destroyed at 600 s.
ENEMY_BUILDINGS = (("fact", 74, 48), ("weap", 70, 44), ("powr", 60, 30), ("tsla", 66, 52))
ARMY_AT_SECONDS, WAR_FACTORY_LOST_AT_SECONDS = 120, 600


class FinishFrom:
    """A plan rule: OPENING, then FINISH from a game second on."""

    def __init__(self, seconds: int) -> None:
        self.tick = seconds * TICKS_PER_SECOND

    def select(self, world, ctx, choice) -> str:
        return "FINISH" if world.tick >= self.tick else "OPENING"


@pytest.fixture(scope="module")
def sticky_match() -> tuple[WholeMatch, dict[str, int]]:
    """A whole fake match with sticky_attack_target on under FINISH, and the ids of the enemy buildings by type."""
    placed: dict[str, int] = {}

    def buildings(game: FakeRedAlert) -> None:
        placed.update({kind: game.place_enemy(kind, x, y, visible=True) for kind, x, y in ENEMY_BUILDINGS})

    def army(game: FakeRedAlert) -> None:
        for number in range(8):
            game.add_own("1tnk", 32, 44 + number)

    def war_factory_lost(game: FakeRedAlert) -> None:
        game.remove_enemy(placed["weap"])
        game.emit_event("enemy_killed")

    scenario = [
        at_seconds(1, buildings),
        at_seconds(ARMY_AT_SECONDS, army),
        at_seconds(WAR_FACTORY_LOST_AT_SECONDS, war_factory_lost),
    ]
    return play_whole_match(STICKY, scenario=scenario, plan_selector=FinishFrom(FINISH_FROM_SECONDS)), placed


def test_sticky_attack_target_a_whole_fake_match_with_it_on_runs_without_an_error_or_a_refused_batch_within_the_budget(
    sticky_match,
):
    match, _ = sticky_match

    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.run.invalid_actions == 0 and match.game.invalid_actions == 0
    assert match.compute_p95_ms <= COMPUTE_P95_MS


def test_sticky_attack_target_in_the_whole_match_main_keeps_the_war_factory_until_it_is_lost_then_takes_the_yard(
    sticky_match,
):
    match, placed = sticky_match
    changes = [(logged["reason"], (logged["new"] or {}).get("id")) for logged in match.data_of("attack_target")]

    assert changes == [("none", placed["weap"]), ("lost", placed["fact"])]


# -- the crush run: the cluster --------------------------------------------------------------------

# The shipped tuning, crush runs on; and the same with them off.
CRUSH = Tuning()
CRUSH_OFF = Tuning(combat=CombatTuning(crush_runs=False))
# The shipped tuning but for a run of a single tank, its whole planned group, and for the advantage of 0.7 a run once
# needed.
ONE_TANK_RUNS = Tuning(combat=CombatTuning(crush_min_tanks=1, crush_max_tanks=1))
RATIO_0_7_RUNS = Tuning(combat=CombatTuning(crush_min_ratio=0.7))
# Ten rifles within 3 cells of their centroid (49, 48.2), 8 cells from the nearest of the tanks of TANK_CELLS.
RIFLE_CLUSTER = ((48, 47), (49, 47), (50, 47), (48, 48), (49, 48), (50, 48), (48, 49), (49, 49), (50, 49), (49, 50))
CLUSTER_MIDDLE = (49.0, 48.2)
# A light tank and a flak truck: with the ten rifles, 2,300 against the 4,600 of four heavy tanks, a ratio of 2.0.
ESCORT = (foe(520, "1tnk", 50, 45), foe(521, "ftrk", 50, 52))
# And three heavy tanks more: 5,750 against 4,600, a ratio of 0.8.
ARMOR = (*ESCORT, *[foe(522 + number, "3tnk", 52, 44 + number) for number in range(3)])
# Or four heavy tanks and a light tank more: 7,600 against 4,600, a ratio of 0.6.
OUTMATCHED = (*ESCORT, *[foe(522 + number, "3tnk", 52, 44 + number) for number in range(4)], foe(526, "1tnk", 52, 48))


def rifles(count: int = 10, shift: int = 0) -> list[dict]:
    """`count` enemy rifles on the cells of RIFLE_CLUSTER, moved `shift` cells toward the enemy's start."""
    return [foe(500 + number, "e1", x + shift, y) for number, (x, y) in enumerate(RIFLE_CLUSTER[:count])]


def crush_world(*enemies: dict, buildings=()) -> World:
    """The world of four heavy tanks of the agent's on TANK_CELLS with these enemies and enemy buildings in sight."""
    return seen(World(tracked_items()), 100, tanks("3tnk"), enemies, buildings)


def cluster_of(world: World):
    return qualifying_cluster(list(world.units.values()), world, CRUSH)


def test_crush_run_the_crushing_vehicles_are_the_tanks_of_the_item_table_and_the_apc_with_no_list_besides():
    items = tracked_items()

    assert {item.name for item in items if is_crusher(item.name, items)} == {"1tnk", "2tnk", "3tnk", "4tnk", "apc"}
    assert is_crusher("3tnkr1", items) and not is_crusher("ttnk", items)


def test_crush_run_ten_rifles_within_3_cells_8_cells_from_four_heavy_tanks_at_a_ratio_of_2_are_the_cluster():
    world = crush_world(*rifles(), *ESCORT)

    found = cluster_of(world)

    assert sorted(record.id for record in found.infantry) == list(range(500, 510))
    assert found.centroid == pytest.approx(CLUSTER_MIDDLE)
    assert local_ratio(found.centroid, world, CRUSH) == pytest.approx(2.0)


@pytest.mark.parametrize(
    "enemies, buildings",
    [
        ((*rifles(3), *ESCORT), ()),
        ((*rifles(), *ESCORT), (foe(600, "pbox", 55, 48),)),
        ((*rifles(), *OUTMATCHED), ()),
        ((*rifles(shift=4), *ESCORT), ()),
    ],
    ids=["three rifles", "under a pillbox's coverage", "at a ratio of 0.6", "12 cells from the nearest tank"],
)
def test_crush_run_no_cluster_qualifies(enemies, buildings):
    assert cluster_of(crush_world(*enemies, buildings=buildings)) is None


# An AA gun 9 cells east of the rifles' centroid, whose 12-cell reach takes it in: it fires at aircraft alone.
AA_GUN = foe(600, "agun", 58, 48)


def test_crush_run_a_cluster_covered_only_by_an_aa_gun_is_the_cluster():
    world = crush_world(*rifles(), *ESCORT, buildings=(AA_GUN,))

    found = cluster_of(world)

    assert is_covered(CLUSTER_MIDDLE, world.known_enemy_defenses, world.items, CRUSH.coverage)
    assert found is not None and found.centroid == pytest.approx(CLUSTER_MIDDLE)


def test_crush_run_rifles_out_of_sight_make_no_cluster():
    world = seen(crush_world(*rifles(), *ESCORT), 105, tanks("3tnk"))

    assert infantry_in_sight(world) == [] and cluster_of(world) is None


def test_crush_run_the_cluster_is_the_largest_tight_set_and_rifles_2_cells_apart_in_a_line_make_one_of_3():
    # Eight rifles strung out along a row far from the seven bunched ones, each 2 cells from the next.
    apart = [foe(510 + number, "e1", 30 + 2 * number, 30) for number in range(8)]
    together = infantry_in_sight(crush_world(*rifles(7), *apart))
    spread = infantry_in_sight(crush_world(*apart))

    assert sorted(record.id for record in largest_cluster(together, 3).infantry) == list(range(500, 507))
    assert len(largest_cluster(spread, 3).infantry) == 3 and largest_cluster([], 3) is None


# -- the crush run: the tanks ----------------------------------------------------------------------

# The cell 2 past the centroid of the ten rifles on the line from each of the tanks of TANK_CELLS.
FAR_SIDE = (51, 48)


def cells_of_run(world: World, terrain: Terrain = Terrain()) -> dict[int, tuple[int, int]]:
    """The tanks of the world's own that run over its qualifying cluster, each with the cell it runs to."""
    return run_cells(cluster_of(world), list(world.units.values()), world, terrain, CRUSH.combat)


def test_crush_run_of_ten_rifles_the_three_nearest_of_four_heavy_tanks_run_to_2_cells_past_the_centroid():
    assert cells_of_run(crush_world(*rifles(), *ESCORT)) == {301: FAR_SIDE, 303: FAR_SIDE, 300: FAR_SIDE}


@pytest.mark.parametrize("count, sent", [(4, 2), (6, 2), (7, 3), (10, 3)])
def test_crush_run_one_tank_runs_for_every_3_rifles_and_3_at_the_most(count, sent):
    assert len(cells_of_run(crush_world(*rifles(count), *ESCORT))) == sent


def test_crush_run_a_tank_at_40_percent_is_not_sent_and_the_next_nearest_goes_in_its_place():
    four = [own(300 + number, "3tnk", x, y, hp=40 if number == 1 else 100) for number, (x, y) in enumerate(TANK_CELLS)]

    assert set(cells_of_run(seen(World(tracked_items()), 100, four, [*rifles(), *ESCORT]))) == {300, 302, 303}


# Two heavy tanks of the agent's on the rifles' row: 8 and 12 cells from their centroid.
NEAR_AND_FAR = [own(300, "3tnk", 41, 48), own(301, "3tnk", 37, 48)]


def test_crush_run_a_tank_12_cells_from_the_cluster_is_not_picked_though_one_more_is_wanted():
    world = seen(World(tracked_items()), 100, NEAR_AND_FAR, [*rifles(), *ESCORT])

    assert cells_of_run(world) == {300: FAR_SIDE}


def test_crush_run_the_cell_is_kept_inside_the_map_and_a_blocked_one_moved_to_the_nearest_open_cell():
    world = crush_world()
    tank, on_the_centroid = world.units[301], Unit(310, "3tnk", 49, 48, 100, True, None, None)

    # From (41, 48) past (94, 48) is (96, 48), beyond the map's 96 columns.
    assert far_side(tank, (94.0, 48.0), world, Terrain()) == (95, 48)
    assert far_side(tank, CLUSTER_MIDDLE, world, Terrain(blocked={FAR_SIDE})) == (51, 47)
    assert far_side(on_the_centroid, (49.0, 48.0), world, Terrain()) == (49, 48)


def test_crush_run_a_tank_with_no_open_cell_near_its_own_is_passed_over_for_the_next_nearest():
    around = {(FAR_SIDE[0] + dx, FAR_SIDE[1] + dy) for dx in range(-3, 4) for dy in range(-3, 4)}
    # 304 stands 9 cells east of the centroid, within reach, and runs west through the rifles.
    world = seen(World(tracked_items()), 100, [*tanks("3tnk"), own(304, "3tnk", 58, 48)], [*rifles(), *ESCORT])

    assert cells_of_run(world, Terrain(blocked=around)) == {304: (47, 48)}


# -- the crush run: in the fight -------------------------------------------------------------------


def crush_agent(tuning: Tuning = CRUSH) -> tuple:
    """An agent with the combat manager under a tuning, crush runs on unless told, and its combat manager."""
    manager = CombatManager()
    return new_agent(manager, tuning=tuning), manager


def heavy(*cells, idle: bool = False) -> list[dict]:
    """The four heavy tanks 300 to 303 on these cells, else on TANK_CELLS, moving unless told. 302, the one no run
    takes, stands idle all the same, so that its attack is sent again in every state."""
    return [
        own(300 + number, "3tnk", x, y, idle=idle or number == 2) for number, (x, y) in enumerate(cells or TANK_CELLS)
    ]


def fighting(orders: list[dict]) -> set[int]:
    """The units the attacks and attack-moves among some orders are for."""
    return {unit_id for order in with_command(orders, "attack", "attack_move") for unit_id in order["units"]}


def test_crush_run_three_of_four_heavy_tanks_run_through_ten_rifles_at_a_ratio_of_2_and_the_fourth_keeps_its_attack():
    agent, manager = crush_agent()

    orders = ask(agent, at(1000, heavy(idle=True), [*rifles(), *ESCORT]))

    assert with_command(orders, "move") == [{"cmd": "move", "units": [301, 303, 300], "to": list(FAR_SIDE)}]
    assert manager.mode == ENGAGE and fighting(orders) == {302} and with_command(orders, "attack")
    assert ("move", Priority.CONTACT) in sent_classes(agent)


def test_crush_run_an_engaged_main_all_on_the_run_has_no_other_order():
    agent, manager = crush_agent()

    orders = ask(agent, at(1000, heavy(idle=True)[:3], [*rifles(), *ESCORT]))

    assert manager.mode == ENGAGE and orders == [{"cmd": "move", "units": [301, 300, 302], "to": list(FAR_SIDE)}]


@pytest.mark.parametrize(
    "enemies, buildings",
    [
        ((*rifles(3), *ESCORT), ()),
        ((*rifles(), *ESCORT), (foe(600, "pbox", 55, 48),)),
        ((*rifles(), *OUTMATCHED), ()),
    ],
    ids=["three rifles", "under a pillbox's coverage", "at a ratio of 0.6"],
)
def test_crush_run_none_starts_and_every_tank_takes_main_s_orders(enemies, buildings):
    agent, _ = crush_agent()

    orders = ask(agent, at(1000, heavy(idle=True), enemies, buildings))

    # main fights the cluster, or at a ratio of 0.6 retreats from it.
    assert moved_to(orders, FAR_SIDE) == set()
    assert {unit_id for order in orders for unit_id in order["units"]} == {300, 301, 302, 303}


def test_crush_run_a_cluster_covered_only_by_an_aa_gun_starts_a_run_and_writes_no_skip(match_record):
    agent, _ = crush_agent()

    orders = ask(agent, at(1000, heavy(idle=True), [*rifles(), *ESCORT], [AA_GUN]))

    assert moved_to(orders, FAR_SIDE) == {300, 301, 303} and match_record("combat") == []


def test_crush_run_of_a_tank_8_and_one_12_cells_from_the_cluster_only_the_near_one_runs():
    agent, _ = crush_agent(ONE_TANK_RUNS)

    orders = ask(agent, at(1000, NEAR_AND_FAR, [*rifles(), *ESCORT]))

    assert moved_to(orders, FAR_SIDE) == {300}


# Four rifles on the first cells of RIFLE_CLUSTER, centroid (48.75, 47.25), with ARMOR and a flak truck more: 5,750
# against the 4,600 of four heavy tanks, a ratio of 0.8. Two tanks run, to the cell 2 past the centroid.
FOUR_RIFLES_AT_0_8 = [*rifles(4), *ARMOR, foe(527, "ftrk", 51, 52)]
FOUR_RIFLES_FAR_SIDE = (51, 47)


def tanks_taken(agent, taken: str) -> None:
    """Put the four heavy tanks 300 to 303 in the guard, under base defense's override one by one, or in main called
    home."""
    groups = agent.ctx.groups
    if taken == "guard":
        for unit_id in (300, 301, 302, 303):
            groups.assign(unit_id, GUARD)
    elif taken == "taken by base defense":
        groups.set_unit_override([300, 301, 302, 303])
    else:
        groups.recall(MAIN)


@pytest.mark.parametrize("taken", ["guard", "taken by base defense", "main called home"])
def test_crush_run_guard_tanks_or_tanks_base_defense_has_run_over_4_rifles_at_a_ratio_of_0_8(match_record, taken):
    agent, manager = crush_agent(RATIO_0_7_RUNS)
    ask(agent, at(995, heavy(idle=True)))
    tanks_taken(agent, taken)

    orders = ask(agent, at(1000, heavy(idle=True), FOUR_RIFLES_AT_0_8))

    assert moved_to(orders, FOUR_RIFLES_FAR_SIDE) == {301, 303} and match_record("combat") == []
    assert {unit_id for order in orders for unit_id in order["units"]} == {301, 303}


@pytest.mark.parametrize("taken", ["guard", "taken by base defense", "main called home"])
def test_crush_run_a_guard_tank_or_one_base_defense_has_is_let_go_at_the_end_of_the_run(match_record, taken):
    agent, _ = crush_agent(RATIO_0_7_RUNS)
    ask(agent, at(995, heavy(idle=True)))
    tanks_taken(agent, taken)
    ask(agent, at(1000, heavy(idle=True), FOUR_RIFLES_AT_0_8))

    after = ask(agent, at(1200, heavy(), FOUR_RIFLES_AT_0_8))

    assert after == [] and [event["ended"] for event in match_record("combat")] == ["timed_out"]


def test_crush_run_none_starts_while_the_commander_s_run_over_detachment_is_out_and_the_cluster_is_told(
    match_record,
):
    agent, _ = crush_agent()
    agent.ctx.run_over_out = True

    held = ask(agent, at(1000, heavy(idle=True), [*rifles(), *ESCORT]))
    agent.ctx.run_over_out = False
    started = ask(agent, at(1005, heavy(idle=True), [*rifles(), *ESCORT]))

    assert moved_to(held, FAR_SIDE) == set() and moved_to(started, FAR_SIDE) == {300, 301, 303}
    assert match_record("combat") == [crush_skipped(1000, "run_active")]


def test_crush_run_the_context_holds_the_run_s_tanks_while_it_is_in_force_for_the_commander_and_none_after():
    agent, _ = crush_agent()
    before = dict(agent.ctx.crush_run)
    ask(agent, at(1000, heavy(idle=True), [*rifles(), *ESCORT]))
    during = dict(agent.ctx.crush_run)

    ask(agent, at(1200, heavy(), [*rifles(), *ESCORT]))

    assert before == {} and set(during) == {300, 301, 303} and agent.ctx.crush_run == {}


def test_crush_run_with_the_switch_off_the_tanks_fight_the_cluster_as_before():
    agent, _ = crush_agent(CRUSH_OFF)

    orders = ask(agent, at(1000, heavy(idle=True), [*rifles(), *ESCORT]))

    assert with_command(orders, "move") == [] and fighting(orders) == {300, 301, 302, 303}


def crush_run(tick: int, killed: int, ended: str) -> dict:
    """The `combat` event of the run of tanks 301, 303 and 300 over the ten rifles started at tick 1000, none of them
    lost: the trade is the 100 of each rifle killed."""
    run = {"tick": tick, "reason": "crush_run", "started_tick": 1000, "cell": [49, 48], "cluster": 10}
    trade = {"infantry_killed": killed, "infantry_killed_cost": 100 * killed, "tanks_lost": 0, "trade": 100 * killed}
    return {**run, "tanks": [301, 303, 300], **trade, "ended": ended}


def test_crush_run_the_run_ends_after_8_s_and_its_tanks_take_main_s_orders_again(match_record):
    agent, _ = crush_agent()
    ask(agent, at(1000, heavy(idle=True), [*rifles(), *ESCORT]))

    held_up = ask(agent, at(1199, heavy(), [*rifles(), *ESCORT]))
    after = ask(agent, at(1200, heavy(), [*rifles(), *ESCORT]))

    assert moved_to(held_up, FAR_SIDE) == {300, 301, 303} and fighting(held_up) == {302}
    assert moved_to(after, FAR_SIDE) == set() and fighting(after) == {300, 301, 302, 303}
    assert match_record("combat") == [crush_run(1200, 0, "timed_out")]


def test_crush_run_a_tank_through_takes_main_s_orders_again_and_the_run_ends_when_every_one_is():
    agent, _ = crush_agent()
    ask(agent, at(1000, heavy(idle=True), [*rifles(), *ESCORT]))

    one_through = ask(agent, at(1005, heavy((45, 48), FAR_SIDE, (40, 49), (46, 49)), [*rifles(), *ESCORT]))
    # Through, the tanks stand where they stopped.
    all_through = ask(agent, at(1010, heavy((50, 48), FAR_SIDE, (40, 49), (52, 48), idle=True), [*rifles(), *ESCORT]))

    assert fighting(one_through) == {301, 302} and fighting(all_through) == {300, 301, 302, 303}


def test_crush_run_the_run_ends_when_fewer_than_4_of_its_rifles_stay_together(match_record):
    agent, _ = crush_agent()
    ask(agent, at(1000, heavy(idle=True), [*rifles(), *ESCORT]))

    orders = ask(agent, at(1005, heavy(), [*rifles(3), *ESCORT]))

    assert moved_to(orders, FAR_SIDE) == set() and fighting(orders) == {300, 301, 302, 303}
    assert match_record("combat") == [crush_run(1005, 0, "cluster_broken")]


def test_crush_run_each_run_is_one_combat_event_with_the_rifles_killed_on_it(match_record):
    agent, _ = crush_agent()
    ask(agent, at(1000, heavy(idle=True), [*rifles(), *ESCORT]))
    # Four rifles are run over: they leave sight as four kills are reported, and six stay together.
    ask(agent, at(1005, heavy(), [*rifles(6), *ESCORT], events=[event(1, "enemy_killed", 1005, count=4)]))

    ask(agent, at(1010, heavy((50, 48), FAR_SIDE, (40, 49), (52, 48), idle=True), [*rifles(6), *ESCORT]))

    assert match_record("combat") == [crush_run(1010, 4, "arrived")]


def test_crush_run_a_retreat_of_main_still_reaches_the_tanks_on_a_run():
    agent, manager = crush_agent()
    ask(agent, at(1000, heavy(idle=True), [*rifles(), *ESCORT]))
    mammoths = [foe(530 + number, "4tnk", 46, 44 + number) for number in range(6)]

    orders = ask(agent, at(1005, heavy(), [*rifles(), *ESCORT, *mammoths]))

    assert manager.mode == RETREAT and orders == [{"cmd": "move", "units": [300, 301, 302, 303], "to": STAGING}]
    assert agent.ctx.crush_run == {}


def test_crush_run_its_tanks_are_published_with_their_cells_and_no_later_manager_s_emergency_reaches_them():
    raid = {"cmd": "attack_move", "units": [300, 301, 302, 303], "to": [60, 30]}
    later = ScriptedManager("raids", lambda world, ctx: [Intent(Priority.EMERGENCY, raid)])
    agent = new_agent(CombatManager(), later, tuning=CRUSH)

    orders = ask(agent, at(1000, heavy(idle=True), [*rifles(), *ESCORT]))

    assert agent.ctx.crush_run == {301: FAR_SIDE, 303: FAR_SIDE, 300: FAR_SIDE}
    assert moved_to(orders, FAR_SIDE) == {300, 301, 303}
    assert with_command(orders, "attack_move") == [{**raid, "units": [302]}]


def test_crush_run_run_first_keeps_a_run_s_tank_in_its_run_move_alone_whoever_proposed_the_rest():
    run = {301: FAR_SIDE}
    response = Intent(Priority.EMERGENCY, {"cmd": "attack_move", "units": [300, 301], "to": [22, 48]})
    recall = Intent(Priority.EMERGENCY, {"cmd": "move", "units": [301], "to": STAGING})
    run_move = Intent(Priority.CONTACT, {"cmd": "move", "units": [301], "to": list(FAR_SIDE)})
    stance = Intent(Priority.MOVE, {"cmd": "set_stance", "units": [301], "stance": "defend"})
    build = Intent(Priority.PRODUCTION, {"cmd": "build", "item": "powr"})

    kept = run_first([response, recall, run_move, stance, build], run)

    assert [intent.order for intent in kept] == [
        {"cmd": "attack_move", "units": [300], "to": [22, 48]},
        run_move.order,
        stance.order,
        build.order,
    ]
    assert kept[0].priority == Priority.EMERGENCY and run_first([recall], {}) == [recall]


def test_crush_run_main_s_orders_lose_the_run_s_tanks_but_an_emergency_keeps_them():
    running = frozenset({301})
    retreat = Intent(Priority.EMERGENCY, {"cmd": "move", "units": [300, 301], "to": STAGING})
    attack = Intent(Priority.CONTACT, {"cmd": "attack", "units": [301, 302], "target": 500})
    march = Intent(Priority.MOVE, {"cmd": "attack_move", "units": [301], "to": [50, 48]})

    kept = without(running, [retreat, attack, march])

    assert [intent.order for intent in kept] == [retreat.order, {"cmd": "attack", "units": [302], "target": 500}]
    assert kept[1].priority == Priority.CONTACT and order_without(None, running) is None


# -- the crush run: a whole match ------------------------------------------------------------------

# Four medium tanks of main's in a column 18 cells in front of the base, out of base defense's alarm, put there with ten
# enemy rifles bunched on RIFLE_CLUSTER 9 cells beyond them, always in sight; the rifles are killed 60 s later.
CRUSH_TANK_CELLS = ((40, 46), (40, 47), (40, 48), (40, 49))
CLUSTER_AT_SECONDS, CLUSTER_KILLED_AT_SECONDS = 120, 180


@pytest.fixture(scope="module")
def crush_match() -> tuple[WholeMatch, list[int]]:
    """A whole fake match with the shipped tuning but the crush wave, crush runs on, and a cluster of rifles in front of
    main's tanks, and the tanks' ids."""
    tank_ids: list[int] = []
    rifle_ids: list[int] = []

    def tanks_and_rifles(game: FakeRedAlert) -> None:
        tank_ids.extend(game.add_own("2tnk", x, y) for x, y in CRUSH_TANK_CELLS)
        rifle_ids.extend(game.place_enemy("e1", x, y, visible=True) for x, y in RIFLE_CLUSTER)

    def rifles_killed(game: FakeRedAlert) -> None:
        for rifle_id in rifle_ids:
            game.remove_enemy(rifle_id)
        game.emit_event("enemy_killed", count=len(rifle_ids))

    scenario = [at_seconds(CLUSTER_AT_SECONDS, tanks_and_rifles), at_seconds(CLUSTER_KILLED_AT_SECONDS, rifles_killed)]
    return play_whole_match(crush_wave_off(), scenario=scenario), tank_ids


def test_crush_run_a_whole_fake_match_with_the_shipped_tuning_runs_without_an_error_or_a_refused_batch_in_budget(
    crush_match,
):
    match, _ = crush_match

    assert match.agent.ctx.tuning == crush_wave_off() and match.agent.ctx.tuning.combat.crush_runs
    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.run.invalid_actions == 0 and match.game.invalid_actions == 0
    assert match.compute_p95_ms <= COMPUTE_P95_MS


def test_crush_run_in_the_whole_match_main_s_tanks_run_over_the_rifles_3_at_a_time_their_full_planned_count(
    crush_match,
):
    match, tank_ids = crush_match
    runs = [event for event in match.data_of("combat") if event["reason"] == "crush_run"]

    # Ten rifles are planned min(3, ceil(10 / 3)) = 3 tanks, and no run goes with fewer.
    assert runs and all(run["cluster"] == 10 for run in runs)
    assert all(len(run["tanks"]) == 3 and set(run["tanks"]) <= set(tank_ids) for run in runs)


# -- the crush run: a cluster no run starts over ---------------------------------------------------


def crush_skipped(tick: int, gate: str, cell=(49, 48), cluster: int = 10) -> dict:
    """The `combat` event of a cluster no run started over, the ten rifles of RIFLE_CLUSTER unless told."""
    return {"tick": tick, "reason": "crush_skipped", "cell": list(cell), "cluster": cluster, "gate": gate}


# One heavy tank of main's, 4 cells from the rifles' centroid, at 40% of its health.
WEAK_TANK = [own(300, "3tnk", 45, 48, hp=40)]


@pytest.mark.parametrize("with_rifle", [False, True], ids=["main empty", "main a rifle of ours"])
def test_crush_skipped_a_cluster_near_a_raid_only_tank_writes_no_tank_in_main(match_record, with_rifle):
    agent, _ = crush_agent()
    ours = [*heavy(idle=True), *([own(310, "e1", 30, 40)] if with_rifle else [])]
    ask(agent, at(995, ours))
    for unit_id in (300, 301, 302, 303):
        agent.ctx.groups.assign(unit_id, RAID)

    ask(agent, at(1000, ours, [*rifles(), *ESCORT]))

    assert match_record("combat") == [crush_skipped(1000, "no_tank_in_main")]


def test_crush_skipped_a_cluster_near_a_main_tank_at_40_percent_writes_tank_hp(match_record):
    agent, _ = crush_agent()

    orders = ask(agent, at(1000, WEAK_TANK, [*rifles(), *ESCORT]))

    assert moved_to(orders, FAR_SIDE) == set()
    assert match_record("combat") == [crush_skipped(1000, "tank_hp")]


def test_crush_skipped_a_weak_tank_within_reach_writes_tank_hp_though_a_sound_one_stands_12_cells_off(match_record):
    agent, _ = crush_agent()

    ask(agent, at(1000, [*WEAK_TANK, own(301, "3tnk", 37, 48)], [*rifles(), *ESCORT]))

    assert match_record("combat") == [crush_skipped(1000, "tank_hp")]


@pytest.mark.parametrize(
    "enemies, buildings, gate",
    [((*rifles(), *OUTMATCHED), (), "ratio"), ((*rifles(), *ESCORT), (foe(600, "pbox", 55, 48),), "cover")],
    ids=["at a ratio of 0.6", "under a pillbox's coverage"],
)
def test_crush_skipped_a_cluster_in_a_fight_against_main_or_under_a_defense_writes_ratio_or_cover(
    match_record, enemies, buildings, gate
):
    agent, _ = crush_agent()

    ask(agent, at(1000, heavy(idle=True), enemies, buildings))

    assert match_record("combat") == [crush_skipped(1000, gate)]


def test_crush_skipped_a_cluster_no_tank_has_an_open_cell_beyond_writes_other(match_record):
    agent, _ = crush_agent()
    ask(agent, at(995, heavy(idle=True)))
    agent.ctx.terrain = Terrain(
        blocked={(FAR_SIDE[0] + dx, FAR_SIDE[1] + dy) for dx in range(-3, 4) for dy in range(-3, 4)}
    )

    orders = ask(agent, at(1000, heavy(idle=True), [*rifles(), *ESCORT]))

    assert moved_to(orders, FAR_SIDE) == set()
    assert match_record("combat") == [crush_skipped(1000, "other")]


# Seven rifles bunched 6 cells south of main's tanks and more than 3 cells from the rifles of RIFLE_CLUSTER, their
# centroid at (40, 54.7).
SOUTH_CLUSTER = [
    foe(530 + number, "e1", x, y)
    for number, (x, y) in enumerate(((39, 54), (40, 54), (41, 54), (39, 55), (40, 55), (41, 55), (40, 56)))
]


def test_crush_skipped_a_cluster_beside_the_run_in_force_writes_run_active_and_the_run_s_own_cluster_nothing(
    match_record,
):
    agent, _ = crush_agent()
    ask(agent, at(1000, heavy(idle=True), [*rifles(), *ESCORT]))

    ask(agent, at(1005, heavy(), [*rifles(), *ESCORT]))
    ask(agent, at(1010, heavy(), [*rifles(6), *SOUTH_CLUSTER, *ESCORT]))

    assert match_record("combat") == [crush_skipped(1010, "run_active", cell=(40, 55), cluster=7)]


def test_crush_skipped_repeated_cycles_within_10_s_write_one_event_for_a_rounded_centroid(match_record):
    agent, _ = crush_agent()
    # The rifles 1 cell over round to the same 4-cell corner, (48, 48); 4 cells over to (52, 48).
    for tick, shift in ((1000, 0), (1005, 0), (1100, 1), (1110, 4), (1249, 0), (1250, 0)):
        ask(agent, at(tick, WEAK_TANK, [*rifles(shift=shift), *ESCORT]))

    events = match_record("combat")

    assert [(event["tick"], event["cell"]) for event in events] == [
        (1000, [49, 48]),
        (1110, [53, 48]),
        (1250, [49, 48]),
    ]


def test_crush_skipped_a_run_that_starts_writes_no_skip_event(match_record):
    agent, _ = crush_agent()

    started = ask(agent, at(1000, heavy(idle=True), [*rifles(), *ESCORT]))
    ask(agent, at(1005, heavy(), [*rifles(), *ESCORT]))

    assert moved_to(started, FAR_SIDE) == {300, 301, 303} and match_record("combat") == []


@pytest.mark.parametrize(
    "enemies", [(*rifles(3), *ESCORT), (*rifles(shift=4), *ESCORT)], ids=["three rifles", "12 cells from every tank"]
)
def test_crush_skipped_too_few_rifles_or_a_cluster_out_of_every_own_tank_s_reach_writes_nothing(match_record, enemies):
    agent, _ = crush_agent()

    ask(agent, at(1000, heavy(idle=True), enemies))

    assert match_record("combat") == []


def test_crush_skipped_with_crush_runs_off_nothing_is_written(match_record):
    agent, _ = crush_agent(CRUSH_OFF)

    ask(agent, at(1000, WEAK_TANK, [*rifles(), *ESCORT]))

    assert match_record("combat") == []


# -- the nuke: the silo's clock --------------------------------------------------------------------

NUKE = Tuning().combat
SILO = foe(700, "mslo", 70, 30)
SILO_SEEN_TICK = 1000


@pytest.mark.parametrize(
    "tick, launch",
    [
        (11_499, None),
        (11_500, 13_000),
        (13_500, 13_000),
        (13_501, None),
        (24_999, None),
        (25_000, 26_500),
        (27_000, 26_500),
    ],
)
def test_nuke_spread_a_silo_seen_at_1000_is_expected_to_fire_at_13000_and_every_13500_ticks_after(tick, launch):
    assert nuke.launch_due(SILO_SEEN_TICK, tick, NUKE) == launch


@pytest.mark.parametrize(
    "name, silo", [("mslo", True), ("MSLO", True), ("mslor1", True), ("mcv", False), ("msub", False)]
)
def test_nuke_spread_the_silo_and_its_variants_are_known_by_their_name(name, silo):
    assert nuke.is_silo(name) is silo


def watched(world: World, watch: nuke.NukeWatch, tick: int, buildings=(), tuning: Tuning = Tuning(), **more) -> bool:
    """Whether the watch spreads main after a state of that tick that shows these enemy buildings."""
    world.update(
        fixture_state("opening_mcv", tick, units=[], visible_enemies=[], visible_enemy_buildings=buildings, **more)
    )
    watch.follow(world, tuning.combat)
    return watch.is_spreading


# The clock alone, with no spreading held past an expected launch.
NO_FOLLOW_UP = replace(Tuning(), combat=replace(NUKE, nuke_follow_up=False))


def test_nuke_spread_spreading_is_in_force_from_11500_to_13500_with_a_combat_event_at_each_end(world, match_record):
    watch = nuke.NukeWatch()
    watched(world, watch, SILO_SEEN_TICK, [SILO], NO_FOLLOW_UP)

    spreading = [watched(world, watch, tick, tuning=NO_FOLLOW_UP) for tick in (11_499, 11_500, 13_500, 13_501)]

    assert spreading == [False, True, True, False]
    assert match_record("combat") == [
        {"tick": 11_500, "reason": "nuke_spread", "spreading": True, "launch_tick": 13_000, "silos": [700]},
        {"tick": 13_501, "reason": "nuke_spread", "spreading": False, "launch_tick": 13_000, "silos": [700]},
    ]


def test_nuke_spread_a_silo_destroyed_ends_spreading_at_once(world, match_record):
    watch = nuke.NukeWatch()
    watched(world, watch, SILO_SEEN_TICK, [SILO])
    watched(world, watch, 11_500, [SILO])

    # The silo, in sight at 11,500, leaves sight with one kill reported.
    destroyed = watched(world, watch, 11_505, events=[event(1, "enemy_killed")])

    assert not destroyed and SILO["id"] not in world.enemy_memory
    assert match_record("combat")[-1] == {
        "tick": 11_505,
        "reason": "nuke_spread",
        "spreading": False,
        "launch_tick": 13_000,
        "silos": [],
    }


def lost(deaths_cost: int) -> dict:
    """The military figures of a state in which our side has lost so much in all."""
    return {"military": {"kills_cost": 0, "deaths_cost": deaths_cost, "assets_value": 0, "army_value": 0}}


def test_nuke_follow_up_spreading_holds_past_a_launch_that_did_not_come_until_an_impact_restarts_the_clock(
    world, match_record
):
    watch = nuke.NukeWatch()
    watched(world, watch, SILO_SEEN_TICK, [SILO], **lost(0))

    held = [watched(world, watch, tick, **lost(500)) for tick in (11_500, 13_501, 20_000)]
    # A nuke lands at 23,505: 10,100 lost in one state.
    after_impact = watched(world, watch, 23_505, **lost(10_600))
    next_window = [watched(world, watch, tick, **lost(10_600)) for tick in (35_504, 35_505)]

    assert held == [True, True, True] and after_impact is False and next_window == [False, True]
    impacts = [logged for logged in match_record("combat") if logged["reason"] == "nuke_impact"]
    assert impacts == [{"tick": 23_505, "reason": "nuke_impact", "lost": 10_100, "next_launch": 37_005}]


def test_nuke_follow_up_a_loss_under_3000_in_one_state_is_no_impact(world, match_record):
    watch = nuke.NukeWatch()
    watched(world, watch, SILO_SEEN_TICK, [SILO], **lost(0))

    assert watched(world, watch, 13_600, **lost(2_999)) is True
    assert [logged for logged in match_record("combat") if logged["reason"] == "nuke_impact"] == []


def test_nuke_spread_with_the_switch_off_no_clock_is_kept_and_nothing_is_written(world, match_record):
    watch, off = nuke.NukeWatch(), replace(Tuning(), combat=replace(NUKE, nuke_spread=False))
    watched(world, watch, SILO_SEEN_TICK, [SILO], off)

    assert watched(world, watch, 12_000, tuning=off) is False and match_record("combat") == []


def test_nuke_spread_the_combat_manager_keeps_the_clock_of_a_silo_it_has_seen(match_record):
    agent, _ = combat_agent()
    ask(agent, at(SILO_SEEN_TICK, buildings=[SILO]))
    ask(agent, at(11_500))

    spread = [logged for logged in match_record("combat") if logged["reason"] == "nuke_spread"]
    assert [(logged["tick"], logged["spreading"]) for logged in spread] == [(11_500, True)]


# -- the nuke: main spread around an expected launch -----------------------------------------------

# The construction yard at (22, 48) is the whole base `at` shows: the center of its structure cluster.
CLUSTER_CENTER = (22, 48)


def astray(*, idle: bool = True) -> list[dict]:
    """Six light tanks (4,200 strength) 13 to 18 cells from the staging point, away from any group's cell."""
    return [own(300 + number, "1tnk", 45 + number, 40, idle=idle) for number in range(6)]


def spread_agent(plan: str = "HOLD", tuning: Tuning = Tuning()) -> tuple:
    """An agent under a plan, with the combat manager, that saw an enemy missile silo at tick 1,000."""
    agent, manager = hold_agent(plan=plan, tuning=tuning)
    ask(agent, at(SILO_SEEN_TICK, astray(), buildings=[SILO]))
    return agent, manager


def moves(orders: list[dict]) -> dict[tuple[int, int], list[int]]:
    """The cells the orders of a cycle move units to, each with the units moved there."""
    return {tuple(order["to"]): order["units"] for order in orders if order["cmd"] == "move"}


def is_spread(spots, groups: int = 3) -> bool:
    """Whether cells are as many as the groups, at least 8 cells apart and at least 12 from the structure cluster."""
    apart = all(math.dist(first, second) >= 8 for first, second in itertools.combinations(spots, 2))
    return len(spots) == groups and apart and all(math.dist(spot, CLUSTER_CENTER) >= 12 for spot in spots)


@pytest.mark.parametrize("tick", [11_500, 12_000, 13_500])
def test_nuke_spread_idle_main_under_hold_goes_in_3_groups_8_cells_apart_and_12_from_the_base(tick):
    agent, _ = spread_agent()

    sent = moves(ask(agent, at(tick, astray())))

    assert is_spread(list(sent)) and sorted(len(units) for units in sent.values()) == [2, 2, 2]


def test_nuke_spread_main_standing_idle_at_its_places_is_sent_nowhere():
    agent, _ = spread_agent()
    sent = moves(ask(agent, at(12_000, astray())))

    # Each tank a cell off its group's cell, within 4 of it.
    placed = [own(unit_id, "1tnk", x + 1, y) for (x, y), units in sent.items() for unit_id in units]

    assert ask(agent, at(12_100, placed)) == []


@pytest.mark.parametrize("tick", [11_499, 13_501])
def test_nuke_spread_outside_the_window_idle_main_gathers_at_the_staging_point_as_today(tick):
    agent, _ = spread_agent(tuning=NO_FOLLOW_UP)

    assert ask(agent, at(tick, astray())) == [{"cmd": "move", "units": list(range(300, 306)), "to": STAGING}]


@pytest.mark.parametrize("tuning, spread", [(Tuning(), True), (NO_FOLLOW_UP, False)], ids=["follow-up on", "off"])
def test_nuke_follow_up_the_idle_home_guard_spreads_around_its_post_too(tuning, spread):
    agent, _ = spread_agent(tuning=tuning)
    rifles = [own(250 + number, "e1", 28, 47 + number) for number in range(3)]
    ask(agent, at(11_000, [*astray(), *rifles]))
    for rifle in rifles:
        agent.ctx.groups.assign(rifle["id"], GUARD)

    orders = ask(agent, at(12_000, [*astray(), *rifles]))

    guard_moves = [order for order in orders if order["cmd"] == "move" and set(order["units"]) & {250, 251, 252}]
    assert bool(guard_moves) is spread
    assert all(math.dist(order["to"], CLUSTER_CENTER) >= 12 for order in guard_moves)
    assert agent.ctx.nuke_spreading is True


def test_nuke_follow_up_a_silo_seen_once_long_ago_is_a_must_kill_target_of_the_strike(match_record):
    agent, _ = spread_agent(tuning=NO_FOLLOW_UP)
    ask(agent, at(5_000, astray()))
    world, ctx = agent.world, agent.ctx

    assert must_kill_targets(world, ctx) == []
    ctx.tuning = Tuning()
    (silo,) = must_kill_targets(world, ctx)
    assert silo.id == SILO["id"] and world.tick - silo.last_seen_tick > 30 * TICKS_PER_SECOND


def test_nuke_spread_with_the_switch_off_idle_main_gathers_at_the_staging_point():
    agent, _ = spread_agent(tuning=replace(Tuning(), combat=replace(NUKE, nuke_spread=False)))

    assert ask(agent, at(12_000, astray())) == [{"cmd": "move", "units": list(range(300, 306)), "to": STAGING}]


def test_nuke_spread_under_preserve_main_far_from_the_base_goes_to_the_groups_though_it_is_moving():
    agent, _ = spread_agent(plan="PRESERVE")

    sent = moves(ask(agent, at(12_000, astray(idle=False))))

    assert is_spread(list(sent)) and sorted(unit for units in sent.values() for unit in units) == list(range(300, 306))


def test_nuke_spread_main_standing_at_its_places_counts_as_gathered_and_advances():
    agent, _ = spread_agent(plan="PRESS")
    agent.ctx.min_attack_strength = lambda plan: 1000
    sent = moves(ask(agent, at(12_000, astray())))

    placed = [own(unit_id, "1tnk", *cell) for cell, units in sent.items() for unit_id in units]
    orders = ask(agent, at(12_010, placed))

    assert moves(orders) == {} and advances(orders) and agent.ctx.army == frozenset(range(300, 306))


def test_nuke_spread_the_structure_cluster_is_the_centroid_of_the_densest_one_and_none_without_a_building(world):
    state = base_state("england", ("fact", 22, 48), ("powr", 25, 48), ("tent", 22, 51), ("proc", 40, 30))
    world.update(state)

    assert nuke.structure_cluster(world) == (23.0, 49.0)
    assert nuke.structure_cluster(World(tracked_items())) is None


def test_nuke_spread_no_group_stands_on_blocked_ground_and_with_no_open_cell_main_gathers_at_the_point(world):
    seen(world, 12_000, units=astray())
    point, units = tuple(STAGING), list(world.combat_units.values())
    nearest = nuke.spread_cells(point, world, Terrain(), NUKE)
    everywhere = {(x, y) for x in range(-20, 100) for y in range(-20, 100)}

    assert nearest[0] not in nuke.spread_cells(point, world, Terrain(blocked={nearest[0]}), NUKE)
    gathered = nuke.NukeWatch().gather(units, point, world, Terrain(blocked=everywhere), NUKE)
    assert gathered == [Intent(Priority.MOVE, {"cmd": "move", "units": list(range(300, 306)), "to": list(point)})]


def tanks_at(*columns: int) -> list[Unit]:
    """Light tanks on row 48 at these columns, numbered from 300."""
    return [Unit(300 + number, "1tnk", x, 48, 100, True, None, None) for number, x in enumerate(columns)]


def test_nuke_spread_each_cell_in_turn_takes_its_nearest_share_of_the_units_left():
    spots = [(20, 48), (40, 48), (30, 48)]

    uneven = nuke.places(tanks_at(40, 41, 30, 31, 20), spots)
    two_at_each = nuke.places(tanks_at(20, 21, 40, 41, 30, 31), spots)

    assert uneven == {304: (20, 48), 302: (20, 48), 300: (40, 48), 301: (40, 48), 303: (30, 48)}
    assert two_at_each == {300: (20, 48), 301: (20, 48), 302: (40, 48), 303: (40, 48), 304: (30, 48), 305: (30, 48)}


# -- the nuke: the silo as main's FINISH target ----------------------------------------------------

NUKE_OFF = replace(NUKE, nuke_spread=False)
FAR_SILO = foe(700, "mslo", 70, 30)


@pytest.mark.parametrize("combat, chosen_id", [(NUKE, 700), (NUKE_OFF, 600)], ids=["nuke_spread on", "nuke_spread off"])
def test_nuke_spread_a_silo_seen_100_s_ago_is_chosen_before_a_fresh_nearer_yard(world, combat, chosen_id):
    seen(world, ago(100), buildings=[FAR_SILO])
    seen(world, NOW_TICK, buildings=[foe(600, "fact", 44, 48)])

    assert finish_target.choose(MIDDLE, world, combat).id == chosen_id


# The yard 21 cells from the enemy's start is an exposed expansion, which a strike would take first.
NO_EXPOSED = {"strike_exposed_expansion": False}


@pytest.mark.parametrize(
    "combat, target",
    [
        (CombatTuning(sticky_attack_target=True, **NO_EXPOSED), [70, 30]),
        (CombatTuning(sticky_attack_target=False, **NO_EXPOSED), [70, 30]),
        (CombatTuning(sticky_attack_target=True, nuke_spread=False, **NO_EXPOSED), [55, 40]),
        (CombatTuning(sticky_attack_target=False, nuke_spread=False, **NO_EXPOSED), [55, 40]),
    ],
    ids=["sticky", "nearest", "sticky without nuke_spread", "nearest without nuke_spread"],
)
def test_nuke_spread_main_under_finish_goes_on_a_known_silo_before_a_nearer_yard(combat, target):
    agent, _ = finish_agent(Tuning(combat=combat))

    orders = ask(agent, at(1000, five_tanks(5), buildings=[FAR_SILO, foe(600, "fact", 55, 40)]))

    assert aim(orders) == target


def test_nuke_spread_a_kept_power_plant_gives_way_to_a_silo_seen_later_with_reason_missile_silo(match_record):
    agent, _ = gone_forward(buildings=[POWER_PLANT])

    orders = ask(agent, at(1010, forward(), buildings=[POWER_PLANT, FAR_SILO]))

    assert aim(orders) == [70, 30]
    assert match_record("attack_target")[-1] == {
        "tick": 1010,
        "old": {"id": 600, "type": "powr", "cell": [62, 48]},
        "new": {"id": 700, "type": "mslo", "cell": [70, 30]},
        "reason": "missile_silo",
    }


@pytest.mark.parametrize("kept", [FAR_SILO, foe(600, "fact", 74, 48)], ids=["a silo", "a yard"])
def test_nuke_spread_a_kept_silo_or_yard_is_kept_when_a_war_factory_comes_into_contact(kept):
    agent, first = gone_forward(buildings=[kept])

    orders = ask(agent, at(1010, forward(), buildings=[kept, foe(603, "weap", 60, 52)]))

    assert first == aim(orders) == [kept["x"], kept["y"]]


def test_nuke_spread_a_kept_yard_gives_way_to_a_silo_and_a_kept_silo_to_nothing(match_record):
    agent, _ = gone_forward(buildings=[foe(600, "fact", 74, 48)])
    to_silo = aim(ask(agent, at(1010, forward(), buildings=[foe(600, "fact", 74, 48), FAR_SILO])))

    kept = aim(ask(agent, at(1015, forward(), buildings=[foe(600, "fact", 74, 48), FAR_SILO])))

    assert to_silo == kept == [70, 30]
    assert [logged["reason"] for logged in match_record("attack_target")] == ["none", "missile_silo"]


# -- the strike near us: the must-kill targets -----------------------------------------------------

# Our refinery, 8 cells north of the construction yard of `YARD`.
STRIKE_REFINERY = ("proc", 22, 40)
STRIKE_OFF = Tuning(combat=CombatTuning(strike_near_expansion=False))


def strike_world(tick: int = 1000, enemies=(), buildings=(), units=(), *, earlier: World | None = None) -> World:
    """The world of an Allied base with the yard of `YARD` and the refinery of `STRIKE_REFINERY`, showing these own
    units and enemies; another state of `earlier` when given."""
    world = earlier or World(tracked_items())
    shown = {"units": list(units), "visible_enemies": list(enemies), "visible_enemy_buildings": list(buildings)}
    world.update(base_state("england", YARD, STRIKE_REFINERY, tick=tick, **shown))
    return world


def strike_targets(world: World, tuning: Tuning | None = None, fields=()) -> list[int]:
    """The ids of the must-kill targets in a world, for an agent under a tuning and with these harvested fields."""
    ctx = new_agent(tuning=tuning).ctx
    ctx.harvested_fields = frozenset(fields)
    return [record.id for record in must_kill_targets(world, ctx)]


def test_strike_near_an_enemy_mcv_12_cells_from_our_refinery_is_a_must_kill_target_and_with_the_switch_off_not():
    world = strike_world(enemies=[foe(500, "mcv", 34, 40)])

    assert (strike_targets(world), strike_targets(world, STRIKE_OFF)) == ([500], [])


@pytest.mark.parametrize(
    "cell, is_target",
    [((22, 72), True), ((22, 73), False)],
    ids=["15 cells from the harvested field", "16 cells from it"],
)
def test_strike_near_an_enemy_yard_counts_by_the_nearest_cell_of_a_field_we_harvest(cell, is_target):
    field = OrePatch((22, 60), frozenset({(22, 55), (22, 57)}))
    world = strike_world(buildings=[foe(600, "fact", *cell)])

    assert strike_targets(world, fields=[field]) == ([600] if is_target else [])


@pytest.mark.parametrize(
    "defense, cell, is_target",
    [
        ("gun", (32, 40), True),
        ("gun", (33, 40), False),
        ("gun", (42, 40), False),
        ("agun", (26, 40), False),
    ],
    ids=["gun 10 cells away", "gun 11 cells away", "gun 20 cells away", "AA gun"],
)
def test_strike_near_a_ground_defense_is_a_target_when_its_range_plus_4_reaches_our_refinery(defense, cell, is_target):
    # A gun turret's range is 6.5 cells: with the margin of 4 it reaches 10.5 cells.
    world = strike_world(buildings=[foe(600, defense, *cell)])

    assert strike_targets(world) == ([600] if is_target else [])


def test_strike_near_our_own_ground_defenses_near_our_refinery_are_never_must_kill_targets():
    # Our own gun turret and pillbox 8 cells from our refinery, the harassment towers' kind, with no enemy in sight.
    world = World(tracked_items())
    world.update(base_state("england", YARD, STRIKE_REFINERY, ("gun", 30, 40), ("pbox", 22, 32), tick=1000))

    assert strike_targets(world) == []


@pytest.mark.parametrize("by_defenses, later", [(False, []), (True, [600])], ids=["seen lately", "a building stands"])
def test_strike_near_a_target_unseen_for_30_s_is_none_and_the_nearest_to_our_base_comes_first(by_defenses, later):
    tuning = Tuning(combat=CombatTuning(strike_near_by_defenses=by_defenses))
    world = strike_world(enemies=[foe(500, "mcv", 34, 40)], buildings=[foe(600, "gun", 30, 44)])
    strike_world(1000 + 30 * TICKS_PER_SECOND - 1, earlier=world)

    nearest_first = strike_targets(world, tuning)
    strike_world(1000 + 30 * TICKS_PER_SECOND, earlier=world)

    # With strike_near_by_defenses on, the turret, a building, counts however long ago it was seen; the MCV does not.
    assert (nearest_first, strike_targets(world, tuning)) == ([600, 500], later)


# -- the strike near us: the strike force ----------------------------------------------------------

# An enemy MCV 12 cells east of our refinery, and two enemy light tanks beside it, worth 1,400 against a strike on it.
STRIKE_MCV = foe(500, "mcv", 34, 40)
MCV_ESCORT = [foe(510, "1tnk", 36, 40), foe(511, "1tnk", 36, 41)]
# A gun turret 10 cells east of our refinery: its range of 6.5 cells and the margin of 4 reach the refinery.
STRIKE_GUN = foe(600, "gun", 32, 40)
# The light tanks of `back_at_staging` nearest the MCV: 2,100 against the escort's 1,400 is 1.5.
MCV_STRIKERS = [300, 301, 303]


def strike_state(tick: int, units=(), enemies=(), buildings=(), **observation) -> dict:
    """A state of the Allied base of `strike_world` at that tick with these own units and these enemies in sight."""
    shown = {"units": list(units), "visible_enemies": list(enemies), "visible_enemy_buildings": list(buildings)}
    return base_state("england", YARD, STRIKE_REFINERY, tick=tick, **shown, **observation)


def strike_agent(plan: Always | None = None, tuning: Tuning | None = None) -> tuple:
    """An agent under a plan, PRESS unless one is given, with the combat manager alone, and its combat manager."""
    manager = CombatManager()
    return new_agent(manager, tuning=tuning, plan_selector=plan or Always("PRESS")), manager


def strike_events(match_record) -> list[dict]:
    """The `combat` events of reason `strike_near` of the match, without their ticks."""
    logged = [data for data in match_record("combat") if data["reason"] == "strike_near"]
    return [{key: value for key, value in data.items() if key not in ("tick", "reason")} for data in logged]


def test_strike_near_an_enemy_mcv_12_cells_from_our_refinery_starts_a_strike_for_1_5_attack_moving_to_it(match_record):
    agent, _ = strike_agent()

    orders = ask(agent, strike_state(1000, back_at_staging(), [STRIKE_MCV, *MCV_ESCORT]))

    start = {"phase": "start", "target": 500, "target_type": "mcv", "cell": [34, 40], "units": MCV_STRIKERS}
    assert strike_events(match_record) == [{**start, "ratio": 1.5}]
    assert naming(orders, set(MCV_STRIKERS)) == [{"cmd": "attack_move", "units": MCV_STRIKERS, "to": [34, 40]}]


def test_strike_near_a_unit_of_the_strike_near_its_target_in_sight_attacks_it():
    agent, _ = strike_agent()
    ask(agent, strike_state(1000, back_at_staging(), [STRIKE_MCV]))
    near = [own(300, "1tnk", 33, 43, idle=False), *back_at_staging()[1:]]

    orders = ask(agent, strike_state(1005, near, [STRIKE_MCV]))

    assert naming(orders, {300}) == [{"cmd": "attack", "units": [300], "target": 500}]


def test_strike_near_a_gun_reaching_our_refinery_is_struck_with_our_artillery_and_a_plan_change_does_not_recall_it(
    match_record,
):
    plan = Always("PRESS")
    agent, _ = strike_agent(plan)
    artillery = [own(310, "arty", 30, 50), own(311, "arty", 31, 50)]
    first = ask(agent, strike_state(1000, [*back_at_staging(), *artillery], buildings=[STRIKE_GUN]))
    # The strike force gone on to the gun, more than 15 cells from the center of the base, where PRESERVE recalls main.
    past = [own(300, "1tnk", 38, 40, idle=False), own(310, "arty", 40, 44), own(311, "arty", 41, 44)]
    plan.plan = "PRESERVE"

    orders = ask(agent, strike_state(1005, [*past, *back_at_staging()[1:]], buildings=[STRIKE_GUN]))

    (start,) = strike_events(match_record)
    assert (start["target"], start["units"], start["ratio"]) == (600, [300, 310, 311], 1.5)
    assert naming(first, {300, 310, 311}) == [
        {"cmd": "attack", "units": [310], "target": 600},
        {"cmd": "attack", "units": [311], "target": 600},
        {"cmd": "attack_move", "units": [300], "to": [32, 40]},
    ]
    assert naming(orders, {300, 310, 311}) == [{"cmd": "attack", "units": [300], "target": 600}]


def test_strike_near_the_strike_ends_when_its_target_is_destroyed_and_its_units_are_main_s_again(match_record):
    agent, _ = strike_agent()
    ask(agent, strike_state(1000, back_at_staging(), [STRIKE_MCV, *MCV_ESCORT]))

    orders = ask(agent, strike_state(1005, back_at_staging(), MCV_ESCORT, events=[event(1, "enemy_killed")]))

    assert [data["phase"] for data in strike_events(match_record)] == ["start", "end"]
    assert strike_events(match_record)[-1] == {"phase": "end", "target": 500, "outcome": "destroyed"}
    assert not [order for order in orders if order.get("to") == [34, 40]]


def test_strike_near_a_strike_whose_target_is_destroyed_as_the_staging_point_is_lost_ends_without_an_error(
    match_record,
):
    agent, _ = strike_agent()
    ask(agent, strike_state(1000, back_at_staging(), [STRIKE_MCV, *MCV_ESCORT]))
    # No base center in the state: the staging point is not known in this cycle.
    lost = strike_state(1005, back_at_staging(), MCV_ESCORT, events=[event(1, "enemy_killed")], base_center=None)

    orders = ask(agent, lost)

    assert agent.world.base_center is None and 500 not in agent.world.enemy_memory
    assert strike_events(match_record)[1:] == [{"phase": "end", "target": 500, "outcome": "destroyed"}]
    assert not [order for order in orders if order.get("to") == [34, 40] or order.get("target") == 500]


def test_strike_near_the_strike_ends_when_its_target_has_been_unseen_for_30_s(match_record):
    agent, _ = strike_agent()
    ask(agent, strike_state(1000, back_at_staging(), [STRIKE_MCV, *MCV_ESCORT]))
    ask(agent, strike_state(1000 + 30 * TICKS_PER_SECOND - 1, back_at_staging()))

    ask(agent, strike_state(1000 + 30 * TICKS_PER_SECOND, back_at_staging()))

    assert strike_events(match_record)[1:] == [{"phase": "end", "target": 500, "outcome": "unseen"}]


def test_strike_near_a_strike_losing_its_fight_is_abandoned_in_that_cycle_goes_back_and_its_target_rests(match_record):
    agent, _ = strike_agent()
    ask(agent, strike_state(1000, back_at_staging(), [STRIKE_MCV, *MCV_ESCORT]))
    heavy = [foe(520 + number, "3tnk", 33, 50 + number) for number in range(6)]

    orders = ask(agent, strike_state(1005, back_at_staging(), [STRIKE_MCV, *MCV_ESCORT, *heavy]))
    rested = ask(agent, strike_state(1010, back_at_staging(), [STRIKE_MCV, *MCV_ESCORT]))

    assert strike_events(match_record)[1:] == [{"phase": "end", "target": 500, "outcome": "abandoned"}]
    assert {"cmd": "move", "units": MCV_STRIKERS, "to": STAGING} in orders
    assert not [order for order in rested if order.get("to") == [34, 40]]


def test_strike_near_base_defense_calling_main_home_ends_the_strike(match_record):
    agent, _ = strike_agent()
    ask(agent, strike_state(1000, back_at_staging(), [STRIKE_MCV, *MCV_ESCORT]))
    agent.ctx.groups.recall(MAIN)

    ask(agent, strike_state(1005, back_at_staging(), [STRIKE_MCV, *MCV_ESCORT]))

    assert strike_events(match_record)[1:] == [{"phase": "end", "target": 500, "outcome": "recalled"}]


# Four enemy light tanks 6 cells from the MCV and more than 12 from main at the staging point: 2,800 against main's.
STRONG_ESCORT = [foe(530 + number, "1tnk", 36 + number % 2, 34 + number // 2) for number in range(4)]
STAGING_OFF = Tuning(combat=CombatTuning(forward_staging=False))
STAGING_AND_STRIKE_OFF = Tuning(combat=CombatTuning(forward_staging=False, strike_near_expansion=False))


def test_strike_near_with_no_strike_force_main_as_a_whole_goes_on_the_target_under_hold(match_record):
    agent, _ = strike_agent(Always("HOLD"), STAGING_OFF)

    orders = ask(agent, strike_state(1000, back_at_staging(), [STRIKE_MCV, *STRONG_ESCORT]))

    assert strike_events(match_record) == [
        {"phase": "start", "target": 500, "target_type": "mcv", "cell": [34, 40], "units": [], "ratio": 0.0}
    ]
    assert agent.ctx.strike_target.id == 500
    assert {"cmd": "attack_move", "units": [300, 301, 302, 303], "to": [34, 40]} in orders


def test_strike_near_main_as_a_whole_hands_the_strike_to_a_force_once_it_can_field_one():
    agent, _ = strike_agent(Always("HOLD"), STAGING_OFF)
    ask(agent, strike_state(1000, back_at_staging(), [STRIKE_MCV, *STRONG_ESCORT]))
    more = [own(310 + number, "1tnk", 30 + number % 2, 50 + number // 2) for number in range(4)]

    orders = ask(agent, strike_state(1005, [*back_at_staging(), *more], [STRIKE_MCV, *STRONG_ESCORT]))

    # 4,200 is 1.5 times the escort's 2,800: the six light tanks nearest the MCV.
    assert agent.ctx.strike_target is None
    assert {"cmd": "attack_move", "units": [300, 301, 302, 303, 310, 311], "to": [34, 40]} in orders


def test_strike_near_main_as_a_whole_comes_back_to_the_staging_point_under_hold_once_its_target_is_destroyed():
    agent, _ = strike_agent(Always("HOLD"), STAGING_OFF)
    ask(agent, strike_state(1000, back_at_staging(), [STRIKE_MCV, *STRONG_ESCORT]))
    there = [own(300 + number, "1tnk", 34 + number % 2, 40 + number // 2) for number in range(4)]

    # The MCV and its escort are destroyed together.
    orders = ask(agent, strike_state(1005, there, events=[event(1, "enemy_killed", count=5)]))

    assert agent.ctx.strike_target is None
    assert orders == [{"cmd": "move", "units": [300, 301, 302, 303], "to": STAGING}]


def test_strike_near_main_as_a_whole_under_preserve_goes_on_the_target_and_without_the_strike_stays_home():
    striking, _ = strike_agent(Always("PRESERVE"), STAGING_OFF)
    home, _ = strike_agent(Always("PRESERVE"), STAGING_AND_STRIKE_OFF)

    state = strike_state(1000, back_at_staging(), [STRIKE_MCV, *STRONG_ESCORT])
    gone, stayed = ask(striking, state), ask(home, state)

    assert [order["to"] for order in gone] == [[34, 40]]
    assert not [order for order in stayed if order.get("to") == [34, 40]] and home.ctx.strike_target is None


def test_strike_near_with_the_switch_off_an_enemy_mcv_near_our_refinery_starts_no_strike(match_record):
    agent, _ = strike_agent(tuning=STRIKE_OFF)

    orders = ask(agent, strike_state(1000, back_at_staging(), [STRIKE_MCV, *MCV_ESCORT]))

    # The light tank out of reach of every enemy may attack-move on the MCV the others fire on; the strikers are not
    # sent there.
    assert strike_events(match_record) == []
    assert not [order for order in orders if order.get("to") == [34, 40] and set(order["units"]) & set(MCV_STRIKERS)]


# -- forward staging: the staging cell -------------------------------------------------------------

# A pillbox (range 6) on row 48, 28 cells east of main at the staging point: a cell is in its reach, range and the
# margin of 3, nearer than 9 cells.
PILLBOX = foe(600, "pbox", 60, 48)
MAIN_MIDDLE = (32.2, 48.4)


def reach_of(world: World, *buildings: dict, enemies=(), objective=(60, 48), tuning: Tuning = Tuning()):
    """The enemy reach main stages outside of on its way to an objective, after a state showing these enemies."""
    seen(world, 1000, enemies=list(enemies), buildings=list(buildings))
    return forward_staging.enemy_reach(objective, world, tuning)


def test_forward_staging_with_a_pillbox_of_range_6_at_the_objective_the_cell_is_the_last_of_the_way_9_cells_off(world):
    reach = reach_of(world, PILLBOX)

    cell = forward_staging.staging_cell(MAIN_MIDDLE, (60, 48), reach, Terrain())

    assert cell == (51, 48) and math.dist(cell, (60, 48)) >= 9
    assert [disc.holds(cell) for disc in reach] == [False] and reach[0].holds((52, 48))


def test_forward_staging_on_a_slanted_way_the_cell_is_on_main_s_way_9_cells_from_the_pillbox(world):
    reach = reach_of(world, foe(600, "pbox", 60, 30), objective=(60, 30))

    cell = forward_staging.staging_cell(MAIN_MIDDLE, (60, 30), reach, Terrain())
    way = cells.line((32, 48), (60, 30))

    assert cell in way and math.dist(cell, (60, 30)) >= 9
    assert reach[0].holds(way[way.index(cell) + 1])


def test_forward_staging_a_way_that_never_comes_into_reach_and_one_that_starts_in_it_have_no_cell(world):
    reach = reach_of(world, PILLBOX)

    assert forward_staging.staging_cell(MAIN_MIDDLE, (45, 48), reach, Terrain()) is None
    assert forward_staging.staging_cell((54.0, 48.0), (60, 48), reach, Terrain()) is None


def test_forward_staging_a_blocked_cell_is_passed_over_for_the_open_one_before_it(world):
    reach = reach_of(world, PILLBOX)

    assert forward_staging.staging_cell(MAIN_MIDDLE, (60, 48), reach, Terrain(blocked={(51, 48)})) == (50, 48)


@pytest.mark.parametrize("defense", ["agun", "sam"])
def test_forward_staging_an_aa_gun_or_sam_site_alone_at_the_objective_is_no_reach(world, defense):
    assert reach_of(world, foe(600, defense, 60, 48)) == []


def test_forward_staging_a_visible_tank_near_the_objective_reaches_its_range_and_3_and_one_out_of_sight_none(world):
    tank = foe(500, "1tnk", 55, 48)

    in_sight = reach_of(world, enemies=[tank])
    gone = forward_staging.enemy_reach((60, 48), seen(world, 1005), Tuning())

    assert in_sight == [forward_staging.Reach(55, 48, 4.75 + 3)] and gone == []


@pytest.mark.parametrize(
    "enemies, buildings",
    [([foe(500, "1tnk", 39, 48)], []), ([foe(500, "heli", 58, 48)], []), ([], [foe(600, "pbox", 39, 48)])],
    ids=["a tank 21 cells off", "an aircraft", "a pillbox 21 cells off"],
)
def test_forward_staging_applies_only_with_a_defense_or_a_ground_unit_within_20_cells_of_the_objective(
    world, enemies, buildings
):
    assert reach_of(world, *buildings, enemies=enemies) == []


def test_forward_staging_once_it_applies_every_known_ground_defense_counts_wherever_it_stands(world):
    reach = reach_of(world, PILLBOX, foe(601, "gun", 30, 20))

    assert reach == [forward_staging.Reach(60, 48, 9), forward_staging.Reach(30, 20, 9.5)]


def test_forward_staging_the_margin_is_staging_margin_cells(world):
    tuning = Tuning(combat=CombatTuning(staging_margin_cells=5))

    assert reach_of(world, PILLBOX, tuning=tuning) == [forward_staging.Reach(60, 48, 11)]


# -- forward staging: holding at the staging cell and going on --------------------------------------

# main's staging cell on its way from the staging point to PILLBOX: the last cell of row 48 9 cells from it.
PILLBOX_STAGING = [51, 48]
ARTILLERY_IDS = (320, 321)


def screen_and_artillery(tanks_x: int, artillery_x: int, *, far_tank: bool = False) -> list[dict]:
    """Six light tanks in rows 47 and 49 from column `tanks_x` (the last at (40, 40) with `far_tank`), and two
    artillery at column `artillery_x` on those rows: main worth 5,900, a quarter of it artillery."""
    tanks = [own(300 + number, "1tnk", tanks_x + number % 3, 47 + number // 3 * 2) for number in range(6)]
    if far_tank:
        tanks[-1] = own(305, "1tnk", 40, 40)
    return [*tanks, *(own(unit_id, "arty", artillery_x, row) for unit_id, row in zip(ARTILLERY_IDS, (47, 49)))]


def beyond_the_staging_cell(orders: list[dict]) -> list[dict]:
    """The orders of a cycle that send a unit past the staging cell toward the pillbox, or attack it."""
    return [
        order
        for order in orders
        if order.get("target") == PILLBOX["id"] or ("to" in order and order["to"][0] > PILLBOX_STAGING[0])
    ]


def screen_moves(orders: list[dict]) -> list[dict]:
    """The moves of a cycle for the light tanks of `screen_and_artillery`, main's screen: those of the siege's line."""
    return [order for order in orders if order["cmd"] == "move" and set(order["units"]) <= set(range(300, 306))]


def test_forward_staging_main_advancing_on_a_pillbox_is_sent_to_the_staging_cell_9_cells_short_of_it():
    agent, _ = strong_main()

    orders = ask(agent, at(1000, five_tanks(5), buildings=[PILLBOX]))

    assert orders == [{"cmd": "attack_move", "units": [300, 301, 302, 303, 304], "to": PILLBOX_STAGING}]
    assert agent.ctx.army == frozenset(range(300, 305))


def test_forward_staging_main_holds_at_the_cell_until_75_percent_are_within_6_cells_and_both_artillery_within_10():
    agent, manager = strong_main()
    ask(agent, at(1000, screen_and_artillery(31, 30), buildings=[PILLBOX]))

    # Five of eight within 6 cells, the artillery 9 cells off; then six of eight, the artillery 11 cells off.
    five_there = ask(agent, at(1005, screen_and_artillery(50, 42, far_tank=True), buildings=[PILLBOX]))
    artillery_short = ask(agent, at(1010, screen_and_artillery(50, 40), buildings=[PILLBOX]))
    held_mode = manager.mode
    going_on = ask(agent, at(1015, screen_and_artillery(50, 42), buildings=[PILLBOX]))

    # Holding, the screen is sent nowhere but to the cell; going on, the siege has it hold its line 2 cells ahead of
    # the artillery at (42, 48), as it does on any advance.
    assert held_mode == ENGAGE and five_there == [{"cmd": "attack_move", "units": [305], "to": PILLBOX_STAGING}]
    assert artillery_short == []
    assert screen_moves(going_on) == [{"cmd": "move", "units": list(range(300, 306)), "to": [44, 48]}]


def test_forward_staging_with_the_artillery_stuck_main_goes_on_25_s_after_it_was_sent_to_the_cell():
    agent, _ = strong_main()
    ask(agent, at(1000, screen_and_artillery(31, 30), buildings=[PILLBOX]))
    stuck = screen_and_artillery(50, 36)

    held = [ask(agent, at(tick, stuck, buildings=[PILLBOX])) for tick in (1005, 1300, 1620)]
    going_on = ask(agent, at(1625, stuck, buildings=[PILLBOX]))

    assert all(screen_moves(orders) == [] and beyond_the_staging_cell(orders) == [] for orders in held)
    assert screen_moves(going_on) == [{"cmd": "move", "units": list(range(300, 306)), "to": [38, 48]}]


def test_forward_staging_main_holding_at_the_cell_fires_at_a_rifle_in_range_and_is_sent_back_out_of_its_reach():
    # The rifle is within the range of the tank at (52, 47) alone; its reach, 5 and 3, takes in the staging cell, which
    # moves back to (48, 48), the last cell of main's way out of it.
    agent, manager = strong_main()
    ask(agent, at(1000, screen_and_artillery(31, 30), buildings=[PILLBOX]))
    rifle = foe(500, "e1", 56, 45)

    orders = ask(agent, at(1005, screen_and_artillery(50, 36), [rifle], [PILLBOX]))

    assert manager.mode == ENGAGE and orders == [
        {"cmd": "attack", "units": [302], "target": 500},
        {"cmd": "attack_move", "units": [300, 301, 303, 304, 305], "to": [48, 48]},
    ]


@pytest.mark.parametrize("defense", ["agun", "sam"])
def test_forward_staging_an_aa_gun_or_sam_site_alone_at_the_objective_does_not_stage_main(defense):
    agent, _ = strong_main()

    orders = ask(agent, at(1000, five_tanks(5), buildings=[foe(600, defense, 60, 48)]))

    assert orders == [{"cmd": "attack_move", "units": [300, 301, 302, 303, 304], "to": [60, 48]}]


def test_forward_staging_off_main_goes_straight_on_to_the_pillbox():
    agent, _ = strong_main(tuning=UNSTAGED)

    orders = ask(agent, at(1000, five_tanks(5), buildings=[PILLBOX]))

    assert orders == [{"cmd": "attack_move", "units": [300, 301, 302, 303, 304], "to": [60, 48]}]


def test_forward_staging_main_whose_centroid_stands_in_the_pillbox_s_reach_already_goes_on_as_it_would():
    agent, manager = strong_main()
    ask(agent, at(1000, five_tanks(5)))
    in_reach = [own(300 + number, "1tnk", 53 + number % 3, 47 + number // 3) for number in range(5)]

    orders = ask(agent, at(1005, in_reach, buildings=[PILLBOX]))

    assert manager.mode == ENGAGE and {order["cmd"] for order in orders} == {"attack"}
    assert all(order["target"] == PILLBOX["id"] for order in orders)


# Two light tanks of the enemy's, 4 cells short of PILLBOX, in sight: their reach, 4.75 and 3, starts at column 49.
ENEMY_PAIR = [foe(500, "1tnk", 56, 47), foe(501, "1tnk", 56, 49)]


def moved_up(enemies: list[dict], cells_toward_main: int) -> list[dict]:
    return [{**enemy, "x": enemy["x"] - cells_toward_main} for enemy in enemies]


def staging_targets(orders: list[dict]) -> list[list[int]]:
    """The cells the attack-moves of a cycle send main's units to."""
    return [order["to"] for order in orders if order["cmd"] == "attack_move"]


def test_forward_staging_the_cell_moves_back_as_a_visible_enemy_group_comes_5_cells_nearer_main():
    agent, _ = strong_main()
    first = ask(agent, at(1000, five_tanks(5), ENEMY_PAIR, [PILLBOX]))
    on_the_way = [own(300 + number, "1tnk", 34 + number % 2, 47 + number // 2, idle=False) for number in range(5)]

    second = ask(agent, at(1005, on_the_way, moved_up(ENEMY_PAIR, 5), [PILLBOX]))

    assert staging_targets(first) == [[48, 48]] and staging_targets(second) == [[43, 48]]
    assert all(math.dist((43, 48), (enemy["x"] - 5, enemy["y"])) >= 4.75 + 3 for enemy in ENEMY_PAIR)


def test_forward_staging_once_main_has_gone_on_from_the_cell_it_stages_no_more_for_that_objective():
    agent, _ = strong_main()
    ask(agent, at(1000, five_tanks(5), ENEMY_PAIR, [PILLBOX]))
    at_the_cell = [own(300 + number, "1tnk", 46 + number % 2, 47 + number // 2) for number in range(5)]
    ask(agent, at(1005, at_the_cell, ENEMY_PAIR, [PILLBOX]))

    # Gone 7 cells farther, the enemy pair would leave a new staging cell at (51, 48), 4.6 cells ahead of main.
    orders = ask(agent, at(1010, at_the_cell, moved_up(ENEMY_PAIR, -7), [PILLBOX]))

    assert staging_targets(orders) == [[60, 48]]


def test_forward_staging_main_that_retreats_stages_again_when_it_next_sets_out():
    agent, manager = strong_main()
    ask(agent, at(1000, five_tanks(5), buildings=[PILLBOX]))
    at_the_cell = [own(300 + number, "1tnk", 50 + number % 2, 47 + number // 2) for number in range(5)]
    ask(agent, at(1005, at_the_cell, buildings=[PILLBOX]))
    heavy = [foe(510 + number, "3tnk", 46, 46 + number) for number in range(7)]
    ask(agent, at(1010, at_the_cell, heavy, [PILLBOX]))
    retreated = manager.mode

    orders = ask(agent, at(1200, five_tanks(5), buildings=[PILLBOX]))

    assert retreated == RETREAT and staging_targets(orders) == [PILLBOX_STAGING]


def test_forward_staging_main_sent_back_unscreened_stages_afresh_and_holds_when_it_next_sets_out(match_record):
    # Main set out at tick 1000; at 1005 its light tanks, at 20 percent, are worth less than the artillery (840 against
    # 1,700) and it comes back. Setting out again 28 s after the first time, it holds at the cell on a new clock.
    agent, _ = strong_main()
    ask(agent, at(1000, screen_and_artillery(31, 30), buildings=[PILLBOX]))
    worn = [{**unit, "hp": 20} if unit["type"] == "1tnk" else unit for unit in screen_and_artillery(31, 30)]
    ask(agent, at(1005, worn, buildings=[PILLBOX]))
    sent_back = agent.ctx.army

    orders = ask(agent, at(1700, screen_and_artillery(31, 30), buildings=[PILLBOX]))

    assert sent_back == frozenset() and agent.ctx.army == frozenset(range(300, 306)) | set(ARTILLERY_IDS)
    assert {"cmd": "attack_move", "units": list(range(300, 306)), "to": PILLBOX_STAGING} in orders
    assert [logged["phase"] for logged in match_record("combat") if logged["reason"] == "staging"] == []


# -- forward staging: the staging events -----------------------------------------------------------


def staging_events(match_record) -> list[dict]:
    """The combat events of reason staging the match wrote."""
    return [logged for logged in match_record("combat") if logged["reason"] == "staging"]


def test_forward_staging_main_s_arrival_and_its_going_on_on_its_body_are_combat_events_of_reason_staging(match_record):
    agent, _ = strong_main()
    ask(agent, at(1000, screen_and_artillery(31, 30), buildings=[PILLBOX]))
    ask(agent, at(1005, screen_and_artillery(50, 40), buildings=[PILLBOX]))
    ask(agent, at(1010, screen_and_artillery(50, 42), buildings=[PILLBOX]))

    there = {"reason": "staging", "cell": PILLBOX_STAGING, "body_share": 0.75, "long_range": 2}
    assert staging_events(match_record) == [
        {"tick": 1005, "phase": "arrival", **there, "long_range_present": False},
        {"tick": 1010, "phase": "engage", **there, "long_range_present": True, "engaged_by": "body"},
    ]


def test_forward_staging_with_the_artillery_stuck_the_engage_event_says_timeout_25_s_after_main_was_sent(match_record):
    agent, _ = strong_main()
    ask(agent, at(1000, screen_and_artillery(31, 30), buildings=[PILLBOX]))
    for tick in (1005, 1300, 1620, 1625, 1630):
        ask(agent, at(tick, screen_and_artillery(50, 36), buildings=[PILLBOX]))

    written = [(logged["tick"], logged["phase"], logged.get("engaged_by")) for logged in staging_events(match_record)]
    assert written == [(1005, "arrival", None), (1625, "engage", "timeout")]
    assert staging_events(match_record)[-1]["long_range_present"] is False


def test_forward_staging_main_that_needs_no_staging_writes_no_staging_event(match_record):
    agent, _ = strong_main()
    ask(agent, at(1000, five_tanks(5), buildings=[foe(600, "agun", 60, 48)]))

    assert staging_events(match_record) == []


# -- forward staging: the units joining main -------------------------------------------------------


def at_the_staging_cell() -> list[dict]:
    """The five light tanks of `five_tanks` at PILLBOX_STAGING, centroid (50.4, 47.8)."""
    return [own(300 + number, "1tnk", 50 + number % 2, 47 + number // 2) for number in range(5)]


def gone_on_from_the_cell():
    """A combat agent whose main of five light tanks has staged at PILLBOX_STAGING and gone on from it."""
    agent, _ = strong_main()
    ask(agent, at(1000, five_tanks(5), buildings=[PILLBOX]))
    ask(agent, at(1005, at_the_staging_cell(), buildings=[PILLBOX]))
    return agent


def naming(orders: list[dict], ids: set[int]) -> list[dict]:
    """The orders of a cycle that name any of these units."""
    return [order for order in orders if ids & set(order["units"])]


def test_forward_staging_a_unit_joining_main_after_it_went_on_is_sent_to_the_staging_cell_and_1000_worth_go_on():
    agent = gone_on_from_the_cell()
    # A medium tank (850) made at the staging point, then at the staging cell, where a light tank (700) joins it.
    medium, light = own(400, "2tnk", 32, 48), own(401, "1tnk", 50, 49)
    arrived = {**medium, "x": 50, "y": 50}

    made = ask(agent, at(1010, [*at_the_staging_cell(), medium], buildings=[PILLBOX]))
    alone = ask(agent, at(1015, [*at_the_staging_cell(), arrived], buildings=[PILLBOX]))
    together = ask(agent, at(1020, [*at_the_staging_cell(), arrived, light], buildings=[PILLBOX]))

    assert naming(made, {400}) == [{"cmd": "move", "units": [400], "to": PILLBOX_STAGING}]
    assert naming(alone, {400}) == []
    assert naming(together, {400, 401}) == [{"cmd": "attack_move", "units": [400, 401], "to": [50, 48]}]
    assert {400, 401} <= agent.ctx.army


def test_forward_staging_a_unit_on_its_way_to_the_staging_point_is_turned_to_the_staging_cell():
    agent = gone_on_from_the_cell()

    orders = ask(agent, at(1010, [*at_the_staging_cell(), own(400, "2tnk", 26, 48, idle=False)], buildings=[PILLBOX]))

    assert naming(orders, {400}) == [{"cmd": "move", "units": [400], "to": PILLBOX_STAGING}]


@pytest.mark.parametrize("tick, sent", [(1764, False), (1765, True)], ids=["29.96 s after", "30 s after"])
def test_forward_staging_joiners_at_the_staging_cell_go_on_30_s_after_the_first_arrived(tick, sent):
    agent = gone_on_from_the_cell()
    medium = own(400, "2tnk", 50, 50)
    ask(agent, at(1015, [*at_the_staging_cell(), medium], buildings=[PILLBOX]))

    orders = ask(agent, at(tick, [*at_the_staging_cell(), medium], buildings=[PILLBOX]))

    assert naming(orders, {400}) == ([{"cmd": "attack_move", "units": [400], "to": [50, 48]}] if sent else [])


def test_forward_staging_a_unit_made_while_main_holds_at_the_cell_meets_it_there():
    agent, _ = strong_main()
    ask(agent, at(1000, screen_and_artillery(31, 30), buildings=[PILLBOX]))

    orders = ask(agent, at(1005, [*screen_and_artillery(50, 36), own(400, "2tnk", 32, 48)], buildings=[PILLBOX]))

    assert naming(orders, {400}) == [{"cmd": "move", "units": [400], "to": PILLBOX_STAGING}]


# -- forward staging: a whole match ----------------------------------------------------------------

# An enemy pillbox in sight from the first minute on the way to the enemy's start, 8 cells short of it.
STAGING_PILLBOX_CELL = (66, 48)


@pytest.fixture(scope="module")
def staging_match() -> WholeMatch:
    """A whole fake match with the shipped tuning but the stepped movement, which forward staging gives way to on a
    stepped move, forward staging on, and a pillbox on main's way to the enemy."""

    def pillbox(game: FakeRedAlert) -> None:
        game.place_enemy("pbox", *STAGING_PILLBOX_CELL, visible=True)

    return play_whole_match(stepping_off(), scenario=[at_seconds(60, pillbox)])


def test_forward_staging_a_whole_fake_match_with_the_shipped_tuning_runs_without_an_error_or_a_refused_batch_in_budget(
    staging_match,
):
    assert staging_match.agent.ctx.tuning == stepping_off() and staging_match.agent.ctx.tuning.combat.forward_staging
    assert len(staging_match.compute_ms) == WHOLE_MATCH_STEPS and staging_match.data_of("error") == []
    assert staging_match.run.invalid_actions == 0 and staging_match.game.invalid_actions == 0
    assert staging_match.compute_p95_ms <= COMPUTE_P95_MS


def test_forward_staging_in_the_whole_match_main_stages_at_least_9_cells_from_the_pillbox_and_goes_on(staging_match):
    staged = [logged for logged in staging_match.data_of("combat") if logged["reason"] == "staging"]

    assert [logged["phase"] for logged in staged][-2:] == ["arrival", "engage"]
    assert all(math.dist(logged["cell"], STAGING_PILLBOX_CELL) >= 9 for logged in staged)


# -- vehicle repair: a vehicle about to be lost leaves main for a repair zone ----------------------

# A medium tank (850 at full health), worth repairing, with the four light tanks of `tanks`.
REPAIR_TANK = 310
MECHANICS = [own(600, "mech", 36, 48), own(601, "mech", 36, 49)]
# Two rifles in sight, which main's four light tanks engage.
TWO_RIFLES = [foe(500, "e1", 46, 48), foe(501, "e1", 46, 49)]
FAR_RIFLES = [foe(500, "e1", 70, 48), foe(501, "e1", 70, 49)]
DEPOT = ("fix", 26, 54)


def repair_tank(hp: int, x: int = 40, y: int = 47, unit_type: str = "2tnk") -> dict:
    return own(REPAIR_TANK, unit_type, x, y, hp)


def zone_moves(orders: list[dict]) -> list[dict]:
    """The orders that name the repairing tank."""
    return [order for order in orders if REPAIR_TANK in order["units"]]


def soviet_at(tick: int, hp: int, x: int = 40, y: int = 47, enemies=()) -> dict:
    """A state of a Soviet base at that tick with a heavy tank at `hp` and four more at full health."""
    units = [repair_tank(hp, x, y, "3tnk"), *tanks("3tnk")]
    return base_state("russia", YARD, tick=tick, units=units, visible_enemies=list(enemies))


@pytest.mark.parametrize(
    "first_hp, hp, enters",
    [(70, 60, True), (85, 80, False), (30, 30, True)],
    ids=["60 percent losing 10 a second", "80 percent losing 5 a second", "30 percent"],
)
def test_vehicle_repair_a_medium_tank_enters_repair_by_its_damage_rate_or_at_35_percent_or_less(first_hp, hp, enters):
    agent, manager = combat_agent()
    ask(agent, at(1000, [repair_tank(first_hp), *tanks()]))

    ask(agent, at(1000 + TICKS_PER_SECOND, [repair_tank(hp), *tanks()]))

    assert manager.repairing == (frozenset({REPAIR_TANK}) if enters else frozenset())


def test_vehicle_repair_with_two_mobile_mechanics_beyond_15_cells_of_the_tank_it_moves_to_their_centroid():
    agent, manager = combat_agent()
    # 16 cells and more from the tank at (40, 47), within 25 of main's centroid.
    far_mechanics = [own(600, "mech", 24, 50), own(601, "mech", 24, 51)]

    orders = ask(agent, at(1000, [repair_tank(30), *tanks(), *far_mechanics]))

    assert zone_moves(orders) == [{"cmd": "move", "units": [REPAIR_TANK], "to": [24, 51]}]


def test_vehicle_repair_with_no_mobile_mechanic_but_a_base_mechanic_the_tank_moves_to_the_base_repair_point():
    no_mobile = replace(Tuning(), composition=replace(Tuning().composition, mechanics_max=1, mechanics_base=1))
    agent, manager = combat_agent(tuning=no_mobile)
    units = [repair_tank(30), *tanks(), own(600, "mech", 30, 60)]

    orders = ask(agent, base_state("england", YARD, DEPOT, tick=1000, units=units))

    # 3 cells from the depot at (26, 54) on its side away from the enemy's start at (74, 48).
    assert zone_moves(orders) == [{"cmd": "move", "units": [REPAIR_TANK], "to": [23, 54]}]


def test_vehicle_repair_as_soviets_with_no_mechanic_the_tank_moves_to_the_staging_point():
    agent, manager = combat_agent()

    orders = ask(agent, soviet_at(1000, 30))

    assert zone_moves(orders) == [{"cmd": "move", "units": [REPAIR_TANK], "to": STAGING}]


def test_vehicle_repair_the_repairing_tank_gets_no_main_order_and_at_90_percent_rejoins(match_record):
    agent, manager = combat_agent()
    first = ask(agent, at(1000, [repair_tank(30), *tanks(), *MECHANICS], TWO_RIFLES))
    healing = ask(agent, at(1050, [repair_tank(60, 36, 49), *tanks(), *MECHANICS], TWO_RIFLES))
    in_repair = manager.repairing

    ask(agent, at(1100, [repair_tank(90, 36, 49), *tanks(), *MECHANICS], TWO_RIFLES))
    repaired = [logged for logged in match_record("combat") if logged["reason"] == "repair"]

    # The nearer of the two mechanics, at (36, 48).
    assert zone_moves(first) == [{"cmd": "move", "units": [REPAIR_TANK], "to": [36, 48]}]
    assert with_command(first, "attack") and zone_moves(healing) == []
    assert in_repair == frozenset({REPAIR_TANK}) and manager.repairing == frozenset()
    assert [(logged["phase"], logged["hp"]) for logged in repaired] == [("enter", 30), ("exit", 90)]
    assert (repaired[0]["zone"], repaired[0]["type"], repaired[1]["ended"]) == ("mobile", "2tnk", "healed")


def test_vehicle_repair_at_the_staging_point_with_no_mechanic_a_tank_rejoins_once_main_has_no_enemy_near(
    match_record,
):
    agent, manager = combat_agent()
    ask(agent, soviet_at(1000, 30, enemies=TWO_RIFLES))
    ask(agent, soviet_at(1025, 30, *STAGING, enemies=TWO_RIFLES))
    waiting = manager.repairing

    # The rifles walk off, out of reach of main's centroid.
    ask(agent, soviet_at(1050, 30, *STAGING, enemies=FAR_RIFLES))
    rejoined = manager.repairing
    ask(agent, soviet_at(1075, 30, *STAGING, enemies=FAR_RIFLES))
    kept_at_the_same_hp = manager.repairing
    ask(agent, soviet_at(1100, 25, *STAGING, enemies=FAR_RIFLES))
    repaired = [logged for logged in match_record("combat") if logged["reason"] == "repair"]

    assert (waiting, rejoined, kept_at_the_same_hp) == (frozenset({REPAIR_TANK}), frozenset(), frozenset())
    assert manager.repairing == frozenset({REPAIR_TANK})
    assert [(logged["phase"], logged.get("zone"), logged.get("ended")) for logged in repaired] == [
        ("enter", "staging", None),
        ("exit", None, "waited"),
        ("enter", "staging", None),
    ]


def test_vehicle_repair_mobile_mechanics_and_a_medic_go_3_cells_behind_main_s_artillery_and_never_get_an_attack():
    agent, manager = combat_agent()
    artillery = [own(320, "arty", 38, 48), own(321, "arty", 38, 49)]
    support_units = [own(600, "mech", 30, 40), own(601, "mech", 30, 41), own(700, "medi", 30, 42)]

    orders = ask(agent, at(1000, [*tanks(), *artillery, *support_units], TWO_RIFLES))
    supporting = [order for order in orders if {600, 601, 700} & set(order["units"])]

    # The artillery's centroid (38, 49), 3 cells on toward the side away from the enemy's start at (74, 48).
    assert with_command(orders, "attack") and supporting == [{"cmd": "move", "units": [600, 601, 700], "to": [35, 49]}]


def test_vehicle_repair_a_unit_released_for_repair_neither_closes_the_gate_nor_stays_main():
    tuning = Tuning().commander
    tanks_of_main = [Unit(300 + number, "2tnk", 40, 48, 100, True, None, None) for number in range(3)]
    released, kept = ForwardGate(), ForwardGate()
    for gate in (released, kept):
        gate.split(tanks_of_main, 1000)
        gate.follow(Measure(6000.0, 1.0, 0, True, tuple((unit.id, 2000.0) for unit in tanks_of_main)), False, tuning)

    released.release([302])
    # The two tanks left are worth 2,100 of the 4,000 they were worth when the gate opened; the third is repairing.
    after_release = released.follow(Measure(2100.0, 1.0, 0, True), False, tuning)
    after_keeping = kept.follow(Measure(2100.0, 1.0, 0, True), False, tuning)
    commanded = released.split(tanks_of_main, 1025)

    assert after_release is None and released.is_open
    assert after_keeping.reason == REGROUP and not kept.is_open
    assert [unit.id for unit in commanded] == [300, 301] and released.joiners.ids == frozenset({302})


def test_vehicle_repair_off_a_tank_at_30_percent_withdraws_on_its_own_and_on_it_goes_into_repair_instead():
    off_agent, off_manager = combat_agent(tuning=NO_VEHICLE_REPAIR)
    on_agent, on_manager = combat_agent()

    off_orders = ask(off_agent, at(1000, with_heavy_tank(30), EVEN_ENEMY))
    ask(on_agent, at(1000, with_heavy_tank(30), EVEN_ENEMY))

    assert (off_manager.withdrawn, off_manager.repairing) == (frozenset({REPAIR_TANK}), frozenset())
    assert off_orders[0] == {"cmd": "move", "units": [REPAIR_TANK], "to": STAGING}
    assert (on_manager.withdrawn, on_manager.repairing) == (frozenset(), frozenset({REPAIR_TANK}))


# -- repair toward near mechanics -------------------------------------------------------------------


def test_repair_near_of_2_mechanics_both_trail_main_and_none_stands_at_the_base_repair_point():
    agent, manager = combat_agent()
    mechanics = [own(600 + number, "mech", 30 + number, 60) for number in range(2)]

    orders = ask(agent, base_state("england", YARD, DEPOT, tick=1000, units=[*tanks(), *mechanics]))

    # main's centroid (41, 49), 3 cells on away from the enemy's start at (74, 48).
    assert [order for order in orders if {600, 601} & set(order["units"])] == [
        {"cmd": "move", "units": [600, 601], "to": [38, 49]},
    ]


def test_mechanics_real_army_with_a_depot_a_third_mechanic_also_trails_main_and_none_stands_at_the_base():
    agent, manager = combat_agent()
    mechanics = [own(600 + number, "mech", 30 + number, 60) for number in range(3)]

    orders = ask(agent, base_state("england", YARD, DEPOT, tick=1000, units=[*tanks(), *mechanics]))

    # main's centroid (41, 49), 3 cells on away from the enemy's start at (74, 48); nothing goes beside the depot.
    assert [order for order in orders if {600, 601, 602} & set(order["units"])] == [
        {"cmd": "move", "units": [600, 601, 602], "to": [38, 49]},
    ]


def test_mechanics_real_army_with_a_depot_and_no_mobile_mechanic_near_main_the_tank_moves_to_the_staging_point():
    agent, manager = combat_agent()
    # More than 25 cells from main's centroid: no mobile repair zone, and no base mechanic stands at the depot.
    far_mechanic = own(600, "mech", 10, 60)

    orders = ask(agent, base_state("england", YARD, DEPOT, tick=1000, units=[repair_tank(30), *tanks(), far_mechanic]))

    assert zone_moves(orders) == [{"cmd": "move", "units": [REPAIR_TANK], "to": STAGING}]


@pytest.mark.parametrize(
    "mechanic_x, first_hp, hp, enters",
    [(30, 55, 55, True), (20, 55, 55, False), (20, 65, 55, True), (30, 61, 61, False)],
    ids=[
        "55 percent with a mechanic 10 cells away",
        "55 percent with the mechanic 20 cells away",
        "20 cells away but losing 10 a second",
        "61 percent with a mechanic 10 cells away",
    ],
)
def test_repair_near_a_tank_at_60_percent_or_less_enters_repair_with_a_mobile_mechanic_within_15_cells(
    mechanic_x, first_hp, hp, enters, match_record
):
    agent, manager = combat_agent()
    mechanic = own(600, "mech", mechanic_x, 47)
    ask(agent, at(1000, [repair_tank(first_hp), *tanks(), mechanic]))

    ask(agent, at(1000 + TICKS_PER_SECOND, [repair_tank(hp), *tanks(), mechanic]))
    entered = [logged for logged in match_record("combat") if logged["reason"] == "repair"]

    assert manager.repairing == (frozenset({REPAIR_TANK}) if enters else frozenset())
    assert [logged["mechanic"] for logged in entered] == ([600 if mechanic_x == 30 else None] if enters else [])


def test_repair_near_the_tank_goes_to_the_nearest_mechanic_holds_while_its_hp_rises_and_after_5_s_still_goes_again():
    agent, manager = combat_agent()
    far_601 = own(601, "mech", 30, 52)
    # Mechanic 601 comes beside the tank, nearer than 600, while the tank heals beside 600.
    near_601 = own(601, "mech", 36, 48)
    states = [
        (1000, repair_tank(30), far_601),
        (1025, repair_tank(33, 35, 47), far_601),
        (1050, repair_tank(36, 35, 48), near_601),
        *((tick, repair_tank(36, 35, 48), near_601) for tick in range(1075, 1175, TICKS_PER_SECOND)),
        (1175, repair_tank(36, 35, 48), near_601),
    ]

    moves = {
        tick: zone_moves(ask(agent, at(tick, [tank, *tanks(), own(600, "mech", 34, 47), mechanic_601])))
        for tick, tank, mechanic_601 in states
    }

    assert manager.repairing == frozenset({REPAIR_TANK})
    # Sent to 600, 6 cells off; none while beside it and healing, nor for 5 s after its hp last rose at 1050; then
    # to the nearest one, 601.
    assert {tick: orders for tick, orders in moves.items() if orders} == {
        1000: [{"cmd": "move", "units": [REPAIR_TANK], "to": [34, 47]}],
        1175: [{"cmd": "move", "units": [REPAIR_TANK], "to": [36, 48]}],
    }


def test_repair_near_the_mechanic_a_repairing_tank_goes_to_does_not_trail_main_until_the_tank_leaves_repair():
    agent, manager = combat_agent()
    mechanics = [own(600, "mech", 34, 47), own(601, "mech", 30, 52)]

    def mechanic_moves(tick: int, tank: dict) -> list[dict]:
        orders = ask(agent, at(tick, [tank, *tanks(), *mechanics]))
        return [order for order in orders if {600, 601} & set(order["units"])]

    entering = mechanic_moves(1000, repair_tank(30))
    healing = mechanic_moves(1025, repair_tank(60, 35, 47))
    in_repair = manager.repairing
    healed = mechanic_moves(1050, repair_tank(90, 35, 47))

    # 601 trails main, 3 cells behind its centroid (41, 49); 600 stands for the tank until it rejoins at 90.
    assert entering == [{"cmd": "move", "units": [601], "to": [38, 49]}]
    assert all(600 not in order["units"] for order in healing) and in_repair == frozenset({REPAIR_TANK})
    assert manager.repairing == frozenset() and any(600 in order["units"] for order in healed)


# -- the rally covering the expansions -------------------------------------------------------------------------------

COVER_RALLY = Tuning(combat=CombatTuning(expansion_cover_rally=True))
# The cover tests' line: from the base at (20, 50) east to the enemy's start at (100, 50); halfway is 40 cells out.
COVER_BASE, COVER_ENEMY = (20, 50), (100, 50)
# The main yard, its middle (19, 50) by the base's center.
COVER_MAIN_YARD = ("fact", 19, 49)


def yard_at(middle: tuple[int, int]) -> tuple[str, int, int]:
    """A construction yard whose middle cell is `middle`."""
    return "fact", middle[0], middle[1] - 1


def cover_world(*middles: tuple[int, int], enemy_buildings=()) -> World:
    """The world of an Allied base with the main yard and expansion yards whose middles are given, on the line from
    COVER_BASE to COVER_ENEMY."""
    world = World(tracked_items())
    yards = [yard_at(middle) for middle in middles]
    world.update(base_state("england", COVER_MAIN_YARD, *yards, visible_enemy_buildings=list(enemy_buildings)))
    world.base_center, world.enemy_base = COVER_BASE, COVER_ENEMY
    return world


def depth(cell: tuple[int, int]) -> float:
    """How far along the cover tests' line a cell lies."""
    return cell[0] - COVER_BASE[0]


def test_cover_point_ahead_of_expansion_on_line():
    world = cover_world((36, 56))

    point = cover_point(world, COVER_RALLY)

    # The yard's depth 16 and 6 cells more.
    assert point == (42, 50) and math.dist(point, (36, 56)) <= 18


def test_cover_point_pulled_back_out_of_defense_reach():
    pillbox = foe(600, "pbox", 50, 50)
    world = cover_world((36, 56), enemy_buildings=[pillbox])

    point = cover_point(world, COVER_RALLY)

    # A pillbox shoots 6 cells; with the margin of 4 the point stands back from (42, 50).
    assert point[1] == 50 and point[0] < 42 and math.dist(point, (50, 50)) > 6 + 4
    assert not is_covered(point, world.known_enemy_defenses, world.items, replace(Tuning().coverage, margin_cells=4))
    assert math.dist(point, (36, 56)) <= 18


def test_cover_point_within_reach_of_every_expansion():
    world = cover_world((30, 40), (40, 50))

    point = cover_point(world, COVER_RALLY)

    # 26 cells out, (46, 50) lies 18.9 cells from (30, 40): the point steps back to the first cell within 18 of both.
    assert point[1] == 50 and 44 <= point[0] <= 45
    assert all(math.dist(point, middle) <= 18 for middle in ((30, 40), (40, 50)))


def test_cover_point_fallback_far_apart_expansions():
    world = cover_world((25, 25), (40, 75))

    point = cover_point(world, COVER_RALLY)

    # No cell of the line lies within 18 of both: 6 cells from the farther-forward yard toward the enemy's start.
    assert math.dist((25, 25), (40, 75)) > 50
    assert point == cells.toward((40, 75), COVER_ENEMY, 6)


def test_never_past_halfway():
    world = cover_world((58, 50))

    point = cover_point(world, COVER_RALLY)

    assert point == (60, 50) and depth(point) <= 40


def test_cover_point_none_without_expansion_or_switch_off():
    assert cover_point(cover_world(), COVER_RALLY) is None


# In the agent's state, the base at (22, 48) and the enemy's start at (74, 48): an expansion yard whose middle is
# (35, 52), 13 cells out, and the cover point 6 cells beyond it on the line.
EXPANSION = yard_at((35, 52))
COVER = (41, 48)


def worth_800(x: int = 32, y: int = 48) -> list[dict]:
    """A jeep and a rocket soldier, 800 in a fight together, at a cell and beside it."""
    return [own(320, "jeep", x, y), own(321, "e3", x + 1, y)]


def cover_at(tick: int, units=(), enemies=(), *more_buildings) -> dict:
    """A state of an Allied base with the expansion yard and the buildings given, at that tick, with these own units
    and these enemies in sight."""
    shown = {"units": list(units), "visible_enemies": list(enemies)}
    return base_state("england", YARD, EXPANSION, *more_buildings, tick=tick, **shown)


@pytest.mark.parametrize("plan", ["HOLD", "RAID"])
def test_cover_rally_main_gathers_at_the_cover_point_whatever_its_strength(plan, match_record):
    agent, _ = hold_agent(plan=plan, tuning=COVER_RALLY)

    sent = ask(agent, cover_at(1000, worth_800()))

    assert agent.ctx.gather_point == COVER and moved_to(sent, COVER) == {320, 321}
    assert match_record("hold_position") == [
        {"tick": 1000, "cell": list(COVER), "reason": "cover_expansions", "strength": 800}
    ]


def test_cover_rally_under_opening_waits_for_the_war_factory():
    agent, _ = hold_agent(plan="OPENING", tuning=COVER_RALLY)

    ask(agent, cover_at(1000, worth_800()))
    without = agent.ctx.gather_point
    ask(agent, cover_at(1005, worth_800(), (), ("weap", 15, 48)))

    assert (without, agent.ctx.gather_point) == (None, COVER)


def test_no_expansion_rally_unchanged(match_record):
    agent, _ = hold_agent(tuning=COVER_RALLY)

    sent = ask(agent, at(1000, worth_2000(43, 48)))

    assert sent == [{"cmd": "move", "units": [300, 301, 302], "to": STAGING}] and agent.ctx.gather_point is None
    assert match_record("hold_position") == []


def test_attack_plans_unchanged():
    agent, _ = hold_agent(plan="PRESS", tuning=COVER_RALLY)

    sent = ask(agent, cover_at(1000, worth_800()))

    assert agent.ctx.gather_point is None and moved_to(sent, COVER) == set()


def test_switch_off_rally_as_before(match_record):
    agent, _ = hold_agent(tuning=Tuning(combat=CombatTuning(expansion_cover_rally=False)))

    sent = ask(agent, cover_at(1000, worth_800()))

    assert agent.ctx.gather_point is None and moved_to(sent, COVER) == set()
    assert match_record("hold_position") == []


# -- reinforcements held home while main is away (reinforce_hold_home) -------------------------------------------------

# main of six medium tanks gone forward 45 cells east of the staging point.
AWAY_MAIN = [own(300 + n, "2tnk", 76 + n % 3, 47 + n // 3) for n in range(6)]
AT_STAGING = [own(300 + n, "2tnk", 31 + n % 3, 47 + n // 3) for n in range(6)]
# Main back 23 cells from the staging point.
BACK_MAIN = [own(300 + n, "2tnk", 54 + n % 3, 47 + n // 3) for n in range(6)]


# The hold on, which ships off.
HOLD_ON = Tuning(combat=CombatTuning(reinforce_hold_home=True))


def held_home_agent(tuning: Tuning | None = None):
    """A combat agent whose main of six medium tanks went forward from the staging point at tick 1000, the hold on."""
    manager = CombatManager()
    agent = situations.new_agent(manager, tuning=tuning or stepping_off(screen_place_off(HOLD_ON)))
    agent.ctx.min_attack_strength = lambda plan: 3000
    ask(agent, at(1000, AT_STAGING))
    assert agent.ctx.army == frozenset(range(300, 306))
    return agent, manager


def new_tanks(count: int, first: int = 400) -> list[dict]:
    """Medium tanks made at the staging point (850 each), numbered from `first`."""
    return [own(first + n, "2tnk", 32 + n % 2, 48 + n // 2) for n in range(count)]


def test_reinforce_hold_held_while_away_two_tanks_get_no_order_toward_main_for_60_s(match_record):
    agent, _ = held_home_agent()
    made = new_tanks(2)

    cycles = [ask(agent, at(1005 + 25 * n, [*AWAY_MAIN, *made])) for n in range(61)]

    sent = [order for orders in cycles for order in naming(orders, {400, 401})]
    assert all(math.dist(order["to"], STAGING) <= 6 for order in sent)
    assert not {400, 401} & agent.ctx.army
    (held,) = [data for data in match_record("combat") if data["reason"] == "reinforce"]
    assert (held["phase"], held["count"], held["distance"]) == ("held", 2, 45.0)


def test_reinforce_hold_group_of_own_sets_out_together_by_steps_with_a_sent_event(match_record):
    agent, _ = held_home_agent(screen_place_off(HOLD_ON))
    made = [own(400 + n, "2tnk", x, y) for n, (x, y) in enumerate([(31, 48), (33, 48), (32, 47), (32, 49)])]

    orders = ask(agent, at(1005, [*AWAY_MAIN, *made]))

    (going,) = naming(orders, {400, 401, 402, 403})
    assert sorted(going["units"]) == [400, 401, 402, 403] and 9 <= math.dist(going["to"], STAGING) <= 11
    assert going["to"][0] > STAGING[0]
    sent = [data for data in match_record("combat") if data["reason"] == "reinforce" and data["phase"] == "sent"]
    assert [(data["count"], data["value"]) for data in sent] == [(4, 3400)]


def test_reinforce_hold_a_group_that_reached_main_joins_it():
    agent, _ = held_home_agent()
    ask(agent, at(1005, [*AWAY_MAIN, *new_tanks(4)]))
    arrived = [own(400 + n, "2tnk", 75, 46 + n) for n in range(4)]

    ask(agent, at(1010, [*AWAY_MAIN, *arrived]))

    assert {400, 401, 402, 403} <= agent.ctx.army


def test_reinforce_hold_army_back_within_25_cells_the_held_tanks_join_by_today_s_rule():
    agent, _ = held_home_agent()
    ask(agent, at(1005, [*AWAY_MAIN, *new_tanks(2)]))

    orders = ask(agent, at(1010, [*BACK_MAIN, *new_tanks(2)]))

    # 1,700 together, over reinforcement_strength: both attack-move to main's centroid as one group.
    assert naming(orders, {400, 401}) == [{"cmd": "attack_move", "units": [400, 401], "to": [55, 48]}]


def test_reinforce_hold_staging_joiners_held_with_the_staging_cell_40_cells_away():
    far_pillbox = foe(600, "pbox", 81, 48)
    agent, _ = strong_main(tuning=Tuning(combat=CombatTuning(vehicle_repair=False, reinforce_hold_home=True)))
    ask(agent, at(1000, five_tanks(5), buildings=[far_pillbox]))
    at_far_cell = [own(300 + number, "1tnk", 71 + number % 2, 47 + number // 2) for number in range(5)]
    ask(agent, at(1005, at_far_cell, buildings=[far_pillbox]))
    # A light tank, a rocket soldier and two rifles: 1,200 together.
    joiners = [own(400, "1tnk", 32, 48), own(401, "e3", 33, 48), own(402, "e1", 32, 49), own(403, "e1", 33, 49)]

    cycles = [ask(agent, at(tick, [*at_far_cell, *joiners], buildings=[far_pillbox])) for tick in (1010, 1765, 1800)]

    sent = [order for orders in cycles for order in naming(orders, {400, 401, 402, 403})]
    assert all(order["cmd"] != "attack_move" and math.dist(order["to"], STAGING) <= 6 for order in sent)


def test_reinforce_hold_switch_off_the_two_tanks_go_after_main_at_once():
    agent, _ = held_home_agent(stepping_off(SWITCHES_OFF))

    orders = ask(agent, at(1005, [*AWAY_MAIN, *new_tanks(2)]))

    assert naming(orders, {400, 401}) == [{"cmd": "attack_move", "units": [400, 401], "to": [77, 48]}]


def test_reinforce_hold_home_point_is_the_forward_gather_point_while_one_is_published():
    hold = reinforce_hold.HomeHold()
    world = seen(World(tracked_items()), 1000, units=new_tanks(2))
    joining = list(world.combat_units.values())

    verdict = hold.follow(joining, (90.0, 40.0), reinforce_hold.home_point((50, 40), (32, 48)), world, HOLD_ON)

    assert reinforce_hold.home_point(None, (32, 48)) == (32, 48)
    assert verdict.held == joining and verdict.group == [] and verdict.target == (90, 40)
    assert hold.follow(joining, (60.0, 40.0), (50, 40), world, HOLD_ON) is None


def test_reinforce_hold_keys_ship_off_and_the_tracked_file_holds_the_built_in_values():
    combat = CombatTuning()
    keys = (combat.reinforce_hold_home, combat.reinforce_away_cells, combat.reinforce_group_min_value)

    assert keys == (False, 30, 3000)
    assert Tuning.from_file(REPO_ROOT).combat == combat and not SWITCHES_OFF.combat.reinforce_hold_home
