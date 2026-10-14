"""Fixtures for the core tests: a temporary git repository standing in for the agent's own, other processes,
what a test needs to talk to a server it starts on the loopback interface, and an open match record."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterator

import httpx
import pytest

from altruagent import DecisionContext
from ucla_agent.core import records

AGENT_CONFIG_WITHOUT_MODEL_SERVER = "[models]\nautostart = false\n"
CHILD_LIFETIME_SECONDS = 120
LOOPBACK = "127.0.0.1"
REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_HTTP_TIMEOUT_SECONDS = 5.0
WAIT_LIMIT_SECONDS = 10.0
WAIT_POLL_SECONDS = 0.02
RECORDED_SESSION_ID = "recorded-session"


@pytest.fixture
def git() -> Callable[..., str]:
    """Runs git in a folder and returns its output, independent of the machine's own git settings."""
    environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]

    def run(root: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *identity, *arguments], cwd=root, env=environment, check=True, capture_output=True, text=True
        )
        return completed.stdout.strip()

    return run


@pytest.fixture
def git_repo(run_folder: Path, git: Callable[..., str]) -> Path:
    """The current directory as a git repository with `config/agent.toml` committed."""
    config_path = run_folder / "config" / "agent.toml"
    config_path.parent.mkdir()
    config_path.write_bytes(AGENT_CONFIG_WITHOUT_MODEL_SERVER.encode("utf-8"))
    git(run_folder, "init", "--quiet")
    git(run_folder, "add", "config")
    git(run_folder, "commit", "--quiet", "--message", "initial")
    return run_folder


@pytest.fixture
def checked_project(run_folder: Path, git: Callable[..., str]) -> Path:
    """The current directory as a git repository holding the tracked `config/` and `scripts/scan_secrets.py`.

    Both are copied byte for byte from this repository and committed, so the
    readiness check finds there what it finds in the real project root.
    """
    shutil.copytree(REPO_ROOT / "config", run_folder / "config")
    (run_folder / "scripts").mkdir()
    shutil.copy2(REPO_ROOT / "scripts" / "scan_secrets.py", run_folder / "scripts")
    git(run_folder, "init", "--quiet")
    git(run_folder, "add", "config", "scripts")
    git(run_folder, "commit", "--quiet", "--message", "the tracked configuration")
    return run_folder


@pytest.fixture
def live_process():
    """Another process that stays alive for the whole test."""
    process = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({CHILD_LIFETIME_SECONDS})"])
    yield process
    process.kill()
    process.wait()


@pytest.fixture
def exited_process():
    """A process that has already exited. Holding the object keeps its id from being handed to a new process."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process


@pytest.fixture
def free_port() -> Callable[[], int]:
    """Gives out loopback ports that nothing listens on, chosen by the system."""

    def pick() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((LOOPBACK, 0))
            return probe.getsockname()[1]

    return pick


@pytest.fixture(scope="session")
def local_http() -> Iterator[httpx.Client]:
    """An HTTP client for servers a test starts on this machine; a proxy named in the environment is not used.

    One client serves the whole run: building one costs more time than most of these tests take.
    """
    with httpx.Client(trust_env=False, timeout=LOCAL_HTTP_TIMEOUT_SECONDS) as client:
        yield client


@pytest.fixture
def wait_for() -> Callable[..., None]:
    """Waits until a condition holds: returns as soon as it does and fails the test after the limit."""

    def wait(condition: Callable[[], bool], what: str, limit_seconds: float = WAIT_LIMIT_SECONDS) -> None:
        deadline = time.monotonic() + limit_seconds
        while not condition():
            assert time.monotonic() < deadline, f"{what} did not happen within {limit_seconds} s"
            time.sleep(WAIT_POLL_SECONDS)

    return wait


@pytest.fixture
def match_record(run_folder: Path, read_jsonl: Callable[[Path], list[dict]]) -> Callable[[], list[dict]]:
    """Opens a match record in a temporary folder; the returned function closes it and gives its event lines."""
    context = DecisionContext(
        session_id=RECORDED_SESSION_ID, tournament_id=None, game_type="red_alert", agent_id="agent-1"
    )
    records.start_match(context, None)

    def close_and_read() -> list[dict]:
        records.finish_match({"is_terminal": True})
        (events_path,) = run_folder.glob(f"runs/*/{RECORDED_SESSION_ID}__seat/events.jsonl")
        return read_jsonl(events_path)

    return close_and_read
