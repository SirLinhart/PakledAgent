"""Post-run metrics: reading match records, the metrics every game has, and the figures built into the fixture."""

from __future__ import annotations

import json
import math
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from ucla_agent.core import metrics, stats
from ucla_agent.core.record_files import events_file_name, meta_file_name

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "records" / "redalert_synthetic"
RECORD_START = datetime(2026, 10, 12, 18, 0, 0, tzinfo=timezone.utc)
AGENT_ID = "agent-1"
WIN = {"returns": {AGENT_ID: 1.0, "agent-2": 0.0}, "termination_reason": "victory"}
LOSS = {"returns": {AGENT_ID: 0.0, "agent-2": 1.0}, "termination_reason": "victory"}
Event = tuple[str, dict[str, Any]]


def write_attempt(
    folder: Path,
    events: list[Event],
    *,
    attempt: int = 1,
    meta: dict[str, Any] | None = None,
    starts_after_s: int = 0,
) -> None:
    """Write one attempt's events file and meta file into a match folder: one event per second from a fixed start."""
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "seq": seq,
                "t": _timestamp(starts_after_s + seq),
                "mono_ms": 1000 * seq,
                "kind": kind,
                "data": data,
            }
        )
        for seq, (kind, data) in enumerate(events)
    ]
    (folder / events_file_name(attempt)).write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    content = {"session_id": "session-1", "game_type": "werewolf", "agent_id": AGENT_ID, "attempt": attempt}
    (folder / meta_file_name(attempt)).write_text(json.dumps({**content, **(meta or {})}), encoding="utf-8")


def _timestamp(seconds: int) -> str:
    moment = RECORD_START + timedelta(seconds=seconds)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _nearest_rank(values: list[float], percent: int) -> float:
    """The nearest-rank percentile, written out independently of the module under test."""
    ordered = sorted(values)
    return ordered[math.ceil(percent * len(ordered) / 100) - 1]


def _metrics_of(folder: Path) -> dict[str, Any]:
    return metrics.compute_metrics(metrics.load_match(folder))


@pytest.fixture(scope="module")
def fixture_match() -> metrics.Match:
    return metrics.load_match(FIXTURE)


@pytest.fixture(scope="module")
def fixture_metrics(fixture_match) -> dict[str, Any]:
    return metrics.compute_metrics(fixture_match)


# -- reading a match folder ------------------------------------------------------------------------


def test_the_fixture_is_read_as_one_attempt_of_two_hundred_events(fixture_match):
    (attempt,) = fixture_match.attempts

    assert (attempt.number, len(attempt.events), attempt.unreadable_lines) == (1, 200, 0)
    assert attempt.meta["session_id"] == "redalert-synthetic"
    assert len(fixture_match.data_of("state")) == 100


def test_attempts_are_read_in_attempt_order_each_with_its_meta(tmp_path):
    for attempt in (10, 1, 2):
        write_attempt(tmp_path, [("state", {"attempt": attempt})], attempt=attempt)

    match = metrics.load_match(tmp_path)

    assert [attempt.number for attempt in match.attempts] == [1, 2, 10]
    assert [attempt.meta["attempt"] for attempt in match.attempts] == [1, 2, 10]
    assert [data["attempt"] for data in match.data_of("state")] == [1, 2, 10]
    assert [event["kind"] for event in match.events] == ["state", "state", "state"]


def test_a_line_that_is_not_an_event_is_skipped_and_counted(tmp_path):
    write_attempt(tmp_path, [("state", {"state_version": 1}), ("state", {"state_version": 2})])
    events_path = tmp_path / "events.jsonl"
    cut_off = '{"seq": 2, "t": "2026-10-12T18:00:02.000Z", "mono_ms": 2000, "kind": "sta'
    events_path.write_text(events_path.read_text(encoding="utf-8") + '[1, 2]\n{"kind": 5}\n\n' + cut_off, "utf-8")

    (attempt,) = metrics.load_match(tmp_path).attempts

    assert [event["data"]["state_version"] for event in attempt.events] == [1, 2]
    assert attempt.unreadable_lines == 3
    assert _metrics_of(tmp_path)["unreadable_lines"] == 3


