"""Startup and shutdown around one playing run of the agent.

Everything that has to be in place before the vendored runtime starts polling
the platform, and everything that has to be undone when it stops, happens
here, so the entry point stays a thin command-line layer.
"""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Callable, Sequence

from ucla_agent.core import instance_lock, records
from ucla_agent.core.config import ConfigError, autostart_roles, hash_config_files, load_agent_config
from ucla_agent.core.console import print_line
from ucla_agent.core.paths import repository_root

EXIT_STARTUP_FAILED = 1
EXIT_ALREADY_RUNNING = 3
_MODEL_SERVER_MODULE = "ucla_agent.core.modelserver"


class StartupError(Exception):
    """Startup could not be completed; the message says why."""


def git_commit(root: Path) -> str:
    """The commit the working tree under `root` is on (`git rev-parse HEAD`)."""
    try:
        completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise StartupError(f"Could not read the git commit of {root}: {exc.stderr.strip()}") from exc
    except OSError as exc:
        raise StartupError(f"Could not read the git commit of {root}: git could not be run ({exc})") from exc
    return completed.stdout.strip()


def run_session(flags: Sequence[str], play: Callable[[], int]) -> int:
    """Run `play` (the runtime, until it stops) as one recorded run of the agent; return the exit code.

    Takes the single-instance lock, loads the configuration, starts the model
    server with the roles `[models] autostart_roles` lists when the
    configuration asks for it and writes `run_started`; then calls `play`; then
    writes `run_stopped`, stops the model server and gives the lock up. Returns
    `EXIT_ALREADY_RUNNING` without doing any of that when another live copy
    holds the lock, and `EXIT_STARTUP_FAILED` without calling `play` when the
    configuration or the commit cannot be read, or an autostart role is one
    `config/models.toml` does not have.
    """
    root = repository_root()
    lock_path = root / instance_lock.LOCK_FILE_NAME
    try:
        instance_lock.acquire(lock_path)
    except instance_lock.AlreadyRunningError as exc:
        print_line(str(exc))
        return EXIT_ALREADY_RUNNING
    try:
        return _run_with_lock_held(root, flags, play)
    finally:
        instance_lock.release(lock_path)


def _run_with_lock_held(root: Path, flags: Sequence[str], play: Callable[[], int]) -> int:
    try:
        config = load_agent_config(root)
        run_started = {
            "commit": git_commit(root),
            "config_hashes": hash_config_files(root),
            "flags": list(flags),
        }
        # The model server finds an unknown role before it launches anything, so nothing is left to stop here.
        model_server = _start_model_server(config)
    except (ConfigError, StartupError) as exc:
        print_line(f"Startup error: {exc}")
        return EXIT_STARTUP_FAILED

    try:
        records.append_run_line("run_started", run_started)
        return _play_until_stopped(play)
    finally:
        if model_server is not None:
            model_server.stop()


def _start_model_server(config: dict) -> ModuleType | None:
    """Start the included model server with the autostart roles and return its module, whose `stop()` ends it.

    Returns None, and starts nothing, when autostart is off or lists no role.
    The module is imported here, and only when there is a role to start, so an
    agent that runs without local models never loads it. Raises `ConfigError`
    for a role `config/models.toml` does not have.
    """
    roles = autostart_roles(config)
    if not config["models"]["autostart"] or not roles:
        return None
    model_server = importlib.import_module(_MODEL_SERVER_MODULE)
    model_server.start(roles=roles)
    return model_server


def _play_until_stopped(play: Callable[[], int]) -> int:
    """Call `play` and write `run_stopped` however it ends.

    The stop line is written before the caller shuts the model server down, so
    it is on disk even when that shutdown fails or is interrupted. Its
    `exit_code` is null when `play` ended with an exception.
    """
    exit_code = None
    try:
        exit_code = play()
        return exit_code
    finally:
        records.append_run_line("run_stopped", {"exit_code": exit_code})
