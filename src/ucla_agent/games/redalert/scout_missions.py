"""Scripted scout missions (`[scouting] scout_missions`): what the scout slots' scouts go out to learn.

Without the switch the slots' scouts go from place to place, the one gone
unseen longest first (see `scout_targets`), or where a model picks among those
places. The places are points; nothing says what a scout learns there, and a
model asked to choose among them chose no better than the script. With the
switch on, each scout takes a mission instead: a place to watch for a reason
the script knows. There are five kinds:

- an enemy expansion watch: each ore field more than 20 cells from every own
  construction yard and not within 15 cells of the enemy's start, never seen
  or unseen for over a minute, where the enemy would expand (priority 3);
- an enemy home field watch: each ore field within 15 cells of the enemy's
  start, where the enemy mines, while neither the field's center nor its
  standoff cell has been in sight for HOME_FIELD_UNSEEN_SECONDS (or ever).
  The scout watches from the standoff cell, since the field itself lies in the
  enemy's base: the first cell from the field's center out toward our base, a
  cell at a time, that lies farther than scout_enemy_base_cells, a cell and
  the flee distance from every remembered enemy building but a wall, and
  farther than a cell and the flee distance from every danger (see
  `scout_steering`). A scout seldom sees the field's center from there, so
  the standoff cell in sight counts as the field seen. With no such cell
  within HOME_FIELD_STANDOFF_MAX_CELLS of the field, the field is not watched
  (priority 2: below the expansion watch's 3, since a score grows with its
  priority and a new enemy base is the greater news);
- the enemy main's edge: the cell scout_view_cells from the enemy's start on
  the side that faces our base, side_offset_cells to the side of the line
  between the two, from where a scout sees into the base without standing in
  front of it (priority 2; 4 while no enemy barracks or war factory is
  remembered, since what the enemy produces is the first thing to learn).
  With `scout_cautious` on, whose scouts keep off the ground of the enemy's
  bases (see `scout_steering`), the cell is moved out along the line toward
  our base, a cell at a time, to the first that lies farther than
  scout_enemy_base_cells, a cell and the flee distance from every remembered
  enemy building but a wall: a scout can stand and watch there without
  fleeing. It stays where it was when no such cell lies short of our base;
- our expansion approach: halfway between our main yard and the site of our
  next yard, when there is one (the further MCV's site of `mcv_repeat`, else
  the field `mcv_reserve_field` keeps for the MCV), where a raid on the new
  yard would come by (priority 2);
- the attack lane: halfway between the centre of our base and the enemy's
  start, where an army coming at us passes (priority 1; 3 when no enemy unit
  that fights has been in sight for a minute and a half).

Our main yard is the construction yard nearest the centre of the base, or the
centre itself when no yard stands. The enemy's start is the spawn of the map
that is not ours, the one farther from our base: on a map of two spawns it is
known from the first state, so no mission waits to find it. On a map of more
it is the world's enemy base. A mission's cell lies inside the map.

What a mission is worth to a scout is its score: priority x gap / (travel +
mission_observe_seconds + 1). The gap is how little is known of the place:
1.0 for a place never seen, else the seconds since it was last in our units'
sight over 120, at most 1.0. The travel is the length of the scout's way
there, over its speed, in seconds. A scout is offered only the missions it has
a safe way to (see `scout_steering`: around every danger, though not around
the map's middle, where the lane mission lies) and that are not blacklisted.
With `scout_cautious` on the way must keep the flee distance from every
danger as well (`route` with must_keep_off): a way that only stays out of a
danger can pass near enough to it to make the scout flee on the way.

The scouts are assigned their missions together, not one after the other:
every combination of one mission (or none) for each scout is tried, no
mission given twice, and the one that keeps the most scouts busy and is worth
the most in total wins. Two missions within 15 cells of each other are
counted at half their total, since a scout at one sees much of what the other
would: a pair apart that is worth at least half a pair close together is
taken over it, and two scouts are sent near each other only when no such
pair exists.

A forced mission comes before all that: the combination that gives the most
forced missions wins, whatever the scores. The attack lane and the enemy
main's edge are forced in a cycle of a refresh, when the scouts look for an
enemy army nobody has seen while main is big (see `scout_mission_planner`).
"""

