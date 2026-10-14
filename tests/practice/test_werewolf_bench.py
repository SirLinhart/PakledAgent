"""The Werewolf practice benchmark: its arms, its runs and results files, the model server it needs, and its report.

The numbered tests are the acceptance items of the benchmark: (1) to (6) for
the benchmark itself, (7) to (11) for the `self` mix, the tuning option, the
seer-claim figures and the baseline's message history. No test reaches a
model server: runs use the `scripted` and `self` mixes with the `baseline`
and `agent-script` arms, and the model server's start, stop and health are
stubs.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import os
import re
import shutil
import socket
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Collection, Iterator

import httpx
import pytest

from altruagent import TERMINATE_MESSAGING, DecisionContext, GameState, SendMessage
from ucla_agent.core import metrics, modelserver, paths
from ucla_agent.core.bench import fisher_exact_p
from ucla_agent.games.werewolf import agent as werewolf_agent
from ucla_agent.games.werewolf.agent import WerewolfAgent
from ucla_agent.games.werewolf.ledger import SEER_CLAIM_TEXT
from ucla_agent.games.werewolf.settings import WerewolfSettings
from ucla_agent.practice.werewolf import arms, bench, bench_models, bench_report, bench_truth, bench_tuning
from ucla_agent.practice.werewolf import play as practice_play
from ucla_agent.practice.werewolf.agent_opponent import AgentOpponent, agent_state
from ucla_agent.practice.werewolf.engine import ABSTAIN, DAY_VOTE, SEER, VILLAGER, WOLF, WerewolfEngine
from ucla_agent.practice.werewolf.mixes import (
    MIXES,
    OPPONENT_FACTORIES,
    RECORD_VOTER,
    SCRIPTED_MIX,
    SELF_MIX,
    WEREWOLF_AGENT,
    build_opponents,
    seat_opponents,
)
from ucla_agent.practice.werewolf.opponents import RecordVoter
from ucla_agent.practice.werewolf.play import play_practice_game
from ucla_agent.practice.werewolf.view import TRANSCRIPT_FIELD

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACKED = WerewolfSettings.from_files(REPO_ROOT)
SIDES = {"villager", "wolf"}
ROLES = ("villager", "seer", "wolf")
SEED_BASE = 1000
GAMES_PER_ROLE = 3
DEALT_COLUMNS = ("seed", "seat", "role")
# The position in a run of the game in which `_RaisesOnDayTwo` raises.
RAISING_GAME = 3
BENCH_SCRIPT = "werewolf_bench.py"
BENCH_SECONDS_LIMIT = 30
LOOPBACK = "127.0.0.1"
# The three roles of the shipped `config/models.toml`, in its order, as the test copy of `stub_models_config` has them.
STUB_ROLES = ("fast", "strong", "small")


@pytest.fixture
def runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary runs folder that `UCLA_AGENT_RUNS_DIR` names, so no test writes under the project's `runs/`."""
    folder = tmp_path / "runs"
    monkeypatch.setenv(paths.RUNS_DIR_ENV, str(folder))
    return folder


def _only_record(runs_folder: Path) -> metrics.Match:
    (folder,) = [match for dated in runs_folder.iterdir() for match in metrics.match_folders(dated)]
    return metrics.load_match(folder)


# -- the arms --------------------------------------------------------------------------------------


class _ComparedRecordVoterSeat(arms.RecordVoterSeat):
    """The baseline seat, noting next to each answer what `RecordVoter` answers to the same state object."""

    answers: list[tuple[Any, Any]] = []

    def choose_action(self, state: GameState, context: DecisionContext) -> int:
        action = super().choose_action(state, context)
        self.answers.append((action, RecordVoter().choose_action(state.raw)))
        return action

    def choose_message(self, state: GameState, context: DecisionContext) -> Any:
        message = super().choose_message(state, context)
        expected = RecordVoter().choose_message(state.raw)
        self.answers.append((getattr(message, "content", message), getattr(expected, "content", expected)))
        return message


def test_the_baseline_arm_seats_record_voter_and_records_each_of_its_moves(runs):
    arm = arms.make_arm(arms.BASELINE, REPO_ROOT)
    _ComparedRecordVoterSeat.answers = []

    result = play_practice_game(1003, _ComparedRecordVoterSeat, SCRIPTED_MIX, "seer", seat=1003 % 7)
    actions = _only_record(runs).data_of("action")

    assert isinstance(arm.factory(), arms.RecordVoterSeat)
    assert arm.settings == {"decision_maker": arms.RECORD_VOTER_NAME}
    assert arm.model_roles == frozenset()
    assert result.winner in SIDES
    assert _ComparedRecordVoterSeat.answers
    assert all(answer == expected for answer, expected in _ComparedRecordVoterSeat.answers)
    assert actions and all(isinstance(action["compute_ms"], float) for action in actions)


@pytest.fixture
def watched_engines(monkeypatch: pytest.MonkeyPatch) -> list[WerewolfEngine]:
    """Every engine the practice games of the test deal, in the order dealt."""
    engines: list[WerewolfEngine] = []

    class Watched(WerewolfEngine):
        def __init__(self, *arguments: Any, **options: Any) -> None:
            super().__init__(*arguments, **options)
            engines.append(self)

    monkeypatch.setattr(practice_play, "WerewolfEngine", Watched)
    return engines


class _VoteNotingRecordVoterSeat(arms.RecordVoterSeat):
    """The baseline seat, noting every day-vote state it is handed."""

    vote_states: list[GameState] = []

    def choose_action(self, state: GameState, context: DecisionContext) -> int:
        if state.raw["game_state"]["phase"] == DAY_VOTE:
            self.vote_states.append(state)
        return super().choose_action(state, context)


# A table of six record voters, our villager in seat 4: the record_voter seer found the wolf in seat 1 on night 1
# and claims it on day 1, before our seat votes, while the vote state alone would have our seat vote seat 0.
FOLLOW_SEED, FOLLOW_SEAT, CLAIMED_WOLF = 11, 4, 1


def test_11_a_baseline_villager_votes_the_living_wolf_a_record_voter_seer_claimed_that_day(runs, watched_engines):
    _VoteNotingRecordVoterSeat.vote_states = []

    play_practice_game(FOLLOW_SEED, _VoteNotingRecordVoterSeat, [RECORD_VOTER] * 6, VILLAGER, seat=FOLLOW_SEAT)
    (engine,) = watched_engines
    day_one_claims = [
        (message.sender, message.content)
        for message in engine.messages
        if message.day == 1 and SEER_CLAIM_TEXT.search(message.content)
    ]
    day_one_vote = _VoteNotingRecordVoterSeat.vote_states[0]

    assert day_one_claims == [(engine.roles.index(SEER), f"I am the seer. Player{CLAIMED_WOLF} is a wolf.")]
    assert engine.roles[CLAIMED_WOLF] == WOLF
    assert day_one_vote.raw["game_state"]["day"] == 1 and day_one_vote.raw["new_messages"] == []
    assert engine.vote_history[0].votes[FOLLOW_SEAT] == CLAIMED_WOLF
    assert RecordVoter().choose_action(day_one_vote.raw) != CLAIMED_WOLF


