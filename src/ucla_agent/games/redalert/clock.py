"""Clock play: money left over at the end of a match is money wasted, and a lead is kept behind defenses.

A match that runs its full time is decided on the trade balance and then on
the value of what each side has standing. Money in the bank counts for
nothing there, so in the last two minutes (the last three when the balance is
even, and the plan is HOLD by R3) every empty Defense, Vehicle and Infantry
queue is given the most expensive defense or fighting unit the money can
still pay for, the dearest first, until less than 300 is left. The reserve
the unit queues otherwise keep for the buildings is waived then: no building
started that late is worth more than what the money buys at once. The
Defense queue is one of them only with `endgame_defenses` on, or while a
ground enemy hit an own building within the last minute: a defense bought
with no attack on the base is standing value that never fights, and the
money goes to the unit queues instead.

While a lead is preserved (PRESERVE), the Defense queue builds a defense
once a minute while there is the money for it, so that an enemy that comes
for the lead meets more than the army that stays home. It is the defense
against infantry. With upkeep's `preserve_anti_armor` switch on, it is the
defense against armor, the Tesla coil or the gun, when the enemy army seen is
mostly vehicles (at least the army mix's `vehicle_heavy_share` of its value),
since a final wave of tanks rolls over flame towers and pillboxes. That one
drains more power, and is held to the power target upkeep keeps, a power
plant first when it would leave less. The switch ships on, for both
families. With upkeep's `defenses_on_threat_only` switched on, the lead adds
its defense only while the base is under attack from the ground: a threat of
aircraft alone adds none, since a defense against ground units does not reach
them and the anti-air defenses are upkeep's.

While the endgame lead is kept (rule R2E, see `endgame_lead`), every spare
credit goes to the strongest defense: the Tesla coil for the Soviets, the
turret for the Allies, the defense against infantry when that is not on
offer. Whenever the Defense queue is free and the money the game can spend
covers it, one is ordered, `endgame_lead_defenses_max` at the most, held to
the power target with a power plant first when it would leave less over;
the production reserve, `defenses_on_threat_only` and `endgame_defenses` do
not hold it back, and PreserveDefense's defense of every minute does not run
meanwhile. Its goal is the lead point, so that placement puts it on the
placeable cell nearest that point, between the bases. The unit queues, the
end-of-match spending among them, get only the money above the next
defense's price (`lead_reserve`). With no placeable cell within
`endgame_defense_cluster_cells` of the lead point, a defense takes the place
placement gives any defense, on the side facing the enemy's start. Each defense ordered is a `clock` event of
the reason `endgame_lead_defense`: the item, the lead point, the trade
balance, our army and the enemy army estimate.

The agent never sells a building: selling turns standing value into money,
and money counts for nothing.
"""

from __future__ import annotations

from typing import Callable

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, endgame_lead, placement
from ucla_agent.games.redalert.batch import Intent
from ucla_agent.games.redalert.composition import PRESERVE, seen_army_value
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD, FAMILIES
from ucla_agent.games.redalert.items import NON_COMBAT_TYPES, TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.plans import R3
from ucla_agent.games.redalert.production import DEFENSE_QUEUE, INFANTRY_QUEUE, VEHICLE_QUEUE
from ucla_agent.games.redalert.tuning import MixTuning
from ucla_agent.games.redalert.world import Cell, World

# The queues the end-of-match money goes to: what fights, as buildings and as units; the units alone while the base
# has not been hit from the ground for a while.
SPEND_QUEUES = (DEFENSE_QUEUE, VEHICLE_QUEUE, INFANTRY_QUEUE)
UNIT_SPEND_QUEUES = (VEHICLE_QUEUE, INFANTRY_QUEUE)
# A ground enemy that hit an own building within this many seconds puts the Defense queue back among them.
GROUND_HIT_SECONDS = 60
# How upkeep orders a defense within its power target: called as `within_power(item, world, ctx)`, it gives the order
# for the defense, or for the power plant that has to come first.
WithinPower = Callable[[str, World, MatchContext], list[Intent]]
CLOCK_EVENT = "clock"
ENDGAME_LEAD_DEFENSE = "endgame_lead_defense"


def is_spending(world: World, ctx: MatchContext) -> bool:
    """Whether the end-of-match spending is in force: the last minutes of the match, and money to spend."""
    clock = ctx.tuning.clock
    remaining = world.remaining_seconds
    is_late = ctx.plan_rule == R3 or (remaining is not None and remaining <= clock.spend_remaining_seconds)
    return is_late and world.economy.spendable >= clock.spend_floor_cash


