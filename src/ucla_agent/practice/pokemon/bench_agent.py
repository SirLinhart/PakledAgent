"""Who plays our seat in the Pokémon practice benchmark: `PokemonAgent`, in one of four arms.

- `off`: the tracked settings with every model switch off, so every draft
  pick, lineup and turn is the script's best and the run needs no model
  server;
- `on`: the tracked settings with `model_choices` on and `model_briefing`
  and `model_decides` off, so the `pokemon_choice` chain of
  `config/agent.toml` chooses among the script's best few, ranked and
  scored, reading the one-line summary;
- `brief`: `model_choices` and `model_briefing` on and `model_decides` off,
  so the chain reads the full briefing and the same ranked few;
- `decide`: `model_choices`, `model_briefing` and `model_decides` all on, so
  the chain reads the full briefing and is offered the script's wider
  shortlist unscored and shuffled, writing its reason first.

Each model arm sets `model_briefing` and `model_decides` itself, whatever the
tracked switches say, so an arm measures the same thing whichever values
ship. Likewise every arm plays with `[maximin] enabled` of the tuning set as
the run asks (off unless it asks for maximin), whatever the tracked file
says; the opponents are not touched. The model arms need the local roles the chain names (`strong` as
tracked). The settings are read once, when the seat is made, so every match
of a run plays with the same ones. The benchmark never asks a hosted model:
a model arm is refused while the chain names a Claude entry with
`[claude] enabled` true or the Jev entry with `[jev] enabled` true, since
those entries would then be asked.

The live agent may be playing on the same machine, with its model server on
the ports of `config/models.toml`. A run therefore plays inside
`model_server_ports`, which moves this process's model server, its health
check and its model clients to ports of the run's own (11600 for the
endpoint and 41600 up for the `llama-server`s, by default) and refuses any
that would meet the live agent's.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from ucla_agent.core.config import ConfigError
from ucla_agent.core.decide import settings as decide_settings
from ucla_agent.core.modelserver import settings as server_settings
from ucla_agent.games.pokemon.agent import PokemonAgent
from ucla_agent.games.pokemon.settings import CHOICE_CHAIN, PokemonSettings
from ucla_agent.practice.pokemon.opponents import models_off

AGENT_NAME = "PokemonAgent"
MODELS_OFF, MODELS_ON, MODELS_BRIEF, MODELS_DECIDE = "off", "on", "brief", "decide"
ARMS = (MODELS_OFF, MODELS_ON, MODELS_BRIEF, MODELS_DECIDE)
# The switches each model arm plays with besides `model_choices`, overriding the tracked ones.
MODEL_ARM_SWITCHES = {
    MODELS_ON: {"model_briefing": False, "model_decides": False},
    MODELS_BRIEF: {"model_briefing": True, "model_decides": False},
    MODELS_DECIDE: {"model_briefing": True, "model_decides": True},
}
# A hundred above the tracked endpoint port and backend port base, so a run's server sits apart from the live one.
BENCH_MODEL_PORT = 11600
BENCH_BACKEND_PORT_BASE = 41600


class HostedModelRefused(Exception):
    """A model arm would ask a hosted model as the configuration stands; the message says which entries and why."""


@dataclass(frozen=True)
class BenchSeat:
    """Our seat for a run: the factory of its decision-maker, the settings the summary records and the model server
    roles it needs."""

    factory: Callable[[], PokemonAgent]
    settings: dict[str, Any]
    model_roles: frozenset[str]


def make_seat(arm: str, root: Path, *, maximin: bool = False) -> BenchSeat:
    """Our seat in `arm` (one of `ARMS`) with the settings of the tracked files under `root`, `[maximin] enabled`
    set to `maximin`.

    Raises `ConfigError` for an unusable configuration and
    `HostedModelRefused` for a model arm while the chain would ask a hosted
    model.
    """
    tracked = _with_maximin(PokemonSettings.from_files(root), maximin)
    if arm == MODELS_OFF:
        played = models_off(tracked)
        return BenchSeat(lambda: PokemonAgent(settings=played), _described(played), frozenset())
    roles = _local_roles(root)
    played = dataclasses.replace(tracked, model_choices=True, **MODEL_ARM_SWITCHES[arm])
    return BenchSeat(lambda: PokemonAgent(settings=played), _described(played), roles)


def _with_maximin(settings: PokemonSettings, enabled: bool) -> PokemonSettings:
    maximin = dataclasses.replace(settings.tuning.maximin, enabled=enabled)
    return dataclasses.replace(settings, tuning=dataclasses.replace(settings.tuning, maximin=maximin))


def _described(settings: PokemonSettings) -> dict[str, Any]:
    """What the summary records of our seat's settings: the model switches, maximin, the candidate counts and the
    deadlines."""
    return {
        "decision_maker": AGENT_NAME,
        "model_choices": settings.model_choices,
        "model_briefing": settings.model_briefing,
        "model_decides": settings.model_decides,
        "maximin": settings.tuning.maximin.enabled,
        "top_k": settings.top_k,
        "decide_top_k": settings.decide_top_k,
        "draft_deadline_ms": settings.draft_deadline_ms,
        "preview_deadline_ms": settings.preview_deadline_ms,
        "turn_deadline_ms": settings.turn_deadline_ms,
    }


def _local_roles(root: Path) -> frozenset[str]:
    """The model server roles the choice chain names; `HostedModelRefused` when it would also ask a hosted model."""
    decide = decide_settings.load(root)
    entries = decide.chains.get(CHOICE_CHAIN, ())
    hosted = [entry for entry in entries if _asks_a_hosted_model(entry, decide)]
    if hosted:
        raise HostedModelRefused(
            f"the {CHOICE_CHAIN} chain of config/agent.toml names {', '.join(hosted)} while its service is "
            "enabled, and the practice benchmark never asks a hosted model: switch it off for the run"
        )
    prefix = decide_settings.LOCAL_ENTRY_PREFIX
    return frozenset(entry.removeprefix(prefix) for entry in entries if entry.startswith(prefix))


@contextlib.contextmanager
def model_server_ports(port: int, backend_port_base: int, root: Path) -> Iterator[str]:
    """Have this process's model server, its health check and its model clients use the endpoint `port` and the
    backend ports from `backend_port_base` while the run is played; yields the run's endpoint URL.

    Raises `ConfigError`, before anything is changed, when one of the run's ports is one `config/models.toml` under
    `root` gives the live agent (its endpoint port and one backend port per role), when the run's endpoint port is
    one of its own backend ports, or when its backend ports pass the last port. The variables are restored after.
    """
    tracked = server_settings.load_tracked(root)
    roles = len(tracked.roles)
    live = {tracked.port, *range(tracked.backend_port_base, tracked.backend_port_base + roles)}
    backends = set(range(backend_port_base, backend_port_base + roles))
    clashes = sorted((({port} | backends) & live) | ({port} & backends))
    if clashes:
        raise ConfigError(
            f"The benchmark's model server would use port {', '.join(map(str, clashes))}, which the live agent's "
            f"server (port {tracked.port}, backends from {tracked.backend_port_base}) or the run's own endpoint "
            "already uses: give --model-port and --model-backend-port-base ports apart from both."
        )
    if backend_port_base + roles - 1 > server_settings.MAX_PORT:
        raise ConfigError(f"The run's {roles} backend ports from {backend_port_base} pass {server_settings.MAX_PORT}.")
    ports = {server_settings.PORT_ENV: str(port), server_settings.BACKEND_PORT_BASE_ENV: str(backend_port_base)}
    with _environment(ports):
        yield server_settings.load(root).endpoint_url


@contextlib.contextmanager
def _environment(values: Mapping[str, str]) -> Iterator[None]:
    """Set the environment variables for the duration; each is restored, or removed, after."""
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _asks_a_hosted_model(entry: str, decide: decide_settings.DecideSettings) -> bool:
    if entry.startswith(decide_settings.CLAUDE_ENTRY_PREFIX):
        return decide.claude.enabled
    return entry == decide_settings.JEV_ENTRY and decide.jev.enabled
