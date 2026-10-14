"""The glossary of Red Alert unit and building types that a model reads with the plan choice briefing.

A small model knows Red Alert by name but not what each of its types does, so
the briefing explains every type it names with a line drawn from the tracked
file `config/redalert/glossary.toml`: the type's name, its side and kind, a
short sentence on its role, and what it beats and what beats it. The cost on
the line is the item table's, so it is what the agent itself weighs the type
at, the platform's rules included.

The file is data, not code, and is read strictly like every tracked file: an
entry that lacks a field or has one nobody reads, a side or kind outside the
words allowed, or a text that is empty, runs over one line or is longer than
its limit is a `ConfigError` that names the file, the entry and the key. That
every type of the item table has an entry is checked by the tests, not here:
the agent plays on with a type it cannot explain, which the briefing then
names without a glossary line.

Reading the file is the only I/O here and happens in `from_file`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

from ucla_agent.core.config_tables import Table, read_tables
from ucla_agent.games.redalert.items import ItemTable

GLOSSARY_FILE = Path("config") / "redalert" / "glossary.toml"
ALLIES_SIDE = "Allies"
SOVIETS_SIDE = "Soviets"
BOTH_SIDES = "both"
SIDES = (ALLIES_SIDE, SOVIETS_SIDE, BOTH_SIDES)
KINDS = ("infantry", "vehicle", "aircraft", "ship", "building", "defense")
# A small model reads a short line better than a paragraph: the role is one short sentence, a matchup a few words.
MAX_ROLE_CHARS = 100
MAX_MATCHUP_CHARS = 70
# How a line names the side of a type built by one side only.
_SIDE_ADJECTIVES = {ALLIES_SIDE: "Allied", SOVIETS_SIDE: "Soviet"}


@dataclass(frozen=True)
class Entry:
    """What one type is, in plain words."""

    name: str
    side: str
    kind: str
    role: str
    strong_vs: str
    weak_vs: str

    def line(self, code: str, cost: int | None) -> str:
        """The glossary line of the type: name, code, side and kind, cost when known, role and matchups."""
        side_and_kind = (
            f"{self.kind} of both sides" if self.side == BOTH_SIDES else f"{_SIDE_ADJECTIVES[self.side]} {self.kind}"
        )
        priced = "" if cost is None else f", {cost:,}"
        matchups = f"strong vs {self.strong_vs}; weak vs {self.weak_vs}"
        return f"{self.name} ({code}), {side_and_kind}{priced}: {self.role}; {matchups}"


class Glossary:
    """The glossary entries by type code. See the module's note."""

    def __init__(self, entries: Mapping[str, Entry]) -> None:
        self._entries = dict(entries)

    @classmethod
    def from_file(cls, root: Path) -> Glossary:
        """The glossary `config/redalert/glossary.toml` under `root` holds. Raises `ConfigError` when it is unusable."""
        path = root / GLOSSARY_FILE
        return cls({code: _entry(Table(path, code, values)) for code, values in read_tables(path).items()})

    def get(self, code: str) -> Entry | None:
        """The entry of a type code; None for a type the glossary does not explain."""
        return self._entries.get(code)

    def line(self, code: str, items: ItemTable) -> str | None:
        """The glossary line of a type, priced from the item table; None for a type the glossary does not explain."""
        entry = self._entries.get(code)
        if entry is None:
            return None
        facts = items.get(code)
        return entry.line(code, None if facts is None else facts.cost)

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


def _entry(table: Table) -> Entry:
    """One entry of the file, every key of its table accounted for."""
    entry = Entry(
        name=_short_text(table, "name", MAX_MATCHUP_CHARS),
        side=table.one_of("side", SIDES),
        kind=table.one_of("kind", KINDS),
        role=_short_text(table, "role", MAX_ROLE_CHARS),
        strong_vs=_short_text(table, "strong_vs", MAX_MATCHUP_CHARS),
        weak_vs=_short_text(table, "weak_vs", MAX_MATCHUP_CHARS),
    )
    table.reject_unread_keys()
    return entry


def _short_text(table: Table, key: str, max_chars: int) -> str:
    """A text of one line, not empty and at most `max_chars` long."""
    value = table.text(key)
    if not value.strip() or "\n" in value or len(value) > max_chars:
        raise table.error(key, f"one line of 1 to {max_chars} characters")
    return value
