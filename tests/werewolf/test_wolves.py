"""The wolves' plan on hand-built ledgers: the kill, the word with the ally, the cover vote and the counterclaim.

The numbered tests are the eight acceptance items of the wolves' play. Unless
a test says otherwise our seat is 2 and our ally is seat 4.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator

import pytest
from werewolf_states import MESSAGING, MOVING, NIGHT_KILL, NIGHT_WOLF, VILLAGER, VOTE, WOLF, chat, practice_states
from werewolf_states import state

from ucla_agent.games.werewolf import wolves
from ucla_agent.games.werewolf.inference import Beliefs, infer
from ucla_agent.games.werewolf.ledger import Ledger
from ucla_agent.games.werewolf.tuning import Tuning

REPO_ROOT = Path(__file__).resolve().parents[2]
TUNING = Tuning.from_file(REPO_ROOT)
COORDINATION = TUNING.chat.templates.coordination
ABSTAIN = 7
SEATS = range(7)
US, ALLY = 2, 4
PRACTICE_SEEDS = range(10)
POLICY_LIMIT_SECONDS = 0.005
TIMED_RUNS = 5


def ledger_of(*states_fields: dict) -> Ledger:
    """A ledger given one state per set of fields, by default those of our wolf seat 2 with ally 4."""
    ledger = Ledger(TUNING.chat)
    for fields in states_fields:
        ledger.update(state(**{"seat": US, "role": WOLF, "allies": [ALLY], **fields}))
    return ledger


def beliefs(p_seer: dict[int, float] | None = None, p_wolf: dict[int, float] | None = None) -> Beliefs:
    """Beliefs with the given probabilities and 0 for every seat left out."""
    return Beliefs(
        p_wolf={seat: (p_wolf or {}).get(seat, 0.0) for seat in SEATS},
        p_seer={seat: (p_seer or {}).get(seat, 0.0) for seat in SEATS},
        layouts=1,
    )


def accusations(target: int, accusers: list[int], first_seq: int = 0) -> list[dict]:
    """One chat from each accuser calling the target a wolf."""
    return [
        chat(first_seq + offset, accuser, f"Player{target} is suspicious") for offset, accuser in enumerate(accusers)
    ]


# -- (1) the kill never names our ally ---------------------------------------------------------------


def living_wolf_ledgers(seed: int) -> Iterator[Ledger]:
    """A wolf seat's ledger after each state of a practice game in which that seat is alive and the game goes on."""
    for seat in SEATS:
        ledger = Ledger(TUNING.chat)
        for game_state in practice_states(seed, seat):
            ledger.update(game_state)
            if _is_living_wolf(ledger):
                yield ledger


def _is_living_wolf(ledger: Ledger) -> bool:
    facts = ledger.facts()
    return ledger.private().role == WOLF and not facts.eliminated and facts.winner is None


def wolves_of(ledger: Ledger) -> set[int]:
    """Our seat and our allies'."""
    return {ledger.facts().seat, *ledger.private().allies}


@pytest.mark.parametrize("seed", PRACTICE_SEEDS)
def test_1_over_practice_games_no_kill_list_names_a_wolf(seed):
    # Each ledger is read as it is yielded: the generator goes on updating it with the game's next state.
    kills = [
        (wolves_of(ledger), wolves.night_wolf(ledger, infer(ledger, TUNING.inference), TUNING.wolves))
        for ledger in living_wolf_ledgers(seed)
        if ledger.facts().sub_phase == NIGHT_WOLF
    ]

    assert kills and all(targets and not set(targets) & wolf_seats for wolf_seats, targets in kills)


def test_1_the_ally_is_left_out_even_as_the_likeliest_seer():
    targets = wolves.night_wolf(ledger_of({}), beliefs(p_seer={ALLY: 0.9, 3: 0.1}), TUNING.wolves)

    assert ALLY not in targets and US not in targets and targets[0] == 3


# -- (2) and (8) the ally's kill request -------------------------------------------------------------


def test_2_an_ally_s_private_kill_request_makes_its_target_the_only_candidate():
    request = chat(0, ALLY, "kill Player5", recipients=[US])
    ledger = ledger_of({"phase": MESSAGING, "messages": [chat(1, 6, "I am the seer."), request]})

    assert wolves.night_wolf(ledger, beliefs(p_seer={6: 0.9}), TUNING.wolves) == [5]


def test_2_a_request_naming_a_dead_seat_is_ignored():
    day = {"phase": MESSAGING, "messages": [chat(0, ALLY, "kill Player5", recipients=[US])]}
    night = {"sub_phase": NIGHT_WOLF, "day": 2, "dead": [(1, 5, VILLAGER, VOTE)]}

    targets = wolves.night_wolf(ledger_of(day, night), beliefs(p_seer={3: 0.5}), TUNING.wolves)

    assert 5 not in targets and targets[0] == 3


