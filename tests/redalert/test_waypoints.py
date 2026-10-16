"""Waypoint routing: the [routing] keys, the graph of waypoints, the way through it, and the users that go by it."""

from __future__ import annotations

import dataclasses
import shutil
import statistics
import time
from pathlib import Path

import pytest

from fake_game import FakeRedAlert
from situations import REPO_ROOT, OpeningThen, StubChooser, ask, base_state, context, tracked_items
from switches_off import expansion_off, new_agent, placement_off, stepping_off, tuning_off
from ucla_agent.core.config import ConfigError
from ucla_agent.games.redalert import cells, command_army, coverage, expansion, raid_routes, scout_steering
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.commander import MAIN_ROLE, Commander
from ucla_agent.games.redalert.composition import HOLD, RAID
from ucla_agent.games.redalert.creep import NearestFarField
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.raids import RaidManager, launch_blocker
from ucla_agent.games.redalert.sight import SightGrid
from ucla_agent.games.redalert.route_walk import RouteWalks
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import TUNING_FILE, CommanderTuning, RoutingTuning, Tuning
from ucla_agent.games.redalert.upkeep import UpkeepManager
from ucla_agent.games.redalert.waypoints import WaypointRoute, Waypoints, log_route
from ucla_agent.games.redalert.world import Unit, World
from whole_match import COMPUTE_P95_MS, WHOLE_MATCH_STEPS, WholeMatch, play_whole_match, with_ore

# The shipped switch set off, and waypoint routing on.
WAYPOINTS_ON = tuning_off(routing=RoutingTuning(waypoints=True))
BASE_CENTER = (22, 48)
# The four cells 14 cells (scout_view_cells) north, east, south and west of the enemy's start.
FLANKS = [(74, 34), (88, 48), (74, 62), (60, 48)]
# The corners and middles of the sides of the 96 by 96 map, 4 cells inside it.
EDGES = [(4, 4), (48, 4), (91, 4), (4, 48), (91, 48), (4, 91), (48, 91), (91, 91)]
# Three ore fields: by the base, south of the middle, and far east, short of the enemy.
NEAR_FIELD, SOUTH_FIELD, FAR_FIELD = (22, 40), (40, 66), (58, 48)


@pytest.fixture
def tuning_root(tmp_path: Path) -> Path:
    """A folder standing in for the repository root, with a copy of the tracked tuning file."""
    target = tmp_path / TUNING_FILE
    target.parent.mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / TUNING_FILE, target)
    return tmp_path


def rewrite(root: Path, old: str, new: str) -> None:
    """Replace one piece of text in the tuning file under `root`; the piece has to be there."""
    path = root / TUNING_FILE
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


# -- the [routing] keys ------------------------------------------------------------------------------------------------


def test_settings_the_routing_table_ships_off_with_30_and_2_cells_built_in_and_in_the_tracked_file():
    routing = Tuning.from_file(REPO_ROOT).routing

    assert routing == RoutingTuning() == RoutingTuning(waypoints=False, edge_max_cells=30, edge_clear_margin_cells=2)
    assert Tuning().routing == routing


def test_settings_values_of_the_file_replace_the_built_in_ones(tuning_root):
    rewrite(tuning_root, "waypoints = false", "waypoints = true")
    rewrite(tuning_root, "edge_max_cells = 30", "edge_max_cells = 24")
    rewrite(tuning_root, "edge_clear_margin_cells = 2", "edge_clear_margin_cells = 0")

    assert Tuning.from_file(tuning_root).routing == RoutingTuning(True, 24, 0)


@pytest.mark.parametrize(
    "old, new, named",
    [
        ("waypoints = false\n", "", r"waypoints in \[routing\]"),
        ("waypoints = false", "waypoints = 0", r"waypoints in \[routing\] must be true or false"),
        ("edge_max_cells = 30\n", "", r"edge_max_cells in \[routing\]"),
        ("edge_max_cells = 30", "edge_max_cells = -1", r"edge_max_cells in \[routing\]"),
        ("edge_clear_margin_cells = 2", "edge_clear_margin_cells = 1.5", r"edge_clear_margin_cells in \[routing\]"),
        ("edge_clear_margin_cells = 2", "edge_clear_margin_cells = 2\nedge_cells = 3", r"edge_cells"),
    ],
    ids=["switch missing", "switch not true or false", "max missing", "max negative", "margin a fraction", "unknown"],
)
def test_settings_a_routing_key_missing_unknown_or_of_the_wrong_kind_is_a_config_error_naming_it(
    tuning_root, old, new, named
):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=named):
        Tuning.from_file(tuning_root)


def test_settings_the_routing_group_is_replaced_like_any_other():
    switched_on = dataclasses.replace(Tuning(), routing=RoutingTuning(waypoints=True))

    assert switched_on.routing.waypoints and switched_on.raids == Tuning().raids


# -- the graph ---------------------------------------------------------------------------------------------------------


def enemy(enemy_id: int, enemy_type: str, x: int, y: int) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


def field(center: tuple[int, int]) -> OrePatch:
    """An ore field of the 3 by 3 cells around its center."""
    x, y = center
    return OrePatch(center, frozenset((x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)))


