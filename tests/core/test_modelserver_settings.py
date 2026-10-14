"""Reading `config/models.toml`: the shipped file, the defaults and every way the file can be unusable."""

from __future__ import annotations

from pathlib import Path

import pytest

from ucla_agent.core.config import ConfigError
from ucla_agent.core.modelserver import settings
from ucla_agent.core.modelserver.settings import ModelServerSettings, RoleSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER = """
[server]
host = "127.0.0.1"
port = 11500
backend_port_base = 41500
"""
FAST_ROLE = """
[roles.fast]
model_path = "C:/Models/gguf/fast.gguf"
required = false
ctx_size = 8192
temperature = 0
reasoning = "off"
"""


def _write(root: Path, text: str) -> Path:
    path = root / "config" / "models.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def _load_error(root: Path, text: str) -> str:
    path = _write(root, text)
    with pytest.raises(ConfigError) as raised:
        settings.load(root)
    assert str(path) in str(raised.value)
    return str(raised.value)


# -- the shipped file ------------------------------------------------------------------------------


def test_the_shipped_file_pins_the_endpoint_and_the_llama_server():
    shipped = settings.load(REPO_ROOT)

    assert (shipped.host, shipped.port, shipped.backend_port_base) == ("127.0.0.1", 11500, 41500)
    assert shipped.llama_server_path == Path("C:/Tools/llama.cpp/llama-server.exe")
    assert shipped.endpoint_url == "http://127.0.0.1:11500"
    assert shipped.modelbouncer_lease is True


def test_the_shipped_file_defines_the_fast_strong_and_small_roles_in_that_order():
    fast, strong, small = settings.load(REPO_ROOT).roles

    assert fast == RoleSettings(
        name="fast",
        model_path=Path("E:/Models/gguf/gemma-4-26B-A4B-it-qat-heretic-UD-Q4_K_XL.gguf"),
        required=False,
        ctx_size=8192,
        n_gpu_layers=999,
        parallel=1,
        temperature=0,
        top_k=64,
        top_p=0.95,
        seed=0,
        reasoning="off",
        extra_args=("--no-mmap",),
    )
    assert strong == RoleSettings(
        name="strong",
        model_path=Path("E:/Models/gguf/Qwen3.8-27B-Q8_0.gguf"),
        required=False,
        ctx_size=16384,
        n_gpu_layers=999,
        parallel=1,
        temperature=0,
        top_k=20,
        top_p=0.95,
        seed=0,
        reasoning="off",
        extra_args=("--no-mmap",),
    )
    assert small == RoleSettings(
        name="small",
        model_path=Path("C:/Models/gguf/Qwen3.5-4B-Q8_0.gguf"),
        required=False,
        ctx_size=8192,
        n_gpu_layers=999,
        parallel=1,
        temperature=0,
        top_k=20,
        top_p=0.8,
        seed=0,
        reasoning="off",
        extra_args=("--no-mmap",),
    )


def test_the_shipped_small_role_is_optional_and_served_on_port_41502():
    shipped = settings.load(REPO_ROOT)

    ports = {role.name: shipped.backend_port(index) for index, role in enumerate(shipped.roles)}
    small = next(role for role in shipped.roles if role.name == "small")

    assert ports == {"fast": 41500, "strong": 41501, "small": 41502}
    assert small.required is False


# -- defaults and order ----------------------------------------------------------------------------


def test_options_the_file_leaves_out_get_their_defaults(tmp_path):
    _write(tmp_path, SERVER + FAST_ROLE)

    loaded = settings.load(tmp_path)

    assert loaded == ModelServerSettings(
        host="127.0.0.1",
        port=11500,
        llama_server_path=Path("C:/Tools/llama.cpp/llama-server.exe"),
        backend_port_base=41500,
        modelbouncer_lease=False,
        roles=(
            RoleSettings(
                name="fast",
                model_path=Path("C:/Models/gguf/fast.gguf"),
                required=False,
                ctx_size=8192,
                n_gpu_layers=999,
                parallel=1,
                temperature=0,
                top_k=None,
                top_p=None,
                seed=None,
                reasoning="off",
                extra_args=(),
            ),
        ),
    )


