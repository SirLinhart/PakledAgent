"""The battle board read from a doubles observation and its template, with each key's default, and the tracker."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pokemon_states import PASS, card, doubles, memory_of, move, pokemon, switch

from ucla_agent.games.pokemon import data
from ucla_agent.games.pokemon.battle import BattleTracker, Option, read_board
from ucla_agent.games.pokemon.draft import THEIRS, DraftMemory
from ucla_agent.games.pokemon.observe import Reader, UnexpectedState
from ucla_agent.practice.pokemon.battle_log import BattleLog
from ucla_agent.practice.pokemon.cards import load_catalog
from ucla_agent.practice.pokemon.doubles import DoublesView

REPO_ROOT = Path(__file__).resolve().parents[2]
SHOWDOWN = json.loads(
    (REPO_ROOT / "tests" / "fixtures" / "pokemon_practice" / "showdown_p1_view.json").read_text("utf-8")
)
OURS = [card("Incineroar", ["Fake Out", "Flare Blitz"], "Assault Vest", "Intimidate")]
THEIRS_CARDS = [card("Miraidon", ["Electro Drift"], "Choice Specs", "Hadron Engine")]


def lookup_of(memory: DraftMemory):
    return memory.set_for_species


def real_turn() -> tuple[dict, dict]:
    """The practice harness's observation and template of the real Showdown move request (turn 1)."""
    catalog = load_catalog()
    log = BattleLog("p1")
    log.read(SHOWDOWN["move_log"])
    view = DoublesView(SHOWDOWN["move_request"], log, catalog, "p1", catalog.battle_format)
    return view.observation(), view.legal_action("s", 0)["input"]["action"]


def basic(**changes) -> tuple[dict, dict]:
    return doubles(
        [pokemon("Incineroar", 0.5, status="BRN", boosts={"atk": -1}), pokemon("Rillaboom")],
        [pokemon("Miraidon", 0.25), None],
        [[move("Flare Blitz", [-2, 1]), switch("Amoonguss")], [move("Grassy Glide", [-1, 1]), PASS]],
        reserves=[pokemon("Amoonguss", 0.75, active=False)],
        **changes,
    )


def test_the_real_showdown_turn_reads_with_no_degradation():
    observation, template = real_turn()
    reader = Reader()

    board = read_board(observation, template, lambda side, species: None, reader)

    assert reader.degraded_keys == []
    assert [fighter.species for fighter in board.ours] == ["urshifurapidstrike", "incineroar"]
    assert [fighter.species if fighter else None for fighter in board.theirs] == ["miraidon", "fluttermane"]
    assert board.slots[1].options[0] == Option("move", move_id="fakeout", targets=(-1, 1, 2))
    assert all(fighter.set is not None for fighter in [*board.ours, *board.theirs])
    assert set(board.reserves) == {"amoonguss", "rillaboom"} and board.terrain == "electricterrain"


def test_a_hand_built_board_reads_hp_status_stages_positions_and_reserves():
    memory = memory_of(OURS, THEIRS_CARDS)
    observation, template = basic()

    board = read_board(observation, template, lookup_of(memory), Reader())
    incineroar = board.own(board.slots[0])

    assert (incineroar.hp_fraction, incineroar.status, incineroar.boosts["atk"]) == (0.5, "brn", -1)
    assert (incineroar.set.item, board.at(1).set.item) == ("assaultvest", "choicespecs")
    assert (board.at(2), board.at(-2).species, board.at(0)) == (None, "rillaboom", None)
    assert (board.reserves["amoonguss"].hp_fraction, board.reserves["amoonguss"].active) == (0.75, False)
    assert (board.force_switch, board.turn) == ((False, False), 1)


def test_weather_fields_and_side_conditions_become_showdown_ids():
    observation, template = basic(
        weather={"RAINDANCE": 2},
        fields={"ELECTRIC_TERRAIN": 1, "TRICK_ROOM": 3},
        side_conditions={"TAILWIND": 2, "REFLECT": 1},
        opponent_side_conditions={"LIGHT_SCREEN": 1},
    )

    board = read_board(observation, template, lambda side, species: None, Reader())

    assert (board.weather, board.terrain, board.trick_room) == ("raindance", "electricterrain", True)
    assert board.our_conditions == {"tailwind", "reflect"} and board.their_conditions == {"lightscreen"}


