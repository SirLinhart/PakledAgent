"""The included model server's settings, as `config/models.toml` pins them.

Every option a model runs with comes from that one tracked file, so the file's
hash in a run record identifies how each model was run. The file is therefore
read strictly: a missing key, a wrong type or a key this module does not know
(a misspelled option would otherwise be ignored without a word) is an error
that names the file, the table and the key.

A role is a `llama-server` of one model file (`[roles.<name>]`) or a native
systemone server of a model folder (`[systemone_roles.<name>]`). The second
kind needs a runtime llama-server does not have, so it runs a serve script in
a Python environment of its own and is asked over the systemone API on its own
port rather than through the endpoint. Both kinds are numbered together for
their ports, the llama roles first.

The ports are the one exception. A process that runs a model server of its
own beside the live agent's, as the practice benchmarks do, names other ports
in `UCLA_AGENT_MODEL_PORT` (the endpoint) and
`UCLA_AGENT_MODEL_BACKEND_PORT_BASE` (the first `llama-server`), and `load`
gives them in place of the file's. The server that process starts, its health
check and its model clients all read the ports through `load`, so they move
together and never meet the live agent's. Which port a server listens on does
not change how its models run.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ucla_agent.core.config import CONFIG_DIR_NAME, ConfigError
from ucla_agent.core.config_tables import Table, read_tables, reject_unknown_keys
from ucla_agent.core.match_meta import MODELS_CONFIG_FILE_NAME

DEFAULT_LLAMA_SERVER_PATH = "C:/Tools/llama.cpp/llama-server.exe"
DEFAULT_GPU_LAYERS = 999
DEFAULT_PARALLEL = 1
REASONING_VALUES = ("off", "on")
SERVER_TABLE = "server"
ROLES_TABLE = "roles"
SYSTEMONE_ROLES_TABLE = "systemone_roles"
COURTESY_TABLE = "courtesy"
ENV_TABLE = "env"
# A native server reads, checks and graphs its model before it answers: minutes, not the seconds of a GGUF load.
DEFAULT_SYSTEMONE_STARTUP_SECONDS = 600
# The files of a model folder that hold its weights, which is what the courtesy lease measures.
WEIGHTS_GLOB = "*.safetensors"
MAX_PORT = 65535
PORT_ENV = "UCLA_AGENT_MODEL_PORT"
BACKEND_PORT_BASE_ENV = "UCLA_AGENT_MODEL_BACKEND_PORT_BASE"


@dataclass(frozen=True)
class RoleSettings:
    """One model role: its model file and every option its `llama-server` is started with."""

    name: str
    model_path: Path
    required: bool
    ctx_size: int
    n_gpu_layers: int
    parallel: int
    temperature: float
    top_k: int | None
    top_p: float | None
    seed: int | None
    reasoning: str
    extra_args: tuple[str, ...]


@dataclass(frozen=True)
class SystemoneRoleSettings:
    """One native systemone role: the Python that runs its serve script, the model folder it serves, the environment
    it is given and how long it may take to come up.

    `script` is resolved against the repository root when the file names it
    relatively. `env` is added to the server's environment, so the machine's
    caches and runtime switches stay in the configuration file. `slot` is the
    role's number among all roles, counted from 0 with the llama roles first:
    it is served on `backend_port_base + slot`.
    """

    name: str
    python_path: Path
    script: Path
    model_dir: Path
    required: bool
    startup_seconds: int
    env: Mapping[str, str]
    slot: int

    def weight_files(self) -> list[Path]:
        """The weight files of the model folder; none when the folder is missing."""
        return sorted(self.model_dir.glob(WEIGHTS_GLOB))


@dataclass(frozen=True)
class ModelServerSettings:
    """Everything `config/models.toml` says: the endpoint, the `llama-server` to run, the roles in file order and the
    systemone roles after them."""

    host: str
    port: int
    llama_server_path: Path
    backend_port_base: int
    modelbouncer_lease: bool
    roles: tuple[RoleSettings, ...]
    systemone_roles: tuple[SystemoneRoleSettings, ...] = ()

    @property
    def endpoint_url(self) -> str:
        """The base URL of the endpoint game code sends its requests to."""
        return f"http://{self.host}:{self.port}"

    @property
    def every_role(self) -> tuple[RoleSettings | SystemoneRoleSettings, ...]:
        """The llama roles, then the systemone roles."""
        return (*self.roles, *self.systemone_roles)

    def backend_port(self, role_index: int) -> int:
        """The port of the `llama-server` behind the role at this position in the file."""
        return self.backend_port_base + role_index

    def systemone_port(self, role: SystemoneRoleSettings) -> int:
        """The port a systemone role's own server listens on."""
        return self.backend_port(role.slot)

    def systemone_url(self, role: SystemoneRoleSettings) -> str:
        """The base URL of a systemone role's own server."""
        return f"http://{self.host}:{self.systemone_port(role)}"


