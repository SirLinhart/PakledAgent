"""The fixed facts of one attempt at a match, as its meta file holds them.

Everything here is known when the attempt starts and does not change while
the match is played: which match and seat, which code (the git commit of the
working tree), which settings (the hash of every file under `config/`), and
which models and decision services the configuration names. Next to the
events file, this is what connects a submitted commit to what the agent did
in a match.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path, PureWindowsPath
from typing import Any

from ucla_agent.core.config import AGENT_CONFIG_FILE_NAME, CONFIG_DIR_NAME, hash_config_files

MODELS_CONFIG_FILE_NAME = "models.toml"
TESTING_CONTEXT = "testing"
TOURNAMENT_CONTEXT = "tournament"
LOCAL_MODELS_SERVICE_NAME = "local"
JEV_SERVICE_NAME = "jev"
_GIT_TIMEOUT_SECONDS = 10.0


def build(context: Any, seat: str, attempt: int, started_at: str, root: Path) -> dict[str, Any]:
    """The meta file content for one attempt at the match `context` names, played from the repository at `root`.

    `seat` is the label of the seat played, the same one the match folder's
    name ends in.

    Never raises: when the commit, the configuration hashes, the models or the
    decision services cannot be worked out, the match's own facts are returned
    with an `error` entry in their place, so the record still says whose it is.
    """
    meta = {
        "session_id": context.session_id,
        "game_type": context.game_type,
        "tournament_id": context.tournament_id,
        "context": TOURNAMENT_CONTEXT if context.tournament_id else TESTING_CONTEXT,
        "agent_id": context.agent_id,
        "seat": seat,
        "attempt": attempt,
        "started_at": started_at,
    }
    try:
        meta.update(_working_tree_facts(root))
    except Exception as exc:  # noqa: BLE001 - whatever is wrong with the working tree, the match is recorded
        meta["error"] = f"{type(exc).__name__}: {exc}"
    return meta


def _working_tree_facts(root: Path) -> dict[str, Any]:
    agent_config = _read_toml(root / CONFIG_DIR_NAME / AGENT_CONFIG_FILE_NAME)
    models_config = _read_toml(root / CONFIG_DIR_NAME / MODELS_CONFIG_FILE_NAME)
    return {
        "commit": _git_commit(root),
        "config_hashes": hash_config_files(root),
        "models": _models(models_config),
        "decision_services": _decision_services(agent_config, models_config),
    }


def _read_toml(path: Path) -> dict[str, Any]:
    """The tables of a TOML file; none when the file does not exist."""
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _git_commit(root: Path) -> str | None:
    """The commit the working tree under `root` is on, or None when git cannot say (no git, not a repository)."""
    command = ["git", "rev-parse", "HEAD"]
    try:
        completed = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=_GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _models(models_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Each model role the configuration defines: its name, its model file's name and its other settings."""
    return [
        {
            "role": role,
            # PureWindowsPath takes both separators, so the name comes out the same on every platform.
            "model_file": PureWindowsPath(settings.get("model_path", "")).name,
            "settings": {name: value for name, value in settings.items() if name != "model_path"},
        }
        for role, settings in models_config.get("roles", {}).items()
    ]


def _decision_services(agent_config: dict[str, Any], models_config: dict[str, Any]) -> list[dict[str, str]]:
    """The services the configuration lets a decision come from, each with its base URL."""
    services = []
    server = models_config.get("server", {})
    if "host" in server and "port" in server:
        services.append({"name": LOCAL_MODELS_SERVICE_NAME, "base_url": f"http://{server['host']}:{server['port']}"})
    jev = agent_config.get("jev", {})
    if jev.get("enabled") and "base_url" in jev:
        services.append({"name": JEV_SERVICE_NAME, "base_url": jev["base_url"]})
    return services