def test_missing_boosts_and_weather_count_two_degradations_and_read_as_none():
    observation, template = basic()
    del observation["weather"]
    for entry in [*observation["active_pokemon"], *observation["opponent_active_pokemon"]]:
        if entry is not None:
            del entry["boosts"]
    reader = Reader()

    board = read_board(observation, template, lambda side, species: None, reader)

    assert reader.degraded_keys == ["boosts", "weather"] and reader.degradations == 2
    assert board.weather is None and board.own(board.slots[0]).boosts == {}


def test_without_actives_ours_come_from_the_template_and_theirs_from_the_opponent_team():
    observation, template = basic()
    del observation["active_pokemon"], observation["opponent_active_pokemon"]
    reader = Reader()

    board = read_board(observation, template, lambda side, species: None, reader)

    assert [fighter.species for fighter in board.ours] == ["incineroar", "rillaboom"]
    assert board.ours[0].hp_fraction == 1.0 and board.at(1).species == "miraidon"
    assert reader.degraded_keys == ["active_pokemon", "opponent_active_pokemon"]


def test_without_force_switch_a_slot_offered_only_switches_must_switch():
    observation, template = basic()
    template["slots"][1]["options"] = [switch("Amoonguss"), PASS]
    del observation["force_switch"]
    reader = Reader()

    board = read_board(observation, template, lambda side, species: None, reader)

    assert board.force_switch == (False, True) and reader.degraded_keys == ["force_switch"]


def test_a_pokemon_s_hp_falls_back_to_its_counts_and_a_missing_species_leaves_it_off_the_board():
    observation, template = basic()
    del observation["opponent_active_pokemon"][0]["current_hp_fraction"]
    observation["active_pokemon"][1] = {"name": "Rillaboom"}
    reader = Reader()

    board = read_board(observation, template, lambda side, species: None, reader)

    assert board.at(1).hp_fraction == 0.25 and board.at(-2) is None
    assert "current_hp_fraction" in reader.degraded_keys and "species" in reader.degraded_keys


def test_options_and_targets_that_are_off_their_shape_count_and_are_left_out_or_defaulted():
    observation, template = basic()
    template["slots"][0]["options"] = [{"type": "dance"}, {"type": "move", "move_id": "flareblitz"}]
    reader = Reader()

    board = read_board(observation, template, lambda side, species: None, reader)

    assert board.slots[0].options == (Option("move", move_id="flareblitz", targets=(0,)),)
    assert reader.degraded_keys == ["options", "targets"]


@pytest.mark.parametrize(
    "edit",
    [
        lambda observation, template: template.update(slots=template["slots"][:1]),
        lambda observation, template: template.pop("slots"),
        lambda observation, template: observation.update(is_doubles=False),
    ],
    ids=["one slot", "no slots", "singles"],
)
def test_a_board_that_is_no_doubles_turn_is_an_unexpected_state(edit):
    observation, template = basic()
    edit(observation, template)

    with pytest.raises(UnexpectedState):
        read_board(observation, template, lambda side, species: None, Reader())


def test_an_observation_that_is_no_object_is_an_unexpected_state():
    _observation, template = basic()

    with pytest.raises(UnexpectedState):
        read_board("garbled", template, lambda side, species: None, Reader())


def test_a_move_the_data_lacks_takes_the_facts_available_moves_gives():
    observation, template = basic()
    details = copy.deepcopy(observation["available_moves"][0][0]) | {"id": "newmove", "name": "New Move"}
    observation["available_moves"][0].append(details)

    board = read_board(observation, template, lambda side, species: None, Reader())

    assert data.move("newmove") is None
    assert (board.move("newmove").type, board.move("newmove").accuracy) == ("Fire", 100)
    assert board.move("flareblitz") == data.move("flareblitz") and board.move("nothing") is None


def test_an_opponent_without_a_drafted_card_stands_as_the_facts_of_its_entry():
    memory = memory_of(OURS, [])
    observation, template = basic()
    observation["opponent_active_pokemon"][0]["item"] = "choicespecs"

    board = read_board(observation, template, lookup_of(memory), Reader())

    assert memory.set_for_species(THEIRS, "miraidon") is None
    assert board.at(1).set.item == "choicespecs" and board.at(1).set.moves == ()


