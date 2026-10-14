"""A choice from the Jev decision service.

Jev takes the state of a situation and a schema of the answer it is to give,
and returns a value that fits the schema together with how confident it is.
The service documents the route (`POST /v1/decide`) but not the names of the
fields of the request and the answer. Everything that depends on those names
is therefore in two functions, `build_body` and `read_answer`, and the names
themselves are settings (`JevFields`), so that matching the real service is a
change of configuration and of nothing else.

The key is read from the environment variable `JEV_API_KEY` and goes out only
as the `Authorization` header of a client bound to the service's own base URL.

Jev is asked in one request body rather than in messages, so the record of a
call holds that body, as it was sent, and the body of the service's answer, as
it came back.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import partial
from typing import Any

import httpx

from ucla_agent.core import secrets
from ucla_agent.core.decide.choice import ChoiceRequest
from ucla_agent.core.decide.guard import MALFORMED_ANSWER, Answer, SourceError, SourceGuard, ensure_ok, holding
from ucla_agent.core.decide.journal import SourceCall
from ucla_agent.core.decide.local import choice_schema
from ucla_agent.core.decide.model_io import Received, Sent
from ucla_agent.core.decide.source import DecisionSource
from ucla_agent.core.secrets import BoundClient

SOURCE_NAME = "jev"
DECIDE_ROUTE = "/v1/decide"
API_KEY_ENV = "JEV_API_KEY"
DEFAULT_MIN_CONFIDENCE = 0.55
NO_API_KEY = "no_api_key"
# Separates the keys of a path into a nested answer, as in `value.choice`.
PATH_SEPARATOR = "."


@dataclass(frozen=True)
class JevFields:
    """The names of the fields of Jev's request and answer.

    `state` and `output_schema` are keys of the request body. `value` and
    `confidence` say where the answer holds the chosen option and the
    confidence: a key, or a path of keys joined by dots into nested objects.
    """

    state: str = "state"
    output_schema: str = "output_schema"
    value: str = "value.choice"
    confidence: str = "confidence"


class JevSource(DecisionSource):
    """Asks the Jev service to choose; its name is `jev`."""

    name = SOURCE_NAME

    def __init__(
        self,
        base_url: str,
        *,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        fields: JevFields = JevFields(),
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """An answer whose confidence is below `min_confidence` is not used. `transport` replaces the network in tests."""
        # Loads `.env` when that has not happened yet, and makes the key's value known to the redaction.
        secrets.load_secrets()
        self._api_key = os.environ.get(API_KEY_ENV, "")
        self._fields = fields
        credential = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else None
        self._client = BoundClient(base_url, headers=credential, transport=transport)
        self._guard = SourceGuard(SOURCE_NAME, min_confidence)

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        """One call to the service, held to the request's deadline."""
        body = build_body(request, self._fields)
        # Without a key nothing is sent, so nothing is recorded as sent either.
        sent = _sent(body, self._fields, request.deadline_ms) if self._api_key else None
        return [self._guard.call(request, partial(self._exchange, body), sent)]

    def _exchange(self, body: dict[str, Any], http_timeout_seconds: float) -> Answer:
        if not self._api_key:
            # Without a key the service would only refuse; nothing is sent.
            raise SourceError(NO_API_KEY)
        response = self._client.request("POST", DECIDE_ROUTE, json_body=body, timeout=http_timeout_seconds)

        def read_ok() -> Answer:
            ensure_ok(response)
            return read_answer(response, self._fields)

        return holding(Received(response.text), read_ok)


def _sent(body: dict[str, Any], fields: JevFields, deadline_ms: int) -> Sent:
    """What a decide request sends: its whole body as the JSON text that goes out, and its output schema."""
    return Sent(None, json.dumps(body), body[fields.output_schema], {"deadline_ms": deadline_ms})


def build_body(request: ChoiceRequest, fields: JevFields) -> dict[str, Any]:
    """The body of the decide request: the situation as the state, and a schema that allows only the option ids."""
    state = {
        "question": request.question,
        "context": request.context,
        "options": [{"id": option.id, "description": option.description} for option in request.options],
    }
    return {fields.state: state, fields.output_schema: choice_schema(request.option_ids())}


def read_answer(response: httpx.Response, fields: JevFields) -> Answer:
    """The chosen option and the confidence in Jev's answer. Raises `SourceError` when either cannot be read."""
    try:
        body = response.json()
    except ValueError as exc:
        raise SourceError(MALFORMED_ANSWER) from exc
    value = _at(body, fields.value)
    confidence = _at(body, fields.confidence)
    if not isinstance(value, str) or not _is_number(confidence):
        raise SourceError(MALFORMED_ANSWER)
    return Answer(value, float(confidence))


def _is_number(value: Any) -> bool:
    # bool is a kind of number in Python, and `true` is not a confidence.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _at(body: Any, path: str) -> Any:
    """What the answer holds at a path of keys. Raises `SourceError` when the path leads nowhere."""
    found = body
    for key in path.split(PATH_SEPARATOR):
        if not isinstance(found, dict) or key not in found:
            raise SourceError(MALFORMED_ANSWER)
        found = found[key]
    return found
