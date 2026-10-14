"""The Pokémon practice benchmark: the named first drafter, its runs and results files, our seat, its command line
and its report.

The numbered tests are the acceptance items of the benchmark: (1) a run of 4
matches against `first` writes 4 rows; (2) a match that raises is a row with
one unhandled error and the run goes on; (3) the report prints a label's
figures and the comparison the Fisher p; (5) a `self` run of 2 matches has
both seats played by `PokemonAgent`, ours through `run_game`, with no
unhandled error and none of the opponent's events in our record. Every match
is played on the fake battle helper; no test reaches Showdown or a model
server.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest

from altruagent import DecisionContext, GameState
from ucla_agent.core import metrics, paths, record_files, records
from ucla_agent.core.bench import fisher_exact_p
from ucla_agent.core.config import ConfigError
from ucla_agent.core.decide import ChoiceRequest, LocalModelSource, Option
from ucla_agent.core.modelserver import settings as server_settings
from ucla_agent.games.pokemon.agent import PokemonAgent
from ucla_agent.games.pokemon.settings import PokemonSettings
from ucla_agent.practice.pokemon import bench, bench_agent, bench_report, bridge
from ucla_agent.practice.pokemon import play as practice_play
from ucla_agent.practice.werewolf import bench_models
from ucla_agent.practice.pokemon.cards import CardCatalog, load_catalog
from ucla_agent.practice.pokemon.draft import PracticeDraft
from ucla_agent.practice.pokemon.match import DEFAULT_SEAT_IDS, PokemonPracticeMatch
from ucla_agent.practice.pokemon.opponents import FIRST, OPPONENT_FACTORIES, SELF, PolicyAgent, models_off
from ucla_agent.practice.pokemon.play import play_practice_match

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_HELPER = [sys.executable, str(REPO_ROOT / "tests" / "stubs" / "fake_pokemon_sim.py")]
SHOWDOWN_BATTLE = REPO_ROOT / "tests" / "fixtures" / "pokemon_practice" / "showdown_battle_3_turns.jsonl"
TURN_LINE_PREFIX = "|turn|"
SCRIPTED = models_off(PokemonSettings.from_files(REPO_ROOT))
DRAFT_SEEDS = range(5000, 5020)
SEED_BASE = 5000
OUR_AGENT_ID = DEFAULT_SEAT_IDS[0]
# Four matches on the fake helper take well under a second; the limit only catches a run that hangs.
BENCH_SECONDS_LIMIT = 30


@pytest.fixture(scope="module")
def catalog() -> CardCatalog:
    return load_catalog()


@pytest.fixture
def runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary runs folder that `UCLA_AGENT_RUNS_DIR` names, so no test writes under the project's `runs/`."""
    folder = tmp_path / "runs"
    monkeypatch.setenv(paths.RUNS_DIR_ENV, str(folder))
    return folder


def scripted_agent() -> PokemonAgent:
    """Our seat's agent with its model switched off: the script's best in every phase."""
    return PokemonAgent(settings=SCRIPTED)


def _records(runs_folder: Path) -> list[metrics.Match]:
    return [metrics.load_match(match) for dated in runs_folder.iterdir() for match in metrics.match_folders(dated)]


# -- the first drafter -----------------------------------------------------------------------------


@pytest.mark.parametrize("first", [0, 1])
def test_a_named_first_drafter_opens_the_snake_order_and_leaves_the_seed_s_pool_as_it_was(catalog, first):
    for seed in DRAFT_SEEDS:
        drawn = PracticeDraft(seed, catalog, DEFAULT_SEAT_IDS)
        named = PracticeDraft(seed, catalog, DEFAULT_SEAT_IDS, first_drafter=DEFAULT_SEAT_IDS[first])

        assert named.first_drafter == named.current_seat == DEFAULT_SEAT_IDS[first]
        assert named.order[:4] == tuple(DEFAULT_SEAT_IDS[(first + step) % 2] for step in (0, 1, 1, 0))
        assert [card.card_id for card in named.pool] == [card.card_id for card in drawn.pool]


def test_without_a_named_first_drafter_the_seed_draws_both_seats_over_twenty_seeds(catalog):
    drawn = {PracticeDraft(seed, catalog, DEFAULT_SEAT_IDS).first_drafter for seed in DRAFT_SEEDS}

    assert drawn == set(DEFAULT_SEAT_IDS)


def test_the_match_names_the_first_drafter_by_seat(catalog):
    match = PokemonPracticeMatch(5000, catalog, helper=None, first_drafter=1)

    assert match.draft.first_drafter == DEFAULT_SEAT_IDS[1]


@pytest.mark.parametrize("first", [0, 1])
def test_a_practice_match_with_a_named_first_drafter_shows_our_first_draft_state_after_that_many_picks(runs, first):
    result = play_practice_match(5001, scripted_agent, FIRST, helper_command=FAKE_HELPER, first_drafter=first)

    [record] = _records(runs)
    first_state = record.data_of("state")[0]["state"]
    assert first_state["phase"] == "draft"
    assert len(first_state["observation"]["picks"]) == first
    assert result.winner is not None and not result.unhandled_error


# -- the run ---------------------------------------------------------------------------------------


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as results_file:
        return list(csv.DictReader(results_file))


def _run(folder: Path, opponent: str, games: int, **options: Any) -> list[dict[str, Any]]:
    planned = bench.planned_matches(games, SEED_BASE)
    return bench.run_bench(folder, scripted_agent, opponent, planned, helper_command=FAKE_HELPER, **options)


def test_the_planned_matches_take_seeds_from_the_seed_base_and_alternate_our_first_pick_by_seed():
    planned = bench.planned_matches(4, 5000) + bench.planned_matches(2, 7)

    assert [(match.seed, match.first_drafter) for match in planned] == [
        (5000, 0),
        (5001, 1),
        (5002, 0),
        (5003, 1),
        (7, 1),
        (8, 0),
    ]


def test_the_label_folder_is_under_the_runs_folder_the_variable_names(runs):
    assert bench.label_folder("pk") == runs / "practice" / "pokemon" / "pk"


def test_1_a_run_of_4_matches_against_first_writes_4_rows_their_records_and_the_summary(runs):
    folder = bench.label_folder("first-4")

    rows = _run(folder, FIRST, 4, settings={"model_choices": False}, model_roles_started=())

    written = _read_csv(folder / bench.RESULTS_FILE_NAME)
    summary = json.loads((folder / bench.SUMMARY_FILE_NAME).read_text(encoding="utf-8"))
    assert len(rows) == len(written) == 4
    assert tuple(written[0]) == bench.COLUMNS
    assert [row["seed"] for row in written] == ["5000", "5001", "5002", "5003"]
    assert {row["opponent"] for row in written} == {"first"}
    assert all(int(row["turns"]) > 0 and int(row["decisions"]) > 6 for row in written)
    assert all(row["unhandled_errors"] == "0" and row["fallbacks"] == "0" for row in written)
    assert all(float(row[column]) >= 0 for row in written for column in bench.MAX_DECISION_COLUMNS.values())
    assert len(_records(folder)) == 4
    assert summary["seeds"] == [5000, 5001, 5002, 5003]
    assert summary["our_first_pick_seeds"] == [5000, 5002]
    assert summary["games"] == 4 and summary["wins"] == sum(row["our_won"] for row in rows)
    assert summary["settings"] == {"model_choices": False} and summary["model_roles_started"] == []
    assert set(summary["slowest_decision_ms"]) == {"draft", "team_preview", "moving"}