def test_the_baseline_seat_hands_record_voter_each_message_it_has_read_once_in_seq_order():
    seat = arms.RecordVoterSeat()
    first, second = (
        {"seq": seq, "sender": sender, "recipients": [], "type": "chat", "content": f"chat {seq}"}
        for seq, sender in ((2, 1), (5, 3))
    )
    states = [{"new_messages": [first]}, {"new_messages": [second, first]}, {"new_messages": []}]

    views = [seat._view(GameState.from_mcp_state(state)) for state in states]

    assert [view[TRANSCRIPT_FIELD] for view in views] == [[first], [first, second], [first, second]]
    assert views[-1]["new_messages"] == []


def test_the_agent_script_arm_is_the_werewolf_agent_with_both_model_switches_off():
    arm = arms.make_arm(arms.AGENT_SCRIPT, REPO_ROOT)

    assert isinstance(arm.factory(), WerewolfAgent)
    assert arm.settings == {
        "decision_maker": arms.AGENT_NAME,
        "model_choices": False,
        "model_messages": False,
        "choice_deadline_ms": TRACKED.choice_deadline_ms,
        "message_deadline_ms": TRACKED.message_deadline_ms,
    }
    assert arm.model_roles == frozenset()


def test_the_agent_model_arm_plays_the_tracked_settings_and_needs_the_roles_its_chains_name():
    arm = arms.make_arm(arms.AGENT_MODEL, REPO_ROOT)

    assert arm.settings["model_choices"] is TRACKED.model_choices
    assert arm.settings["model_messages"] is TRACKED.model_messages
    assert arm.model_roles == frozenset({"strong"})


def test_the_agent_model_arm_is_refused_while_claude_is_switched_on(tmp_path):
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    agent_toml = tmp_path / "config" / "agent.toml"
    text = agent_toml.read_text(encoding="utf-8")
    claude_table = text.index("\n[claude]\n")
    switched_on = text[:claude_table] + text[claude_table:].replace("enabled = false", "enabled = true", 1)
    agent_toml.write_text(switched_on, encoding="utf-8")

    with pytest.raises(arms.ArmRefused, match=r"\[claude\] enabled"):
        arms.make_arm(arms.AGENT_MODEL, tmp_path)
    assert arms.make_arm(arms.AGENT_SCRIPT, tmp_path).name == arms.AGENT_SCRIPT


def test_a_name_that_is_not_an_arm_is_refused():
    with pytest.raises(ValueError, match="no arm is named"):
        arms.make_arm("agent-claude", REPO_ROOT)


# -- a run and its results files -------------------------------------------------------------------


def _baseline_arm() -> arms.Arm:
    return arms.make_arm(arms.BASELINE, REPO_ROOT)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as results_file:
        return list(csv.DictReader(results_file))


class _RaisesOnDayTwo(arms.RecordVoterSeat):
    """The baseline seat, which raises out of its first call on day 2 in one game of a run.

    That game is the first seer game of seed base 1000 (seed 1000, seat 6),
    where our seat is still alive and asked on day 2.
    """

    games_made = 0

    def __init__(self) -> None:
        super().__init__()
        type(self).games_made += 1
        self._raises = type(self).games_made == RAISING_GAME + 1

    def choose_action(self, state: GameState, context: DecisionContext) -> int:
        self._raise_on_day_two(state)
        return super().choose_action(state, context)

    def choose_message(self, state: GameState, context: DecisionContext) -> Any:
        self._raise_on_day_two(state)
        return super().choose_message(state, context)

    def _raise_on_day_two(self, state: GameState) -> None:
        if self._raises and state.raw["game_state"]["day"] == 2:
            raise RuntimeError("raised on day 2")


def test_the_planned_games_are_n_per_role_with_seeds_from_the_seed_base_and_the_seat_from_the_seed():
    games = bench.planned_games(GAMES_PER_ROLE, SEED_BASE)

    assert [(game.role, game.seed, game.seat) for game in games] == [
        (role, seed, seed % 7) for role in ROLES for seed in (1000, 1001, 1002)
    ]


def test_the_label_folder_is_under_the_runs_folder_the_variable_names(runs):
    assert bench.label_folder("baseline-30") == runs.absolute() / "practice" / "werewolf" / "baseline-30"


def test_a_baseline_run_writes_one_row_per_game_its_records_and_the_summary(runs):
    folder = bench.label_folder("baseline")
    played: list[bench.PlayedGame] = []

    rows = bench.run_bench(
        folder, _baseline_arm(), SCRIPTED_MIX, bench.planned_games(GAMES_PER_ROLE, SEED_BASE), on_game=played.append
    )
    written = _read_csv(folder / bench.RESULTS_FILE_NAME)
    summary = json.loads((folder / bench.SUMMARY_FILE_NAME).read_text(encoding="utf-8"))
    record_folders = [match for dated in folder.iterdir() if dated.is_dir() for match in metrics.match_folders(dated)]

    assert len(rows) == len(written) == len(played) == 9
    assert tuple(written[0]) == bench.COLUMNS
    assert [row["role"] for row in written].count("villager") == GAMES_PER_ROLE
    assert {row["arm"] for row in written} == {arms.BASELINE}
    assert {row["mix"] for row in written} == {SCRIPTED_MIX}
    assert {row["winner"] for row in written} <= SIDES
    assert all(row["fallbacks"] == row["model_calls"] == row["unhandled_errors"] == "0" for row in written)
    assert all(float(row["decision_p95_ms"]) >= 0 and int(row["days"]) >= 1 for row in written)
    assert [game.error for game in played] == [None] * 9
    assert len(record_folders) == 9
    assert summary["arm"] == arms.BASELINE and summary["mix"] == SCRIPTED_MIX
    assert summary["opponents"] == list(MIXES[SCRIPTED_MIX])
    assert summary["settings"] == {"decision_maker": arms.RECORD_VOTER_NAME}
    assert summary["model_roles_started"] == []
    assert summary["seeds"] == [1000, 1001, 1002]
    assert {role: counts["games"] for role, counts in summary["roles"].items()} == dict.fromkeys(ROLES, 3)
    assert summary["all"]["wins"] == sum(int(row["our_side_won"]) for row in written)
    assert os.environ[paths.RUNS_DIR_ENV] == str(runs)


