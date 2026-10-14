"""Placement: the candidate cells around the construction yard, the cells kept free, and each building's preference."""

from __future__ import annotations

import copy
import dataclasses
import json
import math
from collections import Counter
from pathlib import Path

import pytest

import situations
from fake_game import MAP_ID, REPO_ROOT, FakeRedAlert
from switches_off import expansion_off, new_agent, opening_off, placement_off, tuning_off, upkeep_off
from ucla_agent.games.redalert import cells, creep, expansion, forward_hold, placement, refinery_fields
from ucla_agent.games.redalert.items import TICKS_PER_SECOND, ItemTable
from ucla_agent.games.redalert.ledger import OrderLedger
from ucla_agent.games.redalert.managers import OpeningBuild
from ucla_agent.games.redalert.opening import OpeningManager
from ucla_agent.games.redalert.placement import (
    FIELD_NEAREST_UNDER_CAP,
    FIELD_SHARED_REACHED,
    FIELD_UNSERVED_NEAREST,
    FIELD_UNSERVED_REACHED,
    Box,
    box_at,
    candidate_cells,
    choose_placement,
    excluded_cells,
    footprint,
    ore_distance,
    preferred_cell,
    reaches,
)
from ucla_agent.games.redalert.production import Production
from ucla_agent.games.redalert.refused import RefusedPlaces
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import REACH_FIRST, PlacementTuning, Tuning
from ucla_agent.games.redalert.upkeep import UpkeepManager, creep_target_after_opening
from ucla_agent.games.redalert.world import DEFENSE_TYPES, World
from whole_match import COMPUTE_P95_MS, WHOLE_MATCH_STEPS, WholeMatch, play_whole_match, with_ore

TUNING = placement_off()
YARD = ("fact", 22, 48)
# The cells the yard covers: the state reports it at the middle of its footprint.
YARD_BOX = box_at("fact", (21, 47))
BOUNDS = (0, 0, 96, 96)
NO_TERRAIN = Terrain()


def square_patch(center: tuple[int, int], reach: int = 1) -> OrePatch:
    """An ore patch of the square of cells within `reach` of its center, counted as a king moves."""
    x, y = center
    field = frozenset((x + dx, y + dy) for dy in range(-reach, reach + 1) for dx in range(-reach, reach + 1))
    return OrePatch(center=center, cells=field)


# The fake game's ore north of its first spawn, as the terrain reads it.
HOME_PATCH = square_patch((22, 40))
HOME_ORE = [(x, y) for y in (39, 40, 41) for x in (21, 22, 23)]


def world_with(*buildings: tuple[str, int, int], enemy_base=(74, 48), bounds=BOUNDS, base_center=None) -> World:
    """A world whose own buildings are the given (type, x, y), on a map with the given bounds.

    Each (x, y) is the cell the state reports the building at: the middle cell of a building two cells or more across,
    one right and one down of its footprint's top-left cell (see `placement.reported_offset`). The base's center is
    the first building's cell unless one is given.
    """
    first_cell = [list(spec[1:]) for spec in buildings[:1]]
    state = {
        "phase": "playing",
        "map": {"bounds": bounds},
        "observation": {
            "tick": 100,
            "buildings": [situations.building(100 + number, *spec) for number, spec in enumerate(buildings)],
            "base_center": base_center or next(iter(first_cell), None),
        },
        "legal_actions": {"enemy_base": enemy_base},
    }
    world = World(ItemTable([]))
    world.update(state)
    return world


def stands(item: str, cell: tuple[int, int]) -> tuple[str, int, int]:
    """A building that stands where it was asked onto, as the state reports it: its footprint's top-left cell plus the
    type's reported offset (`placement.reported_offset`)."""
    offset_x, offset_y = placement.reported_offset(item)
    return item, cell[0] + offset_x, cell[1] + offset_y


def boxes_of(world: World) -> list[Box]:
    return [placement.standing_box(building) for building in world.buildings.values()]


# -- sizes and rectangles --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "item, size",
    [("fact", (3, 4)), ("powr", (2, 3)), ("tent", (2, 3)), ("barr", (2, 3)), ("proc", (3, 4)), ("weap", (3, 3))],
)
def test_the_buildings_of_the_opening_have_the_sizes_of_the_game_s_rules(item, size):
    assert footprint(item) == size


@pytest.mark.parametrize(
    "item, size", [("pbox", (1, 1)), ("gun", (1, 1)), ("ftur", (1, 1)), ("tsla", (1, 1)), ("sam", (2, 1))]
)
def test_a_defense_covers_one_cell_unless_the_rules_say_otherwise(item, size):
    assert footprint(item) == size


def test_a_silo_and_a_kennel_cover_one_cell():
    assert footprint("silo") == footprint("kenn") == (1, 1)


def test_a_building_that_is_not_known_is_given_the_size_of_the_largest():
    assert footprint("syrd") == footprint("mystery") == (3, 4) == max(placement.FOOTPRINTS.values())


def test_a_box_is_the_rectangle_from_a_building_s_top_left_cell():
    box = box_at("proc", (24, 48))

    assert box == Box(left=24, top=48, width=3, height=4)
    assert box.center == (25.0, 49.5) and box_at("pbox", (27, 48)).center == (27.0, 48.0)
    assert box.grown(1) == Box(left=23, top=47, width=5, height=6)


@pytest.mark.parametrize(
    "other, shared",
    [
        (Box(26, 51, 2, 2), True),
        (Box(27, 48, 2, 2), False),
        (Box(24, 52, 3, 1), False),
        (Box(22, 46, 2, 2), False),
        (Box(25, 49, 1, 1), True),
        (Box(20, 40, 20, 20), True),
    ],
    ids=["a shared corner cell", "next to it", "below it", "above and left", "inside it", "around it"],
)
def test_two_boxes_overlap_when_they_share_a_cell(other, shared):
    box = Box(24, 48, 3, 4)

    assert box.overlaps(other) is shared and other.overlaps(box) is shared


@pytest.mark.parametrize(
    "box, inside",
    [
        (Box(2, 2, 3, 4), True),
        (Box(95, 94, 3, 4), True),
        (Box(96, 94, 3, 4), False),
        (Box(1, 2, 3, 4), False),
        (Box(50, 95, 3, 4), False),
    ],
    ids=["the first corner", "the last corner", "over the right edge", "over the left edge", "over the lower edge"],
)
def test_a_box_is_inside_the_bounds_when_all_its_cells_are(box, inside):
    assert box.is_inside((2, 2, 96, 96)) is inside


def test_the_cells_of_a_box_are_listed_row_by_row():
    assert Box(24, 48, 3, 2).cells() == [(24, 48), (25, 48), (26, 48), (24, 49), (25, 49), (26, 49)]
    assert Box(24, 48, 3, 0).cells() == []


def test_a_box_holds_a_cell_when_one_of_its_cells_is_among_those_given():
    box = Box(24, 48, 3, 4)

    assert box.holds_any({(26, 51)}) and box.holds_any({(0, 0), (24, 48)})
    assert not box.holds_any({(27, 48), (24, 52), (23, 47)}) and not box.holds_any(set())


# -- the cells kept free ---------------------------------------------------------------------------


def test_two_rows_as_wide_as_the_building_are_kept_free_below_a_war_factory():
    world = world_with(YARD, ("weap", 21, 43))

    assert excluded_cells(world, NO_TERRAIN, TUNING) == {(20, 45), (21, 45), (22, 45), (20, 46), (21, 46), (22, 46)}


def test_the_straight_line_from_a_refinery_to_its_nearest_ore_patch_is_kept_free():
    world = world_with(YARD, ("proc", 27, 45))
    terrain = Terrain([HOME_PATCH, square_patch((74, 40))], HOME_ORE)

    lane = excluded_cells(world, terrain, TUNING)

    assert lane == set(cells.line((27, 46), (22, 40)))
    assert {(27, 46), (22, 40)} <= lane and len(lane) == 7


def test_without_ore_or_without_such_buildings_no_cell_is_kept_free():
    assert excluded_cells(world_with(YARD, ("proc", 26, 44)), NO_TERRAIN, TUNING) == set()
    assert excluded_cells(world_with(YARD, ("powr", 19, 48)), Terrain([HOME_PATCH]), TUNING) == set()


def test_each_war_factory_and_each_refinery_adds_its_own_cells():
    world = world_with(YARD, ("weap", 21, 43), ("weap", 31, 43), ("proc", 27, 45), ("proc", 19, 37))

    kept = excluded_cells(world, Terrain([HOME_PATCH]), TUNING)

    assert {(20, 45), (32, 46), (27, 46), (19, 38)} <= kept
    lanes = set(cells.line((27, 46), (22, 40))) | set(cells.line((19, 38), (22, 40)))
    assert len(kept) == 6 + 6 + len(lanes) and lanes <= kept


# -- candidates ------------------------------------------------------------------------------------


def gap_between(box: Box, other: Box) -> int:
    """How many free cells lie between two boxes that do not overlap, counted along the wider of the two gaps."""
    across = max(other.left - (box.left + box.width), box.left - (other.left + other.width))
    down = max(other.top - (box.top + box.height), box.top - (other.top + other.height))
    return max(across, down)


def test_every_candidate_keeps_a_gap_to_each_building_and_stands_next_to_one():
    world = world_with(YARD, ("powr", 19, 48), ("tent", 22, 44))

    candidates = candidate_cells("proc", world, NO_TERRAIN, TUNING)

    gaps = [[gap_between(box_at("proc", cell), standing) for standing in boxes_of(world)] for cell in candidates]
    assert len(candidates) > 20
    assert all(min(to_each) == 1 for to_each in gaps)


def test_candidates_lie_between_2_and_12_cells_from_the_construction_yard_nearest_first():
    world = world_with(YARD, ("powr", 19, 48), ("tent", 22, 44), ("proc", 26, 44), ("weap", 15, 48), ("powr", 26, 50))

    distances = [cells.distance(cell, (22, 48)) for cell in candidate_cells("dome", world, NO_TERRAIN, TUNING)]

    assert distances == sorted(distances) and 2 <= distances[0] and distances[-1] <= 12
    assert len(distances) > 30


def test_the_first_candidate_next_to_a_lone_construction_yard_is_the_nearest_cell_that_leaves_a_gap():
    world = world_with(YARD)

    assert candidate_cells("powr", world, NO_TERRAIN, TUNING)[:3] == [(25, 48), (25, 47), (25, 49)]
    assert candidate_cells("proc", world, NO_TERRAIN, TUNING)[0] == (25, 48)


def test_a_building_must_stand_within_2_cells_of_another_and_a_defense_within_7():
    world = world_with(YARD)

    farthest_building = max(
        gap_between(box_at("powr", cell), YARD_BOX) for cell in candidate_cells("powr", world, NO_TERRAIN, TUNING)
    )
    farthest_defense = max(
        gap_between(box_at("pbox", cell), YARD_BOX) for cell in candidate_cells("pbox", world, NO_TERRAIN, TUNING)
    )

    assert (farthest_building, farthest_defense) == (1, 6)


def test_no_candidate_covers_ground_that_cannot_be_built_on_or_a_cell_kept_free():
    world = world_with(YARD, ("weap", 20, 42), ("proc", 26, 44))
    terrain = Terrain([HOME_PATCH], HOME_ORE)
    unusable = excluded_cells(world, terrain, TUNING) | terrain.blocked

    candidates = candidate_cells("powr", world, terrain, TUNING)
    without_terrain = candidate_cells("powr", world, NO_TERRAIN, TUNING)

    assert candidates and not any(box_at("powr", cell).holds_any(unusable) for cell in candidates)
    assert set(candidates) < set(without_terrain)
    assert any(box_at("powr", cell).holds_any(unusable) for cell in without_terrain)


def test_a_building_is_placed_outside_a_block_of_structure_characters_next_to_the_base():
    # A 5 by 10 block of `#` east of the construction yard, over the cell a power plant would go to without it.
    structure = {(x, y) for x in range(24, 29) for y in range(44, 54)}
    rows = ["".join("#" if (x, y) in structure else "." for x in range(96)) for y in range(96)]
    maps = {"rules": {"maps": {"arena": {"layout": {"origin": [0, 0], "rows": rows}}}}}
    terrain = Terrain.from_game_config(maps, "arena")
    world = world_with(YARD)

    chosen = preferred_cell("powr", world, terrain, TUNING)

    assert box_at("powr", preferred_cell("powr", world, NO_TERRAIN, TUNING)).holds_any(structure)
    assert chosen is not None and not box_at("powr", chosen).holds_any(structure)
    candidates = candidate_cells("powr", world, terrain, TUNING)
    assert candidates and not any(box_at("powr", cell).holds_any(structure) for cell in candidates)


def test_candidates_keep_clear_of_a_building_that_was_asked_for_and_does_not_show_yet():
    world = world_with(YARD)
    promised = box_at("powr", (19, 48))

    candidates = candidate_cells("tent", world, NO_TERRAIN, TUNING, promised=[promised])

    assert (19, 48) not in candidates and candidates
    assert all(gap_between(box_at("tent", cell), promised) >= 1 for cell in candidates)


def exit_rows(cell: tuple[int, int]) -> Box:
    """The two rows below a war factory with its top-left corner on a cell."""
    return Box(cell[0], cell[1] + 3, 3, 2)


def has_blocked_exit(cell: tuple[int, int], taken: list[Box], unbuildable: frozenset) -> bool:
    rows = exit_rows(cell)
    return rows.holds_any(unbuildable) or any(rows.overlaps(box) for box in taken)


def with_a_blocked_exit(places: list[tuple[int, int]], world: World, terrain: Terrain) -> set[tuple[int, int]]:
    """The places at which a war factory would have a building, its gap or unbuildable ground below it."""
    taken = [box.grown(1) for box in boxes_of(world)]
    return {cell for cell in places if has_blocked_exit(cell, taken, terrain.blocked)}


def test_a_war_factory_is_asked_only_where_the_two_rows_below_it_are_free():
    world = world_with(YARD, ("powr", 19, 48), ("tent", 22, 44), ("pbox", 27, 48))
    terrain = Terrain([HOME_PATCH], HOME_ORE)

    for_a_war_factory = candidate_cells("weap", world, terrain, TUNING)
    for_a_depot = candidate_cells("fix", world, terrain, TUNING)

    blocked = with_a_blocked_exit(for_a_depot, world, terrain)
    assert for_a_war_factory == [cell for cell in for_a_depot if cell not in blocked]
    assert len(blocked) > 5 and len(for_a_war_factory) > 5


def test_every_candidate_lies_inside_the_playable_map():
    bounds = (2, 2, 30, 60)
    world = world_with(("fact", 27, 4), bounds=bounds)

    candidates = candidate_cells("proc", world, NO_TERRAIN, TUNING)

    assert candidates and all(box_at("proc", cell).is_inside(bounds) for cell in candidates)
    assert all(cell[0] + 3 <= 32 and cell[1] >= 2 for cell in candidates)


def test_without_a_construction_yard_the_base_s_center_is_what_the_rings_are_laid_around():
    world = world_with(("powr", 40, 40), ("tent", 44, 40), base_center=(42, 40))

    candidates = candidate_cells("powr", world, NO_TERRAIN, TUNING)

    assert candidates and candidates[0] == min(candidates, key=lambda cell: cells.distance(cell, (42, 40)))


@pytest.mark.parametrize(
    "world",
    [world_with(), world_with(YARD, bounds=None), world_with(("fact", 22, 48), bounds=(60, 60, 20, 20))],
    ids=["no building and no base", "no bounds", "a base outside the bounds"],
)
def test_there_is_no_candidate_without_a_base_or_a_map_to_place_on(world):
    assert candidate_cells("powr", world, NO_TERRAIN, TUNING) == []
    assert preferred_cell("powr", world, NO_TERRAIN, TUNING) is None


# -- preferences -----------------------------------------------------------------------------------


def test_a_building_without_a_preference_goes_to_the_candidate_nearest_the_construction_yard():
    world = world_with(YARD, ("powr", 19, 48))

    assert preferred_cell("tent", world, NO_TERRAIN, TUNING) == candidate_cells("tent", world, NO_TERRAIN, TUNING)[0]
    assert preferred_cell("tent", world, NO_TERRAIN, TUNING) == (25, 48)


def test_the_first_refinery_goes_to_the_candidate_nearest_the_closest_ore_patch():
    world = world_with(YARD, ("powr", 20, 49), ("tent", 23, 45))
    terrain = Terrain([HOME_PATCH, square_patch((74, 40))], HOME_ORE)

    chosen = preferred_cell("proc", world, terrain, TUNING)

    candidates = candidate_cells("proc", world, terrain, TUNING)
    nearest = min(cells.distance(cells.nearest_cell(*box_at("proc", cell).center), (22, 40)) for cell in candidates)
    assert chosen == (24, 39)
    assert cells.distance(cells.nearest_cell(*box_at("proc", chosen).center), (22, 40)) == nearest < 4
    assert not box_at("proc", chosen).holds_any(set(HOME_ORE))


def test_a_second_refinery_goes_toward_a_patch_in_reach_that_no_refinery_serves():
    world = world_with(YARD, ("powr", 19, 48), ("tent", 22, 44), ("proc", 24, 39), ("powr", 26, 48))
    second_patch = OrePatch(center=(34, 52), cells=frozenset((x, y) for x in range(33, 37) for y in (51, 52, 53)))
    terrain = Terrain([HOME_PATCH, second_patch], HOME_ORE)

    chosen = preferred_cell("proc", world, terrain, TUNING)

    candidates = candidate_cells("proc", world, terrain, TUNING)
    assert chosen == min(candidates, key=lambda cell: cells.distance(box_at("proc", cell).center, (34, 52)))
    assert cells.distance(chosen, (34, 52)) < cells.distance(chosen, (22, 40))


def test_with_every_patch_in_reach_served_a_second_refinery_goes_to_the_nearest_patch_again():
    world = world_with(YARD, ("powr", 19, 48), ("tent", 22, 44), ("proc", 24, 39))
    terrain = Terrain([HOME_PATCH, square_patch((74, 40))], HOME_ORE)

    chosen = preferred_cell("proc", world, terrain, TUNING)

    assert cells.distance(box_at("proc", chosen).center, (22, 40)) < 8
    assert not box_at("proc", chosen).holds_any(excluded_cells(world, terrain, TUNING) | terrain.blocked)


def test_a_refinery_with_no_ore_patch_in_reach_goes_to_the_candidate_nearest_the_construction_yard():
    world = world_with(YARD, ("powr", 19, 48))
    far = Terrain([square_patch((22, 27))])

    assert preferred_cell("proc", world, far, TUNING) == candidate_cells("proc", world, far, TUNING)[0]
    assert preferred_cell("proc", world, NO_TERRAIN, TUNING) == candidate_cells("proc", world, NO_TERRAIN, TUNING)[0]


@pytest.mark.parametrize(
    "enemy_base, expected",
    [((74, 48), (27, 48)), ((22, 8), (22, 43)), ((2, 48), (17, 48))],
    ids=["enemy to the east", "enemy to the north", "enemy to the west"],
)
def test_a_defense_goes_to_the_point_5_cells_from_the_construction_yard_toward_the_enemy(enemy_base, expected):
    world = world_with(YARD, enemy_base=enemy_base)

    chosen = preferred_cell("pbox", world, NO_TERRAIN, TUNING)

    assert chosen == expected and cells.is_within(chosen, (22, 48), 6)


def test_a_defense_stays_within_6_cells_of_the_construction_yard_when_its_point_is_taken():
    world = world_with(YARD, ("powr", 26, 48), ("pbox", 27, 46), enemy_base=(74, 48))

    chosen = preferred_cell("ftur", world, NO_TERRAIN, TUNING)

    within = [cell for cell in candidate_cells("ftur", world, NO_TERRAIN, TUNING) if cells.is_within(cell, (22, 48), 6)]
    assert chosen == min(within, key=lambda cell: cells.distance(cell, (27, 48)))
    assert cells.is_within(chosen, (22, 48), 6) and chosen != (27, 48)


def test_a_defense_goes_to_the_candidate_nearest_the_construction_yard_while_the_enemy_s_start_is_unknown():
    world = world_with(YARD, enemy_base=None)

    assert preferred_cell("gun", world, NO_TERRAIN, TUNING) == candidate_cells("gun", world, NO_TERRAIN, TUNING)[0]


def test_the_distances_follow_the_tuning():
    world = world_with(YARD)
    tuning = placement_off(ring_min_cells=4, ring_max_cells=5, defense_distance_cells=4, defense_radius_cells=4)

    distances = [cells.distance(cell, (22, 48)) for cell in candidate_cells("pbox", world, NO_TERRAIN, tuning)]

    assert min(distances) == 4 and max(distances) == 5
    assert preferred_cell("pbox", world, NO_TERRAIN, tuning) == (26, 48)


# -- AA defenses -----------------------------------------------------------------------------------

# A refinery west of the construction yard and one east of it, whose middle cells are (15, 48) and (31, 48), as the
# state reports them.
WEST_REFINERY, EAST_REFINERY = ("proc", 15, 47), ("proc", 31, 47)
WEST_MIDDLE, EAST_MIDDLE = (15, 48), (31, 48)
AA_REACH = 6


def two_refineries(enemy_base=(74, 48)) -> World:
    return world_with(YARD, WEST_REFINERY, EAST_REFINERY, enemy_base=enemy_base)


@pytest.mark.parametrize("item", ["agun", "sam"])
@pytest.mark.parametrize(
    "enemy_base, exposed, other",
    [((74, 48), EAST_MIDDLE, WEST_MIDDLE), ((2, 48), WEST_MIDDLE, EAST_MIDDLE)],
    ids=["enemy to the east", "enemy to the west"],
)
def test_air_an_aa_defense_goes_to_the_free_cell_nearest_the_refinery_nearest_the_enemy_start(
    item, enemy_base, exposed, other
):
    world = two_refineries(enemy_base)

    chosen = preferred_cell(item, world, NO_TERRAIN, TUNING, aa_refinery_cells=AA_REACH)

    candidates = candidate_cells(item, world, NO_TERRAIN, TUNING)
    nearest = min(cells.distance(box_at(item, cell).center, exposed) for cell in candidates)
    assert (
        cells.is_within(chosen, exposed, AA_REACH) and cells.distance(box_at(item, chosen).center, exposed) == nearest
    )
    assert cells.distance(chosen, exposed) < cells.distance(chosen, other)
    assert not any(box_at(item, chosen).overlaps(box.grown(TUNING.gap_cells)) for box in boxes_of(world))


def test_air_the_aa_defense_s_cell_differs_from_the_defense_point_in_front_of_the_base():
    world = two_refineries(enemy_base=(22, 8))

    near_refinery = preferred_cell("agun", world, NO_TERRAIN, TUNING, aa_refinery_cells=AA_REACH)
    as_a_defense = preferred_cell("agun", world, NO_TERRAIN, TUNING)

    # With the enemy to the north both refineries are as far from it; the first found, the west one, is taken.
    assert as_a_defense == preferred_cell("pbox", world, NO_TERRAIN, TUNING) == (22, 43)
    assert cells.is_within(near_refinery, WEST_MIDDLE, AA_REACH) and near_refinery != as_a_defense


@pytest.mark.parametrize(
    "world, reach",
    [
        (world_with(YARD, enemy_base=(74, 48)), AA_REACH),
        (two_refineries(enemy_base=None), AA_REACH),
        (two_refineries(), 1),
    ],
    ids=["no refinery", "enemy start unknown", "no free cell within 1 of the refinery"],
)
def test_air_without_a_free_cell_by_the_exposed_refinery_an_aa_defense_is_placed_as_any_other_defense(world, reach):
    chosen = preferred_cell("agun", world, NO_TERRAIN, TUNING, aa_refinery_cells=reach)

    assert (
        chosen == preferred_cell("agun", world, NO_TERRAIN, TUNING) == preferred_cell("pbox", world, NO_TERRAIN, TUNING)
    )


def test_air_without_the_aa_reach_an_aa_defense_is_placed_as_any_other_defense_and_other_defenses_ignore_the_reach():
    world = two_refineries()

    assert preferred_cell("agun", world, NO_TERRAIN, TUNING) == preferred_cell("pbox", world, NO_TERRAIN, TUNING)
    assert preferred_cell("pbox", world, NO_TERRAIN, TUNING, aa_refinery_cells=AA_REACH) == preferred_cell(
        "pbox", world, NO_TERRAIN, TUNING
    )


@pytest.mark.parametrize("reach", [3, 4, 9])
def test_air_the_aa_defense_s_cell_is_within_the_reach_given(reach):
    chosen = preferred_cell("sam", two_refineries(), NO_TERRAIN, TUNING, aa_refinery_cells=reach)

    assert cells.is_within(chosen, EAST_MIDDLE, reach)


# -- the field a refinery serves, chosen by reach ----------------------------------------------------------------

BY_REACH = dataclasses.replace(TUNING, refinery_field_by_reach=True)
POWER = ("powr", 20, 49)
BARRACKS = ("tent", 23, 45)


def rectangle(left: int, top: int, width: int, height: int) -> frozenset[tuple[int, int]]:
    """The cells of a rectangle from its top-left cell."""
    return frozenset((x, y) for y in range(top, top + height) for x in range(left, left + width))


def field_of(field: frozenset[tuple[int, int]]) -> OrePatch:
    """The ore patch of a field of cells, its center the middle of them as the terrain reads it."""
    middle_x = sum(x for x, _ in field) / len(field)
    middle_y = sum(y for _, y in field) / len(field)
    return OrePatch(center=cells.nearest_cell(middle_x, middle_y), cells=field)


def terrain_of(*patches: OrePatch) -> Terrain:
    """A terrain of these ore patches, their ore the only ground nothing can be built on."""
    return Terrain(patches, frozenset().union(*(patch.cells for patch in patches)))


# A field of 9 cells east of the yard, reached by a refinery east of it.
EAST_FIELD = field_of(rectangle(29, 51, 3, 3))
# A field of 40 cells north of the yard, served by a refinery standing west of it, and a second refinery east of it.
NORTH_FIELD = field_of(rectangle(19, 36, 8, 5))
WEST_OF_NORTH, EAST_OF_NORTH = ("proc", 16, 38), ("proc", 28, 38)
# The same field with 20 cells.
SMALL_NORTH_FIELD = field_of(rectangle(19, 36, 5, 4))
# A field of 9 cells south of the yard: in ore reach, but 6 cells from the nearest refinery candidate.
SOUTH_FIELD = field_of(rectangle(21, 61, 3, 3))
# A field of 153 cells east of the yard, its center farther than the south field's, its west edge reached.
WIDE_EAST_FIELD = field_of(rectangle(29, 44, 17, 9))


def refinery_ore_distance(cell: tuple[int, int], patch: OrePatch) -> int:
    return ore_distance(box_at("proc", cell), patch)


def nearest_refinery_ore_distance(
    world: World, terrain: Terrain, patch: OrePatch, tuning: PlacementTuning = BY_REACH
) -> int:
    """How near a field's ore the nearest refinery candidate stands."""
    return min(refinery_ore_distance(cell, patch) for cell in candidate_cells("proc", world, terrain, tuning))


def test_field_a_box_reaches_a_field_within_the_reach_counted_as_a_king_moves():
    patch = field_of(rectangle(10, 10, 2, 2))

    assert [reaches(Box(13, 13, 3, 4), patch, reach) for reach in (1, 2)] == [False, True]
    assert reaches(Box(12, 6, 3, 4), patch, 1) and not reaches(Box(13, 6, 3, 4), patch, 1)
    assert reaches(Box(10, 10, 1, 1), patch, 0) and not reaches(Box(12, 10, 1, 1), patch, 0)


@pytest.mark.parametrize(
    "box, distance",
    [(Box(13, 13, 3, 4), 2), (Box(12, 6, 3, 4), 1), (Box(9, 9, 1, 1), 1), (Box(11, 9, 2, 3), 0), (Box(4, 14, 2, 2), 5)],
    ids=["diagonally two", "touching at a corner", "a cell at a corner", "covering ore", "below and to the left"],
)
def test_field_the_ore_distance_of_a_box_is_from_its_nearest_cell_to_the_nearest_ore_cell(box, distance):
    patch = field_of(rectangle(10, 10, 2, 2))

    assert ore_distance(box, patch) == distance
    assert reaches(box, patch, distance) and (distance == 0 or not reaches(box, patch, distance - 1))


