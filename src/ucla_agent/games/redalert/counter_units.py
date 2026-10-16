"""The counter quota on the unit queues: the counter units the enemy army seen calls for, trained first.

With `counter_quota` of [composition] on, the table of `composition` says how
many counter units of each item the enemy army seen lately calls for
(`counter_quota`). In every cycle the unit queues are fed (see `upkeep`),
each queue that makes an item of the quota the army is short of trains the
one it is furthest short of before the mix's pick: infantry
`infantry_at_a_time` at a time, a vehicle one at a time. Short is the number
wanted less the own units of that item in main, the guard and the escort and
those on their way; the raid group and the scouts are not counted. Only an
item on offer is trained, and only by a queue the plan in force feeds at all:
under PRESERVE no counter vehicle is bought, and the raid quota's infantry
keeps the Infantry queue while the raid is short of it. While
`no_army_infantry` of [upkeep] holds the army's infantry back, the Infantry
queue's counter is a rocket soldier or a flamethrower, which that rule keeps
exempt; the riflemen wait, but while the counter is lifted (below).

No counter unit is trained while those of the quota's items in main, the
guard and the escort are worth, at cost, `counter_max_share` of all their
combat units: the mix's main tank keeps the rest of the production, so that
the tanks never stop. With `counter_cap_skips_rifles` on, riflemen are left
out of the counters summed: they are the base army's own infantry as much as
the Allied counter, and an army of them read as capped from the start.
One `counter_quota` event says what is wanted each time the counts wanted, or whether they are capped, change: the enemy counted (R
rocket soldiers, N other infantry, T tanks), the items and counts wanted and
the capped flag.

A Soviet base wants flamethrowers, which a flame tower puts on offer. While
the quota wants them and none is on offer, one flame tower is ordered for
them, once in a match: when the Defense queue is free, no flame tower stands
or is on its way, and the money above the production reserve covers it. It
is placed on the side of the main base facing the point `TOWARD_ENEMY_CELLS`
from the base's centre toward the enemy's start, and it is a trigger of its
own: `defenses_on_threat_only` does not hold it back.

With `infantry_counter_lift` of [composition] on, the counter is lifted while
the enemy is infantry-heavy (`is_counter_lifted`): tanks crush a few rifles,
but an army of rockets and rifles beats the tanks, so the blocks a war factory
puts on the counter infantry let it through. Base defense's counter against
an infantry threat is then the family's counter infantry
(`lifted_infantry_counter`: the Soviet flamethrower while it is on offer, else
the rifleman, never the grenadier; the Allied rifleman) where the tank army
would have been left it, and no_army_infantry lets the quota's riflemen
through up to the army's shortfall of them. Once the enemy is no longer
infantry-heavy the blocks hold as before.

With `counter_on_approach` of [composition] on, while the approach alarm is
in force (see `approach_alarm`) the counters are trained even while upkeep's
unit queues are otherwise held, in the opening or by an economy hold
(`CounterFill.on_approach`): the item each free unit queue upkeep may use is
furthest short of, infantry `infantry_at_a_time` at a time and a vehicle one,
within the money above the production reserve.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ucla_agent.core import records
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.batch import Intent
from ucla_agent.games.redalert.composition import (
    FLAMETHROWER,
    Composition,
    CounterQuota,
    counter_quota,
    is_enemy_infantry_heavy,
)
from ucla_agent.games.redalert.expansion import ESCORT
from ucla_agent.games.redalert.family import RIFLE_INFANTRY, ROCKET_INFANTRY
from ucla_agent.games.redalert.groups import GUARD, MAIN
from ucla_agent.games.redalert.items import SOVIET
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.production import DEFENSE_QUEUE, INFANTRY_QUEUE, VEHICLE_QUEUE
from ucla_agent.games.redalert.scout_missions import enemy_start
from ucla_agent.games.redalert.world import Cell, World

COUNTER_QUOTA_EVENT = "counter_quota"
# The groups whose units count toward a quota and toward the army the cap is a share of.
COUNTED_GROUPS = frozenset({MAIN, GUARD, ESCORT})
# The Soviet flame tower, which puts the flamethrower on offer.
FLAME_TOWER = "ftur"
# A defense put toward the enemy stands on the side of the main base facing the point this many cells from the base's
# centre toward the enemy's start: the side an attack comes from, near enough to cover the base.
TOWARD_ENEMY_CELLS = 15
# The counter infantry no_army_infantry lets through as it does the mix's: rockets against armor, flamers against
# infantry.
_HELD_EXEMPT = frozenset({ROCKET_INFANTRY, FLAMETHROWER})


@dataclass(frozen=True)
class CounterFirst:
    """What the unit queues make once the counter quota is filled first: the composition, and how many of the
    Infantry queue's counter item the army is short of; None while the Infantry queue makes its own pick."""

    composition: Composition
    infantry_short: int | None = None


