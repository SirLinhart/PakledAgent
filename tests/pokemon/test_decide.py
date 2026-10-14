"""The model's choice among the script's best candidates, the per-phase deadlines and the `pokemon_decision` event.

The numbered tests are the acceptance items of the model picks and deadlines;
the `test_clock_` ones are those of the deadlines the observation's clock
bounds, and the `test_decides_` ones those of the unscored, shuffled shortlist
a model decides from. No test reaches a model server: the sources are the stubs of
`pokemon_stubs`, or the tracked chain over a stand-in for the network.
"""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from pokemon_states import PASS, US, card, envelope
from pokemon_stubs import Answering, Guarded

from altruagent import DecisionContext, GameState
from ucla_agent.core import records
from ucla_agent.core.config import ConfigError
from ucla_agent.core.decide import USED, ChoiceRequest, LocalModelSource, Option, build_chain
from ucla_agent.core.decide import local as local_module
from ucla_agent.games.pokemon.agent import PokemonAgent
from ucla_agent.games.pokemon.decide import (
    DECIDE_ANSWER_TOKENS,
    DECIDE_QUESTIONS,
    DRAFT,
    PREVIEW,
    QUESTIONS,
    RANKED_BY_MAXIMIN,
    RANKED_BY_SCRIPT,
    TURN,
    Asked,
    Candidate,
    choose,
    compute_until,
    deadline_ms,
    option_ids,
    shown_ranks,
)
from ucla_agent.games.pokemon.draft import DraftMemory, pick_action, score_cards
from ucla_agent.games.pokemon.observe import Clock, Reader
from ucla_agent.games.pokemon.settings import CHOICE_CHAIN, PokemonSettings
from ucla_agent.practice.pokemon.battle_log import BattleLog
from ucla_agent.practice.pokemon.cards import CATALOG_PATH, load_catalog
from ucla_agent.practice.pokemon.doubles import DoublesView
from ucla_agent.practice.pokemon.preview import lineup_legal_action, preview_observation

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACKED = PokemonSettings.from_files(REPO_ROOT)
# The tracked settings with `model_decides` off, so the tests of the ranked choice among `top_k` hold whatever value
# ships; the tests of the deciding choice switch it on themselves.
SETTINGS = dataclasses.replace(TRACKED, model_decides=False)
SESSION_ID = "pokemon-decide-test"
MODEL_SERVER_URL = "http://127.0.0.1:11500/v1/chat/completions"
SHORT_DEADLINE_MS = 300
SLEEP_SECONDS = 1.0
SHORT_DEADLINE_LIMIT_SECONDS = 0.5
NEVER_SECONDS = 30.0
DRAFT_LIMIT_SECONDS = 6.5
# Showdown's VGC timer: the platform plays a random battle choice at 50 s, and the bank holds 420 s for the battle.
DECISION_SECONDS = 50
BANK_SECONDS = 420
# A decision early in a battle: neither its clock nor the bank cuts the configured deadline.
ROOMY_CLOCK = Clock(DECISION_SECONDS, BANK_SECONDS)
BATTLE_DECISIONS = 40
TURN_ACTIONS = [
    {"type": "doubles_turn", "slot_0": {"type": "move", "move_id": move, "target": 1}, "slot_1": {"type": "pass"}}
    for move in ("flareblitz", "knockoff", "fakeout", "uturn", "protect")
]


def turn_asked(clock: Clock | None = ROOMY_CLOCK) -> Asked:
    """Five turn candidates, best first, as the turn policy ranks them, asked with `clock`."""
    candidates = [Candidate(action, 100.0 - 10 * place, f"option {place}") for place, action in enumerate(TURN_ACTIONS)]
    return Asked(TURN, candidates, "Turn 3. Ours: Incineroar 80%.", "t3-v5", clock)


def preview_asked(clock: Clock | None) -> Asked:
    """Two lineup candidates, best first, asked with `clock`."""
    candidates = [Candidate({"lineup": place}, 50.0 - place, f"lineup {place}") for place in range(2)]
    return Asked(PREVIEW, candidates, "Team preview.", "preview-v2", clock)


def decided(asked: Asked, source, settings: PokemonSettings = SETTINGS) -> Candidate:
    return choose(asked, settings, source, time.perf_counter(), Reader())


# -- (1) to (3) the choice -------------------------------------------------------------------------


