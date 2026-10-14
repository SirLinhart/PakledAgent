"""One native systemone role: its serve script brought from launch to ready, and ended.

The server runs in the role's own Python environment (the runtime a native
model needs, which this project does not install), from a serve script given
the model folder and the port on its command line and everything else in the
environment the role's settings add. Nothing about the machine is in the
script.

A role is `ready` only once its server answers `GET /healthz` and has answered
one warm-up choice question: the first question records the server's CUDA
graphs, and a game's first real question must not pay for that. Like a
llama role, a role that cannot be brought up is `down` with a reason, and
nothing here raises for it.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from ucla_agent.core.modelserver import children
from ucla_agent.core.modelserver.command import LOOPBACK
from ucla_agent.core.modelserver.roles import (
    CLIENT_HEADER,
    CLIENT_NAME,
    DOWN,
    EXITED,
    HEALTH_POLL_SECONDS,
    HEALTH_PROBE_TIMEOUT_SECONDS,
    HTTP_OK,
    LAUNCH_FAILED,
    NOT_READY_IN_TIME,
    NOT_STARTED,
    PORT_IN_USE,
    READY,
    STOPPED,
    WARM_UP_FAILED,
    port_is_free,
)
from ucla_agent.core.modelserver.settings import SystemoneRoleSettings
from ucla_agent.core.secrets import BoundClient

HEALTHZ_ROUTE = "/healthz"
SYSTEMONE_ROUTE = "/v1/systemone"
# Why a systemone role is down besides the reasons a llama role has; each names the path that is missing.
PYTHON_MISSING = "python_missing"
SCRIPT_MISSING = "script_missing"
MODEL_DIR_MISSING = "model_dir_missing"
WARM_UP_TIMEOUT_SECONDS = 120.0
WARM_UP_QUESTION = "warmup"
# A choice of two, the least a native choice question may hold, so the warm-up runs the path real questions take.
WARM_UP_BODY = {
    "state": "A warm-up question of the agent's model server; nothing depends on the answer.",
    "questions": {
        WARM_UP_QUESTION: {
            "type": "choice",
            "instructions": "Which option comes first?",
            "criteria": {"A": "the first option", "B": "the second option"},
        }
    },
}


def serve_command(settings: SystemoneRoleSettings, port: int) -> list[str]:
    """The arguments that start the role's server on the loopback `port`, its Python first."""
    return [
        str(settings.python_path),
        str(settings.script),
        "--model-dir",
        str(settings.model_dir),
        "--host",
        LOOPBACK,
        "--port",
        str(port),
    ]


def serve_environment(settings: SystemoneRoleSettings) -> dict[str, str]:
    """This process's environment with the role's own variables over it."""
    return {**os.environ, **settings.env}


class SystemoneRole:
    """The native systemone server behind one role, on its own loopback port."""

    def __init__(self, settings: SystemoneRoleSettings, port: int, log_path: Path) -> None:
        """`log_path` is where the server's own output goes."""
        self.name = settings.name
        self.port = port
        self._settings = settings
        self._log_path = log_path
        self._client = BoundClient(f"http://{LOOPBACK}:{port}", headers={CLIENT_HEADER: CLIENT_NAME})
        self._child: children.Child | None = None
        self._state = DOWN
        self._reason: str | None = NOT_STARTED

    @property
    def state(self) -> str:
        """`ready` or `down`. A role that was ready is down from the moment its server has exited."""
        if self._state == READY and not self._child.is_running():
            self._state, self._reason = DOWN, EXITED
        return self._state

    @property
    def reason(self) -> str | None:
        """Why the role is down; None while it is ready."""
        return None if self.state == READY else self._reason

    def start(self) -> None:
        """Launch the server and bring it to ready; never raises, the role is down with a reason instead.

        The Python, the script and the model folder must exist and the port
        must be free (a server somebody else runs there is left alone). The
        server then has the role's `startup_seconds` to answer `/healthz`, and
        gets one warm-up choice. A server that fails any of this is ended.
        """
        self._reason = self._refusal_to_launch() or self._launch() or self._failure_to_get_ready()
        self._state = READY if self._reason is None else DOWN

    def stop(self) -> None:
        """End the server if one runs, and close the connections to it. A ready role is down afterwards."""
        if self._child is not None:
            self._child.terminate()
        self._client.close()
        if self._state == READY:
            self._state, self._reason = DOWN, STOPPED

    def _refusal_to_launch(self) -> str | None:
        """Why no server is launched at all, naming the missing path, or None when one can be."""
        settings = self._settings
        missing = (
            (PYTHON_MISSING, settings.python_path, settings.python_path.is_file()),
            (SCRIPT_MISSING, settings.script, settings.script.is_file()),
            (MODEL_DIR_MISSING, settings.model_dir, settings.model_dir.is_dir()),
        )
        for reason, path, exists in missing:
            if not exists:
                return f"{reason}: {path.as_posix()}"
        if not port_is_free(self.port):
            return PORT_IN_USE
        return None

    def _launch(self) -> str | None:
        command = serve_command(self._settings, self.port)
        try:
            self._child = children.spawn(command, self._log_path, serve_environment(self._settings))
        except OSError:
            return LAUNCH_FAILED
        return None

    def _failure_to_get_ready(self) -> str | None:
        """Wait for the launched server and warm it up; a server that fails either is ended."""
        failure = self._wait_for_health() or self._warm_up()
        if failure is not None:
            self._child.terminate()
        return failure

    def _wait_for_health(self) -> str | None:
        deadline = time.monotonic() + self._settings.startup_seconds
        while time.monotonic() < deadline:
            if not self._child.is_running():
                return EXITED
            if self._is_healthy():
                return None
            time.sleep(HEALTH_POLL_SECONDS)
        return NOT_READY_IN_TIME

    def _is_healthy(self) -> bool:
        try:
            answer = self._client.request("GET", HEALTHZ_ROUTE, timeout=HEALTH_PROBE_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            # The server listens only once its model is loaded; until then the connection is refused.
            return False
        return answer.status_code == HTTP_OK

    def _warm_up(self) -> str | None:
        try:
            answer = self._client.request(
                "POST", SYSTEMONE_ROUTE, json_body=WARM_UP_BODY, timeout=WARM_UP_TIMEOUT_SECONDS
            )
            answered = answer.status_code == HTTP_OK and "choice" in answer.json()["answers"][WARM_UP_QUESTION]
        except (httpx.HTTPError, ValueError, LookupError, TypeError):
            return WARM_UP_FAILED
        return None if answered else WARM_UP_FAILED