def test_2_a_public_kill_message_or_one_from_another_seat_is_no_request():
    messages = [chat(0, ALLY, "kill Player5"), chat(1, 6, "kill Player1", recipients=[US])]

    targets = wolves.night_wolf(ledger_of({"phase": MESSAGING, "messages": messages}), beliefs(), TUNING.wolves)

    assert targets == [0, 1, 3, 5, 6]


def test_2_when_both_wolves_asked_the_earlier_request_stands():
    ours = chat(0, US, "Kill Player3 tonight.", recipients=[ALLY])
    theirs = chat(1, ALLY, "Kill Player5 tonight.", recipients=[US])

    assert wolves.night_wolf(ledger_of({"phase": MESSAGING, "messages": [theirs, ours]}), beliefs(), TUNING.wolves) == [
        3
    ]


@pytest.mark.parametrize("night_day", [1, 2], ids=["same day", "next day"])
def test_8_the_day_s_request_holds_at_the_following_night_whatever_day_the_night_reports(night_day):
    day = {"phase": MESSAGING, "messages": [chat(0, ALLY, "Kill Player5 tonight.", recipients=[US])]}
    night = {"phase": MOVING, "sub_phase": NIGHT_WOLF, "day": night_day, "dead": [(1, 1, VILLAGER, VOTE)]}

    assert wolves.night_wolf(ledger_of(day, night), beliefs(p_seer={3: 0.9}), TUNING.wolves) == [5]


# -- (3) and (4) the ranking -------------------------------------------------------------------------


def test_3_without_a_request_a_seer_claimant_ranks_first_then_p_seer():
    ledger = ledger_of({"phase": MESSAGING, "messages": [chat(0, 6, "I am the seer.")]})

    targets = wolves.night_wolf(ledger, beliefs(p_seer={1: 0.5, 3: 0.3, 6: 0.2}), TUNING.wolves)

    assert targets[:3] == [6, 1, 3]


def test_3_a_claimant_that_took_its_claim_back_ranks_by_p_seer_again():
    messages = [chat(0, 6, "I am the seer."), chat(1, 6, "Sorry, I am not the seer.")]
    ledger = ledger_of({"phase": MESSAGING, "messages": messages})

    assert wolves.night_wolf(ledger, beliefs(p_seer={1: 0.5, 6: 0.2}), TUNING.wolves)[:2] == [1, 6]


def test_3_equal_p_seer_goes_to_the_seat_that_accused_a_wolf_most():
    messages = [*accusations(US, [5], 0), *accusations(ALLY, [5, 3], 1)]
    ledger = ledger_of({"phase": MESSAGING, "messages": messages})

    assert wolves.night_wolf(ledger, beliefs(), TUNING.wolves)[:3] == [5, 3, 0]


def test_4_both_wolves_rank_the_same_top_kill_target():
    messages = [chat(0, 3, "I am the seer. Player6 is a villager."), *accusations(ALLY, [0, 6], 1)]
    day = {"phase": MESSAGING, "day": 2, "messages": messages, "dead": [(1, 1, VILLAGER, NIGHT_KILL)]}
    night = {"sub_phase": NIGHT_WOLF, "day": 2, "dead": [(1, 1, VILLAGER, NIGHT_KILL)]}
    ours = ledger_of(day, night)
    theirs = Ledger(TUNING.chat)
    for fields in (day, night):
        theirs.update(state(**{**fields, "seat": ALLY, "role": WOLF, "allies": [US]}))

    our_targets = wolves.night_wolf(ours, infer(ours, TUNING.inference), TUNING.wolves)
    their_targets = wolves.night_wolf(theirs, infer(theirs, TUNING.inference), TUNING.wolves)

    assert our_targets[0] == their_targets[0] == 3
    assert our_targets == their_targets


# -- (5) the word with the ally ----------------------------------------------------------------------


def test_5_the_coordination_message_is_exactly_kill_the_top_target_tonight_to_the_ally():
    ledger = ledger_of({"phase": MESSAGING, "messages": [chat(0, 6, "I am the seer.")]})

    message = wolves.coordination(ledger, beliefs(), TUNING.wolves, COORDINATION)

    assert message == wolves.PrivateMessage("Kill Player6 tonight.", ALLY)


def test_5_no_coordination_after_the_ally_s_own_request():
    request = chat(0, ALLY, "Kill Player5 tonight.", recipients=[US])
    ledger = ledger_of({"phase": MESSAGING, "messages": [request]})

    assert wolves.coordination(ledger, beliefs(), TUNING.wolves, COORDINATION) is None


def test_5_no_coordination_without_a_living_ally():
    ledger = ledger_of({"phase": MESSAGING, "day": 2, "dead": [(1, ALLY, WOLF, VOTE)]})

    assert wolves.coordination(ledger, beliefs(), TUNING.wolves, COORDINATION) is None


# -- (6) the cover vote ------------------------------------------------------------------------------

LATE_DEATH = [(1, 6, VILLAGER, VOTE)]


