"""Forward staging: main gathers just outside the enemy's reach before it goes in, and its joiners meet it there.

An army sent on its objective by an attack-move arrives as it walks: its fast
units first, its artillery last and the units made since one by one, so the
enemy meets a few of them at a time. With `forward_staging` of `[combat]` on,
main stops short of the enemy's reach instead, waits there for its body and
its long-range units, and goes in together.

**When.** Main sets out on an objective: under the plan's movement (the
advance from the staging point and the way on to the objective, its siege and
its sweep, under PRESS, STRIKE, FINISH or any plan that sends main forward),
or under the model commander's advance, an attack on a group farther than
near_attack_cells of `[commander]` from main's centroid, a flank on its last
leg, or a siege. Staging applies only while a known enemy defense that fires
at ground units or a visible enemy combat unit lies within `THREAT_CELLS` of
the objective.

**The staging cell.** The cells of the straight way from main's centroid to
the cell main heads for now (the next cell of a way through the graph of
waypoints while one is left, else the objective) are walked in order. A cell
is in the enemy's reach when it lies nearer than its range plus
staging_margin_cells to a known enemy defense that fires at ground units (an
AA gun or a SAM site is none), or to a visible enemy combat unit within
`THREAT_CELLS` of the objective (an aircraft is none: it is not held by the
ground). The staging cell is the last cell before the first one in reach,
passing over the cells the terrain blocks. There is none when no cell of the
way is in reach; when the first one is, main's centroid standing in reach
already; or when it lies within `MIN_STAGING_CELLS` of main's centroid, main
being there already. Main then goes on as it would without staging, and the
next cycle looks again.

**Holding there.** While main stages, it is sent to the staging cell, where
it holds: its units in contact fire at the enemies already within their
weapon range and are not sent forward, its long-range units go to their slots
behind the screen (see `siege.Siege.march`) and the rest attack-move to the
cell, but those standing idle there. The cell is worked out again in every
cycle while main stages, from where main stands then, and moved when the cell
it had is in the enemy's reach by then or the new one lies `RESTAGE_CELLS`
or more from it: an enemy coming at main pushes it back, and the cell does
not wander a cell at a time as main moves. While no cell is found, the one it
had is kept.

**Engaging.** Main goes on from the staging cell into its normal movement
once staging_body_share of its units stand within staging_body_cells of the
cell and every long-range unit (artillery, V2) within staging_body_cells and
`LONG_RANGE_EXTRA_CELLS`, its slots lying behind the screen; or once
staging_timeout_seconds have passed since it was first sent there. Once it
has, it does not stage again until its objective changes, it retreats, or the
commander's forward gate closes.

**Joiners.** While a staging cell stands, from the cycle main is first sent
there until main stages no more, the units joining main go to the staging
cell, and on together from there to main's centroid once those standing
there are worth reinforcement_strength of `[combat]`, or `JOIN_WAIT_SECONDS`
after the first of them arrived (see `JoinWait`). This takes the place of
the plan's reinforcement at the staging point and of the joining units of
the commander's forward gate (see `command_gate`).

**Precedence.** The commander's retreat override, base defense's override
and recall, the crush runs and the commander's detachments come first: a
cycle in which main is pulled back, taken or called home is no staging cycle,
and its staging is over; the tanks on a crush run and the units on a
detachment are not main's in its staging.

**Records.** Each staging writes a `combat` event of reason `staging` when
main arrives at the cell, its centroid within staging_body_cells of it
(phase `arrival`), and one when it goes on (phase `engage`, `engaged_by`
`body` or `timeout`): the cell, the share of main's units within
staging_body_cells of it, whether every long-range unit is within its reach
of it and how many there are.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math
from collections.abc import Hashable
from dataclasses import dataclass

from ucla_agent.core import records
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.fight import Point, centroid, ground_defenses, is_near
from ucla_agent.games.redalert.groups import SIEGE_TYPES
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

COMBAT_EVENT = "combat"
STAGING = "staging"
# The phases of a staging event, and why main went on from the staging cell.
ARRIVAL = "arrival"
ENGAGE = "engage"
BODY = "body"
TIMEOUT = "timeout"
# Staging applies only while an enemy defense or combat unit lies this near the objective: the fight is there.
THREAT_CELLS = 20
# No staging cell this near main's centroid: main stands there already.
MIN_STAGING_CELLS = 4
# The long-range units count as at the staging cell this much farther out than the body: their slots lie behind it.
LONG_RANGE_EXTRA_CELLS = 4
# A staging cell found this far from the one main has replaces it, though that one is still out of reach.
RESTAGE_CELLS = 2
# The units joining main at the staging cell go on this long after the first of them arrived, whatever they are worth.
JOIN_WAIT_SECONDS = 30
# The decimals the share of main's units at the staging cell is written with.
_SHARE_DECIMALS = 2


@dataclass(frozen=True)
class Reach:
    """The disc around an enemy that main stages outside of: its cell and its range plus staging_margin_cells."""

    x: int
    y: int
    radius: float

    def holds(self, cell: Cell) -> bool:
        """Whether a cell lies inside the disc."""
        return (cell[0] - self.x) ** 2 + (cell[1] - self.y) ** 2 < self.radius * self.radius


def enemy_reach(objective: Cell, world: World, tuning: Tuning) -> list[Reach]:
    """The reach main stages outside of on its way to an objective: each known enemy defense that fires at ground
    units, and each visible enemy combat unit on the ground within THREAT_CELLS of the objective, with its range plus
    staging_margin_cells; none while neither lies within THREAT_CELLS of the objective."""
    defenses = ground_defenses(world)
    units = [record for record in world.enemy_memory.values() if _is_ground_threat(record, objective, world)]
    if not units and not any(cells.is_within((record.x, record.y), objective, THREAT_CELLS) for record in defenses):
        return []
    margin = tuning.combat.staging_margin_cells
    return [Reach(record.x, record.y, _range(record, world, tuning) + margin) for record in (*defenses, *units)]


def _is_ground_threat(record: EnemyRecord, objective: Cell, world: World) -> bool:
    """Whether a remembered enemy is a combat unit in sight on the ground, no aircraft, within THREAT_CELLS of the
    objective."""
    is_ground_unit = is_combat_unit(record) and not world.items.damage.get(record.type).air
    return record.visible and is_ground_unit and cells.is_within((record.x, record.y), objective, THREAT_CELLS)


def staging_cell(start: Point, heading_to: Cell, reach: list[Reach], terrain: Terrain) -> Cell | None:
    """The last cell the terrain leaves open on the straight way from `start` to `heading_to` before the first cell
    in `reach`; None when no cell of the way is in reach, or when none before it is open, as when the first is."""
    way = cells.line(cells.nearest_cell(*start), heading_to)
    near = _near_the_way(way, reach)
    last_open = None
    for cell in way:
        if any(disc.holds(cell) for disc in near):
            return last_open
        if cell not in terrain.blocked:
            last_open = cell
    return None


def _near_the_way(way: list[Cell], reach: list[Reach]) -> list[Reach]:
    """The discs that come into the box the way runs in: the only ones that may hold a cell of it."""
    xs, ys = [cell[0] for cell in way], [cell[1] for cell in way]
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    return [
        disc
        for disc in reach
        if left - disc.radius < disc.x < right + disc.radius and top - disc.radius < disc.y < bottom + disc.radius
    ]


def _range(record: EnemyRecord, world: World, tuning: Tuning) -> float:
    """An enemy's weapon range by the item table, or the one taken for a defense whose range is not known."""
    facts = world.items.get(record.type)
    return facts.range if facts is not None and facts.range is not None else tuning.coverage.unknown_range_cells


