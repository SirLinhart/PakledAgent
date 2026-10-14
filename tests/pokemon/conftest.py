"""Fixtures shared by the Pokémon agent's tests."""

from __future__ import annotations

import sys
from pathlib import Path

# `pokemon_states` is test code the test files import by name. Pytest's importlib mode puts no test folder on the
# import path, so this folder is added.
POKEMON_TESTS_FOLDER = Path(__file__).resolve().parent
if str(POKEMON_TESTS_FOLDER) not in sys.path:
    sys.path.insert(0, str(POKEMON_TESTS_FOLDER))
