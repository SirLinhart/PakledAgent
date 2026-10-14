"""Asking a decision source from a real-time game without waiting for the answer.

A real-time game returns an action several times a second, and a model takes
longer than that to answer. The game therefore hands its question to an
`AsyncChooser`, goes on playing by its script, and looks on every later call
whether the answer has arrived. Neither `submit` nor `poll` ever waits for
the source.

By the time an answer arrives the situation it was asked about may be gone.
Only the game can tell: it gives the chooser a rule that compares the state
tag the question was asked with to the current one, and an answer the rule
calls stale is discarded instead of being handed over as a choice.
"""

from __future__ import annotations

import dataclasses
import threading
from typing import Callable

from ucla_agent.core.decide import journal
from ucla_agent.core.decide.choice import ERROR, STALE_DISCARDED, USED, ChoiceRequest, ChoiceResult
from ucla_agent.core.decide.journal import SourceCall
from ucla_agent.core.decide.source import DecisionSource

# The game's staleness rule: given the state tag a question was asked with
# and the current one, whether an answer to that question is out of date.
StalenessRule = Callable[[str, str], bool]
# The reason of the `error` result a request ends in when that rule raises.
STALENESS_RULE_REASON = "staleness_rule_{exception}"


@dataclasses.dataclass
class _InFlight:
    """The one request a chooser is busy with; `calls` is None until the source has been asked."""

    request: ChoiceRequest
    calls: list[SourceCall] | None = None


class AsyncChooser:
    """Puts one question at a time to a decision source on a worker thread."""

    def __init__(self, source: DecisionSource, is_stale: StalenessRule) -> None:
        self._source = source
        self._is_stale = is_stale
        self._lock = threading.Lock()
        self._in_flight: _InFlight | None = None

    def submit(self, request: ChoiceRequest) -> bool:
        """Start asking the source and return at once.

        Returns False, and does nothing, while an earlier request is in
        flight: from its `submit` until `poll` has handed its result over.
        """
        with self._lock:
            if self._in_flight is not None:
                return False
            in_flight = self._in_flight = _InFlight(request)
        threading.Thread(target=self._ask, args=(in_flight,), name="decision-chooser", daemon=True).start()
        return True

    def poll(self, current_state_tag: str) -> ChoiceResult | None:
        """The result of the request in flight, once; None while it is still being asked, or when there is none.

        An answer the game's rule calls stale for `current_state_tag` comes
        back with the outcome `stale_discarded` and no option. A rule that
        raises does not raise out of here: the answer then comes back as an
        `error` with no option. The request's events are written to the match
        record here, when its result is handed over, so the record holds the
        outcome the game acted on.
        """
        with self._lock:
            in_flight = self._in_flight
            if in_flight is None or in_flight.calls is None:
                return None
            self._in_flight = None
        calls = self._without_a_stale_answer(in_flight.request, in_flight.calls, current_state_tag)
        journal.record(in_flight.request, calls, self._source.name)
        return journal.result_of(calls, self._source.name)

    def _ask(self, in_flight: _InFlight) -> None:
        """The worker thread's work: ask the source and leave what came of it for `poll`."""
        calls: list[SourceCall] = []
        try:
            calls = self._source.ask(in_flight.request)
        finally:
            # Also when a source breaks its promise not to raise: a request
            # that never finished would keep every later one from being asked.
            with self._lock:
                in_flight.calls = calls

    def _without_a_stale_answer(
        self, request: ChoiceRequest, calls: list[SourceCall], current_state_tag: str
    ) -> list[SourceCall]:
        """The calls, with a used answer marked as discarded when the game's rule says it is out of date."""
        if not calls or calls[-1].outcome != USED:
            return calls
        return [*calls[:-1], self._judged_by_the_rule(request, calls[-1], current_state_tag)]

    def _judged_by_the_rule(self, request: ChoiceRequest, call: SourceCall, current_state_tag: str) -> SourceCall:
        """A used answer as the game's rule leaves it: kept, discarded as stale, or an error when the rule fails."""
        try:
            stale = self._is_stale(request.state_tag, current_state_tag)
        except Exception as exc:  # noqa: BLE001 - the rule is game code, and nothing here may raise into the game
            # Without the rule's verdict the answer cannot be known to fit the situation, so it is not used.
            reason = STALENESS_RULE_REASON.format(exception=type(exc).__name__)
            return dataclasses.replace(call, outcome=ERROR, reason=reason)
        return dataclasses.replace(call, outcome=STALE_DISCARDED) if stale else call
