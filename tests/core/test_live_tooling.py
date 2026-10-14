"""The tooling for live validation: the facts a match record captures, the live summary, and the checklist documents."""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent.agent import create_agent
from fake_game import FakeRedAlert
from switches_off import write_model_switches_off
from ucla_agent.core import metrics, paths

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_RECORD = REPO_ROOT / "tests" / "fixtures" / "records" / "redalert_synthetic"
TIMING_MEASURES = ("compute_ms", "round_trip_ms", "state_age_ms", "ticks_per_call")
# A fake match of 1,000 ticks: the states of ticks 0 to 1,000, five ticks apart.
STATES_OF_1000_TICKS = 201


def fake_match_events(root: Path, steps: int, context=None) -> list[dict]:
    """Play the fake game for `steps` states with the dispatcher `create_agent()` returns, its copy of the tracked
    `config/agent.toml` with the model switches off so that no model server is reached, and read back its record."""
    write_model_switches_off(root / "config" / "agent.toml")
    game = FakeRedAlert()
    game.run(create_agent(), steps, context)
    (events_file,) = root.glob("runs/*/*/events.jsonl")
    return [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines()]


# -- what the match record captures ---------------------------------------------------------------------


def test_1_a_fake_match_with_a_game_config_records_it_exactly_once_as_it_came(checked_project):
    game_config = FakeRedAlert().game_config

    events = fake_match_events(checked_project, 20)

    logged = [event["data"] for event in events if event["kind"] == "game_config"]
    assert logged == [game_config]


def test_a_fake_match_without_a_game_config_records_none(checked_project):
    context = FakeRedAlert().context()

    events = fake_match_events(checked_project, 20, dataclasses.replace(context, game_config=None))

    assert [event for event in events if event["kind"] == "game_config"] == []


def test_2_a_1000_tick_fake_match_records_four_timing_windows_with_the_mean_and_maximum_of_four_measures(
    checked_project,
):
    events = fake_match_events(checked_project, STATES_OF_1000_TICKS)

    timings = [event["data"] for event in events if event["kind"] == "timing"]
    assert [(timing["from_tick"], timing["to_tick"]) for timing in timings] == [
        (0, 250),
        (250, 500),
        (500, 750),
        (750, 1000),
    ]
    assert all(set(timing[measure]) == {"mean", "max"} for timing in timings for measure in TIMING_MEASURES)
    assert all(timing["compute_ms"]["max"] >= timing["compute_ms"]["mean"] > 0 for timing in timings)


# -- the live summary -------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_summary(load_script):
    return load_script("live_summary.py")


@pytest.fixture(scope="module")
def reported() -> dict:
    """What `scripts/report.py` computes for the synthetic record."""
    return metrics.compute_metrics(metrics.load_match(SYNTHETIC_RECORD))


def summary_of(live_summary, capsys, folder: Path) -> dict[str, str]:
    """The summary's lines by their label (the text before the first `: `)."""
    assert live_summary.main([str(folder)]) == 0
    lines = capsys.readouterr().out.splitlines()
    return {line.split(": ", 1)[0]: line.split(": ", 1)[1] for line in lines if ": " in line and not line[0].isspace()}


def test_3_the_summary_shows_what_the_report_computes_for_the_same_record(live_summary, reported, capsys):
    shown = summary_of(live_summary, capsys, SYNTHETIC_RECORD)

    plans = ", ".join(f"{step['plan']} at {step['game_seconds']} s" for step in reported["plan_sequence"])
    assert shown["Map"] == reported["map_id"] == "sidestep"
    assert shown["Factions"] == f"{reported['faction']} (own), {reported['enemy_faction']} (enemy)"
    assert shown["Result"] == f"{reported['outcome']} ({reported['termination_reason']})" == "win (time_limit)"
    assert shown["batches_per_second"] == str(reported["batches_per_second"])
    assert shown["cycle_latency_ms"].endswith(f"p95 {reported['cycle_latency_ms']['p95']}")
    assert shown["Plan sequence"] == plans == "OPENING at 0.0 s, HOLD at 70.0 s, RAID at 120.0 s"


def test_the_summary_gives_the_seat_the_final_military_the_latencies_the_orders_and_the_errors(
    live_summary, reported, capsys
):
    shown = summary_of(live_summary, capsys, SYNTHETIC_RECORD)

    assert shown["Seat"] == "Multi0"
    assert shown["Final military"] == "kills_cost 2900, deaths_cost 1400, assets_value 5960, army_value 800"
    for figure in ("compute_ms", "round_trip_ms", "cycle_latency_ms"):
        assert shown[figure] == f"p50 {reported[figure]['p50']}, p95 {reported[figure]['p95']}"
    assert (
        shown["ticks_per_call"] == f"mean {reported['ticks_per_call']['mean']}, max {reported['ticks_per_call']['max']}"
    )
    assert shown["Batches refused whole, by error"] == "INVALID_ACTION 2"
    assert shown["duplicate_production_orders"] == "0"
    assert shown["Rejected orders by reason"] == "not_available 2, not_ready 1"
    assert shown["Lost orders"] == "1 (by reason: none)"
    assert shown["Errors"] == "1"


