"""The manager interface: the fixed order of the slots, queue claims, and the context a match's managers share."""

from __future__ import annotations

import pytest

from switches_off import tuning_off
from ucla_agent.games.redalert.batch import Intent, Packed, Priority
from ucla_agent.games.redalert.composition import HOLD, OPENING, ArmyMix, Composition, CompositionTable
from ucla_agent.games.redalert.groups import GUARD, MAIN, RAID
from ucla_agent.games.redalert.items import Item, ItemTable
from ucla_agent.games.redalert.ledger import OrderLedger
from ucla_agent.games.redalert.managers import MANAGER_ORDER, ManagerList, MatchContext, QueueClaims
from ucla_agent.games.redalert.production import BUILDING_QUEUE, INFANTRY_QUEUE, VEHICLE_QUEUE
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import CompositionTuning, OpeningTuning, Tuning
from ucla_agent.games.redalert.world import World

ITEMS = ItemTable(
    [Item("powr", True, cost=300, power=100, queue="Building"), Item("e1", False, cost=100, queue="Infantry")]
)


class NamedManager:
    """A manager that proposes one order naming itself."""

    def __init__(self, name: str) -> None:
        self.name = name

    def intents(self, _world, _ctx) -> list[Intent]:
        return [Intent(Priority.PRODUCTION, {"cmd": "stop", "units": [MANAGER_ORDER.index(self.name)]})]


class SilentChooser:
    """A chooser that is never asked anything and has nothing to hand over."""

    def submit(self, _request) -> bool:
        return True

    def poll(self, _current_state_tag):
        return None


def names(managers: ManagerList) -> list[str]:
    return [manager.name for manager in managers]


def state(
    tick: int, units: tuple[str, ...] = (), production: tuple[dict, ...] = (), offered: tuple[str, ...] = ("powr", "e1")
) -> dict:
    """A playing state with one power plant, own units of the given types, and the items given on offer."""
    observation = {
        "tick": tick,
        "faction": "england",
        "economy": {"cash": 1000, "power_provided": 100, "power_drained": 40, "harvesters": 0},
        "buildings": [{"id": 101, "type": "powr", "x": 20, "y": 48}],
        "units": [{"id": 200 + n, "type": name, "x": 30, "y": 40 + n} for n, name in enumerate(units)],
        "production": list(production),
        "available_production": list(offered),
    }
    return {"phase": "playing", "state_version": tick, "observation": observation}


@pytest.fixture
def ctx() -> MatchContext:
    return MatchContext(Tuning(), OrderLedger(), SilentChooser())


# -- the fixed order -------------------------------------------------------------------------------


def test_the_six_slots_run_in_the_order_the_loop_requires():
    assert MANAGER_ORDER == ("base_defense", "upkeep", "opening", "scouting", "combat", "raids")


def test_managers_come_out_in_the_fixed_order_whatever_order_they_were_registered_in():
    managers = ManagerList(NamedManager(name) for name in ("raids", "opening", "base_defense", "combat"))
    managers.register(NamedManager("upkeep"))
    managers.register(NamedManager("scouting"))

    assert names(managers) == list(MANAGER_ORDER)


def test_a_slot_without_a_manager_is_left_out_and_the_rest_keep_their_order():
    assert names(ManagerList([NamedManager("opening"), NamedManager("upkeep")])) == ["upkeep", "opening"]
    assert names(ManagerList([NamedManager("raids")])) == ["raids"]
    assert names(ManagerList()) == []


def test_a_manager_registered_later_takes_its_slot_among_those_already_there():
    managers = ManagerList([NamedManager("opening"), NamedManager("upkeep")])
    before = names(managers)

    managers.register(NamedManager("base_defense"))
    managers.register(NamedManager("combat"))

    assert before == ["upkeep", "opening"]
    assert names(managers) == ["base_defense", "upkeep", "opening", "combat"]


def test_the_list_can_be_walked_again_and_gives_the_managers_themselves():
    upkeep, opening = NamedManager("upkeep"), NamedManager("opening")
    managers = ManagerList([opening, upkeep])

    assert list(managers) == [upkeep, opening] and list(managers) == [upkeep, opening]


def test_a_manager_that_names_no_slot_is_refused():
    with pytest.raises(ValueError, match="'economy' is not a manager slot; the slots are base_defense, upkeep"):
        ManagerList([NamedManager("economy")])


def test_a_second_manager_for_a_slot_is_refused_and_the_first_stays():
    first = NamedManager("upkeep")
    managers = ManagerList([first])

    with pytest.raises(ValueError, match="the manager slot 'upkeep' is already taken"):
        managers.register(NamedManager("upkeep"))

    assert list(managers) == [first]


# -- claims on production queues -------------------------------------------------------------------


def test_a_queue_nobody_claims_is_held_by_no_other():
    assert not QueueClaims().is_held_by_other(INFANTRY_QUEUE, "upkeep")


def test_a_claim_holds_the_queue_against_every_other_manager_but_not_against_its_holder():
    claims = QueueClaims()

    claims.hold(INFANTRY_QUEUE, "opening")

    assert claims.is_held_by_other(INFANTRY_QUEUE, "upkeep")
    assert claims.is_held_by_other(INFANTRY_QUEUE, "base_defense")
    assert not claims.is_held_by_other(INFANTRY_QUEUE, "opening")
    assert not claims.is_held_by_other(VEHICLE_QUEUE, "upkeep")


