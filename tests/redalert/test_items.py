"""The item table: the fallback file read strictly, a match's rules put over it, names matched without case."""

from __future__ import annotations

import builtins
import csv
from dataclasses import replace
from pathlib import Path

import pytest

from situations import fixture_state
from ucla_agent.core.config import ConfigError
from ucla_agent.games.redalert import items, targeting
from ucla_agent.games.redalert.items import ALLIES, SOVIET, Item, ItemTable
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.world import World

REPO_ROOT = Path(__file__).resolve().parents[2]
KNOWN_QUEUES = {"Building", "Defense", "Infantry", "Vehicle", "Ship", "Aircraft"}
MINIMAL_FILE = """
[buildings.powr]
cost = 300
power = 100
queue = "Building"
prerequisites = []
hp = 40000
roles = ["power"]

[units.e1]
cost = 100
queue = "Infantry"
prerequisites = []
hp = 5000
speed = 1.32
range = 5.0
roles = ["infantry"]
"""
MINIMAL_DAMAGE_FILE = """
[powr]
armour = "wood"

[e1]
armour = "none"
range = 5.0
min_range = 0.0
dps = { none = 1875.0, light = 500.0, heavy = 125.0, wood = 375.0, concrete = 125.0 }
"""
AIR_DAMAGE_FILE = """
[sam]
armour = "heavy"
dps_air = 7500.0
air_range = 7.5

[yak]
armour = "light"
air = true
range = 5.0
min_range = 2.5
dps = { none = 66667.0, light = 40000.0, heavy = 16667.0, wood = 33333.0, concrete = 16667.0 }
"""
MATCHUP_REFERENCE = REPO_ROOT / "docs" / "research" / "red-alert-unit-matchups" / "items.csv"
# The aircraft of the damage file: every one an enemy can field but the hind, which the standard rules disable.
AIRCRAFT = ("heli", "mh60", "tran", "yak", "mig", "badr", "u2")


@pytest.fixture(scope="module")
def fallback() -> ItemTable:
    """The table of the tracked fallback file."""
    return ItemTable.from_file(REPO_ROOT)


@pytest.fixture(scope="module")
def tracked() -> ItemTable:
    """The agent's table: the tracked fallback file with the tracked damage file."""
    return ItemTable.from_tracked_files(REPO_ROOT)


def _write_items_file(root: Path, text: str) -> None:
    path = root / items.ITEMS_FILE
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")


def _write_damage_file(root: Path, text: str) -> None:
    path = root / items.DAMAGE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _rules(**sections: dict) -> dict:
    return {"rules": sections}


# -- the fallback file -----------------------------------------------------------------------------


def test_the_fallback_file_gives_every_fact_of_an_item(fallback):
    assert fallback["pbox"] == Item(
        name="pbox",
        is_building=True,
        cost=600,
        power=-20,
        queue="Defense",
        prerequisites=("tent",),
        factions=("england", "france", "germany"),
        hp=40000,
        speed=None,
        range=6.0,
        roles=frozenset({"defense", "anti_infantry"}),
    )


def test_a_unit_of_the_fallback_file_has_speed_and_no_power(fallback):
    rifle = fallback["e1"]

    assert (rifle.is_building, rifle.cost, rifle.power, rifle.queue) == (False, 100, 0, "Infantry")
    assert (rifle.hp, rifle.speed, rifle.range, rifle.factions) == (5000, 1.32, 5.0, ())


@pytest.mark.parametrize("name", ["PBOX", "Pbox", "pbox"])
def test_names_are_matched_without_regard_to_case(fallback, name):
    assert fallback[name].cost == 600
    assert fallback.get(name) is fallback["pbox"]


def test_an_unknown_type_is_none_from_get_and_a_key_error_from_indexing(fallback):
    assert fallback.get("no-such-item") is None
    with pytest.raises(KeyError):
        fallback["no-such-item"]


@pytest.mark.parametrize("variant, base", [("e1r1", "e1"), ("E3R1", "e3"), ("2tnkr12", "2tnk")])
def test_a_variant_of_a_listed_type_has_the_facts_of_that_type(fallback, variant, base):
    assert fallback.get(variant) is fallback[base]


@pytest.mark.parametrize("name", ["camera.spyplane", "hind", "e1x", "e1r", "r1", "e9r1"])
def test_a_type_that_is_no_variant_of_a_listed_one_has_no_facts(fallback, name):
    assert fallback.get(name) is None and fallback.get(name) is None


