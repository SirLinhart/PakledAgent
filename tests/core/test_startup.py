"""One recorded run: the lock, the configuration, the model server and the run lines around the runtime."""

from __future__ import annotations

import hashlib
import os
import socket
import sys
import types
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx
import pytest

from ucla_agent.core import modelserver, startup
from ucla_agent.core.instance_lock import process_is_alive
from ucla_agent.core.modelserver import children
from ucla_agent.core.startup import StartupError, git_commit, run_session

MODEL_SERVER_MODULE = "ucla_agent.core.modelserver"
FLAGS = ["--tournament", "--match"]
# The roles of the test copy of config/models.toml that `stub_models_config` writes, in its order.
STUB_ROLES = ("fast", "strong", "small")


def _run_lines(root, read_jsonl) -> list[dict]:
    return [line for run_path in root.glob("runs/*/run.jsonl") for line in read_jsonl(run_path)]


def _enable_model_server(root, roles: str | None = None) -> None:
    """Turn autostart on; `roles` is the autostart_roles list as the file writes it, None to leave the key out."""
    listed = "" if roles is None else f"autostart_roles = {roles}\n"
    (root / "config" / "agent.toml").write_bytes(f"[models]\nautostart = true\n{listed}".encode("utf-8"))


@pytest.fixture
def model_server(monkeypatch):
    """A stand-in for the model server module that remembers when it was started, with which roles, and stopped."""
    module = types.ModuleType(MODEL_SERVER_MODULE)
    module.events = []
    module.roles = None

    def start(roles):
        module.roles = tuple(roles)
        module.events.append("start")

    module.start = start
    module.stop = lambda: module.events.append("stop")
    monkeypatch.setitem(sys.modules, MODEL_SERVER_MODULE, module)
    return module


# -- a normal run ----------------------------------------------------------------------------------


def test_a_run_writes_started_and_stopped_around_play(git_repo, git, read_jsonl):
    seen_during_play = {}

    def play() -> int:
        seen_during_play["lock"] = (git_repo / ".agent.lock").read_text(encoding="ascii")
        seen_during_play["kinds"] = [line["kind"] for line in _run_lines(git_repo, read_jsonl)]
        return 0

    assert run_session(FLAGS, play) == 0

    started, stopped = _run_lines(git_repo, read_jsonl)
    config_bytes = (git_repo / "config" / "agent.toml").read_bytes()
    assert seen_during_play == {"lock": str(os.getpid()), "kinds": ["run_started"]}
    assert started["kind"] == "run_started" and started["t"].endswith("Z")
    assert started["data"] == {
        "commit": git(git_repo, "rev-parse", "HEAD"),
        "config_hashes": {"config/agent.toml": hashlib.sha256(config_bytes).hexdigest()},
        "flags": FLAGS,
    }
    assert (stopped["kind"], stopped["data"]) == ("run_stopped", {"exit_code": 0})
    assert not (git_repo / ".agent.lock").exists()


def test_the_exit_code_of_play_is_returned_and_recorded(git_repo, read_jsonl):
    assert run_session(["--match"], lambda: 1) == 1

    assert _run_lines(git_repo, read_jsonl)[-1]["data"] == {"exit_code": 1}


def test_config_hashes_have_one_entry_per_file_under_config(git_repo, read_jsonl):
    (git_repo / "config" / "models.toml").write_bytes(b"[server]\nport = 11500\n")
    (git_repo / "config" / "prompts").mkdir()
    (git_repo / "config" / "prompts" / "plan.txt").write_bytes(b"choose a plan")

    run_session(FLAGS, lambda: 0)

    started = _run_lines(git_repo, read_jsonl)[0]
    assert sorted(started["data"]["config_hashes"]) == [
        "config/agent.toml",
        "config/models.toml",
        "config/prompts/plan.txt",
    ]


def test_startup_changes_nothing_under_config(git_repo):
    before = {path: path.read_bytes() for path in (git_repo / "config").rglob("*") if path.is_file()}

    run_session(FLAGS, lambda: 0)

    assert {path: path.read_bytes() for path in (git_repo / "config").rglob("*") if path.is_file()} == before


# -- the single-instance lock ----------------------------------------------------------------------


def test_a_live_copy_keeps_a_second_one_from_starting(git_repo, live_process, capsys):
    played = []
    (git_repo / ".agent.lock").write_text(str(live_process.pid), encoding="ascii")

    exit_code = run_session(FLAGS, lambda: played.append("play") or 0)

    assert exit_code == 3 and played == []
    assert capsys.readouterr().out == f"Another copy of the agent is already running (pid {live_process.pid})\n"
    assert (git_repo / ".agent.lock").read_text(encoding="ascii") == str(live_process.pid)
    assert not (git_repo / "runs").exists()


