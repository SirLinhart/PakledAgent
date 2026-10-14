"""One match record read while it is written, for the feed page.

A match folder holds one events file per attempt at the match: `events.jsonl`,
and `events.<n>.jsonl` for each restart of the agent. The reader reads them in
attempt order and, at every refresh, only what was appended since the last
one, so following a live match costs what is new and no more. A line is taken
in once its newline is there: the writer flushes line by line, and the end of
a file can be a line still being written.

Every event but `state` is numbered for the page, from 1 up across all
attempts (`seq`), so a page asks for what it has not seen yet by the last
number it has. States come five a second in Red Alert and the page shows them
only through its header, so only the latest is kept.

Each event gets the game time it belongs to: its own `game_seconds`, else its
`tick`, else, in Red Alert, its `state_version` (a Red Alert state's version
is its tick, 25 to the second, and a decision names the tick it was asked at
as its state tag), else the tick of the state recorded last. Other games have
no game clock, and their events none.

The files are read through `ucla_agent.core.metrics`, the one module that
reads match records. Nothing here writes.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ucla_agent.core import metrics
from ucla_agent.core.record_files import FIRST_ATTEMPT, attempt_of_events_file
from ucla_agent.core.stats import numbers

TICKS_PER_SECOND = 25
RED_ALERT = "red_alert"
STATE_KIND = "state"
# The events that close a record: the result, or the ending of a process that never saw one.
CLOSING_KINDS = frozenset({"result", "match_end"})
_NEWLINE = b"\n"
_FILE_ENCODING = "utf-8"


@dataclass(frozen=True)
class FeedEvent:
    """One event of the record other than a state, numbered for the page."""

    seq: int
    attempt: int
    record_seq: Any
    t: Any
    kind: str
    data: dict[str, Any]
    game_seconds: float | None

    def as_dict(self) -> dict[str, Any]:
        """The event as the page gets it, in JSON."""
        return {
            "seq": self.seq,
            "attempt": self.attempt,
            "record_seq": self.record_seq,
            "t": self.t,
            "kind": self.kind,
            "data": self.data,
            "game_seconds": self.game_seconds,
        }


class _FollowedFile:
    """One events file: how far it has been read, and the start of a line whose newline has not come yet."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._offset = 0
        self._unfinished = b""

    def new_lines(self) -> list[str]:
        """The lines completed since the last call, in file order; blank lines left out."""
        appended = metrics.read_from(self.path, self._offset)
        self._offset += len(appended)
        *complete, self._unfinished = (self._unfinished + appended).split(_NEWLINE)
        return [line.decode(_FILE_ENCODING, errors="replace") for line in complete if line.strip()]


class MatchReader:
    """Follows the record of one match folder: every attempt's events file, in attempt order. See the module's note."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.meta: dict[str, Any] = {}
        # The data of the state recorded last, and the event that closed the record, once there is one.
        self.latest_state: dict[str, Any] | None = None
        self.closing: dict[str, Any] | None = None
        self.unreadable_lines = 0
        self._files: dict[int, _FollowedFile] = {}
        self._events: list[FeedEvent] = []
        self._clock_seconds: float | None = None

    @property
    def name(self) -> str:
        """The match folder's name: `<session id>__<seat>`."""
        return self.folder.name

    @property
    def game_type(self) -> str | None:
        """The platform's game type, from the first attempt's meta file; None while that is not on disk."""
        return self.meta.get("game_type")

    @property
    def last_seq(self) -> int:
        """The number of the newest event kept; 0 while there is none."""
        return len(self._events)

    def refresh(self) -> list[dict[str, Any]]:
        """Read what every events file got since the last refresh; returns every new event, states included, in order.

        A line that is not an event (a line cut off when a process was killed)
        is skipped and counted in `unreadable_lines`.
        """
        if not self.meta:
            self.meta = metrics.read_meta(self.folder, FIRST_ATTEMPT)
        self._find_new_attempts()
        new_events = []
        for attempt in sorted(self._files):
            for line in self._files[attempt].new_lines():
                event = _parsed(line)
                if event is None:
                    self.unreadable_lines += 1
                    continue
                self._take_in(attempt, event)
                new_events.append(event)
        return new_events

    def events_after(self, seq: int) -> list[FeedEvent]:
        """The kept events numbered above `seq`, in order; all of them for 0 or less."""
        return self._events[max(seq, 0) :]

    def _find_new_attempts(self) -> None:
        for name in os.listdir(self.folder):
            attempt = attempt_of_events_file(name)
            if attempt is not None and attempt not in self._files:
                self._files[attempt] = _FollowedFile(self.folder / name)

    def _take_in(self, attempt: int, event: dict[str, Any]) -> None:
        kind, data = event["kind"], event["data"]
        if kind == STATE_KIND:
            self.latest_state = data
            self._note_clock(data)
            return
        if kind in CLOSING_KINDS:
            self.closing = {"kind": kind, "data": data}
        self._events.append(
            FeedEvent(
                seq=len(self._events) + 1,
                attempt=attempt,
                record_seq=event.get("seq"),
                t=event.get("t"),
                kind=kind,
                data=data,
                game_seconds=self._game_seconds(data),
            )
        )

    def _note_clock(self, state: dict[str, Any]) -> None:
        """Set the game clock from a Red Alert state, whose version is its tick."""
        if self.game_type == RED_ALERT and numbers([state.get("state_version")]):
            self._clock_seconds = state["state_version"] / TICKS_PER_SECOND

    def _game_seconds(self, data: dict[str, Any]) -> float | None:
        """When in the game an event happened: its own game_seconds, its tick, its Red Alert state version, else the
        last state's clock."""
        if numbers([data.get("game_seconds")]):
            return data["game_seconds"]
        if numbers([data.get("tick")]):
            return data["tick"] / TICKS_PER_SECOND
        version = _whole_number(data.get("state_version"))
        if self.game_type == RED_ALERT and version is not None:
            return version / TICKS_PER_SECOND
        return self._clock_seconds


def _whole_number(value: Any) -> int | None:
    """A whole number, or the text of one (a decision's state tag); None for anything else."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return int(value) if isinstance(value, str) and value.isdigit() else None


def _parsed(line: str) -> dict[str, Any] | None:
    """The event a record line holds, or None when the line is not one."""
    try:
        event = json.loads(line)
    except ValueError:
        return None
    is_event = isinstance(event, dict) and isinstance(event.get("kind"), str) and isinstance(event.get("data"), dict)
    return event if is_event else None
