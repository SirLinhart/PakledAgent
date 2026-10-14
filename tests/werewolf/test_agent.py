"""The Werewolf decision-maker call by call: the daily ledger event, the dead seat, failures inside a call, its chains.

Whole games are in `test_full_games.py`; here each call is given a hand-built
state. No test reaches a model server.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path
from typing import Callable

import pytest
from werewolf_states import DAY_VOTE, MESSAGING, NIGHT_KILL, VILLAGER, WOLF, chat, raw_state
from werewolf_stubs import Answering

from altruagent import TERMINATE_MESSAGING, DecisionContext, GameState, SendMessage
from ucla_agent.core import paths, records
from ucla_agent.games.werewolf.agent import WerewolfAgent
from ucla_agent.games.werewolf.chat import PAKLED_LINES
from ucla_agent.games.werewolf.settings import WerewolfSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = WerewolfSettings.from_files(REPO_ROOT)
MODELS_OFF = dataclasses.replace(SETTINGS, model_choices=False, model_messages=False)
SESSION_ID = "werewolf-agent-test"
CONTEXT = DecisionContext(SESSION_ID, None, "werewolf", "agent-3", seat_position=3)
SECONDS_LEFT = 100


class CountingSource(Answering):
    """An answering stub that also counts how often it was asked for its usage."""

    def __init__(self, choice: str | None = None, text: str | None = None) -> None:
        super().__init__(choice, text)
        self.usage_events = 0

    def write_usage_event(self) -> None:
        self.usage_events += 1


def game_state(legal: list[int] | None = None, **fields) -> GameState:
    """A state of seat 3 (see `werewolf_states.raw_state`), with legal actions when `legal` is given."""
    raw = {**raw_state(**fields), "messaging_seconds_left": SECONDS_LEFT}
    if legal is not None:
        actions = [{"action_id": str(action), "label": f"Player{action}", "input": {}} for action in legal]
        raw = {**raw, "is_current_actor": True, "legal_actions": {"actions": actions}}
    return GameState.from_mcp_state(raw)


@pytest.fixture
def record(run_folder: Path, read_jsonl) -> Callable[[], list[dict]]:
    """Opens a match record; the returned function closes it and gives its events, kind and data."""
    records.start_match(CONTEXT, None)

    def close_and_read() -> list[dict]:
        records.finish_match({"is_terminal": True})
        (events_path,) = run_folder.glob(f"runs/*/{SESSION_ID}__*/events.jsonl")
        return [{"kind": event["kind"], "data": event["data"]} for event in read_jsonl(events_path)]

    return close_and_read


def of_kind(events: list[dict], kind: str) -> list[dict]:
    return [event["data"] for event in events if event["kind"] == kind]


def test_the_first_state_of_each_window_writes_one_ledger_event_with_facts_claims_and_beliefs(record):
    agent = WerewolfAgent(MODELS_OFF)
    first_day = {"phase": MESSAGING, "messages": [chat(0, 5, "I am the seer. Player1 is a wolf.")]}

    agent.choose_message(game_state(**first_day), CONTEXT)
    agent.choose_message(game_state(**first_day), CONTEXT)
    agent.choose_action(game_state([0, 1, 2, 4, 5, 6, 7], sub_phase=DAY_VOTE), CONTEXT)
    agent.choose_message(game_state(phase=MESSAGING, day=2, dead=[(1, 6, VILLAGER, NIGHT_KILL)]), CONTEXT)

    ledgers = of_kind(record(), "werewolf_ledger")
    assert [(ledger["window"], ledger["day"]) for ledger in ledgers] == [(1, 1), (2, 2)]
    first = ledgers[0]
    assert first["facts"]["seat"] == 3 and first["private"] == {"role": VILLAGER, "allies": [], "results": {}}
    assert {"kind": "seer_claim", "seat": 5, "target": None, "result": None, "day": 1, "window": 1} in first["claims"]
    assert set(first["p_wolf"]) == set(first["p_seer"]) == {str(seat) for seat in range(7)}
    assert first["p_wolf"]["3"] == 0.0 and json.dumps(first)


def test_an_eliminated_seat_terminates_and_plays_the_first_legal_action_without_asking_a_model(record):
    source = Answering(choice="P4", text="I suspect Player4.")
    agent = WerewolfAgent(SETTINGS, choice_source=source, message_source=source)
    dead = {"day": 2, "dead": [(1, 3, VILLAGER, NIGHT_KILL)]}

    message = agent.choose_message(game_state(phase=MESSAGING, **dead), CONTEXT)
    action = agent.choose_action(game_state([7, 4, 5], sub_phase=DAY_VOTE, **dead), CONTEXT)

    assert message is TERMINATE_MESSAGING and action.action_id == "7"
    assert source.choice_requests == [] and source.text_requests == []
    assert of_kind(record(), "error") == []


def test_a_failure_inside_choose_action_is_an_unhandled_error_answered_with_a_legal_move_that_does_not_abstain(record):
    agent = WerewolfAgent(MODELS_OFF)
    unreadable = GameState.from_mcp_state(
        {"phase": "moving", "state_version": 4, "legal_actions": {"actions": [{"action_id": "7"}, {"action_id": "2"}]}}
    )

    action = agent.choose_action(unreadable, CONTEXT)

    (error,) = of_kind(record(), "error")
    assert action.action_id == "2"
    assert (error["where"], error["type"], error["exception"]) == ("choose_action", "unhandled", "LedgerError")
    assert "raised at ledger.py" in error["message"]


def test_a_failure_inside_choose_message_is_an_unhandled_error_answered_with_terminate(record):
    agent = WerewolfAgent(MODELS_OFF)

    message = agent.choose_message(GameState.from_mcp_state({"phase": MESSAGING}), CONTEXT)

    (error,) = of_kind(record(), "error")
    assert message is TERMINATE_MESSAGING and error["where"] == "choose_message" and error["type"] == "unhandled"


def test_a_living_seat_chats_through_the_agent(record):
    agent = WerewolfAgent(MODELS_OFF)

    message = agent.choose_message(game_state(phase=MESSAGING), CONTEXT)

    # The tracked settings play the Pakled persona: a villager's first chat of the window is a Pakled line.
    assert isinstance(message, SendMessage) and message.content in PAKLED_LINES
    assert len(of_kind(record(), "werewolf_chat")) == 1


def test_at_the_end_of_the_match_both_sources_write_their_usage_once(record):
    choices, messages = CountingSource(), CountingSource()
    WerewolfAgent(SETTINGS, choice_source=choices, message_source=messages)

    record()

    assert (choices.usage_events, messages.usage_events) == (1, 1)


def test_an_agent_made_from_the_tracked_files_builds_its_chains_without_a_word_to_the_record(record, run_folder):
    shutil.copytree(REPO_ROOT / "config", run_folder / "config")

    WerewolfAgent()

    assert of_kind(record(), "error") == []


def test_a_chain_the_configuration_does_not_name_is_a_handled_error_and_the_script_plays_on(
    record, run_folder, monkeypatch
):
    shutil.copytree(REPO_ROOT / "config", run_folder / "config")
    agent_toml = run_folder / "config" / "agent.toml"
    text = agent_toml.read_text(encoding="utf-8").replace('werewolf_choice = ["local:strong"]\n', "")
    agent_toml.write_text(text, encoding="utf-8")
    monkeypatch.setenv(paths.ROOT_ENV, str(run_folder))
    agent = WerewolfAgent(SETTINGS, message_source=Answering(text="I suspect Player0."))

    action = agent.choose_action(game_state([0, 1, 2, 4, 5, 6, 7], sub_phase=DAY_VOTE), CONTEXT)

    events = record()
    (error,) = of_kind(events, "error")
    assert (error["where"], error["type"]) == ("werewolf.chains", "handled")
    assert "no chain named werewolf_choice" in error["message"]
    (decision,) = of_kind(events, "werewolf_decision")
    assert decision["source"] == "script" and action.action_id == str(decision["script_choice"])


def test_a_wolf_s_kill_comes_from_its_own_plan(record):
    agent = WerewolfAgent(MODELS_OFF)

    action = agent.choose_action(
        game_state([0, 1, 2, 4, 6], role=WOLF, allies=[5], day=2, sub_phase="night_wolf"), CONTEXT
    )

    assert action.action_id == "0" and of_kind(record(), "werewolf_decision")[0]["sub_phase"] == "night_wolf"
