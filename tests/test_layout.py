"""The project layout rules.

The vendored starter is byte-for-byte what its manifest records, the shared
core imports no game area except through the registry, no game area imports
another, and `python -m agent` answers with the starter's flags.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
import sys
from importlib.util import resolve_name
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src"
VENDORED_PACKAGE = SOURCE_ROOT / "altruagent"
UPSTREAM_NOTE = "UPSTREAM.md"
UPSTREAM_COMMIT = "951f4f74b008c6aa7f40bec09e741aa67d3c45cc"
# A manifest line: <sha256>  <file name>
MANIFEST_LINE = re.compile(r"^(?P<sha256>[0-9a-f]{64})  (?P<name>\S+\.py)$", re.MULTILINE)

CORE_FOLDER = Path("ucla_agent") / "core"
GAMES_FOLDER = Path("ucla_agent") / "games"
GAMES_PACKAGE = "ucla_agent.games"
REGISTRY_FILE = "registry.py"
DYNAMIC_IMPORT_FUNCTIONS = {"import_module", "__import__"}
START_FLAGS = ("--tournament", "--match", "--check-tournament", "--agent")


# -- the vendored starter --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def upstream_note() -> str:
    return (VENDORED_PACKAGE / UPSTREAM_NOTE).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def manifest(upstream_note) -> dict[str, str]:
    return {line["name"]: line["sha256"] for line in MANIFEST_LINE.finditer(upstream_note)}


def _vendored_files(suffix: str = "") -> set[str]:
    """Paths under the vendored package, relative to it, leaving out Python's bytecode cache."""
    return {
        path.relative_to(VENDORED_PACKAGE).as_posix()
        for path in VENDORED_PACKAGE.rglob(f"*{suffix}")
        if path.is_file() and "__pycache__" not in path.parts
    }


def test_the_manifest_names_the_upstream_commit(upstream_note):
    assert UPSTREAM_COMMIT in upstream_note


def test_every_vendored_python_file_has_the_hash_its_manifest_records(manifest):
    actual = {
        name: hashlib.sha256((VENDORED_PACKAGE / name).read_bytes()).hexdigest() for name in _vendored_files(".py")
    }

    assert actual == manifest
    assert len(manifest) > 0


def test_the_vendored_folder_holds_nothing_but_the_manifest_files_and_the_note(manifest):
    assert _vendored_files() == set(manifest) | {UPSTREAM_NOTE}


# -- import boundaries -----------------------------------------------------------------------------


def _dynamic_import_target(node: ast.AST) -> str | None:
    """The module named by an `import_module("...")` or `__import__("...")` call with a literal argument."""
    if not isinstance(node, ast.Call) or not node.args:
        return None
    called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
    argument = node.args[0]
    if called in DYNAMIC_IMPORT_FUNCTIONS and isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    return None


def _names_imported_by(node: ast.AST, package: str) -> set[str]:
    """Every dotted module name one syntax node imports, relative imports resolved; empty if it imports nothing."""
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        base = resolve_name("." * node.level + (node.module or ""), package)
        return {base} | {f"{base}.{alias.name}" for alias in node.names}
    target = _dynamic_import_target(node)
    return {target} if target else set()


def _imports_of(path: Path, source_root: Path) -> set[str]:
    """Every module a source file imports, anywhere in the file, as absolute dotted names."""
    package = ".".join(path.parent.relative_to(source_root).parts)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return set().union(*(_names_imported_by(node, package) for node in ast.walk(tree)))


def _in_another_game(name: str, own_game: str) -> bool:
    parts = name.split(".")
    return ".".join(parts[:2]) == GAMES_PACKAGE and len(parts) > 2 and parts[2] != own_game


def core_imports_of_game_areas(source_root: Path) -> dict[str, set[str]]:
    """For each core module other than the registry that imports a game area: the game modules it imports."""
    found = {}
    for path in sorted((source_root / CORE_FOLDER).rglob("*.py")):
        imported = {name for name in _imports_of(path, source_root) if f"{name}.".startswith(f"{GAMES_PACKAGE}.")}
        if imported and path.name != REGISTRY_FILE:
            found[path.relative_to(source_root).as_posix()] = imported
    return found


