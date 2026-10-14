"""Scouting: the scout report, and the scout that keeps the picture of the enemy fresh."""

from __future__ import annotations

import dataclasses
import re

import pytest

import situations
from situations import ask, base_state
from switches_off import new_agent, opening_off, own_agent, raids_off, scouting_off, tuning_off
from ucla_agent.core import records
from ucla_agent.core.decide import STALE_DISCARDED, USED, ChoiceResult
from ucla_agent.games.redalert import (
    cells,
    coverage,
    exposed_trucks,
    plans,
    raid_targets,
    scout_berth,
    scout_missions,
    scout_steering,
    scout_targets,
)
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.economy_hold import EconomyHold
from ucla_agent.games.redalert.expansion import ESCORT
from ucla_agent.games.redalert.groups import GUARD, MAIN, RAID, SCOUT
from ucla_agent.games.redalert.managers import MAJOR_THREAT, NO_THREAT, OpeningBuild
from ucla_agent.games.redalert.raid_routes import side_waypoints
from ucla_agent.games.redalert.rescout import StaleIntel, far_fields
from ucla_agent.games.redalert.scout_choice import ScoutQuestion
from ucla_agent.games.redalert.scout_mission_planner import MissionPlanner
from ucla_agent.games.redalert.scout_report import ScoutReport
from ucla_agent.games.redalert.scout_route import indirect_route
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.sight import SightGrid
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import FACTORY_FIRST, Tuning
from ucla_agent.games.redalert.upkeep import UpkeepManager
from ucla_agent.games.redalert.world import Unit, World

YARD = ("fact", 22, 48)
POWER = ("powr", 19, 52)
BARRACKS = ("tent", 18, 44)


def enemy(enemy_id: int, enemy_type: str, x: int, y: int, hp: int = 100) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": hp}


def base(tick: int = 1000, **observation) -> dict:
    """An Allied base of the construction yard, a power plant and a barracks, with what the test adds."""
    return base_state("england", YARD, POWER, BARRACKS, tick=tick, **observation)


def scouting_agent(*more):
    return new_agent(ScoutingManager(), *more)


# -- the scout report ------------------------------------------------------------------------------

PILLBOXES = [enemy(600, "pbox", 66, 44), enemy(601, "pbox", 66, 52)]
ENEMY_HARVESTER = enemy(602, "harv", 60, 30)
ENEMY_RIFLES = [enemy(610 + number, "e1", 64, 40 + number) for number in range(6)]


def report_after(agent, *states: dict) -> ScoutReport:
    for state in states:
        ask(agent, state)
    return agent.ctx.scout_report


def test_the_report_counts_two_remembered_pillboxes_one_harvester_and_six_rifles_worth_600():
    agent = scouting_agent()
    seen = base(tick=1000, visible_enemies=[ENEMY_HARVESTER, *ENEMY_RIFLES], visible_enemy_buildings=PILLBOXES)

    report = report_after(agent, seen, base(tick=1005))

    assert report.enemy_defense_count == 2
    assert report.enemy_harvester_cells == [(60, 30)]
    assert report.enemy_combat_value == 600
    assert report.enemy_buildings == {"pbox": 2}
    assert report.last_building_seen_tick == 1000
    assert not report.enemy_has_vehicles


def test_a_unit_gone_stale_no_longer_counts_toward_the_enemy_s_combat_value():
    agent = scouting_agent()
    ask(agent, base(tick=1000, visible_enemies=ENEMY_RIFLES))

    fresh = report_after(agent, base(tick=1749)).enemy_combat_value
    stale = report_after(agent, base(tick=1750)).enemy_combat_value

    assert (fresh, stale) == (600, 0)


def test_a_harvester_is_reported_where_it_was_last_seen_and_counts_toward_nothing_else():
    agent = scouting_agent()

    report = report_after(
        agent,
        base(tick=1000, visible_enemies=[enemy(602, "harv", 50, 20)]),
        base(tick=1005, visible_enemies=[ENEMY_HARVESTER]),
    )

    assert report.enemy_harvester_cells == [(60, 30)] and report.enemy_combat_value == 0


@pytest.mark.parametrize(
    "seen, has_vehicles",
    [
        ({"visible_enemies": [enemy(700, "1tnk", 60, 40)]}, True),
        ({"visible_enemy_buildings": [enemy(700, "weap", 70, 48)]}, True),
        ({"visible_enemy_buildings": [enemy(700, "fix", 70, 48)]}, True),
        ({"visible_enemies": [enemy(700, "harv", 60, 40), enemy(701, "e1", 61, 40)]}, False),
    ],
    ids=["a tank", "a war factory", "a service depot", "a harvester and a rifle"],
)
def test_the_enemy_has_vehicles_once_a_vehicle_that_fights_or_a_building_that_makes_them_was_seen(seen, has_vehicles):
    agent = scouting_agent()

    report = report_after(agent, base(tick=1000, **seen))

    assert report.enemy_has_vehicles is has_vehicles


def test_vehicles_once_seen_stay_reported_after_the_vehicle_is_destroyed():
    agent = scouting_agent()
    ask(agent, base(tick=1000, visible_enemies=[enemy(700, "1tnk", 60, 40)]))

    report = report_after(agent, base(tick=1005, visible_enemies=[enemy(700, "1tnk.husk", 60, 40)]))

    assert report.enemy_has_vehicles and report.enemy_combat_value == 0


@pytest.mark.parametrize(
    "building, seen",
    [
        (enemy(700, "powr", 74, 63), True),
        (enemy(700, "powr", 74, 64), False),
        (enemy(700, "fact", 40, 20), True),
        (enemy(700, "pbox", 50, 48), False),
    ],
    ids=["15 cells from its start", "16 cells from its start", "a construction yard anywhere", "a pillbox afield"],
)
def test_the_enemy_base_is_seen_once_a_building_near_its_start_or_its_construction_yard_was_in_sight(building, seen):
    agent = scouting_agent()

    report = report_after(agent, base(tick=1000, visible_enemy_buildings=[building]), base(tick=1005))

    assert report.enemy_base_seen is seen


def test_before_any_enemy_is_seen_the_report_knows_nothing():
    agent = scouting_agent()

    report = report_after(agent, base(tick=1000))

    assert report == ScoutReport() and report.last_building_seen_tick is None and report.enemy_buildings == {}


def test_the_last_building_seen_tick_moves_on_only_while_a_building_is_in_sight():
    agent = scouting_agent()
    ask(agent, base(tick=1000, visible_enemy_buildings=PILLBOXES))

    while_hidden = report_after(agent, base(tick=1500)).last_building_seen_tick
    again = report_after(agent, base(tick=1600, visible_enemy_buildings=PILLBOXES[:1])).last_building_seen_tick

    assert (while_hidden, again) == (1000, 1600)


def test_a_new_match_starts_with_an_empty_report():
    first, second = scouting_agent(), scouting_agent()
    ask(first, base(tick=1000, visible_enemy_buildings=PILLBOXES))

    ask(second, base(tick=1000))

    assert first.ctx.scout_report.enemy_defense_count == 2 and second.ctx.scout_report == ScoutReport()


def test_the_agent_s_own_managers_publish_the_report_in_every_cycle():
    agent = own_agent()

    ask(agent, base(tick=1000, visible_enemy_buildings=PILLBOXES))

    assert agent.ctx.scout_report.enemy_defense_count == 2


# -- the scout's mission ---------------------------------------------------------------------------

GUARD_IDS = [200, 201, 202, 203, 204]
SCOUT_ID = 205
ENEMY_BASE = [74, 48]
# 10 cells from the base toward the enemy's start.
STAGING = [32, 48]


def guard() -> list[dict]:
    """The home guard: five rifle infantry at the guard post."""
    return [situations.unit(unit_id, "e1", 28, 46 + number) for number, unit_id in enumerate(GUARD_IDS)]


def rifle(x: int = 20, y: int = 48, *, hp: int = 100, idle: bool = True) -> dict:
    """The sixth rifle infantry: the first one beyond the home guard."""
    return situations.unit(SCOUT_ID, "e1", x, y, hp=hp, idle=idle)


def moves(orders: list[dict]) -> list[dict]:
    return situations.with_command(orders, "move")


def sent_out(agent, *more: dict, family_base=base) -> list[dict]:
    """Show the agent its guard at tick 995 and the sixth rifle at 1000: the orders that second state gets."""
    ask(agent, family_base(tick=995, units=[*guard(), *more]))
    return ask(agent, family_base(tick=1000, units=[*guard(), rifle(), *more]))


def scout_at(agent, tick: int, x: int, y: int, **observation) -> list[dict]:
    """The orders for a state in which the scout stands on a cell."""
    return ask(agent, base(tick=tick, units=[*guard(), rifle(x, y, hp=observation.pop("hp", 100))], **observation))


def test_with_five_guard_rifles_and_a_sixth_created_the_sixth_is_sent_to_the_enemy_base_and_the_five_are_not():
    agent = scouting_agent()

    orders = sent_out(agent)

    assert orders == [{"cmd": "move", "units": [SCOUT_ID], "to": ENEMY_BASE}]
    assert agent.ctx.groups.members(SCOUT) == [SCOUT_ID] and agent.ctx.groups.members(GUARD) == GUARD_IDS
    assert [entry.priority for entry in agent.ledger.entries] == [Priority.SCOUT]


def test_only_one_scout_is_sent_the_first_rifle_that_joined_main():
    agent = scouting_agent()
    more = [situations.unit(206, "e1", 21, 48), situations.unit(207, "e1", 21, 49)]

    orders = sent_out(agent, *more)

    assert orders == [{"cmd": "move", "units": [206], "to": ENEMY_BASE}]
    assert agent.ctx.groups.members(SCOUT) == [206] and agent.ctx.groups.members(MAIN) == [207, SCOUT_ID]


def test_a_scout_that_is_lost_leaves_no_scout_and_the_next_rifle_beyond_the_guard_is_not_sent_at_once():
    agent = scouting_agent()
    sent_out(agent)

    orders = ask(agent, base(tick=1005, units=[*guard(), situations.unit(206, "e1", 20, 48)]))

    assert orders == [] and agent.ctx.groups.members(SCOUT) == []


def test_a_scouting_vehicle_is_not_replaced_by_another():
    agent = scouting_agent()
    sent_out(agent)
    first_jeep = situations.unit(300, "jeep", 30, 52)
    ask(agent, base(tick=1005, units=[*guard(), rifle(30, 48), first_jeep]))

    ask(agent, base(tick=1010, units=[*guard(), rifle(30, 48), first_jeep, situations.unit(301, "jeep", 20, 52)]))

    assert agent.ctx.groups.members(SCOUT) == [300] and agent.ctx.groups.group_of(301) == MAIN


def test_no_scout_is_sent_while_the_enemy_s_start_is_not_known():
    agent = scouting_agent()

    def unknown_enemy(**observation):
        state = base(**observation)
        del state["legal_actions"]["enemy_base"]
        return state

    assert sent_out(agent, family_base=unknown_enemy) == [] and agent.ctx.groups.members(SCOUT) == []


def test_a_jeep_that_is_created_becomes_the_scout_and_the_infantry_scout_goes_back_to_the_home_guard():
    agent = scouting_agent()
    sent_out(agent)

    orders = ask(agent, base(tick=1005, units=[*guard(), rifle(30, 48), situations.unit(300, "jeep", 20, 52)]))

    groups = agent.ctx.groups
    assert orders == [{"cmd": "move", "units": [300], "to": ENEMY_BASE}]
    assert groups.members(SCOUT) == [300] and groups.group_of(SCOUT_ID) == GUARD
    assert groups.members(GUARD) == [*GUARD_IDS, SCOUT_ID]


def test_a_jeep_of_the_raid_group_does_not_take_over_the_scouting():
    agent = scouting_agent()
    jeep = situations.unit(300, "jeep", 20, 52)
    sent_out(agent, jeep)
    agent.ctx.groups.assign(300, RAID)

    orders = ask(agent, base(tick=1005, units=[*guard(), rifle(30, 48), jeep]))

    assert orders == [{"cmd": "move", "units": [SCOUT_ID], "to": ENEMY_BASE}]
    assert agent.ctx.groups.members(SCOUT) == [SCOUT_ID]


@pytest.mark.parametrize("unit_type, takes_over", [("dog", True), ("ftrk", False)], ids=["dog", "flak truck"])
def test_a_soviet_dog_and_never_a_flak_truck_takes_over_from_an_infantry_scout(unit_type, takes_over):
    agent = scouting_agent()

    def soviet_base(**observation):
        return base_state("russia", YARD, POWER, ("barr", 18, 44), **observation)

    sent_out(agent, family_base=soviet_base)
    ask(agent, soviet_base(tick=1005, units=[*guard(), rifle(30, 48), situations.unit(300, unit_type, 20, 52)]))

    assert (agent.ctx.groups.members(SCOUT) == [300]) is takes_over


def remembered_pillbox(agent, x: int) -> None:
    """Let the agent see an enemy pillbox at (x, 48) at tick 1003; it is remembered and out of sight from then on."""
    ask(agent, base(tick=1003, units=[*guard(), rifle(30, 48)], visible_enemy_buildings=[enemy(600, "pbox", x, 48)]))


@pytest.mark.parametrize("pillbox_x, withdraws", [(64, True), (65, False)], ids=["8 cells", "9 cells"])
def test_a_scout_8_cells_from_a_remembered_pillbox_withdraws_toward_the_base_and_at_9_cells_goes_on(
    pillbox_x, withdraws
):
    agent = scouting_agent()
    sent_out(agent)
    remembered_pillbox(agent, pillbox_x)

    orders = scout_at(agent, 1005, 56, 48)

    assert agent.ctx.scout_report.enemy_defense_count == 1
    assert moves(orders) == [{"cmd": "move", "units": [SCOUT_ID], "to": [48, 48] if withdraws else ENEMY_BASE}]


def test_a_scout_at_40_percent_health_withdraws_8_cells_toward_the_base():
    agent = scouting_agent()
    sent_out(agent)

    orders = scout_at(agent, 1005, 50, 48, hp=40)

    assert orders == [{"cmd": "move", "units": [SCOUT_ID], "to": [42, 48]}]


def test_a_scout_that_withdrew_goes_back_to_the_staging_point_and_is_released_to_main():
    agent = scouting_agent()
    sent_out(agent)
    scout_at(agent, 1005, 50, 48, hp=40)

    to_staging = scout_at(agent, 1010, 42, 48, hp=40)
    released = scout_at(agent, 1015, 32, 48, hp=40)

    assert to_staging == [{"cmd": "move", "units": [SCOUT_ID], "to": STAGING}]
    assert released == [] and agent.ctx.groups.group_of(SCOUT_ID) == MAIN


def test_after_arriving_within_10_cells_of_the_enemy_base_the_scout_circles_it_through_four_waypoints_in_order():
    agent = scouting_agent()
    sent_out(agent)

    visited = [scout_at(agent, tick, *cell) for tick, cell in zip(range(1005, 1035, 5), CIRCLE_STOPS)]

    assert [order["to"] for orders in visited for order in orders] == [[74, 38], [84, 48], [74, 58], [64, 48], STAGING]
    assert visited[-1] == [] and agent.ctx.groups.group_of(SCOUT_ID) == MAIN


# Where the scout stands in the states of a circle: within 10 cells of the base, then at each waypoint in turn.
CIRCLE_STOPS = [(64, 48), (74, 38), (84, 48), (74, 58), (64, 48), (32, 48)]


def test_the_waypoints_around_a_base_near_the_edge_of_the_map_are_clamped_inside_it():
    agent = scouting_agent()

    def cornered(**observation):
        state = base(**observation)
        state["legal_actions"]["enemy_base"] = [90, 5]
        return state

    sent_out(agent, family_base=cornered)
    stops = [(84, 8), (90, 0), (95, 5), (90, 15), (80, 5)]
    orders = [ask(agent, cornered(tick=1005 + 5 * n, units=[*guard(), rifle(*cell)])) for n, cell in enumerate(stops)]

    waypoints = [order["to"] for batch in orders for order in batch][:4]
    assert waypoints == [[90, 0], [95, 5], [90, 15], [80, 5]]
    assert all(0 <= x < 96 and 0 <= y < 96 for x, y in waypoints)


def test_a_scout_that_stands_idle_short_of_a_waypoint_for_125_ticks_gives_it_up_for_the_next():
    agent = scouting_agent()
    sent_out(agent)
    scout_at(agent, 1005, 64, 48)

    waiting = scout_at(agent, 1129, 70, 40)
    given_up = scout_at(agent, 1130, 70, 40)

    assert waiting == [{"cmd": "move", "units": [SCOUT_ID], "to": [74, 38]}]
    assert given_up == [{"cmd": "move", "units": [SCOUT_ID], "to": [84, 48]}]


def test_a_scout_on_its_way_is_not_given_the_same_order_again_in_every_state():
    agent = scouting_agent()
    sent_out(agent)

    on_its_way = ask(agent, base(tick=1005, units=[*guard(), rifle(30, 48, idle=False)]))

    assert on_its_way == []


# -- stale knowledge and lost scouts ---------------------------------------------------------------

# These tests pin the trigger rescout replaces, so they play with it off: a scout is due once no enemy building has
# been seen for stale_after_ticks.
RESCOUT_OFF = dataclasses.replace(tuning_off(), scouting=scouting_off(rescout=False))


def stale_trigger_agent():
    """A scouting agent with rescout off, whose scout is due by the staleness of the enemy buildings seen."""
    return new_agent(ScoutingManager(), tuning=RESCOUT_OFF)


TANK = situations.unit(300, "1tnk", 20, 52)
ROCKETS = situations.unit(301, "e3", 21, 52)
ENEMY_POWER_PLANT = enemy(700, "powr", 70, 48)


def scouts_at(agent, tick: int, *units: dict, **observation) -> list[int]:
    """Hand the agent the base with the guard and the units at a tick; the scout group after it."""
    ask(agent, base(tick=tick, units=[*guard(), *units], **observation))
    return agent.ctx.groups.members(SCOUT)


def test_a_new_scout_is_sent_once_no_enemy_building_has_been_seen_for_2250_ticks_and_not_at_2249():
    agent = stale_trigger_agent()
    scouts_at(agent, 1000, TANK, visible_enemy_buildings=[ENEMY_POWER_PLANT])

    before = scouts_at(agent, 3249, TANK)
    orders = ask(agent, base(tick=3250, units=[*guard(), TANK]))

    assert before == [] and agent.ctx.groups.members(SCOUT) == [300]
    assert orders == [{"cmd": "move", "units": [300], "to": ENEMY_BASE}]


def test_a_second_new_scout_is_not_sent_within_60_seconds_of_the_first():
    agent = stale_trigger_agent()
    scouts_at(agent, 1000, TANK, visible_enemy_buildings=[ENEMY_POWER_PLANT])
    first = scouts_at(agent, 3250, TANK, ROCKETS)

    # The first scout is lost at once, before it has seen anything: its replacement too waits out the minute.
    within_a_minute = [scouts_at(agent, tick, TANK) for tick in (3255, 4005, 4745)]
    second = scouts_at(agent, 4750, TANK)

    assert first == [301] and within_a_minute == [[], [], []] and second == [300]


def test_the_new_scout_is_the_cheapest_idle_unit_outside_the_guard_and_the_raid():
    agent = stale_trigger_agent()
    units = [
        TANK,
        situations.unit(301, "e3", 21, 52, idle=False),
        situations.unit(302, "e3", 22, 52),
        situations.unit(303, "dog", 23, 52),
    ]
    scouts_at(agent, 1000, *units[:3])
    scouts_at(agent, 1005, *units)
    agent.ctx.groups.assign(303, RAID)

    assert scouts_at(agent, 2250, *units) == [302]


def test_a_scout_lost_before_seeing_a_building_is_replaced_30_seconds_later_and_not_sooner():
    agent = stale_trigger_agent()
    sent_out(agent)

    lost = scouts_at(agent, 2600, ROCKETS)
    sooner = scouts_at(agent, 3349, ROCKETS)
    replaced = scouts_at(agent, 3350, ROCKETS)

    assert (lost, sooner, replaced) == ([], [], [301])


def test_a_scout_lost_after_it_saw_a_building_is_not_replaced_until_knowledge_is_stale():
    agent = stale_trigger_agent()
    sent_out(agent)
    ask(agent, base(tick=1003, units=[*guard(), rifle(60, 48)], visible_enemy_buildings=[ENEMY_POWER_PLANT]))

    after_the_loss = [scouts_at(agent, tick, ROCKETS) for tick in (1005, 2500, 3252)]
    stale = scouts_at(agent, 3253, ROCKETS)

    assert after_the_loss == [[], [], []] and stale == [301]


def test_after_two_scouts_are_lost_before_seeing_a_building_none_is_sent_for_60_seconds_and_one_event_is_logged(
    match_record,
):
    agent = stale_trigger_agent()
    sent_out(agent)
    first_lost = scouts_at(agent, 1005, situations.unit(206, "e1", 21, 52))
    replacement = scouts_at(agent, 2500, situations.unit(206, "e1", 21, 52))
    reserve = situations.unit(207, "e1", 22, 52)

    paused = [scouts_at(agent, tick, reserve) for tick in (2505, 3000, 4004)]
    after_the_pause = scouts_at(agent, 4005, reserve)

    events = match_record("error")
    assert first_lost == [] and replacement == [206]
    assert paused == [[], [], []] and after_the_pause == [207]
    assert [(event["where"], event["type"], event["exception"]) for event in events] == [
        ("redalert.scouting", "handled", "ScoutsLost")
    ]


def test_when_knowledge_is_stale_and_only_the_guard_stands_idle_no_scout_is_taken_from_it():
    agent = stale_trigger_agent()

    assert scouts_at(agent, 2250, situations.unit(300, "1tnk", 20, 52, idle=False)) == []
    assert agent.ctx.groups.members(GUARD) == GUARD_IDS


def test_only_the_first_rifle_hands_its_mission_over_to_a_jeep_and_a_later_infantry_scout_keeps_it():
    agent = stale_trigger_agent()
    scouts_at(agent, 2250, ROCKETS)

    after_a_jeep = scouts_at(agent, 2255, ROCKETS, situations.unit(302, "jeep", 20, 52))

    assert after_a_jeep == [301] and agent.ctx.groups.group_of(302) == MAIN


# -- keeping a lead: no scouting under PRESERVE ----------------------------------------------------


class SwitchablePlan:
    """A plan selector that keeps the agent in the plan a test sets."""

    def __init__(self, plan: str) -> None:
        self.plan = plan

    def select(self, world, ctx, choice) -> str:
        return self.plan


def test_under_preserve_a_scout_in_the_field_comes_back_to_the_staging_point_and_is_released_to_main():
    plan = SwitchablePlan("OPENING")
    agent = new_agent(ScoutingManager(), plan_selector=plan)
    sent_out(agent)
    plan.plan = "PRESERVE"

    coming_back = scout_at(agent, 1100, 50, 48)
    home = scout_at(agent, 1200, 32, 48)

    assert moves(coming_back) == [{"cmd": "move", "units": [SCOUT_ID], "to": [32, 48]}]
    assert home == [] and SCOUT_ID in agent.ctx.groups.members(MAIN)