def test_indexing_takes_the_listed_name_only(fallback):
    with pytest.raises(KeyError):
        fallback["e1r1"]


@pytest.mark.parametrize(
    "variant, base",
    [("harv.frans", "harv"), ("E6.FRANS", "e6"), ("mh60.frans", "mh60"), ("heli.frans", "heli"), ("2tnk.x.y", "2tnk")],
)
def test_a_dotted_variant_of_a_listed_type_has_the_facts_of_that_type_and_is_read_under_its_name(
    fallback, variant, base
):
    assert fallback.get(variant) is fallback[base]
    assert fallback.base_name(variant) == base


@pytest.mark.parametrize("name", ["tecn", "tecn2", "zzz.frans", "camera.spyplane", ".frans", "harv."])
def test_a_dotted_name_whose_base_is_not_listed_has_no_facts_and_keeps_its_name(fallback, name):
    assert fallback.get(name) is None
    assert fallback.base_name(name) == name


@pytest.mark.parametrize("name, read_as", [("2TNK", "2tnk"), ("e1r1", "e1r1"), ("harv", "harv"), ("Tecn", "tecn")])
def test_a_listed_name_an_r_variant_and_an_unknown_name_are_read_under_their_own_lowercase_name(
    fallback, name, read_as
):
    assert fallback.base_name(name) == read_as


def test_a_stock_type_keeps_its_value_beside_a_dotted_variant_of_it(fallback):
    assert (fallback["2tnk"].cost, fallback.get("2tnk.frans").cost, fallback["harv"].cost) == (850, 850, 1100)


@pytest.mark.parametrize("name, seconds", [("e1", 2.4), ("powr", 7.2), ("proc", 33.6), ("weap", 48.0), ("mcv", 40.0)])
def test_build_time_is_24_ms_per_credit_except_where_the_file_states_one(fallback, name, seconds):
    assert fallback[name].build_time_s == pytest.approx(seconds)


def test_the_construction_yard_has_no_queue_so_no_faction_can_build_it(fallback):
    yard = fallback["fact"]

    assert yard.queue is None
    assert not yard.buildable_by("england") and not yard.buildable_by("russia")


@pytest.mark.parametrize(
    "name, faction, buildable",
    [
        ("tent", "england", True),
        ("tent", "russia", False),
        ("barr", "ukraine", True),
        ("barr", "France", False),
        ("powr", "germany", True),
        ("powr", "russia", True),
    ],
)
def test_an_item_is_buildable_by_the_factions_it_lists_or_by_all_when_it_lists_none(fallback, name, faction, buildable):
    assert fallback[name].buildable_by(faction) is buildable


def test_the_fallback_file_is_consistent_with_itself(fallback):
    buildings = {item.name for item in fallback if item.is_building}

    assert len(fallback) == 60
    for item in fallback:
        assert set(item.prerequisites) <= buildings, item.name
        assert set(item.factions) <= set(items.FACTION_FAMILIES), item.name
        assert item.queue is None or item.queue in KNOWN_QUEUES, item.name
        assert item.roles, item.name


def test_the_ship_queue_holds_the_ships_of_the_fallback_file(fallback):
    assert {item.name for item in fallback if item.queue == items.SHIP_QUEUE} == {"ss", "msub", "pt", "dd", "ca", "lst"}


def test_a_minimal_file_is_read(tmp_path):
    _write_items_file(tmp_path, MINIMAL_FILE)

    table = ItemTable.from_file(tmp_path)

    assert [item.name for item in table] == ["powr", "e1"]
    assert table["powr"].is_building and not table["e1"].is_building
    assert len(table.damage) == 0


def test_a_missing_file_is_a_config_error_naming_it(tmp_path):
    with pytest.raises(ConfigError, match="items.toml was not found"):
        ItemTable.from_file(tmp_path)