def test_a_folder_without_an_events_file_is_not_a_match_record(tmp_path):
    (tmp_path / "metrics.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="holds no match record"):
        metrics.load_match(tmp_path)


@pytest.mark.parametrize("meta_text", [None, "{not json", "[1, 2]"], ids=["missing", "not json", "not an object"])
def test_a_meta_file_that_cannot_be_used_reads_as_empty(tmp_path, meta_text):
    write_attempt(tmp_path, [("result", {"result": WIN})])
    (tmp_path / "meta.json").unlink()
    if meta_text is not None:
        (tmp_path / "meta.json").write_text(meta_text, encoding="utf-8")

    match = metrics.load_match(tmp_path)

    assert match.attempts[0].meta == {}
    assert metrics.compute_metrics(match)["session_id"] is None


def test_other_files_in_the_folder_are_not_attempts(tmp_path):
    write_attempt(tmp_path, [("state", {})])
    for name in ("metrics.json", "events-backup.jsonl", "events.1.jsonl", "notes.txt"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    assert [attempt.number for attempt in metrics.load_match(tmp_path).attempts] == [1]


# -- percentiles -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "values, expected",
    [
        (list(range(1, 101)), {"p50": 50, "p95": 95, "p99": 99}),
        ([50, 15, 40, 20, 35], {"p50": 35, "p95": 50, "p99": 50}),
        ([7.5], {"p50": 7.5, "p95": 7.5, "p99": 7.5}),
        ([], {"p50": None, "p95": None, "p99": None}),
        ([3, None, "fast", True, 1], {"p50": 1, "p95": 3, "p99": 3}),
    ],
    ids=["one to a hundred", "five unsorted values", "one value", "no value", "only the numbers count"],
)
def test_percentiles_use_the_nearest_rank_method(values, expected):
    assert stats.percentiles(values) == expected


def test_a_percentile_is_always_one_of_the_recorded_values():
    values = [2.5, 9.0, 4.25, 7.75]

    assert all(stats.percentile(values, percent) in values for percent in range(1, 101))
    assert stats.percentile(values, 25) == 2.5 and stats.percentile(values, 26) == 4.25


# -- the metrics every game has, on the fixture ----------------------------------------------------


def test_the_fixture_is_a_win_by_time_limit_played_in_one_attempt(fixture_metrics):
    assert (fixture_metrics["outcome"], fixture_metrics["termination_reason"]) == ("win", "time_limit")
    assert (fixture_metrics["attempts"], fixture_metrics["duration_s"]) == (1, 199.0)
    assert (fixture_metrics["session_id"], fixture_metrics["game_type"]) == ("redalert-synthetic", "red_alert")
    assert (fixture_metrics["context"], fixture_metrics["agent_id"], fixture_metrics["seat"]) == (
        "testing",
        "agent-synthetic",
        "Multi0",
    )


def test_the_fixture_decisions_are_counted_by_source_with_their_latencies(fixture_metrics):
    assert fixture_metrics["decisions_total"] == 4
    assert fixture_metrics["decisions_by_source"] == {"local:fast": 3, "jev": 1}
    assert fixture_metrics["decision_latency_ms"] == {
        "jev": {"p50": 80, "p95": 80, "p99": 80},
        "local:fast": {"p50": 900, "p95": 1500, "p99": 1500},
    }
    assert fixture_metrics["stale_discarded_count"] == 1


def test_the_fixture_has_three_fallbacks_one_handled_error_and_no_dropped_event(fixture_metrics):
    assert fixture_metrics["fallback_count"] == 3
    assert fixture_metrics["fallback_by_reason"] == {"timeout": 1, "error": 1, "stale_discarded": 1}
    assert (fixture_metrics["error_count"], fixture_metrics["unhandled_error_count"]) == (1, 0)
    assert (fixture_metrics["dropped_events"], fixture_metrics["unreadable_lines"]) == (0, 0)


def test_compute_ms_percentiles_are_over_every_action_of_the_fixture(fixture_match, fixture_metrics):
    compute_times = [action["compute_ms"] for action in fixture_match.data_of("action")]

    assert len(compute_times) == 42
    assert fixture_metrics["compute_ms"] == {
        "p50": _nearest_rank(compute_times, 50),
        "p95": _nearest_rank(compute_times, 95),
        "p99": _nearest_rank(compute_times, 99),
    }


def test_the_attempt_facts_of_the_fixture_are_reported(fixture_metrics):
    assert fixture_metrics["commit_by_attempt"] == ["0" * 40]
    assert fixture_metrics["config_hashes_by_attempt"] == [{"config/agent.toml": "0" * 64}]
    assert fixture_metrics["commit_differs_between_attempts"] is False
    assert fixture_metrics["config_differs_between_attempts"] is False


# -- outcome ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result, outcome, termination_reason",
    [
        ({"returns": {AGENT_ID: 1.0, "agent-2": 0.0}, "termination_reason": "victory"}, "win", "victory"),
        ({"returns": {AGENT_ID: 0.0, "agent-2": 1.0}, "termination_reason": "resignation"}, "loss", "resignation"),
        ({"returns": {AGENT_ID: 0.5, "agent-2": 0.5}, "termination_reason": "draw"}, "draw", "draw"),
        ({"returns": {AGENT_ID: 1, "agent-2": 0}, "termination_reason": "time_limit"}, "win", "time_limit"),
        ({"returns": {}, "your_return": None, "termination_reason": "engine_crashed"}, "no_result", "engine_crashed"),
        ({"returns": {"someone-else": 1.0}, "your_return": 0.0}, "loss", "unknown"),
        ({"returns": {AGENT_ID: -1.0, "agent-2": 1.0}, "termination_reason": "wolves_win"}, "loss", "wolves_win"),
        ({"returns": {AGENT_ID: 1.0, "agent-2": -1.0}, "termination_reason": "village_wins"}, "win", "village_wins"),
        ({"returns": {"Player 3": 1.0}, "your_return": -1}, "loss", "unknown"),
        ({"returns": {AGENT_ID: -0.25, "agent-2": -3.0}}, "loss", "unknown"),
        ({"returns": {AGENT_ID: 0.25, "agent-2": 0.1, "agent-3": "n/a"}}, "win", "unknown"),
        ({"returns": {AGENT_ID: 3, "agent-2": 3}}, "win", "unknown"),
        ({"returns": {AGENT_ID: 0.25}}, "win", "unknown"),
        ({"returns": {AGENT_ID: 0.25, "agent-2": 0.75}}, "draw", "unknown"),
        ({"returns": {"someone-else": 2.0}, "your_return": 1.5}, "draw", "unknown"),
        ({"returns": {AGENT_ID: 2.0, "agent-2": 1.0}}, "win", "unknown"),
        ({"returns": {AGENT_ID: 0.5, "agent-2": 0.0}}, "draw", "unknown"),
        ({"returns": {AGENT_ID: "lost"}}, "no_result", "unknown"),
        ({"returns": {AGENT_ID: True}}, "no_result", "unknown"),
        ({"returns": {AGENT_ID: math.nan}}, "no_result", "unknown"),
        ({}, "no_result", "unknown"),
        ("not an object", "no_result", "unknown"),
    ],
    ids=[
        "win",
        "loss",
        "draw",
        "integer returns",
        "failed match",
        "your_return when the agent is not named",
        "a negative return is a loss",
        "a positive return of 1 on a signed scale is a win",
        "a negative your_return is a loss",
        "a negative return is a loss even when it is the highest",
        "a positive return no seat beats is a win, text returns ignored",
        "a positive return tied for the highest is a win",
        "the only positive return is a win",
        "a positive return another seat beats is a draw",
        "a positive your_return another seat beats is a draw",
        "a return above 1 that no seat beats is a win",
        "a return of 0.5 is a draw even when no seat beats it",
        "a return that is text is no result",
        "a return that is a boolean is no result",
        "a return that is NaN is no result",
        "empty result",
        "result that is not an object",
    ],
)
def test_the_outcome_is_this_agents_return_in_the_result(tmp_path, result, outcome, termination_reason):
    write_attempt(tmp_path, [("state", {}), ("result", {"result": result, "dropped_events": 0})])

    computed = _metrics_of(tmp_path)

    assert (computed["outcome"], computed["termination_reason"]) == (outcome, termination_reason)


@pytest.mark.parametrize(
    "ending",
    [[("match_end", {"reason": "process_exit", "dropped_events": 0})], []],
    ids=["ends in match_end", "ends in nothing"],
)
def test_a_record_with_no_result_event_is_no_result_with_an_unknown_reason(tmp_path, ending):
    write_attempt(tmp_path, [("state", {}), ("ledger_summary", {"orders_sent": 3}), *ending])

    computed = _metrics_of(tmp_path)

    assert (computed["outcome"], computed["termination_reason"]) == ("no_result", "unknown")


def test_an_events_file_with_no_event_gives_metrics_without_raising(tmp_path):
    write_attempt(tmp_path, [])

    computed = _metrics_of(tmp_path)

    assert (computed["outcome"], computed["duration_s"], computed["dropped_events"]) == ("no_result", None, None)
    assert computed["compute_ms"] == {"p50": None, "p95": None, "p99": None}
    assert (computed["decisions_total"], computed["fallback_count"], computed["error_count"]) == (0, 0, 0)


def test_the_duration_ignores_an_event_whose_time_cannot_be_read(tmp_path):
    write_attempt(tmp_path, [("state", {}), ("state", {}), ("state", {}), ("state", {})])
    events_path = tmp_path / "events.jsonl"
    lines = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    lines[0]["t"] = "at some point"
    del lines[3]["t"]
    events_path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")

    assert _metrics_of(tmp_path)["duration_s"] == 1.0


# -- a match played in several attempts ------------------------------------------------------------


