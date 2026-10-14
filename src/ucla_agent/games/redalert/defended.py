"""No fight under enemy guns: main holds off from defenses it cannot beat, takes another attack point, or sieges.

Against an enemy army that stands under its base defenses, crushing the
infantry and rushing the artillery wins the fight in the open and loses it
under the guns. So with `defended_redirect` of `[combat]` on, the combat
manager weighs main's objective before it sends main there, the figures those
of `[combat]`:

- **The check.** Main's objective (the cell of the model commander's forward
  action, or the plan's objective) is defended while a known enemy ground
  defense (`fight.ground_defenses`: never the AA gun or the SAM site) has it
  within its coverage: its weapon range (`command_targets.range_cells`) and
  defended_margin_cells. Main is then weighed there (`fight.weigh`): every unit
  of it against the enemy units that count in a fight within contact_cells of
  the objective and those defenses, each at defense_weight times its cost.
- **Hold off.** Below defended_engage_ratio main advances into the coverage of
  no known ground defense. Of its orders in the cycle, but its emergencies, a
  move or an attack-move to a covered cell goes instead to the first cell out
  of the coverage on the way back from it toward the units, and an attack on a
  target in the coverage is kept only for the units already within their
  weapon range of it, which fire from where they stand. Every unit of main is
  given an order of the verdict, so that a march under way stops at the
  coverage's edge.
- **Redirect.** Main takes the best attack point instead: a known enemy
  construction yard, refinery or production building, or an enemy harvester
  seen in the last 20 s within 3 cells of ore (`overextended`), that no known
  ground defense covers; and while main has artillery or V2s, a lone defense,
  one with no enemy combat unit within `LONE_DEFENSE_CELLS` of it. They rank by
  what they are worth (cost times health) over their distance from main's
  centroid, the best first. One whose straight way from main crosses covered
  ground is taken by the way the graph of waypoints gives (with `waypoints` of
  [routing] on, see `waypoints`), and passed over without one. Main goes on an
  uncovered point as a strike does (see `strike`): only once gathered_share of
  it stands within gathered_cells of its rally cell (its centroid, or the first
  cell out of the coverage on the way to the base), the units away from that
  cell gathering there meanwhile; then it moves to each cell of the way in
  turn, on to the next once its centroid is within `waypoints.REACHED_CELLS` of
  one, and attack-moves on the point, attacking the target in sight once within
  `ATTACK_CELLS` of it. The redirect is abandoned, the point resting for
  `REST_SECONDS`, once main's own fight (`fight.estimate`) or its ratio at the
  point (`fight.ratio_against_target` against what stands with it, see
  `command_targets.field_weighed_with`) falls under strike_abandon_ratio of
  `[plan_rules]`; it ends once its target has left the enemy memory or is
  covered.
- **Shelling.** On a lone defense the long-range units stand at its range and
  defended_margin_cells, on the way from it toward main and out of every other
  known defense's coverage, and attack it from there while it is in sight.
- **Siege fallback.** With no attack point the long-range units stand just
  beyond the coverage of every known ground defense, on the way from the
  objective toward main, and fire on what comes into their firing band, falling
  back from what closes in (`siege.Siege` with no screen and no advance).
- **The screen.** In the siege and the shelling the other units hold
  `SCREEN_BEHIND_CELLS` cells behind the long-range units' front, toward the
  base, a cell each; those in contact fire at what is within their weapon range
  (the combat manager's hold fire). Every vision_dart_seconds one fast unit, a
  jeep, else the fastest tank, darts to the middle of the front, at the
  coverage's edge (defended_margin_cells outside the defenses' range), to see
  what the long-range units fire on, and then goes back to its cell.

The stand cells lie across the way from the objective toward main's centroid
as the siege or shelling starts, slot_spacing_cells of `[siege]` apart, and do
not move while it lasts unless the known defenses change. Each
redirect, siege fallback and shelling, as it starts, is a `combat` event with
the reason `defended_redirect`, `defended_siege` or `defense_shelling`. With no
defense covering main's objective, or at defended_engage_ratio and above, the
fight and the crush runs go as ever. A strike, a crush run, base defense's
override and recall, and the retreats come first (see `combat`).

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable, Sequence

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, overextended, siege, waypoints
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.command_targets import field_weighed_with, ground_enemies, range_cells
from ucla_agent.games.redalert.family import HARVESTER, REFINERY
from ucla_agent.games.redalert.fight import (
    Point,
    centroid,
    estimate,
    ground_defenses,
    ratio_against_target,
    weigh,
    worth,
)
from ucla_agent.games.redalert.finish_target import PRODUCTION_TYPES
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

COMBAT_EVENT = "combat"
# The kinds of verdict, and the reasons of their events.
REDIRECT = "defended_redirect"
SIEGE = "defended_siege"
SHELLING = "defense_shelling"
# A defense with no enemy combat unit within this many cells of it is a lone one, an attack point of its own.
LONE_DEFENSE_CELLS = 10
# The screen holds this many cells behind the long-range units' front, toward the base: the middle of 2 to 4.
SCREEN_BEHIND_CELLS = 3
# On a redirect, a unit this near its target in sight attacks it rather than attack-moving to its cell, as on a strike.
ATTACK_CELLS = 6
# A redirect point abandoned is no attack point for this long.
REST_SECONDS = 30
# The enemy buildings that are attack points while no defense covers them: the yard, the production, the refinery.
POINT_BUILDINGS = PRODUCTION_TYPES | {REFINERY}
# A unit within this many cells of its stand cell stands at it.
AT_CELL_CELLS = 1.5
# The fast units that dart for vision, the jeep first, then the tanks, the fastest first.
DART_FIRST = "jeep"
TANK_TYPES = frozenset({"1tnk", "2tnk", "3tnk", "4tnk"})
# The front is sought from the objective outward this many cells at a time, and no farther than the most.
_STEP_CELLS = 0.5
_FRONT_SEARCH_CELLS = 48
_RATIO_DECIMALS = 2


@dataclass(frozen=True)
class AttackPoint:
    """An enemy main may go on instead of a defended objective: its record, whether it is a lone defense, which
    main shells, and the cells of its way through the graph of waypoints short of it (none for the straight way)."""

    target: EnemyRecord
    is_defense: bool
    legs: tuple[Cell, ...] = ()


@dataclass(frozen=True)
class Verdict:
    """What main does in a cycle while its objective is defended beyond what it can beat.

    `kind` is `REDIRECT`, `SHELLING` or `SIEGE`. On a redirect `cell` is where
    main goes now: its rally cell while it gathers (`is_gathering`), else the
    next cell of its way, else its target's. In a shelling and a siege `cell`
    is the middle of the long-range units' front and `heading` the unit vector
    from the front back toward main.
    """

    kind: str
    objective: Cell
    ratio: float
    target: EnemyRecord | None = None
    cell: Cell | None = None
    is_gathering: bool = False
    front: Point | None = None
    heading: Point | None = None


def reach(defense: EnemyRecord, world: World, ctx: MatchContext) -> float:
    """How far a defense's coverage reaches: its weapon range and defended_margin_cells."""
    return range_cells(defense.type, world, ctx) + ctx.tuning.combat.defended_margin_cells


