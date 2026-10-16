"""Reinforcements held home: while main is away, the units made since wait at home and go to it only as a group.

Units made while main is away were sent after it in small parcels: the plan's reinforcements at 1,000, forward
staging's joiners at 1,000 or after 30 s, the commander gate's joiners four at a time or after 30 s. Each parcel met
the enemy alone on its way and was crushed. With `reinforce_hold_home` of `[combat]` on they wait at home instead.

**Away.** The home gather point is the forward hold's gather point while it publishes one, else the staging point.
main is away while its army's centroid (the combat manager's units gone forward, or main's units while the model
commander commands it) lies more than reinforce_away_cells from the home gather point: on an attack, a flank or
committed at the enemy base.

**Held home.** While main is away, the units joining it (the plan's waiting units, forward staging's joiners and the
commander gate's joiners) are held at the home gather point: the combat manager gathers them there as main gathers
(idle units moved there, those in contact firing at what is within their weapon range), and base defense uses them as
it uses main. None of them is sent toward main while those gathered are worth less than reinforce_group_min_value
together: neither reinforcement_strength, nor `command_gate.JOIN_GROUP`, nor any wait sends them.

**A group of their own.** Once the held units within gathered_cells of `[combat]` of the home gather point are worth
reinforce_group_min_value together, they set out for main as one group: with stepped_advance on by steps (see
`stepping`, kind advance, the group's own stepped move to main's centroid as it stands at each release, met enemies
and catches on the way fought or stepped back from as main's are), else by one `attack_move` to main's centroid, sent
again at most every `RESEND_SECONDS`. A unit of the group within gathered_cells of `[raids]` of main's centroid has
joined main, and is main's from then on.

**Army back.** Once main's centroid comes back within reinforce_away_cells of the home gather point, the units joining
it join by the rules of their own path again, and a group on its way is one no more.

**Records.** A `combat` event of reason `reinforce` and phase `held` is written in the first cycle units are held
(their count, what they are worth and main's distance from home), and one of phase `sent` when a group sets out (its
count and what it is worth).

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, stepping
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.fight import Point, is_near, strength
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.world import Cell, Unit, World

COMBAT_EVENT = "combat"
REINFORCE = "reinforce"
HELD = "held"
SENT = "sent"
# What tells the group's stepped move apart from main's.
GROUP_KEY = ("reinforce", "group")
# Without stepped_advance, the group's attack-move to main's centroid is sent again at most this often.
RESEND_SECONDS = 5
_RESEND_TICKS = RESEND_SECONDS * TICKS_PER_SECOND
_DISTANCE_DECIMALS = 1


@dataclass(frozen=True)
class Verdict:
    """The units joining main in a cycle while it is away: those held home, those of a group on its way to main, the
    ids of the group's units that joined main in the cycle, and main's centroid cell, the group's destination."""

    held: list[Unit]
    group: list[Unit]
    joined: frozenset[int]
    target: Cell


def home_point(gather_point: Cell | None, staging_point: Cell) -> Cell:
    """The home gather point: the forward hold's gather point while it publishes one, else the staging point."""
    return staging_point if gather_point is None else gather_point


def is_away(main_center: Point, home: Cell, tuning: Tuning) -> bool:
    """Whether main's centroid lies more than reinforce_away_cells from the home gather point."""
    return not is_near(home, main_center[0], main_center[1], tuning.combat.reinforce_away_cells)


class HomeHold:
    """The units joining main held home while it is away, the group of their own on its way, and its steps. See the
    module's note."""

    def __init__(self) -> None:
        # The ids of the units of the group on its way to main.
        self._group: set[int] = set()
        # Whether units were held in the latest cycle the hold was followed in.
        self._is_holding = False
        # The tick the group was last sent its attack-move; None for none.
        self._sent: int | None = None
        # The group's stepped move and its watch for a catch on the way (see `stepping`).
        self.stepper = stepping.Stepper()
        self.caught = stepping.CaughtWatch()

    def begin_cycle(self) -> None:
        """A cycle starts: the group steps in it only when it is followed."""
        self.stepper.begin_cycle()
        self.caught.begin_cycle()

    def end_cycle(self) -> None:
        """A cycle ends: a group's stepped move not followed in it is over."""
        self.stepper.end_cycle()
        self.caught.end_cycle()

    def follow(
        self, joining: Sequence[Unit], main_center: Point | None, home: Cell, world: World, tuning: Tuning
    ) -> Verdict | None:
        """The units joining main held home and sent on as a group while main is away; None with the switch off, no
        main or main not away, every unit joining by its path's own rules then and no group left on its way."""
        if not tuning.combat.reinforce_hold_home or main_center is None or not is_away(main_center, home, tuning):
            self._group, self._is_holding, self._sent = set(), False, None
            return None
        self._group &= {unit.id for unit in joining}
        reach = tuning.raids.gathered_cells
        joined = frozenset(
            unit.id for unit in joining if unit.id in self._group and is_near(main_center, unit.x, unit.y, reach)
        )
        self._group -= joined
        held = [unit for unit in joining if unit.id not in self._group and unit.id not in joined]
        held = self._set_out(held, home, world, tuning)
        if held and not self._is_holding:
            _log(HELD, world.tick, len(held), strength(held, world), distance=cells.distance(home, main_center))
        self._is_holding = bool(held)
        group = [unit for unit in joining if unit.id in self._group]
        return Verdict(held, group, joined, cells.nearest_cell(*main_center))

    def _set_out(self, held: list[Unit], home: Cell, world: World, tuning: Tuning) -> list[Unit]:
        """Set the held units gathered home out as a group once they are worth reinforce_group_min_value together,
        with a `sent` event; the units still held."""
        there = [unit for unit in held if is_near(home, unit.x, unit.y, tuning.combat.gathered_cells)]
        value = strength(there, world)
        if not there or value < tuning.combat.reinforce_group_min_value:
            return held
        self._group.update(unit.id for unit in there)
        self._sent = None
        _log(SENT, world.tick, len(there), value)
        return [unit for unit in held if unit.id not in self._group]

    def attack_moves(self, group: Sequence[Unit], target: Cell, tick: int) -> list[Intent]:
        """The group's attack-move to main's centroid without steps: sent when the group sets out, then again at most
        every RESEND_SECONDS; none otherwise."""
        if not group or (self._sent is not None and tick - self._sent < _RESEND_TICKS):
            return []
        self._sent = tick
        order = {"cmd": "attack_move", "units": [unit.id for unit in group], "to": list(target)}
        return [Intent(Priority.MOVE, order)]


def group_plan(target: Cell) -> stepping.Plan:
    """The group's stepped move to main's centroid."""
    return stepping.Plan(GROUP_KEY, stepping.ADVANCE, target)


def _log(phase: str, tick: int, count: int, value: float, **more: float) -> None:
    """Write one `combat` event of reason `reinforce`."""
    data = {"tick": tick, "reason": REINFORCE, "phase": phase, "count": count, "value": round(value)}
    data.update({key: round(number, _DISTANCE_DECIMALS) for key, number in more.items()})
    records.log(COMBAT_EVENT, data)
