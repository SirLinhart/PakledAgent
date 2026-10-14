"""The Werewolf practice benchmark: our seat's games for each role, one row per game, and the label's results files.

A run plays `N` games for each of the roles villager, seer and wolf. Game `i`
of a role has seed `seed_base + i`, forces that role on our seat and seats us
at `seed % 7`; the deal and the opponents' seating follow from the seed, role
and seat alone, so every arm of one seed base plays identical games.

Everything a run writes goes into its label folder,
`<runs>/practice/werewolf/<label>/`, where `<runs>` is `runs/` or the folder
`UCLA_AGENT_RUNS_DIR` names: the match records of our seat's games (the
variable names the label folder while the games are played), `results.csv`
with one row per game and `summary.json` with the arm, the mix, the settings,
the tuning file's path and SHA-256, the model server roles started for the
run and the games and wins of each role. Both files are written again after
each game, so a run that is stopped keeps the games it played.

A game's `fallbacks`, `model_calls`, `decision_p95_ms` and recorded
`unhandled_errors` are read from its match record, and its
`seer_claim_day`, `seer_voted_out_on_claim_day` and `village_won` from the
finished engine (`bench_truth`), which no seat sees. A game that raises is a
row with no winner, no claim day and one more unhandled error, and the run
goes on. A `results.csv` written before the engine-truth columns existed is
still read, its rows without them.
"""

from __future__ import annotations

import contextlib
import csv
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Collection, Iterable, Iterator, Mapping

from ucla_agent.core import metrics, records
from ucla_agent.core.paths import RUNS_DIR_ENV, repository_root, runs_folder
from ucla_agent.core.stats import percentile
from ucla_agent.practice.werewolf.arms import Arm
from ucla_agent.practice.werewolf.bench_truth import TRUTH_COLUMNS, UNPLAYED, truth_columns
from ucla_agent.practice.werewolf.engine import SEAT_COUNT, SEER, VILLAGER, WOLF
from ucla_agent.practice.werewolf.mixes import MIXES, OPPONENT_FACTORIES
from ucla_agent.practice.werewolf.play import PracticeResult, play_practice_game
from ucla_agent.practice.werewolf.seat import Opponent

BENCH_ROLES = (VILLAGER, SEER, WOLF)
DEFAULT_SEED_BASE = 1000
PRACTICE_FOLDER = Path("practice") / "werewolf"
RESULTS_FILE_NAME = "results.csv"
SUMMARY_FILE_NAME = "summary.json"
COLUMNS = (
    "seed",
    "seat",
    "role",
    "mix",
    "arm",
    "winner",
    "our_side_won",
    "days",
    "fallbacks",
    "model_calls",
    "decision_p95_ms",
    "unhandled_errors",
    *TRUTH_COLUMNS,
)
# The columns of a results file written before the engine-truth columns; its rows are read without them.
EARLIER_COLUMNS = COLUMNS[: -len(TRUTH_COLUMNS)]
# The winner of a game that raised before it ended.
NO_WINNER = "none"
ALL_ROLES = "all"
DECISION_PERCENTILE = 95
_JSON_INDENT = 2
_FILE_ENCODING = "utf-8"


@dataclass(frozen=True)
class PlannedGame:
    """One game of a run: the role forced on our seat, the seed and our seat."""

    role: str
    seed: int
    seat: int


@dataclass(frozen=True)
class PlayedGame:
    """A game's row of `results.csv`, and the exception that ended it when it raised."""

    row: dict[str, Any]
    error: str | None


@dataclass(frozen=True)
class Tally:
    """The games, wins and unhandled errors of a set of rows."""

    games: int
    wins: int
    unhandled_errors: int

    @property
    def win_rate(self) -> float | None:
        """Wins over games; None without a game."""
        return self.wins / self.games if self.games else None


def planned_games(games_per_role: int, seed_base: int) -> list[PlannedGame]:
    """The games of a run, role after role: seeds `seed_base` to `seed_base + games_per_role - 1`, seat `seed % 7`."""
    seeds = range(seed_base, seed_base + games_per_role)
    return [PlannedGame(role, seed, seed % SEAT_COUNT) for role in BENCH_ROLES for seed in seeds]


