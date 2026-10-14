"""The agent's account of every order it has sent, from sending to outcome.

The platform answers a batch twice. At once it says, order by order, whether
the order was refused or sent on to the game (`pending`), and it may refuse
the whole batch. What the game then made of a sent order arrives later, in
the `last_orders` and `recent_order_problems` of the states that follow. An
order awaiting that verdict is not done: it may still be rejected, or lost on
the way. The ledger keeps each order with its status until the verdict is in,
so that the rest of the agent can tell what it has asked for from what it has
got.

A production order needs more care than most, because the platform queues
every `build` and `train` it is sent: repeat one before the first shows in
the queue, and the item is made twice. So a sent production order holds a
reservation until it is seen to have taken effect or to have failed, and
while it does, `admit` lets no order through that would repeat it.

An order the platform lost never reached the game, so the ledger proposes it
again, once, at the priority it first had. And what a cycle's packing could
not fit into its batch is not forgotten either: the ledger carries those
intents into the next cycle, all but the combat classes' (`DROPPED_CLASSES`),
which the managers propose afresh from each state. Both come back through
`admit`, next to what the agent proposes afresh.

Placing a finished building can fail in a way that repeating does not cure.
The game puts a building near the construction yard when the wanted cell is
unusable, and answers `invalid` only when it finds no free cell near the base
at all. After such an answer the ledger takes the item over: it holds back
every further `place` of it, tries once more itself a little later without a
wanted cell, and when that fails too it has the item cancelled, so that the
finished building does not block its queue for the rest of the match.

One cycle of the agent uses the ledger in this order: `reconcile` with the
new state, `admit` for the intents proposed in the cycle, `record` with what
was packed for sending, and then, when the runtime reports the platform's
answer, `on_action_result`.

At the end of a match the ledger's counters go into the match record as a
`ledger_summary` event: `finalize` writes it, once, however often and from
wherever it is called.

The ledger reads only what it is handed and writes only to the match record,
which queues events without waiting for the disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from operator import attrgetter
from typing import Any, Iterable, Mapping, Sequence

from ucla_agent.core import records
from ucla_agent.games.redalert.batch import DEFAULT_TRAIN_COUNT, DROPPED_CLASSES, Intent, Packed, Priority
from ucla_agent.games.redalert.world import World

PENDING = "pending"
CONFIRMED = "confirmed"
REJECTED = "rejected"
PARTIAL = "partial"
LOST = "lost"
# No verdict came in time; the order is taken to have been carried out.
CONFIRMED_ASSUMED = "confirmed_assumed"
# The status the platform gives a result row -> the status of the order in the ledger. A row that is still
# `pending` is no verdict and changes nothing.
STATUS_OF_PLATFORM_ROW = {"ok": CONFIRMED, "rejected": REJECTED, "partial": PARTIAL, "lost": LOST}
# How long (3 seconds) an order waits for its verdict before it is assumed to have been carried out.
VERDICT_TIMEOUT_TICKS = 75
# The error of a batch the platform refused because of what it held; any other error means it never arrived.
INVALID_ACTION = "INVALID_ACTION"
# One order's entry in the detail of a refused batch: "[0] place: not_ready (powr is not finished)".
_REFUSAL_ROW = re.compile(r"\[(\d+)\] [a-z_]+: ([a-z_]+)")
# The statuses an order can still get a verdict in.
_AWAITING_VERDICT = (PENDING, CONFIRMED_ASSUMED)
# The verdicts after which a production order will never show in a queue.
_FAILED = (REJECTED, LOST)

BUILD = "build"
TRAIN = "train"
PRODUCTION_COMMANDS = frozenset({BUILD, TRAIN})
# How long (3 seconds) a production order holds its reservation when nothing shows that it took effect.
RESERVATION_TIMEOUT_TICKS = 75
# The reason the game gives when it refuses a production order because the queue is full, and how long
# (2 seconds) the ledger then lets nothing more through for that queue.
QUEUE_FULL_REASON = "not_ready"
QUEUE_BLOCK_TICKS = 50
# The events that report a new own actor.
_CREATED_EVENTS = frozenset({"unit_created", "building_created"})

PLACE = "place"
CANCEL = "cancel"
# The reason the game gives when it finds no free cell near the base for a building.
NO_CELL_REASON = "invalid"
# How long (2 seconds) after such an answer the one retry goes out.
PLACE_RETRY_TICKS = 50
# The verdicts and assumptions after which an order counts as carried out.
_CARRIED_OUT = (CONFIRMED, PARTIAL, CONFIRMED_ASSUMED)
_ERROR_SOURCE = "redalert.ledger"
# The phase of a state of a match that is over.
FINISHED_PHASE = "finished"


@dataclass(slots=True)
class LedgerEntry:
    """One order the agent sent, and what became of it."""

    # The ledger's own number for the batch, counted from 1 in the order the batches were sent.
    batch: int
    # The order's place in its batch.
    index: int
    # The state version the batch was sent with, and the game tick of that state.
    state_version: int
    sent_tick: int
    order: Mapping[str, Any]
    priority: Priority
    status: str = PENDING
    # The platform's reason, for an order that was rejected, went through in part or was lost.
    reason: str | None = None
    # The order is one the ledger sent again after the platform lost it; it is not sent a third time.
    resend: bool = False


@dataclass(frozen=True, slots=True)
class Reservation:
    """A production order that was sent and has not yet been seen to take effect or to fail."""

    queue: str
    item: str
    count: int
    # The credits the whole order takes.
    cost: int
    # What the order holds while it is unresolved: a build its queue, a train its item.
    held: tuple[str, str]
    entry: LedgerEntry


@dataclass(slots=True)
class _RefusedPlacement:
    """An item the game found no cell for, and how far the ledger is with it."""

    # The tick from which the one retry may go out.
    retry_at: int
    # The retry, once it is sent.
    retry: LedgerEntry | None = None
    # The retry was refused too, and the item's cancellation has been proposed.
    given_up: bool = False


def _item_of(order: Mapping[str, Any]) -> str:
    return str(order.get("item", "")).lower()


def _queue_of(item: str, world: World) -> str:
    """The production queue an item is made in; an item the table does not know is taken to have one of its own."""
    facts = world.items.get(item)
    return facts.queue if facts is not None and facts.queue else item


def _held_by(order: Mapping[str, Any], world: World) -> tuple[str, str]:
    """What a production order holds until it is resolved: a build the queue it goes into, a train the item."""
    item = _item_of(order)
    return (BUILD, _queue_of(item, world)) if order["cmd"] == BUILD else (TRAIN, item)


class OrderLedger:
    """Every order of one match, with its status, and the counters the match record reports."""

    def __init__(self) -> None:
        # Every order sent, oldest first.
        self.entries: list[LedgerEntry] = []
        self.orders_sent = 0
        self.orders_rejected_by_reason: dict[str, int] = {}
        self.orders_lost = 0
        # Intents of which nothing could be sent when their batch was packed.
        self.dropped_invalid = 0
        # Production orders sent while an order holding the same queue or item was unresolved. It stays 0 as
        # long as every production intent goes through `admit`.
        self.duplicate_production_orders = 0
        # The production orders that are still unresolved, oldest first.
        self.reservations: list[Reservation] = []
        self._queue_blocked_until: dict[str, int] = {}
        # What the ledger itself has to propose in the next cycle (lost orders again, cancellations), and the
        # intents the last packing deferred that are not of a combat class.
        self._owed: list[Intent] = []
        self._carried: list[Intent] = []
        # The items whose placement the game refused for want of a cell, until they are placed or gone.
        self._refused_placements: dict[str, _RefusedPlacement] = {}
        self._finalized = False
        self._tick = 0
        self._batches_sent = 0
        self._open: list[LedgerEntry] = []
        # The batch recorded last, until the platform's answer to it is reported.
        self._awaiting_answer: Sequence[LedgerEntry] | None = None
        # The newest accepted batch, until a state shows the number the platform gave it.
        self._awaiting_number: Sequence[LedgerEntry] | None = None
        # Sent batches by the platform's batch number.
        self._numbered: dict[Any, Sequence[LedgerEntry]] = {}
        self._newest_platform_batch: Any = None

    def summary(self) -> dict[str, Any]:
        """The ledger's counters, as a `ledger_summary` event of the match record holds them."""
        return {
            "orders_sent": self.orders_sent,
            "orders_rejected_by_reason": dict(self.orders_rejected_by_reason),
            "orders_lost": self.orders_lost,
            "dropped_invalid": self.dropped_invalid,
            "duplicate_production_orders": self.duplicate_production_orders,
        }

    def finalize(self) -> None:
        """Write the counters to the match record as the match's closing `ledger_summary`. Only the first call writes."""
        if self._finalized:
            return
        self._finalized = True
        records.log("ledger_summary", self.summary())

    def record(self, packed: Packed, world: World, state_version: int) -> None:
        """Take in what this cycle's packing gave: count what was discarded, and record each order of the batch as sent.

        Call it in every cycle, also when the batch is empty and nothing is
        sent: what packing deferred is kept here for the next cycle's
        `admit`, but for the intents of the combat classes, which are dropped.
        `state_version` is the version of the state the batch goes out with.
        """
        self.dropped_invalid += packed.dropped_invalid
        self._carried = [intent for intent in packed.deferred if intent.priority not in DROPPED_CLASSES]
        if not packed.intents:
            return
        self._batches_sent += 1
        sent = [
            LedgerEntry(
                self._batches_sent,
                index,
                state_version,
                world.tick,
                intent.order,
                intent.priority,
                resend=intent.resend,
            )
            for index, intent in enumerate(packed.intents)
        ]
        self.entries += sent
        self._open += sent
        self.orders_sent += len(sent)
        self._awaiting_answer = sent
        for entry in sent:
            if entry.order["cmd"] in PRODUCTION_COMMANDS:
                self._reserve(entry, world)
            elif entry.order["cmd"] == PLACE:
                self._note_placement_sent(entry)

    def _note_placement_sent(self, entry: LedgerEntry) -> None:
        """Remember a `place` sent for an item whose retry is owed as that retry: admission lets no other through."""
        refused = self._refused_placements.get(_item_of(entry.order))
        if refused is not None and refused.retry is None and not refused.given_up:
            refused.retry = entry

    def _reserve(self, entry: LedgerEntry, world: World) -> None:
        """Open the reservation of a production order just sent, counting it when it repeats an unresolved one."""
        item = _item_of(entry.order)
        facts = world.items.get(item)
        count = entry.order.get("count", DEFAULT_TRAIN_COUNT)
        held = _held_by(entry.order, world)
        if any(reservation.held == held for reservation in self.reservations):
            self.duplicate_production_orders += 1
        cost = (facts.cost if facts is not None else 0) * count
        self.reservations.append(Reservation(_queue_of(item, world), item, count, cost, held, entry))

    def admit(self, intents: Iterable[Intent], world: World) -> list[Intent]:
        """The intents that may go on to packing in this cycle, the most urgent first.

        Besides the intents given, these are the orders the platform lost,
        each proposed again once, the intents the last packing deferred and
        did not drop, and the ledger's own retry or cancellation of a
        placement the game refused. Within a priority class the intents given
        come first, so that a fresh proposal wins over an older one for the
        same unit, and an order that says exactly what an earlier one of the
        cycle says is left out.

        A `build` is held back while an earlier build for the same queue is
        unresolved, and a `train` while an earlier train of the same item is;
        of several in one cycle the most urgent goes on. Both are held back
        while their queue is blocked after the game reported it full. An
        intent for an item that is not on offer holds nothing back: packing
        discards it. A `place` of an item the game found no cell for is held
        back, whoever proposes it.
        """
        held = {reservation.held for reservation in self.reservations}
        said: set[str] = set()
        admitted = []
        for intent in self._proposals_of_the_cycle(intents, world):
            wording = repr(sorted(intent.order.items()))
            if wording not in said and self._may_be_produced(intent.order, world, held):
                admitted.append(intent)
            said.add(wording)
        return admitted

    def _proposals_of_the_cycle(self, intents: Iterable[Intent], world: World) -> list[Intent]:
        """Everything that asks to be sent in this cycle, the most urgent first.

        These are the intents given, what the ledger owes and what was carried
        over, in that order within a priority class. A `place` of an item the
        game found no cell for is left out, whoever proposes it; in its stead
        comes the ledger's own retry, when it is due.
        """
        owed, self._owed = self._owed, []
        carried, self._carried = self._carried, []
        proposed = [intent for intent in (*intents, *owed, *carried) if not self._is_held_placement(intent.order)]
        proposed += [
            Intent(Priority.PLACEMENT, {"cmd": PLACE, "item": item})
            for item, refused in self._refused_placements.items()
            if refused.retry is None and not refused.given_up and world.tick >= refused.retry_at
        ]
        return sorted(proposed, key=attrgetter("priority"))

    def _is_held_placement(self, order: Mapping[str, Any]) -> bool:
        return order.get("cmd") == PLACE and _item_of(order) in self._refused_placements

    def _may_be_produced(self, order: Mapping[str, Any], world: World, held: set[tuple[str, str]]) -> bool:
        """Whether a production order may go on, marking what it holds; any other order may."""
        item = _item_of(order)
        if order.get("cmd") not in PRODUCTION_COMMANDS or item not in world.available_production:
            return True
        wanted = _held_by(order, world)
        if wanted in held or self._is_queue_blocked(_queue_of(item, world), world.tick):
            return False
        held.add(wanted)
        return True

    def _is_queue_blocked(self, queue: str, tick: int) -> bool:
        return tick < self._queue_blocked_until.get(queue, 0)

    def on_action_result(self, result: Mapping[str, Any], _context: Any = None) -> None:
        """Take in the platform's answer to the batch recorded last: write it to the match record and apply it.

        The answer is what the runtime hands to an agent's `on_action_result`:
        the result rows of an accepted batch, or `{"error": code, "detail":
        ...}` for a batch that was refused whole. A refusal ends every order
        of the batch: with the reason the platform names for it when the
        batch was refused for what it held, and as lost when the batch never
        arrived.
        """
        records.log_action_result(dict(result))
        sent, self._awaiting_answer = self._awaiting_answer, None
        if sent is None:
            return
        if "error" in result:
            self._end_refused_batch(sent, result)
            return
        self._awaiting_number = sent
        for row in result.get("results") or ():
            self._apply_row(sent, row)

    def reconcile(self, world: World, raw_state: Mapping[str, Any]) -> None:
        """Bring the ledger up to date with a new state: the verdicts it carries, and the orders left without one.

        A terminal state also closes the ledger: its summary is written to
        the match record.
        """
        self._tick = world.tick
        last_orders = raw_state.get("last_orders")
        if isinstance(last_orders, Mapping):
            sent = self._batch_numbered(last_orders.get("batch"))
            for row in last_orders.get("results") or ():
                self._apply_row(sent, row)
        for row in raw_state.get("recent_order_problems") or ():
            self._apply_row(self._numbered.get(row.get("batch"), ()), row)
        self._assume_overdue_orders_confirmed()
        self._resolve_reservations(world)
        self._forget_settled_placements(world)
        if raw_state.get("is_terminal") or raw_state.get("phase") == FINISHED_PHASE:
            self.finalize()

    def _forget_settled_placements(self, world: World) -> None:
        """Let go of the refused placements that are over: the item was placed after all, or no longer waits."""
        over = [
            item
            for item, refused in self._refused_placements.items()
            if item not in world.ready_to_place or (refused.retry is not None and refused.retry.status in _CARRIED_OUT)
        ]
        for item in over:
            del self._refused_placements[item]

    def _resolve_reservations(self, world: World) -> None:
        """Close the reservations whose order has shown in its queue, made its item, failed or waited long enough."""
        created = {
            str(event.get("type", "")).lower() for event in world.events_new if event.get("kind") in _CREATED_EVENTS
        }
        self.reservations = [
            reservation for reservation in self.reservations if not self._is_resolved(reservation, world, created)
        ]

    @staticmethod
    def _is_resolved(reservation: Reservation, world: World, created: set[str]) -> bool:
        entry = reservation.entry
        if entry.status in _FAILED or world.tick - entry.sent_tick >= RESERVATION_TIMEOUT_TICKS:
            return True
        if world.tick <= entry.sent_tick:
            # The state the order was made from cannot show its effect: an item it shows was there before.
            return False
        queued = any(job.item == reservation.item for job in world.production.get(reservation.queue, ()))
        return queued or reservation.item in created

    def _batch_numbered(self, platform_batch: Any) -> Sequence[LedgerEntry]:
        """The orders of the batch the platform calls by this number; empty when the ledger did not send it.

        The platform's answer to a batch does not say which number the batch
        got. `last_orders` always describes the newest batch, so a number seen
        there for the first time belongs to the newest batch sent.
        """
        if platform_batch is not None and platform_batch != self._newest_platform_batch:
            self._newest_platform_batch = platform_batch
            newest, self._awaiting_number = self._awaiting_number, None
            if newest is None:
                # No answer was reported for the batch sent last; the state shows that it arrived all the same.
                newest, self._awaiting_answer = self._awaiting_answer, None
            if newest is not None:
                self._numbered[platform_batch] = newest
        return self._numbered.get(platform_batch, ())

    def _apply_row(self, sent: Sequence[LedgerEntry], row: Mapping[str, Any]) -> None:
        """Apply one result row to the order of a batch it speaks of, when the batch has such an order."""
        index = row.get("index")
        if isinstance(index, int) and 0 <= index < len(sent):
            self._settle(sent[index], row.get("status"), row.get("reason"))

    def _settle(self, entry: LedgerEntry, platform_status: Any, reason: Any) -> None:
        """Give an order its verdict, once: a row that is no verdict, or comes after one, changes nothing."""
        status = STATUS_OF_PLATFORM_ROW.get(platform_status)
        if status is None or entry.status not in _AWAITING_VERDICT:
            return
        entry.status = status
        entry.reason = None if status == CONFIRMED else str(reason)
        if status == REJECTED:
            self.orders_rejected_by_reason[entry.reason] = self.orders_rejected_by_reason.get(entry.reason, 0) + 1
            self._block_full_queue(entry)
        elif status == LOST:
            self.orders_lost += 1
            if not entry.resend:
                self._owed.append(Intent(entry.priority, entry.order, resend=True))
        if entry.order["cmd"] == PLACE:
            self._after_placement_verdict(entry)

    def _after_placement_verdict(self, entry: LedgerEntry) -> None:
        """Follow up the verdict on a `place`: a first refusal for want of a cell is retried once, a second ends it."""
        item = _item_of(entry.order)
        refused = self._refused_placements.get(item)
        if entry.status == REJECTED and entry.reason == NO_CELL_REASON:
            if refused is None:
                self._refused_placements[item] = _RefusedPlacement(retry_at=self._tick + PLACE_RETRY_TICKS)
            elif refused.retry is entry:
                self._give_up_placing(item, refused)
        elif entry.status == LOST and refused is not None and refused.retry is entry:
            # The retry never reached the game, so it is still owed.
            refused.retry = None

    def _give_up_placing(self, item: str, refused: _RefusedPlacement) -> None:
        """Have an item cancelled that the game twice found no cell for, and say so in the match record."""
        refused.given_up = True
        self._owed.append(Intent(Priority.PRODUCTION, {"cmd": CANCEL, "item": item}))
        records.log(
            "error",
            {
                "where": _ERROR_SOURCE,
                "type": records.HANDLED_ERROR,
                "exception": "PlacementRefused",
                "message": f"The game found no cell for {item} twice; the item is cancelled",
            },
        )

    def _block_full_queue(self, rejected: LedgerEntry) -> None:
        """After the game refused a production order because its queue is full, let nothing more through for it for a while."""
        if rejected.reason != QUEUE_FULL_REASON:
            return
        for reservation in self.reservations:
            if reservation.entry is rejected:
                self._queue_blocked_until[reservation.queue] = self._tick + QUEUE_BLOCK_TICKS

    def _end_refused_batch(self, sent: Sequence[LedgerEntry], refusal: Mapping[str, Any]) -> None:
        error = str(refusal["error"])
        if error != INVALID_ACTION:
            for entry in sent:
                self._settle(entry, LOST, error.lower())
            return
        reasons = {int(index): reason for index, reason in _REFUSAL_ROW.findall(str(refusal.get("detail")))}
        for entry in sent:
            self._settle(entry, REJECTED, reasons.get(entry.index, error.lower()))

    def _assume_overdue_orders_confirmed(self) -> None:
        """Mark the orders that have waited a full timeout for their verdict as assumed carried out."""
        still_open = []
        for entry in self._open:
            if entry.status != PENDING:
                continue
            if self._tick - entry.sent_tick >= VERDICT_TIMEOUT_TICKS:
                entry.status = CONFIRMED_ASSUMED
            else:
                still_open.append(entry)
        self._open = still_open
