"""The model server's systemone roles: `[systemone_roles.<name>]` read from config/models.toml, a native server started
from its own Python with its own environment, ready only after `/healthz` and a warm-up choice, and stopped with the
server; the stub stands in for the native server and runs as a real process on the free ports of `model_ports`."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from ucla_agent.core import modelserver
from ucla_agent.core.config import ConfigError
from ucla_agent.core.instance_lock import process_is_alive
from ucla_agent.core.modelserver import RequiredModelUnavailable, systemone
from ucla_agent.core.modelserver import settings as server_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
STUB = REPO_ROOT / "tests" / "stubs" / "fake_systemone_server.py"
SERVE_SCRIPT = REPO_ROOT / "scripts" / "serve_jev9b.py"
LLAMA_TABLE = 'required = false\nctx_size = 8192\ntemperature = 0\nreasoning = "off"\n'
# The stub's model load and its first answer, which records the CUDA graphs of a real server.
LOAD_SECONDS = 1.0
WARM_SECONDS = 1.5


def _systemone_table(
    model_dir: Path,
    *,
    python: Path = Path(sys.executable),
    script: Path = STUB,
    required: bool = False,
    startup_seconds: int = 30,
    env: dict[str, str] | None = None,
) -> str:
    lines = [
        "[systemone_roles.jev9b]",
        f'python_path = "{python.as_posix()}"',
        f'script = "{script.as_posix()}"',
        f'model_dir = "{model_dir.as_posix()}"',
        f"required = {str(required).lower()}",
        f"startup_seconds = {startup_seconds}",
        "",
        "[systemone_roles.jev9b.env]",
        *(f'{name} = "{value}"' for name, value in (env or {}).items()),
    ]
    return "\n".join(lines) + "\n"


@pytest.fixture
def native_folder(run_folder: Path, stub_models_config: Callable[..., Any]) -> Any:
    """Writes a configuration of the llama role fast (the llama stub) and the systemone role jev9b (the systemone
    stub) into the current directory; gives the writer, which returns the ports. The server is stopped afterwards."""
    model_dir = run_folder / "JevK5-9B"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(b"weights")

    def write(**options: Any) -> Any:
        return stub_models_config(
            run_folder, {"fast": LLAMA_TABLE}, extra_tables=_systemone_table(model_dir, **options)
        )

    write.model_dir = model_dir
    yield write
    modelserver.stop()


def _launched(local_http: httpx.Client, port: int) -> dict:
    return local_http.get(f"http://127.0.0.1:{port}/stub/launch").json()


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


# -- the settings ----------------------------------------------------------------------------------


def test_a_systemone_role_is_read_with_its_paths_env_and_a_slot_after_the_llama_roles(native_folder, run_folder):
    ports = native_folder(env={"JEVK5_GRAPHS": "1", "HF_HOME": "E:/Models/.hf-native"})

    loaded = server_settings.load(run_folder)

    (role,) = loaded.systemone_roles
    assert [settings.name for settings in loaded.roles] == ["fast"]
    assert [settings.name for settings in loaded.every_role] == ["fast", "jev9b"]
    assert (role.python_path, role.script, role.model_dir) == (Path(sys.executable), STUB, native_folder.model_dir)
    assert (role.required, role.startup_seconds, role.slot) == (False, 30, 1)
    assert role.env == {"JEVK5_GRAPHS": "1", "HF_HOME": "E:/Models/.hf-native"}
    assert loaded.systemone_port(role) == ports.backend_port(1)
    assert loaded.systemone_url(role) == f"http://127.0.0.1:{ports.backend_port(1)}"
    assert role.weight_files() == [native_folder.model_dir / "model.safetensors"]


def test_a_relative_script_is_read_from_the_repository_root_and_startup_seconds_defaults_to_600(run_folder):
    config = run_folder / "config"
    config.mkdir()
    (config / "models.toml").write_text(
        '[server]\nhost = "127.0.0.1"\nport = 21000\nbackend_port_base = 21001\n\n'
        '[systemone_roles.jev9b]\npython_path = "C:/native/python.exe"\nscript = "scripts/serve_jev9b.py"\n'
        'model_dir = "E:/Models/JevK5-9B"\nrequired = false\n',
        encoding="utf-8",
    )

    (role,) = server_settings.load(run_folder).systemone_roles

    assert (role.script, role.startup_seconds, role.env, role.slot) == (
        run_folder / "scripts" / "serve_jev9b.py",
        600,
        {},
        0,
    )


@pytest.mark.parametrize(
    "extra, expected",
    [
        ("port = 8765\n", "has no setting named port"),
        ("startup_seconds = 0\n", "startup_seconds in [systemone_roles.jev9b] must be a whole number of at least 1"),
    ],
    ids=["unknown key", "no startup time"],
)
def test_a_systemone_role_is_read_strictly(native_folder, run_folder, extra, expected):
    native_folder()
    path = run_folder / "config" / "models.toml"
    text = path.read_text(encoding="utf-8").replace("startup_seconds = 30\n", extra)
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ConfigError, match=expected.replace("[", r"\[").replace("]", r"\]")):
        server_settings.load(run_folder)


def test_a_name_both_kinds_of_role_use_is_refused(native_folder, run_folder):
    native_folder()
    path = run_folder / "config" / "models.toml"
    path.write_text(path.read_text(encoding="utf-8").replace("[roles.fast]", "[roles.jev9b]"), encoding="utf-8")

    with pytest.raises(ConfigError, match="both name jev9b"):
        server_settings.load(run_folder)


def test_the_shipped_jev9b_role_is_optional_on_port_41503_and_is_what_starts_beside_strong(monkeypatch):
    monkeypatch.delenv(server_settings.BACKEND_PORT_BASE_ENV, raising=False)
    shipped = server_settings.load(REPO_ROOT)
    agent_toml = (REPO_ROOT / "config" / "agent.toml").read_text(encoding="utf-8")

    (role,) = shipped.systemone_roles
    assert (role.name, role.required, role.slot, shipped.systemone_port(role)) == ("jev9b", False, 3, 41503)
    assert role.script == REPO_ROOT / "scripts" / "serve_jev9b.py" and role.script.is_file()
    assert set(role.env) == {"PYTORCH_CUDA_ALLOC_CONF", "HF_HOME", "TRITON_CACHE_DIR", "JEVK5_GRAPHS"}
    assert 'autostart_roles = ["strong", "jev9b"]' in agent_toml
    assert [settings.name for settings in shipped.roles] == ["fast", "strong", "small"]


# -- starting and stopping -------------------------------------------------------------------------


def test_a_named_systemone_role_is_started_with_its_command_line_and_env_and_stopped_with_the_server(
    native_folder, local_http, capsys
):
    ports = native_folder(env={"STUB_ROLE_MARK": "jev9b-env", "STUB_ECHO": "HF_HOME", "HF_HOME": "E:/hf"})
    port = ports.backend_port(1)

    modelserver.start(["jev9b"])

    launch = _launched(local_http, port)
    arguments = dict(zip(launch["arguments"][0::2], launch["arguments"][1::2]))
    assert arguments == {"--model-dir": str(native_folder.model_dir), "--host": "127.0.0.1", "--port": str(port)}
    assert launch["environment"]["STUB_ROLE_MARK"] == "jev9b-env"
    assert launch["environment"]["HF_HOME"] == "E:/hf"
    assert capsys.readouterr().out.splitlines()[0] == f"Model server: role jev9b is ready on port {port}."
    health = local_http.get(ports.endpoint_url + "/health").json()
    assert health == {"roles": {"jev9b": {"state": "ready"}}}

    modelserver.stop()

    assert not process_is_alive(launch["pid"]) and _port_is_free(port)


def test_the_role_is_ready_only_after_healthz_and_one_warm_up_choice_of_a_slow_to_warm_server(
    native_folder, local_http, monkeypatch
):
    ports = native_folder()
    monkeypatch.setenv("STUB_LOAD_SECONDS", str(LOAD_SECONDS))
    monkeypatch.setenv("STUB_WARM_SECONDS", str(WARM_SECONDS))
    started = time.perf_counter()

    modelserver.start(["jev9b"])

    took = time.perf_counter() - started
    requests = local_http.get(f"http://127.0.0.1:{ports.backend_port(1)}/stub/requests").json()["requests"]
    assert took >= LOAD_SECONDS + WARM_SECONDS
    assert requests == [systemone.WARM_UP_BODY]
    assert local_http.get(ports.endpoint_url + "/health").json()["roles"]["jev9b"] == {"state": "ready"}


def test_a_systemone_role_answers_no_chat_completion_through_the_endpoint(native_folder, local_http):
    ports = native_folder()

    modelserver.start(["fast", "jev9b"])

    refused = local_http.post(ports.endpoint_url + "/v1/chat/completions", json={"model": "jev9b", "messages": []})
    models = local_http.get(ports.endpoint_url + "/v1/models").json()
    assert (refused.status_code, refused.json()) == (404, {"error": "unknown_role"})
    assert [model["id"] for model in models["data"]] == ["fast", "jev9b"]


def test_jev9b_is_not_started_when_it_is_not_named(native_folder, local_http):
    ports = native_folder()

    modelserver.start(["fast"])

    assert local_http.get(ports.endpoint_url + "/health").json() == {"roles": {"fast": {"state": "ready"}}}
    assert _port_is_free(ports.backend_port(1))


def test_an_unknown_role_name_lists_the_systemone_roles_too(native_folder):
    native_folder()

    with pytest.raises(ConfigError, match="its roles are fast, jev9b"):
        modelserver.start(["jev27b"])


@pytest.mark.parametrize("missing", ["python", "script", "model_dir"])
def test_a_missing_path_leaves_the_role_down_naming_it_and_the_server_runs_without_it(
    native_folder, run_folder, local_http, capsys, missing
):
    absent = run_folder / "absent"
    options = {"python": {"python": absent}, "script": {"script": absent}, "model_dir": {}}[missing]
    ports = native_folder(**options)
    if missing == "model_dir":
        for child in native_folder.model_dir.iterdir():
            child.unlink()
        native_folder.model_dir.rmdir()
    path = native_folder.model_dir if missing == "model_dir" else absent
    reason = f"{missing}_missing: {path.as_posix()}"

    modelserver.start(["fast", "jev9b"])

    health = local_http.get(ports.endpoint_url + "/health").json()["roles"]
    assert health == {"fast": {"state": "ready"}, "jev9b": {"state": "down", "reason": reason}}
    assert f"Model server: role jev9b is down ({reason})." in capsys.readouterr().out.splitlines()


def test_a_server_that_does_not_answer_healthz_within_startup_seconds_is_ended_and_down(
    native_folder, local_http, monkeypatch
):
    ports = native_folder(startup_seconds=1)
    monkeypatch.setenv("STUB_LOAD_SECONDS", "5")

    modelserver.start(["jev9b"])

    jev9b = local_http.get(ports.endpoint_url + "/health").json()["roles"]["jev9b"]
    assert jev9b == {"state": "down", "reason": "not_ready_in_time"}
    assert _port_is_free(ports.backend_port(1))


@pytest.mark.parametrize(
    "variable, value, reason", [("STUB_EXIT_CODE", "3", "exited"), ("STUB_STATUS", "422", "warm_up_failed")]
)
def test_a_server_that_exits_or_refuses_its_warm_up_is_down(
    native_folder, local_http, monkeypatch, variable, value, reason
):
    ports = native_folder()
    monkeypatch.setenv(variable, value)

    modelserver.start(["jev9b"])

    assert local_http.get(ports.endpoint_url + "/health").json()["roles"]["jev9b"] == {
        "state": "down",
        "reason": reason,
    }


def test_a_required_systemone_role_that_is_down_stops_the_server(native_folder, run_folder):
    ports = native_folder(required=True, python=run_folder / "absent")

    with pytest.raises(RequiredModelUnavailable, match="jev9b"):
        modelserver.start(["fast", "jev9b"])

    assert _port_is_free(ports.endpoint) and _port_is_free(ports.backend_port(0))


def test_the_courtesy_lease_covers_the_systemone_role_s_weights(native_folder, run_folder, monkeypatch):
    native_folder()
    path = run_folder / "config" / "models.toml"
    path.write_text(path.read_text(encoding="utf-8") + "\n[courtesy]\nmodelbouncer_lease = true\n", encoding="utf-8")
    leased: list[list[Path]] = []
    monkeypatch.setattr(modelserver.courtesy, "request_lease", leased.append)

    modelserver.start(["fast", "jev9b"])

    assert leased == [[run_folder / "models" / "fast.gguf", native_folder.model_dir / "model.safetensors"]]


# -- the serve script ------------------------------------------------------------------------------


def test_the_serve_command_gives_the_script_the_model_folder_and_the_loopback_port():
    role = server_settings.SystemoneRoleSettings(
        "jev9b",
        Path("C:/native/python.exe"),
        Path("scripts/serve_jev9b.py"),
        Path("E:/Models/JevK5-9B"),
        False,
        600,
        {},
        3,
    )

    assert systemone.serve_command(role, 41503) == [
        str(Path("C:/native/python.exe")),
        str(Path("scripts/serve_jev9b.py")),
        "--model-dir",
        str(Path("E:/Models/JevK5-9B")),
        "--host",
        "127.0.0.1",
        "--port",
        "41503",
    ]


def test_the_serve_script_holds_no_machine_path_and_names_its_sources():
    text = SERVE_SCRIPT.read_text(encoding="utf-8")

    assert not any(marker in text for marker in ("C:/", "C:\\", "E:/", "E:\\", "Users", "/home/"))
    for source in ("serve-native-cascade.py", "jev-style", "allebee/jevk5", "Apache-2.0", "JevK5-9B"):
        assert source in text


def test_the_serve_script_refuses_a_missing_model_folder_before_loading_anything(tmp_path, model_ports):
    completed = subprocess.run(
        [
            sys.executable,
            str(SERVE_SCRIPT),
            "--model-dir",
            str(tmp_path / "absent"),
            "--port",
            str(model_ports.endpoint),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 1
    assert "does not exist" in completed.stderr


class _NativeRuntime:
    """Stands in for the native runtime the serve script imports: torch, uvicorn and jev-style's JevK5 engine."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, cuda: bool = True, graphs: int = 13) -> None:
        self.loaded: list[tuple[str, bool]] = []
        self.served: list[tuple[Any, str, int, str]] = []
        torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(
                is_available=lambda: cuda, get_device_name=lambda index: "Fake GPU", memory_reserved=lambda: 20 * 2**30
            )
        )
        uvicorn = types.SimpleNamespace(
            run=lambda app, host, port, log_level: self.served.append((app, host, port, log_level))
        )
        engine_module = types.ModuleType("jev_style.jevk5_engine")
        engine_module.load_jevk5 = self._load
        server_module = types.ModuleType("jev_style.server")
        server_module.create_app = lambda engine: ("app", engine.model_id)
        self._graphs = graphs
        for name, module in (
            ("torch", torch),
            ("uvicorn", uvicorn),
            ("jev_style", types.ModuleType("jev_style")),
            ("jev_style.jevk5_engine", engine_module),
            ("jev_style.server", server_module),
        ):
            monkeypatch.setitem(sys.modules, name, module)
        for name in ("PYTORCH_CUDA_ALLOC_CONF", "JEVK5_GRAPHS"):
            monkeypatch.delenv(name, raising=False)

    def _load(self, source: str, verify: bool) -> Any:
        self.loaded.append((source, verify))
        model = types.SimpleNamespace(graphs=dict.fromkeys(range(self._graphs)))
        return types.SimpleNamespace(model=model, model_id=source, backend="jevk5-cuda")


