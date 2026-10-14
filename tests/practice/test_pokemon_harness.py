"""The Pokémon practice harness without Node: observation conversion, the match, the seat session and whole matches.

The numbered sections are acceptance items of the local practice harness
this file covers: (3) the team preview and doubles observations match the
guide-derived fixtures, built from requests real Showdown sent
(`tests/fixtures/pokemon_practice/showdown_p1_view.json`); (4) with the fake
battle helper, a placeholder agent and the `first` opponent complete 20
matches through `run_game`.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from altruagent import DecisionContext, GameState
from altruagent.mcp_transport import MCPToolError
from altruagent.runner import run_game
from ucla_agent.practice.pokemon.battle_log import BattleLog
from ucla_agent.practice.pokemon.bridge import BattleHelper, HelperUpdate, SideUpdate
from ucla_agent.practice.pokemon.cards import CardCatalog, load_catalog
from ucla_agent.practice.pokemon.doubles import DoublesView, move_targets
from ucla_agent.practice.pokemon.match import PokemonPracticeMatch, battle_seed
from ucla_agent.practice.pokemon.preview import (
    lineup_choice,
    lineup_legal_action,
    preview_observation,
    roster_ids,
    species_of,
    level_of,
)
from ucla_agent.core import metrics, paths, records
from ucla_agent.practice.pokemon import play_practice_match
from ucla_agent.practice.pokemon.opponents import FIRST, PolicyAgent
from ucla_agent.practice.pokemon.rules import (
    GAME_ALREADY_COMPLETE,
    INVALID_ACTION,
    NOT_YOUR_TURN,
    STALE_STATE,
    RuleError,
)
from ucla_agent.practice.pokemon.seat import OpponentError, PokemonSeatSession, PracticeStalled

SESSION_ID = "practice-pokemon"
REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_HELPER = [sys.executable, str(REPO_ROOT / "tests" / "stubs" / "fake_pokemon_sim.py")]
MAX_DECISIONS = 2000


@pytest.fixture(scope="module")
def catalog() -> CardCatalog:
    return load_catalog()


@pytest.fixture(scope="module")
def showdown(pokemon_fixture: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    """What real Showdown sent p1 in one battle: requests and log lines (see the fixture's `_source`)."""
    return pokemon_fixture("showdown_p1_view.json")


# -- (3) team preview ------------------------------------------------------------------------------


def test_the_team_preview_observation_from_a_real_request_matches_the_guide_fixture(
    catalog, showdown, pokemon_fixture, json_shape
):
    fixture = pokemon_fixture("team_preview_observation.json")
    opponent_cards = [catalog.card(card_id) for card_id in showdown["p2_cards"]]

    observation = preview_observation(showdown["team_preview_request"], opponent_cards, catalog)

    assert "guide-derived, unverified on the platform" in fixture["_source"]
    assert json_shape(observation) == json_shape(fixture["observation"])
    assert observation["your_roster"][0] == fixture["observation"]["your_roster"][0]
    assert [entry["species"] for entry in observation["opponent_roster"]][:2] == ["miraidon", "fluttermane"]
    assert observation["your_roster"][1]["species"] == "urshifurapidstrike"


def test_the_select_lineup_action_lists_the_roster_species_ids_in_request_order(showdown, pokemon_fixture, json_shape):
    fixture = pokemon_fixture("team_preview_observation.json")

    action = lineup_legal_action(showdown["team_preview_request"], SESSION_ID, 0)

    assert json_shape(action) == json_shape(fixture["legal_action"])
    assert action["input"]["action"]["roster"] == fixture["legal_action"]["input"]["action"]["roster"]


def test_a_lineup_becomes_a_team_choice_with_the_leads_first(showdown, pokemon_fixture):
    answer = pokemon_fixture("team_preview_observation.json")["answer"]

    assert lineup_choice(answer, showdown["team_preview_request"]) == "team 1432"


def test_a_lineup_ignores_case_and_punctuation(showdown):
    action = {
        "type": "select_lineup",
        "bring": ["Urshifu-Rapid-Strike", "WHIMSICOTT", "grimmsnarl", "Amoonguss"],
        "leads": ["Whimsicott", "Urshifu-Rapid-Strike"],
    }

    assert lineup_choice(action, showdown["team_preview_request"]) == "team 5263"


@pytest.mark.parametrize(
    "action",
    [
        None,
        {"type": "doubles_turn"},
        {"type": "select_lineup", "bring": ["incineroar", "amoonguss", "rillaboom"], "leads": ["incineroar"]},
        {"type": "select_lineup", "bring": ["incineroar"] * 4, "leads": ["incineroar", "incineroar"]},
        {"type": "select_lineup", "bring": ["incineroar", "amoonguss", "rillaboom", "gyarados"], "leads": []},
        {
            "type": "select_lineup",
            "bring": ["incineroar", "amoonguss", "rillaboom", "whimsicott"],
            "leads": ["incineroar", "grimmsnarl"],
        },
        {"type": "select_lineup", "bring": "incineroar", "leads": ["incineroar", "amoonguss"]},
    ],
    ids=["no action", "another type", "three brought", "repeated", "not on the roster", "lead not brought", "text"],
)
def test_a_lineup_that_breaks_the_rules_is_invalid(showdown, action):
    with pytest.raises(RuleError) as refused:
        lineup_choice(action, showdown["team_preview_request"])
    assert refused.value.code == INVALID_ACTION


def test_species_and_level_are_read_from_showdown_details(showdown):
    assert species_of("Urshifu-Rapid-Strike, L50, F") == "Urshifu-Rapid-Strike"
    assert level_of("Urshifu-Rapid-Strike, L50, F") == 50 and level_of("Mew") == 100
    assert roster_ids(showdown["team_preview_request"])[1] == "urshifurapidstrike"


# -- (3) doubles turns -----------------------------------------------------------------------------


def doubles_view(
    showdown: dict[str, Any], catalog: CardCatalog, request_key: str, log_key: str, edit: Callable | None = None
) -> DoublesView:
    """A view of one of p1's real requests, after the log lines p1 had then; `edit` changes a copy of the request."""
    request = copy.deepcopy(showdown[request_key])
    if edit is not None:
        edit(request)
    log = BattleLog("p1")
    log.read(showdown[log_key])
    return DoublesView(request, log, catalog, "p1", "gen9vgc2025regi")


@pytest.fixture
def turn_one(showdown, catalog) -> DoublesView:
    return doubles_view(showdown, catalog, "move_request", "move_log")


@pytest.fixture
def forced(showdown, catalog) -> DoublesView:
    return doubles_view(showdown, catalog, "force_switch_request", "force_switch_log")


def answer(slot_0: dict[str, Any], slot_1: dict[str, Any]) -> dict[str, Any]:
    return {"type": "doubles_turn", "slot_0": slot_0, "slot_1": slot_1}


def move(move_id: str, target: Any = None) -> dict[str, Any]:
    chosen = {"type": "move", "move_id": move_id}
    return chosen if target is None else {**chosen, "target": target}


def switch(species: str) -> dict[str, Any]:
    return {"type": "switch", "species": species}


PASS = {"type": "pass"}


def slot_options(view: DoublesView, slot: int) -> list[dict[str, Any]]:
    return view.legal_action(SESSION_ID, 0)["input"]["action"]["slots"][slot]["options"]


def test_the_doubles_observation_from_a_real_request_matches_the_guide_fixture(turn_one, pokemon_fixture, json_shape):
    fixture = pokemon_fixture("doubles_observation.json")
    expected = fixture["observation"]

    observation = turn_one.observation()

    assert "guide-derived, unverified on the platform" in fixture["_source"]
    assert json_shape(observation) == json_shape(expected)
    assert observation["active_pokemon"][0] == expected["active_pokemon"][0]
    assert observation["opponent_active_pokemon"][0] == expected["opponent_active_pokemon"][0]
    assert observation["available_moves"][0][0] == expected["available_moves"][0][0]
    assert observation["available_switches"][0][0] == expected["available_switches"][0][0]
    assert (observation["turn"], observation["fields"], observation["can_tera"]) == (
        1,
        {"ELECTRIC_TERRAIN": 0},
        [True, True],
    )


def test_the_opponent_s_team_holds_only_the_pokemon_it_has_shown_on_the_board(turn_one):
    observation = turn_one.observation()

    assert list(observation["opponent_team"]) == ["p2: Miraidon", "p2: Flutter Mane"]
    assert list(observation["team"]) == ["p1: Urshifu", "p1: Incineroar", "p1: Amoonguss", "p1: Rillaboom"]


def test_the_doubles_turn_template_lists_each_slot_s_options_and_the_target_legend(
    turn_one, pokemon_fixture, json_shape
):
    fixture = pokemon_fixture("doubles_observation.json")

    action = turn_one.legal_action(SESSION_ID, 0)

    assert json_shape(action) == json_shape(fixture["legal_action"])
    slot_0, slot_1 = action["input"]["action"]["slots"]
    assert (slot_0["board_position"], slot_1["board_position"]) == (-1, -2)
    named = [option.get("move_id", option.get("species")) for option in slot_0["options"]]
    assert named == ["surgingstrikes", "closecombat", "aquajet", "uturn", "amoonguss", "rillaboom"]
    assert (slot_0["options"][0]["targets"], slot_1["options"][0]["targets"]) == ([-2, 1, 2], [-1, 1, 2])
    assert "notice" not in action["input"]["action"]


def test_a_move_showdown_marks_disabled_is_not_offered(turn_one):
    # Incineroar holds an Assault Vest: Showdown's request marks Parting Shot, a status move, disabled.
    offered = [option["move_id"] for option in slot_options(turn_one, 1) if option["type"] == "move"]

    assert offered == ["fakeout", "knockoff", "flareblitz"]
    with pytest.raises(RuleError) as refused:
        turn_one.choice(answer(move("surgingstrikes", 1), move("partingshot", 1)))
    assert refused.value.code == INVALID_ACTION


def test_a_filled_in_turn_becomes_showdown_s_choice(turn_one, pokemon_fixture):
    fixture_answer = pokemon_fixture("doubles_observation.json")["answer"]

    assert turn_one.choice(fixture_answer) == "move 1 2, switch 3"
    assert turn_one.choice(answer(move("U-turn", -2), move("fakeout", 1))) == "move 4 -2, move 1 1"


def test_a_forced_switch_offers_pass_to_the_standing_slot_and_the_reserves_to_the_fainted_one(forced):
    observation = forced.observation()

    assert observation["force_switch"] == [False, True]
    assert observation["available_moves"] == [[], []]
    assert [entry["species"] for entry in observation["available_switches"][1]] == ["amoonguss", "rillaboom"]
    assert slot_options(forced, 0) == [PASS]
    assert [option["species"] for option in slot_options(forced, 1)] == ["amoonguss", "rillaboom"]
    assert forced.choice(answer(PASS, switch("Rillaboom"))) == "pass, switch 4"


def both_fainted_one_reserve(request: dict[str, Any]) -> None:
    request["forceSwitch"] = [True, True]
    request["side"]["pokemon"][0]["condition"] = "0 fnt"
    request["side"]["pokemon"][3]["condition"] = "0 fnt"


def test_both_fainted_with_one_reserve_offer_it_and_pass_to_both_slots_with_a_notice(showdown, catalog):
    view = doubles_view(showdown, catalog, "force_switch_request", "force_switch_log", both_fainted_one_reserve)
    reserve = {"type": "switch", "species": "amoonguss", "name": "Amoonguss"}

    assert slot_options(view, 0) == [reserve, PASS]
    assert slot_options(view, 1) == [reserve, PASS]
    assert "notice" in view.legal_action(SESSION_ID, 0)["input"]["action"]
    assert view.choice(answer(switch("amoonguss"), PASS)) == "switch 3, pass"
    with pytest.raises(RuleError):
        view.choice(answer(PASS, PASS))
    with pytest.raises(RuleError):
        view.choice(answer(switch("amoonguss"), switch("amoonguss")))


def fainted_partner_and_trapped_lead(request: dict[str, Any]) -> None:
    request["side"]["pokemon"][1]["condition"] = "0 fnt"
    request["active"][0]["trapped"] = True


def test_a_fainted_slot_with_no_reserve_only_passes_and_a_trapped_slot_cannot_switch(showdown, catalog):
    view = doubles_view(showdown, catalog, "move_request", "move_log", fainted_partner_and_trapped_lead)

    assert {option["type"] for option in slot_options(view, 0)} == {"move"}
    assert slot_options(view, 1) == [PASS]
    assert view.choice(answer(move("aquajet", 1), PASS)) == "move 3 1, pass"


def struggle_and_protect(request: dict[str, Any]) -> None:
    request["active"][0]["moves"] = [{"move": "Struggle", "id": "struggle", "target": "randomNormal"}]
    request["active"][1]["moves"][1] = {"move": "Protect", "id": "protect", "pp": 16, "maxpp": 16, "target": "self"}


def test_struggle_and_a_move_with_no_target_take_none(showdown, catalog):
    view = doubles_view(showdown, catalog, "move_request", "move_log", struggle_and_protect)

    assert view.choice(answer(move("struggle"), move("protect", 0))) == "move 1, move 2"
    with pytest.raises(RuleError):
        view.choice(answer(move("struggle", 1), move("protect")))


@pytest.mark.parametrize(
    "turn",
    [
        None,
        {"type": "select_lineup"},
        {"type": "doubles_turn", "slot_0": move("surgingstrikes", 1)},
        answer(move("hydropump", 1), PASS),
        answer(move("surgingstrikes", 3), move("fakeout", 1)),
        answer(move("surgingstrikes"), move("fakeout", 1)),
        answer(move("surgingstrikes", True), move("fakeout", 1)),
        answer(switch("incineroar"), move("fakeout", 1)),
        answer(PASS, move("fakeout", 1)),
        answer(switch("amoonguss"), switch("amoonguss")),
        answer({"type": "tera"}, move("fakeout", 1)),
    ],
    ids=[
        "no action",
        "another type",
        "a slot missing",
        "unknown move",
        "target off the list",
        "target left out",
        "target a bool",
        "switch to an active",
        "pass not offered",
        "same switch twice",
        "tera",
    ],
)
def test_a_turn_the_rules_refuse_is_invalid(turn_one, turn):
    with pytest.raises(RuleError) as refused:
        turn_one.choice(turn)
    assert refused.value.code == INVALID_ACTION


@pytest.mark.parametrize(
    ("slot", "target_type", "occupied", "targets"),
    [
        (0, "normal", {-1, -2, 1, 2}, [-2, 1, 2]),
        (1, "any", {-1, -2, 1, 2}, [-1, 1, 2]),
        (0, "normal", {-1, -2, 1}, [-2, 1]),
        (0, "adjacentFoe", {-1, -2, 1, 2}, [1, 2]),
        (1, "adjacentAlly", {-1, -2, 1, 2}, [-1]),
        (0, "adjacentAllyOrSelf", {-1, -2, 1, 2}, [-2, -1]),
        (0, "allAdjacentFoes", {-1, -2, 1, 2}, [0]),
        (0, None, {-1, -2, 1, 2}, [0]),
        (1, "adjacentAlly", {-2, 1, 2}, [-1]),
    ],
    ids=["normal", "any", "empty foe left out", "foes only", "ally", "ally or self", "spread", "locked", "no ally"],
)
def test_move_targets_follow_showdown_s_rules_in_poke_env_s_order(slot, target_type, occupied, targets):
    assert move_targets(slot, target_type, occupied) == targets


# -- the match on the fake battle helper -----------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_791_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def fake_helper() -> Iterator[BattleHelper]:
    with BattleHelper(FAKE_HELPER) as helper:
        yield helper


@pytest.fixture
def match(catalog, fake_helper) -> PokemonPracticeMatch:
    return PokemonPracticeMatch(5, catalog, fake_helper, clock=FakeClock())


def first_answer(state: dict[str, Any]) -> dict[str, Any]:
    """The simplest legal answer to a state with a decision: the first card, the first four, the first options."""
    template = state["legal_actions"]["actions"][0]["input"]["action"]
    if template["type"] == "draft_pick":
        return template
    if template["type"] == "select_lineup":
        return {"type": "select_lineup", "bring": template["roster"][:4], "leads": template["roster"][:2]}
    slot_0 = _first_option(template["slots"][0]["options"], taken=None)
    slot_1 = _first_option(template["slots"][1]["options"], taken=slot_0.get("species"))
    return {"type": "doubles_turn", "slot_0": slot_0, "slot_1": slot_1}


def _first_option(options: list[dict[str, Any]], taken: str | None) -> dict[str, Any]:
    """The first option, passing over a switch into the Pokémon the other slot already switches to."""
    option = next(option for option in options if taken is None or option.get("species") != taken)
    if option["type"] == "move":
        return {"type": "move", "move_id": option["move_id"], "target": option["targets"][-1]}
    return {key: option[key] for key in ("type", "species") if key in option}


def play_first(match: PokemonPracticeMatch, seat: int) -> None:
    state = match.state(seat, SESSION_ID)
    match.play(seat, state["state_version"], action=first_answer(state))


def finish_draft(match: PokemonPracticeMatch) -> None:
    while match.battle is None:
        play_first(match, match.seat_ids.index(match.draft.current_seat))


def test_a_draft_state_is_the_guide_s_envelope_with_one_shared_counter(match, pokemon_fixture, json_shape):
    fixture = pokemon_fixture("state_envelope.json")
    drafter = match.seat_ids.index(match.draft.current_seat)
    waiting = 1 - drafter

    state = match.state(drafter, SESSION_ID)
    other = match.state(waiting, SESSION_ID)

    assert "guide-derived, unverified on the platform" in fixture["_source"]
    shown = {key: value for key, value in state.items() if key != "observation"}
    assert set(shown) == set(fixture["state"])
    for key, value in fixture["state"].items():
        expected_shape = json_shape(value) if key != "legal_actions" else json_shape(shown[key])
        assert json_shape(shown[key]) == expected_shape, key
    assert set(shown["legal_actions"]) == set(fixture["state"]["legal_actions"])
    assert (state["phase"], state["is_current_actor"], other["is_current_actor"]) == ("draft", True, False)
    assert "legal_actions" not in other and other["observation"] == state["observation"]
    assert other["current_actor"] == {"agent_id": match.draft.current_seat, "position": drafter}
    play_first(match, drafter)
    assert match.state_version(0) == match.state_version(1) == 1


def test_a_pick_out_of_turn_or_on_an_old_version_is_refused(match):
    drafter = match.seat_ids.index(match.draft.current_seat)
    card = match.draft.offered(match.draft.current_seat)[0].card_id

    with pytest.raises(RuleError) as out_of_turn:
        match.play(1 - drafter, 0, action_id=f"draft_pick:{card}")
    with pytest.raises(RuleError) as stale:
        match.play(drafter, 3, action_id=f"draft_pick:{card}")

    assert (out_of_turn.value.code, stale.value.code) == (NOT_YOUR_TURN, STALE_STATE)
    match.play(drafter, 0, action_id=f"draft_pick:{card}", reasoning_summary="A fine card.")
    assert match.draft.picks[0].public_reason == "A fine card."


def test_the_draft_clock_picks_for_a_seat_that_waits_15_seconds(match):
    match.draft._clock.now += 15

    assert match.apply_timeouts() is True
    assert match.draft.picks[0].auto is True and match.draft_version == 1
    assert match.apply_timeouts() is False


def test_the_twelfth_pick_starts_the_battle_at_team_preview_with_each_seat_s_counter_at_0(match):
    finish_draft(match)

    assert match.draft_version == 12
    for seat in (0, 1):
        state = match.state(seat, SESSION_ID)
        assert (state["phase"], state["state_version"], state["is_current_actor"]) == ("team_preview", 0, True)
        assert state["legal_actions"]["actions"][0]["action_id"] == "select_lineup"
        roster = state["observation"]["your_roster"]
        assert [entry["name"] for entry in roster] == [
            card.species for card in match.draft.roster(match.seat_ids[seat])
        ]


def test_the_opponent_s_lineup_choice_stays_private(match):
    finish_draft(match)
    before = match.state(0, SESSION_ID)

    play_first(match, 1)
    after = match.state(0, SESSION_ID)

    assert after == before


def test_a_lineup_needs_its_structured_action_and_then_waits_for_the_other_seat(match):
    finish_draft(match)
    with pytest.raises(RuleError) as bare:
        match.play(0, 0, action_id="select_lineup")
    assert bare.value.code == INVALID_ACTION

    play_first(match, 0)
    waiting = match.state(0, SESSION_ID)
    assert (waiting["state_version"], waiting["is_current_actor"]) == (1, False)
    assert waiting["observation"] == {
        "message": "No pending decision is currently available.",
        "waiting_for_action": False,
        "finished": False,
    }
    with pytest.raises(RuleError) as again:
        match.play(0, 1, action={"type": "select_lineup"})
    assert again.value.code == NOT_YOUR_TURN

    play_first(match, 1)
    moving = match.state(0, SESSION_ID)
    assert (moving["phase"], moving["is_current_actor"], moving["observation"]["turn"]) == ("moving", True, 1)
    assert moving["legal_actions"]["actions"][0]["action_id"] == "doubles_turn"


def test_a_choice_the_simulator_refuses_comes_back_with_its_message_and_leaves_the_decision_open(match, monkeypatch):
    finish_draft(match)
    play_first(match, 0)
    play_first(match, 1)
    helper_choose = match.helper.choose

    def refusing(side: str, choice: str) -> HelperUpdate:
        refused = SideUpdate((), None, "[Invalid choice] Can't move: Urshifu's Surging Strikes is disabled")
        return HelperUpdate({"p1": refused, "p2": SideUpdate((), None, None)}, False, None)

    monkeypatch.setattr(match.helper, "choose", refusing)
    with pytest.raises(RuleError) as refused:
        play_first(match, 0)
    assert refused.value.code == INVALID_ACTION and "is disabled" in refused.value.detail
    assert match.has_decision(0) and match.state_version(0) == 1

    monkeypatch.setattr(match.helper, "choose", helper_choose)
    play_first(match, 0)
    assert match.state_version(0) == 2


def play_to_the_end(match: PokemonPracticeMatch) -> None:
    for _decision in range(MAX_DECISIONS):
        seat = next((seat for seat in (0, 1) if match.has_decision(seat)), None)
        if seat is None:
            return
        play_first(match, seat)


def test_a_whole_match_of_first_answers_ends_with_a_result(match):
    play_to_the_end(match)

    assert (match.finished, match.termination_reason) == (True, "completed")
    assert match.turns > 1
    result = match.result(0, SESSION_ID)
    assert result["winner_agent_id"] in match.seat_ids and sorted(result["returns"].values()) == [0.0, 1.0]
    state = match.state(0, SESSION_ID)
    assert (state["is_terminal"], state["status"], state["observation"]["finished"]) == (True, "completed", True)
    with pytest.raises(RuleError) as over:
        match.play(0, state["state_version"], action={"type": "doubles_turn"})
    assert over.value.code == GAME_ALREADY_COMPLETE


def test_a_resignation_ends_the_match_at_once_with_the_other_seat_winning(match):
    assert match.result(0, SESSION_ID)["returns"] is None

    match.resign(0)

    result = match.result(0, SESSION_ID)
    assert (result["winner_agent_id"], result["your_return"], result["termination_reason"]) == (
        "practice-p2",
        0.0,
        "resignation",
    )
    with pytest.raises(RuleError) as again:
        match.resign(1)
    assert again.value.code == GAME_ALREADY_COMPLETE


class FirstAnswers:
    """A test player of either kind: as an opponent it answers state objects, as an agent `GameState`s."""

    def choose(self, state: dict[str, Any]) -> dict[str, Any]:
        return first_answer(state)

    def choose_action(self, state: GameState, _context: DecisionContext) -> dict[str, Any]:
        return first_answer(state.raw)


class RefusedAnswers:
    def choose(self, _state: dict[str, Any]) -> dict[str, Any]:
        return {"type": "draft_pick", "card_id": "vgc-no-such-card"}


def seat_session(match: PokemonPracticeMatch, opponent: Any = None) -> PokemonSeatSession:
    return PokemonSeatSession(match, 0, opponent or FirstAnswers(), SESSION_ID)


def context() -> DecisionContext:
    return DecisionContext(SESSION_ID, None, "pokemon_vgc_doubles_draft", "practice-p1", seat_position=0)


def test_the_runner_plays_the_driven_seat_through_the_whole_match(match):
    session = seat_session(match)

    final = run_game(session, context(), FirstAnswers())

    assert final.is_terminal and final.returns is not None
    # Six picks, then the lineup and every turn, each of which moved the seat's battle counter by one.
    assert match.finished and session.decisions == 6 + match.battle[0].version


def test_the_opponent_s_decisions_are_taken_before_the_driven_seat_is_answered(match):
    session = seat_session(match)

    state = session.get_state()

    assert state.is_current_actor is True and match.draft.current_seat == "practice-p1"
    assert all(pick.seat_id == "practice-p2" for pick in match.draft.picks)


def test_wait_for_update_answers_at_once_and_says_whether_anything_changed(match):
    session = seat_session(match)
    seen = session.get_state()

    same = session.wait_for_update(since_version=seen.state_version, since_is_current_actor=True, since_phase="draft")
    moved = session.wait_for_update(since_version=seen.state_version - 1)

    assert (same.raw["updated"], moved.raw["updated"]) == (False, True)


def test_a_driven_seat_move_the_rules_refuse_is_the_platform_s_tool_error(match):
    session = seat_session(match)
    state = session.get_state()

    with pytest.raises(MCPToolError) as stale:
        session.play_action(action_id=state.legal_actions[0].action_id, state_version=state.state_version + 1)
    with pytest.raises(MCPToolError) as invalid:
        session.play_action(action_id="draft_pick:vgc-no-such-card", state_version=state.state_version)

    assert (stale.value.error_code, invalid.value.error_code) == ("STALE_STATE", "INVALID_ACTION")


def test_a_pick_s_reply_carries_the_draft_s_progress_and_the_next_state(match):
    session = seat_session(match)
    state = session.get_state()

    reply = session.play_action(action=first_answer(state.raw), state_version=state.state_version)

    assert reply["accepted"] is True and reply["state"]["state_version"] == reply["state_version"]
    assert reply["picks_remaining"] == 12 - len(match.draft.picks)
    assert (reply["draft_complete"], reply["battle_starting"]) == (False, False)


def test_an_opponent_that_breaks_a_rule_raises_opponent_error(catalog, fake_helper):
    seed = next(
        seed for seed in range(20) if PokemonPracticeMatch(seed, catalog, fake_helper).draft.order[0] == "practice-p2"
    )
    session = seat_session(PokemonPracticeMatch(seed, catalog, fake_helper), RefusedAnswers())

    with pytest.raises(OpponentError, match="vgc-no-such-card"):
        session.get_state()


def test_the_draft_clock_picks_for_a_driven_seat_that_waits(match):
    session = seat_session(match)
    session.get_state()
    picks = len(match.draft.picks)

    match.draft._clock.now += 15
    session.get_state()

    assert match.draft.picks[picks].seat_id == "practice-p1" and match.draft.picks[picks].auto is True


def test_waiting_when_no_seat_has_a_decision_is_a_stall_not_an_endless_wait(match, monkeypatch):
    session = seat_session(match)
    monkeypatch.setattr(PokemonPracticeMatch, "has_decision", lambda _match, _seat: False)

    with pytest.raises(PracticeStalled, match="no seat has a decision"):
        session.wait_for_update(since_version=0)


def test_messaging_is_unavailable_and_a_resignation_ends_the_match(match):
    session = seat_session(match)

    for call in (session.send_message, session.get_messages):
        with pytest.raises(MCPToolError) as unavailable:
            call(message_type="chat", content="hi")
        assert unavailable.value.error_code == "RUNTIME_UNAVAILABLE"
    result = session.resign()
    assert (result["your_return"], result["termination_reason"]) == (0.0, "resignation")
    assert session.get_result() == result


def test_the_battle_seed_is_four_16_bit_numbers_drawn_from_the_match_seed():
    parts = [int(part) for part in battle_seed(3).split(",")]

    assert len(parts) == 4 and all(0 <= part < 65536 for part in parts)
    assert battle_seed(3) == battle_seed(3) != battle_seed(4)


# -- (4) whole practice matches through run_game ---------------------------------------------------

PRACTICE_MATCHES = 20


@pytest.fixture
def runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The match records go to a temporary folder."""
    monkeypatch.setenv(paths.RUNS_DIR_ENV, str(tmp_path))
    return tmp_path


def placeholder_agent() -> PolicyAgent:
    """A placeholder decision-maker: the starter smoke agent's first-legal-option rule, valid moves and no strategy."""
    return PolicyAgent(FIRST)


class RaisingAgent:
    def choose_action(self, _state: GameState, _context: DecisionContext) -> dict[str, Any]:
        raise RuntimeError("the decision-maker broke")


class RecordingAgent:
    """Plays the first legal options and logs one fallback and one unhandled error into its match record."""

    def __init__(self) -> None:
        self._policy = PolicyAgent(FIRST)
        self._logged = False

    def choose_action(self, state: GameState, context: DecisionContext) -> dict[str, Any]:
        if not self._logged:
            self._logged = True
            records.log("fallback", {"reason": "timeout"})
            records.log("error", {"where": "test", "type": "unhandled", "exception": "X", "message": "logged"})
        return self._policy.choose_action(state, context)


def test_20_practice_matches_of_a_placeholder_agent_against_first_finish_through_run_game(runs_dir):
    results = [
        play_practice_match(seed, placeholder_agent, FIRST, helper_command=FAKE_HELPER)
        for seed in range(PRACTICE_MATCHES)
    ]

    assert all(result.winner in ("practice-p1", "practice-p2") for result in results)
    assert not any(result.unhandled_error for result in results)
    assert all(result.termination_reason == "completed" and result.turns > 1 for result in results)
    assert all(result.decisions > 6 and result.fallbacks == 0 for result in results)
    assert {result.our_won for result in results} == {True, False}
    assert len({result.session_id for result in results}) == PRACTICE_MATCHES


def test_each_practice_match_writes_a_closed_record_of_its_own_under_the_runs_folder(runs_dir):
    result = play_practice_match(3, placeholder_agent, FIRST, helper_command=FAKE_HELPER)

    [folder] = [match for dated in runs_dir.iterdir() for match in metrics.match_folders(dated)]
    record = metrics.load_match(folder)
    assert folder.name.startswith(result.session_id)
    assert record.data_of("result")[0]["result"]["winner_agent_id"] == result.winner


def test_a_decision_maker_that_raises_is_an_unhandled_error_and_ends_the_match_for_us(runs_dir):
    result = play_practice_match(4, RaisingAgent, FIRST, helper_command=FAKE_HELPER)

    assert (result.unhandled_error, result.winner, result.decisions) == (True, None, 0)
    assert "the decision-maker broke" in result.error


def test_fallbacks_and_unhandled_errors_the_decision_maker_records_are_counted(runs_dir):
    result = play_practice_match(5, RecordingAgent, FIRST, helper_command=FAKE_HELPER)

    assert (result.fallbacks, result.unhandled_error, result.error) == (1, True, None)
    assert result.winner is not None
