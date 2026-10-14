"""Role inference: how likely each seat is to be a wolf, and to be the seer, from what the ledger holds.

Seven seats hold two wolves and one seer, so there are 105 layouts: 21 pairs
of wolf seats, then 5 seats left for the seer. `infer` keeps every layout no
certain fact contradicts (a dead seat's revealed role, our own role, our wolf
ally, our seer results), weighs each by the evidence of the public record and
the chat, and gives each seat, for each role, its weight share of the layouts
in which it holds that role.

The evidence weights are a heuristic, not a learned model. Each is a
multiplier from `[inference]` of `config/werewolf/tuning.toml`, applied once
for each piece of evidence to the layouts it names:

- a vote a seat cast for a player since revealed as the seer or a villager
  (`vote_for_revealed_villager`), or as a wolf (`vote_for_revealed_wolf`):
  the layouts where the voter is a wolf;
- a seat that claimed the seer: every layout, by the role the seat holds in
  it (`seer_claim_if_seer`, `_if_wolf`, `_if_villager`);
- a seer result a seat claimed: the layouts where that seat is the seer and
  the result is wrong (`false_result_if_seer`);
- a seat that accused a player before that player was revealed as a wolf:
  the layouts where the accuser is a wolf (`accused_revealed_wolf`);
- a seat that made an injection attempt: the layouts where it is a wolf
  (`injection_attempt`).

A claim repeated word for word is not new evidence, so each seer claimant,
result claim, accusing pair and injecting seat counts once. Every weight is
above 0, so evidence never rules a layout out: only certain facts do.

So only facts that contradict each other leave no layout, which a sound
state never shows. Should it happen, the beliefs fall back to the uniform
distribution over the layouts the engine facts alone allow (the public
record outranks what our observation was read as), or over all 105 when even
those contradict each other; they are marked `reset`, and one
`werewolf_inference_reset` event is written to the match record.

It is a pure function of the ledger and the tuning: no randomness, no state
kept between calls, and no I/O but that event.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import combinations
from typing import Any, Iterable, Sequence

from ucla_agent.core import records
from ucla_agent.games.werewolf.ledger import (
    ACCUSATION,
    INJECTION_ATTEMPT,
    RESULT_CLAIM,
    SEER,
    SEER_CLAIM,
    VILLAGER,
    WOLF,
    Claim,
    EngineFacts,
    Ledger,
    PrivateFacts,
)
from ucla_agent.games.werewolf.tuning import InferenceTuning

SEAT_COUNT = 7
SEATS = tuple(range(SEAT_COUNT))
WOLVES_PER_GAME = 2
# The roles a seat the seer found `villager` may hold: it is not a wolf, and the seer cannot look at itself.
NOT_WOLF = frozenset({SEER, VILLAGER})
RESET_EVENT = "werewolf_inference_reset"
# What the reset event says the beliefs fell back to.
ENGINE_FACTS_FALLBACK = "engine_facts"
ALL_LAYOUTS_FALLBACK = "all_layouts"


@dataclass(frozen=True)
class Layout:
    """One way the roles can sit: the role of each seat, in seat order."""

    roles: tuple[str, ...]

    @classmethod
    def of(cls, wolves: Iterable[int], seer: int) -> Layout:
        """The layout with wolves at `wolves`, the seer at `seer` and villagers everywhere else."""
        wolf_seats = set(wolves)
        return cls(tuple(WOLF if seat in wolf_seats else SEER if seat == seer else VILLAGER for seat in SEATS))


# Every layout of the game, in a fixed order.
LAYOUTS = tuple(
    Layout.of(pair, seer) for pair in combinations(SEATS, WOLVES_PER_GAME) for seer in SEATS if seer not in pair
)


@dataclass(frozen=True)
class Constraint:
    """A certain fact about one seat: the roles it may hold."""

    seat: int
    roles: frozenset[str]

    def allows(self, layout: Layout) -> bool:
        """Whether the layout gives the seat one of the roles the fact allows."""
        return layout.roles[self.seat] in self.roles


@dataclass(frozen=True)
class Beliefs:
    """For every seat, the probability that it is a wolf and that it is the seer; `layouts` were left possible.

    `reset` is true when the certain facts left no layout and the beliefs are
    the uniform fallback instead.
    """

    p_wolf: dict[int, float]
    p_seer: dict[int, float]
    layouts: int
    reset: bool = False


@dataclass(frozen=True)
class Evidence:
    """What weighs on the layouts, gathered from the ledger once for all of them.

    `wolf_factors` is, for each seat, the product of every multiplier that
    applies when that seat is a wolf; `seer_claimants` are the seats that
    claimed the seer, weighed by `seer_claim_factors` for the role each holds
    in a layout; `result_claims` are the distinct (claimant, target, result)
    seer results claimed, each weighed by `false_result_factor` in a layout
    where its claimant is the seer and the result is wrong.
    """

    wolf_factors: dict[int, float]
    seer_claimants: frozenset[int]
    seer_claim_factors: dict[str, float]
    result_claims: frozenset[tuple[int, int, str]]
    false_result_factor: float


def infer(ledger: Ledger, tuning: InferenceTuning) -> Beliefs:
    """The beliefs the ledger supports: each seat's weight share of the layouts consistent with every certain fact."""
    facts, private = ledger.facts(), ledger.private()
    public = engine_constraints(facts)
    consistent = _allowed(LAYOUTS, public + private_constraints(facts.seat, private))
    if not consistent:
        return _reset(facts, private, public)
    evidence = gather_evidence(facts, ledger.claims(), tuning)
    return _beliefs(consistent, [layout_weight(layout, evidence) for layout in consistent])


