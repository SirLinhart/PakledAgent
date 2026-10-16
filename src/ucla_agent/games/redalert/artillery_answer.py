"""Our units under an enemy artillery piece or V2 close in on it or leave its range (`[combat] artillery_answer`).

Enemy artillery shells from 12 cells (a V2 from 10), beyond the reach focus
fire gives a unit's candidates (its weapon range and focus_unit_reach_cells),
so tank_charge_artillery, which acts on a target already given, never got
one, and the fight estimate weighs a piece's long range so heavily that the
retreat override, a met step back and the plan's retreat pulled main away
from a piece standing alone. Units at the staging point, holding or waiting
for a step, stood idle under its shells. So with `artillery_answer` on, the
combat manager answers each enemy artillery piece and V2 in sight, before
the commander's question and main's orders, with the own ground units (main's
on no job of their own, and the home guard; none on a crush run, walking a
route or taken by base defense) within its weapon range and
artillery_leave_margin_cells and 2 more of it, each with the nearest piece:

- **Attack.** A unit on the ground that can hurt the piece (artillery and V2
  only within their own range) is given an `attack` on it, whatever the
  fight's ratio: that is the piece's to lose. A move after its cell, which
  shifts a cell or two as it fires and rolls, had the units walk to and fro.
- **Leave.** Any other answering unit within the piece's weapon range and a
  cell, but one of main's met fight (see `stepping`), moves straight away
  from it to artillery_leave_margin_cells beyond its range, and stays
  answered there, so that no gather, hold or step wait walks it back under
  the shells.

**Out of sight.** With `artillery_unseen_answer` on, a unit hit (its hp
fallen since the cycle before) with no enemy in sight whose weapon range and
SHOOTER_MARGIN_CELLS reach it answers the nearest artillery piece or V2 seen within
artillery_memory_seconds within artillery_memory_cells of it as if it stood
on its last cell (a vehicle charges onto the cell, any other unit in its
range leaves it): a vehicle charges a piece no known enemy ground defense
covers, infantry too while the piece is unescorted (no enemy unit that
fights at short range within artillery_escort_cells of it). With none
remembered near, once an enemy piece has been in
sight in the match, the hit units step
artillery_unseen_step_cells straight away from the enemy's start, out of a
shooter's likely range, and one vehicle steps as far toward it to see the
shooter. Such an answer holds artillery_unseen_hold_seconds after the hit.

An answering unit takes no order of main's or the commander's in the cycle;
a crush run keeps its precedence (its tanks leave the answer). An order is
sent once and again only when its command or target changes; a move once
its cell moved RESEND_MOVE_CELLS or RESEND_MOVE_SECONDS after it. Nothing here reads a file, a
clock or the network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, coverage
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.fight import can_hurt, ground_defenses, is_counted
from ucla_agent.games.redalert.groups import SIEGE_TYPES
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.production import VEHICLE_QUEUE
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.targeting import enemies_in_sight
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

COMBAT_EVENT = "combat"
# A unit's answer move is sent again once its cell moved this far, or this long after it was sent.
RESEND_MOVE_CELLS = 3
RESEND_MOVE_SECONDS = 3
ARTILLERY_ANSWER = "artillery_answer"
ARTILLERY_UNSEEN = "artillery_unseen"
# An enemy in sight this much beyond its weapon range of a unit hit may have shot it, as in contact (see `targeting`).
SHOOTER_MARGIN_CELLS = 2
# An answering unit is this much farther out than the leave distance at most.
ANSWER_EXTRA_CELLS = 2


@dataclass
class ArtilleryAnswer:
    """The units answering enemy artillery in the latest cycle, and the aim each was last sent at."""

    units: frozenset[int] = frozenset()
    # Unit id -> (command, aim) of the order it was last sent.
    _sent: dict[int, tuple[str, object]] = field(default_factory=dict)
    # Unit id -> its hp in the latest cycle, for the hits of a shooter out of sight.
    _hp: dict[int, int] = field(default_factory=dict)
    # Unit id -> the tick until which its answer to a shooter out of sight holds, and that answer.
    _held: dict[int, tuple[int, tuple[str, object]]] = field(default_factory=dict)
    # Unit id -> the tick its last order was sent at.
    _sent_tick: dict[int, int] = field(default_factory=dict)
    # Whether an enemy artillery piece or V2 has been in sight in this match.
    _has_seen_artillery: bool = False

    def follow(
        self,
        candidates: Sequence[Unit],
        world: World,
        ctx: MatchContext,
        others: Sequence[Unit] = (),
        fighting: frozenset[int] = frozenset(),
    ) -> list[Intent]:
        """The orders of this cycle's answer (see the module's note), `others` the own ground units on a job of their
        own that answer a shooter out of sight alone; none with artillery_answer off."""
        tuning = ctx.tuning.combat
        if not tuning.artillery_answer:
            self.units, self._sent, self._hp, self._held = frozenset(), {}, {}, {}
            return []
        everyone = [*candidates, *others]
        hit = {unit.id for unit in everyone if unit.hp < self._hp.get(unit.id, unit.hp)}
        self._hp = {unit.id: unit.hp for unit in everyone}
        pieces = [record for record in enemies_in_sight(world) if record.type in SIEGE_TYPES]
        shown = pieces or [record for record in world.enemy_memory.values() if record.type in SIEGE_TYPES]
        self._has_seen_artillery = self._has_seen_artillery or bool(shown)
        answers = {}
        for unit in candidates:
            piece = _nearest_in_reach(unit, pieces, world, ctx)
            answer = None if piece is None else _answer(unit, piece, world, ctx, fighting)
            if answer is not None:
                answers[unit.id] = answer
        seen = set(answers)
        if tuning.artillery_unseen_answer:
            rest = [unit for unit in everyone if unit.id not in answers]
            answers.update(self._unseen(rest, hit, world, ctx))
        self.units = frozenset(answers)
        self._sent = {unit_id: aim for unit_id, aim in self._sent.items() if unit_id in answers}
        fresh = {unit_id: answer for unit_id, answer in answers.items() if self._is_fresh(unit_id, answer, world)}
        self._sent.update(fresh)
        self._sent_tick.update(dict.fromkeys(fresh, world.tick))
        _log_fresh(fresh, seen, world)
        # An answer to a shooter out of sight is an emergency: base defense's and a strike's orders give way to it.
        return [
            _order(unit_id, command, aim, Priority.CONTACT if unit_id in seen else Priority.EMERGENCY)
            for unit_id, (command, aim) in sorted(fresh.items())
        ]

    def _unseen(
        self, units: Sequence[Unit], hit: set[int], world: World, ctx: MatchContext
    ) -> dict[int, tuple[str, object]]:
        """With artillery_unseen_answer on, the answers to a shooter out of sight: of each unit hit in this cycle with
        no enemy in sight whose weapon reaches it, and of those whose answer still holds (see the module's note)."""
        tuning = ctx.tuning.combat
        hold_until = world.tick + tuning.artillery_unseen_hold_seconds * TICKS_PER_SECOND
        guessing = []
        for unit in units:
            if unit.id not in hit or _is_shot_in_sight(unit, world):
                continue
            piece = _remembered_piece(unit, world, ctx)
            answer = None if piece is None else _answer_unseen(unit, piece, world, ctx)
            if answer is None:
                guessing.append(unit)
            else:
                self._held[unit.id] = (hold_until, answer)
        # A hit out of sight is taken for artillery only once the enemy has shown some.
        for unit_id, answer in _guesses(guessing if self._has_seen_artillery else [], world, ctx).items():
            self._held[unit_id] = (hold_until, answer)
        ids = {unit.id for unit in units}
        self._held = {unit_id: held for unit_id, held in self._held.items() if held[0] > world.tick and unit_id in ids}
        return {unit_id: answer for unit_id, (_, answer) in self._held.items()}

    def _is_fresh(self, unit_id: int, answer: tuple[str, object], world: World) -> bool:
        """Whether an answer is sent: one of another command or target; a move only once its cell moved
        RESEND_MOVE_CELLS or more from the last one sent, or RESEND_MOVE_SECONDS after it."""
        sent = self._sent.get(unit_id)
        if sent is None or sent[0] != answer[0] or answer[0] != "move":
            return sent != answer
        is_far = not cells.is_within(tuple(answer[1]), tuple(sent[1]), RESEND_MOVE_CELLS)
        return is_far or world.tick - self._sent_tick.get(unit_id, world.tick) >= RESEND_MOVE_SECONDS * TICKS_PER_SECOND

    def without(self, unit_ids: frozenset[int]) -> None:
        """Let go of units another job took in this cycle (a crush run), which are sent afresh when answered again."""
        self.units -= unit_ids
        self._sent = {unit_id: aim for unit_id, aim in self._sent.items() if unit_id not in unit_ids}
        self._held = {unit_id: held for unit_id, held in self._held.items() if unit_id not in unit_ids}


def _is_shot_in_sight(unit: Unit, world: World) -> bool:
    """Whether an enemy in sight that fights stands within its weapon range and SHOOTER_MARGIN_CELLS of a unit."""
    damage = world.items.damage
    return any(
        damage.get(record.type).range > 0
        and _distance(unit, record) <= damage.get(record.type).range + SHOOTER_MARGIN_CELLS
        for record in enemies_in_sight(world)
    )


def _remembered_piece(unit: Unit, world: World, ctx: MatchContext) -> EnemyRecord | None:
    """The nearest enemy artillery piece or V2 out of sight, seen within artillery_memory_seconds, within
    artillery_memory_cells of a unit, taken to stand where it was last seen; None for none."""
    tuning = ctx.tuning.combat
    fresh = tuning.artillery_memory_seconds * TICKS_PER_SECOND
    near = [
        record
        for record in world.enemy_memory.values()
        if record.type in SIEGE_TYPES
        and not record.visible
        and world.tick - record.last_seen_tick <= fresh
        and _distance(unit, record) <= tuning.artillery_memory_cells
    ]
    return min(near, key=lambda record: (_distance(unit, record), record.id), default=None)


def _answer_unseen(unit: Unit, piece: EnemyRecord, world: World, ctx: MatchContext) -> tuple[str, object] | None:
    """A unit's answer to a piece out of sight at its last cell: a vehicle charges onto that cell, any other unit
    within its range and a cell leaves it; no attack, which wants its target in sight."""
    if unit.type not in SIEGE_TYPES and _is_vehicle(unit.type, world) and _may_charge(unit, piece, world, ctx):
        return "move", (piece.x, piece.y)
    if _distance(unit, piece) > _range(piece, world) + 1:
        return None
    return "move", _away(unit, piece, world, ctx)


def _guesses(units: Sequence[Unit], world: World, ctx: MatchContext) -> dict[int, tuple[str, object]]:
    """The units hit by a shooter out of sight with no piece remembered near: each steps artillery_unseen_step_cells
    straight away from the enemy's start, out of its likely range, but one vehicle, which steps as far toward it to
    see the shooter."""
    if not units or world.enemy_base is None:
        return {}
    step = ctx.tuning.combat.artillery_unseen_step_cells
    vehicles = [unit for unit in units if _is_vehicle(unit.type, world) and unit.type not in SIEGE_TYPES]
    scout = min(vehicles, key=lambda unit: unit.id, default=None)
    return {unit.id: ("move", _stepped(unit, world, step if unit is scout else -step)) for unit in units}


def _stepped(unit: Unit, world: World, step: float) -> Cell:
    """The cell `step` cells from a unit toward the enemy's start (away from it for a negative step), in the map."""
    target = world.enemy_base
    span = math.hypot(target[0] - unit.x, target[1] - unit.y) or 1.0
    cell = cells.nearest_cell(unit.x + (target[0] - unit.x) / span * step, unit.y + (target[1] - unit.y) / span * step)
    return cell if world.map_bounds is None else cells.clamped(cell, world.map_bounds)


def _nearest_in_reach(unit: Unit, pieces: Sequence[EnemyRecord], world: World, ctx: MatchContext) -> EnemyRecord | None:
    """The nearest piece within its weapon range, artillery_leave_margin_cells and ANSWER_EXTRA_CELLS of a unit."""
    margin = ctx.tuning.combat.artillery_leave_margin_cells + ANSWER_EXTRA_CELLS
    near = [piece for piece in pieces if _distance(unit, piece) <= _range(piece, world) + margin]
    return min(near, key=lambda piece: (_distance(unit, piece), piece.id), default=None)


def _answer(
    unit: Unit, piece: EnemyRecord, world: World, ctx: MatchContext, fighting: frozenset[int] = frozenset()
) -> tuple[str, object] | None:
    """A unit's answer to a piece in sight, as (command, aim): an attack on it by a unit on the ground that can hurt it
    (artillery and V2 within their range), a move out of its range for any other within it and a cell; None for one
    out of its range, or in a fight of main's (`fighting`). Moves after its cell, which shifts as it fires and rolls,
    had the units walk to and fro without firing."""
    hurts = not world.items.damage.get(unit.type).air and can_hurt(unit.type, piece.type, world)
    if hurts and (unit.type not in SIEGE_TYPES or _distance(unit, piece) <= _range_of(unit.type, world)):
        return "attack", piece.id
    if unit.id in fighting or _distance(unit, piece) > _range(piece, world) + 1:
        return None
    return "move", _away(unit, piece, world, ctx)


def _may_charge(unit: Unit, piece: EnemyRecord, world: World, ctx: MatchContext) -> bool:
    """Whether a unit charges a piece: one on the ground that can hurt it, the piece covered by no known enemy ground
    defense, and the unit a vehicle or the piece unescorted."""
    if world.items.damage.get(unit.type).air or not can_hurt(unit.type, piece.type, world):
        return False
    if coverage.is_covered((piece.x, piece.y), ground_defenses(world), world.items, ctx.tuning.coverage):
        return False
    return _is_vehicle(unit.type, world) or not _is_escorted(piece, world, ctx)


def _is_escorted(piece: EnemyRecord, world: World, ctx: MatchContext) -> bool:
    """Whether an enemy unit that fights at short range (a ground combat unit but artillery and V2) stands within
    artillery_escort_cells of a piece."""
    reach = ctx.tuning.combat.artillery_escort_cells
    return any(
        record.id != piece.id
        and not record.is_building
        and is_counted(record)
        and is_combat_unit(record)
        and record.type not in SIEGE_TYPES
        and not world.items.damage.get(record.type).air
        and cells.is_within((record.x, record.y), (piece.x, piece.y), reach)
        for record in world.enemy_memory.values()
    )


def _away(unit: Unit, piece: EnemyRecord, world: World, ctx: MatchContext) -> Cell:
    """The cell straight away from a piece, artillery_leave_margin_cells beyond its weapon range, inside the map."""
    span = _distance(unit, piece) or 1.0
    reach = _range(piece, world) + ctx.tuning.combat.artillery_leave_margin_cells
    x = piece.x + (unit.x - piece.x) / span * reach
    y = piece.y + (unit.y - piece.y) / span * reach
    cell = cells.nearest_cell(x, y)
    return cell if world.map_bounds is None else cells.clamped(cell, world.map_bounds)


def _order(unit_id: int, command: str, aim: object, priority: Priority) -> Intent:
    """The intent of one unit's answer."""
    if command == "attack":
        return Intent(priority, {"cmd": "attack", "units": [unit_id], "target": aim})
    return Intent(priority, {"cmd": "move", "units": [unit_id], "to": list(aim)})


def _log_fresh(fresh: dict[int, tuple[str, object]], seen: set[int], world: World) -> None:
    """Write the answers sent in this cycle as `combat` events: reason artillery_answer for those to a piece in sight,
    artillery_unseen for those to a shooter out of sight, each with its units and their orders."""
    for reason, ids in ((ARTILLERY_ANSWER, seen), (ARTILLERY_UNSEEN, set(fresh) - seen)):
        sent = [
            [unit_id, command, aim if isinstance(aim, int) else list(aim)]
            for unit_id, (command, aim) in sorted(fresh.items())
            if unit_id in ids
        ]
        if sent:
            records.log(COMBAT_EVENT, {"tick": world.tick, "reason": reason, "orders": sent})


def _is_vehicle(unit_type: str, world: World) -> bool:
    """Whether the War Factory's queue makes a type, by the item table."""
    facts = world.items.get(unit_type)
    return facts is not None and facts.queue == VEHICLE_QUEUE


def _range(piece: EnemyRecord, world: World) -> float:
    return _range_of(piece.type, world)


def _range_of(unit_type: str, world: World) -> float:
    return world.items.damage.get(unit_type).range


def _distance(unit: Unit, piece: EnemyRecord) -> float:
    return math.hypot(unit.x - piece.x, unit.y - piece.y)