def test_under_preserve_no_scout_is_sent():
    agent = new_agent(ScoutingManager(), plan_selector=SwitchablePlan("PRESERVE"))

    assert sent_out(agent) == [] and agent.ctx.groups.members(SCOUT) == []


# -- the scout after a pause -----------------------------------------------------------------------


def paused_agent():
    """A scouting agent whose first two scouts were lost blind at 1005 and 2505: no scout before 4005."""
    agent = stale_trigger_agent()
    sent_out(agent)
    scouts_at(agent, 1005, situations.unit(206, "e1", 21, 52))
    scouts_at(agent, 2500, situations.unit(206, "e1", 21, 52))
    scouts_at(agent, 2505)
    return agent


def test_after_two_blind_losses_the_next_scout_is_the_fastest_idle_unit_sent_by_a_side_waypoint():
    agent = paused_agent()
    rifle, jeep = situations.unit(207, "e1", 22, 52), situations.unit(208, "jeep", 23, 52)

    before = scouts_at(agent, 4004, rifle, jeep)
    orders = ask(agent, base(tick=4005, units=[*guard(), rifle, jeep]))

    side = side_waypoints((23, 52), tuple(ENEMY_BASE), (0, 0, 96, 96), 12)[0]
    assert before == [] and agent.ctx.groups.members(SCOUT) == [208]
    assert moves(orders) == [{"cmd": "move", "units": [208], "to": list(side)}]
    assert cells.distance(side, (48, 50)) == pytest.approx(12, abs=1)


def test_the_scout_by_the_side_waypoint_goes_on_to_the_enemy_start_once_it_is_there():
    agent = paused_agent()
    jeep = situations.unit(208, "jeep", 23, 52)
    scouts_at(agent, 4005, jeep)
    side = side_waypoints((23, 52), tuple(ENEMY_BASE), (0, 0, 96, 96), 12)[0]

    orders = ask(agent, base(tick=4100, units=[*guard(), situations.unit(208, "jeep", *side)]))

    assert moves(orders) == [{"cmd": "move", "units": [208], "to": ENEMY_BASE}]


def test_the_side_waypoint_is_the_one_no_remembered_defense_covers():
    agent = paused_agent()
    near, far = side_waypoints((23, 52), tuple(ENEMY_BASE), (0, 0, 96, 96), 12)
    pillbox = enemy(710, "pbox", near[0], near[1] + 2)
    # Seen at 2600, so knowledge is stale again at 4850.
    scouts_at(agent, 2600, visible_enemy_buildings=[pillbox])

    orders = ask(agent, base(tick=4850, units=[*guard(), situations.unit(208, "jeep", 23, 52)]))

    assert moves(orders) == [{"cmd": "move", "units": [208], "to": list(far)}]


def test_the_scout_after_the_one_sent_by_the_side_is_again_the_cheapest_and_goes_straight():
    agent = paused_agent()
    scouts_at(agent, 4005, situations.unit(208, "jeep", 23, 52))
    rifle = situations.unit(207, "e1", 22, 52)
    ask(agent, base(tick=4010, units=[*guard(), rifle]))

    orders = ask(agent, base(tick=5505, units=[*guard(), rifle, situations.unit(209, "jeep", 24, 52)]))

    assert moves(orders) == [{"cmd": "move", "units": [207], "to": ENEMY_BASE}]


def test_with_no_idle_unit_after_the_pause_the_fast_scout_waits_for_one():
    agent = paused_agent()

    nobody = scouts_at(agent, 4005)
    later = scouts_at(agent, 4010, situations.unit(207, "e1", 22, 52), situations.unit(208, "jeep", 23, 52))

    assert (nobody, later) == ([], [208])


# -- rescout: a scout again whenever the base area, the army or a far field has gone unseen -------------------------

RESCOUT = dataclasses.replace(tuning_off(), scouting=scouting_off(rescout=True))
JEEP = situations.unit(300, "jeep", 20, 52)
# An enemy rifle and an enemy power plant out on the map, far from the enemy's start: in sight, they keep the army and
# the last building seen fresh.
ENEMY_RIFLE_AFIELD = enemy(800, "e1", 50, 20)
ENEMY_PLANT_AFIELD = enemy(801, "powr", 52, 20)
# A harvester of ours 8 cells from the enemy's start: it keeps the enemy base area in sight.
HARVESTER_AT_THE_ENEMY = situations.unit(400, "harv", 66, 48)
FAR_FIELD = OrePatch(center=(60, 80), cells=frozenset({(60, 80), (61, 80)}))
NEAR_FIELD = OrePatch(center=(22, 30), cells=frozenset({(22, 30), (23, 30)}))


