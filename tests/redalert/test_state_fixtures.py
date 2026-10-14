"""The committed single-state fixtures: what each one shows, and that all keep the shape of a real state."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from fake_game import FIRST_ACTOR_ID, FakeRedAlert

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_FOLDER = REPO_ROOT / "tests" / "fixtures"
STATES_FOLDER = FIXTURES_FOLDER / "redalert" / "states"
FIXTURE_NAMES = ("opening_mcv", "midgame_allies", "midgame_soviet", "base_under_attack", "wreck", "events_gap")
MAX_FIXTURE_BYTES = 4 * 1024 * 1024
POWER_PLANT_STEPS = 36
RIFLE_STEPS = 12
# The parts of a state that are objects with a fixed set of fields.
OBJECT_PARTS = ("pacing", "limits", "time", "map", "match_rules", "observation", "legal_actions", "last_orders")
OBSERVATION_OBJECT_PARTS = ("economy", "military", "map")
# The lists of an observation whose entries all have the same fields; a wreck carries `wreck` as well.
OBSERVATION_ENTRY_LISTS = ("units", "buildings", "production", "visible_enemies", "visible_enemy_buildings")
WRECK_FIELD = "wreck"
EVENT_FIELDS = {"seq", "tick", "kind"}


def load_state(name: str) -> dict:
    """The fixture state of that name."""
    return json.loads((STATES_FOLDER / f"{name}.json").read_text(encoding="utf-8"))


def _send(game: FakeRedAlert, *orders: dict) -> None:
    game.play_action({"type": "orders", "orders": list(orders)})


@pytest.fixture(scope="module")
def reference_state() -> dict:
    """A state of the fake game that has at least one entry in every list of its observation."""
    game = FakeRedAlert("england", "russia")
    _send(game, {"cmd": "deploy", "units": [FIRST_ACTOR_ID]})
    game.step()
    for item in ("powr", "tent"):
        _send(game, {"cmd": "build", "item": item})
        for _ in range(round(game.items[item].build_time_s * 5)):
            game.step()
        _send(game, {"cmd": "place", "item": item})
        game.step()
    _send(game, {"cmd": "train", "item": "e1", "count": 2})
    for _ in range(RIFLE_STEPS):
        game.step()
    game.place_enemy("e1", 26, 48)
    game.place_enemy("pbox", 27, 50)
    game.step()
    state = game.state()
    assert all(state["observation"][name] for name in OBSERVATION_ENTRY_LISTS)
    return state


@pytest.fixture(scope="module", params=FIXTURE_NAMES)
def fixture_state(request: pytest.FixtureRequest) -> dict:
    return load_state(request.param)


def _entry_fields(entries: list[dict]) -> set[frozenset[str]]:
    """The distinct field sets of a list's entries, the optional wreck mark left out."""
    return {frozenset(entry) - {WRECK_FIELD} for entry in entries}


# -- the shape -------------------------------------------------------------------------------------


def test_the_states_folder_holds_exactly_the_six_fixtures():
    assert sorted(path.name for path in STATES_FOLDER.iterdir()) == sorted(f"{name}.json" for name in FIXTURE_NAMES)


def test_a_fixture_has_the_fields_of_a_state(fixture_state, reference_state):
    assert set(fixture_state) == set(reference_state)
    for part in OBJECT_PARTS:
        if fixture_state[part] is not None:
            assert set(fixture_state[part]) == set(reference_state[part]), part


def test_a_fixture_s_observation_objects_have_the_fields_of_a_state_s(fixture_state, reference_state):
    for part in OBSERVATION_OBJECT_PARTS:
        assert set(fixture_state["observation"][part]) == set(reference_state["observation"][part]), part


def test_the_entries_of_a_fixture_s_observation_lists_have_the_fields_of_a_state_s(fixture_state, reference_state):
    for name in OBSERVATION_ENTRY_LISTS:
        (expected,) = _entry_fields(reference_state["observation"][name])
        assert _entry_fields(fixture_state["observation"][name]) <= {expected}, name


def test_a_fixture_s_events_are_numbered_and_dated(fixture_state):
    events = fixture_state["observation"]["events"]

    assert all(EVENT_FIELDS <= set(event) for event in events)
    assert [event["seq"] for event in events] == sorted(event["seq"] for event in events)
    assert len(events) <= 20


def test_a_fixture_s_clock_and_version_agree(fixture_state):
    tick = fixture_state["observation"]["tick"]

    assert fixture_state["state_version"] == tick
    assert fixture_state["time"]["game_seconds"] == pytest.approx(tick / 25)
    assert (fixture_state["phase"], fixture_state["is_terminal"]) == ("playing", False)


