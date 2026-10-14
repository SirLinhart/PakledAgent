"""Whole Werewolf games: the agent plays every role to the end on the practice table, through the real runner loop.

The numbered tests are the acceptance items of the registered agent. Each
game is a `scripted` table with the agent in the seat the seed gives and the
role the test forces. The engine refuses an illegal move and a chat over the
platform's limits, which the runner turns into a `DecisionError`, so a game
that finishes is a game in which every move was legal and every chat within
the limits. No test reaches a model server: model use is off, or the sources
are the stubs of `werewolf_stubs`.
"""

from __future__ import annotations

import dataclasses
import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest
from werewolf_states import MESSAGING, SEER, VILLAGER, WOLF, raw_state
from werewolf_stubs import Guarded

from altruagent import TERMINATE_MESSAGING, DecisionContext, GameState
from ucla_agent.core import paths, registry
from ucla_agent.core.dispatch import Dispatcher
from ucla_agent.core.record_files import match_folder_name, seat_label
from ucla_agent.games.werewolf.agent import WerewolfAgent
from ucla_agent.games.werewolf.settings import WerewolfSettings
from ucla_agent.practice.werewolf import play as practice
from ucla_agent.practice.werewolf.engine import WerewolfEngine
from ucla_agent.practice.werewolf.mixes import SCRIPTED_MIX
from ucla_agent.practice.werewolf.play import PracticeResult

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = WerewolfSettings.from_files(REPO_ROOT)
TUNING = SETTINGS.tuning
ROLES = (VILLAGER, SEER, WOLF)
SIDES = ("villager", "wolf")
SCRIPTED_GAMES, NONSENSE_GAMES, SLEEPING_GAMES = 30, 10, 5
NONSENSE_SEED_BASE, SLEEPING_SEED_BASE = 1000, 2000
MODELS_OFF = dataclasses.replace(SETTINGS, model_choices=False, model_messages=False)
SHORT_DEADLINE_MS = 30
SHORT_DEADLINES = dataclasses.replace(
    SETTINGS,
    choice_deadline_ms=SHORT_DEADLINE_MS,
    message_deadline_ms=SHORT_DEADLINE_MS,
    tuning=dataclasses.replace(TUNING, chat=dataclasses.replace(TUNING.chat, min_deadline_ms=1)),
)
EIGHTY_WORDS = " ".join(["Player1"] * 80)
SLEEP_SECONDS = 1.0
GAME_LIMIT_SECONDS = 10.0
ACTION, CHAT, TERMINATE = "action", "chat", "terminate"


@dataclass(frozen=True)
class Game:
    """One finished practice game: its result, its engine, the agent's match record and the seconds it took."""

    result: PracticeResult
    engine: WerewolfEngine
    events: list[dict[str, Any]]
    seconds: float

    def data_of(self, kind: str) -> list[dict[str, Any]]:
        """The data of the record's events of one kind, in file order."""
        return [event["data"] for event in self.events if event["kind"] == kind]


@pytest.fixture
def play_game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Game]:
    """Plays one practice game; its record goes under a temporary runs folder and its engine notes every attempt.

    Every move, chat and terminate a seat tries is noted with whether the
    seat was alive when it tried, before the engine accepts or refuses it.
    """
    runs = tmp_path / "runs"
    monkeypatch.setenv(paths.RUNS_DIR_ENV, str(runs))
    engines: list[WatchedEngine] = []

    class WatchedEngine(WerewolfEngine):
        def __init__(self, *arguments: Any, **options: Any) -> None:
            super().__init__(*arguments, **options)
            self.attempts: list[tuple[int, str, bool]] = []
            engines.append(self)

        def play(self, seat: int, action: int) -> None:
            self.attempts.append((seat, ACTION, self.is_alive(seat)))
            super().play(seat, action)

        def send_chat(self, seat: int, content: str, recipients: Any = None) -> Any:
            self.attempts.append((seat, CHAT, self.is_alive(seat)))
            return super().send_chat(seat, content, recipients)

        def terminate(self, seat: int) -> Any:
            self.attempts.append((seat, TERMINATE, self.is_alive(seat)))
            return super().terminate(seat)

    monkeypatch.setattr(practice, "WerewolfEngine", WatchedEngine)

    def play(seed: int, role: str, agent_factory: Callable[[], Any]) -> Game:
        started = time.perf_counter()
        result = practice.play_practice_game(seed, agent_factory, SCRIPTED_MIX, forced_role=role)
        seconds = time.perf_counter() - started
        (events_file,) = runs.glob(f"*/{match_folder_name(result.session_id, seat_label(result.seat))}/events.jsonl")
        events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines()]
        return Game(result, engines[-1], events, seconds)

    return play


