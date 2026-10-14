"""The local Werewolf practice engine, its seat session and the practice game, on scripted games with fixed seeds.

The numbered sections are the ten acceptance items of the practice engine;
the sections after them cover the window's time limit, the session's other
answers and the practice game's result and match record.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Callable

import pytest

from altruagent import TERMINATE_MESSAGING, DecisionContext, SendMessage
from altruagent.mcp_transport import MCPToolError
from altruagent.runner import DecisionError, run_game
from ucla_agent.core import paths, records
from ucla_agent.practice.werewolf import (
    OpponentError,
    PracticeSeatSession,
    RuleError,
    WerewolfEngine,
    play_practice_game,
)
from ucla_agent.practice.werewolf.engine import (
    ABSTAIN,
    DAY_VOTE,
    INACTIVITY_LIMIT_SECONDS,
    MESSAGING,
    MOVING,
    NIGHT_KILL,
    NIGHT_SEER,
    NIGHT_WOLF,
    SEER,
    VILLAGER,
    VOTE,
    WINDOW_LIMIT_SECONDS,
    WOLF,
    Death,
)
from ucla_agent.practice.werewolf.opponents import PlaceholderPlayer
from ucla_agent.practice.werewolf.view import observation_text, state_payload, visible_messages

SEED = 2026
SEEDS = range(200)
DRIVEN = 0
SESSION_ID = "practice-test"
CLOCK_START = 1_000_000.0
PLAYER_GAMES = 20
PLAYER_GAME_LIMIT_SECONDS = 1.0
MAX_DAYS = 20
PRIVATE_CONTENT = "Target Player6 tonight?"
# The guide's next_actions hint for a seat that is out of a running game.
GUIDE_DEAD_HINT = "You are out of this game but it is still running. Keep reading state and messages; you cannot act."


class FakeClock:
    """A clock that moves only when the test moves it."""

    def __init__(self) -> None:
        self.now = CLOCK_START

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class StarterPlaceholder:
    """The starter kit's placeholder agent: the first legal action, and a terminate in every window."""

    def choose_action(self, state, context):
        return state.legal_actions[0]

    def choose_message(self, state, context):
        return TERMINATE_MESSAGING


def _placeholders(driven: int = DRIVEN) -> dict[int, PlaceholderPlayer]:
    return {seat: PlaceholderPlayer() for seat in range(7) if seat != driven}


def _context(seat: int = DRIVEN) -> DecisionContext:
    return DecisionContext(SESSION_ID, None, "werewolf", f"seat{seat}", seat)


def _first_legal(engine: WerewolfEngine) -> Callable[[int], int]:
    return lambda seat: engine.legal_actions(seat)[0]


def _play_night(
    engine: WerewolfEngine, kill_of: Callable[[int], int] | None = None, look: Callable[[int], int] | None = None
) -> None:
    """Play the night: each wolf names `kill_of(wolf)`, the seer looks at `look(seer)`; first legal by default."""
    kill_of = kill_of or _first_legal(engine)
    look = look or _first_legal(engine)
    while engine.sub_phase in (NIGHT_WOLF, NIGHT_SEER):
        actor = engine.current_actor()
        engine.play(actor, kill_of(actor) if engine.sub_phase == NIGHT_WOLF else look(actor))


def _play_day(engine: WerewolfEngine, vote_of: Callable[[int], int]) -> None:
    """Close the window with every living seat's terminate, then let every living seat vote `vote_of(voter)`."""
    for seat in engine.window_waiting_on():
        engine.terminate(seat)
    while engine.sub_phase == DAY_VOTE:
        voter = engine.current_actor()
        engine.play(voter, vote_of(voter))


def _abstain(_voter: int) -> int:
    return ABSTAIN


def _non_wolves(engine: WerewolfEngine) -> list[int]:
    return [seat for seat in engine.living_seats() if engine.roles[seat] != WOLF]


def _error_code(call: Callable[[], object]) -> str:
    with pytest.raises(MCPToolError) as raised:
        call()
    return raised.value.error_code


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def engine(clock) -> WerewolfEngine:
    """A game in which the driven seat 0 is a villager."""
    return WerewolfEngine(SEED, VILLAGER, DRIVEN, clock=clock)


