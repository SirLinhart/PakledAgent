"""The order throughput the feed shows: per second of game time and against the pool of 20, last 10 s and match."""

from __future__ import annotations

import pytest

from ucla_agent.feed.throughput import Throughput

WAIT = "WAIT"


def _action(tick: int, orders: int, *, commands: int | None = None, counted: bool = True) -> dict:
    """An action event: a batch of `orders` move orders, or WAIT for 0; counted as the agent counts it, or not."""
    action = WAIT if orders == 0 else {"type": "orders", "orders": [{"cmd": "move"}] * orders}
    data = {"state_version": tick, "action": action, "compute_ms": 0.5}
    if counted:
        data.update(order_count=orders, command_count=orders if commands is None else commands)
    return {"kind": "action", "data": data}


def _taken_in(*events: dict) -> Throughput:
    throughput = Throughput()
    for event in events:
        throughput.take_in(event)
    return throughput


def _full_state(tick: int, *, batch: int, rows: list[dict], problems: list[dict] = ()) -> dict:
    last_orders = {"batch": batch, "pending": False, "results": rows}
    state = {"state_version": tick, "last_orders": last_orders, "recent_order_problems": list(problems)}
    return {"kind": "state", "data": {"state_version": tick, "phase": "playing", "state": state}}


def test_ten_batches_in_two_seconds_holding_20_20_5_and_seven_empty_ones():
    orders = [20, 20, 5, 0, 0, 0, 0, 0, 0, 0]
    throughput = _taken_in(*(_action(5 * (position + 1), count) for position, count in enumerate(orders)))

    figures = throughput.figures()

    for span in ("last_10s", "match"):
        assert figures[span]["seconds"] == pytest.approx(2.0)
        assert figures[span]["orders_per_s"] == pytest.approx(22.5)
        assert figures[span]["batches_per_s"] == pytest.approx(5.0)
        assert figures[span]["mean_orders_per_batch"] == pytest.approx(4.5)
        assert figures[span]["pool_orders"] == 20
        assert figures[span]["at_limit_percent"] == pytest.approx(20.0)


def test_the_last_ten_seconds_leave_out_the_batches_sent_before_them():
    throughput = _taken_in(_action(25, 10), _action(375, 4), _action(500, 6))

    figures = throughput.figures()

    assert (figures["last_10s"]["seconds"], figures["last_10s"]["batches"], figures["last_10s"]["orders"]) == (
        10,
        2,
        10,
    )
    assert figures["last_10s"]["orders_per_s"] == pytest.approx(1.0)
    assert (figures["match"]["seconds"], figures["match"]["batches"], figures["match"]["orders"]) == (20.0, 3, 20)
    assert figures["match"]["orders_per_s"] == pytest.approx(1.0)
    assert figures["match"]["batches_per_s"] == pytest.approx(0.15)


def test_commands_per_batch_are_counted_against_the_limit_of_200():
    throughput = _taken_in(_action(25, 2, commands=120), _action(30, 0), _action(35, 1, commands=30))

    match = throughput.figures()["match"]

    assert (match["commands"], match["command_limit"]) == (150, 200)
    assert match["mean_commands_per_batch"] == pytest.approx(50.0)
    assert match["commands_per_s"] == pytest.approx(150 / 1.4)


def test_a_record_without_counts_is_read_by_counting_the_orders_and_its_commands_are_unknown():
    throughput = _taken_in(_action(25, 3, counted=False), _action(30, 0, counted=False))

    match = throughput.figures()["match"]

    assert (match["orders"], match["batches"], match["mean_orders_per_batch"]) == (3, 2, 1.5)
    assert (match["commands"], match["commands_per_s"], match["mean_commands_per_batch"]) == (None, None, None)


