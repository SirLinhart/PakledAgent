"""The guard every Red Alert test runs under: no test builds an agent with a client of the shipped model server.

An agent or a loop built from the shipped config with a model switch on and
no stub chooser builds such a client, over the network, for each chain it
asks. No call here sends anything: a client is only built, and the agents are
asked nothing.
"""

from __future__ import annotations

import dataclasses
import shutil
import threading

import httpx
import pytest

from altruagent import GameState
from situations import REPO_ROOT, StubChooser, context, fixture_state, orders_of, tracked_items
from switches_off import MODEL_SWITCHES, settings_models_off, write_model_switches_off
from ucla_agent.core.dispatch import Dispatcher
from ucla_agent.core.secrets import BoundClient
from ucla_agent.games.redalert.agent import RedAlertAgent
from ucla_agent.games.redalert.settings import RedAlertSettings
from ucla_agent.games.redalert.tuning import Tuning

SHIPPED_ENDPOINT = "http://127.0.0.1:11500"
# The server of the systemone role jev9b, which the shipped Red Alert chains ask.
SHIPPED_JEV9B = "http://127.0.0.1:41503"
SHIPPED_MODEL_SERVER = "a client of the shipped model server"
GUARD_MESSAGE = f"the test built an agent with a client of the shipped model server at {SHIPPED_JEV9B}"
# Each model switch that builds a client over a chain of the tracked config/agent.toml, alone, on the built-in settings.
MODEL_SWITCH_SETTINGS = [
    RedAlertSettings(model_plan_choice=True),
    RedAlertSettings(model_commander=True),
]


def _unused_transport(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"nothing is sent in these tests: {request.url}")


def _agent(settings: RedAlertSettings, **choosers: object) -> RedAlertAgent:
    """An agent on the tracked items, the built-in thresholds and the settings given, its own choosers unless given."""
    return RedAlertAgent(items=tracked_items(), tuning=Tuning(), settings=settings, **choosers)


@pytest.mark.parametrize("settings", MODEL_SWITCH_SETTINGS, ids=["model_plan_choice", "model_commander"])
def test_an_agent_built_from_the_shipped_config_with_a_model_switch_on_and_no_stub_chooser_fails_the_test(settings):
    with pytest.raises(pytest.fail.Exception, match=GUARD_MESSAGE):
        _agent(settings)


def test_with_the_commander_on_and_only_the_plan_chooser_a_stub_the_test_fails_as_the_raid_matches_did():
    with pytest.raises(pytest.fail.Exception, match=GUARD_MESSAGE):
        _agent(RedAlertSettings(model_commander=True), chooser=StubChooser())


def test_the_guard_s_message_names_the_helper_and_the_stub_choosers_that_fix_it():
    with pytest.raises(pytest.fail.Exception) as failure:
        _agent(RedAlertSettings(model_commander=True))

    message = str(failure.value)
    assert "switches_off.settings_models_off()" in message and "write_model_switches_off" in message
    assert "stub choosers" in message


def test_with_every_model_switch_on_and_stub_choosers_the_agent_is_built():
    every_switch_on = RedAlertSettings(**dict.fromkeys(MODEL_SWITCHES, True))

    agent = _agent(every_switch_on, chooser=StubChooser(), commander_chooser=StubChooser())

    assert agent.ctx.commander is not None


def test_the_tracked_settings_with_the_model_switches_off_build_an_agent_that_asks_no_model():
    tracked = RedAlertSettings.from_file(REPO_ROOT)

    agent = _agent(settings_models_off())

    assert settings_models_off() == dataclasses.replace(tracked, model_plan_choice=False, model_commander=False)
    assert agent.ctx.commander is None and agent.ctx.scout_choosers == ()


@pytest.mark.parametrize(
    "base_url",
    [SHIPPED_ENDPOINT, SHIPPED_JEV9B, "http://localhost:11500", "http://[::1]:11500", "http://localhost:41503"],
)
def test_a_client_of_the_shipped_endpoint_over_the_network_built_by_an_agent_fails_the_test(
    shipped_endpoint_client_guard, base_url
):
    with shipped_endpoint_client_guard.agent_build(), pytest.raises(pytest.fail.Exception, match=SHIPPED_MODEL_SERVER):
        BoundClient(base_url)


def test_a_client_of_the_shipped_endpoint_over_a_stand_in_transport_built_by_an_agent_is_built(
    shipped_endpoint_client_guard,
):
    with shipped_endpoint_client_guard.agent_build():
        BoundClient(SHIPPED_ENDPOINT, transport=httpx.MockTransport(_unused_transport))


def test_a_client_of_a_test_s_own_model_server_port_built_by_an_agent_is_built(
    shipped_endpoint_client_guard, model_ports
):
    with shipped_endpoint_client_guard.agent_build():
        BoundClient(model_ports.endpoint_url)


@pytest.mark.parametrize("base_url", ["http://127.0.0.1:20500", "https://api.anthropic.com", "http://10.0.0.5:11500"])
def test_a_client_of_another_port_or_host_built_by_an_agent_is_built(shipped_endpoint_client_guard, base_url):
    with shipped_endpoint_client_guard.agent_build():
        BoundClient(base_url)


def test_a_client_of_the_shipped_endpoint_built_outside_an_agent_is_built_as_the_readiness_check_builds_one():
    # The config check builds every chain of config/agent.toml to check it, and asks nothing.
    BoundClient(SHIPPED_ENDPOINT)


def test_an_agent_built_on_another_thread_of_the_test_is_kept_and_fails_the_test(shipped_endpoint_client_guard):
    def build_on_another_thread() -> None:
        with pytest.raises(pytest.fail.Exception):
            _agent(RedAlertSettings(model_commander=True))

    thread = threading.Thread(target=build_on_another_thread)
    thread.start()
    thread.join()

    assert shipped_endpoint_client_guard.off_thread_clients == [SHIPPED_JEV9B]
    with pytest.raises(pytest.fail.Exception, match="a thread of the test built an agent with a client"):
        shipped_endpoint_client_guard.check_no_off_thread_client()
    # Kept, it would fail this test at teardown too.
    shipped_endpoint_client_guard.off_thread_clients.clear()


def test_a_loop_over_a_copy_of_the_shipped_config_fails_the_test_at_its_first_state_and_with_the_switches_off_plays(
    run_folder,
):
    shutil.copytree(REPO_ROOT / "config", run_folder / "config")
    state = GameState.from_mcp_state(fixture_state("opening_mcv"))

    with pytest.raises(pytest.fail.Exception, match=GUARD_MESSAGE):
        Dispatcher().choose_action(state, context())
    write_model_switches_off(run_folder / "config" / "agent.toml")
    loop = Dispatcher()

    assert orders_of(loop.choose_action(state, context())) == [{"cmd": "deploy", "units": [100]}]
    assert loop.decision_maker.ctx.commander is None