def test_1_with_model_choices_off_no_request_is_made_and_the_script_s_choice_is_played():
    source = Answering(choice="T2")

    chosen = decided(turn_asked(), source, dataclasses.replace(SETTINGS, model_choices=False))

    assert chosen.action == TURN_ACTIONS[0] and source.choice_requests == []


def test_2_a_stub_answering_t2_plays_the_second_candidate_among_the_top_four():
    source = Answering(choice="T2")

    chosen = decided(turn_asked(), source)

    (request,) = source.choice_requests
    assert chosen.action == TURN_ACTIONS[1]
    assert request.option_ids() == ["T1", "T2", "T3", "T4"]
    assert request.deadline_ms == SETTINGS.turn_deadline_ms and request.context.startswith("Turn 3.")
    assert [option.description for option in request.options] == ["option 0", "option 1", "option 2", "option 3"]


@pytest.mark.parametrize("answer", ["T9", "T5", "C2", None])
def test_3_an_answer_that_names_no_option_plays_the_script_s_choice(answer):
    assert decided(turn_asked(), Answering(choice=answer)).action == TURN_ACTIONS[0]


def test_a_single_candidate_is_played_without_asking():
    source = Answering(choice="T1")
    alone = dataclasses.replace(turn_asked(), candidates=turn_asked().candidates[:1])

    assert decided(alone, source).action == TURN_ACTIONS[0] and source.choice_requests == []


def test_each_phase_has_its_option_letter_and_deadline():
    assert option_ids(DRAFT, 2) == ["C1", "C2"] and option_ids(PREVIEW, 1) == ["L1"] and option_ids(TURN, 0) == []
    assert [deadline_ms(SETTINGS, phase, ROOMY_CLOCK) for phase in (DRAFT, PREVIEW, TURN)] == [6000, 20000, 20000]


# -- (4) and (5) deadlines -------------------------------------------------------------------------


def test_4_a_stub_sleeping_past_a_300_ms_deadline_plays_the_script_s_choice_within_500_ms():
    short = dataclasses.replace(SETTINGS, turn_deadline_ms=SHORT_DEADLINE_MS)
    started = time.perf_counter()

    chosen = decided(turn_asked(), Guarded("T2", SLEEP_SECONDS), short)

    assert chosen.action == TURN_ACTIONS[0] and time.perf_counter() - started < SHORT_DEADLINE_LIMIT_SECONDS


def test_5_a_draft_pick_with_the_6000_ms_deadline_and_a_stub_that_never_answers_is_made_within_6_5_s():
    pool = [card(name, ["Body Slam"]) for name in ("Snorlax", "Tauros", "Kangaskhan", "Pikachu", "Raichu", "Eevee")]
    started = time.perf_counter()
    memory = DraftMemory()
    memory.update({"available_cards": pool, "rosters": {"us": [], "them": []}, "current_seat": "us"}, "us", Reader())
    scores = score_cards(memory, memory.available, SETTINGS.tuning.draft)
    asked = Asked(
        DRAFT, [Candidate(pick_action(score), score.total, score.description()) for score in scores], "", "d1"
    )

    chosen = choose(asked, SETTINGS, Guarded("C2", NEVER_SECONDS), started, Reader())

    assert chosen.action == pick_action(scores[0])
    assert time.perf_counter() - started < DRAFT_LIMIT_SECONDS < 15


# -- (6) the event ---------------------------------------------------------------------------------


@pytest.fixture
def decision_events(run_folder, read_jsonl):
    """Opens a match record; the returned function closes it and gives its `pokemon_decision` events' data."""
    records.start_match(DecisionContext(SESSION_ID, None, "pokemon_vgc_doubles_draft", "us", seat_position=0), None)

    def close_and_read() -> list[dict]:
        records.finish_match({"is_terminal": True})
        (events_path,) = run_folder.glob(f"runs/*/{SESSION_ID}__*/events.jsonl")
        return [event["data"] for event in read_jsonl(events_path) if event["kind"] == "pokemon_decision"]

    return close_and_read


