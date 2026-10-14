"""Stub decision sources for the Werewolf agent's tests: nothing here reaches a model server or the network.

`Answering` gives one fixed choice and one fixed text to whatever it is asked,
as a source whose answer arrived in time and was used. `Guarded` answers
through the same guard every real source calls through: an answer that is not
one of the options is refused, and an answer slower than the request's
deadline is given up at the deadline. Both keep every request they were asked.
"""

from __future__ import annotations

import time

from ucla_agent.core.decide import USED, ChoiceRequest, DecisionSource, TextRequest
from ucla_agent.core.decide.guard import Answer, SourceGuard
from ucla_agent.core.decide.journal import SourceCall, TextCall

ANSWER_LATENCY_MS = 5
DEFAULT_TEXT = "No text worth reading."


class Answering(DecisionSource):
    """A source that answers every choice with `choice` and every text with `text`, at once and as used."""

    name = "stub:answering"

    def __init__(self, choice: str | None = None, text: str | None = None) -> None:
        self._choice = choice
        self._text = text
        self.choice_requests: list[ChoiceRequest] = []
        self.text_requests: list[TextRequest] = []

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        self.choice_requests.append(request)
        return [SourceCall(self.name, self._choice, None, ANSWER_LATENCY_MS, USED)]

    def ask_text(self, request: TextRequest) -> list[TextCall]:
        self.text_requests.append(request)
        return [TextCall(self.name, self._text, ANSWER_LATENCY_MS, USED)]


class Guarded(DecisionSource):
    """A source that answers `choice` and `text` after `seconds`, held to each request by a real source's guard."""

    name = "stub:guarded"

    def __init__(self, choice: str = "P0", text: str = DEFAULT_TEXT, seconds: float = 0.0) -> None:
        self._choice = choice
        self._text = text
        self._seconds = seconds
        self._guard = SourceGuard(self.name)
        self.choice_requests: list[ChoiceRequest] = []
        self.text_requests: list[TextRequest] = []

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        self.choice_requests.append(request)
        return [self._guard.call(request, lambda _timeout: self._answer(self._choice))]

    def ask_text(self, request: TextRequest) -> list[TextCall]:
        self.text_requests.append(request)
        return [self._guard.call_text(request, lambda _timeout: self._answer(self._text))]

    def _answer(self, value: str) -> Answer:
        time.sleep(self._seconds)
        return Answer(value)