class CounterFill:
    """Puts the counter quota's shortfall first on the unit queues, and writes the quota when it changes. See the
    module's note."""

    def __init__(self) -> None:
        # The quota and the capped flag last written; None before the first.
        self._written: tuple[tuple[tuple[str, int], ...], bool] | None = None

    def first(
        self, produces: Composition, made: Composition, world: World, ctx: MatchContext, infantry_held: bool
    ) -> CounterFirst:
        """`made`, what the unit queues would make of the plan's composition `produces`, with each queue the plan
        feeds given the counter item it is furthest short of in place of its own items; `made` as it is while the
        counter units are capped or none is short. While `infantry_held` (`no_army_infantry`), the Infantry queue's
        counter is only one that rule lets through (`passes_no_army_infantry`)."""
        quota = counter_quota(world.family, world, ctx)
        if not quota.wanted:
            self._note(quota, False, world)
            return CounterFirst(made)
        capped = is_capped(quota.wanted, world, ctx)
        self._note(quota, capped, world)
        if capped:
            return CounterFirst(made)
        short = shortfalls(quota.wanted, world, ctx)
        is_lifted = infantry_held and is_counter_lifted(world, ctx)
        infantry_short = {
            item: count
            for item, count in short.items()
            if not infantry_held or passes_no_army_infantry(item, is_lifted)
        }
        infantry = _most_short(infantry_short, INFANTRY_QUEUE, world) if _feeds_infantry(produces) else None
        vehicle = _most_short(short, VEHICLE_QUEUE, world) if produces.vehicle else None
        infantry_mix = {infantry: sum(produces.infantry.values())} if infantry is not None else made.infantry
        vehicle_mix = {vehicle: produces.vehicle_share} if vehicle is not None else made.vehicle
        composition = Composition(infantry=infantry_mix, vehicle=vehicle_mix, raid_infantry=made.raid_infantry)
        return CounterFirst(composition, None if infantry is None else short[infantry])

    def on_approach(self, world: World, ctx: MatchContext, manager: str) -> list[Intent]:
        """With counter_on_approach on and the approach alarm in force, the training of the quota's shortfall on each
        free unit queue no manager but `manager` claims, while upkeep's unit queues are otherwise held (see the
        module's note); none otherwise, or while the counters are capped."""
        if not (ctx.approach_alarm and ctx.tuning.composition.counter_on_approach):
            return []
        quota = counter_quota(world.family, world, ctx)
        if not quota.wanted or is_capped(quota.wanted, world, ctx):
            return []
        short, production = shortfalls(quota.wanted, world, ctx), ctx.production
        money = world.economy.spendable - ctx.tuning.upkeep.production_cash_reserve
        orders = []
        for queue, most in ((INFANTRY_QUEUE, ctx.tuning.upkeep.infantry_at_a_time), (VEHICLE_QUEUE, 1)):
            item = _most_short(short, queue, world)
            if item is None or not production.is_queue_free(queue) or ctx.claims.is_held_by_other(queue, manager):
                continue
            count = min(most, short[item], money // max(1, production.cost(item)))
            if count > 0:
                orders.append(production.train(item, count))
                money -= count * production.cost(item)
        return orders

    def _note(self, quota: CounterQuota, capped: bool, world: World) -> None:
        """Write the `counter_quota` event when the counts wanted or the capped flag differ from those last written;
        nothing before anything is wanted."""
        written = (tuple(quota.wanted.items()), capped)
        if written == self._written or (self._written is None and not quota.wanted):
            return
        self._written = written
        counted = quota.counted
        records.log(
            COUNTER_QUOTA_EVENT,
            {
                "tick": world.tick,
                "rockets": counted.rockets,
                "infantry": counted.infantry,
                "tanks": counted.tanks,
                "wanted": dict(quota.wanted),
                "capped": capped,
            },
        )


def counted_units(world: World, ctx: MatchContext) -> dict[str, int]:
    """The own combat units of main, the guard and the escort, by type, a variant as its type."""
    counts: dict[str, int] = {}
    for unit in world.combat_units.values():
        facts = world.items.get(unit.type)
        if facts is not None and ctx.groups.group_of(unit.id) in COUNTED_GROUPS:
            counts[facts.name] = counts.get(facts.name, 0) + 1
    return counts


def is_capped(wanted: Mapping[str, int], world: World, ctx: MatchContext) -> bool:
    """Whether the own units of the quota's items in main, the guard and the escort are worth, at cost,
    `counter_max_share` of all their combat units; never while there are none. With counter_cap_skips_rifles on, the
    riflemen are not summed among the counters."""
    counts, items = counted_units(world, ctx), world.items
    worth = {name: count * items.get(name).cost for name, count in counts.items()}
    skipped = RIFLE_INFANTRY if ctx.tuning.composition.counter_cap_skips_rifles else None
    counters = sum(value for name, value in worth.items() if name in wanted and name != skipped)
    return counters > 0 and counters >= ctx.tuning.composition.counter_max_share * sum(worth.values())


def shortfalls(wanted: Mapping[str, int], world: World, ctx: MatchContext) -> dict[str, int]:
    """How many of each item of the quota the army is short of: those wanted less the own units of it in main, the
    guard and the escort and those on their way; only the items short of some."""
    counts = counted_units(world, ctx)
    short = {item: count - counts.get(item, 0) - ctx.production.pending(item) for item, count in wanted.items()}
    return {item: count for item, count in short.items() if count > 0}


def _feeds_infantry(produces: Composition) -> bool:
    """Whether the counter quota may take the Infantry queue: the plan feeds it, and not with the raid quota's item."""
    return bool(produces.infantry) and not produces.raid_infantry


def _most_short(short: Mapping[str, int], queue: str, world: World) -> str | None:
    """The item a queue makes and has on offer that the army is furthest short of; the first of the quota on a tie;
    None when it is short of none."""
    made = [item for item in short if item in world.available_production and _queue_of(item, world) == queue]
    return max(made, key=lambda item: short[item], default=None)


def _queue_of(item: str, world: World) -> str | None:
    facts = world.items.get(item)
    return None if facts is None else facts.queue


def toward_enemy(world: World) -> Cell | None:
    """The point `TOWARD_ENEMY_CELLS` from the base's centre toward the enemy's start; None while either is not
    known."""
    start = enemy_start(world)
    if world.base_center is None or start is None:
        return None
    return cells.toward(world.base_center, start, TOWARD_ENEMY_CELLS)


def build_toward_enemy(item: str, orders: list[Intent], world: World, ctx: MatchContext) -> list[Intent]:
    """`orders`, and with the defense `item` among them its goal set toward the enemy's start (`toward_enemy`), so
    that upkeep's placement puts it on that side of the base."""
    goal = toward_enemy(world)
    if goal is not None and any(intent.order.get("item") == item for intent in orders):
        ctx.defense_goals[item] = goal
    return orders


class FlameTowerForCounters:
    """Orders the one flame tower a Soviet counter quota of flamethrowers waits for. See the module's note."""

    def __init__(self) -> None:
        self._is_ordered = False

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for the flame tower, once in a match, while it is due (`_is_due`)."""
        if self._is_ordered or not self._is_due(world, ctx):
            return []
        self._is_ordered = True
        return build_toward_enemy(FLAME_TOWER, [ctx.production.build(FLAME_TOWER)], world, ctx)

    def _is_due(self, world: World, ctx: MatchContext) -> bool:
        """Whether the quota of a Soviet base wants flamethrowers that are not on offer, a flame tower is, none stands
        or is on its way, the Defense queue is free and the money above the production reserve covers it."""
        production = ctx.production
        if world.family != SOVIET or FLAMETHROWER in world.available_production:
            return False
        if FLAME_TOWER not in world.available_production or production.planned(FLAME_TOWER):
            return False
        if FLAMETHROWER not in counter_quota(world.family, world, ctx).wanted:
            return False
        money = world.economy.spendable - ctx.tuning.upkeep.production_cash_reserve
        return production.is_queue_free(DEFENSE_QUEUE) and money >= production.cost(FLAME_TOWER)


def passes_no_army_infantry(item: str, is_lifted: bool) -> bool:
    """Whether a counter item of the Infantry queue is trained while `no_army_infantry` holds the army's infantry
    back: the rocket soldier and the flamethrower, which that rule keeps exempt, and while the counter `is_lifted`
    (`is_counter_lifted`) the rifleman as well."""
    return item in _HELD_EXEMPT or (is_lifted and item == RIFLE_INFANTRY)


def is_counter_lifted(world: World, ctx: MatchContext) -> bool:
    """Whether the war factory's blocks on counter infantry are lifted: `infantry_counter_lift` is on and the enemy
    army counted is infantry-heavy (`is_enemy_infantry_heavy`)."""
    tuning = ctx.tuning.composition
    return tuning.infantry_counter_lift and is_enemy_infantry_heavy(world, tuning)


def lifted_infantry_counter(world: World) -> str:
    """The family's counter infantry against an infantry threat while the counter is lifted: the Soviet flamethrower
    while it is on offer, else the rifleman; never the grenadier."""
    if world.family == SOVIET and FLAMETHROWER in world.available_production:
        return FLAMETHROWER
    return RIFLE_INFANTRY
