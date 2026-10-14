"""The engine's truth about the real seer's claim in a finished practice game, for the benchmark's results only.

These columns are read from the engine once the game is over and go into
`results.csv`; no seat is ever shown them. A seer claim is a public chat
that matches the agent ledger's own seer-claim pattern (`SEER_CLAIM_TEXT`),
so the benchmark counts as a claim exactly what the agent reads as one.

- `seer_claim_day`: the first day the real seer made a public seer claim;
  empty when it never did.
- `seer_voted_out_on_claim_day`: 1 when the real seer was eliminated by the
  vote of that day and no other seat had publicly claimed the seer by that
  day (the honest lone claimant the village voted out), else 0.
- `village_won`: 1 when the village side won.
"""

from __future__ import annotations

from typing import Any

from ucla_agent.games.werewolf.ledger import SEER_CLAIM_TEXT
from ucla_agent.practice.werewolf.engine import CHAT, SEER, VILLAGER, VOTE, WerewolfEngine

SEER_CLAIM_DAY = "seer_claim_day"
SEER_VOTED_OUT_ON_CLAIM_DAY = "seer_voted_out_on_claim_day"
VILLAGE_WON = "village_won"
TRUTH_COLUMNS = (SEER_CLAIM_DAY, SEER_VOTED_OUT_ON_CLAIM_DAY, VILLAGE_WON)
# The columns of a game that raised before it ended: no claim seen and no win.
UNPLAYED = {SEER_CLAIM_DAY: None, SEER_VOTED_OUT_ON_CLAIM_DAY: 0, VILLAGE_WON: 0}


def truth_columns(engine: WerewolfEngine) -> dict[str, Any]:
    """The engine-truth columns of a finished game: the real seer's first claim day, whether the vote of that day
    eliminated it as the only claimant so far, and whether the village won."""
    seer = engine.roles.index(SEER)
    first_claims = first_claim_days(engine)
    claim_day = first_claims.get(seer)
    lone_claimant = claim_day is not None and all(
        day > claim_day for claimant, day in first_claims.items() if claimant != seer
    )
    voted_out = any(death.seat == seer and death.cause == VOTE and death.day == claim_day for death in engine.deaths)
    return {
        SEER_CLAIM_DAY: claim_day,
        SEER_VOTED_OUT_ON_CLAIM_DAY: int(lone_claimant and voted_out),
        VILLAGE_WON: int(engine.winner == VILLAGER),
    }


def first_claim_days(engine: WerewolfEngine) -> dict[int, int]:
    """Each seat that made a public seer claim, with the day of its first one."""
    first: dict[int, int] = {}
    for message in engine.messages:
        if message.type == CHAT and not message.recipients and SEER_CLAIM_TEXT.search(message.content):
            first.setdefault(message.sender, message.day)
    return first
