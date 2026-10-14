"""The command line that starts one role's `llama-server`.

It is built as an argument list and never as a shell string: a model path
holds backslashes and may hold spaces, and quoting one by hand is how a load
quietly becomes a different load. The order of the arguments is fixed, so the
same settings always give the same line.

Every option the role's settings pin is stated on the line, whether or not it
equals the program's own default: those defaults differ between builds of
`llama-server`, and a model file can carry sampling defaults of its own.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ucla_agent.core.modelserver.settings import RoleSettings

LOOPBACK = "127.0.0.1"
PYTHON_SCRIPT_SUFFIX = ".py"


def build(server_path: Path, role: RoleSettings, port: int) -> list[str]:
    """The arguments that start `role`'s server on the loopback `port`, the program first.

    The role's `extra_args` come last, so they can override an option stated
    before them.
    """
    return [
        *_program(server_path),
        "--model",
        str(role.model_path),
        "--host",
        LOOPBACK,
        "--port",
        str(port),
        "--ctx-size",
        str(role.ctx_size),
        "--n-gpu-layers",
        str(role.n_gpu_layers),
        "--parallel",
        str(role.parallel),
        "--temp",
        str(role.temperature),
        *_when_set("--top-k", role.top_k),
        *_when_set("--top-p", role.top_p),
        *_when_set("--seed", role.seed),
        "--reasoning",
        role.reasoning,
        *role.extra_args,
    ]


def _program(server_path: Path) -> list[str]:
    """What runs the server: the executable itself, or this interpreter for a server that is a Python script.

    The script form is how a stand-in server written in Python is started
    where the operating system cannot run a `.py` file as a program.
    """
    if server_path.suffix == PYTHON_SCRIPT_SUFFIX:
        return [sys.executable, str(server_path)]
    return [str(server_path)]


def _when_set(flag: str, value: int | float | None) -> list[str]:
    """The flag with its value, or nothing for an option the role leaves to the server."""
    return [] if value is None else [flag, str(value)]
