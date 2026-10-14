"""One model role's `llama-server`: brought from launch to ready, asked for completions, and ended.

A role is either `ready` or `down`, and a down role always says why. Nothing
here raises for a server that cannot be started: a role that is down is a
normal state, which the endpoint reports and the callers of a decision source
fall back from.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Any

import httpx

from ucla_agent.core.modelserver import children, command
from ucla_agent.core.modelserver.settings import RoleSettings
from ucla_agent.core.secrets import BoundClient

READY = "ready"
DOWN = "down"
# Why a role is down.
NOT_STARTED = "not_started"
MODEL_FILE_MISSING = "model_file_missing"
PORT_IN_USE = "port_in_use"
LAUNCH_FAILED = "launch_failed"
EXITED = "exited"
NOT_READY_IN_TIME = "not_ready_in_time"
WARM_UP_FAILED = "warm_up_failed"
STOPPED = "stopped"

HEALTH_ROUTE = "/health"
COMPLETIONS_ROUTE = "/v1/chat/completions"
CLIENT_HEADER = "x-client"
CLIENT_NAME = "ucla-agent"
HTTP_OK = 200
# How long a server may take to load its model before the role is given up as down.
READY_LIMIT_SECONDS = 180.0
HEALTH_POLL_SECONDS = 0.25
HEALTH_PROBE_TIMEOUT_SECONDS = 2.0
WARM_UP_TIMEOUT_SECONDS = 60.0
# The longest a forwarded completion may take. Callers with a deadline enforce
# their own, much shorter one; this only keeps a stuck server from holding a
# request forever.
COMPLETION_TIMEOUT_SECONDS = 120.0
# One generated token is enough to make the server build its prompt cache and
# sampler, so the first real request does not pay for that.
WARM_UP_BODY = {"messages": [{"role": "user", "content": "Hello"}], "max_tokens": 1}


class Role:
    """The `llama-server` behind one role, on its own loopback port."""

    def __init__(self, settings: RoleSettings, server_path: Path, port: int, log_path: Path) -> None:
        """`log_path` is where the server's own output goes."""
        self.name = settings.name
        self.port = port
        self._settings = settings
        self._server_path = server_path
        self._log_path = log_path
        self._client = BoundClient(f"http://{command.LOOPBACK}:{port}", headers={CLIENT_HEADER: CLIENT_NAME})
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
        """Launch the role's server and bring it to ready; never raises, the role is down with a reason instead.

        The model file must exist (nothing is ever downloaded) and the port
        must be free (a server somebody else runs there is left alone). The
        server then has `READY_LIMIT_SECONDS` to answer its health check, and
        gets one warm-up completion. A server that fails any of this is ended.
        """
        self._reason = self._refusal_to_launch() or self._launch() or self._failure_to_get_ready()
        self._state = READY if self._reason is None else DOWN

    def complete(self, body: Any) -> httpx.Response:
        """Send one chat completion to the role's server and return its answer, whatever its status.

        The body goes out as it came in, with secrets redacted. Raises
        `httpx.HTTPError` when the server cannot be reached or does not answer
        within `COMPLETION_TIMEOUT_SECONDS`.
        """
        return self._client.request("POST", COMPLETIONS_ROUTE, json_body=body, timeout=COMPLETION_TIMEOUT_SECONDS)

    def stop(self) -> None:
        """End the role's server if one runs, and close the connections to it. A ready role is down afterwards."""
        if self._child is not None:
            self._child.terminate()
        self._client.close()
        if self._state == READY:
            self._state, self._reason = DOWN, STOPPED

    def _refusal_to_launch(self) -> str | None:
        """Why no server is launched at all, or None when one can be."""
        if not self._settings.model_path.is_file():
            return MODEL_FILE_MISSING
        if not port_is_free(self.port):
            return PORT_IN_USE
        return None

    def _launch(self) -> str | None:
        arguments = command.build(self._server_path, self._settings, self.port)
        try:
            self._child = children.spawn(arguments, self._log_path)
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
        deadline = time.monotonic() + READY_LIMIT_SECONDS
        while time.monotonic() < deadline:
            if not self._child.is_running():
                return EXITED
            if self._is_healthy():
                return None
            time.sleep(HEALTH_POLL_SECONDS)
        return NOT_READY_IN_TIME

    def _is_healthy(self) -> bool:
        try:
            answer = self._client.request("GET", HEALTH_ROUTE, timeout=HEALTH_PROBE_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            # A server that is still starting refuses the connection; one that is loading answers 503.
            return False
        return answer.status_code == HTTP_OK

    def _warm_up(self) -> str | None:
        try:
            answer = self._client.request(
                "POST", COMPLETIONS_ROUTE, json_body=WARM_UP_BODY, timeout=WARM_UP_TIMEOUT_SECONDS
            )
        except httpx.HTTPError:
            return WARM_UP_FAILED
        return None if answer.status_code == HTTP_OK else WARM_UP_FAILED


def port_is_free(port: int) -> bool:
    """Whether nothing listens on this loopback port, found out by binding it for a moment."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # Without this, Windows lets the probe bind a port another program already listens on.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind((command.LOOPBACK, port))
        except OSError:
            return False
    return True
