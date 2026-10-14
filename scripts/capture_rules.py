"""Store the game reference of a recorded Red Alert match as a tracked file.

    python scripts/capture_rules.py <match folder>

Run it from the repository root, after a real match. The match folder is the
record of one seat (`runs/<UTC yyyymmdd>/<session id>__<seat>`). The tool
reads the `game_config` event of the record, which holds the platform's game
reference object as the match received it (item rules, maps, match rules), and
writes that object to `config/redalert/captured/rules-<yyyymmdd>.json`, where
the date is the UTC date on which the event was recorded.

The captured file is reference material: it shows what the platform's rules
were on that day, and `scripts/diff_rules.py` compares it with the agent's own
fallback table, `config/redalert/items.toml`. The agent never reads it.

Exit code 0: the file was written. 2: the folder holds no record, the record
holds no `game_config` event, or the command line is wrong.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ucla_agent.core import metrics
from ucla_agent.core.console import print_line
from ucla_agent.core.paths import repository_root

EXIT_WRITTEN = 0
EXIT_NOT_WRITTEN = 2
CAPTURED_FOLDER = Path("config") / "redalert" / "captured"
GAME_CONFIG_EVENT = "game_config"
_FILE_NAME_FORMAT = "rules-%Y%m%d.json"
_JSON_INDENT = 2


class NoCaptureError(Exception):
    """The match folder gives nothing to capture; the message says why."""


def game_config_event(match_folder: Path) -> dict[str, Any]:
    """The first `game_config` event of a match record. Raises `NoCaptureError` when there is no record or no such event."""
    try:
        match = metrics.load_match(match_folder)
    except OSError as exc:
        raise NoCaptureError(f"{match_folder} holds no match record: {exc}") from exc
    event = next((event for event in match.events if event["kind"] == GAME_CONFIG_EVENT), None)
    if event is None:
        raise NoCaptureError(f"The record in {match_folder} holds no {GAME_CONFIG_EVENT} event")
    return event


def capture_path(recorded_at: Any) -> Path:
    """Where the capture of an event recorded at that time goes: named for its UTC date, under the repository root.

    Raises `NoCaptureError` when the event carries no time that can be read.
    """
    try:
        recorded = datetime.fromisoformat(recorded_at).astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise NoCaptureError(f"The {GAME_CONFIG_EVENT} event has no readable time ({recorded_at!r})") from exc
    return repository_root() / CAPTURED_FOLDER / recorded.strftime(_FILE_NAME_FORMAT)


def capture(match_folder: Path) -> Path:
    """Write the game reference of a match record to its capture file and return the file's path."""
    event = game_config_event(match_folder)
    path = capture_path(event.get("t"))
    metrics.write_output(path, json.dumps(event["data"], indent=_JSON_INDENT, sort_keys=True) + "\n")
    return path


def main(arguments: list[str]) -> int:
    """Capture the game reference of the match folder given on the command line; returns the exit code."""
    parser = argparse.ArgumentParser(prog="python scripts/capture_rules.py", description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "match_folder", type=Path, help="the record of one seat: runs/<UTC yyyymmdd>/<session id>__<seat>"
    )
    args = parser.parse_args(arguments)
    try:
        written = capture(args.match_folder)
    except NoCaptureError as exc:
        print_line(str(exc), error=True)
        return EXIT_NOT_WRITTEN
    print_line(f"Wrote {written}")
    return EXIT_WRITTEN


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
