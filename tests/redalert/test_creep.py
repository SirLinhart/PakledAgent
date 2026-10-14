"""Walked routes: an infantry unit walks to each far field first, the base grows along the route it walked, and a field
it cannot reach, or reaches only by a long detour, is skipped."""

from __future__ import annotations

import math

import pytest

import situations
from fake_game import FakeRedAlert
from situations import ask, base_state, with_command
from switches_off import new_agent, opening_off, placement_off, scouting_off, tuning_off
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.creep import NearestFarField
from ucla_agent.games.redalert.groups import GUARD, MAIN, SCOUT
from ucla_agent.games.redalert.items import ItemTable
from ucla_agent.games.redalert.ledger import OrderLedger
from ucla_agent.games.redalert.managers import MatchContext, OpeningBuild
from ucla_agent.games.redalert.opening import OpeningManager
from ucla_agent.games.redalert.route_walk import RouteWalks
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import PlacementTuning, Tuning
from ucla_agent.games.redalert.upkeep import UpkeepManager
from ucla_agent.games.redalert.world import World
from whole_match import COMPUTE_P95_MS, WHOLE_MATCH_STEPS, WholeMatch, play_whole_match, with_ore

YARD = ("fact", 22, 48)
POWER = ("powr", 19, 52)
BARRACKS = ("tent", 18, 44)
GUARD_IDS = [200, 201, 202]
WALKER = 201


def base(*buildings, tick: int = 1000, **observation) -> dict:
    """An Allied base of the construction yard, a power plant and a barracks, with what the test adds."""
    return base_state("england", YARD, POWER, BARRACKS, *buildings, tick=tick, **observation)


def guard_rifles(x: int = 28) -> list[dict]:
    """Three rifle infantry of the home guard standing idle by the guard post."""
    return [situations.unit(unit_id, "e1", x, 46 + number) for number, unit_id in enumerate(GUARD_IDS)]


def naming(orders: list[dict], unit_id: int) -> list[dict]:
    """The orders that name the unit."""
    return [order for order in orders if unit_id in order.get("units", ())]


def walking(agent, unit_id: int, units: list[dict], tick: int = 995) -> None:
    """Show the agent its units once, then mark one of them as walking a route."""
    ask(agent, base(tick=tick, units=units))
    agent.ctx.groups.set_walking(unit_id, True)


# -- the walking mark ---------------------------------------------------------------------------------------------------


def test_walked_route_a_walking_guard_rifle_answers_neither_a_minor_nor_a_major_threat():
    minor, major = new_agent(BaseDefenseManager()), new_agent(BaseDefenseManager())
    for agent in (minor, major):
        walking(agent, WALKER, guard_rifles())
    jeep = [{"id": 500, "type": "jeep", "x": 32, "y": 48, "hp": 100}]
    rifles = [{"id": 600 + number, "type": "e1", "x": 32, "y": 44 + number, "hp": 100} for number in range(8)]

    minor_orders = ask(minor, base(units=guard_rifles(), visible_enemies=jeep))
    major_orders = ask(major, base(units=guard_rifles(), visible_enemies=rifles))

    assert with_command(minor_orders, "attack") == [{"cmd": "attack", "units": [200, 202], "target": 500}]
    (answer,) = with_command(major_orders, "attack_move")
    assert answer["units"] == [200, 202]


def test_walked_route_a_walking_unit_of_main_takes_no_combat_order_and_the_others_take_theirs():
    agent = new_agent(CombatManager())
    tanks = [situations.unit(300, "1tnk", 24, 52), situations.unit(301, "1tnk", 24, 54)]
    ask(agent, base(tick=990, units=tanks))
    agent.ctx.groups.set_walking(301, True)

    orders = ask(agent, base(units=tanks))

    assert naming(orders, 300) != [] and naming(orders, 301) == []
    assert agent.ctx.groups.group_of(301) == MAIN


