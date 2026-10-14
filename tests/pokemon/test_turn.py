"""The doubles turn policy: per-slot option scores and the best legal joint `doubles_turn` action.

The numbered tests are the acceptance items of the turn policy, on hand-built
observations in the guide's doubles format (pokemon.md, section 7, through
`pokemon_states`) and on random boards of the tracked practice cards with
the targets the practice harness gives each move. Consecutive turns go
through one `BattleTracker`, as in a match.
"""

from __future__ import annotations

import dataclasses
import random
import time
from pathlib import Path
from typing import Any

import pytest
from pokemon_states import PASS, card, doubles, memory_of, move, pokemon, switch

from ucla_agent.games.pokemon import data
from ucla_agent.games.pokemon.battle import BattleTracker, read_board
from ucla_agent.games.pokemon.maximin import (
    IDLE,
    PASS_CHOICE,
    OneTurnModel,
    Side,
    locks_choice,
    opponent_view,
    rank_turn,
    shield_chance,
)
from ucla_agent.games.pokemon.observe import Reader
from ucla_agent.games.pokemon.tuning import Tuning
from ucla_agent.games.pokemon.turn import PASS_SCORE, JointAction, Scorer, SlotChoice, rank_actions
from ucla_agent.practice.pokemon.cards import load_catalog
from ucla_agent.practice.pokemon.doubles import move_targets

REPO_ROOT = Path(__file__).resolve().parents[2]
TUNING = Tuning.from_file(REPO_ROOT).turn
RANDOM_BOARDS = 200
BUILD_LIMIT_SECONDS = 0.1


def play(memory, observation: dict, template: dict, tracker: BattleTracker | None = None) -> dict[str, Any]:
    """The best action for one turn, the tracker (a new one unless given) taking in the board and the choice."""
    tracker = tracker or BattleTracker()
    board = read_board(observation, template, memory.set_for_species, Reader())
    tracker.observe(board)
    best = rank_actions(board, tracker, TUNING)[0].action
    tracker.note_choice(board, best)
    return best


# -- an independent check of an action against its template ---------------------------------------


def _same_option(option: dict, choice: dict) -> bool:
    """Whether a choice is this template option: the same move at one of its targets, switch or pass."""
    if option["type"] != choice["type"]:
        return False
    if choice["type"] == "move":
        return option["move_id"] == choice["move_id"] and choice["target"] in option["targets"]
    return choice["type"] == "pass" or option["species"] == choice["species"]


def _offered(options: list[dict], choice: dict) -> bool:
    return any(_same_option(option, choice) for option in options)


def legal(template: dict, action: dict) -> bool:
    """Whether an action keeps the template's rules: offered options and targets, no shared switch, not two passes."""
    choices = [action[f"slot_{slot['slot']}"] for slot in template["slots"]]
    offered = all(_offered(slot["options"], choice) for slot, choice in zip(template["slots"], choices))
    switched = [choice["species"] for choice in choices if choice["type"] == "switch"]
    return (
        offered and choices != [PASS, PASS] and len(switched) == len(set(switched)) and action["type"] == "doubles_turn"
    )


# -- random boards of the practice cards ------------------------------------------------------------


def _slot_options(rng: random.Random, slot: int, mon: dict, reserves: list[str], forced: bool, occupied: set[int]):
    if forced:
        options = [switch(species) for species in reserves]
        return options + ([PASS] if len(reserves) < 2 or not options else [])
    moves = [move(name, move_targets(slot, data.move(name).target, occupied)) for name in mon["moves"]]
    return moves + [switch(species) for species in reserves if rng.random() < 0.7]


def random_turn(rng: random.Random, catalog) -> tuple[Any, dict, dict]:
    """A random doubles turn of practice cards: our 2 actives and up to 2 reserves, their 2 actives."""
    cards = [entry.draft_entry() for entry in rng.sample(list(catalog.cards.values()), 8)]
    ours, theirs = cards[:4], cards[4:6]
    reserves = [data.to_id(entry["species"]) for entry in ours[2 : 2 + rng.randint(0, 2)]]
    forced = [rng.random() < 0.15, rng.random() < 0.15]
    actives = [
        pokemon(entry["species"], 0.0 if flag else rng.uniform(0.1, 1.0), fainted=flag)
        for entry, flag in zip(ours, forced)
    ]
    foes = [pokemon(entry["species"], rng.uniform(0.05, 1.0)) for entry in theirs]
    occupied = {-1, -2, 1, 2}
    options = [_slot_options(rng, slot, ours[slot], reserves, forced[slot], occupied) for slot in (0, 1)]
    options = [slot_options or [PASS] for slot_options in options]
    observation, template = doubles(
        actives, foes, options, reserves=[pokemon(species, active=False) for species in reserves], force_switch=forced
    )
    return memory_of(ours, theirs), observation, template


# -- (1) and (2) legality --------------------------------------------------------------------------


def test_1_every_ranked_action_uses_only_template_options_and_targets_over_200_random_observations():
    rng, catalog = random.Random(7), load_catalog()
    checked = 0
    for _board in range(RANDOM_BOARDS):
        memory, observation, template = random_turn(rng, catalog)
        board = read_board(observation, template, memory.set_for_species, Reader())

        ranked = rank_actions(board, BattleTracker(), TUNING)

        assert ranked and all(legal(template, joint.action) for joint in ranked)
        assert "terastallize" not in str(ranked[0].action)
        checked += len(ranked)
    assert checked > RANDOM_BOARDS


def test_2_with_both_slots_down_and_one_reserve_left_one_slot_switches_and_the_other_passes():
    memory = memory_of(
        [card("Incineroar", ["Flare Blitz"]), card("Amoonguss", ["Pollen Puff"])], [card("Miraidon", ["Electro Drift"])]
    )
    observation, template = doubles(
        [pokemon("Incineroar", 0.0, fainted=True), pokemon("Rillaboom", 0.0, fainted=True)],
        [pokemon("Miraidon"), None],
        [[switch("Amoonguss"), PASS], [switch("Amoonguss"), PASS]],
        reserves=[pokemon("Amoonguss", active=False)],
        force_switch=[True, True],
    )

    action = play(memory, observation, template)
    board = read_board(observation, template, memory.set_for_species, Reader())

    assert sorted([action["slot_0"]["type"], action["slot_1"]["type"]]) == ["pass", "switch"]
    assert all(joint.slots[0].choice != joint.slots[1].choice for joint in rank_actions(board, BattleTracker(), TUNING))


