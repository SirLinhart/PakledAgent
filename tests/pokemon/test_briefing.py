"""The model's briefing: the draft, preview and battle contexts a model choice is asked with when it is switched on.

The numbered tests are the acceptance items of the briefing, on hand-built
states in the guide's shapes (`pokemon_states`) and on the tracked practice
cards.
"""

from __future__ import annotations

import dataclasses
import json
import math
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import httpx
import pytest
from pokemon_states import THEM, US, card, doubles, envelope, legal_action, memory_of, move, pokemon, switch
from pokemon_stubs import Answering

from altruagent import DecisionContext
from ucla_agent.core import paths, records
from ucla_agent.core.decide import ChoiceRequest, LocalModelSource
from ucla_agent.core.decide import local as local_module
from ucla_agent.core.modelserver import settings as server_settings
from ucla_agent.games.pokemon import data
from ucla_agent.games.pokemon.agent import PokemonAgent
from ucla_agent.games.pokemon.battle import Board, BattleTracker, Fighter, read_board
from ucla_agent.games.pokemon.briefing import (
    BENCH_MOVES_LEFT_OUT,
    CUT_MARKER,
    GUESSED_MARK,
    GUESSED_MOVES_LEFT_OUT,
    MAX_CONTEXT_CHARS,
    battle_context,
    draft_context,
    draft_reply,
    is_guessed,
    lineup_reply,
    preview_context,
    set_line,
    turn_reply,
)
from ucla_agent.games.pokemon.decide import DRAFT, PREVIEW, TURN
from ucla_agent.games.pokemon.draft import DraftMemory, score_cards, score_cards_lookahead
from ucla_agent.games.pokemon.maximin import MaximinAction, Outcome, Side, opponent_view, rank_turn
from ucla_agent.games.pokemon.observe import Reader
from ucla_agent.games.pokemon.preview import Lineup, MaximinLineup, RosterPokemon, TheirLineup, read_rosters
from ucla_agent.games.pokemon.recent import SECTION_TITLE, recent_turns
from ucla_agent.games.pokemon.sets import SPREAD_TARGETS, best_damage, from_card, from_facts, is_damaging, type_effect
from ucla_agent.games.pokemon.settings import PokemonSettings
from ucla_agent.games.pokemon.tuning import BriefingTuning, Tuning
from ucla_agent.games.pokemon.turn import JointAction, Scorer, SlotChoice, rank_actions
from ucla_agent.practice.pokemon.cards import CATALOG_PATH, load_catalog
from ucla_agent.practice.pokemon import play_practice_match
from ucla_agent.practice.pokemon.draft import draw_pool

REPO_ROOT = Path(__file__).resolve().parents[2]
TUNING = Tuning.from_file(REPO_ROOT)
# Read before any test points the project root at a folder of its own. The script's own ranking, whatever
# `[maximin] enabled` ships: the briefing tests assert on today's candidates and their descriptions.
TRACKED = dataclasses.replace(
    PokemonSettings.from_files(REPO_ROOT),
    tuning=dataclasses.replace(TUNING, maximin=dataclasses.replace(TUNING.maximin, enabled=False)),
)
CATALOG = load_catalog(REPO_ROOT / CATALOG_PATH)
STRONG_CTX_SIZE = next(role.ctx_size for role in server_settings.load(REPO_ROOT).roles if role.name == "strong")
POOL_SEED = 7
# An 18-card pool of the tracked practice cards, each as the guide's `available_cards` lists a card.
POOL = [
    card(drawn.species, drawn.moves, drawn.item, drawn.ability, drawn.nature, dict(drawn.evs))
    for drawn in draw_pool(CATALOG, random.Random(POOL_SEED))
]
INCINEROAR = card("Incineroar", ["Fake Out", "Knock Off", "Flare Blitz", "Parting Shot"], "Assault Vest", "Intimidate")
AMOONGUSS = card("Amoonguss", ["Spore", "Rage Powder", "Pollen Puff", "Protect"], "Sitrus Berry", "Regenerator")


# -- the set line ----------------------------------------------------------------------------------


def test_a_set_line_names_types_item_ability_every_stat_and_each_move_s_type_power_and_target():
    incineroar = from_card(INCINEROAR)

    line = set_line(incineroar, guessed=False)

    stats = " ".join(f"{label} {incineroar.stat(stat)}" for stat, label in [("hp", "HP"), ("atk", "Atk")])
    assert line.startswith(f"Incineroar [Fire/Dark] @ assaultvest, Intimidate | {stats} Def ")
    assert line.endswith(
        " | moves: Fake Out (Normal, 40, one target), Knock Off (Dark, 65, one target), "
        "Flare Blitz (Fire, 120, one target), Parting Shot (Dark, status, one target)"
    )
    assert f"SpA {incineroar.stat('spa')} SpD {incineroar.stat('spd')} Spe {incineroar.stat('spe')} |" in line


def test_a_set_line_words_spread_and_self_targets_and_drops_its_moves_on_request():
    amoonguss = from_card(AMOONGUSS)
    heat_wave = from_card(card("Torkoal", ["Heat Wave", "Earthquake"]))

    assert "Rage Powder (Bug, status, self)" in set_line(amoonguss, guessed=False)
    assert "Heat Wave (Fire, 95, both foes), Earthquake (Ground, 100, foes and ally)" in set_line(
        heat_wave, guessed=False
    )
    assert "moves:" not in set_line(amoonguss, guessed=False, with_moves=False)
    assert set_line(amoonguss, guessed=False, with_moves=False).endswith(f"Spe {amoonguss.stat('spe')}")


def test_a_set_made_from_facts_is_marked_guessed_and_shows_the_assumed_attacks():
    memory = memory_of([INCINEROAR], [AMOONGUSS])
    guessed = from_facts("Flutter Mane")

    line = set_line(guessed, guessed=is_guessed(guessed, memory))

    assert is_guessed(guessed, memory) and not is_guessed(from_card(AMOONGUSS), memory)
    assert line.startswith("Flutter Mane [Ghost/Fairy] @ no item, unknown ability | HP ")
    assert line.endswith(
        f"moves: Ghost attack (Ghost, 80, one target), Fairy attack (Fairy, 80, one target) {GUESSED_MARK}"
    )


# -- the draft -------------------------------------------------------------------------------------


