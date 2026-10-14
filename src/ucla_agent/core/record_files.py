"""How the folders and files of a match record are named.

A match folder is `<session id>__<seat>`: one folder for each seat the agent
plays. In a self-play match the agent plays several seats of one game, each
in its own process, and all of them share the session id; the seat in the
name keeps their records apart.

A match folder holds one events file and one meta file per attempt:
`events.jsonl` and `meta.json` for the first attempt, and `events.<n>.jsonl`
and `meta.<n>.json` for attempt `n` when the agent was restarted during the
match. The writer and the reader both go by the rule in this module.
"""

from __future__ import annotations

import re
from typing import Any

EVENTS_FILE_NAME = "events.jsonl"
META_FILE_NAME = "meta.json"
# Written into a match folder by the report tool after a run, never by the agent.
METRICS_FILE_NAME = "metrics.json"
FIRST_ATTEMPT = 1
SEAT_SEPARATOR = "__"
# The seat label when neither the game nor the runtime says which seat is played.
UNNAMED_SEAT = "seat"
UNNAMED_SESSION = "unknown-session"
_EVENTS_FILE_PATTERN = re.compile(r"events(?:\.(?P<attempt>[1-9][0-9]*))?\.jsonl")
# Session ids and seat names come from the platform and end up in a folder
# name: any character that could leave the dated folder, or that a file
# system could refuse, is replaced.
_UNSAFE_IN_FOLDER_NAME = re.compile(r"[^A-Za-z0-9_-]")


def seat_label(*names: Any) -> str:
    """The label of the seat an agent plays: the first of `names` that says something, made safe for a folder name.

    Callers give the names in order of preference (the game's own name for the
    seat, then the seat position the runtime knows). A name that is None or
    empty says nothing; when none says anything the label is `seat`.
    """
    for name in names:
        if name is not None and str(name):
            return _UNSAFE_IN_FOLDER_NAME.sub("_", str(name))
    return UNNAMED_SEAT


def match_folder_name(session_id: str, seat: str) -> str:
    """The name of the folder that holds the record of one seat of one match."""
    session = _UNSAFE_IN_FOLDER_NAME.sub("_", session_id) or UNNAMED_SESSION
    return f"{session}{SEAT_SEPARATOR}{seat}"


def events_file_name(attempt: int) -> str:
    """The name of the events file of the given attempt (1 is the first)."""
    return _numbered(EVENTS_FILE_NAME, attempt)


def meta_file_name(attempt: int) -> str:
    """The name of the meta file of the given attempt (1 is the first)."""
    return _numbered(META_FILE_NAME, attempt)


def attempt_of_events_file(file_name: str) -> int | None:
    """The attempt whose events file has this name, or None when no attempt's events file is named so."""
    match = _EVENTS_FILE_PATTERN.fullmatch(file_name)
    if match is None:
        return None
    attempt = int(match["attempt"] or FIRST_ATTEMPT)
    return attempt if events_file_name(attempt) == file_name else None


def _numbered(first_attempt_name: str, attempt: int) -> str:
    if attempt == FIRST_ATTEMPT:
        return first_attempt_name
    stem, extension = first_attempt_name.split(".")
    return f"{stem}.{attempt}.{extension}"
