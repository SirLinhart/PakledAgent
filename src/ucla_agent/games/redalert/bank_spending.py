"""Money the base banks and does not spend: the second war factory on a bank held for a while, and an order for a
Building queue that has stood empty while the money piled up.

The producers add a war factory on the income the base measures (see
`upkeep`), and that income can stay under the gate of the second war
factory while the money piles up: one war factory spends only so fast, and
money in the bank fights nothing. So once no barracks or war factory is due
on its own gates, the producer rule looks at the bank itself.

With exactly one war factory standing, and the money above the production
reserve at least `bank_factory_money` in every state of the last
`bank_factory_seconds`, the second war factory is ordered whatever the
income. It is still a war factory like any other: it waits while none is on
offer, for `max_war_factories`, for the power target and for its place.

An empty Building queue spends nothing at all. Once it has held nothing (no
building queued, being built or waiting to be placed) for
`idle_queue_seconds`, with at least `idle_bank_money` above the reserve,
the first of these that is allowed is ordered: a war factory while fewer
than `max_war_factories` stand; a refinery for an untaken field one can
stand against now, under the refinery rules' caps; a power plant when the
power target calls for one. Never a defense, and one such order in
`idle_queue_seconds` at most. A finished building held unplaced keeps the
queue from being empty; that one is the placement's to free (see
`held_cancel_ticks`).

Each order of either kind is written to the match record as one `economy`
event: the tick, the reason (`bank_factory` or `idle_bank`), the item
ordered (the power plant when the power target puts it first) and the money,
the cash and the ore together.

Ore that the refineries and silos cannot hold is lost, so ore near the cap
is money about to be thrown away. The game reports how much ore can be
stored, `resource_capacity`, which caps the ore alone: the cash has no cap.
Once the ore held has been at least `storage_spend_share` of it in every
state of the last `storage_spend_seconds`, the storage rule is in force,
and it stays in force while every state is so. The state that brings it
into force is written as one `economy` event of the reason
`storage_near_cap`, with no item, the money, the ore and the capacity.
Upkeep notes it once at the start of every cycle, so that every rule of the
cycle reads the same answer. While it is in force the producer rule orders
one more war factory while fewer than `storage_war_factories_max` stand or
are on their way, past `max_war_factories`, and one in
`storage_spend_seconds` at most: more war factories spend faster. Each
order, the power plant the power target puts first included, is an
`economy` event of the same reason with its item.

With `no_float` on, cash is never left to float: once the money
(`spendable`) has been at least `no_float_cash` in every state of the last
`no_float_seconds`, a refinery standing, whatever the plan, the spending
focus or the military recovery, the producer rule orders one more producer at
once (`float_producer`): a barracks or a war factory, the one whose unit
queue was the busier, up to `max_infantry_producers` and `max_war_factories`
(the other when the busier one is at its most), or the power plant it waits
for; one such order in `no_float_seconds` at most. More producers spend more
at once: the unit queues fill every free producer as ever, and the money
held back from MCVs and refineries under the military focus goes to them.
It comes before the idle bank, which waits for idle_bank_money with the
Building queue empty. Each order is an `economy` event of the reason
`no_float` with the item and the cash. In the live games of 20261016 4,000
floated at 5:00 under the military focus with one barracks and one war
factory, the idle bank first ordering at 3,389.

Nothing here sends an order: the bank and the Building queue are followed
from state to state, and the producer rule orders.
"""

from __future__ import annotations

from ucla_agent.core import records
from ucla_agent.games.redalert.batch import Intent
from ucla_agent.games.redalert.family import WAR_FACTORY
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.production import BUILDING_QUEUE
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.world import World

ECONOMY_EVENT = "economy"
# The reasons an `economy` event gives: the second war factory ordered on a bank, and an order for an empty Building
# queue.
BANK_FACTORY = "bank_factory"
IDLE_BANK = "idle_bank"
# The reason an `economy` event of the storage rule gives, when it comes into force and for each order it makes.
STORAGE_NEAR_CAP = "storage_near_cap"
# The reason of an order for a producer on floating cash.
NO_FLOAT = "no_float"


