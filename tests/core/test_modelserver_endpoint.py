"""The model server's HTTP endpoint, with stand-in roles behind it: the three routes and every refusal."""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any, Callable, Iterator

import httpx
import pytest

from ucla_agent.core.modelserver import endpoint as endpoint_module
from ucla_agent.core.modelserver.endpoint import Endpoint

COMPLETION = {"choices": [{"index": 0, "message": {"role": "assistant", "content": '{"choice": "A"}'}}]}
CHAT_REQUEST = {
    "model": "fast",
    "messages": [{"role": "user", "content": "Choose a plan."}],
    "temperature": 0,
    "max_tokens": 8,
    "response_format": {
        "type": "json_schema",
        "json_schema": {"schema": {"type": "object", "properties": {"choice": {"enum": ["A", "B"]}}}},
    },
    "grammar": 'root ::= "A" | "B"',
}
SLOW_ANSWER_SECONDS = 0.5


class _Role:
    """Stands in for a role: remembers the completions it is asked for and answers as the test says."""

    def __init__(self, name: str, state: str = "ready", reason: str | None = None) -> None:
        self.name = name
        self.state = state
        self.reason = reason
        self.received: list[Any] = []
        self.answer = httpx.Response(200, json=COMPLETION)
        self.failure: Exception | None = None
        self.delay_seconds = 0.0

    def complete(self, body: Any) -> httpx.Response:
        self.received.append(body)
        time.sleep(self.delay_seconds)
        if self.failure is not None:
            raise self.failure
        return self.answer


@pytest.fixture
def fast() -> _Role:
    return _Role("fast")


@pytest.fixture
def strong() -> _Role:
    return _Role("strong", state="down", reason="model_file_missing")


@pytest.fixture
def served(fast, strong, free_port) -> Iterator[Endpoint]:
    """An endpoint serving the ready role `fast` and the down role `strong` on a free port."""
    endpoint = Endpoint("127.0.0.1", free_port(), [fast, strong])
    endpoint.serve()
    yield endpoint
    endpoint.close()


@pytest.fixture
def url(served) -> Callable[[str], str]:
    host, port = served.address
    return lambda route: f"http://{host}:{port}{route}"


# -- models and health -----------------------------------------------------------------------------


def test_models_lists_the_role_names_in_order(url, local_http):
    answer = local_http.get(url("/v1/models"))

    assert answer.status_code == 200
    assert answer.json() == {
        "object": "list",
        "data": [
            {"id": "fast", "object": "model", "owned_by": "ucla-agent"},
            {"id": "strong", "object": "model", "owned_by": "ucla-agent"},
        ],
    }


def test_health_gives_each_role_s_state_and_the_reason_for_a_down_one(url, local_http):
    answer = local_http.get(url("/health"))

    assert answer.status_code == 200
    assert answer.headers["content-type"] == "application/json"
    assert answer.json() == {
        "roles": {"fast": {"state": "ready"}, "strong": {"state": "down", "reason": "model_file_missing"}}
    }


def test_health_follows_a_role_that_goes_down(url, local_http, fast):
    fast.state, fast.reason = "down", "exited"

    assert local_http.get(url("/health")).json()["roles"]["fast"] == {"state": "down", "reason": "exited"}


# -- completions -----------------------------------------------------------------------------------


def test_a_completion_goes_to_the_role_its_model_field_names_and_the_answer_comes_back(url, local_http, fast):
    answer = local_http.post(url("/v1/chat/completions"), json=CHAT_REQUEST, headers={"x-client": "ucla-agent"})

    assert (answer.status_code, answer.json()) == (200, COMPLETION)
    assert fast.received == [CHAT_REQUEST]


def test_the_backend_s_own_status_and_body_are_passed_back(url, local_http, fast):
    fast.answer = httpx.Response(500, json={"error": {"message": "out of memory", "code": 500}})

    answer = local_http.post(url("/v1/chat/completions"), json=CHAT_REQUEST)

    assert (answer.status_code, answer.json()) == (500, {"error": {"message": "out of memory", "code": 500}})


@pytest.mark.parametrize(
    "model_field",
    [{"model": "nope"}, {}, {"model": ["fast"]}, {"model": None}],
    ids=["another name", "no model field", "a list", "null"],
)
def test_an_unknown_role_is_404(url, local_http, fast, model_field):
    request = {key: value for key, value in CHAT_REQUEST.items() if key != "model"} | model_field

    answer = local_http.post(url("/v1/chat/completions"), json=request)

    assert (answer.status_code, answer.json()) == (404, {"error": "unknown_role"})
    assert fast.received == []


def test_a_down_role_is_503_with_its_reason_and_is_not_asked(url, local_http, strong):
    answer = local_http.post(url("/v1/chat/completions"), json={**CHAT_REQUEST, "model": "strong"})

    assert (answer.status_code, answer.json()) == (503, {"error": "role_down", "reason": "model_file_missing"})
    assert strong.received == []


def test_a_streaming_request_is_refused(url, local_http, fast):
    answer = local_http.post(url("/v1/chat/completions"), json={**CHAT_REQUEST, "stream": True})

    assert (answer.status_code, answer.json()) == (400, {"error": "streaming_not_supported"})
    assert fast.received == []


def test_stream_false_is_an_ordinary_request(url, local_http, fast):
    answer = local_http.post(url("/v1/chat/completions"), json={**CHAT_REQUEST, "stream": False})

    assert answer.status_code == 200 and len(fast.received) == 1


