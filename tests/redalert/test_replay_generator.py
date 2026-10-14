"""The replay generator: the scripted match it plays, and the session fixture that holds its states."""

from __future__ import annotations

from types import ModuleType

import pytest

SHORT_MATCH_STEPS = 700
TICKS_PER_STEP = 5
RAID_FIRST_STEP = 1500
RAID_LAST_STEP = 1700
RAIDERS = 30
DEFENSES = ("pbox", "gun")


@pytest.fixture(scope="module")
def generator(load_script) -> ModuleType:
    """The generator script, imported once."""
    return load_script("make_replay_fixture.py")


@pytest.fixture(scope="module")
def short_match(generator: ModuleType):
    """The first 700 states of the scripted match, as the fake game's account of the run."""
    return generator.play(seed=7, steps=SHORT_MATCH_STEPS)


def _own_unit_counts(states: list[dict]) -> list[int]:
    return [len(state["observation"]["units"]) for state in states]


def _enemies_in_sight(state: dict) -> int:
    return len(state["observation"]["visible_enemies"]) + len(state["observation"]["visible_enemy_buildings"])


# -- the scripted match ----------------------------------------------------------------------------


def test_the_scripted_player_sends_no_batch_the_fake_game_refuses(short_match):
    assert short_match.invalid_actions == 0
    assert all(answer["accepted"] and answer["rejected_orders"] == 0 for answer in short_match.answers)
    assert short_match.waits + len(short_match.batches) == SHORT_MATCH_STEPS


def test_the_scripted_player_builds_its_two_lists_in_order(long_match_states):
    built = [building["type"] for building in long_match_states[-1]["observation"]["buildings"]]

    assert [item for item in built if item not in DEFENSES] == [
        "fact",
        "powr",
        "tent",
        "proc",
        "proc",
        "powr",
        "weap",
        "powr",
    ]
    assert [item for item in built if item in DEFENSES] == ["pbox", "gun"]


def test_the_states_of_a_run_follow_each_other_from_the_opening(short_match):
    states = short_match.states

    assert [state["state_version"] for state in states] == list(range(0, (SHORT_MATCH_STEPS + 1) * TICKS_PER_STEP, 5))
    assert [unit["type"] for unit in states[0]["observation"]["units"]] == ["mcv"]
    assert (states[0]["observation"]["faction"], states[0]["observation"]["enemy_faction"]) == ("england", "russia")
    assert states[0]["observation"]["economy"]["cash"] == 5000


def test_the_same_seed_gives_the_same_states_and_another_seed_other_states(generator, short_match):
    again = generator.play(seed=7, steps=SHORT_MATCH_STEPS)
    other = generator.play(seed=8, steps=SHORT_MATCH_STEPS)

    assert again.states == short_match.states and again.batches == short_match.batches
    assert other.states != short_match.states


def test_peak_own_units_is_the_largest_unit_count_of_any_state(generator, short_match):
    assert generator.peak_own_units(short_match.states) == max(_own_unit_counts(short_match.states)) > 10


# -- the long replay of the session ----------------------------------------------------------------


def test_the_long_match_is_the_generator_s_match_for_seed_7(long_match_states, short_match):
    assert long_match_states[: SHORT_MATCH_STEPS + 1] == short_match.states


def test_the_long_match_grows_to_a_steady_army_of_more_than_150_units(long_match_states):
    counts = _own_unit_counts(long_match_states)

    assert len(long_match_states) == 3201
    assert counts[0] == 1 and counts[-1] >= 150 and max(counts) == counts[-1]
    # The first state has the MCV, which is deployed at once; from then on no unit is ever lost.
    assert counts[1:] == sorted(counts[1:])


def test_the_long_match_shows_the_raid_on_the_base_and_the_enemy_base(long_match_states):
    during_the_raid = long_match_states[RAID_FIRST_STEP:RAID_LAST_STEP]
    after_the_raid = long_match_states[RAID_LAST_STEP + 1]
    raid_events = [event["kind"] for event in long_match_states[RAID_FIRST_STEP]["observation"]["events"]]
    enemy_building_types = {
        building["type"] for state in long_match_states for building in state["observation"]["visible_enemy_buildings"]
    }

    assert all(_enemies_in_sight(state) >= RAIDERS for state in during_the_raid)
    assert _enemies_in_sight(after_the_raid) < RAIDERS
    assert "under_attack" in raid_events and raid_events.count("enemy_spotted") >= 1
    assert {"fact", "barr", "weap", "ftur", "tsla"} <= enemy_building_types


def test_the_long_match_has_armies_on_the_move(long_match_states):
    marching = [
        sum(1 for unit in state["observation"]["units"] if unit["act"] == "attack_move") for state in long_match_states
    ]

    assert max(marching) >= 100 and marching[0] == 0


# -- the generator as a program --------------------------------------------------------------------


def test_main_prints_the_number_of_states_and_the_peak_for_the_seed_it_was_given(
    generator, short_match, run_folder, monkeypatch, capsys
):
    seeds = []
    monkeypatch.setattr(generator, "build", lambda seed: seeds.append(seed) or short_match.states)

    exit_code = generator.main(["11"])

    peak = max(_own_unit_counts(short_match.states))
    assert (exit_code, seeds) == (0, [11])
    assert capsys.readouterr().out == f"{SHORT_MATCH_STEPS + 1} states, peak of {peak} own units (seed 11)\n"


def test_the_generator_run_as_a_program_prints_the_size_of_the_replay_and_writes_nothing(run_script, tmp_path):
    completed = run_script(tmp_path, "make_replay_fixture.py")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "3201 states, peak of 172 own units (seed 7)\n"
    assert list(tmp_path.iterdir()) == []


def test_the_generator_refuses_a_seed_that_is_not_a_number(run_script, tmp_path):
    completed = run_script(tmp_path, "make_replay_fixture.py", "seven")

    assert completed.returncode == 2
    assert "invalid int value: 'seven'" in completed.stderr
