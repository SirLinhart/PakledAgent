"""Steering a designated scout: the ground it keeps out of, and the way it takes around it.

A scout of the scout slots (see `scout_slots`) is worth what it sees, and it
sees nothing once it is dead, and a scout that drives straight at the enemy
is lost to the first defender on its way. So a scout never takes a way that
crosses dangerous ground, and the ground is judged afresh in every cycle
from what is known now.

A cell is dangerous when a remembered enemy defense covers it (its range and
the margin of `coverage`), or when it lies within the weapon range of an
enemy unit that fights and is in sight, and scout_danger_margin_cells more,
of where that unit stands. A unit whose range the item table does not give is
taken to reach as far as an unknown defense.

The way to a destination is the straight line, when no part of it comes
within a dangerous cell's reach and it does not pass within
scout_center_clear_cells of the middle of the map, where the armies meet. A
scout standing within that circle may take a way out of it, but none that
goes deeper. Otherwise the way goes by a side waypoint: one of the cells
scout_side_cells to either side of the middle of the straight line, or twice
as far, as `raid_routes` puts them, whichever gives the shortest way whose
two legs are both clear. When none does, there is no safe way to the
destination.

With `waypoints` of [routing] on, a destination whose straight line crosses a
danger is gone to first by the way the graph of waypoints gives there (see
`waypoints`), when each of its legs keeps clear as a leg of the way must: off
every danger, by the flee distance when the way must keep it, and off the
map's middle while the way must keep from it. When the graph gives no such
way, the way is found as above. Each way through the graph a scout's course
takes to a destination is written as a `route` event.

A leg is judged as a straight segment against each danger's circle, one cell
wider than its reach, so that the cells a unit walks along the line, which
stray from it by half a cell at the most, are clear as well. A way that also
keeps scout_flee_cells from every danger is taken before one that does not,
so that a scout does not walk its way into fleeing.

In every cycle each scout is steered:

- A scout with a dangerous cell within scout_flee_cells of it flees: it is
  sent scout_flee_cells directly away from the nearest danger, again once it
  is there while the danger is still that near, and anew when another danger
  is the nearest. The destination it had is given up: the way there ran into
  what it fled.
- Otherwise it keeps its destination and its way while the rest of the way
  crosses no danger. A way that now does is drawn again to the same
  destination, and when there is none the destination is given up. A scout
  takes a new destination once it is within scout_reached_cells of its own,
  and once it has stood idle stuck_ticks short of the next cell of its way.
- The new destination is the first candidate (see `scout_targets`) with a
  safe way to it, the places the other scouts are heading for left out.
  After a flight the way must keep scout_flee_cells from every danger, so
  that the scout does not turn straight back into what it fled. When no
  candidate has such a way, the scout waits at the staging point and looks
  again in the next cycle.

Each change of destination says why: `new` for a scout without one, then
`reached`, `stuck`, `unsafe` or `fled`.

With `scout_wide_berth` on, wide_danger_margin_cells and wide_flee_cells
stand for scout_danger_margin_cells and scout_flee_cells, and the ground
within hurt_area_cells of where a scout was hurt lately is dangerous as well
(see `scout_berth`): a destination there has no safe way and is replaced at
once by the next candidate.

With `scout_cautious` on as well as `scout_slots`, the ground within
scout_enemy_base_cells of every remembered enemy building that is not a wall
is dangerous as well, as a defense's cover is: a scout that drives into an
enemy base is lost there whether or not a defense of it is known. The ways
keep out of it, and a scout near it flees. A flight whose cell lies within
scout_flee_cells of another danger goes to the staging point instead: between
two dangers, a flight straight away from one lands the scout by the other,
whose flight sends it back.

With `scout_missions` on, a scout's destination is its mission's cell and its
way the mission's (see `scout_missions`), set in every cycle before it is
steered. It flees as above; a scout at its mission's cell stands there to
watch it, and one with no mission waits at the staging point.

A scout the model commander steers (see `command_scout`) is steered to the
cell of the action it was given the same way, flight included. One it holds
in place is left alone while the hold lasts, whether or not it stands idle,
but for a flight, which still applies.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Iterable

from ucla_agent.games.redalert import cells, coverage, raid_routes
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.scout_berth import danger_margin_cells, flee_cells
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.scout_route import distance_to_segment
from ucla_agent.games.redalert.scout_stance import is_cautious
from ucla_agent.games.redalert.scout_targets import candidates, map_center
from ucla_agent.games.redalert.sight import SightGrid
from ucla_agent.games.redalert.waypoints import SCOUT, WaypointRoute, log_route
from ucla_agent.games.redalert.world import Cell, Unit, World

# A leg is kept this much farther from a danger than its reach: the cells along a line stray from it.
LINE_TOLERANCE_CELLS = 1
# The enemy buildings that are walls: they hold nothing that shoots and stand far out from the base they guard.
WALL_TYPES = frozenset({"sbag", "fenc", "brik", "barb", "wood", "cycl"})
# The side waypoints are tried this many times scout_side_cells off the line, the nearer first.
_SIDE_OFFSETS = (1, 2)
# Who chose a destination: the script, a model (see `scout_choice`), the script's missions (see `scout_missions`), or
# the model commander (see `command_scout`).
SCRIPT = "script"
MODEL = "model"
MISSION = "mission"
COMMANDER = "commander"
# Why a scout's destination changed.
NEW = "new"
REACHED = "reached"
STUCK = "stuck"
UNSAFE = "unsafe"
FLED = "fled"
# How urgent a scout's move is, the most urgent first: a flight, a move not yet sent (a new destination or way), the
# same move again for a scout that stands idle, the same move again.
FLEE_URGENCY = 0
CHANGE_URGENCY = 1
IDLE_URGENCY = 2
KEEP_URGENCY = 3


@dataclass
class Course:
    """Where one scout is going and by which way."""

    destination: Cell | None = None
    # Who chose the destination: SCRIPT or MODEL.
    source: str = SCRIPT
    # The cells the scout goes to in turn, the destination last; none without a destination.
    legs: list[Cell] = field(default_factory=list)
    # The tick the scout was sent toward the first of the legs.
    leg_tick: int = 0
    # Where the scout flees to, and the centre of the danger it flees from; None while it does not.
    flee_to: Cell | None = None
    fled_from: Cell | None = None
    # Why the next destination is taken, once one is: what became of the last.
    reason: str = NEW
    # The cell of the last move sent to the scout; None before the first.
    sent: Cell | None = None
    # The tick the destination was chosen.
    chosen_tick: int = 0
    # The destination of the last way through the graph of waypoints written for the course; None for none.
    routed_to: Cell | None = None

    def go(self, destination: Cell, legs: list[Cell], source: str, tick: int) -> None:
        """Head for a destination by a way; a destination it was not heading for is chosen at this tick."""
        if destination != self.destination:
            self.chosen_tick = tick
        self.destination, self.legs, self.source, self.leg_tick = destination, legs, source, tick

    def give_up(self, reason: str) -> None:
        """Drop the destination; the next is taken for the reason given."""
        self.destination, self.legs, self.reason, self.routed_to = None, [], reason, None


@dataclass(frozen=True)
class Move:
    """Where a scout is sent in a cycle, how urgent that is, and the reason of the cycle's new destination, if any."""

    cell: Cell
    urgency: int
    change: str | None = None
    # The enemy the scout is ordered to attack in place of a move, at its cell: one of the scout slots' own attacks
    # (see `scout_attacks`); None for a move.
    target: int | None = None