from __future__ import annotations

import dataclasses
import itertools
import math
from dataclasses import dataclass
from typing import Collection, Iterable, Sequence

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.family import BARRACKS_TYPES, CONSTRUCTION_YARD, WAR_FACTORY
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.own_fields import is_own
from ucla_agent.games.redalert.scout_berth import flee_cells
from ucla_agent.games.redalert.scout_stance import is_cautious
from ucla_agent.games.redalert.scout_steering import LINE_TOLERANCE_CELLS, Dangers, enemy_base_cells, route
from ucla_agent.games.redalert.sight import SightGrid
from ucla_agent.games.redalert.tuning import ScoutingTuning
from ucla_agent.games.redalert.world import Cell, Unit, World

# The kinds of mission.
EXPANSION_WATCH = "expansion_watch"
ENEMY_HOME_FIELD = "enemy_home_field"
MAIN_EDGE = "main_edge"
EXPANSION_APPROACH = "expansion_approach"
ATTACK_LANE = "attack_lane"
# The kinds a refresh forces: where an enemy army coming at us passes, and where it stands at home.
REFRESH_KINDS = frozenset({ATTACK_LANE, MAIN_EDGE})
# An ore field is watched for an enemy expansion when it lies more than this from every own yard ...
WATCH_FIELD_FROM_YARD_CELLS = 20
# ... not within this of the enemy's start, where the enemy's own fields are ...
WATCH_FIELD_FROM_ENEMY_CELLS = 15
# ... and has gone unseen longer than this, or was never seen.
WATCH_FIELD_UNSEEN_SECONDS = 60
# A field within WATCH_FIELD_FROM_ENEMY_CELLS of the enemy's start is watched once it has gone unseen longer than this,
# from a standoff cell at most HOME_FIELD_STANDOFF_MAX_CELLS from its center.
HOME_FIELD_UNSEEN_SECONDS = 120
HOME_FIELD_STANDOFF_MAX_CELLS = 20
# The priority of each kind, and the higher one of the main's edge and of the lane while what they watch for is
# unknown or has not been seen for LANE_UNSEEN_ARMY_SECONDS.
EXPANSION_WATCH_PRIORITY = 3
# Below the expansion watch: the higher number ranks first in a mission's score.
ENEMY_HOME_FIELD_PRIORITY = 2
MAIN_EDGE_PRIORITY = 2
MAIN_EDGE_UNKNOWN_PRODUCTION_PRIORITY = 4
EXPANSION_APPROACH_PRIORITY = 2
ATTACK_LANE_PRIORITY = 1
ATTACK_LANE_UNSEEN_ARMY_PRIORITY = 3
LANE_UNSEEN_ARMY_SECONDS = 90
# The enemy buildings that say what the enemy produces.
PRODUCTION_BUILDINGS = BARRACKS_TYPES | {WAR_FACTORY}
# A map of this many spawns has one that is not ours: the enemy's start.
TWO_PLAYER_SPAWNS = 2
# A place unseen this long is as unknown as one never seen.
GAP_FULL_SECONDS = 120
NEVER_SEEN_GAP = 1.0
# The speed a scout is taken to have when the item table gives none, in cells a second: no faster than a rifleman.
UNKNOWN_SPEED_CELLS_PER_SECOND = 1.0
# Two missions this close overlap, and a combination with an overlap counts for this share of its total.
OVERLAP_CELLS = 15
OVERLAP_SHARE = 0.5
# Up to this many scouts are assigned over all their options; more are each tried with their best few only.
FULL_SEARCH_SCOUTS = 2
OPTIONS_PER_SCOUT_BEYOND_FULL_SEARCH = 6
_HALF = 0.5


