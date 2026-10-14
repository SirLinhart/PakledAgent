"""Choices and short texts from Claude, through the Anthropic Messages API.

Claude is a hosted service: a request leaves the machine, is paid for and
needs a key. A Claude source therefore sends nothing while `[claude] enabled`
is false, while no key is set, or once it has used up its tokens for the
match; it answers at once with an `error` whose reason says which, so a chain
goes straight on to its next source. None of these counts as a failure of the
service, so none opens the source's circuit.

The token cap is per source instance, and a game agent builds its chains
once per match, so the count is the match's. A source counts the input and
output tokens of every answer it receives; a call that starts below
`[claude] max_tokens_per_match` is made even if its answer takes the count
past it, and every later call is refused. Besides the events of its calls, a
source writes two of its own: `claude_token_cap` the first time its count
reaches the cap, and `claude_usage`, its totals, when the game agent calls
`write_usage_event` at the end of the match.

The key is `ANTHROPIC_API_KEY`, from the environment or the untracked `.env`.
It goes out only as the `x-api-key` header of a client bound to
`[claude] base_url`, never in a request body, an event or a log line.

A request is one `POST /v1/messages` with a system prompt and one user
message. A choice is asked in the words a local model is asked in and read
from the first JSON object of Claude's reply; a text is asked like a local
model's and cleaned the same way; a source built with `choice_reasons` asks a
choice with a short reason, as a local model is asked, and reads the reason
by the local model's rule, unless the request's own `with_reason` says
otherwise. A request that asks for its reason first is put
in the local model's reason-first words, with the same token cap, and its
answer line names the reason before the choice, whatever `choice_reasons`
says. A request with its own system text and answer tokens is put to Claude
as to a local model: that system text, the plain user message and that cap.
Every request sent is recorded with its two messages, its answer format and
its settings, and with Claude's text as it came back, before it is read.
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Callable

import httpx

from ucla_agent.core import records, secrets
from ucla_agent.core.config import AGENT_CONFIG_FILE_NAME, CONFIG_DIR_NAME, ConfigError, load_agent_config
from ucla_agent.core.config_tables import Table
from ucla_agent.core.decide import local, text
from ucla_agent.core.decide.choice import ERROR, ChoiceRequest
from ucla_agent.core.decide.guard import (
    MALFORMED_ANSWER,
    Answer,
    SourceError,
    SourceGuard,
    UnusableAnswer,
    Usage,
    ensure_ok,
    holding,
    usage_of,
)
from ucla_agent.core.decide.journal import SourceCall, TextCall
from ucla_agent.core.decide.model_io import Received, Sent
from ucla_agent.core.decide.source import DecisionSource
from ucla_agent.core.decide.text import TextRequest
from ucla_agent.core.paths import repository_root
from ucla_agent.core.secrets import BoundClient

SOURCE_NAME_PREFIX = "claude:"
CLAUDE_TABLE = "claude"
MODELS_TABLE = "models"
MESSAGES_ROUTE = "/v1/messages"
API_KEY_ENV = "ANTHROPIC_API_KEY"
API_KEY_HEADER = "x-api-key"
VERSION_HEADER = "anthropic-version"
CHOICE_MAX_TOKENS = 16
# The same, and a reason of at most local.MAX_REASON_CHARS characters, which is about 25 tokens of English.
CHOICE_REASON_MAX_TOKENS = 56
CHOICE_TEMPERATURE = 0
CHOICE_ANSWER_FORMAT = 'Answer with {"choice": "<id>"} only.'
CHOICE_REASON_ANSWER_FORMAT = 'Answer with {"choice": "<id>", "reason": "<at most 12 words>"} only.'
REASON_FIRST_ANSWER_FORMAT = 'Answer with {{"reason": "<at most {words} words>", "choice": "<id>"}} only.'
# The names under which the Messages API reports the tokens Claude read and wrote.
INPUT_TOKENS = "input_tokens"
OUTPUT_TOKENS = "output_tokens"
TEXT_BLOCK = "text"
THINKING_BLOCK = "thinking"
REFUSAL_STOP_REASON = "refusal"
# Claude is asked without extended thinking, so it reasons only in the text of its answer, if at all.
REASONING = "off"
MODEL_PARAM = "model_id"
# 429 is the API's rate limit and 529 its overload: both say "not now", whatever was asked.
RATE_LIMITED_STATUSES = frozenset({429, 529})

# Reasons a Claude call did not end in a used answer, besides those every source shares.
DISABLED = "disabled"
NO_KEY = "no_key"
TOKEN_CAP = "token_cap"
RATE_LIMITED = "rate_limited"
REFUSED = "refusal"
TOKEN_CAP_KIND = "claude_token_cap"
USAGE_KIND = "claude_usage"


@dataclass(frozen=True)
class ClaudeSettings:
    """The `[claude]` table: whether Claude may be asked at all, where, in which API version, and its models.

    `models` maps each alias a chain may name to the model id sent to the
    API. `max_tokens_per_match` is how many tokens one source may count in a
    match before it stops asking.
    """

    enabled: bool
    base_url: str
    api_version: str
    max_tokens_per_match: int
    models: dict[str, str]


def read_settings(table: Table) -> ClaudeSettings:
    """The `[claude]` table and its `[claude.models]`. Raises `ConfigError` when either is unusable."""
    models = table.subtable(MODELS_TABLE)
    settings = ClaudeSettings(
        enabled=table.boolean("enabled"),
        base_url=table.https_url("base_url"),
        api_version=table.text("api_version"),
        max_tokens_per_match=table.integer("max_tokens_per_match", minimum=1),
        models={alias: models.text(alias) for alias in models.keys()},
    )
    table.reject_unread_keys()
    return settings


def load_settings(root: Path) -> ClaudeSettings:
    """The Claude settings of `config/agent.toml` under `root`."""
    path = root / CONFIG_DIR_NAME / AGENT_CONFIG_FILE_NAME
    return read_settings(Table(path, CLAUDE_TABLE, load_agent_config(root).get(CLAUDE_TABLE)))


class ClaudeSource(DecisionSource):
    """Asks one Claude model, named by its alias in `[claude.models]`, to choose or to write.

    Its name is `claude:<alias>`. Every call it makes carries the model id
    and the tokens the API reported into the record. An instance counts its
    tokens for one match: build a new one, through a new chain, for the next.
    """

    def __init__(
        self,
        model_alias: str,
        *,
        settings: ClaudeSettings | None = None,
        choice_reasons: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """`settings` are the `[claude]` table's; left out, they are read from `config/agent.toml`.

        Raises `ConfigError` for an alias `[claude.models]` does not define.
        With `choice_reasons` a choice is asked with a short reason.
        `transport` replaces the network in tests.
        """
        if settings is None:
            settings = load_settings(repository_root())
        if model_alias not in settings.models:
            raise ConfigError(
                f"{CONFIG_DIR_NAME}/{AGENT_CONFIG_FILE_NAME}: [{CLAUDE_TABLE}.{MODELS_TABLE}] "
                f"has no model named {model_alias}."
            )
        self.name = f"{SOURCE_NAME_PREFIX}{model_alias}"
        self._model_id = settings.models[model_alias]
        self._choice_reasons = choice_reasons
        self._enabled = settings.enabled
        # Loads `.env` when that has not happened yet, and makes the key's value known to the redaction.
        secrets.load_secrets()
        api_key = os.environ.get(API_KEY_ENV, "")
        self._has_key = bool(api_key)
        headers = {VERSION_HEADER: settings.api_version}
        if api_key:
            headers[API_KEY_HEADER] = api_key
        self._client = BoundClient(settings.base_url, headers=headers, transport=transport)
        self._guard = SourceGuard(self.name)
        self._meter = TokenMeter(self.name, self._model_id, settings.max_tokens_per_match)

    def write_usage_event(self) -> None:
        """Write the `claude_usage` event of the match: the tokens counted and the requests sent, once."""
        self._meter.write_usage_event()

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        """One call to Claude for a choice, held to the request's deadline; none while Claude may not be asked."""
        reason = self._reason_not_to_ask()
        if reason is not None:
            return [SourceCall(self.name, None, None, 0, ERROR, reason, model=self._model_id)]
        with_reason = request.asks_reason_after(self._choice_reasons)
        asks_reason = with_reason or request.reason_first
        body = choice_body(self._model_id, request, with_reason=with_reason)
        sent = _sent(body, answer_format(request, with_reason), request.deadline_ms, asks_reason=asks_reason)
        read = partial(_choice_of, with_reason=asks_reason, max_reason_chars=local.max_reason_chars(request))
        call = self._guard.call(request, partial(self._exchange, body, read), sent)
        return [dataclasses.replace(call, model=self._model_id)]

    def ask_text(self, request: TextRequest) -> list[TextCall]:
        """One call to Claude for a text, held to the request's deadline; none while Claude may not be asked."""
        reason = self._reason_not_to_ask()
        if reason is not None:
            return [TextCall(self.name, None, 0, ERROR, reason, model=self._model_id)]
        body = text_body(self._model_id, request)
        sent = _sent(body, None, request.deadline_ms)
        call = self._guard.call_text(request, partial(self._exchange, body, _text_of_reply), sent)
        return [dataclasses.replace(call, model=self._model_id)]

    def _reason_not_to_ask(self) -> str | None:
        """Why no request may be sent at all right now, or None when one may."""
        if not self._enabled:
            return DISABLED
        if not self._has_key:
            return NO_KEY
        if self._meter.is_capped():
            return TOKEN_CAP
        return None

    def _exchange(self, body: dict[str, Any], read: Callable[[Reply], Answer], http_timeout_seconds: float) -> Answer:
        self._meter.note_call()
        response = self._client.request("POST", MESSAGES_ROUTE, json_body=body, timeout=http_timeout_seconds)

        def counted_and_read() -> Answer:
            reply = read_reply(response)
            # Counted before the reply is judged: a refusal or an unreadable answer has used its tokens all the same.
            self._meter.note_usage(reply.usage)
            return read(reply)

        return holding(received_of(response), counted_and_read)