@pytest.mark.parametrize(
    "broken, message",
    [
        (MINIMAL_FILE.replace("cost = 300", "price = 300"), r"cost in \[buildings.powr\] must be a whole number"),
        (MINIMAL_FILE.replace("power = 100", "power = 100\npwer = 5"), r"\[buildings.powr\] has no setting named pwer"),
        (MINIMAL_FILE.replace("cost = 100", 'cost = "cheap"'), r"cost in \[units.e1\] must be a whole number"),
        (MINIMAL_FILE.replace("speed = 1.32", 'speed = "fast"'), r"speed in \[units.e1\] must be a number"),
        (MINIMAL_FILE.replace('roles = ["power"]', ""), r"roles in \[buildings.powr\] must be a list of strings"),
        (MINIMAL_FILE + "\n[ships.ss]\ncost = 950\n", r"the file has no setting named ships"),
        (MINIMAL_FILE.split("[units.e1]")[0], r"must have a \[units\] table"),
    ],
    ids=[
        "missing key",
        "unknown key",
        "text for a number",
        "text for a speed",
        "no roles",
        "unknown section",
        "no units",
    ],
)
def test_a_file_that_does_not_say_what_it_must_is_a_config_error(tmp_path, broken, message):
    _write_items_file(tmp_path, broken)

    with pytest.raises(ConfigError, match=message):
        ItemTable.from_file(tmp_path)


# -- a match's rules over the table ----------------------------------------------------------------


def test_a_value_the_game_config_gives_replaces_the_file_s_value_and_leaves_the_rest(fallback):
    table = fallback.with_game_config(_rules(buildings={"pbox": {"cost": 400}}))

    assert table["pbox"] == Item(
        name="pbox",
        is_building=True,
        cost=400,
        power=-20,
        queue="Defense",
        prerequisites=("tent",),
        factions=("england", "france", "germany"),
        hp=40000,
        range=6.0,
        roles=frozenset({"defense", "anti_infantry"}),
    )
    assert fallback["pbox"].cost == 600


def test_every_fact_a_rules_entry_states_is_taken(fallback):
    entry = {
        "cost": 120,
        "power": -5,
        "queue": "Infantry",
        "prerequisites": ["Barracks", "~techlevel.low"],
        "factions": ["Russia"],
        "hp": 6000,
        "speed": 2.5,
        "range": 5.5,
        "build_time_s": 3.5,
    }

    rifle = fallback.with_game_config(_rules(units={"e1": entry}))["e1"]

    assert (rifle.cost, rifle.power, rifle.queue, rifle.hp) == (120, -5, "Infantry", 6000)
    assert (rifle.prerequisites, rifle.factions) == (("barracks", "~techlevel.low"), ("russia",))
    assert (rifle.speed, rifle.range, rifle.build_time_s) == (2.5, 5.5, 3.5)
    assert rifle.roles == frozenset({"infantry", "anti_infantry"})


def test_an_item_only_the_rules_know_is_added(fallback):
    table = fallback.with_game_config(_rules(units={"SHOK": {"cost": 350, "queue": "Infantry"}}))

    assert table["shok"] == Item(name="shok", is_building=False, cost=350, queue="Infantry")
    assert len(table) == len(fallback) + 1


def test_item_names_in_the_rules_are_matched_without_regard_to_case(fallback):
    table = fallback.with_game_config(_rules(buildings={"PBOX": {"cost": 400}}))

    assert table["pbox"].cost == 400
    assert len(table) == len(fallback)


def test_a_cost_from_the_rules_changes_the_build_time_that_follows_from_it(fallback):
    table = fallback.with_game_config(_rules(buildings={"powr": {"cost": 400}}))

    assert table["powr"].build_time_s == pytest.approx(9.6)


def test_a_range_is_taken_from_the_weapon_when_the_entry_gives_it_there(fallback):
    table = fallback.with_game_config(_rules(units={"e1": {"weapon": {"name": "M1Carbine", "range": 5.25}}}))

    assert table["e1"].range == 5.25


def test_a_range_of_the_entry_itself_is_preferred_to_the_weapon_s(fallback):
    table = fallback.with_game_config(_rules(units={"e1": {"range": 4.0, "weapon": {"range": 9.0}}}))

    assert table["e1"].range == 4.0


@pytest.mark.parametrize("given, cells_per_second", [(54, 1.32), (164, 4.0), (223, 5.44), (1.5, 1.5), (4, 4)])
def test_a_speed_in_the_engine_s_own_unit_is_converted_to_cells_per_second(fallback, given, cells_per_second):
    table = fallback.with_game_config(_rules(units={"e1": {"speed": given}}))

    assert table["e1"].speed == cells_per_second


