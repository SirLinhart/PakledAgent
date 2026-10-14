"""PokemonAgent: registered for `pokemon_vgc_doubles_draft`, every phase played with a legal structured action.

The numbered tests are the acceptance items of the agent's registration and
its fallbacks. The team preview and doubles states are the practice
harness's, built from the requests real Showdown sent
(`tests/fixtures/pokemon_practice/showdown_p1_view.json`), and each answer is
checked by the same rules the practice match applies; whole matches run on
the practice harness with its fake battle helper.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

import pytest
from pokemon_states import US, card, doubles, envelope, legal_action, move, pokemon
from pokemon_stubs import Answering

from altruagent import DecisionContext, GameState, WithReasoning
from ucla_agent.core import paths, records, registry
from ucla_agent.core.dispatch import Dispatcher
from ucla_agent.games.pokemon import agent as agent_module
from ucla_agent.games.pokemon.agent import PokemonAgent
from ucla_agent.games.pokemon.briefing import set_line
from ucla_agent.games.pokemon.draft import DraftMemory, offered_card_ids, score_cards
from ucla_agent.games.pokemon.observe import Reader
from ucla_agent.games.pokemon.settings import PokemonSettings
from ucla_agent.practice.pokemon import play_practice_match
from ucla_agent.practice.pokemon.battle_log import BattleLog
from ucla_agent.practice.pokemon.cards import CATALOG_PATH, load_catalog
from ucla_agent.practice.pokemon.doubles import DoublesView
from ucla_agent.practice.pokemon.draft import PracticeDraft
from ucla_agent.practice.pokemon.preview import lineup_choice, lineup_legal_action, preview_observation

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "pokemon_practice"
SHOWDOWN = json.loads((FIXTURES / "showdown_p1_view.json").read_text(encoding="utf-8"))
GAME_TYPE = "pokemon_vgc_doubles_draft"
SESSION_ID = "pokemon-agent-test"
TRACKED = PokemonSettings.from_files(REPO_ROOT)
# The script's own ranking, whatever `[maximin] enabled` ships: the tests of today's play assert on it, and the
# maximin tests switch it on themselves.
SCRIPT_RANKING = dataclasses.replace(TRACKED.tuning, maximin=dataclasses.replace(TRACKED.tuning.maximin, enabled=False))
SETTINGS = dataclasses.replace(TRACKED, model_choices=False, tuning=SCRIPT_RANKING)
FAKE_HELPER = [sys.executable, str(REPO_ROOT / "tests" / "stubs" / "fake_pokemon_sim.py")]
PRACTICE_MATCHES = 4
CONTEXT = DecisionContext(SESSION_ID, None, GAME_TYPE, US, seat_position=0)
# Read before any test points the project root at a folder of its own.
CATALOG = load_catalog(REPO_ROOT / CATALOG_PATH)


def models_off() -> PokemonAgent:
    return PokemonAgent(settings=SETTINGS)


@pytest.fixture
def match_events(run_folder, read_jsonl) -> Callable[[], list[dict]]:
    """Opens a match record; the returned function closes it and gives its events."""
    records.start_match(CONTEXT, None)

    def close_and_read() -> list[dict]:
        records.finish_match({"is_terminal": True})
        (events_path,) = run_folder.glob(f"runs/*/{SESSION_ID}__*/events.jsonl")
        return read_jsonl(events_path)

    return close_and_read


def kinds(events: list[dict], kind: str) -> list[dict]:
    return [event["data"] for event in events if event["kind"] == kind]


def draft_state() -> GameState:
    """Our first pick of a practice draft of the tracked catalog, in the guide's envelope."""
    draft = PracticeDraft(1, CATALOG, (US, "agent_them"))
    if draft.current_seat != US:
        draft.pick(draft.current_seat, draft.offered(draft.current_seat)[0].card_id)
    return envelope("draft", draft.observation(), draft.legal_actions(US, SESSION_ID, 0))