def test_walked_route_a_walking_guard_rifle_is_not_sent_to_the_guard_post_and_still_counts_for_the_guard():
    lean = tuning_off(opening=opening_off(lean_infantry=True))
    astray = [situations.unit(unit_id, "e1", 22, 70) for unit_id in GUARD_IDS]
    marked, short = new_agent(OpeningManager(), tuning=lean), new_agent(OpeningManager(), tuning=lean)
    # The guard was sent to its post 100 ticks ago, and stands idle far from it since.
    walking(marked, WALKER, astray, tick=900)
    ask(short, base(tick=900, units=astray[:2]))

    marked_orders = ask(marked, base(units=astray, cash=5000))
    short_orders = ask(short, base(units=astray[:2], cash=5000))

    (to_post,) = with_command(marked_orders, "move")
    assert to_post["units"] == [200, 202] and marked.ctx.groups.count(GUARD, "e1") == 3
    assert with_command(marked_orders, "train") == []
    assert with_command(short_orders, "train") == [{"cmd": "train", "item": "e1", "count": 1}]


# -- the walker --------------------------------------------------------------------------------------------------------


def square_patch(center: tuple[int, int], reach: int = 1) -> OrePatch:
    """An ore patch of the square of cells within `reach` of its center, counted as a king moves."""
    x, y = center
    field = frozenset((x + dx, y + dy) for dy in range(-reach, reach + 1) for dx in range(-reach, reach + 1))
    return OrePatch(center=center, cells=field)


def terrain_of(*patches: OrePatch) -> Terrain:
    """A terrain of these ore patches, their ore the only ground nothing can be built on."""
    return Terrain(patches, frozenset().union(*(patch.cells for patch in patches)))


WALKED = placement_off(creep_from_opening=True, walked_routes=True)
# The fake game's ore north of its first spawn, which a refinery by the base stands against, and a field 15 cells east
# of the yard's middle (22, 49) out of a refinery's reach: the far field, whose ore cell nearest the yard is (37, 49).
HOME_PATCH = square_patch((22, 40))
EAST_FIELD = square_patch((38, 50))
EAST_ORE = [37, 49]
TERRAIN = terrain_of(HOME_PATCH, EAST_FIELD)
# The guard's first rifle stands 9.85 cells from that ore cell.
FIRST_GUARD_CELL = (28, 46)
# A remembered pillbox (cover 6 + 2 cells) 7 cells south of the yard's line to the far field, 13.4 cells from its ore.
PILLBOX = {"id": 900, "type": "pbox", "x": 25, "y": 57, "hp": 100}


def walk_agent(*managers, placement: PlacementTuning = WALKED, units=(), **groups) -> object:
    """An agent with upkeep and the managers given, walking routes, that has seen a first state of its base with these
    units at tick 990, on the map of the home field and the far field east."""
    agent = new_agent(UpkeepManager(), *managers, tuning=tuning_off(placement=placement, **groups))
    ask(agent, base(tick=990, units=list(units)))
    agent.ctx.terrain = TERRAIN
    return agent


def rifle(unit_id: int, cell: tuple[int, int], *, idle: bool = True) -> dict:
    return situations.unit(unit_id, "e1", *cell, idle=idle)


def walker_moves(orders: list[dict]) -> list[dict]:
    """The moves of the orders that go to the far field's ore cell."""
    return [order for order in with_command(orders, "move") if order["to"] == EAST_ORE]


def test_walked_route_a_far_field_without_a_route_sends_one_idle_guard_rifle_with_a_move_to_its_ore_cell(match_record):
    rifles = [rifle(200, FIRST_GUARD_CELL), rifle(201, (28, 47)), rifle(202, (28, 48))]
    agent = walk_agent(units=rifles)

    sent = ask(agent, base(tick=1000, units=rifles))
    later = ask(agent, base(tick=1005, units=[rifle(200, (29, 46), idle=False), *rifles[1:]]))

    assert walker_moves(sent) == [{"cmd": "move", "units": [200], "to": EAST_ORE}] and walker_moves(later) == []
    assert agent.ctx.groups.is_walking(200) and agent.ctx.groups.group_of(200) == GUARD
    assert match_record("route_walk") == [
        {
            "tick": 1000,
            "phase": "sent",
            "field_center": [38, 50],
            "yard": "main",
            "walker_type": "e1",
            "path_length_cells": 0.0,
            "straight_distance_cells": round(math.dist(FIRST_GUARD_CELL, EAST_ORE), 2),
            "point_count": 1,
        }
    ]


