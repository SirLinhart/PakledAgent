"""A courtesy to the ModelBouncer service some workstations run.

ModelBouncer hands out the GPU memory of a shared workstation. The included
model server does not go through it, but where it runs, telling it beforehand
how much memory the models are about to take lets it plan around them. The
lease is tied to this process (`owner_pid`), so ModelBouncer drops it by
itself when the agent exits.

Nothing depends on the answer. Where no such service runs nothing is sent, and
a refused, queued or failed lease changes nothing about how the model server
starts: a failure is printed as one warning line and that is all.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Sequence

import httpx

from ucla_agent.core.console import print_line
from ucla_agent.core.secrets import BoundClient

MODELBOUNCER_URL = "http://127.0.0.1:11435"
STATUS_ROUTE = "/status"
LEASES_ROUTE = "/v1/leases"
CLIENT_HEADER = "x-modelbouncer-client"
CLIENT_NAME = "ucla-agent"
# A service that is there answers its status at once; waiting longer would only delay a start without one.
STATUS_TIMEOUT_SECONDS = 1.0
LEASE_TIMEOUT_SECONDS = 5.0
# Asked for on top of the model files' own sizes: context buffers and the runtime's working memory.
LEASE_MARGIN_MIB = 4096
BYTES_PER_MIB = 1024 * 1024
# ModelBouncer answers 201 for a lease it holds now and 202 for one it has queued.
LEASE_ACCEPTED_STATUSES = (201, 202)


def request_lease(model_paths: Sequence[Path], *, transport: httpx.BaseTransport | None = None) -> None:
    """Ask ModelBouncer, if it runs on this machine, for a memory lease covering these model files.

    Never raises and returns nothing. `transport` replaces the network in tests.
    """
    try:
        _ask_for_lease(model_paths, transport)
    except Exception as exc:  # noqa: BLE001 - whatever goes wrong with a courtesy, the model server still starts
        _warn(f"the lease could not be asked for ({type(exc).__name__})")


def lease_mib(model_paths: Sequence[Path]) -> int:
    """The size of the lease in MiB: the model files that exist, rounded up, plus the margin.

    Raises `OSError` when a file cannot be read, for example one that is
    removed between being found and being measured.
    """
    model_bytes = sum(path.stat().st_size for path in model_paths if path.is_file())
    return math.ceil(model_bytes / BYTES_PER_MIB) + LEASE_MARGIN_MIB


def _ask_for_lease(model_paths: Sequence[Path], transport: httpx.BaseTransport | None) -> None:
    client = BoundClient(MODELBOUNCER_URL, headers={CLIENT_HEADER: CLIENT_NAME}, transport=transport)
    try:
        if _service_answers(client):
            _post_lease(client, lease_mib(model_paths))
    finally:
        client.close()


def _service_answers(client: BoundClient) -> bool:
    """Whether a ModelBouncer service answers its status route in time, with any status."""
    try:
        client.request("GET", STATUS_ROUTE, timeout=STATUS_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return False
    return True


def _post_lease(client: BoundClient, megabytes: int) -> None:
    body = {
        "kind": "memory",
        "client": CLIENT_NAME,
        "vram_mb": megabytes,
        "exclusive": False,
        "owner_pid": os.getpid(),
    }
    try:
        answer = client.request("POST", LEASES_ROUTE, json_body=body, timeout=LEASE_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        _warn(f"the request failed ({type(exc).__name__})")
        return
    if answer.status_code not in LEASE_ACCEPTED_STATUSES:
        _warn(f"it answered HTTP {answer.status_code}")


def _warn(what_happened: str) -> None:
    print_line(
        f"Warning: ModelBouncer did not take the courtesy memory lease: {what_happened}. "
        "The model server starts all the same.",
        error=True,
    )
