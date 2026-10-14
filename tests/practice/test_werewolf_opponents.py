"""The Werewolf practice opponents and the table mixes, on whole games and on hand-built seat states.

The numbered sections are the acceptance items of the practice opponents:
(1) to (7) for the opponents and the mixes, (8) to (11) for `record_voter`'s
seer claims. The model-driven player is given a stub source: one that times
out, or the local model source over a stub transport.
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

import httpx
import pytest

from altruagent import TERMINATE_MESSAGING, DecisionContext, SendMessage
from altruagent.runner import run_game
from ucla_agent.core import paths
from ucla_agent.core.decide import TIMEOUT, ChainSource, DecisionSource, LocalModelSource
from ucla_agent.core.decide.journal import SourceCall, TextCall
from ucla_agent.practice.werewolf import PracticeSeatSession, WerewolfEngine, llm_player, play_practice_game
from ucla_agent.practice.werewolf.engine import (
    ABSTAIN,
    CHAT,
    DAY_VOTE,
    MAX_CHARACTERS,
    MAX_WORDS,
    MOVING,
    NIGHT_SEER,
    NIGHT_WOLF,
    SEER,
    VILLAGER,
    WOLF,
    Message,
)
from ucla_agent.practice.werewolf.llm_player import DEADLINE_MS, LlmPlayer
from ucla_agent.practice.werewolf.mixes import (
    LLM_PLAYER,
    MIXES,
    OPPONENT_FACTORIES,
    RECORD_VOTER,
    build_opponents,
    seat_opponents,
)
from ucla_agent.practice.werewolf.opponents import (
    ABSTAINER_LINE,
    AbstainerPlayer,
    PlaceholderPlayer,
    RecordVoter,
    SeatView,
)
from ucla_agent.practice.werewolf.view import TRANSCRIPT_FIELD, state_payload, visible_messages

SCRIPTED_GAMES = 50
DRIVEN = 0
SESSION_ID = "opponents-test"
MODEL_SERVER_URL = "http://127.0.0.1:11500"
SIXTY_WORDS = " ".join(["suspicious"] * 60)
MODEL_LINE = "Player5 voted out a villager on day one."
PRACTICE_SOURCE = Path(__file__).resolve().parents[2] / "src" / "ucla_agent" / "practice" / "werewolf"
OPPONENT_MODULES = ("opponents.py", "llm_player.py", "agent_opponent.py")
ENGINE_NAMES = {"WerewolfEngine", "PracticeSeatSession"}


class StarterPlaceholder:
    """The starter kit's placeholder agent for the driven seat."""

    def choose_action(self, state, context):
        return state.legal_actions[0]

    def choose_message(self, state, context):
        return TERMINATE_MESSAGING


class Checked:
    """Wraps an opponent and notes every answer that breaks a rule, and every answer of a wolf that targets its ally."""

    def __init__(self, kind: str, inner: Any, findings: dict[str, list]) -> None:
        self.kind = kind
        self._inner = inner
        self._findings = findings

    def choose_action(self, view):
        action = self._inner.choose_action(view)
        seat_view = SeatView(view)
        self._findings["actions"].append((self.kind, seat_view.sub_phase))
        if action not in seat_view.legal:
            self._findings["illegal"].append((self.kind, action, seat_view.legal))
        if seat_view.role == WOLF and action in seat_view.allies:
            self._findings["ally_targeted"].append((self.kind, action))
        return action

    def choose_message(self, view):
        decision = self._inner.choose_message(view)
        if decision is not TERMINATE_MESSAGING:
            self._findings["chats"].append((self.kind, decision.content))
            if _breaks_a_chat_rule(decision):
                self._findings["bad_chats"].append((self.kind, decision.content))
        return decision


def _breaks_a_chat_rule(decision: SendMessage) -> bool:
    """Whether a chat is empty, too long, or not the public broadcast every opponent chat is."""
    content = decision.content
    return (
        not content.strip()
        or len(content.split()) > MAX_WORDS
        or len(content) > MAX_CHARACTERS
        or bool(decision.recipients)
    )