@pytest.mark.parametrize(
    "entry",
    [
        {"cost": "400"},
        {"cost": True},
        {"cost": None},
        {"hp": 40.5},
        {"queue": ""},
        {"prerequisites": "tent"},
        {"prerequisites": ["tent", 7]},
        {"range": "far"},
        {"weapon": "Vulcan"},
        {"weapon": {"range": None}},
        {"speed": "fast"},
        {"unknown_fact": 1},
    ],
)
def test_a_value_of_the_wrong_kind_changes_nothing(fallback, entry):
    table = fallback.with_game_config(_rules(buildings={"pbox": entry}))

    assert table["pbox"] == fallback["pbox"]


@pytest.mark.parametrize(
    "game_config",
    [
        None,
        {},
        {"rules": None},
        {"rules": {"buildings": None, "units": []}},
        {"rules": {"buildings": {"pbox": 5}}},
        "x",
    ],
    ids=["none", "empty", "no rules", "sections of the wrong kind", "an entry of the wrong kind", "not an object"],
)
def test_a_game_config_without_usable_rules_leaves_the_table_as_it_is(fallback, game_config):
    table = fallback.with_game_config(game_config)

    assert list(table) == list(fallback)


def test_a_table_can_be_built_from_rules_alone():
    table = ItemTable(()).with_game_config(
        _rules(buildings={"powr": {"cost": 300, "power": 100}}, units={"e1": {"cost": 100}})
    )

    assert [(item.name, item.is_building, item.cost, item.power) for item in table] == [
        ("powr", True, 300, 100),
        ("e1", False, 100, 0),
    ]


def test_putting_a_game_config_over_a_table_opens_no_file(fallback, monkeypatch):
    opened = []
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda *args, **kwargs: opened.append(args) or real_open(*args, **kwargs))
    monkeypatch.setattr(Path, "open", lambda self, *args, **kwargs: opened.append(self) or real_open(self, *args))

    fallback.with_game_config(_rules(buildings={"pbox": {"cost": 400}}))

    assert opened == []


# -- the damage table ------------------------------------------------------------------------------


def test_the_damage_file_holds_every_item_of_the_matchup_reference_with_its_values(tracked):
    with MATCHUP_REFERENCE.open(encoding="utf-8", newline="") as reference:
        rows = list(csv.DictReader(reference))

    damage = tracked.damage
    for row in rows:
        facts = damage.get(row["key"])
        assert facts.armour == row["armor"].lower(), row["key"]
        fights = row["combat"] == "True"
        assert facts.range == (float(row["max_range_cells"]) if fights else 0.0), row["key"]
        expected = {armour: float(row[f"dps_{armour}"]) for armour in items.ARMOUR_CLASSES} if fights else {}
        assert dict(facts.dps) == expected, row["key"]
        assert (facts.dps_air, facts.air) == (float(row["dps_air"]), False), row["key"]
    assert len(rows) == 52 and len(damage) == len(rows) + len(AIRCRAFT)


def test_a_rifle_does_1875_hp_a_second_to_infantry_in_the_hp_of_items_toml(tracked):
    rifle = tracked.damage.get("e1")

    assert (rifle.dps_against("none"), rifle.dps_against("heavy"), tracked["e1"].hp) == (1875.0, 125.0, 5000)


@pytest.mark.parametrize("name, min_range", [("arty", 4.0), ("v2rl", 4.0), ("e3", 0.5), ("4tnk", 0.0), ("e1", 0.0)])
def test_the_minimum_range_is_that_of_the_ground_weapons(tracked, name, min_range):
    assert tracked.damage.get(name).min_range == min_range


def test_an_item_that_does_not_fight_has_its_armour_and_does_no_damage(tracked):
    harvester = tracked.damage.get("harv")

    assert harvester == items.Damage("heavy") and harvester.dps_against("heavy") == 0.0


@pytest.mark.parametrize("name", ["zzz", "camera.spyplane", "hind"])
def test_an_item_the_damage_file_does_not_list_does_no_damage_and_wears_no_armour(tracked, name):
    assert tracked.damage.get(name) == items.NO_DAMAGE == items.Damage("none", {}, 0.0, 0.0, False, 0.0, 0.0)


def test_a_variant_hits_as_its_base_type_and_names_are_matched_without_case(tracked):
    assert tracked.damage.get("E3R1") == tracked.damage.get("e3") != items.NO_DAMAGE