def test_2_two_forced_switches_go_to_two_different_reserves():
    memory = memory_of(
        [card("Milotic", ["Scald"]), card("Gyarados", ["Waterfall"])], [card("Miraidon", ["Electro Drift"])]
    )
    observation, template = doubles(
        [pokemon("Incineroar", 0.0, fainted=True), pokemon("Rillaboom", 0.0, fainted=True)],
        [pokemon("Miraidon"), None],
        [[switch("Milotic"), switch("Gyarados")], [switch("Milotic"), switch("Gyarados")]],
        reserves=[pokemon("Milotic", active=False), pokemon("Gyarados", active=False)],
        force_switch=[True, True],
    )

    action = play(memory, observation, template)

    assert {action["slot_0"]["species"], action["slot_1"]["species"]} == {"milotic", "gyarados"}


# -- (3) knockouts and overkill --------------------------------------------------------------------


def test_3_a_slot_with_a_knockout_available_takes_it():
    memory = memory_of(
        [card("Urshifu-Rapid-Strike", ["Close Combat", "Aqua Jet"]), card("Amoonguss", ["Spore"])],
        [card("Incineroar", ["Flare Blitz"]), card("Miraidon", ["Electro Drift"])],
    )
    observation, template = doubles(
        [pokemon("Urshifu-Rapid-Strike"), pokemon("Amoonguss")],
        [pokemon("Incineroar", 0.15), pokemon("Miraidon")],
        [[move("Close Combat", [-2, 1, 2]), move("Aqua Jet", [-2, 1, 2])], [move("Spore", [-1, 1, 2])]],
    )

    action = play(memory, observation, template)

    assert action["slot_0"] == {"type": "move", "move_id": "closecombat", "target": 1}


def test_both_slots_do_not_pile_onto_a_foe_one_of_them_knocks_out_alone():
    strong = ["Close Combat"]
    memory = memory_of(
        [card("Urshifu-Rapid-Strike", strong), card("Iron Hands", strong)],
        [card("Incineroar", ["Flare Blitz"]), card("Milotic", ["Scald"])],
    )
    observation, template = doubles(
        [pokemon("Urshifu-Rapid-Strike"), pokemon("Iron Hands")],
        [pokemon("Incineroar", 0.1), pokemon("Milotic")],
        [[move("Close Combat", [-2, 1, 2])], [move("Close Combat", [-1, 1, 2])]],
    )

    action = play(memory, observation, template)

    assert {action["slot_0"]["target"], action["slot_1"]["target"]} == {1, 2}


# -- (4) Fake Out ----------------------------------------------------------------------------------


def _fake_out_turn(turn: int) -> tuple[dict, dict]:
    return doubles(
        [pokemon("Incineroar"), pokemon("Amoonguss")],
        [pokemon("Milotic"), pokemon("Vaporeon")],
        [[move("Fake Out", [1, 2]), move("Knock Off", [-2, 1, 2])], [move("Spore", [-1, 1, 2])]],
        turn=turn,
    )


def test_4_fake_out_is_used_on_the_first_turn_out_and_never_after():
    memory = memory_of(
        [card("Incineroar", ["Fake Out", "Knock Off"]), card("Amoonguss", ["Spore"])],
        [card("Milotic", ["Scald"]), card("Vaporeon", ["Scald"])],
    )
    tracker = BattleTracker()

    moves = [play(memory, *_fake_out_turn(turn), tracker)["slot_0"]["move_id"] for turn in (1, 2, 3, 4)]

    assert moves == ["fakeout", "knockoff", "knockoff", "knockoff"]


def test_fake_out_is_not_aimed_at_a_ghost_it_cannot_touch():
    memory = memory_of(
        [card("Incineroar", ["Fake Out", "Knock Off"]), card("Amoonguss", ["Spore"])],
        [card("Gholdengo", ["Make It Rain"]), card("Flutter Mane", ["Moonblast"])],
    )
    observation, template = doubles(
        [pokemon("Incineroar"), pokemon("Amoonguss")],
        [pokemon("Gholdengo"), pokemon("Flutter Mane")],
        [[move("Fake Out", [1, 2]), move("Knock Off", [-2, 1, 2])], [move("Spore", [-1, 1, 2])]],
    )

    action = play(memory, observation, template)

    assert action["slot_0"]["move_id"] == "knockoff"


# -- (5) Protect -----------------------------------------------------------------------------------


def _protect_turn(turn: int) -> tuple[dict, dict]:
    return doubles(
        [pokemon("Amoonguss", 0.2), pokemon("Incineroar")],
        [pokemon("Flutter Mane"), pokemon("Milotic")],
        [[move("Protect", [0]), move("Pollen Puff", [-2, 1, 2])], [move("Knock Off", [-1, 1, 2])]],
        turn=turn,
    )


def test_5_protect_is_preferred_under_a_likely_knockout_but_not_twice_in_a_row():
    memory = memory_of(
        [card("Amoonguss", ["Protect", "Pollen Puff"]), card("Incineroar", ["Knock Off"])],
        [card("Flutter Mane", ["Moonblast"], nature="Timid", evs={"spa": 252, "spe": 252}), card("Milotic", ["Scald"])],
    )
    tracker = BattleTracker()

    moves = [play(memory, *_protect_turn(turn), tracker)["slot_0"]["move_id"] for turn in (1, 2, 3)]

    assert moves == ["protect", "pollenpuff", "protect"]