@pytest.mark.parametrize(
    "content", [b"{not json", b'["fast"]', b'"fast"', b""], ids=["broken", "array", "string", "empty"]
)
def test_a_body_that_is_not_a_json_object_is_400(url, local_http, fast, content):
    answer = local_http.post(url("/v1/chat/completions"), content=content)

    assert (answer.status_code, answer.json()) == (400, {"error": "invalid_json"})
    assert fast.received == []


def test_a_backend_that_cannot_be_reached_is_502(url, local_http, fast):
    fast.failure = httpx.ConnectError("connection refused")

    answer = local_http.post(url("/v1/chat/completions"), json=CHAT_REQUEST)

    assert (answer.status_code, answer.json()) == (502, {"error": "backend_unreachable"})


@pytest.mark.parametrize(
    "method, route", [("GET", "/props"), ("GET", "/"), ("POST", "/v1/embeddings"), ("POST", "/health")]
)
def test_any_other_route_is_404(url, local_http, method, route):
    answer = local_http.request(method, url(route), json={})

    assert (answer.status_code, answer.json()) == (404, {"error": "not_found"})


def test_a_request_with_an_unreadable_content_length_is_400(served):
    request = b"POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\nContent-Length: many\r\n\r\n"

    with socket.create_connection(served.address, timeout=5) as connection:
        connection.sendall(request)
        reply = connection.makefile("rb").read()

    head, _, body = reply.partition(b"\r\n\r\n")
    assert head.splitlines()[0].endswith(b"400 Bad Request")
    assert json.loads(body) == {"error": "invalid_json"}


def test_a_slow_completion_does_not_hold_up_another_request(url, local_http, fast):
    fast.delay_seconds = SLOW_ANSWER_SECONDS
    slow_answers = []

    def ask_the_slow_role() -> None:
        slow_answers.append(httpx.post(url("/v1/chat/completions"), json=CHAT_REQUEST, trust_env=False))

    slow = threading.Thread(target=ask_the_slow_role)
    slow.start()
    started = time.monotonic()

    health = local_http.get(url("/health"))
    health_seconds = time.monotonic() - started
    slow.join()

    assert health.status_code == 200 and health_seconds < SLOW_ANSWER_SECONDS
    assert slow_answers[0].status_code == 200


# -- the listening socket --------------------------------------------------------------------------


def test_the_endpoint_listens_on_the_loopback_address_it_is_given(served):
    host, port = served.address

    assert host == "127.0.0.1" and port > 0


def test_a_port_that_is_already_served_is_never_shared(served, fast):
    _host, port = served.address

    with pytest.raises(OSError):
        Endpoint("127.0.0.1", port, [fast])


def test_a_port_another_program_listens_on_is_never_shared(free_port, fast):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", free_port()))
        listener.listen()

        with pytest.raises(OSError):
            Endpoint("127.0.0.1", listener.getsockname()[1], [fast])


def test_close_stops_answering_and_gives_the_port_back(fast, free_port, local_http):
    port = free_port()
    endpoint = Endpoint("127.0.0.1", port, [fast])
    endpoint.serve()
    assert local_http.get(f"http://127.0.0.1:{port}/health").status_code == 200

    endpoint.close()

    Endpoint("127.0.0.1", port, [fast]).close()


def test_an_endpoint_that_never_served_can_be_closed(fast, free_port):
    port = free_port()

    Endpoint("127.0.0.1", port, [fast]).close()

    Endpoint("127.0.0.1", port, [fast]).close()


def test_each_endpoint_answers_for_its_own_roles(fast, strong, free_port, local_http):
    first = Endpoint("127.0.0.1", free_port(), [fast])
    second = Endpoint("127.0.0.1", free_port(), [strong])
    first.serve()
    second.serve()
    try:
        first_models = local_http.get(f"http://127.0.0.1:{first.address[1]}/v1/models").json()["data"]
        second_models = local_http.get(f"http://127.0.0.1:{second.address[1]}/v1/models").json()["data"]
    finally:
        first.close()
        second.close()

    assert [model["id"] for model in first_models] == ["fast"]
    assert [model["id"] for model in second_models] == ["strong"]
    assert "router" not in vars(endpoint_module._Handler)


# -- failures inside a request ---------------------------------------------------------------------


def test_a_caller_that_hung_up_is_not_reported(served, capsys):
    try:
        raise ConnectionResetError("the caller gave up at its deadline")
    except ConnectionResetError:
        served._server.handle_error(None, ("127.0.0.1", 50000))

    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_any_other_failure_of_a_request_is_printed_through_the_redacting_writer(
    served, capsys, monkeypatch, run_folder
):
    monkeypatch.setenv("JEV_API_KEY", "jev-4f9c2d7e1a6b8053")
    try:
        raise RuntimeError("the role answered jev-4f9c2d7e1a6b8053")
    except RuntimeError:
        served._server.handle_error(None, ("127.0.0.1", 50000))

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("The model server endpoint failed on a request:\nTraceback")
    assert "RuntimeError: the role answered [REDACTED]" in captured.err


def test_the_endpoint_writes_no_console_line_for_a_request(url, local_http, capsys):
    local_http.get(url("/health"))
    local_http.post(url("/v1/chat/completions"), json=CHAT_REQUEST)

    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")