def test_6_each_decision_writes_one_pokemon_decision_event_with_the_listed_fields(decision_events):
    reader = Reader()
    reader.value({}, "weather", dict, {})
    decided(turn_asked(), Answering(choice="T2"))
    choose(turn_asked(), SETTINGS, Answering(choice="T7"), time.perf_counter(), reader)

    first, second = decision_events()

    assert {key: value for key, value in first.items() if key != "latency_ms"} == {
        "phase": "moving",
        "candidates": [
            {"id": f"T{place + 1}", "score": 100.0 - 10 * place, "action": TURN_ACTIONS[place]} for place in range(4)
        ],
        "script_choice": "T1",
        "chosen": "T2",
        "source": "stub:answering",
        "outcome": "used",
        "parse_degradations": 0,
        "degraded_keys": [],
        "clock_decision_seconds_left": DECISION_SECONDS,
        "clock_bank_seconds_left": BANK_SECONDS,
        "deadline_ms": 20000,
    }
    assert (second["chosen"], second["source"], second["outcome"]) == ("T1", "script", "not_an_option")
    assert (second["parse_degradations"], second["degraded_keys"]) == (1, ["weather"])
    assert isinstance(first["latency_ms"], int) and first["latency_ms"] >= 0


# -- (7) the chain ---------------------------------------------------------------------------------


def test_7_the_choice_chain_builds_from_the_tracked_config_and_asks_the_strong_role():
    asked: list[httpx.Request] = []

    def model_server(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"choice": "T2"})}}]})

    chain = build_chain(CHOICE_CHAIN, transport=httpx.MockTransport(model_server))
    request = ChoiceRequest("What now?", [Option("T1", "Protect"), Option("T2", "Fake Out")], "Turn 1.", 1500, "t1")
    result = chain.choose(request)

    assert chain.name == CHOICE_CHAIN and (result.option_id, result.outcome) == ("T2", USED)
    assert [str(call.url) for call in asked] == [MODEL_SERVER_URL]
    assert json.loads(asked[0].content)["model"] == "strong"


# -- the clock-bound deadline (1) to (7) and (9) ---------------------------------------------------


def asked_deadline(asked: Asked) -> int | None:
    """The deadline the model was asked with for `asked`, or None when no model was asked."""
    source = Answering(choice="T2")
    decided(asked, source)
    return source.choice_requests[0].deadline_ms if source.choice_requests else None


def test_clock_1_a_battle_decision_with_41_s_left_and_a_312_s_bank_gets_20000_ms():
    assert deadline_ms(SETTINGS, TURN, Clock(41, 312)) == min(20000, 31000, 31200) == 20000
    assert asked_deadline(turn_asked(Clock(41, 312))) == 20000


@pytest.mark.parametrize("bank, expected", [(200, 20000), (150, 15000), (120, 12000), (95, 5000), (90.5, 500)])
def test_clock_2_a_tenth_of_the_bank_and_the_reserve_cut_the_deadline(bank, expected):
    assert deadline_ms(SETTINGS, TURN, Clock(41, bank)) == expected
    assert asked_deadline(turn_asked(Clock(41, bank))) == expected


@pytest.mark.parametrize("bank", [85, 90, 0])
def test_clock_3_a_bank_at_or_under_90_s_asks_no_model_and_plays_the_script_s_answer(bank):
    source = Answering(choice="T2")

    chosen = decided(turn_asked(Clock(41, bank)), source)

    assert deadline_ms(SETTINGS, TURN, Clock(41, bank)) == 0
    assert chosen.action == TURN_ACTIONS[0] and source.choice_requests == []


@pytest.mark.parametrize("decision_left, expected", [(14, 4000), (10.5, 500), (10, None), (9, None), (-3, None)])
def test_clock_4_the_deadline_stops_10_s_short_of_the_decision_s_clock(decision_left, expected):
    assert deadline_ms(SETTINGS, TURN, Clock(decision_left, 312)) == (expected or 0)
    assert asked_deadline(turn_asked(Clock(decision_left, 312))) == expected


@pytest.mark.parametrize("bank", [420, 85, 5, 0])
def test_clock_5_team_preview_with_80_s_left_gets_20000_ms_whatever_the_bank(bank):
    assert deadline_ms(SETTINGS, PREVIEW, Clock(80, bank)) == 20000
    assert asked_deadline(preview_asked(Clock(80, bank))) == 20000


def test_clock_5_team_preview_stops_10_s_short_of_its_clock():
    assert deadline_ms(SETTINGS, PREVIEW, Clock(25, 0)) == 15000
    assert asked_deadline(preview_asked(Clock(8, 420))) is None