def test_field_rule_1_with_two_fields_in_reach_the_refinery_stands_against_the_unserved_one_it_reaches():
    world = world_with(YARD, POWER, BARRACKS, ("proc", 24, 39))
    terrain = terrain_of(HOME_PATCH, EAST_FIELD)

    choice = choose_placement("proc", world, terrain, BY_REACH)

    assert (choice.field.patch, choice.field.rule, choice.field.served_by) == (EAST_FIELD, FIELD_UNSERVED_REACHED, 0)
    assert refinery_ore_distance(choice.cell, EAST_FIELD) <= BY_REACH.refinery_ore_cells
    assert choice.cell in candidate_cells("proc", world, terrain, BY_REACH)


def test_field_rule_1_takes_the_unserved_field_it_reaches_over_a_nearer_one_it_does_not():
    world = world_with(YARD, POWER)
    terrain = terrain_of(SOUTH_FIELD, WIDE_EAST_FIELD)

    by_reach = choose_placement("proc", world, terrain, BY_REACH)
    nearest = choose_placement("proc", world, terrain, TUNING)

    assert cells.distance(SOUTH_FIELD.center, (22, 48)) < cells.distance(WIDE_EAST_FIELD.center, (22, 48))
    assert nearest_refinery_ore_distance(world, terrain, SOUTH_FIELD) > BY_REACH.refinery_ore_cells
    assert (by_reach.field.patch, by_reach.field.rule) == (WIDE_EAST_FIELD, FIELD_UNSERVED_REACHED)
    assert refinery_ore_distance(by_reach.cell, WIDE_EAST_FIELD) <= BY_REACH.refinery_ore_cells
    assert nearest.field is None and cells.distance(nearest.cell, SOUTH_FIELD.center) < 11


def test_field_rule_2_with_the_unserved_field_out_of_reach_the_refinery_shares_the_served_field_of_40_cells():
    world = world_with(YARD, POWER, BARRACKS, WEST_OF_NORTH)
    terrain = terrain_of(NORTH_FIELD, SOUTH_FIELD)

    choice = choose_placement("proc", world, terrain, BY_REACH)

    assert NORTH_FIELD.size == 40 and nearest_refinery_ore_distance(world, terrain, SOUTH_FIELD) == 6
    assert (choice.field.patch, choice.field.rule, choice.field.served_by) == (NORTH_FIELD, FIELD_SHARED_REACHED, 1)
    assert refinery_ore_distance(choice.cell, NORTH_FIELD) <= BY_REACH.refinery_ore_cells
    # South of the field, where the refinery that serves it stands west of it.
    assert box_at("proc", choice.cell).top > max(y for _, y in NORTH_FIELD.cells)
    assert box_at("proc", WEST_OF_NORTH[1:]).left + 3 <= min(x for x, _ in NORTH_FIELD.cells)


@pytest.mark.parametrize(
    "buildings, north",
    [((WEST_OF_NORTH, EAST_OF_NORTH), NORTH_FIELD), ((WEST_OF_NORTH,), SMALL_NORTH_FIELD)],
    ids=["served by 2 refineries already", "of 20 cells"],
)
def test_field_rule_3_with_the_served_field_full_or_too_small_the_refinery_goes_toward_the_unserved_field(
    buildings, north
):
    world = world_with(YARD, POWER, BARRACKS, *buildings)
    terrain = terrain_of(north, SOUTH_FIELD)

    choice = choose_placement("proc", world, terrain, BY_REACH)

    candidates = candidate_cells("proc", world, terrain, BY_REACH)
    goal = SOUTH_FIELD.nearest_cell((22, 48))
    assert (choice.field.patch, choice.field.rule) == (SOUTH_FIELD, FIELD_UNSERVED_NEAREST)
    assert choice.cell == min(candidates, key=lambda cell: cells.distance(box_at("proc", cell).center, goal))
    assert refinery_ore_distance(choice.cell, SOUTH_FIELD) < refinery_ore_distance(candidates[0], SOUTH_FIELD)


def test_field_rule_4_with_every_field_in_reach_served_a_refinery_goes_to_the_nearest_that_takes_one_more():
    world = world_with(YARD, POWER, BARRACKS, ("proc", 24, 39))

    choice = choose_placement("proc", world, terrain_of(HOME_PATCH, square_patch((74, 40))), BY_REACH)

    assert (choice.field.patch, choice.field.rule, choice.field.served_by) == (HOME_PATCH, FIELD_NEAREST_UNDER_CAP, 1)
    assert refinery_ore_distance(choice.cell, HOME_PATCH) <= BY_REACH.refinery_ore_cells


def test_field_a_field_that_serves_as_many_refineries_as_a_field_takes_is_never_chosen_again():
    world = world_with(YARD, POWER, BARRACKS, ("proc", 24, 39), ("proc", 18, 38))
    terrain = terrain_of(HOME_PATCH, square_patch((74, 40)))

    choice = choose_placement("proc", world, terrain, BY_REACH)

    assert choice == placement.PlacementChoice(candidate_cells("proc", world, terrain, BY_REACH)[0])


def test_field_no_field_beyond_the_ore_reach_is_ever_chosen():
    world = world_with(YARD, POWER)
    far = terrain_of(square_patch((22, 27)))

    choice = choose_placement("proc", world, far, BY_REACH)

    assert choice == placement.PlacementChoice(candidate_cells("proc", world, far, BY_REACH)[0])
    assert choose_placement("proc", world, NO_TERRAIN, BY_REACH).field is None


def test_field_the_refinery_goes_to_the_reaching_candidate_nearest_the_reachable_ore_cell_nearest_the_yard():
    world = world_with(YARD, POWER, BARRACKS)
    terrain = terrain_of(HOME_PATCH)

    choice = choose_placement("proc", world, terrain, BY_REACH)

    candidates = candidate_cells("proc", world, terrain, BY_REACH)
    reaching = [cell for cell in candidates if refinery_ore_distance(cell, HOME_PATCH) <= 2]
    goal = HOME_PATCH.nearest_cell((22, 48))
    assert goal == (22, 41)
    assert choice.cell == min(reaching, key=lambda cell: cells.distance(box_at("proc", cell).center, goal))


@pytest.mark.parametrize(
    "buildings, patch, distance",
    [((POWER,), HOME_PATCH, 1), ((POWER,), SOUTH_FIELD, 6), ((POWER, BARRACKS, WEST_OF_NORTH), NORTH_FIELD, 1)],
    ids=["the home field", "the south field", "a shared field"],
)
def test_field_refinery_reach_gives_the_fewest_cells_from_a_refinery_candidate_to_the_target_field_s_ore(
    buildings, patch, distance
):
    world, terrain = world_with(YARD, *buildings), terrain_of(patch)

    reach = placement.refinery_reach(world, terrain, TUNING)

    candidates = candidate_cells("proc", world, terrain, TUNING)
    assert min(refinery_ore_distance(cell, patch) for cell in candidates) == distance
    assert reach == placement.RefineryReach(patch, distance, distance <= TUNING.refinery_ore_cells)


def test_field_refinery_reach_is_none_without_a_base_or_a_field_and_has_no_distance_without_a_candidate():
    no_candidate = world_with(YARD, bounds=(20, 46, 6, 6))

    assert placement.refinery_reach(world_with(), terrain_of(HOME_PATCH), TUNING) is None
    assert placement.refinery_reach(world_with(YARD), terrain_of(square_patch((22, 27))), TUNING) is None
    assert placement.refinery_reach(no_candidate, terrain_of(HOME_PATCH), TUNING) == placement.RefineryReach(
        HOME_PATCH, None, False
    )


def todays_refinery_cell(world: World, terrain: Terrain) -> tuple[int, int]:
    """The cell a refinery was asked onto before the choice by reach: nearest the center of the nearest field in
    reach that no refinery serves, else of the nearest in reach; the candidate nearest the yard without a field."""
    yard = next((b.x, b.y) for b in world.buildings.values() if b.type == "fact")
    in_reach = [patch for patch in terrain.ore_patches if cells.distance(patch.center, yard) <= 20]
    middles = [box_at(b.type, (b.x, b.y)).center for b in world.buildings.values() if b.type == "proc"]
    served = {terrain.nearest_patch(cells.nearest_cell(*middle)) for middle in middles}
    fields = [patch for patch in in_reach if patch not in served] or in_reach
    candidates = candidate_cells("proc", world, terrain, TUNING)
    if not fields:
        return candidates[0]
    target = min(fields, key=lambda patch: cells.distance(patch.center, yard)).center
    return min(candidates, key=lambda cell: cells.distance(box_at("proc", cell).center, target))


@pytest.mark.parametrize(
    "buildings, patches",
    [
        ((POWER, BARRACKS, ("proc", 24, 39)), (HOME_PATCH, EAST_FIELD)),
        ((POWER,), (SOUTH_FIELD, WIDE_EAST_FIELD)),
        ((POWER, BARRACKS, WEST_OF_NORTH), (NORTH_FIELD, SOUTH_FIELD)),
        ((POWER, BARRACKS, WEST_OF_NORTH, EAST_OF_NORTH), (NORTH_FIELD, SOUTH_FIELD)),
        ((POWER, BARRACKS, ("proc", 24, 39)), (HOME_PATCH, square_patch((74, 40)))),
        ((POWER,), (square_patch((22, 27)),)),
    ],
    ids=["rule 1", "rule 1 over a nearer field", "rule 2", "rule 3", "every field served", "no field in reach"],
)
def test_field_with_the_switch_off_the_refinery_s_field_and_cell_are_today_s(buildings, patches):
    world = world_with(YARD, *buildings)
    terrain = terrain_of(*patches)

    choice = choose_placement("proc", world, terrain, TUNING)

    assert TUNING.refinery_field_by_reach is False
    assert choice == placement.PlacementChoice(todays_refinery_cell(world, terrain))
    assert preferred_cell("proc", world, terrain, TUNING) == choice.cell


@pytest.mark.parametrize("item", ["powr", "tent", "weap", "pbox"])
def test_field_the_choice_by_reach_changes_where_no_other_building_goes(item):
    world = world_with(YARD, POWER, BARRACKS, WEST_OF_NORTH)
    terrain = terrain_of(NORTH_FIELD, SOUTH_FIELD)

    assert choose_placement(item, world, terrain, BY_REACH) == choose_placement(item, world, terrain, TUNING)
    assert choose_placement(item, world, terrain, BY_REACH).field is None


def placing_agent(tuning: PlacementTuning, *buildings: tuple[str, int, int], terrain: Terrain):
    """An agent that plays with the upkeep manager alone and places under a tuning, having seen a first state of a
    base with these buildings on a map of this terrain."""
    agent = new_agent(UpkeepManager(), tuning=tuning_off(placement=tuning))
    situations.ask(agent, situations.base_state("england", *buildings, tick=FIRST_TICK))
    agent.ctx.terrain = terrain
    return agent


def with_refinery_ready(*buildings: tuple[str, int, int], tick: int) -> dict:
    """A state of a base with these buildings and a refinery waiting in the Building queue to be placed."""
    return situations.base_state(
        "england", *buildings, tick=tick, production=[situations.job("Building", "proc", ready=True)]
    )


FIRST_TICK = 995
SERVED_HOME = (YARD, POWER, BARRACKS, ("proc", 25, 40))


def test_field_one_refinery_field_event_is_written_as_a_refinery_is_first_asked_onto_the_map(match_record):
    agent = placing_agent(BY_REACH, *SERVED_HOME, terrain=terrain_of(HOME_PATCH, EAST_FIELD))

    asked = [
        situations.with_command(situations.ask(agent, with_refinery_ready(*SERVED_HOME, tick=tick)), "place")
        for tick in (1000, 1050)
    ]

    (event,) = match_record("refinery_field")
    # Asked again after 50 ticks, off the cell it was asked onto first, which is still kept for it.
    assert asked[0] == [{"cmd": "place", "item": "proc", "at": [25, 49]}]
    assert [order["item"] for order in asked[1]] == ["proc"]
    assert event == {
        "tick": 1000,
        "cell": [25, 49],
        "rule": FIELD_UNSERVED_REACHED,
        "field_center": [30, 52],
        "field_cells": 9,
        "served_by": 0,
        "ore_distance_cells": 2,
    }


def test_field_a_refinery_with_no_field_to_serve_writes_its_event_with_the_field_s_entries_null(match_record):
    standing = (*SERVED_HOME, ("proc", 18, 38))
    agent = placing_agent(BY_REACH, *standing, terrain=terrain_of(HOME_PATCH))

    (order,) = situations.with_command(situations.ask(agent, with_refinery_ready(*standing, tick=1000)), "place")

    (event,) = match_record("refinery_field")
    assert event == {
        "tick": 1000,
        "cell": order["at"],
        "rule": None,
        "field_center": None,
        "field_cells": None,
        "served_by": None,
        "ore_distance_cells": None,
    }


def test_field_with_the_switch_off_no_refinery_field_event_is_written_and_the_cell_is_today_s(match_record):
    terrain = terrain_of(HOME_PATCH, EAST_FIELD)
    agent = placing_agent(TUNING, *SERVED_HOME, terrain=terrain)

    (order,) = situations.with_command(situations.ask(agent, with_refinery_ready(*SERVED_HOME, tick=1000)), "place")

    assert match_record("refinery_field") == []
    assert order["at"] == list(todays_refinery_cell(world_with(*SERVED_HOME), terrain))


@pytest.fixture(scope="module")
def match_by_reach() -> WholeMatch:
    """A whole fake match played with the refinery's field chosen by reach."""
    return play_whole_match(tuning_off(placement=BY_REACH))


def test_field_a_whole_fake_match_with_the_switch_on_runs_without_an_error_within_the_compute_budget(match_by_reach):
    fields = match_by_reach.data_of("refinery_field")
    refineries = [b for b in match_by_reach.game.own_buildings() if b["type"] == "proc"]

    assert len(match_by_reach.compute_ms) == WHOLE_MATCH_STEPS and match_by_reach.data_of("error") == []
    assert match_by_reach.compute_p95_ms <= COMPUTE_P95_MS
    assert len(fields) == len(refineries) >= 2
    assert {field["rule"] for field in fields} <= {1, 2, 3, 4, None} and fields[0]["rule"] == FIELD_UNSERVED_REACHED


# -- the creep toward the ore of a refinery that is due ------------------------------------------------------------

CREEP = dataclasses.replace(TUNING, creep_toward_ore=True)
# A field 19 cells south of the yard, 5 rows below the reach of the refinery candidates of a lone yard ...
CREEP_FIELD = SOUTH_FIELD
# ... and one 20 cells south, where no refinery can stand against it within the ring around the yard.
FAR_SOUTH_FIELD = field_of(rectangle(21, 67, 3, 3))
# The ore cells of the two fields nearest the yard.
CREEP_ORE, FAR_SOUTH_ORE = (22, 61), (22, 67)


def middle_distance(item: str, cell: tuple[int, int], ore: tuple[int, int]) -> float:
    """How far the middle of a building of a type with its top-left corner on a cell lies from an ore cell."""
    return cells.distance(box_at(item, cell).center, ore)


def test_creep_a_power_plant_goes_to_the_candidate_nearest_the_field_s_ore_while_no_refinery_reaches_it():
    world = world_with(YARD, POWER)
    terrain = terrain_of(CREEP_FIELD)

    choice = choose_placement("powr", world, terrain, CREEP, toward_ore=True)

    candidates = candidate_cells("powr", world, terrain, CREEP)
    assert nearest_refinery_ore_distance(world, terrain, CREEP_FIELD) > CREEP.refinery_ore_cells
    assert CREEP_FIELD.nearest_cell((22, 48)) == CREEP_ORE
    assert choice.cell == min(candidates, key=lambda cell: middle_distance("powr", cell, CREEP_ORE)) == (22, 52)
    assert choice.cell != candidates[0] == preferred_cell("powr", world, terrain, TUNING)
    assert choice.creep == placement.Creep(
        CREEP_FIELD.center,
        CREEP_ORE,
        middle_distance("powr", candidates[0], CREEP_ORE),
        middle_distance("powr", choice.cell, CREEP_ORE),
    )


def test_creep_of_candidates_as_near_the_ore_the_one_nearer_the_yard_is_taken():
    world = world_with(YARD)
    terrain = terrain_of(CREEP_FIELD)

    choice = choose_placement("powr", world, terrain, CREEP, toward_ore=True)

    candidates = candidate_cells("powr", world, terrain, CREEP)
    nearest = min(middle_distance("powr", cell, CREEP_ORE) for cell in candidates)
    tied = [cell for cell in candidates if middle_distance("powr", cell, CREEP_ORE) == nearest]
    assert len(tied) >= 2 and choice.cell == tied[0]
    assert cells.distance(tied[0], (22, 48)) <= cells.distance(tied[1], (22, 48))


# A ring wide enough for three power plants in a row to walk the base to the far south field.
WIDE_RING_CREEP = dataclasses.replace(CREEP, ring_max_cells=16)


def test_creep_a_chain_of_three_power_plants_walks_the_base_closer_to_the_ore_with_each():
    built = [YARD, POWER]
    terrain = terrain_of(FAR_SOUTH_FIELD)
    walked, reach_before = [], []

    for _ in range(3):
        world = world_with(*built)
        reach_before.append(nearest_refinery_ore_distance(world, terrain, FAR_SOUTH_FIELD, WIDE_RING_CREEP))
        choice = choose_placement("powr", world, terrain, WIDE_RING_CREEP, toward_ore=True)
        walked.append(choice.creep.distance_after)
        built.append(stands("powr", choice.cell))

    reached = world_with(*built)
    assert reach_before == [12, 8, 4] and walked == sorted(walked, reverse=True) and len(set(walked)) == 3
    assert nearest_refinery_ore_distance(reached, terrain, FAR_SOUTH_FIELD, WIDE_RING_CREEP) == 1
    refinery = choose_placement("proc", reached, terrain, WIDE_RING_CREEP)
    assert refinery_ore_distance(refinery.cell, FAR_SOUTH_FIELD) <= WIDE_RING_CREEP.refinery_ore_cells


def test_creep_there_is_none_toward_a_field_no_free_place_in_the_ring_could_stand_a_refinery_against():
    world = world_with(YARD, POWER)
    terrain = terrain_of(FAR_SOUTH_FIELD)

    choice = choose_placement("powr", world, terrain, CREEP, toward_ore=True)

    assert cells.distance(FAR_SOUTH_FIELD.center, (22, 48)) <= CREEP.ore_reach_cells
    assert choice == placement.PlacementChoice(candidate_cells("powr", world, terrain, CREEP)[0])
    assert choose_placement("powr", world, terrain, WIDE_RING_CREEP, toward_ore=True).creep is not None


def test_creep_once_a_candidate_reaches_the_field_the_refinery_stands_against_it_and_power_goes_by_the_yard_again():
    terrain = terrain_of(CREEP_FIELD)
    crept = choose_placement("powr", world_with(YARD, POWER), terrain, CREEP, toward_ore=True)
    reached = world_with(YARD, POWER, stands("powr", crept.cell))

    refinery = choose_placement("proc", reached, terrain, CREEP)
    after = world_with(YARD, POWER, stands("powr", crept.cell), stands("proc", refinery.cell))
    next_power = choose_placement("powr", after, terrain, CREEP, toward_ore=True)

    assert refinery_ore_distance(refinery.cell, CREEP_FIELD) <= CREEP.refinery_ore_cells
    assert choose_placement("powr", reached, terrain, CREEP, toward_ore=True).creep is None
    assert next_power == placement.PlacementChoice(candidate_cells("powr", after, terrain, CREEP)[0])


def test_creep_a_refinery_takes_the_reaching_candidate_nearest_the_field_s_center():
    world = world_with(YARD, POWER, BARRACKS)
    terrain = terrain_of(HOME_PATCH)

    choice = choose_placement("proc", world, terrain, CREEP)

    reaching = [
        cell for cell in candidate_cells("proc", world, terrain, CREEP) if refinery_ore_distance(cell, HOME_PATCH) <= 2
    ]
    assert choice == placement.PlacementChoice(min(reaching, key=lambda cell: middle_distance("proc", cell, (22, 40))))


@pytest.mark.parametrize("item", ["pbox", "gun", "agun", "sam"])
@pytest.mark.parametrize("aa_reach", [None, 6], ids=["no air seen", "air seen"])
def test_creep_defenses_are_placed_exactly_as_with_the_switch_off(item, aa_reach):
    world = world_with(YARD, POWER, ("proc", 24, 39))
    terrain = terrain_of(HOME_PATCH, CREEP_FIELD)

    crept = choose_placement(item, world, terrain, CREEP, aa_refinery_cells=aa_reach, toward_ore=True)

    assert crept == choose_placement(item, world, terrain, TUNING, aa_refinery_cells=aa_reach)


@pytest.mark.parametrize("item", ["powr", "tent", "weap", "fix", "dome", "proc"])
def test_creep_with_no_refinery_due_or_no_field_in_reach_every_building_goes_where_it_did(item):
    world = world_with(YARD, POWER)

    not_due = choose_placement(item, world, terrain_of(CREEP_FIELD), CREEP)
    no_field = choose_placement(item, world, terrain_of(square_patch((22, 27))), CREEP, toward_ore=True)

    assert not_due == choose_placement(item, world, terrain_of(CREEP_FIELD), TUNING)
    assert no_field == choose_placement(item, world, terrain_of(square_patch((22, 27))), TUNING)


def test_creep_with_the_field_chosen_by_reach_the_base_creeps_toward_that_field_and_not_without_one():
    both = dataclasses.replace(CREEP, refinery_field_by_reach=True)
    at_the_cap = world_with(YARD, POWER, BARRACKS, ("proc", 24, 39), ("proc", 18, 38))

    toward_south = choose_placement("powr", world_with(YARD, POWER), terrain_of(CREEP_FIELD), both, toward_ore=True)
    no_field = choose_placement("powr", at_the_cap, terrain_of(HOME_PATCH), both, toward_ore=True)

    assert toward_south == choose_placement(
        "powr", world_with(YARD, POWER), terrain_of(CREEP_FIELD), CREEP, toward_ore=True
    )
    assert toward_south.creep is not None and toward_south.creep.field_center == CREEP_FIELD.center
    assert no_field == placement.PlacementChoice(candidate_cells("powr", at_the_cap, terrain_of(HOME_PATCH), both)[0])


def creep_agent(tuning: PlacementTuning, *managers, state: dict, terrain: Terrain):
    """An agent that plays with these managers and places under a tuning, having seen a first state, on a map of
    this terrain."""
    agent = new_agent(*managers, tuning=tuning_off(placement=tuning))
    situations.ask(agent, state)
    agent.ctx.terrain = terrain
    return agent


def england_base(*buildings, tick: int, production=(), **observation) -> dict:
    return situations.base_state("england", *buildings, tick=tick, production=list(production), **observation)


BARRACKS_QUEUED = situations.job("Building", "tent")
BARRACKS_READY = situations.job("Building", "tent", ready=True)


@pytest.mark.parametrize("tuning", [CREEP, TUNING], ids=["creep on", "creep off"])
def test_creep_while_the_opening_s_next_step_is_a_refinery_its_barracks_is_placed_toward_the_ore(tuning, match_record):
    first = england_base(YARD, POWER, tick=FIRST_TICK, production=[BARRACKS_QUEUED])
    agent = creep_agent(tuning, UpkeepManager(), OpeningManager(), state=first, terrain=terrain_of(CREEP_FIELD))

    asked = [
        situations.with_command(
            situations.ask(agent, england_base(YARD, POWER, tick=tick, production=[BARRACKS_READY])), "place"
        )
        for tick in (1000, 1050)
    ]

    world, terrain = world_with(YARD, POWER), terrain_of(CREEP_FIELD)
    crept = choose_placement("tent", world, terrain, CREEP, toward_ore=True)
    events = match_record("creep_placement")
    assert agent.ctx.opening_build.steps == ("powr", "tent", "proc", "proc", "powr", "weap")
    if tuning.creep_toward_ore:
        assert asked[0] == [{"cmd": "place", "item": "tent", "at": list(crept.cell)}]
        assert events == [
            {
                "tick": 1000,
                "building": "tent",
                "cell": list(crept.cell),
                "field_center": list(CREEP_FIELD.center),
                "ore_cell": list(CREEP_ORE),
                "distance_before_cells": round(crept.creep.distance_before, 2),
                "distance_after_cells": round(crept.creep.distance_after, 2),
            }
        ]
    else:
        assert asked[0] == [
            {"cmd": "place", "item": "tent", "at": list(preferred_cell("tent", world, terrain, TUNING))}
        ]
        assert events == []


# The base once the opening is complete: the two refineries serve the field north of the yard.
COMPLETE_BASE = (YARD, POWER, BARRACKS, ("proc", 25, 40), ("proc", 19, 40), ("powr", 27, 49), ("weap", 16, 49))
HARVESTERS = [situations.unit(300 + number, "harv", 22, 42) for number in range(4)]
POWER_READY = situations.job("Building", "powr", ready=True)


@pytest.mark.parametrize(
    "cash, crept", [(5000, True), (1000, False)], ids=["a third refinery called for", "too little money"]
)
def test_creep_while_upkeep_calls_for_a_third_refinery_a_power_plant_is_placed_toward_the_unserved_field(cash, crept):
    terrain = terrain_of(HOME_PATCH, CREEP_FIELD)
    state = england_base(*COMPLETE_BASE, tick=FIRST_TICK, cash=cash, units=HARVESTERS)
    agent = creep_agent(CREEP, UpkeepManager(), state=state, terrain=terrain)

    (order,) = situations.with_command(
        situations.ask(
            agent, england_base(*COMPLETE_BASE, tick=1000, cash=cash, units=HARVESTERS, production=[POWER_READY])
        ),
        "place",
    )

    world = world_with(*COMPLETE_BASE)
    toward = choose_placement("powr", world, terrain, CREEP, toward_ore=True)
    assert nearest_refinery_ore_distance(world, terrain, CREEP_FIELD) > CREEP.refinery_ore_cells
    assert toward.creep is not None
    assert order["at"] == list(toward.cell if crept else preferred_cell("powr", world, terrain, TUNING))


def with_own_ore_south(game_config: dict) -> dict:
    """A copy of the fake game's reference whose map pictures the own spawn's ore where the creep field lies, 13
    cells south of the spawn, in place of 8 cells north: in ore reach, out of reach of a refinery by the first
    buildings."""
    moved = copy.deepcopy(game_config)
    layout = moved["rules"]["maps"][MAP_ID]["layout"]
    rows = [list(row) for row in layout["rows"]]
    for x, y in HOME_ORE:
        rows[y][x] = "."
    for x, y in CREEP_FIELD.cells:
        rows[y][x] = "$"
    layout["rows"] = ["".join(row) for row in rows]
    return moved


def first_refinery_ore_distance(match: WholeMatch) -> int:
    """How many cells the first refinery of a match stands from the ore, counted as a king moves."""
    first = next(building for building in match.game.own_buildings() if building["type"] == "proc")
    return min(refinery_ore_distance((first["x"], first["y"]), patch) for patch in match.agent.ctx.terrain.ore_patches)


@pytest.fixture(scope="module")
def creep_matches() -> dict[bool, WholeMatch]:
    """Whole fake matches on a map whose own ore lies south of the spawn, with the creep toward ore on and off."""
    game_config = with_own_ore_south(FakeRedAlert().game_config)
    return {
        switch: play_whole_match(
            tuning_off(placement=dataclasses.replace(TUNING, creep_toward_ore=switch)),
            game_config=game_config,
            engine_reports=True,
        )
        for switch in (True, False)
    }


def test_creep_a_whole_fake_match_with_the_switch_on_runs_without_an_error_within_the_compute_budget(creep_matches):
    with_creep = creep_matches[True]

    assert len(with_creep.compute_ms) == WHOLE_MATCH_STEPS and with_creep.data_of("error") == []
    assert with_creep.compute_p95_ms <= COMPUTE_P95_MS


def test_creep_in_a_whole_fake_match_the_barracks_creeps_south_and_the_first_refinery_stands_against_the_ore(
    creep_matches,
):
    first_creep = creep_matches[True].data_of("creep_placement")[0]

    assert CREEP_FIELD in creep_matches[True].agent.ctx.terrain.ore_patches
    assert (first_creep["building"], first_creep["ore_cell"]) == ("tent", list(CREEP_ORE))
    assert first_creep["distance_after_cells"] < first_creep["distance_before_cells"]
    assert first_refinery_ore_distance(creep_matches[True]) <= CREEP.refinery_ore_cells
    assert first_refinery_ore_distance(creep_matches[False]) > CREEP.refinery_ore_cells
    assert creep_matches[False].data_of("creep_placement") == []