def coverage(world: World, ctx: MatchContext) -> list[tuple[Cell, float]]:
    """Each known enemy ground defense's cell and how far its coverage reaches."""
    return [((defense.x, defense.y), reach(defense, world, ctx)) for defense in ground_defenses(world)]


def is_covered(point: Point, covered: Iterable[tuple[Cell, float]]) -> bool:
    """Whether a point lies inside the coverage of any of the defenses given: nearer than its reach."""
    return any(math.dist(point, cell) < reaches for cell, reaches in covered)


def covering(objective: Cell, world: World, ctx: MatchContext) -> list[EnemyRecord]:
    """The known enemy ground defenses whose coverage takes in the objective."""
    return [
        defense
        for defense in ground_defenses(world)
        if math.dist(objective, _cell(defense)) < reach(defense, world, ctx)
    ]


def ratio_at(
    units: Sequence[Unit], objective: Cell, defenses: Sequence[EnemyRecord], world: World, ctx: MatchContext
) -> float:
    """Main weighed at its objective (`fight.weigh`): every unit of it against the enemy units that count in a fight
    within contact_cells of the objective and the defenses given; ratio_without_enemy when that is worth nothing."""
    tuning = ctx.tuning
    near = [
        record
        for record in ground_enemies(world)
        if cells.is_within(objective, _cell(record), tuning.combat.contact_cells)
    ]
    own, enemy = weigh(list(units), near, world, tuning, defenses)
    return own / enemy if enemy > 0 else tuning.combat.ratio_without_enemy


