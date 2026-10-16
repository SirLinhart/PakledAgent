"""The match record: run-level lines, the per-match event file and the thread that writes it."""

from __future__ import annotations

import ast
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO

import pytest

from altruagent import WAIT, DecisionContext, GameState
from ucla_agent.core import paths, records
from ucla_agent.core.records import EventWriter

EVENING = datetime(2026, 10, 12, 23, 59, 30, 250000, tzinfo=timezone.utc)
AFTER_MIDNIGHT = datetime(2026, 10, 13, 0, 0, 5, tzinfo=timezone.utc)
RESULT = {"is_terminal": True, "status": "completed", "returns": {"agent-1": 1.0}, "termination_reason": "victory"}
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
DEFINED_KINDS = [
    "game_config",
    "state",
    "decision",
    "text_decision",
    "claude_token_cap",
    "claude_usage",
    "plan_change",
    "plan_rule_blocked",
    "raid",
    "harvesters_parked",
    "harvesters_moved",
    "harvesters_rebalanced",
    "expansion",
    "refinery_field",
    "creep_placement",
    "placement_refused",
    "route_walk",
    "route",
    "economy_hold",
    "economy",
    "opening_reach",
    "opening_map_size",
    "scout",
    "scout_slot",
    "rush_verdict",
    "enemy_estimate",
    "clock",
    "spending_focus",
    "counter_quota",
    "base_defense",
    "anti_air",
    "hold_position",
    "siege",
    "attack_target",
    "combat",
    "commander",
    "action",
    "action_result",
    "timing",
    "ledger_summary",
    "werewolf_ledger",
    "werewolf_inference_reset",
    "werewolf_chat",
    "werewolf_decision",
    "pokemon_decision",
    "fallback",
    "unsupported_game",
    "message_in",
    "message_out",
    "error",
    "result",
    "match_end",
]
SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
AGENT_PACKAGE = SOURCE_ROOT / "ucla_agent"
SEAT_PROCESS_LIMIT_SECONDS = 60
METRICS_MODULE = AGENT_PACKAGE / "core" / "metrics.py"
# A string that is a record file's name, or a path ending in one.
RECORD_FILE_NAME = re.compile(r"(?:^|[/\\])(?:events[^/\\]*\.jsonl|meta[^/\\]*\.json|metrics\.json|run\.jsonl)$")
RECORD_NAME_IDENTIFIERS = {
    "EVENTS_FILE_NAME",
    "META_FILE_NAME",
    "RUN_FILE_NAME",
    "METRICS_FILE_NAME",
    "events_file_name",
    "meta_file_name",
}
OPEN_MODE = re.compile(r"[rwxabt+U]+")
DEFAULT_OPEN_MODE = "r"
READING_MODE_LETTERS = "r+"
READING_METHODS = {"read_text", "read_bytes"}
ROUND_TRIP_SECONDS = 0.12
WAIT_LIMIT_SECONDS = 5.0
POLL_SECONDS = 0.005
TIMED_LOG_CALLS = 1000
MAX_MEDIAN_LOG_SECONDS = 0.001
SMALL_QUEUE = 5
SHORT_CLOSE_TIMEOUT_SECONDS = 0.2


def _context(session_id: str = "session-1", game_type: str = "red_alert") -> DecisionContext:
    return DecisionContext(session_id=session_id, tournament_id=None, game_type=game_type, agent_id="agent-1")


def _wait_until(condition: Callable[[], bool]) -> None:
    """Give the writer thread time: returns as soon as `condition()` holds and fails after `WAIT_LIMIT_SECONDS`."""
    deadline = time.monotonic() + WAIT_LIMIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "the writer thread did not get there in time"
        time.sleep(POLL_SECONDS)


@pytest.fixture
def clock(monkeypatch):
    """A settable UTC clock for the record writer."""
    moments = {"now": EVENING}
    monkeypatch.setattr(records, "_utc_now", lambda: moments["now"])
    return moments


@pytest.fixture
def match_events(run_folder, read_jsonl) -> Callable[..., list[dict]]:
    """Reads the event lines of a finished match record by its session id."""

    def read(session_id: str = "session-1") -> list[dict]:
        (events_path,) = run_folder.glob(f"runs/*/{session_id}__seat/events.jsonl")
        return read_jsonl(events_path)

    return read


def test_records_land_under_the_project_root_the_test_names(run_folder):
    assert paths.repository_root() == run_folder


# -- run-level lines -------------------------------------------------------------------------------


def test_run_line_is_one_json_object_in_the_utc_dated_run_file(run_folder, read_jsonl, clock):
    records.append_run_line("run_started", {"commit": "abc", "flags": ["--match"]})

    lines = read_jsonl(run_folder / "runs" / "20261012" / "run.jsonl")
    assert lines == [
        {"t": "2026-10-12T23:59:30.250Z", "kind": "run_started", "data": {"commit": "abc", "flags": ["--match"]}}
    ]


def test_run_lines_append_in_order(run_folder, read_jsonl, clock):
    records.append_run_line("run_started", {})
    records.append_run_line("run_stopped", {"exit_code": 0})

    lines = read_jsonl(run_folder / "runs" / "20261012" / "run.jsonl")
    assert [line["kind"] for line in lines] == ["run_started", "run_stopped"]


def test_a_run_that_passes_midnight_keeps_its_lines_in_the_file_it_started(run_folder, read_jsonl, clock):
    records.append_run_line("run_started", {})
    clock["now"] = AFTER_MIDNIGHT
    records.append_run_line("run_stopped", {})

    lines = read_jsonl(run_folder / "runs" / "20261012" / "run.jsonl")
    assert [line["kind"] for line in lines] == ["run_started", "run_stopped"]
    assert lines[1]["t"] == "2026-10-13T00:00:05.000Z"
    assert not (run_folder / "runs" / "20261013").exists()


