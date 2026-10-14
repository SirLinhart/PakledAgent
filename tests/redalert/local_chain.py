"""A Red Alert chain built as the tracked files build it, asking the local role small instead of the shipped engine.

The shipped Red Alert chains ask the systemone role jev9b, whose answers are not chat completions. A test whose
stand-in model answers as the included model server does, and which checks the requests and records of a local
role, builds its chain here: the same files, the same transport, the one entry `local:small`.
"""

from __future__ import annotations

import dataclasses

import httpx
import pytest

from ucla_agent.core.decide import ChainSource, build_chain
from ucla_agent.core.decide import settings as decide_settings

LOCAL_ENTRY = "local:small"


def build_local_chain(name: str, *, transport: httpx.BaseTransport | None = None) -> ChainSource:
    """`build_chain(name, transport=transport)` with the chain's entries replaced by `local:small`."""
    tracked_load = decide_settings.load

    def load(root):
        tracked = tracked_load(root)
        return dataclasses.replace(tracked, chains={**tracked.chains, name: (LOCAL_ENTRY,)})

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(decide_settings, "load", load)
        return build_chain(name, transport=transport)
