"""What game code hands to a decision source, and what it gets back.

A game's script works out which options are open and asks a decision source
to choose one of them. The answer is always a `ChoiceResult`: a source that
fails, times out or answers nonsense never raises into the game, it reports an
outcome other than `used` and no option, and the game applies its own
scripted choice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

USED = "used"
TIMEOUT = "timeout"
ERROR = "error"
INVALID = "invalid"
STALE_DISCARDED = "stale_discarded"
OUTCOMES = (USED, TIMEOUT, ERROR, INVALID, STALE_DISCARDED)
# Reasons of an `error` result for which the source was not called at all.
CIRCUIT_OPEN = "circuit_open"
NO_SOURCE = "no_source"
# The reason of a `used` result no source was asked for: the request offered
# one option, so that option is the answer.
SINGLE_OPTION = "single_option"


@dataclass(frozen=True)
class Option:
    """One thing the source may choose: the id the game acts on and a description for the source to read."""

    id: str
    description: str


@dataclass(frozen=True)
class ChoiceRequest:
    """One question to a decision source.

    `context` is what the source needs to know about the situation.
    `deadline_ms` is how long the whole answer may take. `state_tag` names
    the situation the question was asked in, in the game's own terms, so that
    an answer that arrives after the situation has changed can be told apart.

    With `reason_first` a model writes a reason of at most `reason_words`
    words before it names its choice, so the choice follows from what it has
    just weighed; the answer may take `max_answer_tokens` tokens, or the
    source's own cap for that many words when it is None.

    A game that writes the whole prompt itself gives `system_text`: a model
    source sends it as the system prompt in place of the one it builds, and
    lays the user message out plainly, the context, the question and a line
    `id: description` for each option, so that what the model reads is the
    game's text and nothing besides. `answer_tokens` is the most tokens the
    answer may take, whatever `reason_first` says. Left out, both change
    nothing.

    `with_reason` says whether a model is asked for a short reason after its
    choice, in place of the source's own `choice_reasons` setting: False asks
    for the choice alone, so that an answer of a few tokens is enough; None
    leaves it to the source. A source that is not a model ignores all six.

    `state_object` is the same situation as `context`, as a JSON object, for
    a source that reads a structured state (a systemone server) in place of
    the text; None for a request with the text alone. Every other source
    reads `context`.
    """

    question: str
    options: Sequence[Option]
    context: str
    deadline_ms: int
    state_tag: str
    reason_first: bool = False
    reason_words: int = 30
    max_answer_tokens: int | None = None
    system_text: str | None = None
    answer_tokens: int | None = None
    with_reason: bool | None = None
    state_object: Mapping[str, Any] | None = None

    def option_ids(self) -> list[str]:
        """The ids of the options, in the order they were given."""
        return [option.id for option in self.options]

    def asks_reason_after(self, source_default: bool) -> bool:
        """Whether a model is asked for a reason after its choice: the request's `with_reason` when it gives one, else
        `source_default`, the source's own setting."""
        return source_default if self.with_reason is None else self.with_reason


@dataclass(frozen=True)
class ChoiceResult:
    """What became of one request.

    `option_id` is the chosen option when `outcome` is `used` and None for
    every other outcome. `source` names the source that was asked last,
    `latency_ms` is how long it took, and `reason` says more about an outcome
    other than `used` (for example `circuit_open`). `model_reason` is the
    short reason a model gave for the option it chose, when it was asked for
    one and gave one; like the option, only for an answer that is used. It is
    for people reading the choice, and never changes which option is used.
    """

    option_id: str | None
    confidence: float | None
    source: str
    latency_ms: int
    outcome: str
    reason: str | None = None
    model_reason: str | None = None
