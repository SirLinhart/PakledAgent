"""A choice from a native systemone server, such as the model server's jev9b role.

A systemone server reads a situation as plain text and answers typed questions
with a calibrated probability for each option, in one forward pass. A request
is put to it as one choice question, `action`:

- `state` is the situation alone: the request's state object when it
  carries one (a JSON object of the same facts as its text), else its
  context;
- `instructions` is what is asked: the request's own system text, when it
  brings one, then its question;
- `criteria` maps each option id to its description, so the options are sent
  once, in their own field.

The answer's `answers.action.choice` is the option chosen and
`answers.action.confidence` how sure the model is, from 0 (the probabilities
are even) to 1. The server writes no reason, so a choice comes without one,
and one less confident than `min_confidence` is not used. Everything else
(the deadline, an error status, an answer that cannot be read, an option that
was not offered) is judged by the guard as for every other source.

The record of a call holds the request body as it was sent and the answer's
body as it came back, probabilities and all.
"""

from __future__ import annotations

import json
from functools import partial
from typing import Any

import httpx

from ucla_agent.core.decide.choice import ChoiceRequest
from ucla_agent.core.decide.guard import MALFORMED_ANSWER, Answer, SourceError, SourceGuard, ensure_ok, holding
from ucla_agent.core.decide.journal import SourceCall
from ucla_agent.core.decide.model_io import Received, Sent
from ucla_agent.core.decide.source import DecisionSource
from ucla_agent.core.modelserver.roles import CLIENT_HEADER, CLIENT_NAME
from ucla_agent.core.modelserver.systemone import SYSTEMONE_ROUTE
from ucla_agent.core.secrets import BoundClient

SOURCE_NAME_PREFIX = "systemone:"
QUESTION_ID = "action"
QUESTION_TYPE = "choice"
DEFAULT_MIN_CONFIDENCE = 0.0


class SystemoneSource(DecisionSource):
    """Asks one systemone server to choose; its name is `systemone:<name>`."""

    def __init__(
        self,
        name: str,
        *,
        base_url: str,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """`base_url` is the server's own address. An answer whose confidence is below `min_confidence` is not used.
        `transport` replaces the network in tests."""
        self.name = f"{SOURCE_NAME_PREFIX}{name}"
        self._min_confidence = min_confidence
        self._client = BoundClient(base_url, headers={CLIENT_HEADER: CLIENT_NAME}, transport=transport)
        self._guard = SourceGuard(self.name, min_confidence)

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        """One call to the server, held to the request's deadline."""
        body = build_body(request)
        params = {"source": self.name, "min_confidence": self._min_confidence, "deadline_ms": request.deadline_ms}
        sent = Sent(None, json.dumps(body, ensure_ascii=False), None, params)
        return [self._guard.call(request, partial(self._exchange, body), sent)]

    def _exchange(self, body: dict[str, Any], http_timeout_seconds: float) -> Answer:
        response = self._client.request("POST", SYSTEMONE_ROUTE, json_body=body, timeout=http_timeout_seconds)

        def read_ok() -> Answer:
            ensure_ok(response)
            return read_answer(response)

        return holding(Received(response.text), read_ok)


def instructions_of(request: ChoiceRequest) -> str:
    """What the question asks: the request's own system text, when it gives one, then its question."""
    if request.system_text is None:
        return request.question
    return f"{request.system_text}\n{request.question}"


def build_body(request: ChoiceRequest) -> dict[str, Any]:
    """The systemone request: the state object, else the context, as the state, and one choice question whose criteria
    are the options, in the order the request gives them."""
    question = {
        "type": QUESTION_TYPE,
        "instructions": instructions_of(request),
        "criteria": {option.id: option.description for option in request.options},
    }
    state = request.context if request.state_object is None else request.state_object
    return {"state": state, "questions": {QUESTION_ID: question}}


def read_answer(response: httpx.Response) -> Answer:
    """The chosen option and its confidence. Raises `SourceError` when either cannot be read."""
    try:
        answer = response.json()["answers"][QUESTION_ID]
        choice, confidence = answer["choice"], answer["confidence"]
    except (ValueError, LookupError, TypeError) as exc:
        raise SourceError(MALFORMED_ANSWER) from exc
    if not isinstance(choice, str) or not _is_number(confidence):
        raise SourceError(MALFORMED_ANSWER)
    return Answer(choice, float(confidence))


def _is_number(value: Any) -> bool:
    # bool is a kind of number in Python, and `true` is not a confidence.
    return isinstance(value, (int, float)) and not isinstance(value, bool)
