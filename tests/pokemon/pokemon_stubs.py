"""Stub decision sources for the Pokémon agent's tests: nothing here reaches a model server or the network.

`Answering` gives one fixed choice to whatever it is asked, as a source whose
answer arrived in time and was used. `Guarded` answers after a delay through
the guard every real source calls through, so an answer slower than the
request's deadline is given up at the deadline. Both keep every request.
"""

from __future__ import annotations

import time

from ucla_agent.core.decide import USED, ChoiceRequest, DecisionSource
from ucla_agent.core.decide.guard import Answer, SourceGuard
from ucla_agent.core.decide.journal import SourceCall

ANSWER_LATENCY_MS = 5


class Answering(DecisionSource):
    """A source that answers every choice with `choice`, at once and as used."""

    name = "stub:answering"

    def __init__(self, choice: str | None = None) -> None:
        self._choice = choice
        self.choice_requests: list[ChoiceRequest] = []

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        self.choice_requests.append(request)
        return [SourceCall(self.name, self._choice, None, ANSWER_LATENCY_MS, USED)]


class Guarded(DecisionSource):
    """A source that answers `choice` after `seconds`, held to each request's deadline by a real source's guard."""

    name = "stub:guarded"

    def __init__(self, choice: str, seconds: float) -> None:
        self._choice = choice
        self._seconds = seconds
        self._guard = SourceGuard(self.name)
        self.choice_requests: list[ChoiceRequest] = []

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        self.choice_requests.append(request)
        return [self._guard.call(request, lambda _timeout: self._answer())]

    def _answer(self) -> Answer:
        time.sleep(self._seconds)
        return Answer(self._choice)
