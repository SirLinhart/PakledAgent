"""The command line that starts one role's llama-server."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

from ucla_agent.core.modelserver import command, settings
from ucla_agent.core.modelserver.settings import RoleSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = Path("C:/Tools/llama.cpp/llama-server.exe")
ROLE = RoleSettings(
    name="strong",
    model_path=Path("C:/Models/gguf/strong model.gguf"),
    required=True,
    ctx_size=16384,
    n_gpu_layers=40,
    parallel=2,
    temperature=0.7,
    top_k=20,
    top_p=0.8,
    seed=42,
    reasoning="on",
    extra_args=("--kv-unified", "--ctx-size", "4096"),
)


def test_every_pinned_option_is_stated_in_a_fixed_order():
    assert command.build(SERVER_PATH, ROLE, 41501) == [
        str(SERVER_PATH),
        "--model",
        str(Path("C:/Models/gguf/strong model.gguf")),
        "--host",
        "127.0.0.1",
        "--port",
        "41501",
        "--ctx-size",
        "16384",
        "--n-gpu-layers",
        "40",
        "--parallel",
        "2",
        "--temp",
        "0.7",
        "--top-k",
        "20",
        "--top-p",
        "0.8",
        "--seed",
        "42",
        "--reasoning",
        "on",
        "--kv-unified",
        "--ctx-size",
        "4096",
    ]


def test_a_model_path_with_a_space_stays_one_argument():
    arguments = command.build(SERVER_PATH, ROLE, 41501)

    assert arguments[arguments.index("--model") + 1] == str(Path("C:/Models/gguf/strong model.gguf"))


def test_options_the_role_leaves_out_are_not_on_the_line():
    role = dataclasses.replace(ROLE, top_k=None, top_p=None, seed=None, extra_args=())

    arguments = command.build(SERVER_PATH, role, 41500)

    assert not {"--top-k", "--top-p", "--seed"} & set(arguments)
    assert arguments[-4:] == ["--temp", "0.7", "--reasoning", "on"]


def test_a_value_of_zero_is_stated_and_not_left_out():
    role = dataclasses.replace(ROLE, temperature=0, top_k=0, top_p=0.0, seed=0, n_gpu_layers=0)

    arguments = command.build(SERVER_PATH, role, 41500)

    stated = {flag: arguments[arguments.index(flag) + 1] for flag in ("--temp", "--top-k", "--top-p", "--seed")}
    assert stated == {"--temp": "0", "--top-k": "0", "--top-p": "0.0", "--seed": "0"}
    assert arguments[arguments.index("--n-gpu-layers") + 1] == "0"


def test_a_server_that_is_a_python_script_is_run_with_this_interpreter():
    script = Path("tests/stubs/fake_llama_server.py")

    arguments = command.build(script, ROLE, 41500)

    assert arguments[:3] == [sys.executable, str(script), "--model"]


def test_the_shipped_fast_role_gives_the_line_the_configuration_pins():
    shipped = settings.load(REPO_ROOT)
    fast = shipped.roles[0]

    assert command.build(shipped.llama_server_path, fast, shipped.backend_port(0)) == [
        str(Path("C:/Tools/llama.cpp/llama-server.exe")),
        "--model",
        str(Path("E:/Models/gguf/gemma-4-26B-A4B-it-qat-heretic-UD-Q4_K_XL.gguf")),
        "--host",
        "127.0.0.1",
        "--port",
        "41500",
        "--ctx-size",
        "8192",
        "--n-gpu-layers",
        "999",
        "--parallel",
        "1",
        "--temp",
        "0",
        "--top-k",
        "64",
        "--top-p",
        "0.95",
        "--seed",
        "0",
        "--reasoning",
        "off",
        "--no-mmap",
    ]


def test_every_shipped_role_loads_its_model_without_memory_mapping():
    shipped = settings.load(REPO_ROOT)

    endings = {
        role.name: command.build(shipped.llama_server_path, role, shipped.backend_port(index))[-3:]
        for index, role in enumerate(shipped.roles)
    }

    assert endings == dict.fromkeys(["fast", "strong", "small"], ["--reasoning", "off", "--no-mmap"])