def test_the_outcome_of_a_resumed_match_is_the_result_of_its_last_attempt(tmp_path):
    first = [
        ("state", {}),
        ("error", {"type": "unhandled"}),
        ("match_end", {"reason": "process_exit", "dropped_events": 2}),
    ]
    second = [("state", {}), ("error", {"type": "handled"}), ("result", {"result": WIN, "dropped_events": 1})]
    write_attempt(tmp_path, first, meta={"commit": "aaa", "config_hashes": {"config/agent.toml": "1"}})
    write_attempt(
        tmp_path,
        second,
        attempt=2,
        starts_after_s=60,
        meta={"commit": "bbb", "config_hashes": {"config/agent.toml": "1"}},
    )

    computed = _metrics_of(tmp_path)

    assert (computed["attempts"], computed["outcome"], computed["termination_reason"]) == (2, "win", "victory")
    assert computed["commit_by_attempt"] == ["aaa", "bbb"]
    assert (computed["commit_differs_between_attempts"], computed["config_differs_between_attempts"]) == (True, False)
    assert (computed["error_count"], computed["unhandled_error_count"], computed["dropped_events"]) == (2, 1, 3)
    assert computed["duration_s"] == 62.0


def test_a_later_attempt_without_a_result_does_not_hide_an_earlier_result(tmp_path):
    write_attempt(tmp_path, [("result", {"result": WIN, "dropped_events": 0})])
    write_attempt(tmp_path, [("state", {}), ("match_end", {"reason": "process_exit"})], attempt=2, starts_after_s=30)

    computed = _metrics_of(tmp_path)

    assert (computed["attempts"], computed["outcome"]) == (2, "win")


# -- Red Alert metrics, on the fixture -------------------------------------------------------------


def test_the_fixture_reports_the_values_built_into_it(fixture_metrics):
    assert fixture_metrics["outcome"] == "win"
    assert (fixture_metrics["batches_sent"], fixture_metrics["batches_accepted"]) == (40, 38)
    assert fixture_metrics["orders_rejected_by_reason"] == {"not_available": 2, "not_ready": 1}
    assert (fixture_metrics["orders_lost"], fixture_metrics["duplicate_production_orders"]) == (1, 0)
    assert fixture_metrics["fallback_count"] == 3
    assert (fixture_metrics["low_power_seconds"], fixture_metrics["ready_unplaced_seconds"]) == (12.0, 4.0)
    assert fixture_metrics["final_trade_balance"] == 1500
    assert [step["plan"] for step in fixture_metrics["plan_sequence"]][:3] == ["OPENING", "HOLD", "RAID"]


def test_cycle_latency_p95_is_the_nearest_rank_of_compute_plus_round_trip_per_cycle(fixture_match, fixture_metrics):
    cycles, compute_ms = [], None
    for event in fixture_match.events:
        if event["kind"] == "action":
            compute_ms = event["data"]["compute_ms"]
        elif event["kind"] == "action_result":
            cycles.append(compute_ms + event["data"]["round_trip_ms"])

    assert len(cycles) == 40
    assert abs(fixture_metrics["cycle_latency_ms"]["p95"] - _nearest_rank(cycles, 95)) <= 0.5
    assert fixture_metrics["cycle_latency_ms"] == {
        "p50": _nearest_rank(cycles, 50),
        "p95": _nearest_rank(cycles, 95),
        "p99": _nearest_rank(cycles, 99),
    }
    assert fixture_metrics["cycle_latency_ms"]["p99"] == 908.0


def test_the_fixture_throughput_and_round_trip(fixture_match, fixture_metrics):
    round_trips = [result["round_trip_ms"] for result in fixture_match.data_of("action_result")]

    assert (fixture_metrics["playing_seconds"], fixture_metrics["batches_per_second"]) == (198.0, round(38 / 198, 4))
    assert fixture_metrics["round_trip_ms"] == {
        "p50": _nearest_rank(round_trips, 50),
        "p95": _nearest_rank(round_trips, 95),
        "p99": 900.0,
    }
    assert fixture_metrics["ticks_per_call"] == {"mean": 5.5, "max": 12}


def test_the_fixture_ledger_counters_are_those_of_its_last_summary(fixture_metrics):
    assert (fixture_metrics["orders_sent"], fixture_metrics["dropped_invalid"]) == (118, 2)


def test_the_fixture_economy_figures(fixture_metrics):
    assert (fixture_metrics["zero_harvester_seconds"], fixture_metrics["harvesters_lost"]) == (40.0, 1)
    assert (fixture_metrics["ore_capped_seconds"], fixture_metrics["zero_spendable_seconds"]) == (38.0, 8.0)
    assert fixture_metrics["idle_production_seconds"] == {
        "Building": 60.0,
        "Defense": 120.0,
        "Infantry": 30.0,
        "Vehicle": 80.0,
    }


def test_the_fixture_trade_balance_is_sampled_every_thirty_game_seconds(fixture_metrics):
    series = fixture_metrics["trade_balance_series"]

    assert [point["game_seconds"] for point in series] == [0, 30, 60, 90, 120, 150, 180]
    assert [point["trade_balance"] for point in series] == [0, 0, 0, 0, 300, 100, 1500]
    assert fixture_metrics["final_assets_value"] == 5960


def test_the_fixture_build_times_plans_and_match_facts(fixture_metrics):
    assert (fixture_metrics["time_first_defense_s"], fixture_metrics["time_war_factory_s"]) == (50.0, 70.0)
    assert fixture_metrics["plan_sequence"] == [
        {"plan": "OPENING", "game_seconds": 0.0},
        {"plan": "HOLD", "game_seconds": 70.0},
        {"plan": "RAID", "game_seconds": 120.0},
    ]
    assert fixture_metrics["plan_time_share"] == {"OPENING": 0.3535, "HOLD": 0.2525, "RAID": 0.3939}
    assert (fixture_metrics["faction"], fixture_metrics["enemy_faction"], fixture_metrics["map_id"]) == (
        "england",
        "russia",
        "sidestep",
    )


# -- Red Alert metrics, on small records -----------------------------------------------------------

RED_ALERT = {"game_type": "red_alert"}
RED_ALERT_ONLY_KEYS = {"batches_sent", "orders_sent", "low_power_seconds", "plan_sequence", "map_id"}


def ra_state(
    tick: int,
    *,
    full: bool = True,
    cash: int = 5000,
    ore: tuple[int, int] | None = None,
    power: tuple[int, int] = (100, 20),
    harvesters: int = 1,
    costs: tuple[int, int] = (0, 0),
    production: tuple[tuple[str, bool], ...] = (),
    buildings: tuple[str, ...] = (),
    phase: str = "playing",
) -> Event:
    """A Red Alert `state` event: `power` is (provided, drained), `costs` (kills, deaths), `production` (queue, ready).

    `ore` is (ore, resource_capacity); without it the economy names neither.
    """
    economy = {"cash": cash, "power_provided": power[0], "power_drained": power[1], "harvesters": harvesters}
    if ore is not None:
        economy.update(ore=ore[0], resource_capacity=ore[1])
    military = {"kills_cost": costs[0], "deaths_cost": costs[1], "assets_value": 1000 + tick}
    if not full:
        return "state", {"state_version": tick, "phase": phase, "economy": economy, "military": military}
    observation = {
        "faction": "russia",
        "enemy_faction": "france",
        "economy": economy,
        "military": military,
        "production": [{"queue": queue, "item": "item", "ready": ready} for queue, ready in production],
        "buildings": [{"id": 300 + position, "type": name} for position, name in enumerate(buildings)],
        "map": {"id": "trapped"},
    }
    return "state", {"state_version": tick, "phase": phase, "state": {"phase": phase, "observation": observation}}