def defended_ratio(units: Sequence[Unit], objective: Cell, world: World, ctx: MatchContext) -> float | None:
    """Main's ratio at its objective while known ground defenses cover it and it is under defended_engage_ratio there;
    None otherwise."""
    defenses = covering(objective, world, ctx)
    if not defenses:
        return None
    ratio = ratio_at(units, objective, defenses, world, ctx)
    return ratio if ratio < ctx.tuning.combat.defended_engage_ratio else None


def attack_points(
    units: Sequence[Unit], has_long_range: bool, world: World, ctx: MatchContext, resting: Iterable[int] = ()
) -> list[AttackPoint]:
    """The attack points main may take, the best first: the uncovered yards, production buildings, refineries and
    harvesters at a field, and with long-range units the lone defenses, by worth over distance from main's centroid;
    of those not resting, only the ones main can reach (see the module's note)."""
    middle = centroid(units)
    covered = coverage(world, ctx)
    rested = frozenset(resting)
    ranked = sorted(
        (entry for entry in _candidates(has_long_range, covered, world, ctx) if entry[0].id not in rested),
        key=lambda entry: (-_value(entry[0], middle, world), entry[0].id),
    )
    start = cells.nearest_cell(*middle)
    points = (_reached(record, is_defense, start, covered, world, ctx) for record, is_defense in ranked)
    return [point for point in points if point is not None]


def _candidates(
    has_long_range: bool, covered: list[tuple[Cell, float]], world: World, ctx: MatchContext
) -> list[tuple[EnemyRecord, bool]]:
    """The enemies of the kinds of attack point that no coverage takes in, and with long-range units the lone
    defenses, each with whether it is a defense."""
    found = [
        (record, False)
        for record in world.enemy_memory.values()
        if _is_point(record, world, ctx) and not is_covered(_cell(record), covered)
    ]
    if has_long_range:
        found.extend((defense, True) for defense in ground_defenses(world) if is_lone(defense, world))
    return found


def is_lone(defense: EnemyRecord, world: World) -> bool:
    """Whether no enemy combat unit that counts in a fight stands within LONE_DEFENSE_CELLS of a defense."""
    cell = _cell(defense)
    return not any(cells.is_within(cell, _cell(record), LONE_DEFENSE_CELLS) for record in ground_enemies(world))


def held_off(intents: list[Intent], world: World, ctx: MatchContext) -> list[Intent]:
    """Main's orders of a cycle with none of them into covered ground: a move or an attack-move to a covered cell goes
    to the first cell out of the coverage on the way back toward its units, dropped when there is none, and an attack
    on a target in the coverage is kept for the units within their weapon range of it alone. Emergencies are kept as
    they are."""
    covered = coverage(world, ctx)
    if not covered:
        return intents
    kept = []
    for intent in intents:
        held = intent if intent.priority == Priority.EMERGENCY else _held(intent, covered, world)
        if held is not None:
            kept.append(held)
    return kept