# -- the events of a match -------------------------------------------------------------------------


def test_log_before_start_match_writes_nothing(run_folder):
    records.log("state", {"state_version": 1})

    assert not (run_folder / "runs").exists()


def test_every_line_has_seq_time_elapsed_milliseconds_kind_and_data(run_folder, clock, match_events):
    records.start_match(_context("session-9"), None)
    records.log("unsupported_game", {"game_type": "chess"})
    records.log("ledger_summary", {"orders_sent": 0})
    records.finish_match(RESULT)

    lines = match_events("session-9")
    assert [(line["seq"], line["kind"]) for line in lines] == [
        (0, "unsupported_game"),
        (1, "ledger_summary"),
        (2, "result"),
    ]
    assert [line["data"] for line in lines[:2]] == [{"game_type": "chess"}, {"orders_sent": 0}]
    assert all(set(line) == {"seq", "t", "mono_ms", "kind", "data"} for line in lines)
    assert all(line["t"] == "2026-10-12T23:59:30.250Z" for line in lines)
    assert all(isinstance(line["mono_ms"], int) and line["mono_ms"] >= 0 for line in lines)


def test_the_events_file_is_in_the_utc_dated_folder_of_the_session(run_folder, clock):
    records.start_match(_context("session-9"), None)
    records.finish_match(RESULT)

    assert (run_folder / "runs" / "20261012" / "session-9__seat" / "events.jsonl").is_file()


def test_the_events_file_is_on_disk_from_match_start(run_folder, clock):
    records.start_match(_context("session-9"), None)

    _wait_until((run_folder / "runs" / "20261012" / "session-9__seat" / "events.jsonl").is_file)


def test_meta_json_is_written_at_match_start_with_all_the_listed_keys(git_repo, git, clock):
    context = DecisionContext(
        session_id="session-9", tournament_id="tour-7", game_type="red_alert", agent_id="agent-1", seat_position=1
    )
    meta_path = git_repo / "runs" / "20261012" / "session-9__1" / "meta.json"

    records.start_match(context, None)
    _wait_until(lambda: meta_path.is_file() and meta_path.stat().st_size > 0)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    assert set(meta) == META_KEYS
    assert (meta["session_id"], meta["game_type"], meta["tournament_id"]) == ("session-9", "red_alert", "tour-7")
    assert (meta["context"], meta["agent_id"], meta["seat"], meta["attempt"]) == ("tournament", "agent-1", "1", 1)
    assert meta["started_at"] == "2026-10-12T23:59:30.250Z"
    assert meta["commit"] == git(git_repo, "rev-parse", "HEAD")
    assert list(meta["config_hashes"]) == ["config/agent.toml"]
    assert (meta["models"], meta["decision_services"]) == ([], [])


def test_meta_json_passes_through_redaction(run_folder, monkeypatch, clock):
    monkeypatch.setenv("JEV_API_KEY", "jev-4f9c2d7e1a6b8053")
    context = DecisionContext(
        session_id="session-9", tournament_id="held with jev-4f9c2d7e1a6b8053", game_type="red_alert", agent_id="a"
    )

    records.start_match(context, None)
    records.finish_match(RESULT)

    meta_text = (run_folder / "runs" / "20261012" / "session-9__seat" / "meta.json").read_text(encoding="utf-8")
    assert json.loads(meta_text)["tournament_id"] == "held with [REDACTED]"


def test_start_match_is_idempotent_within_a_process(run_folder):
    records.start_match(_context("first"), None)
    records.start_match(_context("second"), None)
    records.log("state", {})
    records.finish_match(RESULT)

    assert len(list(run_folder.glob("runs/*/first__seat/events.jsonl"))) == 1
    assert list(run_folder.glob("runs/*/second*")) == []


def test_finish_match_runs_callbacks_in_order_then_writes_the_result(run_folder, match_events):
    records.start_match(_context(), None)
    records.on_match_end(lambda: records.log("ledger_summary", {"registered": "first"}))
    records.on_match_end(lambda: records.log("ledger_summary", {"registered": "second"}))

    records.finish_match(RESULT)

    lines = match_events()
    assert [line["kind"] for line in lines] == ["ledger_summary", "ledger_summary", "result"]
    assert [line["data"].get("registered") for line in lines[:2]] == ["first", "second"]
    assert lines[-1]["data"]["result"] == RESULT
    assert lines[-1]["data"]["ended_at"].endswith("Z")
    assert lines[-1]["data"]["dropped_events"] == 0


def test_finish_match_runs_once(run_folder, match_events):
    calls = []
    records.start_match(_context(), None)
    records.on_match_end(lambda: calls.append("callback"))

    records.finish_match(RESULT)
    records.finish_match(RESULT)

    assert calls == ["callback"]
    assert [line["kind"] for line in match_events()] == ["result"]


def test_callbacks_registered_before_the_record_opens_still_run(run_folder):
    calls = []
    records.on_match_end(lambda: calls.append("callback"))

    records.finish_match({})

    assert calls == ["callback"]
    assert not (run_folder / "runs").exists()


def test_an_event_logged_after_the_result_is_not_written(run_folder, match_events):
    records.start_match(_context(), None)
    records.finish_match(RESULT)

    records.log("state", {"state_version": 99})

    assert [line["kind"] for line in match_events()] == ["result"]


