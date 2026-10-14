"""What the base earns and how busy its production queues are, as a rolling estimate over the last minute.

The game reports money, never income: `cash` and `ore` go up as the
harvesters deliver and down as the queues are paid, and both happen at once.
Income is therefore read back from the money and what was spent. Each queue
pays for its front item while it is built, in step with its `progress`, so
the progress made between two states, times the item's price, is what that
queue spent in between. Money now, less money then, plus what was spent in
between, is what came in.

The item at the front of a queue that is not waiting to be placed is
followed from state to state. While it is the same item and its progress does
not fall, the rise in progress was paid for. When another item takes its
place, or the same name shows again at a lower progress, the one followed has
ended: it was finished when it was last seen nearly done, and what remained
of it was paid; otherwise it was cancelled, the game gave back what had been
paid, and that money shows again as a rise in the money. A cancelled item's
spending is therefore taken back, or the refund would count as income twice.
Repairs are not seen at all, so the estimate errs low.

What the Vehicle queue pays for harvesters and MCVs is counted apart from
that queue, under `economy`. They earn the income or build the base and are
no part of the army, and the unit queues are balanced by what each spent on
the army: a harvester counted as a vehicle would hold back the tanks. The
total spending, and so the income, counts it like any other.

Every state adds one sample: its tick, the money, the spending so far in
total, for each queue and for the economy, and which queues were busy.
Samples older than the window are dropped. The income is renewed every so
many ticks from the oldest and the newest sample, once the samples span long
enough to mean something; until then it is unknown, and a rule that needs it
does not act. A queue is busy in a state when it holds an item that is not
paused, a finished building waiting to be placed included; its utilization is
the share of the window's states in which it was busy.

The income is in credits a second. With it the vehicle income is renewed,
over the same samples: the income less what the Infantry queue and MCVs
spent a second over the window, never below 0. It is what the war factories
could spend on the army and the harvesters, which the war factory load gate
compares with what they spend (see `war_factory_load`); what MCVs cost is
kept apart for it, as the economy entry counts them with the harvesters.

The investment test asks whether the base can take on a building of a price
and a build time: the money above the production reserve, and a share of what
the base earns while it is built, together cover the price. The rest of the
income is left to the unit queues.

Nothing here sends an order or reads anything but the world, and a new match
gets a new estimate.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Sequence

from ucla_agent.games.redalert.family import HARVESTER, MCV
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.production import BUILDING_QUEUE, DEFENSE_QUEUE, INFANTRY_QUEUE, VEHICLE_QUEUE
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.world import ProductionItem, World

PRODUCTION_QUEUES = (BUILDING_QUEUE, DEFENSE_QUEUE, INFANTRY_QUEUE, VEHICLE_QUEUE)
# The spending entry of the harvesters and MCVs, which the Vehicle queue makes but which are no part of the army.
ECONOMY = "economy"
SPENDING_ENTRIES = (*PRODUCTION_QUEUES, ECONOMY)
_ECONOMY_UNITS = frozenset({HARVESTER, MCV})
_DECIMALS = 3


def can_invest(
    cost: int, build_seconds: float, money: int, income_per_s: float | None, reserve: int, income_share: float
) -> bool:
    """Whether the money above the reserve, and a share of the income earned during the build, cover a price.

    False while the income is unknown.
    """
    if income_per_s is None:
        return False
    return money - reserve + income_per_s * build_seconds * income_share >= cost


@dataclass(frozen=True, slots=True)
class _Front:
    """The item at the front of a queue as last seen: its name and how far it had got."""

    item: str
    progress: float


@dataclass(frozen=True, slots=True)
class _Sample:
    """One state as the estimate keeps it."""

    tick: int
    money: int
    # The spending counted from the start of the match: in total, and for each of SPENDING_ENTRIES.
    spent: float
    spent_by_entry: tuple[float, ...]
    # For each of PRODUCTION_QUEUES, whether it was busy.
    busy: tuple[bool, ...]
    # What went on MCVs from the start of the match, which the economy entry holds together with the harvesters.
    spent_on_mcvs: float


class IncomeEstimate:
    """The rolling income, spending and utilization of one match. See the module's note."""

    def __init__(self, tuning: Tuning) -> None:
        self._tuning = tuning.income
        self._reserve = tuning.upkeep.production_cash_reserve
        self._window_ticks = self._tuning.income_window_seconds * TICKS_PER_SECOND
        self._samples: deque[_Sample] = deque()
        # Queue -> its front item in the last state; None for a queue that had none.
        self._fronts: dict[str, _Front | None] = dict.fromkeys(PRODUCTION_QUEUES)
        self._spent_by_entry = dict.fromkeys(SPENDING_ENTRIES, 0.0)
        self._spent_on_mcvs = 0.0
        # For each of PRODUCTION_QUEUES, in how many of the samples kept it was busy.
        self._busy_states = [0] * len(PRODUCTION_QUEUES)
        self._renewed_at: int | None = None
        # Credits a second; None while the samples span too short a time.
        self.income_per_s: float | None = None
        # The income less what the Infantry queue and MCVs spent a second over the same window, renewed with it: what
        # the war factories could spend (see `war_factory_load`). Credits a second; None while the income is unknown.
        self.vehicle_income_per_s: float | None = None

    def update(self, world: World) -> None:
        """Take in one fresh state: what was spent since the last, one sample, and the income when it is due."""
        for queue in PRODUCTION_QUEUES:
            for item, paid in self._payments(queue, world):
                self._spent_by_entry[_entry_of(queue, item)] += paid
                self._spent_on_mcvs += paid if item == MCV else 0.0
        busy = tuple(_is_busy(world.production.get(queue, ())) for queue in PRODUCTION_QUEUES)
        spent = tuple(self._spent_by_entry.values())
        sample = _Sample(world.tick, world.economy.spendable, sum(spent), spent, busy, self._spent_on_mcvs)
        self._add(sample)
        if self._renewed_at is None or world.tick - self._renewed_at >= self._tuning.income_update_ticks:
            self._renewed_at = world.tick
            self.income_per_s = self._realized_income()
            self.vehicle_income_per_s = self._vehicle_income(self.income_per_s)

    def utilization(self, queue: str) -> float:
        """The share of the window's states in which a queue was busy."""
        return self._busy_states[PRODUCTION_QUEUES.index(queue)] / len(self._samples)

    def spend_by_queue(self) -> dict[str, float]:
        """What each queue spent over the window, and under `economy` what went on harvesters and MCVs.

        The Vehicle entry is what was spent on the army's vehicles alone.
        """
        oldest, newest = self._samples[0], self._samples[-1]
        return {
            entry: newest.spent_by_entry[index] - oldest.spent_by_entry[index]
            for index, entry in enumerate(SPENDING_ENTRIES)
        }

    def spend_per_s(self) -> float:
        """What all the queues together spent a second over the window; 0 while the window spans no time."""
        oldest, newest = self._samples[0], self._samples[-1]
        seconds = (newest.tick - oldest.tick) / TICKS_PER_SECOND
        return (newest.spent - oldest.spent) / seconds if seconds else 0.0

    def figures(self) -> dict[str, Any]:
        """The estimate as a `timing` event gives it: the income (null while unknown), spending and utilization."""
        income = None if self.income_per_s is None else round(self.income_per_s, _DECIMALS)
        return {
            "income_per_s": income,
            "spend_per_s": round(self.spend_per_s(), _DECIMALS),
            "utilization": {queue: round(self.utilization(queue), _DECIMALS) for queue in PRODUCTION_QUEUES},
        }

    def can_invest(self, cost: int, build_seconds: float) -> bool:
        """The investment test for an item of a price and a build time, at the money of the latest state."""
        money = self._samples[-1].money
        return can_invest(
            cost, build_seconds, money, self.income_per_s, self._reserve, self._tuning.invest_income_share
        )

    def has_held_above(self, money: int, seconds: float) -> bool:
        """Whether the money has been above an amount in every state of the last so many seconds.

        False when the samples do not reach that far back.
        """
        since = self._samples[-1].tick - seconds * TICKS_PER_SECOND
        if self._samples[0].tick > since:
            return False
        for sample in reversed(self._samples):
            if sample.tick < since:
                break
            if sample.money <= money:
                return False
        return True

    def _add(self, sample: _Sample) -> None:
        """Keep a sample, and drop those that have fallen out of the window."""
        self._samples.append(sample)
        self._count_busy(sample, 1)
        while self._samples[0].tick < sample.tick - self._window_ticks:
            self._count_busy(self._samples.popleft(), -1)

    def _count_busy(self, sample: _Sample, step: int) -> None:
        for index, busy in enumerate(sample.busy):
            self._busy_states[index] += step if busy else 0

    def _realized_income(self) -> float | None:
        """The money gained and spent over the window, a second, never below 0; None while the window is too short."""
        oldest, newest = self._samples[0], self._samples[-1]
        seconds = (newest.tick - oldest.tick) / TICKS_PER_SECOND
        if seconds < self._tuning.income_min_window_seconds:
            return None
        return max(0.0, (newest.money - oldest.money + newest.spent - oldest.spent) / seconds)

    def _vehicle_income(self, income_per_s: float | None) -> float | None:
        """An income less what the Infantry queue and MCVs spent a second over the window it was taken over, never
        below 0; None for an unknown income."""
        if income_per_s is None:
            return None
        oldest, newest = self._samples[0], self._samples[-1]
        seconds = (newest.tick - oldest.tick) / TICKS_PER_SECOND
        infantry = SPENDING_ENTRIES.index(INFANTRY_QUEUE)
        infantry_spent = newest.spent_by_entry[infantry] - oldest.spent_by_entry[infantry]
        mcvs_spent = newest.spent_on_mcvs - oldest.spent_on_mcvs
        return max(0.0, income_per_s - (infantry_spent + mcvs_spent) / seconds)

    def _payments(self, queue: str, world: World) -> list[tuple[str, float]]:
        """What a queue paid since the last state, item by item, by the progress of the item at its front."""
        front = _front(world.production.get(queue, ()))
        before, self._fronts[queue] = self._fronts[queue], front
        if before is not None and front is not None and front.item == before.item and front.progress >= before.progress:
            return [(front.item, _cost(front.item, world) * (front.progress - before.progress))]
        ended = [] if before is None else [(before.item, self._settled(before, world))]
        return ended + ([] if front is None else [(front.item, _cost(front.item, world) * front.progress)])

    def _settled(self, ended: _Front, world: World) -> float:
        """What an item that has left the front adds: the rest of its price when finished, less what was counted if not."""
        cost = _cost(ended.item, world)
        if ended.progress >= self._tuning.finished_progress:
            return cost * (1.0 - ended.progress)
        return -cost * ended.progress


def _entry_of(queue: str, item: str) -> str:
    """The spending entry what an item costs is counted under: the economy for a harvester or an MCV, else its queue."""
    return ECONOMY if item in _ECONOMY_UNITS else queue


def _front(jobs: Sequence[ProductionItem]) -> _Front | None:
    """The first item of a queue that is not waiting to be placed; None when there is none."""
    return next((_Front(job.item, job.progress) for job in jobs if not job.ready), None)


def _is_busy(jobs: Sequence[ProductionItem]) -> bool:
    """Whether a queue holds an item that is not paused: a finished one waiting to be placed counts."""
    return any(not job.paused for job in jobs)


def _cost(item: str, world: World) -> int:
    """The price of an item; 0 for an item the table does not know."""
    facts = world.items.get(item)
    return facts.cost if facts is not None else 0
