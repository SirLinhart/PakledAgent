"""Fixtures shared by every test folder."""

from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
import random
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterator, Mapping

import pytest

from altruagent import runner
from ucla_agent.core import paths, records, secrets

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src"
SCRIPTS_FOLDER = REPO_ROOT / "scripts"
REDALERT_TESTS_FOLDER = REPO_ROOT / "tests" / "redalert"
UNUSABLE_AGENT_KEY = "not-an-agent-key"
UNREACHABLE_CONTROL_URL = "http://127.0.0.1:9"
REPLAY_GENERATOR = "make_replay_fixture.py"
REPLAY_SEED = 7
# The ports the live agent's model server holds with the shipped config/models.toml: its endpoint, the
# llama-servers of the roles fast, strong and small, and the server of the systemone role jev9b.
SHIPPED_MODEL_PORTS = frozenset({11500, 41500, 41501, 41502, 41503})
GUARDED_SOCKET_METHODS = ("connect", "connect_ex", "bind")
LOCAL_HOST_NAMES = frozenset({"", "localhost"})
LOOPBACK = "127.0.0.1"
LLAMA_SERVER_STUB = REPO_ROOT / "tests" / "stubs" / "fake_llama_server.py"
# The roles of the shipped config/models.toml, in its order: role number i is served on backend_port_base + i.
SHIPPED_ROLES = ("fast", "strong", "small")
# The rest of a role's table in a test copy of config/models.toml, after its model_path, when the test names none.
STUB_ROLE_TABLE = 'required = false\nctx_size = 8192\ntemperature = 0\nreasoning = "off"\nextra_args = ["--no-mmap"]\n'
# Where a test's own model server listens. The band lies below the dynamic port ranges of Windows (49152 up) and
# Linux (32768 up), from which the system gives out the local ports of outgoing connections, so a connection the
# test makes cannot be given a backend port between the pick and the stub's bind; and it holds neither 11500 nor
# the shipped backend ports 41500-41599.
TEST_MODEL_PORT_BAND = range(20000, 32768)
MODEL_PORT_PICK_ATTEMPTS = 100
_model_port_picker = random.Random()

# The fake Red Alert game (`fake_game`) is test code that test files, scenario files and the replay generator
# import by name. Pytest's importlib mode puts no test folder on the import path, so its folder is added here.
if str(REDALERT_TESTS_FOLDER) not in sys.path:
    sys.path.insert(0, str(REDALERT_TESTS_FOLDER))


def _is_local_host(host: Any) -> bool:
    """Whether a socket address's host is this machine: a loopback or wildcard address, or the name localhost."""
    if host in LOCAL_HOST_NAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _is_shipped_model_address(address: Any) -> bool:
    """Whether a socket address is a port of the shipped model server on this machine."""
    return (
        isinstance(address, tuple)
        and len(address) >= 2
        and address[1] in SHIPPED_MODEL_PORTS
        and _is_local_host(address[0])
    )


class ShippedModelPortGuard:
    """Fails a test whose process connects or binds a socket to a port of the shipped model server on this machine.

    The live agent can hold those ports while the suite runs: a test that
    talked to them would reach the agent's own model server, and a test that
    took one would keep the agent from serving. The contact fails the test at
    once, before the socket is used. A contact made on another thread fails
    only that thread there, so it is also kept and fails the test at teardown.
    """

    def __init__(self) -> None:
        self.test_thread = threading.get_ident()
        self.off_thread_contacts: list[str] = []

    def guarded(self, action: str, real_method: Callable[..., Any]) -> Callable[..., Any]:
        """`real_method` of `socket.socket`, refusing the shipped model ports."""

        def call(sock: socket.socket, address: Any) -> Any:
            self.check(action, address)
            return real_method(sock, address)

        return call

    def check(self, action: str, address: Any) -> None:
        """Fail the test when `address` is a port of the shipped model server."""
        if not _is_shipped_model_address(address):
            return
        contact = f"{action} {address[0]}:{address[1]}"
        if threading.get_ident() != self.test_thread:
            self.off_thread_contacts.append(contact)
        pytest.fail(
            f"the test tried to {contact}, a port of the shipped model server that the live agent may hold; "
            "start model servers on the ports of the model_ports fixture"
        )

    def check_no_off_thread_contact(self) -> None:
        """Fail the test when one of its other threads touched a shipped model port."""
        if self.off_thread_contacts:
            pytest.fail(f"a thread of the test touched a shipped model port: {'; '.join(self.off_thread_contacts)}")