# -- the creep to the next field along the lane toward the enemy ---------------------------------------------------

NEXT_PATCH = dataclasses.replace(TUNING, creep_next_patch=True)
# Along the line from the yard (22, 48) to the enemy's start (74, 48): 52 cells, of which 0.6 is 31.2.
# A field 4 cells east of the home field, one beyond it in the lane, and the next one along.
BESIDE_HOME = square_patch((28, 40))
LANE_FIELD = square_patch((36, 46))
NEXT_LANE_FIELD = square_patch((44, 50))
# A field 20 cells off the line, and one that lies 15 cells from the enemy's start.
OFF_THE_LINE = square_patch((32, 28))
NEAR_THE_ENEMY = square_patch((54, 48))
HOME_REFINERIES = (("proc", 25, 40), ("proc", 19, 39))


def lane_world(*buildings, defenses=(), production=(), enemy_base=(74, 48), bounds=BOUNDS, tick=100) -> World:
    """A world of these own buildings at a tick, base_center the first one's cell, with remembered enemy defenses
    (type, x, y) and the own production queue given."""
    state = {
        "phase": "playing",
        "map": {"bounds": bounds},
        "observation": {
            "tick": tick,
            "buildings": [situations.building(100 + number, *spec) for number, spec in enumerate(buildings)],
            "base_center": list(buildings[0][1:]),
            "visible_enemy_buildings": [
                {"id": 900 + number, "type": kind, "x": x, "y": y, "hp": 100}
                for number, (kind, x, y) in enumerate(defenses)
            ],
            "production": list(production),
        },
        "legal_actions": {"enemy_base": list(enemy_base)},
    }
    world = World(ItemTable([]))
    world.update(state)
    return world


def target_of(world: World, terrain: Terrain, tuning: PlacementTuning = NEXT_PATCH):
    """The creep target a new lane keeper names for a world."""
    return creep.NextPatch().target(world, terrain, Production(world, OrderLedger()), tuning)


def test_lane_a_field_beside_the_home_field_is_never_the_target_and_the_nearest_one_beyond_it_is():
    world = lane_world(YARD, POWER, *HOME_REFINERIES)
    terrain = terrain_of(HOME_PATCH, BESIDE_HOME, NEXT_LANE_FIELD, LANE_FIELD)

    target = target_of(world, terrain)

    assert min(cells.distance(home, cell) for home in HOME_PATCH.cells for cell in BESIDE_HOME.cells) == 4
    assert BESIDE_HOME in creep.lane_fields(world, terrain, NEXT_PATCH)
    assert target == placement.CreepTarget(LANE_FIELD, 1)


def test_lane_a_field_20_cells_off_the_line_is_never_a_target():
    world = lane_world(YARD, POWER, *HOME_REFINERIES)
    terrain = terrain_of(HOME_PATCH, OFF_THE_LINE)

    assert OFF_THE_LINE not in creep.lane_fields(world, terrain, NEXT_PATCH)
    assert target_of(world, terrain) is None
    assert target_of(world, terrain_of(HOME_PATCH, OFF_THE_LINE, LANE_FIELD)).patch == LANE_FIELD


def test_lane_a_field_past_0_6_of_the_way_is_never_a_target():
    # The enemy's start 100 cells east: a field 64 cells along, 36 from it, is past 0.6 and clear of the start.
    far_enemy = (122, 48)
    beyond = square_patch((86, 48))
    world = lane_world(YARD, POWER, *HOME_REFINERIES, enemy_base=far_enemy, bounds=(0, 0, 160, 96))
    terrain = terrain_of(HOME_PATCH, beyond)

    assert cells.distance(beyond.center, far_enemy) > NEXT_PATCH.creep_enemy_clear_cells
    assert target_of(world, terrain) is None
    assert target_of(world, terrain, dataclasses.replace(NEXT_PATCH, creep_max_share=0.7)).patch == beyond


def test_lane_a_field_within_25_cells_of_the_enemy_s_start_is_never_a_target():
    world = lane_world(YARD, POWER, *HOME_REFINERIES)

    assert target_of(world, terrain_of(HOME_PATCH, NEAR_THE_ENEMY)) is None
    assert (
        target_of(
            world,
            terrain_of(HOME_PATCH, NEAR_THE_ENEMY),
            dataclasses.replace(NEXT_PATCH, creep_max_share=1.0, creep_enemy_clear_cells=14),
        )
        is not None
    )


@pytest.mark.parametrize("distance, is_target", [(10, False), (11, True)])
def test_lane_a_field_with_a_remembered_enemy_defense_within_10_cells_is_never_a_target(distance, is_target):
    # The defense stands south of the field's lowest row of cells.
    world = lane_world(YARD, POWER, *HOME_REFINERIES, defenses=[("gun", 36, 47 + distance)])
    terrain = terrain_of(HOME_PATCH, LANE_FIELD)

    assert (target_of(world, terrain) is not None) is is_target


def test_lane_after_the_target_gets_2_refineries_the_next_field_of_the_lane_is_the_target():
    terrain = terrain_of(HOME_PATCH, LANE_FIELD, NEXT_LANE_FIELD)
    keeper = creep.NextPatch()
    first = lane_world(YARD, POWER, *HOME_REFINERIES, ("proc", 32, 45))
    second = lane_world(YARD, POWER, *HOME_REFINERIES, ("proc", 32, 45), ("proc", 36, 41))

    targets = [keeper.target(world, terrain, Production(world, OrderLedger()), NEXT_PATCH) for world in (first, second)]

    served = [placement.patch_of(b, terrain) for b in second.buildings.values() if b.type == "proc"]
    assert served == [HOME_PATCH, HOME_PATCH, LANE_FIELD, LANE_FIELD]
    assert targets == [placement.CreepTarget(LANE_FIELD, 1), placement.CreepTarget(NEXT_LANE_FIELD, 2)]


def test_lane_there_is_no_target_before_the_home_field_has_2_refineries_standing_or_on_their_way():
    terrain = terrain_of(HOME_PATCH, LANE_FIELD)
    one = lane_world(YARD, POWER, ("proc", 24, 39))
    one_and_one_queued = lane_world(YARD, POWER, ("proc", 24, 39), production=[situations.job("Building", "proc")])

    assert target_of(one, terrain) is None
    assert target_of(one_and_one_queued, terrain) == placement.CreepTarget(LANE_FIELD, 1)


def test_lane_with_the_switch_off_there_is_never_a_target():
    world = lane_world(YARD, POWER, *HOME_REFINERIES)

    assert target_of(world, terrain_of(HOME_PATCH, LANE_FIELD), TUNING) is None


# A chain of power plants east of the yard, the last of them beyond the ring of 12 cells.
EAST_CHAIN = (("powr", 27, 48), ("powr", 31, 48), ("powr", 35, 48))
FAR_LANE_FIELD = square_patch((44, 46))


def test_next_patch_a_building_creeps_next_to_one_beyond_the_ring_nearer_the_target_than_any_ring_candidate():
    world = lane_world(YARD, POWER, *HOME_REFINERIES, *EAST_CHAIN)
    terrain = terrain_of(HOME_PATCH, FAR_LANE_FIELD)
    target = placement.CreepTarget(FAR_LANE_FIELD, 1)

    choice = choose_placement("powr", world, terrain, NEXT_PATCH, target=target)

    ore = FAR_LANE_FIELD.nearest_cell((22, 48))
    ring = candidate_cells("powr", world, terrain, NEXT_PATCH)
    box = box_at("powr", choice.cell)
    assert any(box.grown(NEXT_PATCH.adjacent_cells).overlaps(standing) for standing in boxes_of(world))
    assert middle_distance("powr", choice.cell, ore) < min(middle_distance("powr", cell, ore) for cell in ring)
    assert cells.distance(choice.cell, (22, 48)) > NEXT_PATCH.ring_max_cells
    assert choice.creep == placement.Creep(
        FAR_LANE_FIELD.center,
        ore,
        middle_distance("powr", ring[0], ore),
        middle_distance("powr", choice.cell, ore),
        lane_rank=1,
    )


def test_next_patch_a_building_creeping_never_covers_ore_a_refinery_s_lane_or_a_war_factory_s_exit():
    world = lane_world(YARD, POWER, *HOME_REFINERIES, *EAST_CHAIN, ("weap", 38, 44))
    terrain = terrain_of(HOME_PATCH, FAR_LANE_FIELD)

    choice = choose_placement("powr", world, terrain, NEXT_PATCH, target=placement.CreepTarget(FAR_LANE_FIELD, 1))

    kept = excluded_cells(world, terrain, NEXT_PATCH) | terrain.blocked
    assert choice.creep is not None and not box_at("powr", choice.cell).holds_any(kept)
    assert not any(box_at("powr", choice.cell).overlaps(box.grown(1)) for box in boxes_of(world))


def test_next_patch_the_third_refinery_stands_within_2_cells_of_the_target_s_ore():
    world = lane_world(YARD, POWER, *HOME_REFINERIES, ("powr", 26, 47), ("powr", 30, 47))
    terrain = terrain_of(HOME_PATCH, LANE_FIELD)

    choice = choose_placement("proc", world, terrain, NEXT_PATCH, target=placement.CreepTarget(LANE_FIELD, 1))

    assert refinery_ore_distance(choice.cell, LANE_FIELD) <= NEXT_PATCH.refinery_ore_cells
    assert placement.patch_of(lane_world(YARD, stands("proc", choice.cell)).buildings[101], terrain) == LANE_FIELD
    assert choice.creep is not None and choice.creep.lane_rank == 1


def test_next_patch_a_refinery_no_candidate_of_which_reaches_the_target_is_placed_as_without_one():
    world = lane_world(YARD, POWER, *HOME_REFINERIES)
    terrain = terrain_of(HOME_PATCH, FAR_LANE_FIELD)

    with_target = choose_placement("proc", world, terrain, NEXT_PATCH, target=placement.CreepTarget(FAR_LANE_FIELD, 1))

    assert with_target == choose_placement("proc", world, terrain, NEXT_PATCH)


@pytest.mark.parametrize("item", ["pbox", "gun", "agun"])
def test_next_patch_defenses_keep_their_own_goal(item):
    world = lane_world(YARD, POWER, *HOME_REFINERIES, *EAST_CHAIN)
    terrain = terrain_of(HOME_PATCH, FAR_LANE_FIELD)

    crept = choose_placement(item, world, terrain, NEXT_PATCH, target=placement.CreepTarget(FAR_LANE_FIELD, 1))

    assert crept == choose_placement(item, world, terrain, TUNING)


# The base once the opening is complete, its two refineries on the home field.
LANE_BASE = (YARD, POWER, BARRACKS, *HOME_REFINERIES, ("powr", 27, 49), ("weap", 16, 49))


def test_next_patch_after_the_opening_a_power_plant_creeps_toward_the_target_with_one_creep_placement_event(
    match_record,
):
    terrain = terrain_of(HOME_PATCH, LANE_FIELD)
    first = england_base(*LANE_BASE, tick=FIRST_TICK, cash=1000, units=HARVESTERS)
    agent = creep_agent(NEXT_PATCH, UpkeepManager(), state=first, terrain=terrain)

    (order,) = situations.with_command(
        situations.ask(
            agent, england_base(*LANE_BASE, tick=1000, cash=1000, units=HARVESTERS, production=[POWER_READY])
        ),
        "place",
    )

    world = lane_world(*LANE_BASE)
    toward = choose_placement("powr", world, terrain, NEXT_PATCH, target=agent.ctx.creep_target)
    (event,) = match_record("creep_placement")
    assert agent.ctx.creep_target == placement.CreepTarget(LANE_FIELD, 1)
    assert order["at"] == list(toward.cell) != list(preferred_cell("powr", world, terrain, TUNING))
    assert event == {
        "tick": 1000,
        "building": "powr",
        "cell": list(toward.cell),
        "field_center": list(LANE_FIELD.center),
        "ore_cell": list(toward.creep.ore_cell),
        "distance_before_cells": round(toward.creep.distance_before, 2),
        "distance_after_cells": round(toward.creep.distance_after, 2),
        "target_patch_center": list(LANE_FIELD.center),
        "lane_rank": 1,
        "reason": "next_patch",
    }


def test_next_patch_the_opening_s_steps_are_placed_as_with_the_switch_off(match_record):
    # Both refineries of the opening stand and its last steps are still to come: the target is known, and unused.
    terrain = terrain_of(HOME_PATCH, LANE_FIELD)
    opening_base = (YARD, POWER, BARRACKS, *HOME_REFINERIES)
    asked = {}
    for tuning in (NEXT_PATCH, TUNING):
        first = england_base(*opening_base, tick=FIRST_TICK, production=[situations.job("Building", "powr")])
        agent = creep_agent(tuning, UpkeepManager(), OpeningManager(), state=first, terrain=terrain)
        (asked[tuning.creep_next_patch],) = situations.with_command(
            situations.ask(agent, england_base(*opening_base, tick=1000, production=[POWER_READY])), "place"
        )
        assert agent.ctx.opening_build.steps

    assert asked[True] == asked[False]
    assert match_record("creep_placement") == []


@pytest.fixture(scope="module")
def next_patch_match() -> WholeMatch:
    """A whole fake match on a map with a field in the lane, played with the creep to the next field on."""
    return play_whole_match(
        tuning_off(placement=NEXT_PATCH), game_config=with_ore(FakeRedAlert().game_config, LANE_FIELD.cells)
    )


def test_next_patch_a_whole_fake_match_runs_without_an_error_and_its_refineries_stand_on_2_fields(next_patch_match):
    terrain = next_patch_match.agent.ctx.terrain
    refineries = [b for b in next_patch_match.agent.world.buildings.values() if b.type == "proc"]
    crept = next_patch_match.data_of("creep_placement")

    assert len(next_patch_match.compute_ms) == WHOLE_MATCH_STEPS and next_patch_match.data_of("error") == []
    assert next_patch_match.compute_p95_ms <= COMPUTE_P95_MS
    assert LANE_FIELD in terrain.ore_patches
    assert len({placement.patch_of(refinery, terrain) for refinery in refineries}) >= 2
    assert crept and {event["reason"] for event in crept} == {"next_patch"}


# -- two construction yards ---------------------------------------------------------------------------------------------

FAR_YARD = ("fact", 50, 48)
# A field by the far yard, out of the ore reach of the first one.
FAR_YARD_FIELD = square_patch((56, 43))


def test_two_yards_a_refinery_for_the_far_field_is_placed_by_the_far_yard_s_ring():
    world = world_with(YARD, POWER, *HOME_REFINERIES, FAR_YARD)
    terrain = terrain_of(HOME_PATCH, FAR_YARD_FIELD)

    choice = choose_placement("proc", world, terrain, TUNING)

    far_ring = candidate_cells("proc", world, terrain, TUNING, anchor=(50, 48))
    assert cells.distance(FAR_YARD_FIELD.center, (22, 48)) > TUNING.ore_reach_cells
    assert choice.cell in far_ring and cells.is_within(choice.cell, (50, 48), TUNING.ring_max_cells)
    assert choice.cell == min(far_ring, key=lambda cell: middle_distance("proc", cell, FAR_YARD_FIELD.center))
    assert placement.field_at(choice.cell, terrain) == FAR_YARD_FIELD


@pytest.mark.parametrize("item", ["powr", "tent", "pbox"])
def test_two_yards_a_building_without_a_field_or_a_target_is_placed_by_the_first_yard_as_before(item):
    two = world_with(YARD, POWER, *HOME_REFINERIES, FAR_YARD)
    terrain = terrain_of(HOME_PATCH, FAR_YARD_FIELD)

    chosen = choose_placement(item, two, terrain, TUNING).cell

    assert cells.is_within(chosen, (22, 48), TUNING.ring_max_cells)
    assert chosen in candidate_cells(item, two, terrain, TUNING, anchor=(22, 48))


def test_two_yards_a_building_creeping_toward_a_target_by_the_far_yard_is_placed_by_that_yard():
    world = world_with(YARD, POWER, *HOME_REFINERIES, FAR_YARD)
    terrain = terrain_of(HOME_PATCH, FAR_YARD_FIELD)

    choice = choose_placement("powr", world, terrain, NEXT_PATCH, target=placement.CreepTarget(FAR_YARD_FIELD, 1))

    assert cells.distance(choice.cell, (50, 48)) < cells.distance(choice.cell, (22, 48))
    assert choice.creep is not None and choice.creep.ore_cell == FAR_YARD_FIELD.nearest_cell((50, 48))


def test_two_yards_with_one_yard_the_anchor_is_the_yard_as_before():
    world = world_with(YARD, POWER, *HOME_REFINERIES)
    terrain = terrain_of(HOME_PATCH, FAR_YARD_FIELD)

    assert candidate_cells("proc", world, terrain, TUNING) == candidate_cells(
        "proc", world, terrain, TUNING, anchor=(22, 48)
    )


# -- the far field, walked toward in a straight line from the opening on ------------------------------------------------

FAR = dataclasses.replace(TUNING, creep_from_opening=True)
# The middle cell of the footprint of the yard reported at (22, 48), where the line toward the far field starts.
FAR_ORIGIN = (22, 49)
# A field 12 cells west of the yard, away from the enemy's start, and one 16 cells east of it, in the lane: no refinery
# by a lone yard can stand against either.
WEST_FIELD = square_patch((11, 50))
EAST_FAR_FIELD = square_patch((39, 50))
# Two power plants walking west from the yard, the second of them close enough to the west field for a refinery
# beside it to stand against the field.
WEST_WALK = (("powr", 20, 49), ("powr", 16, 49))


def test_far_field_a_field_12_cells_west_away_from_the_enemy_is_the_target_when_nearest_out_of_range():
    world = lane_world(YARD)
    terrain = terrain_of(HOME_PATCH, EAST_FAR_FIELD, WEST_FIELD)

    field = placement.far_field(world, terrain, FAR)

    assert (
        round(cells.distance(WEST_FIELD.center, FAR_ORIGIN)) == 11 < cells.distance(EAST_FAR_FIELD.center, FAR_ORIGIN)
    )
    assert nearest_refinery_ore_distance(world, terrain, WEST_FIELD, FAR) > FAR.refinery_ore_cells
    assert nearest_refinery_ore_distance(world, terrain, HOME_PATCH, FAR) <= FAR.refinery_ore_cells
    # There is no lane: the lane toward the enemy holds the east field and never the west one.
    lane = creep.lane_fields(world, terrain, FAR)
    assert EAST_FAR_FIELD in lane and WEST_FIELD not in lane
    assert field == placement.FarField(WEST_FIELD, FAR_ORIGIN, HOME_PATCH)


def test_far_field_once_a_refinery_can_stand_against_the_target_the_next_nearest_out_of_range_field_is_the_target():
    terrain = terrain_of(HOME_PATCH, EAST_FAR_FIELD, WEST_FIELD)
    before, after = lane_world(YARD), lane_world(YARD, *WEST_WALK)

    targets = [placement.far_field(world, terrain, FAR).patch for world in (before, after)]

    assert nearest_refinery_ore_distance(before, terrain, WEST_FIELD, FAR) > FAR.refinery_ore_cells
    assert nearest_refinery_ore_distance(after, terrain, WEST_FIELD, FAR) <= FAR.refinery_ore_cells
    assert targets == [WEST_FIELD, EAST_FAR_FIELD]


def test_far_field_the_target_is_recomputed_with_the_places_promised_to_buildings_kept_clear():
    terrain = terrain_of(HOME_PATCH, EAST_FAR_FIELD, WEST_FIELD)
    after = lane_world(YARD, *WEST_WALK)
    # Every place west of the second power plant from which a refinery would reach the west field, promised away.
    promised = [Box(5, 40, 9, 9), Box(5, 52, 9, 6)]

    assert placement.far_field(after, terrain, FAR).patch == EAST_FAR_FIELD
    assert placement.far_field(after, terrain, FAR, promised).patch == WEST_FIELD


def test_far_field_a_field_a_refinery_stands_against_is_never_the_target_though_no_free_place_is_left_by_it():
    world = world_with(YARD, ("proc", 24, 40))
    terrain = terrain_of(HOME_PATCH, WEST_FIELD)
    # Every free place by the home field, promised to buildings that do not show yet.
    walled = [Box(10, 30, 30, 17)]

    places = candidate_cells("proc", world, terrain, FAR, walled)
    assert ore_distance(box_at("proc", (24, 40)), HOME_PATCH) <= FAR.refinery_ore_cells
    assert not any(reaches(box_at("proc", cell), HOME_PATCH, FAR.refinery_ore_cells) for cell in places)
    assert placement.far_field(world, terrain, FAR, walled) == placement.FarField(WEST_FIELD, FAR_ORIGIN, HOME_PATCH)


@pytest.mark.parametrize("distance, is_target", [(10, False), (11, True)])
def test_far_field_a_field_with_a_remembered_enemy_defense_within_10_cells_is_skipped(distance, is_target):
    # The defense stands south of the west field's lowest row of cells.
    world = lane_world(YARD, defenses=[("gun", 11, 51 + distance)])
    terrain = terrain_of(HOME_PATCH, WEST_FIELD, EAST_FAR_FIELD)

    field = placement.far_field(world, terrain, FAR)

    assert field.patch == (WEST_FIELD if is_target else EAST_FAR_FIELD)


def test_far_field_with_the_enemy_s_start_unknown_a_field_is_the_target_by_the_other_conditions_alone():
    # A field 32 cells east of the yard's middle, its ore 30 from the yard, and 20 from the enemy's start.
    terrain = terrain_of(HOME_PATCH, NEAR_THE_ENEMY)
    known, unknown = world_with(YARD), world_with(YARD, enemy_base=None)

    assert unknown.enemy_base is None and ore_distance(YARD_BOX, NEAR_THE_ENEMY) == FAR.creep_far_max_cells
    assert placement.far_field(known, terrain, FAR) is None
    assert placement.far_field(unknown, terrain, FAR) == placement.FarField(NEAR_THE_ENEMY, FAR_ORIGIN, HOME_PATCH)
    assert placement.far_field(unknown, terrain_of(HOME_PATCH, NEAR_THE_ENEMY), FAR).patch == NEAR_THE_ENEMY


@pytest.mark.parametrize("center, is_target", [((23, 81), True), ((23, 82), False)], ids=["30 cells", "31 cells"])
def test_far_field_a_field_whose_ore_lies_farther_than_30_cells_from_the_yard_is_never_the_target(center, is_target):
    far = square_patch(center)
    assert ore_distance(YARD_BOX, far) == (30 if is_target else 31)

    field = placement.far_field(world_with(YARD), terrain_of(HOME_PATCH, far), FAR)

    assert (field == placement.FarField(far, FAR_ORIGIN, HOME_PATCH)) is is_target
    assert is_target or field is None


def test_far_field_there_is_none_with_only_the_home_field_or_without_a_base_and_no_home_without_a_reached_field():
    assert placement.far_field(world_with(YARD), terrain_of(HOME_PATCH), FAR) is None
    assert placement.far_field(world_with(), terrain_of(HOME_PATCH, WEST_FIELD), FAR) is None
    assert placement.far_field(world_with(YARD), terrain_of(WEST_FIELD), FAR) == placement.FarField(
        WEST_FIELD, FAR_ORIGIN, None
    )


# A field 15 cells east of the yard's middle, out of a refinery's reach by a lone yard.
EAST_15_FIELD = square_patch((38, 50))
# Beyond the field 9 cells east of the yard, which a refinery by a lone yard stands against: a field out of reach.
BEYOND_EAST_FIELD = square_patch((40, 52))


def along_and_off(item: str, cell: tuple[int, int], start: tuple[int, int], end: tuple[int, int]):
    """How far along the segment from `start` to `end` the middle of a building at `cell` lies, and how far from the
    segment, a middle beyond an end counting as at that end."""
    middle, length = box_at(item, cell).center, math.dist(start, end)
    projected = ((middle[0] - start[0]) * (end[0] - start[0]) + (middle[1] - start[1]) * (end[1] - start[1])) / length
    along = min(max(projected, 0.0), length)
    on_line = (start[0] + (end[0] - start[0]) * along / length, start[1] + (end[1] - start[1]) * along / length)
    return along, math.dist(middle, on_line)


def farthest_along_on_line(item: str, places: list, home: OrePatch, start: tuple[int, int], end: tuple[int, int]):
    """How far along the segment from `start` to `end` the farthest of the places lies whose middle is within the
    line's band, the places from which a refinery would stand against `home` left out."""
    valid = [cell for cell in places if not reaches(box_at("proc", cell), home, FAR.refinery_ore_cells)]
    along_off = [along_and_off(item, cell, start, end) for cell in valid]
    return max(along for along, off in along_off if off <= FAR.creep_line_cells)


def test_far_field_the_first_power_plant_stands_on_the_line_at_the_farthest_valid_cell_and_the_barracks_farther():
    terrain = terrain_of(HOME_PATCH, EAST_15_FIELD)
    lone = world_with(YARD)

    first = choose_placement("powr", lone, terrain, FAR)
    second = choose_placement("tent", world_with(YARD, stands("powr", first.cell)), terrain, FAR)

    ore = EAST_15_FIELD.nearest_cell(FAR_ORIGIN)
    ring = candidate_cells("powr", lone, terrain, FAR)
    first_along, first_off = along_and_off("powr", first.cell, FAR_ORIGIN, ore)
    second_along, second_off = along_and_off("tent", second.cell, FAR_ORIGIN, ore)
    assert round(cells.distance(EAST_15_FIELD.center, FAR_ORIGIN)) == 16
    assert ore == (37, 49)
    assert first.cell == (25, 48)
    assert first_off <= FAR.creep_line_cells
    assert first_along == farthest_along_on_line("powr", ring, HOME_PATCH, FAR_ORIGIN, ore)
    assert second.cell == (28, 48)
    assert second_off <= FAR.creep_line_cells
    assert second_along > first_along
    assert first.creep == placement.Creep(
        EAST_15_FIELD.center,
        ore,
        middle_distance("powr", ring[0], ore),
        middle_distance("powr", first.cell, ore),
        line_distance=first_off,
    )


def test_far_field_of_places_as_far_along_the_line_the_one_nearest_it_is_taken():
    terrain = terrain_of(HOME_PATCH, EAST_15_FIELD)
    ore = EAST_15_FIELD.nearest_cell(FAR_ORIGIN)

    choice = choose_placement("powr", world_with(YARD), terrain, FAR)

    # The places right of the yard, one row apart, all as far along the line, and each but one off it.
    as_far = [(25, top) for top in range(46, 51)]
    assert len({along_and_off("powr", cell, FAR_ORIGIN, ore)[0] for cell in as_far}) == 1
    assert choice.cell == min(as_far, key=lambda cell: along_and_off("powr", cell, FAR_ORIGIN, ore)[1])
    assert choice.creep.line_distance == 0


def test_far_field_the_line_comes_before_the_creep_toward_ore_and_the_creep_target():
    # The south field is the far field, 12 cells from the yard; the creep target is the field 15 cells east.
    world, terrain = world_with(YARD, POWER), terrain_of(HOME_PATCH, EAST_15_FIELD, SOUTH_FIELD)
    every_creep = dataclasses.replace(FAR, creep_toward_ore=True, creep_next_patch=True)
    target = placement.CreepTarget(EAST_15_FIELD, 1)

    gated = choose_placement("tent", world, terrain, every_creep, toward_ore=True, target=target)

    without_line = dataclasses.replace(every_creep, creep_from_opening=False)
    toward_target = choose_placement("tent", world, terrain, without_line, toward_ore=True, target=target)
    assert gated == choose_placement("tent", world, terrain, FAR)
    assert gated.creep.field_center == SOUTH_FIELD.center and gated.creep.line_distance is not None
    assert toward_target.creep.field_center == EAST_15_FIELD.center and gated.cell != toward_target.cell


def test_far_field_with_no_candidate_at_all_a_building_is_left_to_the_platform():
    no_room = world_with(YARD, bounds=(22, 48, 3, 4))

    assert placement.far_field(no_room, terrain_of(HOME_PATCH), FAR).patch == HOME_PATCH
    assert choose_placement("powr", no_room, terrain_of(HOME_PATCH), FAR) == placement.PlacementChoice(None)


