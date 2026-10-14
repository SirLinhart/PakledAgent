"""Play the Werewolf practice benchmark: one arm in our seat, a number of games for each role, at a practice table.

    python scripts/werewolf_bench.py --label <label> --arm baseline|agent-script|agent-model
        --mix scripted|mixed|self --games-per-role N [--seed-base 1000] [--tuning <path>]

Run it from the repository root. It plays N games for each of the roles
villager, seer and wolf on the local practice table: game i of a role has
seed `seed base + i`, forces that role on our seat and seats us at
`seed % 7`, so two arms run with one seed base play identical deals, seats
and opponent seatings. The arm says who plays our seat: `baseline` the
practice table's record-voting script, `agent-script` the Werewolf agent with
its models switched off, `agent-model` the agent as configured. The mix
names the six opponents: `scripted` (scripts only), `mixed` (two of them
play on the local model) or `self` (six copies of the Werewolf agent with its
models switched off, whose own records are kept nowhere). With `--tuning`,
every Werewolf agent of the run, ours and the `self` copies, plays the tuning
file at that path instead of `config/werewolf/tuning.toml`.

Everything goes into `runs/practice/werewolf/<label>/` (under the folder
`UCLA_AGENT_RUNS_DIR` names, when it is set): our seat's match records,
`results.csv` with one row per game and `summary.json` with the arm, the mix,
the settings, the tuning file's path and SHA-256, the model server roles
started for the run and each role's games and wins. A game that raises is a
row with an unhandled error, and the run goes on. `python scripts/report.py --werewolf-bench <folder>` prints a
label's table; `--werewolf-bench-compare` compares two.

`agent-model` and `mixed` need the local model server with the roles they
ask (`strong` for the agent as tracked, `fast` for the model opponents). A
model server that already serves them is used; otherwise the included one is
started for the run with only those roles, which are printed, and stopped at
its end. The benchmark never contacts the platform or Claude.

Exit code 0: the run was played and its files written. 2: the command line is
wrong, the configuration or the tuning file cannot be read, the arm is
refused, the label already has results, or a model role the run needs cannot
be made ready.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from ucla_agent.core.config import ConfigError
from ucla_agent.core.console import print_line
from ucla_agent.core.paths import repository_root
from ucla_agent.practice.werewolf import arms, bench, bench_models, bench_tuning
from ucla_agent.practice.werewolf.llm_player import FAST_ROLE
from ucla_agent.practice.werewolf.mixes import LLM_PLAYER, MIXES

EXIT_PLAYED = 0
EXIT_REFUSED = 2
_SECONDS_DECIMALS = 2


def _positive(text: str) -> int:
    count = int(text)
    if count < 1:
        raise argparse.ArgumentTypeError(f"needs at least 1 game, not {count}")
    return count


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python scripts/werewolf_bench.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--label", required=True, help="the name of the run: its folder under runs/practice/werewolf/")
    parser.add_argument("--arm", required=True, choices=arms.ARMS, help="who plays our seat")
    parser.add_argument("--mix", required=True, choices=sorted(MIXES), help="the six opponents at the table")
    parser.add_argument("--games-per-role", required=True, type=_positive, metavar="N", help="games for each role")
    parser.add_argument(
        "--seed-base",
        type=int,
        default=bench.DEFAULT_SEED_BASE,
        help=f"the seed of each role's first game (default {bench.DEFAULT_SEED_BASE})",
    )
    parser.add_argument(
        "--tuning",
        type=Path,
        metavar="PATH",
        help="a Werewolf tuning file every Werewolf agent of the run plays, in place of config/werewolf/tuning.toml",
    )
    return parser


def model_roles(arm: arms.Arm, mix: str) -> frozenset[str]:
    """The model server roles a run asks: those of the arm's chains, and `fast` when the mix seats model players."""
    return arm.model_roles | ({FAST_ROLE} if LLM_PLAYER in MIXES[mix] else frozenset())


def game_line(played: bench.PlayedGame, seconds: float) -> str:
    """The line printed for one game as soon as it is played."""
    row = played.row
    game = f"{row['role']} seed {row['seed']} seat {row['seat']} ({seconds:.{_SECONDS_DECIMALS}f} s)"
    if played.error is not None:
        return f"{game}: raised {played.error}"
    outcome = "won" if row["our_side_won"] else "lost"
    return (
        f"{game}: {outcome}, the {row['winner']} side won on day {row['days']}; "
        f"{row['model_calls']} model calls, {row['fallbacks']} fallbacks, {row['unhandled_errors']} unhandled errors"
    )


def main(arguments: list[str]) -> int:
    """Play the run the command line asks for and write its files; the exit code."""
    args = _parser().parse_args(arguments)
    root = repository_root()
    folder = bench.label_folder(args.label)
    if (folder / bench.RESULTS_FILE_NAME).exists():
        print_line(f"{folder} already holds {bench.RESULTS_FILE_NAME}: give the run a new label", error=True)
        return EXIT_REFUSED
    last_game_end = time.perf_counter()

    def report(played: bench.PlayedGame) -> None:
        nonlocal last_game_end
        now = time.perf_counter()
        print_line(game_line(played, now - last_game_end))
        last_game_end = now

    try:
        tuning = bench_tuning.run_tuning(args.tuning, root)
        arm = arms.make_arm(args.arm, root, tuning.tuning)
        opponents = arms.opponent_factories(root, tuning.tuning)
        with bench_models.serving(model_roles(arm, args.mix), root) as started:
            games = bench.planned_games(args.games_per_role, args.seed_base)
            last_game_end = time.perf_counter()
            bench.run_bench(
                folder,
                arm,
                args.mix,
                games,
                opponent_factories=opponents,
                on_game=report,
                model_roles_started=started,
                tuning=tuning.described(),
            )
    except (ConfigError, arms.ArmRefused, bench_models.ModelsUnavailable) as exc:
        print_line(str(exc), error=True)
        return EXIT_REFUSED
    print_line(f"Wrote {folder / bench.RESULTS_FILE_NAME}")
    print_line(f"Wrote {folder / bench.SUMMARY_FILE_NAME}")
    return EXIT_PLAYED


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
