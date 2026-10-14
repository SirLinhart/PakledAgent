"""The one HTTP endpoint through which game code reaches the local models.

It speaks the OpenAI chat format and nothing else: `POST /v1/chat/completions`
with a role name in the `model` field, `GET /v1/models` and `GET /health`. A
completion request is handed to the role's `llama-server` as it came in, so
whatever that server understands (`response_format`, `grammar`) works here
too. There is no queue, no priority and no eviction: a role is ready or it is
down, and a request for a down role is refused at once. A systemone role is
listed with the others, but it answers no chat completion: it is asked on its
own port, over the systemone API.

Each request is answered on its own connection, which is then closed. Closing
the endpoint therefore ends it for every caller: no connection is left on
which an old request thread would go on answering.
"""

from __future__ import annotations

import json
import socket
import socketserver
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Sequence

import httpx

from ucla_agent.core.console import print_line
from ucla_agent.core.modelserver.roles import COMPLETIONS_ROUTE, HEALTH_ROUTE, READY, Role
from ucla_agent.core.modelserver.systemone import SystemoneRole

MODELS_ROUTE = "/v1/models"
OK = 200
BAD_REQUEST = 400
NOT_FOUND = 404
BAD_GATEWAY = 502
SERVICE_UNAVAILABLE = 503
UNKNOWN_ROLE = "unknown_role"
ROLE_DOWN = "role_down"
INVALID_JSON = "invalid_json"
STREAMING_NOT_SUPPORTED = "streaming_not_supported"
BACKEND_UNREACHABLE = "backend_unreachable"
ROUTE_NOT_FOUND = "not_found"
MODEL_OWNER = "ucla-agent"
# How often the serving thread looks whether it was asked to stop, which bounds how long `close` takes.
_SHUTDOWN_POLL_SECONDS = 0.05

# An answer as a route gives it: the HTTP status and the JSON text.
Answer = tuple[int, bytes]


def _json(status: int, body: dict[str, Any]) -> Answer:
    return status, json.dumps(body).encode("utf-8")


def _error(status: int, name: str) -> Answer:
    return _json(status, {"error": name})


class _Router:
    """What the endpoint answers to each request, apart from the HTTP around it."""

    def __init__(self, roles: Sequence[Role | SystemoneRole]) -> None:
        self._roles = {role.name: role for role in roles}
        self._chat_roles = {role.name: role for role in roles if not isinstance(role, SystemoneRole)}

    def get(self, path: str) -> Answer:
        """The answer to a GET request."""
        if path == HEALTH_ROUTE:
            return _json(OK, {"roles": {name: _status_of(role) for name, role in self._roles.items()}})
        if path == MODELS_ROUTE:
            models = [{"id": name, "object": "model", "owned_by": MODEL_OWNER} for name in self._roles]
            return _json(OK, {"object": "list", "data": models})
        return _error(NOT_FOUND, ROUTE_NOT_FOUND)

    def post(self, path: str, raw_body: bytes) -> Answer:
        """The answer to a POST request."""
        if path != COMPLETIONS_ROUTE:
            return _error(NOT_FOUND, ROUTE_NOT_FOUND)
        body = _json_object(raw_body)
        if body is None:
            return _error(BAD_REQUEST, INVALID_JSON)
        if body.get("stream"):
            return _error(BAD_REQUEST, STREAMING_NOT_SUPPORTED)
        role = self._role_named(body.get("model"))
        if role is None:
            return _error(NOT_FOUND, UNKNOWN_ROLE)
        return _forward(role, body)

    def _role_named(self, model: Any) -> Role | None:
        """The role a request's `model` field names; None for anything that is not the name of a chat role."""
        return self._chat_roles.get(model) if isinstance(model, str) else None


def _status_of(role: Role | SystemoneRole) -> dict[str, str]:
    """A role's entry in the health answer: its state, and the reason when it is down."""
    if role.state == READY:
        return {"state": role.state}
    return {"state": role.state, "reason": role.reason}


def _json_object(raw_body: bytes) -> dict[str, Any] | None:
    """The request body as a JSON object, or None when it is not one."""
    try:
        body = json.loads(raw_body)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _forward(role: Role, body: dict[str, Any]) -> Answer:
    """Hand a completion request to the role's server and pass its answer back, status and all."""
    if role.state != READY:
        return _json(SERVICE_UNAVAILABLE, {"error": ROLE_DOWN, "reason": role.reason})
    try:
        answer = role.complete(body)
    except httpx.HTTPError:
        return _error(BAD_GATEWAY, BACKEND_UNREACHABLE)
    return answer.status_code, answer.content


class _Handler(BaseHTTPRequestHandler):
    """The HTTP around the router; `router` is set on the subclass an endpoint is built with."""

    router: _Router

    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        self._send(self.router.get(self.path))

    def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
        self._send(self.router.post(self.path, self.rfile.read(self._content_length())))

    def log_message(self, _format: str, *_arguments: Any) -> None:
        """Requests are not logged: the base class would write one console line for each."""

    def _content_length(self) -> int:
        try:
            return max(0, int(self.headers.get("Content-Length", "0")))
        except ValueError:
            # Read as an empty body, which the router refuses as invalid JSON.
            return 0

    def _send(self, answer: Answer) -> None:
        status, payload = answer
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _LoopbackServer(ThreadingHTTPServer):
    """The listening socket and one thread per request."""

    daemon_threads = True
    # On Windows the address-reuse option lets a second server bind a port that
    # is already served. Elsewhere it only allows binding again right after a
    # restart, which is wanted.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # Not the HTTP server's own bind: that one also looks the host's name
        # up, which can take seconds and is of no use to a loopback server.
        socketserver.TCPServer.server_bind(self)

    def handle_error(self, _request: Any, _client_address: Any) -> None:
        """Report a failed request, unless the caller just hung up.

        A caller that gave up at its deadline closes the connection, and
        writing the answer then fails. That is the caller's normal way out,
        so nothing is printed for it.
        """
        if not isinstance(sys.exc_info()[1], ConnectionError):
            print_line(f"The model server endpoint failed on a request:\n{traceback.format_exc()}", error=True)


class Endpoint:
    """The model server's HTTP endpoint for a set of roles, on one loopback address."""

    def __init__(self, host: str, port: int, roles: Sequence[Role | SystemoneRole]) -> None:
        """Bind `host:port` at once. Raises `OSError` when the port is taken: it is never shared."""
        handler = type("EndpointHandler", (_Handler,), {"router": _Router(roles)})
        self._server = _LoopbackServer((host, port), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, args=(_SHUTDOWN_POLL_SECONDS,), name="model-server-endpoint", daemon=True
        )

    @property
    def address(self) -> tuple[str, int]:
        """The address and port the endpoint is bound to."""
        host, port = self._server.server_address[:2]
        return host, port

    def serve(self) -> None:
        """Start answering requests, on a background thread."""
        self._thread.start()

    def close(self) -> None:
        """Stop answering and give the port back."""
        # shutdown() waits for the serving loop to end, and would wait forever for one that never ran.
        if self._thread.is_alive():
            self._server.shutdown()
        self._server.server_close()
