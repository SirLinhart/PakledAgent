"""The local benchmark: where the agent's records go, and the table over a label's harness summaries and records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from altruagent import DecisionContext
from ucla_agent.core import bench, metrics, paths, records
from ucla_agent.core.record_files import events_file_name, meta_file_name
from ucla_agent.games.redalert.items import ItemTable

REPO_ROOT = Path(__file__).resolve().parents[2]
ITEMS_FILE = Path("config") / "redalert" / "items.toml"
RESULT = {"is_terminal": True, "returns": {"agent-1": 1.0}, "termination_reason": "victory"}
SESSION_FOLDER = "game1-sidestep-401__Multi0"
RECORD_START = "2026-10-08T00:41:48.000Z"
UNITS_CREATED = {"e1": 10, "2tnk": 2, "harv": 2}
# Ten seconds short of power, then enough: power drained above power provided from tick 0 to tick 250.
POWER_STATES = [
    ("state", {"state_version": 0, "phase": "playing", "economy": {"power_drained": 120, "power_provided": 100}}),
    ("state", {"state_version": 250, "phase": "playing", "economy": {"power_drained": 80, "power_provided": 100}}),
    ("state", {"state_version": 500, "phase": "playing", "economy": {"power_drained": 80, "power_provided": 100}}),
]
TIMINGS = [
    ("timing", {"income_per_s": 40, "barracks": 1, "war_factories": 1}),
    ("timing", {"income_per_s": 60, "barracks": 3, "war_factories": 2}),
]
ACTIONS = [("action", {"state_version": 0, "action": None, "compute_ms": ms}) for ms in (1.5, 2.5, 4.0)]
FULL_RECORD = [*POWER_STATES, *TIMINGS, *ACTIONS]
Event = tuple[str, dict[str, Any]]


def write_record(match_folder: Path, events: list[Event], attempt: int = 1) -> None:
    """Write one attempt's events file and meta file of a Red Alert match into a match folder."""
    match_folder.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"seq": seq, "t": RECORD_START, "mono_ms": seq, "kind": kind, "data": data})
        for seq, (kind, data) in enumerate(events)
    ]
    (match_folder / events_file_name(attempt)).write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    meta = {
        "session_id": "game1-sidestep-401",
        "game_type": "red_alert",
        "agent_id": "local-Multi0",
        "attempt": attempt,
    }
    (match_folder / meta_file_name(attempt)).write_text(json.dumps(meta), encoding="utf-8")


def write_summary(
    game_folder: Path,
    *,
    winner: str | None = "Multi0",
    result: str | None = None,
    termination_reason: str = "victory",
    units_created: dict[str, int] | None = None,
    buildings_created: dict[str, int] | None = None,
) -> None:
    """Write a `summary.json` shaped like the harness's for the one game a game folder holds, the agent in Multi0."""
    bot, seed = game_folder.name.split("-")[0], int(game_folder.name.rsplit("-", 1)[1])
    agent_seat = {
        "name": "agent.agent:create_agent",
        "kind": "agent",
        "faction": "england",
        "tiebreak": [5000, 12000],
        "stats": {"units_created": units_created or UNITS_CREATED, "buildings_created": buildings_created or {}},
    }
    game = {
        "session_id": f"game1-sidestep-{seed}",
        "seed": seed,
        "result": result or ("draw" if winner is None else f"{winner} wins"),
        "winner_seat": winner,
        "termination_reason": termination_reason,
        "seats": {"Multi0": agent_seat, "Multi1": {"name": f"bot:{bot}", "kind": "bot", "tiebreak": [-5000, 3000]}},
    }
    summary = {"command": {"opponent": f"bot:{bot}", "seed": seed, "agent_seat": "Multi0"}, "games": [game]}
    game_folder.mkdir(parents=True, exist_ok=True)
    (game_folder / bench.SUMMARY_FILE_NAME).write_text(json.dumps(summary, indent=2), encoding="utf-8")


