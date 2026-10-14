"""Which opponents sit at a practice table, and in which seats.

A table mix names the six opponents the agent plays against, by kind. The
`scripted` mix is the starter kit's placeholder, the guide's abstaining
skeleton and the record-voting script, two of each; the `mixed` mix swaps
the abstainers for two players on the local model, closer to a tournament
table of other contestants' agents; the `self` mix is six copies of the
Werewolf agent itself with its models off, for self-play. The six are placed
in the six seats the agent does not play, in an order shuffled by a
generator seeded from the game's seed alone, so the same seed and mix always
give the same seating and the deal's own generator is left untouched.
"""

from __future__ import annotations

import random
from typing import Callable, Mapping, Sequence

from ucla_agent.practice.werewolf.agent_opponent import AgentOpponent
from ucla_agent.practice.werewolf.engine import SEAT_COUNT, SEATS
from ucla_agent.practice.werewolf.llm_player import LlmPlayer
from ucla_agent.practice.werewolf.opponents import AbstainerPlayer, PlaceholderPlayer, RecordVoter
from ucla_agent.practice.werewolf.seat import Opponent

PLACEHOLDER, ABSTAINER, RECORD_VOTER, LLM_PLAYER = "placeholder", "abstainer", "record_voter", "llm_player"
WEREWOLF_AGENT = "werewolf_agent"
SCRIPTED_MIX, MIXED_MIX, SELF_MIX = "scripted", "mixed", "self"
# The name a table goes by when it is given as six kinds rather than a mix name.
CUSTOM_MIX = "custom"
OPPONENT_COUNT = SEAT_COUNT - 1
OPPONENT_FACTORIES: dict[str, Callable[[], Opponent]] = {
    PLACEHOLDER: PlaceholderPlayer,
    ABSTAINER: AbstainerPlayer,
    RECORD_VOTER: RecordVoter,
    LLM_PLAYER: LlmPlayer,
    WEREWOLF_AGENT: AgentOpponent,
}
MIXES: dict[str, tuple[str, ...]] = {
    SCRIPTED_MIX: (PLACEHOLDER, PLACEHOLDER, ABSTAINER, ABSTAINER, RECORD_VOTER, RECORD_VOTER),
    MIXED_MIX: (PLACEHOLDER, PLACEHOLDER, RECORD_VOTER, RECORD_VOTER, LLM_PLAYER, LLM_PLAYER),
    SELF_MIX: (WEREWOLF_AGENT,) * OPPONENT_COUNT,
}
_SEATING_SEED = "werewolf-seating:{seed}"


def table_kinds(opponents: str | Sequence[str]) -> tuple[str, tuple[str, ...]]:
    """The mix name and the six opponent kinds of a table given as a mix name or as six kind names."""
    if isinstance(opponents, str):
        if opponents not in MIXES:
            raise ValueError(f"no table mix is named {opponents!r}; the mixes are {sorted(MIXES)}")
        return opponents, MIXES[opponents]
    kinds = tuple(opponents)
    unknown = sorted(set(kinds) - set(OPPONENT_FACTORIES))
    if len(kinds) != OPPONENT_COUNT or unknown:
        raise ValueError(f"a table is {OPPONENT_COUNT} kinds of {sorted(OPPONENT_FACTORIES)}, got {list(kinds)}")
    return CUSTOM_MIX, kinds


def seat_opponents(kinds: Sequence[str], seed: int, driven_seat: int) -> dict[int, str]:
    """Each seat other than `driven_seat` with the kind of opponent placed there, shuffled by the seed."""
    order = list(kinds)
    random.Random(_SEATING_SEED.format(seed=seed)).shuffle(order)
    return dict(zip([seat for seat in SEATS if seat != driven_seat], order))


def build_opponents(
    seating: Mapping[int, str], factories: Mapping[str, Callable[[], Opponent]] = OPPONENT_FACTORIES
) -> dict[int, Opponent]:
    """One new opponent of the seated kind for each seat."""
    return {seat: factories[kind]() for seat, kind in seating.items()}