@dataclass(frozen=True)
class Mission:
    """A place a scout is sent to watch, and why."""

    kind: str
    cell: Cell
    priority: int
    # The last tick the cell's square was in our units' sight; None while it never was.
    seen_tick: int | None
    # Given before any mission not forced, whatever the scores (see `assign`).
    forced: bool = False

    @property
    def key(self) -> tuple[str, Cell]:
        """What tells one mission from another: its kind and its cell."""
        return self.kind, self.cell


@dataclass(frozen=True)
class Option:
    """A mission one scout may take, by a safe way, and what it is worth to that scout."""

    mission: Mission
    # The cells the scout goes to in turn, the mission's cell last.
    legs: tuple[Cell, ...]
    score: float
    # What the option counts for when the scouts are assigned: its score.
    weight: float


def enemy_start(world: World) -> Cell | None:
    """The enemy's start: of the map's two spawns, the one farther from our base; the world's enemy base on a map of
    another number of spawns."""
    if len(world.spawns) != TWO_PLAYER_SPAWNS or world.base_center is None:
        return world.enemy_base
    return max(world.spawns, key=lambda spawn: cells.distance(spawn, world.base_center))


def missions(world: World, ctx: MatchContext, sight: SightGrid, army_seen_tick: int) -> list[Mission]:
    """The missions of the cycle, of every kind (see the module's note); `army_seen_tick` is the last tick an enemy
    unit that fights was in sight, or the first of the match when none was. Asked once the map, the base and the
    enemy's start are known."""
    start = enemy_start(world)
    found = [
        *_expansion_watches(start, world, ctx, sight),
        *_home_field_watches(start, world, ctx, sight),
        _main_edge(start, world, ctx, sight),
    ]
    approach = _expansion_approach(world, ctx, sight)
    if approach is not None:
        found.append(approach)
    return [*found, _attack_lane(start, world, sight, army_seen_tick)]


def forced(found: Iterable[Mission]) -> list[Mission]:
    """The missions of a refresh: those of the kinds REFRESH_KINDS forced, the others as they are."""
    return [
        dataclasses.replace(mission, forced=True) if mission.kind in REFRESH_KINDS else mission for mission in found
    ]


def _expansion_watches(start: Cell, world: World, ctx: MatchContext, sight: SightGrid) -> list[Mission]:
    """A watch on each ore field far from our yards and from the enemy's start that has gone unseen a while, but our
    own fields (see `own_fields`)."""
    yards = _yards(world) or [world.base_center]
    watches = []
    for patch in ctx.terrain.ore_patches:
        cell = cells.clamped(patch.center, world.map_bounds)
        seen = sight.last_seen(cell)
        is_ours = is_own(patch, world, ctx.tuning.scouting.own_field_cells)
        if _is_far_from_both(cell, yards, start) and not is_ours and _is_stale(seen, world.tick):
            watches.append(Mission(EXPANSION_WATCH, cell, EXPANSION_WATCH_PRIORITY, seen))
    return watches


def _is_far_from_both(cell: Cell, yards: list[Cell], start: Cell) -> bool:
    """Whether a field lies more than WATCH_FIELD_FROM_YARD_CELLS from every own yard and WATCH_FIELD_FROM_ENEMY_CELLS
    from the enemy's start."""
    is_ours = any(cells.is_within(cell, yard, WATCH_FIELD_FROM_YARD_CELLS) for yard in yards)
    return not is_ours and not cells.is_within(cell, start, WATCH_FIELD_FROM_ENEMY_CELLS)


def _is_stale(seen_tick: int | None, tick: int, unseen_seconds: int = WATCH_FIELD_UNSEEN_SECONDS) -> bool:
    """Whether a place last seen at `seen_tick` has never been seen or not for over `unseen_seconds`."""
    return seen_tick is None or tick - seen_tick > unseen_seconds * TICKS_PER_SECOND