def test_a_span_of_waits_alone_from_an_older_record_has_zero_commands():
    throughput = _taken_in(
        _action(25, 3, counted=False), _action(400, 0, counted=False), _action(500, 0, counted=False)
    )

    figures = throughput.figures()

    assert figures["last_10s"]["commands"] == 0 and figures["match"]["commands"] is None


def test_every_order_of_a_batch_refused_whole_is_rejected():
    refused = {"kind": "action_result", "data": {"result": {"error": "INVALID_ACTION"}, "accepted": False}}
    accepted = {"kind": "action_result", "data": {"result": {"accepted_orders": 2}, "accepted": True}}

    throughput = _taken_in(_action(25, 3), refused, _action(50, 2), accepted, _action(75, 0))

    match = throughput.figures()["match"]
    assert match["rejected"] == 3 and match["rejected_per_s"] == pytest.approx(1.0)


def test_rejected_verdicts_of_full_states_count_once_per_batch_and_order():
    rejected_row = {"index": 1, "status": "rejected", "reason": "not_available"}
    ok_row = {"index": 0, "status": "ok", "reason": "ok"}
    lost_problem = {"batch": 7, "index": 0, "status": "lost"}
    rejected_problem = {"batch": 7, "index": 2, "status": "rejected"}

    throughput = _taken_in(
        _action(100, 3),
        _full_state(105, batch=8, rows=[ok_row, rejected_row]),
        _full_state(110, batch=8, rows=[ok_row, rejected_row]),
        _full_state(115, batch=8, rows=[ok_row, rejected_row], problems=[lost_problem, rejected_problem]),
        {"kind": "state", "data": {"state_version": 120, "phase": "playing", "economy": {}}},
    )

    match = throughput.figures()["match"]
    assert match["rejected"] == 2 and match["seconds"] == pytest.approx(4.8)


def test_the_latest_state_moves_the_clock_on_when_no_batch_is_sent():
    throughput = _taken_in(_action(25, 4), {"kind": "state", "data": {"state_version": 100, "phase": "playing"}})

    match = throughput.figures()["match"]

    assert match["seconds"] == pytest.approx(4.0) and match["orders_per_s"] == pytest.approx(1.0)


def test_before_any_batch_the_rates_and_means_are_unknown():
    figures = Throughput().figures()

    assert figures["match"]["seconds"] == 0 and figures["match"]["batches"] == 0
    assert all(figures["match"][name] is None for name in ("orders_per_s", "mean_orders_per_batch", "at_limit_percent"))


def test_events_without_a_tick_and_other_kinds_change_nothing():
    throughput = _taken_in(
        {"kind": "action", "data": {"state_version": "day-1", "action": "vote"}},
        {"kind": "state", "data": {"state_version": None}},
        {"kind": "raid", "data": {"tick": 9000}},
    )

    assert throughput.figures()["match"]["batches"] == 0 and throughput.series() == [
        {"second": 0, "orders": 0, "batches": 0}
    ]


def test_the_chart_series_holds_orders_and_batches_for_each_second_with_empty_seconds_as_zero():
    throughput = _taken_in(_action(5, 20), _action(10, 5), _action(60, 3), _action(110, 0))

    assert throughput.series() == [
        {"second": 0, "orders": 25, "batches": 2},
        {"second": 1, "orders": 0, "batches": 0},
        {"second": 2, "orders": 3, "batches": 1},
        {"second": 3, "orders": 0, "batches": 0},
        {"second": 4, "orders": 0, "batches": 1},
    ]
    assert [bucket["second"] for bucket in throughput.series(seconds=2)] == [3, 4]


def test_a_full_state_with_no_newest_batch_counts_the_rejected_problems_alone():
    state = {
        "state_version": 50,
        "last_orders": None,
        "recent_order_problems": [{"batch": 3, "index": 0, "status": "rejected"}],
    }

    throughput = _taken_in({"kind": "state", "data": {"state_version": 50, "phase": "playing", "state": state}})

    assert throughput.figures()["match"]["rejected"] == 1