def three_fields(blocked=()) -> Terrain:
    """A terrain of the three ore fields, with these cells blocked besides the ore."""
    patches = [field(center) for center in (NEAR_FIELD, SOUTH_FIELD, FAR_FIELD)]
    return Terrain(patches, [*blocked, *(cell for patch in patches for cell in patch.cells)])


def world_seeing(*buildings: dict) -> World:
    """The world of an agent that has seen one state of its base at tick 1000 with these enemy buildings in sight."""
    agent = new_agent(tuning=WAYPOINTS_ON)
    ask(agent, base_state("england", ("fact", *BASE_CENTER), tick=1000, visible_enemy_buildings=list(buildings)))
    return agent.world


def graph(world: World, terrain: Terrain | None = None, walked=(), tuning: Tuning = WAYPOINTS_ON) -> Waypoints:
    """The graph of waypoints built from a world, a terrain (the three fields by default) and walked routes."""
    built = Waypoints()
    built.update(world, three_fields() if terrain is None else terrain, walked, tuning)
    return built


def wall(x: int, top: int, bottom: int) -> list[tuple[int, int]]:
    """The cells of column `x` from `top` to `bottom`."""
    return [(x, y) for y in range(top, bottom + 1)]


def test_graph_the_nodes_are_the_ore_fields_the_base_the_enemy_flanks_and_the_map_edges():
    built = graph(world_seeing())

    assert built.nodes == (NEAR_FIELD, SOUTH_FIELD, FAR_FIELD, BASE_CENTER, *FLANKS, *EDGES)


def test_graph_two_nodes_within_30_cells_with_a_clear_line_are_joined_and_farther_ones_are_not():
    built = graph(world_seeing())

    assert built.has_edge(SOUTH_FIELD, FAR_FIELD) and built.has_edge(FAR_FIELD, SOUTH_FIELD)
    assert cells.distance(BASE_CENTER, FAR_FIELD) > 30 and not built.has_edge(BASE_CENTER, FAR_FIELD)


def test_graph_blocked_terrain_between_two_nodes_removes_their_edge():
    open_ground = graph(world_seeing())
    walled = graph(world_seeing(), three_fields(blocked=wall(49, 50, 64)))

    assert open_ground.has_edge(SOUTH_FIELD, FAR_FIELD) and not walled.has_edge(SOUTH_FIELD, FAR_FIELD)
    assert walled.has_edge(BASE_CENTER, SOUTH_FIELD)


@pytest.mark.parametrize("pillbox_x, joined", [(31, False), (33, True)], ids=["9 cells off", "11 cells off"])
def test_graph_an_edge_within_2_cells_of_a_pillbox_s_cover_is_removed(pillbox_x, joined):
    # The line from the base to the near field runs down x = 22; the pillbox covers 6 + 2 cells around it.
    built = graph(world_seeing(enemy(600, "pbox", pillbox_x, 44)))

    assert built.has_edge(BASE_CENTER, NEAR_FIELD) is joined


def test_graph_a_node_on_covered_ground_is_no_node():
    built = graph(world_seeing(enemy(600, "pbox", 60, 50)))

    assert FAR_FIELD not in built.nodes and (60, 48) not in built.nodes and SOUTH_FIELD in built.nodes


def test_graph_a_walked_route_adds_an_edge_between_its_ends_whatever_the_straight_line():
    walk = ((8, 70), (6, 80), (12, 88), (20, 88))
    walled = three_fields(blocked=wall(14, 72, 87))

    without = graph(world_seeing(), walled)
    walked = graph(world_seeing(), walled, [walk])

    assert (8, 70) not in without.nodes and not without.is_clear((8, 70), (20, 88))
    assert walked.has_edge((8, 70), (20, 88)) and {(8, 70), (20, 88)} <= set(walked.nodes)


def test_graph_a_walked_route_ending_on_blocked_ground_adds_no_edge():
    walk = ((8, 70), (6, 80), (12, 88), (20, 88))

    walked = graph(world_seeing(), three_fields(blocked=[*wall(14, 72, 87), (20, 88)]), [walk])

    assert (20, 88) not in walked.nodes and (8, 70) in walked.nodes and not walked.has_edge((8, 70), (20, 88))


def test_graph_a_walked_route_a_pillbox_now_covers_adds_no_edge():
    walk = ((8, 70), (6, 80), (12, 88), (20, 88))

    walked = graph(world_seeing(enemy(600, "pbox", 2, 84)), three_fields(blocked=wall(14, 72, 87)), [walk])

    assert not walked.has_edge((8, 70), (20, 88))


def test_graph_is_built_by_the_match_context_with_the_switch_on_and_again_when_a_defense_is_seen():
    agent = new_agent(tuning=WAYPOINTS_ON)
    ask(agent, base_state("england", ("fact", *BASE_CENTER), tick=1000))
    agent.ctx.terrain = three_fields()
    ask(agent, base_state("england", ("fact", *BASE_CENTER), tick=1005))
    before = agent.ctx.waypoints.has_edge(SOUTH_FIELD, FAR_FIELD)

    ask(
        agent,
        base_state("england", ("fact", *BASE_CENTER), tick=1010, visible_enemy_buildings=[enemy(600, "pbox", 49, 57)]),
    )

    assert before and not agent.ctx.waypoints.has_edge(SOUTH_FIELD, FAR_FIELD)


