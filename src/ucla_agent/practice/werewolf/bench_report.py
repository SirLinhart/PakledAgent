"""The table of a Werewolf practice benchmark label, and the comparison of two labels.

A label is read from its `results.csv` alone: how many games our seat played
in each role and in all, how many its side won, the win rate and the
unhandled errors; then, from the engine-truth columns, in how many of those
games the real seer claimed, in how many of these the vote of its claim day
eliminated it as the lone claimant, and how many games the village won. Two
labels are compared role by role, with the two-sided Fisher exact test of
their win counts, and the goal line says whether label B beat label A: a
higher win rate than A's in every role, with no unhandled error. The lines
are comma-separated, like the Red Alert benchmark's, so they can be pasted
into a spreadsheet as they are.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ucla_agent.core.bench import GOAL_MET, GOAL_NOT_MET, MISSING, SIGNIFICANCE, cell, csv_line, fisher_exact_p
from ucla_agent.practice.werewolf.bench import ALL_ROLES, BENCH_ROLES, Tally, tally
from ucla_agent.practice.werewolf.bench_truth import SEER_CLAIM_DAY, SEER_VOTED_OUT_ON_CLAIM_DAY, VILLAGE_WON

TALLY_COLUMNS = ("role", "games", "wins", "win_rate", "unhandled_errors")
SEER_CLAIM_COLUMNS = ("role", "seer_claimed", "seer_voted_out_on_claim_day", "village_wins")
COMPARE_COLUMNS = ("role", "games_a", "wins_a", "games_b", "wins_b", SIGNIFICANCE)
# Three decimals: a role's sample is a third of the label's, and its p-values often sit near 0.05.
_P_DECIMALS = 3
# How a row with no claim day holds it: None as played, an empty cell as read back from results.csv.
_NO_CLAIM_DAY = (None, "")

Rows = list[Mapping[str, Any]]


def label_lines(rows: Rows) -> list[str]:
    """What `--werewolf-bench` prints: the arm and the mix of the rows, each role's tally and the tally of all, then
    each role's seer-claim figures and those of all."""
    return [
        csv_line(("arm", *_distinct(rows, "arm"))),
        csv_line(("mix", *_distinct(rows, "mix"))),
        *tally_lines(rows),
        *seer_claim_lines(rows),
    ]


def tally_lines(rows: Rows) -> list[str]:
    """The column names, then one line per role, villager, seer and wolf, and one for all games."""
    return [csv_line(TALLY_COLUMNS), *(_tally_line(group, counted) for group, counted in tally(rows).items())]


def seer_claim_lines(rows: Rows) -> list[str]:
    """The column names, then for each role and for all games: the games in which the real seer claimed, how many of
    those had it voted out on its claim day as the lone claimant, and the games the village won.

    A label written before the engine-truth columns has none of these figures, and shows `-` for each.
    """
    groups = {role: [row for row in rows if row["role"] == role] for role in BENCH_ROLES}
    groups[ALL_ROLES] = list(rows)
    return [csv_line(SEER_CLAIM_COLUMNS), *(_seer_claim_line(group, grouped) for group, grouped in groups.items())]


def _seer_claim_line(group: str, rows: Rows) -> str:
    if any(VILLAGE_WON not in row for row in rows):
        return csv_line((group, MISSING, MISSING, MISSING))
    claimed = [row for row in rows if row[SEER_CLAIM_DAY] not in _NO_CLAIM_DAY]
    voted_out = sum(int(row[SEER_VOTED_OUT_ON_CLAIM_DAY]) for row in claimed)
    village_wins = sum(int(row[VILLAGE_WON]) for row in rows)
    return csv_line((group, str(len(claimed)), str(voted_out), str(village_wins)))


def _tally_line(group: str, counted: Tally) -> str:
    counts = (str(counted.games), str(counted.wins), cell(counted.win_rate), str(counted.unhandled_errors))
    return csv_line((group, *counts))


def compare_lines(label_a: str, rows_a: Rows, label_b: str, rows_b: Rows) -> list[str]:
    """What `--werewolf-bench-compare` prints: label A's lines, label B's, each role's win counts with the Fisher p,
    and the goal line last."""
    tallies_a, tallies_b = tally(rows_a), tally(rows_b)
    return [
        csv_line(("A", label_a)),
        *label_lines(rows_a),
        "",
        csv_line(("B", label_b)),
        *label_lines(rows_b),
        "",
        csv_line(COMPARE_COLUMNS),
        *(_compare_line(role, tallies_a[role], tallies_b[role]) for role in BENCH_ROLES),
        "",
        GOAL_MET if goal_met(tallies_a, tallies_b) else GOAL_NOT_MET,
    ]


def goal_met(tallies_a: Mapping[str, Tally], tallies_b: Mapping[str, Tally]) -> bool:
    """Whether B's win rate is higher than A's in every role and B had no unhandled error.

    A role in which either label played no game has no win rate to compare,
    so it does not meet the goal.
    """
    if tallies_b[ALL_ROLES].unhandled_errors:
        return False
    return all(_higher_rate(tallies_a[role], tallies_b[role]) for role in BENCH_ROLES)


def _higher_rate(counted_a: Tally, counted_b: Tally) -> bool:
    # Cross-multiplied, so two rates over different game counts are compared exactly.
    return (
        bool(counted_a.games and counted_b.games)
        and counted_b.wins * counted_a.games > counted_a.wins * counted_b.games
    )


def _compare_line(role: str, counted_a: Tally, counted_b: Tally) -> str:
    p_value = fisher_exact_p(counted_a.wins, counted_a.games, counted_b.wins, counted_b.games)
    shown = MISSING if p_value is None else f"{p_value:.{_P_DECIMALS}f}"
    games_and_wins = (counted_a.games, counted_a.wins, counted_b.games, counted_b.wins)
    return csv_line((role, *(str(count) for count in games_and_wins), shown))


def _distinct(rows: Iterable[Mapping[str, Any]], column: str) -> list[str]:
    """The values a column takes over the rows, sorted; a label played as one run has exactly one."""
    return sorted({str(row[column]) for row in rows})
