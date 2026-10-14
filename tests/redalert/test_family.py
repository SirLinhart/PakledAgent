"""The family table: the names each family orders by, checked against the tracked item facts."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from ucla_agent.games.redalert import family
from ucla_agent.games.redalert.family import FAMILIES, Family
from ucla_agent.games.redalert.items import ALLIES, FACTION_FAMILIES, SOVIET, ItemTable

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_NAMES = (
    family.CONSTRUCTION_YARD,
    family.MCV,
    family.POWER_PLANT,
    family.ADVANCED_POWER_PLANT,
    family.REFINERY,
    family.WAR_FACTORY,
    family.SERVICE_DEPOT,
    family.RADAR,
    family.HARVESTER,
    family.RIFLE_INFANTRY,
    family.ROCKET_INFANTRY,
)


@pytest.fixture(scope="module")
def items() -> ItemTable:
    return ItemTable.from_file(REPO_ROOT)


def names_of(table: Family) -> set[str]:
    """Every item name a family's table holds."""
    names: set[str] = set()
    for value in dataclasses.astuple(table):
        names.update(value if isinstance(value, tuple) else [value])
    return names - {None}


def test_there_is_a_table_for_each_family_a_faction_can_have():
    assert set(FAMILIES) == set(FACTION_FAMILIES.values()) == {ALLIES, SOVIET}


def test_the_allied_names_are_the_ones_the_requirements_give():
    assert FAMILIES[ALLIES] == Family(
        barracks="tent",
        anti_infantry_defense="pbox",
        anti_armor_defense="gun",
        opening_anti_armor_defense="gun",
        light_vehicle="1tnk",
        heavy_vehicle="2tnk",
        raid_vehicles=("1tnk", "jeep"),
        raid_infantry=None,
    )


def test_the_soviet_names_are_the_ones_the_requirements_give():
    assert FAMILIES[SOVIET] == Family(
        barracks="barr",
        anti_infantry_defense="ftur",
        anti_armor_defense="tsla",
        opening_anti_armor_defense=None,
        light_vehicle="ftrk",
        heavy_vehicle="3tnk",
        raid_vehicles=(),
        raid_infantry="e2",
    )


def test_the_soviet_defense_against_armor_is_the_tesla_coil_which_needs_a_war_factory_only(items):
    coil = items[FAMILIES[SOVIET].anti_armor_defense]

    assert (coil.name, coil.prerequisites, coil.cost, coil.power) == ("tsla", ("weap",), 1200, -80)
    assert FAMILIES[SOVIET].opening_anti_armor_defense is None


def test_the_names_both_families_share_are_the_platform_s():
    assert SHARED_NAMES == ("fact", "mcv", "powr", "apwr", "proc", "weap", "fix", "dome", "harv", "e1", "e3")


def test_the_barracks_types_are_those_of_the_two_families():
    assert family.BARRACKS_TYPES == {"tent", "barr"}


def factions_of(name: str) -> list[str]:
    return [faction for faction, of_family in FACTION_FAMILIES.items() if of_family == name]


def cannot_produce(items: ItemTable, names: set[str], factions: list[str]) -> set[tuple[str, str]]:
    """Every (item, faction) of the given ones for which the item table says the faction cannot produce the item."""
    return {(item, faction) for item in names for faction in factions if not items[item].buildable_by(faction)}


def queues_of(items: ItemTable, *names: str | None) -> set[str | None]:
    """The production queues of the items named; a name a family does not have is left out."""
    return {items[name].queue for name in names if name}


@pytest.mark.parametrize("name", [ALLIES, SOVIET])
def test_every_name_of_a_family_is_an_item_each_faction_of_the_family_can_produce(items, name):
    factions = factions_of(name)

    assert len(factions) >= 2
    assert cannot_produce(items, names_of(FAMILIES[name]), factions) == set()


@pytest.mark.parametrize("faction", sorted(FACTION_FAMILIES))
def test_every_shared_name_but_the_construction_yard_can_be_produced_by_every_faction(items, faction):
    produced = [name for name in SHARED_NAMES if items[name].buildable_by(faction)]

    assert produced == [name for name in SHARED_NAMES if name != family.CONSTRUCTION_YARD]


@pytest.mark.parametrize("name", [ALLIES, SOVIET])
def test_a_family_s_defenses_come_from_the_defense_queue_and_its_barracks_from_the_building_queue(items, name):
    table = FAMILIES[name]

    defenses = (table.anti_infantry_defense, table.anti_armor_defense, table.opening_anti_armor_defense)
    assert queues_of(items, *defenses) == {"Defense"}
    assert queues_of(items, table.barracks) == {"Building"}
    assert queues_of(items, table.raid_infantry) <= {"Infantry"}
    assert queues_of(items, table.light_vehicle, table.heavy_vehicle, *table.raid_vehicles) == {"Vehicle"}
