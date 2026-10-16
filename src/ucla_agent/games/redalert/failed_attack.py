"""main's failed attacks remembered, and the gate an attack on an objective passes (`[combat] failed_attack_memory`).

An attack on a decently defended base that is pulled back, or that loses
half of main near its objective, tells what the base holds; sending a force
of about the same size at it again by the same way loses it again. So with
`failed_attack_memory` on the combat manager keeps a memory of main's failed
attacks, published as `MatchContext.failed_attacks`:

- **A failure.** While main's attack is out (see `attack_approach`), main's
  peak strength (cost times share of health) is kept, and whether its
  centroid has come within failed_attack_near_cells of the objective. Once it
  has, the attack failed when the retreat override or main's plan retreat
  pulls main back, or main is down to failed_attack_loss_share of its peak
  or less. The objective, the peak, the enemy weighed there (the enemy units
  that fight and the ground defenses within failed_attack_near_cells of it,
  as `fight.weigh` weighs them), the approach's side and the tick are
  remembered, one failure an attack, with a `combat` event of reason
  `attack_failed`.
- **Forgetting.** A failure is forgotten failed_attack_memory_seconds after
  it, or once every ground defense remembered there is gone from the enemy
  memory (destroyed or seen gone).
- **The gate.** An attack on a cell within failed_attack_block_cells of a
  failure is blocked (`blocking`) unless main's strength is at least
  failed_attack_force_ratio times the failed force and failed_attack_enemy_ratio
  times the enemy remembered there, or it comes by another side (a flank where
  the failure came straight on). With `objective_ratio`, an attack on a base
  is weighed with the ground defenses that reach its objective or the last
  attack_leg_cells of the straight way there, and the enemy units near it: an
  objective under attack_min_objective_ratio is not attacked.

The commander's advance, attack and flank on a blocked cell are not offered,
nor is PRESS on its objective (`MatchContext.press_blocked`); each first
block of a failure is a `combat` event of reason `attack_blocked_failed`
with the numbers.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ucla_agent.core import records
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.command_targets import reaches
from ucla_agent.games.redalert.fight import Point, centroid, ground_defenses, is_counted, strength, weigh
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

COMBAT_EVENT = "combat"
ATTACK_FAILED = "attack_failed"
ATTACK_BLOCKED_FAILED = "attack_blocked_failed"
# Why an attack is blocked: a failure near its objective, or the objective weighed under attack_min_objective_ratio.
FAILED_HERE = "failed_here"
OBJECTIVE_DEFENDED = "objective_defended"
# The side an attack comes by straight on, as the approach of `attack_approach` names it, and a flank's.
DIRECT = "direct"
FLANK = "flank"


@dataclass(frozen=True)
class FailedAttack:
    """An attack of main's that failed: its objective, main's peak strength on it, the enemy weighed there, the side
    it came by, the tick it failed and the ids of the ground defenses remembered near the objective then."""

    cell: Cell
    force: float
    enemy: float
    side: str
    tick: int
    defenses: frozenset[int]


@dataclass(frozen=True)
class Blocked:
    """Why an attack on a cell is blocked: the reason, main's strength, and the figures it fell short of."""

    reason: str
    cell: Cell
    own: float
    needed_force: float = 0.0
    needed_enemy: float = 0.0
    ratio: float | None = None
    failed: FailedAttack | None = None


class AttackMemory:
    """main's failed attacks, and the attack out being watched for a failure. See the module's note."""

    def __init__(self) -> None:
        self.failed: tuple[FailedAttack, ...] = ()
        self._peak = 0.0
        self._was_near = False
        self._is_recorded = False

    def follow(
        self, units: Sequence[Unit], objective: Cell | None, side: str, is_pulled: bool, world: World, ctx: MatchContext
    ) -> None:
        """Bring the memory up to date at the end of a cycle: `objective` the objective of the attack out at the start
        of the cycle (None for none), `is_pulled` whether main is pulled back in this cycle; published on the match
        context. Nothing with failed_attack_memory off."""
        tuning = ctx.tuning.combat
        if not tuning.failed_attack_memory:
            return
        self._forget(world, ctx)
        if objective is None or not units:
            self._peak, self._was_near, self._is_recorded = 0.0, False, False
        else:
            self._watch(units, objective, side, is_pulled, world, ctx)
        ctx.failed_attacks = self.failed

    def _watch(
        self, units: Sequence[Unit], objective: Cell, side: str, is_pulled: bool, world: World, ctx: MatchContext
    ) -> None:
        """Follow the attack out for a failure, remembered once an attack."""
        tuning = ctx.tuning.combat
        value = strength(units, world)
        self._peak = max(self._peak, value)
        middle = cells.nearest_cell(*centroid(units))
        self._was_near = self._was_near or cells.is_within(middle, objective, tuning.failed_attack_near_cells)
        is_lost = value <= self._peak * tuning.failed_attack_loss_share
        if self._is_recorded or not self._was_near or not (is_pulled or is_lost):
            return
        self._is_recorded = True
        enemies, defenses = _enemy_near(objective, world, ctx)
        _, enemy = weigh(list(units), enemies, world, ctx.tuning, defenses)
        failure = FailedAttack(
            objective, self._peak, enemy, side, world.tick, frozenset(defense.id for defense in defenses)
        )
        self.failed = (*self.failed, failure)
        _log(
            ATTACK_FAILED,
            world.tick,
            cell=list(objective),
            force=round(self._peak),
            left=round(value),
            enemy=round(enemy),
            side=side,
            pulled=is_pulled,
        )

    def _forget(self, world: World, ctx: MatchContext) -> None:
        """Forget the failures that are old, or whose ground defenses are all gone from the enemy memory."""
        keep_ticks = ctx.tuning.combat.failed_attack_memory_seconds * TICKS_PER_SECOND
        memory = world.enemy_memory
        self.failed = tuple(
            failure
            for failure in self.failed
            if world.tick - failure.tick < keep_ticks
            and (not failure.defenses or any(defense in memory for defense in failure.defenses))
        )