def _roster(cards: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"card_id": entry["card_id"], "species": entry["species"]} for entry in cards]


def drafted(available: Sequence[dict[str, Any]], ours: Sequence[dict[str, Any]], theirs: Sequence[dict[str, Any]]):
    """A draft observation: the cards left in the pool, and both rosters in pick order."""
    return {"available_cards": list(available), "rosters": {US: _roster(ours), THEM: _roster(theirs)}}


def draft_after_four_picks() -> DraftMemory:
    """The 18-card draft after its first four picks: the opponent took the first card before we saw the pool, then
    we took two and they took one."""
    taken_unseen, our_first, our_second, their_second, *left = POOL
    memory = DraftMemory()
    memory.update(drafted(POOL[1:], [], [taken_unseen]), US, Reader())
    memory.update(drafted(left, [our_first, our_second], [taken_unseen, their_second]), US, Reader())
    return memory


def _listed(entries: Sequence[dict[str, Any]]) -> list[str]:
    return [f"- {set_line(from_card(entry), guessed=False)}" for entry in entries]


def test_1_the_draft_context_holds_the_pool_left_both_rosters_in_pick_order_and_the_picks_left():
    taken_unseen, our_first, our_second, their_second, *left = POOL

    lines = draft_context(draft_after_four_picks()).splitlines()

    pool_at, ours_at, theirs_at = (lines.index(title) for title in ("Pool (14 left):", "Our roster:", "Their roster:"))
    assert lines[0] == "Draft." and pool_at == 1
    assert lines[pool_at + 1 : ours_at] == _listed(left) and len(left) == 14
    assert lines[ours_at + 1 : theirs_at] == _listed([our_first, our_second])
    assert lines[theirs_at + 1 :] == [
        f"- {set_line(from_facts(taken_unseen['species']), guessed=True)}",
        *_listed([their_second]),
        "Picks left: ours 4, theirs 4",
    ]
    assert lines[theirs_at + 1].endswith(GUESSED_MARK) and sum(GUESSED_MARK in line for line in lines) == 1


def test_1_each_offered_card_s_description_gains_its_types_and_speed_beside_today_s_score_parts():
    offered = [entry["card_id"] for entry in POOL[4:8]]

    scores = score_cards(draft_after_four_picks(), offered, TUNING.draft)

    assert len(scores) == len(offered)
    for score in scores:
        typed = f" [{'/'.join(score.card.types)}] Spe {score.card.stat('spe')}"
        briefed = score.description(with_types_and_speed=True)
        assert briefed.startswith(f"{score.card.record.name}{typed} @ ")
        assert briefed.replace(typed, "", 1) == score.description()


def test_a_draft_before_any_pick_lists_empty_rosters_and_six_picks_each():
    memory = DraftMemory()
    memory.update(drafted(POOL, [], []), US, Reader())

    lines = draft_context(memory).splitlines()

    assert lines[1] == "Pool (18 left):" and len(lines) == 2 + 18 + 3
    assert lines[-3:] == ["Our roster: none", "Their roster: none", "Picks left: ours 6, theirs 6"]


# -- team preview ----------------------------------------------------------------------------------

MATCHUP = re.compile(
    r"Ours (?P<x>.+) vs Theirs (?P<y>.+): (?P=x)→(?P=y) (?P<xy>\d+)%, (?P=y)→(?P=x) (?P<yx>\d+)%, faster (?P<f>.+)"
)


def _summary(entry: dict[str, Any]) -> dict[str, str]:
    return {"species": data.to_id(entry["species"]), "name": entry["species"]}


def preview_after_draft(ours: Sequence[dict[str, Any]], theirs: Sequence[dict[str, Any]]):
    """Our six and the opponent's six as the team preview reads them after a draft of these cards, and the memory."""
    memory = memory_of(ours, theirs)
    observation = {
        "phase": "team_preview",
        "your_roster": [_summary(entry) for entry in ours],
        "opponent_roster": [_summary(entry) for entry in theirs],
    }
    template = {"type": "select_lineup", "roster": [data.to_id(entry["species"]) for entry in ours]}
    return (*read_rosters(observation, template, memory, Reader()), memory)


def test_2_the_preview_context_holds_both_sixes_and_36_matchups_of_best_damage_and_speed():
    ours, theirs, memory = preview_after_draft(POOL[:6], POOL[6:12])

    lines = preview_context(ours, theirs, memory).splitlines()

    ours_at, theirs_at = lines.index("Ours:"), lines.index("Theirs:")
    matchups_at = next(place for place, line in enumerate(lines) if line.startswith("Matchups:"))
    assert lines[ours_at + 1 : theirs_at] == [f"- {set_line(pokemon.set, guessed=False)}" for pokemon in ours]
    assert lines[theirs_at + 1 : matchups_at] == [f"- {set_line(found, guessed=False)}" for found in theirs]
    matchups = [MATCHUP.fullmatch(line) for line in lines[matchups_at + 1 :]]
    assert len(matchups) == 36 and all(matchups)
    for found, (mine, foe) in zip(matchups, [(pokemon.set, foe) for pokemon in ours for foe in theirs]):
        assert (found["x"], found["y"]) == (mine.record.name, foe.record.name)
        assert (int(found["xy"]), int(found["yx"])) == (round(best_damage(mine, foe)), round(best_damage(foe, mine)))
        speeds = {mine.record.name: mine.stat("spe"), foe.record.name: foe.stat("spe")}
        assert found["f"] == ("tie" if len(set(speeds.values())) == 1 else max(speeds, key=speeds.get))


def test_a_guessed_opponent_set_is_marked_and_a_species_unknown_to_the_data_is_named_without_matchups():
    ours, theirs, memory = preview_after_draft(POOL[:6], POOL[6:12])
    guessed = from_facts("Raichu")

    lines = preview_context([*ours[:5], RosterPokemon("Missingno", None)], [*theirs[:5], guessed], memory).splitlines()

    assert "- Missingno (species unknown to the data)" in lines
    assert lines[lines.index("Theirs:") + 6] == f"- {set_line(guessed, guessed=True)}"
    assert sum(bool(MATCHUP.fullmatch(line)) for line in lines) == 5 * 6