class DefendedWatch:
    """Main's verdict while its objective is defended, and what it remembers of it from cycle to cycle. See the module's
    note."""

    def __init__(self) -> None:
        self._siege = siege.Siege()
        # The objective of the verdict in force, its kind and its attack point; None while there is none.
        self._objective: Cell | None = None
        self._kind: str | None = None
        self._point: AttackPoint | None = None
        # Whether the redirect has set out from its rally cell, and the cells of its way it has still to pass.
        self._set_out = False
        self._legs: list[Cell] = []
        # The unit vector from the objective toward main as the siege or the shelling started.
        self._heading: Point | None = None
        # Target id -> the tick until which an abandoned redirect point is no attack point.
        self._resting: dict[int, int] = {}
        # The long-range units whose last order was the siege's fire or fall-back.
        self._firing: set[int] = set()
        # The unit darting for vision while it is on its way out, and the tick the last dart set out (or the siege
        # started).
        self._dart: int | None = None
        self._dart_tick: int | None = None

    @property
    def kind(self) -> str | None:
        """The kind of the verdict in force; None while there is none."""
        return self._kind

    def stop(self) -> None:
        """Forget the verdict in force: main's objective is no longer defended beyond what it can beat."""
        self._objective, self._kind, self._point, self._heading = None, None, None, None
        self._set_out, self._legs, self._firing, self._dart, self._dart_tick = False, [], set(), None, None

    def follow(
        self, units: list[Unit], long_range: list[Unit], objective: Cell, world: World, ctx: MatchContext
    ) -> Verdict | None:
        """The verdict of the cycle on main (`units`, its artillery and V2s `long_range`) going on `objective`; None
        while no known defense covers it or main is worth defended_engage_ratio there."""
        self._resting = {target: until for target, until in self._resting.items() if until > world.tick}
        ratio = defended_ratio(units, objective, world, ctx)
        if ratio is None:
            self.stop()
            return None
        if objective != self._objective:
            self.stop()
            self._objective = objective
        if self._point is not None and not self._still_holds(self._point, units, long_range, world, ctx):
            self._point, self._set_out, self._legs = None, False, []
        if self._point is None:
            self._choose(units, long_range, objective, ratio, world, ctx)
        if self._kind == REDIRECT:
            return self._redirect(units, objective, ratio, world, ctx)
        return self._stand_off(units, objective, ratio, world, ctx)

    def orders(
        self, verdict: Verdict, long_range: list[Unit], screen: list[Unit], world: World, ctx: MatchContext
    ) -> list[Intent]:
        """The orders that carry out a verdict for main's units given: its long-range units and the rest, its screen."""
        units = [*long_range, *screen]
        if verdict.kind == REDIRECT:
            if verdict.is_gathering:
                return _gather_at(units, verdict.cell, ctx)
            return _go_on(units, verdict.cell, verdict.target)
        if verdict.front is None:
            return _gather_at(units, _rally_cell(units, world, ctx), ctx)
        stands = self._long_range_orders(verdict, long_range, world, ctx)
        return [*stands, *self._screen_orders(verdict, screen, world, ctx)]

    def _choose(
        self, units: list[Unit], long_range: list[Unit], objective: Cell, ratio: float, world: World, ctx: MatchContext
    ) -> None:
        """Take the best attack point, or the siege fallback with none; a new shelling or siege writes its event."""
        points = attack_points(units, bool(long_range), world, ctx, self._resting)
        point = points[0] if points else None
        kind = SIEGE if point is None else (SHELLING if point.is_defense else REDIRECT)
        if kind == SIEGE and self._kind == SIEGE:
            return
        self._kind, self._point, self._legs, self._set_out = (
            kind,
            point,
            [] if point is None else list(point.legs),
            False,
        )
        if kind == REDIRECT:
            return
        aim = objective if point is None else _cell(point.target)
        self._heading = _heading_from(aim, centroid(units), world)
        self._dart, self._dart_tick, self._firing = None, world.tick, set()
        _log_stand_off(kind, point, objective, ratio, _front(aim, self._heading, world, ctx), world, ctx)

    def _still_holds(
        self, point: AttackPoint, units: list[Unit], long_range: list[Unit], world: World, ctx: MatchContext
    ) -> bool:
        """Whether main keeps its attack point: the target is still known, a redirect's still uncovered and not
        abandoned, a lone defense still lone while main has long-range units to shell it with."""
        target = world.enemy_memory.get(point.target.id)
        if target is None:
            return False
        if point.is_defense:
            return bool(long_range) and is_lone(target, world)
        if is_covered(_cell(target), coverage(world, ctx)):
            return False
        if self._set_out and _is_lost(units, target, world, ctx):
            self._resting[target.id] = world.tick + REST_SECONDS * TICKS_PER_SECOND
            return False
        return True

    def _redirect(self, units: list[Unit], objective: Cell, ratio: float, world: World, ctx: MatchContext) -> Verdict:
        """The redirect's verdict: gathering at the rally cell until gathered_share of main stands there, then on to
        the next cell of the way, else the target; setting out writes its event."""
        target = world.enemy_memory[self._point.target.id]
        if not self._set_out:
            rally = _rally_cell(units, world, ctx)
            if not _is_gathered(units, rally, ctx):
                return Verdict(REDIRECT, objective, ratio, target, rally, is_gathering=True)
            self._set_out = True
            event = {
                "tick": world.tick,
                "reason": REDIRECT,
                "objective": list(objective),
                "ratio": round(ratio, _RATIO_DECIMALS),
                "target": target.id,
                "target_type": target.type,
                "cell": list(_cell(target)),
                "legs": [list(leg) for leg in self._legs],
                "units": sorted(unit.id for unit in units),
            }
            records.log(COMBAT_EVENT, event)
        middle = centroid(units)
        while self._legs and cells.is_within(cells.nearest_cell(*middle), self._legs[0], waypoints.REACHED_CELLS):
            self._legs.pop(0)
        return Verdict(REDIRECT, objective, ratio, target, self._legs[0] if self._legs else _cell(target))

    def _stand_off(self, units: list[Unit], objective: Cell, ratio: float, world: World, ctx: MatchContext) -> Verdict:
        """The verdict of a shelling or a siege: the front the long-range units stand at, the same way as it started."""
        target = None if self._point is None else world.enemy_memory[self._point.target.id]
        aim = objective if target is None else _cell(target)
        if self._heading is None:
            self._heading = _heading_from(aim, centroid(units), world)
        front = _front(aim, self._heading, world, ctx)
        return Verdict(self._kind, objective, ratio, target, None, front=front, heading=self._heading)

    def _long_range_orders(
        self, verdict: Verdict, long_range: list[Unit], world: World, ctx: MatchContext
    ) -> list[Intent]:
        """The long-range units' orders: on a lone defense an attack on it from where they stand once at their stand
        cells, else a move there; in a siege the siege's fire and fall-backs, and a move to its stand cell for each
        unit the siege gives nothing and keeps busy no more."""
        if not long_range:
            return []
        stands = _line_cells(long_range, verdict.front, verdict.heading, world, ctx)
        if verdict.kind == SHELLING:
            return _given(_shell(unit, verdict.target, stands[unit.id], world, ctx) for unit in long_range)
        return self._siege_fire(long_range, stands, world, ctx)

    def _siege_fire(
        self, long_range: list[Unit], stands: dict[int, Cell], world: World, ctx: MatchContext
    ) -> list[Intent]:
        """The siege's fire and fall-backs, with no screen and no advance, and a move to its stand cell for each unit
        the siege gives nothing in this cycle and keeps busy no more: one whose last order was the siege's stands idle.
        """
        fire = self._siege.orders(long_range, [], None, False, world, ctx.tuning)
        fired = {unit_id for intent in fire for unit_id in intent.order["units"]}
        busy = {unit.id for unit in long_range if unit.id in self._firing and not unit.idle}
        self._firing = fired | busy
        moves = (
            _move_to(unit, stands[unit.id], Priority.CONTACT) for unit in long_range if unit.id not in self._firing
        )
        return [*fire, *_given(moves)]

    def _screen_orders(self, verdict: Verdict, screen: list[Unit], world: World, ctx: MatchContext) -> list[Intent]:
        """The screen's orders: each unit to its cell `SCREEN_BEHIND_CELLS` behind the front toward the base, but the
        one darting for vision, sent to the front's middle every vision_dart_seconds."""
        if not screen:
            return []
        dart = self._follow_dart(verdict, screen, world, ctx)
        behind = _toward_base(verdict.front, verdict.heading, world)
        middle = (
            verdict.front[0] + behind[0] * SCREEN_BEHIND_CELLS,
            verdict.front[1] + behind[1] * SCREEN_BEHIND_CELLS,
        )
        # The unit darting keeps its cell on the line, so that the others' cells do not move while it is away.
        places = _line_cells(screen, middle, behind, world, ctx)
        moves = (_move_to(unit, places[unit.id], Priority.SCREEN) for unit in screen if unit.id != self._dart)
        return [*dart, *_given(moves)]

    def _follow_dart(self, verdict: Verdict, screen: list[Unit], world: World, ctx: MatchContext) -> list[Intent]:
        """Bring the vision dart up to date: the unit darting comes back once at the front's middle or standing idle
        there a second after it set out; a new one sets out vision_dart_seconds after the last; its move, when it sets
        out in this cycle."""
        tick, present = world.tick, {unit.id: unit for unit in screen}
        front = _clamped(cells.nearest_cell(*verdict.front), world)
        darting = present.get(self._dart) if self._dart is not None else None
        if darting is not None:
            has_left = tick - self._dart_tick >= TICKS_PER_SECOND
            if cells.is_within((darting.x, darting.y), front, AT_CELL_CELLS) or (has_left and darting.idle):
                self._dart = None
            return []
        self._dart = None
        if tick - self._dart_tick < ctx.tuning.combat.vision_dart_seconds * TICKS_PER_SECOND:
            return []
        runner = _dart_unit(screen, world)
        if runner is None:
            return []
        self._dart, self._dart_tick = runner.id, tick
        return [Intent(Priority.SCREEN, {"cmd": "move", "units": [runner.id], "to": list(front)})]


