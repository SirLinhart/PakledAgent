"""How a match record is opened and closed: one file per attempt, and exactly one ending.

The last section plays whole matches: under the vendored `run_game` with our
dispatcher, and in a child process that exits like one of the runtime's
workers, and checks what the report tool then says about each record.
"""

from __future__ import annotations

import builtins
import hashlib
import importlib
import inspect
import json
import multiprocessing
import os
import re
import subprocess
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.agent import create_agent
from altruagent import WAIT, DecisionContext, GameState, runner
from altruagent.runner import run_game
from ucla_agent.core import records, runtime_hooks

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
VENDORED_PACKAGE = SOURCE_ROOT / "altruagent"
# A line of the vendored package's manifest: <sha256>  <file name>
MANIFEST_LINE = re.compile(r"^([0-9a-f]{64})  (\S+\.py)$", re.MULTILINE)
CHILD_PROCESS_LIMIT_SECONDS = 60
MATCH_DAY = datetime(2026, 10, 12, 18, 30, tzinfo=timezone.utc)
# A process that opens a match record, logs one state and then simply ends, as `python -c` runs it.
EXITING_PROCESS = """
from altruagent import DecisionContext
from ucla_agent.core import records

context = DecisionContext(session_id="exiting-process", tournament_id=None, game_type="red_alert", agent_id="agent-1")
records.start_match(context, None)
records.on_match_end(lambda: records.log("ledger_summary", {"orders_sent": 0}))
records.log_state({"state_version": 7, "phase": "playing", "observation": None})
"""
RESULT = {"is_terminal": True, "status": "completed", "returns": {"agent-1": 1.0}, "termination_reason": "victory"}
EARLIER_ATTEMPT_EVENTS = b'{"seq": 0, "t": "2026-10-12T18:00:00.000Z", "mono_ms": 0, "kind": "state", "data": {}}\n'


def _context(session_id: str = "session-1") -> DecisionContext:
    return DecisionContext(session_id=session_id, tournament_id=None, game_type="red_alert", agent_id="agent-1")


@pytest.fixture
def day_folder(run_folder, monkeypatch) -> Path:
    """The dated folder this test's records go to: the record clock stands still on one day."""
    monkeypatch.setattr(records, "_utc_now", lambda: MATCH_DAY)
    return run_folder / "runs" / "20261012"


@pytest.fixture
def opened_existing_files(monkeypatch) -> list[Path]:
    """Every file that already existed when `open` was called on it during the test."""
    opened: list[Path] = []
    real_open = builtins.open

    def spying_open(file, *arguments, **options):
        if isinstance(file, (str, os.PathLike)) and os.path.exists(file):
            opened.append(Path(file).resolve())
        return real_open(file, *arguments, **options)

    monkeypatch.setattr(builtins, "open", spying_open)
    return opened


def _play_one_attempt(session_id: str = "session-1") -> None:
    records.start_match(_context(session_id), None)
    records.log("state", {"state_version": 1})
    records.finish_match(RESULT)