def write_record(folder: Path, events: list[tuple[str, dict]]) -> Path:
    """A match folder whose events file holds these events, numbered in order, and a meta file."""
    folder.mkdir(parents=True)
    lines = [json.dumps({"seq": seq, "t": None, "kind": kind, "data": data}) for seq, (kind, data) in enumerate(events)]
    (folder / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (folder / "meta.json").write_text(json.dumps({"game_type": "red_alert", "seat": "Multi1"}), encoding="utf-8")
    return folder


def lost_verdicts(batch: int, *reasons: str, pending: bool = False) -> dict:
    rows = [{"index": index, "status": "lost", "reason": reason} for index, reason in enumerate(reasons)]
    return {"batch": batch, "pending": pending, "results": rows}


def full_state(tick: int, **fields) -> tuple[str, dict]:
    state = {"state_version": tick, "phase": "playing", "observation": {"military": {"kills_cost": tick}}, **fields}
    return "state", {"state_version": tick, "phase": "playing", "state": state}


def test_lost_orders_are_counted_by_reason_once_each_from_verdicts_and_from_batches_never_received(
    live_summary, tmp_path, capsys
):
    batch_of_three = {"type": "orders", "orders": [{"cmd": "stop", "units": [1]}] * 3}
    folder = write_record(
        tmp_path / "match__Multi1",
        [
            full_state(5, last_orders=lost_verdicts(1, "not_applied", "not_applied", pending=True)),
            full_state(10, last_orders=lost_verdicts(1, "not_applied", "not_applied")),
            full_state(15, last_orders=lost_verdicts(1, "not_applied", "not_applied")),
            full_state(20, recent_order_problems=[{"batch": 1, "index": 0, "status": "lost", "reason": "not_applied"}]),
            ("action", {"action": batch_of_three}),
            ("action_result", {"result": {"error": "RUNTIME_TEMPORARILY_UNAVAILABLE"}}),
            ("action", {"action": batch_of_three}),
            ("action_result", {"result": {"error": "INVALID_ACTION", "detail": "[0] stop: invalid"}}),
            ("action", {"action": "WAIT"}),
            ("action_result", {"result": {"error": "GAME_ALREADY_COMPLETE"}}),
            ("ledger_summary", {"orders_lost": 5}),
        ],
    )

    shown = summary_of(live_summary, capsys, folder)

    assert shown["Lost orders"] == "5 (by reason: runtime_temporarily_unavailable 3, not_applied 2)"
    assert shown["Batches refused whole, by error"] == (
        "GAME_ALREADY_COMPLETE 1, INVALID_ACTION 1, RUNTIME_TEMPORARILY_UNAVAILABLE 1"
    )
    assert shown["Final military"] == "kills_cost 20"


def test_a_record_of_thinned_states_gives_the_military_of_the_last_playing_state(live_summary, tmp_path, capsys):
    folder = write_record(
        tmp_path / "thin__Multi1",
        [
            ("state", {"state_version": 50, "phase": "playing", "military": {"kills_cost": 300}}),
            ("state", {"state_version": 40, "phase": "playing", "military": {"kills_cost": 100}}),
            ("state", {"state_version": 60, "phase": "finished", "military": {"kills_cost": 900}}),
        ],
    )

    shown = summary_of(live_summary, capsys, folder)

    assert shown["Final military"] == "kills_cost 300" and shown["Plan sequence"] == "none"
    assert shown["Map"] == "n/a" and shown["Rejected orders by reason"] == "n/a"


def test_a_record_without_states_or_ledger_says_what_it_cannot_tell(live_summary, tmp_path, capsys):
    folder = write_record(tmp_path / "empty__Multi1", [("action", {"action": "WAIT"})])

    shown = summary_of(live_summary, capsys, folder)

    assert shown["Final military"] == "n/a" and shown["Lost orders"] == "n/a (by reason: none)"
    assert shown["Errors"] == "0" and shown["Result"] == "no_result (unknown)"


def test_each_error_event_is_listed_and_a_secret_in_one_never_printed(live_summary, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "jev-key-for-the-summary-test")
    error = {"where": "plan", "type": "unhandled", "exception": "KeyError", "message": "jev-key-for-the-summary-test"}
    folder = write_record(tmp_path / "errors__Multi1", [("error", error)])

    assert live_summary.main([str(folder)]) == 0

    out = capsys.readouterr().out
    assert "  plan unhandled KeyError: [REDACTED]" in out.splitlines() and "jev-key-for-the-summary-test" not in out


@pytest.mark.parametrize("given", ["runs", "runs/20261001"], ids=["no record at all", "a dated folder"])
def test_a_folder_that_is_not_one_seat_s_match_folder_is_refused(live_summary, tmp_path, capsys, given):
    write_record(tmp_path / "runs" / "20261001" / "match__Multi0", [("action", {"action": "WAIT"})])

    assert live_summary.main([str(tmp_path / given)]) == 2

    assert "holds no match record" in capsys.readouterr().err


# -- the score probe ----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def score_probe() -> str:
    return " ".join((REPO_ROOT / "scripts" / "score_probe.md").read_text(encoding="utf-8").split())


@pytest.mark.parametrize(
    "observation",
    [
        "## Observation 1: does unspent cash change `assets_value`?",
        "## Observation 2: does losing a unit raise `deaths_cost` by its full cost?",
        "## Observation 3: does destroying an enemy building raise `kills_cost` by its cost?",
    ],
)
def test_5_the_score_probe_holds_the_three_observations_each_with_a_place_for_its_answer(score_probe, observation):
    section = score_probe.split(observation)[1].split("## ")[0]

    assert "**What to read:**" in section and "**Expected:**" in section
    assert "**Answer**" in section and "**Match folder:** - **Date:**" in section


@pytest.mark.parametrize(
    "rule",
    [
        "Nothing is sold, destroyed on purpose or exploited to test the scoring.",
        "No behavior of the agent is built on a probe result.",
        "is reported to the organizers and is not used.",
        "self-play test match",
    ],
)
def test_the_score_probe_states_its_rules(score_probe, rule):
    assert rule in score_probe


# -- the live validation checklist -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def checklist() -> str:
    return (REPO_ROOT / "docs" / "LIVE-VALIDATION.md").read_text(encoding="utf-8")


def checklist_item(checklist: str, number: int) -> str:
    """The text of one numbered item of the checklist, up to the next heading of its level."""
    return checklist.split(f"\n## {number}. ")[1].split("\n## ")[0]


@pytest.mark.parametrize(
    "number, title, must_say",
    [
        (1, "The readiness check passes with the key set", ["python -m ucla_agent.check", "PASS platform"]),
        (
            2,
            "Six self-play test matches over all three maps and both families",
            ["sidestep", "trapped", "shadowfiend-ii", "seat_plans", "Mine\n(self-hosted)", "unhandled"],
        ),
        (
            3,
            "Clean orders and fast decisions in every match of item 2",
            ["INVALID_ACTION", "duplicate_production_orders", "p95 at most 50", "round_trip_ms", "400"],
        ),
        (4, "One match with every decision source unavailable", ["model_plan_choice = false", "autostart = false"]),
        (5, "One match killed and restarted mid-game", ["events.2.jsonl", "manual-interventions.md"]),
        (6, "The platform's rules captured and compared", ["capture_rules.py", "diff_rules.py", "items.toml"]),
        (7, "The score probe's three answers", ["score_probe.md", "assets_value", "deaths_cost", "kills_cost"]),
    ],
)
def test_4_the_checklist_holds_the_seven_numbered_items_each_with_a_place_for_folder_and_date(
    checklist, number, title, must_say
):
    item = checklist_item(checklist, number)

    assert item.startswith(title + "\n")
    assert all(text in item for text in must_say)
    assert "folder" in item.lower() and "- Date:\n" in item + "\n"


def test_the_checklist_has_exactly_seven_items_and_says_who_declares_red_alert_ready(checklist):
    numbered = [line for line in checklist.splitlines() if line[:3] == "## " and line[3].isdigit()]

    assert [line.split(".")[0] for line in numbered] == [f"## {number}" for number in range(1, 8)]
    assert "## Declaring Red Alert `ready`" in checklist and "GAMES-STATUS.md" in checklist


# -- the tests left out by default --------------------------------------------------------------------------------

LIVE_TEST = "tests/live/test_platform_check.py::test_the_platform_step_passes_against_the_real_platform"
GPU_TEST = "tests/core/test_modelserver.py::test_the_real_fast_role_answers_a_completion_within_30_seconds"


def pytest_in_a_child(*arguments: str, environment: dict | None = None) -> subprocess.CompletedProcess:
    """Run pytest in its own process from the repository root, with the repository's own pytest.ini."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *arguments],
        cwd=REPO_ROOT,
        env=environment or os.environ,
        capture_output=True,
        text=True,
    )


def collected(*arguments: str) -> list[str]:
    completed = pytest_in_a_child("--collect-only", "-q", *arguments)
    return [line for line in completed.stdout.splitlines() if "::" in line]


def test_6_collecting_the_live_tests_lists_the_platform_check():
    assert collected("-m", "live") == [LIVE_TEST]


def test_collecting_gpu_or_live_lists_both_and_neither_is_collected_by_default():
    assert {GPU_TEST, LIVE_TEST} <= set(collected("-m", "gpu or live"))
    assert {GPU_TEST, LIVE_TEST}.isdisjoint(collected())


def test_6_the_live_platform_check_without_a_key_is_reported_skipped(tmp_path):
    # A project root with no .env, so that no key can be loaded from one.
    environment = {name: value for name, value in os.environ.items() if name != "ALTRUAGENT_OFFICIAL_AGENT_KEY"}
    environment[paths.ROOT_ENV] = str(tmp_path)

    completed = pytest_in_a_child("-m", "live", "-rs", LIVE_TEST.split("::")[0], environment=environment)

    assert completed.returncode == 0, completed.stdout
    assert "1 skipped" in completed.stdout and "ALTRUAGENT_OFFICIAL_AGENT_KEY is not set" in completed.stdout