def label_folder(label: str) -> Path:
    """The folder of a label: `practice/werewolf/<label>` under `runs/` or the folder `UCLA_AGENT_RUNS_DIR` names."""
    return runs_folder(repository_root() / records.RUNS_DIR_NAME) / PRACTICE_FOLDER / label


def run_bench(
    folder: Path,
    arm: Arm,
    mix: str,
    games: Iterable[PlannedGame],
    *,
    opponent_factories: Mapping[str, Callable[[], Opponent]] = OPPONENT_FACTORIES,
    on_game: Callable[[PlayedGame], None] = lambda _played: None,
    model_roles_started: Collection[str] = (),
    tuning: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Play every planned game with `arm` in our seat at a `mix` table; returns the rows, as written to `folder`.

    `on_game` is told of each game as soon as it is played. `opponent_factories`
    makes the opponents: the run's, whose `self` copies play the run's
    settings, or stubs for tests. `model_roles_started` are the model server
    roles started for the run and `tuning` the path and SHA-256 of the tuning
    file its Werewolf agents play, both of which the summary records.
    """
    planned = list(games)
    rows: list[dict[str, Any]] = []
    with _records_under(folder):
        for game in planned:
            played = _play(folder, game, arm, mix, opponent_factories)
            rows.append(played.row)
            write_results(folder, rows)
            write_summary(folder, summary(arm, mix, planned, rows, model_roles_started, tuning))
            on_game(played)
    return rows


@contextlib.contextmanager
def _records_under(folder: Path) -> Iterator[None]:
    """Have the match records written under `folder` while the games are played; the variable is restored after."""
    previous = os.environ.get(RUNS_DIR_ENV)
    os.environ[RUNS_DIR_ENV] = str(folder)
    try:
        yield
    finally:
        if previous is None:
            del os.environ[RUNS_DIR_ENV]
        else:
            os.environ[RUNS_DIR_ENV] = previous


def _play(
    folder: Path, game: PlannedGame, arm: Arm, mix: str, opponent_factories: Mapping[str, Callable[[], Opponent]]
) -> PlayedGame:
    """Play one game and read its row; the match folder that appeared during the game is the game's record."""
    before = _match_folders(folder)
    try:
        result = play_practice_game(
            game.seed, arm.factory, mix, game.role, seat=game.seat, opponent_factories=opponent_factories
        )
    except Exception as exc:  # noqa: BLE001 - a game that raises is a row with an unhandled error, and the run goes on
        result, error = None, f"{type(exc).__name__}: {exc}"
    else:
        error = None
    new_folders = _match_folders(folder) - before
    record = metrics.load_match(new_folders.pop()) if new_folders else None
    row = {
        "seed": game.seed,
        "seat": game.seat,
        "role": game.role,
        "mix": mix,
        "arm": arm.name,
        **_outcome(result),
        **record_figures(record, raised=result is None),
    }
    return PlayedGame(row, error)


def _outcome(result: PracticeResult | None) -> dict[str, Any]:
    """The columns the finished game gives: its winner, whether our side won, its last day and its engine truth; a
    game that raised (no result) has no winner, no last day and no claim day."""
    if result is None:
        return {"winner": NO_WINNER, "our_side_won": 0, "days": None, **UNPLAYED}
    return {
        "winner": result.winner,
        "our_side_won": int(result.our_side_won),
        "days": result.days_played,
        **truth_columns(result.engine),
    }


def _match_folders(folder: Path) -> set[Path]:
    """The match folders under the dated folders of `folder`."""
    if not folder.is_dir():
        return set()
    return {match for dated in folder.iterdir() if dated.is_dir() for match in metrics.match_folders(dated)}


def record_figures(record: metrics.Match | None, *, raised: bool) -> dict[str, Any]:
    """The columns a game's match record gives; a game that raised has one unhandled error more than its record shows.

    `fallbacks` counts the record's `fallback` events, `model_calls` its
    `decision` and `text_decision` events (one per call to a source), and
    `decision_p95_ms` is the 95th percentile of the `compute_ms` of its
    `action` events: how long our seat took to decide a move.
    """
    if record is None:
        return {"fallbacks": 0, "model_calls": 0, "decision_p95_ms": None, "unhandled_errors": int(raised)}
    recorded_unhandled = sum(1 for error in record.data_of("error") if error.get("type") == metrics.UNHANDLED_ERROR)
    return {
        "fallbacks": len(record.data_of("fallback")),
        "model_calls": len(record.data_of("decision")) + len(record.data_of("text_decision")),
        "decision_p95_ms": percentile(
            (action.get("compute_ms") for action in record.data_of("action")), DECISION_PERCENTILE
        ),
        "unhandled_errors": recorded_unhandled + int(raised),
    }


# -- the results files -----------------------------------------------------------------------------


def tally(rows: Iterable[Mapping[str, Any]]) -> dict[str, Tally]:
    """The tally of each benchmark role, in order, then of all rows; works on rows as played and as read back."""
    rows = list(rows)
    by_role = {role: _tally([row for row in rows if row["role"] == role]) for role in BENCH_ROLES}
    return {**by_role, ALL_ROLES: _tally(rows)}


def _tally(rows: list[Mapping[str, Any]]) -> Tally:
    return Tally(
        games=len(rows),
        wins=sum(int(row["our_side_won"]) for row in rows),
        unhandled_errors=sum(int(row["unhandled_errors"]) for row in rows),
    )


def summary(
    arm: Arm,
    mix: str,
    planned: list[PlannedGame],
    rows: list[dict[str, Any]],
    model_roles_started: Collection[str] = (),
    tuning: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The content of `summary.json`: the arm, the mix and its opponents, the settings, the tuning file the run's
    Werewolf agents play (path and SHA-256), the model server roles started for the run, the seeds, and each role's
    games, wins and unhandled errors so far."""
    tallies = tally(rows)
    return {
        "arm": arm.name,
        "mix": mix,
        "opponents": list(MIXES[mix]),
        "settings": arm.settings,
        "tuning": None if tuning is None else dict(tuning),
        "model_roles_started": sorted(model_roles_started),
        "seeds": sorted({game.seed for game in planned}),
        "roles": {role: _tally_fields(tallies[role]) for role in BENCH_ROLES},
        ALL_ROLES: _tally_fields(tallies[ALL_ROLES]),
    }


def _tally_fields(counted: Tally) -> dict[str, int]:
    return {"games": counted.games, "wins": counted.wins, "unhandled_errors": counted.unhandled_errors}


def write_results(folder: Path, rows: list[dict[str, Any]]) -> Path:
    """Write `results.csv` into `folder`, one row per game under the column names; a missing value is empty."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / RESULTS_FILE_NAME
    with path.open("w", encoding=_FILE_ENCODING, newline="") as results_file:
        writer = csv.DictWriter(results_file, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_summary(folder: Path, content: dict[str, Any]) -> Path:
    """Write `summary.json` into `folder`."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / SUMMARY_FILE_NAME
    path.write_text(json.dumps(content, indent=_JSON_INDENT) + "\n", encoding=_FILE_ENCODING)
    return path


def read_results(folder: Path) -> list[dict[str, str]]:
    """The rows of a label folder's `results.csv`, as text; a file written before the engine-truth columns gives rows
    without them.

    Raises `ValueError` when the folder holds no `results.csv` or the file's
    columns are not the benchmark's.
    """
    path = folder / RESULTS_FILE_NAME
    if not path.is_file():
        raise ValueError(f"{folder} holds no {RESULTS_FILE_NAME}")
    with path.open(encoding=_FILE_ENCODING, newline="") as results_file:
        reader = csv.DictReader(results_file)
        if tuple(reader.fieldnames or ()) not in (COLUMNS, EARLIER_COLUMNS):
            raise ValueError(f"{path} does not have the columns {', '.join(COLUMNS)}")
        return list(reader)
