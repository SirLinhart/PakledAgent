"""The village's plan on hand-built ledgers: the vote, the seer's investigation and the seer's reveal.

The numbered tests are the acceptance items of the village's play: (1) to (7),
then (8) to (13) for the vote's sparing of a lone seer claimant.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

import pytest
from werewolf_states import DAY_VOTE, MESSAGING, MOVING, NIGHT_KILL, NIGHT_SEER, SEER, VILLAGER, VOTE, WOLF, chat
from werewolf_states import practice_states, state

from ucla_agent.games.werewolf import decide, village
from ucla_agent.games.werewolf.inference import Beliefs, infer
from ucla_agent.games.werewolf.ledger import Ledger
from ucla_agent.games.werewolf.tuning import Tuning

REPO_ROOT = Path(__file__).resolve().parents[2]
TUNING = Tuning.from_file(REPO_ROOT)
RETRACTION = TUNING.wolves.seer_retraction_pattern
NOT_SPARING = dataclasses.replace(TUNING.village, spare_lone_claimant=False)
ABSTAIN = 7
SEATS = range(7)
PRACTICE_SEEDS = range(8)
POLICY_LIMIT_SECONDS = 0.005
TIMED_RUNS = 5


def ledger_of(*states_fields: dict) -> Ledger:
    """A ledger given one state per set of fields, in order (see `werewolf_states.raw_state`)."""
    ledger = Ledger(TUNING.chat)
    for fields in states_fields:
        ledger.update(state(**fields))
    return ledger


def beliefs(p_wolf: dict[int, float], p_seer: dict[int, float] | None = None) -> Beliefs:
    """Beliefs with the given probabilities and 0 for every seat left out."""
    return Beliefs(
        p_wolf={seat: p_wolf.get(seat, 0.0) for seat in SEATS},
        p_seer={seat: (p_seer or {}).get(seat, 0.0) for seat in SEATS},
        layouts=1,
    )


def accusations(target: int, accusers: list[int], first_seq: int = 0) -> list[dict]:
    """One chat from each accuser calling the target suspicious."""
    return [
        chat(first_seq + offset, accuser, f"Player{target} is suspicious") for offset, accuser in enumerate(accusers)
    ]


# -- (1) the vote never abstains ---------------------------------------------------------------------


@pytest.mark.parametrize("seed", PRACTICE_SEEDS)
@pytest.mark.parametrize("seat", [0, 3, 6])
def test_1_the_vote_list_never_contains_abstain_and_names_only_living_others(seed, seat):
    ledger = Ledger(TUNING.chat)
    for game_state in practice_states(seed, seat):
        ledger.update(game_state)
        facts = ledger.facts()
        if facts.sub_phase != DAY_VOTE or facts.eliminated or ledger.private().role == WOLF:
            continue
        votes = village.day_vote(ledger, infer(ledger, TUNING.inference), TUNING.village, RETRACTION)

        assert ABSTAIN not in votes
        assert set(votes) <= set(facts.living) - {seat}


# -- (2) and (3) our own seer results ----------------------------------------------------------------


def test_2_a_seat_our_results_cleared_is_never_in_the_vote_list_even_when_it_is_the_likeliest_wolf():
    ledger = ledger_of({"seat": 0, "role": SEER, "results": [(2, VILLAGER)]})

    votes = village.day_vote(ledger, beliefs({2: 0.9, 4: 0.3, 5: 0.2}), TUNING.village, RETRACTION)

    assert 2 not in votes and votes[0] == 4 and ABSTAIN not in votes


def test_3_a_living_seat_our_results_named_a_wolf_is_the_only_vote_candidate():
    ledger = ledger_of({"seat": 0, "role": SEER, "results": [(2, VILLAGER), (5, WOLF)]})

    assert village.day_vote(ledger, beliefs({4: 0.9, 5: 0.1}), TUNING.village, RETRACTION) == [5]


def test_3_a_found_wolf_that_is_dead_leaves_the_vote_to_p_wolf():
    ledger = ledger_of({"seat": 0, "role": SEER, "results": [(5, WOLF)], "day": 2, "dead": [(1, 5, WOLF, VOTE)]})

    assert village.day_vote(ledger, beliefs({4: 0.9, 1: 0.5}), TUNING.village, RETRACTION)[:2] == [4, 1]


# -- (4) accusations order close candidates ----------------------------------------------------------


def test_4_of_two_close_seats_the_one_three_seats_accused_today_ranks_first():
    ledger = ledger_of({"phase": MESSAGING, "messages": accusations(2, [0, 4, 6])})

    votes = village.day_vote(ledger, beliefs({1: 0.40, 2: 0.35, 4: 0.10, 5: 0.10}), TUNING.village, RETRACTION)

    assert votes[:2] == [2, 1]


def test_4_a_seat_further_than_the_margin_below_the_top_stays_behind_whatever_its_accusers():
    ledger = ledger_of({"phase": MESSAGING, "messages": accusations(2, [0, 4, 6])})

    votes = village.day_vote(ledger, beliefs({1: 0.40, 2: 0.25, 4: 0.10, 5: 0.10}), TUNING.village, RETRACTION)

    assert votes[:2] == [1, 2]


def test_4_accusations_of_an_earlier_window_do_not_count_today():
    yesterday = {"phase": MESSAGING, "messages": accusations(2, [0, 4, 6])}
    today = {"phase": MESSAGING, "day": 2, "messages": []}

    votes = village.day_vote(ledger_of(yesterday, today), beliefs({1: 0.40, 2: 0.35}), TUNING.village, RETRACTION)

    assert votes[:2] == [1, 2]


def test_4_close_seats_with_as_many_accusers_keep_p_wolf_then_seat_order():
    ranked = village.by_suspicion([5, 1, 2, 4], {1: 0.4, 2: 0.4, 4: 0.38, 5: 0.1}, {4: {0}, 2: {0}}, 0.1)

    assert ranked == [2, 4, 1, 5]
    assert village.by_suspicion([], {}, {}, 0.1) == []


def test_4_a_seer_claim_or_a_result_claim_is_no_accusation():
    messages = [chat(0, 4, "I am the seer. Player2 is a wolf."), *accusations(1, [5], first_seq=1)]
    ledger = ledger_of({"phase": MESSAGING, "messages": messages})

    # Seat 4 is a lone claimant, whose named wolf would go first by item (11); this item is about accusations only.
    votes = village.day_vote(ledger, beliefs({1: 0.40, 2: 0.35}), NOT_SPARING, RETRACTION)

    assert votes[:2] == [1, 2]


# -- (5) the investigation ---------------------------------------------------------------------------


def test_5_the_investigation_list_leaves_out_investigated_and_dead_seats_and_puts_a_half_before_a_nine():
    ledger = ledger_of(
        {
            "seat": 0,
            "role": SEER,
            "results": [(1, VILLAGER)],
            "day": 2,
            "sub_phase": NIGHT_SEER,
            "dead": [(1, 6, VILLAGER, NIGHT_KILL)],
        }
    )

    targets = village.night_seer(ledger, beliefs({2: 0.9, 3: 0.5, 4: 0.2, 5: 0.2}))

    assert targets[0] == 3 and targets.index(3) < targets.index(2)
    assert set(targets) == {2, 3, 4, 5}


def test_5_equal_uncertainty_goes_to_a_seer_claimant_then_to_the_seat_that_spoke_most_today():
    messages = [chat(0, 4, "I am the seer."), chat(1, 2, "hello"), chat(2, 2, "again"), chat(3, 5, "hi")]
    ledger = ledger_of({"seat": 0, "role": SEER, "phase": MESSAGING, "messages": messages})

    targets = village.night_seer(ledger, beliefs({seat: 0.4 for seat in range(1, 7)}))

    assert targets == [4, 2, 5, 1, 3, 6]


# -- (6) the seer's reveal ---------------------------------------------------------------------------


def test_6_no_reveal_with_only_villager_results_and_no_accusations():
    ledger = ledger_of({"seat": 0, "role": SEER, "results": [(2, VILLAGER)], "phase": MESSAGING})

    assert not village.should_reveal(ledger, TUNING.village)


def test_6_a_reveal_after_a_wolf_result_on_a_living_seat():
    ledger = ledger_of({"seat": 0, "role": SEER, "results": [(2, VILLAGER), (4, WOLF)], "phase": MESSAGING})

    assert village.should_reveal(ledger, TUNING.village)


def test_6_a_reveal_once_two_different_seats_accused_us_today_and_not_after_one():
    one = ledger_of({"seat": 0, "role": SEER, "phase": MESSAGING, "messages": accusations(0, [3, 3])})
    two = ledger_of({"seat": 0, "role": SEER, "phase": MESSAGING, "messages": accusations(0, [3, 5])})

    assert not village.should_reveal(one, TUNING.village)
    assert village.should_reveal(two, TUNING.village)


def test_6_the_reveal_stays_on_the_following_day_once_made():
    day_one = {"seat": 0, "role": SEER, "phase": MESSAGING, "messages": accusations(0, [3, 5])}
    day_two = {"seat": 0, "role": SEER, "results": [(2, VILLAGER)], "phase": MESSAGING, "day": 2}
    ledger = ledger_of(day_one, day_two)

    assert not village.should_reveal(ledger, TUNING.village)
    assert village.should_reveal(ledger, TUNING.village, revealed=True)


@pytest.mark.parametrize(
    "fields",
    [
        {"seat": 0, "role": VILLAGER, "phase": MESSAGING, "messages": accusations(0, [3, 5])},
        {"seat": 0, "role": SEER, "results": [(4, WOLF)], "dead": [(1, 0, SEER, NIGHT_KILL)], "day": 2},
    ],
    ids=["not the seer", "dead seer"],
)
def test_6_only_the_living_seer_reveals(fields):
    assert not village.should_reveal(ledger_of(fields), TUNING.village, revealed=True)


# -- (7) the reveal's wording ------------------------------------------------------------------------


def test_7_the_reveal_lists_the_claim_and_every_result_in_the_required_wording():
    ledger = ledger_of({"seat": 0, "role": SEER, "results": [(2, VILLAGER), (4, WOLF), (6, VILLAGER)]})

    assert village.reveal_points(ledger) == (
        "I am the seer",
        "Player2 is a villager",
        "Player4 is a wolf",
        "Player6 is a villager",
    )


# -- (8) to (13) a lone seer claimant is spared on the day of its claim ------------------------------

CLAIM = "I am the seer."
TODAY = {"phase": MESSAGING, "day": 2}


def votes_of(ledger: Ledger, p_wolf: dict[int, float], tuning=TUNING.village) -> list[int]:
    return village.day_vote(ledger, beliefs(p_wolf), tuning, RETRACTION)


def test_8_a_villager_s_vote_leaves_out_a_lone_claimant_who_claimed_today_even_as_the_likeliest_wolf():
    ledger = ledger_of({"phase": MESSAGING, "messages": [chat(0, 5, CLAIM)]})

    assert votes_of(ledger, {5: 0.9, 1: 0.4, 2: 0.3}) == [1, 2, 0, 4, 6]
    assert village.lone_claimant(ledger, RETRACTION) == 5


def test_8_the_played_vote_leaves_the_lone_claimant_out_too():
    ledger = ledger_of({"phase": MESSAGING, "messages": [chat(0, 5, CLAIM)]})

    assert decide.candidates(ledger, beliefs({5: 0.9, 1: 0.4}), TUNING)[0] == 1


@pytest.mark.parametrize(
    "days",
    [
        [{**TODAY, "messages": [chat(0, 5, CLAIM), chat(1, 4, CLAIM)]}],
        [{"phase": MESSAGING, "messages": [chat(0, 4, CLAIM)]}, {**TODAY, "messages": [chat(1, 5, CLAIM)]}],
    ],
    ids=["both claimed today", "a living claimant of yesterday"],
)
def test_9_with_two_standing_seer_claimants_neither_is_left_out(days):
    ledger = ledger_of(*days)

    assert votes_of(ledger, {5: 0.9, 4: 0.85, 1: 0.3})[:2] == [5, 4]
    assert village.lone_claimant(ledger, RETRACTION) is None


@pytest.mark.parametrize("revealed, spared", [(VILLAGER, False), (SEER, False), (WOLF, True)])
def test_10_a_lone_claimant_whose_claimed_wolf_died_as_no_wolf_is_not_left_out(revealed, spared):
    yesterday = {"phase": MESSAGING, "messages": [chat(0, 5, "I am the seer. Player2 is a wolf.")]}
    today = {**TODAY, "dead": [(1, 2, revealed, VOTE)], "messages": [chat(1, 5, CLAIM)]}

    votes = votes_of(ledger_of(yesterday, today), {5: 0.9, 1: 0.4})

    assert (5 not in votes) is spared


NAMING_TWO = "I am the seer. Player2 is a wolf."


@pytest.mark.parametrize(
    "p_wolf, accused, first_two",
    [
        ({5: 0.9, 1: 0.45, 2: 0.36}, [], [2, 1]),
        ({5: 0.9, 1: 0.45, 2: 0.36}, [0, 4, 6], [2, 1]),
        ({5: 0.9, 1: 0.45, 2: 0.30}, [], [1, 2]),
    ],
    ids=["within the margin", "within the margin, the top one accused", "beyond the margin"],
)
def test_11_a_lone_claimant_s_named_living_wolf_is_first_when_within_the_close_margin_of_the_top(
    p_wolf, accused, first_two
):
    ledger = ledger_of({"phase": MESSAGING, "messages": [chat(0, 5, NAMING_TWO), *accusations(1, accused, 1)]})

    votes = votes_of(ledger, p_wolf)

    assert votes[:2] == first_two and 5 not in votes
    assert village.claimed_wolves(ledger, 5) == [2]


def test_11_with_the_switch_off_the_named_wolf_is_not_moved():
    ledger = ledger_of({"phase": MESSAGING, "messages": [chat(0, 5, NAMING_TWO)]})

    assert votes_of(ledger, {5: 0.9, 1: 0.45, 2: 0.36}, tuning=NOT_SPARING)[:3] == [5, 1, 2]


def test_11_on_the_inferred_beliefs_a_villager_votes_the_wolf_a_lone_claimant_named():
    ledger = ledger_of({"phase": MESSAGING, "messages": [chat(0, 5, NAMING_TWO)]})

    assert decide.candidates(ledger, infer(ledger, TUNING.inference), TUNING)[0] == 2


def test_11_named_seats_go_first_in_the_order_named_and_only_living_candidates_move():
    named = [chat(0, 5, "I am the seer. Player4 is a wolf."), chat(1, 5, "Player2 is a wolf. Player3 is a wolf.")]
    ledger = ledger_of({"phase": MESSAGING, "messages": named})

    assert village.claimed_wolves(ledger, 5) == [4, 2, 3]
    assert votes_of(ledger, {5: 0.9, 1: 0.4, 2: 0.35, 4: 0.32})[:3] == [4, 2, 1]
    assert village.rallied([1, 6], [4, 2], {1: 0.5, 6: 0.45}, 0.1) == [1, 6]


def test_12_a_lone_claimant_that_is_the_only_candidate_stays_the_only_candidate():
    late_game = {
        "phase": MESSAGING,
        "day": 3,
        "dead": [
            (1, 0, VILLAGER, VOTE),
            (1, 1, VILLAGER, NIGHT_KILL),
            (2, 2, WOLF, VOTE),
            (2, 4, VILLAGER, NIGHT_KILL),
            (3, 6, VILLAGER, NIGHT_KILL),
        ],
        "messages": [chat(0, 5, "I am the seer. Player2 is a wolf.")],
    }
    ledger = ledger_of(late_game)

    assert village.lone_claimant(ledger, RETRACTION) == 5
    assert votes_of(ledger, {5: 0.9}) == [5]


def test_13_with_the_switch_off_the_lone_claimant_stays_ranked_by_p_wolf_as_before():
    ledger = ledger_of({"phase": MESSAGING, "messages": [chat(0, 5, CLAIM)]})
    p_wolf = {5: 0.9, 1: 0.4, 2: 0.3}

    votes = votes_of(ledger, p_wolf, tuning=NOT_SPARING)

    assert votes == [5, 1, 2, 0, 4, 6]
    assert votes == village.by_suspicion([0, 1, 2, 4, 5, 6], beliefs(p_wolf).p_wolf, {}, TUNING.village.close_margin)


def test_a_claimant_that_claimed_only_on_an_earlier_day_is_not_left_out_today():
    ledger = ledger_of({"phase": MESSAGING, "messages": [chat(0, 5, CLAIM)]}, {**TODAY, "messages": []})

    assert votes_of(ledger, {5: 0.9, 1: 0.4})[0] == 5


@pytest.mark.parametrize(
    "messages, left_out, first",
    [
        ([chat(0, 4, CLAIM), chat(1, 5, CLAIM), chat(2, 4, "Sorry, I am not the seer.")], 5, 4),
        ([chat(0, 5, CLAIM), chat(1, 5, "ok I'm not seer, just testing")], None, 5),
    ],
    ids=["the other claimant took its claim back", "the claimant took its own claim back"],
)
def test_a_claim_taken_back_does_not_stand(messages, left_out, first):
    ledger = ledger_of({"phase": MESSAGING, "messages": messages})

    assert village.lone_claimant(ledger, RETRACTION) == left_out
    assert votes_of(ledger, {5: 0.9, 4: 0.8, 1: 0.3})[0] == first


def test_a_dead_claimant_leaves_the_living_one_alone():
    yesterday = {"phase": MESSAGING, "messages": [chat(0, 4, CLAIM)]}
    today = {**TODAY, "dead": [(1, 4, VILLAGER, NIGHT_KILL)], "messages": [chat(1, 5, CLAIM)]}

    assert 5 not in votes_of(ledger_of(yesterday, today), {5: 0.9, 1: 0.4})


QUIET_SEER = {"seat": 0, "role": SEER, "results": [(2, VILLAGER)], "phase": MESSAGING}


@pytest.mark.parametrize("we_claimed, lone", [(True, None), (False, 5)], ids=["we revealed", "we did not"])
def test_our_seer_s_vote_keeps_another_seer_claimant_whether_or_not_we_revealed(we_claimed, lone):
    ours = [chat(0, 0, "I am the seer. Player2 is a villager. Vote Player1.")] if we_claimed else []
    ledger = ledger_of({**QUIET_SEER, "messages": [*ours, chat(1, 5, "I am the seer. Player0 is a wolf.")]})

    assert village.lone_claimant(ledger, RETRACTION) == lone
    assert votes_of(ledger, {5: 0.9, 1: 0.4})[0] == 5


def test_an_unrevealed_seer_keeps_a_lone_claimant_of_today_in_its_vote_list_ranked_as_before():
    ledger = ledger_of({**QUIET_SEER, "messages": [chat(0, 5, "I am the seer. Player1 is a wolf.")]})
    p_wolf = {5: 0.9, 1: 0.85, 4: 0.3}

    votes = votes_of(ledger, p_wolf)

    assert village.lone_claimant(ledger, RETRACTION) == 5
    assert votes == [5, 1, 4, 3, 6] == votes_of(ledger, p_wolf, tuning=NOT_SPARING)


# -- the constraint: pure and fast -------------------------------------------------------------------


def test_each_policy_takes_under_five_milliseconds_on_a_late_ledger():
    ledger = ledger_of(
        {
            "seat": 0,
            "role": SEER,
            "results": [(1, VILLAGER), (2, VILLAGER), (4, VILLAGER)],
            "day": 4,
            "phase": MESSAGING,
            "dead": [(1, 6, VILLAGER, VOTE), (2, 5, VILLAGER, NIGHT_KILL)],
            "votes": [(1, {0: 6, 1: 6, 2: 3, 3: 6, 4: 6, 5: None, 6: 2}, 6)],
            "messages": accusations(0, [1, 2, 3, 4]),
        }
    )
    found = infer(ledger, TUNING.inference)

    for policy in (
        lambda: village.day_vote(ledger, found, TUNING.village, RETRACTION),
        lambda: village.night_seer(ledger, found),
        lambda: village.should_reveal(ledger, TUNING.village),
        lambda: village.reveal_points(ledger),
    ):
        assert min(_seconds(policy) for _ in range(TIMED_RUNS)) < POLICY_LIMIT_SECONDS


def _seconds(call) -> float:
    started = time.perf_counter()
    call()
    return time.perf_counter() - started


def test_the_state_s_platform_phase_does_not_change_the_vote():
    fields = {"seat": 0, "role": SEER, "results": [(2, VILLAGER)]}
    found = beliefs({4: 0.5, 5: 0.4})

    assert votes_of(ledger_of({**fields, "phase": MOVING}), found.p_wolf) == votes_of(
        ledger_of({**fields, "phase": MESSAGING}), found.p_wolf
    )