def test_two_runs_of_one_seed_base_draft_from_the_same_pools(runs):
    for label in ("a", "b"):
        _run(bench.label_folder(label), FIRST, 2)

    def pools(label: str) -> list[list[str]]:
        drafted = []
        for record in _records(bench.label_folder(label)):
            draft = next(state["state"] for state in record.data_of("state") if state["phase"] == "draft")
            picked = [pick["card_id"] for pick in draft["observation"]["picks"]]
            drafted.append(sorted([card["card_id"] for card in draft["observation"]["available_cards"]] + picked))
        return sorted(drafted)

    assert pools("a") == pools("b")
    assert len(pools("a")) == 2 and pools("a")[0] != pools("a")[1]


class _RaisingOpponent:
    def choose(self, _state: Any) -> Any:
        raise RuntimeError("the opponent broke")


def test_2_a_match_that_raises_is_one_unhandled_error_with_no_turns_and_the_run_goes_on(runs):
    folder = bench.label_folder("raises")
    raising_seed = SEED_BASE + 1

    def first_or_raising(seed: int | None = None) -> Any:
        return _RaisingOpponent() if seed == raising_seed else OPPONENT_FACTORIES[FIRST](seed)

    played: list[bench.PlayedMatch] = []

    rows = _run(folder, FIRST, 3, opponent_factories={FIRST: first_or_raising}, on_match=played.append)

    assert [row["unhandled_errors"] for row in rows] == [0, 1, 0]
    assert rows[1]["turns"] is None and rows[1]["our_won"] == 0
    assert (played[0].error, played[2].error) == (None, None)
    assert "the opponent broke" in played[1].error
    assert [row["unhandled_errors"] for row in _read_csv(folder / bench.RESULTS_FILE_NAME)] == ["0", "1", "0"]


class _RaisingAgent:
    def choose_action(self, _state: GameState, _context: DecisionContext) -> Any:
        raise RuntimeError("the decision-maker broke")


def test_a_decision_maker_that_raises_ends_its_match_with_one_unhandled_error(runs):
    planned = bench.planned_matches(1, SEED_BASE)

    [row] = bench.run_bench(
        bench.label_folder("agent-raises"), _RaisingAgent, FIRST, planned, helper_command=FAKE_HELPER
    )

    assert (row["unhandled_errors"], row["our_won"], row["decisions"]) == (1, 0, 0)


class _DegradedAgent:
    """Plays the first legal options; on its first decision it logs what our agent logs for an observation with 2
    keys read by their defaults, for a template-only fallback, for a model answer not used and for a failure of its
    own."""

    def __init__(self) -> None:
        self._policy = PolicyAgent(FIRST)
        self._logged = False

    def choose_action(self, state: GameState, context: DecisionContext) -> Any:
        records.log_state(state.raw)
        if not self._logged:
            self._logged = True
            records.log("pokemon_decision", {"phase": "draft", "parse_degradations": 2, "source": "script"})
            records.log("error", {"where": "choose_action", "type": "unexpected_state", "message": "no observation"})
            records.log("fallback", {"intended_source": "local:strong", "reason": "timeout", "used": "script"})
            records.log("error", {"where": "choose_action", "type": "unhandled", "message": "logged"})
        action = self._policy.choose_action(state, context)
        records.log_action(state.state_version, action, 1.5)
        return action


def test_fallbacks_count_parse_degradations_template_only_fallbacks_and_model_fallbacks(runs):
    planned = bench.planned_matches(1, SEED_BASE)

    [row] = bench.run_bench(bench.label_folder("degraded"), _DegradedAgent, FIRST, planned, helper_command=FAKE_HELPER)

    assert (row["parse_degradations"], row["fallbacks"], row["unhandled_errors"]) == (3, 4, 1)
    assert row["decisions"] > 6 and row["decision_p95_ms"] == 1.5


def _event(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"kind": kind, "data": data}


def _record(events: list[dict[str, Any]]) -> metrics.Match:
    return metrics.Match(Path("match"), [metrics.Attempt(1, {}, events, 0)])


def _decided(phase: str, compute_ms: float, degradations: int = 0) -> list[dict[str, Any]]:
    return [
        _event("state", {"phase": phase, "state_version": 0}),
        _event("pokemon_decision", {"phase": phase, "parse_degradations": degradations}),
        _event("action", {"compute_ms": compute_ms}),
    ]


def test_the_record_figures_time_each_decision_under_the_phase_of_the_state_before_it():
    events = [
        *_decided("draft", 3.0),
        *_decided("draft", 9.0, degradations=1),
        *_decided("team_preview", 40.0),
        _event("state", {"phase": "moving"}),
        _event("error", {"type": "unexpected_state"}),
        _event("action", {"compute_ms": 2.0}),
        _event("fallback", {"reason": "timeout"}),
        *_decided("moving", 20.0, degradations=2),
        _event("error", {"type": "handled"}),
    ]

    figures = bench.record_figures(_record(events))

    assert figures == {
        "decisions": 5,
        "fallbacks": 5,
        "parse_degradations": 4,
        "decision_p95_ms": 40.0,
        "max_decision_ms_draft": 9.0,
        "max_decision_ms_team_preview": 40.0,
        "max_decision_ms_moving": 20.0,
        "unhandled_errors": 0,
    }


def test_without_a_record_every_count_is_0_and_no_decision_is_timed():
    figures = bench.record_figures(None)

    assert figures["decisions"] == figures["fallbacks"] == figures["unhandled_errors"] == 0
    assert figures["decision_p95_ms"] is None and figures["max_decision_ms_moving"] is None


