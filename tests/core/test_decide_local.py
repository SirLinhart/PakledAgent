"""`LocalModelSource`: what it sends to the model server and what it makes of each kind of answer."""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from ucla_agent.core.config import ConfigError
from ucla_agent.core.decide import ChoiceRequest, ChoiceResult, LocalModelSource, Option, TextRequest
from ucla_agent.core.decide import local as local_module
from ucla_agent.core.modelserver.endpoint import Endpoint
from ucla_agent.core.modelserver.roles import Role
from ucla_agent.core.modelserver.settings import RoleSettings
from ucla_agent.core.secrets import REDACTED

ENDPOINT_URL = "http://127.0.0.1:11500"
OPTIONS = [Option("HOLD", "Keep the army at the base."), Option("RAID", "Attack the enemy's harvesters.")]
ENUM_SCHEMA = {"type": "object", "properties": {"choice": {"enum": ["HOLD", "RAID"]}}, "required": ["choice"]}
SLOW_ANSWER_SECONDS = 0.4
MODELS_TOML = b"""
[server]
host = "127.0.0.1"
port = 11777
backend_port_base = 41500
"""


def _request(deadline_ms: int = 1500, context: str = "Minute 6. We have 8 tanks.") -> ChoiceRequest:
    return ChoiceRequest("Which plan?", OPTIONS, context, deadline_ms, "1250")


def _completion(content: Any) -> dict:
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}


class _ModelServer:
    """Stands in for the model server's endpoint: remembers each request and answers as the test says."""

    def __init__(self, answer: httpx.Response | Exception | None = None, seconds: float = 0.0) -> None:
        self.requests: list[httpx.Request] = []
        self._answer = answer if answer is not None else httpx.Response(200, json=_completion('{"choice": "RAID"}'))
        self._seconds = seconds
        self.transport = httpx.MockTransport(self._respond)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        time.sleep(self._seconds)
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer

    def source(self, role: str = "fast") -> LocalModelSource:
        return LocalModelSource(role, base_url=ENDPOINT_URL, transport=self.transport)

    def sent_body(self) -> dict:
        (request,) = self.requests
        return json.loads(request.content)


# -- the request -----------------------------------------------------------------------------------


def test_a_choice_of_the_model_is_used(run_folder):
    server = _ModelServer(httpx.Response(200, json=_completion('{"choice": "RAID"}')))

    result = server.source().choose(_request())

    assert (result.option_id, result.confidence, result.source, result.outcome, result.reason) == (
        "RAID",
        None,
        "local:fast",
        "used",
        None,
    )


def test_a_spaced_answer_naming_a_seat_is_used(run_folder):
    seats = [Option(seat, f"Vote out {seat}.") for seat in ("P1", "P2", "P3")]
    server = _ModelServer(httpx.Response(200, json=_completion('{ "choice" : "P3" }')))

    result = server.source("strong").choose(ChoiceRequest("Whom do you vote out?", seats, "Day 2.", 1500, "d2"))

    assert (result.option_id, result.source, result.outcome, result.reason) == ("P3", "local:strong", "used", None)


def test_the_request_goes_to_the_chat_completions_route_of_the_endpoint_with_the_client_header(run_folder):
    server = _ModelServer()

    server.source().choose(_request())

    (request,) = server.requests
    assert (request.method, str(request.url)) == ("POST", f"{ENDPOINT_URL}/v1/chat/completions")
    assert request.headers["x-client"] == "ucla-agent"
    assert request.headers["content-type"] == "application/json"


def test_the_request_pins_the_role_temperature_0_max_tokens_24_and_the_enum_schema(run_folder):
    server = _ModelServer()

    server.source("strong").choose(_request())

    body = server.sent_body()
    assert (body["model"], body["temperature"], body["max_tokens"]) == ("strong", 0, 24)
    assert body["response_format"] == {"type": "json_schema", "json_schema": {"name": "choice", "schema": ENUM_SCHEMA}}
    assert set(body) == {"model", "messages", "temperature", "max_tokens", "response_format"}


def test_the_prompt_holds_the_context_the_question_and_every_option(run_folder):
    server = _ModelServer()

    server.source().choose(_request())

    system, user = server.sent_body()["messages"]
    assert system == {"role": "system", "content": local_module.SYSTEM_PROMPT}
    assert user == {
        "role": "user",
        "content": (
            "Minute 6. We have 8 tanks.\n\n"
            "Which plan?\n"
            "Options:\n"
            "- HOLD: Keep the army at the base.\n"
            "- RAID: Attack the enemy's harvesters."
        ),
    }


