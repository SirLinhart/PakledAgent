"""Red Alert metrics, computed from the fields of a match record's events.

Nothing here imports the Red Alert agent: every figure is read from what the
events hold, so the report tool measures a record whatever code played it.

Game time is counted in ticks, 25 to the second, and the tick of a state is
its `state_version`. A match played in several attempts is measured as one:
its states are put in tick order, and its ledger counters are added up, since
each attempt starts with a fresh ledger.

Money is what the game can spend: the `cash` and the `ore` of a state's
economy together. Harvested income arrives as ore, which the refineries hold
up to `resource_capacity`; the game spends both.

A figure that the record cannot answer is null, not zero: seconds of low
power need at least one state, and anything read from buildings or production
queues needs at least one state that was recorded in full.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from ucla_agent.core.stats import numbers, percentiles

if TYPE_CHECKING:
    from ucla_agent.core.metrics import Match

TICKS_PER_SECOND = 25
PLAYING_PHASE = "playing"
TRADE_BALANCE_SAMPLE_SECONDS = 30
# A queue counts as idle only while this much money (cash and ore) is at hand:
# with less, the agent may rightly be saving for the next item and its reserve.
IDLE_CASH_THRESHOLD = 1500
PRODUCTION_QUEUES = ("Building", "Defense", "Infantry", "Vehicle")
# Queues whose finished items wait until they are placed.
PLACEABLE_QUEUES = frozenset({"Building", "Defense"})
DEFENSE_BUILDING_TYPES = frozenset({"pbox", "hbox", "gun", "ftur", "tsla", "agun", "sam"})
WAR_FACTORY_TYPES = frozenset({"weap"})
SUMMED_LEDGER_COUNTERS = ("orders_sent", "orders_lost", "dropped_invalid", "duplicate_production_orders")
REJECTED_BY_REASON = "orders_rejected_by_reason"
_SECONDS_DECIMALS = 3
_RATIO_DECIMALS = 4


@dataclass(frozen=True)
class _Sample:
    """One recorded state of the running game.

    `state` is the whole state object when the record holds it, and None for a
    state that was recorded thinned (economy, military and counts only).
    """

    tick: int
    economy: dict[str, Any]
    military: dict[str, Any]
    state: dict[str, Any] | None

    @property
    def observation(self) -> dict[str, Any]:
        """The observation of a state recorded in full; empty for a thinned one."""
        return _as_dict(self.state.get("observation")) if self.state else {}

    @property
    def production(self) -> list[dict[str, Any]]:
        """The items in the production queues of a state recorded in full."""
        return _dicts_in(self.observation.get("production"))


def compute(match: Match) -> dict[str, Any]:
    """The Red Alert metrics of one match."""
    samples = _samples(match)
    full = [sample for sample in samples if sample.state is not None]
    return {
        **_batch_metrics(match, samples),
        **_ledger_metrics(match),
        **_economy_metrics(samples, full),
        **_production_metrics(match.data_of("timing")),
        **_trade_metrics(samples),
        "time_first_defense_s": _first_seen_s(full, DEFENSE_BUILDING_TYPES),
        "time_war_factory_s": _first_seen_s(full, WAR_FACTORY_TYPES),
        **_plan_metrics(match, samples),
        **_match_facts(full),
    }


# -- the states of the running game ----------------------------------------------------------------


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dicts_in(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _samples(match: Match) -> list[_Sample]:
    """Every state recorded while the game was playing, over all attempts, in tick order."""
    samples = [sample for data in match.data_of("state") if (sample := _sample(data)) is not None]
    return sorted(samples, key=lambda sample: sample.tick)


def _sample(data: dict[str, Any]) -> _Sample | None:
    tick = data.get("state_version")
    if data.get("phase") != PLAYING_PHASE or not numbers([tick]):
        return None
    state = data.get("state") if isinstance(data.get("state"), dict) else None
    figures = _as_dict(state.get("observation")) if state else data
    return _Sample(tick, _as_dict(figures.get("economy")), _as_dict(figures.get("military")), state)


def _seconds_while(samples: list[_Sample], holds: Callable[[_Sample], bool]) -> float | None:
    """Game seconds during which a condition held, each state standing until the next; None without any state."""
    if not samples:
        return None
    ticks = sum(later.tick - earlier.tick for earlier, later in zip(samples, samples[1:]) if holds(earlier))
    return round(ticks / TICKS_PER_SECOND, _SECONDS_DECIMALS)


def _game_seconds(sample: _Sample) -> float:
    return round(sample.tick / TICKS_PER_SECOND, _SECONDS_DECIMALS)


# -- batches and latency ---------------------------------------------------------------------------


def _batch_metrics(match: Match, samples: list[_Sample]) -> dict[str, Any]:
    results = match.data_of("action_result")
    accepted = sum(1 for result in results if result.get("accepted") is True)
    playing_seconds = _game_seconds(samples[-1]) - _game_seconds(samples[0]) if samples else None
    return {
        "batches_sent": sum(1 for action in match.data_of("action") if _carries_orders(action.get("action"))),
        "batches_accepted": accepted,
        "playing_seconds": playing_seconds,
        "batches_per_second": round(accepted / playing_seconds, _RATIO_DECIMALS) if playing_seconds else None,
        "round_trip_ms": percentiles(result.get("round_trip_ms") for result in results),
        "cycle_latency_ms": percentiles(_cycle_latencies(match)),
        "ticks_per_call": _ticks_per_call(match.data_of("timing")),
    }


def _carries_orders(action: Any) -> bool:
    return isinstance(action, dict) and bool(action.get("orders"))


def _cycle_latencies(match: Match) -> list[float | None]:
    """For each answered batch: the compute time of the action plus the round trip of its result."""
    latencies: list[float | None] = []
    for attempt in match.attempts:
        compute_ms = None
        for event in attempt.events:
            if event["kind"] == "action":
                compute_ms = event["data"].get("compute_ms")
            elif event["kind"] == "action_result":
                parts = numbers([compute_ms, event["data"].get("round_trip_ms")])
                latencies.append(sum(parts) if len(parts) == 2 else None)
                compute_ms = None
    return latencies


def _ticks_per_call(timings: list[dict[str, Any]]) -> dict[str, float | None]:
    """The mean, weighted by each timing window's length in ticks, and the maximum of ticks per call."""
    windows = [window for timing in timings if (window := _timing_window(timing)) is not None]
    total_ticks = sum(ticks for ticks, _, _ in windows)
    if not total_ticks:
        return {"mean": None, "max": None}
    mean = sum(ticks * window_mean for ticks, window_mean, _ in windows) / total_ticks
    return {"mean": round(mean, _SECONDS_DECIMALS), "max": max(window_max for _, _, window_max in windows)}