def test_5_a_self_run_of_2_matches_has_both_seats_played_by_the_agent_and_only_our_events_in_our_records(
    runs, monkeypatch
):
    deciding_seats: list[int | None] = []
    runner_seats: list[str] = []
    choose_action = PokemonAgent.choose_action
    run_game = practice_play.run_game

    def watched_choose_action(agent: PokemonAgent, state: GameState, context: DecisionContext) -> Any:
        deciding_seats.append(context.seat_position)
        return choose_action(agent, state, context)

    def watched_run_game(session: Any, context: DecisionContext, agent: Any) -> Any:
        runner_seats.append(context.agent_id)
        return run_game(session, context, agent)

    monkeypatch.setattr(PokemonAgent, "choose_action", watched_choose_action)
    monkeypatch.setattr(practice_play, "run_game", watched_run_game)
    folder = bench.label_folder("self-2")

    rows = _run(folder, SELF, 2)

    our_records = _records(folder)
    assert [row["unhandled_errors"] for row in rows] == [0, 0]
    assert all(row["turns"] > 0 for row in rows)
    assert runner_seats == [OUR_AGENT_ID, OUR_AGENT_ID]
    assert set(deciding_seats) == {0, 1}
    assert len(our_records) == 2
    for record in our_records:
        actors = {state["state"]["current_actor"]["agent_id"] for state in record.data_of("state")}
        assert actors == {OUR_AGENT_ID}
        assert len(record.data_of("state")) == len(record.data_of("pokemon_decision")) == len(record.data_of("action"))
    assert sum(row["decisions"] for row in rows) == deciding_seats.count(0)


def test_the_results_read_back_as_written_and_tally_and_give_the_slowest_decision_of_each_phase(tmp_path):
    rows = [
        {**dict.fromkeys(bench.COLUMNS, 0), "seed": 1, "opponent": "random", "our_won": 1, "fallbacks": 2},
        {
            **dict.fromkeys(bench.COLUMNS, 0),
            "seed": 2,
            "opponent": "random",
            "parse_degradations": 1,
            "fallbacks": 1,
            "unhandled_errors": 1,
            "max_decision_ms_draft": 12.5,
            "max_decision_ms_moving": None,
        },
    ]
    bench.write_results(tmp_path, rows)

    read = bench.read_results(tmp_path)

    assert bench.tally(read) == bench.tally(rows) == bench.Tally(2, 1, 3, 1, 1)
    assert bench.tally(read).win_rate == 0.5 and bench.tally([]).win_rate is None
    assert bench.slowest_decisions(read) == bench.slowest_decisions(rows)
    assert bench.slowest_decisions(read) == {"draft": 12.5, "team_preview": 0.0, "moving": 0.0}
    assert bench.slowest_decisions([]) == {"draft": None, "team_preview": None, "moving": None}


def test_a_folder_without_results_or_with_other_columns_is_refused(tmp_path):
    with pytest.raises(ValueError, match="holds no results.csv"):
        bench.read_results(tmp_path)

    (tmp_path / bench.RESULTS_FILE_NAME).write_text("seed,role\n1,seer\n", encoding="utf-8")
    with pytest.raises(ValueError, match="does not have the columns"):
        bench.read_results(tmp_path)


def test_without_the_variable_set_the_records_still_go_to_the_label_folder_and_it_is_unset_after(tmp_path, monkeypatch):
    monkeypatch.delenv(paths.RUNS_DIR_ENV, raising=False)
    folder = tmp_path / "label"

    bench.run_bench(folder, scripted_agent, FIRST, bench.planned_matches(1, SEED_BASE), helper_command=FAKE_HELPER)

    assert len(_records(folder)) == 1
    assert paths.RUNS_DIR_ENV not in os.environ


# -- the turn count --------------------------------------------------------------------------------


class ReplayedShowdown:
    """The battle helper's answers of one real battle, given back in order whatever is sent.

    The answers are `tools/pokemon_sim/sim.js`'s own, recorded from a local
    Showdown battle of seed 5000 against `first` with models off: each side
    brings 4 of its 6, and our Lunala and Calyrex-Shadow knock out two
    Pokémon a turn.
    """

    def __init__(self, path: Path) -> None:
        self.answers = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self._unsent = iter(self.answers)

    def start(self, _battle_format: str, _seed: str, _players: Any) -> bridge.HelperUpdate:
        return bridge.parse_answer(next(self._unsent))

    def choose(self, _side: str, _choice: str) -> bridge.HelperUpdate:
        return bridge.parse_answer(next(self._unsent))


def test_turns_is_the_last_turn_line_of_a_real_showdown_battle_won_during_its_third_turn(catalog):
    showdown = ReplayedShowdown(SHOWDOWN_BATTLE)
    match = PokemonPracticeMatch(SEED_BASE, catalog, showdown, first_drafter=0)
    rule = OPPONENT_FACTORIES[FIRST](None)
    our_turn_decisions = 0

    while not match.finished:
        # The opponent's seat answers first when both have a decision, as it did in the recorded battle.
        seat = next(seat for seat in (1, 0) if match.has_decision(seat))
        state = match.state(seat, "turn-count")
        our_turn_decisions += int(seat == 0 and state["phase"] == "moving")
        match.play(seat, state["state_version"], action=rule.choose(state))

    our_log = [line for answer in showdown.answers for line in answer["sides"]["p1"]["log"]]
    assert [line for line in our_log if line.startswith(TURN_LINE_PREFIX)] == ["|turn|1", "|turn|2", "|turn|3"]
    assert our_log.index("|win|practice-p1") > our_log.index("|turn|3")
    assert match.turns == our_turn_decisions == 3
    assert (match.winner, match.termination_reason) == (0, "completed")


# -- our seat --------------------------------------------------------------------------------------


TRACKED_CHAIN = '["local:strong"]'
BRIEFING_LINE = re.compile(r"^model_briefing = (true|false)$", re.MULTILINE)
DECIDES_LINE = re.compile(r"^model_decides = (true|false)$", re.MULTILINE)


def _project(
    tmp_path: Path,
    chain: str,
    enabled_table: str | None = None,
    *,
    model_briefing: bool = False,
    model_decides: bool = False,
) -> Path:
    """A copy of the tracked `config/` under `tmp_path` whose `pokemon_choice` chain is `chain`, with `enabled`
    set to true in `enabled_table` when one is named and `[pokemon] model_briefing` and `model_decides` as given."""
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    agent_toml = tmp_path / "config" / "agent.toml"
    text = agent_toml.read_text(encoding="utf-8")
    tracked_chain = f"pokemon_choice = {TRACKED_CHAIN}"
    assert tracked_chain in text
    text = text.replace(tracked_chain, f"pokemon_choice = {chain}")
    # Other tables have a model_briefing of their own, so the switches are set in [pokemon] alone.
    start = text.index("\n[pokemon]\n")
    end = text.index("\n[", start + 1)
    pokemon = text[start:end]
    assert len(BRIEFING_LINE.findall(pokemon)) == len(DECIDES_LINE.findall(pokemon)) == 1
    pokemon = BRIEFING_LINE.sub(f"model_briefing = {str(model_briefing).lower()}", pokemon)
    pokemon = DECIDES_LINE.sub(f"model_decides = {str(model_decides).lower()}", pokemon)
    text = text[:start] + pokemon + text[end:]
    if enabled_table is not None:
        table = text.index(f"\n[{enabled_table}]\n")
        text = text[:table] + text[table:].replace("enabled = false", "enabled = true", 1)
    agent_toml.write_text(text, encoding="utf-8")
    return tmp_path