def test_a_dotted_variant_hits_and_wears_armour_as_its_base_type_and_an_unknown_one_wears_infantry_s(tracked):
    assert tracked.damage.get("harv.frans") == tracked.damage.get("harv") != items.NO_DAMAGE
    assert tracked.damage.get("harv.frans").armour == "heavy"
    assert tracked.damage.get("tecn") == tracked.damage.get("zzz.frans") == items.NO_DAMAGE
    assert tracked.damage.get("tecn").armour == tracked.damage.get("e1").armour == "none"


def test_a_weapon_range_of_the_rules_replaces_the_damage_file_s_range_and_nothing_else(tracked):
    table = tracked.with_game_config(_rules(units={"E1": {"weapon": {"range": 3.0}, "cost": 90}}))

    assert table.damage.get("e1") == replace(tracked.damage.get("e1"), range=3.0)
    assert table.damage.get("e3") == tracked.damage.get("e3")


@pytest.mark.parametrize(
    "entry",
    [{"range": 3.0}, {"weapon": {"range": "far"}}, {"weapon": "M1Carbine"}, {"weapon": {"damage": 9000}}, 5],
    ids=["a range of the entry itself", "text for a range", "a weapon by name", "a weapon's damage", "no entry"],
)
def test_only_a_weapon_range_that_is_a_number_changes_the_damage_table(tracked, entry):
    table = tracked.with_game_config(_rules(units={"e1": entry}))

    assert table.damage.get("e1") == tracked.damage.get("e1")


def test_a_weapon_range_for_an_item_the_damage_file_does_not_list_gives_it_that_range_and_no_damage(tracked):
    table = tracked.with_game_config(_rules(units={"hind": {"weapon": {"range": 5.0}}}))

    assert table.damage.get("hind") == items.Damage(range=5.0)


# The entries of the types with an anti-air weapon as the rules of a watched game list them: one weapon each, the
# anti-air one, its range given twice.
WATCHED_ANTI_AIR_RULES = {
    "e3": {"cost": 300, "queue": "Infantry", "weapon": {"name": "RedEye", "range": 7.5}, "range": 7.5},
    "ftrk": {"cost": 600, "queue": "Vehicle", "weapon": {"name": "FLAK-23-AA", "range": 8.0}, "range": 8.0},
    "heli": {"cost": 2000, "queue": "Aircraft", "weapon": {"name": "HellfireAA", "range": 4.0}, "range": 4.0},
    "4tnk": {"cost": 2000, "queue": "Vehicle", "weapon": {"name": "120mm", "range": 4.75}, "range": 4.75},
}


@pytest.mark.parametrize(
    "name, ground_range, air_range",
    [("e3", 5.0, 7.5), ("ftrk", 6.0, 8.0), ("heli", 5.0, 4.0)],
)
def test_ground_range_stays_the_ground_weapon_s_when_the_rules_give_the_anti_air_weapon_s_range(
    tracked, name, ground_range, air_range
):
    table = tracked.with_game_config(_rules(units=WATCHED_ANTI_AIR_RULES))

    assert (table.damage.get(name).range, table.damage.get(name).air_range) == (ground_range, air_range)
    assert table.damage.get(name) == tracked.damage.get(name)


def test_ground_range_and_air_range_of_one_weapon_that_hits_both_are_the_rules_range():
    damage = items.DamageTable({"flak": items.Damage("light", {"none": 900.0}, 6.5, dps_air=900.0, air_range=6.5)})

    facts = damage.with_game_config(_rules(units={"flak": {"weapon": {"range": 6.5}}})).get("flak")

    assert (facts.range, facts.air_range) == (6.5, 6.5)


@pytest.mark.parametrize(
    "name, weapon_range, ground_range, air_range",
    [("4tnk", 4.75, 4.75, 6.5), ("e3", 6.0, 6.0, 7.5), ("e1", 3.0, 3.0, 0.0)],
)
def test_ground_range_is_a_rules_range_that_matches_neither_file_range_and_air_range_stays_the_file_s(
    tracked, name, weapon_range, ground_range, air_range
):
    entry = WATCHED_ANTI_AIR_RULES.get(name, {}) | {"weapon": {"range": weapon_range}}

    facts = tracked.with_game_config(_rules(units={name: entry})).damage.get(name)

    assert (facts.range, facts.air_range) == (ground_range, air_range)