def test_a_key_in_the_context_never_reaches_the_model_server(run_folder, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "jev-4f9c2d7e1a6b8053")
    server = _ModelServer()

    server.source().choose(_request(context="The operator pasted jev-4f9c2d7e1a6b8053 here."))

    (request,) = server.requests
    assert b"jev-4f9c2d7e1a6b8053" not in request.content
    assert f"The operator pasted {REDACTED} here." in server.sent_body()["messages"][1]["content"]


def test_the_http_timeout_is_the_deadline(run_folder):
    server = _ModelServer()

    server.source().choose(_request(deadline_ms=700))

    assert set(server.requests[0].extensions["timeout"].values()) == {0.7}


# -- answers that cannot be used -------------------------------------------------------------------


def test_a_choice_that_is_not_an_option_is_invalid(run_folder):
    server = _ModelServer(httpx.Response(200, json=_completion('{"choice": "zzz"}')))

    result = server.source().choose(_request())

    assert result == ChoiceResult(None, None, "local:fast", result.latency_ms, "invalid", "not_an_option")


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, json=_completion('{"choice": "RA')),
        httpx.Response(200, json=_completion("RAID")),
        httpx.Response(200, json=_completion('["RAID"]')),
        httpx.Response(200, json=_completion('{"plan": "RAID"}')),
        httpx.Response(200, json=_completion('{"choice": 2}')),
        httpx.Response(200, json=_completion(None)),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"error": "no choices here"}),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, content=b"<html>not json</html>"),
    ],
    ids=[
        "cut off",
        "bare word",
        "json array",
        "other key",
        "number",
        "no content",
        "no choice entry",
        "no choices key",
        "body is a list",
        "body is not json",
    ],
)
def test_a_body_without_a_readable_choice_is_an_error(run_folder, answer):
    result = _ModelServer(answer).source().choose(_request())

    assert (result.option_id, result.outcome, result.reason) == (None, "error", "malformed_answer")


@pytest.mark.parametrize(
    "status, body, reason",
    [(404, {"error": "unknown_role"}, "http_404"), (503, {"error": "role_down"}, "http_503"), (500, {}, "http_500")],
)
def test_a_refusal_of_the_model_server_is_an_error_with_its_status(run_folder, status, body, reason):
    result = _ModelServer(httpx.Response(status, json=body)).source().choose(_request())

    assert (result.option_id, result.outcome, result.reason) == (None, "error", reason)


def test_a_model_server_that_is_not_running_is_an_error_and_not_an_exception(run_folder):
    result = _ModelServer(httpx.ConnectError("connection refused")).source().choose(_request())

    assert (result.option_id, result.outcome, result.reason) == (None, "error", "connection_failed")


def test_an_answer_slower_than_the_deadline_is_a_timeout_at_the_deadline(run_folder):
    server = _ModelServer(seconds=SLOW_ANSWER_SECONDS)

    result = server.source().choose(_request(deadline_ms=200))

    assert (result.option_id, result.outcome, result.reason) == (None, "timeout", "deadline")
    assert 200 <= result.latency_ms <= 300


# -- the record ------------------------------------------------------------------------------------


def test_a_call_is_recorded_as_a_decision_of_the_source_local_role(match_record):
    server = _ModelServer(httpx.Response(503, json={"error": "role_down"}))

    server.source().choose(_request())

    events = [(line["kind"], line["data"]) for line in match_record() if line["kind"] in ("decision", "fallback")]
    (_, decision), (_, fallback) = events
    assert (decision["source"], decision["outcome"], decision["reason"], decision["state_version"]) == (
        "local:fast",
        "error",
        "http_503",
        "1250",
    )
    assert decision["request_summary"] == "Which plan? [HOLD, RAID]"
    assert fallback == {"intended_source": "local:fast", "reason": "error", "used": "script"}


def _decisions(match_record, kind: str = "decision") -> list[dict]:
    return [line["data"] for line in match_record() if line["kind"] == kind]


