"""One call to one decision source: held to its deadline and turned into an outcome.

Every source that talks to a service does so through a `SourceGuard`, which
is what makes the promise of the decision sources true: the caller has its
answer by the deadline, and the answer is a result, never an exception.

The deadline is enforced here and not left to the HTTP client. A client's
timeout limits each step of a request (connecting, waiting for the next
bytes), not the request as a whole, so a slow service could keep a caller far
longer than it asked for. The request therefore runs on a helper thread, and
the caller stops waiting at the deadline whatever that thread is doing.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from ucla_agent.core.decide.choice import CIRCUIT_OPEN, ERROR, INVALID, TIMEOUT, USED, ChoiceRequest
from ucla_agent.core.decide.circuit import CircuitBreaker
from ucla_agent.core.decide.journal import SourceCall, TextCall
from ucla_agent.core.decide.model_io import NOTHING_RECEIVED, ModelIO, Received, Sent
from ucla_agent.core.decide.text import TextRequest

# Reasons a call did not end in a used answer.
DEADLINE = "deadline"
CONNECTION_FAILED = "connection_failed"
MALFORMED_ANSWER = "malformed_answer"
NOT_AN_OPTION = "not_an_option"
LOW_CONFIDENCE = "low_confidence"
EMPTY_TEXT = "empty_text"
HTTP_STATUS_REASON = "http_{status}"
UNEXPECTED_REASON = "unexpected_{exception}"

# With less than this an HTTP client could not even open a connection, so a
# request with almost no time left still gets a real attempt.
MIN_HTTP_TIMEOUT_MS = 50
FIRST_ERROR_STATUS = 400
_MILLISECONDS_PER_SECOND = 1000
# Resolves well under a millisecond on every platform, which a latency of a few milliseconds needs.
_clock = time.perf_counter


@dataclass(frozen=True)
class Usage:
    """The tokens a model reports for one answer: those it read and those it wrote; None where it reports none."""

    input_tokens: int | None = None
    output_tokens: int | None = None


NO_USAGE = Usage()


def usage_of(reported: Any, input_key: str, output_key: str) -> Usage:
    """The usage in the part of an answer that reports it, under the names the service gives the two counts."""
    if not isinstance(reported, dict):
        return NO_USAGE
    return Usage(_token_count(reported.get(input_key)), _token_count(reported.get(output_key)))


def _token_count(value: Any) -> int | None:
    # bool is a kind of int in Python, and `true` is not a number of tokens.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(frozen=True)
class Answer:
    """What a source said: the option it chose or the text it wrote, how sure it is, and the tokens it took.

    `received` is the reply it was read from, as it came back, and
    `model_reason` the short reason a model gave for its choice, if any.
    """

    value: str
    confidence: float | None = None
    usage: Usage = NO_USAGE
    received: Received = NOTHING_RECEIVED
    model_reason: str | None = None


class SourceError(Exception):
    """A source answered, but not with something that can be read as an answer; `reason` names what was wrong.

    `usage` is what the model reported for the answer, when it answered at
    all, and `received` the reply as it came back.
    """

    def __init__(self, reason: str, usage: Usage = NO_USAGE, received: Received = NOTHING_RECEIVED) -> None:
        super().__init__(reason)
        self.reason = reason
        self.usage = usage
        self.received = received


class UnusableAnswer(SourceError):
    """A source answered in a way that can be read but not used, such as refusing; the call is `invalid`."""


# A source's own request: given the HTTP timeout in seconds, it returns the
# source's answer or raises `SourceError` or an `httpx` error.
Exchange = Callable[[float], Answer]


def ensure_ok(response: httpx.Response) -> None:
    """Raise `SourceError` for an HTTP 4xx or 5xx answer."""
    if response.status_code >= FIRST_ERROR_STATUS:
        raise SourceError(HTTP_STATUS_REASON.format(status=response.status_code))


def holding(received: Received, read: Callable[[], Answer]) -> Answer:
    """The answer `read` gives, holding the reply as it came back; a `SourceError` it raises holds the reply too.

    A source reads every reply through this, so the record keeps the model's
    raw output whether the answer turns out to be used, unusable or unreadable.
    """
    try:
        answer = read()
    except SourceError as exc:
        exc.received = received
        raise
    return dataclasses.replace(answer, received=received)


@dataclass(frozen=True)
class _Verdict:
    outcome: str
    reason: str | None
    answer: Answer | None = None
    usage: Usage = NO_USAGE
    received: Received = NOTHING_RECEIVED


class SourceGuard:
    """Makes the calls to one source: under the deadline, behind a circuit breaker, each ending in an outcome.

    Choices and texts are asked through the same guard, so a source that
    fails at either is left alone for both.
    """

    def __init__(self, name: str, min_confidence: float | None = None) -> None:
        """`min_confidence` is the least confidence an answer needs to be used; None for a source that gives none."""
        self._name = name
        self._min_confidence = min_confidence
        self._breaker = CircuitBreaker()

    def call(self, request: ChoiceRequest, exchange: Exchange, sent: Sent | None = None) -> SourceCall:
        """Make one call for `request` and return what came of it; never raises.

        `exchange` is given the HTTP timeout to use: the request's deadline,
        or `MIN_HTTP_TIMEOUT_MS` when that is shorter. The call is given up
        at the deadline all the same. While the source's circuit is open
        `exchange` is not called at all. `sent` is what the exchange sends to
        the model; the call's `io` holds it with the reply that came back.
        """
        if self._breaker.is_open():
            return SourceCall(self._name, None, None, 0, ERROR, CIRCUIT_OPEN)
        verdict, latency_ms = self._run(request.deadline_ms, exchange, lambda answer: self._judge(request, answer))
        answer = verdict.answer
        return SourceCall(
            source=self._name,
            answer=None if answer is None else answer.value,
            confidence=None if answer is None else answer.confidence,
            latency_ms=latency_ms,
            outcome=verdict.outcome,
            reason=verdict.reason,
            input_tokens=verdict.usage.input_tokens,
            output_tokens=verdict.usage.output_tokens,
            io=_io(sent, verdict),
            model_reason=None if answer is None else answer.model_reason,
        )

    def call_text(self, request: TextRequest, exchange: Exchange, sent: Sent | None = None) -> TextCall:
        """Make one call for a text under the rules of `call`; a text that is empty is not used."""
        if self._breaker.is_open():
            return TextCall(self._name, None, 0, ERROR, CIRCUIT_OPEN)
        verdict, latency_ms = self._run(request.deadline_ms, exchange, _judge_text)
        answer = verdict.answer
        return TextCall(
            source=self._name,
            text=None if answer is None else answer.value,
            latency_ms=latency_ms,
            outcome=verdict.outcome,
            reason=verdict.reason,
            input_tokens=verdict.usage.input_tokens,
            output_tokens=verdict.usage.output_tokens,
            io=_io(sent, verdict),
        )

    def _run(self, deadline_ms: int, exchange: Exchange, judge: Callable[[Answer], _Verdict]) -> tuple[_Verdict, int]:
        """Make the exchange under the deadline and judge what came of it; returns the verdict and the latency."""
        started = _clock()
        http_timeout_seconds = max(deadline_ms, MIN_HTTP_TIMEOUT_MS) / _MILLISECONDS_PER_SECOND
        try:
            answer = _within(deadline_ms / _MILLISECONDS_PER_SECOND, lambda: exchange(http_timeout_seconds))
        except Exception as exc:  # noqa: BLE001 - whatever a source's code runs into, the game gets a result
            verdict = _failure(exc)
        else:
            verdict = dataclasses.replace(judge(answer), usage=answer.usage, received=answer.received)
        latency_ms = round((_clock() - started) * _MILLISECONDS_PER_SECOND)
        self._breaker.note(verdict.outcome)
        return verdict, latency_ms

    def _judge(self, request: ChoiceRequest, answer: Answer) -> _Verdict:
        """Whether a choice that arrived in time can be used."""
        if answer.value not in request.option_ids():
            return _Verdict(INVALID, NOT_AN_OPTION, answer)
        if self._min_confidence is not None and _confidence_of(answer) < self._min_confidence:
            return _Verdict(INVALID, LOW_CONFIDENCE, answer)
        return _Verdict(USED, None, answer)


def _io(sent: Sent | None, verdict: _Verdict) -> ModelIO | None:
    """What a call sent and the reply it got; None for a call that sent nothing to a model."""
    return None if sent is None else ModelIO(sent, verdict.received)


def _judge_text(answer: Answer) -> _Verdict:
    """Whether a text that arrived in time can be used: anything but nothing at all."""
    if not answer.value:
        return _Verdict(INVALID, EMPTY_TEXT, answer)
    return _Verdict(USED, None, answer)


def _confidence_of(answer: Answer) -> float:
    """The answer's confidence; none at all counts as the least there is."""
    return 0.0 if answer.confidence is None else answer.confidence


