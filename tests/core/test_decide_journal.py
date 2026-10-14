"""The `io` of a model decision: what a guarded call sent and got back, carried to its `decision` or `text_decision`
event, each text capped at 32,000 characters, and no io where nothing was sent to a model."""

from __future__ import annotations

import json
import time

import pytest

from ucla_agent.core.decide import ChoiceRequest, DecisionSource, Option, TextRequest
from ucla_agent.core.decide.guard import Answer, SourceError, SourceGuard, holding
from ucla_agent.core.decide.journal import SourceCall, TextCall
from ucla_agent.core.decide.model_io import CUT_MARKER, MAX_TEXT_CHARS, ModelIO, Received, Sent, capped
from ucla_agent.core.secrets import REDACTED
from ucla_agent.games.redalert.groups import Groups
from ucla_agent.games.redalert.items import ItemTable
from ucla_agent.games.redalert.shorthand import Commands, translate
from ucla_agent.games.redalert.world import World

OPTIONS = [Option("HOLD", "Keep the army at the base."), Option("RAID", "Attack the harvesters.")]
SENT = Sent(
    system="You choose exactly one of the options you are given.",
    user="Minute 6.\n\nWhich plan?\nOptions:\n- HOLD: Keep the army at the base.\n- RAID: Attack the harvesters.",
    response_format={"type": "json_schema"},
    params={"role": "fast", "temperature": 0, "max_tokens": 24, "reasoning": "off", "deadline_ms": 1500},
)
RAW = '{"choice": "RAID"}'
SLOW_ANSWER_SECONDS = 0.4
SHORT_DEADLINE_MS = 200
SECRET_VALUE = "jev-4f9c2d7e1a6b8053"


def _request(deadline_ms: int = 1500) -> ChoiceRequest:
    return ChoiceRequest("Which plan?", OPTIONS, "Minute 6.", deadline_ms, "1250")


def _text_request() -> TextRequest:
    return TextRequest("Write your message.", "Day 2.", 20, 1500, "d2")


class _Guarded(DecisionSource):
    """A source that makes one guarded call with the exchange and the `Sent` the test gives."""

    name = "local:fast"

    def __init__(self, exchange, sent: Sent | None = SENT) -> None:
        self._guard = SourceGuard(self.name)
        self._exchange = exchange
        self._sent = sent

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        return [self._guard.call(request, self._exchange, self._sent)]

    def ask_text(self, request: TextRequest) -> list[TextCall]:
        return [self._guard.call_text(request, self._exchange, self._sent)]


def _replying(raw: str, read=lambda raw: Answer(json.loads(raw)["choice"])):
    """An exchange whose model replies `raw`, read by `read` through `holding`."""
    return lambda http_timeout: holding(Received(raw), lambda: read(raw))


def _of_kind(lines: list[dict], kind: str) -> list[dict]:
    return [line["data"] for line in lines if line["kind"] == kind]


# -- the cap ---------------------------------------------------------------------------------------


def test_record_a_40000_character_text_is_cut_to_32000_characters_ending_with_the_marker():
    text = "".join(chr(ord("a") + index % 26) for index in range(40_000))

    cut = capped(text)

    marker = CUT_MARKER.format(count=8017)
    assert len(cut) == MAX_TEXT_CHARS == 32_000
    assert marker == "…[cut 8017 chars]" and cut.endswith(marker)
    assert cut[: 32_000 - len(marker)] == text[:31_983]


@pytest.mark.parametrize("length", [32_001, 32_017, 52_000, 132_000])
def test_record_a_cut_text_is_always_32000_characters_and_names_the_characters_it_lost(length):
    text = "x" * length

    cut = capped(text)

    kept = cut.index("…")
    assert len(cut) == MAX_TEXT_CHARS
    assert cut[kept:] == f"…[cut {length - kept} chars]"


@pytest.mark.parametrize("value", [None, "", "x" * MAX_TEXT_CHARS, {"type": "json_schema"}, ["HOLD"]])
def test_record_a_text_within_the_cap_and_anything_but_a_text_is_kept_as_it_is(value):
    assert capped(value) == value


