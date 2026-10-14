"""One Pokémon decision: the script's best candidate, or the model's choice among the script's best few.

Each phase's policy ranks its candidates, best first: draft cards, team
preview lineups or doubles turn actions. With `model_choices` on and two or
more candidates, the `pokemon_choice` chain is asked to choose among the first
`top_k`, each an option with a short id (`C1`... for cards, `L1`... for
lineups, `T1`... for turns, short enough for the model's answer) described by
the script's own figures. The model never writes an action: an answer that
was used and names one of the options plays that candidate, anything else
(no answer by the phase's deadline, an error, an id that is no option) plays
the script's first.

With `model_decides` on, the model is offered the script's best
`decide_top_k` instead, described without the script's scores (the agent
writes those descriptions) and listed in an order shuffled for each decision,
so the model cannot simply take the first or learn where the script's best
sits. The shuffle is seeded with the CRC-32 of the decision's state tag, so
one situation always shows one order and the order can be worked out again
from the record. The ids follow the shown order. The question says the
options are unranked and in random order, and the model writes a reason of
at most `decide_reason_words` words before its choice, in an answer of at most
`DECIDE_ANSWER_TOKENS` tokens. The deadlines are the same either way.

A draft pick has its own 15 s clock and its configured deadline. Team
Preview and battle decisions run on Showdown's VGC timer, whose time left
the observation's `clock` reports (`deadline_ms`): the model never waits
past the decision's clock less a margin, a battle decision spends at most a
share of the bank and never takes it below the reserve, and before Showdown
has reported the clock the wait is short. A deadline of 0 asks no model:
the script's best, always computed first, is sent at once.

With `[maximin] enabled`, the script's ranking itself is the maximin one,
which takes more computing; it must be done by `compute_until`: the
decision's clock less `decision_margin_seconds` when the observation reports
one, else the phase's configured deadline, from the moment the decision
began. A ranking that is not done by then gives way to the script's own.

Each decision writes one `pokemon_decision` event: the phase, the candidates
offered with their scores and actions, the script's choice, the one chosen,
which source decided and what came of the request, the latency from the
moment the decision began, the parse degradations of its observation, the
clock it was read with (null without one) and the deadline the model had.
The candidates are listed in the order they were shown, and with
`model_decides` on the event also holds `shown_order`, the script's rank of
each candidate shown (1 for its best), and `script_rank_of_choice`, the rank
of the one played. With maximin enabled it also holds `ranking`: `maximin`
when the candidates were ranked by maximin, `script` when the script's own
ranking stood (out of time, or no lookahead in the draft).
"""

from __future__ import annotations

import dataclasses
import math
import random
import time
import zlib
from dataclasses import dataclass
from typing import Any, Sequence

from ucla_agent.core import records
from ucla_agent.core.decide import USED, ChoiceRequest, DecisionSource, Option
from ucla_agent.games.pokemon.observe import Clock, Reader
from ucla_agent.games.pokemon.settings import PokemonSettings

DECISION_EVENT = "pokemon_decision"
DRAFT, PREVIEW, TURN = "draft", "team_preview", "moving"
OPTION_PREFIXES = {DRAFT: "C", PREVIEW: "L", TURN: "T"}
QUESTIONS = {
    DRAFT: "Which card do we draft? Each option is a card offered to us, with the script's score of it.",
    PREVIEW: "Which lineup do we bring? Each option names the four we bring and the two of them that lead.",
    TURN: "What do our two Pokémon do this turn? Each option gives both slots' actions, with expected damage "
    "in percent of the target's HP and the chance to knock it out.",
}
UNRANKED = "The options are unranked and listed in random order. Choose the best one for this situation."
DECIDE_QUESTIONS = {
    DRAFT: f"Which card do we draft? Each option is a card offered to us, as its set. {UNRANKED}",
    PREVIEW: f"Which lineup do we bring? Each option names the four we bring and the two of them that lead. {UNRANKED}",
    TURN: "What do our two Pokémon do this turn? Each option gives both slots' actions, with expected damage "
    f"in percent of the target's HP and the chance to knock it out. {UNRANKED}",
}
# A deciding model's answer: a reason of at most `decide_reason_words` words, then its choice.
DECIDE_ANSWER_TOKENS = 160
# The decider and outcome a decision records when no source was asked or its answer was not played.
SCRIPT = "script"
NOT_ASKED = "not_asked"
NOT_AN_OPTION = "not_an_option"
SCORE_DECIMALS = 1
# How the candidates were ranked, as the event records it while maximin is enabled.
RANKED_BY_MAXIMIN, RANKED_BY_SCRIPT = "maximin", "script"
_MILLISECONDS_PER_SECOND = 1000


