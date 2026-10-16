"""The enemy army estimate: what the enemy army is likely worth now, in credits, from what this match has shown.

What the agent sees of the enemy army is only what is in sight or was lately.
An army seen two minutes ago and out of sight since has not gone away: it has
grown by what the enemy has spent since. With `enemy_army_estimate` of
[plan_rules] on, the plan rules bring an estimate E up to date once a cycle,
and every rule that weighs the enemy army against ours reads it from
`MatchContext.enemy_army_estimate` (see `spending_focus`).

- The seen value V is what the remembered enemy units that fight on the
  ground cost and are not stale: no harvester, MCV, aircraft or defense.
- The enemy's income I is its harvesters H_e, the more of the enemy
  harvesters and refineries remembered (our own harvesters while neither has
  been seen), times what one harvester brings in: our own income a harvester
  while the income is known and we have one, else
  `estimate_income_per_harvester` credits a second.
- The growth G is I x `estimate_army_spend_share` x the seconds since the
  last update, counted only up to `estimate_growth_max_seconds` after an
  enemy unit of its army was last in sight: an army unseen for longer is not
  assumed to grow without end.
- The killed value K is what `kills_cost` rose by since the last update.
- E = max(V, E + G - K), never below 0: the estimate is never below what is
  seen. It is unknown (None) until an enemy unit of its army has been seen.

One `enemy_estimate` event gives E, V, I, H_e and the seconds since an enemy
army unit was last in sight, at most every 30 seconds. With the switch off
there is no estimate and no event.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from ucla_agent.core import records
from ucla_agent.games.redalert.composition import AIRCRAFT_QUEUE, seen_army_value
from ucla_agent.games.redalert.family import HARVESTER, REFINERY
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.tuning import PlanRulesTuning
from ucla_agent.games.redalert.world import EnemyRecord, World

ENEMY_ESTIMATE_EVENT = "enemy_estimate"
# The estimate is written at most once in this many ticks: often enough to follow, seldom enough not to flood.
_EVENT_EVERY_TICKS = 30 * TICKS_PER_SECOND
# The decimal places the event gives its figures with.
_DECIMALS = 1


class EnemyArmyEstimate:
    """The estimate E of one match, brought up to date once a cycle. See the module's note."""

    def __init__(self) -> None:
        self._estimate: float | None = None
        # The tick of the last update and `kills_cost` then; None before the first update.
        self._updated_tick: int | None = None
        self._kills_cost = 0
        # The last tick an enemy unit of its army was in sight; None while none has been.
        self._army_seen_tick: int | None = None
        # The tick the last event was written at; None before the first.
        self._logged_tick: int | None = None

    def update(self, world: World, ctx: MatchContext) -> float | None:
        """Take in the cycle's world and return E; None with `enemy_army_estimate` off and before an enemy army unit
        has been seen."""
        tuning = ctx.tuning.plan_rules
        if not tuning.enemy_army_estimate:
            return None
        # The growth is counted from the sighting before this cycle's: time past the cap does not grow it back.
        growth_seconds = self._growth_seconds(world.tick, tuning)
        if any(record.visible and is_army_unit(record, world) for record in world.enemy_memory.values()):
            self._army_seen_tick = world.tick
        killed = world.military.kills_cost - self._kills_cost
        self._updated_tick, self._kills_cost = world.tick, world.military.kills_cost
        if self._army_seen_tick is None:
            return None
        seen = seen_value(world)
        income, harvesters = enemy_income(world, ctx.income.income_per_s, tuning)
        if self._estimate is None:
            self._estimate = float(seen)
        else:
            grown = self._estimate + income * tuning.estimate_army_spend_share * growth_seconds - killed
            self._estimate = max(float(seen), grown, 0.0)
        self._log(world, seen, income, harvesters)
        return self._estimate

    def _growth_seconds(self, tick: int, tuning: PlanRulesTuning) -> float:
        """The seconds since the last update that the estimate grows for: those up to `estimate_growth_max_seconds`
        after an enemy army unit was last in sight; none at the first update and while none has been seen."""
        if self._updated_tick is None or self._army_seen_tick is None:
            return 0.0
        growth_end = min(tick, self._army_seen_tick + tuning.estimate_growth_max_seconds * TICKS_PER_SECOND)
        return max(0, growth_end - self._updated_tick) / TICKS_PER_SECOND

    def _log(self, world: World, seen: int, income: float, harvesters: int) -> None:
        """Write the `enemy_estimate` event, unless one was written within `_EVENT_EVERY_TICKS`."""
        if self._logged_tick is not None and world.tick - self._logged_tick < _EVENT_EVERY_TICKS:
            return
        self._logged_tick = world.tick
        unseen = world.tick - self._army_seen_tick
        records.log(
            ENEMY_ESTIMATE_EVENT,
            {
                "tick": world.tick,
                "estimate": round(self._estimate, _DECIMALS),
                "seen_value": seen,
                "income_per_s": round(income, _DECIMALS),
                "enemy_harvesters": harvesters,
                "army_unseen_seconds": round(unseen / TICKS_PER_SECOND, _DECIMALS),
            },
        )


def is_army_unit(record: EnemyRecord, world: World) -> bool:
    """Whether a remembered enemy is a unit of its army: one that fights, and not an aircraft."""
    facts = world.items.get(record.type)
    return is_combat_unit(record) and facts is not None and facts.queue != AIRCRAFT_QUEUE


def seen_value(world: World) -> int:
    """V: what the remembered enemy units that fight on the ground and are not stale cost together."""
    value = seen_army_value(world)
    return sum(worth for queue, worth in value.items() if queue != AIRCRAFT_QUEUE)


def enemy_income(world: World, income_per_s: float | None, tuning: PlanRulesTuning) -> tuple[float, int]:
    """I, the enemy's income in credits a second, and H_e, the harvesters it is taken from: the more of the enemy
    harvesters and refineries remembered, our own harvesters while neither has been seen; each brings in what one of
    ours does (`income_per_s` over our harvesters), or `estimate_income_per_harvester` while that is not known."""
    remembered = world.enemy_memory.values()
    enemy_harvesters = sum(1 for record in remembered if record.type == HARVESTER and not record.is_building)
    enemy_refineries = sum(1 for record in remembered if record.type == REFINERY and record.is_building)
    own = sum(1 for unit in world.units.values() if unit.type == HARVESTER)
    harvesters = max(enemy_harvesters, enemy_refineries) or own
    is_rate_known = income_per_s is not None and own > 0
    per_harvester = income_per_s / own if is_rate_known else tuning.estimate_income_per_harvester
    return harvesters * per_harvester, harvesters
