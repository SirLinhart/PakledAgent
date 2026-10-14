"""Entry point for `python -m agent`.

Start the agent from the repository root, with the Official Agent Key in
`.env` (`ALTRUAGENT_OFFICIAL_AGENT_KEY`):

    python -m agent --match               # test matches (Testing page)
    python -m agent --tournament          # tournament games
    python -m agent --tournament --match  # both, in one process

The flags, their messages and the runtime behind them are the official
starter's: this file is derived from its `agent/__main__.py`, and
`altruagent.supervisor.run_tournament_forever` signs in, polls for assigned
games and keeps one worker process per game until Ctrl+C. What this entry
point adds is the run around it (`ucla_agent.core.startup.run_session`): one
copy of the agent at a time, the configuration loaded before the first poll,
and a `run_started` and a `run_stopped` line in the run record.

`--check-tournament` verifies the connection and the agent without playing; it
takes no lock and writes no run line, so it can be used while the agent runs.
`--agent MODULE[:FACTORY]` picks a different factory than
`agent.agent:create_agent`, which returns our dispatcher.

Retired modes only print a notice pointing to the commands above (see
`altruagent.notices`): `python -m agent` with no mode (the old platform
API-key mode) and `--claim` (Testing claim codes).

IMPORTANT (Windows multiprocessing): the `if __name__ == "__main__":` guard
at the bottom of this file is not just style. `multiprocessing`'s `spawn`
start method, which the runtime uses everywhere, re-imports this exact module
in every worker process. Without the guard each worker would run `main()`
itself, try to take the lock and spawn further workers.
"""

from __future__ import annotations

import argparse
import os
import sys

from altruagent.agent_loader import DEFAULT_AGENT_SPEC
from altruagent.agent_loader import load_agent_factory as _load_agent_factory
from altruagent.errors import AltruAgentError, AuthenticationError, ConfigurationError
from altruagent.notices import AGENT_GUIDE_URL, CLAIM_CODES_RETIRED_NOTICE, PLATFORM_KEY_RETIRED_NOTICE
from altruagent.official import OfficialAgentClient, OfficialAgentError, is_fatal_auth_error, is_registration_incomplete
from altruagent.runner import _resolve_decision_fn
from altruagent.supervisor import ALL_KINDS, TESTING, TOURNAMENT, WAITING_MESSAGE, run_tournament_forever

from ucla_agent.core.console import can_print, print_line
from ucla_agent.core.startup import run_session

CLAIM_TOKEN_ENV = "ALTRUAGENT_CLAIM_TOKEN"

# What the runtime says it plays, by the kinds the flags chose.
PLAYING_MESSAGES = {
    ALL_KINDS: "Playing your test matches and tournament games.",
    frozenset({TOURNAMENT}): "Playing your tournament games only. Add --match to also play your test matches.",
    frozenset({TESTING}): "Playing your test matches only. Add --tournament to also play your tournament games.",
}


# -- playing ---------------------------------------------------------------------------------------


def _run_tournament(agent_spec: str, kinds: frozenset[str] = ALL_KINDS) -> int:
    """The runtime: authenticate with the Official Agent Key, then keep one
    worker process per assigned game of the chosen ``kinds`` (test matches,
    tournament games or both) until Ctrl+C.
    """
    try:
        _load_agent_factory(agent_spec)  # validated eagerly; built once per match, in its worker
    except ValueError as exc:
        print_line(f"Startup error: {exc}")
        return 1

    try:
        official = OfficialAgentClient()
    except ConfigurationError as exc:
        print_line(f"Configuration error: {exc}")
        return 1

    try:
        return _play_assigned_games(official, agent_spec, kinds)
    finally:
        official.close()


def _play_assigned_games(official: OfficialAgentClient, agent_spec: str, kinds: frozenset[str]) -> int:
    try:
        if not _sign_in(official):
            return 1
        print_line(PLAYING_MESSAGES[kinds])
        print_line(f"{WAITING_MESSAGE} (Press Ctrl+C to stop.)")
        run_tournament_forever(official, agent_spec=agent_spec, kinds=kinds)
        return 0
    except KeyboardInterrupt:
        print_line("\nStopped.")
        return 0
    except AltruAgentError as exc:
        print_line(f"\nStopped due to an unrecoverable error: {exc}")
        return 1


def _sign_in(official: OfficialAgentClient) -> bool:
    """Sign in with the Official Agent Key; whether the runtime should start.

    An incomplete event registration (for example updated Official Rules to
    accept) is not a reason to stop: the key is fine and only the contestant
    can finish the registration, on the dashboard. The runtime loop prints
    what to do and keeps checking, so the agent plays as soon as they have.
    """
    try:
        official.authenticate()
    except AltruAgentError as exc:
        if is_registration_incomplete(exc):
            return True
        print_line(f"Could not connect with your Official Agent Key: {exc}")
        return False
    print_line("Connected with your Official Agent Key.")
    return True


# -- checking --------------------------------------------------------------------------------------


def _mark(symbol: str, fallback: str) -> str:
    """``symbol`` if the console can print it (a redirected Windows console may not)."""
    return symbol if can_print(symbol) else fallback


def _ok(message: str) -> None:
    print_line(f"{_mark('✓', '[ok]')} {message}")


def _fail(message: str) -> None:
    print_line(f"{_mark('✗', '[FAIL]')} {message}")


