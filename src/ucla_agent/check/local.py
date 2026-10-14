"""The readiness checks that need nothing beyond this machine: configuration, secrets, games and records.

Each check reads what the agent itself would read, the way the agent reads
it, and turns what it finds into one `CheckLine`. A check raises only for
something it did not expect; the command reports that on the check's line.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ucla_agent.check.line import FAIL, PASS, CheckLine
from ucla_agent.core import registry
from ucla_agent.core.config import CONFIG_DIR_NAME, ConfigError, load_agent_config
from ucla_agent.core.decide import build_chain
from ucla_agent.core.decide import settings as decide_settings
from ucla_agent.core.modelserver import settings as server_settings
from ucla_agent.core.records import RUNS_DIR_NAME, EventWriter
from ucla_agent.games.pokemon import data as pokemon_data
from ucla_agent.games.pokemon.settings import PokemonSettings
from ucla_agent.games.pokemon.tuning import TUNING_FILE as POKEMON_TUNING_FILE
from ucla_agent.games.pokemon.tuning import Tuning as PokemonTuning
from ucla_agent.games.redalert.glossary import GLOSSARY_FILE, Glossary
from ucla_agent.games.redalert.items import DamageTable, ItemTable
from ucla_agent.games.redalert.settings import RedAlertSettings
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.werewolf.settings import WerewolfSettings
from ucla_agent.games.werewolf.tuning import Tuning as WerewolfTuning
from ucla_agent.practice.pokemon import cards as practice_cards

CONFIG = "config"
SECRETS_SCAN = "secrets_scan"
GAMES = "games"
RECORDS = "records"
# The games of the tournament, in the order the check lists them.
TOURNAMENT_GAMES = ("red_alert", "werewolf", "pokemon_vgc_doubles_draft", "honor_of_kings")
READY = "ready"
NOT_AVAILABLE = "not available"
SCAN_SCRIPT = Path("scripts") / "scan_secrets.py"
SCAN_CLEAN = 0
SCAN_FOUND_SECRETS = 1
PROBE_FILE = Path(RUNS_DIR_NAME) / ".check" / "probe.jsonl"
PROBE_KIND = "probe"
_FILE_ENCODING = "utf-8"
# The folder `ucla_agent` was loaded from, which a script started in a child process has to be able to import from.
_SOURCE_FOLDER = Path(__file__).resolve().parents[2]


def _read_agent_settings(root: Path) -> None:
    """`config/agent.toml` as the agent reads it: its own tables, the decision settings and chains, and each game's."""
    load_agent_config(root)
    for chain in decide_settings.load(root).chains:
        build_chain(chain)
    RedAlertSettings.from_file(root)
    WerewolfSettings.from_files(root)
    PokemonSettings.from_files(root)


def _pokemon_table_reader(file_name: str) -> Callable[[Path], Any]:
    """The reader of one table of the Pokémon game data under `root`."""
    return lambda root: pokemon_data.read_table(root / pokemon_data.DATA_FOLDER / file_name)


def _read_pokemon_license_note(root: Path) -> str:
    return pokemon_data.read_license_note(root / pokemon_data.DATA_FOLDER / pokemon_data.LICENSE_FILE)


def _read_pokemon_practice_cards(root: Path) -> practice_cards.CardCatalog:
    """The Pokémon practice card catalog, as the local practice harness reads it."""
    path = root / practice_cards.CATALOG_PATH
    try:
        return practice_cards.load_catalog(path)
    except FileNotFoundError as exc:
        raise ConfigError(f"{path} was not found: rebuild it with scripts/build_pokemon_cards.py.") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise ConfigError(
            f"{path} is not a card catalog the practice harness can read ({type(exc).__name__}: {exc})."
        ) from exc


# Each file under `config/`, by its path from the project root, and how the agent reads it.
CONFIG_READERS: dict[str, Callable[[Path], Any]] = {
    f"{CONFIG_DIR_NAME}/agent.toml": _read_agent_settings,
    f"{CONFIG_DIR_NAME}/{server_settings.MODELS_CONFIG_FILE_NAME}": server_settings.load,
    f"{CONFIG_DIR_NAME}/redalert/items.toml": ItemTable.from_file,
    f"{CONFIG_DIR_NAME}/redalert/damage.toml": DamageTable.from_file,
    f"{CONFIG_DIR_NAME}/redalert/tuning.toml": Tuning.from_file,
    GLOSSARY_FILE.as_posix(): Glossary.from_file,
    f"{CONFIG_DIR_NAME}/werewolf/tuning.toml": WerewolfTuning.from_file,
    POKEMON_TUNING_FILE.as_posix(): PokemonTuning.from_file,
    **{(pokemon_data.DATA_FOLDER / name).as_posix(): _pokemon_table_reader(name) for name in pokemon_data.TABLE_FILES},
    (pokemon_data.DATA_FOLDER / pokemon_data.LICENSE_FILE).as_posix(): _read_pokemon_license_note,
    practice_cards.CATALOG_PATH.as_posix(): _read_pokemon_practice_cards,
}


