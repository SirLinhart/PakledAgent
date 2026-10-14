"""The synthetic Red Alert record: what its generator writes, and that the committed copy is exactly that."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from ucla_agent.core import records

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "records" / "redalert_synthetic"
EVENT_COUNT = 200
KIND_COUNTS = {
    "game_config": 1,
    "state": 100,
    "action": 42,
    "action_result": 40,
    "plan_change": 3,
    "decision": 4,
    "fallback": 3,
    "timing": 3,
    "ledger_summary": 2,
    "error": 1,
    "result": 1,
}
META_KEYS = {
    "session_id",
    "game_type",
    "tournament_id",
    "context",
    "agent_id",
    "seat",
    "attempt",
    "commit",
    "config_hashes",
    "models",
    "decision_services",
    "started_at",
}


@pytest.fixture(scope="module")
def generator(load_script):
    """The fixture generator script, imported once."""
    return load_script("make_metrics_fixture.py")


@pytest.fixture(scope="module")
def events(generator) -> list[dict]:
    return generator.build_events()


def _of_kind(events: list[dict], kind: str) -> list[dict]:
    return [event["data"] for event in events if event["kind"] == kind]


# -- the generator and the committed copy ----------------------------------------------------------


def test_the_committed_fixture_is_byte_for_byte_what_the_generator_writes(generator, tmp_path):
    generator.write(tmp_path)

    assert (tmp_path / "events.jsonl").read_bytes() == (COMMITTED_FIXTURE / "events.jsonl").read_bytes()
    assert (tmp_path / "meta.json").read_bytes() == (COMMITTED_FIXTURE / "meta.json").read_bytes()


def test_writing_twice_gives_the_same_bytes_with_unix_line_endings(generator, tmp_path):
    generator.write(tmp_path / "first")
    generator.write(tmp_path / "second")
    generator.write(tmp_path / "second")

    first = (tmp_path / "first" / "events.jsonl").read_bytes()
    assert first == (tmp_path / "second" / "events.jsonl").read_bytes()
    assert first.count(b"\n") == EVENT_COUNT and b"\r" not in first


def test_the_command_writes_both_files_into_the_folder_it_is_given(run_folder, run_script):
    completed = run_script(run_folder, "make_metrics_fixture.py", "generated")

    assert (completed.returncode, completed.stderr) == (0, "")
    assert completed.stdout.startswith("Wrote events.jsonl and meta.json to generated")
    assert sorted(path.name for path in (run_folder / "generated").iterdir()) == ["events.jsonl", "meta.json"]


def test_the_command_without_a_folder_writes_the_committed_fixture_folder(generator, run_folder, capsys):
    exit_code = generator.main([])

    written = run_folder / "tests" / "fixtures" / "records" / "redalert_synthetic"
    assert exit_code == 0
    assert (written / "events.jsonl").read_bytes() == (COMMITTED_FIXTURE / "events.jsonl").read_bytes()
    assert "redalert_synthetic" in capsys.readouterr().out


# -- the shape of the record -----------------------------------------------------------------------


def test_the_record_has_two_hundred_lines_with_gap_free_seq_in_time_order(events):
    assert len(events) == EVENT_COUNT
    assert [event["seq"] for event in events] == list(range(EVENT_COUNT))
    assert all(set(event) == {"seq", "t", "mono_ms", "kind", "data"} for event in events)
    assert [event["mono_ms"] for event in events] == sorted(event["mono_ms"] for event in events)
    assert (events[0]["t"], events[-1]["t"]) == ("2026-10-01T18:00:00.000Z", "2026-10-01T18:03:19.000Z")


def test_the_record_holds_only_defined_kinds_in_the_planned_numbers(events):
    counts = Counter(event["kind"] for event in events)

    assert counts == KIND_COUNTS
    assert set(counts) <= records.EVENT_KINDS
    assert (events[0]["kind"], events[-2]["kind"], events[-1]["kind"]) == ("game_config", "ledger_summary", "result")


def test_the_meta_file_has_the_keys_a_match_meta_file_has(generator):
    meta = generator.build_meta()

    assert set(meta) == META_KEYS
    assert (meta["game_type"], meta["context"], meta["attempt"]) == ("red_alert", "testing", 1)
    assert (meta["session_id"], meta["seat"]) == ("redalert-synthetic", "Multi0")
    assert json.loads(json.dumps(meta)) == meta


def test_states_are_thinned_as_the_record_writer_thins_them(events):
    states = _of_kind(events, "state")
    full = ["state" in state for state in states]

    assert [index for index, is_full in enumerate(full) if is_full] == [
        *range(0, 30, 5),
        30,
        31,
        32,
        *range(35, 100, 5),
    ]
    assert all(any(full[start : start + 5]) for start in range(len(full) - 4))
    assert set(states[1]) == {
        "state_version",
        "phase",
        "economy",
        "military",
        "units_count",
        "buildings_count",
        "visible_enemies_count",
    }
    assert [state["state_version"] for state in states] == list(range(0, 5000, 50))


# -- the values built in ---------------------------------------------------------------------------


def test_forty_batches_are_sent_and_thirty_eight_accepted(events):
    actions = _of_kind(events, "action")
    results = _of_kind(events, "action_result")

    assert sum(1 for action in actions if isinstance(action["action"], dict)) == 40
    assert [action["action"] for action in actions if not isinstance(action["action"], dict)] == ["WAIT", "WAIT"]
    assert sum(1 for result in results if result["accepted"]) == 38
    assert [result["result"]["error"] for result in results if not result["accepted"]] == ["INVALID_ACTION"] * 2


def test_every_result_follows_its_action_by_its_round_trip(events, generator):
    cycles = [event for event in events if event["kind"] in ("action", "action_result")]
    with_orders = [event for event in cycles if event["kind"] == "action_result" or event["data"]["action"] != "WAIT"]

    for batch in range(40):
        action, result = with_orders[2 * batch], with_orders[2 * batch + 1]
        assert (action["kind"], result["kind"]) == ("action", "action_result")
        assert action["data"]["compute_ms"] == generator.compute_ms(batch)
        assert result["data"]["round_trip_ms"] == generator.round_trip_ms(batch)
        assert result["mono_ms"] - action["mono_ms"] == int(generator.round_trip_ms(batch))


def test_the_last_ledger_summary_holds_the_final_counters(events):
    assert _of_kind(events, "ledger_summary")[-1] == {
        "orders_sent": 118,
        "orders_rejected_by_reason": {"not_available": 2, "not_ready": 1},
        "orders_lost": 1,
        "dropped_invalid": 2,
        "duplicate_production_orders": 0,
    }


def test_three_of_four_decisions_fall_back(events):
    decisions = _of_kind(events, "decision")

    assert [decision["outcome"] for decision in decisions] == ["timeout", "error", "used", "stale_discarded"]
    assert [fallback["reason"] for fallback in _of_kind(events, "fallback")] == ["timeout", "error", "stale_discarded"]


def test_power_is_low_in_six_states_and_a_building_waits_unplaced_in_two(events):
    states = _of_kind(events, "state")
    economies = [state["state"]["observation"]["economy"] if "state" in state else state["economy"] for state in states]
    low_power = [
        index for index, economy in enumerate(economies) if economy["power_drained"] > economy["power_provided"]
    ]
    ready = [
        index
        for index, state in enumerate(states)
        if "state" in state and any(item["ready"] for item in state["state"]["observation"]["production"])
    ]

    assert low_power == [40, 41, 42, 43, 44, 45]
    assert ready == [30, 31]


def _economies(events: list[dict]) -> list[dict]:
    states = _of_kind(events, "state")
    return [state["state"]["observation"]["economy"] if "state" in state else state["economy"] for state in states]


def test_the_money_is_gone_in_four_states_and_the_refinery_is_full_from_state_80(events):
    economies = _economies(events)
    money = [economy["cash"] + economy["ore"] for economy in economies]
    full = [index for index, economy in enumerate(economies) if economy["ore"] == economy["resource_capacity"] > 0]

    assert [index for index, amount in enumerate(money) if amount == 0] == [56, 57, 58, 59]
    assert full == list(range(80, 100))
    assert [index for index, amount in enumerate(money) if amount >= 1500] == [*range(44), *range(69, 100)]


def test_what_is_earned_from_state_60_is_ore_and_the_cash_is_what_was_left(events):
    economies = _economies(events)

    assert {economy["ore"] for economy in economies[:61]} == {0}
    assert [(economies[index]["cash"], economies[index]["ore"]) for index in (61, 79, 99)] == [
        (600, 100),
        (600, 1900),
        (2500, 2000),
    ]


def test_the_match_ends_as_a_win_with_a_trade_balance_of_1500(events, generator):
    last_state = _of_kind(events, "state")[-1]
    result = _of_kind(events, "result")[0]

    assert last_state["military"]["kills_cost"] - last_state["military"]["deaths_cost"] == 1500
    assert result["result"]["returns"][generator.build_meta()["agent_id"]] == 1.0
    assert result["result"]["termination_reason"] == "time_limit"


def test_the_final_trade_balance_can_be_set_for_a_second_run_set(generator):
    last_state = _of_kind(generator.build_events(final_trade_balance=900), "state")[-1]

    assert last_state["military"]["kills_cost"] - last_state["military"]["deaths_cost"] == 900


def test_the_plans_are_opening_hold_and_raid(events):
    changes = _of_kind(events, "plan_change")

    assert [(change["old"], change["new"], change["game_seconds"]) for change in changes] == [
        (None, "OPENING", 0.0),
        ("OPENING", "HOLD", 70.0),
        ("HOLD", "RAID", 120.0),
    ]
