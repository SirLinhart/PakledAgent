"""A slot's lost scout remembered (`[scouting] scout_loss_memory`): its replacement waits 120 s and the next run comes
another way, clear of where it was lost."""

from __future__ import annotations

import dataclasses

import situations
from situations import ask
from test_scout_run_through import RUNNING, at, scout
from switches_off import new_agent
from ucla_agent.games.redalert import rush_scout
from ucla_agent.games.redalert.rush_scout import RushScout
from ucla_agent.games.redalert.scout_steering import Course
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.tuning import ScoutingTuning, Tuning

REMEMBERING = dataclasses.replace(RUNNING, scouting=dataclasses.replace(RUNNING.scouting, scout_loss_memory=True))


def trained_rifles(orders: list[dict]) -> int:
    return sum(order["count"] for order in orders if order["cmd"] == "train" and order["item"] == "e1")


def test_a_killed_slot_scout_is_replaced_120_s_later_at_the_soonest():
    agent = new_agent(ScoutingManager(), tuning=REMEMBERING)
    ask(agent, at(40, scout(18, 46, idle=True)))
    ask(agent, at(60, scout(50, 48)))

    soon = [trained_rifles(ask(agent, at(seconds))) for seconds in (61, 90, 150)]
    late = trained_rifles(ask(agent, at(181)))

    assert soon == [0, 0, 0] and late >= 1


def test_without_the_memory_the_slot_is_refilled_at_once():
    off = dataclasses.replace(REMEMBERING, scouting=dataclasses.replace(REMEMBERING.scouting, scout_loss_memory=False))
    agent = new_agent(ScoutingManager(), tuning=off)
    ask(agent, at(40, scout(18, 46, idle=True)))
    ask(agent, at(60, scout(50, 48)))

    assert sum(trained_rifles(ask(agent, at(seconds))) for seconds in (61, 66, 71)) >= 1


def runner_scene():
    agent = new_agent(ScoutingManager(), tuning=REMEMBERING)
    ask(agent, at(40, scout(18, 46, idle=True)))
    agent.ctx.session_id = None
    return agent


def test_the_next_run_takes_the_next_approach_clear_of_a_loss_by_8_cells():
    agent = runner_scene()
    runs = RushScout()
    direct = rush_scout.run_legs(rush_scout.DIRECT, agent.world, agent.ctx)
    runs.lost_cells.append(direct[0])

    assert runs._approach_clear_of_losses(agent.world, agent.ctx) == rush_scout.LEFT and runs.runs == 1


def test_a_run_whose_scout_was_lost_on_the_way_is_over_and_the_next_scout_comes_another_way():
    agent = runner_scene()
    runs, course = RushScout(), Course()
    first = agent.world.combat_units[300]
    runs.move(first, None, course, agent.world, agent.ctx)
    other = dataclasses.replace(first, id=301)

    move = runs.move(other, None, Course(), agent.world, agent.ctx)

    left = rush_scout.run_legs(rush_scout.LEFT, agent.world, agent.ctx)
    assert runs.runs == 1 and move.cell == left[0]


def test_tuning_scout_loss_memory_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).scouting, ScoutingTuning()):
        assert tuning.scout_loss_memory is True
        assert (tuning.scout_lost_avoid_cells, tuning.scout_slot_replace_seconds) == (8, 120)
