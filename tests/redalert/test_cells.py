"""Cell geometry: distances, the point toward a target, clamping, the line between two cells, the staging point."""

from __future__ import annotations

import pytest

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.items import ItemTable
from ucla_agent.games.redalert.world import World

BOUNDS = (2, 2, 96, 96)


def placed_world(base_center, enemy_base, bounds=BOUNDS) -> World:
    """A world that knows only where its base is, where the enemy starts and how large the map is."""
    world = World(ItemTable([]))
    world.base_center, world.enemy_base, world.map_bounds = base_center, enemy_base, bounds
    return world


@pytest.mark.parametrize(
    "point, cell",
    [((2.4, 7.6), (2, 8)), ((2.5, 3.5), (3, 4)), ((-0.5, -1.5), (0, -1)), ((4.0, 9.0), (4, 9))],
    ids=["fractions", "halves go up", "negative halves go up", "whole numbers"],
)
def test_a_point_between_cells_belongs_to_the_nearest_cell(point, cell):
    assert cells.nearest_cell(*point) == cell


def test_the_distance_between_two_cells_is_the_straight_line():
    assert cells.distance((22, 48), (22, 48)) == 0
    assert cells.distance((22, 48), (25, 52)) == 5
    assert cells.distance((25, 52), (22, 48)) == 5


@pytest.mark.parametrize(
    "other, reach, within",
    [((30, 48), 8, True), ((30, 49), 8, False), ((28, 54), 8.5, True), ((22, 48), 0, True)],
    ids=["exactly at reach", "just beyond", "a fractional reach", "the same cell"],
)
def test_two_cells_are_within_reach_up_to_and_including_the_reach(other, reach, within):
    assert cells.is_within((22, 48), other, reach) is within


@pytest.mark.parametrize(
    "target, steps, expected",
    [
        ((74, 48), 10, (32, 48)),
        ((22, 8), 5, (22, 43)),
        ((52, 88), 10, (28, 56)),
        ((2, 48), 10, (12, 48)),
        ((25, 48), 10, (32, 48)),
        ((22, 48), 10, (22, 48)),
    ],
    ids=["east", "north", "a diagonal", "west", "past a near target", "no direction"],
)
def test_toward_gives_the_cell_that_many_cells_along_the_line(target, steps, expected):
    assert cells.toward((22, 48), target, steps) == expected


def test_toward_rounds_a_half_up_on_both_axes():
    assert cells.toward((0, 0), (4, 0), 2.5) == (3, 0)
    assert cells.toward((0, 0), (0, 4), 2.5) == (0, 3)
    assert cells.toward((0, 0), (4, 0), 1.5) == (2, 0)


@pytest.mark.parametrize(
    "cell, expected",
    [((50, 50), (50, 50)), ((0, 50), (2, 50)), ((120, 50), (97, 50)), ((50, -4), (50, 2)), ((99, 99), (97, 97))],
    ids=["inside", "left of the map", "right of the map", "above the map", "past the far corner"],
)
def test_a_cell_is_clamped_to_the_nearest_cell_inside_the_bounds(cell, expected):
    assert cells.clamped(cell, BOUNDS) == expected


@pytest.mark.parametrize(
    "start, end, expected",
    [
        ((3, 3), (3, 3), [(3, 3)]),
        ((3, 3), (6, 3), [(3, 3), (4, 3), (5, 3), (6, 3)]),
        ((3, 6), (3, 3), [(3, 6), (3, 5), (3, 4), (3, 3)]),
        ((0, 0), (3, 3), [(0, 0), (1, 1), (2, 2), (3, 3)]),
        ((0, 0), (4, 2), [(0, 0), (1, 0), (2, 1), (3, 1), (4, 2)]),
        ((4, 2), (0, 0), [(4, 2), (3, 2), (2, 1), (1, 1), (0, 0)]),
    ],
    ids=["one cell", "a row", "a column upward", "a diagonal", "a shallow slope", "the slope walked back"],
)
def test_a_line_holds_every_cell_from_start_to_end_in_order(start, end, expected):
    assert cells.line(start, end) == expected


def test_each_step_of_a_line_moves_to_a_neighbouring_cell():
    walked = cells.line((22, 48), (40, 17))

    steps = [(later[0] - earlier[0], later[1] - earlier[1]) for earlier, later in zip(walked, walked[1:])]
    assert walked[0] == (22, 48) and walked[-1] == (40, 17) and len(walked) == 32
    assert all(max(abs(dx), abs(dy)) == 1 for dx, dy in steps)


def test_the_staging_point_is_10_cells_from_the_base_toward_the_enemy():
    assert cells.staging_point(placed_world((22, 48), (74, 48)), 10) == (32, 48)
    assert cells.staging_point(placed_world((74, 48), (22, 48)), 10) == (64, 48)


def test_the_staging_point_is_clamped_inside_the_map():
    world = placed_world((93, 50), (99, 50), bounds=(2, 2, 94, 94))

    assert cells.staging_point(world, 10) == (95, 50)


@pytest.mark.parametrize(
    "base_center, enemy_base, bounds",
    [(None, (74, 48), BOUNDS), ((22, 48), None, BOUNDS), ((22, 48), (74, 48), None)],
    ids=["no base", "no enemy start", "no bounds"],
)
def test_there_is_no_staging_point_before_base_enemy_start_and_map_are_known(base_center, enemy_base, bounds):
    assert cells.staging_point(placed_world(base_center, enemy_base, bounds), 10) is None
