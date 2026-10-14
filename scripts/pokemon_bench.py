"""Play the Pokémon practice benchmark: our seat against one practice opponent, a number of seeded matches.

    python scripts/pokemon_bench.py --label <label> --opponent random|first|self --games N
        [--seed-base 5000] [--models off|on|brief|decide] [--maximin off|on] [--model-port 11600]
        [--model-backend-port-base 41600]

Run it from the repository root, after `npm ci` in `tools/pokemon_sim`. It
plays N practice matches of `pokemon_vgc_doubles_draft` on a local Showdown:
match i has seed `seed base + i`, which draws its card pool, and our seat
makes the draft's first pick on an even seed, the second on an odd one, so
two labels run with one seed base draft from the same pools. Our seat is the
Pokémon agent in one of four arms: `--models off` (the default) has its
model choice off, the script's best every time; `--models on` has the local
`strong` model choose among the script's best few, ranked, reading the
one-line summary of the rosters or the board (`model_briefing` and
`model_decides` off); `--models brief` has it choose among them reading the
full briefing (`model_briefing` on, `model_decides` off); `--models decide`
has it read the full briefing and choose among the script's wider shortlist,
unscored and shuffled, writing a reason first (`model_briefing` and
`model_decides` on). Each arm sets both switches itself, whatever
`config/agent.toml` ships. `--maximin on` has our seat rank every phase by
maximin (`[maximin] enabled` of `config/pokemon/tuning.toml` on), and
`--maximin off` (the default) by the script's own ranking, whatever the
tuning file ships; `summary.json` records it as `maximin`. The opponent is
`random` (a random legal choice), `first` (the first legal option, the
starter's smoke agent) or `self` (the agent itself, models off, with the
tracked tuning, whose own records are kept nowhere).

Everything goes into `runs/practice/pokemon/<label>/` (under the folder
`UCLA_AGENT_RUNS_DIR` names, when it is set): our seat's match records,
`results.csv` with one row per match and `summary.json`. A match that raises
is a row with an unhandled error, and the run goes on.
`python scripts/report.py --pokemon-bench <folder>` prints a label's figures;
`--pokemon-bench-compare` compares two.

`--models on`, `brief` and `decide` need the local model server with the
roles the `pokemon_choice` chain names (`strong` as tracked), on the run's
own ports: the endpoint on `--model-port` (11600 by default) and the
`llama-server`s from `--model-backend-port-base` (41600 by default), never
the ports of `config/models.toml` the live agent uses (11500, and 41500 up).
A model server that already serves the roles on that port is used;
otherwise the included one is started there for the run with only those
roles, which are printed, and stopped at its end. `summary.json` records the
endpoint as `model_endpoint`. The benchmark never contacts the platform or a
hosted model.

Exit code 0: the run was played and its files written. 2: the command line is
wrong, the configuration cannot be read, the configuration would have a
hosted model asked, a model port is one the live agent uses, the label
already has results, or a model role the run needs cannot be made ready.
"""

from __future__ import annotations

import argparse
import sys
import time

from ucla_agent.core.config import ConfigError
from ucla_agent.core.console import print_line
from ucla_agent.core.modelserver import settings as server_settings
from ucla_agent.core.paths import repository_root
from ucla_agent.practice.pokemon import bench, bench_agent
from ucla_agent.practice.pokemon.opponents import OPPONENT_FACTORIES
from ucla_agent.practice.werewolf import bench_models

EXIT_PLAYED = 0
EXIT_REFUSED = 2
MAXIMIN_OFF, MAXIMIN_ON = "off", "on"
MAXIMIN_ARMS = (MAXIMIN_OFF, MAXIMIN_ON)
_SECONDS_DECIMALS = 2


def _positive(text: str) -> int:
    count = int(text)
    if count < 1:
        raise argparse.ArgumentTypeError(f"needs at least 1 match, not {count}")
    return count


