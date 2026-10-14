"""Fixtures shared by the practice tests: the Pokémon observation fixtures and the shape they are compared by."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

POKEMON_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pokemon_practice"
# Objects whose keys are data (stat names, Pokémon names, condition names), not field names: their shape is the
# shape of their values.
FREE_KEY_FIELDS = frozenset(
    {"evs", "ivs", "base_stats", "stats", "boosts", "team", "opponent_team", "weather", "fields"}
    | {"side_conditions", "opponent_side_conditions", "target_legend"}
)


def shape_of(value: Any, field: str | None = None) -> Any:
    """What a JSON value looks like: an object's field names with the shapes of their values, a list's first
    element's shape, a scalar's kind. Objects in `FREE_KEY_FIELDS` keep only the shape of their first value."""
    if isinstance(value, dict):
        return _object_shape(value, field)
    if isinstance(value, list):
        return [shape_of(value[0], field)] if value else []
    return _scalar_shape(value)


def _object_shape(value: dict[str, Any], field: str | None) -> dict[str, Any]:
    if field not in FREE_KEY_FIELDS:
        return {key: shape_of(item, key) for key, item in value.items()}
    return {"*": shape_of(next(iter(value.values())))} if value else {}


def _scalar_shape(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    return "number" if isinstance(value, (int, float)) else type(value).__name__


@pytest.fixture(scope="session")
def pokemon_fixture() -> Callable[[str], dict[str, Any]]:
    """Loads one of the guide-derived Pokémon fixtures by file name."""

    def load(name: str) -> dict[str, Any]:
        return json.loads((POKEMON_FIXTURES / name).read_text(encoding="utf-8"))

    return load


@pytest.fixture(scope="session")
def json_shape() -> Callable[[Any], Any]:
    """`shape_of`, for comparing an observation with a fixture field by field."""
    return shape_of