# The game references `scripts/capture_rules.py` stores: reference material that no part of the agent reads.
CAPTURED_RULES = re.compile(rf"{CONFIG_DIR_NAME}/redalert/captured/rules-\d{{8}}\.json")
GAME_REFERENCE_RULES = "rules"


def check_config(root: Path) -> CheckLine:
    """Every file under `config/` parses and passes its schema; a file nothing reads is a failure too."""
    present = sorted(
        path.relative_to(root).as_posix() for path in (root / CONFIG_DIR_NAME).rglob("*") if path.is_file()
    )
    problems = [problem for name in present if name not in CONFIG_READERS and (problem := _unread(root, name))]
    for read in CONFIG_READERS.values():
        try:
            read(root)
        except ConfigError as exc:
            problems.append(str(exc))
    if problems:
        # One file's fault can surface twice: the chains of agent.toml are checked against models.toml.
        return CheckLine(FAIL, CONFIG, "; ".join(dict.fromkeys(problems)))
    return CheckLine(PASS, CONFIG, f"{len(present)} files under {CONFIG_DIR_NAME}/ parse and pass their schema")


def _unread(root: Path, name: str) -> str | None:
    """What is wrong with a file under `config/` that the agent does not read; None for a sound captured reference."""
    if not CAPTURED_RULES.fullmatch(name):
        return f"{name} is read by nothing: the agent has no schema for it"
    try:
        reference = json.loads((root / name).read_text(encoding=_FILE_ENCODING))
    except ValueError as exc:
        return f"{name} is not valid JSON: {exc}"
    if not isinstance(reference, dict) or not isinstance(reference.get(GAME_REFERENCE_RULES), dict):
        return f"{name} is no captured game reference: it has no {GAME_REFERENCE_RULES} table"
    return None


def check_secrets_scan(root: Path) -> CheckLine:
    """`scripts/scan_secrets.py` finds no secret in a tracked file; only the places of hits are named, never a line."""
    import_path = os.pathsep.join(filter(None, [str(_SOURCE_FOLDER), os.environ.get("PYTHONPATH")]))
    environment = {**os.environ, "PYTHONPATH": import_path}
    completed = subprocess.run(
        [sys.executable, str(root / SCAN_SCRIPT)], cwd=root, env=environment, capture_output=True, text=True
    )
    hits = completed.stdout.split()
    if completed.returncode == SCAN_CLEAN:
        return CheckLine(PASS, SECRETS_SCAN, "no file git tracks holds a secret")
    if completed.returncode == SCAN_FOUND_SECRETS:
        return CheckLine(FAIL, SECRETS_SCAN, f"{len(hits)} line(s) of tracked files hold a secret: {', '.join(hits)}")
    said = (completed.stderr or completed.stdout).strip()
    return CheckLine(FAIL, SECRETS_SCAN, f"{SCAN_SCRIPT.as_posix()} exited with code {completed.returncode}: {said}")


@dataclass(frozen=True)
class GamesFound:
    """The `games` check's line, and the games whose decision-maker could be made."""

    line: CheckLine
    ready: tuple[str, ...]


def check_games() -> GamesFound:
    """For each tournament game, whether a decision-maker is registered and can be made; a factory that raises fails."""
    ready, said, failures = [], [], []
    for game in TOURNAMENT_GAMES:
        factory = registry.GAME_FACTORIES.get(game)
        if factory is None:
            said.append(f"{game} {NOT_AVAILABLE}")
            continue
        try:
            factory()
        except Exception as exc:  # noqa: BLE001 - whatever stops a decision-maker being made is what the check reports
            failures.append(game)
            said.append(f"{game} could not be made ({type(exc).__name__}: {exc})")
            continue
        ready.append(game)
        said.append(f"{game} {READY}")
    return GamesFound(CheckLine(FAIL if failures else PASS, GAMES, ", ".join(said)), tuple(ready))


def check_records(root: Path) -> CheckLine:
    """A probe event written to `runs/.check/probe.jsonl` through the record writer is then in the file."""
    path = root / PROBE_FILE
    token = uuid.uuid4().hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Opened anew on every run, so the file only ever holds the probe of the last check.
        writer = EventWriter(lambda: open(path, "w", encoding=_FILE_ENCODING))
        writer.submit(PROBE_KIND, {"probe": token})
        writer.close()
        found = _holds_probe(path, token)
    except OSError as exc:
        return CheckLine(FAIL, RECORDS, f"{PROBE_FILE.as_posix()} could not be written or read: {exc}")
    if not found:
        return CheckLine(FAIL, RECORDS, f"the probe event did not reach {PROBE_FILE.as_posix()}")
    return CheckLine(PASS, RECORDS, f"a probe event was written to {PROBE_FILE.as_posix()} and read back")


def _holds_probe(path: Path, token: str) -> bool:
    for line in path.read_text(encoding=_FILE_ENCODING).splitlines():
        event = json.loads(line)
        if event.get("kind") == PROBE_KIND and event.get("data", {}).get("probe") == token:
            return True
    return False
