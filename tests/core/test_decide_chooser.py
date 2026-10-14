"""`AsyncChooser`: asking without waiting, one request at a time, and discarding a stale answer."""

from __future__ import annotations

import threading
import time

import pytest

from ucla_agent.core.decide import AsyncChooser, ChainSource, ChoiceRequest, ChoiceResult, DecisionSource, Option
from ucla_agent.core.decide import chooser as chooser_module
from ucla_agent.core.decide.journal import SourceCall

OPTIONS = [Option("HOLD", "Keep the army at the base."), Option("RAID", "Attack the enemy's harvesters.")]
ANSWER_SECONDS = 0.2
# Far less than the source takes: a call that waited for the source would not fit in it.
NO_WAIT_SECONDS = 0.05
RESULT_LIMIT_SECONDS = 5.0


def _request(state_tag: str = "1250") -> ChoiceRequest:
    return ChoiceRequest("Which plan?", OPTIONS, "Minute 6.", 1500, state_tag)


def _never_stale(asked_tag: str, current_tag: str) -> bool:
    return False


def _stale_when_the_tag_changed(asked_tag: str, current_tag: str) -> bool:
    return asked_tag != current_tag


class _Source(DecisionSource):
    """A source that takes a while, remembers who asked it and from which thread, and ends as the test says."""

    name = "local:fast"

    def __init__(self, calls: list[SourceCall] | None = None, seconds: float = 0.0) -> None:
        self.requests: list[ChoiceRequest] = []
        self.threads: list[str] = []
        self.finished = threading.Event()
        self._calls = calls if calls is not None else [SourceCall("local:fast", "RAID", None, 200, "used")]
        self._seconds = seconds

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        self.requests.append(request)
        self.threads.append(threading.current_thread().name)
        time.sleep(self._seconds)
        self.finished.set()
        return self._calls


def _result_when_ready(chooser: AsyncChooser, current_state_tag: str = "1250") -> ChoiceResult:
    """Poll until the chooser hands a result over; fails the test when none comes."""
    give_up_at = time.monotonic() + RESULT_LIMIT_SECONDS
    while time.monotonic() < give_up_at:
        result = chooser.poll(current_state_tag)
        if result is not None:
            return result
        time.sleep(0.005)
    raise AssertionError("the chooser never handed a result over")


def _events(match_record) -> list[tuple[str, dict]]:
    return [(line["kind"], line["data"]) for line in match_record() if line["kind"] in ("decision", "fallback")]


# -- asking without waiting ------------------------------------------------------------------------


def test_submit_returns_at_once_and_the_source_is_asked_on_a_worker_thread(run_folder):
    source = _Source(seconds=ANSWER_SECONDS)
    chooser = AsyncChooser(source, _never_stale)
    started = time.perf_counter()

    accepted = chooser.submit(_request())
    submit_seconds = time.perf_counter() - started

    assert accepted is True and submit_seconds < NO_WAIT_SECONDS
    assert source.finished.wait(RESULT_LIMIT_SECONDS)
    assert source.threads == ["decision-chooser"]


def test_poll_returns_nothing_while_the_source_is_asked_and_never_waits_for_it(run_folder):
    source = _Source(seconds=ANSWER_SECONDS)
    chooser = AsyncChooser(source, _never_stale)
    chooser.submit(_request())
    started = time.perf_counter()

    while_asking = chooser.poll("1250")
    poll_seconds = time.perf_counter() - started

    assert while_asking is None and poll_seconds < NO_WAIT_SECONDS
    assert not source.finished.is_set()


def test_the_finished_result_is_handed_over_once(run_folder):
    chooser = AsyncChooser(_Source(), _never_stale)
    chooser.submit(_request())

    result = _result_when_ready(chooser)

    assert result == ChoiceResult("RAID", None, "local:fast", 200, "used")
    assert chooser.poll("1250") is None


def test_poll_without_any_request_returns_nothing(run_folder):
    assert AsyncChooser(_Source(), _never_stale).poll("1250") is None


# -- one request at a time -------------------------------------------------------------------------


def test_a_second_submit_while_one_is_in_flight_returns_false_and_does_nothing(run_folder):
    source = _Source(seconds=ANSWER_SECONDS)
    chooser = AsyncChooser(source, _never_stale)
    first = chooser.submit(_request("1250"))

    second = chooser.submit(_request("1260"))

    result = _result_when_ready(chooser)
    assert (first, second) == (True, False)
    assert [request.state_tag for request in source.requests] == ["1250"]
    assert result.outcome == "used"


