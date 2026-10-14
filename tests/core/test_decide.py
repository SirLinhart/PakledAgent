"""The decision sources as game code uses them, with HTTP replaced by a stub transport.

Each test opens a match record, asks through the public names of
`ucla_agent.core.decide`, and checks the result together with the `decision`
and `fallback` events the request left in the record.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

import httpx
import pytest

from ucla_agent.core.decide import (
    AsyncChooser,
    ChainSource,
    ChoiceRequest,
    ChoiceResult,
    JevSource,
    LocalModelSource,
    Option,
    circuit,
)

MODEL_SERVER_URL = "http://127.0.0.1:11500"
JEV_URL = "https://api.jevai.net"
JEV_KEY = "jev-4f9c2d7e1a6b8053"
OPTIONS = [Option("HOLD", "Keep the army at the base."), Option("RAID", "Attack the enemy's harvesters.")]
ENUM_SCHEMA = {"type": "object", "properties": {"choice": {"enum": ["HOLD", "RAID"]}}, "required": ["choice"]}
SLOW_ANSWER_SECONDS = 0.4
CIRCUIT_OPEN_LIMIT_SECONDS = 0.005
RESULT_LIMIT_SECONDS = 5.0


def _request(deadline_ms: int = 1500, state_tag: str = "1250") -> ChoiceRequest:
    return ChoiceRequest("Which plan?", OPTIONS, "Minute 6. We have 8 tanks.", deadline_ms, state_tag)


def _completion(choice: str) -> httpx.Response:
    content = json.dumps({"choice": choice})
    return httpx.Response(200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]})


class _Stub:
    """A stub transport: remembers every request, waits as long as the test says and gives the test's answer."""

    def __init__(self, answer: Callable[[], httpx.Response], seconds: float = 0.0) -> None:
        self.requests: list[httpx.Request] = []
        self.seconds = seconds
        self._answer = answer
        self.transport = httpx.MockTransport(self._respond)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        time.sleep(self.seconds)
        return self._answer()

    def model_source(self, role: str = "fast") -> LocalModelSource:
        return LocalModelSource(role, base_url=MODEL_SERVER_URL, transport=self.transport)


def _events(match_record) -> list[tuple[str, Any, Any]]:
    """The decision and fallback events of the record, each as (kind, source, outcome or what decided instead)."""
    events = []
    for line in match_record():
        data = line["data"]
        if line["kind"] == "decision":
            events.append(("decision", data["source"], data["outcome"]))
        elif line["kind"] == "fallback":
            events.append(("fallback", data["intended_source"], data["used"]))
    return events


def _polled(chooser: AsyncChooser, current_state_tag: str) -> ChoiceResult:
    give_up_at = time.monotonic() + RESULT_LIMIT_SECONDS
    while time.monotonic() < give_up_at:
        result = chooser.poll(current_state_tag)
        if result is not None:
            return result
        time.sleep(0.005)
    raise AssertionError("the chooser never handed a result over")


@pytest.fixture
def circuit_clock(monkeypatch) -> dict[str, float]:
    """The circuit breakers' clock, in seconds; a test moves it by changing `now`."""
    moment = {"now": 100.0}
    monkeypatch.setattr(circuit, "_now", lambda: moment["now"])
    return moment


# -- (1) and (2) the local model's answer ----------------------------------------------------------


def test_the_local_model_s_choice_is_used_and_the_request_pins_max_tokens_temperature_and_the_enum_schema(
    match_record,
):
    stub = _Stub(lambda: _completion("RAID"))

    result = stub.model_source().choose(_request())

    (request,) = stub.requests
    body = json.loads(request.content)
    assert (result.option_id, result.source, result.outcome) == ("RAID", "local:fast", "used")
    assert (body["max_tokens"], body["temperature"]) == (24, 0)
    assert body["response_format"]["json_schema"]["schema"] == ENUM_SCHEMA
    assert _events(match_record) == [("decision", "local:fast", "used")]


def test_a_choice_that_is_not_one_of_the_options_is_invalid(match_record):
    stub = _Stub(lambda: _completion("zzz"))

    result = stub.model_source().choose(_request())

    assert (result.option_id, result.outcome) == (None, "invalid")
    assert _events(match_record) == [("decision", "local:fast", "invalid"), ("fallback", "local:fast", "script")]


# -- (3) the deadline ------------------------------------------------------------------------------


def test_an_answer_that_takes_400_ms_under_a_200_ms_deadline_is_a_timeout_after_200_to_300_ms(match_record):
    stub = _Stub(lambda: _completion("RAID"), seconds=SLOW_ANSWER_SECONDS)

    result = stub.model_source().choose(_request(deadline_ms=200))

    assert (result.option_id, result.outcome) == (None, "timeout")
    assert 200 <= result.latency_ms <= 300
    assert _events(match_record) == [("decision", "local:fast", "timeout"), ("fallback", "local:fast", "script")]


# -- (4) a chain -----------------------------------------------------------------------------------


def test_a_chain_of_a_failing_and_a_working_source_returns_the_working_one_s_result_within_the_deadline(
    match_record,
):
    failing = _Stub(lambda: httpx.Response(503, json={"error": "role_down"}))
    working = _Stub(lambda: _completion("HOLD"))
    chain = ChainSource([failing.model_source("strong"), working.model_source("fast")])
    started = time.perf_counter()

    result = chain.choose(_request(deadline_ms=1000))
    seconds = time.perf_counter() - started

    assert (result.option_id, result.source, result.outcome) == ("HOLD", "local:fast", "used")
    assert seconds < 1.0
    assert (len(failing.requests), len(working.requests)) == (1, 1)
    assert _events(match_record) == [
        ("decision", "local:strong", "error"),
        ("fallback", "local:strong", "local:fast"),
        ("decision", "local:fast", "used"),
    ]