def _ledger(orders_sent: int, rejected: dict[str, int] | None = None) -> Event:
    summary = {
        "orders_sent": orders_sent,
        "orders_rejected_by_reason": rejected or {},
        "orders_lost": 1,
        "dropped_invalid": 0,
        "duplicate_production_orders": 0,
    }
    return "ledger_summary", summary


def test_a_match_in_two_attempts_adds_up_each_attempts_last_ledger_summary(tmp_path):
    first = [ra_state(0), _ledger(4), _ledger(10, {"not_ready": 1}), ("match_end", {"reason": "process_exit"})]
    second = [ra_state(900), _ledger(7, {"not_ready": 2, "invalid": 1}), ("result", {"result": WIN})]
    write_attempt(tmp_path, first, meta=RED_ALERT)
    write_attempt(tmp_path, second, attempt=2, starts_after_s=40, meta=RED_ALERT)

    computed = _metrics_of(tmp_path)

    assert (computed["attempts"], computed["outcome"], computed["orders_sent"]) == (2, "win", 17)
    assert computed["orders_rejected_by_reason"] == {"not_ready": 3, "invalid": 1}
    assert computed["orders_lost"] == 2


def test_a_record_with_no_ledger_summary_has_null_ledger_counters(tmp_path):
    write_attempt(tmp_path, [ra_state(0), ra_state(50), ("result", {"result": WIN})], meta=RED_ALERT)

    computed = _metrics_of(tmp_path)

    assert computed["orders_lost"] is None
    assert [computed[counter] for counter in ("orders_sent", "dropped_invalid", "duplicate_production_orders")] == [
        None,
        None,
        None,
    ]
    assert computed["orders_rejected_by_reason"] is None
    assert computed["outcome"] == "win"


def test_a_match_of_another_game_has_no_red_alert_metrics(tmp_path):
    write_attempt(tmp_path, [("state", {"state_version": 1, "phase": "moving"}), ("result", {"result": WIN})])

    assert RED_ALERT_ONLY_KEYS.isdisjoint(_metrics_of(tmp_path))


def test_a_red_alert_record_without_a_state_leaves_state_figures_null(tmp_path):
    write_attempt(tmp_path, [_ledger(0), ("result", {"result": WIN})], meta=RED_ALERT)

    computed = _metrics_of(tmp_path)

    state_figures = ("low_power_seconds", "zero_harvester_seconds", "harvesters_lost", "ready_unplaced_seconds")
    assert [computed[name] for name in state_figures] == [None, None, None, None]
    assert (computed["idle_production_seconds"], computed["playing_seconds"], computed["batches_per_second"]) == (
        None,
        None,
        None,
    )
    assert (computed["trade_balance_series"], computed["final_trade_balance"], computed["final_assets_value"]) == (
        [],
        None,
        None,
    )
    assert (computed["faction"], computed["map_id"], computed["time_war_factory_s"]) == (None, None, None)
    assert (computed["plan_sequence"], computed["plan_time_share"]) == ([], {})
    assert computed["ticks_per_call"] == {"mean": None, "max": None}


def test_states_of_all_attempts_are_merged_by_tick(tmp_path):
    first = [ra_state(0, power=(100, 150)), ra_state(250, power=(100, 150), harvesters=2)]
    second = [ra_state(1000, power=(100, 20), harvesters=1), ra_state(1250, power=(100, 150))]
    write_attempt(tmp_path, second, attempt=2, starts_after_s=40, meta=RED_ALERT)
    write_attempt(tmp_path, first, meta=RED_ALERT)

    computed = _metrics_of(tmp_path)

    assert computed["low_power_seconds"] == 40.0
    assert (computed["playing_seconds"], computed["harvesters_lost"]) == (50.0, 1)


def test_thinned_states_count_for_power_and_harvesters_but_not_for_queues(tmp_path):
    states = [ra_state(tick, full=False, power=(100, 130), harvesters=0) for tick in (0, 100, 200)]
    write_attempt(tmp_path, states, meta=RED_ALERT)

    computed = _metrics_of(tmp_path)

    assert (computed["low_power_seconds"], computed["zero_harvester_seconds"]) == (8.0, 8.0)
    assert (computed["ready_unplaced_seconds"], computed["idle_production_seconds"]) == (None, None)
    assert (computed["time_first_defense_s"], computed["faction"]) == (None, None)


def test_states_outside_play_are_not_measured(tmp_path):
    waiting = ("state", {"state_version": 0, "phase": "waiting_for_agents", "state": {"observation": None}})
    states = [waiting, waiting, ra_state(500, power=(100, 150)), ra_state(750), ("state", {"phase": "playing"})]
    write_attempt(tmp_path, states, meta=RED_ALERT)

    computed = _metrics_of(tmp_path)

    assert (computed["playing_seconds"], computed["low_power_seconds"]) == (10.0, 10.0)


def test_a_ready_item_counts_only_in_a_queue_that_needs_placing(tmp_path):
    states = [
        ra_state(0, production=(("Infantry", True),)),
        ra_state(50, production=(("Defense", True), ("Building", False))),
        ra_state(150, production=(("Building", True),)),
        ra_state(175, production=(("Building", False),)),
    ]
    write_attempt(tmp_path, states, meta=RED_ALERT)

    assert _metrics_of(tmp_path)["ready_unplaced_seconds"] == 5.0


def test_a_queue_is_idle_only_while_empty_with_at_least_1500_cash(tmp_path):
    states = [
        ra_state(0, cash=1500),
        ra_state(100, cash=1499),
        ra_state(200, cash=3000, production=(("Infantry", False),)),
        ra_state(300, cash=3000),
    ]
    write_attempt(tmp_path, states, meta=RED_ALERT)

    assert _metrics_of(tmp_path)["idle_production_seconds"] == {
        "Building": 8.0,
        "Defense": 8.0,
        "Infantry": 4.0,
        "Vehicle": 8.0,
    }


def test_a_queue_is_idle_while_empty_with_at_least_1500_in_cash_and_ore_together(tmp_path):
    states = [
        ra_state(0, cash=0, ore=(1500, 2000)),
        ra_state(100, cash=1000, ore=(499, 2000)),
        ra_state(200, cash=700, ore=(800, 2000), production=(("Infantry", False), ("Vehicle", False))),
        ra_state(300, cash=0, ore=(0, 0)),
    ]
    write_attempt(tmp_path, states, meta=RED_ALERT)

    assert _metrics_of(tmp_path)["idle_production_seconds"] == {
        "Building": 8.0,
        "Defense": 8.0,
        "Infantry": 4.0,
        "Vehicle": 4.0,
    }


