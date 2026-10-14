"""Cells of the map: how far apart two are, what lies between them, and where a point toward another is.

The platform names every place as a cell `[x, y]`, and its rules are stated
in cells: within 8 cells of the construction yard, 10 cells toward the enemy.
Distances here are straight-line distances between cell coordinates, which is
how those rules are read everywhere in the agent.
"""

from __future__ import annotations

import math

from ucla_agent.games.redalert.world import Cell, World

# The playable part of a map as (x, y, width, height).
Bounds = tuple[int, int, int, int]


def nearest_cell(x: float, y: float) -> Cell:
    """The cell a point given in fractions of cells lies nearest to; a half goes up."""
    # Python's own rounding sends a half to the even neighbour, so two points half a cell apart could
    # land on the same cell.
    return math.floor(x + 0.5), math.floor(y + 0.5)


def distance(first: Cell, second: Cell) -> float:
    """The straight-line distance between two cells, in cells."""
    return math.hypot(first[0] - second[0], first[1] - second[1])


def is_within(first: Cell, second: Cell, reach: float) -> bool:
    """Whether two cells are at most `reach` cells apart."""
    return (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2 <= reach * reach


def toward(origin: Cell, target: Cell, cells: float) -> Cell:
    """The cell `cells` away from `origin` on the straight line to `target`; `origin` itself when the two are one cell.

    The line is followed past `target` when `target` is nearer than `cells`.
    """
    gap = distance(origin, target)
    if gap == 0:
        return origin
    share = cells / gap
    return nearest_cell(origin[0] + (target[0] - origin[0]) * share, origin[1] + (target[1] - origin[1]) * share)


def clamped(cell: Cell, bounds: Bounds) -> Cell:
    """The cell of the playable part of the map nearest to `cell`: `cell` itself when it lies inside."""
    left, top, width, height = bounds
    return min(max(cell[0], left), left + width - 1), min(max(cell[1], top), top + height - 1)


def line(start: Cell, end: Cell) -> list[Cell]:
    """The cells a straight line from `start` to `end` passes through, both ends included, in order."""
    (x, y), (end_x, end_y) = start, end
    span_x, span_y = abs(end_x - x), abs(end_y - y)
    step_x, step_y = (1 if end_x > x else -1), (1 if end_y > y else -1)
    # Bresenham's line: the error says how far the cells walked so far have drifted off the true line.
    error = span_x - span_y
    cells = [(x, y)]
    while (x, y) != (end_x, end_y):
        doubled = 2 * error
        if doubled > -span_y:
            error -= span_y
            x += step_x
        if doubled < span_x:
            error += span_x
            y += step_y
        cells.append((x, y))
    return cells


def staging_point(world: World, distance_cells: int) -> Cell | None:
    """The cell `distance_cells` from the base toward the enemy's start, inside the playable part of the map.

    None while the base, the enemy's start or the map's bounds are not known.
    """
    if world.base_center is None or world.enemy_base is None or world.map_bounds is None:
        return None
    return clamped(toward(world.base_center, world.enemy_base, distance_cells), world.map_bounds)
