"""The local model server a model run of the Werewolf practice benchmark needs.

The `agent-model` arm asks the roles its chains name (`strong`, as tracked)
and the model players of the `mixed` mix ask `fast`. Before such a run plays
its first game, `serving` makes sure every role it needs is ready on the
endpoint of `config/models.toml`. A model server that already serves them is
used as it is; otherwise the included model server is started for the run
with only those roles, and stopped when the run ends. Starting every role of
the file would load models no game of the run asks, and the shipped roles
together take about 50 GB of memory. A role that is still not ready stops
the run before its first game: games in which every model call falls back to
the script would measure the wrong thing.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, Collection, Iterator

import httpx

from ucla_agent.core import modelserver
from ucla_agent.core.console import print_line
from ucla_agent.core.modelserver import settings as server_settings
from ucla_agent.core.modelserver.roles import HEALTH_ROUTE, READY

HEALTH_TIMEOUT_SECONDS = 2.0
LOGS_FOLDER = "runs/model-server/"


class ModelsUnavailable(Exception):
    """A role the run needs is not ready and cannot be made ready; the message names the roles and why."""


@contextlib.contextmanager
def serving(
    roles: Collection[str], root: Path, *, transport: httpx.BaseTransport | None = None
) -> Iterator[tuple[str, ...]]:
    """Hold the model server roles a run needs ready while the run is played; nothing when it needs none.

    Yields the roles started for the run, by name: none when the run needs no
    model or a running server already serves its roles. `root` is the project
    whose `config/models.toml` names the endpoint and the server to start.
    `transport` replaces the network of the health check, for tests. Raises
    `ModelsUnavailable` before the run when a role cannot be made ready.
    """
    if not roles:
        yield ()
        return
    needed = tuple(sorted(roles))
    endpoint = server_settings.load(root).endpoint_url
    if not not_ready(needed, endpoint, transport):
        print_line(f"Model server: {endpoint} already serves {', '.join(needed)} ready; no role started for the run.")
        yield ()
        return
    _start_for_the_run(needed, endpoint)
    try:
        missing = not_ready(needed, endpoint, transport)
        if missing:
            raise ModelsUnavailable(
                f"The model server started for the run has {', '.join(missing)} down. "
                f"Each server's own output is in {LOGS_FOLDER}."
            )
        yield needed
    finally:
        modelserver.stop()


def _start_for_the_run(needed: tuple[str, ...], endpoint: str) -> None:
    """Start the included model server with only the `needed` roles, saying which; `ModelsUnavailable` when it
    cannot be started."""
    print_line(f"Model server: starting only the roles the run needs: {', '.join(needed)}.")
    try:
        modelserver.start(needed)
    except (OSError, modelserver.RequiredModelUnavailable) as exc:
        raise ModelsUnavailable(
            f"No model server on {endpoint} serves {', '.join(needed)} ready, and the included one could not "
            f"be started for the run: {exc}"
        ) from exc


def not_ready(roles: Collection[str], endpoint: str, transport: httpx.BaseTransport | None = None) -> list[str]:
    """The roles among `roles` the endpoint's `/health` does not report ready, by name; all of them when it does not
    answer as the model server does."""
    try:
        with httpx.Client(transport=transport, timeout=HEALTH_TIMEOUT_SECONDS) as client:
            answer = client.get(endpoint + HEALTH_ROUTE).json()
    except (httpx.HTTPError, ValueError):
        answer = None
    states = answer.get("roles") if isinstance(answer, dict) else None
    states = states if isinstance(states, dict) else {}
    return sorted(role for role in roles if _state(states.get(role)) != READY)


def _state(entry: Any) -> Any:
    """The state a health answer gives one role; None when the entry is not a role's status."""
    return entry.get("state") if isinstance(entry, dict) else None
