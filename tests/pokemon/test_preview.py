"""Team preview: the best 4 of our 6 and 2 leads, scored against the opponent's six known sets.

The numbered tests are the acceptance items of the team preview policy, on
observations in the guide's shape (pokemon.md, section 6): hand-built ones,
and the one the practice harness builds from a real Showdown team-preview
request (`tests/fixtures/pokemon_practice/showdown_p1_view.json`).
"""

from __future__ import annotations

import json
import time
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest

from ucla_agent.games.pokemon import data
from ucla_agent.games.pokemon.draft import THEIRS, DraftMemory
from ucla_agent.games.pokemon.observe import Reader, UnexpectedState
from ucla_agent.games.pokemon.preview import (
    LineupTable,
    RosterPokemon,
    matchup,
    rank_lineups,
    rank_lineups_maximin,
    read_rosters,
)
from ucla_agent.games.pokemon.sets import best_damage, from_facts
from ucla_agent.games.pokemon.tuning import Tuning
from ucla_agent.practice.pokemon.cards import load_catalog
from ucla_agent.practice.pokemon.preview import lineup_choice, lineup_legal_action, preview_observation

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "pokemon_practice"
TUNING = Tuning.from_file(REPO_ROOT).preview
CHOOSING_LIMIT_SECONDS = 0.2


def entry(species: str, moves: list[str], **facts: Any) -> dict[str, Any]:
    """A roster entry as the guide shows it: the species id, name and types, and the set's facts."""
    record = data.species(species)
    return {
        "species": data.to_id(species),
        "name": record.name,
        "types": [kind.upper() for kind in record.types],
        "base_stats": dict(record.base_stats),
        "level": 50,
        "moves": [data.to_id(move) for move in moves],
        **facts,
    }


def preview(ours: list[dict], theirs: list[dict], roster: list[str] | None = None) -> tuple[dict, dict]:
    """A team preview observation and its `select_lineup` template; `roster` spells our species in the template."""
    template = {
        "type": "select_lineup",
        "roster": roster or [pokemon["species"] for pokemon in ours],
        "bring_count": 4,
        "lead_count": 2,
    }
    return {"phase": "team_preview", "your_roster": ours, "opponent_roster": theirs}, template


def best_lineup(observation: dict, template: dict, memory: DraftMemory | None = None):
    ours, theirs = read_rosters(observation, template, memory or DraftMemory(), Reader())
    return rank_lineups(ours, theirs, TUNING)[0]


WATER_SIX = [
    entry(species, ["Waterfall", "Hydro Pump"])
    for species in ("Vaporeon", "Milotic", "Suicune", "Blastoise", "Golduck", "Feraligatr")
]
# Three pairs of equal stats, one of each pair Fire and the other Grass (or Electric/Grass).
MIXED_SIX = [
    entry("Simisear", ["Flamethrower"]),
    entry("Pansear", ["Flamethrower"]),
    entry("Rotom-Heat", ["Flamethrower"]),
    entry("Simisage", ["Energy Ball"]),
    entry("Pansage", ["Energy Ball"]),
    entry("Rotom-Mow", ["Thunderbolt"]),
]


# -- (1) a valid lineup spelled as the template spells the roster ----------------------------------


def test_1_the_lineup_brings_4_distinct_roster_species_and_leads_with_2_of_them_spelled_as_the_template():
    spelled = ["Simisear", "PANSEAR", "Rotom-Heat", "Simisage", "Pansage", "rotom-mow"]
    observation, template = preview(MIXED_SIX, WATER_SIX, roster=spelled)

    action = best_lineup(observation, template).action()

    assert action["type"] == "select_lineup"
    assert len(set(action["bring"])) == 4 and set(action["bring"]) <= set(spelled)
    assert len(set(action["leads"])) == 2 and set(action["leads"]) <= set(action["bring"])


