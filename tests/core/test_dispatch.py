"""Dispatch by game type: one decision-maker per match, and safe handling of a game that has none."""

from __future__ import annotations

import pytest

from agent.agent import create_agent
from altruagent import TERMINATE_MESSAGING, WAIT, DecisionContext, GameState, SendMessage
from altruagent.runner import run_game
from ucla_agent.core import dispatch, records, registry
from ucla_agent.core.dispatch import Dispatcher, UnsupportedGameAgent
from ucla_agent.games.redalert.agent import RedAlertAgent


def _context(game_type: str | None, session_id: str = "session-1") -> DecisionContext:
    return DecisionContext(session_id=session_id, tournament_id=None, game_type=game_type, agent_id="agent-1")


def _state(action_ids: tuple[str, ...] = (), *, version: int = 1, terminal: bool = False) -> GameState:
    return GameState.from_mcp_state(
        {
            "session_id": "session-1",
            "state_version": version,
            "phase": "moving",
            "is_terminal": terminal,
            "is_current_actor": not terminal,
            "legal_actions": {
                "actions": [{"action_id": action_id, "label": action_id} for action_id in action_ids],
                "state_version": version,
            },
        }
    )


class _RecordingDecisionMaker:
    """A decision-maker with every hook, remembering what it was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def choose_action(self, state, context):
        self.calls.append(("choose_action", state, context))
        return "chosen-action"

    def choose_message(self, state, context):
        self.calls.append(("choose_message", state, context))
        return SendMessage("hello")

    def on_action_result(self, result, context):
        self.calls.append(("on_action_result", result, context))


@pytest.fixture
def made(monkeypatch):
    """Every decision-maker the registered `red_alert` factory produces during the test."""
    registered_factory = registry.GAME_FACTORIES["red_alert"]
    instances = []

    def counting_factory():
        instances.append(registered_factory())
        return instances[-1]

    monkeypatch.setitem(registry.GAME_FACTORIES, "red_alert", counting_factory)
    return instances


@pytest.fixture
def recording(monkeypatch):
    """Registers the game type `recorded`, whose decision-makers remember their calls."""
    instances = []

    def factory():
        instances.append(_RecordingDecisionMaker())
        return instances[-1]

    monkeypatch.setitem(registry.GAME_FACTORIES, "recorded", factory)
    return instances


def _unsupported_game_events(run_folder, read_jsonl) -> list[dict]:
    records.finish_match({})
    return [
        line
        for events_path in run_folder.glob("runs/*/*/events.jsonl")
        for line in read_jsonl(events_path)
        if line["kind"] == "unsupported_game"
    ]


# -- a registered game ---------------------------------------------------------------------------


def test_red_alert_calls_go_to_one_instance_from_the_registered_factory(run_folder, made):
    dispatcher = create_agent()

    answers = [dispatcher.choose_action(_state(version=version), _context("red_alert")) for version in range(1, 6)]

    assert len(made) == 1 and isinstance(made[0], RedAlertAgent)
    assert answers == [WAIT] * 5


def test_every_call_is_forwarded_with_its_arguments_and_its_answer_returned(run_folder, recording):
    dispatcher, context = create_agent(), _context("recorded")
    first_state, second_state = _state(("a",)), _state(("b",), version=2)

    assert dispatcher.choose_action(first_state, context) == "chosen-action"
    dispatcher.on_action_result({"accepted": True}, context)
    message = dispatcher.choose_message(second_state, context)

    (decision_maker,) = recording
    assert isinstance(message, SendMessage) and message.content == "hello"
    assert decision_maker.calls == [
        ("choose_action", first_state, context),
        ("on_action_result", {"accepted": True}, context),
        ("choose_message", second_state, context),
    ]


@pytest.mark.parametrize("first_call", ["choose_action", "choose_message", "on_action_result"])
def test_the_decision_maker_is_created_by_whichever_call_comes_first(run_folder, recording, first_call):
    dispatcher, context = create_agent(), _context("recorded")
    argument = {"error": "INVALID_ACTION"} if first_call == "on_action_result" else _state(("a",))

    getattr(dispatcher, first_call)(argument, context)
    dispatcher.choose_action(_state(("a",)), context)

    (decision_maker,) = recording
    assert [call[0] for call in decision_maker.calls] == [first_call, "choose_action"]


def test_the_decision_maker_the_dispatcher_made_can_be_read_and_is_none_before_the_first_call(run_folder, made):
    dispatcher = create_agent()
    before = dispatcher.decision_maker

    dispatcher.choose_action(_state(), _context("red_alert"))

    assert before is None and dispatcher.decision_maker is made[0]


def test_two_matches_get_two_distinct_decision_makers(run_folder, made):
    first_dispatcher, second_dispatcher = create_agent(), create_agent()

    first_dispatcher.choose_action(_state(), _context("red_alert", "match-a"))
    second_dispatcher.choose_action(_state(), _context("red_alert", "match-b"))
    first_dispatcher.choose_action(_state(version=2), _context("red_alert", "match-a"))

    assert first_dispatcher is not second_dispatcher
    assert len(made) == 2 and made[0] is not made[1]


def test_dispatchers_share_no_state_across_game_types(run_folder, made, recording):
    red_alert_dispatcher, recorded_dispatcher = create_agent(), create_agent()

    assert red_alert_dispatcher.choose_action(_state(), _context("red_alert")) is WAIT
    assert recorded_dispatcher.choose_action(_state(("a",)), _context("recorded")) == "chosen-action"
    assert red_alert_dispatcher.choose_action(_state(), _context("red_alert")) is WAIT

    assert len(made) == 1 and len(recording) == 1


def test_missing_optional_hooks_get_the_runtime_defaults(run_folder, made):
    dispatcher, context = create_agent(), _context("red_alert")

    assert dispatcher.choose_message(_state(), context) is TERMINATE_MESSAGING
    assert dispatcher.on_action_result({"accepted": True}, context) is None
    assert len(made) == 1


def test_the_match_record_is_opened_once_by_the_first_call(run_folder, monkeypatch, made):
    opened = []
    monkeypatch.setattr(records, "start_match", lambda context, state: opened.append((context, state)))
    dispatcher, context, first_state = create_agent(), _context("red_alert"), _state()

    dispatcher.choose_action(first_state, context)
    dispatcher.choose_action(_state(version=2), context)
    dispatcher.on_action_result({}, context)

    assert opened == [(context, first_state)]


# -- a game with no decision-maker ---------------------------------------------------------------


def test_unknown_game_plays_the_first_legal_action(run_folder):
    state = _state(("vote:1", "vote:2"))

    assert create_agent().choose_action(state, _context("unknown_game")) is state.legal_actions[0]


def test_unknown_game_waits_when_there_is_no_legal_action(run_folder):
    assert create_agent().choose_action(_state(), _context("unknown_game")) is WAIT


def test_unknown_game_closes_the_messaging_round(run_folder):
    assert create_agent().choose_message(_state(), _context("unknown_game")) is TERMINATE_MESSAGING


def test_unknown_game_is_recorded_once_across_three_calls(run_folder, read_jsonl):
    dispatcher, context = create_agent(), _context("unknown_game")

    dispatcher.choose_action(_state(("vote:1", "vote:2")), context)
    dispatcher.choose_action(_state(), context)
    dispatcher.choose_message(_state(), context)

    events = _unsupported_game_events(run_folder, read_jsonl)
    assert [event["data"] for event in events] == [{"game_type": "unknown_game"}]


def test_a_match_without_a_game_type_is_treated_as_unsupported(run_folder, read_jsonl):
    dispatcher, context = create_agent(), _context(None)

    assert dispatcher.choose_action(_state(), context) is WAIT
    dispatcher.on_action_result({"error": "INVALID_ACTION", "detail": "refused"}, context)

    events = _unsupported_game_events(run_folder, read_jsonl)
    assert [event["data"] for event in events] == [{"game_type": None}]


def test_an_unknown_game_gets_one_unsupported_game_agent_for_the_whole_match(run_folder, monkeypatch):
    created = []

    def counting_factory():
        created.append(UnsupportedGameAgent())
        return created[-1]

    monkeypatch.setattr(dispatch, "UnsupportedGameAgent", counting_factory)
    dispatcher = create_agent()

    for _ in range(3):
        dispatcher.choose_action(_state(), _context("unknown_game"))

    assert len(created) == 1


# -- under the vendored runtime ------------------------------------------------------------------


class _TurnBasedGameStub:
    """A turn-based match of an unregistered game that ends after one move."""

    def __init__(self) -> None:
        self.played: list[dict] = []

    def get_state(self) -> GameState:
        return _state(("vote:1", "vote:2"))

    def play_action(self, **move) -> dict:
        self.played.append(move)
        return {"accepted": True, "state": {"session_id": "session-1", "state_version": 2, "is_terminal": True}}

    def get_result(self) -> dict:
        return {"is_terminal": True, "status": "completed", "termination_reason": "normal"}


def test_the_vendored_runtime_finishes_an_unsupported_game_through_the_dispatcher(run_folder, read_jsonl):
    game = _TurnBasedGameStub()

    final_state = run_game(game, _context("unknown_game"), create_agent())

    assert final_state.is_terminal and final_state.termination_reason == "normal"
    assert [(move["action_id"], move["state_version"]) for move in game.played] == [("vote:1", 1)]
    assert len(_unsupported_game_events(run_folder, read_jsonl)) == 1


# -- the registry and the factory ----------------------------------------------------------------


def test_supported_games_lists_pokemon_red_alert_and_werewolf():
    assert registry.supported_games() == ["pokemon_vgc_doubles_draft", "red_alert", "werewolf"]


def test_create_agent_returns_a_new_dispatcher_each_time():
    first, second = create_agent(), create_agent()

    assert isinstance(first, Dispatcher) and isinstance(second, Dispatcher)
    assert first is not second