@pytest.mark.parametrize("name", ["sam", "agun"])
def test_ground_range_of_a_defense_with_an_anti_air_weapon_alone_is_none_with_its_rules_range(tracked, name):
    weapon_range = tracked.damage.get(name).air_range

    facts = tracked.with_game_config(_rules(buildings={name: {"weapon": {"range": weapon_range}}})).damage.get(name)

    assert (facts.range, facts.air_range, facts.hits_ground) == (0.0, weapon_range, False)


def test_ground_range_none_gives_a_unit_with_an_anti_air_weapon_alone_no_ground_pairing_in_targeting(tracked):
    flak = Item("flak", is_building=False, cost=600, queue="Vehicle", roles=frozenset({"anti_air"}))
    anti_air_only = items.Damage("light", dps_air=3000.0, air_range=8.0)
    damage = items.DamageTable({"flak": anti_air_only, **{name: tracked.damage.get(name) for name in ("e1", "yak")}})
    table = ItemTable([*tracked, flak], damage).with_game_config(_rules(units={"flak": {"weapon": {"range": 8.0}}}))
    world = World(table)
    # A rifle 6 cells off, within the anti-air range of 8, and an aircraft 7 cells off.
    world.update(
        fixture_state(
            "opening_mcv",
            100,
            units=[{"id": 300, "type": "flak", "x": 40, "y": 48, "hp": 100, "act": None, "idle": True}],
            visible_enemies=[
                {"id": 500, "type": "e1", "x": 46, "y": 48, "hp": 100},
                {"id": 501, "type": "yak", "x": 47, "y": 48, "hp": 100},
            ],
        )
    )
    flak_unit, rifle, aircraft = world.units[300], world.enemy_memory[500], world.enemy_memory[501]

    assigned = targeting.assign([flak_unit], [rifle, aircraft], world, Tuning())

    assert table.damage.get("flak").range == 0.0
    assert targeting.assign([flak_unit], [rifle], world, Tuning()) == []
    assert [(assignment.target.id, assignment.attackers) for assignment in assigned] == [(501, (300,))]
    assert not targeting.is_in_contact(flak_unit, [rifle], world)


@pytest.mark.parametrize("name, ground_range", [("e3", 5.0), ("ftrk", 6.0), ("heli", 5.0)])
def test_item_ground_range_of_a_unit_whose_rules_range_is_its_anti_air_weapon_s_is_the_damage_table_s(
    tracked, name, ground_range
):
    table = tracked.with_game_config(_rules(units=WATCHED_ANTI_AIR_RULES))

    assert table[name].range == table.damage.get(name).range == ground_range


def test_item_ground_range_of_4tnk_stays_the_rules_4_75_though_the_damage_file_gives_6_5(tracked):
    table = tracked.with_game_config(_rules(units=WATCHED_ANTI_AIR_RULES))

    assert (tracked["4tnk"].range, tracked.damage.get("4tnk").range) == (4.75, 6.5)
    assert table["4tnk"].range == 4.75


@pytest.mark.parametrize(
    "name, entry, item_range",
    [
        ("e3", {"range": 6.0}, 6.0),
        ("e3", {"weapon": {"range": 6.0}}, 6.0),
        ("e1", {"weapon": {"range": 3.0}}, 3.0),
        ("hind", {"weapon": {"range": 7.5}}, 7.5),
    ],
)
def test_item_ground_range_is_a_rules_range_that_matches_neither_damage_file_range(tracked, name, entry, item_range):
    assert tracked.with_game_config(_rules(units={name: entry}))[name].range == item_range


def test_item_ground_range_follows_the_entry_s_own_range_before_the_weapon_s(tracked):
    entry = {"range": 7.5, "weapon": {"name": "Dragon", "range": 9.0}}

    assert tracked.with_game_config(_rules(units={"e3": entry}))["e3"].range == 5.0


@pytest.mark.parametrize("name", ["sam", "agun"])
def test_item_ground_range_of_an_anti_air_defense_is_its_rules_range(tracked, name):
    weapon_range = tracked.damage.get(name).air_range

    table = tracked.with_game_config(_rules(buildings={name: {"weapon": {"range": weapon_range}}}))

    assert (table[name].range, table.damage.get(name).range) == (weapon_range, 0.0)