# -- (5) the circuit breaker -----------------------------------------------------------------------


def test_three_timeouts_open_the_circuit_and_thirty_seconds_later_the_source_is_reached_again(
    match_record, circuit_clock
):
    stub = _Stub(lambda: _completion("RAID"), seconds=0.15)
    source = stub.model_source()

    timeouts = [source.choose(_request(deadline_ms=50)) for _ in range(3)]
    started = time.perf_counter()
    fourth = source.choose(_request())
    fourth_seconds = time.perf_counter() - started
    stub.seconds = 0.0
    circuit_clock["now"] += 30.0
    later = source.choose(_request())

    assert [result.outcome for result in timeouts] == ["timeout", "timeout", "timeout"]
    assert (fourth.option_id, fourth.outcome, fourth.reason) == (None, "error", "circuit_open")
    assert fourth_seconds < CIRCUIT_OPEN_LIMIT_SECONDS
    assert len(stub.requests) == 4
    assert (later.option_id, later.outcome) == ("RAID", "used")
    assert _events(match_record) == [
        *[("decision", "local:fast", "timeout"), ("fallback", "local:fast", "script")] * 3,
        ("decision", "local:fast", "error"),
        ("fallback", "local:fast", "script"),
        ("decision", "local:fast", "used"),
    ]


# -- (6) and (7) asking without waiting ------------------------------------------------------------


def test_an_answer_for_a_situation_the_game_calls_outdated_comes_back_as_stale_discarded(match_record):
    stub = _Stub(lambda: _completion("RAID"))
    chooser = AsyncChooser(stub.model_source(), is_stale=lambda asked_tag, current_tag: asked_tag != current_tag)
    chooser.submit(_request(state_tag="1250"))

    result = _polled(chooser, current_state_tag="1400")

    assert (result.option_id, result.source, result.outcome) == (None, "local:fast", "stale_discarded")
    assert _events(match_record) == [
        ("decision", "local:fast", "stale_discarded"),
        ("fallback", "local:fast", "script"),
    ]


def test_a_second_submit_while_one_is_in_flight_returns_false(match_record):
    stub = _Stub(lambda: _completion("HOLD"), seconds=0.2)
    chooser = AsyncChooser(stub.model_source(), is_stale=lambda asked_tag, current_tag: False)

    first = chooser.submit(_request(state_tag="1250"))
    second = chooser.submit(_request(state_tag="1260"))
    result = _polled(chooser, current_state_tag="1260")

    assert (first, second) == (True, False)
    assert len(stub.requests) == 1
    assert (result.option_id, result.outcome) == ("HOLD", "used")
    assert _events(match_record) == [("decision", "local:fast", "used")]


# -- (8) Jev ---------------------------------------------------------------------------------------


def test_a_jev_answer_with_confidence_0_40_is_invalid(match_record, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", JEV_KEY)
    stub = _Stub(lambda: httpx.Response(200, json={"value": {"choice": "RAID"}, "confidence": 0.40}))

    result = JevSource(JEV_URL, transport=stub.transport).choose(_request())

    assert (result.option_id, result.confidence, result.source, result.outcome) == (None, 0.40, "jev", "invalid")
    assert str(stub.requests[0].url) == f"{JEV_URL}/v1/decide"
    assert _events(match_record) == [("decision", "jev", "invalid"), ("fallback", "jev", "script")]


# -- (9) the record of all of them -----------------------------------------------------------------


def test_every_call_leaves_a_decision_event_and_every_result_but_used_a_fallback_event(match_record, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", JEV_KEY)
    used = _Stub(lambda: _completion("RAID")).model_source().choose(_request())
    invalid = _Stub(lambda: _completion("zzz")).model_source().choose(_request())
    timeout = _Stub(lambda: _completion("RAID"), seconds=0.15).model_source().choose(_request(deadline_ms=50))
    error = _Stub(lambda: httpx.Response(500)).model_source().choose(_request())
    low = JevSource(
        JEV_URL,
        transport=_Stub(lambda: httpx.Response(200, json={"value": {"choice": "HOLD"}, "confidence": 0.4})).transport,
    ).choose(_request())

    lines = [line for line in match_record() if line["kind"] in ("decision", "fallback")]
    decisions = [line["data"] for line in lines if line["kind"] == "decision"]
    fallbacks = [line["data"] for line in lines if line["kind"] == "fallback"]

    assert [result.outcome for result in (used, invalid, timeout, error, low)] == [
        "used",
        "invalid",
        "timeout",
        "error",
        "invalid",
    ]
    assert [decision["outcome"] for decision in decisions] == ["used", "invalid", "timeout", "error", "invalid"]
    assert [(fallback["intended_source"], fallback["reason"]) for fallback in fallbacks] == [
        ("local:fast", "invalid"),
        ("local:fast", "timeout"),
        ("local:fast", "error"),
        ("jev", "invalid"),
    ]
    for decision in decisions:
        assert set(decision) >= {"state_version", "source", "request_summary", "answer", "latency_ms", "outcome"}
    for fallback in fallbacks:
        assert set(fallback) == {"intended_source", "reason", "used"}
