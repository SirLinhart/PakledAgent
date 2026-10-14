"""The draft: card memory of both rosters and the scored pick with its public reason.

The numbered tests are the acceptance items of the draft policy, on draft
observations in the guide's shape (pokemon.md, section 4): hand-built ones,
and those of the practice harness's draft over the tracked card catalog.
"""

from __future__ import annotations

import dataclasses
import random
import time
from pathlib import Path
from typing import Any

import pytest

from altruagent import LegalAction
from ucla_agent.games.pokemon import sets
from ucla_agent.games.pokemon.draft import (
    BULK,
    DENIAL,
    LOOKAHEAD,
    OURS,
    POWER,
    THEIRS,
    DraftMemory,
    offered_card_ids,
    pick_action,
    score_cards,
    score_cards_lookahead,
)
from ucla_agent.games.pokemon.observe import Reader
from ucla_agent.games.pokemon.tuning import Tuning
from ucla_agent.practice.pokemon.cards import load_catalog
from ucla_agent.practice.pokemon.draft import PracticeDraft

REPO_ROOT = Path(__file__).resolve().parents[2]
TUNING = Tuning.from_file(REPO_ROOT).draft
US, THEM = "agent_us", "agent_them"
RANDOM_DRAFTS = 10
POOL_SIZE = 18
SCORING_LIMIT_SECONDS = 0.5


def card(card_id: str, species: str, moves: list[str], item: str = "Leftovers", ability: str = "Pressure") -> dict:
    """A draft card as `available_cards` lists it, with a common neutral spread."""
    return {
        "card_id": card_id,
        "species": species,
        "item": item,
        "ability": ability,
        "nature": "Serious",
        "level": 50,
        "evs": {"hp": 252, "atk": 252, "spa": 4},
        "ivs": {},
        "moves": moves,
    }


def observation(available: list[dict], rosters: dict[str, list[dict]] | None = None, **extra: Any) -> dict:
    """A draft observation at our pick: `rosters` maps a seat to the cards it holds."""
    rosters = rosters or {US: [], THEM: []}
    return {
        "phase": "draft",
        "current_seat": US,
        "available_card_ids": [entry["card_id"] for entry in available],
        "available_cards": available,
        "rosters": {
            seat: [{"card_id": entry["card_id"], "species": entry["species"]} for entry in held]
            for seat, held in rosters.items()
        },
        "picks": [],
        **extra,
    }


def remembered(*observations: dict) -> DraftMemory:
    memory = DraftMemory()
    for seen in observations:
        memory.update(seen, US, Reader())
    return memory


def best(memory: DraftMemory, offered: list[str] | None = None):
    return score_cards(memory, offered if offered is not None else memory.available, TUNING)[0]


# A pool of weak Normal-type cards with Normal moves: neutral to every type in the tests below.
NORMAL_POOL = [
    card("vgc-snorlax", "Snorlax", ["Body Slam"]),
    card("vgc-kangaskhan", "Kangaskhan", ["Body Slam"]),
    card("vgc-tauros", "Tauros", ["Body Slam"]),
    card("vgc-rattata", "Rattata", ["Tackle"]),
    card("vgc-pidgey", "Pidgey", ["Tackle"]),
    card("vgc-sentret", "Sentret", ["Tackle"]),
]


# -- (1) the pick is always an offered card of the pool --------------------------------------------


def _practice_states(seed: int):
    """Each of our picks in a practice draft whose other seat picks at random: the observation and legal actions."""
    draft = PracticeDraft(seed, load_catalog(), (US, THEM))
    rng = random.Random(seed)
    while not draft.is_complete:
        seat = draft.current_seat
        legal = [LegalAction.from_dict(action) for action in draft.legal_actions(seat, "s", 0)]
        if seat == US:
            yield draft.observation(), legal
            draft.pick(US, offered_card_ids(legal)[0])
        else:
            draft.pick(THEM, rng.choice(offered_card_ids(legal)))


def test_1_a_pick_is_always_one_of_the_available_card_ids_and_offered():
    picks = 0
    for seed in range(RANDOM_DRAFTS):
        memory = DraftMemory()
        for seen, legal in _practice_states(seed):
            memory.update(seen, US, Reader())
            offered = offered_card_ids(legal)

            chosen = best(memory, offered)

            assert chosen.card_id in seen["available_card_ids"] and chosen.card_id in offered
            picks += 1
    assert picks == RANDOM_DRAFTS * 6