@pytest.fixture
def session(engine) -> PracticeSeatSession:
    """The driven seat 0 against six placeholders, at day 1's open window."""
    seat_session = PracticeSeatSession(engine, DRIVEN, _placeholders(), SESSION_ID)
    assert seat_session.get_state().phase == MESSAGING
    return seat_session


# -- (1) the deal --------------------------------------------------------------------------------


def test_every_seed_deals_two_wolves_one_seer_and_four_villagers():
    for seed in SEEDS:
        assert Counter(WerewolfEngine(seed).roles) == {WOLF: 2, SEER: 1, VILLAGER: 4}


@pytest.mark.parametrize("role", [SEER, WOLF, VILLAGER])
def test_a_forced_role_sits_at_the_forced_seat_and_one_seed_always_gives_one_deal(role):
    for seed in SEEDS:
        (deal,) = {WerewolfEngine(seed, role, 3).roles for _ in range(2)}

        assert deal[3] == role
        assert Counter(deal) == {WOLF: 2, SEER: 1, VILLAGER: 4}


def test_an_unforced_deal_is_the_same_on_every_run_and_differs_between_seeds():
    deals = {seed: WerewolfEngine(seed).roles for seed in SEEDS}

    assert all(WerewolfEngine(seed).roles == deal for seed, deal in deals.items())
    assert len(set(deals.values())) > 1


def test_a_forced_seat_needs_a_forced_role_and_a_seat_of_the_table():
    with pytest.raises(ValueError):
        WerewolfEngine(SEED, None, 3)
    with pytest.raises(ValueError):
        WerewolfEngine(SEED, SEER, 7)


# -- (2) night 1 ---------------------------------------------------------------------------------


def test_night_one_is_the_seer_s_alone_and_kills_nobody():
    for seed in range(PLAYER_GAMES):
        game = WerewolfEngine(seed)
        assert (game.sub_phase, game.current_actor()) == (NIGHT_SEER, game.roles.index(SEER))

        _play_night(game)

        assert game.deaths == []
        assert game.living_seats() == list(range(7))
        assert (game.day, game.platform_phase, game.sub_phase) == (1, MESSAGING, DAY_VOTE)


# -- (3) the night kill --------------------------------------------------------------------------


@pytest.mark.parametrize("first_wolf_names_the_lower_seat", [True, False])
def test_a_one_one_wolf_split_kills_the_lower_seat(engine, first_wolf_names_the_lower_seat):
    _play_night(engine)
    _play_day(engine, _abstain)
    first_wolf, second_wolf = engine.wolves()
    lower, higher = _non_wolves(engine)[:2]
    names = (lower, higher) if first_wolf_names_the_lower_seat else (higher, lower)

    _play_night(engine, kill_of={first_wolf: names[0], second_wolf: names[1]}.get)

    assert engine.deaths == [Death(lower, engine.roles[lower], 2, NIGHT_KILL)]


def test_the_kill_lands_at_dawn_after_the_seer_has_looked_even_at_the_seer(engine):
    _play_night(engine)
    _play_day(engine, _abstain)
    seer = engine.roles.index(SEER)
    wolf = engine.wolves()[0]

    _play_night(engine, kill_of=lambda _wolf: seer, look=lambda _seer: wolf)

    assert engine.investigations[-1] == (wolf, WOLF)
    assert engine.deaths == [Death(seer, SEER, 2, NIGHT_KILL)]
    assert engine.platform_phase == MESSAGING


def test_wolves_are_offered_only_living_non_wolves_and_the_seer_everyone_but_itself(engine):
    seer = engine.roles.index(SEER)
    assert engine.legal_actions(seer) == [seat for seat in range(7) if seat != seer]
    _play_night(engine)
    _play_day(engine, _abstain)

    wolf = engine.current_actor()

    assert engine.legal_actions(wolf) == _non_wolves(engine)


# -- (4) days without an elimination -------------------------------------------------------------