def test_graph_with_the_switch_off_the_match_context_builds_none():
    agent = new_agent()
    ask(agent, base_state("england", ("fact", *BASE_CENTER), tick=1000))

    assert agent.ctx.waypoints.nodes == ()


@pytest.mark.parametrize("walked_routes, published", [(True, (((28, 46), (37, 50)),)), (False, ())], ids=["on", "off"])
def test_graph_the_routes_walked_are_published_in_the_match_context_while_routes_are_walked(walked_routes, published):
    placement = placement_off(creep_from_opening=True, walked_routes=walked_routes)
    agent = new_agent(tuning=tuning_off(placement=placement, routing=RoutingTuning(waypoints=True)))
    ask(agent, base_state("england", ("fact", *BASE_CENTER), tick=1000))
    walks = RouteWalks(NearestFarField())
    walks.routes[field(FAR_FIELD)] = ((28, 46), (37, 50))

    walks.intents(agent.world, agent.ctx)

    assert agent.ctx.walked_routes == published


def test_graph_is_not_built_before_the_map_the_base_and_the_enemy_s_start_are_known():
    built = Waypoints()

    built.update(World(tracked_items(), None, Tuning().world), three_fields(), (), WAYPOINTS_ON)

    assert built.nodes == ()


def test_graph_an_update_from_what_it_was_built_from_keeps_it_as_it_is():
    world, terrain = world_seeing(enemy(600, "pbox", 49, 57)), three_fields()
    built = graph(world, terrain)
    nodes = built.nodes

    built.update(world, terrain, (), WAYPOINTS_ON)

    assert built.nodes == nodes and not built.has_edge(SOUTH_FIELD, FAR_FIELD)


# -- the way through the graph -----------------------------------------------------------------------------------------

# A pillbox (cover 6 + 2 cells) on the straight line from the base to the far field.
MIDDLE_PILLBOX = enemy(600, "pbox", 40, 48)


def walked_cells(start: tuple[int, int], legs) -> list[tuple[int, int]]:
    """Every cell a unit passes going from `start` to each of `legs` in turn in a straight line."""
    stops = [start, *legs]
    return [cell for first, second in zip(stops, stops[1:]) for cell in cells.line(first, second)]


def test_route_with_a_pillbox_on_the_direct_line_to_a_far_field_goes_by_an_ore_field_node_clear_of_its_cover():
    world = world_seeing(MIDDLE_PILLBOX)
    built = graph(world)

    found = built.route(BASE_CENTER, FAR_FIELD)

    def is_covered(cell):
        return coverage.is_covered(cell, world.known_enemy_defenses, world.items, WAYPOINTS_ON.coverage)

    assert any(map(is_covered, cells.line(BASE_CENTER, FAR_FIELD)))
    assert found.legs == (SOUTH_FIELD, FAR_FIELD) and found.nodes[0] == SOUTH_FIELD
    assert not any(map(is_covered, found.legs)) and not any(map(is_covered, walked_cells(BASE_CENTER, found.legs)))
    assert found.length == pytest.approx(2 * cells.distance(BASE_CENTER, SOUTH_FIELD))


def test_route_a_node_on_the_start_is_left_out_and_a_goal_off_the_nodes_comes_last():
    found = graph(world_seeing(MIDDLE_PILLBOX)).route(BASE_CENTER, (56, 50))

    assert (found.start, found.goal) == (BASE_CENTER, (56, 50))
    assert found.nodes == (SOUTH_FIELD,) and found.legs == (SOUTH_FIELD, (56, 50))


def test_route_a_start_with_no_node_within_edge_max_cells_is_joined_to_the_nearest_node_it_has_a_clear_line_to():
    narrow = dataclasses.replace(WAYPOINTS_ON, routing=RoutingTuning(True, 10, 2))

    found = graph(world_seeing(), tuning=narrow).route((36, 30), (30, 52))

    assert found.legs == (NEAR_FIELD, BASE_CENTER, (30, 52))


@pytest.mark.parametrize(
    "buildings, blocked, start, goal",
    [
        ([MIDDLE_PILLBOX], (), BASE_CENTER, (44, 50)),
        ([], wall(49, 0, 95), BASE_CENTER, FAR_FIELD),
        ([enemy(600, "pbox", 22, 40)], (), (22, 44), (8, 40)),
    ],
    ids=["goal near cover", "a wall across the map", "start in cover"],
)
def test_route_with_no_safe_chain_is_none(buildings, blocked, start, goal):
    built = graph(world_seeing(*buildings), three_fields(blocked=blocked))

    assert built.route(start, goal) is None


def test_route_before_the_graph_is_built_is_none():
    assert Waypoints().route(BASE_CENTER, FAR_FIELD) is None


def spread_fields() -> Terrain:
    """A terrain of 30 ore fields spread over the map, 15 cells apart."""
    patches = [field((8 + 15 * column, 8 + 16 * row)) for row in range(6) for column in range(5)]
    return Terrain(patches, [cell for patch in patches for cell in patch.cells])


def median_ms(action, times: int) -> float:
    """The median of the milliseconds an action takes over a number of runs."""
    taken = []
    for _ in range(times):
        started = time.perf_counter()
        action()
        taken.append((time.perf_counter() - started) * 1000)
    return statistics.median(taken)