def test_walked_route_a_rifle_in_a_scout_slot_never_walks_and_one_first_seen_in_this_cycle_waits_for_the_next():
    slots = scouting_off(scout_slots=True)
    agent = walk_agent(ScoutingManager(), scouting=slots)
    rifles = [rifle(300, (18, 46)), rifle(301, (18, 47)), rifle(302, (18, 48))]

    first_seen = ask(agent, base(tick=1000, units=rifles))
    next_cycle = ask(agent, base(tick=1005, units=rifles))

    assert agent.ctx.groups.members(SCOUT) == [300, 301] and agent.ctx.groups.group_of(302) == GUARD
    assert walker_moves(first_seen) == []
    assert walker_moves(next_cycle) == [{"cmd": "move", "units": [302], "to": EAST_ORE}]


def test_walked_route_the_walker_s_cells_form_the_route_done_within_3_cells_and_it_is_sent_back(match_record):
    # The last cell is the first within 3 cells of the ore cell (37, 49), and lies 1.4 cells from the last point kept.
    walker = [(30, 46), (32, 47), (34, 47), (35, 48)]
    agent = walk_agent(units=[rifle(200, FIRST_GUARD_CELL)])
    ask(agent, base(tick=1000, units=[rifle(200, FIRST_GUARD_CELL)]))

    asked = [
        ask(agent, base(tick=1005 + 5 * step, units=[rifle(200, cell, idle=False)])) for step, cell in enumerate(walker)
    ]

    route = ((28, 46), (30, 46), (32, 47), (34, 47), (35, 48))
    length = sum(math.dist(first, second) for first, second in zip(route, route[1:]))
    assert [walker_moves(orders) for orders in asked] == [[]] * 4
    assert with_command(asked[-1], "move") == [{"cmd": "move", "units": [200], "to": list(FIRST_GUARD_CELL)}]
    assert not agent.ctx.groups.is_walking(200)
    done = match_record("route_walk")[-1]
    assert done == {
        "tick": 1020,
        "phase": "done",
        "field_center": [38, 50],
        "yard": "main",
        "walker_type": "e1",
        "path_length_cells": round(length, 2),
        "straight_distance_cells": round(math.dist(FIRST_GUARD_CELL, EAST_ORE), 2),
        "point_count": 5,
    }


def test_walked_route_a_far_field_whose_straight_way_a_remembered_pillbox_covers_gets_no_walker_and_is_skipped(
    match_record,
):
    rifles = [rifle(200, FIRST_GUARD_CELL)]
    agent = walk_agent(units=rifles)

    orders = ask(agent, base(tick=1000, units=rifles, visible_enemy_buildings=[PILLBOX]))

    assert walker_moves(orders) == [] and not agent.ctx.groups.is_walking(200)
    assert agent.ctx.skipped_fields == [EAST_FIELD] and match_record("route_walk") == []
    assert match_record("creep_placement") == [
        {
            "tick": 1000,
            "building": None,
            "cell": None,
            "field_center": [38, 50],
            "target_patch_center": [38, 50],
            "reason": "walk_defended",
            "yard": "main",
        }
    ]


def test_walked_route_with_lean_infantry_on_a_guard_rifle_sent_as_the_walker_has_no_extra_rifle_ordered():
    lean = opening_off(lean_infantry=True)
    rifles = [rifle(200, FIRST_GUARD_CELL), rifle(201, (28, 47)), rifle(202, (28, 48))]
    agent = walk_agent(OpeningManager(), units=rifles, opening=lean)

    sent = ask(agent, base(tick=1000, units=rifles, cash=5000))
    walking_on = ask(agent, base(tick=1005, units=[rifle(200, (29, 46), idle=False), *rifles[1:]], cash=5000))

    assert walker_moves(sent) == [{"cmd": "move", "units": [200], "to": EAST_ORE}]
    assert agent.ctx.groups.count(GUARD, "e1") == 3 and agent.ctx.groups.members(GUARD) == [201, 202]
    assert [with_command(orders, "train") for orders in (sent, walking_on)] == [[], []]


