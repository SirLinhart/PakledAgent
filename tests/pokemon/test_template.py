"""The template-only fallback per phase, and the check of a lineup or turn against its template."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pokemon_states import PASS, doubles, envelope, legal_action, move, pokemon, switch

from altruagent import WAIT, LegalAction
from ucla_agent.games.pokemon.template import fallback, is_legal, template_of
from ucla_agent.practice.pokemon.battle_log import BattleLog
from ucla_agent.practice.pokemon.cards import load_catalog
from ucla_agent.practice.pokemon.doubles import DoublesView
from ucla_agent.practice.pokemon.preview import lineup_choice, lineup_legal_action

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pokemon_practice"
SHOWDOWN = json.loads((FIXTURES / "showdown_p1_view.json").read_text(encoding="utf-8"))


def view(request_key: str, log_key: str, edit=None) -> DoublesView:
    catalog = load_catalog()
    request = copy.deepcopy(SHOWDOWN[request_key])
    if edit is not None:
        edit(request)
    log = BattleLog("p1")
    log.read(SHOWDOWN[log_key])
    return DoublesView(request, log, catalog, "p1", catalog.battle_format)


def both_fainted_one_reserve(request: dict) -> None:
    request["forceSwitch"] = [True, True]
    request["side"]["pokemon"][0]["condition"] = "0 fnt"
    request["side"]["pokemon"][3]["condition"] = "0 fnt"


def turn_state(doubles_view: DoublesView):
    return envelope("moving", "garbled", [doubles_view.legal_action("s", 0)])


# -- the fallback ----------------------------------------------------------------------------------


def test_a_draft_falls_back_to_the_first_card_offered():
    actions = [
        legal_action({"type": "draft_pick", "card_id": card_id}, f"draft_pick:{card_id}") for card_id in ("b", "a")
    ]

    assert fallback(envelope("draft", None, actions)) == {"type": "draft_pick", "card_id": "b"}


def test_a_draft_pick_naming_no_card_falls_back_to_the_first_available_card_id():
    actions = [legal_action({"type": "draft_pick"}, "draft_pick")]

    assert fallback(envelope("draft", {"available_card_ids": [3, "vgc-x"]}, actions)) == {
        "type": "draft_pick",
        "card_id": "vgc-x",
    }


def test_a_draft_with_nothing_to_name_plays_the_first_legal_action():
    state = envelope("draft", {}, [legal_action({"type": "draft_pick"}, "draft_pick")])

    assert fallback(state) is state.legal_actions[0]


def test_team_preview_falls_back_to_the_roster_s_first_four_leading_with_its_first_two():
    request = SHOWDOWN["team_preview_request"]
    state = envelope("team_preview", "garbled", [lineup_legal_action(request, "s", 0)])

    action = fallback(state)

    assert action == {
        "type": "select_lineup",
        "bring": ["incineroar", "urshifurapidstrike", "amoonguss", "rillaboom"],
        "leads": ["incineroar", "urshifurapidstrike"],
    }
    assert lineup_choice(action, request) == "team 1234"


def test_a_turn_falls_back_to_each_slot_s_first_move_at_its_first_opponent_target():
    turn = view("move_request", "move_log")

    action = fallback(turn_state(turn))

    assert action["slot_0"] == {"type": "move", "move_id": "surgingstrikes", "target": 1}
    assert turn.choice(action) == "move 1 1, move 1 1"


def test_a_forced_switch_falls_back_to_a_switch_and_a_pass_where_pass_is_all_there_is():
    forced = view("force_switch_request", "force_switch_log")

    action = fallback(turn_state(forced))

    assert sorted(choice["type"] for choice in (action["slot_0"], action["slot_1"])) == ["pass", "switch"]
    assert forced.choice(action)


def test_with_one_reserve_left_the_second_slot_does_not_switch_into_it_too():
    last = view("force_switch_request", "force_switch_log", both_fainted_one_reserve)

    action = fallback(turn_state(last))

    assert action["slot_0"]["type"] == "switch" and action["slot_1"] == PASS
    assert last.choice(action)


def test_a_slot_the_template_does_not_list_passes():
    fixture = json.loads((FIXTURES / "doubles_observation.json").read_text(encoding="utf-8"))
    state = envelope("moving", fixture["observation"], [fixture["legal_action"]])

    assert fallback(state) == {
        "type": "doubles_turn",
        "slot_0": {"type": "move", "move_id": "surgingstrikes", "target": 1},
        "slot_1": PASS,
    }


def test_another_template_plays_the_first_legal_action_and_no_legal_action_waits():
    teambuild = envelope("teambuild", {}, [legal_action({"type": "submit_team", "team": []}, "submit_team")])

    assert fallback(teambuild) is teambuild.legal_actions[0]
    assert fallback(envelope("moving", {}, [])) is WAIT
    assert template_of(LegalAction("x", "x", {"action": "not a template"})) is None


# -- legality --------------------------------------------------------------------------------------

LINEUP = {"type": "select_lineup", "roster": ["Incineroar", "rillaboom", "amoonguss", "urshifu-rapid-strike", "x", "y"]}


@pytest.mark.parametrize(
    "bring, leads, legal",
    [
        (["incineroar", "Rillaboom", "amoonguss", "urshifurapidstrike"], ["incineroar", "amoonguss"], True),
        (["incineroar", "rillaboom", "amoonguss"], ["incineroar", "rillaboom"], False),
        (["incineroar", "rillaboom", "amoonguss", "incineroar"], ["incineroar", "rillaboom"], False),
        (["incineroar", "rillaboom", "amoonguss", "kyogre"], ["incineroar", "rillaboom"], False),
        (["incineroar", "rillaboom", "amoonguss", "x"], ["incineroar", "y"], False),
        (["incineroar", "rillaboom", "amoonguss", "x"], "incineroar", False),
    ],
    ids=["legal", "three", "repeated", "not on the roster", "lead not brought", "leads not a list"],
)
def test_a_lineup_is_legal_only_with_four_roster_species_and_two_of_them_leading(bring, leads, legal):
    assert is_legal(LINEUP, {"type": "select_lineup", "bring": bring, "leads": leads}) is legal


def _turn_template() -> dict:
    _observation, template = doubles(
        [pokemon("Incineroar"), pokemon("Rillaboom")],
        [pokemon("Miraidon"), pokemon("Chien-Pao")],
        [[move("Flare Blitz", [-2, 1, 2]), switch("Amoonguss")], [move("Fake Out", [1, 2]), switch("Amoonguss")]],
        reserves=[pokemon("Amoonguss", active=False)],
    )
    return template


@pytest.mark.parametrize(
    "slot_0, slot_1, legal",
    [
        ({"type": "move", "move_id": "flareblitz", "target": 2}, {"type": "switch", "species": "amoonguss"}, True),
        (
            {"type": "move", "move_id": "flareblitz", "target": 0},
            {"type": "move", "move_id": "fakeout", "target": 1},
            False,
        ),
        (
            {"type": "move", "move_id": "knockoff", "target": 1},
            {"type": "move", "move_id": "fakeout", "target": 1},
            False,
        ),
        ({"type": "switch", "species": "amoonguss"}, {"type": "switch", "species": "amoonguss"}, False),
        (PASS, {"type": "move", "move_id": "fakeout", "target": 2}, False),
        ({"type": "terastallize"}, {"type": "move", "move_id": "fakeout", "target": 2}, False),
        (None, {"type": "move", "move_id": "fakeout", "target": 2}, False),
    ],
    ids=[
        "legal",
        "target not listed",
        "move not offered",
        "same switch",
        "pass not offered",
        "unknown kind",
        "no slot",
    ],
)
def test_a_turn_is_legal_only_with_offered_options_and_targets_and_no_clash(slot_0, slot_1, legal):
    assert is_legal(_turn_template(), {"type": "doubles_turn", "slot_0": slot_0, "slot_1": slot_1}) is legal


def test_both_slots_passing_is_never_legal_and_another_type_is_never_checked():
    template = {"type": "doubles_turn", "slots": [{"slot": 0, "options": [PASS]}, {"slot": 1, "options": [PASS]}]}

    assert not is_legal(template, {"type": "doubles_turn", "slot_0": PASS, "slot_1": PASS})
    assert not is_legal({"type": "draft_pick", "card_id": "x"}, {"type": "draft_pick", "card_id": "x"})
    assert not is_legal(LINEUP, "select_lineup")