def test_a_pair_of_equal_speed_is_a_tie():
    incineroar = from_card(INCINEROAR)

    lines = preview_context([RosterPokemon("incineroar", incineroar)], [incineroar], memory_of([INCINEROAR], []))

    assert lines.splitlines()[-1].endswith(", faster tie")


# -- battle ----------------------------------------------------------------------------------------

OUR_FOUR = [
    INCINEROAR,
    AMOONGUSS,
    card("Urshifu-Rapid-Strike", ["Surging Strikes", "Close Combat", "Aqua Jet", "Protect"], "Choice Scarf"),
    card("Flutter Mane", ["Moonblast", "Shadow Ball", "Dazzling Gleam", "Protect"], "Focus Sash"),
]
THEIR_FOUR = [
    card("Tornadus", ["Tailwind", "Bleakwind Storm", "Taunt", "Protect"], "Covert Cloak", "Prankster"),
    card("Iron Bundle", ["Icy Wind", "Freeze-Dry", "Hydro Pump", "Protect"], "Booster Energy"),
    card("Rillaboom", ["Fake Out", "Grassy Glide", "Wood Hammer", "U-turn"], "Miracle Seed", "Grassy Surge"),
    card("Farigiraf", ["Trick Room", "Hyper Voice", "Psychic", "Protect"], "Electric Seed", "Armor Tail"),
]


def battle_memory() -> DraftMemory:
    """The draft memory after a draft of our four and their four, their last card taken before we saw the pool."""
    memory = DraftMemory()
    memory.update(drafted([*OUR_FOUR, *THEIR_FOUR[:3]], [], []), US, Reader())
    memory.update(drafted([], OUR_FOUR, THEIR_FOUR), US, Reader())
    return memory


