"""The project metadata declares the three packages under src/ and the starter's dependency bounds."""

from __future__ import annotations

import configparser
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STARTER_RUNTIME_DEPENDENCIES = {"httpx>=0.27", "python-dotenv>=1.0", "mcp>=1.12.0,<2.0.0"}
PACKAGES = ("altruagent", "agent", "ucla_agent")


def _pyproject() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_runtime_dependencies_match_the_vendored_starter():
    project = _pyproject()["project"]

    assert set(project["dependencies"]) == STARTER_RUNTIME_DEPENDENCIES
    assert project["requires-python"] == ">=3.11"


def test_dev_extra_provides_pytest_8_or_newer():
    assert _pyproject()["project"]["optional-dependencies"]["dev"] == ["pytest>=8.0"]


def test_the_three_packages_are_found_under_src():
    setuptools_table = _pyproject()["tool"]["setuptools"]

    assert setuptools_table["package-dir"] == {"": "src"}
    assert setuptools_table["packages"]["find"]["where"] == ["src"]
    assert set(setuptools_table["packages"]["find"]["include"]) == {f"{name}*" for name in PACKAGES}
    for name in PACKAGES:
        assert (REPO_ROOT / "src" / name / "__init__.py").is_file()


def test_game_areas_and_core_are_packages():
    for relative in ("ucla_agent/core", "ucla_agent/games", "ucla_agent/games/redalert"):
        assert (REPO_ROOT / "src" / relative / "__init__.py").is_file()


def test_pytest_collects_only_the_tests_folder_with_src_importable():
    parser = configparser.ConfigParser()
    parser.read(REPO_ROOT / "pytest.ini", encoding="utf-8")
    settings = parser["pytest"]

    assert settings["testpaths"] == "tests"
    assert settings["pythonpath"] == "src"
    assert {"docs", "tmp", "runs"} <= set(settings["norecursedirs"].split())
