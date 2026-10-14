"""Compare a captured game reference with the agent's fallback item table.

    python scripts/diff_rules.py <captured file>

Run it from the repository root. The captured file is one that
`scripts/capture_rules.py` wrote (`config/redalert/captured/rules-<yyyymmdd>.json`):
the platform's game reference as a match received it. The tool prints one line
for every item whose cost, power, prerequisites or hit points differ between
that file's rules and `config/redalert/items.toml`, giving both values of each
field that differs, and one line for every item that only one of the two
holds. It changes nothing: a person reads the lines and decides what to
correct in `items.toml`.

In a match the platform's values always win over the file's, so a difference
matters only for matches that arrive without the reference.

Exit code 0: nothing differs. 1: at least one item differs. 2: the captured
file or `items.toml` cannot be read, or the command line is wrong.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ucla_agent.core.config import ConfigError
from ucla_agent.core.console import print_line
from ucla_agent.core.paths import repository_root
from ucla_agent.games.redalert.items import ITEMS_FILE, Item, ItemTable

EXIT_SAME = 0
EXIT_DIFFERENT = 1
EXIT_UNREADABLE = 2
# The facts that are compared, in the order they are reported.
COMPARED_FACTS = ("cost", "power", "prerequisites", "hp")
CAPTURED_LABEL = "captured"
FALLBACK_LABEL = ITEMS_FILE.name
NOT_GIVEN = "not given"
_FILE_ENCODING = "utf-8"


class UnreadableRulesError(Exception):
    """A file to compare cannot be read as what it should be; the message says why."""


def captured_table(path: Path) -> ItemTable:
    """The item facts a captured game reference states. Raises `UnreadableRulesError` when it is not such a file."""
    try:
        game_config = json.loads(path.read_text(encoding=_FILE_ENCODING))
    except (OSError, ValueError) as exc:
        raise UnreadableRulesError(f"{path} cannot be read as a captured game reference: {exc}") from exc
    if not isinstance(game_config, dict) or not isinstance(game_config.get("rules"), dict):
        raise UnreadableRulesError(f"{path} is not a captured game reference: it has no rules object")
    return ItemTable(()).with_game_config(game_config)


def fallback_table() -> ItemTable:
    """The item facts of `config/redalert/items.toml` under the repository root."""
    try:
        return ItemTable.from_file(repository_root())
    except ConfigError as exc:
        raise UnreadableRulesError(str(exc)) from exc


def differences(captured: ItemTable, fallback: ItemTable) -> list[str]:
    """One line for each item that the two tables do not agree on, in name order."""
    names = sorted({item.name for item in captured} | {item.name for item in fallback})
    lines = (_difference(name, captured.get(name), fallback.get(name)) for name in names)
    return [line for line in lines if line is not None]


def _difference(name: str, captured: Item | None, fallback: Item | None) -> str | None:
    """The line for one item, or None when both tables hold it with the same compared facts."""
    if captured is None:
        return f"{name}: only in {FALLBACK_LABEL}"
    if fallback is None:
        return f"{name}: only in the {CAPTURED_LABEL} file"
    differing = [
        f"{fact} {_shown(captured, fact)} ({CAPTURED_LABEL}) / {_shown(fallback, fact)} ({FALLBACK_LABEL})"
        for fact in COMPARED_FACTS
        if _comparable(captured, fact) != _comparable(fallback, fact)
    ]
    return f"{name}: " + "; ".join(differing) if differing else None


def _comparable(item: Item, fact: str) -> Any:
    """A fact in the form two items are compared in: the order of prerequisites says nothing."""
    value = getattr(item, fact)
    return sorted(value) if isinstance(value, tuple) else value


def _shown(item: Item, fact: str) -> str:
    value = _comparable(item, fact)
    if value is None:
        return NOT_GIVEN
    return "[" + ", ".join(value) + "]" if isinstance(value, list) else str(value)


def main(arguments: list[str]) -> int:
    """Print how the captured file given on the command line differs from items.toml; returns the exit code."""
    parser = argparse.ArgumentParser(prog="python scripts/diff_rules.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("captured_file", type=Path, help="a file written by scripts/capture_rules.py")
    args = parser.parse_args(arguments)
    try:
        lines = differences(captured_table(args.captured_file), fallback_table())
    except UnreadableRulesError as exc:
        print_line(str(exc), error=True)
        return EXIT_UNREADABLE
    for line in lines:
        print_line(line)
    if not lines:
        print_line(f"No item differs between {args.captured_file} and {ITEMS_FILE.as_posix()}.")
    return EXIT_DIFFERENT if lines else EXIT_SAME


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