def assert_played_to_the_end(game: Game) -> None:
    """The game has a winner and no unhandled error, and the agent behaved as items (4) and (5) require."""
    assert game.result.winner in SIDES and not game.result.unhandled_error, game.result
    assert_silent_once_eliminated(game)
    assert_one_ledger_event_per_day_seen(game)


def assert_silent_once_eliminated(game: Game) -> None:
    """(4) Our seat tried no move and no message once it was out, and the record ends with the game's result."""
    seat = game.result.seat
    assert [attempt for attempt in game.engine.attempts if attempt[0] == seat and not attempt[2]] == []
    assert game.events[-1]["kind"] == "result"


def assert_one_ledger_event_per_day_seen(game: Game) -> None:
    """(5) One `werewolf_ledger` event for each day whose discussion our seat saw, and none for another day."""
    days_seen = {state["state"]["game_state"]["day"] for state in game.data_of("state") if state["phase"] == MESSAGING}
    ledger_days = Counter(ledger["day"] for ledger in game.data_of("werewolf_ledger"))

    assert days_seen and ledger_days == Counter(days_seen)


def agent_with(settings: WerewolfSettings, source_factory: Callable[[], Any] | None = None) -> Callable[[], Any]:
    """A factory of agents with these settings, each with its own new stub source for choices and for messages."""
    if source_factory is None:
        return lambda: WerewolfAgent(settings)
    return lambda: WerewolfAgent(settings, choice_source=source_factory(), message_source=source_factory())


# -- (1) to (5) whole games --------------------------------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_1_thirty_scripted_games_per_role_with_models_off_all_finish_with_a_winner(play_game, role):
    games = [play_game(seed, role, agent_with(MODELS_OFF)) for seed in range(SCRIPTED_GAMES)]

    for game in games:
        assert game.result.role == role
        assert_played_to_the_end(game)
    assert len({game.result.seat for game in games}) > 1
    assert any(not game.engine.is_alive(game.result.seat) for game in games)


@pytest.mark.parametrize("role", ROLES)
def test_2_games_with_sources_answering_nonsense_finish_and_record_their_fallbacks(play_game, role):
    nonsense = agent_with(SETTINGS, lambda: Guarded(choice="P9", text=EIGHTY_WORDS))

    games = [play_game(NONSENSE_SEED_BASE + seed, role, nonsense) for seed in range(NONSENSE_GAMES)]

    for game in games:
        assert_played_to_the_end(game)
        assert all(not chat["model_text_used"] for chat in game.data_of("werewolf_chat"))
    assert sum(len(game.data_of("fallback")) for game in games) > 0


@pytest.mark.parametrize("role", ROLES)
def test_3_games_with_sources_sleeping_past_short_deadlines_finish_within_ten_seconds_each(play_game, role):
    sleeping = agent_with(SHORT_DEADLINES, lambda: Guarded(choice="P1", text="Player1.", seconds=SLEEP_SECONDS))

    games = [play_game(SLEEPING_SEED_BASE + seed, role, sleeping) for seed in range(SLEEPING_GAMES)]

    for game in games:
        assert_played_to_the_end(game)
        assert game.seconds < GAME_LIMIT_SECONDS
    timeouts = [call for game in games for call in game.data_of("fallback") if call["reason"] == "timeout"]
    assert timeouts


def test_the_agent_s_chats_reach_the_table_and_every_decision_is_recorded(play_game):
    game = play_game(3, WOLF, agent_with(MODELS_OFF))
    seat = game.result.seat

    chats = [message for message in game.engine.messages if message.sender == seat and message.type == CHAT]
    assert chats and len(chats) == len(game.data_of("werewolf_chat"))
    moves = [attempt for attempt in game.engine.attempts if attempt[0] == seat and attempt[1] == ACTION]
    assert len(moves) == len(game.data_of("werewolf_decision")) == len(game.data_of("action"))


# -- (6) the registered decision-maker ----------------------------------------------------------------


def test_6_the_registry_names_werewolf_agent_and_the_dispatcher_hands_a_werewolf_match_to_it(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.RUNS_DIR_ENV, str(tmp_path / "runs"))
    dispatcher = Dispatcher()
    context = DecisionContext("registered-werewolf", None, "werewolf", "seat3", seat_position=3)
    # Our seat is out of the game, so the agent answers without asking any model.
    eliminated = GameState.from_mcp_state(raw_state(phase=MESSAGING, day=2, dead=[(1, 3, VILLAGER, "night_kill")]))

    answer = dispatcher.choose_message(eliminated, context)

    assert registry.GAME_FACTORIES["werewolf"] is WerewolfAgent
    assert isinstance(dispatcher.decision_maker, WerewolfAgent) and answer is TERMINATE_MESSAGING
