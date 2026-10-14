"""What the public repository leaves out: the development tools' state and the local Showdown install."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def is_ignored(path: str) -> bool:
    """Whether `.gitignore` leaves `path` out, judged by its patterns alone, whether or not git tracks the file."""
    completed = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", path], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return completed.returncode == 0


@pytest.mark.parametrize("path", [".mcp.json", ".claude-control/session.json"])
def test_development_tool_state_is_ignored(path):
    assert is_ignored(path)


def test_the_local_showdown_install_is_ignored_and_its_helper_is_kept():
    assert is_ignored("tools/pokemon_sim/node_modules/pokemon-showdown/package.json")
    for kept in ("package.json", "package-lock.json", ".npmrc", "sim.js", "dex.js"):
        assert not is_ignored(f"tools/pokemon_sim/{kept}")