def test_record_io_holds_the_messages_sent_the_format_the_params_and_the_raw_completion(match_record):
    server = _ModelServer(httpx.Response(200, json=_completion('{ "choice": "RAID" }')))
    request = _request(deadline_ms=1200)

    LocalModelSource("fast", base_url=ENDPOINT_URL, reasoning="off", transport=server.transport).choose(request)

    (decision,) = _decisions(match_record)
    io, body = decision["io"], server.sent_body()
    assert io["user"] == local_module.choice_prompt(request)
    assert [io["system"], io["user"]] == [message["content"] for message in body["messages"]]
    assert io["system"] == local_module.SYSTEM_PROMPT
    assert io["response_format"] == body["response_format"]
    assert io["params"] == {
        "model_role": "fast",
        "temperature": 0,
        "max_tokens": 24,
        "reasoning": "off",
        "deadline_ms": 1200,
    }
    assert (io["raw_output"], io["reasoning_text"]) == ('{ "choice": "RAID" }', None)


def test_record_io_keeps_the_raw_output_of_an_answer_that_is_not_an_option(match_record):
    _ModelServer(httpx.Response(200, json=_completion('{"choice": "zzz"}'))).source().choose(_request())

    (decision,) = _decisions(match_record)
    assert (decision["outcome"], decision["io"]["raw_output"]) == ("invalid", '{"choice": "zzz"}')


@pytest.mark.parametrize(
    "answer, raw",
    [
        (httpx.Response(200, json=_completion('{"choice": "RA')), '{"choice": "RA'),
        (httpx.Response(200, content=b"<html>not json</html>"), "<html>not json</html>"),
        (httpx.Response(503, json={"error": "role_down"}), '{"error":"role_down"}'),
    ],
    ids=["cut off", "body is not json", "role down"],
)
def test_record_io_keeps_what_came_back_when_it_cannot_be_read(match_record, answer, raw):
    _ModelServer(answer).source().choose(_request())

    (decision,) = _decisions(match_record)
    assert decision["outcome"] == "error"
    assert decision["io"]["raw_output"].replace(" ", "") == raw.replace(" ", "")


def test_record_io_of_a_timeout_holds_what_was_sent_and_raw_output_null(match_record):
    server = _ModelServer(seconds=SLOW_ANSWER_SECONDS)

    server.source().choose(_request(deadline_ms=200))

    (decision,) = _decisions(match_record)
    assert decision["outcome"] == "timeout"
    assert decision["io"]["user"] == local_module.choice_prompt(_request())
    assert (decision["io"]["params"]["deadline_ms"], decision["io"]["raw_output"]) == (200, None)


def test_record_io_holds_the_reasoning_the_server_returned_apart_from_the_answer(match_record):
    message = {"role": "assistant", "content": '{"choice": "HOLD"}', "reasoning_content": "The base is weak."}
    server = _ModelServer(httpx.Response(200, json={"choices": [{"message": message}]}))

    result = server.source().choose(_request())

    (decision,) = _decisions(match_record)
    assert result.option_id == "HOLD"
    assert (decision["io"]["raw_output"], decision["io"]["reasoning_text"]) == (
        '{"choice": "HOLD"}',
        "The base is weak.",
    )


def test_record_io_of_a_text_holds_the_system_prompt_the_prompt_and_the_raw_text(match_record):
    server = _ModelServer(httpx.Response(200, json=_completion('  "Seat 3 lied."\n')))
    request = TextRequest("Write your message.", "Day 2.", 20, 1500, "d2", "You are seat 4.")

    result = server.source().write(request)

    (decision,) = _decisions(match_record, "text_decision")
    assert result.text == "Seat 3 lied."
    assert decision["io"] == {
        "system": "You are seat 4.",
        "user": "Day 2.\n\nWrite your message.\nAt most 20 words.",
        "response_format": None,
        "params": {"model_role": "fast", "temperature": 0.7, "max_tokens": 60, "reasoning": None, "deadline_ms": 1500},
        "raw_output": '  "Seat 3 lied."\n',
        "reasoning_text": None,
    }


def test_record_io_of_a_40000_character_context_is_cut_to_32000_characters_and_the_model_gets_all_of_it(
    match_record,
):
    server = _ModelServer()
    request = _request(context="c" * 40_000)

    server.source().choose(request)

    (decision,) = _decisions(match_record)
    prompt = local_module.choice_prompt(request)
    assert server.sent_body()["messages"][1]["content"] == prompt
    assert len(decision["io"]["user"]) == 32_000
    assert decision["io"]["user"] == prompt[:31_983] + f"…[cut {len(prompt) - 31_983} chars]"