def battle_turn(**board: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """A doubles turn of four Pokémon a side: our two actives (Amoonguss burned at 60% with Atk -1) and two reserves
    (one fainted); their two actives, a benched Rillaboom seen at 45% and a Farigiraf never seen."""
    observation, template = doubles(
        [pokemon("Incineroar"), pokemon("Amoonguss", 0.6, status="BRN", boosts={"atk": -1})],
        [pokemon("Tornadus", 0.8), pokemon("Iron Bundle")],
        [
            [move("Fake Out", [1, 2]), move("Knock Off", [-2, 1, 2]), switch("Urshifu-Rapid-Strike")],
            [move("Spore", [-1, 1, 2]), move("Pollen Puff", [-1, 1, 2]), move("Protect", [0])],
        ],
        reserves=[pokemon("Urshifu-Rapid-Strike", active=False)],
        **board,
    )
    observation["team"]["p1: Flutter Mane"] = pokemon("Flutter Mane", 0.0, fainted=True, active=False)
    observation["opponent_team"]["p2: Rillaboom"] = pokemon("Rillaboom", 0.45, active=False)
    return observation, template


def battle_board(memory: DraftMemory, **board: Any) -> Board:
    return read_board(*battle_turn(**board), memory.set_for_species, Reader())


def scorer_of(board: Board) -> Scorer:
    return Scorer(board, BattleTracker(), TUNING.turn)


def briefed(board: Board, memory: DraftMemory) -> str:
    """The battle context of a board, with the board's scorer."""
    return battle_context(board, memory, scorer_of(board))


def section(lines: list[str], title: str, end: str | None = None) -> list[str]:
    """The lines after `title` up to the line that starts with `end` (to the last line when `end` is None)."""
    start = lines.index(title) + 1
    stop = (
        next((place for place, line in enumerate(lines) if place > start and line.startswith(end)), None)
        if end
        else None
    )
    return lines[start:stop]


def test_3_the_battle_context_holds_the_field_and_both_sides_actives_and_bench_with_their_sets():
    incineroar, amoonguss, urshifu, flutter_mane = (set_line(from_card(entry), guessed=False) for entry in OUR_FOUR)
    tornadus, iron_bundle, rillaboom = (set_line(from_card(entry), guessed=False) for entry in THEIR_FOUR[:3])
    farigiraf = set_line(from_facts("Farigiraf"), guessed=True)
    memory = battle_memory()
    board = battle_board(
        memory,
        weather={"RAINDANCE": 3},
        side_conditions={"TAILWIND": 2, "SPIKES": 1},
        opponent_side_conditions={"REFLECT": 5, "LIGHTSCREEN": 3},
    )

    lines = briefed(board, memory).splitlines()

    assert lines[:2] == [
        "Turn 1.",
        "Field: weather raindance; terrain none; Trick Room not up; our side Tailwind; "
        "their side Reflect, Light Screen",
    ]
    assert section(lines, "Our side:", "Their side:") == [
        f"- A: HP 100% | {incineroar}",
        f"- B: HP 60%, burned, boosts Atk -1 | {amoonguss}",
        f"- bench: HP 100% | {urshifu}",
        f"- bench: fainted | {flutter_mane}",
    ]
    assert section(lines, "Their side:", "Speed order")[:4] == [
        f"- A: HP 80% | {tornadus}",
        f"- B: HP 100% | {iron_bundle}",
        f"- bench: HP 45% | {rillaboom}",
        f"- bench: HP unknown | {farigiraf}",
    ]


def test_the_field_line_names_terrain_trick_room_and_aurora_veil_and_an_empty_position():
    memory = battle_memory()
    observation, template = battle_turn(fields={"ELECTRICTERRAIN": 4, "TRICKROOM": 3})
    observation["opponent_active_pokemon"][1] = None
    observation["opponent_side_conditions"] = {"AURORAVEIL": 5}

    lines = briefed(read_board(observation, template, memory.set_for_species, Reader()), memory).splitlines()

    assert lines[1] == (
        "Field: weather none; terrain electricterrain; Trick Room up; our side none; their side Aurora Veil"
    )
    assert section(lines, "Their side:")[1] == "- B: empty"


def test_the_opponent_s_seen_bench_is_read_without_counting_a_parse_degradation():
    memory = battle_memory()
    observation, template = battle_turn()
    del observation["opponent_team"]["p2: Rillaboom"]["boosts"]
    reader = Reader()

    board = read_board(observation, template, memory.set_for_species, reader)

    assert board.their_seen["rillaboom"].hp_fraction == 0.45
    assert reader.degradations == 0


def test_without_their_drafted_roster_the_opponent_s_bench_is_what_was_seen_of_it():
    memory = memory_of(OUR_FOUR, [])

    lines = briefed(battle_board(memory), memory).splitlines()

    their_side = section(lines, "Their side:", "Speed order")
    assert len(their_side) == 3
    assert their_side[2].startswith("- bench: HP 45% | Rillaboom [Grass] @ no item, unknown ability | ")
    assert their_side[2].endswith(GUESSED_MARK)


SPEED_ENTRY = re.compile(r"(?P<label>(?:Ours|Theirs) [AB] .+) (?P<speed>\d+)")
BEATS = re.compile(
    r"- (?P<x>(?:Ours|Theirs) [AB] .+) → (?P<y>(?:Ours|Theirs) [AB] .+): (?P<move>.+), "
    r"expected (?P<d>\d+)%, KO (?P<k>\d+)%, ×(?P<e>[\d.]+)"
)


def labelled(board: Board) -> dict[str, Fighter]:
    """The standing actives by the label the briefing gives them."""
    sides = (("Ours", board.ours), ("Theirs", board.theirs))
    return {
        f"{side} {letter} {fighter.set.record.name}": fighter
        for side, fighters in sides
        for letter, fighter in zip("AB", fighters)
        if fighter is not None and fighter.standing
    }


def speed_order(lines: list[str]) -> list[tuple[str, int]]:
    """The speed order line's entries: each active's label and the speed shown for it."""
    (line,) = [line for line in lines if line.startswith("Speed order")]
    entries = [SPEED_ENTRY.fullmatch(entry) for entry in line.split(": ", 1)[1].split(", ")]
    return [(entry["label"], int(entry["speed"])) for entry in entries]


def test_3_the_speed_order_is_the_turn_scorer_s_fastest_first_and_doubles_our_speed_under_tailwind():
    memory = battle_memory()
    calm, tailwind = battle_board(memory), battle_board(memory, side_conditions={"TAILWIND": 3})

    for board in (calm, tailwind):
        actives, scorer = labelled(board), scorer_of(board)
        order = speed_order(briefed(board, memory).splitlines())
        assert [label for label, _speed in order] == sorted(actives, key=lambda label: -scorer.speed(actives[label]))
        assert all(speed == round(scorer.speed(actives[label])) for label, speed in order)
    calm_speeds, tailwind_speeds = (
        dict(speed_order(briefed(board, memory).splitlines())) for board in (calm, tailwind)
    )
    assert tailwind_speeds["Ours A Incineroar"] == 2 * calm_speeds["Ours A Incineroar"] == 2 * 80
    assert tailwind_speeds["Theirs B Iron Bundle"] == calm_speeds["Theirs B Iron Bundle"]


def test_3_under_trick_room_the_speed_order_is_slowest_first():
    memory = battle_memory()
    board = battle_board(memory, fields={"TRICKROOM": 4})

    lines = briefed(board, memory).splitlines()

    order = speed_order(lines)
    assert next(line for line in lines if line.startswith("Speed order")).startswith("Speed order under Trick Room")
    assert [speed for _label, speed in order] == sorted(speed for _label, speed in order)
    assert order[0][0] == "Ours B Amoonguss" and order[-1][0] == "Theirs B Iron Bundle"


def test_the_speed_order_counts_choice_scarf_stages_and_paralysis():
    memory = battle_memory()
    observation, template = doubles(
        [pokemon("Urshifu-Rapid-Strike"), pokemon("Incineroar", boosts={"spe": -1})],
        [pokemon("Tornadus", status="PAR"), pokemon("Iron Bundle", boosts={"spe": 2})],
        [[move("Surging Strikes", [-2, 1, 2])], [move("Knock Off", [-1, 1, 2])]],
    )
    board = read_board(observation, template, memory.set_for_species, Reader())

    speeds = dict(speed_order(briefed(board, memory).splitlines()))

    urshifu, tornadus, iron_bundle = (
        memory.set_for_species(side, name)
        for side, name in [("ours", "urshifurapidstrike"), ("theirs", "tornadus"), ("theirs", "ironbundle")]
    )
    assert speeds["Ours A Urshifu-Rapid-Strike"] == round(urshifu.stat("spe") * 1.5)
    assert speeds["Ours B Incineroar"] == round(80 * 2 / 3)
    assert speeds["Theirs A Tornadus"] == round(tornadus.stat("spe") * 0.5)
    assert speeds["Theirs B Iron Bundle"] == iron_bundle.stat("spe") * 2


def test_3_what_beats_what_gives_each_ordered_pair_of_opposing_actives_the_scorer_s_best_hit():
    memory = battle_memory()
    board = battle_board(memory, weather={"RAINDANCE": 3}, opponent_side_conditions={"REFLECT": 5})
    actives, scorer = labelled(board), scorer_of(board)

    lines = [BEATS.fullmatch(line) for line in briefed(board, memory).splitlines() if " → " in line]

    assert len(lines) == 8 and all(lines)
    ours = [label for label in actives if label.startswith("Ours")]
    theirs = [label for label in actives if label.startswith("Theirs")]
    pairs = [*((mine, foe) for mine in ours for foe in theirs), *((foe, mine) for foe in theirs for mine in ours)]
    for found, (attacker, defender) in zip(lines, pairs):
        attacking, defending = actives[attacker], actives[defender]
        hits = [
            (scorer.hit(attacking, move, defending, spread=move.target in SPREAD_TARGETS), move)
            for move in attacking.set.move_records()
            if is_damaging(move)
        ]
        hit, move = max(hits, key=lambda scored: (scored[0].expected, scored[0].ko_chance))
        assert (found["x"], found["y"], found["move"]) == (attacker, defender, move.name)
        assert (found["d"], found["k"]) == (f"{hit.expected:.0f}", f"{hit.ko_chance * 100:.0f}")
        assert float(found["e"]) == type_effect(move.type, defending.set)


def test_a_fainted_active_is_left_out_of_both_tables_and_a_pokemon_with_no_attack_says_so():
    memory = memory_of([AMOONGUSS, card("Indeedee-F", ["Follow Me", "Trick Room"])], THEIR_FOUR[:2])
    observation, template = doubles(
        [pokemon("Indeedee-F"), pokemon("Amoonguss")],
        [pokemon("Tornadus"), pokemon("Iron Bundle", 0.0, fainted=True)],
        [[move("Follow Me", [0])], [move("Spore", [-1, 1, 2])]],
    )
    board = read_board(observation, template, memory.set_for_species, Reader())

    lines = briefed(board, memory).splitlines()

    assert [label for label, _speed in speed_order(lines)] == [
        "Theirs A Tornadus",
        "Ours A Indeedee-F",
        "Ours B Amoonguss",
    ]
    beats = [line for line in lines if " → " in line]
    assert beats[0] == "- Ours A Indeedee-F → Theirs A Tornadus: no damaging move known"
    assert len(beats) == 4


def test_an_active_of_a_species_unknown_to_the_data_is_named_by_its_id_with_no_attack_either_way():
    memory = battle_memory()
    observation, template = battle_turn()
    observation["opponent_active_pokemon"][1] = {
        **pokemon("Iron Bundle"),
        "species": "notapokemon",
        "name": "Not A Pokemon",
    }

    lines = briefed(read_board(observation, template, memory.set_for_species, Reader()), memory).splitlines()

    assert "- B: HP 100% | notapokemon (species unknown to the data)" in lines
    assert speed_order(lines)[-1] == ("Theirs B notapokemon", 0)
    assert "- Ours A Incineroar → Theirs B notapokemon: no damaging move known" in lines
    assert "- Theirs B notapokemon → Ours B Amoonguss: no damaging move known" in lines


# -- the size cap ----------------------------------------------------------------------------------

UNCAPPED = 10**6


def test_4_each_fixture_s_context_is_within_the_cap_and_shows_every_move_list():
    memory = battle_memory()
    ours, theirs, preview_memory = preview_after_draft(POOL[:6], POOL[6:12])
    contexts = [
        draft_context(draft_after_four_picks()),
        preview_context(ours, theirs, preview_memory),
        briefed(battle_board(memory), memory),
    ]

    assert all(len(context) <= MAX_CONTEXT_CHARS for context in contexts)
    assert not any(note in context for note in (BENCH_MOVES_LEFT_OUT, GUESSED_MOVES_LEFT_OUT) for context in contexts)


def test_4_an_oversized_battle_context_leaves_out_the_bench_move_lists_first():
    memory = battle_memory()
    observation, template = battle_turn()
    for drawn in CATALOG.cards.values():
        shown = {**pokemon(drawn.species, active=False), "item": drawn.item, "moves": list(drawn.moves)}
        observation["team"][f"p1: {drawn.species}"] = shown
        observation["opponent_team"][f"p2: {drawn.species}"] = shown
    board = read_board(observation, template, memory.set_for_species, Reader())

    full = battle_context(board, memory, scorer_of(board), max_chars=UNCAPPED)
    lines = briefed(board, memory).splitlines()

    bench = [line for line in lines if line.startswith("- bench: ")]
    assert len(full) > MAX_CONTEXT_CHARS >= len("\n".join(lines))
    assert lines[1] == BENCH_MOVES_LEFT_OUT and GUESSED_MOVES_LEFT_OUT not in lines
    assert len(bench) > 2 * 70 and not any("moves:" in line for line in bench)
    assert all("moves:" in line for line in section(lines, "Our side:", "- bench:"))


def test_4_past_the_bench_the_guessed_sets_move_lists_are_left_out_and_then_the_context_is_cut():
    memory = battle_memory()
    observation, template = battle_turn()
    observation["opponent_active_pokemon"][1] = pokemon("Farigiraf")
    board = read_board(observation, template, memory.set_for_species, Reader())
    full = battle_context(board, memory, scorer_of(board), max_chars=UNCAPPED)

    no_bench = battle_context(board, memory, scorer_of(board), max_chars=len(full) - 1)
    no_guessed = battle_context(board, memory, scorer_of(board), max_chars=len(no_bench) - 1)
    cut = battle_context(board, memory, scorer_of(board), max_chars=len(no_guessed) - 1)

    assert no_bench.splitlines()[1:3] == [
        BENCH_MOVES_LEFT_OUT,
        "Field: weather none; terrain none; Trick Room not up; our side none; their side none",
    ]
    assert no_guessed.splitlines()[1:3] == [BENCH_MOVES_LEFT_OUT, GUESSED_MOVES_LEFT_OUT]
    farigiraf = next(line for line in no_guessed.splitlines() if line.startswith("- B: HP 100% | Farigiraf"))
    assert "moves:" not in farigiraf and farigiraf.endswith(GUESSED_MARK)
    assert "moves:" in next(line for line in no_guessed.splitlines() if line.startswith("- A: HP 80% | Tornadus"))
    assert len(cut) == len(no_guessed) - 1 and cut.endswith(CUT_MARKER)


def test_4_a_draft_with_no_bench_notes_only_the_guessed_sets_it_shortens():
    memory = draft_after_four_picks()
    full = draft_context(memory)

    shortened = draft_context(memory, max_chars=len(full) - 1).splitlines()

    assert shortened[1] == GUESSED_MOVES_LEFT_OUT and BENCH_MOVES_LEFT_OUT not in shortened
    their_guess = shortened[shortened.index("Their roster:") + 1]
    assert "moves:" not in their_guess and their_guess.endswith(GUESSED_MARK)
    assert all("moves:" in line for line in section(shortened, "Pool (14 left):", "Our roster:"))


# -- the agent -------------------------------------------------------------------------------------

SESSION_ID = "pokemon-briefing-test"
CONTEXT = DecisionContext(SESSION_ID, None, "pokemon_vgc_doubles_draft", US, seat_position=0)
BUILD_LIMIT_SECONDS = 0.05
DECISION_FIELDS = {
    "phase",
    "candidates",
    "script_choice",
    "chosen",
    "source",
    "outcome",
    "latency_ms",
    "parse_degradations",
    "degraded_keys",
    "clock_decision_seconds_left",
    "clock_bank_seconds_left",
    "deadline_ms",
}
CHARS_PER_TOKEN = 3
RECORD_CUT = "…[cut "


def settings(briefing: bool) -> PokemonSettings:
    """The tracked settings with the model asked, `model_briefing` as given and `model_decides` off, so the
    ranked descriptions are the ones measured whatever value ships."""
    return dataclasses.replace(TRACKED, model_choices=True, model_briefing=briefing, model_decides=False)


def offers(cards: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """A draft pick's legal actions: one `draft_pick` per offered card."""
    return [
        legal_action({"type": "draft_pick", "card_id": entry["card_id"]}, f"draft_pick:{entry['card_id']}")
        for entry in cards
    ]


def _names(entries: Sequence[dict[str, Any]]) -> str:
    return ", ".join(data.species(entry["species"]).name for entry in entries)


def play_draft(agent: PokemonAgent) -> None:
    """Our picks of the 18-card draft: the first after the opponent took a card we never saw, the second after the
    first four picks."""
    taken_unseen, our_first, our_second, their_second, *left = POOL
    agent.choose_action(envelope("draft", drafted(POOL[1:], [], [taken_unseen]), offers(POOL[1:5])), CONTEXT)
    agent.choose_action(
        envelope("draft", drafted(left, [our_first, our_second], [taken_unseen, their_second]), offers(left[:4])),
        CONTEXT,
    )


def remember_draft(
    agent: PokemonAgent,
    ours: Sequence[dict[str, Any]],
    theirs: Sequence[dict[str, Any]],
    shown: Sequence[dict[str, Any]],
) -> None:
    """Show the agent a finished draft of these cards, of which it saw `shown` in the pool. Neither state offers it a
    card, so each is played with the fallback and asks no model."""
    agent.choose_action(envelope("draft", drafted(shown, [], []), []), CONTEXT)
    agent.choose_action(envelope("draft", drafted([], ours, theirs), []), CONTEXT)


def preview_state() -> Any:
    """The team preview after a draft of the pool's first twelve cards, ours the first six."""
    ours, theirs = POOL[:6], POOL[6:12]
    observation = {
        "phase": "team_preview",
        "your_roster": [_summary(entry) for entry in ours],
        "opponent_roster": [_summary(entry) for entry in theirs],
    }
    template = {
        "type": "select_lineup",
        "roster": [data.to_id(entry["species"]) for entry in ours],
        "bring_count": 4,
        "lead_count": 2,
    }
    return envelope("team_preview", observation, [legal_action(template, "select_lineup")])


def turn_state() -> Any:
    observation, template = battle_turn(weather={"RAINDANCE": 3})
    return envelope("moving", observation, [legal_action(template, "doubles_turn")])


def play_all_phases(new_agent: Callable[[], PokemonAgent]) -> None:
    """The draft's two picks, a team preview and a doubles turn, each phase played by a new agent after the draft of
    its own fixture."""
    play_draft(new_agent())
    previewing = new_agent()
    remember_draft(previewing, POOL[:6], POOL[6:12], POOL[:12])
    previewing.choose_action(preview_state(), CONTEXT)
    battling = new_agent()
    remember_draft(battling, OUR_FOUR, THEIR_FOUR, [*OUR_FOUR, *THEIR_FOUR[:3]])
    battling.choose_action(turn_state(), CONTEXT)


def asked(briefing: bool) -> dict[str, ChoiceRequest]:
    """The last request of each phase the agents asked the model, with the briefing switched on or off."""
    source = Answering("C1")
    play_all_phases(lambda: PokemonAgent(settings=settings(briefing), choice_source=source))
    by_prefix = {request.options[0].id[0]: request for request in source.choice_requests}
    return {DRAFT: by_prefix["C"], PREVIEW: by_prefix["L"], TURN: by_prefix["T"]}


def expected_briefings() -> dict[str, str]:
    """Each phase's briefing built here from the same states the agent was shown."""
    ours, theirs, preview_memory = preview_after_draft(POOL[:6], POOL[6:12])
    battle = battle_memory()
    return {
        DRAFT: draft_context(draft_after_four_picks()),
        PREVIEW: preview_context(ours, theirs, preview_memory),
        TURN: briefed(battle_board(battle, weather={"RAINDANCE": 3}), battle),
    }


def test_1_with_the_switch_on_the_agent_asks_with_the_briefing_and_the_cards_types_and_speed():
    requests = asked(briefing=True)

    assert {phase: request.context for phase, request in requests.items()} == expected_briefings()
    scores = score_cards(draft_after_four_picks(), [entry["card_id"] for entry in POOL[4:8]], TUNING.draft)
    assert [option.description for option in requests[DRAFT].options] == [
        score.description(with_types_and_speed=True) for score in scores[: TRACKED.top_k]
    ]


MATCH_LOG = json.loads((REPO_ROOT / "tests" / "fixtures" / "pokemon" / "match_0556bdb3_turn5.json").read_text("utf-8"))[
    "protocol_log"
]


def asked_turn(turns: int, log: Any) -> ChoiceRequest:
    """The doubles turn's request with the briefing on, `[briefing] recent_turns` as given and `log` as the
    observation's protocol log (none when None)."""
    source = Answering("C1")
    briefed = settings(True)
    tuned = dataclasses.replace(briefed, tuning=dataclasses.replace(briefed.tuning, briefing=BriefingTuning(turns)))
    battling = PokemonAgent(settings=tuned, choice_source=source)
    remember_draft(battling, OUR_FOUR, THEIR_FOUR, [*OUR_FOUR, *THEIR_FOUR[:3]])
    observation, template = battle_turn(weather={"RAINDANCE": 3})
    if log is not None:
        observation["protocol_log"] = log
    battling.choose_action(envelope("moving", observation, [legal_action(template, "doubles_turn")]), CONTEXT)
    (request,) = source.choice_requests
    return request


def test_the_battle_briefing_ends_with_the_recent_turns_of_the_protocol_log_and_only_the_battle_s():
    battle = battle_memory()
    board = battle_board(battle, weather={"RAINDANCE": 3})
    section = recent_turns(MATCH_LOG, "p1", 2)

    with_log = asked_turn(2, MATCH_LOG)

    assert section.startswith(SECTION_TITLE) and with_log.context.endswith(f"\n{section}")
    assert with_log.context == battle_context(board, battle, scorer_of(board), recent=section)
    assert len(with_log.context) <= MAX_CONTEXT_CHARS
    assert asked_turn(0, MATCH_LOG).context == asked_turn(2, None).context == briefed(board, battle)
    assert not any(SECTION_TITLE in request.context for request in asked(briefing=True).values())


def test_a_battle_briefing_with_the_recent_turns_stays_within_its_cap():
    battle = battle_memory()
    board = battle_board(battle, weather={"RAINDANCE": 3})
    plain = briefed(board, battle)

    capped = battle_context(
        board, battle, scorer_of(board), max_chars=len(plain), recent=recent_turns(MATCH_LOG, "p1", 2)
    )

    assert len(capped) <= len(plain)


def test_5_with_the_switch_off_every_context_and_description_is_today_s():
    requests = asked(briefing=False)

    taken_unseen, our_first, our_second, their_second = POOL[:4]
    assert requests[DRAFT].context == (
        f"Draft. Our roster: {_names([our_first, our_second])}. Their roster: {_names([taken_unseen, their_second])}."
    )
    assert requests[PREVIEW].context == f"Team preview. Ours: {_names(POOL[:6])}. Theirs: {_names(POOL[6:12])}."
    assert requests[TURN].context == (
        "Turn 1. Ours: incineroar 100%, amoonguss 60%. Theirs: tornadus 80%, ironbundle 100%. Field: raindance."
    )
    scores = score_cards(draft_after_four_picks(), [entry["card_id"] for entry in POOL[4:8]], TUNING.draft)
    assert [option.description for option in requests[DRAFT].options] == [
        score.description() for score in scores[: TRACKED.top_k]
    ]


def test_6_building_each_context_takes_under_50_ms():
    draft_memory = draft_after_four_picks()
    ours, theirs, preview_memory = preview_after_draft(POOL[:6], POOL[6:12])
    battle = battle_memory()
    board = battle_board(battle, weather={"RAINDANCE": 3}, side_conditions={"TAILWIND": 2})
    best_damage.cache_clear()
    builds: list[Callable[[], str]] = [
        lambda: draft_context(draft_memory),
        lambda: preview_context(ours, theirs, preview_memory),
        lambda: briefed(board, battle),
    ]

    for build in builds:
        started = time.perf_counter()
        build()
        assert time.perf_counter() - started < BUILD_LIMIT_SECONDS


# -- what the record keeps -------------------------------------------------------------------------


class ModelServer:
    """Stands in for the model server: answers each choice with its second option and keeps every request body."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.transport = httpx.MockTransport(self._respond)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        second = body["response_format"]["json_schema"]["schema"]["properties"]["choice"]["enum"][1]
        message = {"role": "assistant", "content": json.dumps({"choice": second})}
        return httpx.Response(200, json={"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]})

    def source(self) -> LocalModelSource:
        return LocalModelSource("strong", base_url="http://127.0.0.1:11500", transport=self.transport)


@pytest.fixture
def match_events(run_folder, read_jsonl) -> Callable[[], list[dict]]:
    """Opens a match record; the returned function closes it and gives its events."""
    records.start_match(CONTEXT, None)

    def close_and_read() -> list[dict]:
        records.finish_match({"is_terminal": True})
        (events_path,) = run_folder.glob(f"runs/*/{SESSION_ID}__*/events.jsonl")
        return read_jsonl(events_path)

    return close_and_read


def _of_kind(events: list[dict], kind: str) -> list[dict]:
    return [event["data"] for event in events if event["kind"] == kind]


def test_7_each_pokemon_decision_keeps_its_fields_and_each_model_decision_s_io_holds_the_whole_briefing(
    match_events,
):
    server = ModelServer()

    play_all_phases(lambda: PokemonAgent(settings=settings(briefing=True), choice_source=server.source()))

    events = match_events()
    briefings = expected_briefings()
    decisions = _of_kind(events, "pokemon_decision")
    asked_decisions = [decision for decision in decisions if decision["outcome"] == "used"]
    assert all(set(decision) == DECISION_FIELDS for decision in decisions)
    assert [decision["phase"] for decision in asked_decisions] == [DRAFT, DRAFT, PREVIEW, TURN]
    model_decisions = _of_kind(events, "decision")
    sent = [body["messages"][1]["content"] for body in server.bodies]
    assert [decision["io"]["user"] for decision in model_decisions] == sent
    for phase, context in briefings.items():
        assert any(user.startswith(f"{context}\n\n") for user in sent), phase


def giant_draft() -> dict[str, Any]:
    """A draft observation of every catalog card in the pool and an opponent's roster of every catalog species taken
    unseen: a briefing past the cap even without its guessed sets' move lists."""
    pool = [drawn.draft_entry() for drawn in CATALOG.cards.values()]
    unseen = [{"card_id": f"unseen-{entry['card_id']}", "species": entry["species"]} for entry in pool]
    return drafted(pool, [], unseen)


def giant_draft_state() -> Any:
    observation = giant_draft()
    return envelope("draft", observation, offers(observation["available_cards"][:2]))


def test_8_a_briefing_of_the_full_24000_characters_is_recorded_whole_with_no_cut_marker(match_events):
    server = ModelServer()
    memory = DraftMemory()
    memory.update(giant_draft(), US, Reader())

    PokemonAgent(settings=settings(briefing=True), choice_source=server.source()).choose_action(
        giant_draft_state(), CONTEXT
    )

    (decision,) = _of_kind(match_events(), "decision")
    briefing = draft_context(memory)
    assert len(briefing) == MAX_CONTEXT_CHARS
    assert decision["io"]["user"].startswith(f"{briefing}\n\n") and RECORD_CUT not in decision["io"]["user"]
    assert decision["io"]["user"] == server.bodies[0]["messages"][1]["content"]


def prompt_tokens(request: ChoiceRequest) -> int:
    """The tokens the strong role reads and writes for a request: its system message and user message at one token
    per three characters, and the answer's most tokens."""
    body = local_module.build_body("strong", request, with_reason=True)
    characters = sum(len(message["content"]) for message in body["messages"])
    return math.ceil(characters / CHARS_PER_TOKEN) + local_module.REASON_MAX_TOKENS


def test_9_each_phase_s_largest_prompt_fits_the_strong_role_s_context_window():
    requests = asked(briefing=True)
    source = Answering("C1")
    PokemonAgent(settings=settings(briefing=True), choice_source=source).choose_action(giant_draft_state(), CONTEXT)
    (requests[DRAFT],) = source.choice_requests
    at_the_cap = {
        phase: dataclasses.replace(request, context="x" * MAX_CONTEXT_CHARS) for phase, request in requests.items()
    }

    assert STRONG_CTX_SIZE == 16_384
    assert len(requests[DRAFT].context) == MAX_CONTEXT_CHARS
    assert all(prompt_tokens(request) <= STRONG_CTX_SIZE for request in [*requests.values(), *at_the_cap.values()])


FAKE_HELPER = [sys.executable, str(REPO_ROOT / "tests" / "stubs" / "fake_pokemon_sim.py")]
PRACTICE_MATCHES = 3


def test_whole_practice_matches_briefed_and_answered_by_a_model_finish_with_no_error_or_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.RUNS_DIR_ENV, str(tmp_path))
    server = ModelServer()

    def briefed_agent() -> PokemonAgent:
        return PokemonAgent(settings=settings(briefing=True), choice_source=server.source())

    results = [
        play_practice_match(seed, briefed_agent, "random", helper_command=FAKE_HELPER)
        for seed in range(PRACTICE_MATCHES)
    ]

    assert all(result.termination_reason == "completed" and not result.unhandled_error for result in results)
    assert all(result.fallbacks == 0 and result.decisions > 6 for result in results)
    contexts = [body["messages"][1]["content"] for body in server.bodies]
    assert any(context.startswith("Draft.") for context in contexts)
    assert any(context.startswith("Team preview.") for context in contexts)
    assert any(context.startswith("Turn ") and "What beats what:" in context for context in contexts)


# -- maximin: each candidate's best reply ----------------------------------------------------------


def _attack_or_protect() -> tuple[list[MaximinAction], Board]:
    """The maximin ranking of a turn where our Urshifu at 35% may attack or Protect, and the opponent's board."""
    memory = memory_of(
        [card("Urshifu-Rapid-Strike", ["Close Combat", "Protect"]), card("Rillaboom", ["Wood Hammer", "Protect"])],
        [card("Flutter Mane", ["Moonblast"], nature="Timid"), card("Incineroar", ["Flare Blitz", "Knock Off"])],
    )
    observation, template = doubles(
        [pokemon("Urshifu-Rapid-Strike", 0.35), pokemon("Rillaboom")],
        [pokemon("Flutter Mane"), pokemon("Incineroar", 0.45)],
        [
            [move("Close Combat", [-2, 1, 2]), move("Protect", [0])],
            [move("Wood Hammer", [-1, 1, 2]), move("Protect", [0])],
        ],
    )
    board = read_board(observation, template, memory.set_for_species, Reader())
    ours, theirs = Side(board, BattleTracker()), Side(opponent_view(board), BattleTracker())
    ranked = rank_actions(board, ours.tracker, TUNING.turn)
    return rank_turn(ours, theirs, ranked, TUNING.turn, TUNING.maximin, time.perf_counter() + 10), theirs.board


def _worst_words(outcome: Outcome) -> str:
    return (
        f"worst case: we deal {outcome.dealt:.0f}% and take {outcome.taken:.0f}% of HP, with "
        f"{outcome.knocked_out:.1f} of theirs and {outcome.lost:.1f} of ours expected to faint"
    )


def test_maximin_a_turn_candidate_s_reply_names_each_of_their_actions_from_our_side_and_the_worst_case_in_words():
    weighed, their_board = _attack_or_protect()
    attack = next(action for action in weighed if action.joint.action["slot_0"]["move_id"] == "closecombat")

    text = turn_reply(attack, their_board)

    assert text == (
        "their best reply: their A Flutter Mane uses Moonblast at our A Urshifu-Rapid-Strike; "
        f"their B Incineroar uses Flare Blitz at our B Rillaboom; {_worst_words(attack.outcome)}"
    )
    assert attack.outcome.lost > 1 and "score" not in text


def test_maximin_a_reply_that_switches_passes_or_aims_at_its_own_side_reads_from_our_side():
    weighed, their_board = _attack_or_protect()
    reply = JointAction(
        {},
        0.0,
        (
            SlotChoice({"type": "switch", "species": "garchomp"}, 0.0, ""),
            SlotChoice({"type": "move", "move_id": "knockoff", "target": -1}, 0.0, ""),
        ),
    )
    outcome = Outcome(dealt=10.0, taken=20.0, knocked_out=0.5, lost=0.0)
    passed = dataclasses.replace(reply, slots=(SlotChoice({"type": "pass"}, 0.0, ""), reply.slots[1]))

    switched = turn_reply(dataclasses.replace(weighed[0], reply=reply, outcome=outcome), their_board)

    assert switched == (
        "their best reply: their A Flutter Mane switches to Garchomp; their B Incineroar uses Knock Off at their A "
        "Flutter Mane; worst case: we deal 10% and take 20% of HP, with 0.5 of theirs and 0.0 of ours expected to faint"
    )
    assert "their A Flutter Mane passes;" in turn_reply(dataclasses.replace(weighed[0], reply=passed), their_board)


def test_maximin_a_lineup_s_reply_names_the_four_they_bring_their_leads_and_the_worst_score():
    lineup = MaximinLineup(
        Lineup(("a", "b", "c", "d"), ("a", "b"), -12.4),
        -12.4,
        3.0,
        TheirLineup(("Garchomp", "Chi-Yu", "Incineroar", "Milotic"), ("Chi-Yu", "Incineroar")),
    )

    assert lineup_reply(lineup) == (
        "their best reply: bring Garchomp, Chi-Yu, Incineroar, Milotic; lead Chi-Yu and Incineroar; "
        "worst case: the matchups come to -12 (our best damage less theirs, in %)"
    )


def test_maximin_a_draft_card_s_line_names_the_opponent_s_likely_next_pick():
    pool = [card("Kingambit", ["Kowtow Cleave"], "Black Glasses"), card("Snorlax", ["Body Slam"], "Leftovers")]
    memory = DraftMemory()
    memory.update({"available_cards": pool, "rosters": {US: [], THEM: []}}, US, Reader())

    scored = {score.card_id: score for score in score_cards_lookahead(memory, memory.available, TUNING.draft, 1e12)}

    assert draft_reply(scored["vgc-snorlax"]) == (
        "their likely next pick: Kingambit; worst case: they take Kingambit, the card left that adds most to their "
        "roster"
    )
    no_pick = dataclasses.replace(scored["vgc-snorlax"], their_next=None)
    assert draft_reply(no_pick) == "their likely next pick: none"
