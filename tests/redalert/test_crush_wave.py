"""The crush wave: against an infantry all-in, every fit tank near gathers outside the mass, then all drive through it
together on parallel lanes, the stragglers as one second wave of two or more, never one alone."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pytest

from situations import ask, base_state, new_agent
from ucla_agent.games.redalert import crush, crush_wave
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning
from whole_match import COMPUTE_P95_MS, WHOLE_MATCH_STEPS, WholeMatch, play_whole_match

REPO_ROOT = Path(__file__).resolve().parents[2]
YARD = ("fact", 22, 48)
SHIPPED = Tuning()
WAVE_OFF = replace(SHIPPED, combat=replace(SHIPPED.combat, crush_wave=False))
# The mass: ten rifles within 2 cells of (42, 48), 20 cells east of the base's center.
MASS = (42, 48)
MASS_CELLS = [(41, 47), (42, 47), (43, 47), (41, 48), (42, 48), (43, 48), (41, 49), (42, 49), (43, 49), (42, 50)]
# Six medium tanks 13 to 14 cells west of the mass; their gather point lies 8 cells west of it, at (34, 48).
TANK_CELLS = [(28, 46), (29, 47), (28, 48), (29, 49), (28, 50), (29, 48)]
GATHER = (34, 48)
TANK_IDS = list(range(300, 306))
START = 1000


def own(unit_id: int, unit_type: str, x: int, y: int, hp: int = 100) -> dict:
    """One own unit as an observation lists it, idle."""
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": hp, "act": None, "idle": True}


def foe(enemy_id: int, enemy_type: str, x: int, y: int) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


def rifles(count: int = 10, at: tuple[int, int] = MASS) -> list[dict]:
    """`count` enemy rifles of the mass, moved so that its centroid stands at `at`."""
    dx, dy = at[0] - MASS[0], at[1] - MASS[1]
    return [foe(500 + n, "e1", x + dx, y + dy) for n, (x, y) in enumerate(MASS_CELLS[:count])]


def tanks(cells=TANK_CELLS, first: int = 300) -> list[dict]:
    """Medium tanks of ours on these cells, numbered from `first`."""
    return [own(first + n, "2tnk", x, y) for n, (x, y) in enumerate(cells)]


def at_gather(count: int) -> list[tuple[int, int]]:
    """The tanks' cells with the first `count` of them standing within 3 cells of the gather point."""
    near = [(34, 47), (34, 49), (33, 48), (35, 48), (34, 48), (33, 47)]
    return [*near[:count], *TANK_CELLS[count:]]


def state(tick: int, units=(), enemies=(), buildings=()) -> dict:
    """A state of the Allied base at that tick with these own units and these enemies in sight."""
    shown = {"units": list(units), "visible_enemies": list(enemies), "visible_enemy_buildings": list(buildings)}
    return base_state("england", YARD, tick=tick, **shown)


def moves(orders: list[dict]) -> dict[int, tuple[int, int]]:
    """The cell each unit of a cycle's moves goes to."""
    return {unit_id: tuple(order["to"]) for order in orders if order["cmd"] == "move" for unit_id in order["units"]}


def events(match_record, reason: str) -> list[dict]:
    return [data for data in match_record("combat") if data["reason"] == reason]


class Wave:
    """An agent with the combat manager under a tuning, one cycle at a time."""

    def __init__(self, tuning: Tuning = SHIPPED, *more) -> None:
        self.manager = CombatManager()
        self.agent = new_agent(*more, self.manager, tuning=tuning)

    def cycle(self, tick: int, units, enemies=(), buildings=()) -> list[dict]:
        return ask(self.agent, state(tick, units, enemies, buildings))


def formed() -> tuple[Wave, list[dict]]:
    """A wave formed over the ten rifles by the six tanks, and the forming cycle's orders."""
    wave = Wave()
    return wave, wave.cycle(START, tanks(), rifles())


def launched_with_five() -> tuple[Wave, list[dict]]:
    """The wave launched with five of six present, and the launch cycle's orders."""
    wave, _ = formed()
    return wave, wave.cycle(START + 5, tanks(at_gather(5)), rifles())


# -- 1. detection ----------------------------------------------------------------------------------------------------


def test_detection_ten_rifles_20_cells_from_base_with_six_fit_tanks_form_a_wave(match_record):
    wave, _ = formed()

    assert set(wave.agent.ctx.crush_run) == set(TANK_IDS)
    assert set(wave.agent.ctx.crush_run.values()) == {GATHER}
    assert events(match_record, crush.CRUSH_RUN) == []


def test_detection_seven_rifles_form_no_wave_and_the_script_s_run_is_taken():
    wave = Wave()
    near = [(x + 6, y) for x, y in TANK_CELLS]

    orders = wave.cycle(START, tanks(near), rifles(7))

    # The script's run: min(3, ceil(7 / 3)) = 3 tanks, each to the cell 2 past the cluster's centroid.
    assert len(wave.agent.ctx.crush_run) == 3 and GATHER not in wave.agent.ctx.crush_run.values()
    assert wave.manager._wave.is_active is False and len(moves(orders)) >= 3


