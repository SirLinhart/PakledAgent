"""The result hook: an in-memory wrap of the vendored runner's final-state function."""

from __future__ import annotations

import inspect
import types

import pytest

from altruagent import RESIGN, DecisionContext, GameState, runner
from altruagent.runner import run_game
from ucla_agent.core import records, runtime_hooks

RESULT = {"is_terminal": True, "status": "completed", "returns": {"agent-1": 1.0}, "termination_reason": "victory"}

WEREWOLF_SESSION_ID = "werewolf-1"
WEREWOLF_SEAT = 2
# A Werewolf resign takes only the resigning player out: the answer says the game goes on without them.
RESIGN_ANSWER = {"session_id": WEREWOLF_SESSION_ID, "is_terminal": False, "eliminated": True, "status": "active"}
# The game's own result once the other players have finished it.
FINAL_RESULT = {
    "session_id": WEREWOLF_SESSION_ID,
    "is_terminal": True,
    "status": "completed",
    "returns": {"agent-1": 0.0, "agent-2": 1.0},
    "termination_reason": "completed",
    "your_return": 0.0,
    "result": {"seats": {"agent-1": {"outcome": "resigned"}}},
}


def _last_state() -> GameState:
    return GameState.from_mcp_state({"session_id": "session-1", "state_version": 4310, "phase": "finished"})


def _werewolf_state(version: int, *, my_turn: bool = False, eliminated: bool = False, over: bool = False) -> GameState:
    state = {
        "session_id": WEREWOLF_SESSION_ID,
        "game_type": "werewolf",
        "state_version": version,
        "phase": "moving",
        "is_terminal": over,
        "is_current_actor": my_turn,
        "status": "completed" if over else "active",
    }
    if my_turn:
        vote = {"action_id": "vote:3", "label": "Vote for player 3"}
        state["legal_actions"] = {"actions": [vote], "state_version": version}
    if eliminated:
        state["eliminated"] = True
    return GameState.from_mcp_state(state)


class _WerewolfSessionStub:
    """A Werewolf game in which the agent resigns on its first turn and the other players then finish the game."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.resigned = False

    def get_state(self) -> GameState:
        self.calls.append("get_state")
        return _werewolf_state(2, eliminated=True) if self.resigned else _werewolf_state(1, my_turn=True)

    def resign(self) -> dict:
        self.calls.append("resign")
        self.resigned = True
        return RESIGN_ANSWER

    def wait_for_update(self, **_since) -> GameState:
        self.calls.append("wait_for_update")
        return _werewolf_state(9, eliminated=True, over=True)

    def get_result(self) -> dict:
        self.calls.append("get_result")
        return FINAL_RESULT


def _werewolf_context() -> DecisionContext:
    return DecisionContext(
        session_id=WEREWOLF_SESSION_ID,
        tournament_id=None,
        game_type="werewolf",
        agent_id="agent-1",
        seat_position=WEREWOLF_SEAT,
    )


def _resign(state: GameState, context: DecisionContext):
    return RESIGN


def test_the_vendored_runner_has_a_final_state_function_taking_two_positional_arguments():
    parameters = list(inspect.signature(runner._terminal_game_state).parameters.values())

    assert callable(runner._terminal_game_state)
    assert [parameter.kind for parameter in parameters] == [inspect.Parameter.POSITIONAL_OR_KEYWORD] * 2
    assert runtime_hooks.HOOKED_FUNCTION_NAME == "_terminal_game_state"


def test_the_hooked_function_hands_the_result_over_and_returns_what_the_original_returns():
    original = runner._terminal_game_state
    results = []

    installed = runtime_hooks.install(results.append)
    final_state = runner._terminal_game_state(_last_state(), RESULT)

    assert installed is True
    assert runner._terminal_game_state is not original
    assert results == [RESULT]
    assert final_state == original(_last_state(), RESULT)
    assert (final_state.is_terminal, final_state.returns, final_state.termination_reason) == (
        True,
        {"agent-1": 1.0},
        "victory",
    )


def test_the_listener_gets_the_result_before_the_original_runs(monkeypatch):
    order = []

    def original(last_state, result):
        order.append("original")
        return "the final state"

    monkeypatch.setattr(runner, "_terminal_game_state", original)
    runtime_hooks.install(lambda result: order.append("listener"))

    assert runner._terminal_game_state(_last_state(), RESULT) == "the final state"
    assert order == ["listener", "original"]


def test_a_listener_that_raises_is_reported_on_standard_error_and_the_runtime_gets_its_value(run_folder, capsys):
    def failing_listener(result):
        raise RuntimeError("the record is closed")

    original = runner._terminal_game_state
    runtime_hooks.install(failing_listener)

    final_state = runner._terminal_game_state(_last_state(), RESULT)

    captured = capsys.readouterr()
    assert final_state == original(_last_state(), RESULT)
    assert captured.out == ""
    assert captured.err.startswith("The match result could not be recorded:\nTraceback")
    assert "RuntimeError: the record is closed" in captured.err


def test_installing_twice_wraps_once_and_keeps_the_first_listener():
    first, second = [], []

    assert runtime_hooks.install(first.append) is True
    wrapped_once = runner._terminal_game_state
    assert runtime_hooks.install(second.append) is True
    runner._terminal_game_state(_last_state(), RESULT)

    assert runner._terminal_game_state is wrapped_once
    assert (first, second) == ([RESULT], [])


def test_the_wrap_keeps_the_name_of_the_function_it_replaces():
    runtime_hooks.install(lambda result: None)

    assert runner._terminal_game_state.__name__ == "_terminal_game_state"


def test_a_runner_without_the_function_is_left_untouched(monkeypatch):
    without_the_function = types.ModuleType(runner.__name__)
    for name, value in vars(runner).items():
        if name != "_terminal_game_state":
            setattr(without_the_function, name, value)
    monkeypatch.setattr(runtime_hooks, "runner", without_the_function)

    installed = runtime_hooks.install(lambda result: pytest.fail("no result can arrive without the hook"))

    assert installed is False
    assert not hasattr(without_the_function, "_terminal_game_state")


def test_after_a_werewolf_resign_the_listener_gets_only_the_final_result_once_the_game_ends():
    game = _WerewolfSessionStub()
    results = []
    runtime_hooks.install(results.append)

    final_state = run_game(game, _werewolf_context(), _resign, sleep=lambda seconds: None)

    assert game.calls == ["get_state", "resign", "get_state", "wait_for_update", "get_result"]
    assert results == [FINAL_RESULT]
    assert (final_state.is_terminal, final_state.returns, final_state.final_result) == (
        True,
        FINAL_RESULT["returns"],
        FINAL_RESULT,
    )


def test_after_a_werewolf_resign_the_match_record_ends_with_the_final_result(run_folder, read_jsonl):
    def open_the_record_and_resign(state: GameState, context: DecisionContext):
        records.start_match(context, state)
        return RESIGN

    run_game(_WerewolfSessionStub(), _werewolf_context(), open_the_record_and_resign, sleep=lambda seconds: None)

    (events_path,) = run_folder.glob(f"runs/*/{WEREWOLF_SESSION_ID}__{WEREWOLF_SEAT}/events.jsonl")
    result_events = [line for line in read_jsonl(events_path) if line["kind"] == "result"]
    assert [event["data"]["result"] for event in result_events] == [FINAL_RESULT]
