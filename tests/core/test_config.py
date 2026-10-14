"""Loading `config/agent.toml` and hashing the files under `config/`."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ucla_agent.core.config import ConfigError, autostart_roles, hash_config_files, load_agent_config

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_agent_config(root: Path, text: str) -> Path:
    path = root / "config" / "agent.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob("*") if path.is_file()}


# -- load_agent_config -----------------------------------------------------------------------------


def test_the_shipped_configuration_autostarts_the_strong_and_jev9b_roles():
    shipped = load_agent_config(REPO_ROOT)

    assert shipped["models"]["autostart"] is True and shipped["models"]["autostart_roles"] == ["strong", "jev9b"]
    assert autostart_roles(shipped) == ("strong", "jev9b")


def test_tables_are_returned_as_parsed(tmp_path):
    _write_agent_config(tmp_path, '[models]\nautostart = true\n\n[redalert]\nplan = "standard"\n')

    assert load_agent_config(tmp_path) == {"models": {"autostart": True}, "redalert": {"plan": "standard"}}


def test_a_missing_file_says_where_to_start_the_agent(tmp_path):
    with pytest.raises(ConfigError) as raised:
        load_agent_config(tmp_path)

    assert str(tmp_path / "config" / "agent.toml") in str(raised.value)
    assert "Start the agent from the repository root" in str(raised.value)


def test_invalid_toml_is_reported_with_the_file(tmp_path):
    path = _write_agent_config(tmp_path, "[models\nautostart = false\n")

    with pytest.raises(ConfigError) as raised:
        load_agent_config(tmp_path)

    assert str(path) in str(raised.value) and "not valid TOML" in str(raised.value)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[models]\n",
        '[models]\nautostart = "yes"\n',
        "[models]\nautostart = 1\n",
        "models = 3\n",
    ],
    ids=["empty file", "no autostart", "string", "integer", "models is not a table"],
)
def test_autostart_must_be_a_boolean_in_the_models_table(tmp_path, text):
    _write_agent_config(tmp_path, text)

    with pytest.raises(ConfigError) as raised:
        load_agent_config(tmp_path)

    assert "must set autostart to true or false in its [models] table" in str(raised.value)


# -- autostart_roles -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "listed, roles",
    [('["fast", "strong"]', ("fast", "strong")), ('["strong"]', ("strong",)), ("[]", ())],
    ids=["two roles in their order", "one role", "empty list starts none"],
)
def test_autostart_roles_are_read_as_listed(tmp_path, listed, roles):
    _write_agent_config(tmp_path, f"[models]\nautostart = true\nautostart_roles = {listed}\n")

    assert autostart_roles(load_agent_config(tmp_path)) == roles


def test_without_autostart_roles_the_strong_role_is_started(tmp_path):
    _write_agent_config(tmp_path, "[models]\nautostart = true\n")

    assert autostart_roles(load_agent_config(tmp_path)) == ("strong",)


@pytest.mark.parametrize(
    "listed",
    ['"strong"', "[1, 2]", '["strong", 3]', "true", "{ strong = true }"],
    ids=["a string", "numbers", "one number", "a boolean", "a table"],
)
def test_autostart_roles_must_be_a_list_of_role_names(tmp_path, listed):
    path = _write_agent_config(tmp_path, f"[models]\nautostart = true\nautostart_roles = {listed}\n")

    with pytest.raises(ConfigError) as raised:
        load_agent_config(tmp_path)

    assert str(path) in str(raised.value)
    assert "must set autostart_roles in its [models] table to a list of role names" in str(raised.value)


def test_a_role_name_is_not_checked_against_the_model_server_settings_when_the_file_is_read(tmp_path):
    _write_agent_config(tmp_path, '[models]\nautostart = true\nautostart_roles = ["huge"]\n')

    assert autostart_roles(load_agent_config(tmp_path)) == ("huge",)


# -- hash_config_files -----------------------------------------------------------------------------


def test_every_file_under_config_gets_one_entry(tmp_path):
    agent_path = _write_agent_config(tmp_path, "[models]\nautostart = false\n")
    nested_path = tmp_path / "config" / "prompts" / "plan.txt"
    nested_path.parent.mkdir()
    nested_path.write_bytes(b"choose a plan")
    (tmp_path / "config" / "empty-folder").mkdir()

    hashes = hash_config_files(tmp_path)

    assert hashes == {
        "config/agent.toml": hashlib.sha256(agent_path.read_bytes()).hexdigest(),
        "config/prompts/plan.txt": hashlib.sha256(b"choose a plan").hexdigest(),
    }


def test_a_changed_file_changes_its_hash_only(tmp_path):
    _write_agent_config(tmp_path, "[models]\nautostart = false\n")
    (tmp_path / "config" / "models.toml").write_bytes(b"[server]\nport = 11500\n")
    before = hash_config_files(tmp_path)

    _write_agent_config(tmp_path, "[models]\nautostart = true\n")
    after = hash_config_files(tmp_path)

    assert after["config/agent.toml"] != before["config/agent.toml"]
    assert after["config/models.toml"] == before["config/models.toml"]


def test_the_shipped_config_folder_has_one_hash_per_file():
    shipped_files = {f"config/{name}" for name in _snapshot(REPO_ROOT / "config")}

    assert set(hash_config_files(REPO_ROOT)) == shipped_files
    assert "config/agent.toml" in shipped_files


def test_reading_and_hashing_change_nothing_under_config(tmp_path):
    _write_agent_config(tmp_path, "[models]\nautostart = false\n")
    before = _snapshot(tmp_path / "config")

    load_agent_config(tmp_path)
    hash_config_files(tmp_path)

    assert _snapshot(tmp_path / "config") == before