def test_item_ground_range_of_a_table_without_damage_facts_is_the_rules_range(fallback):
    assert fallback.with_game_config(_rules(units=WATCHED_ANTI_AIR_RULES))["e3"].range == 7.5


def test_the_damage_file_loads_every_listed_aircraft_as_air_with_light_armour(tracked):
    for name in AIRCRAFT:
        facts = tracked.damage.get(name)
        assert (facts.air, facts.armour) == (True, "light"), name
    assert tracked.damage.get("hind") == items.NO_DAMAGE


# The ground dps by armour class (none, light, heavy, wood, concrete) of each armed aircraft, computed from its weapon
# rules by the matchup method: Damage x Versus x hit factor x Burst x 25 / (ReloadDelay + (Burst - 1) x BurstDelay).
@pytest.mark.parametrize(
    "name, weapon_range, min_range, dps",
    [
        ("heli", 5.0, 1.25, (1973.0, 6136.0, 6818.0, 6136.0, 6818.0)),
        ("mh60", 5.0, 0.75, (18000.0, 9000.0, 3500.0, 7500.0, 3500.0)),
        ("yak", 5.0, 2.5, (66667.0, 40000.0, 16667.0, 33333.0, 16667.0)),
        ("mig", 9.0, 2.0, (1321.0, 5525.0, 7060.0, 5526.0, 6140.0)),
    ],
)
def test_an_armed_aircraft_does_the_ground_dps_of_its_weapon_rules(tracked, name, weapon_range, min_range, dps):
    facts = tracked.damage.get(name)

    assert (facts.range, facts.min_range) == (weapon_range, min_range)
    assert tuple(facts.dps_against(armour) for armour in items.ARMOUR_CLASSES) == dps
    assert facts.hits_ground


@pytest.mark.parametrize("name", ["tran", "badr", "u2"])
def test_an_unarmed_aircraft_hurts_nothing_on_the_ground_or_in_the_air(tracked, name):
    facts = tracked.damage.get(name)

    assert facts == items.Damage("light", air=True) and not facts.hits_ground


@pytest.mark.parametrize(
    "name, dps_air, air_range",
    [
        ("e3", 1200.0, 7.5),
        ("ftrk", 3000.0, 8.0),
        ("4tnk", 2308.0, 6.5),
        ("agun", 7500.0, 10.0),
        ("sam", 7500.0, 7.5),
        ("heli", 3750.0, 4.0),
        ("e1", 0.0, 0.0),
        ("2tnk", 0.0, 0.0),
    ],
)
def test_the_anti_air_dps_and_range_are_those_of_the_anti_air_weapon_and_0_without_one(
    tracked, name, dps_air, air_range
):
    facts = tracked.damage.get(name)

    assert (facts.dps_air, facts.air_range, facts.air) == (dps_air, air_range, name == "heli")


def test_against_an_aircraft_a_type_does_its_anti_air_dps_at_its_anti_air_range_and_against_the_rest_its_ground_dps(
    tracked,
):
    rocket, rifle, yak = (tracked.damage.get(name) for name in ("e3", "e1", "yak"))

    assert (rocket.dps_to(yak), rocket.range_to(yak)) == (1200.0, 7.5)
    assert (rocket.dps_to(rifle), rocket.range_to(rifle)) == (241.0, 5.0)
    assert (rifle.dps_to(yak), rifle.dps_to(rifle)) == (0.0, 1875.0)


def test_every_listed_aircraft_has_the_hp_of_its_rules_and_the_support_powers_planes_cost_nothing(fallback):
    assert {name: fallback[name].hp for name in AIRCRAFT} == {
        "heli": 12000,
        "mh60": 10000,
        "tran": 14000,
        "yak": 6000,
        "mig": 8000,
        "badr": 40000,
        "u2": 200000,
    }
    assert [(fallback[name].cost, fallback[name].queue) for name in ("badr", "u2")] == [(0, None), (0, None)]


def test_a_damage_file_s_air_keys_are_read_and_a_type_without_them_is_a_ground_type_without_anti_air(tmp_path):
    _write_damage_file(tmp_path, MINIMAL_DAMAGE_FILE + AIR_DAMAGE_FILE)

    table = items.DamageTable.from_file(tmp_path)

    assert table.get("sam") == items.Damage("heavy", dps_air=7500.0, air_range=7.5)
    assert (table.get("yak").air, table.get("yak").min_range, table.get("yak").dps_air) == (True, 2.5, 0.0)
    assert (table.get("e1").air, table.get("e1").dps_air, table.get("e1").air_range) == (False, 0.0, 0.0)


