"""The way main's attack sets out on (`[combat] flank_approach`), and whether the attack is out.

An army that walks the straight way into the defenses before the enemy's
base pays for every cell of it, while a side way a little longer may pass
them by. So when an attack sets out the script weighs three approaches from
main's centroid S to the objective O and sends the whole of main by one:

- **Direct**: the straight cells from S to O.
- **Left and right**: by a side cell, flank_side_cells off the line along its
  perpendicular, left and right as seen facing from S to O, at the point
  flank_lead_cells short of O (S itself when O is nearer). An approach by a
  side cell goes S to the side cell, then on to O.

A side is blocked (`edge`) when keeping its cell inside the map moves it more
than SIDE_SNAP_CELLS; (`impassable`) when its cell is impassable and no
passable cell lies within SIDE_SNAP_CELLS of it, which otherwise takes its
place; (`no_walk`) when a leg has no walk over passable cells within
WALK_LENGTH_SHARE of its straight length and WALK_EXTRA_STEPS more
(`Terrain.walk_steps`); and (`too_long`) when its walk is longer than
flank_max_length_ratio times the direct walk (the straight length when the
direct way has no walk). A terrain with no map picture has no impassable
cell.

An approach's exposure is what may shoot at main on it: each known ground
defense whose coverage (`coverage.reach`) holds a cell of its legs, at
defense_weight times its cost, and each enemy combat unit on the ground seen
within flank_army_seen_seconds standing within flank_army_cells of a cell of
them, at its cost. The direct approach is taken while no ground defense
covers it, and for an objective within flank_min_distance_cells. Otherwise
the open side of the lower exposure (the shorter walk on a tie) is taken when
its exposure is flank_better_share of the direct one's or less, else the
direct approach. Each weighing is one `combat` event of reason `approach`.

**The attack being out.** `AttackWatch` keeps whether main's attack is out
for the combat manager, which publishes it as `MatchContext.attack_out` with
its objective and the tick it set out (events `attack_out` and
`attack_home`): it sets out when the commander's forward gate opens, or when
main is sent forward as a whole by its plan, and ends when it is sent home
or base defense takes or calls it home, or once main's centroid comes back
within retreat_home_min_cells of `[commander]` of the base's center after
having been more than AWAY_CELLS from it. The approach is weighed when the
attack sets out and again once its objective moves more than
flank_recompute_cells; it holds until main's centroid has come within
PASSED_CELLS of its side cell.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, coverage, waypoints
from ucla_agent.games.redalert.fight import centroid, ground_defenses, worth
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.waypoints import WaypointRoute
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

COMBAT_EVENT = "combat"
# The reasons of the combat events written here.
APPROACH = "approach"
ATTACK_OUT = "attack_out"
ATTACK_HOME = "attack_home"
# The approaches, and why a side is blocked.
LEFT = "left"
DIRECT = "direct"
RIGHT = "right"
SIDES = (LEFT, RIGHT)
EDGE = "edge"
IMPASSABLE = "impassable"
NO_WALK = "no_walk"
TOO_LONG = "too_long"
# Why an attack ends: main is home, or it was sent home.
HOME = "home"
SENT_HOME = "sent_home"
# A side cell the map's edge moves farther than this is off the map; an impassable one gives way to the nearest
# passable cell this near it.
SIDE_SNAP_CELLS = 2
# A leg's walk is searched for up to this share of its straight length and these steps more.
WALK_LENGTH_SHARE = 1.5
WALK_EXTRA_STEPS = 6
# main's centroid this near a side cell has passed it.
PASSED_CELLS = waypoints.REACHED_CELLS
# An attack ends once main is back near the base after it has been this far from the base's center.
AWAY_CELLS = 20
# The user a side approach's `route` event names.
APPROACH_USER = "approach"
_FULL_HEALTH = 100


@dataclass(frozen=True)
class Approach:
    """The approach an attack takes: which, from where to where, and the side cell it goes by (None for direct)."""

    chosen: str
    start: Cell
    objective: Cell
    side_cell: Cell | None = None

    @property
    def legs(self) -> tuple[Cell, ...]:
        """The cells main goes to in turn: the side cell, if any, then the objective."""
        return (self.objective,) if self.side_cell is None else (self.side_cell, self.objective)


def is_kept(tuning: Tuning) -> bool:
    """Whether the attack being out is kept: one of the switches that read it is on."""
    return (
        tuning.combat.flank_approach
        or tuning.combat.commit_on_cross
        or tuning.scouting.canary_post
        or tuning.upkeep.canary_alarm
    )


def way_cells(start: Cell, legs: Sequence[Cell]) -> list[Cell]:
    """The cells of the straight legs from `start` through `legs` in turn."""
    found, here = [start], start
    for leg in legs:
        found.extend(cells.line(here, leg)[1:])
        here = leg
    return found


def covering(way: Sequence[Cell], world: World, ctx: MatchContext) -> list[EnemyRecord]:
    """The known ground defenses whose coverage holds a cell of the way."""
    return [
        defense
        for defense in ground_defenses(world)
        if _touches(way, (defense.x, defense.y), coverage.reach(defense.type, world.items, ctx.tuning.coverage))
    ]


def exposure(start: Cell, legs: Sequence[Cell], world: World, ctx: MatchContext) -> float:
    """What may shoot at main on an approach: the covering ground defenses at defense_weight times their cost, and
    the enemy combat units on the ground seen within flank_army_seen_seconds within flank_army_cells of it."""
    combat = ctx.tuning.combat
    way = way_cells(start, legs)
    defenses = sum(
        combat.defense_weight * worth(record.type, _FULL_HEALTH, world) for record in covering(way, world, ctx)
    )
    seen_ticks = combat.flank_army_seen_seconds * TICKS_PER_SECOND
    army = sum(
        worth(record.type, _FULL_HEALTH, world)
        for record in world.enemy_memory.values()
        if _is_ground_unit(record, world)
        and world.tick - record.last_seen_tick <= seen_ticks
        and _touches(way, (record.x, record.y), combat.flank_army_cells)
    )
    return defenses + army


def is_defended(start: Cell, legs: Sequence[Cell], world: World, ctx: MatchContext) -> bool:
    """Whether a ground defense's coverage holds a cell of an approach."""
    return bool(covering(way_cells(start, legs), world, ctx))


