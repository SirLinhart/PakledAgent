"""No floating cash (`[producers] no_float`): money held at 1,500 or more for 20 s buys one more producer at once, the
busier queue's, whatever the focus."""

from __future__ import annotations

import dataclasses

import pytest

import situations
from situations import ask
from test_producers import TWO_REFINERIES_ONLY, SetIncome, base, builds, producer_agent
from ucla_agent.core import records
from ucla_agent.games.redalert.managers import MILITARY_FOCUS
from ucla_agent.games.redalert.tuning import ProducersTuning, Tuning

FLOATING = dataclasses.replace(
    TWO_REFINERIES_ONLY, producers=dataclasses.replace(TWO_REFINERIES_ONLY.producers, no_float=True)
)
STEP_SECONDS = 5


@pytest.fixture
def economy_events(monkeypatch) -> list[dict]:
    logged: list[dict] = []
    monkeypatch.setattr(records, "log", lambda kind, data: logged.append(data) if kind == "economy" else None)
    return logged


def floating(agent, cash: int, seconds: int, start: int = 300, **state) -> list[list[str]]:
    """What the agent builds in the Building queue in each state of a run, one every 5 s, with the cash given."""
    return [builds(ask(agent, base(at, cash=cash, **state))) for at in range(start, start + seconds + 1, STEP_SECONDS)]


def test_1600_held_for_20_s_buys_a_barracks_when_the_infantry_queue_is_the_busier(economy_events):
    # Below every income gate: only the floating cash calls for a producer.
    agent = producer_agent(SetIncome(20, infantry=0.9, vehicle=0.4), tuning=FLOATING)

    ordered = floating(agent, 1600, 20)

    assert ordered == [[]] * 4 + [["tent"]]
    assert economy_events == [{"tick": 320 * 25, "reason": "no_float", "item": "tent", "cash": 1600}]


def test_the_busier_vehicle_queue_buys_a_war_factory():
    agent = producer_agent(SetIncome(20, infantry=0.3, vehicle=0.9), tuning=FLOATING)

    assert floating(agent, 1600, 20)[-1] == ["weap"]


def test_under_the_military_focus_the_float_still_buys_a_producer():
    agent = producer_agent(SetIncome(20, infantry=0.9), tuning=FLOATING)
    agent.ctx.spending_focus = MILITARY_FOCUS

    assert floating(agent, 1600, 20)[-1] == ["tent"]


def test_1400_or_15_s_buys_nothing():
    low = producer_agent(SetIncome(20, infantry=0.9), tuning=FLOATING)
    short = producer_agent(SetIncome(20, infantry=0.9), tuning=FLOATING)

    assert floating(low, 1400, 20) == [[]] * 5 and floating(short, 1600, 15) == [[]] * 4


def test_one_order_in_20_s_at_most():
    agent = producer_agent(SetIncome(20, infantry=0.9), tuning=FLOATING)
    first = floating(agent, 1600, 20)

    # The barracks ordered at 320 s stands in the next states, and the cash still floats: the next at 340 s.
    after = floating(agent, 1600, 20, start=325, barracks=2)

    assert first[-1] == ["tent"] and after == [[], [], [], ["tent"], []]


def test_switch_off_floats_as_before():
    agent = producer_agent(SetIncome(20, infantry=0.9))

    assert floating(agent, 1600, 20) == [[]] * 5


def test_tuning_no_float_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).producers, ProducersTuning()):
        assert tuning.no_float is True and (tuning.no_float_cash, tuning.no_float_seconds) == (1500, 20)