def test_a_request_stays_in_flight_until_its_result_has_been_handed_over(run_folder):
    source = _Source()
    chooser = AsyncChooser(source, _never_stale)
    chooser.submit(_request("1250"))
    assert source.finished.wait(RESULT_LIMIT_SECONDS)

    refused_before_poll = chooser.submit(_request("1260"))
    _result_when_ready(chooser)
    accepted_after_poll = chooser.submit(_request("1270"))

    assert (refused_before_poll, accepted_after_poll) == (False, True)
    _result_when_ready(chooser)
    assert [request.state_tag for request in source.requests] == ["1250", "1270"]


# -- stale answers ---------------------------------------------------------------------------------


def test_an_answer_the_game_s_rule_calls_stale_is_discarded(match_record):
    chooser = AsyncChooser(_Source(), _stale_when_the_tag_changed)
    chooser.submit(_request(state_tag="1250"))

    result = _result_when_ready(chooser, current_state_tag="1400")

    assert result == ChoiceResult(None, None, "local:fast", 200, "stale_discarded")
    (_, decision), (_, fallback) = _events(match_record)
    assert (decision["answer"], decision["outcome"], decision["state_version"]) == ("RAID", "stale_discarded", "1250")
    assert fallback == {"intended_source": "local:fast", "reason": "stale_discarded", "used": "script"}


def test_an_answer_for_the_situation_that_still_holds_is_used(match_record):
    chooser = AsyncChooser(_Source(), _stale_when_the_tag_changed)
    chooser.submit(_request(state_tag="1250"))

    result = _result_when_ready(chooser, current_state_tag="1250")

    assert (result.option_id, result.outcome) == ("RAID", "used")
    ((kind, decision),) = _events(match_record)
    assert (kind, decision["outcome"]) == ("decision", "used")


def test_the_rule_is_given_the_tag_of_the_question_and_the_current_tag(run_folder):
    seen = []

    def remember(asked_tag: str, current_tag: str) -> bool:
        seen.append((asked_tag, current_tag))
        return False

    chooser = AsyncChooser(_Source(), remember)
    chooser.submit(_request(state_tag="1250"))

    _result_when_ready(chooser, current_state_tag="1400")

    assert seen == [("1250", "1400")]


def test_a_call_that_gave_no_usable_answer_keeps_its_own_outcome_however_old_it_is(match_record):
    timed_out = _Source([SourceCall("local:fast", None, None, 1500, "timeout", "deadline")])
    chooser = AsyncChooser(timed_out, _stale_when_the_tag_changed)
    chooser.submit(_request(state_tag="1250"))

    result = _result_when_ready(chooser, current_state_tag="1400")

    assert (result.outcome, result.reason) == ("timeout", "deadline")
    (_, decision), (_, fallback) = _events(match_record)
    assert (decision["outcome"], fallback["reason"]) == ("timeout", "timeout")


def test_a_stale_answer_of_a_chain_leaves_the_script_to_decide_for_every_call(match_record):
    calls = [
        SourceCall("jev", None, None, 80, "error", "http_503"),
        SourceCall("local:fast", "HOLD", None, 300, "used"),
    ]
    chooser = AsyncChooser(_Source(calls), _stale_when_the_tag_changed)
    chooser.submit(_request(state_tag="1250"))

    result = _result_when_ready(chooser, current_state_tag="1400")

    assert (result.option_id, result.outcome) == (None, "stale_discarded")
    assert [(kind, data.get("outcome"), data.get("used")) for kind, data in _events(match_record)] == [
        ("decision", "error", None),
        ("fallback", None, "script"),
        ("decision", "stale_discarded", None),
        ("fallback", None, "script"),
    ]


# -- a question with one option -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current_tag", "outcome", "option_id"), [("1250", "used", "HOLD"), ("1400", "stale_discarded", None)]
)
def test_a_one_option_question_is_answered_by_the_chain_and_handed_over_as_a_normal_answer(
    match_record, current_tag, outcome, option_id
):
    source = _Source()
    chooser = AsyncChooser(ChainSource([source], name="red_alert_commander"), _stale_when_the_tag_changed)
    chooser.submit(ChoiceRequest("Which plan?", OPTIONS[:1], "Minute 6.", 1500, "1250"))

    result = _result_when_ready(chooser, current_tag)

    assert result == ChoiceResult(option_id, 1.0, "red_alert_commander", 0, outcome, "single_option")
    assert source.requests == []
    (kind, decision), *_ = _events(match_record)
    assert (kind, decision["answer"], decision["outcome"], decision["reason"]) == (
        "decision",
        "HOLD",
        outcome,
        "single_option",
    )


