"""What is being made, as one cycle of the agent sees it.

Every rule that orders a building or a unit asks the same few questions
first: is there one already, is one on its way, is the queue free, is there
power for it. The answers come from three places that have to be read
together. The state shows what stands and what is in the queues. The order
ledger knows what was ordered so recently that no state shows it yet. And
within one cycle, a rule that runs later has to know what a rule before it
has just proposed, or two of them would fill the same queue and spend the
same power twice.

A `Production` is made once per cycle from the world and the ledger and puts
the three together. Rules make their `build` and `train` intents through it,
which is how it learns of what the cycle has proposed so far; a rule that
spends money reads those proposals to leave alone what they have spoken for.
"""

from __future__ import annotations

from collections import Counter
from typing import NamedTuple

from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.ledger import OrderLedger
from ucla_agent.games.redalert.world import World

BUILDING_QUEUE = "Building"
DEFENSE_QUEUE = "Defense"
INFANTRY_QUEUE = "Infantry"
VEHICLE_QUEUE = "Vehicle"


class Proposal(NamedTuple):
    """One `build` or `train` a rule of this cycle has proposed."""

    item: str
    count: int
    priority: Priority


class Production:
    """The queues, the orders on their way and the power, for the rules of one cycle. See the module's note."""

    def __init__(self, world: World, ledger: OrderLedger) -> None:
        self._world = world
        self._standing = Counter(building.type for building in world.buildings.values())
        self._units = Counter(unit.type for unit in world.units.values())
        # Items in a queue, a finished one that waits to be placed included, and items ordered but not yet showing.
        self._pending = Counter(job.item for jobs in world.production.values() for job in jobs)
        self._taken_queues = set(world.production)
        # The same, counted by the queue they are made in.
        self._queued = Counter({queue: len(jobs) for queue, jobs in world.production.items()})
        for reservation in ledger.reservations:
            self._pending[reservation.item] += reservation.count
            self._taken_queues.add(reservation.queue)
            self._queued[reservation.queue] += reservation.count
        self._reserved_drain = sum(self.drain(item) * count for item, count in self._pending.items())
        self._proposed: list[Proposal] = []

    def proposed(self) -> tuple[Proposal, ...]:
        """What the rules of this cycle have proposed so far, in the order they proposed it."""
        return tuple(self._proposed)

    def standing(self, item: str) -> int:
        """How many own buildings of a type stand."""
        return self._standing[item]

    def units(self, item: str) -> int:
        """How many own units of a type there are."""
        return self._units[item]

    def pending(self, item: str) -> int:
        """How many of an item are on their way: in a queue, ordered and not yet showing, or proposed in this cycle."""
        return self._pending[item]

    def planned(self, item: str) -> int:
        """How many buildings of a type stand or are on their way."""
        return self._standing[item] + self._pending[item]

    def is_queue_free(self, queue: str) -> bool:
        """Whether a queue holds nothing, has no order on its way and was given nothing in this cycle."""
        return queue not in self._taken_queues

    def queued(self, queue: str) -> int:
        """How many items a queue holds, has ordered and not yet showing, and was given in this cycle."""
        return self._queued[queue]

    def cost(self, item: str) -> int:
        """The credits one of an item takes; 0 for an item the table does not know."""
        facts = self._world.items.get(item)
        return facts.cost if facts is not None else 0

    def drain(self, item: str) -> int:
        """The power one of an item takes once it stands; 0 when it provides power or the table does not know it."""
        facts = self._world.items.get(item)
        return max(0, -facts.power) if facts is not None else 0

    def headroom(self) -> int:
        """The power left over once everything on its way is placed: provided, less drained, less reserved."""
        economy = self._world.economy
        return economy.power_provided - economy.power_drained - self._reserved_drain

    def planned_drain(self) -> int:
        """The power the base will drain once everything on its way is placed: drained now, and reserved."""
        return self._world.economy.power_drained + self._reserved_drain

    def can_power(self, item: str, keep: int = 0) -> bool:
        """Whether an item can be added and leave at least `keep` power over: by default, without going below zero."""
        return self.headroom() - self.drain(item) >= keep

    def build(self, item: str, priority: Priority = Priority.PRODUCTION) -> Intent:
        """The intent to build one of an item, noted so that the rest of the cycle sees its queue and power taken."""
        self._note(Proposal(item, 1, priority))
        return Intent(priority, {"cmd": "build", "item": item})

    def train(self, item: str, count: int = 1, priority: Priority = Priority.PRODUCTION) -> Intent:
        """The intent to train `count` of a unit, noted so that the rest of the cycle sees its queue taken."""
        self._note(Proposal(item, count, priority))
        return Intent(priority, {"cmd": "train", "item": item, "count": count})

    def _note(self, proposal: Proposal) -> None:
        self._proposed.append(proposal)
        item, count = proposal.item, proposal.count
        self._pending[item] += count
        self._taken_queues.add(self._queue(item))
        self._queued[self._queue(item)] += count
        self._reserved_drain += self.drain(item) * count

    def _queue(self, item: str) -> str:
        """The queue an item is made in; an item the table does not know is taken to have one of its own."""
        facts = self._world.items.get(item)
        return facts.queue if facts is not None and facts.queue else item