def write_game(label: Path, name: str, record: list[Event] | None = None, **summary: Any) -> Path:
    """A game folder with its summary and, when `record` is given, the agent's record under `records/`."""
    game_folder = label / name
    write_summary(game_folder, **summary)
    if record is not None:
        write_record(game_folder / bench.RECORDS_FOLDER_NAME / "20261008" / SESSION_FOLDER, record)
    return game_folder


@pytest.fixture(scope="module")
def unit_cost() -> bench.UnitCost:
    """The cost of a unit type from the tracked item table."""
    table = ItemTable.from_file(REPO_ROOT)
    return lambda name: None if (item := table.get(name)) is None else item.cost


def _play_fake_match(session_id: str) -> None:
    """Open and close the record of one match, as the agent does for a game it plays to the end."""
    context = DecisionContext(session_id=session_id, tournament_id=None, game_type="red_alert", agent_id="agent-1")
    records.start_match(context, None)
    records.finish_match(RESULT)


def _events_files(folder: Path) -> list[Path]:
    return sorted(folder.glob("*/*/events.jsonl"))


# -- the records folder ----------------------------------------------------------------------------


def test_with_ucla_agent_runs_dir_set_a_match_record_is_written_under_it_and_not_under_runs(
    run_folder, tmp_path_factory, monkeypatch
):
    records_folder = tmp_path_factory.mktemp("game") / "records"
    monkeypatch.setenv(paths.RUNS_DIR_ENV, str(records_folder))

    _play_fake_match("game1-sidestep-401")

    (events_path,) = _events_files(records_folder)
    assert events_path.parent.name == "game1-sidestep-401__seat"
    assert events_path.read_text(encoding="utf-8").count('"kind": "result"') == 1
    assert not (run_folder / "runs").exists()


def test_with_ucla_agent_runs_dir_unset_a_match_record_is_written_under_runs_of_the_root(run_folder):
    _play_fake_match("game1-sidestep-401")

    (events_path,) = _events_files(run_folder / "runs")
    assert events_path.parent.name == "game1-sidestep-401__seat"


def test_run_lines_follow_the_records_into_the_named_folder(run_folder, tmp_path_factory, monkeypatch):
    records_folder = tmp_path_factory.mktemp("game") / "records"
    monkeypatch.setenv(paths.RUNS_DIR_ENV, str(records_folder))

    records.append_run_line("run_started", {})

    assert [path.name for path in records_folder.glob("*/*")] == [records.RUN_FILE_NAME]
    assert not (run_folder / "runs").exists()


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_an_unset_or_empty_runs_dir_keeps_the_default(monkeypatch, tmp_path, value):
    if value is None:
        monkeypatch.delenv(paths.RUNS_DIR_ENV, raising=False)
    else:
        monkeypatch.setenv(paths.RUNS_DIR_ENV, value)

    assert paths.runs_folder(tmp_path / "runs") == tmp_path / "runs"


def test_a_relative_runs_dir_is_taken_from_the_current_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(paths.RUNS_DIR_ENV, "records")

    assert paths.runs_folder(Path("elsewhere")) == tmp_path / "records"


def test_the_configuration_is_still_read_from_the_project_root(run_folder, tmp_path_factory, monkeypatch):
    monkeypatch.setenv(paths.RUNS_DIR_ENV, str(tmp_path_factory.mktemp("records")))

    assert paths.repository_root() == run_folder


# -- the newest attempt under a game folder's records ----------------------------------------------


def test_a_missing_or_empty_records_folder_holds_no_attempt(tmp_path):
    (tmp_path / "empty").mkdir()
    (tmp_path / "dated-only" / "20261008").mkdir(parents=True)

    assert metrics.newest_attempt(tmp_path / "missing") is None
    assert metrics.newest_attempt(tmp_path / "empty") is None
    assert metrics.newest_attempt(tmp_path / "dated-only") is None


