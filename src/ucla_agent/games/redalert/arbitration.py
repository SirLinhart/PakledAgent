"""Leaving out orders that would change nothing.

A manager says in every cycle where it wants its units, which is the simple
way to write one: it need not remember what it said before. Sent as they are,
those intents would tell a marching group to march to the same cell five
times a second. Each repeat costs a place in a batch of at most 20 orders,
makes the engine plan the unit's path anew, and buries the orders that do
change something in the match record.

`RepeatFilter` stands between the managers and the batch. It remembers the
last order that gave each unit something to do, and takes a unit out of a
movement or attack order when that last order says the same and may still be
in force: it was neither rejected nor lost, and is recent. A unit that stands
idle is always ordered again, whatever it was told before: the order has run
its course or come to nothing.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping

from ucla_agent.games.redalert.batch import ACTIVITY_COMMANDS, Intent
from ucla_agent.games.redalert.ledger import LOST, REJECTED, LedgerEntry
from ucla_agent.games.redalert.world import World

# The orders that send a unit somewhere or at something, which are the ones a manager repeats.
MOVEMENT_COMMANDS = frozenset({"move", "attack_move", "attack"})
# The verdicts after which an order is known not to be in force.
_NOT_IN_FORCE = (REJECTED, LOST)


class RepeatFilter:
    """Takes out of a cycle's intents what each unit was already told. See the module's note."""

    def __init__(self, reissue_after_ticks: int) -> None:
        """An order older than `reissue_after_ticks` no longer keeps the same order from being sent again."""
        self._reissue_after_ticks = reissue_after_ticks
        # Unit id -> the last order recorded in the ledger that gave the unit something to do.
        self._last_order: dict[int, LedgerEntry] = {}

    @property
    def last_orders(self) -> Mapping[int, LedgerEntry]:
        """Unit id -> the last order recorded that gave the unit something to do; the filter's own record, which
        grows as batches are sent."""
        return self._last_order

    def note_sent(self, entries: Iterable[LedgerEntry]) -> None:
        """Take in the orders of a batch that the ledger has just recorded as sent."""
        for entry in entries:
            if entry.order["cmd"] in ACTIVITY_COMMANDS:
                for unit_id in entry.order["units"]:
                    self._last_order[unit_id] = entry

    def without_repeats(self, intents: Iterable[Intent], world: World) -> list[Intent]:
        """The intents, each movement or attack order narrowed to the units it tells something new.

        An order left with no unit is dropped; every other intent comes back
        as it was given. So does the model commander's order: the commander
        sends it again only when it is due (see `commander`).
        """
        kept = []
        for intent in intents:
            order = intent.order
            if order.get("cmd") not in MOVEMENT_COMMANDS or intent.commander:
                kept.append(intent)
                continue
            units = [unit_id for unit_id in order["units"] if not self._holds(unit_id, order, world)]
            if len(units) == len(order["units"]):
                kept.append(intent)
            elif units:
                kept.append(replace(intent, order={**order, "units": units}))
        return kept

    def _holds(self, unit_id: int, order: Mapping[str, Any], world: World) -> bool:
        """Whether a unit is busy with an order that says what `order` says and may still be in force."""
        last = self._last_order.get(unit_id)
        if last is None or last.status in _NOT_IN_FORCE or _aim(last.order) != _aim(order):
            return False
        unit = world.units.get(unit_id)
        is_idle = unit is not None and unit.idle
        return not is_idle and world.tick - last.sent_tick < self._reissue_after_ticks


def _aim(order: Mapping[str, Any]) -> tuple[Any, ...]:
    """What an order has a unit do: the command, its cell or target, and whether it is added to the unit's plan."""
    cell = order.get("to")
    return order["cmd"], None if cell is None else tuple(cell), order.get("target"), bool(order.get("queued"))
