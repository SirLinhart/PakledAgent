"""A stand-in for the native systemone server of a systemone role, so the role can be tested without a GPU or a model.

It takes the serve script's command line (`--model-dir`, `--host`, `--port`)
and serves the routes the model server and the systemone source use:
`GET /healthz` and `POST /v1/systemone`, which answers each choice question
with one of its options and a confidence.

`GET /stub/launch` returns the stub's process id, its arguments and the
environment variables whose names start with `STUB_` or are listed in
`STUB_ECHO`, and `GET /stub/requests` every systemone request it received, so
a test can see what happened in a process it cannot look into.

Environment variables of its own make it behave the way a real server can:

    STUB_LOAD_SECONDS    sleep this long before listening, as the model loads
    STUB_WARM_SECONDS    take this long to answer the first systemone request, as the CUDA graphs are recorded
    STUB_CHOICE          the option it chooses (default: the first one)
    STUB_CONFIDENCE      the confidence it gives (default 0.9)
    STUB_STATUS          the HTTP status of every systemone answer (default 200)
    STUB_EXIT_CODE       exit with this code before listening
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

HEALTHZ_ROUTE = "/healthz"
SYSTEMONE_ROUTE = "/v1/systemone"
LAUNCH_ROUTE = "/stub/launch"
REQUESTS_ROUTE = "/stub/requests"
OK = 200
NOT_FOUND = 404
UNPROCESSABLE = 422
STUB_PREFIX = "STUB_"
ECHO_ENV = "STUB_ECHO"
DEFAULT_CONFIDENCE = 0.9


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    """The serve script's command line."""
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    return parser.parse_args(arguments)


class _State:
    """What the stub was started with and what it received."""

    def __init__(self, arguments: list[str]) -> None:
        self.arguments = arguments
        self.requests: list[Any] = []
        self.warmed = False
        self.lock = threading.Lock()

    def launch(self) -> dict[str, Any]:
        echoed = set(os.environ.get(ECHO_ENV, "").split(","))
        environment = {name: value for name, value in os.environ.items() if name.startswith(STUB_PREFIX)}
        environment |= {name: os.environ[name] for name in echoed if name in os.environ}
        return {"pid": os.getpid(), "arguments": self.arguments, "environment": environment}

    def get(self, path: str) -> tuple[int, dict[str, Any]]:
        """The answer to a GET request."""
        routes = {
            HEALTHZ_ROUTE: lambda: {"status": "ok", "model": "stub", "backend": "stub"},
            LAUNCH_ROUTE: self.launch,
            REQUESTS_ROUTE: lambda: {"requests": self.requests},
        }
        if path not in routes:
            return NOT_FOUND, {"error": {"code": "not_found", "message": path}}
        return OK, routes[path]()

    def post(self, path: str, raw: bytes) -> tuple[int, dict[str, Any]]:
        """The answer to a POST request."""
        if path != SYSTEMONE_ROUTE:
            return NOT_FOUND, {"error": {"code": "not_found", "message": path}}
        try:
            body = json.loads(raw)
        except ValueError:
            return UNPROCESSABLE, {"error": {"code": "invalid_json", "message": "not JSON"}}
        return self.answer(body)

    def answer(self, body: Any) -> tuple[int, dict[str, Any]]:
        with self.lock:
            self.requests.append(body)
            first = not self.warmed
            self.warmed = True
        if first:
            time.sleep(float(os.environ.get("STUB_WARM_SECONDS", "0")))
        status = int(os.environ.get("STUB_STATUS", str(OK)))
        if status != OK:
            return status, {"error": {"code": "invalid_request", "message": "the stub refuses"}}
        try:
            questions = body["questions"]
            answers = {qid: _choice(question) for qid, question in questions.items()}
        except (KeyError, TypeError, AttributeError, StopIteration):
            return UNPROCESSABLE, {"error": {"code": "invalid_request", "message": "not a systemone request"}}
        return OK, {"model": "stub", "answers": answers, "usage": {"input_tokens": 1, "output_tokens": 0}}


def _choice(question: dict[str, Any]) -> dict[str, Any]:
    options = list(question["criteria"])
    choice = os.environ.get("STUB_CHOICE") or next(iter(options))
    confidence = float(os.environ.get("STUB_CONFIDENCE", str(DEFAULT_CONFIDENCE)))
    probabilities = {option: (1.0 if option == choice else 0.0) for option in options}
    return {"type": "choice", "choice": choice, "confidence": confidence, "probabilities": probabilities}


def make_handler(state: _State) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
            self._send(*state.get(self.path))

        def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
            self._send(*state.post(self.path, self.rfile.read(int(self.headers.get("Content-Length", "0")))))

        def log_message(self, _format: str, *_arguments: Any) -> None:
            """Requests are not logged."""

        def _send(self, status: int, body: dict[str, Any]) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def main(arguments: list[str]) -> int:
    options = parse_arguments(arguments)
    exit_code = os.environ.get("STUB_EXIT_CODE")
    if exit_code:
        return int(exit_code)
    time.sleep(float(os.environ.get("STUB_LOAD_SECONDS", "0")))
    server = ThreadingHTTPServer((options.host, options.port), make_handler(_State(arguments)))
    print(f"fake systemone server: listening on {options.host}:{options.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
