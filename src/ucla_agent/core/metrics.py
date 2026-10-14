"""Post-run metrics, computed from match records.

This is the one module of the agent that reads match records, and nothing
that plays imports it: it is used after a run, by `scripts/report.py`, and
while a match is played by the feed page (`ucla_agent.feed`), which follows a
record as it grows (`read_from`, `read_meta`). Every figure comes from the
events and meta files of a match folder alone, so the same record always
gives the same metrics, with no network, no key and no game code involved.

A match folder holds one events file per attempt (the agent may have been
restarted during the match). All attempts are read, in attempt order, as one
match. The records folder of a local benchmark game is the exception: there
only the newest attempt is the game (`newest_attempt`).
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ucla_agent.core import metrics_redalert
from ucla_agent.core.record_files import METRICS_FILE_NAME, attempt_of_events_file, meta_file_name
from ucla_agent.core.secrets import redact
from ucla_agent.core.stats import numbers, percentiles

WIN = "win"
LOSS = "loss"
DRAW = "draw"
NO_RESULT = "no_result"
UNKNOWN_TERMINATION = "unknown"
# The agent's return in the result object, as the platform scores a match. A
# game may score on a scale of its own (Werewolf: +1 a win, -1 a loss), which
# `_outcome_off_the_scale` reads for every other return.
OUTCOME_BY_RETURN = {1.0: WIN, 0.0: LOSS, 0.5: DRAW}
CLOSING_KINDS = ("result", "match_end")
UNHANDLED_ERROR = "unhandled"
STALE_DISCARDED = "stale_discarded"
IDENTITY_KEYS = ("session_id", "game_type", "tournament_id", "context", "agent_id", "seat", "started_at")
_SECONDS_DECIMALS = 3
_JSON_INDENT = 2
_FILE_ENCODING = "utf-8"


@dataclass(frozen=True)
class Attempt:
    """One attempt at a match: its number, its meta file's content and its events in file order."""

    number: int
    meta: dict[str, Any]
    events: list[dict[str, Any]]
    unreadable_lines: int

    def data_of(self, kind: str) -> list[dict[str, Any]]:
        """The data of this attempt's events of one kind, in file order."""
        return [event["data"] for event in self.events if event["kind"] == kind]


@dataclass(frozen=True)
class Match:
    """Every attempt at one match, in attempt order."""

    folder: Path
    attempts: list[Attempt]

    @property
    def events(self) -> list[dict[str, Any]]:
        """The events of all attempts, attempt after attempt."""
        return [event for attempt in self.attempts for event in attempt.events]

    def data_of(self, kind: str) -> list[dict[str, Any]]:
        """The data of every event of one kind, attempt after attempt."""
        return [data for attempt in self.attempts for data in attempt.data_of(kind)]


# -- reading a match folder ------------------------------------------------------------------------


def match_folders(path: Path) -> list[Path]:
    """The match folders a path stands for: itself when it holds a record, else its sub-folders that do, by name.

    A dated folder under `runs/` therefore gives every match of that day. A
    path that is no folder, or holds no record at either level, gives none.
    """
    if _holds_a_record(path):
        return [path]
    if not path.is_dir():
        return []
    return sorted(child for child in path.iterdir() if _holds_a_record(child))


def _holds_a_record(folder: Path) -> bool:
    return folder.is_dir() and any(attempt_of_events_file(name) is not None for name in os.listdir(folder))


def load_match(folder: Path) -> Match:
    """Read every attempt of the match recorded in `folder`.

    A line that is not an event (a line cut off when the process was killed,
    for instance) is skipped and counted. Raises `FileNotFoundError` when the
    folder holds no events file.
    """
    numbered = sorted(
        (attempt, name) for name in os.listdir(folder) if (attempt := attempt_of_events_file(name)) is not None
    )
    if not numbered:
        raise FileNotFoundError(f"{folder} holds no match record: there is no events file in it")
    return Match(folder, [_load_attempt(folder, attempt, name) for attempt, name in numbered])


def newest_attempt(records_folder: Path) -> Match | None:
    """The newest attempt recorded under a records folder, read as a match of its own; None when the folder holds none.

    A records folder is laid out as `runs/` is: dated folders of match
    folders. The newest attempt is the one in the latest dated folder with the
    highest attempt number, the match folder's name deciding between two
    folders of one day, and only its events file is read. A local benchmark
    keeps one game in each folder and runs the agent inside the harness, which
    never restarts it during a game: two attempts there are two plays of the
    game, and the newest is the one the harness summary next to them describes.
    """
    if not records_folder.is_dir():
        return None
    attempts = [
        (dated_folder.name, attempt, folder.name, folder, name)
        for dated_folder in records_folder.iterdir()
        for folder in match_folders(dated_folder)
        for name in os.listdir(folder)
        if (attempt := attempt_of_events_file(name)) is not None
    ]
    if not attempts:
        return None
    _, attempt, _, folder, events_name = max(attempts)
    return Match(folder, [_load_attempt(folder, attempt, events_name)])


