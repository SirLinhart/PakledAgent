"""The single-instance lock: at most one keyed copy of the agent per machine.

The lock is a file holding the process id of the copy that is running. A
second copy that finds the id of a live process refuses to start. A file left
behind by a copy that was killed names a process that no longer exists, and
is replaced.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

LOCK_FILE_NAME = ".agent.lock"

# Win32 values used to ask whether a process has exited.
_SYNCHRONIZE = 0x00100000
_WAIT_TIMEOUT = 0x00000102
_ERROR_ACCESS_DENIED = 5
_NO_SIGNAL = 0


class AlreadyRunningError(Exception):
    """Another live copy of the agent holds the lock; `pid` is its process id."""

    def __init__(self, pid: int) -> None:
        super().__init__(f"Another copy of the agent is already running (pid {pid})")
        self.pid = pid


def process_is_alive(pid: int) -> bool:
    """Whether a process with this id exists and has not exited. The process is never signalled."""
    if sys.platform == "win32":
        return _windows_process_is_alive(pid)
    return _posix_process_is_alive(pid)


def _windows_process_is_alive(pid: int) -> bool:
    # os.kill is not an option here: on Windows it terminates the target
    # for any signal number, so the process is asked through its handle.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)

    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        # Usually there is no such process. Being refused a handle means there is one.
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
    try:
        # An exited process still has an object while any handle to it is open;
        # a zero-length wait tells the two apart: it times out only while the process runs.
        return kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def _posix_process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, _NO_SIGNAL)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists and belongs to another user.
        return True
    return True


def _read_holder(lock_path: Path) -> int | None:
    """The process id the lock file names, or None when there is no file or no usable id in it."""
    try:
        text = lock_path.read_text(encoding="ascii", errors="replace").strip()
    except FileNotFoundError:
        return None
    return int(text) if text.isdecimal() and int(text) > 0 else None


def acquire(lock_path: Path) -> None:
    """Take the lock for this process, replacing a lock whose holder is no longer alive.

    Raises `AlreadyRunningError` when the lock names another live process.
    """
    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = _read_holder(lock_path)
            # A lock naming this very process was left by a dead one whose id the system reused.
            if holder is not None and holder != os.getpid() and process_is_alive(holder):
                raise AlreadyRunningError(holder) from None
            lock_path.unlink(missing_ok=True)
            continue
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(str(os.getpid()))
        return


def release(lock_path: Path) -> None:
    """Remove the lock file if it names this process; a lock held by another copy is left alone."""
    if _read_holder(lock_path) == os.getpid():
        lock_path.unlink()
