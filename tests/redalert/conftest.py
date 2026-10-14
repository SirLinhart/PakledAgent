"""Fixtures shared by the tests of the Red Alert agent."""

from __future__ import annotations

import ipaddress
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx
import pytest

import situations
from ucla_agent.core import records
from ucla_agent.core.modelserver import settings as server_settings
from ucla_agent.core.secrets import BoundClient
from ucla_agent.games.redalert.agent import RedAlertAgent

REPO_ROOT = Path(__file__).resolve().parents[2]
# The endpoint of the model server the shipped config/models.toml runs (127.0.0.1:11500), and the ports of its
# systemone roles' own servers (jev9b on 41503, which the shipped Red Alert chains ask), which the live agent may hold.
SHIPPED_MODEL_SERVER_SETTINGS = server_settings.load_tracked(REPO_ROOT)
SHIPPED_MODEL_ENDPOINT = httpx.URL(SHIPPED_MODEL_SERVER_SETTINGS.endpoint_url)
SHIPPED_MODEL_PORTS = frozenset(
    {
        SHIPPED_MODEL_ENDPOINT.port,
        *(SHIPPED_MODEL_SERVER_SETTINGS.systemone_port(role) for role in SHIPPED_MODEL_SERVER_SETTINGS.systemone_roles),
    }
)
LOCAL_HOST_NAME = "localhost"
SHIPPED_ENDPOINT_FIX = (
    "build the agent with the model switches off (switches_off.settings_models_off(), or "
    "switches_off.write_model_switches_off on a copy of config/agent.toml) or give it stub choosers"
)


def is_shipped_model_endpoint(url: httpx.URL) -> bool:
    """Whether `url` is on the port of the shipped model server's endpoint, or of one of its systemone roles' servers,
    on this machine."""
    if url.port not in SHIPPED_MODEL_PORTS:
        return False
    if url.host == LOCAL_HOST_NAME:
        return True
    try:
        return ipaddress.ip_address(url.host).is_loopback
    except ValueError:
        return False


class ShippedEndpointClientGuard:
    """Fails a test that builds an agent with a client of the shipped model server over the network.

    An agent built from the shipped config with a model switch on and no stub
    chooser builds one for the chain of each switch on, and so does a loop,
    which builds the agent. The test fails as the client is built, whether or
    not a question is ever sent: the port guard of tests/conftest.py fails it
    only on a connection, which the chooser's thread may try after the test
    has ended. An agent built on another thread of the test also fails the
    test at teardown. A client over a stand-in transport sends nothing over the
    network and is let through, as is one built outside an agent, such as the
    clients the readiness check builds to check the chains of a config.
    """

    def __init__(self) -> None:
        self.test_thread = threading.get_ident()
        self.off_thread_clients: list[str] = []
        self._building = threading.local()

    @contextmanager
    def agent_build(self) -> Iterator[None]:
        """While an agent is built on this thread: a client it builds is checked."""
        self._building.depth = getattr(self._building, "depth", 0) + 1
        try:
            yield
        finally:
            self._building.depth -= 1

    def check_client(self, base_url: str, transport: httpx.BaseTransport | None) -> None:
        """Fail the test when an agent under way on this thread builds a client of the shipped model server over the
        network."""
        if not getattr(self._building, "depth", 0) or transport is not None:
            return
        if not is_shipped_model_endpoint(httpx.URL(base_url)):
            return
        if threading.get_ident() != self.test_thread:
            self.off_thread_clients.append(base_url)
        pytest.fail(
            f"the test built an agent with a client of the shipped model server at {base_url}; {SHIPPED_ENDPOINT_FIX}"
        )

    def check_no_off_thread_client(self) -> None:
        """Fail the test when one of its other threads built an agent with a client of the shipped model server."""
        if self.off_thread_clients:
            pytest.fail(
                f"a thread of the test built an agent with a client of the shipped model server at "
                f"{'; '.join(self.off_thread_clients)}; {SHIPPED_ENDPOINT_FIX}"
            )


@pytest.fixture(autouse=True)
def shipped_endpoint_client_guard(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ShippedEndpointClientGuard | None]:
    """No Red Alert test builds an agent, or a loop, that would ask the shipped model server; tests marked gpu are left
    alone, as the port guard of tests/conftest.py leaves them."""
    if request.node.get_closest_marker("gpu") is not None:
        yield None
        return
    guard = ShippedEndpointClientGuard()
    real_agent_init, real_client_init = RedAlertAgent.__init__, BoundClient.__init__

    def agent_init(agent: RedAlertAgent, **options: Any) -> None:
        with guard.agent_build():
            real_agent_init(agent, **options)

    def client_init(client: BoundClient, base_url: str, **options: Any) -> None:
        guard.check_client(base_url, options.get("transport"))
        real_client_init(client, base_url, **options)

    monkeypatch.setattr(RedAlertAgent, "__init__", agent_init)
    monkeypatch.setattr(BoundClient, "__init__", client_init)
    yield guard
    guard.check_no_off_thread_client()


@pytest.fixture
def match_record(run_folder: Path, read_jsonl: Callable[[Path], list[dict]]) -> Callable[..., list[dict]]:
    """An open match record for the test's agent to log into.

    Calling what this returns ends the match and gives the record's lines, or
    with a kind the data of every event of that kind.
    """
    records.start_match(situations.context(), None)

    def events(kind: str | None = None) -> list[dict]:
        records.finish_match(situations.RESULT)
        (events_path,) = run_folder.glob(f"runs/*/{situations.SESSION_ID}__seat/events.jsonl")
        lines = read_jsonl(events_path)
        return lines if kind is None else [line["data"] for line in lines if line["kind"] == kind]

    return events