def test_record_io_names_the_reasoning_setting_config_models_toml_gives_the_role(run_folder, match_record):
    (run_folder / "config").mkdir()
    roles = (
        '\n[roles.fast]\nmodel_path = "C:/m.gguf"\nrequired = false\nctx_size = 8\ntemperature = 0\nreasoning = "on"\n'
    )
    (run_folder / "config" / "models.toml").write_bytes(MODELS_TOML + roles.encode("utf-8"))
    server = _ModelServer()

    LocalModelSource("fast", transport=server.transport).choose(_request())
    LocalModelSource("strong", transport=server.transport).choose(_request())

    assert [decision["io"]["params"]["reasoning"] for decision in _decisions(match_record)] == ["on", None]


# -- a short reason with the choice ----------------------------------------------------------------

REASON_SCHEMA = {
    "type": "object",
    "properties": {"choice": {"enum": ["HOLD", "RAID"]}, "reason": {"type": "string", "maxLength": 100}},
    "required": ["choice"],
}


def _reasoned(answer: str) -> _ModelServer:
    return _ModelServer(httpx.Response(200, json=_completion(answer)))


def _asking_reasons(server: _ModelServer) -> LocalModelSource:
    return LocalModelSource("fast", base_url=ENDPOINT_URL, choice_reasons=True, transport=server.transport)


def test_reason_on_the_body_has_the_reason_property_max_tokens_64_and_a_system_prompt_naming_both_keys(run_folder):
    server = _reasoned('{"choice": "HOLD"}')

    _asking_reasons(server).choose(_request())

    body = server.sent_body()
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "choice", "schema": REASON_SCHEMA},
    }
    assert (body["max_tokens"], body["temperature"]) == (64, 0)
    system = body["messages"][0]["content"]
    assert system == local_module.REASON_SYSTEM_PROMPT
    assert '"choice"' in system and '"reason"' in system and "12 words" in system
    assert body["messages"][1]["content"] == local_module.choice_prompt(_request())


def test_reason_given_with_a_valid_choice_is_used_and_recorded_as_model_reason(match_record):
    server = _reasoned('{"choice":"HOLD","reason":"base threatened, army weaker"}')

    result = _asking_reasons(server).choose(_request())

    (decision,) = [line["data"] for line in match_record() if line["kind"] == "decision"]
    assert (result.option_id, result.outcome, result.model_reason) == ("HOLD", "used", "base threatened, army weaker")
    assert (decision["model_reason"], decision["reason"]) == ("base threatened, army weaker", None)


@pytest.mark.parametrize(
    "answer",
    [
        '{"choice":"HOLD"}',
        '{"choice":"HOLD","reason":""}',
        '{"choice":"HOLD","reason":"   "}',
        '{"choice":"HOLD","reason":7}',
    ],
    ids=["no reason", "empty", "blank", "not a string"],
)
def test_reason_missing_or_empty_leaves_the_choice_used_and_model_reason_null(match_record, answer):
    result = _asking_reasons(_reasoned(answer)).choose(_request())

    (decision,) = [line["data"] for line in match_record() if line["kind"] == "decision"]
    assert (result.option_id, result.outcome, result.model_reason) == ("HOLD", "used", None)
    assert "model_reason" in decision and decision["model_reason"] is None


def test_reason_of_300_characters_is_cut_to_100(match_record):
    reason = "".join(chr(ord("a") + index % 26) for index in range(300))

    result = _asking_reasons(_reasoned(json.dumps({"choice": "RAID", "reason": reason}))).choose(_request())

    (decision,) = [line["data"] for line in match_record() if line["kind"] == "decision"]
    assert (result.option_id, result.model_reason) == ("RAID", reason[:100])
    assert decision["model_reason"] == reason[:100] and len(decision["model_reason"]) == 100


def test_reason_of_an_answer_that_is_not_used_is_recorded_and_not_handed_to_the_game(match_record):
    invalid = _asking_reasons(_reasoned('{"choice":"zzz","reason":"looks best"}')).choose(_request())
    late = _asking_reasons(_ModelServer(seconds=SLOW_ANSWER_SECONDS)).choose(_request(deadline_ms=200))

    decisions = [line["data"] for line in match_record() if line["kind"] == "decision"]
    assert (invalid.outcome, invalid.model_reason, late.outcome, late.model_reason) == (
        "invalid",
        None,
        "timeout",
        None,
    )
    assert [(data["model_reason"], data["reason"]) for data in decisions] == [
        ("looks best", "not_an_option"),
        (None, "deadline"),
    ]