class Dangers:
    """The ground a scout keeps out of in one cycle: a circle around each remembered enemy defense, each enemy unit
    in sight and each of the `hurt` areas given (see `scout_berth`), and with `scout_cautious` on each remembered
    enemy building but a wall. See the module's note."""

    def __init__(self, world: World, ctx: MatchContext, hurt: Iterable[Cell] = ()) -> None:
        tuning = ctx.tuning
        margin = danger_margin_cells(tuning.scouting)
        # The cells where scouts were hurt lately, whose ground no scout is sent to either.
        self.hurt = tuple(hurt)
        # The ways through the graph of waypoints given in this cycle, by their legs; shared by the copies `leaving`
        # makes.
        self.waypoint_routes: dict[tuple[Cell, ...], WaypointRoute] = {}
        # (centre, reach) of every danger.
        self._zones: list[tuple[Cell, float]] = [
            ((defense.x, defense.y), coverage.reach(defense.type, world.items, tuning.coverage))
            for defense in world.known_enemy_defenses
        ]
        self._zones += [
            ((record.x, record.y), _weapon_range(record.type, world, ctx) + margin)
            for record in world.enemy_memory.values()
            if record.visible and is_combat_unit(record)
        ]
        self._zones += [(cell, tuning.scouting.hurt_area_cells) for cell in self.hurt]
        if is_cautious(tuning.scouting):
            self._zones += [(cell, tuning.scouting.scout_enemy_base_cells) for cell in enemy_base_cells(world)]

    def crosses(self, start: Cell, end: Cell, margin: float = 0) -> bool:
        """Whether the straight leg between two cells comes within a danger's reach, a cell more and `margin`."""
        keep_off = LINE_TOLERANCE_CELLS + margin
        return any(distance_to_segment(centre, start, end) <= reach + keep_off for centre, reach in self._zones)

    def nearest(self, cell: Cell) -> tuple[Cell, float] | None:
        """The centre of the danger whose reach ends nearest a cell, and how far from the cell it ends (0 for a cell
        within it); None when there is no danger."""
        edges = ((centre, max(0.0, cells.distance(cell, centre) - reach)) for centre, reach in self._zones)
        return min(edges, key=lambda edge: edge[1], default=None)

    def is_near_other(self, cell: Cell, danger: Cell, distance: float) -> bool:
        """Whether the reach of a danger other than the one centred on `danger` ends within `distance` of a cell."""
        return any(
            centre != danger and cells.distance(cell, centre) - reach <= distance for centre, reach in self._zones
        )

    def leaving(self, cell: Cell) -> Dangers:
        """These dangers but those whose reach, and a cell more, takes in `cell`: what a way out from a scout standing
        there still has to keep clear of, since every way out of the ground it stands on crosses that ground."""
        ahead = copy.copy(self)
        ahead._zones = [
            (centre, reach)
            for centre, reach in self._zones
            if not cells.is_within(cell, centre, reach + LINE_TOLERANCE_CELLS)
        ]
        return ahead


