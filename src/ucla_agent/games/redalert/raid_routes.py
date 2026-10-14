"""The way a raid takes to its target, around the ground the enemy's defenses cover.

A raid does not drive straight at its target. It stops at an approach cell 8
cells short of it, on the line from the staging point, and attacks from
there, so that it arrives together and can turn back before it is in the
middle of the enemy's base. The way to the approach cell is the straight line,
unless that line crosses a cell a remembered enemy defense covers. Then the
raid goes round by a side waypoint: one of the two cells 12 cells to either
side of the middle of that line, inside the map, whichever gives a way that
crosses no covered cell (the shorter, when both do). When neither does, there
is no safe way to the target.

With `waypoints` of [routing] on, a straight line that crosses a covered cell
is first gone round by the way the graph of waypoints gives to the approach
cell (see `waypoints`): its cells in turn, none of them on or near covered
ground. The side waypoints are tried only when the graph gives no way.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.cells import Bounds
from ucla_agent.games.redalert.tuning import RaidTuning
from ucla_agent.games.redalert.waypoints import WaypointRoute
from ucla_agent.games.redalert.world import Cell

# What finds a way through the graph of waypoints from a cell to another; None when there is none.
Router = Callable[[Cell, Cell], WaypointRoute | None]


@dataclass(frozen=True)
class Route:
    """A raid's way to its target."""

    # The cells to go to in turn, the approach cell last.
    legs: tuple[Cell, ...]
    # Every cell the way passes through, from where it starts to the approach cell.
    path: tuple[Cell, ...]
    # The way through the graph of waypoints the legs are, when they are; None for any other way.
    waypoint_route: WaypointRoute | None = None

    @property
    def approach(self) -> Cell:
        """The cell the raid attacks from."""
        return self.legs[-1]


def approach_cell(staging: Cell, target: Cell, tuning: RaidTuning) -> Cell:
    """The cell `approach_cells` short of the target on the line from the staging point; the staging point if nearer."""
    if cells.distance(staging, target) <= tuning.approach_cells:
        return staging
    return cells.toward(target, staging, tuning.approach_cells)


def route(
    start: Cell,
    staging: Cell,
    target: Cell,
    bounds: Bounds,
    is_covered: Callable[[Cell], bool],
    tuning: RaidTuning,
    router: Router | None = None,
) -> Route | None:
    """The way from `start` to the approach cell of a target that crosses no covered cell: the straight line, else the
    way `router` finds through the graph of waypoints when one is given, else by a side waypoint; None when there is
    none."""
    approach = approach_cell(staging, target, tuning)
    direct = cells.line(start, approach)
    if not any(map(is_covered, direct)):
        return Route((approach,), tuple(direct))
    by_waypoints = None if router is None else router(start, approach)
    if by_waypoints is not None:
        return _through(by_waypoints)
    sides = side_waypoints(start, approach, bounds, tuning.side_offset_cells)
    ways = [_round_by(side, start, approach) for side in sides]
    clear = [way for way in ways if not any(map(is_covered, way.path))]
    return min(clear, key=lambda way: len(way.path), default=None)


def _through(found: WaypointRoute) -> Route:
    """The way through the graph of waypoints as a raid's: its cells in turn, and every cell passed on the way."""
    stops = (found.start, *found.legs)
    path = (found.start, *(cell for first, second in zip(stops, stops[1:]) for cell in cells.line(first, second)[1:]))
    return Route(found.legs, path, found)


def _round_by(side: Cell, start: Cell, approach: Cell) -> Route:
    """The way from `start` to the approach cell through a side waypoint."""
    return Route((side, approach), (*cells.line(start, side), *cells.line(side, approach)[1:]))


def side_waypoints(start: Cell, end: Cell, bounds: Bounds, offset: int) -> list[Cell]:
    """The two cells `offset` cells to either side of the middle of the line from `start` to `end`, inside the map."""
    (x, y), (end_x, end_y) = start, end
    length = math.hypot(end_x - x, end_y - y)
    if length == 0:
        return []
    # The unit vector at a right angle to the line.
    across = (-(end_y - y) / length, (end_x - x) / length)
    middle = ((x + end_x) / 2, (y + end_y) / 2)
    return [
        cells.clamped(
            cells.nearest_cell(middle[0] + side * offset * across[0], middle[1] + side * offset * across[1]), bounds
        )
        for side in (1, -1)
    ]