def test_reason_off_the_body_and_the_record_are_as_without_the_switch(match_record):
    server = _reasoned('{"choice":"HOLD","reason":"base threatened"}')

    result = server.source().choose(_request())

    (decision,) = [line["data"] for line in match_record() if line["kind"] == "decision"]
    assert server.sent_body() == {
        "model": "fast",
        "messages": [
            {"role": "system", "content": local_module.SYSTEM_PROMPT},
            {"role": "user", "content": local_module.choice_prompt(_request())},
        ],
        "temperature": 0,
        "max_tokens": 24,
        "response_format": {"type": "json_schema", "json_schema": {"name": "choice", "schema": ENUM_SCHEMA}},
    }
    assert (result.option_id, result.model_reason) == ("HOLD", None)
    assert set(decision) == {
        "state_version",
        "source",
        "request_summary",
        "answer",
        "latency_ms",
        "outcome",
        "confidence",
        "reason",
        "io",
    }


def test_reason_is_never_asked_for_a_text(run_folder):
    server = _ModelServer(httpx.Response(200, json=_completion("Seat 3 lied.")))

    _asking_reasons(server).write(TextRequest("Write your message.", "Day 2.", 20, 1500, "d2"))

    assert "response_format" not in server.sent_body()
    assert server.sent_body()["max_tokens"] == 60


# -- a reason before the choice --------------------------------------------------------------------

# A conservative count for a stand-in tokenizer: English text runs at about four characters a token.
STUB_CHARS_PER_TOKEN = 3
THIRTY_WORD_REASON = (
    "Their army is weaker than ours and their harvesters are exposed on the field, so a raid costs little "
    "and slows their next tank wave before it reaches our base"
)
REASON_FIRST_SCHEMA = {
    "type": "object",
    "properties": {"reason": {"type": "string", "maxLength": 240}, "choice": {"enum": ["HOLD", "RAID"]}},
    "required": ["reason", "choice"],
}


def _reason_first_request(max_answer_tokens: int | None = None, reason_words: int = 30) -> ChoiceRequest:
    return ChoiceRequest(
        "Which plan?",
        OPTIONS,
        "Minute 6. We have 8 tanks.",
        1500,
        "1250",
        reason_first=True,
        reason_words=reason_words,
        max_answer_tokens=max_answer_tokens,
    )