def _home_field_watches(start: Cell, world: World, ctx: MatchContext, sight: SightGrid) -> list[Mission]:
    """A watch from its standoff cell on each ore field within WATCH_FIELD_FROM_ENEMY_CELLS of the enemy's start
    that has gone unseen HOME_FIELD_UNSEEN_SECONDS, the field's center and its standoff cell alike; none for a field
    with no standoff cell."""
    dangers = Dangers(world, ctx)
    watches = []
    for patch in ctx.terrain.ore_patches:
        center = cells.clamped(patch.center, world.map_bounds)
        if not cells.is_within(center, start, WATCH_FIELD_FROM_ENEMY_CELLS):
            continue
        cell = _standoff(center, dangers, world, ctx.tuning.scouting)
        if cell is None:
            continue
        seen = max(
            (tick for tick in (sight.last_seen(center), sight.last_seen(cell)) if tick is not None), default=None
        )
        if _is_stale(seen, world.tick, HOME_FIELD_UNSEEN_SECONDS):
            watches.append(Mission(ENEMY_HOME_FIELD, cell, ENEMY_HOME_FIELD_PRIORITY, seen))
    return watches


def _standoff(field: Cell, dangers: Dangers, world: World, tuning: ScoutingTuning) -> Cell | None:
    """The first cell from a field's center out toward our base, a cell at a time, that lies farther than
    scout_enemy_base_cells, a cell and the flee distance from every remembered enemy building but a wall and farther
    than a cell and the flee distance from every danger; None when none within HOME_FIELD_STANDOFF_MAX_CELLS of the
    field does."""
    keep_off = flee_cells(tuning)
    buildings = enemy_base_cells(world)
    keep = tuning.scout_enemy_base_cells + LINE_TOLERANCE_CELLS + keep_off
    for along_cells in range(HOME_FIELD_STANDOFF_MAX_CELLS + 1):
        cell = cells.clamped(cells.toward(field, world.base_center, along_cells), world.map_bounds)
        if not cells.is_within(cell, field, HOME_FIELD_STANDOFF_MAX_CELLS):
            break
        is_off_ground = not any(cells.is_within(cell, building, keep) for building in buildings)
        if is_off_ground and not dangers.crosses(cell, cell, keep_off):
            return cell
    return None


def _yards(world: World) -> list[Cell]:
    """The cells of our construction yards."""
    return [(building.x, building.y) for building in world.buildings.values() if building.type == CONSTRUCTION_YARD]


def _main_edge(start: Cell, world: World, ctx: MatchContext, sight: SightGrid) -> Mission:
    """The cell scout_view_cells from the enemy's start toward our base, side_offset_cells to the side; with
    `scout_cautious` on, moved out of the ground of the enemy's bases."""
    tuning = ctx.tuning.scouting
    cell = _edge_cell(start, tuning.scout_view_cells, world, tuning)
    if is_cautious(tuning):
        cell = _off_base_ground(start, cell, world, tuning)
    priority = MAIN_EDGE_PRIORITY if _is_production_known(world) else MAIN_EDGE_UNKNOWN_PRODUCTION_PRIORITY
    return Mission(MAIN_EDGE, cell, priority, sight.last_seen(cell))


def _edge_cell(start: Cell, along_cells: float, world: World, tuning: ScoutingTuning) -> Cell:
    """The cell `along_cells` from the enemy's start on the line toward our base, side_offset_cells to the side,
    inside the map."""
    span_x, span_y = world.base_center[0] - start[0], world.base_center[1] - start[1]
    length = math.hypot(span_x, span_y) or 1.0
    along, across = along_cells / length, tuning.side_offset_cells / length
    # Across the line is the line's direction turned a quarter: (x, y) -> (y, -x).
    edge = cells.nearest_cell(start[0] + along * span_x + across * span_y, start[1] + along * span_y - across * span_x)
    return cells.clamped(edge, world.map_bounds)


