"""The infantry run through the enemy base guarded (`[scouting] run_through_guard`): one owner, an unseen defense
suspected, never idle under fire, the start passed beside after a loss, and runs paused after two losses in the core."""

from __future__ import annotations

import dataclasses

import situations
from situations import ask
from test_scout_loss import REMEMBERING
from test_scout_run_through import at, scout
from switches_off import new_agent
from ucla_agent.games.redalert import cells, rush_scout
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.rush_scout import POST, RushScout
from ucla_agent.games.redalert.scout_steering import Course
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.tuning import ScoutingTuning, Tuning

GUARDED = dataclasses.replace(REMEMBERING, scouting=dataclasses.replace(REMEMBERING.scouting, run_through_guard=True))
SECOND = TICKS_PER_SECOND
ENEMY_START = (74, 48)


def scene(seconds: float = 40, **unit):
    agent = new_agent(ScoutingManager(), tuning=GUARDED)
    ask(agent, at(seconds, scout(*unit.pop("cell", (18, 46)), **unit)))
    agent.ctx.session_id = None
    return agent


def test_a_runner_on_the_pass_is_the_runs_alone():
    agent = scene()

    assert RushScout().is_running(agent.world.combat_units[300], agent.world, agent.ctx) is True


def test_a_hit_with_no_enemy_in_sight_suspects_a_defense_5_cells_ahead():
    agent, runs = scene(cell=(61, 48)), RushScout()
    runs.move(agent.world.combat_units[300], None, Course(), agent.world, agent.ctx)
    ask(agent, at(42, scout(62, 48, hp=60)))

    runs.move(agent.world.combat_units[300], None, Course(), agent.world, agent.ctx)

    (suspected,) = runs.lost_cells
    assert cells.distance(suspected, (62, 48)) == 5 and suspected[0] > 62


def test_a_runner_shot_standing_still_for_2_s_goes_on_to_its_next_leg():
    agent, runs = scene(cell=(61, 48)), RushScout()
    first = runs.move(agent.world.combat_units[300], None, Course(), agent.world, agent.ctx)
    for seconds, hp in ((42, 90), (44, 70)):
        ask(agent, at(seconds, scout(64, 48, hp=hp)))
        moved = runs.move(agent.world.combat_units[300], None, Course(), agent.world, agent.ctx)

    legs = rush_scout.run_legs(rush_scout.DIRECT, agent.world, agent.ctx)
    assert first.cell == legs[1] and moved.cell == legs[2]


def test_after_a_loss_by_the_start_the_run_passes_10_cells_beside_it():
    agent = scene()

    legs = rush_scout.run_legs(rush_scout.DIRECT, agent.world, agent.ctx, lost=[(70, 48)])

    assert legs[1] != ENEMY_START and cells.distance(legs[1], (70, 48)) >= 10


def test_two_losses_in_the_core_pause_the_runs_for_the_post():
    agent, runs = scene(), RushScout()
    runs.note_lost((70, 48), agent.world.tick)
    runs.note_lost((72, 50), agent.world.tick)

    runs.move(agent.world.combat_units[300], None, Course(), agent.world, agent.ctx)

    assert runs.stage == POST and not runs.is_running(agent.world.combat_units[300], agent.world, agent.ctx)


def test_tuning_run_through_guard_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).scouting, ScoutingTuning()):
        assert tuning.run_through_guard is True