def test_a_lock_left_by_a_dead_copy_is_replaced(git_repo, exited_process, read_jsonl):
    held_by = []
    (git_repo / ".agent.lock").write_text(str(exited_process.pid), encoding="ascii")

    exit_code = run_session(FLAGS, lambda: held_by.append((git_repo / ".agent.lock").read_text(encoding="ascii")) or 0)

    assert exit_code == 0 and held_by == [str(os.getpid())]
    assert [line["kind"] for line in _run_lines(git_repo, read_jsonl)] == ["run_started", "run_stopped"]
    assert not (git_repo / ".agent.lock").exists()


# -- startup failures ------------------------------------------------------------------------------


def test_a_missing_configuration_stops_startup(git_repo, capsys):
    played = []
    (git_repo / "config" / "agent.toml").unlink()

    exit_code = run_session(FLAGS, lambda: played.append("play") or 0)

    output = capsys.readouterr().out
    assert exit_code == 1 and played == []
    assert output.startswith("Startup error: ") and "Start the agent from the repository root" in output
    assert not (git_repo / "runs").exists() and not (git_repo / ".agent.lock").exists()


def test_a_folder_that_is_not_a_git_repository_stops_startup(run_folder, monkeypatch, capsys):
    played = []
    (run_folder / "config").mkdir()
    (run_folder / "config" / "agent.toml").write_bytes(b"[models]\nautostart = false\n")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(run_folder.parent))

    exit_code = run_session(FLAGS, lambda: played.append("play") or 0)

    assert exit_code == 1 and played == []
    assert capsys.readouterr().out.startswith("Startup error: Could not read the git commit of ")
    assert not (run_folder / "runs").exists() and not (run_folder / ".agent.lock").exists()


def test_git_commit_reads_head(git_repo, git):
    assert git_commit(git_repo) == git(git_repo, "rev-parse", "HEAD")


def test_git_commit_reports_when_git_cannot_be_run(git_repo, monkeypatch):
    def no_git(*_arguments, **_options):
        raise FileNotFoundError("git")

    monkeypatch.setattr(startup.subprocess, "run", no_git)

    with pytest.raises(StartupError) as raised:
        git_commit(git_repo)

    assert "git could not be run" in str(raised.value)


# -- the model server ------------------------------------------------------------------------------


def test_the_model_server_is_not_loaded_when_autostart_is_off(git_repo, monkeypatch):
    monkeypatch.delitem(sys.modules, MODEL_SERVER_MODULE, raising=False)

    assert run_session(FLAGS, lambda: 0) == 0

    assert MODEL_SERVER_MODULE not in sys.modules


def test_the_model_server_runs_from_before_run_started_until_after_run_stopped(git_repo, model_server, read_jsonl):
    _enable_model_server(git_repo)
    kinds_at = {}
    model_server.start = lambda roles: kinds_at.update(
        start=[line["kind"] for line in _run_lines(git_repo, read_jsonl)]
    )
    model_server.stop = lambda: kinds_at.update(stop=[line["kind"] for line in _run_lines(git_repo, read_jsonl)])

    def play() -> int:
        kinds_at["play"] = [line["kind"] for line in _run_lines(git_repo, read_jsonl)]
        return 0

    assert run_session(FLAGS, play) == 0

    assert list(kinds_at) == ["start", "play", "stop"]
    assert kinds_at == {"start": [], "play": ["run_started"], "stop": ["run_started", "run_stopped"]}


def test_a_model_server_that_fails_to_start_ends_the_run_before_it_is_recorded(git_repo, model_server):
    _enable_model_server(git_repo)
    played = []

    def fail_to_start(roles) -> None:
        raise RuntimeError("required model unavailable")

    model_server.start = fail_to_start

    with pytest.raises(RuntimeError, match="required model unavailable"):
        run_session(FLAGS, lambda: played.append("play") or 0)

    assert played == [] and model_server.events == []
    assert not (git_repo / "runs").exists() and not (git_repo / ".agent.lock").exists()


# -- which roles autostart starts ------------------------------------------------------------------