def test_a_tie_and_an_all_abstain_day_eliminate_nobody_and_the_third_in_a_row_is_a_wolf_win(engine):
    _play_night(engine)
    _play_day(engine, _abstain)
    assert engine.vote_history[-1].eliminated is None and not engine.is_over
    _play_night(engine)
    first, second = engine.living_seats()[:2]

    _play_day(engine, lambda voter: second if voter == first else first if voter == second else ABSTAIN)

    assert Counter(engine.vote_history[-1].votes.values())[first] == 1
    assert engine.vote_history[-1].eliminated is None and not engine.is_over
    _play_night(engine)
    _play_day(engine, _abstain)
    assert engine.winner == WOLF
    assert [vote.eliminated for vote in engine.vote_history] == [None, None, None]
    assert all(death.cause == NIGHT_KILL for death in engine.deaths)


def test_plurality_without_a_majority_eliminates(engine):
    _play_night(engine)
    _play_day(engine, _abstain)
    _play_night(engine)
    target, other = _non_wolves(engine)[:2]
    voters = [seat for seat in engine.living_seats() if seat not in (target, other)]

    _play_day(engine, lambda voter: target if voter in voters[:2] else other if voter == voters[2] else ABSTAIN)

    assert engine.vote_history[-1].eliminated == target
    assert engine.deaths[-1] == Death(target, engine.roles[target], 2, VOTE)


def test_an_elimination_starts_the_count_of_days_without_one_again(engine):
    first_wolf = engine.wolves()[0]
    _play_night(engine)
    for _day in range(2):
        _play_day(engine, _abstain)
        _play_night(engine)
    _play_day(engine, lambda voter: first_wolf if voter != first_wolf else ABSTAIN)
    _play_night(engine)

    _play_day(engine, _abstain)

    assert [vote.eliminated for vote in engine.vote_history] == [None, None, first_wolf, None]
    assert not engine.is_over and engine.sub_phase == NIGHT_WOLF


# -- (5) the winners -----------------------------------------------------------------------------


def test_both_wolves_dead_is_a_village_win(engine):
    first_wolf, second_wolf = engine.wolves()
    _play_night(engine)
    _play_day(engine, lambda voter: first_wolf if voter != first_wolf else second_wolf)
    assert engine.vote_history[-1].eliminated == first_wolf and not engine.is_over
    _play_night(engine)

    _play_day(engine, lambda voter: second_wolf if voter != second_wolf else _non_wolves(engine)[0])

    assert engine.winner == VILLAGER
    assert engine.returns() == {seat: (-1.0 if engine.roles[seat] == WOLF else 1.0) for seat in range(7)}


def test_living_wolves_as_many_as_the_rest_is_a_wolf_win(engine):
    _play_night(engine)
    for _day in range(2):
        victim = _non_wolves(engine)[0]
        _play_day(engine, lambda voter, victim=victim: victim if voter != victim else _non_wolves(engine)[1])
        if not engine.is_over:
            _play_night(engine)

    assert engine.winner == WOLF
    assert [death.cause for death in engine.deaths] == [VOTE, NIGHT_KILL, VOTE]
    assert len(engine.living_seats()) == 4


# -- (6) the chat limits -------------------------------------------------------------------------


def test_a_51_word_chat_is_refused_and_a_50_word_chat_is_sent(session):
    assert _error_code(lambda: session.send_message(message_type="chat", content=" ".join(["wolf"] * 51))) == (
        "TOO_MANY_WORDS"
    )

    answer = session.send_message(message_type="chat", content=" ".join(["wolf"] * 50))

    assert answer["accepted"] and answer["remaining"]["this_turn"] == 4


def test_a_sixth_chat_exceeds_the_window_s_quota(session):
    for chat in range(5):
        session.send_message(message_type="chat", content=f"Chat number {chat + 1}.")

    assert _error_code(lambda: session.send_message(message_type="chat", content="One more.")) == (
        "MESSAGES_QUOTA_EXCEEDED"
    )