class TokenMeter:
    """What one Claude source has used in its match: the tokens it read and wrote, and the requests it sent.

    Safe to use from several threads: each call counts on its own helper thread.
    """

    def __init__(self, source: str, model_id: str, cap: int) -> None:
        self._source = source
        self._model_id = model_id
        self._cap = cap
        self._lock = threading.Lock()
        self._input_tokens = 0
        self._output_tokens = 0
        self._calls = 0
        self._cap_written = False
        self._usage_written = False

    def is_capped(self) -> bool:
        """Whether the tokens counted have reached the cap."""
        with self._lock:
            return self._input_tokens + self._output_tokens >= self._cap

    def note_call(self) -> None:
        """Count one request sent."""
        with self._lock:
            self._calls += 1

    def note_usage(self, usage: Usage) -> None:
        """Count an answer's tokens, and write `claude_token_cap` the first time the count reaches the cap."""
        with self._lock:
            self._input_tokens += usage.input_tokens or 0
            self._output_tokens += usage.output_tokens or 0
            count = self._input_tokens + self._output_tokens
            reached_now = count >= self._cap and not self._cap_written
            self._cap_written |= reached_now
        if reached_now:
            records.log(
                TOKEN_CAP_KIND, {"source": self._source, "model": self._model_id, "count": count, "cap": self._cap}
            )

    def write_usage_event(self) -> None:
        """Write the `claude_usage` event with the totals; a second call writes nothing."""
        with self._lock:
            if self._usage_written:
                return
            self._usage_written = True
            usage = {
                "source": self._source,
                "model": self._model_id,
                "input_tokens": self._input_tokens,
                "output_tokens": self._output_tokens,
                "calls": self._calls,
            }
        records.log(USAGE_KIND, usage)