def _log_stand_off(
    kind: str,
    point: AttackPoint | None,
    objective: Cell,
    ratio: float,
    front: Point | None,
    world: World,
    ctx: MatchContext,
) -> None:
    """Write the event of a shelling (its defense) or a siege fallback (the defenses that cover the objective) as it
    starts, with the middle of its front."""
    event = {"tick": world.tick, "reason": kind, "objective": list(objective), "ratio": round(ratio, _RATIO_DECIMALS)}
    if point is None:
        event["defenses"] = sorted(defense.id for defense in covering(objective, world, ctx))
    else:
        event.update(target=point.target.id, target_type=point.target.type, cell=list(_cell(point.target)))
    event["front"] = None if front is None else list(cells.nearest_cell(*front))
    records.log(COMBAT_EVENT, event)


def _is_point(record: EnemyRecord, world: World, ctx: MatchContext) -> bool:
    """Whether an enemy is of the kinds of attack point: a yard, a production building, a refinery, or a harvester seen
    lately within `overextended.FIELD_CELLS` of ore."""
    if record.is_building:
        return record.type in POINT_BUILDINGS
    return record.type == HARVESTER and overextended.is_seen_lately(record, world) and _at_field(record, ctx)


def _at_field(record: EnemyRecord, ctx: MatchContext) -> bool:
    cell = _cell(record)
    return any(
        cells.is_within(cell, patch.nearest_cell(cell), overextended.FIELD_CELLS) for patch in ctx.terrain.ore_patches
    )