def test_the_newest_attempt_is_the_highest_attempt_of_the_folder_read_alone(tmp_path):
    match_folder = tmp_path / "records" / "20261008" / SESSION_FOLDER
    write_record(match_folder, [("timing", {"barracks": 1})])
    write_record(match_folder, [("timing", {"barracks": 2}), ("timing", {"barracks": 3})], attempt=2)

    match = metrics.newest_attempt(tmp_path / "records")

    assert (match.folder, [attempt.number for attempt in match.attempts]) == (match_folder, [2])
    assert match.data_of("timing") == [{"barracks": 2}, {"barracks": 3}]
    assert match.attempts[0].meta["attempt"] == 2


def test_a_later_dated_folder_holds_the_newest_attempt_whatever_the_attempt_numbers(tmp_path):
    write_record(tmp_path / "records" / "20261007" / SESSION_FOLDER, [("timing", {"barracks": 1})], attempt=3)
    write_record(tmp_path / "records" / "20261008" / SESSION_FOLDER, [("timing", {"barracks": 4})])
    (tmp_path / "records" / "20261008" / records.RUN_FILE_NAME).write_text("{}\n", encoding="utf-8")

    match = metrics.newest_attempt(tmp_path / "records")

    assert match.folder == tmp_path / "records" / "20261008" / SESSION_FOLDER
    assert match.data_of("timing") == [{"barracks": 4}]


# -- the table rows --------------------------------------------------------------------------------


def test_a_label_of_two_games_gives_two_rows_with_every_column_filled(tmp_path, unit_cost):
    label = tmp_path / "label"
    write_game(label, "normal-england-sidestep-419", FULL_RECORD, winner="Multi1", termination_reason="time_limit")
    write_game(label, "rush-england-sidestep-401", FULL_RECORD)

    games = bench.read_label(label, unit_cost)

    assert [game.row for game in games] == [
        {
            "config": "rush-england-sidestep",
            "seed": 401,
            "result": "win",
            "termination_reason": "victory",
            "kd_value": 5000,
            "assets_value": 12000,
            "army_value_produced": 2700,
            "income_per_s": 50.0,
            "barracks_max": 3,
            "war_factories_max": 2,
            "low_power_seconds": 10.0,
            "compute_ms_p95": 4.0,
        },
        {
            "config": "normal-england-sidestep",
            "seed": 419,
            "result": "loss",
            "termination_reason": "time_limit",
            "kd_value": 5000,
            "assets_value": 12000,
            "army_value_produced": 2700,
            "income_per_s": 50.0,
            "barracks_max": 3,
            "war_factories_max": 2,
            "low_power_seconds": 10.0,
            "compute_ms_p95": 4.0,
        },
    ]
    assert [(game.bot, game.config) for game in games] == [
        ("rush", "rush-england-sidestep"),
        ("normal", "normal-england-sidestep"),
    ]


def test_the_table_prints_the_column_names_then_one_comma_separated_line_per_game(tmp_path, unit_cost):
    write_game(tmp_path / "label", "rush-england-sidestep-401", FULL_RECORD)

    lines = bench.table_lines(bench.read_label(tmp_path / "label", unit_cost))

    assert lines == [
        "config,seed,result,termination_reason,kd_value,assets_value,army_value_produced,income_per_s,barracks_max,"
        "war_factories_max,low_power_seconds,compute_ms_p95",
        "rush-england-sidestep,401,win,victory,5000,12000,2700,50,3,2,10,4",
    ]


def test_a_game_folder_without_records_shows_a_dash_in_every_record_column(tmp_path, unit_cost):
    write_game(tmp_path / "label", "rush-russia-trapped-410")

    (game,) = bench.read_label(tmp_path / "label", unit_cost)

    assert {column: game.row[column] for column in bench.RECORD_COLUMNS} == dict.fromkeys(bench.RECORD_COLUMNS)
    assert bench.table_lines([game])[1] == "rush-russia-trapped,410,win,victory,5000,12000,2700,-,-,-,-,-"


