"""A choice or a short text from a role of the included model server.

For a choice the model writes nothing of its own: it is given the options and
has to answer with the id of one of them, as a JSON object the server
constrains to a schema that lists exactly those ids. The schema ends the
answer as soon as the object closes, so `MAX_TOKENS` only bounds an answer
that would not close: it leaves room for the spaces a model may put around the
key and the colon (`{ "choice" : "P3" }`) and for an id of a few tokens. Option
ids still have to be short (a word such as `HOLD` or a seat such as `P3`): an
id the model needs many tokens for leaves the object unfinished, and an
unfinished object is an error, not a choice.

A source built with `choice_reasons` also asks for a short reason, in the same
object (`{"choice": "HOLD", "reason": "base threatened, army weaker"}`), for
people reading the record. Only the choice is required: a reason that is
missing or empty is none, one that is too long is cut, and neither ever keeps
a valid choice from being used. A request's own `with_reason` decides in place
of `choice_reasons`: one with it False is asked for the choice alone.

A request that asks for its reason first (`reason_first`) is answered the
other way round, `{"reason": "...", "choice": "HOLD"}`, whatever
`choice_reasons` says: the schema lists the reason before the choice and
requires both, so the model weighs the options in a few words before it
commits to one. The reason may take `REASON_CHARS_PER_WORD` characters for
each word the request allows, and the answer the request's
`max_answer_tokens`, or about two tokens a word and room for the object when
it gives none. A reason that is empty is none and one that is too long is cut,
and neither keeps a valid choice from being used.

A request that brings its own system text (`system_text`) is sent with it in
place of the system prompt built here, and with its user message laid out
plainly, a line for each option, as the game wrote its prompt; the schema is
still built here, so its answer keeps the form the source reads. A request's
`answer_tokens` caps the answer whatever else the request says.

For a text the model writes freely, with some variety (`TEXT_TEMPERATURE`),
and is cut off after a few tokens per word of the limit it was asked to keep.

Every call that reaches the model server is recorded with what it sent (the
two messages, the response format and the role's settings) and with the
completion text exactly as it came back, before it is read.
"""

from __future__ import annotations

import json
from functools import partial
from typing import Any, Callable, Sequence

import httpx

from ucla_agent.core.decide import text
from ucla_agent.core.decide.choice import ChoiceRequest
from ucla_agent.core.decide.guard import (
    MALFORMED_ANSWER,
    Answer,
    SourceError,
    SourceGuard,
    ensure_ok,
    holding,
    usage_of,
)
from ucla_agent.core.decide.journal import SourceCall, TextCall
from ucla_agent.core.decide.model_io import Received, Sent
from ucla_agent.core.decide.source import DecisionSource
from ucla_agent.core.decide.text import TextRequest
from ucla_agent.core.modelserver import settings as server_settings
from ucla_agent.core.modelserver.roles import CLIENT_HEADER, CLIENT_NAME, COMPLETIONS_ROUTE
from ucla_agent.core.paths import repository_root
from ucla_agent.core.secrets import BoundClient

SOURCE_NAME_PREFIX = "local:"
# Room for a spaced answer object with an id of a few tokens; a normal answer closes, and so ends, well before it.
MAX_TOKENS = 24
# The same, and a reason of at most MAX_REASON_CHARS characters, which is about 25 tokens of English.
REASON_MAX_TOKENS = 64
TEMPERATURE = 0
CHOICE_KEY = "choice"
REASON_KEY = "reason"
MAX_REASON_CHARS = 100
SYSTEM_PROMPT = (
    "You choose exactly one of the options you are given. "
    'Answer with a JSON object of the form {"choice": "<id of the option>"} and nothing else.'
)
REASON_SYSTEM_PROMPT = (
    "You choose exactly one of the options you are given. "
    'Answer with a JSON object of the form {"choice": "<id of the option>", "reason": "<why, in at most 12 words>"} '
    "and nothing else."
)
# A reason asked for first may take this many characters a word: about five letters and a space, and room for
# longer words.
REASON_CHARS_PER_WORD = 8
# A reason-first answer without a token cap of its own: about two tokens a word of its reason, and room for the
# object's keys, quotes and an id of a few tokens.
REASON_FIRST_TOKENS_PER_WORD = 2
REASON_FIRST_OBJECT_TOKENS = 24
REASON_FIRST_SYSTEM_PROMPT = (
    "You choose exactly one of the options you are given. "
    "First give your reason, in at most {words} words, then your choice. "
    'Answer with a JSON object of the form {{"reason": "<why, in at most {words} words>", '
    '"choice": "<id of the option>"}} and nothing else.'
)
# The names under which an OpenAI-style completion reports the tokens it read and wrote.
PROMPT_TOKENS = "prompt_tokens"
COMPLETION_TOKENS = "completion_tokens"
# Where llama-server puts what a model reasoned, apart from its answer, when the role reasons.
REASONING_CONTENT = "reasoning_content"
ROLE_PARAM = "model_role"


