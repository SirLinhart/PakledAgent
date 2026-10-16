"""Stepped movement: main goes to a far destination in short steps and waits for itself at each step point.

An army sent 60 cells by one attack-move arrives strung out: its fast units
meet the enemy alone while its artillery is still half-way. With
`stepped_advance` of `[combat]` on for its advances, and `stepped_return` for
its moves home, main goes such a way in steps instead, and sets out on the
next only once it stands together at the one it is on.

**Which moves step.** A move steps when its destination lies
step_min_move_cells or more from the group's centroid as it starts; a shorter
one goes as one order, as before. The combat manager says which of main's
moves are advances and which are moves home (see `combat`).

**Step points.** The route is the move's waypoints, then its destination. A
step point lies step_cells along the route from the step's start, which is
the group's centroid for the first step and the step point released last
after it; the last step point is the destination, once step_cells or less of
the route is left. A step point on blocked ground is moved to the nearest
open cell within step_snap_cells, else to the first open cell of the route
up to step_snap_cells farther on. No step point lies within the range plus
staging_margin_cells of a known enemy ground defense (an AA gun or a SAM site
is none; an unknown range is unknown_range_cells of `[coverage]`): when the
step point or the way to it is in such reach, the step point is the last open
cell before it (see `forward_staging.staging_cell`), and the stepping ends
there. When that cell lies within step_arrive_cells of the step's start, the
stepping ends at the start. A step point is fixed when its step is ordered,
and worked out again only when a defense seen since puts it or the way to it
in reach.

**Release.** A unit has arrived within step_arrive_cells of the step point,
an artillery piece or a V2 within step_long_range_extra_cells more. The next
step is ordered in the first cycle in which step_arrive_share of the group
and every long-range unit have arrived (`arrived`), step_straggler_seconds
after half of the group first had (`stragglers`), or step_max_seconds after
the step was ordered (`blocked`). Once the last step point is released the
move is over: the combat manager carries the action on at its destination as
it would without steps, and the same move does not step again.

**Orders.** When a step is ordered every unit of the group not standing idle
at its step point is due its order; after that a unit standing idle away from
it is due again at most every `REORDER_SECONDS` (see `StepMove.due`).

**Stepping back.** A group on a stepped advance may step back (see
`StepMove.step_back`): it moves together to the current step's start, the
step point released last or the move's start, and no step is released while
it is back; it steps on once it is told to resume, the abandoned step ordered
afresh with its clocks restarted. The commander's retreat override steps
main back so (see `commander`), and lets it step on when it lets main go.

**Meeting the enemy.** With `met_enemy_decision` of `[combat]` on, a group
on a stepped advance that meets enemies decides once whether it fights them
or steps back from them. The enemies it meets are the enemy ground combat
units that count in a fight (in sight or seen lately; no aircraft) within
met_cells of one of its units, and the known ground defenses whose range and
`DEFENSE_MET_MARGIN` reach one; its ratio against them is that of its units
within `BODY_CELLS` of its centroid (`fight.ratio_against`). At met_fight_ratio
or more it takes `fight`, else `step_back`; a decision is kept for
met_decision_seconds whatever the ratio does, and after that a fight turns
to a step back only below met_abandon_ratio. Under a fight no step is
released while a met enemy remains (the combat manager fights them within
met_chase_cells of the step's way, see `combat`), and once none has remained
for met_clear_seconds the step is ordered afresh, its clocks restarted.
Under a step back the group moves together to the current step's start, the
step point released last or the move's start, and waits there. It steps
forward again, the abandoned step ordered afresh, once no counted enemy has
stood within step_back_clear_cells of the abandoned step point for
step_back_clear_seconds (`enemy_left`), or once its units, the units that
joined it included, weigh met_resume_ratio or more against the enemies it
met (`ratio`); waiting there and met again at under met_fight_ratio, it steps
back one more step point, never past the move's start. Each decision writes a
`combat` event of reason `met` (the decision, the ratio, the value met and
the group's cell), and each end one of reason `met_end` (`resumed_by`
`cleared`, `enemy_left` or `ratio`).

**Caught on the move.** With `moving_returns_fire` of `[combat]` on, a group
on its way home (stepped or not), stepping back, or between two step points
of an advance with no meeting decided is caught once a visible enemy ground
combat unit has had one of its units within its weapon range and
`IN_RANGE_MARGIN` in every cycle for caught_confirm_seconds (`in_range`), or
once the way from its centroid to the cell it moves to passes within
route_block_cells of one (`on_route`): it does not run through it. A caught
group fights together whatever its ratio (see `CaughtWatch` and `combat`),
and moves on once, for caught_clear_seconds, no enemy has had one of its
units in reach and no visible enemy combat unit has stood within
`CAUGHT_NEAR_CELLS` of its centroid. A catch writes a `combat` event of
reason `caught` (the cell, the enemy value, the ratio and `by`), and its end
one of reason `caught_end` with the seconds the fight lasted.

**Records.** A `combat` event of reason `step` is written when a move starts
stepping (phase `start`: its kind, destination and route), at each release
(phase `release`: the step's index and cell, the share of the group arrived,
whether every long-range unit had, what released it and the seconds the step
took) and when the move is over (phase `end`).

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, siege
from ucla_agent.games.redalert.fight import (
    Point,
    centroid,
    ground_defenses,
    is_counted,
    is_near,
    ratio_against,
    worth,
)
from ucla_agent.games.redalert.forward_staging import Reach, staging_cell
from ucla_agent.games.redalert.groups import SIEGE_TYPES
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.scout_route import distance_to_segment
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

COMBAT_EVENT = "combat"
STEP = "step"
# The kinds of a stepped move: toward the enemy, or back toward the base.
ADVANCE = "advance"
HOME = "home"
# The phases of a step event, and what released a step.
START = "start"
RELEASE = "release"
END = "end"
ARRIVED = "arrived"
STRAGGLERS = "stragglers"
BLOCKED = "blocked"
# A unit standing idle away from its step point is ordered there again at most this often: an order is under way.
REORDER_SECONDS = 2
# The share of the group whose arrival starts the stragglers' clock.
HALF = 0.5
_SHARE_DECIMALS = 2
# What holds a stepping group's step back: it has stepped back.
STEP_BACK = "step_back"
MET = "met"
MET_END = "met_end"
# What a stepping group that meets the enemy does, and what ends a step back or a fight.
FIGHT = "fight"
STEP_BACK_AGAIN = "step_back_again"
CLEARED = "cleared"
ENEMY_LEFT = "enemy_left"
RATIO = "ratio"
# The group's units this near its centroid are weighed against the enemies it meets.
BODY_CELLS = 15
# A ground defense is met once its range and this many cells reach a unit of the group.
DEFENSE_MET_MARGIN = 2
_RATIO_DECIMALS = 2
CAUGHT = "caught"
CAUGHT_END = "caught_end"
# How a group on the move is caught: an enemy has had it in range long enough, or stands on its way.
IN_RANGE = "in_range"
ON_ROUTE = "on_route"
# An enemy shoots at a unit of ours within its weapon range and this margin.
IN_RANGE_MARGIN = 1
# A caught group's fight is not over while a visible enemy combat unit stands this near its centroid.
CAUGHT_NEAR_CELLS = 8


@dataclass(frozen=True)
class Plan:
    """A move the combat manager asks to step: what tells it apart from other moves, whether it is an advance or a move
    home, its destination and the waypoints before it."""

    key: Hashable
    kind: str
    destination: Cell
    waypoints: tuple[Cell, ...] = ()


@dataclass(frozen=True)
class StepStatus:
    """What the commander reads of main's stepped move in a cycle (see `commander`): its kind, its destination, whether
    its step is released in the cycle, where a step back would take main, how near another destination counts as the
    same (step_cells), and the longest a step may take (step_max_seconds)."""

    kind: str
    destination: Cell
    releases: bool
    back_cell: Cell
    same_cells: int
    max_seconds: int


@dataclass(frozen=True)
class Arrival:
    """How much of the group stands at its step point: the share of it arrived and whether every long-range unit has."""

    share: float
    long_range_present: bool


def defense_reach(world: World, tuning: Tuning) -> list[Reach]:
    """The discs no step point lies in: each known enemy ground defense with its range plus staging_margin_cells."""
    margin = tuning.combat.staging_margin_cells
    return [Reach(record.x, record.y, _range(record, world, tuning) + margin) for record in ground_defenses(world)]


def along(start: Point, route: Sequence[Cell], distance: float) -> Point:
    """The point `distance` cells along the polyline from `start` through the route's cells; its last cell when the
    polyline is shorter."""
    here = start
    left = distance
    for cell in route:
        span = math.hypot(cell[0] - here[0], cell[1] - here[1])
        if span >= left and span > 0:
            share = left / span
            return here[0] + (cell[0] - here[0]) * share, here[1] + (cell[1] - here[1]) * share
        left -= span
        here = cell
    return here


def route_length(start: Point, route: Sequence[Cell]) -> float:
    """The length of the polyline from `start` through the route's cells."""
    total, here = 0.0, start
    for cell in route:
        total += math.hypot(cell[0] - here[0], cell[1] - here[1])
        here = cell
    return total


