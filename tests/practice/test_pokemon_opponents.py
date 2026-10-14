"""The Pokémon practice opponents `first`, `random` and `self`, and the policy agent, on real and whole-match states.

The doubles states are built from the requests real Showdown sent
(`tests/fixtures/pokemon_practice/showdown_p1_view.json`); every answer is
checked with the same `DoublesView.choice` the practice match applies.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
from pathlib import Path
from typing import Any, Callable

import pytest

from altruagent import RESIGN, GameState, LegalAction, WithReasoning
from ucla_agent.practice.pokemon.battle_log import BattleLog
from ucla_agent.practice.pokemon.cards import CardCatalog, load_catalog
from ucla_agent.practice.pokemon.doubles import DoublesView
from ucla_agent.practice.pokemon.draft import PracticeDraft
from ucla_agent.practice.pokemon.opponents import (
    FIRST,
    OPPONENT_FACTORIES,
    RANDOM,
    SELF,
    FirstOpponent,
    PolicyAgent,
    RandomOpponent,
    make_opponent,
    models_off,
    structured_action,
)
from ucla_agent.practice.pokemon.preview import lineup_choice, lineup_legal_action

SESSION_ID = "practice-pokemon"
REPO_ROOT = Path(__file__).resolve().parents[2]
RANDOM_DRAWS = 300
SEATS = ("practice-p1", "practice-p2")


@pytest.fixture(scope="module")
def catalog() -> CardCatalog:
    return load_catalog()


@pytest.fixture(scope="module")
def showdown(pokemon_fixture: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    return pokemon_fixture("showdown_p1_view.json")


def state_with(action: dict[str, Any]) -> dict[str, Any]:
    """A seat's state object reduced to what an opponent reads: its legal actions."""
    return {"session_id": SESSION_ID, "legal_actions": {"session_id": SESSION_ID, "actions": [action]}}


def doubles_view(showdown: dict[str, Any], catalog: CardCatalog, request_key: str, log_key: str, edit=None):
    request = copy.deepcopy(showdown[request_key])
    if edit is not None:
        edit(request)
    log = BattleLog("p1")
    log.read(showdown[log_key])
    return DoublesView(request, log, catalog, "p1", "gen9vgc2025regi")


def both_fainted_one_reserve(request: dict[str, Any]) -> None:
    request["forceSwitch"] = [True, True]
    request["side"]["pokemon"][0]["condition"] = "0 fnt"
    request["side"]["pokemon"][3]["condition"] = "0 fnt"


@pytest.fixture
def views(showdown, catalog) -> dict[str, DoublesView]:
    return {
        "turn": doubles_view(showdown, catalog, "move_request", "move_log"),
        "forced": doubles_view(showdown, catalog, "force_switch_request", "force_switch_log"),
        "one reserve": doubles_view(
            showdown, catalog, "force_switch_request", "force_switch_log", both_fainted_one_reserve
        ),
    }


def draft_state(draft: PracticeDraft) -> dict[str, Any]:
    return {
        "session_id": SESSION_ID,
        "legal_actions": {"actions": draft.legal_actions(draft.current_seat, SESSION_ID, 0)},
    }


# -- first ------------------------------------------------------------------------------------------


def test_first_picks_the_first_card_offered(catalog):
    draft = PracticeDraft(1, catalog, SEATS)

    pick = FirstOpponent().choose(draft_state(draft))

    assert pick == {"type": "draft_pick", "card_id": draft.offered(draft.current_seat)[0].card_id}


def test_first_brings_the_first_four_and_leads_with_the_first_two(showdown):
    lineup = FirstOpponent().choose(state_with(lineup_legal_action(showdown["team_preview_request"], SESSION_ID, 0)))

    assert lineup_choice(lineup, showdown["team_preview_request"]) == "team 1234"


def test_first_moves_with_each_slot_s_first_move_at_its_first_opponent_target(views):
    view = views["turn"]

    turn = FirstOpponent().choose(state_with(view.legal_action(SESSION_ID, 0)))

    assert turn["slot_0"] == {"type": "move", "move_id": "surgingstrikes", "target": 1}
    assert view.choice(turn) == "move 1 1, move 1 1"


def test_first_switches_where_it_must_and_passes_where_it_may(views):
    forced = FirstOpponent().choose(state_with(views["forced"].legal_action(SESSION_ID, 0)))
    one_reserve = FirstOpponent().choose(state_with(views["one reserve"].legal_action(SESSION_ID, 0)))

    assert views["forced"].choice(forced) == "pass, switch 3"
    assert views["one reserve"].choice(one_reserve) == "switch 3, pass"