def test_no_tracked_fixture_file_exceeds_4_mib_and_all_end_lines_with_lf():
    files = [path for path in FIXTURES_FOLDER.rglob("*") if path.is_file()]

    assert len(files) >= len(FIXTURE_NAMES)
    for path in files:
        assert path.stat().st_size <= MAX_FIXTURE_BYTES, path
    for name in FIXTURE_NAMES:
        assert b"\r" not in (STATES_FOLDER / f"{name}.json").read_bytes(), name


# -- what each fixture shows -----------------------------------------------------------------------


def test_the_opening_state_is_the_first_state_with_one_mcv():
    state = load_state("opening_mcv")
    observation = state["observation"]

    assert (observation["tick"], observation["economy"]["cash"], observation["buildings"]) == (0, 5000, [])
    assert [unit["type"] for unit in observation["units"]] == ["mcv"]
    assert state["legal_actions"]["deploy"] == [observation["units"][0]["id"]]
    assert state["last_orders"] is None


def test_the_mid_game_allied_state_has_an_allied_base_an_army_and_a_building_to_place():
    state = load_state("midgame_allies")
    observation = state["observation"]
    buildings = [building["type"] for building in observation["buildings"]]

    assert (observation["faction"], observation["enemy_faction"]) == ("england", "russia")
    assert {"fact", "powr", "tent", "proc", "weap", "pbox"} <= set(buildings) and "barr" not in buildings
    assert len(observation["units"]) == 13 and {"e1", "e3", "medi", "jeep", "1tnk", "harv"} <= {
        unit["type"] for unit in observation["units"]
    }
    assert {job["queue"] for job in observation["production"]} == {"Building", "Defense", "Infantry", "Vehicle"}
    assert (
        [job["item"] for job in observation["production"] if job["ready"]]
        == ["pbox"]
        == state["legal_actions"]["place"]
    )
    assert [enemy["type"] for enemy in observation["visible_enemies"]] == ["e1", "ftrk"]
    assert [enemy["type"] for enemy in observation["visible_enemy_buildings"]] == ["ftur"]


def test_the_mid_game_soviet_state_has_a_soviet_base_and_an_enemy_scout_in_sight():
    state = load_state("midgame_soviet")
    observation = state["observation"]
    buildings = [building["type"] for building in observation["buildings"]]

    assert (observation["faction"], observation["enemy_faction"]) == ("russia", "england")
    assert {"fact", "powr", "barr", "proc", "weap", "ftur"} <= set(buildings) and "tent" not in buildings
    assert {"e1", "e2", "e3", "ftrk", "apc", "harv"} <= {unit["type"] for unit in observation["units"]}
    assert [(enemy["type"], enemy["hp"]) for enemy in observation["visible_enemies"]] == [("1tnk", 64)]
    assert [unit["act"] for unit in observation["units"]].count("attack") == 3
    assert {job["item"] for job in observation["production"]} == {"proc", "tsla", "ftrk"}


def test_the_base_under_attack_state_has_attackers_near_the_yard_and_reports_the_attack():
    state = load_state("base_under_attack")
    observation = state["observation"]
    base_x, base_y = observation["base_center"]
    own_ids = {actor["id"] for actor in observation["units"] + observation["buildings"]}
    attacked = [event for event in observation["events"] if event["kind"] == "under_attack"]

    assert len(observation["visible_enemies"]) == 8
    assert all(math.hypot(enemy["x"] - base_x, enemy["y"] - base_y) <= 12 for enemy in observation["visible_enemies"])
    assert [event["type"] for event in attacked] == ["fact", "harv"] and {event["id"] for event in attacked} <= own_ids
    assert sorted(building["hp"] for building in observation["buildings"])[:2] == [45, 82]
    assert observation["military"]["deaths_cost"] == 100 and observation["events"][-1]["kind"] == "unit_lost"
    assert state["legal_actions"]["attack"] == [enemy["id"] for enemy in observation["visible_enemies"]]


def test_the_wreck_state_has_three_units_of_which_one_is_a_wreck():
    state = load_state("wreck")
    observation = state["observation"]
    wrecks = [unit for unit in observation["units"] if unit.get("wreck")]
    (lost,) = [event for event in observation["events"] if event["kind"] == "unit_lost"]

    assert len(observation["units"]) == 3 and len(wrecks) == 1
    assert wrecks[0]["type"].endswith("husk") and lost["wreck_id"] == wrecks[0]["id"]
    assert [(enemy["type"], enemy.get("wreck", False)) for enemy in observation["visible_enemies"]] == [
        ("3tnk.husk", True),
        ("e3", False),
    ]
    assert wrecks[0]["id"] not in state["legal_actions"]["idle_units"]


def test_the_events_gap_state_skips_two_event_numbers():
    observation = load_state("events_gap")["observation"]

    assert [event["seq"] for event in observation["events"]] == [1, 2, 3, 4, 5, 6, 9]
