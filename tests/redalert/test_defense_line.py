"""The ground defenses in a line across the attack lane on the main base's edge (`defense_line_across_lane`)."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import situations
from situations import ask, base_state
from ucla_agent.games.redalert import defense_line
from ucla_agent.games.redalert.tuning import PlacementTuning, Tuning

# Our base's centre is (22, 48), the enemy's start (74, 48); the base reaches 4 cells toward it (the war factory and
# the refinery at x = 26).
ALLIED_BASE = (("fact", 22, 48), ("powr", 19, 52), ("tent", 18, 44), ("proc", 26, 52), ("weap", 26, 44))
SHIPPED = Tuning()
EAST = (74, 48)


def scene(*more: tuple[str, int, int], tuning: Tuning = SHIPPED):
    agent = situations.new_agent(tuning=tuning)
    ask(agent, base_state("england", *ALLIED_BASE, *more))
    return agent


def test_the_first_defense_goes_3_cells_beyond_the_outermost_building_toward_the_lane():
    agent = scene()

    assert defense_line.line_slot(EAST, agent.world, agent.ctx) == (29, 48)


def test_the_next_ones_go_across_the_lane_3_cells_apart():
    one, two = scene(("pbox", 29, 48)), scene(("pbox", 29, 48), ("pbox", 29, 51))

    assert defense_line.line_slot(EAST, one.world, one.ctx) == (29, 51)
    assert defense_line.line_slot(EAST, two.world, two.ctx) == (29, 45)


def test_the_line_follows_the_base_as_it_grows_toward_the_enemy():
    grown = scene(("powr", 34, 48))

    assert defense_line.line_slot(EAST, grown.world, grown.ctx) == (37, 48)


def test_an_expansion_far_out_does_not_move_the_line():
    agent = scene(("proc", 60, 48))

    assert defense_line.line_slot(EAST, agent.world, agent.ctx) == (29, 48)


def test_with_no_goal_the_lane_with_the_fewest_defenses_is_taken():
    defended_east = scene(("pbox", 29, 48), ("pbox", 29, 51))
    lanes = defense_line.LaneMemory()
    lanes.note(defended_east.world, SimpleNamespace(threat_cell=(22, 70), threat_aircraft_only=False))

    assert defense_line.lane_goal(None, defended_east.world, defended_east.ctx, lanes) == (22, 70)
    assert defense_line.lane_goal(None, defended_east.world, defended_east.ctx, defense_line.LaneMemory()) == EAST


def test_two_threats_by_one_lane_keep_one_cell_and_a_goal_by_another_yard_is_left_alone():
    agent = scene(("fact", 60, 20))
    lanes = defense_line.LaneMemory()
    for cell in ((22, 70), (24, 72), (60, 48)):
        lanes.note(agent.world, SimpleNamespace(threat_cell=cell, threat_aircraft_only=False))

    assert lanes.cells == [(24, 72), (60, 48)]
    assert defense_line.lane_goal((58, 22), agent.world, agent.ctx, lanes) is None


def test_only_ground_defenses_with_the_switch_on():
    off = dataclasses.replace(SHIPPED, placement=dataclasses.replace(SHIPPED.placement, defense_line_across_lane=False))

    assert defense_line.is_on("pbox", scene().ctx) and not defense_line.is_on("agun", scene().ctx)
    assert not defense_line.is_on("pbox", scene(tuning=off).ctx)


def test_tuning_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).placement, PlacementTuning()):
        assert tuning.defense_line_across_lane is True
        assert (tuning.defense_line_front_cells, tuning.defense_line_spacing_cells) == (3, 3)