class BankWatch:
    """Since when the money above the production reserve has stayed at least `bank_factory_money` and the Building
    queue empty, state by state, and when the last order for an empty Building queue was made."""

    def __init__(self) -> None:
        # The tick of the first state of the run of states, up to the last one, that held at least bank_factory_money
        # above the reserve; None while the last one held less.
        self._banked_since: int | None = None
        # The same for the states whose Building queue held nothing.
        self._idle_since: int | None = None
        # The tick of the last order made for an empty Building queue; None before the first.
        self._idle_ordered_at: int | None = None
        # The same run for the states whose money was at least no_float_cash, and the tick of the last order on it.
        self._floating_since: int | None = None
        self._float_ordered_at: int | None = None

    def note(self, world: World, tuning: Tuning) -> None:
        """Take in one fresh state."""
        is_banked = _above_reserve(world, tuning) >= tuning.producers.bank_factory_money
        self._banked_since = _run_start(self._banked_since, is_banked, world.tick)
        is_idle = not world.production.get(BUILDING_QUEUE)
        self._idle_since = _run_start(self._idle_since, is_idle, world.tick)
        is_floating = world.economy.spendable >= tuning.producers.no_float_cash
        self._floating_since = _run_start(self._floating_since, is_floating, world.tick)

    def is_floating(self, world: World, tuning: Tuning) -> bool:
        """With no_float on, whether the money has stayed at least `no_float_cash` for `no_float_seconds`, and no
        order was made on it in the last `no_float_seconds`."""
        producers = tuning.producers
        if not producers.no_float:
            return False
        rested = self._float_ordered_at is None or _has_lasted(
            self._float_ordered_at, producers.no_float_seconds, world.tick
        )
        return rested and _has_lasted(self._floating_since, producers.no_float_seconds, world.tick)

    def float_orders(self, orders: list[Intent], world: World) -> list[Intent]:
        """Note the orders made on floating cash, so that the next waits `no_float_seconds`, write each as an
        `economy` event of the reason `no_float` with the cash, and give them back."""
        if orders:
            self._float_ordered_at = world.tick
        for intent in orders:
            data = {
                "tick": world.tick,
                "reason": NO_FLOAT,
                "item": intent.order["item"],
                "cash": world.economy.spendable,
            }
            records.log(ECONOMY_EVENT, data)
        return orders

    def has_banked(self, world: World, tuning: Tuning) -> bool:
        """Whether the money has stayed at least `bank_factory_money` above the reserve for `bank_factory_seconds`."""
        return _has_lasted(self._banked_since, tuning.producers.bank_factory_seconds, world.tick)

    def is_idle_with_bank(self, world: World, tuning: Tuning) -> bool:
        """Whether the Building queue has held nothing for `idle_queue_seconds` with at least `idle_bank_money` above
        the reserve now, and no order was made for an empty Building queue in the last `idle_queue_seconds`."""
        producers = tuning.producers
        is_rich = _above_reserve(world, tuning) >= producers.idle_bank_money
        is_idle = _has_lasted(self._idle_since, producers.idle_queue_seconds, world.tick)
        is_rested = self._idle_ordered_at is None or _has_lasted(
            self._idle_ordered_at, producers.idle_queue_seconds, world.tick
        )
        return is_rich and is_idle and is_rested

    def idle_orders(self, orders: list[Intent], world: World) -> list[Intent]:
        """Note the orders made for an empty Building queue, so that the next waits `idle_queue_seconds`, write them
        (`log_orders`) and give them back."""
        if orders:
            self._idle_ordered_at = world.tick
        return log_orders(IDLE_BANK, orders, world)


class StorageWatch:
    """Whether the ore held has stayed near the storage cap for `storage_spend_seconds`, state by state, and when the
    storage rule last ordered a war factory. See the module's note."""

    def __init__(self) -> None:
        # The tick of the first state of the run of states, up to the last one, whose ore was at least
        # storage_spend_share of the storage; None while the last one held less.
        self._near_cap_since: int | None = None
        # Whether that run had lasted storage_spend_seconds by the last state noted.
        self._is_in_force = False
        # The tick of the last war factory the storage rule ordered; None before the first.
        self._factory_ordered_at: int | None = None

    @property
    def is_in_force(self) -> bool:
        """Whether the ore has stayed near the cap for `storage_spend_seconds`, as of the last state noted."""
        return self._is_in_force

    def note(self, world: World, tuning: Tuning) -> None:
        """Take in one fresh state; the state that brings the rule into force is written as an `economy` event."""
        producers, capacity = tuning.producers, world.economy.resource_capacity
        # With no refinery or silo standing nothing is stored, and there is no cap to be near.
        is_near_cap = 0 < capacity and world.economy.ore >= producers.storage_spend_share * capacity
        self._near_cap_since = _run_start(self._near_cap_since, is_near_cap, world.tick)
        was_in_force = self._is_in_force
        self._is_in_force = _has_lasted(self._near_cap_since, producers.storage_spend_seconds, world.tick)
        if self._is_in_force and not was_in_force:
            _log_storage(None, world)

    def may_order_factory(self, world: World, tuning: Tuning) -> bool:
        """Whether the storage rule may order one more war factory now: it is in force, and it ordered none in the last
        `storage_spend_seconds`."""
        seconds = tuning.producers.storage_spend_seconds
        is_rested = self._factory_ordered_at is None or _has_lasted(self._factory_ordered_at, seconds, world.tick)
        return self._is_in_force and is_rested

    def orders(self, orders: list[Intent], world: World) -> list[Intent]:
        """Write each order the storage rule made as an `economy` event, note a war factory among them so that the
        next waits `storage_spend_seconds`, and give them back."""
        for intent in orders:
            item = intent.order["item"]
            if item == WAR_FACTORY:
                self._factory_ordered_at = world.tick
            _log_storage(item, world)
        return orders


def log_orders(reason: str, orders: list[Intent], world: World) -> list[Intent]:
    """Write one `economy` event for each order a bank rule made, with its reason, and give the orders back."""
    for intent in orders:
        data = {"tick": world.tick, "reason": reason, "item": intent.order["item"], "money": world.economy.spendable}
        records.log(ECONOMY_EVENT, data)
    return orders


def _log_storage(item: str | None, world: World) -> None:
    """Write one `economy` event of the storage rule: the item it ordered, none when it comes into force, with the
    money, the ore and the storage."""
    economy = world.economy
    data = {
        "tick": world.tick,
        "reason": STORAGE_NEAR_CAP,
        "item": item,
        "money": economy.spendable,
        "ore": economy.ore,
        "capacity": economy.resource_capacity,
    }
    records.log(ECONOMY_EVENT, data)


def _above_reserve(world: World, tuning: Tuning) -> int:
    """The money above the production reserve: what the bank rules count as spendable."""
    return world.economy.spendable - tuning.upkeep.production_cash_reserve


def _run_start(since: int | None, holds: bool, tick: int) -> int | None:
    """The first tick of the run of states, up to this one, in which something holds; None when it does not now."""
    if not holds:
        return None
    return tick if since is None else since


def _has_lasted(since: int | None, seconds: int, tick: int) -> bool:
    """Whether a run of states that started at `since` has lasted so many seconds by this tick."""
    return since is not None and tick - since >= seconds * TICKS_PER_SECOND
