"""The request and result types of the decision sources."""

from __future__ import annotations

import dataclasses

import pytest

from ucla_agent.core import decide
from ucla_agent.core.decide import ChoiceRequest, ChoiceResult, Option

OPTIONS = [Option("HOLD", "Keep the army at the base."), Option("RAID", "Send the army to the enemy's harvesters.")]


def _request() -> ChoiceRequest:
    return ChoiceRequest(
        question="Which plan?", options=OPTIONS, context="Minute 6, 8 tanks.", deadline_ms=1500, state_tag="1250"
    )


def test_a_request_holds_the_question_the_options_the_context_the_deadline_and_the_state_tag():
    request = _request()

    assert (request.question, request.context, request.deadline_ms, request.state_tag) == (
        "Which plan?",
        "Minute 6, 8 tanks.",
        1500,
        "1250",
    )
    assert [(option.id, option.description) for option in request.options] == [
        ("HOLD", "Keep the army at the base."),
        ("RAID", "Send the army to the enemy's harvesters."),
    ]


def test_a_request_has_no_system_text_and_no_answer_tokens_unless_given():
    given = dataclasses.replace(_request(), system_text="Choose one OPEN plan.", answer_tokens=48)

    assert (_request().system_text, _request().answer_tokens) == (None, None)
    assert (given.system_text, given.answer_tokens) == ("Choose one OPEN plan.", 48)


def test_option_ids_come_in_the_order_the_options_were_given():
    assert _request().option_ids() == ["HOLD", "RAID"]


def test_with_reason_a_request_leaves_the_reason_to_the_source_unless_it_says_false_or_true():
    choice_alone = dataclasses.replace(_request(), with_reason=False)
    with_a_reason = dataclasses.replace(_request(), with_reason=True)

    assert _request().with_reason is None
    assert [_request().asks_reason_after(source_default) for source_default in (False, True)] == [False, True]
    assert [choice_alone.asks_reason_after(source_default) for source_default in (False, True)] == [False, False]
    assert [with_a_reason.asks_reason_after(source_default) for source_default in (False, True)] == [True, True]


def test_a_result_holds_option_confidence_source_latency_and_outcome_and_no_reason_unless_given():
    result = ChoiceResult("RAID", 0.8, "local:fast", 420, "used")

    assert dataclasses.astuple(result) == ("RAID", 0.8, "local:fast", 420, "used", None, None)
    assert ChoiceResult(None, None, "jev", 0, "error", "circuit_open").reason == "circuit_open"
    assert ChoiceResult("HOLD", None, "local:fast", 300, "used", model_reason="army weaker").model_reason == (
        "army weaker"
    )


def test_the_five_outcomes_have_the_names_the_match_record_uses():
    assert decide.OUTCOMES == ("used", "timeout", "error", "invalid", "stale_discarded")
    assert (decide.USED, decide.TIMEOUT, decide.ERROR, decide.INVALID, decide.STALE_DISCARDED) == decide.OUTCOMES


@pytest.mark.parametrize(
    "value, field",
    [(OPTIONS[0], "id"), (_request(), "deadline_ms"), (ChoiceResult(None, None, "jev", 80, "error"), "option_id")],
    ids=["option", "request", "result"],
)
def test_requests_and_results_cannot_be_changed_once_made(value, field):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(value, field, "changed")


def test_two_results_with_the_same_content_are_equal():
    assert ChoiceResult("HOLD", None, "local:fast", 90, "used") == ChoiceResult("HOLD", None, "local:fast", 90, "used")
