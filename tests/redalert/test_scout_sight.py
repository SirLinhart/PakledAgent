"""Sight by unit type (`[scouting] sight_by_type`): the item table's sight, the sight grid, and the rush scout's exit
post placed where the scout sees the exit."""

from __future__ import annotations

import dataclasses

import situations
from situations import ask, base_state
from ucla_agent.games.redalert import rush_scout
from ucla_agent.games.redalert.items import TICKS_PER_SECOND, ItemTable
from ucla_agent.games.redalert.sight import FALLBACK_SIGHT_CELLS, SIGHT_CELLS, SightGrid, sight_of
from ucla_agent.games.redalert.tuning import ScoutingTuning, Tuning

ALLIED_BASE = (("fact", 22, 48), ("powr", 19, 52), ("tent", 18, 44), ("proc", 26, 52))
# The enemy's start is (74, 48): the exit cell 8 cells from it toward our base at (22, 48).
EXIT_CELL = (66, 48)
SECOND = TICKS_PER_SECOND
OFF = dataclasses.replace(Tuning(), scouting=dataclasses.replace(Tuning().scouting, sight_by_type=False))


def agent_seeing(tuning: Tuning = Tuning(), units=(), enemy_buildings=(), tick: int = 1000):
    agent = situations.new_agent(tuning=tuning)
    state = base_state(
        "england", *ALLIED_BASE, tick=tick, units=list(units), visible_enemy_buildings=list(enemy_buildings)
    )
    ask(agent, state)
    return agent


def scout(x: int, y: int, unit_type: str = "e1") -> dict:
    return situations.unit(400, unit_type, x, y)


def test_the_item_table_gives_the_engines_sight():
    items = ItemTable.from_file(situations.REPO_ROOT)

    assert (items.get("e1").sight, items.get("dog").sight, items.get("jeep").sight) == (4, 5.5, 7)
    assert sight_of("e1", items, by_type=True) == 4 and sight_of("yak", items, by_type=True) == FALLBACK_SIGHT_CELLS
    assert sight_of("e1", items, by_type=False) == SIGHT_CELLS


def test_an_e1_scout_at_the_exit_post_sees_the_exit_cell():
    agent = agent_seeing()
    post = rush_scout.watch_post("e1", agent.world, agent.ctx)
    at_post = agent_seeing(units=[scout(*post)])
    grid = SightGrid()

    grid.observe(at_post.world, by_type=True)

    # 8 + 4 - 1 cells from the enemy's start: the exit cell lies 3 cells off, within the rifleman's sight.
    assert post == (63, 48)
    assert grid.last_seen(EXIT_CELL) == 1000


def test_the_old_post_13_out_does_not_see_the_exit_with_a_riflemans_sight():
    old_post = agent_seeing(units=[scout(61, 48)])
    grid = SightGrid()

    grid.observe(old_post.world, by_type=True)

    assert grid.last_seen(EXIT_CELL) is None


def test_a_post_in_defense_reach_is_moved_out_and_the_scout_peeks_in():
    pillbox = {"id": 900, "type": "pbox", "x": 64, "y": 46, "hp": 100}
    peeking = agent_seeing(enemy_buildings=[pillbox], tick=15 * SECOND * 10)
    backed_off = agent_seeing(enemy_buildings=[pillbox], tick=15 * SECOND * 10 + 5 * SECOND)

    in_sight, safe = rush_scout.posts("e1", peeking.world, peeking.ctx)

    assert in_sight == (63, 48) and safe[0] < in_sight[0]
    assert rush_scout.watch_post("e1", peeking.world, peeking.ctx) == in_sight
    assert rush_scout.watch_post("e1", backed_off.world, backed_off.ctx) == safe


def test_switch_off_keeps_the_post_13_out():
    agent = agent_seeing(tuning=OFF)

    assert rush_scout.watch_post("e1", agent.world, agent.ctx) == (61, 48)


def test_tuning_sight_by_type_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).scouting, ScoutingTuning()):
        assert tuning.sight_by_type is True
        assert (tuning.rush_exit_watch_cells, tuning.scout_peek_seconds, tuning.scout_peek_hold_seconds) == (8, 15, 4)