def load(root: Path) -> ModelServerSettings:
    """Read `config/models.toml` under `root`, with the endpoint port and backend port base of `PORT_ENV` and
    `BACKEND_PORT_BASE_ENV` in place of the file's when they are set.

    Raises `ConfigError` when the file is missing or unusable, or a variable is not a port.
    """
    tracked = load_tracked(root)
    return dataclasses.replace(
        tracked,
        port=_port_from_environment(PORT_ENV, tracked.port),
        backend_port_base=_port_from_environment(BACKEND_PORT_BASE_ENV, tracked.backend_port_base),
    )


def load_tracked(root: Path) -> ModelServerSettings:
    """Read `config/models.toml` under `root` as the file stands, whatever the environment names.

    Raises `ConfigError` when the file is missing or unusable.
    """
    path = root / CONFIG_DIR_NAME / MODELS_CONFIG_FILE_NAME
    tables = read_tables(path)
    reject_unknown_keys(path, "the file", tables, {SERVER_TABLE, ROLES_TABLE, SYSTEMONE_ROLES_TABLE, COURTESY_TABLE})
    server = Table(path, SERVER_TABLE, tables.get(SERVER_TABLE))
    courtesy = Table(path, COURTESY_TABLE, tables.get(COURTESY_TABLE, {}))
    roles = Table(path, ROLES_TABLE, tables.get(ROLES_TABLE, {}))
    native = Table(path, SYSTEMONE_ROLES_TABLE, tables.get(SYSTEMONE_ROLES_TABLE, {}))
    llama_roles = tuple(_role(roles.subtable(name)) for name in roles.keys())
    settings = ModelServerSettings(
        host=_loopback_host(server),
        port=server.integer("port", minimum=1, maximum=MAX_PORT),
        llama_server_path=Path(server.text("llama_server_path", DEFAULT_LLAMA_SERVER_PATH)),
        backend_port_base=server.integer("backend_port_base", minimum=1, maximum=MAX_PORT),
        modelbouncer_lease=courtesy.boolean("modelbouncer_lease", False),
        roles=llama_roles,
        systemone_roles=tuple(
            _systemone_role(native.subtable(name), root, len(llama_roles) + place)
            for place, name in enumerate(native.keys())
        ),
    )
    server.reject_unread_keys()
    courtesy.reject_unread_keys()
    _reject_shared_names(path, settings)
    return settings


def _reject_shared_names(path: Path, settings: ModelServerSettings) -> None:
    """Raise `ConfigError` for a name both kinds of role use: autostart_roles names a role by its name alone."""
    shared = sorted({role.name for role in settings.roles} & {role.name for role in settings.systemone_roles})
    if shared:
        raise ConfigError(f"{path}: [{ROLES_TABLE}] and [{SYSTEMONE_ROLES_TABLE}] both name {', '.join(shared)}.")


def _port_from_environment(name: str, tracked: int) -> int:
    """The port the environment variable `name` names; `tracked` when it is unset or empty."""
    text = os.environ.get(name, "")
    if not text:
        return tracked
    port = int(text) if text.isdecimal() else 0
    if not 1 <= port <= MAX_PORT:
        raise ConfigError(f"The environment variable {name} is {text!r}, not a port from 1 to {MAX_PORT}.")
    return port


def _loopback_host(server: Table) -> str:
    """The endpoint's address, which has to be one no other machine can reach."""
    host = server.text("host")
    try:
        is_loopback = ipaddress.IPv4Address(host).is_loopback
    except ipaddress.AddressValueError:
        is_loopback = False
    if not is_loopback:
        raise server.error("host", "an IPv4 loopback address such as 127.0.0.1: the model server is local only")
    return host


def _role(table: Table) -> RoleSettings:
    role = RoleSettings(
        name=table.name.removeprefix(f"{ROLES_TABLE}."),
        model_path=Path(table.text("model_path")),
        required=table.boolean("required"),
        ctx_size=table.integer("ctx_size", minimum=1),
        n_gpu_layers=table.integer("n_gpu_layers", DEFAULT_GPU_LAYERS, minimum=0),
        parallel=table.integer("parallel", DEFAULT_PARALLEL, minimum=1),
        temperature=table.number("temperature"),
        top_k=table.integer("top_k", None, minimum=0),
        top_p=table.number("top_p", None),
        seed=table.integer("seed", None),
        reasoning=table.one_of("reasoning", REASONING_VALUES),
        extra_args=table.texts("extra_args", ()),
    )
    table.reject_unread_keys()
    return role


def _systemone_role(table: Table, root: Path, slot: int) -> SystemoneRoleSettings:
    """A `[systemone_roles.<name>]` table, the role `slot` among all roles."""
    script = Path(table.text("script"))
    env = table.subtable(ENV_TABLE, optional=True)
    role = SystemoneRoleSettings(
        name=table.name.removeprefix(f"{SYSTEMONE_ROLES_TABLE}."),
        python_path=Path(table.text("python_path")),
        script=script if script.is_absolute() else root / script,
        model_dir=Path(table.text("model_dir")),
        required=table.boolean("required"),
        startup_seconds=table.integer("startup_seconds", DEFAULT_SYSTEMONE_STARTUP_SECONDS, minimum=1),
        env={name: env.text(name) for name in env.keys()},
        slot=slot,
    )
    table.reject_unread_keys()
    env.reject_unread_keys()
    return role
