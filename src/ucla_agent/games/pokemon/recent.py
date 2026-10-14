"""What both sides did on the last turns of a doubles battle, read from the observation's Showdown protocol log, as
the "Recent turns" section of the model's battle briefing.

`recent_turns(log, our_side, turns)` gives one line for each of the last
`turns` completed turns, oldest first. A turn is the protocol lines from its
`|turn|N` marker to the next one, and its line lists every action in order:

- a move, with each Pokémon it touched and what the log shows there: the
  effectiveness (`super effective`, `resisted`, `no effect`), a critical hit,
  `protected`, `missed`, and the HP percent change (`100→16%`); `(failed)`
  for a move that failed; a move at its own user names no target;
- a switch in and a faint;
- an HP change from an item, an ability or a status, with its source
  (`theirs Archaludon 16→22% from Leftovers`);
- weather, terrain and room changes, and side conditions starting or ending.

Sides are `ours` and `theirs`, as the rest of the briefing names them. HP is
in percent of the Pokémon's maximum, as the log gives it (ours exact, theirs
out of 100), followed from every switch, damage and heal line since the
battle began; a change from an HP not seen yet shows the new HP alone.

The section is at most `MAX_SECTION_CHARS` long: the oldest actions are left
out first, and the turn that lost some says so. Lines the reading does not
know (chat, timers, stat stages, abilities) are passed over. The log comes
from outside the agent, so nothing here raises on it: a log that is not a
list of strings, or that cannot be read, gives None, which the caller counts
as a parse degradation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

SECTION_TITLE = "Recent turns (oldest first):"
MAX_SECTION_CHARS = 1200
CUT_NOTE = "(earlier actions left out) "
OURS, THEIRS = "ours", "theirs"
PERCENT = 100.0
# A Pokémon as the log names it: `p2a: Landorus` (side, board position, name) or `p2: Landorus`.
IDENT = re.compile(r"^(p\d)[a-d]?: (.+)$")
SIDE_KEY = re.compile(r"^(p\d):")
OTHER_SIDE = {"p1": "p2", "p2": "p1"}
RESULT_WORDS = {
    "-supereffective": "super effective",
    "-resisted": "resisted",
    "-immune": "no effect",
    "-crit": "critical hit",
}
PROTECTING_EFFECTS = frozenset(
    {"Protect", "Detect", "Spiky Shield", "King's Shield", "Baneful Bunker", "Silk Trap", "Burning Bulwark", "Obstruct"}
)
SWITCHES = frozenset({"switch", "drag", "replace"})
EFFECT_PREFIX = "move: "
FROM_TAG = "[from] "
UPKEEP_TAG = "[upkeep]"
NO_WEATHER = "none"


class _Unreadable(Exception):
    """A log line that holds no Pokémon, HP or turn where its kind needs one."""


@dataclass
class _Move:
    """One move as the turn's line shows it: its user, the move and each Pokémon it touched with the results there."""

    user: str
    move: str
    targets: dict[str, list[str]] = field(default_factory=dict)
    failed: bool = False

    def text(self) -> str:
        touched = [
            f"{label} ({', '.join(results)})" if results else label
            for label, results in self.targets.items()
            if label != self.user or results
        ]
        aimed = f" on {', '.join(touched)}" if touched else ""
        return f"{self.user} used {self.move}{aimed}{' (failed)' if self.failed else ''}"


@dataclass
class _Reading:
    """The log read so far: each Pokémon's HP percent, the turns' actions by number, and the move still open."""

    our_side: str
    hp: dict[str, float] = field(default_factory=dict)
    turns: dict[int, list[Any]] = field(default_factory=lambda: {0: []})
    current: int = 0
    move: _Move | None = None

    def label(self, ident: str) -> str:
        side, name = _ident(ident)
        return f"{OURS if side == self.our_side else THEIRS} {name}"

    def add(self, action: Any) -> None:
        self.turns[self.current].append(action)

    def hp_change(self, ident: str, condition: str) -> str:
        """The HP change a line shows, `before→after%`, and the HP it leaves; `after%` when none was seen before."""
        side, name = _ident(ident)
        key = f"{side}: {name}"
        before, after = self.hp.get(key), _hp_percent(condition)
        self.hp[key] = after
        return f"{after:.0f}%" if before is None else f"{before:.0f}→{after:.0f}%"