class Walks:
    """The route walker looked at cycle after cycle as upkeep looks at it, the creep keeper naming the far fields first,
    in a base of these buildings on a map of this terrain."""

    def __init__(
        self, placement: PlacementTuning = WALKED, terrain: Terrain = TERRAIN, buildings=(YARD, POWER, BARRACKS)
    ):
        self.world = World(situations.tracked_items())
        self.ctx = MatchContext(Tuning(placement=placement), OrderLedger(), situations.StubChooser())
        self.ctx.terrain, self.buildings = terrain, buildings
        self.keeper = NearestFarField()
        self.walks = RouteWalks(self.keeper)

    def cycle(self, tick: int, *units: dict, **observation) -> list[dict]:
        """The orders of a cycle at a tick in which the base has these units."""
        self.world.update(base_state("england", *self.buildings, tick=tick, units=list(units), **observation))
        self.ctx.groups.update(self.world)
        self.keeper.target(self.world, self.ctx.terrain, self.ctx.tuning.placement, self.ctx.skipped_fields)
        return [dict(intent.order) for intent in self.walks.intents(self.world, self.ctx)]


def test_walked_route_with_the_switch_off_or_without_the_creep_from_the_opening_nobody_walks():
    rifles = [rifle(200, FIRST_GUARD_CELL)]
    for placement in (placement_off(creep_from_opening=True), placement_off(walked_routes=True)):
        walks = Walks(placement)

        orders = [walks.cycle(tick, *rifles) for tick in (1000, 1005)]

        assert orders == [[], []] and walks.walks.routes == {}


def test_walked_route_a_walker_that_is_lost_ends_its_walk_and_the_next_idle_rifle_walks_the_field_again(match_record):
    walks = Walks()
    rifles = [rifle(200, FIRST_GUARD_CELL), rifle(201, (28, 47))]
    walks.cycle(1000, *rifles)
    sent = walks.cycle(1005, *rifles)

    lost = walks.cycle(1010, rifles[1])
    again = walks.cycle(1015, rifles[1])

    assert sent == [{"cmd": "move", "units": [200], "to": EAST_ORE}] and lost == []
    assert again == [{"cmd": "move", "units": [201], "to": EAST_ORE}]
    assert walks.ctx.skipped_fields == [] and walks.walks.routes == {}
    phases = [(event["phase"], event.get("reason")) for event in match_record("route_walk")]
    assert phases == [("sent", None), ("failed", "walker_lost"), ("sent", None)]


def test_walked_route_a_walker_taken_into_another_group_ends_its_walk_with_no_order():
    walks = Walks()
    rifles = [rifle(200, FIRST_GUARD_CELL)]
    walks.cycle(1000, *rifles)
    walks.cycle(1005, *rifles)
    walks.ctx.groups.assign(200, SCOUT)

    taken = walks.cycle(1010, rifle(200, (29, 46), idle=False))

    assert taken == [] and not walks.ctx.groups.is_walking(200)


def test_walked_route_in_the_opening_the_next_rifle_trained_walks_and_one_on_its_way_from_before_does_not():
    on_its_way, trained = rifle(200, FIRST_GUARD_CELL, idle=False), rifle(201, (18, 46), idle=False)
    sent = {}
    for opening in ((), ("proc", "proc", "powr", "weap")):
        walks = Walks()
        walks.ctx.opening_build = OpeningBuild(opening)
        wanted = [walks.cycle(tick, on_its_way) for tick in (995, 1000)]
        first_seen = walks.cycle(1005, on_its_way, trained)
        sent[bool(opening)] = (wanted, first_seen, walks.cycle(1010, on_its_way, trained))

    assert sent[True] == ([[], []], [], [{"cmd": "move", "units": [201], "to": EAST_ORE}])
    assert sent[False] == ([[], []], [], [])


# The main yard and a second yard 30 cells south of it, the far field between them, and the second yard's own far field
# east of it, whose ore cell nearest that yard's middle (22, 79) is (39, 79).
SECOND_YARD = ("fact", 22, 78)
BETWEEN_THE_YARDS = square_patch((23, 68))
BY_THE_SECOND_YARD = square_patch((40, 80))
REMOTE = placement_off(creep_from_opening=True, remote_yard_growth=True, walked_routes=True)
REMOTE_TERRAIN = terrain_of(HOME_PATCH, BETWEEN_THE_YARDS, BY_THE_SECOND_YARD)