def test_a_record_without_timing_fields_shows_a_dash_for_them_only(tmp_path, unit_cost):
    write_game(tmp_path / "label", "rush-russia-trapped-410", [*POWER_STATES, ("timing", {}), *ACTIONS])

    (game,) = bench.read_label(tmp_path / "label", unit_cost)

    assert [game.row[column] for column in bench.RECORD_COLUMNS] == [None, None, None, 10.0, 4.0]


def test_a_game_folder_without_a_summary_is_a_no_result_row_with_the_seed_of_its_name(tmp_path, unit_cost):
    (tmp_path / "label" / "rush-england-sidestep-401" / "engine-logs").mkdir(parents=True)

    (game,) = bench.read_label(tmp_path / "label", unit_cost)

    assert bench.table_lines([game])[1] == "rush-england-sidestep,401,no_result,-,-,-,-,-,-,-,-,-"


@pytest.mark.parametrize(
    "summary, result",
    [
        ({"winner": "Multi0"}, "win"),
        ({"winner": "Multi1"}, "loss"),
        ({"winner": None}, "draw"),
        ({"winner": None, "result": "no_result", "termination_reason": "engine_error"}, "no_result"),
    ],
    ids=["win", "loss", "draw", "no result"],
)
def test_the_result_is_read_for_the_seat_whose_kind_is_agent(tmp_path, unit_cost, summary, result):
    write_game(tmp_path / "label", "normal-russia-trapped-428", **summary)

    (game,) = bench.read_label(tmp_path / "label", unit_cost)

    assert game.row["result"] == result


def test_a_unit_the_item_table_does_not_know_leaves_the_army_value_missing(tmp_path, unit_cost):
    write_game(tmp_path / "label", "rush-england-sidestep-401", units_created={"e1": 3, "mystery": 1})

    (game,) = bench.read_label(tmp_path / "label", unit_cost)

    assert game.row["army_value_produced"] is None


def test_games_are_in_configuration_order_then_seed_order_and_other_configurations_last(tmp_path, unit_cost):
    names = [
        "turtle-england-sidestep-1",
        "normal-england-sidestep-419",
        "rush-russia-sidestep-410",
        "rush-england-trapped-404",
        "rush-england-sidestep-1000",
        "rush-england-sidestep-98",
    ]
    for name in names:
        write_game(tmp_path / "label", name)

    games = bench.read_label(tmp_path / "label", unit_cost)

    assert [f"{game.config}-{game.row['seed']}" for game in games] == [
        "rush-england-sidestep-98",
        "rush-england-sidestep-1000",
        "rush-england-trapped-404",
        "rush-russia-sidestep-410",
        "normal-england-sidestep-419",
        "turtle-england-sidestep-1",
    ]


def test_a_sub_folder_not_named_as_a_game_folder_is_refused(tmp_path, unit_cost):
    (tmp_path / "label" / "notes").mkdir(parents=True)

    with pytest.raises(ValueError, match="is not a game folder"):
        bench.read_label(tmp_path / "label", unit_cost)


def test_a_summary_without_an_agent_seat_is_refused(tmp_path, unit_cost):
    game_folder = write_game(tmp_path / "label", "rush-england-sidestep-401")
    summary = json.loads((game_folder / bench.SUMMARY_FILE_NAME).read_text(encoding="utf-8"))
    summary["games"][0]["seats"]["Multi0"]["kind"] = "bot"
    (game_folder / bench.SUMMARY_FILE_NAME).write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises(ValueError, match="0 seats of kind agent"):
        bench.read_label(tmp_path / "label", unit_cost)


def test_a_summary_of_several_games_is_refused(tmp_path, unit_cost):
    game_folder = write_game(tmp_path / "label", "rush-england-sidestep-401")
    summary = json.loads((game_folder / bench.SUMMARY_FILE_NAME).read_text(encoding="utf-8"))
    summary["games"] *= 2
    (game_folder / bench.SUMMARY_FILE_NAME).write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises(ValueError, match="describes 2 games"):
        bench.read_label(tmp_path / "label", unit_cost)