def preview_state() -> GameState:
    """The team preview the practice harness builds from the real Showdown request."""
    request = SHOWDOWN["team_preview_request"]
    observation = preview_observation(request, [CATALOG.card(card_id) for card_id in SHOWDOWN["p2_cards"]], CATALOG)
    return envelope("team_preview", observation, [lineup_legal_action(request, SESSION_ID, 0)])


def turn_view() -> DoublesView:
    log = BattleLog("p1")
    log.read(SHOWDOWN["move_log"])
    return DoublesView(SHOWDOWN["move_request"], log, CATALOG, "p1", CATALOG.battle_format)


def turn_state(observation: Any = None) -> GameState:
    """The doubles turn the practice harness builds from the real Showdown move request."""
    view = turn_view()
    return envelope(
        "moving", view.observation() if observation is None else observation, [view.legal_action(SESSION_ID, 0)]
    )


# -- (1) registration ------------------------------------------------------------------------------


def test_1_the_registry_maps_the_game_to_the_pokemon_agent_and_the_dispatcher_hands_it_the_match(
    run_folder, match_events, stub_models_config
):
    shutil.copytree(REPO_ROOT / "config", run_folder / "config")
    # The tracked chain asks local:strong; its endpoint is a free test port where nothing listens, never the live
    # agent's model server, so the choice falls back to the script.
    stub_models_config(run_folder)
    dispatcher = Dispatcher()

    dispatcher.choose_action(preview_state(), CONTEXT)

    assert registry.GAME_FACTORIES[GAME_TYPE] is PokemonAgent
    assert isinstance(dispatcher.decision_maker, PokemonAgent)
    match_events()


# -- (2) and (3) every phase -----------------------------------------------------------------------


def test_2_a_draft_state_gives_a_with_reasoning_draft_pick_of_an_offered_card(match_events):
    state = draft_state()

    decision = models_off().choose_action(state, CONTEXT)

    assert isinstance(decision, WithReasoning)
    assert decision.action["type"] == "draft_pick"
    assert f"draft_pick:{decision.action['card_id']}" in [action.action_id for action in state.legal_actions]
    assert 0 < len(decision.reasoning_summary) <= 120
    (event,) = kinds(match_events(), "pokemon_decision")
    assert (event["phase"], event["parse_degradations"]) == ("draft", 0)


def test_3_team_preview_and_doubles_states_give_dicts_the_guide_s_template_rules_accept(match_events):
    agent = models_off()

    lineup = agent.choose_action(preview_state(), CONTEXT)
    turn = agent.choose_action(turn_state(), CONTEXT)

    assert lineup_choice(lineup, SHOWDOWN["team_preview_request"]).startswith("team ")
    assert turn["type"] == "doubles_turn" and turn_view().choice(turn)
    events = match_events()
    assert [event["phase"] for event in kinds(events, "pokemon_decision")] == ["team_preview", "moving"]
    assert kinds(events, "error") == []


def test_the_draft_memory_carries_from_the_draft_to_the_battle():
    ours = [card("Incineroar", ["Fake Out", "Knock Off"], "Assault Vest", "Intimidate"), card("Amoonguss", ["Spore"])]
    theirs = [card("Milotic", ["Scald"]), card("Vaporeon", ["Scald"])]
    agent = models_off()
    agent.choose_action(envelope("draft", {"available_cards": [*ours, *theirs], "rosters": {}}, []), CONTEXT)
    rosters = {US: ours, "agent_them": theirs}
    drafted = {
        "available_cards": [],
        "available_card_ids": [],
        "rosters": {
            s: [{"card_id": c["card_id"], "species": c["species"]} for c in held] for s, held in rosters.items()
        },
    }
    agent.choose_action(
        envelope("draft", drafted, [legal_action({"type": "draft_pick", "card_id": "x"}, "draft_pick:x")]), CONTEXT
    )
    observation, template = doubles(
        [pokemon("Incineroar"), pokemon("Amoonguss")],
        [pokemon("Milotic"), pokemon("Vaporeon")],
        [[move("Fake Out", [1, 2]), move("Knock Off", [-2, 1, 2])], [move("Spore", [-1, 1, 2])]],
    )

    first = agent.choose_action(envelope("moving", observation, [legal_action(template, "doubles_turn")]), CONTEXT)
    observation["turn"] = 2
    second = agent.choose_action(envelope("moving", observation, [legal_action(template, "doubles_turn")]), CONTEXT)

    assert (first["slot_0"]["move_id"], second["slot_0"]["move_id"]) == ("fakeout", "knockoff")