def test_record_a_secret_the_cut_falls_inside_is_redacted_before_the_cut(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", SECRET_VALUE)
    # Cut as it stands, the text would keep its first 31,983 characters: ten of the secret's twenty.
    text = "x" * 31_973 + SECRET_VALUE + "y" * 5_000

    cut = capped(text)

    assert SECRET_VALUE[:8] not in cut
    assert cut[31_973:31_983] == REDACTED and len(cut) == MAX_TEXT_CHARS


def test_record_a_text_over_the_cap_that_redaction_brings_within_it_is_kept_whole_and_redacted(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", SECRET_VALUE)
    text = "x" * 31_985 + SECRET_VALUE

    assert capped(text) == "x" * 31_985 + REDACTED


def test_record_the_io_object_holds_the_messages_the_format_the_params_and_the_reply():
    io = ModelIO(SENT, Received("raw text", "thought about it"))

    assert io.record() == {
        "system": SENT.system,
        "user": SENT.user,
        "response_format": {"type": "json_schema"},
        "params": {"role": "fast", "temperature": 0, "max_tokens": 24, "reasoning": "off", "deadline_ms": 1500},
        "raw_output": "raw text",
        "reasoning_text": "thought about it",
    }


# -- the guard carries what came back -------------------------------------------------------------


def test_record_a_used_choice_records_what_was_sent_and_the_raw_reply(match_record):
    result = _Guarded(_replying(RAW)).choose(_request())

    (decision,) = _of_kind(match_record(), "decision")
    assert result.option_id == "RAID"
    assert decision["io"] == ModelIO(SENT, Received(RAW)).record()
    assert decision["io"]["raw_output"] == RAW


def test_record_an_answer_that_is_not_an_option_keeps_its_raw_output(match_record):
    _Guarded(_replying('{"choice": "zzz"}')).choose(_request())

    decision, fallback = [line["data"] for line in match_record() if line["kind"] in ("decision", "fallback")]
    assert (decision["outcome"], decision["io"]["raw_output"]) == ("invalid", '{"choice": "zzz"}')
    assert "io" not in fallback


def test_record_an_unreadable_reply_keeps_its_raw_output(match_record):
    def unreadable(raw: str) -> Answer:
        raise SourceError("malformed_answer")

    _Guarded(_replying('{"choice": "RA', unreadable)).choose(_request())

    (decision,) = _of_kind(match_record(), "decision")
    assert (decision["reason"], decision["io"]["raw_output"]) == ("malformed_answer", '{"choice": "RA')


def test_record_a_timeout_records_what_was_sent_and_raw_output_null(match_record):
    def slow(http_timeout: float) -> Answer:
        time.sleep(SLOW_ANSWER_SECONDS)
        return holding(Received(RAW), lambda: Answer("RAID"))

    result = _Guarded(slow).choose(_request(SHORT_DEADLINE_MS))

    (decision,) = _of_kind(match_record(), "decision")
    assert result.outcome == "timeout"
    assert (decision["io"]["user"], decision["io"]["raw_output"], decision["io"]["reasoning_text"]) == (
        SENT.user,
        None,
        None,
    )


def test_record_a_text_decision_records_the_system_prompt_and_the_raw_text(match_record):
    text_sent = Sent("You write one short message.", "Day 2.\n\nWrite your message.\nAt most 20 words.", None, {})

    result = _Guarded(_replying('  "Seat 3 lies."  ', lambda raw: Answer("Seat 3 lies.")), text_sent).write(
        _text_request()
    )

    (decision,) = _of_kind(match_record(), "text_decision")
    assert result.text == "Seat 3 lies."
    assert (decision["io"]["system"], decision["io"]["raw_output"]) == (
        "You write one short message.",
        '  "Seat 3 lies."  ',
    )


def test_record_a_40000_character_context_is_cut_in_the_record(match_record):
    long_sent = Sent(SENT.system, "c" * 40_000, None, {})

    _Guarded(_replying(RAW), long_sent).choose(_request())

    (decision,) = _of_kind(match_record(), "decision")
    assert len(decision["io"]["user"]) == MAX_TEXT_CHARS
    assert decision["io"]["user"].endswith("…[cut 8017 chars]")


# -- no io where nothing was sent to a model -------------------------------------------------------


def test_record_a_call_that_sent_nothing_has_no_io(match_record):
    _Guarded(_replying(RAW), sent=None).choose(_request())

    (decision,) = _of_kind(match_record(), "decision")
    assert "io" not in decision


def test_record_a_call_skipped_while_the_circuit_is_open_has_no_io(match_record):
    def failing(http_timeout: float) -> Answer:
        raise SourceError("http_503")

    source = _Guarded(failing)
    for _ in range(4):
        source.choose(_request())

    decisions = _of_kind(match_record(), "decision")
    assert [decision["reason"] for decision in decisions] == ["http_503"] * 3 + ["circuit_open"]
    assert ["io" in decision for decision in decisions] == [True, True, True, False]


def test_record_fallbacks_and_shorthand_decisions_have_no_io(match_record):
    commands = Commands(staging=None, request_plan=lambda plan: False)
    translate("mv main 40,41", World(ItemTable([])), Groups(), commands, from_model=True)
    _Guarded(_replying('{"choice": "zzz"}')).choose(_request())

    lines = match_record()
    shorthand = [data for data in _of_kind(lines, "decision") if data["source"] == "shorthand"]
    assert len(shorthand) == 1 and "io" not in shorthand[0]
    assert _of_kind(lines, "fallback") and all("io" not in data for data in _of_kind(lines, "fallback"))