def snapped(cell: Cell, start: Point, route: Sequence[Cell], distance: float, terrain: Terrain, snap: int) -> Cell:
    """A step point on open ground: the cell itself when open, else the nearest open cell within `snap`, else the
    first open cell of the route up to `snap` cells farther on; the cell itself when none is open."""
    blocked = terrain.blocked
    if cell not in blocked:
        return cell
    near = [
        (math.hypot(dx, dy), (cell[0] + dx, cell[1] + dy))
        for dx in range(-snap, snap + 1)
        for dy in range(-snap, snap + 1)
        if math.hypot(dx, dy) <= snap and (cell[0] + dx, cell[1] + dy) not in blocked
    ]
    if near:
        return min(near)[1]
    for extra in range(1, snap + 1):
        farther = cells.nearest_cell(*along(start, route, distance + extra))
        if farther not in blocked:
            return farther
    return cell


def next_step(
    start: Cell, route: Sequence[Cell], world: World, terrain: Terrain, tuning: Tuning
) -> tuple[Cell, bool] | None:
    """The step point after `start` on the route left, and whether it is the last; None when the stepping ends at
    `start`, the way on lying in a defense's reach from there."""
    combat = tuning.combat
    length = route_length(start, route)
    is_last = length <= combat.step_cells
    if is_last:
        point = route[-1]
    else:
        raw = cells.nearest_cell(*along(start, route, combat.step_cells))
        point = snapped(raw, start, route, combat.step_cells, terrain, combat.step_snap_cells)
    return out_of_reach(start, point, is_last, defense_reach(world, tuning), terrain, combat)