def test_2_two_arms_of_one_seed_base_play_rows_with_identical_seed_seat_and_role(runs):
    games = bench.planned_games(GAMES_PER_ROLE, SEED_BASE)
    baseline = bench.run_bench(bench.label_folder("a"), _baseline_arm(), SCRIPTED_MIX, games)
    agent_script = bench.run_bench(
        bench.label_folder("b"), arms.make_arm(arms.AGENT_SCRIPT, REPO_ROOT), SCRIPTED_MIX, games
    )

    def dealt(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        return [tuple(row[column] for column in DEALT_COLUMNS) for row in rows]

    assert dealt(baseline) == dealt(agent_script)
    assert {row["arm"] for row in agent_script} == {arms.AGENT_SCRIPT}
    assert all(row["model_calls"] == 0 and row["unhandled_errors"] == 0 for row in agent_script)


def test_3_a_game_whose_decision_maker_raises_on_day_2_is_one_unhandled_error_and_the_run_goes_on(runs):
    _RaisesOnDayTwo.games_made = 0
    arm = arms.Arm(arms.BASELINE, _RaisesOnDayTwo, {"decision_maker": "raises on day 2"})
    played: list[bench.PlayedGame] = []

    rows = bench.run_bench(
        bench.label_folder("raises"),
        arm,
        SCRIPTED_MIX,
        bench.planned_games(GAMES_PER_ROLE, SEED_BASE),
        on_game=played.append,
    )
    raised, others = rows[RAISING_GAME], rows[:RAISING_GAME] + rows[RAISING_GAME + 1 :]

    assert (raised["role"], raised["seed"]) == ("seer", 1000)
    assert raised["unhandled_errors"] == 1
    assert (raised["winner"], raised["our_side_won"], raised["days"]) == (bench.NO_WINNER, 0, None)
    assert "raised on day 2" in played[RAISING_GAME].error
    assert len(others) == 8
    assert all(row["winner"] in SIDES and row["unhandled_errors"] == 0 for row in others)


def _match(events: list[tuple[str, dict[str, Any]]]) -> metrics.Match:
    recorded = [{"kind": kind, "data": data} for kind, data in events]
    return metrics.Match(Path("match"), [metrics.Attempt(1, {}, recorded, 0)])


# -- the engine-truth columns ----------------------------------------------------------------------

SEER_CLAIM = "I am the seer. Player0 is a wolf."
# Our seat in the first game of each role at seed base 1000.
CLAIMING_SEED, CLAIMING_SEAT = 1000, 6


class _ClaimingSeer:
    """Our seat as a seer that claims once in every discussion window and takes the first legal action."""

    def __init__(self) -> None:
        self._claimed_days: set[int] = set()

    def choose_action(self, state: GameState, _context: DecisionContext) -> Any:
        return state.legal_actions[0]

    def choose_message(self, state: GameState, _context: DecisionContext) -> Any:
        day = state.raw["game_state"]["day"]
        if day in self._claimed_days:
            return TERMINATE_MESSAGING
        self._claimed_days.add(day)
        return SendMessage(SEER_CLAIM)


class _VotesOutOurSeat:
    """An opponent that votes our seat whenever it may and otherwise takes the first legal action, and never chats."""

    def choose_action(self, view: dict[str, Any]) -> int:
        legal = [int(action["action_id"]) for action in view["legal_actions"]["actions"]]
        return CLAIMING_SEAT if view["game_state"]["phase"] == DAY_VOTE and CLAIMING_SEAT in legal else legal[0]

    def choose_message(self, _view: dict[str, Any]) -> Any:
        return TERMINATE_MESSAGING


def test_8_a_seer_who_claims_on_day_1_and_is_voted_out_that_day_has_claim_day_1_and_voted_out_1(runs):
    arm = arms.Arm("claiming-seer", _ClaimingSeer, {"decision_maker": "claims seer every day"})
    (game,) = [game for game in bench.planned_games(1, CLAIMING_SEED) if game.role == SEER]

    (row,) = bench.run_bench(
        bench.label_folder("claim"),
        arm,
        SCRIPTED_MIX,
        [game],
        opponent_factories=dict.fromkeys(OPPONENT_FACTORIES, _VotesOutOurSeat),
    )
    written = _read_csv(bench.label_folder("claim") / bench.RESULTS_FILE_NAME)

    assert (row["seat"], row["role"]) == (CLAIMING_SEAT, SEER)
    assert (row["seer_claim_day"], row["seer_voted_out_on_claim_day"]) == (1, 1)
    assert row["village_won"] == int(row["winner"] == VILLAGER) and row["unhandled_errors"] == 0
    assert (written[0]["seer_claim_day"], written[0]["seer_voted_out_on_claim_day"]) == ("1", "1")


# The hand-played day: a game of seed 5 with the seer forced on seat 2.
TRUTH_SEED, TRUTH_SEER = 5, 2


def _day_one(chats: list[tuple[int, str, list[int]]], voted_out: int) -> WerewolfEngine:
    """A game played by hand through its first day: the seer looks at the first seat it may on night 1, the `chats`
    are sent, every seat terminates, and every living seat but `voted_out` votes `voted_out`, which abstains."""
    engine = WerewolfEngine(TRUTH_SEED, SEER, TRUTH_SEER)
    engine.play(TRUTH_SEER, engine.legal_actions(TRUTH_SEER)[0])
    for sender, content, recipients in chats:
        engine.send_chat(sender, content, recipients)
    for seat in engine.living_seats():
        engine.terminate(seat)
    # The day's voters act in seat order, and the vote is resolved after the last of them.
    for voter in engine.living_seats():
        engine.play(voter, ABSTAIN if voter == voted_out else voted_out)
    return engine


def _wolf_and_villager(engine: WerewolfEngine) -> tuple[int, int]:
    """The lowest wolf seat and the lowest villager seat of the deal."""
    return engine.roles.index(WOLF), engine.roles.index(VILLAGER)


@pytest.mark.parametrize(
    ("claims", "voted_out_is_the_seer", "expected"),
    [
        pytest.param(["seer"], True, (1, 1), id="lone-claimant-voted-out"),
        pytest.param(["seer"], False, (1, 0), id="lone-claimant-spared"),
        pytest.param(["wolf", "seer"], True, (1, 0), id="wolf-claimed-first"),
        pytest.param(["seer", "wolf"], True, (1, 0), id="wolf-claimed-the-same-day"),
        pytest.param(["seer-privately"], True, (None, 0), id="private-claim"),
        pytest.param(["wolf"], True, (None, 0), id="only-a-wolf-claimed"),
        pytest.param([], True, (None, 0), id="nobody-claimed"),
    ],
)
def test_the_truth_columns_count_only_the_real_seer_s_public_claim_and_only_as_a_lone_claimant(
    claims, voted_out_is_the_seer, expected
):
    wolf, villager = _wolf_and_villager(WerewolfEngine(TRUTH_SEED, SEER, TRUTH_SEER))
    chats = {
        "seer": (TRUTH_SEER, f"I'm the seer. Player{wolf} is a wolf.", []),
        "seer-privately": (TRUTH_SEER, "I am the seer, keep it quiet.", [villager]),
        "wolf": (wolf, f"I am the seer. Player{villager} is a wolf.", []),
    }

    engine = _day_one([chats[claim] for claim in claims], TRUTH_SEER if voted_out_is_the_seer else villager)
    columns = bench_truth.truth_columns(engine)

    assert (columns["seer_claim_day"], columns["seer_voted_out_on_claim_day"]) == expected
    assert columns["village_won"] == 0 and not engine.is_over


def test_village_won_is_1_exactly_when_the_village_side_won(runs):
    arm = arms.make_arm(arms.AGENT_SCRIPT, REPO_ROOT)

    rows = bench.run_bench(bench.label_folder("village"), arm, SELF_MIX, bench.planned_games(2, SEED_BASE))

    assert {row["winner"] for row in rows} == SIDES
    assert all(row["village_won"] == int(row["winner"] == VILLAGER) for row in rows)


def test_a_game_that_raised_has_no_claim_day_and_neither_voted_out_nor_village_win(runs):
    _RaisesOnDayTwo.games_made = RAISING_GAME
    arm = arms.Arm(arms.BASELINE, _RaisesOnDayTwo, {"decision_maker": "raises on day 2"})

    (row,) = bench.run_bench(bench.label_folder("raised"), arm, SCRIPTED_MIX, [bench.planned_games(1, SEED_BASE)[1]])

    assert row["unhandled_errors"] == 1
    assert (row["seer_claim_day"], row["seer_voted_out_on_claim_day"], row["village_won"]) == (None, 0, 0)


def test_a_results_file_written_before_the_engine_truth_columns_is_still_read(tmp_path):
    header = ",".join(bench.EARLIER_COLUMNS)
    (tmp_path / bench.RESULTS_FILE_NAME).write_text(
        f"{header}\n1000,6,villager,scripted,baseline,wolf,0,2,0,0,0.1,0\n", encoding="utf-8"
    )

    (row,) = bench.read_results(tmp_path)

    assert bench.COLUMNS == (*bench.EARLIER_COLUMNS, "seer_claim_day", "seer_voted_out_on_claim_day", "village_won")
    assert tuple(row) == bench.EARLIER_COLUMNS and row["winner"] == "wolf"


def test_the_record_figures_count_fallbacks_model_calls_unhandled_errors_and_the_decision_p95():
    actions = [("action", {"compute_ms": float(ms)}) for ms in range(1, 21)]
    record = _match(
        [
            *actions,
            ("decision", {"source": "local:strong"}),
            ("text_decision", {"source": "local:strong"}),
            ("text_decision", {"source": "local:strong"}),
            ("fallback", {"reason": "timeout"}),
            ("error", {"type": "unhandled"}),
            ("error", {"type": "handled"}),
        ]
    )

    figures = bench.record_figures(record, raised=False)

    assert figures == {"fallbacks": 1, "model_calls": 3, "decision_p95_ms": 19.0, "unhandled_errors": 1}
    assert bench.record_figures(record, raised=True)["unhandled_errors"] == 2
    assert bench.record_figures(None, raised=True) == {
        "fallbacks": 0,
        "model_calls": 0,
        "decision_p95_ms": None,
        "unhandled_errors": 1,
    }


def test_the_results_read_back_as_written_and_tally_per_role_and_in_all(tmp_path):
    rows = [
        {**dict.fromkeys(bench.COLUMNS, 0), "role": role, "our_side_won": won, "decision_p95_ms": None}
        for role, won in (("villager", 1), ("villager", 0), ("wolf", 1))
    ]
    rows[2]["unhandled_errors"] = 1

    bench.write_results(tmp_path, rows)
    read = bench.read_results(tmp_path)
    tallies = bench.tally(read)

    assert [row["decision_p95_ms"] for row in read] == ["", "", ""]
    assert tallies["villager"] == bench.Tally(games=2, wins=1, unhandled_errors=0)
    assert tallies["seer"] == bench.Tally(games=0, wins=0, unhandled_errors=0)
    assert tallies["seer"].win_rate is None
    assert tallies["all"] == bench.Tally(games=3, wins=2, unhandled_errors=1)
    assert tallies["villager"].win_rate == 0.5


def test_a_folder_without_results_or_with_other_columns_is_refused(tmp_path):
    with pytest.raises(ValueError, match="holds no results.csv"):
        bench.read_results(tmp_path)
    (tmp_path / bench.RESULTS_FILE_NAME).write_text("seed,role\n1000,wolf\n", encoding="utf-8")
    with pytest.raises(ValueError, match="does not have the columns"):
        bench.read_results(tmp_path)


def test_the_summary_records_the_model_server_roles_started_for_the_run(runs):
    folder = bench.label_folder("started")

    bench.run_bench(
        folder,
        _baseline_arm(),
        SCRIPTED_MIX,
        bench.planned_games(1, SEED_BASE)[:1],
        model_roles_started=("strong", "fast"),
    )
    summary = json.loads((folder / bench.SUMMARY_FILE_NAME).read_text(encoding="utf-8"))

    assert summary["model_roles_started"] == ["fast", "strong"]


def test_without_the_variable_set_the_records_still_go_to_the_label_folder_and_it_is_unset_after(tmp_path, monkeypatch):
    monkeypatch.delenv(paths.RUNS_DIR_ENV, raising=False)
    folder = tmp_path / "label"

    bench.run_bench(folder, _baseline_arm(), SCRIPTED_MIX, bench.planned_games(1, SEED_BASE)[:1])

    assert paths.RUNS_DIR_ENV not in os.environ
    assert [match.name for dated in folder.iterdir() if dated.is_dir() for match in metrics.match_folders(dated)]


# -- the self mix ----------------------------------------------------------------------------------


@pytest.fixture
def agent_errors(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every failure any `WerewolfAgent` of the test catches and plays on from, its copies' included, whose own
    records are written nowhere."""
    caught: list[str] = []
    monkeypatch.setattr(werewolf_agent, "_log_error", lambda where, error, *_kind: caught.append(f"{where}: {error!r}"))
    return caught


def _records_by_seat(folder: Path) -> list[tuple[int, metrics.Match]]:
    """Each match record of a label folder with the seat it was played from, the number its folder name ends in."""
    folders = [match for dated in folder.iterdir() if dated.is_dir() for match in metrics.match_folders(dated)]
    return [(int(match.name.rsplit("__", 1)[1]), metrics.load_match(match)) for match in folders]


def _discussion_days(record: metrics.Match) -> list[int]:
    """The days whose discussion window our seat saw, from the states of its record."""
    days = [state["state"]["game_state"]["day"] for state in record.data_of("state") if state["phase"] == "messaging"]
    return sorted(set(days))


def test_7_a_self_run_of_2_games_per_role_plays_6_games_without_error_and_records_only_our_seat_s_events(
    runs, bench_tool, agent_errors
):
    exit_code = bench_tool.main(["--label", "self", "--arm", "agent-script", "--mix", "self", "--games-per-role", "2"])
    folder = runs / "practice" / "werewolf" / "self"
    rows = _read_csv(folder / bench.RESULTS_FILE_NAME)
    summary = json.loads((folder / bench.SUMMARY_FILE_NAME).read_text(encoding="utf-8"))
    recorded = _records_by_seat(folder)

    assert exit_code == bench_tool.EXIT_PLAYED
    assert len(rows) == 6 and {row["mix"] for row in rows} == {SELF_MIX}
    assert all(row["winner"] in SIDES and row["unhandled_errors"] == row["model_calls"] == "0" for row in rows)
    assert agent_errors == []
    assert summary["opponents"] == [WEREWOLF_AGENT] * 6
    assert sorted(seat for seat, _record in recorded) == sorted(int(row["seat"]) for row in rows)
    for seat, record in recorded:
        ledgers = record.data_of("werewolf_ledger")
        assert {state["state"]["your_position"] for state in record.data_of("state")} == {seat}
        assert [(ledger["window"], ledger["day"]) for ledger in ledgers] == list(
            enumerate(_discussion_days(record), start=1)
        )
        assert {ledger["facts"]["seat"] for ledger in ledgers} == {seat}


def test_the_self_mix_seats_six_agents_with_models_off_that_share_no_ledger_chat_or_chain():
    models_on = dataclasses.replace(TRACKED, model_choices=True, model_messages=True)
    factories = {**OPPONENT_FACTORIES, WEREWOLF_AGENT: lambda: AgentOpponent(models_on)}

    opponents = build_opponents(seat_opponents(MIXES[SELF_MIX], SEED_BASE, 0), factories)
    agents = [opponent._agent for opponent in opponents.values()]
    parts = [(agent, agent._ledger, agent._chat, agent._choice_source, agent._message_source) for agent in agents]

    assert sorted(opponents) == [1, 2, 3, 4, 5, 6]
    assert all(isinstance(opponent, AgentOpponent) for opponent in opponents.values())
    assert len({id(part) for seat_parts in parts for part in seat_parts}) == 6 * 5
    assert {(agent._settings.model_choices, agent._settings.model_messages) for agent in agents} == {(False, False)}
    assert {agent._settings.tuning for agent in agents} == {TRACKED.tuning}


def test_a_copy_reads_its_seat_s_whole_transcript_as_the_new_messages_of_its_state():
    transcript = [
        {"seq": seq, "sender": sender, "recipients": [], "type": "chat", "content": f"chat {seq}"}
        for seq, sender in ((0, 3), (4, 5))
    ]
    view = {"session_id": "self-test", "phase": "moving", "new_messages": [], TRANSCRIPT_FIELD: transcript}

    state = agent_state(view)

    assert [(message.index, message.sender, message.content) for message in state.new_messages] == [
        (0, 3, "chat 0"),
        (4, 5, "chat 4"),
    ]
    assert state.raw[TRANSCRIPT_FIELD] == transcript


# -- the model server a model run needs ------------------------------------------------------------


class _ModelServerStub:
    """The included model server's start and stop, and its endpoint's `/health`, without a model or a socket.

    `ready_before` are the roles a server already running reports ready;
    `ready_after_start` those the started server reports ready. Nothing
    answers on the endpoint until a server runs.
    """

    def __init__(
        self,
        ready_before: set[str] | None = None,
        ready_after_start: frozenset[str] | set[str] = frozenset(),
        start_error: Exception | None = None,
    ) -> None:
        self.ready_before = ready_before
        self.ready_after_start = ready_after_start
        self.start_error = start_error
        self.started = self.stopped = 0
        self.started_roles: list[tuple[str, ...]] = []
        self.health_requests = 0
        self.transport = httpx.MockTransport(self._answer)

    def start(self, roles: Collection[str]) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started += 1
        self.started_roles.append(tuple(roles))

    def stop(self) -> None:
        self.stopped += 1

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.health_requests += 1
        assert request.url.path == "/health"
        ready = self.ready_after_start if self.started else self.ready_before
        if ready is None:
            raise httpx.ConnectError("connection refused", request=request)
        roles = {role: {"state": "ready" if role in ready else "down"} for role in ("fast", "strong", "small")}
        return httpx.Response(200, json={"roles": roles})


@pytest.fixture
def model_server(monkeypatch: pytest.MonkeyPatch):
    """Makes a `_ModelServerStub` and puts its start and stop in place of the included model server's."""

    def install(**states: Any) -> _ModelServerStub:
        stub = _ModelServerStub(**states)
        monkeypatch.setattr(bench_models.modelserver, "start", stub.start)
        monkeypatch.setattr(bench_models.modelserver, "stop", stub.stop)
        return stub

    return install


def test_a_run_that_needs_no_model_asks_nothing_and_starts_nothing(model_server, capsys):
    stub = model_server(ready_before=set())

    with bench_models.serving(set(), REPO_ROOT, transport=stub.transport) as started:
        pass

    assert started == ()
    assert (stub.health_requests, stub.started, stub.stopped) == (0, 0, 0)
    assert capsys.readouterr().out == ""


def test_a_server_that_already_serves_the_roles_is_used_as_it_is(model_server, capsys):
    stub = model_server(ready_before={"strong", "fast"})

    with bench_models.serving({"strong", "fast"}, REPO_ROOT, transport=stub.transport) as started:
        played = True

    assert played and started == () and (stub.started, stub.stopped) == (0, 0)
    assert capsys.readouterr().out == (
        "Model server: http://127.0.0.1:11500 already serves fast, strong ready; no role started for the run.\n"
    )


def test_without_a_server_the_included_one_is_started_with_only_the_needed_roles_and_stopped_after_it(
    model_server, capsys
):
    stub = model_server(ready_after_start={"strong", "fast", "small"})

    with bench_models.serving({"strong"}, REPO_ROOT, transport=stub.transport) as started:
        assert (stub.started, stub.stopped) == (1, 0)

    assert started == ("strong",) and stub.started_roles == [("strong",)]
    assert (stub.started, stub.stopped) == (1, 1)
    assert capsys.readouterr().out == "Model server: starting only the roles the run needs: strong.\n"


def test_a_needed_role_still_down_after_the_start_stops_the_run_before_it_plays(model_server):
    stub = model_server(ready_after_start={"strong"})
    played = False

    with pytest.raises(bench_models.ModelsUnavailable, match="has fast down"):
        with bench_models.serving({"strong", "fast"}, REPO_ROOT, transport=stub.transport):
            played = True

    assert not played and (stub.started, stub.stopped) == (1, 1)


def test_a_server_that_cannot_be_started_stops_the_run_naming_the_roles_and_why(model_server):
    stub = model_server(start_error=OSError("port 11500 is in use"))

    with pytest.raises(bench_models.ModelsUnavailable, match="serves fast ready.*port 11500 is in use"):
        with bench_models.serving({"fast"}, REPO_ROOT, transport=stub.transport):
            pass


def test_the_started_server_is_stopped_also_when_the_run_raises(model_server):
    stub = model_server(ready_after_start={"fast"})

    with pytest.raises(RuntimeError):
        with bench_models.serving({"fast"}, REPO_ROOT, transport=stub.transport):
            raise RuntimeError("a run that fails")

    assert stub.stopped == 1


@pytest.mark.parametrize(
    "answer",
    [httpx.Response(200, text="not json"), httpx.Response(200, json=["roles"]), httpx.Response(200, json={"roles": 1})],
    ids=["not json", "not an object", "roles not an object"],
)
def test_an_endpoint_that_does_not_answer_as_the_model_server_has_no_role_ready(answer):
    transport = httpx.MockTransport(lambda _request: answer)

    assert bench_models.not_ready({"strong", "fast"}, "http://127.0.0.1:11500", transport) == ["fast", "strong"]


# -- the command line ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bench_tool(load_script):
    return load_script(BENCH_SCRIPT)


def test_1_a_baseline_run_of_3_games_per_role_writes_9_rows_3_per_role_in_under_30_seconds(runs, bench_tool, capsys):
    started = time.perf_counter()

    exit_code = bench_tool.main(
        ["--label", "baseline-3", "--arm", "baseline", "--mix", "scripted", "--games-per-role", "3"]
    )
    seconds = time.perf_counter() - started
    folder = runs / "practice" / "werewolf" / "baseline-3"
    rows = _read_csv(folder / bench.RESULTS_FILE_NAME)
    printed = capsys.readouterr().out.splitlines()

    assert exit_code == bench_tool.EXIT_PLAYED
    assert len(rows) == 9
    assert {role: [row["role"] for row in rows].count(role) for role in ROLES} == dict.fromkeys(ROLES, 3)
    assert [row["seed"] for row in rows[:3]] == ["1000", "1001", "1002"]
    assert seconds < BENCH_SECONDS_LIMIT
    assert len(printed) == 11
    assert printed[0].startswith("villager seed 1000 seat 6 (")
    assert printed[-2:] == [f"Wrote {folder / bench.RESULTS_FILE_NAME}", f"Wrote {folder / bench.SUMMARY_FILE_NAME}"]


def test_a_label_that_already_has_results_is_refused_and_left_alone(runs, bench_tool, capsys):
    folder = runs / "practice" / "werewolf" / "taken"
    folder.mkdir(parents=True)
    (folder / bench.RESULTS_FILE_NAME).write_text("kept\n", encoding="utf-8")

    exit_code = bench_tool.main(["--label", "taken", "--arm", "baseline", "--mix", "scripted", "--games-per-role", "1"])

    assert exit_code == bench_tool.EXIT_REFUSED
    assert "give the run a new label" in capsys.readouterr().err
    assert (folder / bench.RESULTS_FILE_NAME).read_text(encoding="utf-8") == "kept\n"


@pytest.mark.parametrize(
    ("arm", "mix", "roles"),
    [
        ("agent-model", "scripted", {"strong"}),
        ("baseline", "mixed", {"fast"}),
        ("agent-model", "mixed", {"strong", "fast"}),
        ("agent-script", "scripted", set()),
        ("agent-script", "self", set()),
        ("baseline", "self", set()),
    ],
)
def test_a_run_holds_the_model_roles_its_arm_and_mix_ask_and_stops_when_they_cannot_be_made_ready(
    runs, bench_tool, monkeypatch, capsys, arm, mix, roles
):
    asked: list[set[str]] = []

    @contextmanager
    def unavailable(needed: frozenset[str], _root: Path) -> Iterator[None]:
        asked.append(set(needed))
        raise bench_models.ModelsUnavailable("no model server for the run")
        yield

    monkeypatch.setattr(bench_models, "serving", unavailable)

    exit_code = bench_tool.main(["--label", "models", "--arm", arm, "--mix", mix, "--games-per-role", "1"])

    assert asked == [roles]
    assert exit_code == bench_tool.EXIT_REFUSED
    assert "no model server for the run" in capsys.readouterr().err
    assert not (runs / "practice" / "werewolf" / "models").exists()


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((LOOPBACK, port))
        except OSError:
            return False
    return True


@pytest.fixture
def stub_model_project(
    tmp_path: Path, runs: Path, monkeypatch: pytest.MonkeyPatch, stub_models_config: Callable[..., Any]
) -> Iterator[Any]:
    """The project root as a copy of the tracked `config/`, whose `config/models.toml` is the test copy with the
    shipped roles fast, strong and small run by the stub llama-server on the free ports of `model_ports`; gives
    those ports. The model server is stopped after."""
    root = tmp_path / "project"
    shutil.copytree(REPO_ROOT / "config", root / "config")
    ports = stub_models_config(root)
    monkeypatch.setenv(paths.ROOT_ENV, str(root))
    yield ports
    modelserver.stop()


@pytest.mark.parametrize(
    ("arm", "mix", "started"),
    [
        ("agent-script", "mixed", ["fast"]),
        ("baseline", "mixed", ["fast"]),
        ("agent-model", "mixed", ["fast", "strong"]),
        ("agent-model", "scripted", ["strong"]),
    ],
)
def test_6_a_run_starts_only_the_roles_it_needs_prints_them_and_passes_them_to_the_results_and_never_starts_small(
    stub_model_project, bench_tool, monkeypatch, capsys, arm, mix, started
):
    seen: list[dict[str, Any]] = []
    backend_ports = {role: stub_model_project.backend_port(index) for index, role in enumerate(STUB_ROLES)}

    def observed_run(_folder: Path, _arm: arms.Arm, _mix: str, _games: Any, **options: Any) -> list[dict[str, Any]]:
        with httpx.Client(trust_env=False) as client:
            health = client.get(stub_model_project.endpoint_url + "/health").json()
        live = sorted(role for role, port in backend_ports.items() if not _port_is_free(port))
        seen.append({"health": health, "live": live, "recorded": list(options["model_roles_started"])})
        return []

    monkeypatch.setattr(bench, "run_bench", observed_run)

    exit_code = bench_tool.main(["--label", arm, "--arm", arm, "--mix", mix, "--games-per-role", "1"])
    printed = capsys.readouterr().out.splitlines()

    assert exit_code == bench_tool.EXIT_PLAYED
    assert seen == [
        {"health": {"roles": dict.fromkeys(started, {"state": "ready"})}, "live": started, "recorded": started}
    ]
    assert printed[0] == f"Model server: starting only the roles the run needs: {', '.join(started)}."
    assert [line for line in printed if line.startswith("Model server: role ")] == [
        f"Model server: role {role} is ready on port {backend_ports[role]}." for role in started
    ]
    assert all(_port_is_free(port) for port in backend_ports.values())


def test_the_agent_model_arm_is_refused_on_the_command_line_while_claude_is_switched_on(
    tmp_path, runs, bench_tool, monkeypatch, capsys
):
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    agent_toml = tmp_path / "config" / "agent.toml"
    text = agent_toml.read_text(encoding="utf-8")
    claude_table = text.index("\n[claude]\n")
    agent_toml.write_text(
        text[:claude_table] + text[claude_table:].replace("enabled = false", "enabled = true", 1), encoding="utf-8"
    )
    monkeypatch.setenv(paths.ROOT_ENV, str(tmp_path))

    exit_code = bench_tool.main(
        ["--label", "claude", "--arm", "agent-model", "--mix", "scripted", "--games-per-role", "1"]
    )

    assert exit_code == bench_tool.EXIT_REFUSED
    assert "never asks Claude" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--label", "x", "--arm", "baseline", "--mix", "scripted", "--games-per-role", "0"],
        ["--label", "x", "--arm", "claude", "--mix", "scripted", "--games-per-role", "1"],
        ["--label", "x", "--arm", "baseline", "--mix", "tournament", "--games-per-role", "1"],
        ["--arm", "baseline", "--mix", "scripted", "--games-per-role", "1"],
    ],
    ids=["no games", "unknown arm", "unknown mix", "no label"],
)
def test_a_wrong_command_line_exits_with_code_2(bench_tool, arguments):
    with pytest.raises(SystemExit) as exited:
        bench_tool.main(arguments)

    assert exited.value.code == bench_tool.EXIT_REFUSED


