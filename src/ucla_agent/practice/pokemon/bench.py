"""The Pokémon practice benchmark: our seat's matches against one practice opponent, one row per match, and the
label's results files.

A run plays N matches against one opponent kind (`random`, `first` or
`self`). Match i has seed `seed_base + i`, which draws its 18-card pool, the
battle's seed and the `random` opponent's choices, so two runs of one seed
base play the same pools. Our seat (seat 0) makes the draft's first pick on
an even seed and the second on an odd one, so a run alternates who opens the
draft instead of leaving it to the seed's draw.

Everything a run writes goes into its label folder,
`<runs>/practice/pokemon/<label>/`, where `<runs>` is `runs/` or the folder
`UCLA_AGENT_RUNS_DIR` names: the match records of our seat (the variable
names the label folder while the matches are played), `results.csv` with one
row per match and `summary.json`. Both files are written again after each
match, so a run that is stopped keeps the matches it played.

A row's `our_won` and `turns` come from the finished match, the rest from
our seat's match record (`record_figures`). A match that raises is a row
with no turns and one more unhandled error, and the run goes on.
"""

from __future__ import annotations

import contextlib
import csv
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Collection, Iterable, Iterator, Mapping, Sequence

from ucla_agent.core import metrics, records
from ucla_agent.core.decide.journal import FALLBACK_KIND
from ucla_agent.core.paths import RUNS_DIR_ENV, repository_root, runs_folder
from ucla_agent.core.stats import numbers, percentile
from ucla_agent.games.pokemon.agent import UNEXPECTED_STATE
from ucla_agent.games.pokemon.decide import DECISION_EVENT
from ucla_agent.practice.pokemon.cards import load_catalog
from ucla_agent.practice.pokemon.doubles import MOVING_PHASE
from ucla_agent.practice.pokemon.draft import DRAFT_PHASE
from ucla_agent.practice.pokemon.opponents import OPPONENT_FACTORIES
from ucla_agent.practice.pokemon.play import OUR_SEAT, PokemonPracticeResult, play_practice_match
from ucla_agent.practice.pokemon.preview import PREVIEW_PHASE

DEFAULT_SEED_BASE = 5000
OTHER_SEAT = 1 - OUR_SEAT
PRACTICE_FOLDER = Path("practice") / "pokemon"
RESULTS_FILE_NAME = "results.csv"
SUMMARY_FILE_NAME = "summary.json"
PHASES = (DRAFT_PHASE, PREVIEW_PHASE, MOVING_PHASE)
MAX_DECISION_COLUMNS = {phase: f"max_decision_ms_{phase}" for phase in PHASES}
COLUMNS = (
    "seed",
    "opponent",
    "our_won",
    "turns",
    "decisions",
    "fallbacks",
    "parse_degradations",
    "decision_p95_ms",
    *MAX_DECISION_COLUMNS.values(),
    "unhandled_errors",
)
DECISION_PERCENTILE = 95
_JSON_INDENT = 2
_FILE_ENCODING = "utf-8"


@dataclass(frozen=True)
class PlannedMatch:
    """One match of a run: its seed and the seat that makes the draft's first pick."""

    seed: int
    first_drafter: int


@dataclass(frozen=True)
class PlayedMatch:
    """A match's row of `results.csv`, and the error that ended it early when there was one."""

    row: dict[str, Any]
    error: str | None


@dataclass(frozen=True)
class Tally:
    """The matches, wins, fallbacks, parse degradations and unhandled errors of a set of rows."""

    games: int
    wins: int
    fallbacks: int
    parse_degradations: int
    unhandled_errors: int

    @property
    def win_rate(self) -> float | None:
        """Wins over matches; None without a match."""
        return self.wins / self.games if self.games else None


def planned_matches(games: int, seed_base: int) -> list[PlannedMatch]:
    """The matches of a run: seeds `seed_base` to `seed_base + games - 1`, our seat drafting first on an even seed."""
    return [
        PlannedMatch(seed, OUR_SEAT if seed % 2 == 0 else OTHER_SEAT) for seed in range(seed_base, seed_base + games)
    ]


def label_folder(label: str) -> Path:
    """The folder of a label: `practice/pokemon/<label>` under `runs/` or the folder `UCLA_AGENT_RUNS_DIR` names."""
    return runs_folder(repository_root() / records.RUNS_DIR_NAME) / PRACTICE_FOLDER / label


