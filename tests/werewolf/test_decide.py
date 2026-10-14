"""Werewolf's model choices with stub choice sources: the candidates, the deadline, the legal fallback, the event,
and the settings and chains they are made with.

The numbered tests are the acceptance items of the model choices and their
deadlines. No test reaches a model server: the sources are the stubs of
`werewolf_stubs`, or a chain over a stand-in for the network.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import statistics
import time
from pathlib import Path

import httpx
import pytest
from werewolf_states import DAY_VOTE, MESSAGING, NIGHT_KILL, NIGHT_SEER, NIGHT_WOLF, SEER, VILLAGER, VOTE, WOLF, chat
from werewolf_states import raw_state
from werewolf_stubs import Answering, Guarded

from altruagent import DecisionContext, GameState
from ucla_agent.core import records
from ucla_agent.core.config import ConfigError
from ucla_agent.core.decide import USED, ChoiceRequest, Option, TextRequest, build_chain
from ucla_agent.games.werewolf import decide
from ucla_agent.games.werewolf.inference import infer
from ucla_agent.games.werewolf.ledger import Ledger
from ucla_agent.games.werewolf.settings import CHOICE_CHAIN, MESSAGE_CHAIN, WerewolfSettings
from ucla_agent.games.werewolf.tuning import Tuning

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_TOML = Path("config") / "agent.toml"
MODEL_SERVER_URL = "http://127.0.0.1:11500/v1/chat/completions"
SETTINGS = WerewolfSettings.from_files(REPO_ROOT)
TUNING = SETTINGS.tuning
SESSION_ID = "werewolf-decide-test"
# A wolf in seat 0 with its ally in seat 1, at night with seats 3 and 6 dead: the kill candidates are 2, 4 and 5.
NIGHT = {
    "seat": 0,
    "role": WOLF,
    "allies": [1],
    "day": 3,
    "sub_phase": NIGHT_WOLF,
    "dead": [(1, 3, VILLAGER, VOTE), (2, 6, VILLAGER, NIGHT_KILL)],
}
NIGHT_TARGETS = [2, 4, 5]
SHORT_DEADLINE_MS = 300
SLEEP_SECONDS = 1.0
SHORT_DEADLINE_LIMIT_SECONDS = 0.5
SCRIPT_RUNS = 50
SCRIPT_LIMIT_SECONDS = 0.050


def acting_state(legal: list[int], **fields) -> GameState:
    """A state in which our seat is to act, with `legal` as its legal actions (see `werewolf_states.raw_state`)."""
    raw = raw_state(**fields)
    actions = [{"action_id": str(action), "label": f"Player{action}", "input": {}} for action in legal]
    return GameState.from_mcp_state({**raw, "is_current_actor": True, "legal_actions": {"actions": actions}})


def decided(game_state: GameState, source, settings: WerewolfSettings = SETTINGS) -> int:
    """The seat played for a state, decided by a ledger that has taken in that state alone."""
    ledger = Ledger(TUNING.chat)
    ledger.update(game_state)
    action = decide.decide_action(game_state, ledger, infer(ledger, TUNING.inference), settings, source)
    return int(action.action_id)


# -- (1) to (6) the choice ---------------------------------------------------------------------------


def test_the_kill_candidates_of_the_scene_are_two_four_and_five():
    ledger = Ledger(TUNING.chat)
    ledger.update(acting_state(NIGHT_TARGETS, **NIGHT))

    assert decide.candidates(ledger, infer(ledger, TUNING.inference), TUNING) == NIGHT_TARGETS


def test_1_with_model_choices_off_no_request_is_made_and_the_script_s_first_candidate_is_played():
    source = Answering(choice="P4")

    played = decided(acting_state(NIGHT_TARGETS, **NIGHT), source, dataclasses.replace(SETTINGS, model_choices=False))

    assert played == 2 and source.choice_requests == []


def test_2_a_stub_answering_p4_among_two_four_and_five_plays_seat_four():
    source = Answering(choice="P4")

    played = decided(acting_state(NIGHT_TARGETS, **NIGHT), source)

    (request,) = source.choice_requests
    assert played == 4 and request.option_ids() == ["P2", "P4", "P5"]


def test_3_a_stub_answering_p6_which_is_no_candidate_plays_the_script_s_choice():
    assert decided(acting_state(NIGHT_TARGETS, **NIGHT), Answering(choice="P6")) == 2


def test_4_a_stub_sleeping_past_the_deadline_plays_the_script_s_choice_within_the_deadline_and_a_margin():
    short = dataclasses.replace(SETTINGS, choice_deadline_ms=SHORT_DEADLINE_MS)
    started = time.perf_counter()

    played = decided(acting_state(NIGHT_TARGETS, **NIGHT), Guarded(choice="P4", seconds=SLEEP_SECONDS), short)

    assert played == 2 and time.perf_counter() - started < SHORT_DEADLINE_LIMIT_SECONDS


def test_5_a_first_candidate_that_is_no_longer_legal_gives_way_to_the_next_legal_candidate():
    assert decided(acting_state([4, 5], **NIGHT), Answering(), dataclasses.replace(SETTINGS, model_choices=False)) == 4


def test_5_a_model_choice_that_is_not_legal_gives_way_to_the_script_s_legal_candidate():
    assert decided(acting_state([2, 5], **NIGHT), Answering(choice="P4")) == 2


@pytest.mark.parametrize(
    "legal, played", [([3, 7], 3), ([7, 5], 5), ([7], 7)], ids=["first", "abstain listed first", "only abstain"]
)
def test_5_with_no_legal_candidate_the_first_legal_action_that_is_not_abstaining_is_played(legal, played):
    game_state = acting_state(legal, seat=0, role=VILLAGER, sub_phase=DAY_VOTE)

    assert int(decide.legal_action(game_state, [1, 2]).action_id) == played


def test_6_with_a_single_candidate_no_request_is_made():
    source = Answering(choice="P4")
    found_wolf = acting_state([1, 2, 3, 4, 5, 6, 7], seat=0, role=SEER, results=[(5, WOLF)], sub_phase=DAY_VOTE)

    assert decided(found_wolf, source) == 5 and source.choice_requests == []


def test_the_question_names_the_role_and_the_options_describe_each_seat_with_the_script_s_pick_first():
    source = Answering(choice="P4")
    messages = [chat(0, 4, "I am the seer. Player2 is a wolf."), chat(1, 5, "Player4 is suspicious")]
    scene = {**NIGHT, "phase": MESSAGING, "sub_phase": DAY_VOTE}

    decided(acting_state([2, 3, 4, 5, 7], **{**scene, "messages": messages}), source)

    (request,) = source.choice_requests
    assert "You are a wolf." in request.question and request.question.endswith("whatever you do.")
    assert request.options[0].description.endswith("(script's pick)")
    four = next(option.description for option in request.options if option.id == "P4")
    assert "accused by 1 today" in four and "claims to be the seer; claims Player2 is wolf" in four
    assert 'Player4 said: "I am the seer. Player2 is a wolf."' in request.context.splitlines()
    assert request.deadline_ms == SETTINGS.choice_deadline_ms


def test_the_seer_s_night_asks_which_player_to_investigate_among_those_not_looked_at():
    source = Answering(choice="P6")
    seer_night = acting_state([1, 3, 4, 5, 6], seat=0, role=SEER, results=[(2, VILLAGER)], sub_phase=NIGHT_SEER)

    played = decided(seer_night, source)

    (request,) = source.choice_requests
    assert played == 6 and request.question.endswith("Which player do you investigate tonight?")
    assert "P2" not in request.option_ids()


def test_no_more_than_six_options_are_offered():
    source = Answering(choice="P1")
    day_one = acting_state([0, 1, 2, 4, 5, 6, 7], seat=3, role=VILLAGER, sub_phase=DAY_VOTE)

    decided(day_one, source)

    assert len(source.choice_requests[0].options) == decide.MAX_OPTIONS


# -- (7) the werewolf_decision event -----------------------------------------------------------------


@pytest.fixture
def decision_events(run_folder, read_jsonl):
    """Opens a match record; the returned function closes it and gives its `werewolf_decision` events' data."""
    records.start_match(DecisionContext(SESSION_ID, None, "werewolf", "agent-0", seat_position=0), None)

    def close_and_read() -> list[dict]:
        records.finish_match({"is_terminal": True})
        (events_path,) = run_folder.glob(f"runs/*/{SESSION_ID}__*/events.jsonl")
        return [event["data"] for event in read_jsonl(events_path) if event["kind"] == "werewolf_decision"]

    return close_and_read