def test_a_game_that_raised_is_printed_with_what_it_raised(bench_tool):
    played = bench.PlayedGame({"role": "seer", "seed": 1000, "seat": 6}, "RuntimeError: raised on day 2")

    assert bench_tool.game_line(played, 0.25) == "seer seed 1000 seat 6 (0.25 s): raised RuntimeError: raised on day 2"


# -- another tuning file for the run ---------------------------------------------------------------

TRACKED_TUNING = REPO_ROOT / "config" / "werewolf" / "tuning.toml"
# A seat that claims the seer is taken for a wolf far more readily than the tracked file has it.
SUSPICIOUS_CLAIMS = ("seer_claim_if_wolf", "20.0")
# Enough self-play games that a changed seer-claim weight shows in the votes or chats of some of them.
TUNING_GAMES_PER_ROLE = 10


def _tuning_with(folder: Path, key: str, value: str) -> Path:
    """A copy of the tracked tuning file with `key` set to `value`, written into `folder`."""
    text, replaced = re.subn(rf"(?m)^{key} = .*$", f"{key} = {value}", TRACKED_TUNING.read_text(encoding="utf-8"))
    assert replaced == 1
    path = folder / f"tuning-{key}.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _played_table(engine: WerewolfEngine) -> tuple[Any, ...]:
    """What every seat of a finished game decided: each day's votes and every chat, with its sender."""
    votes = [(vote.day, sorted(vote.votes.items())) for vote in engine.vote_history]
    return votes, [(message.sender, message.content) for message in engine.messages if message.type == "chat"]


