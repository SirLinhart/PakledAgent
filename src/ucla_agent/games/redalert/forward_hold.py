"""The forward hold: under HOLD, main stands at a point on the line toward the enemy instead of at the staging point.

With `forward_hold` of `[combat]` off, main gathers at the staging point
under HOLD and never goes forward. With it on, main stands farther out, where
it meets what comes toward the base before it gets there.

**The forward point.** By default it is `forward_hold_share` (40 percent) of
the way from the base to the enemy's start. An own construction yard or the
creep target's ore field that lies farther forward along that line is an
outpost of the base, and main covers it: the point is then
`forward_hold_cover_cells` beyond the nearest such outpost, toward the
enemy's start. Forward is measured along the line from the base to the
enemy's start, so that a yard off to one side counts by how far toward the
enemy it stands. Either point is then moved back toward the base, half a cell
at a time, until it is no farther than halfway along the line and outside
the range of every remembered enemy defense by
`forward_hold_defense_clear_cells`: main holds on its own half of the map,
where no known defense reaches it. A point that would have to come back
behind the staging point is none.

**Where main gathers.** Under HOLD, while main is worth at least
`forward_hold_min_strength` in a fight, it gathers at the forward point;
weaker, under any other plan, or with no forward point, at the staging point.
Under base defense's override or recall the combat manager orders main
nothing, and the gather point stays as it was. Each change of the gather
point is written to the match record as one `hold_position` event: the cell,
the reason (`forward` or `staging`) and main's strength. The match starts
with main at the staging point, which is no change. Under HOLD main is never
sent forward: an army gone forward under another plan comes back to the
gather point when HOLD comes in, and main at the forward point only gathers
there, whatever strength the plan asks before an advance.

**Fighting there.** At the forward point main engages as it does anywhere,
on the estimate of its fight within the contact distance, at `engage_ratio`
or more; but it goes only after enemy units: never a building, and never a
unit that a remembered enemy defense covers, so that holding never takes it
into the enemy's base or its defenses. A unit that has chased an enemy
farther than `forward_hold_chase_cells` from the point is sent back to it and
goes after nothing more until it is back within `gathered_cells` of the
point. The units main has sent after an enemy are published with the army it
has sent forward (`army` of the match context): a unit standing at the
forward point waits there, and the scout, the raid and the MCV's escort may be
taken from it; a unit that chases may not.

**Falling back.** Main falls back to the staging point when the estimate of
its fight is below `retreat_ratio`, or when enemy units in sight within twice
the contact distance of the forward point, that have come at least two cells
closer to it than where they were first seen there, are worth more together
than main: an army stronger than main is coming at it. Falling back is a
retreat: main's mode turns to retreat at once, whatever time its mode has
been kept, so that it is sent to the staging point in an emergency order, and
the gather point is the staging point, written as a `hold_position` event
with the reason `fell_back`. Main goes forward again once the estimate of its
fight has stayed at `engage_ratio` or more (no enemy near counts as that) for
`mode_hold_ticks`. A main that was not holding forward, but at the staging
point, stays there on the same signs, without a retreat.

**Covering the expansions.** An expansion yard far from the staging point is
lost before main comes out to it. So with `expansion_cover_rally` of
`[combat]` on, while an own construction yard other than the main yard
stands and the plan is OPENING (once a war factory stands), HOLD or RAID,
main gathers at the cover point (`cover_point`) whatever its strength, and
the producers rally there; with `forward_hold` on as well it replaces the
forward point. The cover point lies on the line from the base to the enemy's
start, `expansion_cover_forward_cells` beyond the deepest expansion yard's
middle, moved back toward the base half a cell at a time to the first cell
that is at most halfway, outside every remembered defense's reach and the
clear margin, and within `expansion_cover_reach_cells` of every expansion
yard's middle. With no such cell on the line, it is the cell
`expansion_cover_forward_cells` from the farthest-forward yard toward the
enemy, pulled back the same way but for the reach; with none either, main
gathers at the staging point. It is worked out again whenever the yards or
the remembered defenses change, and every `COVER_EVERY_TICKS` otherwise.
Main fights, chases, falls back and comes forward again there as at the
forward point, and each change of the gather point to it is a
`hold_position` event with the reason `cover_expansions`. An army gone
forward is not called back for it: the cover point is where main waits, not
when it attacks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Callable

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, coverage, expansion
from ucla_agent.games.redalert.composition import HOLD, OPENING, RAID
from ucla_agent.games.redalert.family import WAR_FACTORY
from ucla_agent.games.redalert.fight import Estimate, Point, is_counted, strength, worth
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.placement import CreepTarget
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

HOLD_POSITION_EVENT = "hold_position"
# The reasons a `hold_position` event gives: main gathers at the forward point, at the staging point, or at the staging
# point after falling back from the forward point.
FORWARD = "forward"
STAGING = "staging"
FELL_BACK = "fell_back"
# ... and at the cover point of the expansions.
COVER = "cover_expansions"
# The plans under which main covers the expansions: those under which it waits rather than attacks.
COVER_PLANS = frozenset({OPENING, HOLD, RAID})
# The cover point is worked out again at least this often while the yards and the remembered defenses stay the same,
# so that it follows the enemy's start and the map as they come to be known.
COVER_EVERY_TICKS = 125
# Enemy units are watched for coming at the forward point from this many times the contact distance, so that an army
# is seen coming before it is in the fight ...
APPROACH_WATCH_CONTACTS = 2
# ... and one has come at it once it stands this many cells closer to the point than where it was first seen there.
APPROACH_CLOSING_CELLS = 2

# The forward point is moved back toward the base this far at a time while it is too far out or a defense reaches it.
_PULL_BACK_STEP_CELLS = 0.5
# The share of the way to the enemy's start the forward point never lies beyond.
_HALFWAY_SHARE = 0.5


class ForwardHold:
    """Where main gathers with the forward hold on, written to the match record at each change. See the module's note."""

    def __init__(self) -> None:
        # The gather point in force, and why; None before the first cycle that ordered main.
        self.cell: Cell | None = None
        self.reason = STAGING
        # Whether the plan in force was HOLD in the latest cycle the hold was followed.
        self.is_under_hold = False
        # The units of main on their way back to the forward point after chasing too far.
        self._returning: set[int] = set()
        self._is_fallen_back = False
        # While fallen back, the tick since which main's estimate has been at the engage ratio or more.
        self._steady_since: int | None = None
        # The forward point enemy units are watched for coming at, and how far from it each was first seen, by id.
        self._watched_point: Cell | None = None
        self._first_seen: dict[int, float] = {}
        # With expansion_cover_rally on: the cover point last worked out, what it was worked out from and when.
        self._cover: Cell | None = None
        self._cover_inputs: tuple[object, ...] | None = None
        self._cover_tick: int | None = None

    @property
    def is_forward(self) -> bool:
        """Whether main gathers at the forward point, or at the cover point of the expansions."""
        return self.reason in (FORWARD, COVER)

    def follow(self, main: list[Unit], found: Estimate | None, staging: Cell, world: World, ctx: MatchContext) -> bool:
        """Take the point main gathers at in this cycle (`cell`): the forward point under HOLD while main is strong
        enough and has not fallen back, else the staging point. True when main falls back from the forward point in
        this cycle; `found` is the estimate of main's fight, None for no enemy near."""
        tuning = ctx.tuning.combat
        power = strength(main, world)
        self._recover(found, world.tick, tuning)
        self.is_under_hold = tuning.forward_hold and ctx.plan == HOLD
        forward, reason = self._point(world, ctx)
        if self._is_unpublished(forward, tuning):
            return False
        # The enemy is watched for coming at the forward point whatever main's strength, so that a main that has just
        # grown strong enough knows what is on its way there.
        coming = 0.0 if forward is None else self._coming_worth(forward, world, tuning)
        if forward is None or _is_too_weak(reason, power, tuning):
            self._take(staging, STAGING, power, world)
            return False
        if not self._is_fallen_back and _is_outmatched(power, found, coming, tuning):
            was_forward = self.is_forward
            self._is_fallen_back, self._steady_since = True, None
            self._take(staging, FELL_BACK, power, world)
            return was_forward
        self._take(*((staging, FELL_BACK) if self._is_fallen_back else (forward, reason)), power, world)
        return False

    def _is_unpublished(self, forward: Cell | None, tuning: CombatTuning) -> bool:
        """Whether main has no gather point to publish: with the forward hold off, none is until main has covered an
        expansion once, so that without one the gather point and the rally stay as the forward hold's switch has them.
        """
        return forward is None and not tuning.forward_hold and self.cell is None

    def _point(self, world: World, ctx: MatchContext) -> tuple[Cell | None, str]:
        """Where main holds out from the staging point in this cycle, and why: the cover point while it covers the
        expansions (`_covers`), which may be none; else with the forward hold on under HOLD the forward point; else
        none."""
        if self._covers(world, ctx):
            return self._cover_point(world, ctx), COVER
        if self.is_under_hold:
            return forward_point(world, ctx.creep_target, ctx.tuning), FORWARD
        return None, FORWARD

    @staticmethod
    def _covers(world: World, ctx: MatchContext) -> bool:
        """Whether main covers the expansions: `expansion_cover_rally` on, an expansion yard standing, and the plan
        OPENING once a war factory stands, HOLD or RAID."""
        if not ctx.tuning.combat.expansion_cover_rally or ctx.plan not in COVER_PLANS:
            return False
        has_war_factory = any(building.type == WAR_FACTORY for building in world.buildings.values())
        return (ctx.plan != OPENING or has_war_factory) and bool(expansion.expansion_yards(world))

    def _cover_point(self, world: World, ctx: MatchContext) -> Cell | None:
        """The cover point (`cover_point`), worked out again when the yards or the remembered defenses change, or
        `COVER_EVERY_TICKS` after it last was."""
        defenses = tuple(sorted((record.type, record.x, record.y) for record in world.known_enemy_defenses))
        inputs = (tuple(sorted(expansion.yards(world))), defenses)
        is_due = self._cover_tick is None or world.tick - self._cover_tick >= COVER_EVERY_TICKS
        if inputs != self._cover_inputs or is_due:
            self._cover_inputs, self._cover_tick = inputs, world.tick
            self._cover = cover_point(world, ctx.tuning)
        return self._cover

    def _recover(self, found: Estimate | None, tick: int, tuning: CombatTuning) -> None:
        """End a fall-back once main's estimate has stayed at the engage ratio or more for the mode hold time."""
        if not self._is_fallen_back:
            return
        ratio = tuning.ratio_without_enemy if found is None else found.ratio
        if ratio < tuning.engage_ratio:
            self._steady_since = None
        elif self._steady_since is None:
            self._steady_since = tick
        elif tick - self._steady_since >= tuning.mode_hold_ticks:
            self._is_fallen_back = False

    def _coming_worth(self, point: Cell, world: World, tuning: CombatTuning) -> float:
        """What the enemy units in sight near the forward point that have come closer to it than where they were first
        seen near it are worth in a fight together."""
        if point != self._watched_point:
            self._watched_point, self._first_seen = point, {}
        watch = APPROACH_WATCH_CONTACTS * tuning.contact_cells
        near = {
            record.id: (record, cells.distance((record.x, record.y), point))
            for record in world.enemy_memory.values()
            if record.visible and is_counted(record) and cells.is_within((record.x, record.y), point, watch)
        }
        self._first_seen = {enemy_id: self._first_seen.get(enemy_id, gap) for enemy_id, (_, gap) in near.items()}
        return sum(
            worth(record.type, record.hp, world)
            for enemy_id, (record, gap) in near.items()
            if self._first_seen[enemy_id] - gap >= APPROACH_CLOSING_CELLS
        )

    def _take(self, cell: Cell, reason: str, power: float, world: World) -> Cell:
        """Make a cell the gather point, with a `hold_position` event when it differs from the one in force."""
        is_first_staging = self.cell is None and reason == STAGING
        if cell != self.cell and not is_first_staging:
            data = {"tick": world.tick, "cell": list(cell), "reason": reason, "strength": round(power)}
            records.log(HOLD_POSITION_EVENT, data)
        self.cell, self.reason = cell, reason
        if not self.is_forward:
            self._returning.clear()
        return cell

    def leash(self, army: list[Unit], point: Cell, tuning: CombatTuning) -> tuple[list[Unit], list[Unit]]:
        """main at the forward point as the units on their way back to it, having chased too far, and the rest.

        A unit goes back once it is farther than `forward_hold_chase_cells`
        from the point, and fights again once it is within `gathered_cells`
        of it.
        """
        here = {unit.id: (unit.x, unit.y) for unit in army}
        far = {
            unit_id
            for unit_id, cell in here.items()
            if not cells.is_within(cell, point, tuning.forward_hold_chase_cells)
        }
        home = {unit_id for unit_id, cell in here.items() if cells.is_within(cell, point, tuning.gathered_cells)}
        self._returning = ((self._returning & here.keys()) | far) - home
        back = [unit for unit in army if unit.id in self._returning]
        return back, [unit for unit in army if unit.id not in self._returning]