def out_of_reach(
    start: Cell, point: Cell, is_last: bool, reach: list[Reach], terrain: Terrain, tuning: CombatTuning
) -> tuple[Cell, bool] | None:
    """The step point kept out of the defenses' reach: `point` itself while neither it nor the way to it is in reach;
    else the last open cell before the reach, the last step point, or None when that cell is none or within
    step_arrive_cells of `start`."""
    way = cells.line(start, point)
    if not any(disc.holds(cell) for cell in way for disc in reach):
        return point, is_last
    short = staging_cell(start, point, reach, terrain)
    if short is None or cells.is_within(short, start, tuning.step_arrive_cells):
        return None
    return short, True


def arrival(units: Sequence[Unit], point: Cell, tuning: CombatTuning) -> Arrival:
    """How much of the group stands at a step point; the long-range units count within step_long_range_extra_cells
    more."""
    near = tuning.step_arrive_cells
    if tuning.step_plan_continuity and len(units) >= tuning.step_large_group_units:
        # A large group stands spread wider than step_arrive_cells round its step point however well it arrives.
        near += tuning.step_large_arrive_extra_cells
    far = near + tuning.step_long_range_extra_cells
    arrived = [is_near(point, unit.x, unit.y, far if unit.type in SIEGE_TYPES else near) for unit in units]
    long_range = [is_there for unit, is_there in zip(units, arrived) if unit.type in SIEGE_TYPES]
    return Arrival(sum(arrived) / len(units), all(long_range))


def long_range_slots(
    units: Sequence[Unit], start: Cell, point: Cell, kind: str, world: World, tuning: Tuning
) -> dict[int, Cell]:
    """The slot of each long-range unit on a step from `start` to `point`, by unit id: on a line across the step's
    way, slot_spacing_cells of `[siege]` apart (see `siege.slot_cells`), through the step point moved
    step_long_range_extra_cells back toward the step's start on an advance, behind the screen, and on beyond it on the
    way home and on a step back, the screen between them and the enemy behind; within the reach a long-range unit has
    arrived in."""
    span = cells.distance(start, point)
    if not units or span == 0:
        return {unit.id: point for unit in units}
    heading = ((point[0] - start[0]) / span, (point[1] - start[1]) / span)
    offset = tuning.combat.step_long_range_extra_cells * (-1 if kind == ADVANCE else 1)
    middle = (point[0] + heading[0] * offset, point[1] + heading[1] * offset)
    slots = siege.slot_cells(list(units), siege.SlotLine(middle, heading), world, tuning.siege)
    return {unit.id: slots.get(unit.id, point) for unit in units}


def met_enemies(units: Sequence[Unit], world: World, tuning: Tuning) -> list[EnemyRecord]:
    """The enemies a stepping group meets: the enemy ground combat units that count in a fight within met_cells of one
    of its units, and the known ground defenses whose range and DEFENSE_MET_MARGIN reach one."""
    reach = tuning.combat.met_cells
    damage = world.items.damage
    hostile = [
        record
        for record in world.enemy_memory.values()
        if is_counted(record)
        and not damage.get(record.type).air
        and any(is_near((unit.x, unit.y), record.x, record.y, reach) for unit in units)
    ]
    defenses = [
        record
        for record in ground_defenses(world)
        if any(
            is_near((unit.x, unit.y), record.x, record.y, _range(record, world, tuning) + DEFENSE_MET_MARGIN)
            for unit in units
        )
    ]
    return [*hostile, *defenses]


def met_ratio(units: Sequence[Unit], enemies: Sequence[EnemyRecord], world: World, tuning: Tuning) -> float:
    """The ratio of the group's units within BODY_CELLS of its centroid against enemies it met."""
    middle = centroid(units)
    body = [unit for unit in units if is_near(middle, unit.x, unit.y, BODY_CELLS)]
    return ratio_against(body, enemies, world, tuning)