def _timing_window(timing: dict[str, Any]) -> tuple[float, float, float] | None:
    """A timing event as (ticks covered, mean ticks per call, maximum ticks per call); None when it lacks a figure."""
    per_call = _as_dict(timing.get("ticks_per_call"))
    figures = numbers([timing.get("from_tick"), timing.get("to_tick"), per_call.get("mean"), per_call.get("max")])
    if len(figures) != 4:
        return None
    from_tick, to_tick, mean, maximum = figures
    return max(to_tick - from_tick, 0), mean, maximum


# -- the order ledger ------------------------------------------------------------------------------


def _ledger_metrics(match: Match) -> dict[str, Any]:
    """The ledger counters, added up over the last `ledger_summary` of every attempt; all null without any."""
    last_summaries = [summaries[-1] for attempt in match.attempts if (summaries := attempt.data_of("ledger_summary"))]
    if not last_summaries:
        return {counter: None for counter in (*SUMMED_LEDGER_COUNTERS, REJECTED_BY_REASON)}
    rejected: Counter[str] = Counter()
    for summary in last_summaries:
        by_reason = _as_dict(summary.get(REJECTED_BY_REASON))
        rejected.update({reason: count for reason, count in by_reason.items() if numbers([count])})
    totals = {
        counter: int(sum(numbers(summary.get(counter) for summary in last_summaries)))
        for counter in SUMMED_LEDGER_COUNTERS
    }
    return {**totals, REJECTED_BY_REASON: dict(rejected)}


# -- economy ---------------------------------------------------------------------------------------


def _economy_metrics(samples: list[_Sample], full: list[_Sample]) -> dict[str, Any]:
    idle_seconds = {
        queue: _seconds_while(full, lambda sample, queue=queue: _is_idle_with_cash(sample, queue))
        for queue in PRODUCTION_QUEUES
    }
    return {
        "low_power_seconds": _seconds_while(samples, _is_low_on_power),
        "zero_harvester_seconds": _seconds_while(samples, lambda sample: sample.economy.get("harvesters") == 0),
        "harvesters_lost": _harvesters_lost(samples),
        "ready_unplaced_seconds": _seconds_while(full, _has_ready_unplaced_item),
        "idle_production_seconds": idle_seconds if full else None,
        "ore_capped_seconds": _seconds_while(samples, _is_ore_capped),
        "zero_spendable_seconds": _seconds_while(samples, lambda sample: _spendable(sample) == 0),
    }


def _is_low_on_power(sample: _Sample) -> bool:
    power = numbers([sample.economy.get("power_drained"), sample.economy.get("power_provided")])
    return len(power) == 2 and power[0] > power[1]


def _has_ready_unplaced_item(sample: _Sample) -> bool:
    return any(item.get("ready") is True and item.get("queue") in PLACEABLE_QUEUES for item in sample.production)


def _spendable(sample: _Sample) -> float | None:
    """The money of a state: its cash and its ore together, an economy without ore having none; None without cash."""
    cash = numbers([sample.economy.get("cash")])
    ore = numbers([sample.economy.get("ore", 0)])
    return cash[0] + ore[0] if cash and ore else None