def _is_too_weak(reason: str, power: float, tuning: CombatTuning) -> bool:
    """Whether main is too weak to hold at its point: at the forward point below `forward_hold_min_strength`; at the
    cover point never, since the expansions need covering whatever main is worth."""
    return reason == FORWARD and power < tuning.forward_hold_min_strength


def _is_outmatched(power: float, found: Estimate | None, coming: float, tuning: CombatTuning) -> bool:
    """Whether main must not hold forward: the estimate of its fight is below the retreat ratio, or what comes at the
    forward point is worth more than main."""
    return (found is not None and found.ratio < tuning.retreat_ratio) or coming > power


def may_chase(record: EnemyRecord, world: World, tuning: Tuning) -> bool:
    """Whether main at the forward point may go after an enemy: a unit, not a building, that no remembered enemy
    defense covers."""
    is_covered = coverage.is_covered((record.x, record.y), world.known_enemy_defenses, world.items, tuning.coverage)
    return not record.is_building and not is_covered


@dataclass(frozen=True)
class _Line:
    """The straight way from the base to the enemy's start, along which forward and back are measured."""

    base: Cell
    enemy: Cell

    @property
    def length(self) -> float:
        return cells.distance(self.base, self.enemy)

    def depth(self, point: Point) -> float:
        """How far along the line toward the enemy a point lies, in cells: the length of its projection on the line."""
        span_x, span_y = self.enemy[0] - self.base[0], self.enemy[1] - self.base[1]
        return ((point[0] - self.base[0]) * span_x + (point[1] - self.base[1]) * span_y) / self.length