@pytest.mark.parametrize(
    "value, shown",
    [(None, "-"), (-39650, "-39650"), (50.0, "50"), (0.604, "0.6"), (39.992, "39.99"), (-0.001, "0"), ("x,y", "x,y")],
)
def test_a_cell_shows_a_missing_value_as_a_dash_and_a_fraction_to_two_decimals(value, shown):
    assert bench.cell(value) == shown


def test_a_cell_holding_a_comma_is_quoted_in_its_line():
    assert bench.csv_line(["a", "x,y", "-"]) == 'a,"x,y",-'


def test_a_summary_seat_without_stats_or_tiebreak_leaves_those_columns_missing(tmp_path, unit_cost):
    game_folder = write_game(tmp_path / "label", "rush-england-sidestep-401")
    summary = json.loads((game_folder / bench.SUMMARY_FILE_NAME).read_text(encoding="utf-8"))
    for key in ("stats", "tiebreak"):
        del summary["games"][0]["seats"]["Multi0"][key]
    (game_folder / bench.SUMMARY_FILE_NAME).write_text(json.dumps(summary), encoding="utf-8")

    (game,) = bench.read_label(tmp_path / "label", unit_cost)

    assert [game.row[column] for column in ("result", "kd_value", "assets_value", "army_value_produced")] == [
        "win",
        None,
        None,
        None,
    ]


# -- the build profile -----------------------------------------------------------------------------


def test_the_build_profile_gives_each_configuration_s_mean_counts_from_the_summaries(tmp_path, unit_cost):
    label = tmp_path / "label"
    write_game(
        label,
        "rush-england-sidestep-401",
        units_created={"2tnk": 3, "e1": 9, "harv": 2},
        buildings_created={"pbox": 2, "gun": 1, "powr": 4, "apwr": 1, "tent": 1, "weap": 2, "proc": 2},
    )
    write_game(
        label,
        "rush-england-sidestep-402",
        units_created={"2tnk": 1, "e1": 4},
        buildings_created={"pbox": 1, "powr": 3, "tent": 2, "weap": 1},
    )
    write_game(
        label,
        "normal-russia-trapped-428",
        units_created={"3tnk": 4},
        buildings_created={"ftur": 1, "tsla": 2, "powr": 6, "barr": 2, "weap": 2},
    )
    (label / "normal-russia-trapped-429").mkdir()

    lines = bench.build_profile_lines(bench.read_label(label, unit_cost))

    assert lines == [
        "build_profile,games,main_tank,defenses,power_plants,producers",
        "rush-england-sidestep,2,2,2,4,3",
        "normal-russia-trapped,1,4,3,6,4",
    ]


def test_a_summary_without_building_counts_counts_no_building(tmp_path, unit_cost):
    game_folder = write_game(tmp_path / "label", "rush-england-sidestep-401", units_created={"3tnk": 2})
    summary = json.loads((game_folder / bench.SUMMARY_FILE_NAME).read_text(encoding="utf-8"))
    del summary["games"][0]["seats"]["Multi0"]["stats"]["buildings_created"]
    (game_folder / bench.SUMMARY_FILE_NAME).write_text(json.dumps(summary), encoding="utf-8")

    (game,) = bench.read_label(tmp_path / "label", unit_cost)

    assert game.build_counts == {"main_tank": 2, "defenses": 0, "power_plants": 0, "producers": 0}


def test_a_configuration_without_any_summary_shows_no_games_and_dashes(tmp_path, unit_cost):
    (tmp_path / "label" / "rush-england-sidestep-401").mkdir(parents=True)

    lines = bench.build_profile_lines(bench.read_label(tmp_path / "label", unit_cost))

    assert lines[1:] == ["rush-england-sidestep,0,-,-,-,-"]


