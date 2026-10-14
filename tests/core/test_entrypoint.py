"""`python -m agent`: the start modes, the run lines and the single-instance lock.

The official client and the runtime loop are replaced by stand-ins, so nothing
here contacts the platform or starts a worker process. Each test runs in a
temporary git repository that stands in for the agent's own.
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

import agent.__main__ as agent_main
from agent.agent import create_agent
from altruagent.agent_loader import load_agent_factory
from altruagent.errors import AuthenticationError, ConfigurationError, PlatformError
from altruagent.official import OfficialAgentError, registration_wait_message
from altruagent.supervisor import (
    DEFAULT_TOURNAMENT_POLL_SECONDS,
    REGISTRATION_RETRY_SECONDS,
    TESTING,
    TOURNAMENT,
    WAITING_MESSAGE,
    run_tournament_forever,
)
from ucla_agent.core.dispatch import Dispatcher
from ucla_agent.core.secrets import REDACTED

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
DISPATCHER_FACTORY_SPEC = "agent.agent:create_agent"
# Put together at run time, so that this file holds no literal the tracked-file scan would report.
AGENT_KEY = "eak_" + "live_" + "abcdefgh12345678"
LOADED_KEY = "jev-4f9c2d7e1a6b8053"
REGISTRATION_INCOMPLETE = "registration_incomplete"
RULES_STEP = "rules"
# Polls of the runtime loop that reach the second check of an incomplete registration: the first poll checks, the
# next ones wait out the retry pause, and the last one checks again.
POLLS_TO_THE_SECOND_CHECK = int(REGISTRATION_RETRY_SECONDS // DEFAULT_TOURNAMENT_POLL_SECONDS) + 1


class _OfficialStub:
    """Stands in for the platform client: accepts the sign-in and lists no assignment unless told to fail."""

    control_url = "https://control.example.test"

    def __init__(self, sign_in_error: Exception | None, assignments_error: Exception | None) -> None:
        self.sign_in_error = sign_in_error
        self.assignments_error = assignments_error
        self.closed = False
        self.assignment_calls = 0

    def authenticate(self) -> None:
        if self.sign_in_error is not None:
            raise self.sign_in_error

    def assignments(self) -> list:
        self.assignment_calls += 1
        if self.assignments_error is not None:
            raise self.assignments_error
        return []

    def close(self) -> None:
        self.closed = True


class _Platform:
    """What the entry point did with the platform client and the runtime loop, and how they should behave."""

    def __init__(self) -> None:
        self.clients: list[_OfficialStub] = []
        self.runtime_calls: list[dict] = []
        self.during_runtime = lambda: None
        self.configuration_error: Exception | None = None
        self.sign_in_error: Exception | None = None
        self.assignments_error: Exception | None = None

    def new_client(self) -> _OfficialStub:
        if self.configuration_error is not None:
            raise self.configuration_error
        self.clients.append(_OfficialStub(self.sign_in_error, self.assignments_error))
        return self.clients[-1]

    def run_forever(self, client, *, agent_spec, kinds) -> None:
        self.runtime_calls.append({"client": client, "agent_spec": agent_spec, "kinds": kinds})
        self.during_runtime()


@pytest.fixture
def platform(monkeypatch) -> _Platform:
    """Replaces the official client and the vendored runtime loop with recording stand-ins."""
    stub = _Platform()
    monkeypatch.setattr(agent_main, "OfficialAgentClient", stub.new_client)
    monkeypatch.setattr(agent_main, "run_tournament_forever", stub.run_forever)
    monkeypatch.setattr(agent_main, "_mark", lambda symbol, fallback: fallback)
    return stub


class _Clock:
    """The runtime loop's clock and sleep: sleeping only moves the clock on."""

    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def sleep(self, seconds: float) -> None:
        self.seconds += seconds


def _no_worker_process(**_process_arguments) -> None:
    pytest.fail("no game is assigned, so no worker process may start")


@pytest.fixture
def vendored_runtime_loop(monkeypatch):
    """Puts the vendored runtime loop back in place of the stand-in, on a test clock, for a set number of polls."""

    def run_for(polls: int) -> None:
        clock = _Clock()

        def bounded_loop(official, *, agent_spec, kinds) -> None:
            run_tournament_forever(
                official,
                agent_spec=agent_spec,
                kinds=kinds,
                sleep=clock.sleep,
                now=clock,
                process_factory=_no_worker_process,
                max_iterations=polls,
            )

        monkeypatch.setattr(agent_main, "run_tournament_forever", bounded_loop)

    return run_for