@pytest.mark.parametrize("recipients", [[1, 2], [DRIVEN], [7], [-1]], ids=["two", "self", "seat 7", "seat -1"])
def test_recipients_other_than_none_or_one_other_seat_are_refused(session, recipients):
    assert _error_code(lambda: session.send_message(message_type="chat", content="Hi.", recipients=recipients)) == (
        "INVALID_RECIPIENTS"
    )


@pytest.mark.parametrize(
    "content, code", [("", "INVALID_CONTENT"), ("   ", "INVALID_CONTENT"), ("x" * 1025, "MESSAGE_TOO_LONG")]
)
def test_an_empty_or_over_long_chat_is_refused(session, content, code):
    assert _error_code(lambda: session.send_message(message_type="chat", content=content)) == code


# -- (7) private messages ------------------------------------------------------------------------


def test_a_private_message_reaches_its_recipient_and_no_third_seat(session, engine):
    session.send_message(message_type="chat", content=PRIVATE_CONTENT, recipients=[2])

    window_chats = {
        seat: [m["content"] for m in state_payload(engine, seat, SESSION_ID)["new_messages"] if m["type"] == "chat"]
        for seat in range(7)
    }
    transcripts = {seat: [m["content"] for m in visible_messages(engine, seat, SESSION_ID)] for seat in range(7)}
    assert window_chats[DRIVEN] == [PRIVATE_CONTENT] and window_chats[2] == [PRIVATE_CONTENT]
    for third_seat in (1, 3, 4, 5, 6):
        assert PRIVATE_CONTENT not in window_chats[third_seat]
        assert PRIVATE_CONTENT not in transcripts[third_seat]
    (envelope,) = [m for m in state_payload(engine, 2, SESSION_ID)["new_messages"] if m["type"] == "chat"]
    assert (envelope["sender"], envelope["recipients"], envelope["visibility"]) == (DRIVEN, [2], "direct")


def test_a_broadcast_reaches_every_seat(session, engine):
    session.send_message(message_type="chat", content="Who is quiet?")

    for seat in range(7):
        assert "Who is quiet?" in [m["content"] for m in state_payload(engine, seat, SESSION_ID)["new_messages"]]


# -- (8) the observation -------------------------------------------------------------------------


def test_the_seer_reads_its_investigation_in_the_guide_s_format_and_no_other_seat_does():
    game = WerewolfEngine(SEED, SEER, 3)
    assert observation_text(game, 3).splitlines()[2] == "You have not investigated anyone yet."
    wolf = game.wolves()[0]

    game.play(3, wolf)

    assert observation_text(game, 3).splitlines()[:3] == [
        "=== Werewolf ===",
        "Your role: SEER",
        f"Your investigations: Player{wolf} is wolf",
    ]
    for seat in (0, 1, 2, 4, 5, 6):
        observation = observation_text(game, seat)
        assert "Your investigations" not in observation and f"Player{wolf} is wolf" not in observation


def test_the_seer_s_investigations_accumulate_in_order():
    game = WerewolfEngine(SEED, SEER, 3)
    wolf = game.wolves()[0]
    villager = next(seat for seat in range(7) if game.roles[seat] == VILLAGER)
    _play_night(game, look=lambda _seer: wolf)
    _play_day(game, _abstain)

    _play_night(game, kill_of=lambda _wolf: villager, look=lambda _seer: villager)

    assert f"Your investigations: Player{wolf} is wolf, Player{villager} is villager" in observation_text(game, 3)


def test_a_wolf_sees_its_ally_and_a_villager_sees_no_role():
    game = WerewolfEngine(SEED, WOLF, 3)
    ally = next(wolf for wolf in game.wolves() if wolf != 3)
    villager = next(seat for seat in range(7) if game.roles[seat] == VILLAGER)

    assert observation_text(game, 3).splitlines()[1:3] == ["Your role: WOLF", f"Your fellow wolves: Player{ally}"]
    assert observation_text(game, villager).splitlines()[1:3] == [
        "Your role: VILLAGER",
        "You do not know anyone else's role.",
    ]


