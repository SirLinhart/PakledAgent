"""Fixtures of the feed's tests: match folders written the way the agent writes them."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

FEED_TESTS_FOLDER = Path(__file__).resolve().parent
# The record builders (`feed_records`) are test code the feed's test files import by name. Pytest's importlib mode
# puts no test folder on the import path, so this folder is added here.
if str(FEED_TESTS_FOLDER) not in sys.path:
    sys.path.insert(0, str(FEED_TESTS_FOLDER))

from feed_records import STARTED_AT, event_line, meta  # noqa: E402 - importable only once the folder is on the path


@pytest.fixture
def write_match(tmp_path: Path) -> Callable[..., Path]:
    """Writes a match folder under `<tmp>/runs/<day>/<name>/`: the attempts' events files and meta files.

    `attempts` are lists of (kind, data) pairs, one list per attempt, each
    numbered from seq 0 as the record writer does. Gives the folder.
    """

    def write(
        attempts: list[list[tuple[str, dict[str, Any]]]],
        *,
        name: str = "game1-sidestep-1__Multi0",
        day: str = "20261010",
        game_type: str = "red_alert",
        started_at: str = STARTED_AT,
        runs: Path | None = None,
    ) -> Path:
        folder = (runs or tmp_path / "runs") / day / name
        folder.mkdir(parents=True)
        for number, events in enumerate(attempts, start=1):
            suffix = "" if number == 1 else f".{number}"
            lines = "".join(event_line(seq, kind, data) for seq, (kind, data) in enumerate(events))
            (folder / f"events{suffix}.jsonl").write_bytes(lines.encode("utf-8"))
            attempt_meta = meta(game_type, started_at=started_at, attempt=number)
            (folder / f"meta{suffix}.json").write_bytes(json.dumps(attempt_meta).encode("utf-8"))
        return folder

    return write