def test_9_another_tuning_file_changes_the_self_play_games_decisions_and_is_recorded_with_its_hash(
    tmp_path, runs, bench_tool, watched_engines
):
    alternative = _tuning_with(tmp_path, *SUSPICIOUS_CLAIMS)
    run = ["--mix", "self", "--arm", "agent-script", "--games-per-role", str(TUNING_GAMES_PER_ROLE)]

    tracked_exit = bench_tool.main(["--label", "tracked", *run])
    tracked_games = [_played_table(engine) for engine in watched_engines]
    watched_engines.clear()
    alternative_exit = bench_tool.main(["--label", "alternative", *run, "--tuning", str(alternative)])
    alternative_games = [_played_table(engine) for engine in watched_engines]
    summaries = {
        label: json.loads(
            (runs / "practice" / "werewolf" / label / bench.SUMMARY_FILE_NAME).read_text(encoding="utf-8")
        )
        for label in ("tracked", "alternative")
    }

    assert tracked_exit == alternative_exit == bench_tool.EXIT_PLAYED
    assert len(tracked_games) == len(alternative_games) == 3 * TUNING_GAMES_PER_ROLE
    assert any(tracked != changed for tracked, changed in zip(tracked_games, alternative_games))
    assert summaries["alternative"]["tuning"] == {
        "path": str(alternative),
        "sha256": hashlib.sha256(alternative.read_bytes()).hexdigest(),
    }
    assert summaries["tracked"]["tuning"] == {
        "path": "config/werewolf/tuning.toml",
        "sha256": hashlib.sha256(TRACKED_TUNING.read_bytes()).hexdigest(),
    }


