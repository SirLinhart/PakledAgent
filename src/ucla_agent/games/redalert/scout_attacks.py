"""The slot scouts' own attacks: what a careful scout fires at on purpose.

A careful scout holds fire and is given no attack by any manager (see
`scout_stance`), but for the attacks the scout slots' own rules order for it:

- the bait shot of a retreat begun (see `scout_bait`);
- the hunt of a lone enemy unit it clearly outfights (see `scout_hunt`);
- the harass of an enemy base with no defense and no army by it (see
  `scout_harass`).

Each is an `attack` order on one enemy at the scouting priority, as urgent as
a flight, and counts within scout_orders_per_batch. It is marked as the slots'
own (`Intent.scout_rule`), so that the attack strip of the careful scouts keeps
it while it still strips every other manager's attack on a scout. While one of
these holds a scout, the attack is its order of the cycle in place of its
move, and while its move is held back after a bait shot, or a hunt's or a
harass's attack is not due again, it is sent none.

A scout that hunts or harasses is engaged: it takes no retreat cause (see
`scout_retreat`) until that ends, and the rule that ends it says whether a
retreat follows, with or without the bait shot. A scout that may begin either
hunts first. Each hunt begun is a `scout_slot` event of kind `hunt` with the
target's id and type and the scout's cell, and each hunt ended one of kind
`hunt_end` with the target's id and why it ended. Each harass begun is one of
kind `harass` with the scout's cell and its first target's id and type, and
each harass ended one of kind `harass_end` with why and the count of attack
orders it made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert import scout_harass, scout_hunt
from ucla_agent.games.redalert.scout_bait import BaitShots
from ucla_agent.games.redalert.scout_harass import Harasses
from ucla_agent.games.redalert.scout_hunt import Hunts
from ucla_agent.games.redalert.scout_steering import FLEE_URGENCY, Move
from ucla_agent.games.redalert.world import EnemyRecord, Unit, World

# The kinds of the `scout_slot` events of a hunt begun and of a hunt ended, and of a harass begun and ended.
HUNT = "hunt"
HUNT_END = "hunt_end"
HARASS = "harass"
HARASS_END = "harass_end"


@dataclass(frozen=True)
class Note:
    """A `scout_slot` event about a slot's scout for the slots to write: its kind, the slot and what more it gives."""

    kind: str
    slot: int
    fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Handover:
    """A scout that one of these attacks hands over to a retreat as it ends: its slot, the retreat's cause, and whether
    the retreat takes the bait shot."""

    slot: int
    cause: str
    baits: bool


class ScoutAttacks:
    """The attacks of one match's slot scouts. See the module's note."""

    def __init__(self) -> None:
        self._bait = BaitShots()
        self._hunts = Hunts()
        self._harasses = Harasses()
        # The events of the cycle for the slots to write, in the order they happened.
        self._notes: list[Note] = []

    @property
    def engaged(self) -> frozenset[int]:
        """The scouts these attacks send at the enemy on purpose, which take no retreat cause meanwhile."""
        return self._hunts.hunting | self._harasses.harassing

    def end(
        self, scouts: Mapping[int, Unit], last_hp: Callable[[int], int | None], world: World, ctx: MatchContext
    ) -> list[Handover]:
        """End the hunts and harasses of the slots' scouts in the field (`scouts`, by slot number) whose end has come,
        by each scout's health in the last state (`last_hp`), each with its `hunt_end` or `harass_end` note; the scouts
        handed over to a retreat, which takes the bait shot after a harass and not after a hunt."""
        handovers = []
        for hunted in self._hunts.end(scouts, world, ctx):
            fields = {"target_id": hunted.hunt.target_id, "reason": hunted.reason}
            self._notes.append(Note(HUNT_END, hunted.slot, fields))
            if hunted.reason in scout_hunt.RETREAT_CAUSES:
                handovers.append(Handover(hunted.slot, scout_hunt.RETREAT_CAUSES[hunted.reason], baits=False))
        for harassed in self._harasses.end(scouts, last_hp, world, ctx):
            fields = {"reason": harassed.reason, "orders": harassed.harass.orders}
            self._notes.append(Note(HARASS_END, harassed.slot, fields))
            if harassed.reason in scout_harass.RETREAT_CAUSES:
                handovers.append(Handover(harassed.slot, scout_harass.RETREAT_CAUSES[harassed.reason], baits=True))
        return handovers

    def bait(self, scout: Unit, world: World, ctx: MatchContext) -> EnemyRecord | None:
        """The target of the bait shot of a scout whose retreat begins in this cycle; None when it makes none (see
        `scout_bait`)."""
        return self._bait.shoot(scout, world, ctx)

    def start(self, scouts: Mapping[int, Unit], world: World, ctx: MatchContext) -> None:
        """Begin the hunts and the harasses of the scouts free to begin one (`scouts`, by slot number: in the field, not
        retreating, not the rush scout while its rule steers it), a hunt before a harass, each with its note."""
        for number, scout in scouts.items():
            if scout.id in self.engaged:
                continue
            hunt = self._hunts.start(scout, world, ctx)
            if hunt is not None:
                fields = {"target_id": hunt.target_id, "target_type": hunt.target_type, "cell": [scout.x, scout.y]}
                self._notes.append(Note(HUNT, number, fields))
                continue
            harassed = self._harasses.start(scout, world, ctx)
            if harassed is not None:
                target = harassed[1]
                fields = {"cell": [scout.x, scout.y], "target_id": target.id, "target_type": target.type}
                self._notes.append(Note(HARASS, number, fields))

    def moves(self, scouts: Mapping[int, Unit], world: World, ctx: MatchContext) -> dict[int, Move | None]:
        """By slot number, the scouts in the field (`scouts`, by slot number) whose order of the cycle is one of these
        attacks, with the attack's move; None for a scout these attacks hold that is sent nothing in this cycle: its
        move held back after its bait shot, or its hunt's or harass's attack not due."""
        moves: dict[int, Move | None] = {}
        for number, scout in scouts.items():
            target = self._due(scout, world)
            if target is not None:
                moves[number] = attack_move(target)
            elif self._bait.holds_back(scout.id, world, ctx) or scout.id in self.engaged:
                moves[number] = None
        return moves

    def sent(self, unit_id: int, tick: int) -> None:
        """Note that a scout's attack order of these went out at a tick."""
        self._hunts.ordered(unit_id, tick)
        self._harasses.ordered(unit_id, tick)

    def _due(self, scout: Unit, world: World) -> EnemyRecord | None:
        """The target of the attack a scout is to be sent in this cycle: its bait shot's, its hunt's or its harass's;
        None for none."""
        return self._bait.shot_now(scout.id, world) or self._hunts.due(scout, world) or self._harasses.due(scout, world)

    def notes(self) -> list[Note]:
        """The events of the cycle for the slots to write, once each."""
        notes, self._notes = self._notes, []
        return notes


def attack_move(target: EnemyRecord) -> Move:
    """The move of a scout ordered to attack an enemy: at the enemy's cell, as urgent as a flight."""
    return Move((target.x, target.y), FLEE_URGENCY, target=target.id)


def scout_order(unit_id: int, move: Move) -> Intent:
    """The order a scout's move of the cycle stands for, at the scouting priority: an attack on the move's target,
    marked as the slots' own, when it has one; else a move to its cell."""
    if move.target is not None:
        return Intent(Priority.SCOUT, {"cmd": "attack", "units": [unit_id], "target": move.target}, scout_rule=True)
    return Intent(Priority.SCOUT, {"cmd": "move", "units": [unit_id], "to": list(move.cell)})
