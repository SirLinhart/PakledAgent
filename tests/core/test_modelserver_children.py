"""Child processes of the model server: started with their output in a file, ended on request, and never left behind."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from ucla_agent.core.instance_lock import process_is_alive
from ucla_agent.core.modelserver import children

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
SLEEPER = [sys.executable, "-c", "import time; time.sleep(120)"]
# A child that starts a child of its own, writes that one's process id to its output and stays alive.
PARENT_OF_A_SLEEPER = [
    sys.executable,
    "-c",
    "import subprocess, sys, time\n"
    "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "print(grandchild.pid, flush=True)\n"
    "time.sleep(120)\n",
]
# Another process that starts a child through `children.spawn`, reports both process ids and stays alive.
SPAWNING_HELPER = (
    "import os, sys, time\n"
    "from pathlib import Path\n"
    "from ucla_agent.core.modelserver import children\n"
    "child = children.spawn([sys.executable, '-c', 'import time; time.sleep(120)'], Path(sys.argv[1]))\n"
    "print(os.getpid(), child.pid, flush=True)\n"
    "time.sleep(120)\n"
)
ORPHAN_LIMIT_SECONDS = 5.0
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="a job object is how Windows ties a child to us")


@pytest.fixture
def spawn(tmp_path):
    """Starts children through `children.spawn` and ends every one of them after the test."""
    started = []

    def start(command, log_name="child.log"):
        child = children.spawn(command, tmp_path / "logs" / log_name)
        started.append(child)
        return child

    yield start
    for child in started:
        child.terminate()


def _log_text(tmp_path: Path, log_name: str = "child.log") -> str:
    return (tmp_path / "logs" / log_name).read_text(encoding="utf-8")


# -- starting and ending ---------------------------------------------------------------------------


def test_a_spawned_child_runs_until_it_is_terminated(spawn):
    child = spawn(SLEEPER)

    assert child.is_running()
    assert process_is_alive(child.pid)

    child.terminate()

    assert not child.is_running()
    assert not process_is_alive(child.pid)


def test_terminating_again_or_after_the_child_exited_changes_nothing(spawn, wait_for):
    child = spawn([sys.executable, "-c", "raise SystemExit(4)"])
    wait_for(lambda: not child.is_running(), "the child exiting by itself")

    child.terminate()
    child.terminate()

    assert not child.is_running()


def test_the_child_s_output_and_errors_are_appended_to_the_log_file(spawn, tmp_path, wait_for):
    script = "import sys; print('to standard output'); print('to standard error', file=sys.stderr)"
    first = spawn([sys.executable, "-c", script])
    wait_for(lambda: not first.is_running(), "the first child exiting")
    second = spawn([sys.executable, "-c", "print('a later start')"])
    wait_for(lambda: not second.is_running(), "the second child exiting")

    lines = _log_text(tmp_path).splitlines()

    assert sorted(lines[:2]) == ["to standard error", "to standard output"]
    assert lines[2:] == ["a later start"]


def test_the_log_folder_is_created_when_it_is_missing(spawn, tmp_path, wait_for):
    child = spawn([sys.executable, "-c", "print('ready')"], "nested/role.log")
    wait_for(lambda: not child.is_running(), "the child exiting")

    assert _log_text(tmp_path, "nested/role.log") == "ready\n"


def test_a_program_that_does_not_exist_cannot_be_spawned(tmp_path):
    with pytest.raises(OSError):
        children.spawn([str(tmp_path / "no-such-llama-server.exe")], tmp_path / "child.log")


# -- nothing is left behind ------------------------------------------------------------------------


@windows_only
def test_terminating_a_child_also_ends_what_it_started(spawn, tmp_path, wait_for):
    child = spawn(PARENT_OF_A_SLEEPER)
    wait_for(lambda: _log_text(tmp_path).strip().isdecimal(), "the child reporting its own child")
    grandchild_pid = int(_log_text(tmp_path))
    assert process_is_alive(grandchild_pid)

    child.terminate()

    assert not process_is_alive(grandchild_pid)


def test_a_child_is_ended_when_the_process_that_spawned_it_is_killed(tmp_path, wait_for):
    environment = {**os.environ, "PYTHONPATH": str(SOURCE_ROOT)}
    command = [sys.executable, "-c", SPAWNING_HELPER, str(tmp_path / "child.log")]
    helper = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, text=True)
    try:
        helper_pid, child_pid = (int(pid) for pid in helper.stdout.readline().split())
        assert process_is_alive(child_pid)

        # Killed, not asked to stop: the helper gets no chance to clean up after itself.
        os.kill(helper_pid, signal.SIGTERM if sys.platform == "win32" else signal.SIGKILL)

        wait_for(lambda: not process_is_alive(child_pid), "the orphaned child being ended", ORPHAN_LIMIT_SECONDS)
    finally:
        helper.kill()
        helper.wait()


@windows_only
def test_a_child_that_cannot_be_tied_to_this_process_never_runs(tmp_path, monkeypatch):
    seen = []

    def fail_to_resume(pid: int) -> None:
        seen.append(pid)
        raise OSError("the thread could not be resumed")

    monkeypatch.setattr(children, "_resume_only_thread", fail_to_resume)

    with pytest.raises(OSError, match="could not be resumed"):
        children.spawn(SLEEPER, tmp_path / "child.log")

    (pid,) = seen
    assert not process_is_alive(pid)


@windows_only
def test_a_process_that_is_gone_has_no_thread_to_resume(exited_process):
    with pytest.raises(OSError, match="has no thread to resume"):
        children._resume_only_thread(exited_process.pid)


@windows_only
def test_a_failed_system_call_is_reported_with_the_system_s_reason():
    job = children._KillOnCloseJob()
    try:
        with pytest.raises(OSError):
            job.assign(0)
    finally:
        job.close()
