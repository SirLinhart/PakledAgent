"""The decision settings of `config/agent.toml`, and the chains of sources they name.

Which source is asked for which kind of choice is configuration, not code: a
game asks for a chain by name (`build_chain("red_alert_plan")`) and gets the
sources the tracked file lists for it, in order. An entry is `local:<role>`,
a role of the included model server, `claude:<alias>`, a Claude model of
`[claude.models]`, `systemone:<name>`, a native systemone server, or `jev`,
the Jev service. A systemone server is the model server's systemone role of
that name, on its own port, unless `[systemone.<name>]` gives a loopback
`base_url` of its own; that table may also set `min_confidence`. Jev is only
ever part of a
chain when `[jev].enabled` is true; turned off, its entries are left out and
the rest of the chain stands. A Claude entry stays in its chain whatever
`[claude].enabled` says: turned off, it answers at once without sending
anything, and the record shows that it was skipped. `choice_reasons` has every
local and Claude source of a chain ask for a short reason with each choice.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

import httpx

from ucla_agent.core.config import AGENT_CONFIG_FILE_NAME, CONFIG_DIR_NAME, ConfigError, load_agent_config
from ucla_agent.core.config_tables import Table
from ucla_agent.core.decide import claude, jev, local, systemone
from ucla_agent.core.decide.chain import ChainSource
from ucla_agent.core.decide.claude import ClaudeSettings, ClaudeSource
from ucla_agent.core.decide.jev import JevFields, JevSource
from ucla_agent.core.decide.local import LocalModelSource
from ucla_agent.core.decide.source import DecisionSource
from ucla_agent.core.decide.systemone import SystemoneSource
from ucla_agent.core.modelserver import settings as server_settings
from ucla_agent.core.paths import repository_root

DECIDE_TABLE = "decide"
CHAINS_TABLE = "chains"
JEV_TABLE = "jev"
FIELDS_TABLE = "fields"
JEV_ENTRY = jev.SOURCE_NAME
LOCAL_ENTRY_PREFIX = local.SOURCE_NAME_PREFIX
CLAUDE_ENTRY_PREFIX = claude.SOURCE_NAME_PREFIX
SYSTEMONE_ENTRY_PREFIX = systemone.SOURCE_NAME_PREFIX
SYSTEMONE_TABLE = "systemone"
LOOPBACK_SCHEME = "http"
LOOPBACK_NAME = "localhost"


@dataclass(frozen=True)
class JevSettings:
    """The `[jev]` table: whether the service may be used at all, where it is, and how its answers are read."""

    enabled: bool
    base_url: str
    min_confidence: float
    fields: JevFields


@dataclass(frozen=True)
class SystemoneSettings:
    """One `[systemone.<name>]` table: the server's own base URL, None for the model server's role of that name, and
    the least confidence an answer needs to be used."""

    base_url: str | None = None
    min_confidence: float = systemone.DEFAULT_MIN_CONFIDENCE


@dataclass(frozen=True)
class DecideSettings:
    """The `[decide]`, `[jev]`, `[claude]` and `[systemone]` tables: the default deadline, whether a model's choice
    comes with a short reason, each chain's entries in order, Jev, Claude and the systemone servers."""

    default_deadline_ms: int
    choice_reasons: bool
    chains: dict[str, tuple[str, ...]]
    jev: JevSettings
    claude: ClaudeSettings
    systemone: dict[str, SystemoneSettings] = field(default_factory=dict)


