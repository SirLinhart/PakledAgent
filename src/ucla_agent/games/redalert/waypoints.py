"""Waypoint routing: a graph of places to go by, and the shortest way through it around the enemy's defenses.

A way that runs straight at its destination meets whatever stands on it, and
a way round by one side waypoint (see `raid_routes`) does not get round a
line of defenses. With `waypoints` of the tuning file's [routing] switched
on, the agent keeps a graph of waypoints for the match.

The nodes are the center of every ore field of the map picture (see
`terrain`), the base's center, the four cells scout_view_cells of [scouting]
north, east, south and west of the enemy's start, and eight cells by the
map's edge, its corners and the middles of its sides, 4 cells inside it; each
inside the map. The two ends of every walked route (see `route_walk`) are
nodes as well.

Two nodes within edge_max_cells of each other are joined when the straight
line of cells between them crosses no blocked ground and no cell within
edge_clear_margin_cells of the ground a remembered enemy defense covers (see
`coverage`). The map picture tells only where nothing can be built: its ore
aside, every such cell is taken to be blocked, which errs on the safe side. A
node on such a cell is joined to nothing. A walked route joins its two ends by
the length of the way walked, whatever the straight line between them, unless
an end or a point kept of that way lies within that margin of covered ground.

The graph is built in the first cycle the map, the base and the enemy's start
are known, and again in every cycle the remembered defenses or the walked
routes have changed.

The way from a cell to a destination (`Waypoints.route`) joins each of the two
to the nodes within edge_max_cells it has a clear line to, by the rule of an
edge, or to the nearest node it has one to when none is that near. It is the
chain of nodes between them of the least length, found by A*, with the
destination last. There is none when either has a clear line to no node, or
no chain joins them. So no cell of a way lies on or near covered ground.

Each way a user of the graph takes is written as one `route` event
(`log_route`): who took it (`raid`, `scout`, `flank` or `mcv`), from where to
where, how many nodes it passes and how long it is.
"""

from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass
from typing import Sequence

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, coverage
from ucla_agent.games.redalert.cells import Bounds
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.world import Cell, World

# The match record's event of a way taken, and who takes ways: a raid, a scout, main on the commander's flank, the MCV.
ROUTE_EVENT = "route"
RAID = "raid"
SCOUT = "scout"
FLANK = "flank"
MCV = "mcv"
# A user that comes this near a cell of its way goes on to the next, in cells.
REACHED_CELLS = 3
# How far inside the map's edge its eight waypoints lie, in cells.
EDGE_INSET_CELLS = 4
_LENGTH_DECIMALS = 2
# The directions of the four waypoints around the enemy's start: north, east, south and west.
_FLANK_DIRECTIONS = ((0, -1), (1, 0), (0, 1), (-1, 0))


@dataclass(frozen=True)
class WaypointRoute:
    """A way through the graph of waypoints from a cell to a destination."""

    start: Cell
    goal: Cell
    # The nodes of the chain in the order they are passed, but a node on `start` itself.
    nodes: tuple[Cell, ...]
    # How long the way is from `start` through the nodes to `goal`, in cells.
    length: float

    @property
    def legs(self) -> tuple[Cell, ...]:
        """The cells to go to in turn: the nodes, and `goal` last."""
        return self.nodes if self.nodes[-1:] == (self.goal,) else (*self.nodes, self.goal)