def _meta(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _earlier_attempts(folder: Path, *file_names: str) -> None:
    folder.mkdir(parents=True)
    for file_name in file_names:
        (folder / file_name).write_bytes(EARLIER_ATTEMPT_EVENTS)


# -- one events file per attempt -------------------------------------------------------------------


def test_the_first_attempt_writes_events_jsonl(day_folder, read_jsonl):
    _play_one_attempt()

    folder = day_folder / "session-1__seat"
    assert sorted(path.name for path in folder.iterdir()) == ["events.jsonl", "meta.json"]
    assert [line["seq"] for line in read_jsonl(folder / "events.jsonl")] == [0, 1]
    assert _meta(folder / "meta.json")["attempt"] == 1


def test_a_resumed_match_writes_events_2_and_never_opens_the_earlier_attempt(
    day_folder, read_jsonl, opened_existing_files
):
    folder = day_folder / "session-1__seat"
    _earlier_attempts(folder, "events.jsonl", "meta.json")

    _play_one_attempt()

    lines = read_jsonl(folder / "events.2.jsonl")
    assert [(line["seq"], line["kind"]) for line in lines] == [(0, "state"), (1, "result")]
    assert _meta(folder / "meta.2.json")["attempt"] == 2
    assert sorted(path.name for path in folder.iterdir()) == [
        "events.2.jsonl",
        "events.jsonl",
        "meta.2.json",
        "meta.json",
    ]
    assert (folder / "events.jsonl").read_bytes() == EARLIER_ATTEMPT_EVENTS
    assert (folder / "meta.json").read_bytes() == EARLIER_ATTEMPT_EVENTS
    assert [path for path in opened_existing_files if path.parent == folder.resolve()] == []


def test_the_spy_reports_an_existing_file_that_is_opened(tmp_path, opened_existing_files):
    existing = tmp_path / "events.jsonl"
    existing.write_bytes(EARLIER_ATTEMPT_EVENTS)

    with open(existing, encoding="utf-8"):
        pass
    with open(tmp_path / "events.2.jsonl", "x", encoding="utf-8"):
        pass

    assert opened_existing_files == [existing.resolve()]


def test_after_a_failed_write_an_attempt_goes_on_in_its_own_file(day_folder):
    folder = day_folder / "session-1__seat"
    _earlier_attempts(folder, "events.jsonl")
    attempt_files = records._AttemptFiles(folder, lambda attempt: {"attempt": attempt})

    with attempt_files.open_events_file() as events_file:
        events_file.write("written before the failure\n")
    with attempt_files.open_events_file() as events_file:
        events_file.write("written after it\n")

    assert sorted(path.name for path in folder.iterdir()) == ["events.2.jsonl", "events.jsonl", "meta.2.json"]
    assert (folder / "events.2.jsonl").read_text(encoding="utf-8").splitlines() == [
        "written before the failure",
        "written after it",
    ]


def test_a_meta_file_that_could_not_be_written_is_written_by_the_next_open(day_folder, monkeypatch):
    folder = day_folder / "session-1__seat"
    failures = [OSError("no space left on device")]
    real_open = builtins.open

    def failing_once_for_meta(file, *arguments, **options):
        if Path(file).name == "meta.json" and failures:
            raise failures.pop()
        return real_open(file, *arguments, **options)

    monkeypatch.setattr(builtins, "open", failing_once_for_meta)
    attempt_files = records._AttemptFiles(folder, lambda attempt: {"attempt": attempt})

    with pytest.raises(OSError, match="no space left"):
        attempt_files.open_events_file()
    attempt_files.open_events_file().close()

    assert sorted(path.name for path in folder.iterdir()) == ["events.jsonl", "meta.json"]
    assert _meta(folder / "meta.json") == {"attempt": 1}


def test_a_third_attempt_writes_events_3(day_folder, read_jsonl):
    folder = day_folder / "session-1__seat"
    _earlier_attempts(folder, "events.jsonl", "meta.json", "events.2.jsonl", "meta.2.json")

    _play_one_attempt()

    assert [line["seq"] for line in read_jsonl(folder / "events.3.jsonl")] == [0, 1]
    assert _meta(folder / "meta.3.json")["attempt"] == 3
    assert (folder / "events.2.jsonl").read_bytes() == EARLIER_ATTEMPT_EVENTS


@pytest.mark.parametrize(
    "present, expected",
    [
        (["events.jsonl", "events.3.jsonl"], ["events.4.jsonl", "meta.4.json"]),
        (["events.jsonl", "meta.2.json"], ["events.3.jsonl", "meta.3.json"]),
        (["events.jsonl", "metrics.json", "notes.txt", "events-backup.jsonl"], ["events.2.jsonl", "meta.2.json"]),
        (["metrics.json"], ["events.jsonl", "meta.json"]),
    ],
    ids=["number taken by an events file", "number taken by a meta file", "other files", "no attempt yet"],
)
def test_the_attempt_number_never_reuses_a_name_in_the_folder(day_folder, present, expected):
    folder = day_folder / "session-1__seat"
    _earlier_attempts(folder, *present)

    _play_one_attempt()

    assert sorted(path.name for path in folder.iterdir()) == sorted([*present, *expected])
    assert all((folder / name).read_bytes() == EARLIER_ATTEMPT_EVENTS for name in present)


def test_a_process_that_loses_the_race_for_an_attempt_takes_the_next_one(day_folder, monkeypatch, read_jsonl):
    folder = day_folder / "session-1__seat"
    _earlier_attempts(folder, "events.jsonl")
    listings = [[]]
    real_listdir = os.listdir
    monkeypatch.setattr(records.os, "listdir", lambda path: listings.pop() if listings else real_listdir(path))

    _play_one_attempt()

    assert (folder / "events.jsonl").read_bytes() == EARLIER_ATTEMPT_EVENTS
    assert [line["seq"] for line in read_jsonl(folder / "events.2.jsonl")] == [0, 1]


@pytest.mark.parametrize(
    "session_id, folder_name",
    [
        ("6f1c2a9e-77b0-4d3c-9a51-0c8f3e2b1d44", "6f1c2a9e-77b0-4d3c-9a51-0c8f3e2b1d44"),
        ("../../outside", "______outside"),
        ("a/b\\c:d", "a_b_c_d"),
        ("", "unknown-session"),
    ],
    ids=["platform id", "parent folders", "separators", "empty"],
)
def test_the_match_folder_is_always_directly_inside_the_dated_folder(day_folder, run_folder, session_id, folder_name):
    _play_one_attempt(session_id)

    assert [path.name for path in day_folder.iterdir()] == [f"{folder_name}__seat"]
    assert (day_folder / f"{folder_name}__seat" / "events.jsonl").is_file()
    assert sorted(path.name for path in run_folder.iterdir()) == ["runs"]


# -- closing a record with its result --------------------------------------------------------------


@pytest.fixture
def events(day_folder, read_jsonl):
    """Reads the event lines of this test's first attempt at `session-1`."""
    return lambda: read_jsonl(day_folder / "session-1__seat" / "events.jsonl")


def _summary_logger(name: str):
    return lambda: records.log("ledger_summary", {"logged_by": name})


def test_the_result_event_holds_the_result_as_received_the_end_time_and_the_dropped_count(day_folder, events):
    records.start_match(_context(), None)

    records.finish_match(RESULT)

    (line,) = events()
    assert line["kind"] == "result"
    assert line["data"] == {"result": RESULT, "ended_at": "2026-10-12T18:30:00.000Z", "dropped_events": 0}


def test_callbacks_run_in_registration_order_before_the_result(day_folder, events):
    records.start_match(_context(), None)
    for name in ("first", "second", "third"):
        records.on_match_end(_summary_logger(name))

    records.finish_match(RESULT)

    lines = events()
    assert [line["kind"] for line in lines] == ["ledger_summary", "ledger_summary", "ledger_summary", "result"]
    assert [line["data"].get("logged_by") for line in lines[:3]] == ["first", "second", "third"]


def test_a_callback_that_raises_does_not_stop_the_next_callback_or_the_result(day_folder, events):
    def failing_summary() -> None:
        raise RuntimeError("the ledger is in a bad state")

    records.start_match(_context(), None)
    records.on_match_end(_summary_logger("first"))
    records.on_match_end(failing_summary)
    records.on_match_end(_summary_logger("last"))

    records.finish_match(RESULT)

    lines = events()
    assert [line["kind"] for line in lines] == ["ledger_summary", "error", "ledger_summary", "result"]
    assert lines[1]["data"] == {
        "where": "match_end_callback",
        "type": "handled",
        "exception": "RuntimeError",
        "message": "the ledger is in a bad state",
    }
    assert lines[2]["data"] == {"logged_by": "last"}
    assert lines[3]["data"]["result"] == RESULT


def test_callbacks_run_once_however_often_the_match_is_finished(day_folder, events):
    calls = []
    records.start_match(_context(), None)
    records.on_match_end(lambda: calls.append("summary"))

    for _ in range(3):
        records.finish_match(RESULT)

    assert calls == ["summary"]
    assert [line["kind"] for line in events()] == ["result"]


def test_a_failing_callback_does_not_raise_when_no_record_is_open(run_folder):
    calls = []

    def failing_summary() -> None:
        raise RuntimeError("no record to write to")

    records.on_match_end(failing_summary)
    records.on_match_end(lambda: calls.append("next"))

    records.finish_match(RESULT)

    assert calls == ["next"]
    assert not (run_folder / "runs").exists()


# -- the runtime's result reaches the record -------------------------------------------------------


def _last_state() -> GameState:
    return GameState.from_mcp_state({"session_id": "session-1", "state_version": 4310, "phase": "finished"})


def test_start_match_hooks_the_runtimes_final_state_function_to_the_record(day_folder, events):
    original = runner._terminal_game_state
    records.start_match(_context(), None)

    final_state = runner._terminal_game_state(_last_state(), RESULT)

    (line,) = events()
    assert runner._terminal_game_state is not original
    assert (line["kind"], line["data"]["result"]) == ("result", RESULT)
    assert final_state == original(_last_state(), RESULT)


def test_calling_the_hooked_function_twice_writes_one_result(day_folder, events):
    records.start_match(_context(), None)
    records.on_match_end(_summary_logger("ledger"))

    runner._terminal_game_state(_last_state(), RESULT)
    runner._terminal_game_state(_last_state(), {"returns": {"agent-1": 0.0}})

    lines = events()
    assert [line["kind"] for line in lines] == ["ledger_summary", "result"]
    assert lines[-1]["data"]["result"] == RESULT


def test_a_runner_without_the_function_is_recorded_as_one_handled_error_and_play_goes_on(
    day_folder, events, monkeypatch
):
    without_the_function = types.ModuleType(runner.__name__)
    for name, value in vars(runner).items():
        if name != "_terminal_game_state":
            setattr(without_the_function, name, value)
    monkeypatch.setattr(runtime_hooks, "runner", without_the_function)

    records.start_match(_context(), None)
    records.log_state({"state_version": 5, "phase": "playing", "observation": None})
    records._finalize_at_process_exit()

    lines = events()
    assert [line["kind"] for line in lines] == ["error", "state", "match_end"]
    assert lines[0]["data"]["where"] == "runtime_hooks"
    assert lines[0]["data"]["type"] == "handled"
    assert "_terminal_game_state" in lines[0]["data"]["message"]
    assert not hasattr(without_the_function, "_terminal_game_state")


# -- a process that exits without a result ---------------------------------------------------------


@pytest.fixture
def registered_finalizers(monkeypatch) -> dict[str, list]:
    """What `start_match` hands to `atexit` and to multiprocessing's finalizers, captured instead of registered."""
    captured: dict[str, list] = {"atexit": [], "multiprocessing": []}

    def capture_finalize(owner, callback, exitpriority):
        captured["multiprocessing"].append((owner, callback, exitpriority))

    monkeypatch.setattr(records, "_exit_finalizer_registered", False)
    monkeypatch.setattr(records.atexit, "register", captured["atexit"].append)
    monkeypatch.setattr(records.multiprocessing_util, "Finalize", capture_finalize)
    return captured


def test_start_match_registers_one_finalizer_with_atexit_and_with_multiprocessing(run_folder, registered_finalizers):
    records.start_match(_context(), None)
    records.start_match(_context(), None)

    (at_interpreter_exit,) = registered_finalizers["atexit"]
    ((owner, at_worker_exit, exit_priority),) = registered_finalizers["multiprocessing"]
    assert at_interpreter_exit is at_worker_exit
    assert owner is None and exit_priority >= 0


def test_the_finalizer_is_registered_once_per_process(run_folder, monkeypatch, registered_finalizers):
    records.start_match(_context("first-match"), None)
    first_record = records._process
    monkeypatch.setattr(records, "_process", records._ProcessRecord())
    records.start_match(_context("second-match"), None)
    records.finish_match(RESULT)
    first_record.writer.close()

    assert len(registered_finalizers["atexit"]) == 1
    assert len(registered_finalizers["multiprocessing"]) == 1


def test_the_finalizer_writes_one_match_end_after_the_callbacks_and_nothing_the_second_time(
    day_folder, events, registered_finalizers
):
    records.start_match(_context(), None)
    records.on_match_end(_summary_logger("first"))
    records.on_match_end(_summary_logger("second"))
    records.log_state({"state_version": 4310, "phase": "playing", "observation": None})
    (finalizer,) = registered_finalizers["atexit"]

    finalizer()
    after_the_first_run = (day_folder / "session-1__seat" / "events.jsonl").read_bytes()
    finalizer()

    lines = events()
    assert [line["kind"] for line in lines] == ["state", "ledger_summary", "ledger_summary", "match_end"]
    assert [line["data"].get("logged_by") for line in lines[1:3]] == ["first", "second"]
    assert lines[-1]["data"] == {
        "reason": "process_exit",
        "last_state_version": 4310,
        "last_phase": "playing",
        "dropped_events": 0,
    }
    assert (day_folder / "session-1__seat" / "events.jsonl").read_bytes() == after_the_first_run


def test_the_finalizer_writes_nothing_after_the_result(day_folder, events, registered_finalizers):
    calls = []
    records.start_match(_context(), None)
    records.on_match_end(lambda: calls.append("summary"))
    records.finish_match(RESULT)
    (finalizer,) = registered_finalizers["atexit"]

    finalizer()

    assert calls == ["summary"]
    assert [line["kind"] for line in events()] == ["result"]


def test_the_finalizer_names_no_state_when_none_was_seen(day_folder, events, registered_finalizers):
    records.start_match(_context(), None)
    (finalizer,) = registered_finalizers["atexit"]

    finalizer()

    (line,) = events()
    assert (line["kind"], line["data"]["last_state_version"], line["data"]["last_phase"]) == ("match_end", None, None)


def test_the_finalizer_does_nothing_in_a_process_that_opened_no_record(run_folder):
    calls = []
    records.on_match_end(lambda: calls.append("summary"))

    records._finalize_at_process_exit()

    assert calls == []
    assert not (run_folder / "runs").exists()


def test_a_process_that_just_exits_leaves_a_record_ending_in_match_end(run_folder, read_jsonl):
    environment = {**os.environ, "PYTHONPATH": str(SOURCE_ROOT)}

    completed = subprocess.run(
        [sys.executable, "-c", EXITING_PROCESS], cwd=run_folder, env=environment, capture_output=True, text=True
    )

    (events_path,) = run_folder.glob("runs/*/exiting-process__seat/events.jsonl")
    lines = read_jsonl(events_path)
    assert (completed.returncode, completed.stderr) == (0, "")
    assert [line["kind"] for line in lines] == ["state", "ledger_summary", "match_end"]
    assert lines[-1]["data"]["reason"] == "process_exit"
    assert lines[-1]["data"]["last_state_version"] == 7


# -- a whole match under the vendored runtime, a worker-style process, and the report ---------------


def _red_alert_state(*, tick: int, finished: bool) -> GameState:
    return GameState.from_mcp_state(
        {
            "session_id": "session-1",
            "game_type": "red_alert",
            "state_version": tick,
            "phase": "finished" if finished else "playing",
            "is_terminal": finished,
            "is_current_actor": not finished,
            "pacing": {"mode": "realtime"},
            "legal_actions": {"actions": [], "state_version": tick},
        }
    )


class _RedAlertGameStub:
    """A Red Alert match that is over once the agent has been asked for one move."""

    def __init__(self) -> None:
        self.moves_asked_while_playing = 0

    def get_state(self) -> GameState:
        return _red_alert_state(tick=100, finished=False)

    def wait_for_update(self, **_since) -> GameState:
        self.moves_asked_while_playing += 1
        return _red_alert_state(tick=105, finished=True)

    def get_result(self) -> dict:
        return RESULT


@pytest.fixture
def match_played_to_its_result(day_folder) -> Path:
    """The folder of a match the vendored `run_game` played to its end with our dispatcher."""
    run_game(_RedAlertGameStub(), _context(), create_agent())
    return day_folder / "session-1__seat"


@pytest.fixture
def match_left_by_an_exited_worker(run_folder, monkeypatch) -> Path:
    """The folder of a match whose worker-style child process returned without a result."""
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    child = importlib.import_module("worker_style_child")
    target = child.open_a_record_log_three_events_and_return
    process = multiprocessing.get_context("spawn").Process(target=target, args=(str(run_folder),))
    process.start()
    process.join(CHILD_PROCESS_LIMIT_SECONDS)
    assert process.exitcode == 0
    (folder,) = run_folder.glob(f"runs/*/{child.SESSION_ID}__seat")
    return folder


def test_the_vendored_runner_has_the_function_the_hook_wraps_with_two_positional_arguments():
    parameters = inspect.signature(runner._terminal_game_state).parameters.values()

    assert callable(runner._terminal_game_state)
    assert [parameter.kind for parameter in parameters] == [inspect.Parameter.POSITIONAL_OR_KEYWORD] * 2


def test_run_game_with_our_dispatcher_ends_the_record_with_ledger_summary_then_result(day_folder, events):
    game = _RedAlertGameStub()

    final_state = run_game(game, _context(), create_agent())

    lines = events()
    assert game.moves_asked_while_playing == 1
    assert [line["kind"] for line in lines[-2:]] == ["ledger_summary", "result"]
    assert lines[-2]["data"]["orders_sent"] == 0
    assert lines[-1]["data"]["result"] == RESULT
    assert (final_state.is_terminal, final_state.returns, final_state.termination_reason) == (
        True,
        {"agent-1": 1.0},
        "victory",
    )


def test_run_game_returns_the_same_value_with_and_without_the_hook(day_folder):
    original = runner._terminal_game_state
    without_the_hook = run_game(_RedAlertGameStub(), _context(), lambda state, context: WAIT)
    still_unhooked = runner._terminal_game_state is original

    with_the_hook = run_game(_RedAlertGameStub(), _context(), create_agent())

    assert still_unhooked and runner._terminal_game_state is not original
    assert with_the_hook == without_the_hook


def test_a_worker_style_child_process_leaves_its_three_events_and_a_match_end(
    match_left_by_an_exited_worker, read_jsonl
):
    lines = read_jsonl(match_left_by_an_exited_worker / "events.jsonl")

    assert [(line["seq"], line["kind"]) for line in lines] == [
        (0, "state"),
        (1, "state"),
        (2, "state"),
        (3, "match_end"),
    ]
    assert [line["data"]["state_version"] for line in lines[:3]] == [1, 2, 3]
    assert lines[-1]["data"]["reason"] == "process_exit"
    assert json.loads((match_left_by_an_exited_worker / "meta.json").read_text(encoding="utf-8"))["attempt"] == 1


def test_the_report_gives_a_win_for_the_finished_match_and_no_result_for_the_exited_worker(
    match_played_to_its_result, match_left_by_an_exited_worker, run_folder, run_script
):
    completed = run_script(
        run_folder, "report.py", str(match_played_to_its_result), str(match_left_by_an_exited_worker)
    )

    finished = json.loads((match_played_to_its_result / "metrics.json").read_text(encoding="utf-8"))
    exited = json.loads((match_left_by_an_exited_worker / "metrics.json").read_text(encoding="utf-8"))
    assert (completed.returncode, completed.stderr) == (0, "")
    assert (finished["outcome"], finished["termination_reason"]) == ("win", "victory")
    assert (exited["outcome"], exited["termination_reason"]) == ("no_result", "unknown")
    assert (finished["orders_sent"], exited["orders_sent"]) == (0, None)


def test_no_vendored_file_differs_from_its_manifest_hash_after_the_hook_was_used(day_folder):
    run_game(_RedAlertGameStub(), _context(), create_agent())
    upstream_note = (VENDORED_PACKAGE / "UPSTREAM.md").read_text(encoding="utf-8")
    manifest = {name: sha256 for sha256, name in MANIFEST_LINE.findall(upstream_note)}

    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(VENDORED_PACKAGE.glob("*.py"))
    }

    assert hashes == manifest
    assert "runner.py" in manifest and len(manifest) == 17