def test_a_value_json_cannot_encode_is_written_as_text(run_folder, match_events):
    records.start_match(_context(), None)

    records.log("action", {"action": WAIT})
    records.finish_match(RESULT)

    assert match_events()[0]["data"] == {"action": "WAIT"}


# -- one folder per seat ---------------------------------------------------------------------------

# A process that plays one seat of a match, as `python -c` runs it with the seat's name as its argument:
# it opens the record with a first state that names its seat, logs that state and ends.
SEAT_PROCESS = """
import sys

from altruagent import DecisionContext, GameState
from ucla_agent.core import records

context = DecisionContext(session_id="shared-session", tournament_id=None, game_type="red_alert", agent_id="agent-1")
first_state = GameState.from_mcp_state({"state_version": 0, "phase": "playing", "your_seat": sys.argv[1]})
records.start_match(context, first_state)
records.log_state(first_state.raw)
"""


def _state_naming_seat(your_seat: str | None) -> GameState:
    return GameState.from_mcp_state({"state_version": 1, "phase": "playing", "your_seat": your_seat})


def _seat_context(seat_position: int | None = None) -> DecisionContext:
    return DecisionContext(
        session_id="session-9",
        tournament_id=None,
        game_type="red_alert",
        agent_id="agent-1",
        seat_position=seat_position,
    )


def test_two_processes_playing_two_seats_of_one_session_write_two_folders(run_folder, read_jsonl):
    environment = {**os.environ, "PYTHONPATH": str(SOURCE_ROOT)}
    processes = [
        subprocess.Popen([sys.executable, "-c", SEAT_PROCESS, seat], cwd=run_folder, env=environment)
        for seat in ("Multi0", "Multi1")
    ]
    exit_codes = [process.wait(SEAT_PROCESS_LIMIT_SECONDS) for process in processes]

    folders = sorted(run_folder.glob("runs/*/*"))
    assert exit_codes == [0, 0]
    assert [folder.name for folder in folders] == ["shared-session__Multi0", "shared-session__Multi1"]
    for folder, seat in zip(folders, ("Multi0", "Multi1")):
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        assert sorted(path.name for path in folder.iterdir()) == ["events.jsonl", "meta.json"]
        assert (meta["session_id"], meta["seat"], meta["attempt"]) == ("shared-session", seat, 1)
        assert [line["kind"] for line in read_jsonl(folder / "events.jsonl")] == ["state", "match_end"]


@pytest.mark.parametrize(
    "your_seat, seat_position, seat",
    [
        ("Multi0", 1, "Multi0"),
        (None, 1, "1"),
        (None, 0, "0"),
        ("", 2, "2"),
        (None, None, "seat"),
        ("Seat 3/West..", None, "Seat_3_West__"),
    ],
    ids=[
        "the game's name for the seat",
        "the runtime's seat position",
        "seat position zero",
        "an empty name",
        "nothing known",
        "a name with unsafe characters",
    ],
)
def test_the_match_folder_and_the_meta_file_carry_the_seat_played(run_folder, clock, your_seat, seat_position, seat):
    records.start_match(_seat_context(seat_position), _state_naming_seat(your_seat))
    records.finish_match(RESULT)

    folder = run_folder / "runs" / "20261012" / f"session-9__{seat}"
    assert [path.name for path in folder.parent.iterdir()] == [folder.name]
    assert json.loads((folder / "meta.json").read_text(encoding="utf-8"))["seat"] == seat


def test_the_seat_is_read_from_any_state_object_that_carries_the_raw_state(run_folder, clock):
    state_from_a_harness = types.SimpleNamespace(raw={"your_seat": "Multi1", "state_version": 5}, legal_actions=[])

    records.start_match(_seat_context(), state_from_a_harness)
    records.finish_match(RESULT)

    assert (run_folder / "runs" / "20261012" / "session-9__Multi1" / "events.jsonl").is_file()


def test_a_resumed_seat_takes_attempt_2_in_its_own_folder_and_leaves_the_other_seat_alone(
    run_folder, clock, monkeypatch
):
    for seat in ("Multi0", "Multi1", "Multi0"):
        monkeypatch.setattr(records, "_process", records._ProcessRecord())
        records.start_match(_seat_context(), _state_naming_seat(seat))
        records.finish_match(RESULT)

    day_folder = run_folder / "runs" / "20261012"
    assert sorted(path.name for path in day_folder.iterdir()) == ["session-9__Multi0", "session-9__Multi1"]
    assert sorted(path.name for path in (day_folder / "session-9__Multi0").iterdir()) == [
        "events.2.jsonl",
        "events.jsonl",
        "meta.2.json",
        "meta.json",
    ]
    assert sorted(path.name for path in (day_folder / "session-9__Multi1").iterdir()) == ["events.jsonl", "meta.json"]
    assert json.loads((day_folder / "session-9__Multi0" / "meta.2.json").read_text(encoding="utf-8"))["attempt"] == 2


# -- state events ----------------------------------------------------------------------------------


def _red_alert_state(tick: int, *, batch: int = 1, problems: tuple = ()) -> dict:
    """A Red Alert state as the platform sends it, with two units, one building and one visible enemy."""
    return {
        "session_id": "session-1",
        "state_version": tick,
        "phase": "playing",
        "time": {"max_minutes": 20.0, "game_seconds": tick / 25, "remaining_seconds": 1200 - tick / 25},
        "observation": {
            "tick": tick,
            "economy": {"cash": 5000 - tick, "power_provided": 100, "power_drained": 20, "harvesters": 1},
            "military": {"kills_cost": tick, "deaths_cost": 300, "assets_value": 4000, "army_value": 200},
            "units": [{"id": 120, "type": "e1"}, {"id": 121, "type": "e1"}],
            "buildings": [{"id": 140, "type": "fact"}],
            "visible_enemies": [{"id": 900, "type": "e1"}],
            "explored_percent": 12.5,
        },
        "last_orders": {"batch": batch, "pending": False, "results": [{"index": 0, "status": "ok"}]},
        "recent_order_problems": list(problems),
    }