def _range(record: EnemyRecord, world: World, tuning: Tuning) -> float:
    """An enemy defense's weapon range by the item table, or the one taken for a range that is not known."""
    facts = world.items.get(record.type)
    return facts.range if facts is not None and facts.range is not None else tuning.coverage.unknown_range_cells


class StepMove:
    """One stepped move: its route, the step points released, the step under way and its clocks."""

    def __init__(self, plan: Plan, origin: Cell, point: Cell, is_last: bool, tick: int) -> None:
        self.key, self.kind, self.destination = plan.key, plan.kind, plan.destination
        # The waypoints not yet passed by a step point, then the destination.
        self.waypoints: list[Cell] = list(plan.waypoints)
        # The step points released, the move's start first: the last is the current step's start.
        self.released: list[Cell] = [origin]
        self.point, self.is_last = point, is_last
        self.index = 1
        self.ordered_tick = tick
        # The tick the move was planned at and the destination it was planned for: a destination step_cells or more from
        # that one re-plans the move, no sooner than step_replan_seconds after.
        self.planned_tick, self.planned_destination = tick, plan.destination
        # The tick at which half of the group first stood at the step point; None before.
        self.half_tick: int | None = None
        # The units of main the move orders: those of its start, and on an advance those that have joined main since.
        self.group: set[int] = set()
        # Unit id -> the tick it was last ordered to the step point, and the tick every unit was last due afresh.
        self._sent: dict[int, int] = {}
        self._fresh_tick = tick
        # What holds the step back (STEP_BACK, or a fight with the enemies met), and since when; None while the group
        # steps on.
        self.decision: str | None = None
        self.decided_tick = tick
        # Whether the commander's retreat override has the group stepped back, until it lets main go.
        self.is_pulled_back = False
        # Whether the group is caught on the move in the cycle under way (see `CaughtWatch`): it fights, and no step
        # is released.
        self.is_caught = False
        # Since when the group has been clear of the enemies it met; None while it is not.
        self.clear_since: int | None = None
        # The enemies met when the group stepped back, which it weighs itself against to step forward again.
        self.remembered: tuple[EnemyRecord, ...] = ()

    @property
    def start(self) -> Cell:
        """The current step's start: the step point released last, or the move's start."""
        return self.released[-1]

    @property
    def back_point(self) -> Cell:
        """Where a group stepping back goes: the current step's start."""
        return self.released[-1]

    @property
    def is_holding(self) -> bool:
        """Whether the step is held back: no step is released while the group is stepped back or fights."""
        return self.decision is not None or self.is_caught

    def step_back(self, tick: int, met: Sequence[EnemyRecord] = ()) -> None:
        """Step back to the current step's start, every unit due its move there, remembering the enemies met."""
        self.decision, self.decided_tick, self.clear_since = STEP_BACK, tick, None
        self.remembered = tuple(met)
        self.resend(tick)

    def step_back_again(self, tick: int) -> bool:
        """Step back one more step point from the one the group waits at, the step it leaves the one to take afresh;
        False on the first step, which goes no farther back than the move's start."""
        if len(self.released) == 1:
            return False
        self.point, self.is_last = self.released.pop(), False
        self.decided_tick, self.clear_since = tick, None
        self.resend(tick)
        return True

    def restart(self, tick: int) -> None:
        """The group goes on after a fight it was caught in: the step it was on, or its step back, ordered afresh."""
        if self.decision is None:
            self.order(self.point, self.is_last, tick, 0)
        else:
            self.resend(tick)

    def resume(self, tick: int) -> None:
        """Step on: the step under way ordered afresh, its clocks restarted."""
        self.decision, self.clear_since, self.remembered = None, None, ()
        self.order(self.point, self.is_last, tick, 0)

    @property
    def route(self) -> list[Cell]:
        """The cells of the route left: the waypoints not yet passed, then the destination."""
        return [*self.waypoints, self.destination]

    def order(self, point: Cell, is_last: bool, tick: int, step_cells: int) -> None:
        """Order a step to a point step_cells along the route, or its end: its clocks start, and every unit not at the
        point is due."""
        self.point, self.is_last, self.ordered_tick, self.half_tick = point, is_last, tick, None
        self.resend(tick)
        self._drop_passed(math.inf if is_last else step_cells)

    def resend(self, tick: int) -> None:
        """Have every unit not at its cell ordered there afresh in this cycle: the step was ordered, or its point
        moved."""
        self._sent, self._fresh_tick = {}, tick

    def due(self, targets: Mapping[int, Cell], units: Sequence[Unit], tick: int, reach: float) -> list[int]:
        """The units given their step order in this cycle, each to its cell of `targets`: in the cycle the step is
        ordered all not standing idle within `reach` of it; after that those standing idle away from it, each at most
        every REORDER_SECONDS."""
        away = [unit for unit in units if not (unit.idle and is_near(targets[unit.id], unit.x, unit.y, reach))]
        if tick == self._fresh_tick:
            going = away
        else:
            wait = REORDER_SECONDS * TICKS_PER_SECOND
            going = [unit for unit in away if unit.idle and tick - self._sent.get(unit.id, -wait) >= wait]
        for unit in going:
            self._sent[unit.id] = tick
        return [unit.id for unit in going]

    def released_by(self, units: Sequence[Unit], tick: int, tuning: CombatTuning) -> tuple[str | None, Arrival]:
        """What releases the step in this cycle, None for nothing, and how much of the group has arrived; the
        stragglers' clock is started when half of it first has."""
        found = arrival(units, self.point, tuning)
        if self.half_tick is None and found.share >= HALF:
            self.half_tick = tick
        return self._reason(found, self.half_tick, tick, tuning), found

    def would_release(self, units: Sequence[Unit], tick: int, tuning: CombatTuning) -> bool:
        """Whether the step is released in this cycle, read without starting any clock."""
        if self.is_holding:
            return False
        found = arrival(units, self.point, tuning)
        half_tick = tick if self.half_tick is None and found.share >= HALF else self.half_tick
        return self._reason(found, half_tick, tick, tuning) is not None

    def _reason(self, found: Arrival, half_tick: int | None, tick: int, tuning: CombatTuning) -> str | None:
        """What releases the step, the group arrived as `found` and half of it first at `half_tick`; None for
        nothing."""
        if found.share >= tuning.step_arrive_share and found.long_range_present:
            return ARRIVED
        if half_tick is not None and tick - half_tick >= tuning.step_straggler_seconds * TICKS_PER_SECOND:
            return STRAGGLERS
        if tick - self.ordered_tick >= tuning.step_max_seconds * TICKS_PER_SECOND:
            return BLOCKED
        return None

    def _drop_passed(self, travelled: float) -> None:
        """Forget the waypoints the step to its point passes: those within `travelled` cells along the route from the
        step's start."""
        here, gone = self.start, 0.0
        while self.waypoints:
            gone += cells.distance(here, self.waypoints[0])
            if gone > travelled:
                return
            here = self.waypoints.pop(0)