def _is_ore_capped(sample: _Sample) -> bool:
    """Whether the refineries are full: ore at the capacity, which is above zero, so that more income is lost."""
    held = numbers([sample.economy.get("ore"), sample.economy.get("resource_capacity")])
    return len(held) == 2 and held[1] > 0 and held[0] >= held[1]


def _is_idle_with_cash(sample: _Sample, queue: str) -> bool:
    money = _spendable(sample)
    busy_queues = {item.get("queue") for item in sample.production}
    return money is not None and money >= IDLE_CASH_THRESHOLD and queue not in busy_queues


def _harvesters_lost(samples: list[_Sample]) -> int | None:
    """Every drop in the harvester count from one state to the next, added up; None when no state gives a count."""
    counts = numbers(sample.economy.get("harvesters") for sample in samples)
    if not counts:
        return None
    return int(sum(max(earlier - later, 0) for earlier, later in zip(counts, counts[1:])))


# -- production, as the timing events give it ------------------------------------------------------


def _production_metrics(timings: list[dict[str, Any]]) -> dict[str, Any]:
    """The income the agent estimated, its mean and its last value, and the mean utilization of each queue.

    Each figure is over the timing events that give it; null when none does.
    """
    incomes = numbers(timing.get("income_per_s") for timing in timings)
    utilization = {
        queue: _mean(numbers(_as_dict(timing.get("utilization")).get(queue) for timing in timings))
        for queue in PRODUCTION_QUEUES
    }
    return {
        "income_per_s": {"mean": _mean(incomes), "final": incomes[-1] if incomes else None},
        "utilization": utilization,
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), _RATIO_DECIMALS) if values else None


# -- trades ----------------------------------------------------------------------------------------


def _trade_metrics(samples: list[_Sample]) -> dict[str, Any]:
    balances = [(sample.tick, balance) for sample in samples if (balance := _trade_balance(sample)) is not None]
    assets = numbers(sample.military.get("assets_value") for sample in samples)
    return {
        "trade_balance_series": _sampled_series(balances),
        "final_trade_balance": balances[-1][1] if balances else None,
        "final_assets_value": assets[-1] if assets else None,
    }


def _trade_balance(sample: _Sample) -> float | None:
    costs = numbers([sample.military.get("kills_cost"), sample.military.get("deaths_cost")])
    return costs[0] - costs[1] if len(costs) == 2 else None


def _sampled_series(balances: list[tuple[int, float]]) -> list[dict[str, float]]:
    """The trade balance every 30 game seconds: at each sample time, the balance of the latest state up to then."""
    if not balances:
        return []
    last_second = balances[-1][0] // TICKS_PER_SECOND
    series = []
    for second in range(0, last_second + 1, TRADE_BALANCE_SAMPLE_SECONDS):
        known = [balance for tick, balance in balances if tick <= second * TICKS_PER_SECOND]
        if known:
            series.append({"game_seconds": second, "trade_balance": known[-1]})
    return series


# -- buildings, plans and the match's own facts ----------------------------------------------------


def _first_seen_s(full: list[_Sample], building_types: frozenset[str]) -> float | None:
    """The game second of the first fully recorded state that lists a building of one of the types; None if none does."""
    for sample in full:
        own_types = {str(building.get("type")).lower() for building in _dicts_in(sample.observation.get("buildings"))}
        if own_types & building_types:
            return _game_seconds(sample)
    return None


def _plan_metrics(match: Match, samples: list[_Sample]) -> dict[str, Any]:
    sequence = [
        {"plan": change["new"], "game_seconds": change["game_seconds"]}
        for change in match.data_of("plan_change")
        if isinstance(change.get("new"), str) and numbers([change.get("game_seconds")])
    ]
    played_until = _game_seconds(samples[-1]) if samples else 0.0
    return {"plan_sequence": sequence, "plan_time_share": _time_shares(sequence, played_until)}


def _time_shares(sequence: list[dict[str, Any]], played_until: float) -> dict[str, float]:
    """The fraction of the time from the first plan to the last recorded state that each plan was in force."""
    starts = [step["game_seconds"] for step in sequence]
    ends = [*starts[1:], played_until]
    seconds: Counter[str] = Counter()
    for step, start, end in zip(sequence, starts, ends):
        seconds[step["plan"]] += max(end - start, 0)
    total = sum(seconds.values())
    return {plan: round(spent / total, _RATIO_DECIMALS) for plan, spent in seconds.items()} if total else {}


def _match_facts(full: list[_Sample]) -> dict[str, Any]:
    """The factions and the map, from the first state recorded in full; null when there is none."""
    if not full:
        return {"faction": None, "enemy_faction": None, "map_id": None}
    first = full[0]
    map_id = _as_dict(first.state.get("map")).get("id") or _as_dict(first.observation.get("map")).get("id")
    return {
        "faction": first.observation.get("faction"),
        "enemy_faction": first.observation.get("enemy_faction"),
        "map_id": map_id,
    }