def forward_point(world: World, creep_target: CreepTarget | None, tuning: Tuning) -> Cell | None:
    """Where main holds under HOLD with the forward hold on. See the module's note.

    None while the base, the enemy's start or the map's bounds are not known,
    and when no cell ahead of the staging point is clear of the enemy's
    remembered defenses.
    """
    base, enemy = world.base_center, world.enemy_base
    if base is None or enemy is None or world.map_bounds is None or base == enemy:
        return None
    line, combat = _Line(base, enemy), tuning.combat
    default = combat.forward_hold_share * line.length
    farther = [outpost for outpost in _outposts(world, creep_target) if line.depth(outpost) > default]
    if farther:
        point = cells.toward(min(farther, key=line.depth), enemy, combat.forward_hold_cover_cells)
    else:
        point = cells.toward(base, enemy, default)
    return _pulled_back(point, line, world, tuning)


def _outposts(world: World, creep_target: CreepTarget | None) -> list[Cell]:
    """The places the base reaches out to: every own construction yard that stands, and the creep target's field."""
    field = [] if creep_target is None else [creep_target.patch.center]
    return [*expansion.yards(world), *field]


def cover_point(world: World, tuning: Tuning) -> Cell | None:
    """Where main covers the expansions with `expansion_cover_rally` on. See the module's note.

    None while the base, the enemy's start or the map's bounds are not known,
    while no expansion yard stands, and when neither the line nor the farthest
    expansion has a cell ahead of the staging point clear of the enemy's
    remembered defenses.
    """
    base, enemy = world.base_center, world.enemy_base
    middles = expansion.expansion_yards(world)
    if base is None or enemy is None or world.map_bounds is None or base == enemy or not middles:
        return None
    line, combat = _Line(base, enemy), tuning.combat
    ahead, reach = combat.expansion_cover_forward_cells, combat.expansion_cover_reach_cells

    def reaches_every_yard(cell: Cell) -> bool:
        return all(cells.is_within(cell, middle, reach) for middle in middles)

    deepest = max(line.depth(middle) for middle in middles)
    on_line = _pulled_back(cells.toward(base, enemy, deepest + ahead), line, world, tuning, reaches_every_yard)
    if on_line is not None:
        return on_line
    farthest = max(middles, key=line.depth)
    return _pulled_back(cells.toward(farthest, enemy, ahead), line, world, tuning)


def _pulled_back(
    point: Cell, line: _Line, world: World, tuning: Tuning, keeps: Callable[[Cell], bool] | None = None
) -> Cell | None:
    """The first cell from `point` back toward the base that is at most halfway along the line, that no remembered
    defense reaches within the clear margin and, with `keeps` given, that it keeps; None when the search comes back
    behind the staging point first."""
    halfway = _HALFWAY_SHARE * line.length
    staging = tuning.upkeep.staging_distance_cells
    # A defense's reach as the scouts' and raids' coverage takes it, with the forward hold's own margin.
    clear = replace(tuning.coverage, margin_cells=tuning.combat.forward_hold_defense_clear_cells)
    defenses = world.known_enemy_defenses
    steps = math.floor(cells.distance(point, line.base) / _PULL_BACK_STEP_CELLS)
    for step in range(steps + 1):
        cell = cells.clamped(cells.toward(point, line.base, step * _PULL_BACK_STEP_CELLS), world.map_bounds)
        depth = line.depth(cell)
        if depth < staging:
            return None
        is_kept = keeps is None or keeps(cell)
        if depth <= halfway and is_kept and not coverage.is_covered(cell, defenses, world.items, clear):
            return cell
    return None