def _is_full(line: dict) -> bool:
    return "state" in line["data"]


@pytest.fixture
def logged_states(run_folder, match_events) -> Callable[..., list[dict]]:
    """Logs the given states in a match of the given game, finishes it and returns the `state` lines."""

    def log_all(states: list[dict], game_type: str = "red_alert") -> list[dict]:
        records.start_match(_context(game_type=game_type), None)
        for state in states:
            records.log_state(state)
        records.finish_match(RESULT)
        return [line for line in match_events() if line["kind"] == "state"]

    return log_all


def test_of_twenty_red_alert_states_with_unchanged_orders_four_hold_the_full_observation(logged_states):
    states = [_red_alert_state(tick) for tick in range(100, 200, 5)]

    lines = logged_states(states)

    full_positions = [position for position, line in enumerate(lines) if _is_full(line)]
    assert len(lines) == 20
    assert full_positions == [0, 5, 10, 15]
    assert all(lines[position]["data"]["state"] == states[position] for position in full_positions)


def test_a_full_state_event_holds_version_phase_and_the_state_as_received(logged_states):
    state = _red_alert_state(100)

    (line,) = logged_states([state])

    assert line["data"] == {"state_version": 100, "phase": "playing", "state": state}


def test_a_thinned_state_event_holds_the_figures_the_counts_the_balance_the_explored_share_and_the_clock(
    logged_states,
):
    lines = logged_states([_red_alert_state(100), _red_alert_state(105)])

    assert lines[1]["data"] == {
        "state_version": 105,
        "phase": "playing",
        "economy": {"cash": 4895, "power_provided": 100, "power_drained": 20, "harvesters": 1},
        "military": {"kills_cost": 105, "deaths_cost": 300, "assets_value": 4000, "army_value": 200},
        "units_count": 2,
        "buildings_count": 1,
        "visible_enemies_count": 1,
        "trade_balance": -195,
        "explored_percent": 12.5,
        "time": {"max_minutes": 20.0, "game_seconds": 4.2, "remaining_seconds": 1195.8},
    }


@pytest.mark.parametrize(
    "military",
    [None, {"kills_cost": 400}, {"kills_cost": 400, "deaths_cost": None}, {"kills_cost": "400", "deaths_cost": 0}],
    ids=["no military figures", "no deaths_cost", "a null deaths_cost", "a kills_cost that is text"],
)
def test_a_thinned_state_whose_military_figures_do_not_give_both_costs_has_no_trade_balance(logged_states, military):
    second = _red_alert_state(105)
    second["observation"]["military"] = military

    lines = logged_states([_red_alert_state(100), second])

    assert lines[1]["data"]["trade_balance"] is None


def test_a_thinned_state_without_a_clock_or_an_explored_share_holds_null_for_them(logged_states):
    second = _red_alert_state(105)
    del second["time"], second["observation"]["explored_percent"]

    lines = logged_states([_red_alert_state(100), second])

    assert (lines[1]["data"]["time"], lines[1]["data"]["explored_percent"]) == (None, None)


def test_the_annotations_a_decision_maker_gives_are_added_to_full_and_thinned_states_alike(run_folder, match_events):
    records.start_match(_context(), None)

    records.log_state(_red_alert_state(100), {"plan": "HOLD", "enemy_army_value": 800})
    records.log_state(_red_alert_state(105), {"plan": "RAID", "enemy_army_value": 1200})
    records.log_state(_red_alert_state(110))
    records.finish_match(RESULT)

    lines = [line["data"] for line in match_events() if line["kind"] == "state"]
    assert ["state" in data for data in lines] == [True, False, False]
    assert [(data.get("plan"), data.get("enemy_army_value")) for data in lines] == [
        ("HOLD", 800),
        ("RAID", 1200),
        (None, None),
    ]
    assert lines[0]["state"] == _red_alert_state(100)


def test_changed_order_verdicts_put_the_full_state_in_the_record(logged_states):
    states = [
        _red_alert_state(100),
        _red_alert_state(105),
        _red_alert_state(110, batch=2),
        _red_alert_state(115, batch=2),
        _red_alert_state(120, batch=2, problems=({"batch": 2, "index": 0, "status": "lost"},)),
        _red_alert_state(125, batch=2, problems=({"batch": 2, "index": 0, "status": "lost"},)),
    ]

    lines = logged_states(states)

    assert [_is_full(line) for line in lines] == [True, False, True, False, True, False]


def test_every_five_consecutive_red_alert_states_include_a_full_one(logged_states):
    states = [_red_alert_state(tick, batch=2 if tick >= 137 else 1) for tick in range(100, 215, 5)]

    full = [_is_full(line) for line in logged_states(states)]

    assert len(full) == 23
    assert all(any(full[start : start + 5]) for start in range(len(full) - 4))


def test_a_red_alert_state_without_an_observation_is_written_as_received(logged_states):
    waiting = {"state_version": 0, "phase": "waiting_for_agents", "observation": None}

    lines = logged_states([waiting, waiting, waiting])

    assert [line["data"] for line in lines] == [
        {"state_version": 0, "phase": "waiting_for_agents", "state": waiting}
    ] * 3