def test_route_the_graph_is_built_within_20_ms_and_a_way_found_within_2_ms():
    pillboxes = [enemy(600 + number, "pbox", 56 + 12 * (number % 3), 10 + 20 * (number // 3)) for number in range(12)]
    world, terrain = world_seeing(*pillboxes), spread_fields()
    built = graph(world, terrain)

    build_ms = median_ms(lambda: Waypoints().update(world, terrain, (), WAYPOINTS_ON), 5)
    route_ms = median_ms(lambda: built.route(BASE_CENTER, (88, 88)), 20)

    assert len(built.nodes) > 25 and built.route(BASE_CENTER, (88, 88)) is not None
    assert build_ms <= 20 and route_ms <= 2


# -- the route event ---------------------------------------------------------------------------------------------------


def test_event_a_route_taken_is_written_with_its_user_start_goal_node_count_and_length(match_record):
    log_route("raid", WaypointRoute((32, 48), (52, 48), ((42, 62),), 34.4053), 1000)

    assert match_record("route") == [
        {"tick": 1000, "user": "raid", "start": [32, 48], "goal": [52, 48], "node_count": 1, "length_cells": 34.41}
    ]


# -- the raids ---------------------------------------------------------------------------------------------------------

MAP = (0, 0, 96, 96)
# The raid's staging point and the approach cell 8 cells short of a harvester at (60, 48).
STAGING, APPROACH = (32, 48), (52, 48)
FAR_HARVESTER = enemy(500, "harv", 60, 48)
# A pillbox whose cover (6 + 2 cells) takes in the straight line from the staging point to the approach cell.
NORTH_PILLBOX = enemy(600, "pbox", 42, 40)
# The fake game's map with one more ore field, south of the pillbox.
RAID_FIELD = (42, 62)


def covered_by(*defenses: dict):
    """Whether a cell lies within the cover (6 + 2 cells) of pillboxes at the given actors' cells."""
    return lambda cell: any(cells.is_within(cell, (defense["x"], defense["y"]), 8) for defense in defenses)


def found_route(*legs: tuple[int, int]):
    """A router that finds a way of these cells from any start to the last of them."""
    return lambda start, goal: WaypointRoute(start, goal, legs[:-1], 30.0)


def no_router(start, goal):
    raise AssertionError("the router is not to be asked")


def test_raids_a_covered_straight_line_goes_by_the_router_s_way_with_every_cell_passed_in_the_path():
    way = raid_routes.route(
        STAGING, STAGING, (60, 48), MAP, covered_by(NORTH_PILLBOX), Tuning().raids, found_route(RAID_FIELD, APPROACH)
    )

    assert way.legs == (RAID_FIELD, APPROACH) and way.waypoint_route.start == STAGING
    assert way.path == (*cells.line(STAGING, RAID_FIELD), *cells.line(RAID_FIELD, APPROACH)[1:])


def test_raids_a_clear_straight_line_asks_no_router_and_a_router_with_no_way_leaves_the_side_waypoint():
    straight = raid_routes.route(STAGING, STAGING, (60, 48), MAP, lambda cell: False, Tuning().raids, no_router)
    by_side = raid_routes.route(
        STAGING, STAGING, (60, 48), MAP, covered_by(NORTH_PILLBOX), Tuning().raids, lambda start, goal: None
    )

    assert straight.legs == (APPROACH,) and straight.waypoint_route is None
    assert by_side.legs == ((42, 60), APPROACH) and by_side.waypoint_route is None


def light_tanks() -> list[dict]:
    """Four light tanks at the staging point."""
    return [
        {"id": 300 + number, "type": "1tnk", "x": 32, "y": 46 + number, "hp": 100, "act": None, "idle": True}
        for number in range(4)
    ]


class FixedPlan:
    """A plan selector that keeps the agent in one plan."""

    def __init__(self, plan: str) -> None:
        self.plan = plan

    def select(self, world, ctx, choice) -> str:
        return self.plan


def allied_base(tick: int, **observation) -> dict:
    """A state of an Allied base with a war factory and a service depot."""
    buildings = (
        ("fact", 22, 48),
        ("powr", 19, 52),
        ("tent", 18, 44),
        ("proc", 26, 44),
        ("weap", 18, 56),
        ("fix", 14, 56),
    )
    return base_state("england", *buildings, tick=tick, **observation)


def raid_orders(tuning: Tuning) -> tuple[list[dict], RaidManager]:
    """The orders of a raid agent with a ready group at the staging point, the far harvester in sight and the north
    pillbox remembered, on the fake game's map with the raid field; and its raid manager."""
    manager = RaidManager()
    agent = new_agent(manager, tuning=tuning, plan_selector=FixedPlan(RAID))
    game_config = with_ore(FakeRedAlert().game_config, [RAID_FIELD])
    shown = {"units": light_tanks(), "visible_enemies": [FAR_HARVESTER], "visible_enemy_buildings": [NORTH_PILLBOX]}
    return ask(agent, allied_base(1000, **shown), context(game_config)), manager


def test_raids_with_the_switch_on_a_raid_goes_by_the_waypoint_route_and_writes_it(match_record):
    orders, manager = raid_orders(WAYPOINTS_ON)

    assert orders == [{"cmd": "move", "units": [300, 301, 302, 303], "to": list(RAID_FIELD)}]
    assert manager.mission.legs == [RAID_FIELD, APPROACH]
    (written,) = match_record("route")
    assert (written["user"], written["start"], written["goal"], written["node_count"]) == (
        "raid",
        [32, 48],
        [52, 48],
        1,
    )


def test_raids_with_the_switch_off_a_raid_goes_by_today_s_side_waypoint(match_record):
    orders, manager = raid_orders(tuning_off())

    assert orders == [{"cmd": "move", "units": [300, 301, 302, 303], "to": [42, 60]}]
    assert manager.mission.legs == [(42, 60), APPROACH] and match_record("route") == []


def test_raids_with_no_safe_chain_a_raid_falls_back_to_the_side_waypoint(match_record):
    # With joins of 10 cells at most, no chain of nodes leads from the base's side to the enemy's.
    sparse = dataclasses.replace(WAYPOINTS_ON, routing=RoutingTuning(True, 10, 2))

    orders, _ = raid_orders(sparse)

    assert orders == [{"cmd": "move", "units": [300, 301, 302, 303], "to": [42, 60]}] and match_record("route") == []


def test_raids_the_launch_check_finds_the_waypoint_route_without_writing_it(match_record):
    manager = RaidManager()
    agent = new_agent(manager, tuning=WAYPOINTS_ON, plan_selector=FixedPlan(RAID))
    shown = {"units": light_tanks(), "visible_enemies": [FAR_HARVESTER], "visible_enemy_buildings": [NORTH_PILLBOX]}
    ask(agent, allied_base(1000, **shown), context(with_ore(FakeRedAlert().game_config, [RAID_FIELD])))

    blocker = launch_blocker(agent.world, agent.ctx, True)

    # The one route written is the raid's that set out; the check after it writes none.
    assert blocker is None and len(match_record("route")) == 1


# -- the scouts --------------------------------------------------------------------------------------------------------

NORTH_START, NORTH_END = (30, 20), (70, 20)
# A pillbox on the scout's straight way along the north.
SCOUT_PILLBOX = enemy(600, "pbox", 50, 20)


def scout_world(tuning: Tuning, *buildings: dict) -> tuple:
    """The world and match context of an agent under a tuning that has seen its base with these enemy buildings."""
    agent = new_agent(tuning=tuning)
    ask(agent, base_state("england", ("fact", *BASE_CENTER), tick=1000, visible_enemy_buildings=list(buildings)))
    return agent.world, agent.ctx


def scout_unit(x: int, y: int) -> Unit:
    """A scout of the world as the steering reads it."""
    return Unit(id=300, type="e1", x=x, y=y, hp=100, idle=False, activity="move", stance=None)


def scout_way(tuning: Tuning, start=NORTH_START, end=NORTH_END, *buildings) -> tuple:
    """A scout's way from `start` to `end` with the scout pillbox, or the buildings given, remembered; and its world."""
    world, ctx = scout_world(tuning, *(buildings or (SCOUT_PILLBOX,)))
    return scout_steering.route(start, end, scout_steering.Dangers(world, ctx), world, ctx), world, ctx


def test_scouts_with_the_switch_on_a_way_crossing_danger_goes_by_the_waypoint_route_clear_of_cover():
    way, world, ctx = scout_way(WAYPOINTS_ON)

    def is_covered(cell):
        return coverage.is_covered(cell, world.known_enemy_defenses, world.items, ctx.tuning.coverage)

    assert way == [(48, 4), NORTH_END] and not any(map(is_covered, walked_cells(NORTH_START, way)))


def test_scouts_with_the_switch_off_a_way_crossing_danger_goes_by_today_s_side_waypoint():
    way, world, _ = scout_way(tuning_off())

    sides = [side for offset in (14, 28) for side in raid_routes.side_waypoints(NORTH_START, NORTH_END, MAP, offset)]
    assert len(way) == 2 and way[0] in sides and way[-1] == NORTH_END


def test_scouts_with_the_switch_on_a_clear_straight_way_is_still_the_straight_line():
    way, _, _ = scout_way(WAYPOINTS_ON, NORTH_START, NORTH_END, enemy(600, "pbox", 50, 70))

    assert way == [NORTH_END]


def test_scouts_with_no_safe_chain_the_way_falls_back_to_today_s_side_waypoint():
    sparse = dataclasses.replace(WAYPOINTS_ON, routing=RoutingTuning(True, 10, 2))

    assert scout_way(sparse)[0] == scout_way(tuning_off())[0]


def test_scouts_a_waypoint_route_whose_leg_passes_the_map_s_middle_is_not_taken():
    # The only way round the pillbox north of the middle is by an ore field 4 cells from the map's middle (48, 48).
    start, end = (36, 30), (60, 30)
    world, ctx = scout_world(WAYPOINTS_ON, enemy(600, "pbox", 48, 26))
    ctx.terrain = Terrain([field((48, 44))], field((48, 44)).cells)
    ctx.waypoints.update(world, ctx.terrain, (), ctx.tuning)

    way = scout_steering.route(start, end, scout_steering.Dangers(world, ctx), world, ctx)
    mission_way = scout_steering.route(
        start, end, scout_steering.Dangers(world, ctx), world, ctx, keeps_from_center=False
    )

    assert ctx.waypoints.route(start, end).legs == ((48, 44), end) and mission_way == [(48, 44), end]
    assert way is not None and way[0] != (48, 44)


def test_scouts_a_course_redrawn_by_the_waypoint_route_writes_one_route_event(match_record):
    world, ctx = scout_world(WAYPOINTS_ON, SCOUT_PILLBOX)
    course = scout_steering.Course()
    course.go(NORTH_END, [NORTH_END], scout_steering.SCRIPT, 900)
    scout = scout_unit(*NORTH_START)

    first = scout_steering.steer(course, scout, [], SightGrid(), scout_steering.Dangers(world, ctx), world, ctx)
    second = scout_steering.steer(course, scout, [], SightGrid(), scout_steering.Dangers(world, ctx), world, ctx)

    assert first.cell == second.cell == (48, 4) and course.legs == [(48, 4), NORTH_END]
    (written,) = match_record("route")
    assert (written["user"], written["start"], written["goal"], written["node_count"]) == (
        "scout",
        [30, 20],
        [70, 20],
        1,
    )


def test_scouts_a_mission_way_drawn_again_every_cycle_writes_its_route_once_and_again_for_a_new_destination(
    match_record,
):
    world, ctx = scout_world(WAYPOINTS_ON, SCOUT_PILLBOX)
    course, scout = scout_steering.Course(), scout_unit(*NORTH_START)

    for tick, destination in ((1000, NORTH_END), (1005, NORTH_END), (1010, (70, 24))):
        dangers = scout_steering.Dangers(world, ctx)
        way = scout_steering.route(NORTH_START, destination, dangers, world, ctx, keeps_from_center=False)
        course.go(destination, way, scout_steering.MISSION, tick)
        scout_steering.steer_to_mission(course, scout, dangers, world, ctx)

    assert [written["goal"] for written in match_record("route")] == [[70, 20], [70, 24]]


# -- the commander's flank ---------------------------------------------------------------------------------------------

# A war factory to flank to, a pillbox in range of main's straight way there, and two ore fields south of the way.
WAR_FACTORY, FLANK_PILLBOX = enemy(641, "weap", 72, 52), enemy(600, "pbox", 56, 48)
FLANK_FIELDS = [(44, 64), (60, 64)]
FLANK_LEGS = ((44, 64), (60, 64), (72, 52))
# An advance is the plain attack-move on the objective, and main's flank one move to each cell of its way, unstepped.
# The commander's flank is offered only while the script does not flank by the attack's approach (see
# `attack_approach`): stepping_off turns the approach off as well.
NO_MARCH = stepping_off(
    dataclasses.replace(Tuning(), siege=dataclasses.replace(Tuning().siege, march_when_clear=False))
)
FLANK_ON = dataclasses.replace(NO_MARCH, routing=RoutingTuning(waypoints=True))


def tanks(x: int, y: int, *, idle: bool = True) -> list[dict]:
    """Six heavy tanks in a column of three from a cell, numbered from 400, standing idle or on their way."""
    return [
        {"id": 400 + n, "type": "3tnk", "x": x + n % 3, "y": y + n // 3, "hp": 100, "act": None, "idle": idle}
        for n in range(6)
    ]


def soviet_base(tick: int, units: list[dict]) -> dict:
    """A Soviet base with these units of main, the war factory and the pillbox remembered."""
    buildings = (("fact", 22, 48), ("powr", 19, 52), ("barr", 18, 44))
    return base_state(
        "russia", *buildings, tick=tick, units=units, visible_enemy_buildings=[FLANK_PILLBOX, WAR_FACTORY]
    )


def flank_scene(tuning: Tuning) -> tuple:
    """An agent with the combat manager alone under PRESS, main at (40, 47) after a cycle on the fake map with the two
    flank fields, its combat manager and the flank main's question offers."""
    combat = CombatManager()
    agent = new_agent(combat, tuning=tuning, plan_selector=FixedPlan("PRESS"))
    ask(agent, soviet_base(12500, tanks(40, 47)), context(with_ore(FakeRedAlert().game_config, FLANK_FIELDS)))
    main = [agent.world.combat_units[unit_id] for unit_id in agent.ctx.groups.members(MAIN)]
    question = command_army.question(main, (), {}, command_army.EnemyPeak(), agent.world, agent.ctx)
    flank = next(action for action in question.actions if action.kind == "flank")
    return agent, combat, flank


def moves(agent, tick: int, units: list[dict]) -> list[tuple[str, list[int]]]:
    """The commands and cells of the orders of a cycle at a tick with main's units where given."""
    return [(order["cmd"], order["to"]) for order in ask(agent, soviet_base(tick, units))]


def test_flank_with_the_switch_on_the_flank_goes_via_the_waypoint_route_s_first_cell():
    _, _, flank = flank_scene(FLANK_ON)

    assert flank.via == (44, 64) and flank.route.legs == FLANK_LEGS and flank.cell == (72, 52)
    assert flank.head == "flank via (44,64) to weap (72,52), 49 cells"


def commanded_aims(tuning: Tuning, *cycles: tuple[int, list[dict]]) -> list[tuple[str, list[int]]]:
    """The command and cell of the one order main's flank stands for in each of these cycles (tick, units)."""
    agent, combat, flank = flank_scene(tuning)
    combat.command(flank)
    aims = []
    for tick, units in cycles:
        ask(agent, soviet_base(tick, units))
        aims.append((combat.commanded_order["cmd"], combat.commanded_order["to"]))
    return aims


def test_flank_main_moves_to_each_cell_of_the_route_in_turn_within_3_cells_then_marches_on_the_objective():
    aims = commanded_aims(
        FLANK_ON,
        (12505, tanks(40, 47)),
        (12510, tanks(42, 60, idle=False)),
        (12515, tanks(43, 63, idle=False)),
        (12520, tanks(59, 63, idle=False)),
    )

    # With its centroid 3.6 cells short of (44, 64) main keeps on to it; 0.5 cells short it goes on to the next.
    assert aims == [("move", [44, 64]), ("move", [44, 64]), ("move", [60, 64]), ("attack_move", [72, 52])]


def test_flank_main_standing_idle_within_6_cells_of_a_cell_of_the_route_goes_on_to_the_next():
    aims = commanded_aims(FLANK_ON, (12505, tanks(40, 47)), (12510, tanks(42, 60)))

    assert aims == [("move", [44, 64]), ("move", [60, 64])]


def test_flank_with_the_switch_off_the_flank_goes_by_today_s_side_waypoint():
    agent, combat, flank = flank_scene(NO_MARCH)
    combat.command(flank)

    sides = raid_routes.side_waypoints((41, 48), (72, 52), MAP, 12)
    assert flank.route is None and flank.via in sides
    assert moves(agent, 12505, tanks(40, 47)) == [("move", list(flank.via))]


def test_flank_with_no_safe_chain_the_flank_falls_back_to_the_side_waypoint():
    _, _, flank = flank_scene(dataclasses.replace(FLANK_ON, routing=RoutingTuning(True, 10, 2)))

    assert flank.route is None and flank.via in raid_routes.side_waypoints((41, 48), (72, 52), MAP, 12)


def test_flank_the_commander_taking_a_flank_by_the_waypoint_route_writes_one_route_event(match_record):
    agent, combat, flank = flank_scene(FLANK_ON)
    commander = Commander(StubChooser(), CommanderTuning())
    commander.role, commander.standing = MAIN_ROLE, flank
    agent.ctx.commander = commander

    first = moves(agent, 12505, tanks(40, 47))
    moves(agent, 12510, tanks(41, 50))

    assert first == [("move", [44, 64])]
    (written,) = match_record("route")
    assert (written["user"], written["start"], written["goal"], written["node_count"]) == (
        "flank",
        [41, 48],
        [72, 52],
        2,
    )


# -- the MCV -----------------------------------------------------------------------------------------------------------

# An Allied base with a war factory, a service depot and its own pillbox; the main yard's middle is (23, 50).
MCV_BASE = (
    ("fact", 22, 48),
    ("powr", 19, 48),
    ("tent", 22, 44),
    ("proc", 24, 39),
    ("proc", 18, 38),
    ("powr", 26, 48),
    ("weap", 15, 48),
    ("powr", 26, 52),
    ("pbox", 28, 44),
    ("fix", 15, 53),
)
MCV_ID, MCV_START = 400, (23, 44)
# The field 18 cells south of the main yard, the MCV's site's, and a pillbox whose cover takes in the MCV's straight
# line to that site but not the way by the west side waypoint. It stands 16 cells from the site (20, 68), beyond the 14
# of mcv_enemy_building_cells, within which the site would not qualify.
SOUTH_YARD_FIELD = field((23, 68))
MCV_PILLBOX = enemy(900, "pbox", 27, 54)
HOME_ORE = field((22, 40))
HARVESTERS = [
    {"id": 300 + n, "type": "harv", "x": 30, "y": 30, "hp": 100, "act": "move", "idle": False} for n in range(4)
]
ESCORT_TANKS = [
    {"id": 500 + n, "type": "2tnk", "x": 24 + n, "y": 58, "hp": 100, "act": None, "idle": True} for n in range(3)
]
SAFE_SITE = tuning_off(expansion=expansion_off(second_yard=True, mcv_safe_site=True))
SAFE_SITE_ON = dataclasses.replace(SAFE_SITE, routing=RoutingTuning(waypoints=True))


def england(seconds: int, mcv_cell: tuple[int, int] | None = None, *, seen=(MCV_PILLBOX,), enemies=()) -> dict:
    """A state of the base at a time with these enemy buildings and units in sight, and the MCV, on its way, where
    given."""
    mcv = [] if mcv_cell is None else [{"id": MCV_ID, "type": "mcv", "x": mcv_cell[0], "y": mcv_cell[1], "hp": 100}]
    units = [*HARVESTERS, *({**unit, "act": "move", "idle": False} for unit in mcv), *ESCORT_TANKS]
    return base_state(
        "england",
        *MCV_BASE,
        tick=seconds * 25,
        cash=5000,
        units=units,
        visible_enemy_buildings=list(seen),
        visible_enemies=list(enemies),
    )


def mcv_agent(tuning: Tuning, **shown):
    """An agent with upkeep alone under HOLD on the terrain of the home field and the south field, that has seen the
    base at 500 s with the pillbox, or what is `shown`."""
    agent = new_agent(UpkeepManager(), plan_selector=OpeningThen(HOLD), tuning=tuning)
    ask(agent, base_state("england", *MCV_BASE, tick=100, cash=0))
    agent.ctx.terrain = Terrain((HOME_ORE, SOUTH_YARD_FIELD), HOME_ORE.cells | SOUTH_YARD_FIELD.cells)
    ask(agent, england(500, **shown))
    return agent


def trip(agent, seconds: int, cell: tuple[int, int], **shown) -> tuple[list[list[int]], list[list[int]]]:
    """Where the MCV and its escort are sent in a cycle with the MCV at a cell."""
    orders = ask(agent, england(seconds, cell, **shown))
    mcv_to = [order["to"] for order in orders if order.get("units") == [MCV_ID]]
    escort_to = [order["to"] for order in orders if order["cmd"] == "attack_move"]
    return mcv_to, escort_to


def held(agent, site: list[int], approach: tuple[int, int]) -> list[int]:
    """The escort's hold point by the site, the MCV coming from `approach`: the escort keeps off the yard's ground."""
    return list(expansion.escort_hold(tuple(site), approach, agent.world, agent.ctx.terrain))


def written(match_record) -> tuple[list[int], list[dict]]:
    """The site the MCV's `site` event gives, and the `route` events, of the match record."""
    lines = match_record()
    (site,) = [
        line["data"]["cell"] for line in lines if line["kind"] == "expansion" and line["data"]["phase"] == "site"
    ]
    return site, [line["data"] for line in lines if line["kind"] == "route"]


def test_mcv_with_the_switch_on_an_mcv_whose_straight_line_is_unsafe_goes_by_the_waypoint_route(match_record):
    agent = mcv_agent(SAFE_SITE_ON)

    first = trip(agent, 505, MCV_START)
    at_leg = trip(agent, 510, tuple(first[0][0]))

    site, routes = written(match_record)
    found = agent.ctx.waypoints.route(MCV_START, tuple(site))
    leg = list(found.legs[0])
    assert list(found.legs[-1]) == site and leg != site
    assert first == ([leg], [leg]) and at_leg == ([site], [held(agent, site, tuple(leg))])
    assert [(route["user"], route["start"], route["goal"]) for route in routes] == [("mcv", list(MCV_START), site)]


def test_mcv_with_the_switch_off_the_mcv_goes_straight_to_its_site(match_record):
    agent = mcv_agent(SAFE_SITE)

    first = trip(agent, 505, MCV_START)

    site, routes = written(match_record)
    assert first == ([site], [held(agent, site, MCV_START)]) and routes == []


def test_mcv_with_no_safe_chain_the_mcv_goes_straight_to_its_site(match_record):
    agent = mcv_agent(dataclasses.replace(SAFE_SITE_ON, routing=RoutingTuning(True, 5, 2)))

    first = trip(agent, 505, MCV_START)

    site, routes = written(match_record)
    assert first == ([site], [held(agent, site, MCV_START)]) and routes == []


def test_mcv_without_the_safe_site_the_switch_leaves_the_trip_straight(match_record):
    tuning = dataclasses.replace(
        SAFE_SITE_ON, expansion=expansion_off(second_yard=True), routing=RoutingTuning(waypoints=True)
    )
    agent = mcv_agent(tuning)

    mcv_to, _ = trip(agent, 505, MCV_START)

    assert mcv_to and agent.ctx.waypoints.nodes and match_record("route") == []


@pytest.mark.parametrize(
    "shown",
    [{"seen": ()}, {"enemies": [enemy(700, "dog", 4, 56)]}],
    ids=["straight line clear", "an enemy unit by the route"],
)
def test_mcv_with_a_clear_straight_line_or_an_unsafe_waypoint_route_the_mcv_goes_straight(match_record, shown):
    agent = mcv_agent(SAFE_SITE_ON, **shown)

    first = trip(agent, 505, MCV_START, **shown)

    site, routes = written(match_record)
    assert first == ([site], [held(agent, site, MCV_START)]) and routes == []


# -- a whole match -----------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def routed_match() -> WholeMatch:
    """One whole 20-minute fake match for the Allied start with the shipped tuning and waypoint routing on."""
    return play_whole_match(dataclasses.replace(Tuning(), routing=RoutingTuning(waypoints=True)))


def test_whole_match_with_the_switch_on_runs_without_an_unhandled_error_or_a_refused_batch(routed_match):
    errors = routed_match.data_of("error")

    assert routed_match.agent.ctx.tuning.routing.waypoints and routed_match.agent.ctx.waypoints.nodes
    assert len(routed_match.compute_ms) == WHOLE_MATCH_STEPS
    assert [error for error in errors if error["type"] == "unhandled"] == []
    assert routed_match.run.invalid_actions == 0 and routed_match.game.invalid_actions == 0


def test_whole_match_with_the_switch_on_compute_p95_is_at_most_50_ms(routed_match):
    assert routed_match.compute_p95_ms <= COMPUTE_P95_MS