def test_1_on_the_practice_harness_s_real_preview_every_ranked_lineup_is_one_showdown_takes():
    showdown = json.loads((FIXTURES / "showdown_p1_view.json").read_text(encoding="utf-8"))
    catalog = load_catalog()
    request = showdown["team_preview_request"]
    observation = preview_observation(request, [catalog.card(card_id) for card_id in showdown["p2_cards"]], catalog)
    template = lineup_legal_action(request, "s", 0)["input"]["action"]

    lineups = rank_lineups(*read_rosters(observation, template, DraftMemory(), Reader()), TUNING)

    assert len(lineups) == 90 and len({(lineup.bring, lineup.leads) for lineup in lineups}) == 90
    assert all(lineup_choice(lineup.action(), request).startswith("team ") for lineup in lineups)


# -- (2) type matchups -----------------------------------------------------------------------------


def test_2_against_an_all_water_roster_a_grass_and_electric_heavy_four_is_brought_over_a_fire_heavy_four():
    observation, template = preview(MIXED_SIX, WATER_SIX)

    chosen = best_lineup(observation, template)
    lineups = rank_lineups(*read_rosters(observation, template, DraftMemory(), Reader()), TUNING)
    fire_heavy = max(
        (lineup for lineup in lineups if {"simisear", "pansear", "rotomheat"} <= set(lineup.bring)),
        key=lambda lineup: lineup.score,
    )

    assert {"simisage", "pansage", "rotommow"} <= set(chosen.bring)
    assert chosen.score > fire_heavy.score


# -- (3) a card the draft memory lacks -------------------------------------------------------------


def test_3_a_roster_card_missing_from_the_draft_memory_is_scored_from_the_observation_facts():
    observation, template = preview(MIXED_SIX, WATER_SIX)

    ours, theirs = read_rosters(observation, template, DraftMemory(), Reader())
    mow = next(pokemon for pokemon in ours if pokemon.name == "rotommow")

    assert mow.set == from_facts("rotommow", moves=["thunderbolt"], level=50)
    assert matchup(mow.set, theirs) > 0
    assert matchup(mow.set, theirs) == pytest.approx(
        sum(best_damage(mow.set, foe) - best_damage(foe, mow.set) for foe in theirs)
    )


def test_the_draft_memory_s_full_sets_are_preferred_to_the_observation_facts():
    banded = {"card_id": "vgc-gyarados", "species": "Gyarados", "item": "Choice Band", "ability": "Moxie"}
    banded |= {"nature": "Adamant", "level": 50, "evs": {"atk": 252}, "ivs": {}, "moves": ["Waterfall"]}
    memory = DraftMemory()
    memory.update({"available_cards": [banded], "rosters": {"us": [], "them": []}}, "us", Reader())
    memory.update({"available_cards": [], "rosters": {"us": [], "them": [banded]}}, "us", Reader())
    observation, template = preview(MIXED_SIX, [entry("Gyarados", [])])

    _ours, theirs = read_rosters(observation, template, memory, Reader())

    assert theirs == memory.roster_sets(THEIRS) and theirs[0].item == "choiceband"


def test_without_an_opponent_roster_the_drafted_roster_stands_in_and_counts_as_a_degradation():
    memory = DraftMemory()
    gyarados = {"card_id": "vgc-gyarados", "species": "Gyarados", "moves": ["Waterfall"]}
    memory.update({"available_cards": [gyarados], "rosters": {"them": [gyarados]}}, "us", Reader())
    observation, template = preview(MIXED_SIX, [])
    del observation["opponent_roster"]
    reader = Reader()

    _ours, theirs = read_rosters(observation, template, memory, reader)

    assert [foe.species for foe in theirs] == ["gyarados"] and reader.degraded_keys == ["opponent_roster"]


def test_an_ill_typed_optional_key_counts_and_a_species_unknown_to_the_data_scores_zero():
    odd = {**entry("Simisage", ["Energy Ball"]), "types": "GRASS"}
    observation, template = preview(
        [*MIXED_SIX[:5], odd], WATER_SIX, roster=[*[p["species"] for p in MIXED_SIX[:5]], "missingmon"]
    )
    reader = Reader()

    ours, _theirs = read_rosters(observation, template, DraftMemory(), reader)

    assert reader.degraded_keys == ["types"]
    assert ours[-1] == RosterPokemon("missingmon", None) and matchup(None, []) == 0.0


