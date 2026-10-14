"""The indirect way a scout goes with `[scouting] indirect_route` on: past the ore fields and around the enemy's flank.

A scout that drives straight at the enemy's start meets whatever guards the
way in, and sees little but that. The indirect way goes wide instead, in this
order:

1. a side waypoint, scout_side_cells to one side of the line from our base to
   the enemy's start, scout_side_share of the way along it; one mission goes
   by one side, the next by the other;
2. up to scout_fields_max ore fields whose middles lie within
   scout_field_cells of the way out (from our base to the side waypoint and on
   to the flank) and that our units have not had in sight for two minutes,
   the nearest the side waypoint first;
3. the enemy base's flank on that side: scout_view_cells from the enemy's
   start, at a right angle to the line;
4. the viewing arc: the four points north, east, south and west of the
   enemy's start at scout_view_cells, those on the far side of the base from
   the flank on, round to the other flank;
5. home by the opposite side: the side waypoint on the other side of the line,
   then the staging point, where the scout is released.

Every waypoint is kept inside the map. One within scout_min_enemy_cells of the
enemy's start is dropped. One that a remembered enemy defense covers (its
range and the margin) is moved out along the line from that defense to just
beyond its cover, and dropped when it is still covered or too near the
enemy's start there; an arc point is dropped at once, the arc going on
without it.
"""

from __future__ import annotations

import math

from ucla_agent.games.redalert import cells, coverage
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.own_fields import not_own
from ucla_agent.games.redalert.sight import SightGrid
from ucla_agent.games.redalert.terrain import OrePatch
from ucla_agent.games.redalert.world import Cell, EnemyRecord, World

# An ore field our units have had in sight this lately is not worth going by.
FIELD_UNSEEN_SECONDS = 120
# A waypoint moved out of a defense's cover is put this far beyond the edge of it.
_BEYOND_COVER_CELLS = 1
# The directions of today's circle around a base: north, east, south and west.
_COMPASS = ((0, -1), (1, 0), (0, 1), (-1, 0))
# Angles this close count as the same: the flank's own point is not part of the arc, the other flank's is.
_ANGLE_TOLERANCE = 1e-9
_FULL_TURN = 2 * math.pi

Vector = tuple[float, float]


def indirect_route(world: World, ctx: MatchContext, side: int, sight: SightGrid) -> list[Cell]:
    """The waypoints of a mission by the indirect way on a side (1 or -1 of the line), the staging point last."""
    tuning = ctx.tuning.scouting
    base, enemy = world.base_center, world.enemy_base
    length = cells.distance(base, enemy)
    along = ((enemy[0] - base[0]) / length, (enemy[1] - base[1]) / length)
    across = (-along[1] * side, along[0] * side)
    way_along = length * tuning.scout_side_share
    side_point = _offset(base, along, way_along, across, tuning.scout_side_cells)
    flank = _offset(enemy, along, 0, across, tuning.scout_view_cells)
    keeper = _Keeper(world, ctx)
    fields = [keeper.moved(field) for field in _unseen_fields(base, side_point, flank, world, ctx, sight)]
    outbound = [keeper.moved(side_point), *[field for field in fields if field is not None][: tuning.scout_fields_max]]
    arc = [keeper.kept(point) for point in _arc(enemy, along, across, tuning.scout_view_cells)]
    home = keeper.moved(_offset(base, along, way_along, across, -tuning.scout_side_cells))
    waypoints = [*outbound, keeper.moved(flank), *arc, home]
    return [*(point for point in waypoints if point is not None), keeper.staging_point]


def _offset(origin: Cell, along: Vector, distance_along: float, across: Vector, distance_across: float) -> Cell:
    """The cell `distance_along` from a cell along a direction and `distance_across` across it."""
    return cells.nearest_cell(
        origin[0] + along[0] * distance_along + across[0] * distance_across,
        origin[1] + along[1] * distance_along + across[1] * distance_across,
    )