def test_7_each_decision_writes_one_werewolf_decision_event_with_the_fields_listed(decision_events):
    decided(acting_state(NIGHT_TARGETS, **NIGHT), Answering(choice="P4"))
    decided(acting_state(NIGHT_TARGETS, **NIGHT), Answering(choice="P6"))

    first, second = decision_events()

    assert {key: value for key, value in first.items() if key != "latency_ms"} == {
        "sub_phase": NIGHT_WOLF,
        "candidates": NIGHT_TARGETS,
        "script_choice": 2,
        "chosen": 4,
        "source": "stub:answering",
        "outcome": "used",
    }
    assert (second["chosen"], second["source"], second["outcome"]) == (2, "script", "not_an_option")
    assert isinstance(first["latency_ms"], int) and first["latency_ms"] >= 0


# -- (8) the script's own time ----------------------------------------------------------------------


def test_8_the_script_part_of_a_decision_takes_under_fifty_milliseconds_on_a_day_five_ledger():
    day_five = acting_state(
        [0, 1, 4, 7],
        seat=2,
        role=SEER,
        results=[(0, VILLAGER), (1, VILLAGER), (5, VILLAGER), (6, VILLAGER)],
        day=5,
        sub_phase=DAY_VOTE,
        phase=MESSAGING,
        dead=[(1, 6, VILLAGER, VOTE), (2, 5, VILLAGER, NIGHT_KILL), (3, 3, WOLF, VOTE)],
        votes=[
            (1, {0: 6, 1: 6, 2: 6, 3: 6, 4: 6, 5: None, 6: 0}, 6),
            (2, {0: 3, 1: 3, 2: 3, 3: 1, 4: 2}, 3),
            (4, {0: 4, 1: 4, 2: 4, 4: None}, None),
        ],
        messages=[chat(seq, seq % 5, f"Player{(seq + 1) % 5} is suspicious, vote them") for seq in range(20)],
    )
    off = dataclasses.replace(SETTINGS, model_choices=False)

    def script_part() -> float:
        started = time.perf_counter()
        decided(day_five, Answering(), off)
        return time.perf_counter() - started

    assert statistics.mean(script_part() for _ in range(SCRIPT_RUNS)) < SCRIPT_LIMIT_SECONDS


