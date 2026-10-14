"""`ClaudeSource` as game code uses it, with HTTP replaced by a stub transport, and its per-match token cap.

Each test asks through the public names of `ucla_agent.core.decide` and
checks what was sent to the stand-in for the Messages API, the result, and
the events the request left in the match record. No test reaches Anthropic.
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from ucla_agent.core.config import ConfigError
from ucla_agent.core.decide import (
    ChainSource,
    ChoiceRequest,
    ClaudeSettings,
    ClaudeSource,
    LocalModelSource,
    Option,
    TextRequest,
    TextResult,
    build_chain,
    settings,
)
from ucla_agent.core.decide import claude as claude_module
from ucla_agent.core.decide import local as local_module
from ucla_agent.core.secrets import REDACTED, SecretDestinationError

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_URL = "https://api.anthropic.com"
MESSAGES_URL = f"{CLAUDE_URL}/v1/messages"
MODEL_SERVER_URL = "http://127.0.0.1:11500"
API_KEY = "claude-test-key-5e1b7c9a3d26f804"
SONNET = "claude-sonnet-4-5"
SETTINGS = ClaudeSettings(
    enabled=True,
    base_url=CLAUDE_URL,
    api_version="2023-06-01",
    max_tokens_per_match=300000,
    models={"sonnet": SONNET, "haiku": "claude-haiku-4-5"},
)
OPTIONS = [Option("S3", "Vote for seat 3."), Option("S5", "Vote for seat 5.")]
CONTEXT = "Day 2. Seat 3 claimed seer and said seat 5 is a wolf."
INSTRUCTION = "Write your message for the day discussion."
MAX_WORDS = 40
CAP = 3000
INPUT_PER_CALL = 900
OUTPUT_PER_CALL = 100
INSTANT_LIMIT_SECONDS = 0.005
DEFAULT_TEXT_SYSTEM_PROMPT = "You write one short message. Answer with the message text only."


def _choice_request(deadline_ms: int = 1500) -> ChoiceRequest:
    return ChoiceRequest("Who do you vote for?", OPTIONS, CONTEXT, deadline_ms, "day-2-vote")


def _text_request(system: str | None = None) -> TextRequest:
    return TextRequest(INSTRUCTION, CONTEXT, MAX_WORDS, 1500, "day-2", system)


def _message(
    written: str = '{"choice": "S5"}',
    *,
    stop_reason: str = "end_turn",
    usage: tuple[int, int] = (612, 9),
) -> httpx.Response:
    """A Messages API answer as the service gives it."""
    return httpx.Response(
        200,
        json={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": SONNET,
            "content": [{"type": "text", "text": written}],
            "stop_reason": stop_reason,
            "usage": {"input_tokens": usage[0], "output_tokens": usage[1]},
        },
    )


class _Claude:
    """Stands in for the Messages API: remembers every request and answers as the test says."""

    def __init__(self, answer: Callable[[], httpx.Response] = _message, seconds: float = 0.0) -> None:
        self.requests: list[httpx.Request] = []
        self._answer = answer
        self._seconds = seconds
        self.transport = httpx.MockTransport(self._respond)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        time.sleep(self._seconds)
        return self._answer()

    def source(self, alias: str = "sonnet", **changes: Any) -> ClaudeSource:
        return ClaudeSource(alias, settings=dataclasses.replace(SETTINGS, **changes), transport=self.transport)

    def sent_body(self, index: int = 0) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


@pytest.fixture
def key(monkeypatch) -> str:
    monkeypatch.setenv("ANTHROPIC_API_KEY", API_KEY)
    return API_KEY


def _of_kind(lines: list[dict], kind: str) -> list[dict]:
    return [line["data"] for line in lines if line["kind"] == kind]


def _timed(call: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    result = call()
    return result, time.perf_counter() - started


# -- (1) a choice ----------------------------------------------------------------------------------


def test_a_choice_call_sends_the_key_the_version_the_model_16_tokens_and_temperature_0_and_uses_the_choice(
    match_record, key
):
    claude = _Claude(lambda: _message('{"choice": "S5"}'))

    result = claude.source().choose(_choice_request())

    (request,) = claude.requests
    body = claude.sent_body()
    assert str(request.url) == MESSAGES_URL
    assert (request.headers["x-api-key"], request.headers["anthropic-version"]) == (API_KEY, "2023-06-01")
    assert (body["model"], body["max_tokens"], body["temperature"]) == (SONNET, 16, 0)
    assert body["system"] == local_module.SYSTEM_PROMPT
    assert body["messages"] == [
        {
            "role": "user",
            "content": f"{CONTEXT}\n\nWho do you vote for?\nOptions:\n- S3: Vote for seat 3.\n- S5: Vote for seat 5.\n"
            'Answer with {"choice": "<id>"} only.',
        }
    ]
    assert (result.option_id, result.source, result.outcome) == ("S5", "claude:sonnet", "used")
    (decision,) = _of_kind(match_record(), "decision")
    assert (decision["model"], decision["input_tokens"], decision["output_tokens"]) == (SONNET, 612, 9)
    assert decision["latency_ms"] == result.latency_ms


def test_the_choice_is_read_from_the_first_json_object_of_the_reply(match_record, key):
    claude = _Claude(lambda: _message('Seat 5 lied. {"choice": "S3"} and then {"choice": "S5"}'))

    result = claude.source().choose(_choice_request())

    assert (result.option_id, result.outcome) == ("S3", "used")


# -- (2) answers that are not a choice -------------------------------------------------------------


def test_a_choice_that_is_not_an_option_is_invalid(match_record, key):
    result = _Claude(lambda: _message('{"choice":"zzz"}')).source().choose(_choice_request())

    assert (result.option_id, result.outcome, result.reason) == (None, "invalid", "not_an_option")


@pytest.mark.parametrize(
    "written",
    ["S5", "{choice: S5}", "", '{"pick": "S5"}', '{"choice": 5}', '["S5"]'],
    ids=["bare id", "not json", "nothing", "no choice key", "not a string", "a list"],
)
def test_a_reply_without_a_json_object_that_holds_a_choice_is_a_malformed_answer(match_record, key, written):
    result = _Claude(lambda: _message(written)).source().choose(_choice_request())

    assert (result.option_id, result.outcome, result.reason) == (None, "error", "malformed_answer")


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, json={"content": "S5"}),
        httpx.Response(200, json={"type": "message"}),
        httpx.Response(200, json=["S5"]),
        httpx.Response(200, text="<html>Service Unavailable</html>"),
    ],
    ids=["content not a list", "no content", "not an object", "not json"],
)
def test_a_body_that_is_no_messages_answer_is_a_malformed_answer(match_record, key, answer):
    result = _Claude(lambda: answer).source().choose(_choice_request())

    assert (result.outcome, result.reason) == ("error", "malformed_answer")


# -- (3) a text ------------------------------------------------------------------------------------


def test_a_text_call_sends_3_tokens_per_word_and_temperature_0_7_and_returns_the_text_with_its_tokens(
    match_record, key
):
    claude = _Claude(lambda: _message('"Seat 3, why did you wait until today?"', usage=(540, 14)))

    result = claude.source().write(_text_request())

    body = claude.sent_body()
    assert (body["model"], body["max_tokens"], body["temperature"]) == (SONNET, 3 * MAX_WORDS, 0.7)
    assert body["system"] == DEFAULT_TEXT_SYSTEM_PROMPT
    assert body["messages"] == [{"role": "user", "content": f"{CONTEXT}\n\n{INSTRUCTION}\nAt most {MAX_WORDS} words."}]
    assert result == TextResult(
        "Seat 3, why did you wait until today?", "claude:sonnet", result.latency_ms, "used", None, 540, 14
    )
    (decision,) = _of_kind(match_record(), "text_decision")
    assert (decision["model"], decision["input_tokens"], decision["output_tokens"]) == (SONNET, 540, 14)
    assert decision["text"] == "Seat 3, why did you wait until today?"


def test_a_text_request_s_own_system_prompt_is_sent_and_an_empty_text_is_invalid(match_record, key):
    claude = _Claude(lambda: _message("  "))

    result = claude.source().write(_text_request(system="You are seat 4, a villager."))

    assert claude.sent_body()["system"] == "You are seat 4, a villager."
    assert (result.text, result.outcome, result.reason) == (None, "invalid", "empty_text")


# -- (4) no key, or not enabled --------------------------------------------------------------------


def test_without_a_key_nothing_is_sent_and_the_call_is_an_error_at_once(match_record, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    claude = _Claude()
    source = claude.source()

    choice, choice_seconds = _timed(lambda: source.choose(_choice_request()))
    written, text_seconds = _timed(lambda: source.write(_text_request()))
    later = [source.choose(_choice_request()).reason for _ in range(4)]

    assert (choice.outcome, choice.reason, written.outcome, written.reason) == ("error", "no_key", "error", "no_key")
    assert max(choice_seconds, text_seconds) < INSTANT_LIMIT_SECONDS
    assert later == ["no_key"] * 4
    assert claude.requests == []
    lines = match_record()
    assert [data["reason"] for data in _of_kind(lines, "fallback")] == ["error"] * 6
    assert {data["model"] for data in _of_kind(lines, "decision") + _of_kind(lines, "text_decision")} == {SONNET}


def test_while_claude_is_not_enabled_nothing_is_sent_even_with_a_key(match_record, key):
    claude = _Claude()
    source = claude.source(enabled=False)

    choice, choice_seconds = _timed(lambda: source.choose(_choice_request()))
    written, text_seconds = _timed(lambda: source.write(_text_request()))

    assert (choice.outcome, choice.reason, written.outcome, written.reason) == (
        "error",
        "disabled",
        "error",
        "disabled",
    )
    assert max(choice_seconds, text_seconds) < INSTANT_LIMIT_SECONDS
    assert claude.requests == []


# -- (5) the service refuses the request -----------------------------------------------------------


@pytest.mark.parametrize(
    "status, reason",
    [(429, "rate_limited"), (529, "rate_limited"), (400, "http_400"), (401, "http_401"), (500, "http_500")],
)
def test_a_refused_request_is_an_error_and_429_and_529_are_rate_limited(match_record, key, status, reason):
    answer = httpx.Response(status, json={"type": "error", "error": {"type": "rate_limit_error"}})
    claude = _Claude(lambda: answer)

    choice = claude.source().choose(_choice_request())
    written = claude.source().write(_text_request())

    assert (choice.outcome, choice.reason, written.outcome, written.reason) == ("error", reason, "error", reason)


def test_a_service_that_cannot_be_reached_gives_an_error_and_never_raises(match_record, key):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    source = ClaudeSource("sonnet", settings=SETTINGS, transport=httpx.MockTransport(refuse))

    assert source.choose(_choice_request()).reason == "connection_failed"


# -- (6) a refusal ---------------------------------------------------------------------------------


def test_a_refusal_is_invalid_for_a_choice_and_for_a_text(match_record, key):
    claude = _Claude(lambda: _message("", stop_reason="refusal", usage=(600, 1)))

    choice = claude.source().choose(_choice_request())
    written = claude.source().write(_text_request())

    assert (choice.option_id, choice.outcome, choice.reason) == (None, "invalid", "refusal")
    assert (written.text, written.outcome, written.reason, written.input_tokens) == (None, "invalid", "refusal", 600)


# -- (7) and (8) the key ---------------------------------------------------------------------------


def test_the_client_that_holds_the_key_refuses_to_send_to_another_host(match_record, key):
    claude = _Claude()
    source = claude.source()

    with pytest.raises(SecretDestinationError):
        source._client.request("POST", "https://collector.example.test/v1/messages", json_body={}, timeout=1.0)

    assert claude.requests == []


def test_no_event_of_the_match_record_holds_the_key_or_its_header(match_record, key):
    answers = iter(
        [
            _message('{"choice": "S5"}'),
            _message("I vote seat 5."),
            httpx.Response(429),
            _message("", stop_reason="refusal"),
            _message("no choice here"),
        ]
    )
    claude = _Claude(lambda: next(answers))
    source = claude.source()

    source.choose(_choice_request())
    source.write(_text_request())
    source.choose(_choice_request())
    source.write(_text_request())
    source.choose(_choice_request())
    record_text = json.dumps(match_record())

    assert len(claude.requests) == 5
    assert all(request.headers["x-api-key"] == API_KEY for request in claude.requests)
    assert API_KEY not in record_text
    assert "x-api-key" not in record_text
    assert REDACTED not in record_text


# -- the io of a call ------------------------------------------------------------------------------


def test_record_io_of_a_choice_holds_the_system_prompt_the_user_message_with_its_answer_line_and_the_raw_reply(
    match_record, key
):
    written = 'Seat 5 lied. {"choice": "S5"}'
    claude = _Claude(lambda: _message(written))
    request = _choice_request(deadline_ms=1400)

    claude.source().choose(request)

    (decision,) = _of_kind(match_record(), "decision")
    io, body = decision["io"], claude.sent_body()
    assert (io["system"], io["user"]) == (body["system"], body["messages"][0]["content"])
    assert io["user"] == f'{local_module.choice_prompt(request)}\nAnswer with {{"choice": "<id>"}} only.'
    assert io["system"] == local_module.SYSTEM_PROMPT
    assert io["response_format"] == 'Answer with {"choice": "<id>"} only.'
    assert io["params"] == {
        "model_id": SONNET,
        "temperature": 0,
        "max_tokens": 16,
        "reasoning": "off",
        "deadline_ms": 1400,
    }
    assert (io["raw_output"], io["reasoning_text"]) == (written, None)


def test_record_io_of_a_text_holds_its_system_prompt_its_prompt_and_the_raw_text(match_record, key):
    claude = _Claude(lambda: _message('"Seat 3, why did you wait?"\n'))

    claude.source().write(_text_request(system="You are seat 4, a villager."))

    (decision,) = _of_kind(match_record(), "text_decision")
    assert decision["text"] == "Seat 3, why did you wait?"
    assert decision["io"] == {
        "system": "You are seat 4, a villager.",
        "user": f"{CONTEXT}\n\n{INSTRUCTION}\nAt most {MAX_WORDS} words.",
        "response_format": None,
        "params": {"model_id": SONNET, "temperature": 0.7, "max_tokens": 120, "reasoning": "off", "deadline_ms": 1500},
        "raw_output": '"Seat 3, why did you wait?"\n',
        "reasoning_text": None,
    }


@pytest.mark.parametrize(
    "answer, outcome, raw",
    [
        (lambda: _message('{"choice":"zzz"}'), "invalid", '{"choice":"zzz"}'),
        (lambda: _message("no choice here"), "error", "no choice here"),
        (lambda: _message("", stop_reason="refusal"), "invalid", ""),
        (lambda: httpx.Response(429, text='{"type":"error"}'), "error", '{"type":"error"}'),
        (
            lambda: httpx.Response(200, text="<html>Service Unavailable</html>"),
            "error",
            "<html>Service Unavailable</html>",
        ),
    ],
    ids=["not an option", "no json object", "refusal", "rate limited", "not json"],
)
def test_record_io_keeps_the_raw_reply_of_an_answer_that_is_not_used(match_record, key, answer, outcome, raw):
    _Claude(answer).source().choose(_choice_request())

    (decision,) = _of_kind(match_record(), "decision")
    assert (decision["outcome"], decision["io"]["raw_output"]) == (outcome, raw)


def test_record_io_holds_the_thinking_blocks_of_a_reply_as_its_reasoning_text(match_record, key):
    reply = _message('{"choice": "S3"}')
    body = json.loads(reply.content)
    body["content"] = [{"type": "thinking", "thinking": "Seat 3 is consistent."}, *body["content"]]
    claude = _Claude(lambda: httpx.Response(200, json=body))

    claude.source().choose(_choice_request())

    (decision,) = _of_kind(match_record(), "decision")
    assert (decision["io"]["raw_output"], decision["io"]["reasoning_text"]) == (
        '{"choice": "S3"}',
        "Seat 3 is consistent.",
    )


def test_record_no_io_is_written_for_a_call_that_sent_nothing(match_record, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    without_key = _Claude().source()
    monkeypatch.setenv("ANTHROPIC_API_KEY", API_KEY)
    disabled = _Claude().source(enabled=False)

    without_key.choose(_choice_request())
    disabled.write(_text_request())

    lines = match_record()
    events = _of_kind(lines, "decision") + _of_kind(lines, "text_decision")
    assert [(data["reason"], "io" in data) for data in events] == [("no_key", False), ("disabled", False)]


# -- a short reason with the choice ----------------------------------------------------------------

REASON_ANSWER_LINE = 'Answer with {"choice": "<id>", "reason": "<at most 12 words>"} only.'


def _asking_reasons(claude: _Claude) -> ClaudeSource:
    return ClaudeSource("sonnet", settings=SETTINGS, choice_reasons=True, transport=claude.transport)


def test_reason_on_a_choice_names_both_keys_in_the_system_prompt_and_the_answer_line_and_sends_56_tokens(
    match_record, key
):
    claude = _Claude(lambda: _message('{"choice": "S5", "reason": "seat 5 contradicted the seer"}'))

    result = _asking_reasons(claude).choose(_choice_request())

    body = claude.sent_body()
    assert (body["max_tokens"], body["temperature"]) == (56, 0)
    assert body["system"] == local_module.REASON_SYSTEM_PROMPT and '"reason"' in body["system"]
    assert body["messages"][0]["content"] == f"{local_module.choice_prompt(_choice_request())}\n{REASON_ANSWER_LINE}"
    assert (result.option_id, result.model_reason) == ("S5", "seat 5 contradicted the seer")
    (decision,) = _of_kind(match_record(), "decision")
    assert decision["model_reason"] == "seat 5 contradicted the seer"
    assert (decision["io"]["response_format"], decision["io"]["params"]["max_tokens"]) == (REASON_ANSWER_LINE, 56)


@pytest.mark.parametrize(
    "written, expected",
    [
        ('{"choice": "S3"}', None),
        ('{"choice": "S3", "reason": ""}', None),
        ('I pick {"choice": "S3", "reason": "' + "r" * 300 + '"}', "r" * 100),
    ],
    ids=["no reason", "empty", "300 characters"],
)
def test_reason_missing_or_too_long_never_keeps_a_valid_choice_from_being_used(match_record, key, written, expected):
    result = _asking_reasons(_Claude(lambda: _message(written))).choose(_choice_request())

    (decision,) = _of_kind(match_record(), "decision")
    assert (result.option_id, result.outcome, result.model_reason) == ("S3", "used", expected)
    assert "model_reason" in decision and decision["model_reason"] == expected


def test_reason_off_the_choice_body_and_the_record_are_as_without_the_switch(match_record, key):
    claude = _Claude(lambda: _message('{"choice": "S5", "reason": "seat 5 lied"}'))

    result = claude.source().choose(_choice_request())

    body = claude.sent_body()
    assert (body["system"], body["max_tokens"]) == (local_module.SYSTEM_PROMPT, 16)
    assert body["messages"][0]["content"].endswith('\nAnswer with {"choice": "<id>"} only.')
    assert (result.option_id, result.model_reason) == ("S5", None)
    (decision,) = _of_kind(match_record(), "decision")
    assert "model_reason" not in decision


def test_reason_is_never_asked_for_a_text(match_record, key):
    claude = _Claude(lambda: _message("Seat 3, why now?"))

    _asking_reasons(claude).write(_text_request())

    assert (claude.sent_body()["max_tokens"], claude.sent_body()["system"]) == (
        3 * MAX_WORDS,
        DEFAULT_TEXT_SYSTEM_PROMPT,
    )
    (decision,) = _of_kind(match_record(), "text_decision")
    assert "model_reason" not in decision


# -- a reason before the choice --------------------------------------------------------------------

REASON_FIRST_ANSWER_LINE = 'Answer with {"reason": "<at most 30 words>", "choice": "<id>"} only.'


def _reason_first_request(max_answer_tokens: int | None = None) -> ChoiceRequest:
    return dataclasses.replace(_choice_request(), reason_first=True, max_answer_tokens=max_answer_tokens)


@pytest.mark.parametrize("choice_reasons", [False, True], ids=["choice_reasons off", "choice_reasons on"])
def test_reason_first_claude_is_asked_for_the_reason_before_the_choice_in_the_local_words_and_cap(
    match_record, key, choice_reasons
):
    claude = _Claude(lambda: _message('{"reason": "seat 5 contradicted the seer twice", "choice": "S5"}'))
    source = ClaudeSource("sonnet", settings=SETTINGS, choice_reasons=choice_reasons, transport=claude.transport)

    result = source.choose(_reason_first_request())

    body = claude.sent_body()
    assert (body["max_tokens"], body["temperature"]) == (2 * 30 + 24, 0)
    assert body["system"] == local_module.reason_first_system_prompt(30)
    assert body["messages"][0]["content"] == (
        f"{local_module.choice_prompt(_choice_request())}\n{REASON_FIRST_ANSWER_LINE}"
    )
    assert (result.option_id, result.model_reason) == ("S5", "seat 5 contradicted the seer twice")
    (decision,) = _of_kind(match_record(), "decision")
    assert decision["model_reason"] == "seat 5 contradicted the seer twice"
    assert (decision["io"]["response_format"], decision["io"]["params"]["max_tokens"]) == (REASON_FIRST_ANSWER_LINE, 84)


def test_reason_first_claude_s_cap_is_max_answer_tokens_when_the_request_gives_it(match_record, key):
    claude = _Claude(lambda: _message('{"reason": "seat 5 lied", "choice": "S5"}'))

    claude.source().choose(_reason_first_request(max_answer_tokens=96))

    (decision,) = _of_kind(match_record(), "decision")
    assert claude.sent_body()["max_tokens"] == decision["io"]["params"]["max_tokens"] == 96


@pytest.mark.parametrize(
    "written, expected",
    [
        ('{"choice": "S3"}', None),
        ('{"reason": "", "choice": "S3"}', None),
        ('I pick {"reason": "' + "r" * 300 + '", "choice": "S3"}', "r" * 240),
    ],
    ids=["no reason", "empty", "300 characters"],
)
def test_reason_first_claude_uses_a_valid_choice_with_a_missing_or_over_long_reason(
    match_record, key, written, expected
):
    result = _Claude(lambda: _message(written)).source().choose(_reason_first_request())

    (decision,) = _of_kind(match_record(), "decision")
    assert (result.option_id, result.outcome, result.model_reason) == ("S3", "used", expected)
    assert "model_reason" in decision and decision["model_reason"] == expected


@pytest.mark.parametrize(
    "choice_reasons, system, max_tokens, answer_line",
    [
        (False, local_module.SYSTEM_PROMPT, 16, 'Answer with {"choice": "<id>"} only.'),
        (True, local_module.REASON_SYSTEM_PROMPT, 56, REASON_ANSWER_LINE),
    ],
    ids=["choice_reasons off", "choice_reasons on"],
)
def test_reason_first_off_claude_s_body_is_today_s_for_both_choice_reasons_values(
    match_record, key, choice_reasons, system, max_tokens, answer_line
):
    claude = _Claude()
    source = ClaudeSource("sonnet", settings=SETTINGS, choice_reasons=choice_reasons, transport=claude.transport)

    source.choose(dataclasses.replace(_choice_request(), reason_first=False, max_answer_tokens=160))

    assert claude.sent_body() == {
        "model": SONNET,
        "system": system,
        "messages": [{"role": "user", "content": f"{local_module.choice_prompt(_choice_request())}\n{answer_line}"}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }


# -- a request's own system text and answer tokens -------------------------------------------------

OWN_SYSTEM_TEXT = 'Choose one OPEN plan. Return only {"choice":"ID","reason":"up to 12 words"}.'


@pytest.mark.parametrize("reason_first", [False, True], ids=["reason_first off", "reason_first on"])
def test_system_text_and_answer_tokens_claude_gets_that_system_prompt_the_plain_message_and_that_cap(
    match_record, key, reason_first
):
    claude = _Claude(lambda: _message('{"choice": "S5", "reason": "seat 5 contradicted the seer"}'))
    request = dataclasses.replace(
        _choice_request(), question="OPEN:", reason_first=reason_first, system_text=OWN_SYSTEM_TEXT, answer_tokens=48
    )

    result = _asking_reasons(claude).choose(request)

    body = claude.sent_body()
    user = f"{CONTEXT}\nOPEN:\nS3: Vote for seat 3.\nS5: Vote for seat 5."
    assert (body["system"], body["max_tokens"]) == (OWN_SYSTEM_TEXT, 48)
    assert body["messages"][0]["content"] == f"{user}\n{claude_module.answer_format(request, True)}"
    assert (result.option_id, result.model_reason) == ("S5", "seat 5 contradicted the seer")
    (decision,) = _of_kind(match_record(), "decision")
    assert (decision["io"]["system"], decision["io"]["params"]["max_tokens"]) == (OWN_SYSTEM_TEXT, 48)


def test_system_text_and_answer_tokens_unset_claude_s_body_is_today_s(match_record, key):
    claude = _Claude()

    _asking_reasons(claude).choose(_choice_request())

    body = claude.sent_body()
    assert (body["system"], body["max_tokens"]) == (local_module.REASON_SYSTEM_PROMPT, 56)
    assert body["messages"][0]["content"] == f"{local_module.choice_prompt(_choice_request())}\n{REASON_ANSWER_LINE}"


# -- a request's own with_reason -------------------------------------------------------------------


def test_with_reason_false_claude_is_asked_the_choice_alone_whatever_choice_reasons_says(match_record, key):
    claude = _Claude(lambda: _message('{"choice": "S5", "reason": "not asked for"}'))
    request = dataclasses.replace(_choice_request(), system_text=OWN_SYSTEM_TEXT, answer_tokens=16, with_reason=False)

    result = _asking_reasons(claude).choose(request)

    body = claude.sent_body()
    assert (body["system"], body["max_tokens"]) == (OWN_SYSTEM_TEXT, 16)
    assert body["messages"][0]["content"].endswith('\nAnswer with {"choice": "<id>"} only.')
    assert (result.option_id, result.model_reason) == ("S5", None)
    (decision,) = _of_kind(match_record(), "decision")
    assert "model_reason" not in decision


def test_with_reason_true_claude_is_asked_a_reason_by_a_source_without_choice_reasons(match_record, key):
    claude = _Claude(lambda: _message('{"choice": "S5", "reason": "seat 5 lied"}'))

    result = claude.source().choose(dataclasses.replace(_choice_request(), with_reason=True))

    body = claude.sent_body()
    assert (body["system"], body["max_tokens"]) == (local_module.REASON_SYSTEM_PROMPT, 56)
    assert body["messages"][0]["content"].endswith(REASON_ANSWER_LINE)
    assert result.model_reason == "seat 5 lied"


# -- (9) chains and settings -----------------------------------------------------------------------


MODELS_TOML = """
[server]
host = "127.0.0.1"
port = 11777
backend_port_base = 41500