def enemy_base_cells(world: World) -> list[Cell]:
    """The cells of the remembered enemy buildings that are not walls: the ground of the enemy's bases."""
    return [
        (record.x, record.y)
        for record in world.enemy_memory.values()
        if record.is_building and record.type not in WALL_TYPES
    ]


def route(
    start: Cell,
    destination: Cell,
    dangers: Dangers,
    world: World,
    ctx: MatchContext,
    *,
    must_keep_off: bool = False,
    keeps_from_center: bool = True,
) -> list[Cell] | None:
    """The cells a scout goes to in turn on its way from `start` to a destination, the destination last: a way that
    keeps scout_flee_cells from every danger before one that only stays out of them, which `must_keep_off` rules out; of
    each, the straight way, else the shortest by a side waypoint whose legs are both clear. None when no way is. Unless
    `keeps_from_center` is false, as for a scout mission (see `scout_missions`) to the lane between the bases, no leg
    passes the map's middle."""
    keep_off = flee_cells(ctx.tuning.scouting)
    margins = (keep_off,) if must_keep_off else (keep_off, 0)
    by_waypoints = _waypoint_way(start, destination, _Clearance(margins[-1], keeps_from_center), dangers, world, ctx)
    if by_waypoints is not None:
        return by_waypoints
    for margin in margins:
        way = _way(start, destination, _Clearance(margin, keeps_from_center), dangers, world, ctx)
        if way is not None:
            return way
    return None


