"""`ChainSource`: the order of its sources, the one deadline they share, and what the record says."""

from __future__ import annotations

import time

import httpx

from ucla_agent.core.decide import (
    ChainSource,
    ChoiceRequest,
    ChoiceResult,
    DecisionSource,
    LocalModelSource,
    Option,
    TextRequest,
    TextResult,
)
from ucla_agent.core.decide.journal import SourceCall, TextCall

OPTIONS = [Option("HOLD", "Keep the army at the base."), Option("RAID", "Attack the enemy's harvesters.")]
ENDPOINT_URL = "http://127.0.0.1:11500"
CHAIN_DEADLINE_MS = 1000
SLOW_FAILURE_SECONDS = 0.3


def _request(deadline_ms: int = CHAIN_DEADLINE_MS) -> ChoiceRequest:
    return ChoiceRequest("Which plan?", OPTIONS, "Minute 6.", deadline_ms, "1250")


class _Source(DecisionSource):
    """A source that remembers the requests it gets, takes its time and ends each one as the test says."""

    def __init__(self, name: str, outcome: str, answer: str | None = None, seconds: float = 0.0) -> None:
        self.name = name
        self.requests: list[ChoiceRequest] = []
        self._outcome = outcome
        self._answer = answer
        self._seconds = seconds

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        self.requests.append(request)
        time.sleep(self._seconds)
        latency_ms = round(self._seconds * 1000)
        return [SourceCall(self.name, self._answer, None, latency_ms, self._outcome)]

    def ask_text(self, request: TextRequest) -> list[TextCall]:
        self.requests.append(request)
        time.sleep(self._seconds)
        latency_ms = round(self._seconds * 1000)
        return [TextCall(self.name, self._answer, latency_ms, self._outcome)]


def _text_request(deadline_ms: int = CHAIN_DEADLINE_MS) -> TextRequest:
    return TextRequest("Write your message.", "Day 2.", 40, deadline_ms, "day-2")


def _events(match_record) -> list[tuple]:
    kinds = ("decision", "fallback")
    return [
        (line["kind"], line["data"].get("source") or line["data"]["intended_source"], line["data"].get("used"))
        for line in match_record()
        if line["kind"] in kinds
    ]


# -- order -----------------------------------------------------------------------------------------


def test_the_first_used_answer_is_the_result_and_later_sources_are_not_asked(run_folder):
    first, second = _Source("jev", "used", "RAID"), _Source("local:fast", "used", "HOLD")

    result = ChainSource([first, second]).choose(_request())

    assert result == ChoiceResult("RAID", None, "jev", 0, "used")
    assert (len(first.requests), len(second.requests)) == (1, 0)


def test_a_failing_source_is_followed_by_the_next_one_whose_answer_is_the_result(match_record):
    failing, working = _Source("jev", "error"), _Source("local:fast", "used", "HOLD")

    result = ChainSource([failing, working]).choose(_request())

    assert result == ChoiceResult("HOLD", None, "local:fast", 0, "used")
    assert _events(match_record) == [
        ("decision", "jev", None),
        ("fallback", "jev", "local:fast"),
        ("decision", "local:fast", None),
    ]


def test_when_every_source_fails_the_last_one_is_the_result_and_the_script_decides(match_record):
    chain = ChainSource([_Source("jev", "error"), _Source("local:fast", "invalid", "zzz")])

    result = chain.choose(_request())

    assert (result.option_id, result.source, result.outcome) == (None, "local:fast", "invalid")
    assert _events(match_record) == [
        ("decision", "jev", None),
        ("fallback", "jev", "script"),
        ("decision", "local:fast", None),
        ("fallback", "local:fast", "script"),
    ]


def test_every_source_gets_the_same_question_options_context_and_state_tag(run_folder):
    failing, working = _Source("jev", "timeout"), _Source("local:fast", "used", "HOLD")

    ChainSource([failing, working]).choose(_request())

    for source in (failing, working):
        (asked,) = source.requests
        assert (asked.question, asked.options, asked.context, asked.state_tag) == (
            "Which plan?",
            OPTIONS,
            "Minute 6.",
            "1250",
        )


# -- one deadline for the whole chain --------------------------------------------------------------


def test_each_source_is_given_the_time_that_is_left(run_folder):
    slow_failure = _Source("jev", "error", seconds=SLOW_FAILURE_SECONDS)
    working = _Source("local:fast", "used", "HOLD")

    ChainSource([slow_failure, working]).choose(_request(CHAIN_DEADLINE_MS))

    assert slow_failure.requests[0].deadline_ms in range(CHAIN_DEADLINE_MS - 20, CHAIN_DEADLINE_MS + 1)
    assert working.requests[0].deadline_ms in range(600, 701)


def test_a_source_for_which_no_time_is_left_is_not_asked(run_folder):
    used_up = _Source("jev", "timeout", seconds=0.25)
    never_asked = _Source("local:fast", "used", "HOLD")

    result = ChainSource([used_up, never_asked]).choose(_request(deadline_ms=200))

    assert (result.source, result.outcome, result.option_id) == ("jev", "timeout", None)
    assert never_asked.requests == []