@pytest.mark.parametrize("values, expected", [([1, 2, 4], 7 / 3), ([], None)], ids=["numbers", "none"])
def test_the_mean_of_no_number_is_none(values, expected):
    assert bench.mean(values) == expected


# -- totals and the goal ---------------------------------------------------------------------------


def _games(bot: str, won: int, played: int, other: str = "loss") -> list[bench.Game]:
    """`played` games against one bot, the first `won` of them won and the rest ending as `other`."""
    config = f"{bot}-england-sidestep"
    rows = [
        {"result": "win" if number < won else other, **dict.fromkeys(bench.NUMERIC_COLUMNS)} for number in range(played)
    ]
    return [bench.Game(bot, config, row, None) for row in rows]


@pytest.mark.parametrize(
    "normal_won, normal_played, rush_won, rush_played, line",
    [
        (10, 18, 9, 18, "goal: met"),
        (9, 18, 9, 18, "goal: not met"),
        (10, 18, 8, 18, "goal: not met"),
        (19, 36, 18, 36, "goal: met"),
        (18, 36, 18, 36, "goal: not met"),
        (19, 36, 17, 36, "goal: not met"),
        (10, 18, 0, 0, "goal: not met"),
        (0, 0, 9, 18, "goal: not met"),
    ],
    ids=[
        "36 games met",
        "9 normal wins",
        "8 rush wins",
        "72 games met",
        "18 of 36",
        "17 of 36",
        "no rush",
        "no normal",
    ],
)
def test_the_goal_is_more_than_half_against_normal_and_at_least_half_against_rush(
    normal_won, normal_played, rush_won, rush_played, line
):
    games = _games("rush", rush_won, rush_played) + _games("normal", normal_won, normal_played)

    assert bench.goal_line(games) == line
    assert bench.goal_met(games) is (line == "goal: met")


def test_a_game_without_a_result_counts_as_a_game_not_won():
    games = _games("rush", 9, 18) + _games("normal", 9, 17) + _games("normal", 0, 1, other="no_result")

    assert bench.goal_line(games) == "goal: not met"


def test_the_totals_give_the_outcomes_overall_and_per_opponent_and_the_column_means():
    games = (
        _games("rush", 9, 17)
        + _games("rush", 0, 1, other="draw")
        + _games("normal", 10, 17)
        + _games("normal", 0, 1, other="no_result")
    )
    games[0].row.update(kd_value=1000, income_per_s=40.0)
    games[-1].row.update(kd_value=-2000, income_per_s=45.5, compute_ms_p95=0.604)

    lines = bench.totals_lines(games)

    assert lines == [
        "outcomes,games,win,loss,draw,no_result",
        "all,36,19,15,1,1",
        "rush,18,9,8,1,0",
        "normal,18,10,7,0,1",
        "means,kd_value,assets_value,army_value_produced,income_per_s,barracks_max,war_factories_max,"
        "low_power_seconds,compute_ms_p95",
        "all,-500,-,-,42.75,-,-,-,0.6",
    ]


def test_rush_and_normal_are_always_listed_and_another_bot_after_them():
    lines = bench.totals_lines(_games("turtle", 1, 2))

    assert lines[1:5] == ["all,2,1,1,0,0", "rush,0,0,0,0,0", "normal,0,0,0,0,0", "turtle,2,1,1,0,0"]


