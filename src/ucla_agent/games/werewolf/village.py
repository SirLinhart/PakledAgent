"""The script's play for a villager or seer seat: whom to vote, whom to investigate, when the seer reveals itself.

Each choice is a list of candidate seats ranked best first. The first is the
script's own choice; a model may only pick another one of the list, so what
the list leaves out (abstaining, a seat our own seer results cleared, for a
villager a lone seer claimant on the day of its claim) is never played. Every function is
pure: it reads the ledger, the beliefs and the `[village]` tuning, and, to
tell a seer claim taken back, the retraction pattern of `[wolves]`.
"""

from __future__ import annotations

import re
from typing import Collection, Mapping, Sequence

from ucla_agent.games.werewolf.inference import Beliefs
from ucla_agent.games.werewolf.ledger import RESULT_CLAIM, SEER, SEER_CLAIM, VILLAGER, WOLF, Claim, Ledger, label
from ucla_agent.games.werewolf.talk import accusers_today, active_seer_claimants, messages_sent_today, seer_claimants
from ucla_agent.games.werewolf.tuning import VillageTuning

# The claim and the wording of each result a seer's reveal carries.
SEER_CLAIM_POINT = "I am the seer"
RESULT_POINT = "{player} is a {result}"
# Probabilities are sums of floating-point shares: two that differ by less than this are equal.
TOLERANCE = 1e-9
# Uncertainties are compared at this many decimals, so float noise never breaks a tie.
UNCERTAINTY_DECIMALS = 9


def day_vote(ledger: Ledger, beliefs: Beliefs, tuning: VillageTuning, retraction: re.Pattern[str]) -> list[int]:
    """The seats to vote for, best first: never abstain, never a seat our seer results cleared.

    A living seat our results found to be a wolf is the only candidate (each
    one, in the order found). Otherwise the living others go by `p_wolf`, and
    the seats within `close_margin` of the top one are put in order by their
    distinct accusers today, then `p_wolf`, then lowest seat. For a villager
    with `spare_lone_claimant` on, the `lone_claimant` (`retraction` tells a
    seer claim taken back) is left out, unless no other candidate is left, and
    the village rallies on its claim: each seat it named a wolf goes first
    when its `p_wolf` is within `close_margin` of the top candidate's. Our
    seer's vote is never changed by this: our seer is the real one, so any
    other seat claiming the seer is lying.
    """
    private = ledger.private()
    facts, results = ledger.facts(), private.results
    others = [seat for seat in facts.living if seat != facts.seat]
    found_wolves = [seat for seat, result in results.items() if result == WOLF and seat in others]
    if found_wolves:
        return found_wolves
    candidates = [seat for seat in others if results.get(seat) != VILLAGER]
    accusers = accusers_today(ledger)
    spares = tuning.spare_lone_claimant and private.role == VILLAGER
    claimant = lone_claimant(ledger, retraction) if spares else None
    spared = [seat for seat in candidates if seat != claimant]
    if claimant is None or not spared:
        return by_suspicion(candidates, beliefs.p_wolf, accusers, tuning.close_margin)
    ranked = by_suspicion(spared, beliefs.p_wolf, accusers, tuning.close_margin)
    return rallied(ranked, claimed_wolves(ledger, claimant), beliefs.p_wolf, tuning.close_margin)


def lone_claimant(ledger: Ledger, retraction: re.Pattern[str]) -> int | None:
    """The seer claimant the village believes today; None when there is none.

    It is a seat that claimed the seer in today's discussion, is the only
    living seat whose seer claim stands (see `talk.active_seer_claimants`:
    ours counts once we claimed), and has claimed no seer result a death has
    shown false. Such a claimant is most often the real seer, whom a wolf
    would like the village to vote out.
    """
    living = ledger.facts().living
    standing = [seat for seat in active_seer_claimants(ledger, retraction) if seat in living]
    if len(standing) != 1:
        return None
    claimant = standing[0]
    claimed_today = any(claim.kind == SEER_CLAIM and claim.seat == claimant for claim in ledger.claims(today=True))
    shown_false = any(_shown_false(ledger, claim) for claim in ledger.claims() if claim.seat == claimant)
    return claimant if claimed_today and not shown_false else None


