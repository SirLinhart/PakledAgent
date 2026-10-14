"""What one call sent to a model and what came back, as the match record keeps it.

A reader of a record has to be able to see exactly what a model was asked and
what it answered: the messages, the answer format and the sampling settings
that went out, and the completion text as it came back, also when it was not
used. A source therefore hands its call's `Sent` to the guard, and the guard
adds what was `Received`; the journal writes both as the `io` object of the
call's `decision` or `text_decision` event.

Request headers are never part of it: a credential travels in a header and
nowhere else, so `io` holds message contents only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ucla_agent.core.secrets import redact

# The most characters any one text of the io object keeps, its cut marker included.
MAX_TEXT_CHARS = 32_000
CUT_MARKER = "…[cut {count} chars]"


@dataclass(frozen=True)
class Sent:
    """What a call sent: the system and user messages verbatim, the answer format and the settings.

    `system` is None for a request without a system message and
    `response_format` None for one that leaves the answer's form free.
    `params` names the model (a role of the model server or a model id) and
    holds the settings the answer was asked under, the deadline among them.
    `asks_reason` is whether the request asked for a short reason besides the
    choice, which the messages and the format show.
    """

    system: str | None
    user: str
    response_format: Any
    params: Mapping[str, Any]
    asks_reason: bool = False


@dataclass(frozen=True)
class Received:
    """What came back: the completion text exactly as returned, and any reasoning returned apart from it.

    Both are None when nothing came back, as for a call that timed out.
    """

    raw_output: str | None = None
    reasoning_text: str | None = None


NOTHING_RECEIVED = Received()


@dataclass(frozen=True)
class ModelIO:
    """One call's messages and answer together."""

    sent: Sent
    received: Received = NOTHING_RECEIVED

    def record(self) -> dict[str, Any]:
        """The `io` object of the call's event, each text cut to `MAX_TEXT_CHARS`."""
        return {
            "system": capped(self.sent.system),
            "user": capped(self.sent.user),
            "response_format": capped(self.sent.response_format),
            "params": dict(self.sent.params),
            "raw_output": capped(self.received.raw_output),
            "reasoning_text": capped(self.received.reasoning_text),
        }


def capped(value: Any) -> Any:
    """A text cut to `MAX_TEXT_CHARS` and ending with the cut marker when it is longer; anything else unchanged.

    A text that is cut is redacted first: the cut could fall inside a secret,
    and the part left of it would no longer be recognized by the redaction
    every record line passes through.
    """
    if not isinstance(value, str) or len(value) <= MAX_TEXT_CHARS:
        return value
    text = redact(value)
    if len(text) <= MAX_TEXT_CHARS:
        return text
    cut = len(text) - MAX_TEXT_CHARS
    # The marker names how many characters it replaces, and its own length depends on that number. Each round can
    # only lengthen the marker by a digit, so a few rounds settle both.
    while True:
        marker = CUT_MARKER.format(count=cut)
        kept = MAX_TEXT_CHARS - len(marker)
        if len(text) - kept == cut:
            return text[:kept] + marker
        cut = len(text) - kept
