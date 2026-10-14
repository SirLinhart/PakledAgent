"""The figures of a Pokémon practice benchmark label, and the comparison of two labels.

A label is read from its `results.csv`: the opponent its matches were played
against; how many matches our seat played, how many it won, the win rate,
the fallbacks, the parse degradations and the unhandled errors; then the
slowest decision of each phase (draft, team preview, battle turn) in
milliseconds. Its match records give the median decision of each phase (the
nearest-rank median of the `compute_ms` of our seat's `action` events, each
of the phase of the `state` event before it), and how often the model
changed the script's mind: of the decisions a model answered (a
`pokemon_decision` event whose `source` is a model, not `script`), how many
chose another candidate than the script's best (`chosen` is not
`script_choice`), and that share. Two labels are compared with the two-sided
Fisher exact test of their win counts. The lines are comma-separated, like
the other benchmarks', so they can be pasted into a spreadsheet as they are.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ucla_agent.core import metrics
from ucla_agent.core.bench import MISSING, SIGNIFICANCE, cell, csv_line, fisher_exact_p
from ucla_agent.core.stats import percentile
from ucla_agent.games.pokemon.decide import DECISION_EVENT, SCRIPT
from ucla_agent.practice.pokemon.bench import PHASES, slowest_decisions, tally, timed_decisions

TALLY_COLUMNS = ("games", "wins", "win_rate", "fallbacks", "parse_degradations", "unhandled_errors")
SLOWEST_COLUMNS = ("phase", "slowest_decision_ms")
MEDIAN_COLUMNS = ("phase", "median_decision_ms")
MEDIAN_PERCENTILE = 50
MODEL_CHOICE_COLUMNS = ("model_answers", "model_differed", "model_differed_share")
COMPARE_COLUMNS = ("games_a", "wins_a", "games_b", "wins_b", SIGNIFICANCE)
# Three decimals: with 50 matches a label, p-values near 0.05 are the ones that matter.
_P_DECIMALS = 3

Rows = list[Mapping[str, Any]]
Records = Sequence[metrics.Match]


@dataclass(frozen=True)
class ModelChoices:
    """The decisions a model answered, and how many of them differed from the script's choice."""

    answers: int
    differed: int

    @property
    def differed_share(self) -> float | None:
        """Differed over answered; None when no model answered."""
        return self.differed / self.answers if self.answers else None


def model_choices(records: Records) -> ModelChoices:
    """The model's answers among the `pokemon_decision` events of the records, and those that differed from the
    script's choice."""
    answered = [
        decision
        for record in records
        for decision in record.data_of(DECISION_EVENT)
        if decision.get("source") not in (None, SCRIPT)
    ]
    differed = sum(1 for decision in answered if decision.get("chosen") != decision.get("script_choice"))
    return ModelChoices(len(answered), differed)


def median_decisions(records: Records) -> dict[str, float | None]:
    """The nearest-rank median decision time of each phase over the records, in ms; None for a phase none timed."""
    timed = [decision for record in records for decision in timed_decisions(record)]
    return {
        phase: percentile((compute_ms for of_phase, compute_ms in timed if of_phase == phase), MEDIAN_PERCENTILE)
        for phase in PHASES
    }


def label_lines(rows: Rows, records: Records = ()) -> list[str]:
    """What `--pokemon-bench` prints: the opponent of the rows, the tally's column names and figures, the slowest
    decision of each phase, then from the records the median decision of each phase, the model's answers and the
    share of them that differed from the script."""
    counted = tally(rows)
    figures = (
        counted.games,
        counted.wins,
        counted.win_rate,
        counted.fallbacks,
        counted.parse_degradations,
        counted.unhandled_errors,
    )
    return [
        csv_line(("opponent", *_distinct(rows, "opponent"))),
        csv_line(TALLY_COLUMNS),
        csv_line(cell(figure) for figure in figures),
        csv_line(SLOWEST_COLUMNS),
        *(csv_line((phase, cell(slowest))) for phase, slowest in slowest_decisions(rows).items()),
        csv_line(MEDIAN_COLUMNS),
        *(csv_line((phase, cell(median))) for phase, median in median_decisions(records).items()),
        *model_choice_lines(model_choices(records)),
    ]


def model_choice_lines(choices: ModelChoices) -> list[str]:
    """The column names and figures of the model's answers: how many, how many differed, the share."""
    return [
        csv_line(MODEL_CHOICE_COLUMNS),
        csv_line((str(choices.answers), str(choices.differed), cell(choices.differed_share))),
    ]


def compare_lines(
    label_a: str, rows_a: Rows, label_b: str, rows_b: Rows, *, records_a: Records = (), records_b: Records = ()
) -> list[str]:
    """What `--pokemon-bench-compare` prints: label A's lines, label B's, then both win counts with the Fisher p."""
    tally_a, tally_b = tally(rows_a), tally(rows_b)
    p_value = fisher_exact_p(tally_a.wins, tally_a.games, tally_b.wins, tally_b.games)
    shown = MISSING if p_value is None else f"{p_value:.{_P_DECIMALS}f}"
    counts = (tally_a.games, tally_a.wins, tally_b.games, tally_b.wins)
    return [
        csv_line(("A", label_a)),
        *label_lines(rows_a, records_a),
        "",
        csv_line(("B", label_b)),
        *label_lines(rows_b, records_b),
        "",
        csv_line(COMPARE_COLUMNS),
        csv_line((*(str(count) for count in counts), shown)),
    ]


def _distinct(rows: Iterable[Mapping[str, Any]], column: str) -> list[str]:
    """The values a column takes over the rows, sorted; a label played as one run has exactly one."""
    return sorted({str(row[column]) for row in rows})