def test_an_offered_card_outside_the_pool_is_never_scored():
    memory = remembered(observation(NORMAL_POOL))

    scored = score_cards(memory, ["vgc-not-in-pool", "vgc-rattata"], TUNING)

    assert [score.card_id for score in scored] == ["vgc-rattata"]
    assert score_cards(memory, ["vgc-not-in-pool"], TUNING) == []


# -- (2) a card that outclasses the pool -----------------------------------------------------------


def test_2_with_an_empty_roster_the_card_that_outclasses_the_rest_in_power_and_bulk_is_picked():
    strong = card("vgc-kingambit", "Kingambit", ["Kowtow Cleave", "Iron Head"])
    memory = remembered(observation([*NORMAL_POOL[3:], strong]))

    chosen = best(memory)
    weak = [score for score in score_cards(memory, memory.available, TUNING) if score.card_id != chosen.card_id]

    assert chosen.card_id == "vgc-kingambit"
    assert all(chosen.components[POWER] > score.components[POWER] for score in weak)
    assert all(chosen.components[BULK] > score.components[BULK] for score in weak)
    assert pick_action(chosen) == {"type": "draft_pick", "card_id": "vgc-kingambit"}


# -- (3) synergy -----------------------------------------------------------------------------------


def test_3_with_three_fire_weak_pokemon_a_water_card_is_preferred_over_an_otherwise_equal_grass_card():
    roster = [
        card("vgc-scizor", "Scizor", ["Bullet Punch"]),
        card("vgc-ferrothorn", "Ferrothorn", ["Power Whip"]),
        card("vgc-venusaur", "Venusaur", ["Sludge Bomb"]),
    ]
    water = card("vgc-rotom-wash", "Rotom-Wash", ["Waterfall"], ability="Levitate")
    grass = card("vgc-rotom-mow", "Rotom-Mow", ["Seed Bomb"], ability="Levitate")
    memory = remembered(
        observation([*roster, *NORMAL_POOL, water, grass]),
        observation([*NORMAL_POOL, water, grass], {US: roster, THEM: []}),
    )

    scores = {score.card_id: score for score in score_cards(memory, ["vgc-rotom-mow", "vgc-rotom-wash"], TUNING)}

    assert best(memory, ["vgc-rotom-mow", "vgc-rotom-wash"]).card_id == "vgc-rotom-wash"
    assert scores["vgc-rotom-wash"].synergy_note == "covers our Fire weakness"
    assert scores["vgc-rotom-wash"].components["synergy"] > scores["vgc-rotom-mow"].components["synergy"]


# -- (4) memory ------------------------------------------------------------------------------------


def test_4_the_opponent_s_roster_and_every_seen_card_s_full_set_are_remembered_across_observations():
    taken = card("vgc-incineroar", "Incineroar", ["Fake Out", "Flare Blitz"], "Assault Vest", "Intimidate")
    first = observation([taken, *NORMAL_POOL])
    later = observation(NORMAL_POOL[1:], {US: [NORMAL_POOL[0]], THEM: [taken]})

    memory = remembered(first, later)

    assert memory.roster_sets(THEIRS) == [sets.from_card(taken)]
    assert memory.roster_sets(OURS) == [sets.from_card(NORMAL_POOL[0])]
    assert set(memory.cards) == {entry["card_id"] for entry in [taken, *NORMAL_POOL]}
    assert memory.set_for_species(THEIRS, "incineroar").item == "assaultvest"
    assert memory.available == [entry["card_id"] for entry in NORMAL_POOL[1:]]


def test_a_card_taken_before_we_saw_the_pool_stands_as_its_species_facts():
    memory = remembered(observation(NORMAL_POOL, {US: [], THEM: [card("vgc-gyarados", "Gyarados", ["Waterfall"])]}))

    (their_card,) = memory.roster_sets(THEIRS)

    assert their_card == sets.from_facts("gyarados") and their_card.moves == ()


def test_without_rosters_the_rosters_come_from_the_picks_and_count_as_a_degradation():
    seen = observation(NORMAL_POOL)
    del seen["rosters"]
    seen["picks"] = [{"pick_number": 1, "seat_id": THEM, "card_id": "vgc-gyarados", "species": "Gyarados"}]
    reader = Reader()

    memory = DraftMemory()
    memory.update(seen, US, reader)

    assert [entry.species for entry in memory.roster_sets(THEIRS)] == ["gyarados"]
    assert reader.degraded_keys == ["rosters"]