def _registration_incomplete(next_step: str | None) -> OfficialAgentError:
    """The platform's answer to an agent whose event registration is missing a step (`rules`: accept updated Rules)."""
    error = OfficialAgentError(
        "Your event registration isn't complete.", status_code=403, error_code=REGISTRATION_INCOMPLETE
    )
    error.next_step = next_step
    return error


def _run_lines(root: Path, read_jsonl) -> list[dict]:
    return [line for run_path in root.glob("runs/*/run.jsonl") for line in read_jsonl(run_path)]


def _hold_lock(root: Path, process: subprocess.Popen) -> Path:
    lock_path = root / ".agent.lock"
    lock_path.write_text(str(process.pid), encoding="ascii")
    return lock_path


# -- which games one process plays ---------------------------------------------------------------


def test_both_flags_run_the_runtime_once_for_both_kinds_with_our_dispatcher(git_repo, platform):
    assert agent_main.main(["--tournament", "--match"]) == 0

    (call,) = platform.runtime_calls
    assert call["client"] is platform.clients[0]
    assert call["kinds"] == frozenset({TESTING, TOURNAMENT})
    assert call["agent_spec"] == DISPATCHER_FACTORY_SPEC
    assert load_agent_factory(call["agent_spec"]) is create_agent
    assert isinstance(load_agent_factory(call["agent_spec"])(), Dispatcher)


@pytest.mark.parametrize(
    "flags, kinds",
    [(["--match"], {TESTING}), (["--tournament"], {TOURNAMENT}), (["--match", "--tournament"], {TESTING, TOURNAMENT})],
    ids=["match", "tournament", "match then tournament"],
)
def test_each_flag_chooses_the_kinds_of_game(git_repo, platform, flags, kinds):
    assert agent_main.main(flags) == 0

    (call,) = platform.runtime_calls
    assert call["kinds"] == frozenset(kinds)
    assert call["agent_spec"] == DISPATCHER_FACTORY_SPEC


def test_an_agent_override_reaches_the_runtime_and_the_run_record(git_repo, platform, read_jsonl):
    flags = ["--match", "--agent", "ucla_agent.core.dispatch:Dispatcher"]

    assert agent_main.main(flags) == 0

    assert platform.runtime_calls[0]["agent_spec"] == "ucla_agent.core.dispatch:Dispatcher"
    assert _run_lines(git_repo, read_jsonl)[0]["data"]["flags"] == flags


# -- the run record ------------------------------------------------------------------------------


def test_run_started_names_the_commit_the_config_hashes_and_the_flags(git_repo, git, platform, read_jsonl):
    (git_repo / "config" / "models.toml").write_bytes(b"[server]\nport = 11500\n")
    config_files = sorted(path for path in (git_repo / "config").rglob("*") if path.is_file())

    assert agent_main.main(["--tournament", "--match"]) == 0

    started = _run_lines(git_repo, read_jsonl)[0]
    assert started["kind"] == "run_started"
    assert started["t"].endswith("Z")
    assert started["data"]["commit"] == git(git_repo, "rev-parse", "HEAD")
    assert started["data"]["flags"] == ["--tournament", "--match"]
    assert started["data"]["config_hashes"] == {
        path.relative_to(git_repo).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in config_files
    }
    assert len(config_files) == 2


def test_run_started_is_written_before_the_runtime_is_entered(git_repo, platform, read_jsonl):
    kinds_seen = []
    platform.during_runtime = lambda: kinds_seen.extend(line["kind"] for line in _run_lines(git_repo, read_jsonl))

    agent_main.main(["--match"])

    assert kinds_seen == ["run_started"]


def test_after_a_normal_stop_run_stopped_is_the_last_line_and_the_lock_is_gone(git_repo, platform, read_jsonl):
    locks_seen = []
    platform.during_runtime = lambda: locks_seen.append((git_repo / ".agent.lock").read_text(encoding="ascii"))

    assert agent_main.main(["--tournament", "--match"]) == 0

    lines = _run_lines(git_repo, read_jsonl)
    assert [line["kind"] for line in lines] == ["run_started", "run_stopped"]
    assert lines[-1]["data"] == {"exit_code": 0}
    assert locks_seen == [str(os.getpid())]
    assert not (git_repo / ".agent.lock").exists()
    assert platform.clients[0].closed is True