def side_cell(start: Cell, objective: Cell, side: str, world: World, ctx: MatchContext) -> tuple[Cell, str | None]:
    """The side cell of a side approach and why the side is blocked at it (`edge`, `impassable`), None while it is
    not."""
    combat = ctx.tuning.combat
    span = cells.distance(start, objective)
    lead = cells.toward(objective, start, combat.flank_lead_cells) if span > combat.flank_lead_cells else start
    heading = ((objective[0] - start[0]) / span, (objective[1] - start[1]) / span)
    # Facing from S to O with y growing downward, the left hand points along (dy, -dx).
    sign = 1 if side == LEFT else -1
    raw = cells.nearest_cell(
        lead[0] + sign * heading[1] * combat.flank_side_cells, lead[1] - sign * heading[0] * combat.flank_side_cells
    )
    bounds = world.map_bounds
    cell = raw if bounds is None else cells.clamped(raw, bounds)
    if cells.distance(raw, cell) > SIDE_SNAP_CELLS:
        return cell, EDGE
    if cell not in ctx.terrain.impassable:
        return cell, None
    near = _passable_near(cell, ctx.terrain.impassable)
    return (cell, IMPASSABLE) if near is None else (near, None)


def _passable_near(cell: Cell, impassable: frozenset[Cell]) -> Cell | None:
    """The passable cell within SIDE_SNAP_CELLS of a cell nearest it; None for none."""
    near = [
        (math.hypot(dx, dy), (cell[0] + dx, cell[1] + dy))
        for dx in range(-SIDE_SNAP_CELLS, SIDE_SNAP_CELLS + 1)
        for dy in range(-SIDE_SNAP_CELLS, SIDE_SNAP_CELLS + 1)
        if math.hypot(dx, dy) <= SIDE_SNAP_CELLS and (cell[0] + dx, cell[1] + dy) not in impassable
    ]
    return min(near)[1] if near else None


