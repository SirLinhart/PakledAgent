"""The stand-in for `llama-server`: its routes, its flags, and that it runs as a program of its own."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Callable, Iterator

import httpx
import pytest

MODEL_PATH = "C:/Models/gguf/fast-model.gguf"
REAL_FLAGS = ["--ctx-size", "8192", "--n-gpu-layers", "999", "--parallel", "1", "--temp", "0", "--reasoning", "off"]
CHAT_REQUEST = {
    "model": "fast",
    "messages": [{"role": "user", "content": "Choose a plan."}],
    "response_format": {"type": "json_schema", "json_schema": {"schema": {"type": "object"}}},
}
READY_DELAY_SECONDS = 0.3
SERVER_POLL_SECONDS = 0.01
PROCESS_EXIT_LIMIT_SECONDS = 10


@pytest.fixture(scope="module")
def stub() -> ModuleType:
    """The stub imported into this process from its file, so its routes can be served from a thread."""
    path = Path(__file__).resolve().parents[1] / "stubs" / "fake_llama_server.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def serve(stub: ModuleType, free_port: Callable[[], int]) -> Iterator[Callable[..., str]]:
    """Serves a stub from a thread of this process and returns its base URL; every stub is closed afterwards."""
    servers = []

    def start(*flags: str) -> str:
        port = free_port()
        server = stub.build_server(["--model", MODEL_PATH, "--host", "127.0.0.1", "--port", str(port), *flags])
        servers.append(server)
        # A short poll interval, so that closing the stub after the test does not wait for the default half second.
        threading.Thread(target=server.serve_forever, args=(SERVER_POLL_SECONDS,), daemon=True).start()
        return f"http://127.0.0.1:{port}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


# -- health ----------------------------------------------------------------------------------------


def test_health_answers_200_once_the_stub_is_up(serve, local_http):
    answer = local_http.get(serve() + "/health")

    assert (answer.status_code, answer.json()) == (200, {"status": "ok"})


def test_a_stub_told_never_to_be_ready_answers_503_like_a_loading_server(serve, local_http):
    answer = local_http.get(serve("--stub-never-ready") + "/health")

    assert answer.status_code == 503
    assert answer.json()["error"]["message"] == "Loading model"


def test_a_stub_told_to_be_ready_later_answers_503_first_and_200_after_the_delay(serve, local_http, wait_for):
    base_url = serve("--stub-ready-after", str(READY_DELAY_SECONDS))
    started = time.monotonic()

    first = local_http.get(base_url + "/health")
    wait_for(lambda: local_http.get(base_url + "/health").status_code == 200, "the stub becoming ready")

    assert first.status_code == 503
    assert time.monotonic() - started >= READY_DELAY_SECONDS


# -- completions -----------------------------------------------------------------------------------


def test_a_completion_is_the_canned_answer_in_the_chat_completion_shape(serve, local_http):
    answer = local_http.post(serve() + "/v1/chat/completions", json=CHAT_REQUEST)

    body = answer.json()
    assert answer.status_code == 200
    assert body["object"] == "chat.completion" and body["model"] == "fast-model.gguf"
    assert body["choices"] == [
        {"index": 0, "message": {"role": "assistant", "content": '{"choice": "A"}'}, "finish_reason": "stop"}
    ]


def test_the_canned_answer_can_be_chosen(serve, local_http):
    answer = local_http.post(serve("--stub-content", '{"choice": "RAID"}') + "/v1/chat/completions", json=CHAT_REQUEST)

    assert answer.json()["choices"][0]["message"]["content"] == '{"choice": "RAID"}'


def test_every_completion_request_is_kept_with_its_headers_and_body(serve, local_http):
    base_url = serve()
    second_request = {**CHAT_REQUEST, "grammar": 'root ::= "A"'}

    local_http.post(base_url + "/v1/chat/completions", json=CHAT_REQUEST, headers={"x-client": "ucla-agent"})
    local_http.post(base_url + "/v1/chat/completions", json=second_request)

    first, second = local_http.get(base_url + "/stub/requests").json()["requests"]
    assert first["body"] == CHAT_REQUEST and first["headers"]["x-client"] == "ucla-agent"
    assert second["body"] == second_request and "x-client" not in second["headers"]


def test_a_stub_told_to_refuse_completions_answers_with_that_status_and_still_keeps_the_request(serve, local_http):
    base_url = serve("--stub-completion-status", "500")

    answer = local_http.post(base_url + "/v1/chat/completions", json=CHAT_REQUEST)

    assert answer.status_code == 500 and answer.json()["error"]["code"] == 500
    assert len(local_http.get(base_url + "/stub/requests").json()["requests"]) == 1


# -- what a test can ask the stub ------------------------------------------------------------------


def test_launch_reports_the_process_and_every_argument_it_was_started_with(serve, local_http):
    base_url = serve(*REAL_FLAGS)

    launch = local_http.get(base_url + "/stub/launch").json()

    port = base_url.rsplit(":", 1)[1]
    assert launch["pid"] == os.getpid()
    assert launch["arguments"] == ["--model", MODEL_PATH, "--host", "127.0.0.1", "--port", port, *REAL_FLAGS]


def test_no_request_has_been_received_at_first(serve, local_http):
    assert local_http.get(serve() + "/stub/requests").json() == {"requests": []}


@pytest.mark.parametrize("method, route", [("GET", "/props"), ("POST", "/v1/embeddings")])
def test_a_route_the_stub_does_not_serve_is_404(serve, local_http, method, route):
    answer = local_http.request(method, serve() + route, json={})

    assert (answer.status_code, answer.json()) == (404, {"error": "not_found"})


# -- the command line ------------------------------------------------------------------------------


def test_flags_of_the_real_program_are_accepted_and_ignored(stub):
    options = stub.parse_arguments(["--model", MODEL_PATH, "--port", "41500", *REAL_FLAGS, "--no-mmap"])

    assert (options.model, options.host, options.port) == (MODEL_PATH, "127.0.0.1", 41500)
    assert (options.stub_exit_code, options.stub_never_ready, options.stub_completion_status) == (None, False, 200)


def test_a_port_another_stub_listens_on_cannot_be_taken(stub, serve):
    port = serve().rsplit(":", 1)[1]

    with pytest.raises(OSError):
        stub.build_server(["--model", MODEL_PATH, "--port", port])


def test_a_stub_told_to_exit_returns_that_code_without_listening(stub, free_port, capsys):
    exit_code = stub.main(["--model", MODEL_PATH, "--port", str(free_port()), "--stub-exit-code", "3"])

    assert exit_code == 3
    assert capsys.readouterr().out == "fake llama-server: exiting with code 3\n"


# -- as a program ----------------------------------------------------------------------------------


def _run_stub(llama_stub: Path, port: int, *flags: str) -> subprocess.Popen:
    command = [sys.executable, str(llama_stub), "--model", MODEL_PATH, "--port", str(port), *REAL_FLAGS, *flags]
    return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _is_healthy(local_http: httpx.Client, port: int) -> bool:
    try:
        return local_http.get(f"http://127.0.0.1:{port}/health").status_code == 200
    except httpx.TransportError:
        return False


def test_the_stub_runs_as_a_program_on_the_port_it_is_given(llama_stub, free_port, local_http, wait_for):
    port = free_port()
    process = _run_stub(llama_stub, port)
    try:
        wait_for(lambda: _is_healthy(local_http, port), "the stub program answering /health")
        launch = local_http.get(f"http://127.0.0.1:{port}/stub/launch").json()
        completion = local_http.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=CHAT_REQUEST)
    finally:
        process.kill()
        output, _ = process.communicate(timeout=PROCESS_EXIT_LIMIT_SECONDS)

    assert launch["pid"] == process.pid
    assert launch["arguments"][:4] == ["--model", MODEL_PATH, "--port", str(port)]
    assert completion.json()["choices"][0]["message"]["content"] == '{"choice": "A"}'
    assert output.startswith(f"fake llama-server: listening on 127.0.0.1:{port}\n")


def test_the_stub_program_exits_with_the_code_it_is_told(llama_stub, free_port):
    process = _run_stub(llama_stub, free_port(), "--stub-exit-code", "7")

    output, _ = process.communicate(timeout=PROCESS_EXIT_LIMIT_SECONDS)

    assert process.returncode == 7
    assert output == "fake llama-server: exiting with code 7\n"
