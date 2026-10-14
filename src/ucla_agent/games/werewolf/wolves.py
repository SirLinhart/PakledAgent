"""The script's play for a wolf seat: tonight's kill, the word with our ally, the cover vote, the counterclaim.

Like the village's plan, each choice is a list of candidate seats ranked best
first, the first being the script's own choice. Our ally is never a kill
candidate and is a vote candidate only when the table is about to vote it out
anyway. Both wolves rank the kill in the same way from what both can see, so
two copies of this agent agree on the victim without having to talk. Every
function is pure: it reads the ledger, the beliefs and the `[wolves]` tuning.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from ucla_agent.games.werewolf.inference import Beliefs
from ucla_agent.games.werewolf.ledger import ACCUSATION, RESULT_CLAIM, WOLF, ChatMessage, Ledger, label
from ucla_agent.games.werewolf.talk import accusers_today, active_seer_claimants, seer_claimants
from ucla_agent.games.werewolf.tuning import WolvesTuning
from ucla_agent.games.werewolf.village import RESULT_POINT, SEER_CLAIM_POINT

# Probabilities are compared at this many decimals, so float noise never breaks a tie.
PROBABILITY_DECIMALS = 9


@dataclass(frozen=True)
class PrivateMessage:
    """A chat for one seat only: the text and the seat it goes to."""

    text: str
    recipient: int


def night_wolf(ledger: Ledger, beliefs: Beliefs, tuning: WolvesTuning) -> list[int]:
    """Tonight's kill targets, best first: living seats that are neither us nor our ally.

    A target named by today's kill request between us and our living ally is
    the only candidate (see `agreed_kill`). Otherwise the targets go by: a
    seer claim not taken back, then `p_seer`, then the accusations each made
    against either wolf, then lowest seat.
    """
    targets = kill_targets(ledger)
    agreed = agreed_kill(ledger, tuning, targets)
    if agreed is not None:
        return [agreed]
    return ranked_kills(ledger, beliefs, tuning, targets)


def kill_targets(ledger: Ledger) -> list[int]:
    """The seats the wolves may kill: the living seats other than ours and our allies'."""
    facts, allies = ledger.facts(), ledger.private().allies
    return [seat for seat in facts.living if seat != facts.seat and seat not in allies]


def agreed_kill(ledger: Ledger, tuning: WolvesTuning, targets: list[int]) -> int | None:
    """The target of the first of today's private kill requests between us and our living ally that names one.

    Our ally's request is followed. When both of us sent one, the earlier
    stands: each wolf sends its request before it sees the other's, and the
    earlier one is the same for both, so two copies of this agent still agree.
    """
    pair = {ledger.facts().seat, *_living_allies(ledger)}
    for message in sorted(ledger.messages(today=True), key=lambda message: message.seq):
        named = _kill_request(message, pair, tuning)
        if named in targets:
            return named
    return None


def ranked_kills(ledger: Ledger, beliefs: Beliefs, tuning: WolvesTuning, targets: list[int]) -> list[int]:
    """The targets by seer claim not taken back, then `p_seer`, then accusations against either wolf, then seat."""
    claimants = set(active_seer_claimants(ledger, tuning.seer_retraction_pattern))
    wolves = {ledger.facts().seat, *ledger.private().allies}
    accusations = Counter(
        claim.seat for claim in ledger.claims() if claim.kind == ACCUSATION and claim.target in wolves
    )
    return sorted(
        targets,
        key=lambda seat: (
            seat not in claimants,
            -round(beliefs.p_seer[seat], PROBABILITY_DECIMALS),
            -accusations[seat],
            seat,
        ),
    )