@pytest.fixture(autouse=True)
def shipped_model_ports_guard(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ShippedModelPortGuard | None]:
    """No test opens a socket to, or takes, a port the live agent's model server holds.

    Tests marked gpu are left alone: they are run only on request, and they
    run the shipped configuration on its own ports.
    """
    if request.node.get_closest_marker("gpu") is not None:
        yield None
        return
    guard = ShippedModelPortGuard()
    for method in GUARDED_SOCKET_METHODS:
        monkeypatch.setattr(socket.socket, method, guard.guarded(method, getattr(socket.socket, method)))
    yield guard
    guard.check_no_off_thread_contact()


@dataclass(frozen=True)
class ModelPorts:
    """Free loopback ports for a model server a test starts: its endpoint, and one backend port per shipped role."""

    endpoint: int
    backend_port_base: int

    @property
    def endpoint_url(self) -> str:
        return f"http://{LOOPBACK}:{self.endpoint}"

    def backend_port(self, role_index: int) -> int:
        """The port of the llama-server of role number `role_index`, counted from 0 in the order of the file."""
        return self.backend_port_base + role_index

    def server_table(self, llama_server_path: Path) -> str:
        """The `[server]` table of a config/models.toml that serves on these ports and runs `llama_server_path`."""
        return (
            f'[server]\nhost = "{LOOPBACK}"\nport = {self.endpoint}\n'
            f'llama_server_path = "{llama_server_path.as_posix()}"\nbackend_port_base = {self.backend_port_base}\n'
        )


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((LOOPBACK, port))
        except OSError:
            return False
    return True


def pick_model_ports() -> ModelPorts:
    """An endpoint port and a backend port per shipped role, consecutive, all different and all free right now."""
    last_base = TEST_MODEL_PORT_BAND.stop - len(SHIPPED_ROLES)
    for _attempt in range(MODEL_PORT_PICK_ATTEMPTS):
        endpoint = _model_port_picker.randrange(TEST_MODEL_PORT_BAND.start, TEST_MODEL_PORT_BAND.stop)
        base = _model_port_picker.randrange(TEST_MODEL_PORT_BAND.start, last_base + 1)
        backend = range(base, base + len(SHIPPED_ROLES))
        if endpoint not in backend and all(_port_is_free(port) for port in (endpoint, *backend)):
            return ModelPorts(endpoint, base)
    pytest.fail(f"no free model server ports in {TEST_MODEL_PORT_BAND} after {MODEL_PORT_PICK_ATTEMPTS} tries")


@pytest.fixture
def model_ports() -> ModelPorts:
    """Free ports for the model server a test starts, none of them a port the live agent's model server holds."""
    return pick_model_ports()


@pytest.fixture
def llama_stub() -> Path:
    """The program that stands in for `llama-server` in tests."""
    return LLAMA_SERVER_STUB


@pytest.fixture
def stub_models_config(model_ports: ModelPorts, llama_stub: Path) -> Callable[..., ModelPorts]:
    """Writes a test copy of `config/models.toml` into a project root, serving on the ports of `model_ports`.

    The copy runs the stub in place of llama-server. `role_tables` maps each
    role, in the order of the file, to the rest of its table after its
    `model_path`; left out, the roles are the shipped fast, strong and small,
    each run with `--no-mmap`. A role's model file is `models/<role>.gguf`
    under the root, made when the first copy is written there, so a test can
    remove one and write the copy again. `extra_tables` follow the `[server]`
    table. Gives the ports.
    """

    def write(root: Path, role_tables: Mapping[str, str] | None = None, *, extra_tables: str = "") -> ModelPorts:
        tables = dict.fromkeys(SHIPPED_ROLES, STUB_ROLE_TABLE) if role_tables is None else role_tables
        models_folder = root / "models"
        if not models_folder.exists():
            models_folder.mkdir()
            for role in tables:
                (models_folder / f"{role}.gguf").write_bytes(b"GGUF " + role.encode("ascii"))
        text = [model_ports.server_table(llama_stub), extra_tables]
        for role, rest in tables.items():
            model_path = (models_folder / f"{role}.gguf").as_posix()
            text.append(f'[roles.{role}]\nmodel_path = "{model_path}"\n{rest}')
        config_path = root / "config" / "models.toml"
        config_path.parent.mkdir(exist_ok=True)
        config_path.write_bytes("\n".join(text).encode("utf-8"))
        return model_ports

    return write


