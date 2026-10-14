"""Gen 9 Pokémon game data: species, moves, the type chart and natures.

The data is the tracked JSON under `config/pokemon/data/` that
`scripts/build_pokemon_data.py` writes. It belongs to the code's version, not
to a run's settings, so it is read from the project the package was loaded
from even when `UCLA_AGENT_ROOT` names another folder for records and
settings; it is read once per process, on the first lookup.

Every lookup takes a display name or a Showdown id (`to_id` turns the one into
the other), so the platform's ids and names both resolve. An unknown name
gives None, never an error.
"""

from __future__ import annotations

import functools
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ucla_agent.core import paths
from ucla_agent.core.config import ConfigError

DATA_FOLDER = Path("config") / "pokemon" / "data"
SPECIES_FILE = "species.json"
MOVES_FILE = "moves.json"
TYPECHART_FILE = "typechart.json"
NATURES_FILE = "natures.json"
TABLE_FILES = (SPECIES_FILE, MOVES_FILE, TYPECHART_FILE, NATURES_FILE)
LICENSE_FILE = "LICENSE-DATA.md"
LICENSE_NAME = "MIT License"
STATS = ("hp", "atk", "def", "spa", "spd", "spe")
NATURE_STATS = STATS[1:]
NOT_AN_ID_CHARACTER = re.compile(r"[^a-z0-9]")
ENCODING = "utf-8"


@dataclass(frozen=True)
class Species:
    """One species or forme: its types, base stats (hp, atk, def, spa, spd, spe), abilities and weight."""

    id: str
    name: str
    types: tuple[str, ...]
    base_stats: Mapping[str, int]
    abilities: tuple[str, ...]
    weight_kg: float


@dataclass(frozen=True)
class StatChange:
    """A change of stat stages a move makes, with a chance in percent; `who` is "user" or "target"."""

    who: str
    chance: int
    boosts: Mapping[str, int]


@dataclass(frozen=True)
class Move:
    """One move. `accuracy` is True for a move that never misses; `multihit` is a count, a (min, max) pair or None.

    `drain` and `recoil` are fractions of the damage dealt as (numerator,
    denominator), or None; `flags` holds those of contact, protect, punch, bite
    and sound that the move has; `has_secondary` says whether it has a secondary
    effect, which Sheer Force removes in exchange for more power.
    """

    id: str
    name: str
    type: str
    category: str
    base_power: int
    accuracy: int | bool
    priority: int
    target: str
    flags: frozenset[str]
    multihit: int | tuple[int, int] | None
    drain: tuple[int, int] | None
    recoil: tuple[int, int] | None
    stat_changes: tuple[StatChange, ...]
    has_secondary: bool
    will_crit: bool


@dataclass(frozen=True)
class GameData:
    """The four tables, each keyed by Showdown id."""

    species: Mapping[str, Species]
    moves: Mapping[str, Move]
    type_chart: Mapping[str, Mapping[str, float]]
    natures: Mapping[str, Mapping[str, float]]


def to_id(name: str) -> str:
    """Showdown's id of a name: lowercase, letters and digits only ("Urshifu-Rapid-Strike" -> "urshifurapidstrike")."""
    return NOT_AN_ID_CHARACTER.sub("", name.lower())


def _pair(value: list[int] | None) -> tuple[int, int] | None:
    return (value[0], value[1]) if value is not None else None


def _species(species_id: str, record: dict[str, Any]) -> Species:
    return Species(
        id=species_id,
        name=record["name"],
        types=tuple(record["types"]),
        base_stats={stat: int(record["base_stats"][stat]) for stat in STATS},
        abilities=tuple(record["abilities"]),
        weight_kg=record["weight_kg"],
    )