@dataclass(frozen=True)
class Candidate:
    """One thing the decision may play: the structured action, the script's score and what a model reads of it."""

    action: Any
    score: float
    description: str


@dataclass(frozen=True)
class Asked:
    """What one decision asks: its phase, its candidates best first, the situation as a model reads it, the tag of
    the state it was asked in, the clock its observation reported (None for a draft pick or before Showdown has
    reported it), and how its candidates were ranked (None while maximin is not enabled)."""

    phase: str
    candidates: Sequence[Candidate]
    context: str
    state_tag: str
    clock: Clock | None = None
    ranking: str | None = None


def _configured_ms(settings: PokemonSettings, phase: str) -> int:
    """The phase's configured deadline in milliseconds."""
    return {
        DRAFT: settings.draft_deadline_ms,
        PREVIEW: settings.preview_deadline_ms,
        TURN: settings.turn_deadline_ms,
    }[phase]


def compute_until(settings: PokemonSettings, phase: str, clock: Clock | None, started: float) -> float:
    """The `time.perf_counter()` moment by which the script must have ranked a decision's candidates that began at
    `started`: the decision's clock less `decision_margin_seconds` when there is one, else the phase's configured
    deadline. A draft pick has no clock of its own here."""
    if clock is not None and phase != DRAFT:
        return started + clock.decision_seconds_left - settings.decision_margin_seconds
    return started + _configured_ms(settings, phase) / _MILLISECONDS_PER_SECOND


def deadline_ms(settings: PokemonSettings, phase: str, clock: Clock | None) -> int:
    """How long the phase's model choice may take, in milliseconds; 0 when no model is to be asked.

    A draft pick keeps its configured deadline. A lineup or battle decision without a clock waits at most
    `no_clock_deadline_ms`; with one, never past the decision's clock less `decision_margin_seconds`. A battle
    decision also spends at most `bank_share` of the bank, and never takes it below `bank_reserve_seconds`, so a
    bank at or under the reserve asks no model. Team Preview does not use the bank.
    """
    configured = _configured_ms(settings, phase)
    if phase == DRAFT:
        return configured
    if clock is None:
        return min(configured, settings.no_clock_deadline_ms)
    limits_seconds = [clock.decision_seconds_left - settings.decision_margin_seconds]
    if phase == TURN:
        # A share of the bank alone only shrinks it geometrically, which passes the reserve within one battle; the
        # reserve is a limit of its own, so model time never spends the bank below it.
        limits_seconds += [
            settings.bank_share * clock.bank_seconds_left,
            clock.bank_seconds_left - settings.bank_reserve_seconds,
        ]
    limit_ms = min(configured, *(_MILLISECONDS_PER_SECOND * seconds for seconds in limits_seconds))
    return max(0, math.floor(limit_ms))


def option_ids(phase: str, count: int) -> list[str]:
    """The ids of the first `count` options of a phase: its letter and the option's place, from 1."""
    return [f"{OPTION_PREFIXES[phase]}{place}" for place in range(1, count + 1)]


