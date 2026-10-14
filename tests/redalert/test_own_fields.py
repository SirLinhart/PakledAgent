"""Our own ore fields: no scout mission, rescout, scout place or commander option goes to a field by our buildings."""

from __future__ import annotations

import dataclasses

import pytest

import situations
from situations import ask, base_state
from switches_off import new_agent, scouting_off, tuning_off
from ucla_agent.games.redalert import command_scout, own_fields, scout_missions, scout_targets
from ucla_agent.games.redalert.rescout import StaleIntel
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.sight import SightGrid
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.world import World

YARD = ("fact", 22, 48)
POWER = ("powr", 19, 52)
BARRACKS = ("tent", 18, 44)
TWO_STARTS = {"rules": {"maps": {"fake-arena": {"bounds": [0, 0, 96, 96], "spawns": [[22, 48], [74, 48]]}}}}
MISSIONS = dataclasses.replace(tuning_off(), scouting=scouting_off(scout_slots=True, scout_missions=True))
RESCOUT = dataclasses.replace(tuning_off(), scouting=scouting_off(rescout=True))
# A field far from both starts: 34 cells from our yard at (22, 48), 32 from the enemy's at (74, 48).
FAR_ORE = (60, 80)
# Our refinery 5 cells from the field's center, a second yard 8 cells from it, and a building 20 cells off.
REFINERY_BY_IT = ("proc", 55, 80)
SECOND_YARD_BY_IT = ("fact", 60, 72)
FAR_POWER = ("powr", 40, 80)
REACH = MISSIONS.scouting.own_field_cells


def square_field(center: tuple[int, int]) -> OrePatch:
    """An ore field of the nine cells around a middle."""
    return OrePatch(center, frozenset((center[0] + dx, center[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)))


def world_with(*buildings: tuple[str, int, int], tick: int = 3000) -> World:
    world = World(situations.tracked_items())
    world.update(base_state("england", YARD, POWER, BARRACKS, *buildings, tick=tick))
    return world


@pytest.mark.parametrize(
    "building, ours",
    [(REFINERY_BY_IT, True), (SECOND_YARD_BY_IT, True), (FAR_POWER, False), (("powr", 60, 94), False)],
    ids=["refinery 5 cells off", "second yard 8 cells off", "power plant 20 cells off", "power plant 14 cells off"],
)
def test_a_field_is_ours_with_one_of_our_buildings_within_12_cells_of_its_center_or_ore(building, ours):
    field = square_field(FAR_ORE)

    assert own_fields.is_own(field, world_with(building), REACH) is ours
    assert (own_fields.not_own([field], world_with(building), REACH) == []) is ours


def test_a_building_within_12_cells_of_the_field_s_nearest_ore_cell_makes_it_ours_though_its_center_is_farther():
    # Ore from (60, 80) to (60, 95): the center (60, 87); the building 13 cells from it, 11 from the nearest ore cell.
    long_field = OrePatch((60, 87), frozenset((60, y) for y in range(80, 96)))

    assert own_fields.is_own(long_field, world_with(("powr", 60, 69)), REACH)


def missions_agent(*buildings: tuple[str, int, int], tick: int = 3000):
    """A slots agent with scout missions on, shown its base with the buildings given at a tick, and the far field."""
    agent = new_agent(ScoutingManager(), tuning=MISSIONS)
    state = base_state("england", YARD, POWER, BARRACKS, *buildings, tick=tick)
    ask(agent, state, situations.context(TWO_STARTS))
    agent.ctx.terrain = Terrain([square_field(FAR_ORE)])
    return agent


def watches(agent) -> list[tuple[int, int]]:
    found = scout_missions.missions(agent.world, agent.ctx, SightGrid(), army_seen_tick=agent.world.tick)
    return [mission.cell for mission in found if mission.kind == "expansion_watch"]


@pytest.mark.parametrize(
    "buildings, watched",
    [((), True), ((REFINERY_BY_IT,), False), ((SECOND_YARD_BY_IT,), False), ((FAR_POWER,), True)],
    ids=["no building", "our refinery 5 cells off", "our second yard 8 cells off", "a building 20 cells off"],
)
def test_a_field_of_ours_never_seen_by_a_unit_for_300_s_gets_no_expansion_watch(buildings, watched):
    agent = missions_agent(*buildings, tick=7500)

    assert (FAR_ORE in watches(agent)) is watched


@pytest.mark.parametrize("buildings, stale", [((), "stale_field"), ((REFINERY_BY_IT,), None)], ids=["far", "ours"])
def test_a_field_of_ours_unseen_for_300_s_is_never_stale_for_the_rescout(buildings, stale):
    intel = StaleIntel()
    terrain = Terrain([OrePatch(FAR_ORE, frozenset({FAR_ORE, (61, 80)}))])
    # Our harvester by the enemy's start keeps the base area in sight, and an enemy rifle afield the army.
    harvester = situations.unit(400, "harv", 66, 48)
    rifle = {"id": 800, "type": "e1", "x": 50, "y": 20, "hp": 100}
    reasons = []
    for tick in (1000, 8500):
        world = World(situations.tracked_items())
        world.update(
            base_state(
                "england", YARD, POWER, BARRACKS, *buildings, tick=tick, units=[harvester], visible_enemies=[rifle]
            )
        )
        intel.observe(world)
        reasons.append(intel.reason(world, terrain, RESCOUT.scouting))

    assert reasons == [None, stale]


@pytest.mark.parametrize("buildings, offered", [((), True), ((REFINERY_BY_IT,), False)], ids=["far", "ours"])
def test_a_field_of_ours_is_no_scout_place_and_no_commander_field(buildings, offered):
    agent = missions_agent(*buildings)
    places = [
        cell for cell, kind, _ in scout_targets._places(agent.world, agent.ctx) if kind == scout_targets.ORE_FIELD
    ]
    fields = [patch.center for patch in command_scout._watched_fields(agent.world, agent.ctx)]

    assert (FAR_ORE in places) is offered and (FAR_ORE in fields) is offered
