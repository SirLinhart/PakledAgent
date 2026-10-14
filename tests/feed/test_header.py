"""The feed's header: the latest Red Alert state's figures, thinned or full, and the result once the record closes."""

from __future__ import annotations

import pytest

from feed_records import AGENT_ID, thinned_state
from ucla_agent.feed.header import header_fields

FIGURES = (
    "game_seconds",
    "remaining_seconds",
    "cash",
    "trade_balance",
    "army_value",
    "enemy_army_value",
    "harvesters",
    "explored_percent",
    "plan",
)


def _full_state(tick: int) -> dict:
    """The data of a Red Alert state event written in full, annotated by the agent."""
    return {
        "state_version": tick,
        "phase": "playing",
        "state": {
            "state_version": tick,
            "time": {"max_minutes": 20.0, "game_seconds": 268.8, "remaining_seconds": 931.2},
            "observation": {
                "tick": tick,
                "economy": {"cash": 1250, "ore": 0, "harvesters": 3},
                "military": {"kills_cost": 2400, "deaths_cost": 3100, "army_value": 6000},
                "explored_percent": 10.1,
                "units": [],
            },
        },
        "plan": "HOLD",
        "enemy_army_value": 4500,
    }


def test_the_header_of_a_thinned_state_gives_every_figure_from_the_summary():
    header = header_fields(
        "red_alert", thinned_state(4400, economy={"cash": 2800, "ore": 150, "harvesters": 2}), None, AGENT_ID
    )

    assert header == {
        "game_type": "red_alert",
        "result": None,
        "game_seconds": 176.0,
        "remaining_seconds": 1024.0,
        "cash": 2800,
        "trade_balance": 900,
        "army_value": 4100,
        "enemy_army_value": 2600,
        "harvesters": 2,
        "explored_percent": 23.4,
        "plan": "RAID",
    }


def test_the_header_of_a_full_state_reads_the_same_figures_from_the_state_object():
    header = header_fields("red_alert", _full_state(6725), None, AGENT_ID)

    assert {name: header[name] for name in FIGURES} == {
        "game_seconds": 268.8,
        "remaining_seconds": 931.2,
        "cash": 1250,
        "trade_balance": -700,
        "army_value": 6000,
        "enemy_army_value": 4500,
        "harvesters": 3,
        "explored_percent": 10.1,
        "plan": "HOLD",
    }


def test_a_thinned_state_of_an_older_record_without_the_header_figures_gives_what_it_can():
    older = {
        "state_version": 500,
        "phase": "playing",
        "economy": {"cash": 4000, "harvesters": 1},
        "military": {"kills_cost": 300, "deaths_cost": 100, "army_value": 900},
        "units_count": 3,
    }

    header = header_fields("red_alert", older, None, AGENT_ID)

    assert (header["game_seconds"], header["trade_balance"], header["cash"]) == (20.0, 200, 4000)
    assert (header["remaining_seconds"], header["explored_percent"], header["plan"]) == (None, None, None)


def test_before_any_state_every_figure_is_unknown():
    header = header_fields("red_alert", None, None, AGENT_ID)

    assert {name: header[name] for name in FIGURES} == dict.fromkeys(FIGURES)


def test_a_full_state_without_an_observation_has_no_figures_but_its_clock():
    waiting = {"state_version": 0, "phase": "waiting_for_agents", "state": {"observation": None}}

    header = header_fields("red_alert", waiting, None, AGENT_ID)

    assert header["game_seconds"] == 0.0 and header["cash"] is None and header["trade_balance"] is None


@pytest.mark.parametrize(
    "returns, outcome",
    [({AGENT_ID: 1.0, "bot:normal": 0.0}, "win"), ({AGENT_ID: 0.0}, "loss"), ({AGENT_ID: 0.5}, "draw")],
)
def test_the_result_is_the_agent_s_outcome_and_the_termination_reason(returns, outcome):
    closing = {"kind": "result", "data": {"result": {"returns": returns, "termination_reason": "time_limit"}}}

    header = header_fields("red_alert", thinned_state(30000), closing, AGENT_ID)

    assert header["result"] == {"outcome": outcome, "reason": "time_limit"}


def test_a_record_closed_without_a_result_shows_no_result_and_why():
    closing = {"kind": "match_end", "data": {"reason": "process_exit", "last_state_version": 9000}}

    assert header_fields("red_alert", None, closing, AGENT_ID)["result"] == {
        "outcome": "no_result",
        "reason": "process_exit",
    }


def test_the_header_of_another_game_gives_its_type_and_result_only():
    closing = {"kind": "result", "data": {"result": {"returns": {AGENT_ID: 1.0}, "termination_reason": "game_over"}}}

    header = header_fields("werewolf", {"state_version": 12, "state": {"phase": "day"}}, closing, AGENT_ID)

    assert header == {"game_type": "werewolf", "result": {"outcome": "win", "reason": "game_over"}}