@pytest.mark.parametrize("item", ["proc", "pbox", "gun", "ftur", "tsla", "agun", "sam"])
def test_far_field_defenses_and_refineries_are_placed_as_with_the_switch_off(item):
    world = world_with(YARD, *WEST_WALK)
    terrain = terrain_of(HOME_PATCH, WEST_FIELD, EAST_FAR_FIELD)

    assert placement.far_field(world, terrain, FAR) is not None
    for off in (TUNING, BY_REACH, CREEP):
        on = dataclasses.replace(off, creep_from_opening=True)
        assert choose_placement(item, world, terrain, on) == choose_placement(item, world, terrain, off)
        assert choose_placement(item, world, terrain, on, aa_refinery_cells=AA_REACH) == choose_placement(
            item, world, terrain, off, aa_refinery_cells=AA_REACH
        )


@pytest.mark.parametrize(
    "patches", [(HOME_PATCH,), (HOME_PATCH, square_patch((23, 91)))], ids=["only the home field", "41 cells away"]
)
@pytest.mark.parametrize("item", ["powr", "tent", "weap", "dome"])
def test_far_field_with_no_field_that_may_be_the_far_field_a_building_is_placed_as_with_the_switch_off(patches, item):
    world, terrain = world_with(YARD, POWER), terrain_of(*patches)
    target = placement.CreepTarget(HOME_PATCH, 1)
    lane_on = dataclasses.replace(NEXT_PATCH, creep_from_opening=True)

    assert placement.far_field(world, terrain, FAR) is None
    assert choose_placement(item, world, terrain, FAR) == choose_placement(item, world, terrain, TUNING)
    assert choose_placement(item, world, terrain, lane_on, target=target) == choose_placement(
        item, world, terrain, NEXT_PATCH, target=target
    )


def test_far_field_the_power_plant_and_barracks_never_take_a_place_a_refinery_by_the_home_field_would_need():
    terrain = terrain_of(EAST_FIELD, BEYOND_EAST_FIELD)
    lone = world_with(YARD)

    power = choose_placement("powr", lone, terrain, FAR)
    barracks = choose_placement("tent", world_with(YARD, stands("powr", power.cell)), terrain, FAR)

    ore = BEYOND_EAST_FIELD.nearest_cell(FAR_ORIGIN)
    ring = candidate_cells("powr", lone, terrain, FAR)
    on_line = [cell for cell in ring if along_and_off("powr", cell, FAR_ORIGIN, ore)[1] <= FAR.creep_line_cells]
    home_places = {cell for cell in ring if reaches(box_at("proc", cell), EAST_FIELD, FAR.refinery_ore_cells)}
    assert placement.far_field(lone, terrain, FAR) == placement.FarField(BEYOND_EAST_FIELD, FAR_ORIGIN, EAST_FIELD)
    # Every place by the line is one a refinery by the home field would need, and other places are free ...
    assert on_line and set(on_line) <= home_places and set(ring) - home_places
    # ... so the power plant and the barracks keep off all of them, at the compact places by the yard.
    for choice in (power, barracks):
        assert not reaches(box_at("proc", choice.cell), EAST_FIELD, FAR.refinery_ore_cells)
        assert choice.creep is None


# A field 24 cells along the line from the base's center to the enemy's start, deeper than the forward hold's share of
# it (0.4 of 52 cells), and out of a refinery's reach.
FORWARD_FIELD = square_patch((46, 48))
POWER_QUEUED = situations.job("Building", "powr")


def test_far_field_during_the_opening_the_creep_target_is_the_far_field_and_the_second_yard_and_forward_hold_read_it():
    terrain = terrain_of(HOME_PATCH, FORWARD_FIELD)
    first = england_base(YARD, tick=FIRST_TICK, production=[POWER_QUEUED])
    agent = creep_agent(FAR, UpkeepManager(), OpeningManager(), state=first, terrain=terrain)

    situations.ask(agent, england_base(YARD, tick=1000, production=[POWER_QUEUED]))

    world, ctx = agent.world, agent.ctx
    target = placement.CreepTarget(FORWARD_FIELD, 1)
    held = forward_hold.forward_point(world, ctx.creep_target, ctx.tuning)
    assert ctx.opening_build.steps
    assert creep_target_after_opening(ctx) is None
    assert ctx.creep_target == target
    assert expansion.deploy_site(world, ctx)[1] == FORWARD_FIELD
    assert held == forward_hold.forward_point(world, target, ctx.tuning)
    assert held != forward_hold.forward_point(world, None, ctx.tuning)


def test_far_field_after_the_opening_the_far_field_is_the_creep_target_placement_and_the_refinery_cap_go_by():
    terrain = terrain_of(HOME_PATCH, FORWARD_FIELD)
    agent = creep_agent(FAR, UpkeepManager(), state=england_base(*COMPLETE_BASE, tick=FIRST_TICK), terrain=terrain)

    situations.ask(agent, england_base(*COMPLETE_BASE, tick=1000))

    assert not agent.ctx.opening_build.steps
    assert creep_target_after_opening(agent.ctx) == placement.CreepTarget(FORWARD_FIELD, 1)


def test_far_field_with_no_far_field_the_creep_target_is_the_one_the_switch_off_rules_publish():
    terrain = terrain_of(HOME_PATCH, LANE_FIELD)
    # The lane field's ore lies 8 cells from the nearest building: beyond a creep cap of 5 cells.
    near_only = dataclasses.replace(NEXT_PATCH, creep_from_opening=True, creep_far_max_cells=5)
    published = {}
    for tuning in (near_only, NEXT_PATCH):
        first = england_base(*LANE_BASE, tick=FIRST_TICK, cash=1000, units=HARVESTERS)
        agent = creep_agent(tuning, UpkeepManager(), state=first, terrain=terrain)
        situations.ask(agent, england_base(*LANE_BASE, tick=1000, cash=1000, units=HARVESTERS))
        published[tuning.creep_from_opening] = agent.ctx.creep_target

    assert placement.far_field(lane_world(*LANE_BASE), terrain, near_only) is None
    assert published[True] == published[False] == placement.CreepTarget(LANE_FIELD, 1)


def test_far_field_the_published_target_is_worked_out_again_only_when_what_decides_it_changes(monkeypatch):
    worked_out = []
    far_field = placement.far_field
    monkeypatch.setattr(placement, "far_field", lambda *args: worked_out.append(args) or far_field(*args))
    keeper = creep.NearestFarField()
    terrain = terrain_of(HOME_PATCH, EAST_FAR_FIELD, WEST_FIELD)
    walked = lane_world(YARD, *WEST_WALK)
    # The east field with a gun 10 cells south of it, once the west field is reached.
    defended = lane_world(YARD, *WEST_WALK, defenses=[("gun", 39, 61)])

    targets = [keeper.target(world, terrain, FAR) for world in (lane_world(YARD), lane_world(YARD), walked, defended)]

    west, east = placement.CreepTarget(WEST_FIELD, 1), placement.CreepTarget(EAST_FAR_FIELD, 1)
    assert targets == [west, west, east, None]
    assert len(worked_out) == 3
    assert keeper.target(walked, terrain, TUNING) is None


POWER_READY_TO_PLACE = [situations.job("Building", "powr", ready=True)]


def test_far_field_the_opening_s_first_power_plant_is_asked_onto_the_line_with_one_creep_placement_event(match_record):
    terrain = terrain_of(HOME_PATCH, EAST_15_FIELD)
    first = england_base(YARD, tick=FIRST_TICK, production=[POWER_QUEUED])
    agent = creep_agent(FAR, UpkeepManager(), OpeningManager(), state=first, terrain=terrain)

    asked = [
        situations.with_command(
            situations.ask(agent, england_base(YARD, tick=tick, production=POWER_READY_TO_PLACE)), "place"
        )
        for tick in (1000, 1050)
    ]

    along = choose_placement("powr", world_with(YARD), terrain, FAR)
    (event,) = match_record("creep_placement")
    assert agent.ctx.opening_build.steps
    assert asked[0] == [{"cmd": "place", "item": "powr", "at": [25, 48]}]
    assert along.cell == (25, 48)
    # Asked again after 50 ticks: the same building, which the record has its reasons for already.
    assert [order["item"] for order in asked[1]] == ["powr"]
    assert event == {
        "tick": 1000,
        "building": "powr",
        "cell": [25, 48],
        "field_center": list(EAST_15_FIELD.center),
        "ore_cell": [37, 49],
        "distance_before_cells": round(along.creep.distance_before, 2),
        "distance_after_cells": round(along.creep.distance_after, 2),
        "target_patch_center": list(EAST_15_FIELD.center),
        "reason": "nearest_far_field",
        "line_distance_cells": 0.0,
    }


def test_far_field_with_no_far_field_the_opening_s_power_plant_is_asked_where_it_is_with_the_switch_off(match_record):
    terrain = terrain_of(HOME_PATCH)
    asked = {}
    for tuning in (FAR, TUNING):
        first = england_base(YARD, tick=FIRST_TICK, production=[POWER_QUEUED])
        agent = creep_agent(tuning, UpkeepManager(), OpeningManager(), state=first, terrain=terrain)
        (asked[tuning.creep_from_opening],) = situations.with_command(
            situations.ask(agent, england_base(YARD, tick=1000, production=POWER_READY_TO_PLACE)), "place"
        )

    assert asked[True] == asked[False]
    assert match_record("creep_placement") == []


def test_far_field_the_reach_first_opening_s_extra_power_plant_goes_along_the_line(match_record):
    terrain = terrain_of(SOUTH_FIELD)
    tuning = tuning_off(placement=FAR, opening=opening_off(order=REACH_FIRST))
    agent = new_agent(UpkeepManager(), OpeningManager(), tuning=tuning)
    situations.ask(agent, situations.base_state("england", YARD, tick=990, withheld=["powr"]))
    agent.ctx.terrain = terrain

    situations.ask(agent, england_base(YARD, POWER, tick=995, production=[BARRACKS_QUEUED]))
    situations.ask(agent, england_base(YARD, POWER, BARRACKS, tick=1000))
    (placed,) = situations.with_command(
        situations.ask(agent, england_base(YARD, POWER, BARRACKS, tick=1005, production=POWER_READY_TO_PLACE)),
        "place",
    )

    along = choose_placement("powr", world_with(YARD, POWER, BARRACKS), terrain, FAR)
    assert agent.ctx.opening_build.steps == ("powr", "tent", "powr", "proc", "proc", "powr", "weap")
    assert placed["at"] == list(along.cell)
    assert [event["reason"] for event in match_record("creep_placement")] == ["nearest_far_field"]


# Fields of ore the fake map gains for the whole match: one 14 cells east of the own spawn and one 13 cells west of it.
EAST_ORE = [(x, y) for y in (45, 46, 47) for x in (35, 36, 37)]
WEST_ORE = [(x, y) for y in (49, 50, 51) for x in (8, 9, 10)]


@pytest.fixture(scope="module")
def far_field_match() -> WholeMatch:
    """A whole fake match on a map with a field east and a field west of the own spawn, played with the creep from the
    opening on."""
    return play_whole_match(
        tuning_off(placement=FAR),
        game_config=with_ore(FakeRedAlert().game_config, [*EAST_ORE, *WEST_ORE]),
        engine_reports=True,
    )


def test_far_field_a_whole_fake_match_runs_without_an_error_within_the_compute_budget_field_after_field(
    far_field_match,
):
    crept = far_field_match.data_of("creep_placement")
    targets = [tuple(event["target_patch_center"]) for event in crept]

    assert len(far_field_match.compute_ms) == WHOLE_MATCH_STEPS
    assert far_field_match.data_of("error") == []
    assert far_field_match.compute_p95_ms <= COMPUTE_P95_MS
    assert crept
    assert {event["reason"] for event in crept} == {"nearest_far_field"}
    # The opening's first power plant walks toward the west field, the nearer the yard's middle, and once it is
    # reached the base walks east.
    assert crept[0]["building"] == "powr"
    assert crept[0]["distance_after_cells"] < crept[0]["distance_before_cells"]
    assert list(dict.fromkeys(targets)) == [(9, 50), (36, 46)]


# -- the places the engine refused -------------------------------------------------------------------------------------

REFUSED = dataclasses.replace(TUNING, refused_detection=True)
# A band of 4 cells about the line leaves a place along it once the first place is refused.
FAR_REFUSED = dataclasses.replace(FAR, refused_detection=True, creep_line_cells=4)
# A lone yard whose first power plant is asked onto (12, 61).
WEST_YARD = ("fact", 9, 61)
ASKED_AT_TICK = 100


def noticed(asked: dict, before: tuple, shown: tuple, tuning: PlacementTuning = REFUSED, tick: int = ASKED_AT_TICK):
    """The places refused once a watch has seen the buildings `before`, each item of `asked` has been asked onto its
    cell at `tick`, and the buildings `shown` have shown up besides, at tick 100."""
    ctx = new_agent(tuning=tuning_off(placement=tuning)).ctx
    places = RefusedPlaces()
    places.notice(world_with(*before), ctx)
    for item, cell in asked.items():
        places.asked(item, box_at(item, cell), tick, None)
    places.notice(world_with(*before, *shown), ctx)
    return places


def test_refused_a_power_plant_asked_at_12_61_that_shows_up_at_22_41_is_a_miss_and_its_place_is_never_chosen_again(
    match_record,
):
    shown = world_with(WEST_YARD, ("powr", 22, 41))

    places = noticed({"powr": (12, 61)}, (WEST_YARD,), (("powr", 22, 41),))

    (event,) = match_record("placement_refused")
    refused = box_at("powr", (12, 61))
    assert choose_placement("powr", world_with(WEST_YARD), NO_TERRAIN, REFUSED).cell == (12, 61)
    assert choose_placement("powr", shown, NO_TERRAIN, REFUSED).cell == (12, 61)
    assert places.boxes == (refused,)
    assert event == {
        "tick": 100,
        "item": "powr",
        "asked_cell": [12, 61],
        "actual_cell": [22, 41],
        "distance_cells": 20,
        "target_patch_center": None,
        "skipped": False,
    }
    for item in ("powr", "tent", "proc", "weap", "dome", "pbox", "gun"):
        cell = choose_placement(item, shown, NO_TERRAIN, REFUSED, refused=places.boxes).cell
        assert cell is not None and not box_at(item, cell).overlaps(refused)


@pytest.mark.parametrize(
    "shown_at, is_miss", [((13, 62), False), ((9, 58), False), ((8, 61), True)], ids=["1 cell", "3 cells", "4 cells"]
)
def test_refused_a_power_plant_that_shows_up_3_cells_or_fewer_off_is_no_miss(shown_at, is_miss, match_record):
    places = noticed({"powr": (12, 61)}, (WEST_YARD,), (("powr", *shown_at),))

    assert (places.boxes == (box_at("powr", (12, 61)),)) is is_miss
    assert len(match_record("placement_refused")) == int(is_miss)


@pytest.mark.parametrize("asked_at, is_miss", [(25, True), (24, False)], ids=["75 ticks before", "76 ticks before"])
def test_refused_only_a_building_that_shows_up_within_promise_ticks_of_its_asking_is_compared(asked_at, is_miss):
    places = noticed({"powr": (12, 61)}, (WEST_YARD,), (("powr", 22, 41),), tick=asked_at)

    assert bool(places.boxes) is is_miss


def test_refused_a_building_of_another_item_or_one_that_stood_before_is_no_miss():
    places = noticed({"tent": (12, 61)}, (WEST_YARD, ("powr", 22, 41)), (("powr", 30, 41),))

    assert places.boxes == ()


def test_refused_the_places_kept_never_number_more_than_64_the_oldest_dropped_first():
    ctx = new_agent(tuning=tuning_off(placement=REFUSED)).ctx
    places, shown = RefusedPlaces(), [YARD]
    places.notice(world_with(*shown), ctx)

    for number in range(70):
        places.asked("powr", box_at("powr", (number, 0)), ASKED_AT_TICK, None)
        shown.append(("powr", 50, 90))
        places.notice(world_with(*shown), ctx)

    assert REFUSED.refused_spots_max == 64 and len(places.boxes) == 64
    assert (places.boxes[0], places.boxes[-1]) == (box_at("powr", (6, 0)), box_at("powr", (69, 0)))


def test_refused_with_refused_places_the_terrain_has_their_cells_blocked_and_none_leaves_it_as_it_is():
    terrain = terrain_of(HOME_PATCH)

    blocked = placement.with_refused(terrain, [Box(1, 1, 2, 2)])

    assert placement.with_refused(terrain, ()) is terrain
    assert placement.with_refused(terrain, [Box(21, 39, 3, 3)]) is terrain
    assert blocked.blocked == terrain.blocked | {(1, 1), (2, 1), (1, 2), (2, 2)}
    assert blocked.ore_patches == terrain.ore_patches


def test_refused_the_far_field_is_worked_out_with_the_refused_places_kept_off():
    terrain = terrain_of(HOME_PATCH, EAST_FAR_FIELD, WEST_FIELD)
    walked = lane_world(YARD, *WEST_WALK)
    # Every cell within 2 of the west field's ore.
    around_west = Box(8, 47, 7, 7)

    reached = choose_placement("powr", walked, terrain, FAR_REFUSED)
    kept_off = choose_placement("powr", walked, terrain, FAR_REFUSED, refused=[around_west])

    assert reached.creep.field_center == EAST_FAR_FIELD.center
    assert kept_off.creep.field_center == WEST_FIELD.center


SHOWN_ELSEWHERE_ROWS = 15


@pytest.mark.parametrize("tuning", [REFUSED, TUNING], ids=["switch on", "switch off"])
def test_refused_upkeep_asks_the_next_power_plant_off_a_place_the_engine_refused(tuning, match_record):
    base = (YARD, POWER, BARRACKS)
    agent = creep_agent(tuning, UpkeepManager(), state=england_base(*base, tick=FIRST_TICK), terrain=NO_TERRAIN)

    (first,) = situations.with_command(
        situations.ask(agent, england_base(*base, tick=1000, production=[POWER_READY])), "place"
    )
    asked = tuple(first["at"])
    elsewhere = ("powr", asked[0], asked[1] + SHOWN_ELSEWHERE_ROWS)
    situations.ask(agent, england_base(*base, elsewhere, tick=1010))
    # Once the place first asked for is no longer kept for the building on its way.
    (second,) = situations.with_command(
        situations.ask(agent, england_base(*base, elsewhere, tick=1100, production=[POWER_READY])), "place"
    )

    events = match_record("placement_refused")
    if tuning.refused_detection:
        assert not box_at("powr", tuple(second["at"])).overlaps(box_at("powr", asked))
        assert events == [
            {
                "tick": 1010,
                "item": "powr",
                "asked_cell": list(asked),
                "actual_cell": list(elsewhere[1:]),
                "distance_cells": SHOWN_ELSEWHERE_ROWS,
                "target_patch_center": None,
                "skipped": False,
            }
        ]
    else:
        assert second["at"] == first["at"] and events == []


# A field 14 rows below the west yard, which the power plants of the skip tests are placed toward.
BELOW_WEST_YARD = square_patch((13, 75))


def misses_toward(field: OrePatch, rows_off: list, tuning: PlacementTuning = REFUSED, toward=BELOW_WEST_YARD.center):
    """The match's context once a watch has seen a lone yard and then, for each entry of `rows_off`, a power plant
    asked onto a place toward the far field centered at `toward` has shown up that many rows below that place."""
    ctx = new_agent(tuning=tuning_off(placement=tuning)).ctx
    ctx.terrain = terrain_of(HOME_PATCH, field)
    places, shown = RefusedPlaces(), [WEST_YARD]
    places.notice(world_with(*shown), ctx)
    for number, rows in enumerate(rows_off):
        asked = (12 + 3 * number, 61)
        places.asked("powr", box_at("powr", asked), ASKED_AT_TICK, toward)
        shown.append(("powr", asked[0], asked[1] + rows))
        places.notice(world_with(*shown), ctx)
    return ctx


@pytest.mark.parametrize(
    "rows_off, skipped",
    [
        ([20, 20], [False, True]),
        ([20], [False]),
        ([20, 0, 20], [False, False]),
        ([20, 1, 20, 20], [False, False, True]),
        ([20, 20, 20], [False, True, True]),
    ],
    ids=["two in a row", "one", "a landing between", "two after a landing", "three in a row"],
)
def test_refused_two_misses_in_a_row_toward_a_far_field_skip_it_and_a_landing_between_them_starts_afresh(
    rows_off, skipped, match_record
):
    ctx = misses_toward(BELOW_WEST_YARD, rows_off)

    events = match_record("placement_refused")
    assert [event["skipped"] for event in events] == skipped
    assert {tuple(event["target_patch_center"]) for event in events} == {BELOW_WEST_YARD.center}
    assert ctx.skipped_fields == ([BELOW_WEST_YARD] if skipped[-1] else [])


def test_refused_with_3_misses_to_skip_a_field_two_in_a_row_do_not_skip_it():
    three = dataclasses.replace(REFUSED, refused_misses_to_skip=3)

    assert misses_toward(BELOW_WEST_YARD, [20, 20], three).skipped_fields == []
    assert misses_toward(BELOW_WEST_YARD, [20, 20, 20], three).skipped_fields == [BELOW_WEST_YARD]


def test_refused_misses_of_buildings_placed_toward_no_far_field_never_skip_one(match_record):
    ctx = misses_toward(BELOW_WEST_YARD, [20, 20, 20], toward=None)

    assert ctx.skipped_fields == []
    assert {event["skipped"] for event in match_record("placement_refused")} == {False}


def data_of(lines: list, kind: str) -> list:
    return [line["data"] for line in lines if line["kind"] == kind]


def test_refused_two_power_plants_placed_along_the_line_and_put_elsewhere_skip_the_far_field(match_record):
    terrain = terrain_of(HOME_PATCH, EAST_15_FIELD)
    agent = creep_agent(FAR_REFUSED, UpkeepManager(), state=england_base(YARD, tick=FIRST_TICK), terrain=terrain)
    shown = [YARD]

    for number, tick in enumerate((1000, 1100)):
        situations.ask(agent, england_base(*shown, tick=tick, production=POWER_READY_TO_PLACE))
        shown.append(("powr", 10, 30 + 4 * number))
        situations.ask(agent, england_base(*shown, tick=tick + 10))

    lines = match_record()
    crept, refused = data_of(lines, "creep_placement"), data_of(lines, "placement_refused")
    assert [event["target_patch_center"] for event in crept] == [list(EAST_15_FIELD.center)] * 2
    assert [(event["asked_cell"], event["skipped"]) for event in refused] == [
        (crept[0]["cell"], False),
        (crept[1]["cell"], True),
    ]
    assert agent.ctx.skipped_fields == [EAST_15_FIELD]


# Ore 27 cells south of the own spawn, beyond the 16 cells from its yard within which the fake game puts a building
# where it is asked: a field the engine will not build toward.
UNREACHABLE_SOUTH_ORE = [(x, y) for y in (74, 75, 76) for x in (21, 22, 23)]
UNREACHABLE_SOUTH_CENTER = [22, 75]


@pytest.fixture(scope="module")
def refused_match() -> WholeMatch:
    """A whole fake match on a map with ore out of the fake game's reach to the south, played with the refused places
    noticed, the MCV sent to a skipped field, the second yard and the creep from the opening on."""
    tuning = tuning_off(placement=FAR_REFUSED, expansion=expansion_off(second_yard=True, mcv_to_skipped_fields=True))
    return play_whole_match(tuning, game_config=with_ore(FakeRedAlert().game_config, UNREACHABLE_SOUTH_ORE))


def test_refused_a_whole_fake_match_with_the_switches_on_runs_without_an_error_and_skips_the_field_out_of_reach(
    refused_match,
):
    refused = refused_match.data_of("placement_refused")
    trained = [event for event in refused_match.data_of("expansion") if event["phase"] == "mcv_trained"]

    assert len(refused_match.compute_ms) == WHOLE_MATCH_STEPS and refused_match.data_of("error") == []
    assert refused_match.compute_p95_ms <= COMPUTE_P95_MS
    assert refused and all(event["distance_cells"] > FAR_REFUSED.refused_miss_cells for event in refused)
    assert [event["skipped"] for event in refused[:2]] == [False, True]
    assert {tuple(event["target_patch_center"]) for event in refused[:2]} == {tuple(UNREACHABLE_SOUTH_CENTER)}
    assert [field.center for field in refused_match.agent.ctx.skipped_fields] == [tuple(UNREACHABLE_SOUTH_CENTER)]
    # The MCV of the second yard is taken to the field the base could not grow to.
    assert trained and trained[0]["field_center"] == UNREACHABLE_SOUTH_CENTER


# -- the far field, walked to field after field within the creep cap ---------------------------------------------------

CONTINUOUS = dataclasses.replace(FAR, creep_continuous=True)
WIDE_BOUNDS = (0, 0, 128, 128)
# A field 56 cells below the middle of the yard reported at (22, 48), and one 26 cells south-east of it.
FIELD_56_CELLS_AWAY = square_patch((22, 105))
SOUTH_EAST_FIELD = square_patch((50, 75))
# A refinery standing against the west field, and one standing against the east field 16 cells from the yard.
AGAINST_WEST_FIELD, AGAINST_EAST_FAR_FIELD = ("proc", 13, 47), ("proc", 35, 48)


@pytest.mark.parametrize("tuning", [CONTINUOUS, FAR], ids=["continuous", "not continuous"])
def test_continuous_a_field_56_cells_from_the_yard_is_never_the_far_field_whatever_the_switch(tuning):
    terrain = terrain_of(HOME_PATCH, FIELD_56_CELLS_AWAY)

    field = placement.far_field(world_with(YARD, bounds=WIDE_BOUNDS), terrain, tuning)

    assert cells.distance(FIELD_56_CELLS_AWAY.center, FAR_ORIGIN) == 56
    assert ore_distance(YARD_BOX, FIELD_56_CELLS_AWAY) == 54 > tuning.creep_far_max_cells == 30
    assert field is None


def test_continuous_once_a_refinery_stands_at_each_target_the_next_nearest_follows_three_fields_in_order():
    terrain = terrain_of(HOME_PATCH, WEST_FIELD, EAST_FAR_FIELD, SOUTH_EAST_FIELD, FIELD_56_CELLS_AWAY)
    bases = [(YARD,), (YARD, AGAINST_WEST_FIELD), (YARD, AGAINST_WEST_FIELD, AGAINST_EAST_FAR_FIELD)]

    targets = [placement.far_field(world_with(*base, bounds=WIDE_BOUNDS), terrain, CONTINUOUS).patch for base in bases]

    assert refinery_ore_distance(AGAINST_WEST_FIELD[1:], WEST_FIELD) <= CONTINUOUS.refinery_ore_cells
    assert refinery_ore_distance(AGAINST_EAST_FAR_FIELD[1:], EAST_FAR_FIELD) <= CONTINUOUS.refinery_ore_cells
    assert targets == [WEST_FIELD, EAST_FAR_FIELD, SOUTH_EAST_FIELD]
    # The field 56 cells off is out of the creep cap of every building, and the cap holds with the switch off too.
    assert placement.far_field(world_with(*bases[-1], bounds=WIDE_BOUNDS), terrain, FAR).patch == SOUTH_EAST_FIELD


def test_continuous_a_skipped_field_is_never_the_far_field_and_the_next_one_is():
    world, terrain = world_with(YARD), terrain_of(HOME_PATCH, WEST_FIELD, EAST_FAR_FIELD)

    walked = choose_placement("powr", world, terrain, CONTINUOUS, skipped=[WEST_FIELD])

    assert placement.far_field(world, terrain, CONTINUOUS, skipped=[WEST_FIELD]).patch == EAST_FAR_FIELD
    assert walked.creep.field_center == EAST_FAR_FIELD.center
    # With every field skipped there is none, and the building is placed as with the switch off.
    assert placement.far_field(world, terrain, CONTINUOUS, skipped=[WEST_FIELD, EAST_FAR_FIELD]) is None
    every_one = choose_placement("powr", world, terrain, CONTINUOUS, skipped=[WEST_FIELD, EAST_FAR_FIELD])
    assert every_one == choose_placement("powr", world, terrain, TUNING)
    # With the switch off a skipped field is still the far field.
    assert placement.far_field(world, terrain, FAR, skipped=[WEST_FIELD]).patch == WEST_FIELD


def test_continuous_the_published_far_field_passes_over_a_field_once_it_is_skipped():
    keeper = creep.NearestFarField()
    world, terrain = lane_world(YARD), terrain_of(HOME_PATCH, WEST_FIELD, EAST_FAR_FIELD)

    targets = [keeper.target(world, terrain, CONTINUOUS, skipped) for skipped in ([], [WEST_FIELD])]

    assert targets == [placement.CreepTarget(WEST_FIELD, 1), placement.CreepTarget(EAST_FAR_FIELD, 1)]


