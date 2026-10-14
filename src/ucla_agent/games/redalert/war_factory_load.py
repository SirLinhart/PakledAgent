"""Whether the production load justifies one more war factory: the gate every rule that orders a war factory beyond the
first goes through, with `war_factory_load_gate` of the tuning file's [producers] on.

The rules that add war factories (the producer gates and `tank_factories`'s,
the bank, the idle bank and the storage rule, see `upkeep` and
`bank_spending`) each look at something of their own: an income step, money
banked, ore near the cap. None of them asks whether the war factories that
stand are kept busy, and a war factory the income cannot keep busy is money
that fights nothing. This gate asks it, on top of each rule's own terms.

How the game makes vehicles. The state shows one Vehicle queue however many
war factories stand, and the engine builds one item of it at a time. The
engine's rules (the RA mod of OpenRA, `mods/ra/rules/player.yaml`,
`ClassicProductionQueue@Vehicle`) give that queue `SpeedUp` with
`BuildTimeSpeedReduction: 100, 75, 60, 50`: with 2 war factories an item takes
75 percent of its time with one, with 3 60 percent, and with 4 or more 50
percent, the count clamped to the table. Match records agree: a medium tank
in 15.3 s with two against 20.4 s with one (see `tank_factories`). So one more
war factory adds no production line of its own: it makes the one line faster,
by less with each one more and not at all past the fourth.

An item takes `BUILD_SECONDS_PER_CREDIT` seconds a credit with one war factory,
and the engine charges it as it builds, so the Vehicle queue building without
pause at N war factories spends 1 / (BUILD_SECONDS_PER_CREDIT x share) credits
a second, share the build-time share at N: 41.7, 55.6, 69.4 and 83.3 at 1, 2,
3 and 4 or more.

The load. What the war factories could spend is the vehicle income: the
income estimate, in credits a second over its window, less what the Infantry
queue and MCVs spent a second over the same window (see `income`). An MCV
builds the base, not the army, and is bought for expansion whatever the load:
its price is no load, and this gate never holds one back.

The gate. With the switch on, a war factory beyond the first is ordered only
while the vehicle income is at least `war_factory_load_share` of the queue's
spend rate at N war factories, N those standing or on their way: the ones
there are to be kept that busy before one more is worth its price. At 0.8 the
2nd needs 33.3 a second, the 3rd 44.4, the 4th 55.6 and any more 66.7. An
unknown income reaches no gate. The first war factory is never gated: it is
the base's only way to make vehicles at all.

Each refusal of a war factory a rule would otherwise order now is written as
one `economy` event of the reason `war_factory_load_wait`: the rule, the
vehicle income, N and the income needed; one in `LOG_COOLDOWN_SECONDS` at
most, so that a refusal that holds for minutes does not fill the record.

The gate as the trigger. On top of the rules, the gate only held war
factories back: the producer rule still waited for its income steps
(`factories_income`, or `tank_factories_income` from
`tank_factories_from_seconds`), which an income of 41 to 75 a second never
reached, though it kept a 2nd war factory 33.3 a second needs busy. With
`war_factory_on_load` of [producers] on as well as the gate (with the gate
off it does nothing), the gate is that rule's trigger instead: from
`war_factory_on_load_from_seconds` on, while one war factory stands, fewer
than `max_war_factories` stand or are on their way, none is on its way and
one is on offer, one more is wanted (`is_on_load_wanted`); it is due once
the money above the production reserve, with `float_seconds` of the income,
pays for its price (`has_money_within`): with `economy_first` on the unit
queues wait for it meanwhile, so the money gathers at the income. The
income steps, their busy shares and their time no longer apply then. The
rule still asks the gate (`LoadGate.allows`, the rule `war_factory_on_load`)
and each order it makes is one `economy` event of the reason
`war_factory_on_load`. The bank, idle-bank and storage rules keep their own
terms behind the gate; the first war factory and MCVs are left alone.
"""

from __future__ import annotations

from ucla_agent.core import records
from ucla_agent.games.redalert.bank_spending import ECONOMY_EVENT
from ucla_agent.games.redalert.family import WAR_FACTORY
from ucla_agent.games.redalert.items import BUILD_SECONDS_PER_CREDIT, TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.world import World