def _shown_false(ledger: Ledger, claim: Claim) -> bool:
    """Whether `claim` is a seer result whose seat has died and was revealed as the side it said it was not."""
    if claim.kind != RESULT_CLAIM:
        return False
    revealed = ledger.facts().revealed_role(claim.target)
    return revealed is not None and (revealed == WOLF) != (claim.result == WOLF)


def claimed_wolves(ledger: Ledger, claimant: int) -> list[int]:
    """The seats `claimant` named a wolf as its seer results, in the order first named."""
    return list(
        dict.fromkeys(
            claim.target
            for claim in ledger.claims()
            if claim.kind == RESULT_CLAIM and claim.seat == claimant and claim.result == WOLF
        )
    )


def rallied(ranked: list[int], named: Sequence[int], p_wolf: Mapping[int, float], close_margin: float) -> list[int]:
    """`ranked` with each seat of `named` in it, in that order, moved to the front when its `p_wolf` is within
    `close_margin` of the top one of `ranked`."""
    top = max(p_wolf[seat] for seat in ranked)
    front = [seat for seat in named if seat in ranked and top - p_wolf[seat] <= close_margin + TOLERANCE]
    return front + [seat for seat in ranked if seat not in front]


def by_suspicion(
    candidates: Collection[int],
    p_wolf: Mapping[int, float],
    accusers: Mapping[int, Collection[int]],
    close_margin: float,
) -> list[int]:
    """The candidates by `p_wolf`, those within `close_margin` of the top re-ordered by their number of accusers."""
    ranked = sorted(candidates, key=lambda seat: (-p_wolf[seat], seat))
    if not ranked:
        return []
    top = p_wolf[ranked[0]]
    close_count = sum(1 for seat in ranked if top - p_wolf[seat] <= close_margin + TOLERANCE)
    # A stable sort: seats with as many accusers keep their p_wolf, then seat, order.
    close = sorted(ranked[:close_count], key=lambda seat: -len(accusers.get(seat, ())))
    return close + ranked[close_count:]


def night_seer(ledger: Ledger, beliefs: Beliefs) -> list[int]:
    """The seats to investigate, best first: living others not looked at yet, the most uncertain first.

    Uncertainty is `p_wolf * (1 - p_wolf)`; ties go to a seat that claimed
    the seer, then to the seat that sent the most messages today, then to the
    lowest seat.
    """
    facts, results = ledger.facts(), ledger.private().results
    claimants = set(seer_claimants(ledger))
    spoke = messages_sent_today(ledger)
    candidates = [seat for seat in facts.living if seat != facts.seat and seat not in results]

    def rank(seat: int) -> tuple[float, bool, int, int]:
        p_wolf = beliefs.p_wolf[seat]
        uncertainty = round(p_wolf * (1 - p_wolf), UNCERTAINTY_DECIMALS)
        return -uncertainty, seat not in claimants, -spoke[seat], seat

    return sorted(candidates, key=rank)


def should_reveal(ledger: Ledger, tuning: VillageTuning, *, revealed: bool = False) -> bool:
    """Whether our seat, the living seer, says so today with every result.

    True when a living seat is a wolf by our results, when at least
    `reveal_when_accused_by` distinct seats accused us today, or when we
    revealed in an earlier window (`revealed`): once out, the claim is kept.
    """
    facts, private = ledger.facts(), ledger.private()
    if private.role != SEER or facts.eliminated:
        return False
    living = set(facts.living)
    if revealed or any(result == WOLF and seat in living for seat, result in private.results.items()):
        return True
    return len(accusers_today(ledger).get(facts.seat, ())) >= tuning.reveal_when_accused_by


def reveal_points(ledger: Ledger) -> tuple[str, ...]:
    """What a reveal must say: the claim `I am the seer` and every result, as `PlayerN is a wolf` or `a villager`."""
    results = ledger.private().results
    return SEER_CLAIM_POINT, *(RESULT_POINT.format(player=label(seat), result=found) for seat, found in results.items())