def test_states_of_another_game_are_always_written_in_full(logged_states):
    states = [{"state_version": version, "phase": "moving", "observation": "board"} for version in range(1, 8)]

    lines = logged_states(states, game_type="werewolf")

    assert [line["data"]["state"] for line in lines] == states


def test_log_state_before_start_match_writes_nothing(run_folder):
    records.log_state(_red_alert_state(100))

    assert not (run_folder / "runs").exists()


def test_start_match_takes_the_first_state_as_the_last_one_seen(run_folder):
    first_state = GameState.from_mcp_state({"state_version": 41, "phase": "playing"})

    records.start_match(_context(), first_state)

    assert (records._process.state_log.last_state_version, records._process.state_log.last_phase) == (41, "playing")


def test_log_state_keeps_the_last_version_and_phase_seen(run_folder):
    records.start_match(_context(), None)

    records.log_state(_red_alert_state(100))
    records.log_state({"state_version": 105, "phase": "finished", "observation": None})

    assert (records._process.state_log.last_state_version, records._process.state_log.last_phase) == (105, "finished")


# -- actions and their results ---------------------------------------------------------------------


def test_one_event_of_each_main_kind_gives_five_lines_with_their_fields(run_folder, match_events):
    batch = {"type": "orders", "orders": [{"cmd": "deploy", "units": [102]}]}
    decision = {
        "state_version": 100,
        "source": "local:fast",
        "request_summary": "plan: HOLD or RAID",
        "answer": "RAID",
        "latency_ms": 412,
        "outcome": "used",
    }
    records.start_match(_context(), None)

    records.log_state(_red_alert_state(100))
    records.log("decision", decision)
    records.log_action(100, batch, 3.2)
    records.log_action_result({"accepted": True, "results": [{"index": 0, "status": "pending"}]})
    records.finish_match(RESULT)

    lines = match_events()
    assert [(line["seq"], line["kind"]) for line in lines] == [
        (0, "state"),
        (1, "decision"),
        (2, "action"),
        (3, "action_result"),
        (4, "result"),
    ]
    assert set(lines[0]["data"]) == {"state_version", "phase", "state"}
    assert lines[1]["data"] == decision
    assert lines[2]["data"] == {"state_version": 100, "action": batch, "compute_ms": 3.2}
    assert set(lines[3]["data"]) == {"result", "accepted", "round_trip_ms"}
    assert set(lines[4]["data"]) == {"result", "ended_at", "dropped_events"}


def test_an_action_given_its_order_and_command_counts_carries_them_and_a_wait_carries_its_zeros(
    run_folder, match_events
):
    batch = {"type": "orders", "orders": [{"cmd": "move", "units": [101, 102, 103], "to": [5, 5]}]}
    records.start_match(_context(), None)

    records.log_action(100, batch, 3.2, order_count=1, command_count=3)
    records.log_action(105, WAIT, 1.0, order_count=0, command_count=0)
    records.log_action(110, WAIT, 1.0, order_count=0)
    records.finish_match(RESULT)

    actions = [line["data"] for line in match_events() if line["kind"] == "action"]
    assert actions == [
        {"state_version": 100, "action": batch, "compute_ms": 3.2, "order_count": 1, "command_count": 3},
        {"state_version": 105, "action": "WAIT", "compute_ms": 1.0, "order_count": 0, "command_count": 0},
        {"state_version": 110, "action": "WAIT", "compute_ms": 1.0, "order_count": 0},
    ]


def test_an_action_result_carries_the_time_since_its_action(run_folder, match_events):
    records.start_match(_context(), None)

    records.log_action(100, {"type": "orders", "orders": []}, 2.0)
    time.sleep(ROUND_TRIP_SECONDS)
    records.log_action_result({"accepted": True})
    records.finish_match(RESULT)

    result_line = match_events()[1]
    assert result_line["data"]["result"] == {"accepted": True}
    assert result_line["data"]["accepted"] is True
    assert 110 <= result_line["data"]["round_trip_ms"] <= 200


def test_an_action_result_with_an_error_key_is_not_accepted(run_folder, match_events):
    refusal = {"error": "INVALID_ACTION", "detail": "[0] train: not_available"}
    records.start_match(_context(), None)

    records.log_action(100, {"type": "orders", "orders": [{"cmd": "train", "item": "3tnk"}]}, 2.0)
    records.log_action_result(refusal)
    records.finish_match(RESULT)

    assert match_events()[1]["data"]["result"] == refusal
    assert match_events()[1]["data"]["accepted"] is False


def test_the_round_trip_is_measured_from_the_last_action(run_folder, match_events):
    records.start_match(_context(), None)

    records.log_action(100, WAIT, 1.0)
    time.sleep(ROUND_TRIP_SECONDS)
    records.log_action(105, {"type": "orders", "orders": []}, 1.0)
    records.log_action_result({"accepted": True})
    records.finish_match(RESULT)

    lines = match_events()
    assert [line["data"].get("action") for line in lines[:2]] == ["WAIT", {"type": "orders", "orders": []}]
    assert lines[2]["data"]["round_trip_ms"] < 100


def test_an_action_result_without_an_action_has_no_round_trip(run_folder, match_events):
    records.start_match(_context(), None)

    records.log_action_result({"accepted": True})
    records.finish_match(RESULT)

    assert match_events()[0]["data"] == {"result": {"accepted": True}, "accepted": True, "round_trip_ms": None}


def test_actions_and_results_before_start_match_write_nothing(run_folder):
    records.log_action(100, WAIT, 1.0)
    records.log_action_result({"accepted": True})

    assert not (run_folder / "runs").exists()
    assert records._process.last_action_at is None


# -- what a thread logs while it is suppressed -----------------------------------------------------