def test_continuous_refused_two_misses_skip_the_far_field_and_the_base_walks_toward_the_next_field(match_record):
    terrain = terrain_of(HOME_PATCH, EAST_15_FIELD, FAR_SOUTH_FIELD)
    # A band of 4 cells about the line leaves a place along it once the first place is refused.
    tuning = dataclasses.replace(FAR_REFUSED, creep_continuous=True, creep_line_cells=4)
    agent = creep_agent(tuning, UpkeepManager(), state=england_base(YARD, tick=FIRST_TICK), terrain=terrain)
    shown = [YARD]

    for number, tick in enumerate((1000, 1100)):
        situations.ask(agent, england_base(*shown, tick=tick, production=POWER_READY_TO_PLACE))
        shown.append(("powr", 10, 30 + 4 * number))
        situations.ask(agent, england_base(*shown, tick=tick + 10))
    situations.ask(agent, england_base(*shown, tick=1200, production=POWER_READY_TO_PLACE))

    targets = [tuple(event["target_patch_center"]) for event in match_record("creep_placement")]
    assert cells.distance(EAST_15_FIELD.center, FAR_ORIGIN) < cells.distance(FAR_SOUTH_FIELD.center, FAR_ORIGIN)
    assert targets == [EAST_15_FIELD.center, EAST_15_FIELD.center, FAR_SOUTH_FIELD.center]
    assert agent.ctx.skipped_fields == [EAST_15_FIELD]
    assert agent.ctx.creep_target == placement.CreepTarget(FAR_SOUTH_FIELD, 1)


# Besides the fields east and west of the own spawn, ore 42 cells from the yard's middle, south of the west field: out
# of the creep cap of the yard, within it once the base has grown toward it.
BEYOND_CAP_ORE = [(x, y) for y in (89, 90, 91) for x in (9, 10, 11)]


@pytest.fixture(scope="module")
def continuous_match() -> WholeMatch:
    """A whole fake match on a map with fields east and west of the own spawn and one 42 cells from the yard's middle,
    played with the creep from the opening and the continuous creep on."""
    ore = [*EAST_ORE, *WEST_ORE, *BEYOND_CAP_ORE]
    return play_whole_match(tuning_off(placement=CONTINUOUS), game_config=with_ore(FakeRedAlert().game_config, ore))


def test_continuous_a_whole_fake_match_runs_without_an_error_and_walks_on_to_a_field_42_cells_off(continuous_match):
    crept = continuous_match.data_of("creep_placement")
    targets = list(dict.fromkeys(tuple(event["target_patch_center"]) for event in crept))

    assert len(continuous_match.compute_ms) == WHOLE_MATCH_STEPS and continuous_match.data_of("error") == []
    assert continuous_match.compute_p95_ms <= COMPUTE_P95_MS
    assert crept and {event["reason"] for event in crept} == {"nearest_far_field"}
    # West, then east, then on to the field 42 cells off once the base has grown within the cap of it.
    assert targets == [(9, 50), (36, 46), (10, 90)]
    assert cells.distance(targets[-1], FAR_ORIGIN) > CONTINUOUS.creep_far_max_cells


# -- the fields the base reached, which a refinery claims --------------------------------------------------------------

CLAIMS = tuning_off(placement=FAR, upkeep=dataclasses.replace(upkeep_off(), refinery_claims_reached=True))
# The base once the opening is complete, with three refineries north of the yard: the east field, 16 cells away, is the
# far field, and once two more power plants walk east toward it the field 18 cells south is.
CLAIM_BASE = (*COMPLETE_BASE, ("proc", 28, 42))
EAST_WALK = (("powr", 31, 48), ("powr", 35, 48))
CLAIM_TERRAIN = terrain_of(HOME_PATCH, EAST_FAR_FIELD, FAR_SOUTH_FIELD)
# A gun 10 cells below the east field's lowest row.
GUN_BY_EAST_FIELD = [("gun", 39, 61)]


def claim_keeper_targets(*worlds: World, claims: bool = True) -> tuple[list, list]:
    """The far field a new keeper names for each world in turn, and its claim fields after each."""
    keeper, targets, claimed = creep.NearestFarField(), [], []
    for world in worlds:
        target = keeper.target(world, CLAIM_TERRAIN, FAR, (), claims)
        targets.append(None if target is None else target.patch)
        claimed.append(list(keeper.claim_fields))
    return targets, claimed


def test_claim_a_far_field_the_base_reached_becomes_a_claim_field_and_leaves_once_a_refinery_stands_against_it():
    before, after = lane_world(*CLAIM_BASE, EAST_WALK[0]), lane_world(*CLAIM_BASE, *EAST_WALK)
    served = lane_world(*CLAIM_BASE, *EAST_WALK, ("proc", 35, 48))

    targets, claimed = claim_keeper_targets(before, after, served)

    assert targets == [EAST_FAR_FIELD, FAR_SOUTH_FIELD, FAR_SOUTH_FIELD]
    assert claimed == [[], [EAST_FAR_FIELD], []]


@pytest.mark.parametrize(
    "then",
    [lane_world(*CLAIM_BASE, *EAST_WALK, defenses=GUN_BY_EAST_FIELD), lane_world(*CLAIM_BASE, EAST_WALK[0])],
    ids=["a defense within 10 cells", "no place reaches it any more"],
)
def test_claim_a_claim_field_with_a_defense_near_it_or_no_place_against_it_any_more_is_dropped(then):
    targets, claimed = claim_keeper_targets(
        lane_world(*CLAIM_BASE, *EAST_WALK[:1]), lane_world(*CLAIM_BASE, *EAST_WALK), then
    )

    assert claimed == [[], [EAST_FAR_FIELD], []]


def test_claim_with_the_switch_off_no_field_is_ever_a_claim_field():
    targets, claimed = claim_keeper_targets(
        lane_world(*CLAIM_BASE, EAST_WALK[0]), lane_world(*CLAIM_BASE, *EAST_WALK), claims=False
    )

    assert targets == [EAST_FAR_FIELD, FAR_SOUTH_FIELD] and claimed == [[], []]


def claims_agent(tuning: Tuning = CLAIMS, base: tuple = CLAIM_BASE):
    """An agent that plays with the upkeep manager alone under a tuning, having seen a base with these buildings and
    the first power plant walking east, on the claim map, and then the same base once more."""
    agent = new_agent(UpkeepManager(), tuning=tuning)
    situations.ask(agent, england_base(*base, EAST_WALK[0], tick=FIRST_TICK))
    agent.ctx.terrain = CLAIM_TERRAIN
    situations.ask(agent, england_base(*base, EAST_WALK[0], tick=1000))
    return agent


@pytest.mark.parametrize("tuning, claimed", [(CLAIMS, True), (tuning_off(placement=FAR), False)], ids=["on", "off"])
def test_claim_upkeep_publishes_the_claim_fields_and_whether_a_claim_is_due(tuning, claimed):
    agent = claims_agent(tuning)
    before = (list(agent.ctx.claim_fields), agent.ctx.refinery_claim_due)

    situations.ask(agent, england_base(*CLAIM_BASE, *EAST_WALK, tick=1010))

    assert before == ([], False)
    assert agent.ctx.creep_target == placement.CreepTarget(FAR_SOUTH_FIELD, 1)
    assert (agent.ctx.claim_fields, agent.ctx.refinery_claim_due) == (
        ([EAST_FAR_FIELD], True) if claimed else ([], False)
    )


# A refinery's price, 1,400, and the production reserve.
REFINERY_PRICE_AND_RESERVE = 1400 + upkeep_off().production_cash_reserve


def builds_of(orders: list) -> list:
    """The items of the build orders among those given."""
    return [order["item"] for order in situations.with_command(orders, "build")]


@pytest.mark.parametrize(
    "cash, ordered",
    [(REFINERY_PRICE_AND_RESERVE, True), (REFINERY_PRICE_AND_RESERVE - 1, False)],
    ids=["covered", "1 short"],
)
def test_claim_once_the_far_field_is_reached_a_refinery_is_ordered_past_refineries_max_once_cash_covers_it(
    cash, ordered
):
    agent = claims_agent()

    orders = situations.ask(agent, england_base(*CLAIM_BASE, *EAST_WALK, tick=1010, cash=cash))

    assert upkeep_off().refineries_max == 3 and agent.ctx.production.standing("proc") == 3
    assert agent.world.items["proc"].cost == 1400 and agent.ctx.refinery_claim_due
    assert ("proc" in builds_of(orders)) is ordered


def test_claim_while_the_far_field_is_not_reached_no_claim_is_due_and_no_refinery_is_ordered():
    agent = claims_agent()

    orders = situations.ask(agent, england_base(*CLAIM_BASE, EAST_WALK[0], tick=1010, cash=5000))

    assert agent.ctx.creep_target == placement.CreepTarget(EAST_FAR_FIELD, 1)
    assert not agent.ctx.refinery_claim_due and "proc" not in builds_of(orders)


def test_claim_with_8_refineries_standing_no_claim_is_due_or_ordered():
    # Five more refineries far north-west of the base: 8 in all.
    eight = (*CLAIM_BASE, *[("proc", 2 + 4 * number, 2) for number in range(5)])
    agent = claims_agent(base=eight)

    orders = situations.ask(agent, england_base(*eight, *EAST_WALK, tick=1010, cash=5000))

    assert agent.ctx.claim_fields == [EAST_FAR_FIELD] and agent.ctx.production.standing("proc") == 8
    assert not agent.ctx.refinery_claim_due and "proc" not in builds_of(orders)


def test_claim_with_the_switch_off_the_reached_far_field_orders_no_refinery_past_refineries_max():
    agent = claims_agent(tuning_off(placement=FAR))

    orders = situations.ask(agent, england_base(*CLAIM_BASE, *EAST_WALK, tick=1010, cash=5000))

    assert "proc" not in builds_of(orders)


PROC_READY = [situations.job("Building", "proc", ready=True)]


def test_claim_a_claim_placement_stands_against_the_field_or_is_none_while_no_place_reaches_it():
    reached, unreached = lane_world(*CLAIM_BASE, *EAST_WALK), lane_world(*CLAIM_BASE, EAST_WALK[0])

    choice = placement.claim_placement(EAST_FAR_FIELD, reached, CLAIM_TERRAIN, FAR)

    assert refinery_ore_distance(choice.cell, EAST_FAR_FIELD) <= FAR.refinery_ore_cells
    assert choice.field == placement.FieldChoice(EAST_FAR_FIELD, placement.FIELD_CLAIM, 0)
    assert placement.claim_placement(EAST_FAR_FIELD, unreached, CLAIM_TERRAIN, FAR) is None


@pytest.mark.parametrize("tuning", [CLAIMS, tuning_off(placement=FAR)], ids=["on", "off"])
def test_claim_the_refinery_stands_against_the_claimed_field_with_a_refinery_field_event_of_rule_claim(
    tuning, match_record
):
    agent = claims_agent(tuning)
    situations.ask(agent, england_base(*CLAIM_BASE, *EAST_WALK, tick=1010))
    today = choose_placement("proc", agent.world, CLAIM_TERRAIN, FAR).cell
    claimed = placement.claim_placement(EAST_FAR_FIELD, agent.world, CLAIM_TERRAIN, FAR).cell

    (placed,) = situations.with_command(
        situations.ask(agent, england_base(*CLAIM_BASE, *EAST_WALK, tick=1020, production=PROC_READY)), "place"
    )
    situations.ask(agent, england_base(*CLAIM_BASE, *EAST_WALK, stands("proc", placed["at"]), tick=1030))

    events = match_record("refinery_field")
    assert today != claimed and refinery_ore_distance(claimed, EAST_FAR_FIELD) <= FAR.refinery_ore_cells
    if tuning.upkeep.refinery_claims_reached:
        assert placed["at"] == list(claimed)
        assert events == [
            {
                "tick": 1020,
                "cell": list(claimed),
                "rule": "claim",
                "field_center": [39, 50],
                "field_cells": 9,
                "served_by": 0,
                "ore_distance_cells": refinery_ore_distance(claimed, EAST_FAR_FIELD),
            }
        ]
        # Once the refinery stands against it, the field is claimed no more.
        assert agent.ctx.claim_fields == [] and not agent.ctx.refinery_claim_due
    else:
        assert placed["at"] == list(today) and events == []


def test_claim_while_the_opening_s_build_goes_on_a_refinery_is_placed_as_without_claims(match_record):
    agent = claims_agent()
    situations.ask(agent, england_base(*CLAIM_BASE, *EAST_WALK, tick=1010))
    agent.ctx.opening_build = OpeningBuild(steps=("proc", "weap"))
    today = choose_placement("proc", agent.world, CLAIM_TERRAIN, FAR).cell

    (placed,) = situations.with_command(
        situations.ask(agent, england_base(*CLAIM_BASE, *EAST_WALK, tick=1020, production=PROC_READY)), "place"
    )

    assert agent.ctx.claim_fields == [EAST_FAR_FIELD]
    assert placed["at"] == list(today) and match_record("refinery_field") == []


@pytest.fixture(scope="module")
def claim_match() -> WholeMatch:
    """A whole fake match on the map with fields east and west of the own spawn, played with the creep from the opening
    and the refinery claims on."""
    return play_whole_match(
        CLAIMS, game_config=with_ore(FakeRedAlert().game_config, [*EAST_ORE, *WEST_ORE]), engine_reports=True
    )


def test_claim_a_whole_fake_match_runs_without_an_error_and_each_reached_far_field_gets_a_refinery(claim_match):
    crept = claim_match.data_of("creep_placement")
    targets = list(dict.fromkeys(tuple(event["target_patch_center"]) for event in crept))
    claims = [event for event in claim_match.data_of("refinery_field") if event["rule"] == "claim"]
    refineries = [
        box_at("proc", placement.footprint_corner("proc", (b["x"], b["y"])))
        for b in claim_match.game.own_buildings()
        if b["type"] == "proc"
    ]
    fields = {patch.center: patch for patch in claim_match.agent.ctx.terrain.ore_patches}

    assert len(claim_match.compute_ms) == WHOLE_MATCH_STEPS and claim_match.data_of("error") == []
    assert claim_match.compute_p95_ms <= COMPUTE_P95_MS
    # The base reached the west field, then the east one, and moved on from both. The west field, reached during the
    # opening, took the opening's second refinery; the east one a claim.
    assert targets == [(9, 50), (36, 46)] and claim_match.agent.ctx.creep_target is None
    assert [tuple(event["field_center"]) for event in claims] == targets[1:]
    assert all(event["ore_distance_cells"] <= FAR.refinery_ore_cells for event in claims)
    for center in targets:
        assert min(ore_distance(box, fields[center]) for box in refineries) <= FAR.refinery_ore_cells


# -- a second yard's own line of growth --------------------------------------------------------------------------------

REMOTE = dataclasses.replace(FAR, remote_yard_growth=True)
# A second yard 30 cells below the first, and its middle.
FAR_SECOND_YARD = ("fact", 22, 78)
SECOND_ORIGIN = (22, 79)
# The main base's far field, between the yards and nearer the second, and a field 17 cells east of the second yard.
BETWEEN_THE_YARDS = square_patch((23, 68))
BY_THE_SECOND_YARD = square_patch((40, 80))
REMOTE_TERRAIN = terrain_of(HOME_PATCH, BETWEEN_THE_YARDS, BY_THE_SECOND_YARD)


def test_remote_yard_the_second_yard_s_target_is_the_field_nearest_it_that_is_not_the_main_target():
    world = world_with(YARD, FAR_SECOND_YARD)

    main, second = placement.far_fields(world, REMOTE_TERRAIN, REMOTE)

    assert cells.distance(YARD[1:], FAR_SECOND_YARD[1:]) == 30
    assert (
        main
        == placement.far_field(world, REMOTE_TERRAIN, REMOTE)
        == placement.FarField(BETWEEN_THE_YARDS, FAR_ORIGIN, HOME_PATCH)
    )
    assert cells.distance(BETWEEN_THE_YARDS.center, SECOND_ORIGIN) < cells.distance(
        BY_THE_SECOND_YARD.center, SECOND_ORIGIN
    )
    assert second == placement.FarField(BY_THE_SECOND_YARD, SECOND_ORIGIN, HOME_PATCH)
    yard = placement.second_yard(world, REMOTE)
    assert (yard.x, yard.y) == FAR_SECOND_YARD[1:]


def test_remote_yard_a_skipped_field_is_never_the_second_yard_s_target_and_the_two_targets_are_never_one_field():
    world = world_with(YARD, FAR_SECOND_YARD)

    skipped = placement.far_fields(world, REMOTE_TERRAIN, REMOTE, skipped=[BY_THE_SECOND_YARD])
    only_one = placement.far_fields(world, terrain_of(HOME_PATCH, BETWEEN_THE_YARDS), REMOTE)

    assert skipped == (placement.FarField(BETWEEN_THE_YARDS, FAR_ORIGIN, HOME_PATCH), None)
    assert only_one == (placement.FarField(BETWEEN_THE_YARDS, FAR_ORIGIN, HOME_PATCH), None)


@pytest.mark.parametrize(
    "buildings, tuning",
    [((YARD,), REMOTE), ((YARD, ("fact", 30, 48)), REMOTE), ((YARD, FAR_SECOND_YARD), FAR)],
    ids=["one yard", "a yard 8 cells away", "switch off"],
)
def test_remote_yard_without_a_far_second_yard_or_with_the_switch_off_there_is_no_second_target(buildings, tuning):
    world = world_with(*buildings)

    main, second = placement.far_fields(world, REMOTE_TERRAIN, tuning)

    assert placement.second_yard(world, tuning) is None and second is None
    assert main == placement.far_field(world, REMOTE_TERRAIN, FAR)


# A field 20 cells west of the main yard's middle: the main base's far field, while the second yard's is the one east
# of that yard.
WEST_OF_THE_MAIN_YARD = square_patch((3, 50))


def place_four_power_plants(tuning: PlacementTuning, terrain: Terrain) -> None:
    """Have an upkeep agent with the two yards place four power plants in turn, each standing before the next."""
    shown = [YARD, FAR_SECOND_YARD]
    agent = creep_agent(tuning, UpkeepManager(), state=england_base(*shown, tick=FIRST_TICK), terrain=terrain)
    for number in range(4):
        tick = 1000 + 100 * number
        (placed,) = situations.with_command(
            situations.ask(agent, england_base(*shown, tick=tick, production=POWER_READY_TO_PLACE)), "place"
        )
        shown.append(stands("powr", placed["at"]))
        situations.ask(agent, england_base(*shown, tick=tick + 10))


def test_remote_yard_four_power_plants_alternate_main_second_main_second_each_on_its_line(match_record):
    place_four_power_plants(REMOTE, terrain_of(HOME_PATCH, WEST_OF_THE_MAIN_YARD, BY_THE_SECOND_YARD))

    crept = match_record("creep_placement")
    assert [event["yard"] for event in crept] == ["main", "second", "main", "second"]
    assert [tuple(event["target_patch_center"]) for event in crept] == [
        WEST_OF_THE_MAIN_YARD.center,
        BY_THE_SECOND_YARD.center,
    ] * 2
    assert all(event["line_distance_cells"] <= REMOTE.creep_line_cells for event in crept)
    # The second yard's line starts at its middle and walks east, the main base's walks west from the first yard.
    assert all(event["cell"][1] >= FAR_SECOND_YARD[2] for event in crept[1::2])
    assert all(event["cell"][0] < YARD[1] and event["cell"][1] < FAR_SECOND_YARD[2] for event in crept[::2])


def test_remote_yard_with_no_target_for_the_second_yard_all_go_on_the_main_line(match_record):
    place_four_power_plants(REMOTE, terrain_of(HOME_PATCH, WEST_OF_THE_MAIN_YARD))

    crept = match_record("creep_placement")
    assert len(crept) == 4
    assert {tuple(event["target_patch_center"]) for event in crept} == {WEST_OF_THE_MAIN_YARD.center}
    assert all("yard" not in event for event in crept)


@pytest.mark.parametrize("item", ["powr", "tent", "weap", "proc", "pbox"])
def test_remote_yard_with_the_switch_off_or_one_yard_the_second_line_changes_nothing(item):
    terrain = terrain_of(HOME_PATCH, WEST_OF_THE_MAIN_YARD, BY_THE_SECOND_YARD)
    two, one = world_with(YARD, FAR_SECOND_YARD), world_with(YARD)

    assert choose_placement(item, two, terrain, FAR, second_line=True) == choose_placement(item, two, terrain, FAR)
    assert choose_placement(item, one, terrain, REMOTE, second_line=True) == choose_placement(item, one, terrain, FAR)


# A field 22.6 cells from the second yard's middle and 41.3 from the first yard's: beyond the main base's 40-cell cap.
BEYOND_THE_SECOND_YARD = square_patch((45, 85))
# Five power plants walking from the second yard toward it, the fifth close enough for a refinery to reach it.
SECOND_WALK = (("powr", 27, 81), ("powr", 30, 82), ("powr", 33, 83), ("powr", 36, 84), ("powr", 39, 84))
TWO_LINE_TERRAIN = terrain_of(HOME_PATCH, WEST_FIELD, BEYOND_THE_SECOND_YARD)
REMOTE_CLAIMS = tuning_off(placement=REMOTE, upkeep=dataclasses.replace(upkeep_off(), refinery_claims_reached=True))


@pytest.mark.parametrize("tuning, second_claimed", [(REMOTE, True), (FAR, False)], ids=["two lines", "switch off"])
def test_remote_yard_claims_a_field_reached_on_the_second_line_is_claimed_after_an_older_main_line_field(
    tuning, second_claimed
):
    keeper, targets, claimed = creep.NearestFarField(), [], []

    for walk in ((), WEST_WALK, (*WEST_WALK, *SECOND_WALK)):
        world = lane_world(YARD, FAR_SECOND_YARD, *walk)
        targets.append(keeper.target(world, TWO_LINE_TERRAIN, tuning, (), True))
        claimed.append(list(keeper.claim_fields))

    seconds = [placement.far_fields(lane_world(YARD, FAR_SECOND_YARD), TWO_LINE_TERRAIN, tuning)[1]]
    # With no second line the main base's creep cap is measured from every building, the second yard's included, so
    # its line then walks toward the field by the second yard.
    by_the_second_yard = None if second_claimed else placement.CreepTarget(BEYOND_THE_SECOND_YARD, 1)
    assert targets == [placement.CreepTarget(WEST_FIELD, 1), by_the_second_yard, None]
    if second_claimed:
        assert seconds == [placement.FarField(BEYOND_THE_SECOND_YARD, SECOND_ORIGIN, HOME_PATCH)]
    else:
        assert seconds == [None]
    assert claimed == [[], [WEST_FIELD], [WEST_FIELD, BEYOND_THE_SECOND_YARD]]


def test_remote_yard_claims_the_main_line_field_gets_the_first_claim_refinery_and_the_second_line_field_the_next(
    match_record,
):
    walked = (YARD, FAR_SECOND_YARD, *WEST_WALK, *SECOND_WALK)
    agent = new_agent(UpkeepManager(), tuning=REMOTE_CLAIMS)
    situations.ask(agent, england_base(YARD, FAR_SECOND_YARD, tick=FIRST_TICK))
    agent.ctx.terrain = TWO_LINE_TERRAIN
    for tick, shown in ((1000, (YARD, FAR_SECOND_YARD)), (1010, walked[:4]), (1020, walked)):
        situations.ask(agent, england_base(*shown, tick=tick))
    claimed_in_turn = [list(agent.ctx.claim_fields)]

    placed = []
    for tick in (1030, 1050):
        (order,) = situations.with_command(
            situations.ask(agent, england_base(*walked, *placed, tick=tick, production=PROC_READY)), "place"
        )
        placed.append(stands("proc", order["at"]))
        situations.ask(agent, england_base(*walked, *placed, tick=tick + 10))
        claimed_in_turn.append(list(agent.ctx.claim_fields))

    events = match_record("refinery_field")
    assert claimed_in_turn == [[WEST_FIELD, BEYOND_THE_SECOND_YARD], [BEYOND_THE_SECOND_YARD], []]
    assert [(event["rule"], tuple(event["field_center"])) for event in events] == [
        ("claim", WEST_FIELD.center),
        ("claim", BEYOND_THE_SECOND_YARD.center),
    ]
    assert refinery_ore_distance(placed[0][1:], WEST_FIELD) <= REMOTE.refinery_ore_cells
    assert refinery_ore_distance(placed[1][1:], BEYOND_THE_SECOND_YARD) <= REMOTE.refinery_ore_cells


# Besides the ore 27 cells south of the own spawn, by which the second yard deploys, ore south-east of that, the main
# base's far field once the south field is reached, and ore south-west of it beyond the main base's 40-cell cap.
SOUTH_EAST_ORE = [(x, y) for y in (80, 81, 82) for x in (38, 39, 40)]
SOUTH_WEST_BEYOND_ORE = [(x, y) for y in (91, 92, 93) for x in (9, 10, 11)]


@pytest.fixture(scope="module")
def remote_yard_match() -> WholeMatch:
    """A whole fake match on a map with ore south, south-east and south-west of the own spawn, played with the second
    yard, the creep from the opening and the remote yard growth on."""
    tuning = tuning_off(placement=REMOTE, expansion=expansion_off(second_yard=True))
    ore = [*UNREACHABLE_SOUTH_ORE, *SOUTH_EAST_ORE, *SOUTH_WEST_BEYOND_ORE]
    return play_whole_match(tuning, game_config=with_ore(FakeRedAlert().game_config, ore), engine_reports=True)


def test_remote_yard_a_whole_fake_match_with_the_second_yard_runs_without_an_error_and_grows_on_both_lines(
    remote_yard_match,
):
    crept = remote_yard_match.data_of("creep_placement")
    yards = [(b["x"], b["y"]) for b in remote_yard_match.game.own_buildings() if b["type"] == "fact"]
    lines = {(event.get("yard"), tuple(event["target_patch_center"])) for event in crept}

    assert len(remote_yard_match.compute_ms) == WHOLE_MATCH_STEPS and remote_yard_match.data_of("error") == []
    assert remote_yard_match.compute_p95_ms <= COMPUTE_P95_MS
    assert len(yards) == 2 and cells.distance(*yards) > REMOTE.ring_max_cells
    assert {("main", (39, 81)), ("second", (10, 92))} <= lines


# -- refineries one to a field, before any field gets a second ---------------------------------------------------------

SPREAD = dataclasses.replace(TUNING, refinery_spread=True)
# The base once the opening is complete, with one refinery by the home field north of the yard; a field 11.0 cells east
# of the yard and one 11.2 cells west, both of which a refinery of the base can stand against, and one 16 cells south
# that none can.
SPREAD_BASE = (YARD, POWER, BARRACKS, ("proc", 25, 40), ("powr", 27, 49), ("weap", 16, 49))
SPREAD_EAST = square_patch((33, 47))
SPREAD_WEST = square_patch((11, 50))
SPREAD_TERRAIN = terrain_of(HOME_PATCH, SPREAD_EAST, SPREAD_WEST)


def test_refinery_spread_a_field_is_taken_once_a_refinery_stands_against_it_or_it_is_among_the_asked_fields():
    world = world_with(*SPREAD_BASE)

    assert placement.fields_against(box_at("proc", (24, 39)), SPREAD_TERRAIN, 2) == (HOME_PATCH,)
    assert placement.fields_against(box_at("proc", (40, 60)), SPREAD_TERRAIN, 2) == ()
    assert placement.taken_fields(world, SPREAD_TERRAIN, SPREAD, ()) == {HOME_PATCH}
    assert placement.taken_fields(world, SPREAD_TERRAIN, SPREAD, [SPREAD_EAST]) == {HOME_PATCH, SPREAD_EAST}
    # A refinery 3 cells from the ore does not stand against it.
    far = world_with(YARD, ("proc", 27, 40))
    assert ore_distance(box_at("proc", (26, 39)), HOME_PATCH) == 3
    assert placement.taken_fields(far, SPREAD_TERRAIN, SPREAD, ()) == frozenset()


def spread_agent(tuning: Tuning, base: tuple = SPREAD_BASE, terrain: Terrain = SPREAD_TERRAIN):
    """An agent that plays with the upkeep manager alone under a tuning, having seen this base once, on a map of this
    terrain."""
    agent = new_agent(UpkeepManager(), tuning=tuning)
    situations.ask(agent, england_base(*base, tick=FIRST_TICK))
    agent.ctx.terrain = terrain
    return agent