def _off_base_ground(start: Cell, edge: Cell, world: World, tuning: ScoutingTuning) -> Cell:
    """The first edge cell, from scout_view_cells out toward our base a cell at a time, farther than
    scout_enemy_base_cells, a cell and the flee distance from every remembered enemy building but a wall; `edge`, the
    first, when none short of our base is."""
    buildings = enemy_base_cells(world)
    keep = tuning.scout_enemy_base_cells + LINE_TOLERANCE_CELLS + flee_cells(tuning)
    for along_cells in range(tuning.scout_view_cells, math.ceil(cells.distance(start, world.base_center)) + 1):
        cell = _edge_cell(start, along_cells, world, tuning)
        if not any(cells.is_within(cell, building, keep) for building in buildings):
            return cell
    return edge


def _expansion_approach(world: World, ctx: MatchContext, sight: SightGrid) -> Mission | None:
    """Halfway between our main yard and the site of our next yard; None while there is no such site."""
    site = ctx.repeat_mcv_site or (None if ctx.reserved_field is None else ctx.reserved_field.center)
    if site is None:
        return None
    cell = cells.clamped(_midpoint(_main_yard(world), site), world.map_bounds)
    return Mission(EXPANSION_APPROACH, cell, EXPANSION_APPROACH_PRIORITY, sight.last_seen(cell))


def _attack_lane(start: Cell, world: World, sight: SightGrid, army_seen_tick: int) -> Mission:
    """Halfway between the centre of our base and the enemy's start, of the higher priority while no enemy army has
    been in sight for LANE_UNSEEN_ARMY_SECONDS."""
    cell = cells.clamped(_midpoint(world.base_center, start), world.map_bounds)
    is_army_unseen = world.tick - army_seen_tick > LANE_UNSEEN_ARMY_SECONDS * TICKS_PER_SECOND
    priority = ATTACK_LANE_UNSEEN_ARMY_PRIORITY if is_army_unseen else ATTACK_LANE_PRIORITY
    return Mission(ATTACK_LANE, cell, priority, sight.last_seen(cell))


def _is_production_known(world: World) -> bool:
    """Whether an enemy barracks or war factory is remembered."""
    return any(record.is_building and record.type in PRODUCTION_BUILDINGS for record in world.enemy_memory.values())


def _main_yard(world: World) -> Cell:
    """Our construction yard nearest the centre of the base; the centre itself when no yard stands."""
    return min(_yards(world), key=lambda yard: cells.distance(yard, world.base_center), default=world.base_center)


def _midpoint(first: Cell, second: Cell) -> Cell:
    """The cell halfway between two cells."""
    return cells.nearest_cell((first[0] + second[0]) * _HALF, (first[1] + second[1]) * _HALF)


def gap(mission: Mission, tick: int) -> float:
    """How little is known of a mission's place: 1.0 for a place never seen, else its seconds unseen over
    GAP_FULL_SECONDS, at most 1.0."""
    if mission.seen_tick is None:
        return NEVER_SEEN_GAP
    return min(NEVER_SEEN_GAP, (tick - mission.seen_tick) / TICKS_PER_SECOND / GAP_FULL_SECONDS)


def score(mission: Mission, travel_seconds: float, tick: int, tuning: ScoutingTuning) -> float:
    """What a mission is worth: priority x gap / (travel_seconds + mission_observe_seconds + 1)."""
    return mission.priority * gap(mission, tick) / (travel_seconds + tuning.mission_observe_seconds + 1)