# The reason of the `economy` event a refusal writes.
LOAD_WAIT = "war_factory_load_wait"
# The rule a refusal names for the producer gates, with `tank_factories` off and on; the bank rules and the storage
# rule are named by the reasons of their own `economy` events.
PRODUCER_GATES = "producer_gates"
TANK_FACTORY_GATES = "tank_factories"
# The rule a refusal names, and the reason of the `economy` event each order gives, with `war_factory_on_load` on.
ON_LOAD = "war_factory_on_load"
# The build time of a Vehicle-queue item with 1, 2, 3 and 4 or more war factories standing, as a share of its time with
# one: the engine's BuildTimeSpeedReduction for that queue.
VEHICLE_BUILD_TIME_SHARES = (1.0, 0.75, 0.6, 0.5)
# A refusal is written once in this many seconds at most.
LOG_COOLDOWN_SECONDS = 30
_DECIMALS = 1


def queue_spend_per_s(factories: int) -> float:
    """The credits a second the Vehicle queue spends building without pause with so many war factories, one at the
    least."""
    shares = VEHICLE_BUILD_TIME_SHARES
    share = shares[min(max(factories, 1), len(shares)) - 1]
    return 1.0 / (BUILD_SECONDS_PER_CREDIT * share)


def needed_income_per_s(factories: int, load_share: float) -> float:
    """The vehicle income a second one more war factory needs with so many standing or on their way."""
    return load_share * queue_spend_per_s(factories)


def is_loaded(ctx: MatchContext) -> bool:
    """Whether the load lets one more war factory be ordered: the switch off, none standing or on its way, or a known
    vehicle income that reaches what one more needs."""
    tuning = ctx.tuning.producers
    factories = ctx.production.planned(WAR_FACTORY)
    if not tuning.war_factory_load_gate or factories == 0:
        return True
    income = ctx.income.vehicle_income_per_s
    return income is not None and income >= needed_income_per_s(factories, tuning.war_factory_load_share)


def is_on_load(ctx: MatchContext) -> bool:
    """Whether the load gate is the producer rule's trigger for a war factory beyond the first: `war_factory_on_load`
    and `war_factory_load_gate` both on."""
    tuning = ctx.tuning.producers
    return tuning.war_factory_on_load and tuning.war_factory_load_gate


def is_on_load_wanted(world: World, ctx: MatchContext) -> bool:
    """Whether one more war factory is wanted on the load, the load and the money aside: in time, one standing, fewer
    than `max_war_factories` standing or on their way, none on its way and one on offer."""
    tuning, production = ctx.tuning.producers, ctx.production
    is_in_time = world.game_seconds >= tuning.war_factory_on_load_from_seconds
    has_room = production.standing(WAR_FACTORY) > 0 and production.planned(WAR_FACTORY) < tuning.max_war_factories
    is_free = not production.pending(WAR_FACTORY) and WAR_FACTORY in world.available_production
    return is_in_time and has_room and is_free


def has_money_within(world: World, ctx: MatchContext) -> bool:
    """Whether the money above the production reserve pays for one more war factory now, or will with `float_seconds`
    of the income; with the income unknown, only now."""
    income = ctx.income.income_per_s or 0.0
    coming = income * ctx.tuning.producers.float_seconds
    money = world.economy.spendable - ctx.tuning.upkeep.production_cash_reserve
    return money + coming >= ctx.production.cost(WAR_FACTORY)


class LoadGate:
    """The gate as the producer rule asks it, and when it last wrote a refusal. See the module's note."""

    def __init__(self) -> None:
        # The tick of the last refusal written; None before the first.
        self._logged_at: int | None = None

    def allows(self, rule: str, world: World, ctx: MatchContext) -> bool:
        """Whether the war factory a rule would order now may be ordered (`is_loaded`); a refusal is written as an
        `economy` event of the reason `war_factory_load_wait`, one in `LOG_COOLDOWN_SECONDS` at most."""
        if is_loaded(ctx):
            return True
        is_rested = self._logged_at is None or world.tick - self._logged_at >= LOG_COOLDOWN_SECONDS * TICKS_PER_SECOND
        if is_rested:
            self._logged_at = world.tick
            _log_wait(rule, world, ctx)
        return False


def _log_wait(rule: str, world: World, ctx: MatchContext) -> None:
    """Write one refusal: the rule, the vehicle income (null while unknown), the war factories standing or on their
    way and the income one more needs."""
    factories = ctx.production.planned(WAR_FACTORY)
    income = ctx.income.vehicle_income_per_s
    needed = needed_income_per_s(factories, ctx.tuning.producers.war_factory_load_share)
    data = {
        "tick": world.tick,
        "reason": LOAD_WAIT,
        "rule": rule,
        "vehicle_income_per_s": None if income is None else round(income, _DECIMALS),
        "war_factories": factories,
        "needed_per_s": round(needed, _DECIMALS),
    }
    records.log(ECONOMY_EVENT, data)