def test_walked_route_the_second_yard_s_far_field_takes_a_rifle_within_15_cells_of_that_yard_first():
    by_the_main_yard, by_the_second_yard = rifle(200, FIRST_GUARD_CELL), rifle(201, (25, 74))
    sent = []
    for rifles in ((by_the_main_yard, by_the_second_yard), (by_the_main_yard,)):
        walks = Walks(REMOTE, REMOTE_TERRAIN, (YARD, SECOND_YARD))
        walks.walks.routes[BETWEEN_THE_YARDS] = ((23, 50), (23, 66))
        walks.cycle(1000, *rifles)
        sent.append(walks.cycle(1005, *rifles))

    assert [line for line, _ in walks.keeper.lines()] == ["main", "second"]
    assert sent == [
        [{"cmd": "move", "units": [201], "to": [39, 79]}],
        [{"cmd": "move", "units": [200], "to": [39, 79]}],
    ]


def test_walked_route_the_route_kept_for_the_field_runs_from_the_walker_s_start_through_its_points_to_its_arrival():
    walks = Walks()
    walks.cycle(1000, rifle(200, FIRST_GUARD_CELL))
    walks.cycle(1005, rifle(200, FIRST_GUARD_CELL))
    steps = [(29, 46), (31, 47), (35, 49)]

    for number, cell in enumerate(steps):
        walks.cycle(1010 + 5 * number, rifle(200, cell, idle=False))

    # (29, 46) lies 1 cell from the start and is not kept; the arrival cell is kept once.
    assert walks.walks.routes == {EAST_FIELD: ((28, 46), (31, 47), (35, 49))}


def test_walked_route_the_route_of_a_field_skipped_or_taken_is_forgotten():
    # A refinery stands 1 cell south of the home field: the home field is taken.
    walks = Walks(buildings=(YARD, POWER, BARRACKS, ("proc", 21, 42)))
    walks.walks.routes[EAST_FIELD] = ((28, 46), (37, 50))
    walks.walks.routes[HOME_PATCH] = ((23, 50), (22, 42))
    walks.cycle(1000)
    kept = dict(walks.walks.routes)
    walks.ctx.skipped_fields.append(EAST_FIELD)

    walks.cycle(1005)

    assert kept == {EAST_FIELD: ((28, 46), (37, 50))} and walks.walks.routes == {}


# -- the walks that skip their field ------------------------------------------------------------------------------------

SENT_AT = 1005
# The walker's time: 60 s for a field 9.85 cells away, 2 x 35.23 / 1.32 + 10 = 63.4 s for one 35.23 cells away.
TIMEOUT_TICKS = 60 * 25
FAR_START = (2, 46)
FAR_TIMEOUT_TICKS = math.ceil((2 * math.dist(FAR_START, EAST_ORE) / 1.32 + 10) * 25)


def sent_walker(start=FIRST_GUARD_CELL) -> Walks:
    """The route walker with rifle 200 sent from `start` toward the far field's ore cell at SENT_AT."""
    walks = Walks()
    walks.cycle(1000, rifle(200, start))
    assert walks.cycle(SENT_AT, rifle(200, start)) == [{"cmd": "move", "units": [200], "to": EAST_ORE}]
    return walks


def shuffling(walks: Walks, start, until: int) -> list[list[dict]]:
    """Show the walker stepping one cell to and fro from `start` every 5 s up to tick `until`: never still, never
    farther along; the orders of each cycle."""
    ticks = range(SENT_AT + 125, until + 1, 125)
    return [
        walks.cycle(tick, rifle(200, (start[0], start[1] + number % 2), idle=False))
        for number, tick in enumerate(ticks, start=1)
    ]


def skip_events(match_record) -> tuple[list, list]:
    """The reasons of the route_walk failed events and of the creep_placement events."""
    walks = [event["reason"] for event in match_record("route_walk") if event["phase"] == "failed"]
    return walks, [event["reason"] for event in match_record("creep_placement")]


def test_walked_route_a_walker_that_has_not_arrived_in_60_s_skips_its_field_as_unreachable(match_record):
    walks = sent_walker()

    orders = shuffling(walks, FIRST_GUARD_CELL, SENT_AT + TIMEOUT_TICKS)

    assert orders[:-1] == [[]] * (len(orders) - 1)
    assert orders[-1] == [{"cmd": "move", "units": [200], "to": list(FIRST_GUARD_CELL)}]
    assert walks.ctx.skipped_fields == [EAST_FIELD] and not walks.ctx.groups.is_walking(200)
    assert skip_events(match_record) == (["walk_unreachable"], ["walk_unreachable"])