def test_the_tuning_given_is_played_by_our_seat_s_agent_and_by_every_self_copy(tmp_path):
    given = bench_tuning.run_tuning(_tuning_with(tmp_path, *SUSPICIOUS_CLAIMS), REPO_ROOT)

    ours = arms.make_arm(arms.AGENT_SCRIPT, REPO_ROOT, given.tuning).factory()
    copy = arms.opponent_factories(REPO_ROOT, given.tuning)[WEREWOLF_AGENT]()

    assert given.tuning.inference.seer_claim_if_wolf == 20.0 != TRACKED.tuning.inference.seer_claim_if_wolf
    assert ours._settings.tuning == copy._agent._settings.tuning == given.tuning
    assert (copy._agent._settings.model_choices, copy._agent._settings.model_messages) == (False, False)


def test_without_a_tuning_file_the_run_plays_and_records_the_tracked_one():
    tracked = bench_tuning.run_tuning(None, REPO_ROOT)

    assert tracked.tuning == TRACKED.tuning
    assert tracked.described() == {
        "path": "config/werewolf/tuning.toml",
        "sha256": hashlib.sha256(TRACKED_TUNING.read_bytes()).hexdigest(),
    }


@pytest.mark.parametrize(
    ("value", "reason"),
    [("0", "seer_claim_if_wolf in \\[inference\\] must be a number above 0"), (None, "cannot be read")],
    ids=["a weight of 0", "no such file"],
)
def test_a_tuning_file_that_cannot_be_played_is_refused_with_exit_code_2_naming_it(
    tmp_path, runs, bench_tool, capsys, value, reason
):
    path = tmp_path / "missing.toml" if value is None else _tuning_with(tmp_path, "seer_claim_if_wolf", value)

    exit_code = bench_tool.main(
        ["--label", "refused", "--arm", "agent-script", "--mix", "self", "--games-per-role", "1", "--tuning", str(path)]
    )
    error = capsys.readouterr().err

    assert exit_code == bench_tool.EXIT_REFUSED
    assert str(path) in error and re.search(reason, error)
    assert not (runs / "practice" / "werewolf" / "refused").exists()