def shown_ranks(asked: Asked, settings: PokemonSettings) -> list[int]:
    """The script's ranks (1 for its best) of the candidates a decision offers, in the order they are shown: the
    best `top_k` best first, or with `model_decides` the best `decide_top_k` in the order the state tag's shuffle
    gives them."""
    if not settings.model_decides:
        return list(range(1, min(settings.top_k, len(asked.candidates)) + 1))
    ranks = list(range(1, min(settings.decide_top_k, len(asked.candidates)) + 1))
    random.Random(zlib.crc32(asked.state_tag.encode("utf-8"))).shuffle(ranks)
    return ranks


def choose(
    asked: Asked, settings: PokemonSettings, source: DecisionSource, started: float, reader: Reader
) -> Candidate:
    """The candidate to play (see the module note); writes the decision's `pokemon_decision` event.

    `asked.candidates` holds at least one candidate; `started` is the `time.perf_counter()` of the moment the
    decision began.
    """
    ranks = shown_ranks(asked, settings)
    shown = [asked.candidates[rank - 1] for rank in ranks]
    ids = option_ids(asked.phase, len(shown))
    script_place = ranks.index(1)
    deadline = deadline_ms(settings, asked.phase, asked.clock)
    place, decider, outcome = script_place, SCRIPT, NOT_ASKED
    if settings.model_choices and len(shown) > 1 and deadline > 0:
        place, decider, outcome = _model_pick(source, _request(asked, shown, ids, deadline, settings), script_place)
    event = {
        "phase": asked.phase,
        "candidates": [
            {"id": option_id, "score": round(candidate.score, SCORE_DECIMALS), "action": candidate.action}
            for option_id, candidate in zip(ids, shown)
        ],
        "script_choice": ids[script_place],
        "chosen": ids[place],
        "source": decider,
        "outcome": outcome,
        "latency_ms": round((time.perf_counter() - started) * _MILLISECONDS_PER_SECOND),
        "parse_degradations": reader.degradations,
        "degraded_keys": reader.degraded_keys,
        **_clock_fields(asked.clock),
        "deadline_ms": deadline,
    }
    if settings.model_decides:
        event.update(shown_order=ranks, script_rank_of_choice=ranks[place])
    if asked.ranking is not None:
        event["ranking"] = asked.ranking
    records.log(DECISION_EVENT, event)
    return shown[place]


def _clock_fields(clock: Clock | None) -> dict[str, float | None]:
    """The clock a decision was read with, as its event records it: null seconds without one."""
    return {
        "clock_decision_seconds_left": None if clock is None else clock.decision_seconds_left,
        "clock_bank_seconds_left": None if clock is None else clock.bank_seconds_left,
    }


def _request(
    asked: Asked, shown: Sequence[Candidate], ids: Sequence[str], deadline: int, settings: PokemonSettings
) -> ChoiceRequest:
    """The question put to the source; with `model_decides`, the unranked question with the reason asked first."""
    request = ChoiceRequest(
        question=QUESTIONS[asked.phase],
        options=[Option(option_id, candidate.description) for option_id, candidate in zip(ids, shown)],
        context=asked.context,
        deadline_ms=deadline,
        state_tag=asked.state_tag,
    )
    if not settings.model_decides:
        return request
    return dataclasses.replace(
        request,
        question=DECIDE_QUESTIONS[asked.phase],
        reason_first=True,
        reason_words=settings.decide_reason_words,
        max_answer_tokens=DECIDE_ANSWER_TOKENS,
    )


def _model_pick(source: DecisionSource, request: ChoiceRequest, script_place: int) -> tuple[int, str, str]:
    """The place of the candidate the source chose (the script's, at `script_place`, unless its answer was used and
    is an option), the source that decided, and the request's outcome."""
    result = source.choose(request)
    if result.outcome != USED:
        return script_place, SCRIPT, result.outcome
    if result.option_id not in request.option_ids():
        return script_place, SCRIPT, NOT_AN_OPTION
    return request.option_ids().index(result.option_id), result.source, USED