def test_bench_prints_the_table_the_build_profile_the_totals_and_the_goal_line_last(tmp_path, unit_cost):
    label = tmp_path / "label"
    write_game(label, "rush-england-sidestep-401", FULL_RECORD)
    write_game(label, "normal-england-sidestep-419", winner="Multi1")

    lines = bench.bench_lines(bench.read_label(label, unit_cost))

    assert lines[:4] == [
        bench.table_lines([])[0],
        "rush-england-sidestep,401,win,victory,5000,12000,2700,50,3,2,10,4",
        "normal-england-sidestep,419,loss,victory,5000,12000,2700,-,-,-,-,-",
        "",
    ]
    assert lines[4:8] == [
        "build_profile,games,main_tank,defenses,power_plants,producers",
        "rush-england-sidestep,1,2,0,0,0",
        "normal-england-sidestep,1,2,0,0,0",
        "",
    ]
    assert lines[8:12] == [
        "outcomes,games,win,loss,draw,no_result",
        "all,2,1,1,0,0",
        "rush,1,1,0,0,0",
        "normal,1,0,1,0,0",
    ]
    assert lines[-3:] == ["all,5000,12000,2700,50,3,2,10,4", "", "goal: not met"]
    assert max(len(line) for line in lines) <= 160


# -- comparing two labels --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wins_a, games_a, wins_b, games_b, expected",
    [(3, 3, 0, 3, 0.1), (0, 3, 3, 3, 0.1), (15, 72, 15, 72, 1.0), (1, 1, 0, 1, 1.0)],
    ids=["3 of 3 against 0 of 3", "the other way round", "equal", "one game each"],
)
def test_fisher_exact_p_is_the_two_sided_p_value_of_the_win_table(wins_a, games_a, wins_b, games_b, expected):
    assert bench.fisher_exact_p(wins_a, games_a, wins_b, games_b) == pytest.approx(expected)


def test_12_wins_of_36_against_6_of_36_gives_a_p_value_of_0_17():
    p_value = bench.fisher_exact_p(12, 36, 6, 36)

    assert p_value == pytest.approx(0.1727, abs=1e-4)
    assert p_value == bench.fisher_exact_p(6, 36, 12, 36)


def test_there_is_no_p_value_for_a_set_without_games():
    assert bench.fisher_exact_p(0, 0, 3, 5) is None
    assert bench.fisher_exact_p(2, 5, 0, 0) is None


def test_the_comparison_prints_both_totals_blocks_then_the_differences_and_the_p_values():
    label_a = _games("rush", 4, 18) + _games("normal", 2, 18)
    label_b = _games("rush", 9, 18) + _games("normal", 3, 18)
    label_a[0].row.update(kd_value=1000, compute_ms_p95=0.5)
    label_b[0].row.update(kd_value=2500, compute_ms_p95=0.75)
    label_b[-1].row.update(income_per_s=40.0)

    lines = bench.compare_lines("runs/bench/a", label_a, "runs/bench/b", label_b)

    block_a, block_b, differences = "\n".join(lines).split("\n\n")
    assert block_a.splitlines() == ["A,runs/bench/a", *bench.totals_lines(label_a)]
    assert block_b.splitlines() == ["B,runs/bench/b", *bench.totals_lines(label_b)]
    assert block_a.splitlines()[2:5] == ["all,36,6,30,0,0", "rush,18,4,14,0,0", "normal,18,2,16,0,0"]
    assert differences.splitlines() == [
        "wins,all,rush,normal",
        "B - A,6,5,1",
        f"fisher_p_two_sided,{bench.fisher_exact_p(6, 36, 12, 36):.2f},{bench.fisher_exact_p(4, 18, 9, 18):.2f},1.00",
        bench.totals_lines([])[-2],
        "B - A,1500,-,-,-,-,-,-,0.25",
    ]


def test_the_comparison_of_12_wins_of_36_against_6_prints_0_17_for_all_games():
    label_a = _games("rush", 6, 18) + _games("normal", 6, 18)
    label_b = _games("rush", 3, 18) + _games("normal", 3, 18)

    significance = bench.compare_lines("a", label_a, "b", label_b)[-3]

    assert significance.split(",")[:2] == ["fisher_p_two_sided", "0.17"]


def test_an_opponent_only_one_label_played_has_no_p_value():
    lines = bench.compare_lines("a", _games("rush", 1, 2), "b", _games("rush", 1, 2) + _games("turtle", 1, 1))

    assert lines[-5:-2] == ["wins,all,rush,normal,turtle", "B - A,1,0,0,1", "fisher_p_two_sided,1.00,1.00,-,-"]