@dataclass(frozen=True)
class _Clearance:
    """What a leg of a way has to keep clear of: every danger by `margin` more, and the map's middle while
    `keeps_from_center`."""

    margin: float
    keeps_from_center: bool = True


def _waypoint_way(
    start: Cell, destination: Cell, clearance: _Clearance, dangers: Dangers, world: World, ctx: MatchContext
) -> list[Cell] | None:
    """With `waypoints` of [routing] on and the straight line to the destination crossing a danger, the cells of the
    way the graph of waypoints gives there when each of its legs keeps the clearance, kept among the cycle's ways in
    `dangers`; None otherwise."""
    if not ctx.tuning.routing.waypoints or not dangers.crosses(start, destination):
        return None
    found = ctx.waypoints.route(start, destination)
    if found is None:
        return None
    stops = (start, *found.legs)
    if not all(_is_clear(first, second, clearance, dangers, world, ctx) for first, second in zip(stops, stops[1:])):
        return None
    dangers.waypoint_routes[found.legs] = found
    return list(found.legs)


def _way(
    start: Cell, destination: Cell, clearance: _Clearance, dangers: Dangers, world: World, ctx: MatchContext
) -> list[Cell] | None:
    """The straight way, else the shortest way by a side waypoint, whose legs keep the clearance; None when neither
    does."""
    if _is_clear(start, destination, clearance, dangers, world, ctx):
        return [destination]
    side_cells = ctx.tuning.scouting.scout_side_cells
    sides = [
        side
        for offset in _SIDE_OFFSETS
        for side in raid_routes.side_waypoints(start, destination, world.map_bounds, offset * side_cells)
    ]
    clear = [
        side
        for side in sides
        if _is_clear(start, side, clearance, dangers, world, ctx)
        and _is_clear(side, destination, clearance, dangers, world, ctx)
    ]
    shortest = min(
        clear, key=lambda side: cells.distance(start, side) + cells.distance(side, destination), default=None
    )
    return None if shortest is None else [shortest, destination]


def _is_clear(start: Cell, end: Cell, clearance: _Clearance, dangers: Dangers, world: World, ctx: MatchContext) -> bool:
    """Whether a scout may take the straight leg between two cells: it keeps the clearance's margin off every danger
    and, while the clearance says so, does not pass the map's middle."""
    if dangers.crosses(start, end, clearance.margin):
        return False
    return not clearance.keeps_from_center or not _passes_center(start, end, world, ctx)


def _passes_center(start: Cell, end: Cell, world: World, ctx: MatchContext) -> bool:
    """Whether a leg passes within scout_center_clear_cells of the map's middle, closer to it than it starts: a scout
    within the circle may leave it, but not go deeper."""
    center = map_center(world)
    closest = distance_to_segment(center, start, end)
    return closest <= ctx.tuning.scouting.scout_center_clear_cells and closest < cells.distance(start, center)


def _weapon_range(unit_type: str, world: World, ctx: MatchContext) -> float:
    """How far an enemy unit of a type shoots, in cells; as far as an unknown defense when the item table does not
    say."""
    facts = world.items.get(unit_type)
    if facts is None or facts.range is None:
        return ctx.tuning.coverage.unknown_range_cells
    return facts.range


