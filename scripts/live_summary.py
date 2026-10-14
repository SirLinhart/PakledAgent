"""Print what one real match record says about a live match: the facts the live validation checklist asks for.

    python scripts/live_summary.py <match folder>

Run it from the repository root, after a test match, on one seat's match
folder (`runs/<UTC yyyymmdd>/<session id>__<seat>`). It prints the map, both
factions, the seat, the result and why the match ended, the final `military`
values, how often batches went out, the p50 and p95 of the compute time, the
round trip and the cycle latency, how many game ticks passed between two
decisions, the batches refused whole, the duplicate production orders, the
rejected and lost orders by reason, the plans in the order they were taken,
and every `error` event. Every figure that `scripts/report.py` also computes
is computed by the same code (`ucla_agent.core.metrics`), so the two tools
always agree.

Lost orders are counted twice over: the order ledger's total, and by reason
where the record shows one: a `lost` verdict in a state's `last_orders` or
`recent_order_problems`, or a batch the platform never received, counted under
its error code as the ledger counts it.

The tool only reads the record and writes nothing; it needs no network and no
key. Exit code 0: the summary was printed. 2: the folder holds no match
record, or the command line is wrong.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from ucla_agent.core import metrics
from ucla_agent.core.console import print_line
from ucla_agent.core.stats import numbers

EXIT_SHOWN = 0
EXIT_NO_RECORD = 2
PLAYING_PHASE = "playing"
LOST = "lost"
# The error of a batch refused for what it held; any other error means the batch never arrived and its orders are lost.
INVALID_ACTION = "INVALID_ACTION"
LATENCY_FIGURES = ("compute_ms", "round_trip_ms", "cycle_latency_ms")
NOT_RECORDED = "n/a"
NONE_SEEN = "none"


def _shown(value: Any) -> str:
    return NOT_RECORDED if value is None else str(value)


def _counts(by_reason: dict[str, int] | None) -> str:
    """Counts by reason as `reason count, ...`, the most frequent first; `none` when there are none."""
    if by_reason is None:
        return NOT_RECORDED
    ordered = sorted(by_reason.items(), key=lambda pair: (-pair[1], pair[0]))
    return ", ".join(f"{reason} {count}" for reason, count in ordered) or NONE_SEEN


def final_military(match: metrics.Match) -> dict[str, Any] | None:
    """The `military` values of the last state recorded while the game was playing; None when no state holds them."""
    playing = [data for data in match.data_of("state") if data.get("phase") == PLAYING_PHASE]
    for data in sorted(playing, key=lambda data: numbers([data.get("state_version")]) or [0], reverse=True):
        state = data.get("state")
        figures = state.get("observation", {}) if isinstance(state, dict) else data
        if isinstance(figures.get("military"), dict):
            return figures["military"]
    return None


def refused_batches(match: metrics.Match) -> dict[str, int]:
    """The batches the platform answered with an error instead of taking them, by the error it named."""
    results = (data.get("result") for data in match.data_of("action_result"))
    return dict(Counter(str(result["error"]) for result in results if isinstance(result, dict) and "error" in result))


def lost_by_reason(match: metrics.Match) -> dict[str, int]:
    """The lost orders the record shows by reason: lost verdicts in states, and the orders of batches never received."""
    return dict(_lost_verdicts(match) + _batches_never_received(match))


def _lost_verdicts(match: metrics.Match) -> Counter[str]:
    """The `lost` verdicts of the states, by reason; a verdict that several states repeat is counted once."""
    lost_rows: dict[tuple[Any, Any], str] = {}
    full_states = (data["state"] for data in match.data_of("state") if isinstance(data.get("state"), dict))
    for state in full_states:
        lost_rows.update(
            {
                (batch, row.get("index")): str(row.get("reason"))
                for batch, row in _verdict_rows(state)
                if row.get("status") == LOST
            }
        )
    return Counter(lost_rows.values())


def _batches_never_received(match: metrics.Match) -> Counter[str]:
    """The orders of every batch the platform answered with an error other than INVALID_ACTION, by that error."""
    counts: Counter[str] = Counter()
    for attempt in match.attempts:
        orders_in_last_batch = 0
        for event in attempt.events:
            if event["kind"] == "action":
                action = event["data"].get("action")
                orders_in_last_batch = len(action.get("orders") or ()) if isinstance(action, dict) else 0
            elif event["kind"] == "action_result":
                error = (event["data"].get("result") or {}).get("error")
                if error is not None and error != INVALID_ACTION:
                    counts[str(error).lower()] += orders_in_last_batch
    return counts


def _verdict_rows(state: dict[str, Any]) -> list[tuple[Any, dict[str, Any]]]:
    """The final verdicts a state carries, each with the platform's number of its batch."""
    rows = [(row.get("batch"), row) for row in state.get("recent_order_problems") or () if isinstance(row, dict)]
    last_orders = state.get("last_orders")
    if isinstance(last_orders, dict) and not last_orders.get("pending"):
        batch = last_orders.get("batch")
        rows += [(batch, row) for row in last_orders.get("results") or () if isinstance(row, dict)]
    return rows


def summary_lines(match: metrics.Match) -> list[str]:
    """The lines of the summary of one match."""
    computed = metrics.compute_metrics(match)
    military = final_military(match)
    plans = ", ".join(f"{step['plan']} at {step['game_seconds']} s" for step in computed["plan_sequence"])
    errors = match.data_of("error")
    ticks = computed["ticks_per_call"]
    return [
        f"Match folder: {match.folder}",
        f"Map: {_shown(computed['map_id'])}",
        f"Factions: {_shown(computed['faction'])} (own), {_shown(computed['enemy_faction'])} (enemy)",
        f"Seat: {_shown(computed['seat'])}",
        f"Result: {computed['outcome']} ({computed['termination_reason']})",
        "Final military: "
        + (", ".join(f"{name} {value}" for name, value in military.items()) if military else NOT_RECORDED),
        f"batches_per_second: {_shown(computed['batches_per_second'])}",
        *(
            f"{figure}: p50 {_shown(computed[figure]['p50'])}, p95 {_shown(computed[figure]['p95'])}"
            for figure in LATENCY_FIGURES
        ),
        f"ticks_per_call: mean {_shown(ticks['mean'])}, max {_shown(ticks['max'])}",
        f"Batches refused whole, by error: {_counts(refused_batches(match))}",
        f"duplicate_production_orders: {_shown(computed['duplicate_production_orders'])}",
        f"Rejected orders by reason: {_counts(computed['orders_rejected_by_reason'])}",
        f"Lost orders: {_shown(computed['orders_lost'])} (by reason: {_counts(lost_by_reason(match))})",
        f"Plan sequence: {plans or NONE_SEEN}",
        f"Errors: {len(errors)}",
        *(
            f"  {error.get('where')} {error.get('type')} {error.get('exception')}: {error.get('message')}"
            for error in errors
        ),
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python scripts/live_summary.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", help="one seat's match folder, runs/<UTC yyyymmdd>/<session id>__<seat>")
    return parser


def main(arguments: list[str]) -> int:
    """Print the summary of the match folder given; returns the exit code."""
    folder = Path(_parser().parse_args(arguments).folder)
    if metrics.match_folders(folder) != [folder]:
        print_line(f"{folder} holds no match record: it is not one seat's match folder under runs/", error=True)
        return EXIT_NO_RECORD
    for line in summary_lines(metrics.load_match(folder)):
        print_line(line)
    return EXIT_SHOWN


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
