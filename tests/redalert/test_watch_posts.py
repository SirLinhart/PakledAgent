"""The main-edge and home-field watch posts within a scout's sight of what they watch (`watch_posts_in_sight`)."""

from __future__ import annotations

import dataclasses

import situations
from situations import ask
from switches_off import scouting_off, tuning_off
from test_scouting import TWO_STARTS, base, slots_agent
from ucla_agent.games.redalert import cells, scout_missions
from ucla_agent.games.redalert.sight import SightGrid
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import ScoutingTuning, Tuning

POSTS = dataclasses.replace(
    tuning_off(), scouting=scouting_off(scout_slots=True, scout_missions=True, watch_posts_in_sight=True)
)
OLD = dataclasses.replace(tuning_off(), scouting=scouting_off(scout_slots=True, scout_missions=True))
# An enemy barracks on the side of the enemy's start (74, 48) that faces our base (22, 48), and a field by the start.
BARRACKS = {"id": 700, "type": "tent", "x": 62, "y": 54, "hp": 100}
HOME_FIELD = OrePatch(center=(70, 56), cells=frozenset((x, y) for x in (69, 70, 71) for y in (55, 56, 57)))


def missions_of(tuning: Tuning, **observation) -> dict[str, list]:
    agent = slots_agent(tuning)
    ask(agent, base(tick=1000, **observation), situations.context(TWO_STARTS))
    agent.ctx.terrain = Terrain([HOME_FIELD])
    found = scout_missions.missions(agent.world, agent.ctx, SightGrid(), army_seen_tick=1000)
    kinds: dict[str, list] = {}
    for mission in found:
        kinds.setdefault(mission.kind, []).append(mission.cell)
    return kinds


def sees(unit_type: str, post: tuple[int, int], watched: tuple[int, int]) -> bool:
    """Whether a scout of a type standing at a post has the watched cell in sight, as the sight grid takes it."""
    agent = slots_agent(POSTS)
    ask(agent, base(tick=1000, units=[situations.unit(400, unit_type, *post)]), situations.context(TWO_STARTS))
    grid = SightGrid()
    grid.observe(agent.world, by_type=True)
    return grid.last_seen(watched) == 1000


def test_the_main_edge_post_lies_3_cells_from_the_enemy_building_toward_our_base_and_an_e1_and_a_dog_see_it():
    (post,) = missions_of(POSTS, visible_enemy_buildings=[BARRACKS])["main_edge"]

    assert cells.distance(post, (62, 54)) <= 3.5
    assert sees("e1", post, (62, 54)) and sees("dog", post, (62, 54))


def test_the_old_main_edge_post_is_beyond_a_riflemans_sight():
    (post,) = missions_of(OLD, visible_enemy_buildings=[BARRACKS])["main_edge"]

    assert cells.distance(post, (62, 54)) > 4


def test_the_home_field_post_lies_3_cells_from_its_center():
    (post,) = missions_of(POSTS)["enemy_home_field"]

    assert cells.distance(post, HOME_FIELD.center) <= 3.5 and sees("e1", post, HOME_FIELD.center)


def test_a_post_in_a_defenses_reach_is_moved_out_toward_our_base():
    pillbox = {"id": 701, "type": "pbox", "x": 60, "y": 54, "hp": 100}
    (post,) = missions_of(POSTS, visible_enemy_buildings=[BARRACKS, pillbox])["main_edge"]

    assert cells.distance(post, (60, 54)) > 6 and post[0] < 60


def test_tuning_watch_posts_in_sight_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).scouting, ScoutingTuning()):
        assert tuning.watch_posts_in_sight is True
