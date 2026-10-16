"""The scout missions held (`[scouting] mission_hold`): no mission taken from the scout on it, no two scouts on one place,
and no early switch for a small gain."""

from __future__ import annotations

import dataclasses

import situations
from test_scouting import MISSIONS, NORTH, SOUTH, jeep_at, mission, planned, taken
from switches_off import new_agent
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.scout_mission_planner import MissionPlanner
from ucla_agent.games.redalert.tuning import ScoutingTuning, Tuning

HOLDING = dataclasses.replace(MISSIONS, scouting=dataclasses.replace(MISSIONS.scouting, mission_hold=True))
SECOND = TICKS_PER_SECOND


def two_jeeps() -> dict[int, dict]:
    """The jeep of slot 1 standing at the south place, the one of slot 2 far off."""
    return {1: situations.unit(400, "jeep", *SOUTH), 2: situations.unit(401, "jeep", 30, 90)}


def test_the_mission_a_scout_is_on_is_not_taken_from_it_and_the_other_gets_the_next_best():
    agent, planner = new_agent(tuning=HOLDING), MissionPlanner()
    offered = [mission("expansion_watch", SOUTH, 10), mission("main_edge", NORTH, 1)]
    planned(agent, planner, {2: two_jeeps()[2]}, offered, 1000)

    plans = [planned(agent, planner, two_jeeps(), offered, tick) for tick in (1005, 1010, 1015)]

    # Without the hold the near jeep takes the south place, the far one is turned to the north and back each cycle.
    assert [(taken(plan, 1)[0], taken(plan, 2)[0]) for plan in plans] == [(NORTH, SOUTH)] * 3
    assert [taken(plan, 2)[1] for plan in plans] == [None, None, None]


def test_without_the_hold_the_mission_is_taken_from_the_scout_on_it():
    agent, planner = new_agent(tuning=MISSIONS), MissionPlanner()
    offered = [mission("expansion_watch", SOUTH, 10)]
    planned(agent, planner, {2: two_jeeps()[2]}, offered, 1000)

    both = planned(agent, planner, two_jeeps(), offered, 1005)

    assert (taken(both, 1), taken(both, 2)) == ((SOUTH, "new"), (None, "switched"))


def test_a_young_mission_gives_way_only_to_one_1_5_times_better():
    agent, planner = new_agent(tuning=HOLDING), MissionPlanner()
    planned(agent, planner, jeep_at(), [mission("expansion_watch", SOUTH, 5)], 1000)
    both = [mission("expansion_watch", SOUTH, 5), mission("expansion_watch", NORTH, 7)]

    early = planned(agent, planner, jeep_at(), both, 1000 + 10 * SECOND)
    late = planned(agent, planner, jeep_at(), both, 1000 + 20 * SECOND)

    # 7 / 5 = 1.4 times: over the margin of 1.25, under 1.5, so kept for 20 s.
    assert taken(early) == (SOUTH, None) and taken(late) == (NORTH, "switched")


def test_tuning_mission_hold_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).scouting, ScoutingTuning()):
        assert tuning.mission_hold is True
        assert (tuning.mission_min_hold_seconds, tuning.mission_early_switch_ratio) == (20, 1.5)
