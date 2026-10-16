"""The circuit breaker of a decision source, on a clock the test moves."""

from __future__ import annotations

import threading

import pytest

from ucla_agent.core.decide import circuit
from ucla_agent.core.decide.circuit import CircuitBreaker


@pytest.fixture
def clock(monkeypatch) -> dict[str, float]:
    """The breaker's clock, in seconds; a test moves it by changing `now`."""
    moment = {"now": 1000.0}
    monkeypatch.setattr(circuit, "_now", lambda: moment["now"])
    return moment


def _note(breaker: CircuitBreaker, *outcomes: str) -> None:
    for outcome in outcomes:
        breaker.note(outcome)


def test_a_new_breaker_is_closed(clock):
    assert CircuitBreaker().is_open() is False


def test_two_failures_in_a_row_leave_the_circuit_closed_and_the_third_opens_it(clock):
    breaker = CircuitBreaker()

    _note(breaker, "timeout", "timeout")
    after_two = breaker.is_open()
    breaker.note("timeout")

    assert (after_two, breaker.is_open()) == (False, True)


@pytest.mark.parametrize(
    "outcomes",
    [("error", "error", "error"), ("timeout", "error", "timeout"), ("error", "timeout", "timeout")],
    ids=["errors", "mixed", "mixed again"],
)
def test_timeouts_and_errors_count_alike(clock, outcomes):
    breaker = CircuitBreaker()

    _note(breaker, *outcomes)

    assert breaker.is_open() is True


@pytest.mark.parametrize("outcome_between", ["used", "invalid", "stale_discarded"])
def test_any_other_outcome_starts_the_count_again(clock, outcome_between):
    breaker = CircuitBreaker()

    _note(breaker, "timeout", "error", outcome_between, "timeout", "error")

    assert breaker.is_open() is False


def test_the_circuit_stays_open_for_12_seconds_and_is_half_open_after_that(clock):
    breaker = CircuitBreaker()
    _note(breaker, "timeout", "timeout", "timeout")

    clock["now"] += 11.9
    just_before = breaker.is_open()
    clock["now"] += 0.1

    assert (just_before, breaker.is_open()) == (True, False)
    assert (circuit.FAILURES_TO_OPEN, circuit.OPEN_SECONDS) == (3, 12.0)


def test_after_the_pause_a_used_answer_closes_the_circuit_and_failures_are_counted_from_zero(clock):
    breaker = CircuitBreaker()
    _note(breaker, "error", "error", "error")
    clock["now"] += 12.0
    assert breaker.is_open() is False

    _note(breaker, "used", "error", "error")
    after_two = breaker.is_open()
    breaker.note("error")

    assert (after_two, breaker.is_open()) == (False, True)


def test_a_probe_that_fails_after_the_pause_opens_the_circuit_again_at_once_for_another_12_seconds(clock):
    breaker = CircuitBreaker()
    _note(breaker, "timeout", "timeout", "timeout")
    clock["now"] += 12.0
    probing = breaker.is_open()
    breaker.note("timeout")

    reopened = breaker.is_open()
    clock["now"] += 11.0
    still_open = breaker.is_open()
    clock["now"] += 1.0

    assert (probing, reopened, still_open, breaker.is_open()) == (False, True, True, False)


def test_each_source_has_a_circuit_of_its_own(clock):
    failing, working = CircuitBreaker(), CircuitBreaker()

    _note(failing, "error", "error", "error")

    assert (failing.is_open(), working.is_open()) == (True, False)


def test_failures_noted_from_many_threads_at_once_open_the_circuit(clock):
    breaker = CircuitBreaker()
    threads = [threading.Thread(target=breaker.note, args=("timeout",)) for _ in range(20)]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert breaker.is_open() is True


def test_the_breaker_runs_on_a_clock_that_never_goes_backwards():
    assert circuit._now() <= circuit._now()
    assert circuit._now.__name__ == "monotonic"