def walk_length(start: Cell, legs: Sequence[Cell], ctx: MatchContext) -> int | None:
    """The steps of an approach's walk, leg by leg, each searched within its step cap; None when a leg has none."""
    total, here = 0, start
    for leg in legs:
        cap = math.ceil(WALK_LENGTH_SHARE * cells.distance(here, leg)) + WALK_EXTRA_STEPS
        steps = ctx.terrain.walk_steps(here, leg, cap)
        if steps is None:
            return None
        total, here = total + steps, leg
    return total


def approaches(start: Cell, objective: Cell, world: World, ctx: MatchContext) -> Approach:
    """The approach an attack from `start` on `objective` takes (see the module's note), with its `approach` event."""
    combat = ctx.tuning.combat
    direct = Approach(DIRECT, start, objective)
    exposures = {DIRECT: exposure(start, direct.legs, world, ctx)}
    if cells.is_within(start, objective, combat.flank_min_distance_cells):
        _log_approach(direct, exposures, {}, world)
        return direct
    direct_walk = walk_length(start, direct.legs, ctx)
    longest = combat.flank_max_length_ratio * (cells.distance(start, objective) if direct_walk is None else direct_walk)
    open_sides, blocked = _open_sides(start, objective, longest, world, ctx)
    exposures.update({side: exposure(start, (cell, objective), world, ctx) for side, (cell, _) in open_sides.items()})
    chosen = direct
    if open_sides and is_defended(start, direct.legs, world, ctx):
        best = min(open_sides, key=lambda side: (exposures[side], open_sides[side][1]))
        if exposures[best] <= combat.flank_better_share * exposures[DIRECT]:
            chosen = Approach(best, start, objective, open_sides[best][0])
    _log_approach(chosen, exposures, blocked, world)
    return chosen


def _open_sides(
    start: Cell, objective: Cell, longest: float, world: World, ctx: MatchContext
) -> tuple[dict[str, tuple[Cell, int]], dict[str, str]]:
    """The sides open to an approach, each with its side cell and walk, and the blocked ones with why: a walk longer
    than `longest` is too long."""
    open_sides: dict[str, tuple[Cell, int]] = {}
    blocked: dict[str, str] = {}
    for side in SIDES:
        cell, reason = side_cell(start, objective, side, world, ctx)
        walk = None if reason is not None else walk_length(start, (cell, objective), ctx)
        if reason is None:
            reason = NO_WALK if walk is None else (TOO_LONG if walk > longest else None)
        if reason is None:
            open_sides[side] = (cell, walk)
        else:
            blocked[side] = reason
    return open_sides, blocked