def our_side(observation: Mapping[str, Any]) -> str | None:
    """Our side in the log (`p1` or `p2`), from the keys of the observation's `team`, else the other side of its
    `opponent_team`; None when neither says."""
    for key, mine in (("team", True), ("opponent_team", False)):
        team = observation.get(key)
        sides = [SIDE_KEY.match(name) for name in team] if isinstance(team, Mapping) else []
        found = next((match.group(1) for match in sides if match is not None), None)
        if found is not None:
            return found if mine else OTHER_SIDE.get(found)
    return None


def recent_turns(log: Any, side: str | None, turns: int, max_chars: int = MAX_SECTION_CHARS) -> str | None:
    """The "Recent turns" section of the last `turns` completed turns of `log` (see the module note), `side` being
    ours; "" when no completed turn shows an action; None when the log or our side cannot be read."""
    if side is None or not isinstance(log, list) or not all(isinstance(line, str) for line in log):
        return None
    try:
        reading = _read(log, side)
    except (_Unreadable, ValueError, IndexError):
        return None
    completed = [number for number in sorted(reading.turns) if 0 < number < reading.current]
    shown = [(number, [_text(action) for action in reading.turns[number]]) for number in completed[-turns:]]
    return _fitted([(number, actions) for number, actions in shown if actions], max_chars)


def _read(log: Sequence[str], side: str) -> _Reading:
    reading = _Reading(side)
    for line in log:
        parts = line.split("|")[1:]
        if parts:
            _READERS.get(parts[0], _pass_over)(reading, parts[1:])
    return reading


def _text(action: Any) -> str:
    return action.text() if isinstance(action, _Move) else action


def _fitted(turns: list[tuple[int, list[str]]], max_chars: int) -> str:
    """The section, its oldest actions left out until it is at most `max_chars` long."""
    actions = [(number, action) for number, listed in turns for action in listed]
    cut = 0
    # Leaving every action out gives "", which fits any cap, so the loop ends.
    while len(text := _section(actions[cut:], cut > 0)) > max_chars:
        cut += 1
    return text


def _section(actions: list[tuple[int, str]], cut: bool) -> str:
    if not actions:
        return ""
    lines: dict[int, list[str]] = {}
    for number, action in actions:
        lines.setdefault(number, []).append(action)
    shown = [
        f"Turn {number}: {CUT_NOTE if cut and index == 0 else ''}{'; '.join(listed)}."
        for index, (number, listed) in enumerate(lines.items())
    ]
    return "\n".join([SECTION_TITLE, *shown])


# -- the protocol lines ----------------------------------------------------------------------------


def _ident(ident: str) -> tuple[str, str]:
    match = IDENT.match(ident)
    if match is None:
        raise _Unreadable(f"no Pokémon in {ident!r}")
    return match.group(1), match.group(2)


def _hp_percent(condition: str) -> float:
    """`58/146`, `16/100 par` or `0 fnt` as a percent of the maximum."""
    shown = condition.split(" ")[0]
    if "/" not in shown:
        return float(shown)
    current, maximum = shown.split("/")
    return float(current) / float(maximum) * PERCENT


def _source(parts: Sequence[str]) -> str | None:
    """The source a `[from] item: Leftovers` tag names (`Leftovers`), or None."""
    tag = next((part for part in parts if part.startswith(FROM_TAG)), None)
    return None if tag is None else tag[len(FROM_TAG) :].split(": ")[-1]


def _effect(name: str) -> str:
    return name[len(EFFECT_PREFIX) :] if name.startswith(EFFECT_PREFIX) else name


