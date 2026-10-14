"""The world model: what `World.update` makes of each state, checked on the fixture states and on the fake game."""

from __future__ import annotations

import builtins
import copy
import dataclasses
import json
import socket
import time
from pathlib import Path

import pytest

from altruagent import WAIT
from fake_game import FIRST_ACTOR_ID, MAP_ID, FakeRedAlert
from ucla_agent.games.redalert import targeting
from ucla_agent.games.redalert.fight import centroid, estimate, ratio_against, ratio_against_target
from ucla_agent.games.redalert.fight import worth as fight_worth
from ucla_agent.games.redalert.items import ITEMS_FILE, ItemTable
from ucla_agent.games.redalert.scout_report import EnemyIntel, is_combat_unit
from ucla_agent.games.redalert.tuning import ScoutingTuning, Tuning, WorldTuning
from ucla_agent.games.redalert.world import (
    Building,
    CargoUnit,
    Economy,
    EnemyRecord,
    Military,
    ProductionItem,
    Unit,
    World,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
STATES_FOLDER = REPO_ROOT / "tests" / "fixtures" / "redalert" / "states"
FIXTURE_NAMES = ("opening_mcv", "midgame_allies", "midgame_soviet", "base_under_attack", "wreck", "events_gap")
# The size of state the update must handle within its time limit.
LARGE_STATE_UNITS = 150
LARGE_STATE_BUILDINGS = 40
LARGE_STATE_ENEMIES = 60
MAX_UPDATE_SECONDS = 0.010
TIMED_UPDATES = 30


def load_state(name: str) -> dict:
    """A fresh copy of the fixture state of that name."""
    return json.loads((STATES_FOLDER / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def items() -> ItemTable:
    """The item table of the tracked fallback file."""
    return ItemTable.from_file(REPO_ROOT)


@pytest.fixture(scope="module")
def game_config() -> dict:
    """The game reference object of the fake game, whose one map has the fixture states' map id."""
    return FakeRedAlert().game_config


@pytest.fixture
def world(items: ItemTable, game_config: dict) -> World:
    """An empty world for a match on the fake game's map."""
    return World(items, game_config)


def updated(world: World, name: str, **observation_changes) -> World:
    """The world after one update with a fixture state, the given observation fields replaced first."""
    state = load_state(name)
    state["observation"].update(observation_changes)
    world.update(state)
    return world


def own_unit(unit_id: int, unit_type: str, x: int = 20, y: int = 20, **fields) -> dict:
    return {
        "id": unit_id,
        "type": unit_type,
        "x": x,
        "y": y,
        "hp": 100,
        "act": None,
        "idle": True,
        "stance": "defend",
        **fields,
    }


# -- an empty world --------------------------------------------------------------------------------


def test_a_world_is_created_empty(world):
    assert (world.tick, world.game_seconds, world.remaining_seconds, world.phase) == (0, 0.0, None, None)
    assert (world.faction, world.enemy_faction, world.family) == (None, None, None)
    assert (world.map_bounds, world.base_center, world.enemy_base, world.spawns) == (None, None, None, ())
    assert (world.economy, world.military, world.trade_balance) == (Economy(), Military(), 0)
    assert (world.units, world.buildings, world.combat_units, world.production) == ({}, {}, {}, {})
    assert (world.available_production, world.ready_to_place) == (frozenset(), ())
    assert (world.events_new, world.events_gap) == ([], False)
    assert (world.enemy_memory, world.known_enemy_defenses) == ({}, [])


# -- the clock, the sides and the map --------------------------------------------------------------


def test_the_clock_is_read_from_the_state(world):
    updated(world, "midgame_allies")

    assert (world.tick, world.game_seconds, world.remaining_seconds, world.phase) == (5760, 230.4, 969.6, "playing")


@pytest.mark.parametrize(
    "name, faction, enemy_faction, family",
    [
        ("opening_mcv", "england", "russia", "allies"),
        ("midgame_allies", "england", "russia", "allies"),
        ("midgame_soviet", "russia", "england", "soviet"),
    ],
)
def test_the_family_is_allies_for_england_and_soviet_for_russia(world, name, faction, enemy_faction, family):
    updated(world, name)

    assert (world.faction, world.enemy_faction, world.family) == (faction, enemy_faction, family)


@pytest.mark.parametrize(
    "faction, family",
    [("england", "allies"), ("france", "allies"), ("germany", "allies"), ("russia", "soviet"), ("ukraine", "soviet")],
)
def test_each_faction_has_its_family_whatever_it_is_offered(world, faction, family):
    updated(world, "opening_mcv", faction=faction, available_production=["tent", "barr"])

    assert world.family == family


@pytest.mark.parametrize(
    "faction, available, family",
    [
        ("random", ["powr", "tent"], "allies"),
        ("random", ["powr", "barr"], "soviet"),
        ("random", [], "soviet"),
        ("Atlantis", ["POWR", "TENT"], "allies"),
        (None, ["powr", "tent"], "allies"),
    ],
)
def test_an_unknown_faction_is_allied_when_it_is_offered_a_tent_and_soviet_otherwise(world, faction, available, family):
    updated(world, "opening_mcv", faction=faction, available_production=available)

    assert world.family == family


def test_faction_names_are_read_without_regard_to_case(world):
    updated(world, "opening_mcv", faction="England", enemy_faction="UKRAINE")

    assert (world.faction, world.enemy_faction, world.family) == ("england", "ukraine", "allies")


def test_the_map_bounds_the_bases_and_the_spawns_are_known_after_the_first_state(world):
    updated(world, "opening_mcv")

    assert world.map_bounds == (0, 0, 96, 96)
    assert (world.base_center, world.enemy_base) == ((22, 48), (74, 48))
    assert world.spawns == ((22, 48), (74, 48))


def test_without_a_game_config_there_are_no_spawns_and_the_enemy_base_comes_from_the_state(items):
    world = updated(World(items), "opening_mcv")

    assert (world.spawns, world.enemy_base, world.map_bounds) == ((), (74, 48), (0, 0, 96, 96))


def test_without_the_state_naming_it_the_enemy_base_is_the_spawn_farthest_from_the_base(world):
    state = load_state("midgame_allies")
    del state["legal_actions"]

    world.update(state)

    assert (world.base_center, world.enemy_base) == ((22, 48), (74, 48))


def test_the_enemy_base_is_kept_when_a_later_state_does_not_name_it(world):
    updated(world, "opening_mcv")
    later = load_state("midgame_allies")
    later["legal_actions"] = None
    later["observation"]["base_center"] = [70, 50]

    world.update(later)

    assert (world.base_center, world.enemy_base) == ((70, 50), (74, 48))


def test_the_bounds_come_from_the_game_config_when_the_state_has_none(world):
    state = load_state("opening_mcv")
    state["map"] = {"id": MAP_ID, "width": 96, "height": 96}

    world.update(state)

    assert world.map_bounds == (0, 0, 96, 96)


def test_without_bounds_in_the_state_or_the_game_config_the_map_stays_unknown(items):
    world = World(items)
    state = load_state("opening_mcv")
    state["map"] = {"id": MAP_ID, "width": 96, "height": 96}
    state["legal_actions"] = None

    world.update(state)

    assert (world.map_bounds, world.spawns, world.enemy_base) == (None, (), None)
    assert world.in_bounds(22, 48) is False and world.base_center == (22, 48)


@pytest.mark.parametrize(
    "cell, inside",
    [((2, 3), True), ((97, 92), True), ((1, 3), False), ((2, 2), False), ((98, 50), False), ((50, 93), False)],
)
def test_a_cell_is_in_bounds_when_it_lies_in_the_playable_rectangle(world, cell, inside):
    state = load_state("opening_mcv")
    state["map"]["bounds"] = [2, 3, 96, 90]
    world.update(state)

    assert world.in_bounds(*cell) is inside


def test_no_cell_is_in_bounds_before_the_map_is_known(world):
    assert world.in_bounds(10, 10) is False


def test_a_state_without_an_observation_sets_the_phase_and_nothing_else(world):
    updated(world, "midgame_allies")
    units_before, tick_before = dict(world.units), world.tick

    world.update({"phase": "waiting_for_agents", "state_version": 0, "observation": None, "is_current_actor": False})

    assert world.phase == "waiting_for_agents"
    assert (world.units, world.tick, world.faction) == (units_before, tick_before, "england")


# -- the agent's own side --------------------------------------------------------------------------


def test_economy_military_and_trade_balance_are_read_from_the_state(world):
    updated(
        world,
        "base_under_attack",
        military={"kills_cost": 2900, "deaths_cost": 1400, "assets_value": 6300, "army_value": 400},
    )

    assert world.economy == Economy(
        cash=420, ore=0, resource_capacity=2000, power_provided=100, power_drained=70, harvesters=1
    )
    assert world.military == Military(kills_cost=2900, deaths_cost=1400, assets_value=6300, army_value=400)
    assert world.trade_balance == 1500


def test_a_state_with_no_cash_and_1800_ore_gives_1800_to_spend(world):
    economy = {"cash": 0, "ore": 1800, "resource_capacity": 2000, "power_provided": 100, "power_drained": 70}

    updated(world, "base_under_attack", economy={**economy, "harvesters": 1})

    assert (world.economy.cash, world.economy.ore, world.economy.resource_capacity) == (0, 1800, 2000)
    assert world.economy.spendable == 1800


@pytest.mark.parametrize(
    "cash, ore, spendable", [(5000, 0, 5000), (300, 450, 750), (0, 0, 0)], ids=["cash alone", "both", "nothing"]
)
def test_spendable_money_is_the_cash_and_the_stored_ore_together(world, cash, ore, spendable):
    updated(world, "midgame_allies", economy={"cash": cash, "ore": ore, "resource_capacity": 4000})

    assert world.economy.spendable == spendable


def test_an_economy_that_names_no_ore_has_its_cash_to_spend(world):
    updated(world, "midgame_allies", economy={"cash": 640})

    assert (world.economy.ore, world.economy.resource_capacity, world.economy.spendable) == (0, 0, 640)


def test_a_state_with_three_units_of_which_one_is_a_wreck_gives_two_units(world):
    state = load_state("wreck")
    assert [bool(unit.get("wreck")) for unit in state["observation"]["units"]] == [False, False, True]

    world.update(state)

    assert sorted(unit.type for unit in world.units.values()) == ["e1", "harv"]
    assert len(world.units) == 2 and all(not unit.type.endswith("husk") for unit in world.units.values())
    assert [unit.type for unit in world.combat_units.values()] == ["e1"]


def test_a_wreck_is_left_out_by_its_type_alone_or_by_its_mark_alone(world):
    units = [
        own_unit(1, "e1"),
        own_unit(2, "1TNK.Husk"),
        own_unit(3, "2tnk", wreck=True),
        own_unit(4, "jeep", wreck=False),
    ]

    updated(world, "opening_mcv", units=units)

    assert sorted(world.units) == [1, 4]


def test_units_are_keyed_by_id_with_the_fields_of_the_state(world):
    updated(world, "midgame_soviet")

    assert world.units[110] == Unit(
        id=110, type="e1", x=22, y=49, hp=100, idle=False, activity="attack", stance="attack_anything"
    )
    assert len(world.units) == 13 and set(world.units) == {unit.id for unit in world.units.values()}


def test_buildings_are_keyed_by_id_with_the_fields_of_the_state(world):
    state = load_state("base_under_attack")
    state["observation"]["buildings"][2].update(rally=[30, 40], producing="E1", powered=False, powered_down=True)

    world.update(state)

    assert world.buildings[102] == Building(
        id=102,
        type="powr",
        x=22,
        y=46,
        hp=45,
        powered=True,
        powered_down=False,
        repairing=True,
        producing=None,
        rally=None,
    )
    assert world.buildings[103] == Building(
        id=103,
        type="tent",
        x=20,
        y=48,
        hp=100,
        powered=False,
        powered_down=True,
        repairing=False,
        producing="e1",
        rally=(30, 40),
    )
    assert [building.type for building in world.buildings.values()] == ["fact", "powr", "tent", "proc", "pbox"]


def test_combat_units_leave_out_the_non_combat_types_and_the_ships(world):
    types = ["mcv", "harv", "e6", "medi", "mech", "thf", "spy", "e1", "e3", "1tnk", "jeep", "dd", "ss", "lst", "arty"]
    units = [own_unit(number, unit_type.upper()) for number, unit_type in enumerate(types, start=1)]

    updated(world, "opening_mcv", units=units)

    assert len(world.units) == len(types)
    assert sorted(unit.type for unit in world.combat_units.values()) == ["1tnk", "arty", "e1", "e3", "jeep"]
    assert all(world.combat_units[unit_id] is world.units[unit_id] for unit_id in world.combat_units)


def test_a_ship_is_known_by_the_queue_the_item_table_gives_it(items, game_config):
    with_a_new_ship = items.with_game_config({"rules": {"units": {"carrier": {"cost": 3000, "queue": "Ship"}}}})
    world = World(with_a_new_ship, game_config)

    updated(world, "opening_mcv", units=[own_unit(1, "carrier"), own_unit(2, "unknown-walker")])

    assert [unit.type for unit in world.combat_units.values()] == ["unknown-walker"]


# -- production ------------------------------------------------------------------------------------


def test_production_is_grouped_by_queue_in_queue_order(world):
    updated(world, "midgame_allies")

    assert set(world.production) == {"Building", "Defense", "Infantry", "Vehicle"}
    assert world.production["Infantry"] == [
        ProductionItem(queue="Infantry", item="e1", progress=0.75, remaining_s=0.6, paused=False, ready=False),
        ProductionItem(queue="Infantry", item="e1", progress=0.0, remaining_s=2.4, paused=False, ready=False),
    ]
    assert [(item.item, item.ready) for item in world.production["Defense"]] == [("pbox", True)]


def test_a_queue_without_items_is_not_in_production(world):
    updated(world, "opening_mcv")

    assert world.production == {} and world.production.get("Building", []) == []


def test_ready_to_place_holds_the_finished_items_of_the_building_and_defense_queues(world):
    production = [
        {"queue": "Defense", "item": "PBOX", "progress": 1.0, "remaining_s": 0, "paused": False, "ready": True},
        {"queue": "Building", "item": "powr", "progress": 1.0, "remaining_s": 0, "paused": False, "ready": True},
        {"queue": "Building", "item": "tent", "progress": 0.2, "remaining_s": 9.6, "paused": False, "ready": False},
        {"queue": "Infantry", "item": "e1", "progress": 1.0, "remaining_s": 0, "paused": False, "ready": True},
    ]

    updated(world, "opening_mcv", production=production)

    assert world.ready_to_place == ("powr", "pbox")


def test_available_production_is_a_set_of_lowercase_names(world):
    updated(world, "opening_mcv", available_production=["POWR", "Tent", "e1", "powr"])

    assert world.available_production == frozenset({"powr", "tent", "e1"})


def test_item_names_of_a_state_are_lowercased_everywhere(world):
    state = load_state("midgame_allies")
    for part in ("units", "buildings"):
        for actor in state["observation"][part]:
            actor["type"] = actor["type"].upper()
    for job in state["observation"]["production"]:
        job["item"] = job["item"].upper()

    world.update(state)

    assert {unit.type for unit in world.units.values()} == {"harv", "e1", "e3", "medi", "jeep", "1tnk"}
    assert {building.type for building in world.buildings.values()} == {"fact", "powr", "tent", "proc", "weap", "pbox"}
    assert world.ready_to_place == ("pbox",) and len(world.combat_units) == 11


# -- events ----------------------------------------------------------------------------------------


def event(seq: int, kind: str = "unit_created", **details) -> dict:
    return {"seq": seq, "tick": 100 + seq, "kind": kind, **details}


def with_events(world: World, seqs: list[int]) -> World:
    """The world after one more update with a state whose event window holds events of these numbers."""
    return updated(world, "events_gap", events=[event(seq) for seq in seqs])


def test_the_events_of_the_first_state_are_all_new(world):
    updated(world, "midgame_allies")

    assert [new["seq"] for new in world.events_new] == list(range(11, 31))
    assert world.events_gap is False


@pytest.mark.parametrize(
    "window, new, gap",
    [
        ([3, 4, 5, 6, 9], [5, 6, 9], True),
        ([3, 4, 5, 6, 7], [5, 6, 7], False),
        ([7, 8], [7, 8], True),
        ([4, 5], [5], False),
        ([2, 3, 4], [], False),
        ([], [], False),
        ([6], [6], True),
    ],
    ids=["5 6 9", "5 6 7", "7 8", "5", "nothing new", "no events", "6"],
)
def test_after_seq_4_the_new_events_are_those_above_it_and_a_gap_is_any_number_missed(world, window, new, gap):
    with_events(world, [1, 2, 3, 4])

    with_events(world, window)

    assert [new_event["seq"] for new_event in world.events_new] == new
    assert world.events_gap is gap


def test_the_events_gap_fixture_has_three_new_events_after_seq_4_and_a_gap(world):
    earlier = load_state("events_gap")
    earlier["observation"]["events"] = [old for old in earlier["observation"]["events"] if old["seq"] <= 4]
    world.update(earlier)
    assert world.events_gap is False and len(world.events_new) == 4

    updated(world, "events_gap")

    assert [new["seq"] for new in world.events_new] == [5, 6, 9]
    assert world.events_gap is True


def test_numbers_missing_inside_the_first_window_are_a_gap_but_its_first_number_is_not(world):
    with_events(world, [480, 481, 482])
    first_window = (world.events_gap, len(world.events_new))
    fresh = with_events(World(world.items), [480, 481, 485])

    assert first_window == (False, 3)
    assert (fresh.events_gap, len(fresh.events_new)) == (True, 3)


def test_the_gap_flag_and_the_new_events_are_of_the_latest_update_only(world):
    with_events(world, [1, 2, 3, 4])
    with_events(world, [4, 5, 9])
    assert world.events_gap is True

    with_events(world, [5, 9, 10])
    after_one_more = (world.events_gap, [new["seq"] for new in world.events_new])
    with_events(world, [5, 9, 10])

    assert after_one_more == (False, [10])
    assert (world.events_gap, world.events_new) == (False, [])


def test_new_events_keep_every_field_and_are_copies_of_the_state_s(world):
    state = load_state("base_under_attack")

    world.update(state)
    attacked = [new for new in world.events_new if new["kind"] == "under_attack"]
    state["observation"]["events"][-1]["kind"] = "changed"

    assert attacked[0] == {
        "seq": 24,
        "tick": 2160,
        "kind": "under_attack",
        "id": 101,
        "type": "fact",
        "x": 22,
        "y": 48,
        "hp": 82,
    }
    assert world.events_new[-1]["kind"] == "unit_lost"


def test_a_state_without_an_observation_leaves_the_events_as_they_were(world):
    with_events(world, [1, 2])

    world.update({"phase": "waiting_for_agents", "observation": None})
    with_events(world, [2, 3])

    assert [new["seq"] for new in world.events_new] == [3] and world.events_gap is False


# -- the enemy memory ------------------------------------------------------------------------------


# The six buildings of an enemy base that the world must not forget when it loses sight of them.
BASE_TYPES = ("fact", "powr", "proc", "barr", "weap", "ftur")


def enemy(enemy_id: int, enemy_type: str, x: int, y: int, hp: int = 100, **fields) -> dict:
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": hp, **fields}


def at_tick(
    world: World,
    tick: int,
    *,
    enemies: tuple[dict, ...] = (),
    enemy_buildings: tuple[dict, ...] = (),
    units: tuple[dict, ...] = (),
    events: tuple[dict, ...] = (),
) -> World:
    """The world after one more update with a state of that tick showing exactly these enemies, own units and events."""
    state = load_state("opening_mcv")
    state["state_version"] = tick
    state["observation"].update(
        tick=tick,
        units=list(units),
        visible_enemies=list(enemies),
        visible_enemy_buildings=list(enemy_buildings),
        events=list(events),
    )
    world.update(state)
    return world


def test_an_enemy_in_sight_is_remembered_with_what_the_state_shows_of_it(world):
    at_tick(world, 100, enemies=(enemy(7, "E1", 40, 41, hp=80),), enemy_buildings=(enemy(8, "PBOX", 50, 51),))

    assert world.enemy_memory == {
        7: EnemyRecord(
            id=7, type="e1", x=40, y=41, hp=80, is_building=False, last_seen_tick=100, visible=True, stale=False
        ),
        8: EnemyRecord(id=8, type="pbox", x=50, y=51, hp=100, is_building=True, last_seen_tick=100, visible=True),
    }


def test_an_enemy_building_seen_at_tick_100_and_absent_at_tick_200_is_remembered_as_last_seen(world):
    at_tick(world, 100, enemy_buildings=(enemy(8, "barr", 70, 46, hp=90),), units=(own_unit(1, "e1", 20, 20),))

    at_tick(world, 200, units=(own_unit(1, "e1", 20, 20),))

    assert world.enemy_memory[8] == EnemyRecord(
        id=8, type="barr", x=70, y=46, hp=90, is_building=True, last_seen_tick=100, visible=False, stale=False
    )


def test_an_enemy_unit_last_seen_at_tick_100_is_stale_at_tick_900_and_not_at_tick_800(world):
    at_tick(world, 100, enemies=(enemy(7, "3tnk", 60, 40),))

    at_tick(world, 800)
    at_800 = (world.enemy_memory[7].stale, world.enemy_memory[7].visible, world.enemy_memory[7].last_seen_tick)
    at_tick(world, 900)

    assert at_800 == (False, False, 100)
    assert (world.enemy_memory[7].stale, world.enemy_memory[7].visible, world.enemy_memory[7].last_seen_tick) == (
        True,
        False,
        100,
    )


@pytest.mark.parametrize("tick, stale", [(849, False), (850, True)])
def test_a_unit_becomes_stale_750_ticks_after_it_was_last_seen(world, tick, stale):
    at_tick(world, 100, enemies=(enemy(7, "e1", 60, 40),))

    at_tick(world, tick)

    assert world.enemy_memory[7].stale is stale


def test_a_stale_unit_seen_again_is_fresh_and_at_its_new_place(world):
    at_tick(world, 100, enemies=(enemy(7, "e1", 60, 40),))
    at_tick(world, 1000)
    assert world.enemy_memory[7].stale is True

    at_tick(world, 1005, enemies=(enemy(7, "e1", 30, 44, hp=35),))

    assert world.enemy_memory[7] == EnemyRecord(
        id=7, type="e1", x=30, y=44, hp=35, is_building=False, last_seen_tick=1005
    )


def test_a_building_out_of_sight_never_becomes_stale(world):
    at_tick(world, 100, enemy_buildings=(enemy(8, "tsla", 70, 46),))

    at_tick(world, 5000)

    assert (world.enemy_memory[8].stale, world.enemy_memory[8].visible) == (False, False)


def test_a_building_is_forgotten_when_a_kill_is_reported_as_it_leaves_sight(world):
    at_tick(world, 100, enemy_buildings=(enemy(8, "ftur", 70, 46), enemy(9, "barr", 72, 46)))

    at_tick(world, 105, enemy_buildings=(enemy(9, "barr", 72, 46),), events=(event(1, "enemy_killed", count=1),))

    assert set(world.enemy_memory) == {9}


def test_a_building_that_leaves_sight_without_a_kill_being_reported_is_kept(world):
    at_tick(world, 100, enemy_buildings=(enemy(8, "ftur", 70, 46),))

    at_tick(world, 105, events=(event(1, "unit_lost", id=5), event(2, "enemy_spotted", id=77)))

    assert world.enemy_memory[8].visible is False


def test_a_kill_reported_while_a_remembered_building_was_already_out_of_sight_does_not_remove_it(world):
    at_tick(world, 100, enemy_buildings=(enemy(8, "ftur", 70, 46),))
    at_tick(world, 105)

    at_tick(world, 110, events=(event(1, "enemy_killed", count=3),))

    assert world.enemy_memory[8].last_seen_tick == 100


def test_a_kill_report_that_was_seen_before_is_not_evidence_again(world):
    killed = event(1, "enemy_killed", count=1)
    at_tick(world, 100, enemy_buildings=(enemy(8, "ftur", 70, 46),), events=(killed,))

    at_tick(world, 105, events=(killed,))

    assert 8 in world.enemy_memory


def test_a_kill_reported_as_a_building_and_a_unit_leave_sight_must_account_for_both(world):
    sighted = {"enemies": (enemy(7, "e1", 60, 46),), "enemy_buildings": (enemy(8, "ftur", 70, 46),)}
    at_tick(world, 100, **sighted)
    at_tick(world, 105, events=(event(1, "enemy_killed", count=1),))
    one_kill_for_two = set(world.enemy_memory)

    at_tick(world, 110, **sighted)
    at_tick(world, 115, events=(event(2, "enemy_killed", count=1), event(3, "enemy_killed", count=1)))

    assert one_kill_for_two == {7, 8}
    assert set(world.enemy_memory) == set() and world.killed_in_sight == frozenset({7, 8})


def test_two_buildings_that_leave_sight_with_one_kill_reported_are_both_kept(world):
    at_tick(world, 100, enemy_buildings=(enemy(8, "ftur", 70, 46), enemy(9, "barr", 72, 46)))

    at_tick(world, 105, events=(event(1, "enemy_killed", count=1),))

    assert set(world.enemy_memory) == {8, 9} and world.killed_in_sight == frozenset()


def test_an_enemy_base_out_of_sight_for_3000_ticks_with_kills_reported_elsewhere_is_still_six_buildings(world):
    base = tuple(enemy(10 + number, name, 70 + 3 * number, 46) for number, name in enumerate(BASE_TYPES))
    at_tick(world, 100, enemy_buildings=base, units=(own_unit(1, "jeep", 70, 50), own_unit(2, "e1", 30, 33)))
    # The jeep that saw the base is lost in the state in which a kill is reported.
    at_tick(world, 105, units=(own_unit(2, "e1", 30, 33),), events=(event(1, "enemy_killed", count=1),))
    for number, tick in enumerate(range(355, 3201, 250), start=2):
        rifle = enemy(100 + number, "e1", 30, 30)
        at_tick(world, tick, enemies=(rifle,), units=(own_unit(2, "e1", 30, 33),))
        at_tick(world, tick + 5, units=(own_unit(2, "e1", 30, 33),), events=(event(number, "enemy_killed", count=1),))

    remembered = [record.type for record in world.enemy_memory.values() if record.is_building]

    assert world.tick - 100 >= 3000 and remembered == list(BASE_TYPES)


@pytest.mark.parametrize(
    "unit_cell, forgotten",
    [((70, 50), True), ((74, 46), True), ((72, 49), True), ((73, 49), False), ((70, 51), False), ((75, 46), False)],
    ids=["4 below", "4 right", "3.6 away", "4.2 away", "5 below", "5 right"],
)
def test_a_missing_building_is_forgotten_when_an_own_unit_stands_within_4_cells_of_its_cell_for_50_ticks(
    world, unit_cell, forgotten
):
    at_tick(world, 100, enemy_buildings=(enemy(8, "proc", 70, 46),), units=(own_unit(1, "jeep", 60, 46),))

    at_tick(world, 200, units=(own_unit(1, "jeep", *unit_cell), own_unit(2, "e1", 20, 20)))
    after_one_state = 8 in world.enemy_memory
    at_tick(world, 250, units=(own_unit(1, "jeep", *unit_cell), own_unit(2, "e1", 20, 20)))

    assert after_one_state and (8 not in world.enemy_memory) is forgotten


def test_an_own_unit_that_passes_a_missing_building_is_no_evidence_until_it_has_stood_near_for_50_ticks(world):
    at_tick(world, 100, enemy_buildings=(enemy(8, "proc", 70, 46),))
    for tick, cell in ((200, (72, 49)), (245, (72, 49)), (250, (80, 49)), (255, (72, 49)), (300, (72, 49))):
        at_tick(world, tick, units=(own_unit(1, "jeep", *cell),))
    kept_after_45_ticks = 8 in world.enemy_memory

    at_tick(world, 305, units=(own_unit(1, "jeep", 72, 49),))

    assert kept_after_45_ticks and 8 not in world.enemy_memory


def test_an_own_building_next_to_a_missing_enemy_building_is_no_evidence(world):
    state = load_state("opening_mcv")
    state["observation"].update(tick=100, visible_enemy_buildings=[enemy(8, "proc", 24, 48)])
    world.update(state)
    state = load_state("midgame_allies")
    state["observation"].update(units=[], visible_enemies=[], visible_enemy_buildings=[])

    world.update(state)

    assert any(building.x == 24 and building.y == 48 for building in world.buildings.values())
    assert world.enemy_memory[8].visible is False


def test_an_enemy_unit_out_of_sight_is_not_forgotten_however_long_an_own_unit_stands_near_its_cell(world):
    at_tick(world, 100, enemies=(enemy(7, "e1", 40, 40),), units=(own_unit(1, "e1", 40, 41),))

    at_tick(world, 105, units=(own_unit(1, "e1", 40, 41),))
    at_tick(world, 200, units=(own_unit(1, "e1", 40, 41),))

    assert world.enemy_memory[7] == EnemyRecord(
        id=7, type="e1", x=40, y=40, hp=100, is_building=False, last_seen_tick=100, visible=False
    )


def test_killed_in_sight_an_enemy_rifle_is_forgotten_in_the_update_that_reports_its_kill(world):
    at_tick(world, 100, enemies=(enemy(7, "e1", 40, 40), enemy(8, "e1", 42, 40)))

    at_tick(world, 105, enemies=(enemy(8, "e1", 42, 40),), events=(event(1, "enemy_killed", count=1),))

    assert world.killed_in_sight == frozenset({7}) and set(world.enemy_memory) == {8}


@pytest.mark.parametrize("tick, stale", [(105, False), (849, False), (850, True)])
def test_killed_in_sight_holds_no_rifle_that_leaves_sight_without_a_kill_and_it_is_remembered_until_stale(
    world, tick, stale
):
    at_tick(world, 100, enemies=(enemy(7, "e1", 40, 40),))

    at_tick(world, tick)

    assert world.killed_in_sight == frozenset()
    assert (world.enemy_memory[7].visible, world.enemy_memory[7].stale) == (False, stale)


@pytest.mark.parametrize(
    "gone, kills, remembered",
    [((7, 8), 2, set()), ((7, 8, 9), 1, {7, 8, 9})],
    ids=["2 gone with 2 kills", "3 gone with 1 kill"],
)
def test_killed_in_sight_forgets_the_units_gone_only_when_the_kills_reported_account_for_all_of_them(
    world, gone, kills, remembered
):
    at_tick(world, 100, enemies=tuple(enemy(enemy_id, "e1", 40 + enemy_id, 40) for enemy_id in gone))

    at_tick(world, 105, events=tuple(event(seq, "enemy_killed", count=1) for seq in range(1, kills + 1)))

    assert set(world.enemy_memory) == remembered
    assert all(world.enemy_memory[enemy_id].visible is False for enemy_id in remembered)


@pytest.mark.parametrize("killed", [True, False], ids=["killed in sight", "left sight"])
def test_killed_in_sight_the_fight_estimate_and_targeting_count_a_rifle_only_while_it_is_not_reported_killed(
    game_config, killed
):
    # The agent's item table, with the damage table that tells targeting how far each unit reaches.
    world = World(ItemTable.from_tracked_files(REPO_ROOT), game_config)
    tanks = tuple(own_unit(unit_id, "2tnk", 40, 48 + unit_id) for unit_id in (1, 2))
    at_tick(world, 100, enemies=(enemy(7, "e1", 42, 49), enemy(8, "e1", 43, 50)), units=tanks)
    kill = (event(1, "enemy_killed", count=1),) if killed else ()

    at_tick(world, 105, enemies=(enemy(8, "e1", 43, 50),), units=tanks, events=kill)
    units = list(world.units.values())
    fight = estimate(units, world, Tuning())
    ranked = targeting.targets(units, centroid(units), world, Tuning())

    counted = {8} if killed else {7, 8}
    assert {record.id for record in fight.enemies} == counted
    assert {record.id for record in ranked} == counted


def test_an_enemy_wreck_is_never_remembered_and_the_wreck_of_a_remembered_unit_removes_it(world):
    at_tick(
        world,
        100,
        enemies=(enemy(7, "2tnk", 40, 40), enemy(8, "3tnk.husk", 41, 40), enemy(9, "1tnk", 42, 40, wreck=True)),
    )
    remembered_first = set(world.enemy_memory)

    at_tick(world, 105, enemies=(enemy(7, "2tnk.husk", 40, 40, wreck=True),))

    assert remembered_first == {7}
    assert world.enemy_memory == {}


def test_an_enemy_e1r1_is_a_combat_unit_valued_classed_and_ranged_as_a_rifle(world):
    at_tick(world, 100, enemies=(enemy(7, "e1r1", 40, 41), enemy(8, "e3r1", 41, 41)))

    rifle, facts = world.enemy_memory[7], world.items.get("e1r1")

    assert is_combat_unit(rifle) and (facts.cost, facts.queue, facts.range) == (100, "Infantry", 5.0)
    assert EnemyIntel().observe(world, ScoutingTuning()).enemy_combat_value == 100 + 300


def test_each_type_the_item_table_cannot_value_is_logged_once_in_the_match(world, match_record):
    at_tick(world, 100, enemies=(enemy(7, "camera.spyplane", 40, 41), enemy(8, "e1r1", 41, 41)))
    at_tick(world, 105, enemies=(enemy(7, "camera.spyplane", 42, 41),), units=(own_unit(1, "zzz", 20, 20),))
    at_tick(world, 110, enemies=(enemy(9, "camera.spyplane", 43, 41),), units=(own_unit(1, "zzz", 20, 21),))

    errors = match_record("error")

    assert [(error["where"], error["type"], error["exception"]) for error in errors] == [
        ("redalert.world", "handled", "UnknownItemType")
    ] * 2
    assert ["'camera.spyplane'" in errors[0]["message"], "'zzz'" in errors[1]["message"]] == [True, True]
    assert EnemyIntel().observe(world, ScoutingTuning()).enemy_combat_value == 100


def test_a_dotted_variant_enemy_harv_frans_is_remembered_as_a_harvester_and_no_type_of_it_is_logged(
    world, match_record
):
    at_tick(
        world,
        100,
        enemies=(enemy(7, "harv.frans", 40, 41), enemy(8, "E6.frans", 41, 41), enemy(9, "mh60.frans", 42, 41)),
        units=(own_unit(1, "harv.frans", 20, 20), own_unit(2, "2tnk", 20, 21)),
    )
    report = EnemyIntel().observe(world, ScoutingTuning())

    assert [world.enemy_memory[enemy_id].type for enemy_id in (7, 8, 9)] == ["harv", "e6", "mh60"]
    assert not is_combat_unit(world.enemy_memory[7]) and not is_combat_unit(world.enemy_memory[8])
    assert report.enemy_harvester_cells == [(40, 41)] and report.enemy_combat_value == 1500
    assert (world.units[1].type, set(world.combat_units)) == ("harv", {2})
    assert match_record("error") == []


@pytest.mark.parametrize("name", ["tecn", "tecn2", "zzz.frans"])
def test_an_enemy_unit_of_a_type_no_variant_rule_resolves_counts_unknown_unit_value_in_a_fight_estimate(
    game_config, name
):
    world = World(ItemTable.from_tracked_files(REPO_ROOT), game_config)
    tanks = tuple(own_unit(unit_id, "2tnk", 40, 48 + unit_id) for unit_id in (1, 2))
    at_tick(world, 100, enemies=(enemy(7, name, 42, 49), enemy(8, name, 43, 50, hp=50)), units=tanks)
    units = list(world.units.values())

    fight = estimate(units, world, Tuning())
    raised = dataclasses.replace(Tuning(), combat=dataclasses.replace(Tuning().combat, unknown_unit_value=500))

    assert (fight.own, fight.enemy, fight.ratio) == (1700, 450, pytest.approx(1700 / 450))
    assert ratio_against(units, world.enemy_memory.values(), world, Tuning()) == pytest.approx(1700 / 450)
    assert estimate(units, world, raised).enemy == 750
    assert fight_worth(name, 100, world) == 0.0 and world.items.damage.get(name).armour == "none"


def test_a_fight_estimate_values_a_stock_tank_at_its_cost_and_takes_no_harv_frans_for_a_fighter(game_config):
    world = World(ItemTable.from_tracked_files(REPO_ROOT), game_config)
    tanks = tuple(own_unit(unit_id, "2tnk", 40, 48 + unit_id) for unit_id in (1, 2))
    at_tick(world, 100, enemies=(enemy(7, "harv.frans", 42, 49),), units=tanks)
    no_fight = estimate(list(world.units.values()), world, Tuning())

    at_tick(world, 105, enemies=(enemy(7, "harv.frans", 42, 49), enemy(8, "2tnk", 43, 50)), units=tanks)
    fight = estimate(list(world.units.values()), world, Tuning())

    assert no_fight is None
    assert (fight.enemy, [record.id for record in fight.enemies]) == (850, [8])


def test_a_rifle_target_of_an_unknown_type_is_hurt_as_infantry_by_ratio_against_target(game_config):
    world = World(ItemTable.from_tracked_files(REPO_ROOT), game_config)
    rifles = tuple(own_unit(unit_id, "e1", 40, 48 + unit_id) for unit_id in (1, 2, 3))
    at_tick(world, 100, enemies=(enemy(7, "tecn", 42, 49),), units=rifles)
    target = [world.enemy_memory[7]]

    ratio = ratio_against_target(list(world.units.values()), target, world, Tuning(), enemies=target)

    assert ratio == pytest.approx(300 / 300)


def test_known_enemy_defenses_are_the_remembered_buildings_that_shoot(world):
    defenses = ("pbox", "hbox", "gun", "ftur", "tsla", "agun", "sam")
    others = ("fact", "powr", "barr", "proc", "weap", "dome", "silo")
    buildings = tuple(
        enemy(100 + number, name.upper(), 60 + number, 30) for number, name in enumerate(defenses + others)
    )
    at_tick(world, 100, enemy_buildings=buildings, enemies=(enemy(7, "pbox", 10, 10), enemy(8, "ftrk", 11, 10)))

    in_sight = [record.type for record in world.known_enemy_defenses]
    at_tick(world, 200)

    assert in_sight == list(defenses)
    assert [(record.type, record.visible) for record in world.known_enemy_defenses] == [
        (name, False) for name in defenses
    ]
    assert all(world.enemy_memory[record.id] is record for record in world.known_enemy_defenses)


def test_the_enemy_memory_of_the_allied_fixture_holds_the_three_enemies_the_scout_sees(world):
    updated(world, "midgame_allies")

    assert {record.id: (record.type, record.is_building) for record in world.enemy_memory.values()} == {
        123: ("e1", False),
        124: ("ftrk", False),
        122: ("ftur", True),
    }
    assert [record.id for record in world.known_enemy_defenses] == [122]


def test_the_enemy_memory_follows_a_fake_match(items):
    game = FakeRedAlert("england", "russia")
    world = World(items, game.game_config)
    turret = game.place_enemy("ftur", 60, 48)
    rifleman = game.place_enemy("e1", 61, 49)
    scout = game.place_enemy("jeep", 22, 60, visible=True)
    game.show_enemy(turret, True)
    world.update(game.state())
    assert {record.id: record.visible for record in world.enemy_memory.values()} == {turret: True, scout: True}

    game.show_enemy(turret, None)
    game.show_enemy(scout, False)
    game.step()
    world.update(game.state())
    out_of_sight = {record.id: (record.visible, record.last_seen_tick) for record in world.enemy_memory.values()}
    game.show_enemy(turret, True)
    game.show_enemy(rifleman, True)
    game.step()
    world.update(game.state())
    game.remove_enemy(turret)
    game.emit_event("enemy_killed", count=1)
    game.step()
    world.update(game.state())

    assert out_of_sight == {turret: (False, 0), scout: (False, 0)}
    assert set(world.enemy_memory) == {scout, rifleman}
    assert world.enemy_memory[rifleman].visible is True and world.known_enemy_defenses == []


def test_two_worlds_do_not_share_their_memory_of_the_enemy(items):
    first, second = World(items), World(items)

    at_tick(first, 100, enemies=(enemy(7, "e1", 40, 40),))
    at_tick(second, 100, enemy_buildings=(enemy(8, "pbox", 50, 50),))

    assert (set(first.enemy_memory), set(second.enemy_memory)) == ({7}, {8})
    assert (first.known_enemy_defenses, len(second.known_enemy_defenses)) == ([], 1)


# -- the item table and the rule tools -------------------------------------------------------------

SYNTHETIC_RECORD = REPO_ROOT / "tests" / "fixtures" / "records" / "redalert_synthetic"


def test_the_item_table_reports_a_cost_from_the_game_config_and_the_file_s_cost_without_one(items):
    with_game_config = items.with_game_config({"rules": {"buildings": {"pbox": {"cost": 400}}}})
    without_game_config = items.with_game_config(None)

    assert with_game_config["pbox"].cost == 400
    assert (without_game_config["pbox"].cost, items["pbox"].cost) == (600, 600)


def test_capture_rules_on_a_fixture_record_writes_the_rules_of_its_game_config_event(run_script, tmp_path):
    first_line = (SYNTHETIC_RECORD / "events.jsonl").read_text(encoding="utf-8").splitlines()[0]
    game_config_event = json.loads(first_line)
    assert game_config_event["kind"] == "game_config"

    completed = run_script(tmp_path, "capture_rules.py", str(SYNTHETIC_RECORD))

    captured_file = tmp_path / "config" / "redalert" / "captured" / "rules-20261001.json"
    assert completed.returncode == 0, completed.stderr
    assert json.loads(captured_file.read_text(encoding="utf-8"))["rules"] == game_config_event["data"]["rules"]


def test_diff_rules_lists_pbox_when_its_cost_differs_from_items_toml(run_script, run_folder, game_config):
    items_file = run_folder / ITEMS_FILE
    items_file.parent.mkdir(parents=True)
    items_file.write_bytes((REPO_ROOT / ITEMS_FILE).read_bytes())
    changed = copy.deepcopy(game_config)
    changed["rules"]["buildings"]["pbox"]["cost"] = 400
    record_folder = run_folder / "runs" / "20261009" / "session__Multi0"
    record_folder.mkdir(parents=True)
    line = {"seq": 0, "t": "2026-10-09T18:00:00.000Z", "mono_ms": 0, "kind": "game_config", "data": changed}
    (record_folder / "events.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")

    captured = run_script(run_folder, "capture_rules.py", str(record_folder))
    captured_file = run_folder / "config" / "redalert" / "captured" / "rules-20261009.json"
    compared = run_script(run_folder, "diff_rules.py", str(captured_file))

    assert captured.returncode == 0, captured.stderr
    assert compared.returncode == 1, compared.stderr
    assert compared.stdout == "pbox: cost 400 (captured) / 600 (items.toml)\n"


# -- one world per match ---------------------------------------------------------------------------


def test_two_worlds_updated_with_different_states_share_no_data(items, game_config):
    allied, soviet = World(items, game_config), World(items, game_config)
    updated(allied, "midgame_allies")
    updated(soviet, "midgame_soviet")
    soviet_before = (dict(soviet.units), dict(soviet.buildings), dict(soviet.production), soviet.economy, soviet.tick)

    allied.units.clear()
    allied.buildings.clear()
    allied.production["Infantry"].clear()
    allied.combat_units.clear()
    updated(allied, "base_under_attack")

    assert (
        dict(soviet.units),
        dict(soviet.buildings),
        dict(soviet.production),
        soviet.economy,
        soviet.tick,
    ) == soviet_before
    assert (allied.faction, soviet.faction, allied.tick, soviet.tick) == ("england", "russia", 2170, 4825)
    for name in ("units", "buildings", "combat_units", "production"):
        assert getattr(allied, name) is not getattr(soviet, name), name


def test_a_new_world_knows_nothing_of_the_match_before(items, game_config):
    updated(World(items, game_config), "midgame_allies")

    fresh = World(items, game_config)

    assert (fresh.units, fresh.buildings, fresh.tick, fresh.faction, fresh.enemy_base) == ({}, {}, 0, None, None)


def test_an_update_keeps_no_reference_to_the_state_and_does_not_change_it(world):
    state = load_state("midgame_allies")
    untouched = copy.deepcopy(state)

    world.update(state)
    units_after_update = dict(world.units)
    state["observation"]["units"].clear()
    state["observation"]["available_production"].clear()
    state["observation"]["production"][0]["item"] = "changed"

    assert world.units == units_after_update and len(world.units) == 13
    assert "powr" in world.available_production and world.production["Building"][0].item == "fix"
    world.update(untouched)
    assert untouched == load_state("midgame_allies")


# -- no I/O, and fast enough -----------------------------------------------------------------------


def large_state() -> dict:
    """A state with 150 own units, 40 buildings and 60 enemies in sight."""
    state = load_state("midgame_allies")
    observation = state["observation"]
    unit_types = ("e1", "e3", "1tnk", "2tnk", "jeep", "harv", "medi", "arty")
    observation["units"] = [
        own_unit(1000 + number, unit_types[number % len(unit_types)], 10 + number % 40, 20 + number % 30)
        for number in range(LARGE_STATE_UNITS)
    ]
    template = observation["buildings"][0]
    observation["buildings"] = [
        {**template, "id": 2000 + number, "x": 12 + 3 * (number % 8), "y": 40 + 3 * (number // 8)}
        for number in range(LARGE_STATE_BUILDINGS)
    ]
    enemy_units = [
        {
            "id": 3000 + number,
            "type": ("e1", "3tnk", "ftrk")[number % 3],
            "x": 50 + number % 20,
            "y": 30 + number % 25,
            "hp": 90,
        }
        for number in range(LARGE_STATE_ENEMIES - 15)
    ]
    enemy_buildings = [
        {"id": 4000 + number, "type": ("ftur", "tsla", "barr")[number % 3], "x": 60 + number, "y": 60, "hp": 100}
        for number in range(15)
    ]
    observation["visible_enemies"], observation["visible_enemy_buildings"] = enemy_units, enemy_buildings
    return state


def test_an_update_with_150_units_40_buildings_and_60_enemies_takes_under_10_ms(world):
    state = large_state()
    later = copy.deepcopy(state)
    later["observation"]["tick"] += 5
    later["observation"]["visible_enemies"] = later["observation"]["visible_enemies"][::2]
    world.update(state)

    durations = []
    for number in range(TIMED_UPDATES):
        started = time.perf_counter()
        world.update(later if number % 2 else state)
        durations.append(time.perf_counter() - started)

    assert (len(world.units), len(world.buildings)) == (LARGE_STATE_UNITS, LARGE_STATE_BUILDINGS)
    assert sorted(durations)[len(durations) // 2] < MAX_UPDATE_SECONDS


def test_an_update_opens_no_file_and_no_connection(world, monkeypatch):
    states = [load_state(name) for name in FIXTURE_NAMES]
    calls = []
    real_open = builtins.open
    monkeypatch.setattr(
        builtins, "open", lambda *args, **kwargs: calls.append(("open", args)) or real_open(*args, **kwargs)
    )
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: calls.append(("connect", args)))
    monkeypatch.setattr(time, "sleep", lambda *args: calls.append(("sleep", args)))

    for state in states:
        world.update(state)

    assert calls == [] and world.tick == states[-1]["observation"]["tick"]


# -- the world of a fake match ---------------------------------------------------------------------


class WorldFollower:
    """Deploys and builds a power plant and a barracks, updating a world from every state it is asked on."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.to_build = ["powr", "barr"]

    def choose_action(self, state, _context):
        self.world.update(state.raw)
        orders = [*self._base_orders(state.raw["legal_actions"]), *self._build_orders(), *self._train_orders()]
        return {"type": "orders", "orders": orders} if orders else WAIT

    def _base_orders(self, legal: dict) -> list[dict]:
        deploy = [{"cmd": "deploy", "units": legal["deploy"]}] if legal["deploy"] else []
        return deploy + [{"cmd": "place", "item": item} for item in self.world.ready_to_place]

    def _build_orders(self) -> list[dict]:
        if not self.to_build or "Building" in self.world.production:
            return []
        if self.to_build[0] not in self.world.available_production:
            return []
        return [{"cmd": "build", "item": self.to_build.pop(0)}]

    def _train_orders(self) -> list[dict]:
        if "e1" not in self.world.available_production or "Infantry" in self.world.production:
            return []
        return [{"cmd": "train", "item": "e1", "count": 2}]


def test_a_world_follows_a_fake_match_state_by_state(items):
    game = FakeRedAlert("russia", "england")
    world = World(items.with_game_config(game.game_config), game.game_config)

    played = game.run(WorldFollower(world), 160)
    world.update(game.state())
    observation = game.state()["observation"]

    assert played.invalid_actions == 0
    assert (world.tick, world.faction, world.family, world.enemy_faction) == (800, "russia", "soviet", "england")
    assert [building.type for building in world.buildings.values()] == ["fact", "powr", "barr"]
    assert set(world.units) == {unit["id"] for unit in observation["units"]} and len(world.units) >= 4
    assert world.combat_units == world.units and FIRST_ACTOR_ID not in world.units
    assert world.economy.cash == observation["economy"]["cash"] and world.economy.power_provided == 100
    assert (world.base_center, world.enemy_base, world.spawns) == ((22, 48), (74, 48), ((22, 48), (74, 48)))


# -- cargo: units aboard a transport ---------------------------------------------------------------

APC_ID = 500
ROCKET_IDS = [501, 502, 503, 504, 505]
SIXTH_ROCKET = 506
APC_CELL = (40, 40)
ROCKET_HP = 80


def apc(x: int = APC_CELL[0], y: int = APC_CELL[1]) -> dict:
    return own_unit(APC_ID, "apc", x, y)


def rockets(unit_ids: list[int] = ROCKET_IDS) -> tuple[dict, ...]:
    """Rocket soldiers of the given ids next to the APC, each at `ROCKET_HP`."""
    return tuple(own_unit(unit_id, "e3", APC_CELL[0] + 1, APC_CELL[1], hp=ROCKET_HP) for unit_id in unit_ids)


def aboard(unit_ids: list[int]) -> tuple[CargoUnit, ...]:
    """The rocket soldiers of these ids as the cargo lists them."""
    return tuple(CargoUnit(unit_id, "e3", ROCKET_HP) for unit_id in unit_ids)


def into_the_apc(unit_ids: list[int]) -> dict:
    return {"cmd": "enter_transport", "units": list(unit_ids), "target": APC_ID}


UNLOAD_THE_APC = {"cmd": "unload", "units": [APC_ID]}


def lost(unit_ids: list[int], first_seq: int = 1) -> tuple[dict, ...]:
    """The `unit_lost` events the platform reports for rocket soldiers that left the units."""
    return tuple(
        event(first_seq + number, "unit_lost", id=unit_id, type="e3", x=APC_CELL[0] + 1, y=APC_CELL[1])
        for number, unit_id in enumerate(unit_ids)
    )


def kinds(world: World) -> list[str]:
    return [new["kind"] for new in world.events_new]


def boarded(world: World, unit_ids: list[int] = ROCKET_IDS, tick: int = 105) -> World:
    """The world after the rocket soldiers of these ids were ordered into the APC at tick 100 and left the units."""
    at_tick(world, 100, units=(apc(), *rockets(unit_ids)))
    world.note_orders([into_the_apc(unit_ids)])
    return at_tick(world, tick, units=(apc(),), events=lost(unit_ids))


def test_cargo_a_new_world_has_nothing_aboard(world):
    assert (world.cargo, world.cargo_lost, world.taken_over) == ({}, (), {})
    assert (world.aboard(ROCKET_IDS[0]), world.transport_of(ROCKET_IDS[0])) == (None, None)


def test_cargo_after_enter_transport_of_5_e3_into_an_apc_the_next_state_without_them_has_them_aboard(world):
    boarded(world)

    assert world.cargo == {APC_ID: aboard(ROCKET_IDS)}
    assert set(world.units) == {APC_ID} and world.cargo_lost == ()
    assert (world.aboard(503), world.transport_of(503)) == (CargoUnit(503, "e3", ROCKET_HP), APC_ID)


def test_cargo_the_unit_lost_events_of_the_units_that_boarded_become_boarded_events_naming_the_apc(world):
    boarded(world)

    assert kinds(world) == ["boarded"] * 5
    assert [(new["id"], new["transport"]) for new in world.events_new] == [(unit_id, APC_ID) for unit_id in ROCKET_IDS]


def test_cargo_after_unload_the_units_are_units_again_and_the_cargo_list_is_empty(world):
    boarded(world)

    world.note_orders([UNLOAD_THE_APC])
    at_tick(world, 110, units=(apc(), *rockets()))

    assert (world.cargo, world.taken_over, world.cargo_lost) == ({}, {}, ())
    assert set(world.units) == {APC_ID, *ROCKET_IDS} and world.aboard(501) is None


def test_cargo_units_that_come_out_one_state_after_another_leave_the_cargo_as_they_appear(world):
    boarded(world)

    world.note_orders([UNLOAD_THE_APC])
    at_tick(world, 110, units=(apc(), *rockets(ROCKET_IDS[:2])))

    assert world.cargo == {APC_ID: aboard(ROCKET_IDS[2:])}


def test_cargo_a_unit_gone_from_units_with_no_enter_transport_order_is_lost_as_before(world):
    at_tick(world, 100, units=(apc(), *rockets()))
    at_tick(world, 105, units=(apc(),), events=lost(ROCKET_IDS))

    assert (world.cargo, world.cargo_lost) == ({}, ())
    assert kinds(world) == ["unit_lost"] * 5


@pytest.mark.parametrize("gone_at, is_aboard", [(200, True), (205, False)], ids=["100 ticks after", "105 after"])
def test_cargo_a_unit_gone_from_units_is_aboard_only_within_100_ticks_of_its_order(world, gone_at, is_aboard):
    at_tick(world, 100, units=(apc(), *rockets([501])))
    world.note_orders([into_the_apc([501])])
    at_tick(world, gone_at - 5, units=(apc(), *rockets([501])))

    at_tick(world, gone_at, units=(apc(),), events=lost([501]))

    assert (world.transport_of(501) == APC_ID, kinds(world)) == (is_aboard, ["boarded" if is_aboard else "unit_lost"])


def test_cargo_the_board_window_is_the_one_the_tuning_gives(items, game_config):
    world = World(items, game_config, WorldTuning(board_window_ticks=200, transport_capacity=5))

    boarded(world, [501], tick=300)

    assert world.cargo == {APC_ID: aboard([501])}


def test_cargo_a_later_order_that_gives_the_unit_something_else_to_do_ends_its_boarding_order(world):
    at_tick(world, 100, units=(apc(), *rockets([501, 502, 503])))
    world.note_orders([into_the_apc([501, 502, 503])])

    world.note_orders(
        [
            {"cmd": "move", "units": [501], "to": [50, 50]},
            {"cmd": "set_stance", "units": [502], "stance": "defend"},
            {"cmd": "attack_move", "units": [503], "to": [60, 60], "queued": True},
            {"cmd": "build", "item": "powr"},
        ]
    )
    at_tick(world, 105, units=(apc(),), events=lost([501, 502, 503]))

    assert world.cargo == {APC_ID: aboard([502, 503])}
    assert kinds(world) == ["unit_lost", "boarded", "boarded"]


def test_cargo_units_gone_in_the_state_their_transport_is_lost_in_are_lost_not_aboard(world):
    at_tick(world, 100, units=(apc(), *rockets()))
    world.note_orders([into_the_apc(ROCKET_IDS)])

    at_tick(world, 105, units=(), events=lost(ROCKET_IDS))

    assert (world.cargo, world.cargo_lost) == ({}, ())
    assert kinds(world) == ["unit_lost"] * 5


def test_cargo_when_the_apc_dies_with_5_aboard_the_5_count_lost_with_it(world):
    boarded(world)

    at_tick(world, 110, units=(), events=(event(6, "unit_lost", id=APC_ID, type="apc", x=40, y=40),))
    lost_then = world.cargo_lost
    at_tick(world, 115, units=())

    assert lost_then == aboard(ROCKET_IDS) and world.cargo_lost == ()
    assert world.cargo == {} and world.aboard(501) is None and world.transport_of(501) is None


def test_cargo_a_sixth_unit_ordered_in_with_the_five_is_not_taken_aboard(world):
    boarded(world, [*ROCKET_IDS, SIXTH_ROCKET])

    assert world.cargo == {APC_ID: aboard(ROCKET_IDS)}
    assert kinds(world) == ["boarded"] * 5 + ["unit_lost"] and world.aboard(SIXTH_ROCKET) is None


def test_cargo_a_sixth_unit_ordered_into_a_full_apc_later_is_not_taken_aboard(world):
    boarded(world)

    at_tick(world, 110, units=(apc(), *rockets([SIXTH_ROCKET])))
    world.note_orders([into_the_apc([SIXTH_ROCKET])])
    at_tick(world, 115, units=(apc(),), events=lost([SIXTH_ROCKET], first_seq=6))

    assert world.cargo == {APC_ID: aboard(ROCKET_IDS)} and kinds(world) == ["unit_lost"]


def test_cargo_the_transport_capacity_is_the_one_the_tuning_gives(items, game_config):
    world = World(items, game_config, WorldTuning(board_window_ticks=100, transport_capacity=3))

    boarded(world)

    assert world.cargo == {APC_ID: aboard(ROCKET_IDS[:3])}


def test_cargo_after_unload_a_new_unit_of_a_cargo_type_next_to_the_apc_takes_the_place_of_one_aboard(world):
    boarded(world)
    world.note_orders([UNLOAD_THE_APC])

    at_tick(
        world,
        110,
        units=(
            apc(),
            own_unit(601, "e3", 41, 40),
            own_unit(602, "e3", 38, 40),
            own_unit(603, "e1", 40, 41),
            own_unit(604, "e3", 50, 50),
        ),
    )

    assert world.taken_over == {501: 601, 502: 602}
    assert world.cargo == {APC_ID: aboard(ROCKET_IDS[2:])}


def test_cargo_a_new_unit_next_to_an_apc_that_was_not_ordered_to_unload_takes_no_place(world):
    boarded(world)

    at_tick(world, 110, units=(apc(), own_unit(601, "e3", 41, 40)))

    assert (world.taken_over, world.cargo) == ({}, {APC_ID: aboard(ROCKET_IDS)})


def test_cargo_unloaded_units_that_do_not_reappear_within_100_ticks_of_the_unload_leave_the_cargo(world):
    boarded(world)
    world.note_orders([UNLOAD_THE_APC])

    at_tick(world, 205, units=(apc(),))
    still_aboard = world.cargo
    at_tick(world, 206, units=(apc(),))

    assert still_aboard == {APC_ID: aboard(ROCKET_IDS)}
    assert (world.cargo, world.cargo_lost) == ({}, ())


def test_cargo_units_that_board_again_after_an_unload_are_aboard_for_good(world):
    boarded(world)
    world.note_orders([UNLOAD_THE_APC])
    at_tick(world, 110, units=(apc(), *rockets()))
    world.note_orders([into_the_apc(ROCKET_IDS)])

    at_tick(world, 115, units=(apc(),), events=lost(ROCKET_IDS, first_seq=6))
    at_tick(world, 400, units=(apc(),))

    assert world.cargo == {APC_ID: aboard(ROCKET_IDS)}


def walking_from_tick_0(world: World, unit_ids: tuple[int, ...] = (501,)) -> World:
    """The world at tick 0 with the rocket soldiers of these ids ordered into the APC."""
    at_tick(world, 0, units=(apc(), *rockets(list(unit_ids))))
    world.note_orders([into_the_apc(list(unit_ids))])
    return world


def test_cargo_a_unit_still_walking_100_ticks_after_its_order_is_ordered_in_again_and_boarding_at_150_is_aboard(
    world,
):
    walking_from_tick_0(world)
    at_tick(world, 95, units=(apc(), *rockets([501])))
    before_the_window_is_over = world.boarding_due()
    at_tick(world, 100, units=(apc(), *rockets([501])))
    due = world.boarding_due()
    world.note_orders(due)
    at_tick(world, 105, units=(apc(), *rockets([501])))
    after_the_second_order = world.boarding_due()

    at_tick(world, 150, units=(apc(),), events=lost([501]))

    assert (before_the_window_is_over, due, after_the_second_order) == ([], [into_the_apc([501])], [])
    assert world.cargo == {APC_ID: aboard([501])} and kinds(world) == ["boarded"]


def test_cargo_without_the_second_order_a_unit_that_boards_150_ticks_after_its_order_is_lost(world):
    walking_from_tick_0(world)
    at_tick(world, 100, units=(apc(), *rockets([501])))

    at_tick(world, 150, units=(apc(),), events=lost([501]))

    assert world.cargo == {} and kinds(world) == ["unit_lost"]


def test_cargo_boarding_orders_are_due_again_once_a_window_one_per_transport_for_the_units_still_outside(world):
    other_apc = own_unit(510, "apc", 30, 30)
    at_tick(world, 0, units=(apc(), other_apc, *rockets([501, 502, 503, 504])))
    world.note_orders([into_the_apc([503, 501]), {"cmd": "enter_transport", "units": [504], "target": 510}])
    world.note_orders([into_the_apc([502])])
    at_tick(world, 50, units=(apc(), other_apc, *rockets([501, 502, 503, 504])))
    world.note_orders([into_the_apc([502])])

    at_tick(world, 100, units=(apc(), other_apc, *rockets([501, 503, 504])), events=lost([502]))
    first = world.boarding_due()
    world.note_orders(first)
    at_tick(world, 195, units=(apc(), other_apc, *rockets([501, 503, 504])))
    too_soon = world.boarding_due()
    at_tick(world, 200, units=(apc(), other_apc, *rockets([501, 503, 504])))

    assert first == [into_the_apc([501, 503]), {"cmd": "enter_transport", "units": [504], "target": 510}]
    assert too_soon == [] and world.boarding_due() == first
    assert world.cargo == {APC_ID: aboard([502])}


def test_cargo_no_boarding_order_is_due_again_once_a_later_order_replaced_it_or_its_transport_is_lost(world):
    walking_from_tick_0(world, (501, 502))
    world.note_orders([{"cmd": "stop", "units": [501]}])
    at_tick(world, 100, units=(apc(), *rockets([501, 502])))
    after_the_stop = world.boarding_due()

    at_tick(world, 105, units=rockets([501, 502]), events=(event(1, "unit_lost", id=APC_ID),))

    assert after_the_stop == [into_the_apc([502])] and world.boarding_due() == []


def test_cargo_without_any_boarding_order_nothing_is_due(world):
    at_tick(world, 100, units=(apc(), *rockets()))
    at_tick(world, 1000, units=(apc(), *rockets()))

    assert world.boarding_due() == []


def test_cargo_two_worlds_share_no_cargo(items, game_config):
    first, second = World(items, game_config), World(items, game_config)

    boarded(first)
    at_tick(second, 100, units=(apc(), *rockets()))
    at_tick(second, 105, units=(apc(),), events=lost(ROCKET_IDS))

    assert first.cargo == {APC_ID: aboard(ROCKET_IDS)} and second.cargo == {}


def test_cargo_a_world_follows_the_fake_game_through_boarding_unloading_and_the_loss_of_the_apc(items):
    game = FakeRedAlert("russia", "england")
    world = World(items.with_game_config(game.game_config), game.game_config)
    transport = game.add_own("apc", *APC_CELL)
    soldiers = [game.add_own("e3", x, y) for x, y in [(40, 42), (41, 41), (39, 41), (42, 40), (38, 40)]]
    world.update(game.state())

    def send(*orders: dict, steps: int = 1) -> None:
        game.play_action({"type": "orders", "orders": list(orders)})
        world.note_orders(orders)
        for _ in range(steps):
            game.step()
            world.update(game.state())

    send({"cmd": "enter_transport", "units": soldiers, "target": transport})
    after_boarding = (dict(world.cargo), kinds(world))
    send({"cmd": "move", "units": [transport], "to": [50, 40]}, steps=20)
    send({"cmd": "unload", "units": [transport]})
    after_unloading = (dict(world.cargo), set(world.units) >= set(soldiers))
    send({"cmd": "enter_transport", "units": soldiers, "target": transport})
    game.kill_own(transport)
    game.step()
    world.update(game.state())

    assert [unit.id for unit in after_boarding[0][transport]] == soldiers
    assert after_boarding[1] == ["boarded"] * 5
    assert after_unloading == ({}, True)
    assert [unit.id for unit in world.cargo_lost] == soldiers and world.cargo == {}
    assert kinds(world) == ["unit_lost"] and world.events_new[0]["id"] == transport
