"""Where a designated scout goes next: the places worth a look, the one gone unseen longest first.

The scout slots' scouts (see `scout_slots`) are not sent at the enemy's start
and back. They go from place to place, each time to the one our units have
not had in sight for the longest, so that over a match they see as much as
they can. The places are of four kinds:

- the enemy base's flanks: the points scout_view_cells north, east, south
  and west of the enemy's start, at the edge of what a scout sees of the base
  from outside it. The base's middle is never a place to go;
- the ore fields, at their middles: where the enemy expands and harvests;
- the enemy's expansions: each remembered enemy building more than
  base_seen_cells from its start, but for a defense;
- the map's edges: its corners and the middles of its sides, a few cells
  inside, so that the scouts also look where neither base is.

How long a place has gone unseen is read from the sight grid (see `sight`):
the seconds since its square was last within sight of one of our units, or
since the match began for a square never seen. The places that tell the most
about the enemy (an expansion, the base's flanks and the ore fields, in that
order) are ranked as though they had gone unseen a while longer, so that of
two places as stale the one with more to see is first.

A place the scouts are never sent to is no place at all: one within
scout_min_enemy_cells of the enemy's start, within scout_center_clear_cells of
the map's middle (where the armies meet), under a remembered enemy defense,
near the place the other scout is heading for, which it will see, or within
hurt_area_cells of where a scout was hurt lately (see `scout_berth`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ucla_agent.games.redalert import cells, coverage
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.own_fields import not_own
from ucla_agent.games.redalert.sight import SIGHT_CELLS, SightGrid
from ucla_agent.games.redalert.world import DEFENSE_TYPES, Cell, World

# The kinds of place, as the short name of the area a candidate is in. A flank's name has its compass side.
FLANK = "enemy base {side} flank"
ORE_FIELD = "ore field"
EXPANSION = "expansion"
MAP_EDGE = "map edge"
# The compass sides of the enemy base's flanks, with the direction of each from its start.
_FLANK_SIDES = (("N", (0, -1)), ("E", (1, 0)), ("S", (0, 1)), ("W", (-1, 0)))
# How many seconds longer than it has a place of each kind is taken to have gone unseen: what the enemy has there.
EXPANSION_BONUS_SECONDS = 90
FLANK_BONUS_SECONDS = 60
ORE_FIELD_BONUS_SECONDS = 30
# The map's edge points lie this many cells inside it.
EDGE_INSET_CELLS = 4
# A place this close to where the other scout is heading is in the sight of that scout once it is there.
AVOID_CELLS = 2 * SIGHT_CELLS


@dataclass(frozen=True)
class Candidate:
    """A place a scout may be sent to look at."""

    cell: Cell
    # The short name of the area: an enemy base flank, an ore field, an expansion or a map edge.
    area: str
    # How long since our units last had the place in sight, in seconds; since the match began when they never did.
    unseen_seconds: float
    # What the place is ranked by: the seconds unseen and the bonus of its kind.
    score: float


def candidates(
    world: World, ctx: MatchContext, sight: SightGrid, avoid: Iterable[Cell] = (), hurt: Iterable[Cell] = ()
) -> list[Candidate]:
    """The places a scout may be sent to look at, the most worth a look first; a place near one of the cells to
    `avoid`, or within hurt_area_cells of one of the `hurt` cells, is left out. Asked once the map, the base and the
    enemy's start are known."""
    avoided, hurt_areas = list(avoid), list(hurt)
    kept: dict[Cell, Candidate] = {}
    for cell, area, bonus in _places(world, ctx):
        inside = cells.clamped(cell, world.map_bounds)
        if inside not in kept and _may_go(inside, avoided, hurt_areas, world, ctx):
            unseen = _unseen_seconds(inside, sight, world.tick)
            kept[inside] = Candidate(inside, area, unseen, unseen + bonus)
    return sorted(kept.values(), key=lambda candidate: (-candidate.score, candidate.cell))


def map_center(world: World) -> Cell:
    """The middle cell of the playable part of the map."""
    left, top, width, height = world.map_bounds
    return left + width // 2, top + height // 2


def _places(world: World, ctx: MatchContext) -> list[tuple[Cell, str, int]]:
    """Every place of the four kinds, with its area and the bonus of its kind, the kinds with the larger bonus first;
    the same cell may come more than once."""
    tuning = ctx.tuning.scouting
    (enemy_x, enemy_y), view = world.enemy_base, tuning.scout_view_cells
    places = [
        ((enemy_x + east * view, enemy_y + south * view), FLANK.format(side=side), FLANK_BONUS_SECONDS)
        for side, (east, south) in _FLANK_SIDES
    ]
    places += [(cell, EXPANSION, EXPANSION_BONUS_SECONDS) for cell in _expansions(world, ctx)]
    fields = not_own(ctx.terrain.ore_patches, world, tuning.own_field_cells)
    places += [(patch.center, ORE_FIELD, ORE_FIELD_BONUS_SECONDS) for patch in fields]
    return places + [(cell, MAP_EDGE, 0) for cell in _edges(world)]


def _expansions(world: World, ctx: MatchContext) -> list[Cell]:
    """The cells of the remembered enemy buildings away from the enemy's start that are not defenses."""
    far = ctx.tuning.scouting.base_seen_cells
    return [
        (record.x, record.y)
        for record in world.enemy_memory.values()
        if record.is_building
        and record.type not in DEFENSE_TYPES
        and not cells.is_within((record.x, record.y), world.enemy_base, far)
    ]


def _edges(world: World) -> list[Cell]:
    """The map's corners and the middles of its sides, EDGE_INSET_CELLS inside it."""
    left, top, width, height = world.map_bounds
    xs = (left + EDGE_INSET_CELLS, left + width // 2, left + width - 1 - EDGE_INSET_CELLS)
    ys = (top + EDGE_INSET_CELLS, top + height // 2, top + height - 1 - EDGE_INSET_CELLS)
    middle = map_center(world)
    return [(x, y) for y in ys for x in xs if (x, y) != middle]


def _may_go(cell: Cell, avoided: list[Cell], hurt_areas: list[Cell], world: World, ctx: MatchContext) -> bool:
    """Whether a scout may be sent to a cell: away from the enemy's start, the map's middle, every remembered defense,
    the cells to avoid and the hurt areas."""
    tuning = ctx.tuning
    if cells.is_within(cell, world.enemy_base, tuning.scouting.scout_min_enemy_cells):
        return False
    if cells.is_within(cell, map_center(world), tuning.scouting.scout_center_clear_cells):
        return False
    if any(cells.is_within(cell, other, AVOID_CELLS) for other in avoided):
        return False
    if any(cells.is_within(cell, area, tuning.scouting.hurt_area_cells) for area in hurt_areas):
        return False
    return not coverage.is_covered(cell, world.known_enemy_defenses, world.items, tuning.coverage)


def _unseen_seconds(cell: Cell, sight: SightGrid, tick: int) -> float:
    """How long since our units last had a cell's square in sight, in seconds; since tick 0 when they never did."""
    seen = sight.last_seen(cell)
    return (tick - (seen or 0)) / TICKS_PER_SECOND