class EndgameSpend:
    """Spends what is left at the end of a match on what fights, the dearest first, on every empty queue."""

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """One order for each empty queue that money can still fill, while money of the floor or more is left."""
        if not is_spending(world, ctx):
            return []
        production, floor = ctx.production, ctx.tuning.clock.spend_floor_cash
        money = world.economy.spendable - lead_reserve(world, ctx)
        free = [queue for queue in _spend_queues(world, ctx) if production.is_queue_free(queue)]
        orders = []
        while money >= floor:
            choices = [(production.cost(item), item) for item in _affordable(free, money, world, ctx)]
            if not choices:
                break
            cost, item = max(choices)
            orders.append(production.build(item) if _queue_of(item, world) == DEFENSE_QUEUE else production.train(item))
            free.remove(_queue_of(item, world))
            money -= cost
        return orders


def _spend_queues(world: World, ctx: MatchContext) -> tuple[str, ...]:
    """The queues the end-of-match money goes to: the Defense queue among them only with `endgame_defenses` on or a
    ground enemy's hit on an own building within the last GROUND_HIT_SECONDS."""
    hit = ctx.ground_hit_tick
    is_recent = hit is not None and world.tick - hit <= GROUND_HIT_SECONDS * TICKS_PER_SECOND
    return SPEND_QUEUES if ctx.tuning.clock.endgame_defenses or is_recent else UNIT_SPEND_QUEUES


def _affordable(queues: list[str], money: int, world: World, ctx: MatchContext) -> list[str]:
    """The items on offer for the queues given that fight, that the money pays for and that the power can carry."""
    production = ctx.production
    return [
        item
        for item in world.available_production
        if _queue_of(item, world) in queues
        and _fights(item, world)
        and production.cost(item) <= money
        and production.can_power(item)
    ]


def _queue_of(item: str, world: World) -> str | None:
    facts = world.items.get(item)
    return facts.queue if facts is not None else None


def _fights(item: str, world: World) -> bool:
    """Whether an item fights: it has a weapon, and is not one of the types that never fight."""
    facts = world.items.get(item)
    return facts is not None and facts.range is not None and item not in NON_COMBAT_TYPES