def _value(record: EnemyRecord, middle: Point, world: World) -> float:
    """What an attack point is worth over its distance from main's centroid, a cell at the least."""
    return worth(record.type, record.hp, world) / max(1.0, math.dist(middle, _cell(record)))


def _reached(
    record: EnemyRecord,
    is_defense: bool,
    start: Cell,
    covered: list[tuple[Cell, float]],
    world: World,
    ctx: MatchContext,
) -> AttackPoint | None:
    """An attack point with its way from `start`: straight while no cell of that line is covered (a lone defense's own
    coverage aside), else by the graph of waypoints with `waypoints` of [routing] on; None without a way."""
    goal = _cell(record)
    others = [(cell, reaches) for cell, reaches in covered if not (is_defense and cell == goal)]
    line = cells.line(start, goal)
    if not any(is_covered(cell, others) for cell in line):
        return AttackPoint(record, is_defense)
    if is_defense or not ctx.tuning.routing.waypoints:
        return None
    route = ctx.waypoints.route(start, goal)
    return None if route is None else AttackPoint(record, is_defense, tuple(route.legs[:-1]))


def _is_lost(units: list[Unit], target: EnemyRecord, world: World, ctx: MatchContext) -> bool:
    """Whether a redirect is lost: main's own fight, or its ratio at the target against what stands with it, under
    strike_abandon_ratio of [plan_rules]."""
    least = ctx.tuning.plan_rules.strike_abandon_ratio
    around = estimate(units, world, ctx.tuning)
    if around is not None and around.ratio < least:
        return True
    support, defenses = field_weighed_with(_cell(target), world, ctx)
    return ratio_against_target(units, (target,), world, ctx.tuning, support, defenses) < least


def _rally_cell(units: list[Unit], world: World, ctx: MatchContext) -> Cell:
    """Where main gathers before a redirect: the cell of its centroid, or the first cell out of every coverage on the
    way from there to the base."""
    here = cells.nearest_cell(*centroid(units))
    covered = coverage(world, ctx)
    if not is_covered(here, covered) or world.base_center is None:
        return here
    return next((cell for cell in cells.line(here, world.base_center) if not is_covered(cell, covered)), here)


def _is_gathered(units: list[Unit], cell: Cell, ctx: MatchContext) -> bool:
    """Whether at least gathered_share of the units stand within gathered_cells of a cell."""
    tuning = ctx.tuning.combat
    there = [unit for unit in units if cells.is_within((unit.x, unit.y), cell, tuning.gathered_cells)]
    return len(there) >= tuning.gathered_share * len(units)