def _log_one_of_each(label: str) -> None:
    """A state, an event, an action, its result and an end-of-match callback that logs, each carrying `label`."""
    records.log_state({"state_version": label, "phase": label, "observation": None})
    records.log("ledger_summary", {"logged": label})
    records.log_action(label, WAIT, 1.0)
    records.log_action_result({"logged": label})
    records.on_match_end(lambda: records.log("ledger_summary", {"callback": label}))


def test_what_a_thread_logs_inside_suppressed_is_written_nowhere_and_what_it_logs_after_is_recorded(
    run_folder, match_events
):
    records.start_match(_context(), None)

    with records.suppressed():
        _log_one_of_each("suppressed")
    _log_one_of_each("recorded")
    records.finish_match(RESULT)

    lines = match_events()
    assert [line["kind"] for line in lines] == [
        "state",
        "ledger_summary",
        "action",
        "action_result",
        "ledger_summary",
        "result",
    ]
    assert lines[4]["data"] == {"callback": "recorded"}
    assert "suppressed" not in json.dumps(lines)


def test_suppressed_leaves_the_last_state_seen_and_the_round_trip_mark_as_they_were(run_folder):
    records.start_match(_context(), None)
    records.log_state({"state_version": 7, "phase": "moving", "observation": None})
    records.log_action(7, WAIT, 1.0)
    marked = records._process.last_action_at

    with records.suppressed():
        records.log_state({"state_version": 9, "phase": "messaging", "observation": None})
        records.log_action(9, WAIT, 1.0)

    assert (records._process.state_log.last_state_version, records._process.state_log.last_phase) == (7, "moving")
    assert records._process.last_action_at == marked


def test_suppression_holds_only_for_the_thread_that_entered_it(run_folder, match_events):
    records.start_match(_context(), None)

    with records.suppressed():
        other = threading.Thread(target=records.log, args=("ledger_summary", {"logged": "by another thread"}))
        other.start()
        other.join()
        records.log("ledger_summary", {"logged": "by the suppressed thread"})
    records.finish_match(RESULT)

    assert [line["data"] for line in match_events()[:-1]] == [{"logged": "by another thread"}]


def test_a_nested_block_leaves_the_thread_suppressed_until_the_outer_block_ends(run_folder, match_events):
    records.start_match(_context(), None)

    with records.suppressed():
        with records.suppressed():
            pass
        records.log("ledger_summary", {"logged": "inside the outer block"})
    records.log("ledger_summary", {"logged": "after it"})
    records.finish_match(RESULT)

    assert [line["data"] for line in match_events()[:-1]] == [{"logged": "after it"}]


def test_a_block_left_by_an_exception_ends_the_suppression(run_folder, match_events):
    records.start_match(_context(), None)

    with pytest.raises(RuntimeError), records.suppressed():
        raise RuntimeError("left the block")
    records.log("ledger_summary", {"logged": "after it"})
    records.finish_match(RESULT)

    assert [line["data"] for line in match_events()[:-1]] == [{"logged": "after it"}]


def test_an_undefined_kind_inside_suppressed_still_raises_under_the_test_configuration(run_folder):
    records.start_match(_context(), None)

    with records.suppressed(), pytest.raises(ValueError, match="not a defined event kind"):
        records.log("werewolf_ledgr", {})


# -- the closed list of event kinds ----------------------------------------------------------------


def test_the_list_of_event_kinds_is_the_one_the_record_defines():
    assert records.EVENT_KINDS == set(DEFINED_KINDS)
    assert len(DEFINED_KINDS) == len(set(DEFINED_KINDS))


@pytest.mark.parametrize("kind", DEFINED_KINDS)
def test_every_defined_kind_is_written(run_folder, match_events, kind):
    records.start_match(_context(), None)

    records.log(kind, {})
    records.finish_match(RESULT)

    assert [line["kind"] for line in match_events()] == [kind, "result"]


def test_an_undefined_kind_raises_under_the_test_configuration(run_folder, match_events):
    records.start_match(_context(), None)

    with pytest.raises(ValueError, match="'no_such_kind' is not a defined event kind"):
        records.log("no_such_kind", {})
    records.finish_match(RESULT)

    assert [line["kind"] for line in match_events()] == ["result"]


def test_an_undefined_kind_raises_under_the_test_configuration_without_an_open_record(run_folder):
    with pytest.raises(ValueError):
        records.log("no_such_kind", {})


def test_in_play_an_undefined_kind_is_recorded_as_a_handled_error(run_folder, monkeypatch, match_events):
    monkeypatch.delenv(records.STRICT_EVENT_KINDS_ENV)
    records.start_match(_context(), None)

    records.log("no_such_kind", {"state_version": 7})
    records.finish_match(RESULT)

    lines = match_events()
    assert [line["kind"] for line in lines] == ["error", "result"]
    assert lines[0]["data"] == {
        "where": "records.log",
        "type": "handled",
        "exception": "ValueError",
        "message": "'no_such_kind' is not a defined event kind",
    }


def test_in_play_an_undefined_kind_before_start_match_does_nothing(run_folder, monkeypatch):
    monkeypatch.delenv(records.STRICT_EVENT_KINDS_ENV)

    records.log("no_such_kind", {})

    assert not (run_folder / "runs").exists()


# -- recording never delays or stops play ----------------------------------------------------------