def test_clock_6_without_a_clock_a_lineup_or_turn_gets_5000_ms_and_a_draft_pick_keeps_its_deadline():
    assert [deadline_ms(SETTINGS, phase, None) for phase in (DRAFT, PREVIEW, TURN)] == [6000, 5000, 5000]
    assert deadline_ms(SETTINGS, DRAFT, Clock(3, 0)) == SETTINGS.draft_deadline_ms
    assert asked_deadline(turn_asked(None)) == 5000 and asked_deadline(preview_asked(None)) == 5000


def test_clock_6_the_no_clock_deadline_never_lengthens_a_shorter_configured_one():
    short = dataclasses.replace(SETTINGS, turn_deadline_ms=SHORT_DEADLINE_MS)

    assert deadline_ms(short, TURN, None) == SHORT_DEADLINE_MS


def test_clock_7_a_40_decision_battle_whose_model_takes_its_whole_deadline_never_takes_the_bank_below_90_s():
    source = Answering(choice="T2")
    bank = BANK_SECONDS
    banks = []

    for _ in range(BATTLE_DECISIONS):
        asked_before = len(source.choice_requests)
        decided(turn_asked(Clock(min(DECISION_SECONDS, bank), bank)), source)
        for request in source.choice_requests[asked_before:]:
            bank -= request.deadline_ms / 1000
        banks.append(bank)

    assert min(banks) >= SETTINGS.bank_reserve_seconds == 90
    assert banks[-1] == pytest.approx(SETTINGS.bank_reserve_seconds, abs=0.01)
    assert 0 < len(source.choice_requests) < BATTLE_DECISIONS


def test_clock_9_the_pokemon_decision_event_records_the_clock_and_the_deadline(decision_events):
    decided(turn_asked(Clock(41, 312)), Answering(choice="T2"))
    decided(turn_asked(Clock(41, 85)), Answering(choice="T2"))
    decided(turn_asked(None), Answering(choice="T2"))

    with_clock, out_of_bank, without_clock = decision_events()

    fields = ("clock_decision_seconds_left", "clock_bank_seconds_left", "deadline_ms", "outcome")
    assert [with_clock[key] for key in fields] == [41, 312, 20000, "used"]
    assert [out_of_bank[key] for key in fields] == [41, 85, 0, "not_asked"]
    assert [without_clock[key] for key in fields] == [None, None, 5000, "used"]


# -- model decides (1) to (6): the shortlist, the shuffle, the question and the record ----------------

DECIDES = dataclasses.replace(SETTINGS, model_choices=True, model_decides=True)
DECIDES_OFF = dataclasses.replace(SETTINGS, model_choices=True, model_decides=False)
MANY_ACTIONS = [
    {"type": "doubles_turn", "slot_0": {"type": "move", "move_id": f"move{place}", "target": 1}, "slot_1": PASS}
    for place in range(8)
]
# The script's ranks of the six candidates shown, in the order the shuffle seeded with each state tag gives them.
SHOWN_RANKS = {"t3-v5": [2, 5, 4, 3, 6, 1], "t4-v6": [6, 1, 5, 4, 3, 2]}
TODAY_EVENT_KEYS = {
    "phase",
    "candidates",
    "script_choice",
    "chosen",
    "source",
    "outcome",
    "latency_ms",
    "parse_degradations",
    "degraded_keys",
    "clock_decision_seconds_left",
    "clock_bank_seconds_left",
    "deadline_ms",
}
MODEL_SERVER_ENDPOINT = "http://127.0.0.1:11500"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "pokemon_practice"
SHOWDOWN = json.loads((FIXTURES / "showdown_p1_view.json").read_text(encoding="utf-8"))
# Read before any test points the project root at a folder of its own.
CATALOG = load_catalog(REPO_ROOT / CATALOG_PATH)
AGENT_CONTEXT = DecisionContext(SESSION_ID, None, "pokemon_vgc_doubles_draft", US, seat_position=0)
OPTION_LINE = re.compile(r"^- ([CLT]\d): ", re.MULTILINE)
REASON_AND_T3 = '{"reason": "Hits both foes hardest.", "choice": "T3"}'


def many_asked(tag: str = "t3-v5", clock: Clock | None = ROOMY_CLOCK) -> Asked:
    """Eight turn candidates, best first, each described by its script rank, asked in the state `tag`."""
    candidates = [
        Candidate(action, 100.0 - 10 * place, f"option of rank {place + 1}")
        for place, action in enumerate(MANY_ACTIONS)
    ]
    return Asked(TURN, candidates, "Turn 3. Ours: Incineroar 80%.", tag, clock)