class TimingOutSource(DecisionSource):
    """A model source that never answers in time; it remembers what it was asked."""

    name = "local:fast"

    def __init__(self) -> None:
        self.choice_requests = []
        self.text_requests = []

    def ask(self, request):
        self.choice_requests.append(request)
        return [SourceCall(self.name, None, None, request.deadline_ms, TIMEOUT, "deadline")]

    def ask_text(self, request):
        self.text_requests.append(request)
        return [TextCall(self.name, None, request.deadline_ms, TIMEOUT, "deadline")]


def _local_model(choice: str, text: str) -> tuple[LocalModelSource, list[dict]]:
    """The `local:fast` source over a stub transport answering every choice with `choice` and every text with `text`."""
    bodies: list[dict] = []

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        content = text if "response_format" not in body else json.dumps({"choice": choice})
        return httpx.Response(
            200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
        )

    return LocalModelSource("fast", base_url=MODEL_SERVER_URL, transport=httpx.MockTransport(answer)), bodies


def _context() -> DecisionContext:
    return DecisionContext(SESSION_ID, None, "werewolf", f"seat{DRIVEN}", DRIVEN)


def _hand_built_view(
    *,
    seat: int,
    private_lines: list[str],
    dead: list[dict],
    vote_history: list[dict],
    phase: str = DAY_VOTE,
    legal: list[int] | None = None,
    transcript: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A seat state as the session builds it, written out by hand: only what the seat may know.

    With `transcript`, the state is an opponent's, carrying every message the
    seat has read under `messages`; `new_messages` stays empty, as in a vote.
    """
    dead_seats = {int(death["player"].removeprefix("seat")) for death in dead}
    alive = [seat_number for seat_number in range(7) if seat_number not in dead_seats]
    if legal is None:
        legal = [other for other in alive if other != seat] + ([ABSTAIN] if phase == DAY_VOTE else [])
    labels = {NIGHT_WOLF: "Kill", NIGHT_SEER: "Investigate", DAY_VOTE: "Vote to eliminate"}
    transcript_field = {} if transcript is None else {TRANSCRIPT_FIELD: transcript}
    return {
        **transcript_field,
        "your_position": seat,
        "state_version": 12,
        "phase": "moving",
        "players": [{"position": number, "name": f"seat{number}", "agent_id": f"seat{number}"} for number in range(7)],
        "observation": "\n".join(["=== Werewolf ===", *private_lines, f"Day 2 | phase {phase} | alive {len(alive)}/7"]),
        "game_state": {
            "day": 2,
            "phase": phase,
            "alive": [f"seat{number}" for number in alive],
            "dead": dead,
            "vote_history": vote_history,
            "winner": None,
        },
        "legal_actions": {
            "actions": [
                {
                    "action_id": str(action),
                    "label": "Abstain" if action == ABSTAIN else f"{labels[phase]} Player{action}",
                }
                for action in legal
            ]
        },
        "new_messages": [],
    }


def _abstain_except(votes: dict[int, int]) -> dict[str, str]:
    return {f"seat{voter}": (f"seat{votes[voter]}" if voter in votes else "abstain") for voter in range(7)}


def _chats(*chats: tuple[int, str] | tuple[int, str, int]) -> list[dict[str, Any]]:
    """Chats as the seat reads them, oldest first: `(sender, content)` is a broadcast, a third item a private chat."""
    return [
        {"seq": seq, "sender": chat[0], "recipients": list(chat[2:]), "type": "chat", "content": chat[1]}
        for seq, chat in enumerate(chats)
    ]


# Day 1: only seat 5 voted, for seat 3, who was eliminated and revealed a villager.
VILLAGER_VOTED_OUT = {
    "dead": [{"day": 1, "player": "seat3", "role": "villager", "cause": "vote"}],
    "vote_history": [{"day": 1, "votes": _abstain_except({5: 3}), "eliminated": "seat3"}],
}
VILLAGER_LINES = ["Your role: VILLAGER", "You do not know anyone else's role."]
WOLF_LINES = ["Your role: WOLF", "Your fellow wolves: Player0"]


def _villager_view() -> dict[str, Any]:
    return _hand_built_view(seat=1, private_lines=VILLAGER_LINES, **VILLAGER_VOTED_OUT)


def _play_checked_games(
    mix: str, games: int, factories: dict[str, Callable[[], Any]] = OPPONENT_FACTORIES
) -> tuple[list[WerewolfEngine], dict[str, list]]:
    findings: dict[str, list] = {"actions": [], "illegal": [], "ally_targeted": [], "chats": [], "bad_chats": []}
    engines = []
    for seed in range(games):
        engine = WerewolfEngine(seed)
        seating = seat_opponents(MIXES[mix], seed, DRIVEN)
        opponents = {seat: Checked(kind, factories[kind](), findings) for seat, kind in seating.items()}
        final = run_game(PracticeSeatSession(engine, DRIVEN, opponents, SESSION_ID), _context(), StarterPlaceholder())
        assert final.is_terminal and engine.winner in (WOLF, VILLAGER)
        engines.append(engine)
    return engines, findings


# -- (1) only legal actions over fifty scripted games --------------------------------------------


def test_every_scripted_opponent_answers_legally_over_fifty_seeded_games_that_all_finish():
    _engines, findings = _play_checked_games("scripted", SCRIPTED_GAMES)

    assert findings["illegal"] == [] and findings["bad_chats"] == []
    assert {kind for kind, _phase in findings["actions"]} == {"placeholder", "abstainer", "record_voter"}
    assert {phase for _kind, phase in findings["actions"]} == {NIGHT_WOLF, NIGHT_SEER, DAY_VOTE}
    assert {kind for kind, _content in findings["chats"]} == {"abstainer", "record_voter"}


def test_the_placeholder_takes_the_first_legal_action_and_never_chats():
    view = _villager_view()

    assert PlaceholderPlayer().choose_action(view) == 0
    assert PlaceholderPlayer().choose_message(view) is TERMINATE_MESSAGING


def test_the_abstainer_says_its_line_once_abstains_by_day_and_takes_the_first_target_by_night():
    day = _villager_view()
    night = _hand_built_view(
        seat=1,
        private_lines=["Your role: SEER", "You have not investigated anyone yet."],
        phase=NIGHT_SEER,
        dead=[],
        vote_history=[],
    )
    spoken = {**day, "new_messages": [{"seq": 4, "sender": 1, "recipients": [], "type": "chat", "content": "x"}]}

    assert AbstainerPlayer().choose_action(day) == ABSTAIN
    assert AbstainerPlayer().choose_action(night) == 0
    assert AbstainerPlayer().choose_message(day).content == ABSTAINER_LINE
    assert AbstainerPlayer().choose_message(spoken) is TERMINATE_MESSAGING


# -- (2) record_voter as a villager --------------------------------------------------------------


def test_record_voter_as_a_villager_votes_the_seat_that_voted_out_a_revealed_villager():
    view = _villager_view()

    assert RecordVoter().choose_action(view) == 5
    assert RecordVoter().choose_message(view).content == "I am voting Player5."


def test_record_voter_as_a_villager_without_evidence_votes_the_lowest_living_other_seat():
    view = _hand_built_view(seat=1, private_lines=VILLAGER_LINES, dead=[], vote_history=[])

    assert RecordVoter().choose_action(view) == 0


def test_record_voter_as_the_seer_votes_a_found_wolf_and_investigates_the_lowest_unlooked_seat():
    seer_lines = ["Your role: SEER", "Your investigations: Player0 is villager, Player6 is wolf"]
    day = _hand_built_view(seat=2, private_lines=seer_lines, **VILLAGER_VOTED_OUT)
    night = _hand_built_view(seat=2, private_lines=seer_lines, phase=NIGHT_SEER, **VILLAGER_VOTED_OUT)

    assert RecordVoter().choose_action(day) == 6
    assert RecordVoter().choose_action(night) == 1


# -- (3) record_voter as a wolf ------------------------------------------------------------------


def test_record_voter_as_a_wolf_votes_the_lowest_living_non_wolf_and_not_its_ally():
    view = _hand_built_view(seat=2, private_lines=WOLF_LINES, **VILLAGER_VOTED_OUT)

    assert RecordVoter().choose_action(view) == 1
    assert RecordVoter().choose_message(view).content == "I am voting Player1."


def test_record_voter_as_a_wolf_kills_the_non_wolf_with_most_votes_for_a_wolf():
    votes = [
        {"day": 1, "votes": _abstain_except({3: 0, 6: 2, 1: 6}), "eliminated": None},
        {"day": 2, "votes": _abstain_except({6: 0}), "eliminated": "seat0"},
    ]
    view = _hand_built_view(
        seat=2, private_lines=WOLF_LINES, phase=NIGHT_WOLF, dead=[], vote_history=votes, legal=[1, 3, 4, 5, 6]
    )

    assert RecordVoter().choose_action(view) == 6


def test_record_voter_as_a_wolf_never_targets_its_ally_over_fifty_scripted_games():
    _engines, findings = _play_checked_games("scripted", SCRIPTED_GAMES)

    assert [finding for finding in findings["ally_targeted"] if finding[0] == RECORD_VOTER] == []


# -- (4) llm_player falls back on a timeout ------------------------------------------------------


def test_llm_player_with_a_source_that_times_out_falls_back_to_record_voter_s_choice_and_line():
    source = TimingOutSource()
    player = LlmPlayer(source)
    view = _villager_view()

    assert player.choose_action(view) == RecordVoter().choose_action(view) == 5
    assert player.choose_message(view).content == RecordVoter().choose_message(view).content == "I am voting Player5."
    (choice,) = source.choice_requests
    (text,) = source.text_requests
    assert choice.deadline_ms == text.deadline_ms == DEADLINE_MS == 20_000
    assert choice.option_ids() == ["0", "2", "4", "5", "6", "7"]
    assert text.max_words == MAX_WORDS


def test_llm_player_uses_the_local_model_s_choice_and_text_when_they_come_back_in_form():
    source, bodies = _local_model("4", MODEL_LINE)
    player = LlmPlayer(source)
    view = _villager_view()

    assert player.choose_action(view) == 4
    assert player.choose_message(view).content == MODEL_LINE
    assert [body["model"] for body in bodies] == ["fast", "fast"]


def test_llm_player_asks_the_model_server_s_fast_role_by_default(monkeypatch):
    roles = []
    monkeypatch.setattr(llm_player, "LocalModelSource", lambda role: roles.append(role) or TimingOutSource())

    chain = llm_player.default_source()

    assert roles == ["fast"]
    assert isinstance(chain, ChainSource) and chain.name == llm_player.CHAIN_NAME


def test_llm_player_terminates_once_it_has_chatted_in_the_window():
    view = {
        **_villager_view(),
        "new_messages": [{"seq": 9, "sender": 1, "recipients": [], "type": "chat", "content": "x"}],
    }
    source = TimingOutSource()

    assert LlmPlayer(source).choose_message(view) is TERMINATE_MESSAGING
    assert source.text_requests == []


# -- (5) llm_player never sends an over-long chat ------------------------------------------------


def test_llm_player_given_a_60_word_text_sends_record_voter_s_line_instead():
    source, _bodies = _local_model("4", SIXTY_WORDS)

    message = LlmPlayer(source).choose_message(_villager_view())

    assert message.content == "I am voting Player5."
    assert len(message.content.split()) <= MAX_WORDS


def test_no_chat_of_a_mixed_table_with_a_60_word_model_exceeds_fifty_words():
    factories = {**OPPONENT_FACTORIES, LLM_PLAYER: lambda: LlmPlayer(_local_model("7", SIXTY_WORDS)[0])}

    engines, findings = _play_checked_games("mixed", 10, factories)

    assert findings["illegal"] == [] and findings["bad_chats"] == []
    assert any(kind == LLM_PLAYER for kind, _content in findings["chats"])
    assert all(len(message.content.split()) <= MAX_WORDS for engine in engines for message in engine.messages)


# -- (6) the seating ------------------------------------------------------------------------------


def test_one_seed_seats_the_mixed_table_the_same_way_every_time_and_another_seed_differently():
    first = seat_opponents(MIXES["mixed"], 7, DRIVEN)
    second = seat_opponents(MIXES["mixed"], 7, DRIVEN)
    other = seat_opponents(MIXES["mixed"], 8, DRIVEN)

    assert first == second and first != other
    assert set(first) == {1, 2, 3, 4, 5, 6}
    assert Counter(first.values()) == {"placeholder": 2, "record_voter": 2, "llm_player": 2}


def test_the_mixes_are_the_named_tables():
    assert Counter(MIXES["scripted"]) == {"placeholder": 2, "abstainer": 2, "record_voter": 2}
    assert Counter(MIXES["mixed"]) == {"placeholder": 2, "record_voter": 2, "llm_player": 2}
    assert Counter(MIXES["self"]) == {"werewolf_agent": 6}


def test_a_practice_game_writes_the_mix_and_each_opponent_s_seat_into_its_result(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.RUNS_DIR_ENV, str(tmp_path / "runs"))
    factories = {**OPPONENT_FACTORIES, LLM_PLAYER: lambda: LlmPlayer(TimingOutSource())}

    result = play_practice_game(7, StarterPlaceholder, "mixed", seat=DRIVEN, opponent_factories=factories)

    assert result.mix == "mixed"
    assert result.opponent_seats == seat_opponents(MIXES["mixed"], 7, DRIVEN)


def test_build_opponents_makes_one_opponent_of_the_seated_kind_per_seat():
    opponents = build_opponents({1: "placeholder", 2: "abstainer", 3: "record_voter"})

    assert [type(opponents[seat]) for seat in (1, 2, 3)] == [PlaceholderPlayer, AbstainerPlayer, RecordVoter]


# -- (7) opponents read the seat's view and nothing else ------------------------------------------


def _plain_views(seed: int) -> list[dict[str, Any]]:
    """Every seat's state at each opponent turn of a game, as plain read-only data: no object of the engine in it."""
    collected: list[dict[str, Any]] = []

    class Collecting(PlaceholderPlayer):
        def choose_action(self, view):
            collected.append(json.loads(json.dumps(view)))
            return super().choose_action(view)

    engine = WerewolfEngine(seed)
    opponents = {seat: Collecting() for seat in range(1, 7)}
    run_game(PracticeSeatSession(engine, DRIVEN, opponents, SESSION_ID), _context(), StarterPlaceholder())
    return collected


@pytest.mark.parametrize("kind", sorted(OPPONENT_FACTORIES))
def test_each_opponent_answers_from_a_plain_view_with_no_engine_in_reach(kind):
    factories = {**OPPONENT_FACTORIES, LLM_PLAYER: lambda: LlmPlayer(TimingOutSource())}
    views = [view for seed in range(5) for view in _plain_views(seed)]
    opponent = factories[kind]()

    for view in views:
        frozen = MappingProxyType(view)
        assert opponent.choose_action(frozen) in SeatView(frozen).legal
        decision = opponent.choose_message(frozen)
        assert decision is TERMINATE_MESSAGING or isinstance(decision, SendMessage)
    assert {SeatView(view).sub_phase for view in views} == {NIGHT_WOLF, NIGHT_SEER, DAY_VOTE}


WHISPER = "Between us: I trust Player3."
WHISPERER, WHISPERED_TO = 1, 2
TRANSCRIPT_SEED = 4


def _recorded_game(seed: int) -> tuple[WerewolfEngine, list[tuple[dict[str, Any], int]]]:
    """A game of placeholders where seat 1 whispers to seat 2 once a day: each view handed to an opponent, with the
    number of messages the game held at that moment."""
    engine = WerewolfEngine(seed)
    collected: list[tuple[dict[str, Any], int]] = []

    class Recording(PlaceholderPlayer):
        def __init__(self, whispers: bool) -> None:
            self._whispers = whispers

        def choose_action(self, view):
            collected.append((view, len(engine.messages)))
            return super().choose_action(view)

        def choose_message(self, view):
            collected.append((view, len(engine.messages)))
            if self._whispers and not SeatView(view).has_chatted:
                return SendMessage(WHISPER, [WHISPERED_TO])
            return TERMINATE_MESSAGING

    opponents = {seat: Recording(seat == WHISPERER) for seat in range(1, 7)}
    run_game(PracticeSeatSession(engine, DRIVEN, opponents, SESSION_ID), _context(), StarterPlaceholder())
    return engine, collected


def test_each_opponent_s_view_holds_its_seat_s_whole_transcript_also_during_the_vote():
    engine, collected = _recorded_game(TRANSCRIPT_SEED)
    vote_views = [
        view for view, _count in collected if view["phase"] == MOVING and SeatView(view).sub_phase == DAY_VOTE
    ]
    whisper_readers = {
        view["your_position"] for view, _count in collected if WHISPER in [m["content"] for m in view[TRANSCRIPT_FIELD]]
    }

    for view, message_count in collected:
        readable = visible_messages(engine, view["your_position"], SESSION_ID)
        assert view[TRANSCRIPT_FIELD] == [message for message in readable if message["seq"] < message_count]
    assert vote_views and all(view["new_messages"] == [] for view in vote_views)
    assert all(view[TRANSCRIPT_FIELD] for view in vote_views)
    assert whisper_readers == {WHISPERER, WHISPERED_TO}


def test_the_driven_seat_s_state_has_no_transcript_field():
    opponents = build_opponents(dict.fromkeys(range(1, 7), "abstainer"))
    session = PracticeSeatSession(WerewolfEngine(TRANSCRIPT_SEED), DRIVEN, opponents, SESSION_ID)

    assert TRANSCRIPT_FIELD not in session.get_state().raw


def test_a_seat_view_reads_the_same_from_the_session_s_state_and_from_its_json_copy():
    engine = WerewolfEngine(3, SEER, 2)
    engine.play(2, engine.wolves()[0])
    state = state_payload(engine, 2, SESSION_ID)

    live, copied = SeatView(state), SeatView(json.loads(json.dumps(state)))

    assert (live.role, live.investigations, live.living) == (copied.role, copied.investigations, copied.living)
    assert live.role == SEER and live.investigations == {engine.wolves()[0]: WOLF}


@pytest.mark.parametrize("file_name", OPPONENT_MODULES)
def test_the_opponent_modules_never_name_the_engine_or_the_session(file_name):
    tree = ast.parse((PRACTICE_SOURCE / file_name).read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}

    assert imported.isdisjoint(ENGINE_NAMES) and names.isdisjoint(ENGINE_NAMES)
    assert modules.isdisjoint({"ucla_agent.practice.werewolf.seat", "ucla_agent.practice.werewolf.play"})


# -- (8) record_voter as the seer claims the wolf it found ---------------------------------------

LONE_CLAIM = "I am the seer. Player6 is a wolf."


def test_record_voter_as_the_seer_with_a_living_wolf_found_sends_exactly_its_claim_and_votes_that_wolf():
    seer_lines = ["Your role: SEER", "Your investigations: Player0 is villager, Player6 is wolf"]
    view = _hand_built_view(seat=2, private_lines=seer_lines, **VILLAGER_VOTED_OUT)

    assert RecordVoter().choose_message(view).content == "I am the seer. Player6 is a wolf."
    assert RecordVoter().choose_action(view) == 6


def test_record_voter_as_the_seer_with_two_living_wolves_found_claims_and_votes_the_lower_one():
    seer_lines = ["Your role: SEER", "Your investigations: Player6 is wolf, Player4 is wolf"]
    view = _hand_built_view(seat=2, private_lines=seer_lines, **VILLAGER_VOTED_OUT)

    assert RecordVoter().choose_message(view).content == "I am the seer. Player4 is a wolf."
    assert RecordVoter().choose_action(view) == 4


def test_record_voter_as_the_seer_whose_found_wolf_is_dead_names_its_vote_and_claims_nothing():
    seer_lines = ["Your role: SEER", "Your investigations: Player0 is villager, Player6 is wolf"]
    wolf_voted_out = {
        "dead": [{"day": 1, "player": "seat6", "role": "wolf", "cause": "vote"}],
        "vote_history": [{"day": 1, "votes": _abstain_except({5: 6}), "eliminated": "seat6"}],
    }
    view = _hand_built_view(seat=2, private_lines=seer_lines, **wolf_voted_out)

    assert RecordVoter().choose_message(view).content == "I am voting Player0."
    assert RecordVoter().choose_action(view) == 0


def test_record_voter_as_the_seer_never_follows_another_seat_s_seer_claim():
    seer_lines = ["Your role: SEER", "Your investigations: Player0 is villager"]
    view = _hand_built_view(seat=2, private_lines=seer_lines, transcript=_chats((4, LONE_CLAIM)), **VILLAGER_VOTED_OUT)

    assert RecordVoter().choose_action(view) == 5
    assert RecordVoter().choose_message(view).content == "I am voting Player5."


# -- (9) record_voter as a villager follows a lone seer claim ------------------------------------


def _villager_reading(*chats: tuple[int, str] | tuple[int, str, int]) -> dict[str, Any]:
    """The villager in seat 1 at the vote, having read `chats`; its earlier rule votes seat 5."""
    return _hand_built_view(seat=1, private_lines=VILLAGER_LINES, transcript=_chats(*chats), **VILLAGER_VOTED_OUT)


@pytest.mark.parametrize(
    "claim", [LONE_CLAIM, "i'm the seer and Player6 is a wolf", "I’m the seer. Player6 is a WOLF!"]
)
def test_record_voter_as_a_villager_votes_the_wolf_a_lone_claimant_named(claim):
    view = _villager_reading((0, ABSTAINER_LINE), (4, claim))

    assert RecordVoter().choose_action(view) == 6
    assert RecordVoter().choose_message(view).content == "I am voting Player6."


def test_record_voter_as_a_villager_votes_the_living_seat_the_claimant_named_last():
    view = _villager_reading(
        (4, "I am the seer."), (4, "Player2 is a wolf."), (4, "Player6 is a wolf."), (0, "Player4 is a wolf.")
    )

    assert RecordVoter().choose_action(view) == 6


def test_record_voter_as_a_villager_counts_only_living_claimants():
    view = _villager_reading((3, "I am the seer. Player4 is a wolf."), (2, "I'm the seer. Player6 is a wolf."))

    assert RecordVoter().choose_action(view) == 6


def test_record_voter_as_a_villager_follows_a_claim_read_in_the_open_window_without_a_transcript():
    claim = {"seq": 3, "sender": 4, "recipients": [], "type": "chat", "content": LONE_CLAIM}
    view = {**_villager_view(), "new_messages": [claim]}

    assert RecordVoter().choose_message(view).content == "I am voting Player6."


# -- (10) record_voter as a villager without a lone claim keeps its earlier rule -----------------


@pytest.mark.parametrize(
    "chats",
    [
        pytest.param([(4, LONE_CLAIM), (2, "I'm the seer. Player4 is a wolf.")], id="two-claimants"),
        pytest.param([(4, LONE_CLAIM), (2, "I am the seer.")], id="two-claimants-one-naming-nobody"),
        pytest.param([(4, "I am the seer.")], id="claim-naming-nobody"),
        pytest.param([(4, "I am the seer. Player3 is a wolf.")], id="named-seat-dead"),
        pytest.param([(4, "I am the seer. Player1 is a wolf.")], id="named-seat-is-the-voter"),
        pytest.param([(4, LONE_CLAIM, 1)], id="claim-sent-privately"),
        pytest.param([(0, "Player6 is a wolf.")], id="no-claimant"),
    ],
)
def test_record_voter_as_a_villager_without_a_lone_claim_to_follow_uses_its_earlier_rule(chats):
    view = _villager_reading(*chats)

    assert RecordVoter().choose_action(view) == 5
    assert RecordVoter().choose_message(view).content == "I am voting Player5."


def test_record_voter_as_a_wolf_ignores_a_seer_claim_and_votes_as_before():
    claim = _chats((4, "I am the seer. Player2 is a wolf."))
    view = _hand_built_view(seat=2, private_lines=WOLF_LINES, transcript=claim, **VILLAGER_VOTED_OUT)

    assert RecordVoter().choose_action(view) == 1
    assert RecordVoter().choose_message(view).content == "I am voting Player1."


# -- (11) record_voter's claims over fifty scripted games ----------------------------------------

CLAIM_TEXT = re.compile(r"^I am the seer\. Player(?P<seat>[0-9]) is a wolf\.$")
# A record_voter seer that has found a wolf, seated with a record_voter villager, is rare at the scripted table:
# fifty games hold two votes of such a villager on a claim day, two hundred hold seventeen.
FOLLOW_GAMES = 200


def _record_voter_seats(seed: int) -> set[int]:
    return {seat for seat, kind in seat_opponents(MIXES["scripted"], seed, DRIVEN).items() if kind == RECORD_VOTER}


def _record_voter_chats(seed: int, engine: WerewolfEngine) -> list[Message]:
    seats = _record_voter_seats(seed)
    return [message for message in engine.messages if message.sender in seats and message.type == CHAT]


def _claimed_wolf(content: str) -> int | None:
    """The seat a chat in the exact claim wording names a wolf; None for any other chat."""
    match = CLAIM_TEXT.match(content)
    return int(match["seat"]) if match else None


def test_record_voter_as_a_wolf_never_claims_seer_and_its_seer_claims_are_true_over_fifty_scripted_games():
    engines, _findings = _play_checked_games("scripted", SCRIPTED_GAMES)
    chats = [(engine, message) for seed, engine in enumerate(engines) for message in _record_voter_chats(seed, engine)]
    wolf_chats = [message.content for engine, message in chats if engine.roles[message.sender] == WOLF]
    seer_mentions = [(engine, message) for engine, message in chats if "seer" in message.content.lower()]
    untrue = [
        (engine.roles, message)
        for engine, message in seer_mentions
        if engine.roles[message.sender] != SEER
        or (claimed := _claimed_wolf(message.content)) is None
        or engine.roles[claimed] != WOLF
    ]

    assert wolf_chats and [content for content in wolf_chats if "seer" in content.lower()] == []
    assert seer_mentions and untrue == []


def test_record_voter_villagers_vote_the_wolf_a_record_voter_seer_claimed_that_day_over_scripted_games():
    engines, _findings = _play_checked_games("scripted", FOLLOW_GAMES)
    votes_on_claim_days = []
    for seed, engine in enumerate(engines):
        claims = _record_voter_chats(seed, engine)
        claimed = {message.day: seat for message in claims if (seat := _claimed_wolf(message.content)) is not None}
        villagers = {seat for seat in _record_voter_seats(seed) if engine.roles[seat] == VILLAGER}
        votes_on_claim_days += [
            (seed, vote.day, voter, vote.votes[voter], claimed[vote.day])
            for vote in engine.vote_history
            if vote.day in claimed
            for voter in sorted(villagers & set(vote.votes))
        ]

    assert votes_on_claim_days
    assert [vote for vote in votes_on_claim_days if vote[3] != vote[4]] == []
