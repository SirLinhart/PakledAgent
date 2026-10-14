"""The closing-army intercept: main sent out to meet an enemy army closing on a yard, the travelling MCV or the base.

Base defense alarms only within alarm_radius_cells of an own building and
threat_area_cells of base_center, and the closing watch of the model commander
(`command_threat.ClosingWatch`) serves only the commander. So with
`closing_intercept` of `[base_defense]` on, while the model commander does not
command main (it is off, or still commands the scout), the combat manager
watches in every cycle, the figures those of `[base_defense]`:

- **Targets:** each own construction yard, the travelling MCV (an own MCV
  while a yard stands), the site it goes to (`MatchContext.mcv_site`) and
  base_center.
- **Trigger:** an enemy army closing on a target (`ClosingWatch.closing_on`):
  the enemy combat units seen within the last 10 s that stand within
  closing_cells (30) of it, worth closing_min_value (1,000) or more together,
  whose centroid is closer to it than that of the same units 15 s before, or
  none of which was seen then. Of several targets, the one its closing army
  stands nearest is taken.
- **Gate:** under HOLD or RAID only, never PRESERVE; with no major threat in
  force (base defense keeps the threats inside its alarm radius), no strike in
  force and main not taken back by base defense. An army with a unit within
  contact_cells of `[combat]` of main's centroid is main's own fight already
  (`fight.estimate` counts it), and starts no response. One response runs at
  a time.

**The response.** main, its units on no detachment, strike or join, is weighed
against the army by `fight.ratio_against`:

- **Intercept:** at closing_min_ratio (1.0) or more, with main gathered at the
  staging point (`strike.gathered_at`), every unit of main attack-moves to the
  cell intercept_front_cells (8) in front of the target on the line to the
  army's centroid: the gathered units go, the stragglers follow. A cell inside
  the coverage of a known enemy ground defense (`defended.coverage`: its range
  and defended_margin_cells of `[combat]`) is pulled back toward the target
  until it lies outside every one.
- **Defend:** under it, main attack-moves to the target's cell when the target
  lies within far_rescue_cells (20) of base_center;
- **Hold:** and holds at the staging point beyond.

main engaged with an enemy near fights where it stands (see `combat`). A
target whose intercept was abandoned is not intercepted again for
`REST_SECONDS`; it is defended or held meanwhile.

**The MCV.** While main defends or holds against an army closing on the
travelling MCV or its site, the MCV holds where it stood when the response
started (`mcv_halt`, which the combat manager publishes as
`MatchContext.mcv_halt` and the MCV's rule in `expansion` obeys): it does not
walk on toward the army that main is too weak to stop. Once the army stands
`KEEPS_CLOSING_CELLS` nearer the MCV than it did then, the MCV goes back to
base_center, away from the army, until the response ends.

**End.** A response ends when its army is gone, none of its units seen for
`GONE_SECONDS` (`gone`); when the army stands beyond closing_cells +
`AWAY_MARGIN_CELLS` of the target (`away`); for an intercept, when main's
ratio against the army, or the estimate of main's own fight where it stands,
falls under strike_abandon_ratio of `[plan_rules]` (`abandoned`, see
`strike.is_losing`); when its target is gone (`target_gone`); and when the
plan leaves HOLD and RAID, a major threat comes, a strike starts, or base
defense or the commander takes main (`recalled`). main then goes back to the
staging point, but for a recall.

Each start and end is a `combat` event with the reason `closing_intercept`:
the response, the target, its cell, the army's value and main's ratio against
it.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from operator import attrgetter
from typing import Mapping, Sequence

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, defended
from ucla_agent.games.redalert.command_threat import ClosingGroup, ClosingWatch
from ucla_agent.games.redalert.composition import HOLD, RAID
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD, MCV
from ucla_agent.games.redalert.fight import centroid, is_near, ratio_against
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MAJOR_THREAT, MatchContext
from ucla_agent.games.redalert.strike import gathered_at, is_losing
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

COMBAT_EVENT = "combat"
CLOSING_INTERCEPT = "closing_intercept"
# The responses.
INTERCEPT = "intercept"
DEFEND = "defend"
HOLD_AT_STAGING = "hold"
# The targets watched.
YARD_TARGET = "yard"
MCV_TARGET = "mcv"
SITE_TARGET = "site"
BASE_TARGET = "base"
# The targets for which the travelling MCV holds.
MCV_TARGETS = frozenset({MCV_TARGET, SITE_TARGET})
# Why a response ends.
GONE = "gone"
AWAY = "away"
ABANDONED = "abandoned"
TARGET_GONE = "target_gone"
RECALLED = "recalled"
# The plans under which main answers an army closing.
PLANS = frozenset({HOLD, RAID})
# An army none of whose units was seen this long is gone.
GONE_SECONDS = 20
# An army this much farther than closing_cells from its target has gone away.
AWAY_MARGIN_CELLS = 10
# A target whose intercept was abandoned is not intercepted again for this long.
REST_SECONDS = 30
# An army this much nearer the held MCV than when the hold began keeps closing on it, and the MCV goes back home.
KEEPS_CLOSING_CELLS = 2
_RATIO_DECIMALS = 2
_BY_ID = attrgetter("id")


@dataclass(frozen=True)
class Target:
    """What an army closes on: its kind, the id of the yard or the MCV (None for the site and the base) and its
    cell."""

    kind: str
    key: int | None
    cell: Cell

    @property
    def identity(self) -> tuple[str, int | None]:
        """The target whatever its cell: its kind and id."""
        return self.kind, self.key


@dataclass(frozen=True)
class Response:
    """main's answer to an army closing: its kind, the target, the cell main goes to, the ids of the army's units
    and main's ratio against the army, as of the latest cycle."""

    kind: str
    target: Target
    cell: Cell
    army: frozenset[int]
    value: int
    ratio: float


