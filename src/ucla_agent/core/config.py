"""The agent's tracked configuration under `config/`.

Settings that affect play live in TOML files there. The agent only ever reads
them: a run records their hashes, so the files in force can be identified
afterwards.
"""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import Any

CONFIG_DIR_NAME = "config"
AGENT_CONFIG_FILE_NAME = "agent.toml"
MODELS_TABLE = "models"
AUTOSTART_ROLES_KEY = "autostart_roles"
# The role the Werewolf and Pokémon chains ask; the others are left unloaded, so their memory stays free.
DEFAULT_AUTOSTART_ROLES = ("strong",)
# How many of a model's own recent picks each Red Alert question shows (see `games.redalert.model_history`).
HISTORY_PICKS_KEY = "history_picks"
DEFAULT_HISTORY_PICKS = 3


class ConfigError(Exception):
    """The configuration under `config/` is missing or unusable; the message says what to fix."""


def load_agent_config(root: Path) -> dict[str, Any]:
    """Parse `config/agent.toml` under `root` and return its tables."""
    path = root / CONFIG_DIR_NAME / AGENT_CONFIG_FILE_NAME
    try:
        with path.open("rb") as handle:
            config = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"{path} was not found. Start the agent from the repository root.") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
    models = config.get(MODELS_TABLE)
    if not isinstance(models, dict) or not isinstance(models.get("autostart"), bool):
        raise ConfigError(f"{path} must set autostart to true or false in its [models] table.")
    # Only the shape is checked here; whether config/models.toml has each role is found when the server starts.
    roles = models.get(AUTOSTART_ROLES_KEY, list(DEFAULT_AUTOSTART_ROLES))
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        raise ConfigError(f"{path} must set {AUTOSTART_ROLES_KEY} in its [models] table to a list of role names.")
    _check_history_picks(models, path)
    return config


def _check_history_picks(models: dict[str, Any], path: Path) -> None:
    """Raise `ConfigError` unless the `[models]` table's history_picks, when it is set, is a whole number of 0 or
    more."""
    picks = models.get(HISTORY_PICKS_KEY, DEFAULT_HISTORY_PICKS)
    if not isinstance(picks, int) or isinstance(picks, bool) or picks < 0:
        raise ConfigError(f"{path} must set {HISTORY_PICKS_KEY} in its [models] table to a whole number of 0 or more.")


def autostart_roles(config: dict[str, Any]) -> tuple[str, ...]:
    """The roles of `config/models.toml` the agent starts when autostart is on, as `load_agent_config` returned them.

    `DEFAULT_AUTOSTART_ROLES` when the `[models]` table does not list them; an
    empty list starts none.
    """
    return tuple(config[MODELS_TABLE].get(AUTOSTART_ROLES_KEY, DEFAULT_AUTOSTART_ROLES))


def history_picks(config: dict[str, Any]) -> int:
    """How many of a model's own recent picks each Red Alert question shows, as `load_agent_config` returned the
    configuration; `DEFAULT_HISTORY_PICKS` when the `[models]` table does not say."""
    return config[MODELS_TABLE].get(HISTORY_PICKS_KEY, DEFAULT_HISTORY_PICKS)


def hash_config_files(root: Path) -> dict[str, str]:
    """SHA-256 of every file under `config/`, keyed by its path relative to `root` with forward slashes."""
    config_dir = root / CONFIG_DIR_NAME
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(config_dir.rglob("*"))
        if path.is_file()
    }