def test_ctrl_c_is_a_normal_stop(git_repo, platform, read_jsonl, capsys):
    def interrupt() -> None:
        raise KeyboardInterrupt

    platform.during_runtime = interrupt

    assert agent_main.main(["--match"]) == 0

    assert "Stopped." in capsys.readouterr().out
    assert [line["kind"] for line in _run_lines(git_repo, read_jsonl)] == ["run_started", "run_stopped"]
    assert not (git_repo / ".agent.lock").exists()


# -- one copy at a time --------------------------------------------------------------------------


def test_a_second_copy_exits_with_code_3_without_contacting_the_platform(git_repo, platform, live_process, capsys):
    lock_path = _hold_lock(git_repo, live_process)

    assert agent_main.main(["--tournament", "--match"]) == 3

    assert capsys.readouterr().out == f"Another copy of the agent is already running (pid {live_process.pid})\n"
    assert platform.clients == []
    assert platform.runtime_calls == []
    assert lock_path.read_text(encoding="ascii") == str(live_process.pid)
    assert not (git_repo / "runs").exists()


def test_check_tournament_runs_while_the_lock_is_held(git_repo, platform, live_process, capsys):
    lock_path = _hold_lock(git_repo, live_process)

    assert agent_main.main(["--check-tournament"]) == 0

    output_lines = capsys.readouterr().out.splitlines()
    assert output_lines[-2:] == [
        f"[ok] Agent ready ({DISPATCHER_FACTORY_SPEC})",
        "[ok] Ready to play Testing and tournament games",
    ]
    assert platform.runtime_calls == []
    assert lock_path.read_text(encoding="ascii") == str(live_process.pid)
    assert not (git_repo / "runs").exists()


def test_a_lock_naming_a_dead_process_is_replaced_and_startup_proceeds(git_repo, platform, exited_process):
    locks_seen = []
    _hold_lock(git_repo, exited_process)
    platform.during_runtime = lambda: locks_seen.append((git_repo / ".agent.lock").read_text(encoding="ascii"))

    assert agent_main.main(["--match"]) == 0

    assert len(platform.runtime_calls) == 1
    assert locks_seen == [str(os.getpid())]


# -- modes that play nothing ---------------------------------------------------------------------


def test_no_mode_prints_the_retired_notice_and_starts_no_run(git_repo, platform, monkeypatch, capsys):
    monkeypatch.delenv("ALTRUAGENT_CLAIM_TOKEN", raising=False)

    assert agent_main.main([]) == 1

    assert "run with --tournament" in capsys.readouterr().out
    assert platform.clients == []
    assert not (git_repo / "runs").exists()
    assert not (git_repo / ".agent.lock").exists()


def test_the_retired_claim_flag_prints_its_notice(git_repo, platform, capsys):
    assert agent_main.main(["--claim", "retired-code"]) == 1

    assert capsys.readouterr().out.startswith("Testing claim codes were retired")
    assert platform.runtime_calls == []


def test_a_leftover_claim_code_in_the_environment_gets_the_same_notice(git_repo, platform, monkeypatch, capsys):
    monkeypatch.setenv("ALTRUAGENT_CLAIM_TOKEN", "retired-code")

    assert agent_main.main([]) == 1

    assert capsys.readouterr().out.startswith("Testing claim codes were retired")
    assert platform.runtime_calls == []


@pytest.mark.parametrize(
    "flags",
    [
        ["--match", "--check-tournament"],
        ["--tournament", "--check-tournament"],
        ["--match", "--claim", "code"],
        ["--agent", "agent.agent"],
    ],
    ids=["match with check", "tournament with check", "match with claim", "agent alone"],
)
def test_conflicting_flags_are_a_usage_error(git_repo, platform, flags):
    with pytest.raises(SystemExit) as stopped:
        agent_main.main(flags)

    assert stopped.value.code == 2
    assert platform.runtime_calls == []


# -- a run that cannot start playing ------------------------------------------------------------


def test_an_unloadable_agent_factory_ends_the_run_before_any_sign_in(git_repo, platform, read_jsonl, capsys):
    assert agent_main.main(["--match", "--agent", "no_such_module_for_this_test"]) == 1

    assert "Startup error: Could not find agent module" in capsys.readouterr().out
    assert platform.clients == []
    assert [line["data"] for line in _run_lines(git_repo, read_jsonl)][-1] == {"exit_code": 1}
    assert not (git_repo / ".agent.lock").exists()


def test_a_missing_key_is_reported_as_a_configuration_error(git_repo, platform, capsys):
    platform.configuration_error = ConfigurationError("ALTRUAGENT_OFFICIAL_AGENT_KEY is not set.")

    assert agent_main.main(["--tournament"]) == 1

    assert "Configuration error: ALTRUAGENT_OFFICIAL_AGENT_KEY is not set." in capsys.readouterr().out
    assert platform.runtime_calls == []