def test_every_option_of_a_role_is_read_as_written(tmp_path):
    role = """
[roles.strong]
model_path = 'C:\\Models\\gguf\\strong.gguf'
required = true
ctx_size = 16384
n_gpu_layers = 40
parallel = 2
temperature = 0.7
top_k = 20
top_p = 0.8
seed = 42
reasoning = "on"
extra_args = ["--kv-unified", "--no-mmap"]
"""
    _write(tmp_path, SERVER + 'llama_server_path = "D:/llama/llama-server.exe"\n' + role)

    loaded = settings.load(tmp_path)

    assert loaded.llama_server_path == Path("D:/llama/llama-server.exe")
    assert loaded.roles == (
        RoleSettings(
            name="strong",
            model_path=Path("C:\\Models\\gguf\\strong.gguf"),
            required=True,
            ctx_size=16384,
            n_gpu_layers=40,
            parallel=2,
            temperature=0.7,
            top_k=20,
            top_p=0.8,
            seed=42,
            reasoning="on",
            extra_args=("--kv-unified", "--no-mmap"),
        ),
    )


def test_roles_keep_the_order_of_the_file_and_get_consecutive_backend_ports(tmp_path):
    _write(tmp_path, SERVER + FAST_ROLE.replace("fast", "zulu") + FAST_ROLE.replace("fast", "alpha"))

    loaded = settings.load(tmp_path)

    assert [role.name for role in loaded.roles] == ["zulu", "alpha"]
    assert [loaded.backend_port(index) for index in range(len(loaded.roles))] == [41500, 41501]


def test_a_file_without_roles_has_none(tmp_path):
    _write(tmp_path, SERVER)

    assert settings.load(tmp_path).roles == ()


def test_the_courtesy_lease_is_on_only_when_the_file_says_so(tmp_path):
    _write(tmp_path, SERVER + "\n[courtesy]\nmodelbouncer_lease = true\n")

    assert settings.load(tmp_path).modelbouncer_lease is True


# -- a file that cannot be used --------------------------------------------------------------------


def test_a_missing_file_says_where_to_start_the_agent(tmp_path):
    with pytest.raises(ConfigError) as raised:
        settings.load(tmp_path)

    assert str(tmp_path / "config" / "models.toml") in str(raised.value)
    assert "Start the agent from the repository root" in str(raised.value)


def test_invalid_toml_is_reported_with_the_file(tmp_path):
    assert "not valid TOML" in _load_error(tmp_path, "[server\nport = 11500\n")


@pytest.mark.parametrize(
    "text, table",
    [
        (FAST_ROLE, "server"),
        ("server = 3\n", "server"),
        ("roles = 3\n" + SERVER, "roles"),
        ("courtesy = true\n" + SERVER, "courtesy"),
        (SERVER + "\n[roles]\nfast = 3\n", "roles.fast"),
    ],
    ids=["no server table", "server is a value", "roles is a value", "courtesy is a value", "a role is a value"],
)
def test_each_part_of_the_file_must_be_a_table(tmp_path, text, table):
    assert f"must have a [{table}] table" in _load_error(tmp_path, text)


@pytest.mark.parametrize("key", ["host", "port", "backend_port_base"])
def test_the_server_table_must_have_each_key_without_a_default(tmp_path, key):
    without_key = "\n".join(line for line in SERVER.splitlines() if not line.startswith(key))

    assert f"{key} in [server] must be" in _load_error(tmp_path, without_key)


@pytest.mark.parametrize("key", ["model_path", "required", "ctx_size", "temperature", "reasoning"])
def test_a_role_must_have_each_key_without_a_default(tmp_path, key):
    without_key = "\n".join(line for line in FAST_ROLE.splitlines() if not line.startswith(key))

    assert f"{key} in [roles.fast] must be" in _load_error(tmp_path, SERVER + without_key)