def _played_settings(seat: bench_agent.BenchSeat, monkeypatch: pytest.MonkeyPatch) -> PokemonSettings:
    """The settings the seat's factory hands the agent it makes."""
    monkeypatch.setattr(bench_agent, "PokemonAgent", lambda settings: settings)
    return seat.factory()


def test_with_models_off_our_seat_is_the_agent_with_every_model_switch_off_and_needs_no_model_role():
    seat = bench_agent.make_seat("off", REPO_ROOT)

    assert seat.model_roles == frozenset()
    assert seat.settings["decision_maker"] == "PokemonAgent" and seat.settings["model_choices"] is False
    assert isinstance(seat.factory(), PokemonAgent)


def test_with_models_on_our_seat_has_model_choices_on_and_needs_only_the_strong_role():
    tracked = PokemonSettings.from_files(REPO_ROOT)

    seat = bench_agent.make_seat("on", REPO_ROOT)

    assert seat.model_roles == frozenset({"strong"})
    assert seat.settings == {
        "decision_maker": "PokemonAgent",
        "model_choices": True,
        "model_briefing": False,
        "model_decides": False,
        "maximin": False,
        "top_k": tracked.top_k,
        "decide_top_k": tracked.decide_top_k,
        "draft_deadline_ms": tracked.draft_deadline_ms,
        "preview_deadline_ms": tracked.preview_deadline_ms,
        "turn_deadline_ms": tracked.turn_deadline_ms,
    }


def test_with_models_brief_our_seat_has_model_choices_and_model_briefing_on_and_needs_only_the_strong_role(
    monkeypatch,
):
    seat = bench_agent.make_seat("brief", REPO_ROOT)

    played = _played_settings(seat, monkeypatch)
    assert seat.model_roles == frozenset({"strong"})
    assert (played.model_choices, played.model_briefing) == (True, True)
    assert (seat.settings["model_choices"], seat.settings["model_briefing"]) == (True, True)
    assert played.model_decides is False
    assert played.top_k == PokemonSettings.from_files(REPO_ROOT).top_k


def test_with_models_decide_our_seat_has_model_choices_briefing_and_decides_on_and_needs_only_the_strong_role(
    monkeypatch,
):
    tracked = PokemonSettings.from_files(REPO_ROOT)

    seat = bench_agent.make_seat("decide", REPO_ROOT)

    played = _played_settings(seat, monkeypatch)
    assert seat.model_roles == frozenset({"strong"})
    assert (played.model_choices, played.model_briefing, played.model_decides) == (True, True, True)
    assert (played.decide_top_k, played.decide_reason_words) == (tracked.decide_top_k, tracked.decide_reason_words)
    assert seat.settings == {
        "decision_maker": "PokemonAgent",
        "model_choices": True,
        "model_briefing": True,
        "model_decides": True,
        "maximin": False,
        "top_k": tracked.top_k,
        "decide_top_k": tracked.decide_top_k,
        "draft_deadline_ms": tracked.draft_deadline_ms,
        "preview_deadline_ms": tracked.preview_deadline_ms,
        "turn_deadline_ms": tracked.turn_deadline_ms,
    }


# Each arm's (model_choices, model_briefing, model_decides), whatever config/agent.toml ships.
ARM_SWITCHES = {
    "off": (False, False, False),
    "on": (True, False, False),
    "brief": (True, True, False),
    "decide": (True, True, True),
}
MODEL_SWITCHES = ("model_choices", "model_briefing", "model_decides")


@pytest.mark.parametrize("tracked_decides", [False, True], ids=["tracked decides off", "tracked decides on"])
@pytest.mark.parametrize("tracked_briefing", [False, True], ids=["tracked briefing off", "tracked briefing on"])
def test_each_arm_sets_model_briefing_and_model_decides_itself_so_on_stays_short_and_ranked_whatever_is_tracked(
    tmp_path, monkeypatch, tracked_briefing, tracked_decides
):
    root = _project(tmp_path, TRACKED_CHAIN, model_briefing=tracked_briefing, model_decides=tracked_decides)
    tracked = PokemonSettings.from_files(root)
    assert (tracked.model_briefing, tracked.model_decides) == (tracked_briefing, tracked_decides)

    seats = {arm: bench_agent.make_seat(arm, root) for arm in bench_agent.ARMS}

    played = {arm: _played_settings(seat, monkeypatch) for arm, seat in seats.items()}
    assert {arm: tuple(getattr(settings, name) for name in MODEL_SWITCHES) for arm, settings in played.items()} == (
        ARM_SWITCHES
    )
    assert {arm: tuple(seat.settings[name] for name in MODEL_SWITCHES) for arm, seat in seats.items()} == ARM_SWITCHES


@pytest.mark.parametrize("arm", ["on", "brief", "decide"])
@pytest.mark.parametrize(
    ("chain", "enabled_table"),
    [('["claude:sonnet", "local:strong"]', "claude"), ('["jev", "local:strong"]', "jev")],
    ids=["claude", "jev"],
)
def test_models_on_is_refused_while_the_chain_would_ask_a_hosted_model(tmp_path, chain, enabled_table, arm):
    root = _project(tmp_path, chain, enabled_table)

    with pytest.raises(bench_agent.HostedModelRefused, match="never asks a hosted model"):
        bench_agent.make_seat(arm, root)
    assert bench_agent.make_seat("off", root).model_roles == frozenset()


@pytest.mark.parametrize(
    ("chain", "enabled_table", "roles"),
    [
        ('["claude:sonnet", "local:strong"]', None, {"strong"}),
        ('["local:strong", "local:fast"]', "claude", {"strong", "fast"}),
        ('["jev", "local:strong"]', None, {"strong"}),
    ],
    ids=["claude named but off", "claude on but not named", "jev named but off"],
)
def test_a_hosted_entry_that_is_switched_off_or_a_service_the_chain_does_not_name_is_not_refused(
    tmp_path, chain, enabled_table, roles
):
    root = _project(tmp_path, chain, enabled_table)

    assert bench_agent.make_seat("on", root).model_roles == roles


# -- the command line ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bench_tool(load_script):
    return load_script("pokemon_bench.py")