def run_bench(
    folder: Path,
    agent_factory: Callable[[], Any],
    opponent: str,
    matches: Iterable[PlannedMatch],
    *,
    settings: Mapping[str, Any] | None = None,
    model_roles_started: Collection[str] = (),
    on_match: Callable[[PlayedMatch], None] = lambda _played: None,
    helper_command: Sequence[str] | None = None,
    opponent_factories: Mapping[str, Callable[[int | None], Any]] = OPPONENT_FACTORIES,
) -> list[dict[str, Any]]:
    """Play every planned match with `agent_factory`'s decision-maker in our seat against `opponent`; returns the
    rows, as written to `folder`.

    `settings` describes our seat and `model_roles_started` names the model
    server roles started for the run, both for the summary. `on_match` is told
    of each match as soon as it is played. `helper_command` replaces real
    Showdown and `opponent_factories` the practice opponents, for tests.
    """
    planned = list(matches)
    catalog = load_catalog()

    def play(match: PlannedMatch) -> PokemonPracticeResult:
        return play_practice_match(
            match.seed,
            agent_factory,
            opponent,
            helper_command=helper_command,
            catalog=catalog,
            opponent_factories=opponent_factories,
            first_drafter=match.first_drafter,
        )

    rows: list[dict[str, Any]] = []
    with _records_under(folder):
        for match in planned:
            played = _played(folder, match, opponent, play)
            rows.append(played.row)
            write_results(folder, rows)
            write_summary(folder, summary(opponent, planned, rows, settings or {}, model_roles_started))
            on_match(played)
    return rows


@contextlib.contextmanager
def _records_under(folder: Path) -> Iterator[None]:
    """Have the match records written under `folder` while the matches are played; the variable is restored after."""
    previous = os.environ.get(RUNS_DIR_ENV)
    os.environ[RUNS_DIR_ENV] = str(folder)
    try:
        yield
    finally:
        if previous is None:
            del os.environ[RUNS_DIR_ENV]
        else:
            os.environ[RUNS_DIR_ENV] = previous


def _played(
    folder: Path, match: PlannedMatch, opponent: str, play: Callable[[PlannedMatch], PokemonPracticeResult]
) -> PlayedMatch:
    """Play one match and make its row; the match folder that appeared during the match is our seat's record.

    The row's `unhandled_errors` adds one to the record's for a runner error
    that ended the match for us and for a match that raised.
    """
    before = _match_folders(folder)
    try:
        result = play(match)
    except Exception as exc:  # noqa: BLE001 - a match that raises is a row with an unhandled error, and the run goes on
        result, error = None, f"{type(exc).__name__}: {exc}"
    else:
        error = result.error
    new_folders = _match_folders(folder) - before
    figures = record_figures(metrics.load_match(new_folders.pop()) if new_folders else None)
    figures["unhandled_errors"] += int(error is not None)
    row = {
        "seed": match.seed,
        "opponent": opponent,
        "our_won": int(result is not None and result.our_won),
        "turns": None if result is None else result.turns,
        **figures,
    }
    return PlayedMatch(row, error)


def _match_folders(folder: Path) -> set[Path]:
    """The match folders under the dated folders of `folder`."""
    if not folder.is_dir():
        return set()
    return {match for dated in folder.iterdir() if dated.is_dir() for match in metrics.match_folders(dated)}


def match_records(folder: Path) -> list[metrics.Match]:
    """Our seat's match records in a label folder, in the order of their folders; none for a folder without any."""
    return [metrics.load_match(match) for match in sorted(_match_folders(folder))]


def record_figures(record: metrics.Match | None) -> dict[str, Any]:
    """The columns our seat's match record gives; all counts 0 and no times without a record.

    - `decisions`: its `action` events, one per decision our seat made.
    - `parse_degradations`: the `parse_degradations` of its `pokemon_decision`
      events plus its `unexpected_state` errors, each a template-only
      fallback, which writes no `pokemon_decision` event.
    - `fallbacks`: the parse degradations plus its `fallback` events, each a
      model answer that was not used.
    - `decision_p95_ms` and the slowest decision of each phase: the
      `compute_ms` of its `action` events, each of the phase of the `state`
      event before it.
    - `unhandled_errors`: its `unhandled` errors.
    """
    timed = timed_decisions(record)
    degradations = 0 if record is None else _parse_degradations(record)
    model_fallbacks = 0 if record is None else len(record.data_of(FALLBACK_KIND))
    return {
        "decisions": len(timed),
        "fallbacks": degradations + model_fallbacks,
        "parse_degradations": degradations,
        "decision_p95_ms": percentile((compute_ms for _phase, compute_ms in timed), DECISION_PERCENTILE),
        **{
            column: max(numbers(compute_ms for of_phase, compute_ms in timed if of_phase == phase), default=None)
            for phase, column in MAX_DECISION_COLUMNS.items()
        },
        "unhandled_errors": 0 if record is None else _errors_of_type(record, metrics.UNHANDLED_ERROR),
    }