def _unseen_fields(
    base: Cell, side_point: Cell, flank: Cell, world: World, ctx: MatchContext, sight: SightGrid
) -> list[Cell]:
    """The middles of the ore fields near the way out that have not been in sight for two minutes, nearest the side
    waypoint first."""
    tuning, tick = ctx.tuning.scouting, world.tick
    near = [
        patch.center
        for patch in not_own(ctx.terrain.ore_patches, world, tuning.own_field_cells)
        if _is_unseen(patch, sight, tick)
        and min(
            distance_to_segment(patch.center, base, side_point), distance_to_segment(patch.center, side_point, flank)
        )
        <= tuning.scout_field_cells
    ]
    return sorted(near, key=lambda center: (cells.distance(center, side_point), center))


def _is_unseen(patch: OrePatch, sight: SightGrid, tick: int) -> bool:
    """Whether our units have not had a field's middle in sight for two minutes."""
    seen = sight.last_seen(patch.center)
    return seen is None or tick - seen >= FIELD_UNSEEN_SECONDS * TICKS_PER_SECOND


def distance_to_segment(point: Cell, start: Cell, end: Cell) -> float:
    """The distance from a cell to the nearest point of the straight segment between two others."""
    span = (end[0] - start[0], end[1] - start[1])
    squared = span[0] ** 2 + span[1] ** 2
    if squared == 0:
        return cells.distance(point, start)
    share = ((point[0] - start[0]) * span[0] + (point[1] - start[1]) * span[1]) / squared
    share = min(1.0, max(0.0, share))
    return math.hypot(point[0] - (start[0] + share * span[0]), point[1] - (start[1] + share * span[1]))


def _arc(enemy: Cell, along: Vector, across: Vector, radius: int) -> list[Cell]:
    """The points of today's circle around the enemy's start at `radius` that lie on the far side of the base from
    the flank on, in the order a scout coming round from the flank meets them."""
    turned = []
    for east, south in _COMPASS:
        # The angle from the flank, through the base's far side, to the other flank: 0 to pi on the far half. The
        # turn is taken whole so that a sign of zero (an angle of -pi for pi) cannot drop the other flank.
        angle = math.atan2(east * along[0] + south * along[1], east * across[0] + south * across[1]) % _FULL_TURN
        if _ANGLE_TOLERANCE < angle <= math.pi + _ANGLE_TOLERANCE:
            turned.append((angle, (enemy[0] + east * radius, enemy[1] + south * radius)))
    return [point for _, point in sorted(turned)]


class _Keeper:
    """Keeps a mission's waypoints inside the map, away from the enemy's start and out of its defenses' cover."""

    def __init__(self, world: World, ctx: MatchContext) -> None:
        self._world, self._ctx = world, ctx
        self._bounds = world.map_bounds
        self.staging_point = cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells)

    def kept(self, point: Cell) -> Cell | None:
        """The waypoint inside the map, or None when it is too near the enemy's start or a defense covers it."""
        inside = cells.clamped(point, self._bounds)
        return inside if self._is_allowed(inside) else None

    def moved(self, point: Cell) -> Cell | None:
        """The waypoint inside the map, moved out of the cover of the defense that covers it; None when it cannot be
        kept even so."""
        inside = cells.clamped(point, self._bounds)
        covering = next(
            (defense for defense in self._world.known_enemy_defenses if self._covers(defense, inside)), None
        )
        if covering is not None:
            beyond = coverage.reach(covering.type, self._world.items, self._ctx.tuning.coverage) + _BEYOND_COVER_CELLS
            inside = cells.clamped(cells.toward((covering.x, covering.y), inside, beyond), self._bounds)
        return inside if self._is_allowed(inside) else None

    def _covers(self, defense: EnemyRecord, point: Cell) -> bool:
        return coverage.is_covered(point, (defense,), self._world.items, self._ctx.tuning.coverage)

    def _is_allowed(self, point: Cell) -> bool:
        """Whether a waypoint keeps scout_min_enemy_cells from the enemy's start and out of every defense's cover."""
        world, tuning = self._world, self._ctx.tuning
        if cells.is_within(point, world.enemy_base, tuning.scouting.scout_min_enemy_cells):
            return False
        return not coverage.is_covered(point, world.known_enemy_defenses, world.items, tuning.coverage)
