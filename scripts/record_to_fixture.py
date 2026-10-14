"""Turn the record of a real Red Alert match into a replay fixture for the tests.

    python scripts/record_to_fixture.py <match folder> <name>

Run it from the repository root, after a match. The match folder is the record
of one seat (`runs/<UTC yyyymmdd>/<session id>__<seat>`). The tool writes the
states that the record holds in full, in the order they were received, to
`tests/fixtures/redalert/replays/<name>.jsonl.gz`: gzip-compressed JSON Lines,
one state object per line, with secrets redacted. A record holds only some of
a match's states in full (at least every fifth one), so a replay has gaps of a
second or so between its states.

A fixture is tracked, so it is never larger than 4 MiB. When all the full
states of a record would not fit, the tool keeps every Nth of them, with the
smallest N that fits, and says which N it took.

The tool only reads the record. The same record always gives the same file,
byte for byte.

Exit code 0: the fixture was written. 2: the folder holds no record, the
record holds no full state, a single state is too large, or the command line
is wrong.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path
from typing import Any

from ucla_agent.core import metrics
from ucla_agent.core.console import print_line
from ucla_agent.core.paths import repository_root
from ucla_agent.core.secrets import redact

EXIT_WRITTEN = 0
EXIT_NOT_WRITTEN = 2
REPLAYS_FOLDER = Path("tests") / "fixtures" / "redalert" / "replays"
FIXTURE_SUFFIX = ".jsonl.gz"
MAX_FIXTURE_BYTES = 4 * 1024 * 1024
# A fixture's name becomes a file name, so it may hold nothing that could leave the replays folder.
FIXTURE_NAME = re.compile(r"[A-Za-z0-9_-]+")
# No time stamp in the gzip header: the same states always compress to the same bytes.
_NO_TIMESTAMP = 0
_FILE_ENCODING = "utf-8"


class NoFixtureError(Exception):
    """No fixture can be written from what was given; the message says why."""


def full_states(match_folder: Path) -> list[dict[str, Any]]:
    """The state objects a match record holds in full, attempt after attempt, in the order they were recorded.

    Raises `NoFixtureError` when the folder holds no record or the record no full state.
    """
    try:
        match = metrics.load_match(match_folder)
    except OSError as exc:
        raise NoFixtureError(f"{match_folder} holds no match record: {exc}") from exc
    states = [data["state"] for data in match.data_of("state") if isinstance(data.get("state"), dict)]
    if not states:
        raise NoFixtureError(f"The record in {match_folder} holds no state in full")
    return states


def compress(states: list[dict[str, Any]]) -> bytes:
    """The fixture file for these states: one redacted JSON line per state, gzip-compressed."""
    lines = "".join(redact(json.dumps(state)) + "\n" for state in states)
    return gzip.compress(lines.encode(_FILE_ENCODING), mtime=_NO_TIMESTAMP)


def thin_to_fit(states: list[dict[str, Any]], max_bytes: int = MAX_FIXTURE_BYTES) -> tuple[bytes, int]:
    """The fixture file that fits in `max_bytes`, and the N of "every Nth state" it was thinned to (1: all kept).

    The smallest N that fits is taken. Raises `NoFixtureError` when even the
    first state alone is too large.
    """
    for every in range(1, len(states) + 1):
        data = compress(states[::every])
        if len(data) <= max_bytes:
            return data, every
    raise NoFixtureError(f"One state alone compresses to more than {max_bytes} bytes; no fixture was written")


def fixture_path(name: str) -> Path:
    """Where the fixture of that name goes, under the repository root. Raises `NoFixtureError` for an unusable name."""
    if not FIXTURE_NAME.fullmatch(name):
        raise NoFixtureError(f"A fixture name may hold letters, digits, - and _ only, got {name!r}")
    return repository_root() / REPLAYS_FOLDER / f"{name}{FIXTURE_SUFFIX}"


def write_fixture(match_folder: Path, name: str, max_bytes: int = MAX_FIXTURE_BYTES) -> str:
    """Write the fixture for a match record and return the line that reports what was written."""
    path = fixture_path(name)
    states = full_states(match_folder)
    data, every = thin_to_fit(states, max_bytes)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    kept = len(states[::every])
    thinned = "every state kept" if every == 1 else f"1 state in {every} kept"
    return f"Wrote {path}: {kept} of {len(states)} full states, {thinned} (N = {every}), {len(data)} bytes"


def main(arguments: list[str]) -> int:
    """Write the fixture named on the command line from the match folder given there; returns the exit code."""
    parser = argparse.ArgumentParser(prog="python scripts/record_to_fixture.py", description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "match_folder", type=Path, help="the record of one seat: runs/<UTC yyyymmdd>/<session id>__<seat>"
    )
    parser.add_argument("name", help="the fixture's name: tests/fixtures/redalert/replays/<name>.jsonl.gz")
    args = parser.parse_args(arguments)
    try:
        report = write_fixture(args.match_folder, args.name)
    except NoFixtureError as exc:
        print_line(str(exc), error=True)
        return EXIT_NOT_WRITTEN
    print_line(report)
    return EXIT_WRITTEN


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
