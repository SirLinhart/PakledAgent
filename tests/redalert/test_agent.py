"""The Red Alert decision-maker under the vendored runtime: a match it has nothing to order in, from start to end."""

from __future__ import annotations

import dataclasses
import threading
from typing import Iterator

import pytest

from altruagent import WAIT, DecisionContext, GameState
from altruagent.runner import run_game
from situations import new_agent, tracked_items
from switches_off import SWITCHES_OFF, settings_models_off
from ucla_agent.core import records
from ucla_agent.core.config import ConfigError
from ucla_agent.core.decide import STALE_DISCARDED, TIMEOUT, USED, ChainSource, ChoiceRequest, DecisionSource, Option
from ucla_agent.core.decide.journal import SourceCall
from ucla_agent.games.redalert import agent as agent_module
from ucla_agent.games.redalert.agent import RedAlertAgent
from ucla_agent.games.redalert.settings import RedAlertSettings
from ucla_agent.games.redalert.tuning import ScoutingTuning, Tuning

EMPTY_LEDGER = {
    "orders_sent": 0,
    "orders_rejected_by_reason": {},
    "orders_lost": 0,
    "dropped_invalid": 0,
    "duplicate_production_orders": 0,
}
RESULT = {"is_terminal": True, "status": "completed", "returns": {"agent-1": 0.5}, "termination_reason": "time_limit"}


def _context() -> DecisionContext:
    return DecisionContext(session_id="ra-session", tournament_id=None, game_type="red_alert", agent_id="agent-1")


def _state(*, version: int, terminal: bool = False) -> GameState:
    return GameState.from_mcp_state(
        {
            "session_id": "ra-session",
            "game_type": "red_alert",
            "state_version": version,
            "phase": "finished" if terminal else "playing",
            "is_terminal": terminal,
            "is_current_actor": not terminal,
            "pacing": {"mode": "realtime"},
            "legal_actions": {"actions": [], "state_version": version},
        }
    )


class _RealtimeGameStub:
    """A real-time match that is over after the agent has been asked for one move."""

    def __init__(self) -> None:
        self.moves_sent = 0
        self.waits = 0

    def get_state(self) -> GameState:
        return _state(version=1)

    def wait_for_update(self, **_since) -> GameState:
        self.waits += 1
        return _state(version=2, terminal=True)

    def play_action(self, **_move) -> dict:
        self.moves_sent += 1
        return {}

    def get_result(self) -> dict:
        return RESULT


def test_states_that_show_nothing_of_the_game_are_answered_with_wait():
    agent = RedAlertAgent(settings=settings_models_off())

    assert agent.choose_action(_state(version=1), _context()) is WAIT
    assert agent.choose_action(_state(version=2), _context()) is WAIT


def test_the_vendored_runtime_plays_a_real_time_match_to_its_end_without_a_move():
    game = _RealtimeGameStub()

    final_state = run_game(game, _context(), RedAlertAgent(settings=settings_models_off()))

    assert final_state.is_terminal and final_state.termination_reason == "time_limit"
    assert (game.moves_sent, game.waits) == (0, 1)


def test_match_end_logs_one_ledger_summary_with_empty_counters(run_folder, read_jsonl):
    records.start_match(_context(), None)
    new_agent()

    records.finish_match(RESULT)

    (events_path,) = run_folder.glob("runs/*/ra-session__seat/events.jsonl")
    lines = read_jsonl(events_path)
    assert [line["kind"] for line in lines] == ["ledger_summary", "result"]
    assert lines[0]["data"] == EMPTY_LEDGER


def test_finalize_without_an_open_record_writes_nothing(run_folder):
    RedAlertAgent().finalize()

    assert not (run_folder / "runs").exists()


# -- the choosers of the scout slots' destinations ---------------------------------------------------------------------

SCOUT_SLOTS = dataclasses.replace(Tuning(), scouting=ScoutingTuning(scout_slots=True))
BOTH_MODEL_CHOICES = RedAlertSettings(model_plan_choice=True, model_scout_choice=True)