def test_a_protect_after_n_in_a_row_scores_minus_protect_bonus_times_n_and_a_first_one_as_before():
    memory = memory_of(
        [card("Amoonguss", ["Protect", "Pollen Puff"]), card("Incineroar", ["Knock Off"])],
        [card("Flutter Mane", ["Moonblast"], nature="Timid", evs={"spa": 252, "spe": 252}), card("Milotic", ["Scald"])],
    )
    tuning = dataclasses.replace(TUNING, protect_bonus=60.0)
    tracker = BattleTracker()
    protect = {"type": "doubles_turn", "slot_0": {"type": "move", "move_id": "protect", "target": 0}}
    scores = []
    for turn in (1, 2, 3):
        board = read_board(*_protect_turn(turn), memory.set_for_species, Reader())
        tracker.observe(board)
        choices = Scorer(board, tracker, tuning).slot_choices(board.slots[0])
        scores.append(next(choice for choice in choices if choice.choice.get("move_id") == "protect"))
        tracker.note_choice(board, protect)

    assert [choice.score for choice in scores] == [60.0, -60.0, -120.0]
    assert [choice.label for choice in scores] == [
        "Protect (likely knockout)",
        "Protect (2 in a row)",
        "Protect (3 in a row)",
    ]


def test_protect_is_not_used_without_a_threat():
    memory = memory_of(
        [card("Amoonguss", ["Protect", "Pollen Puff"]), card("Incineroar", ["Knock Off"])],
        [card("Milotic", ["Scald"]), card("Vaporeon", ["Scald"])],
    )
    observation, template = doubles(
        [pokemon("Amoonguss"), pokemon("Incineroar")],
        [pokemon("Milotic"), pokemon("Vaporeon")],
        [[move("Protect", [0]), move("Pollen Puff", [-2, 1, 2])], [move("Knock Off", [-1, 1, 2])]],
    )

    assert play(memory, observation, template)["slot_0"]["move_id"] == "pollenpuff"


# -- (6) spread moves ------------------------------------------------------------------------------


def _earthquake_turn(ally: str, foes: tuple[str, str]) -> tuple[dict, dict]:
    return doubles(
        [pokemon("Garchomp"), pokemon(ally)],
        [pokemon(foes[0]), pokemon(foes[1])],
        [[move("Earthquake", [0]), move("Dragon Claw", [-2, 1, 2])], [move("Protect", [0])]],
    )


def test_6_a_spread_move_that_hits_our_ally_harder_than_the_foes_is_not_chosen():
    foes = ("Tornadus", "Corviknight")
    memory = memory_of(
        [card("Garchomp", ["Earthquake", "Dragon Claw"]), card("Incineroar", ["Protect"])],
        [card(name, ["Hurricane"]) for name in foes],
    )

    action = play(memory, *_earthquake_turn("Incineroar", foes))

    assert action["slot_0"]["move_id"] == "dragonclaw"


def test_a_spread_move_that_hits_the_foes_and_spares_a_flying_ally_is_chosen():
    foes = ("Incineroar", "Heatran")
    memory = memory_of(
        [card("Garchomp", ["Earthquake", "Dragon Claw"]), card("Corviknight", ["Protect"])],
        [card(name, ["Flare Blitz"]) for name in foes],
    )

    action = play(memory, *_earthquake_turn("Corviknight", foes))

    assert action["slot_0"] == {"type": "move", "move_id": "earthquake", "target": 0}


def test_a_move_that_makes_its_user_faint_counts_the_user_s_own_worth_against_it():
    memory = memory_of(
        [card("Regieleki", ["Explosion", "Thunderbolt"]), card("Corviknight", ["Protect"])],
        [card("Incineroar", ["Flare Blitz"]), card("Milotic", ["Scald"])],
    )
    observation, template = doubles(
        [pokemon("Regieleki"), pokemon("Corviknight")],
        [pokemon("Incineroar"), pokemon("Milotic")],
        [[move("Explosion", [0]), move("Thunderbolt", [-2, 1, 2])], [move("Protect", [0])]],
    )
    board = read_board(observation, template, memory.set_for_species, Reader())

    ranked = rank_actions(board, BattleTracker(), TUNING)
    explosion = [joint.slots[0].score for joint in ranked if joint.slots[0].choice.get("move_id") == "explosion"]

    assert play(memory, observation, template)["slot_0"]["move_id"] == "thunderbolt"
    assert explosion and max(explosion) < 0


def _labels_at_foe_a(item: str) -> set[str]:
    memory = memory_of(
        [card("Urshifu-Rapid-Strike", ["Close Combat", "Surging Strikes"]), card("Amoonguss", ["Spore"])],
        [card("Chi-Yu", ["Heat Wave"], item=item), card("Milotic", ["Scald"])],
    )
    observation, template = doubles(
        [pokemon("Urshifu-Rapid-Strike"), pokemon("Amoonguss")],
        [pokemon("Chi-Yu"), pokemon("Milotic")],
        [[move("Close Combat", [-2, 1, 2]), move("Surging Strikes", [-2, 1, 2])], [move("Spore", [-1, 1, 2])]],
    )
    board = read_board(observation, template, memory.set_for_species, Reader())
    return {
        joint.slots[0].label for joint in rank_actions(board, BattleTracker(), TUNING) if joint.slots[0].target == 1
    }


def test_a_focus_sash_at_full_hp_turns_a_single_hit_knockout_into_none_but_not_a_multi_hit_one():
    assert _labels_at_foe_a("Focus Sash") == {
        "Close Combat -> foe A chiyu: 100%, KO 0%",
        "Surging Strikes -> foe A chiyu: 100%, KO 100%",
    }
    assert "Close Combat -> foe A chiyu: 100%, KO 100%" in _labels_at_foe_a("Leftovers")


# -- (7) forced switches ---------------------------------------------------------------------------


