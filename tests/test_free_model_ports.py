"""The ports and the test copy of `config/models.toml` that every test starting a model server uses.

The live agent's model server holds the shipped ports (11500, and 41500 up
for its roles) while the suite runs, so a test's own server goes elsewhere.
"""

from __future__ import annotations

import random
import socket
import tomllib
from pathlib import Path

import pytest

from ucla_agent.core.modelserver import settings

LOOPBACK = "127.0.0.1"
SHIPPED_ENDPOINT_PORT = 11500
SHIPPED_BACKEND_PORTS = range(41500, 41600)
SHIPPED_ROLES = ["fast", "strong", "small"]
STUB_PATH = Path("C:/stubs/llama.py")
# The rest of a role's table, after its model_path, for a copy that names its own roles.
STUB_ROLE = 'required = false\nctx_size = 8192\ntemperature = 0\nreasoning = "off"\n'


def _bindable(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((LOOPBACK, port))
        except OSError:
            return False
    return True


def _scripted_draws(monkeypatch: pytest.MonkeyPatch, first_draws: list[int]) -> list[int]:
    """Makes the port picker draw `first_draws` first and at random after; gives every value drawn."""
    real_randrange = random.Random.randrange
    drawn: list[int] = []

    def draw(picker: random.Random, start: int, stop: int) -> int:
        value = first_draws.pop(0) if first_draws else real_randrange(picker, start, stop)
        drawn.append(value)
        return value

    monkeypatch.setattr(random.Random, "randrange", draw)
    return drawn


# -- model_ports -----------------------------------------------------------------------------------


def test_the_endpoint_and_the_three_backend_ports_are_free_different_and_none_is_a_shipped_port(model_ports):
    backend = [model_ports.backend_port(index) for index in range(len(SHIPPED_ROLES))]
    picked = [model_ports.endpoint, *backend]

    assert backend == [model_ports.backend_port_base + index for index in range(3)]
    assert len(set(picked)) == 4
    assert SHIPPED_ENDPOINT_PORT not in picked and not set(picked) & set(SHIPPED_BACKEND_PORTS)
    assert all(_bindable(port) for port in picked)


def test_the_endpoint_url_and_the_server_table_name_the_picked_ports(model_ports):
    server = tomllib.loads(model_ports.server_table(STUB_PATH))["server"]

    assert model_ports.endpoint_url == f"http://127.0.0.1:{model_ports.endpoint}"
    assert server == {
        "host": LOOPBACK,
        "port": model_ports.endpoint,
        "llama_server_path": "C:/stubs/llama.py",
        "backend_port_base": model_ports.backend_port_base,
    }


def test_a_pick_with_a_taken_port_is_drawn_again(monkeypatch, request):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind((LOOPBACK, 0))
        taken_port = taken.getsockname()[1]
        drawn = _scripted_draws(monkeypatch, [taken_port, 20000])

        model_ports = request.getfixturevalue("model_ports")

    assert drawn[:2] == [taken_port, 20000] and len(drawn) >= 4
    assert model_ports.endpoint != taken_port


def test_an_endpoint_among_the_backend_ports_is_drawn_again(monkeypatch, request):
    drawn = _scripted_draws(monkeypatch, [25001, 25000])

    model_ports = request.getfixturevalue("model_ports")

    assert drawn[:2] == [25001, 25000] and len(drawn) >= 4
    assert model_ports.endpoint not in range(model_ports.backend_port_base, model_ports.backend_port_base + 3)


def test_no_free_ports_after_every_try_fails_the_test_that_asked(monkeypatch, request):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind((LOOPBACK, 0))
        monkeypatch.setattr(random.Random, "randrange", lambda _picker, _start, _stop: taken.getsockname()[1])

        with pytest.raises(pytest.fail.Exception, match=r"no free model server ports in range\(20000, 32768\)"):
            request.getfixturevalue("model_ports")


# -- stub_models_config ----------------------------------------------------------------------------


def test_the_default_copy_serves_the_three_shipped_roles_on_the_picked_ports_with_the_stub(
    tmp_path, stub_models_config, model_ports, llama_stub
):
    returned = stub_models_config(tmp_path)

    loaded = settings.load(tmp_path)
    assert returned == model_ports
    assert (loaded.endpoint_url, loaded.llama_server_path) == (model_ports.endpoint_url, llama_stub)
    assert [role.name for role in loaded.roles] == SHIPPED_ROLES
    assert [loaded.backend_port(index) for index in range(3)] == [model_ports.backend_port(index) for index in range(3)]
    assert all(role.extra_args == ("--no-mmap",) and not role.required for role in loaded.roles)
    assert [role.model_path for role in loaded.roles] == [
        tmp_path / "models" / f"{name}.gguf" for name in SHIPPED_ROLES
    ]
    assert [role.model_path.read_bytes() for role in loaded.roles] == [b"GGUF fast", b"GGUF strong", b"GGUF small"]
    assert not loaded.modelbouncer_lease


def test_a_copy_with_its_own_roles_and_extra_tables_keeps_them_in_order(tmp_path, stub_models_config, model_ports):
    stub_models_config(
        tmp_path,
        {"strong": 'required = true\nctx_size = 16384\ntemperature = 0\nreasoning = "off"\n', "fast": STUB_ROLE},
        extra_tables="[courtesy]\nmodelbouncer_lease = true\n",
    )

    loaded = settings.load(tmp_path)
    assert [(role.name, role.required, role.ctx_size) for role in loaded.roles] == [
        ("strong", True, 16384),
        ("fast", False, 8192),
    ]
    assert loaded.modelbouncer_lease and loaded.port == model_ports.endpoint


def test_a_model_file_the_test_removed_stays_removed_when_the_copy_is_written_again(tmp_path, stub_models_config):
    stub_models_config(tmp_path)
    (tmp_path / "models" / "strong.gguf").unlink()

    stub_models_config(tmp_path, {"fast": STUB_ROLE, "strong": STUB_ROLE.replace("false", "true")})

    assert sorted(path.name for path in (tmp_path / "models").iterdir()) == ["fast.gguf", "small.gguf"]
    assert [role.required for role in settings.load(tmp_path).roles] == [False, True]


def test_the_copy_is_written_beside_the_rest_of_a_copied_config_folder(tmp_path, stub_models_config):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agent.toml").write_bytes(b"[models]\nautostart = false\n")

    stub_models_config(tmp_path)

    assert sorted(path.name for path in (tmp_path / "config").iterdir()) == ["agent.toml", "models.toml"]