@dataclass(frozen=True)
class _Body:
    """How much of main stands at the staging cell: the share of its units within staging_body_cells of it, whether
    every long-range unit is within their reach of it, and how many long-range units main has."""

    share: float
    long_range_present: bool
    long_range: int

    def is_there(self, tuning: CombatTuning) -> bool:
        """Whether main is there to go on: staging_body_share of it, every long-range unit included."""
        return self.share >= tuning.staging_body_share and self.long_range_present


def _body(units: list[Unit], cell: Cell, tuning: CombatTuning) -> _Body:
    """How much of main's units stand at the staging cell."""
    near = sum(is_near(cell, unit.x, unit.y, tuning.staging_body_cells) for unit in units)
    long_range = [unit for unit in units if unit.type in SIEGE_TYPES]
    reach = tuning.staging_body_cells + LONG_RANGE_EXTRA_CELLS
    present = all(is_near(cell, unit.x, unit.y, reach) for unit in long_range)
    return _Body(near / len(units), present, len(long_range))


class ForwardStaging:
    """Where main stages on its way to an objective, whether it has gone on from there, and the units joining it at
    the staging cell. See the module's note."""

    def __init__(self) -> None:
        # The objective the staging is for, as the combat manager tells objectives apart; None before the first.
        self._objective: Hashable | None = None
        # The staging cell while one stands: from the cycle main is first sent there until it stages no more.
        self.cell: Cell | None = None
        # The tick main was first sent to the staging cell; None while it has none.
        self._sent_tick: int | None = None
        self._has_arrived = False
        # Whether main has gone on from the staging cell to its objective.
        self.is_engaged = False
        self.joining = JoinWait(JOIN_WAIT_SECONDS * TICKS_PER_SECOND)
        # Whether main staged in the cycle under way; a cycle with no staging ends it.
        self._is_followed = False

    def begin_cycle(self) -> None:
        """A cycle starts: main stages in it only if `follow` is called."""
        self._is_followed = False

    def end_cycle(self) -> None:
        """A cycle ends: a main that did not stage in it, called home, taken, pulled back or not sent forward, stages
        no more."""
        if not self._is_followed:
            self._reset(None)

    def stop(self) -> None:
        """Main is sent back to the staging point in this cycle, though it staged in it: its staging is over, the cell
        and the clock with it, so that it stages afresh when it next sets out."""
        self._reset(None)

    def follow(
        self,
        objective: Hashable,
        units: list[Unit],
        heading_to: Cell,
        aim: Cell,
        may_start: bool,
        world: World,
        ctx: MatchContext,
    ) -> Cell | None:
        """The staging cell main holds at in this cycle on its way to an objective (`objective` what tells it apart,
        `aim` its cell, `heading_to` the cell main is on its way to now); None when main goes on with its normal
        movement. A staging starts only with `may_start`; one under way goes on, a new objective starting over."""
        self._is_followed = True
        if objective != self._objective:
            self._reset(objective)
        if self.is_engaged:
            return None
        middle = centroid(units)
        reach = enemy_reach(aim, world, ctx.tuning)
        found = staging_cell(middle, heading_to, reach, ctx.terrain) if reach else None
        if self.cell is None:
            if not may_start or not _is_ahead(found, middle):
                return None
            self.cell, self._sent_tick = found, world.tick
        elif found is not None and _moves(self.cell, found, reach):
            self.cell = found
        return self._holds(units, middle, world, ctx.tuning.combat)

    def _holds(self, units: list[Unit], middle: Point, world: World, tuning: CombatTuning) -> Cell | None:
        """The staging cell while main holds there; None once it goes on, on its body or the timeout, with an
        `engage` event. Its arrival, the first cycle its centroid is within staging_body_cells of the cell, is an
        `arrival` event."""
        body = _body(units, self.cell, tuning)
        if not self._has_arrived and is_near(middle, self.cell[0], self.cell[1], tuning.staging_body_cells):
            self._has_arrived = True
            _log(ARRIVAL, self.cell, body, world.tick)
        if body.is_there(tuning):
            reason = BODY
        elif world.tick - self._sent_tick >= tuning.staging_timeout_seconds * TICKS_PER_SECOND:
            reason = TIMEOUT
        else:
            return self.cell
        self.is_engaged = True
        _log(ENGAGE, self.cell, body, world.tick, engaged_by=reason)
        return None

    def _reset(self, objective: Hashable | None) -> None:
        """Start over, for an objective (None for none): no staging cell, nothing engaged, no unit joining."""
        self._objective, self.cell, self._sent_tick = objective, None, None
        self._has_arrived = self.is_engaged = False
        self.joining = JoinWait(JOIN_WAIT_SECONDS * TICKS_PER_SECOND)


