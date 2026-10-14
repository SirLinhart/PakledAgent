"""The readiness check's command line: the eight checks in order, one line each, and the games that are ready.

    python -m ucla_agent.check [--offline]

Run it from the repository root before test matches and before the
tournament. It loads `.env` first, so that the keys there are used and never
printed, then runs config, secrets_scan, platform, games, model_server, jev,
records and fallback, printing one `<STATUS> <name>: <detail>` line as each
finishes, and ends with `Games ready: <comma list>`. A check that breaks in a
way it did not expect is reported on its own line and the others still run.
With `--offline` the three checks that reach a service (platform,
model_server, jev) are reported as SKIP and nothing outside the machine is
contacted.

Exit code 0: no line is FAIL. 1: at least one is.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable

from ucla_agent.check import fallback, local, services
from ucla_agent.check.line import FAIL, SKIP, WARN, CheckLine
from ucla_agent.core import secrets
from ucla_agent.core.console import print_line
from ucla_agent.core.decide import DecisionSource
from ucla_agent.core.decide.settings import JevSettings
from ucla_agent.core.paths import repository_root

EXIT_READY = 0
EXIT_NOT_READY = 1
OFFLINE = "skipped (--offline)"
GAMES_READY = "Games ready: {games}"
NO_GAME = "none"
# The game whose readiness also rests on the fallback check: its decision-maker is the one that check plays.
RED_ALERT = "red_alert"


@dataclass(frozen=True)
class Services:
    """How the checks reach the platform, the included model server and Jev; the defaults are the real ones."""

    platform_client: Callable[[], services.PlatformClient] = services.official_client
    model_server_probe: services.RoleProbe = services.probe_included_server
    jev_source: Callable[[JevSettings], DecisionSource] = services.jev_source


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ucla_agent.check",
        description="Check that everything the agent depends on works, without playing anything.",
    )
    parser.add_argument(
        "--offline", action="store_true", help="skip the checks that reach a service: platform, model_server and jev"
    )
    return parser


def main(arguments: list[str], reach: Services = Services()) -> int:
    """Run the checks the command line asks for, print their lines and return the exit code."""
    offline = _parser().parse_args(arguments).offline
    secrets.load_secrets()
    root = repository_root()
    games: list[local.GamesFound] = []

    def check_games() -> CheckLine:
        games.append(local.check_games())
        return games[0].line

    # (name, whether it reaches a service, the status of a check that breaks unexpectedly, the check)
    checks = (
        (local.CONFIG, False, FAIL, lambda: local.check_config(root)),
        (local.SECRETS_SCAN, False, FAIL, lambda: local.check_secrets_scan(root)),
        (services.PLATFORM, True, FAIL, lambda: services.check_platform(reach.platform_client)),
        (local.GAMES, False, FAIL, check_games),
        (services.MODEL_SERVER, True, FAIL, lambda: services.check_model_server(root, reach.model_server_probe)),
        (services.JEV, True, WARN, lambda: services.check_jev(root, reach.jev_source)),
        (local.RECORDS, False, FAIL, lambda: local.check_records(root)),
        (fallback.FALLBACK, False, FAIL, lambda: fallback.check_fallback(root)),
    )
    lines = []
    for name, reaches_a_service, status_if_broken, check in checks:
        line = CheckLine(SKIP, name, OFFLINE) if offline and reaches_a_service else _run(name, status_if_broken, check)
        print_line(line.text())
        lines.append(line)
    print_line(GAMES_READY.format(games=", ".join(_ready(games, lines)) or NO_GAME))
    return EXIT_NOT_READY if any(line.status == FAIL for line in lines) else EXIT_READY


def _run(name: str, status_if_broken: str, check: Callable[[], CheckLine]) -> CheckLine:
    """A check's line; one that raises is reported on its line, so that the checks after it still run."""
    try:
        return check()
    except Exception as exc:  # noqa: BLE001 - one broken check must not hide what the others find
        return CheckLine(status_if_broken, name, f"the check could not run: {type(exc).__name__}: {exc}")


def _ready(games: list[local.GamesFound], lines: list[CheckLine]) -> list[str]:
    """The games whose decision-maker could be made; Red Alert only while its fallback check did not fail."""
    fallback_failed = any(line.name == fallback.FALLBACK and line.status == FAIL for line in lines)
    made = games[0].ready if games else ()
    return [game for game in made if not (game == RED_ALERT and fallback_failed)]
