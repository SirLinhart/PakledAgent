"""A chased scout near our base leads its chasers into our kill zone (`[scouting] retreat_to_kill_zone`)."""

from __future__ import annotations

import dataclasses

import situations
from situations import ask, base_state
from ucla_agent.games.redalert import cells, scout_retreat
from ucla_agent.games.redalert.scout_retreat import Retreats
from ucla_agent.games.redalert.tuning import ScoutingTuning, Tuning

SHIPPED = Tuning()
OFF = dataclasses.replace(SHIPPED, scouting=dataclasses.replace(SHIPPED.scouting, retreat_to_kill_zone=False))
# Our base at (22, 48), the enemy's start at (74, 48); our pillbox on the base's east edge.
BASE = (("fact", 22, 48), ("powr", 19, 52), ("tent", 18, 44), ("pbox", 28, 48))
CHASER = {"id": 900, "type": "e1", "x": 33, "y": 64, "hp": 100}


def chased(tuning: Tuning = SHIPPED, buildings=BASE, scout_at=(30, 60)):
    agent = situations.new_agent(tuning=tuning)
    scout = situations.unit(400, "dog", *scout_at)
    ask(agent, base_state("england", *buildings, units=[scout], visible_enemies=[CHASER]))
    world = agent.world
    return Retreats()._away(world.combat_units[400], world.enemy_memory[900], world, agent.ctx), agent


def test_a_chaser_behind_the_scout_is_led_to_the_front_of_our_defense():
    to, agent = chased()

    # The cell 2 in front of the pillbox at (28, 48), toward the enemy's start.
    assert scout_retreat.kill_zone((30, 60), agent.world, agent.ctx) == (30, 48)
    assert cells.distance(to, (30, 48)) < cells.distance((30, 60), (30, 48))


def test_without_the_switch_the_scout_runs_straight_away_from_the_chaser():
    to, _ = chased(OFF)

    # Away from (33, 64): north-west, toward the back of the base.
    assert to[0] < 30 and to[1] < 60


def test_with_no_defense_nor_army_the_scout_runs_to_the_enemy_facing_edge():
    _, agent = chased(buildings=BASE[:3])

    # 8 cells from our base's centre toward the enemy's start; the scout itself is no army to run to.
    assert scout_retreat.kill_zone((30, 60), agent.world, agent.ctx, scout_id=400) == (30, 48)


def test_far_from_our_base_the_retreat_is_as_before():
    _, agent = chased()

    assert scout_retreat.kill_zone((70, 20), agent.world, agent.ctx) is None


def test_tuning_retreat_to_kill_zone_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).scouting, ScoutingTuning()):
        assert tuning.retreat_to_kill_zone is True