class Waypoints:
    """The match's graph of waypoints. See the module's note."""

    def __init__(self) -> None:
        # What the graph was last built from; None before it was first built.
        self._built_from: tuple[object, ...] | None = None
        # The ground no way of the graph crosses: blocked ground and the ground near a remembered defense's cover.
        self._closed: frozenset[Cell] = frozenset()
        # The terrain the blocked ground was last read from, and that ground: the terrain is the match's.
        self._blocked_of: tuple[Terrain, frozenset[Cell]] | None = None
        self._max_cells = 0
        # Node -> each node it is joined to, with the length of the join in cells.
        self._edges: dict[Cell, dict[Cell, float]] = {}

    @property
    def nodes(self) -> tuple[Cell, ...]:
        """The nodes that are not on closed ground, in the order they were found."""
        return tuple(self._edges)

    def has_edge(self, first: Cell, second: Cell) -> bool:
        """Whether two nodes are joined."""
        return second in self._edges.get(first, {})

    def update(self, world: World, terrain: Terrain, walked: Sequence[tuple[Cell, ...]], tuning: Tuning) -> None:
        """Build the graph anew when the map, the base, the enemy's start, the remembered defenses or the `walked`
        routes have changed since it was last built; nothing while the map, the base or the enemy's start is not
        known."""
        if world.map_bounds is None or world.base_center is None or world.enemy_base is None:
            return
        defenses = tuple(sorted((record.type, record.x, record.y) for record in world.known_enemy_defenses))
        built_from = (terrain, world.map_bounds, world.base_center, world.enemy_base, defenses, tuple(walked))
        if built_from == self._built_from:
            return
        self._built_from = built_from
        self._build(world, terrain, walked, tuning)

    def _build(self, world: World, terrain: Terrain, walked: Sequence[tuple[Cell, ...]], tuning: Tuning) -> None:
        """The nodes and their joins as the module's note gives them."""
        danger = _danger(world, tuning)
        self._closed = self._blocked(terrain) | danger
        self._max_cells = tuning.routing.edge_max_cells
        joins = [
            (route[0], route[-1], _walked_length(route))
            for route in walked
            if route[0] != route[-1] and danger.isdisjoint(route)
        ]
        places = [
            *(patch.center for patch in terrain.ore_patches),
            world.base_center,
            *_flank_points(world.enemy_base, world.map_bounds, tuning.scouting.scout_view_cells),
            *_edge_points(world.map_bounds),
            *(end for first, second, _ in joins for end in (first, second)),
        ]
        nodes = [node for node in dict.fromkeys(places) if node not in self._closed]
        self._edges = {node: {} for node in nodes}
        for first, second in itertools.combinations(nodes, 2):
            if cells.is_within(first, second, self._max_cells) and self.is_clear(first, second):
                self._join(first, second, cells.distance(first, second))
        for first, second, length in joins:
            if first in self._edges and second in self._edges:
                self._join(first, second, min(length, self._edges[first].get(second, math.inf)))

    def _join(self, first: Cell, second: Cell, length: float) -> None:
        self._edges[first][second] = self._edges[second][first] = length

    def _blocked(self, terrain: Terrain) -> frozenset[Cell]:
        """The ground of a terrain no unit is taken to cross: every cell nothing can be built on but the ore."""
        if self._blocked_of is None or self._blocked_of[0] is not terrain:
            ore = {cell for patch in terrain.ore_patches for cell in patch.cells}
            self._blocked_of = terrain, terrain.blocked - ore
        return self._blocked_of[1]

    def is_clear(self, start: Cell, end: Cell) -> bool:
        """Whether the straight line of cells between two cells, both included, crosses no closed ground."""
        return self._closed.isdisjoint(cells.line(start, end))

    def route(self, start: Cell, goal: Cell) -> WaypointRoute | None:
        """The shortest way through the graph from `start` to `goal`, each joined to the nodes it reaches (`_reached`);
        None when either reaches no node or no chain of nodes joins them."""
        starts, goals = self._reached(start), self._reached(goal)
        if not starts or not goals:
            return None
        found = self._shortest(goal, starts, goals)
        if found is None:
            return None
        chain, length = found
        return WaypointRoute(start, goal, tuple(chain[1:] if chain[0] == start else chain), length)

    def _reached(self, cell: Cell) -> dict[Cell, float]:
        """The nodes a cell has a clear line to, by its distance from each: those within edge_max_cells, else the
        nearest; none when it has a clear line to no node."""
        if cell in self._closed:
            return {}
        near = {
            node: cells.distance(cell, node)
            for node in self._edges
            if cells.is_within(cell, node, self._max_cells) and self.is_clear(cell, node)
        }
        if near:
            return near
        beyond = sorted(self._edges, key=lambda node: cells.distance(cell, node))
        nearest = next((node for node in beyond if self.is_clear(cell, node)), None)
        return {} if nearest is None else {nearest: cells.distance(cell, nearest)}

    def _shortest(
        self, goal: Cell, starts: dict[Cell, float], goals: dict[Cell, float]
    ) -> tuple[list[Cell], float] | None:
        """The chain of nodes of the least length from the nodes the start reaches (`starts`, by their distance from
        it) to the goal, which the nodes of `goals` reach, and that length; None when no chain joins them.

        A*, with the straight distance to the goal as the estimate of the length left: no join is shorter than
        that. The key None stands for the goal.
        """
        order = itertools.count()
        best: dict[Cell | None, float] = dict(starts)
        came_from: dict[Cell | None, Cell | None] = dict.fromkeys(starts)
        frontier = [(length + cells.distance(node, goal), next(order), node) for node, length in starts.items()]
        heapq.heapify(frontier)
        while frontier:
            _, _, node = heapq.heappop(frontier)
            if node is None:
                return _chain(came_from), best[None]
            for onward, step in self._steps(node, goals):
                length = best[node] + step
                if length < best.get(onward, math.inf):
                    best[onward], came_from[onward] = length, node
                    estimate = length if onward is None else length + cells.distance(onward, goal)
                    heapq.heappush(frontier, (estimate, next(order), onward))
        return None

    def _steps(self, node: Cell, goals: dict[Cell, float]) -> list[tuple[Cell | None, float]]:
        """Where a way can go on from a node, with the length of each step: the nodes it is joined to, and the goal
        (None) when the node reaches it."""
        steps: list[tuple[Cell | None, float]] = list(self._edges[node].items())
        if node in goals:
            steps.append((None, goals[node]))
        return steps