def test_a_forme_seen_in_battle_finds_its_drafted_card():
    terapagos = card("vgc-terapagos", "Terapagos", ["Tera Starstorm"])
    memory = remembered(observation([terapagos, *NORMAL_POOL]), observation(NORMAL_POOL, {US: [terapagos]}))

    assert memory.set_for_species(OURS, "terapagosterastal") == sets.from_card(terapagos)
    assert memory.set_for_species(THEIRS, "terapagos") is None


def test_our_seat_defaults_to_the_seat_whose_pick_it_is():
    memory = DraftMemory()

    memory.update(observation(NORMAL_POOL), None, Reader())
    memory.update(observation(NORMAL_POOL[1:], {US: [NORMAL_POOL[0]], THEM: []}), None, Reader())

    assert memory.our_seat == US and memory.roster_sets(OURS) == [sets.from_card(NORMAL_POOL[0])]


# -- (5) the reason --------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(3))
def test_5_the_reason_names_the_two_largest_components_in_at_most_120_characters(seed):
    for seen, legal in _practice_states(seed):
        chosen = best(remembered(seen), offered_card_ids(legal))
        reason = chosen.reason()

        top = sorted(chosen.components, key=chosen.components.get, reverse=True)[:2]
        assert chosen.top_components() == top
        assert all(chosen.phrase(name) in reason for name in top)
        assert len(reason) <= 120 and reason.endswith(".")


def test_a_card_the_item_clause_keeps_from_the_opponent_has_no_denial():
    held = card("vgc-held", "Tauros", ["Body Slam"], item="Choice Band")
    banded = card("vgc-banded", "Kangaskhan", ["Body Slam"], item="Choice Band")
    free = card("vgc-free", "Snorlax", ["Body Slam"], item="Sitrus Berry")
    memory = remembered(observation([held, banded, free]), observation([banded, free], {US: [], THEM: [held]}))

    scores = {score.card_id: score for score in score_cards(memory, ["vgc-banded", "vgc-free"], TUNING)}

    assert scores["vgc-banded"].components[DENIAL] == 0.0 and scores["vgc-free"].components[DENIAL] > 0


def test_the_description_names_the_set_and_its_score():
    memory = remembered(observation([card("vgc-snorlax", "Snorlax", ["Body Slam", "Protect"]), *NORMAL_POOL[1:]]))

    text = best(memory, ["vgc-snorlax"]).description()

    assert text.startswith("Snorlax @ leftovers, pressure; Body Slam/Protect; score ")
    assert "power" in text and "denial" in text


def test_offered_ids_come_from_the_action_or_its_id():
    actions = [
        LegalAction("draft_pick:vgc-a", "A", {"action": {"type": "draft_pick", "card_id": "vgc-a"}}),
        LegalAction("draft_pick:vgc-b", "B", {}),
        LegalAction("something", "C", {}),
    ]

    assert offered_card_ids(actions) == ["vgc-a", "vgc-b"]


# -- (6) timing ------------------------------------------------------------------------------------


def test_6_scoring_an_18_card_pool_takes_under_500_ms():
    catalog = load_catalog()
    pool = [card_entry.draft_entry() for card_entry in list(catalog.cards.values())[:POOL_SIZE]]
    memory = remembered(observation(pool))
    sets.data.shipped()
    sets.best_damage.cache_clear()

    started = time.perf_counter()
    scored = score_cards(memory, memory.available, TUNING)
    elapsed = time.perf_counter() - started

    assert len(scored) == POOL_SIZE and elapsed < SCORING_LIMIT_SECONDS


# -- maximin: one pick ahead -----------------------------------------------------------------------

KINGAMBIT = card("vgc-kingambit", "Kingambit", ["Kowtow Cleave", "Iron Head"], item="Black Glasses")
# Items none of the pool's cards hold, so the Item Clause keeps no card from either side.
THEIR_TWO = [
    card("vgc-gyarados", "Gyarados", ["Waterfall"], item="Sitrus Berry"),
    card("vgc-arcanine", "Arcanine", ["Flare Blitz"], item="Charcoal"),
]
OUR_TWO = [
    card("vgc-milotic", "Milotic", ["Scald"], item="Mystic Water"),
    card("vgc-togekiss", "Togekiss", ["Air Slash"], item="Scope Lens"),
]