def options(
    scout: Unit,
    offered: Iterable[Mission],
    dangers: Dangers,
    world: World,
    ctx: MatchContext,
    *,
    blacklisted: Collection[tuple[str, Cell]] = (),
    must_keep_off: bool = False,
) -> list[Option]:
    """The missions a scout may take, each with its way and its score: those not `blacklisted` (by key) that it has a
    safe way to from where it stands, a way that keeps the flee distance from every danger with `must_keep_off`.

    With `scout_cautious` on, every way must keep the flee distance: `route` is called with must_keep_off=True
    whatever `must_keep_off` says, so that no leg, a way through the graph of waypoints included, passes close enough to
    a danger to make the scout flee on the way, give the mission up and be given it back once clear. A mission with no
    such way is not offered."""
    position, speed = (scout.x, scout.y), _speed(scout.type, world)
    keeps_off = must_keep_off or is_cautious(ctx.tuning.scouting)
    found = []
    for mission in offered:
        if mission.key in blacklisted:
            continue
        way = route(position, mission.cell, dangers, world, ctx, must_keep_off=keeps_off, keeps_from_center=False)
        if way is not None:
            worth = score(mission, _length(position, way) / speed, world.tick, ctx.tuning.scouting)
            found.append(Option(mission, tuple(way), worth, worth))
    return found


def assign(offered: Sequence[Sequence[Option]]) -> list[Option | None]:
    """The option each scout takes, of the options `offered` to each: the combination of one option or none for each
    scout, no mission twice, that gives the most forced missions, then assigns the most scouts, then is worth the most
    in total weight, one with two missions within OVERLAP_CELLS of each other counted at OVERLAP_SHARE of its total; of
    two as good, one without such an overlap, else the first tried."""
    best: tuple[Option | None, ...] = (None,) * len(offered)
    best_rank = (0, 0, 0.0, True)
    for combination in itertools.product(*[(*choices, None) for choices in _searched(offered)]):
        rank = _rank(combination)
        if rank is not None and rank > best_rank:
            best, best_rank = combination, rank
    return list(best)


def _rank(combination: Sequence[Option | None]) -> tuple[int, int, float, bool] | None:
    """How a combination ranks: the forced missions it gives, the scouts it assigns, its total weight (at OVERLAP_SHARE
    with an overlap) and whether it has no overlap; None for one that gives a mission twice."""
    chosen = [option for option in combination if option is not None]
    if len({option.mission.key for option in chosen}) < len(chosen):
        return None
    overlaps = any(
        cells.is_within(first.mission.cell, second.mission.cell, OVERLAP_CELLS)
        for first, second in itertools.combinations(chosen, 2)
    )
    total = sum(option.weight for option in chosen)
    given = sum(option.mission.forced for option in chosen)
    return given, len(chosen), total * OVERLAP_SHARE if overlaps else total, not overlaps


def _searched(offered: Sequence[Sequence[Option]]) -> Sequence[Sequence[Option]]:
    """The options each scout is tried with: all of them for up to FULL_SEARCH_SCOUTS scouts, else each scout's best
    OPTIONS_PER_SCOUT_BEYOND_FULL_SEARCH, the forced first and then by weight, so that the combinations stay few."""
    if len(offered) <= FULL_SEARCH_SCOUTS:
        return offered
    return [
        sorted(choices, key=lambda option: (not option.mission.forced, -option.weight))[
            :OPTIONS_PER_SCOUT_BEYOND_FULL_SEARCH
        ]
        for choices in offered
    ]


def _length(start: Cell, way: list[Cell]) -> float:
    """How long a way is from `start` through its cells, in cells."""
    stops = [start, *way]
    return sum(cells.distance(first, second) for first, second in zip(stops, stops[1:]))


def _speed(unit_type: str, world: World) -> float:
    """How fast a unit of a type moves, in cells a second; UNKNOWN_SPEED_CELLS_PER_SECOND when the item table does not
    say."""
    facts = world.items.get(unit_type)
    return facts.speed if facts is not None and facts.speed else UNKNOWN_SPEED_CELLS_PER_SECOND