@pytest.mark.parametrize(
    "line, expected",
    [
        ("model_path = 7", "model_path in [roles.fast] must be a string"),
        ('required = "no"', "required in [roles.fast] must be true or false"),
        ("ctx_size = 0", "ctx_size in [roles.fast] must be a whole number of at least 1"),
        ('ctx_size = "8192"', "ctx_size in [roles.fast] must be a whole number of at least 1"),
        ("ctx_size = true", "ctx_size in [roles.fast] must be a whole number of at least 1"),
        ("ctx_size = 8192.0", "ctx_size in [roles.fast] must be a whole number of at least 1"),
        ("n_gpu_layers = -1", "n_gpu_layers in [roles.fast] must be a whole number of at least 0"),
        ("parallel = 0", "parallel in [roles.fast] must be a whole number of at least 1"),
        ("temperature = -0.1", "temperature in [roles.fast] must be a number of at least 0"),
        ("temperature = false", "temperature in [roles.fast] must be a number of at least 0"),
        ('top_p = "high"', "top_p in [roles.fast] must be a number of at least 0"),
        ("top_k = 1.5", "top_k in [roles.fast] must be a whole number of at least 0"),
        ('seed = "random"', "seed in [roles.fast] must be a whole number."),
        ('reasoning = "auto"', 'reasoning in [roles.fast] must be "off" or "on"'),
        ("reasoning = true", 'reasoning in [roles.fast] must be "off" or "on"'),
        ('extra_args = "--no-mmap"', "extra_args in [roles.fast] must be a list of strings"),
        ("extra_args = [1, 2]", "extra_args in [roles.fast] must be a list of strings"),
    ],
)
def test_a_role_value_of_the_wrong_kind_is_reported_with_its_table_and_key(tmp_path, line, expected):
    key = line.split(" = ")[0]
    others = "\n".join(text for text in FAST_ROLE.splitlines() if not text.startswith(key))

    assert expected in _load_error(tmp_path, SERVER + others + "\n" + line + "\n")


@pytest.mark.parametrize(
    "line, expected",
    [
        ("port = 0", "port in [server] must be a whole number of at least 1 and at most 65535"),
        ("port = 70000", "port in [server] must be a whole number of at least 1 and at most 65535"),
        ('backend_port_base = "41500"', "backend_port_base in [server] must be a whole number of at least 1"),
        ("llama_server_path = 3", "llama_server_path in [server] must be a string"),
    ],
)
def test_a_server_value_of_the_wrong_kind_is_reported(tmp_path, line, expected):
    key = line.split(" = ")[0]
    others = "\n".join(text for text in SERVER.splitlines() if not text.startswith(key))

    assert expected in _load_error(tmp_path, others + "\n" + line + "\n")


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "localhost", "::1", ""])
def test_the_endpoint_host_must_be_an_ipv4_loopback_address(tmp_path, host):
    message = _load_error(tmp_path, SERVER.replace("127.0.0.1", host))

    assert "host in [server] must be an IPv4 loopback address" in message


def test_another_loopback_address_is_accepted(tmp_path):
    _write(tmp_path, SERVER.replace("127.0.0.1", "127.0.0.2"))

    assert settings.load(tmp_path).endpoint_url == "http://127.0.0.2:11500"


@pytest.mark.parametrize(
    "text, expected",
    [
        (SERVER + "\n[queue]\nsize = 4\n", "the file has no setting named queue"),
        (SERVER + "priority = 5\n", "[server] has no setting named priority"),
        (SERVER + FAST_ROLE + "ctx_sise = 4096\n", "[roles.fast] has no setting named ctx_sise"),
        (SERVER + "\n[courtesy]\nlease = true\n", "[courtesy] has no setting named lease"),
    ],
    ids=["table", "server key", "role key", "courtesy key"],
)
def test_a_key_the_server_does_not_know_is_an_error(tmp_path, text, expected):
    assert expected in _load_error(tmp_path, text)
