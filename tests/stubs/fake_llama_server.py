"""A stand-in for `llama-server`, so the model server can be tested without a GPU or a model file.

It takes the real program's command line, listens on the `--host` and `--port`
it is given and serves the two routes the model server uses: `GET /health` and
`POST /v1/chat/completions`, which answers every request with one canned
completion. Every other flag of the real program is accepted and ignored.

Two more routes let a test see what happened in a process it cannot look
into: `GET /stub/launch` returns the stub's process id and the arguments it
was started with, and `GET /stub/requests` returns every completion request it
received, with headers and body.

Flags of its own, which a test passes through a role's `extra_args`, make it
misbehave the way a real server can:

    --stub-exit-code N           exit with code N before listening
    --stub-never-ready           answer /health with 503 forever
    --stub-ready-after SECONDS   answer /health with 503 for that long, then 200
    --stub-completion-status N   answer every completion with HTTP status N
    --stub-content TEXT          the assistant message of the canned completion
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PureWindowsPath
from typing import Any

HEALTH_ROUTE = "/health"
COMPLETIONS_ROUTE = "/v1/chat/completions"
LAUNCH_ROUTE = "/stub/launch"
REQUESTS_ROUTE = "/stub/requests"
DEFAULT_CONTENT = '{"choice": "A"}'
OK = 200
NOT_FOUND = 404
LOADING = 503
# What the real server answers on /health while its model is still loading.
LOADING_BODY = {"error": {"message": "Loading model", "type": "unavailable_error", "code": LOADING}}

# An answer as a route gives it: the HTTP status and the JSON body.
Answer = tuple[int, dict[str, Any]]


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    """The flags the stub acts on; every other flag of the real program is accepted and ignored."""
    # Abbreviations off: `--temp` must not be read as a shortened flag of the stub.
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--model", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--stub-exit-code", type=int)
    parser.add_argument("--stub-never-ready", action="store_true")
    parser.add_argument("--stub-ready-after", type=float, default=0.0)
    parser.add_argument("--stub-completion-status", type=int, default=OK)
    parser.add_argument("--stub-content", default=DEFAULT_CONTENT)
    options, _ignored = parser.parse_known_args(arguments)
    return options


def not_found() -> Answer:
    """The answer to a route the stub does not serve."""
    return NOT_FOUND, {"error": "not_found"}


class StubState:
    """What one running stub knows (how it was started, when, what it was asked) and the answers it gives."""

    def __init__(self, options: argparse.Namespace, arguments: list[str]) -> None:
        self._options = options
        self._arguments = arguments
        self._requests: list[dict[str, Any]] = []
        self._started = time.monotonic()
        self._lock = threading.Lock()

    def health(self) -> Answer:
        """`GET /health`: 200 once the stub counts as loaded, 503 before that."""
        loaded = time.monotonic() - self._started >= self._options.stub_ready_after
        if loaded and not self._options.stub_never_ready:
            return OK, {"status": "ok"}
        return LOADING, LOADING_BODY

    def launch(self) -> Answer:
        """`GET /stub/launch`: this process and the arguments it was started with."""
        return OK, {"pid": os.getpid(), "arguments": self._arguments}

    def received(self) -> Answer:
        """`GET /stub/requests`: every completion request so far, oldest first."""
        with self._lock:
            return OK, {"requests": list(self._requests)}

    def complete(self, headers: dict[str, str], body: Any) -> Answer:
        """`POST /v1/chat/completions`: remember the request and give the canned answer."""
        with self._lock:
            self._requests.append({"headers": headers, "body": body})
        status = self._options.stub_completion_status
        if status != OK:
            return status, {"error": {"message": "the stub was told to refuse completions", "code": status}}
        return OK, self._completion()

    def _completion(self) -> dict[str, Any]:
        """One chat completion in the shape the real server gives."""
        return {
            "id": "chatcmpl-stub",
            "object": "chat.completion",
            # PureWindowsPath takes both separators, so the name comes out the same on every platform.
            "model": PureWindowsPath(self._options.model).name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": self._options.stub_content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


class StubHandler(BaseHTTPRequestHandler):
    """Answers the routes of one stub; `state` is set on the subclass a server is built with."""

    state: StubState

    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        routes = {HEALTH_ROUTE: self.state.health, LAUNCH_ROUTE: self.state.launch, REQUESTS_ROUTE: self.state.received}
        self._send(routes.get(self.path, not_found)())

    def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
        if self.path != COMPLETIONS_ROUTE:
            self._send(not_found())
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        headers = {name.lower(): value for name, value in self.headers.items()}
        self._send(self.state.complete(headers, body))

    def log_message(self, format: str, *arguments: Any) -> None:  # noqa: A002 - the base class's signature
        """One line per request on standard output, which the model server sends to the role's log file."""
        print(f"fake llama-server: {format % arguments}", flush=True)

    def _send(self, answer: Answer) -> None:
        status, body = answer
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class StubServer(ThreadingHTTPServer):
    """The stub's listener. Like the real server, it fails to start on a port something else listens on."""

    # http.server turns address reuse on, which on Windows lets two servers share one port.
    allow_reuse_address = False
    daemon_threads = True


def build_server(arguments: list[str]) -> StubServer:
    """A stub listening on the host and port the arguments name; the caller serves and closes it."""
    options = parse_arguments(arguments)
    handler = type("BoundStubHandler", (StubHandler,), {"state": StubState(options, arguments)})
    return StubServer((options.host, options.port), handler)


def main(arguments: list[str]) -> int:
    """Run the stub until it is killed; returns at once with the exit code a test asked for."""
    exit_code = parse_arguments(arguments).stub_exit_code
    if exit_code is not None:
        print(f"fake llama-server: exiting with code {exit_code}", flush=True)
        return exit_code
    with build_server(arguments) as server:
        host, port = server.server_address[:2]
        print(f"fake llama-server: listening on {host}:{port}", flush=True)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