def test_the_public_record_reads_as_the_guide_shows_it(engine):
    _play_night(engine)
    victim = _non_wolves(engine)[0]
    _play_day(engine, lambda voter: victim if voter != victim else ABSTAIN)
    _play_night(engine)
    killed = engine.deaths[-1].seat

    lines = observation_text(engine, DRIVEN).splitlines()

    living = [seat for seat in range(7) if seat not in (victim, killed)]
    votes = ", ".join(f"Player{voter} -> {'abstain' if voter == victim else f'Player{victim}'}" for voter in range(7))
    assert lines[3:] == [
        *(["You are DEAD. You may watch, but not act."] if DRIVEN in (victim, killed) else []),
        "Day 2 | phase day_vote | alive 5/7",
        f"Living: {', '.join(f'Player{seat}' for seat in living)}",
        "Dead:",
        f"  day 1: Player{victim} was {engine.roles[victim]} (eliminated by vote)",
        f"  day 2: Player{killed} was {engine.roles[killed]} (killed at night)",
        f"Day 1 vote: {votes} — Player{victim} eliminated by vote",
    ]


def test_the_game_state_block_names_seats_by_display_name(engine):
    _play_night(engine)
    _play_day(engine, _abstain)

    block = state_payload(engine, DRIVEN, SESSION_ID)["game_state"]

    assert block["day"] == 2 and block["phase"] == NIGHT_WOLF
    assert block["alive"] == [f"seat{seat}" for seat in range(7)]
    assert block["vote_history"] == [
        {"day": 1, "votes": {f"seat{seat}": "abstain" for seat in range(7)}, "eliminated": None}
    ]
    assert block["winner"] is None


# -- (9) whole games through the vendored runner --------------------------------------------------


@pytest.mark.parametrize("seed", range(PLAYER_GAMES))
def test_run_game_plays_a_placeholder_seat_against_six_placeholders_to_the_end_in_under_a_second(seed):
    game = WerewolfEngine(seed)
    seat_session = PracticeSeatSession(game, DRIVEN, _placeholders(), SESSION_ID)
    started = time.perf_counter()

    final = run_game(seat_session, _context(), StarterPlaceholder())

    assert time.perf_counter() - started < PLAYER_GAME_LIMIT_SECONDS
    assert final.is_terminal and game.winner in (WOLF, VILLAGER) and game.day <= MAX_DAYS
    assert final.returns == {f"seat{seat}": score for seat, score in game.returns().items()}
    assert final.observation.endswith(f"Game over — {'WOLVES' if game.winner == WOLF else 'VILLAGERS'} win")


# -- (10) inactivity ------------------------------------------------------------------------------


def _at_the_driven_vote(session: PracticeSeatSession):
    """Close day 1's window from the driven seat, which then is the first voter."""
    session.send_message(message_type="terminate")
    state = session.get_state()
    assert state.phase == MOVING and state.is_current_actor
    return state


def test_a_driven_vote_past_the_inactivity_limit_is_replaced_by_abstain_and_the_late_submit_is_stale(
    session, engine, clock
):
    state = _at_the_driven_vote(session)
    clock.advance(INACTIVITY_LIMIT_SECONDS + 1)

    late = _error_code(lambda: session.play_action(action_id="1", state_version=state.state_version))

    assert late == "STALE_STATE"
    assert engine.vote_history[0].votes[DRIVEN] == ABSTAIN
    assert session.get_state().raw["game_state"]["vote_history"][0]["votes"]["seat0"] == "abstain"


def test_a_driven_vote_at_the_inactivity_limit_still_counts(session, engine, clock):
    state = _at_the_driven_vote(session)
    clock.advance(INACTIVITY_LIMIT_SECONDS)

    session.play_action(action_id="1", state_version=state.state_version)

    assert engine.vote_history[0].votes[DRIVEN] == 1


