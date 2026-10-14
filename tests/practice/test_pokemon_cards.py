"""The Pokémon practice card catalog: the build script over made-up statistics, and the tracked catalog file.

The build script's dex lookups (Showdown's, through Node) are replaced here
by a small table, so these tests need no Node; the marked test in
`test_pokemon_sim.py` runs the real build.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pytest

from ucla_agent.practice.pokemon.cards import CardCatalog, CatalogError, MoveFacts, load_catalog, to_id

BUILD_SCRIPT = "build_pokemon_cards.py"

SPECIES = {
    "Incineroar": {"num": 727, "types": ["Fire", "Dark"], "formes": ["Incineroar"]},
    "Urshifu-Rapid-Strike": {"num": 892, "types": ["Fighting", "Water"], "formes": ["Urshifu", "Urshifu-Rapid-Strike"]},
    "Urshifu": {"num": 892, "types": ["Fighting", "Dark"], "formes": ["Urshifu", "Urshifu-Rapid-Strike"]},
    "Zoroark-Hisui": {"num": 571, "types": ["Normal", "Ghost"], "formes": ["Zoroark", "Zoroark-Hisui"]},
    "Amoonguss": {"num": 591, "types": ["Grass", "Poison"], "formes": ["Amoonguss"]},
    "Gyarados": {"num": 130, "types": ["Water", "Flying"], "formes": ["Gyarados"]},
}
MOVES = {
    "fakeout": ("Fake Out", "Normal", "Physical", 40, 100, 3, "normal"),
    "knockoff": ("Knock Off", "Dark", "Physical", 65, 100, 0, "normal"),
    "flareblitz": ("Flare Blitz", "Fire", "Physical", 120, 100, 0, "normal"),
    "partingshot": ("Parting Shot", "Dark", "Status", 0, 100, 0, "normal"),
    "surgingstrikes": ("Surging Strikes", "Water", "Physical", 25, 100, 0, "normal"),
    "closecombat": ("Close Combat", "Fighting", "Physical", 120, 100, 0, "normal"),
    "aquajet": ("Aqua Jet", "Water", "Physical", 40, 100, 1, "normal"),
    "protect": ("Protect", "Normal", "Status", 0, True, 4, "self"),
    "wickedblow": ("Wicked Blow", "Dark", "Physical", 75, 100, 0, "normal"),
    "spore": ("Spore", "Grass", "Status", 0, 100, 0, "normal"),
    "ragepowder": ("Rage Powder", "Bug", "Status", 0, True, 2, "self"),
    "pollenpuff": ("Pollen Puff", "Bug", "Special", 90, 100, 0, "normal"),
    "hypervoice": ("Hyper Voice", "Normal", "Special", 90, 100, 0, "allAdjacentFoes"),
    "bittermalice": ("Bitter Malice", "Ghost", "Special", 75, 100, 0, "normal"),
    "shadowball": ("Shadow Ball", "Ghost", "Special", 80, 100, 0, "normal"),
    "snarl": ("Snarl", "Dark", "Special", 55, 95, 0, "allAdjacentFoes"),
    "waterfall": ("Waterfall", "Water", "Physical", 80, 100, 0, "normal"),
    "dragondance": ("Dragon Dance", "Dragon", "Status", 0, True, 0, "self"),
    "icefang": ("Ice Fang", "Ice", "Physical", 65, 95, 0, "normal"),
    "struggle": ("Struggle", "Normal", "Physical", 50, True, 0, "randomNormal"),
}
ITEM_NAMES = {"assaultvest": "Assault Vest", "choicescarf": "Choice Scarf", "focussash": "Focus Sash"}
ABILITY_NAMES = {
    "intimidate": "Intimidate",
    "unseenfist": "Unseen Fist",
    "illusion": "Illusion",
    "regenerator": "Regenerator",
    "moxie": "Moxie",
}
# Sets the made-up validator refuses, by species.
REFUSED_SPECIES = {"Gyarados"}


def usage_entry(usage: float, item: str, ability: str, spread: str, moves: dict[str, float]) -> dict[str, Any]:
    return {
        "usage": usage,
        "Items": {item: 10.0, "nothing": 99.0},
        "Abilities": {ability: 10.0},
        "Spreads": {spread: 5.0, "Hardy:0/0/0/0/0/0": 1.0},
        "Moves": {**moves, "": 1000.0},
    }


STATS = {
    "info": {"metagame": "gen9vgc2025regi", "cutoff": 1630.0, "number of battles": 1234},
    "data": {
        "Incineroar": usage_entry(
            0.5,
            "assaultvest",
            "intimidate",
            "Impish:252/0/252/0/4/0",
            {"fakeout": 9.0, "knockoff": 8.0, "flareblitz": 7.0, "partingshot": 6.0, "protect": 1.0},
        ),
        "Urshifu-Rapid-Strike": usage_entry(
            0.4,
            "choicescarf",
            "unseenfist",
            "Adamant:4/252/0/0/0/252",
            {"surgingstrikes": 9.0, "closecombat": 8.0, "aquajet": 8.0, "protect": 7.0},
        ),
        "Urshifu": usage_entry(
            0.35,
            "focussash",
            "unseenfist",
            "Jolly:4/252/0/0/0/252",
            {"wickedblow": 9.0, "closecombat": 8.0, "aquajet": 8.0, "protect": 7.0},
        ),
        "Zoroark-Hisui": usage_entry(
            0.3,
            "focussash",
            "illusion",
            "Timid:0/0/0/252/4/252",
            {"hypervoice": 9.0, "bittermalice": 8.0, "shadowball": 7.0, "snarl": 6.0},
        ),
        "Gyarados": usage_entry(
            0.25,
            "assaultvest",
            "moxie",
            "Adamant:4/252/0/0/0/252",
            {"waterfall": 9.0, "dragondance": 8.0, "icefang": 7.0, "protect": 6.0},
        ),
        "Amoonguss": usage_entry(
            0.2,
            "focussash",
            "regenerator",
            "Bold:236/0/236/0/36/0",
            {"spore": 9.0, "ragepowder": 8.0, "protect": 7.0, "pollenpuff": 6.0},
        ),
    },
}


def species_answer(name: str) -> dict[str, Any]:
    if name not in SPECIES:
        return {"exists": False}
    facts = SPECIES[name]
    return {
        "exists": True,
        "name": name,
        "id": name.lower().replace("-", ""),
        "num": facts["num"],
        "types": facts["types"],
        "base_stats": {"hp": 100, "atk": 100, "def": 100, "spa": 100, "spd": 100, "spe": 100},
        "formes": facts["formes"],
    }


def move_answer(name: str) -> dict[str, Any]:
    move_id = name.lower().replace(" ", "").replace("-", "")
    if move_id not in MOVES:
        return {"exists": False}
    display, kind, category, power, accuracy, priority, target = MOVES[move_id]
    return {
        "exists": True,
        "name": display,
        "id": move_id,
        "type": kind,
        "category": category,
        "base_power": power,
        "accuracy": accuracy,
        "priority": priority,
        "target": target,
    }


def table_dex(query: dict[str, Any]) -> dict[str, Any]:
    """Answers a dex.js query from the tables above."""

    def named(table: dict[str, str]) -> Callable[[str], dict[str, Any]]:
        return lambda key: {"exists": True, "name": table[key], "id": key} if key in table else {"exists": False}

    def nature(name: str) -> dict[str, Any]:
        return {"exists": True, "name": name, "id": name.lower()}

    return {
        "species": {name: species_answer(name) for name in query.get("species", [])},
        "moves": {name: move_answer(name) for name in query.get("moves", [])},
        "items": {name: named(ITEM_NAMES)(name) for name in query.get("items", [])},
        "abilities": {name: named(ABILITY_NAMES)(name) for name in query.get("abilities", [])},
        "natures": {name: nature(name) for name in query.get("natures", [])},
        "problems": [
            ["refused"] if card_set["species"] in REFUSED_SPECIES or "ivs" in card_set else None
            for card_set in query.get("sets", [])
        ],
    }


@pytest.fixture(scope="module")
def build(load_script: Callable[[str], ModuleType]) -> ModuleType:
    return load_script(BUILD_SCRIPT)


# -- the build script ------------------------------------------------------------------------------


def test_a_usage_table_s_most_used_entries_leave_out_empty_slots_and_break_ties_by_name(build):
    weights = {"": 50.0, "nothing": 40.0, "b": 3.0, "a": 3.0, "c": 9.0}

    assert build.most_used(weights) == ["c"]
    assert build.most_used(weights, 3) == ["c", "a", "b"]


def test_a_spread_gives_its_nature_and_its_non_zero_evs(build):
    assert build.spread_of("Impish:252/0/252/0/4/0") == ("Impish", {"hp": 252, "def": 252, "spd": 4})


@pytest.mark.parametrize(
    ("species", "slug"),
    [("Urshifu-Rapid-Strike", "urshifu-rapid-strike"), ("Flutter Mane", "flutter-mane"), ("Mr. Rime", "mr-rime")],
)
def test_a_card_slug_is_the_species_name_in_lower_case_with_hyphens(build, species, slug):
    assert build.card_slug(species) == slug


def test_cards_take_each_species_most_used_set_in_usage_order(build):
    cards = build.build_catalog(STATS, table_dex)["cards"]

    assert cards[0] == {
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
    assert cards[1]["moves"] == ["Surging Strikes", "Aqua Jet", "Close Combat", "Protect"]


def test_cards_leave_out_a_second_base_species_an_unfollowed_ability_and_a_refused_set(build):
    card_ids = [card["card_id"] for card in build.build_catalog(STATS, table_dex)["cards"]]

    # Urshifu shares Urshifu-Rapid-Strike's dex number, Zoroark-Hisui has Illusion, Gyarados is refused.
    assert card_ids == ["vgc-incineroar", "vgc-urshifu-rapid-strike", "vgc-amoonguss"]


def test_cards_stop_at_the_count_asked_for(build):
    assert len(build.build_catalog(STATS, table_dex, count=2)["cards"]) == 2


def test_species_facts_cover_every_forme_of_each_card_species_in_upper_case_types(build):
    species = build.build_catalog(STATS, table_dex)["species"]

    assert sorted(species) == ["amoonguss", "incineroar", "urshifu", "urshifurapidstrike"]
    assert species["urshifurapidstrike"]["types"] == ["FIGHTING", "WATER"]
    assert species["urshifurapidstrike"]["name"] == "Urshifu-Rapid-Strike"


def test_move_facts_cover_every_card_move_struggle_and_recharge_with_accuracy_as_a_fraction(build):
    moves = build.build_catalog(STATS, table_dex)["moves"]

    assert moves["fakeout"] == {
        "name": "Fake Out",
        "type": "NORMAL",
        "category": "PHYSICAL",
        "base_power": 40,
        "accuracy": 1.0,
        "priority": 3,
        "target": "normal",
    }
    assert moves["protect"]["accuracy"] == 1.0 and moves["protect"]["target"] == "self"
    assert moves["struggle"]["target"] == "randomNormal"
    assert moves["recharge"]["category"] == "STATUS"
    assert "wickedblow" not in moves and "waterfall" not in moves


def test_the_catalog_names_its_source_and_its_text_is_stable(build):
    catalog = build.build_catalog(STATS, table_dex)

    assert catalog["format"] == "gen9vgc2025regi"
    assert catalog["source"]["statistics"] == "Smogon usage statistics gen9vgc2025regi-1630"
    assert catalog["source"]["battles"] == 1234
    text = build.catalog_text(catalog)
    assert text.endswith("}\n") and json.loads(text) == catalog
    assert text == build.catalog_text(build.build_catalog(STATS, table_dex))


# -- the tracked catalog and its loader ------------------------------------------------------------

GUIDE_CARD_FIELDS = ["card_id", "species", "item", "ability", "nature", "level", "evs", "ivs", "moves"]


@pytest.fixture(scope="module")
def catalog() -> CardCatalog:
    return load_catalog()


def test_the_tracked_catalog_holds_at_least_60_level_50_cards_with_four_moves_and_vgc_ids(catalog):
    cards = list(catalog.cards.values())

    assert len(cards) >= 60
    assert catalog.battle_format == "gen9vgc2025regi"
    assert all(re.fullmatch(r"vgc-[a-z0-9]+(-[a-z0-9]+)*", card.card_id) for card in cards)
    assert all(card.level == 50 and len(card.moves) == 4 for card in cards)
    assert catalog.card("vgc-urshifu-rapid-strike").species == "Urshifu-Rapid-Strike"


def test_every_card_species_and_move_has_its_facts(catalog):
    for card in catalog.cards.values():
        assert card.species_id in catalog.species
        for move in card.moves:
            assert to_id(move) in catalog.moves
    urshifu = catalog.species["urshifurapidstrike"]
    assert urshifu.types == ("FIGHTING", "WATER") and urshifu.base_stats["spe"] == 97
    assert catalog.moves["struggle"].target == "randomNormal"
    assert catalog.moves["recharge"].category == "STATUS"
    assert catalog.moves["fakeout"] == MoveFacts("Fake Out", "NORMAL", "PHYSICAL", 40, 1.0, 3, "normal")


def test_a_card_s_draft_entry_has_exactly_the_guide_s_card_fields(catalog):
    entry = catalog.card("vgc-incineroar").draft_entry()

    assert list(entry) == GUIDE_CARD_FIELDS
    assert entry["level"] == 50 and entry["ivs"] == {} and len(entry["moves"]) == 4


def test_a_card_s_showdown_set_has_no_nickname_and_the_card_s_set(catalog):
    card = catalog.card("vgc-incineroar")

    expected = {key: value for key, value in card.draft_entry().items() if key != "card_id"}
    assert card.showdown_set() == {"name": "", **expected}


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Urshifu-Rapid-Strike", "urshifurapidstrike"), ("Flutter Mane", "fluttermane"), ("Mr. Rime", "mrrime")],
)
def test_a_showdown_id_keeps_lower_case_letters_and_digits(name, expected):
    assert to_id(name) == expected


def write_catalog(tmp_path: Path, cards: list[dict[str, Any]]) -> Path:
    path = tmp_path / "practice_cards.json"
    path.write_text(json.dumps({"format": "gen9vgc2025regi", "cards": cards, "species": {}, "moves": {}}))
    return path


def a_card(number: int, **changes: Any) -> dict[str, Any]:
    card = {
        "card_id": f"vgc-card-{number}",
        "species": f"Card {number}",
        "item": "Leftovers",
        "ability": "Pressure",
        "nature": "Bold",
        "level": 50,
        "evs": {},
        "ivs": {},
        "moves": ["Protect", "Tackle", "Growl", "Splash"],
    }
    return {**card, **changes}


def test_a_catalog_of_60_good_cards_loads(tmp_path):
    loaded = load_catalog(write_catalog(tmp_path, [a_card(number) for number in range(60)]))

    assert list(loaded.cards)[:2] == ["vgc-card-0", "vgc-card-1"]


@pytest.mark.parametrize(
    ("cards", "message"),
    [
        ([a_card(number) for number in range(59)], "fewer than 60"),
        ([a_card(number) for number in range(60)] + [a_card(0)], "more than once"),
        ([a_card(number) for number in range(59)] + [a_card(59, level=100)], "level 50"),
        ([a_card(number) for number in range(59)] + [a_card(59, moves=["Protect"])], "4 moves"),
    ],
)
def test_a_catalog_that_breaks_a_rule_is_refused(tmp_path, cards, message):
    with pytest.raises(CatalogError, match=message):
        load_catalog(write_catalog(tmp_path, cards))