def test_7_a_forced_switch_picks_the_reserve_that_resists_the_foes_strongest_move():
    memory = memory_of(
        [card("Rotom-Wash", ["Waterfall"]), card("Rotom-Mow", ["Seed Bomb"]), card("Incineroar", ["Knock Off"])],
        [card("Arcanine", ["Flare Blitz"]), card("Simisear", ["Flamethrower"])],
    )
    observation, template = doubles(
        [pokemon("Incineroar", 0.0, fainted=True), pokemon("Amoonguss")],
        [pokemon("Arcanine"), pokemon("Simisear")],
        [[switch("Rotom-Mow"), switch("Rotom-Wash")], [PASS]],
        reserves=[pokemon("Rotom-Mow", active=False), pokemon("Rotom-Wash", active=False)],
        force_switch=[True, False],
    )

    action = play(memory, observation, template)

    assert action == {"type": "doubles_turn", "slot_0": {"type": "switch", "species": "rotomwash"}, "slot_1": PASS}


def test_a_free_switch_is_made_only_out_of_a_likely_knockout_into_a_resist():
    memory = memory_of(
        [card("Amoonguss", ["Pollen Puff"]), card("Incineroar", ["Knock Off"]), card("Heatran", ["Flamethrower"])],
        [
            card("Arcanine", ["Flare Blitz"], item="Choice Band", nature="Adamant", evs={"atk": 252}),
            card("Milotic", ["Scald"]),
        ],
    )
    board = dict(reserves=[pokemon("Heatran", active=False)])
    threatened = doubles(
        [pokemon("Amoonguss", 0.4), pokemon("Incineroar")],
        [pokemon("Arcanine"), pokemon("Milotic")],
        [[move("Pollen Puff", [-2, 1, 2]), switch("Heatran")], [move("Knock Off", [-1, 1, 2])]],
        **board,
    )
    safe = doubles(
        [pokemon("Amoonguss"), pokemon("Incineroar")],
        [pokemon("Milotic"), pokemon("Vaporeon")],
        [[move("Pollen Puff", [-2, 1, 2]), switch("Heatran")], [move("Knock Off", [-1, 1, 2])]],
        **board,
    )

    assert play(memory, *threatened)["slot_0"] == {"type": "switch", "species": "heatran"}
    assert play(memory, *safe)["slot_0"]["type"] == "move"


# -- speed control ---------------------------------------------------------------------------------


def _tailwind_turn(**board: Any) -> tuple[dict, dict]:
    return doubles(
        [pokemon("Tornadus"), pokemon("Amoonguss")],
        [pokemon("Flutter Mane"), pokemon("Iron Bundle")],
        [[move("Tailwind", [0]), move("Gust", [-2, 1, 2])], [move("Spore", [-1, 1, 2])]],
        **board,
    )


def test_tailwind_is_set_when_it_turns_the_speed_order_our_way_and_not_while_it_is_up():
    memory = memory_of(
        [card("Tornadus", ["Tailwind", "Gust"]), card("Amoonguss", ["Spore"])],
        [card("Flutter Mane", ["Moonblast"]), card("Iron Bundle", ["Freeze-Dry"])],
    )

    assert play(memory, *_tailwind_turn())["slot_0"]["move_id"] == "tailwind"
    assert play(memory, *_tailwind_turn(side_conditions={"TAILWIND": 1}))["slot_0"]["move_id"] == "gust"


def test_trick_room_is_set_by_a_slower_team_and_not_while_it_is_up():
    memory = memory_of(
        [card("Farigiraf", ["Trick Room", "Tackle"]), card("Torkoal", ["Heat Wave"])],
        [card("Flutter Mane", ["Moonblast"]), card("Iron Bundle", ["Freeze-Dry"])],
    )
    options = [[move("Trick Room", [0]), move("Tackle", [-2, 1, 2])], [move("Heat Wave", [0])]]
    turn = doubles(
        [pokemon("Farigiraf"), pokemon("Torkoal")], [pokemon("Flutter Mane"), pokemon("Iron Bundle")], options
    )
    up = doubles(
        [pokemon("Farigiraf"), pokemon("Torkoal")],
        [pokemon("Flutter Mane"), pokemon("Iron Bundle")],
        options,
        fields={"TRICK_ROOM": 1},
    )

    assert play(memory, *turn)["slot_0"]["move_id"] == "trickroom"
    assert play(memory, *up)["slot_0"]["move_id"] == "tackle"


# -- what the model reads, and timing ---------------------------------------------------------------


def test_each_action_describes_both_slots_with_expected_damage_and_knockout_chance():
    memory = memory_of(
        [card("Urshifu-Rapid-Strike", ["Close Combat"]), card("Amoonguss", ["Spore"])],
        [card("Incineroar", ["Flare Blitz"]), card("Miraidon", ["Electro Drift"])],
    )
    observation, template = doubles(
        [pokemon("Urshifu-Rapid-Strike"), pokemon("Amoonguss")],
        [pokemon("Incineroar", 0.15), pokemon("Miraidon")],
        [[move("Close Combat", [-2, 1, 2])], [move("Spore", [-1, 1, 2]), PASS]],
    )
    board = read_board(observation, template, memory.set_for_species, Reader())

    ranked = rank_actions(board, BattleTracker(), TUNING)

    assert ranked[0].description().startswith("slot 0: Close Combat -> foe A incineroar: 15%, KO 100%; slot 1: Spore")
    assert ranked[-1].slots[1].score == PASS_SCORE


# -- an unranked description (model_decides) --------------------------------------------------------


def _ranked(memory, observation: dict, template: dict) -> tuple[Any, list]:
    board = read_board(observation, template, memory.set_for_species, Reader())
    return board, rank_actions(board, BattleTracker(), TUNING)


def _hit_text(where: str, defender, hit) -> str:
    return f"{where} {defender.species}: {hit.expected:.0f}%, KO {hit.ko_chance:.0%}"


