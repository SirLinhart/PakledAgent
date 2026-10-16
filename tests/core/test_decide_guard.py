"""One guarded call to a decision source, for a choice or a text: the deadline, the outcome it is turned into,
the usage it reports, and the circuit breaker."""

from __future__ import annotations

import threading
import time

import httpx
import pytest

from ucla_agent.core.decide import ChoiceRequest, Option, TextRequest, circuit
from ucla_agent.core.decide.guard import (
    Answer,
    SourceError,
    SourceGuard,
    UnusableAnswer,
    Usage,
    ensure_ok,
    usage_of,
)
from ucla_agent.core.decide.journal import SourceCall, TextCall

OPTIONS = [Option("HOLD", "Keep the army at the base."), Option("RAID", "Attack the harvesters.")]
SLOW_ANSWER_SECONDS = 0.4
SHORT_DEADLINE_MS = 200
CIRCUIT_OPEN_LIMIT_SECONDS = 0.005


def _request(deadline_ms: int = 1500) -> ChoiceRequest:
    return ChoiceRequest("Which plan?", OPTIONS, "Minute 6.", deadline_ms, "1250")


class _Exchange:
    """A source's request as the test wants it: remembers each call and answers, fails or takes its time."""

    def __init__(self, answer: Answer | Exception = Answer("RAID"), seconds: float = 0.0) -> None:
        self.http_timeouts: list[float] = []
        self.threads: list[str] = []
        self._answer = answer
        self._seconds = seconds

    def __call__(self, http_timeout_seconds: float) -> Answer:
        self.http_timeouts.append(http_timeout_seconds)
        self.threads.append(threading.current_thread().name)
        time.sleep(self._seconds)
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


@pytest.fixture
def circuit_clock(monkeypatch) -> dict[str, float]:
    """The circuit breaker's clock, in seconds; a test moves it by changing `now`."""
    moment = {"now": 500.0}
    monkeypatch.setattr(circuit, "_now", lambda: moment["now"])
    return moment


# -- answers ---------------------------------------------------------------------------------------


def test_an_answer_that_is_one_of_the_options_is_used():
    exchange = _Exchange(Answer("RAID"))

    call = SourceGuard("local:fast").call(_request(), exchange)

    assert (call.source, call.answer, call.confidence, call.outcome, call.reason) == (
        "local:fast",
        "RAID",
        None,
        "used",
        None,
    )
    assert 0 <= call.latency_ms < 100


def test_the_request_itself_runs_on_a_helper_thread():
    exchange = _Exchange()

    SourceGuard("local:fast").call(_request(), exchange)

    assert exchange.threads == ["decision-source-call"]


@pytest.mark.parametrize("deadline_ms, http_timeout", [(1500, 1.5), (200, 0.2), (50, 0.05), (20, 0.05), (1, 0.05)])
def test_the_http_timeout_is_the_deadline_and_never_less_than_50_ms(deadline_ms, http_timeout):
    exchange = _Exchange()

    SourceGuard("local:fast").call(_request(deadline_ms), exchange)

    assert exchange.http_timeouts == [http_timeout]


def test_an_answer_that_is_not_one_of_the_options_is_invalid_and_kept_for_the_record():
    call = SourceGuard("local:fast").call(_request(), _Exchange(Answer("zzz")))

    assert (call.answer, call.outcome, call.reason) == ("zzz", "invalid", "not_an_option")
    assert call.result().option_id is None


@pytest.mark.parametrize(
    "confidence, outcome, reason",
    [
        (0.40, "invalid", "low_confidence"),
        (0.5499, "invalid", "low_confidence"),
        (0.55, "used", None),
        (0.9, "used", None),
    ],
)
def test_an_answer_below_the_source_s_minimum_confidence_is_invalid(confidence, outcome, reason):
    call = SourceGuard("jev", min_confidence=0.55).call(_request(), _Exchange(Answer("HOLD", confidence)))

    assert (call.answer, call.confidence, call.outcome, call.reason) == ("HOLD", confidence, outcome, reason)


def test_an_answer_without_a_confidence_is_invalid_for_a_source_that_needs_one():
    call = SourceGuard("jev", min_confidence=0.55).call(_request(), _Exchange(Answer("HOLD")))

    assert (call.outcome, call.reason) == ("invalid", "low_confidence")


def test_a_source_without_a_minimum_uses_an_answer_whatever_its_confidence():
    call = SourceGuard("local:fast").call(_request(), _Exchange(Answer("HOLD", 0.1)))

    assert (call.outcome, call.confidence) == ("used", 0.1)


# -- the deadline ----------------------------------------------------------------------------------


def test_a_request_slower_than_the_deadline_is_given_up_at_the_deadline_as_a_timeout():
    exchange = _Exchange(Answer("RAID"), seconds=SLOW_ANSWER_SECONDS)
    started = time.perf_counter()

    call = SourceGuard("local:fast").call(_request(SHORT_DEADLINE_MS), exchange)
    waited_seconds = time.perf_counter() - started

    assert (call.answer, call.outcome, call.reason) == (None, "timeout", "deadline")
    assert 200 <= call.latency_ms <= 300
    assert waited_seconds < SLOW_ANSWER_SECONDS


