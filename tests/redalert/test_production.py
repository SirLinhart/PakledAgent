"""The production view of one cycle: what stands, what is on its way, which queue is free, how much power is left."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ucla_agent.games.redalert.batch import Intent, Packed, Priority
from ucla_agent.games.redalert.items import ItemTable
from ucla_agent.games.redalert.ledger import OrderLedger
from ucla_agent.games.redalert.production import (
    BUILDING_QUEUE,
    DEFENSE_QUEUE,
    INFANTRY_QUEUE,
    VEHICLE_QUEUE,
    Production,
    Proposal,
)
from ucla_agent.games.redalert.world import World

REPO_ROOT = Path(__file__).resolve().parents[2]
STATES_FOLDER = REPO_ROOT / "tests" / "fixtures" / "redalert" / "states"
TICK = 6000


@pytest.fixture(scope="module")
def items() -> ItemTable:
    return ItemTable.from_file(REPO_ROOT)


def allied_state(**observation) -> dict:
    """The Allied mid-game fixture state with these observation fields in place of its own.

    As it is, two power plants give 200 and 100 are drained; a `fix` is being
    built, a `pbox` waits to be placed, two `e1` and a `1tnk` are in training.
    """
    state = json.loads((STATES_FOLDER / "midgame_allies.json").read_text(encoding="utf-8"))
    state["state_version"] = state["observation"]["tick"] = TICK
    state["observation"]["events"] = []
    state["observation"].update(observation)
    return state


def seen(items: ItemTable, ledger: OrderLedger | None = None, **observation) -> tuple[World, OrderLedger]:
    """A world updated with the fixture state, and a ledger reconciled with it."""
    world, ledger = World(items), ledger or OrderLedger()
    state = allied_state(**observation)
    world.update(state)
    ledger.reconcile(world, state)
    return world, ledger


def send(ledger: OrderLedger, world: World, *orders: dict) -> None:
    """Record these orders as sent in this cycle, which opens the reservation of each production order."""
    ledger.record(Packed(tuple(Intent(Priority.PRODUCTION, order) for order in orders), (), 0), world, world.tick)


def test_the_queue_names_are_the_platform_s():
    assert (BUILDING_QUEUE, DEFENSE_QUEUE, INFANTRY_QUEUE, VEHICLE_QUEUE) == (
        "Building",
        "Defense",
        "Infantry",
        "Vehicle",
    )


def test_standing_counts_own_buildings_and_units_counts_own_units_by_type(items):
    production = Production(*seen(items))

    assert [production.standing(item) for item in ("powr", "tent", "fact", "fix", "barr")] == [2, 1, 1, 0, 0]
    assert [production.units(item) for item in ("e1", "e3", "harv", "1tnk", "2tnk")] == [6, 2, 1, 2, 0]


def test_pending_counts_what_is_in_a_queue_a_finished_item_included(items):
    production = Production(*seen(items))

    assert [production.pending(item) for item in ("fix", "pbox", "e1", "1tnk", "powr")] == [1, 1, 2, 1, 0]
    assert [production.planned(item) for item in ("pbox", "fix", "powr", "dome")] == [2, 1, 2, 0]


def test_an_order_that_does_not_show_in_a_queue_yet_is_pending_too(items):
    world, ledger = seen(items, production=[])
    send(ledger, world, {"cmd": "build", "item": "dome"}, {"cmd": "train", "item": "e3", "count": 3})

    production = Production(world, ledger)

    assert (production.pending("dome"), production.pending("e3"), production.pending("e1")) == (1, 3, 0)
    assert production.planned("dome") == 1


def test_a_queue_is_free_when_it_holds_nothing_and_no_order_for_it_is_on_its_way(items):
    world, ledger = seen(items, production=[{"queue": "Vehicle", "item": "1tnk", "progress": 0.5}])
    send(ledger, world, {"cmd": "train", "item": "e1"})

    production = Production(world, ledger)

    assert production.is_queue_free(BUILDING_QUEUE) and production.is_queue_free(DEFENSE_QUEUE)
    assert not production.is_queue_free(VEHICLE_QUEUE)
    assert not production.is_queue_free(INFANTRY_QUEUE)


def test_queued_counts_what_a_queue_holds_what_was_ordered_for_it_and_what_the_cycle_gave_it(items):
    # The fixture's queues hold a fix, a pbox waiting to be placed, two e1 and a 1tnk.
    world, ledger = seen(items)
    send(ledger, world, {"cmd": "train", "item": "2tnk", "count": 2})
    production = Production(world, ledger)
    before = [production.queued(queue) for queue in (BUILDING_QUEUE, DEFENSE_QUEUE, INFANTRY_QUEUE, VEHICLE_QUEUE)]

    production.train("harv")
    production.train("e3", 3)

    assert before == [1, 1, 2, 3]
    assert (production.queued(VEHICLE_QUEUE), production.queued(INFANTRY_QUEUE)) == (4, 5)
    assert Production(*seen(items, production=[])).queued(VEHICLE_QUEUE) == 0


def test_a_finished_building_that_waits_to_be_placed_keeps_its_queue_taken(items):
    production = Production(*seen(items))

    assert not production.is_queue_free(DEFENSE_QUEUE)


def test_the_headroom_is_power_provided_less_drained_less_what_the_queues_will_drain(items):
    with_queues = Production(*seen(items))
    without = Production(*seen(items, production=[]))

    assert without.headroom() == 200 - 100
    assert with_queues.headroom() == 200 - 100 - 30 - 20


def test_a_reserved_building_counts_toward_the_reserved_drain_and_a_unit_or_a_power_plant_does_not(items):
    world, ledger = seen(items, production=[])
    send(ledger, world, {"cmd": "build", "item": "dome"}, {"cmd": "train", "item": "e1", "count": 5})

    assert Production(world, ledger).headroom() == 100 - 40
    assert Production(*seen(items, production=[{"queue": "Building", "item": "powr"}])).headroom() == 100


@pytest.mark.parametrize(
    "drained, item, powered",
    [(160, "gun", True), (161, "gun", False), (200, "powr", True), (200, "e1", True), (170, "proc", True)],
    ids=["headroom exactly used", "one short", "a power plant", "a unit", "a refinery"],
)
def test_an_item_can_be_powered_while_the_headroom_after_it_is_at_least_zero(items, drained, item, powered):
    economy = {"cash": 1000, "power_provided": 200, "power_drained": drained, "harvesters": 1}

    production = Production(*seen(items, production=[], economy=economy))

    assert production.can_power(item) is powered


def test_the_drain_of_an_item_is_the_power_it_takes_and_zero_for_what_provides_power_or_is_not_known(items):
    production = Production(*seen(items))

    assert [production.drain(item) for item in ("proc", "gun", "fix", "powr", "e1", "mystery")] == [30, 40, 30, 0, 0, 0]


def test_the_cost_of_an_item_comes_from_the_item_table(items):
    production = Production(*seen(items))

    assert [production.cost(item) for item in ("powr", "weap", "e1", "1tnk", "harv")] == [300, 2000, 100, 700, 1100]


def test_build_and_train_give_the_intents_with_their_priority(items):
    production = Production(*seen(items, production=[]))

    assert production.build("powr", Priority.POWER) == Intent(Priority.POWER, {"cmd": "build", "item": "powr"})
    assert production.build("pbox") == Intent(Priority.PRODUCTION, {"cmd": "build", "item": "pbox"})
    assert production.train("e1", 5) == Intent(Priority.PRODUCTION, {"cmd": "train", "item": "e1", "count": 5})
    assert production.train("harv").order == {"cmd": "train", "item": "harv", "count": 1}


def test_what_a_cycle_has_proposed_takes_its_queue_for_the_rest_of_the_cycle(items):
    production = Production(*seen(items, production=[]))

    production.build("fix")
    production.train("e1", 2)
    production.train("e3")

    assert not production.is_queue_free(BUILDING_QUEUE) and not production.is_queue_free(INFANTRY_QUEUE)
    assert production.is_queue_free(DEFENSE_QUEUE) and production.is_queue_free(VEHICLE_QUEUE)
    assert (production.pending("fix"), production.pending("e1"), production.pending("e3")) == (1, 2, 1)
    assert production.planned("fix") == 1


def test_what_a_cycle_has_proposed_takes_its_power_for_the_rest_of_the_cycle(items):
    economy = {"cash": 1000, "power_provided": 200, "power_drained": 150, "harvesters": 1}
    production = Production(*seen(items, production=[], economy=economy))
    powered_before = production.can_power("pbox")

    production.build("proc")

    assert powered_before and production.headroom() == 20
    assert production.can_power("pbox") and not production.can_power("fix")


def test_a_view_of_the_next_cycle_starts_from_the_state_and_the_ledger_alone(items):
    world, ledger = seen(items, production=[])
    Production(world, ledger).build("fix")

    assert Production(world, ledger).is_queue_free(BUILDING_QUEUE)
    assert Production(world, ledger).pending("fix") == 0
    assert Production(world, ledger).proposed() == ()


def test_the_proposals_of_a_cycle_are_given_in_order_with_their_count_and_priority(items):
    production = Production(*seen(items, production=[]))

    production.build("powr", Priority.POWER)
    production.train("e1", 3)
    production.build("pbox", Priority.EMERGENCY)

    assert production.proposed() == (
        Proposal("powr", 1, Priority.POWER),
        Proposal("e1", 3, Priority.PRODUCTION),
        Proposal("pbox", 1, Priority.EMERGENCY),
    )


def test_an_item_the_table_does_not_know_costs_nothing_drains_nothing_and_has_a_queue_of_its_own(items):
    production = Production(*seen(items, production=[]))

    intent = production.build("mystery")

    assert intent.order == {"cmd": "build", "item": "mystery"}
    assert (production.cost("mystery"), production.headroom(), production.pending("mystery")) == (0, 100, 1)
    assert production.can_power("mystery") and production.is_queue_free(BUILDING_QUEUE)


def test_an_empty_world_has_nothing_standing_nothing_pending_and_every_queue_free(items):
    production = Production(World(items), OrderLedger())

    assert (production.standing("powr"), production.pending("powr"), production.units("e1")) == (0, 0, 0)
    assert production.headroom() == 0 and production.is_queue_free(BUILDING_QUEUE)
