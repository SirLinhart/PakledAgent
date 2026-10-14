"""The content of a match's meta file: the match's own facts and those of the working tree it is played from."""

from __future__ import annotations

import hashlib
import subprocess

import pytest

from altruagent import DecisionContext
from ucla_agent.core import match_meta

STARTED_AT = "2026-10-12T18:30:00.000Z"
MATCH_KEYS = {"session_id", "game_type", "tournament_id", "context", "agent_id", "seat", "attempt", "started_at"}
WORKING_TREE_KEYS = {"commit", "config_hashes", "models", "decision_services"}
MODELS_TOML = rb"""
[server]
host = "127.0.0.1"
port = 11500
backend_port_base = 41500

[roles.fast]
model_path = "C:/Models/gguf/fast-model-Q4_K_XL.gguf"
required = false
ctx_size = 8192
temperature = 0

[roles.strong]
model_path = 'C:\Models\gguf\strong-model-Q8_0.gguf'
required = true
ctx_size = 16384
"""
AGENT_TOML_WITH_JEV = b"""
[models]
autostart = false

[jev]
enabled = %s
base_url = "https://api.jevai.net"
"""


def _context(tournament_id: str | None = None) -> DecisionContext:
    return DecisionContext(
        session_id="session-9", tournament_id=tournament_id, game_type="red_alert", agent_id="agent-1"
    )


def test_meta_holds_the_facts_of_the_match_and_of_the_working_tree(git_repo, git):
    config_bytes = (git_repo / "config" / "agent.toml").read_bytes()

    meta = match_meta.build(_context(tournament_id="tour-7"), "Multi1", 3, STARTED_AT, git_repo)

    assert meta == {
        "session_id": "session-9",
        "game_type": "red_alert",
        "tournament_id": "tour-7",
        "context": "tournament",
        "agent_id": "agent-1",
        "seat": "Multi1",
        "attempt": 3,
        "started_at": STARTED_AT,
        "commit": git(git_repo, "rev-parse", "HEAD"),
        "config_hashes": {"config/agent.toml": hashlib.sha256(config_bytes).hexdigest()},
        "models": [],
        "decision_services": [],
    }
    assert set(meta) == MATCH_KEYS | WORKING_TREE_KEYS


def test_a_match_without_a_tournament_is_a_testing_match(git_repo):
    meta = match_meta.build(_context(), "seat", 1, STARTED_AT, git_repo)

    assert (meta["context"], meta["tournament_id"], meta["seat"]) == ("testing", None, "seat")


def test_models_are_the_roles_of_the_models_file_with_file_names_and_settings(git_repo):
    (git_repo / "config" / "models.toml").write_bytes(MODELS_TOML)

    meta = match_meta.build(_context(), "Multi0", 1, STARTED_AT, git_repo)

    assert meta["models"] == [
        {
            "role": "fast",
            "model_file": "fast-model-Q4_K_XL.gguf",
            "settings": {"required": False, "ctx_size": 8192, "temperature": 0},
        },
        {"role": "strong", "model_file": "strong-model-Q8_0.gguf", "settings": {"required": True, "ctx_size": 16384}},
    ]
    assert meta["decision_services"] == [{"name": "local", "base_url": "http://127.0.0.1:11500"}]
    assert sorted(meta["config_hashes"]) == ["config/agent.toml", "config/models.toml"]


@pytest.mark.parametrize(
    "enabled, services",
    [(b"true", [{"name": "jev", "base_url": "https://api.jevai.net"}]), (b"false", [])],
    ids=["enabled", "disabled"],
)
def test_jev_is_a_decision_service_only_when_it_is_enabled(git_repo, enabled, services):
    (git_repo / "config" / "agent.toml").write_bytes(AGENT_TOML_WITH_JEV % enabled)

    assert match_meta.build(_context(), "Multi0", 1, STARTED_AT, git_repo)["decision_services"] == services


def test_outside_a_git_repository_the_commit_is_null(run_folder, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(run_folder.parent))

    meta = match_meta.build(_context(), "Multi0", 1, STARTED_AT, run_folder)

    assert meta["commit"] is None
    assert (meta["config_hashes"], meta["models"], meta["decision_services"]) == ({}, [], [])


@pytest.mark.parametrize(
    "failure",
    [FileNotFoundError("git"), subprocess.TimeoutExpired(cmd="git", timeout=10.0)],
    ids=["git is not installed", "git does not answer"],
)
def test_the_commit_is_null_when_git_cannot_be_asked(git_repo, monkeypatch, failure):
    def no_answer(*_arguments, **_options):
        raise failure

    monkeypatch.setattr(match_meta.subprocess, "run", no_answer)

    assert match_meta.build(_context(), "Multi0", 1, STARTED_AT, git_repo)["commit"] is None


def test_a_configuration_file_that_cannot_be_parsed_leaves_the_match_facts_and_an_error(git_repo):
    (git_repo / "config" / "models.toml").write_bytes(b"[roles.fast\nmodel_path = ")

    meta = match_meta.build(_context(tournament_id="tour-7"), "Multi0", 2, STARTED_AT, git_repo)

    assert set(meta) == MATCH_KEYS | {"error"}
    assert (meta["session_id"], meta["seat"], meta["attempt"], meta["context"]) == (
        "session-9",
        "Multi0",
        2,
        "tournament",
    )
    assert meta["error"].startswith("TOMLDecodeError: ")