def test_what_an_abandoned_request_brings_back_later_changes_nothing():
    guard = SourceGuard("local:fast")
    guard.call(_request(50), _Exchange(Answer("RAID"), seconds=0.15))
    time.sleep(0.25)

    call = guard.call(_request(), _Exchange(Answer("HOLD")))

    assert (call.answer, call.outcome) == ("HOLD", "used")


@pytest.mark.parametrize(
    "failure",
    [httpx.ReadTimeout("no bytes"), httpx.ConnectTimeout("no connection"), TimeoutError("timed out")],
    ids=["read timeout", "connect timeout", "builtin timeout"],
)
def test_a_timeout_of_the_http_client_itself_is_a_timeout(failure):
    started = time.perf_counter()

    call = SourceGuard("jev").call(_request(), _Exchange(failure))

    assert (call.outcome, call.reason) == ("timeout", "deadline")
    assert time.perf_counter() - started < 0.5


# -- errors ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure, reason",
    [
        (SourceError("http_503"), "http_503"),
        (SourceError("malformed_answer"), "malformed_answer"),
        (httpx.ConnectError("connection refused"), "connection_failed"),
        (httpx.RemoteProtocolError("server hung up"), "connection_failed"),
        (KeyError("choices"), "unexpected_KeyError"),
    ],
    ids=["http status", "malformed", "refused", "hung up", "a bug in the source"],
)
def test_a_request_that_fails_is_an_error_with_its_reason_and_never_an_exception(failure, reason):
    call = SourceGuard("jev").call(_request(), _Exchange(failure))

    assert call == SourceCall("jev", None, None, call.latency_ms, "error", reason)


@pytest.mark.parametrize("status", [400, 401, 404, 429, 500, 503])
def test_an_http_4xx_or_5xx_answer_is_refused_with_its_status(status):
    with pytest.raises(SourceError) as refused:
        ensure_ok(httpx.Response(status))

    assert refused.value.reason == f"http_{status}"


@pytest.mark.parametrize("status", [200, 201, 204, 302])
def test_any_other_http_answer_is_let_through(status):
    ensure_ok(httpx.Response(status))


# -- the circuit breaker ---------------------------------------------------------------------------


def test_after_three_timeouts_in_a_row_the_source_is_not_called_and_the_answer_comes_at_once(circuit_clock):
    guard = SourceGuard("local:fast")
    for _ in range(3):
        guard.call(_request(50), _Exchange(seconds=0.15))
    exchange = _Exchange()
    started = time.perf_counter()

    call = guard.call(_request(), exchange)
    seconds = time.perf_counter() - started

    assert call == SourceCall("local:fast", None, None, 0, "error", "circuit_open")
    assert exchange.http_timeouts == []
    assert seconds < CIRCUIT_OPEN_LIMIT_SECONDS


def test_twelve_seconds_later_the_source_is_called_again(circuit_clock):
    guard = SourceGuard("local:fast")
    for _ in range(3):
        guard.call(_request(), _Exchange(httpx.ConnectError("connection refused")))
    circuit_clock["now"] += 11.0
    skipped = guard.call(_request(), _Exchange())
    circuit_clock["now"] += 1.0
    exchange = _Exchange(Answer("HOLD"))

    call = guard.call(_request(), exchange)

    assert (skipped.outcome, skipped.reason) == ("error", "circuit_open")
    assert (call.answer, call.outcome) == ("HOLD", "used")
    assert len(exchange.http_timeouts) == 1


def test_a_skipped_call_does_not_count_as_a_failure_of_the_source(circuit_clock):
    guard = SourceGuard("local:fast")
    for _ in range(3):
        guard.call(_request(), _Exchange(SourceError("http_503")))
    for _ in range(5):
        guard.call(_request(), _Exchange())
    circuit_clock["now"] += 12.0

    # The pause is over whatever the five calls skipped meanwhile: the source is probed.
    exchange = _Exchange()
    guard.call(_request(), exchange)

    assert len(exchange.http_timeouts) == 1


def test_a_probe_that_fails_pauses_the_source_again_at_once(circuit_clock):
    guard = SourceGuard("local:fast")
    for _ in range(3):
        guard.call(_request(), _Exchange(SourceError("http_503")))
    circuit_clock["now"] += 12.0
    guard.call(_request(), _Exchange(SourceError("http_503")))

    skipped = guard.call(_request(), _Exchange())

    assert (skipped.outcome, skipped.reason) == ("error", "circuit_open")


def test_a_used_answer_between_failures_keeps_the_circuit_closed(circuit_clock):
    guard = SourceGuard("local:fast")
    outcomes = []
    for exchange in (
        _Exchange(SourceError("http_500")),
        _Exchange(SourceError("http_500")),
        _Exchange(Answer("HOLD")),
        _Exchange(SourceError("http_500")),
        _Exchange(SourceError("http_500")),
        _Exchange(Answer("RAID")),
    ):
        outcomes.append(guard.call(_request(), exchange).outcome)

    assert outcomes == ["error", "error", "used", "error", "error", "used"]


