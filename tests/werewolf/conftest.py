"""Fixtures shared by the tests of the Werewolf agent's knowledge."""

from __future__ import annotations

import sys
from pathlib import Path

# `werewolf_states` is test code the test files import by name. Pytest's importlib mode puts no test folder on the
# import path, so this folder is added.
WEREWOLF_TESTS_FOLDER = Path(__file__).resolve().parent
if str(WEREWOLF_TESTS_FOLDER) not in sys.path:
    sys.path.insert(0, str(WEREWOLF_TESTS_FOLDER))
