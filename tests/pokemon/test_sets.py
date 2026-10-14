"""Known sets: from a draft card or observation facts, their stats, and the best damage one set does to another."""

from __future__ import annotations

import pytest

from ucla_agent.games.pokemon import damage, data
from ucla_agent.games.pokemon.sets import (
    ASSUMED_EVS_PER_STAT,
    PokemonSet,
    best_damage,
    current_hp,
    from_card,
    from_facts,
    is_damaging,
    type_effect,
)

# The guide's card, as `available_cards` lists it (pokemon.md, section 4).
URSHIFU_CARD = {
    "card_id": "vgc-urshifu-rapid-strike",
    "species": "Urshifu-Rapid-Strike",
    "item": "Mystic Water",
    "ability": "Unseen Fist",
    "nature": "Adamant",
    "level": 50,
    "evs": {"hp": 4, "atk": 252, "spe": 252},
    "ivs": {},
    "moves": ["Surging Strikes", "Close Combat", "Aqua Jet", "Protect"],
}
INCINEROAR_CARD = {
    "card_id": "vgc-incineroar",
    "species": "Incineroar",
    "item": "Assault Vest",
    "ability": "Intimidate",
    "nature": "Impish",
    "level": 50,
    "evs": {"hp": 252, "def": 252, "spd": 4},
    "ivs": {},
    "moves": ["Fake Out", "Knock Off", "Flare Blitz", "Parting Shot"],
}


def test_a_draft_card_becomes_a_set_of_showdown_ids():
    urshifu = from_card(URSHIFU_CARD)

    assert urshifu == PokemonSet(
        species="urshifurapidstrike",
        item="mysticwater",
        ability="unseenfist",
        nature="Adamant",
        level=50,
        evs=(("atk", 252), ("hp", 4), ("spe", 252)),
        ivs=(),
        moves=("surgingstrikes", "closecombat", "aquajet", "protect"),
    )
    assert hash(urshifu) == hash(from_card(dict(URSHIFU_CARD)))


@pytest.mark.parametrize(
    "entry",
    [None, "vgc-urshifu", {"species": "Notamon"}, {"species": 7}, {"card_id": "x"}],
    ids=["none", "a string", "unknown species", "species not a string", "no species"],
)
def test_a_card_naming_no_known_species_gives_no_set(entry):
    assert from_card(entry) is None


def test_stats_at_level_50_match_what_showdown_shows_for_the_card():
    incineroar = from_card(INCINEROAR_CARD)

    # Showdown's own request for this card shows 202 HP and these stats (showdown_p1_view.json).
    assert incineroar.max_hp() == 202
    assert [incineroar.stat(name) for name in ("atk", "def", "spa", "spd", "spe")] == [135, 156, 90, 111, 80]


def test_unusable_card_fields_take_the_assumptions_and_unknown_moves_are_left_out():
    odd = from_facts("Incineroar", item=3, ability=None, moves=["Fake Out", "Not A Move", "Fake Out"], level=True)

    assert (odd.item, odd.ability, odd.nature, odd.level) == ("", "", "Serious", 50)
    assert odd.evs == tuple((stat, ASSUMED_EVS_PER_STAT) for stat in data.STATS)
    assert odd.moves == ("fakeout",)


def test_a_set_with_no_known_move_attacks_with_one_assumed_move_per_type_by_its_better_attacking_stat():
    urshifu = from_facts("urshifurapidstrike")
    flutter = from_facts("Flutter Mane")

    assert [(move.type, move.category, move.base_power) for move in urshifu.move_records()] == [
        ("Fighting", "Physical", 80),
        ("Water", "Physical", 80),
    ]
    assert [move.category for move in flutter.move_records()] == ["Special", "Special"]


def test_a_battle_mon_carries_the_share_of_hp_status_and_stages():
    incineroar = from_card(INCINEROAR_CARD)

    mon = incineroar.battle_mon(0.5, "brn", {"atk": -1})

    assert (mon.current_hp, mon.status, dict(mon.boosts), mon.item, mon.ability) == (
        101,
        "brn",
        {"atk": -1},
        "assaultvest",
        "intimidate",
    )
    assert incineroar.battle_mon().current_hp is None
    assert (current_hp(202, 0.001), current_hp(202, 0.0)) == (1, 0)


def test_best_damage_is_the_highest_max_pct_of_the_attacker_s_moves_capped_at_100():
    urshifu, incineroar = from_card(URSHIFU_CARD), from_card(INCINEROAR_CARD)
    expected = max(
        damage.estimate(urshifu.battle_mon(), move, incineroar.battle_mon()).max_pct
        for move in urshifu.move_records()
        if is_damaging(move)
    )

    assert best_damage(urshifu, incineroar) == pytest.approx(min(100.0, expected))
    assert best_damage(from_facts("Pikachu", moves=["Protect"]), incineroar) == 0.0
    assert best_damage(from_facts("Calyrex-Shadow", moves=["Astral Barrage"]), from_facts("Pikachu")) == 100.0


def test_type_effect_reads_types_and_an_immunity_ability():
    rotom = from_facts("Rotom-Wash", ability="Levitate")

    assert type_effect("Ground", rotom) == 0.0
    assert type_effect("Grass", rotom) == 2.0
    assert type_effect("Normal", rotom) == 1.0
    assert type_effect("Fire", from_facts("Scizor")) == 4.0
    assert type_effect("Fire", from_facts("Rotom-Wash")) == 0.5