def test_a_thousand_log_calls_have_a_median_under_one_millisecond(run_folder, match_events):
    units = [{"id": 100 + index, "type": "e1", "x": index, "y": 17, "hp": 100, "idle": True} for index in range(30)]
    records.start_match(_context(), None)
    durations = []

    for version in range(TIMED_LOG_CALLS):
        started = time.perf_counter()
        records.log("state", {"state_version": version, "phase": "playing", "state": {"units": units}})
        durations.append(time.perf_counter() - started)
    records.finish_match(RESULT)

    lines = match_events()
    assert statistics.median(durations) < MAX_MEDIAN_LOG_SECONDS
    assert [line["seq"] for line in lines] == list(range(TIMED_LOG_CALLS + 1))
    assert lines[-1]["data"]["dropped_events"] == 0


def test_log_does_not_raise_on_an_unwritable_path_and_the_result_counts_what_was_dropped(
    run_folder, clock, match_events
):
    in_the_way = run_folder / "runs" / "20261012"
    in_the_way.parent.mkdir()
    in_the_way.write_text("a file where the dated folder has to be", encoding="utf-8")
    records.start_match(_context(), None)

    for version in range(3):
        records.log("state", {"state_version": version})
    _wait_until(lambda: records._process.writer.dropped_events == 3)
    in_the_way.unlink()
    records.finish_match(RESULT)

    (line,) = match_events()
    assert (line["seq"], line["kind"], line["data"]["dropped_events"]) == (0, "result", 3)


# -- the writer thread -----------------------------------------------------------------------------


class _GatedFile:
    """Opens a file for an `EventWriter`, but only once the test has opened the gate."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.waiting = threading.Event()
        self.gate = threading.Event()

    def open(self) -> TextIO:
        self.waiting.set()
        self.gate.wait(WAIT_LIMIT_SECONDS)
        return open(self.path, "a", encoding="utf-8")


class _FailingFile:
    """A file whose every write fails, as on a full or vanished disk."""

    def write(self, _text: str) -> int:
        raise OSError("no space left on device")

    def close(self) -> None:
        raise OSError("the device is gone")


def _appending_to(path: Path) -> Callable[[], TextIO]:
    return lambda: open(path, "a", encoding="utf-8")


def test_the_queue_holds_ten_thousand_events():
    assert records.MAX_QUEUED_EVENTS == 10_000


def test_an_event_that_does_not_fit_on_the_queue_is_dropped_and_counted(tmp_path, monkeypatch, read_jsonl):
    monkeypatch.setattr(records, "MAX_QUEUED_EVENTS", SMALL_QUEUE)
    gated = _GatedFile(tmp_path / "events.jsonl")
    writer = EventWriter(gated.open)
    gated.waiting.wait(WAIT_LIMIT_SECONDS)

    for number in range(SMALL_QUEUE + 3):
        writer.submit("state", {"number": number})
    dropped_while_stuck = writer.dropped_events
    gated.gate.set()
    writer.close("result", {})

    lines = read_jsonl(gated.path)
    assert dropped_while_stuck == 3
    assert [line["data"].get("number") for line in lines[:-1]] == list(range(SMALL_QUEUE))
    assert [line["seq"] for line in lines] == list(range(SMALL_QUEUE + 1))
    assert lines[-1]["data"] == {"dropped_events": 3}


def test_an_event_that_cannot_be_serialized_is_dropped_and_the_next_one_is_written(tmp_path, read_jsonl):
    circular: dict = {}
    circular["itself"] = circular
    writer = EventWriter(_appending_to(tmp_path / "events.jsonl"))

    writer.submit("state", circular)
    writer.submit("state", {"state_version": 2})
    writer.close("result", {})

    lines = read_jsonl(tmp_path / "events.jsonl")
    assert [(line["seq"], line["kind"]) for line in lines] == [(0, "state"), (1, "result")]
    assert lines[-1]["data"] == {"dropped_events": 1}


def test_after_a_failed_write_the_file_is_opened_again_for_the_next_event(tmp_path, read_jsonl):
    files = [_FailingFile(), _FailingFile()]
    writer = EventWriter(lambda: files.pop() if files else open(tmp_path / "events.jsonl", "a", encoding="utf-8"))

    writer.submit("state", {"state_version": 1})
    writer.submit("state", {"state_version": 2})
    writer.submit("state", {"state_version": 3})
    writer.close("result", {})

    lines = read_jsonl(tmp_path / "events.jsonl")
    assert [(line["seq"], line["data"]) for line in lines] == [(0, {"state_version": 3}), (1, {"dropped_events": 2})]


def test_close_returns_within_its_time_limit_when_the_disk_is_stuck(tmp_path, monkeypatch, read_jsonl):
    monkeypatch.setattr(records, "CLOSE_TIMEOUT_SECONDS", SHORT_CLOSE_TIMEOUT_SECONDS)
    gated = _GatedFile(tmp_path / "events.jsonl")
    writer = EventWriter(gated.open)
    writer.submit("state", {"state_version": 1})

    started = time.monotonic()
    writer.close("result", {})
    waited = time.monotonic() - started
    written_when_close_returned = gated.path.exists()
    gated.gate.set()

    # The gate would open by itself only after WAIT_LIMIT_SECONDS: close gave up long before.
    assert waited < WAIT_LIMIT_SECONDS / 2
    assert written_when_close_returned is False
    _wait_until(lambda: gated.path.is_file() and gated.path.read_text(encoding="utf-8").count("\n") == 2)
    assert [line["kind"] for line in read_jsonl(gated.path)] == ["state", "result"]


def test_closing_without_a_last_event_writes_only_what_was_submitted(tmp_path, read_jsonl):
    writer = EventWriter(_appending_to(tmp_path / "probe.jsonl"))

    writer.submit("probe", {"written_by": "check"})
    writer.close()

    assert [(line["seq"], line["kind"]) for line in read_jsonl(tmp_path / "probe.jsonl")] == [(0, "probe")]


def test_closing_twice_and_submitting_after_close_change_nothing(tmp_path, read_jsonl):
    writer = EventWriter(_appending_to(tmp_path / "events.jsonl"))

    writer.close("result", {})
    writer.close("match_end", {})
    writer.submit("state", {})

    assert [line["kind"] for line in read_jsonl(tmp_path / "events.jsonl")] == ["result"]


def test_default_close_time_limit_is_two_seconds():
    assert records.CLOSE_TIMEOUT_SECONDS == 2.0


# -- records are written only ----------------------------------------------------------------------


def _is_record_file_name(node: ast.AST) -> bool:
    """Whether a syntax node is a string constant that is a record file's name or a path ending in one."""
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and bool(RECORD_FILE_NAME.search(node.value))


