"""One role's server, with the stub standing in for llama-server: ready, down with each reason, completions, the end."""

from __future__ import annotations

import os
import signal
import socket
import time
from pathlib import Path
from typing import Callable, Iterator

import httpx
import pytest

from ucla_agent.core.instance_lock import process_is_alive
from ucla_agent.core.modelserver import roles
from ucla_agent.core.modelserver.roles import Role
from ucla_agent.core.modelserver.settings import RoleSettings

CHAT_REQUEST = {
    "model": "fast",
    "messages": [{"role": "user", "content": "Choose a plan."}],
    "max_tokens": 8,
    "response_format": {"type": "json_schema", "json_schema": {"schema": {"type": "object"}}},
    "grammar": 'root ::= "A" | "B"',
}
SHORT_READY_LIMIT_SECONDS = 0.6
READY_DELAY_SECONDS = 0.5


def _settings(model_path: Path, *extra_args: str) -> RoleSettings:
    return RoleSettings(
        name="fast",
        model_path=model_path,
        required=False,
        ctx_size=8192,
        n_gpu_layers=999,
        parallel=1,
        temperature=0,
        top_k=None,
        top_p=None,
        seed=None,
        reasoning="off",
        extra_args=extra_args,
    )


@pytest.fixture
def model_file(tmp_path: Path) -> Path:
    """A file standing in for a model: a role only ever checks that its model file is there."""
    path = tmp_path / "models" / "fast.gguf"
    path.parent.mkdir()
    path.write_bytes(b"GGUF")
    return path


@pytest.fixture
def make_role(run_folder, tmp_path, llama_stub, free_port, model_file) -> Iterator[Callable[..., Role]]:
    """Builds roles served by the stub, each on a free port, and stops every one of them after the test.

    The tests run in a temporary folder: sending a request loads the secrets of
    the current folder's `.env`, and that must never be a developer's own.
    """
    made = []

    def make(*extra_args: str, model_path: Path = model_file, server_path: Path = llama_stub) -> Role:
        role = Role(_settings(model_path, *extra_args), server_path, free_port(), tmp_path / "logs" / "fast.log")
        made.append(role)
        return role

    yield make
    for role in made:
        role.stop()


def _stub(local_http: httpx.Client, role: Role, route: str) -> dict:
    return local_http.get(f"http://127.0.0.1:{role.port}{route}").json()


