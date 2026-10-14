"""The economy first: the unit queues wait, a short while at a time, for the economy's next item.

With `economy_first` of the tuning file's [upkeep] switched on, the money of
a base past its opening goes first to what brings in more money and makes
more units: a refinery for a field the base has reached, one more war
factory, and the second construction yard's MCV. Income arrives as a
trickle, and unit queues that are fed whenever they are free take it as
fast as it comes, so that the price of such a building is never gathered.

Upkeep lists in every cycle the economy's items that are due and not on
their way, the first first (see `upkeep`): the refinery while a refinery
claim is due (`refinery_claim_due`), a growth refinery while one is due
(`refinery_growth`), one more war factory whose gates but the money are met
while the base is quiet, and, with `second_yard` on, the MCV whose gates but
the money are met. Each says whether the money covers
it now, by the test of the rule that orders it.

While the first of them is not covered, a hold starts and the unit queues
are given nothing new while it lasts, so that the money gathers. It lasts
until its item is on its way (`ordered`), until `economy_wait_seconds` have
passed (`timed_out`), or until its item is due no longer (`not_due`): the
base came under attack, the income fell, or the claim was given up. The
unit queues are then fed for `economy_release_seconds` at the least before
the next hold may start, so that tanks are never starved for long.

Only the unit queues' own orders wait. Whatever is ordered ahead of them is
never held, whether by a rule before them in upkeep or by another manager on
a queue it claims: the power plants the power target calls for, base
defense's emergency orders, and the upgrades and replacements of the scout
slots, which are ordered ahead of the unit queues for that reason.

Money gathered is pointless once the ore is near the storage cap: what
comes in then is lost. While the storage rule is in force (see
`bank_spending`) no hold holds the unit queues: one in force ends then
(`storage_near_cap`), its release begins, and none starts.

Each start and each end of a hold is written to the match record as one
`economy_hold` event: the tick, the item and the reason (`started`,
`ordered`, `timed_out`, `not_due` or `storage_near_cap`).

With `mcv_first` of [upkeep] switched on (and the second yard), the second
yard's MCV is not one of these items: it has a hold of its own, the full
hold, which holds far more than the unit queues. Upkeep decides it once at
the start of every cycle, before any of its rules is asked (see `upkeep`).
Once a service depot stands and the MCV is wanted, it is due first; while
the money does not cover it, a full hold is in force, so that what comes in
gathers for the MCV instead of going into new buildings as it arrives. A
full hold starts only when the last one's release is over, and it ends when
the MCV is on its way (`ordered`), when the MCV is wanted no longer
(`not_due`), or after `mcv_first_wait_seconds` (`timed_out`); then nothing
at all is held for `economy_release_seconds`. Its events add the scope
`all`. The unit queues' own hold gives way to it: none starts while a full
hold is in force or released, and one in force when a full hold starts
ends then, as `not_due`.

While the second MCV is due by its time (see `expansion`), a full hold is
untimed: it starts whatever the last one's release, and it neither times out
nor is released until the MCV is on its way or wanted no longer, so that the
money gathers for the second yard rather than going to the army every
`mcv_first_wait_seconds`.
"""

from __future__ import annotations

from typing import Callable, NamedTuple

from ucla_agent.core import records
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.tuning import UpkeepTuning

ECONOMY_HOLD_EVENT = "economy_hold"
# The reasons an `economy_hold` event gives: a hold that starts, and the three ways it ends.
STARTED = "started"
ORDERED = "ordered"
TIMED_OUT = "timed_out"
NOT_DUE = "not_due"
# The scope an `economy_hold` event of a full hold gives: it holds upkeep's orders, not the unit queues' alone.
SCOPE_ALL = "all"


class EconomyItem(NamedTuple):
    """An item of the economy that is due and not on its way, and whether the money covers it now."""

    item: str
    is_covered: bool