@pytest.mark.parametrize("roster", [None, ["simisage", "pansage", "simisage"], "simisage"])
def test_a_template_roster_without_four_species_is_an_unexpected_state(roster):
    observation, template = preview(MIXED_SIX, WATER_SIX)
    template["roster"] = roster

    with pytest.raises(UnexpectedState):
        read_rosters(observation, template, DraftMemory(), Reader())


def test_a_lineup_describes_itself_for_the_model():
    observation, template = preview(MIXED_SIX, WATER_SIX)

    text = best_lineup(observation, template).description()

    assert text.startswith("bring ") and "; lead " in text and "; score " in text


def test_decides_an_unscored_lineup_names_the_four_brought_and_the_two_leads_and_no_score():
    observation, template = preview(MIXED_SIX, WATER_SIX)
    lineup = best_lineup(observation, template)

    text = lineup.description(with_score=False)

    assert text == f"bring {', '.join(lineup.bring)}; lead {' and '.join(lineup.leads)}"
    assert len(lineup.bring) == 4 and len(lineup.leads) == 2 and "score" not in text
    assert lineup.description() == f"{text}; score {lineup.score:.0f}"


# -- (4) timing ------------------------------------------------------------------------------------


def test_4_choosing_a_lineup_takes_under_200_ms():
    observation, template = preview(MIXED_SIX, WATER_SIX)
    data.shipped()
    best_damage.cache_clear()

    started = time.perf_counter()
    best_lineup(observation, template)

    assert time.perf_counter() - started < CHOOSING_LIMIT_SECONDS


# -- maximin ---------------------------------------------------------------------------------------

# A varied six for the opponent, so that which four they bring matters.
VARIED_SIX = [
    entry("Garchomp", ["Earthquake", "Dragon Claw"]),
    entry("Chi-Yu", ["Heat Wave", "Dark Pulse"]),
    entry("Amoonguss", ["Pollen Puff", "Spore"]),
    entry("Tornadus", ["Tailwind", "Bleakwind Storm"]),
    entry("Incineroar", ["Fake Out", "Flare Blitz"]),
    entry("Milotic", ["Scald"]),
]


def _lineups(size: int) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
    """Every lineup of a roster of `size` Pokémon by index: 4 of them brought (all, when fewer), 2 of those leading."""
    bring = min(4, size)
    return [(group, pair) for group in combinations(range(size), bring) for pair in combinations(group, min(2, bring))]


def _pair_score(table: LineupTable, ours, theirs) -> float:
    """One lineup of ours against one of theirs, as the requirement scores it, written out on its own."""
    (group, pair), (their_group, their_pair) = ours, theirs
    brought = sum(table.matchups[mine][foe] for mine in group for foe in their_group)
    leading = sum(table.matchups[mine][foe] for mine in pair for foe in their_pair)
    control = sum(table.our_control[mine] for mine in pair) - sum(table.their_control[foe] for foe in their_pair)
    return brought + TUNING.lead_weight * leading + TUNING.speed_control_bonus * control


def _today_score(table: LineupTable, ours) -> float:
    """Today's lineup score of the matrix: against the opponent's six, leads weighted, our speed control counted."""
    group, pair = ours
    against_six = [sum(row) for row in table.matchups]
    control = sum(table.our_control[mine] for mine in pair)
    return (
        sum(against_six[mine] for mine in group)
        + TUNING.lead_weight * sum(against_six[mine] for mine in pair)
        + TUNING.speed_control_bonus * control
    )


def _names(table: LineupTable, lineup) -> tuple[tuple[str, ...], tuple[str, ...]]:
    group, pair = lineup
    return tuple(table.our_names[index] for index in group), tuple(table.our_names[index] for index in pair)


def _worsts(table: LineupTable) -> dict[tuple, float]:
    """Each of our lineups' worst score over the opponent's 90 lineups, by its names, worked out pair by pair."""
    theirs = _lineups(len(table.their_names))
    return {
        _names(table, ours): min(_pair_score(table, ours, reply) for reply in theirs)
        for ours in _lineups(len(table.our_names))
    }