# -- (4) and (6) fallbacks -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [
        lambda: envelope("draft", None, draft_state().raw["legal_actions"]["actions"]),
        lambda: envelope("team_preview", ["not", "an", "object"], preview_state().raw["legal_actions"]["actions"]),
        lambda: turn_state("garbled"),
        lambda: envelope("teambuild", {}, preview_state().raw["legal_actions"]["actions"]),
    ],
    ids=["draft without observation", "preview list", "turn string", "unknown phase"],
)
def test_4_and_6_a_malformed_state_gives_the_template_only_fallback_and_one_unexpected_state_event(match_events, state):
    made = state()

    decision = models_off().choose_action(made, CONTEXT)

    errors = kinds(match_events(), "error")
    assert [error["type"] for error in errors] == ["unexpected_state"]
    assert decision is not None and decision != made.legal_actions[0].action_id


def test_6_the_preview_and_turn_fallbacks_are_legal_for_their_templates(match_events):
    agent = models_off()

    lineup = agent.choose_action(
        envelope("team_preview", "garbled", preview_state().raw["legal_actions"]["actions"]), CONTEXT
    )
    turn = agent.choose_action(turn_state(["garbled"]), CONTEXT)

    assert lineup_choice(lineup, SHOWDOWN["team_preview_request"]) == "team 1234"
    assert turn_view().choice(turn) == "move 1 1, move 1 1"
    assert [error["type"] for error in kinds(match_events(), "error")] == ["unexpected_state", "unexpected_state"]


def test_a_failure_of_the_agent_s_own_plays_the_fallback_with_an_unhandled_error_event(match_events, monkeypatch):
    def broken(*_args: Any) -> list:
        raise RuntimeError("the turn policy broke")

    monkeypatch.setattr(agent_module, "rank_actions", broken)

    turn = models_off().choose_action(turn_state(), CONTEXT)

    (error,) = kinds(match_events(), "error")
    assert turn_view().choice(turn) == "move 1 1, move 1 1"
    assert (error["type"], error["exception"]) == ("unhandled", "RuntimeError")
    assert "the turn policy broke (raised at test_agent.py" in error["message"]


def test_an_action_the_template_refuses_is_never_sent(match_events, monkeypatch):
    monkeypatch.setattr(agent_module, "is_legal", lambda template, action: False)

    lineup = models_off().choose_action(preview_state(), CONTEXT)

    (error,) = kinds(match_events(), "error")
    assert (error["type"], error["exception"]) == ("unhandled", "IllegalAction")
    assert lineup_choice(lineup, SHOWDOWN["team_preview_request"]) == "team 1234"


# -- (7) parse degradations ------------------------------------------------------------------------


def test_a_protocol_log_that_cannot_be_read_is_one_parse_degradation_and_the_turn_is_still_played(match_events):
    observation = copy.deepcopy(turn_view().observation())
    observation["protocol_log"] = "|turn|1"
    briefed = dataclasses.replace(SETTINGS, model_choices=True, model_briefing=True, model_decides=False)

    turn = PokemonAgent(settings=briefed, choice_source=Answering("T1")).choose_action(turn_state(observation), CONTEXT)

    events = match_events()
    (decision,) = kinds(events, "pokemon_decision")
    assert turn_view().choice(turn)
    assert (decision["parse_degradations"], decision["degraded_keys"]) == (1, ["protocol_log"])
    assert kinds(events, "error") == []