def coordination(ledger: Ledger, beliefs: Beliefs, tuning: WolvesTuning, template: str) -> PrivateMessage | None:
    """Today's private word to our living ally naming our top kill target, filled into `template`.

    None when we have no living ally, when our ally already sent us a kill
    request today (we follow it and say nothing), or when there is no target.
    """
    allies = _living_allies(ledger)
    if not allies:
        return None
    ally, pair = allies[0], {ledger.facts().seat, *allies}
    ally_asked = any(
        message.sender == ally and _kill_request(message, pair, tuning) is not None
        for message in ledger.messages(today=True)
    )
    if ally_asked:
        return None
    ranked = ranked_kills(ledger, beliefs, tuning, kill_targets(ledger))
    return PrivateMessage(template.format(player=label(ranked[0])), ally) if ranked else None


def day_vote(ledger: Ledger, beliefs: Beliefs, tuning: WolvesTuning) -> list[int]:
    """The seats to vote for, best first: never abstain, and never our ally unless the table is voting it out.

    The living seats that are not wolves go by their distinct accusers today
    (the table's leading candidate first), then a seer claim not taken back,
    then lowest seat. When our ally leads today's accusations and at least
    `bus_accuser_share` of the living non-wolf seats (rounded up) accuse it,
    the vote cannot save it, and the ally is the only candidate.
    """
    facts = ledger.facts()
    wolves = {facts.seat, *ledger.private().allies}
    candidates = [seat for seat in facts.living if seat not in wolves]
    accusers = accusers_today(ledger)
    claimants = set(active_seer_claimants(ledger, tuning.seer_retraction_pattern))
    for ally in _living_allies(ledger):
        if _is_bussed(ally, facts.living, candidates, accusers, tuning.bus_accuser_share):
            return [ally]
    return sorted(candidates, key=lambda seat: (-len(accusers.get(seat, ())), seat not in claimants, seat))


def _is_bussed(
    ally: int, living: tuple[int, ...], non_wolves: list[int], accusers: dict[int, frozenset[int]], share: float
) -> bool:
    """Whether our ally leads today's accusations, accused by at least `share` of the living non-wolves."""
    against_ally = len(accusers.get(ally, frozenset()) & set(non_wolves))
    leads = len(accusers.get(ally, ())) > max(
        (len(accusers.get(seat, ())) for seat in living if seat != ally), default=0
    )
    return leads and against_ally >= math.ceil(share * len(non_wolves))


def counterclaim_against(ledger: Ledger, *, earlier: int | None = None) -> int | None:
    """The seat we claim the seer against: a seer claimant that called our living ally a wolf; None for no claim.

    Only while our ally has not claimed the seer itself. A counterclaim made
    on an earlier day (`earlier`) is kept and repeated, never switched to
    another seat.
    """
    if earlier is not None:
        return earlier
    facts, private = ledger.facts(), ledger.private()
    if private.role != WOLF or facts.eliminated:
        return None
    allies = _living_allies(ledger)
    claimants = seer_claimants(ledger)
    if any(ally in claimants for ally in allies):
        return None
    return next((seat for seat in _accusing_seers(ledger, allies) if seat in facts.living), None)


def _accusing_seers(ledger: Ledger, allies: list[int]) -> list[int]:
    """The seats that, as claimed seers, said one of `allies` is a wolf, in the order they said it."""
    return [
        claim.seat
        for claim in ledger.claims()
        if claim.kind == RESULT_CLAIM and claim.result == WOLF and claim.target in allies
    ]


def counterclaim_points(accuser: int) -> tuple[str, ...]:
    """What a counterclaim must say: the claim `I am the seer` and `PlayerX is a wolf` for the accuser."""
    return SEER_CLAIM_POINT, RESULT_POINT.format(player=label(accuser), result=WOLF)


def _living_allies(ledger: Ledger) -> list[int]:
    living = set(ledger.facts().living)
    return [ally for ally in ledger.private().allies if ally in living]


def _kill_request(message: ChatMessage, pair: set[int], tuning: WolvesTuning) -> int | None:
    """The seat a private message between the two wolves of `pair` asks to kill; None when it is no such request."""
    private_within_pair = message.sender in pair and len(message.recipients) == 1 and message.recipients[0] in pair
    found = tuning.kill_request_pattern.search(message.text) if private_within_pair else None
    return None if found is None else int(found.group(1))
