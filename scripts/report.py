"""Compute the metrics of recorded matches and write a report.

    python scripts/report.py <path>...
    python scripts/report.py --compare <A> <B>
    python scripts/report.py --bench <label folder>
    python scripts/report.py --bench-compare <label folder A> <label folder B>
    python scripts/report.py --werewolf-bench <label folder>
    python scripts/report.py --werewolf-bench-compare <label folder A> <label folder B>
    python scripts/report.py --pokemon-bench <label folder>
    python scripts/report.py --pokemon-bench-compare <label folder A> <label folder B>

Run it from the repository root, after a run. Each path is a match folder
(`runs/<UTC yyyymmdd>/<session id>__<seat>`) or a dated folder
(`runs/<UTC yyyymmdd>`, meaning every match folder of that day). A match
folder is the record of one seat: for each one the tool writes `metrics.json`
into it, and it writes one `report.md` for all of them into
`runs/reports/<UTC timestamp>/`. The seats of a self-play game are separate
folders with the same session id; the report shows them on adjacent rows,
marked as the same game. A second table shows the production of each Red
Alert match: mean and final income a second, mean utilization of each queue
and the seconds of low power.

With `--compare`, A and B are two such paths, each standing for a set of
matches (two days of play, or the same day before and after a change). The
tool writes `compare.md` into `runs/reports/<UTC timestamp>/`: one row per
numeric metric with its mean over A, its mean over B and the difference. It
writes no `metrics.json` in this mode.

With `--bench`, the path is the folder of one label of the local benchmark
(docs/RUNNING.md, "Local benchmark"): a game folder per game, each with the
harness's `summary.json` and the agent's records in `records/`. The tool
prints the label's table, one row per game, the build profile of each
configuration, the totals and the goal line. With `--bench-compare` it prints
the totals of two labels, the differences and the p-value of each difference
in wins. Both print to standard output and write no file; unit costs come
from `config/redalert/items.toml` under the project root.

With `--werewolf-bench`, the path is the folder of one label of the
Werewolf practice benchmark (`scripts/werewolf_bench.py`, docs/RUNNING.md,
"Werewolf practice"), read from its `results.csv`: the tool prints the
label's arm and mix, and for each role and in all its games, wins, win rate
and unhandled errors, then the games in which the real seer claimed, how
many of those had it voted out on its claim day, and the village's wins
(`-` for a label written before those columns). With
`--werewolf-bench-compare` it prints both labels' lines, then for each role
the games and wins of A and B and the two-sided Fisher exact p-value, and
last `goal: met` when B's win rate is higher than A's in every role and B had
no unhandled error, else `goal: not met`. Both print to standard output and
write no file.

With `--pokemon-bench`, the path is the folder of one label of the Pokémon
practice benchmark (`scripts/pokemon_bench.py`, docs/RUNNING.md, "Pokémon
practice"), read from its `results.csv` and our seat's match records in it:
the tool prints the label's opponent, then its games, wins, win rate,
fallbacks, parse degradations and unhandled errors, then the slowest and the
median decision of each phase in milliseconds, then the decisions a model
answered, how many of them differed from the script's choice and that share.
With `--pokemon-bench-compare` it prints both labels' lines, then the games
and wins of A and B and the two-sided Fisher exact p-value of the two win
counts. Both print to standard output and write no file.

The tool only reads match records and writes its own files. It needs no
network and no key, and nothing the agent plays with is changed, so it can be
run at any time, on any machine that has the `runs/` folder.

Exit code 0: the file was written, or the benchmark printed. 2: a path holds
no match record, a label is not a benchmark label folder, or the command line
is wrong.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ucla_agent.core import bench, metrics, metrics_redalert
from ucla_agent.core.console import print_line
from ucla_agent.core.paths import repository_root
from ucla_agent.games.redalert.items import ItemTable
from ucla_agent.practice.pokemon import bench as pokemon_bench
from ucla_agent.practice.pokemon import bench_report as pokemon_bench_report
from ucla_agent.practice.werewolf import bench_report
from ucla_agent.practice.werewolf.bench import read_results

EXIT_WRITTEN = 0
EXIT_NO_RECORD = 2
REPORTS_FOLDER = Path("runs") / "reports"
REPORT_FILE_NAME = "report.md"
COMPARE_FILE_NAME = "compare.md"
OUTCOMES = ("win", "loss", "draw", "no_result")
NOT_AVAILABLE = "n/a"
_MEAN_DECIMALS = 4
_REPORT_FOLDER_FORMAT = "%Y%m%dT%H%M%SZ"
_GENERATED_FORMAT = "%Y-%m-%d %H:%M:%S UTC"
MATCH_HEADING = "Match"
# The summary table after its Match column: column heading -> the cell text for one match folder's metrics.
SUMMARY_COLUMNS = {
    "Seat": lambda computed: computed.get("seat"),
    "Game": lambda computed: computed.get("game_type"),
    "Map": lambda computed: computed.get("map_id"),
    "Faction": lambda computed: computed.get("faction"),
    "Enemy faction": lambda computed: computed.get("enemy_faction"),
    "Outcome": lambda computed: computed.get("outcome"),
    "Final trade balance": lambda computed: computed.get("final_trade_balance"),
    "Cycle latency p95 (ms)": lambda computed: (computed.get("cycle_latency_ms") or {}).get("p95"),
    "Fallbacks": lambda computed: computed.get("fallback_count"),
    "Rejected orders": lambda computed: _rejected_orders(computed.get("orders_rejected_by_reason")),
}
PRODUCTION_HEADING = "## Production"
# The production table after its Match column, for the Red Alert matches: heading -> the cell text for one match.
PRODUCTION_COLUMNS = {
    "Income/s mean": lambda computed: (computed.get("income_per_s") or {}).get("mean"),
    "Income/s final": lambda computed: (computed.get("income_per_s") or {}).get("final"),
    **{
        f"Utilization {queue}": lambda computed, queue=queue: (computed.get("utilization") or {}).get(queue)
        for queue in metrics_redalert.PRODUCTION_QUEUES
    },
    "Low power (s)": lambda computed: computed.get("low_power_seconds"),
}


class NoRecordError(Exception):
    """A path given on the command line holds no match record, or is not the folder of a benchmark label."""


def find_matches(paths: list[str]) -> list[Path]:
    """The match folders the given paths stand for, in the order given. Raises `NoRecordError` for a path with none."""
    folders: list[Path] = []
    for path in paths:
        found = metrics.match_folders(Path(path))
        if not found:
            raise NoRecordError(f"{path} holds no match record: it is not a match folder or a dated folder under runs/")
        folders += found
    return folders


def compute_and_write(folders: list[Path]) -> list[dict[str, Any]]:
    """Compute each match's metrics and write them as `metrics.json` into its folder; returns them in order."""
    computed = [metrics.compute_metrics(metrics.load_match(folder)) for folder in folders]
    for folder, match_metrics in zip(folders, computed):
        print_line(f"Wrote {metrics.write_metrics_file(folder, match_metrics)}")
    return computed