def square_field(center: tuple[int, int]) -> OrePatch:
    """An ore field of the nine cells around a middle."""
    return OrePatch(center, frozenset((center[0] + dx, center[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)))


def rescout_agent(tuning: Tuning = RESCOUT):
    return new_agent(ScoutingManager(), tuning=tuning)


def intel_at(intel: StaleIntel, tick: int, *units: dict, terrain: Terrain = Terrain(), **observation) -> str | None:
    """Hand the stale-intel clocks one state of the base with the guard and these units; the reason it then gives."""
    world = World(situations.tracked_items())
    world.update(base(tick=tick, units=[*guard(), *units], **observation))
    intel.observe(world)
    return intel.reason(world, terrain, RESCOUT.scouting)


def test_rescout_the_base_area_unseen_for_90_s_is_stale_base_though_a_building_afield_is_in_sight():
    intel = StaleIntel()
    in_sight = {"visible_enemies": [ENEMY_RIFLE_AFIELD], "visible_enemy_buildings": [ENEMY_PLANT_AFIELD]}

    reasons = [intel_at(intel, tick, JEEP, **in_sight) for tick in (1000, 3249, 3250)]

    assert reasons == [None, None, "stale_base"]


def test_rescout_no_enemy_unit_that_fights_in_sight_for_60_s_is_stale_army():
    intel = StaleIntel()
    intel_at(intel, 1000, HARVESTER_AT_THE_ENEMY, visible_enemies=[ENEMY_RIFLE_AFIELD])

    reasons = [intel_at(intel, tick, HARVESTER_AT_THE_ENEMY) for tick in (2499, 2500)]

    assert reasons == [None, "stale_army"]


def test_rescout_a_field_more_than_25_cells_from_the_base_unseen_for_180_s_is_stale_field_and_a_near_one_is_not():
    far, near = StaleIntel(), StaleIntel()
    fresh = {"visible_enemies": [ENEMY_RIFLE_AFIELD]}

    far_reasons = [
        intel_at(far, tick, HARVESTER_AT_THE_ENEMY, terrain=Terrain([FAR_FIELD]), **fresh)
        for tick in (1000, 5499, 5500)
    ]
    near_reason = [
        intel_at(near, tick, HARVESTER_AT_THE_ENEMY, terrain=Terrain([NEAR_FIELD]), **fresh) for tick in (1000, 9000)
    ]

    assert far_reasons == [None, None, "stale_field"] and near_reason == [None, None]
    assert far_fields(World(situations.tracked_items()), Terrain([FAR_FIELD])) == []


def test_rescout_the_first_clock_run_out_is_the_reason_base_before_army_before_field():
    intel = StaleIntel()
    intel_at(intel, 1000, terrain=Terrain([FAR_FIELD]))

    assert intel_at(intel, 6000, terrain=Terrain([FAR_FIELD])) == "stale_base"
    assert intel_at(intel, 6005, HARVESTER_AT_THE_ENEMY, terrain=Terrain([FAR_FIELD])) == "stale_army"


def test_rescout_a_scout_is_sent_once_the_base_area_is_unseen_for_90_s_while_a_building_afield_is_in_sight():
    agent = rescout_agent()
    in_sight = {"visible_enemies": [ENEMY_RIFLE_AFIELD], "visible_enemy_buildings": [ENEMY_PLANT_AFIELD]}
    scouts_at(agent, 1000, JEEP, **in_sight)

    before = scouts_at(agent, 3249, JEEP, **in_sight)
    orders = ask(agent, base(tick=3250, units=[*guard(), JEEP], **in_sight))

    assert before == [] and agent.ctx.groups.members(SCOUT) == [300]
    assert orders == [{"cmd": "move", "units": [300], "to": ENEMY_BASE}]


def test_rescout_off_a_building_afield_in_sight_keeps_any_scout_from_being_sent():
    agent = rescout_agent(tuning=RESCOUT_OFF)
    in_sight = {"visible_enemies": [ENEMY_RIFLE_AFIELD], "visible_enemy_buildings": [ENEMY_PLANT_AFIELD]}

    assert [scouts_at(agent, tick, JEEP, **in_sight) for tick in (1000, 3250, 6000)] == [[], [], []]


def test_rescout_a_scout_is_sent_once_no_enemy_unit_has_been_seen_for_60_s():
    agent = rescout_agent()
    scouts_at(agent, 1000, JEEP, HARVESTER_AT_THE_ENEMY, visible_enemies=[ENEMY_RIFLE_AFIELD])

    assert [scouts_at(agent, tick, JEEP, HARVESTER_AT_THE_ENEMY) for tick in (2499, 2500)] == [[], [300]]


def test_rescout_a_scout_is_sent_once_a_far_field_is_unseen_for_180_s():
    agent = rescout_agent()
    fresh = {"visible_enemies": [ENEMY_RIFLE_AFIELD]}
    scouts_at(agent, 1000, JEEP, HARVESTER_AT_THE_ENEMY, **fresh)
    # The terrain the match's first state brought is replaced by one with a far field.
    agent.ctx.terrain = Terrain([FAR_FIELD])

    assert [scouts_at(agent, tick, JEEP, HARVESTER_AT_THE_ENEMY, **fresh) for tick in (5499, 5500)] == [[], [300]]


def test_rescout_no_scout_is_sent_under_a_major_threat_and_one_is_once_it_is_over():
    agent = rescout_agent()
    scouts_at(agent, 1000, JEEP, HARVESTER_AT_THE_ENEMY, visible_enemies=[ENEMY_RIFLE_AFIELD])
    agent.ctx.threat_level = MAJOR_THREAT

    under_threat = scouts_at(agent, 2500, JEEP, HARVESTER_AT_THE_ENEMY)
    agent.ctx.threat_level = NO_THREAT
    after = scouts_at(agent, 2505, JEEP, HARVESTER_AT_THE_ENEMY)

    assert (under_threat, after) == ([], [300])


def test_rescout_no_new_scout_is_sent_within_60_s_of_the_last():
    agent = rescout_agent()
    second_jeep = situations.unit(301, "jeep", 21, 52)
    scouts_at(agent, 1000, JEEP, second_jeep, HARVESTER_AT_THE_ENEMY, visible_enemies=[ENEMY_RIFLE_AFIELD])
    first = scouts_at(agent, 2500, JEEP, second_jeep, HARVESTER_AT_THE_ENEMY)

    # The first is lost at once; the picture stays stale, but the next waits out the minute.
    within = [scouts_at(agent, tick, second_jeep, HARVESTER_AT_THE_ENEMY) for tick in (2505, 3300, 3999)]
    second = scouts_at(agent, 4000, second_jeep, HARVESTER_AT_THE_ENEMY)

    assert first == [300] and within == [[], [], []] and second == [301]


def stale_army_agent(*units: dict):
    """A rescout agent whose first scout, the rifle sent at 1000, saw an enemy building and rifle at 1003 and was lost
    at 1005, when the units given come out; its picture of the enemy army runs out at 2503, the enemy base area kept
    in sight."""
    agent = rescout_agent()
    sent_out(agent, HARVESTER_AT_THE_ENEMY)
    seen = {"visible_enemies": [ENEMY_RIFLE_AFIELD], "visible_enemy_buildings": [ENEMY_PLANT_AFIELD]}
    ask(agent, base(tick=1003, units=[*guard(), rifle(60, 48), HARVESTER_AT_THE_ENEMY], **seen))
    scouts_at(agent, 1005, *units, HARVESTER_AT_THE_ENEMY)
    return agent


@pytest.mark.parametrize(
    "units, chosen",
    [
        ([situations.unit(310, "e1", 21, 52), situations.unit(311, "jeep", 22, 52)], [311]),
        ([situations.unit(310, "e1", 21, 52), situations.unit(311, "dog", 22, 52)], [311]),
        ([situations.unit(310, "e1", 21, 52), situations.unit(311, "ftrk", 22, 52)], [310]),
        ([situations.unit(311, "e1", 22, 52), situations.unit(310, "e1", 21, 52)], [310]),
        ([situations.unit(310, "1tnk", 21, 52), situations.unit(311, "e3", 22, 52)], []),
    ],
    ids=[
        "a jeep before a rifle",
        "a dog before a rifle",
        "a rifle, never a flak truck",
        "the first of two rifles",
        "neither of the types",
    ],
)
def test_rescout_the_scout_is_a_jeep_then_a_dog_then_a_rifle(units, chosen):
    agent = stale_army_agent(*units)

    assert scouts_at(agent, 2502, *units, HARVESTER_AT_THE_ENEMY) == []
    assert scouts_at(agent, 2505, *units, HARVESTER_AT_THE_ENEMY) == chosen


def test_rescout_a_rifle_of_main_on_its_way_to_the_staging_point_is_chosen_and_one_sent_forward_is_not():
    waiting, forward = situations.unit(310, "e1", 26, 50, idle=False), situations.unit(311, "jeep", 40, 48, idle=False)
    agent = stale_army_agent(waiting, forward)
    agent.ctx.army = frozenset({311})

    assert scouts_at(agent, 2505, waiting, forward, HARVESTER_AT_THE_ENEMY) == [310]


def test_rescout_a_jeep_of_the_raid_group_and_the_guard_s_rifles_are_never_chosen():
    jeep = situations.unit(311, "jeep", 22, 52)
    agent = stale_army_agent(jeep)
    agent.ctx.groups.assign(311, RAID)

    assert scouts_at(agent, 2505, jeep, HARVESTER_AT_THE_ENEMY) == []
    assert agent.ctx.groups.members(GUARD) == GUARD_IDS


@pytest.mark.parametrize("tuning", [RESCOUT_OFF, RESCOUT], ids=["rescout off", "rescout on"])
def test_a_unit_escorting_the_mcv_to_a_second_yard_is_never_taken_as_a_scout(tuning):
    agent = rescout_agent(tuning)
    escort = situations.unit(311, "jeep", 22, 52)
    scouts_at(agent, 1000, escort, visible_enemy_buildings=[ENEMY_POWER_PLANT])
    agent.ctx.groups.assign(311, ESCORT)

    # Knowledge is stale either way at 3250: no building for 2250 ticks, and the enemy base area and army unseen.
    assert [scouts_at(agent, tick, escort) for tick in (3250, 6000)] == [[], []]
    assert agent.ctx.groups.group_of(311) == ESCORT


def test_rescout_with_no_unit_to_send_for_30_s_one_rifleman_is_trained_and_sent_once_it_is_out():
    agent = stale_army_agent()
    ask(agent, base(tick=2505, units=[*guard(), HARVESTER_AT_THE_ENEMY]))

    waiting = ask(agent, base(tick=3254, units=[*guard(), HARVESTER_AT_THE_ENEMY]))
    trained = ask(agent, base(tick=3255, units=[*guard(), HARVESTER_AT_THE_ENEMY]))
    on_its_way = ask(
        agent,
        base(tick=4005, units=[*guard(), HARVESTER_AT_THE_ENEMY], production=[situations.job("Infantry", "e1")]),
    )
    new_rifle = situations.unit(320, "e1", 18, 47)
    sent = ask(agent, base(tick=4010, units=[*guard(), HARVESTER_AT_THE_ENEMY, new_rifle]))

    assert waiting == [] and on_its_way == []
    assert trained == [{"cmd": "train", "item": "e1", "count": 1}]
    assert [entry.priority for entry in agent.ledger.entries if entry.order["cmd"] == "train"] == [Priority.PRODUCTION]
    assert sent == [{"cmd": "move", "units": [320], "to": ENEMY_BASE}] and agent.ctx.groups.members(SCOUT) == [320]


def test_rescout_no_rifleman_is_trained_while_another_manager_claims_the_infantry_queue_or_with_rescout_off():
    claimed = stale_army_agent()
    claimed.ctx.claims.hold("Infantry", "opening")
    off = rescout_agent(RESCOUT_OFF)
    scouts_at(off, 1000, visible_enemy_buildings=[ENEMY_POWER_PLANT])

    claimed_orders = [ask(claimed, base(tick=tick, units=[*guard(), HARVESTER_AT_THE_ENEMY])) for tick in (2505, 3255)]
    off_orders = [ask(off, base(tick=tick, units=guard())) for tick in (3250, 4000, 4750)]

    assert claimed_orders == [[], []] and off_orders == [[], [], []]


def sent_for_a_stale_army(tuning: Tuning = RESCOUT):
    """An agent that sent the jeep 300 at 2500 because no enemy unit had been seen for a minute."""
    agent = rescout_agent(tuning)
    scouts_at(agent, 1000, JEEP, HARVESTER_AT_THE_ENEMY, visible_enemies=[ENEMY_RIFLE_AFIELD])
    scouts_at(agent, 2500, JEEP, HARVESTER_AT_THE_ENEMY)
    return agent


def test_rescout_a_mission_ends_after_120_s_and_its_unit_goes_back_to_main():
    agent = sent_for_a_stale_army()
    afield = situations.unit(300, "jeep", 40, 30, idle=False)
    fresh = {"visible_enemies": [ENEMY_RIFLE_AFIELD]}

    still = scouts_at(agent, 5499, afield, HARVESTER_AT_THE_ENEMY, **fresh)
    orders = ask(agent, base(tick=5500, units=[*guard(), afield, HARVESTER_AT_THE_ENEMY], **fresh))

    assert still == [300] and agent.ctx.groups.members(SCOUT) == [] and agent.ctx.groups.group_of(300) == MAIN
    assert orders == []


def test_rescout_a_scout_base_defense_takes_is_lost_to_its_mission_and_the_next_waits_for_the_threat_to_end():
    agent = sent_for_a_stale_army()
    agent.ctx.groups.set_defense_override(SCOUT)
    agent.ctx.threat_level = MAJOR_THREAT

    taken = scouts_at(agent, 2600, JEEP, HARVESTER_AT_THE_ENEMY)
    under_threat = scouts_at(agent, 4000, JEEP, HARVESTER_AT_THE_ENEMY)
    agent.ctx.groups.release_defense()
    agent.ctx.threat_level = NO_THREAT
    after = scouts_at(agent, 4005, JEEP, HARVESTER_AT_THE_ENEMY)

    assert (taken, under_threat, after) == ([], [], [300]) and agent.ctx.groups.group_of(300) == SCOUT


@pytest.mark.parametrize("marked, kept", [((300,), []), ((301,), [300])], ids=["the scout", "another unit"])
def test_rescout_unit_override_a_scout_base_defense_takes_alone_is_lost_to_its_mission(marked, kept):
    agent = sent_for_a_stale_army()
    agent.ctx.groups.set_unit_override(marked)

    still = scouts_at(agent, 2600, JEEP, HARVESTER_AT_THE_ENEMY)

    assert still == kept and agent.ctx.groups.group_of(300) == (SCOUT if kept else MAIN)


def test_rescout_off_a_mission_has_no_time_limit_and_a_scout_under_the_override_keeps_it():
    agent = sent_for_a_stale_army(RESCOUT_OFF)
    sent = agent.ctx.groups.members(SCOUT)
    agent.ctx.groups.set_defense_override(SCOUT)

    later = scouts_at(agent, 9000, situations.unit(300, "jeep", 40, 30, idle=False), HARVESTER_AT_THE_ENEMY)

    assert sent == [300] and later == [300]


# -- scout events ------------------------------------------------------------------------------------------------------


def phases(events: list[dict]) -> list[tuple]:
    return [(event["phase"], event["reason"], event["unit_type"], event["cell"]) for event in events]


def test_rescout_a_mission_writes_scout_events_sent_at_each_waypoint_and_lost(match_record):
    agent = sent_for_a_stale_army()
    for tick, cell in ((2505, (64, 48)), (2510, (74, 38))):
        scouts_at(agent, tick, situations.unit(300, "jeep", *cell), HARVESTER_AT_THE_ENEMY)
    scouts_at(agent, 2515, HARVESTER_AT_THE_ENEMY)

    events = match_record("scout")

    assert "scout" in records.EVENT_KINDS
    assert phases(events) == [
        ("sent", "stale_army", "jeep", [20, 52]),
        ("waypoint", "stale_army", "jeep", [74, 38]),
        ("lost", "stale_army", "jeep", [74, 38]),
    ]
    assert events[0]["seen"] == {"buildings": {}, "army_value": 0, "defenses": 0}


def test_rescout_a_mission_out_for_120_s_ends_with_a_done_event_that_says_what_is_known(match_record):
    agent = sent_for_a_stale_army()
    seen = {"visible_enemies": [ENEMY_RIFLE_AFIELD], "visible_enemy_buildings": PILLBOXES}

    scouts_at(agent, 5500, situations.unit(300, "jeep", 40, 30, idle=False), HARVESTER_AT_THE_ENEMY, **seen)

    events = match_record("scout")
    assert phases(events) == [("sent", "stale_army", "jeep", [20, 52]), ("done", "stale_army", "jeep", [40, 30])]
    assert events[-1]["seen"] == {"buildings": {"pbox": 2}, "army_value": 100, "defenses": 2}


def test_the_first_rifle_handing_over_to_a_jeep_ends_its_mission_done_and_the_jeep_s_is_sent_initial(match_record):
    agent = new_agent(
        ScoutingManager(), tuning=dataclasses.replace(tuning_off(), scouting=scouting_off(indirect_route=True))
    )
    sent_out(agent)
    ask(agent, base(tick=1005, units=[*guard(), rifle(30, 48), situations.unit(300, "jeep", 20, 52)]))

    assert phases(match_record("scout")) == [
        ("sent", "initial", "e1", [20, 48]),
        ("done", "initial", "e1", [30, 48]),
        ("sent", "initial", "jeep", [20, 52]),
    ]


def test_with_rescout_and_the_indirect_route_off_no_scout_event_is_written(match_record):
    agent = rescout_agent(RESCOUT_OFF)
    sent_out(agent)
    scout_at(agent, 1005, 50, 48, hp=40)
    scouts_at(agent, 1010)

    assert match_record("scout") == []


# -- the indirect route --------------------------------------------------------------------------------------------

INDIRECT = dataclasses.replace(tuning_off(), scouting=scouting_off(indirect_route=True))
# On the fake map, from the base at (22, 48) to the enemy's start at (74, 48), the first mission's way: the side
# waypoint 14 cells south of the line 40 percent of the way, the flank, the arc round the far side (east, then north),
# home by the side waypoint north of the line, and the staging point.
FIRST_WAY = [[43, 62], [74, 62], [88, 48], [74, 34], [43, 34], STAGING]


def indirect_agent():
    return new_agent(ScoutingManager(), tuning=INDIRECT)


def route_of(agent, side: int = 1) -> list[tuple[int, int]]:
    """The indirect way the agent's world gives a mission on a side, with nothing yet in sight."""
    return indirect_route(agent.world, agent.ctx, side, SightGrid())


def test_indirect_the_first_waypoint_lies_14_cells_off_the_line_at_40_percent_and_the_next_mission_s_on_the_other_side():
    agent = indirect_agent()
    first = sent_out(agent)
    ask(agent, base(tick=1005, units=guard()))

    second = ask(agent, base(tick=2500, units=[*guard(), situations.unit(206, "e1", 21, 52)]))

    assert first == [{"cmd": "move", "units": [SCOUT_ID], "to": [43, 62]}]
    assert second == [{"cmd": "move", "units": [206], "to": [43, 34]}]
    # 40 percent of the 52 cells to the enemy's start is 20.8, from x = 22.
    assert [abs(order[0]["to"][1] - 48) for order in (first, second)] == [14, 14]


def test_indirect_the_scout_goes_by_the_side_the_flank_the_far_side_and_home_by_the_other_side_then_is_released():
    agent = indirect_agent()
    sent_out(agent)

    stops = [tuple(cell) for cell in FIRST_WAY]
    orders = [scout_at(agent, 1005 + 5 * number, *cell) for number, cell in enumerate(stops)]

    assert [order["to"] for batch in orders for order in batch] == FIRST_WAY[1:]
    assert orders[-1] == [] and agent.ctx.groups.group_of(SCOUT_ID) == MAIN


def test_indirect_an_ore_field_10_cells_from_the_way_unseen_for_120_s_is_a_waypoint_and_one_seen_30_s_ago_is_not():
    agent = indirect_agent()
    ask(agent, base(tick=250, units=[*guard(), situations.unit(400, "harv", 50, 72)]))
    agent.ctx.terrain = Terrain([square_field((50, 72)), square_field((58, 72))])

    first = sent_out(agent)

    assert first == [{"cmd": "move", "units": [SCOUT_ID], "to": [43, 62]}]
    assert scout_at(agent, 1005, 43, 62) == [{"cmd": "move", "units": [SCOUT_ID], "to": [58, 72]}]
    assert scout_at(agent, 1010, 58, 72) == [{"cmd": "move", "units": [SCOUT_ID], "to": [74, 62]}]


def test_indirect_at_most_two_unseen_fields_are_waypoints_the_nearest_the_side_waypoint_first():
    agent = indirect_agent()
    ask(agent, base(tick=1000, units=guard()))
    agent.ctx.terrain = Terrain([square_field((66, 70)), square_field((50, 72)), square_field((58, 74))])

    assert route_of(agent)[:4] == [(43, 62), (50, 72), (58, 74), (74, 62)]


def test_indirect_no_waypoint_is_within_9_cells_of_the_enemy_start_or_a_remembered_defense_s_range_and_2():
    agent = indirect_agent()
    pillboxes = [enemy(600, "pbox", 70, 66), enemy(601, "pbox", 88, 52)]
    ask(agent, base(tick=1000, units=guard(), visible_enemy_buildings=pillboxes))
    # Unseen and 10 cells from the way, but 8.9 cells from the enemy's start.
    agent.ctx.terrain = Terrain([square_field((66, 52))])

    route = route_of(agent)

    # The flank (74, 62) moved out along the line from the pillbox at (70, 66); the arc point east, (88, 48), dropped.
    assert route == [(43, 62), (76, 60), (74, 34), (43, 34), tuple(STAGING)]
    assert all(cells.distance(point, tuple(ENEMY_BASE)) > 9 for point in route)
    assert all(cells.distance(point, (pbox["x"], pbox["y"])) > 8 for point in route for pbox in pillboxes)


def test_indirect_a_waypoint_still_covered_where_it_would_be_moved_to_is_dropped():
    agent = indirect_agent()
    # The flank (74, 62) is covered by the first; moved out, 9 cells from it toward (78, 60), the second covers it.
    pillboxes = [enemy(600, "pbox", 70, 66), enemy(601, "pbox", 80, 58)]
    ask(agent, base(tick=1000, units=guard(), visible_enemy_buildings=pillboxes))

    assert route_of(agent) == [(43, 62), (88, 48), (74, 34), (43, 34), tuple(STAGING)]


def test_indirect_the_way_of_a_base_near_the_edge_of_the_map_stays_inside_it():
    agent = indirect_agent()
    state = base(tick=1000, units=guard())
    state["legal_actions"]["enemy_base"] = [90, 5]
    ask(agent, state)

    route = route_of(agent)

    assert all(0 <= x < 96 and 0 <= y < 96 for x, y in route)
    assert all(cells.distance(point, (90, 5)) > 9 for point in route)


def test_indirect_off_the_scout_goes_straight_to_the_enemy_start_as_before():
    agent = scouting_agent()

    assert sent_out(agent) == [{"cmd": "move", "units": [SCOUT_ID], "to": ENEMY_BASE}]


def test_indirect_with_no_side_offset_the_side_waypoint_is_the_base_and_a_field_near_it_is_still_found():
    flat = dataclasses.replace(
        tuning_off(), scouting=scouting_off(indirect_route=True, scout_side_share=0.0, scout_side_cells=0)
    )
    agent = new_agent(ScoutingManager(), tuning=flat)
    ask(agent, base(tick=1000, units=guard()))
    # 12 cells from the yard a field is ours (see `own_fields`): this one lies over 15 from every building of the base.
    agent.ctx.terrain = Terrain([square_field((34, 60))])

    assert route_of(agent)[:3] == [(22, 48), (34, 60), (74, 62)]


# The scout on its way from the side waypoint to the flank, and an enemy in sight near the arc's first point.
ON_THE_WAY = (62, 62)
NEAR_THE_ARC = enemy(900, "e1", 85, 50)


def hit_on_the_way(hp_before: int, hp_after: int, *in_sight: dict) -> list[dict]:
    """The orders for a scout of the indirect way hit on its way to the flank: at `hp_before` in one state, then at
    `hp_after`."""
    agent = indirect_agent()
    sent_out(agent)
    scout_at(agent, 1005, 43, 62)
    scout_at(agent, 1010, 60, 62, hp=hp_before)
    return scout_at(agent, 1015, *ON_THE_WAY, hp=hp_after, visible_enemies=list(in_sight))


def test_indirect_a_12_hp_drop_in_one_state_skips_to_the_next_waypoint_farther_from_the_attacker():
    # The attacker at (85, 50) stands 25.9 cells from the scout: the arc's (88, 48) and (74, 34) are nearer it, so the
    # scout goes on to the home side waypoint (43, 34).
    assert moves(hit_on_the_way(100, 88, NEAR_THE_ARC)) == [{"cmd": "move", "units": [SCOUT_ID], "to": [43, 34]}]


def test_indirect_a_9_hp_drop_does_not_skip_and_a_hit_with_no_enemy_in_sight_skips_one_waypoint():
    assert moves(hit_on_the_way(100, 91, NEAR_THE_ARC)) == [{"cmd": "move", "units": [SCOUT_ID], "to": [74, 62]}]
    assert moves(hit_on_the_way(100, 88)) == [{"cmd": "move", "units": [SCOUT_ID], "to": [88, 48]}]


def test_indirect_a_hit_that_leaves_the_scout_below_half_health_withdraws_it_as_before():
    assert moves(hit_on_the_way(60, 45, NEAR_THE_ARC)) == [{"cmd": "move", "units": [SCOUT_ID], "to": [54, 59]}]


def test_indirect_a_scout_hit_on_its_way_home_keeps_going_home():
    agent = indirect_agent()
    sent_out(agent)
    for number, cell in enumerate(FIRST_WAY[:-1]):
        scout_at(agent, 1005 + 5 * number, *cell)
    scout_at(agent, 1040, 38, 40)

    assert moves(scout_at(agent, 1045, 36, 42, hp=80, visible_enemies=[NEAR_THE_ARC])) == [
        {"cmd": "move", "units": [SCOUT_ID], "to": STAGING}
    ]


# -- the scout slots ---------------------------------------------------------------------------------------------------

SLOTS = dataclasses.replace(tuning_off(), scouting=scouting_off(scout_slots=True))


def slots_agent(tuning: Tuning = SLOTS, *more, **options):
    return new_agent(ScoutingManager(), *more, tuning=tuning, **options)


def rifles(*unit_ids: int) -> list[dict]:
    """Rifle infantry by the barracks, one for each id."""
    return [situations.unit(unit_id, "e1", 18, 46) for unit_id in unit_ids]


def trains(orders: list[dict]) -> list[dict]:
    return situations.with_command(orders, "train")


def test_scout_slots_the_first_two_rifles_produced_are_in_scout_and_not_in_main_or_guard_and_the_third_guards():
    agent = slots_agent()
    ask(agent, base(tick=1000))

    for tick, unit_ids in ((1005, (300,)), (1010, (300, 301)), (1015, (300, 301, 302))):
        ask(agent, base(tick=tick, units=rifles(*unit_ids)))

    groups = agent.ctx.groups
    assert groups.members(SCOUT) == [300, 301]
    assert (groups.members(GUARD), groups.members(MAIN)) == ([302], [])


def test_scout_slots_infantry_new_in_one_state_takes_the_slots_by_id_and_a_vehicle_takes_none():
    agent = slots_agent()

    ask(agent, base(tick=1000, units=[situations.unit(299, "jeep", 20, 52), *rifles(302, 300, 301)]))

    groups = agent.ctx.groups
    assert groups.members(SCOUT) == [300, 301] and groups.group_of(299) == MAIN and groups.group_of(302) == GUARD


def test_scout_slots_at_most_scout_slot_count_units_are_in_scout():
    one_slot = dataclasses.replace(tuning_off(), scouting=scouting_off(scout_slots=True, scout_slot_count=1))
    agent = slots_agent(one_slot)

    ask(agent, base(tick=1000, units=rifles(300)))
    ask(agent, base(tick=1005, units=rifles(300, 301)))

    assert agent.ctx.groups.members(SCOUT) == [300] and agent.ctx.groups.group_of(301) == GUARD


def test_scout_slots_on_the_scouting_manager_sends_no_mission_and_the_scout_trainer_orders_nothing():
    agent = slots_agent()

    sixth = sent_out(agent)
    later = [ask(agent, base(tick=tick, units=guard())) for tick in (2505, 3255, 4005, 4755)]

    assert agent.ctx.tuning.scouting.rescout
    assert [order for order in sixth if order.get("to") == ENEMY_BASE] == []
    assert [trains(orders) for orders in later] == [[], [], [], []]
    assert agent.ctx.groups.members(SCOUT) == GUARD_IDS[:2]


def test_scout_slots_each_slot_taken_by_the_first_rifles_writes_an_assigned_scout_slot_event(match_record):
    agent = slots_agent()
    ask(agent, base(tick=1000, units=rifles(300)))
    ask(agent, base(tick=1005, units=rifles(300, 301, 302)))

    assigned = [event for event in match_record("scout_slot") if event["kind"] == "assigned"]

    assert "scout_slot" in records.EVENT_KINDS
    assert assigned == [
        {"kind": "assigned", "slot": 1, "unit_id": 300, "unit_type": "e1"},
        {"kind": "assigned", "slot": 2, "unit_id": 301, "unit_type": "e1"},
    ]


def test_scout_slots_off_the_first_rifles_stay_in_the_guard_and_no_scout_slot_event_is_written(match_record):
    agent = scouting_agent()

    ask(agent, base(tick=1000, units=rifles(300, 301)))

    assert agent.ctx.groups.members(GUARD) == [300, 301] and agent.ctx.groups.members(SCOUT) == []
    assert match_record("scout_slot") == []


@pytest.mark.parametrize("tuning, scouts_weighed", [(SLOTS, 0), (tuning_off(), 200)], ids=["slots on", "slots off"])
def test_scout_slots_the_scouts_count_toward_no_army_value_the_plans_weigh(tuning, scouts_weighed):
    agent = slots_agent(tuning)
    state = base(tick=1000, units=rifles(300, 301, 302), military={"army_value": 900})
    ask(agent, state)
    agent.ctx.groups.assign(300, SCOUT)
    agent.ctx.groups.assign(301, SCOUT)

    situation = plans.observe(agent.world, agent.ctx, agent.ctx.tuning, None)

    assert agent.ctx.groups.members(SCOUT) == [300, 301] and agent.ctx.groups.members(GUARD) == [302]
    assert (situation.combat_army_value, situation.army_value) == (100 + scouts_weighed, 700 + scouts_weighed)
    assert situation.main_strength == 0


SOVIET_BASE = (YARD, POWER, ("powr", 16, 52), ("barr", 18, 44), ("proc", 26, 54), ("proc", 30, 54))
SOVIET_WAR_FACTORY = ("weap", 28, 44)


def soviet(*more_buildings, tick: int = 1000, **observation) -> dict:
    """A Soviet base of the construction yard, two power plants, a barracks and two refineries, with what the test adds."""
    return base_state("russia", *SOVIET_BASE, *more_buildings, tick=tick, **observation)


def kennel_builds(orders: list[dict]) -> list[dict]:
    return [order for order in situations.with_command(orders, "build") if order["item"] == "kenn"]


def test_scout_slots_a_soviet_base_orders_one_kennel_once_its_war_factory_stands_and_none_before():
    agent = new_agent(UpkeepManager(), tuning=SLOTS)

    before = ask(agent, soviet(tick=1000))
    ordered = ask(agent, soviet(SOVIET_WAR_FACTORY, tick=1005))
    on_its_way = ask(agent, soviet(SOVIET_WAR_FACTORY, tick=1010, production=[situations.job("Building", "kenn")]))
    standing = ask(agent, soviet(SOVIET_WAR_FACTORY, ("kenn", 14, 44), tick=1015))

    assert [kennel_builds(orders) for orders in (before, on_its_way, standing)] == [[], [], []]
    assert kennel_builds(ordered) == [{"cmd": "build", "item": "kenn"}]


@pytest.mark.parametrize(
    "tuning, state",
    [
        (
            SLOTS,
            base_state(
                "england", YARD, POWER, ("powr", 16, 52), BARRACKS, ("proc", 26, 54), ("proc", 30, 54), ("weap", 28, 44)
            ),
        ),
        (tuning_off(), soviet(SOVIET_WAR_FACTORY)),
    ],
    ids=["allies", "slots off"],
)
def test_scout_slots_no_kennel_is_ordered_for_the_allies_or_with_the_switch_off(tuning, state):
    agent = new_agent(UpkeepManager(), tuning=tuning)

    assert kennel_builds(ask(agent, state)) == []


def test_scout_slots_the_kennel_waits_for_the_money_and_its_reserve():
    agent = new_agent(UpkeepManager(), tuning=SLOTS)
    short = SLOTS.upkeep.production_cash_reserve + 199

    waiting = ask(agent, soviet(SOVIET_WAR_FACTORY, tick=1000, cash=short))
    paid = ask(agent, soviet(SOVIET_WAR_FACTORY, tick=1005, cash=short + 1))

    assert (kennel_builds(waiting), kennel_builds(paid)) == ([], [{"cmd": "build", "item": "kenn"}])


ALLIED_WITH_WAR_FACTORY = (
    YARD,
    POWER,
    ("powr", 16, 52),
    BARRACKS,
    ("proc", 26, 54),
    ("proc", 30, 54),
    ("weap", 28, 44),
)


def allied(*units: dict, tick: int = 1000, **observation) -> dict:
    """An Allied base with a war factory, so that jeeps can be trained, and the units given."""
    return base_state("england", *ALLIED_WITH_WAR_FACTORY, tick=tick, units=list(units), **observation)


def jeep(unit_id: int) -> dict:
    return situations.unit(unit_id, "jeep", 28, 48)


def test_scout_slots_allies_with_a_war_factory_order_two_jeeps_and_each_takes_a_slot_its_rifle_going_to_main():
    agent = slots_agent()

    ordered = ask(agent, allied(*rifles(300, 301), tick=1000))
    waiting = ask(agent, allied(*rifles(300, 301), tick=1005, production=[situations.job("Vehicle", "jeep")]))
    claimed = agent.ctx.claims.is_held_by_other("Vehicle", "upkeep")
    ask(agent, allied(*rifles(300, 301), jeep(400), tick=1010, production=[situations.job("Vehicle", "jeep")]))
    first = (agent.ctx.groups.members(SCOUT), agent.ctx.groups.group_of(300))
    ask(agent, allied(*rifles(300, 301), jeep(400), jeep(401), tick=1015))

    groups = agent.ctx.groups
    assert trains(ordered) == [{"cmd": "train", "item": "jeep", "count": 2}] and trains(waiting) == []
    assert [entry.priority for entry in agent.ledger.entries if entry.order["cmd"] == "train"] == [Priority.PRODUCTION]
    assert claimed and not agent.ctx.claims.is_held_by_other("Vehicle", "upkeep")
    assert first == ([301, 400], MAIN)
    assert groups.members(SCOUT) == [400, 401] and [groups.group_of(unit_id) for unit_id in (300, 301)] == [MAIN, MAIN]


def test_scout_slots_each_upgrade_writes_an_upgraded_event_naming_the_unit_it_replaces(match_record):
    agent = slots_agent()
    ask(agent, allied(*rifles(300, 301), tick=1000))

    ask(agent, allied(*rifles(300, 301), jeep(400), jeep(401), tick=1005))

    upgraded = [event for event in match_record("scout_slot") if event["kind"] == "upgraded"]
    assert upgraded == [
        {"kind": "upgraded", "slot": 1, "unit_id": 400, "unit_type": "jeep", "replaces": 300},
        {"kind": "upgraded", "slot": 2, "unit_id": 401, "unit_type": "jeep", "replaces": 301},
    ]


def test_scout_slots_no_jeep_is_ordered_without_a_war_factory_or_during_the_opening_s_build():
    without = slots_agent()
    during = slots_agent()
    during.ctx.opening_build = OpeningBuild(steps=("proc",))

    no_factory = ask(without, base(tick=1000, units=rifles(300, 301)))
    in_the_opening = ask(during, allied(*rifles(300, 301), tick=1000))
    during.ctx.opening_build = OpeningBuild()
    once_complete = ask(during, allied(*rifles(300, 301), tick=1005))

    assert (trains(no_factory), trains(in_the_opening)) == ([], [])
    assert trains(once_complete) == [{"cmd": "train", "item": "jeep", "count": 2}]


def test_scout_slots_soviets_order_the_kennel_after_the_opening_then_two_dogs_that_take_the_slots():
    agent = new_agent(UpkeepManager(), ScoutingManager(), tuning=SLOTS)
    scouts = rifles(300, 301)

    before = ask(agent, soviet(tick=1000, units=scouts))
    kennel = ask(agent, soviet(SOVIET_WAR_FACTORY, tick=1005, units=scouts))
    dogs = ask(agent, soviet(SOVIET_WAR_FACTORY, ("kenn", 14, 44), tick=1010, units=scouts))
    dog_units = [situations.unit(unit_id, "dog", 14, 48) for unit_id in (400, 401)]
    ask(agent, soviet(SOVIET_WAR_FACTORY, ("kenn", 14, 44), tick=1015, units=[*scouts, *dog_units]))

    assert (kennel_builds(before), trains(before)) == ([], [])
    assert kennel_builds(kennel) == [{"cmd": "build", "item": "kenn"}]
    assert {"cmd": "train", "item": "dog", "count": 2} in trains(dogs)
    assert agent.ctx.groups.members(SCOUT) == [400, 401] and agent.ctx.groups.group_of(300) == MAIN


def test_scout_slots_an_order_that_shows_nowhere_is_given_up_and_made_again():
    agent = slots_agent()

    train_ticks = [tick for tick in range(1000, 1200, 5) if trains(ask(agent, allied(*rifles(300, 301), tick=tick)))]

    # The order holds the ledger's reservation for 3 s (75 ticks); 2 s after that it is given up and made again.
    assert len(train_ticks) == 2 and train_ticks[0] == 1000 and 1075 < train_ticks[1] < 1200


def test_scout_slots_the_jeeps_are_ordered_while_an_economy_first_hold_keeps_the_unit_queues_waiting(monkeypatch):
    monkeypatch.setattr(EconomyHold, "holds", lambda *arguments: True)
    tuning = dataclasses.replace(SLOTS, upkeep=dataclasses.replace(SLOTS.upkeep, economy_first=True))
    agent = new_agent(UpkeepManager(), ScoutingManager(), tuning=tuning)

    orders = ask(agent, allied(*rifles(300, 301), tick=1000))

    assert {"cmd": "train", "item": "jeep", "count": 2} in trains(orders)
    assert [order["item"] for order in trains(orders) if order["item"] not in ("jeep", "harv")] == []


def jeep_scouts(agent) -> None:
    """Have two rifles take the slots at 1000, the two jeeps ordered for them show in the Vehicle queue at 1003, and
    the jeeps, 400 and 401, take over at 1005."""
    ask(agent, allied(*rifles(300, 301), tick=1000))
    ask(agent, allied(*rifles(300, 301), tick=1003, production=[situations.job("Vehicle", "jeep")] * 2))
    ask(agent, allied(*rifles(300, 301), jeep(400), jeep(401), tick=1005))


def test_scout_slots_a_scout_lost_while_a_jeep_is_buildable_is_replaced_by_a_jeep_that_takes_its_slot():
    agent = slots_agent()
    jeep_scouts(agent)

    lost = ask(agent, allied(*rifles(300, 301), jeep(401), tick=1010))
    queued = ask(agent, allied(*rifles(300, 301), jeep(401), tick=1015, production=[situations.job("Vehicle", "jeep")]))
    ask(agent, allied(*rifles(300, 301), jeep(401), jeep(402), tick=1020))

    assert trains(lost) == [{"cmd": "train", "item": "jeep", "count": 1}] and trains(queued) == []
    assert agent.ctx.groups.members(SCOUT) == [401, 402]


def test_scout_slots_a_second_loss_within_20_s_is_replaced_once_the_floor_has_passed_since_the_first_order():
    agent = slots_agent()
    jeep_scouts(agent)
    ask(agent, allied(*rifles(300, 301), jeep(401), tick=1010))
    ask(agent, allied(*rifles(300, 301), jeep(401), jeep(402), tick=1015))

    ordered_at = [
        tick for tick in range(1100, 1600, 5) if trains(ask(agent, allied(*rifles(300, 301), jeep(402), tick=tick)))
    ]

    # The first replacement was ordered at 1010; the floor is 20 s, 500 ticks.
    assert ordered_at == [1510]


def test_scout_slots_with_every_slot_empty_a_rifle_is_ordered_at_once_whatever_the_floor():
    agent = slots_agent()
    jeep_scouts(agent)
    first = ask(agent, allied(*rifles(300, 301), jeep(401), tick=1010))

    both = ask(agent, allied(*rifles(300, 301), tick=1015, production=[situations.job("Vehicle", "jeep")]))
    ask(agent, allied(*rifles(300, 301, 310), tick=1020, production=[situations.job("Vehicle", "jeep")]))

    assert trains(first) == [{"cmd": "train", "item": "jeep", "count": 1}]
    assert trains(both) == [{"cmd": "train", "item": "e1", "count": 1}]
    assert agent.ctx.groups.members(SCOUT) == [310]


def test_scout_slots_a_lost_scout_writes_a_lost_event_and_its_replacement_a_replaced_event(match_record):
    agent = slots_agent()
    jeep_scouts(agent)
    ask(agent, allied(*rifles(300, 301), jeep(401), tick=1010))
    ask(agent, allied(*rifles(300, 301), jeep(401), jeep(402), tick=1015))

    events = [event for event in match_record("scout_slot") if event["kind"] in ("lost", "replaced")]

    assert events == [
        {"kind": "lost", "slot": 1, "unit_id": 400, "unit_type": "jeep", "reason": "killed"},
        {"kind": "replaced", "slot": 1, "unit_id": 402, "unit_type": "jeep"},
    ]


def test_scout_slots_a_rifle_scout_lost_while_its_jeep_is_on_its_way_gets_no_second_order_and_the_jeep_refills_it():
    agent = slots_agent()
    ask(agent, allied(*rifles(300, 301), tick=1000))
    on_its_way = [situations.job("Vehicle", "jeep")] * 2

    lost = ask(agent, allied(*rifles(301), tick=1005, production=on_its_way))
    ask(agent, allied(*rifles(301), jeep(400), tick=1010, production=on_its_way[:1]))

    assert trains(lost) == [] and agent.ctx.groups.members(SCOUT) == [301, 400]


# -- the scout spend cap ----------------------------------------------------------------------------------------------

SPEND_CAP = dataclasses.replace(tuning_off(), scouting=scouting_off(scout_slots=True, scout_spend_cap=True))


@pytest.mark.parametrize("tuning, ordered_at", [(SLOTS, 1760), (SPEND_CAP, 2510)], ids=["cap off", "cap on"])
def test_scout_spend_cap_a_loss_30_s_after_the_last_replacement_waits_until_60_s_have_passed(tuning, ordered_at):
    agent = slots_agent(tuning)
    jeep_scouts(agent)
    first = ask(agent, allied(*rifles(300, 301), jeep(401), tick=1010))
    ask(agent, allied(*rifles(300, 301), jeep(401), jeep(402), tick=1015))

    later = next(
        tick for tick in range(1760, 2600, 5) if trains(ask(agent, allied(*rifles(300, 301), jeep(402), tick=tick)))
    )

    # The first replacement was ordered at 1010 and the second scout lost at 1760, 30 s later: the floor is 20 s (500
    # ticks) with the cap off and 60 s (1,500 ticks) with it on.
    assert trains(first) == [{"cmd": "train", "item": "jeep", "count": 1}]
    assert later == ordered_at


@pytest.mark.parametrize("tuning, ordered", [(SLOTS, [{"cmd": "train", "item": "e1", "count": 1}]), (SPEND_CAP, [])])
def test_scout_spend_cap_a_slot_whose_jeep_is_on_its_way_is_not_empty_and_the_rifle_waits(tuning, ordered):
    agent = slots_agent(tuning)
    jeep_scouts(agent)
    ask(agent, allied(*rifles(300, 301), jeep(401), tick=1010))

    both_lost = ask(agent, allied(*rifles(300, 301), tick=1015, production=[situations.job("Vehicle", "jeep")]))

    assert trains(both_lost) == ordered


@pytest.mark.parametrize("tuning, count", [(SLOTS, 2), (SPEND_CAP, 1)], ids=["cap off", "cap on"])
def test_scout_spend_cap_with_every_slot_empty_and_nothing_on_its_way_a_rifle_is_ordered_at_once(tuning, count):
    agent = slots_agent(tuning)
    jeep_scouts(agent)
    ask(agent, allied(*rifles(300, 301), jeep(401), tick=1010))
    ask(agent, allied(*rifles(300, 301), jeep(401), jeep(402), tick=1015))

    both_lost = ask(agent, allied(*rifles(300, 301), tick=1020))

    # Within the floor of the replacement ordered at 1010: with the cap on, the rifle ordered for the first slot keeps
    # the second waiting.
    assert trains(both_lost) == [{"cmd": "train", "item": "e1", "count": count}]


# The floor of the replacements with the spend cap on, in ticks: 60 s.
CAPPED_FLOOR_TICKS = 1500


def six_jeeps_ordered(agent) -> list[dict]:
    """Have two jeeps take the slots, then slot 1's jeep lost and replaced by a new one four times, each loss a floor
    after the last; the train orders of it all. The last jeep, 405, holds slot 1 from 5515."""
    orders = ask(agent, allied(*rifles(300, 301), tick=1000))
    ask(agent, allied(*rifles(300, 301), tick=1003, production=[situations.job("Vehicle", "jeep")] * 2))
    ask(agent, allied(*rifles(300, 301), jeep(400), jeep(401), tick=1005))
    for number in range(4):
        tick = 1010 + number * CAPPED_FLOOR_TICKS
        orders += ask(agent, allied(*rifles(300, 301), jeep(401), tick=tick))
        ask(agent, allied(*rifles(300, 301), jeep(401), jeep(402 + number), tick=tick + 5))
    return trains(orders)


def capped(events: list[dict]) -> list[dict]:
    return [event for event in events if event["kind"] == "capped"]


@pytest.mark.parametrize("tuning, item", [(SLOTS, "jeep"), (SPEND_CAP, "e1")], ids=["cap off", "cap on"])
def test_scout_spend_cap_after_6_jeeps_were_ordered_a_lost_scout_is_replaced_by_a_rifle(tuning, item, match_record):
    agent = slots_agent(tuning)
    jeeps = six_jeeps_ordered(agent)

    seventh = ask(agent, allied(*rifles(300, 301), jeep(401), tick=7010))

    assert sum(order["count"] for order in jeeps if order["item"] == "jeep") == 6
    assert trains(seventh) == [{"cmd": "train", "item": item, "count": 1}]
    refused = {
        "kind": "capped",
        "slot": 1,
        "unit_id": None,
        "unit_type": None,
        "refused": "jeep",
        "order": "replacement",
    }
    assert capped(match_record("scout_slot")) == ([refused] if item == "e1" else [])


def test_scout_spend_cap_once_6_jeeps_were_ordered_the_rifle_that_refills_a_slot_is_never_upgraded(match_record):
    agent = slots_agent(SPEND_CAP)
    six_jeeps_ordered(agent)
    ask(agent, allied(*rifles(300, 301), jeep(401), tick=7010))

    later = [ask(agent, allied(*rifles(300, 301, 310), jeep(401), tick=tick)) for tick in (7015, 7020, 9000)]

    assert [trains(orders) for orders in later] == [[], [], []]
    assert agent.ctx.groups.members(SCOUT) == [401, 310]
    refused = {"kind": "capped", "slot": 1, "unit_id": 310, "unit_type": "e1", "refused": "jeep", "order": "upgrade"}
    assert capped(match_record("scout_slot"))[1:] == [refused]


ONE_FAST_SCOUT = dataclasses.replace(
    tuning_off(), scouting=scouting_off(scout_slots=True, scout_spend_cap=True, fast_scouts_max=1)
)


def test_scout_spend_cap_two_upgrades_with_one_fast_scout_left_give_one_order_and_one_capped_event(match_record):
    agent = slots_agent(ONE_FAST_SCOUT)

    first = ask(agent, allied(*rifles(300, 301), tick=1000))
    again = ask(agent, allied(*rifles(300, 301), tick=1005, production=[situations.job("Vehicle", "jeep")]))

    assert trains(first) == [{"cmd": "train", "item": "jeep", "count": 1}] and trains(again) == []
    refused = {"kind": "capped", "slot": 2, "unit_id": 301, "unit_type": "e1", "refused": "jeep", "order": "upgrade"}
    assert capped(match_record("scout_slot")) == [refused]


# -- the Soviet scout unit --------------------------------------------------------------------------------------------

FLAK_SLOTS = dataclasses.replace(tuning_off(), scouting=scouting_off(scout_slots=True, soviet_scout_unit="ftrk"))


def flak_truck(unit_id: int) -> dict:
    return situations.unit(unit_id, "ftrk", 28, 48)


def test_soviet_scout_unit_ftrk_with_a_war_factory_two_flak_trucks_are_ordered_and_each_takes_a_slot_once_out():
    agent = slots_agent(FLAK_SLOTS)
    on_its_way = [situations.job("Vehicle", "ftrk")] * 2

    ordered = ask(agent, soviet(SOVIET_WAR_FACTORY, tick=1000, units=rifles(300, 301)))
    waiting = ask(agent, soviet(SOVIET_WAR_FACTORY, tick=1005, units=rifles(300, 301), production=on_its_way))
    while_waiting = (agent.ctx.groups.members(SCOUT), agent.ctx.claims.is_held_by_other("Vehicle", "upkeep"))
    ask(agent, soviet(SOVIET_WAR_FACTORY, tick=1010, units=[*rifles(300, 301), flak_truck(400), flak_truck(401)]))

    groups = agent.ctx.groups
    assert trains(ordered) == [{"cmd": "train", "item": "ftrk", "count": 2}] and trains(waiting) == []
    assert while_waiting == ([300, 301], True)
    assert groups.members(SCOUT) == [400, 401] and [groups.group_of(unit_id) for unit_id in (300, 301)] == [MAIN, MAIN]


def flak_scouts(agent) -> None:
    """Have two rifles take the Soviet slots at 1000, the two flak trucks ordered for them show in the Vehicle queue at
    1003, and the flak trucks, 400 and 401, take over at 1005."""
    queued = [situations.job("Vehicle", "ftrk")] * 2
    ask(agent, soviet(SOVIET_WAR_FACTORY, tick=1000, units=rifles(300, 301)))
    ask(agent, soviet(SOVIET_WAR_FACTORY, tick=1003, units=rifles(300, 301), production=queued))
    ask(agent, soviet(SOVIET_WAR_FACTORY, tick=1005, units=[*rifles(300, 301), flak_truck(400), flak_truck(401)]))


def test_soviet_scout_unit_ftrk_a_scout_lost_with_a_war_factory_standing_is_replaced_by_a_flak_truck():
    agent = slots_agent(FLAK_SLOTS)
    flak_scouts(agent)

    lost = ask(agent, soviet(SOVIET_WAR_FACTORY, tick=1010, units=[*rifles(300, 301), flak_truck(401)]))
    ask(agent, soviet(SOVIET_WAR_FACTORY, tick=1015, units=[*rifles(300, 301), flak_truck(401), flak_truck(402)]))

    assert trains(lost) == [{"cmd": "train", "item": "ftrk", "count": 1}]
    assert agent.ctx.groups.members(SCOUT) == [401, 402]


@pytest.mark.parametrize("tuning, unit", [(SLOTS, "dog"), (FLAK_SLOTS, "ftrk")], ids=["dog", "ftrk"])
def test_soviet_scout_unit_with_a_kennel_and_a_war_factory_the_slots_train_the_unit_the_setting_names(tuning, unit):
    agent = slots_agent(tuning)

    ordered = ask(agent, soviet(SOVIET_WAR_FACTORY, ("kenn", 14, 44), tick=1000, units=rifles(300, 301)))

    assert trains(ordered) == [{"cmd": "train", "item": unit, "count": 2}]


@pytest.mark.parametrize("tuning, scout_unit", [(SLOTS, "dog"), (FLAK_SLOTS, "ftrk")], ids=["dog", "ftrk"])
def test_soviet_scout_unit_a_kennel_is_ordered_for_dogs_and_none_at_any_point_for_flak_trucks(tuning, scout_unit):
    agent = new_agent(UpkeepManager(), ScoutingManager(), tuning=tuning)
    scouts = rifles(300, 301)

    batches = [ask(agent, soviet(tick=1000, units=scouts))]
    batches += [ask(agent, soviet(SOVIET_WAR_FACTORY, tick=tick, units=scouts)) for tick in range(1005, 1500, 5)]
    batches.append(ask(agent, soviet(SOVIET_WAR_FACTORY, ("kenn", 14, 44), tick=1500, units=scouts)))

    kennels = [order for orders in batches for order in kennel_builds(orders)]
    assert bool(kennels) is (scout_unit == "dog") and kennel_builds(batches[0]) == []
    assert scout_unit in {order["item"] for orders in batches for order in trains(orders)}


def test_soviet_scout_unit_ftrk_orders_no_flak_truck_before_a_war_factory_and_leaves_the_allied_jeeps():
    soviets, allies = slots_agent(FLAK_SLOTS), slots_agent(FLAK_SLOTS)

    no_factory = ask(soviets, soviet(tick=1000, units=rifles(300, 301)))
    allied_orders = ask(allies, allied(*rifles(300, 301), tick=1000))

    assert trains(no_factory) == [] and soviets.ctx.groups.members(SCOUT) == [300, 301]
    assert trains(allied_orders) == [{"cmd": "train", "item": "jeep", "count": 2}]


# -- steering the scouts: where they go -------------------------------------------------------------------------------

N_FLANK, E_FLANK, S_FLANK = (74, 34), (88, 48), (74, 62)
MAP_CENTER = (48, 48)


def candidates_at(tick: int, *, seen: tuple[dict, ...] = (), avoid: tuple = (), **observation) -> list:
    """The scout candidates of a slots agent's world at `tick`, with a sight grid that saw `seen` units at tick 900."""
    agent = slots_agent()
    sight = SightGrid()
    if seen:
        ask(agent, base(tick=900, units=list(seen)))
        sight.observe(agent.world)
    ask(agent, base(tick=tick, **observation))
    return scout_targets.candidates(agent.world, agent.ctx, sight, avoid)


def test_steering_the_enemy_base_flanks_14_cells_out_are_candidates_but_the_one_by_the_map_center():
    flanks = {candidate.area: candidate.cell for candidate in candidates_at(1000) if "flank" in candidate.area}

    # The west flank, (60, 48), lies 12 cells from the map's center.
    assert flanks == {"enemy base N flank": N_FLANK, "enemy base E flank": E_FLANK, "enemy base S flank": S_FLANK}


def test_steering_the_enemy_base_center_is_never_a_candidate_nor_any_cell_within_9_cells_of_it_or_12_of_the_center():
    found = candidates_at(1000, visible_enemy_buildings=[enemy(700, "proc", 74, 44)])

    assert found and all(not cells.is_within(candidate.cell, tuple(ENEMY_BASE), 9) for candidate in found)
    assert all(not cells.is_within(candidate.cell, MAP_CENTER, 12) for candidate in found)


def test_steering_the_candidates_are_ranked_by_the_seconds_unseen_and_the_bonus_of_their_kind():
    found = candidates_at(1000, seen=(situations.unit(300, "e1", *N_FLANK),))
    by_cell = {candidate.cell: candidate for candidate in found}

    # Never seen counts from tick 0: 40 s at tick 1000. The north flank was seen at tick 900, 4 s ago.
    assert (by_cell[E_FLANK].unseen_seconds, by_cell[E_FLANK].score) == (40, 100)
    assert (by_cell[N_FLANK].unseen_seconds, by_cell[N_FLANK].score) == (4, 64)
    assert (by_cell[(4, 4)].area, by_cell[(4, 4)].score) == ("map edge", 40)
    # Of two as stale, the first cell: (74, 62) before (88, 48).
    assert [candidate.cell for candidate in found[:3]] == [S_FLANK, E_FLANK, N_FLANK]


def test_steering_an_enemy_building_away_from_its_start_is_an_expansion_and_an_ore_field_a_candidate():
    agent = slots_agent()
    ask(agent, base(tick=995))
    agent.ctx.terrain = Terrain([square_field((60, 80))])
    away = [enemy(700, "proc", 40, 84), enemy(701, "pbox", 30, 70)]
    ask(agent, base(tick=1000, visible_enemy_buildings=away))

    found = {candidate.cell: candidate for candidate in scout_targets.candidates(agent.world, agent.ctx, SightGrid())}

    assert (found[(40, 84)].area, found[(40, 84)].score) == ("expansion", 130)
    assert (found[(60, 80)].area, found[(60, 80)].score) == ("ore field", 70)
    assert (30, 70) not in found


def test_steering_a_place_a_remembered_defense_covers_or_near_the_other_scout_s_destination_is_no_candidate():
    covered = candidates_at(1000, visible_enemy_buildings=[enemy(600, "pbox", 74, 37)])
    avoided = candidates_at(1000, avoid=(E_FLANK,))

    assert N_FLANK not in [candidate.cell for candidate in covered]
    assert all(not cells.is_within(candidate.cell, E_FLANK, 12) for candidate in avoided)
    assert {E_FLANK, (91, 48)} - {candidate.cell for candidate in candidates_at(1000)} == set()


# -- steering the scouts: danger and the way round it ------------------------------------------------------------------

NORTH_START, NORTH_END = (30, 20), (70, 20)


def world_and_ctx(**observation):
    """The world and match context of a slots agent that has seen one state at tick 1000 with what is given."""
    agent = slots_agent()
    ask(agent, base(tick=1000, **observation))
    return agent.world, agent.ctx


def walked(route: list, start: tuple[int, int]) -> list[tuple[int, int]]:
    """Every cell a unit passes going from `start` through the cells of a route in turn."""
    stops = [start, *route]
    return [cell for first, second in zip(stops, stops[1:]) for cell in cells.line(first, second)]


def test_steering_with_a_known_pillbox_on_the_straight_line_the_way_goes_by_a_side_waypoint_that_crosses_no_cover():
    world, ctx = world_and_ctx(visible_enemy_buildings=[enemy(600, "pbox", 50, 20)])
    dangers = scout_steering.Dangers(world, ctx)

    way = scout_steering.route(NORTH_START, NORTH_END, dangers, world, ctx)

    assert len(way) == 2 and way[-1] == NORTH_END
    assert way[0] in side_waypoints(NORTH_START, NORTH_END, world.map_bounds, 14)
    covered = [
        cell
        for cell in walked(way, NORTH_START)
        if coverage.is_covered(cell, world.known_enemy_defenses, world.items, ctx.tuning.coverage)
    ]
    assert covered == []


def test_steering_a_way_with_no_danger_and_far_from_the_map_center_is_the_straight_line():
    world, ctx = world_and_ctx()

    assert scout_steering.route(NORTH_START, NORTH_END, scout_steering.Dangers(world, ctx), world, ctx) == [NORTH_END]


def test_steering_a_way_that_would_pass_within_12_cells_of_the_map_center_goes_by_a_side_waypoint():
    world, ctx = world_and_ctx()
    start = (24, 48)

    way = scout_steering.route(start, E_FLANK, scout_steering.Dangers(world, ctx), world, ctx)

    side, end = way
    assert end == E_FLANK and side in side_waypoints(start, E_FLANK, world.map_bounds, 28)
    assert min(cells.distance(cell, MAP_CENTER) for cell in walked(way, start)) > 12


def test_steering_a_destination_a_danger_covers_has_no_safe_way():
    world, ctx = world_and_ctx(visible_enemy_buildings=[enemy(600, "pbox", 70, 22)])

    assert scout_steering.route(NORTH_START, NORTH_END, scout_steering.Dangers(world, ctx), world, ctx) is None


def test_steering_an_enemy_unit_in_sight_is_dangerous_within_its_range_and_3_cells_and_one_out_of_sight_is_not():
    world, ctx = world_and_ctx(visible_enemies=[enemy(800, "2tnk", 40, 20)])
    seen = scout_steering.Dangers(world, ctx)
    out_of_sight = slots_agent()
    ask(out_of_sight, base(tick=1000, visible_enemies=[enemy(800, "2tnk", 40, 20)]))
    ask(out_of_sight, base(tick=1005))
    gone = scout_steering.Dangers(out_of_sight.world, out_of_sight.ctx)

    # The 2tnk shoots 4.75 cells; with the margin of 3 its danger reaches 7.75 cells.
    assert (seen.nearest((47, 20)), seen.nearest((48, 20))) == (((40, 20), 0.0), ((40, 20), 0.25))
    assert seen.nearest((50, 20)) == ((40, 20), 2.25)
    assert gone.nearest((40, 20)) is None


def test_steering_a_scout_within_the_map_center_circle_may_leave_it_but_not_go_deeper():
    world, ctx = world_and_ctx()
    dangers = scout_steering.Dangers(world, ctx)

    assert scout_steering.route((44, 48), (20, 48), dangers, world, ctx) == [(20, 48)]
    assert scout_steering.route((44, 48), (44, 90), dangers, world, ctx) == [(44, 90)]
    assert scout_steering.route((44, 48), (88, 48), dangers, world, ctx) != [(88, 48)]


# -- steering the scouts: the orders of each cycle ---------------------------------------------------------------------


def scout_unit(x: int, y: int, *, idle: bool = False) -> Unit:
    """A scout of the world as the steering reads it."""
    return Unit(id=300, type="e1", x=x, y=y, hp=100, idle=idle, activity=None if idle else "move", stance=None)


def scout_moves(orders: list[dict]) -> list[dict]:
    """The move orders of a batch that name a single unit: those the steering sends."""
    return [order for order in moves(orders) if len(order["units"]) == 1]


def destinations(events: list[dict]) -> list[dict]:
    return [event for event in events if event["kind"] == "destination"]


def test_steering_a_pillbox_seen_on_the_scout_s_straight_way_turns_it_by_a_side_waypoint_that_crosses_no_cover():
    world, ctx = world_and_ctx(visible_enemy_buildings=[enemy(600, "pbox", 50, 20)])
    course = scout_steering.Course()
    course.go(NORTH_END, [NORTH_END], scout_steering.SCRIPT, 900)

    move = scout_steering.steer(
        course, scout_unit(*NORTH_START), [], SightGrid(), scout_steering.Dangers(world, ctx), world, ctx
    )

    assert course.destination == NORTH_END and move.change is None
    assert move.cell == course.legs[0] and move.cell in side_waypoints(NORTH_START, NORTH_END, world.map_bounds, 14)
    covered = [
        cell
        for cell in walked(course.legs, NORTH_START)
        if coverage.is_covered(cell, world.known_enemy_defenses, world.items, ctx.tuning.coverage)
    ]
    assert covered == []


def test_steering_the_first_destination_past_the_map_center_is_reached_by_a_side_waypoint_and_written(match_record):
    agent = slots_agent()

    orders = ask(agent, base(tick=1000, units=[situations.unit(300, "e1", 24, 48), situations.unit(301, "e1", 24, 40)]))

    first, second = destinations(match_record("scout_slot"))
    assert (first["slot"], first["unit_id"], first["source"], first["reason"]) == (1, 300, "script", "new")
    # The three flanks are as stale; the first by cell is the north one.
    assert first["cell"] == list(N_FLANK) and second["cell"] != list(N_FLANK)
    (to_first,) = [order for order in scout_moves(orders) if order["units"] == [300]]
    sides = [side for offset in (14, 28) for side in side_waypoints((24, 48), N_FLANK, agent.world.map_bounds, offset)]
    assert to_first["to"] in [list(side) for side in sides]
    assert agent.ctx.scout_destinations == (N_FLANK, tuple(second["cell"]))


def test_steering_an_enemy_tank_coming_within_6_cells_gets_a_flee_order_away_from_it_in_that_cycle():
    agent = slots_agent()
    scouts = [situations.unit(300, "e1", 30, 20, idle=False), situations.unit(301, "e1", 18, 46, idle=False)]
    ask(agent, base(tick=1000, units=scouts))

    orders = ask(agent, base(tick=1005, units=scouts, visible_enemies=[enemy(800, "2tnk", 36, 20)]))

    assert {"cmd": "move", "units": [300], "to": [24, 20]} in orders
    flee = [
        entry for entry in agent.ledger.entries if entry.order.get("units") == [300] and entry.order["to"] == [24, 20]
    ]
    assert [entry.priority for entry in flee] == [Priority.SCOUT]


def test_steering_an_idle_scout_is_ordered_again_in_the_next_cycle_and_one_on_its_way_is_not():
    agent = slots_agent()
    on_its_way = [situations.unit(300, "e1", 24, 48, idle=False), situations.unit(301, "e1", 24, 40, idle=False)]
    ask(agent, base(tick=1000, units=on_its_way))

    going = ask(agent, base(tick=1005, units=on_its_way))
    idle = ask(agent, base(tick=1010, units=[situations.unit(300, "e1", 24, 48, idle=True), on_its_way[1]]))

    assert scout_moves(going) == []
    assert [order["units"] for order in scout_moves(idle)] == [[300]]


def test_steering_no_batch_carries_more_than_2_scout_orders():
    three = dataclasses.replace(tuning_off(), scouting=scouting_off(scout_slots=True, scout_slot_count=3))
    agent = slots_agent(three)
    scouts = [situations.unit(300 + number, "e1", 24, 40 + 4 * number) for number in range(3)]

    batches = [ask(agent, base(tick=tick, units=scouts)) for tick in range(1000, 1100, 5)]

    assert agent.ctx.groups.members(SCOUT) == [300, 301, 302]
    assert max(len(scout_moves(orders)) for orders in batches) == 2
    assert {order["units"][0] for orders in batches for order in scout_moves(orders)} == {300, 301, 302}


def test_steering_a_scout_at_35_hp_is_released_to_main_at_the_staging_point_and_its_slot_refilled(match_record):
    agent = slots_agent()
    jeep_scouts(agent)

    orders = ask(agent, allied(*rifles(300, 301), situations.unit(400, "jeep", 40, 30, hp=35), jeep(401), tick=1010))

    assert {"cmd": "move", "units": [400], "to": STAGING} in orders
    assert agent.ctx.groups.group_of(400) == MAIN and agent.ctx.groups.members(SCOUT) == [401]
    assert trains(orders) == [{"cmd": "train", "item": "jeep", "count": 1}]
    lost = [event for event in match_record("scout_slot") if event["kind"] == "lost"]
    assert lost == [{"kind": "lost", "slot": 1, "unit_id": 400, "unit_type": "jeep", "reason": "released"}]


def test_steering_a_scout_that_reaches_its_destination_takes_the_next_and_none_is_ever_the_enemy_base_center(
    match_record,
):
    agent = slots_agent()
    ask(agent, base(tick=1000, units=[situations.unit(300, "e1", 24, 48), situations.unit(301, "e1", 24, 40)]))
    for tick in range(1005, 3000, 5):
        here = [tuple(cell) for cell in agent.ctx.scout_destinations]
        units = [situations.unit(300 + number, "e1", *cell, idle=True) for number, cell in enumerate(here)]
        ask(agent, base(tick=tick, units=units))

    events = destinations(match_record("scout_slot"))
    assert len(events) > 10 and {event["reason"] for event in events} == {"new", "reached"}
    assert all(not cells.is_within(tuple(event["cell"]), tuple(ENEMY_BASE), 9) for event in events)
    assert len({tuple(event["cell"]) for event in events}) > 8


def test_steering_a_scout_with_no_place_to_go_waits_at_the_staging_point_and_has_no_destination(monkeypatch):
    monkeypatch.setattr(scout_steering, "candidates", lambda *arguments: [])
    agent = slots_agent()

    orders = ask(agent, base(tick=1000, units=[situations.unit(300, "e1", 40, 70)]))
    there = ask(agent, base(tick=1005, units=[situations.unit(300, "e1", *STAGING)]))

    assert scout_moves(orders) == [{"cmd": "move", "units": [300], "to": STAGING}] and scout_moves(there) == []
    assert agent.ctx.scout_destinations == ()


@pytest.mark.parametrize(
    "tuning, heading_for, active",
    [
        (SLOTS, ((40, 84), (4, 4)), False),
        (SLOTS, ((40, 84), N_FLANK), True),
        (SLOTS, (), False),
        (tuning_off(), (), True),
    ],
    ids=["both far", "one at a flank 14 cells out", "none heading anywhere", "slots off, a scout out"],
)
def test_scout_slots_scout_active_only_while_a_slot_scout_heads_within_14_cells_of_the_enemy_base(
    tuning, heading_for, active
):
    agent = slots_agent(tuning)
    ask(agent, base(tick=1000, units=rifles(300, 301)))
    agent.ctx.groups.assign(300, SCOUT)
    agent.ctx.scout_destinations = heading_for

    situation = plans.observe(agent.world, agent.ctx, agent.ctx.tuning, None)

    assert agent.ctx.groups.members(SCOUT) and situation.scout_active is active


# -- steering the scouts with the wide berth ---------------------------------------------------------------------------

WIDE = dataclasses.replace(tuning_off(), scouting=scouting_off(scout_slots=True, scout_wide_berth=True))


@pytest.mark.parametrize("wide, berth", [(False, (3, 6, 40)), (True, (6, 10, 60))], ids=["off", "wide berth on"])
def test_wide_berth_the_margins_in_force_are_the_shipped_ones_off_and_the_wide_ones_on(wide, berth):
    tuning = scouting_off(scout_wide_berth=wide)

    in_force = (scout_berth.danger_margin_cells(tuning), scout_berth.flee_cells(tuning))

    assert (*in_force, scout_berth.release_below_hp(tuning)) == berth


def test_wide_berth_an_enemy_unit_in_sight_is_dangerous_within_its_range_and_6_cells():
    agent = slots_agent(WIDE)
    ask(agent, base(tick=1000, visible_enemies=[enemy(800, "2tnk", 40, 20)]))

    dangers = scout_steering.Dangers(agent.world, agent.ctx)

    # The 2tnk shoots 4.75 cells; with the wide margin of 6 its danger reaches 10.75 cells.
    assert (dangers.nearest((50, 20)), dangers.nearest((51, 20))) == (((40, 20), 0.0), ((40, 20), 0.25))


@pytest.mark.parametrize(
    "tank_cells, tuning, flees",
    [(17, WIDE, True), (17, SLOTS, False), (9, WIDE, True)],
    ids=["17 cells, wide berth on", "17 cells, off", "9 cells, wide berth on"],
)
def test_wide_berth_a_tank_whose_danger_ends_9_cells_off_gives_a_flee_10_cells_away_with_the_switch_on_and_none_off(
    tank_cells, tuning, flees
):
    agent = slots_agent(tuning)
    scouts = [situations.unit(300, "e1", 30, 20, idle=False), situations.unit(301, "e1", 18, 46, idle=False)]
    ask(agent, base(tick=1000, units=scouts))

    orders = ask(agent, base(tick=1005, units=scouts, visible_enemies=[enemy(800, "2tnk", 30 + tank_cells, 20)]))

    # The 2tnk shoots 4.75 cells. 17 cells off, its danger ends 9.25 cells from the scout with the shipped margin of 3,
    # beyond the flee distance of 6, and 6.25 cells with the wide margin of 6, within the wide flee distance of 10. A
    # tank 9 cells off is within the shipped flee distance too: its danger ends 1.25 cells from the scout.
    assert ({"cmd": "move", "units": [300], "to": [20, 20]} in orders) is flees


@pytest.mark.parametrize("tuning, released", [(WIDE, True), (SLOTS, False)], ids=["wide berth on", "off"])
def test_wide_berth_a_scout_at_55_hp_is_released_to_main_with_the_switch_on_and_kept_with_it_off(tuning, released):
    agent = slots_agent(tuning)
    jeep_scouts(agent)

    ask(agent, allied(*rifles(300, 301), situations.unit(400, "jeep", 40, 30, hp=55), jeep(401), tick=1010))

    groups = agent.ctx.groups
    assert (groups.group_of(400) == MAIN, groups.members(SCOUT) == [401]) == (released, released)


def scout_with(hp: int, cell: tuple[int, int] = (40, 30), unit_id: int = 300) -> Unit:
    """A scout of the world at a cell with the health given."""
    return Unit(id=unit_id, type="e1", x=cell[0], y=cell[1], hp=hp, idle=False, activity="move", stance=None)


def noted(areas: scout_berth.HurtAreas, scout: Unit, tick: int) -> bool:
    """Note a scout's health in a state, then keep it for the next, as the scout slots do; whether it was hurt."""
    was_hurt = areas.note(scout, tick, WIDE.scouting)
    areas.keep_health([scout])
    return was_hurt


def test_wide_berth_a_3_point_drop_records_no_hurt_area_and_a_5_point_drop_one_where_the_scout_stands():
    areas = scout_berth.HurtAreas()

    hurt = [noted(areas, scout_with(hp, cell), tick) for hp, cell, tick in HURT_STEPS]

    assert hurt == [False, False, True] and areas.live(1010, WIDE.scouting) == ((42, 30),)


# A scout's health and cell in three states: 3 points lost, then 5.
HURT_STEPS = [(100, (40, 30), 1000), (97, (41, 30), 1005), (92, (42, 30), 1010)]


def hurt_nine_times(areas: scout_berth.HurtAreas) -> None:
    """Have nine scouts each hurt once, the nth at (10 + n, 30) at tick 1000 + n."""
    for number in range(9):
        noted(areas, scout_with(100, unit_id=number), 999)
        noted(areas, scout_with(90, (10 + number, 30), unit_id=number), 1000 + number)


def test_wide_berth_a_9th_hurt_area_drops_the_oldest():
    areas = scout_berth.HurtAreas()

    hurt_nine_times(areas)

    assert areas.live(1008, WIDE.scouting) == tuple((10 + number, 30) for number in range(1, 9))


def test_wide_berth_a_hurt_area_is_avoided_for_1500_ticks_and_its_place_is_a_candidate_again_after():
    areas, agent = scout_berth.HurtAreas(), slots_agent(WIDE)
    ask(agent, base(tick=1000))
    noted(areas, scout_with(100, N_FLANK), 1000)
    noted(areas, scout_with(80, N_FLANK), 1005)

    places = [
        {candidate.cell for candidate in scout_targets.candidates(agent.world, agent.ctx, SightGrid(), (), hurt)}
        for hurt in (areas.live(2504, WIDE.scouting), areas.live(2505, WIDE.scouting))
    ]

    assert (areas.live(2504, WIDE.scouting), areas.live(2505, WIDE.scouting)) == ((N_FLANK,), ())
    assert [cell for cell in places[0] if cells.is_within(cell, N_FLANK, 10)] == [] and N_FLANK in places[1]


def test_wide_berth_a_way_past_a_hurt_area_on_the_straight_line_keeps_more_than_10_cells_from_it():
    world, ctx = world_and_ctx()
    hurt_at = (50, 20)

    straight = scout_steering.route(NORTH_START, NORTH_END, scout_steering.Dangers(world, ctx), world, ctx)
    way = scout_steering.route(NORTH_START, NORTH_END, scout_steering.Dangers(world, ctx, [hurt_at]), world, ctx)

    assert straight == [NORTH_END] and len(way) == 2 and way[-1] == NORTH_END
    assert min(cells.distance(cell, hurt_at) for cell in walked(way, NORTH_START)) > 10


def steered_for(agent, positions: dict[int, tuple[int, int]], hp: dict[int, int], ticks) -> set[tuple[int, int]]:
    """Ask the agent at each tick with each scout idle, at the health given, where the last move sent to it put it; every
    destination the scouts had after any of these states."""
    heading_for: set[tuple[int, int]] = set()
    for tick in ticks:
        units = [situations.unit(unit_id, "e1", *cell, hp=hp[unit_id]) for unit_id, cell in positions.items()]
        for order in scout_moves(ask(agent, base(tick=tick, units=units))):
            positions[order["units"][0]] = tuple(order["to"])
        heading_for.update(agent.ctx.scout_destinations)
    return heading_for


def near(places, cell: tuple[int, int]) -> list[tuple[int, int]]:
    """The places that lie within 10 cells of a cell."""
    return [place for place in places if cells.is_within(tuple(place), cell, 10)]


def slot_destinations(events: list[dict], slot: int) -> list[dict]:
    """The `destination` events of one slot."""
    return [event for event in destinations(events) if event["slot"] == slot]


def test_wide_berth_after_a_scout_is_hurt_at_a_cell_no_destination_within_10_cells_of_it_is_chosen_for_1500_ticks(
    match_record,
):
    agent, positions = slots_agent(WIDE), {300: N_FLANK, 301: (24, 40)}
    steered_for(agent, positions, {300: 100, 301: 100}, [1000])
    positions[300] = N_FLANK

    during = steered_for(agent, positions, {300: 80, 301: 100}, range(1005, 2505, 5))
    after = steered_for(agent, positions, {300: 80, 301: 100}, range(2505, 6000, 5))

    hurt = [event for event in match_record("scout_slot") if event["kind"] == "hurt"]
    assert hurt == [{"kind": "hurt", "slot": 1, "unit_id": 300, "unit_type": "e1", "cell": list(N_FLANK), "hp": 80}]
    assert len(during) > 5 and near(during, N_FLANK) == [] and near(after, N_FLANK)


def test_wide_berth_a_scout_whose_destination_lies_in_a_new_hurt_area_gets_a_new_destination_in_the_same_cycle(
    match_record,
):
    agent = slots_agent(WIDE)
    ask(agent, base(tick=1000, units=list(TWO_SCOUTS)))
    second = agent.ctx.scout_destinations[1]

    orders = ask(agent, base(tick=1005, units=[situations.unit(300, "e1", *second, hp=80), TWO_SCOUTS[1]]))

    first, changed = slot_destinations(match_record("scout_slot"), 2)
    assert (first["reason"], changed["reason"], near([changed["cell"]], second)) == ("new", "unsafe", [])
    assert agent.ctx.scout_destinations == (tuple(changed["cell"]),)
    assert [order["units"] for order in scout_moves(orders)] == [[300], [301]]


def test_wide_berth_off_a_scout_s_hp_drop_records_no_hurt_area_and_the_other_keeps_its_destination(match_record):
    agent = slots_agent(SLOTS)
    ask(agent, base(tick=1000, units=list(TWO_SCOUTS)))
    second = agent.ctx.scout_destinations[1]

    ask(agent, base(tick=1005, units=[situations.unit(300, "e1", *second, hp=80), TWO_SCOUTS[1]]))

    assert [event for event in match_record("scout_slot") if event["kind"] == "hurt"] == []
    assert second in agent.ctx.scout_destinations


def test_wide_berth_the_model_is_offered_no_place_within_10_cells_of_a_hurt_area():
    agent, chooser = slots_agent(WIDE), situations.StubChooser()
    ask(agent, base(tick=1000))
    dangers = scout_steering.Dangers(agent.world, agent.ctx, [N_FLANK])

    ScoutQuestion(chooser).ask(scout_unit(24, 48), [], SightGrid(), dangers, agent.world, agent.ctx)

    (request,) = chooser.submitted
    offered = [option_cell(request, option.id) for option in request.options]
    assert offered and near(offered, N_FLANK) == []


# -- a model's choice of the scouts' destinations ----------------------------------------------------------------------

TWO_SCOUTS = (situations.unit(300, "e1", 24, 48), situations.unit(301, "e1", 24, 40))
OPTION_CELL = re.compile(r"at \((\d+), (\d+)\)")


def choice_agent():
    """A slots agent whose two scout slots ask stub choosers, and the stubs."""
    choosers = (situations.StubChooser(), situations.StubChooser())
    return slots_agent(SLOTS, scout_choosers=choosers), choosers


def option_cell(request, option_id: str) -> tuple[int, int]:
    """The cell an option of a scout request names."""
    (description,) = [option.description for option in request.options if option.id == option_id]
    return tuple(int(part) for part in OPTION_CELL.search(description).groups())


def answer(option_id: str, outcome: str = USED) -> ChoiceResult:
    return ChoiceResult(option_id, None, "red_alert_scout", 40, outcome)


def test_scout_choice_with_a_stub_answering_option_3_the_scout_s_destination_is_option_3_s_cell_from_the_model(
    match_record,
):
    agent, (first, _) = choice_agent()
    ask(agent, base(tick=1000, units=list(TWO_SCOUTS)))
    third = option_cell(first.submitted[0], "3")
    first.results.append(answer("3"))

    ask(agent, base(tick=1005, units=list(TWO_SCOUTS)))

    assert agent.ctx.scout_destinations[0] == third
    model = [event for event in destinations(match_record("scout_slot")) if event["source"] == "model"]
    assert model == [
        {
            "kind": "destination",
            "slot": 1,
            "unit_id": 300,
            "unit_type": "e1",
            "cell": list(third),
            "source": "model",
            "reason": "answered",
        }
    ]


def test_scout_choice_with_a_stub_that_never_answers_the_destination_is_the_script_s_top_candidate_in_the_same_cycle(
    match_record,
):
    agent, (first, _) = choice_agent()

    orders = ask(agent, base(tick=1000, units=list(TWO_SCOUTS)))

    (request,) = first.submitted
    assert agent.ctx.scout_destinations[0] == option_cell(request, "1")
    assert [order["units"] for order in scout_moves(orders)] == [[300], [301]]
    assert {event["source"] for event in destinations(match_record("scout_slot"))} == {"script"}


@pytest.mark.parametrize("result", [answer("3", STALE_DISCARDED), answer("9"), answer(None, "timeout")])
def test_scout_choice_a_stale_or_unknown_or_missing_answer_is_not_used(result):
    agent, (first, _) = choice_agent()
    ask(agent, base(tick=1000, units=list(TWO_SCOUTS)))
    scripted = agent.ctx.scout_destinations
    first.results.append(result)

    ask(agent, base(tick=1005, units=list(TWO_SCOUTS)))

    assert agent.ctx.scout_destinations == scripted


def test_scout_choice_the_request_offers_at_most_6_places_with_seconds_unseen_and_what_was_last_seen_not_the_other_s():
    agent, (first, second) = choice_agent()

    ask(agent, base(tick=1000, units=list(TWO_SCOUTS), visible_enemy_buildings=[enemy(700, "powr", 78, 40)]))

    (request,) = second.submitted
    other = agent.ctx.scout_destinations[0]
    assert 1 <= len(request.options) <= 6 and [option.id for option in request.options][:2] == ["1", "2"]
    assert all(
        "unseen for" in option.description and "last seen there:" in option.description for option in request.options
    )
    assert all(not cells.is_within(option_cell(request, option.id), other, 12) for option in request.options)
    assert f"heading to: ({other[0]}, {other[1]})" in request.context and "Scout: e1 at (24, 40)" in request.context
    assert (request.reason_first, request.reason_words, request.max_answer_tokens) == (True, 20, 64)
    assert (request.deadline_ms, request.state_tag) == (1500, "1000")
    north = [option for option in first.submitted[0].options if option_cell(first.submitted[0], option.id) == N_FLANK]
    assert north and "buildings powr 1" in north[0].description


def test_scout_choice_one_question_at_a_time_and_again_once_the_pick_is_125_ticks_old():
    agent, (first, _) = choice_agent()
    ask(agent, base(tick=1000, units=list(TWO_SCOUTS)))
    while_in_flight = len(first.submitted)
    ask(agent, base(tick=1005, units=list(TWO_SCOUTS)))
    first.results.append(answer("1"))

    for tick in range(1010, 1125, 5):
        ask(agent, base(tick=tick, units=list(TWO_SCOUTS)))
    before = len(first.submitted)
    ask(agent, base(tick=1125, units=list(TWO_SCOUTS)))

    assert (while_in_flight, before, len(first.submitted)) == (1, 1, 2)


def test_scout_choice_with_the_scout_slots_off_no_scout_request_is_made():
    choosers = (situations.StubChooser(), situations.StubChooser())
    agent = slots_agent(tuning_off(), scout_choosers=choosers)

    sent_out(agent)
    for tick in range(1005, 1300, 5):
        ask(agent, base(tick=tick, units=[*guard(), rifle(30, 48)]))

    assert [chooser.submitted for chooser in choosers] == [[], []]


# -- the scout slots while the second yard's MCV is bought first --------------------------------------------------------

KENNEL = ("kenn", 14, 44)


def test_mcv_first_no_jeep_is_ordered_for_a_slot_while_the_mcv_is_due_first():
    agent = slots_agent()
    agent.ctx.mcv_first_due = True

    held = ask(agent, allied(*rifles(300, 301), tick=1000))
    agent.ctx.mcv_first_due = False
    freed = ask(agent, allied(*rifles(300, 301), tick=1005))

    assert trains(held) == [] and trains(freed) == [{"cmd": "train", "item": "jeep", "count": 2}]


@pytest.mark.parametrize(
    "tuning, more_buildings, ordered",
    [(FLAK_SLOTS, (), []), (SLOTS, (KENNEL,), [{"cmd": "train", "item": "dog", "count": 2}])],
    ids=["flak trucks wait", "dogs are trained"],
)
def test_mcv_first_a_soviet_flak_truck_upgrade_waits_for_the_mcv_and_a_dog_does_not(tuning, more_buildings, ordered):
    agent = slots_agent(tuning)
    agent.ctx.mcv_first_due = True

    orders = ask(agent, soviet(SOVIET_WAR_FACTORY, *more_buildings, units=rifles(300, 301)))

    assert trains(orders) == ordered


def test_mcv_first_the_vehicle_queue_claim_is_given_up_while_the_mcv_is_due_first():
    agent = slots_agent()
    ask(agent, allied(*rifles(300, 301), tick=1000))
    claimed = agent.ctx.claims.is_held_by_other("Vehicle", "upkeep")

    agent.ctx.mcv_first_due = True
    ask(agent, allied(*rifles(300, 301), tick=1005, production=[situations.job("Vehicle", "jeep")]))

    assert claimed and not agent.ctx.claims.is_held_by_other("Vehicle", "upkeep")


def test_mcv_first_during_a_full_hold_a_lost_scout_waits_and_once_every_slot_is_empty_rifles_are_ordered():
    agent = slots_agent()
    jeep_scouts(agent)
    agent.ctx.full_hold = True

    one_lost = ask(agent, allied(*rifles(300, 301), jeep(401), tick=1010))
    both_lost = ask(agent, allied(*rifles(300, 301), tick=1015))

    assert trains(one_lost) == [] and trains(both_lost) == [{"cmd": "train", "item": "e1", "count": 2}]


# -- scripted scout missions (scout_missions) ----------------------------------------------------------------------

MISSIONS = dataclasses.replace(tuning_off(), scouting=scouting_off(scout_slots=True, scout_missions=True))
# The fake arena's two starts, ours by the base at (22, 48); and the same map with a third start.
TWO_STARTS = {"rules": {"maps": {"fake-arena": {"bounds": [0, 0, 96, 96], "spawns": [[22, 48], [74, 48]]}}}}
THREE_STARTS = {"rules": {"maps": {"fake-arena": {"bounds": [0, 0, 96, 96], "spawns": [[22, 48], [80, 30], [48, 10]]}}}}
# Ore fields: one far from both bases, one by our yard, one by the enemy's start and one seen lately.
FAR_ORE, OUR_FIELD, THEIR_FIELD, SEEN_FIELD = (60, 80), (30, 52), (70, 40), (40, 14)
# The enemy main's edge: 14 cells from the enemy's start (74, 48) toward our base, 12 to the side; the lane's middle.
MAIN_EDGE_CELL, LANE_CELL = (60, 60), (48, 48)


def missions_agent(tick: int = 0, game_config=TWO_STARTS, **observation):
    """A slots agent with scout missions on, shown its base at a tick with the map's spawns, and the four ore fields."""
    agent = slots_agent(MISSIONS)
    ask(agent, base(tick=tick, **observation), situations.context(game_config))
    agent.ctx.terrain = Terrain([square_field(cell) for cell in (FAR_ORE, OUR_FIELD, THEIR_FIELD, SEEN_FIELD)])
    return agent


def sight_of(agent, seen: dict[int, tuple[int, int]]) -> SightGrid:
    """A sight grid that saw each cell given at the tick it is keyed by, by a unit standing on it."""
    sight = SightGrid()
    for tick, cell in sorted(seen.items()):
        ask(agent, base(tick=tick, units=[situations.unit(900, "e1", *cell)]))
        sight.observe(agent.world)
    return sight


def by_kind(found: list) -> dict[str, list[tuple]]:
    """The missions found by kind, each as its cell and priority."""
    kinds: dict[str, list[tuple]] = {}
    for mission in found:
        kinds.setdefault(mission.kind, []).append((mission.cell, mission.priority))
    return kinds


@pytest.mark.parametrize(
    "game_config, start", [(TWO_STARTS, (74, 48)), (THREE_STARTS, (74, 48))], ids=["two spawns", "three spawns"]
)
def test_scout_missions_the_enemy_start_is_known_at_tick_0_from_the_map_s_spawns(game_config, start):
    agent = missions_agent(0, game_config)

    assert agent.world.tick == 0 and scout_missions.enemy_start(agent.world) == start


def test_scout_missions_of_two_spawns_the_enemy_start_is_the_one_farther_from_our_base():
    agent = missions_agent(0, {"rules": {"maps": {"fake-arena": {"spawns": [[80, 30], [22, 48]]}}}})

    assert scout_missions.enemy_start(agent.world) == (80, 30)


def test_scout_missions_each_kind_is_found_at_its_cell_with_its_priority():
    agent = missions_agent(3000)
    sight = sight_of(agent, {2500: SEEN_FIELD})
    ask(agent, base(tick=3000))
    agent.ctx.repeat_mcv_site = (40, 80)

    found = by_kind(scout_missions.missions(agent.world, agent.ctx, sight, army_seen_tick=2000))

    # The far field was never seen; the one seen 20 s ago is not watched, nor the fields by either base.
    assert found["expansion_watch"] == [(FAR_ORE, 3)]
    # No enemy barracks or war factory is known; the army was seen 40 s ago.
    assert found["main_edge"] == [(MAIN_EDGE_CELL, 4)] and found["attack_lane"] == [(LANE_CELL, 1)]
    # Halfway between the yard (22, 48) and the further MCV's site (40, 80).
    assert found["expansion_approach"] == [((31, 64), 2)]


def test_scout_missions_the_priorities_rise_while_production_is_unknown_and_the_army_unseen_for_over_90_s():
    agent = missions_agent(5000, visible_enemy_buildings=[enemy(700, "tent", 80, 48)])

    known = by_kind(scout_missions.missions(agent.world, agent.ctx, SightGrid(), army_seen_tick=2749))
    fresh = by_kind(scout_missions.missions(agent.world, agent.ctx, SightGrid(), army_seen_tick=2750))

    assert known["main_edge"] == [(MAIN_EDGE_CELL, 2)]
    assert (known["attack_lane"], fresh["attack_lane"]) == ([(LANE_CELL, 3)], [(LANE_CELL, 1)])


@pytest.mark.parametrize(
    "seen_at, watched", [(None, True), (1499, True), (1500, False)], ids=["never", "just over 60 s", "60 s"]
)
def test_scout_missions_a_far_field_is_watched_never_seen_or_unseen_for_over_60_s(seen_at, watched):
    agent = missions_agent(3000)
    sight = sight_of(agent, {} if seen_at is None else {seen_at: FAR_ORE})
    ask(agent, base(tick=3000))

    found = by_kind(scout_missions.missions(agent.world, agent.ctx, sight, army_seen_tick=3000))

    assert ((FAR_ORE, 3) in found.get("expansion_watch", [])) is watched


def test_scout_missions_the_approach_is_to_the_reserved_field_and_none_without_a_next_yard():
    agent = missions_agent(1000)
    none = by_kind(scout_missions.missions(agent.world, agent.ctx, SightGrid(), army_seen_tick=1000))
    agent.ctx.reserved_field = square_field((50, 70))

    reserved = by_kind(scout_missions.missions(agent.world, agent.ctx, SightGrid(), army_seen_tick=1000))

    assert "expansion_approach" not in none and reserved["expansion_approach"] == [((36, 59), 2)]


def mission(kind: str, cell: tuple[int, int], priority: int, seen_tick: int | None = None) -> scout_missions.Mission:
    return scout_missions.Mission(kind, cell, priority, seen_tick)


@pytest.mark.parametrize("seen_tick, expected", [(None, 1.0), (2500, 0.5), (0, 1.0)], ids=["never", "60 s", "160 s"])
def test_scout_missions_the_gap_is_1_never_seen_else_the_seconds_unseen_over_120_at_most_1(seen_tick, expected):
    assert scout_missions.gap(mission("attack_lane", LANE_CELL, 1, seen_tick), 4000) == expected


def test_scout_missions_a_never_seen_field_30_s_away_scores_above_a_lane_seen_20_s_ago_whatever_its_priority():
    scouting = MISSIONS.scouting
    seen_20_s_ago = 3000 - 20 * 25
    field = scout_missions.score(mission("expansion_watch", FAR_ORE, 3), 30, 3000, scouting)
    lanes = [
        scout_missions.score(mission("attack_lane", LANE_CELL, p, seen_20_s_ago), 0, 3000, scouting) for p in (1, 3)
    ]

    # 3 x 1.0 / (30 + 10 + 1) against 1 (or 3) x 20/120 / (0 + 10 + 1).
    assert field == pytest.approx(3 / 41) and lanes == pytest.approx([1 / 6 / 11, 3 / 6 / 11])
    assert field > max(lanes)


def scout_at_lane_end(**observation):
    """A missions agent at tick 1000 with a jeep scout (speed 4) at (30, 48), and the jeep."""
    agent = missions_agent(1000, units=[situations.unit(400, "jeep", 30, 48)], **observation)
    return agent, agent.world.combat_units[400]


def test_scout_missions_the_lane_by_the_map_middle_is_offered_and_its_travel_is_the_way_over_the_speed():
    agent, jeep = scout_at_lane_end()
    lane = mission("attack_lane", LANE_CELL, 1)

    (option,) = scout_missions.options(
        jeep, [lane], scout_steering.Dangers(agent.world, agent.ctx), agent.world, agent.ctx
    )

    # 18 cells at 4 cells a second: 4.5 s, and 10 s to watch.
    assert option.legs == (LANE_CELL,) and option.score == pytest.approx(1 / (4.5 + 10 + 1))


def test_scout_missions_a_mission_whose_cell_or_way_is_dangerous_or_that_is_blacklisted_is_not_offered():
    covering = enemy(600, "pbox", 48, 52)
    agent, jeep = scout_at_lane_end(visible_enemy_buildings=[covering])
    offered = [
        mission("attack_lane", LANE_CELL, 1),
        mission("expansion_watch", FAR_ORE, 3),
        mission("main_edge", (60, 60), 4),
    ]
    dangers = scout_steering.Dangers(agent.world, agent.ctx)

    found = scout_missions.options(
        jeep, offered, dangers, agent.world, agent.ctx, blacklisted={("main_edge", (60, 60))}
    )

    assert [option.mission.kind for option in found] == ["expansion_watch"]
    assert all(not dangers.crosses(start, end) for start, end in zip([(30, 48), *found[0].legs], found[0].legs))


def option(cell: tuple[int, int], weight: float, kind: str = "expansion_watch") -> scout_missions.Option:
    """An option of a mission of a kind at a cell, worth `weight`."""
    return scout_missions.Option(mission(kind, cell, 1), (cell,), weight, weight)


def assigned_cells(*offered: list) -> list:
    return [None if chosen is None else chosen.mission.cell for chosen in scout_missions.assign(offered)]


# Two places 10 cells apart, which overlap, and one far from both.
NEAR_A, NEAR_B, FAR_C = (10, 10), (20, 10), (80, 80)


def test_scout_missions_a_pair_apart_worth_half_the_best_pair_close_together_is_taken_over_it():
    # Close together: (10 + 10) x 50% = 10; apart: 10 + 0 = 10, exactly half, and it wins the tie.
    assert assigned_cells([option(NEAR_A, 10)], [option(NEAR_B, 10), option(FAR_C, 0)]) == [NEAR_A, FAR_C]
    assert assigned_cells([option(NEAR_A, 10)], [option(NEAR_B, 10), option(FAR_C, 3)]) == [NEAR_A, FAR_C]


def test_scout_missions_two_scouts_go_within_15_cells_of_each_other_only_when_no_pair_apart_is_worth_half():
    # Each scout's other place lies within 15 cells of the other scout's best: the only pair apart is worth 2 of 20.
    first = [option(NEAR_A, 10), option((32, 10), 1)]
    second = [option(NEAR_B, 10), option((10, 22), 1)]

    assert assigned_cells(first, second) == [NEAR_A, NEAR_B]


def test_scout_missions_no_mission_is_given_twice_and_a_scout_with_none_offered_gets_none():
    same = option(FAR_C, 10)

    assert assigned_cells([same], [option(FAR_C, 9), option(NEAR_A, 1)]) == [FAR_C, NEAR_A]
    assert assigned_cells([], [option(NEAR_A, 5)]) == [None, NEAR_A]
    assert assigned_cells([same], [option(FAR_C, 9)]) == [FAR_C, None]


def test_scout_missions_three_scouts_are_assigned_three_missions_apart():
    places = [(10, 10), (50, 10), (90, 10), (10, 90)]
    offered = [[option(cell, weight) for cell, weight in zip(places, (4, 3, 2, 1))] for _ in range(3)]

    assert sorted(assigned_cells(*offered)) == sorted(places[:3])


# The jeep scout of slot 1 at (30, 48), and two places 12 cells from it: 3 s at its speed of 4, so that a place never
# seen scores its priority / (3 + 10 + 1).
JEEP_AT, SOUTH, NORTH = (30, 48), (30, 60), (30, 36)


def planned(agent, planner, scouts: dict[int, dict], offered: list, tick: int, **observation) -> dict:
    """Show the agent its base with the scouts (by slot) at a tick, and plan their missions among those offered."""
    ask(agent, base(tick=tick, units=list(scouts.values()), **observation))
    units = {slot: agent.world.combat_units[unit["id"]] for slot, unit in scouts.items()}
    return planner.plan(units, offered, scout_steering.Dangers(agent.world, agent.ctx), agent.world, agent.ctx)


def taken(plan: dict, slot: int = 1) -> tuple:
    """The cell of a slot's mission in a plan, and why it changed."""
    assignment = plan[slot]
    return None if assignment.option is None else assignment.option.mission.cell, assignment.change


def jeep_at(cell: tuple[int, int] = JEEP_AT, hp: int = 100) -> dict[int, dict]:
    return {1: situations.unit(400, "jeep", *cell, hp=hp)}


@pytest.mark.parametrize("own, other, kept", [(5, 6, True), (10, 13, False)], ids=["1.2 times", "1.3 times"])
def test_scout_missions_a_scout_keeps_its_mission_against_one_1_2_times_better_and_switches_for_1_3(own, other, kept):
    agent, planner = new_agent(tuning=MISSIONS), MissionPlanner()
    first = planned(agent, planner, jeep_at(), [mission("expansion_watch", SOUTH, own)], 1000)
    both = [mission("expansion_watch", SOUTH, own), mission("expansion_watch", NORTH, other)]

    second = planned(agent, planner, jeep_at(), both, 1005)

    assert taken(first) == (SOUTH, "new")
    assert taken(second) == ((SOUTH, None) if kept else (NORTH, "switched"))


def test_scout_missions_a_mission_the_scout_lost_health_on_is_not_given_to_any_scout_for_90_s():
    agent, planner = new_agent(tuning=MISSIONS), MissionPlanner()
    best, other = mission("expansion_watch", SOUTH, 10), mission("main_edge", NORTH, 1)

    plans = [
        planned(agent, planner, jeep_at(hp=hp), [best, other], tick)
        for tick, hp in ((1000, 100), (1005, 90), (1005 + 2249, 90), (1005 + 2250, 90))
    ]

    assert [taken(plan) for plan in plans] == [(SOUTH, "new"), (NORTH, "hurt"), (NORTH, None), (SOUTH, "switched")]


def test_scout_missions_a_scout_keeps_its_mission_until_it_has_watched_the_place_10_s_then_takes_the_next():
    agent, planner = new_agent(tuning=MISSIONS), MissionPlanner()
    there, next_place = mission("expansion_watch", SOUTH, 10), mission("main_edge", NORTH, 1)

    def seen_at(tick: int) -> list:
        """The missions as offered once the scout has the place in sight: the place, seen now, and the next."""
        return [mission("expansion_watch", SOUTH, 10, tick), next_place]

    plans = [planned(agent, planner, jeep_at(SOUTH), [there, next_place], 1000)]
    plans += [planned(agent, planner, jeep_at(SOUTH), seen_at(tick), tick) for tick in (1005, 1254, 1255)]

    assert [taken(plan) for plan in plans] == [(SOUTH, "new"), (SOUTH, None), (SOUTH, None), (NORTH, "reached")]


def test_scout_missions_a_mission_with_no_safe_way_left_ends_and_a_scout_that_must_flee_has_none():
    agent, planner = new_agent(tuning=MISSIONS), MissionPlanner()
    offered = [mission("expansion_watch", SOUTH, 10), mission("main_edge", NORTH, 1)]
    covering, tank = enemy(600, "pbox", 30, 64), enemy(610, "2tnk", 33, 48)

    plans = [
        planned(agent, planner, jeep_at(), offered, 1000),
        planned(agent, planner, jeep_at(), offered, 1005, visible_enemy_buildings=[covering]),
        planned(agent, planner, jeep_at(), offered, 1010, visible_enemies=[tank]),
    ]

    assert [taken(plan) for plan in plans] == [(SOUTH, "new"), (NORTH, "unsafe"), (None, "fled")]


def test_scout_missions_the_lane_s_priority_rises_once_no_enemy_army_has_been_in_sight_for_over_90_s():
    agent, planner = missions_agent(1000), MissionPlanner()

    def lane_priority(tick: int, **observation) -> int:
        ask(agent, base(tick=tick, **observation))
        found = planner.missions(agent.world, agent.ctx, SightGrid())
        return next(found_mission.priority for found_mission in found if found_mission.kind == "attack_lane")

    priorities = [lane_priority(1000), lane_priority(3250), lane_priority(3251)]
    priorities += [lane_priority(3255, visible_enemies=[enemy(610, "e1", 70, 20)]), lane_priority(3260)]

    assert priorities == [1, 1, 3, 1, 1]


def test_scout_missions_one_mission_goes_to_the_scout_it_is_worth_more_to_and_the_other_is_left_with_none():
    agent, planner = new_agent(tuning=MISSIONS), MissionPlanner()
    only = [mission("expansion_watch", SOUTH, 10)]
    far_jeep = {2: situations.unit(401, "jeep", 30, 90)}
    near_jeep = {1: situations.unit(400, "jeep", *SOUTH)}

    alone = planned(agent, planner, far_jeep, only, 1000)
    both = planned(agent, planner, {**near_jeep, **far_jeep}, only, 1005)

    # 10 / 11 for the jeep at the place against 1.25 x 10 / (7.5 + 11) for the far one that held it.
    assert taken(alone, 2) == (SOUTH, "new")
    assert (taken(both, 1), taken(both, 2)) == ((SOUTH, "new"), (None, "switched"))


def test_scout_missions_a_slot_whose_scout_is_gone_or_another_unit_starts_afresh():
    agent, planner = new_agent(tuning=MISSIONS), MissionPlanner()
    offered = [mission("expansion_watch", SOUTH, 10)]
    other_unit = {1: situations.unit(402, "jeep", *JEEP_AT)}

    plans = [planned(agent, planner, jeep_at(), offered, 1000), planned(agent, planner, {}, offered, 1005)]
    plans += [planned(agent, planner, jeep_at(), offered, 1010), planned(agent, planner, other_unit, offered, 1015)]

    assert [taken(plan) if plan else None for plan in plans] == [(SOUTH, "new"), None, (SOUTH, "new"), (SOUTH, "new")]


# The missions of the fake arena with no ore field known: the enemy main's edge and the lane, 17 cells apart.
MISSION_KINDS = {"expansion_watch", "main_edge", "expansion_approach", "attack_lane"}


def test_scout_missions_on_each_scout_is_sent_to_its_mission_and_each_mission_is_a_destination_event(match_record):
    agent = slots_agent(MISSIONS)

    orders = ask(agent, base(tick=1000, units=list(TWO_SCOUTS)))

    events = destinations(match_record("scout_slot"))
    assert sorted((event["mission"], tuple(event["cell"])) for event in events) == [
        ("attack_lane", LANE_CELL),
        ("main_edge", MAIN_EDGE_CELL),
    ]
    assert {(event["source"], event["reason"]) for event in events} == {("mission", "new")}
    assert all(event["score"] > 0 for event in events)
    assert sorted(agent.ctx.scout_destinations) == sorted([LANE_CELL, MAIN_EDGE_CELL])
    assert {tuple(order["to"]) for order in scout_moves(orders)} == {LANE_CELL, MAIN_EDGE_CELL}
    assert {entry.priority for entry in agent.ledger.entries} == {Priority.SCOUT}


def test_scout_missions_on_a_scout_at_its_mission_s_cell_stands_and_watches_and_writes_no_new_event(match_record):
    agent = slots_agent(MISSIONS)
    ask(agent, base(tick=1000, units=list(TWO_SCOUTS)))
    by_unit = {event["unit_id"]: tuple(event["cell"]) for event in destinations(match_record("scout_slot"))}
    there = [situations.unit(unit_id, "e1", *by_unit[unit_id]) for unit_id in (300, 301)]

    orders = ask(agent, base(tick=1005, units=there))

    assert scout_moves(orders) == [] and len(destinations(match_record("scout_slot"))) == 2


def test_scout_missions_on_a_scout_with_no_safe_mission_waits_at_the_staging_point_and_one_near_danger_flees():
    agent = slots_agent(MISSIONS)
    covering = [enemy(600, "pbox", 48, 52), enemy(601, "pbox", 60, 64)]
    tank = enemy(610, "2tnk", 21, 34)

    orders = ask(
        agent, base(tick=1000, units=list(TWO_SCOUTS), visible_enemy_buildings=covering, visible_enemies=[tank])
    )

    sent = {order["units"][0]: order["to"] for order in scout_moves(orders)}
    # 300 has no mission it can reach safely: both places are under a pillbox. 301 has the tank 6.7 cells off.
    assert sent[300] == STAGING and sent[301] != STAGING
    assert cells.distance(tuple(sent[301]), (21, 34)) > cells.distance((24, 40), (21, 34))


@pytest.mark.parametrize("tuning, asked", [(MISSIONS, False), (SLOTS, True)], ids=["missions on", "missions off"])
def test_scout_missions_on_no_scout_model_request_is_made(tuning, asked):
    choosers = (situations.StubChooser(), situations.StubChooser())
    agent = slots_agent(tuning, scout_choosers=choosers)

    for tick in range(1000, 1300, 5):
        ask(agent, base(tick=tick, units=list(TWO_SCOUTS)))

    assert any(chooser.submitted for chooser in choosers) is asked


def test_scout_missions_on_a_scout_whose_mission_turns_unsafe_with_none_left_gives_it_up_and_waits_at_staging():
    agent = slots_agent(MISSIONS)
    ask(agent, base(tick=1000, units=list(TWO_SCOUTS)))
    covering = [enemy(600, "pbox", 48, 52), enemy(601, "pbox", 60, 64)]

    orders = ask(agent, base(tick=1005, units=list(TWO_SCOUTS), visible_enemy_buildings=covering))

    assert agent.ctx.scout_destinations == ()
    assert {tuple(order["to"]) for order in scout_moves(orders)} == {tuple(STAGING)}


# -- exposed trucks (exposed_trucks) -------------------------------------------------------------------------------

EXPOSED = dataclasses.replace(tuning_off(), scouting=scouting_off(exposed_trucks=True))
# An ore field 30 cells from the enemy's start at (74, 48), and one 15 cells from it.
TRUCK_FIELD, FIELD_BY_THEIR_START = (50, 30), (74, 33)


def trucks_at(field: tuple[int, int], first_id: int = 700) -> list[dict]:
    """Two enemy harvesters at work at a 9-cell field: one on its center, one 3 cells off its east edge."""
    return [enemy(first_id, "harv", *field), enemy(first_id + 1, "harv", field[0] + 4, field[1] + 1)]


def rifles_by(field: tuple[int, int], count: int, off: int = 5) -> list[dict]:
    """`count` enemy rifles, worth 100 each, standing `off` cells south of a field's center."""
    return [enemy(710 + number, "e1", field[0], field[1] + off) for number in range(count)]


def trucks_seen(*enemies: dict, field=TRUCK_FIELD, buildings=(), tick: int = 1000) -> list:
    """The opportunities one state of the base with these enemies in sight gives with one 9-cell ore field."""
    world = World(situations.tracked_items())
    world.update(base(tick=tick, visible_enemies=list(enemies), visible_enemy_buildings=list(buildings)))
    return list(exposed_trucks.sighted(world, Terrain([square_field(field)]), EXPOSED).values())


def test_exposed_trucks_two_harvesters_at_a_field_30_cells_from_the_enemy_start_with_no_guard_are_one_of_count_2():
    # A third harvester 4 cells off the field's edge works no field.
    (found,) = trucks_seen(*trucks_at(TRUCK_FIELD), enemy(702, "harv", 50, 25))

    assert found == exposed_trucks.ExposedTrucks(
        field=TRUCK_FIELD,
        harvester_count=2,
        harvester_ids=(700, 701),
        guard_value=0,
        seen_tick=1000,
        nearest_harvester=(50, 30),
    )


# A pillbox 7 cells from the field's center covers it: its range of 6 and the margin of 2.
@pytest.mark.parametrize(
    "field, guard, buildings, count",
    [
        (TRUCK_FIELD, rifles_by(TRUCK_FIELD, 8), [], None),
        (TRUCK_FIELD, rifles_by(TRUCK_FIELD, 6), [], 2),
        (TRUCK_FIELD, rifles_by(TRUCK_FIELD, 8, off=10), [], 2),
        (TRUCK_FIELD, [], [enemy(720, "pbox", 50, 37)], None),
        (FIELD_BY_THEIR_START, [], [], None),
        ((74, 28), [], [], None),
        ((74, 27), [], [], 2),
    ],
    ids=[
        "an 800 guard",
        "a 600 guard",
        "800 9 cells off the field",
        "under a pillbox",
        "15 cells from the start",
        "20 cells from the start",
        "21 cells from the start",
    ],
)
def test_exposed_trucks_a_guard_over_600_a_pillbox_over_the_field_or_20_cells_or_less_from_the_start_give_none(
    field, guard, buildings, count
):
    found = trucks_seen(*trucks_at(field), *guard, field=field, buildings=buildings)

    assert [opportunity.harvester_count for opportunity in found] == ([] if count is None else [count])


def test_exposed_trucks_none_while_the_enemy_start_is_not_known():
    world = World(situations.tracked_items())

    assert exposed_trucks.sighted(world, Terrain([square_field(TRUCK_FIELD)]), EXPOSED) == {}


# A second ore field 33 cells from the enemy's start.
SECOND_TRUCK_FIELD = (50, 70)


def watched(*states: dict) -> list[list[tuple]]:
    """What one watch keeps after each of these states of the base, seen in turn by one world with the two truck
    fields: each kept opportunity's field, harvester count and tick, in the order given."""
    world, watch = World(situations.tracked_items()), exposed_trucks.ExposedTruckWatch()
    terrain = Terrain([square_field(TRUCK_FIELD), square_field(SECOND_TRUCK_FIELD)])
    kept = []
    for state in states:
        world.update(state)
        kept.append(
            [(each.field, each.harvester_count, each.seen_tick) for each in watch.observe(world, terrain, EXPOSED)]
        )
    return kept


def test_exposed_trucks_an_opportunity_expires_60_s_after_it_was_last_seen():
    seen = base(tick=1000, visible_enemies=trucks_at(TRUCK_FIELD))

    kept = watched(seen, base(tick=2499), base(tick=2500))

    assert kept == [[(TRUCK_FIELD, 2, 1000)], [(TRUCK_FIELD, 2, 1000)], []]


def test_exposed_trucks_harvesters_seen_again_renew_their_opportunity_with_what_is_seen_then():
    one_truck = trucks_at(TRUCK_FIELD)[:1]

    kept = watched(
        base(tick=1000, visible_enemies=trucks_at(TRUCK_FIELD)),
        base(tick=2400, visible_enemies=one_truck),
        base(tick=3899),
        base(tick=3900),
    )

    assert kept == [[(TRUCK_FIELD, 2, 1000)], [(TRUCK_FIELD, 1, 2400)], [(TRUCK_FIELD, 1, 2400)], []]


@pytest.mark.parametrize(
    "later",
    [{"visible_enemy_buildings": [enemy(720, "pbox", 50, 37)]}, {"visible_enemies": rifles_by(TRUCK_FIELD, 7)}],
    ids=["a pillbox seen over the field", "a 700 guard seen by it"],
)
def test_exposed_trucks_an_opportunity_is_dropped_once_a_defense_covers_its_field_or_its_guard_passes_600(later):
    kept = watched(base(tick=1000, visible_enemies=trucks_at(TRUCK_FIELD)), base(tick=1005, **later))

    assert kept == [[(TRUCK_FIELD, 2, 1000)], []]


def test_exposed_trucks_the_opportunities_are_kept_one_for_each_field_the_newest_first():
    kept = watched(
        base(tick=1000, visible_enemies=trucks_at(TRUCK_FIELD)),
        base(tick=1100, visible_enemies=trucks_at(SECOND_TRUCK_FIELD, first_id=740)),
    )

    assert kept[-1] == [(SECOND_TRUCK_FIELD, 2, 1100), (TRUCK_FIELD, 2, 1000)]


@pytest.mark.parametrize("switch, published", [(True, [(TRUCK_FIELD, 2, 1000)]), (False, [])], ids=["on", "off"])
def test_exposed_trucks_the_scouting_manager_publishes_the_opportunities_on_the_match_context_with_the_switch_on(
    switch, published
):
    agent = new_agent(ScoutingManager(), tuning=tuning_off(scouting=scouting_off(exposed_trucks=switch)))
    ask(agent, base(tick=995))
    # The terrain the match's first state brought is replaced by one with the truck field.
    agent.ctx.terrain = Terrain([square_field(TRUCK_FIELD)])

    ask(agent, base(tick=1000, visible_enemies=trucks_at(TRUCK_FIELD)))
    ask(agent, base(tick=1005))

    assert [(each.field, each.harvester_count, each.seen_tick) for each in agent.ctx.exposed_trucks] == published


def test_exposed_trucks_each_new_opportunity_writes_one_scout_slot_event_and_one_seen_again_while_kept_none(
    match_record,
):
    guarded = [*trucks_at(TRUCK_FIELD), *rifles_by(TRUCK_FIELD, 3)]

    watched(
        base(tick=1000, visible_enemies=guarded),
        base(tick=1005, visible_enemies=guarded),
        base(tick=2600, visible_enemies=trucks_at(TRUCK_FIELD)[1:]),
    )

    events = [event for event in match_record("scout_slot") if event["kind"] == "exposed_trucks"]
    first = {"field": [50, 30], "harvesters": 2, "guard_value": 300, "tick": 1000, "nearest_harvester": [50, 30]}
    again = {"field": [50, 30], "harvesters": 1, "guard_value": 0, "tick": 2600, "nearest_harvester": [54, 31]}
    assert events == [{"kind": "exposed_trucks", **first}, {"kind": "exposed_trucks", **again}]


# The staging point, 10 cells from the yard toward the enemy's start, and a harvester on no field 8 cells from it.
RAID_START, NEAR_HARVESTER = (32, 48), enemy(750, "harv", 40, 48)


@pytest.mark.parametrize("prefer, ranking", [(True, [700, 701, 750]), (False, [750, 700, 701])], ids=["on", "off"])
def test_exposed_trucks_the_raid_ranking_prefers_an_opportunity_s_harvester_with_prefer_exposed_trucks_on(
    prefer, ranking
):
    tuning = tuning_off(scouting=scouting_off(exposed_trucks=True), raids=raids_off(prefer_exposed_trucks=prefer))
    agent = new_agent(ScoutingManager(), tuning=tuning)
    ask(agent, base(tick=995))
    agent.ctx.terrain = Terrain([square_field(TRUCK_FIELD)])
    ask(agent, base(tick=1000, visible_enemies=[*trucks_at(TRUCK_FIELD), NEAR_HARVESTER]))

    targets = raid_targets.ranked(agent.world, RAID_START, [], tuning, agent.ctx.exposed_trucks)

    assert [target.id for target in targets] == ranking


# -- the Soviet infantry counter: the shipped Soviet scouts are dogs or rifles ------------------------------------

SHIPPED_SCOUTING = Tuning()
KENNEL = ("kenn", 14, 44)


def test_soviet_infantry_counter_with_a_kennel_the_shipped_slots_are_upgraded_to_dogs_not_flak_trucks():
    agent = slots_agent(SHIPPED_SCOUTING)

    ordered = ask(agent, soviet(SOVIET_WAR_FACTORY, KENNEL, tick=1000, units=rifles(300, 301)))

    assert trains(ordered) == [{"cmd": "train", "item": "dog", "count": 2}]


def test_soviet_infantry_counter_without_a_kennel_the_shipped_slots_keep_and_refill_rifles_never_a_flak_truck():
    agent = slots_agent(SHIPPED_SCOUTING)

    held = ask(agent, soviet(SOVIET_WAR_FACTORY, tick=1000, units=rifles(300, 301)))
    lost = ask(agent, soviet(SOVIET_WAR_FACTORY, tick=1010, units=rifles(301)))

    assert "ftrk" in agent.world.available_production and "dog" not in agent.world.available_production
    assert trains(held) == [] and trains(lost) == [{"cmd": "train", "item": "e1", "count": 1}]


# -- careful scouts: hold fire, retreat early, keep off the enemy's bases ---------------------------------------------

CAUTIOUS = dataclasses.replace(tuning_off(), scouting=scouting_off(scout_slots=True, scout_cautious=True))
CAUTIOUS_MISSIONS = dataclasses.replace(
    tuning_off(), scouting=scouting_off(scout_slots=True, scout_missions=True, scout_cautious=True)
)
HOLD_FIRE = {"cmd": "set_stance", "units": [300], "stance": "hold_fire"}
MAIN_STANCE = {"cmd": "set_stance", "units": [300], "stance": "attack_anything"}


def stances(orders: list[dict]) -> list[dict]:
    return situations.with_command(orders, "set_stance")


def retreats(events: list[dict]) -> list[dict]:
    return [event for event in events if event["kind"] == "retreat"]


def test_scout_cautious_a_new_scout_gets_one_hold_fire_order_and_a_released_one_gets_attack_anything_once_home():
    agent = slots_agent(CAUTIOUS)

    batches = [ask(agent, base(tick=tick, units=rifles(300, 301))) for tick in range(1000, 1100, 5)]
    released = ask(agent, base(tick=1100, units=[situations.unit(300, "e1", 18, 46, hp=35), *rifles(301)]))
    home = ask(agent, base(tick=1105, units=[situations.unit(300, "e1", 30, 47, hp=35), *rifles(301)]))

    assert [order for orders in batches for order in stances(orders)] == [HOLD_FIRE, {**HOLD_FIRE, "units": [301]}]
    assert stances(released) == [] and stances(home) == [MAIN_STANCE]
    assert agent.ctx.groups.members(SCOUT) == [301] and agent.ctx.groups.group_of(300) == MAIN


def test_scout_cautious_a_jeep_taking_over_holds_fire_and_the_rifle_it_replaces_gets_attack_anything():
    agent = slots_agent(CAUTIOUS)
    ask(agent, allied(*rifles(300, 301), tick=1000))
    ask(agent, allied(*rifles(300, 301), tick=1003, production=[situations.job("Vehicle", "jeep")] * 2))

    taken = ask(agent, allied(*rifles(300, 301), jeep(400), jeep(401), tick=1005))

    assert sorted((order["units"][0], order["stance"]) for order in stances(taken)) == [
        (300, "attack_anything"),
        (301, "attack_anything"),
        (400, "hold_fire"),
        (401, "hold_fire"),
    ]


def test_scout_cautious_a_scout_shown_in_another_stance_2_s_after_its_order_is_ordered_to_hold_fire_again():
    agent = slots_agent(CAUTIOUS)
    ask(agent, base(tick=1000, units=rifles(300, 301)))
    shown = [{**unit, "stance": "attack_anything"} for unit in rifles(300, 301)]

    early = ask(agent, base(tick=1045, units=shown))
    due = ask(agent, base(tick=1050, units=shown))
    held = ask(agent, base(tick=1100, units=[{**unit, "stance": "hold_fire"} for unit in rifles(300, 301)]))

    assert stances(early) == [] and stances(held) == []
    assert stances(due) == [HOLD_FIRE, {**HOLD_FIRE, "units": [301]}]


def test_scout_cautious_off_no_stance_order_is_given_to_a_scout():
    agent = slots_agent()

    batches = [ask(agent, base(tick=tick, units=rifles(300, 301))) for tick in range(1000, 1050, 5)]
    released = ask(agent, base(tick=1050, units=[situations.unit(300, "e1", 18, 46, hp=35), *rifles(301)]))

    assert [stances(orders) for orders in [*batches, released]] == [[]] * 11


# Where scout 300 stands, a rifle in sight 16 cells east of it (beyond scout_outclass_cells), and where a rifleman
# retreats to from it: 1.32 cells a second for 8 seconds, straight away from the rifle.
SCOUT_AT, FAR_RIFLE, RETREAT_TO = (30, 20), (46, 20), [19, 20]
OTHER_SCOUT = situations.unit(301, "e1", 18, 46, idle=False)


def hit_scout(agent, hp: int) -> list[dict]:
    """A slots agent's two scouts, 300 at SCOUT_AT on its way with 100 health and then `hp`, a rifle in sight at
    FAR_RIFLE in the second state; the orders of the second state."""
    ask(agent, base(tick=1000, units=[situations.unit(300, "e1", *SCOUT_AT, idle=False), OTHER_SCOUT]))
    hurt = situations.unit(300, "e1", *SCOUT_AT, idle=False, hp=hp)
    return ask(agent, base(tick=1005, units=[hurt, OTHER_SCOUT], visible_enemies=[enemy(800, "e1", *FAR_RIFLE)]))


def test_scout_cautious_a_scout_dropping_from_100_to_98_retreats_straight_away_from_the_enemy(match_record):
    agent = slots_agent(CAUTIOUS)

    orders = hit_scout(agent, 98)

    assert {"cmd": "move", "units": [300], "to": RETREAT_TO} in orders
    assert cells.distance(tuple(RETREAT_TO), FAR_RIFLE) > cells.distance(SCOUT_AT, FAR_RIFLE)
    (retreat,) = retreats(match_record("scout_slot"))
    assert (retreat["unit_id"], retreat["cause"], retreat["hp"]) == (300, "hit", 98)
    assert (retreat["cell"], retreat["to"], retreat["enemy"]) == (list(SCOUT_AT), RETREAT_TO, list(FAR_RIFLE))


def test_scout_cautious_a_retreat_lasts_8_s_with_one_event_and_the_cell_hit_on_becomes_a_hurt_area(match_record):
    agent = slots_agent(CAUTIOUS)
    hit_scout(agent, 98)
    standing = situations.unit(300, "e1", 20, 20, idle=True, hp=98)

    during = ask(agent, base(tick=1100, units=[standing, OTHER_SCOUT]))
    over = ask(agent, base(tick=1205, units=[standing, OTHER_SCOUT]))

    assert [order["to"] for order in scout_moves(during) if order["units"] == [300]] == [RETREAT_TO]
    # Then it is steered again, and flees the hurt area within 10 cells of SCOUT_AT: 6 cells straight away from it.
    assert [order["to"] for order in scout_moves(over) if order["units"] == [300]] == [[14, 20]]
    assert len(retreats(match_record("scout_slot"))) == 1


@pytest.mark.parametrize("tuning, hp", [(SLOTS, 98), (CAUTIOUS, 100)], ids=["switch off", "no drop"])
def test_scout_cautious_off_or_without_a_health_drop_a_scout_does_not_retreat(tuning, hp, match_record):
    orders = hit_scout(slots_agent(tuning), hp)

    assert {"cmd": "move", "units": [300], "to": RETREAT_TO} not in orders
    assert retreats(match_record("scout_slot")) == []


def jeep_near_rifles(agent, count: int) -> list[dict]:
    """A cautious agent's jeeps 400 and 401 holding the slots at (28, 48), then `count` enemy rifles in sight 7 cells
    east of them, the jeeps at full health; the orders of that state."""
    jeep_scouts(agent)
    sighted = [enemy(800 + number, "e1", 35, 48 + number) for number in range(count)]
    return ask(agent, allied(*rifles(300, 301), jeep(400), jeep(401), tick=1010, visible_enemies=sighted))


def test_scout_cautious_a_jeep_with_three_enemy_rifles_7_cells_away_retreats_unhurt_as_outclassed(match_record):
    agent = slots_agent(CAUTIOUS)

    orders = jeep_near_rifles(agent, 3)

    (retreat,) = [event for event in retreats(match_record("scout_slot")) if event["unit_id"] == 400]
    assert (retreat["cause"], retreat["hp"], retreat["enemy"]) == ("outclassed", 100, [35, 48])
    assert {"cmd": "move", "units": [400], "to": retreat["to"]} in orders
    assert cells.distance(tuple(retreat["to"]), (35, 48)) > cells.distance((28, 48), (35, 48))


def test_scout_cautious_a_jeep_with_two_enemy_rifles_7_cells_away_is_not_outclassed(match_record):
    agent = slots_agent(CAUTIOUS)

    jeep_near_rifles(agent, 2)

    assert retreats(match_record("scout_slot")) == []


def test_scout_cautious_the_retreats_count_within_the_2_scout_orders_of_a_batch():
    three = dataclasses.replace(CAUTIOUS, scouting=dataclasses.replace(CAUTIOUS.scouting, scout_slot_count=3))
    agent = slots_agent(three)
    scouts = [situations.unit(300 + number, "e1", 30, 20 + 4 * number, idle=False) for number in range(3)]
    ask(agent, base(tick=1000, units=scouts))

    orders = ask(agent, base(tick=1005, units=[{**scout, "hp": 90} for scout in scouts]))

    assert len(scout_moves(orders)) == 2


# A seen enemy building 5 cells off the straight way from NORTH_START to NORTH_END.
NEAR_BUILDING = (50, 25)


def base_ground_world(tuning: Tuning, building: str):
    """The world and context of a slots agent with the tuning given that has seen an enemy building at NEAR_BUILDING."""
    agent = slots_agent(tuning)
    ask(agent, base(tick=1000, visible_enemy_buildings=[enemy(600, building, *NEAR_BUILDING)]))
    return agent.world, agent.ctx


def test_scout_cautious_a_way_5_cells_from_a_seen_enemy_war_factory_is_routed_around_it():
    world, ctx = base_ground_world(CAUTIOUS, "weap")

    way = scout_steering.route(NORTH_START, NORTH_END, scout_steering.Dangers(world, ctx), world, ctx)

    assert way is not None and way != [NORTH_END] and way[-1] == NORTH_END
    assert min(cells.distance(cell, NEAR_BUILDING) for cell in walked(way, NORTH_START)) > 8


@pytest.mark.parametrize("tuning, building", [(SLOTS, "weap"), (CAUTIOUS, "sbag")], ids=["switch off", "a wall"])
def test_scout_cautious_off_or_by_a_wall_the_way_is_the_straight_line(tuning, building):
    world, ctx = base_ground_world(tuning, building)

    assert scout_steering.route(NORTH_START, NORTH_END, scout_steering.Dangers(world, ctx), world, ctx) == [NORTH_END]


def test_scout_cautious_a_scout_within_the_flee_distance_of_a_seen_enemy_building_s_ground_flees_it():
    world, ctx = base_ground_world(CAUTIOUS, "fact")
    dangers = scout_steering.Dangers(world, ctx)

    assert scout_steering.fled_from((50, 39), dangers, ctx) == NEAR_BUILDING
    assert scout_steering.fled_from((50, 40), dangers, ctx) is None


@pytest.mark.parametrize("tuning, moved", [(CAUTIOUS_MISSIONS, True), (MISSIONS, False)], ids=["on", "off"])
def test_scout_cautious_the_main_edge_viewing_point_is_moved_off_a_seen_enemy_building_toward_our_base(tuning, moved):
    agent = slots_agent(tuning)
    seen = [enemy(600, "weap", *MAIN_EDGE_CELL)]
    ask(agent, base(tick=0, visible_enemy_buildings=seen), situations.context(TWO_STARTS))

    found = scout_missions.missions(agent.world, agent.ctx, SightGrid(), 0)

    (edge,) = [mission.cell for mission in found if mission.kind == "main_edge"]
    if moved:
        # Past the building's 8 cells, a cell and the flee distance of 6, and no farther than the next cell.
        assert edge[1] == MAIN_EDGE_CELL[1] and edge[0] < MAIN_EDGE_CELL[0]
        assert 8 + 1 + 6 < cells.distance(edge, MAIN_EDGE_CELL) <= 8 + 1 + 6 + 1
    else:
        assert edge == MAIN_EDGE_CELL


class Proposes:
    """A manager that proposes the same orders in every cycle, at CONTACT, as one of the fight might for units near an
    enemy; placed ahead of the scouting manager, as base defense is."""

    name = "base_defense"

    def __init__(self, *orders: dict) -> None:
        self._orders = orders

    def intents(self, world, ctx) -> list[Intent]:
        return [Intent(Priority.CONTACT, dict(order)) for order in self._orders]


ATTACK = {"cmd": "attack", "units": [300, 302], "target": 800}
ATTACK_MOVE = {"cmd": "attack_move", "units": [301], "to": [40, 40]}


def attacks(orders: list[dict]) -> list[dict]:
    return [order for order in orders if order["cmd"] in ("attack", "attack_move")]


@pytest.mark.parametrize(
    "tuning, sent",
    [(CAUTIOUS, [{**ATTACK, "units": [302]}]), (SLOTS, [ATTACK_MOVE, ATTACK])],
    ids=["on", "off"],
)
def test_scout_cautious_a_slot_scout_gets_no_attack_or_attack_move_even_in_the_cycle_it_takes_its_slot(tuning, sent):
    agent = new_agent(Proposes(ATTACK, ATTACK_MOVE), ScoutingManager(), tuning=tuning)
    sighted = [enemy(800, "e1", 40, 40)]

    first = ask(agent, base(tick=1000, units=rifles(300, 301, 302), visible_enemies=sighted))
    later = ask(agent, base(tick=1005, units=rifles(300, 301, 302), visible_enemies=sighted))

    assert agent.ctx.groups.members(SCOUT) == [300, 301]
    assert attacks(first) == sent


def test_scout_cautious_no_later_cycle_gives_a_slot_scout_an_attack_either():
    agent = new_agent(Proposes(ATTACK, ATTACK_MOVE), ScoutingManager(), tuning=CAUTIOUS)
    sighted = [enemy(800, "e1", 40, 40)]

    batches = [
        ask(agent, base(tick=tick, units=rifles(300, 301, 302), visible_enemies=sighted)) for tick in (1000, 1005, 1010)
    ]

    assert {unit for orders in batches for order in attacks(orders) for unit in order["units"]} == {302}


# -- careful scouts: a released scout goes home first ------------------------------------------------------------------

# A scout leaves its slot below 60 hp with the wide berth on.
CAUTIOUS_WIDE = dataclasses.replace(
    tuning_off(), scouting=scouting_off(scout_slots=True, scout_cautious=True, scout_wide_berth=True)
)
# 40 cells east of the centre of the base at (22, 48), with two enemy rifles in sight beside it.
FAR_OUT = (62, 48)
RIFLES_BY_IT = [enemy(800, "e1", 64, 48), enemy(801, "e1", 64, 49)]
# What main's focus fire and base defense might propose for the jeep in every cycle.
JEEP_ATTACK = {"cmd": "attack", "units": [400], "target": 800}
JEEP_ATTACK_MOVE = {"cmd": "attack_move", "units": [400], "to": [64, 48]}


def jeep_at_hp(cell: tuple[int, int], hp: int, *, idle: bool = False) -> dict:
    return situations.unit(400, "jeep", *cell, hp=hp, idle=idle)


def released_jeep(agent, tick: int = 1010) -> list[dict]:
    """Two jeeps hold the slots; 400 is shown at 50 hp 40 cells out with enemy rifles beside it: the orders then."""
    jeep_scouts(agent)
    return ask(agent, allied(jeep_at_hp(FAR_OUT, 50), jeep(401), tick=tick, visible_enemies=RIFLES_BY_IT))


def returned(events: list[dict]) -> list[dict]:
    return [event for event in events if event["kind"] == "returned"]


def test_scout_cautious_a_jeep_released_at_50_hp_40_cells_out_goes_home_with_no_attack_order_with_enemies_in_sight(
    match_record,
):
    agent = slots_agent(CAUTIOUS_WIDE, Proposes(JEEP_ATTACK, JEEP_ATTACK_MOVE))
    released = released_jeep(agent)
    on_the_way = [
        ask(agent, allied(jeep_at_hp((62 - step, 48), 50), jeep(401), tick=tick, visible_enemies=RIFLES_BY_IT))
        for step, tick in enumerate(range(1015, 1060, 5))
    ]

    batches = [released, *on_the_way]
    assert {"cmd": "move", "units": [400], "to": STAGING} in released
    assert [order for orders in batches for order in attacks(orders) if 400 in order["units"]] == []
    assert [order for orders in batches for order in stances(orders) if order["units"] == [400]] == []
    assert agent.ctx.groups.group_of(400) == SCOUT and agent.ctx.returning_scouts == frozenset({400})
    assert returned(match_record("scout_slot")) == []


def test_scout_cautious_a_released_jeep_reaching_the_staging_point_joins_main_with_attack_anything(match_record):
    agent = slots_agent(CAUTIOUS_WIDE)
    released_jeep(agent)
    short = ask(agent, allied(jeep_at_hp((37, 48), 50), jeep(401), tick=1015))
    still_returning = agent.ctx.returning_scouts

    home = ask(agent, allied(jeep_at_hp((35, 49), 50), jeep(401), tick=1020))

    assert stances(short) == [] and still_returning == frozenset({400})
    assert {"cmd": "set_stance", "units": [400], "stance": "attack_anything"} in stances(home)
    assert agent.ctx.groups.group_of(400) == MAIN and agent.ctx.returning_scouts == frozenset()
    expected = {"kind": "returned", "slot": 1, "unit_id": 400, "unit_type": "jeep"}
    assert returned(match_record("scout_slot")) == [expected]


def test_scout_cautious_a_released_jeep_idle_short_of_the_staging_point_is_moved_there_again_2_s_after_its_move():
    agent = slots_agent(CAUTIOUS_WIDE)
    released_jeep(agent)

    early = ask(agent, allied(jeep_at_hp((50, 48), 50, idle=True), jeep(401), tick=1055))
    due = ask(agent, allied(jeep_at_hp((50, 48), 50, idle=True), jeep(401), tick=1060))

    home_move = {"cmd": "move", "units": [400], "to": STAGING}
    assert home_move not in early and home_move in due


def test_scout_cautious_off_a_jeep_released_at_35_hp_joins_main_at_once():
    agent = slots_agent(SLOTS)
    jeep_scouts(agent)

    released = ask(agent, allied(jeep_at_hp(FAR_OUT, 35), jeep(401), tick=1010))

    assert {"cmd": "move", "units": [400], "to": STAGING} in released
    assert agent.ctx.groups.group_of(400) == MAIN and agent.ctx.returning_scouts == frozenset()


def test_scout_cautious_base_defense_takes_a_returning_jeep_within_15_cells_of_a_major_threat_into_main_at_once():
    agent = new_agent(BaseDefenseManager(), ScoutingManager(), tuning=CAUTIOUS_WIDE)
    released_jeep(agent)
    raiders = [enemy(800 + number, "e1", 26, 40 + number) for number in range(8)]

    taken = ask(agent, allied(jeep_at_hp((26, 46), 50), jeep(401), tick=1015, visible_enemies=raiders))

    assert agent.ctx.threat_level == MAJOR_THREAT and agent.ctx.groups.group_of(400) == MAIN
    assert {"cmd": "set_stance", "units": [400], "stance": "attack_anything"} in stances(taken)
    assert any(400 in order["units"] for order in attacks(taken))


# -- scout jitter: keep-off ways, the flee memory, a flight between two dangers ------------------------------------

CAUTIOUS_WIDE_MISSIONS = dataclasses.replace(
    tuning_off(),
    scouting=scouting_off(scout_slots=True, scout_missions=True, scout_cautious=True, scout_wide_berth=True),
)
# A seen enemy war factory, and a mission cell 16 cells from it: 8 cells past its 8 cells of base ground, within the
# wide flee distance of 10 and a cell.
ENEMY_WAR_FACTORY, BY_BASE_GROUND = (60, 30), (60, 46)


def test_scout_jitter_a_mission_whose_only_way_passes_8_cells_from_enemy_base_ground_is_not_given():
    agent, planner = new_agent(tuning=CAUTIOUS_WIDE_MISSIONS), MissionPlanner()
    seen = [enemy(600, "weap", *ENEMY_WAR_FACTORY)]

    plan = planned(
        agent, planner, jeep_at(), [mission("expansion_watch", BY_BASE_GROUND, 10)], 1000, visible_enemy_buildings=seen
    )
    dangers = scout_steering.Dangers(agent.world, agent.ctx)
    way = scout_steering.route(JEEP_AT, BY_BASE_GROUND, dangers, agent.world, agent.ctx, keeps_from_center=False)

    assert cells.distance(BY_BASE_GROUND, ENEMY_WAR_FACTORY) - 8 == 8
    # A way that only keeps out of the base ground is there; the keep-off way the mission needs is not.
    assert way == [BY_BASE_GROUND] and taken(plan) == (None, None)


@pytest.mark.parametrize("tuning, given_back", [(CAUTIOUS_MISSIONS, False), (MISSIONS, True)], ids=["on", "off"])
def test_scout_jitter_a_scout_that_fled_is_not_given_the_mission_it_fled_back_within_60_s(tuning, given_back):
    agent, planner = new_agent(tuning=tuning), MissionPlanner()
    offered = [mission("expansion_watch", SOUTH, 10), mission("main_edge", NORTH, 1)]
    tank = enemy(610, "2tnk", 33, 48)

    plans = [planned(agent, planner, jeep_at(), offered, 1000)]
    plans += [planned(agent, planner, jeep_at(), offered, 1005, visible_enemies=[tank])]
    plans += [planned(agent, planner, jeep_at(), offered, tick) for tick in (1010, 1005 + 1499, 1005 + 1500)]

    assert [taken(plan) for plan in plans[:2]] == [(SOUTH, "new"), (None, "fled")]
    if given_back:
        assert taken(plans[2]) == (SOUTH, "fled")
    else:
        # The flight is 60 s (1,500 ticks) old at 2505: until then the mission fled is not offered.
        assert [taken(plan) for plan in plans[2:]] == [(NORTH, "fled"), (NORTH, None), (SOUTH, "switched")]


def test_scout_jitter_the_flee_memory_ends_once_the_scout_stands_at_its_new_mission_s_cell():
    agent, planner = new_agent(tuning=CAUTIOUS_MISSIONS), MissionPlanner()
    offered = [mission("expansion_watch", SOUTH, 10), mission("main_edge", NORTH, 1)]

    plans = [planned(agent, planner, jeep_at(), offered, 1000)]
    plans += [planned(agent, planner, jeep_at(), offered, 1005, visible_enemies=[enemy(610, "2tnk", 33, 48)])]
    plans += [planned(agent, planner, jeep_at(), offered, 1010), planned(agent, planner, jeep_at(NORTH), offered, 1015)]

    assert [taken(plan) for plan in plans[2:]] == [(NORTH, "fled"), (SOUTH, "switched")]


# Two seen enemy buildings 30 cells apart, and a scout 13 cells from the first: 5 cells from its base ground.
FIRST_DANGER, SECOND_DANGER, BETWEEN = (50, 20), (50, 50), (50, 33)


def flight_from_between(*buildings: tuple[int, int]):
    """The move of a scout at BETWEEN with the careful scouts on and the enemy buildings given seen, the dangers and
    the world and context."""
    agent = slots_agent(CAUTIOUS)
    seen = [enemy(600 + number, "weap", *cell) for number, cell in enumerate(buildings)]
    ask(agent, base(tick=1000, visible_enemy_buildings=seen))
    dangers = scout_steering.Dangers(agent.world, agent.ctx)
    move = scout_steering.steer_to_mission(
        scout_steering.Course(), scout_unit(*BETWEEN), dangers, agent.world, agent.ctx
    )
    return move, dangers, agent.ctx


def test_scout_jitter_a_flight_that_would_land_within_the_flee_distance_of_a_second_danger_goes_to_staging():
    alone, _, _ = flight_from_between(FIRST_DANGER)
    move, dangers, ctx = flight_from_between(FIRST_DANGER, SECOND_DANGER)

    # 6 cells straight away from the first is (50, 39): 3 cells from the second's base ground.
    assert alone.cell == (50, 39) and alone.urgency == scout_steering.FLEE_URGENCY
    assert dangers.is_near_other((50, 39), FIRST_DANGER, 6)
    assert move.cell == tuple(STAGING) and move.urgency == scout_steering.FLEE_URGENCY
    assert scout_steering.fled_from(move.cell, dangers, ctx) is None


# -- the enemy home field watch --------------------------------------------------------------------------------------

# A field 11 cells from the enemy's start at (74, 48), toward our base; the enemy yard on the start and a refinery.
HOME_FIELD = (63, 48)
ENEMY_HOME = [enemy(620, "fact", 74, 48), enemy(621, "proc", 66, 50)]


def home_field_watches(tick: int, sight: SightGrid, agent) -> list:
    """The enemy home field watches among the missions of the cycle at a tick."""
    ask(agent, base(tick=tick))
    found = scout_missions.missions(agent.world, agent.ctx, sight, army_seen_tick=tick)
    return [found_mission for found_mission in found if found_mission.kind == "enemy_home_field"]


def home_field_agent(*buildings: dict):
    """A careful missions agent that has seen the enemy buildings given, with HOME_FIELD its one ore field."""
    agent = slots_agent(CAUTIOUS_MISSIONS)
    ask(agent, base(tick=0, visible_enemy_buildings=list(buildings)), situations.context(TWO_STARTS))
    agent.ctx.terrain = Terrain([square_field(HOME_FIELD)])
    return agent


def test_home_field_a_field_11_cells_from_the_enemy_start_is_watched_from_a_cell_clear_of_base_ground_and_danger():
    agent = home_field_agent(*ENEMY_HOME)

    (watch,) = home_field_watches(1000, SightGrid(), agent)

    dangers = scout_steering.Dangers(agent.world, agent.ctx)
    assert cells.distance(HOME_FIELD, (74, 48)) == 11
    # Past the refinery's 8 cells of base ground, a cell and the flee distance of 6: 15.1 cells off it.
    assert watch.cell == (51, 48) and watch.priority == 2 and watch.seen_tick is None
    assert all(cells.distance(watch.cell, (b["x"], b["y"])) > 8 + 1 + 6 for b in ENEMY_HOME)
    assert (
        cells.distance(watch.cell, HOME_FIELD) <= 20
        and scout_steering.fled_from(watch.cell, dangers, agent.ctx) is None
    )


def test_home_field_ranks_below_the_expansion_watch():
    assert scout_missions.ENEMY_HOME_FIELD_PRIORITY < scout_missions.EXPANSION_WATCH_PRIORITY


def test_home_field_a_field_with_no_clear_cell_within_20_cells_is_not_watched():
    # A building on the line toward our base keeps every cell from the field out to 20 cells within its ground.
    agent = home_field_agent(*ENEMY_HOME, enemy(622, "powr", 45, 48))

    assert home_field_watches(1000, SightGrid(), agent) == []


def test_home_field_a_field_seen_is_not_watched_again_for_120_s():
    agent = home_field_agent(*ENEMY_HOME)
    sight = sight_of(agent, {1000: HOME_FIELD})

    watched = [home_field_watches(tick, sight, agent) for tick in (4000, 4001)]

    # 120 s is 3,000 ticks.
    assert watched[0] == [] and [watch.seen_tick for watch in watched[1]] == [1000]


def test_home_field_on_the_mission_is_a_destination_event_that_names_its_kind(match_record):
    agent = home_field_agent(*ENEMY_HOME)

    ask(agent, base(tick=1000, units=list(TWO_SCOUTS)))

    kinds = {event["mission"] for event in destinations(match_record("scout_slot"))}
    assert "enemy_home_field" in kinds


# -- the Allied jeep waits for 2 war factories or 300 s ----------------------------------------------------------------

JEEP_GATED = dataclasses.replace(
    tuning_off(), scouting=scouting_off(scout_slots=True, allied_fast_scout_after_seconds=300)
)
SECOND_WAR_FACTORY = ("weap", 34, 44)


def test_allied_jeep_delay_england_at_200_s_with_one_war_factory_orders_no_jeep_and_at_300_s_it_does():
    early, late = slots_agent(JEEP_GATED), slots_agent(JEEP_GATED)

    at_200_s = ask(early, allied(*rifles(300, 301), tick=5000))
    at_300_s = ask(late, allied(*rifles(300, 301), tick=7500))

    assert trains(at_200_s) == [] and trains(at_300_s) == [{"cmd": "train", "item": "jeep", "count": 2}]


def test_allied_jeep_delay_england_at_200_s_with_two_war_factories_orders_the_jeeps():
    agent = slots_agent(JEEP_GATED)

    ordered = ask(
        agent, base_state("england", *ALLIED_WITH_WAR_FACTORY, SECOND_WAR_FACTORY, tick=5000, units=rifles(300, 301))
    )

    assert trains(ordered) == [{"cmd": "train", "item": "jeep", "count": 2}]


def test_allied_jeep_delay_a_scout_lost_before_the_gate_opens_is_replaced_by_a_rifle_from_the_barracks():
    agent = slots_agent(JEEP_GATED)
    ask(agent, allied(*rifles(300, 301), tick=5000))

    lost = ask(agent, allied(*rifles(301), tick=5005))

    assert "jeep" in agent.world.available_production
    assert trains(lost) == [{"cmd": "train", "item": "e1", "count": 1}]


def test_allied_jeep_delay_russia_s_dogs_are_ordered_at_200_s_as_before():
    agent = slots_agent(JEEP_GATED)

    ordered = ask(agent, soviet(SOVIET_WAR_FACTORY, KENNEL, tick=5000, units=rifles(300, 301)))

    assert trains(ordered) == [{"cmd": "train", "item": "dog", "count": 2}]


# -- the scout slots under the factory-first opening ------------------------------------------------------------------

FACTORY_FIRST_SLOTS = dataclasses.replace(SLOTS, opening=opening_off(order=FACTORY_FIRST))
SOVIET_WITH_WAR_FACTORY = (YARD, POWER, ("barr", 22, 44), ("proc", 24, 39), ("weap", 15, 48))


def factory_first_slots_agent():
    """A scouting agent under the factory-first opening, its build under way."""
    agent = slots_agent(FACTORY_FIRST_SLOTS)
    agent.ctx.opening_build = OpeningBuild(steps=("powr", "tent", "proc", "weap", "proc", "fix"))
    return agent


def soviet_factory(*units: dict, tick: int = 1000, **observation) -> dict:
    """A Soviet base with a war factory and the units given."""
    return base_state("russia", *SOVIET_WITH_WAR_FACTORY, tick=tick, units=list(units), **observation)


def test_scout_slots_factory_first_the_vehicle_queue_is_claimed_from_the_first_cycle_before_any_war_factory():
    agent = factory_first_slots_agent()

    orders = ask(agent, base(tick=1000))

    assert trains(orders) == [] and agent.ctx.claims.is_held_by_other("Vehicle", "upkeep")


@pytest.mark.parametrize(
    "state, vehicle",
    [(allied, "jeep"), (soviet_factory, "ftrk")],
    ids=["allies: a jeep before allied_fast_scout_after_seconds", "soviets: a flak truck with soviet_scout_unit dog"],
)
def test_scout_slots_factory_first_the_rifleman_holds_the_first_slot_and_the_vehicle_ordered_once_takes_the_second(
    state, vehicle, match_record
):
    agent = factory_first_slots_agent()
    ask(agent, base(tick=1000, units=rifles(300)))

    ordered = ask(agent, state(*rifles(300), tick=1005))
    waiting = ask(agent, state(*rifles(300), tick=1010, production=[situations.job("Vehicle", vehicle)]))
    ask(agent, state(*rifles(300), situations.unit(400, vehicle, 28, 48), tick=1015))

    assert trains(ordered) == [{"cmd": "train", "item": vehicle, "count": 1}] and trains(waiting) == []
    assert agent.ctx.groups.members(SCOUT) == [300, 400]
    assert not agent.ctx.claims.is_held_by_other("Vehicle", "upkeep")
    taken = [event for event in match_record("scout_slot") if event["kind"] in ("assigned", "replaced", "upgraded")]
    assert [(event["kind"], event["slot"], event["unit_type"]) for event in taken] == [
        ("assigned", 1, "e1"),
        ("assigned", 2, vehicle),
    ]


def test_scout_slots_factory_first_infantry_takes_no_slot_but_the_first_while_the_vehicle_is_to_come():
    agent = factory_first_slots_agent()
    ask(agent, base(tick=1000, units=rifles(300)))

    ask(agent, base(tick=1005, units=rifles(300, 301)))

    assert agent.ctx.groups.members(SCOUT) == [300] and agent.ctx.groups.group_of(301) != SCOUT


def test_scout_slots_factory_first_a_rifleman_lost_before_the_vehicle_is_not_replaced_and_the_vehicle_takes_its_slot(
    match_record,
):
    agent = factory_first_slots_agent()
    ask(agent, base(tick=1000, units=rifles(300)))

    lost = ask(agent, base(tick=1600))
    ordered = ask(agent, allied(tick=1605))
    ask(agent, allied(jeep(400), tick=1610))

    assert trains(lost) == [] and trains(ordered) == [{"cmd": "train", "item": "jeep", "count": 1}]
    assert agent.ctx.groups.members(SCOUT) == [400]
    kinds = [(event["kind"], event["slot"]) for event in match_record("scout_slot") if event["kind"] != "destination"]
    assert kinds == [("assigned", 1), ("lost", 1), ("replaced", 1)]


def test_scout_slots_factory_first_until_the_build_is_complete_no_rifleman_replaces_a_scout_and_after_it_one_does():
    agent = factory_first_slots_agent()
    ask(agent, base(tick=1000, units=rifles(300)))
    ask(agent, allied(*rifles(300), jeep(400), tick=1005))

    under_way = ask(agent, base(tick=1600, units=[jeep(400)]))
    agent.ctx.opening_build = OpeningBuild()
    complete = ask(agent, base(tick=1605, units=[jeep(400)]))

    assert trains(under_way) == [] and trains(complete) == [{"cmd": "train", "item": "e1", "count": 1}]


def test_scout_slots_barracks_first_orders_no_vehicle_for_a_slot_during_the_opening_s_build_as_before():
    agent = slots_agent()
    agent.ctx.opening_build = OpeningBuild(steps=("proc",))
    ask(agent, base(tick=1000, units=rifles(300)))

    orders = ask(agent, allied(*rifles(300), tick=1005))

    assert trains(orders) == [] and not agent.ctx.claims.is_held_by_other("Vehicle", "upkeep")


# -- the scout slots while the opening holds infantry back (single_scout_rifle) -------------------------------------

HOLD_SLOTS = dataclasses.replace(SLOTS, opening=opening_off(single_scout_rifle=True))


def held_slots_agent(order: str = "barracks_first"):
    """A scouting agent while the opening holds every infantry but its scout rifleman back, under an opening order
    whose build is under way under factory_first."""
    agent = slots_agent(dataclasses.replace(HOLD_SLOTS, opening=opening_off(order=order, single_scout_rifle=True)))
    agent.ctx.scout_rifle_hold = True
    if order == FACTORY_FIRST:
        agent.ctx.opening_build = OpeningBuild(steps=("powr", "tent", "proc", "weap", "proc", "fix"))
    return agent


def test_scout_slots_hold_a_second_rifle_takes_no_slot():
    agent = held_slots_agent()
    ask(agent, base(tick=1000, units=rifles(300)))

    ask(agent, base(tick=1005, units=rifles(300, 301)))

    assert agent.ctx.groups.members(SCOUT) == [300] and agent.ctx.groups.group_of(301) != SCOUT


@pytest.mark.parametrize("order", ["barracks_first", FACTORY_FIRST])
def test_scout_slots_hold_the_rifleman_lost_at_150_s_is_replaced_once_and_a_second_loss_is_not(order, match_record):
    agent = held_slots_agent(order)
    ask(agent, base(tick=1000, units=rifles(300)))

    lost = ask(agent, base(tick=3750))
    ask(agent, base(tick=3800, units=rifles(301)))
    lost_again = ask(agent, base(tick=5000))

    assert trains(lost) == [{"cmd": "train", "item": "e1", "count": 1}] and trains(lost_again) == []
    kinds = [(event["kind"], event["slot"]) for event in match_record("scout_slot") if event["kind"] != "destination"]
    assert kinds == [("assigned", 1), ("lost", 1), ("replaced", 1), ("lost", 1)]


def test_scout_slots_hold_a_rifleman_lost_after_the_rush_watch_ends_is_not_replaced_by_a_rifle():
    agent = held_slots_agent()
    ask(agent, base(tick=1000, units=rifles(300)))

    lost = ask(agent, base(tick=7600))

    assert trains(lost) == []


def test_scout_slots_hold_the_second_slot_never_held_takes_the_jeep_once_it_is_open():
    agent = held_slots_agent()
    ask(agent, base(tick=1000, units=rifles(300)))

    without = ask(agent, base(tick=5000, units=rifles(300)))
    with_war_factories = ask(agent, allied(*rifles(300), tick=5005))
    ask(agent, allied(*rifles(300), jeep(400), jeep(401), tick=5010))

    assert trains(without) == [] and trains(with_war_factories) == [{"cmd": "train", "item": "jeep", "count": 2}]
    assert agent.ctx.groups.members(SCOUT) == [400, 401]


def test_scout_slots_hold_released_the_second_slot_takes_the_next_rifle_as_before():
    agent = held_slots_agent()
    ask(agent, base(tick=1000, units=rifles(300)))
    agent.ctx.scout_rifle_hold = False

    ask(agent, base(tick=1005, units=rifles(300, 301)))

    assert agent.ctx.groups.members(SCOUT) == [300, 301]
