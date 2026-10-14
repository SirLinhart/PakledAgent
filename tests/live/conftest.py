"""The live tests talk to the real AltruAgent platform; nothing replaces their credentials."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def no_platform_credentials() -> None:
    """Leaves the platform key and control URL as the environment and `.env` give them.

    Every other test folder replaces them with values that cannot sign in
    (`tests/conftest.py`). The live tests are the ones meant to sign in, and
    only run when asked for with `-m live`.
    """