[roles.fast]
model_path = "C:/Models/gguf/fast.gguf"
required = false
ctx_size = 8192
temperature = 0
reasoning = "off"
"""
AGENT_TOML = """
[models]
autostart = false

[decide]
default_deadline_ms = 1500
choice_reasons = false

[decide.chains]
werewolf_vote = ["claude:sonnet", "local:fast"]

[jev]
enabled = false
base_url = "https://api.jevai.net"
min_confidence = 0.55

[claude]
enabled = true
base_url = "https://api.anthropic.com"
api_version = "2023-06-01"
max_tokens_per_match = 300000

[claude.models]
sonnet = "claude-sonnet-4-5"
opus = "claude-opus-4-1"
haiku = "claude-haiku-4-5"
"""


def _write_config(root: Path, agent_toml: str = AGENT_TOML) -> Path:
    config = root / "config"
    config.mkdir(exist_ok=True)
    (config / "agent.toml").write_bytes(agent_toml.encode("utf-8"))
    (config / "models.toml").write_bytes(MODELS_TOML.encode("utf-8"))
    return config / "agent.toml"


def test_build_chain_accepts_a_claude_entry_and_asks_claude_before_the_local_role(match_record, key, run_folder):
    _write_config(run_folder)
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _message('{"choice": "S3"}')

    chain = build_chain("werewolf_vote", transport=httpx.MockTransport(answer))
    result = chain.choose(_choice_request())

    assert [str(request.url) for request in seen] == [MESSAGES_URL]
    assert json.loads(seen[0].content)["model"] == SONNET
    assert (result.option_id, result.source, result.outcome) == ("S3", "claude:sonnet", "used")


def test_a_chain_entry_naming_a_claude_model_the_file_does_not_define_is_a_configuration_error(run_folder):
    path = _write_config(run_folder, AGENT_TOML.replace('["claude:sonnet", "local:fast"]', '["claude:nosuch"]'))

    with pytest.raises(ConfigError) as at_load:
        settings.load(run_folder)
    with pytest.raises(ConfigError) as at_build:
        build_chain("werewolf_vote")

    expected = f"{path}: the chain werewolf_vote names the Claude model nosuch, which [claude.models] does not define."
    assert str(at_load.value) == str(at_build.value) == expected


def test_a_claude_entry_without_an_alias_is_not_a_chain_entry(run_folder):
    _write_config(run_folder, AGENT_TOML.replace('["claude:sonnet", "local:fast"]', '["claude:"]'))

    with pytest.raises(ConfigError, match="werewolf_vote in \\[decide.chains\\] must be a list of"):
        settings.load(run_folder)


def test_a_disabled_claude_entry_stays_in_its_chain_and_the_chain_goes_on_without_sending_to_it(
    match_record, key, run_folder
):
    _write_config(run_folder, AGENT_TOML.replace("enabled = true", "enabled = false"))
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"choice": "S5"}'}}]})

    result = build_chain("werewolf_vote", transport=httpx.MockTransport(answer)).choose(_choice_request())

    assert seen == ["http://127.0.0.1:11777/v1/chat/completions"]
    assert (result.option_id, result.source) == ("S5", "local:fast")
    assert [(data["source"], data["reason"]) for data in _of_kind(match_record(), "decision")] == [
        ("claude:sonnet", "disabled"),
        ("local:fast", None),
    ]


def test_the_shipped_file_turns_claude_off_names_the_three_models_and_no_red_alert_chain_names_claude():
    shipped = settings.load(REPO_ROOT)

    assert shipped.claude == ClaudeSettings(
        enabled=False,
        base_url="https://api.anthropic.com",
        api_version="2023-06-01",
        max_tokens_per_match=300000,
        models={"sonnet": "claude-sonnet-4-5", "opus": "claude-opus-4-1", "haiku": "claude-haiku-4-5"},
    )
    red_alert_entries = [entry for name, entries in shipped.chains.items() if "red_alert" in name for entry in entries]
    assert red_alert_entries and not any(entry.startswith("claude:") for entry in red_alert_entries)


def test_a_source_built_from_its_alias_alone_reads_the_settings_of_the_repository_root(run_folder, key):
    _write_config(run_folder)

    assert ClaudeSource("opus").name == "claude:opus"
    with pytest.raises(ConfigError, match=r"\[claude.models\] has no model named nosuch"):
        ClaudeSource("nosuch")


@pytest.mark.parametrize(
    "change, expected",
    [
        (("[claude", "[claud"), "must have a [claude] table"),
        (('base_url = "https://api.anthropic.com"', 'base_url = "http://api.anthropic.com"'), "base_url in [claude]"),
        (("max_tokens_per_match = 300000", "max_tokens_per_match = 0"), "max_tokens_per_match in [claude] must be"),
        (('api_version = "2023-06-01"', "api_version = 2023"), "api_version in [claude] must be a string"),
        (('haiku = "claude-haiku-4-5"', "haiku = 45"), "haiku in [claude.models] must be a string"),
        (
            ("enabled = true\nbase_url", "enabled = true\nretries = 2\nbase_url"),
            "[claude] has no setting named retries",
        ),
        (("\n[claude.models]\n", "\n[claude.aliases]\n"), "must have a [claude.models] table"),
    ],
    ids=["no table", "not tls", "cap zero", "version a number", "model id a number", "unknown key", "no models"],
)
def test_a_claude_setting_that_is_missing_of_the_wrong_kind_or_unknown_is_a_configuration_error(
    run_folder, change, expected
):
    path = _write_config(run_folder, AGENT_TOML.replace(*change))

    with pytest.raises(ConfigError) as raised:
        settings.load(run_folder)

    assert str(path) in str(raised.value)
    assert expected in str(raised.value)


# -- the per-match token cap -----------------------------------------------------------------------


def _capped_claude() -> _Claude:
    return _Claude(lambda: _message('{"choice": "S5"}', usage=(INPUT_PER_CALL, OUTPUT_PER_CALL)))


def test_token_cap_the_first_three_calls_are_made_and_the_fourth_is_refused_at_once_without_a_request(
    match_record, key
):
    claude = _capped_claude()
    source = claude.source(max_tokens_per_match=CAP)

    first_three = [source.choose(_choice_request()) for _ in range(3)]
    fourth, seconds = _timed(lambda: source.choose(_choice_request()))

    assert [(result.option_id, result.outcome) for result in first_three] == [("S5", "used")] * 3
    assert (fourth.option_id, fourth.outcome, fourth.reason) == (None, "error", "token_cap")
    assert seconds < INSTANT_LIMIT_SECONDS
    assert len(claude.requests) == 3


def test_token_cap_exactly_one_claude_token_cap_event_is_written(match_record, key):
    source = _capped_claude().source(max_tokens_per_match=CAP)

    for _ in range(6):
        source.choose(_choice_request())

    assert _of_kind(match_record(), "claude_token_cap") == [
        {"source": "claude:sonnet", "model": SONNET, "count": 3000, "cap": CAP}
    ]


def test_token_cap_a_chain_whose_claude_source_is_over_the_cap_returns_the_local_answer(match_record, key):
    claude = _capped_claude()
    capped = claude.source(max_tokens_per_match=CAP)
    for _ in range(3):
        capped.choose(_choice_request())
    local_requests: list[httpx.Request] = []

    def local_answer(request: httpx.Request) -> httpx.Response:
        local_requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"choice": "S3"}'}}]})

    working_local = LocalModelSource("fast", base_url=MODEL_SERVER_URL, transport=httpx.MockTransport(local_answer))

    result = ChainSource([capped, working_local]).choose(_choice_request())

    assert (result.option_id, result.source, result.outcome) == ("S3", "local:fast", "used")
    assert (len(claude.requests), len(local_requests)) == (3, 1)
    decisions = _of_kind(match_record(), "decision")
    assert [(data["source"], data["reason"]) for data in decisions[-2:]] == [
        ("claude:sonnet", "token_cap"),
        ("local:fast", None),
    ]


def test_token_cap_a_new_instance_starts_at_zero(match_record, key):
    claude = _capped_claude()
    first = claude.source(max_tokens_per_match=CAP)
    for _ in range(4):
        first.choose(_choice_request())

    second = claude.source(max_tokens_per_match=CAP)
    results = [second.choose(_choice_request()).outcome for _ in range(3)]

    assert results == ["used"] * 3
    assert len(claude.requests) == 6


def test_token_cap_the_claude_usage_event_reports_the_tokens_and_calls_of_the_model(match_record, key):
    claude = _capped_claude()
    source = claude.source(max_tokens_per_match=CAP)
    for _ in range(4):
        source.choose(_choice_request())

    source.write_usage_event()

    assert _of_kind(match_record(), "claude_usage") == [
        {"source": "claude:sonnet", "model": SONNET, "input_tokens": 2700, "output_tokens": 300, "calls": 3}
    ]


def test_token_cap_a_call_that_starts_below_the_cap_is_made_and_texts_count_too(match_record, key):
    claude = _capped_claude()
    source = claude.source(max_tokens_per_match=2500)

    made = [source.write(_text_request()).outcome, source.choose(_choice_request()).outcome]
    third = source.write(_text_request())
    fourth = source.write(_text_request())

    assert made == ["used", "used"]
    assert third.outcome == "used"
    assert (fourth.outcome, fourth.reason) == ("error", "token_cap")
    assert len(claude.requests) == 3
    assert _of_kind(match_record(), "claude_token_cap") == [
        {"source": "claude:sonnet", "model": SONNET, "count": 3000, "cap": 2500}
    ]


def test_token_cap_refusals_do_not_open_the_circuit(match_record, key):
    source = _capped_claude().source(max_tokens_per_match=1000)
    source.choose(_choice_request())

    reasons = [source.choose(_choice_request()).reason for _ in range(5)]

    assert reasons == ["token_cap"] * 5


def test_token_cap_the_usage_event_is_written_once_and_a_chain_forwards_it_to_its_claude_sources(match_record, key):
    claude = _capped_claude()
    sonnet, haiku = claude.source(), claude.source("haiku")
    local = LocalModelSource("fast", base_url=MODEL_SERVER_URL, transport=claude.transport)
    chain = ChainSource([sonnet, local, ChainSource([haiku])])
    sonnet.choose(_choice_request())

    chain.write_usage_event()
    chain.write_usage_event()

    assert _of_kind(match_record(), "claude_usage") == [
        {"source": "claude:sonnet", "model": SONNET, "input_tokens": 900, "output_tokens": 100, "calls": 1},
        {"source": "claude:haiku", "model": "claude-haiku-4-5", "input_tokens": 0, "output_tokens": 0, "calls": 0},
    ]