@pytest.fixture
def fake_showdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every battle helper the tool starts is the fake one."""
    monkeypatch.setattr(bridge, "default_command", lambda: list(FAKE_HELPER))


def test_1_games_4_against_first_writes_4_rows_and_prints_a_line_per_match_then_the_files(
    runs, fake_showdown, bench_tool, capsys
):
    started = time.perf_counter()

    exit_code = bench_tool.main(["--label", "first-4", "--opponent", "first", "--games", "4"])

    seconds = time.perf_counter() - started
    folder = runs / "practice" / "pokemon" / "first-4"
    written = _read_csv(folder / bench.RESULTS_FILE_NAME)
    summary = json.loads((folder / bench.SUMMARY_FILE_NAME).read_text(encoding="utf-8"))
    printed = capsys.readouterr().out.splitlines()
    assert exit_code == bench_tool.EXIT_PLAYED
    assert len(written) == 4
    assert [row["seed"] for row in written] == ["5000", "5001", "5002", "5003"]
    assert summary["settings"]["model_choices"] is False and summary["model_roles_started"] == []
    assert len(printed) == 6
    assert all(line.startswith(f"seed {5000 + index} (") for index, line in enumerate(printed[:4]))
    assert "unhandled errors" in printed[0]
    assert printed[-2:] == [f"Wrote {folder / bench.RESULTS_FILE_NAME}", f"Wrote {folder / bench.SUMMARY_FILE_NAME}"]
    assert seconds < BENCH_SECONDS_LIMIT


def test_the_seed_base_option_moves_the_seeds(runs, fake_showdown, bench_tool):
    exit_code = bench_tool.main(["--label", "base", "--opponent", "random", "--games", "2", "--seed-base", "7"])

    rows = _read_csv(runs / "practice" / "pokemon" / "base" / bench.RESULTS_FILE_NAME)
    assert exit_code == bench_tool.EXIT_PLAYED
    assert [(row["seed"], row["opponent"]) for row in rows] == [("7", "random"), ("8", "random")]


def test_a_label_that_already_has_results_is_refused_and_left_alone(runs, bench_tool, capsys):
    folder = runs / "practice" / "pokemon" / "taken"
    folder.mkdir(parents=True)
    (folder / bench.RESULTS_FILE_NAME).write_text("kept\n", encoding="utf-8")

    exit_code = bench_tool.main(["--label", "taken", "--opponent", "first", "--games", "1"])

    assert exit_code == bench_tool.EXIT_REFUSED
    assert "give the run a new label" in capsys.readouterr().err
    assert (folder / bench.RESULTS_FILE_NAME).read_text(encoding="utf-8") == "kept\n"


@pytest.mark.parametrize(
    ("models", "roles"), [("on", {"strong"}), ("brief", {"strong"}), ("decide", {"strong"}), ("off", set())]
)
def test_a_run_asks_the_model_server_check_for_only_the_roles_its_seat_needs_and_stops_when_they_are_not_ready(
    runs, bench_tool, monkeypatch, capsys, models, roles
):
    asked: list[set[str]] = []

    @contextmanager
    def unavailable(needed: frozenset[str], _root: Path) -> Iterator[tuple[str, ...]]:
        asked.append(set(needed))
        raise bench_models.ModelsUnavailable("no model server for the run")
        yield ()

    monkeypatch.setattr(bench_models, "serving", unavailable)

    exit_code = bench_tool.main(["--label", "models", "--opponent", "first", "--games", "1", "--models", models])

    assert asked == [roles]
    assert exit_code == bench_tool.EXIT_REFUSED
    assert "no model server for the run" in capsys.readouterr().err
    assert not (runs / "practice" / "pokemon" / "models").exists()


@pytest.mark.parametrize(("asked", "enabled"), [({}, False), ({"maximin": False}, False), ({"maximin": True}, True)])
@pytest.mark.parametrize("arm", ["off", "decide"])
def test_maximin_our_seat_plays_with_maximin_as_the_run_asks_whatever_the_tracked_tuning_says(
    monkeypatch, arm, asked, enabled
):
    seat = bench_agent.make_seat(arm, REPO_ROOT, **asked)

    played = _played_settings(seat, monkeypatch)
    assert played.tuning.maximin.enabled is enabled and seat.settings["maximin"] is enabled
    tracked = PokemonSettings.from_files(REPO_ROOT).tuning
    assert (played.tuning.turn, played.tuning.maximin.our_top) == (tracked.turn, tracked.maximin.our_top)


@pytest.mark.parametrize(("flag", "enabled"), [([], False), (["--maximin", "off"], False), (["--maximin", "on"], True)])
def test_maximin_the_command_line_flag_sets_our_seat_s_maximin_and_the_summary_records_it(
    runs, bench_tool, monkeypatch, flag, enabled
):
    seen: list[dict[str, Any]] = []

    def observed_run(_folder: Path, factory: Any, opponent: str, planned: Any, **options: Any) -> list[dict[str, Any]]:
        seen.append({"maximin": factory()._settings.tuning.maximin.enabled, **options})
        return []

    monkeypatch.setattr(bench, "run_bench", observed_run)

    exit_code = bench_tool.main(["--label", "mx", "--opponent", "self", "--games", "1", "--models", "off", *flag])

    assert exit_code == bench_tool.EXIT_PLAYED
    [run] = seen
    assert run["maximin"] is enabled and run["settings"]["maximin"] is enabled


def test_maximin_a_run_with_maximin_on_against_first_plays_its_matches_with_no_unhandled_error(
    runs, fake_showdown, bench_tool
):
    label = "pk-maximin-on-first-2"

    exit_code = bench_tool.main(["--label", label, "--opponent", "first", "--games", "2", "--maximin", "on"])

    folder = runs / "practice" / "pokemon" / label
    summary = json.loads((folder / bench.SUMMARY_FILE_NAME).read_text(encoding="utf-8"))
    decisions = [
        event["data"]
        for match in bench.match_records(folder)
        for event in match.events
        if event["kind"] == "pokemon_decision"
    ]
    assert exit_code == bench_tool.EXIT_PLAYED
    assert summary["settings"]["maximin"] is True and summary["games"] == 2 and summary["unhandled_errors"] == 0
    assert decisions and {decision["ranking"] for decision in decisions} == {"maximin"}


def test_models_on_plays_the_agent_with_model_choices_on_and_records_the_roles_started(
    runs, bench_tool, monkeypatch, capsys
):
    seen: list[dict[str, Any]] = []

    @contextmanager
    def ready(needed: frozenset[str], _root: Path) -> Iterator[tuple[str, ...]]:
        yield tuple(sorted(needed))

    def observed_run(_folder: Path, factory: Any, opponent: str, planned: Any, **options: Any) -> list[dict[str, Any]]:
        seen.append({"agent": type(factory()).__name__, "opponent": opponent, "games": len(planned), **options})
        return []

    monkeypatch.setattr(bench_models, "serving", ready)
    monkeypatch.setattr(bench, "run_bench", observed_run)

    exit_code = bench_tool.main(["--label", "on", "--opponent", "random", "--games", "3", "--models", "on"])

    assert exit_code == bench_tool.EXIT_PLAYED
    [run] = seen
    assert (run["agent"], run["opponent"], run["games"]) == ("PokemonAgent", "random", 3)
    assert run["settings"]["model_choices"] is True
    assert run["model_roles_started"] == ("strong",)


def test_models_brief_plays_the_agent_with_model_choices_and_model_briefing_on_and_records_the_roles_started(
    runs, bench_tool, monkeypatch
):
    seen: list[dict[str, Any]] = []

    @contextmanager
    def ready(needed: frozenset[str], _root: Path) -> Iterator[tuple[str, ...]]:
        yield tuple(sorted(needed))

    def observed_run(_folder: Path, factory: Any, opponent: str, planned: Any, **options: Any) -> list[dict[str, Any]]:
        seen.append({"agent": type(factory()).__name__, "opponent": opponent, "games": len(planned), **options})
        return []

    monkeypatch.setattr(bench_models, "serving", ready)
    monkeypatch.setattr(bench, "run_bench", observed_run)

    exit_code = bench_tool.main(["--label", "brief", "--opponent", "self", "--games", "2", "--models", "brief"])

    assert exit_code == bench_tool.EXIT_PLAYED
    [run] = seen
    assert (run["agent"], run["opponent"], run["games"]) == ("PokemonAgent", "self", 2)
    assert (run["settings"]["model_choices"], run["settings"]["model_briefing"]) == (True, True)
    assert run["settings"]["model_decides"] is False
    assert run["model_roles_started"] == ("strong",)


def test_models_decide_plays_the_agent_with_all_three_model_switches_on_and_records_model_decides(
    runs, no_port_variables, bench_tool, monkeypatch
):
    seen: list[dict[str, Any]] = []

    @contextmanager
    def ready(needed: frozenset[str], _root: Path) -> Iterator[tuple[str, ...]]:
        yield tuple(sorted(needed))

    def observed_run(_folder: Path, factory: Any, opponent: str, planned: Any, **options: Any) -> list[dict[str, Any]]:
        seen.append({"agent": type(factory()).__name__, "opponent": opponent, "games": len(planned), **options})
        return []

    monkeypatch.setattr(bench_models, "serving", ready)
    monkeypatch.setattr(bench, "run_bench", observed_run)

    exit_code = bench_tool.main(["--label", "decide", "--opponent", "first", "--games", "2", "--models", "decide"])

    assert exit_code == bench_tool.EXIT_PLAYED
    [run] = seen
    assert (run["agent"], run["opponent"], run["games"]) == ("PokemonAgent", "first", 2)
    assert tuple(run["settings"][name] for name in MODEL_SWITCHES) == ARM_SWITCHES["decide"]
    assert run["settings"]["model_endpoint"] == BENCH_ENDPOINT
    assert run["model_roles_started"] == ("strong",)


def test_models_on_is_refused_on_the_command_line_while_the_chain_would_ask_claude(
    tmp_path, runs, bench_tool, monkeypatch, capsys
):
    monkeypatch.setenv(paths.ROOT_ENV, str(_project(tmp_path, '["claude:sonnet", "local:strong"]', "claude")))

    exit_code = bench_tool.main(["--label", "claude", "--opponent", "first", "--games", "1", "--models", "on"])

    assert exit_code == bench_tool.EXIT_REFUSED
    assert "never asks a hosted model" in capsys.readouterr().err
    assert not (runs / "practice" / "pokemon" / "claude").exists()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--label", "x", "--opponent", "first", "--games", "0"],
        ["--label", "x", "--opponent", "claude", "--games", "1"],
        ["--label", "x", "--opponent", "first", "--games", "1", "--models", "maybe"],
        ["--opponent", "first", "--games", "1"],
        ["--label", "x", "--opponent", "first", "--games", "1", "--model-port", "0"],
        ["--label", "x", "--opponent", "first", "--games", "1", "--model-backend-port-base", "65536"],
    ],
    ids=["no games", "unknown opponent", "unknown models", "no label", "port 0", "backend port 65536"],
)
def test_a_wrong_command_line_exits_with_code_2(bench_tool, arguments):
    with pytest.raises(SystemExit) as exited:
        bench_tool.main(arguments)

    assert exited.value.code == 2


# -- the model server's ports ----------------------------------------------------------------------

LIVE_ENDPOINT = "http://127.0.0.1:11500"
BENCH_ENDPOINT = "http://127.0.0.1:11600"
STRONG_ROLE_PLACE = 1


@pytest.fixture
def no_port_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither port variable set, whatever the shell running the tests has."""
    monkeypatch.delenv(server_settings.PORT_ENV, raising=False)
    monkeypatch.delenv(server_settings.BACKEND_PORT_BASE_ENV, raising=False)


