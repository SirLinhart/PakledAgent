"""Serve the live feed page of a match record.

    python -m ucla_agent.feed [--match <match folder> | --latest] [--port 8765] [--runs-dir <dir>]

With `--match` the page shows that one match folder
(`runs/<UTC yyyymmdd>/<session id>__<seat>`). With `--latest`, the default,
it shows the newest match under the runs folder (`--runs-dir`, else
`UCLA_AGENT_RUNS_DIR`, else `runs/` of the project root) and moves on to a
newer one as soon as it appears. Open http://127.0.0.1:<port>/ in a browser;
Ctrl+C stops the server.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ucla_agent.core.console import print_line
from ucla_agent.feed.server import DEFAULT_PORT, LOOPBACK, FeedServer, FeedView
from ucla_agent.feed.sources import FixedMatch, LatestMatch, Source, default_runs_folder

EXIT_STOPPED = 0
EXIT_CANNOT_SERVE = 1
EXIT_BAD_ARGUMENTS = 2
# How often the serving loop looks whether Ctrl+C was pressed.
_POLL_SECONDS = 0.2


def main(argv: list[str] | None = None) -> int:
    """Serve the page until Ctrl+C; the exit code says whether it could serve at all."""
    arguments = _parser().parse_args(argv)
    if arguments.match is not None and not Path(arguments.match).is_dir():
        print_line(f"There is no match folder at {arguments.match}.", error=True)
        return EXIT_BAD_ARGUMENTS
    source, following = _source(arguments)
    try:
        server = FeedServer(arguments.port, FeedView(source))
    except OSError as exc:
        print_line(f"The feed cannot listen on {LOOPBACK}:{arguments.port}: {exc}", error=True)
        return EXIT_CANNOT_SERVE
    print_line(f"Feed: http://{LOOPBACK}:{server.port}/ shows {following}. Press Ctrl+C to stop it.")
    try:
        server.serve_forever(_POLL_SECONDS)
    except KeyboardInterrupt:
        print_line("Feed stopped.")
    finally:
        server.server_close()
    return EXIT_STOPPED


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ucla_agent.feed", description="Serve the live feed page.")
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--match", help="the match folder to show")
    which.add_argument("--latest", action="store_true", help="show the newest match under the runs folder (default)")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"the port on {LOOPBACK} (default {DEFAULT_PORT})"
    )
    parser.add_argument("--runs-dir", help="the runs folder --latest looks in (default: UCLA_AGENT_RUNS_DIR or runs/)")
    return parser


def _source(arguments: argparse.Namespace) -> tuple[Source, str]:
    """The source the arguments name, and how the start line describes it."""
    if arguments.match is not None:
        folder = Path(arguments.match)
        return FixedMatch(folder), f"the match in {folder}"
    runs = Path(arguments.runs_dir) if arguments.runs_dir else default_runs_folder()
    return LatestMatch(runs), f"the newest match under {runs}"


if __name__ == "__main__":
    sys.exit(main())