def test_detection_a_mass_30_cells_from_base_and_14_from_every_tank_forms_no_wave():
    wave = Wave()

    wave.cycle(START, tanks(), rifles(at=(52, 48)))

    assert wave.manager._wave.is_active is False


# -- 2. gather, not trickle ------------------------------------------------------------------------------------------


def test_gather_in_the_forming_cycle_all_six_tanks_move_to_one_cell_8_cells_from_the_mass_on_their_side():
    _, orders = formed()

    sent = moves(orders)
    assert {unit_id: sent.get(unit_id) for unit_id in TANK_IDS} == dict.fromkeys(TANK_IDS, GATHER)
    assert 7 <= math.dist(GATHER, MASS) <= 9 and GATHER[0] < MASS[0]
    assert not any(order["to"][0] > GATHER[0] for order in orders if set(order["units"]) & set(TANK_IDS))


# -- 3. ready launch -------------------------------------------------------------------------------------------------


def test_ready_launch_five_of_six_present_go_in_one_cycle_on_lanes_2_apart_4_past_the_mass(match_record):
    _, orders = launched_with_five()

    sent = moves(orders)
    targets = [sent[unit_id] for unit_id in TANK_IDS[:5]]
    assert all(abs(cell[0] - (MASS[0] + 4)) <= 1 for cell in targets)
    assert all(math.dist(a, b) >= 2 for i, a in enumerate(targets) for b in targets[i + 1 :])
    assert sent.get(305) in (None, GATHER)
    (launch,) = events(match_record, crush_wave.CRUSH_WAVE)
    assert (launch["wave"], launch["launch"], launch["present"], launch["total"]) == (1, "ready", 5, 6)
    assert sorted(launch["tanks"]) == TANK_IDS[:5] and launch["gather"] == list(GATHER) and launch["mass"] == 10


# -- 4. timeout ------------------------------------------------------------------------------------------------------


def test_timeout_three_of_six_present_wait_at_5_9_s_and_launch_together_at_6_0_s(match_record):
    wave, _ = formed()
    waiting = wave.cycle(START + 147, tanks(at_gather(3)), rifles())
    gone = wave.cycle(START + 6 * TICKS_PER_SECOND, tanks(at_gather(3)), rifles())

    assert all(cell == GATHER for unit_id, cell in moves(waiting).items() if unit_id in TANK_IDS)
    assert all(moves(gone)[unit_id][0] > MASS[0] for unit_id in TANK_IDS[:3])
    (launch,) = events(match_record, crush_wave.CRUSH_WAVE)
    assert (launch["launch"], launch["present"], launch["waited_s"]) == ("timed_out", 3, 6.0)


def test_timeout_with_one_present_at_6_s_the_wave_dissolves_with_no_launch(match_record):
    wave, _ = formed()

    orders = wave.cycle(START + 6 * TICKS_PER_SECOND, tanks(at_gather(1)), rifles())

    (dissolved,) = events(match_record, crush_wave.CRUSH_WAVE)
    assert dissolved["launch"] == "dissolved" and dissolved["tanks"] == []
    assert not any(cell[0] > MASS[0] for cell in moves(orders).values())


# -- 5. the second wave ----------------------------------------------------------------------------------------------


def test_second_wave_the_sixth_tank_alone_never_runs_and_with_a_seventh_both_launch_as_wave_2(match_record):
    wave, _ = launched_with_five()
    alone = [wave.cycle(START + 10 + 5 * n, tanks(at_gather(6)), rifles()) for n in range(4)]
    seventh = [*tanks(at_gather(6)), own(306, "2tnk", 33, 49)]

    pair = wave.cycle(START + 40, seventh, rifles())

    assert all(moves(orders).get(305) in (None, GATHER) for orders in alone)
    assert moves(pair)[305][0] > MASS[0] and moves(pair)[306][0] > MASS[0]
    second = [data for data in events(match_record, crush_wave.CRUSH_WAVE) if data["wave"] == 2]
    assert [sorted(data["tanks"]) for data in second] == [[305, 306]] and second[0]["launch"] == "ready"


# -- 6. no per-tank release ------------------------------------------------------------------------------------------


def test_no_per_tank_release_a_tank_at_its_cell_at_3_s_stays_in_the_run_until_it_ends_for_all(match_record):
    wave, launch = launched_with_five()
    targets = moves(launch)
    arrived = [own(300, "2tnk", *targets[300]), *tanks(at_gather(6))[1:]]

    wave.cycle(START + 5 + 3 * TICKS_PER_SECOND, arrived, rifles())
    held = dict(wave.agent.ctx.crush_run)
    wave.cycle(START + 5 + 10 * TICKS_PER_SECOND, arrived, rifles())
    after = dict(wave.agent.ctx.crush_run)

    assert held[300] == targets[300] and set(TANK_IDS[:5]) <= set(held)
    assert not set(TANK_IDS[:5]) & set(after)
    (run,) = events(match_record, crush.CRUSH_RUN)
    assert (run["wave"], run["ended"], sorted(run["tanks"])) == (1, crush.TIMED_OUT, TANK_IDS[:5])