def test_6_with_three_of_four_living_non_wolves_accusing_the_ally_it_is_the_only_candidate():
    ledger = ledger_of({"phase": MESSAGING, "day": 2, "dead": LATE_DEATH, "messages": accusations(ALLY, [0, 1, 3])})

    assert wolves.day_vote(ledger, beliefs(), TUNING.wolves) == [ALLY]


def test_6_with_one_of_four_accusing_the_ally_it_is_absent():
    ledger = ledger_of({"phase": MESSAGING, "day": 2, "dead": LATE_DEATH, "messages": accusations(ALLY, [0])})

    votes = wolves.day_vote(ledger, beliefs(), TUNING.wolves)

    assert votes == [0, 1, 3, 5] and ABSTAIN not in votes


def test_6_the_table_s_leading_candidate_comes_first_then_a_claimed_seer_then_the_lowest_seat():
    messages = [*accusations(5, [0, 1]), *accusations(ALLY, [3], 2), chat(3, 3, "I am the seer.")]
    ledger = ledger_of({"phase": MESSAGING, "day": 2, "dead": LATE_DEATH, "messages": messages})

    assert wolves.day_vote(ledger, beliefs(), TUNING.wolves) == [5, 3, 0, 1]


def test_6_an_ally_that_ties_for_the_lead_is_not_voted():
    messages = [*accusations(ALLY, [0, 1]), *accusations(3, [5, 6], 2)]

    votes = wolves.day_vote(ledger_of({"phase": MESSAGING, "messages": messages}), beliefs(), TUNING.wolves)

    assert ALLY not in votes and votes[0] == 3


@pytest.mark.parametrize("seed", PRACTICE_SEEDS)
def test_6_over_practice_games_no_vote_list_holds_abstain_or_a_wolf_without_the_bussing_rule(seed):
    # The practice states carry no chat, so no accusation can make the bussing rule fire.
    votes = [
        (wolves_of(ledger), wolves.day_vote(ledger, infer(ledger, TUNING.inference), TUNING.wolves))
        for ledger in living_wolf_ledgers(seed)
    ]

    assert votes and all(ABSTAIN not in listed and not set(listed) & wolf_seats for wolf_seats, listed in votes)


# -- (7) the counterclaim ----------------------------------------------------------------------------


def test_7_a_seer_claimant_naming_our_ally_a_wolf_draws_a_counterclaim_naming_it_a_wolf():
    ledger = ledger_of({"phase": MESSAGING, "messages": [chat(0, 6, "I am the seer. Player4 is a wolf.")]})

    accuser = wolves.counterclaim_against(ledger)

    assert accuser == 6
    assert wolves.counterclaim_points(accuser) == ("I am the seer", "Player6 is a wolf")


def test_7_no_counterclaim_when_our_ally_already_claimed_the_seer():
    messages = [chat(0, ALLY, "I am the seer."), chat(1, 6, "I am the seer. Player4 is a wolf.")]

    assert wolves.counterclaim_against(ledger_of({"phase": MESSAGING, "messages": messages})) is None


def test_7_no_counterclaim_for_a_claim_against_another_seat_or_for_a_village_seat():
    against_another = ledger_of({"phase": MESSAGING, "messages": [chat(0, 6, "I am the seer. Player3 is a wolf.")]})
    village_seat = Ledger(TUNING.chat)
    village_seat.update(
        state(seat=2, role=VILLAGER, phase=MESSAGING, messages=[chat(0, 6, "I am the seer. Player4 is a wolf.")])
    )

    assert wolves.counterclaim_against(against_another) is None
    assert wolves.counterclaim_against(village_seat) is None


def test_7_a_counterclaim_once_made_is_repeated_against_the_same_seat():
    ledger = ledger_of({"phase": MESSAGING, "day": 2, "messages": [chat(0, 5, "I am the seer. Player4 is a wolf.")]})

    assert wolves.counterclaim_against(ledger, earlier=6) == 6


# -- the constraint: fast ----------------------------------------------------------------------------


def test_each_policy_takes_under_five_milliseconds_on_a_late_ledger():
    messages = [chat(0, 6, "I am the seer. Player4 is a wolf."), *accusations(ALLY, [0, 1, 3], 1)]
    ledger = ledger_of(
        {
            "phase": MESSAGING,
            "day": 4,
            "messages": messages,
            "dead": [(1, 5, VILLAGER, NIGHT_KILL)],
            "votes": [(1, {0: 5, 1: 5, 2: 5, 3: 5, 4: 0, 5: 0, 6: None}, 5)],
        }
    )
    found = infer(ledger, TUNING.inference)

    for policy in (
        lambda: wolves.night_wolf(ledger, found, TUNING.wolves),
        lambda: wolves.day_vote(ledger, found, TUNING.wolves),
        lambda: wolves.coordination(ledger, found, TUNING.wolves, COORDINATION),
        lambda: wolves.counterclaim_against(ledger),
    ):
        assert min(_seconds(policy) for _ in range(TIMED_RUNS)) < POLICY_LIMIT_SECONDS


def _seconds(call) -> float:
    started = time.perf_counter()
    call()
    return time.perf_counter() - started