class Stepper:
    """main's stepped move, if any, and the moves that are over. See the module's note."""

    def __init__(self) -> None:
        self.move: StepMove | None = None
        # The key of the move whose stepping is over, which steps no more while it is followed; None for none.
        self._done: Hashable | None = None
        self._is_followed = False

    def begin_cycle(self) -> None:
        """A cycle starts: main steps in it only if `follow` is called."""
        self._is_followed = False

    def end_cycle(self) -> None:
        """A cycle ends: a move not followed in it is over, and so is the memory of a move done."""
        if not self._is_followed:
            self.move, self._done = None, None

    def is_done(self, key: Hashable) -> bool:
        """Whether the move of this key has been stepped to its end: it is carried on as it would without steps."""
        return self._done == key

    def finish(self, world: World) -> None:
        """End the move under way at once: its action goes on unstepped (a siege's long-range units reach their band)."""
        self._is_followed = True
        if self.move is not None:
            self._end(world)

    def follow(
        self, plan: Plan, units: Sequence[Unit], world: World, terrain: Terrain, tuning: Tuning
    ) -> StepMove | None:
        """The stepped move main is on in this cycle for a plan, its step released and the next one ordered when due;
        None while the plan is not stepped: too short a move, one that ended, or none that could start."""
        self._is_followed = True
        self._carry_over(plan, world, tuning)
        if self.move is not None and self.move.key != plan.key:
            self._end(world)
            self._done = None
        if self.move is None:
            if self._done == plan.key:
                return None
            self._done = None
            self.move = self._started(plan, units, world, terrain, tuning)
            return self.move
        self._go_on(self.move, plan, units, world, terrain, tuning)
        return self.move

    def _carry_over(self, plan: Plan, world: World, tuning: Tuning) -> None:
        """With step_plan_continuity on, a move under way of the plan's kind carried over to the plan: its steps kept
        and its key the plan's, while the plan's destination lies within step_cells of the one the move was planned
        for or the move was planned less than step_replan_seconds ago; ended to be planned afresh otherwise. A plan's
        destination on a moving enemy group shifts a few cells from cycle to cycle with its key, and each shift started
        the steps afresh, so that no step was ever released."""
        move, combat = self.move, tuning.combat
        if move is None or not combat.step_plan_continuity or plan.kind != move.kind:
            return
        is_near = cells.is_within(plan.destination, move.planned_destination, combat.step_cells)
        if is_near or world.tick - move.planned_tick < combat.step_replan_seconds * TICKS_PER_SECOND:
            move.key = plan.key
            return
        self._end(world)
        self._done = None

    def _go_on(
        self, move: StepMove, plan: Plan, units: Sequence[Unit], world: World, terrain: Terrain, tuning: Tuning
    ) -> None:
        """Bring a move under way up to date in a cycle: its destination and group, its step point kept out of reach,
        its decision on the enemies met, and its step released when due; ended once none of its group is left."""
        move.destination = plan.destination
        if move.kind == ADVANCE:
            move.group.update(unit.id for unit in units)
        members = [unit for unit in units if unit.id in move.group]
        if not members:
            self._end(world)
            return
        self._keep_out_of_reach(move, world, terrain, tuning)
        if self.move is not None and move.kind == ADVANCE and tuning.combat.met_enemy_decision:
            _meet(move, members, world, tuning)
        if self.move is not None and not move.is_holding:
            self._release(move, members, world, terrain, tuning)

    def status(self, units: Sequence[Unit], world: World, tuning: Tuning) -> StepStatus | None:
        """What the commander reads of the move under way in this cycle, its units given; None while there is none."""
        move = self.move
        if move is None:
            return None
        members = [unit for unit in units if unit.id in move.group]
        combat = tuning.combat
        releases = bool(members) and move.would_release(members, world.tick, combat)
        return StepStatus(
            move.kind, move.destination, releases, move.back_point, combat.step_cells, combat.step_max_seconds
        )

    def is_stepping(self, key: Hashable) -> bool:
        """Whether the move of this key is under way in steps."""
        return self.move is not None and self.move.key == key

    def has_passed_waypoints(self, key: Hashable) -> bool:
        """Whether the move of this key has passed its waypoints by its step points, or has been stepped to its end."""
        move = self.move
        return self._done == key or (move is not None and move.key == key and not move.waypoints)

    def _started(
        self, plan: Plan, units: Sequence[Unit], world: World, terrain: Terrain, tuning: Tuning
    ) -> StepMove | None:
        """A new stepped move, with its `start` event, when its destination is far enough to step; None otherwise."""
        middle = centroid(units)
        if (
            math.hypot(plan.destination[0] - middle[0], plan.destination[1] - middle[1])
            < tuning.combat.step_min_move_cells
        ):
            return None
        origin = cells.nearest_cell(*middle)
        route = [*plan.waypoints, plan.destination]
        first = next_step(origin, route, world, terrain, tuning)
        if first is None:
            self._done = plan.key
            return None
        move = StepMove(plan, origin, first[0], first[1], world.tick)
        move.group = {unit.id for unit in units}
        move.order(first[0], first[1], world.tick, tuning.combat.step_cells)
        _log(
            START, world.tick, kind=plan.kind, destination=list(plan.destination), route=[list(cell) for cell in route]
        )
        return move

    def _keep_out_of_reach(self, move: StepMove, world: World, terrain: Terrain, tuning: Tuning) -> None:
        """Move the step point out of the reach of a defense seen since it was fixed; end the move when none is left."""
        kept = out_of_reach(move.start, move.point, move.is_last, defense_reach(world, tuning), terrain, tuning.combat)
        if kept is None:
            self._end(world)
        elif kept[0] != move.point:
            move.point, move.is_last = kept
            move.resend(world.tick)

    def _release(self, move: StepMove, units: Sequence[Unit], world: World, terrain: Terrain, tuning: Tuning) -> None:
        """Release the step when it is due, with its `release` event, and order the next one or end the move."""
        reason, found = move.released_by(units, world.tick, tuning.combat)
        if reason is None:
            return
        _log(
            RELEASE,
            world.tick,
            index=move.index,
            cell=list(move.point),
            arrived_share=round(found.share, _SHARE_DECIMALS),
            long_range_present=found.long_range_present,
            released_by=reason,
            seconds=round((world.tick - move.ordered_tick) / TICKS_PER_SECOND, 1),
        )
        move.released.append(move.point)
        following = None if move.is_last else next_step(move.point, move.route, world, terrain, tuning)
        if following is None:
            self._end(world)
            return
        move.index += 1
        move.order(following[0], following[1], world.tick, tuning.combat.step_cells)

    def _end(self, world: World) -> None:
        """The move under way is over, with its `end` event: its key steps no more while it is followed."""
        move, self.move = self.move, None
        self._done = move.key
        _log(END, world.tick, kind=move.kind, cell=list(move.start), steps=len(move.released) - 1)


