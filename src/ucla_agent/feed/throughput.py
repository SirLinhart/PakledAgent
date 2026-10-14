"""How much of the platform's order capacity the agent uses in a Red Alert match, from the events of its record.

Every `action` event is one turn of the agent: one batch of orders sent, or
`WAIT`, a batch of 0 orders. The platform takes at most 20 orders in a batch
(the pool of a turn) and at most 200 engine commands. The figures here count
batches, orders and engine commands per second of game time, how full the
pool of 20 was on average and how often it was full, and how many orders the
platform rejected, over the last 10 seconds of game time and over the whole
match.

An action event names its batch's `order_count` and `command_count`; a record
written before it did is read by counting the orders of the action, and its
command counts are unknown. Game time is the tick, 25 to the second: a
batch's tick is its `state_version`, and the match has run until the latest
tick of any batch or state.

Rejected orders are counted where the record shows them: every order of a
batch the platform refused whole (an `action_result` that was not accepted),
and every row the platform marked rejected in the `last_orders` or
`recent_order_problems` of a state written in full, once for each batch and
order index however many states repeat it. A record holds every change of
those verdicts, since a state whose verdicts changed is written in full.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ucla_agent.core.stats import numbers
from ucla_agent.feed.reader import STATE_KIND, TICKS_PER_SECOND

# The platform's limits on one batch: its pool of orders, and the engine commands those may expand to.
POOL_ORDERS = 20
COMMAND_LIMIT = 200
WINDOW_SECONDS = 10
# How many seconds of game time the live chart shows.
CHART_SECONDS = 120
ACTION_KIND = "action"
ACTION_RESULT_KIND = "action_result"
REJECTED = "rejected"
PERCENT = 100


@dataclass(frozen=True)
class _Batch:
    """One turn of the agent: the tick it was sent at, its orders and the engine commands they count as."""

    tick: int
    orders: int
    # None for a batch of a record that did not count its commands.
    commands: int | None


class Throughput:
    """The batches, orders, commands and rejected orders of one Red Alert match, taken in event by event."""

    def __init__(self) -> None:
        self._batches: list[_Batch] = []
        # (tick, how many orders) for each rejection found.
        self._rejected: list[tuple[int, int]] = []
        self._rejected_rows: set[tuple[Any, Any]] = set()
        self._latest_tick = 0
        # The newest batch that carried orders: the one an answer of the platform is about.
        self._awaiting_answer: _Batch | None = None

    def take_in(self, event: dict[str, Any]) -> None:
        """Take in one event of the record; kinds other than action, action_result and state change nothing."""
        kind, data = event["kind"], event["data"]
        if kind == ACTION_KIND:
            self._take_in_batch(data)
        elif kind == ACTION_RESULT_KIND:
            self._take_in_answer(data)
        elif kind == STATE_KIND:
            self._take_in_state(data)

    def figures(self) -> dict[str, dict[str, Any]]:
        """The throughput over the last 10 seconds of game time (`last_10s`) and over the whole match (`match`)."""
        now = self._latest_tick / TICKS_PER_SECOND
        return {
            "last_10s": self._span(after=now - WINDOW_SECONDS, seconds=min(WINDOW_SECONDS, now)),
            "match": self._span(after=None, seconds=now),
        }

    def series(self, seconds: int = CHART_SECONDS) -> list[dict[str, int]]:
        """Orders and batches in each whole second of game time, for the last `seconds` seconds up to the latest."""
        last_second = self._latest_tick // TICKS_PER_SECOND
        first_second = max(0, last_second - seconds + 1)
        per_second = {
            second: {"second": second, "orders": 0, "batches": 0} for second in range(first_second, last_second + 1)
        }
        for batch in self._batches:
            bucket = per_second.get(batch.tick // TICKS_PER_SECOND)
            if bucket is not None:
                bucket["orders"] += batch.orders
                bucket["batches"] += 1
        return list(per_second.values())

    def _take_in_batch(self, data: dict[str, Any]) -> None:
        tick = data.get("state_version")
        if not numbers([tick]):
            return
        orders = data.get("order_count")
        if not numbers([orders]):
            orders = _orders_in(data.get("action"))
        commands = data.get("command_count")
        if not numbers([commands]):
            # A batch of no orders makes no command, whatever the record counted.
            commands = 0 if orders == 0 else None
        batch = _Batch(tick, orders, commands)
        self._batches.append(batch)
        self._note_tick(tick)
        if orders > 0:
            self._awaiting_answer = batch

    def _take_in_answer(self, data: dict[str, Any]) -> None:
        """A batch refused whole had every one of its orders rejected."""
        if data.get("accepted") is False and self._awaiting_answer is not None:
            self._rejected.append((self._awaiting_answer.tick, self._awaiting_answer.orders))
        self._awaiting_answer = None

    def _take_in_state(self, data: dict[str, Any]) -> None:
        tick = data.get("state_version")
        if not numbers([tick]):
            return
        self._note_tick(tick)
        state = data.get("state")
        if isinstance(state, dict):
            for row_key in _rejected_rows(state):
                if row_key not in self._rejected_rows:
                    self._rejected_rows.add(row_key)
                    self._rejected.append((tick, 1))

    def _note_tick(self, tick: int) -> None:
        self._latest_tick = max(self._latest_tick, tick)

    def _span(self, *, after: float | None, seconds: float) -> dict[str, Any]:
        """The figures of the batches and rejections later than `after` seconds of game time (all of them for None),
        over `seconds` of game time."""
        batches = [batch for batch in self._batches if _is_after(batch.tick, after)]
        rejected = sum(count for tick, count in self._rejected if _is_after(tick, after))
        orders = sum(batch.orders for batch in batches)
        known_commands = [batch.commands for batch in batches if batch.commands is not None]
        commands = sum(known_commands) if len(known_commands) == len(batches) else None
        return {
            "seconds": seconds,
            "batches": len(batches),
            "orders": orders,
            "commands": commands,
            "rejected": rejected,
            "orders_per_s": _per_second(orders, seconds),
            "batches_per_s": _per_second(len(batches), seconds),
            "commands_per_s": _per_second(commands, seconds),
            "rejected_per_s": _per_second(rejected, seconds),
            "pool_orders": POOL_ORDERS,
            "mean_orders_per_batch": _per_batch(orders, batches),
            "at_limit_percent": _per_batch(
                PERCENT * sum(1 for batch in batches if batch.orders >= POOL_ORDERS), batches
            ),
            "command_limit": COMMAND_LIMIT,
            "mean_commands_per_batch": _per_batch(commands, batches),
        }


def _orders_in(action: Any) -> int:
    """How many orders an action holds: those of a batch of orders; 0 for WAIT."""
    orders = action.get("orders") if isinstance(action, dict) else None
    return len(orders) if isinstance(orders, list) else 0


def _rejected_rows(state: dict[str, Any]) -> list[tuple[Any, Any]]:
    """(batch, index) of every order a full state's verdicts mark rejected: the newest batch's rows, which do not
    name their batch, and the problems of the batches before it, which do."""
    newest = state.get("last_orders") if isinstance(state.get("last_orders"), dict) else {}
    rows = [{**row, "batch": newest.get("batch")} for row in _dicts(newest.get("results"))]
    rows += _dicts(state.get("recent_order_problems"))
    return [(row.get("batch"), row.get("index")) for row in rows if row.get("status") == REJECTED]


def _dicts(value: Any) -> list[dict[str, Any]]:
    """The objects in a list of the record; none when it is not a list."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _is_after(tick: int, after: float | None) -> bool:
    return after is None or tick / TICKS_PER_SECOND > after


def _per_second(count: int | None, seconds: float) -> float | None:
    return None if count is None or seconds <= 0 else count / seconds


def _per_batch(count: int | None, batches: list[_Batch]) -> float | None:
    return None if count is None or not batches else count / len(batches)