def test_an_idle_wolf_s_night_turn_is_given_the_first_legal_target(clock):
    game = WerewolfEngine(SEED, WOLF, DRIVEN, clock=clock)
    ally = next(wolf for wolf in game.wolves() if wolf != DRIVEN)
    _play_night(game)
    _play_day(game, lambda voter: ally if voter != ally else ABSTAIN)
    assert (game.sub_phase, game.current_actor()) == (NIGHT_WOLF, DRIVEN)
    first_target = game.legal_actions(DRIVEN)[0]
    clock.advance(INACTIVITY_LIMIT_SECONDS + 1)

    game.apply_timeouts()
    _play_night(game)

    assert game.deaths[-1] == Death(first_target, game.roles[first_target], 2, NIGHT_KILL)


# -- the window's time limit ---------------------------------------------------------------------


def test_the_window_counts_down_and_closes_by_itself_when_its_time_is_up(session, engine, clock):
    assert session.get_state().raw["messaging_seconds_left"] == WINDOW_LIMIT_SECONDS
    clock.advance(WINDOW_LIMIT_SECONDS / 2)
    assert session.get_state().raw["messaging_seconds_left"] == WINDOW_LIMIT_SECONDS / 2

    clock.advance(WINDOW_LIMIT_SECONDS / 2)

    state = session.get_state()
    assert state.phase == MOVING and state.raw["messaging_seconds_left"] is None
    driven_terminates = [m for m in session.get_messages()["messages"] if m["sender"] == DRIVEN]
    assert [(m["type"], m["content"]) for m in driven_terminates] == [("terminate", "")]
    assert _error_code(lambda: session.send_message(message_type="chat", content="Too late.")) == "WRONG_PHASE"


def test_the_window_closes_once_every_living_seat_has_terminated(session):
    answer = session.send_message(message_type="terminate")

    assert answer["phase"] == MOVING and answer["message"]["type"] == "terminate"
    terminates = [m["sender"] for m in session.get_messages()["messages"] if m["type"] == "terminate"]
    assert sorted(terminates) == list(range(7))


# -- the session's other answers -----------------------------------------------------------------


def test_wait_for_update_answers_at_once_and_says_whether_anything_changed(session):
    state = session.get_state()
    started = time.perf_counter()

    unchanged = session.wait_for_update(
        since_version=state.state_version,
        since_message_seq=max(m.index for m in state.new_messages),
        since_is_current_actor=bool(state.is_current_actor),
        since_phase=state.phase,
        timeout_seconds=20,
    )
    changed = session.wait_for_update(since_version=state.state_version - 1)

    assert time.perf_counter() - started < 0.5
    assert unchanged.raw["updated"] is False and changed.raw["updated"] is True


def test_wait_for_update_wakes_on_a_change_of_turn_or_phase(session):
    state = session.get_state()
    version = state.state_version

    assert session.wait_for_update(since_version=version, since_is_current_actor=not state.is_current_actor).raw[
        "updated"
    ]
    assert session.wait_for_update(since_version=version, since_phase=MOVING).raw["updated"]


def test_get_legal_actions_lists_the_first_voter_s_votes_with_ready_inputs_while_the_window_is_open(session):
    legal = session.get_legal_actions()

    assert [action["label"] for action in legal["actions"]] == [
        *(f"Vote to eliminate Player{seat}" for seat in range(1, 7)),
        "Abstain",
    ]
    assert legal["actions"][-1]["input"] == {
        "session_id": SESSION_ID,
        "action_id": "7",
        "state_version": legal["state_version"],
    }


def test_a_message_type_other_than_chat_or_terminate_is_invalid_content(session):
    assert _error_code(lambda: session.send_message(message_type="shout", content="Hi.")) == "INVALID_CONTENT"


def test_a_second_terminate_is_a_no_op_and_nothing_is_posted_after_the_game(engine):
    _play_night(engine)
    seat = engine.window_waiting_on()[0]
    engine.terminate(seat)

    assert engine.terminate(seat) is None
    engine.resign(seat)
    with pytest.raises(RuleError) as raised:
        engine.send_chat(seat, "Good game.")
    assert raised.value.code == "GAME_ALREADY_COMPLETE"
    assert engine.window_waiting_on() == []


