"""Strict reading of a tracked configuration file, table by table.

The files under `config/` pin how the agent plays, and a run records their
hashes. A setting that is silently ignored (a misspelled key) or silently
replaced (a value of the wrong kind) would make the recorded file say
something the agent did not do. So a table is read key by key through
`Table`: a missing key, a value of the wrong kind and a key nobody asked for
are each a `ConfigError` that names the file, the table and the key.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ucla_agent.core.config import ConfigError

# Marks a key that has no default: the table must have it.
REQUIRED: Any = object()
HTTPS_SCHEME = "https://"


def read_tables(path: Path) -> dict[str, Any]:
    """Parse a TOML file and return its tables. Raises `ConfigError` when it is missing or not valid TOML."""
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"{path} was not found. Start the agent from the repository root.") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc


def reject_unknown_keys(path: Path, where: str, values: dict[str, Any], known: set[str]) -> None:
    """Raise `ConfigError` when `values` holds a key that is not in `known`; `where` names the place in the file."""
    unknown = sorted(set(values) - known)
    if unknown:
        raise ConfigError(f"{path}: {where} has no setting named {', '.join(unknown)}.")


class Table:
    """One table of a configuration file, read key by key.

    A getter without a default is for a key the table must have. A default of
    None is for an option that may be left out, and comes back as None.
    """

    def __init__(self, path: Path, name: str, values: Any) -> None:
        """`values` is what the file holds under `name`. Raises `ConfigError` when that is not a table."""
        if not isinstance(values, dict):
            raise ConfigError(f"{path} must have a [{name}] table.")
        self.name = name
        self._path = path
        self._values = values
        self._read: set[str] = set()

    def keys(self) -> list[str]:
        """The table's keys in file order."""
        return list(self._values)

    def subtable(self, key: str, *, optional: bool = False) -> Table:
        """The table held under `key`. A missing one is an error, or an empty table when it is `optional`."""
        self._read.add(key)
        values = self._values.get(key, {}) if optional else self._values.get(key)
        return Table(self._path, f"{self.name}.{key}", values)

    def error(self, key: str, expected: str) -> ConfigError:
        """The error for a key that is missing or holds something else than `expected`."""
        return ConfigError(f"{self._path}: {key} in [{self.name}] must be {expected}.")

    def reject_unread_keys(self) -> None:
        """Raise for a key no getter asked for: every key the table may hold is one that was read."""
        reject_unknown_keys(self._path, f"[{self.name}]", self._values, self._read)

    def text(self, key: str, default: Any = REQUIRED) -> str:
        """A string."""
        return self._get(key, default, "a string", lambda value: isinstance(value, str))

    def https_url(self, key: str) -> str:
        """A string that starts with `https://`: the address of a service a credential travels to, only over TLS."""
        url = self.text(key)
        if not url.startswith(HTTPS_SCHEME):
            raise self.error(key, f"a URL that starts with {HTTPS_SCHEME}")
        return url

    def boolean(self, key: str, default: Any = REQUIRED) -> bool:
        """`true` or `false`."""
        return self._get(key, default, "true or false", lambda value: isinstance(value, bool))

    def integer(
        self, key: str, default: Any = REQUIRED, *, minimum: float = -math.inf, maximum: float = math.inf
    ) -> int:
        """A whole number, within the bounds that are given."""
        kind = _WholeNumber(minimum, maximum)
        return self._get(key, default, kind.description(), kind.accepts)

    def integers(self, key: str, default: Any = REQUIRED, *, minimum: float = -math.inf) -> tuple[int, ...]:
        """A list of whole numbers, each at least `minimum`."""
        kind = _WholeNumber(minimum, math.inf)

        def is_list_of_whole_numbers(value: Any) -> bool:
            return isinstance(value, list) and all(kind.accepts(item) for item in value)

        expected = "a list of whole numbers" + kind.description().removeprefix("a whole number")
        return tuple(self._get(key, default, expected, is_list_of_whole_numbers))

    def number(self, key: str, default: Any = REQUIRED) -> float:
        """A number of zero or more, whole or not."""
        return self._get(key, default, "a number of at least 0", lambda value: _is_number(value) and value >= 0)

    def one_of(self, key: str, allowed: tuple[str, ...]) -> str:
        """One of the `allowed` strings."""
        expected = " or ".join(f'"{value}"' for value in allowed)
        return self._get(key, REQUIRED, expected, lambda value: value in allowed)

    def texts(self, key: str, default: Any = REQUIRED) -> tuple[str, ...]:
        """A list of strings."""

        def is_list_of_strings(value: Any) -> bool:
            return isinstance(value, list) and all(isinstance(item, str) for item in value)

        return tuple(self._get(key, default, "a list of strings", is_list_of_strings))

    def _get(self, key: str, default: Any, expected: str, accepts: Callable[[Any], bool]) -> Any:
        self._read.add(key)
        if key not in self._values:
            if default is REQUIRED:
                raise self.error(key, expected)
            return default
        value = self._values[key]
        if not accepts(value):
            raise self.error(key, expected)
        return value


@dataclass(frozen=True)
class _WholeNumber:
    """What a whole-number setting may hold: an integer within the bounds, either of which may be infinite."""

    minimum: float
    maximum: float

    def description(self) -> str:
        """The kind of value, as an error message names it."""
        text = "a whole number"
        if self.minimum != -math.inf:
            text += f" of at least {self.minimum}"
        if self.maximum != math.inf:
            text += f" and at most {self.maximum}"
        return text

    def accepts(self, value: Any) -> bool:
        """Whether `value` is a whole number within the bounds."""
        # bool is a kind of int in Python, and `true` is not a port or a context size.
        if isinstance(value, bool):
            return False
        return isinstance(value, int) and self.minimum <= value <= self.maximum


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