def test_a_refused_sign_in_ends_the_run_without_entering_the_runtime(git_repo, platform, read_jsonl, capsys):
    platform.sign_in_error = OfficialAgentError("The Official Agent Key was not accepted.", status_code=401)

    assert agent_main.main(["--tournament"]) == 1

    assert "Could not connect with your Official Agent Key" in capsys.readouterr().out
    assert platform.runtime_calls == []
    assert platform.clients[0].closed is True
    assert [line["kind"] for line in _run_lines(git_repo, read_jsonl)] == ["run_started", "run_stopped"]


def test_an_unrecoverable_runtime_error_is_reported_and_recorded(git_repo, platform, read_jsonl, capsys):
    def key_revoked() -> None:
        raise AuthenticationError("The Official Agent Key was not accepted.")

    platform.during_runtime = key_revoked

    assert agent_main.main(["--tournament", "--match"]) == 1

    assert "Stopped due to an unrecoverable error" in capsys.readouterr().out
    assert _run_lines(git_repo, read_jsonl)[-1]["data"] == {"exit_code": 1}
    assert not (git_repo / ".agent.lock").exists()


# -- an incomplete event registration ------------------------------------------------------------


def test_an_incomplete_registration_at_startup_prints_the_wait_message_and_keeps_checking(
    git_repo, platform, vendored_runtime_loop, read_jsonl, capsys
):
    refusal = _registration_incomplete(RULES_STEP)
    platform.sign_in_error = refusal
    platform.assignments_error = refusal
    vendored_runtime_loop(POLLS_TO_THE_SECOND_CHECK)

    assert agent_main.main(["--match"]) == 0

    output = capsys.readouterr().out.splitlines()
    assert output == [
        "Playing your test matches only. Add --tournament to also play your tournament games.",
        f"{WAITING_MESSAGE} (Press Ctrl+C to stop.)",
        registration_wait_message(refusal),
    ]
    assert output[-1].startswith("Accept the updated Official Rules on your dashboard (")
    assert platform.clients[0].assignment_calls == 2
    assert [line["data"] for line in _run_lines(git_repo, read_jsonl)][-1] == {"exit_code": 0}


def test_an_incomplete_registration_while_running_keeps_the_run_going(
    git_repo, platform, vendored_runtime_loop, read_jsonl, capsys
):
    refusal = _registration_incomplete(None)
    platform.assignments_error = refusal
    vendored_runtime_loop(POLLS_TO_THE_SECOND_CHECK)

    assert agent_main.main(["--tournament", "--match"]) == 0

    output = capsys.readouterr().out.splitlines()
    assert output == [
        "Connected with your Official Agent Key.",
        "Playing your test matches and tournament games.",
        f"{WAITING_MESSAGE} (Press Ctrl+C to stop.)",
        registration_wait_message(refusal),
    ]
    assert output[-1].startswith("Your event registration isn't complete. Finish it on your dashboard (")
    assert platform.clients[0].assignment_calls == 2
    assert [line["data"] for line in _run_lines(git_repo, read_jsonl)][-1] == {"exit_code": 0}


# -- a key inside an error text ------------------------------------------------------------------


def test_a_key_in_the_text_of_a_sign_in_error_is_redacted_on_the_console(git_repo, platform, capsys):
    platform.sign_in_error = OfficialAgentError(f"The platform refused {AGENT_KEY}.", status_code=401)

    assert agent_main.main(["--tournament"]) == 1

    output = capsys.readouterr().out
    assert AGENT_KEY not in output
    assert f"Could not connect with your Official Agent Key: The platform refused {REDACTED}." in output


def test_a_key_in_the_text_of_a_runtime_error_is_redacted_on_the_console(git_repo, platform, capsys):
    def key_revoked() -> None:
        raise AuthenticationError(f"{AGENT_KEY} was revoked.")

    platform.during_runtime = key_revoked

    assert agent_main.main(["--match"]) == 1

    output = capsys.readouterr().out
    assert AGENT_KEY not in output
    assert f"Stopped due to an unrecoverable error: {REDACTED} was revoked." in output


def test_a_key_in_the_text_of_a_configuration_error_is_redacted_on_the_console(git_repo, platform, capsys):
    platform.configuration_error = ConfigurationError(f"{AGENT_KEY} is not a usable key.")

    assert agent_main.main(["--tournament"]) == 1

    output = capsys.readouterr().out
    assert AGENT_KEY not in output
    assert f"Configuration error: {REDACTED} is not a usable key." in output


