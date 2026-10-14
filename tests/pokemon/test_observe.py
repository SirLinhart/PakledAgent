"""The observation reader: a key's value or its documented default, and the key names counted as degraded."""

from __future__ import annotations

import pytest

from ucla_agent.games.pokemon.observe import Clock, Reader, UnexpectedState, read_clock


def test_a_key_of_the_expected_kind_is_read_and_nothing_is_degraded():
    reader = Reader()

    assert reader.value({"turn": 3}, "turn", int, 0) == 3
    assert reader.value({"status": None}, "status", (str, type(None)), "x") is None
    assert reader.degradations == 0 and reader.degraded_keys == []


@pytest.mark.parametrize(
    "source, kinds",
    [({}, int), ({"turn": "3"}, int), ({"turn": True}, int), ({"turn": None}, int), (["turn"], int), (None, int)],
    ids=["missing", "string", "bool for a number", "null", "not a mapping", "no source"],
)
def test_a_missing_or_ill_typed_key_gives_the_default_and_counts(source, kinds):
    reader = Reader()

    assert reader.value(source, "turn", kinds, 7) == 7
    assert reader.degradations == 1 and reader.degraded_keys == ["turn"]


def test_a_bool_passes_where_a_bool_is_expected():
    reader = Reader()

    assert reader.value({"fainted": False}, "fainted", bool, True) is False
    assert reader.degradations == 0


def test_a_key_missing_from_many_pokemon_counts_once_and_names_are_sorted():
    reader = Reader()
    pokemon = [{"species": "incineroar"}, {"species": "rillaboom"}, {"species": "amoonguss"}]

    for entry in pokemon:
        reader.value(entry, "boosts", dict, {})
    reader.value({}, "weather", dict, {})

    assert reader.degradations == 2 and reader.degraded_keys == ["boosts", "weather"]


def test_a_noted_key_counts_like_a_missing_one():
    reader = Reader()

    reader.note("targets")
    reader.note("targets")

    assert reader.degraded_keys == ["targets"]


def test_unexpected_state_is_an_exception_with_its_message():
    with pytest.raises(UnexpectedState, match="no slots"):
        raise UnexpectedState("the template has no slots")


def test_an_optional_key_left_out_or_null_is_none_and_no_degradation():
    reader = Reader()

    assert reader.optional({}, "clock", dict) is None and reader.optional({"clock": None}, "clock", dict) is None
    assert reader.optional({"clock": {"a": 1}}, "clock", dict) == {"a": 1}
    assert reader.degradations == 0


def test_an_optional_key_of_another_kind_is_none_and_counts():
    reader = Reader()

    assert reader.optional({"clock": "soon"}, "clock", dict) is None
    assert reader.degraded_keys == ["clock"]


def test_the_clock_is_read_from_the_observation_without_a_degradation():
    reader = Reader()

    clock = read_clock({"clock": {"decision_seconds_left": 41, "bank_seconds_left": 312.5}}, reader)

    assert clock == Clock(decision_seconds_left=41, bank_seconds_left=312.5)
    assert reader.degradations == 0


@pytest.mark.parametrize("observation", [{}, {"clock": None}], ids=["left out", "null"])
def test_a_clock_not_yet_reported_is_none_and_no_degradation(observation):
    reader = Reader()

    assert read_clock(observation, reader) is None
    assert reader.degradations == 0


@pytest.mark.parametrize(
    "clock, degraded",
    [
        ("41s", ["clock"]),
        ([41, 312], ["clock"]),
        ({"decision_seconds_left": 41}, ["bank_seconds_left"]),
        ({"decision_seconds_left": True, "bank_seconds_left": 312}, ["decision_seconds_left"]),
        ({"decision_seconds_left": "41", "bank_seconds_left": "312"}, ["bank_seconds_left", "decision_seconds_left"]),
    ],
    ids=["a string", "a list", "no bank", "a bool for seconds", "strings for seconds"],
)
def test_an_unusable_clock_is_none_and_counts_its_keys(clock, degraded):
    reader = Reader()

    assert read_clock({"clock": clock}, reader) is None
    assert reader.degraded_keys == degraded
