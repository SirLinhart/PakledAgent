"""The harvester crush: one harvester run over an enemy infantry cluster at the base under a rush, when no tank can."""

from __future__ import annotations

import dataclasses

import pytest

import situations
from situations import ask, base_state
from switches_off import new_agent, tuning_off
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.tuning import OpeningTuning

YARD = ("fact", 22, 48)
POWER = ("powr", 19, 52)
BARRACKS = ("tent", 18, 44)
REFINERY = ("proc", 24, 39)
HARVESTER_ID, TANK_ID = 400, 401
# The opening's rush counter and harvester crush as they ship, the other groups' switches off.
CRUSHING = tuning_off(opening=OpeningTuning())
# Six enemy rifles worth 600 about (30.5, 48), 8 cells east of the base's centre at (22, 48): the centroid's cell is
# (30, 48).
CLUSTER = [
    {"id": 500 + number, "type": "e1", "x": 30 + number // 3, "y": 47 + number % 3, "hp": 100} for number in range(6)
]
CLUSTER_CELL = [30, 48]


def at(seconds: float, *units: dict, enemies: list[dict] = CLUSTER, more: tuple = ()) -> dict:
    return base_state(
        "england",
        YARD,
        POWER,
        BARRACKS,
        REFINERY,
        *more,
        tick=round(seconds * TICKS_PER_SECOND),
        units=list(units),
        visible_enemies=enemies,
    )


def harvester(x: int = 26, y: int = 40, hp: int = 100) -> dict:
    return situations.unit(HARVESTER_ID, "harv", x, y, hp=hp, idle=False)


def crush_agent(verdict: str | None = "rush", tuning=CRUSHING):
    agent = new_agent(BaseDefenseManager(), tuning=tuning)
    agent.ctx.rush_verdict = verdict
    return agent


def orders_for(orders: list[dict], unit_id: int) -> list[dict]:
    return [order for order in orders if unit_id in order.get("units", ())]


def test_under_a_rush_a_harvester_runs_over_6_rifles_8_cells_from_the_base_and_harvests_again_after(match_record):
    agent = crush_agent()

    run = ask(agent, at(230, harvester()))
    published = dict(agent.ctx.harvester_crush)
    # The cluster run over, the harvester on its cell.
    after = ask(agent, at(233, harvester(30, 48), enemies=[]))

    assert orders_for(run, HARVESTER_ID) == [{"cmd": "move", "units": [HARVESTER_ID], "to": CLUSTER_CELL}]
    assert published == {HARVESTER_ID: tuple(CLUSTER_CELL)}
    assert orders_for(after, HARVESTER_ID) == [{"cmd": "harvest", "units": [HARVESTER_ID]}]
    assert agent.ctx.harvester_crush == {}
    events = [event for event in match_record("combat") if event["reason"] == "harvester_crush"]
    assert [(event["ended"], event["cluster_size"], event["harvester"]) for event in events] == [
        ("arrived", 6, HARVESTER_ID)
    ]


def test_the_run_ends_after_15_s_and_no_second_harvester_is_sent_meanwhile():
    agent = crush_agent()
    # Farther from the cluster than the first.
    second = situations.unit(HARVESTER_ID + 10, "harv", 18, 40, idle=False)
    ask(agent, at(230, harvester(), second))

    during = ask(agent, at(244, harvester(28, 44), second))
    over = ask(agent, at(245, harvester(28, 44), second))

    assert {"cmd": "move", "units": [HARVESTER_ID + 10], "to": CLUSTER_CELL} not in during
    assert {"cmd": "move", "units": [HARVESTER_ID], "to": CLUSTER_CELL} in orders_for(during, HARVESTER_ID)
    assert orders_for(over, HARVESTER_ID) == [{"cmd": "harvest", "units": [HARVESTER_ID]}]


SWITCHED_OFF = dataclasses.replace(CRUSHING, opening=OpeningTuning(rush_harvester_crush=False))
TANK_IN_REACH = situations.unit(TANK_ID, "1tnk", 25, 50)
FAR_OFF = [{**record, "x": record["x"] + 10} for record in CLUSTER]


def own_rifles(count: int) -> list[dict]:
    """Our rifles beside the cluster, 2 cells south of it."""
    return [situations.unit(450 + number, "e1", 29 + number, 51) for number in range(count)]


PILLBOX_4_CELLS_OFF = ("pbox", 35, 48)


# The cases that send no harvester: (tuning, verdict, own units, enemies in sight, more buildings), by name.
NO_CRUSH_CASES = {
    "a tank in reach": (CRUSHING, "rush", [harvester(), TANK_IN_REACH], CLUSTER, ()),
    "no rush with a war factory": (CRUSHING, "none", [harvester()], CLUSTER, (("weap", 15, 48),)),
    "the switch off": (SWITCHED_OFF, "rush", [harvester()], CLUSTER, ()),
    "a harvester at 40 percent": (CRUSHING, "rush", [harvester(hp=40)], CLUSTER, ()),
    "4 rifles worth 400": (CRUSHING, "rush", [harvester()], CLUSTER[:4], ()),
    "18 cells off": (CRUSHING, "rush", [harvester()], FAR_OFF, ()),
    "a pillbox 4 cells off": (CRUSHING, "rush", [harvester()], CLUSTER, (PILLBOX_4_CELLS_OFF,)),
    "5 rifles worth 500 by 4 worth 400": (CRUSHING, "rush", [harvester(), *own_rifles(5)], CLUSTER[:4], ()),
    "6 rifles worth 600 by 6 worth 600": (CRUSHING, "rush", [harvester(), *own_rifles(6)], CLUSTER, ()),
}


@pytest.mark.parametrize("tuning, verdict, units, enemies, more", NO_CRUSH_CASES.values(), ids=NO_CRUSH_CASES.keys())
def test_no_harvester_is_sent(tuning, verdict, units, enemies, more):
    agent = crush_agent(verdict, tuning)

    orders = ask(agent, at(230, *units, enemies=enemies, more=more))

    assert {"cmd": "move", "units": [HARVESTER_ID], "to": CLUSTER_CELL} not in orders
    assert agent.ctx.harvester_crush == {}


def test_a_threat_at_the_base_before_a_war_factory_calls_the_crush_without_a_verdict():
    agent = crush_agent(verdict=None)

    run = ask(agent, at(230, harvester()))

    assert agent.ctx.threat_active
    assert orders_for(run, HARVESTER_ID) == [{"cmd": "move", "units": [HARVESTER_ID], "to": CLUSTER_CELL}]


def test_5_of_our_rifles_worth_500_by_a_cluster_worth_600_leave_it_to_the_harvester():
    agent = crush_agent()

    run = ask(agent, at(230, harvester(), *own_rifles(5)))

    assert {"cmd": "move", "units": [HARVESTER_ID], "to": CLUSTER_CELL} in run


@pytest.mark.parametrize(
    "idle, activity, resent",
    [(True, None, True), (False, "Move", True), (False, "FindAndDeliverResources", False)],
    ids=["idle", "moving", "harvesting"],
)
def test_10_s_after_the_run_the_harvest_order_is_sent_again_unless_the_harvester_is_harvesting(idle, activity, resent):
    agent = crush_agent()
    ask(agent, at(230, harvester()))
    ask(agent, at(233, harvester(30, 48), enemies=[]))
    after = {**harvester(30, 48), "idle": idle, "act": activity}

    early = ask(agent, at(242.9, after, enemies=[]))
    later = ask(agent, at(243, after, enemies=[]))
    once = ask(agent, at(250, after, enemies=[]))

    harvest = {"cmd": "harvest", "units": [HARVESTER_ID]}
    assert harvest not in early and (harvest in later) is resent and harvest not in once
