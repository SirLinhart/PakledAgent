"""Intents and batches: priority classes, the check of each order against the current world, and packing."""

from __future__ import annotations

import copy
import gc
import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Iterable

import pytest

from fake_game import FakeRedAlert
from situations import ScriptedManager, ask, fixture_state, new_agent
from ucla_agent.games.redalert.batch import (
    DROPPED_CLASSES,
    ORDER_FIELDS,
    Intent,
    Limits,
    Packed,
    Priority,
    command_count,
    pack,
    packing_rank,
    validated,
)
from ucla_agent.games.redalert.fight import centroid
from ucla_agent.games.redalert.items import ItemTable
from ucla_agent.games.redalert.ledger import OrderLedger
from ucla_agent.games.redalert.targeting import FocusFire
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.world import World

REPO_ROOT = Path(__file__).resolve().parents[2]
STATES_FOLDER = REPO_ROOT / "tests" / "fixtures" / "redalert" / "states"
LIMITS = Limits()
# In the Allied mid-game fixture: the scouting jeep sees these three enemies near the enemy base.
ENEMY_RIFLEMAN = 123
ENEMY_FLAK_TRUCK = 124
ENEMY_FLAME_TOWER = 122
UNKNOWN_ID = 99999


def load_state(name: str) -> dict:
    return json.loads((STATES_FOLDER / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def items() -> ItemTable:
    return ItemTable.from_tracked_files(REPO_ROOT)


@pytest.fixture(scope="module")
def game_config() -> dict:
    return FakeRedAlert().game_config


@pytest.fixture
def world(items: ItemTable, game_config: dict) -> World:
    """The world of the Allied mid-game fixture: 13 units, 7 buildings, a `pbox` to place, three enemies in sight."""
    allied = World(items, game_config)
    allied.update(load_state("midgame_allies"))
    return allied


def ids_of(world: World, actor_type: str) -> list[int]:
    """The ids of the world's own units or buildings of one type."""
    actors = {**world.units, **world.buildings}
    return [actor_id for actor_id, actor in actors.items() if actor.type == actor_type]


def check(world: World, order: dict, priority: Priority = Priority.MOVE) -> dict | None:
    """What `validated` makes of one order in that world, under the usual limits."""
    return validated(Intent(priority, order), world, LIMITS)


# -- priorities and limits -------------------------------------------------------------------------


def test_the_priority_classes_run_from_emergency_to_scout():
    assert sorted(Priority) == [
        Priority.EMERGENCY,
        Priority.PLACEMENT,
        Priority.POWER,
        Priority.PRODUCTION,
        Priority.CONTACT,
        Priority.SCREEN,
        Priority.MOVE,
        Priority.SCOUT,
    ]
    assert [priority.name.lower() for priority in sorted(Priority)] == [
        "emergency",
        "placement",
        "power",
        "production",
        "contact",
        "screen",
        "move",
        "scout",
    ]


def test_the_usual_limits_are_20_orders_50_units_and_200_engine_commands():
    assert Limits() == Limits(
        max_orders_per_batch=20, max_units_per_order=50, max_engine_commands=200, max_train_count=10
    )


def test_the_limits_are_read_from_the_state_s_limits_object():
    state = {
        "limits": {
            "max_orders_per_batch": 12,
            "max_units_per_order": 30,
            "max_engine_commands": 90,
            "max_train_count": 5,
        }
    }

    assert Limits.from_state(state) == Limits(
        max_orders_per_batch=12, max_units_per_order=30, max_engine_commands=90, max_train_count=5
    )


def test_a_fixture_state_gives_the_usual_limits_as_it_names_no_command_limit():
    state = load_state("midgame_allies")
    assert "max_engine_commands" not in state["limits"]

    assert Limits.from_state(state) == Limits()


@pytest.mark.parametrize(
    "given",
    [
        None,
        {},
        [],
        {"max_orders_per_batch": 0},
        {"max_orders_per_batch": "20"},
        {"max_orders_per_batch": True},
        {"other": 3},
    ],
    ids=["none", "empty", "a list", "zero", "text", "a boolean", "an unknown limit"],
)
def test_a_limit_the_state_does_not_give_a_usable_value_for_keeps_its_usual_value(given):
    assert Limits.from_state({"limits": given}) == Limits()
    assert Limits.from_state({}) == Limits()


def test_a_state_may_give_some_limits_and_leave_the_others_usual():
    assert Limits.from_state({"limits": {"max_units_per_order": 25}}) == Limits(max_units_per_order=25)


@pytest.mark.parametrize(
    "name, given, taken",
    [
        ("max_orders_per_batch", 19, 19),
        ("max_orders_per_batch", 20, 20),
        ("max_orders_per_batch", 21, 20),
        ("max_units_per_order", 500, 50),
        ("max_engine_commands", 199, 199),
        ("max_engine_commands", 5000, 200),
        ("max_train_count", 99, 10),
    ],
)
def test_a_state_can_lower_a_limit_but_never_raise_it_above_the_usual_one(name, given, taken):
    assert getattr(Limits.from_state({"limits": {name: given}}), name) == taken


# -- unit orders -----------------------------------------------------------------------------------


def test_a_unit_order_for_own_living_units_comes_back_as_it_was(world):
    rifles = ids_of(world, "e1")
    order = {"cmd": "attack_move", "units": rifles, "to": [40, 48]}

    assert check(world, order) == order
    assert check(world, order) is not order


def test_ids_that_are_not_own_living_units_are_dropped_from_a_unit_order(world):
    rifles = ids_of(world, "e1")

    kept = check(world, {"cmd": "move", "units": [rifles[0], UNKNOWN_ID, ENEMY_RIFLEMAN, rifles[1]], "to": [40, 48]})

    assert kept == {"cmd": "move", "units": rifles[:2], "to": [40, 48]}


def test_a_wreck_is_not_a_unit_that_takes_orders(items, game_config):
    world = World(items, game_config)
    state = load_state("wreck")
    world.update(state)
    wreck_id = next(unit["id"] for unit in state["observation"]["units"] if unit.get("wreck"))
    rifle = ids_of(world, "e1")[0]

    assert check(world, {"cmd": "move", "units": [wreck_id, rifle], "to": [30, 48]})["units"] == [rifle]
    assert check(world, {"cmd": "move", "units": [wreck_id], "to": [30, 48]}) is None


def test_an_order_left_with_no_unit_is_discarded(world):
    assert check(world, {"cmd": "move", "units": [UNKNOWN_ID, ENEMY_RIFLEMAN], "to": [40, 48]}) is None
    assert check(world, {"cmd": "stop", "units": []}) is None


def test_a_unit_named_twice_is_ordered_once(world):
    rifles = ids_of(world, "e1")

    assert check(world, {"cmd": "stop", "units": [rifles[0], rifles[1], rifles[0]]})["units"] == rifles[:2]


def test_a_building_takes_stop_stance_attack_and_guard_but_no_other_unit_order(world):
    pillbox, rifle = ids_of(world, "pbox")[0], ids_of(world, "e1")[0]

    assert check(world, {"cmd": "stop", "units": [pillbox]})["units"] == [pillbox]
    assert check(world, {"cmd": "set_stance", "units": [pillbox], "stance": "hold_fire"})["units"] == [pillbox]
    assert check(world, {"cmd": "attack", "units": [pillbox, rifle], "target": ENEMY_RIFLEMAN})["units"] == [
        pillbox,
        rifle,
    ]
    assert check(world, {"cmd": "guard", "units": [pillbox], "target": rifle})["units"] == [pillbox]
    assert check(world, {"cmd": "move", "units": [pillbox, rifle], "to": [40, 48]})["units"] == [rifle]
    assert check(world, {"cmd": "harvest", "units": [pillbox]}) is None


@pytest.mark.parametrize("cell, sendable", [([0, 0], True), ([95, 95], True), ([96, 48], False), ([48, -1], False)])
def test_a_cell_must_lie_inside_the_map_bounds(world, cell, sendable):
    rifles = ids_of(world, "e1")

    for cmd in ("move", "attack_move", "attack", "harvest"):
        assert (check(world, {"cmd": cmd, "units": rifles, "to": cell}) is not None) is sendable, cmd


@pytest.mark.parametrize("cell", [None, [40], [40, 48, 2], "40,48", 40])
def test_a_cell_that_is_not_a_pair_of_coordinates_is_no_cell(world, cell):
    assert check(world, {"cmd": "move", "units": ids_of(world, "e1"), "to": cell}) is None


def test_a_harvest_order_needs_no_cell(world):
    harvester = ids_of(world, "harv")

    assert check(world, {"cmd": "harvest", "units": harvester}) == {"cmd": "harvest", "units": harvester}


def test_an_attack_on_an_enemy_in_sight_can_be_sent(world):
    rifles = ids_of(world, "e1")

    for target in (ENEMY_RIFLEMAN, ENEMY_FLAK_TRUCK, ENEMY_FLAME_TOWER):
        assert check(world, {"cmd": "attack", "units": rifles, "target": target}) == {
            "cmd": "attack",
            "units": rifles,
            "target": target,
        }


def test_an_attack_on_an_enemy_that_is_remembered_but_no_longer_in_sight_is_discarded(world):
    later = load_state("midgame_allies")
    later["observation"]["tick"] += 5
    later["observation"]["visible_enemies"] = []
    world.update(later)
    assert world.enemy_memory[ENEMY_RIFLEMAN].visible is False

    assert check(world, {"cmd": "attack", "units": ids_of(world, "e1"), "target": ENEMY_RIFLEMAN}) is None
    assert check(world, {"cmd": "attack", "units": ids_of(world, "e1"), "target": ENEMY_FLAME_TOWER}) is not None


def test_an_attack_on_an_id_never_seen_or_on_an_own_unit_is_discarded(world):
    rifles = ids_of(world, "e1")

    assert check(world, {"cmd": "attack", "units": rifles, "target": UNKNOWN_ID}) is None
    assert check(world, {"cmd": "attack", "units": rifles[:1], "target": rifles[1]}) is None


def test_an_attack_names_a_target_or_a_cell_and_not_both(world):
    rifles = ids_of(world, "e1")

    assert check(world, {"cmd": "attack", "units": rifles}) is None
    assert check(world, {"cmd": "attack", "units": rifles, "target": ENEMY_RIFLEMAN, "to": [69, 47]}) is None
    assert check(world, {"cmd": "attack", "units": rifles, "to": [69, 47]}) is not None


def test_guard_and_enter_transport_need_an_own_actor_as_target(world):
    rifles, barracks = ids_of(world, "e1"), ids_of(world, "tent")[0]

    assert check(world, {"cmd": "guard", "units": rifles[:1], "target": barracks}) is not None
    assert check(world, {"cmd": "enter_transport", "units": rifles[:1], "target": rifles[1]}) is not None
    assert check(world, {"cmd": "guard", "units": rifles[:1], "target": ENEMY_RIFLEMAN}) is None
    assert check(world, {"cmd": "enter_transport", "units": rifles[:1], "target": UNKNOWN_ID}) is None


def test_a_stance_must_be_one_of_the_four(world):
    rifles = ids_of(world, "e1")

    for stance in ("hold_fire", "return_fire", "defend", "attack_anything"):
        assert check(world, {"cmd": "set_stance", "units": rifles, "stance": stance}) is not None
    assert check(world, {"cmd": "set_stance", "units": rifles, "stance": "berserk"}) is None


def test_a_queued_unit_order_keeps_its_flag(world):
    order = {"cmd": "move", "units": ids_of(world, "jeep"), "to": [50, 50], "queued": True}

    assert check(world, order) == order


# -- production and placement ----------------------------------------------------------------------


def test_build_and_train_need_an_item_that_is_on_offer_now(world):
    assert check(world, {"cmd": "build", "item": "powr"}) == {"cmd": "build", "item": "powr"}
    assert check(world, {"cmd": "train", "item": "e1", "count": 3}) == {"cmd": "train", "item": "e1", "count": 3}
    assert check(world, {"cmd": "train", "item": "1tnk"}) == {"cmd": "train", "item": "1tnk"}
    assert check(world, {"cmd": "build", "item": "barr"}) is None
    assert check(world, {"cmd": "train", "item": "3tnk"}) is None
    assert check(world, {"cmd": "train", "item": "2tnk"}) is None


def test_an_item_name_is_matched_without_regard_to_case_and_sent_in_lower_case(world):
    assert check(world, {"cmd": "build", "item": "POWR"}) == {"cmd": "build", "item": "powr"}
    assert check(world, {"cmd": "place", "item": "Pbox"}) == {"cmd": "place", "item": "pbox"}


@pytest.mark.parametrize(
    "count, sendable", [(1, True), (10, True), (0, False), (11, False), (True, False), (2.0, False), ("3", False)]
)
def test_a_train_count_is_a_whole_number_from_1_to_the_limit(world, count, sendable):
    assert (check(world, {"cmd": "train", "item": "e1", "count": count}) is not None) is sendable


def test_the_train_count_limit_is_the_one_given(world):
    order = Intent(Priority.PRODUCTION, {"cmd": "train", "item": "e1", "count": 6})

    assert validated(order, world, Limits(max_train_count=5)) is None
    assert validated(order, world, Limits(max_train_count=6)) is not None


def test_place_needs_an_item_that_is_ready_to_place(world):
    assert world.ready_to_place == ("pbox",)

    assert check(world, {"cmd": "place", "item": "pbox"}) == {"cmd": "place", "item": "pbox"}
    assert check(world, {"cmd": "place", "item": "pbox", "at": [26, 50]}) == {
        "cmd": "place",
        "item": "pbox",
        "at": [26, 50],
    }
    assert check(world, {"cmd": "place", "item": "fix"}) is None
    assert check(world, {"cmd": "place", "item": "powr"}) is None


def test_a_place_at_a_cell_outside_the_bounds_is_discarded(world):
    assert check(world, {"cmd": "place", "item": "pbox", "at": [96, 50]}) is None
    assert check(world, {"cmd": "place", "item": "pbox", "at": None}) is None


def test_cancel_needs_the_item_to_be_in_a_queue(world):
    assert check(world, {"cmd": "cancel", "item": "fix"}) == {"cmd": "cancel", "item": "fix"}
    assert check(world, {"cmd": "cancel", "item": "PBOX"}) == {"cmd": "cancel", "item": "pbox"}
    assert check(world, {"cmd": "cancel", "item": "powr"}) is None


# -- orders for a building -------------------------------------------------------------------------


def test_an_order_for_a_building_needs_one_of_the_agent_s_own(world):
    barracks, rifle = ids_of(world, "tent")[0], ids_of(world, "e1")[0]

    for cmd in ("sell", "set_primary"):
        assert check(world, {"cmd": cmd, "building": barracks}) == {"cmd": cmd, "building": barracks}
        assert check(world, {"cmd": cmd, "building": rifle}) is None
        assert check(world, {"cmd": cmd, "building": ENEMY_FLAME_TOWER}) is None


@pytest.mark.parametrize("cmd", ["repair", "power_toggle"])
def test_repair_and_power_toggle_always_carry_an_explicit_on(world, cmd):
    barracks = ids_of(world, "tent")[0]

    assert check(world, {"cmd": cmd, "building": barracks, "on": True}) == {
        "cmd": cmd,
        "building": barracks,
        "on": True,
    }
    assert check(world, {"cmd": cmd, "building": barracks, "on": False}) == {
        "cmd": cmd,
        "building": barracks,
        "on": False,
    }
    assert check(world, {"cmd": cmd, "building": barracks}) is None
    assert check(world, {"cmd": cmd, "building": barracks, "on": 1}) is None
    assert check(world, {"cmd": cmd, "building": UNKNOWN_ID, "on": True}) is None


def test_a_rally_point_is_a_cell_inside_the_bounds_for_an_own_building(world):
    barracks = ids_of(world, "tent")[0]

    assert check(world, {"cmd": "rally", "building": barracks, "to": [32, 48]}) is not None
    assert check(world, {"cmd": "rally", "building": barracks, "to": [32, 96]}) is None
    assert check(world, {"cmd": "rally", "building": UNKNOWN_ID, "to": [32, 48]}) is None


# -- orders the platform would not read ------------------------------------------------------------


@pytest.mark.parametrize(
    "order",
    [
        {},
        {"item": "powr"},
        {"cmd": "construct", "item": "powr"},
        {"cmd": "build"},
        {"cmd": "build", "item": "powr", "count": 2},
        {"cmd": "build", "item": "powr", "at": [26, 50]},
        {"cmd": "move", "units": [1]},
        {"cmd": "move", "to": [40, 48]},
        {"cmd": "stop", "units": [1], "to": [40, 48]},
        {"cmd": "rally", "building": 1},
        {"cmd": "place", "item": "pbox", "to": [26, 50]},
        {"cmd": "train", "item": ["e1"]},
    ],
    ids=[
        "empty",
        "no cmd",
        "unknown cmd",
        "missing item",
        "count on build",
        "at on build",
        "move without a cell",
        "move without units",
        "a cell on stop",
        "rally without a cell",
        "to on place",
        "an item that is not a name",
    ],
)
def test_an_order_without_the_fields_of_its_command_is_discarded(world, order):
    assert check(world, order) is None


def test_every_command_of_the_platform_has_its_fields_and_a_check(world):
    rifle, barracks = ids_of(world, "e1")[0], ids_of(world, "tent")[0]
    values = {
        "units": [rifle],
        "to": [40, 48],
        "target": barracks,
        "stance": "defend",
        "item": "pbox",
        "building": barracks,
        "on": True,
    }

    for cmd, (required, _optional) in ORDER_FIELDS.items():
        order = {"cmd": cmd, **{name: values[name] for name in required}}
        assert isinstance(check(world, order), (dict, type(None))), cmd
    assert len(ORDER_FIELDS) == 19


def test_validation_does_not_change_the_intent_s_order(world):
    rifles = ids_of(world, "e1")
    order = {"cmd": "move", "units": [rifles[0], UNKNOWN_ID], "to": [40, 48]}
    intent = Intent(Priority.MOVE, order)
    before = copy.deepcopy(order)

    sendable = validated(intent, world, LIMITS)

    assert order == before and intent.order is order
    assert sendable["units"] == [rifles[0]] and sendable["units"] is not order["units"]


def test_an_intent_is_rechecked_against_each_new_world(world):
    jeep = ids_of(world, "jeep")
    intent = Intent(Priority.SCOUT, {"cmd": "move", "units": jeep, "to": [70, 48]})
    assert validated(intent, world, LIMITS) is not None
    later = load_state("midgame_allies")
    later["observation"]["tick"] += 5
    later["observation"]["units"] = [unit for unit in later["observation"]["units"] if unit["type"] != "jeep"]

    world.update(later)

    assert validated(intent, world, LIMITS) is None


# -- packing: the limits of a batch ----------------------------------------------------------------

FIRST_UNIT_ID = 1000
FIRST_ENEMY_ID = 5000
MAX_PACK_SECONDS = 0.010
TIMED_PACKS = 20


def army_world(items: ItemTable, game_config: dict, unit_count: int, enemies: Iterable[tuple[str, int]] = ()) -> World:
    """A world with that many rifle infantry, numbered from 1000, and nothing else of its own.

    In sight there is one enemy of each (type, health in percent) given, numbered from 5000.
    """
    state = load_state("opening_mcv")
    state["observation"]["units"] = [
        {
            "id": FIRST_UNIT_ID + number,
            "type": "e1",
            "x": 20 + number % 30,
            "y": 40 + number % 20,
            "hp": 100,
            "idle": True,
        }
        for number in range(unit_count)
    ]
    state["observation"]["visible_enemies"] = [
        {"id": FIRST_ENEMY_ID + number, "type": enemy_type, "x": 60, "y": 30 + number, "hp": hp}
        for number, (enemy_type, hp) in enumerate(enemies)
    ]
    state["observation"]["available_production"] = ["powr", "e1", "e3"]
    army = World(items, game_config)
    army.update(state)
    return army


def unit_ids(count: int, start: int = 0) -> list[int]:
    return [FIRST_UNIT_ID + start + number for number in range(count)]


def move(units: list[int], priority: Priority = Priority.MOVE, to: tuple[int, int] = (40, 48), **fields) -> Intent:
    return Intent(priority, {"cmd": "move", "units": units, "to": list(to), **fields})


def test_30_intents_of_one_unit_each_pack_into_20_orders_and_leave_10_in_priority_order(items, game_config):
    world = army_world(items, game_config, 30)
    classes = [Priority.SCOUT, Priority.MOVE, Priority.EMERGENCY]
    intents = [move([unit_id], classes[number % 3]) for number, unit_id in enumerate(unit_ids(30))]

    packed = pack(intents, world, LIMITS)

    in_priority_order = sorted(intents, key=lambda intent: intent.priority)
    assert len(packed.orders) == 20 and len(packed.deferred) == 10
    assert list(packed.intents) == in_priority_order[:20] and list(packed.deferred) == in_priority_order[20:]
    assert [intent.priority for intent in packed.intents] == [Priority.EMERGENCY] * 10 + [Priority.MOVE] * 10
    assert {intent.priority for intent in packed.deferred} == {Priority.SCOUT}
    assert packed.orders == [intent.order for intent in in_priority_order[:20]] and packed.dropped_invalid == 0


def test_an_attack_move_for_120_units_becomes_three_orders_of_50_50_and_20(items, game_config):
    world = army_world(items, game_config, 120)
    ids = unit_ids(120)

    packed = pack([Intent(Priority.MOVE, {"cmd": "attack_move", "units": ids, "to": [60, 48]})], world, LIMITS)

    assert [len(order["units"]) for order in packed.orders] == [50, 50, 20]
    assert [unit for order in packed.orders for unit in order["units"]] == ids
    assert {(order["cmd"], tuple(order["to"])) for order in packed.orders} == {("attack_move", (60, 48))}
    assert packed.deferred == () and {intent.priority for intent in packed.intents} == {Priority.MOVE}


def test_four_orders_of_50_units_fill_the_200_commands_and_a_train_of_10_is_deferred(items, game_config):
    world = army_world(items, game_config, 200)
    marches = [move(unit_ids(50, start), Priority.EMERGENCY) for start in (0, 50, 100, 150)]
    train = Intent(Priority.PRODUCTION, {"cmd": "train", "item": "e1", "count": 10})

    packed = pack([train, *marches], world, LIMITS)

    assert list(packed.intents) == marches and packed.deferred == (train,)
    assert sum(command_count(order) for order in packed.orders) == 200


def test_an_attack_on_a_target_that_is_not_in_sight_is_discarded_and_counted(world):
    later = load_state("midgame_allies")
    later["observation"]["tick"] += 5
    later["observation"]["visible_enemies"] = []
    world.update(later)
    rifles = ids_of(world, "e1")
    attack = Intent(Priority.CONTACT, {"cmd": "attack", "units": rifles, "target": ENEMY_RIFLEMAN})
    build = Intent(Priority.PRODUCTION, {"cmd": "build", "item": "powr"})

    packed = pack([attack, build], world, LIMITS)

    assert packed.dropped_invalid == 1
    assert packed.orders == [{"cmd": "build", "item": "powr"}] and packed.deferred == ()


def test_of_two_movement_intents_for_the_same_unit_the_higher_priority_one_is_sent(items, game_config):
    world = army_world(items, game_config, 3)
    (unit,) = unit_ids(1)
    advance = move([unit], Priority.MOVE, to=(70, 48))
    retreat = move([unit], Priority.EMERGENCY, to=(22, 48))

    packed = pack([advance, retreat], world, LIMITS)

    assert packed.orders == [{"cmd": "move", "units": [unit], "to": [22, 48]}]
    assert (packed.deferred, packed.dropped_invalid) == ((), 0)


def test_a_less_urgent_order_keeps_the_units_no_more_urgent_order_has_taken(items, game_config):
    world = army_world(items, game_config, 6)
    ids = unit_ids(6)
    flee = Intent(Priority.EMERGENCY, {"cmd": "move", "units": ids[:2], "to": [22, 48]})
    attack = Intent(Priority.MOVE, {"cmd": "attack_move", "units": ids, "to": [70, 48]})
    scout = Intent(Priority.SCOUT, {"cmd": "move", "units": ids[3:4], "to": [80, 80]})

    packed = pack([scout, attack, flee], world, LIMITS)

    assert packed.orders == [
        {"cmd": "move", "units": ids[:2], "to": [22, 48]},
        {"cmd": "attack_move", "units": ids[2:], "to": [70, 48]},
    ]
    assert attack.order["units"] == ids


@pytest.mark.parametrize(
    "cmd", ["move", "attack_move", "attack", "stop", "guard", "harvest", "deploy", "unload", "enter_transport"]
)
def test_every_order_that_gives_a_unit_something_to_do_competes_for_it(items, game_config, cmd):
    world = army_world(items, game_config, 3)
    first, second, third = unit_ids(3)
    extra = {"to": [50, 50]} if cmd in ("move", "attack_move", "attack") else {}
    extra = {"target": third} if cmd in ("guard", "enter_transport") else extra
    urgent = Intent(Priority.EMERGENCY, {"cmd": cmd, "units": [first], **extra})

    packed = pack([move([first, second]), urgent], world, LIMITS)

    assert [(order["cmd"], order["units"]) for order in packed.orders] == [(cmd, [first]), ("move", [second])]


def test_a_queued_order_and_a_change_of_stance_do_not_compete_for_a_unit(items, game_config):
    world = army_world(items, game_config, 2)
    (unit,) = unit_ids(1)
    first = move([unit], Priority.EMERGENCY, to=(30, 48))
    then = move([unit], Priority.MOVE, to=(60, 48), queued=True)
    stance = Intent(Priority.MOVE, {"cmd": "set_stance", "units": [unit], "stance": "hold_fire"})

    packed = pack([first, then, stance], world, LIMITS)

    assert list(packed.intents) == [first, then, stance]


def test_two_orders_of_the_same_priority_for_one_unit_keep_the_one_proposed_first(items, game_config):
    world = army_world(items, game_config, 2)
    (unit,) = unit_ids(1)

    packed = pack([move([unit], to=(30, 48)), move([unit], to=(60, 48))], world, LIMITS)

    assert packed.orders == [{"cmd": "move", "units": [unit], "to": [30, 48]}]


def test_units_of_a_deferred_urgent_order_are_not_sent_elsewhere_by_a_less_urgent_one(items, game_config):
    world = army_world(items, game_config, 60)
    urgent = move(unit_ids(50), Priority.EMERGENCY, to=(22, 48))
    lesser = move(unit_ids(5), Priority.MOVE, to=(70, 48))
    other = move(unit_ids(5, start=50), Priority.MOVE, to=(70, 48))

    packed = pack([urgent, lesser, other], world, Limits(max_engine_commands=20))

    assert list(packed.deferred) == [urgent]
    assert list(packed.intents) == [other] and packed.dropped_invalid == 0


def test_an_order_that_does_not_fit_is_deferred_whole_and_a_smaller_one_after_it_still_goes_in(items, game_config):
    world = army_world(items, game_config, 100)
    large = move(unit_ids(40), Priority.EMERGENCY)
    too_large = move(unit_ids(30, start=40), Priority.MOVE)
    small = move(unit_ids(8, start=70), Priority.SCOUT)

    packed = pack([large, too_large, small], world, Limits(max_engine_commands=50))

    assert list(packed.intents) == [large, small] and list(packed.deferred) == [too_large]
    assert too_large.order["units"] == packed.deferred[0].order["units"] == unit_ids(30, start=40)


def test_the_parts_of_a_split_order_that_do_not_fit_are_deferred_with_their_units(items, game_config):
    world = army_world(items, game_config, 120)
    march = Intent(Priority.MOVE, {"cmd": "attack_move", "units": unit_ids(120), "to": [60, 48]})

    packed = pack([march], world, Limits(max_engine_commands=100))

    assert [order["units"] for order in packed.orders] == [unit_ids(50), unit_ids(50, start=50)]
    assert [intent.order for intent in packed.deferred] == [
        {"cmd": "attack_move", "units": unit_ids(20, start=100), "to": [60, 48]}
    ]
    assert packed.deferred[0].priority == Priority.MOVE


def test_the_limits_given_are_the_ones_kept(items, game_config):
    world = army_world(items, game_config, 30)
    intents = [move(unit_ids(10)), move(unit_ids(10, start=10)), move(unit_ids(10, start=20))]

    two_orders = pack(intents, world, Limits(max_orders_per_batch=2))
    four_units = pack(intents[:1], world, Limits(max_units_per_order=4))

    assert (len(two_orders.orders), len(two_orders.deferred)) == (2, 1)
    assert [len(order["units"]) for order in four_units.orders] == [4, 4, 2]


def test_a_train_counts_its_count_and_every_other_order_one_command(world):
    barracks = ids_of(world, "tent")[0]
    orders = [
        {"cmd": "train", "item": "e1", "count": 7},
        {"cmd": "train", "item": "1tnk"},
        {"cmd": "build", "item": "powr"},
        {"cmd": "place", "item": "pbox"},
        {"cmd": "repair", "building": barracks, "on": True},
        {"cmd": "move", "units": ids_of(world, "e1"), "to": [30, 48]},
    ]

    packed = pack([Intent(Priority.PRODUCTION, order) for order in orders], world, Limits(max_engine_commands=16))

    assert [command_count(order) for order in orders] == [7, 1, 1, 1, 1, 6]
    assert packed.orders == orders[:5] and [intent.order for intent in packed.deferred] == orders[5:]


def test_intents_of_one_priority_keep_the_order_they_were_proposed_in(world):
    orders = [{"cmd": "build", "item": "powr"}, {"cmd": "train", "item": "e1"}, {"cmd": "cancel", "item": "fix"}]

    packed = pack([Intent(Priority.PRODUCTION, order) for order in orders], world, LIMITS)

    assert packed.orders == orders


def test_packing_nothing_gives_an_empty_batch(world):
    assert pack([], world, LIMITS) == Packed(intents=(), deferred=(), dropped_invalid=0)
    assert pack([], world, LIMITS).orders == []


def test_the_orders_of_a_batch_are_new_objects_and_not_the_intents_own(items, game_config):
    world = army_world(items, game_config, 2)
    intent = move(unit_ids(2))

    packed = pack([intent], world, LIMITS)
    packed.orders[0]["units"].clear()

    assert intent.order["units"] == unit_ids(2)
    assert packed.intents[0].order is not intent.order and packed.orders[0] is packed.intents[0].order


def test_no_batch_exceeds_20_orders_50_units_an_order_or_200_commands(items, game_config):
    world = army_world(items, game_config, 400)
    intents = [move(unit_ids(70, start), Priority(start // 70 % 6)) for start in range(0, 350, 70)]
    intents += [Intent(Priority.PRODUCTION, {"cmd": "train", "item": "e1", "count": 10}) for _ in range(30)]
    intents += [move([unit_id], Priority.SCOUT) for unit_id in unit_ids(50, start=350)]

    packed = pack(intents, world, LIMITS)

    assert len(packed.orders) <= 20
    assert max(len(order.get("units", ())) for order in packed.orders) <= 50
    assert sum(command_count(order) for order in packed.orders) <= 200
    sent_or_kept = sum(command_count(intent.order) for intent in (*packed.intents, *packed.deferred))
    assert sent_or_kept == sum(command_count(intent.order) for intent in intents)


def test_batches_packed_under_the_limits_of_a_state_that_overstates_them_stay_within_20_50_and_200(items, game_config):
    overstated = Limits.from_state(
        {"limits": {"max_orders_per_batch": 100, "max_units_per_order": 500, "max_engine_commands": 5000}}
    )
    world = army_world(items, game_config, 400)
    marches = [move(unit_ids(70, start)) for start in range(0, 350, 70)]
    scouts = [move([unit_id], Priority.SCOUT) for unit_id in unit_ids(50, start=350)]

    large = pack([*marches, *scouts], world, overstated)
    many = pack(scouts, world, overstated)

    assert max(len(order["units"]) for order in large.orders) == 50
    assert sum(command_count(order) for order in large.orders) == 200
    assert (len(many.orders), len(many.deferred)) == (20, 30)


def test_packing_300_intents_takes_under_10_ms(items, game_config):
    world = army_world(items, game_config, 300)
    classes = list(Priority)
    intents = [move([unit_id], classes[number % 6]) for number, unit_id in enumerate(unit_ids(300))]
    intents[::10] = [Intent(Priority.PRODUCTION, {"cmd": "train", "item": "e1", "count": 2}) for _ in intents[::10]]
    assert len(intents) == 300

    durations = []
    for _ in range(TIMED_PACKS):
        started = time.perf_counter()
        packed = pack(intents, world, LIMITS)
        durations.append(time.perf_counter() - started)

    assert len(packed.orders) == 20 and len(packed.deferred) == 280
    assert sorted(durations)[len(durations) // 2] < MAX_PACK_SECONDS


# -- packing the combat classes --------------------------------------------------------------------


def attack(units: list[int], target: int, priority: Priority = Priority.CONTACT) -> Intent:
    return Intent(priority, {"cmd": "attack", "units": units, "target": target})


def test_the_priority_classes_fill_20_orders_with_production_contact_screen_and_two_moves_and_no_scout(
    items, game_config
):
    world = army_world(items, game_config, 40, enemies=[("e1", 100)] * 5)
    ids = unit_ids(40)
    contact = [attack([unit], FIRST_ENEMY_ID + number) for number, unit in enumerate(ids[:5])]
    production = [Intent(Priority.PRODUCTION, {"cmd": "train", "item": item}) for item in ("e1", "e3")]
    production.append(Intent(Priority.PRODUCTION, {"cmd": "build", "item": "powr"}))
    screen = [move([unit], Priority.SCREEN) for unit in ids[5:15]]
    moves = [move([unit], Priority.MOVE) for unit in ids[15:35]]
    scouts = [move([unit], Priority.SCOUT) for unit in ids[35:37]]
    intents = [*scouts, *moves, *screen, *contact, *production]

    packed = pack(intents, world, LIMITS)

    assert len(intents) == 40 and len(packed.orders) == 20
    assert Counter(intent.priority for intent in packed.intents) == {
        Priority.PRODUCTION: 3,
        Priority.CONTACT: 5,
        Priority.SCREEN: 10,
        Priority.MOVE: 2,
    }
    assert list(packed.intents) == [*production, *contact, *screen, *moves[:2]]
    assert list(packed.deferred) == [*moves[2:], *scouts]


def test_the_combat_priority_classes_come_after_production_and_before_scouting_and_are_dropped():
    assert Priority.PRODUCTION < Priority.CONTACT < Priority.SCREEN < Priority.MOVE < Priority.SCOUT
    assert [int(priority) for priority in sorted(Priority)] == list(range(8))
    assert DROPPED_CLASSES == {Priority.CONTACT, Priority.SCREEN, Priority.MOVE}


@pytest.mark.parametrize("tank_hp, tank_first", [(100, True), (10, False)], ids=["2tnk at 100%", "2tnk at 10%"])
def test_within_contact_a_band_move_goes_first_then_the_attack_on_the_target_worth_more(
    items, game_config, tank_hp, tank_first
):
    world = army_world(items, game_config, 3, enemies=[("e1", 100), ("2tnk", tank_hp)])
    rifle_attack = attack(unit_ids(1), FIRST_ENEMY_ID)
    tank_attack = attack(unit_ids(1, start=1), FIRST_ENEMY_ID + 1)
    # A long-range unit's move into its firing band: an order in CONTACT that is no attack on a target.
    band_move = move(unit_ids(1, start=2), Priority.CONTACT, to=(30, 48))

    packed = pack([rifle_attack, tank_attack, band_move], world, LIMITS)

    attacks = [tank_attack, rifle_attack] if tank_first else [rifle_attack, tank_attack]
    assert list(packed.intents) == [band_move, *attacks]
    assert packing_rank(tank_attack, world) == (Priority.CONTACT, 1, -850.0 * tank_hp / 100)
    assert packing_rank(rifle_attack, world) == (Priority.CONTACT, 1, -100.0)
    assert packing_rank(band_move, world) == (Priority.CONTACT, 0, 0.0)


def test_a_contact_attack_on_an_enemy_no_longer_known_ranks_last_and_an_attack_of_another_class_by_class():
    world = World(ItemTable([]))

    assert packing_rank(attack(unit_ids(1), UNKNOWN_ID), world) == (Priority.CONTACT, 1, 0.0)
    assert packing_rank(attack(unit_ids(1), FIRST_ENEMY_ID, Priority.MOVE), world) == (Priority.MOVE, 0, 0.0)


def test_25_contact_attacks_and_5_moves_give_the_20_contact_attacks_on_the_targets_worth_most_and_no_move(
    items, game_config
):
    # Enemy rifles at 100%, 97%, 94% ...: each attack's target is worth less than the one before.
    world = army_world(items, game_config, 30, enemies=[("e1", 100 - 3 * number) for number in range(25)])
    contact = [attack([unit], FIRST_ENEMY_ID + number) for number, unit in enumerate(unit_ids(25))]
    moves = [move([unit], Priority.MOVE) for unit in unit_ids(5, start=25)]

    packed = pack([*moves, *reversed(contact)], world, LIMITS)

    assert list(packed.intents) == contact[:20]
    assert list(packed.deferred) == [*contact[20:], *moves]


def test_9_contact_attacks_of_20_units_are_packed_and_a_move_of_30_is_dropped_by_the_200_command_limit(
    items, game_config
):
    world = army_world(items, game_config, 210, enemies=[("e1", 100)] * 9)
    attacks = [attack(unit_ids(20, start=20 * number), FIRST_ENEMY_ID + number) for number in range(9)]
    march = move(unit_ids(30, start=180), Priority.MOVE)

    packed = pack([march, *attacks], world, LIMITS)

    assert list(packed.intents) == attacks and list(packed.deferred) == [march]
    assert sum(command_count(order) for order in packed.orders) == 180


def test_a_dropped_combat_intent_is_not_in_the_next_batch_unless_its_manager_proposes_it_again(items, game_config):
    world = army_world(items, game_config, 22)
    ledger = OrderLedger()
    moves = [move([unit], Priority.MOVE) for unit in unit_ids(21)]
    scout = move(unit_ids(1, start=21), Priority.SCOUT, to=(80, 20))

    def cycle(proposed: list[Intent]) -> Packed:
        packed = pack(ledger.admit(proposed, world), world, LIMITS)
        ledger.record(packed, world, world.tick)
        return packed

    first, second, third = cycle([*moves, scout]), cycle([]), cycle([moves[20]])

    assert list(first.deferred) == [moves[20], scout]
    # The scout's intent is of a class the ledger carries over; the dropped move is not.
    assert (second.orders, third.orders) == ([scout.order], [moves[20].order])


# -- what did not fit, in the timing events --------------------------------------------------------

# The first six own units of the Allied mid-game fixture, and three more.
STOPPED = [105, 106, 107, 108, 109, 110]
STEADIED = [115, 116, 117]


def test_dropped_by_class_in_a_timing_event_counts_the_intents_left_out_of_its_window_s_batches(match_record):
    # Six stops (MOVE) and three changes of stance (SCOUT), at tick 1000 only, into batches of at most four orders.
    stops = [Intent(Priority.MOVE, {"cmd": "stop", "units": [unit_id]}) for unit_id in STOPPED]
    steady = [
        Intent(Priority.SCOUT, {"cmd": "set_stance", "units": [unit_id], "stance": "defend"}) for unit_id in STEADIED
    ]
    agent = new_agent(ScriptedManager("combat", lambda world, ctx: [*stops, *steady] if world.tick == 1000 else []))

    sent = []
    for tick in (1000, 1250, 1500):
        state = fixture_state("midgame_allies", tick)
        state["limits"] = {"max_orders_per_batch": 4}
        sent.append(ask(agent, state))

    first, second = match_record("timing")
    # At 1250 the three changes of stance the ledger carried over go out; the two stops left out are gone.
    assert [len(orders) for orders in sent] == [4, 3, 0]
    assert first["dropped_by_class"] == {**dict.fromkeys(Priority.__members__, 0), "MOVE": 2, "SCOUT": 3}
    assert second["dropped_by_class"] == dict.fromkeys(Priority.__members__, 0)


# -- a large battle --------------------------------------------------------------------------------

TIMED_BATTLES = 50
MAX_MEDIAN_BATTLE_SECONDS = 0.010
MAX_BATTLE_SECONDS = 0.025
OWN_ARMY = ("e1", "e1", "e3", "1tnk", "2tnk", "arty")
ENEMY_ARMY = ("e1", "e3", "3tnk", "1tnk", "v2rl", "harv")


def battle_world(items: ItemTable, game_config: dict) -> World:
    """150 own units of six types in a block 6 cells wide and 60 enemies of six types facing it, all within 12 cells."""
    state = load_state("opening_mcv")
    state["observation"]["units"] = [
        {
            "id": FIRST_UNIT_ID + number,
            "type": OWN_ARMY[number % len(OWN_ARMY)],
            "x": 34 + number % 6,
            "y": 32 + number // 6,
            "hp": 100,
            "idle": True,
        }
        for number in range(150)
    ]
    state["observation"]["visible_enemies"] = [
        {
            "id": FIRST_ENEMY_ID + number,
            "type": ENEMY_ARMY[number % len(ENEMY_ARMY)],
            "x": 41 + number % 6,
            "y": 39 + number // 6,
            "hp": 40 + number,
        }
        for number in range(60)
    ]
    battle = World(items, game_config)
    battle.update(state)
    return battle


def timed_cycles(world: World) -> tuple[list[float], Packed]:
    """How long each of 50 cycles of assignment and packing of all the world's units takes, and the last batch."""
    units = list(world.combat_units.values())
    middle, tuning, focus = centroid(units), Tuning(), FocusFire()
    durations = []
    # What the earlier tests of the session left in memory is frozen out of garbage collection, so that the
    # collector's pauses are the ones the cycle's own objects cause, as in a worker process of its own.
    gc.collect()
    gc.freeze()
    try:
        for _ in range(TIMED_BATTLES):
            started = time.perf_counter()
            packed = pack(focus.orders(units, middle, world, tuning), world, LIMITS)
            durations.append(time.perf_counter() - started)
    finally:
        gc.unfreeze()
    return durations, packed


def test_a_cycle_of_150_units_engaged_with_60_enemies_assigns_and_packs_in_a_median_of_10_ms_and_at_most_25(
    items, game_config
):
    world = battle_world(items, game_config)

    durations, packed = timed_cycles(world)

    assert (len(world.combat_units), len(world.enemy_memory), len(durations)) == (150, 60, 50)
    assert len(packed.orders) == 20 and {intent.priority for intent in packed.intents} == {Priority.CONTACT}
    assert statistics.median(durations) <= MAX_MEDIAN_BATTLE_SECONDS
    assert max(durations) <= MAX_BATTLE_SECONDS


# -- the model commander's slot ----------------------------------------------------------------------


def commanded(units: list[int], to: tuple[int, int] = (60, 48)) -> Intent:
    """The model commander's attack-move for some units."""
    return Intent(Priority.MOVE, {"cmd": "attack_move", "units": units, "to": list(to)}, commander=True)


def test_commander_slot_25_pending_orders_and_the_commander_s_give_19_others_and_it_and_without_it_20_others(
    items, game_config
):
    world = army_world(items, game_config, 30)
    others = [move([unit_id], Priority.EMERGENCY) for unit_id in unit_ids(25)]
    order_for_main = commanded(unit_ids(5, start=25))

    with_commander = pack([*others, order_for_main], world, LIMITS)
    without = pack(others, world, LIMITS)

    assert len(with_commander.intents) == 20 and with_commander.intents[0] == order_for_main
    assert sum(not intent.commander for intent in with_commander.intents) == 19
    assert list(with_commander.intents[1:]) == others[:19] and list(with_commander.deferred) == others[19:]
    assert list(without.intents) == others[:20] and not any(intent.commander for intent in without.intents)


def test_commander_slot_the_commander_s_order_goes_before_an_emergency_order_and_keeps_its_units(items, game_config):
    world = army_world(items, game_config, 4)
    retreat = move(unit_ids(4), Priority.EMERGENCY, to=(20, 48))

    packed = pack([retreat, commanded(unit_ids(2))], world, LIMITS)

    assert [(intent.commander, intent.order["units"]) for intent in packed.intents] == [
        (True, unit_ids(2)),
        (False, unit_ids(2, start=2)),
    ]


def test_commander_slot_a_commander_order_over_the_unit_limit_carries_its_first_50_and_main_s_order_the_rest(
    items, game_config
):
    world = army_world(items, game_config, 60)
    nearest_first = list(reversed(unit_ids(60)))
    same_for_main = Intent(Priority.MOVE, {"cmd": "attack_move", "units": unit_ids(60), "to": [60, 48]})

    packed = pack([commanded(nearest_first), same_for_main], world, LIMITS)

    first, second = packed.intents
    assert first.commander and first.order["units"] == nearest_first[:50]
    assert not second.commander and sorted(second.order["units"]) == sorted(nearest_first[50:])
    assert packed.deferred == ()


def test_commander_slot_a_batch_holds_one_commander_order_at_most(items, game_config):
    world = army_world(items, game_config, 4)

    packed = pack([commanded(unit_ids(2)), commanded(unit_ids(2, start=2), to=(30, 30))], world, LIMITS)

    assert [intent.order["units"] for intent in packed.intents] == [unit_ids(2)]
    assert packed.deferred == () and packed.dropped_invalid == 0


def test_commander_slot_a_commander_order_whose_units_are_gone_leaves_every_slot_to_the_others(items, game_config):
    world = army_world(items, game_config, 20)
    others = [move([unit_id]) for unit_id in unit_ids(20)]

    packed = pack([commanded([UNKNOWN_ID]), *others], world, LIMITS)

    assert list(packed.intents) == others and packed.dropped_invalid == 1