@dataclass(frozen=True)
class Reply:
    """A Messages API answer: all its text blocks together (None when its content cannot be read), why
    Claude stopped, and the tokens it counted."""

    text: str | None
    stop_reason: Any
    usage: Usage


def choice_body(model_id: str, request: ChoiceRequest, *, with_reason: bool = False) -> dict[str, Any]:
    """The Messages API request that asks for a choice in the words a local model is asked in: its reason first
    when the request asks for it first, else with a short reason after it `with_reason`."""
    if request.reason_first:
        system = local.reason_first_system_prompt(request.reason_words)
        max_tokens = local.reason_first_max_tokens(request)
    else:
        system = local.system_prompt(with_reason)
        max_tokens = CHOICE_REASON_MAX_TOKENS if with_reason else CHOICE_MAX_TOKENS
    return {
        "model": model_id,
        "system": local.request_system_text(request, system),
        "messages": [
            {"role": "user", "content": f"{local.choice_prompt(request)}\n{answer_format(request, with_reason)}"}
        ],
        "max_tokens": local.answer_cap(request, max_tokens),
        "temperature": CHOICE_TEMPERATURE,
    }


def answer_format(request: ChoiceRequest, with_reason: bool) -> str:
    """The line that ends the request's user message: the reason before the choice when the request asks for it
    first, else the choice with a reason `with_reason` or alone."""
    if request.reason_first:
        return REASON_FIRST_ANSWER_FORMAT.format(words=request.reason_words)
    return choice_answer_format(with_reason)


def choice_answer_format(with_reason: bool) -> str:
    """The line that ends a choice's user message: the form of the answer, with a reason or without."""
    return CHOICE_REASON_ANSWER_FORMAT if with_reason else CHOICE_ANSWER_FORMAT


def _sent(body: dict[str, Any], answer_format: str | None, deadline_ms: int, *, asks_reason: bool = False) -> Sent:
    """What a Messages API request sends: its system prompt, its one user message, the answer format that message
    ends with (None for a text) and its settings. Its headers, the key among them, are not part of it."""
    (message,) = body["messages"]
    params = {
        MODEL_PARAM: body["model"],
        "temperature": body["temperature"],
        "max_tokens": body["max_tokens"],
        "reasoning": REASONING,
        "deadline_ms": deadline_ms,
    }
    return Sent(body["system"], message["content"], answer_format, params, asks_reason)


