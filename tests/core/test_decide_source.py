"""`DecisionSource.choose`: the result the caller gets and the decision and fallback events in the match record."""

from __future__ import annotations

import pytest

from ucla_agent.core.decide import ChoiceRequest, ChoiceResult, DecisionSource, Option
from ucla_agent.core.decide.journal import SourceCall, request_summary
from ucla_agent.core.secrets import REDACTED

REQUEST = ChoiceRequest(
    question="Which plan?",
    options=[Option("HOLD", "Keep the army at the base."), Option("RAID", "Attack the harvesters.")],
    context="Minute 6, 8 tanks.",
    deadline_ms=1500,
    state_tag="1250",
)
SUMMARY = "Which plan? [HOLD, RAID]"


class _Scripted(DecisionSource):
    """A source whose calls are decided by the test."""

    def __init__(self, calls: list[SourceCall], name: str = "local:fast") -> None:
        self.name = name
        self._calls = calls

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        return self._calls


def _decision_and_fallback_events(match_record) -> list[tuple[str, dict]]:
    return [(line["kind"], line["data"]) for line in match_record() if line["kind"] in ("decision", "fallback")]


def _decision(source: str, answer: str | None, latency_ms: int, outcome: str, **more) -> tuple[str, dict]:
    data = {
        "state_version": "1250",
        "source": source,
        "request_summary": SUMMARY,
        "answer": answer,
        "latency_ms": latency_ms,
        "outcome": outcome,
        "confidence": None,
        "reason": None,
    }
    return "decision", data | more


def _fallback(intended_source: str, reason: str, used: str = "script") -> tuple[str, dict]:
    return "fallback", {"intended_source": intended_source, "reason": reason, "used": used}


# -- one call --------------------------------------------------------------------------------------


def test_a_used_answer_is_returned_and_recorded_as_one_decision_event(match_record):
    source = _Scripted([SourceCall("local:fast", "RAID", None, 420, "used")])

    result = source.choose(REQUEST)

    assert result == ChoiceResult("RAID", None, "local:fast", 420, "used")
    assert _decision_and_fallback_events(match_record) == [_decision("local:fast", "RAID", 420, "used")]


@pytest.mark.parametrize(
    "outcome, reason, fallback_reason",
    [
        ("timeout", "deadline", "timeout"),
        ("error", "http_503", "error"),
        ("error", "circuit_open", "circuit_open"),
        ("invalid", "low_confidence", "invalid"),
    ],
)
def test_a_call_without_a_usable_answer_gives_no_option_and_a_fallback_to_the_script(
    match_record, outcome, reason, fallback_reason
):
    source = _Scripted([SourceCall("jev", None, None, 80, outcome, reason)], name="jev")

    result = source.choose(REQUEST)

    assert result == ChoiceResult(None, None, "jev", 80, outcome, reason)
    assert _decision_and_fallback_events(match_record) == [
        _decision("jev", None, 80, outcome, reason=reason),
        _fallback("jev", fallback_reason),
    ]


def test_an_answer_that_is_not_an_option_is_kept_in_the_record_and_kept_from_the_caller(match_record):
    source = _Scripted([SourceCall("local:fast", "zzz", None, 300, "invalid", "not_an_option")])

    result = source.choose(REQUEST)

    assert result.option_id is None and result.outcome == "invalid"
    assert _decision_and_fallback_events(match_record) == [
        _decision("local:fast", "zzz", 300, "invalid", reason="not_an_option"),
        _fallback("local:fast", "invalid"),
    ]


def test_an_answer_discarded_as_stale_is_kept_in_the_record_and_kept_from_the_caller(match_record):
    source = _Scripted([SourceCall("local:fast", "RAID", None, 900, "stale_discarded")])

    result = source.choose(REQUEST)

    assert result == ChoiceResult(None, None, "local:fast", 900, "stale_discarded")
    assert _decision_and_fallback_events(match_record) == [
        _decision("local:fast", "RAID", 900, "stale_discarded"),
        _fallback("local:fast", "stale_discarded"),
    ]


def test_the_confidence_of_an_answer_is_returned_and_recorded(match_record):
    source = _Scripted([SourceCall("jev", "HOLD", 0.91, 120, "used")], name="jev")

    result = source.choose(REQUEST)

    assert result.confidence == 0.91
    assert _decision_and_fallback_events(match_record) == [_decision("jev", "HOLD", 120, "used", confidence=0.91)]


# -- several calls for one request -----------------------------------------------------------------


def test_a_failed_call_followed_by_a_used_one_names_the_source_that_decided_instead(match_record):
    source = _Scripted(
        [SourceCall("jev", None, None, 80, "error", "http_503"), SourceCall("local:fast", "HOLD", None, 400, "used")],
        name="plan",
    )

    result = source.choose(REQUEST)

    assert result == ChoiceResult("HOLD", None, "local:fast", 400, "used")
    assert _decision_and_fallback_events(match_record) == [
        _decision("jev", None, 80, "error", reason="http_503"),
        _fallback("jev", "error", used="local:fast"),
        _decision("local:fast", "HOLD", 400, "used"),
    ]


def test_when_every_call_fails_each_falls_back_to_the_script_and_the_last_one_is_the_result(match_record):
    source = _Scripted(
        [
            SourceCall("jev", None, None, 1000, "timeout", "deadline"),
            SourceCall("local:fast", None, None, 5, "error", "circuit_open"),
        ],
        name="plan",
    )

    result = source.choose(REQUEST)

    assert result == ChoiceResult(None, None, "local:fast", 5, "error", "circuit_open")
    assert _decision_and_fallback_events(match_record) == [
        _decision("jev", None, 1000, "timeout", reason="deadline"),
        _fallback("jev", "timeout"),
        _decision("local:fast", None, 5, "error", reason="circuit_open"),
        _fallback("local:fast", "circuit_open"),
    ]


def test_a_request_no_source_was_called_for_is_an_error_and_one_fallback(match_record):
    source = _Scripted([], name="red_alert_plan")

    result = source.choose(REQUEST)

    assert result == ChoiceResult(None, None, "red_alert_plan", 0, "error", "no_source")
    assert _decision_and_fallback_events(match_record) == [_fallback("red_alert_plan", "no_source")]


# -- the record itself -----------------------------------------------------------------------------


def test_the_request_summary_is_the_question_and_the_option_ids():
    assert request_summary(REQUEST) == SUMMARY


def test_a_key_in_the_question_is_redacted_in_the_record(match_record, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "jev-4f9c2d7e1a6b8053")
    request = ChoiceRequest("Plan for jev-4f9c2d7e1a6b8053?", REQUEST.options, "", 1500, "1250")

    _Scripted([SourceCall("local:fast", "HOLD", None, 90, "used")]).choose(request)

    ((_kind, data),) = _decision_and_fallback_events(match_record)
    assert data["request_summary"] == f"Plan for {REDACTED}? [HOLD, RAID]"


def test_without_an_open_match_record_the_result_is_returned_all_the_same(run_folder):
    source = _Scripted([SourceCall("local:fast", None, None, 1500, "timeout", "deadline")])

    assert source.choose(REQUEST) == ChoiceResult(None, None, "local:fast", 1500, "timeout", "deadline")
    assert not (run_folder / "runs").exists()


def test_a_source_must_say_how_it_asks():
    with pytest.raises(TypeError):
        DecisionSource()
