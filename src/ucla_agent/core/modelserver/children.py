"""Child processes that cannot outlive this process.

A `llama-server` holding tens of gigabytes of GPU memory survives the process
that started it: nothing in an ordinary parent and child relationship ends
the child when the parent is killed. So every child started here is tied to
this process by the operating system itself, which acts however this process
dies.

On Windows that is a job object with kill-on-close. The only handle to the job
is held by this process; when the process dies the handle closes and the
kernel ends every process in the job. The child is created suspended, put in
the job and only then resumed, so it belongs to the job before it runs a
single instruction and there is no moment in which a running child is outside
it. Each child gets a job of its own, which also makes ending one child end
everything that child started.

On Linux the child asks the kernel, before the program starts, for a kill
signal on its parent's death.
"""

from __future__ import annotations

import ctypes
import functools
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Sequence

# How long `terminate` waits for a child that was just killed to be gone.
TERMINATE_WAIT_SECONDS = 10.0
_KILLED_EXIT_CODE = 1
_MILLISECONDS_PER_SECOND = 1000

# winbase.h, winnt.h and tlhelp32.h values.
_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
# A model server child is one process, or two when a launcher starts the real program.
_MAX_LISTED_JOB_MEMBERS = 64
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_SYNCHRONIZE = 0x00100000
_WAIT_TIMEOUT = 0x00000102
_THREAD_SUSPEND_RESUME = 0x0002
_TH32CS_SNAPTHREAD = 0x00000004
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_RESUME_FAILED = 0xFFFFFFFF

# linux/prctl.h
_PR_SET_PDEATHSIG = 1


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _ProcessIdList(ctypes.Structure):
    _fields_ = [
        ("NumberOfAssignedProcesses", ctypes.c_uint32),
        ("NumberOfProcessIdsInList", ctypes.c_uint32),
        ("ProcessIdList", ctypes.c_size_t * _MAX_LISTED_JOB_MEMBERS),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ThreadID", ctypes.c_uint32),
        ("th32OwnerProcessID", ctypes.c_uint32),
        ("tpBasePri", ctypes.c_int32),
        ("tpDeltaPri", ctypes.c_int32),
        ("dwFlags", ctypes.c_uint32),
    ]


class Child:
    """One child process started by `spawn`, and the way to end it."""

    def __init__(self, process: subprocess.Popen, job: _KillOnCloseJob | None) -> None:
        self._process = process
        self._job = job

    @property
    def pid(self) -> int:
        """The child's process id."""
        return self._process.pid

    def is_running(self) -> bool:
        """Whether the child has not exited yet."""
        return self._process.poll() is None

    def terminate(self) -> None:
        """End the child, and on Windows everything it started; returns once it is gone.

        Safe to call again, and for a child that has already exited. Raises
        `TimeoutError` or `subprocess.TimeoutExpired` when something is still
        there after `TERMINATE_WAIT_SECONDS`.
        """
        if self._job is None:
            self._process.kill()
        else:
            self._job.terminate(TERMINATE_WAIT_SECONDS)
        self._process.wait(TERMINATE_WAIT_SECONDS)
        if self._job is not None:
            self._job.close()


def spawn(command: Sequence[str], log_path: Path, env: Mapping[str, str] | None = None) -> Child:
    """Start `command` as a child that the operating system ends when this process dies.

    The child's standard output and error are appended to `log_path`; it gets
    no standard input. `env` is its whole environment, or this process's when
    None. Raises `OSError` when the program cannot be started or cannot be
    tied to this process, in which case no child is left running.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log_file:
        if sys.platform == "win32":
            return _spawn_in_job(command, log_file, env)
        return _spawn_with_parent_death_signal(command, log_file, env)


# -- Windows ---------------------------------------------------------------------------------------


def _spawn_in_job(command: Sequence[str], log_file: BinaryIO, env: Mapping[str, str] | None) -> Child:
    # The job comes first: when it cannot be made, no process has been created yet.
    job = _KillOnCloseJob()
    try:
        return Child(_start_in(job, command, log_file, env), job)
    except OSError:
        job.close()
        raise


def _start_in(
    job: _KillOnCloseJob, command: Sequence[str], log_file: BinaryIO, env: Mapping[str, str] | None
) -> subprocess.Popen:
    """Create the process suspended, put it in `job` and let it run."""
    # A new process group keeps a Ctrl+C typed at the agent's console from reaching
    # the child: it stops when the agent stops it, not in the middle of a request.
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=_CREATE_SUSPENDED | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    try:
        job.assign(process.pid)
        _resume_only_thread(process.pid)
    except OSError:
        # A child that could not be tied to this process must never get to run.
        process.kill()
        process.wait()
        raise
    return process


class _KillOnCloseJob:
    """A Windows job object whose processes the kernel ends when the job's last handle closes.

    The job is anonymous and its handle is not inheritable: a named job could
    be opened by another process and an inherited handle would live on in the
    child, and either would keep the job open after this process has died.
    """

    def __init__(self) -> None:
        self._handle = _checked(_kernel32().CreateJobObjectW(None, None))
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _checked(
            _kernel32().SetInformationJobObject(
                self._handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
            )
        )

    def assign(self, pid: int) -> None:
        """Put the process with this id into the job."""
        process_handle = _checked(_kernel32().OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid))
        try:
            _checked(_kernel32().AssignProcessToJobObject(self._handle, process_handle))
        finally:
            _kernel32().CloseHandle(process_handle)

    def terminate(self, wait_seconds: float) -> None:
        """End every process in the job and wait until each is gone; does nothing once the job is closed."""
        if self._handle is None:
            return
        # Opened before the kill: the kill call returns while its processes are still
        # going down, and by then their ids can no longer be turned into something to wait on.
        members = self._open_members()
        try:
            _checked(_kernel32().TerminateJobObject(self._handle, _KILLED_EXIT_CODE))
            deadline = time.monotonic() + wait_seconds
            for member in members:
                _wait_for_exit(member, deadline)
        finally:
            for member in members:
                _kernel32().CloseHandle(member)

    def _open_members(self) -> list[int]:
        """A handle to wait on for every process in the job right now."""
        listing = _ProcessIdList()
        # The result is deliberately not checked: for a job with more members than
        # the list holds the call reports an error and still fills the list.
        _kernel32().QueryInformationJobObject(
            self._handle, _JOB_OBJECT_BASIC_PROCESS_ID_LIST, ctypes.byref(listing), ctypes.sizeof(listing), None
        )
        process_ids = listing.ProcessIdList[: listing.NumberOfProcessIdsInList]
        handles = [_kernel32().OpenProcess(_SYNCHRONIZE, False, process_id) for process_id in process_ids]
        # A member that exited since it was listed has no process left to open.
        return [handle for handle in handles if handle]

    def close(self) -> None:
        """Give the job's handle back; a process still in the job is ended by that."""
        if self._handle is not None:
            _kernel32().CloseHandle(self._handle)
            self._handle = None