# -- random -----------------------------------------------------------------------------------------


def test_random_always_answers_a_legal_turn_and_covers_the_options(views):
    opponent = RandomOpponent(seed=1)
    for name, view in views.items():
        state = state_with(view.legal_action(SESSION_ID, 0))
        choices = {view.choice(opponent.choose(state)) for _draw in range(RANDOM_DRAWS)}
        assert len(choices) > 1, name


def test_random_lineups_and_picks_are_legal_and_vary(showdown, catalog):
    opponent = RandomOpponent(seed=2)
    preview = state_with(lineup_legal_action(showdown["team_preview_request"], SESSION_ID, 0))
    lineups = {lineup_choice(opponent.choose(preview), showdown["team_preview_request"]) for _draw in range(50)}
    draft = PracticeDraft(4, catalog, SEATS)
    offered = {card.card_id for card in draft.offered(draft.current_seat)}
    picks = {opponent.choose(draft_state(draft))["card_id"] for _draw in range(50)}

    assert len(lineups) > 10
    assert picks <= offered and len(picks) > 1


def test_random_is_repeatable_for_a_seed(views):
    state = state_with(views["turn"].legal_action(SESSION_ID, 0))

    def answers(seed: int) -> list[dict[str, Any]]:
        opponent = RandomOpponent(seed)
        return [opponent.choose(state) for _draw in range(20)]

    assert answers(5) == answers(5) != answers(6)


# -- self, the factories and the policy agent -------------------------------------------------------


def test_the_opponent_kinds_are_random_first_and_self():
    assert sorted(OPPONENT_FACTORIES) == sorted([RANDOM, FIRST, SELF])
    assert isinstance(make_opponent(FIRST), FirstOpponent) and isinstance(make_opponent(RANDOM, 3), RandomOpponent)
    with pytest.raises(ValueError, match="unknown opponent kind"):
        make_opponent("smart")


def test_the_opponents_module_imports_the_pokemon_agent_only_when_a_self_opponent_is_made():
    tree = ast.parse((REPO_ROOT / "src" / "ucla_agent" / "practice" / "pokemon" / "opponents.py").read_text("utf-8"))
    top_level = [node.module for node in tree.body if isinstance(node, ast.ImportFrom)]
    inside_self = [
        node.module
        for node in ast.walk(next(node for node in tree.body if getattr(node, "name", "") == "SelfOpponent"))
        if isinstance(node, ast.ImportFrom)
    ]

    assert not any(module.startswith("ucla_agent.games.pokemon") for module in top_level)
    assert "ucla_agent.games.pokemon.agent" in inside_self


def test_the_self_opponent_is_the_pokemon_agent_with_its_models_off(showdown):
    opponent = make_opponent(SELF, 1)
    preview = state_with(lineup_legal_action(showdown["team_preview_request"], SESSION_ID, 0))

    assert lineup_choice(opponent.choose(preview), showdown["team_preview_request"]).startswith("team ")


def test_models_off_turns_off_every_model_switch_and_nothing_else():
    @dataclasses.dataclass(frozen=True)
    class Settings:
        model_choices: bool = True
        model_messages: bool = True
        model_timeout_ms: int = 900
        margin: float = 0.5

    assert models_off(Settings()) == Settings(model_choices=False, model_messages=False)


def test_an_agent_s_decision_becomes_the_structured_action_it_stands_for():
    pick = {"type": "draft_pick", "card_id": "vgc-gyarados"}
    legal = LegalAction("draft_pick:vgc-gyarados", "Draft", {"action": pick})
    state = GameState.from_mcp_state(
        {"legal_actions": {"actions": [{"action_id": legal.action_id, "input": legal.input}]}}
    )

    assert structured_action(pick, state) == pick
    assert structured_action(WithReasoning(pick, "Intimidate."), state) == pick
    assert structured_action(legal, state) == pick
    assert structured_action("draft_pick:vgc-gyarados", state) == pick
    with pytest.raises(ValueError):
        structured_action(RESIGN, state)


def test_the_policy_agent_plays_an_opponent_kind_over_the_runner_s_game_state(views):
    raw = state_with(views["turn"].legal_action(SESSION_ID, 0))

    decision = PolicyAgent(FIRST).choose_action(GameState.from_mcp_state(raw), None)

    assert views["turn"].choice(decision) == "move 1 1, move 1 1"