def test_walked_route_the_time_to_arrive_grows_with_the_distance_over_the_walker_s_speed():
    walks = sent_walker(FAR_START)

    orders = shuffling(walks, FAR_START, SENT_AT + FAR_TIMEOUT_TICKS - 1)
    late = walks.cycle(SENT_AT + FAR_TIMEOUT_TICKS, rifle(200, FAR_START, idle=False))

    assert FAR_TIMEOUT_TICKS > TIMEOUT_TICKS and walks.ctx.skipped_fields == [EAST_FIELD]
    assert orders == [[]] * len(orders) and late == [{"cmd": "move", "units": [200], "to": list(FAR_START)}]


def test_walked_route_a_walker_that_stands_still_10_s_short_of_the_field_skips_it_as_unreachable(match_record):
    walks = sent_walker()
    stuck = (31, 47)
    walks.cycle(SENT_AT + 25, rifle(200, stuck, idle=False))

    waiting = walks.cycle(SENT_AT + 25 + 249, rifle(200, stuck))
    still = walks.cycle(SENT_AT + 25 + 250, rifle(200, stuck))

    assert waiting == [] and still == [{"cmd": "move", "units": [200], "to": list(FIRST_GUARD_CELL)}]
    assert walks.ctx.skipped_fields == [EAST_FIELD]
    assert skip_events(match_record) == (["walk_unreachable"], ["walk_unreachable"])


def test_walked_route_a_walker_whose_way_grows_past_twice_the_straight_distance_skips_the_field_at_once(match_record):
    walks = sent_walker()
    # West, away from the ore cell: 4, 8, 12, 16 and then 20 cells walked, the last past 2 x 9.85 = 19.70.
    westward = [(24, 46), (20, 46), (16, 46), (12, 46), (8, 46)]

    orders = [walks.cycle(SENT_AT + 5 * step, rifle(200, cell, idle=False)) for step, cell in enumerate(westward, 1)]

    assert orders[:-1] == [[]] * 4 and orders[-1] == [{"cmd": "move", "units": [200], "to": list(FIRST_GUARD_CELL)}]
    assert walks.ctx.skipped_fields == [EAST_FIELD]
    assert skip_events(match_record) == (["walk_detour"], ["walk_detour"])
    failed = [event for event in match_record("route_walk") if event["phase"] == "failed"]
    assert (failed[0]["path_length_cells"], failed[0]["point_count"]) == (20.0, 6)


def test_walked_route_a_field_skipped_meanwhile_for_another_reason_is_not_skipped_twice(match_record):
    walks = sent_walker()
    walks.ctx.skipped_fields.append(EAST_FIELD)
    walks.cycle(SENT_AT + 25, rifle(200, (31, 47), idle=False))

    walks.cycle(SENT_AT + 275, rifle(200, (31, 47)))

    assert walks.ctx.skipped_fields == [EAST_FIELD] and match_record("creep_placement") == []
    assert [event["phase"] for event in match_record("route_walk")] == ["sent", "failed"]


def test_walked_route_a_skipped_field_is_never_walked_again():
    walks = sent_walker()
    walks.cycle(SENT_AT + 25, rifle(200, (31, 47), idle=False))
    walks.cycle(SENT_AT + 275, rifle(200, (31, 47)))

    # With creep_continuous off the skipped field stays the far field; no rifle is sent to it again.
    again = [walks.cycle(tick, rifle(200, FIRST_GUARD_CELL), rifle(201, (28, 47))) for tick in (1400, 1405)]

    assert walks.keeper.lines()[0][1].patch == EAST_FIELD and again == [[], []]


# -- a whole match ------------------------------------------------------------------------------------------------------

# Ore out of a refinery's reach from the own spawn (22, 48): a field east of it and one west of it.
EAST_OF_THE_SPAWN = [(x, y) for y in (45, 46, 47) for x in (35, 36, 37)]
WEST_OF_THE_SPAWN = [(x, y) for y in (49, 50, 51) for x in (8, 9, 10)]


@pytest.fixture(scope="module")
def walked_match() -> WholeMatch:
    """A whole fake match on the map with a field east and a field west of the own spawn, played with the creep from
    the opening, the continuous creep and the walked routes on."""
    placement = placement_off(creep_from_opening=True, creep_continuous=True, walked_routes=True)
    ore = [*EAST_OF_THE_SPAWN, *WEST_OF_THE_SPAWN]
    return play_whole_match(tuning_off(placement=placement), game_config=with_ore(FakeRedAlert().game_config, ore))