def load(root: Path) -> DecideSettings:
    """Read the decision settings from `config/agent.toml` under `root`. Raises `ConfigError` when they are unusable."""
    path = root / CONFIG_DIR_NAME / AGENT_CONFIG_FILE_NAME
    tables = load_agent_config(root)
    decide = Table(path, DECIDE_TABLE, tables.get(DECIDE_TABLE))
    chains = decide.subtable(CHAINS_TABLE, optional=True)
    settings = DecideSettings(
        default_deadline_ms=decide.integer("default_deadline_ms", minimum=1),
        choice_reasons=decide.boolean("choice_reasons"),
        chains={name: _entries(chains, name) for name in chains.keys()},
        jev=_jev(Table(path, JEV_TABLE, tables.get(JEV_TABLE))),
        claude=claude.read_settings(Table(path, claude.CLAUDE_TABLE, tables.get(claude.CLAUDE_TABLE))),
        systemone=_systemone(Table(path, SYSTEMONE_TABLE, tables.get(SYSTEMONE_TABLE, {}))),
    )
    decide.reject_unread_keys()
    _check_claude_entries(path, settings)
    return settings


def build_chain(name: str, *, transport: httpx.BaseTransport | None = None) -> ChainSource:
    """The chain `[decide.chains]` of the repository root's `config/agent.toml` lists under `name`.

    Raises `ConfigError` when there is no such chain or one of its entries
    names a role `config/models.toml` does not define or a Claude model
    `[claude.models]` does not define. `transport` replaces the network of
    every source in tests.
    """
    root = repository_root()
    settings = load(root)
    if name not in settings.chains:
        path = root / CONFIG_DIR_NAME / AGENT_CONFIG_FILE_NAME
        raise ConfigError(f"{path}: [{DECIDE_TABLE}.{CHAINS_TABLE}] has no chain named {name}.")
    return ChainSource(list(_sources(root, settings, name, transport)), name=name)


def _sources(
    root: Path, settings: DecideSettings, chain: str, transport: httpx.BaseTransport | None
) -> Iterator[DecisionSource]:
    for entry in settings.chains[chain]:
        if entry.startswith(CLAUDE_ENTRY_PREFIX):
            alias = entry.removeprefix(CLAUDE_ENTRY_PREFIX)
            yield ClaudeSource(
                alias, settings=settings.claude, choice_reasons=settings.choice_reasons, transport=transport
            )
        elif entry.startswith(SYSTEMONE_ENTRY_PREFIX):
            yield _systemone_source(root, settings, chain, entry.removeprefix(SYSTEMONE_ENTRY_PREFIX), transport)
        elif entry != JEV_ENTRY:
            yield _local_source(root, chain, entry.removeprefix(LOCAL_ENTRY_PREFIX), settings.choice_reasons, transport)
        elif settings.jev.enabled:
            jev_settings = settings.jev
            yield JevSource(
                jev_settings.base_url,
                min_confidence=jev_settings.min_confidence,
                fields=jev_settings.fields,
                transport=transport,
            )


def _local_source(
    root: Path, chain: str, role: str, choice_reasons: bool, transport: httpx.BaseTransport | None
) -> LocalModelSource:
    """The source for a role, which has to be one the model server's configuration defines."""
    server = server_settings.load(root)
    defined = {settings.name: settings for settings in server.roles}
    if role not in defined:
        raise ConfigError(
            f"{root / CONFIG_DIR_NAME / AGENT_CONFIG_FILE_NAME}: the chain {chain} names the role {role}, "
            f"which {CONFIG_DIR_NAME}/{server_settings.MODELS_CONFIG_FILE_NAME} does not define."
        )
    return LocalModelSource(
        role,
        base_url=server.endpoint_url,
        reasoning=defined[role].reasoning,
        choice_reasons=choice_reasons,
        transport=transport,
    )


def _systemone_source(
    root: Path, settings: DecideSettings, chain: str, name: str, transport: httpx.BaseTransport | None
) -> SystemoneSource:
    """The source for a systemone server: at its own base URL, else the model server's systemone role of the name."""
    own = settings.systemone.get(name, SystemoneSettings())
    base_url = own.base_url
    if base_url is None:
        server = server_settings.load(root)
        role = next((role for role in server.systemone_roles if role.name == name), None)
        if role is None:
            raise ConfigError(
                f"{root / CONFIG_DIR_NAME / AGENT_CONFIG_FILE_NAME}: the chain {chain} names the systemone server "
                f"{name}, which neither [{SYSTEMONE_TABLE}.{name}] gives a base_url for nor "
                f"{CONFIG_DIR_NAME}/{server_settings.MODELS_CONFIG_FILE_NAME} defines as a systemone role."
            )
        base_url = server.systemone_url(role)
    return SystemoneSource(name, base_url=base_url, min_confidence=own.min_confidence, transport=transport)


