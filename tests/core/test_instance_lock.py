"""The single-instance lock file and the process liveness check behind it."""

from __future__ import annotations

import os
import sys

import pytest

from ucla_agent.core import instance_lock
from ucla_agent.core.instance_lock import AlreadyRunningError, acquire, process_is_alive, release

# Larger than any id an operating system hands out, yet still a valid 32-bit process id argument.
BEYOND_ANY_PROCESS_ID = 2**31 - 4


@pytest.fixture
def lock_path(tmp_path):
    return tmp_path / instance_lock.LOCK_FILE_NAME


# -- process_is_alive ------------------------------------------------------------------------------


def test_this_process_is_alive():
    assert process_is_alive(os.getpid()) is True


def test_a_running_child_is_alive_and_survives_being_asked(live_process):
    assert process_is_alive(live_process.pid) is True
    assert process_is_alive(live_process.pid) is True
    assert live_process.poll() is None


def test_an_exited_child_is_not_alive(exited_process):
    assert process_is_alive(exited_process.pid) is False


def test_an_id_no_process_has_is_not_alive():
    assert process_is_alive(BEYOND_ANY_PROCESS_ID) is False


@pytest.mark.parametrize(
    "kill_outcome, expected",
    [(None, True), (ProcessLookupError(), False), (PermissionError(), True)],
    ids=["signal accepted", "no such process", "owned by another user"],
)
def test_posix_check_asks_with_signal_zero(monkeypatch, kill_outcome, expected):
    asked = []

    def fake_kill(pid, signal_number):
        asked.append((pid, signal_number))
        if kill_outcome is not None:
            raise kill_outcome

    monkeypatch.setattr(os, "kill", fake_kill)

    assert instance_lock._posix_process_is_alive(4242) is expected
    assert asked == [(4242, 0)]


def test_platforms_other_than_windows_use_the_posix_check(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(instance_lock, "_posix_process_is_alive", lambda pid: pid == 77)

    assert process_is_alive(77) is True
    assert process_is_alive(78) is False


# -- acquire ---------------------------------------------------------------------------------------


def test_acquire_writes_this_process_id(lock_path):
    acquire(lock_path)

    assert lock_path.read_text(encoding="ascii") == str(os.getpid())


def test_a_lock_naming_a_live_process_refuses_a_second_copy(lock_path, live_process):
    lock_path.write_text(str(live_process.pid), encoding="ascii")

    with pytest.raises(AlreadyRunningError) as raised:
        acquire(lock_path)

    assert raised.value.pid == live_process.pid
    assert str(raised.value) == f"Another copy of the agent is already running (pid {live_process.pid})"
    assert lock_path.read_text(encoding="ascii") == str(live_process.pid)


def test_a_lock_naming_a_dead_process_is_replaced(lock_path, exited_process):
    lock_path.write_text(str(exited_process.pid), encoding="ascii")

    acquire(lock_path)

    assert lock_path.read_text(encoding="ascii") == str(os.getpid())


def test_a_lock_naming_this_process_is_replaced(lock_path):
    lock_path.write_text(str(os.getpid()), encoding="ascii")

    acquire(lock_path)

    assert lock_path.read_text(encoding="ascii") == str(os.getpid())


@pytest.mark.parametrize("content", ["", "not a pid", "0", "-5", "12 34"], ids=repr)
def test_a_lock_without_a_usable_process_id_is_replaced(lock_path, content):
    lock_path.write_text(content, encoding="ascii")

    acquire(lock_path)

    assert lock_path.read_text(encoding="ascii") == str(os.getpid())


def test_surrounding_whitespace_in_the_lock_is_ignored(lock_path, live_process):
    lock_path.write_text(f"  {live_process.pid}\n", encoding="ascii")

    with pytest.raises(AlreadyRunningError):
        acquire(lock_path)


# -- release ---------------------------------------------------------------------------------------


def test_release_removes_the_lock_this_process_holds(lock_path):
    acquire(lock_path)

    release(lock_path)

    assert not lock_path.exists()


def test_release_leaves_a_lock_held_by_another_copy(lock_path, live_process):
    lock_path.write_text(str(live_process.pid), encoding="ascii")

    release(lock_path)

    assert lock_path.read_text(encoding="ascii") == str(live_process.pid)


def test_release_without_a_lock_file_does_nothing(lock_path):
    release(lock_path)

    assert not lock_path.exists()


def test_the_lock_can_be_taken_again_after_release(lock_path):
    acquire(lock_path)
    release(lock_path)

    acquire(lock_path)

    assert lock_path.read_text(encoding="ascii") == str(os.getpid())