def test_decides_an_unscored_spread_move_shows_each_target_s_expected_damage_and_knockout_chance_and_no_score():
    foes = ("Tornadus", "Incineroar")
    memory = memory_of(
        [card("Garchomp", ["Earthquake", "Dragon Claw"]), card("Arcanine", ["Protect"])],
        [card(name, ["Hurricane"]) for name in foes],
    )
    board, ranked = _ranked(memory, *_earthquake_turn("Arcanine", foes))
    quake = next(joint for joint in ranked if joint.slots[0].choice.get("move_id") == "earthquake")
    scorer = Scorer(board, BattleTracker(), TUNING)
    garchomp, earthquake = board.ours[0], board.move("earthquake")
    targets = [("foe A", board.theirs[0]), ("foe B", board.theirs[1]), ("our ally", board.ours[1])]
    hits = [
        _hit_text(where, fighter, scorer.hit(garchomp, earthquake, fighter, spread=True)) for where, fighter in targets
    ]

    unscored = quake.description(with_score=False)

    assert unscored == f"slot 0: Earthquake (spread) -> {'; '.join(hits)}; slot 1: {quake.slots[1].label}"
    assert "score" not in unscored and "(spread):" not in unscored
    assert quake.description() == (
        f"slot 0: Earthquake (spread): {quake.slots[0].score:.0f}; slot 1: {quake.slots[1].label}; "
        f"score {quake.score:.0f}"
    )


def test_decides_an_unscored_random_foe_move_shows_each_foe_s_figures_and_no_summed_value():
    memory = memory_of(
        [card("Dragonite", ["Outrage"]), card("Amoonguss", ["Spore"])],
        [card("Tornadus", ["Hurricane"]), card("Incineroar", ["Flare Blitz"])],
    )
    observation, template = doubles(
        [pokemon("Dragonite"), pokemon("Amoonguss")],
        [pokemon("Tornadus"), pokemon("Incineroar")],
        [[move("Outrage", [0])], [move("Spore", [-1, 1, 2])]],
    )
    board, ranked = _ranked(memory, observation, template)
    scorer = Scorer(board, BattleTracker(), TUNING)
    dragonite, outrage = board.ours[0], board.move("outrage")
    hits = [
        _hit_text(where, foe, scorer.hit(dragonite, outrage, foe, spread=False))
        for where, foe in (("foe A", board.theirs[0]), ("foe B", board.theirs[1]))
    ]

    slot_0 = ranked[0].slots[0]

    assert slot_0.unscored_label == f"Outrage (random foe) -> {' or '.join(hits)}"
    assert slot_0.label == f"Outrage (random foe): {slot_0.score:.0f}"


def test_decides_an_unscored_forced_switch_leaves_out_its_matchup():
    memory = memory_of(
        [card("Rotom-Wash", ["Waterfall"]), card("Rotom-Mow", ["Seed Bomb"]), card("Incineroar", ["Knock Off"])],
        [card("Arcanine", ["Flare Blitz"]), card("Simisear", ["Flamethrower"])],
    )
    observation, template = doubles(
        [pokemon("Incineroar", 0.0, fainted=True), pokemon("Amoonguss")],
        [pokemon("Arcanine"), pokemon("Simisear")],
        [[switch("Rotom-Mow"), switch("Rotom-Wash")], [PASS]],
        reserves=[pokemon("Rotom-Mow", active=False), pokemon("Rotom-Wash", active=False)],
        force_switch=[True, False],
    )
    _, ranked = _ranked(memory, observation, template)

    assert ranked[0].description(with_score=False) == "slot 0: switch to rotomwash; slot 1: pass"
    assert ranked[0].description().startswith("slot 0: switch to rotomwash (matchup ")


def test_8_building_a_turn_takes_under_100_ms():
    rng, catalog = random.Random(11), load_catalog()
    memory, observation, template = random_turn(rng, catalog)
    data.shipped()

    started = time.perf_counter()
    board = read_board(observation, template, memory.set_for_species, Reader())
    rank_actions(board, BattleTracker(), TUNING)

    assert time.perf_counter() - started < BUILD_LIMIT_SECONDS


# -- maximin ---------------------------------------------------------------------------------------

MAXIMIN = Tuning.from_file(REPO_ROOT).maximin
MAXIMIN_LIMIT_SECONDS = 0.3
MAXIMIN_RUNS = 20
OCCUPIED = {-1, -2, 1, 2}


def _sides(memory, observation: dict, template: dict) -> tuple[Side, Side]:
    """Both sides' views of one turn, each with a tracker that has taken it in."""
    board = read_board(observation, template, memory.set_for_species, Reader())
    ours, theirs = Side(board, BattleTracker()), Side(opponent_view(board), BattleTracker())
    ours.tracker.observe(ours.board)
    theirs.tracker.observe(theirs.board)
    return ours, theirs


def _maximin(ours: Side, theirs: Side, until: float | None = None) -> tuple[list, list | None]:
    """The script's ranking and the maximin ranking of one turn."""
    ranked = rank_actions(ours.board, ours.tracker, TUNING)
    limit = time.perf_counter() + 10 if until is None else until
    return ranked, rank_turn(ours, theirs, ranked, TUNING, MAXIMIN, limit)


def _protect_or_attack_turn() -> tuple[Side, Side]:
    memory = memory_of(
        [card("Urshifu-Rapid-Strike", ["Close Combat", "Protect"]), card("Rillaboom", ["Wood Hammer", "Protect"])],
        [
            card("Flutter Mane", ["Moonblast", "Shadow Ball"], nature="Timid", evs={"spa": 252, "spe": 252}),
            card("Incineroar", ["Flare Blitz", "Knock Off"]),
        ],
    )
    observation, template = doubles(
        [pokemon("Urshifu-Rapid-Strike", 0.35), pokemon("Rillaboom")],
        [pokemon("Flutter Mane"), pokemon("Incineroar", 0.45)],
        [
            [move("Close Combat", [-2, 1, 2]), move("Protect", [0])],
            [move("Wood Hammer", [-1, 1, 2]), move("Protect", [0])],
        ],
    )
    return _sides(memory, observation, template)


def test_maximin_1_protect_plus_the_partner_s_attack_beats_an_attack_the_faster_foe_knocks_out_first():
    ours, theirs = _protect_or_attack_turn()

    ranked, weighed = _maximin(ours, theirs)

    assert ranked[0].action["slot_0"] == {"type": "move", "move_id": "closecombat", "target": 2}
    best = weighed[0]
    assert best.joint.action["slot_0"]["move_id"] == "protect"
    assert best.joint.action["slot_1"] == {"type": "move", "move_id": "woodhammer", "target": 1}
    attack = next(action for action in weighed if action.joint is ranked[0])
    assert attack.worst < best.worst and attack.outcome.lost > best.outcome.lost
    assert attack.reply.action["slot_0"] == {"type": "move", "move_id": "moonblast", "target": 1}


