"""The agent factory the vendored runtime loads (`agent.agent:create_agent`)."""

from __future__ import annotations

from ucla_agent.core.dispatch import Dispatcher


def create_agent() -> Dispatcher:
    """A new dispatcher. The runtime calls this once per match, in that match's own worker process."""
    return Dispatcher()