def _move(move_id: str, record: dict[str, Any]) -> Move:
    multihit = record["multihit"]
    return Move(
        id=move_id,
        name=record["name"],
        type=record["type"],
        category=record["category"],
        base_power=record["base_power"],
        accuracy=record["accuracy"],
        priority=record["priority"],
        target=record["target"],
        flags=frozenset(record["flags"]),
        multihit=_pair(multihit) if isinstance(multihit, list) else multihit,
        drain=_pair(record["drain"]),
        recoil=_pair(record["recoil"]),
        stat_changes=tuple(
            StatChange(change["who"], change["chance"], dict(change["boosts"])) for change in record["stat_changes"]
        ),
        has_secondary=record["has_secondary"],
        will_crit=record["will_crit"],
    )


def _type_row(_type_id: str, record: dict[str, Any]) -> Mapping[str, float]:
    return {defending: float(multiplier) for defending, multiplier in record.items()}


def _nature(_nature_id: str, record: dict[str, Any]) -> Mapping[str, float]:
    return {stat: float(record[stat]) for stat in NATURE_STATS}


_RECORD_MAKERS: dict[str, Callable[[str, dict[str, Any]], Any]] = {
    SPECIES_FILE: _species,
    MOVES_FILE: _move,
    TYPECHART_FILE: _type_row,
    NATURES_FILE: _nature,
}


def read_table(path: Path) -> dict[str, Any]:
    """One of the four tables, by its file name, as records keyed by id. Raises `ConfigError` naming the file."""
    try:
        records = json.loads(path.read_text(encoding=ENCODING))
    except FileNotFoundError as exc:
        raise ConfigError(f"{path} was not found: rebuild it with scripts/build_pokemon_data.py.") from exc
    except ValueError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    make_record = _RECORD_MAKERS[path.name]
    try:
        return {key: make_record(key, record) for key, record in records.items()}
    except (AttributeError, KeyError, TypeError, IndexError, ValueError) as exc:
        raise ConfigError(f"{path} has a record the agent cannot read ({type(exc).__name__}: {exc}).") from exc


def read_license_note(path: Path) -> str:
    """The license note beside the tables. Raises `ConfigError` when it is missing or names no MIT license."""
    try:
        note = path.read_text(encoding=ENCODING)
    except FileNotFoundError as exc:
        raise ConfigError(f"{path} was not found: rebuild it with scripts/build_pokemon_data.py.") from exc
    if LICENSE_NAME not in note:
        raise ConfigError(f"{path} names no {LICENSE_NAME}: the data's source license has to be noted.")
    return note


def load(folder: Path) -> GameData:
    """Read the four tables from a data folder. Raises `ConfigError` naming the file that is missing or unreadable."""
    return GameData(
        species=read_table(folder / SPECIES_FILE),
        moves=read_table(folder / MOVES_FILE),
        type_chart=read_table(folder / TYPECHART_FILE),
        natures=read_table(folder / NATURES_FILE),
    )


@functools.cache
def shipped() -> GameData:
    """The data shipped with the code, read once per process from the project the package was loaded from."""
    project = paths.project_holding(paths.PACKAGE_DIR) or paths.repository_root()
    return load(project / DATA_FOLDER)


def species(name: str) -> Species | None:
    """The species or forme of this name or id; None when there is none."""
    return shipped().species.get(to_id(name))


def move(name: str) -> Move | None:
    """The move of this name or id; None when there is none."""
    return shipped().moves.get(to_id(name))


def nature(name: str) -> Mapping[str, float] | None:
    """A nature's multiplier on atk, def, spa, spd and spe; None for an unknown nature."""
    return shipped().natures.get(to_id(name))


def type_multiplier(attack_type: str, defender_types: Iterable[str]) -> float | None:
    """How effective a move of `attack_type` is on a defender of these types (0, 0.25 ... 4); None for an unknown type."""
    against = shipped().type_chart.get(to_id(attack_type))
    if against is None:
        return None
    multipliers = [against.get(to_id(defender_type)) for defender_type in defender_types]
    return None if None in multipliers else math.prod(multipliers, start=1.0)