class _HeldSource(DecisionSource):
    """A decision source that answers only once it is let go: a question put to it stays in flight until then."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.let_go = threading.Event()

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        self.let_go.wait(timeout=5)
        return [SourceCall(self.name, None, None, 0, TIMEOUT)]


def _question(tag: str) -> ChoiceRequest:
    return ChoiceRequest("Where next?", [Option("1", "here"), Option("2", "there")], "", 1500, tag)


class _HeldChains:
    """Stands in for `build_chain`: each chain it builds is a held source, kept in the order built."""

    def __init__(self) -> None:
        self.sources: list[_HeldSource] = []

    def __call__(self, name: str) -> _HeldSource:
        source = _HeldSource(name)
        self.sources.append(source)
        return source

    def let_go(self) -> None:
        """Let every source answer, so that no worker thread outlives the test."""
        for source in self.sources:
            source.let_go.set()


@pytest.fixture
def held_chains(monkeypatch) -> Iterator[_HeldChains]:
    chains = _HeldChains()
    monkeypatch.setattr(agent_module, "build_chain", chains)
    yield chains
    chains.let_go()


def test_scout_choice_the_plan_chooser_and_the_two_scout_choosers_run_apart_and_a_pending_plan_question_blocks_none(
    held_chains,
):
    agent = RedAlertAgent(items=tracked_items(), tuning=SCOUT_SLOTS, settings=BOTH_MODEL_CHOICES)
    plan, (first_scout, second_scout) = agent.ctx.chooser, agent.ctx.scout_choosers

    plan_asked = (plan.submit(_question("1000")), plan.submit(_question("1001")))
    scouts_asked = (first_scout.submit(_question("1002")), second_scout.submit(_question("1002")))

    assert [source.name for source in held_chains.sources] == ["red_alert_plan", "red_alert_scout", "red_alert_scout"]
    assert plan_asked == (True, False) and scouts_asked == (True, True)
    assert len({id(plan), id(first_scout), id(second_scout)}) == 3


@pytest.mark.parametrize(
    "tuning, settings",
    [(SCOUT_SLOTS, RedAlertSettings(model_plan_choice=True)), (SWITCHES_OFF, BOTH_MODEL_CHOICES)],
    ids=["model scout choice off", "scout slots off"],
)
def test_scout_choice_off_or_without_the_scout_slots_no_scout_chain_is_built_and_no_scout_chooser_made(
    monkeypatch, tuning, settings
):
    built: list[str] = []
    monkeypatch.setattr(agent_module, "build_chain", lambda name: built.append(name) or ChainSource([], name=name))

    agent = RedAlertAgent(items=tracked_items(), tuning=tuning, settings=settings)

    assert built == ["red_alert_plan"] and agent.ctx.scout_choosers == ()


def test_scout_choice_a_scout_chain_that_cannot_be_built_is_said_and_its_choosers_ask_nobody(match_record, monkeypatch):
    def broken(name: str) -> ChainSource:
        raise ConfigError(f"no chain named {name}")

    monkeypatch.setattr(agent_module, "build_chain", broken)

    agent = RedAlertAgent(items=tracked_items(), tuning=SCOUT_SLOTS, settings=RedAlertSettings(model_scout_choice=True))

    errors = match_record("error")
    assert [(error["where"], error["exception"]) for error in errors] == [("redalert.config", "ConfigError")] * 2
    assert [chooser.poll("1") for chooser in agent.ctx.scout_choosers] == [None, None]


class _AnsweringSource(DecisionSource):
    """A decision source that answers every question at once with its first option."""

    def __init__(self, name: str) -> None:
        self.name = name

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        return [SourceCall(self.name, request.options[0].id, None, 1, USED)]


def _result_at(chooser, tag: str):
    """The result a chooser hands over, polled with a state tag until the source has answered."""
    for _ in range(500):
        result = chooser.poll(tag)
        if result is not None:
            return result
        threading.Event().wait(0.01)
    raise AssertionError("the source never answered")


@pytest.mark.parametrize("now, outcome", [("1250", USED), ("1251", STALE_DISCARDED)], ids=["250 ticks", "251 ticks"])
def test_scout_choice_a_scout_chooser_discards_an_answer_older_than_choice_max_age_ticks(monkeypatch, now, outcome):
    monkeypatch.setattr(agent_module, "build_chain", _AnsweringSource)
    agent = RedAlertAgent(items=tracked_items(), tuning=SCOUT_SLOTS, settings=RedAlertSettings(model_scout_choice=True))
    chooser = agent.ctx.scout_choosers[0]

    chooser.submit(_question("1000"))

    assert Tuning().loop.choice_max_age_ticks == 250
    assert _result_at(chooser, now).outcome == outcome
