"""The tracked environment template names the variables and carries no value; the real .env stays untracked."""

from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_VARIABLES = {"ALTRUAGENT_CONTROL_URL", "ALTRUAGENT_OFFICIAL_AGENT_KEY"}


def test_template_names_the_runtime_variables_with_empty_values():
    values = dotenv_values(REPO_ROOT / ".env.example")

    assert RUNTIME_VARIABLES <= set(values)
    assert [name for name, value in values.items() if value] == []


def test_dotenv_file_is_ignored_by_git():
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert ".env" in ignored