class _CountingModelServer(_ModelServer):
    """A model server that writes `content`, counts its tokens at `STUB_CHARS_PER_TOKEN` characters a token and
    reports them, and, like a real server, stops at the request's `max_tokens` with `finish_reason` `length`."""

    def __init__(self, content: str) -> None:
        super().__init__()
        self._content = content
        self.reported: dict[str, Any] = {}

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        needed = -(-len(self._content) // STUB_CHARS_PER_TOKEN)
        fits = needed <= body["max_tokens"]
        written = self._content if fits else self._content[: body["max_tokens"] * STUB_CHARS_PER_TOKEN]
        completion = _completion(written)
        completion["choices"][0]["finish_reason"] = "stop" if fits else "length"
        prompt_chars = sum(len(message["content"]) for message in body["messages"])
        completion["usage"] = {
            "prompt_tokens": -(-prompt_chars // STUB_CHARS_PER_TOKEN),
            "completion_tokens": min(needed, body["max_tokens"]),
        }
        self.reported = completion
        return httpx.Response(200, json=completion)


@pytest.mark.parametrize("choice_reasons", [False, True], ids=["choice_reasons off", "choice_reasons on"])
def test_reason_first_the_schema_lists_reason_then_choice_both_required_whatever_choice_reasons_says(
    run_folder, choice_reasons
):
    server = _reasoned(json.dumps({"reason": "army stronger", "choice": "RAID"}))
    source = LocalModelSource("fast", base_url=ENDPOINT_URL, choice_reasons=choice_reasons, transport=server.transport)

    source.choose(_reason_first_request())

    body = server.sent_body()
    schema = body["response_format"]["json_schema"]["schema"]
    assert schema == REASON_FIRST_SCHEMA and list(schema["properties"]) == ["reason", "choice"]
    assert body["response_format"] == {"type": "json_schema", "json_schema": {"name": "choice", "schema": schema}}
    assert (body["max_tokens"], body["temperature"]) == (2 * 30 + 24, 0)
    system = body["messages"][0]["content"]
    assert system == local_module.reason_first_system_prompt(30)
    assert "reason, in at most 30 words, then your choice" in system
    assert system.index('"reason"') < system.index('"choice"') and "12 words" not in system
    assert body["messages"][1]["content"] == local_module.choice_prompt(_request())


def test_reason_first_max_answer_tokens_sets_the_cap_and_reason_words_the_reason_s_length(run_folder):
    server = _reasoned('{"reason": "army stronger", "choice": "RAID"}')

    server.source().choose(_reason_first_request(max_answer_tokens=160, reason_words=40))

    body = server.sent_body()
    assert body["max_tokens"] == 160
    assert body["response_format"]["json_schema"]["schema"]["properties"]["reason"] == {
        "type": "string",
        "maxLength": 320,
    }
    assert "at most 40 words" in body["messages"][0]["content"]


def test_reason_first_8_a_30_word_reason_then_a_choice_parses_and_is_used_under_a_96_token_cap(match_record):
    answer = json.dumps({"reason": THIRTY_WORD_REASON, "choice": "RAID"})
    server = _CountingModelServer(answer)

    result = server.source().choose(_reason_first_request(max_answer_tokens=96))

    (decision,) = _decisions(match_record)
    usage, (choice,) = server.reported["usage"], server.reported["choices"]
    assert len(THIRTY_WORD_REASON.split()) == 30 and len(THIRTY_WORD_REASON) <= 240
    assert server.sent_body()["max_tokens"] == 96
    assert usage["completion_tokens"] <= 96 and choice["finish_reason"] == "stop"
    assert (result.option_id, result.outcome, result.model_reason) == ("RAID", "used", THIRTY_WORD_REASON)
    assert (decision["answer"], decision["model_reason"], decision["reason"]) == ("RAID", THIRTY_WORD_REASON, None)


def test_reason_first_an_answer_over_its_cap_is_cut_by_the_server_and_is_an_error_not_a_choice(run_folder):
    answer = json.dumps({"reason": THIRTY_WORD_REASON, "choice": "RAID"})

    result = _CountingModelServer(answer).source().choose(_reason_first_request(max_answer_tokens=24))

    assert (result.option_id, result.outcome, result.reason) == (None, "error", "malformed_answer")


@pytest.mark.parametrize(
    "answer, expected",
    [
        ('{"reason": "", "choice": "HOLD"}', None),
        ('{"reason": "   ", "choice": "HOLD"}', None),
        ('{"choice": "HOLD"}', None),
        (json.dumps({"reason": "w" * 300, "choice": "HOLD"}), "w" * 240),
    ],
    ids=["empty", "blank", "missing", "300 characters"],
)
def test_reason_first_a_valid_choice_is_used_with_an_empty_missing_or_over_long_reason(match_record, answer, expected):
    result = _reasoned(answer).source().choose(_reason_first_request())

    (decision,) = _decisions(match_record)
    assert (result.option_id, result.outcome, result.model_reason) == ("HOLD", "used", expected)
    assert "model_reason" in decision and decision["model_reason"] == expected


def test_reason_first_the_record_io_holds_the_schema_and_the_params_as_sent(match_record):
    server = _reasoned('{"reason": "base threatened", "choice": "HOLD"}')
    source = LocalModelSource("fast", base_url=ENDPOINT_URL, reasoning="off", transport=server.transport)

    source.choose(_reason_first_request(max_answer_tokens=96))

    (decision,) = _decisions(match_record)
    body, io = server.sent_body(), decision["io"]
    assert io["response_format"] == body["response_format"]
    assert list(io["response_format"]["json_schema"]["schema"]["properties"]) == ["reason", "choice"]
    assert io["params"] == {
        "model_role": "fast",
        "temperature": 0,
        "max_tokens": 96,
        "reasoning": "off",
        "deadline_ms": 1500,
    }
    assert [io["system"], io["user"]] == [message["content"] for message in body["messages"]]
    assert io["raw_output"] == '{"reason": "base threatened", "choice": "HOLD"}'
    assert decision["model_reason"] == "base threatened"


@pytest.mark.parametrize(
    "choice_reasons, system, max_tokens, schema",
    [
        (False, local_module.SYSTEM_PROMPT, 24, ENUM_SCHEMA),
        (True, local_module.REASON_SYSTEM_PROMPT, 64, REASON_SCHEMA),
    ],
    ids=["choice_reasons off", "choice_reasons on"],
)
def test_reason_first_9_off_the_body_is_today_s_for_both_choice_reasons_values(
    run_folder, choice_reasons, system, max_tokens, schema
):
    server = _reasoned('{"choice": "HOLD"}')
    source = LocalModelSource("fast", base_url=ENDPOINT_URL, choice_reasons=choice_reasons, transport=server.transport)
    request = ChoiceRequest(
        "Which plan?", OPTIONS, "Minute 6. We have 8 tanks.", 1500, "1250", reason_first=False, max_answer_tokens=160
    )

    source.choose(request)

    assert server.sent_body() == {
        "model": "fast",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": local_module.choice_prompt(_request())},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_schema", "json_schema": {"name": "choice", "schema": schema}},
    }


# -- a request's own system text and answer tokens -------------------------------------------------

OWN_SYSTEM_TEXT = 'Choose one OPEN plan. Return only {"choice":"ID","reason":"up to 12 words"}.'


def _own_text_request(*, reason_first: bool = False, answer_tokens: int | None = 48) -> ChoiceRequest:
    return ChoiceRequest(
        "OPEN:",
        OPTIONS,
        "left_s=581\nbase_threat=none",
        1500,
        "1250",
        reason_first=reason_first,
        max_answer_tokens=96,
        system_text=OWN_SYSTEM_TEXT,
        answer_tokens=answer_tokens,
    )


def test_system_text_is_the_system_prompt_and_the_user_message_is_laid_out_plainly_with_a_line_an_option(run_folder):
    server = _reasoned('{"choice": "RAID", "reason": "harvesters exposed, home safe"}')

    result = _asking_reasons(server).choose(_own_text_request())

    system, user = (message["content"] for message in server.sent_body()["messages"])
    assert system == OWN_SYSTEM_TEXT
    assert user == (
        "left_s=581\nbase_threat=none\nOPEN:\nHOLD: Keep the army at the base.\nRAID: Attack the enemy's harvesters."
    )
    assert user == local_module.choice_prompt(_own_text_request())
    assert (result.option_id, result.outcome, result.model_reason) == ("RAID", "used", "harvesters exposed, home safe")


def test_system_text_the_schema_is_the_source_s_own_choice_first_enum_constrained_then_reason(run_folder):
    server = _reasoned('{"choice": "HOLD"}')

    _asking_reasons(server).choose(_own_text_request())

    schema = server.sent_body()["response_format"]["json_schema"]["schema"]
    assert schema == REASON_SCHEMA and list(schema["properties"]) == ["choice", "reason"]
    assert schema["properties"]["choice"] == {"enum": ["HOLD", "RAID"]} and schema["required"] == ["choice"]


@pytest.mark.parametrize("reason_first", [False, True], ids=["reason_first off", "reason_first on"])
def test_answer_tokens_cap_the_answer_whatever_reason_first_and_max_answer_tokens_say(run_folder, reason_first):
    server = _reasoned('{"choice": "HOLD"}')

    _asking_reasons(server).choose(_own_text_request(reason_first=reason_first))

    assert server.sent_body()["max_tokens"] == 48


def test_answer_tokens_unset_leave_the_source_s_cap_with_the_system_text(run_folder):
    server = _reasoned('{"choice": "HOLD"}')

    _asking_reasons(server).choose(_own_text_request(answer_tokens=None))

    body = server.sent_body()
    assert (body["max_tokens"], body["messages"][0]["content"]) == (64, OWN_SYSTEM_TEXT)


def test_system_text_and_answer_tokens_are_left_out_by_default_and_the_body_is_then_today_s(run_folder):
    server = _reasoned('{"choice": "HOLD"}')

    _asking_reasons(server).choose(_request())

    body = server.sent_body()
    assert (_request().system_text, _request().answer_tokens) == (None, None)
    assert (body["messages"][0]["content"], body["max_tokens"]) == (local_module.REASON_SYSTEM_PROMPT, 64)
    assert body["messages"][1]["content"].startswith("Minute 6. We have 8 tanks.\n\nWhich plan?\nOptions:\n- HOLD")


def test_system_text_the_record_io_holds_the_system_text_the_plain_message_and_the_cap_as_sent(match_record):
    server = _reasoned('{"choice": "RAID", "reason": "harvesters exposed"}')
    source = LocalModelSource(
        "fast", base_url=ENDPOINT_URL, reasoning="off", choice_reasons=True, transport=server.transport
    )

    source.choose(_own_text_request())

    (decision,) = _decisions(match_record)
    io = decision["io"]
    assert [io["system"], io["user"]] == [message["content"] for message in server.sent_body()["messages"]]
    assert (io["system"], io["params"]["max_tokens"]) == (OWN_SYSTEM_TEXT, 48)
    assert io["raw_output"] == '{"choice": "RAID", "reason": "harvesters exposed"}'
    assert decision["model_reason"] == "harvesters exposed"


# -- a request's own with_reason -------------------------------------------------------------------

CHOICE_ONLY_SCHEMA = {"type": "object", "properties": {"choice": {"enum": ["HOLD", "RAID"]}}, "required": ["choice"]}


def test_with_reason_false_asks_the_choice_alone_in_16_tokens_from_a_source_with_choice_reasons(match_record):
    server = _reasoned('{"choice": "RAID", "reason": "not asked for"}')
    request = dataclasses.replace(_own_text_request(answer_tokens=16), with_reason=False)

    result = _asking_reasons(server).choose(request)

    body = server.sent_body()
    assert body["response_format"]["json_schema"]["schema"] == CHOICE_ONLY_SCHEMA
    assert (body["max_tokens"], body["messages"][0]["content"]) == (16, OWN_SYSTEM_TEXT)
    assert (result.option_id, result.outcome, result.model_reason) == ("RAID", "used", None)
    (decision,) = _decisions(match_record)
    assert "model_reason" not in decision and decision["io"]["params"]["max_tokens"] == 16


def test_with_reason_true_asks_a_reason_of_a_source_without_choice_reasons(run_folder):
    server = _reasoned('{"choice": "HOLD", "reason": "home is safe"}')
    source = LocalModelSource("fast", base_url=ENDPOINT_URL, choice_reasons=False, transport=server.transport)

    result = source.choose(dataclasses.replace(_request(), with_reason=True))

    body = server.sent_body()
    assert body["response_format"]["json_schema"]["schema"] == REASON_SCHEMA
    assert (body["messages"][0]["content"], body["max_tokens"]) == (local_module.REASON_SYSTEM_PROMPT, 64)
    assert result.model_reason == "home is safe"


# -- where the endpoint is -------------------------------------------------------------------------


def test_without_a_base_url_the_endpoint_is_the_one_of_the_models_configuration(run_folder):
    (run_folder / "config").mkdir()
    (run_folder / "config" / "models.toml").write_bytes(MODELS_TOML)
    server = _ModelServer()

    LocalModelSource("fast", transport=server.transport).choose(_request())

    assert str(server.requests[0].url) == "http://127.0.0.1:11777/v1/chat/completions"


def test_without_a_base_url_and_without_a_models_configuration_the_source_cannot_be_built(run_folder):
    with pytest.raises(ConfigError, match="models.toml"):
        LocalModelSource("fast")


# -- through the real endpoint ---------------------------------------------------------------------


def _stub_role(tmp_path: Path, llama_stub: Path, port: int) -> Role:
    model_file = tmp_path / "fast.gguf"
    model_file.write_bytes(b"GGUF")
    settings = RoleSettings(
        name="fast",
        model_path=model_file,
        required=False,
        ctx_size=8192,
        n_gpu_layers=999,
        parallel=1,
        temperature=0,
        top_k=None,
        top_p=None,
        seed=None,
        reasoning="off",
        extra_args=("--stub-content", '{"choice": "HOLD"}'),
    )
    return Role(settings, llama_stub, port, tmp_path / "logs" / "fast.log")


def test_a_choice_travels_through_the_endpoint_to_the_role_s_server_and_back(
    run_folder, tmp_path, llama_stub, free_port, local_http
):
    role = _stub_role(tmp_path, llama_stub, free_port())
    role.start()
    endpoint = Endpoint("127.0.0.1", free_port(), [role])
    endpoint.serve()
    try:
        source = LocalModelSource("fast", base_url=f"http://127.0.0.1:{endpoint.address[1]}")
        result = source.choose(_request(deadline_ms=5000))
        missing = LocalModelSource("nope", base_url=f"http://127.0.0.1:{endpoint.address[1]}").choose(_request())
        received = local_http.get(f"http://127.0.0.1:{role.port}/stub/requests").json()["requests"][-1]
    finally:
        endpoint.close()
        role.stop()

    assert (result.option_id, result.source, result.outcome) == ("HOLD", "local:fast", "used")
    assert (missing.outcome, missing.reason) == ("error", "http_404")
    assert received["body"]["response_format"]["json_schema"]["schema"] == ENUM_SCHEMA
    assert (received["body"]["max_tokens"], received["body"]["temperature"]) == (24, 0)
    assert received["headers"]["x-client"] == "ucla-agent"