def test_the_first_source_is_asked_even_when_the_deadline_is_zero(run_folder):
    first = _Source("local:fast", "timeout")

    result = ChainSource([first]).choose(_request(deadline_ms=0))

    assert (len(first.requests), first.requests[0].deadline_ms, result.outcome) == (1, 0, "timeout")


def test_a_chain_of_a_failing_and_a_working_model_source_answers_within_the_deadline(run_folder):
    refusing = httpx.MockTransport(lambda request: httpx.Response(503, json={"error": "role_down"}))
    answering = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": '{"choice": "HOLD"}'}}]})
    )
    chain = ChainSource(
        [
            LocalModelSource("strong", base_url=ENDPOINT_URL, transport=refusing),
            LocalModelSource("fast", base_url=ENDPOINT_URL, transport=answering),
        ]
    )
    started = time.perf_counter()

    result = chain.choose(_request(deadline_ms=500))
    seconds = time.perf_counter() - started

    assert (result.option_id, result.source, result.outcome) == ("HOLD", "local:fast", "used")
    assert seconds < 0.5


# -- chains of other shapes ------------------------------------------------------------------------


def test_a_chain_without_a_source_is_an_error_named_after_the_chain(match_record):
    result = ChainSource([], name="red_alert_plan").choose(_request())

    assert result == ChoiceResult(None, None, "red_alert_plan", 0, "error", "no_source")
    assert _events(match_record) == [("fallback", "red_alert_plan", "script")]


def test_a_chain_is_called_chain_unless_it_is_given_a_name():
    assert ChainSource([]).name == "chain"


def test_a_chain_can_hold_a_chain(run_folder):
    inner = ChainSource([_Source("jev", "error"), _Source("local:strong", "timeout")])
    working = _Source("local:fast", "used", "RAID")

    calls = ChainSource([inner, working]).ask(_request())

    assert [(call.source, call.outcome) for call in calls] == [
        ("jev", "error"),
        ("local:strong", "timeout"),
        ("local:fast", "used"),
    ]


def test_an_empty_chain_inside_a_chain_is_passed_over(run_folder):
    working = _Source("local:fast", "used", "RAID")

    result = ChainSource([ChainSource([]), working]).choose(_request())

    assert (result.option_id, result.source) == ("RAID", "local:fast")


# -- one option ------------------------------------------------------------------------------------


def _one_option_request() -> ChoiceRequest:
    return ChoiceRequest("Which plan?", OPTIONS[:1], "Minute 6.", CHAIN_DEADLINE_MS, "1250")


def test_a_one_option_choice_is_answered_by_the_chain_with_that_option_and_no_source_is_asked(match_record):
    sources = [_Source("jev", "error"), _Source("local:fast", "used", "RAID")]

    result = ChainSource(sources, name="red_alert_commander").choose(_one_option_request())

    assert result == ChoiceResult("HOLD", 1.0, "red_alert_commander", 0, "used", "single_option")
    assert [source.requests for source in sources] == [[], []]
    (decision,) = [line["data"] for line in match_record() if line["kind"] in ("decision", "fallback")]
    assert (decision["source"], decision["answer"], decision["outcome"]) == ("red_alert_commander", "HOLD", "used")
    assert (decision["confidence"], decision["reason"], decision["request_summary"]) == (
        1.0,
        "single_option",
        "Which plan? [HOLD]",
    )


def test_a_one_option_choice_is_answered_even_by_a_chain_without_a_source(run_folder):
    calls = ChainSource([], name="red_alert_plan").ask(_one_option_request())

    assert calls == [SourceCall("red_alert_plan", "HOLD", 1.0, 0, "used", "single_option")]


def test_a_two_option_choice_is_still_asked_of_the_sources(run_folder):
    source = _Source("local:fast", "used", "RAID")

    result = ChainSource([source], name="red_alert_commander").choose(_request())

    assert result == ChoiceResult("RAID", None, "local:fast", 0, "used")
    assert len(source.requests) == 1


# -- texts -----------------------------------------------------------------------------------------


def test_a_text_is_asked_of_the_sources_in_order_until_one_is_used(run_folder):
    failing = _Source("local:strong", "invalid", "")
    working = _Source("local:fast", "used", "I vote seat 5.")
    never_asked = _Source("local:small", "used", "I vote seat 3.")

    result = ChainSource([failing, working, never_asked]).write(_text_request())

    assert result == TextResult("I vote seat 5.", "local:fast", 0, "used")
    assert [len(source.requests) for source in (failing, working, never_asked)] == [1, 1, 0]


def test_a_text_source_is_given_the_time_that_is_left_and_none_once_it_is_used_up(run_folder):
    slow_failure = _Source("local:strong", "error", seconds=SLOW_FAILURE_SECONDS)
    working = _Source("local:fast", "used", "I vote seat 5.")
    used_up = _Source("local:strong", "timeout", seconds=0.25)
    never_asked = _Source("local:fast", "used", "I vote seat 5.")

    ChainSource([slow_failure, working]).write(_text_request(CHAIN_DEADLINE_MS))
    result = ChainSource([used_up, never_asked]).write(_text_request(deadline_ms=200))

    assert working.requests[0].deadline_ms in range(600, 701)
    assert (working.requests[0].instruction, working.requests[0].context) == ("Write your message.", "Day 2.")
    assert (result.source, result.outcome, result.text) == ("local:strong", "timeout", None)
    assert never_asked.requests == []