class ClosingIntercept:
    """The response of main in force to an enemy army closing on a target, and the targets resting. See the module's
    note."""

    def __init__(self) -> None:
        self._watch = ClosingWatch()
        # The response in force; None while there is none.
        self.response: Response | None = None
        # Target identity -> the tick until which it is not intercepted again.
        self._resting: dict[tuple[str, int | None], int] = {}
        # Where the travelling MCV holds while main defends or holds against an army closing on it or its site, and
        # how far the army stood from it as the hold began; None while it does not hold.
        self.mcv_halt: Cell | None = None
        self._held_gap: float | None = None

    def follow(
        self, main: Mapping[int, Unit], point: Cell | None, blocked: bool, world: World, ctx: MatchContext
    ) -> str | None:
        """Bring the response up to date in this cycle, main's free units being `main`: end it when it is over or
        `blocked` (a strike in force, or base defense or the commander has main), else start one on the army closing
        nearest a target while the staging point `point` is known. Why the response ended in this cycle; None while
        none did."""
        if not ctx.tuning.base_defense.closing_intercept:
            return None
        self._watch.note(world)
        self._resting = {target: until for target, until in self._resting.items() if until > world.tick}
        units = list(main.values())
        if self.response is not None:
            outcome = self._ended(self.response, units, blocked, world, ctx)
            if outcome is not None:
                self._end(outcome, world)
                return outcome
            self._keep_up(self.response, units, point, world, ctx)
            return None
        if not blocked and point is not None and units and _may_start(ctx):
            self._start(main, point, world, ctx)
        return None

    def _start(self, main: Mapping[int, Unit], point: Cell, world: World, ctx: MatchContext) -> None:
        """Start a response to the army closing nearest a target, when one does, and write its start event."""
        units = list(main.values())
        found = self._closing(units, world, ctx)
        if found is None:
            return
        target, group = found
        ratio = ratio_against(units, group.records, world, ctx.tuning)
        may_intercept = ratio >= ctx.tuning.base_defense.closing_min_ratio and target.identity not in self._resting
        kind = INTERCEPT if may_intercept and gathered_at(main, point, ctx) else _fallback(target, world, ctx)
        army = frozenset(record.id for record in group.records)
        cell = _response_cell(kind, target, group.records, point, world, ctx)
        self.response = Response(kind, target, cell, army, group.value, ratio)
        self._log("start", world)
        mcv = _travelling_mcv(world)
        if kind != INTERCEPT and target.kind in MCV_TARGETS and mcv is not None:
            self.mcv_halt = mcv.x, mcv.y
            self._held_gap = cells.distance(cells.nearest_cell(*group.centroid), self.mcv_halt)

    def _closing(self, units: list[Unit], world: World, ctx: MatchContext) -> tuple[Target, ClosingGroup] | None:
        """The target an enemy army closes on and that army, the one standing nearest its target of all, of the armies
        not in contact with main's units (`units`); None while none closes."""
        tuning = ctx.tuning.base_defense
        main_middle, contact = centroid(units), ctx.tuning.combat.contact_cells
        found = []
        for target in watched_targets(world, ctx):
            group = self._watch.closing_on(target.cell, tuning.closing_cells, tuning.closing_min_value, world)
            if group is not None and not _is_met(group, main_middle, contact):
                found.append((cells.distance(cells.nearest_cell(*group.centroid), target.cell), target, group))
        if not found:
            return None
        _, target, group = min(found, key=lambda item: item[0])
        return target, group

    def _ended(
        self, response: Response, units: list[Unit], blocked: bool, world: World, ctx: MatchContext
    ) -> str | None:
        """Why a response is over in this cycle; None while it goes on."""
        if blocked or not _may_start(ctx):
            return RECALLED
        target = _target_now(response.target, world, ctx)
        if target is None:
            return TARGET_GONE
        seen = _seen(response.army, world)
        if not seen:
            return GONE
        reach = ctx.tuning.base_defense.closing_cells + AWAY_MARGIN_CELLS
        if not cells.is_within(cells.nearest_cell(*_middle(seen)), target.cell, reach):
            return AWAY
        if response.kind == INTERCEPT and is_losing(ratio_against(units, seen, world, ctx.tuning), units, world, ctx):
            return ABANDONED
        return None

    def _keep_up(
        self, response: Response, units: list[Unit], point: Cell | None, world: World, ctx: MatchContext
    ) -> None:
        """Bring a response that goes on up to date: its target's cell, the army's units (those closing on the target
        again added), their value, main's ratio against them and the cell main goes to."""
        tuning = ctx.tuning.base_defense
        target = _target_now(response.target, world, ctx)
        group = self._watch.closing_on(target.cell, tuning.closing_cells, tuning.closing_min_value, world)
        army = response.army | (frozenset() if group is None else {record.id for record in group.records})
        seen = _seen(army, world)
        value = round(sum(_cost(record, world) for record in seen))
        ratio = ratio_against(units, seen, world, ctx.tuning)
        cell = response.cell if point is None else _response_cell(response.kind, target, seen, point, world, ctx)
        self.response = replace(response, target=target, cell=cell, army=army, value=value, ratio=ratio)
        self._keep_mcv_back(seen, world)

    def _keep_mcv_back(self, seen: list[EnemyRecord], world: World) -> None:
        """Send the held MCV back to base_center once the army stands KEEPS_CLOSING_CELLS nearer it than as the hold
        began."""
        mcv, home = _travelling_mcv(world), world.base_center
        if self._held_gap is None or mcv is None or home is None:
            return
        if cells.distance(cells.nearest_cell(*_middle(seen)), (mcv.x, mcv.y)) <= self._held_gap - KEEPS_CLOSING_CELLS:
            self.mcv_halt, self._held_gap = home, None

    def _end(self, outcome: str, world: World) -> None:
        """End the response with its end event; a target whose intercept was abandoned rests."""
        response = self.response
        if response.kind == INTERCEPT and outcome == ABANDONED:
            self._resting[response.target.identity] = world.tick + REST_SECONDS * TICKS_PER_SECOND
        self._log("end", world, outcome)
        self.response, self.mcv_halt, self._held_gap = None, None, None

    def _log(self, phase: str, world: World, outcome: str | None = None) -> None:
        """Write the start or end event of the response in force."""
        response = self.response
        event = {
            "tick": world.tick,
            "reason": CLOSING_INTERCEPT,
            "phase": phase,
            "response": response.kind,
            "target": response.target.kind,
            "target_id": response.target.key,
            "cell": list(response.target.cell),
            "to": list(response.cell),
            "value": response.value,
            "ratio": round(response.ratio, _RATIO_DECIMALS),
        }
        if outcome is not None:
            event["outcome"] = outcome
        records.log(COMBAT_EVENT, event)