CHAIN_TURN = 5
PROTECT_CHOICE = {"type": "move", "move_id": "protect", "target": 0}


def _taken_through_protect(chain: int | None) -> float:
    """The HP percent our Urshifu at 35% loses to Flutter Mane's Moonblast when it Protects after `chain` Protects in
    a row, or when it does not Protect (None); every other slot passes."""
    ours, theirs = _protect_or_attack_turn()
    board = dataclasses.replace(ours.board, turn=CHAIN_TURN)
    for turn in range(CHAIN_TURN - (chain or 0), CHAIN_TURN):
        ours.tracker.note_choice(dataclasses.replace(board, turn=turn), {"slot_0": PROTECT_CHOICE})
    urshifu = SlotChoice(PROTECT_CHOICE, 0.0, "Protect") if chain is not None else PASS_CHOICE
    moonblast = SlotChoice({"type": "move", "move_id": "moonblast", "target": 1}, 0.0, "Moonblast -> Urshifu")
    model = OneTurnModel(Side(board, ours.tracker), theirs, TUNING)
    return model.play(
        JointAction({}, 0.0, (urshifu, PASS_CHOICE)), JointAction({}, 0.0, (moonblast, PASS_CHOICE))
    ).taken


def test_maximin_a_protect_after_n_in_a_row_shields_its_user_with_chance_a_third_to_the_n():
    unshielded = _taken_through_protect(None)

    assert [shield_chance(chain) for chain in (0, 1, 2)] == pytest.approx([1, 1 / 3, 1 / 9])
    assert unshielded > 0
    assert [_taken_through_protect(chain) / unshielded for chain in (0, 1, 2)] == pytest.approx([0, 2 / 3, 8 / 9])


def _match_memory(landorus_item: str):
    """Match 0556bdb3's sets, as the draft's cards gave them, Landorus-Therian holding `landorus_item`."""
    return memory_of(
        [
            card(
                "Lucario",
                ["Close Combat", "Bullet Punch", "Poison Jab", "Protect"],
                "Life Orb",
                "Inner Focus",
                "Jolly",
                {"hp": 4, "atk": 252, "spe": 252},
            ),
            card(
                "Landorus-Therian",
                ["Earthquake", "Rock Slide", "U-turn", "Protect"],
                landorus_item,
                "Intimidate",
                "Jolly",
                {"atk": 252, "def": 4, "spe": 252},
            ),
        ],
        [
            card(
                "Archaludon",
                ["Body Press", "Draco Meteor", "Flash Cannon", "Protect"],
                "Leftovers",
                "Stamina",
                "Bold",
                {"hp": 252, "def": 252, "spa": 4},
            ),
            card(
                "Cresselia",
                ["Icy Wind", "Moonblast", "Helping Hand", "Protect"],
                "Sitrus Berry",
                "Levitate",
                "Bold",
                {"hp": 252, "def": 252, "spd": 4},
            ),
        ],
    )


LUCARIO_TURN_5_HP = 45 / 146
LANDORUS_MOVES = ("Earthquake", "Rock Slide", "U-turn", "Protect")


def _match_0556bdb3_board(
    turn: int,
    landorus_item: str = "Choice Scarf",
    landorus_hp: float = 1.0,
    landorus_moves: tuple[str, ...] = LANDORUS_MOVES,
    **board_fields,
):
    """Match 0556bdb3's board as of turn 5, numbered `turn`: our Lucario at 45/146 with -1 Def, SpD and Spe beside
    Landorus-Therian (full HP and Choice Scarf unless given), against Archaludon at 28% (-1 Atk, +1 Def) and Cresselia
    at 74% (-2 Atk). Landorus is offered all four of its moves unless given; the match's own template offered it
    Protect alone, Choice Scarf having locked it."""
    landorus_targets = {"Earthquake": [0], "Rock Slide": [0], "U-turn": [-1, 1, 2], "Protect": [0]}
    observation, template = doubles(
        [
            pokemon("Lucario", LUCARIO_TURN_5_HP, boosts={"def": -1, "spd": -1, "spe": -1}),
            pokemon("Landorus-Therian", landorus_hp),
        ],
        [pokemon("Archaludon", 0.28, boosts={"atk": -1, "def": 1}), pokemon("Cresselia", 0.74, boosts={"atk": -2})],
        [
            [move(name, [-2, 1, 2]) for name in ("Close Combat", "Bullet Punch", "Poison Jab")]
            + [move("Protect", [0])],
            [move(name, landorus_targets[name]) for name in landorus_moves],
        ],
        turn=turn,
        **board_fields,
    )
    return read_board(observation, template, _match_memory(landorus_item).set_for_species, Reader())


def _landorus_protects(joint) -> bool:
    return joint.action["slot_1"].get("move_id") == "protect"


def test_maximin_match_0556bdb3_turn_5_landorus_after_two_protects_in_a_row_is_not_given_a_third():
    ours, theirs = BattleTracker(), BattleTracker()
    close_combat = {"type": "move", "move_id": "closecombat", "target": 1}
    for turn, lucario in ((3, close_combat), (4, PROTECT_CHOICE)):
        board = _match_0556bdb3_board(turn)
        ours.observe(board)
        theirs.observe(opponent_view(board))
        ours.note_choice(board, {"type": "doubles_turn", "slot_0": lucario, "slot_1": PROTECT_CHOICE})
    board = _match_0556bdb3_board(5)
    ours.observe(board)
    their_board = opponent_view(board)
    theirs.observe(their_board)

    ranked, weighed = _maximin(Side(board, ours), Side(their_board, theirs))

    landorus_protect = {joint.slots[1].score for joint in ranked if _landorus_protects(joint)}
    assert landorus_protect == {-2 * TUNING.protect_bonus} == {-120.0}
    assert not any(_landorus_protects(action.joint) for action in weighed)
    assert not _landorus_protects(weighed[0].joint)


