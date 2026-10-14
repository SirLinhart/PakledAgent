"""Walked routes: a rifleman walks to each far field before the base grows toward it.

The base walks toward its far field along a straight line (see `placement`),
which the map picture alone decides; a cliff or water the picture does not
show can stand in the way, and the base keeps growing at it. The game's own
path finding knows the way round. With `walked_routes` of the tuning file's
[placement] switched on (it works with `creep_from_opening`), a rifleman walks
to each far field first, and the base grows along the way it walked.

The walker. When the far field of the main base's line, or else of the
second yard's, has no walked route yet, one rifleman is sent with `move` to
the field's ore cell nearest the line's start: the first idle one of the
home guard or main, else, during the opening, the next one trained once a
walk is wanted. A unit is taken only from the cycle after the one it was
first seen in: the scout slots take the units they hold in the cycle those
are first seen, so a walker never holds a scout slot. For the second yard's
line a rifleman within `second_line_radius_cells` of that yard is taken
first. One walks at a time. While it walks it keeps its group but is marked
as walking (see `groups`), so that no manager of its group orders it. A field
whose straight way from the line's start crosses the cover of a remembered
enemy defense (see `coverage`) gets no walker: it joins the fields placement
skipped, with a `creep_placement` event of the reason `walk_defended`. So
does a field whose center lies within `walker_enemy_refinery_cells` of a
remembered enemy refinery, with the reason `walk_enemy_field`: that is the
enemy's own ground. No walker is sent while the base is under threat, nor
to a field while an enemy unit that fights was seen within 8 cells of its
straight way in the last `walker_enemy_seen_seconds`: such a field waits,
and the next line's may be walked meanwhile.

The route. The walker's cell is read in every state and kept as a point of
the route when it lies `walk_sample_cells` or more from the last point kept.
Once the walker comes within 3 cells of the ore cell the route is done: it is
kept for the field until the field is skipped or taken (see `placement`), and
placement grows along it. The walker is then sent back to where it started
and its mark taken off, so that it is its group's again. The routes kept are
published in every cycle as the match's walked routes. A walker that a
manager took into another group, or that is gone without word of its death,
ends its walk as lost (`walker_lost`) with the field left as it was, and the
next rifleman walks it again. One that dies (a `unit_lost` event names it)
ends its walk as killed (`walker_killed`): once `walker_deaths_to_skip`
walkers were killed on their way to a field, the field is skipped with the
reason `walk_killed`, so that no more riflemen are fed to whatever guards
the way.

Skipping. The field is given up, as a field placement skipped (see
`refused`), with a `creep_placement` event of the reason, and the walker
sent back, when the walker has not arrived within the larger of
`walk_timeout_seconds` and `walk_detour_factor` times the straight distance
over its speed plus 10 seconds, or its cell has not changed for 10 seconds
short of the ore cell (the reason `walk_unreachable`): the engine found no
way, or none it would take. And at once, while it walks, when its way grows
longer than `walk_detour_factor` times the straight distance from its start
to the ore cell (the reason `walk_detour`): a way round so long is no way
for the base to grow along.

Each walk writes `route_walk` events as it is sent, done or failed: the field,
the line, the walker's type, the length of its way so far, the straight
distance from its start to the ore cell and how many points it kept.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, coverage, placement
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.cargo import UNIT_LOST_EVENT
from ucla_agent.games.redalert.creep import NearestFarField
from ucla_agent.games.redalert.family import REFINERY, RIFLE_INFANTRY
from ucla_agent.games.redalert.groups import GUARD, MAIN
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.placement import SECOND_LINE, FarField
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.terrain import OrePatch
from ucla_agent.games.redalert.tuning import PlacementTuning
from ucla_agent.games.redalert.world import Cell, Unit, World

# The match record's event of a walk, its phases and the reasons a walk ends when its walker is gone from its group:
# lost, or killed.
ROUTE_WALK_EVENT = "route_walk"
SENT = "sent"
DONE = "done"
FAILED = "failed"
WALKER_LOST = "walker_lost"
WALKER_KILLED = "walker_killed"
# The event a skipped field is written as, as every field placement skipped is, and the reason for a field whose
# straight way crosses a remembered enemy defense's cover.
CREEP_PLACEMENT_EVENT = "creep_placement"
WALK_DEFENDED_REASON = "walk_defended"
# The reasons a field is skipped for by its walk: no way within the time, or a way too long.
WALK_UNREACHABLE_REASON = "walk_unreachable"
WALK_DETOUR_REASON = "walk_detour"
# The reason a field is skipped for once walkers were killed on their way to it, and for one by an enemy refinery.
WALK_KILLED_REASON = "walk_killed"
WALK_ENEMY_FIELD_REASON = "walk_enemy_field"
# No walker goes to a field while an enemy unit that fights was seen lately this close to the straight way to it.
ENEMY_NEAR_WAY_CELLS = 8
# What walks, and the groups it is taken from.
WALKER_TYPE = RIFLE_INFANTRY
_WALKER_GROUPS = (GUARD, MAIN)
# How near the ore cell a walker has arrived.
ARRIVAL_CELLS = 3
# How long a walker may stand still short of the ore cell, what its time to arrive is given beyond the detour's, and the
# speed it is taken to have when the item table gives none: no faster than a rifleman.
STAND_STILL_SECONDS = 10
TIMEOUT_SLACK_SECONDS = 10
_UNKNOWN_SPEED_CELLS_PER_SECOND = 1.0
_DISTANCE_DECIMALS = 2


@dataclass
class _Walk:
    """A walk under way: the far field and line it is for, the walker and its group, and the route kept so far."""

    field: FarField
    line: str
    unit_id: int
    unit_type: str
    group: str
    # The field's ore cell nearest the line's start, which the walker is sent to.
    ore: Cell
    # The cells kept, the walker's own when it was sent first.
    points: list[Cell]
    # The tick the walker was sent at, and how many ticks it has to arrive.
    sent_tick: int
    timeout_ticks: int
    # How long the way through the points kept is, in cells.
    length: float = 0.0
    # The walker's cell in the last state, and the tick it was last seen to move at.
    last_cell: Cell = field(init=False)
    moved_tick: int = field(init=False)

    def __post_init__(self) -> None:
        self.last_cell, self.moved_tick = self.start, self.sent_tick

    @property
    def start(self) -> Cell:
        """Where the walker stood when it was sent."""
        return self.points[0]

    @property
    def straight(self) -> float:
        """The straight distance from the walker's start to the ore cell, in cells."""
        return cells.distance(self.start, self.ore)

    def record(self, position: Cell, tick: int, sample_cells: int) -> None:
        """Note the walker's cell in a state at a tick: whether it moved, and the cell as a point when it lies
        `sample_cells` or more from the last point kept."""
        if position != self.last_cell:
            self.last_cell, self.moved_tick = position, tick
        if cells.distance(self.points[-1], position) >= sample_cells:
            self._keep(position)

    def walked(self, position: Cell) -> float:
        """How long the walker's way is to its cell now: through the points kept, then straight to the cell."""
        return self.length + cells.distance(self.points[-1], position)

    def verdict(self, position: Cell, tick: int, tuning: PlacementTuning) -> str | None:
        """DONE once the walker is within ARRIVAL_CELLS of the ore cell, else the reason its field is skipped for:
        `walk_detour` once its way is longer than `walk_detour_factor` times the straight distance, `walk_unreachable`
        once it stood still STAND_STILL_SECONDS or its time is up; None while it walks on."""
        if cells.is_within(position, self.ore, ARRIVAL_CELLS):
            return DONE
        if self.walked(position) > tuning.walk_detour_factor * self.straight:
            return WALK_DETOUR_REASON
        is_still = tick - self.moved_tick >= STAND_STILL_SECONDS * TICKS_PER_SECOND
        is_late = tick - self.sent_tick >= self.timeout_ticks
        return WALK_UNREACHABLE_REASON if is_still or is_late else None

    def finish(self, position: Cell) -> tuple[Cell, ...]:
        """The route walked, ending at the walker's cell on arrival."""
        if position != self.points[-1]:
            self._keep(position)
        return tuple(self.points)

    def _keep(self, position: Cell) -> None:
        """Keep a cell as the next point of the route."""
        self.length += cells.distance(self.points[-1], position)
        self.points.append(position)