def test_after_a_one_option_question_the_next_question_is_asked_of_the_source(run_folder):
    source = _Source()
    chooser = AsyncChooser(ChainSource([source], name="red_alert_commander"), _never_stale)
    chooser.submit(ChoiceRequest("Which plan?", OPTIONS[:1], "Minute 6.", 1500, "1250"))
    first = _result_when_ready(chooser)

    accepted = chooser.submit(_request())
    second = _result_when_ready(chooser)

    assert (first.option_id, first.reason, accepted) == ("HOLD", "single_option", True)
    assert (second.option_id, second.source, second.outcome) == ("RAID", "local:fast", "used")
    assert len(source.requests) == 1


# -- the record ------------------------------------------------------------------------------------


def test_the_events_of_a_request_are_written_when_its_result_is_handed_over(run_folder, monkeypatch):
    recorded = []
    monkeypatch.setattr(
        chooser_module.journal, "record", lambda request, calls, asked: recorded.append((request.state_tag, asked))
    )
    source = _Source()
    chooser = AsyncChooser(source, _never_stale)
    chooser.submit(_request("1250"))
    assert source.finished.wait(RESULT_LIMIT_SECONDS)
    time.sleep(0.05)

    before_poll = list(recorded)
    _result_when_ready(chooser)

    assert (before_poll, recorded) == ([], [("1250", "local:fast")])


# -- a source that breaks its promise --------------------------------------------------------------


def test_a_source_that_raises_does_not_leave_the_chooser_stuck(run_folder, monkeypatch):
    class _Broken(_Source):
        def ask(self, request: ChoiceRequest) -> list[SourceCall]:
            raise RuntimeError("a bug in the source")

    uncaught = []
    monkeypatch.setattr(threading, "excepthook", lambda arguments: uncaught.append(arguments.exc_type))
    chooser = AsyncChooser(_Broken(), _never_stale)
    chooser.submit(_request())

    result = _result_when_ready(chooser)

    assert result == ChoiceResult(None, None, "local:fast", 0, "error", "no_source")
    assert uncaught == [RuntimeError]
    assert chooser.submit(_request()) is True


@pytest.mark.parametrize("current_tag", ["1250", "9999"])
def test_a_request_no_source_was_called_for_is_an_error_whatever_the_tag(run_folder, current_tag):
    chooser = AsyncChooser(_Source([]), _stale_when_the_tag_changed)
    chooser.submit(_request())

    assert _result_when_ready(chooser, current_tag) == ChoiceResult(None, None, "local:fast", 0, "error", "no_source")


# -- a staleness rule that breaks ------------------------------------------------------------------


def test_a_staleness_rule_that_raises_gives_an_error_result_and_never_an_exception(match_record):
    def broken_rule(asked_tag: str, current_tag: str) -> bool:
        raise KeyError("no such tick")

    chooser = AsyncChooser(_Source(), broken_rule)
    chooser.submit(_request(state_tag="1250"))

    result = _result_when_ready(chooser, current_state_tag="1400")

    assert result == ChoiceResult(None, None, "local:fast", 200, "error", "staleness_rule_KeyError")
    (_, decision), (_, fallback) = _events(match_record)
    assert (decision["answer"], decision["outcome"], decision["reason"]) == ("RAID", "error", "staleness_rule_KeyError")
    assert fallback == {"intended_source": "local:fast", "reason": "error", "used": "script"}


def test_after_a_staleness_rule_raised_the_next_request_is_asked_as_usual(run_folder):
    verdicts = iter([RuntimeError("the rule broke"), False])

    def rule_that_breaks_once(asked_tag: str, current_tag: str) -> bool:
        verdict = next(verdicts)
        if isinstance(verdict, Exception):
            raise verdict
        return verdict

    chooser = AsyncChooser(_Source(), rule_that_breaks_once)
    chooser.submit(_request())
    first = _result_when_ready(chooser)

    accepted = chooser.submit(_request())
    second = _result_when_ready(chooser)

    assert (first.outcome, first.reason, first.option_id) == ("error", "staleness_rule_RuntimeError", None)
    assert accepted is True
    assert (second.outcome, second.option_id) == ("used", "RAID")


def test_a_broken_rule_is_not_asked_about_a_call_that_gave_no_usable_answer(run_folder):
    def broken_rule(asked_tag: str, current_tag: str) -> bool:
        raise KeyError("no such tick")

    timed_out = _Source([SourceCall("local:fast", None, None, 1500, "timeout", "deadline")])
    chooser = AsyncChooser(timed_out, broken_rule)
    chooser.submit(_request())

    result = _result_when_ready(chooser)

    assert (result.outcome, result.reason) == ("timeout", "deadline")
