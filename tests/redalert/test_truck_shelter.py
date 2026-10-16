"""A retreating truck's shelter clear of the enemy and on our side of the threat (`truck_shelter_safe`)."""

from __future__ import annotations

import dataclasses

import situations
from situations import ask
from test_base_defense import (
    DEFENDED_REFINERY,
    HARVESTER_ID,
    RETREAT,
    at_work,
    enemy,
    retreat_agent,
    truck_orders,
)
from ucla_agent.games.redalert.tuning import BaseDefenseTuning, Tuning

SAFE = dataclasses.replace(RETREAT, base_defense=dataclasses.replace(RETREAT.base_defense, truck_shelter_safe=True))
TANK = enemy(500, "2tnk", 49, 40)


def test_a_refinery_with_an_enemy_unit_by_it_is_no_shelter():
    # An enemy rifleman 4 cells from the defended refinery: the truck goes to the base's centre instead.
    lurker = enemy(501, "e1", 26, 48)
    agent = retreat_agent(SAFE)

    orders = truck_orders(ask(agent, at_work(1000, enemies=[TANK, lurker])))

    assert orders == [{"cmd": "move", "units": [HARVESTER_ID], "to": list(agent.world.base_center)}]


def test_without_the_switch_the_truck_runs_to_it_all_the_same():
    lurker = enemy(501, "e1", 26, 48)

    orders = truck_orders(ask(retreat_agent(RETREAT), at_work(1000, enemies=[TANK, lurker])))

    assert orders == [{"cmd": "move", "units": [HARVESTER_ID], "to": list(DEFENDED_REFINERY[1:])}]


def test_a_clear_refinery_on_our_side_stays_the_shelter():
    orders = truck_orders(ask(retreat_agent(SAFE), at_work(1000, enemies=[TANK])))

    assert orders == [{"cmd": "move", "units": [HARVESTER_ID], "to": list(DEFENDED_REFINERY[1:])}]


def test_tuning_truck_shelter_safe_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).base_defense, BaseDefenseTuning()):
        assert tuning.truck_shelter_safe is True and tuning.truck_shelter_clear_cells == 10