# -- 7. defenses -----------------------------------------------------------------------------------------------------


def test_defenses_a_mass_covered_by_a_known_pillbox_forms_no_wave():
    wave = Wave()

    wave.cycle(START, tanks(), rifles(), [foe(600, "pbox", 45, 48)])

    assert wave.manager._wave.is_active is False


def test_defenses_a_gather_cell_in_a_turret_s_coverage_moves_along_the_ray_and_aa_covers_nothing():
    wave = Wave()
    wave.cycle(START, tanks(), rifles(), [foe(600, "gun", 35, 55), foe(601, "agun", 34, 45)])
    world = wave.agent.world
    units = [unit for unit in world.combat_units.values()]

    point = crush_wave.gather_point(MASS, units, world, Terrain(), SHIPPED)

    # The turret's coverage (8.5 cells) takes in (34, 48) to (31, 48): the point is moved on to (30, 48).
    assert point == (30, 48) and not crush_wave.is_covered(point, world, SHIPPED)
    assert crush_wave.is_covered(GATHER, world, SHIPPED) and not crush_wave.is_covered((34, 45), world, SHIPPED)


# -- 8. exclusivity --------------------------------------------------------------------------------------------------


def test_exclusivity_while_forming_no_script_run_starts_and_the_commander_s_run_over_is_held_off(match_record):
    wave, _ = formed()

    wave.cycle(START + 5, tanks(), rifles())

    # A non-empty crush run is what keeps the commander's run-over (J) off its task options.
    assert wave.manager._crush.units == frozenset() and set(wave.agent.ctx.crush_run) == set(TANK_IDS)


def test_exclusivity_while_the_run_over_detachment_is_out_no_wave_forms():
    wave = Wave()
    wave.agent.ctx.run_over_out = True

    wave.cycle(START, tanks(), rifles())

    assert wave.manager._wave.is_active is False


def test_exclusivity_base_defense_gives_a_wave_tank_no_order():
    wave = Wave(SHIPPED, BaseDefenseManager())
    wave.cycle(START, tanks(), rifles())

    orders = wave.cycle(START + 5, tanks(at_gather(2)), rifles())

    wave_ids = set(wave.agent.ctx.crush_run)
    assert all(
        order["cmd"] == "move" and tuple(order["to"]) == GATHER for order in orders if set(order["units"]) & wave_ids
    )


def test_exclusivity_an_emergency_of_main_s_reaches_a_wave_tank_and_takes_it_out_of_the_wave():
    wave, _ = formed()
    retreat = Intent(Priority.EMERGENCY, {"cmd": "move", "units": [300], "to": [22, 48]})

    held = wave.manager._wave.held([retreat])

    assert 300 not in held and 300 not in wave.manager._wave.units and 301 in held


# -- 10. the switch --------------------------------------------------------------------------------------------------


def test_switch_off_the_ten_rifles_give_the_script_s_run_exactly():
    off = Wave(WAVE_OFF)
    near = [(x + 6, y) for x, y in TANK_CELLS]

    orders = off.cycle(START, tanks(near), rifles())
    world_off = off.agent.world

    cluster = crush.qualifying_cluster(list(world_off.combat_units.values()), world_off, WAVE_OFF)
    expected = crush.run_cells(cluster, list(world_off.combat_units.values()), world_off, Terrain(), WAVE_OFF.combat, 3)
    assert dict(off.agent.ctx.crush_run) == expected and off.manager._wave.is_active is False
    assert {unit_id: moves(orders)[unit_id] for unit_id in expected} == expected


def test_the_wave_keys_ship_on_and_the_tracked_file_holds_the_built_in_values():
    combat = CombatTuning()
    keys = {
        "crush_wave": True,
        "wave_min_infantry": 8,
        "wave_mass_cells": 6,
        "wave_base_cells": 25,
        "wave_army_cells": 12,
        "wave_reach_cells": 16,
        "wave_min_tanks": 3,
        "wave_max_tanks": 10,
        "wave_gather_cells": 8,
        "wave_gather_radius_cells": 3,
        "wave_ready_share": 0.75,
        "wave_max_wait_seconds": 6,
        "wave_lane_cells": 2,
        "wave_beyond_cells": 4,
        "wave_run_seconds": 10,
        "wave_second_min_tanks": 2,
    }

    assert {key: getattr(combat, key) for key in keys} == keys
    assert Tuning.from_file(REPO_ROOT).combat == combat


# -- 11. a whole match -----------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def wave_match() -> WholeMatch:
    return play_whole_match(Tuning())


def test_whole_match_with_the_shipped_tuning_runs_without_an_error_in_budget(wave_match):
    assert wave_match.agent.ctx.tuning.combat.crush_wave
    assert len(wave_match.compute_ms) == WHOLE_MATCH_STEPS and wave_match.data_of("error") == []
    assert wave_match.compute_p95_ms <= COMPUTE_P95_MS
