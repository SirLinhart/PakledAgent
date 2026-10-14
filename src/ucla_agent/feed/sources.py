"""Which match record the feed follows: the one folder it was given, or the newest one under a runs folder.

A runs folder is laid out as `runs/` is: dated folders (`<UTC yyyymmdd>`)
of match folders. The newest match is the one whose first attempt started
last, by the `started_at` of its meta file; a match folder whose meta file is
not on disk yet is not a candidate until it is. Only the latest dated folder
that holds a match is looked through, since a match that starts later goes
into that folder or a later one. The runs folder is only listed and read:
when it does not exist yet, there is simply no match to follow.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

from ucla_agent.core import metrics
from ucla_agent.core.paths import repository_root, runs_folder
from ucla_agent.core.record_files import FIRST_ATTEMPT
from ucla_agent.core.records import RUNS_DIR_NAME
from ucla_agent.feed.reader import MatchReader

_DATED_FOLDER = re.compile(r"[0-9]{8}")


def default_runs_folder() -> Path:
    """The folder the agent writes its records under: `UCLA_AGENT_RUNS_DIR` when it is set, else `runs/` of the
    project root."""
    return runs_folder(repository_root() / RUNS_DIR_NAME)


class Source(Protocol):
    """What the feed reads from: a match reader, once there is a match, brought up to date on every refresh."""

    reader: MatchReader | None

    def refresh(self) -> list[dict[str, Any]]:
        """Bring the reader up to date; gives the events it read, states included."""


class FixedMatch:
    """The one match folder named on the command line (`--match`)."""

    def __init__(self, folder: Path) -> None:
        self.reader: MatchReader | None = MatchReader(folder)

    def refresh(self) -> list[dict[str, Any]]:
        """Read what the folder's events files got since the last refresh."""
        return self.reader.refresh()


class LatestMatch:
    """The newest match folder under a runs folder (`--latest`), followed until a newer one appears."""

    def __init__(self, runs_folder: Path) -> None:
        self.runs_folder = runs_folder
        self.reader: MatchReader | None = None
        # A match folder's start time never changes once its meta file is written, so each is read once.
        self._started_at: dict[Path, str] = {}

    def refresh(self) -> list[dict[str, Any]]:
        """Switch to the newest match folder when it is not the one followed, then read what it got since."""
        newest = self.newest_match_folder()
        if newest is not None and (self.reader is None or newest != self.reader.folder):
            self.reader = MatchReader(newest)
        return [] if self.reader is None else self.reader.refresh()

    def newest_match_folder(self) -> Path | None:
        """The match folder whose first attempt started last, in the latest dated folder that holds one; None while
        there is none."""
        dated = sorted(folder for folder in _sub_folders(self.runs_folder) if _DATED_FOLDER.fullmatch(folder.name))
        for day in reversed(dated):
            started = [
                (started_at, folder.name, folder)
                for folder in metrics.match_folders(day)
                if (started_at := self._start_of(folder)) is not None
            ]
            if started:
                return max(started)[2]
        return None

    def _start_of(self, folder: Path) -> str | None:
        if folder not in self._started_at:
            started_at = metrics.read_meta(folder, FIRST_ATTEMPT).get("started_at")
            if not isinstance(started_at, str):
                return None
            self._started_at[folder] = started_at
        return self._started_at[folder]


def _sub_folders(folder: Path) -> list[Path]:
    """The folders directly under `folder`; none when it does not exist."""
    if not folder.is_dir():
        return []
    return [child for child in folder.iterdir() if child.is_dir()]