class EconomyHold:
    """Whether the unit queues wait for the economy's next item in a cycle, and the record of each hold. See the
    module's note."""

    def __init__(self) -> None:
        # The item the hold in force waits for and the tick it started at; None while no hold is in force.
        self._hold: tuple[str, int] | None = None
        # The tick the last hold ended at; None before one has.
        self._ended_at: int | None = None

    def holds(
        self, due: list[EconomyItem], is_on_its_way: Callable[[str], bool], tick: int, tuning: UpkeepTuning
    ) -> bool:
        """Whether the unit queues wait in this cycle: a hold in force goes on, or one starts for the first item `due`
        when the money does not cover it and the last hold's release is over. A hold that ends leaves the unit queues
        free in the cycle it ends in."""
        if self._hold is not None:
            return self._goes_on(due, is_on_its_way, tick, tuning)
        if not due or due[0].is_covered or self._is_released(tick, tuning):
            return False
        self._hold = (due[0].item, tick)
        _log(tick, due[0].item, STARTED)
        return True

    def _goes_on(
        self, due: list[EconomyItem], is_on_its_way: Callable[[str], bool], tick: int, tuning: UpkeepTuning
    ) -> bool:
        """Whether the hold in force goes on; one that ends is written with its reason, and its release begins."""
        item, started = self._hold
        reason = _end_reason(item, started, due, is_on_its_way, tick, tuning)
        if reason is None:
            return True
        self._hold, self._ended_at = None, tick
        _log(tick, item, reason)
        return False

    def _is_released(self, tick: int, tuning: UpkeepTuning) -> bool:
        """Whether the unit queues are still free after the last hold: less than `economy_release_seconds` ago."""
        return _is_released(self._ended_at, tick, tuning)

    def give_way(self, tick: int, reason: str = NOT_DUE) -> None:
        """End the hold in force, if there is one, with a reason: `not_due` when a full hold holds the unit queues now,
        and its release leaves them free; the storage rule's reason when the ore near the storage cap lifts it."""
        if self._hold is None:
            return
        item, _ = self._hold
        self._hold, self._ended_at = None, tick
        _log(tick, item, reason)


class FullHold:
    """Whether the full hold for the second yard's MCV is in force in a cycle, and the record of each hold. See the
    module's note."""

    def __init__(self, item: str) -> None:
        # The item the hold waits for, as its events name it.
        self._item = item
        # The tick the hold in force started at; None while no hold is in force.
        self._started: int | None = None
        # The tick the last hold ended at; None before one has.
        self._ended_at: int | None = None

    def holds(self, is_due: bool, is_covered: bool, tick: int, tuning: UpkeepTuning, *, untimed: bool = False) -> bool:
        """Whether the full hold is in force in this cycle: its item is due first and the money does not cover it,
        and a hold goes on or one starts because the last one's release is over, or it is `untimed`. A hold whose
        item is due no longer, or that has lasted `mcv_first_wait_seconds` without being `untimed`, ends in this cycle
        and holds nothing in it."""
        if self._started is not None:
            reason = _full_end_reason(is_due, self._started, tick, tuning, untimed)
            if reason is None:
                return not is_covered
            self._end(tick, reason)
            return False
        is_resting = _is_released(self._ended_at, tick, tuning) and not untimed
        if not is_due or is_covered or is_resting:
            return False
        self._started = tick
        _log(tick, self._item, STARTED, scope=SCOPE_ALL)
        return True

    def settle(self, is_on_its_way: bool, tick: int) -> None:
        """End the hold in force as `ordered` in the cycle its item went on its way."""
        if self._started is not None and is_on_its_way:
            self._end(tick, ORDERED)

    def is_engaged(self, tick: int, tuning: UpkeepTuning) -> bool:
        """Whether a full hold is in force, or ended less than `economy_release_seconds` ago: no other hold may
        start meanwhile."""
        return self._started is not None or _is_released(self._ended_at, tick, tuning)

    def _end(self, tick: int, reason: str) -> None:
        """End the hold in force with its reason; its release begins."""
        self._started, self._ended_at = None, tick
        _log(tick, self._item, reason, scope=SCOPE_ALL)


def _is_released(ended_at: int | None, tick: int, tuning: UpkeepTuning) -> bool:
    """Whether a hold that ended at a tick ended less than `economy_release_seconds` ago; never before one has."""
    return ended_at is not None and tick - ended_at < tuning.economy_release_seconds * TICKS_PER_SECOND


def _full_end_reason(is_due: bool, started: int, tick: int, tuning: UpkeepTuning, untimed: bool) -> str | None:
    """Why a full hold that started at a tick ends now: its item is due no longer, or it has lasted
    `mcv_first_wait_seconds` and is not `untimed`; None while it goes on."""
    if not is_due:
        return NOT_DUE
    if not untimed and tick - started >= tuning.mcv_first_wait_seconds * TICKS_PER_SECOND:
        return TIMED_OUT
    return None


def _end_reason(
    item: str,
    started: int,
    due: list[EconomyItem],
    is_on_its_way: Callable[[str], bool],
    tick: int,
    tuning: UpkeepTuning,
) -> str | None:
    """Why a hold for an item that started at a tick ends now: its item is on its way, it has lasted
    `economy_wait_seconds`, or its item is due no longer; None while it goes on."""
    if is_on_its_way(item):
        return ORDERED
    if tick - started >= tuning.economy_wait_seconds * TICKS_PER_SECOND:
        return TIMED_OUT
    return None if any(entry.item == item for entry in due) else NOT_DUE


def _log(tick: int, item: str, reason: str, **more: str) -> None:
    """Write one `economy_hold` event, with what more is given."""
    records.log(ECONOMY_HOLD_EVENT, {"tick": tick, "item": item, "reason": reason, **more})