def _gather_at(units: list[Unit], cell: Cell, ctx: MatchContext) -> list[Intent]:
    """A move to a cell for the units not standing idle within gathered_cells of it: those still on their way
    elsewhere stop going there."""
    reach_cells = ctx.tuning.combat.gathered_cells
    going = [unit.id for unit in units if not (unit.idle and cells.is_within((unit.x, unit.y), cell, reach_cells))]
    return [Intent(Priority.MOVE, {"cmd": "move", "units": going, "to": list(cell)})] if going else []


def _go_on(units: list[Unit], cell: Cell, target: EnemyRecord) -> list[Intent]:
    """The redirect under way: a move to the next cell of its way; on its last leg an attack on the target in sight for
    the units within ATTACK_CELLS of it and an attack-move to its cell for the others."""
    goal = _cell(target)
    if cell != goal:
        return [Intent(Priority.MOVE, {"cmd": "move", "units": [unit.id for unit in units], "to": list(cell)})]
    near = [unit.id for unit in units if target.visible and cells.is_within((unit.x, unit.y), goal, ATTACK_CELLS)]
    going = [unit.id for unit in units if unit.id not in near]
    attack = [Intent(Priority.MOVE, {"cmd": "attack", "units": near, "target": target.id})] if near else []
    move = [Intent(Priority.MOVE, {"cmd": "attack_move", "units": going, "to": list(goal)})] if going else []
    return [*attack, *move]


def _shell(unit: Unit, defense: EnemyRecord, stand: Cell, world: World, ctx: MatchContext) -> Intent | None:
    """A long-range unit's order on a lone defense: an attack on it in sight once the unit stands at its stand cell, or
    out of the coverage within its firing band of it; else a move to the stand cell."""
    here, aim = (unit.x, unit.y), _cell(defense)
    is_out = not is_covered(here, coverage(world, ctx))
    in_band = siege.firing_band(unit.type, aim, world, ctx.tuning.siege).holds(math.dist(here, aim))
    if defense.visible and in_band and (is_out or cells.is_within(here, stand, AT_CELL_CELLS)):
        return Intent(Priority.CONTACT, {"cmd": "attack", "units": [unit.id], "target": defense.id})
    return _move_to(unit, stand, Priority.CONTACT)


def _move_to(unit: Unit, cell: Cell, priority: Priority) -> Intent | None:
    """A move to a cell for a unit not standing at it already."""
    if cells.is_within((unit.x, unit.y), cell, AT_CELL_CELLS) and unit.idle:
        return None
    return Intent(priority, {"cmd": "move", "units": [unit.id], "to": list(cell)})


def _dart_unit(screen: list[Unit], world: World) -> Unit | None:
    """The unit that darts for vision: a jeep, else the fastest tank; None with neither."""
    jeeps = [unit for unit in screen if unit.type == DART_FIRST]
    tanks = [unit for unit in screen if unit.type in TANK_TYPES]
    runners = jeeps or tanks
    return min(runners, key=lambda unit: (-_speed(unit.type, world), unit.id)) if runners else None


def _speed(unit_type: str, world: World) -> float:
    facts = world.items.get(unit_type)
    return 0.0 if facts is None or facts.speed is None else facts.speed


def _heading_from(aim: Cell, middle: Point, world: World) -> Point:
    """The unit vector from an aim toward main's centroid; toward the base, else west, when main stands on the aim."""
    for toward in (middle, world.base_center):
        if toward is not None and math.dist(aim, toward) > 0:
            span = math.dist(aim, toward)
            return (toward[0] - aim[0]) / span, (toward[1] - aim[1]) / span
    return -1.0, 0.0


def _front(aim: Cell, heading: Point, world: World, ctx: MatchContext) -> Point | None:
    """The middle of the long-range units' front: the first point from the aim along `heading`, half a cell at a time,
    out of every known ground defense's coverage; None within `_FRONT_SEARCH_CELLS`."""
    covered = coverage(world, ctx)
    for step in range(math.floor(_FRONT_SEARCH_CELLS / _STEP_CELLS) + 1):
        depth = step * _STEP_CELLS
        point = (aim[0] + heading[0] * depth, aim[1] + heading[1] * depth)
        if not is_covered(point, covered):
            return point
    return None


def _toward_base(front: Point, heading: Point, world: World) -> Point:
    """The unit vector from the front toward the base; `heading` while the base is not known or stands on the front."""
    base = world.base_center
    if base is None or math.dist(front, base) == 0:
        return heading
    span = math.dist(front, base)
    return (base[0] - front[0]) / span, (base[1] - front[1]) / span