# Under Trick Room both foes move first, and Cresselia's Icy Wind likely knocks Landorus out at 60%.
UNDER_TRICK_ROOM = {"fields": {"trickroom": 3}}
LANDORUS_THREATENED_HP = 0.6


def _threatened_landorus(item: str, moves: tuple[str, ...] = LANDORUS_MOVES):
    return _match_0556bdb3_board(5, item, LANDORUS_THREATENED_HP, moves, **UNDER_TRICK_ROOM)


def _landorus_protect(board) -> SlotChoice:
    choices = Scorer(board, BattleTracker(), TUNING).slot_choices(board.slots[1])
    return next(choice for choice in choices if choice.choice.get("move_id") == "protect")


def test_a_choice_scarf_holder_s_protect_beside_a_damaging_move_scores_minus_protect_bonus_and_is_not_chosen():
    scarf, leftovers = _threatened_landorus("Choice Scarf"), _threatened_landorus("Leftovers")

    locking = _landorus_protect(scarf)

    assert (locking.score, locking.label, locking.choice_lock) == (-TUNING.protect_bonus, "Protect (Choice lock)", True)
    assert not _landorus_protects(rank_actions(scarf, BattleTracker(), TUNING)[0])
    unlocked = _landorus_protect(leftovers)
    assert (unlocked.score, unlocked.label, unlocked.choice_lock) == (
        TUNING.protect_bonus,
        "Protect (likely knockout)",
        False,
    )
    assert _landorus_protects(rank_actions(leftovers, BattleTracker(), TUNING)[0])


def test_a_choice_scarf_holder_offered_protect_alone_still_plays_it_scored_as_any_protect():
    locked = _threatened_landorus("Choice Scarf", ("Protect",))

    ranked = rank_actions(locked, BattleTracker(), TUNING)

    assert ranked and all(_landorus_protects(joint) for joint in ranked)
    assert (_landorus_protect(locked).score, _landorus_protect(locked).choice_lock) == (TUNING.protect_bonus, False)


def test_maximin_leaves_joint_actions_holding_a_choice_locking_protect_out_of_our_top():
    board = _threatened_landorus("Choice Scarf")
    ours, theirs = Side(board, BattleTracker()), Side(opponent_view(board), BattleTracker())
    ours.tracker.observe(ours.board)
    theirs.tracker.observe(theirs.board)
    ranked = rank_actions(board, ours.tracker, TUNING)
    locking = [joint for joint in ranked if locks_choice(joint)]
    locking_first = locking + [joint for joint in ranked if not locks_choice(joint)]

    weighed = rank_turn(ours, theirs, locking_first, TUNING, MAXIMIN, time.perf_counter() + 10)

    assert locking and all(_landorus_protects(joint) for joint in locking)
    assert len(weighed) == MAXIMIN.our_top
    assert not any(_landorus_protects(action.joint) or locks_choice(action.joint) for action in weighed)


def test_maximin_ranks_by_the_worst_result_then_the_mean_and_keeps_the_reply_that_gave_the_worst():
    ours, theirs = _protect_or_attack_turn()
    model = OneTurnModel(ours, theirs, TUNING)
    replies = rank_actions(theirs.board, theirs.tracker, TUNING)[: MAXIMIN.their_top]

    _ranked, weighed = _maximin(ours, theirs)

    assert [(action.worst, action.mean) for action in weighed] == sorted(
        ((action.worst, action.mean) for action in weighed), reverse=True
    )
    for action in weighed:
        values = [model.play(action.joint, reply).value(TUNING.ko_bonus) for reply in replies]
        assert action.worst == pytest.approx(min(values)) and action.mean == pytest.approx(sum(values) / len(values))
        assert model.play(action.joint, action.reply).value(TUNING.ko_bonus) == pytest.approx(action.worst)


def test_maximin_the_opponent_s_view_offers_their_known_moves_at_our_actives_and_switches_into_their_seen_bench():
    memory = memory_of(
        [card("Incineroar", ["Flare Blitz"]), card("Amoonguss", ["Spore"])],
        [card("Flutter Mane", ["Moonblast", "Protect"]), card("Garchomp", ["Earthquake"]), card("Milotic", ["Scald"])],
    )
    observation, template = doubles(
        [pokemon("Incineroar"), pokemon("Amoonguss")],
        [pokemon("Flutter Mane"), None],
        [[move("Flare Blitz", [-2, 1, 2])], [move("Spore", [-1, 1, 2])]],
    )
    observation["opponent_team"]["p2: Garchomp"] = pokemon("Garchomp", active=False)
    observation["opponent_team"]["p2: Milotic"] = pokemon("Milotic", 0.0, fainted=True, active=False)
    board = read_board(observation, template, memory.set_for_species, Reader())

    view = opponent_view(board)

    flutter_mane, empty = view.slots
    assert [(option.kind, option.move_id or option.species, option.targets) for option in flutter_mane.options] == [
        ("move", "moonblast", (1, 2)),
        ("move", "protect", (0,)),
        ("switch", "garchomp", ()),
    ]
    assert [option.kind for option in empty.options] == ["pass"]
    assert view.ours == board.theirs and view.theirs == board.ours
    assert (view.our_conditions, view.their_conditions) == (board.their_conditions, board.our_conditions)


def _fake_out_against_heat_wave(turn: int):
    """Our Fake Out at Chi-Yu against its Heat Wave, on the turn our Incineroar came in or two turns later."""
    memory = memory_of(
        [card("Incineroar", ["Fake Out", "Knock Off"]), card("Amoonguss", ["Pollen Puff"])],
        [card("Chi-Yu", ["Heat Wave"]), card("Milotic", ["Scald"])],
    )
    observation, template = doubles(
        [pokemon("Incineroar"), pokemon("Amoonguss")],
        [pokemon("Chi-Yu"), pokemon("Milotic")],
        [[move("Fake Out", [1, 2]), move("Knock Off", [-2, 1, 2])], [move("Pollen Puff", [-1, 1, 2])]],
        turn=turn,
    )
    board = read_board(observation, template, memory.set_for_species, Reader())
    ours, theirs = Side(board, BattleTracker()), Side(opponent_view(board), BattleTracker())
    for side in (ours, theirs):
        side.tracker.observe(dataclasses.replace(side.board, turn=1))
        side.tracker.observe(side.board)
    fake_out = next(joint for joint in rank_actions(board, ours.tracker, TUNING) if _aims(joint, "fakeout", 1))
    heat_wave = next(joint for joint in rank_actions(theirs.board, theirs.tracker, TUNING) if _aims(joint, "heatwave"))
    return OneTurnModel(ours, theirs, TUNING).play(fake_out, heat_wave)