def _failure(exc: Exception) -> _Verdict:
    """The outcome a call that raised is given."""
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return _Verdict(TIMEOUT, DEADLINE)
    if isinstance(exc, SourceError):
        outcome = INVALID if isinstance(exc, UnusableAnswer) else ERROR
        return _Verdict(outcome, exc.reason, usage=exc.usage, received=exc.received)
    if isinstance(exc, httpx.HTTPError):
        return _Verdict(ERROR, CONNECTION_FAILED)
    return _Verdict(ERROR, UNEXPECTED_REASON.format(exception=type(exc).__name__))


def _answer_into(answered: Future[Answer], exchange: Callable[[], Answer]) -> None:
    """The helper thread's work: make the exchange and leave its answer, or what it raised, for the caller."""
    try:
        answered.set_result(exchange())
    except Exception as exc:  # noqa: BLE001 - passed on to the waiting caller, who decides what it means
        answered.set_exception(exc)


def _within(seconds: float, exchange: Callable[[], Answer]) -> Answer:
    """Run `exchange` on a helper thread and return its answer, or raise what it raised.

    Raises `TimeoutError` when it has not finished after `seconds`. The helper
    thread is then left to itself: it ends when its HTTP request ends, and
    whatever it brings back is dropped.
    """
    answered: Future[Answer] = Future()
    threading.Thread(target=_answer_into, args=(answered, exchange), name="decision-source-call", daemon=True).start()
    give_up_at = _clock() + seconds
    while True:
        try:
            return answered.result(timeout=max(0.0, give_up_at - _clock()))
        except TimeoutError:
            # A wait can end a moment early on a coarse system timer; the
            # deadline is only reached when the clock says so.
            if answered.done() or _clock() >= give_up_at:
                raise