class CaughtWatch:
    """Whether a group of main on the move is caught by the enemy, and the fight it then has. See the module's
    note."""

    def __init__(self) -> None:
        # The group's centroid when it was caught, and the tick; None while it is not caught.
        self.center: Cell | None = None
        self._caught_tick = 0
        # The first tick of the cycles in a row in which an enemy had a unit of the group in reach; None for none.
        self._in_range_since: int | None = None
        # The first tick of the cycles in a row in which a caught group was clear; None for none.
        self._clear_since: int | None = None
        self._is_followed = False
        # Whether the fight of a caught group ended in the cycle under way: its move is ordered afresh.
        self.ended_now = False

    def begin_cycle(self) -> None:
        """A cycle starts: the group is watched in it only if `follow` is called."""
        self._is_followed = False
        self.ended_now = False

    def end_cycle(self) -> None:
        """A cycle ends: a group not on the move in it is caught no more, its clocks started over."""
        if not self._is_followed:
            self.center, self._in_range_since, self._clear_since = None, None, None

    def follow(self, units: Sequence[Unit], heading_to: Cell | None, world: World, tuning: Tuning) -> bool:
        """Whether a group on the move to `heading_to` (None for no cell) is caught in this cycle, with a `caught`
        event when it is caught and a `caught_end` event when its fight ends."""
        self._is_followed = True
        visible = _visible_ground_units(world)
        shooting = [record for record in visible if _has_in_reach(record, units, world)]
        if self.center is not None:
            return self._fights_on(units, visible, bool(shooting), world, tuning)
        return self._is_caught_now(units, heading_to, visible, shooting, world, tuning)

    def _fights_on(
        self, units: Sequence[Unit], visible: list[EnemyRecord], is_shot_at: bool, world: World, tuning: Tuning
    ) -> bool:
        """Whether a caught group fights on in this cycle: until caught_clear_seconds with no enemy in reach of it and
        none within CAUGHT_NEAR_CELLS of its centroid, its end a `caught_end` event."""
        tick = world.tick
        middle = centroid(units)
        near = any(is_near(middle, record.x, record.y, CAUGHT_NEAR_CELLS) for record in visible)
        self._clear_since = None if is_shot_at or near else (self._clear_since or tick)
        if (
            self._clear_since is None
            or tick - self._clear_since < tuning.combat.caught_clear_seconds * TICKS_PER_SECOND
        ):
            return True
        seconds = round((tick - self._caught_tick) / TICKS_PER_SECOND, 1)
        self.center, self._in_range_since, self._clear_since, self.ended_now = None, None, None, True
        records.log(COMBAT_EVENT, {"tick": tick, "reason": CAUGHT_END, "seconds": seconds})
        return False

    def _is_caught_now(
        self,
        units: Sequence[Unit],
        heading_to: Cell | None,
        visible: list[EnemyRecord],
        shooting: list[EnemyRecord],
        world: World,
        tuning: Tuning,
    ) -> bool:
        """Whether a group on the move is caught in this cycle: in range for caught_confirm_seconds, or an enemy on
        its way; the catch a `caught` event."""
        combat, tick = tuning.combat, world.tick
        self._in_range_since = (self._in_range_since or tick) if shooting else None
        by = None
        if (
            self._in_range_since is not None
            and tick - self._in_range_since >= combat.caught_confirm_seconds * TICKS_PER_SECOND
        ):
            by = IN_RANGE
        elif heading_to is not None and _on_the_way(units, heading_to, visible, combat.route_block_cells):
            by = ON_ROUTE
        if by is None:
            return False
        self._catch(by, units, [*shooting, *visible], world, tuning)
        return True

    def _catch(self, by: str, units: Sequence[Unit], enemies: list[EnemyRecord], world: World, tuning: Tuning) -> None:
        """The group is caught, with its `caught` event: the enemies within caught_group_cells of its centroid weighed
        against it."""
        middle = centroid(units)
        reach = tuning.combat.caught_group_cells
        near = list({record.id: record for record in enemies if is_near(middle, record.x, record.y, reach)}.values())
        self.center, self._caught_tick, self._clear_since = cells.nearest_cell(*middle), world.tick, None
        data = {
            "tick": world.tick,
            "reason": CAUGHT,
            "cell": list(self.center),
            "enemy_value": round(sum(worth(record.type, record.hp, world) for record in near)),
            "ratio": round(ratio_against(list(units), near, world, tuning), _RATIO_DECIMALS),
            "by": by,
        }
        records.log(COMBAT_EVENT, data)