def timed_decisions(record: metrics.Match | None) -> list[tuple[str | None, Any]]:
    """Each `action` event's phase and `compute_ms`, in order; the phase is that of the `state` event before it."""
    timed: list[tuple[str | None, Any]] = []
    phase = None
    for event in record.events if record is not None else ():
        if event["kind"] == "state":
            phase = event["data"].get("phase")
        elif event["kind"] == "action":
            timed.append((phase, event["data"].get("compute_ms")))
    return timed


def _parse_degradations(record: metrics.Match) -> int:
    counted = sum(numbers(decision.get("parse_degradations") for decision in record.data_of(DECISION_EVENT)))
    return int(counted) + _errors_of_type(record, UNEXPECTED_STATE)


def _errors_of_type(record: metrics.Match, error_type: str) -> int:
    return sum(1 for error in record.data_of("error") if error.get("type") == error_type)


# -- the results files -----------------------------------------------------------------------------


def tally(rows: Iterable[Mapping[str, Any]]) -> Tally:
    """The tally of the rows; works on rows as played and as read back."""
    rows = list(rows)
    return Tally(
        games=len(rows),
        wins=_total(rows, "our_won"),
        fallbacks=_total(rows, "fallbacks"),
        parse_degradations=_total(rows, "parse_degradations"),
        unhandled_errors=_total(rows, "unhandled_errors"),
    )


def _total(rows: list[Mapping[str, Any]], column: str) -> int:
    return sum(int(row[column]) for row in rows)


def slowest_decisions(rows: Iterable[Mapping[str, Any]]) -> dict[str, float | None]:
    """The slowest decision of each phase over the rows, in ms; None for a phase no row timed."""
    rows = list(rows)
    return {
        phase: max(numbers(_number(row[column]) for row in rows), default=None)
        for phase, column in MAX_DECISION_COLUMNS.items()
    }


def _number(value: Any) -> float | None:
    """A time as played (a number or None) or as read back (its text, empty when missing)."""
    return None if value is None or value == "" else float(value)


def summary(
    opponent: str,
    planned: list[PlannedMatch],
    rows: list[dict[str, Any]],
    settings: Mapping[str, Any],
    model_roles_started: Collection[str] = (),
) -> dict[str, Any]:
    """The content of `summary.json`: the opponent, our seat's settings, the model server roles started for the run,
    the seeds, the seeds on which our seat drafted first, and the tally and slowest decisions so far."""
    counted = tally(rows)
    return {
        "opponent": opponent,
        "settings": dict(settings),
        "model_roles_started": sorted(model_roles_started),
        "seeds": [match.seed for match in planned],
        "our_first_pick_seeds": [match.seed for match in planned if match.first_drafter == OUR_SEAT],
        "games": counted.games,
        "wins": counted.wins,
        "fallbacks": counted.fallbacks,
        "parse_degradations": counted.parse_degradations,
        "unhandled_errors": counted.unhandled_errors,
        "slowest_decision_ms": slowest_decisions(rows),
    }


def write_results(folder: Path, rows: list[dict[str, Any]]) -> Path:
    """Write `results.csv` into `folder`, one row per match under the column names; a missing value is empty."""
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
    """The rows of a label folder's `results.csv`, as text.

    Raises `ValueError` when the folder holds no `results.csv` or the file's
    columns are not the benchmark's.
    """
    path = folder / RESULTS_FILE_NAME
    if not path.is_file():
        raise ValueError(f"{folder} holds no {RESULTS_FILE_NAME}")
    with path.open(encoding=_FILE_ENCODING, newline="") as results_file:
        reader = csv.DictReader(results_file)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"{path} does not have the columns {', '.join(COLUMNS)}")
        return list(reader)
