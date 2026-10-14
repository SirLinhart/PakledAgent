"""Where the agent finds its project root: the folder of its configuration, its records and its lock file."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ucla_agent.core import paths

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "src"


@pytest.fixture
def elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty temporary folder as the current directory, with no project root named in the environment."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(paths.ROOT_ENV, raising=False)
    return tmp_path


def test_from_an_empty_folder_the_root_is_the_repository_that_holds_the_package(elsewhere):
    root = paths.repository_root()

    assert root == REPO_ROOT and root != elsewhere
    assert (root / "config" / "agent.toml").is_file()


def test_with_ucla_agent_root_set_the_root_is_the_folder_it_names(elsewhere, tmp_path_factory, monkeypatch):
    named = tmp_path_factory.mktemp("named-root")
    monkeypatch.setenv(paths.ROOT_ENV, str(named))

    assert paths.repository_root() == named


def test_the_named_root_wins_even_over_the_repository_the_package_is_in(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setenv(paths.ROOT_ENV, str(REPO_ROOT / "tmp"))

    assert paths.repository_root() == REPO_ROOT / "tmp"


def test_a_relative_named_root_is_taken_from_the_current_directory(elsewhere, monkeypatch):
    monkeypatch.setenv(paths.ROOT_ENV, "project")

    assert paths.repository_root() == elsewhere / "project"


def test_an_empty_ucla_agent_root_counts_as_not_set(elsewhere, monkeypatch):
    monkeypatch.setenv(paths.ROOT_ENV, "")

    assert paths.repository_root() == REPO_ROOT


def test_a_package_with_no_project_above_it_falls_back_to_the_current_directory(
    elsewhere, tmp_path_factory, monkeypatch
):
    installed = tmp_path_factory.mktemp("site-packages") / "ucla_agent"
    installed.mkdir()
    monkeypatch.setattr(paths, "PACKAGE_DIR", installed)

    assert paths.repository_root() == elsewhere


def test_the_nearest_folder_above_the_package_that_holds_the_configuration_is_the_project(tmp_path):
    outer, inner = tmp_path, tmp_path / "checkout"
    for project in (outer, inner):
        (project / "config").mkdir(parents=True)
        (project / "config" / "agent.toml").write_text("[models]\nautostart = false\n", encoding="utf-8")
    package = inner / "src" / "ucla_agent"
    package.mkdir(parents=True)

    assert paths.project_holding(package) == inner


def test_a_folder_named_config_without_agent_toml_does_not_mark_a_project(tmp_path):
    (tmp_path / "config").mkdir()
    package = tmp_path / "src" / "ucla_agent"
    package.mkdir(parents=True)

    assert paths.project_holding(package) is None


def test_a_process_started_in_another_folder_resolves_the_repository(elsewhere):
    environment = {name: value for name, value in os.environ.items() if name != paths.ROOT_ENV}
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    script = "from ucla_agent.core import paths; print(paths.repository_root())"

    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=elsewhere, env=environment, capture_output=True, text=True
    )

    assert (completed.returncode, completed.stderr) == (0, "")
    assert Path(completed.stdout.strip()) == REPO_ROOT