def asked_fields_after_placing(tuning: Tuning) -> tuple[list, list, list, dict]:
    """The asked fields an upkeep agent publishes before a ready refinery is placed, the cycle after, and once the
    promise of its place is over without its showing; and the place it was asked onto."""
    agent = spread_agent(tuning)
    before = list(agent.ctx.asked_refinery_fields)
    (order,) = situations.with_command(
        situations.ask(agent, england_base(*SPREAD_BASE, tick=1000, production=PROC_READY)), "place"
    )
    situations.ask(agent, england_base(*SPREAD_BASE, tick=1010))
    after = list(agent.ctx.asked_refinery_fields)
    situations.ask(agent, england_base(*SPREAD_BASE, tick=1000 + tuning.placement.promise_ticks))
    return before, after, list(agent.ctx.asked_refinery_fields), order


@pytest.mark.parametrize("tuning", [tuning_off(placement=SPREAD), tuning_off()], ids=["on", "off"])
def test_refinery_spread_the_fields_a_refinery_s_place_stands_against_are_asked_fields_for_promise_ticks(tuning):
    before, after, expired, order = asked_fields_after_placing(tuning)

    against = list(placement.fields_against(box_at("proc", tuple(order["at"])), SPREAD_TERRAIN, 2))
    assert before == [] and expired == [] and against != []
    assert after == (against if tuning.placement.refinery_spread else [])


def test_refinery_spread_the_field_a_claim_refinery_on_order_is_for_is_an_asked_field_until_it_is_placed():
    agent = claims_agent(tuning_off(placement=dataclasses.replace(FAR, refinery_spread=True), upkeep=CLAIMS.upkeep))
    orders = situations.ask(agent, england_base(*CLAIM_BASE, *EAST_WALK, tick=1010, cash=5000))
    on_order = [situations.job("Building", "proc")]

    situations.ask(agent, england_base(*CLAIM_BASE, *EAST_WALK, tick=1020, production=on_order))
    while_on_order = list(agent.ctx.asked_refinery_fields)
    situations.ask(agent, england_base(*CLAIM_BASE, *EAST_WALK, tick=1030 + FAR.promise_ticks))

    assert "proc" in builds_of(orders)
    assert while_on_order == [EAST_FAR_FIELD] and agent.ctx.asked_refinery_fields == []


SPREAD_UPKEEP = tuning_off(placement=SPREAD)
# A field 20 cells south of the yard, out of the reach of any place a refinery of the base could take.
SPREAD_FAR_SOUTH = FAR_SOUTH_FIELD


def place_refinery(agent, *shown, tick: int) -> tuple[int, int]:
    """The place an upkeep agent asks a ready refinery onto, in a state of these buildings."""
    (order,) = situations.with_command(
        situations.ask(agent, england_base(*shown, tick=tick, production=PROC_READY)), "place"
    )
    return tuple(order["at"])


def test_refinery_spread_after_the_opening_the_next_refinery_stands_against_the_nearest_untaken_field(match_record):
    agent = spread_agent(SPREAD_UPKEEP)

    cell = place_refinery(agent, *SPREAD_BASE, tick=1000)

    (event,) = match_record("refinery_field")
    assert cells.distance(SPREAD_EAST.center, YARD[1:]) < cells.distance(SPREAD_WEST.center, YARD[1:])
    assert refinery_ore_distance(cell, SPREAD_EAST) <= SPREAD.refinery_ore_cells
    assert (event["rule"], event["field_center"], event["served_by"]) == ("spread", list(SPREAD_EAST.center), 0)
    assert event["cell"] == list(cell)


def test_refinery_spread_with_a_refinery_asked_against_a_field_and_not_standing_the_next_goes_to_a_third_field():
    agent = spread_agent(SPREAD_UPKEEP)

    first = place_refinery(agent, *SPREAD_BASE, tick=1000)
    situations.ask(agent, england_base(*SPREAD_BASE, tick=1005))
    second = place_refinery(agent, *SPREAD_BASE, tick=1010)

    assert agent.ctx.asked_refinery_fields == [SPREAD_EAST]
    assert refinery_ore_distance(first, SPREAD_EAST) <= SPREAD.refinery_ore_cells
    assert refinery_ore_distance(second, SPREAD_WEST) <= SPREAD.refinery_ore_cells


def test_refinery_spread_a_refinery_asked_again_keeps_to_the_field_of_its_own_earlier_place():
    agent = spread_agent(SPREAD_UPKEEP)

    first = place_refinery(agent, *SPREAD_BASE, tick=1000)
    again = place_refinery(agent, *SPREAD_BASE, tick=1000 + SPREAD.place_again_after_ticks)

    assert again != first and refinery_ore_distance(again, SPREAD_EAST) <= SPREAD.refinery_ore_cells


def test_refinery_spread_with_every_standable_field_holding_one_a_second_goes_to_the_nearest_under_the_cap():
    terrain = terrain_of(HOME_PATCH, SPREAD_EAST, SPREAD_WEST, SPREAD_FAR_SOUTH)
    east = placement.spread_placement(world_with(*SPREAD_BASE), terrain, SPREAD, ()).cell
    west = placement.spread_placement(world_with(*SPREAD_BASE), terrain, SPREAD, [SPREAD_EAST]).cell
    one_each = world_with(*SPREAD_BASE, stands("proc", east), stands("proc", west))
    home_full = world_with(*SPREAD_BASE, stands("proc", east), stands("proc", west), ("proc", 19, 40))

    to_home = placement.spread_placement(one_each, terrain, SPREAD, ())
    after_home = placement.spread_placement(home_full, terrain, SPREAD, ())

    assert placement.taken_fields(one_each, terrain, SPREAD, ()) == {HOME_PATCH, SPREAD_EAST, SPREAD_WEST}
    assert to_home.field == placement.FieldChoice(HOME_PATCH, placement.FIELD_SPREAD, 1)
    assert refinery_ore_distance(to_home.cell, HOME_PATCH) <= SPREAD.refinery_ore_cells
    assert after_home.field == placement.FieldChoice(SPREAD_EAST, placement.FIELD_SPREAD, 1)


def test_refinery_spread_with_no_field_a_refinery_can_stand_against_there_is_no_spread_place():
    world = world_with(*SPREAD_BASE, ("proc", 18, 39))

    assert placement.spread_placement(world, terrain_of(HOME_PATCH, SPREAD_FAR_SOUTH), SPREAD, ()) is None
    assert placement.spread_placement(world_with(), SPREAD_TERRAIN, SPREAD, ()) is None


@pytest.mark.parametrize(
    "tuning, steps", [(tuning_off(), ()), (SPREAD_UPKEEP, ("proc", "weap"))], ids=["off", "opening"]
)
def test_refinery_spread_with_the_switch_off_or_during_the_opening_a_refinery_is_placed_as_today(
    tuning, steps, match_record
):
    agent = spread_agent(tuning)
    agent.ctx.opening_build = OpeningBuild(steps=steps)

    cell = place_refinery(agent, *SPREAD_BASE, tick=1000)

    assert cell == choose_placement("proc", world_with(*SPREAD_BASE), SPREAD_TERRAIN, TUNING).cell
    assert match_record("refinery_field") == []


def test_refinery_spread_a_dropped_claim_field_is_never_claimed_again_though_the_base_reaches_it_once_more():
    keeper = creep.NearestFarField()
    before, after = lane_world(*CLAIM_BASE, EAST_WALK[0]), lane_world(*CLAIM_BASE, *EAST_WALK)
    keeper.target(before, CLAIM_TERRAIN, FAR, (), True)
    keeper.target(after, CLAIM_TERRAIN, FAR, (), True)
    reached = list(keeper.claim_fields)

    keeper.drop({EAST_FAR_FIELD})
    keeper.target(before, CLAIM_TERRAIN, FAR, (), True)
    keeper.target(after, CLAIM_TERRAIN, FAR, (), True)

    assert reached == [EAST_FAR_FIELD] and keeper.claim_fields == []


SPREAD_CLAIMS = tuning_off(placement=dataclasses.replace(FAR, refinery_spread=True), upkeep=CLAIMS.upkeep)
PROC_ON_ORDER = [situations.job("Building", "proc")]


def claim_in_turn(tuning: Tuning) -> tuple[list, list, list]:
    """A claim refinery ordered, on order, placed and never showing: the claim fields after each step, the refinery
    builds ordered in each, and the place it was asked onto."""
    agent = claims_agent(tuning)
    walked, rich = (*CLAIM_BASE, *EAST_WALK), {"cash": 5000}
    steps = [(1010, ()), (1020, PROC_ON_ORDER), (1030, PROC_READY), (1040, ()), (1050 + FAR.promise_ticks, ())]
    claimed, ordered, placed = [], [], []
    for tick, production in steps:
        orders = situations.ask(agent, england_base(*walked, tick=tick, production=production, **rich))
        claimed.append(list(agent.ctx.claim_fields))
        ordered.append(builds_of(orders).count("proc"))
        placed += [tuple(order["at"]) for order in situations.with_command(orders, "place")]
    return claimed, ordered, placed


def test_refinery_spread_a_claim_field_a_refinery_is_on_order_and_asked_against_leaves_the_claims_for_good(
    match_record,
):
    claimed, ordered, placed = claim_in_turn(SPREAD_CLAIMS)

    (event,) = match_record("refinery_field")
    assert claimed == [[EAST_FAR_FIELD], [], [], [], []] and ordered == [1, 0, 0, 0, 0]
    assert refinery_ore_distance(placed[0], EAST_FAR_FIELD) <= FAR.refinery_ore_cells
    assert (event["rule"], event["field_center"]) == ("claim", list(EAST_FAR_FIELD.center))


def test_refinery_spread_with_the_switch_off_the_field_of_a_claim_refinery_just_placed_is_claimed_again():
    claimed, ordered, placed = claim_in_turn(CLAIMS)

    # A second claim refinery is ordered as soon as the first leaves the queue, before it could stand.
    assert claimed == [[EAST_FAR_FIELD]] * 5 and ordered[:4] == [1, 0, 0, 1]
    assert refinery_ore_distance(placed[0], EAST_FAR_FIELD) <= FAR.refinery_ore_cells


# Besides the ore east and west of the own spawn, ore south-east and north-west of it: five fields round the base.
SOUTH_EAST_NEAR_ORE = [(x, y) for y in (60, 61, 62) for x in (33, 34, 35)]
NORTH_WEST_NEAR_ORE = [(x, y) for y in (30, 31, 32) for x in (12, 13, 14)]


@pytest.fixture(scope="module")
def spread_match() -> WholeMatch:
    """A whole fake match on a map with five fields round the own spawn, played with the creep from the opening, the
    refinery claims and the refineries one to a field on."""
    ore = [*EAST_ORE, *WEST_ORE, *SOUTH_EAST_NEAR_ORE, *NORTH_WEST_NEAR_ORE]
    return play_whole_match(SPREAD_CLAIMS, game_config=with_ore(FakeRedAlert().game_config, ore))


def test_refinery_spread_a_whole_fake_match_runs_without_an_error_and_no_field_has_two_while_one_has_none(
    spread_match,
):
    world, terrain = spread_match.agent.world, spread_match.agent.ctx.terrain
    boxes = [box_at(b.type, (b.x, b.y)) for b in world.buildings.values() if b.type == "proc"]
    held = Counter(field for box in boxes for field in placement.fields_against(box, terrain, FAR.refinery_ore_cells))
    standable_and_empty = placement.claimable(terrain.ore_patches, world, terrain, FAR)
    claimed = [tuple(event["field_center"]) for event in spread_match.data_of("refinery_field")]

    assert len(spread_match.compute_ms) == WHOLE_MATCH_STEPS and spread_match.data_of("error") == []
    assert spread_match.compute_p95_ms <= COMPUTE_P95_MS
    assert len(held) >= 4 and (max(held.values()) < 2 or standable_and_empty == [])
    # No field was claimed or given a refinery one to a field twice.
    assert len(claimed) == len(set(claimed))


# -- growth that makes no headway gives its far field up ----------------------------------------------------------------

STALL = dataclasses.replace(CONTINUOUS, creep_stall_skip=True)
# The far field 20 cells west of the yard's middle, whose ore cell nearest it lies 19 cells off, and the next one 25
# cells south.
STALL_FIELD = WEST_OF_THE_MAIN_YARD
NEXT_AFTER_STALL = square_patch((22, 75))
STALL_TERRAIN = terrain_of(HOME_PATCH, STALL_FIELD, NEXT_AFTER_STALL)
# Power plants walking west toward it, each 3.5 cells or more nearer its ore than the one before.
WEST_STEPS = (("powr", 20, 49), ("powr", 16, 49), ("powr", 12, 49))
STALL_FROM = 1010
PROMISE = TUNING.promise_ticks
# A power plant being built: the stall rule counts only the time the Building queue is active.
BUILDING_ACTIVE = [situations.job("Building", "powr")]


class StallKeeper:
    """A creep keeper looked at in turn with worlds of the given buildings, as upkeep looks at it in each cycle."""

    def __init__(self, tuning: PlacementTuning = STALL, terrain: Terrain = STALL_TERRAIN) -> None:
        self.keeper, self.skipped, self.tuning, self.terrain = creep.NearestFarField(), [], tuning, terrain

    def cycle(self, tick: int, *buildings: tuple) -> OrePatch | None:
        """The far field after the stall check and the far field of a cycle at a tick, the Building queue active."""
        world = lane_world(*(buildings or (YARD,)), tick=tick, production=BUILDING_ACTIVE)
        self.keeper.skip_stalled(world, self.tuning, self.skipped)
        target = self.keeper.target(world, self.terrain, self.tuning, self.skipped)
        return None if target is None else target.patch


def test_creep_stall_three_placements_that_leave_the_best_distance_put_the_field_in_skipped_fields():
    stall = StallKeeper()
    first = [stall.cycle(tick) for tick in (1000, STALL_FROM)]
    for tick in (STALL_FROM, STALL_FROM + 10, STALL_FROM + 20):
        stall.keeper.note_line_placement(None, STALL_FIELD.center, tick)

    judged = [
        (stall.cycle(tick), list(stall.skipped)) for tick in (STALL_FROM + PROMISE + offset for offset in (0, 10))
    ]
    last = stall.cycle(STALL_FROM + PROMISE + 20)

    assert first == [STALL_FIELD, STALL_FIELD] and judged == [(STALL_FIELD, [])] * 2
    assert stall.skipped == [STALL_FIELD] and last == NEXT_AFTER_STALL


def test_creep_stall_placements_that_each_get_2_cells_closer_or_more_never_skip():
    stall = StallKeeper()
    stall.cycle(1000)
    stall.cycle(STALL_FROM)
    walked: tuple = ()
    for number, step in enumerate(WEST_STEPS):
        tick = STALL_FROM + 100 * number
        stall.keeper.note_line_placement(None, STALL_FIELD.center, tick)
        walked = (*walked, step)
        stall.cycle(tick + PROMISE, YARD, *walked)

    targets = [stall.cycle(STALL_FROM + 1000, YARD, *walked)]

    assert stall.skipped == [] and targets == [STALL_FIELD]


def test_creep_stall_3000_ticks_with_no_progress_and_no_placement_skip_the_field():
    stall = StallKeeper()
    stall.cycle(1000)
    stall.cycle(STALL_FROM)

    before = (stall.cycle(STALL_FROM + 2999), list(stall.skipped))
    after = stall.cycle(STALL_FROM + 3000)

    assert STALL.stall_seconds * 25 == 3000
    assert before == (STALL_FIELD, []) and (after, stall.skipped) == (NEXT_AFTER_STALL, [STALL_FIELD])


STALL_REMOTE = dataclasses.replace(REMOTE, creep_continuous=True, creep_stall_skip=True)


def test_creep_stall_the_second_line_s_stall_is_followed_apart_from_the_main_line_s():
    stall = StallKeeper(STALL_REMOTE, REMOTE_TERRAIN)
    yards = (YARD, FAR_SECOND_YARD)
    stall.cycle(1000, *yards)
    stall.cycle(STALL_FROM, *yards)
    for tick in (STALL_FROM, STALL_FROM + 10, STALL_FROM + 20):
        stall.keeper.note_line_placement(placement.SECOND_LINE, BY_THE_SECOND_YARD.center, tick)

    targets = [stall.cycle(STALL_FROM + PROMISE + offset, *yards) for offset in (0, 10, 20)]

    assert targets == [BETWEEN_THE_YARDS] * 3 and stall.skipped == [BY_THE_SECOND_YARD]


def test_creep_stall_with_the_switch_off_no_field_is_ever_skipped():
    stall = StallKeeper(CONTINUOUS)
    stall.cycle(1000)
    for tick in (STALL_FROM, STALL_FROM + 10, STALL_FROM + 20):
        stall.keeper.note_line_placement(None, STALL_FIELD.center, tick)

    targets = [stall.cycle(tick) for tick in (STALL_FROM + PROMISE + 20, STALL_FROM + 3000)]

    assert targets == [STALL_FIELD] * 2 and stall.skipped == []


def test_creep_stall_upkeep_skips_the_field_with_a_creep_placement_event_of_reason_stalled_skip(match_record):
    agent = creep_agent(STALL, UpkeepManager(), state=england_base(YARD, tick=FIRST_TICK), terrain=STALL_TERRAIN)
    for tick in (1000, STALL_FROM):
        situations.ask(agent, england_base(YARD, tick=tick, production=BUILDING_ACTIVE))

    situations.ask(agent, england_base(YARD, tick=STALL_FROM + 3000, production=BUILDING_ACTIVE))

    events = [event for event in match_record("creep_placement") if event["reason"] == "stalled_skip"]
    assert agent.ctx.skipped_fields == [STALL_FIELD]
    assert agent.ctx.creep_target == placement.CreepTarget(NEXT_AFTER_STALL, 1)
    assert events == [
        {
            "tick": STALL_FROM + 3000,
            "building": None,
            "cell": None,
            "field_center": [3, 50],
            "target_patch_center": [3, 50],
            "reason": "stalled_skip",
            "best_distance_cells": 18.01,
            "yard": "main",
        }
    ]


@pytest.fixture(scope="module")
def stall_match() -> WholeMatch:
    """A whole fake match on a map with fields east and west of the own spawn, ore out of the fake game's reach to the
    south and ore beyond the 40-cell cap, played with the creep from the opening, the continuous creep and the stall
    skip on."""
    ore = [*EAST_ORE, *WEST_ORE, *UNREACHABLE_SOUTH_ORE, *BEYOND_CAP_ORE]
    return play_whole_match(tuning_off(placement=STALL), game_config=with_ore(FakeRedAlert().game_config, ore))


def test_creep_stall_a_whole_fake_match_runs_without_an_error_and_gives_up_the_field_out_of_reach(stall_match):
    skips = [event for event in stall_match.data_of("creep_placement") if event["reason"] == "stalled_skip"]
    skipped = [field.center for field in stall_match.agent.ctx.skipped_fields]

    assert len(stall_match.compute_ms) == WHOLE_MATCH_STEPS and stall_match.data_of("error") == []
    assert stall_match.compute_p95_ms <= COMPUTE_P95_MS
    assert skips and skips[0]["target_patch_center"] == UNREACHABLE_SOUTH_CENTER
    assert [tuple(event["target_patch_center"]) for event in skips] == skipped


# -- the second yard's line grows from the second yard ------------------------------------------------------------------

SECOND_LOCAL = dataclasses.replace(REMOTE, second_line_local=True)
# A main yard and a second yard 18 cells south-west of it, as in the watched game, with main-base buildings north of
# the main yard; the main base's far field east of it, and the second yard's north of the main base, so that the
# second yard's line runs past the main base's buildings.
LOCAL_MAIN_YARD, LOCAL_SECOND_YARD = ("fact", 24, 45), ("fact", 9, 55)
LOCAL_BASE = (
    LOCAL_MAIN_YARD,
    LOCAL_SECOND_YARD,
    ("powr", 29, 45),
    ("tent", 21, 45),
    ("powr", 22, 40),
    ("powr", 23, 36),
)
NORTH_OF_THE_MAIN_BASE = square_patch((25, 25))
EAST_OF_THE_MAIN_BASE = square_patch((44, 40))
LOCAL_TERRAIN = terrain_of(NORTH_OF_THE_MAIN_BASE, EAST_OF_THE_MAIN_BASE)
# How far from the second yard a place of its line may lie: second_line_radius_cells + adjacent_cells + 3.
LOCAL_REACH = 15 + 2 + 3


def second_line_power_plant(tuning: PlacementTuning, terrain: Terrain = LOCAL_TERRAIN) -> placement.PlacementChoice:
    """Where a power plant that takes the second yard's line is asked onto in the base with the two yards."""
    return choose_placement("powr", world_with(*LOCAL_BASE), terrain, tuning, second_line=True)


def test_second_line_local_a_second_line_power_plant_stands_within_20_cells_of_the_second_yard():
    local, today = second_line_power_plant(SECOND_LOCAL), second_line_power_plant(REMOTE)

    assert local.creep.yard == today.creep.yard == "second"
    assert local.creep.field_center == NORTH_OF_THE_MAIN_BASE.center
    assert cells.is_within(local.cell, LOCAL_SECOND_YARD[1:], LOCAL_REACH)
    # Without the switch the place is the one by the main base farthest along the line, 27.8 cells from the yard.
    assert not cells.is_within(today.cell, LOCAL_SECOND_YARD[1:], LOCAL_REACH)


def test_second_line_local_a_place_next_to_a_building_of_the_main_base_is_never_a_second_line_candidate():
    today = second_line_power_plant(REMOTE).cell
    by_the_main_base = [spec for spec in LOCAL_BASE if spec[1:] != LOCAL_SECOND_YARD[1:]]

    assert any(gap_between(box_at("powr", today), box_at(*spec[:1], spec[1:])) <= 2 for spec in by_the_main_base)
    assert second_line_power_plant(SECOND_LOCAL).cell != today


def test_second_line_local_with_the_second_yard_s_area_blocked_the_building_goes_on_the_main_line():
    around_the_second_yard = frozenset(
        (x, y) for x in range(-12, 31) for y in range(34, 77) if cells.is_within((x, y), LOCAL_SECOND_YARD[1:], 20)
    )
    blocked = Terrain(LOCAL_TERRAIN.ore_patches, LOCAL_TERRAIN.blocked | around_the_second_yard)

    choice = second_line_power_plant(SECOND_LOCAL, blocked)

    assert choice.creep.yard == "main" and choice.creep.field_center == EAST_OF_THE_MAIN_BASE.center


@pytest.mark.parametrize("item", ["powr", "tent", "proc", "pbox"])
def test_second_line_local_with_one_yard_or_on_the_main_line_placement_is_as_with_the_switch_off(item):
    two, one = world_with(*LOCAL_BASE), world_with(LOCAL_MAIN_YARD, *LOCAL_BASE[2:])

    assert choose_placement(item, two, LOCAL_TERRAIN, SECOND_LOCAL) == choose_placement(
        item, two, LOCAL_TERRAIN, REMOTE
    )
    on_one = choose_placement(item, one, LOCAL_TERRAIN, SECOND_LOCAL, second_line=True)
    assert on_one == choose_placement(item, one, LOCAL_TERRAIN, REMOTE, second_line=True)


@pytest.fixture(scope="module")
def second_line_local_match() -> WholeMatch:
    """A whole fake match on the map with ore south, south-east and south-west of the own spawn, played with the second
    yard, the creep from the opening, the remote yard growth and the second line local on."""
    tuning = tuning_off(placement=SECOND_LOCAL, expansion=expansion_off(second_yard=True))
    ore = [*UNREACHABLE_SOUTH_ORE, *SOUTH_EAST_ORE, *SOUTH_WEST_BEYOND_ORE]
    return play_whole_match(tuning, game_config=with_ore(FakeRedAlert().game_config, ore), engine_reports=True)


def test_second_line_local_a_whole_fake_match_places_every_second_line_building_within_20_cells_of_the_second_yard(
    second_line_local_match,
):
    match = second_line_local_match
    first, second = [(b["x"], b["y"]) for b in match.game.own_buildings() if b["type"] == "fact"]
    on_the_second_line = [event for event in match.data_of("creep_placement") if event.get("yard") == "second"]

    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.compute_p95_ms <= COMPUTE_P95_MS
    assert cells.distance(first, second) > SECOND_LOCAL.ring_max_cells and on_the_second_line
    assert all(cells.is_within(tuple(event["cell"]), second, LOCAL_REACH) for event in on_the_second_line)


# -- growth along a walked route ----------------------------------------------------------------------------------------

WALKED = dataclasses.replace(FAR, walked_routes=True)
# A wall of cliff east of the yard across the straight line from the yard's middle (22, 49) to the far field's ore
# cell (37, 50), and the L-shaped way a rifleman walked round it from the guard: north, east, then south to the field.
CLIFF = frozenset((x, y) for x in range(29, 35) for y in range(45, 56))
CLIFF_TERRAIN = Terrain((HOME_PATCH, EAST_15_FIELD), HOME_PATCH.cells | EAST_15_FIELD.cells | CLIFF)
L_ROUTE = ((25, 47), (25, 45), (25, 43), (27, 42), (29, 42), (31, 42), (33, 42), (35, 42), (36, 44), (37, 46), (37, 48))
# The base so far: the yard and a power plant north-east of it, from which the places along the L's top lie.
L_BASE = (YARD, ("powr", 27, 45))


def along_the_way(point: tuple[float, float], way: tuple) -> tuple[float, float]:
    """How far along a way through the cells of `way` its point nearest `point` lies, and how far from it."""
    best, walked = (math.inf, 0.0), 0.0
    for start, end in zip(way, way[1:]):
        along, off = segment_along_and_off(point, start, end)
        best = min(best, (off, walked + along))
        walked += math.dist(start, end)
    return best[1], best[0]


def segment_along_and_off(point, start, end) -> tuple[float, float]:
    """How far along the segment from `start` to `end` the point of it nearest `point` lies, and how far from it."""
    length = math.dist(start, end)
    if length == 0:
        return 0.0, math.dist(point, start)
    share = ((point[0] - start[0]) * (end[0] - start[0]) + (point[1] - start[1]) * (end[1] - start[1])) / length**2
    share = min(max(share, 0.0), 1.0)
    nearest = (start[0] + share * (end[0] - start[0]), start[1] + share * (end[1] - start[1]))
    return share * length, math.dist(point, nearest)


def test_walked_route_the_way_starts_straight_from_the_line_s_start_to_the_route_s_point_nearest_it():
    route = ((28, 46), (25, 47), (25, 45), (27, 42))

    assert placement.walked_line(FAR_ORIGIN, route) == ((22, 49), (25, 47), (25, 45), (27, 42))
    assert placement.walked_line((25, 47), route) == ((25, 47), (25, 47), (25, 45), (27, 42))


def test_walked_route_on_an_l_round_a_cliff_the_building_stands_along_the_l_not_on_the_straight_line_to_the_cliff():
    world, routes = world_with(*L_BASE), {EAST_15_FIELD: L_ROUTE}

    straight = choose_placement("powr", world, CLIFF_TERRAIN, WALKED)
    walked = choose_placement("powr", world, CLIFF_TERRAIN, WALKED, routes=routes)

    way = placement.walked_line(FAR_ORIGIN, L_ROUTE)
    ore = EAST_15_FIELD.nearest_cell(FAR_ORIGIN)
    places = candidate_cells("powr", world, CLIFF_TERRAIN, WALKED)
    in_band = [cell for cell in places if along_the_way(box_at("powr", cell).center, way)[1] <= WALKED.creep_line_cells]
    farthest = max(along_the_way(box_at("powr", cell).center, way)[0] for cell in in_band)
    assert straight.cell == (27, 48) and along_and_off("powr", straight.cell, FAR_ORIGIN, ore)[1] <= 2
    assert walked.cell == (29, 41) and along_the_way(box_at("powr", walked.cell).center, way) == (farthest, 0.0)
    # The L's top runs 7 cells north of the straight line, which the cliff cuts.
    assert along_and_off("powr", walked.cell, FAR_ORIGIN, ore)[1] == 7.0
    assert any(cell in CLIFF for cell in cells.line(FAR_ORIGIN, ore)) and not set(L_ROUTE) & CLIFF
    assert walked.creep == placement.Creep(
        EAST_15_FIELD.center,
        ore,
        middle_distance("powr", candidate_cells("powr", world, CLIFF_TERRAIN, WALKED)[0], ore),
        middle_distance("powr", walked.cell, ore),
        line_distance=0.0,
    )