def test_a_table_made_without_a_damage_table_does_no_damage():
    assert len(ItemTable(()).damage) == 0 and ItemTable(()).damage.get("e1") == items.NO_DAMAGE


def test_the_agent_s_table_is_the_fallback_file_s_with_the_damage_file_read_alone(tracked, fallback):
    assert list(tracked) == list(fallback)
    assert tracked.damage.get("2tnk") == items.DamageTable.from_file(REPO_ROOT).get("2tnk") != items.NO_DAMAGE


def test_the_agent_s_table_reads_the_damage_file_from_the_same_root(tmp_path):
    _write_items_file(tmp_path, MINIMAL_FILE)
    _write_damage_file(tmp_path, MINIMAL_DAMAGE_FILE)

    table = ItemTable.from_tracked_files(tmp_path)

    assert [item.name for item in table] == ["powr", "e1"]
    assert (table.damage.get("powr").armour, table.damage.get("e1").range, len(table.damage)) == ("wood", 5.0, 2)


def test_a_missing_damage_file_is_a_config_error_naming_it(tmp_path):
    _write_items_file(tmp_path, MINIMAL_FILE)

    with pytest.raises(ConfigError, match="damage.toml was not found"):
        ItemTable.from_tracked_files(tmp_path)


@pytest.mark.parametrize(
    "broken, message",
    [
        (MINIMAL_DAMAGE_FILE.replace('"wood"', '"brick"'), r"armour in \[powr\] must be \"none\" or"),
        (MINIMAL_DAMAGE_FILE.replace(", concrete = 125.0", ""), r"concrete in \[e1.dps\] must be a number"),
        (
            MINIMAL_DAMAGE_FILE.replace("wood = 375.0", "wood = 375.0, air = 0.0"),
            r"\[e1.dps\] has no setting named air",
        ),
        (MINIMAL_DAMAGE_FILE.replace("range = 5.0\n", ""), r"range in \[e1\] must be a number"),
        (MINIMAL_DAMAGE_FILE.replace("min_range = 0.0", "min_range = -1.0"), r"min_range in \[e1\] must be a number"),
        (MINIMAL_DAMAGE_FILE.replace('armour = "wood"', 'armour = "wood"\nrange = 6.0'), r"\[powr\] has no setting"),
        ('rifle = "e1"\n' + MINIMAL_DAMAGE_FILE, r"must have a \[rifle\] table"),
        (MINIMAL_DAMAGE_FILE.replace('"wood"', '"wood"\nair = "yes"'), r"air in \[powr\] must be true or false"),
        (MINIMAL_DAMAGE_FILE.replace('"wood"', '"wood"\ndps_air = 900.0'), r"air_range in \[powr\] must be a number"),
        (MINIMAL_DAMAGE_FILE.replace('"wood"', '"wood"\nair_range = 7.5'), r"\[powr\] has no setting named air_range"),
    ],
    ids=[
        "unknown armour",
        "an armour class left out",
        "unknown armour class",
        "no range",
        "a negative minimum range",
        "a range without dps",
        "a value for an item",
        "air not true or false",
        "anti-air dps without its range",
        "anti-air range without its dps",
    ],
)
def test_a_damage_file_that_does_not_say_what_it_must_is_a_config_error(tmp_path, broken, message):
    _write_damage_file(tmp_path, broken)

    with pytest.raises(ConfigError, match=message):
        items.DamageTable.from_file(tmp_path)


# -- the vocabulary shared with the world model and the fake game ----------------------------------


def test_each_faction_belongs_to_a_family():
    assert items.FACTION_FAMILIES == {
        "england": ALLIES,
        "france": ALLIES,
        "germany": ALLIES,
        "russia": SOVIET,
        "ukraine": SOVIET,
    }
    assert (ALLIES, SOVIET, items.ALLIED_BARRACKS) == ("allies", "soviet", "tent")


def test_the_non_combat_types_are_the_seven_the_world_model_names():
    assert items.NON_COMBAT_TYPES == {"mcv", "harv", "e6", "medi", "mech", "thf", "spy"}