class PreserveDefense:
    """Builds a defense once a minute under PRESERVE, while the money allows: against infantry, or, with the
    `preserve_anti_armor` switch on, against armor when the enemy army seen is mostly vehicles. See the module's
    note."""

    def __init__(self, within_power: WithinPower) -> None:
        """A rule that holds a defense against armor to the power target the way `within_power` does."""
        # The tick the last such defense was ordered at; None while none was.
        self._ordered_tick: int | None = None
        self._within_power = within_power

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for one defense when one is due under PRESERVE and can be had, or for the power plant that a
        defense against armor has to wait for. With upkeep's `defenses_on_threat_only` on, only while the base is
        under attack and not by aircraft alone."""
        is_ground_threat = ctx.threat_active and not ctx.threat_aircraft_only
        is_waiting_for_threat = ctx.tuning.upkeep.defenses_on_threat_only and not is_ground_threat
        # The endgame lead's own defenses take the Defense queue and the money meanwhile.
        is_lead_kept = endgame_lead.is_in_force(ctx)
        if ctx.plan != PRESERVE or is_waiting_for_threat or is_lead_kept or not self._is_due(world, ctx):
            return []
        item, orders = self._defense(world, ctx)
        # A power plant ordered first is not the defense, which is still due.
        if any(intent.order.get("item") == item for intent in orders):
            self._ordered_tick = world.tick
        return orders

    def _defense(self, world: World, ctx: MatchContext) -> tuple[str, list[Intent]]:
        """The defense due, and the orders for it: none while it is not on offer or the Defense queue is busy."""
        names = FAMILIES[world.family]
        is_against_armor = ctx.tuning.upkeep.preserve_anti_armor and _is_vehicle_heavy(world, ctx.tuning.mix)
        item = names.anti_armor_defense if is_against_armor else names.anti_infantry_defense
        if item not in world.available_production or not ctx.production.is_queue_free(DEFENSE_QUEUE):
            return item, []
        # The defense against armor drains the more power and is held to the power target; the one against infantry
        # only keeps the base from going short.
        return item, self._within_power(item, world, ctx) if is_against_armor else _within_headroom(item, ctx)

    def _is_due(self, world: World, ctx: MatchContext) -> bool:
        """Whether a minute has passed since the last one, and the money is there."""
        clock = ctx.tuning.clock
        wait_ticks = clock.preserve_defense_seconds * TICKS_PER_SECOND
        has_waited = self._ordered_tick is None or world.tick - self._ordered_tick >= wait_ticks
        return has_waited and world.economy.spendable >= clock.preserve_defense_min_cash


def _within_headroom(item: str, ctx: MatchContext) -> list[Intent]:
    """The order for a defense that the power left over carries without going short; none when it does not."""
    production = ctx.production
    return [production.build(item)] if production.can_power(item) else []


def _is_vehicle_heavy(world: World, tuning: MixTuning) -> bool:
    """Whether the enemy army seen is mostly vehicles: at least `vehicle_heavy_share` of its value; not while none is
    seen."""
    value = seen_army_value(world)
    worth = sum(value.values())
    return worth > 0 and value[VEHICLE_QUEUE] >= tuning.vehicle_heavy_share * worth


class EndgameLeadDefense:
    """Builds the strongest defense back to back around the lead point while the endgame lead is kept. See the
    module's note."""

    def __init__(self, within_power: WithinPower) -> None:
        """A rule that holds each defense to the power target the way `within_power` does."""
        self._within_power = within_power
        # How many defenses were ordered under R2E.
        self._ordered = 0

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for the next defense, or for the power plant it has to wait for, while R2E is in force, fewer than
        `endgame_lead_defenses_max` were ordered, the Defense queue is free and the money covers it."""
        item = self.due(world, ctx)
        if item is None or not ctx.production.is_queue_free(DEFENSE_QUEUE):
            return []
        if world.economy.spendable < ctx.production.cost(item):
            return []
        orders = self._within_power(item, world, ctx)
        if any(intent.order.get("item") == item for intent in orders):
            self._ordered += 1
            self._note(item, world, ctx)
        return orders

    def due(self, world: World, ctx: MatchContext) -> str | None:
        """The defense the lead buys next: none while R2E is not in force or the most were ordered."""
        tuning = ctx.tuning.clock
        if not endgame_lead.is_in_force(ctx) or self._ordered >= tuning.endgame_lead_defenses_max:
            return None
        return strongest_defense(world)

    @staticmethod
    def _note(item: str, world: World, ctx: MatchContext) -> None:
        """Give the defense the lead point as its goal, so that placement puts it nearest there, and write the
        `clock` event of the reason `endgame_lead_defense`."""
        point = endgame_lead.lead_point(world, ctx.tuning.clock)
        if point is not None and _has_place_by(item, point, world, ctx):
            ctx.defense_goals[item] = point
            ctx.exact_defense_goals.add(item)
        estimate = ctx.enemy_army_estimate
        data = {
            "tick": world.tick,
            "reason": ENDGAME_LEAD_DEFENSE,
            "item": item,
            "cell": None if point is None else list(point),
            "balance": world.trade_balance,
            "army": endgame_lead.army_value(world, ctx),
            "enemy_estimate": None if estimate is None else round(estimate),
        }
        records.log(CLOCK_EVENT, data)


def _has_place_by(item: str, point: Cell, world: World, ctx: MatchContext) -> bool:
    """Whether placement has a cell for the defense within `endgame_defense_cluster_cells` of the lead point, among
    those laid out around the construction yard nearest it; without one, the defense takes the place placement gives
    any defense, on the side of the base facing the enemy's start."""
    yards = [(building.x, building.y) for building in world.buildings.values() if building.type == CONSTRUCTION_YARD]
    anchor = min(yards, key=lambda yard: cells.distance(yard, point), default=None)
    candidates = placement.candidate_cells(item, world, ctx.terrain, ctx.tuning.placement, anchor=anchor)
    cluster = ctx.tuning.clock.endgame_defense_cluster_cells
    return any(cells.is_within(cell, point, cluster) for cell in candidates)


def strongest_defense(world: World) -> str | None:
    """The defense the endgame lead buys: against armor (the Tesla coil, the turret) while it is on offer, else against
    infantry; None while neither is."""
    names = FAMILIES[world.family]
    offered = world.available_production
    return next((item for item in (names.anti_armor_defense, names.anti_infantry_defense) if item in offered), None)


def lead_reserve(world: World, ctx: MatchContext) -> int:
    """The money the unit queues leave for the endgame lead's next defense, its price, while R2E is in force; 0
    otherwise."""
    if not endgame_lead.is_in_force(ctx):
        return 0
    item = strongest_defense(world)
    return 0 if item is None else ctx.production.cost(item)