# -- the report ------------------------------------------------------------------------------------


# A game's engine truth: the real seer's claim day, whether that day's vote eliminated it, whether the village won.
NO_CLAIM_WOLF_WIN = (None, 0, 0)


def _label(
    folder: Path,
    wins: dict[str, int],
    *,
    unhandled_errors: int = 0,
    arm: str = arms.BASELINE,
    truth: dict[str, list[tuple[int | None, int, int]]] | None = None,
) -> Path:
    """Write a label folder's `results.csv`: 3 games per role, the first `wins[role]` of them won; `truth` gives
    each role's games their engine truth, by default no seer claim and a wolf win."""
    truth = truth or dict.fromkeys(ROLES, [NO_CLAIM_WOLF_WIN] * GAMES_PER_ROLE)
    rows = [
        {
            **dict.fromkeys(bench.COLUMNS, 0),
            "seed": SEED_BASE + index,
            "seat": (SEED_BASE + index) % 7,
            "role": role,
            "mix": SCRIPTED_MIX,
            "arm": arm,
            "winner": "villager" if village_won else "wolf",
            "our_side_won": int(index < wins[role]),
            "days": 2,
            "decision_p95_ms": 0.1,
            "seer_claim_day": claim_day,
            "seer_voted_out_on_claim_day": voted_out,
            "village_won": village_won,
        }
        for role in ROLES
        for index, (claim_day, voted_out, village_won) in enumerate(truth[role])
    ]
    rows[-1]["unhandled_errors"] = unhandled_errors
    bench.write_results(folder, rows)
    return folder


A_WINS = {"villager": 1, "seer": 0, "wolf": 2}
B_HIGHER_IN_EVERY_ROLE = {"villager": 2, "seer": 1, "wolf": 3}


@pytest.fixture(scope="module")
def report(load_script):
    return load_script("report.py")


