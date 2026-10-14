"""What the match record keeps about a request to a decision source.

Every call to a source is written as an event, whatever came of it: a
`decision` event for a choice and a `text_decision` event for a text. Every
call whose answer was not used is followed by a `fallback` event that names
what decided instead. Together they let a reader of the record see which
choices and texts a model made, which it was asked for and did not make, and
why. Contexts and texts are redacted on the way into the record, like every
other line of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence, TypeVar

from ucla_agent.core import records
from ucla_agent.core.decide.choice import CIRCUIT_OPEN, ERROR, NO_SOURCE, USED, ChoiceRequest, ChoiceResult
from ucla_agent.core.decide.model_io import ModelIO
from ucla_agent.core.decide.text import TextRequest, TextResult

# What decides when no source gave an answer that was used: the game's own script.
SCRIPT = "script"
DECISION_KIND = "decision"
TEXT_DECISION_KIND = "text_decision"
FALLBACK_KIND = "fallback"


@dataclass(frozen=True)
class SourceCall:
    """One call to one source for a choice and what came of it.

    `answer` is what the source said, and is kept also when it was not used
    (an answer that is not one of the options, or one that arrived too late),
    so the record shows it, and so is `model_reason`, the short reason the
    model gave for it. `model` names the model that answered, for a source
    that reports it together with the tokens it counted. `io` is what the
    call sent to the model and what came back; None when nothing was sent.
    """

    source: str
    answer: str | None
    confidence: float | None
    latency_ms: int
    outcome: str
    reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None
    io: ModelIO | None = None
    model_reason: str | None = None

    def result(self) -> ChoiceResult:
        """The call as the game sees it: the answer and its reason are passed on only when the answer was used."""
        is_used = self.outcome == USED
        return ChoiceResult(
            option_id=self.answer if is_used else None,
            confidence=self.confidence,
            source=self.source,
            latency_ms=self.latency_ms,
            outcome=self.outcome,
            reason=self.reason,
            model_reason=self.model_reason if is_used else None,
        )


@dataclass(frozen=True)
class TextCall:
    """One call to one source for a text and what came of it; `text` is what the source wrote, if anything.

    `io` is what the call sent to the model and what came back; None when nothing was sent.
    """

    source: str
    text: str | None
    latency_ms: int
    outcome: str
    reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None
    io: ModelIO | None = None

    def result(self) -> TextResult:
        """The call as the game sees it: the text is passed on only when it was used."""
        return TextResult(
            text=self.text if self.outcome == USED else None,
            source=self.source,
            latency_ms=self.latency_ms,
            outcome=self.outcome,
            reason=self.reason,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )


_Call = TypeVar("_Call", SourceCall, TextCall)


def result_of(calls: Sequence[SourceCall], asked: str) -> ChoiceResult:
    """What a request ended in: its last call, or an error naming `asked` when there was no source to call."""
    if not calls:
        return ChoiceResult(None, None, asked, 0, ERROR, NO_SOURCE)
    return calls[-1].result()


def text_result_of(calls: Sequence[TextCall], asked: str) -> TextResult:
    """What a text request ended in: its last call, or an error naming `asked` when there was no source to call."""
    if not calls:
        return TextResult(None, asked, 0, ERROR, NO_SOURCE)
    return calls[-1].result()


def record(request: ChoiceRequest, calls: Sequence[SourceCall], asked: str) -> None:
    """Write the events of one request: the calls made for it, in order, for the source named `asked`.

    Does nothing before a match record is open, like every other logging call.
    """
    summary = request_summary(request)
    _record_calls(DECISION_KIND, calls, asked, lambda call: _decision_data(request, summary, call))


def record_text(request: TextRequest, calls: Sequence[TextCall], asked: str) -> None:
    """Write the events of one text request: a `text_decision` per call, in order, and its fallbacks."""
    _record_calls(TEXT_DECISION_KIND, calls, asked, lambda call: _text_decision_data(request, call))


def request_summary(request: ChoiceRequest) -> str:
    """The question and the ids of the options, as the record names a request."""
    return f"{request.question} [{', '.join(request.option_ids())}]"


def _record_calls(kind: str, calls: Sequence[_Call], asked: str, data_of: Callable[[_Call], dict[str, object]]) -> None:
    """Write one event of `kind` per call, each call that was not used followed by its `fallback` event."""
    if not calls:
        records.log(FALLBACK_KIND, {"intended_source": asked, "reason": NO_SOURCE, "used": SCRIPT})
    for position, call in enumerate(calls):
        records.log(kind, data_of(call))
        if call.outcome != USED:
            decided_instead = _first_used(calls[position + 1 :])
            records.log(
                FALLBACK_KIND,
                {"intended_source": call.source, "reason": _fallback_reason(call), "used": decided_instead},
            )


def _decision_data(request: ChoiceRequest, summary: str, call: SourceCall) -> dict[str, object]:
    data: dict[str, Any] = {
        # The record's name for the situation a decision belongs to; a game's state tag is its version of it.
        "state_version": request.state_tag,
        "source": call.source,
        "request_summary": summary,
        "answer": call.answer,
        "latency_ms": call.latency_ms,
        "outcome": call.outcome,
        "confidence": call.confidence,
        "reason": call.reason,
    }
    if call.model is not None:
        data.update(model=call.model, input_tokens=call.input_tokens, output_tokens=call.output_tokens)
    if call.io is not None and call.io.sent.asks_reason:
        # Beside `reason`, which says why an answer was not used: the model's own reason for its choice, if any.
        data["model_reason"] = call.model_reason
    _add_io(data, call)
    return data


def _text_decision_data(request: TextRequest, call: TextCall) -> dict[str, object]:
    data: dict[str, Any] = {
        "state_tag": request.state_tag,
        "source": call.source,
        "instruction": request.instruction,
        "context": request.context,
        "text": call.text if call.outcome == USED else None,
        "latency_ms": call.latency_ms,
        "outcome": call.outcome,
        "reason": call.reason,
        "input_tokens": call.input_tokens,
        "output_tokens": call.output_tokens,
    }
    if call.model is not None:
        data["model"] = call.model
    _add_io(data, call)
    return data


def _add_io(data: dict[str, Any], call: SourceCall | TextCall) -> None:
    """Add what the call sent to the model and what came back, when it sent anything at all."""
    if call.io is not None:
        data["io"] = call.io.record()


def _first_used(later_calls: Sequence[SourceCall | TextCall]) -> str:
    """The source of the first later call whose answer was used, or the script when there is none."""
    return next((call.source for call in later_calls if call.outcome == USED), SCRIPT)


def _fallback_reason(call: SourceCall | TextCall) -> str:
    """Why a call's answer was not used: its outcome, or that the source was not even asked."""
    return CIRCUIT_OPEN if call.reason == CIRCUIT_OPEN else call.outcome