def test_an_opponent_that_breaks_a_rule_is_an_opponent_error_not_the_driven_seat_s(engine):
    class Cheater(PlaceholderPlayer):
        def choose_message(self, view):
            return SendMessage(" ".join(["wolf"] * 51))

    seat_session = PracticeSeatSession(engine, DRIVEN, {**_placeholders(), 4: Cheater()}, SESSION_ID)

    with pytest.raises(OpponentError, match="seat 4"):
        seat_session.get_state()


def test_a_move_in_the_window_is_the_wrong_phase(session):
    state = session.get_state()

    assert _error_code(lambda: session.play_action(action_id="1", state_version=state.state_version)) == "WRONG_PHASE"


def test_a_move_out_of_turn_is_refused(engine):
    _play_night(engine)
    for seat in engine.window_waiting_on():
        engine.terminate(seat)

    with pytest.raises(RuleError) as raised:
        engine.play(engine.current_actor() + 1, ABSTAIN)

    assert raised.value.code == "NOT_YOUR_TURN"


def test_an_action_outside_the_legal_list_is_invalid(session):
    state = _at_the_driven_vote(session)

    assert _error_code(lambda: session.play_action(action_id="0", state_version=state.state_version)) == (
        "INVALID_ACTION"
    )
    assert _error_code(lambda: session.play_action(action={"type": "vote"}, state_version=state.state_version)) == (
        "INVALID_ACTION"
    )


def test_a_dead_seat_may_not_move_chat_or_resign_while_the_game_runs(engine):
    _play_night(engine)
    victim = _non_wolves(engine)[0]
    _play_day(engine, lambda voter: victim if voter != victim else ABSTAIN)
    _play_night(engine)
    attempts = {
        "move": lambda: engine.play(victim, ABSTAIN),
        "chat": lambda: engine.send_chat(victim, "I am back."),
        "terminate": lambda: engine.terminate(victim),
        "resign": lambda: engine.resign(victim),
    }

    for attempt in attempts.values():
        with pytest.raises(RuleError) as raised:
            attempt()
        assert raised.value.code == "PLAYER_ELIMINATED"
    assert state_payload(engine, victim, SESSION_ID)["next_actions"] == [
        {"tool": "wait_for_update", "hint": GUIDE_DEAD_HINT}
    ]


def test_a_driven_seat_voted_out_reads_its_death_and_the_game_plays_on_to_its_end(session, engine):
    state = _at_the_driven_vote(session)

    session.play_action(action_id="1", state_version=state.state_version)

    dead = session.get_state()
    assert dead.raw["eliminated"] and "You are DEAD. You may watch, but not act." in dead.observation
    assert dead.is_terminal and engine.is_over
    assert _error_code(lambda: session.resign()) == "GAME_ALREADY_COMPLETE"


def test_a_resignation_ends_the_game_for_the_resigner_s_side(session, engine):
    result = session.resign()

    assert result["is_terminal"] and result["termination_reason"] == "resignation"
    assert engine.winner == WOLF
    assert result["returns"] == {
        f"seat{seat}": (-1.0 if seat == DRIVEN else 1.0 if engine.roles[seat] == WOLF else 0.0) for seat in range(7)
    }
    assert result["your_return"] == -1.0


def test_get_result_is_null_while_the_game_runs(session):
    result = session.get_result()

    assert (result["is_terminal"], result["returns"], result["your_return"]) == (False, None, None)


def test_a_session_needs_one_opponent_in_each_other_seat(engine):
    with pytest.raises(ValueError):
        PracticeSeatSession(engine, DRIVEN, {1: PlaceholderPlayer()})


# -- the practice game ---------------------------------------------------------------------------


@pytest.fixture
def practice_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The folder the practice games' records go to."""
    runs = tmp_path / "practice-runs"
    monkeypatch.setenv(paths.RUNS_DIR_ENV, str(runs))
    return runs


def _record_folder(runs: Path, session_id: str, seat: int) -> Path:
    (folder,) = runs.glob(f"*/{session_id}__{seat}")
    return folder