def test_a_key_in_the_text_of_a_failed_check_is_redacted_on_the_console(git_repo, platform, capsys):
    platform.sign_in_error = OfficialAgentError(f"The key {AGENT_KEY} was rejected.", status_code=401)

    assert agent_main.main(["--check-tournament"]) == 1

    output = capsys.readouterr().out
    assert AGENT_KEY not in output
    assert output.splitlines()[-1] == f"[FAIL] Official Agent Key rejected: The key {REDACTED} was rejected."


def test_a_key_loaded_from_the_environment_is_redacted_in_a_startup_error(git_repo, platform, monkeypatch, capsys):
    monkeypatch.setenv("JEV_API_KEY", LOADED_KEY)

    assert agent_main.main(["--match", "--agent", f"no_such_module_{LOADED_KEY}"]) == 1

    output = capsys.readouterr().out
    assert LOADED_KEY not in output
    assert output.startswith("Startup error: ") and REDACTED in output


# -- the connection check ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failing_step, error, last_line",
    [
        ("sign_in_error", PlatformError("no route", status_code=None), "[FAIL] Control plane not reachable"),
        ("sign_in_error", OfficialAgentError("refused", status_code=401), "[FAIL] Official Agent Key rejected"),
        ("sign_in_error", OfficialAgentError("busy", status_code=500), "[FAIL] Official agent authentication failed"),
        ("assignments_error", AuthenticationError("rejected"), "[FAIL] Tournament agent session was not accepted"),
        ("assignments_error", PlatformError("down", status_code=500), "[FAIL] Assignment discovery failed"),
        ("configuration_error", ConfigurationError("the key is not set"), "[FAIL] the key is not set"),
    ],
    ids=["unreachable", "key rejected", "sign-in failed", "session rejected", "discovery failed", "no key"],
)
def test_a_failed_check_is_nonzero_and_names_the_step(git_repo, platform, capsys, failing_step, error, last_line):
    setattr(platform, failing_step, error)

    assert agent_main.main(["--check-tournament"]) == 1

    output = capsys.readouterr().out
    assert output.splitlines()[-1].startswith(last_line)
    assert "Ready to play" not in output


def test_the_check_reports_an_agent_factory_that_cannot_be_created(git_repo, platform, capsys):
    assert agent_main.main(["--check-tournament", "--agent", "ucla_agent.core.dispatch:missing_factory"]) == 1

    last_line = capsys.readouterr().out.splitlines()[-1]
    assert last_line.startswith("[FAIL] Agent ucla_agent.core.dispatch:missing_factory could not be created")
    assert platform.clients[0].closed is True


def test_the_check_lists_every_step_in_order_when_all_pass(git_repo, platform, capsys):
    assert agent_main.main(["--check-tournament"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "[ok] Control plane reachable",
        "[ok] Official Agent Key accepted",
        "[ok] Tournament agent authenticated",
        "[ok] Assignment discovery available (0 active assignment(s))",
        f"[ok] Agent ready ({DISPATCHER_FACTORY_SPEC})",
        "[ok] Ready to play Testing and tournament games",
    ]


@pytest.mark.parametrize(
    "encoding, expected",
    [("utf-8", "✓"), ("cp1252", "[ok]")],
    ids=["printable", "not printable"],
)
def test_check_marks_fall_back_when_the_console_cannot_print_them(monkeypatch, encoding, expected):
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding=encoding))

    assert agent_main._mark("✓", "[ok]") == expected


# -- the real command ----------------------------------------------------------------------------


def _python_m_agent(root: Path, *flags: str) -> subprocess.CompletedProcess:
    environment = {**os.environ, "PYTHONPATH": str(SOURCE_ROOT)}
    command = [sys.executable, "-m", "agent", *flags]
    return subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True)


def test_the_command_refuses_a_second_copy_before_any_sign_in(git_repo, live_process):
    _hold_lock(git_repo, live_process)

    completed = _python_m_agent(git_repo, "--tournament", "--match")

    assert completed.returncode == 3
    assert completed.stdout == f"Another copy of the agent is already running (pid {live_process.pid})\n"
    assert not (git_repo / "runs").exists()


def test_the_command_lists_the_start_flags_in_its_help(git_repo):
    completed = _python_m_agent(git_repo, "--help")

    assert completed.returncode == 0
    for flag in ("--tournament", "--match", "--check-tournament", "--agent"):
        assert flag in completed.stdout
    assert "--claim" not in completed.stdout