def _port(text: str) -> int:
    port = int(text)
    if not 1 <= port <= server_settings.MAX_PORT:
        raise argparse.ArgumentTypeError(f"needs a port from 1 to {server_settings.MAX_PORT}, not {port}")
    return port


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python scripts/pokemon_bench.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--label", required=True, help="the name of the run: its folder under runs/practice/pokemon/")
    parser.add_argument(
        "--opponent", required=True, choices=sorted(OPPONENT_FACTORIES), help="who plays the other seat"
    )
    parser.add_argument("--games", required=True, type=_positive, metavar="N", help="how many matches to play")
    parser.add_argument(
        "--seed-base",
        type=int,
        default=bench.DEFAULT_SEED_BASE,
        help=f"the seed of the first match (default {bench.DEFAULT_SEED_BASE})",
    )
    parser.add_argument(
        "--models",
        choices=bench_agent.ARMS,
        default=bench_agent.MODELS_OFF,
        help="off: the script's best; on: the local model chooses among the script's best few; brief: it chooses "
        "reading the full briefing; decide: it reads the full briefing and chooses among the script's wider "
        "shortlist, unscored and shuffled (default off)",
    )
    parser.add_argument(
        "--maximin",
        choices=MAXIMIN_ARMS,
        default=MAXIMIN_OFF,
        help="on: our seat ranks every phase by maximin; off: by the script's own ranking (default off)",
    )
    parser.add_argument(
        "--model-port",
        type=_port,
        default=bench_agent.BENCH_MODEL_PORT,
        help=f"the port of the run's model server, never the live agent's (default {bench_agent.BENCH_MODEL_PORT})",
    )
    parser.add_argument(
        "--model-backend-port-base",
        type=_port,
        default=bench_agent.BENCH_BACKEND_PORT_BASE,
        help="the port of the run's first llama-server, one more for each role after it "
        f"(default {bench_agent.BENCH_BACKEND_PORT_BASE})",
    )
    return parser


def match_line(played: bench.PlayedMatch, seconds: float) -> str:
    """The line printed for one match as soon as it is played."""
    row = played.row
    match = f"seed {row['seed']} ({seconds:.{_SECONDS_DECIMALS}f} s)"
    if row["turns"] is None:
        return f"{match}: raised {played.error}"
    outcome = "won" if row["our_won"] else "did not win"
    line = (
        f"{match}: {outcome} in {row['turns']} turns; {row['decisions']} decisions, {row['fallbacks']} fallbacks, "
        f"{row['parse_degradations']} parse degradations, {row['unhandled_errors']} unhandled errors"
    )
    return line if played.error is None else f"{line}; ended early: {played.error}"


def main(arguments: list[str]) -> int:
    """Play the run the command line asks for and write its files; the exit code."""
    args = _parser().parse_args(arguments)
    root = repository_root()
    folder = bench.label_folder(args.label)
    if (folder / bench.RESULTS_FILE_NAME).exists():
        print_line(f"{folder} already holds {bench.RESULTS_FILE_NAME}: give the run a new label", error=True)
        return EXIT_REFUSED
    last_match_end = time.perf_counter()

    def report(played: bench.PlayedMatch) -> None:
        nonlocal last_match_end
        now = time.perf_counter()
        print_line(match_line(played, now - last_match_end))
        last_match_end = now

    try:
        with bench_agent.model_server_ports(args.model_port, args.model_backend_port_base, root) as endpoint:
            seat = bench_agent.make_seat(args.models, root, maximin=args.maximin == MAXIMIN_ON)
            settings = {**seat.settings, "model_endpoint": endpoint if seat.model_roles else None}
            with bench_models.serving(seat.model_roles, root) as started:
                planned = bench.planned_matches(args.games, args.seed_base)
                last_match_end = time.perf_counter()
                bench.run_bench(
                    folder,
                    seat.factory,
                    args.opponent,
                    planned,
                    settings=settings,
                    model_roles_started=started,
                    on_match=report,
                )
    except (ConfigError, bench_agent.HostedModelRefused, bench_models.ModelsUnavailable) as exc:
        print_line(str(exc), error=True)
        return EXIT_REFUSED
    print_line(f"Wrote {folder / bench.RESULTS_FILE_NAME}")
    print_line(f"Wrote {folder / bench.SUMMARY_FILE_NAME}")
    return EXIT_PLAYED


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
