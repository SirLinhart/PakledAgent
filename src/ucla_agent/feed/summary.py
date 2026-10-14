"""The summary of a watched match, from its record alone: what the watch command prints when the game is over.

It reads the newest match under a records folder with the feed's own reader,
so its figures are the ones the feed page showed: the result, the trade
balance, the length of the game, the model's choices and how long they took,
the fallbacks, the share of the map explored and the order throughput over
the whole match.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from ucla_agent.core.stats import numbers, percentile
from ucla_agent.feed.header import header_fields
from ucla_agent.feed.sources import LatestMatch
from ucla_agent.feed.throughput import Throughput

MEDIAN = 50
DASH = "-"
SECONDS_PER_MINUTE = 60
OUTCOME_WORDS = {"win": "won", "loss": "lost", "draw": "draw", "no_result": "no result"}


def summary_lines(records_folder: Path) -> list[str]:
    """The summary of the newest match recorded under a records folder, as lines; one line when there is none."""
    source = LatestMatch(records_folder)
    throughput = Throughput()
    for event in source.refresh():
        throughput.take_in(event)
    reader = source.reader
    if reader is None:
        return [f"No match record under {records_folder}: the game did not get as far as its first move."]
    header = header_fields(reader.game_type, reader.latest_state, reader.closing, reader.meta.get("agent_id"))
    events = reader.events_after(0)
    return [
        f"Match {reader.name}",
        f"  result:         {_result(header.get('result'))}",
        f"  trade balance:  {_signed(header.get('trade_balance'))}",
        f"  game length:    {_clock(header.get('game_seconds'))} of game time",
        f"  model choices:  {_choices([event.data for event in events if event.kind == 'decision'])}",
        f"  fallbacks:      {sum(1 for event in events if event.kind == 'fallback')}",
        f"  map explored:   {_number(header.get('explored_percent'), 1)}%",
        f"  throughput:     {_throughput(throughput.figures()['match'])}",
    ]


def _result(result: dict[str, Any] | None) -> str:
    if result is None:
        return "no result in the record (the game was stopped before it ended)"
    outcome = OUTCOME_WORDS.get(result["outcome"], result["outcome"])
    return f"{outcome} ({result['reason'] or 'no reason given'})"


def _choices(decisions: list[dict[str, Any]]) -> str:
    """How many choices were asked of a model, how long they took and what came of them."""
    if not decisions:
        return "none"
    latencies = [decision.get("latency_ms") for decision in decisions]
    known = numbers(latencies)
    outcomes = Counter(str(decision.get("outcome")) for decision in decisions)
    timing = f"latency median {_number(percentile(known, MEDIAN))} ms, max {_number(max(known, default=None))} ms"
    return f"{len(decisions)}, {timing}; " + ", ".join(f"{outcome} {count}" for outcome, count in outcomes.items())


def _throughput(match: dict[str, Any]) -> str:
    return (
        f"{_number(match['orders_per_s'], 2)} orders/s, {_number(match['batches_per_s'], 2)} batches/s, "
        f"{_number(match['commands_per_s'], 1)} commands/s; "
        f"{_number(match['mean_orders_per_batch'], 2)} / {match['pool_orders']} orders per batch, "
        f"{_number(match['at_limit_percent'], 1)}% of batches at {match['pool_orders']}, "
        f"{_number(match['mean_commands_per_batch'], 1)} / {match['command_limit']} commands per batch; "
        f"{_number(match['rejected_per_s'], 3)} rejected/s"
    )


def _number(value: Any, digits: int = 0) -> str:
    return f"{value:,.{digits}f}" if numbers([value]) else DASH


def _signed(value: Any) -> str:
    return f"{value:+,.0f}" if numbers([value]) else DASH


def _clock(seconds: Any) -> str:
    if not numbers([seconds]):
        return DASH
    minutes, rest = divmod(int(seconds), SECONDS_PER_MINUTE)
    return f"{minutes}:{rest:02d}"