def test_the_port_variables_move_the_model_server_s_endpoint_and_backends_and_must_name_ports(
    no_port_variables, monkeypatch
):
    assert server_settings.load(REPO_ROOT).endpoint_url == LIVE_ENDPOINT

    monkeypatch.setenv(server_settings.PORT_ENV, "11600")
    monkeypatch.setenv(server_settings.BACKEND_PORT_BASE_ENV, "41600")

    moved = server_settings.load(REPO_ROOT)
    assert (moved.endpoint_url, moved.backend_port(STRONG_ROLE_PLACE)) == (BENCH_ENDPOINT, 41601)
    assert moved.roles == server_settings.load_tracked(REPO_ROOT).roles
    assert server_settings.load_tracked(REPO_ROOT).endpoint_url == LIVE_ENDPOINT
    monkeypatch.setenv(server_settings.PORT_ENV, "")
    assert server_settings.load(REPO_ROOT).endpoint_url == LIVE_ENDPOINT
    for not_a_port in ("0", "65536", "-1", "port"):
        monkeypatch.setenv(server_settings.PORT_ENV, not_a_port)
        with pytest.raises(ConfigError, match=server_settings.PORT_ENV):
            server_settings.load(REPO_ROOT)


def test_inside_the_run_s_ports_the_model_clients_ask_port_11600_and_the_variables_are_restored_after(
    no_port_variables, monkeypatch
):
    monkeypatch.setenv(server_settings.BACKEND_PORT_BASE_ENV, "40000")
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(f"{request.url.scheme}://{request.url.host}:{request.url.port}")
        return httpx.Response(503)

    choice = ChoiceRequest("Which?", [Option("C1", "one"), Option("C2", "two")], "", 1000, "draft-v0")
    with bench_agent.model_server_ports(11600, 41600, REPO_ROOT) as endpoint:
        served = server_settings.load(REPO_ROOT)
        LocalModelSource("strong", transport=httpx.MockTransport(answer)).ask(choice)

    assert endpoint == BENCH_ENDPOINT and asked == [BENCH_ENDPOINT]
    assert (served.endpoint_url, served.backend_port(STRONG_ROLE_PLACE)) == (BENCH_ENDPOINT, 41601)
    assert server_settings.PORT_ENV not in os.environ
    assert os.environ[server_settings.BACKEND_PORT_BASE_ENV] == "40000"