def test_the_serve_script_loads_the_9b_checked_with_graphs_prints_what_it_loaded_and_serves_it(
    load_script, monkeypatch, tmp_path, model_ports, capsys
):
    runtime = _NativeRuntime(monkeypatch)
    script = load_script("serve_jev9b.py")

    code = script.main(["--model-dir", str(tmp_path), "--port", str(model_ports.endpoint)])

    # The script logs to standard error; the model server appends both of its streams to the role's log.
    loaded = json.loads(capsys.readouterr().err)
    assert code == 0 and runtime.loaded == [(str(tmp_path), True)]
    assert runtime.served == [(("app", str(tmp_path)), "127.0.0.1", model_ports.endpoint, "warning")]
    assert (loaded["cuda_graphs"], loaded["gpu"], loaded["cuda_memory_reserved_gib"]) == (13, "Fake GPU", 20.0)
    assert loaded["api"] == f"http://127.0.0.1:{model_ports.endpoint}/v1/systemone"
    assert (os.environ["PYTORCH_CUDA_ALLOC_CONF"], os.environ["JEVK5_GRAPHS"]) == ("expandable_segments:False", "1")


@pytest.mark.parametrize(
    "runtime_options, message",
    [({"cuda": False}, "needs CUDA"), ({"graphs": 0}, "recorded no CUDA graphs")],
    ids=["no CUDA", "no graphs"],
)
def test_the_serve_script_refuses_to_serve_without_cuda_or_cuda_graphs(
    load_script, monkeypatch, tmp_path, model_ports, capsys, runtime_options, message
):
    runtime = _NativeRuntime(monkeypatch, **runtime_options)

    code = load_script("serve_jev9b.py").main(["--model-dir", str(tmp_path), "--port", str(model_ports.endpoint)])

    assert code == 1 and message in capsys.readouterr().err and runtime.served == []


def test_the_serve_script_refuses_a_port_something_already_listens_on(
    load_script, monkeypatch, tmp_path, model_ports, capsys
):
    runtime = _NativeRuntime(monkeypatch)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", model_ports.endpoint))
        taken.listen()

        code = load_script("serve_jev9b.py").main(["--model-dir", str(tmp_path), "--port", str(model_ports.endpoint)])

    assert code == 1 and "already has a listening service" in capsys.readouterr().err and runtime.loaded == []