def _visible_ground_units(world: World) -> list[EnemyRecord]:
    """The enemy combat units in sight that stand on the ground."""
    damage = world.items.damage
    return [
        record
        for record in world.enemy_memory.values()
        if record.visible and is_combat_unit(record) and not damage.get(record.type).air
    ]


def _has_in_reach(record: EnemyRecord, units: Sequence[Unit], world: World) -> bool:
    """Whether an enemy unit has one of ours within its weapon range and IN_RANGE_MARGIN, so that it can shoot it."""
    reach = world.items.damage.get(record.type).range + IN_RANGE_MARGIN
    return any(is_near((unit.x, unit.y), record.x, record.y, reach) for unit in units)


def _on_the_way(units: Sequence[Unit], heading_to: Cell, visible: list[EnemyRecord], reach: float) -> bool:
    """Whether a visible enemy stands within `reach` of the way from the group's centroid to the cell it moves to,
    ahead of the centroid: one at its heels is a pursuer, which catches it by its range alone."""
    middle = centroid(units)
    span = (heading_to[0] - middle[0], heading_to[1] - middle[1])
    return any(
        (record.x - middle[0]) * span[0] + (record.y - middle[1]) * span[1] > 0
        and distance_to_segment((record.x, record.y), middle, heading_to) <= reach
        for record in visible
    )


