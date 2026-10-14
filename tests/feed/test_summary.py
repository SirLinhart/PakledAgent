"""The end summary of a watched match, read from its record with the feed's own reader."""

from __future__ import annotations

from feed_records import AGENT_ID, red_alert_events, thinned_state
from ucla_agent.feed.summary import summary_lines

RESULT = {
    "result": {"returns": {AGENT_ID: 1.0, "bot:normal": 0.0}, "termination_reason": "victory"},
    "ended_at": "2026-10-10T18:20:00.000Z",
    "dropped_events": 0,
}


def test_the_summary_gives_the_result_balance_length_choices_fallbacks_explored_share_and_throughput(
    write_match, tmp_path
):
    write_match([[*red_alert_events(), ("result", RESULT)]])

    lines = summary_lines(tmp_path / "runs")

    assert lines == [
        "Match game1-sidestep-1__Multi0",
        "  result:         won (victory)",
        "  trade balance:  +900",
        "  game length:    2:56 of game time",
        "  model choices:  2, latency median 180 ms, max 1,500 ms; used 1, timeout 1",
        "  fallbacks:      1",
        "  map explored:   23.4%",
        "  throughput:     0.01 orders/s, 0.01 batches/s, 0.0 commands/s; 0.50 / 20 orders per batch, "
        "0.0% of batches at 20, 1.0 / 200 commands per batch; 0.000 rejected/s",
    ]


def test_the_newest_match_under_the_records_folder_is_summed_up(write_match, tmp_path):
    write_match([red_alert_events()], name="earlier__Multi0", started_at="2026-10-10T17:00:00.000Z")
    later = [
        ("state", thinned_state(25000, trade_balance=-1200, explored_percent=40.0)),
        ("match_end", {"reason": "process_exit"}),
    ]
    write_match([later], name="later__Multi0", started_at="2026-10-10T18:30:00.000Z")

    lines = summary_lines(tmp_path / "runs")

    assert lines[:4] == [
        "Match later__Multi0",
        "  result:         no result (process_exit)",
        "  trade balance:  -1,200",
        "  game length:    16:40 of game time",
    ]
    assert lines[4:7] == ["  model choices:  none", "  fallbacks:      0", "  map explored:   40.0%"]


def test_a_record_still_open_says_the_game_was_stopped_before_its_end(write_match, tmp_path):
    write_match([red_alert_events()])

    assert (
        summary_lines(tmp_path / "runs")[1]
        == "  result:         no result in the record (the game was stopped before it ended)"
    )


def test_a_record_without_states_shows_dashes_for_what_it_cannot_tell(write_match, tmp_path):
    write_match([[("game_config", {})]])

    lines = summary_lines(tmp_path / "runs")

    assert lines[2:4] == ["  trade balance:  -", "  game length:    - of game time"]
    assert lines[6] == "  map explored:   -%"
    assert lines[7].startswith("  throughput:     - orders/s, - batches/s, - commands/s; - / 20")


def test_a_records_folder_without_a_match_gives_one_line_saying_so(tmp_path):
    assert summary_lines(tmp_path / "records") == [
        f"No match record under {tmp_path / 'records'}: the game did not get as far as its first move."
    ]
