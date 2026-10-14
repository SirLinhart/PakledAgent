"""The draft of a practice match, by the rules of the platform's guide (pokemon.md, section 4).

An 18-card pool is drawn from the catalog by seed, and so is the first
drafter unless the match names it. The two seats then pick in the snake order A B B A A B B A A B B A
until each has six cards. A seat is offered only the remaining cards whose
item is not already on its roster (the Item Clause). Each pick has 15
seconds from the moment it becomes the seat's turn; when they pass, a random
card of those offered to the seat is picked for it and marked
`auto: true, auto_reason: draft_timeout`, and the next pick has its own 15
seconds from that moment.

The pool never holds more than two cards with the same item. A seat then
always has a card on offer: at most one remaining card per item on its
roster is withheld from it (its own card took the other), so with at most
five items on its roster and at least seven cards left before any pick,
two or more are offered. The platform does not say how it draws its pool;
this is how the practice draft keeps every draft finishable.

`available_cards` in a seat's observation lists every card still in the
pool, the same for both seats; the legal actions are the cards offered to
the seat whose pick it is.
"""

from __future__ import annotations

import random
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from ucla_agent.practice.pokemon.cards import Card, CardCatalog
from ucla_agent.practice.pokemon.rules import INVALID_ACTION, NOT_YOUR_TURN, RuleError

POOL_SIZE = 18
ROSTER_SIZE = 6
MAX_CARDS_PER_ITEM = 2
DECISION_TIMEOUT_SECONDS = 15
# Which of the two drafters picks at each of the 12 picks: 0 the first drafter, 1 the other.
SNAKE_ORDER = (0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0)
DRAFT_PHASE = "draft"
PICK_TYPE = "draft_pick"
ACTION_ID_PREFIX = f"{PICK_TYPE}:"
TIMEOUT_REASON = "draft_timeout"
_POOL_SEED = "pokemon-draft-pool:{seed}"
_AUTO_PICK_SEED = "pokemon-draft-auto-pick:{seed}"


@dataclass(frozen=True)
class Pick:
    """One pick of the draft, as the observation's `picks` lists it."""

    pick_number: int
    seat_id: str
    card: Card
    public_reason: str | None
    auto: bool

    def entry(self) -> dict[str, Any]:
        return {
            "pick_number": self.pick_number,
            "seat_id": self.seat_id,
            "card_id": self.card.card_id,
            "species": self.card.species,
            "public_reason": self.public_reason,
            "auto": self.auto,
            "auto_reason": TIMEOUT_REASON if self.auto else None,
        }


def draw_pool(catalog: CardCatalog, rng: random.Random) -> list[Card]:
    """`POOL_SIZE` cards in drawn order, no item on more than `MAX_CARDS_PER_ITEM` of them; raises when that is impossible."""
    shuffled = list(catalog.cards.values())
    rng.shuffle(shuffled)
    pool: list[Card] = []
    items: Counter[str] = Counter()
    for card in shuffled:
        if items[card.item] < MAX_CARDS_PER_ITEM:
            pool.append(card)
            items[card.item] += 1
        if len(pool) == POOL_SIZE:
            return pool
    raise ValueError(f"the catalog cannot fill a pool of {POOL_SIZE} with at most {MAX_CARDS_PER_ITEM} cards per item")


