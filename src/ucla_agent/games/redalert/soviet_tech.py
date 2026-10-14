"""The Soviet tech center: one stek once the base has outgrown its money, which opens the Mammoth tank.

The Soviet main force is built around the heavy tank with V2s behind it (see
`composition`), and the Mammoth tank joins it once a tech center stands. The
tech center is dear and opens nothing else the agent builds, so it waits for
a base whose money piles up faster than its war factories spend it: with
`soviet_tech_center` on, 2 or more war factories standing and the spendable
money at least `soviet_tech_min_money` in every state of the last
`soviet_tech_seconds`, one stek is ordered. It is on offer only once its
radar stands, and it is ordered only while none stands or is on its way, the
Building queue is free, no full hold for the second yard's MCV is in force
and no money is saved for a lost core building. It goes through upkeep's
power rule (the power plant first when the stek would leave less than the
power target) and is placed like any other building.

The money is followed in every state, so the rule is asked during a full
hold too, and orders nothing then. Each order, the power plant the power
target puts first included, is written as one `economy` event of the reason
`soviet_tech`, with the item and the money.
"""

from __future__ import annotations

from typing import Callable

from ucla_agent.games.redalert.bank_spending import log_orders
from ucla_agent.games.redalert.batch import Intent
from ucla_agent.games.redalert.composition import TECH_CENTER
from ucla_agent.games.redalert.family import WAR_FACTORY
from ucla_agent.games.redalert.items import SOVIET, TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.production import BUILDING_QUEUE
from ucla_agent.games.redalert.world import World

# The reason the `economy` event of a tech center order gives.
SOVIET_TECH = "soviet_tech"
# The war factories that stand before a tech center is ordered: the base makes tanks faster than one factory can.
TECH_WAR_FACTORIES = 2

# How upkeep orders a building within its power target: called as `within_power(item, world, ctx)`, it gives the
# order for the building, or for the power plant that has to come first.
WithinPower = Callable[[str, World, MatchContext], list[Intent]]
# The lost core building money is saved for, if any: called as `saved_for(world, ctx)`.
SavedFor = Callable[[World, MatchContext], str | None]


class SovietTechCenter:
    """Orders one Soviet tech center when it is due. See the module's note."""

    def __init__(self, within_power: WithinPower, saved_for: SavedFor) -> None:
        self._within_power = within_power
        self._saved_for = saved_for
        # The tick of the first state of the run of states, up to the last one, whose spendable money was at least
        # soviet_tech_min_money; None while the last one held less.
        self._rich_since: int | None = None

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for the tech center, or for the power plant it waits for, when it is due; none else."""
        tuning = ctx.tuning.producers
        if not tuning.soviet_tech_center or world.family != SOVIET:
            return []
        is_rich = world.economy.spendable >= tuning.soviet_tech_min_money
        self._rich_since = (world.tick if self._rich_since is None else self._rich_since) if is_rich else None
        if ctx.full_hold or not self._is_due(world, ctx):
            return []
        return log_orders(SOVIET_TECH, self._within_power(TECH_CENTER, world, ctx), world)

    def _is_due(self, world: World, ctx: MatchContext) -> bool:
        """Whether the tech center is due: the money has stayed rich long enough, enough war factories stand, none is
        standing or on its way, it is on offer with the Building queue free, and no lost core building is saved for."""
        production, seconds = ctx.production, ctx.tuning.producers.soviet_tech_seconds
        has_lasted = self._rich_since is not None and world.tick - self._rich_since >= seconds * TICKS_PER_SECOND
        is_wanted = production.standing(WAR_FACTORY) >= TECH_WAR_FACTORIES and not production.planned(TECH_CENTER)
        can_start = TECH_CENTER in world.available_production and production.is_queue_free(BUILDING_QUEUE)
        return has_lasted and is_wanted and can_start and self._saved_for(world, ctx) is None