def test_a_released_claim_frees_the_queue_and_releasing_what_is_not_held_changes_nothing():
    claims = QueueClaims()
    claims.hold(INFANTRY_QUEUE, "opening")
    claims.hold(INFANTRY_QUEUE, "opening")

    claims.release(INFANTRY_QUEUE, "upkeep")
    claims.release(VEHICLE_QUEUE, "opening")
    still_held = claims.is_held_by_other(INFANTRY_QUEUE, "upkeep")
    claims.release(INFANTRY_QUEUE, "opening")

    assert still_held and not claims.is_held_by_other(INFANTRY_QUEUE, "upkeep")


def test_two_managers_can_claim_one_queue_and_it_stays_held_until_both_let_go():
    claims = QueueClaims()
    claims.hold(INFANTRY_QUEUE, "opening")
    claims.hold(INFANTRY_QUEUE, "base_defense")

    claims.release(INFANTRY_QUEUE, "base_defense")

    assert claims.is_held_by_other(INFANTRY_QUEUE, "upkeep")
    assert not claims.is_held_by_other(INFANTRY_QUEUE, "opening")


# -- the match context -----------------------------------------------------------------------------


def test_a_new_context_starts_in_the_opening_with_no_threat_no_claims_and_an_empty_terrain(ctx):
    assert (ctx.plan, ctx.threat_active) == (OPENING, False)
    assert ctx.terrain.ore_patches == () and ctx.groups.members(GUARD) == []
    assert not ctx.claims.is_held_by_other(INFANTRY_QUEUE, "upkeep")
    assert isinstance(ctx.composition, CompositionTable) and isinstance(ctx.army_mix, ArmyMix)


def test_the_composition_provider_uses_the_tuning_s_raid_quotas_and_army_mix():
    # A jeep stands and is on offer, the light tank is not: the jeep is the raid vehicle, which the mix never makes.
    world = World(ITEMS)
    world.update(state(100, units=("jeep",), offered=("powr", "e1", "jeep")))
    met = MatchContext(tuning_off(composition=CompositionTuning(raid_vehicles=1)), OrderLedger(), SilentChooser())
    short = MatchContext(tuning_off(), OrderLedger(), SilentChooser())
    # The quota counts the units the raid can have: the jeep is in met's raid group.
    met.groups.update(world)
    met.groups.assign(200, RAID)

    # The Allied base row's rockets are not on offer either: the rifles take the infantry's 65 points.
    assert met.composition("RAID", "allies", world) == Composition(infantry={"e1": 0.65})
    assert short.composition("RAID", "allies", world) == Composition(infantry={"e1": 0.65}, vehicle={"jeep": 0.35})
    assert short.composition(HOLD, "allies", world) == Composition(infantry={"e1": 0.65})


def test_the_composition_provider_can_be_replaced(ctx):
    ctx.composition = lambda plan, family, world: Composition(infantry={"e3": 1.0})

    assert ctx.composition(HOLD, "allies", World(ITEMS)) == Composition(infantry={"e3": 1.0})


def test_the_home_guard_wants_its_five_rifles_from_the_first_state_on(ctx):
    world = World(ITEMS)
    world.update(state(100, units=("e1",) * 7 + ("e3",)))

    ctx.begin_cycle(world)

    assert ctx.groups.members(GUARD) == [200, 201, 202, 203, 204]
    assert ctx.groups.members(MAIN) == [205, 206, 207]


def test_the_guard_s_rifle_quota_is_the_tuning_s():
    tuning = Tuning(opening=OpeningTuning(guard_rifles=2))
    ctx = MatchContext(tuning, OrderLedger(), SilentChooser())
    world = World(ITEMS)
    world.update(state(100, units=("e1",) * 3))

    ctx.begin_cycle(world)

    assert (ctx.groups.count(GUARD, "e1"), ctx.groups.count(MAIN, "e1")) == (2, 1)


def test_beginning_a_cycle_gives_new_units_their_group_and_makes_the_cycle_s_production_view(ctx):
    world = World(ITEMS)
    world.update(state(100, units=("e1", "e1"), production=({"queue": "Infantry", "item": "e1"},)))

    ctx.begin_cycle(world)

    assert ctx.groups.members(GUARD) == [200, 201] and ctx.groups.members(MAIN) == []
    assert (ctx.production.standing("powr"), ctx.production.pending("e1"), ctx.production.headroom()) == (1, 1, 60)
    assert not ctx.production.is_queue_free(INFANTRY_QUEUE) and ctx.production.is_queue_free(BUILDING_QUEUE)


def test_each_cycle_gets_a_fresh_production_view_that_knows_the_ledger_s_reservations(ctx):
    world = World(ITEMS)
    world.update(state(100))
    ctx.begin_cycle(world)
    first_view = ctx.production
    ctx.ledger.record(Packed((first_view.build("powr"),), (), 0), world, 100)

    ctx.begin_cycle(world)

    assert ctx.production is not first_view
    assert ctx.production.pending("powr") == 1 and not ctx.production.is_queue_free(BUILDING_QUEUE)


def test_two_contexts_share_no_groups_claims_or_terrain():
    first = MatchContext(Tuning(), OrderLedger(), SilentChooser())
    second = MatchContext(Tuning(), OrderLedger(), SilentChooser(), terrain=Terrain(blocked=[(1, 1)]))

    first.claims.hold(INFANTRY_QUEUE, "opening")
    first.groups.set_guard_quota("e1", 5)

    assert first.groups is not second.groups and first.claims is not second.claims
    assert not second.claims.is_held_by_other(INFANTRY_QUEUE, "upkeep")
    assert first.terrain.blocked == frozenset() and second.terrain.blocked == {(1, 1)}