def test_ore_capped_seconds_count_the_time_the_refineries_held_all_the_ore_they_can(tmp_path):
    states = [
        ra_state(0, ore=(0, 0)),
        ra_state(100, full=False, ore=(1999, 2000)),
        ra_state(200, full=False, ore=(2000, 2000)),
        ra_state(300, ore=(4000, 4000)),
        ra_state(400, ore=(3000, 4000)),
        ra_state(500, ore=(0, 0)),
        ra_state(600),
    ]
    write_attempt(tmp_path, states, meta=RED_ALERT)

    assert _metrics_of(tmp_path)["ore_capped_seconds"] == 8.0


def test_zero_spendable_seconds_count_the_time_with_neither_cash_nor_ore(tmp_path):
    states = [
        ra_state(0, cash=0, ore=(0, 2000)),
        ra_state(100, full=False, cash=0, ore=(0, 2000)),
        ra_state(200, cash=0, ore=(5, 2000)),
        ra_state(300, cash=0),
        ra_state(400, cash=1, ore=(0, 0)),
        ra_state(500, cash=0, ore=(0, 0)),
    ]
    write_attempt(tmp_path, states, meta=RED_ALERT)

    assert _metrics_of(tmp_path)["zero_spendable_seconds"] == 12.0


def test_without_states_the_money_figures_are_null(tmp_path):
    write_attempt(tmp_path, [_ledger(0), ("result", {"result": WIN})], meta=RED_ALERT)

    computed = _metrics_of(tmp_path)

    assert (computed["ore_capped_seconds"], computed["zero_spendable_seconds"]) == (None, None)


def test_only_actions_that_carry_orders_are_batches(tmp_path):
    orders = {"type": "orders", "orders": [{"cmd": "deploy", "units": [1]}]}
    events = [
        ("action", {"action": orders, "compute_ms": 4.0}),
        ("action_result", {"accepted": True, "round_trip_ms": 200.0}),
        ("action", {"action": "WAIT", "compute_ms": 1.0}),
        ("action", {"action": {"type": "orders", "orders": []}, "compute_ms": 1.0}),
        ("action", {"action": orders, "compute_ms": 6.0}),
        ("action_result", {"accepted": False, "round_trip_ms": 300.0}),
    ]
    write_attempt(tmp_path, events, meta=RED_ALERT)

    computed = _metrics_of(tmp_path)

    assert (computed["batches_sent"], computed["batches_accepted"]) == (2, 1)
    assert computed["cycle_latency_ms"] == {"p50": 204.0, "p95": 306.0, "p99": 306.0}


def test_a_cycle_needs_an_action_and_its_own_result(tmp_path):
    events = [
        ("action_result", {"accepted": True, "round_trip_ms": 111.0}),
        ("action", {"action": "WAIT", "compute_ms": 5.0}),
        ("action_result", {"accepted": True, "round_trip_ms": 100.0}),
        ("action_result", {"accepted": True, "round_trip_ms": 222.0}),
        ("action", {"action": "WAIT", "compute_ms": None}),
        ("action_result", {"accepted": True, "round_trip_ms": 333.0}),
        ("action", {"action": "WAIT", "compute_ms": 9.0}),
    ]
    write_attempt(tmp_path, events, meta=RED_ALERT)

    computed = _metrics_of(tmp_path)

    assert computed["cycle_latency_ms"] == {"p50": 105.0, "p95": 105.0, "p99": 105.0}
    assert computed["round_trip_ms"]["p99"] == 333.0


def test_ticks_per_call_is_weighted_by_the_ticks_each_timing_window_covers(tmp_path):
    def timing(from_tick, to_tick, mean, maximum):
        return "timing", {"from_tick": from_tick, "to_tick": to_tick, "ticks_per_call": {"mean": mean, "max": maximum}}

    events = [
        timing(0, 250, 4.0, 7),
        timing(250, 500, 6.0, 15),
        timing(500, 600, 10.0, 11),
        ("timing", {"from_tick": 9}),
    ]
    write_attempt(tmp_path, events, meta=RED_ALERT)

    assert _metrics_of(tmp_path)["ticks_per_call"] == {
        "mean": round((4.0 * 250 + 6.0 * 250 + 10.0 * 100) / 600, 3),
        "max": 15,
    }


def production_timing(income: float | None, utilization: dict[str, Any] | None = None) -> Event:
    """A `timing` event of a window, with the production figures the agent adds to it."""
    return "timing", {"from_tick": 0, "to_tick": 250, "income_per_s": income, "utilization": utilization or {}}


def test_the_income_is_the_mean_and_the_last_of_the_timing_events_that_give_it(tmp_path):
    events = [
        production_timing(None),
        production_timing(40.0, {"Infantry": 0.5, "Vehicle": 1.0}),
        production_timing(60.0, {"Infantry": 1.0, "Vehicle": None}),
        production_timing(None, {"Infantry": 0.0}),
        ("timing", {"from_tick": 9}),
    ]
    write_attempt(tmp_path, events, meta=RED_ALERT)

    computed = _metrics_of(tmp_path)

    assert computed["income_per_s"] == {"mean": 50.0, "final": 60.0}
    assert computed["utilization"] == {"Building": None, "Defense": None, "Infantry": 0.5, "Vehicle": 1.0}


def test_without_production_figures_the_income_and_the_utilization_are_null(tmp_path):
    write_attempt(tmp_path, [ra_state(0), ("timing", {"from_tick": 0, "to_tick": 250})], meta=RED_ALERT)

    computed = _metrics_of(tmp_path)

    assert computed["income_per_s"] == {"mean": None, "final": None}
    assert set(computed["utilization"].values()) == {None}


def test_the_trade_balance_series_starts_at_the_first_sample_time_with_a_state(tmp_path):
    states = [ra_state(1000, costs=(500, 200)), ra_state(1600, costs=(900, 200)), ra_state(2300, costs=(900, 1000))]
    write_attempt(tmp_path, states, meta=RED_ALERT)

    computed = _metrics_of(tmp_path)

    assert computed["trade_balance_series"] == [
        {"game_seconds": 60, "trade_balance": 300},
        {"game_seconds": 90, "trade_balance": 700},
    ]
    assert (computed["final_trade_balance"], computed["final_assets_value"]) == (-100, 3300)


def test_build_times_are_the_first_full_state_that_lists_the_building(tmp_path):
    states = [
        ra_state(0, buildings=("fact",)),
        ra_state(100, full=False),
        ra_state(250, buildings=("fact", "powr", "FTUR")),
        ra_state(500, buildings=("fact", "powr", "ftur", "weap")),
    ]
    write_attempt(tmp_path, states, meta=RED_ALERT)

    computed = _metrics_of(tmp_path)

    assert (computed["time_first_defense_s"], computed["time_war_factory_s"]) == (10.0, 20.0)
    assert (computed["faction"], computed["enemy_faction"], computed["map_id"]) == ("russia", "france", "trapped")