def seats_by_game(all_metrics: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """The match folders' metrics grouped into games, each game's seats in seat order.

    Folders that share a session id are the seats of one game (a self-play
    match). Games keep the order in which their first folder was given; a
    folder whose session id is unknown is a game of its own.
    """
    games: dict[Any, list[dict[str, Any]]] = {}
    for position, computed in enumerate(all_metrics):
        session_id = computed.get("session_id")
        games.setdefault(position if session_id is None else session_id, []).append(computed)
    return [sorted(seats, key=lambda computed: str(computed.get("seat"))) for seats in games.values()]


def render_report(all_metrics: list[dict[str, Any]], generated: datetime) -> str:
    """The text of `report.md`: a summary row per match folder, then production, then the totals by outcome.

    The production table has a row for each Red Alert match: the income the
    agent estimated, how busy each production queue was and how long the base
    was short of power.
    """
    games = seats_by_game(all_metrics)
    outcomes = Counter(computed.get("outcome") for computed in all_metrics)
    generated_at = generated.strftime(_GENERATED_FORMAT)
    lines = [
        "# Match report",
        "",
        f"Generated {generated_at} from {len(all_metrics)} match record(s) of {len(games)} game(s).",
        "",
        *_table([MATCH_HEADING, *SUMMARY_COLUMNS], _rows(games, SUMMARY_COLUMNS)),
        "",
        *_production_section(games),
        "## Totals by outcome",
        "",
        *_table(["Outcome", "Seats played"], [[outcome, outcomes[outcome]] for outcome in OUTCOMES]),
        "",
    ]
    return "\n".join(lines)


def _production_section(games: list[list[dict[str, Any]]]) -> list[str]:
    """The production table of the Red Alert matches, with its heading; nothing when no match is of Red Alert."""
    red_alert = [[computed for computed in seats if "income_per_s" in computed] for seats in games]
    rows = _rows([seats for seats in red_alert if seats], PRODUCTION_COLUMNS)
    if not rows:
        return []
    return [PRODUCTION_HEADING, "", *_table([MATCH_HEADING, *PRODUCTION_COLUMNS], rows), ""]


def _rows(games: list[list[dict[str, Any]]], columns: dict[str, Any]) -> list[list[Any]]:
    """One row per seat recorded, each marked when its game has several, with a cell per column."""
    rows = []
    for seats in games:
        for number, computed in enumerate(seats, start=1):
            match = computed.get("session_id")
            if len(seats) > 1:
                match = f"{match} (same game: seat {number} of {len(seats)})"
            rows.append([match, *(cell(computed) for cell in columns.values())])
    return rows


def new_report_folder(now: datetime) -> Path:
    """The folder one invocation writes its report into: `runs/reports/<UTC timestamp>/` under the repository root."""
    return repository_root() / REPORTS_FOLDER / now.strftime(_REPORT_FOLDER_FORMAT)


def _rejected_orders(by_reason: dict[str, int] | None) -> int | None:
    return None if by_reason is None else sum(by_reason.values())


def _table(headings: list[str], rows: list[list[Any]]) -> list[str]:
    """The lines of a markdown table; a missing value is shown as `n/a`."""
    shown = [[NOT_AVAILABLE if value is None else str(value) for value in row] for row in rows]
    separator = ["---"] * len(headings)
    return ["| " + " | ".join(cells) + " |" for cells in (headings, separator, *shown)]


# -- comparing two run sets ------------------------------------------------------------------------


def numeric_figures(computed: dict[str, Any], prefix: str = "") -> dict[str, float]:
    """The numbers in one match's metrics, nested ones under dotted names, plus one 0-or-1 figure per outcome.

    The outcome figures make the mean over a run set the share of matches that
    ended that way. Text, lists, nulls and the match's identity are left out.
    """
    figures: dict[str, float] = {}
    for name, value in computed.items():
        if isinstance(value, dict):
            figures.update(numeric_figures(value, f"{prefix}{name}."))
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and name not in metrics.IDENTITY_KEYS:
            figures[f"{prefix}{name}"] = value
    if not prefix:
        figures.update({f"outcome.{outcome}": float(computed.get("outcome") == outcome) for outcome in OUTCOMES})
    return figures


def mean_figures(run_set: list[dict[str, Any]]) -> dict[str, float]:
    """For each numeric figure, its mean over the matches of a run set that report it."""
    reported: dict[str, list[float]] = {}
    for computed in run_set:
        for name, value in numeric_figures(computed).items():
            reported.setdefault(name, []).append(value)
    return {name: sum(values) / len(values) for name, values in reported.items()}


def render_comparison(run_sets: dict[str, list[dict[str, Any]]], generated: datetime) -> str:
    """The text of `compare.md` for two run sets, given as path -> metrics of its matches, A first then B."""
    (path_a, set_a), (path_b, set_b) = run_sets.items()
    means_a, means_b = mean_figures(set_a), mean_figures(set_b)
    rows = [
        [
            name,
            _rounded(means_a.get(name)),
            _rounded(means_b.get(name)),
            _difference(means_a.get(name), means_b.get(name)),
        ]
        for name in sorted(means_a.keys() | means_b.keys())
    ]
    lines = [
        "# Run comparison",
        "",
        f"Generated {generated.strftime(_GENERATED_FORMAT)}.",
        "",
        f"- A: `{path_a}`, {len(set_a)} match record(s)",
        f"- B: `{path_b}`, {len(set_b)} match record(s)",
        "",
        *_table(["Metric", "Mean A", "Mean B", "Difference (B - A)"], rows),
        "",
    ]
    return "\n".join(lines)


def _rounded(value: float | None) -> str | None:
    """A mean as text, to four decimals without trailing zeros; None stays None (shown as `n/a`)."""
    if value is None:
        return None
    # Adding 0.0 turns a rounded negative zero into zero, so no row shows "-0".
    return f"{round(value, _MEAN_DECIMALS) + 0.0:.{_MEAN_DECIMALS}f}".rstrip("0").rstrip(".")


def _difference(mean_a: float | None, mean_b: float | None) -> str | None:
    return None if mean_a is None or mean_b is None else _rounded(mean_b - mean_a)


# -- the command line ------------------------------------------------------------------------------


def _write_report(paths: list[str], now: datetime) -> Path:
    report_path = new_report_folder(now) / REPORT_FILE_NAME
    all_metrics = compute_and_write(find_matches(paths))
    metrics.write_output(report_path, render_report(all_metrics, now))
    return report_path


def _write_comparison(paths: list[str], now: datetime) -> Path:
    run_sets = {
        path: [metrics.compute_metrics(metrics.load_match(folder)) for folder in find_matches([path])] for path in paths
    }
    comparison_path = new_report_folder(now) / COMPARE_FILE_NAME
    metrics.write_output(comparison_path, render_comparison(run_sets, now))
    return comparison_path


def _bench_lines(args: argparse.Namespace) -> list[str]:
    """The lines of the benchmark table of one label, or of the comparison of two."""
    labels = [args.bench] if args.bench else args.bench_compare
    unit_cost = _unit_cost(ItemTable.from_file(repository_root()))
    games = [_read_label(label, unit_cost) for label in labels]
    return bench.bench_lines(*games) if args.bench else bench.compare_lines(labels[0], games[0], labels[1], games[1])


def _read_label(label: str, unit_cost: bench.UnitCost) -> list[bench.Game]:
    """The games of a label folder; raises `NoRecordError` when the path is not one."""
    folder = Path(label)
    if not folder.is_dir():
        raise NoRecordError(f"{label} is not a folder: give the folder of a benchmark label")
    try:
        return bench.read_label(folder, unit_cost)
    except ValueError as exc:
        raise NoRecordError(f"{label} is not a benchmark label folder: {exc}") from exc


def _werewolf_bench_lines(args: argparse.Namespace) -> list[str]:
    """The lines of the Werewolf practice benchmark table of one label, or of the comparison of two."""
    if args.werewolf_bench:
        return bench_report.label_lines(_werewolf_rows(args.werewolf_bench))
    label_a, label_b = args.werewolf_bench_compare
    return bench_report.compare_lines(label_a, _werewolf_rows(label_a), label_b, _werewolf_rows(label_b))


def _werewolf_rows(label: str) -> list[dict[str, str]]:
    """The rows of a Werewolf practice label folder's `results.csv`; raises `NoRecordError` when there is none."""
    try:
        return read_results(Path(label))
    except ValueError as exc:
        raise NoRecordError(f"{label} is not a Werewolf practice benchmark label folder: {exc}") from exc


def _pokemon_bench_lines(args: argparse.Namespace) -> list[str]:
    """The lines of the Pokémon practice benchmark figures of one label, or of the comparison of two."""
    if args.pokemon_bench:
        label = args.pokemon_bench
        return pokemon_bench_report.label_lines(_pokemon_rows(label), pokemon_bench.match_records(Path(label)))
    label_a, label_b = args.pokemon_bench_compare
    return pokemon_bench_report.compare_lines(
        label_a,
        _pokemon_rows(label_a),
        label_b,
        _pokemon_rows(label_b),
        records_a=pokemon_bench.match_records(Path(label_a)),
        records_b=pokemon_bench.match_records(Path(label_b)),
    )


def _pokemon_rows(label: str) -> list[dict[str, str]]:
    """The rows of a Pokémon practice label folder's `results.csv`; raises `NoRecordError` when there is none."""
    try:
        return pokemon_bench.read_results(Path(label))
    except ValueError as exc:
        raise NoRecordError(f"{label} is not a Pokémon practice benchmark label folder: {exc}") from exc


def _unit_cost(table: ItemTable) -> bench.UnitCost:
    """The cost of a unit type as the item table gives it, or None for a type the table does not know."""

    def cost(name: str) -> int | None:
        item = table.get(name)
        return None if item is None else item.cost

    return cost


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python scripts/report.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*", metavar="path", help="a match folder, or a dated folder under runs/")
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("A", "B"),
        help="write compare.md with the mean of every metric over run set A, over run set B, and the difference",
    )
    parser.add_argument(
        "--bench",
        metavar="LABEL",
        help="print the benchmark table of a label folder: a row per game, the build profile, totals and the goal line",
    )
    parser.add_argument(
        "--bench-compare",
        nargs=2,
        metavar=("A", "B"),
        help="print the benchmark totals of label folders A and B, the differences and the p-value of each in wins",
    )
    parser.add_argument(
        "--werewolf-bench",
        metavar="LABEL",
        help="print the games, wins and win rate of each role of a Werewolf practice benchmark label folder",
    )
    parser.add_argument(
        "--werewolf-bench-compare",
        nargs=2,
        metavar=("A", "B"),
        help="print the wins of each role of Werewolf practice label folders A and B, the Fisher p and the goal line",
    )
    parser.add_argument(
        "--pokemon-bench",
        metavar="LABEL",
        help="print the games, wins, win rate, fallbacks, unhandled errors, slowest decisions and the share of the "
        "model's choices that differed from the script's of a Pokémon practice benchmark label folder",
    )
    parser.add_argument(
        "--pokemon-bench-compare",
        nargs=2,
        metavar=("A", "B"),
        help="print the figures of Pokémon practice label folders A and B and the Fisher p of their wins",
    )
    return parser