def _nothing_listens_on(port: int) -> bool:
    """Whether the port can be bound, which it cannot while a server still listens on it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


# -- ready -----------------------------------------------------------------------------------------


def test_a_role_is_down_and_not_started_before_it_is_started(make_role):
    role = make_role()

    assert (role.name, role.state, role.reason) == ("fast", "down", "not_started")


def test_start_launches_the_server_on_the_role_s_port_and_the_role_is_ready(make_role, local_http, model_file):
    role = make_role()

    role.start()

    arguments = _stub(local_http, role, "/stub/launch")["arguments"]
    assert (role.state, role.reason) == ("ready", None)
    assert arguments[:6] == ["--model", str(model_file), "--host", "127.0.0.1", "--port", str(role.port)]
    assert arguments[6:12] == ["--ctx-size", "8192", "--n-gpu-layers", "999", "--parallel", "1"]


def test_a_ready_role_has_had_one_warm_up_completion_of_one_token(make_role, local_http):
    role = make_role()

    role.start()

    (warm_up,) = _stub(local_http, role, "/stub/requests")["requests"]
    assert warm_up["body"]["max_tokens"] == 1
    assert warm_up["headers"]["x-client"] == "ucla-agent"


def test_a_server_that_needs_time_to_load_is_waited_for(make_role):
    role = make_role("--stub-ready-after", str(READY_DELAY_SECONDS))
    started = time.monotonic()

    role.start()

    assert role.state == "ready"
    assert time.monotonic() - started >= READY_DELAY_SECONDS


def test_the_server_s_own_output_goes_to_the_role_s_log_file(make_role, tmp_path):
    role = make_role()

    role.start()

    log_text = (tmp_path / "logs" / "fast.log").read_text(encoding="utf-8")
    assert log_text.startswith(f"fake llama-server: listening on 127.0.0.1:{role.port}\n")


# -- completions -----------------------------------------------------------------------------------


def test_a_completion_reaches_the_server_unchanged_with_the_client_header(make_role, local_http):
    role = make_role("--stub-content", '{"choice": "B"}')
    role.start()

    answer = role.complete(CHAT_REQUEST)

    _warm_up, forwarded = _stub(local_http, role, "/stub/requests")["requests"]
    assert answer.status_code == 200
    assert answer.json()["choices"][0]["message"]["content"] == '{"choice": "B"}'
    assert forwarded["body"] == CHAT_REQUEST
    assert forwarded["headers"]["x-client"] == "ucla-agent"


def test_a_prompt_holding_a_loaded_key_reaches_the_server_redacted(make_role, local_http, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "jev-4f9c2d7e1a6b8053")
    role = make_role()
    role.start()

    role.complete({"messages": [{"role": "user", "content": "The key is jev-4f9c2d7e1a6b8053."}]})

    forwarded = _stub(local_http, role, "/stub/requests")["requests"][-1]
    assert forwarded["body"]["messages"][0]["content"] == "The key is [REDACTED]."


def test_a_completion_the_server_refuses_comes_back_with_its_status(make_role, monkeypatch):
    role = make_role("--stub-completion-status", "500")
    monkeypatch.setattr(Role, "_warm_up", lambda self: None)
    role.start()

    assert role.complete(CHAT_REQUEST).status_code == 500


# -- down, with a reason ---------------------------------------------------------------------------


def test_a_missing_model_file_leaves_the_role_down_and_launches_nothing(make_role, tmp_path):
    role = make_role(model_path=tmp_path / "models" / "not-downloaded.gguf")

    role.start()

    assert (role.state, role.reason) == ("down", "model_file_missing")
    assert _nothing_listens_on(role.port)
    assert not (tmp_path / "logs").exists()


def test_a_port_somebody_else_listens_on_leaves_the_role_down_and_the_listener_alone(make_role, tmp_path):
    role = make_role()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", role.port))
        listener.listen()

        role.start()

        assert (role.state, role.reason) == ("down", "port_in_use")
        assert not (tmp_path / "logs").exists()
        with socket.create_connection(("127.0.0.1", role.port), timeout=5) as connection:
            accepted, _address = listener.accept()
            accepted.close()
            assert connection.getpeername()[1] == role.port


def test_a_server_program_that_cannot_be_started_leaves_the_role_down(make_role, tmp_path):
    role = make_role(server_path=tmp_path / "no-such-llama-server.exe")

    role.start()

    assert (role.state, role.reason) == ("down", "launch_failed")


def test_a_server_that_exits_while_starting_leaves_the_role_down(make_role, tmp_path):
    role = make_role("--stub-exit-code", "3")

    role.start()

    assert (role.state, role.reason) == ("down", "exited")
    assert "exiting with code 3" in (tmp_path / "logs" / "fast.log").read_text(encoding="utf-8")


def test_a_server_that_is_not_ready_in_time_is_ended_and_the_role_is_down(make_role, monkeypatch):
    monkeypatch.setattr(roles, "READY_LIMIT_SECONDS", SHORT_READY_LIMIT_SECONDS)
    role = make_role("--stub-never-ready")
    started = time.monotonic()

    role.start()

    assert (role.state, role.reason) == ("down", "not_ready_in_time")
    assert time.monotonic() - started >= SHORT_READY_LIMIT_SECONDS
    assert _nothing_listens_on(role.port)


def test_a_server_that_fails_its_warm_up_is_ended_and_the_role_is_down(make_role):
    role = make_role("--stub-completion-status", "500")

    role.start()

    assert (role.state, role.reason) == ("down", "warm_up_failed")
    assert _nothing_listens_on(role.port)


def test_a_server_that_does_not_answer_its_warm_up_is_ended_and_the_role_is_down(make_role, monkeypatch):
    role = make_role()

    def unreachable(*_arguments, **_options):
        raise httpx.ConnectError("the server went away")

    def healthy_then_unreachable(self: Role) -> bool:
        monkeypatch.setattr(self._client, "request", unreachable)
        return True

    monkeypatch.setattr(Role, "_is_healthy", healthy_then_unreachable)

    role.start()

    assert (role.state, role.reason) == ("down", "warm_up_failed")


# -- after it was ready ----------------------------------------------------------------------------


def test_a_ready_role_whose_server_dies_is_down_with_reason_exited(make_role, local_http, wait_for):
    role = make_role()
    role.start()
    server_pid = _stub(local_http, role, "/stub/launch")["pid"]

    os.kill(server_pid, signal.SIGTERM)

    wait_for(lambda: role.state == "down", "the role noticing that its server is gone")
    assert role.reason == "exited"
    with pytest.raises(httpx.HTTPError):
        role.complete(CHAT_REQUEST)


def test_stop_ends_the_server_and_the_role_is_down(make_role, local_http):
    role = make_role()
    role.start()
    server_pid = _stub(local_http, role, "/stub/launch")["pid"]

    role.stop()

    assert not process_is_alive(server_pid)
    assert (role.state, role.reason) == ("down", "stopped")
    assert _nothing_listens_on(role.port)


def test_stopping_a_role_that_never_ran_keeps_its_reason(make_role, tmp_path):
    role = make_role(model_path=tmp_path / "models" / "not-downloaded.gguf")
    role.start()

    role.stop()

    assert (role.state, role.reason) == ("down", "model_file_missing")