@pytest.mark.parametrize(
    ("port", "backend_port_base", "message"),
    [
        (11500, 41600, "would use port 11500"),
        (11600, 41500, "would use port 41500, 41501, 41502"),
        (11600, 41498, "would use port 41500"),
        (41502, 41600, "would use port 41502"),
        (11600, 11598, "would use port 11600"),
        (11600, 65534, "pass 65535"),
    ],
    ids=[
        "live endpoint",
        "live backends",
        "backends reaching the live ones",
        "a live backend as endpoint",
        "endpoint among the run's backends",
        "backends past the last port",
    ],
)
def test_the_run_s_ports_are_refused_when_they_meet_the_live_agent_s_or_each_other(
    no_port_variables, port, backend_port_base, message
):
    with pytest.raises(ConfigError, match=message):
        with bench_agent.model_server_ports(port, backend_port_base, REPO_ROOT):
            pytest.fail("the run's ports were taken")

    assert server_settings.PORT_ENV not in os.environ


@pytest.mark.parametrize(("models", "endpoint"), [("brief", BENCH_ENDPOINT), ("off", None)])
def test_a_run_serves_its_model_roles_on_port_11600_by_default_and_the_summary_records_the_endpoint(
    runs, no_port_variables, bench_tool, monkeypatch, models, endpoint
):
    served: list[tuple[str, int]] = []
    seen: list[dict[str, Any]] = []

    @contextmanager
    def ready(needed: frozenset[str], root: Path) -> Iterator[tuple[str, ...]]:
        moved = server_settings.load(root)
        served.append((moved.endpoint_url, moved.backend_port(STRONG_ROLE_PLACE)))
        yield tuple(sorted(needed))

    def observed_run(_folder: Path, _factory: Any, _opponent: str, _planned: Any, **options: Any) -> list[Any]:
        seen.append(options["settings"])
        return []

    monkeypatch.setattr(bench_models, "serving", ready)
    monkeypatch.setattr(bench, "run_bench", observed_run)

    exit_code = bench_tool.main(["--label", "ports", "--opponent", "first", "--games", "1", "--models", models])

    assert exit_code == bench_tool.EXIT_PLAYED
    assert served == [(BENCH_ENDPOINT, 41601)]
    assert [settings["model_endpoint"] for settings in seen] == [endpoint]
    assert server_settings.PORT_ENV not in os.environ


def test_the_live_agent_s_model_port_is_refused_on_the_command_line_before_anything_is_played(
    runs, no_port_variables, bench_tool, monkeypatch, capsys
):
    asked: list[frozenset[str]] = []
    monkeypatch.setattr(bench_models, "serving", lambda needed, _root: asked.append(needed))

    exit_code = bench_tool.main(
        ["--label", "live", "--opponent", "first", "--games", "1", "--models", "brief", "--model-port", "11500"]
    )

    assert exit_code == bench_tool.EXIT_REFUSED
    assert "would use port 11500" in capsys.readouterr().err
    assert asked == [] and not (runs / "practice" / "pokemon" / "live").exists()


def _played_row(**columns: Any) -> dict[str, Any]:
    return {**dict.fromkeys(bench.COLUMNS, 0), "seed": 5001, "turns": 9, **columns}


@pytest.mark.parametrize(
    ("played", "line"),
    [
        (
            bench.PlayedMatch(_played_row(our_won=1, decisions=20, fallbacks=2, parse_degradations=1), None),
            "seed 5001 (0.25 s): won in 9 turns; 20 decisions, 2 fallbacks, 1 parse degradations, 0 unhandled errors",
        ),
        (
            bench.PlayedMatch(_played_row(turns=None, unhandled_errors=1), "OpponentError: broke a rule"),
            "seed 5001 (0.25 s): raised OpponentError: broke a rule",
        ),
        (
            bench.PlayedMatch(_played_row(turns=0, unhandled_errors=1), "DecisionError: refused"),
            "seed 5001 (0.25 s): did not win in 0 turns; 0 decisions, 0 fallbacks, 0 parse degradations, "
            "1 unhandled errors; ended early: DecisionError: refused",
        ),
    ],
    ids=["played", "raised", "ended early"],
)
def test_the_line_of_a_match_says_how_it_ended(bench_tool, played, line):
    assert bench_tool.match_line(played, 0.25) == line


# -- the report ------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def report(load_script):
    return load_script("report.py")


def _label(
    folder: Path, opponent: str, games: int, wins: int, *, fallbacks: int = 0, unhandled_errors: int = 0
) -> Path:
    """Write a label folder's `results.csv`: `games` matches, the first `wins` of them won, the first match with
    `fallbacks` fallbacks (one of them a parse degradation) and the last with `unhandled_errors`; the slowest
    decisions grow with the seed."""
    rows = [
        {
            **dict.fromkeys(bench.COLUMNS, 0),
            "seed": SEED_BASE + index,
            "opponent": opponent,
            "our_won": int(index < wins),
            "turns": 8,
            "decisions": 16,
            "decision_p95_ms": 900.0,
            "max_decision_ms_draft": 100.0 + index,
            "max_decision_ms_team_preview": 2000.0 + index,
            "max_decision_ms_moving": 3000.25 + index,
        }
        for index in range(games)
    ]
    rows[0].update(fallbacks=fallbacks, parse_degradations=min(fallbacks, 1))
    rows[-1]["unhandled_errors"] = unhandled_errors
    bench.write_results(folder, rows)
    return folder