def test_7_a_doubles_observation_missing_boosts_and_weather_is_played_with_two_parse_degradations(match_events):
    observation = copy.deepcopy(turn_view().observation())
    del observation["weather"]
    for entry in [
        *observation["active_pokemon"],
        *observation["opponent_active_pokemon"],
        *observation["team"].values(),
    ]:
        entry.pop("boosts", None)
    for entry in [
        *observation["opponent_team"].values(),
        *(e for listed in observation["available_switches"] for e in listed),
    ]:
        entry.pop("boosts", None)

    turn = models_off().choose_action(turn_state(observation), CONTEXT)

    events = match_events()
    (decision,) = kinds(events, "pokemon_decision")
    assert turn_view().choice(turn)
    assert (decision["parse_degradations"], decision["degraded_keys"]) == (2, ["boosts", "weather"])
    assert kinds(events, "error") == []


# -- the observation's clock -----------------------------------------------------------------------


def clocked(state: GameState, decision_seconds: float, bank_seconds: float) -> GameState:
    """`state` with its observation's clock reporting `decision_seconds` and `bank_seconds` left."""
    clock = {"decision_seconds_left": decision_seconds, "bank_seconds_left": bank_seconds}
    observation = {**state.raw["observation"], "clock": clock}
    return envelope(state.raw["phase"], observation, state.raw["legal_actions"]["actions"], state.state_version)


def test_the_clock_of_a_lineup_or_turn_observation_bounds_the_model_s_deadline_and_is_recorded(match_events):
    source = Answering(choice="T2")
    agent = PokemonAgent(settings=dataclasses.replace(SETTINGS, model_choices=True), choice_source=source)

    agent.choose_action(clocked(preview_state(), 25, 420), CONTEXT)
    turn = agent.choose_action(clocked(turn_state(), 41, 85), CONTEXT)
    agent.choose_action(turn_state(), CONTEXT)

    events = match_events()
    decisions = kinds(events, "pokemon_decision")
    fields = ("phase", "clock_decision_seconds_left", "clock_bank_seconds_left", "deadline_ms")
    assert [[decision[key] for key in fields] for decision in decisions] == [
        ["team_preview", 25, 420, 15000],
        ["moving", 41, 85, 0],
        ["moving", None, None, 5000],
    ]
    assert decisions[1]["outcome"] == "not_asked" and decisions[2]["outcome"] == "used"
    assert [request.deadline_ms for request in source.choice_requests] == [15000, 5000]
    assert turn_view().choice(turn) and [decision["parse_degradations"] for decision in decisions] == [0, 0, 0]
    assert kinds(events, "error") == []


# -- what the model reads with model_decides --------------------------------------------------------

SET_LINE_TEXT = re.compile(r"^.+ \[[^\]]+\] @ .+ \| HP \d+ .* Spe \d+ \| moves: .+$")
LINEUP_TEXT = re.compile(r"^bring [^;]+, [^;]+, [^;]+, [^;]+; lead [^;]+ and [^;]+$")
TURN_TEXT = re.compile(r"^slot 0: .+; slot 1: .+$")
SCORED = re.compile(r"; score -?\d+")
SCORE_WORD = re.compile("score")


def asked_descriptions(decides: bool) -> dict[str, list[str]]:
    """The option descriptions of a draft pick, a team preview and a doubles turn the agent asks a stub about, with
    model_decides as given and the model briefing off."""
    source = Answering(choice="T1")
    settings = dataclasses.replace(SETTINGS, model_choices=True, model_briefing=False, model_decides=decides)
    PokemonAgent(settings=settings, choice_source=source).choose_action(draft_state(), CONTEXT)
    agent = PokemonAgent(settings=settings, choice_source=source)
    agent.choose_action(preview_state(), CONTEXT)
    agent.choose_action(turn_state(), CONTEXT)
    by_phase = {request.options[0].id[0]: request for request in source.choice_requests}
    return {phase: [option.description for option in by_phase[phase].options] for phase in "CLT"}


