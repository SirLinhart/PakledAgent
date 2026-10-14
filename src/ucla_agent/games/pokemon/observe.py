"""Reading a Pokémon observation key by key, each with a documented default, and counting what had to be defaulted.

The platform's guide names the keys each phase's observation carries, and
the practice harness's fixtures are derived from that guide without having
been checked against the platform itself. So the agent never trusts a key: it
reads each one through a `Reader`, made once per decision, which hands back
the value when it is there and of the kind the guide describes, and the
default the reading code documents otherwise. Each key name that had to be
defaulted counts once per decision, however many Pokémon lack it, and the
count goes into that decision's `pokemon_decision` event as its parse
degradations: a real match whose record shows none read everything as the
guide says.

A state that cannot be read at all (an observation that is not an object, a
template with no slots) is not a degradation: the policy raises
`UnexpectedState`, and the agent plays the template-only fallback.

A key the guide says may be left out, such as the `clock` of a decision
Showdown has not reported the time for yet, is read with `Reader.optional`:
its absence is no degradation, a value of the wrong kind still is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, TypeVar

_Value = TypeVar("_Value")
_MISSING = object()
CLOCK_KEY = "clock"
DECISION_SECONDS_KEY = "decision_seconds_left"
BANK_SECONDS_KEY = "bank_seconds_left"
_SECONDS = (int, float)


class UnexpectedState(Exception):
    """The state is not one the guide describes for its phase: the agent plays the template-only fallback."""


class Reader:
    """Reads the keys of one decision's observation and template, and counts the key names it had to default."""

    def __init__(self) -> None:
        self._degraded: set[str] = set()

    def value(self, source: Any, key: str, kinds: type | tuple[type, ...], default: _Value) -> Any | _Value:
        """`source[key]` when `source` is a mapping holding a value of one of `kinds`; else `default`, and the key
        counts as degraded.

        A bool never passes for a number: `true` is no HP count or turn.
        """
        found = source.get(key, _MISSING) if isinstance(source, Mapping) else _MISSING
        if found is _MISSING or not _is_kind(found, kinds):
            self._degraded.add(key)
            return default
        return found

    def optional(self, source: Mapping[str, Any], key: str, kinds: type | tuple[type, ...]) -> Any | None:
        """`source[key]` when it holds a value of one of `kinds`; None when the key is left out or null, which is no
        degradation; None, and the key counts as degraded, when it holds a value of another kind."""
        found = source.get(key)
        if found is None:
            return None
        if not _is_kind(found, kinds):
            self._degraded.add(key)
            return None
        return found

    def note(self, key: str) -> None:
        """Count `key` as degraded: its value was there but could not be used as the guide describes it."""
        self._degraded.add(key)

    @property
    def degradations(self) -> int:
        """How many key names had to be defaulted in this decision."""
        return len(self._degraded)

    @property
    def degraded_keys(self) -> list[str]:
        """The names of the keys that had to be defaulted, sorted."""
        return sorted(self._degraded)


@dataclass(frozen=True)
class Clock:
    """The time our current decision has left, in seconds, counted when the state was read: until the decision is
    taken out of our hands, and in our bank for the whole battle."""

    decision_seconds_left: float
    bank_seconds_left: float


def read_clock(observation: Mapping[str, Any], reader: Reader) -> Clock | None:
    """The observation's `clock`, or None when it is left out, null or unusable.

    The platform leaves the clock out until Showdown has reported the time for the decision, so its absence is no
    degradation. A clock that is not an object, or lacks a number of seconds for either field, counts as degraded:
    the decision is then timed as one without a clock.
    """
    clock = reader.optional(observation, CLOCK_KEY, Mapping)
    if clock is None:
        return None
    decision_seconds = reader.value(clock, DECISION_SECONDS_KEY, _SECONDS, None)
    bank_seconds = reader.value(clock, BANK_SECONDS_KEY, _SECONDS, None)
    if decision_seconds is None or bank_seconds is None:
        return None
    return Clock(decision_seconds, bank_seconds)


def _is_kind(value: Any, kinds: type | tuple[type, ...]) -> bool:
    allowed = kinds if isinstance(kinds, tuple) else (kinds,)
    if isinstance(value, bool) and bool not in allowed:
        return False
    return isinstance(value, allowed)
