"""The infantry scout's run through the enemy base (`[scouting] infantry_scout_run_through`): entry, the enemy's start
and the exit beyond it, by an approach that turns with every run, hits and all."""

from __future__ import annotations

import dataclasses
import zlib

import situations
from situations import ask, base_state
from switches_off import new_agent, scouting_off, tuning_off
from ucla_agent.games.redalert import rush_scout
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.tuning import ScoutingTuning, Tuning

YARD = ("fact", 22, 48)
POWER = ("powr", 19, 52)
BARRACKS = ("tent", 18, 44)
SCOUT_ID = 300
# The enemy's start (74, 48), our base (22, 48).
ENEMY_BASE = [74, 48]
RUNNING = dataclasses.replace(
    tuning_off(),
    scouting=scouting_off(
        rush_detection=True,
        scout_slots=True,
        scout_missions=True,
        scout_cautious=True,
        scout_wide_berth=True,
        infantry_scout_run_through=True,
    ),
)


def at(seconds: float, *units: dict, **observation) -> dict:
    return base_state(
        "england", YARD, POWER, BARRACKS, tick=round(seconds * TICKS_PER_SECOND), units=list(units), **observation
    )


def scout(x: int, y: int, *, unit_type: str = "e1", hp: int = 100, idle: bool = False) -> dict:
    return situations.unit(SCOUT_ID, unit_type, x, y, hp=hp, idle=idle)


def moves_of(orders: list[dict]) -> list[list[int]]:
    return [order["to"] for order in orders if order["cmd"] == "move" and SCOUT_ID in order["units"]]


def running_agent(tuning: Tuning = RUNNING, unit_type: str = "e1"):
    agent = new_agent(ScoutingManager(), tuning=tuning)
    first = ask(agent, at(40, scout(18, 46, unit_type=unit_type, idle=True)))
    return agent, first


def run_events(match_record) -> list[dict]:
    return [event for event in match_record("scout_slot") if event.get("kind") == rush_scout.RUN_THROUGH]


def first_legs(agent) -> list[tuple[int, int]]:
    """The legs of the match's first run, its approach by the test match's session id."""
    approach = rush_scout.approach_of(0, agent.ctx.session_id)
    return rush_scout.run_legs(approach, agent.world, agent.ctx)


def test_a_rifleman_runs_in_by_the_entry_through_the_start_and_out_the_far_side(match_record):
    agent, first = running_agent()
    entry, start, exit_cell = first_legs(agent)

    to_start = ask(agent, at(60, scout(*entry)))
    to_exit = ask(agent, at(80, scout(start[0] - 3, start[1])))
    to_post = ask(agent, at(100, scout(*exit_cell)))

    assert moves_of(first) == [list(entry)] and moves_of(to_start) == [ENEMY_BASE]
    assert exit_cell[0] > ENEMY_BASE[0] and moves_of(to_exit) == [list(exit_cell)]
    assert moves_of(to_post) == [list(rush_scout.watch_post("e1", agent.world, agent.ctx))]
    phases = [(event["phase"], event.get("approach")) for event in run_events(match_record)]
    assert phases == [("start", rush_scout.approach_of(0, agent.ctx.session_id)), ("done", None)]


def test_a_hit_does_not_turn_the_rifleman_back():
    agent, _ = running_agent()
    entry, _, _ = first_legs(agent)
    ask(agent, at(60, scout(*entry)))

    hit = ask(agent, at(62, scout(entry[0] + 3, entry[1], hp=40)))

    assert moves_of(hit) in ([], [ENEMY_BASE])


def test_a_rifleman_stuck_short_of_the_start_goes_on_to_the_exit():
    agent, _ = running_agent()
    entry, _, exit_cell = first_legs(agent)
    ask(agent, at(60, scout(*entry)))
    ask(agent, at(70, scout(66, 48, idle=True)))

    stuck = ask(agent, at(76, scout(66, 48, idle=True)))

    assert moves_of(stuck) == [list(exit_cell)]


def test_the_approach_turns_with_every_run_and_starts_by_the_session():
    assert [rush_scout.approach_of(runs, None) for runs in range(4)] == ["direct", "left", "right", "direct"]
    first = rush_scout.APPROACHES[zlib.crc32(b"match-7") % 3]
    assert rush_scout.approach_of(0, "match-7") == first


def test_a_flank_enters_12_cells_to_one_side_and_leaves_by_the_other():
    agent, _ = running_agent()

    left = rush_scout.run_legs(rush_scout.LEFT, agent.world, agent.ctx)
    right = rush_scout.run_legs(rush_scout.RIGHT, agent.world, agent.ctx)

    assert left[0] == (61, 60) and left[1] == tuple(ENEMY_BASE) and left[2][1] < ENEMY_BASE[1]
    assert right[0] == (61, 36) and right[2][1] > ENEMY_BASE[1]


def test_infantry_runs_and_a_vehicle_makes_the_pass_as_before():
    agent, _ = running_agent()

    runners = {kind: rush_scout.is_runner(kind, agent.world, agent.ctx) for kind in ("e1", "dog", "jeep", "ftrk")}

    assert runners == {"e1": True, "dog": True, "jeep": False, "ftrk": False}


def test_switch_off_the_rifleman_drives_straight_at_the_start():
    off = dataclasses.replace(RUNNING, scouting=dataclasses.replace(RUNNING.scouting, infantry_scout_run_through=False))
    _, first = running_agent(tuning=off)

    assert moves_of(first) == [ENEMY_BASE]


def test_tuning_run_through_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).scouting, ScoutingTuning()):
        assert tuning.infantry_scout_run_through is True
        assert (tuning.scout_run_side_cells, tuning.scout_run_exit_cells) == (12, 10)