class LocalModelSource(DecisionSource):
    """Asks one role of the included model server to choose or to write; its name is `local:<role>`."""

    def __init__(
        self,
        role: str,
        *,
        base_url: str | None = None,
        reasoning: str | None = None,
        choice_reasons: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """`base_url` is the model server's endpoint and `reasoning` the role's reasoning setting (`off` or `on`),
        which the record names; left out, both are read from `config/models.toml`.

        With `choice_reasons` a choice is asked with a short reason. `transport` replaces the network in tests.
        """
        self.name = f"{SOURCE_NAME_PREFIX}{role}"
        self._role = role
        self._choice_reasons = choice_reasons
        if base_url is None:
            server = server_settings.load(repository_root())
            base_url = server.endpoint_url
            reasoning = reasoning or _reasoning_of(server, role)
        self._reasoning = reasoning
        self._client = BoundClient(base_url, headers={CLIENT_HEADER: CLIENT_NAME}, transport=transport)
        self._guard = SourceGuard(self.name)

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        """One call to the role, held to the request's deadline."""
        with_reason = request.asks_reason_after(self._choice_reasons)
        asks_reason = with_reason or request.reason_first
        body = build_body(self._role, request, with_reason=with_reason)
        sent = self._sent(body, request.deadline_ms, asks_reason=asks_reason)
        read = partial(read_answer, with_reason=asks_reason, max_reason_chars=max_reason_chars(request))
        return [self._guard.call(request, partial(self._exchange, body, read), sent)]

    def ask_text(self, request: TextRequest) -> list[TextCall]:
        """One call to the role for a text, held to the request's deadline and sharing the role's circuit."""
        body = build_text_body(self._role, request)
        sent = self._sent(body, request.deadline_ms)
        return [self._guard.call_text(request, partial(self._exchange, body, read_text), sent)]

    def _exchange(
        self, body: dict[str, Any], read: Callable[[httpx.Response], Answer], http_timeout_seconds: float
    ) -> Answer:
        response = self._client.request("POST", COMPLETIONS_ROUTE, json_body=body, timeout=http_timeout_seconds)

        def read_ok() -> Answer:
            ensure_ok(response)
            return read(response)

        return holding(received_of(response), read_ok)

    def _sent(self, body: dict[str, Any], deadline_ms: int, *, asks_reason: bool = False) -> Sent:
        """What a chat completion request sends to the role: its two messages, its format and its settings."""
        system, user = (message["content"] for message in body["messages"])
        params = {
            ROLE_PARAM: self._role,
            "temperature": body["temperature"],
            "max_tokens": body["max_tokens"],
            "reasoning": self._reasoning,
            "deadline_ms": deadline_ms,
        }
        return Sent(system, user, body.get("response_format"), params, asks_reason)


def _reasoning_of(server: server_settings.ModelServerSettings, role: str) -> str | None:
    """The reasoning setting `config/models.toml` gives the role; None for a role the file does not define."""
    return next((defined.reasoning for defined in server.roles if defined.name == role), None)


def build_body(role: str, request: ChoiceRequest, *, with_reason: bool = False) -> dict[str, Any]:
    """The chat completion request that puts `request` to the role: its reason first when the request asks for it
    first, else with a short reason after the choice `with_reason`."""
    if request.reason_first:
        system = reason_first_system_prompt(request.reason_words)
        max_tokens = reason_first_max_tokens(request)
        schema = reason_first_schema(request)
    else:
        system = system_prompt(with_reason)
        max_tokens = REASON_MAX_TOKENS if with_reason else MAX_TOKENS
        schema = choice_schema(request.option_ids(), with_reason=with_reason)
    return {
        "model": role,
        "messages": [
            {"role": "system", "content": request_system_text(request, system)},
            {"role": "user", "content": choice_prompt(request)},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": answer_cap(request, max_tokens),
        "response_format": {"type": "json_schema", "json_schema": {"name": CHOICE_KEY, "schema": schema}},
    }


def request_system_text(request: ChoiceRequest, built: str) -> str:
    """The system prompt a choice is sent with: the request's own system text when it gives one, else `built`."""
    return built if request.system_text is None else request.system_text


def answer_cap(request: ChoiceRequest, built: int) -> int:
    """The most tokens a choice's answer may take: the request's `answer_tokens` when it gives them, whatever
    `reason_first` says, else `built`."""
    return built if request.answer_tokens is None else request.answer_tokens


def system_prompt(with_reason: bool) -> str:
    """The system prompt of a choice: it names the one key of the answer, or both keys `with_reason`."""
    return REASON_SYSTEM_PROMPT if with_reason else SYSTEM_PROMPT


def reason_first_system_prompt(reason_words: int) -> str:
    """The system prompt of a choice whose reason comes first: a reason of at most `reason_words` words, then the
    choice."""
    return REASON_FIRST_SYSTEM_PROMPT.format(words=reason_words)


def reason_first_schema(request: ChoiceRequest) -> dict[str, Any]:
    """The JSON schema of a reason-first answer: its `reason`, then its `choice` among the option ids, both
    required."""
    properties = {
        REASON_KEY: {"type": "string", "maxLength": max_reason_chars(request)},
        CHOICE_KEY: {"enum": request.option_ids()},
    }
    return {"type": "object", "properties": properties, "required": [REASON_KEY, CHOICE_KEY]}


def reason_first_max_tokens(request: ChoiceRequest) -> int:
    """The tokens a reason-first answer may take: the request's own cap, or two a word of its reason and room for
    the object."""
    if request.max_answer_tokens is not None:
        return request.max_answer_tokens
    return REASON_FIRST_TOKENS_PER_WORD * request.reason_words + REASON_FIRST_OBJECT_TOKENS


def max_reason_chars(request: ChoiceRequest) -> int:
    """The most characters of the reason given for the request's choice: `REASON_CHARS_PER_WORD` a word allowed for
    a reason asked first, `MAX_REASON_CHARS` for one after the choice."""
    return REASON_CHARS_PER_WORD * request.reason_words if request.reason_first else MAX_REASON_CHARS


def choice_schema(option_ids: Sequence[str], *, with_reason: bool = False) -> dict[str, Any]:
    """The JSON schema of an answer: an object whose `choice` is one of the option ids, and `with_reason` that may
    also hold a short `reason`."""
    properties: dict[str, Any] = {CHOICE_KEY: {"enum": list(option_ids)}}
    if with_reason:
        properties[REASON_KEY] = {"type": "string", "maxLength": MAX_REASON_CHARS}
    return {"type": "object", "properties": properties, "required": [CHOICE_KEY]}


def read_answer(
    response: httpx.Response, *, with_reason: bool = False, max_reason_chars: int = MAX_REASON_CHARS
) -> Answer:
    """The choice in a chat completion, and `with_reason` the reason given for it, cut to `max_reason_chars`. Raises
    `SourceError` when the body holds no choice that can be read."""
    try:
        content = response.json()["choices"][0]["message"]["content"]
        found = json.loads(content)
        choice = found[CHOICE_KEY]
    except (ValueError, LookupError, TypeError) as exc:
        raise SourceError(MALFORMED_ANSWER) from exc
    if not isinstance(choice, str):
        raise SourceError(MALFORMED_ANSWER)
    return Answer(choice, model_reason=model_reason_of(found, max_reason_chars) if with_reason else None)


def model_reason_of(found: dict[str, Any], max_chars: int = MAX_REASON_CHARS) -> str | None:
    """The reason an answer object gives for its choice: None when it gives none or an empty one, and cut to
    `max_chars` when it is longer."""
    reason = found.get(REASON_KEY)
    if not isinstance(reason, str) or not reason.strip():
        return None
    return reason.strip()[:max_chars]


def build_text_body(role: str, request: TextRequest) -> dict[str, Any]:
    """The chat completion request that asks the role for a text: free text, so no response format."""
    return {
        "model": role,
        "messages": [
            {"role": "system", "content": text.system_prompt(request)},
            {"role": "user", "content": text.user_prompt(request)},
        ],
        "temperature": text.TEXT_TEMPERATURE,
        "max_tokens": text.max_tokens(request),
    }


def read_text(response: httpx.Response) -> Answer:
    """The text in a chat completion, cleaned, with the usage the server reports. Raises `SourceError` without one."""
    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except (ValueError, LookupError, TypeError) as exc:
        raise SourceError(MALFORMED_ANSWER) from exc
    if not isinstance(content, str):
        raise SourceError(MALFORMED_ANSWER)
    return Answer(text.cleaned(content), usage=usage_of(body.get("usage"), PROMPT_TOKENS, COMPLETION_TOKENS))


def received_of(response: httpx.Response) -> Received:
    """A chat completion as it came back: its message's text, or the whole body when it holds no text, and any
    reasoning the server returned apart from it."""
    message = _message_of(response)
    content = message.get("content")
    reasoning = message.get(REASONING_CONTENT)
    return Received(
        raw_output=content if isinstance(content, str) else response.text,
        reasoning_text=reasoning if isinstance(reasoning, str) else None,
    )


def _message_of(response: httpx.Response) -> dict[str, Any]:
    """The message of a chat completion's first choice; empty when the body holds none."""
    try:
        message = response.json()["choices"][0]["message"]
    except (ValueError, LookupError, TypeError):
        return {}
    return message if isinstance(message, dict) else {}


def choice_prompt(request: ChoiceRequest) -> str:
    """The user message of a choice: the context, then the question and each option's id and description.

    A request with its own system text has written the whole prompt, so its message is laid out plainly: the
    context, the question and a line `id: description` for each option, with no heading or blank line of its own.
    """
    if request.system_text is not None:
        options = "\n".join(f"{option.id}: {option.description}" for option in request.options)
        return f"{request.context}\n{request.question}\n{options}"
    options = "\n".join(f"- {option.id}: {option.description}" for option in request.options)
    return f"{request.context}\n\n{request.question}\nOptions:\n{options}"