def test_walked_route_a_whole_fake_match_runs_without_an_error_within_the_compute_budget_and_walks_a_route(
    walked_match,
):
    phases = [event["phase"] for event in walked_match.data_of("route_walk")]

    assert len(walked_match.compute_ms) == WHOLE_MATCH_STEPS and walked_match.data_of("error") == []
    assert walked_match.compute_p95_ms <= COMPUTE_P95_MS
    assert "done" in phases
    # One walks at a time: every walk sent ends, done or failed, before the next is sent.
    assert all(phase == "sent" for phase in phases[::2]) and all(phase != "sent" for phase in phases[1::2])


# -- the walkers kept from the enemy ------------------------------------------------------------------------------------


def killed(unit_id: int, seq: int, tick: int) -> dict:
    """The observation of a state whose events report an own rifle killed."""
    return {"events": [situations.event(seq, "unit_lost", tick, id=unit_id, type="e1", x=31, y=47)]}


def failed_reasons(match_record) -> list[str]:
    return [event["reason"] for event in match_record("route_walk") if event["phase"] == "failed"]


def test_walker_safety_two_walkers_killed_on_their_way_skip_the_field_and_a_lost_one_does_not_count(match_record):
    walks = Walks()
    rifles = [rifle(200 + number, (28, 46 + number)) for number in range(4)]
    walks.cycle(1000, *rifles)
    sent = [walks.cycle(1005, *rifles)]
    # Rifle 200 is gone without word of its death, then 201 and 202 are killed on their way, each sent in turn; 203
    # would walk next.
    steps = ((1010, {}), (1020, killed(201, 1, 1018)), (1030, killed(202, 2, 1028)))
    for number, (tick, seen) in enumerate(steps, start=1):
        walks.cycle(tick, *rifles[number:], **seen)
        sent.append(walks.cycle(tick + 5, *rifles[number:]))

    assert [orders[0]["units"] for orders in sent[:3]] == [[200], [201], [202]] and sent[3] == []
    assert walks.ctx.skipped_fields == [EAST_FIELD]
    assert failed_reasons(match_record) == ["walker_lost", "walker_killed", "walker_killed"]
    assert [event["reason"] for event in match_record("creep_placement")] == ["walk_killed"]


def test_walker_safety_one_walker_killed_leaves_the_field_to_the_next_walker(match_record):
    walks = Walks()
    rifles = [rifle(200, FIRST_GUARD_CELL), rifle(201, (28, 47))]
    walks.cycle(1000, *rifles)
    walks.cycle(1005, *rifles)

    walks.cycle(1010, rifles[1], **killed(200, 1, 1008))
    again = walks.cycle(1015, rifles[1])

    assert again == [{"cmd": "move", "units": [201], "to": EAST_ORE}] and walks.ctx.skipped_fields == []
    assert failed_reasons(match_record) == ["walker_killed"]


def test_walker_safety_no_walker_is_sent_while_the_base_is_under_threat():
    walks = Walks()
    rifles = [rifle(200, FIRST_GUARD_CELL)]
    walks.cycle(1000, *rifles)
    walks.ctx.threat_active = True
    threatened = walks.cycle(1005, *rifles)
    walks.ctx.threat_active = False

    quiet = walks.cycle(1010, *rifles)

    assert threatened == [] and quiet == [{"cmd": "move", "units": [200], "to": EAST_ORE}]


# The straight way to the far field runs along row 50 from the yard's middle (23, 50) to the ore cell (37, 50).
@pytest.mark.parametrize(
    "tank, seconds_ago, sent",
    [((30, 55), 30, False), ((30, 59), 30, True), ((30, 55), 61, True)],
    ids=["5 cells off 30 s ago", "9 cells off", "61 s ago"],
)
def test_walker_safety_no_walker_is_sent_with_an_enemy_tank_seen_near_the_way_in_the_last_60_s(tank, seconds_ago, sent):
    walks = Walks()
    rifles = [rifle(200, FIRST_GUARD_CELL)]
    seen = [{"id": 910, "type": "2tnk", "x": tank[0], "y": tank[1], "hp": 100}]
    walks.cycle(2000 - seconds_ago * 25, *rifles, visible_enemies=seen)

    orders = walks.cycle(2000, *rifles)

    assert orders == ([{"cmd": "move", "units": [200], "to": EAST_ORE}] if sent else [])
    assert walks.ctx.skipped_fields == []