def test_a_practice_game_reports_its_result_and_writes_a_normal_match_record(practice_runs, read_jsonl):
    result = play_practice_game(SEED, StarterPlaceholder, "scripted", forced_role=SEER, seat=4)

    assert (result.seed, result.seat, result.role, result.mix) == (SEED, 4, SEER, "scripted")
    assert result.winner in (WOLF, VILLAGER) and result.our_side_won == (result.winner == VILLAGER)
    assert 1 <= result.days_played <= MAX_DAYS and result.unhandled_error is False
    assert set(result.opponent_seats) == {0, 1, 2, 3, 5, 6}
    assert Counter(result.opponent_seats.values()) == {"placeholder": 2, "abstainer": 2, "record_voter": 2}
    folder = _record_folder(practice_runs, result.session_id, 4)
    events = read_jsonl(folder / "events.jsonl")
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert (meta["game_type"], meta["seat"], meta["agent_id"]) == ("werewolf", "4", "seat4")
    assert events[-1]["kind"] == "result"
    assert events[-1]["data"]["result"]["your_return"] == (1.0 if result.our_side_won else -1.0)


def test_practice_games_in_one_process_each_get_a_record_of_their_own(practice_runs, read_jsonl):
    results = [play_practice_game(seed, StarterPlaceholder, "scripted") for seed in (1, 2, 3)]

    for result in results:
        events = read_jsonl(_record_folder(practice_runs, result.session_id, result.seat) / "events.jsonl")
        assert events[-1]["kind"] == "result"


def test_one_seed_plays_the_same_game_in_the_same_seat(practice_runs):
    first, second = (play_practice_game(SEED, StarterPlaceholder, "scripted") for _ in range(2))

    assert (first.seat, first.role, first.winner, first.days_played, first.opponent_seats) == (
        second.seat,
        second.role,
        second.winner,
        second.days_played,
        second.opponent_seats,
    )
    assert first.session_id != second.session_id


def test_an_unhandled_error_in_the_record_is_reported(practice_runs):
    class FailingQuietly(StarterPlaceholder):
        def choose_action(self, state, context):
            records.log("error", {"where": "test", "type": "unhandled", "exception": "ValueError", "message": "x"})
            return state.legal_actions[0]

    result = play_practice_game(SEED, FailingQuietly, "scripted", forced_role=SEER, seat=0)

    assert result.unhandled_error is True


def test_a_game_the_runner_gives_up_on_still_closes_its_record_and_the_next_game_gets_its_own(
    practice_runs, read_jsonl
):
    class Broken(StarterPlaceholder):
        def choose_message(self, state, context):
            raise RuntimeError("broken decision-maker")

    with pytest.raises(DecisionError):
        play_practice_game(SEED, Broken, "scripted", forced_role=VILLAGER, seat=DRIVEN)
    after = play_practice_game(SEED, StarterPlaceholder, "scripted", forced_role=VILLAGER, seat=DRIVEN)

    folders = sorted(practice_runs.glob(f"*/practice-werewolf-{SEED}-*__{DRIVEN}"))
    endings = {folder.name: read_jsonl(folder / "events.jsonl")[-1] for folder in folders}
    assert len(endings) == 2
    assert endings[f"{after.session_id}__{DRIVEN}"]["kind"] == "result"
    (aborted,) = [ending for name, ending in endings.items() if not name.startswith(after.session_id)]
    assert (aborted["kind"], aborted["data"]["reason"]) == ("match_end", "next_match")


def test_a_table_given_as_six_kinds_is_a_custom_mix(practice_runs):
    result = play_practice_game(SEED, StarterPlaceholder, ["placeholder"] * 6, seat=DRIVEN)

    assert result.mix == "custom" and set(result.opponent_seats.values()) == {"placeholder"}


@pytest.mark.parametrize("opponents", ["no-such-mix", ["placeholder"] * 5, ["placeholder"] * 5 + ["wizard"]])
def test_an_unknown_mix_or_a_table_of_the_wrong_kinds_is_refused(practice_runs, opponents):
    with pytest.raises(ValueError):
        play_practice_game(SEED, StarterPlaceholder, opponents)