def _check_mode(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Stop with a usage error unless exactly one mode is given, and `--compare` names two different run sets."""
    modes = (
        args.paths,
        args.compare,
        args.bench,
        args.bench_compare,
        args.werewolf_bench,
        args.werewolf_bench_compare,
        args.pokemon_bench,
        args.pokemon_bench_compare,
    )
    if len(list(filter(None, modes))) != 1:
        parser.error(
            "give the paths to report on, --compare A B, --bench LABEL, --bench-compare A B, "
            "--werewolf-bench LABEL, --werewolf-bench-compare A B, --pokemon-bench LABEL "
            "or --pokemon-bench-compare A B: one of them"
        )
    if args.compare and args.compare[0] == args.compare[1]:
        parser.error("--compare needs two different run sets")


def _run(args: argparse.Namespace) -> list[str]:
    """Do what the command line asks; returns the lines to print once it is done."""
    if args.bench or args.bench_compare:
        return _bench_lines(args)
    if args.werewolf_bench or args.werewolf_bench_compare:
        return _werewolf_bench_lines(args)
    if args.pokemon_bench or args.pokemon_bench_compare:
        return _pokemon_bench_lines(args)
    now = datetime.now(timezone.utc)
    written = _write_comparison(args.compare, now) if args.compare else _write_report(args.paths, now)
    return [f"Wrote {written}"]


def main(arguments: list[str]) -> int:
    """Write the report or the comparison of two run sets, or print a benchmark table or comparison; the exit code."""
    parser = _parser()
    args = parser.parse_args(arguments)
    _check_mode(parser, args)
    try:
        lines = _run(args)
    except NoRecordError as exc:
        print_line(str(exc), error=True)
        return EXIT_NO_RECORD
    for line in lines:
        print_line(line)
    return EXIT_WRITTEN


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
