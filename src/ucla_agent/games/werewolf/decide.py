"""One Werewolf action: a vote, a kill or an investigation, chosen among the script's candidates and always legal.

The role's plan (`village`, `wolves`) ranks the candidate seats for the
sub-phase; its first is the script's choice. With `model_choices` on and more
than one candidate, the `werewolf_choice` chain is asked to choose among the
first six, each an option `P<seat>` described by our beliefs about the seat
and what it said, and its answer is played when it names one of them. The
model never names an action of its own, so it can only pick a seat the plan
allows: never abstain by day on the village side, never our ally at night.

Whatever was chosen, the action played is one of `state.legal_actions`: a
chosen seat that is no longer legal gives way to the next legal candidate,
and with no legal candidate at all to the first legal action that is not
abstaining by day, or the first legal action by night. Each action writes one
`werewolf_decision` event to the match record.
"""

from __future__ import annotations

import time
from typing import Sequence

from altruagent import GameState, LegalAction

from ucla_agent.core import records
from ucla_agent.core.decide import USED, ChoiceRequest, DecisionSource, Option
from ucla_agent.games.werewolf import prompts, village, wolves
from ucla_agent.games.werewolf.inference import Beliefs
from ucla_agent.games.werewolf.ledger import CLAIM_TEXT, WOLF, Ledger, label
from ucla_agent.games.werewolf.settings import WerewolfSettings
from ucla_agent.games.werewolf.talk import accusers_today
from ucla_agent.games.werewolf.tuning import Tuning

NIGHT_WOLF, NIGHT_SEER, DAY_VOTE = "night_wolf", "night_seer", "day_vote"
ABSTAIN_ACTION = "7"
MAX_OPTIONS = 6
OPTION_PREFIX = "P"
DECISION_EVENT = "werewolf_decision"
# The decider and outcome a decision records when no source was asked.
SCRIPT = "script"
NOT_ASKED = "not_asked"
NOT_AN_OPTION = "not_an_option"
_MILLISECONDS_PER_SECOND = 1000


def candidates(ledger: Ledger, beliefs: Beliefs, tuning: Tuning) -> list[int]:
    """The ranked candidate seats of our role's plan for the ledger's sub-phase, the script's choice first."""
    sub_phase = ledger.facts().sub_phase
    if sub_phase == NIGHT_WOLF:
        return wolves.night_wolf(ledger, beliefs, tuning.wolves)
    if sub_phase == NIGHT_SEER:
        return village.night_seer(ledger, beliefs)
    if ledger.private().role == WOLF:
        return wolves.day_vote(ledger, beliefs, tuning.wolves)
    return village.day_vote(ledger, beliefs, tuning.village, tuning.wolves.seer_retraction_pattern)


def decide_action(
    state: GameState, ledger: Ledger, beliefs: Beliefs, settings: WerewolfSettings, source: DecisionSource
) -> LegalAction:
    """The action to play for `state`, whose ledger and beliefs are up to date; writes its `werewolf_decision`."""
    started = time.perf_counter()
    ranked = candidates(ledger, beliefs, settings.tuning)
    picked, asked_source, outcome = None, SCRIPT, NOT_ASKED
    if settings.model_choices and len(ranked) > 1:
        picked, asked_source, outcome = _model_pick(source, choice_request(state, ledger, beliefs, ranked, settings))
    action = legal_action(state, ranked if picked is None else [picked, *ranked])
    played = int(action.action_id)
    records.log(
        DECISION_EVENT,
        {
            "sub_phase": ledger.facts().sub_phase,
            "candidates": ranked,
            "script_choice": ranked[0] if ranked else None,
            "chosen": played,
            "source": asked_source if played == picked else SCRIPT,
            "outcome": outcome,
            "latency_ms": round((time.perf_counter() - started) * _MILLISECONDS_PER_SECOND),
        },
    )
    return action


def _model_pick(source: DecisionSource, request: ChoiceRequest) -> tuple[int | None, str, str]:
    """The seat the source chose, None unless its answer was used and is one of the options; its name; the outcome."""
    result = source.choose(request)
    if result.outcome != USED:
        return None, result.source, result.outcome
    if result.option_id not in request.option_ids():
        return None, result.source, NOT_AN_OPTION
    return seat_of(result.option_id), result.source, USED


def choice_request(
    state: GameState, ledger: Ledger, beliefs: Beliefs, ranked: Sequence[int], settings: WerewolfSettings
) -> ChoiceRequest:
    """The question to the chain: choose among the first `MAX_OPTIONS` candidates, the first being the script's."""
    facts, private = ledger.facts(), ledger.private()
    accused = accusers_today(ledger)
    options = [
        Option(
            option_id(seat),
            prompts.option_description(
                seat,
                prompts.OptionFacts(
                    p_wolf=beliefs.p_wolf[seat],
                    p_seer=beliefs.p_seer[seat],
                    accusers_today=len(accused.get(seat, ())),
                    claims=_claims_of(ledger, seat),
                    script_pick=position == 0,
                ),
            ),
        )
        for position, seat in enumerate(ranked[:MAX_OPTIONS])
    ]
    return ChoiceRequest(
        question=prompts.choice_question(private.role, facts.sub_phase),
        options=options,
        context=prompts.context(ledger, settings.tuning.chat.quote_chars),
        deadline_ms=settings.choice_deadline_ms,
        state_tag=f"d{facts.day}-{facts.sub_phase}-v{state.state_version}",
    )


def legal_action(state: GameState, preferred: Sequence[int]) -> LegalAction:
    """The first of `preferred` that is a legal action; else the first legal action that is not abstaining by day,
    or the first legal action by night."""
    legal = {action.action_id: action for action in state.legal_actions}
    chosen = next((legal[str(seat)] for seat in preferred if str(seat) in legal), None)
    if chosen is not None:
        return chosen
    not_abstaining = [action for action in state.legal_actions if action.action_id != ABSTAIN_ACTION]
    return (not_abstaining or state.legal_actions)[0]


def option_id(seat: int) -> str:
    """The id of a seat's option: `P` and the seat number, short enough for any model to answer with."""
    return f"{OPTION_PREFIX}{seat}"


def seat_of(chosen_id: str) -> int:
    """The seat an option id names."""
    return int(chosen_id.removeprefix(OPTION_PREFIX))


def _claims_of(ledger: Ledger, seat: int) -> tuple[str, ...]:
    """What a seat said in chat, as the ledger's claim wording, each distinct claim once."""
    said = (
        CLAIM_TEXT[claim.kind].format(target=None if claim.target is None else label(claim.target), result=claim.result)
        for claim in ledger.claims()
        if claim.seat == seat
    )
    return tuple(dict.fromkeys(said))