@pytest.mark.parametrize(
    "listed, started",
    [(None, ("strong",)), ('["strong"]', ("strong",)), ('["fast", "small"]', ("fast", "small"))],
    ids=["no list starts strong", "strong", "two roles"],
)
def test_autostart_starts_only_the_listed_roles(git_repo, model_server, listed, started):
    _enable_model_server(git_repo, listed)

    assert run_session(FLAGS, lambda: 0) == 0

    assert model_server.roles == started and model_server.events == ["start", "stop"]


def test_an_empty_autostart_list_starts_nothing_and_does_not_load_the_model_server(git_repo, monkeypatch, read_jsonl):
    _enable_model_server(git_repo, "[]")
    monkeypatch.delitem(sys.modules, MODEL_SERVER_MODULE, raising=False)

    assert run_session(FLAGS, lambda: 0) == 0

    assert MODEL_SERVER_MODULE not in sys.modules
    assert [line["kind"] for line in _run_lines(git_repo, read_jsonl)] == ["run_started", "run_stopped"]


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


@pytest.fixture
def stub_models(git_repo: Path, stub_models_config: Callable[..., Any]) -> Iterator[Any]:
    """`config/models.toml` in the repository: a test copy with the three shipped role names, each run by the stub.

    Gives the copy's ports, the free ones of the `model_ports` fixture. The
    model server is stopped after the test whatever the test did.
    """
    yield stub_models_config(git_repo)
    modelserver.stop()


def _backend_ports(ports: Any) -> dict[str, int]:
    """Each stub role -> the port of its server: the one of its place in the file."""
    return {role: ports.backend_port(index) for index, role in enumerate(STUB_ROLES)}


@pytest.fixture
def launched(monkeypatch) -> list[list[str]]:
    """The command line of every server the model server launches during the test."""
    commands = []
    real_spawn = children.spawn

    def spawn_and_remember(arguments, log_path):
        commands.append(list(arguments))
        return real_spawn(arguments, log_path)

    monkeypatch.setattr(children, "spawn", spawn_and_remember)
    return commands


def test_with_the_stub_server_autostart_runs_exactly_the_strong_stub_and_stops_it_at_shutdown(
    git_repo, stub_models, launched, local_http
):
    _enable_model_server(git_repo, '["strong"]')
    backend_ports = _backend_ports(stub_models)
    seen = {}

    def play() -> int:
        seen["health"] = local_http.get(f"{stub_models.endpoint_url}/health").json()
        seen["strong_pid"] = local_http.get(f"http://127.0.0.1:{backend_ports['strong']}/stub/launch").json()["pid"]
        seen["free_backend_ports"] = [role for role, port in backend_ports.items() if _port_is_free(port)]
        return 0

    assert run_session(FLAGS, play) == 0

    assert seen["health"] == {"roles": {"strong": {"state": "ready"}}}
    assert seen["free_backend_ports"] == ["fast", "small"]
    (command,) = launched
    assert command[command.index("--port") + 1] == str(backend_ports["strong"]) and command[-1] == "--no-mmap"
    assert not process_is_alive(seen["strong_pid"])
    assert all(_port_is_free(port) for port in (stub_models.endpoint, *backend_ports.values()))


def test_an_unknown_autostart_role_is_a_startup_error_naming_it_and_nothing_is_launched(
    git_repo, stub_models, launched, local_http, capsys
):
    _enable_model_server(git_repo, '["strong", "huge"]')
    played = []

    exit_code = run_session(FLAGS, lambda: played.append("play") or 0)

    output = capsys.readouterr().out
    assert exit_code == 1 and played == [] and launched == []
    assert output == "Startup error: config/models.toml has no role huge; its roles are fast, strong, small\n"
    assert not (git_repo / "runs").exists() and not (git_repo / ".agent.lock").exists()
    with pytest.raises(httpx.ConnectError):
        local_http.get(f"{stub_models.endpoint_url}/health")


# -- play ends with an exception -------------------------------------------------------------------


@pytest.mark.parametrize("failure", [RuntimeError("runtime bug"), KeyboardInterrupt()], ids=["error", "ctrl-c"])
def test_shutdown_still_happens_when_play_raises(git_repo, model_server, read_jsonl, failure):
    _enable_model_server(git_repo)

    def play() -> int:
        raise failure

    with pytest.raises(type(failure)):
        run_session(FLAGS, play)

    lines = _run_lines(git_repo, read_jsonl)
    assert [line["kind"] for line in lines] == ["run_started", "run_stopped"]
    assert lines[-1]["data"] == {"exit_code": None}
    assert model_server.events == ["start", "stop"]
    assert not (git_repo / ".agent.lock").exists()
