"""`JevSource`: what it sends to the Jev service, how it reads the answer, and what happens without a key."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest

from ucla_agent.core.decide import ChoiceRequest, ChoiceResult, JevFields, JevSource, Option
from ucla_agent.core.decide import jev as jev_module
from ucla_agent.core.secrets import REDACTED

JEV_URL = "https://api.jevai.net"
JEV_KEY = "jev-4f9c2d7e1a6b8053"
OPTIONS = [Option("HOLD", "Keep the army at the base."), Option("RAID", "Attack the enemy's harvesters.")]
ENUM_SCHEMA = {"type": "object", "properties": {"choice": {"enum": ["HOLD", "RAID"]}}, "required": ["choice"]}
SLOW_ANSWER_SECONDS = 0.4


def _request(deadline_ms: int = 1500, context: str = "Minute 6. We have 8 tanks.") -> ChoiceRequest:
    return ChoiceRequest("Which plan?", OPTIONS, context, deadline_ms, "1250")


def _answer(choice: Any = "RAID", confidence: Any = 0.82) -> httpx.Response:
    return httpx.Response(200, json={"value": {"choice": choice}, "confidence": confidence})


class _Jev:
    """Stands in for the Jev service: remembers each request and answers as the test says."""

    def __init__(self, answer: httpx.Response | Exception | None = None, seconds: float = 0.0) -> None:
        self.requests: list[httpx.Request] = []
        self._answer = answer if answer is not None else _answer()
        self._seconds = seconds
        self.transport = httpx.MockTransport(self._respond)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        time.sleep(self._seconds)
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer

    def source(self, **options: Any) -> JevSource:
        return JevSource(JEV_URL, transport=self.transport, **options)


@pytest.fixture
def key(run_folder, monkeypatch) -> str:
    """A Jev key in the environment of a test that runs in a temporary folder."""
    monkeypatch.setenv("JEV_API_KEY", JEV_KEY)
    return JEV_KEY


# -- the request -----------------------------------------------------------------------------------


def test_the_request_goes_to_the_decide_route_of_the_base_url_with_the_key_as_bearer_token(key):
    service = _Jev()

    service.source().choose(_request())

    (request,) = service.requests
    assert (request.method, str(request.url)) == ("POST", f"{JEV_URL}/v1/decide")
    assert request.headers["authorization"] == f"Bearer {JEV_KEY}"
    assert request.headers["content-type"] == "application/json"


def test_the_body_holds_the_situation_as_the_state_and_a_schema_of_the_option_ids(key):
    service = _Jev()

    service.source().choose(_request())

    assert json.loads(service.requests[0].content) == {
        "state": {
            "question": "Which plan?",
            "context": "Minute 6. We have 8 tanks.",
            "options": [
                {"id": "HOLD", "description": "Keep the army at the base."},
                {"id": "RAID", "description": "Attack the enemy's harvesters."},
            ],
        },
        "output_schema": ENUM_SCHEMA,
    }


def test_the_key_goes_out_as_the_header_only_and_a_key_in_the_context_is_redacted(key):
    service = _Jev()

    service.source().choose(_request(context=f"The operator pasted {JEV_KEY} here."))

    (request,) = service.requests
    assert JEV_KEY.encode() not in request.content
    assert json.loads(request.content)["state"]["context"] == f"The operator pasted {REDACTED} here."
    assert request.headers["authorization"] == f"Bearer {JEV_KEY}"


def test_the_http_timeout_is_the_deadline(key):
    service = _Jev()

    service.source().choose(_request(deadline_ms=900))

    assert set(service.requests[0].extensions["timeout"].values()) == {0.9}


# -- the answer ------------------------------------------------------------------------------------


def test_the_returned_value_and_confidence_become_the_result(key):
    result = _Jev(_answer("RAID", 0.82)).source().choose(_request())

    assert result == ChoiceResult("RAID", 0.82, "jev", result.latency_ms, "used")


@pytest.mark.parametrize(
    "confidence, outcome, reason, option",
    [(0.40, "invalid", "low_confidence", None), (0.55, "used", None, "HOLD"), (1, "used", None, "HOLD")],
    ids=["below the minimum", "at the minimum", "whole number"],
)
def test_a_confidence_below_the_minimum_of_0_55_is_invalid(key, confidence, outcome, reason, option):
    result = _Jev(_answer("HOLD", confidence)).source().choose(_request())

    assert (result.option_id, result.confidence, result.outcome, result.reason) == (option, confidence, outcome, reason)
    assert jev_module.DEFAULT_MIN_CONFIDENCE == 0.55


def test_the_minimum_confidence_can_be_set(key):
    service = _Jev(_answer("HOLD", 0.70))

    strict = service.source(min_confidence=0.9).choose(_request())
    lenient = service.source(min_confidence=0.6).choose(_request())

    assert (strict.outcome, strict.reason, lenient.outcome) == ("invalid", "low_confidence", "used")


def test_a_value_that_is_not_an_option_is_invalid(key):
    result = _Jev(_answer("RETREAT", 0.95)).source().choose(_request())

    assert (result.option_id, result.confidence, result.outcome, result.reason) == (
        None,
        0.95,
        "invalid",
        "not_an_option",
    )


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, json={"confidence": 0.9}),
        httpx.Response(200, json={"value": {"choice": "RAID"}}),
        httpx.Response(200, json={"value": "RAID", "confidence": 0.9}),
        httpx.Response(200, json={"value": {"choice": 2}, "confidence": 0.9}),
        httpx.Response(200, json={"value": {"choice": "RAID"}, "confidence": "high"}),
        httpx.Response(200, json={"value": {"choice": "RAID"}, "confidence": True}),
        httpx.Response(200, json={"value": {"choice": "RAID"}, "confidence": None}),
        httpx.Response(200, json=["RAID", 0.9]),
        httpx.Response(200, content=b"<html>not json</html>"),
    ],
    ids=[
        "no value",
        "no confidence",
        "value is not an object",
        "choice is a number",
        "confidence is a word",
        "confidence is true",
        "confidence is null",
        "body is a list",
        "body is not json",
    ],
)
def test_an_answer_without_a_readable_value_and_confidence_is_an_error(key, answer):
    result = _Jev(answer).source().choose(_request())

    assert (result.option_id, result.outcome, result.reason) == (None, "error", "malformed_answer")


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_a_refusal_of_the_service_is_an_error_with_its_status(key, status):
    result = _Jev(httpx.Response(status, json={"error": "refused"})).source().choose(_request())

    assert (result.option_id, result.outcome, result.reason) == (None, "error", f"http_{status}")


def test_a_service_that_cannot_be_reached_is_an_error_and_not_an_exception(key):
    result = _Jev(httpx.ConnectError("no route to host")).source().choose(_request())

    assert (result.outcome, result.reason) == ("error", "connection_failed")


def test_an_answer_slower_than_the_deadline_is_a_timeout_at_the_deadline(key):
    result = _Jev(seconds=SLOW_ANSWER_SECONDS).source().choose(_request(deadline_ms=200))

    assert (result.option_id, result.outcome) == (None, "timeout")
    assert 200 <= result.latency_ms <= 300


# -- the field names are settings ------------------------------------------------------------------


def test_the_request_and_the_answer_use_the_field_names_the_source_is_given(key):
    fields = JevFields(state="input", output_schema="schema", value="decision", confidence="scores.confidence")
    service = _Jev(httpx.Response(200, json={"decision": "HOLD", "scores": {"confidence": 0.77}}))

    result = service.source(fields=fields).choose(_request())

    assert set(json.loads(service.requests[0].content)) == {"input", "schema"}
    assert (result.option_id, result.confidence, result.outcome) == ("HOLD", 0.77, "used")


def test_the_default_field_names():
    assert JevFields() == JevFields(
        state="state", output_schema="output_schema", value="value.choice", confidence="confidence"
    )


# -- the key ---------------------------------------------------------------------------------------


def test_without_a_key_nothing_is_sent_and_the_call_is_an_error(run_folder, monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    service = _Jev()

    result = service.source().choose(_request())

    assert service.requests == []
    assert (result.option_id, result.outcome, result.reason) == (None, "error", "no_api_key")


def test_an_empty_key_counts_as_no_key(run_folder, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "")
    service = _Jev()

    assert service.source().choose(_request()).reason == "no_api_key"
    assert service.requests == []


def test_the_key_is_read_from_the_env_file_of_the_repository_root(run_folder, monkeypatch):
    # Set and removed through monkeypatch, so that whatever the env file loads is taken out again after the test.
    monkeypatch.setenv("JEV_API_KEY", "placeholder")
    monkeypatch.delenv("JEV_API_KEY")
    (run_folder / ".env").write_text(f"JEV_API_KEY={JEV_KEY}\n", encoding="utf-8")
    service = _Jev()

    service.source().choose(_request())

    assert service.requests[0].headers["authorization"] == f"Bearer {JEV_KEY}"


# -- the record ------------------------------------------------------------------------------------


def test_a_call_is_recorded_as_a_decision_of_the_source_jev_with_its_confidence(match_record, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", JEV_KEY)

    _Jev(_answer("HOLD", 0.40)).source().choose(_request())

    events = [(line["kind"], line["data"]) for line in match_record() if line["kind"] in ("decision", "fallback")]
    (_, decision), (_, fallback) = events
    assert (decision["source"], decision["answer"], decision["confidence"], decision["outcome"]) == (
        "jev",
        "HOLD",
        0.40,
        "invalid",
    )
    assert fallback == {"intended_source": "jev", "reason": "invalid", "used": "script"}


def test_record_io_holds_the_request_body_as_sent_and_the_response_body_as_it_came_back(match_record, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", JEV_KEY)
    response_body = '{"value": {"choice": "RAID"}, "confidence": 0.82, "trace": "t-19"}'
    service = _Jev(httpx.Response(200, text=response_body))

    service.source().choose(_request(deadline_ms=900))

    (decision,) = [line["data"] for line in match_record() if line["kind"] == "decision"]
    assert decision["io"] == {
        "system": None,
        "user": service.requests[0].content.decode("utf-8"),
        "response_format": ENUM_SCHEMA,
        "params": {"deadline_ms": 900},
        "raw_output": response_body,
        "reasoning_text": None,
    }
    assert json.loads(decision["io"]["user"])["state"]["context"] == "Minute 6. We have 8 tanks."


@pytest.mark.parametrize(
    "answer, outcome",
    [(_answer("HOLD", 0.40), "invalid"), (httpx.Response(503, json={"error": "refused"}), "error")],
    ids=["low confidence", "refused"],
)
def test_record_io_keeps_the_response_body_of_an_answer_that_is_not_used(match_record, monkeypatch, answer, outcome):
    monkeypatch.setenv("JEV_API_KEY", JEV_KEY)

    _Jev(answer).source().choose(_request())

    (decision,) = [line["data"] for line in match_record() if line["kind"] == "decision"]
    assert (decision["outcome"], decision["io"]["raw_output"]) == (outcome, answer.text)


def test_record_no_io_is_written_without_a_key_since_nothing_is_sent(match_record, monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)

    _Jev().source().choose(_request())

    (decision,) = [line["data"] for line in match_record() if line["kind"] == "decision"]
    assert decision["reason"] == "no_api_key" and "io" not in decision