def test_maximin_2_the_lineup_picked_has_the_highest_worst_score_over_the_opponent_s_90_lineups():
    observation, template = preview(MIXED_SIX, VARIED_SIX)
    table = LineupTable.of(*read_rosters(observation, template, DraftMemory(), Reader()))

    ranked = rank_lineups_maximin(table, TUNING, time.perf_counter() + 10)

    worsts = _worsts(table)
    best = ranked[0]
    assert len(ranked) == 90 and best.worst == pytest.approx(max(worsts.values()))
    assert best.worst == pytest.approx(worsts[best.lineup.bring, best.lineup.leads]) and best.lineup.score == best.worst
    assert [(lineup.worst, lineup.mean) for lineup in ranked] == sorted(
        ((lineup.worst, lineup.mean) for lineup in ranked), reverse=True
    )


def test_maximin_2_in_a_constructed_case_the_best_worst_case_lineup_is_not_today_s_best_nor_the_best_mean():
    # Ours 1 beats five of theirs soundly and is crushed by their sixth; the other five of ours are a little ahead
    # of everyone. Against the six together ours 1 is the best of ours, but every lineup with it meets the sixth.
    strong, crushed, slight = 60.0, -250.0, 5.0
    table = LineupTable(
        our_names=("one", "two", "three", "four", "five", "six"),
        their_names=("a", "b", "c", "d", "e", "f"),
        matchups=((strong,) * 5 + (crushed,), *([(slight,) * 6] * 5)),
        our_control=(False,) * 6,
        their_control=(False,) * 6,
    )

    ranked = rank_lineups_maximin(table, TUNING, time.perf_counter() + 10)

    today = max(_lineups(6), key=lambda lineup: _today_score(table, lineup))
    best_mean = max(ranked, key=lambda lineup: lineup.mean)
    assert "one" in _names(table, today)[0] and "one" in best_mean.lineup.bring
    assert "one" not in ranked[0].lineup.bring
    assert ranked[0].worst == pytest.approx(max(_worsts(table).values()))
    assert "f" in ranked[-1].reply.bring and "f" in ranked[-1].reply.leads


def test_maximin_against_fewer_than_four_known_sets_they_bring_all_they_have():
    observation, template = preview(MIXED_SIX, WATER_SIX[:3])
    table = LineupTable.of(*read_rosters(observation, template, DraftMemory(), Reader()))

    ranked = rank_lineups_maximin(table, TUNING, time.perf_counter() + 10)

    assert len(ranked) == 90 and all(len(lineup.reply.bring) == 3 for lineup in ranked)
    assert ranked[0].reply.description().startswith("bring Vaporeon, Milotic, Suicune; lead ")


def test_maximin_gives_nothing_without_a_known_opponent_set_or_past_its_time_limit():
    observation, template = preview(MIXED_SIX, VARIED_SIX)
    ours, theirs = read_rosters(observation, template, DraftMemory(), Reader())

    assert rank_lineups_maximin(LineupTable.of(ours, []), TUNING, time.perf_counter() + 10) is None
    assert rank_lineups_maximin(LineupTable.of(ours, theirs), TUNING, time.perf_counter() - 1) is None


def test_maximin_on_the_practice_harness_s_real_preview_every_lineup_is_one_showdown_takes():
    showdown = json.loads((FIXTURES / "showdown_p1_view.json").read_text(encoding="utf-8"))
    catalog = load_catalog()
    request = showdown["team_preview_request"]
    observation = preview_observation(request, [catalog.card(card_id) for card_id in showdown["p2_cards"]], catalog)
    template = lineup_legal_action(request, "s", 0)["input"]["action"]
    table = LineupTable.of(*read_rosters(observation, template, DraftMemory(), Reader()))

    ranked = rank_lineups_maximin(table, TUNING, time.perf_counter() + 10)

    assert len({(lineup.lineup.bring, lineup.lineup.leads) for lineup in ranked}) == 90
    assert all(lineup_choice(lineup.lineup.action(), request).startswith("team ") for lineup in ranked)


def test_maximin_choosing_a_lineup_against_the_opponent_s_90_takes_under_200_ms():
    observation, template = preview(MIXED_SIX, VARIED_SIX)
    data.shipped()
    best_damage.cache_clear()

    started = time.perf_counter()
    rank_lineups_maximin(
        LineupTable.of(*read_rosters(observation, template, DraftMemory(), Reader())), TUNING, time.perf_counter() + 10
    )

    assert time.perf_counter() - started < CHOOSING_LIMIT_SECONDS
