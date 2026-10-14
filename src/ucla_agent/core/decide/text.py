"""Asking a decision source to write a short text, and what comes back.

Besides choosing among options, a model can be asked to write: a chat
message, a short claim. The answer is always a `TextResult`: a source that
fails, times out, refuses or writes nothing never raises into the game, it
reports an outcome other than `used` and no text, and the game falls back on
its own scripted text.

The source only asks for the word limit; whether the text keeps to it is for
the caller to check.
"""

from __future__ import annotations

from dataclasses import dataclass

# The reason of the `error` call of a source that cannot write text at all; nothing is sent.
TEXT_UNSUPPORTED = "text_unsupported"
DEFAULT_SYSTEM_PROMPT = "You write one short message. Answer with the message text only."
TEXT_TEMPERATURE = 0.7
# A word is one to two tokens in English; three leaves room without letting a model ramble on.
TOKENS_PER_WORD = 3
# The pairs of quotes a model may wrap its whole message in, opening and closing.
QUOTE_PAIRS = (('"', '"'), ("'", "'"), ("“", "”"))


@dataclass(frozen=True)
class TextRequest:
    """One request for a short text.

    `instruction` says what to write and `context` what the source needs to
    know about the situation. `max_words` is the limit the source is asked to
    keep to, `deadline_ms` how long the whole answer may take, and
    `state_tag` names the situation the request was made in. `system`
    replaces the default system prompt when it is given.
    """

    instruction: str
    context: str
    max_words: int
    deadline_ms: int
    state_tag: str
    system: str | None = None


@dataclass(frozen=True)
class TextResult:
    """What became of one text request.

    `text` is the written text when `outcome` is `used` and None for every
    other outcome. `source` names the source that was asked last,
    `latency_ms` is how long it took, `reason` says more about an outcome
    other than `used`, and the token counts are what the model reported, when
    it reported them.
    """

    text: str | None
    source: str
    latency_ms: int
    outcome: str
    reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


def system_prompt(request: TextRequest) -> str:
    """The system prompt of a text request: the request's own, or the default."""
    return request.system or DEFAULT_SYSTEM_PROMPT


def user_prompt(request: TextRequest) -> str:
    """The user message of a text request: the context, then the instruction and the word limit."""
    return f"{request.context}\n\n{request.instruction}\nAt most {request.max_words} words."


def max_tokens(request: TextRequest) -> int:
    """The most tokens a source may write for the request."""
    return request.max_words * TOKENS_PER_WORD


def cleaned(written: str) -> str:
    """A model's text without surrounding whitespace and without one pair of quotes around all of it."""
    text = written.strip()
    for opening, closing in QUOTE_PAIRS:
        if len(text) > 1 and text.startswith(opening) and text.endswith(closing):
            return text[1:-1].strip()
    return text
