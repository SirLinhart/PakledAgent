"""Text requests to the decision sources as game code makes them, with HTTP replaced by a stub transport.

Each test opens a match record, asks through the public names of
`ucla_agent.core.decide`, and checks the result together with the
`text_decision` and `fallback` events the request left in the record.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

import httpx
import pytest

from ucla_agent.core.decide import (
    ChainSource,
    ChoiceRequest,
    JevSource,
    LocalModelSource,
    Option,
    TextRequest,
    TextResult,
    text,
)
from ucla_agent.core.secrets import REDACTED

MODEL_SERVER_URL = "http://127.0.0.1:11500"
JEV_URL = "https://api.jevai.net"
JEV_KEY = "jev-4f9c2d7e1a6b8053"
INSTRUCTION = "Write your message for the day discussion."
CONTEXT = "Day 2. Seat 3 claimed seer and said seat 5 is a wolf."
STATE_TAG = "day-2"
MAX_WORDS = 40
SLOW_ANSWER_SECONDS = 0.4
DEFAULT_SYSTEM_PROMPT = "You write one short message. Answer with the message text only."
SECRET_NAME = "PRACTICE_SERVICE_API_KEY"
SECRET_VALUE = "practice-7d2e9a41c6b3f805"
CIRCUIT_OPEN_LIMIT_SECONDS = 0.005
TEXT_DECISION_KEYS = {
    "state_tag",
    "source",
    "instruction",
    "context",
    "text",
    "latency_ms",
    "outcome",
    "reason",
    "input_tokens",
    "output_tokens",
}


def _request(deadline_ms: int = 1500, context: str = CONTEXT, system: str | None = None) -> TextRequest:
    return TextRequest(INSTRUCTION, context, MAX_WORDS, deadline_ms, STATE_TAG, system)


def _completion(content: Any, usage: dict[str, Any] | None = None) -> httpx.Response:
    body: dict[str, Any] = {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


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

    def sent_body(self) -> dict[str, Any]:
        (request,) = self.requests
        return json.loads(request.content)


def _events(lines: list[dict]) -> list[tuple[str, Any, Any]]:
    """The text_decision and fallback events, each as (kind, source, outcome or what decided instead)."""
    events = []
    for line in lines:
        data = line["data"]
        if line["kind"] == "text_decision":
            events.append(("text_decision", data["source"], data["outcome"]))
        elif line["kind"] == "fallback":
            events.append(("fallback", data["intended_source"], data["used"]))
    return events


def _text_decisions(lines: list[dict]) -> list[dict]:
    return [line["data"] for line in lines if line["kind"] == "text_decision"]


# -- (1) the local model's text --------------------------------------------------------------------


def test_the_local_model_s_text_is_used_stripped_and_the_request_pins_temperature_max_tokens_and_no_format(
    match_record,
):
    stub = _Stub(lambda: _completion('  "Seat 5, where were you last night?"  \n'))

    result = stub.model_source().write(_request())

    body = stub.sent_body()
    assert str(stub.requests[0].url) == f"{MODEL_SERVER_URL}/v1/chat/completions"
    assert result == TextResult("Seat 5, where were you last night?", "local:fast", result.latency_ms, "used")
    assert (body["model"], body["temperature"], body["max_tokens"]) == ("fast", 0.7, 3 * MAX_WORDS)
    assert "response_format" not in body
    assert body["messages"] == [
        {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": f"{CONTEXT}\n\n{INSTRUCTION}\nAt most {MAX_WORDS} words."},
    ]
    assert _events(match_record()) == [("text_decision", "local:fast", "used")]


def test_a_request_s_own_system_prompt_replaces_the_default(match_record):
    stub = _Stub(lambda: _completion("I am the seer."))

    stub.model_source().write(_request(system="You are a villager in a game of Werewolf."))

    assert stub.sent_body()["messages"][0] == {"role": "system", "content": "You are a villager in a game of Werewolf."}


@pytest.mark.parametrize(
    "written, expected",
    [
        ("Seat 5 lied.", "Seat 5 lied."),
        ('"Seat 5 lied."', "Seat 5 lied."),
        ("'Seat 5 lied.'", "Seat 5 lied."),
        ("“Seat 5 lied.”", "Seat 5 lied."),
        ('  " Seat 5 lied. "  ', "Seat 5 lied."),
        ('""Seat 5 lied.""', '"Seat 5 lied."'),
        ('"Seat 5 lied.', '"Seat 5 lied.'),
        ('Seat 5 said "no".', 'Seat 5 said "no".'),
        ('"', '"'),
    ],
    ids=["plain", "double", "single", "typographic", "spaces inside", "two pairs", "unpaired", "inner quotes", "one"],
)
def test_a_written_text_loses_its_surrounding_whitespace_and_one_pair_of_quotes(written, expected):
    assert text.cleaned(written) == expected


# -- (2) an empty answer ---------------------------------------------------------------------------


@pytest.mark.parametrize("written", ["", "   \n", '""', "' '"], ids=["empty", "blank", "empty quotes", "blank quotes"])
def test_an_empty_answer_is_invalid(match_record, written):
    stub = _Stub(lambda: _completion(written))

    result = stub.model_source().write(_request())

    assert (result.text, result.outcome, result.reason) == (None, "invalid", "empty_text")
    assert _events(match_record()) == [("text_decision", "local:fast", "invalid"), ("fallback", "local:fast", "script")]


@pytest.mark.parametrize(
    "body",
    [{"choices": []}, {"choices": [{"message": {"content": None}}]}, {"error": "role_down"}],
    ids=["no choices", "null content", "no completion"],
)
def test_an_answer_without_a_text_is_a_malformed_answer(match_record, body):
    stub = _Stub(lambda: httpx.Response(200, json=body))

    result = stub.model_source().write(_request())

    assert (result.text, result.outcome, result.reason) == (None, "error", "malformed_answer")


def test_a_server_that_cannot_be_reached_gives_an_error_and_never_raises(match_record):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    source = LocalModelSource("fast", base_url=MODEL_SERVER_URL, transport=httpx.MockTransport(refuse))

    result = source.write(_request())

    assert (result.text, result.outcome, result.reason) == (None, "error", "connection_failed")


# -- (3) the deadline ------------------------------------------------------------------------------


def test_a_text_that_takes_400_ms_under_a_200_ms_deadline_is_a_timeout_after_200_to_300_ms(match_record):
    stub = _Stub(lambda: _completion("Too late."), seconds=SLOW_ANSWER_SECONDS)

    result = stub.model_source().write(_request(deadline_ms=200))

    assert (result.text, result.outcome, result.reason) == (None, "timeout", "deadline")
    assert 200 <= result.latency_ms <= 300
    assert _events(match_record()) == [("text_decision", "local:fast", "timeout"), ("fallback", "local:fast", "script")]


def test_text_and_choice_calls_share_one_circuit_per_source(match_record):
    stub = _Stub(lambda: _completion("Too late."), seconds=0.15)
    source = stub.model_source()

    timeouts = [source.write(_request(deadline_ms=50)).outcome for _ in range(3)]
    started = time.perf_counter()
    choice = source.choose(ChoiceRequest("Vote?", [Option("S3", "Seat 3.")], CONTEXT, 1500, STATE_TAG))
    choice_seconds = time.perf_counter() - started
    text_after = source.write(_request())

    assert timeouts == ["timeout", "timeout", "timeout"]
    assert (choice.outcome, choice.reason) == ("error", "circuit_open")
    assert choice_seconds < CIRCUIT_OPEN_LIMIT_SECONDS
    assert (text_after.outcome, text_after.reason) == ("error", "circuit_open")
    assert len(stub.requests) == 3


# -- (4) a chain -----------------------------------------------------------------------------------


def test_a_chain_of_a_failing_and_a_working_source_writes_the_working_one_s_text_within_the_deadline(match_record):
    failing = _Stub(lambda: httpx.Response(503, json={"error": "role_down"}))
    working = _Stub(lambda: _completion("I vote seat 5."))
    chain = ChainSource([failing.model_source("strong"), working.model_source("fast")])
    started = time.perf_counter()

    result = chain.write(_request(deadline_ms=1000))
    seconds = time.perf_counter() - started

    assert (result.text, result.source, result.outcome) == ("I vote seat 5.", "local:fast", "used")
    assert seconds < 1.0
    assert (len(failing.requests), len(working.requests)) == (1, 1)
    assert _events(match_record()) == [
        ("text_decision", "local:strong", "error"),
        ("fallback", "local:strong", "local:fast"),
        ("text_decision", "local:fast", "used"),
    ]


def test_a_chain_of_no_sources_writes_nothing_and_names_itself(match_record):
    result = ChainSource([], name="werewolf_chat").write(_request())

    assert result == TextResult(None, "werewolf_chat", 0, "error", "no_source")
    lines = match_record()
    assert [line["data"] for line in lines if line["kind"] == "fallback"] == [
        {"intended_source": "werewolf_chat", "reason": "no_source", "used": "script"}
    ]
    assert _text_decisions(lines) == []


# -- (5) Jev ---------------------------------------------------------------------------------------


def test_jev_cannot_write_text_and_sends_nothing(match_record, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", JEV_KEY)
    stub = _Stub(lambda: httpx.Response(200, json={"value": {"choice": "RAID"}, "confidence": 0.9}))
    started = time.perf_counter()

    result = JevSource(JEV_URL, transport=stub.transport).write(_request())
    seconds = time.perf_counter() - started

    assert result == TextResult(None, "jev", 0, "error", "text_unsupported")
    assert stub.requests == []
    assert seconds < CIRCUIT_OPEN_LIMIT_SECONDS
    assert _events(match_record()) == [("text_decision", "jev", "error"), ("fallback", "jev", "script")]


# -- (6) the token counts --------------------------------------------------------------------------


def test_the_token_fields_come_from_the_usage_the_server_reports(match_record):
    stub = _Stub(lambda: _completion("Seat 3 is telling the truth.", {"prompt_tokens": 412, "completion_tokens": 9}))

    result = stub.model_source().write(_request())

    assert (result.input_tokens, result.output_tokens) == (412, 9)
    (decision,) = _text_decisions(match_record())
    assert (decision["input_tokens"], decision["output_tokens"]) == (412, 9)


def test_without_a_usage_the_token_fields_stay_empty(match_record):
    stub = _Stub(lambda: _completion("Seat 3 is telling the truth.", {"prompt_tokens": "many"}))

    result = stub.model_source().write(_request())

    assert (result.outcome, result.input_tokens, result.output_tokens) == ("used", None, None)


# -- (7) the record of all of them -----------------------------------------------------------------


def test_every_text_call_leaves_a_text_decision_event_and_every_result_but_used_a_fallback_event(
    match_record, monkeypatch
):
    monkeypatch.setenv("JEV_API_KEY", JEV_KEY)
    used = _Stub(lambda: _completion("I vote seat 5.")).model_source().write(_request())
    invalid = _Stub(lambda: _completion("")).model_source().write(_request())
    timeout = _Stub(lambda: _completion("Late."), seconds=0.15).model_source().write(_request(deadline_ms=50))
    error = _Stub(lambda: httpx.Response(500)).model_source().write(_request())
    unsupported = JevSource(JEV_URL, transport=_Stub(lambda: httpx.Response(500)).transport).write(_request())

    lines = [line for line in match_record() if line["kind"] in ("text_decision", "fallback")]
    decisions = [line["data"] for line in lines if line["kind"] == "text_decision"]
    fallbacks = [line["data"] for line in lines if line["kind"] == "fallback"]

    assert [result.outcome for result in (used, invalid, timeout, error, unsupported)] == [
        "used",
        "invalid",
        "timeout",
        "error",
        "error",
    ]
    assert [line["kind"] for line in lines] == ["text_decision"] + ["text_decision", "fallback"] * 4
    assert [(decision["outcome"], decision["reason"]) for decision in decisions] == [
        ("used", None),
        ("invalid", "empty_text"),
        ("timeout", "deadline"),
        ("error", "http_500"),
        ("error", "text_unsupported"),
    ]
    assert [(fallback["intended_source"], fallback["reason"]) for fallback in fallbacks] == [
        ("local:fast", "invalid"),
        ("local:fast", "timeout"),
        ("local:fast", "error"),
        ("jev", "error"),
    ]
    assert [decision["text"] for decision in decisions] == ["I vote seat 5.", None, None, None, None]
    # The four calls to the model also record what they sent and got back; Jev sent nothing.
    assert [set(decision) - TEXT_DECISION_KEYS for decision in decisions] == [{"io"}] * 4 + [set()]
    for decision in decisions:
        assert set(decision) >= TEXT_DECISION_KEYS
        assert (decision["state_tag"], decision["instruction"], decision["context"]) == (
            STATE_TAG,
            INSTRUCTION,
            CONTEXT,
        )
    for fallback in fallbacks:
        assert set(fallback) == {"intended_source", "reason", "used"}


# -- (8) secrets -----------------------------------------------------------------------------------


def test_a_secret_value_in_the_context_is_written_to_the_record_and_sent_as_redacted(match_record, monkeypatch):
    monkeypatch.setenv(SECRET_NAME, SECRET_VALUE)
    stub = _Stub(lambda: _completion(f"My key is {SECRET_VALUE}."))

    result = stub.model_source().write(_request(context=f"Seat 4 pasted {SECRET_VALUE} into the chat."))

    lines = match_record()
    (decision,) = _text_decisions(lines)
    assert decision["context"] == f"Seat 4 pasted {REDACTED} into the chat."
    assert decision["text"] == f"My key is {REDACTED}."
    assert SECRET_VALUE not in json.dumps(lines)
    assert SECRET_VALUE not in stub.requests[0].content.decode("utf-8")
    assert result.outcome == "used"