def watched_targets(world: World, ctx: MatchContext) -> list[Target]:
    """The targets an army may close on: the own construction yards, the travelling MCV (an own MCV while a yard
    stands), its site and base_center, in that order and each kind by id."""
    yards = sorted(
        (building for building in world.buildings.values() if building.type == CONSTRUCTION_YARD), key=_BY_ID
    )
    found = [Target(YARD_TARGET, yard.id, (yard.x, yard.y)) for yard in yards]
    if yards:
        mcvs = sorted((unit for unit in world.units.values() if unit.type == MCV), key=_BY_ID)
        found += [Target(MCV_TARGET, mcv.id, (mcv.x, mcv.y)) for mcv in mcvs]
    if ctx.mcv_site is not None:
        found.append(Target(SITE_TARGET, None, ctx.mcv_site))
    if world.base_center is not None:
        found.append(Target(BASE_TARGET, None, world.base_center))
    return found


def _is_met(group: ClosingGroup, main_middle: tuple[float, float], contact: float) -> bool:
    """Whether main is in contact with an army already: a unit of it stands within `contact` cells of main's centroid,
    where main's own fight counts it."""
    return any(is_near(main_middle, record.x, record.y, contact) for record in group.records)


def _travelling_mcv(world: World) -> Unit | None:
    """The travelling MCV: the own MCV of the lowest id while a construction yard stands; None otherwise."""
    if not any(building.type == CONSTRUCTION_YARD for building in world.buildings.values()):
        return None
    return min((unit for unit in world.units.values() if unit.type == MCV), key=_BY_ID, default=None)