def matching(pattern: re.Pattern[str], texts: list[str]) -> list[str]:
    """The texts `pattern` finds anything in."""
    return [text for text in texts if pattern.search(text)]


def offered_scores(state: GameState) -> list:
    memory = DraftMemory()
    memory.update(state.raw["observation"], US, Reader())
    return score_cards(memory, offered_card_ids(state.legal_actions), SETTINGS.tuning.draft)


def test_decides_on_every_option_is_described_without_the_script_s_score():
    described = asked_descriptions(decides=True)

    set_lines = {set_line(score.card, guessed=False) for score in offered_scores(draft_state())}
    assert set(described["C"]) <= set_lines
    assert matching(SET_LINE_TEXT, described["C"]) == described["C"] != []
    assert matching(LINEUP_TEXT, described["L"]) == described["L"] != []
    assert matching(TURN_TEXT, described["T"]) == described["T"] != []
    assert matching(SCORE_WORD, [*described["C"], *described["L"], *described["T"]]) == []


def test_decides_off_every_option_is_described_as_today_with_the_script_s_score():
    described = asked_descriptions(decides=False)

    scores = offered_scores(draft_state())
    assert described["C"] == [score.description() for score in scores[: SETTINGS.top_k]]
    every = [*described["C"], *described["L"], *described["T"]]
    assert matching(SCORED, every) == every


# -- maximin ---------------------------------------------------------------------------------------


def with_maximin(settings: PokemonSettings, **switches: bool) -> PokemonSettings:
    """`settings` with `[maximin]` enabled, and its other switches as given."""
    maximin = dataclasses.replace(settings.tuning.maximin, enabled=True, **switches)
    return dataclasses.replace(settings, tuning=dataclasses.replace(settings.tuning, maximin=maximin))


MAXIMIN_ON = with_maximin(SETTINGS)


def test_maximin_every_phase_is_ranked_by_maximin_legally_and_the_event_says_so(match_events):
    agent = PokemonAgent(settings=MAXIMIN_ON)

    pick = agent.choose_action(draft_state(), CONTEXT)
    lineup = agent.choose_action(preview_state(), CONTEXT)
    turn = agent.choose_action(turn_state(), CONTEXT)

    events = match_events()
    decisions = kinds(events, "pokemon_decision")
    assert [(decision["phase"], decision["ranking"]) for decision in decisions] == [
        ("draft", "maximin"),
        ("team_preview", "maximin"),
        ("moving", "maximin"),
    ]
    assert f"draft_pick:{pick.action['card_id']}" in [action.action_id for action in draft_state().legal_actions]
    assert lineup_choice(lineup, SHOWDOWN["team_preview_request"]).startswith("team ") and turn_view().choice(turn)
    assert kinds(events, "error") == []


def test_maximin_off_the_event_has_no_ranking_field(match_events):
    agent = models_off()

    agent.choose_action(draft_state(), CONTEXT)
    agent.choose_action(preview_state(), CONTEXT)
    agent.choose_action(turn_state(), CONTEXT)

    assert all("ranking" not in decision for decision in kinds(match_events(), "pokemon_decision"))


def _drafted(agent: PokemonAgent, ours: list[dict], theirs: list[dict]) -> None:
    """Have the agent's draft memory hold `ours` and `theirs` as both rosters, every card seen in the pool."""
    agent.choose_action(envelope("draft", {"available_cards": [*ours, *theirs], "rosters": {}}, []), CONTEXT)
    rosters = {US: ours, "agent_them": theirs}
    drafted = {
        "available_cards": [],
        "available_card_ids": [],
        "rosters": {
            seat: [{"card_id": entry["card_id"], "species": entry["species"]} for entry in held]
            for seat, held in rosters.items()
        },
    }
    agent.choose_action(
        envelope("draft", drafted, [legal_action({"type": "draft_pick", "card_id": "x"}, "draft_pick:x")]), CONTEXT
    )