# -- the report tool's benchmark modes -------------------------------------------------------------


@pytest.fixture(scope="module")
def report(load_script):
    """The report script, imported once."""
    return load_script("report.py")


@pytest.fixture
def bench_root(run_folder) -> Path:
    """A temporary project root holding the item table, as a checkout does."""
    items_file = run_folder / ITEMS_FILE
    items_file.parent.mkdir(parents=True)
    items_file.write_bytes((REPO_ROOT / ITEMS_FILE).read_bytes())
    return run_folder


def _files_under(folder: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


def _two_game_label(folder: Path) -> Path:
    write_game(folder, "rush-england-sidestep-401", FULL_RECORD)
    write_game(folder, "normal-england-sidestep-419", FULL_RECORD, winner="Multi1")
    return folder


def test_bench_prints_the_table_to_standard_output_and_writes_no_file(bench_root, run_script, unit_cost):
    label = _two_game_label(bench_root / "bench" / "label")
    before = _files_under(bench_root)

    completed = run_script(bench_root, "report.py", "--bench", str(label))

    assert (completed.returncode, completed.stderr) == (0, "")
    assert completed.stdout.splitlines() == bench.bench_lines(bench.read_label(label, unit_cost))
    assert "rush-england-sidestep,401,win,victory,5000,12000,2700,50,3,2,10,4" in completed.stdout.splitlines()
    assert completed.stdout.splitlines()[-1] == "goal: not met"
    assert max(len(line) for line in completed.stdout.splitlines()) <= 160
    assert _files_under(bench_root) == before


def test_bench_compare_prints_both_totals_blocks_and_the_differences(bench_root, report, capsys):
    label_a = _two_game_label(bench_root / "a")
    label_b = bench_root / "b"
    write_game(label_b, "rush-england-sidestep-401", FULL_RECORD, winner="Multi1")
    write_game(label_b, "normal-england-sidestep-419", winner="Multi0")
    before = _files_under(bench_root)

    exit_code = report.main(["--bench-compare", str(label_a), str(label_b)])

    printed = capsys.readouterr().out.splitlines()
    assert exit_code == 0
    assert printed[0] == f"A,{label_a}" and f"B,{label_b}" in printed
    assert printed.count("outcomes,games,win,loss,draw,no_result") == 2
    assert printed[2:5] == ["all,2,1,1,0,0", "rush,1,1,0,0,0", "normal,1,0,1,0,0"]
    assert printed[-5:-3] == ["wins,all,rush,normal", "B - A,0,-1,1"]
    assert printed[-3] == "fisher_p_two_sided,1.00,1.00,1.00"
    assert printed[-1] == "B - A,0,0,0,0,0,0,0,0"
    assert _files_under(bench_root) == before


def test_bench_of_a_path_that_is_no_folder_ends_with_exit_code_2(bench_root, report, capsys):
    exit_code = report.main(["--bench", str(bench_root / "missing")])

    captured = capsys.readouterr()
    assert (exit_code, captured.out) == (2, "")
    assert captured.err.startswith(f"{bench_root / 'missing'} is not a folder")


def test_bench_of_a_folder_that_is_no_label_ends_with_exit_code_2(bench_root, report, capsys):
    (bench_root / "label" / "notes").mkdir(parents=True)

    exit_code = report.main(["--bench", str(bench_root / "label")])

    assert exit_code == 2
    assert "is not a benchmark label folder" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--bench", "a", "runs/20261016"],
        ["--bench", "a", "--bench-compare", "a", "b"],
        ["--bench", "a", "--compare", "x", "y"],
    ],
    ids=["with a path", "with bench-compare", "with compare"],
)
def test_a_bench_mode_with_another_mode_is_a_usage_error(run_folder, report, arguments):
    with pytest.raises(SystemExit) as stopped:
        report.main(arguments)

    assert stopped.value.code == 2