def _line_cells(units: list[Unit], middle: Point, back: Point, world: World, ctx: MatchContext) -> dict[int, Cell]:
    """A cell each for some units on a line through `middle` across `back`, slot_spacing_cells of [siege] apart, each
    moved back along `back` out of every coverage; the units take them in their order across the line."""
    across = (-back[1], back[0])
    spacing = ctx.tuning.siege.slot_spacing_cells
    covered = coverage(world, ctx)
    offsets = [(index - (len(units) - 1) / 2) * spacing for index in range(len(units))]
    places = [
        _clear_cell((middle[0] + across[0] * offset, middle[1] + across[1] * offset), back, covered, world)
        for offset in offsets
    ]
    order = sorted(
        units, key=lambda unit: ((unit.x - middle[0]) * across[0] + (unit.y - middle[1]) * across[1], unit.id)
    )
    return {unit.id: place for unit, place in zip(order, places)}


def _clear_cell(point: Point, back: Point, covered: list[tuple[Cell, float]], world: World) -> Cell:
    """The cell of a point, moved back along `back` half a cell at a time until it is out of every coverage."""
    cell = _clamped(cells.nearest_cell(*point), world)
    for step in range(math.floor(_FRONT_SEARCH_CELLS / _STEP_CELLS) + 1):
        depth = step * _STEP_CELLS
        cell = _clamped(cells.nearest_cell(point[0] + back[0] * depth, point[1] + back[1] * depth), world)
        if not is_covered(cell, covered):
            return cell
    return cell


def _held(intent: Intent, covered: list[tuple[Cell, float]], world: World) -> Intent | None:
    """One non-emergency order held out of covered ground (see `held_off`); None when nothing of it is left."""
    command = intent.order.get("cmd")
    if command in ("move", "attack_move"):
        return _held_move(intent, covered, world)
    if command == "attack":
        return _held_attack(intent, covered, world)
    return intent


def _held_move(intent: Intent, covered: list[tuple[Cell, float]], world: World) -> Intent | None:
    """A move or an attack-move to a covered cell sent to the coverage's edge on the way back toward its units instead;
    None when there is no such cell."""
    order = intent.order
    if not is_covered(tuple(order["to"]), covered):
        return intent
    units = [world.units[unit_id] for unit_id in order["units"] if unit_id in world.units]
    edge = _edge(tuple(order["to"]), units, covered, world)
    return None if edge is None else replace(intent, order={**order, "to": list(edge)})


def _held_attack(intent: Intent, covered: list[tuple[Cell, float]], world: World) -> Intent | None:
    """An attack on a covered target kept for the units within their weapon range of it alone; None for none."""
    order = intent.order
    target = world.enemy_memory.get(order["target"])
    if target is None or not is_covered(_cell(target), covered):
        return intent
    firing = [unit_id for unit_id in order["units"] if _in_range(world.units.get(unit_id), target, world)]
    return replace(intent, order={**order, "units": firing}) if firing else None


def _edge(cell: Cell, units: list[Unit], covered: list[tuple[Cell, float]], world: World) -> Cell | None:
    """The first cell out of every coverage on the way back from a covered cell toward the units' centroid, and on to
    the base when that is covered too; None for none."""
    stops = [cells.nearest_cell(*centroid(units))] if units else []
    if world.base_center is not None:
        stops.append(world.base_center)
    start = cell
    for stop in stops:
        found = next((step for step in cells.line(start, stop) if not is_covered(step, covered)), None)
        if found is not None:
            return found
        start = stop
    return None


def _in_range(unit: Unit | None, target: EnemyRecord, world: World) -> bool:
    """Whether a unit stands within its weapon range of a target."""
    return unit is not None and math.dist((unit.x, unit.y), _cell(target)) <= world.items.damage.get(unit.type).range


def _given(intents: Iterable[Intent | None]) -> list[Intent]:
    """The intents of some that were given, the Nones left out."""
    return [intent for intent in intents if intent is not None]


def _clamped(cell: Cell, world: World) -> Cell:
    """The cell of the playable map nearest to a cell, while the map's bounds are known."""
    return cell if world.map_bounds is None else cells.clamped(cell, world.map_bounds)


def _cell(record: EnemyRecord) -> Cell:
    return record.x, record.y
