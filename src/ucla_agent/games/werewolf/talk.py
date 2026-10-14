"""What the discussion says about each seat, read from the ledger's claims and messages for both sides' plans.

"Today" is the ledger's most recent discussion window: the day's discussion,
which still counts at that day's vote and the night after it. Every claim is
another seat's words; our own messages never make a claim. Only
`active_seer_claimants` reads our own messages too: a seer claim we sent
stands beside the others'.
"""

from __future__ import annotations

import re
from collections import Counter

from ucla_agent.games.werewolf.ledger import ACCUSATION, SEER_CLAIM, SEER_CLAIM_TEXT, Ledger


def accusers_today(ledger: Ledger) -> dict[int, frozenset[int]]:
    """For each seat accused in today's discussion, the distinct seats that accused it."""
    accusers: dict[int, set[int]] = {}
    for claim in ledger.claims(today=True):
        if claim.kind == ACCUSATION:
            accusers.setdefault(claim.target, set()).add(claim.seat)
    return {target: frozenset(seats) for target, seats in accusers.items()}


def seer_claimants(ledger: Ledger) -> list[int]:
    """The seats that claimed the seer in any window, in the order of their first claim."""
    return list(dict.fromkeys(claim.seat for claim in ledger.claims() if claim.kind == SEER_CLAIM))


def active_seer_claimants(ledger: Ledger, retraction: re.Pattern[str]) -> list[int]:
    """The seats, ours included, whose seer claim stands, in the order of their first claim.

    A seat's claim stands when no message it sent after its last claim
    matches `retraction`. A claim is read from every message by the ledger's
    own seer-claim pattern, so another seat's counts exactly when the ledger
    took a seer claim from it.
    """
    messages = sorted(ledger.messages(), key=lambda message: message.seq)
    claimed = [message for message in messages if SEER_CLAIM_TEXT.search(message.text)]
    last_claim = {message.sender: message.seq for message in claimed}
    retracted = {
        message.sender
        for message in messages
        if message.seq > last_claim.get(message.sender, message.seq) and retraction.search(message.text)
    }
    return [seat for seat in dict.fromkeys(message.sender for message in claimed) if seat not in retracted]


def messages_sent_today(ledger: Ledger) -> Counter[int]:
    """How many chats each seat sent in today's discussion that our seat saw."""
    return Counter(message.sender for message in ledger.messages(today=True))
