"""The settings the Werewolf agent plays by, read from `config/werewolf/tuning.toml`.

The tracked file is the only place these values live: role inference's
evidence weights in `[inference]`, the village's and the wolves' plans of play
in `[village]` and `[wolves]`, and the chat's patterns, limits and scripted
messages in `[chat]` and `[chat.templates]`. A run records the file's hash, so
a setting that is silently ignored or replaced would make the record say
something the agent did not do. The file is therefore read strictly, key by
key, through `core.config_tables.Table`.

Reading the file is the only I/O here and happens in `Tuning.from_file`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from pathlib import Path
from string import Formatter

from ucla_agent.core.config_tables import Table, read_tables, reject_unknown_keys

TUNING_FILE = Path("config") / "werewolf" / "tuning.toml"
INFERENCE_TABLE = "inference"
VILLAGE_TABLE = "village"
WOLVES_TABLE = "wolves"
CHAT_TABLE = "chat"
TEMPLATES_TABLE = "templates"
INJECTION_PATTERNS = "injection_patterns"
KILL_REQUEST_PATTERN = "kill_request_pattern"
SEER_RETRACTION_PATTERN = "seer_retraction_pattern"
# A chat pattern matches whatever the case, and `^` at the start of any line of a message.
PATTERN_FLAGS = re.IGNORECASE | re.MULTILINE
# What an evidence weight must hold. A weight of 0 would rule layouts out, which only a certain fact may do.
MULTIPLIER_KIND = "a number above 0"
SHARE_KIND = "a number from 0 to 1"
# The kill request names the seat in its one group.
KILL_REQUEST_GROUPS = 1
# The placeholders of each scripted message; a template has to use exactly these.
PLAYER, RESULTS, RESULT = "player", "results", "result"
TEMPLATE_FIELDS = {
    "suspect": {PLAYER},
    "seer_reveal": {RESULTS, PLAYER},
    "seer_result": {PLAYER, RESULT},
    "counterclaim": {PLAYER},
    "defense": set(),
    "coordination": {PLAYER},
}


@dataclass(frozen=True)
class InferenceTuning:
    """Role inference's evidence weights: each multiplies the weight of the layouts its evidence names."""

    vote_for_revealed_villager: float
    vote_for_revealed_wolf: float
    seer_claim_if_seer: float
    seer_claim_if_wolf: float
    seer_claim_if_villager: float
    false_result_if_seer: float
    accused_revealed_wolf: float
    injection_attempt: float


@dataclass(frozen=True)
class VillageTuning:
    """The village's plan: how close two vote candidates must be for accusations to order them, when the seer
    reveals itself, and whether the vote spares a lone seer claimant on the day of its claim."""

    close_margin: float
    reveal_when_accused_by: int
    spare_lone_claimant: bool


@dataclass(frozen=True)
class WolvesTuning:
    """The wolves' plan: how an ally's kill request and a retracted seer claim are read, and when the ally is voted."""

    kill_request_pattern: re.Pattern[str]
    seer_retraction_pattern: re.Pattern[str]
    bus_accuser_share: float


@dataclass(frozen=True)
class ChatTemplates:
    """The scripted messages, each a `str.format` template with the placeholders `TEMPLATE_FIELDS` names."""

    suspect: str
    seer_reveal: str
    seer_result: str
    counterclaim: str
    defense: str
    coordination: str


@dataclass(frozen=True)
class ChatTuning:
    """How the chat is read and written.

    `injection_patterns` mark a message as an injection attempt, compiled.
    The rest bounds what the agent sends: the phrases a wolf's public text
    never holds, those the public text of a seer that has not revealed itself
    never holds, the chats per window, the word limit a model is asked for,
    how much of another player's message is quoted to it, how a text
    request's deadline is cut to the window, and the scripted messages.
    `pakled_persona` has a villager or a wolf talk like a Pakled; the seer
    never does.
    """

    injection_patterns: tuple[re.Pattern[str], ...]
    forbidden_public_phrases: tuple[str, ...]
    unrevealed_seer_forbidden_phrases: tuple[str, ...]
    max_chats_per_window: int
    max_words_asked: int
    quote_chars: int
    deadline_margin_ms: int
    min_deadline_ms: int
    pakled_persona: bool
    templates: ChatTemplates


