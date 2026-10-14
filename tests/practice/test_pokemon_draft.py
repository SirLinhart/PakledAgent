"""The Pokémon practice draft: its pool, snake order, Item Clause, 15-second clock and observations.

The numbered sections are the acceptance items of the local practice harness
this file covers: (1) the snake order and the Item Clause over 100 seeded
drafts, (2) a timeout auto-picks a random offered card, (3) the draft
observation matches the guide-derived fixture.
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Callable

import pytest

from ucla_agent.practice.pokemon.cards import CardCatalog, load_catalog
from ucla_agent.practice.pokemon.draft import (
    DECISION_TIMEOUT_SECONDS,
    MAX_CARDS_PER_ITEM,
    POOL_SIZE,
    TIMEOUT_REASON,
    PracticeDraft,
    draw_pool,
    picked_card_id,
)
from ucla_agent.practice.pokemon.rules import INVALID_ACTION, NOT_YOUR_TURN, RuleError

SEATS = ("agent_a", "agent_b")
SNAKE = "ABBAABBAABBA"
SEEDED_DRAFTS = 100
SESSION_ID = "practice-pokemon"


class FakeClock:
    """Seconds since the epoch that only move when a test moves them."""

    def __init__(self, start: float = 1_791_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


@pytest.fixture(scope="module")
def catalog() -> CardCatalog:
    return load_catalog()


def finish_randomly(draft: PracticeDraft, rng: random.Random) -> None:
    while not draft.is_complete:
        seat = draft.current_seat
        draft.pick(seat, rng.choice(draft.offered(seat)).card_id)


# -- (1) snake order and Item Clause over 100 seeded drafts ---------------------------------------


def test_100_seeded_drafts_pick_in_snake_order_and_keep_six_different_items_per_roster(catalog):
    first_drafters = Counter()
    for seed in range(SEEDED_DRAFTS):
        draft = PracticeDraft(seed, catalog, SEATS)
        first, second = draft.first_drafter, next(seat for seat in SEATS if seat != draft.first_drafter)
        finish_randomly(draft, random.Random(seed))

        letters = "".join("A" if pick.seat_id == first else "B" for pick in draft.picks)
        assert letters == SNAKE
        for seat in (first, second):
            items = [card.item for card in draft.roster(seat)]
            assert len(items) == 6 and len(set(items)) == 6
        first_drafters[first] += 1
    assert set(first_drafters) == set(SEATS)


def test_a_seeded_pool_is_18_different_cards_with_no_item_on_more_than_two(catalog):
    for seed in range(SEEDED_DRAFTS):
        pool = PracticeDraft(seed, catalog, SEATS).pool
        assert len(pool) == POOL_SIZE and len({card.card_id for card in pool}) == POOL_SIZE
        assert max(Counter(card.item for card in pool).values()) <= MAX_CARDS_PER_ITEM


def test_the_same_seed_draws_the_same_pool_and_first_drafter(catalog):
    one, other = PracticeDraft(7, catalog, SEATS), PracticeDraft(7, catalog, SEATS)

    assert [card.card_id for card in one.pool] == [card.card_id for card in other.pool]
    assert one.first_drafter == other.first_drafter
    assert [card.card_id for card in PracticeDraft(8, catalog, SEATS).pool] != [card.card_id for card in one.pool]


def test_a_card_whose_item_is_already_on_the_roster_is_not_offered_and_cannot_be_picked(catalog):
    seed = next(seed for seed in range(SEEDED_DRAFTS) if _has_shared_item(PracticeDraft(seed, catalog, SEATS)))
    draft = PracticeDraft(seed, catalog, SEATS)
    item = next(item for item, count in Counter(card.item for card in draft.pool).items() if count == 2)
    holder, twin = [card for card in draft.pool if card.item == item]
    picker = draft.first_drafter
    draft.pick(picker, holder.card_id)
    _pass_turn_back_to(draft, picker, avoid={twin.card_id})

    assert twin in draft.available() and twin not in draft.offered(picker)
    with pytest.raises(RuleError) as refused:
        draft.pick(picker, twin.card_id)
    assert refused.value.code == INVALID_ACTION and "Item Clause" in refused.value.detail


def _has_shared_item(draft: PracticeDraft) -> bool:
    return max(Counter(card.item for card in draft.pool).values()) == 2


def _pass_turn_back_to(draft: PracticeDraft, seat: str, avoid: set[str]) -> None:
    while draft.current_seat != seat:
        other = draft.current_seat
        draft.pick(other, next(card.card_id for card in draft.offered(other) if card.card_id not in avoid))


def test_a_pick_out_of_turn_or_of_a_taken_card_is_refused(catalog):
    draft = PracticeDraft(3, catalog, SEATS)
    first = draft.first_drafter
    other = next(seat for seat in SEATS if seat != first)
    card = draft.offered(first)[0]

    with pytest.raises(RuleError) as out_of_turn:
        draft.pick(other, card.card_id)
    draft.pick(first, card.card_id)
    with pytest.raises(RuleError) as taken:
        draft.pick(other, card.card_id)

    assert out_of_turn.value.code == NOT_YOUR_TURN
    assert taken.value.code == INVALID_ACTION


def test_a_pool_cannot_be_drawn_from_a_catalog_with_too_few_items(catalog):
    one_item = CardCatalog(
        battle_format=catalog.battle_format,
        cards={card_id: card for card_id, card in catalog.cards.items() if card.item == "Assault Vest"},
        species=catalog.species,
        moves=catalog.moves,
    )

    with pytest.raises(ValueError, match="cannot fill a pool"):
        draw_pool(one_item, random.Random(0))


# -- (2) the 15-second clock -----------------------------------------------------------------------


def test_a_pick_left_past_15_seconds_is_a_random_offered_card_marked_as_a_timeout(catalog):
    clock = FakeClock()
    draft = PracticeDraft(11, catalog, SEATS, clock=clock)
    seat, offered = draft.current_seat, draft.offered(draft.current_seat)

    clock.now += DECISION_TIMEOUT_SECONDS - 0.5
    assert draft.apply_timeout() is False and draft.picks == []
    clock.now += 0.5
    assert draft.apply_timeout() is True

    [made] = draft.picks
    assert made.seat_id == seat and made.card in offered
    assert made.entry()["auto"] is True and made.entry()["auto_reason"] == TIMEOUT_REASON
    assert draft.observation()["picks"][0]["auto_reason"] == "draft_timeout"


def test_the_next_pick_after_a_timeout_has_its_own_15_seconds_from_the_deadline(catalog):
    clock = FakeClock()
    draft = PracticeDraft(11, catalog, SEATS, clock=clock)
    started = clock.now

    clock.now += 40
    assert draft.apply_timeout() is True
    assert draft.deadline == started + 2 * DECISION_TIMEOUT_SECONDS
    assert draft.apply_timeout() is True
    assert draft.apply_timeout() is False
    assert draft.deadline == started + 3 * DECISION_TIMEOUT_SECONDS


def test_the_auto_pick_is_random_among_the_offered_cards_and_seeded(catalog):
    picked = set()
    for seed in range(20):
        clock = FakeClock()
        draft = PracticeDraft(5, catalog, SEATS, clock=clock)
        draft._auto_pick_rng = random.Random(seed)
        clock.now += DECISION_TIMEOUT_SECONDS
        draft.apply_timeout()
        picked.add(draft.picks[0].card.card_id)
    assert len(picked) > 1

    def first_auto_pick() -> str:
        clock = FakeClock()
        draft = PracticeDraft(5, catalog, SEATS, clock=clock)
        clock.now += DECISION_TIMEOUT_SECONDS
        draft.apply_timeout()
        return draft.picks[0].card.card_id

    assert first_auto_pick() == first_auto_pick()


def test_a_pick_in_time_resets_the_clock_for_the_next_pick(catalog):
    clock = FakeClock()
    draft = PracticeDraft(2, catalog, SEATS, clock=clock)
    clock.now += 10
    draft.pick(draft.current_seat, draft.offered(draft.current_seat)[0].card_id)

    clock.now += DECISION_TIMEOUT_SECONDS - 1
    assert draft.apply_timeout() is False
    assert draft.deadline == clock.now + 1


def test_a_finished_draft_never_times_out(catalog):
    clock = FakeClock()
    draft = PracticeDraft(2, catalog, SEATS, clock=clock)
    finish_randomly(draft, random.Random(2))
    clock.now += 1000

    assert draft.apply_timeout() is False


# -- (3) the draft observation and its legal actions -----------------------------------------------


def test_the_draft_observation_after_the_first_pick_matches_the_guide_fixture(
    catalog, pokemon_fixture: Callable, json_shape: Callable
):
    seed = next(seed for seed in range(SEEDED_DRAFTS) if PracticeDraft(seed, catalog, SEATS).first_drafter == "agent_a")
    draft = PracticeDraft(seed, catalog, SEATS)
    draft.pick("agent_a", draft.offered("agent_a")[0].card_id, public_reason="Intimidate support.")
    fixture = pokemon_fixture("draft_observation.json")

    assert "guide-derived, unverified on the platform" in fixture["_source"]
    observation = draft.observation()
    assert json_shape(observation) == json_shape(fixture["observation"])
    assert list(observation) == list(fixture["observation"])
    [action] = draft.legal_actions("agent_b", SESSION_ID, 1)[:1]
    assert json_shape(action) == json_shape(fixture["legal_action"])


def test_the_draft_observation_counts_picks_and_names_whose_turn_it_is(catalog):
    clock = FakeClock(start=0.0)
    draft = PracticeDraft(4, catalog, SEATS, clock=clock)
    start = draft.observation()

    assert (start["pick_number"], start["picks_remaining"], start["picks"]) == (1, 12, [])
    assert (start["current_seat"], start["first_drafter"]) == (draft.first_drafter, draft.first_drafter)
    assert len(start["available_cards"]) == 18
    assert start["available_card_ids"][0] == draft.pool[0].card_id
    assert start["decision_deadline_at"] == "1970-01-01T00:00:15+00:00"
    assert (start["draft_complete"], start["battle_starting"]) == (False, False)

    finish_randomly(draft, random.Random(4))
    end = draft.observation()
    assert (end["draft_complete"], end["battle_starting"], end["current_seat"]) == (True, True, None)
    assert (end["picks_remaining"], len(end["available_cards"])) == (0, 6)
    assert [len(roster) for roster in end["rosters"].values()] == [6, 6]


def test_only_the_seat_whose_pick_it_is_has_legal_actions_one_per_offered_card(catalog):
    draft = PracticeDraft(4, catalog, SEATS)
    current = draft.current_seat
    other = next(seat for seat in SEATS if seat != current)

    actions = draft.legal_actions(current, SESSION_ID, 0)
    assert [action["action_id"] for action in actions] == [
        f"draft_pick:{card.card_id}" for card in draft.offered(current)
    ]
    assert actions[0]["input"]["action"] == {"type": "draft_pick", "card_id": draft.offered(current)[0].card_id}
    assert actions[0]["input"]["state_version"] == 0 and actions[0]["input"]["session_id"] == SESSION_ID
    assert draft.legal_actions(other, SESSION_ID, 0) == []


@pytest.mark.parametrize(
    ("action_id", "action", "card_id"),
    [
        (None, {"type": "draft_pick", "card_id": "vgc-gyarados"}, "vgc-gyarados"),
        ("draft_pick:vgc-gyarados", None, "vgc-gyarados"),
        ("draft_pick:vgc-other", {"type": "draft_pick", "card_id": "vgc-gyarados"}, "vgc-gyarados"),
    ],
)
def test_a_pick_is_read_from_the_structured_action_first_then_from_the_action_id(action_id, action, card_id):
    assert picked_card_id(action_id, action) == card_id


@pytest.mark.parametrize(
    ("action_id", "action"),
    [(None, {"type": "select_lineup"}), ("move:0", None), (None, None), (None, {"type": "draft_pick", "card_id": 3})],
)
def test_a_pick_that_names_no_card_is_invalid(action_id, action):
    with pytest.raises(RuleError) as refused:
        picked_card_id(action_id, action)
    assert refused.value.code == INVALID_ACTION