def text_body(model_id: str, request: TextRequest) -> dict[str, Any]:
    """The Messages API request that asks for a text, with the prompts a local model is given."""
    return {
        "model": model_id,
        "system": text.system_prompt(request),
        "messages": [{"role": "user", "content": text.user_prompt(request)}],
        "max_tokens": text.max_tokens(request),
        "temperature": text.TEXT_TEMPERATURE,
    }


def read_reply(response: httpx.Response) -> Reply:
    """What a Messages API answer holds. Raises `SourceError` for a refused request or a body that is no answer."""
    if response.status_code in RATE_LIMITED_STATUSES:
        raise SourceError(RATE_LIMITED)
    ensure_ok(response)
    try:
        body = response.json()
    except ValueError as exc:
        raise SourceError(MALFORMED_ANSWER) from exc
    if not isinstance(body, dict):
        raise SourceError(MALFORMED_ANSWER)
    usage = usage_of(body.get("usage"), INPUT_TOKENS, OUTPUT_TOKENS)
    return Reply(_text_of(body.get("content")), body.get("stop_reason"), usage)


def written_text(reply: Reply) -> str:
    """What Claude wrote. Raises `UnusableAnswer` when it refused and `SourceError` when there is nothing to read."""
    if reply.stop_reason == REFUSAL_STOP_REASON:
        raise UnusableAnswer(REFUSED, reply.usage)
    if reply.text is None:
        raise SourceError(MALFORMED_ANSWER, reply.usage)
    return reply.text


def _choice_of(reply: Reply, *, with_reason: bool = False, max_reason_chars: int = local.MAX_REASON_CHARS) -> Answer:
    """The choice in a reply, and `with_reason` its reason cut to `max_reason_chars`. Raises `SourceError` when there
    is no choice."""
    return read_choice(written_text(reply), reply.usage, with_reason=with_reason, max_reason_chars=max_reason_chars)


def _text_of_reply(reply: Reply) -> Answer:
    """The text of a reply, cleaned. Raises `SourceError` when there is none."""
    return Answer(text.cleaned(written_text(reply)), usage=reply.usage)


def received_of(response: httpx.Response) -> Received:
    """A Messages API answer as it came back: its text blocks joined, or the whole body when it holds none, and its
    thinking blocks joined."""
    try:
        body = response.json()
    except ValueError:
        return Received(response.text)
    content = body.get("content") if isinstance(body, dict) else None
    written = _text_of(content)
    return Received(response.text if written is None else written, _thinking_of(content))


def read_choice(
    written: str, usage: Usage, *, with_reason: bool = False, max_reason_chars: int = local.MAX_REASON_CHARS
) -> Answer:
    """The choice in the first JSON object of what Claude wrote, and `with_reason` the reason that object gives, cut
    to `max_reason_chars`. Raises `SourceError` when there is no choice."""
    found = first_json_object(written)
    choice = None if found is None else found.get(local.CHOICE_KEY)
    if not isinstance(choice, str):
        raise SourceError(MALFORMED_ANSWER, usage)
    reason = local.model_reason_of(found, max_reason_chars) if with_reason else None
    return Answer(choice, usage=usage, model_reason=reason)


def first_json_object(written: str) -> dict[str, Any] | None:
    """The first JSON object in a text, wherever it starts; None when the text holds none."""
    decoder = json.JSONDecoder()
    start = written.find("{")
    while start != -1:
        try:
            found, _ = decoder.raw_decode(written, start)
        except ValueError:
            start = written.find("{", start + 1)
        else:
            return found
    return None


def _text_of(content: Any) -> str | None:
    """The text blocks of an answer's content, joined; None when the content is not a list of blocks."""
    if not isinstance(content, list):
        return None
    return "".join(block["text"] for block in content if _is_text_block(block))


def _is_text_block(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == TEXT_BLOCK and isinstance(block.get("text"), str)


def _thinking_of(content: Any) -> str | None:
    """The thinking blocks of an answer's content, joined; None when there are none."""
    if not isinstance(content, list):
        return None
    thoughts = [block[THINKING_BLOCK] for block in content if _is_thinking_block(block)]
    return "".join(thoughts) if thoughts else None


def _is_thinking_block(block: Any) -> bool:
    return (
        isinstance(block, dict) and block.get("type") == THINKING_BLOCK and isinstance(block.get(THINKING_BLOCK), str)
    )