class JoinWait:
    """Since when the first of the units joining main has stood at the cell they meet at, and when they go on from
    it."""

    def __init__(self, wait_ticks: int) -> None:
        """A wait of `wait_ticks` at most after the first of them arrived."""
        self._wait_ticks = wait_ticks
        # The tick the first of those standing there arrived; None while none stands there.
        self._first_arrival: int | None = None

    def going_on(self, there: list[Unit], is_enough: bool, tick: int) -> list[Unit]:
        """The units standing at the meeting cell that go on in this cycle: all of them once `is_enough` holds or the
        wait has passed since the first arrived; none otherwise."""
        if not there:
            self._first_arrival = None
            return []
        if self._first_arrival is None:
            self._first_arrival = tick
        if not is_enough and tick - self._first_arrival < self._wait_ticks:
            return []
        self._first_arrival = None
        return there


def _moves(cell: Cell, found: Cell, reach: list[Reach]) -> bool:
    """Whether a staging cell found in a cycle replaces the one main has: that one is in the enemy's reach now, or the
    new one lies RESTAGE_CELLS or more from it."""
    return any(disc.holds(cell) for disc in reach) or cells.distance(cell, found) >= RESTAGE_CELLS


def _is_ahead(found: Cell | None, middle: Point) -> bool:
    """Whether a staging cell was found MIN_STAGING_CELLS or more from main's centroid, where main is not yet."""
    return found is not None and math.hypot(found[0] - middle[0], found[1] - middle[1]) >= MIN_STAGING_CELLS


def _log(phase: str, cell: Cell, body: _Body, tick: int, **more: str) -> None:
    """Write one `combat` event of reason `staging`."""
    data = {
        "tick": tick,
        "reason": STAGING,
        "phase": phase,
        "cell": list(cell),
        "body_share": round(body.share, _SHARE_DECIMALS),
        "long_range_present": body.long_range_present,
        "long_range": body.long_range,
        **more,
    }
    records.log(COMBAT_EVENT, data)