@pytest.mark.parametrize(
    "routes",
    [{}, {WEST_OF_THE_MAIN_YARD: ((22, 49), (3, 50))}],
    ids=["no route yet", "a route to another field"],
)
def test_walked_route_without_a_route_to_the_far_field_the_line_is_the_straight_one(routes):
    world = world_with(*L_BASE)

    assert choose_placement("powr", world, CLIFF_TERRAIN, WALKED, routes=routes) == choose_placement(
        "powr", world, CLIFF_TERRAIN, FAR
    )


def test_walked_route_the_second_yard_s_line_starts_straight_from_the_second_yard_s_middle():
    world = world_with(YARD, FAR_SECOND_YARD)
    remote = dataclasses.replace(REMOTE, walked_routes=True)
    second_middle = SECOND_ORIGIN
    # A route to the second yard's field that bends south of it before it turns north-east to the field (40, 80).
    route = ((20, 84), (24, 86), (30, 86), (36, 84), (38, 81))

    straight = choose_placement("powr", world, REMOTE_TERRAIN, remote, second_line=True)
    walked = choose_placement(
        "powr", world, REMOTE_TERRAIN, remote, second_line=True, routes={BY_THE_SECOND_YARD: route}
    )

    way = placement.walked_line(second_middle, route)
    assert way[:2] == (second_middle, (20, 84)) and walked.creep.yard == straight.creep.yard == "second"
    assert walked.cell != straight.cell
    assert along_the_way(box_at("powr", walked.cell).center, way)[1] == pytest.approx(walked.creep.line_distance)
    assert walked.creep.line_distance <= 2


def test_walked_route_once_the_walker_arrived_upkeep_places_the_next_power_plant_along_the_l(match_record):
    rifle = (25, 47)
    first = england_base(*L_BASE, tick=FIRST_TICK, units=[situations.unit(200, "e1", *rifle)])
    agent = creep_agent(WALKED, UpkeepManager(), state=first, terrain=CLIFF_TERRAIN)
    sent = situations.ask(agent, england_base(*L_BASE, tick=1000, units=[situations.unit(200, "e1", *rifle)]))
    for step, cell in enumerate(L_ROUTE[1:], start=1):
        walker = situations.unit(200, "e1", *cell, idle=False)
        situations.ask(agent, england_base(*L_BASE, tick=1000 + 5 * step, units=[walker]))

    placed = situations.ask(agent, england_base(*L_BASE, tick=1100, production=POWER_READY_TO_PLACE))

    assert situations.with_command(sent, "move") == [{"cmd": "move", "units": [200], "to": [37, 49]}]
    assert [event["phase"] for event in match_record("route_walk")] == ["sent", "done"]
    assert situations.with_command(placed, "place") == [{"cmd": "place", "item": "powr", "at": [29, 41]}]
    (event,) = match_record("creep_placement")
    assert (event["reason"], event["line_distance_cells"]) == ("nearest_far_field", 0.0)


# -- refineries to unserved fields -------------------------------------------------------------------------------------

UNSERVED = dataclasses.replace(TUNING, refinery_unserved_only=True)
UNSERVED_UPKEEP = tuning_off(placement=UNSERVED)
# The base once the opening is complete, without a refinery: the yard, its power plants, barracks and war factory.
UNSERVED_BASE = (YARD, POWER, BARRACKS, ("powr", 26, 48), ("weap", 15, 48))
# A refinery serving no field, south-west of the yard: with it two own refineries stand, and the rule applies.
ELSEWHERE = ("proc", 20, 56)
# A home field north of the yard, 30 cells, served by the refinery below its west end.
HOME_30 = field_of(rectangle(19, 37, 10, 3))
HOME_REFINERY = ("proc", 19, 40)
# A field of 45 cells east of the yard (13.0 cells from it), and the field of 9 cells west of it (11.2 cells).
EAST_45 = field_of(rectangle(31, 45, 9, 5))
# A home field of 98 cells north of the yard served by the refinery below its west end, and one of 32 cells.
HOME_98 = field_of(rectangle(15, 33, 14, 7))
HOME_32 = field_of(rectangle(15, 36, 8, 4))
HOME_60 = field_of(rectangle(15, 35, 12, 5))
HOME_WEST_REFINERY = ("proc", 15, 40)
# A field of 36 cells east of the yard.
EAST_36 = field_of(rectangle(31, 45, 9, 4))


def unserved_agent(*base, terrain: Terrain, tuning: Tuning = UNSERVED_UPKEEP, **observation):
    """An upkeep agent under a tuning with refinery_unserved_only on that has seen this base once on this terrain."""
    agent = new_agent(UpkeepManager(), tuning=tuning)
    situations.ask(agent, england_base(*base, tick=FIRST_TICK, **observation))
    agent.ctx.terrain = terrain
    return agent


def middle_of(cell: tuple[int, int]) -> tuple[float, float]:
    return box_at("proc", cell).center


def test_refinery_unserved_the_next_refinery_goes_to_the_45_cell_field_not_the_nearer_9_cell_one():
    terrain = terrain_of(HOME_30, EAST_45, SPREAD_WEST)
    agent = unserved_agent(*UNSERVED_BASE, HOME_REFINERY, ELSEWHERE, terrain=terrain)

    choice = refinery_fields.choose(agent.world, agent.ctx)
    served = refinery_fields.served(agent.world, terrain, 2, ())

    assert (EAST_45.size, SPREAD_WEST.size) == (45, 9)
    assert cells.distance(SPREAD_WEST.center, YARD[1:]) < cells.distance(EAST_45.center, YARD[1:])
    assert (served[HOME_30], served[EAST_45], served[SPREAD_WEST]) == (1, 0, 0)
    assert choice.field.patch == EAST_45 and choice.field.rule == "unserved"
    assert refinery_ore_distance(choice.cell, EAST_45) <= 2


def test_refinery_unserved_a_refinery_asked_against_a_field_serves_it():
    terrain = terrain_of(HOME_30, EAST_45)
    agent = unserved_agent(*UNSERVED_BASE, HOME_REFINERY, ELSEWHERE, terrain=terrain)

    assert refinery_fields.served(agent.world, terrain, 2, [EAST_45])[EAST_45] == 1
    assert refinery_fields.choose(agent.world, agent.ctx, asked=[EAST_45]) is None


def test_refinery_unserved_a_98_cell_field_served_once_gets_a_second_6_cells_or_more_from_the_first():
    agent = unserved_agent(*UNSERVED_BASE, HOME_WEST_REFINERY, ELSEWHERE, terrain=terrain_of(HOME_98))

    choice = refinery_fields.choose(agent.world, agent.ctx)

    assert HOME_98.size == 98 and refinery_fields.served(agent.world, terrain_of(HOME_98), 2, ())[HOME_98] == 1
    assert choice.field.patch == HOME_98 and choice.field.rule == "second"
    assert refinery_ore_distance(choice.cell, HOME_98) <= 2
    assert math.dist(middle_of(choice.cell), middle_of(HOME_WEST_REFINERY[1:])) >= 6


def test_refinery_unserved_a_32_cell_field_served_once_gets_no_second_and_one_served_twice_no_third():
    small = unserved_agent(*UNSERVED_BASE, HOME_WEST_REFINERY, ELSEWHERE, terrain=terrain_of(HOME_32))
    twice = unserved_agent(*UNSERVED_BASE, HOME_WEST_REFINERY, ("proc", 24, 40), ELSEWHERE, terrain=terrain_of(HOME_98))

    assert HOME_32.size == 32 and refinery_fields.choose(small.world, small.ctx) is None
    assert refinery_fields.served(twice.world, terrain_of(HOME_98), 2, ())[HOME_98] == 2
    assert refinery_fields.choose(twice.world, twice.ctx) is None


def test_refinery_unserved_a_place_within_8_5_cells_of_a_known_gun_turret_is_not_used():
    terrain = terrain_of(HOME_30, EAST_45)
    clear = unserved_agent(*UNSERVED_BASE, HOME_REFINERY, ELSEWHERE, terrain=terrain)
    free = refinery_fields.choose(clear.world, clear.ctx)
    box = box_at("proc", free.cell)
    # A gun 8 cells east of the free place's east side: its range of 6.5 and the margin of 2 reach the place.
    gun = (box.left + box.width - 1 + 8, box.top)
    seen = {"visible_enemy_buildings": [{"id": 900, "type": "gun", "x": gun[0], "y": gun[1], "hp": 100}]}
    agent = unserved_agent(*UNSERVED_BASE, HOME_REFINERY, ELSEWHERE, terrain=terrain, **seen)

    guarded = refinery_fields.choose(agent.world, agent.ctx)

    assert agent.world.items.get("gun").range == 6.5
    assert guarded.field.patch == EAST_45 and guarded.cell != free.cell
    assert min(math.dist(gun, cell) for cell in box_at("proc", guarded.cell).cells()) > 8.5
    assert min(math.dist(gun, cell) for cell in box.cells()) <= 8.5


def test_refinery_unserved_a_36_cell_field_qualifies_for_a_refinery_and_for_an_mcv_site():
    agent = unserved_agent(*UNSERVED_BASE, HOME_REFINERY, ELSEWHERE, terrain=terrain_of(HOME_30, EAST_36))

    choice = refinery_fields.choose(agent.world, agent.ctx)

    assert EAST_36.size == 36 and Tuning().expansion.mcv_site_min_ore_cells == 30
    assert choice.field.patch == EAST_36 and choice.field.rule == "unserved"
    assert expansion.ore_cells(EAST_36, agent.ctx) >= agent.ctx.tuning.expansion.mcv_site_min_ore_cells
    assert expansion._lasts(EAST_36, [], agent.ctx)


def test_refinery_unserved_the_opening_s_two_refineries_go_on_the_60_cell_home_field_6_cells_apart(match_record):
    agent = unserved_agent(*UNSERVED_BASE, HOME_WEST_REFINERY, terrain=terrain_of(HOME_60))

    cell = place_refinery(agent, *UNSERVED_BASE, HOME_WEST_REFINERY, tick=1000)

    assert HOME_60.size == 60 and refinery_ore_distance(cell, HOME_60) <= 2
    assert math.dist(middle_of(cell), middle_of(HOME_WEST_REFINERY[1:])) >= 6
    # The opening's place by the yard lies too near the west refinery, so it is spaced from it on the same field.
    assert [(event["cell"], event["field_center"]) for event in match_record("refinery_field")] == [
        (list(cell), list(HOME_60.center))
    ]


def test_refinery_unserved_a_refinery_after_the_opening_s_two_is_placed_against_the_unserved_field(match_record):
    terrain = terrain_of(HOME_30, EAST_45, SPREAD_WEST)
    agent = unserved_agent(*UNSERVED_BASE, HOME_REFINERY, ELSEWHERE, terrain=terrain)

    cell = place_refinery(agent, *UNSERVED_BASE, HOME_REFINERY, ELSEWHERE, tick=1000)

    (event,) = [event for event in match_record("refinery_field") if event["cell"] is not None]
    assert refinery_ore_distance(cell, EAST_45) <= 2
    assert (event["rule"], event["field_center"], event["field_cells"]) == ("unserved", list(EAST_45.center), 45)


def test_refinery_unserved_a_finished_refinery_with_no_field_to_go_to_waits_unplaced():
    agent = unserved_agent(*UNSERVED_BASE, HOME_WEST_REFINERY, ("proc", 24, 40), ELSEWHERE, terrain=terrain_of(HOME_60))

    shown = (*UNSERVED_BASE, HOME_WEST_REFINERY, ("proc", 24, 40), ELSEWHERE)
    orders = situations.ask(agent, england_base(*shown, tick=1000, production=PROC_READY))

    assert situations.with_command(orders, "place") == []


# Four harvesters at work: with two refineries standing, every one has its harvesters, and one more is due.
AT_WORK = [situations.unit(300 + number, "harv", 40, 20, idle=False) for number in range(4)]
# The base with its service depot, which upkeep would otherwise order first, the power plants it needs, and two
# refineries on the home field.
FULL_HOME = (*UNSERVED_BASE, ("fix", 15, 53), ("powr", 26, 52), ("powr", 12, 53), HOME_WEST_REFINERY, ("proc", 24, 40))


def refinery_orders(agent, *base, ticks) -> list[list[str]]:
    """The items an upkeep agent orders built at each tick, in a state of these buildings with four harvesters."""
    return [builds_of(situations.ask(agent, england_base(*base, tick=tick, units=AT_WORK))) for tick in ticks]


def test_refinery_unserved_with_no_qualifying_field_no_refinery_is_ordered_and_waiting_unserved_is_written(
    match_record,
):
    agent = unserved_agent(*FULL_HOME, terrain=terrain_of(HOME_60, SPREAD_WEST))

    ordered = refinery_orders(agent, *FULL_HOME, ticks=[1000, 1005])

    waiting = [event for event in match_record("refinery_field") if event.get("reason") == "waiting_unserved"]
    assert ["proc" in items for items in ordered] == [False, False]
    assert [event["tick"] for event in waiting] == [995]
    empty = dict.fromkeys(("cell", "rule", "field_center", "field_cells", "served_by", "ore_distance_cells"))
    assert waiting[0] == {**empty, "tick": 995, "reason": "waiting_unserved"}


def test_refinery_unserved_with_a_45_cell_field_unserved_the_refinery_due_is_ordered():
    agent = unserved_agent(*FULL_HOME, terrain=terrain_of(HOME_60, EAST_45))

    (ordered,) = refinery_orders(agent, *FULL_HOME, ticks=[1000])

    assert "proc" in ordered


def test_refinery_unserved_with_the_switch_off_the_refinery_due_is_ordered_whatever_the_fields():
    off = unserved_agent(*FULL_HOME, terrain=terrain_of(HOME_60, SPREAD_WEST), tuning=tuning_off())

    (ordered,) = refinery_orders(off, *FULL_HOME, ticks=[1000])

    assert "proc" in ordered


def test_refinery_unserved_waiting_unserved_is_written_once_a_minute_at_most(match_record):
    places, world = refinery_fields.UnservedPlaces(), world_with(YARD)

    for tick in (100, 1599, 1600, 1700):
        world.tick = tick
        places.note_waiting(world)

    assert [event["tick"] for event in match_record("refinery_field")] == [100, 1600]


# -- a refinery's footprint from its reported cell ---------------------------------------------------------------------

# A row of cells nothing can be built on right below the 32-cell home field, so that the nearest a refinery stands to
# its ore is 2 cells, the most that still serves it.
BELOW_HOME_32 = frozenset((x, 40) for x in range(5, 40))
FOOTPRINT_TERRAIN = Terrain((HOME_32, EAST_45), HOME_32.cells | EAST_45.cells | BELOW_HOME_32)
# A refinery the state reports at (19, 42), whose footprint's top-left cell is (18, 41).
REPORTED_PROC = ("proc", 19, 42)


def reported(cell: tuple[int, int]) -> tuple[int, int]:
    """Where the state reports a refinery whose footprint's top-left cell is a cell: the middle cell, one right and
    one down of it."""
    return cell[0] + 1, cell[1] + 1


def the_refinery(agent):
    (refinery,) = [building for building in agent.world.buildings.values() if building.type == "proc"]
    return refinery


def test_refinery_footprint_a_refinery_reported_3_cells_from_the_ore_with_its_corner_2_away_serves_the_field():
    agent = unserved_agent(*UNSERVED_BASE, terrain=FOOTPRINT_TERRAIN)
    cell = place_refinery(agent, *UNSERVED_BASE, tick=1000)
    situations.ask(agent, england_base(*UNSERVED_BASE, ("proc", *reported(cell)), tick=1010))

    served = refinery_fields.served(agent.world, FOOTPRINT_TERRAIN, 2, ())

    assert refinery_ore_distance(reported(cell), HOME_32) == 3 and refinery_ore_distance(cell, HOME_32) == 2
    assert refinery_fields.footprint_cell(the_refinery(agent)) == cell
    assert served[HOME_32] == 1 and served[EAST_45] == 0


def test_refinery_footprint_then_the_32_cell_field_gets_no_further_refinery_while_an_unserved_field_is_reachable():
    agent = unserved_agent(*UNSERVED_BASE, terrain=FOOTPRINT_TERRAIN)
    cell = place_refinery(agent, *UNSERVED_BASE, tick=1000)
    standing = (*UNSERVED_BASE, ("proc", *reported(cell)), ELSEWHERE)
    situations.ask(agent, england_base(*standing, tick=1010))
    # Once the first refinery's place is no longer promised, the field counts as served only by the refinery itself.
    after_promise = 1000 + agent.ctx.tuning.placement.promise_ticks

    third = place_refinery(agent, *standing, tick=after_promise)

    assert agent.ctx.asked_refinery_fields == []
    assert HOME_32.size == 32 < agent.ctx.tuning.placement.refinery_second_min_cells
    assert refinery_ore_distance(third, EAST_45) <= 2 and refinery_ore_distance(third, HOME_32) > 2


def test_refinery_footprint_a_refinery_known_only_from_the_state_uses_its_reported_cell_less_1_1():
    agent = unserved_agent(*UNSERVED_BASE, REPORTED_PROC, terrain=FOOTPRINT_TERRAIN)

    served = refinery_fields.served(agent.world, FOOTPRINT_TERRAIN, 2, ())

    assert refinery_fields.footprint_cell(the_refinery(agent)) == (18, 41)
    assert refinery_ore_distance(REPORTED_PROC[1:], HOME_32) == 3 and served[HOME_32] == 1


def test_refinery_footprint_a_refinery_the_engine_moved_is_taken_where_it_stands_not_where_it_was_asked():
    agent = unserved_agent(*UNSERVED_BASE, terrain=FOOTPRINT_TERRAIN)
    asked = place_refinery(agent, *UNSERVED_BASE, tick=1000)
    # The engine put it one cell lower than asked; the state reports the middle cell of where it stands.
    stands = (asked[0], asked[1] + 1)
    situations.ask(agent, england_base(*UNSERVED_BASE, ("proc", *reported(stands)), tick=1010))

    served = refinery_fields.served(agent.world, FOOTPRINT_TERRAIN, 2, ())

    assert refinery_ore_distance(asked, HOME_32) == 2 and refinery_ore_distance(stands, HOME_32) == 3
    assert refinery_fields.footprint_cell(the_refinery(agent)) == stands
    assert served[HOME_32] == 0


# -- one refinery to a field -------------------------------------------------------------------------------------------

# The shipped field values: one refinery a field, no second on a field of a known map, and a refinery serving the field
# whose ore lies nearest it within 5 cells.
ONE_PER_FIELD = dataclasses.replace(
    UNSERVED,
    max_refineries_per_field=PlacementTuning().max_refineries_per_field,
    refinery_second_min_cells=PlacementTuning().refinery_second_min_cells,
    refinery_serves_cells=PlacementTuning().refinery_serves_cells,
)
ONE_PER_FIELD_UPKEEP = tuning_off(placement=ONE_PER_FIELD)
# A home field of 100 cells north of the yard, served by the refinery below its west end.
HOME_100 = field_of(rectangle(15, 30, 10, 10))
# A refinery east of the 45-cell field whose footprint's west side stands 3 cells from its ore: reported at (43, 46),
# its footprint's top-left cell is (42, 45).
EAST_3_AWAY = ("proc", 43, 46)


def test_one_per_field_a_100_cell_home_field_served_once_gets_a_second_only_when_no_unserved_field_can_be_stood_against():
    alone = unserved_agent(
        *UNSERVED_BASE, HOME_WEST_REFINERY, ELSEWHERE, terrain=terrain_of(HOME_100), tuning=ONE_PER_FIELD_UPKEEP
    )
    with_east = unserved_agent(
        *UNSERVED_BASE,
        HOME_WEST_REFINERY,
        ELSEWHERE,
        terrain=terrain_of(HOME_100, EAST_45),
        tuning=ONE_PER_FIELD_UPKEEP,
    )

    choice = refinery_fields.choose(with_east.world, with_east.ctx)

    stuck = refinery_fields.choose(alone.world, alone.ctx)

    assert HOME_100.size == 100 and refinery_fields.served(alone.world, terrain_of(HOME_100), 5, ())[HOME_100] == 1
    assert (stuck.field.patch, stuck.field.rule) == (HOME_100, "second_when_stuck")
    assert choice.field.patch == EAST_45 and choice.field.rule == "unserved"


@pytest.mark.parametrize("field, ordered", [(HOME_60, False), (HOME_100, True)], ids=["60 cells", "100 cells"])
def test_one_per_field_with_no_unserved_field_a_home_field_under_80_cells_s_refinery_due_waits_unserved(
    field, ordered, match_record
):
    home = (*UNSERVED_BASE, ("fix", 15, 53), ("powr", 26, 52), ("powr", 12, 53), HOME_WEST_REFINERY, ELSEWHERE)
    agent = unserved_agent(*home, terrain=terrain_of(field), tuning=ONE_PER_FIELD_UPKEEP)

    orders = refinery_orders(agent, *home, ticks=[1000])

    waiting = [event for event in match_record("refinery_field") if event.get("reason") == "waiting_unserved"]
    assert ["proc" in items for items in orders] == [ordered]
    # The one waiting_unserved event was written as the agent first saw the base, on the map's own fields.
    assert [event["tick"] for event in waiting] == [FIRST_TICK]


def test_one_per_field_a_refinery_3_cells_from_a_field_s_ore_serves_it_so_the_field_gets_no_second():
    terrain = terrain_of(HOME_30, EAST_45)
    agent = unserved_agent(*UNSERVED_BASE, HOME_REFINERY, EAST_3_AWAY, terrain=terrain, tuning=ONE_PER_FIELD_UPKEEP)

    footprint = refinery_fields.footprint_cell(agent.world.buildings[the_proc_id(agent, EAST_3_AWAY)])

    assert refinery_ore_distance(footprint, EAST_45) == 3
    assert refinery_fields.served(agent.world, terrain, 2, ())[EAST_45] == 0
    assert refinery_fields.served(agent.world, terrain, 5, ())[EAST_45] == 1
    assert refinery_fields.choose(agent.world, agent.ctx) is None


# A field of 85 ore cells north of the yard, served by the refinery below its west end, and the 36-cell field east.
HOME_85 = field_of(rectangle(15, 35, 17, 5))
# The shipped size from which a field served once takes a second refinery while no unserved field can be stood against.
STUCK_CELLS = 80


def test_one_per_field_stuck_an_85_cell_field_with_one_refinery_takes_a_second_only_with_no_unserved_field_in_reach():
    stuck = unserved_agent(*UNSERVED_BASE, HOME_WEST_REFINERY, terrain=terrain_of(HOME_85), tuning=ONE_PER_FIELD_UPKEEP)
    with_east = unserved_agent(
        *UNSERVED_BASE, HOME_WEST_REFINERY, terrain=terrain_of(HOME_85, EAST_36), tuning=ONE_PER_FIELD_UPKEEP
    )

    second = refinery_fields.choose(stuck.world, stuck.ctx)
    unserved = refinery_fields.choose(with_east.world, with_east.ctx)

    others = [middle_of(HOME_WEST_REFINERY[1:])]
    assert HOME_85.size == 85 and PlacementTuning().refinery_second_when_stuck_cells == STUCK_CELLS
    assert PlacementTuning().refinery_second_min_cells == 9999
    assert refinery_fields.counts(stuck.world, stuck.ctx, ())[HOME_85] == 1
    assert (second.field.patch, second.field.rule) == (HOME_85, "second_when_stuck")
    assert all(math.dist(middle_of(second.cell), other) >= 6 for other in others)
    assert (unserved.field.patch, unserved.field.rule) == (EAST_36, "unserved")


def test_one_per_field_stuck_a_field_under_80_cells_or_one_with_two_refineries_takes_none():
    small = unserved_agent(*UNSERVED_BASE, HOME_WEST_REFINERY, terrain=terrain_of(HOME_60), tuning=ONE_PER_FIELD_UPKEEP)
    # A second refinery on the 85-cell field's east end, spaced from the first.
    full = unserved_agent(
        *UNSERVED_BASE, HOME_WEST_REFINERY, ("proc", 30, 37), terrain=terrain_of(HOME_85), tuning=ONE_PER_FIELD_UPKEEP
    )

    assert HOME_60.size == 60 and refinery_fields.choose(small.world, small.ctx) is None
    assert refinery_fields.counts(full.world, full.ctx, ())[HOME_85] == 2
    assert refinery_fields.choose(full.world, full.ctx) is None


# The west, home and south fields of the map of game 20261013T142816Z (sidestep), by their rows of ore cells
# (y, first x, last x): 33, 100 and 47 cells.
def rows_field(rows: list[tuple[int, int, int]]) -> OrePatch:
    return field_of(frozenset((x, y) for y, first, last in rows for x in range(first, last + 1)))


WEST_33 = rows_field(
    [(49, 6, 6), (50, 5, 7), (51, 4, 8), (52, 4, 8), (53, 4, 8), (54, 4, 8), (55, 4, 8), (56, 5, 7), (57, 6, 6)]
)
SIDESTEP_HOME_100 = rows_field(
    [(33, 33, 33), (34, 32, 34), (35, 31, 35), (36, 30, 36), (37, 30, 36)]
    + [(y, 29, 37) for y in range(38, 44)]
    + [(44, 30, 36), (45, 30, 36), (46, 31, 35), (47, 32, 34), (48, 33, 33)]
)
SOUTH_47 = rows_field([(73, 13, 15), (74, 10, 17), (75, 8, 17), (76, 8, 18), (77, 9, 17), (78, 10, 12), (78, 14, 16)])
# The base at tick 5501 of that game, as the state reported it, with the third refinery ready to place: the opening's
# second refinery, reported at (16, 52), stands 7 cells from the west field's ore.
SIDESTEP_BASE = (
    ("fact", 23, 44),
    ("fix", 21, 54),
    ("powr", 14, 51),
    ("powr", 21, 49),
    ("proc", 16, 52),
    ("proc", 27, 40),
    ("tent", 19, 49),
    ("weap", 20, 57),
)
SIDESTEP_TICK = 5501


def test_one_per_field_the_opening_fixture_of_the_sidestep_game_yields_one_refinery_on_the_west_field(match_record):
    terrain = terrain_of(WEST_33, SIDESTEP_HOME_100, SOUTH_47)
    agent = unserved_agent(*SIDESTEP_BASE, terrain=terrain, tuning=ONE_PER_FIELD_UPKEEP)

    cell = place_refinery(agent, *SIDESTEP_BASE, tick=SIDESTEP_TICK)

    tuning = agent.ctx.tuning.placement
    second_opening = box_at(
        "proc", refinery_fields.footprint_cell(agent.world.buildings[the_proc_id(agent, ("proc", 16, 52))])
    )
    assert (WEST_33.center, WEST_33.size, SIDESTEP_HOME_100.size, SOUTH_47.size) == ((6, 53), 33, 100, 47)
    assert ore_distance(second_opening, WEST_33) == 7
    assert refinery_fields.field_served_from(second_opening, terrain, tuning.refinery_serves_cells) is None
    assert refinery_fields.field_counted_from(second_opening, terrain, tuning) == WEST_33
    assert refinery_fields.counts(agent.world, agent.ctx, ())[WEST_33] == 1
    assert refinery_fields.field_counted_from(box_at("proc", cell), terrain, tuning) != WEST_33
    (event,) = [event for event in match_record("refinery_field") if event.get("cell") == list(cell)]
    assert event["field_center"] != list(WEST_33.center)


class AgainstAsked:
    """Refinery places that give none, keeping what each field was asked for with."""

    def __init__(self) -> None:
        self.keeps = {}

    def against(self, field, rule, keep=None):
        self.keeps[field] = (rule, keep)
        return None


def test_one_per_field_a_place_that_reaches_a_field_but_serves_a_nearer_one_is_not_taken_for_it():
    terrain = terrain_of(HOME_30, EAST_45)
    agent = unserved_agent(*UNSERVED_BASE, HOME_REFINERY, ELSEWHERE, terrain=terrain, tuning=ONE_PER_FIELD_UPKEEP)
    places = AgainstAsked()
    # 1 cell below the 30-cell field and 2 cells from the 45-cell one; and one standing on the 45-cell field's edge.
    nearer_home, on_east = box_at("proc", (27, 40)), box_at("proc", (29, 42))

    counts = refinery_fields.served(agent.world, terrain, 5, ())
    found = refinery_fields._unserved_place(counts, places, agent.world, agent.ctx)
    rule, keep = places.keeps[EAST_45]

    assert found is None and list(places.keeps) == [EAST_45] and rule == "unserved"
    assert (ore_distance(nearer_home, HOME_30), ore_distance(nearer_home, EAST_45)) == (1, 2)
    assert refinery_fields.field_served_from(nearer_home, terrain, 5) == HOME_30 and not keep(nearer_home)
    assert refinery_fields.field_served_from(on_east, terrain, 5) == EAST_45 and keep(on_east)