def _allowed(layouts: Sequence[Layout], constraints: Sequence[Constraint]) -> list[Layout]:
    """The layouts no constraint rules out."""
    return [layout for layout in layouts if all(constraint.allows(layout) for constraint in constraints)]


def _reset(facts: EngineFacts, private: PrivateFacts, public: Sequence[Constraint]) -> Beliefs:
    """The uniform fallback for facts that contradict each other, and the event that records it."""
    allowed_by_engine = _allowed(LAYOUTS, public)
    fallback = ENGINE_FACTS_FALLBACK if allowed_by_engine else ALL_LAYOUTS_FALLBACK
    layouts = allowed_by_engine or list(LAYOUTS)
    records.log(RESET_EVENT, _reset_event_data(facts, private, fallback, len(layouts)))
    return replace(_beliefs(layouts, [1.0] * len(layouts)), reset=True)


def _reset_event_data(facts: EngineFacts, private: PrivateFacts, fallback: str, layouts: int) -> dict[str, Any]:
    """The reset event: the certain facts that left no layout, and what the beliefs fell back to."""
    return {
        "fallback": fallback,
        "layouts": layouts,
        "seat": facts.seat,
        "deaths": [{"day": death.day, "seat": death.seat, "role": death.role} for death in facts.deaths],
        "role": private.role,
        "allies": list(private.allies),
        "results": [{"seat": seat, "result": result} for seat, result in private.results.items()],
    }