@pytest.fixture(autouse=True)
def no_platform_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test, and no process a test starts, can sign in to the real platform.

    The vendored runtime reads these variables and loads `.env` only for
    variables that are not already set, so a developer's real key is never
    picked up: the runtime rejects this value before sending anything.
    """
    monkeypatch.setenv("ALTRUAGENT_OFFICIAL_AGENT_KEY", UNUSABLE_AGENT_KEY)
    monkeypatch.setenv("ALTRUAGENT_CONTROL_URL", UNREACHABLE_CONTROL_URL)


@pytest.fixture(autouse=True)
def strict_event_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under test, logging an event kind outside the closed list raises instead of being recorded as an error."""
    monkeypatch.setenv(records.STRICT_EVENT_KINDS_ENV, "1")


@pytest.fixture(autouse=True)
def fresh_secret_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test redacts the secrets of its own environment: nothing an earlier test loaded is remembered."""
    monkeypatch.setattr(secrets, "_redaction", None)


@pytest.fixture(autouse=True)
def fresh_record_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each test starts with no open run or match record and no end-of-match callback, and leaves no open record."""
    state = records._ProcessRecord()
    monkeypatch.setattr(records, "_process", state)
    yield
    if state.writer is not None:
        state.writer.close()


@pytest.fixture(autouse=True)
def unhooked_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with the vendored runner's own final-state function: a hook a test installs is taken off."""
    monkeypatch.setattr(runner, "_terminal_game_state", runner._terminal_game_state)


@pytest.fixture
def run_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary directory standing in for the repository root, so records land there.

    It is the current directory and the project root that `UCLA_AGENT_ROOT`
    names, for this process and every process it starts. `UCLA_AGENT_RUNS_DIR`
    is unset, so a value left in the shell cannot send the records elsewhere.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(paths.ROOT_ENV, str(tmp_path))
    monkeypatch.delenv(paths.RUNS_DIR_ENV, raising=False)
    return tmp_path


@pytest.fixture
def read_jsonl() -> Callable[[Path], list[dict]]:
    """Reader for a JSON Lines file: one parsed object per line, in file order."""

    def read(path: Path) -> list[dict]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    return read


@pytest.fixture(scope="session")
def load_script() -> Callable[[str], ModuleType]:
    """Imports a script from `scripts/` into this process by its file name, without running its `main`."""

    def load(file_name: str) -> ModuleType:
        spec = importlib.util.spec_from_file_location(Path(file_name).stem, SCRIPTS_FOLDER / file_name)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return load


@pytest.fixture
def run_script() -> Callable[..., subprocess.CompletedProcess]:
    """Runs `python scripts/<file name> <arguments>` in a folder, as the project root, with `src/` importable."""

    def run(root: Path, file_name: str, *arguments: str) -> subprocess.CompletedProcess:
        command = [sys.executable, str(SCRIPTS_FOLDER / file_name), *arguments]
        environment = {**os.environ, "PYTHONPATH": str(SOURCE_ROOT), paths.ROOT_ENV: str(root)}
        return subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True)

    return run


@pytest.fixture(scope="session")
def long_match(load_script: Callable[[str], ModuleType]) -> tuple[list[dict], float]:
    """A long scripted fake Red Alert match and the seconds it took to generate, made once per test session."""
    started = time.perf_counter()
    states = load_script(REPLAY_GENERATOR).build(seed=REPLAY_SEED)
    return states, time.perf_counter() - started


@pytest.fixture(scope="session")
def long_match_states(long_match: tuple[list[dict], float]) -> list[dict]:
    """Every state of a long scripted fake Red Alert match, in order: over 3,000, with up to 170 own units.

    The states are generated in memory and shared by the whole session, so a
    test must not change them.
    """
    return long_match[0]


@pytest.fixture(scope="session")
def long_match_generation_seconds(long_match: tuple[list[dict], float]) -> float:
    """How long generating `long_match_states` took."""
    return long_match[1]