def test_plan_time_shares_run_to_the_last_state_and_add_up_repeated_plans(tmp_path):
    def plan(game_seconds, new):
        return "plan_change", {"game_seconds": game_seconds, "old": None, "new": new, "rule": "R1"}

    events = [ra_state(0), plan(0.0, "OPENING"), plan(10.0, "HOLD"), plan(30.0, "OPENING"), ra_state(1000)]
    write_attempt(tmp_path, events, meta=RED_ALERT)

    computed = _metrics_of(tmp_path)

    assert [step["plan"] for step in computed["plan_sequence"]] == ["OPENING", "HOLD", "OPENING"]
    assert computed["plan_time_share"] == {"OPENING": 0.5, "HOLD": 0.5}


# -- the report tool -------------------------------------------------------------------------------

FIXTURE_IN_RUNS = Path("runs") / "20261001" / "redalert-synthetic__Multi0"
SUMMARY_HEADING = (
    "| Match | Seat | Game | Map | Faction | Enemy faction | Outcome | Final trade balance | Cycle latency p95 (ms) "
    "| Fallbacks | Rejected orders |"
)
FIXTURE_ROW = "| redalert-synthetic | Multi0 | red_alert | sidestep | england | russia | win | 1500 | 346.0 | 3 | 3 |"


@pytest.fixture(scope="module")
def report(load_script):
    """The report script, imported once."""
    return load_script("report.py")


@pytest.fixture
def recorded_fixture(run_folder) -> Path:
    """The synthetic record copied to where a run would have written it, under a temporary repository root."""
    shutil.copytree(FIXTURE, run_folder / FIXTURE_IN_RUNS)
    return run_folder / FIXTURE_IN_RUNS


def _report_text(root: Path) -> str:
    (report_path,) = root.glob("runs/reports/*/report.md")
    return report_path.read_text(encoding="utf-8")


def test_the_command_writes_metrics_json_with_the_values_built_into_the_fixture(
    recorded_fixture, run_folder, run_script
):
    completed = run_script(run_folder, "report.py", str(FIXTURE_IN_RUNS))

    written = json.loads((recorded_fixture / "metrics.json").read_text(encoding="utf-8"))
    assert (completed.returncode, completed.stderr) == (0, "")
    assert (written["outcome"], written["batches_sent"], written["batches_accepted"]) == ("win", 40, 38)
    assert written["orders_rejected_by_reason"] == {"not_available": 2, "not_ready": 1}
    assert (written["orders_lost"], written["duplicate_production_orders"], written["fallback_count"]) == (1, 0, 3)
    assert (written["low_power_seconds"], written["ready_unplaced_seconds"]) == (12.0, 4.0)
    assert written["final_trade_balance"] == 1500
    assert [step["plan"] for step in written["plan_sequence"]][:3] == ["OPENING", "HOLD", "RAID"]
    assert (written["session_id"], written["seat"]) == ("redalert-synthetic", "Multi0")
    assert written == json.loads(json.dumps(_metrics_of(recorded_fixture)))


def test_the_command_writes_one_report_with_a_row_per_match_and_totals_by_outcome(
    recorded_fixture, run_folder, run_script
):
    completed = run_script(run_folder, "report.py", str(FIXTURE_IN_RUNS))

    report_text = _report_text(run_folder)
    assert completed.stdout.splitlines()[0] == f"Wrote {FIXTURE_IN_RUNS / 'metrics.json'}"
    assert completed.stdout.splitlines()[1].startswith(f"Wrote {run_folder / 'runs' / 'reports'}")
    assert completed.stdout.splitlines()[1].endswith("report.md")
    assert report_text.startswith("# Match report\n\nGenerated ")
    assert "from 1 match record(s) of 1 game(s)." in report_text
    assert SUMMARY_HEADING in report_text and FIXTURE_ROW in report_text
    assert "## Totals by outcome" in report_text
    assert [f"| {outcome} | {count} |" in report_text for outcome, count in (("win", 1), ("loss", 0))] == [True, True]


def test_a_dated_folder_stands_for_every_match_recorded_that_day(recorded_fixture, run_folder, report, capsys):
    day_folder = recorded_fixture.parent
    write_attempt(day_folder / "werewolf-match", [("state", {}), ("match_end", {"reason": "process_exit"})])
    (day_folder / "run.jsonl").write_text("{}\n", encoding="utf-8")
    (day_folder / "not-a-match").mkdir()

    exit_code = report.main([str(day_folder)])

    report_text = _report_text(run_folder)
    assert exit_code == 0
    assert (day_folder / "werewolf-match" / "metrics.json").is_file() and (recorded_fixture / "metrics.json").is_file()
    assert not (day_folder / "not-a-match" / "metrics.json").exists()
    assert "from 2 match record(s) of 2 game(s)." in report_text
    assert FIXTURE_ROW in report_text
    assert "| session-1 | n/a | werewolf | n/a | n/a | n/a | no_result | n/a | n/a | 0 | n/a |" in report_text
    assert "| win | 1 |" in report_text and "| no_result | 1 |" in report_text
    assert len(capsys.readouterr().out.splitlines()) == 3


def test_running_the_report_twice_gives_identical_metrics_files(recorded_fixture, report):
    report.main([str(recorded_fixture)])
    first = (recorded_fixture / "metrics.json").read_bytes()
    report.main([str(recorded_fixture)])

    assert (recorded_fixture / "metrics.json").read_bytes() == first
    assert first.endswith(b"}\n") and b"\r" not in first
    assert list(json.loads(first)) == sorted(json.loads(first))


def _seat_record(day_folder: Path, session_id: str, seat: str, result: dict) -> Path:
    """Write the record of one seat of a Red Alert game into its own `<session id>__<seat>` folder."""
    folder = day_folder / f"{session_id}__{seat}"
    meta = {"session_id": session_id, "game_type": "red_alert", "seat": seat}
    write_attempt(folder, [ra_state(0), ("result", {"result": result})], meta=meta)
    return folder


def test_the_seats_of_one_game_are_reported_on_adjacent_rows_marked_as_the_same_game(run_folder, report):
    day_folder = run_folder / "runs" / "20261012"
    second_seat = _seat_record(day_folder, "selfplay-1", "Multi1", LOSS)
    other_game = _seat_record(day_folder, "other-9", "Multi0", WIN)
    first_seat = _seat_record(day_folder, "selfplay-1", "Multi0", WIN)

    exit_code = report.main([str(second_seat), str(other_game), str(first_seat)])

    report_text = _report_text(run_folder)
    rows = [line.strip("| ").split(" | ")[:2] for line in report_text.splitlines() if "| red_alert |" in line]
    outcomes = [line.split(" | ")[6] for line in report_text.splitlines() if "| red_alert |" in line]
    assert exit_code == 0
    assert rows == [
        ["selfplay-1 (same game: seat 1 of 2)", "Multi0"],
        ["selfplay-1 (same game: seat 2 of 2)", "Multi1"],
        ["other-9", "Multi0"],
    ]
    assert outcomes == ["win", "loss", "win"]
    assert "from 3 match record(s) of 2 game(s)." in report_text
    assert "| Outcome | Seats played |" in report_text
    assert "| win | 2 |" in report_text and "| loss | 1 |" in report_text


