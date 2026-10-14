"""The included model server as a whole: `start()` and `stop()` with the stub standing in for llama-server.

The stub servers run as real processes. The endpoint and the backend ports
are the free ports of the `model_ports` fixture, never the shipped 11500 and
41500 up, which the live agent's model server can hold while these tests run;
the backend ports follow one another in the order of the roles, as the
shipped ones do.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx
import pytest

from ucla_agent.core import modelserver
from ucla_agent.core.config import ConfigError
from ucla_agent.core.instance_lock import process_is_alive
from ucla_agent.core.modelserver import RequiredModelUnavailable, courtesy, roles
from ucla_agent.core.startup import run_session

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "src"
FAST_TABLE = """required = false
ctx_size = 8192
temperature = 0
reasoning = "off"
extra_args = ["--stub-content", '{"choice": "HOLD"}']
"""
# The strong role's table after its `required` line, which a test can set.
STRONG_TABLE_REST = """ctx_size = 16384
n_gpu_layers = 40
parallel = 2
temperature = 0
reasoning = "off"
extra_args = ["--stub-content", '{"choice": "RAID"}']
"""
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "choice",
        "schema": {"type": "object", "properties": {"choice": {"enum": ["HOLD", "RAID"]}}, "required": ["choice"]},
    },
}
CHAT_REQUEST = {
    "model": "fast",
    "messages": [{"role": "user", "content": "Choose a plan."}],
    "temperature": 0,
    "max_tokens": 8,
    "response_format": RESPONSE_FORMAT,
}
ORPHAN_LIMIT_SECONDS = 5.0
REAL_COMPLETION_LIMIT_SECONDS = 30.0
# Another process that starts the model server of the folder it runs in, reports its own process id and stays alive.
SERVER_HELPER = (
    "import os, time\n"
    "from ucla_agent.core import modelserver\n"
    "modelserver.start()\n"
    "print('helper-pid', os.getpid(), flush=True)\n"
    "time.sleep(120)\n"
)


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


class _ServerFolder:
    """A temporary repository root whose `config/models.toml` runs the stub in place of llama-server.

    The configuration is a test copy written by `stub_models_config`, on the
    free ports of the `model_ports` fixture.
    """

    def __init__(self, root: Path, write_config: Callable[..., Any]) -> None:
        self.root = root
        self.fast_model = root / "models" / "fast.gguf"
        self.strong_model = root / "models" / "strong.gguf"
        self._write_config = write_config
        self.ports = self.write()

    @property
    def url(self) -> str:
        return self.ports.endpoint_url

    @property
    def endpoint_port(self) -> int:
        return self.ports.endpoint

    @property
    def fast_port(self) -> int:
        return self.ports.backend_port(0)

    @property
    def strong_port(self) -> int:
        return self.ports.backend_port(1)

    def write(self, *, strong_required: bool = False, courtesy_lease: bool | None = None) -> Any:
        """Write the configuration: two roles with different settings, each stub answering with its own text.

        Gives the ports of the `model_ports` fixture, which every write keeps.
        """
        lease = "" if courtesy_lease is None else f"[courtesy]\nmodelbouncer_lease = {str(courtesy_lease).lower()}\n"
        strong_table = f"required = {str(strong_required).lower()}\n{STRONG_TABLE_REST}"
        return self._write_config(self.root, {"fast": FAST_TABLE, "strong": strong_table}, extra_tables=lease)


@pytest.fixture
def folder(run_folder: Path, stub_models_config: Callable[..., Any]) -> Iterator[_ServerFolder]:
    """The current directory as a repository root with a stub-backed model server configuration.

    The model server is stopped after the test whatever the test did.
    """
    yield _ServerFolder(run_folder, stub_models_config)
    modelserver.stop()


def _launched(local_http: httpx.Client, port: int) -> dict:
    """What the stub on a backend port says about itself: its process id and its arguments as flag to value."""
    launch = local_http.get(f"http://127.0.0.1:{port}/stub/launch").json()
    arguments = launch["arguments"]
    return {"pid": launch["pid"], **dict(zip(arguments[0::2], arguments[1::2]))}


# -- (1) one server per role, on consecutive ports, with the configured options --------------------


def test_start_launches_one_stub_per_role_on_consecutive_backend_ports_with_the_configured_options(folder, local_http):
    modelserver.start()

    fast, strong = _launched(local_http, folder.fast_port), _launched(local_http, folder.strong_port)
    assert fast["pid"] != strong["pid"] and process_is_alive(fast["pid"]) and process_is_alive(strong["pid"])
    assert (fast["--model"], fast["--ctx-size"], fast["--n-gpu-layers"], fast["--parallel"]) == (
        str(folder.fast_model),
        "8192",
        "999",
        "1",
    )
    assert (strong["--model"], strong["--ctx-size"], strong["--n-gpu-layers"], strong["--parallel"]) == (
        str(folder.strong_model),
        "16384",
        "40",
        "2",
    )
    assert (fast["--port"], strong["--port"]) == (
        str(folder.ports.backend_port_base),
        str(folder.ports.backend_port_base + 1),
    )
    assert (fast["--host"], strong["--host"]) == ("127.0.0.1", "127.0.0.1")


def test_the_endpoint_lists_the_roles_and_reports_both_ready(folder, local_http):
    modelserver.start()

    models = local_http.get(folder.url + "/v1/models").json()
    health = local_http.get(folder.url + "/health").json()
    assert [model["id"] for model in models["data"]] == ["fast", "strong"]
    assert health == {"roles": {"fast": {"state": "ready"}, "strong": {"state": "ready"}}}


def test_start_says_what_became_of_each_role_and_where_the_endpoint_listens(folder, capsys):
    folder.strong_model.unlink()

    modelserver.start()

    assert capsys.readouterr().out.splitlines() == [
        f"Model server: role fast is ready on port {folder.fast_port}.",
        "Model server: role strong is down (model_file_missing).",
        f"Model server: listening on {folder.url}.",
    ]


def test_each_server_s_output_goes_to_its_own_log_file_under_runs(folder):
    modelserver.start()

    for name, port in (("fast", folder.fast_port), ("strong", folder.strong_port)):
        log_text = (folder.root / "runs" / "model-server" / f"{name}.log").read_text(encoding="utf-8")
        assert log_text.startswith(f"fake llama-server: listening on 127.0.0.1:{port}\n")


# -- (2) and (3) completions through the endpoint --------------------------------------------------


def test_a_completion_for_fast_returns_the_stub_s_answer_and_the_response_format_arrives_unchanged(folder, local_http):
    modelserver.start()

    answer = local_http.post(folder.url + "/v1/chat/completions", json=CHAT_REQUEST, headers={"x-client": "ucla-agent"})

    forwarded = local_http.get(f"http://127.0.0.1:{folder.fast_port}/stub/requests").json()["requests"][-1]
    assert answer.status_code == 200
    assert answer.json()["choices"][0]["message"]["content"] == '{"choice": "HOLD"}'
    assert forwarded["body"]["response_format"] == RESPONSE_FORMAT
    assert forwarded["body"] == CHAT_REQUEST
    assert forwarded["headers"]["x-client"] == "ucla-agent"


def test_a_completion_goes_to_the_role_it_names(folder, local_http):
    modelserver.start()

    answer = local_http.post(folder.url + "/v1/chat/completions", json={**CHAT_REQUEST, "model": "strong"})

    strong_requests = local_http.get(f"http://127.0.0.1:{folder.strong_port}/stub/requests").json()["requests"]
    fast_requests = local_http.get(f"http://127.0.0.1:{folder.fast_port}/stub/requests").json()["requests"]
    assert answer.json()["choices"][0]["message"]["content"] == '{"choice": "RAID"}'
    assert strong_requests[-1]["body"]["model"] == "strong"
    assert all("model" not in request["body"] for request in fast_requests)


def test_a_completion_for_an_unknown_role_is_404_unknown_role(folder, local_http):
    modelserver.start()

    answer = local_http.post(folder.url + "/v1/chat/completions", json={**CHAT_REQUEST, "model": "nope"})

    assert (answer.status_code, answer.json()) == (404, {"error": "unknown_role"})


# -- (4) and (5) a role whose model file is missing ------------------------------------------------


def test_a_role_without_its_model_file_is_down_and_the_server_runs_without_it(folder, local_http):
    folder.strong_model.unlink()

    modelserver.start()

    health = local_http.get(folder.url + "/health").json()
    refused = local_http.post(folder.url + "/v1/chat/completions", json={**CHAT_REQUEST, "model": "strong"})
    served = local_http.post(folder.url + "/v1/chat/completions", json=CHAT_REQUEST)
    assert health == {
        "roles": {"fast": {"state": "ready"}, "strong": {"state": "down", "reason": "model_file_missing"}}
    }
    assert (refused.status_code, refused.json()) == (503, {"error": "role_down", "reason": "model_file_missing"})
    assert served.status_code == 200
    assert _port_is_free(folder.strong_port)


def test_a_required_role_without_its_model_file_raises_and_nothing_is_left_running(folder, local_http):
    folder.strong_model.unlink()
    folder.write(strong_required=True)

    with pytest.raises(RequiredModelUnavailable) as raised:
        modelserver.start()

    assert "strong (model_file_missing)" in str(raised.value)
    assert _port_is_free(folder.fast_port) and _port_is_free(folder.strong_port) and _port_is_free(folder.endpoint_port)


def test_a_required_role_that_is_ready_raises_nothing(folder, local_http):
    folder.write(strong_required=True)

    modelserver.start()

    assert local_http.get(folder.url + "/health").json()["roles"]["strong"] == {"state": "ready"}


# -- starting only some of the roles ---------------------------------------------------------------


def test_start_with_roles_launches_only_those_and_keeps_each_on_the_port_of_its_place_in_the_file(folder, local_http):
    modelserver.start(roles={"fast"})

    fast = _launched(local_http, folder.fast_port)
    assert (fast["--model"], fast["--port"]) == (str(folder.fast_model), str(folder.fast_port))
    assert _port_is_free(folder.strong_port)
    assert local_http.get(folder.url + "/health").json() == {"roles": {"fast": {"state": "ready"}}}
    assert [log.name for log in (folder.root / "runs" / "model-server").iterdir()] == ["fast.log"]


def test_a_start_of_the_second_role_alone_serves_it_on_its_own_backend_port_and_leases_only_its_model_file(
    folder, local_http, lease_requests
):
    folder.write(courtesy_lease=True)

    modelserver.start(roles=["strong"])

    assert _launched(local_http, folder.strong_port)["--model"] == str(folder.strong_model)
    assert _port_is_free(folder.fast_port)
    assert [request["model_paths"] for request in lease_requests] == [[folder.strong_model]]


def test_a_required_role_left_out_of_the_start_does_not_stop_it(folder, local_http):
    folder.strong_model.unlink()
    folder.write(strong_required=True)

    modelserver.start(roles={"fast"})

    assert local_http.get(folder.url + "/health").json() == {"roles": {"fast": {"state": "ready"}}}


def test_a_required_role_among_the_started_ones_that_is_down_still_raises_and_nothing_is_left_running(folder):
    folder.strong_model.unlink()
    folder.write(strong_required=True)

    with pytest.raises(RequiredModelUnavailable, match=r"strong \(model_file_missing\)"):
        modelserver.start(roles={"strong", "fast"})

    assert _port_is_free(folder.fast_port) and _port_is_free(folder.strong_port) and _port_is_free(folder.endpoint_port)


def test_a_role_name_the_file_does_not_have_stops_the_start_before_anything_is_launched(folder):
    with pytest.raises(ConfigError, match="has no role small; its roles are fast, strong"):
        modelserver.start(roles={"fast", "small"})

    assert _port_is_free(folder.fast_port) and _port_is_free(folder.endpoint_port)
    assert not (folder.root / "runs").exists()


# -- (6) stop --------------------------------------------------------------------------------------


def test_after_stop_no_stub_process_remains_and_every_port_is_free(folder, local_http):
    modelserver.start()
    stub_pids = [_launched(local_http, port)["pid"] for port in (folder.fast_port, folder.strong_port)]
    assert all(process_is_alive(pid) for pid in stub_pids)

    modelserver.stop()

    assert not any(process_is_alive(pid) for pid in stub_pids)
    assert _port_is_free(folder.fast_port) and _port_is_free(folder.strong_port) and _port_is_free(folder.endpoint_port)


def test_stop_without_a_running_server_and_stop_twice_do_nothing(folder):
    modelserver.stop()
    modelserver.start()

    modelserver.stop()
    modelserver.stop()

    assert _port_is_free(folder.fast_port)


def test_the_server_can_be_started_again_after_it_was_stopped(folder, local_http):
    modelserver.start()
    first_pid = _launched(local_http, folder.fast_port)["pid"]
    modelserver.stop()

    modelserver.start()

    assert _launched(local_http, folder.fast_port)["pid"] != first_pid
    assert local_http.post(folder.url + "/v1/chat/completions", json=CHAT_REQUEST).status_code == 200


def test_starting_a_running_server_again_changes_nothing(folder, local_http):
    modelserver.start()
    stub_pids = [_launched(local_http, port)["pid"] for port in (folder.fast_port, folder.strong_port)]

    modelserver.start()

    assert [_launched(local_http, port)["pid"] for port in (folder.fast_port, folder.strong_port)] == stub_pids


# -- (7) the agent dies ----------------------------------------------------------------------------


def test_killing_the_process_that_started_the_server_leaves_no_stub_process_after_5_seconds(
    folder, local_http, wait_for
):
    environment = {**os.environ, "PYTHONPATH": str(SOURCE_ROOT)}
    helper = subprocess.Popen(
        [sys.executable, "-c", SERVER_HELPER], cwd=folder.root, env=environment, stdout=subprocess.PIPE, text=True
    )
    try:
        # The helper's output ends when it exits, so a helper that fails does not leave this waiting.
        report = next((line for line in helper.stdout if line.startswith("helper-pid")), "")
        assert report, "the helper process did not get its model server started"
        helper_pid = int(report.split()[1])
        stub_pids = [_launched(local_http, port)["pid"] for port in (folder.fast_port, folder.strong_port)]
        assert all(process_is_alive(pid) for pid in stub_pids)

        # Killed, not asked to stop: the helper gets no chance to stop its model server.
        os.kill(helper_pid, signal.SIGTERM if sys.platform == "win32" else signal.SIGKILL)

        wait_for(
            lambda: not any(process_is_alive(pid) for pid in stub_pids),
            "the stub servers of the killed process being ended",
            ORPHAN_LIMIT_SECONDS,
        )
    finally:
        helper.kill()
        helper.wait()


# -- starts that cannot go through -----------------------------------------------------------------


def test_an_endpoint_port_that_is_taken_stops_the_start_before_any_server_is_launched(folder):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", folder.endpoint_port))
        listener.listen()

        with pytest.raises(OSError):
            modelserver.start()

    assert _port_is_free(folder.fast_port) and _port_is_free(folder.strong_port)
    assert not (folder.root / "runs").exists()


def test_an_unusable_configuration_stops_the_start(folder):
    (folder.root / "config" / "models.toml").write_bytes(b"[server]\nhost = '0.0.0.0'\n")

    with pytest.raises(ConfigError, match="IPv4 loopback address"):
        modelserver.start()

    assert _port_is_free(folder.fast_port)


def test_a_start_that_fails_half_way_ends_what_it_had_started(folder, monkeypatch):
    started = []
    real_start = roles.Role.start

    def start_fast_then_fail(role: roles.Role) -> None:
        if started:
            raise KeyboardInterrupt
        real_start(role)
        started.append(role.name)

    monkeypatch.setattr(roles.Role, "start", start_fast_then_fail)

    with pytest.raises(KeyboardInterrupt):
        modelserver.start()

    assert started == ["fast"]
    assert _port_is_free(folder.fast_port) and _port_is_free(folder.endpoint_port)


# -- the courtesy lease ----------------------------------------------------------------------------


@pytest.fixture
def lease_requests(monkeypatch, folder) -> list[dict]:
    """Replaces the courtesy lease with a recorder, so no test ever contacts a real ModelBouncer service."""
    requests = []

    def record(model_paths) -> None:
        requests.append({"model_paths": list(model_paths), "nothing_launched_yet": _port_is_free(folder.fast_port)})

    monkeypatch.setattr(courtesy, "request_lease", record)
    return requests


def test_the_courtesy_lease_is_asked_for_before_any_server_is_launched_when_the_configuration_says_so(
    folder, lease_requests
):
    folder.write(courtesy_lease=True)

    modelserver.start()

    assert lease_requests == [{"model_paths": [folder.fast_model, folder.strong_model], "nothing_launched_yet": True}]


@pytest.mark.parametrize("courtesy_lease", [False, None], ids=["turned off", "not configured"])
def test_no_courtesy_lease_is_asked_for_unless_the_configuration_says_so(folder, lease_requests, courtesy_lease):
    folder.write(courtesy_lease=courtesy_lease)

    modelserver.start()

    assert lease_requests == []


def test_a_lease_step_that_fails_while_measuring_the_model_files_does_not_stop_the_start(
    folder, local_http, monkeypatch, capsys
):
    folder.write(courtesy_lease=True)
    # The real lease step, with a stub standing in for the ModelBouncer service so that none is contacted.
    stub_service = httpx.MockTransport(lambda request: httpx.Response(200, json={"state": "ready"}))
    real_request_lease = courtesy.request_lease
    monkeypatch.setattr(
        courtesy, "request_lease", lambda model_paths: real_request_lease(model_paths, transport=stub_service)
    )

    def vanished(_model_paths) -> int:
        raise FileNotFoundError("the model file was removed while its size was read")

    monkeypatch.setattr(courtesy, "lease_mib", vanished)

    modelserver.start()

    health = local_http.get(folder.url + "/health").json()
    captured = capsys.readouterr()
    assert health == {"roles": {"fast": {"state": "ready"}, "strong": {"state": "ready"}}}
    assert captured.err == (
        "Warning: ModelBouncer did not take the courtesy memory lease: "
        "the lease could not be asked for (FileNotFoundError). The model server starts all the same.\n"
    )
    assert captured.out.splitlines()[:2] == [
        f"Model server: role fast is ready on port {folder.fast_port}.",
        f"Model server: role strong is ready on port {folder.strong_port}.",
    ]


# -- started and stopped by the run session --------------------------------------------------------


def test_the_run_session_starts_the_server_before_play_and_stops_it_after(git_repo, stub_models_config, local_http):
    folder = _ServerFolder(git_repo, stub_models_config)
    (git_repo / "config" / "agent.toml").write_bytes(
        b'[models]\nautostart = true\nautostart_roles = ["fast", "strong"]\n'
    )
    seen = {}

    def play() -> int:
        seen["completion"] = local_http.post(folder.url + "/v1/chat/completions", json=CHAT_REQUEST).status_code
        seen["stub_pids"] = [_launched(local_http, port)["pid"] for port in (folder.fast_port, folder.strong_port)]
        return 0

    try:
        assert run_session(["--match"], play) == 0
    finally:
        modelserver.stop()

    assert seen["completion"] == 200
    assert not any(process_is_alive(pid) for pid in seen["stub_pids"])
    assert _port_is_free(folder.endpoint_port)


# -- the real thing --------------------------------------------------------------------------------


@pytest.mark.gpu
def test_the_real_fast_role_answers_a_completion_within_30_seconds(run_folder, local_http):
    """Loads the shipped `fast` role with the real llama-server.

    Needs the GPU, the model file and the llama-server the shipped
    configuration names, and the ports 11500 and 41500 free. Not part of the
    default run: `pytest -m gpu tests/core/test_modelserver.py`.
    """
    shipped = (REPO_ROOT / "config" / "models.toml").read_text(encoding="utf-8")
    fast_only, strong_table, _rest = shipped.partition("[roles.strong]")
    assert strong_table, "the shipped configuration no longer ends with the strong role"
    (run_folder / "config").mkdir()
    (run_folder / "config" / "models.toml").write_bytes(fast_only.encode("utf-8"))
    request = {
        "model": "fast",
        "messages": [{"role": "user", "content": 'Answer with the JSON object {"choice": "HOLD"}.'}],
        "temperature": 0,
        "max_tokens": 8,
    }

    modelserver.start()
    try:
        health = local_http.get("http://127.0.0.1:11500/health").json()
        assert health == {"roles": {"fast": {"state": "ready"}}}, run_folder / "runs" / "model-server" / "fast.log"
        started = time.monotonic()
        answer = local_http.post(
            "http://127.0.0.1:11500/v1/chat/completions", json=request, timeout=REAL_COMPLETION_LIMIT_SECONDS
        )
        seconds = time.monotonic() - started
    finally:
        modelserver.stop()

    assert answer.status_code == 200 and seconds < REAL_COMPLETION_LIMIT_SECONDS
    assert answer.json()["choices"][0]["message"]["content"].strip()