def _aims(joint, move_id: str, target: int | None = None) -> bool:
    slot_0 = joint.action["slot_0"]
    return slot_0.get("move_id") == move_id and (target is None or slot_0["target"] == target)


def test_maximin_fake_out_on_the_first_turn_out_makes_its_target_flinch_and_fails_after():
    first_turn, later = _fake_out_against_heat_wave(1), _fake_out_against_heat_wave(3)

    assert first_turn.dealt > later.dealt
    assert later.taken > first_turn.taken


def test_maximin_a_move_at_a_foe_that_has_fallen_turns_to_its_partner():
    strong = ["Close Combat"]
    memory = memory_of(
        [card("Urshifu-Rapid-Strike", strong), card("Iron Hands", strong)],
        [card("Incineroar", ["Flare Blitz"]), card("Milotic", ["Scald"])],
    )
    observation, template = doubles(
        [pokemon("Urshifu-Rapid-Strike"), pokemon("Iron Hands")],
        [pokemon("Incineroar", 0.05), pokemon("Milotic")],
        [[move("Close Combat", [-2, 1, 2])], [move("Close Combat", [-1, 1, 2])]],
    )
    ours, theirs = _sides(memory, observation, template)
    model = OneTurnModel(ours, theirs, TUNING)
    both_at_a = next(joint for joint in rank_actions(ours.board, ours.tracker, TUNING) if _both_at(joint, 1))
    only_slot_0 = dataclasses.replace(both_at_a, slots=(both_at_a.slots[0], PASS_CHOICE))

    turned = model.play(both_at_a, IDLE).dealt - model.play(only_slot_0, IDLE).dealt

    assert turned > 0 and model.play(only_slot_0, IDLE).knocked_out == pytest.approx(1.0)


def _both_at(joint, target: int) -> bool:
    return joint.action["slot_0"].get("target") == target and joint.action["slot_1"].get("target") == target


def test_maximin_gives_nothing_once_its_time_limit_has_passed():
    ours, theirs = _protect_or_attack_turn()

    _ranked, weighed = _maximin(ours, theirs, until=time.perf_counter() - 1)

    assert weighed is None


def test_maximin_ranks_only_legal_template_actions_over_100_random_observations():
    rng, catalog = random.Random(26), load_catalog()
    for _board in range(RANDOM_BOARDS // 2):
        memory, observation, template = random_turn(rng, catalog)

        ranked, weighed = _maximin(*_sides(memory, observation, template))
        unlocked = [joint for joint in ranked if not locks_choice(joint)]

        assert len(weighed) == min(MAXIMIN.our_top, len(unlocked))
        assert all(legal(template, action.joint.action) for action in weighed)
        assert {id(action.joint) for action in weighed} == {id(joint) for joint in unlocked[: MAXIMIN.our_top]}


def _full_board():
    """2 versus 2, 4 moves each and 2 benched Pokémon a side, every move with the targets the harness gives."""
    ours = [
        card("Incineroar", ["Fake Out", "Flare Blitz", "Knock Off", "Protect"]),
        card("Flutter Mane", ["Moonblast", "Shadow Ball", "Dazzling Gleam", "Protect"]),
        card("Rillaboom", ["Wood Hammer", "Grassy Glide", "Fake Out", "Protect"]),
        card("Urshifu-Rapid-Strike", ["Surging Strikes", "Close Combat", "Aqua Jet", "Protect"]),
    ]
    theirs = [
        card("Amoonguss", ["Pollen Puff", "Spore", "Rage Powder", "Protect"]),
        card("Chi-Yu", ["Heat Wave", "Dark Pulse", "Overheat", "Protect"]),
        card("Garchomp", ["Earthquake", "Dragon Claw", "Rock Slide", "Protect"]),
        card("Tornadus", ["Tailwind", "Bleakwind Storm", "Taunt", "Protect"]),
    ]
    bench = [entry["species"] for entry in ours[2:]]
    options = [
        [move(name, move_targets(slot, data.move(name).target, OCCUPIED)) for name in ours[slot]["moves"]]
        + [switch(species) for species in bench]
        for slot in (0, 1)
    ]
    observation, template = doubles(
        [pokemon(entry["species"]) for entry in ours[:2]],
        [pokemon(entry["species"]) for entry in theirs[:2]],
        options,
        reserves=[pokemon(species, active=False) for species in bench],
    )
    for entry in theirs[2:]:
        observation["opponent_team"][f"p2: {entry['species']}"] = pokemon(entry["species"], active=False)
    return memory_of(ours, theirs), observation, template


def test_maximin_4_the_turn_evaluation_of_8_against_8_on_a_full_board_takes_under_300_ms():
    memory, observation, template = _full_board()
    data.shipped()
    slowest = 0.0
    for _run in range(MAXIMIN_RUNS):
        started = time.perf_counter()
        ours, theirs = _sides(memory, observation, template)
        ranked, weighed = _maximin(ours, theirs)
        slowest = max(slowest, time.perf_counter() - started)
        assert len(ranked) >= MAXIMIN.our_top and len(weighed) == MAXIMIN.our_top
        assert len(rank_actions(theirs.board, theirs.tracker, TUNING)) >= MAXIMIN.their_top
        assert len(theirs.board.reserves) == 2 and len(ours.board.reserves) == 2

    assert (MAXIMIN.our_top, MAXIMIN.their_top) == (8, 8)
    assert slowest < MAXIMIN_LIMIT_SECONDS