class ReasonFirstModelServer:
    """Stands in for the model server's endpoint: answers every choice with `written` and keeps each request body."""

    def __init__(self, written: str) -> None:
        self.bodies: list[dict[str, Any]] = []
        self._written = written

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        message = {"role": "assistant", "content": self._written}
        return httpx.Response(200, json={"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]})

    def source(self) -> LocalModelSource:
        """The `strong` role over this stand-in, asking for reasons as the tracked `[decide]` table does."""
        transport = httpx.MockTransport(self._respond)
        return LocalModelSource("strong", base_url=MODEL_SERVER_ENDPOINT, choice_reasons=True, transport=transport)


def showdown_preview_state() -> GameState:
    """The team preview the practice harness builds from the real Showdown request."""
    request = SHOWDOWN["team_preview_request"]
    observation = preview_observation(request, [CATALOG.card(card_id) for card_id in SHOWDOWN["p2_cards"]], CATALOG)
    return envelope("team_preview", observation, [lineup_legal_action(request, SESSION_ID, 0)])


def showdown_turn_state() -> GameState:
    """The doubles turn the practice harness builds from the real Showdown move request."""
    log = BattleLog("p1")
    log.read(SHOWDOWN["move_log"])
    view = DoublesView(SHOWDOWN["move_request"], log, CATALOG, "p1", CATALOG.battle_format)
    return envelope("moving", view.observation(), [view.legal_action(SESSION_ID, 0)])


def test_decides_1_the_prompt_a_stubbed_model_gets_holds_6_options_and_no_score(run_folder):
    server = ReasonFirstModelServer(REASON_AND_T3)
    agent = PokemonAgent(settings=DECIDES, choice_source=server.source())

    agent.choose_action(showdown_preview_state(), AGENT_CONTEXT)
    agent.choose_action(showdown_turn_state(), AGENT_CONTEXT)

    preview, turn = server.bodies
    for body, phase in ((preview, PREVIEW), (turn, TURN)):
        system, user = (message["content"] for message in body["messages"])
        assert OPTION_LINE.findall(user) == option_ids(phase, 6)
        assert f"\n{DECIDE_QUESTIONS[phase]}\nOptions:\n" in user
        assert "score" not in user.lower() and "score" not in system.lower()
    assert "The options are unranked and listed in random order." in DECIDE_QUESTIONS[TURN]


def test_decides_shuffle_2_one_state_tag_repeats_its_order_another_differs_and_the_ids_follow_the_shown_order():
    source = Answering(choice=None)

    for tag in ("t3-v5", "t3-v5", "t4-v6"):
        decided(many_asked(tag), source, DECIDES)

    first, again, other = ([option.description for option in request.options] for request in source.choice_requests)
    assert first == again != other
    assert first == [f"option of rank {rank}" for rank in SHOWN_RANKS["t3-v5"]]
    assert other == [f"option of rank {rank}" for rank in SHOWN_RANKS["t4-v6"]]
    assert shown_ranks(many_asked("t3-v5"), DECIDES) == SHOWN_RANKS["t3-v5"]
    assert [request.option_ids() for request in source.choice_requests] == [option_ids(TURN, 6)] * 3


def test_decides_shuffle_fewer_candidates_than_decide_top_k_are_all_shown():
    three = dataclasses.replace(many_asked(), candidates=many_asked().candidates[:3])

    assert sorted(shown_ranks(three, DECIDES)) == [1, 2, 3]
    assert shown_ranks(three, DECIDES_OFF) == [1, 2, 3]


def test_decides_3_the_schema_lists_the_reason_before_the_choice_both_required_and_max_tokens_is_160(run_folder):
    server = ReasonFirstModelServer(REASON_AND_T3)

    decided(many_asked(), server.source(), DECIDES)

    (body,) = server.bodies
    schema = body["response_format"]["json_schema"]["schema"]
    assert list(schema["properties"]) == ["reason", "choice"] and schema["required"] == ["reason", "choice"]
    assert schema["properties"]["reason"] == {"type": "string", "maxLength": 8 * DECIDES.decide_reason_words}
    assert schema["properties"]["choice"] == {"enum": option_ids(TURN, 6)}
    assert body["max_tokens"] == DECIDE_ANSWER_TOKENS == 160
    assert "in at most 40 words" in body["messages"][0]["content"]


@pytest.mark.parametrize("written", [REASON_AND_T3, '{"choice": "T3"}'], ids=["4 with a reason", "5 without one"])
def test_decides_4_and_5_an_answer_t3_plays_the_candidate_shown_third_with_or_without_a_reason(
    decision_events, written
):
    third_rank = SHOWN_RANKS["t3-v5"][2]

    chosen = decided(many_asked(), ReasonFirstModelServer(written).source(), DECIDES)

    (event,) = decision_events()
    shown_actions = [candidate["action"] for candidate in event["candidates"]]
    assert chosen.action == MANY_ACTIONS[third_rank - 1]
    assert (event["chosen"], event["outcome"], event["source"]) == ("T3", "used", "local:strong")
    assert (event["shown_order"], event["script_rank_of_choice"]) == (SHOWN_RANKS["t3-v5"], third_rank)
    assert [candidate["id"] for candidate in event["candidates"]] == option_ids(TURN, 6)
    assert shown_actions == [MANY_ACTIONS[rank - 1] for rank in SHOWN_RANKS["t3-v5"]]
    assert event["script_choice"] == "T6"


def test_decides_a_late_answer_plays_the_script_s_best_wherever_it_is_shown(decision_events):
    short = dataclasses.replace(DECIDES, turn_deadline_ms=SHORT_DEADLINE_MS)

    chosen = decided(many_asked(), Guarded("T3", SLEEP_SECONDS), short)

    (event,) = decision_events()
    assert chosen.action == MANY_ACTIONS[0]
    assert (event["chosen"], event["script_choice"], event["script_rank_of_choice"]) == ("T6", "T6", 1)
    assert (event["source"], event["outcome"]) == ("script", "timeout")


@pytest.mark.parametrize("clock", [ROOMY_CLOCK, Clock(41, 150), Clock(41, 85), None])
def test_decides_the_deadline_is_the_one_the_switch_off_gives(clock):
    on, off = (Answering(choice="T1") for _ in range(2))

    decided(many_asked(clock=clock), on, DECIDES)
    decided(many_asked(clock=clock), off, DECIDES_OFF)

    assert [request.deadline_ms for request in on.choice_requests] == [
        request.deadline_ms for request in off.choice_requests
    ]
    assert deadline_ms(DECIDES, TURN, clock) == deadline_ms(DECIDES_OFF, TURN, clock)


def test_decides_6_with_the_switch_off_the_request_the_body_and_the_record_are_today_s(decision_events):
    source, server = Answering(choice="T2"), ReasonFirstModelServer('{"choice": "T2"}')

    decided(many_asked(), source, DECIDES_OFF)
    decided(many_asked(), server.source(), DECIDES_OFF)

    (request,) = source.choice_requests
    options = [Option(option_id, f"option of rank {rank}") for rank, option_id in enumerate(option_ids(TURN, 4), 1)]
    today = ChoiceRequest(QUESTIONS[TURN], options, "Turn 3. Ours: Incineroar 80%.", SETTINGS.turn_deadline_ms, "t3-v5")
    assert request == today and (request.reason_first, request.max_answer_tokens) == (False, None)
    assert server.bodies == [local_module.build_body("strong", today, with_reason=True)]
    for event in decision_events():
        assert set(event) == TODAY_EVENT_KEYS
        assert (event["script_choice"], event["chosen"]) == ("T1", "T2")
        assert [candidate["action"] for candidate in event["candidates"]] == MANY_ACTIONS[:4]


# -- model decides (7): its keys -------------------------------------------------------------------

AGENT_TOML = Path("config") / "agent.toml"
DECIDE_TOP_K_SAID = "decide_top_k in [pokemon] must be a whole number of at least 2 and at most 9"
DECIDE_REASON_WORDS_SAID = "decide_reason_words in [pokemon] must be a whole number of at least 1 and at most 60"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A copy of the tracked configuration that a test may edit."""
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    return tmp_path


def _edit(root: Path, old: str, new: str) -> None:
    path = root / AGENT_TOML
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_decides_7_the_tracked_keys_ship_the_switch_on_a_shortlist_of_6_and_reasons_of_40_words():
    assert (TRACKED.model_decides, TRACKED.decide_top_k, TRACKED.decide_reason_words) == (True, 6, 40)


@pytest.mark.parametrize(
    "old, new, said",
    [
        ("model_decides = true\n", "", "model_decides in [pokemon] must be true or false"),
        ("model_decides = true", 'model_decides = "no"', "model_decides in [pokemon] must be true or false"),
        ("model_decides = true", "model_decides = 0", "model_decides in [pokemon] must be true or false"),
        ("decide_top_k = 6\n", "", DECIDE_TOP_K_SAID),
        ("decide_top_k = 6", "decide_top_k = 1", DECIDE_TOP_K_SAID),
        ("decide_top_k = 6", "decide_top_k = 10", DECIDE_TOP_K_SAID),
        ("decide_top_k = 6", "decide_top_k = 6.5", DECIDE_TOP_K_SAID),
        ("decide_top_k = 6", 'decide_top_k = "6"', DECIDE_TOP_K_SAID),
        ("decide_reason_words = 40\n", "", DECIDE_REASON_WORDS_SAID),
        ("decide_reason_words = 40", "decide_reason_words = 0", DECIDE_REASON_WORDS_SAID),
        ("decide_reason_words = 40", "decide_reason_words = 61", DECIDE_REASON_WORDS_SAID),
        ("decide_reason_words = 40", "decide_reason_words = true", DECIDE_REASON_WORDS_SAID),
        ("decide_reason_words = 40", "decide_reason_words = 40\ndecide_reasons = 40", "decide_reasons"),
    ],
    ids=[
        "switch missing",
        "switch a string",
        "switch a number",
        "top k missing",
        "top k of 1",
        "top k of 10",
        "top k not whole",
        "top k a string",
        "reason words missing",
        "reason words of 0",
        "reason words of 61",
        "reason words a boolean",
        "unknown key",
    ],
)
def test_decides_7_a_decide_key_missing_or_off_its_schema_is_a_config_error_naming_it(project, old, new, said):
    _edit(project, old, new)

    with pytest.raises(ConfigError) as raised:
        PokemonSettings.from_files(project)

    assert said in str(raised.value)


def test_decides_7_the_bounds_of_the_decide_keys_are_accepted_and_the_switch_off_is_read(project):
    _edit(project, "model_decides = true", "model_decides = false")
    _edit(project, "decide_top_k = 6", "decide_top_k = 2")
    _edit(project, "decide_reason_words = 40", "decide_reason_words = 60")
    low = PokemonSettings.from_files(project)
    _edit(project, "decide_top_k = 2", "decide_top_k = 9")
    _edit(project, "decide_reason_words = 60", "decide_reason_words = 1")
    high = PokemonSettings.from_files(project)

    assert (low.model_decides, low.decide_top_k, low.decide_reason_words) == (False, 2, 60)
    assert (high.decide_top_k, high.decide_reason_words) == (9, 1)


# -- maximin: the computing time and the ranking field ----------------------------------------------


@pytest.mark.parametrize(
    "phase, clock, seconds",
    [
        (TURN, Clock(41, 312), 31),
        (PREVIEW, Clock(80, 0), 70),
        (TURN, Clock(9, 312), -1),
        (TURN, None, 20),
        (PREVIEW, None, 20),
        (DRAFT, None, 6),
        (DRAFT, Clock(3, 0), 6),
    ],
    ids=[
        "turn clock",
        "preview clock",
        "turn clock spent",
        "turn no clock",
        "preview no clock",
        "draft",
        "draft clock",
    ],
)
def test_maximin_the_script_ranks_until_the_clock_less_the_margin_else_the_phase_s_configured_deadline(
    phase, clock, seconds
):
    started = 1000.0

    assert compute_until(SETTINGS, phase, clock, started) == pytest.approx(started + seconds)


def test_maximin_the_event_records_how_the_candidates_were_ranked_only_when_maximin_is_enabled(decision_events):
    decided(turn_asked(), Answering(choice="T1"))
    decided(dataclasses.replace(turn_asked(), ranking=RANKED_BY_MAXIMIN), Answering(choice="T1"))
    decided(dataclasses.replace(turn_asked(), ranking=RANKED_BY_SCRIPT), Answering(choice="T1"))

    plain, maximin, script = decision_events()

    assert "ranking" not in plain
    assert (maximin["ranking"], script["ranking"]) == ("maximin", "script")
    assert {key for key in maximin if key != "ranking"} == set(plain)