def blocking(
    cell: Cell, units: Sequence[Unit], side: str, world: World, ctx: MatchContext, weighs_objective: bool = True
) -> Blocked | None:
    """Why an attack of `units` on `cell` by `side` is blocked (see the module's note), with `weighs_objective` also
    by the objective's weighing; None while it is not, and always with failed_attack_memory off."""
    tuning = ctx.tuning.combat
    if not tuning.failed_attack_memory or not units:
        return None
    own = strength(units, world)
    for failure in ctx.failed_attacks:
        if not cells.is_within(cell, failure.cell, tuning.failed_attack_block_cells) or _is_other_side(side, failure):
            continue
        force, enemy = (
            tuning.failed_attack_force_ratio * failure.force,
            tuning.failed_attack_enemy_ratio * failure.enemy,
        )
        if own < force or own < enemy:
            return Blocked(FAILED_HERE, cell, own, force, enemy, failed=failure)
    ratio = objective_ratio(cell, units, world, ctx) if weighs_objective else None
    if ratio is not None and ratio < tuning.attack_min_objective_ratio:
        return Blocked(OBJECTIVE_DEFENDED, cell, own, ratio=ratio)
    return None


def note_blocked(blocked: Blocked, world: World, user: str, logged: set[object]) -> None:
    """Write the first block of a failure, or of a defended objective's cell, as a `combat` event of reason
    `attack_blocked_failed`; `logged` holds the failures and cells whose first block was written."""
    key = blocked.failed or (blocked.reason, blocked.cell)
    if key in logged:
        return
    logged.add(key)
    _log(
        ATTACK_BLOCKED_FAILED,
        world.tick,
        user=user,
        why=blocked.reason,
        cell=list(blocked.cell),
        own=round(blocked.own),
        needed_force=round(blocked.needed_force),
        needed_enemy=round(blocked.needed_enemy),
        ratio=None if blocked.ratio is None else round(blocked.ratio, 2),
    )


def objective_ratio(cell: Cell, units: Sequence[Unit], world: World, ctx: MatchContext) -> float | None:
    """main's strength over the enemy at an objective: the enemy units that fight within failed_attack_near_cells of it
    and the ground defenses that reach it or the last attack_leg_cells of the straight way from main's centroid; None
    while there is no such enemy."""
    enemies = _units_near(cell, world, ctx.tuning.combat.failed_attack_near_cells)
    leg = _last_leg(centroid(units), cell, ctx.tuning.combat.attack_leg_cells)
    defenses = [
        defense for defense in ground_defenses(world) if any(reaches(defense, point, world, ctx) for point in leg)
    ]
    own, enemy = weigh(list(units), enemies, world, ctx.tuning, defenses)
    return own / enemy if enemy > 0 else None


def _is_other_side(side: str, failure: FailedAttack) -> bool:
    """Whether an attack by `side` comes another way than the failure did: a flank where it came straight on."""
    return side == FLANK and failure.side == DIRECT


def _enemy_near(cell: Cell, world: World, ctx: MatchContext) -> tuple[list[EnemyRecord], list[EnemyRecord]]:
    """The enemy units that fight and the ground defenses within failed_attack_near_cells of a cell."""
    reach = ctx.tuning.combat.failed_attack_near_cells
    defenses = [defense for defense in ground_defenses(world) if cells.is_within((defense.x, defense.y), cell, reach)]
    return _units_near(cell, world, reach), defenses


def _units_near(cell: Cell, world: World, reach: int) -> list[EnemyRecord]:
    """The remembered enemy units that fight and count (not stale) within `reach` of a cell."""
    return [
        record
        for record in world.enemy_memory.values()
        if not record.is_building
        and is_counted(record)
        and is_combat_unit(record)
        and cells.is_within((record.x, record.y), cell, reach)
    ]


def _last_leg(start: Point, end: Cell, length: int) -> list[Point]:
    """The points a cell apart on the straight way from `start` to `end` within `length` of `end`, `end` included."""
    span = cells.distance(cells.nearest_cell(*start), end)
    steps = min(length, int(span))
    if span <= 0:
        return [end]
    return [
        (end[0] + (start[0] - end[0]) * step / span, end[1] + (start[1] - end[1]) * step / span)
        for step in range(steps + 1)
    ]


def _log(reason: str, tick: int, **data: object) -> None:
    """Write one `combat` event of a reason."""
    records.log(COMBAT_EVENT, {"tick": tick, "reason": reason, **data})
