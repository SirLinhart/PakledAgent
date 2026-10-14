"""The one interface of every decision source."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ucla_agent.core.decide import journal
from ucla_agent.core.decide.choice import ERROR, ChoiceRequest, ChoiceResult
from ucla_agent.core.decide.journal import SourceCall, TextCall
from ucla_agent.core.decide.text import TEXT_UNSUPPORTED, TextRequest, TextResult


class DecisionSource(ABC):
    """Something that can be asked to choose among options or to write a short text: a model, a service, a chain.

    A source never raises into its caller. Whatever goes wrong comes back as a
    result with an outcome other than `used` and no option or text, and the
    caller applies its own scripted choice or text.

    `choose` and `write` wait for the answer, up to the request's deadline.
    Code that returns a game action in a real-time game must not call them on
    its own thread; `AsyncChooser` is for that.
    """

    name: str

    @abstractmethod
    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        """Ask for a choice and return every call that was made for it, in order.

        The last call is what the request ended in; the list is empty only
        when there was no source to call. Must not raise, and writes none of
        the request's events to the match record: that is left to whoever
        hands the result on.
        """

    def choose(self, request: ChoiceRequest) -> ChoiceResult:
        """Ask for a choice, write the request's events to the match record and return what it ended in."""
        calls = self.ask(request)
        journal.record(request, calls, self.name)
        return journal.result_of(calls, self.name)

    def ask_text(self, request: TextRequest) -> list[TextCall]:
        """Ask for a text and return every call that was made for it, in order, under the rules of `ask`.

        A source that cannot write text, as this one, answers with one `error`
        call whose reason is `text_unsupported`, and sends nothing.
        """
        return [TextCall(self.name, None, 0, ERROR, TEXT_UNSUPPORTED)]

    def write(self, request: TextRequest) -> TextResult:
        """Ask for a text, write the request's events to the match record and return what it ended in."""
        calls = self.ask_text(request)
        journal.record_text(request, calls, self.name)
        return journal.text_result_of(calls, self.name)

    def write_usage_event(self) -> None:
        """Write what the source used in the match to the record; a game agent calls it when the match ends.

        A source that counts nothing it uses, as this one, writes nothing.
        """