class RouteWalks:
    """Walks a rifleman to each far field and keeps the routes walked, a rule of upkeep. See the module's note."""

    def __init__(self, far_field: NearestFarField) -> None:
        # The creep keeper, which names the far field of each line.
        self._far_field = far_field
        # Field -> the route walked to it, from where the walker started to where it arrived.
        self.routes: dict[OrePatch, tuple[Cell, ...]] = {}
        self._walk: _Walk | None = None
        # Unit id -> the tick the unit was first seen at, for the own units that fight.
        self._first_seen: dict[int, int] = {}
        # The tick since which a walk is wanted and no walker was found; None while none is wanted.
        self._wanted_since: int | None = None
        # Field -> how many walkers were killed on their way to it.
        self._killed: Counter[OrePatch] = Counter()

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """With `walked_routes` and `creep_from_opening` on: the routes kept published as `ctx.walked_routes`, and the
        walk under way followed, else a walker sent to the next far field without a route; none with either off. A walk that ends leaves the next one to the next cycle, so
        that the walker sent back is not sent on at once."""
        tuning = ctx.tuning.placement
        if not (tuning.walked_routes and tuning.creep_from_opening):
            return []
        self._first_seen = {unit_id: self._first_seen.get(unit_id, world.tick) for unit_id in world.combat_units}
        self._forget_routes(world, ctx)
        ctx.walked_routes = tuple(self.routes.values())
        return self._send(world, ctx) if self._walk is None else self._follow(self._walk, world, ctx)

    def _forget_routes(self, world: World, ctx: MatchContext) -> None:
        """Forget the routes of the fields that were skipped or are taken: no building grows toward them any more."""
        if not self.routes:
            return
        taken = placement.taken_fields(world, ctx.terrain, ctx.tuning.placement, ctx.asked_refinery_fields)
        gone = {*taken, *ctx.skipped_fields}
        self.routes = {field: route for field, route in self.routes.items() if field not in gone}

    def _follow(self, walk: _Walk, world: World, ctx: MatchContext) -> list[Intent]:
        """Note the walker's cell, and end the walk once it has arrived or its field is to be skipped (see
        `_Walk.verdict`), with the order that sends the walker back; a walker gone from its group ends the walk with
        no order (`_gone`)."""
        unit = world.combat_units.get(walk.unit_id)
        if unit is None or ctx.groups.group_of(walk.unit_id) != walk.group:
            self._gone(walk, world, ctx)
            return []
        tuning, position = ctx.tuning.placement, (unit.x, unit.y)
        walk.record(position, world.tick, tuning.walk_sample_cells)
        verdict = walk.verdict(position, world.tick, tuning)
        if verdict is None:
            return []
        if verdict == DONE:
            self.routes[walk.field.patch] = walk.finish(position)
            self._end(walk, DONE, world, ctx)
        else:
            _skip(walk.field.patch, walk.line, verdict, world, ctx)
            self._end(walk, FAILED, world, ctx, verdict)
        return [Intent(Priority.MOVE, {"cmd": "move", "units": [walk.unit_id], "to": list(walk.start)})]

    def _gone(self, walk: _Walk, world: World, ctx: MatchContext) -> None:
        """End a walk whose walker is gone from its group: as killed when a `unit_lost` event of this state names it,
        counted toward its field, which is skipped once `walker_deaths_to_skip` walkers were killed on their way to it;
        else as lost, counted toward nothing."""
        if not _is_reported_lost(walk.unit_id, world):
            self._end(walk, FAILED, world, ctx, WALKER_LOST)
            return
        self._end(walk, FAILED, world, ctx, WALKER_KILLED)
        patch = walk.field.patch
        self._killed[patch] += 1
        if self._killed[patch] >= ctx.tuning.placement.walker_deaths_to_skip:
            _skip(patch, walk.line, WALK_KILLED_REASON, world, ctx)

    def _end(self, walk: _Walk, phase: str, world: World, ctx: MatchContext, reason: str | None = None) -> None:
        """End the walk under way: the walker's mark taken off, and its event written."""
        self._walk = None
        ctx.groups.set_walking(walk.unit_id, False)
        _log_walk(phase, walk, world, reason)

    def _send(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order that sends a walker to the first line's far field without a route; none while the base is under
        threat, there is no such field or no rifleman may walk."""
        wanted = None if ctx.threat_active else self._wanted(world, ctx)
        if wanted is None:
            self._wanted_since = None
            return []
        line, field = wanted
        self._wanted_since = world.tick if self._wanted_since is None else self._wanted_since
        unit = self._walker(line, field, world, ctx)
        if unit is None:
            return []
        self._wanted_since = None
        ore, start = field.patch.nearest_cell(field.origin), (unit.x, unit.y)
        timeout = _timeout_ticks(unit.type, cells.distance(start, ore), world, ctx.tuning.placement)
        group = ctx.groups.group_of(unit.id)
        self._walk = _Walk(field, line, unit.id, unit.type, group, ore, [start], world.tick, timeout)
        ctx.groups.set_walking(unit.id, True)
        _log_walk(SENT, self._walk, world)
        return [Intent(Priority.SCOUT, {"cmd": "move", "units": [unit.id], "to": list(ore)})]

    def _wanted(self, world: World, ctx: MatchContext) -> tuple[str, FarField] | None:
        """The first line, the main base's first, whose far field has no route, was not skipped and has no enemy seen
        lately near its way (`_is_way_watched`), with that field. A far field there is a reason to skip
        (`_skip_reason`) is skipped on the way."""
        for line, field in self._far_field.lines():
            if field.patch in self.routes or field.patch in ctx.skipped_fields:
                continue
            reason = _skip_reason(field, world, ctx)
            if reason is not None:
                _skip(field.patch, line, reason, world, ctx)
            elif not _is_way_watched(field, world, ctx):
                return line, field
        return None

    def _walker(self, line: str, field: FarField, world: World, ctx: MatchContext) -> Unit | None:
        """The rifleman sent to walk a line, the one of the lowest id that may walk: for the second yard's line, one
        within `second_line_radius_cells` of that yard first; None without one."""
        free = sorted(
            (unit for unit in world.combat_units.values() if self._may_walk(unit, world, ctx)), key=lambda unit: unit.id
        )
        if line == SECOND_LINE:
            reach = ctx.tuning.placement.second_line_radius_cells
            free = [unit for unit in free if cells.is_within((unit.x, unit.y), field.origin, reach)] or free
        return next(iter(free), None)

    def _may_walk(self, unit: Unit, world: World, ctx: MatchContext) -> bool:
        """Whether a unit may walk a route: a rifleman of the guard or main first seen before this cycle, that stands
        idle or, during the opening, was first seen after a walk was wanted."""
        first_seen = self._first_seen[unit.id]
        is_free = (
            unit.type == WALKER_TYPE and ctx.groups.group_of(unit.id) in _WALKER_GROUPS and first_seen < world.tick
        )
        is_new = bool(ctx.opening_build.steps) and first_seen > self._wanted_since
        return is_free and (unit.idle or is_new)


def _timeout_ticks(unit_type: str, straight: float, world: World, tuning: PlacementTuning) -> int:
    """How many ticks a walker of a type has to walk a straight distance: `walk_timeout_seconds`, or more for a long
    way, `walk_detour_factor` times the distance over the walker's speed and TIMEOUT_SLACK_SECONDS."""
    facts = world.items.get(unit_type)
    speed = facts.speed if facts is not None and facts.speed else _UNKNOWN_SPEED_CELLS_PER_SECOND
    seconds = max(tuning.walk_timeout_seconds, tuning.walk_detour_factor * straight / speed + TIMEOUT_SLACK_SECONDS)
    return math.ceil(seconds * TICKS_PER_SECOND)


def _is_reported_lost(unit_id: int, world: World) -> bool:
    """Whether a `unit_lost` event among the state's new events names an own unit."""
    return any(event.get("kind") == UNIT_LOST_EVENT and event.get("id") == unit_id for event in world.events_new)


def _skip_reason(field: FarField, world: World, ctx: MatchContext) -> str | None:
    """Why a far field gets no walker and is skipped: a remembered enemy defense covers its way (`walk_defended`), or
    its center lies within `walker_enemy_refinery_cells` of the middle of a remembered enemy refinery
    (`walk_enemy_field`); None when it may be walked."""
    if _is_way_covered(field, world, ctx):
        return WALK_DEFENDED_REASON
    reach = ctx.tuning.placement.walker_enemy_refinery_cells
    refineries = (record for record in world.enemy_memory.values() if record.is_building and record.type == REFINERY)
    corners = (placement.footprint_corner(REFINERY, (record.x, record.y)) for record in refineries)
    middles = (cells.nearest_cell(*placement.box_at(REFINERY, corner).center) for corner in corners)
    return (
        WALK_ENEMY_FIELD_REASON if any(cells.is_within(field.patch.center, cell, reach) for cell in middles) else None
    )


def _is_way_watched(field: FarField, world: World, ctx: MatchContext) -> bool:
    """Whether an enemy unit that fights was seen within `ENEMY_NEAR_WAY_CELLS` of a cell of a far field's straight
    way in the last `walker_enemy_seen_seconds`, where it was last seen: a walker sent now would walk into it."""
    lately = ctx.tuning.placement.walker_enemy_seen_seconds * TICKS_PER_SECOND
    seen = [
        (record.x, record.y)
        for record in world.enemy_memory.values()
        if is_combat_unit(record) and world.tick - record.last_seen_tick <= lately
    ]
    return any(cells.is_within(spot, cell, ENEMY_NEAR_WAY_CELLS) for spot in seen for cell in _way(field))


def _way(field: FarField) -> list[Cell]:
    """The cells of the straight way from a far field's line start to its ore cell nearest that start."""
    return cells.line(field.origin, field.patch.nearest_cell(field.origin))


def _is_way_covered(field: FarField, world: World, ctx: MatchContext) -> bool:
    """Whether a remembered enemy defense covers a cell of a far field's straight way (`_way`)."""
    defenses, tuning = world.known_enemy_defenses, ctx.tuning.coverage
    return any(coverage.is_covered(cell, defenses, world.items, tuning) for cell in _way(field))


def _skip(patch: OrePatch, line: str, reason: str, world: World, ctx: MatchContext) -> None:
    """Put a field among those placement skipped, written as a `creep_placement` event of the reason and the line,
    `main` or `second`; no building. A field skipped already, for whatever reason, is left as it is."""
    if patch in ctx.skipped_fields:
        return
    ctx.skipped_fields.append(patch)
    records.log(
        CREEP_PLACEMENT_EVENT,
        {
            "tick": world.tick,
            "building": None,
            "cell": None,
            "field_center": list(patch.center),
            "target_patch_center": list(patch.center),
            "reason": reason,
            "yard": line,
        },
    )


def _log_walk(phase: str, walk: _Walk, world: World, reason: str | None = None) -> None:
    """Write one `route_walk` event of a walk's phase, with the reason a walk failed for."""
    data: dict[str, object] = {
        "tick": world.tick,
        "phase": phase,
        "field_center": list(walk.field.patch.center),
        "yard": walk.line,
        "walker_type": walk.unit_type,
        "path_length_cells": round(walk.length, _DISTANCE_DECIMALS),
        "straight_distance_cells": round(walk.straight, _DISTANCE_DECIMALS),
        "point_count": len(walk.points),
    }
    if reason is not None:
        data["reason"] = reason
    records.log(ROUTE_WALK_EVENT, data)
