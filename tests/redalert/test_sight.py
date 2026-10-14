"""The sight grid: the last tick each 4-cell square of the map was within 6 cells of one of our units."""

from __future__ import annotations

import situations
from ucla_agent.games.redalert.sight import SightGrid, square_of, squares_within
from ucla_agent.games.redalert.world import World

YARD = ("fact", 22, 48)
ENEMY_BASE = (74, 48)


def world_at(tick: int, *units: dict) -> World:
    """A world that has taken in one state of an Allied base with these units."""
    world = World(situations.tracked_items())
    world.update(situations.base_state("england", YARD, tick=tick, units=list(units)))
    return world


def rifle(x: int, y: int) -> dict:
    return situations.unit(300, "e1", x, y)


def test_a_unit_sees_the_squares_whose_middles_lie_within_6_cells_and_no_farther():
    grid = SightGrid()

    grid.observe(world_at(1000, rifle(20, 48)))

    # Square (6, 12) has its middle at (25.5, 49.5), 5.7 cells away; (7, 12) at (29.5, 49.5), 9.6; (6, 13) at
    # (25.5, 53.5), 7.8.
    assert [grid.last_seen(cell) for cell in ((20, 48), (25, 50), (17, 49), (21, 54))] == [1000] * 4
    assert [grid.last_seen(cell) for cell in ((29, 48), (26, 54), (13, 48))] == [None] * 3


def test_a_square_keeps_the_last_tick_it_was_seen_after_the_unit_has_moved_on():
    grid = SightGrid()
    grid.observe(world_at(1000, rifle(20, 48)))

    grid.observe(world_at(1100, rifle(60, 20)))

    assert (grid.last_seen((20, 48)), grid.last_seen((60, 20))) == (1000, 1100)


def test_buildings_see_nothing():
    grid = SightGrid()

    grid.observe(world_at(1000))

    assert grid.last_seen(YARD[1:]) is None


def test_near_a_cell_is_the_last_sighting_of_any_square_whose_middle_lies_within_the_radius():
    near, far = SightGrid(), SightGrid()

    # From (58, 48) the square with its middle at (61.5, 49.5) is seen, 12.6 cells from the enemy's start; from
    # (50, 48) the nearest square seen has its middle at (53.5, 49.5), 20.6 cells from it.
    near.observe(world_at(1000, rifle(58, 48)))
    far.observe(world_at(1000, rifle(50, 48)))

    assert (near.last_seen_near(ENEMY_BASE, 15), far.last_seen_near(ENEMY_BASE, 15)) == (1000, None)


def test_the_squares_within_a_radius_are_those_whose_middles_lie_within_it():
    assert squares_within((2, 2), 1) == ((0, 0),)
    assert set(squares_within((4, 4), 3)) == {(1, 0), (0, 1), (1, 1)}
    assert set(squares_within((4, 4), 4)) == {(0, 0), (1, 0), (0, 1), (1, 1)}
    assert square_of((7, 8)) == (1, 2) and square_of((-1, 0)) == (-1, 0)