def read_from(path: Path, offset: int) -> bytes:
    """The bytes of a record file from byte `offset` to its end: what was appended since a reader got that far.

    A reader that follows a record while it is written calls this with the
    offset its previous call reached, and gets only what is new; the last line
    may still be cut off, its newline not yet written.
    """
    with open(path, "rb") as record_file:
        record_file.seek(offset)
        return record_file.read()


def read_meta(folder: Path, attempt: int) -> dict[str, Any]:
    """The content of one attempt's meta file in a match folder; empty while it is missing or not a JSON object."""
    return _read_meta(folder / meta_file_name(attempt))


def _load_attempt(folder: Path, attempt: int, events_name: str) -> Attempt:
    lines = (folder / events_name).read_text(encoding=_FILE_ENCODING, errors="replace").split("\n")
    parsed = [_parse_event(line) for line in lines if line.strip()]
    events = [event for event in parsed if event is not None]
    return Attempt(attempt, _read_meta(folder / meta_file_name(attempt)), events, len(parsed) - len(events))


def _parse_event(line: str) -> dict[str, Any] | None:
    """The event a record line holds, or None when the line is not one."""
    try:
        event = json.loads(line)
    except ValueError:
        return None
    is_event = isinstance(event, dict) and isinstance(event.get("kind"), str) and isinstance(event.get("data"), dict)
    return event if is_event else None


def _read_meta(path: Path) -> dict[str, Any]:
    """The content of a meta file; empty when the file is missing or is not a JSON object."""
    if not path.is_file():
        return {}
    try:
        meta = json.loads(path.read_text(encoding=_FILE_ENCODING, errors="replace"))
    except ValueError:
        return {}
    return meta if isinstance(meta, dict) else {}


# -- the metrics every game has --------------------------------------------------------------------


def compute_metrics(match: Match) -> dict[str, Any]:
    """The metrics of one match, from its record alone: those every game has, then those of the match's game."""
    game_metrics = _GAME_METRICS.get(match.attempts[0].meta.get("game_type"), _no_game_metrics)
    return {
        **_identity(match),
        **_attempt_facts(match),
        **_outcome(match),
        "duration_s": _duration_s(match),
        **_decision_metrics(match),
        **_fallback_metrics(match),
        **_error_metrics(match),
        "dropped_events": _dropped_events(match),
        "unreadable_lines": sum(attempt.unreadable_lines for attempt in match.attempts),
        "compute_ms": percentiles(action.get("compute_ms") for action in match.data_of("action")),
        **game_metrics(match),
    }


def _no_game_metrics(_match: Match) -> dict[str, Any]:
    return {}


# Platform game type -> the function that adds that game's own metrics.
_GAME_METRICS: dict[Any, Callable[[Match], dict[str, Any]]] = {"red_alert": metrics_redalert.compute}


def _identity(match: Match) -> dict[str, Any]:
    """The facts that do not change between attempts, from the first attempt's meta file."""
    first_meta = match.attempts[0].meta
    return {key: first_meta.get(key) for key in IDENTITY_KEYS}


def _attempt_facts(match: Match) -> dict[str, Any]:
    commits = [attempt.meta.get("commit") for attempt in match.attempts]
    config_hashes = [attempt.meta.get("config_hashes") for attempt in match.attempts]
    return {
        "attempts": len(match.attempts),
        "commit_by_attempt": commits,
        "config_hashes_by_attempt": config_hashes,
        "commit_differs_between_attempts": any(commit != commits[0] for commit in commits),
        "config_differs_between_attempts": any(hashes != config_hashes[0] for hashes in config_hashes),
    }


def _outcome(match: Match) -> dict[str, str]:
    """The outcome and termination reason, from the `result` event of the last attempt that has one."""
    results = [attempt.data_of("result") for attempt in match.attempts]
    last_result = next((events[-1] for events in reversed(results) if events), None)
    if last_result is None:
        return {"outcome": NO_RESULT, "termination_reason": UNKNOWN_TERMINATION}
    result = last_result.get("result")
    result = result if isinstance(result, dict) else {}
    return {
        "outcome": outcome_of(result, match.attempts[0].meta.get("agent_id")),
        "termination_reason": result.get("termination_reason") or UNKNOWN_TERMINATION,
    }


