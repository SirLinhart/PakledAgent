"""The readiness check's platform step against the real platform: run with `pytest -m live tests/live`.

It signs in with the Official Agent Key from the environment or `.env` and
lists the agent's assignments; it plays nothing and changes nothing on the
platform. Without a key it is skipped.
"""

from __future__ import annotations

import os

import pytest

from altruagent.official import OFFICIAL_AGENT_KEY_ENV
from ucla_agent.check import services
from ucla_agent.check.line import PASS
from ucla_agent.core import secrets

pytestmark = pytest.mark.live


def test_the_platform_step_passes_against_the_real_platform():
    secrets.load_secrets()
    if not os.environ.get(OFFICIAL_AGENT_KEY_ENV, "").strip():
        pytest.skip(f"{OFFICIAL_AGENT_KEY_ENV} is not set: the live platform check needs the real key")

    line = services.check_platform()

    assert line.status == PASS, line.text()