def _wait_for_exit(process_handle: int, deadline: float) -> None:
    """Wait for a process that was just killed; raises `TimeoutError` when it is still there at `deadline`."""
    milliseconds_left = max(0, int((deadline - time.monotonic()) * _MILLISECONDS_PER_SECOND))
    if _kernel32().WaitForSingleObject(process_handle, milliseconds_left) == _WAIT_TIMEOUT:
        raise TimeoutError("A process of a terminated job is still running.")


def _resume_only_thread(pid: int) -> None:
    """Let a process that was created suspended start running.

    Such a process has exactly one thread. `subprocess` has already closed the
    handle to it that process creation returned, so the thread is found by the
    process id in a snapshot of the system's threads and opened again.
    """
    thread_handle = _checked(_kernel32().OpenThread(_THREAD_SUSPEND_RESUME, False, _only_thread_id(pid)))
    try:
        if _kernel32().ResumeThread(thread_handle) == _RESUME_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        _kernel32().CloseHandle(thread_handle)


def _only_thread_id(pid: int) -> int:
    snapshot = _kernel32().CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if snapshot == _INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    entry = _ThreadEntry()
    entry.dwSize = ctypes.sizeof(_ThreadEntry)
    try:
        found = _kernel32().Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32OwnerProcessID == pid:
                return entry.th32ThreadID
            found = _kernel32().Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        _kernel32().CloseHandle(snapshot)
    raise OSError(f"The suspended process {pid} has no thread to resume.")


def _checked(result: Any) -> Any:
    """The handle or flag a Win32 call returned; raises `OSError` with the system's reason when it is null."""
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    return result


@functools.cache
def _kernel32() -> Any:
    """kernel32 with the signatures of the calls used here.

    Handles are pointer-sized: without a declared signature ctypes would pass
    and return them as 32-bit integers and cut them off on 64-bit Windows.
    """
    handle, dword, flag, pointer = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p
    signatures = {
        "CreateJobObjectW": (handle, (pointer, ctypes.c_wchar_p)),
        "SetInformationJobObject": (flag, (handle, ctypes.c_int, pointer, dword)),
        "QueryInformationJobObject": (flag, (handle, ctypes.c_int, pointer, dword, pointer)),
        "AssignProcessToJobObject": (flag, (handle, handle)),
        "TerminateJobObject": (flag, (handle, ctypes.c_uint)),
        "OpenProcess": (handle, (dword, flag, dword)),
        "OpenThread": (handle, (dword, flag, dword)),
        "ResumeThread": (dword, (handle,)),
        "CreateToolhelp32Snapshot": (handle, (dword, dword)),
        "Thread32First": (flag, (handle, pointer)),
        "Thread32Next": (flag, (handle, pointer)),
        "WaitForSingleObject": (dword, (handle, dword)),
        "CloseHandle": (flag, (handle,)),
    }
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, (result_type, argument_types) in signatures.items():
        function = getattr(kernel32, name)
        function.restype = result_type
        function.argtypes = argument_types
    return kernel32


# -- POSIX -----------------------------------------------------------------------------------------


def _spawn_with_parent_death_signal(command: Sequence[str], log_file: BinaryIO, env: Mapping[str, str] | None) -> Child:
    die_with_parent = functools.partial(_die_with_parent, os.getpid())
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        preexec_fn=die_with_parent,
    )
    return Child(process, None)


def _die_with_parent(parent_pid: int) -> None:
    """Runs in the child before the program starts: ask the kernel to kill it when its parent dies."""
    ctypes.CDLL(None, use_errno=True).prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)
    # The request only covers a death from now on. A parent that died before
    # it was made has already handed the child over to another process.
    if os.getppid() != parent_pid:
        os._exit(_KILLED_EXIT_CODE)
