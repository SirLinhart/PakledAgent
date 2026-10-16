"""A circuit breaker for one decision source.

A source that keeps failing costs its caller the full deadline every time it
is asked. After a few failures in a row the source is therefore left alone
for a while, and a caller gets its answer (no choice, apply the script) at
once instead of after waiting.

Once the pause is over the circuit is half open: the source is asked again,
and the first outcome decides. A failure opens the circuit again at once for
another pause, so a source still down costs one deadline a pause; anything
else closes it, and failures are counted from zero. The pause is short
(`OPEN_SECONDS`), so that a model server that restarts or catches up during a
match is used again within seconds: in a live match of 20261016 the
commander's model timed out at 7:28 and, probed every 30 seconds and needing
three failures to pause again, answered nothing for the rest of the match.
"""

from __future__ import annotations

import threading
import time

from ucla_agent.core.decide.choice import ERROR, TIMEOUT

FAILURES_TO_OPEN = 3
# How long an open circuit pauses before its probe: a source back up is used again within this long.
OPEN_SECONDS = 12.0
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
        # Half open: the pause is over and the next outcome decides.
        self._is_probing = False

    def is_open(self) -> bool:
        """Whether the source is being skipped right now.

        Once the pause is over the source is asked again: the circuit is half
        open until the next outcome is noted.
        """
        with self._lock:
            if self._open_until is None:
                return False
            if _now() < self._open_until:
                return True
            self._open_until = None
            self._failures_in_a_row = 0
            self._is_probing = True
            return False

    def note(self, outcome: str) -> None:
        """Take the outcome of one call that was made to the source into account."""
        with self._lock:
            is_probe, self._is_probing = self._is_probing, False
            if outcome not in FAILING_OUTCOMES:
                self._failures_in_a_row = 0
                return
            self._failures_in_a_row += 1
            if is_probe or self._failures_in_a_row >= FAILURES_TO_OPEN:
                self._open_until = _now() + OPEN_SECONDS