def _check_tournament(agent_spec: str) -> int:
    """Pre-tournament connection check. Needs no assigned match; never prints
    the key or any token. Returns 0 only if every step passes.
    """
    try:
        official = OfficialAgentClient()
    except ConfigurationError as exc:
        _fail(str(exc))
        return 1

    try:
        passed = _check_sign_in(official) and _check_assignments(official) and _check_agent(agent_spec)
    finally:
        official.close()
    if not passed:
        return 1
    _ok("Ready to play Testing and tournament games")
    return 0


def _check_sign_in(official: OfficialAgentClient) -> bool:
    try:
        official.authenticate()
    except OfficialAgentError as exc:
        _ok("Control plane reachable")
        if exc.status_code in (400, 401) and is_fatal_auth_error(exc):
            _fail(f"Official Agent Key rejected: {exc}")
        else:
            _fail(f"Official agent authentication failed: {exc}")
        return False
    except AltruAgentError as exc:
        _fail(f"Control plane not reachable at {official.control_url}: {exc}")
        return False
    _ok("Control plane reachable")
    _ok("Official Agent Key accepted")
    return True


def _check_assignments(official: OfficialAgentClient) -> bool:
    try:
        assignments = official.assignments()
    except AuthenticationError as exc:
        _fail(f"Tournament agent session was not accepted: {exc}")
        return False
    except AltruAgentError as exc:
        _ok("Tournament agent authenticated")
        _fail(f"Assignment discovery failed: {exc}")
        return False
    _ok("Tournament agent authenticated")
    _ok(f"Assignment discovery available ({len(assignments)} active assignment(s))")
    return True


def _check_agent(agent_spec: str) -> bool:
    try:
        _resolve_decision_fn(_load_agent_factory(agent_spec)())
    except Exception as exc:  # noqa: BLE001 - any failure of the agent factory is reported, not raised
        _fail(f"Agent {agent_spec} could not be created: {exc}")
        return False
    _ok(f"Agent ready ({agent_spec})")
    return True


# -- the command line ------------------------------------------------------------------------------

_DESCRIPTION = """\
Run the tournament agent.

  1. Put your Official Agent Key in .env (ALTRUAGENT_OFFICIAL_AGENT_KEY). It is
     generated on the tournament dashboard's Agent Configuration page.
  2. From the repository root, run it with --match (your test matches),
     --tournament (your tournament games) or both, and leave it running.

Each assigned game is played in its own worker process by the decision-maker
registered for its game type. While it waits for a game it only asks the
platform whether one is ready (every ~10 s) and makes no model call. One copy
runs at a time: a second one exits with code 3."""

_EPILOG = f"""examples:
  python -m agent --check-tournament       check your key, connection and agent
  python -m agent --match                  play your test matches (Ctrl+C to stop)
  python -m agent --tournament             play your tournament games
  python -m agent --tournament --match     play both in one process

run instructions: docs/RUNNING.md
platform guide: {AGENT_GUIDE_URL}"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # --tournament and --match may be combined (both kinds in one process);
    # each excludes --check-tournament and --claim. --match's exclusions are
    # checked in _reject_conflicting_modes: argparse puts an option in one
    # exclusive group only.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--tournament",
        action="store_true",
        help=(
            'play your tournament games (Swiss/bracket games, after you press "Register my agent"); '
            "runs until Ctrl+C"
        ),
    )
    parser.add_argument(
        "--match",
        action="store_true",
        help=(
            'play your test matches (Testing page: matches you create with "Mine (self-hosted)" '
            "or join from Open matches); runs until Ctrl+C"
        ),
    )
    mode.add_argument(
        "--check-tournament",
        action="store_true",
        help="check your Official Agent Key, connection and agent without playing anything",
    )
    # Testing claim codes are retired on the platform. The flag is still parsed so
    # an old command prints the notice instead of an argparse error; hidden from --help.
    mode.add_argument("--claim", nargs="?", const="", metavar="TOKEN", help=argparse.SUPPRESS)
    parser.add_argument(
        "--agent",
        metavar="MODULE[:FACTORY]",
        help=f"agent factory to use (default: {DEFAULT_AGENT_SPEC})",
    )
    return parser


def _reject_conflicting_modes(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.match and args.check_tournament:
        parser.error("argument --match: not allowed with argument --check-tournament")
    if args.match and args.claim is not None:
        parser.error("argument --match: not allowed with argument --claim")


def _chosen_kinds(args: argparse.Namespace) -> frozenset[str]:
    """The kinds of game the flags ask to play; empty when no playing flag was given."""
    return frozenset(kind for kind, chosen in ((TOURNAMENT, args.tournament), (TESTING, args.match)) if chosen)


def _retired(notice: str) -> int:
    """Print a retired mode's notice and fail: nothing was run."""
    print_line(notice)
    return 1


def _retired_mode(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """No playing or checking flag was given: answer with the notice for the retired mode the command meant."""
    if args.claim is not None:
        return _retired(CLAIM_CODES_RETIRED_NOTICE)
    if args.agent is not None:
        parser.error("--agent is only supported together with --tournament, --match or --check-tournament")
    if os.environ.get(CLAIM_TOKEN_ENV):
        return _retired(CLAIM_CODES_RETIRED_NOTICE)
    return _retired(PLATFORM_KEY_RETIRED_NOTICE)


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run the chosen mode; returns the process exit code."""
    flags = [] if argv is None else argv
    parser = _build_parser()
    args = parser.parse_args(flags)
    _reject_conflicting_modes(parser, args)

    agent_spec = args.agent or DEFAULT_AGENT_SPEC
    if args.check_tournament:
        return _check_tournament(agent_spec)
    kinds = _chosen_kinds(args)
    if kinds:
        return run_session(flags, lambda: _run_tournament(agent_spec, kinds))
    return _retired_mode(parser, args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