def steer(
    course: Course,
    scout: Unit,
    avoid: Iterable[Cell],
    sight: SightGrid,
    dangers: Dangers,
    world: World,
    ctx: MatchContext,
) -> Move | None:
    """Where to send a scout in this cycle, its course brought up to date: away from a danger near it, else on its way
    to its destination, a new one when it needs one (see the module's note). None while it has no destination and
    waits at the staging point already."""
    position = (scout.x, scout.y)
    danger = fled_from(position, dangers, ctx)
    if danger is not None:
        return _flee(course, position, danger, dangers, world, ctx)
    _keep_or_drop(course, scout, dangers, world, ctx)
    change = None
    if course.destination is None:
        change = _choose(course, position, avoid, sight, dangers, world, ctx)
        if course.destination is None:
            return _wait(course, scout, world, ctx)
    _note_route(course, dangers, world)
    _advance(course, position, world, ctx)
    return Move(course.legs[0], _urgency(course.legs[0], course, scout), change)


def steer_to_mission(course: Course, scout: Unit, dangers: Dangers, world: World, ctx: MatchContext) -> Move | None:
    """Where to send a scout on a mission in this cycle, its course's destination and way being the mission's: away
    from a danger near it, else on its way to the mission's cell; None while it stands at that cell, watching it, or
    has no mission and waits at the staging point already. A scout the model commander has sent somewhere is steered
    the same way, to the cell it was sent to."""
    position = (scout.x, scout.y)
    danger = fled_from(position, dangers, ctx)
    if danger is not None:
        return _flee(course, position, danger, dangers, world, ctx)
    course.flee_to = course.fled_from = None
    if course.destination is None:
        return _wait(course, scout, world, ctx)
    if cells.is_within(position, course.destination, ctx.tuning.scouting.scout_reached_cells):
        return None
    _note_route(course, dangers, world)
    _advance(course, position, world, ctx)
    return Move(course.legs[0], _urgency(course.legs[0], course, scout))


def steer_held(course: Course, scout: Unit, dangers: Dangers, world: World, ctx: MatchContext) -> Move | None:
    """Where to send a scout the model commander holds in place: away from a danger near it, as any scout; else
    nowhere, not even when it stands idle, for as long as the hold lasts."""
    position = (scout.x, scout.y)
    danger = fled_from(position, dangers, ctx)
    if danger is not None:
        return _flee(course, position, danger, dangers, world, ctx)
    course.flee_to = course.fled_from = None
    return None


def fled_from(position: Cell, dangers: Dangers, ctx: MatchContext) -> Cell | None:
    """The centre of the danger a scout standing at `position` has to flee: the nearest, when a dangerous cell lies
    within scout_flee_cells of it; None when none does."""
    nearest = dangers.nearest(position)
    return nearest[0] if nearest is not None and nearest[1] <= flee_cells(ctx.tuning.scouting) else None


def away_from(position: Cell, danger: Cell, world: World, ctx: MatchContext) -> Cell:
    """The cell scout_flee_cells straight away from a danger's centre, inside the map; toward the base when the scout
    stands on that centre."""
    distance = flee_cells(ctx.tuning.scouting)
    away = cells.toward(position, danger, -distance)
    if away == position:
        away = cells.toward(position, world.base_center, distance)
    return cells.clamped(away, world.map_bounds)


def _urgency(cell: Cell, course: Course, scout: Unit) -> int:
    """How urgent a move to a cell is: one not yet sent first, then one sent before to a scout that now stands idle,
    then one that only says again what the scout was told."""
    if cell != course.sent:
        return CHANGE_URGENCY
    return IDLE_URGENCY if scout.idle else KEEP_URGENCY


def _flee(course: Course, position: Cell, danger: Cell, dangers: Dangers, world: World, ctx: MatchContext) -> Move:
    """Send a scout scout_flee_cells directly away from a danger, giving up the destination it had: anew when it has
    reached the cell it fled to, or another danger is the nearest."""
    tuning = ctx.tuning.scouting
    if course.destination is not None:
        course.give_up(FLED)
    is_there = course.flee_to is not None and cells.is_within(position, course.flee_to, tuning.scout_reached_cells)
    if course.flee_to is None or is_there or course.fled_from != danger:
        course.flee_to, course.fled_from = _flee_cell(position, danger, dangers, world, ctx), danger
    return Move(course.flee_to, FLEE_URGENCY)


