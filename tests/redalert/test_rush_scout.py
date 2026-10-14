"""The rush scout: the first slot's scout passes through the enemy base, then stands at the enemy's exit until 300 s."""

from __future__ import annotations

import dataclasses

import situations
from situations import ask, base_state
from switches_off import new_agent, scouting_off, tuning_off
from ucla_agent.games.redalert.groups import MAIN, SCOUT
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.rush_scout import exit_post
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import Tuning

YARD = ("fact", 22, 48)
POWER = ("powr", 19, 52)
BARRACKS = ("tent", 18, 44)
SCOUT_ID, SECOND_ID = 300, 301
# The enemy's start, and the cell 13 cells from it toward our base at (22, 48).
ENEMY_BASE = [74, 48]
POST = [61, 48]
RUSH_SCOUTING = dataclasses.replace(
    tuning_off(),
    scouting=scouting_off(
        rush_detection=True, scout_slots=True, scout_missions=True, scout_cautious=True, scout_wide_berth=True
    ),
)


def at(seconds: float, *units: dict, **observation) -> dict:
    """An Allied base at a second of the match, with the units and what the test adds."""
    return base_state(
        "england", YARD, POWER, BARRACKS, tick=round(seconds * TICKS_PER_SECOND), units=list(units), **observation
    )


def scout(x: int, y: int, *, hp: int = 100, idle: bool = False) -> dict:
    return situations.unit(SCOUT_ID, "e1", x, y, hp=hp, idle=idle)


def moves_of(orders: list[dict], unit_id: int = SCOUT_ID) -> list[list[int]]:
    return [order["to"] for order in orders if order["cmd"] == "move" and unit_id in order["units"]]


def agent_with_scout(tuning: Tuning = RUSH_SCOUTING):
    """An agent whose first slot was taken at 40 s by a rifle by the barracks."""
    agent = new_agent(ScoutingManager(), tuning=tuning)
    ask(agent, at(40, scout(18, 46, idle=True)))
    return agent


def test_the_first_scout_drives_straight_at_the_enemy_start():
    agent = new_agent(ScoutingManager(), tuning=RUSH_SCOUTING)

    orders = ask(agent, at(40, scout(18, 46, idle=True)))

    assert agent.ctx.groups.group_of(SCOUT_ID) == SCOUT and moves_of(orders) == [ENEMY_BASE]


def test_within_5_cells_of_the_enemy_start_the_scout_goes_to_the_exit_post_and_stands_there_until_300_s():
    agent = agent_with_scout()
    on_the_way = ask(agent, at(80, scout(66, 48)))

    passed = ask(agent, at(90, scout(70, 49)))
    standing = ask(agent, at(150, scout(61, 48, idle=True)))
    strayed = ask(agent, at(200, scout(52, 48, idle=True)))

    assert moves_of(on_the_way) in ([], [ENEMY_BASE])
    assert moves_of(passed) == [POST] and moves_of(standing) == [] and moves_of(strayed) == [POST]
    assert agent.ctx.scout_destinations[0] == tuple(POST)


def test_at_300_s_the_scout_is_handed_back_to_the_missions():
    agent = agent_with_scout()
    ask(agent, at(90, scout(70, 49)))
    ask(agent, at(299, scout(61, 48, idle=True)))

    ask(agent, at(300, scout(61, 48, idle=True)))

    assert agent.ctx.groups.group_of(SCOUT_ID) == SCOUT
    assert tuple(POST) not in agent.ctx.scout_destinations[:1]


def test_a_scout_hit_on_the_pass_goes_to_the_exit_post_not_home():
    agent = agent_with_scout()
    ask(agent, at(60, scout(55, 48, hp=100)))

    hit = ask(agent, at(61, scout(56, 48, hp=90)))

    assert moves_of(hit) == [POST]


def test_a_pass_that_stays_30_s_within_13_cells_of_the_enemy_start_without_getting_through_ends_at_the_post():
    agent = agent_with_scout()
    ask(agent, at(80, scout(63, 48)))

    held_up = ask(agent, at(109, scout(64, 48)))
    over = ask(agent, at(110, scout(64, 48)))

    assert moves_of(held_up) in ([], [ENEMY_BASE]) and moves_of(over) == [POST]


def test_the_rush_scout_keeps_its_slot_at_50_percent_health_though_the_wide_berth_releases_below_60():
    agent = agent_with_scout()

    ask(agent, at(100, scout(64, 48, hp=50)))

    assert agent.ctx.groups.group_of(SCOUT_ID) == SCOUT


def test_the_second_slot_keeps_its_missions_while_the_first_makes_the_pass():
    agent = new_agent(ScoutingManager(), tuning=RUSH_SCOUTING)
    second = situations.unit(SECOND_ID, "e1", 18, 47)

    orders = ask(agent, at(40, scout(18, 46, idle=True), second))

    assert moves_of(orders) == [ENEMY_BASE] and moves_of(orders, SECOND_ID) != [ENEMY_BASE]
    assert agent.ctx.groups.group_of(SECOND_ID) == SCOUT


def test_with_rush_scout_direct_off_the_first_scout_does_not_drive_at_the_enemy_start():
    direct_off = dataclasses.replace(
        RUSH_SCOUTING, scouting=dataclasses.replace(RUSH_SCOUTING.scouting, rush_scout_direct=False)
    )
    agent = new_agent(ScoutingManager(), tuning=direct_off)

    orders = ask(agent, at(40, scout(18, 46, idle=True)))

    assert agent.ctx.groups.group_of(SCOUT_ID) == SCOUT and ENEMY_BASE not in moves_of(orders)


def test_with_rush_detection_off_the_first_scout_does_not_drive_at_the_enemy_start():
    detection_off = dataclasses.replace(
        RUSH_SCOUTING, scouting=dataclasses.replace(RUSH_SCOUTING.scouting, rush_detection=False)
    )
    agent = new_agent(ScoutingManager(), tuning=detection_off)

    orders = ask(agent, at(40, scout(18, 46, idle=True)))

    assert ENEMY_BASE not in moves_of(orders) and agent.ctx.groups.group_of(SCOUT_ID) != MAIN


def test_the_exit_post_on_ground_a_unit_cannot_stand_on_is_the_nearest_passable_cell_toward_our_base():
    agent = agent_with_scout()
    world = agent.world

    assert exit_post(world, Terrain(), 13) == tuple(POST)
    assert exit_post(world, Terrain(impassable={(61, 48), (60, 48)}), 13) == (59, 48)