class AttackWatch:
    """Whether main's attack is out, since when and on what objective, and the approach it takes. See the module's
    note."""

    def __init__(self) -> None:
        self.approach: Approach | None = None
        # The tick the attack set out; None while none is out.
        self.since: int | None = None
        self.objective: Cell | None = None
        # Whether main has passed the side cell of its approach, and whether it has been far from the base.
        self.has_passed = False
        self._was_away = False

    @property
    def is_out(self) -> bool:
        """Whether an attack is out."""
        return self.since is not None

    def set_out(self, objective: Cell, units: Sequence[Unit], world: World, ctx: MatchContext) -> None:
        """An attack sets out on an objective, with its `attack_out` event and its approach weighed; nothing while one
        is out."""
        if self.is_out or not units:
            return
        self.since, self._was_away = world.tick, False
        self._route(objective, units, world, ctx)
        _log(ATTACK_OUT, world.tick, objective=list(objective))

    def aim(self, objective: Cell, units: Sequence[Unit], world: World, ctx: MatchContext) -> None:
        """Follow the objective of the attack out: the approach weighed afresh once it moved more than
        flank_recompute_cells."""
        if not self.is_out or not units or self.objective is None:
            return
        if not cells.is_within(objective, self.objective, ctx.tuning.combat.flank_recompute_cells):
            self._route(objective, units, world, ctx)

    def end(self, reason: str, world: World) -> None:
        """The attack is over, with its `attack_home` event; nothing while none is out."""
        if not self.is_out:
            return
        _log(ATTACK_HOME, world.tick, why=reason)
        self.since, self.objective, self.approach, self.has_passed = None, None, None, False

    def follow(self, units: Sequence[Unit], world: World, ctx: MatchContext, passed: bool = False) -> None:
        """Bring the attack out up to date with main's units: the side cell passed once main's centroid is within
        PASSED_CELLS of it (or `passed` says main's steps have passed it), and the attack over once main is home
        again after it was more than AWAY_CELLS from the base."""
        if not self.is_out or not units or world.base_center is None:
            return
        self.pass_side(units, passed)
        middle = cells.nearest_cell(*centroid(units))
        if not cells.is_within(middle, world.base_center, AWAY_CELLS):
            self._was_away = True
        elif self._was_away and cells.is_within(middle, world.base_center, ctx.tuning.commander.retreat_home_min_cells):
            self.end(HOME, world)

    def pass_side(self, units: Sequence[Unit], passed: bool = False) -> None:
        """Note main past its approach's side cell: its centroid within PASSED_CELLS of it, or `passed` saying main's
        steps have passed it."""
        side = None if self.approach is None else self.approach.side_cell
        if side is None or self.has_passed or not units:
            return
        self.has_passed = passed or cells.is_within(cells.nearest_cell(*centroid(units)), side, PASSED_CELLS)

    def via(self, cell: Cell, tuning: Tuning) -> Cell | None:
        """The side cell an order on `cell` goes by: that of a side approach not passed yet, for a cell within
        flank_recompute_cells of its objective; None otherwise."""
        approach = self.approach
        if approach is None or approach.side_cell is None or self.has_passed:
            return None
        return (
            approach.side_cell
            if cells.is_within(cell, approach.objective, tuning.combat.flank_recompute_cells)
            else None
        )

    def publish(self, ctx: MatchContext) -> None:
        """Publish the attack out on the match context, with the side cell main is still to pass."""
        ctx.attack_out, ctx.attack_out_since, ctx.attack_objective = self.is_out, self.since, self.objective
        approach = self.approach
        ctx.attack_side_cell = None if approach is None or self.has_passed else approach.side_cell

    def _route(self, objective: Cell, units: Sequence[Unit], world: World, ctx: MatchContext) -> None:
        """Weigh the approach on an objective from main's centroid, with a `route` event of user `approach` for a side
        approach; none with flank_approach off."""
        self.objective, self.has_passed = objective, False
        if not ctx.tuning.combat.flank_approach:
            self.approach = None
            return
        start = cells.nearest_cell(*centroid(units))
        self.approach = approaches(start, objective, world, ctx)
        side = self.approach.side_cell
        if side is not None:
            length = cells.distance(start, side) + cells.distance(side, objective)
            waypoints.log_route(APPROACH_USER, WaypointRoute(start, objective, (side,), length), world.tick)


def side_for(cell: Cell, ctx: MatchContext) -> Cell | None:
    """The side cell published for the attack out (see `AttackWatch.publish`) that an order on `cell` goes by: for a
    cell within flank_recompute_cells of the attack's objective; None otherwise."""
    side, objective = ctx.attack_side_cell, ctx.attack_objective
    if side is None or objective is None:
        return None
    return side if cells.is_within(cell, objective, ctx.tuning.combat.flank_recompute_cells) else None


def _touches(way: Sequence[Cell], point: Cell, reach: float) -> bool:
    """Whether a point lies within `reach` of a cell of the way."""
    return any(cells.is_within(cell, point, reach) for cell in way)


def _is_ground_unit(record: EnemyRecord, world: World) -> bool:
    """Whether a remembered enemy is a combat unit on the ground."""
    return is_combat_unit(record) and not world.items.damage.get(record.type).air


def _log_approach(chosen: Approach, exposures: dict[str, float], blocked: dict[str, str], world: World) -> None:
    """Write the `approach` event of one weighing."""
    _log(
        APPROACH,
        world.tick,
        chosen=chosen.chosen,
        start=list(chosen.start),
        objective=list(chosen.objective),
        side_cell=None if chosen.side_cell is None else list(chosen.side_cell),
        exposure={side: round(value) for side, value in exposures.items()},
        blocked=blocked,
    )


def _log(reason: str, tick: int, **data: object) -> None:
    """Write one `combat` event of a reason."""
    records.log(COMBAT_EVENT, {"tick": tick, "reason": reason, **data})