def test_one_per_field_a_refinery_serves_the_field_whose_ore_lies_nearest_within_its_reach_and_none_beyond():
    terrain = terrain_of(HOME_30, EAST_45)
    # 2 cells from both fields: the 30-cell field's center lies nearer the box's middle.
    between = box_at("proc", (27, 41))

    assert (ore_distance(between, HOME_30), ore_distance(between, EAST_45)) == (2, 2)
    assert math.dist(between.center, HOME_30.center) < math.dist(between.center, EAST_45.center)
    assert refinery_fields.field_served_from(between, terrain, 5) == HOME_30
    assert refinery_fields.field_served_from(between, terrain, 1) is None
    assert refinery_fields.field_served_from(between, terrain_of(), 5) is None


def test_one_per_field_the_opening_s_second_refinery_goes_to_the_unserved_field_when_the_home_field_is_served():
    agent = unserved_agent(
        *UNSERVED_BASE, HOME_WEST_REFINERY, terrain=terrain_of(HOME_60, EAST_45), tuning=ONE_PER_FIELD_UPKEEP
    )
    # Against the home field, 7 cells from the refinery that serves it: as a second refinery, it is spaced.
    today = placement.PlacementChoice((22, 40))

    spaced = refinery_fields.spaced_opening(today, agent.world, agent.ctx)

    assert refinery_fields.field_served_from(box_at("proc", today.cell), agent.ctx.terrain, 5) == HOME_60
    assert math.dist(middle_of(today.cell), middle_of(HOME_WEST_REFINERY[1:])) >= 6
    assert spaced.field.patch == EAST_45 and spaced.field.rule == "unserved"
    assert refinery_fields.field_served_from(box_at("proc", spaced.cell), agent.ctx.terrain, 5) == EAST_45


def test_one_per_field_the_opening_s_second_refinery_stays_on_the_served_home_field_with_no_unserved_field_in_reach():
    agent = unserved_agent(*UNSERVED_BASE, HOME_WEST_REFINERY, terrain=terrain_of(HOME_60), tuning=ONE_PER_FIELD_UPKEEP)

    cell = place_refinery(agent, *UNSERVED_BASE, HOME_WEST_REFINERY, tick=1000)

    assert refinery_ore_distance(cell, HOME_60) <= 2
    assert math.dist(middle_of(cell), middle_of(HOME_WEST_REFINERY[1:])) >= 6


def test_one_per_field_a_dry_field_is_never_chosen_and_the_choice_kept_follows_the_dry_fields():
    terrain = terrain_of(HOME_30, EAST_45)
    agent = unserved_agent(*UNSERVED_BASE, HOME_REFINERY, ELSEWHERE, terrain=terrain, tuning=ONE_PER_FIELD_UPKEEP)
    places = refinery_fields.UnservedPlaces()

    before = places.choice(agent.world, agent.ctx)
    agent.ctx.dry_fields = frozenset({EAST_45})
    dry = places.choice(agent.world, agent.ctx)

    assert before.field.patch == EAST_45
    assert dry is None and refinery_fields.choose(agent.world, agent.ctx) is None


def test_one_per_field_the_opening_s_second_refinery_skips_a_dry_unserved_field():
    agent = unserved_agent(
        *UNSERVED_BASE, HOME_WEST_REFINERY, terrain=terrain_of(HOME_60, EAST_45), tuning=ONE_PER_FIELD_UPKEEP
    )
    today = placement.PlacementChoice((22, 40))
    agent.ctx.dry_fields = frozenset({EAST_45})

    spaced = refinery_fields.spaced_opening(today, agent.world, agent.ctx)

    assert refinery_fields.field_served_from(box_at("proc", spaced.cell), agent.ctx.terrain, 5) == HOME_60
    assert math.dist(middle_of(spaced.cell), middle_of(HOME_WEST_REFINERY[1:])) >= 6


def test_one_per_field_a_lost_refinery_s_field_is_unserved_again_and_takes_the_next_refinery():
    terrain = terrain_of(HOME_30, EAST_45)
    standing = unserved_agent(*UNSERVED_BASE, HOME_REFINERY, EAST_3_AWAY, terrain=terrain, tuning=ONE_PER_FIELD_UPKEEP)
    lost = unserved_agent(*UNSERVED_BASE, HOME_REFINERY, ELSEWHERE, terrain=terrain, tuning=ONE_PER_FIELD_UPKEEP)

    assert refinery_fields.choose(standing.world, standing.ctx) is None
    assert refinery_fields.choose(lost.world, lost.ctx).field.patch == EAST_45


def test_one_per_field_the_dry_fields_are_published_whatever_the_second_yard_switches():
    agent = unserved_agent(*UNSERVED_BASE, terrain=terrain_of(HOME_30, EAST_45), tuning=ONE_PER_FIELD_UPKEEP)
    expansion_tuning = agent.ctx.tuning.expansion

    expansion.ForwardMcv().publish_spent_fields(agent.world, agent.ctx, frozenset({EAST_45}))

    assert (expansion_tuning.second_yard, expansion_tuning.mcv_new_area) == (False, False)
    assert agent.ctx.dry_fields == frozenset({EAST_45}) and agent.ctx.harvested_fields == frozenset()


def the_proc_id(agent, refinery: tuple[str, int, int]) -> int:
    """The id of the own refinery the state reports at a refinery's cell."""
    (found,) = [
        building.id
        for building in agent.world.buildings.values()
        if (building.type, building.x, building.y) == refinery
    ]
    return found


# -- standing buildings at their true footprint -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "item, offset",
    [("fact", (1, 1)), ("powr", (1, 1)), ("proc", (1, 1)), ("weap", (1, 1)), ("sam", (1, 0)), ("silo", (0, 0))]
    + [("pbox", (0, 0)), ("mystery", (1, 1))],
)
def test_footprint_each_type_is_reported_at_its_middle_cell_less_its_offset(item, offset):
    width, height = footprint(item)

    assert placement.reported_offset(item) == offset
    assert placement.footprint_corner(item, (40, 40)) == (40 - offset[0], 40 - offset[1])
    assert offset == ((1, 1) if width >= 2 and height >= 2 else (width // 2, height // 2))


def test_footprint_a_refinery_reported_at_71_50_occupies_the_cells_from_70_49():
    world = world_with(("proc", 71, 50), base_center=(71, 50))

    (refinery,) = world.buildings.values()

    assert placement.standing_box(refinery) == Box(70, 49, 3, 4)
    assert refinery_fields.footprint_cell(refinery) == (70, 49)
    assert refinery_fields.REPORTED_OFFSET == placement.reported_offset("proc") == (1, 1)


def test_footprint_a_place_next_to_the_refinery_only_through_its_phantom_cells_is_not_offered():
    world = world_with(("proc", 71, 50), base_center=(71, 50))
    phantom, true = box_at("proc", (71, 50)), Box(70, 49, 3, 4)
    # 2 cells from the box the reported cell would give as top-left, 3 from the cells the refinery covers.
    phantom_only, next_to_it = box_at("powr", (75, 55)), box_at("powr", (74, 54))

    candidates = candidate_cells("powr", world, NO_TERRAIN, TUNING)

    assert phantom.grown(TUNING.adjacent_cells).overlaps(phantom_only)
    assert not true.grown(TUNING.adjacent_cells).overlaps(phantom_only)
    assert (75, 55) not in candidates and (74, 54) in candidates
    assert true.grown(TUNING.adjacent_cells).overlaps(next_to_it)


def test_footprint_a_building_asked_for_keeps_the_cells_from_the_place_it_was_asked_onto():
    world = world_with(YARD)
    promised = box_at("proc", (25, 48))

    candidates = candidate_cells("powr", world, NO_TERRAIN, TUNING, promised=[promised])

    assert all(not box_at("powr", cell).overlaps(promised.grown(TUNING.gap_cells)) for cell in candidates)
    assert any(box_at("powr", cell).overlaps(promised.grown(TUNING.gap_cells + 1)) for cell in candidates)


def test_footprint_the_war_factory_s_exit_and_the_refinery_s_lane_start_from_the_cells_they_cover():
    world = world_with(YARD, ("weap", 31, 43), ("proc", 27, 45))

    kept = excluded_cells(world, Terrain([HOME_PATCH]), TUNING)

    assert {(30, 45), (31, 45), (32, 45), (30, 46), (31, 46), (32, 46)} <= kept
    assert (27, 46) in kept and (28, 47) not in kept


# -- the creep cap and the compact place --------------------------------------------------------------------------------

# The next field once the MCV takes the west field, the crawl's: its ore 44 cells below the yard's footprint.
FIELD_44_BELOW = square_patch((22, 95))
# A power plant standing 20 cells below the yard, toward that field.
POWER_20_BELOW = ("powr", 22, 70)


def test_cap_with_the_crawl_field_taken_and_the_next_field_44_cells_off_there_is_no_far_field_and_power_goes_compact():
    world = world_with(YARD, bounds=WIDE_BOUNDS)
    terrain = terrain_of(HOME_PATCH, WEST_FIELD, FIELD_44_BELOW)
    skipped = [WEST_FIELD]

    choice = choose_placement("powr", world, terrain, CONTINUOUS, skipped=skipped)

    assert ore_distance(YARD_BOX, FIELD_44_BELOW) == 44 > CONTINUOUS.creep_far_max_cells == 30
    assert placement.far_field(world, terrain, CONTINUOUS, skipped=skipped) is None
    assert choice == placement.PlacementChoice(candidate_cells("powr", world, terrain, CONTINUOUS)[0])
    assert choice.cell == preferred_cell("powr", world, terrain, TUNING)


def test_cap_is_measured_from_the_nearest_own_building_so_the_base_walks_on_from_where_it_has_grown():
    terrain = terrain_of(HOME_PATCH, FIELD_44_BELOW)
    grown = world_with(YARD, POWER_20_BELOW, bounds=WIDE_BOUNDS)
    defended = world_with(YARD, ("gun", 22, 72), bounds=WIDE_BOUNDS)

    field = placement.far_field(grown, terrain, CONTINUOUS)

    power_box = placement.standing_box(grown.buildings[101])
    assert ore_distance(power_box, FIELD_44_BELOW) <= CONTINUOUS.creep_far_max_cells
    assert field is not None and field.patch == FIELD_44_BELOW
    # A defense is no part of the base the cap is measured from.
    assert placement.far_field(defended, terrain, CONTINUOUS) is None
    assert placement.is_within_creep_cap(FIELD_44_BELOW, [], (22, 70), 30)
    assert not placement.is_within_creep_cap(FIELD_44_BELOW, [YARD_BOX], (22, 70), 30)


def test_cap_with_no_place_near_the_line_a_power_plant_takes_the_compact_place_not_the_one_nearest_the_ore():
    # Ground nothing can be built on across the line from the yard to the east field, 4 rows on either side of it.
    across = frozenset((x, y) for x in range(24, 37) for y in range(45, 54))
    terrain = Terrain([HOME_PATCH, EAST_15_FIELD], HOME_PATCH.cells | EAST_15_FIELD.cells | across)
    world = world_with(YARD)
    ore = EAST_15_FIELD.nearest_cell(FAR_ORIGIN)

    choice = choose_placement("powr", world, terrain, FAR)

    places = candidate_cells("powr", world, terrain, FAR)
    nearest_the_ore = min(places, key=lambda cell: middle_distance("powr", cell, ore))
    assert placement.far_field(world, terrain, FAR).patch == EAST_15_FIELD
    assert all(along_and_off("powr", cell, FAR_ORIGIN, ore)[1] > FAR.creep_line_cells for cell in places)
    assert choice == placement.PlacementChoice(places[0]) and choice.cell != nearest_the_ore


# -- walked reach: MCV sites and refineries on the sidestep map ---------------------------------------------------------

SIDESTEP_MAP = json.loads((Path(__file__).parents[1] / "fixtures" / "redalert" / "maps" / "sidestep.json").read_text())
SIDESTEP_TERRAIN = Terrain.from_game_config({"rules": {"maps": {"sidestep": SIDESTEP_MAP}}}, "sidestep")
# The top field of sidestep, north of the east start (74, 53) behind a row of cliff, and the yard (81, 27) and the
# refinery (77, 25) the watched game 20261013T220250Z stood south of that row, as the state reported them.
SIDESTEP_TOP_FIELD = SIDESTEP_TERRAIN.nearest_patch((84, 21))
SOUTH_OF_THE_CLIFF_SITE, SOUTH_OF_THE_CLIFF_REFINERY = (81, 27), ("proc", 77, 25)


def sidestep_agent(*buildings):
    """An agent on the sidestep map whose base is the east start's yard and these buildings."""
    agent = new_agent(UpkeepManager())
    state = situations.base_state("england", ("fact", 74, 53), *buildings, tick=1000, base_center=[74, 53])
    situations.ask(agent, state)
    agent.ctx.terrain = SIDESTEP_TERRAIN
    return agent


def test_walked_the_mcv_site_by_the_sidestep_top_field_is_on_the_walkable_side_of_the_cliff():
    agent = sidestep_agent()
    reach = agent.ctx.tuning.expansion.mcv_deploy_cells

    site = expansion._place_by(SIDESTEP_TOP_FIELD, agent.world, agent.ctx)

    south = expansion.yard_box(SOUTH_OF_THE_CLIFF_SITE)
    walked = SIDESTEP_TERRAIN.walked_distance(expansion.yard_box(site).cells(), SIDESTEP_TOP_FIELD, reach)
    assert (SIDESTEP_TOP_FIELD.center, SIDESTEP_TOP_FIELD.size) == ((84, 21), 47)
    assert site == (78, 18) and walked == 1
    # The site the straight line gave, 2 cells from the ore, is 14 by walking round the cliff: refused.
    assert ore_distance(south, SIDESTEP_TOP_FIELD) == 2 <= reach
    assert SIDESTEP_TERRAIN.walked_distance(south.cells(), SIDESTEP_TOP_FIELD, 20) == 14
    assert SIDESTEP_TERRAIN.walked_distance(south.cells(), SIDESTEP_TOP_FIELD, reach) is None
    assert all(cell in SIDESTEP_TERRAIN.impassable for cell in [(x, 25) for x in range(80, 91)])


def test_walked_a_site_whose_walk_to_the_ore_exceeds_mcv_deploy_cells_is_refused():
    agent = sidestep_agent()
    reach = agent.ctx.tuning.expansion.mcv_deploy_cells
    # The field's ore cut off by cliff on every side: no place for a yard is within reach by walking.
    walled = Terrain(
        SIDESTEP_TERRAIN.ore_patches,
        SIDESTEP_TERRAIN.blocked,
        impassable=SIDESTEP_TERRAIN.impassable | (frozenset(Box(70, 10, 25, 20).cells()) - SIDESTEP_TOP_FIELD.cells),
    )
    agent.ctx.terrain = walled

    assert expansion._place_by(SIDESTEP_TOP_FIELD, agent.world, agent.ctx) is None
    assert walled.walked_distance(expansion.yard_box((78, 18)).cells(), SIDESTEP_TOP_FIELD, reach) is None


def test_walked_a_refinery_2_cells_from_the_top_field_but_11_by_walking_does_not_reach_it():
    agent = sidestep_agent(SOUTH_OF_THE_CLIFF_REFINERY)
    (refinery,) = [building for building in agent.world.buildings.values() if building.type == "proc"]
    box = placement.standing_box(refinery)
    reach = agent.ctx.tuning.placement.refinery_ore_cells

    assert box == Box(76, 24, 3, 4) and ore_distance(box, SIDESTEP_TOP_FIELD) == 2
    assert SIDESTEP_TERRAIN.walked_distance(box.cells(), SIDESTEP_TOP_FIELD, 20) == 11
    assert reaches(box, SIDESTEP_TOP_FIELD, reach) and not reaches(box, SIDESTEP_TOP_FIELD, reach, SIDESTEP_TERRAIN)
    assert SIDESTEP_TOP_FIELD not in placement.fields_against(box, SIDESTEP_TERRAIN, reach)
    tuning = agent.ctx.tuning.placement
    assert SIDESTEP_TOP_FIELD not in placement.taken_fields(agent.world, SIDESTEP_TERRAIN, tuning, ())
    reached = placement._reached_fields([SIDESTEP_TOP_FIELD], agent.world, SIDESTEP_TERRAIN, tuning, ())
    assert SIDESTEP_TOP_FIELD not in reached
    # Nor does it serve the field, which one refinery a field would then leave to it.
    open_ground = Terrain(SIDESTEP_TERRAIN.ore_patches, SIDESTEP_TERRAIN.blocked)
    assert refinery_fields.field_served_from(box, open_ground, tuning.refinery_serves_cells) == SIDESTEP_TOP_FIELD
    assert refinery_fields.field_served_from(box, SIDESTEP_TERRAIN, tuning.refinery_serves_cells) != SIDESTEP_TOP_FIELD


def test_walked_a_refinery_stands_against_the_top_field_only_from_a_place_it_walks_to_within_reach():
    agent = sidestep_agent()
    tuning = agent.ctx.tuning.placement
    places = placement.RefineryPlaces(agent.world, SIDESTEP_TERRAIN, tuning, ())

    choice = places.against(SIDESTEP_TOP_FIELD, placement.FIELD_CLAIM)

    assert choice is None or reaches(
        box_at("proc", choice.cell), SIDESTEP_TOP_FIELD, tuning.refinery_ore_cells, SIDESTEP_TERRAIN
    )


# -- a whole fake match on the sidestep map -----------------------------------------------------------------------------

# The sidestep picture laid over the fake game's map so that its east start, (74, 53), lies on the fake's own spawn.
SIDESTEP_EAST_START = (74, 53)
# At most this share of the place orders may ask a footprint the engine would move: onto a building that stands, or
# out of reach of every building but a defense.
MOVED_SHARE_MAX = 0.2


def sidestep_reference() -> dict:
    """The fake game's reference object, its map pictured as sidestep around the east start."""
    reference = copy.deepcopy(FakeRedAlert().game_config)
    layout = copy.deepcopy(SIDESTEP_MAP["layout"])
    own_x, own_y = FakeRedAlert().game_config["rules"]["maps"][MAP_ID]["spawns"][0]
    layout["origin"] = [
        layout["origin"][0] + own_x - SIDESTEP_EAST_START[0],
        layout["origin"][1] + own_y - SIDESTEP_EAST_START[1],
    ]
    reference["rules"]["maps"][MAP_ID]["layout"] = layout
    return reference


def is_asked_place(order: dict) -> bool:
    """Whether an order asks a building onto a cell."""
    return order.get("cmd") == "place" and order.get("at") is not None


def standing_and_bases(world: World) -> tuple[list[Box], list[Box]]:
    """The footprints of the buildings that stand, and of those of them that are not defenses."""
    buildings = list(world.buildings.values())
    standing = [placement.standing_box(building) for building in buildings]
    bases = [placement.standing_box(building) for building in buildings if building.type not in DEFENSE_TYPES]
    return standing, bases


class PlaceWatch:
    """Notes, after each cycle of a whole match, each place order the agent sent in it and whether the engine would
    move it: its footprint overlaps the cells a standing building covers, or lies out of the reach the engine demands
    of every standing building but a defense."""

    def __init__(self) -> None:
        self.seen = 0
        self.moved: list[bool] = []

    def __call__(self, agent) -> None:
        entries, tuning = agent.ctx.ledger.entries, agent.ctx.tuning.placement
        placed = [entry.order for entry in entries[self.seen :] if is_asked_place(entry.order)]
        if placed:
            standing, bases = standing_and_bases(agent.world)
            self.moved += [self._is_moved(order["item"], order["at"], standing, bases, tuning) for order in placed]
        self.seen = len(entries)

    @staticmethod
    def _is_moved(item: str, cell: list[int], standing: list[Box], bases: list[Box], tuning: PlacementTuning) -> bool:
        """Whether the engine would move a building of an item asked onto a cell: its footprint overlaps one of the
        `standing` footprints, or lies out of the reach the engine demands of every one of the `bases`."""
        box = box_at(item, tuple(cell))
        reach = tuning.defense_adjacent_cells if item in DEFENSE_TYPES else tuning.adjacent_cells
        is_adjacent = any(box.grown(reach).overlaps(other) for other in bases)
        return any(box.overlaps(other) for other in standing) or not is_adjacent

    @property
    def moved_share(self) -> float:
        """The share of the place orders the engine would move."""
        return sum(self.moved) / len(self.moved)


@pytest.fixture(scope="module")
def sidestep_match() -> tuple[WholeMatch, PlaceWatch]:
    """A whole fake match under the shipped tuning on the sidestep picture, the fake game reporting each building at
    the middle of its footprint as the engine does."""
    watch = PlaceWatch()
    match = play_whole_match(
        Tuning.from_file(REPO_ROOT), game_config=sidestep_reference(), watch=watch, engine_reports=True
    )
    return match, watch


def test_sidestep_a_shipped_whole_fake_match_asks_at_most_a_fifth_of_its_buildings_where_the_engine_would_move_them(
    sidestep_match,
):
    match, watch = sidestep_match

    assert match.agent.ctx.terrain.impassable and len(watch.moved) >= 10
    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.compute_p95_ms <= COMPUTE_P95_MS
    assert watch.moved_share <= MOVED_SHARE_MAX


# -- a refinery held for an MCV expansion -------------------------------------------------------------------------------

HOLDING = tuning_off(placement=ONE_PER_FIELD, expansion=expansion_off(hold_refinery_for_mcv=True))
# The base with the 85-cell home field served once: a refinery due would be its second, of the rule second_when_stuck.
STUCK_BASE = (*UNSERVED_BASE, HOME_WEST_REFINERY)
# The site chosen for the next MCV, and the MCV out on its way there.
MCV_SITE = (60, 20)
MCV_OUT = situations.unit(700, "mcv", 40, 40, idle=False)
# A field of 36 cells by the yard the MCV deployed, and that yard.
NEW_YARD_FIELD = field_of(rectangle(58, 18, 6, 6))
NEW_YARD = ("fact", 61, 27)
OPENED_AT = 1000


def held_looks(agent, places, *, ticks, site=MCV_SITE, units=()) -> list[bool]:
    """Whether the next refinery is held at each tick, the agent seeing the base with the units given and, after its
    cycle, the MCV's site published as chosen (or not)."""
    held = []
    for tick in ticks:
        situations.ask(agent, england_base(*STUCK_BASE, tick=tick, units=list(units)))
        agent.ctx.repeat_mcv_site = site
        held.append(places.choice(agent.world, agent.ctx) is None)
    return held


def test_refinery_held_for_mcv_with_a_site_chosen_a_second_when_stuck_refinery_waits_with_one_held_for_mcv_event(
    match_record,
):
    agent = unserved_agent(*STUCK_BASE, terrain=terrain_of(HOME_85), tuning=HOLDING)
    places = refinery_fields.UnservedPlaces()

    agent.ctx.repeat_mcv_site = MCV_SITE
    looks = [places.choice(agent.world, agent.ctx) for _ in range(2)]
    for _ in range(2):
        places.note_waiting(agent.world)

    held = [event for event in match_record("refinery_field") if event["reason"] == "held_for_mcv"]
    stuck = places.placed(agent.world, agent.ctx)
    assert looks == [None, None] and (stuck.field.patch, stuck.field.rule) == (HOME_85, "second_when_stuck")
    assert [(event["rule"], event["field_center"], event["served_by"], event["cell"]) for event in held] == [
        ("second_when_stuck", list(HOME_85.center), 1, list(stuck.cell))
    ]


def test_refinery_held_for_mcv_with_the_switch_off_or_no_expansion_the_second_when_stuck_refinery_goes_as_today():
    off = unserved_agent(*STUCK_BASE, terrain=terrain_of(HOME_85), tuning=ONE_PER_FIELD_UPKEEP)
    idle = unserved_agent(*STUCK_BASE, terrain=terrain_of(HOME_85), tuning=HOLDING)
    off.ctx.repeat_mcv_site = MCV_SITE

    with_site_off = refinery_fields.UnservedPlaces().choice(off.world, off.ctx)
    without_site = refinery_fields.UnservedPlaces().choice(idle.world, idle.ctx)

    assert with_site_off == without_site == refinery_fields.choose(off.world, off.ctx)
    assert with_site_off.field.rule == "second_when_stuck"


def test_refinery_held_for_mcv_an_unserved_field_in_reach_still_gets_its_refinery_during_the_window():
    agent = unserved_agent(*STUCK_BASE, terrain=terrain_of(HOME_85, EAST_36), tuning=HOLDING)
    agent.ctx.repeat_mcv_site = MCV_SITE

    choice = refinery_fields.UnservedPlaces().choice(agent.world, agent.ctx)

    assert (choice.field.patch, choice.field.rule) == (EAST_36, "unserved")


def test_refinery_held_for_mcv_the_window_ends_120_s_after_the_site_and_the_claim_resumes_at_121_s():
    agent = unserved_agent(*STUCK_BASE, terrain=terrain_of(HOME_85), tuning=HOLDING)
    ticks = [OPENED_AT, OPENED_AT + 120 * TICKS_PER_SECOND, OPENED_AT + 121 * TICKS_PER_SECOND, OPENED_AT + 4000]

    held = held_looks(agent, refinery_fields.UnservedPlaces(), ticks=ticks)

    # The expansion still under way holds nothing more once its window ran out.
    assert held == [True, True, False, False]


def test_refinery_held_for_mcv_the_window_ends_60_s_after_the_last_site_with_no_mcv_on_order_or_out():
    agent = unserved_agent(*STUCK_BASE, terrain=terrain_of(HOME_85), tuning=HOLDING)
    places = refinery_fields.UnservedPlaces()

    with_site = held_looks(agent, places, ticks=[OPENED_AT])
    without = held_looks(
        agent, places, ticks=[OPENED_AT + 60 * TICKS_PER_SECOND, OPENED_AT + 61 * TICKS_PER_SECOND], site=None
    )

    assert with_site + without == [True, True, False]


def test_refinery_held_for_mcv_the_window_lasts_while_the_mcv_is_out_and_ends_once_it_deploys_or_is_lost():
    agent = unserved_agent(*STUCK_BASE, terrain=terrain_of(HOME_85), tuning=HOLDING)
    places = refinery_fields.UnservedPlaces()

    sited = held_looks(agent, places, ticks=[OPENED_AT])
    out = held_looks(agent, places, ticks=[OPENED_AT + 500, OPENED_AT + 2000], site=None, units=[MCV_OUT])
    gone = held_looks(agent, places, ticks=[OPENED_AT + 2005], site=None)

    assert sited + out + gone == [True, True, True, False]


def test_refinery_held_for_mcv_upkeep_orders_no_second_refinery_while_the_mcv_is_out_and_one_once_it_deployed(
    match_record,
):
    home = (*UNSERVED_BASE, ("fix", 15, 53), ("powr", 26, 52), ("powr", 12, 53), HOME_WEST_REFINERY, ELSEWHERE)
    agent = unserved_agent(*home, terrain=terrain_of(HOME_100), tuning=HOLDING)

    out = builds_of(situations.ask(agent, england_base(*home, tick=1000, units=[*AT_WORK, MCV_OUT])))
    deployed = builds_of(situations.ask(agent, england_base(*home, tick=1005, units=AT_WORK)))

    held = [event for event in match_record("refinery_field") if event["reason"] == "held_for_mcv"]
    assert "proc" not in out and "proc" in deployed
    assert [(event["tick"], event["rule"], event["field_center"]) for event in held] == [
        (1000, "second_when_stuck", list(HOME_100.center))
    ]


def test_refinery_held_for_mcv_once_the_mcv_deployed_the_next_refinery_goes_to_the_new_yard_s_field():
    terrain = terrain_of(HOME_85, EAST_36, NEW_YARD_FIELD)
    agent = unserved_agent(*STUCK_BASE, NEW_YARD, terrain=terrain, tuning=HOLDING)

    choice = refinery_fields.UnservedPlaces().choice(agent.world, agent.ctx)

    # The unserved field by the main yard is nearer it; the new yard's field comes first.
    assert (choice.field.patch, choice.field.rule) == (NEW_YARD_FIELD, "unserved")
    assert math.dist(EAST_36.center, YARD[1:]) < math.dist(NEW_YARD_FIELD.center, YARD[1:])