def _entries(chains: Table, name: str) -> tuple[str, ...]:
    """A chain's entries, each of which has to be `local:<role>`, `claude:<alias>`, `systemone:<name>` or `jev`."""
    entries = chains.texts(name)
    prefixes = (LOCAL_ENTRY_PREFIX, CLAUDE_ENTRY_PREFIX, SYSTEMONE_ENTRY_PREFIX)
    if not all(entry == JEV_ENTRY or _names_one(entry, *prefixes) for entry in entries):
        raise chains.error(
            name,
            f'a list of "{LOCAL_ENTRY_PREFIX}<role>", "{CLAUDE_ENTRY_PREFIX}<alias>", '
            f'"{SYSTEMONE_ENTRY_PREFIX}<name>" and "{JEV_ENTRY}" entries',
        )
    return entries


def _names_one(entry: str, *prefixes: str) -> bool:
    """Whether the entry is one of the prefixes followed by a name."""
    return any(entry.startswith(prefix) and len(entry) > len(prefix) for prefix in prefixes)


def _check_claude_entries(path: Path, settings: DecideSettings) -> None:
    """Raise `ConfigError` for a `claude:<alias>` entry whose alias `[claude.models]` does not define."""
    for chain, entries in settings.chains.items():
        for entry in entries:
            alias = entry.removeprefix(CLAUDE_ENTRY_PREFIX)
            if entry.startswith(CLAUDE_ENTRY_PREFIX) and alias not in settings.claude.models:
                raise ConfigError(
                    f"{path}: the chain {chain} names the Claude model {alias}, "
                    f"which [{claude.CLAUDE_TABLE}.{claude.MODELS_TABLE}] does not define."
                )


def _jev(table: Table) -> JevSettings:
    fields = table.subtable(FIELDS_TABLE, optional=True)
    defaults = JevFields()
    settings = JevSettings(
        enabled=table.boolean("enabled"),
        # The key travels in a header of every request, so the service is only ever reached over TLS.
        base_url=table.https_url("base_url"),
        min_confidence=table.number("min_confidence"),
        fields=JevFields(
            state=fields.text("state", defaults.state),
            output_schema=fields.text("output_schema", defaults.output_schema),
            value=fields.text("value", defaults.value),
            confidence=fields.text("confidence", defaults.confidence),
        ),
    )
    table.reject_unread_keys()
    fields.reject_unread_keys()
    return settings


def _systemone(table: Table) -> dict[str, SystemoneSettings]:
    """The `[systemone.<name>]` tables, by name."""
    return {name: _systemone_server(table.subtable(name)) for name in table.keys()}


def _systemone_server(table: Table) -> SystemoneSettings:
    base_url = table.text("base_url", None)
    min_confidence = table.number("min_confidence", systemone.DEFAULT_MIN_CONFIDENCE)
    table.reject_unread_keys()
    if base_url is not None and not _is_loopback_url(base_url):
        # The server is a local model: an address another machine could answer would send the game's state there.
        raise table.error("base_url", "an http URL of a loopback address, such as http://127.0.0.1:41503")
    if not 0.0 <= min_confidence <= 1.0:
        raise table.error("min_confidence", "a number from 0 to 1")
    return SystemoneSettings(base_url=base_url, min_confidence=min_confidence)


def _is_loopback_url(url: str) -> bool:
    """Whether `url` is an http URL of this machine: a loopback IP address or localhost."""
    parts = urlsplit(url)
    if parts.scheme != LOOPBACK_SCHEME or not parts.hostname:
        return False
    if parts.hostname == LOOPBACK_NAME:
        return True
    try:
        return ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        return False