def test_each_seat_folder_gets_its_own_metrics_file_with_session_and_seat(run_folder, report):
    day_folder = run_folder / "runs" / "20261012"
    folders = [
        _seat_record(day_folder, "selfplay-1", seat, result) for seat, result in (("Multi0", WIN), ("Multi1", LOSS))
    ]

    report.main([str(day_folder)])

    written = [json.loads((folder / "metrics.json").read_text(encoding="utf-8")) for folder in folders]
    assert [(metrics_file["session_id"], metrics_file["seat"]) for metrics_file in written] == [
        ("selfplay-1", "Multi0"),
        ("selfplay-1", "Multi1"),
    ]
    assert [(metrics_file["outcome"], metrics_file["attempts"]) for metrics_file in written] == [
        ("win", 1),
        ("loss", 1),
    ]


def test_a_werewolf_loss_and_win_show_as_loss_and_win_in_metrics_and_report(run_folder, report):
    day_folder = run_folder / "runs" / "20261012"
    folders = []
    for seat, own_return in (("P1", -1.0), ("P2", 1.0)):
        folder = day_folder / f"werewolf-7__{seat}"
        result = {"returns": {"P1": -1.0, "P2": 1.0}, "your_return": own_return, "termination_reason": "game_over"}
        write_attempt(
            folder, [("state", {}), ("result", {"result": result})], meta={"session_id": "werewolf-7", "seat": seat}
        )
        folders.append(folder)

    exit_code = report.main([str(day_folder)])

    written = [json.loads((folder / "metrics.json").read_text(encoding="utf-8"))["outcome"] for folder in folders]
    report_text = _report_text(run_folder)
    outcomes = [line.split(" | ")[6] for line in report_text.splitlines() if "| werewolf |" in line]
    assert exit_code == 0
    assert written == ["loss", "win"]
    assert outcomes == ["loss", "win"]
    assert "| win | 1 |" in report_text and "| loss | 1 |" in report_text and "| no_result | 0 |" in report_text


def test_records_without_a_session_id_are_never_shown_as_one_game(report):
    now = datetime(2026, 10, 13, 7, 5, 9, tzinfo=timezone.utc)

    text = report.render_report([{"outcome": "win"}, {"outcome": "loss"}], now)

    assert "same game" not in text
    assert "from 2 match record(s) of 2 game(s)." in text


def test_the_report_tool_changes_no_record_file(recorded_fixture, report):
    before = {name: (recorded_fixture / name).read_bytes() for name in ("events.jsonl", "meta.json")}

    report.main([str(recorded_fixture)])

    assert {name: (recorded_fixture / name).read_bytes() for name in before} == before
    assert sorted(path.name for path in recorded_fixture.iterdir()) == ["events.jsonl", "meta.json", "metrics.json"]


def test_a_path_without_a_match_record_ends_the_command_with_exit_code_2(recorded_fixture, run_folder, report, capsys):
    (run_folder / "empty").mkdir()

    exit_code = report.main([str(recorded_fixture), "empty"])

    captured = capsys.readouterr()
    assert (exit_code, captured.out) == (2, "")
    assert captured.err.startswith("empty holds no match record")
    assert not (recorded_fixture / "metrics.json").exists() and not (run_folder / "runs" / "reports").exists()


def test_the_command_without_a_path_is_a_usage_error(run_folder, report):
    with pytest.raises(SystemExit) as stopped:
        report.main([])

    assert stopped.value.code == 2


def test_metrics_and_report_files_are_written_redacted(run_folder, monkeypatch, report):
    loaded_key = "jev-4f9c2d7e1a6b8053"
    monkeypatch.setenv("JEV_API_KEY", loaded_key)
    folder = run_folder / "runs" / "20261012" / "session-1"
    write_attempt(folder, [("result", {"result": WIN})], meta={"session_id": f"match-{loaded_key}"})

    report.main([str(folder)])

    written = (folder / "metrics.json").read_text(encoding="utf-8") + _report_text(run_folder)
    assert loaded_key not in written
    assert written.count("match-[REDACTED]") == 2


def test_the_report_folder_is_named_by_the_utc_time_under_runs_reports(run_folder, report):
    now = datetime(2026, 10, 13, 7, 5, 9, tzinfo=timezone.utc)

    assert report.new_report_folder(now) == run_folder / "runs" / "reports" / "20261013T070509Z"


@pytest.mark.parametrize(
    "by_reason, shown",
    [({"not_ready": 2, "invalid": 1}, "3"), ({}, "0"), (None, "n/a")],
    ids=["reasons", "no rejection", "no ledger"],
)
def test_the_report_shows_the_total_of_rejected_orders(report, by_reason, shown):
    now = datetime(2026, 10, 13, 7, 5, 9, tzinfo=timezone.utc)

    text = report.render_report([{"session_id": "m", "outcome": "loss", "orders_rejected_by_reason": by_reason}], now)

    assert f"| m | n/a | n/a | n/a | n/a | n/a | loss | n/a | n/a | n/a | {shown} |" in text
    assert "Generated 2026-10-13 07:05:09 UTC from 1 match record(s) of 1 game(s)." in text


PRODUCTION_HEADING = (
    "| Match | Income/s mean | Income/s final | Utilization Building | Utilization Defense | Utilization Infantry "
    "| Utilization Vehicle | Low power (s) |"
)


def test_the_report_shows_the_production_of_each_red_alert_match_and_of_no_other(report):
    now = datetime(2026, 10, 13, 7, 5, 9, tzinfo=timezone.utc)
    red_alert = {
        "session_id": "m",
        "outcome": "win",
        "income_per_s": {"mean": 52.5, "final": 61.0},
        "utilization": {"Building": 0.9, "Defense": 0.1, "Infantry": 0.85, "Vehicle": None},
        "low_power_seconds": 30.0,
    }

    text = report.render_report([red_alert, {"session_id": "w", "outcome": "loss"}], now)
    without = report.render_report([{"session_id": "w", "outcome": "loss"}], now)

    production = text.split("## Production\n\n")[1].split("\n\n")[0].splitlines()
    assert production == [PRODUCTION_HEADING, "| --- " * 8 + "|", "| m | 52.5 | 61.0 | 0.9 | 0.1 | 0.85 | n/a | 30.0 |"]
    assert "## Production" not in without and "## Totals by outcome" in without


def test_match_folders_are_the_folder_itself_or_its_sub_folders_that_hold_a_record(tmp_path):
    day_folder = tmp_path / "runs" / "20261012"
    for session in ("session-b", "session-a"):
        write_attempt(day_folder / session, [("state", {})])
    write_attempt(day_folder / "resumed-only", [("state", {})], attempt=2)
    (day_folder / "empty-folder").mkdir()
    (day_folder / "run.jsonl").write_text("{}\n", encoding="utf-8")

    assert metrics.match_folders(day_folder / "session-a") == [day_folder / "session-a"]
    assert metrics.match_folders(day_folder) == [
        day_folder / "resumed-only",
        day_folder / "session-a",
        day_folder / "session-b",
    ]
    assert metrics.match_folders(tmp_path / "runs") == []
    assert metrics.match_folders(tmp_path / "missing") == []
    assert metrics.match_folders(day_folder / "run.jsonl") == []