class PracticeDraft:
    """The draft of one practice match between two seats, named by their agent ids."""

    def __init__(
        self,
        seed: int,
        catalog: CardCatalog,
        seats: tuple[str, str],
        clock: Callable[[], float] = time.time,
        first_drafter: str | None = None,
    ) -> None:
        """`clock` gives the time in seconds since the epoch; a test hands in its own to move time on.

        `first_drafter`, one of `seats`, makes pick 1; left out, it is drawn
        from the seed. The pool is drawn before it either way, so naming the
        first drafter never changes the seed's cards.
        """
        rng = random.Random(_POOL_SEED.format(seed=seed))
        self.battle_format = catalog.battle_format
        self.seats = seats
        self.pool = draw_pool(catalog, rng)
        self.first_drafter = seats[rng.randrange(len(seats))] if first_drafter is None else first_drafter
        second = seats[1] if self.first_drafter == seats[0] else seats[0]
        self.order = tuple((self.first_drafter, second)[drafter] for drafter in SNAKE_ORDER)
        self.picks: list[Pick] = []
        self._clock = clock
        self._auto_pick_rng = random.Random(_AUTO_PICK_SEED.format(seed=seed))
        self.deadline = clock() + DECISION_TIMEOUT_SECONDS

    @property
    def is_complete(self) -> bool:
        return len(self.picks) == len(self.order)

    @property
    def current_seat(self) -> str | None:
        """The seat whose pick it is; None once the draft is complete."""
        return None if self.is_complete else self.order[len(self.picks)]

    def available(self) -> list[Card]:
        """The cards not picked yet, in pool order."""
        taken = {pick.card.card_id for pick in self.picks}
        return [card for card in self.pool if card.card_id not in taken]

    def roster(self, seat: str) -> list[Card]:
        """The seat's cards in the order it picked them."""
        return [pick.card for pick in self.picks if pick.seat_id == seat]

    def offered(self, seat: str) -> list[Card]:
        """The cards the seat may pick: those not picked yet whose item is not on its roster."""
        held_items = {card.item for card in self.roster(seat)}
        return [card for card in self.available() if card.item not in held_items]

    def pick(self, seat: str, card_id: str, public_reason: str | None = None) -> Pick:
        """The seat picks a card; refused with NOT_YOUR_TURN or INVALID_ACTION as the platform refuses it."""
        if seat != self.current_seat:
            raise RuleError(NOT_YOUR_TURN, f"it is not {seat}'s pick")
        card = next((card for card in self.available() if card.card_id == card_id), None)
        if card is None:
            raise RuleError(INVALID_ACTION, f"{card_id!r} is not a card left in the pool")
        if card not in self.offered(seat):
            raise RuleError(INVALID_ACTION, f"{card_id} holds {card.item}, already on {seat}'s roster (Item Clause)")
        return self._record(seat, card, public_reason, auto=False, picked_at=self._clock())

    def apply_timeout(self) -> bool:
        """Pick a random offered card for the seat whose 15 seconds have passed; whether one was picked."""
        seat = self.current_seat
        if seat is None or self._clock() < self.deadline:
            return False
        card = self._auto_pick_rng.choice(self.offered(seat))
        self._record(seat, card, None, auto=True, picked_at=self.deadline)
        return True

    def _record(self, seat: str, card: Card, public_reason: str | None, *, auto: bool, picked_at: float) -> Pick:
        made = Pick(len(self.picks) + 1, seat, card, public_reason, auto)
        self.picks.append(made)
        self.deadline = picked_at + DECISION_TIMEOUT_SECONDS
        return made

    def observation(self) -> dict[str, Any]:
        """The draft observation, the same for both seats (pokemon.md, section 4)."""
        available = self.available()
        return {
            "phase": DRAFT_PHASE,
            "draft_complete": self.is_complete,
            "battle_format": self.battle_format,
            "current_seat": self.current_seat,
            "pick_number": min(len(self.picks) + 1, len(self.order)),
            "picks_remaining": len(self.order) - len(self.picks),
            "first_drafter": self.first_drafter,
            "available_card_ids": [card.card_id for card in available],
            "available_cards": [card.draft_entry() for card in available],
            "rosters": {
                seat: [{"card_id": card.card_id, "species": card.species} for card in self.roster(seat)]
                for seat in self.seats
            },
            "picks": [made.entry() for made in self.picks],
            "decision_timeout_seconds": DECISION_TIMEOUT_SECONDS,
            "decision_deadline_at": datetime.fromtimestamp(self.deadline, timezone.utc).isoformat(timespec="seconds"),
            "battle_starting": self.is_complete,
        }

    def legal_actions(self, seat: str, session_id: str, state_version: int) -> list[dict[str, Any]]:
        """One `draft_pick:<card_id>` action per card offered to the seat, when it is the seat's pick; else none."""
        if seat != self.current_seat:
            return []
        return [_pick_action(card, session_id, state_version) for card in self.offered(seat)]


def _pick_action(card: Card, session_id: str, state_version: int) -> dict[str, Any]:
    action_id = ACTION_ID_PREFIX + card.card_id
    return {
        "action_id": action_id,
        "label": f"Draft {card.species} ({card.item})",
        "input": {
            "session_id": session_id,
            "action_id": action_id,
            "state_version": state_version,
            "action": {"type": PICK_TYPE, "card_id": card.card_id},
        },
    }


def picked_card_id(action_id: str | None, action: dict[str, Any] | None) -> str:
    """The card a `play_action` picks: `action={"type": "draft_pick", "card_id": ...}`, else `draft_pick:<card_id>`."""
    return _card_of_action(action) if action is not None else _card_of_action_id(action_id)


def _card_of_action(action: dict[str, Any]) -> str:
    if action.get("type") != PICK_TYPE or not isinstance(action.get("card_id"), str):
        raise RuleError(INVALID_ACTION, f'a draft pick is {{"type": "{PICK_TYPE}", "card_id": ...}}, not {action!r}')
    return action["card_id"]


def _card_of_action_id(action_id: str | None) -> str:
    if isinstance(action_id, str) and action_id.startswith(ACTION_ID_PREFIX):
        return action_id[len(ACTION_ID_PREFIX) :]
    raise RuleError(INVALID_ACTION, f"a draft pick's action_id is {ACTION_ID_PREFIX}<card_id>, not {action_id!r}")