# An enemy refinery whose middle, (48, 50) or (51, 50), lies 10 or 13 cells from the far field's center (38, 50).
@pytest.mark.parametrize("left, skipped", [(47, True), (50, False)], ids=["10 cells", "13 cells"])
def test_walker_safety_a_field_10_cells_from_a_remembered_enemy_refinery_is_skipped(left, skipped, match_record):
    walks = Walks()
    rifles = [rifle(200, FIRST_GUARD_CELL)]
    walks.cycle(1000, *rifles, visible_enemy_buildings=[{"id": 920, "type": "proc", "x": left, "y": 48, "hp": 100}])

    orders = walks.cycle(1005, *rifles)

    assert orders == ([] if skipped else [{"cmd": "move", "units": [200], "to": EAST_ORE}])
    assert walks.ctx.skipped_fields == ([EAST_FIELD] if skipped else [])
    assert [event["reason"] for event in match_record("creep_placement")] == (["walk_enemy_field"] if skipped else [])


# -- the stall rule counts build time only -----------------------------------------------------------------------------

STALL = placement_off(creep_from_opening=True, creep_continuous=True, creep_stall_skip=True)
# The far field 20 cells west of the yard's middle, which the base makes no headway toward, and the next one, 25 cells
# south of it.
STALLED_FIELD = square_patch((3, 50))
NEXT_FIELD = square_patch((22, 75))
STALL_TERRAIN = terrain_of(HOME_PATCH, STALLED_FIELD, NEXT_FIELD)
# The tick from which the far field is followed, one cycle after the first.
STALL_START = 1000
TICKS_A_SECOND = 25


def stall_world(tick: int, *, building: bool) -> World:
    """A world of the yard alone at a tick, its Building queue holding a power plant being built or nothing."""
    state = {
        "phase": "playing",
        "map": {"bounds": [0, 0, 96, 96]},
        "observation": {
            "tick": tick,
            "buildings": [situations.building(100, *YARD)],
            "base_center": list(YARD[1:]),
            "visible_enemy_buildings": [],
            "production": [situations.job("Building", "powr")] if building else [],
        },
        "legal_actions": {"enemy_base": [74, 48]},
    }
    world = World(ItemTable([]))
    world.update(state)
    return world


def stall_run(*stretches: tuple[int, bool]) -> tuple[dict[int, list[OrePatch]], OrePatch | None]:
    """The fields skipped after each second of stretches of so many seconds with the Building queue active or empty,
    the creep keeper looked at once a second as upkeep looks at it, and the far field after the last."""
    keeper, skipped = NearestFarField(), []
    after: dict[int, list[OrePatch]] = {}
    target = None
    ticks = [STALL_START - TICKS_A_SECOND, STALL_START]
    actives = [False, False]
    for seconds, active in stretches:
        for _ in range(seconds):
            ticks.append(ticks[-1] + TICKS_A_SECOND)
            actives.append(active)
    for number, (tick, active) in enumerate(zip(ticks, actives)):
        world = stall_world(tick, building=active)
        keeper.skip_stalled(world, STALL, skipped)
        target = keeper.target(world, STALL_TERRAIN, STALL, skipped)
        after[number - 1] = list(skipped)
    return after, None if target is None else target.patch


def test_stall_build_time_with_the_building_queue_empty_for_200_s_and_no_progress_the_field_is_kept():
    after, target = stall_run((200, False))

    assert after[200] == [] and target == STALLED_FIELD


def test_stall_build_time_with_the_queue_active_120_s_and_no_2_cell_progress_the_field_is_skipped(match_record):
    after, target = stall_run((120, True))

    assert after[119] == [] and after[120] == [STALLED_FIELD] and target == NEXT_FIELD
    (event,) = match_record("creep_placement")
    assert event["reason"] == "stalled_skip" and event["field_center"] == [3, 50]


def test_stall_build_time_60_s_active_100_s_empty_and_60_s_active_skip_at_the_end_of_the_second_active_stretch():
    after, target = stall_run((60, True), (100, False), (60, True))

    assert after[160] == [] and after[219] == [] and after[220] == [STALLED_FIELD] and target == NEXT_FIELD