@pytest.fixture
def config_root(tmp_path: Path) -> Path:
    """A folder standing in for the repository root, with a copy of the tracked configuration."""
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    return tmp_path


def rewrite(root: Path, old: str, new: str) -> None:
    """Replace one piece of text in the agent configuration under `root`; the piece has to be there."""
    path = root / AGENT_TOML
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# -- the settings and the chains -------------------------------------------------------------------


def test_the_tracked_settings_let_the_model_choose_and_write_within_the_requirement_s_deadlines():
    found = WerewolfSettings.from_files(REPO_ROOT)

    assert (found.model_choices, found.model_messages) == (True, True)
    assert (found.choice_deadline_ms, found.message_deadline_ms) == (15000, 20000)
    assert found.tuning == Tuning.from_file(REPO_ROOT)


@pytest.mark.parametrize(
    "old, new, said",
    [
        ("model_choices = true", 'model_choices = "on"', "model_choices in [werewolf] must be true or false"),
        ("model_messages = true\n", "", "model_messages in [werewolf]"),
        ("choice_deadline_ms = 15000", "choice_deadline_ms = 0", "choice_deadline_ms in [werewolf]"),
        ("message_deadline_ms = 20000", "message_deadline_ms = 120000", "message_deadline_ms in [werewolf]"),
        ("model_choices = true", "model_choices = true\nquiet = true", "[werewolf] has no setting named quiet"),
        ("[werewolf]", "[werewolf_old]", "must have a [werewolf] table"),
    ],
    ids=["switch as text", "missing switch", "no time", "past the platform's timeout", "unknown key", "no table"],
)
def test_a_werewolf_table_off_its_schema_is_a_config_error_naming_the_file_and_key(config_root, old, new, said):
    rewrite(config_root, old, new)

    with pytest.raises(ConfigError) as raised:
        WerewolfSettings.from_files(config_root)

    assert said in str(raised.value) and "agent.toml" in str(raised.value)


def test_9_the_choice_chain_builds_from_the_tracked_file_and_asks_the_strong_role():
    asked: list[httpx.Request] = []

    def model_server(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"choice": "P4"})}}]})

    chain = build_chain(CHOICE_CHAIN, transport=httpx.MockTransport(model_server))
    request = ChoiceRequest("Vote?", [Option("P2", "Player2"), Option("P4", "Player4")], "Day 1.", 1500, "w1")
    result = chain.choose(request)

    assert chain.name == CHOICE_CHAIN and (result.option_id, result.outcome) == ("P4", USED)
    assert [str(call.url) for call in asked] == [MODEL_SERVER_URL]
    assert json.loads(asked[0].content)["model"] == "strong"


def test_the_message_chain_builds_from_the_tracked_file_and_asks_the_strong_role_for_a_text():
    def model_server(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["model"] == "strong"
        return httpx.Response(200, json={"choices": [{"message": {"content": "I suspect Player4."}}]})

    chain = build_chain(MESSAGE_CHAIN, transport=httpx.MockTransport(model_server))

    assert chain.write(TextRequest("Say whom you suspect.", "Day 1.", 45, 1500, "w1")).text == "I suspect Player4."
