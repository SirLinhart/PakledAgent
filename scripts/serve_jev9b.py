"""Serve JevK5-9B alone over the systemone API, on one loopback port, for the model server's systemone role.

    <native python> scripts/serve_jev9b.py --model-dir <JevK5-9B folder> --port <port> [--host 127.0.0.1]

The model server starts this script for a `[systemone_roles.<name>]` role of
`config/models.toml`, with that role's Python (an environment with torch,
jev-style and jevk5) and its environment variables (the Hugging Face home,
the Triton cache, the allocator and `JEVK5_GRAPHS`). Nothing about the machine
is written here: the model folder and the port come on the command line and
the rest from the environment.

It follows the JevTest project's serve-native-cascade.py, which serves the
named cascade `cascade-9b` (Jev-Style 2B, then JevK5-9B) with jev-style's
`build_app`, but serves the JevK5-9B tier alone, with no 2B tier in front of
it. jev-style refuses a cascade of one tier, so the tier's engine is built
here the way jev-style builds a `jevk5:<folder>` tier, with
`jev_style.jevk5_engine.load_jevk5` (its SHA256SUMS checked, its calibration
config required, CUDA graphs recorded by jevk5), and served by jev-style's own
`create_app`: `POST /v1/systemone`, `GET /v1/models` and `GET /healthz`. The
server listens only once the model is loaded and its graphs are recorded.

Sources: JevTest's serve-native-cascade.py and native-cascade-benchmark.py;
jev-style (https://github.com/lawrence3699/jev-style); JevK5-9B by
alibiserikbay (https://huggingface.co/alibiserikbay/JevK5-9B) and its runtime
allebee/jevk5 (https://github.com/allebee/jevk5), both Apache-2.0. Neither the
runtime nor the weights are part of this repository.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import time
from pathlib import Path

LOOPBACK = "127.0.0.1"
# jevk5-serve's defaults, which JevTest's launcher also sets: the allocator without expandable segments, and the
# CUDA graphs on. The role's environment may set either; these apply only when it does not.
DEFAULT_ENVIRONMENT = {"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:False", "JEVK5_GRAPHS": "1"}
PORT_PROBE_SECONDS = 0.5
BYTES_PER_GIB = 1024**3
UVICORN_LOG_LEVEL = "warning"
# The script's own lines: what was loaded, or why nothing is served. It runs in the role's own Python environment,
# where the agent's console writer cannot be imported, so it logs, one message a line, to standard error; the model
# server appends the child's standard output and error alike to runs/model-server/<role>.log.
LOG = logging.getLogger("serve_jev9b")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """The command line: the model folder and the loopback address to serve on."""
    parser = argparse.ArgumentParser(description="Serve JevK5-9B alone over the systemone API.")
    parser.add_argument("--model-dir", type=Path, required=True, help="the JevK5-9B folder (with SHA256SUMS)")
    parser.add_argument("--port", type=int, required=True, help="the port to serve on")
    parser.add_argument("--host", default=LOOPBACK, help="the loopback address to serve on")
    return parser.parse_args(argv)


def log_to_standard_error() -> None:
    """Send the script's log, each message alone on its line, to standard error, and nowhere else."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    LOG.handlers[:] = [handler]
    LOG.setLevel(logging.INFO)
    LOG.propagate = False


def port_in_use(host: str, port: int) -> bool:
    """Whether something already listens on the address."""
    with socket.socket() as probe:
        probe.settimeout(PORT_PROBE_SECONDS)
        return probe.connect_ex((host, port)) == 0


class Refusal(Exception):
    """Why the server is not started; the message says it."""


def check_start(arguments: argparse.Namespace) -> None:
    """Raise `Refusal` when the port is taken or the model folder is missing, before anything is loaded."""
    if port_in_use(arguments.host, arguments.port):
        raise Refusal(f"Port {arguments.port} of {arguments.host} already has a listening service.")
    if not arguments.model_dir.is_dir():
        raise Refusal(f"The model folder {arguments.model_dir} does not exist.")


def serve(arguments: argparse.Namespace) -> None:
    """Load JevK5-9B, log what was loaded as one JSON line and serve until the process is ended.

    Raises `Refusal` without CUDA or when no CUDA graph was recorded.
    """
    # Imported only now: the native runtime is in the role's own Python environment, and loading it takes seconds.
    import torch
    import uvicorn
    from jev_style.jevk5_engine import load_jevk5
    from jev_style.server import create_app

    if not torch.cuda.is_available():
        raise Refusal("JevK5-9B needs CUDA; there is no CPU fallback.")
    started = time.perf_counter()
    engine = load_jevk5(str(arguments.model_dir), verify=True)
    graphs = len(engine.model.graphs)
    if graphs == 0:
        raise Refusal("JevK5-9B recorded no CUDA graphs; JEVK5_GRAPHS must not be 0.")
    loaded = {
        "pid": os.getpid(),
        "api": f"http://{arguments.host}:{arguments.port}/v1/systemone",
        "model": engine.model_id,
        "backend": engine.backend,
        "cuda_graphs": graphs,
        "startup_seconds": round(time.perf_counter() - started, 1),
        "gpu": torch.cuda.get_device_name(0),
        "cuda_memory_reserved_gib": round(torch.cuda.memory_reserved() / BYTES_PER_GIB, 2),
        "allocator": os.environ["PYTORCH_CUDA_ALLOC_CONF"],
    }
    LOG.info(json.dumps(loaded))
    uvicorn.run(create_app(engine), host=arguments.host, port=arguments.port, log_level=UVICORN_LOG_LEVEL)


def main(argv: list[str] | None = None) -> int:
    """Serve as the command line says; 1, with the reason on standard error, when the server cannot be started."""
    arguments = parse_arguments(argv)
    log_to_standard_error()
    for name, value in DEFAULT_ENVIRONMENT.items():
        os.environ.setdefault(name, value)
    try:
        check_start(arguments)
        serve(arguments)
    except Refusal as refusal:
        LOG.error("%s", refusal)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