@dataclass(frozen=True)
class Tuning:
    """Every table of the Werewolf tuning file."""

    inference: InferenceTuning
    village: VillageTuning
    wolves: WolvesTuning
    chat: ChatTuning

    @classmethod
    def from_file(cls, root: Path) -> Tuning:
        """The settings held by `config/werewolf/tuning.toml` under `root`.

        Raises `ConfigError`, naming the file, the table and the key, when the
        file is missing or not TOML, has a table or key nobody reads or lacks
        one, holds an evidence weight that is not a number above 0, a share
        outside 0 to 1, a pattern that is not a regular expression, or a
        template whose placeholders are not the ones it is filled with.
        """
        path = root / TUNING_FILE
        tables = read_tables(path)
        reject_unknown_keys(path, "the file", tables, {INFERENCE_TABLE, VILLAGE_TABLE, WOLVES_TABLE, CHAT_TABLE})
        return cls(
            inference=_inference(Table(path, INFERENCE_TABLE, tables.get(INFERENCE_TABLE))),
            village=_village(Table(path, VILLAGE_TABLE, tables.get(VILLAGE_TABLE))),
            wolves=_wolves(Table(path, WOLVES_TABLE, tables.get(WOLVES_TABLE))),
            chat=_chat(Table(path, CHAT_TABLE, tables.get(CHAT_TABLE))),
        )


def _inference(table: Table) -> InferenceTuning:
    """The [inference] table: one evidence weight for each field of `InferenceTuning` and no other key."""
    weights = {weight.name: _multiplier(table, weight.name) for weight in fields(InferenceTuning)}
    table.reject_unread_keys()
    return InferenceTuning(**weights)


def _multiplier(table: Table, key: str) -> float:
    value = table.number(key)
    if value <= 0:
        raise table.error(key, MULTIPLIER_KIND)
    return float(value)


def _village(table: Table) -> VillageTuning:
    """The [village] table: the close margin of the vote, the accusers that make the seer reveal itself, and the
    switch that spares a lone seer claimant."""
    village = VillageTuning(
        close_margin=float(table.number("close_margin")),
        reveal_when_accused_by=table.integer("reveal_when_accused_by", minimum=1),
        spare_lone_claimant=table.boolean("spare_lone_claimant"),
    )
    table.reject_unread_keys()
    return village


def _wolves(table: Table) -> WolvesTuning:
    """The [wolves] table: the kill request and retraction patterns, compiled, and the share that buses the ally."""
    kill_request = _compiled(table, KILL_REQUEST_PATTERN, table.text(KILL_REQUEST_PATTERN))
    if kill_request.groups != KILL_REQUEST_GROUPS:
        raise table.error(KILL_REQUEST_PATTERN, "a regular expression with one group, the seat's number")
    wolves = WolvesTuning(
        kill_request_pattern=kill_request,
        seer_retraction_pattern=_compiled(table, SEER_RETRACTION_PATTERN, table.text(SEER_RETRACTION_PATTERN)),
        bus_accuser_share=_share(table, "bus_accuser_share"),
    )
    table.reject_unread_keys()
    return wolves


def _share(table: Table, key: str) -> float:
    value = table.number(key)
    if value > 1:
        raise table.error(key, SHARE_KIND)
    return float(value)


def _chat(table: Table) -> ChatTuning:
    """The [chat] table and its [chat.templates]: patterns, phrases, limits, the persona switch and the scripted
    messages."""
    patterns = tuple(
        _compiled(table, INJECTION_PATTERNS, text, "a list of regular expressions")
        for text in table.texts(INJECTION_PATTERNS)
    )
    chat = ChatTuning(
        injection_patterns=patterns,
        forbidden_public_phrases=table.texts("forbidden_public_phrases"),
        unrevealed_seer_forbidden_phrases=table.texts("unrevealed_seer_forbidden_phrases"),
        max_chats_per_window=table.integer("max_chats_per_window", minimum=1),
        max_words_asked=table.integer("max_words_asked", minimum=1),
        quote_chars=table.integer("quote_chars", minimum=1),
        deadline_margin_ms=table.integer("deadline_margin_ms", minimum=0),
        min_deadline_ms=table.integer("min_deadline_ms", minimum=1),
        pakled_persona=table.boolean("pakled_persona"),
        templates=_templates(table.subtable(TEMPLATES_TABLE)),
    )
    table.reject_unread_keys()
    return chat


def _templates(table: Table) -> ChatTemplates:
    """The [chat.templates] table: one scripted message for each field of `ChatTemplates`, with its placeholders."""
    templates = {name: _template(table, name, placeholders) for name, placeholders in TEMPLATE_FIELDS.items()}
    table.reject_unread_keys()
    return ChatTemplates(**templates)


def _template(table: Table, key: str, placeholders: set[str]) -> str:
    text = table.text(key)
    try:
        used = {name for _, name, _, _ in Formatter().parse(text) if name is not None}
    except ValueError as exc:
        raise table.error(key, f"a template ({exc})") from exc
    if used != placeholders:
        expected = ", ".join(f"{{{name}}}" for name in sorted(placeholders)) or "no placeholder"
        raise table.error(key, f"a template using exactly {expected}")
    return text


def _compiled(table: Table, key: str, text: str, expected: str = "a regular expression") -> re.Pattern[str]:
    try:
        return re.compile(text, PATTERN_FLAGS)
    except re.error as exc:
        raise table.error(key, f"{expected} ({text!r} is not one: {exc})") from exc