# -- comparing two run sets ------------------------------------------------------------------------

GENERATED = datetime(2026, 10, 13, 7, 5, 9, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def fixture_generator(load_script):
    """The fixture generator script, imported once."""
    return load_script("make_metrics_fixture.py")


@pytest.fixture
def run_sets(run_folder, fixture_generator) -> Path:
    """Two run sets under a temporary repository root: `runs/before` (balances 1500 and 900) and `runs/after` (2100)."""
    for run_set, session, balance in (
        ("before", "match-1", 1500),
        ("before", "match-2", 900),
        ("after", "match-3", 2100),
    ):
        fixture_generator.write(run_folder / "runs" / run_set / session, final_trade_balance=balance)
    return run_folder / "runs"


def _comparison_rows(root: Path) -> dict[str, list[str]]:
    """The rows of the one `compare.md` under a root, as metric name -> [mean A, mean B, difference]."""
    (comparison_path,) = root.glob("runs/reports/*/compare.md")
    cells = [line.strip("| \n").split(" | ") for line in comparison_path.read_text(encoding="utf-8").splitlines()]
    return {row[0]: row[1:] for row in cells if len(row) == 4 and row[0] not in ("Metric", "---")}


def test_compare_writes_the_final_trade_balance_means_of_two_fixture_sets_and_their_difference(
    run_folder, run_script, fixture_generator
):
    shutil.copytree(FIXTURE, run_folder / "runs" / "a" / "redalert-synthetic")
    fixture_generator.write(run_folder / "runs" / "b" / "redalert-synthetic", final_trade_balance=900)

    completed = run_script(run_folder, "report.py", "--compare", "runs/a", "runs/b")

    (comparison_path,) = run_folder.glob("runs/reports/*/compare.md")
    comparison = comparison_path.read_text(encoding="utf-8")
    assert (completed.returncode, completed.stderr) == (0, "")
    assert completed.stdout.strip() == f"Wrote {comparison_path}"
    assert "| final_trade_balance | 1500 | 900 | -600 |" in comparison
    assert "| Metric | Mean A | Mean B | Difference (B - A) |" in comparison
    assert "- A: `runs/a`, 1 match record(s)" in comparison and "- B: `runs/b`, 1 match record(s)" in comparison


def test_compare_shows_the_mean_over_every_match_of_each_run_set(run_sets, report):
    exit_code = report.main(["--compare", str(run_sets / "before"), str(run_sets / "after")])

    rows = _comparison_rows(run_sets.parent)
    assert exit_code == 0
    assert rows["final_trade_balance"] == ["1200", "2100", "900"]
    assert rows["batches_accepted"] == ["38", "38", "0"]
    assert rows["batches_per_second"] == ["0.1919", "0.1919", "0"]
    assert rows["outcome.win"] == ["1", "1", "0"] and rows["outcome.loss"] == ["0", "0", "0"]


def test_compare_flattens_nested_metrics_to_dotted_names(run_sets, report):
    report.main(["--compare", str(run_sets / "before"), str(run_sets / "after")])

    rows = _comparison_rows(run_sets.parent)
    assert rows["cycle_latency_ms.p95"] == ["346", "346", "0"]
    assert rows["idle_production_seconds.Building"] == ["60", "60", "0"]
    assert rows["decision_latency_ms.local:fast.p50"] == ["900", "900", "0"]
    assert rows["orders_rejected_by_reason.not_available"] == ["2", "2", "0"]
    assert list(rows) == sorted(rows)


def test_compare_leaves_out_what_is_not_a_measured_number(run_sets, report):
    report.main(["--compare", str(run_sets / "before"), str(run_sets / "after")])

    rows = _comparison_rows(run_sets.parent)
    not_compared = {"seat", "session_id", "outcome", "faction", "plan_sequence", "commit_differs_between_attempts"}
    assert not_compared.isdisjoint(rows)
    assert "attempts" in rows and "duration_s" in rows


def test_a_metric_one_run_set_does_not_report_is_shown_as_not_available(run_sets, report):
    write_attempt(run_sets / "other-game" / "werewolf-match", [("state", {}), ("result", {"result": WIN})])

    report.main(["--compare", str(run_sets / "before"), str(run_sets / "other-game")])

    rows = _comparison_rows(run_sets.parent)
    assert rows["final_trade_balance"] == ["1200", "n/a", "n/a"]
    assert rows["fallback_count"] == ["3", "0", "-3"]
    assert rows["outcome.win"] == ["1", "1", "0"]


def test_compare_writes_nothing_into_the_match_folders(run_sets, report):
    before = sorted(path.relative_to(run_sets) for path in run_sets.rglob("*") if path.is_file())

    report.main(["--compare", str(run_sets / "before"), str(run_sets / "after")])

    files = sorted(path.relative_to(run_sets) for path in run_sets.rglob("*") if path.is_file())
    assert [path for path in files if path not in before] == [
        next(run_sets.glob("reports/*/compare.md")).relative_to(run_sets)
    ]


@pytest.mark.parametrize(
    "arguments",
    [[], ["runs/before", "--compare", "runs/before", "runs/after"], ["--compare", "runs/before", "runs/before"]],
    ids=["neither paths nor --compare", "both", "one run set twice"],
)
def test_compare_and_paths_cannot_be_mixed_up(run_sets, report, arguments):
    with pytest.raises(SystemExit) as stopped:
        report.main(arguments)

    assert stopped.value.code == 2
    assert not (run_sets / "reports").exists()


def test_compare_with_a_path_that_holds_no_record_ends_with_exit_code_2(run_sets, report, capsys):
    exit_code = report.main(["--compare", str(run_sets / "before"), str(run_sets / "missing")])

    assert exit_code == 2
    assert "missing holds no match record" in capsys.readouterr().err
    assert not (run_sets / "reports").exists()


def test_numeric_figures_are_numbers_only_with_one_figure_per_outcome(report):
    computed = {
        "seat": 1,
        "outcome": "draw",
        "fallback_count": 2,
        "low_power_seconds": None,
        "config_differs_between_attempts": True,
        "plan_sequence": [{"plan": "HOLD", "game_seconds": 3.0}],
        "compute_ms": {"p50": 4.5, "p95": None},
        "decision_latency_ms": {"jev": {"p50": 80}},
    }

    assert report.numeric_figures(computed) == {
        "fallback_count": 2,
        "compute_ms.p50": 4.5,
        "decision_latency_ms.jev.p50": 80,
        "outcome.win": 0.0,
        "outcome.loss": 0.0,
        "outcome.draw": 1.0,
        "outcome.no_result": 0.0,
    }


def test_means_and_differences_are_shown_to_four_decimals_without_trailing_zeros(report):
    run_sets = {"a": [{"x": 1}, {"x": 2}, {"x": 2}], "b": [{"x": 5 / 3}]}

    text = report.render_comparison(run_sets, GENERATED)

    assert "| x | 1.6667 | 1.6667 | 0 |" in text
    assert "Generated 2026-10-13 07:05:09 UTC." in text