def _meet(move: StepMove, units: Sequence[Unit], world: World, tuning: Tuning) -> None:
    """Bring a stepping group's answer to the enemies it meets up to date: decide on a first meeting, turn a fight
    into a step back below met_abandon_ratio once its decision has been kept long enough, end a fight once clear, and
    end a step back or step back again (see the module's note)."""
    met = met_enemies(units, world, tuning)
    ratio = met_ratio(units, met, world, tuning) if met else None
    combat, tick = tuning.combat, world.tick
    if move.decision is None:
        if ratio is not None:
            decision = FIGHT if ratio >= combat.met_fight_ratio else STEP_BACK
            _decide(move, decision, met, ratio, units, world)
        return
    is_kept = tick - move.decided_tick < combat.met_decision_seconds * TICKS_PER_SECOND
    if move.decision == FIGHT:
        if ratio is not None and not is_kept and ratio < combat.met_abandon_ratio:
            _decide(move, STEP_BACK, met, ratio, units, world)
        elif _has_cleared(move, ratio is None, tick, combat.met_clear_seconds):
            _end_meeting(move, CLEARED, tick)
        return
    _wait_stepped_back(move, units, met, ratio, world, tuning)


def _wait_stepped_back(
    move: StepMove,
    units: Sequence[Unit],
    met: list[EnemyRecord],
    ratio: float | None,
    world: World,
    tuning: Tuning,
) -> None:
    """A group stepped back: on again once its ratio against the enemies it met reaches met_resume_ratio or the
    abandoned step point has stayed clear long enough; one step point farther back when met again where it waits at
    under met_fight_ratio, once the step back has been kept met_decision_seconds."""
    combat, tick = tuning.combat, world.tick
    if move.is_pulled_back:
        return
    resumed_by = _resumed_by(move, units, world, tuning)
    if resumed_by is not None:
        _end_meeting(move, resumed_by, tick)
        return
    is_kept = tick - move.decided_tick < combat.met_decision_seconds * TICKS_PER_SECOND
    is_waiting = not is_kept and arrival(units, move.back_point, combat).share >= combat.step_arrive_share
    if is_waiting and ratio is not None and ratio < combat.met_fight_ratio and move.step_back_again(tick):
        _log_met(STEP_BACK_AGAIN, ratio, met, units, world)


def _resumed_by(move: StepMove, units: Sequence[Unit], world: World, tuning: Tuning) -> str | None:
    """Why a group stepped back steps on in this cycle: its ratio against the enemies it met, still remembered, has
    reached met_resume_ratio (RATIO), or no counted enemy has stood within step_back_clear_cells of the abandoned step
    point for step_back_clear_seconds (ENEMY_LEFT); None while neither holds."""
    combat = tuning.combat
    remembered = [world.enemy_memory[record.id] for record in move.remembered if record.id in world.enemy_memory]
    if move.remembered and ratio_against(list(units), remembered, world, tuning) >= combat.met_resume_ratio:
        return RATIO
    reach = combat.step_back_clear_cells
    near = [
        record
        for record in world.enemy_memory.values()
        if is_counted(record) and cells.is_within((record.x, record.y), move.point, reach)
    ]
    return ENEMY_LEFT if _has_cleared(move, not near, world.tick, combat.step_back_clear_seconds) else None


def _decide(
    move: StepMove, decision: str, met: list[EnemyRecord], ratio: float, units: Sequence[Unit], world: World
) -> None:
    """Take a decision on the enemies met, with its `met` event."""
    if decision == STEP_BACK:
        move.step_back(world.tick, met)
    else:
        move.decision, move.decided_tick, move.clear_since = FIGHT, world.tick, None
    _log_met(decision, ratio, met, units, world)


def _has_cleared(move: StepMove, is_clear: bool, tick: int, seconds: int) -> bool:
    """Whether the group has been clear for `seconds`, its clock started in the first clear cycle and stopped by one
    that is not."""
    if not is_clear:
        move.clear_since = None
        return False
    if move.clear_since is None:
        move.clear_since = tick
    return tick - move.clear_since >= seconds * TICKS_PER_SECOND


def _end_meeting(move: StepMove, reason: str, tick: int) -> None:
    """The group steps on, the step ordered afresh, with a `met_end` event."""
    move.resume(tick)
    records.log(COMBAT_EVENT, {"tick": tick, "reason": MET_END, "resumed_by": reason, "cell": list(move.point)})


def _log_met(decision: str, ratio: float, met: Sequence[EnemyRecord], units: Sequence[Unit], world: World) -> None:
    """Write one `combat` event of reason `met`."""
    data = {
        "tick": world.tick,
        "reason": MET,
        "decision": decision,
        "ratio": round(ratio, _RATIO_DECIMALS),
        "enemy_value": round(sum(worth(record.type, record.hp, world) for record in met)),
        "cell": list(cells.nearest_cell(*centroid(units))),
    }
    records.log(COMBAT_EVENT, data)


def _log(phase: str, tick: int, **data: object) -> None:
    """Write one `combat` event of reason `step`."""
    records.log(COMBAT_EVENT, {"tick": tick, "reason": STEP, "phase": phase, **data})