def _flee_cell(position: Cell, danger: Cell, dangers: Dangers, world: World, ctx: MatchContext) -> Cell:
    """Where a scout flees a danger to: scout_flee_cells straight away from it (see `away_from`); with
    `scout_cautious` on, the staging point instead when that cell lies within the flee distance of another danger,
    since a flight there only trades the one danger for the other."""
    away = away_from(position, danger, world, ctx)
    tuning = ctx.tuning.scouting
    if not is_cautious(tuning) or not dangers.is_near_other(away, danger, flee_cells(tuning)):
        return away
    return cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells)


def _keep_or_drop(course: Course, scout: Unit, dangers: Dangers, world: World, ctx: MatchContext) -> None:
    """Keep the scout's destination and way, drawing the way again when the rest of it crosses a danger now; give the
    destination up when it is reached, the scout is stuck short of it, or no safe way is left. A flight is over."""
    course.flee_to = course.fled_from = None
    if course.destination is None:
        return
    tuning, position = ctx.tuning.scouting, (scout.x, scout.y)
    is_stuck = scout.idle and world.tick - course.leg_tick >= tuning.stuck_ticks
    if cells.is_within(position, course.destination, tuning.scout_reached_cells):
        course.give_up(REACHED)
    elif is_stuck and not cells.is_within(position, course.legs[0], tuning.scout_reached_cells):
        course.give_up(STUCK)
    elif _crosses_on(position, course.legs, dangers):
        way = route(position, course.destination, dangers, world, ctx)
        if way is None:
            course.give_up(UNSAFE)
        else:
            course.go(course.destination, way, course.source, world.tick)


def _crosses_on(position: Cell, legs: list[Cell], dangers: Dangers) -> bool:
    """Whether the rest of a way, from where the scout stands through its legs, crosses a danger."""
    stops = [position, *legs]
    return any(dangers.crosses(start, end) for start, end in zip(stops, stops[1:]))


def _choose(
    course: Course,
    position: Cell,
    avoid: Iterable[Cell],
    sight: SightGrid,
    dangers: Dangers,
    world: World,
    ctx: MatchContext,
) -> str | None:
    """Head for the first candidate with a safe way to it, the cells to avoid left out: after a flight, a way that
    keeps the flee distance. The reason of the change, or None when no candidate has such a way."""
    reached, after_flight = ctx.tuning.scouting.scout_reached_cells, course.reason == FLED
    for candidate in candidates(world, ctx, sight, avoid, dangers.hurt):
        if cells.is_within(position, candidate.cell, reached):
            continue
        way = route(position, candidate.cell, dangers, world, ctx, must_keep_off=after_flight)
        if way is not None:
            course.go(candidate.cell, way, SCRIPT, world.tick)
            return course.reason
    return None


def _note_route(course: Course, dangers: Dangers, world: World) -> None:
    """Write a `route` event once a course follows a way the graph of waypoints gave in this cycle to a destination it
    was not following such a way to."""
    found = dangers.waypoint_routes.get(tuple(course.legs))
    if found is None or course.routed_to == course.destination:
        return
    course.routed_to = course.destination
    log_route(SCOUT, found, world.tick)


def _advance(course: Course, position: Cell, world: World, ctx: MatchContext) -> None:
    """Go on to the next cell of the way once the scout is at one short of its destination: a side waypoint, or a
    cell of a way through the graph of waypoints."""
    if len(course.legs) > 1 and cells.is_within(position, course.legs[0], ctx.tuning.scouting.scout_reached_cells):
        course.legs.pop(0)
        course.leg_tick = world.tick


def _wait(course: Course, scout: Unit, world: World, ctx: MatchContext) -> Move | None:
    """Send a scout with no destination to wait at the staging point; None when it is there."""
    staging = cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells)
    if cells.is_within((scout.x, scout.y), staging, ctx.tuning.scouting.scout_reached_cells):
        return None
    return Move(staging, _urgency(staging, course, scout))
