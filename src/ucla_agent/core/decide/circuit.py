"""A circuit breaker for one decision source.

A source that keeps failing costs its caller the full deadline every time it
is asked. After a few failures in a row the source is therefore left alone
for a while, and a caller gets its answer (no choice, apply the script) at
once instead of after waiting.
"""

from __future__ import annotations

import threading
import time

from ucla_agent.core.decide.choice import ERROR, TIMEOUT

FAILURES_TO_OPEN = 3
OPEN_SECONDS = 30.0
# The outcomes that say the source itself is in trouble. An answer that was
# invalid still came from a source that is up and answering in time.
FAILING_OUTCOMES = frozenset({TIMEOUT, ERROR})

# Never goes backwards, so a change of the system clock cannot keep a circuit open or closed.
_now = time.monotonic


class CircuitBreaker:
    """Counts one source's failures in a row and says when the source is to be skipped.

    Safe to use from several threads: a source may be asked by more than one
    chooser at a time.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures_in_a_row = 0
        self._open_until: float | None = None

    def is_open(self) -> bool:
        """Whether the source is being skipped right now.

        Once the pause is over the source is asked again, and its failures
        are counted from zero.
        """
        with self._lock:
            if self._open_until is None:
                return False
            if _now() < self._open_until:
                return True
            self._open_until = None
            self._failures_in_a_row = 0
            return False

    def note(self, outcome: str) -> None:
        """Take the outcome of one call that was made to the source into account."""
        with self._lock:
            if outcome not in FAILING_OUTCOMES:
                self._failures_in_a_row = 0
                return
            self._failures_in_a_row += 1
            if self._failures_in_a_row >= FAILURES_TO_OPEN:
                self._open_until = _now() + OPEN_SECONDS