def intercept_cell(target: Cell, army: Cell, world: World, ctx: MatchContext) -> Cell:
    """The cell intercept_front_cells in front of a target on the line to the army's centroid `army` (the army's cell
    when it stands nearer), pulled back toward the target until it lies outside the coverage of every known enemy
    ground defense (`defended.coverage`); the target's cell when every cell of the line is covered."""
    front = min(ctx.tuning.base_defense.intercept_front_cells, cells.distance(target, army))
    covered = defended.coverage(world, ctx)
    for step in range(int(front), -1, -1):
        cell = cells.toward(target, army, step) if step else target
        if world.map_bounds is not None:
            cell = cells.clamped(cell, world.map_bounds)
        if not defended.is_covered(cell, covered):
            return cell
    return target


def _response_cell(
    kind: str, target: Target, army: Sequence[EnemyRecord], point: Cell, world: World, ctx: MatchContext
) -> Cell:
    """Where main goes for a response: the intercept cell, the target's cell it defends, or the staging point."""
    if kind == INTERCEPT:
        return intercept_cell(target.cell, cells.nearest_cell(*_middle(army)), world, ctx)
    return target.cell if kind == DEFEND else point


def _fallback(target: Target, world: World, ctx: MatchContext) -> str:
    """The response of a main that may not intercept: defend a target within far_rescue_cells of base_center, hold at
    the staging point otherwise."""
    home = world.base_center
    near = home is not None and cells.is_within(target.cell, home, ctx.tuning.base_defense.far_rescue_cells)
    return DEFEND if near else HOLD_AT_STAGING


def _may_start(ctx: MatchContext) -> bool:
    """Whether main may answer an army closing by the match's state: the plan is HOLD or RAID and no major threat is in
    force."""
    return ctx.plan in PLANS and ctx.threat_level != MAJOR_THREAT


def _target_now(target: Target, world: World, ctx: MatchContext) -> Target | None:
    """A target as this cycle shows it, with its cell now; None once it is gone."""
    if target.kind == YARD_TARGET:
        yard = world.buildings.get(target.key)
        return None if yard is None else replace(target, cell=(yard.x, yard.y))
    if target.kind == MCV_TARGET:
        mcv = world.units.get(target.key)
        return None if mcv is None else replace(target, cell=(mcv.x, mcv.y))
    cell = ctx.mcv_site if target.kind == SITE_TARGET else world.base_center
    return None if cell is None else replace(target, cell=cell)


def _seen(army: frozenset[int], world: World) -> list[EnemyRecord]:
    """The army's units the enemy memory holds that were seen within GONE_SECONDS."""
    recent = GONE_SECONDS * TICKS_PER_SECOND
    remembered = (world.enemy_memory.get(record_id) for record_id in sorted(army))
    return [record for record in remembered if record is not None and world.tick - record.last_seen_tick <= recent]


def _middle(army: Sequence[EnemyRecord]) -> tuple[float, float]:
    """The centroid of some enemy units."""
    return centroid(army)


def _cost(record: EnemyRecord, world: World) -> int:
    facts = world.items.get(record.type)
    return facts.cost if facts is not None else 0