def gather_evidence(facts: EngineFacts, claims: Sequence[Claim], tuning: InferenceTuning) -> Evidence:
    """The evidence of the votes and the claims, as the multipliers each kind of layout gets."""
    wolf_factors = dict.fromkeys(SEATS, 1.0)
    for voter, target in _votes_for_the_dead(facts):
        is_wolf = facts.revealed_role(target) == WOLF
        wolf_factors[voter] *= tuning.vote_for_revealed_wolf if is_wolf else tuning.vote_for_revealed_villager
    for accuser in _accusers_of_revealed_wolves(facts, claims):
        wolf_factors[accuser] *= tuning.accused_revealed_wolf
    for injector in {claim.seat for claim in claims if claim.kind == INJECTION_ATTEMPT}:
        wolf_factors[injector] *= tuning.injection_attempt
    return Evidence(
        wolf_factors=wolf_factors,
        seer_claimants=frozenset(claim.seat for claim in claims if claim.kind == SEER_CLAIM),
        seer_claim_factors={
            SEER: tuning.seer_claim_if_seer,
            WOLF: tuning.seer_claim_if_wolf,
            VILLAGER: tuning.seer_claim_if_villager,
        },
        result_claims=frozenset(
            (claim.seat, claim.target, claim.result) for claim in claims if claim.kind == RESULT_CLAIM
        ),
        false_result_factor=tuning.false_result_if_seer,
    )


def layout_weight(layout: Layout, evidence: Evidence) -> float:
    """The weight the evidence gives one layout: the product of every multiplier that applies to it."""
    roles = layout.roles
    weight = 1.0
    for seat, role in enumerate(roles):
        if role == WOLF:
            weight *= evidence.wolf_factors[seat]
    for claimant in evidence.seer_claimants:
        weight *= evidence.seer_claim_factors[roles[claimant]]
    for claimant, target, result in evidence.result_claims:
        if roles[claimant] == SEER and (roles[target] == WOLF) != (result == WOLF):
            weight *= evidence.false_result_factor
    return weight


def _votes_for_the_dead(facts: EngineFacts) -> list[tuple[int, int]]:
    """Every (voter, target) of every completed day's vote whose target has since died and so shown its role."""
    dead = {death.seat for death in facts.deaths}
    return [(voter, target) for vote in facts.votes for voter, target in vote.votes.items() if target in dead]


def _accusers_of_revealed_wolves(facts: EngineFacts, claims: Sequence[Claim]) -> list[int]:
    """The accuser of each distinct (accuser, wolf) pair where the accusation came on or before the wolf's death day.

    A wolf dies only by the day's vote, which follows that day's discussion,
    so an accusation of the wolf's death day came before the reveal.
    """
    wolf_death_days = {death.seat: death.day for death in facts.deaths if death.role == WOLF}
    pairs = {
        (claim.seat, claim.target)
        for claim in claims
        if claim.kind == ACCUSATION
        and claim.target in wolf_death_days
        and claim.day is not None
        and claim.day <= wolf_death_days[claim.target]
    }
    return [accuser for accuser, _ in pairs]


def engine_constraints(facts: EngineFacts) -> list[Constraint]:
    """The certain public facts: each dead seat held the role its death revealed."""
    return [Constraint(death.seat, frozenset({death.role})) for death in facts.deaths]


def private_constraints(seat: int | None, private: PrivateFacts) -> list[Constraint]:
    """The certain private facts: our own role, our fellow wolves, and the seats our seer results name."""
    if seat is None or private.role is None:
        return []
    constraints = [Constraint(seat, frozenset({private.role}))]
    constraints += [Constraint(ally, frozenset({WOLF})) for ally in private.allies]
    constraints += [
        Constraint(target, frozenset({WOLF}) if result == WOLF else NOT_WOLF)
        for target, result in private.results.items()
    ]
    return constraints


def _beliefs(layouts: Sequence[Layout], weights: Sequence[float]) -> Beliefs:
    """Each seat's weight share of the layouts in which it is a wolf, and in which it is the seer."""
    total = sum(weights)
    p_wolf = dict.fromkeys(SEATS, 0.0)
    p_seer = dict.fromkeys(SEATS, 0.0)
    for layout, weight in zip(layouts, weights):
        share = weight / total
        for seat, role in enumerate(layout.roles):
            if role == WOLF:
                p_wolf[seat] += share
            elif role == SEER:
                p_seer[seat] += share
    return Beliefs(p_wolf=p_wolf, p_seer=p_seer, layouts=len(layouts))
