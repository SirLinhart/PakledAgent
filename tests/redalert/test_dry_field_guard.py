"""The dry-field guard (`[upkeep] dry_field_guard`): a mined field is never dry, a truck back from a retreat is no sign
of one, and no truck goes to a field no refinery of ours serves; and the harvester crush leaves the trucks to their
retreat (`[base_defense] crush_respects_retreat`)."""

from __future__ import annotations

import dataclasses

import situations
from situations import ask, base_state
from test_upkeep import (
    BY_THE_REFINERY,
    EAST_ORE,
    FRESH_ORE,
    HOME_FIELD,
    TWO_REFINERIES,
    fresh_agent,
    fresh_run,
    idle_by_the_refinery,
)
from ucla_agent.games.redalert import harvester_crush
from ucla_agent.games.redalert.fresh_ore import FreshOre
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import BaseDefenseTuning, Tuning, UpkeepTuning

GUARDED = dataclasses.replace(FRESH_ORE, upkeep=dataclasses.replace(FRESH_ORE.upkeep, dry_field_guard=True))
MINED_HOME = OrePatch(center=HOME_FIELD.center, cells=HOME_FIELD.cells, mines=2)
TICKS = [4000, 4005, 4010, 4015, 4020]
TWO_IDLE = idle_by_the_refinery((300, 301), busy=(302, 303))


def terrain_of(*patches: OrePatch) -> Terrain:
    return Terrain(patches, frozenset().union(*(patch.cells for patch in patches)))


def moves(sent: list[list[dict]]) -> list[dict]:
    """The harvest orders with a `to`: the moves to another field."""
    return [order for orders in sent for order in orders if "to" in order]


def test_without_the_guard_the_trucks_leave_for_a_field_no_refinery_of_ours_serves():
    agent = fresh_agent(terrain_of(HOME_FIELD, EAST_ORE))

    assert moves(fresh_run(agent, TICKS, TWO_IDLE)) == [
        {"cmd": "harvest", "units": [300, 301], "to": list(EAST_ORE.nearest_cell(BY_THE_REFINERY))}
    ]


def test_with_the_guard_no_truck_goes_to_an_unserved_field():
    agent = fresh_agent(terrain_of(HOME_FIELD, EAST_ORE), tuning=GUARDED)

    assert moves(fresh_run(agent, TICKS, TWO_IDLE)) == []


def guarded_reorders(home: OrePatch, fleeing: frozenset[int] = frozenset()) -> frozenset[OrePatch]:
    """The fields marked dry after two sends back to harvest of the two idle trucks, 5 s apart."""
    agent = fresh_agent(terrain_of(home, EAST_ORE), tuning=GUARDED)
    fresh = FreshOre()
    for tick in (4000, 4125):
        ask(agent, base_state("england", *TWO_REFINERIES, tick=tick, units=TWO_IDLE))
        agent.ctx.fleeing_harvesters = fleeing if tick == 4000 else frozenset()
        fresh.note_reorders([300, 301], agent.world, agent.ctx)
    return fresh.dry_fields


def test_a_field_without_a_mine_is_marked_dry_and_one_with_a_mine_never():
    assert guarded_reorders(HOME_FIELD) == {HOME_FIELD}
    assert guarded_reorders(MINED_HOME) == frozenset()


def test_trucks_back_from_a_retreat_within_10_s_are_no_sign_of_a_dry_field():
    assert guarded_reorders(HOME_FIELD, fleeing=frozenset({300, 301})) == frozenset()


# -- the harvester crush and the retreat ---------------------------------------------------------------------------


def crush_world(enemies: list[dict], fleeing: frozenset[int] = frozenset(), tuning: Tuning = Tuning()):
    agent = situations.new_agent(tuning=tuning)
    trucks = [situations.unit(264, "harv", 28, 44), situations.unit(265, "harv", 20, 40)]
    ask(agent, base_state("england", *TWO_REFINERIES, tick=4000, units=trucks, visible_enemies=enemies))
    agent.ctx.fleeing_harvesters = fleeing
    return agent


def foes(*types: str) -> list[dict]:
    return [{"id": 900 + n, "type": kind, "x": 31, "y": 45 + n % 2, "hp": 100} for n, kind in enumerate(types)]


def test_the_crush_takes_no_retreating_truck():
    agent = crush_world(foes("e1", "e1"), fleeing=frozenset({264, 265}))

    assert harvester_crush._nearest_fit_harvester(_cluster(agent), agent.world, agent.ctx) is None


def test_the_crush_runs_over_no_group_a_truck_must_retreat_from():
    agent = crush_world(foes("e1", "e2", "e3", "e3"))

    assert harvester_crush._start(agent.world, agent.ctx) is None


def _cluster(agent):
    from ucla_agent.games.redalert.crush import infantry_in_sight, largest_cluster

    return largest_cluster(infantry_in_sight(agent.world), agent.ctx.tuning.combat.crush_cluster_cells)


def test_tuning_guards_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT), Tuning()):
        assert tuning.upkeep.dry_field_guard is True and tuning.base_defense.crush_respects_retreat is True
    assert UpkeepTuning().dry_field_guard and BaseDefenseTuning().crush_respects_retreat