def _close(reading: _Reading) -> None:
    reading.move = None


def _turn(reading: _Reading, parts: Sequence[str]) -> None:
    _close(reading)
    reading.current = int(parts[0])
    reading.turns.setdefault(reading.current, [])


def _move(reading: _Reading, parts: Sequence[str]) -> None:
    user = reading.label(parts[0])
    move = _Move(user, parts[1])
    target = parts[2] if len(parts) > 2 else ""
    if IDENT.match(target):
        move.targets[reading.label(target)] = []
    reading.move = move
    reading.add(move)


def _result(reading: _Reading, ident: str, result: str) -> None:
    if reading.move is not None:
        reading.move.targets.setdefault(reading.label(ident), []).append(result)


def _effectiveness(kind: str):
    def read(reading: _Reading, parts: Sequence[str]) -> None:
        _result(reading, parts[0], RESULT_WORDS[kind])

    return read


def _activate(reading: _Reading, parts: Sequence[str]) -> None:
    if len(parts) > 1 and _effect(parts[1]) in PROTECTING_EFFECTS:
        _result(reading, parts[0], "protected")


def _miss(reading: _Reading, parts: Sequence[str]) -> None:
    if len(parts) > 1 and parts[1]:
        _result(reading, parts[1], "missed")


def _fail(reading: _Reading, _parts: Sequence[str]) -> None:
    if reading.move is not None:
        reading.move.failed = True


def _damage(reading: _Reading, parts: Sequence[str]) -> None:
    change, source = reading.hp_change(parts[0], parts[1]), _source(parts[2:])
    if source is None and reading.move is not None:
        _result(reading, parts[0], change)
    else:
        reading.add(f"{reading.label(parts[0])} {change}{f' from {source}' if source else ''}")


def _heal(reading: _Reading, parts: Sequence[str]) -> None:
    change, source = reading.hp_change(parts[0], parts[1]), _source(parts[2:])
    reading.add(f"{reading.label(parts[0])} {change}{f' from {source}' if source else ''}")


def _switch(reading: _Reading, parts: Sequence[str]) -> None:
    _close(reading)
    reading.hp_change(parts[0], parts[2])
    reading.add(f"{reading.label(parts[0])} switched in")


def _faint(reading: _Reading, parts: Sequence[str]) -> None:
    _close(reading)
    reading.add(f"{reading.label(parts[0])} fainted")


def _weather(reading: _Reading, parts: Sequence[str]) -> None:
    if UPKEEP_TAG not in parts:
        reading.add("weather ended" if parts[0] == NO_WEATHER else f"weather {parts[0]}")


def _field(word: str):
    def read(reading: _Reading, parts: Sequence[str]) -> None:
        reading.add(f"{_effect(parts[0])} {word}")

    return read


def _side(word: str):
    def read(reading: _Reading, parts: Sequence[str]) -> None:
        side = SIDE_KEY.match(parts[0])
        owner = OURS if side is not None and side.group(1) == reading.our_side else THEIRS
        reading.add(f"{owner} {_effect(parts[1])} {word}")

    return read


def _pass_over(_reading: _Reading, _parts: Sequence[str]) -> None:
    """A line the section does not show."""


_READERS = {
    "turn": _turn,
    "move": _move,
    "-supereffective": _effectiveness("-supereffective"),
    "-resisted": _effectiveness("-resisted"),
    "-immune": _effectiveness("-immune"),
    "-crit": _effectiveness("-crit"),
    "-activate": _activate,
    "-miss": _miss,
    "-fail": _fail,
    "-damage": _damage,
    "-heal": _heal,
    **{kind: _switch for kind in SWITCHES},
    "faint": _faint,
    "-weather": _weather,
    "-fieldstart": _field("started"),
    "-fieldend": _field("ended"),
    "-sidestart": _side("started"),
    "-sideend": _side("ended"),
    "upkeep": lambda reading, _parts: _close(reading),
}