def _attack_or_protect(agent: PokemonAgent) -> dict:
    """The doubles turn where our Urshifu at 35% may attack or Protect against a faster Flutter Mane."""
    _drafted(
        agent,
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
    return agent.choose_action(envelope("moving", observation, [legal_action(template, "doubles_turn")]), CONTEXT)


def test_maximin_1_the_agent_plays_protect_and_the_partner_s_attack_where_the_script_attacks():
    script = _attack_or_protect(models_off())
    maximin = _attack_or_protect(PokemonAgent(settings=MAXIMIN_ON))

    assert script["slot_0"]["move_id"] == "closecombat"
    assert maximin["slot_0"]["move_id"] == "protect" and maximin["slot_1"]["move_id"] == "woodhammer"


def test_maximin_with_no_time_left_on_the_clock_the_script_s_ranking_is_played(match_events):
    late = clocked(turn_state(), 9, 300)

    played = PokemonAgent(settings=MAXIMIN_ON).choose_action(late, CONTEXT)
    script = models_off().choose_action(late, CONTEXT)

    maximin_decision, script_decision = kinds(match_events(), "pokemon_decision")
    assert played == script and maximin_decision["ranking"] == "script" and "ranking" not in script_decision


def test_maximin_without_the_draft_lookahead_the_draft_is_ranked_by_the_script(match_events):
    PokemonAgent(settings=with_maximin(SETTINGS, draft_lookahead=False)).choose_action(draft_state(), CONTEXT)

    (decision,) = kinds(match_events(), "pokemon_decision")
    assert decision["ranking"] == "script"


@pytest.mark.parametrize("decides", [False, True], ids=["ranked", "decides"])
def test_maximin_each_option_a_model_reads_ends_with_the_opponent_s_best_reply(decides):
    source = Answering(choice="T1")
    settings = dataclasses.replace(MAXIMIN_ON, model_choices=True, model_briefing=False, model_decides=decides)
    PokemonAgent(settings=settings, choice_source=source).choose_action(draft_state(), CONTEXT)
    agent = PokemonAgent(settings=settings, choice_source=source)
    agent.choose_action(preview_state(), CONTEXT)
    agent.choose_action(turn_state(), CONTEXT)

    by_phase = {request.options[0].id[0]: request for request in source.choice_requests}
    described = {phase: [option.description for option in by_phase[phase].options] for phase in "CLT"}

    assert all("; their likely next pick: " in text for text in described["C"])
    assert all("; their best reply: bring " in text and "; worst case: " in text for text in described["L"])
    assert all("; their best reply: their A " in text and "; worst case: we deal " in text for text in described["T"])
    if decides:
        assert matching(SCORE_WORD, [*described["C"], *described["L"], *described["T"]]) == []


# -- whole matches ---------------------------------------------------------------------------------


@pytest.fixture
def runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(paths.RUNS_DIR_ENV, str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("opponent", ["first", "random", "self"])
def test_whole_practice_matches_finish_with_no_unhandled_error(runs_dir, opponent):
    results = [
        play_practice_match(seed, models_off, opponent, helper_command=FAKE_HELPER) for seed in range(PRACTICE_MATCHES)
    ]

    assert all(result.termination_reason == "completed" and result.winner is not None for result in results)
    assert not any(result.unhandled_error for result in results)
    assert all(result.decisions > 6 and result.fallbacks == 0 for result in results)


@pytest.mark.parametrize("opponent", ["first", "self"])
def test_maximin_whole_practice_matches_finish_with_no_unhandled_error(runs_dir, opponent):
    def maximin_agent() -> PokemonAgent:
        return PokemonAgent(settings=MAXIMIN_ON)

    results = [
        play_practice_match(seed, maximin_agent, opponent, helper_command=FAKE_HELPER)
        for seed in range(PRACTICE_MATCHES)
    ]

    # The fake battle may end in a tie, both last Pokémon fainting to the end-of-turn chip together.
    assert all(result.termination_reason == "completed" for result in results)
    assert not any(result.unhandled_error for result in results)
    assert all(result.decisions > 6 and result.fallbacks == 0 for result in results)