def outcome_of(result: dict[str, Any], agent_id: Any) -> str:
    """The outcome a result object gives the agent of this id: `win`, `loss`, `draw` or `no_result`."""
    return _outcome_of_return(_own_return(result, agent_id), result.get("returns"))


def _outcome_of_return(own_return: float | None, returns: Any) -> str:
    """The outcome this agent's return stands for; `no_result` when there is no return.

    The platform's scale (1 a win, 0.5 a draw, 0 a loss) reads as it is; any
    other return is read by `_outcome_off_the_scale`.
    """
    if own_return is None:
        return NO_RESULT
    if own_return in OUTCOME_BY_RETURN:
        return OUTCOME_BY_RETURN[own_return]
    return _outcome_off_the_scale(own_return, returns)


def _outcome_off_the_scale(own_return: float, returns: Any) -> str:
    """The outcome of a return a game scores on a scale of its own (Werewolf: +1 a win, -1 a loss).

    A return below 0 is a loss. One above 0 is a win when no seat got more,
    else a draw: a positive return alone does not say the seat won. A return
    that is neither (NaN, which JSON allows) is no result, so a game that did
    not score is never counted as won or lost.
    """
    if own_return < 0:
        return LOSS
    if own_return > 0:
        return WIN if own_return == _highest_return(own_return, returns) else DRAW
    return NO_RESULT


def _highest_return(own_return: float, returns: Any) -> float:
    """The highest return of any seat: this agent's and the numbers among `returns`."""
    other_returns = numbers(returns.values()) if isinstance(returns, dict) else []
    return max([own_return, *other_returns])


def _own_return(result: dict[str, Any], agent_id: Any) -> float | None:
    """This agent's return in a result object: its entry in `returns`, else `your_return`; None when neither is a number."""
    returns = result.get("returns")
    value = returns.get(agent_id) if isinstance(returns, dict) and agent_id in returns else result.get("your_return")
    return value if numbers([value]) else None


def _duration_s(match: Match) -> float | None:
    """Seconds from the first recorded event to the last, over all attempts."""
    times = [moment for event in match.events if (moment := _parse_time(event.get("t"))) is not None]
    if not times:
        return None
    return round((times[-1] - times[0]).total_seconds(), _SECONDS_DECIMALS)


def _parse_time(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _decision_metrics(match: Match) -> dict[str, Any]:
    decisions = match.data_of("decision")
    sources = sorted({str(decision.get("source")) for decision in decisions})
    return {
        "decisions_total": len(decisions),
        "decisions_by_source": dict(Counter(str(decision.get("source")) for decision in decisions)),
        "decision_latency_ms": {
            source: percentiles(d.get("latency_ms") for d in decisions if str(d.get("source")) == source)
            for source in sources
        },
        "stale_discarded_count": sum(1 for decision in decisions if decision.get("outcome") == STALE_DISCARDED),
    }


def _fallback_metrics(match: Match) -> dict[str, Any]:
    fallbacks = match.data_of("fallback")
    return {
        "fallback_count": len(fallbacks),
        "fallback_by_reason": dict(Counter(str(fallback.get("reason")) for fallback in fallbacks)),
    }


def _error_metrics(match: Match) -> dict[str, int]:
    errors = match.data_of("error")
    return {
        "error_count": len(errors),
        "unhandled_error_count": sum(1 for error in errors if error.get("type") == UNHANDLED_ERROR),
    }


def _dropped_events(match: Match) -> int | None:
    """Events the writer dropped, summed over the attempts that say so in their closing event; None when none does."""
    counts = [
        data.get("dropped_events")
        for attempt in match.attempts
        for kind in CLOSING_KINDS
        for data in attempt.data_of(kind)[-1:]
    ]
    known = numbers(counts)
    return int(sum(known)) if known else None


# -- writing what the report tool produces ---------------------------------------------------------


def write_output(path: Path, text: str) -> None:
    """Write one file of the report tool (`metrics.json`, a report) with secrets redacted, creating its folder.

    Every file the report tool writes goes through here. The bytes are the
    same on every platform, so the same records always give the same file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(redact(text).encode(_FILE_ENCODING))


def write_metrics_file(match_folder: Path, computed: dict[str, Any]) -> Path:
    """Write a match's metrics as `metrics.json` into its folder and return the file's path."""
    path = match_folder / METRICS_FILE_NAME
    write_output(path, json.dumps(computed, indent=_JSON_INDENT, sort_keys=True) + "\n")
    return path