def imports_across_game_areas(source_root: Path) -> dict[str, set[str]]:
    """For each game module that imports from a different game area: the foreign modules it imports."""
    found = {}
    game_folders = sorted(path for path in (source_root / GAMES_FOLDER).iterdir() if (path / "__init__.py").is_file())
    for game_folder in game_folders:
        for path in sorted(game_folder.rglob("*.py")):
            foreign = {name for name in _imports_of(path, source_root) if _in_another_game(name, game_folder.name)}
            if foreign:
                found[path.relative_to(source_root).as_posix()] = foreign
    return found


def test_no_core_module_but_the_registry_imports_a_game_area():
    assert core_imports_of_game_areas(SOURCE_ROOT) == {}


def test_the_registry_is_where_the_core_imports_game_areas():
    registry_imports = _imports_of(SOURCE_ROOT / CORE_FOLDER / REGISTRY_FILE, SOURCE_ROOT)

    assert "ucla_agent.games.redalert.agent" in registry_imports


def test_no_game_area_imports_another_game_area():
    assert imports_across_game_areas(SOURCE_ROOT) == {}


@pytest.fixture
def sample_tree(tmp_path: Path) -> Path:
    """A source tree with every kind of boundary violation the scan must catch, next to permitted imports."""
    sources = {
        "ucla_agent/core/registry.py": "from ucla_agent.games.alpha import agent\n",
        "ucla_agent/core/permitted.py": "import json\nfrom ucla_agent.core import registry\nfrom . import records\n",
        "ucla_agent/core/absolute.py": "from ucla_agent.games.alpha.agent import Agent\n",
        "ucla_agent/core/relative.py": "from ..games import alpha\n",
        "ucla_agent/core/inside_a_function.py": "def load():\n    import ucla_agent.games.beta.rules\n",
        "ucla_agent/core/by_name.py": "import importlib\nmodule = importlib.import_module('ucla_agent.games.alpha')\n",
        "ucla_agent/games/__init__.py": "",
        "ucla_agent/games/alpha/__init__.py": "",
        "ucla_agent/games/alpha/agent.py": "from ucla_agent.core import records\nfrom . import rules\n",
        "ucla_agent/games/alpha/rules.py": "from ucla_agent.games.alpha import agent\n",
        "ucla_agent/games/beta/__init__.py": "",
        "ucla_agent/games/beta/relative.py": "from ..alpha import rules\n",
        "ucla_agent/games/beta/absolute.py": "from ucla_agent.games import alpha\n",
        "ucla_agent/games/beta/permitted.py": "from ucla_agent.games.beta import relative\n",
    }
    for relative_path, text in sources.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_the_scan_catches_every_way_a_core_module_can_import_a_game_area(sample_tree):
    assert core_imports_of_game_areas(sample_tree) == {
        "ucla_agent/core/absolute.py": {"ucla_agent.games.alpha.agent", "ucla_agent.games.alpha.agent.Agent"},
        "ucla_agent/core/relative.py": {"ucla_agent.games", "ucla_agent.games.alpha"},
        "ucla_agent/core/inside_a_function.py": {"ucla_agent.games.beta.rules"},
        "ucla_agent/core/by_name.py": {"ucla_agent.games.alpha"},
    }


def test_the_scan_catches_a_game_area_importing_another(sample_tree):
    assert imports_across_game_areas(sample_tree) == {
        "ucla_agent/games/beta/relative.py": {"ucla_agent.games.alpha", "ucla_agent.games.alpha.rules"},
        "ucla_agent/games/beta/absolute.py": {"ucla_agent.games.alpha"},
    }


# -- the entry point -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def help_run() -> subprocess.CompletedProcess:
    environment = {**os.environ, "PYTHONPATH": str(SOURCE_ROOT)}
    command = [sys.executable, "-m", "agent", "--help"]
    return subprocess.run(command, cwd=REPO_ROOT, env=environment, capture_output=True, text=True)


def test_the_entry_point_help_exits_zero(help_run):
    assert help_run.returncode == 0


@pytest.mark.parametrize("flag", START_FLAGS)
def test_the_entry_point_help_names_each_start_flag(help_run, flag):
    assert flag in help_run.stdout