# -- the tracker -----------------------------------------------------------------------------------


def board_at(turn: int, slot_0: str = "Incineroar", **changes):
    observation, template = doubles(
        [pokemon(slot_0), pokemon("Rillaboom")],
        [pokemon("Miraidon"), pokemon("Chien-Pao")],
        [[move("Fake Out", [1, 2]), move("Protect", [0])], [move("Fake Out", [1, 2])]],
        turn=turn,
        **changes,
    )
    return read_board(observation, template, lambda side, species: None, Reader())


def test_a_slot_s_first_turn_out_is_the_turn_its_species_arrived():
    tracker = BattleTracker()
    first, second, swapped, after = board_at(1), board_at(2), board_at(3, "Amoonguss"), board_at(4, "Amoonguss")

    seen = []
    for board in (first, second, swapped, after):
        tracker.observe(board)
        seen.append(tracker.first_turn_out(board, board.slots[0]))

    assert seen == [True, False, True, False]


PROTECT = {"type": "doubles_turn", "slot_0": {"type": "move", "move_id": "protect", "target": 0}}
FAKE_OUT = {"type": "doubles_turn", "slot_0": {"type": "move", "move_id": "fakeout", "target": 1}}


def chain_after(choices: list[dict]) -> int:
    """Slot 0's Protect chain on the turn after `choices`, one choice a turn from turn 1, as one tracker saw them."""
    tracker = BattleTracker()
    for turn, choice in enumerate(choices, start=1):
        board = board_at(turn)
        tracker.observe(board)
        tracker.note_choice(board, choice)
    following = board_at(len(choices) + 1)
    tracker.observe(following)
    return tracker.protect_chain(following, following.slots[0])


def test_protect_is_remembered_for_the_next_turn_only_and_a_switch_forgets_it():
    tracker = BattleTracker()
    boards = [board_at(turn) for turn in (1, 2, 3)]
    for board in boards:
        tracker.observe(board)

    tracker.note_choice(boards[0], PROTECT)
    assert tracker.protect_chain(boards[1], boards[1].slots[0]) == 1
    assert tracker.protect_chain(boards[2], boards[2].slots[0]) == 0
    assert tracker.last_move(boards[1], boards[1].slots[1]) is None

    tracker.note_choice(boards[1], {"slot_0": {"type": "switch", "species": "amoonguss"}})
    assert tracker.protect_chain(boards[2], boards[2].slots[0]) == 0


def test_a_protect_by_a_pokemon_that_has_since_left_does_not_count():
    tracker = BattleTracker()
    before, after = board_at(1), board_at(2, "Amoonguss")
    tracker.observe(before)
    tracker.note_choice(before, PROTECT)
    tracker.observe(after)

    assert tracker.protect_chain(after, after.slots[0]) == 0


def test_the_protect_chain_counts_protects_in_a_row_and_any_other_move_starts_it_again():
    assert chain_after([PROTECT]) == 1
    assert chain_after([PROTECT, PROTECT]) == 2
    assert chain_after([PROTECT, PROTECT, PROTECT]) == 3
    assert chain_after([PROTECT, FAKE_OUT, PROTECT]) == 1
    assert chain_after([PROTECT, PROTECT, FAKE_OUT]) == 0


def test_detect_continues_a_chain_of_protect_like_moves():
    detect = {"type": "doubles_turn", "slot_0": {"type": "move", "move_id": "detect", "target": 0}}

    assert chain_after([PROTECT, detect]) == 2


def test_a_switch_or_another_species_in_the_slot_resets_the_protect_chain():
    switch_out = {"type": "doubles_turn", "slot_0": {"type": "switch", "species": "amoonguss"}}
    tracker = BattleTracker()
    for board in (board_at(1), board_at(2)):
        tracker.observe(board)
        tracker.note_choice(board, PROTECT)
    swapped = board_at(3, "Amoonguss")
    tracker.observe(swapped)

    assert chain_after([PROTECT, PROTECT, switch_out]) == 0
    assert tracker.protect_chain(swapped, swapped.slots[0]) == 0