def _files_under(folder: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


def test_3_pokemon_bench_prints_the_opponent_games_wins_win_rate_fallbacks_and_slowest_decision_per_phase(
    tmp_path, run_script
):
    label = _label(tmp_path / "label", "random", 5, 4, fallbacks=3, unhandled_errors=1)
    before = _files_under(tmp_path)

    completed = run_script(tmp_path, "report.py", "--pokemon-bench", str(label))

    assert (completed.returncode, completed.stderr) == (0, "")
    assert completed.stdout.splitlines() == [
        "opponent,random",
        "games,wins,win_rate,fallbacks,parse_degradations,unhandled_errors",
        "5,4,0.8,3,1,1",
        "phase,slowest_decision_ms",
        "draft,104",
        "team_preview,2004",
        "moving,3004.25",
        "phase,median_decision_ms",
        "draft,-",
        "team_preview,-",
        "moving,-",
        "model_answers,model_differed,model_differed_share",
        "0,0,-",
    ]
    assert _files_under(tmp_path) == before


def test_3_pokemon_bench_compare_prints_both_labels_and_the_fisher_p_of_their_wins(tmp_path, report, capsys):
    label_a = _label(tmp_path / "a", "first", 50, 30)
    label_b = _label(tmp_path / "b", "first", 50, 42)

    exit_code = report.main(["--pokemon-bench-compare", str(label_a), str(label_b)])
    printed = capsys.readouterr().out.splitlines()

    assert exit_code == 0
    assert printed[0] == f"A,{label_a}" and f"B,{label_b}" in printed
    assert printed.count("games,wins,win_rate,fallbacks,parse_degradations,unhandled_errors") == 2
    assert "50,30,0.6,0,0,0" in printed and "50,42,0.84,0,0,0" in printed
    assert printed[-2:] == [
        "games_a,wins_a,games_b,wins_b,fisher_p_two_sided",
        f"50,30,50,42,{fisher_exact_p(30, 50, 42, 50):.3f}",
    ]
    # scipy.stats.fisher_exact([[30, 20], [42, 8]]) gives 0.013482...
    assert printed[-1] == "50,30,50,42,0.013"


def _model_decision(chosen: str, source: str | None = "local:strong") -> dict[str, Any]:
    """A `pokemon_decision` event the source decided, the script's choice being the first candidate."""
    return _event("pokemon_decision", {"phase": "moving", "script_choice": "T1", "chosen": chosen, "source": source})


# A model match: the script alone (no model asked, or its answer not used), then three model answers, one of them
# another candidate than the script's best; and a second match where the model chose the script's best.
BRIEF_MATCHES = (
    [_model_decision("T1", "script"), _model_decision("T2"), _model_decision("T1"), _model_decision("T1", None)],
    [_model_decision("T1")],
)


def _write_records(label: Path, matches: tuple[list[dict[str, Any]], ...]) -> None:
    """Write one match record of our seat per events list into the label folder's dated folder."""
    for index, events in enumerate(matches):
        match = label / "20261010" / f"practice-pokemon-{SEED_BASE + index}-0000000{index}__0"
        match.mkdir(parents=True)
        lines = "".join(json.dumps(event) + "\n" for event in events)
        (match / record_files.events_file_name(1)).write_text(lines, encoding="utf-8")


def test_the_model_choices_of_brief_records_count_the_model_s_answers_and_those_that_differed_from_the_script():
    choices = bench_report.model_choices([_record(events) for events in BRIEF_MATCHES])

    assert (choices.answers, choices.differed) == (3, 1)
    assert choices.differed_share == 1 / 3
    assert bench_report.model_choices([]).differed_share is None


def _decides_decision(chosen: str, shown_order: list[int]) -> dict[str, Any]:
    """A `pokemon_decision` event of a decide record: the ids follow the shuffled shown order, so the script's best
    is the id at the place of rank 1."""
    script_choice = f"T{shown_order.index(1) + 1}"
    data = {
        "phase": "moving",
        "script_choice": script_choice,
        "chosen": chosen,
        "source": "local:strong",
        "shown_order": shown_order,
        "script_rank_of_choice": shown_order[int(chosen[1:]) - 1],
    }
    return _event("pokemon_decision", data)


def test_the_model_choices_of_decide_records_count_the_script_s_best_by_its_shuffled_id_not_by_the_first_place():
    # The script's best is shown third in both decisions: T3 agrees with the script, T1 (its rank 2) differs.
    record = _record([_decides_decision("T3", [2, 4, 1, 3]), _decides_decision("T1", [2, 4, 1, 3])])

    choices = bench_report.model_choices([record])

    assert (choices.answers, choices.differed) == (2, 1)
    assert choices.differed_share == 1 / 2


def test_the_report_of_a_brief_label_prints_the_share_of_model_decisions_that_differed_from_the_script(
    tmp_path, run_script
):
    label = _label(tmp_path / "label", "self", 2, 1)
    _write_records(label, BRIEF_MATCHES)

    completed = run_script(tmp_path, "report.py", "--pokemon-bench", str(label))

    assert (completed.returncode, completed.stderr) == (0, "")
    assert completed.stdout.splitlines()[-2:] == ["model_answers,model_differed,model_differed_share", "3,1,0.33"]


def test_the_compare_report_gives_each_brief_label_s_share_of_model_decisions_that_differed(tmp_path, report, capsys):
    label_a = _label(tmp_path / "off", "first", 2, 1)
    label_b = _label(tmp_path / "brief", "first", 2, 2)
    _write_records(label_b, BRIEF_MATCHES)

    exit_code = report.main(["--pokemon-bench-compare", str(label_a), str(label_b)])
    printed = capsys.readouterr().out.splitlines()

    assert exit_code == 0
    figures = [printed[index + 1] for index, line in enumerate(printed) if line.startswith("model_answers,")]
    assert figures == ["0,0,-", "3,1,0.33"]
    assert printed.index("0,0,-") < printed.index(f"B,{label_b}") < printed.index("3,1,0.33")


def test_the_median_decision_of_each_phase_is_the_nearest_rank_median_over_a_brief_label_s_records(
    tmp_path, run_script
):
    matches = (
        [*_decided("draft", 30.0), *_decided("draft", 10.0), *_decided("team_preview", 500.0)],
        [*_decided("draft", 20.0), *_decided("draft", 40.0), *_decided("moving", 900.5), *_decided("moving", 700.0)],
    )
    label = _label(tmp_path / "label", "self", 2, 2)
    _write_records(label, matches)

    medians = bench_report.median_decisions([_record(events) for events in matches])
    completed = run_script(tmp_path, "report.py", "--pokemon-bench", str(label))

    # Nearest rank: the second of the four draft times (10, 20, 30, 40) and the first of the two battle turns.
    assert medians == {"draft": 20.0, "team_preview": 500.0, "moving": 700.0}
    assert bench_report.median_decisions([]) == {"draft": None, "team_preview": None, "moving": None}
    printed = completed.stdout.splitlines()
    median_lines = printed.index("phase,median_decision_ms")
    assert printed[median_lines + 1 : median_lines + 4] == ["draft,20", "team_preview,500", "moving,700"]


def test_a_label_without_matches_shows_no_win_rate_slowest_decision_or_fisher_p():
    lines = bench_report.compare_lines("A", [], "B", [])

    assert "0,0,-,0,0,0" in lines and "draft,-" in lines
    assert lines[-1] == "0,0,0,0,-"


def test_pokemon_bench_of_a_folder_without_results_ends_with_exit_code_2(tmp_path, report, capsys):
    exit_code = report.main(["--pokemon-bench", str(tmp_path / "missing")])

    captured = capsys.readouterr()
    assert (exit_code, captured.out) == (2, "")
    assert "is not a Pokémon practice benchmark label folder" in captured.err


def test_a_werewolf_label_is_not_a_pokemon_label(tmp_path, report, capsys):
    (tmp_path / bench.RESULTS_FILE_NAME).write_text("seed,seat,role\n1000,6,seer\n", encoding="utf-8")

    exit_code = report.main(["--pokemon-bench-compare", str(tmp_path), str(tmp_path)])

    assert exit_code == 2
    assert "does not have the columns" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--pokemon-bench", "a", "runs/20261016"],
        ["--pokemon-bench", "a", "--werewolf-bench", "b"],
        ["--pokemon-bench", "a", "--pokemon-bench-compare", "a", "b"],
        ["--pokemon-bench-compare", "a"],
    ],
    ids=["with a path", "with werewolf-bench", "with pokemon-bench-compare", "compare with one label"],
)
def test_a_pokemon_bench_mode_with_another_mode_or_one_label_to_compare_is_a_usage_error(report, arguments):
    with pytest.raises(SystemExit) as stopped:
        report.main(arguments)

    assert stopped.value.code == 2
