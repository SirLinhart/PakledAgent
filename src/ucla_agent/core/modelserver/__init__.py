"""The included local model server: pinned `llama-server` processes behind one loopback endpoint.

`start()` runs one `llama-server` per role of `config/models.toml`, or per
role it is given, and serves them through one OpenAI-style HTTP endpoint;
a systemone role it is given runs its own native server, asked directly on
its own port. `stop()` ends all of it. The run session calls the two when the agent's
configuration turns the server on.
Game code never starts or addresses a `llama-server` itself: it sends its
requests to the endpoint.

Each server's own output is appended to `runs/model-server/<role>.log`.
"""

from __future__ import annotations

import contextlib
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Collection

from ucla_agent.core.config import CONFIG_DIR_NAME, ConfigError
from ucla_agent.core.console import print_line
from ucla_agent.core.match_meta import MODELS_CONFIG_FILE_NAME
from ucla_agent.core.modelserver import courtesy
from ucla_agent.core.modelserver import settings as server_settings
from ucla_agent.core.modelserver.endpoint import Endpoint
from ucla_agent.core.modelserver.roles import READY, Role
from ucla_agent.core.modelserver.settings import ModelServerSettings, RoleSettings, SystemoneRoleSettings
from ucla_agent.core.modelserver.systemone import SystemoneRole
from ucla_agent.core.paths import repository_root
from ucla_agent.core.records import RUNS_DIR_NAME

LOG_DIR_NAME = "model-server"


class RequiredModelUnavailable(Exception):
    """A role the configuration marks as required is down; the message names each such role and why."""


@dataclass(frozen=True)
class _RunningServer:
    """What `start` built and `stop` has to end."""

    endpoint: Endpoint
    roles: tuple[Role | SystemoneRole, ...]


_running: _RunningServer | None = None


def start(roles: Collection[str] | None = None) -> None:
    """Start the model server that `config/models.toml` in the repository root describes.

    `roles` names the roles to start; None starts every role of the file.
    Binds the endpoint's port first, so a port that is taken is found before
    any model is loaded; then starts the roles one after another, each on the
    backend port of its place in the file, and serves the endpoint. A role
    that cannot be brought up is down and the server runs without it, unless
    the configuration requires that role: then everything started is stopped
    again and `RequiredModelUnavailable` is raised. Raises `ConfigError` for an
    unusable configuration or a role name the file does not have, and
    `OSError` for an endpoint port that is taken. Calling it while the server
    runs changes nothing.
    """
    global _running
    if _running is not None:
        return
    root = repository_root()
    settings = server_settings.load(root)
    chosen = _chosen_roles(settings, roles)
    native = _chosen_systemone_roles(settings, roles)
    log_dir = root / RUNS_DIR_NAME / LOG_DIR_NAME
    started = (
        *(
            Role(role, settings.llama_server_path, settings.backend_port(index), log_dir / f"{role.name}.log")
            for index, role in chosen
        ),
        *(SystemoneRole(role, settings.systemone_port(role), log_dir / f"{role.name}.log") for role in native),
    )
    # The ports are fixed above; from here on the settings name only the started roles, so the lease and the
    # required-role check cover exactly what is loaded.
    settings = dataclasses.replace(settings, roles=tuple(role for _index, role in chosen), systemone_roles=native)
    _running = _RunningServer(Endpoint(settings.host, settings.port, started), started)
    try:
        _bring_up(settings, _running)
    except BaseException:
        # Also for Ctrl+C during a long model load: nothing that was started is left running.
        stop()
        raise


def stop() -> None:
    """Close the endpoint and end every `llama-server` that `start` launched; does nothing when none runs."""
    global _running
    if _running is None:
        return
    server, _running = _running, None
    # Every step runs even when an earlier one fails, last registered first:
    # the endpoint stops taking requests before the servers behind it go.
    with contextlib.ExitStack() as steps:
        for role in server.roles:
            steps.callback(role.stop)
        steps.callback(server.endpoint.close)


def _chosen_roles(settings: ModelServerSettings, names: Collection[str] | None) -> list[tuple[int, RoleSettings]]:
    """The roles to start, in file order, each with its place in the file; every role when `names` is None."""
    in_file = list(enumerate(settings.roles))
    if names is None:
        return in_file
    unknown = sorted(set(names) - {role.name for role in settings.every_role})
    if unknown:
        raise ConfigError(
            f"{CONFIG_DIR_NAME}/{MODELS_CONFIG_FILE_NAME} has no role {', '.join(unknown)}; "
            f"its roles are {', '.join(role.name for role in settings.every_role)}"
        )
    return [(index, role) for index, role in in_file if role.name in names]


def _chosen_systemone_roles(
    settings: ModelServerSettings, names: Collection[str] | None
) -> tuple[SystemoneRoleSettings, ...]:
    """The systemone roles to start, in file order; every one when `names` is None."""
    return tuple(role for role in settings.systemone_roles if names is None or role.name in names)


def _lease_paths(settings: ModelServerSettings) -> list[Path]:
    """The model files the started roles load: each llama role's file and each systemone role's weights."""
    return [
        *(role.model_path for role in settings.roles),
        *(path for role in settings.systemone_roles for path in role.weight_files()),
    ]


def _bring_up(settings: ModelServerSettings, server: _RunningServer) -> None:
    if settings.modelbouncer_lease:
        courtesy.request_lease(_lease_paths(settings))
    for role in server.roles:
        role.start()
        print_line(_status_line(role))
    unavailable = _required_but_down(settings, server.roles)
    if unavailable:
        raise RequiredModelUnavailable(
            f"Required model role not available: {', '.join(unavailable)}. "
            f"Each server's own output is in {RUNS_DIR_NAME}/{LOG_DIR_NAME}/."
        )
    server.endpoint.serve()
    host, port = server.endpoint.address
    print_line(f"Model server: listening on http://{host}:{port}.")


def _required_but_down(settings: ModelServerSettings, roles: tuple[Role | SystemoneRole, ...]) -> list[str]:
    """Each role the configuration requires that is not ready, with the reason, as a message names it."""
    return [
        f"{role.name} ({role.reason})"
        for role_settings, role in zip(settings.every_role, roles)
        if role_settings.required and role.state != READY
    ]


def _status_line(role: Role | SystemoneRole) -> str:
    if role.state == READY:
        return f"Model server: role {role.name} is ready on port {role.port}."
    return f"Model server: role {role.name} is down ({role.reason})."
