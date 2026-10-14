"""Timing windows: one summary of the loop's figures for every 250 ticks of play."""

from __future__ import annotations

import pytest

from ucla_agent.core import metrics_redalert
from ucla_agent.games.redalert.timing import TimingWindows

WINDOW_TICKS = 250
TICKS_PER_STATE = 5
MS_PER_STATE = 200.0
NOTHING = {"mean": None, "max": None}


def play(windows: TimingWindows, ticks: range, compute_ms: float = 4.0) -> list[dict]:
    """Call the windows once for each tick, 200 ms apart, each call taking `compute_ms`; the events that came out."""
    events = []
    for tick in ticks:
        event = windows.note_call(tick, tick, tick / TICKS_PER_STATE * MS_PER_STATE)
        windows.note_compute(compute_ms)
        events += [event] if event is not None else []
    return events


@pytest.fixture
def windows() -> TimingWindows:
    return TimingWindows(WINDOW_TICKS)


def test_a_match_of_1000_ticks_gives_four_windows_of_250_ticks(windows):
    events = play(windows, range(0, 1001, TICKS_PER_STATE))

    assert [(event["from_tick"], event["to_tick"]) for event in events] == [
        (0, 250),
        (250, 500),
        (500, 750),
        (750, 1000),
    ]


def test_no_window_ends_before_250_ticks_have_passed(windows):
    assert play(windows, range(0, 250, TICKS_PER_STATE)) == []
    assert windows.note_call(250, 250, 10_000.0) is not None


def test_a_window_holds_the_mean_and_maximum_of_each_figure(windows):
    for tick, now_ms, compute_ms in ((0, 0.0, 2.0), (5, 200.0, 4.0), (15, 700.0, 9.0)):
        windows.note_call(tick, tick, now_ms)
        windows.note_compute(compute_ms)
    windows.note_round_trip(250.0)
    windows.note_round_trip(350.0)

    event = windows.note_call(250, 250, 1000.0)

    assert event == {
        "from_tick": 0,
        "to_tick": 250,
        "compute_ms": {"mean": 5.0, "max": 9.0},
        "round_trip_ms": {"mean": 300.0, "max": 350.0},
        "state_age_ms": {"mean": 333.333, "max": 500.0},
        "ticks_per_call": {"mean": 83.333, "max": 235},
    }


def test_the_call_that_ends_a_window_counts_its_wait_in_that_window_and_its_compute_time_in_the_next(windows):
    windows.note_call(0, 0, 0.0)
    windows.note_compute(1.0)
    first = windows.note_call(250, 250, 400.0)
    windows.note_compute(7.0)

    second = windows.note_call(500, 500, 900.0)

    assert (first["compute_ms"], first["state_age_ms"]) == ({"mean": 1.0, "max": 1.0}, {"mean": 400.0, "max": 400.0})
    assert (second["compute_ms"], second["state_age_ms"]) == ({"mean": 7.0, "max": 7.0}, {"mean": 500.0, "max": 500.0})
    assert (second["from_tick"], second["to_tick"]) == (250, 500)


def test_a_window_without_an_answered_batch_has_no_round_trip_figure(windows):
    (event,) = play(windows, range(0, 251, TICKS_PER_STATE))

    assert event["round_trip_ms"] == NOTHING
    assert event["compute_ms"] == {"mean": 4.0, "max": 4.0}
    assert event["ticks_per_call"] == {"mean": 5.0, "max": 5}


def test_the_first_window_begins_with_the_first_call_whatever_its_tick(windows):
    events = play(windows, range(31, 600, TICKS_PER_STATE))

    assert [(event["from_tick"], event["to_tick"]) for event in events] == [(31, 281), (281, 531)]


def test_a_gap_longer_than_a_window_gives_one_window_that_covers_it(windows):
    windows.note_call(0, 0, 0.0)

    event = windows.note_call(700, 700, 28_000.0)

    assert (event["from_tick"], event["to_tick"]) == (0, 700)
    assert event["ticks_per_call"] == {"mean": 700.0, "max": 700}
    assert event["compute_ms"] == NOTHING and windows.note_call(705, 705, 28_200.0) is None


def test_the_window_length_is_the_one_given():
    events = play(TimingWindows(100), range(0, 301, TICKS_PER_STATE))

    assert [(event["from_tick"], event["to_tick"]) for event in events] == [(0, 100), (100, 200), (200, 300)]


def test_figures_are_rounded_to_a_thousandth_of_a_unit(windows):
    windows.note_call(0, 0, 0.0)
    windows.note_compute(1.23456)
    windows.note_compute(2.0)

    event = windows.note_call(250, 250, 1000.0004)

    assert event["compute_ms"] == {"mean": 1.617, "max": 2.0}
    assert event["state_age_ms"] == {"mean": 1000.0, "max": 1000.0}


def test_the_report_tool_reads_ticks_per_call_from_the_windows(windows):
    events = play(windows, range(0, 1001, TICKS_PER_STATE))

    assert metrics_redalert._ticks_per_call(events) == {"mean": 5.0, "max": 5}
