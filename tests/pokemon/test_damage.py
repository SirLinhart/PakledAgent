"""The damage estimate, against reference damage the Smogon damage calculator computed for the same cases."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ucla_agent.games.pokemon import data
from ucla_agent.games.pokemon.damage import BattleMon, DamageRange, Field, chain, estimate, poke_round

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "damage_reference.json"
REFERENCE = json.loads(FIXTURE.read_text(encoding="utf-8"))
# How far, in HP, an estimate's min or max may be from the calculator's.
TOLERANCE_HP = 2
REFERENCE_CASE_COUNT = 15
ESTIMATE_COUNT = 10_000
ESTIMATES_BUDGET_SECONDS = 1.0

URSHIFU = BattleMon("Urshifu-Rapid-Strike", nature="Adamant", evs={"atk": 252, "spe": 252, "hp": 4})
INCINEROAR = BattleMon("Incineroar", nature="Careful", evs={"hp": 252, "def": 4, "spd": 252})
FLUTTER_MANE = BattleMon("Flutter Mane", nature="Timid", evs={"spa": 252, "spe": 252, "hp": 4})


def _mon(reference_set: dict) -> BattleMon:
    return BattleMon(**reference_set)


def _estimate(case: dict) -> DamageRange:
    return estimate(_mon(case["attacker"]), case["move"], _mon(case["defender"]), Field(**case["field"]))


def _ids(cases: list[dict]) -> list[str]:
    return [case["name"] for case in cases]


# -- the reference cases ---------------------------------------------------------------------------


def test_the_fixture_records_the_calculator_and_its_version_and_holds_15_cases():
    assert REFERENCE["calculator"] == "@smogon/calc" and REFERENCE["calc_version"] == "0.12.0"
    assert len(REFERENCE["cases"]) == REFERENCE_CASE_COUNT


@pytest.mark.parametrize("case", REFERENCE["cases"], ids=_ids(REFERENCE["cases"]))
def test_each_reference_case_is_within_2_hp_of_the_calculator(case):
    result = _estimate(case)

    assert abs(result.min_damage - case["expected"]["min"]) <= TOLERANCE_HP
    assert abs(result.max_damage - case["expected"]["max"]) <= TOLERANCE_HP


@pytest.mark.parametrize("case", REFERENCE["modifier_cases"], ids=_ids(REFERENCE["modifier_cases"]))
def test_each_other_modeled_modifier_is_within_2_hp_of_the_calculator(case):
    result = _estimate(case)

    assert abs(result.min_damage - case["expected"]["min"]) <= TOLERANCE_HP
    assert abs(result.max_damage - case["expected"]["max"]) <= TOLERANCE_HP


@pytest.mark.parametrize("case", REFERENCE["cases"], ids=_ids(REFERENCE["cases"]))
def test_percentages_are_of_the_defenders_max_hp_as_the_calculator_computes_it(case):
    result = _estimate(case)
    max_hp = case["expected"]["defender_max_hp"]

    assert result.min_pct == pytest.approx(100 * result.min_damage / max_hp)
    assert result.max_pct == pytest.approx(100 * result.max_damage / max_hp)


def test_10000_estimates_take_under_a_second():
    cases = [
        (_mon(case["attacker"]), case["move"], _mon(case["defender"]), Field(**case["field"]))
        for case in REFERENCE["cases"]
    ]
    data.shipped()

    started = time.perf_counter()
    for index in range(ESTIMATE_COUNT):
        estimate(*cases[index % len(cases)])
    elapsed = time.perf_counter() - started

    assert elapsed < ESTIMATES_BUDGET_SECONDS


# -- the invariants --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case", REFERENCE["cases"] + REFERENCE["modifier_cases"], ids=_ids(REFERENCE["cases"] + REFERENCE["modifier_cases"])
)
def test_the_range_is_ordered_and_the_ko_chance_is_a_share(case):
    result = _estimate(case)

    assert 0 <= result.min_pct <= result.max_pct
    assert 0 <= result.ko_chance <= 1


@pytest.mark.parametrize(
    "move, defender",
    [
        ("Stomping Tantrum", BattleMon("Corviknight")),
        ("Shadow Ball", BattleMon("Ursaluna")),
        ("Thunderbolt", BattleMon("Pelipper", ability="Lightning Rod")),
        ("Flamethrower", BattleMon("Dachsbun", ability="Well-Baked Body")),
        ("Giga Drain", BattleMon("Azumarill", ability="Sap Sipper")),
        ("Earth Power", BattleMon("Orthworm", ability="Earth Eater")),
    ],
    ids=["type", "ghost", "lightning rod", "well-baked body", "sap sipper", "earth eater"],
)
def test_an_immunity_gives_no_damage(move, defender):
    result = estimate(BattleMon("Garchomp", nature="Modest", evs={"spa": 252}), move, defender)

    assert (result.min_damage, result.max_damage, result.min_pct, result.max_pct, result.ko_chance) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize("move", ["Protect", "Swords Dance", "Seismic Toss", "Super Fang"])
def test_a_status_or_fixed_damage_move_gives_no_damage_and_no_hits(move):
    assert estimate(URSHIFU, move, INCINEROAR) == DamageRange(0, 0, 0.0, 0.0, 0.0, 0)


# -- KO chance and hits ----------------------------------------------------------------------------


def test_the_ko_chance_counts_the_rolls_that_reach_the_current_hp():
    full = estimate(FLUTTER_MANE, "Moonblast", INCINEROAR)
    middle_roll = (full.min_damage + full.max_damage) // 2
    hurt = BattleMon("Incineroar", nature="Careful", evs={"hp": 252, "def": 4, "spd": 252}, current_hp=middle_roll)

    assert full.max_pct < 100 and full.ko_chance == 0
    assert 0 < estimate(FLUTTER_MANE, "Moonblast", hurt).ko_chance < 1
    assert estimate(FLUTTER_MANE, "Moonblast", BattleMon("Incineroar", current_hp=1)).ko_chance == 1


@pytest.mark.parametrize(
    "move, hits",
    [("Surging Strikes", 3), ("Bullet Seed", 3), ("Dragon Darts", 2), ("Population Bomb", 10), ("Close Combat", 1)],
)
def test_hits_counts_fixed_hits_and_two_to_five_as_three(move, hits):
    assert estimate(URSHIFU, move, INCINEROAR).hits == hits


def test_a_move_record_is_accepted_as_well_as_a_name():
    assert estimate(FLUTTER_MANE, data.move("Moonblast"), INCINEROAR) == estimate(FLUTTER_MANE, "Moonblast", INCINEROAR)


def test_a_types_override_changes_effectiveness_and_stab():
    as_fairy = BattleMon("Incineroar", nature="Careful", types=("Fairy",))

    assert (
        estimate(URSHIFU, "Close Combat", as_fairy).max_damage
        < estimate(URSHIFU, "Close Combat", INCINEROAR).max_damage
    )
    assert estimate(BattleMon("Urshifu-Rapid-Strike", types=("Normal",)), "Surging Strikes", INCINEROAR).max_damage < (
        estimate(BattleMon("Urshifu-Rapid-Strike"), "Surging Strikes", INCINEROAR).max_damage
    )


@pytest.mark.parametrize(
    "attacker, move, defender, said",
    [
        (BattleMon("Missingmon"), "Tackle", INCINEROAR, "unknown species 'Missingmon'"),
        (URSHIFU, "Hyper Mega Beam", INCINEROAR, "unknown move 'Hyper Mega Beam'"),
        (URSHIFU, "Tackle", BattleMon("Incineroar", nature="Grumpy"), "unknown nature 'Grumpy'"),
    ],
    ids=["species", "move", "nature"],
)
def test_an_unknown_species_move_or_nature_raises(attacker, move, defender, said):
    with pytest.raises(ValueError, match=said):
        estimate(attacker, move, defender)


@pytest.mark.parametrize("weather", ["SunnyDay", "sun"])
def test_showdowns_weather_and_terrain_names_are_understood(weather):
    charizard = BattleMon("Charizard", nature="Timid", evs={"spa": 252})
    sunny = estimate(charizard, "Flamethrower", INCINEROAR, Field(weather=weather))
    raichu = BattleMon("Raichu", nature="Modest", evs={"spa": 252})

    assert sunny.max_damage > estimate(charizard, "Flamethrower", INCINEROAR).max_damage
    assert estimate(raichu, "Thunderbolt", INCINEROAR, Field(terrain="electricterrain")) == estimate(
        raichu, "Thunderbolt", INCINEROAR, Field(terrain="Electric")
    )


# -- the arithmetic --------------------------------------------------------------------------------


@pytest.mark.parametrize("value, rounded", [(109.5, 109), (109.51, 110), (130.0, 130), (0.4, 0)])
def test_poke_round_rounds_half_down_and_above_half_up(value, rounded):
    assert poke_round(value) == rounded


def test_chain_combines_modifiers_rounding_each_step_and_clamps():
    assert chain([], (41, 131072)) == 4096
    assert chain([6144, 5325], (41, 131072)) == 7988
    assert chain([2048, 2048, 2048, 2048, 2048, 2048, 2048, 2048], (410, 131072)) == 410