def _is_record_name_identifier(node: ast.AST) -> bool:
    """Whether a syntax node uses one of the constants or functions that give a record file's name."""
    identifier = node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
    return identifier in RECORD_NAME_IDENTIFIERS


def _called_name(node: ast.AST) -> str | None:
    """The plain or attribute name a call node calls, or None when the node is no such call."""
    if not isinstance(node, ast.Call):
        return None
    return node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)


def _open_mode(call: ast.Call) -> str:
    """The mode an `open` call asks for, when it is written out as a string; otherwise the default, which reads."""
    arguments = [keyword.value for keyword in call.keywords if keyword.arg == "mode"] + list(call.args)
    for argument in arguments:
        if (
            isinstance(argument, ast.Constant)
            and isinstance(argument.value, str)
            and OPEN_MODE.fullmatch(argument.value)
        ):
            return argument.value
    return DEFAULT_OPEN_MODE


def _opens_for_reading(node: ast.AST) -> bool:
    called = _called_name(node)
    if called in READING_METHODS:
        return True
    return called == "open" and any(letter in _open_mode(node) for letter in READING_MODE_LETTERS)


def reads_in_a_module_that_names_record_files(source: str) -> list[int]:
    """The lines of a Python source that open a file for reading, if the source names a record file anywhere."""
    nodes = list(ast.walk(ast.parse(source)))
    if not any(_is_record_file_name(node) or _is_record_name_identifier(node) for node in nodes):
        return []
    return sorted(node.lineno for node in nodes if _opens_for_reading(node))


def _modules_that_may_not_read_records() -> list[Path]:
    return sorted(path for path in AGENT_PACKAGE.rglob("*.py") if path != METRICS_MODULE)


def test_no_agent_module_but_metrics_opens_a_record_file_for_reading():
    found = {
        path.relative_to(AGENT_PACKAGE).as_posix(): lines
        for path in _modules_that_may_not_read_records()
        if (lines := reads_in_a_module_that_names_record_files(path.read_text(encoding="utf-8")))
    }

    assert found == {}


def test_the_scan_recognizes_the_modules_that_name_record_files():
    naming = {
        path.relative_to(AGENT_PACKAGE).as_posix()
        for path in _modules_that_may_not_read_records()
        if any(
            _is_record_file_name(node) or _is_record_name_identifier(node)
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    }

    assert {"core/records.py", "core/record_files.py"} <= naming
    assert "core/config.py" not in naming


@pytest.mark.parametrize(
    "source, lines",
    [
        ('open("runs/20261012/session/events.jsonl")\n', [1]),
        ('handle = open(folder / EVENTS_FILE_NAME, "r")\n', [1]),
        ('import io\n\nio.open("events.2.jsonl", encoding="utf-8")\n', [3]),
        ('from pathlib import Path\n\ntext = Path("meta.json").read_text()\n', [3]),
        ('data = (folder / "metrics.json").read_bytes()\n', [1]),
        ('with open("run.jsonl", "r+") as handle:\n    pass\n', [1]),
        ("with (folder / meta_file_name(2)).open() as handle:\n    pass\n", [1]),
        ('NAME = "meta.3.json"\n\n\ndef read(path, mode):\n    return open(path, mode)\n', [5]),
        ('NAME = "events.jsonl"\n\n\ndef read(path):\n    return open(path, mode="rb")\n', [5]),
    ],
    ids=[
        "open with the default mode",
        "open with r through a name constant",
        "io.open",
        "read_text",
        "read_bytes",
        "r+",
        "Path.open through the naming function",
        "a mode that is not a constant",
        "the mode keyword",
    ],
)
def test_the_scan_finds_each_way_of_reading_a_record(source, lines):
    assert reads_in_a_module_that_names_record_files(source) == lines


@pytest.mark.parametrize(
    "source",
    [
        'open("events.jsonl", "a")\n',
        'open(folder / events_file_name(2), "x", encoding="utf-8")\n',
        'with (folder / "meta.json").open("w") as handle:\n    pass\n',
        'open("run.jsonl", mode="ab")\n',
        'import os\n\nnames = [name for name in os.listdir(folder) if name != "events.jsonl"]\n',
        'import tomllib\n\nwith open("config/agent.toml", "rb") as handle:\n    config = tomllib.load(handle)\n',
        '"""Events go to `events.jsonl`, which nothing here reads."""\n\ntext = open("notes.txt").read()\n',
    ],
    ids=[
        "append",
        "exclusive creation",
        "Path.open for writing",
        "the mode keyword for appending",
        "listing a folder",
        "reading a file in a module that names no record",
        "a record named only in a docstring",
    ],
)
def test_the_scan_allows_writing_listing_and_reading_other_files(source):
    assert reads_in_a_module_that_names_record_files(source) == []
