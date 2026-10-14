"""More war factories sooner, kept producing tanks: what `tank_factories` of the tuning file's [producers] switches on.

The upkeep rules do the work (see `upkeep`); this module holds what the
switch adds to them and why it is shaped as it is.

The state shows one Vehicle queue however many war factories stand, and the
engine makes one item of it at a time: the second item of the queue waits at
no progress until the first is done. Each war factory more makes that item
sooner. Match records of real games with two war factories standing show a
medium tank built in 15.3 s against 20.4 s with one, a heavy tank in 20.7 s
against 27.6 s, a harvester in 19.8 s against 26.4 s: three quarters of the
time. No war factory reports an item of its own in production, and no queue
of its own appears. Ordering a vehicle for each factory, or making each
primary in turn, would add nothing. What extra factories need is a queue
that never stands empty, so the Vehicle queue is kept filled: up to
`tank_factories_queued` vehicles, ordered as the unit budget pays for them.
The engine charges an item as it is built, so a vehicle that waits its turn
costs nothing until it starts.

The platform queues every `train` it is sent, and the order ledger holds a
train back only until its item shows in the queue. Once the queue already
holds a vehicle of that type, a state that has not taken the new order in
yet shows it all the same, and the ledger lets go too soon. So the top-up
itself is remembered until a state shows the queue as long as it was to
become, or the ledger's own wait for an order to show is over.

Once two war factories stand, vehicles take at least `VEHICLE_FLOOR_SHARE` of
the unit spending when the money pays for one unit queue only: infantry goes
first then only while the vehicles keep that share with the infantry's price
spent (see `upkeep`). Comparing the share before the purchase would let every
infantry order take it below the floor.
"""

from __future__ import annotations

from ucla_agent.games.redalert.family import WAR_FACTORY
from ucla_agent.games.redalert.ledger import RESERVATION_TIMEOUT_TICKS
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.production import VEHICLE_QUEUE
from ucla_agent.games.redalert.world import World

# With the switch on, vehicles take at least this share of the unit spending once this many war factories stand.
VEHICLE_FLOOR_SHARE = 0.6
VEHICLE_FLOOR_WAR_FACTORIES = 2


class VehicleFill:
    """How many vehicles the Vehicle queue takes in this cycle. See the module's note."""

    def __init__(self) -> None:
        # The tick of the last top-up, and how many items the Vehicle queue was to hold once it showed; None while no
        # top-up waits to show.
        self._awaited: tuple[int, int] | None = None

    def room(self, world: World, ctx: MatchContext) -> int:
        """With the switch on, how many fewer items than `tank_factories_queued` the Vehicle queue holds, counting a
        top-up no state shows yet; with it off, one while the queue is free and none while it is not."""
        production, tuning = ctx.production, ctx.tuning.producers
        if not tuning.tank_factories:
            return 1 if production.is_queue_free(VEHICLE_QUEUE) else 0
        queued = production.queued(VEHICLE_QUEUE)
        if self._awaited is not None:
            sent_tick, expected = self._awaited
            shown = len(world.production.get(VEHICLE_QUEUE, ()))
            if shown >= expected or world.tick - sent_tick >= RESERVATION_TIMEOUT_TICKS:
                self._awaited = None
            else:
                queued = max(queued, expected)
        return max(0, tuning.tank_factories_queued - queued)

    def note_top_up(self, world: World, ctx: MatchContext) -> None:
        """Remember a top-up proposed in this cycle: the queue is to hold all the cycle has given it besides what it
        holds."""
        if ctx.tuning.producers.tank_factories:
            self._awaited = (world.tick, ctx.production.queued(VEHICLE_QUEUE))


def keeps_vehicle_floor(ctx: MatchContext) -> bool:
    """Whether vehicles are to take at least `VEHICLE_FLOOR_SHARE` of the unit spending: the switch is on and
    `VEHICLE_FLOOR_WAR_FACTORIES` war factories or more stand."""
    is_on = ctx.tuning.producers.tank_factories
    return is_on and ctx.production.standing(WAR_FACTORY) >= VEHICLE_FLOOR_WAR_FACTORIES


def vehicle_queue_takes_one(ctx: MatchContext) -> bool:
    """Whether the Vehicle queue takes one more item: with the switch on while it holds fewer than
    `tank_factories_queued`, so that a queue kept filled shuts nothing out for good; with it off while it is free."""
    production, tuning = ctx.production, ctx.tuning.producers
    if not tuning.tank_factories:
        return production.is_queue_free(VEHICLE_QUEUE)
    return production.queued(VEHICLE_QUEUE) < tuning.tank_factories_queued