def _two_each() -> DraftMemory:
    """Two picks made each, Kingambit and the weak Normal cards left in the pool."""
    pool = [*NORMAL_POOL, KINGAMBIT]
    return remembered(observation([*OUR_TWO, *THEIR_TWO, *pool]), observation(pool, {US: OUR_TWO, THEM: THEIR_TWO}))


def _later() -> float:
    return time.perf_counter() + 10


def test_maximin_3_the_card_the_opponent_would_most_want_next_scores_higher_with_lookahead_when_it_fits_us():
    memory = _two_each()

    without = {score.card_id: score for score in score_cards(memory, memory.available, TUNING)}
    looked = {score.card_id: score for score in score_cards_lookahead(memory, memory.available, TUNING, _later())}

    wanted = looked["vgc-snorlax"].their_next
    assert wanted is not None and wanted.card_id == "vgc-kingambit"
    assert looked["vgc-kingambit"].total > without["vgc-kingambit"].total
    # The gap from their best to their second best; taking Snorlax first moves their best a little (its denial of
    # us changes with our roster), hence the tolerance.
    assert looked["vgc-kingambit"].components[LOOKAHEAD] == pytest.approx(
        wanted.score - looked["vgc-kingambit"].their_next.score, abs=1.0
    )
    assert looked["vgc-kingambit"].their_next.card_id != "vgc-kingambit"
    assert score_cards_lookahead(memory, memory.available, TUNING, _later())[0].card_id == "vgc-kingambit"
    others = [score.components[LOOKAHEAD] for card_id, score in looked.items() if card_id != "vgc-kingambit"]
    assert max(abs(gain) for gain in others) < looked["vgc-kingambit"].components[LOOKAHEAD]


def test_maximin_a_card_the_item_clause_keeps_from_the_opponent_is_never_their_next_pick():
    pool = [*NORMAL_POOL, KINGAMBIT]
    banded = [THEIR_TWO[0], {**THEIR_TWO[1], "item": "Black Glasses"}]
    memory = remembered(observation([*OUR_TWO, *banded, *pool]), observation(pool, {US: OUR_TWO, THEM: banded}))

    looked = score_cards_lookahead(memory, memory.available, TUNING, _later())

    assert all(score.their_next.card_id != "vgc-kingambit" for score in looked)


def test_maximin_with_no_pick_left_to_the_opponent_the_lookahead_adds_nothing():
    theirs = [card(f"vgc-their-{number}", "Rattata", ["Tackle"], item=f"Item {number}") for number in range(6)]
    pool = [*NORMAL_POOL[:3], KINGAMBIT]
    memory = remembered(observation([*theirs, *pool]), observation(pool, {US: [], THEM: theirs}))

    looked = score_cards_lookahead(memory, memory.available, TUNING, _later())

    assert looked == score_cards(memory, memory.available, TUNING)
    assert all(LOOKAHEAD not in score.components and score.their_next is None for score in looked)


def test_maximin_the_lookahead_gives_nothing_past_its_time_limit():
    assert score_cards_lookahead(_two_each(), ["vgc-kingambit"], TUNING, time.perf_counter() - 1) is None


def test_maximin_the_reason_may_name_the_lookahead_in_at_most_120_characters():
    memory = _two_each()

    picked = score_cards_lookahead(memory, memory.available, TUNING, _later())[0]
    named = dataclasses.replace(picked, components={**picked.components, LOOKAHEAD: 1000.0})

    assert "leaves the opponent a weaker next pick" in named.reason() and len(named.reason()) <= 120


def test_maximin_scoring_an_18_card_pool_one_pick_ahead_takes_under_500_ms():
    catalog = load_catalog()
    pool = [card_entry.draft_entry() for card_entry in list(catalog.cards.values())[:POOL_SIZE]]
    memory = remembered(observation(pool))
    sets.data.shipped()
    sets.best_damage.cache_clear()

    started = time.perf_counter()
    scored = score_cards_lookahead(memory, memory.available, TUNING, _later())
    elapsed = time.perf_counter() - started

    assert len(scored) == POOL_SIZE and all(score.their_next is not None for score in scored)
    assert elapsed < SCORING_LIMIT_SECONDS