def _files_under(folder: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


def test_werewolf_bench_prints_the_arm_the_mix_and_each_role_s_games_wins_and_win_rate(tmp_path, run_script):
    label = _label(tmp_path / "label", {"villager": 1, "seer": 0, "wolf": 3})
    before = _files_under(tmp_path)

    completed = run_script(tmp_path, "report.py", "--werewolf-bench", str(label))

    assert (completed.returncode, completed.stderr) == (0, "")
    assert completed.stdout.splitlines() == [
        "arm,baseline",
        "mix,scripted",
        "role,games,wins,win_rate,unhandled_errors",
        "villager,3,1,0.33,0",
        "seer,3,0,0,0",
        "wolf,3,3,1,0",
        "all,9,4,0.44,0",
        "role,seer_claimed,seer_voted_out_on_claim_day,village_wins",
        "villager,0,0,0",
        "seer,0,0,0",
        "wolf,0,0,0",
        "all,0,0,0",
    ]
    assert _files_under(tmp_path) == before


# Per role, three games of (seer claim day, seer voted out on it, village won).
CLAIMS_AND_VILLAGE_WINS = {
    "villager": [(1, 1, 0), (None, 0, 1), (2, 0, 1)],
    "seer": [(1, 1, 0), (1, 0, 0), (None, 0, 1)],
    "wolf": [(None, 0, 1), (None, 0, 0), (3, 1, 0)],
}
SEER_CLAIM_LINES = [
    "role,seer_claimed,seer_voted_out_on_claim_day,village_wins",
    "villager,2,1,2",
    "seer,2,1,1",
    "wolf,1,1,1",
    "all,5,3,4",
]


def test_10_werewolf_bench_prints_per_role_and_in_all_the_seer_claims_the_seer_voted_out_and_the_village_wins(
    tmp_path, run_script
):
    label = _label(tmp_path / "label", A_WINS, truth=CLAIMS_AND_VILLAGE_WINS)

    completed = run_script(tmp_path, "report.py", "--werewolf-bench", str(label))

    assert (completed.returncode, completed.stderr) == (0, "")
    assert completed.stdout.splitlines()[-5:] == SEER_CLAIM_LINES


def test_10_werewolf_bench_compare_prints_the_seer_claim_figures_of_both_labels(tmp_path, report, capsys):
    label_a = _label(tmp_path / "a", A_WINS, truth=CLAIMS_AND_VILLAGE_WINS)
    label_b = _label(tmp_path / "b", B_HIGHER_IN_EVERY_ROLE)

    exit_code = report.main(["--werewolf-bench-compare", str(label_a), str(label_b)])
    printed = capsys.readouterr().out.splitlines()
    sections = [index for index, line in enumerate(printed) if line == SEER_CLAIM_LINES[0]]

    assert exit_code == 0 and len(sections) == 2
    assert printed[sections[0] : sections[0] + 5] == SEER_CLAIM_LINES
    assert printed[sections[1] + 1 : sections[1] + 5] == ["villager,0,0,0", "seer,0,0,0", "wolf,0,0,0", "all,0,0,0"]
    assert sections[0] < printed.index(f"B,{label_b}") < sections[1]


def test_a_label_written_before_the_engine_truth_columns_shows_no_seer_claim_figures(tmp_path, run_script):
    header = ",".join(bench.EARLIER_COLUMNS)
    rows = [f"{1000 + index},{index},{role},scripted,baseline,wolf,0,2,0,0,0.1,0" for index, role in enumerate(ROLES)]
    (tmp_path / bench.RESULTS_FILE_NAME).write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")

    completed = run_script(tmp_path, "report.py", "--werewolf-bench", str(tmp_path))

    assert (completed.returncode, completed.stderr) == (0, "")
    assert completed.stdout.splitlines()[-5:] == [
        SEER_CLAIM_LINES[0],
        "villager,-,-,-",
        "seer,-,-,-",
        "wolf,-,-,-",
        "all,-,-,-",
    ]


def test_4_werewolf_bench_compare_prints_each_role_s_counts_and_fisher_p_and_goal_met_when_b_is_higher_in_all(
    tmp_path, report, capsys
):
    label_a = _label(tmp_path / "a", A_WINS)
    label_b = _label(tmp_path / "b", B_HIGHER_IN_EVERY_ROLE, arm=arms.AGENT_SCRIPT)

    exit_code = report.main(["--werewolf-bench-compare", str(label_a), str(label_b)])
    printed = capsys.readouterr().out.splitlines()
    comparison = printed[printed.index("role,games_a,wins_a,games_b,wins_b,fisher_p_two_sided") + 1 :]

    assert exit_code == 0
    assert printed[0] == f"A,{label_a}" and f"B,{label_b}" in printed
    assert printed.count("role,games,wins,win_rate,unhandled_errors") == 2
    assert "arm,agent-script" in printed
    assert comparison[:3] == [
        f"{role},3,{A_WINS[role]},3,{B_HIGHER_IN_EVERY_ROLE[role]},"
        f"{fisher_exact_p(A_WINS[role], 3, B_HIGHER_IN_EVERY_ROLE[role], 3):.3f}"
        for role in ROLES
    ]
    assert comparison[:3] == ["villager,3,1,3,2,1.000", "seer,3,0,3,1,1.000", "wolf,3,2,3,3,1.000"]
    assert printed[-1] == "goal: met"


@pytest.mark.parametrize(
    ("b_wins", "unhandled_errors"),
    [
        ({"villager": 2, "seer": 0, "wolf": 3}, 0),
        ({"villager": 0, "seer": 1, "wolf": 3}, 0),
        (B_HIGHER_IN_EVERY_ROLE, 1),
    ],
    ids=["equal in one role", "lower in one role", "higher in all with an unhandled error"],
)
def test_4_werewolf_bench_compare_says_goal_not_met_unless_b_is_higher_in_every_role_with_no_unhandled_error(
    tmp_path, report, capsys, b_wins, unhandled_errors
):
    label_a = _label(tmp_path / "a", A_WINS)
    label_b = _label(tmp_path / "b", b_wins, unhandled_errors=unhandled_errors)

    exit_code = report.main(["--werewolf-bench-compare", str(label_a), str(label_b)])

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines()[-1] == "goal: not met"


def test_a_fisher_p_well_below_one_is_shown_to_three_decimals():
    rows_a = [{"role": role, "our_side_won": 0, "unhandled_errors": 0, "arm": "a", "mix": "m"} for role in ROLES] * 10
    rows_b = [{"role": role, "our_side_won": 1, "unhandled_errors": 0, "arm": "b", "mix": "m"} for role in ROLES] * 10

    lines = bench_report.compare_lines("A", rows_a, "B", rows_b)

    assert f"villager,10,0,10,10,{fisher_exact_p(0, 10, 10, 10):.3f}" in lines
    assert "villager,10,0,10,10,0.000" in lines
    assert lines[-1] == "goal: met"


def test_a_role_without_games_in_either_label_has_no_fisher_p_and_does_not_meet_the_goal():
    rows_a = [{"role": role, "our_side_won": 0, "unhandled_errors": 0, "arm": "a", "mix": "m"} for role in ROLES[:2]]
    rows_b = [{"role": role, "our_side_won": 1, "unhandled_errors": 0, "arm": "b", "mix": "m"} for role in ROLES[:2]]

    lines = bench_report.compare_lines("A", rows_a, "B", rows_b)

    assert "wolf,0,0,0,0,-" in lines
    assert lines[-1] == "goal: not met"


def test_werewolf_bench_of_a_folder_without_results_ends_with_exit_code_2(tmp_path, report, capsys):
    exit_code = report.main(["--werewolf-bench", str(tmp_path / "missing")])

    captured = capsys.readouterr()
    assert (exit_code, captured.out) == (2, "")
    assert "is not a Werewolf practice benchmark label folder" in captured.err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--werewolf-bench", "a", "runs/20261016"],
        ["--werewolf-bench", "a", "--bench", "b"],
        ["--werewolf-bench", "a", "--werewolf-bench-compare", "a", "b"],
    ],
    ids=["with a path", "with bench", "with werewolf-bench-compare"],
)
def test_a_werewolf_bench_mode_with_another_mode_is_a_usage_error(report, arguments):
    with pytest.raises(SystemExit) as stopped:
        report.main(arguments)

    assert stopped.value.code == 2