def test_invalid_answers_do_not_open_the_circuit(circuit_clock):
    guard = SourceGuard("local:fast")

    outcomes = [guard.call(_request(), _Exchange(Answer("zzz"))).outcome for _ in range(5)]

    assert outcomes == ["invalid"] * 5


def test_each_guard_has_a_circuit_of_its_own(circuit_clock):
    failing, working = SourceGuard("jev"), SourceGuard("local:fast")
    for _ in range(3):
        failing.call(_request(), _Exchange(SourceError("http_503")))

    assert failing.call(_request(), _Exchange()).reason == "circuit_open"
    assert working.call(_request(), _Exchange()).outcome == "used"


# -- texts -----------------------------------------------------------------------------------------


def _text_request(deadline_ms: int = 1500) -> TextRequest:
    return TextRequest("Write your message.", "Day 2.", 40, deadline_ms, "day-2")


def test_a_text_that_arrived_in_time_is_used_with_the_usage_the_model_reported():
    exchange = _Exchange(Answer("I vote seat 5.", usage=Usage(310, 6)))

    call = SourceGuard("local:strong").call_text(_text_request(), exchange)

    assert call == TextCall("local:strong", "I vote seat 5.", call.latency_ms, "used", None, 310, 6)
    assert exchange.threads == ["decision-source-call"]


def test_an_empty_text_is_invalid_and_does_not_count_against_the_source(circuit_clock):
    guard = SourceGuard("local:strong")

    calls = [guard.call_text(_text_request(), _Exchange(Answer(""))) for _ in range(4)]

    assert [(call.outcome, call.reason, call.text) for call in calls] == [("invalid", "empty_text", "")] * 4


@pytest.mark.parametrize(
    "failure, outcome, reason",
    [
        (SourceError("http_503"), "error", "http_503"),
        (httpx.ConnectError("connection refused"), "error", "connection_failed"),
        (httpx.ReadTimeout("slow"), "timeout", "deadline"),
        (UnusableAnswer("refusal", Usage(12, 3)), "invalid", "refusal"),
    ],
    ids=["http status", "refused", "client timeout", "unusable"],
)
def test_a_text_call_that_fails_maps_its_failure_like_a_choice_call(failure, outcome, reason):
    call = SourceGuard("local:strong").call_text(_text_request(), _Exchange(failure))

    assert (call.text, call.outcome, call.reason) == (None, outcome, reason)


def test_a_text_past_the_deadline_is_a_timeout():
    call = SourceGuard("local:strong").call_text(_text_request(SHORT_DEADLINE_MS), _Exchange(seconds=0.4))

    assert (call.outcome, call.reason) == ("timeout", "deadline")
    assert SHORT_DEADLINE_MS <= call.latency_ms <= SHORT_DEADLINE_MS + 100


def test_text_and_choice_failures_open_one_circuit(circuit_clock):
    guard = SourceGuard("local:strong")
    guard.call_text(_text_request(), _Exchange(SourceError("http_503")))
    guard.call(_request(), _Exchange(SourceError("http_503")))
    guard.call_text(_text_request(), _Exchange(SourceError("http_503")))
    exchange = _Exchange()

    choice, text = guard.call(_request(), exchange), guard.call_text(_text_request(), exchange)

    assert (choice.reason, text.reason) == ("circuit_open", "circuit_open")
    assert text == TextCall("local:strong", None, 0, "error", "circuit_open")
    assert exchange.http_timeouts == []


# -- usage -----------------------------------------------------------------------------------------


def test_a_choice_call_carries_the_usage_of_its_answer_and_of_an_error_that_reports_one():
    guard = SourceGuard("claude:sonnet")

    used = guard.call(_request(), _Exchange(Answer("HOLD", usage=Usage(500, 7))))
    unusable = guard.call(_request(), _Exchange(UnusableAnswer("refusal", Usage(480, 2))))
    malformed = guard.call(_request(), _Exchange(SourceError("malformed_answer", Usage(470, 16))))

    assert [(call.outcome, call.input_tokens, call.output_tokens) for call in (used, unusable, malformed)] == [
        ("used", 500, 7),
        ("invalid", 480, 2),
        ("error", 470, 16),
    ]


@pytest.mark.parametrize(
    "reported, expected",
    [
        ({"prompt_tokens": 412, "completion_tokens": 9}, Usage(412, 9)),
        ({"prompt_tokens": 412}, Usage(412, None)),
        ({"prompt_tokens": True, "completion_tokens": "9"}, Usage(None, None)),
        (None, Usage(None, None)),
        ([412, 9], Usage(None, None)),
    ],
    ids=["both", "one", "not numbers", "none", "not an object"],
)
def test_the_usage_is_read_from_the_counts_a_service_reports_under_its_own_names(reported, expected):
    assert usage_of(reported, "prompt_tokens", "completion_tokens") == expected
