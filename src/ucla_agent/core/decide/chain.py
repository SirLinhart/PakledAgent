"""Several decision sources tried in order, within one deadline."""

from __future__ import annotations

import dataclasses
import time
from typing import Callable, Sequence, TypeVar

from ucla_agent.core.decide.choice import SINGLE_OPTION, USED, ChoiceRequest
from ucla_agent.core.decide.journal import SourceCall, TextCall
from ucla_agent.core.decide.source import DecisionSource
from ucla_agent.core.decide.text import TextRequest

DEFAULT_NAME = "chain"
_MILLISECONDS_PER_SECOND = 1000
_clock = time.perf_counter

_Request = TypeVar("_Request", ChoiceRequest, TextRequest)
_Call = TypeVar("_Call", SourceCall, TextCall)


class ChainSource(DecisionSource):
    """Asks its sources one after another and stops at the first answer that is used.

    The request's deadline is for the chain as a whole: each source gets the
    time that is left when its turn comes, and once none is left the sources
    after it are not asked. A source that fails quickly therefore costs the
    next one little, and one that uses up the deadline leaves nothing to fall
    back on but the caller's own script. Choices and texts are asked in the
    same way.

    A choice with one option is answered by the chain itself, with that
    option: there is nothing to choose, and a source that is asked one
    anyway may refuse it as an error and so open its circuit for the
    questions that do need it.
    """

    def __init__(self, sources: Sequence[DecisionSource], name: str = DEFAULT_NAME) -> None:
        """`name` is what the match record calls the chain when it holds no source at all."""
        self.name = name
        self._sources = tuple(sources)

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        """The calls made, in order: all of them failed except, when there is one, the last.

        A request with one option makes no call to a source: it ends in one
        call of the chain's own, that option used with confidence 1.0 and the
        reason `single_option`.
        """
        if len(request.options) == 1:
            return [SourceCall(self.name, request.options[0].id, 1.0, 0, USED, SINGLE_OPTION)]
        return self._in_turn(request, lambda source, asked: source.ask(asked))

    def ask_text(self, request: TextRequest) -> list[TextCall]:
        """The calls made for a text, in order: all of them failed except, when there is one, the last."""
        return self._in_turn(request, lambda source, asked: source.ask_text(asked))

    def write_usage_event(self) -> None:
        """Have every source of the chain write what it used in the match."""
        for source in self._sources:
            source.write_usage_event()

    def _in_turn(self, request: _Request, ask: Callable[[DecisionSource, _Request], list[_Call]]) -> list[_Call]:
        """Ask the sources in order, each with the time left of the request's deadline, until one is used."""
        give_up_at = _clock() + request.deadline_ms / _MILLISECONDS_PER_SECOND
        calls: list[_Call] = []
        for source in self._sources:
            milliseconds_left = int((give_up_at - _clock()) * _MILLISECONDS_PER_SECOND)
            if not _may_ask_another(calls, milliseconds_left):
                break
            calls.extend(ask(source, dataclasses.replace(request, deadline_ms=max(0, milliseconds_left))))
        return calls


def _may_ask_another(calls: Sequence[SourceCall | TextCall], milliseconds_left: int) -> bool:
    """Whether the next source gets its turn: no answer was used yet and there is time left.

    The first source is asked whatever the deadline, so that a request to a
    chain that has a source always ends in a call.
    """
    if not calls:
        return True
    return calls[-1].outcome != USED and milliseconds_left > 0