def log_route(user: str, route: WaypointRoute, tick: int) -> None:
    """Write one `route` event of a way a user took at a tick."""
    records.log(
        ROUTE_EVENT,
        {
            "tick": tick,
            "user": user,
            "start": list(route.start),
            "goal": list(route.goal),
            "node_count": len(route.nodes),
            "length_cells": round(route.length, _LENGTH_DECIMALS),
        },
    )


def _chain(came_from: dict[Cell | None, Cell | None]) -> list[Cell]:
    """The nodes from the start's to the goal's, each found from the one it came from; the goal is the key None."""
    chain, node = [], came_from[None]
    while node is not None:
        chain.append(node)
        node = came_from[node]
    return chain[::-1]


def _danger(world: World, tuning: Tuning) -> frozenset[Cell]:
    """The cells within edge_clear_margin_cells of the ground a remembered enemy defense covers."""
    margin = tuning.routing.edge_clear_margin_cells
    found: set[Cell] = set()
    for defense in world.known_enemy_defenses:
        reach = coverage.reach(defense.type, world.items, tuning.coverage) + margin
        span = math.floor(reach)
        found.update(
            (defense.x + dx, defense.y + dy)
            for dx in range(-span, span + 1)
            for dy in range(-span, span + 1)
            if dx * dx + dy * dy <= reach * reach
        )
    return frozenset(found)


def _flank_points(enemy_start: Cell, bounds: Bounds, distance: int) -> list[Cell]:
    """The cells `distance` cells north, east, south and west of the enemy's start, inside the map."""
    x, y = enemy_start
    return [cells.clamped((x + dx * distance, y + dy * distance), bounds) for dx, dy in _FLANK_DIRECTIONS]


def _edge_points(bounds: Bounds) -> list[Cell]:
    """The corners of the map and the middles of its sides, EDGE_INSET_CELLS inside it."""
    left, top, width, height = bounds
    columns = (left + EDGE_INSET_CELLS, left + width // 2, left + width - 1 - EDGE_INSET_CELLS)
    rows = (top + EDGE_INSET_CELLS, top + height // 2, top + height - 1 - EDGE_INSET_CELLS)
    middle = (columns[1], rows[1])
    return [cells.clamped((x, y), bounds) for y in rows for x in columns if (x, y) != middle]


def _walked_length(route: tuple[Cell, ...]) -> float:
    """How long a walked way is through its points, in cells."""
    return sum(cells.distance(first, second) for first, second in zip(route, route[1:]))
