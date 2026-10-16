"""Base upkeep: placement, power, harvesters, production, tech, repair, rally and the low-power emergency."""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

import pytest

import fake_game
import situations
from fake_game import FakeRedAlert
from situations import OpeningThen, ScriptedManager, ask, base_state, with_command
from switches_off import (
    combat_off,
    expansion_off,
    mix_off,
    new_agent,
    own_agent,
    opening_off,
    placement_off,
    producers_off,
    scouting_off,
    switched_off,
    tuning_off,
    upkeep_off,
)
from ucla_agent.core import metrics, metrics_redalert, records
from ucla_agent.core.config import ConfigError
from ucla_agent.core.metrics import Attempt, Match
from ucla_agent.games.redalert import (
    batch,
    cells,
    clock,
    counter_units,
    expansion,
    harvester_room,
    parking,
    placement,
    truck_shares,
)
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.batch import Priority
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.composition import DEPOT_STAGE, HOLD, OPENING, PRESERVE, RAID, Composition, mix_stage
from ucla_agent.games.redalert.economy_hold import EconomyHold, EconomyItem, FullHold
from ucla_agent.games.redalert.expansion import ForwardMcv
from ucla_agent.games.redalert.fresh_ore import FreshOre
from ucla_agent.games.redalert.groups import SCOUT
from ucla_agent.games.redalert.harvester_room import harvester_target
from ucla_agent.games.redalert.income import IncomeEstimate, can_invest
from ucla_agent.games.redalert.managers import MAJOR_THREAT, MINOR_THREAT, NO_THREAT
from ucla_agent.games.redalert.opening import OpeningManager
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.placement import box_at
from ucla_agent.games.redalert.refinery_fields import footprint_cell
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import (
    BARRACKS_FIRST,
    FACTORY_FIRST,
    TANKS,
    TUNING_FILE,
    AntiAirTuning,
    Tuning,
)
from ucla_agent.games.redalert.upkeep import UpkeepManager, anti_air_defenses_wanted, army_vehicle_share, power_target
from ucla_agent.games.redalert.upkeep import _economy_items_due
from ucla_agent.games.redalert.vehicle_turn import main_combat_value
from ucla_agent.games.redalert.world import Economy, ProductionItem
from whole_match import COMPUTE_P95_MS, WHOLE_MATCH_STEPS, WholeMatch, play_whole_match, with_ore

YARD = ("fact", 22, 48)
POWER = ("powr", 19, 48)
BARRACKS = ("tent", 22, 44)
REFINERY = ("proc", 24, 39)
WAR_FACTORY = ("weap", 22, 53)
BOUNDS = (0, 0, 96, 96)


@pytest.fixture(scope="module")
def reference() -> dict:
    """The fake game's reference object, whose map has an ore patch at (22, 40)."""
    return FakeRedAlert().game_config


def upkeep_agent(**more):
    """An agent that plays with the upkeep manager alone, under OPENING until a war factory has stood and HOLD after.

    It keeps to the opening's two refineries, so that no money is saved for a third, unless a test gives it the
    switches-off thresholds.
    """
    return new_agent(UpkeepManager(), **{"plan_selector": OpeningThen(HOLD), "tuning": TWO_REFINERIES_ONLY, **more})


def ready(queue: str, item: str) -> dict:
    """A finished building that waits in its queue to be placed."""
    return situations.job(queue, item, ready=True)


def places(orders: list[dict]) -> list[dict]:
    return with_command(orders, "place")


def kept_free(agent) -> set:
    """The cells no building may be asked onto, as the agent's world and terrain give them."""
    excluded = placement.excluded_cells(agent.world, agent.ctx.terrain, agent.ctx.tuning.placement)
    return excluded | agent.ctx.terrain.blocked


# -- placement -------------------------------------------------------------------------------------


def test_a_building_reported_ready_gets_a_place_order_in_the_same_cycle_inside_the_bounds_and_off_the_kept_cells(
    reference,
):
    agent = upkeep_agent()
    state = base_state("england", YARD, POWER, BARRACKS, REFINERY, WAR_FACTORY, production=[ready("Building", "dome")])

    (order,) = places(ask(agent, state, situations.context(reference)))

    box = box_at("dome", tuple(order["at"]))
    assert order == {"cmd": "place", "item": "dome", "at": order["at"]}
    assert box.is_inside(BOUNDS) and not box.holds_any(kept_free(agent))
    assert len(kept_free(agent)) > 20


def test_the_cell_asked_for_is_the_one_placement_prefers(reference):
    agent = upkeep_agent()
    state = base_state("england", YARD, POWER, BARRACKS, production=[ready("Building", "proc")])

    (order,) = places(ask(agent, state, situations.context(reference)))

    preferred = placement.preferred_cell("proc", agent.world, agent.ctx.terrain, agent.ctx.tuning.placement)
    assert order["at"] == list(preferred) == [24, 39]


def test_a_cell_the_nearest_would_be_but_for_a_war_factory_s_exit_is_not_asked_for():
    with_war_factory = base_state("england", YARD, ("weap", 26, 46), production=[ready("Building", "powr")])
    without = base_state("england", YARD, production=[ready("Building", "powr")])

    (kept,) = places(ask(upkeep_agent(), with_war_factory))
    (free,) = places(ask(upkeep_agent(), without))

    assert free["at"] == [25, 48] and kept["at"] != [25, 48]
    # The war factory covers (25, 45) to (27, 47); the two rows below it are its exit.
    assert box_at("powr", (25, 48)).holds_any({(x, y) for x in (25, 26, 27) for y in (48, 49)})


def test_a_building_that_still_waits_is_asked_onto_the_map_again_only_after_50_ticks():
    agent = upkeep_agent()
    waiting = {"production": [ready("Building", "powr")]}
    first = ask(agent, base_state("england", YARD, tick=1000, **waiting))

    later = [places(ask(agent, base_state("england", YARD, tick=tick, **waiting))) for tick in (1005, 1045, 1050)]

    assert len(places(first)) == 1 and [len(orders) for orders in later] == [0, 0, 1]


def enemy_to_the_south_east(state: dict) -> dict:
    """The state with the enemy starting south-east, where a lone yard's defense goes to the cell (26, 50), next to
    the first power plant's place (25, 48)."""
    state["legal_actions"]["enemy_base"] = [74, 74]
    return state


def test_two_buildings_ready_at_once_are_both_placed_on_cells_that_keep_a_gap_between_them():
    both = base_state("england", YARD, production=[ready("Building", "powr"), ready("Defense", "pbox")])
    alone = base_state("england", YARD, production=[ready("Defense", "pbox")])

    building, defense = places(ask(upkeep_agent(), enemy_to_the_south_east(both)))
    (defense_alone,) = places(ask(upkeep_agent(), enemy_to_the_south_east(alone)))

    assert building == {"cmd": "place", "item": "powr", "at": [25, 48]}
    assert defense_alone == {"cmd": "place", "item": "pbox", "at": [26, 50]}
    assert defense["item"] == "pbox" and defense["at"] != [26, 50]
    assert not box_at("powr", (25, 48)).grown(1).overlaps(box_at("pbox", tuple(defense["at"])))


def test_a_building_placed_a_moment_after_another_keeps_off_the_cell_the_first_was_asked_onto():
    agent = upkeep_agent()
    first = places(ask(agent, base_state("england", YARD, tick=1000, production=[ready("Building", "powr")])))
    next_state = enemy_to_the_south_east(base_state("england", YARD, tick=1005, production=[ready("Defense", "pbox")]))

    (second,) = places(ask(agent, next_state))

    assert first[0]["at"] == [25, 48] and second["at"] != [26, 50]
    assert not box_at("powr", (25, 48)).grown(1).overlaps(box_at("pbox", tuple(second["at"])))


def test_the_next_building_is_placed_by_where_the_last_one_really_stands():
    agent = upkeep_agent()
    ask(agent, base_state("england", YARD, tick=1000, production=[ready("Building", "powr")]))
    elsewhere = ("powr", 19, 48)

    (order,) = places(
        ask(agent, base_state("england", YARD, elsewhere, tick=1100, production=[ready("Building", "tent")]))
    )

    assert order["at"] == [25, 48]


def test_a_building_of_the_same_type_that_becomes_ready_later_is_placed_at_once():
    agent = upkeep_agent()
    ask(agent, base_state("england", YARD, tick=1000, production=[ready("Building", "powr")]))
    ask(agent, base_state("england", YARD, POWER, tick=1005))

    (order,) = places(ask(agent, base_state("england", YARD, POWER, tick=1010, production=[ready("Building", "powr")])))

    assert order["item"] == "powr" and order["at"] != [19, 48]


def test_without_a_candidate_cell_the_building_is_placed_without_naming_one():
    state = base_state("england", YARD, production=[ready("Building", "powr")])
    state["map"]["bounds"] = [20, 46, 6, 6]

    assert places(ask(upkeep_agent(), state)) == [{"cmd": "place", "item": "powr"}]


def test_nothing_is_placed_while_nothing_waits():
    assert ask(upkeep_agent(), base_state("england", YARD, production=[situations.job("Building", "powr")])) == []


def last_asked(batches: list[dict]) -> dict[str, tuple]:
    """The cell each type of building was last asked onto in the batches of a run."""
    return {order["item"]: tuple(order["at"]) for batch in batches for order in places(batch["orders"])}


def last_standing(state: dict) -> dict[str, tuple]:
    """The cell of the last building of each type a state lists."""
    return {building["type"]: (building["x"], building["y"]) for building in state["observation"]["buildings"]}


def test_in_a_fake_match_every_building_appears_on_the_cell_it_was_asked_onto():
    game = FakeRedAlert("russia", "england")
    agent = own_agent()

    played = game.run(agent, 5 * 100, keep_states=True)

    asked, stands = last_asked(played.batches), last_standing(played.states[-1])
    assert set(asked) == {"powr", "barr", "ftur", "proc"}
    assert asked == {item: stands[item] for item in asked}
    assert played.invalid_actions == 0 and agent.ledger.orders_rejected_by_reason == {}


# -- power plants ----------------------------------------------------------------------------------

BASE = (YARD, POWER, BARRACKS, REFINERY)


def built(orders: list[dict], queue: str) -> list[str]:
    """The items of the build orders of a batch that go into a queue."""
    items = situations.tracked_items()
    return [order["item"] for order in with_command(orders, "build") if items[order["item"]].queue == queue]


def builds(orders: list[dict]) -> list[str]:
    """The items a batch orders for the Building queue."""
    return built(orders, "Building")


def powered(provided: int, drained: int, cash: int = 2000, harvesters: int = 1) -> dict:
    """The economy of a base that provides and drains so much power."""
    return situations.economy(cash, provided=provided, drained=drained, harvesters=harvesters)


def test_with_headroom_10_and_nothing_queued_the_next_building_queue_order_is_a_power_plant():
    agent = upkeep_agent()

    orders = ask(agent, base_state("england", *BASE, economy=powered(100, 90)))

    assert orders[0] == {"cmd": "build", "item": "powr"} and builds(orders) == ["powr"]
    assert agent.ledger.entries[0].priority.name == "POWER"


def test_with_headroom_10_and_a_power_plant_already_reserved_no_second_one_is_ordered():
    agent = upkeep_agent()
    ask(agent, base_state("england", *BASE, economy=powered(100, 90), tick=1000))
    reserved = [reservation.item for reservation in agent.ledger.reservations]

    second = ask(agent, base_state("england", *BASE, economy=powered(100, 90), tick=1005))

    assert (reserved, second) == (["powr"], [])
    assert agent.ledger.duplicate_production_orders == 0


@pytest.mark.parametrize("drained, ordered", [(80, []), (81, ["powr"]), (100, ["powr"]), (130, ["powr"])])
def test_a_power_plant_is_ordered_when_the_headroom_is_below_20(drained, ordered):
    assert builds(ask(upkeep_agent(), base_state("england", *BASE, economy=powered(100, drained)))) == ordered


@pytest.mark.parametrize(
    "queued",
    [
        situations.job("Building", "powr"),
        situations.job("Building", "powr", ready=True),
        situations.job("Building", "apwr"),
    ],
    ids=["being built", "waiting to be placed", "an advanced one being built"],
)
def test_no_power_plant_is_ordered_while_one_is_in_the_queue(queued):
    state = base_state("england", *BASE, economy=powered(100, 90), production=[queued])

    assert builds(ask(upkeep_agent(), state)) == []


def test_what_the_buildings_on_their_way_will_drain_counts_against_the_headroom():
    gun_on_its_way = [situations.job("Defense", "gun")]

    with_gun = ask(upkeep_agent(), base_state("england", *BASE, economy=powered(200, 150), production=gun_on_its_way))
    without = ask(upkeep_agent(), base_state("england", *BASE, economy=powered(200, 150)))

    assert (builds(with_gun), builds(without)) == (["powr"], [])


def test_the_power_plant_waits_for_the_building_queue_and_is_ordered_as_soon_as_it_is_free():
    agent = upkeep_agent()
    busy = base_state(
        "england", *BASE, economy=powered(100, 70), tick=1000, production=[situations.job("Building", "proc")]
    )

    while_busy = ask(agent, busy)
    when_free = ask(agent, base_state("england", *BASE, ("proc", 18, 39), economy=powered(100, 100), tick=1005))

    assert (builds(while_busy), builds(when_free)) == ([], ["powr"])


@pytest.mark.parametrize("cash, plant", [(499, "powr"), (500, "apwr")])
def test_the_advanced_power_plant_is_ordered_when_it_is_on_offer_and_cash_is_at_least_500(cash, plant):
    with_radar = (*BASE, ("dome", 26, 48))

    assert builds(ask(upkeep_agent(), base_state("england", *with_radar, economy=powered(100, 100, cash=cash)))) == [
        plant
    ]


def test_without_a_radar_the_plain_power_plant_is_ordered_whatever_the_cash():
    assert builds(ask(upkeep_agent(), base_state("england", *BASE, economy=powered(100, 100, cash=9000)))) == ["powr"]


def test_no_power_plant_is_ordered_when_none_is_on_offer():
    state = base_state("england", *BASE, economy=powered(100, 100), withheld=["powr"])

    assert builds(ask(upkeep_agent(), state)) == []


def test_the_power_plant_goes_ahead_of_the_step_the_opening_would_build():
    agent = new_agent(UpkeepManager(), OpeningManager())

    orders = ask(agent, base_state("england", *BASE, economy=powered(100, 90)))

    assert builds(orders)[0] == "powr" and "proc" not in builds(orders)
    assert orders[0] == {"cmd": "build", "item": "powr"}


# -- the power target ------------------------------------------------------------------------------


# A tick at 380 s, past the sixth minute, from which the power target is 60.
MID_TARGET_TICK = 380 * 25


def with_headroom(seconds: float, *buildings: tuple, headroom: int, faction: str = "england", **more) -> dict:
    """A state at a game time of a base with the buildings given and so much power left over."""
    economy = powered(400, 400 - headroom, cash=5000)
    return base_state(faction, *buildings, tick=round(seconds * 25), economy=economy, **more)


@pytest.mark.parametrize(
    "seconds, headroom, ordered",
    [(120, 19, ["powr"]), (120, 20, []), (300, 59, []), (380, 59, ["powr"]), (380, 60, [])],
    ids=["early, 19", "early, 20", "still early at 300 s, 59", "mid, 59", "mid, 60"],
)
def test_power_the_target_is_20_before_the_sixth_minute_and_60_from_it(seconds, headroom, ordered):
    assert builds(ask(upkeep_agent(), with_headroom(seconds, *BASE, headroom=headroom))) == ordered


# Three barracks and two war factories, five producers, past the opening with its defense: nothing else is wanted in
# the Building queue, and no defense waits for power.
FOUR_PRODUCERS = (
    *BASE,
    ("proc", 18, 39),
    ("powr", 26, 50),
    WAR_FACTORY,
    ("pbox", 27, 46),
    ("tent", 26, 44),
    ("tent", 30, 44),
)
FIVE_PRODUCERS = (*FOUR_PRODUCERS, ("weap", 28, 53))


@pytest.mark.parametrize(
    "buildings, headroom, ordered",
    [(FIVE_PRODUCERS, 99, ["powr"]), (FIVE_PRODUCERS, 100, []), (FOUR_PRODUCERS, 99, [])],
    ids=["five producers, 99", "five producers, 100", "four producers, 99"],
)
def test_power_with_three_barracks_and_two_war_factories_the_target_is_100(buildings, headroom, ordered):
    assert builds(ask(upkeep_agent(), with_headroom(300, *buildings, headroom=headroom))) == ordered


def test_power_a_producer_on_its_way_counts_toward_the_large_target():
    agent = upkeep_agent()
    on_its_way = [situations.job("Building", "weap")]

    ask(agent, with_headroom(300, *FOUR_PRODUCERS, headroom=99, production=on_its_way))

    assert power_target(agent.world, agent.ctx) == 100


SOVIET_BASE = (
    YARD,
    POWER,
    ("barr", 22, 44),
    REFINERY,
    ("proc", 18, 39),
    ("powr", 26, 50),
    WAR_FACTORY,
    ("ftur", 24, 52),
)
TESLA_COILS = (("tsla", 27, 46), ("tsla", 30, 46))


@pytest.mark.parametrize(
    "coils, headroom, ordered",
    [(TESLA_COILS, 150, ["powr"]), (TESLA_COILS, 160, []), (TESLA_COILS[:1], 150, [])],
    ids=["two coils, 150", "two coils, 160", "one coil, 150"],
)
def test_power_with_two_tesla_coils_the_target_is_160(coils, headroom, ordered):
    state = with_headroom(300, *SOVIET_BASE, *coils, headroom=headroom, faction="russia")

    assert builds(ask(upkeep_agent(), state)) == ordered


# Each building of the opening, and the tick of the first state that shows it, as the opening built them before the
# power target grew past 20; for the Soviets also the power plant upkeep orders, once the war factory stands, ahead
# of the first Tesla coil, which would leave less than the target over.
OPENING_BUILT_AT = {
    "england": [
        ("fact", 5),
        ("powr", 190),
        ("tent", 495),
        ("pbox", 860),
        ("proc", 1340),
        ("proc", 2185),
        ("powr", 2370),
        ("weap", 3575),
        ("gun", 4235),
    ],
    "russia": [
        ("fact", 5),
        ("powr", 190),
        ("barr", 495),
        ("ftur", 860),
        ("proc", 1340),
        ("proc", 2185),
        ("powr", 2370),
        ("weap", 3575),
        ("powr", 3760),
    ],
}


def first_appearances(states: list[dict]) -> list[tuple[str, int]]:
    """Every own building of a run, and the tick of the first state that shows it, in the order of appearance."""
    first: dict[int, tuple[str, int]] = {}
    for raw in states:
        for building in raw["observation"]["buildings"]:
            first.setdefault(building["id"], (building["type"], raw["state_version"]))
    return list(first.values())


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_power_the_opening_builds_the_same_buildings_at_the_same_ticks(faction):
    game = FakeRedAlert(faction, ENEMY_OF[faction])

    played = game.run(own_agent(), STATES_PER_SECOND * 179, keep_states=True)

    assert first_appearances(played.states) == OPENING_BUILT_AT[faction]


def without_power_or_money(game: FakeRedAlert) -> None:
    """Take the power plants, the refineries and the money away: nothing can be built again, and the base drains what
    nothing provides."""
    for building in game.own_buildings():
        if building["type"] in ("powr", "proc"):
            game.remove_own(building["id"])
    game.cash = 0


def test_power_the_report_gives_30_seconds_of_low_power_for_a_fake_match_short_of_power_for_30_seconds(
    match_record, run_folder, load_script
):
    game = FakeRedAlert("england", "russia", scenario=(fake_game.at_seconds(150, without_power_or_money),))
    game.run(own_agent(), STATES_PER_SECOND * (150 + 30))
    match_record()
    (folder,) = run_folder.glob(f"runs/*/{situations.SESSION_ID}__seat")

    assert load_script("report.py").main([str(folder)]) == 0

    (report_path,) = run_folder.glob("runs/reports/*/report.md")
    low_power_seconds = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))["low_power_seconds"]
    production = report_path.read_text(encoding="utf-8").split("## Production\n\n")[1].split("\n\n")[0]
    (row,) = production.splitlines()[2:]
    assert low_power_seconds == pytest.approx(30, abs=1)
    assert row.endswith(f"| {low_power_seconds} |")


# -- harvesters ------------------------------------------------------------------------------------

TWO_REFINERIES = (YARD, POWER, BARRACKS, REFINERY, ("proc", 18, 39), ("powr", 26, 50), WAR_FACTORY)


def harvesters(count: int, *, idle: tuple[int, ...] = ()) -> list[dict]:
    """So many harvesters, numbered from 300, at work unless named idle."""
    return [situations.unit(300 + number, "harv", 22, 41, idle=300 + number in idle) for number in range(count)]


def trains(orders: list[dict]) -> list[tuple[str, int]]:
    return [(order["item"], order["count"]) for order in with_command(orders, "train")]


def harvesters_trained(orders: list[dict]) -> int:
    """How many harvesters a batch orders."""
    return sum(count for item, count in trains(orders) if item == "harv")


def harvest_orders(orders: list[dict]) -> list[dict]:
    return with_command(orders, "harvest")


def test_with_two_refineries_three_harvesters_and_a_war_factory_one_harvester_is_trained():
    orders = ask(upkeep_agent(), base_state("england", *TWO_REFINERIES, units=harvesters(3)))

    assert harvesters_trained(orders) == 1


def test_no_harvester_is_trained_while_a_harvester_reservation_is_open():
    agent = upkeep_agent()
    ask(agent, base_state("england", *TWO_REFINERIES, units=harvesters(3), tick=4000))
    reserved = [(reservation.item, reservation.queue) for reservation in agent.ledger.reservations]

    second = ask(agent, base_state("england", *TWO_REFINERIES, units=harvesters(3), tick=4005))

    assert ("harv", "Vehicle") in reserved and harvesters_trained(second) == 0


def test_no_harvester_is_trained_while_one_is_in_the_vehicle_queue():
    state = base_state("england", *TWO_REFINERIES, units=harvesters(2), production=[situations.job("Vehicle", "harv")])

    assert harvesters_trained(ask(upkeep_agent(), state)) == 0


@pytest.mark.parametrize(
    "refineries, count, trained",
    [
        (1, 1, True),
        (1, 2, False),
        (2, 3, True),
        (2, 4, False),
        (3, 5, True),
        (3, 6, False),
        (4, 7, True),
        (4, 8, False),
        (5, 8, False),
    ],
    ids=["1 of 2", "2 of 2", "3 of 4", "4 of 4", "5 of 6", "6 of 6", "7 of 8", "8 of 8", "capped at 8"],
)
def test_the_target_is_two_harvesters_per_refinery_and_never_more_than_eight(refineries, count, trained):
    more = [("proc", 30 + 4 * number, 60) for number in range(refineries - 1)]
    state = base_state("england", YARD, POWER, BARRACKS, REFINERY, *more, WAR_FACTORY, units=harvesters(count))

    assert harvesters_trained(ask(upkeep_agent(), state)) == (1 if trained else 0)


def test_no_harvester_is_trained_without_a_war_factory_or_a_refinery():
    without_war_factory = base_state("england", YARD, POWER, BARRACKS, REFINERY, units=harvesters(1))
    without_refinery = base_state("england", YARD, POWER, BARRACKS, WAR_FACTORY)

    assert harvesters_trained(ask(upkeep_agent(), without_war_factory)) == 0
    assert harvesters_trained(ask(upkeep_agent(), without_refinery)) == 0


def idle_run(agent, ticks: list[int], idle_at: set[int], count: int = 4) -> list[list[dict]]:
    """Show an agent four harvesters at each tick, number 300 idle at the ticks named; the harvest orders of each."""
    return [
        harvest_orders(
            ask(
                agent,
                base_state(
                    "england",
                    *TWO_REFINERIES,
                    units=harvesters(count, idle=(300,) if tick in idle_at else ()),
                    tick=tick,
                ),
            )
        )
        for tick in ticks
    ]


def test_a_harvester_idle_for_three_consecutive_states_is_sent_back_to_harvest_without_a_cell():
    sent = idle_run(upkeep_agent(), [4000, 4005, 4010], idle_at={4000, 4005, 4010})

    assert sent == [[], [], [{"cmd": "harvest", "units": [300]}]]


def test_idleness_that_is_interrupted_starts_counting_again():
    sent = idle_run(upkeep_agent(), [4000, 4005, 4010, 4015, 4020, 4025], idle_at={4000, 4005, 4015, 4020, 4025})

    assert sent == [[], [], [], [], [], [{"cmd": "harvest", "units": [300]}]]


def test_a_harvester_that_was_sent_back_is_not_sent_again_within_250_ticks():
    agent = upkeep_agent()
    ticks = list(range(4000, 4300, 5))

    sent = idle_run(agent, ticks, idle_at=set(ticks))

    sent_at = [tick for tick, orders in zip(ticks, sent) if orders]
    assert sent_at == [4010, 4260]


def test_every_harvester_that_idles_is_sent_in_one_order_and_the_busy_ones_are_left_alone():
    agent = upkeep_agent()
    idle = (301, 303)
    states = [
        base_state("england", *TWO_REFINERIES, units=harvesters(4, idle=idle), tick=tick) for tick in (4000, 4005, 4010)
    ]

    sent = [harvest_orders(ask(agent, state)) for state in states]

    assert sent[-1] == [{"cmd": "harvest", "units": [301, 303]}] and sent[:2] == [[], []]


def test_a_harvester_that_is_gone_is_forgotten():
    agent = upkeep_agent()
    idle_run(agent, [4000, 4005], idle_at={4000, 4005})

    gone = harvest_orders(ask(agent, base_state("england", *TWO_REFINERIES, units=harvesters(4)[1:], tick=4010)))
    back = idle_run(agent, [4015, 4020], idle_at={4015, 4020})

    assert gone == [] and back == [[], []]


# -- harvester parking -----------------------------------------------------------------------------

# The middle of the construction yard's cells, and the parking point 8 cells behind it from the enemy's start (74, 48).
YARD_MIDDLE = (22, 49)
PARKING_POINT = (14, 49)
PARK_DISTANCE = 8


def world_of(*buildings: tuple[str, int, int], reference: dict | None = None, **observation):
    """The world and terrain an upkeep agent has after one state of an England base with these buildings."""
    agent = upkeep_agent()
    ask(agent, base_state("england", *buildings, **observation), situations.context(reference))
    return agent.world, agent.ctx.terrain


def is_open(cell: tuple[int, int], world, terrain) -> bool:
    """A cell a harvester can wait on: in the map, not blocked by the terrain, under no own building."""
    covered = {
        building_cell
        for building in world.buildings.values()
        for building_cell in placement.standing_box(building).cells()
    }
    return world.in_bounds(*cell) and cell not in terrain.blocked and cell not in covered


def test_the_parking_cell_is_8_cells_behind_the_yard_from_the_enemy_start_and_open(reference):
    world, terrain = world_of(YARD, POWER, BARRACKS, REFINERY, WAR_FACTORY, reference=reference)

    cell = parking.parking_cell(world, terrain, PARK_DISTANCE)

    assert cell == PARKING_POINT and is_open(cell, world, terrain) and len(terrain.blocked) > 0
    assert cells.distance(cell, YARD_MIDDLE) == pytest.approx(PARK_DISTANCE, abs=0.5)
    assert cells.distance(cell, world.enemy_base) > cells.distance(YARD_MIDDLE, world.enemy_base) + PARK_DISTANCE - 1


def test_a_parking_point_the_terrain_blocks_moves_to_the_nearest_open_cell():
    world, _ = world_of(YARD, POWER)
    blocked = Terrain(
        blocked=[(PARKING_POINT[0] + dx, PARKING_POINT[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
    )

    cell = parking.parking_cell(world, blocked, PARK_DISTANCE)

    assert cell == (14, 47) and cells.distance(cell, PARKING_POINT) == 2 and is_open(cell, world, blocked)


def test_a_parking_point_under_a_building_moves_to_the_nearest_cell_outside_every_building():
    # A power plant reported at (14, 49) covers (13, 48) to (14, 50): the point and its neighbours above and to the west.
    world, terrain = world_of(YARD, ("powr", 14, 49))

    cell = parking.parking_cell(world, terrain, PARK_DISTANCE)

    assert cell == (15, 49) and is_open(cell, world, terrain)


def test_a_parking_point_beyond_the_edge_of_the_map_moves_to_the_nearest_cell_inside_it():
    world, terrain = world_of(("fact", 3, 48))

    assert parking.parking_cell(world, terrain, PARK_DISTANCE) == (0, 49)


def test_two_yards_the_parking_cell_is_measured_from_the_yard_nearest_the_base_s_center():
    # The far yard is listed first; the base's center is the first yard's cell (22, 48).
    two_yards, terrain = world_of(("fact", 50, 40), YARD, POWER)
    one_yard, _ = world_of(YARD, POWER)

    assert parking.parking_cell(two_yards, terrain, PARK_DISTANCE) == parking.parking_cell(one_yard, terrain, 8)
    assert parking.parking_cell(two_yards, terrain, PARK_DISTANCE) == PARKING_POINT


def test_without_a_yard_the_parking_cell_is_measured_from_the_middle_of_the_refineries():
    # The refineries' middles are (24, 40) and (18, 40): their centroid (21, 40) lies 8 cells from (13, 39).
    world, terrain = world_of(REFINERY, ("proc", 18, 39))

    assert parking.parking_cell(world, terrain, PARK_DISTANCE) == (13, 39)


def test_there_is_no_parking_cell_without_a_yard_or_a_refinery_or_an_enemy_start():
    without_base, terrain = world_of(POWER, BARRACKS)
    state = base_state("england", YARD, POWER)
    del state["legal_actions"]["enemy_base"]
    agent = upkeep_agent()
    ask(agent, state)

    assert parking.parking_cell(without_base, terrain, PARK_DISTANCE) is None
    assert agent.world.enemy_base is None and parking.parking_cell(agent.world, terrain, PARK_DISTANCE) is None


def test_there_is_no_parking_cell_when_no_open_cell_lies_within_twice_the_distance_of_the_point():
    world, _ = world_of(YARD, POWER)
    reach = 2 * PARK_DISTANCE
    around = [
        (PARKING_POINT[0] + dx, PARKING_POINT[1] + dy)
        for dx in range(-reach, reach + 1)
        for dy in range(-reach, reach + 1)
    ]
    open_just_beyond = Terrain(blocked=[cell for cell in around if cells.distance(cell, PARKING_POINT) <= reach])

    assert parking.parking_cell(world, open_just_beyond, PARK_DISTANCE) is None
    assert parking.parking_cell(world, Terrain(blocked=around[:1]), PARK_DISTANCE) == PARKING_POINT


ALL_HARVESTERS = [300, 301, 302, 303]
PARK = {"cmd": "move", "units": ALL_HARVESTERS, "to": list(PARKING_POINT)}
# Eight enemy rifles 10 cells east of the construction yard: a major threat, and more than 6 cells from the harvesters.
RIFLES = [{"id": 500 + number, "type": "e1", "x": 32, "y": 44 + number, "hp": 100} for number in range(8)]


def parking_agent(*managers, plan: str = HOLD, park: bool = True):
    """An agent with the upkeep manager and the managers given, under OPENING until a war factory has stood and `plan`
    after; it keeps to two refineries and parks its harvesters when `park` is true."""
    upkeep = dataclasses.replace(TWO_REFINERIES_ONLY.upkeep, park_harvesters=park)
    tuning = dataclasses.replace(TWO_REFINERIES_ONLY, upkeep=upkeep)
    return new_agent(UpkeepManager(), *managers, plan_selector=OpeningThen(plan), tuning=tuning)


def late_base(tick: int, seconds_left: float | None = 100, units: list[dict] | None = None, **observation) -> dict:
    """The base with two refineries at a tick with so many seconds of the match left (None: not known), and four
    harvesters at work unless the units are given."""
    state = base_state(
        "england", *TWO_REFINERIES, units=harvesters(4) if units is None else units, tick=tick, **observation
    )
    state["time"]["remaining_seconds"] = seconds_left
    return state


def harvester_orders(orders: list[dict]) -> list[dict]:
    """The moves and the harvest orders of a batch that name a harvester."""
    return [order for order in with_command(orders, "move", "harvest") if set(order["units"]) & set(ALL_HARVESTERS)]


def test_park_with_the_switch_on_and_100_s_left_every_harvester_gets_one_move_to_the_parking_cell_and_no_harvest():
    agent = parking_agent()
    # Idle from the second state on, as a harvester that has arrived stands: the idle rule would send it to harvest.
    batches = [
        ask(agent, late_base(tick, units=harvesters(4, idle=tuple(ALL_HARVESTERS) if tick > 4000 else ())))
        for tick in range(4000, 4100, 5)
    ]

    (move,) = [entry for entry in agent.ledger.entries if entry.order["cmd"] == "move"]
    assert harvester_orders(batches[0]) == [PARK] and move.priority == Priority.PRODUCTION
    assert [harvester_orders(orders) for orders in batches[1:]] == [[]] * (len(batches) - 1)


def test_park_a_harvester_within_3_cells_of_the_parking_cell_gets_no_order():
    near = [situations.unit(300, "harv", 15, 50), situations.unit(301, "harv", 17, 49)]
    farther = [situations.unit(302, "harv", 18, 49), situations.unit(303, "harv", 22, 41)]

    orders = ask(parking_agent(), late_base(4000, units=[*near, *farther]))

    assert harvester_orders(orders) == [{**PARK, "units": [302, 303]}]


def test_park_a_harvester_that_comes_later_is_sent_and_one_sent_already_is_not_sent_again():
    agent = parking_agent()

    first = ask(agent, late_base(4000, units=harvesters(2)))
    later = ask(agent, late_base(4005, units=harvesters(3)))

    assert harvester_orders(first) == [{**PARK, "units": [300, 301]}]
    assert harvester_orders(later) == [{**PARK, "units": [302]}]


@pytest.mark.parametrize(
    "seconds_left, parks", [(121, False), (120, True), (None, False)], ids=["121 s", "120 s", "unknown"]
)
def test_park_from_120_s_left_on_and_never_for_the_time_while_it_is_not_known(seconds_left, parks):
    orders = ask(parking_agent(), late_base(4000, seconds_left))

    assert harvester_orders(orders) == ([PARK] if parks else [])


@pytest.mark.parametrize(
    "plan, enemies, seconds_left, threat, parks",
    [
        (PRESERVE, RIFLES, 150, MAJOR_THREAT, True),
        (PRESERVE, RIFLES, None, MAJOR_THREAT, True),
        (PRESERVE, [], 150, NO_THREAT, False),
        (PRESERVE, RIFLES[:1], 150, MINOR_THREAT, False),
        (HOLD, RIFLES, 150, MAJOR_THREAT, False),
    ],
    ids=["preserve, major threat", "time not known", "no threat", "minor threat", "hold, major threat"],
)
def test_park_under_preserve_with_a_major_threat_the_harvesters_park_before_the_last_120_s(
    plan, enemies, seconds_left, threat, parks
):
    agent = parking_agent(BaseDefenseManager(), plan=plan)

    orders = ask(agent, late_base(4000, seconds_left, visible_enemies=enemies))

    assert (agent.current_plan, agent.ctx.threat_level) == (plan, threat)
    assert harvester_orders(orders) == ([PARK] if parks else [])


def test_park_a_harvester_with_an_enemy_within_the_flee_distance_flees_and_parks_a_cycle_after_flight_lets_it_go():
    agent = parking_agent(BaseDefenseManager())
    chased = [situations.unit(300, "harv", 40, 30, idle=False), *harvesters(4)[1:]]
    # Four cells from the harvester at (40, 30), and far from every building: no threat to the base.
    raider = [{"id": 500, "type": "e1", "x": 44, "y": 30, "hp": 100}]

    fled = ask(agent, late_base(4000, units=chased, visible_enemies=raider))
    sheltering = ask(agent, late_base(4060, units=chased))
    let_go = ask(agent, late_base(4125, units=chased))
    parked = ask(agent, late_base(4130, units=chased))

    (shelter,) = [
        entry for entry in agent.ledger.entries if entry.sent_tick == 4000 and entry.order.get("units") == [300]
    ]
    assert harvester_orders(fled) == [
        {"cmd": "move", "units": [300], "to": [22, 48]},
        {**PARK, "units": [301, 302, 303]},
    ]
    assert shelter.priority == Priority.EMERGENCY and harvester_orders(sheltering) == []
    assert harvester_orders(let_go) == [{"cmd": "harvest", "units": [300]}]
    assert harvester_orders(parked) == [{**PARK, "units": [300]}]


def test_park_with_the_switch_off_the_harvester_orders_are_those_of_a_match_with_time_to_go():
    def harvester_orders_over(seconds_left: float) -> list[list[dict]]:
        agent = parking_agent(BaseDefenseManager(), plan=PRESERVE, park=False)
        return [
            harvester_orders(
                ask(agent, late_base(tick, seconds_left, units=harvesters(4, idle=(300,)), visible_enemies=RIFLES))
            )
            for tick in (4000, 4005, 4010)
        ]

    late = harvester_orders_over(100)

    assert late == harvester_orders_over(1000) == [[], [], [{"cmd": "harvest", "units": [300]}]]


# The ticks of a threat beaten off: the rifles stand at the first two and are gone from RIFLES_GONE_AT on.
THREAT_TICKS = (4000, 4005, 4010, 4125, 4130, 4135, 4140, 4145, 4380)
RIFLES_GONE_AT = 4010
# An enemy rifle 4 cells from a harvester at (40, 30), far from every building.
RAIDER = [{"id": 600, "type": "e1", "x": 44, "y": 30, "hp": 100}]


def enemies_at(tick: int, raider_at: int) -> list[dict]:
    """The enemies in sight at a tick of a threat beaten off: the rifles until they leave, the raider at its tick."""
    rifles = RIFLES if tick < RIFLES_GONE_AT else []
    return [*rifles, *(RAIDER if tick == raider_at else [])]


def units_at(tick: int, units: list[dict] | None) -> list[dict]:
    """The units given, or four harvesters at work that stand idle from the moment the rifles leave."""
    if units is not None:
        return units
    return harvesters(4, idle=tuple(ALL_HARVESTERS) if tick >= RIFLES_GONE_AT else ())


def threat_beaten_off(agent, units: list[dict] | None = None, seconds_left: float = 150, raider_at: int = 0):
    """Show an agent eight rifles at ticks 4000 and 4005 and none from 4010 on, with so many seconds left; the
    harvester orders of each state by its tick. Base defense ends the threat at 4130, 125 ticks after the rifles.

    The harvesters stand idle from 4010 on unless the units are given; with `raider_at` the raider stands near
    harvester 300 at that tick.
    """
    return {
        tick: harvester_orders(
            ask(
                agent,
                late_base(tick, seconds_left, units=units_at(tick, units), visible_enemies=enemies_at(tick, raider_at)),
            )
        )
        for tick in THREAT_TICKS
    }


def test_park_under_preserve_harvesters_parked_for_a_threat_harvest_again_in_the_first_cycle_after_it_is_over():
    agent = parking_agent(BaseDefenseManager(), plan=PRESERVE)

    sent = threat_beaten_off(agent)

    (release,) = [
        entry for entry in agent.ledger.entries if entry.order["cmd"] == "harvest" and entry.sent_tick == 4130
    ]
    assert sent[4000] == [PARK] and sent[4005] == sent[4010] == sent[4125] == []
    assert sent[4130] == [{"cmd": "harvest", "units": ALL_HARVESTERS}] and release.priority == Priority.PRODUCTION
    # The harvesters that idle on are not told again by the idle-harvester rule until 250 ticks have passed.
    assert sent[4135] == sent[4140] == sent[4145] == [] and sent[4380] == [{"cmd": "harvest", "units": ALL_HARVESTERS}]


def test_park_a_harvester_still_on_its_way_when_parking_ends_is_told_to_harvest_with_the_parked_ones():
    on_its_way = [situations.unit(300, "harv", 19, 46, idle=False), *harvesters(4, idle=(301, 302, 303))[1:]]

    sent = threat_beaten_off(parking_agent(BaseDefenseManager(), plan=PRESERVE), units=on_its_way)

    assert sent[4130] == [{"cmd": "harvest", "units": ALL_HARVESTERS}]


def test_park_a_harvester_fleeing_when_parking_ends_is_left_to_base_defense():
    chased = [situations.unit(300, "harv", 40, 30, idle=False), *harvesters(4)[1:]]

    sent = threat_beaten_off(parking_agent(BaseDefenseManager(), plan=PRESERVE), units=chased, raider_at=4125)

    assert sent[4125] == [{"cmd": "move", "units": [300], "to": [22, 48]}]
    assert sent[4130] == [{"cmd": "harvest", "units": [301, 302, 303]}]


def test_park_in_the_last_120_s_the_harvesters_stay_parked_after_the_threat_is_over():
    sent = threat_beaten_off(parking_agent(BaseDefenseManager(), plan=PRESERVE), seconds_left=100)

    assert sent[4000] == [PARK] and [orders for tick, orders in sent.items() if tick > 4000] == [[]] * 8


def test_park_without_a_parking_cell_the_harvesters_keep_mining_with_the_switch_on():
    agent = parking_agent()
    states = [late_base(tick, units=harvesters(4, idle=(300,))) for tick in (4000, 4005, 4010)]
    for state in states:
        del state["legal_actions"]["enemy_base"]

    sent = [harvester_orders(ask(agent, state)) for state in states]

    assert agent.world.enemy_base is None and sent == [[], [], [{"cmd": "harvest", "units": [300]}]]


def test_park_one_harvesters_parked_event_is_written_as_parking_starts_with_its_reason(match_record):
    agent = parking_agent()
    for tick in range(4000, 4100, 5):
        ask(agent, late_base(tick))

    assert match_record(parking.PARKED_EVENT) == [
        {
            "reason": parking.TIME_LEFT,
            "cell": list(PARKING_POINT),
            "harvesters": 4,
            "tick": 4000,
            "game_seconds": 160.0,
            "remaining_seconds": 100,
            "plan": HOLD,
            "threat_level": NO_THREAT,
        }
    ]


def test_park_each_start_of_parking_writes_its_own_event_and_the_end_of_the_match_parks_on_without_one(match_record):
    agent = parking_agent(BaseDefenseManager(), plan=PRESERVE)
    threat_beaten_off(agent)
    # A second attack after the release, and the last 120 s reached while the harvesters are parked for it.
    ask(agent, late_base(4400, 150, visible_enemies=RIFLES))
    ask(agent, late_base(4405, 100, visible_enemies=RIFLES))
    ask(agent, late_base(4600, 100))

    events = match_record(parking.PARKED_EVENT)

    assert [(event["tick"], event["reason"], event["threat_level"]) for event in events] == [
        (4000, parking.PRESERVE_MAJOR_THREAT, MAJOR_THREAT),
        (4400, parking.PRESERVE_MAJOR_THREAT, MAJOR_THREAT),
    ]
    assert {event["plan"] for event in events} == {PRESERVE}


def test_park_no_harvesters_parked_event_is_written_with_the_switch_off_or_without_a_parking_cell(match_record):
    switched_off = parking_agent(park=False)
    without_a_cell = parking_agent()
    no_enemy_start = late_base(4000)
    del no_enemy_start["legal_actions"]["enemy_base"]

    ask(switched_off, late_base(4000))
    ask(without_a_cell, no_enemy_start)

    assert match_record(parking.PARKED_EVENT) == []


# -- the unit queues -------------------------------------------------------------------------------

# A base past its opening, with as many harvesters as it wants and the opening's defense against infantry: the
# plan is HOLD and both unit queues are empty.
HOLDING = {
    "england": (*TWO_REFINERIES, ("pbox", 27, 46)),
    "russia": (
        YARD,
        POWER,
        ("barr", 22, 44),
        REFINERY,
        ("proc", 18, 39),
        ("powr", 26, 50),
        WAR_FACTORY,
        ("ftur", 27, 46),
    ),
}


def holding(faction: str = "england", cash: int = 5000, units: list | None = None, **more) -> dict:
    """A state of a base in the HOLD plan with empty queues, and four harvesters unless the units are given."""
    return base_state(faction, *HOLDING[faction], units=harvesters(4) if units is None else units, cash=cash, **more)


def trained(orders: list[dict], queue: str) -> list[tuple[str, int]]:
    """The (item, count) of the train orders of a batch for the items of one queue."""
    items = situations.tracked_items()
    return [(item, count) for item, count in trains(orders) if items[item].queue == queue]


def soldiers(item: str, count: int, first_id: int = 400) -> list[dict]:
    """So many own units of a type, numbered from `first_id`."""
    return [situations.unit(first_id + number, item) for number in range(count)]


# Three rifles standing: the Allied base row of the mix is then short of rockets, which the money rules are shown on.
THREE_RIFLES = soldiers("e1", 3, first_id=450)


def with_rifles(faction: str = "england", **more) -> dict:
    """The holding base with four harvesters and three rifles standing."""
    return holding(faction, units=[*harvesters(4), *THREE_RIFLES], **more)


def test_an_empty_infantry_queue_gets_three_of_the_item_the_army_is_furthest_short_of():
    # The base rows of the mix ask most of the infantry's value in rifles; with rifles standing, rockets, the item
    # with the next largest share in both rows.
    assert trained(ask(upkeep_agent(), holding("england")), "Infantry") == [("e1", 3)]
    assert trained(ask(upkeep_agent(), holding("russia")), "Infantry") == [("e1", 3)]
    with_rifles = [holding(faction, units=[*harvesters(4), *THREE_RIFLES]) for faction in ("england", "russia")]
    assert [trained(ask(upkeep_agent(), state), "Infantry") for state in with_rifles] == [[("e3", 3)], [("e3", 3)]]


def test_an_empty_vehicle_queue_gets_one_vehicle_of_choice():
    assert trained(ask(upkeep_agent(), holding("england")), "Vehicle") == [("1tnk", 1)]
    assert trained(ask(upkeep_agent(), holding("russia")), "Vehicle") == [("ftrk", 1)]


@pytest.mark.parametrize("cash, queued", [(999, []), (1000, [("1tnk", 1)])], ids=["cost plus 299", "cost plus 300"])
def test_a_vehicle_is_queued_only_when_cash_covers_its_cost_and_the_reserve_of_300(cash, queued):
    busy_infantry = [situations.job("Infantry", "e1")]

    orders = ask(upkeep_agent(), holding("england", cash=cash, production=busy_infantry))

    assert trains(orders) == queued


@pytest.mark.parametrize(
    "cash, queued",
    [(599, []), (600, [("e3", 1)]), (899, [("e3", 1)]), (900, [("e3", 2)]), (1200, [("e3", 3)]), (9000, [("e3", 3)])],
)
def test_infantry_is_queued_as_far_as_the_cash_above_the_reserve_goes(cash, queued):
    busy_vehicles = [situations.job("Vehicle", "1tnk")]

    orders = ask(upkeep_agent(), with_rifles(cash=cash, production=busy_vehicles))

    assert trains(orders) == queued


@pytest.mark.parametrize("queue, item", [("Infantry", "e1"), ("Vehicle", "1tnk")])
def test_a_queue_that_holds_an_item_is_given_nothing(queue, item):
    orders = ask(upkeep_agent(), holding("england", production=[situations.job(queue, item)]))

    assert trained(orders, queue) == []


def test_a_queue_with_an_order_on_its_way_is_given_nothing_more():
    agent = upkeep_agent()
    first = ask(agent, holding("england", tick=5000))

    second = ask(agent, holding("england", tick=5005))

    assert len(trains(first)) == 2 and trains(second) == []
    assert agent.ledger.duplicate_production_orders == 0


def test_deficit_the_next_infantry_item_follows_what_the_army_already_has():
    busy_vehicles = [situations.job("Vehicle", "1tnk")]
    armies = [[], soldiers("e1", 3), [*soldiers("e1", 3), *soldiers("e3", 2, first_id=410)]]

    rounds = [
        ask(upkeep_agent(), holding("england", units=[*harvesters(4), *army], production=busy_vehicles))
        for army in armies
    ]

    # Rifles and rockets are asked for at 50 and 15 of the Allied base row's 65 points of infantry: none, then
    # rifles only, then rockets worth twice the rifles.
    assert [trains(orders) for orders in rounds] == [[("e1", 3)], [("e3", 3)], [("e1", 3)]]


def test_an_item_that_cannot_be_paid_for_is_waited_for_and_not_passed_over():
    agent = upkeep_agent()
    busy_vehicles = [situations.job("Vehicle", "1tnk")]
    ask(agent, with_rifles(cash=500, tick=5000, production=busy_vehicles))

    poor = ask(agent, with_rifles(cash=599, tick=5100, production=busy_vehicles))
    rich = ask(agent, with_rifles(cash=600, tick=5200, production=busy_vehicles))

    assert (trains(poor), trains(rich)) == ([], [("e3", 1)])


def test_an_item_that_is_not_on_offer_is_skipped_for_the_cycle():
    orders = ask(upkeep_agent(), holding("england", withheld=["e3"]))

    assert trained(orders, "Infantry") == [("e1", 3)]


def test_a_queue_none_of_whose_items_is_on_offer_is_given_nothing():
    orders = ask(upkeep_agent(), holding("england", withheld=["e1", "e3", "1tnk"]))

    assert trains(orders) == []


def test_a_queue_another_manager_claims_is_left_to_it():
    agent = upkeep_agent()
    agent.ctx.claims.hold("Infantry", "opening")

    orders = ask(agent, holding("england"))

    assert (trained(orders, "Infantry"), trained(orders, "Vehicle")) == ([], [("1tnk", 1)])


def test_while_a_harvester_is_wanted_the_vehicle_queue_makes_the_harvester_and_nothing_else():
    state = base_state("england", *TWO_REFINERIES, units=harvesters(3))

    assert trained(ask(upkeep_agent(), state), "Vehicle") == [("harv", 1)]


def test_in_the_opening_plan_the_unit_queues_are_given_nothing():
    before_the_war_factory = base_state("england", YARD, POWER, BARRACKS, REFINERY, units=harvesters(1))

    assert trains(ask(upkeep_agent(), before_the_war_factory)) == []


def composed(infantry: dict[str, float], vehicle: dict[str, float], **agent_options):
    """An upkeep agent whose plans all make the items given, with the shares of the army's value given."""
    agent = upkeep_agent(**agent_options)
    agent.ctx.composition = lambda plan, family, world: Composition(infantry=infantry, vehicle=vehicle)
    return agent


ROCKETS_HALF = {"e3": 0.5}
JEEPS_AND_TANKS = {"jeep": 0.5 * 5 / 12, "1tnk": 0.5 * 7 / 12}


def test_the_queues_follow_the_composition_of_the_plan_in_force():
    # Jeeps and light tanks are asked for at 5/12 and 7/12 of the vehicles' value.
    first = ask(composed(ROCKETS_HALF, JEEPS_AND_TANKS), holding("england"))
    with_a_tank = ask(
        composed(ROCKETS_HALF, JEEPS_AND_TANKS), holding("england", units=[*harvesters(4), *soldiers("1tnk", 1)])
    )

    assert trains(first) == [("e3", 3), ("1tnk", 1)]
    assert trains(with_a_tank) == [("e3", 3), ("jeep", 1)]


def test_after_the_service_depot_stands_the_vehicle_queue_makes_the_heavier_tank():
    with_depot = (*TWO_REFINERIES, ("fix", 26, 54))

    orders = ask(upkeep_agent(), base_state("england", *with_depot, units=harvesters(4)))

    assert trained(orders, "Vehicle") == [("2tnk", 1)]


# -- tech ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cash, harvester_count, queued",
    [(1500, 4, ["fix"]), (1499, 4, []), (1500, 3, []), (9000, 6, ["fix"])],
    ids=["1500 and 4", "one credit short", "one harvester short", "plenty"],
)
def test_the_service_depot_is_queued_when_cash_is_1500_and_four_harvesters_work(cash, harvester_count, queued):
    state = base_state("england", *TWO_REFINERIES, units=harvesters(harvester_count), cash=cash)

    assert builds(ask(upkeep_agent(), state)) == queued


def test_the_service_depot_is_queued_exactly_once():
    # Before the sixth minute, while the power target of 20 leaves the holding base power for the depot.
    agent = upkeep_agent()
    with_depot = (*TWO_REFINERIES, ("fix", 26, 54), ("powr", 30, 50))
    states = [
        holding(tick=3000),
        holding(tick=3005),
        holding(tick=3010, production=[situations.job("Building", "fix")]),
        holding(tick=3800, production=[situations.job("Building", "fix", ready=True)]),
        base_state("england", *with_depot, units=harvesters(4), tick=3805),
        base_state("england", *with_depot, units=harvesters(4), tick=4000),
    ]

    queued = [builds(ask(agent, state)).count("fix") for state in states]

    assert queued == [1, 0, 0, 0, 0, 0]
    assert agent.ledger.duplicate_production_orders == 0


def test_after_the_service_depot_stands_the_mix_s_vehicle_is_the_heavier_tank():
    agent = upkeep_agent()
    before = ask(agent, holding(tick=6000))
    with_depot = (*TWO_REFINERIES, ("fix", 26, 54), ("powr", 30, 50))

    after = ask(agent, base_state("england", *with_depot, units=harvesters(4), tick=6900))

    assert (trained(before, "Vehicle"), trained(after, "Vehicle")) == ([("1tnk", 1)], [("2tnk", 1)])
    assert mix_stage(agent.world) == DEPOT_STAGE


def test_no_service_depot_is_queued_before_the_war_factory_stands():
    state = base_state("england", YARD, POWER, BARRACKS, REFINERY, ("proc", 18, 39), units=harvesters(4))

    assert "fix" not in builds(ask(upkeep_agent(), state))


def test_the_service_depot_waits_for_the_building_queue():
    busy = holding(production=[situations.job("Building", "dome")])

    assert builds(ask(upkeep_agent(), busy)) == []


def test_a_service_depot_whose_order_came_to_nothing_is_ordered_again():
    agent = upkeep_agent()
    ask(agent, holding(tick=3000))

    assert builds(ask(agent, holding(tick=3075))) == ["fix"]


@pytest.mark.parametrize(
    "drained, queued",
    [(150, ["fix"]), (151, ["powr"]), (180, ["powr"])],
    ids=["the target of 20 left over", "one short", "headroom 20"],
)
def test_power_a_service_depot_that_would_take_the_headroom_below_the_target_gives_way_to_a_power_plant(
    drained, queued
):
    state = holding(economy=powered(200, drained, cash=5000, harvesters=4))

    assert builds(ask(upkeep_agent(), state)) == queued


# -- repair ----------------------------------------------------------------------------------------

REFINERY_ID = 104


def damaged(hp: int, *, repairing: bool = False, cash: int = 2000, ore: int = 0, tick: int = 5000) -> dict:
    """A state of a small base whose refinery (id 104) has so much hp left."""
    busy = [situations.job("Building", "weap")]
    state = base_state("england", *BASE, cash=cash, ore=ore, tick=tick, production=busy)
    refinery = state["observation"]["buildings"][3]
    refinery.update(hp=hp, repairing=repairing)
    return state


def repairs(orders: list[dict]) -> list[dict]:
    return with_command(orders, "repair")


@pytest.mark.parametrize("cash, switched_on", [(300, True), (299, False), (5000, True)])
def test_a_building_at_60_percent_hp_gets_repair_on_when_cash_is_at_least_300(cash, switched_on):
    orders = ask(upkeep_agent(), damaged(60, cash=cash))

    assert repairs(orders) == ([{"cmd": "repair", "building": REFINERY_ID, "on": True}] if switched_on else [])


@pytest.mark.parametrize("hp, switched_on", [(74, True), (75, False), (100, False), (1, True)])
def test_repair_is_switched_on_only_below_75_percent_hp(hp, switched_on):
    assert bool(repairs(ask(upkeep_agent(), damaged(hp)))) is switched_on


@pytest.mark.parametrize(
    "hp, cash, switched_off",
    [(96, 2000, True), (95, 2000, False), (60, 99, True), (60, 100, False), (100, 0, True)],
    ids=["above 95%", "at 95%", "cash below 100", "cash at 100", "whole and broke"],
)
def test_repair_is_switched_off_above_95_percent_hp_or_when_cash_is_below_100(hp, cash, switched_off):
    orders = ask(upkeep_agent(), damaged(hp, repairing=True, cash=cash))

    assert repairs(orders) == ([{"cmd": "repair", "building": REFINERY_ID, "on": False}] if switched_off else [])


PILLBOX_ID = 105


def damaged_pillbox(hp: int, refinery_hp: int = 100) -> dict:
    """A state of the small base with a pillbox (id 105) of so much hp beside it, and the refinery at its own."""
    state = damaged(refinery_hp)
    buildings = state["observation"]["buildings"]
    buildings.append({**buildings[0], "id": PILLBOX_ID, "type": "pbox", "x": 30, "y": 48, "hp": hp, "repairing": False})
    return state


@pytest.mark.parametrize(
    "early, hp, refinery_hp, switched_on",
    [
        (True, 80, 80, [PILLBOX_ID]),
        (True, 94, 100, [PILLBOX_ID]),
        (True, 95, 100, []),
        (False, 80, 80, []),
        (False, 74, 100, [PILLBOX_ID]),
    ],
    ids=["defense at 80%", "defense at 94%", "defense at 95%", "switch off at 80%", "switch off at 74%"],
)
def test_repair_defenses_early_a_defense_is_repaired_below_95_percent_and_another_building_below_75(
    early, hp, refinery_hp, switched_on
):
    upkeep = dataclasses.replace(TWO_REFINERIES_ONLY.upkeep, repair_defenses_early=early)
    agent = upkeep_agent(tuning=dataclasses.replace(TWO_REFINERIES_ONLY, upkeep=upkeep))

    orders = ask(agent, damaged_pillbox(hp, refinery_hp))

    assert [order["building"] for order in repairs(orders) if order["on"]] == switched_on


def test_a_building_that_is_being_repaired_is_not_switched_on_again():
    assert repairs(ask(upkeep_agent(), damaged(40, repairing=True))) == []


def test_a_repair_on_is_not_sent_again_within_250_ticks_while_the_state_never_shows_the_repair():
    agent = upkeep_agent()
    ticks = [5000, 5005, 5075, 5150, 5245, 5250]

    sent = [bool(repairs(ask(agent, damaged(60, tick=tick)))) for tick in ticks]

    assert sent == [True, False, False, False, False, True]


def test_a_building_at_60_percent_held_over_10_consecutive_states_not_shown_repairing_gets_one_repair_on():
    agent = upkeep_agent()

    sent = [order for tick in range(5000, 5050, 5) for order in repairs(ask(agent, damaged(60, tick=tick)))]

    assert sent == [{"cmd": "repair", "building": REFINERY_ID, "on": True}]


def test_ten_states_25_ticks_apart_also_get_one_repair_on():
    agent = upkeep_agent()

    sent = [order for tick in range(5000, 5250, 25) for order in repairs(ask(agent, damaged(60, tick=tick)))]

    assert sent == [{"cmd": "repair", "building": REFINERY_ID, "on": True}]


def answer_the_repair(agent, orders: list[dict], status: str) -> None:
    """Hand the agent the platform's answer to its batch: the repair order in it with this status."""
    index = next(position for position, order in enumerate(orders) if order["cmd"] == "repair")
    row = {"index": index, "cmd": "repair", "status": status, "reason": "not_owned" if status == "rejected" else status}
    agent.on_action_result({"results": [row]}, situations.context())


def test_a_repair_on_that_was_rejected_is_sent_again_after_75_ticks():
    agent = upkeep_agent()
    answer_the_repair(agent, ask(agent, damaged(60, tick=5000)), "rejected")

    sent = [bool(repairs(ask(agent, damaged(60, tick=tick)))) for tick in (5005, 5070, 5075)]

    assert sent == [False, False, True]


def test_a_repair_on_that_was_lost_is_sent_again_once_by_the_ledger_and_not_by_the_rule_besides():
    agent = upkeep_agent()
    answer_the_repair(agent, ask(agent, damaged(60, tick=5000)), "lost")

    again = repairs(ask(agent, damaged(60, tick=5005)))
    later = [order for tick in (5080, 5160, 5240) for order in repairs(ask(agent, damaged(60, tick=tick)))]

    assert again == [{"cmd": "repair", "building": REFINERY_ID, "on": True}] and later == []


def test_a_repair_off_is_still_sent_again_after_75_ticks_while_the_state_shows_the_repair():
    agent = upkeep_agent()

    sent = [bool(repairs(ask(agent, damaged(97, repairing=True, tick=tick)))) for tick in (5000, 5070, 5075)]

    assert sent == [True, False, True]


def test_a_switch_the_other_way_is_sent_at_once():
    agent = upkeep_agent()
    ask(agent, damaged(60, tick=5000))

    off = repairs(ask(agent, damaged(60, repairing=True, cash=50, tick=5005)))
    on = repairs(ask(agent, damaged(60, cash=2000, tick=5010)))

    assert [order["on"] for order in (*off, *on)] == [False, True]


def test_every_damaged_building_gets_its_own_order_and_each_carries_an_explicit_on():
    state = damaged(60)
    state["observation"]["buildings"][0]["hp"] = 30
    state["observation"]["buildings"][1].update(hp=99, repairing=True)

    orders = repairs(ask(upkeep_agent(), state))

    assert orders == [
        {"cmd": "repair", "building": 101, "on": True},
        {"cmd": "repair", "building": 102, "on": False},
        {"cmd": "repair", "building": 104, "on": True},
    ]
    assert all(isinstance(order["on"], bool) for order in orders)


def test_the_fake_game_starts_the_repair_it_is_asked_for_and_the_order_is_not_repeated():
    game = FakeRedAlert("england", "russia")
    agent = own_agent()
    game.run(agent, 60)
    yard = game.own_buildings()[0]["id"]
    game.damage_own(yard, 60)

    played = game.run(agent, 20)

    sent = [order for batch in played.batches for order in repairs(batch["orders"])]
    assert sent == [{"cmd": "repair", "building": yard, "on": True}]
    assert game.own_buildings()[0]["repairing"] is True


# -- rally points ----------------------------------------------------------------------------------

# 10 cells from the base at (22, 48) toward the enemy's start at (74, 48).
STAGING = [32, 48]


def rallies(orders: list[dict]) -> list[dict]:
    return with_command(orders, "rally")


@pytest.mark.parametrize("faction, barracks", [("england", "tent"), ("russia", "barr")])
def test_a_new_barracks_has_its_rally_point_set_to_the_staging_point(faction, barracks):
    orders = ask(upkeep_agent(), base_state(faction, YARD, POWER, (barracks, 22, 44)))

    assert rallies(orders) == [{"cmd": "rally", "building": 103, "to": STAGING}]


def test_a_new_war_factory_has_its_rally_point_set_too_and_other_buildings_have_none():
    agent = upkeep_agent()
    ask(agent, base_state("england", YARD, POWER, BARRACKS, REFINERY, tick=3000))

    orders = ask(agent, base_state("england", YARD, POWER, BARRACKS, REFINERY, WAR_FACTORY, tick=3600))

    assert rallies(orders) == [{"cmd": "rally", "building": 105, "to": STAGING}]


def test_a_rally_point_is_set_once():
    agent = upkeep_agent()
    first = ask(agent, base_state("england", YARD, POWER, BARRACKS, tick=1000))

    later = [
        rallies(ask(agent, base_state("england", YARD, POWER, BARRACKS, tick=tick))) for tick in (1005, 1100, 3000)
    ]

    assert len(rallies(first)) == 1 and later == [[], [], []]


def test_the_staging_point_lies_toward_the_enemy_and_inside_the_map():
    north = base_state("england", YARD, POWER, BARRACKS)
    north["legal_actions"]["enemy_base"] = [22, 8]
    at_the_edge = base_state("england", ("fact", 90, 48), ("powr", 87, 48), ("tent", 90, 44))
    at_the_edge["observation"]["base_center"] = [90, 48]
    at_the_edge["legal_actions"]["enemy_base"] = [120, 48]

    assert rallies(ask(upkeep_agent(), north))[0]["to"] == [22, 38]
    assert rallies(ask(upkeep_agent(), at_the_edge))[0]["to"] == [95, 48]


def test_the_rally_point_waits_until_the_enemy_s_start_is_known():
    agent = upkeep_agent()
    unknown = base_state("england", YARD, POWER, BARRACKS, tick=1000)
    unknown["legal_actions"]["enemy_base"] = None

    before = rallies(ask(agent, unknown))
    after = rallies(ask(agent, base_state("england", YARD, POWER, BARRACKS, tick=1005)))

    assert (before, after) == ([], [{"cmd": "rally", "building": 103, "to": STAGING}])


def test_with_a_gather_point_published_each_producer_is_rallied_to_it_and_again_whenever_it_moves():
    agent = upkeep_agent()
    agent.ctx.gather_point = (43, 48)
    first = rallies(ask(agent, base_state("england", YARD, POWER, BARRACKS, tick=1000)))
    same = rallies(ask(agent, base_state("england", YARD, POWER, BARRACKS, tick=1005)))

    agent.ctx.gather_point = (32, 48)
    moved = rallies(ask(agent, base_state("england", YARD, POWER, BARRACKS, WAR_FACTORY, tick=1010)))

    assert first == [{"cmd": "rally", "building": 103, "to": [43, 48]}] and same == []
    assert moved == [{"cmd": "rally", "building": 103, "to": STAGING}, {"cmd": "rally", "building": 104, "to": STAGING}]


def test_with_the_forward_hold_on_the_producers_are_rallied_to_the_forward_point_once_main_holds_there():
    tuning = dataclasses.replace(TWO_REFINERIES_ONLY, combat=combat_off(forward_hold=True))
    agent = new_agent(UpkeepManager(), CombatManager(), plan_selector=OpeningThen(HOLD), tuning=tuning)
    # Two medium tanks and a rocket soldier of main, 2,000 in a fight, at the staging point.
    main = [
        situations.unit(300, "2tnk", 32, 48),
        situations.unit(301, "2tnk", 33, 48),
        situations.unit(302, "e3", 32, 49),
    ]

    rallied = [
        rallies(ask(agent, base_state("england", YARD, POWER, BARRACKS, WAR_FACTORY, tick=tick, units=main)))
        for tick in (3000, 3005, 3010)
    ]

    assert agent.current_plan == HOLD and agent.ctx.gather_point == (43, 48)
    assert rallied == [
        [{"cmd": "rally", "building": 103, "to": STAGING}, {"cmd": "rally", "building": 104, "to": STAGING}],
        [{"cmd": "rally", "building": 103, "to": [43, 48]}, {"cmd": "rally", "building": 104, "to": [43, 48]}],
        [],
    ]


def test_producers_rally_to_cover_point():
    tuning = dataclasses.replace(TWO_REFINERIES_ONLY, combat=combat_off(expansion_cover_rally=True))
    agent = new_agent(UpkeepManager(), CombatManager(), plan_selector=OpeningThen(HOLD), tuning=tuning)
    # A jeep and a rocket soldier of main, 800 in a fight, at the staging point; an expansion yard whose middle is
    # (35, 52), 13 cells out on the line to the enemy's start, and the cover point 6 cells beyond it.
    main = [situations.unit(320, "jeep", 32, 48), situations.unit(321, "e3", 33, 48)]
    expansion_yard, cover = ("fact", 35, 51), [41, 48]

    rallied = [
        rallies(
            ask(agent, base_state("england", YARD, POWER, BARRACKS, WAR_FACTORY, expansion_yard, tick=tick, units=main))
        )
        for tick in (3000, 3005, 3010)
    ]

    assert agent.current_plan == HOLD and agent.ctx.gather_point == tuple(cover)
    assert rallied == [
        [{"cmd": "rally", "building": 103, "to": STAGING}, {"cmd": "rally", "building": 104, "to": STAGING}],
        [{"cmd": "rally", "building": 103, "to": cover}, {"cmd": "rally", "building": 104, "to": cover}],
        [],
    ]


def test_the_fake_game_shows_the_rally_point_on_the_barracks_and_the_war_factory():
    game = FakeRedAlert("england", "russia")

    game.run(own_agent(), 5 * 150)

    points = {building["type"]: building["rally"] for building in game.own_buildings() if building["rally"]}
    assert points == {"tent": STAGING, "weap": STAGING}


# -- the low-power emergency -----------------------------------------------------------------------

# A base with a service depot (id 106, drains 30), a radar (107, 40), a pillbox (108, 20) and a gun (109, 40).
EQUIPPED = (
    YARD,
    POWER,
    BARRACKS,
    REFINERY,
    WAR_FACTORY,
    ("fix", 26, 54),
    ("dome", 30, 48),
    ("pbox", 27, 48),
    ("gun", 27, 46),
)
DEPOT, RADAR, PILLBOX, GUN = 106, 107, 108, 109
LOW_POWER = [situations.event(40, "low_power", power_provided=200, power_drained=230)]


def short_of_power(
    drained: int, *, provided: int = 200, switched_off: tuple[int, ...] = (), tick: int = 7000, **more
) -> dict:
    """A state of the equipped base that provides and drains so much power, with these buildings switched off.

    The Building queue holds a refinery, so that no power plant is ordered or on its way unless a test puts one there.
    """
    more.setdefault("production", [situations.job("Building", "proc")])
    state = base_state("england", *EQUIPPED, economy=powered(provided, drained, harvesters=0), tick=tick, **more)
    for building in state["observation"]["buildings"]:
        if building["id"] in switched_off:
            building.update(powered_down=True, powered=False)
    return state


def toggles(orders: list[dict]) -> list[tuple[int, bool]]:
    """The (building, on) of the power_toggle orders of a batch, in order."""
    return [(order["building"], order["on"]) for order in with_command(orders, "power_toggle")]


def test_a_low_power_event_with_no_power_plant_in_production_powers_down_the_depot_before_any_defense():
    orders = ask(upkeep_agent(), short_of_power(230, events=LOW_POWER))

    assert with_command(orders, "power_toggle") == [{"cmd": "power_toggle", "building": DEPOT, "on": False}]


@pytest.mark.parametrize("headroom, restored", [(40, [(DEPOT, True)]), (39, []), (90, [(DEPOT, True)])])
def test_the_depot_is_powered_on_again_when_the_headroom_is_at_least_40(headroom, restored):
    agent = upkeep_agent()
    ask(agent, short_of_power(230, events=LOW_POWER, tick=7000))

    orders = ask(agent, short_of_power(300 - headroom, provided=300, switched_off=(DEPOT,), tick=7400))

    assert toggles(orders) == restored


@pytest.mark.parametrize(
    "drained, shed",
    [
        (201, [DEPOT]),
        (230, [DEPOT]),
        (231, [DEPOT, RADAR]),
        (270, [DEPOT, RADAR]),
        (271, [DEPOT, RADAR, PILLBOX]),
        (291, [DEPOT, RADAR, PILLBOX, GUN]),
        (400, [DEPOT, RADAR, PILLBOX, GUN]),
    ],
)
def test_consumers_are_switched_off_depot_first_then_radar_then_defenses_until_the_shortage_is_covered(drained, shed):
    assert toggles(ask(upkeep_agent(), short_of_power(drained))) == [(building, False) for building in shed]


def test_the_power_figures_alone_start_the_emergency_and_an_event_alone_does_not():
    without_event = ask(upkeep_agent(), short_of_power(230))
    event_but_enough_power = ask(upkeep_agent(), short_of_power(200, events=LOW_POWER))

    assert (toggles(without_event), toggles(event_but_enough_power)) == ([(DEPOT, False)], [])


@pytest.mark.parametrize(
    "plant, waits",
    [
        (situations.job("Building", "powr", remaining_s=10.0), True),
        (situations.job("Building", "powr", remaining_s=10.5), False),
        (situations.job("Building", "apwr", remaining_s=3.0), True),
        (situations.job("Building", "powr", ready=True), True),
        ({**situations.job("Building", "powr", remaining_s=3.0), "paused": True}, False),
        (situations.job("Building", "dome", remaining_s=3.0), False),
    ],
    ids=[
        "done in 10 s",
        "done in 10.5 s",
        "an advanced one",
        "waiting to be placed",
        "stalled for cash",
        "not a power plant",
    ],
)
def test_nothing_is_switched_off_while_a_power_plant_completes_within_10_seconds(plant, waits):
    orders = ask(upkeep_agent(), short_of_power(230, production=[plant]))

    assert toggles(orders) == ([] if waits else [(DEPOT, False)])


def test_a_defense_with_a_visible_enemy_within_10_cells_is_not_switched_off():
    ten_cells_from_the_pillbox = [{"id": 900, "type": "e1", "x": 37, "y": 48, "hp": 100}]
    farther = [{"id": 900, "type": "e1", "x": 38, "y": 54, "hp": 100}]

    with_an_enemy_near = ask(upkeep_agent(), short_of_power(300, visible_enemies=ten_cells_from_the_pillbox))
    with_none_near = ask(upkeep_agent(), short_of_power(300, visible_enemies=farther))

    # The gun stands two rows above the pillbox, a little over 10 cells from that enemy.
    assert toggles(with_an_enemy_near) == [(DEPOT, False), (RADAR, False), (GUN, False)]
    assert toggles(with_none_near) == [(DEPOT, False), (RADAR, False), (PILLBOX, False), (GUN, False)]


def test_what_was_switched_off_a_moment_ago_is_neither_switched_off_again_nor_replaced_by_the_next_consumer():
    agent = upkeep_agent()
    first = ask(agent, short_of_power(230, tick=7000))

    still_showing_the_shortage = ask(agent, short_of_power(230, tick=7005))
    still_short_after_it_took_effect = ask(agent, short_of_power(215, switched_off=(DEPOT,), tick=7010))

    assert toggles(first) == [(DEPOT, False)] and toggles(still_showing_the_shortage) == []
    assert toggles(still_short_after_it_took_effect) == [(RADAR, False)]


def test_a_building_that_is_switched_off_already_is_passed_over():
    orders = ask(upkeep_agent(), short_of_power(210, switched_off=(DEPOT,)))

    assert toggles(orders) == [(RADAR, False)]


def test_consumers_come_back_the_most_needed_first_and_only_as_far_as_the_power_reaches():
    everything_off = (DEPOT, RADAR, PILLBOX, GUN)

    plenty = ask(upkeep_agent(), short_of_power(100, provided=300, switched_off=everything_off))
    some = ask(upkeep_agent(), short_of_power(140, provided=200, switched_off=everything_off))
    little = ask(upkeep_agent(), short_of_power(160, provided=200, switched_off=(DEPOT, RADAR)))

    assert toggles(plenty) == [(GUN, True), (PILLBOX, True), (RADAR, True), (DEPOT, True)]
    assert toggles(some) == [(GUN, True)]
    assert toggles(little) == [(RADAR, True)]


def test_what_was_switched_on_a_moment_ago_is_counted_against_the_power_to_spare():
    agent = upkeep_agent()
    first = ask(agent, short_of_power(130, provided=200, switched_off=(DEPOT, RADAR), tick=7000))

    second = ask(agent, short_of_power(130, provided=200, switched_off=(DEPOT, RADAR), tick=7005))

    assert toggles(first) == [(RADAR, True)] and toggles(second) == []


def test_nothing_is_switched_while_the_headroom_is_between_0_and_39():
    for drained in (200, 180, 161):
        assert toggles(ask(upkeep_agent(), short_of_power(drained, switched_off=(DEPOT,)))) == []


def test_every_power_toggle_carries_an_explicit_on():
    off = with_command(ask(upkeep_agent(), short_of_power(400)), "power_toggle")
    on = with_command(ask(upkeep_agent(), short_of_power(0, switched_off=(DEPOT, GUN))), "power_toggle")

    assert len(off) == 4 and len(on) == 2
    assert all(order["on"] is False for order in off) and all(order["on"] is True for order in on)


def switched(batches: list[dict], *, on: bool) -> set[int]:
    """The buildings the batches of a run switched one way."""
    return {building for batch in batches for building, way in toggles(batch["orders"]) if way is on}


def ids_of(game: FakeRedAlert, *types: str) -> set[int]:
    return {building["id"] for building in game.own_buildings() if building["type"] in types}


def test_in_a_fake_match_a_lost_power_plant_is_answered_by_switching_defenses_off_and_on_again():
    game = FakeRedAlert("england", "russia")
    agent = own_agent()
    game.run(agent, 5 * 180)
    game.kill_own(min(ids_of(game, "powr")))

    played = game.run(agent, 5 * 60, keep_states=True)

    economy = played.states[-1]["observation"]["economy"]
    assert switched(played.batches, on=False) == ids_of(game, "pbox", "gun") == switched(played.batches, on=True)
    assert [building["powered_down"] for building in game.own_buildings()].count(True) == 0
    assert economy["power_provided"] >= economy["power_drained"]


# -- money is the cash and the ore together --------------------------------------------------------


def test_with_no_cash_and_2000_ore_an_empty_vehicle_queue_is_filled():
    orders = ask(upkeep_agent(), holding("england", cash=0, ore=2000))

    assert trained(orders, "Vehicle") == [("1tnk", 1)]


@pytest.mark.parametrize(
    "cash, ore, queued",
    [(0, 999, []), (0, 1000, [("1tnk", 1)]), (500, 499, []), (500, 500, [("1tnk", 1)])],
    ids=["ore one short", "ore alone", "together one short", "together"],
)
def test_the_reserve_of_300_is_counted_against_the_cash_and_the_ore_together(cash, ore, queued):
    busy_infantry = [situations.job("Infantry", "e1")]

    orders = ask(upkeep_agent(), holding("england", cash=cash, ore=ore, production=busy_infantry))

    assert trains(orders) == queued


@pytest.mark.parametrize("cash, ore, plant", [(0, 499, "powr"), (0, 500, "apwr"), (250, 250, "apwr")])
def test_the_advanced_power_plant_is_ordered_when_the_cash_and_the_ore_together_are_500(cash, ore, plant):
    with_radar = (*BASE, ("dome", 26, 48))
    economy = situations.economy(cash, provided=100, drained=100, ore=ore)

    assert builds(ask(upkeep_agent(), base_state("england", *with_radar, economy=economy))) == [plant]


@pytest.mark.parametrize("cash, ore, queued", [(0, 1500, ["fix"]), (700, 800, ["fix"]), (700, 799, [])])
def test_the_service_depot_is_queued_when_the_cash_and_the_ore_together_are_1500(cash, ore, queued):
    state = base_state("england", *TWO_REFINERIES, units=harvesters(4), cash=cash, ore=ore)

    assert builds(ask(upkeep_agent(), state)) == queued


@pytest.mark.parametrize("cash, ore, switched_on", [(0, 300, True), (100, 200, True), (100, 199, False)])
def test_repair_is_switched_on_when_the_cash_and_the_ore_together_are_300(cash, ore, switched_on):
    orders = ask(upkeep_agent(), damaged(60, cash=cash, ore=ore))

    assert repairs(orders) == ([{"cmd": "repair", "building": REFINERY_ID, "on": True}] if switched_on else [])


@pytest.mark.parametrize("cash, ore, switched_off", [(0, 99, True), (0, 100, False), (50, 50, False)])
def test_repair_is_switched_off_only_when_the_cash_and_the_ore_together_are_below_100(cash, ore, switched_off):
    orders = ask(upkeep_agent(), damaged(60, repairing=True, cash=cash, ore=ore))

    assert repairs(orders) == ([{"cmd": "repair", "building": REFINERY_ID, "on": False}] if switched_off else [])


# -- the unit queues over a whole match ------------------------------------------------------------

MATCH_SECONDS = 600
STATES_PER_SECOND = 5
AMPLE_CASH = 1_000_000
# The producers' thresholds with the bank rules held back, and the tuning with the shipped switch set off and them: ample
# cash would have the bank rules fill the Building queue with war factories, and a rule that ample cash tests is to find
# that queue free.
BANK_RULES_HELD = producers_off(bank_factory_money=2 * AMPLE_CASH, idle_bank_money=2 * AMPLE_CASH)
NO_BANK_RULES = tuning_off(producers=BANK_RULES_HELD)
ENEMY_OF = {"england": "russia", "russia": "england"}
UNIT_QUEUES = ("Infantry", "Vehicle")
IDLE_LIMIT_SECONDS = 15


@dataclasses.dataclass
class RichMatch:
    """A 600-second fake match the agent played with ample cash, and how long its unit queues stood idle in it."""

    # The tick of the first state the agent answered under a plan after the opening, and of the first that shows a
    # war factory.
    hold_from: int
    war_factory_from: int
    least_cash: int
    # `idle_production_seconds` of each unit queue from `hold_from` on: as the match record gives it, where four
    # states in five are thinned, and as every state of the match written in full would give it.
    by_record: dict[str, float]
    by_every_state: dict[str, float]
    # The same by every state over the whole match, the opening included.
    whole_match: dict[str, float]


def make_rich(game: FakeRedAlert) -> None:
    game.cash = AMPLE_CASH


def in_full(raw: dict) -> dict:
    """The `state` event of a state, as a match record holds a state that is written in full."""
    return {"kind": "state", "data": {"state_version": raw["state_version"], "phase": raw["phase"], "state": raw}}


def idle_seconds(events: list[dict], since_tick: int = 0) -> dict[str, float]:
    """`idle_production_seconds` of the unit queues by the report's own metric, over the states from a tick on."""
    kept = [event for event in events if event["kind"] != "state" or event["data"]["state_version"] >= since_tick]
    idle = metrics_redalert.compute(Match(Path(), [Attempt(1, {}, kept, 0)]))["idle_production_seconds"]
    return {queue: idle[queue] for queue in UNIT_QUEUES}


def recorded_events(run_folder: Path) -> list[dict]:
    """The events of the match record under a folder, read the way the report tool reads a record."""
    (folder,) = run_folder.glob(f"runs/*/{situations.SESSION_ID}__seat")
    return metrics.load_match(folder).events


def first_tick_with(states: list[dict], building_type: str) -> int:
    """The tick of the first state that shows an own building of a type."""
    return next(
        raw["state_version"]
        for raw in states
        if any(building["type"] == building_type for building in raw["observation"]["buildings"])
    )


@pytest.fixture(params=["england", "russia"])
def rich_match(request, match_record, run_folder) -> RichMatch:
    """600 seconds of a match with ample cash, played by the agent with its own managers and recorded as a match is."""
    game = FakeRedAlert(request.param, ENEMY_OF[request.param], scenario=(fake_game.at(0, make_rich),))
    recording = situations.Recording(own_agent())
    states = game.run(recording, MATCH_SECONDS * STATES_PER_SECOND + 1, keep_states=True).states
    match_record()
    hold_from = min(tick for tick, plan in recording.plan_at.items() if plan != OPENING)
    every_state = [in_full(raw) for raw in states]
    return RichMatch(
        hold_from=hold_from,
        war_factory_from=first_tick_with(states, "weap"),
        least_cash=min(raw["observation"]["economy"]["cash"] for raw in states),
        by_record=idle_seconds(recorded_events(run_folder), hold_from),
        by_every_state=idle_seconds(every_state, hold_from),
        whole_match=idle_seconds(every_state),
    )


def test_over_600_seconds_with_ample_cash_each_unit_queue_idles_for_under_15_seconds_once_the_opening_is_over(
    rich_match,
):
    assert rich_match.hold_from == rich_match.war_factory_from < 200 * 25
    assert max(rich_match.by_record.values()) < IDLE_LIMIT_SECONDS
    assert max(rich_match.by_every_state.values()) < IDLE_LIMIT_SECONDS


def test_the_idle_seconds_of_that_match_are_counted_for_real(rich_match):
    # Cash never falls below what the metric asks for, so every state with an empty queue counts; the queues
    # do stand empty for a moment between two orders; and before the war factory the Vehicle queue is idle.
    assert rich_match.least_cash >= metrics_redalert.IDLE_CASH_THRESHOLD
    assert min(rich_match.by_every_state.values()) > 0
    assert rich_match.whole_match["Vehicle"] > rich_match.hold_from / 25 - 1


# -- rebuilding the core of the base ---------------------------------------------------------------


def finished(
    faction: str = "england",
    *lost: str,
    tick: int = 5005,
    withheld: tuple = ("fix",),
    units: list | None = None,
    **more,
) -> dict:
    """A faction's base after its opening, less the first building of each type named, with four harvesters.

    No service depot is on offer unless `withheld` says otherwise, so that
    the Building queue is not taken by one.
    """
    left = list(HOLDING[faction])
    for building_type in lost:
        left.remove(next(spec for spec in left if spec[0] == building_type))
    units = harvesters(4) if units is None else units
    return base_state(faction, *left, units=units, tick=tick, withheld=withheld, **more)


# A base that keeps to the opening's two refineries, so that the Building queue is left to the rule under test.
TWO_REFINERIES_ONLY = tuning_off(upkeep=dataclasses.replace(upkeep_off(), refineries_max=2, refineries_max_late=2))


def past_the_opening(faction: str = "england"):
    """An upkeep agent that has seen the whole base of a faction stand, the war factory included.

    It saw it with no money, so it ordered nothing that would still hold a queue. It builds no third refinery.
    """
    agent = upkeep_agent(tuning=TWO_REFINERIES_ONLY)
    ask(agent, finished(faction, tick=5000, cash=0))
    return agent


def test_before_a_war_factory_has_stood_a_missing_building_is_left_to_the_opening():
    without_barracks = base_state("england", YARD, POWER, REFINERY, ("proc", 18, 39), ("powr", 26, 50))

    assert builds(ask(upkeep_agent(), without_barracks)) == []


@pytest.mark.parametrize(
    "lost, queued",
    [
        ((), []),
        (("tent",), ["tent"]),
        (("proc",), ["proc"]),
        (("weap",), ["weap"]),
        (("proc", "proc"), ["proc"]),
        (("tent", "proc", "weap"), ["tent"]),
        (("proc", "weap"), ["proc"]),
        (("powr", "proc"), ["powr"]),
        (("powr", "tent", "proc"), ["powr"]),
    ],
    ids=["none", "barracks", "refinery", "war factory", "both refineries", "barracks first", "refinery before factory"]
    + ["power plant before refinery", "power plant before barracks"],
)
def test_after_the_opening_the_first_missing_core_building_is_queued_again(lost, queued):
    assert builds(ask(past_the_opening(), finished("england", *lost))) == queued


def test_the_soviet_base_gets_its_own_barracks_back():
    assert builds(ask(past_the_opening("russia"), finished("russia", "barr"))) == ["barr"]


def test_a_core_building_that_is_not_on_offer_is_passed_over_for_the_next():
    state = finished("england", "tent", "weap", withheld=("fix", "tent"))

    assert builds(ask(past_the_opening(), state)) == ["weap"]


@pytest.mark.parametrize(
    "cash, ore, queued",
    [(799, 0, []), (800, 0, ["tent"]), (0, 800, ["tent"]), (400, 399, [])],
    ids=["one short", "cost and reserve", "from ore", "together one short"],
)
def test_a_replacement_waits_until_the_money_covers_its_cost_and_the_reserve_of_300(cash, ore, queued):
    assert builds(ask(past_the_opening(), finished("england", "tent", cash=cash, ore=ore))) == queued


def test_a_replacement_waits_for_the_building_queue_and_is_not_ordered_twice():
    agent = past_the_opening()
    busy = finished("england", "tent", tick=5005, production=[situations.job("Building", "powr")])

    waiting = builds(ask(agent, busy))
    first = builds(ask(agent, finished("england", "tent", tick=5010)))
    second = builds(ask(agent, finished("england", "tent", tick=5015)))

    assert (waiting, first, second) == ([], ["tent"], [])


# From the sixth minute on the target is 60: a war factory (30) needs 90 left over.
@pytest.mark.parametrize("drained, queued", [(110, ["weap"]), (115, ["powr"])], ids=["power for it", "5 short"])
def test_power_a_replacement_that_cannot_be_powered_gives_way_to_a_power_plant(drained, queued):
    state = finished("england", "weap", tick=MID_TARGET_TICK, economy=powered(200, drained, cash=5000, harvesters=4))

    assert builds(ask(past_the_opening(), state)) == queued


@pytest.mark.parametrize("withheld, queued", [(("fix",), ["powr"]), (("fix", "powr"), [])])
def test_while_the_power_left_over_is_low_the_power_plant_comes_first_and_nothing_else_is_queued(withheld, queued):
    state = finished("england", "tent", withheld=withheld, economy=powered(200, 190, cash=5000, harvesters=4))

    assert builds(ask(past_the_opening(), state)) == queued


def test_an_advanced_power_plant_counts_as_one_of_the_two_power_plants():
    advanced = (YARD, POWER, BARRACKS, REFINERY, ("proc", 18, 39), ("apwr", 26, 50), WAR_FACTORY)
    agent = upkeep_agent(tuning=TWO_REFINERIES_ONLY)

    orders = [
        ask(agent, base_state("england", *advanced, units=harvesters(4), withheld=("fix",), tick=tick))
        for tick in (5000, 5005)
    ]

    assert [builds(batch) for batch in orders] == [[], []]


WITH_DEPOT = (*TWO_REFINERIES, ("fix", 26, 54), ("powr", 30, 50))
DEPOT_LOST = (*TWO_REFINERIES, ("powr", 30, 50))


@pytest.mark.parametrize("cash, queued", [(1500, ["fix"]), (1499, [])])
def test_a_service_depot_that_stood_and_was_lost_is_queued_again_when_the_money_allows(cash, queued):
    agent = upkeep_agent()
    ask(agent, base_state("england", *WITH_DEPOT, units=harvesters(4), tick=6805))

    orders = ask(agent, base_state("england", *DEPOT_LOST, units=harvesters(4), tick=7000, cash=cash))

    assert builds(orders) == queued


def test_a_base_that_never_had_a_service_depot_is_not_given_one_by_the_rebuilding():
    agent = past_the_opening()

    # Three harvesters are one too few for the rule that builds the first service depot.
    orders = ask(agent, base_state("england", *HOLDING["england"], units=harvesters(3), tick=5005))

    assert builds(orders) == []


def test_a_lost_core_building_comes_before_the_first_service_depot():
    assert builds(ask(past_the_opening(), finished("england", "tent", withheld=()))) == ["tent"]


def build_orders(played) -> list[str]:
    """The items of every build order of a run's batches, in the order they were sent."""
    return [order["item"] for batch in played.batches for order in with_command(batch["orders"], "build")]


@pytest.mark.parametrize("faction, barracks", [("england", "tent"), ("russia", "barr")])
def test_in_a_fake_match_a_barracks_lost_after_the_opening_is_queued_again_within_three_states(faction, barracks):
    game = FakeRedAlert(faction, ENEMY_OF[faction], scenario=(fake_game.at(0, make_rich),))
    agent = own_agent(tuning=NO_BANK_RULES)
    game.run(agent, STATES_PER_SECOND * 150)
    had_a_war_factory = bool(ids_of(game, "weap"))
    game.kill_own(min(ids_of(game, barracks)))

    within_three_states = game.run(agent, 3)
    game.run(agent, STATES_PER_SECOND * 20)

    assert had_a_war_factory and build_orders(within_three_states) == [barracks]
    assert len(ids_of(game, barracks)) == 1 and agent.ledger.duplicate_production_orders == 0


# -- replacing the opening's defenses --------------------------------------------------------------

OPENING_GUN = ("gun", 27, 50)
# 60 seconds in ticks: the least time between two replacement defenses.
REPLACE_TICKS = 1500


def defenses(orders: list[dict]) -> list[str]:
    """The items a batch orders for the Defense queue."""
    return built(orders, "Defense")


def armed(*lost: str, tick: int = 5005, **more) -> dict:
    """An Allied base past its opening with the opening's pbox and gun and a third power plant, less the first
    building of each type named.

    The third power plant keeps the power target of 60 over, gun included.
    """
    left = [*HOLDING["england"], OPENING_GUN, ("powr", 30, 50)]
    for building_type in lost:
        left.remove(next(spec for spec in left if spec[0] == building_type))
    return base_state("england", *left, units=harvesters(4), tick=tick, withheld=("fix",), **more)


def armed_agent():
    """An upkeep agent that has seen the Allied base stand with its war factory, its pbox and its gun.

    The producers' bank rules are held back: states minutes apart would read as money banked and a Building queue
    empty all the while.
    """
    agent = upkeep_agent(tuning=dataclasses.replace(TWO_REFINERIES_ONLY, producers=BANK_RULES_HELD))
    ask(agent, armed(tick=5000))
    return agent


def test_before_a_war_factory_has_stood_a_lost_defense_is_left_to_the_opening():
    without_defense = base_state("england", YARD, POWER, BARRACKS, REFINERY, ("proc", 18, 39), ("powr", 26, 50))

    assert defenses(ask(upkeep_agent(), without_defense)) == []


@pytest.mark.parametrize("faction, defense", [("england", "pbox"), ("russia", "ftur")])
def test_after_the_opening_a_lost_defense_against_infantry_is_replaced(faction, defense):
    orders = ask(past_the_opening(faction), finished(faction, defense))

    assert (defenses(orders), builds(orders)) == ([defense], [])


def test_a_gun_that_stood_and_was_lost_is_replaced():
    assert defenses(ask(armed_agent(), armed("gun"))) == ["gun"]


def test_a_base_that_never_had_a_gun_is_not_given_one():
    assert defenses(ask(past_the_opening(), finished("england"))) == []


def test_with_both_lost_the_defense_against_infantry_comes_first_and_the_gun_a_minute_later():
    agent = armed_agent()

    first = defenses(ask(agent, armed("pbox", "gun", tick=5005)))
    showing = [situations.job("Defense", "pbox", remaining_s=10.0)]
    while_it_is_built = defenses(ask(agent, armed("pbox", "gun", tick=5010, production=showing)))
    a_minute_less_a_state = defenses(ask(agent, armed("gun", tick=5005 + REPLACE_TICKS - 5)))
    a_minute_later = defenses(ask(agent, armed("gun", tick=5005 + REPLACE_TICKS)))

    assert (first, while_it_is_built, a_minute_less_a_state, a_minute_later) == (["pbox"], [], [], ["gun"])


def test_a_replacement_that_was_lost_again_is_replaced_once_a_minute_has_passed_since_the_last():
    agent = past_the_opening()
    ask(agent, finished("england", "pbox", tick=5005))
    ask(agent, finished("england", tick=5800))

    lost_again = defenses(ask(agent, finished("england", "pbox", tick=6000)))
    a_minute_after_the_first = defenses(ask(agent, finished("england", "pbox", tick=5005 + REPLACE_TICKS)))

    assert (lost_again, a_minute_after_the_first) == ([], ["pbox"])


def test_no_replacement_is_started_while_a_threat_is_active():
    agent = past_the_opening()
    agent.ctx.threat_active = True

    assert defenses(ask(agent, finished("england", "pbox"))) == []


def test_a_replacement_waits_for_the_defense_queue():
    busy = finished("england", "pbox", production=[situations.job("Defense", "gun")])

    assert defenses(ask(past_the_opening(), busy)) == []


DEFENSE_ON_THREAT = dataclasses.replace(TWO_REFINERIES_ONLY, opening=opening_off(defense_on_threat=True))


@pytest.mark.parametrize("faction, defense", [("england", "pbox"), ("russia", "ftur")])
def test_on_threat_a_base_that_never_had_a_defense_against_infantry_is_not_given_one(faction, defense):
    agent = upkeep_agent(tuning=DEFENSE_ON_THREAT)
    ask(agent, finished(faction, defense, tick=5000, cash=0))

    orders = [ask(agent, finished(faction, defense, tick=tick)) for tick in (5005, 5005 + REPLACE_TICKS)]

    assert [defenses(batch) for batch in orders] == [[], []]


@pytest.mark.parametrize("faction, defense", [("england", "pbox"), ("russia", "ftur")])
def test_on_threat_a_defense_against_infantry_that_stood_and_was_lost_is_replaced(faction, defense):
    agent = upkeep_agent(tuning=DEFENSE_ON_THREAT)
    ask(agent, finished(faction, tick=5000, cash=0))

    orders = ask(agent, finished(faction, defense, tick=5005))

    assert (defenses(orders), builds(orders)) == ([defense], [])


# Upkeep's defenses bought only against a real threat.
ON_THREAT_ONLY = dataclasses.replace(
    TWO_REFINERIES_ONLY, upkeep=dataclasses.replace(TWO_REFINERIES_ONLY.upkeep, defenses_on_threat_only=True)
)


@pytest.mark.parametrize("faction, defense", [("england", "pbox"), ("russia", "ftur")])
def test_on_threat_only_a_lost_defense_against_infantry_is_not_replaced_however_long_the_base_stays_quiet(
    faction, defense
):
    agent = upkeep_agent(tuning=ON_THREAT_ONLY)
    ask(agent, finished(faction, tick=5000, cash=0))

    ticks = (5005, 5005 + REPLACE_TICKS, 5005 + 4 * REPLACE_TICKS)
    orders = [ask(agent, finished(faction, defense, tick=tick)) for tick in ticks]

    assert [defense in defenses(batch) for batch in orders] == [False] * 3
    assert not agent.ctx.threat_active


def test_on_threat_only_a_gun_that_stood_and_was_lost_is_not_replaced():
    agent = upkeep_agent(tuning=ON_THREAT_ONLY)
    ask(agent, armed(tick=5000))

    orders = [ask(agent, armed("pbox", "gun", tick=tick)) for tick in (5005, 5005 + REPLACE_TICKS)]

    assert [defenses(batch) for batch in orders] == [[], []]


@pytest.mark.parametrize(
    "cash, ore, queued",
    [(899, 0, []), (900, 0, ["pbox"]), (0, 900, ["pbox"]), (450, 449, [])],
    ids=["one short", "cost and reserve", "from ore", "together one short"],
)
def test_a_replacement_defense_waits_until_the_money_covers_its_cost_and_the_reserve(cash, ore, queued):
    assert defenses(ask(past_the_opening(), finished("england", "pbox", cash=cash, ore=ore))) == queued


@pytest.mark.parametrize(
    "drained, production, queued",
    [(100, [], (["gun"], [])), (101, [], ([], ["powr"])), (101, [situations.job("Building", "weap")], ([], []))],
    ids=["power for it", "one short", "one short and the building queue busy"],
)
def test_power_a_gun_that_would_take_the_headroom_below_the_target_waits_for_a_power_plant(drained, production, queued):
    economy = powered(200, drained, cash=5000, harvesters=4)
    state = armed("gun", tick=MID_TARGET_TICK, economy=economy, production=production)

    orders = ask(armed_agent(), state)

    assert (defenses(orders), builds(orders)) == queued


def defense_orders_from(recording, item: str, tick: int) -> list[int]:
    """The ticks of the states the agent answered with an order for a defense, from a tick on."""
    return [sent_at for sent_at, orders in recording.orders_at.items() if sent_at >= tick and item in defenses(orders)]


def test_in_a_fake_match_a_lost_defense_is_replaced_once_and_not_again_within_60_seconds():
    game = FakeRedAlert("england", "russia", scenario=(fake_game.at(0, make_rich),))
    recording = situations.Recording(own_agent(tuning=NO_BANK_RULES))
    # By 240 s the opening's build stands, its gun included.
    game.run(recording, STATES_PER_SECOND * 240)
    lost_at = game.tick
    game.kill_own(min(ids_of(game, "pbox")))
    game.run(recording, 3)
    within_three_states = defense_orders_from(recording, "pbox", lost_at)

    # The replacement is built (14.4 s) and placed; it is lost too, and the next is due a minute after the first.
    game.run(recording, STATES_PER_SECOND * 20)
    rebuilt = ids_of(game, "pbox")
    game.kill_own(min(rebuilt))
    game.run(recording, STATES_PER_SECOND * 45)

    (first,) = within_three_states
    assert len(rebuilt) == 1
    assert defense_orders_from(recording, "pbox", lost_at) == [first, first + REPLACE_TICKS]
    assert recording.agent.ledger.duplicate_production_orders == 0


# -- saving for a lost core building ---------------------------------------------------------------


def unit_trains(orders: list[dict]) -> list[tuple[str, int]]:
    """The (item, count) of the train orders of a batch for the Infantry and the Vehicle queue."""
    return [*trained(orders, "Infantry"), *trained(orders, "Vehicle")]


@pytest.mark.parametrize("money, queued", [(600, []), (800, ["tent"])])
def test_with_the_barracks_missing_no_unit_is_queued_at_600_and_the_barracks_is_queued_at_800(money, queued):
    orders = ask(past_the_opening(), finished("england", "tent", cash=0, ore=money))

    assert (unit_trains(orders), builds(orders)) == ([], queued)


@pytest.mark.parametrize(
    "lost, money",
    [("proc", 1699), ("weap", 2299)],
    ids=["refinery one short", "war factory one short"],
)
def test_while_the_money_falls_short_of_a_lost_core_building_and_the_reserve_no_unit_is_queued(lost, money):
    orders = ask(past_the_opening(), finished("england", lost, cash=money))

    assert (unit_trains(orders), builds(orders)) == ([], [])


def test_with_nothing_lost_the_same_money_feeds_both_unit_queues():
    orders = ask(past_the_opening(), finished("england", cash=1699))

    assert unit_trains(orders) == [("e1", 3), ("1tnk", 1)]


def test_a_replacement_in_its_queue_is_still_saved_for_until_it_stands():
    in_its_queue = finished("england", "proc", cash=1200, production=[situations.job("Building", "proc")])

    assert unit_trains(ask(past_the_opening(), in_its_queue)) == []


def test_once_the_money_covers_the_replacement_and_the_reserve_units_are_queued_again():
    orders = ask(
        past_the_opening(), finished("england", "proc", cash=3000, production=[situations.job("Building", "proc")])
    )

    assert unit_trains(orders) == [("e1", 3), ("1tnk", 1)]


def test_a_lost_power_plant_is_not_saved_for():
    # 1,300 is the light tank, the reserve and the power plant ordered in the same cycle.
    orders = ask(past_the_opening(), finished("england", "powr", cash=1300))

    assert ("1tnk", 1) in unit_trains(orders) and builds(orders) == ["powr"]


def test_a_service_depot_that_stood_and_was_lost_is_saved_for_and_one_that_never_stood_is_not():
    had_one = upkeep_agent()
    ask(had_one, base_state("england", *WITH_DEPOT, units=harvesters(4), tick=6805, cash=0))
    never = past_the_opening()

    lost = unit_trains(ask(had_one, base_state("england", *DEPOT_LOST, units=harvesters(4), tick=7000, cash=1400)))
    none = unit_trains(ask(never, finished("england", cash=1400, withheld=())))

    assert (lost, none) == ([], [("e1", 3), ("1tnk", 1)])


def test_a_core_building_that_cannot_be_made_is_not_saved_for():
    # Without a construction yard nothing is on offer for the Building queue.
    state = finished("england", "fact", "tent", cash=1000, withheld=("fix", "tent", "proc", "weap", "powr"))

    assert unit_trains(ask(past_the_opening(), state)) == [("1tnk", 1)]


def test_while_saving_the_harvesters_are_still_made():
    state = finished("england", "proc", cash=1200, units=harvesters(1))

    assert trains(ask(past_the_opening(), state)) == [("harv", 1)]


def orders_between(recording, start: int, end: int) -> dict[int, list[dict]]:
    """The orders the agent answered each state with, by tick, for the states from one tick up to another."""
    return {tick: orders for tick, orders in recording.orders_at.items() if start <= tick < end}


def spendable(raw: dict) -> int:
    economy = raw["observation"]["economy"]
    return economy["cash"] + economy["ore"]


def unit_trains_while_short(answered: dict[int, list[dict]], states: list[dict], needed: int) -> list[list]:
    """The unit trains of each answer given to a state whose money was below what is needed."""
    money_at = {raw["state_version"]: spendable(raw) for raw in states}
    return [unit_trains(orders) for tick, orders in answered.items() if money_at[tick] < needed]


def builds_in(answered: dict[int, list[dict]]) -> list[str]:
    return [order["item"] for orders in answered.values() for order in with_command(orders, "build")]


def test_in_a_fake_match_no_unit_is_queued_while_the_money_falls_short_of_a_lost_refinery_and_the_reserve():
    game = FakeRedAlert("england", "russia")
    recording = situations.Recording(own_agent(tuning=TWO_REFINERIES_ONLY))
    game.run(recording, STATES_PER_SECOND * 300)
    lost_at = game.tick
    game.kill_own(max(ids_of(game, "proc")))

    played = game.run(recording, STATES_PER_SECOND * 90, keep_states=True)

    stood_at = first_tick_with_count(played.states, "proc", 2)
    while_lost = orders_between(recording, lost_at, stood_at)
    trains_while_short = unit_trains_while_short(while_lost, played.states, 1400 + 300)
    assert builds_in(while_lost) == ["proc"]
    assert trains_while_short and not any(trains_while_short)
    assert any(map(unit_trains, orders_between(recording, stood_at, game.tick).values()))


def first_tick_with_count(states: list[dict], building_type: str, count: int) -> int:
    """The tick of the first state that shows so many own buildings of a type."""
    return next(
        raw["state_version"]
        for raw in states
        if [building["type"] for building in raw["observation"]["buildings"]].count(building_type) >= count
    )


# -- independent unit queues, fed from one budget --------------------------------------------------

MIXED_BASE = {
    "england": (*HOLDING["england"], ("fix", 26, 54), ("powr", 30, 50)),
    "russia": (*HOLDING["russia"], ("fix", 26, 54), ("powr", 30, 50)),
}


def mixed(faction: str = "england", cash: int = 5000, units: list | None = None, **more) -> dict:
    """A base past its opening with a service depot, four harvesters and the units given besides."""
    return base_state(faction, *MIXED_BASE[faction], units=[*harvesters(4), *(units or [])], cash=cash, **more)


class SpentLately:
    """An income estimate whose measures a test sets: what the Infantry and the Vehicle queue spent in the last minute,
    the income, and the share of the last minute every queue was busy.

    Money never counts as having piled up, and the investment test passes whenever the income is known.
    """

    def __init__(
        self, infantry: float, vehicle: float, income_per_s: float | None = None, busy_share: float = 0.0
    ) -> None:
        self._spent = {"Building": 0.0, "Defense": 0.0, "Infantry": infantry, "Vehicle": vehicle}
        self.income_per_s = income_per_s
        self._busy_share = busy_share

    def update(self, world) -> None:
        """Nothing is learned from a state."""

    def spend_by_queue(self) -> dict[str, float]:
        return dict(self._spent)

    def utilization(self, queue: str) -> float:
        return self._busy_share

    def can_invest(self, cost: int, build_seconds: float) -> bool:
        return self.income_per_s is not None

    def has_held_above(self, money: int, seconds: float) -> bool:
        return False


def spent_lately(agent, infantry: float, vehicle: float):
    """The agent, with an income estimate that says the unit queues spent so much in the last minute."""
    agent.ctx.income = SpentLately(infantry, vehicle)
    return agent


def unit_spending(orders: list[dict]) -> int:
    """What the train orders of a batch for the Infantry and the Vehicle queue cost together."""
    items = situations.tracked_items()
    return sum(items[item].cost * count for item, count in unit_trains(orders))


@pytest.mark.parametrize("faction, vehicle", [("england", "2tnk"), ("russia", "3tnk")])
def test_independent_with_a_service_depot_and_5000_both_queues_are_fed_in_the_same_cycle(faction, vehicle):
    assert unit_trains(ask(upkeep_agent(), mixed(faction))) == [("e1", 3), (vehicle, 1)]


def test_independent_after_ten_vehicles_and_no_infantry_the_vehicle_queue_is_still_fed():
    tanks = soldiers("2tnk", 10)
    busy_infantry = [situations.job("Infantry", "e1")]

    with_infantry_busy = ask(upkeep_agent(), mixed(units=tanks, production=busy_infantry))
    with_both_free = ask(upkeep_agent(), mixed(units=tanks))

    assert unit_trains(with_infantry_busy) == [("2tnk", 1)]
    assert unit_trains(with_both_free) == [("e1", 3), ("2tnk", 1)]


@pytest.mark.parametrize("plan, vehicle", [("RAID", "1tnk"), ("PRESS", "1tnk"), ("FINISH", "2tnk")])
def test_independent_queues_are_fed_alike_under_the_plans_that_build_an_army(plan, vehicle):
    # RAID and PRESS fill the Allied raid quota of light tanks first.
    orders = ask(upkeep_agent(plan_selector=OpeningThen(plan)), mixed())

    assert unit_trains(orders) == [("e1", 3), (vehicle, 1)]


def test_independent_under_strike_the_queues_make_the_mix():
    assert unit_trains(ask(upkeep_agent(plan_selector=OpeningThen("STRIKE")), mixed())) == [("e1", 3), ("2tnk", 1)]


def test_independent_queues_leave_the_opening_its_own_orders():
    agent = composed(ROCKETS_HALF, {"1tnk": 0.5})
    before_the_war_factory = base_state("england", YARD, POWER, BARRACKS, REFINERY, units=harvesters(1))

    assert trains(ask(agent, before_the_war_factory)) == []
    assert agent.current_plan == OPENING


# 1,200 of money: the reserve of 300 leaves 900, and a heavy tank (850) and a rifle (100) need 950.
ONE_ONLY_CASH = 1200
TWO_ROCKETS = soldiers("e3", 2)


def test_budget_with_money_for_one_and_the_vehicles_behind_on_spending_the_vehicle_queue_is_fed():
    agent = spent_lately(upkeep_agent(), infantry=900, vehicle=0)

    assert unit_trains(ask(agent, mixed(cash=ONE_ONLY_CASH, units=TWO_ROCKETS))) == [("2tnk", 1)]


def test_budget_with_money_for_one_and_the_infantry_behind_on_spending_the_infantry_queue_is_fed():
    agent = spent_lately(upkeep_agent(), infantry=0, vehicle=1700)

    assert unit_trains(ask(agent, mixed(cash=ONE_ONLY_CASH, units=TWO_ROCKETS))) == [("e1", 3)]


# The Allied fix row of the mix gives its vehicles 45 of 100: 450 of 1,000 is on target.
@pytest.mark.parametrize("infantry, vehicle", [(0, 0), (550, 450)], ids=["nothing spent", "on target"])
def test_budget_with_no_unit_spending_recorded_or_none_behind_the_vehicle_queue_goes_first(infantry, vehicle):
    agent = spent_lately(upkeep_agent(), infantry=infantry, vehicle=vehicle)

    assert unit_trains(ask(agent, mixed(cash=ONE_ONLY_CASH, units=TWO_ROCKETS))) == [("2tnk", 1)]


def test_budget_a_fresh_agent_has_recorded_no_unit_spending_and_feeds_the_vehicle_queue_first():
    assert unit_trains(ask(upkeep_agent(), mixed(cash=ONE_ONLY_CASH, units=TWO_ROCKETS))) == [("2tnk", 1)]


def test_budget_the_queue_that_goes_first_waits_for_its_money_and_the_other_waits_with_it():
    # 1,000 leaves 700: not the heavy tank, though seven rifles would fit.
    agent = spent_lately(upkeep_agent(), infantry=900, vehicle=0)

    assert unit_trains(ask(agent, mixed(cash=1000, units=TWO_ROCKETS))) == []


def made_one_after_another(queue: str, items: list[str]) -> list[ProductionItem]:
    """The front a queue shows in each state while it makes the items one after another: each just begun, then
    nearly done."""
    return [ProductionItem(queue, item, progress, 0.0, False, False) for item in items for progress in (0.0, 0.95)]


def army_and_harvester_spending() -> IncomeEstimate:
    """A real income estimate that has seen, one state a second from 0 s, the Infantry queue make ten rifles (1,000)
    and the Vehicle queue a harvester (1,100) and then a flak truck (600)."""
    queues = (made_one_after_another("Infantry", ["e1"] * 10), made_one_after_another("Vehicle", ["harv", "ftrk"]))
    estimate = IncomeEstimate(tuning_off())
    for second in range(len(queues[0]) + 1):
        production: dict[str, list[ProductionItem]] = {}
        for fronts in queues:
            if second < len(fronts):
                production[fronts[second].queue] = [fronts[second]]
        world = SimpleNamespace(
            tick=second * 25, economy=Economy(cash=5000), production=production, items=situations.tracked_items()
        )
        estimate.update(world)
    return estimate


def test_budget_harvesters_from_the_vehicle_queue_are_left_out_of_the_vehicle_spending_share():
    spent = army_and_harvester_spending().spend_by_queue()

    assert (spent["Infantry"], spent["Vehicle"], spent["economy"]) == pytest.approx((1000, 600, 1100))
    assert army_vehicle_share(spent) == pytest.approx(0.375)


def test_budget_with_the_harvester_left_out_the_vehicles_are_behind_on_spending_and_the_vehicle_queue_is_fed():
    # 600 of 1,600 is below the mix's vehicle share of 0.45; counted with the harvester, 1,700 of 2,700 is above it.
    agent = upkeep_agent()
    agent.ctx.income = army_and_harvester_spending()

    assert unit_trains(ask(agent, mixed(cash=ONE_ONLY_CASH, units=TWO_ROCKETS))) == [("2tnk", 1)]


# Money for a flak truck above the reserve of 300, but not for one and a rifle besides: each cycle feeds one queue.
ONE_TRUCK_CASH = 999
SOVIET_BASE_PICKS = 200
# The Soviet base row of the mix gives its flak trucks 15 of its 100 points.
SOVIET_BASE_FLAK_SHARE = 0.15
FIRST_PICKED_UNIT_ID = 1000


def test_budget_over_200_picks_at_the_soviet_base_stage_flak_trucks_never_go_past_15_percent_of_the_spending():
    """Each pick is one cycle of a fresh agent that has spent what the picks before it bought, with the units they
    bought standing: the plan asks 15% in flak trucks every time, and a flak truck is bought only while flak trucks
    are at most 15% of the unit spending, or nothing has been spent yet."""
    items = situations.tracked_items()
    spent = {"Infantry": 0, "Vehicle": 0}
    army: list[dict] = []
    for _ in range(SOVIET_BASE_PICKS):
        agent = spent_lately(upkeep_agent(), infantry=spent["Infantry"], vehicle=spent["Vehicle"])
        state = holding("russia", cash=ONE_TRUCK_CASH, units=[*harvesters(4), *army])
        ((item, count),) = unit_trains(ask(agent, state))
        asked = agent.ctx.composition(HOLD, agent.world.family, agent.world)
        assert asked.vehicle == pytest.approx({"ftrk": SOVIET_BASE_FLAK_SHARE})
        if item == "ftrk":
            share_before = army_vehicle_share(spent)
            assert share_before is None or share_before <= SOVIET_BASE_FLAK_SHARE
        spent[items[item].queue] += items[item].cost * count
        army += soldiers(item, count, first_id=FIRST_PICKED_UNIT_ID + len(army))

    trucks = sum(1 for unit in army if unit["type"] == "ftrk")
    assert trucks > 1 and army_vehicle_share(spent) == pytest.approx(SOVIET_BASE_FLAK_SHARE, abs=0.01)


def short_of_power_holding(cash: int) -> dict:
    """The holding base, rifles standing, with so little power left that a power plant is ordered first, and money as
    given."""
    return with_rifles(cash=cash, economy=powered(200, 190, cash=cash, harvesters=4))


@pytest.mark.parametrize("cash", [1000, 1300, 5000])
def test_budget_money_set_aside_for_a_power_plant_is_not_spent_on_units(cash):
    agent = spent_lately(upkeep_agent(), infantry=0, vehicle=700)

    orders = ask(agent, short_of_power_holding(cash))

    assert builds(orders) == ["powr"]
    assert unit_spending(orders) <= cash - 300 - 300


def test_budget_with_1000_a_power_plant_and_the_reserve_leave_400_for_units():
    with_plant = ask(spent_lately(upkeep_agent(), infantry=0, vehicle=700), short_of_power_holding(1000))
    without = ask(spent_lately(upkeep_agent(), infantry=0, vehicle=700), with_rifles(cash=1000))

    assert (unit_trains(with_plant), unit_trains(without)) == ([("e3", 1)], [("e3", 2)])


def wanting_producers(agent, income_per_s: float):
    """The agent, with an income estimate that shows every queue busy and so much income: a producer is wanted."""
    agent.ctx.income = SpentLately(0, 0, income_per_s=income_per_s, busy_share=1.0)
    return agent


# The opening's one barracks is the most there may be, so that the war factory is the producer due.
ONE_BARRACKS_ONLY = dataclasses.replace(
    TWO_REFINERIES_ONLY, producers=dataclasses.replace(producers_off(), max_infantry_producers=1)
)


@pytest.mark.parametrize("headroom, ordered", [(80, ["powr"]), (90, ["weap"])])
def test_power_a_war_factory_that_would_take_the_headroom_below_the_target_waits_for_a_power_plant(headroom, ordered):
    # At 380 s the target is 60; the war factory drains 30.
    state = holding(economy=powered(400, 400 - headroom, cash=5000, harvesters=4), tick=MID_TARGET_TICK)

    assert builds(ask(wanting_producers(upkeep_agent(tuning=ONE_BARRACKS_ONLY), 130), state)) == ordered


@pytest.mark.parametrize("cash, spent", [(1000, 0), (1500, 700)])
def test_budget_money_set_aside_for_a_producer_is_not_spent_on_units(cash, spent):
    # At 240 s, with 110 a second coming in, a second barracks (500) is due; the units get what the reserve and it leave.
    state = holding(cash=cash, economy=powered(500, 100, cash=cash, harvesters=4), tick=240 * 25)

    orders = ask(wanting_producers(upkeep_agent(), 110), state)

    assert builds(orders) == ["tent"]
    assert unit_spending(orders) == spent <= cash - 300 - 500


def emergency_defense(world, ctx) -> list:
    """What base defense proposes against a threat: one defense, at emergency priority."""
    return [ctx.production.build("pbox", Priority.EMERGENCY)]


def test_budget_money_set_aside_for_the_base_s_emergency_is_not_spent_on_units():
    agent = new_agent(
        ScriptedManager("base_defense", emergency_defense),
        UpkeepManager(),
        plan_selector=OpeningThen(HOLD),
        tuning=TWO_REFINERIES_ONLY,
    )
    spent_lately(agent, infantry=0, vehicle=700)

    orders = ask(agent, with_rifles(cash=1500))

    # 1,500 less the reserve and the pillbox's 600 leaves 600: two rockets.
    assert unit_trains(orders) == [("e3", 2)]


RIFLES_AND_ROCKETS_EVENLY = {"e1": 0.25, "e3": 0.25}
LIGHT_TANKS_HALF = {"1tnk": 0.5}


def test_deficit_with_rifles_and_rockets_asked_at_equal_value_and_six_rifles_the_next_infantry_is_rockets():
    agent = composed(RIFLES_AND_ROCKETS_EVENLY, LIGHT_TANKS_HALF)

    orders = ask(agent, holding("england", units=[*harvesters(4), *soldiers("e1", 6)]))

    assert trained(orders, "Infantry") == [("e3", 3)]


def test_deficit_a_tie_goes_to_the_cheaper_item():
    agent = composed(RIFLES_AND_ROCKETS_EVENLY, LIGHT_TANKS_HALF)

    assert trained(ask(agent, holding("england")), "Infantry") == [("e1", 3)]


def test_deficit_a_variant_counts_as_its_type():
    # Paratrooper rifles (e1r1) are rifles: with six of them, rockets are what the army is short of.
    agent = composed(RIFLES_AND_ROCKETS_EVENLY, LIGHT_TANKS_HALF)

    orders = ask(agent, holding("england", units=[*harvesters(4), *soldiers("e1r1", 6)]))

    assert trained(orders, "Infantry") == [("e3", 3)]


# -- growing the economy past the opening's two refineries -----------------------------------------


def grown(
    *extra: tuple,
    cash: int,
    ore: int = 0,
    capacity: int = 4000,
    count: int = 4,
    seconds: int = 300,
    withheld: tuple = ("fix",),
    **more,
) -> dict:
    """An Allied base past its opening with the buildings given besides, `count` harvesters and the money given."""
    economy = {
        "cash": cash,
        "ore": ore,
        "resource_capacity": capacity,
        "power_provided": 400,
        "power_drained": 100,
        "harvesters": count,
    }
    buildings = (*HOLDING["england"], *extra)
    return base_state(
        "england", *buildings, units=harvesters(count), tick=seconds * 25, withheld=withheld, economy=economy, **more
    )


def growing_agent():
    """An agent with the upkeep manager alone and the switches-off thresholds, the third refinery included."""
    return upkeep_agent(tuning=tuning_off())


@pytest.mark.parametrize(
    "cash, ore, capacity, queued",
    [(700, 800, 1000, ["proc"]), (699, 800, 1000, []), (750, 750, 1000, ["proc"]), (800, 700, 1000, [])],
    ids=["1,500, 80% full", "1,499", "1,500, 75% full", "1,500, 70% full"],
)
def test_a_third_refinery_is_queued_with_1500_of_money_and_the_refineries_three_quarters_full(
    cash, ore, capacity, queued
):
    # Three harvesters for two refineries: only the full refineries call for a third.
    assert builds(ask(growing_agent(), grown(cash=cash, ore=ore, capacity=capacity, count=3))) == queued


@pytest.mark.parametrize("count, queued", [(4, ["proc"]), (3, [])])
def test_a_third_refinery_is_queued_when_every_refinery_has_its_two_harvesters(count, queued):
    assert builds(ask(growing_agent(), grown(cash=1500, count=count))) == queued


@pytest.mark.parametrize(
    "cash, production, trains",
    [
        (1000, [], []),
        (1600, [situations.job("Building", "proc")], []),
        (1700, [situations.job("Building", "proc")], [("e1", 3), ("1tnk", 1)]),
    ],
    ids=["called for, 1,000", "on its way, 1,600", "on its way, 1,700 covers it and the reserve"],
)
def test_units_wait_while_the_money_falls_short_of_the_third_refinery_and_the_reserve(cash, production, trains):
    state = grown(cash=cash, production=production, seconds=400)

    assert unit_trains(ask(growing_agent(), state)) == trains


def test_with_the_third_refinery_standing_or_not_called_for_units_are_not_held_back():
    standing = grown(THIRD_REFINERY, cash=1300, count=6)
    not_called_for = grown(cash=1300, count=3)

    # With three harvesters for two refineries the Vehicle queue makes a harvester, and the infantry has the rest.
    assert unit_trains(ask(growing_agent(), standing)) == [("e1", 3), ("1tnk", 1)]
    assert trains(ask(growing_agent(), not_called_for)) == [("harv", 1), ("e1", 3)]


THIRD_REFINERY = ("proc", 30, 39)


@pytest.mark.parametrize("seconds, queued", [(479, []), (480, ["proc"])])
def test_a_fourth_refinery_is_queued_on_the_same_condition_from_480_seconds_on(seconds, queued):
    state = grown(THIRD_REFINERY, cash=1500, count=6, seconds=seconds)

    assert builds(ask(growing_agent(), state)) == queued


def test_no_fifth_refinery_is_queued():
    state = grown(THIRD_REFINERY, ("proc", 34, 39), cash=5000, count=8, seconds=900)

    assert builds(ask(growing_agent(), state)) == []


def test_no_refinery_is_added_before_the_opening_s_war_factory_has_stood():
    without_war_factory = [spec for spec in HOLDING["england"] if spec[0] != "weap"]
    state = base_state("england", *without_war_factory, units=harvesters(4), cash=5000, tick=5000)

    assert builds(ask(growing_agent(), state)) == []


def test_a_refinery_ordered_already_is_not_ordered_again():
    agent = growing_agent()
    first = ask(agent, grown(cash=2000, seconds=300))

    second = ask(agent, grown(cash=2000, seconds=301))

    assert (builds(first), builds(second)) == (["proc"], [])


@pytest.mark.parametrize("seconds, held", [(200, False), (359, False), (360, True), (400, True)])
def test_units_are_held_back_for_the_third_refinery_only_from_360_seconds_on(seconds, held):
    state = grown(cash=1500, seconds=seconds)

    assert (unit_trains(ask(growing_agent(), state)) == []) is held


def test_while_units_wait_for_the_third_refinery_it_goes_ahead_of_the_service_depot():
    early = grown(cash=1500, seconds=200, withheld=())
    saving = grown(cash=1500, seconds=400, withheld=())

    assert builds(ask(growing_agent(), early)) == ["fix"]
    assert builds(ask(growing_agent(), saving)) == ["proc"]


def test_budget_no_fixed_vehicle_spending_share_or_ratio_gate_is_left_in_the_tuning():
    composition = Tuning.from_file(situations.REPO_ROOT).composition

    assert {"vehicle_spend_share", "infantry_per_vehicle"}.isdisjoint(
        {field.name for field in dataclasses.fields(composition)}
    )


@pytest.mark.parametrize(
    "faction, cash, spent, fed",
    [
        ("england", ONE_ONLY_CASH, (520, 480), [("e1", 3)]),
        ("england", ONE_ONLY_CASH, (560, 440), [("2tnk", 1)]),
        ("russia", 1500, (580, 420), [("e1", 3)]),
        ("russia", 1500, (620, 380), [("3tnk", 1)]),
    ],
    ids=["allies 0.48 above 0.45", "allies 0.44 below 0.45", "soviets 0.42 above 0.40", "soviets 0.38 below 0.40"],
)
def test_budget_the_vehicle_spending_target_is_the_mix_s_vehicle_share(faction, cash, spent, fed):
    # The fix rows give the vehicles 45 (Allies) and 40 (Soviets); the money pays for the tank or the rifles, not both.
    agent = spent_lately(upkeep_agent(), infantry=spent[0], vehicle=spent[1])

    assert unit_trains(ask(agent, mixed(faction, cash=cash, units=TWO_ROCKETS))) == fed


# An army of twenty rifles seen far from the base: the mix shifts 10 of the 2tnk's 45 to the rifles.
SEEN_RIFLES = [{"id": 900 + number, "type": "e1", "x": 74, "y": 40 + number % 10, "hp": 100} for number in range(20)]


@pytest.mark.parametrize("enemies, fed", [([], [("2tnk", 1)]), (SEEN_RIFLES, [("e1", 3)])], ids=["no shift", "shift"])
def test_budget_the_vehicle_spending_target_follows_the_counter_shift(enemies, fed):
    # 400 of 1,000 is below the row's 0.45, and above the 0.35 left to the vehicles against an army of infantry.
    agent = spent_lately(upkeep_agent(), infantry=600, vehicle=400)

    assert unit_trains(ask(agent, mixed(cash=ONE_ONLY_CASH, units=TWO_ROCKETS, visible_enemies=enemies))) == fed


# -- the radar for the army mix's dome stage --------------------------------------------------------


class Earning(SpentLately):
    """An income estimate that earns so much a second, nothing busy, and puts each building to the real investment
    test at the money given; it notes the price and build time it was asked about."""

    def __init__(self, income_per_s: float | None, money: int) -> None:
        super().__init__(0, 0, income_per_s=income_per_s)
        self._money = money
        self.asked: list[tuple[int, float]] = []

    def can_invest(self, cost: int, build_seconds: float) -> bool:
        self.asked.append((cost, build_seconds))
        tuning = tuning_off()
        reserve, share = tuning.upkeep.production_cash_reserve, tuning.income.invest_income_share
        return can_invest(cost, build_seconds, self._money, self.income_per_s, reserve, share)


DOME_SECONDS = 380


def earning_agent(income_per_s: float | None = 120, money: int = 1500):
    """An upkeep agent with an income estimate that earns so much a second, and the estimate."""
    agent = upkeep_agent()
    agent.ctx.income = Earning(income_per_s, money)
    return agent, agent.ctx.income


def depot_base(*buildings: tuple, seconds: int = DOME_SECONDS, cash: int = 1500, headroom: int = 300, **more) -> dict:
    """An Allied base past its opening with a service depot, the buildings given besides, and so much power over."""
    economy = powered(400, 400 - headroom, cash=cash, harvesters=4)
    return base_state(
        "england", *MIXED_BASE["england"], *buildings, units=harvesters(4), tick=seconds * 25, economy=economy, **more
    )


def test_dome_at_380_s_with_a_depot_income_120_and_1500_the_radar_is_ordered():
    agent, earning = earning_agent()

    assert builds(ask(agent, depot_base())) == ["dome"]
    # The radar's price and build time: 1,500 and 36 s.
    assert (1500, 36.0) in earning.asked


@pytest.mark.parametrize("seconds, queued", [(300, []), (359, []), (360, ["dome"])])
def test_dome_no_radar_is_ordered_before_360_seconds(seconds, queued):
    agent, _ = earning_agent()

    assert builds(ask(agent, depot_base(seconds=seconds))) == queued


def test_dome_without_a_service_depot_no_radar_is_ordered():
    agent, _ = earning_agent()
    economy = powered(400, 100, cash=1500, harvesters=4)
    no_depot = base_state("england", *DEPOT_LOST, units=harvesters(4), tick=DOME_SECONDS * 25, economy=economy)

    assert "dome" not in builds(ask(agent, no_depot))


def test_dome_with_a_radar_on_its_way_or_standing_no_other_is_ordered():
    agent, _ = earning_agent()
    first = ask(agent, depot_base())
    ordered_a_moment_ago = ask(agent, depot_base(seconds=DOME_SECONDS + 1))
    in_its_queue = ask(earning_agent()[0], depot_base(production=[situations.job("Building", "dome")]))
    standing = ask(earning_agent()[0], depot_base(("dome", 30, 44)))

    assert builds(first) == ["dome"]
    assert [builds(orders) for orders in (ordered_a_moment_ago, in_its_queue, standing)] == [[], [], []]
    assert agent.ledger.duplicate_production_orders == 0


def test_dome_a_radar_that_stood_and_was_lost_is_ordered_again():
    agent, _ = earning_agent()
    ask(agent, depot_base(("dome", 30, 44), seconds=400))

    assert builds(ask(agent, depot_base(seconds=405))) == ["dome"]


def test_dome_no_radar_is_ordered_while_the_base_is_under_attack():
    agent, _ = earning_agent()
    agent.ctx.threat_active = True

    assert builds(ask(agent, depot_base())) == []


def test_dome_no_radar_is_ordered_while_the_money_is_saved_for_a_lost_core_building():
    # The second refinery is lost and 1,500 does not cover its 1,400 and the reserve: the money is saved for it.
    agent, _ = earning_agent()
    ask(agent, depot_base(seconds=DOME_SECONDS - 1, production=[situations.job("Building", "dome")]))
    without_a_refinery = [spec for spec in MIXED_BASE["england"] if spec != ("proc", 18, 39)]
    economy = powered(400, 100, cash=1500, harvesters=4)
    lost = base_state("england", *without_a_refinery, units=harvesters(4), tick=DOME_SECONDS * 25, economy=economy)

    assert builds(ask(agent, lost)) == []


def test_dome_a_radar_that_would_leave_less_than_the_power_target_waits_for_a_power_plant():
    # At 380 s the target is 60 and the radar drains 40: 90 over leaves 50.
    agent, _ = earning_agent()

    assert builds(ask(agent, depot_base(headroom=90))) == ["powr"]
    assert builds(ask(earning_agent()[0], depot_base(headroom=100))) == ["dome"]


@pytest.mark.parametrize(
    "income_per_s, money, queued",
    [(120, 1500, ["dome"]), (0, 1799, []), (0, 1800, ["dome"]), (None, 5000, [])],
    ids=["income 120", "no income, 1,799", "no income, 1,800", "income unknown"],
)
def test_dome_the_radar_waits_for_the_investment_test(income_per_s, money, queued):
    agent, _ = earning_agent(income_per_s, money)

    assert builds(ask(agent, depot_base(cash=money))) == queued


def test_dome_from_seconds_is_read_from_the_tracked_file():
    assert Tuning.from_file(situations.REPO_ROOT).upkeep.dome_from_seconds == 360


# -- the answer to enemy air -----------------------------------------------------------------------


def test_air_the_anti_air_table_is_read_from_the_tracked_file_with_the_requirement_s_values():
    anti_air = Tuning.from_file(situations.REPO_ROOT).anti_air

    assert anti_air == AntiAirTuning()
    assert (anti_air.enabled, anti_air.aa_min_aircraft, anti_air.aa_window_seconds, anti_air.air_flak_points) == (
        True,
        3,
        180,
        0,
    )
    assert (anti_air.aa_defenses, anti_air.aa_defenses_many, anti_air.aa_many_aircraft) == (1, 2, 6)
    assert anti_air.aa_refinery_cells == 6


@pytest.mark.parametrize(
    "old, new, named",
    [
        ("aa_defenses_many = 2", "", "aa_defenses_many"),
        ("aa_refinery_cells = 6", "aa_refinery_cells = -1", "aa_refinery_cells"),
        ("is done.\nenabled = true", "is done.\nenabled = 1", "enabled"),
    ],
    ids=["missing", "negative", "not true or false"],
)
def test_air_a_key_of_the_anti_air_table_that_is_missing_or_wrong_fails_the_loader_naming_it(tmp_path, old, new, named):
    target = tmp_path / TUNING_FILE
    target.parent.mkdir(parents=True)
    text = (situations.REPO_ROOT / TUNING_FILE).read_text(encoding="utf-8")
    assert old in text
    target.write_text(text.replace(old, new), encoding="utf-8")

    with pytest.raises(ConfigError, match=rf"anti_air.*{named}|{named}.*anti_air"):
        Tuning.from_file(tmp_path)


AA_DEFENSES = [("england", "agun"), ("russia", "sam")]
# Where a radar, and an AA defense that stands, stand in the bases of these tests.
RADAR_SPEC, AA_CELL = ("dome", 30, 44), (34, 48)
# The Tesla coils a Soviet base keeps, standing in the Soviet bases of these tests: the Defense queue is then left to
# the AA defense.
STANDING_COILS = {"england": (), "russia": (("tsla", 36, 44), ("tsla", 38, 44))}


def aircraft(count: int, aircraft_type: str = "yak", first_id: int = 950) -> list[dict]:
    """So many enemy combat aircraft in sight, far from the base."""
    return [{"id": first_id + n, "type": aircraft_type, "x": 74, "y": 30 + n, "hp": 100} for n in range(count)]


# The fewest enemy combat aircraft seen lately that call for an AA defense, as the tuning file ships.
TRIGGER = AntiAirTuning().aa_min_aircraft
AIR_PRODUCER = {"id": 980, "type": "afld", "x": 74, "y": 48, "hp": 100}


def air_base(
    faction: str = "england",
    *buildings: tuple,
    seconds: int = DOME_SECONDS,
    cash: int = 1500,
    headroom: int = 300,
    radar: bool = True,
    **more,
) -> dict:
    """A base past its opening with a service depot, a radar unless told otherwise, the Tesla coils of a Soviet base,
    the buildings given besides, four harvesters and so much power over."""
    economy = powered(400, 400 - headroom, cash=cash, harvesters=4)
    radars = (RADAR_SPEC,) if radar else ()
    return base_state(
        faction,
        *MIXED_BASE[faction],
        *radars,
        *STANDING_COILS[faction],
        *buildings,
        units=harvesters(4),
        tick=seconds * 25,
        economy=economy,
        **more,
    )


@pytest.mark.parametrize("faction, defense", AA_DEFENSES)
def test_air_with_air_seen_a_radar_power_and_no_threat_one_aa_defense_is_ordered_once(faction, defense):
    agent, earning = earning_agent()

    first = ask(agent, air_base(faction, visible_enemies=aircraft(TRIGGER)))
    a_moment_later = ask(agent, air_base(faction, seconds=DOME_SECONDS + 1, visible_enemies=aircraft(TRIGGER)))

    assert (defenses(first), defenses(a_moment_later)) == ([defense], [])
    assert agent.ledger.duplicate_production_orders == 0
    # The AA defense's price and build time went to the investment test.
    assert (situations.tracked_items()[defense].cost, situations.tracked_items()[defense].build_time_s) in earning.asked


def test_air_no_aa_defense_is_ordered_without_a_standing_radar_or_without_air_seen_or_when_it_is_not_on_offer():
    # The AA gun is offered without a radar here, so that only the rule's own wait for the radar holds it back.
    offered = [*air_base()["observation"]["available_production"], "agun"]
    no_radar = air_base(radar=False, visible_enemies=aircraft(TRIGGER), available_production=offered)
    radar_on_its_way = air_base(
        radar=False,
        visible_enemies=aircraft(TRIGGER),
        available_production=offered,
        production=[situations.job("Building", "dome")],
    )
    no_air = air_base(
        visible_enemies=[{"id": 950, "type": "e1", "x": 74, "y": 30, "hp": 100}, *aircraft(1, "tran", 960)]
    )
    not_offered = air_base(visible_enemies=aircraft(TRIGGER), withheld=("agun",))

    answers = [ask(earning_agent()[0], state) for state in (no_radar, radar_on_its_way, no_air, not_offered)]

    assert [defenses(orders) for orders in answers] == [[], [], [], []]


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_army_build_an_air_producer_or_fewer_than_3_aircraft_seen_call_for_no_aa_defense(faction):
    producer = {"id": 980, "type": "afld" if faction == "russia" else "hpad", "x": 74, "y": 48, "hp": 100}

    answers = [
        ask(earning_agent()[0], air_base(faction, visible_enemy_buildings=[producer])),
        ask(earning_agent()[0], air_base(faction, visible_enemies=aircraft(1))),
        ask(earning_agent()[0], air_base(faction, visible_enemies=aircraft(TRIGGER - 1))),
    ]

    assert [defenses(orders) for orders in answers] == [[], [], []]


@pytest.mark.parametrize("faction, defense", AA_DEFENSES)
def test_army_build_with_6_enemy_combat_aircraft_seen_lately_a_second_aa_defense_is_ordered(faction, defense):
    standing = (defense, *AA_CELL)

    five = ask(earning_agent()[0], air_base(faction, standing, visible_enemies=aircraft(5)))
    six = ask(earning_agent()[0], air_base(faction, standing, visible_enemies=aircraft(6)))

    assert (defenses(five), defenses(six)) == ([], [defense])


def test_army_build_the_six_aircraft_are_counted_though_each_was_shot_down_before_the_next_came():
    agent, _ = earning_agent()
    standing = ("agun", *AA_CELL)
    answers = []
    for number in range(6):
        # The mig of the state before is shot down: its wreck is seen, and the enemy memory forgets it.
        shot_down = [{"id": 949 + number, "type": "mig", "wreck": True, "x": 74, "y": 30}] if number else []
        in_sight = [*shot_down, *aircraft(1, "mig", 950 + number)]
        answers.append(
            defenses(ask(agent, air_base("england", standing, seconds=380 + number, visible_enemies=in_sight)))
        )

    assert answers == [[], [], [], [], [], ["agun"]]
    assert agent.ctx.air_watch.aircraft_seen == 6 and len(agent.world.enemy_memory) == 1


def test_air_never_more_than_two_aa_defenses_stand_or_are_on_their_way():
    two_standing = air_base("england", ("agun", *AA_CELL), ("agun", 36, 48), visible_enemies=aircraft(8))
    one_and_one_queued = air_base(
        "england", ("agun", *AA_CELL), visible_enemies=aircraft(8), production=[situations.job("Defense", "agun")]
    )

    answers = [ask(earning_agent()[0], state) for state in (two_standing, one_and_one_queued)]

    assert [defenses(orders) for orders in answers] == [[], []]


def test_air_one_aa_defense_is_on_its_way_at_a_time_though_two_are_wanted():
    agent, _ = earning_agent()
    first = ask(agent, air_base("england", visible_enemies=aircraft(6)))
    while_on_its_way = ask(agent, air_base("england", seconds=DOME_SECONDS + 1, visible_enemies=aircraft(6)))
    queued = ask(
        earning_agent()[0],
        air_base("england", visible_enemies=aircraft(6), production=[situations.job("Defense", "agun")]),
    )

    assert [defenses(orders) for orders in (first, while_on_its_way, queued)] == [["agun"], [], []]


def test_air_an_aa_defense_that_stood_and_was_lost_is_ordered_again():
    agent, _ = earning_agent()
    with_one = ask(agent, air_base("england", ("agun", *AA_CELL), seconds=400, visible_enemies=aircraft(TRIGGER)))

    lost = ask(agent, air_base("england", seconds=405))

    assert (defenses(with_one), defenses(lost)) == ([], ["agun"])


def test_air_no_aa_defense_is_ordered_while_the_base_is_under_attack():
    agent, _ = earning_agent()
    agent.ctx.threat_active = True

    assert defenses(ask(agent, air_base(visible_enemies=aircraft(TRIGGER)))) == []


def test_air_no_aa_defense_is_ordered_while_the_money_is_saved_for_a_lost_core_building():
    # The second refinery is lost and 1,500 does not cover its 1,400 and the reserve: the money is saved for it.
    without_a_refinery = [spec for spec in MIXED_BASE["england"] if spec != ("proc", 18, 39)]
    economy = powered(400, 100, cash=1500, harvesters=4)
    lost = base_state(
        "england",
        *without_a_refinery,
        RADAR_SPEC,
        units=harvesters(4),
        tick=DOME_SECONDS * 25,
        economy=economy,
        visible_enemies=aircraft(TRIGGER),
    )

    assert defenses(ask(earning_agent()[0], lost)) == []


def test_air_an_aa_defense_that_would_leave_less_than_the_power_target_waits_for_a_power_plant():
    # At 380 s the target is 60 and the AA gun drains 50: 100 over leaves 50. With a radar standing and 1,500 the
    # power plant is the advanced one.
    short = ask(earning_agent()[0], air_base(headroom=100, visible_enemies=aircraft(TRIGGER)))
    enough = ask(earning_agent()[0], air_base(headroom=110, visible_enemies=aircraft(TRIGGER)))

    assert (defenses(short), builds(short)) == ([], ["apwr"])
    assert defenses(enough) == ["agun"]


@pytest.mark.parametrize(
    "income_per_s, money, ordered",
    [(120, 1500, ["agun"]), (0, 1099, []), (0, 1100, ["agun"]), (None, 5000, [])],
    ids=["income 120", "no income, 1,099", "no income, 1,100", "income unknown"],
)
def test_air_the_aa_defense_waits_for_the_investment_test(income_per_s, money, ordered):
    agent, _ = earning_agent(income_per_s, money)

    assert defenses(ask(agent, air_base(cash=money, visible_enemies=aircraft(TRIGGER)))) == ordered


def test_air_air_seen_before_the_radar_stood_still_calls_for_the_aa_defense_once_it_stands():
    agent, _ = earning_agent()
    before = ask(agent, air_base(radar=False, seconds=400, visible_enemies=aircraft(TRIGGER)))

    # Five seconds later the yak is out of sight and the radar stands.
    after = ask(agent, air_base(seconds=405))

    assert (defenses(before), defenses(after)) == ([], ["agun"])
    assert all(not record.visible for record in agent.world.enemy_memory.values())


@pytest.mark.parametrize(
    "aircraft_seen, wanted, wanted_by_other_tuning",
    [(0, 0, 0), (1, 0, 2), (3, 1, 2), (4, 1, 2), (5, 1, 3), (7, 2, 3)],
    ids=["none", "1", "3", "4", "5", "7"],
)
def test_army_build_the_aa_defenses_wanted_are_none_below_3_aircraft_then_one_and_two_from_6(
    aircraft_seen, wanted, wanted_by_other_tuning
):
    agent = upkeep_agent()
    ask(agent, air_base(visible_enemies=aircraft(aircraft_seen), visible_enemy_buildings=[AIR_PRODUCER]))
    other = AntiAirTuning(aa_min_aircraft=1, aa_defenses=2, aa_defenses_many=3, aa_many_aircraft=5)
    air, tick = agent.ctx.air_watch, agent.world.tick

    assert anti_air_defenses_wanted(air, tick, AntiAirTuning()) == wanted
    assert anti_air_defenses_wanted(air, tick, other) == wanted_by_other_tuning


# The middle cells of the two refineries of these bases, at (24, 39) and (18, 39): with the enemy's start at (74, 48),
# the first is the one nearest it.
EXPOSED_REFINERY_MIDDLE, OTHER_REFINERY_MIDDLE = (24, 40), (18, 40)


@pytest.mark.parametrize("faction, defense", AA_DEFENSES)
def test_air_a_ready_aa_defense_is_placed_within_6_cells_of_the_refinery_nearest_the_enemy_start(
    reference, faction, defense
):
    agent = upkeep_agent()
    state = air_base(faction, visible_enemies=aircraft(TRIGGER), production=[ready("Defense", defense)])

    (order,) = places(ask(agent, state, situations.context(reference)))

    cell, tuning = tuple(order["at"]), agent.ctx.tuning
    assert order["item"] == defense and state["legal_actions"]["enemy_base"] == [74, 48]
    assert cells.is_within(cell, EXPOSED_REFINERY_MIDDLE, 6) and cells.is_within(cell, (24, 39), 6)
    assert cells.distance(cell, EXPOSED_REFINERY_MIDDLE) < cells.distance(cell, OTHER_REFINERY_MIDDLE)
    assert cell == placement.preferred_cell(defense, agent.world, agent.ctx.terrain, tuning.placement, (), 6)
    assert not box_at(defense, cell).holds_any(kept_free(agent))


def test_air_before_air_is_seen_a_ready_aa_gun_is_placed_as_any_other_defense(reference):
    agent = upkeep_agent()
    state = air_base(production=[ready("Defense", "agun")])

    (order,) = places(ask(agent, state, situations.context(reference)))

    as_a_defense = placement.preferred_cell("pbox", agent.world, agent.ctx.terrain, agent.ctx.tuning.placement)
    assert tuple(order["at"]) == as_a_defense and not cells.is_within(as_a_defense, EXPOSED_REFINERY_MIDDLE, 6)


def anti_air_agent(enabled: bool):
    """An upkeep agent with the answer to enemy air switched on or off, and an income estimate that earns 120 a
    second at 1,500."""
    agent = upkeep_agent(tuning=dataclasses.replace(TWO_REFINERIES_ONLY, anti_air=AntiAirTuning(enabled=enabled)))
    agent.ctx.income = Earning(120, 1500)
    return agent


@pytest.mark.parametrize("faction, defense", AA_DEFENSES)
def test_air_with_the_anti_air_answer_switched_off_no_aa_defense_is_ordered_and_the_mix_does_not_shift(
    faction, defense
):
    seen = air_base(faction, visible_enemies=aircraft(4), visible_enemy_buildings=[AIR_PRODUCER])
    off, on, unseen = anti_air_agent(False), anti_air_agent(True), anti_air_agent(True)

    answers = [ask(off, seen), ask(on, seen), ask(unseen, air_base(faction))]

    family = off.world.family
    assert [defenses(orders) for orders in answers] == [[], [defense], []]
    assert [agent.ctx.army_mix.shifts(agent.world) for agent in (off, on, unseen)] == ["none", "air", "none"]
    assert off.ctx.composition(HOLD, family, off.world) == unseen.ctx.composition(HOLD, family, unseen.world)
    assert off.ctx.composition(HOLD, family, off.world) != on.ctx.composition(HOLD, family, on.world)


def test_air_with_the_anti_air_answer_switched_off_a_ready_aa_gun_is_placed_as_any_other_defense(reference):
    agent = anti_air_agent(False)
    state = air_base(visible_enemies=aircraft(1), production=[ready("Defense", "agun")])

    (order,) = places(ask(agent, state, situations.context(reference)))

    as_a_defense = placement.preferred_cell("pbox", agent.world, agent.ctx.terrain, agent.ctx.tuning.placement)
    assert tuple(order["at"]) == as_a_defense


def aircraft_only_agent():
    """An upkeep agent whose AA defenses wait for an enemy combat aircraft (`defenses_on_threat_only` on), with an
    income estimate that earns 120 a second at 1,500."""
    agent = upkeep_agent(tuning=ON_THREAT_ONLY)
    agent.ctx.income = Earning(120, 1500)
    return agent


@pytest.mark.parametrize("faction, defense", AA_DEFENSES)
def test_on_threat_only_an_enemy_airfield_seen_without_aircraft_orders_no_aa_defense_and_the_mix_does_not_shift(
    faction, defense
):
    agent = aircraft_only_agent()

    orders = ask(agent, air_base(faction, visible_enemy_buildings=[AIR_PRODUCER]))

    assert defenses(orders) == []
    assert agent.ctx.air_watch.is_air_seen and agent.ctx.army_mix.shifts(agent.world) == "none"


@pytest.mark.parametrize("faction, defense", AA_DEFENSES)
def test_on_threat_only_once_3_enemy_combat_aircraft_are_seen_an_aa_defense_is_ordered_as_without_the_switch(
    faction, defense
):
    agent = aircraft_only_agent()
    producer_only = ask(agent, air_base(faction, visible_enemy_buildings=[AIR_PRODUCER]))

    seen = {"visible_enemies": aircraft(TRIGGER), "visible_enemy_buildings": [AIR_PRODUCER]}
    orders = ask(agent, air_base(faction, seconds=DOME_SECONDS + 1, **seen))

    assert (defenses(producer_only), defenses(orders)) == ([], [defense])


def file_agent(root: Path):
    """An upkeep agent with the thresholds of the tuning file under `root`, kept to two refineries, and an income
    estimate that earns 120 a second at 1,500."""
    agent = upkeep_agent(tuning=dataclasses.replace(Tuning.from_file(root), upkeep=TWO_REFINERIES_ONLY.upkeep))
    agent.ctx.income = Earning(120, 1500)
    return agent


def test_air_enabled_false_in_the_tracked_file_switches_the_answer_to_enemy_air_off(tmp_path):
    target = tmp_path / TUNING_FILE
    target.parent.mkdir(parents=True)
    text = (situations.REPO_ROOT / TUNING_FILE).read_text(encoding="utf-8")
    target.write_text(text.replace("is done.\nenabled = true", "is done.\nenabled = false"), encoding="utf-8")
    off, on = file_agent(tmp_path), file_agent(situations.REPO_ROOT)

    answers = [ask(agent, air_base(visible_enemies=aircraft(4))) for agent in (off, on)]

    assert off.ctx.tuning.anti_air == AntiAirTuning(enabled=False) and off.ctx.tuning.raids.enabled
    assert [defenses(orders) for orders in answers] == [[], ["agun"]]
    assert (off.ctx.air_watch.is_air_seen, on.ctx.air_watch.is_air_seen) == (False, True)


# -- the Soviet Tesla coils ------------------------------------------------------------------------

# Before the sixth minute, while the power target is 20 until two coils stand or are on their way.
COIL_SECONDS = 300
FIRST_COIL, SECOND_COIL = ("tsla", 30, 46), ("tsla", 33, 46)
# The Tesla coils switched on, as the tuning file ships them, and off.
TWO_COILS = tuning_off(upkeep=dataclasses.replace(TWO_REFINERIES_ONLY.upkeep, soviet_tesla_coils=2))
NO_COILS = tuning_off(upkeep=dataclasses.replace(TWO_REFINERIES_ONLY.upkeep, soviet_tesla_coils=0))


def coil_agent(income_per_s: float | None = 120, money: int = 1500):
    """An upkeep agent that keeps two Tesla coils in a Soviet base, with an income estimate that earns so much a
    second, and the estimate."""
    agent = upkeep_agent(tuning=TWO_COILS)
    agent.ctx.income = Earning(income_per_s, money)
    return agent, agent.ctx.income


def soviet_base(
    *buildings: tuple,
    seconds: int = COIL_SECONDS,
    cash: int = 1500,
    headroom: int = 300,
    withheld: tuple = ("fix",),
    **more,
) -> dict:
    """The Soviet base past its opening, its war factory and flame tower included, with the buildings given besides,
    four harvesters and so much power over.

    No service depot is on offer unless `withheld` says otherwise, so that the Building queue is not taken by one.
    """
    economy = powered(400, 400 - headroom, cash=cash, harvesters=4)
    return base_state(
        "russia",
        *HOLDING["russia"],
        *buildings,
        units=harvesters(4),
        tick=seconds * 25,
        economy=economy,
        withheld=withheld,
        **more,
    )


def test_tesla_a_soviet_base_with_a_war_factory_power_and_money_orders_one_coil_then_a_second_and_no_third():
    agent, earning = coil_agent()

    first = ask(agent, soviet_base())
    second = ask(coil_agent()[0], soviet_base(FIRST_COIL))
    third = ask(coil_agent()[0], soviet_base(FIRST_COIL, SECOND_COIL))

    assert [defenses(orders) for orders in (first, second, third)] == [["tsla"], ["tsla"], []]
    # The coil's price and build time went to the investment test.
    assert (1200, 28.8) in earning.asked


def test_tesla_one_coil_is_on_its_way_at_a_time_though_two_are_wanted():
    agent, _ = coil_agent()
    first = ask(agent, soviet_base())
    a_moment_later = ask(agent, soviet_base(seconds=COIL_SECONDS + 1))
    one_standing_one_queued = ask(
        coil_agent()[0], soviet_base(FIRST_COIL, production=[situations.job("Defense", "tsla")])
    )

    assert [defenses(orders) for orders in (first, a_moment_later, one_standing_one_queued)] == [["tsla"], [], []]
    assert agent.ledger.duplicate_production_orders == 0


@pytest.mark.parametrize(
    "headroom, built, defended",
    [(99, ["powr"], []), (100, [], ["tsla"])],
    ids=["99 over: 19 left", "100 over: 20 left"],
)
def test_tesla_a_coil_that_would_leave_less_than_the_power_target_waits_for_a_power_plant(headroom, built, defended):
    # At 300 s the target is 20 and the coil drains 80.
    orders = ask(coil_agent()[0], soviet_base(headroom=headroom))

    assert (builds(orders), defenses(orders)) == (built, defended)


def test_tesla_without_a_war_factory_or_with_the_coil_off_offer_no_coil_is_ordered():
    without_war_factory = [spec for spec in HOLDING["russia"] if spec != WAR_FACTORY]
    economy = powered(400, 100, cash=1500, harvesters=4)
    no_war_factory = base_state("russia", *without_war_factory, units=harvesters(4), tick=7500, economy=economy)
    # The coil offered without a war factory, so that only the rule's own wait for the war factory holds it back.
    offered = base_state(
        "russia",
        *without_war_factory,
        units=harvesters(4),
        tick=7500,
        economy=economy,
        available_production=[*no_war_factory["observation"]["available_production"], "tsla"],
    )
    not_offered = soviet_base(withheld=("fix", "tsla"))

    answers = [ask(coil_agent()[0], state) for state in (no_war_factory, offered, not_offered)]

    assert "tsla" not in no_war_factory["observation"]["available_production"]
    assert [defenses(orders) for orders in answers] == [[], [], []]


def test_tesla_an_allied_base_gets_no_coil_and_no_gun_from_the_rule():
    economy = powered(400, 100, cash=1500, harvesters=4)
    allied = base_state("england", *HOLDING["england"], units=harvesters(4), tick=COIL_SECONDS * 25, economy=economy)

    assert defenses(ask(coil_agent()[0], allied)) == []


def test_tesla_a_coil_that_stood_and_was_lost_is_ordered_again():
    agent, _ = coil_agent()
    with_two = ask(agent, soviet_base(FIRST_COIL, SECOND_COIL))

    lost = ask(agent, soviet_base(FIRST_COIL, seconds=COIL_SECONDS + 5))

    assert (defenses(with_two), defenses(lost)) == ([], ["tsla"])


def test_tesla_no_coil_is_ordered_while_the_base_is_under_attack():
    agent, _ = coil_agent()
    agent.ctx.threat_active = True

    assert defenses(ask(agent, soviet_base())) == []


def test_tesla_no_coil_is_ordered_while_the_money_is_saved_for_a_lost_core_building():
    # The second refinery is lost and 1,500 does not cover its 1,400 and the reserve: the money is saved for it.
    agent, _ = coil_agent()
    ask(agent, soviet_base(FIRST_COIL, seconds=COIL_SECONDS - 1))
    without_a_refinery = [spec for spec in HOLDING["russia"] if spec != ("proc", 18, 39)]
    economy = powered(400, 100, cash=1500, harvesters=4)
    lost = base_state(
        "russia", *without_a_refinery, units=harvesters(4), tick=COIL_SECONDS * 25, economy=economy, withheld=("fix",)
    )

    assert defenses(ask(agent, lost)) == []


@pytest.mark.parametrize(
    "income_per_s, money, ordered",
    [(120, 1500, ["tsla"]), (0, 1499, []), (0, 1500, ["tsla"]), (None, 5000, [])],
    ids=["income 120", "no income, 1,499", "no income, 1,500", "income unknown"],
)
def test_tesla_the_coil_waits_for_the_investment_test(income_per_s, money, ordered):
    agent, _ = coil_agent(income_per_s, money)

    assert defenses(ask(agent, soviet_base(cash=money))) == ordered


@pytest.mark.parametrize(
    "tuning, coils, ordered",
    [(NO_COILS, 0, [[], []]), (None, 2, [["tsla"], ["tsla"]])],
    ids=["soviet_tesla_coils 0: none kept", "the tracked file: two kept"],
)
def test_tesla_a_soviet_base_keeps_the_coils_its_thresholds_give(tuning, coils, ordered):
    tuning = tuning or switched_off(Tuning.from_file(situations.REPO_ROOT))
    orders = []
    for standing in ((), (FIRST_COIL,)):
        agent = upkeep_agent(tuning=tuning)
        agent.ctx.income = Earning(120, 1500)
        orders.append(ask(agent, soviet_base(*standing)))

    assert tuning.upkeep.soviet_tesla_coils == coils
    assert [defenses(answer) for answer in orders] == ordered


TESLA_MATCH_SECONDS = 400
# The thresholds of a whole match with the Tesla coils switched off.
BUILT_IN_NO_COILS = tuning_off(upkeep=dataclasses.replace(upkeep_off(), soviet_tesla_coils=0))


@pytest.mark.parametrize(
    "faction, tuning, coils",
    [("russia", None, 2), ("england", None, 0), ("russia", BUILT_IN_NO_COILS, 0)],
    ids=["soviets, the tracked file", "allies, the tracked file", "soviets, no coils"],
)
def test_tesla_in_a_fake_match_a_soviet_base_comes_to_the_coils_it_keeps_and_orders_no_more_and_an_allied_one_to_none(
    faction, tuning, coils
):
    tuning = tuning or switched_off(Tuning.from_file(situations.REPO_ROOT))
    recording = situations.Recording(own_agent(tuning=tuning))

    played = FakeRedAlert(faction, ENEMY_OF[faction]).run(
        recording, STATES_PER_SECOND * TESLA_MATCH_SECONDS, keep_states=True
    )

    ordered = [order for orders in recording.orders_at.values() for order in builds_of(orders, "tsla")]
    standing = [b for b in played.states[-1]["observation"]["buildings"] if b["type"] == "tsla"]
    short = [raw["state_version"] for raw in played.states if _is_short_of_power(raw["observation"]["economy"])]
    assert (len(ordered), len(standing), short) == (coils, coils, [])


# -- the Soviet Tesla coils with upkeep's defenses bought only against a real threat -------------------------------

# Two coils kept at the most, ordered only for a vehicle attack.
COILS_ON_THREAT_ONLY = tuning_off(upkeep=dataclasses.replace(TWO_COILS.upkeep, defenses_on_threat_only=True))
# The cells of vehicle attacks on the Soviet base: B within 12 cells of A, C and D more than 12 from A and each other.
ATTACK_A, ATTACK_B, ATTACK_C, ATTACK_D = (33, 44), (38, 50), (16, 52), (12, 36)
# The flame tower of the Soviet base of these tests, the eighth building `soviet_base` lists.
FLAME_TOWER_ID, FLAME_TOWER_CELL = 108, (27, 46)


def threat_only_coil_agent(*more_managers, tuning: Tuning = COILS_ON_THREAT_ONLY):
    """An upkeep agent, with the managers given before it, whose Tesla coils answer vehicle attacks only, with an
    income estimate that passes the investment test."""
    agent = new_agent(*more_managers, UpkeepManager(), plan_selector=OpeningThen(HOLD), tuning=tuning)
    agent.ctx.income = Earning(120, 1500)
    return agent


def flame_tower_hit_by(enemy_type: str) -> dict:
    """The Soviet base with its flame tower reported under attack, one enemy unit of a type in sight 8 cells off."""
    hit = situations.event(30, "under_attack", id=FLAME_TOWER_ID, type="ftur", x=27, y=46, hp=80)
    attacker = {"id": 950, "type": enemy_type, "x": 35, "y": 46, "hp": 100}
    return soviet_base(visible_enemies=[attacker], events=[hit])


@pytest.mark.parametrize("tuning, ordered", [(COILS_ON_THREAT_ONLY, []), (TWO_COILS, ["tsla"])], ids=["on", "off"])
def test_on_threat_only_a_soviet_base_with_a_war_factory_and_no_vehicle_attack_orders_no_coil(tuning, ordered):
    answers = [
        ask(threat_only_coil_agent(tuning=tuning), soviet_base(seconds=seconds, headroom=headroom))
        for seconds, headroom in ((COIL_SECONDS, 300), (COIL_SECONDS, 99), (600, 300))
    ]

    assert [defenses(orders) for orders in answers] == [ordered, [], ordered]
    assert [builds(orders) for orders in answers] == [[], [] if tuning is COILS_ON_THREAT_ONLY else ["powr"], []]


def test_on_threat_only_a_coil_is_ordered_for_a_vehicle_attack_and_placed_on_the_attack_s_cell():
    agent = threat_only_coil_agent()
    agent.ctx.vehicle_attacks.append(ATTACK_A)
    ordered = ask(agent, soviet_base())
    agent.ctx.vehicle_attacks.append(ATTACK_B)
    on_its_way = ask(agent, soviet_base(seconds=COIL_SECONDS + 1, production=[situations.job("Defense", "tsla")]))

    (place,) = places(ask(agent, soviet_base(seconds=COIL_SECONDS + 2, production=[ready("Defense", "tsla")])))

    assert (defenses(ordered), defenses(on_its_way)) == (["tsla"], [])
    assert place == {"cmd": "place", "item": "tsla", "at": list(ATTACK_A)}


def test_on_threat_only_an_attack_within_12_cells_of_a_coil_orders_none_one_farther_a_second_and_a_third_side_none():
    first_coil = ("tsla", *ATTACK_A)
    near_a = threat_only_coil_agent()
    near_a.ctx.vehicle_attacks.extend([ATTACK_A, ATTACK_B])
    other_side = threat_only_coil_agent()
    other_side.ctx.vehicle_attacks.extend([ATTACK_A, ATTACK_B, ATTACK_C])
    third_side = threat_only_coil_agent()
    third_side.ctx.vehicle_attacks.extend([ATTACK_A, ATTACK_B, ATTACK_C, ATTACK_D])
    three_kept = threat_only_coil_agent(
        tuning=tuning_off(upkeep=dataclasses.replace(COILS_ON_THREAT_ONLY.upkeep, soviet_tesla_coils=3))
    )
    three_kept.ctx.vehicle_attacks.extend([ATTACK_A, ATTACK_B, ATTACK_C, ATTACK_D])
    both_coils = (first_coil, ("tsla", *ATTACK_C))

    answers = [
        ask(near_a, soviet_base(first_coil)),
        ask(other_side, soviet_base(first_coil)),
        ask(third_side, soviet_base(*both_coils)),
        ask(three_kept, soviet_base(*both_coils)),
    ]

    assert [defenses(orders) for orders in answers] == [[], ["tsla"], [], ["tsla"]]


def test_on_threat_only_once_the_coil_stands_the_next_attack_s_coil_is_placed_on_that_attack_s_cell():
    agent = threat_only_coil_agent()
    agent.ctx.vehicle_attacks.extend([ATTACK_A, ATTACK_B])
    standing = ask(agent, soviet_base(("tsla", *ATTACK_A)))

    agent.ctx.vehicle_attacks.append(ATTACK_C)
    ordered = ask(agent, soviet_base(("tsla", *ATTACK_A), seconds=COIL_SECONDS + 1))
    ready_coil = [ready("Defense", "tsla")]
    (place,) = places(ask(agent, soviet_base(("tsla", *ATTACK_A), seconds=COIL_SECONDS + 2, production=ready_coil)))

    assert (defenses(standing), defenses(ordered)) == ([], ["tsla"])
    assert place["at"] == list(ATTACK_C)


@pytest.mark.parametrize("attacker, ordered", [("jeep", ["tsla"]), ("e1", [])], ids=["a jeep", "a rifle"])
def test_on_threat_only_a_vehicle_led_attack_orders_a_coil_while_it_goes_on_and_an_infantry_attack_none(
    attacker, ordered
):
    agent = threat_only_coil_agent(BaseDefenseManager())

    orders = ask(agent, flame_tower_hit_by(attacker))

    assert agent.ctx.threat_active and agent.ctx.threat_level == MINOR_THREAT
    assert agent.ctx.vehicle_attacks == ([FLAME_TOWER_CELL] if ordered else [])
    assert defenses(orders) == ordered


@pytest.mark.parametrize("faction", ["russia", "england"])
def test_on_threat_only_in_a_fake_match_no_coil_is_ordered_without_a_vehicle_attack(faction):
    tuning = tuning_off(upkeep=dataclasses.replace(upkeep_off(), defenses_on_threat_only=True))
    recording = situations.Recording(own_agent(tuning=tuning))

    played = FakeRedAlert(faction, ENEMY_OF[faction]).run(
        recording, STATES_PER_SECOND * TESLA_MATCH_SECONDS, keep_states=True
    )

    ordered = [order for orders in recording.orders_at.values() for order in builds_of(orders, "tsla")]
    assert recording.agent.ctx.vehicle_attacks == [] and ordered == []
    assert "weap" in {building["type"] for building in played.states[-1]["observation"]["buildings"]}


# -- the defense a preserved lead adds ----------------------------------------------------------------


def seen_army(tanks: int, rifles: int) -> list[dict]:
    """An enemy army in sight, far from the base: so many light tanks, at 700 each, and rifles, at 100 each."""
    types = ["1tnk"] * tanks + ["e1"] * rifles
    return [
        {"id": 900 + number, "type": unit_type, "x": 74, "y": 30 + number % 30, "hp": 100}
        for number, unit_type in enumerate(types)
    ]


# 2,100 of 3,500 in vehicles, and 2,100 of 7,000.
VEHICLES_60 = seen_army(3, 14)
VEHICLES_30 = seen_army(3, 49)


# The defense against armor switched on for a preserved lead, in a base that keeps no Tesla coil of its own, and off.
ARMOR_PRESERVED = tuning_off(upkeep=dataclasses.replace(NO_COILS.upkeep, preserve_anti_armor=True))
ARMOR_NOT_PRESERVED = tuning_off(upkeep=dataclasses.replace(NO_COILS.upkeep, preserve_anti_armor=False))


def preserving_agent(plan: str = PRESERVE, tuning: Tuning = ARMOR_PRESERVED):
    """An upkeep agent under a plan once a war factory has stood, PRESERVE unless told otherwise, with the defense
    against armor switched on for a preserved lead unless other thresholds are given: a coil it orders is the
    preserved lead's."""
    return upkeep_agent(plan_selector=OpeningThen(plan), tuning=tuning)


def preserved(
    faction: str,
    *buildings: tuple,
    army: list[dict] = (),
    seconds: int = COIL_SECONDS,
    cash: int = 1500,
    headroom: int = 300,
) -> dict:
    """A faction's base past its opening with its war factory and its defense against infantry, the buildings given
    besides, four harvesters, so much power over and an enemy army in sight."""
    economy = powered(400, 400 - headroom, cash=cash, harvesters=4)
    return base_state(
        faction,
        *HOLDING[faction],
        *buildings,
        units=harvesters(4),
        tick=seconds * 25,
        economy=economy,
        withheld=("fix",),
        visible_enemies=list(army),
    )


@pytest.mark.parametrize(
    "faction, army, defense",
    [
        ("russia", VEHICLES_60, "tsla"),
        ("england", VEHICLES_60, "gun"),
        ("russia", VEHICLES_30, "ftur"),
        ("england", VEHICLES_30, "pbox"),
        ("russia", [], "ftur"),
    ],
    ids=["soviet, 60% vehicles", "allied, 60% vehicles", "soviet, 30% vehicles", "allied, 30% vehicles", "none seen"],
)
def test_preserve_against_an_army_of_vehicles_the_defense_is_the_anti_armor_one_and_else_the_anti_infantry_one(
    faction, army, defense
):
    orders = ask(preserving_agent(), preserved(faction, army=army))

    assert defenses(orders) == [defense]


@pytest.mark.parametrize("army", [VEHICLES_60, VEHICLES_30, []], ids=["60% vehicles", "30% vehicles", "none seen"])
@pytest.mark.parametrize("faction, defense", [("russia", "ftur"), ("england", "pbox")])
def test_preserve_with_the_switch_off_the_defense_is_the_anti_infantry_one_whatever_the_enemy_army(
    faction, defense, army
):
    orders = ask(preserving_agent(tuning=ARMOR_NOT_PRESERVED), preserved(faction, army=army))

    assert defenses(orders) == [defense]


@pytest.mark.parametrize(
    "faction, army, defense",
    [("russia", VEHICLES_60, "tsla"), ("england", VEHICLES_60, "gun"), ("russia", VEHICLES_30, "ftur")],
    ids=["soviet, 60% vehicles", "allied, 60% vehicles", "soviet, 30% vehicles"],
)
def test_preserve_with_the_tracked_file_against_an_army_of_vehicles_the_defense_is_the_anti_armor_one(
    faction, army, defense
):
    tracked = switched_off(Tuning.from_file(situations.REPO_ROOT))

    orders = ask(preserving_agent(tuning=tracked), preserved(faction, army=army))

    assert tracked.upkeep.preserve_anti_armor is True
    assert defenses(orders) == [defense]


def test_preserve_with_the_switch_off_a_vehicle_army_gets_the_anti_infantry_defense_on_the_power_left_over():
    # 99 over leaves 19 below the target of 20 after a coil, but carries the flame tower's 20 without going short.
    agent = preserving_agent(tuning=ARMOR_NOT_PRESERVED)

    orders = ask(agent, preserved("russia", army=VEHICLES_60, headroom=99))

    assert (builds(orders), defenses(orders)) == ([], ["ftur"])


def test_preserve_the_anti_armor_defense_is_built_only_under_preserve():
    assert defenses(ask(preserving_agent(HOLD), preserved("england", army=VEHICLES_60))) == []


def test_preserve_the_soviet_lead_adds_a_third_coil_besides_the_two_upkeep_keeps():
    two_coils = tuning_off(upkeep=dataclasses.replace(ARMOR_PRESERVED.upkeep, soviet_tesla_coils=2))
    agent = upkeep_agent(plan_selector=OpeningThen(PRESERVE), tuning=two_coils)

    orders = ask(agent, preserved("russia", FIRST_COIL, SECOND_COIL, army=VEHICLES_60))

    # Two coils standing: the target is 160, and 300 over less the coil's 80 leaves 220.
    assert defenses(orders) == ["tsla"] and power_target(agent.world, agent.ctx) == 160


def test_preserve_one_anti_armor_defense_a_minute():
    agent = preserving_agent()
    answers = [
        ask(agent, preserved("russia", *standing, army=VEHICLES_60, seconds=seconds))
        for seconds, standing in ((300, ()), (330, (FIRST_COIL,)), (359, (FIRST_COIL,)), (360, (FIRST_COIL,)))
    ]

    assert [defenses(orders) for orders in answers] == [["tsla"], [], [], ["tsla"]]


def test_preserve_a_coil_that_would_leave_less_than_the_power_target_waits_for_a_power_plant_and_is_still_due():
    # At 300 s the target is 20 and the coil drains 80: 99 over leaves 19.
    agent = preserving_agent()
    short = ask(agent, preserved("russia", army=VEHICLES_60, headroom=99))

    powered_again = ask(agent, preserved("russia", ("powr", 30, 50), army=VEHICLES_60, seconds=301, headroom=199))

    assert (builds(short), defenses(short)) == (["powr"], [])
    assert defenses(powered_again) == ["tsla"]


@pytest.mark.parametrize("cash, built", [(599, []), (600, ["tsla"])])
def test_preserve_the_anti_armor_defense_waits_for_600_of_money(cash, built):
    assert defenses(ask(preserving_agent(), preserved("russia", army=VEHICLES_60, cash=cash))) == built


# The defense against armor switched on for a preserved lead, with upkeep's defenses bought only against a threat.
ARMOR_PRESERVED_ON_THREAT_ONLY = tuning_off(
    upkeep=dataclasses.replace(ARMOR_PRESERVED.upkeep, defenses_on_threat_only=True)
)


@pytest.mark.parametrize(
    "faction, army, defense",
    [("russia", VEHICLES_60, "tsla"), ("england", VEHICLES_60, "gun"), ("russia", VEHICLES_30, "ftur")],
    ids=["soviet, 60% vehicles", "allied, 60% vehicles", "soviet, 30% vehicles"],
)
def test_on_threat_only_under_preserve_no_defense_is_added_without_a_threat_and_one_as_today_under_attack(
    faction, army, defense
):
    quiet = preserving_agent(tuning=ARMOR_PRESERVED_ON_THREAT_ONLY)
    under_attack = preserving_agent(tuning=ARMOR_PRESERVED_ON_THREAT_ONLY)
    under_attack.ctx.threat_active = True
    a_minute_later = [ask(quiet, preserved(faction, army=army, seconds=seconds)) for seconds in (300, 360, 420)]

    orders = ask(under_attack, preserved(faction, army=army))

    assert [defenses(batch) for batch in a_minute_later] == [[], [], []]
    assert defenses(orders) == [defense]


def last_minutes_defenses(faction: str, tuning: Tuning) -> list[str]:
    """The defenses an upkeep agent with these thresholds orders in a quiet base with 100 seconds of the match left and
    2,000 of money."""
    agent = upkeep_agent(tuning=tuning)
    ask(agent, finished(faction, tick=27000, cash=0))
    state = finished(faction, tick=27500, cash=2000)
    state["time"] = {"max_minutes": 20, "game_seconds": 1100, "remaining_seconds": 100}
    return defenses(ask(agent, state))


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_on_threat_only_the_end_of_match_spending_buys_the_same_defense_as_without_the_switch(faction):
    switched_on = last_minutes_defenses(faction, ON_THREAT_ONLY)

    assert switched_on == last_minutes_defenses(faction, TWO_REFINERIES_ONLY) and len(switched_on) == 1


def builds_of(orders: list[dict], item: str) -> list[dict]:
    """The build orders of a batch for one item."""
    return [order for order in with_command(orders, "build") if order["item"] == item]


def _is_short_of_power(economy: dict) -> bool:
    return economy["power_drained"] > economy["power_provided"]


# -- the early service depot of a main force built around tanks ----------------------------------------------

TANK_FORCE = mix_off(main_force=TANKS)
# The upkeep agent's thresholds, two refineries only, with the main force built around tanks; and the switches-off
# ones, the third refinery included, with it.
TANK_DEPOT_TUNING = dataclasses.replace(TWO_REFINERIES_ONLY, mix=TANK_FORCE)
TANKS_WITH_THIRD_REFINERY = tuning_off(mix=TANK_FORCE)
# The service depot's price and the production reserve: the least money the early depot is ordered on.
DEPOT_AND_RESERVE = 1200 + 300


def tank_agent(tuning: Tuning = TANK_DEPOT_TUNING, plan: str = HOLD):
    """An upkeep agent whose main force is built around tanks, under OPENING until a war factory has stood and `plan`
    after."""
    return new_agent(UpkeepManager(), plan_selector=OpeningThen(plan), tuning=tuning)


def test_depot_with_tanks_1500_above_the_reserve_and_2_harvesters_the_depot_goes_before_a_third_refinery():
    # 1,000 cash and 800 ore: 1,500 above the reserve, and the two refineries, 80% full, call for a third.
    state = grown(cash=1000, ore=800, capacity=1000, count=2, withheld=())
    agent = tank_agent(TANKS_WITH_THIRD_REFINERY)

    first = ask(agent, state)
    # The depot stands ten seconds on, its order long answered.
    depot_standing = ask(agent, grown(("fix", 26, 54), cash=1000, ore=800, capacity=1000, count=2, seconds=310))

    assert (builds(first), builds(depot_standing)) == (["fix"], ["proc"])


def test_depot_with_mixed_the_same_state_orders_no_depot_but_the_third_refinery():
    # Two harvesters are fewer than tech_min_harvesters: the mixed main force's depot waits.
    state = grown(cash=1000, ore=800, capacity=1000, count=2, withheld=())

    assert builds(ask(growing_agent(), state)) == ["proc"]


@pytest.mark.parametrize(
    "money", [DEPOT_AND_RESERVE, DEPOT_AND_RESERVE - 1, 300, 0], ids=["1,500", "1,499", "300", "0"]
)
def test_army_build_the_rushed_depot_is_ordered_whatever_the_money_tech_min_cash_and_harvesters(money):
    # One harvester, and money below tech_min_cash of 1,500 but in the first case.
    state = holding(cash=money, units=harvesters(1))

    assert builds(ask(tank_agent(), state)) == ["fix"]


def test_army_build_the_depot_is_the_first_building_ordered_once_the_war_factory_stands():
    agent = tank_agent()
    building = [situations.job("Building", "weap")]
    without = [spec for spec in HOLDING["england"] if spec[0] != "weap"]

    on_its_way = ask(agent, base_state("england", *without, units=harvesters(4), cash=800, production=building))
    stood = ask(agent, holding(cash=800, tick=1100))

    assert (builds(on_its_way), builds(stood)) == ([], ["fix"])


@pytest.mark.parametrize(
    "cash, level, fed",
    # 1,300 pays for a medium tank above the reserve, not for the depot and the reserve.
    [
        (1300, None, False),
        (DEPOT_AND_RESERVE - 1, None, False),
        (DEPOT_AND_RESERVE, None, True),
        (1300, MAJOR_THREAT, True),
    ],
    ids=["1,300", "1,499", "1,500", "1,300 under a major threat"],
)
def test_army_build_until_the_depot_stands_the_unit_queues_save_for_it_but_under_a_major_threat(cash, level, fed):
    agent = tank_agent()
    if level is not None:
        agent.ctx.threat_active, agent.ctx.threat_level = True, level
    in_its_queue = [situations.job("Building", "fix")]

    orders = ask(agent, holding(cash=cash, units=[*harvesters(4), *rockets(4)], production=in_its_queue))

    assert bool(unit_trains(orders)) == fed


def test_army_build_once_the_depot_stands_the_unit_queues_are_fed_on_any_money():
    orders = ask(tank_agent(), base_state("england", *WITH_DEPOT, units=harvesters(4), cash=1300))

    assert unit_trains(orders) != []


def test_army_build_a_due_claim_refinery_keeps_its_place_ahead_of_the_rushed_depot(monkeypatch):
    monkeypatch.setattr("ucla_agent.games.redalert.upkeep._is_claim_due", lambda ctx: True)

    orders = ask(tank_agent(), holding(cash=1000))

    assert "fix" not in builds(orders)


def test_depot_with_tanks_the_depot_goes_before_one_more_barracks_and_with_mixed_the_barracks_goes_first():
    # At 240 s, with 110 a second coming in, a second barracks (500) is due.
    state = holding(cash=1500, economy=powered(500, 100, cash=1500, harvesters=4), tick=240 * 25)

    tanks = ask(wanting_producers(tank_agent(), 110), state)
    mixed = ask(wanting_producers(upkeep_agent(), 110), state)

    assert (builds(tanks), builds(mixed)) == (["fix"], ["tent"])


def test_depot_with_tanks_the_depot_goes_ahead_of_the_third_refinery_the_units_wait_for():
    # From 360 s the units wait for the third refinery, which the mixed main force's depot waits for as well.
    saving = grown(cash=1500, seconds=400, withheld=())

    assert builds(ask(tank_agent(TANKS_WITH_THIRD_REFINERY), saving)) == ["fix"]
    assert builds(ask(growing_agent(), saving)) == ["proc"]


@pytest.mark.parametrize(
    "level, queued", [(MAJOR_THREAT, []), (MINOR_THREAT, ["fix"])], ids=["a major threat", "a minor threat"]
)
def test_depot_with_tanks_no_depot_is_started_under_a_major_threat(level, queued):
    # Four harvesters and 1,500: the mixed main force's rule would order the depot under any threat.
    agent = tank_agent()
    agent.ctx.threat_active, agent.ctx.threat_level = True, level

    assert builds(ask(agent, holding(cash=1500))) == queued


@pytest.mark.parametrize(
    "drained, queued",
    [(150, ["fix"]), (151, ["powr"])],
    ids=["the target of 20 left over", "one short"],
)
def test_depot_with_tanks_a_depot_that_would_take_the_headroom_below_the_target_gives_way_to_a_power_plant(
    drained, queued
):
    state = holding(economy=powered(200, drained, cash=1500, harvesters=2))

    assert builds(ask(tank_agent(), state)) == queued


def test_depot_with_tanks_the_depot_is_ordered_once():
    agent = tank_agent()

    first = ask(agent, holding(cash=1500, tick=3000))
    second = ask(agent, holding(cash=1500, tick=3005))
    in_its_queue = ask(agent, holding(cash=1500, tick=3010, production=[situations.job("Building", "fix")]))

    assert [builds(orders) for orders in (first, second, in_its_queue)] == [["fix"], [], []]
    assert agent.ledger.duplicate_production_orders == 0


@pytest.mark.parametrize(
    "money, queued",
    [(1600, []), (1700, ["proc"])],
    ids=["covers the depot, not the refinery", "covers the refinery and the reserve"],
)
def test_depot_with_tanks_a_lost_refinery_saved_for_goes_before_the_depot_and_the_unit_queues_get_nothing(
    money, queued
):
    agent = tank_agent()
    ask(agent, finished("england", tick=5000, cash=0))

    orders = ask(agent, finished("england", "proc", cash=money, withheld=()))

    assert (builds(orders), unit_trains(orders)) == (queued, [])


def test_depot_with_tanks_the_vehicle_queue_trains_light_tanks_before_the_depot_and_medium_tanks_from_it():
    before = ask(tank_agent(), holding(cash=5000))
    after = ask(tank_agent(), base_state("england", *WITH_DEPOT, units=harvesters(4), cash=5000))

    assert (trained(before, "Vehicle"), trained(after, "Vehicle")) == ([("1tnk", 1)], [("2tnk", 1)])
    assert trained(before, "Infantry") == [("e1", 3)]


def test_depot_with_tanks_standing_and_the_raid_quota_short_the_raid_s_light_tank_is_trained_not_a_medium_tank():
    agent = tank_agent(plan=RAID)

    orders = ask(agent, base_state("england", *WITH_DEPOT, units=harvesters(4), cash=5000))

    assert trained(orders, "Vehicle") == [("1tnk", 1)]


# The tank force with main's vehicles waiting for the service depot, as it ships.
DEPOT_FIRST_TUNING = dataclasses.replace(
    TANK_DEPOT_TUNING, composition=dataclasses.replace(TANK_DEPOT_TUNING.composition, main_vehicles_need_depot=True)
)


def test_core_tank_mix_before_the_depot_the_allies_queue_no_main_vehicle_and_the_depot_is_ordered():
    orders = ask(tank_agent(DEPOT_FIRST_TUNING), holding(cash=5000))

    assert trained(orders, "Vehicle") == [] and "fix" in builds(orders)


def test_core_tank_mix_before_the_depot_the_raid_quota_still_gets_its_light_tank():
    orders = ask(tank_agent(DEPOT_FIRST_TUNING, plan=RAID), holding(cash=5000))

    assert trained(orders, "Vehicle") == [("1tnk", 1)]


def test_core_tank_mix_before_the_depot_the_soviets_queue_no_flak_truck_for_main():
    orders = ask(tank_agent(DEPOT_FIRST_TUNING), holding("russia", cash=5000))

    assert trained(orders, "Vehicle") == []


def test_core_tank_mix_from_the_depot_on_main_s_vehicles_are_trained_again():
    orders = ask(tank_agent(DEPOT_FIRST_TUNING), base_state("england", *WITH_DEPOT, units=harvesters(4), cash=5000))

    assert trained(orders, "Vehicle") == [("2tnk", 1)]


# -- the core tank mix: the Soviet tech center ----------------------------------------------------------------------

# A Soviet base with two war factories, a radar and a service depot, and power to spare for a tech center.
SOVIET_TECH_BASE = (
    YARD,
    POWER,
    ("barr", 22, 44),
    REFINERY,
    ("proc", 18, 39),
    ("powr", 26, 50),
    ("powr", 30, 50),
    ("powr", 34, 50),
    ("powr", 38, 50),
    WAR_FACTORY,
    ("weap", 30, 56),
    ("dome", 28, 44),
    ("fix", 26, 54),
)
ONE_WAR_FACTORY = tuple(spec for spec in SOVIET_TECH_BASE if spec != ("weap", 30, 56))
WITH_A_TECH_CENTER = (*SOVIET_TECH_BASE, ("stek", 34, 44))
# The tech center switched on, and two war factories at the most, so that no bank rule takes the Building queue for
# a third.
SOVIET_TECH_TUNING = dataclasses.replace(
    TWO_REFINERIES_ONLY, producers=producers_off(max_war_factories=2, soviet_tech_center=True)
)
# A state every 5 seconds from the tenth minute on, the last 30 seconds after the first.
TECH_SECONDS = range(600, 631, 5)


def tech_center_builds(cash: list[int], *buildings, tuning: Tuning = SOVIET_TECH_TUNING) -> list[list[str]]:
    """What a Soviet upkeep agent builds in each state of the run, one every 5 seconds from 600 s, with the cash given
    and the buildings of SOVIET_TECH_BASE unless others are given."""
    agent = new_agent(UpkeepManager(), plan_selector=OpeningThen(HOLD), tuning=tuning)
    base = buildings or SOVIET_TECH_BASE
    return [
        builds(ask(agent, base_state("russia", *base, units=harvesters(4), cash=money, tick=seconds * 25)))
        for seconds, money in zip(TECH_SECONDS, cash)
    ]


@pytest.mark.parametrize("money", [4500, 4000])
def test_core_tank_mix_soviets_with_2_war_factories_and_4000_or_more_for_30_s_order_one_tech_center(money):
    built = tech_center_builds([money] * len(TECH_SECONDS))

    assert built == [[]] * (len(TECH_SECONDS) - 1) + [["stek"]]


def test_core_tank_mix_the_tech_center_order_is_an_economy_event_of_reason_soviet_tech(monkeypatch):
    logged: list[dict] = []
    monkeypatch.setattr(records, "log", lambda kind, data: logged.append(data) if kind == "economy" else None)

    tech_center_builds([4500] * len(TECH_SECONDS))

    assert logged == [{"tick": 630 * 25, "reason": "soviet_tech", "item": "stek", "money": 4500}]


@pytest.mark.parametrize(
    "cash, buildings",
    [
        ([3999] * len(TECH_SECONDS), SOVIET_TECH_BASE),
        ([4500] * 3 + [3999] + [4500] * 3, SOVIET_TECH_BASE),
        ([4500] * len(TECH_SECONDS), ONE_WAR_FACTORY),
        ([4500] * len(TECH_SECONDS), WITH_A_TECH_CENTER),
    ],
    ids=["3,999", "a dip under 4,000", "one war factory", "a tech center standing"],
)
def test_core_tank_mix_no_tech_center_is_ordered_short_of_its_gates(cash, buildings):
    assert all("stek" not in built for built in tech_center_builds(cash, *buildings))


def test_core_tank_mix_with_soviet_tech_center_off_no_tech_center_is_ordered():
    tuning = dataclasses.replace(
        SOVIET_TECH_TUNING, producers=dataclasses.replace(SOVIET_TECH_TUNING.producers, soviet_tech_center=False)
    )

    assert tech_center_builds([4500] * len(TECH_SECONDS), tuning=tuning) == [[]] * len(TECH_SECONDS)


def test_core_tank_mix_once_the_tech_center_stands_the_soviet_tank_force_trains_the_mammoth_it_is_short_of():
    tuning = dataclasses.replace(SOVIET_TECH_TUNING, mix=Tuning().mix)
    agent = new_agent(UpkeepManager(), plan_selector=OpeningThen(HOLD), tuning=tuning)
    army = [*harvesters(4), *soldiers("3tnk", 2), *soldiers("v2rl", 2, first_id=410)]

    orders = ask(agent, base_state("russia", *WITH_A_TECH_CENTER, units=army, cash=5000, tick=15000))

    assert trained(orders, "Vehicle") == [("4tnk", 1)]


# -- the cap of two refineries to a field ---------------------------------------------------------------------------

# The home field north of the yard, which the two refineries of TWO_REFINERIES serve; a field in the lane east of the
# yard that a refinery of the base can stand against, and one too far for any.
HOME_FIELD = OrePatch(center=(22, 40), cells=frozenset((x, y) for x in (21, 22, 23) for y in (39, 40, 41)))
REACHABLE_LANE_FIELD = OrePatch(center=(33, 47), cells=frozenset((x, y) for x in (32, 33, 34) for y in (46, 47, 48)))
FAR_LANE_FIELD = OrePatch(center=(43, 47), cells=frozenset((x, y) for x in (42, 43, 44) for y in (46, 47, 48)))
FIELD_CAP = tuning_off(
    upkeep=dataclasses.replace(upkeep_off(), refinery_field_cap=True),
    placement=dataclasses.replace(placement_off(), creep_next_patch=True),
)
NO_CAP = dataclasses.replace(FIELD_CAP, upkeep=upkeep_off())


def field_terrain(*patches: OrePatch) -> Terrain:
    return Terrain(patches, frozenset().union(*(patch.cells for patch in patches)))


def capped_agent(tuning: Tuning, terrain: Terrain):
    """An agent with the upkeep manager alone under `tuning`, having seen a first state of the grown base without
    money, on a map of this terrain."""
    agent = upkeep_agent(tuning=tuning)
    ask(agent, grown(cash=0, seconds=290))
    agent.ctx.terrain = terrain
    return agent


def test_field_cap_with_the_home_field_full_and_a_reachable_next_field_the_third_refinery_is_ordered_for_it(
    match_record,
):
    agent = capped_agent(FIELD_CAP, field_terrain(HOME_FIELD, REACHABLE_LANE_FIELD))

    orders = ask(agent, grown(cash=2000, seconds=300))
    placed = places(ask(agent, grown(cash=2000, seconds=302, production=[ready("Building", "proc")])))

    (event,) = match_record("refinery_field")
    assert builds(orders) == ["proc"] and agent.ctx.creep_target.patch == REACHABLE_LANE_FIELD
    assert event["cap_held"] is False and event["field_center"] == [33, 47] and event["served_by"] == 0
    assert placement.ore_distance(box_at("proc", tuple(placed[0]["at"])), REACHABLE_LANE_FIELD) <= 2
    assert event["cell"] == placed[0]["at"]


def test_field_cap_with_no_reachable_field_under_the_cap_no_third_refinery_is_ordered_and_the_hold_is_recorded(
    match_record,
):
    agent = capped_agent(FIELD_CAP, field_terrain(HOME_FIELD, FAR_LANE_FIELD))

    held = [builds(ask(agent, grown(cash=2000, seconds=seconds))) for seconds in (300, 301, 302)]

    (event,) = match_record("refinery_field")
    assert held == [[], [], []]
    # The nearest place whose field is under the cap serves the far field, and stands out of reach of its ore.
    assert (event["cap_held"], event["field_center"], event["served_by"]) == (True, [43, 47], 0)
    assert event["ore_distance_cells"] > 2


def test_field_cap_while_the_third_refinery_is_held_the_unit_queues_do_not_save_for_it():
    capped = capped_agent(FIELD_CAP, field_terrain(HOME_FIELD, FAR_LANE_FIELD))
    saving = capped_agent(NO_CAP, field_terrain(HOME_FIELD, FAR_LANE_FIELD))
    for agent in (capped, saving):
        ask(agent, grown(cash=1500, seconds=400))

    assert unit_trains(ask(capped, grown(cash=1500, seconds=402))) != []
    assert unit_trains(ask(saving, grown(cash=1500, seconds=402))) == []


def test_field_cap_a_fourth_refinery_goes_to_the_next_field_while_it_has_fewer_than_2(match_record):
    third = ("proc", 29, 44)
    agent = capped_agent(FIELD_CAP, field_terrain(HOME_FIELD, REACHABLE_LANE_FIELD))

    orders = ask(agent, grown(third, cash=2000, count=6, seconds=500))
    placed = places(ask(agent, grown(third, cash=2000, count=6, seconds=502, production=[ready("Building", "proc")])))

    (event,) = match_record("refinery_field")
    assert builds(orders) == ["proc"]
    assert (event["cap_held"], event["field_center"], event["served_by"]) == (False, [33, 47], 1)
    assert placement.ore_distance(box_at("proc", tuple(placed[0]["at"])), REACHABLE_LANE_FIELD) <= 2


def test_field_cap_with_the_switch_off_a_third_refinery_for_the_home_field_is_ordered_as_today(match_record):
    agent = capped_agent(NO_CAP, field_terrain(HOME_FIELD, FAR_LANE_FIELD))

    assert builds(ask(agent, grown(cash=2000, seconds=300))) == ["proc"]
    assert match_record("refinery_field") == []


@pytest.fixture(scope="module")
def capped_match():
    """A whole fake match on a map with a field in the lane, with the creep to the next field and the cap on."""
    return play_whole_match(FIELD_CAP, game_config=with_ore(FakeRedAlert().game_config, REACHABLE_LANE_FIELD.cells))


def test_field_cap_a_whole_fake_match_runs_without_an_error_and_no_field_has_more_than_2_refineries(capped_match):
    terrain = capped_match.agent.ctx.terrain
    refineries = [b for b in capped_match.agent.world.buildings.values() if b.type == "proc"]
    served = Counter(placement.patch_of(refinery, terrain) for refinery in refineries)

    assert len(capped_match.compute_ms) == WHOLE_MATCH_STEPS and capped_match.data_of("error") == []
    assert capped_match.compute_p95_ms <= COMPUTE_P95_MS
    assert len(refineries) >= 3 and max(served.values()) <= 2
    assert {event["cap_held"] for event in capped_match.data_of("refinery_field")} >= {False}


# -- harvesters to fresh ore ----------------------------------------------------------------------------------------

FRESH_ORE = dataclasses.replace(
    TWO_REFINERIES_ONLY, upkeep=dataclasses.replace(TWO_REFINERIES_ONLY.upkeep, harvesters_to_fresh_ore=True)
)
# A field east of the base with no defense near it, one north-west of it, and one south-east with an enemy gun south
# of it, 9 cells from its nearest cell.
EAST_ORE = OrePatch(center=(33, 47), cells=frozenset((x, y) for x in (32, 33, 34) for y in (46, 47, 48)))
NORTH_WEST_ORE = OrePatch(center=(8, 30), cells=frozenset((x, y) for x in (7, 8, 9) for y in (29, 30, 31)))
GUARDED_ORE = OrePatch(center=(33, 52), cells=frozenset((x, y) for x in (32, 33, 34) for y in (51, 52, 53)))
GUARDED_ORE_GUN = {"id": 900, "type": "gun", "x": 33, "y": 62, "hp": 100}
# Where the harvesters by the refinery (24, 39), whose middle is (25, 41), stand idle: within 8 cells of it, off the ore.
BY_THE_REFINERY = (25, 45)
FAR_FROM_REFINERIES = (60, 20)


def fresh_agent(terrain: Terrain, *managers, tuning: Tuning = FRESH_ORE):
    """An agent with the upkeep manager, and the managers given before it, on a map of this terrain."""
    agent = new_agent(*managers, UpkeepManager(), plan_selector=OpeningThen(HOLD), tuning=tuning)
    ask(agent, base_state("england", *TWO_REFINERIES, tick=3990))
    agent.ctx.terrain = terrain
    return agent


def fresh_ore_terrain(*patches: OrePatch) -> Terrain:
    return Terrain((HOME_FIELD, *patches), frozenset().union(HOME_FIELD.cells, *(patch.cells for patch in patches)))


def idle_by_the_refinery(idle: Iterable[int], busy: Iterable[int] = (), on_ore: Iterable[int] = ()) -> list[dict]:
    """Harvesters standing idle by the refinery, at work far from it, and at work on the home field's ore."""
    return [
        *(situations.unit(number, "harv", *BY_THE_REFINERY) for number in idle),
        *(situations.unit(number, "harv", *FAR_FROM_REFINERIES, idle=False) for number in busy),
        *(situations.unit(number, "harv", 22, 40, idle=False) for number in on_ore),
    ]


def fresh_run(agent, ticks, units, **observation) -> list[list[dict]]:
    """The harvest orders the agent answers a base state at each tick with, the harvesters given."""
    return [
        harvest_orders(ask(agent, base_state("england", *TWO_REFINERIES, tick=tick, units=units, **observation)))
        for tick in ticks
    ]


def test_fresh_ore_two_re_orders_by_a_refinery_mark_its_field_dry_and_the_next_cycle_sends_them_to_fresh_ore():
    agent = fresh_agent(fresh_ore_terrain(EAST_ORE, NORTH_WEST_ORE))

    sent = fresh_run(agent, [4000, 4005, 4010, 4015, 4020], idle_by_the_refinery((300, 301), busy=(302, 303)))

    to = EAST_ORE.nearest_cell(BY_THE_REFINERY)
    assert sent[:3] == [[], [], [{"cmd": "harvest", "units": [300, 301]}]]
    assert sent[3] == [{"cmd": "harvest", "units": [300, 301], "to": list(to)}] and sent[4] == []
    assert cells.distance(EAST_ORE.center, (22, 40)) < cells.distance(NORTH_WEST_ORE.center, (22, 40))


def test_fresh_ore_one_re_order_in_60_s_marks_no_field_dry():
    agent = fresh_agent(fresh_ore_terrain(EAST_ORE))

    sent = fresh_run(agent, [4000, 4005, 4010, 4015], idle_by_the_refinery((300,), busy=(301,)))

    assert sent == [[], [], [{"cmd": "harvest", "units": [300]}], []]


def test_fresh_ore_a_field_with_a_remembered_enemy_defense_within_10_cells_is_never_chosen():
    agent = fresh_agent(fresh_ore_terrain(GUARDED_ORE, NORTH_WEST_ORE))
    seen = {"visible_enemy_buildings": [GUARDED_ORE_GUN]}

    sent = fresh_run(agent, [4000, 4005, 4010, 4015], idle_by_the_refinery((300, 301)), **seen)

    assert cells.distance(GUARDED_ORE.center, (22, 40)) < cells.distance(NORTH_WEST_ORE.center, (22, 40))
    assert sent[3] == [
        {"cmd": "harvest", "units": [300, 301], "to": list(NORTH_WEST_ORE.nearest_cell(BY_THE_REFINERY))}
    ]


def test_fresh_ore_a_field_the_way_to_which_a_remembered_defense_covers_is_never_chosen():
    # The gun stands 11 cells from the east field, beyond the 10 kept clear, and covers the way to it from the home
    # field: its range of 6.5 and the margin of 2 reach the cells of the line 8 cells from it.
    agent = fresh_agent(fresh_ore_terrain(EAST_ORE, NORTH_WEST_ORE))
    gun = {"visible_enemy_buildings": [{"id": 902, "type": "gun", "x": 29, "y": 35, "hp": 100}]}

    sent = fresh_run(agent, [4000, 4005, 4010, 4015], idle_by_the_refinery((300, 301)), **gun)

    assert min(cells.distance((29, 35), cell) for cell in EAST_ORE.cells) > 10
    assert sent[3] == [
        {"cmd": "harvest", "units": [300, 301], "to": list(NORTH_WEST_ORE.nearest_cell(BY_THE_REFINERY))}
    ]


def test_fresh_ore_a_field_in_the_lane_toward_the_enemy_goes_before_a_nearer_one_outside_it():
    lane_ore = OrePatch(center=(42, 48), cells=frozenset((x, y) for x in (41, 42, 43) for y in (47, 48, 49)))
    agent = fresh_agent(fresh_ore_terrain(NORTH_WEST_ORE, lane_ore))

    sent = fresh_run(agent, [4000, 4005, 4010, 4015], idle_by_the_refinery((300, 301)))

    assert cells.distance((22, 40), lane_ore.nearest_cell((22, 40))) > cells.distance((22, 40), (9, 31))
    assert sent[3] == [{"cmd": "harvest", "units": [300, 301], "to": list(lane_ore.nearest_cell(BY_THE_REFINERY))}]


def test_fresh_ore_a_field_with_an_enemy_combat_unit_seen_within_12_cells_is_never_chosen():
    agent = fresh_agent(fresh_ore_terrain(EAST_ORE, NORTH_WEST_ORE))
    rifles = {"visible_enemies": [{"id": 901, "type": "e1", "x": 40, "y": 47, "hp": 100}]}

    sent = fresh_run(agent, [4000, 4005, 4010, 4015], idle_by_the_refinery((300, 301)), **rifles)

    assert sent[3] == [
        {"cmd": "harvest", "units": [300, 301], "to": list(NORTH_WEST_ORE.nearest_cell(BY_THE_REFINERY))}
    ]


def sent_to_fresh_ore(sent: list[list[dict]]) -> list[dict]:
    """The harvest orders of the cycles that send harvesters to a cell: those of the move to fresh ore."""
    return [order for orders in sent for order in orders if "to" in order]


def test_fresh_ore_two_harvesters_idle_by_their_refinery_under_a_threat_do_not_mark_the_field_dry():
    calm = fresh_agent(fresh_ore_terrain(EAST_ORE, NORTH_WEST_ORE))
    threatened = fresh_agent(fresh_ore_terrain(EAST_ORE, NORTH_WEST_ORE))
    threatened.ctx.threat_active = True
    harvesters = idle_by_the_refinery((300, 301), busy=(302, 303))

    sent_calm = fresh_run(calm, [4000, 4005, 4010, 4015, 4020], harvesters)
    sent_threatened = fresh_run(threatened, [4000, 4005, 4010, 4015, 4020], harvesters)

    assert sent_to_fresh_ore(sent_calm) and sent_to_fresh_ore(sent_threatened) == []
    assert {"cmd": "harvest", "units": [300, 301]} in sent_threatened[2]


def test_fresh_ore_harvesters_idle_with_an_enemy_combat_unit_within_12_cells_do_not_mark_the_field_dry():
    agent = fresh_agent(fresh_ore_terrain(EAST_ORE, NORTH_WEST_ORE))
    # 5 cells from the idle harvesters, and 15 from the east field: that field stays safe.
    rifle = {"visible_enemies": [{"id": 901, "type": "e1", "x": 25, "y": 50, "hp": 100}]}

    sent = fresh_run(agent, [4000, 4005, 4010, 4015, 4020], idle_by_the_refinery((300, 301)), **rifle)

    assert cells.distance(BY_THE_REFINERY, (25, 50)) == 5 and sent_to_fresh_ore(sent) == []


# A field whose center lies 10 cells from the enemy's start (74, 48), and a field farther from the home field.
ENEMY_START_ORE = OrePatch(center=(64, 48), cells=frozenset((x, y) for x in (63, 64, 65) for y in (47, 48, 49)))
FAR_SOUTH_ORE = OrePatch(center=(22, 85), cells=frozenset((x, y) for x in (21, 22, 23) for y in (84, 85, 86)))


def test_fresh_ore_a_field_10_cells_from_the_enemy_start_is_never_a_target():
    alone = fresh_agent(fresh_ore_terrain(ENEMY_START_ORE))
    with_south = fresh_agent(fresh_ore_terrain(ENEMY_START_ORE, FAR_SOUTH_ORE))
    harvesters = idle_by_the_refinery((300, 301))

    sent_alone = fresh_run(alone, [4000, 4005, 4010, 4015], harvesters)
    sent_south = fresh_run(with_south, [4000, 4005, 4010, 4015], harvesters)

    home = HOME_FIELD.center
    assert alone.world.enemy_base == (74, 48) and cells.distance(ENEMY_START_ORE.center, (74, 48)) == 10
    assert cells.distance(home, ENEMY_START_ORE.nearest_cell(home)) < cells.distance(
        home, FAR_SOUTH_ORE.nearest_cell(home)
    )
    assert sent_to_fresh_ore(sent_alone) == []
    assert sent_to_fresh_ore(sent_south) == [
        {"cmd": "harvest", "units": [300, 301], "to": list(FAR_SOUTH_ORE.nearest_cell(BY_THE_REFINERY))}
    ]


def test_fresh_ore_with_the_enemy_start_unknown_every_spawn_but_the_own_counts():
    agent = fresh_agent(fresh_ore_terrain(ENEMY_START_ORE, FAR_SOUTH_ORE, NORTH_WEST_ORE))
    agent.world.enemy_base, agent.world.spawns = None, ((22, 48), (74, 48), (12, 22))

    fresh = FreshOre()._fresh_fields(HOME_FIELD, agent.world, agent.ctx)

    # The north-west field lies 9.1 cells from the spawn (12, 22); the base's own spawn is (22, 48).
    assert ENEMY_START_ORE not in fresh and NORTH_WEST_ORE not in fresh and FAR_SOUTH_ORE in fresh
    agent.world.spawns = ()
    assert set(FreshOre()._fresh_fields(HOME_FIELD, agent.world, agent.ctx)) >= {ENEMY_START_ORE, NORTH_WEST_ORE}


def test_fresh_ore_at_most_4_harvesters_are_sent_to_one_field_and_the_others_wait():
    agent = fresh_agent(fresh_ore_terrain(EAST_ORE))

    sent = fresh_run(agent, [4000, 4005, 4010, 4015], idle_by_the_refinery(range(300, 306)))
    later = fresh_run(agent, [4020, 4270], idle_by_the_refinery((304, 305), busy=range(300, 304)))

    to = list(EAST_ORE.nearest_cell(BY_THE_REFINERY))
    assert sent[2] == [{"cmd": "harvest", "units": list(range(300, 306))}]
    assert sent[3] == [{"cmd": "harvest", "units": [300, 301, 302, 303], "to": to}]
    # The two left wait by the refinery: the idle rule does not send them back to the dry field.
    assert later == [[], []]


def test_fresh_ore_a_fleeing_harvester_gets_no_harvest_order():
    def flee(world, ctx):
        ctx.fleeing_harvesters = frozenset({300})
        return []

    agent = fresh_agent(fresh_ore_terrain(EAST_ORE), ScriptedManager("base_defense", flee))

    sent = fresh_run(agent, [4000, 4005, 4010, 4015], idle_by_the_refinery((300, 301, 302, 303)))

    to = list(EAST_ORE.nearest_cell(BY_THE_REFINERY))
    assert sent[2] == [{"cmd": "harvest", "units": [301, 302, 303]}]
    assert sent[3] == [{"cmd": "harvest", "units": [301, 302, 303], "to": to}]


def test_fresh_ore_a_harvester_seen_on_the_dry_field_s_ore_for_3_states_unmarks_it():
    agent = fresh_agent(fresh_ore_terrain(EAST_ORE))
    fresh_run(agent, [4000, 4005, 4010, 4015], idle_by_the_refinery(range(300, 305)))

    # 304 waits by the refinery while the field is dry, the four sent away at work elsewhere; once 310 has stood on
    # the home field's ore for 3 states, the field is dry no longer and the idle rule sends 304 back to harvest.
    waiting = fresh_run(agent, [4020, 4260], idle_by_the_refinery((304,), busy=range(300, 304)))
    mined = fresh_run(agent, [4265, 4270, 4275], idle_by_the_refinery((304,), busy=range(300, 304), on_ore=(310,)))

    assert waiting == [[], []]
    assert mined == [[], [], [{"cmd": "harvest", "units": [304]}]]


def test_fresh_ore_one_harvesters_moved_event_is_written_per_move_and_it_is_a_defined_kind(match_record):
    agent = fresh_agent(fresh_ore_terrain(EAST_ORE))

    fresh_run(agent, [4000, 4005, 4010, 4015, 4020], idle_by_the_refinery((300, 301), busy=(302,)))

    assert "harvesters_moved" in records.EVENT_KINDS
    assert match_record("harvesters_moved") == [
        {
            "tick": 4015,
            "from_field_center": [22, 40],
            "to_field_center": [33, 47],
            "harvester_ids": [300, 301],
            "reason": "dry",
        }
    ]


def test_fresh_ore_with_the_switch_off_harvester_orders_are_as_today(match_record):
    agent = fresh_agent(fresh_ore_terrain(EAST_ORE), tuning=TWO_REFINERIES_ONLY)

    sent = fresh_run(agent, [4000, 4005, 4010, 4015, 4020], idle_by_the_refinery((300, 301), busy=(302, 303)))

    assert sent == [[], [], [{"cmd": "harvest", "units": [300, 301]}], [], []]
    assert match_record("harvesters_moved") == []


def test_fresh_ore_a_whole_fake_match_with_the_switch_on_runs_without_an_error_within_the_compute_budget():
    match = play_whole_match(dataclasses.replace(tuning_off(), upkeep=FRESH_ORE.upkeep))

    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.compute_p95_ms <= COMPUTE_P95_MS


# -- more war factories and tanks -----------------------------------------------------------------------------------

TANK_FACTORIES_ON = dataclasses.replace(producers_off(), tank_factories=True)
TANK_FACTORIES = dataclasses.replace(TWO_REFINERIES_ONLY, producers=TANK_FACTORIES_ON)
# The built-in refinery thresholds, under which the money is saved for a third refinery from 360 s on.
TANK_FACTORIES_SAVING = tuning_off(producers=TANK_FACTORIES_ON)


def tank_factories_agent(income_per_s: float = 95, busy_share: float = 0.65, tuning: Tuning = TANK_FACTORIES):
    """An upkeep agent whose income estimate gives so much income and every queue busy so much of the last minute."""
    agent = upkeep_agent(tuning=tuning)
    agent.ctx.income = SpentLately(0, 0, income_per_s=income_per_s, busy_share=busy_share)
    return agent


def tank_base(
    seconds: float, factories: int = 1, cash: int = 5000, headroom: int = 400, units: list | None = None, **more
) -> dict:
    """The holding Allied base at a game time with so many war factories, four harvesters and the units given, the
    money and the power left over given, and no service depot on offer."""
    extra = [("weap", 30 + 5 * number, 56) for number in range(factories - 1)]
    economy = powered(500, 500 - headroom, cash=cash, harvesters=4)
    more.setdefault("withheld", ("fix",))
    return base_state(
        "england",
        *HOLDING["england"],
        *extra,
        units=[*harvesters(4), *(units or [])],
        cash=cash,
        economy=economy,
        tick=round(seconds * 25),
        **more,
    )


@pytest.mark.parametrize(
    "seconds, tuning, ordered",
    [(320, TANK_FACTORIES, ["weap"]), (280, TANK_FACTORIES, []), (320, TWO_REFINERIES_ONLY, [])],
    ids=["320 s, on", "280 s, on", "320 s, off"],
)
def test_tank_factories_income_95_and_the_vehicle_queue_65_percent_busy_order_a_second_war_factory_from_300_s(
    seconds, tuning, ordered
):
    # The switch off keeps today's gates: 125 a second and the queue busy 85 percent of the last minute.
    agent = tank_factories_agent(95, 0.65, tuning=tuning)

    assert builds(ask(agent, tank_base(seconds))) == ordered


@pytest.mark.parametrize(
    "factories, income_per_s, ordered",
    [(2, 119, []), (2, 120, ["weap"]), (3, 149, []), (3, 150, ["weap"]), (4, 1000, [])],
    ids=["third, 119", "third, 120", "fourth, 149", "fourth, 150", "four standing"],
)
def test_tank_factories_the_third_war_factory_needs_120_a_second_the_fourth_150_and_there_is_no_fifth(
    factories, income_per_s, ordered
):
    agent = tank_factories_agent(income_per_s, 0.65)

    assert builds(ask(agent, tank_base(400, factories=factories))) == ordered


@pytest.mark.parametrize("busy_share, ordered", [(0.59, []), (0.6, ["weap"])])
def test_tank_factories_the_vehicle_queue_has_to_be_busy_60_percent_of_the_last_minute(busy_share, ordered):
    assert builds(ask(tank_factories_agent(200, busy_share), tank_base(320))) == ordered


def test_tank_factories_the_war_factory_waits_for_the_investment_test():
    agent = tank_factories_agent(95, 0.65)
    agent.ctx.income = SpentLately(0, 0, income_per_s=None, busy_share=0.65)

    assert builds(ask(agent, tank_base(320))) == []


@pytest.mark.parametrize("tuning, ordered", [(TANK_FACTORIES, ["weap"]), (TWO_REFINERIES_ONLY, ["tent"])])
def test_tank_factories_when_both_qualify_the_war_factory_goes_before_the_barracks_and_with_the_switch_off_after(
    tuning, ordered
):
    # 200 a second and every queue busy 90 percent of the last minute: one more barracks and war factory are both due.
    agent = tank_factories_agent(200, 0.9, tuning=tuning)

    assert builds(ask(agent, tank_base(320))) == ordered


@pytest.mark.parametrize("headroom, ordered", [(80, ["powr"]), (90, ["weap"])], ids=["10 short", "power for it"])
def test_tank_factories_no_war_factory_is_ordered_that_would_break_the_power_target(headroom, ordered):
    # At 380 s the target is 60; the war factory drains 30, so it needs 90 left over, and the power plant goes first.
    orders = ask(tank_factories_agent(95, 0.65), tank_base(380, headroom=headroom))

    assert builds(orders) == ordered


@pytest.mark.parametrize("cash, ordered", [(1500, ["proc"]), (5000, ["weap"])], ids=["saving", "not saving"])
def test_tank_factories_the_war_factory_waits_while_the_money_is_saved_for_the_third_refinery(cash, ordered):
    # At 380 s with four harvesters on two refineries a third is called for; 1,500 does not cover it and the reserve.
    agent = tank_factories_agent(95, 0.65, tuning=TANK_FACTORIES_SAVING)

    assert builds(ask(agent, tank_base(380, cash=cash))) == ordered


# The Allied base row of the mix: the Vehicle queue makes light tanks (700); the Infantry queue holds rifles, so that
# all the money above the reserve is the Vehicle queue's.
BUSY_INFANTRY = [situations.job("Infantry", "e1")]


@pytest.mark.parametrize(
    "tuning, cash, queued",
    [
        (TANK_FACTORIES, 1700, [("1tnk", 2)]),
        (TANK_FACTORIES, 1699, [("1tnk", 1)]),
        (TWO_REFINERIES_ONLY, 5000, [("1tnk", 1)]),
    ],
    ids=["money for two", "money for one", "switch off"],
)
def test_tank_factories_with_two_war_factories_an_empty_vehicle_queue_gets_two_vehicles_when_the_money_pays_for_them(
    tuning, cash, queued
):
    agent = tank_factories_agent(tuning=tuning)

    orders = ask(agent, tank_base(400, factories=2, cash=cash, production=BUSY_INFANTRY))

    assert trained(orders, "Vehicle") == queued


def test_tank_factories_with_enough_money_both_queues_are_fed_and_the_vehicle_queue_holds_2_after_one_cycle():
    agent = tank_factories_agent()

    first = ask(agent, tank_base(400, factories=2))
    shown = [situations.job("Vehicle", "1tnk"), situations.job("Vehicle", "1tnk"), situations.job("Infantry", "e1")]
    second = ask(agent, tank_base(400.2, factories=2, production=shown))

    assert unit_trains(first) == [("e1", 3), ("1tnk", 2)] and unit_trains(second) == []
    assert agent.ledger.duplicate_production_orders == 0


@pytest.mark.parametrize(
    "jobs, queued",
    [(["1tnk"], [("1tnk", 1)]), (["1tnk", "1tnk"], []), (["harv", "1tnk"], [])],
    ids=["one queued", "two queued", "a harvester and a tank queued"],
)
def test_tank_factories_a_vehicle_queue_holding_fewer_than_2_items_is_topped_up_to_2(jobs, queued):
    production = [*BUSY_INFANTRY, *(situations.job("Vehicle", item) for item in jobs)]

    orders = ask(tank_factories_agent(), tank_base(400, factories=2, production=production))

    assert trained(orders, "Vehicle") == queued


def test_tank_factories_a_harvester_ordered_in_the_cycle_takes_one_of_the_2_places():
    state = base_state("england", *TWO_REFINERIES, units=harvesters(3), production=BUSY_INFANTRY, tick=10000)

    on, off = (ask(upkeep_agent(tuning=tuning), state) for tuning in (TANK_FACTORIES, TWO_REFINERIES_ONLY))

    assert trained(on, "Vehicle") == [("harv", 1), ("1tnk", 1)] and trained(off, "Vehicle") == [("harv", 1)]


def test_tank_factories_a_top_up_no_state_shows_yet_is_not_ordered_again_until_75_ticks_have_passed():
    # At 400 s, 400.2 s, 402.8 s and 403 s: ticks 10,000, 10,005, 10,070 and 10,075. The queue already holds a light tank, so the ledger takes the tank ordered next as shown from the next state on.
    agent = tank_factories_agent()
    one_tank = [*BUSY_INFANTRY, situations.job("Vehicle", "1tnk")]

    sent = [ask(agent, tank_base(seconds, factories=2, production=one_tank)) for seconds in (400, 400.2, 402.8)]
    again = ask(agent, tank_base(403, factories=2, production=one_tank))

    assert [trained(orders, "Vehicle") for orders in sent] == [[("1tnk", 1)], [], []]
    assert trained(again, "Vehicle") == [("1tnk", 1)]


def test_tank_factories_a_queue_another_manager_claims_is_left_to_it_and_no_set_primary_is_ever_sent():
    agent = tank_factories_agent()
    agent.ctx.claims.hold("Vehicle", "opening")

    claimed = ask(agent, tank_base(400, factories=2, production=BUSY_INFANTRY))
    fed = ask(tank_factories_agent(), tank_base(400, factories=2))

    assert trained(claimed, "Vehicle") == [] and trained(fed, "Vehicle") == [("1tnk", 2)]
    assert with_command([*claimed, *fed], "set_primary") == []


# Money for a light tank (700) above the reserve of 300, but not for one and a rifle besides: each pick feeds one queue.
ONE_TANK_CASH = 1050
FLOOR_PICKS = 200
# The Allied base row of the mix gives its light tanks 35 of its 100 points.
ALLIED_BASE_VEHICLE_SHARE = 0.35


def floor_picks(tuning: Tuning, factories: int) -> tuple[dict[str, int], list[float]]:
    """200 picks, each one cycle of a fresh agent that has spent what the picks before it bought, with the units they
    bought standing and money for one queue's item: what was spent on each unit queue, and the vehicles' share of
    the spending after each infantry pick."""
    items = situations.tracked_items()
    spent = {"Infantry": 0, "Vehicle": 0}
    army: list[dict] = []
    after_infantry = []
    for _ in range(FLOOR_PICKS):
        agent = spent_lately(upkeep_agent(tuning=tuning), infantry=spent["Infantry"], vehicle=spent["Vehicle"])
        state = tank_base(400, factories=factories, cash=ONE_TANK_CASH, units=army)
        ((item, count),) = unit_trains(ask(agent, state))
        spent[items[item].queue] += items[item].cost * count
        army += soldiers(item, count, first_id=FIRST_PICKED_UNIT_ID + len(army))
        if items[item].queue == "Infantry":
            after_infantry.append(army_vehicle_share(spent))
    return spent, after_infantry


def test_tank_factories_over_200_picks_with_two_war_factories_vehicles_take_at_least_60_percent_of_the_spending():
    spent, after_infantry = floor_picks(TANK_FACTORIES, factories=2)

    assert len(after_infantry) > 1 and min(after_infantry) >= 0.6
    assert army_vehicle_share(spent) >= 0.6


@pytest.mark.parametrize(
    "tuning, factories", [(TWO_REFINERIES_ONLY, 2), (TANK_FACTORIES, 1)], ids=["switch off", "one war factory"]
)
def test_tank_factories_off_or_with_one_war_factory_the_vehicles_keep_the_mix_s_share_of_35_percent(tuning, factories):
    spent, _ = floor_picks(tuning, factories)

    assert army_vehicle_share(spent) == pytest.approx(ALLIED_BASE_VEHICLE_SHARE, abs=0.05)


@pytest.mark.parametrize(
    "infantry, vehicle, fed",
    [(0, 0, [("1tnk", 1)]), (300, 700, [("1tnk", 1)]), (300, 1400, [("e1", 3)])],
    ids=["nothing spent", "70 percent, 59 after rifles", "82 percent, 74 after rifles"],
)
def test_tank_factories_with_money_for_one_queue_rifles_go_first_only_while_vehicles_stay_at_60_percent_after_them(
    infantry, vehicle, fed
):
    agent = spent_lately(upkeep_agent(tuning=TANK_FACTORIES), infantry=infantry, vehicle=vehicle)

    assert unit_trains(ask(agent, tank_base(400, factories=2, cash=ONE_TANK_CASH))) == fed


TANK_FACTORIES_AND_SECOND_YARD = dataclasses.replace(TANK_FACTORIES, expansion=expansion_off(second_yard=True))
SECOND_YARD_ONLY = dataclasses.replace(TWO_REFINERIES_ONLY, expansion=expansion_off(second_yard=True))


@pytest.mark.parametrize(
    "tuning, jobs, queued",
    [
        (TANK_FACTORIES_AND_SECOND_YARD, ["2tnk"], [("mcv", 1)]),
        (TANK_FACTORIES_AND_SECOND_YARD, ["2tnk", "2tnk"], []),
        (SECOND_YARD_ONLY, ["2tnk"], []),
    ],
    ids=["on, one tank queued", "on, two tanks queued", "off, one tank queued"],
)
def test_tank_factories_the_second_yard_s_mcv_takes_the_room_of_a_filled_vehicle_queue(tuning, jobs, queued):
    # A depot stands and 5,000 is above the reserve and the MCV's 2,000: the MCV is due but for the queue.
    production = [*BUSY_INFANTRY, *(situations.job("Vehicle", item) for item in jobs)]

    orders = ask(upkeep_agent(tuning=tuning), mixed(cash=5000, tick=400 * 25, production=production))

    assert trained(orders, "Vehicle") == queued


def test_tank_factories_with_the_second_yard_too_a_whole_fake_match_comes_to_two_yards_without_an_error():
    match = play_whole_match(tuning_off(producers=TANK_FACTORIES_ON, expansion=expansion_off(second_yard=True)))
    yards = [building for building in match.game.own_buildings() if building["type"] == "fact"]
    deployed = [event for event in match.data_of("expansion") if event["phase"] in ("deployed", "deployed_home")]

    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.compute_p95_ms <= COMPUTE_P95_MS
    assert len(yards) == 2 and deployed


# A whole match with the switch on stands 3 war factories by 900 s, and its second by 400 s: the built-in gates give
# the second at 500 s in the same match.
WAR_FACTORIES_CHECKED_AT = {400: 2, 900: 3}
FILL_FROM_SECONDS = 300


@dataclasses.dataclass
class FilledMatch:
    """A whole fake match with the switch on: the match, the war factories standing at each time checked, and the
    army's vehicles (harvesters left out) the Vehicle queue held in each state from 300 s on."""

    match: WholeMatch
    war_factories: dict[int, int]
    vehicles_queued: list[int]


@pytest.fixture(scope="module")
def tank_factories_match() -> FilledMatch:
    war_factories: dict[int, int] = {}
    vehicles_queued: list[int] = []

    def count_war_factories(seconds: int):
        def note(game: FakeRedAlert) -> None:
            war_factories[seconds] = sum(1 for building in game.own_buildings() if building["type"] == "weap")

        return fake_game.at_seconds(seconds, note)

    def note_queue(game: FakeRedAlert) -> None:
        if game.tick >= FILL_FROM_SECONDS * 25:
            jobs = game.state()["observation"]["production"]
            vehicles_queued.append(sum(1 for job in jobs if job["queue"] == "Vehicle" and job["item"] != "harv"))

    scenario = [
        *map(count_war_factories, WAR_FACTORIES_CHECKED_AT),
        fake_game.at(1, lambda game: game.on_step(note_queue)),
    ]
    match = play_whole_match(tuning_off(producers=TANK_FACTORIES_ON), scenario=scenario)
    return FilledMatch(match, war_factories, vehicles_queued)


def test_tank_factories_a_whole_fake_match_with_the_switch_on_has_3_war_factories_by_900_s_within_the_compute_budget(
    tank_factories_match,
):
    match = tank_factories_match.match

    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.compute_p95_ms <= COMPUTE_P95_MS
    assert all(tank_factories_match.war_factories[at] >= least for at, least in WAR_FACTORIES_CHECKED_AT.items())


def test_tank_factories_in_that_match_the_vehicle_queue_mostly_holds_2_vehicles_and_never_more_and_no_set_primary(
    tank_factories_match,
):
    queued = Counter(tank_factories_match.vehicles_queued)
    orders = [order for batch in tank_factories_match.match.run.batches for order in batch["orders"]]

    assert max(queued) == 2 and queued[2] > sum(queued.values()) / 2
    assert with_command(orders, "set_primary") == []
    assert tank_factories_match.match.agent.ledger.duplicate_production_orders == 0


# -- no army infantry after the first war factory -----------------------------------------------------------------

NO_ARMY_INFANTRY = dataclasses.replace(
    TWO_REFINERIES_ONLY, upkeep=dataclasses.replace(TWO_REFINERIES_ONLY.upkeep, no_army_infantry=True)
)


def rockets(count: int) -> list[dict]:
    """So many own rocket soldiers, numbered from 400."""
    return soldiers("e3", count)


@pytest.mark.parametrize(
    "standing, infantry",
    [(0, [("e3", 3)]), (2, [("e3", 2)]), (3, [("e3", 1)]), (4, []), (6, [])],
    ids=["none", "two", "three", "four", "six"],
)
def test_no_army_infantry_with_a_war_factory_rockets_are_trained_up_to_4_standing_and_no_rifle_ever(standing, infantry):
    orders = ask(upkeep_agent(tuning=NO_ARMY_INFANTRY), holding(units=[*harvesters(4), *rockets(standing)]))

    assert trained(orders, "Infantry") == infantry
    assert trained(orders, "Vehicle") == [("1tnk", 1)]


def test_no_army_infantry_with_the_switch_off_the_same_base_trains_rifles():
    orders = ask(upkeep_agent(), holding(units=[*harvesters(4), *rockets(4)]))

    assert trained(orders, "Infantry") == [("e1", 3)]


@pytest.mark.parametrize("group", ["raid", "scout"])
def test_no_army_infantry_rockets_of_the_raid_group_or_scouting_are_not_counted_toward_the_4(group):
    agent = upkeep_agent(tuning=NO_ARMY_INFANTRY)
    ask(agent, holding(units=[*harvesters(4), *rockets(4)]))
    for rocket in (400, 401):
        agent.ctx.groups.assign(rocket, group)

    orders = ask(agent, holding(units=[*harvesters(4), *rockets(4)], tick=1100, production=BUSY_VEHICLES))

    assert trained(orders, "Infantry") == [("e3", 2)]


BUSY_VEHICLES = [situations.job("Vehicle", "1tnk")]


def lost_war_factory_orders(tuning: Tuning) -> list[tuple[str, int]]:
    """The unit orders of an agent that has seen its war factory stand with both unit queues busy, for the same base
    once the war factory is gone and the queues are empty; a war factory is not on offer, so no money is saved for
    one."""
    agent = upkeep_agent(tuning=tuning)
    ask(agent, holding(production=[situations.job("Infantry", "e1"), *BUSY_VEHICLES]))
    without = [building for building in HOLDING["england"] if building[0] != "weap"]
    lost = base_state("england", *without, units=harvesters(4), tick=1100, withheld=("weap",))
    return unit_trains(ask(agent, lost))


def test_no_army_infantry_with_no_war_factory_standing_the_infantry_ordered_is_as_with_the_switch_off():
    assert lost_war_factory_orders(NO_ARMY_INFANTRY) == lost_war_factory_orders(TWO_REFINERIES_ONLY) == [("e1", 3)]


@pytest.mark.parametrize("tuning", [NO_ARMY_INFANTRY, TWO_REFINERIES_ONLY], ids=["on", "off"])
def test_no_army_infantry_the_soviet_raid_quota_s_grenadiers_are_trained_as_with_the_switch_off(tuning):
    agent = upkeep_agent(tuning=tuning, plan_selector=OpeningThen(RAID))

    orders = ask(agent, holding("russia", units=[*harvesters(4), *rockets(4)]))

    assert agent.current_plan == RAID and trained(orders, "Infantry") == [("e2", 3)]


@pytest.mark.parametrize("tuning, fed", [(NO_ARMY_INFANTRY, [("2tnk", 1)]), (TWO_REFINERIES_ONLY, [("e1", 3)])])
def test_no_army_infantry_with_money_for_one_queue_the_vehicle_goes_first_though_the_infantry_is_behind(tuning, fed):
    # 1,200: the reserve leaves 900, which pays for the heavy tank (850) or the infantry, and the infantry spent nothing.
    agent = upkeep_agent(tuning=tuning)
    agent.ctx.income = SpentLately(infantry=0, vehicle=1700)

    assert unit_trains(ask(agent, mixed(cash=ONE_ONLY_CASH, units=TWO_ROCKETS))) == fed


def test_no_army_infantry_with_money_for_both_the_rockets_short_of_4_and_the_vehicle_are_trained():
    orders = ask(upkeep_agent(tuning=NO_ARMY_INFANTRY), mixed(units=TWO_ROCKETS))

    assert unit_trains(orders) == [("e3", 2), ("2tnk", 1)]


def test_no_army_infantry_a_major_threat_s_counter_rifles_are_still_trained():
    agent = new_agent(BaseDefenseManager(), UpkeepManager(), plan_selector=OpeningThen(HOLD), tuning=NO_ARMY_INFANTRY)

    orders = ask(agent, holding(units=[*harvesters(4), *rockets(4)], visible_enemies=RIFLES))

    assert agent.ctx.threat_level == MAJOR_THREAT
    assert trained(orders, "Infantry") == [("e1", 3)]
    assert [proposal.priority for proposal in agent.ctx.production.proposed() if proposal.item == "e1"] == [
        Priority.EMERGENCY
    ]


@pytest.mark.parametrize(
    "income_per_s, tuning, ordered",
    [
        (110, TWO_REFINERIES_ONLY, ["tent"]),
        (110, NO_ARMY_INFANTRY, []),
        (200, TWO_REFINERIES_ONLY, ["tent"]),
        (200, NO_ARMY_INFANTRY, ["weap"]),
    ],
    ids=["barracks due, off", "barracks due, on", "both due, off", "both due, on"],
)
def test_no_army_infantry_producer_scaling_adds_no_barracks_while_a_war_factory_stands(income_per_s, tuning, ordered):
    # Every queue busy 90 percent of the last minute at 320 s: 110 a second calls for a second barracks and not yet for
    # a second war factory (125), 200 for both.
    agent = tank_factories_agent(income_per_s, 0.9, tuning=tuning)

    assert builds(ask(agent, tank_base(320))) == ordered


# The upkeep manager's own intents, which a recorder that stands in for them calls.
UPKEEP_INTENTS = UpkeepManager.intents


class UpkeepTrains:
    """What the upkeep manager orders over a match, as `recorded` sees it: the tick the first war factory stood at, and
    the (tick, item, count) of each train it ordered before the end-of-match spending began."""

    def __init__(self) -> None:
        self.first_war_factory_tick: int | None = None
        self.trains: list[tuple[int, str, int]] = []
        self.match: WholeMatch | None = None

    def recorded(self, manager: UpkeepManager, world, ctx) -> list:
        """The upkeep manager's intents for a cycle, noted on the way."""
        intents = UPKEEP_INTENTS(manager, world, ctx)
        if self.first_war_factory_tick is None and ctx.production.standing("weap"):
            self.first_war_factory_tick = world.tick
        if not clock.is_spending(world, ctx):
            orders = with_command([intent.order for intent in intents], "train")
            self.trains += [(world.tick, order["item"], order["count"]) for order in orders]
        return intents


@pytest.fixture(scope="module")
def no_army_infantry_match() -> UpkeepTrains:
    """A whole fake match with `no_army_infantry` on, and what the upkeep manager ordered in it."""
    seen = UpkeepTrains()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(UpkeepManager, "intents", lambda manager, world, ctx: seen.recorded(manager, world, ctx))
        seen.match = play_whole_match(tuning_off(upkeep=dataclasses.replace(upkeep_off(), no_army_infantry=True)))
    return seen


def test_no_army_infantry_a_whole_fake_match_with_the_switch_on_runs_without_an_error_within_the_compute_budget(
    no_army_infantry_match,
):
    match = no_army_infantry_match.match

    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.compute_p95_ms <= COMPUTE_P95_MS


def test_army_build_in_that_match_upkeep_orders_no_rifle_after_the_first_war_factory_and_rockets_beyond_4(
    no_army_infantry_match,
):
    stood_at = no_army_infantry_match.first_war_factory_tick
    after = [(item, count) for tick, item, count in no_army_infantry_match.trains if tick >= stood_at]

    assert stood_at is not None and after
    assert [item for item, _ in after if item == "e1"] == []
    assert sum(count for item, count in after if item == "e3") > upkeep_off().rockets_kept


# The shipped army mix over the no-army-infantry base, at the dome stage of an Allied base.
ARMY_BUILD = dataclasses.replace(NO_ARMY_INFANTRY, mix=Tuning().mix)
ALLIED_DOME_STAGE = (*HOLDING["england"], ("fix", 30, 44), ("dome", 30, 48))
TWELVE_TANKS = soldiers("2tnk", 12, first_id=500)


@pytest.mark.parametrize(
    "standing, infantry",
    # 12 medium tanks and 4 rockets are worth 11,400, of which the rockets' 15 percent is 1,710: 2 more; with 5
    # rockets, 11,700 and 1,755: one more; with 6, 12,000 and 1,800: none.
    [(4, [("e3", 2)]), (5, [("e3", 1)]), (6, [])],
    ids=["four", "five", "six"],
)
def test_army_build_with_a_war_factory_rockets_are_trained_beyond_4_for_their_share_and_no_rifle(standing, infantry):
    state = base_state("england", *ALLIED_DOME_STAGE, units=[*harvesters(4), *rockets(standing), *TWELVE_TANKS])

    orders = ask(upkeep_agent(tuning=ARMY_BUILD), state)

    assert trained(orders, "Infantry") == infantry


# -- the economy first: the unit queues wait for a refinery claim, a war factory or the MCV --------------------------

ECONOMY_FIRST = dataclasses.replace(
    TWO_REFINERIES_ONLY, upkeep=dataclasses.replace(TWO_REFINERIES_ONLY.upkeep, economy_first=True)
)
# A war factory's price, 2,000, and the production reserve.
WAR_FACTORY_PRICE_AND_RESERVE = 2000 + upkeep_off().production_cash_reserve


@pytest.mark.parametrize(
    "tuning, cash, ordered",
    [
        (ECONOMY_FIRST, WAR_FACTORY_PRICE_AND_RESERVE, ["weap"]),
        (ECONOMY_FIRST, WAR_FACTORY_PRICE_AND_RESERVE - 1, []),
        (TWO_REFINERIES_ONLY, 500, ["weap"]),
    ],
    ids=["on, covered", "on, 1 short", "off, the investment test alone"],
)
def test_economy_first_a_second_war_factory_waits_for_the_money_to_cover_its_price_and_the_reserve(
    tuning, cash, ordered
):
    # 130 a second and the Vehicle queue busy 90 percent of the last minute at 200 s, before one more barracks is due:
    # the war factory's gates but the money are met, and the investment test passes on the income alone.
    agent = tank_factories_agent(130, 0.9, tuning=tuning)

    assert builds(ask(agent, tank_base(200, cash=cash))) == ordered


def economy_holds(monkeypatch) -> list[dict]:
    """The data of every `economy_hold` event logged from here on; the other events are dropped."""
    logged: list[dict] = []
    monkeypatch.setattr(records, "log", lambda kind, data: logged.append(data) if kind == "economy_hold" else None)
    return logged


def hold(tick: int, item: str, reason: str) -> dict:
    """The data of an `economy_hold` event."""
    return {"tick": tick, "item": item, "reason": reason}


def war_factory_agent(tuning: Tuning = ECONOMY_FIRST):
    """An upkeep agent whose income calls for a second war factory: 130 a second, every queue busy 90 percent of the
    last minute."""
    return tank_factories_agent(130, 0.9, tuning=tuning)


def war_factory_base(tick: int, cash: int = 1000, **more) -> dict:
    """The holding Allied base with one war factory at a tick before 210 s, when one more barracks would be due,
    with 1,000 of money unless given: less than a war factory and the reserve."""
    return tank_base(tick / 25, cash=cash, **more)


HOLD_STARTS = 4000


def test_economy_first_a_due_war_factory_holds_the_unit_queues_until_the_money_covers_it_and_is_then_ordered(
    monkeypatch,
):
    events = economy_holds(monkeypatch)
    agent = war_factory_agent()

    held = [ask(agent, war_factory_base(tick)) for tick in (HOLD_STARTS, 4100, 4250, 4400)]
    covered = ask(agent, war_factory_base(4450, cash=WAR_FACTORY_PRICE_AND_RESERVE))

    assert [(unit_trains(orders), builds(orders)) for orders in held] == [([], [])] * 4
    assert builds(covered) == ["weap"] and unit_trains(covered) == []
    assert events == [hold(HOLD_STARTS, "weap", "started"), hold(4450, "weap", "ordered")]


def test_economy_first_a_hold_ends_after_500_ticks_the_units_resume_and_no_hold_starts_for_500_ticks_more(monkeypatch):
    events = economy_holds(monkeypatch)
    agent = war_factory_agent()

    trains_at = {tick: unit_trains(ask(agent, war_factory_base(tick))) for tick in range(HOLD_STARTS, 5001, 100)}

    assert all(trains_at[tick] == [] for tick in range(HOLD_STARTS, 4500, 100))
    assert trains_at[4500] == [("1tnk", 1)] and trains_at[5000] == []
    assert events == [
        hold(HOLD_STARTS, "weap", "started"),
        hold(4500, "weap", "timed_out"),
        hold(5000, "weap", "started"),
    ]


def test_economy_first_a_power_plant_the_power_target_calls_for_is_ordered_during_a_hold(monkeypatch):
    events = economy_holds(monkeypatch)
    agent = war_factory_agent()
    ask(agent, war_factory_base(HOLD_STARTS))

    orders = ask(agent, war_factory_base(4100, headroom=10))

    assert builds(orders) == ["powr"] and unit_trains(orders) == []
    assert events == [hold(HOLD_STARTS, "weap", "started")]


def test_economy_first_a_hold_whose_war_factory_is_due_no_longer_ends_and_the_units_are_fed(monkeypatch):
    events = economy_holds(monkeypatch)
    agent = war_factory_agent()
    ask(agent, war_factory_base(HOLD_STARTS))
    # 100 a second is short of the second war factory's 125.
    agent.ctx.income = SpentLately(0, 0, income_per_s=100, busy_share=0.9)

    orders = ask(agent, war_factory_base(4100))

    assert unit_trains(orders) == [("1tnk", 1)]
    assert events == [hold(HOLD_STARTS, "weap", "started"), hold(4100, "weap", "not_due")]


# A refinery's price, 1,400, and the production reserve.
REFINERY_PRICE_AND_RESERVE = 1400 + upkeep_off().production_cash_reserve


ECONOMY_FIRST_UNSERVED = dataclasses.replace(
    ECONOMY_FIRST, placement=dataclasses.replace(ECONOMY_FIRST.placement, refinery_unserved_only=True)
)


def test_economy_first_with_no_unserved_place_a_due_claim_refinery_holds_no_queue(monkeypatch):
    events = economy_holds(monkeypatch)
    monkeypatch.setattr("ucla_agent.games.redalert.upkeep._is_claim_due", lambda ctx: True)
    monkeypatch.setattr("ucla_agent.games.redalert.refinery_fields.UnservedPlaces.choice", lambda *_: None)
    agent = war_factory_agent(ECONOMY_FIRST_UNSERVED)

    orders = ask(agent, war_factory_base(HOLD_STARTS))

    # The war factory, short of its money, holds the unit queues; the claim's refinery, with nowhere to go, never.
    assert "proc" not in builds(orders)
    assert events == [hold(HOLD_STARTS, "weap", "started")]


def test_economy_first_a_claim_or_growth_refinery_without_a_place_is_no_item_of_the_hold():
    agent = war_factory_agent(ECONOMY_FIRST_UNSERVED)
    ask(agent, war_factory_base(HOLD_STARTS))
    agent.ctx.refinery_claim_due = True

    with_place = _economy_items_due(agent.world, agent.ctx, True, True)
    without_place = _economy_items_due(agent.world, agent.ctx, True, False)

    assert [item.item for item in with_place] == ["proc", "proc", "weap"]
    assert [item.item for item in without_place] == ["weap"]


def test_economy_first_a_refinery_claim_goes_before_the_war_factory_and_frees_the_units_once_on_its_way(monkeypatch):
    events = economy_holds(monkeypatch)
    monkeypatch.setattr("ucla_agent.games.redalert.upkeep._is_claim_due", lambda ctx: True)
    agent = war_factory_agent()

    held = ask(agent, war_factory_base(HOLD_STARTS))
    covered = ask(agent, war_factory_base(4050, cash=REFINERY_PRICE_AND_RESERVE))
    building = [situations.job("Building", "proc")]
    freed = ask(agent, war_factory_base(4100, cash=REFINERY_PRICE_AND_RESERVE, production=building))

    # The refinery is ordered after the unit queues, which wait in its cycle; the war factory, still short of its
    # money, waits for the release to end before it holds them again.
    assert unit_trains(held) == unit_trains(covered) == [] and builds(covered) == ["proc"]
    assert unit_trains(freed) == [("e1", 3), ("1tnk", 1)]
    assert events == [hold(HOLD_STARTS, "proc", "started"), hold(4100, "proc", "ordered")]


SECOND_YARD_FIRST = dataclasses.replace(
    ECONOMY_FIRST, expansion=dataclasses.replace(ECONOMY_FIRST.expansion, second_yard=True)
)
# The MCV is trained once the money less the reserve reaches second_yard_min_cash.
MCV_MONEY = expansion_off().second_yard_min_cash + upkeep_off().production_cash_reserve


def test_economy_first_the_second_yard_s_mcv_holds_the_unit_queues_until_the_money_covers_it(monkeypatch):
    events = economy_holds(monkeypatch)
    agent = upkeep_agent(tuning=SECOND_YARD_FIRST)

    held = ask(agent, mixed(cash=MCV_MONEY - 1, tick=1000))
    covered = ask(agent, mixed(cash=MCV_MONEY, tick=1100))

    assert unit_trains(held) == [] and unit_trains(covered) == [("mcv", 1)]
    assert events == [hold(1000, "mcv", "started"), hold(1100, "mcv", "ordered")]


def test_economy_first_with_the_switch_off_the_war_factory_is_ordered_at_once_and_no_hold_is_written(monkeypatch):
    events = economy_holds(monkeypatch)

    orders = ask(war_factory_agent(TWO_REFINERIES_ONLY), war_factory_base(HOLD_STARTS))

    assert builds(orders) == ["weap"] and events == []


def test_economy_first_the_hold_starts_only_for_a_first_item_not_covered_and_after_the_release(monkeypatch):
    events = economy_holds(monkeypatch)
    economy, short = EconomyHold(), [EconomyItem("weap", is_covered=False)]
    tuning = upkeep_off()

    def holds(tick: int, due: list[EconomyItem]) -> bool:
        return economy.holds(due, lambda item: False, tick, tuning)

    covered_first = holds(0, [EconomyItem("proc", is_covered=True), *short])
    started, given_up, released, again = holds(10, short), holds(100, []), holds(599, short), holds(600, short)

    assert (covered_first, started, given_up, released, again) == (False, True, False, False, True)
    assert events == [hold(10, "weap", "started"), hold(100, "weap", "not_due"), hold(600, "weap", "started")]


# -- lean power: spare power held to a share of the drain ------------------------------------------------------------

LEAN_POWER = dataclasses.replace(
    TWO_REFINERIES_ONLY, upkeep=dataclasses.replace(TWO_REFINERIES_ONLY.upkeep, lean_power=True)
)


def drained(seconds: float, *buildings: tuple, drain: int, headroom: int, faction: str = "england", **more) -> dict:
    """A state at a game time of a base with the buildings given, which drains so much power and has so much over."""
    economy = powered(drain + headroom, drain, cash=5000, harvesters=4)
    return base_state(faction, *buildings, tick=round(seconds * 25), economy=economy, **more)


@pytest.mark.parametrize(
    "tuning, drain, target",
    [(LEAN_POWER, 560, 84), (TWO_REFINERIES_ONLY, 560, 100), (LEAN_POWER, 100, 20)],
    ids=["on, 560 drained", "off, 560 drained", "on, 100 drained"],
)
def test_lean_power_the_target_is_15_percent_of_the_drain_not_the_mid_or_large_target_and_20_at_least(
    tuning, drain, target
):
    # At 400 s with three barracks and two war factories, the mid target (60) and the large one (100) would apply.
    agent = upkeep_agent(tuning=tuning)

    ask(agent, drained(400, *FIVE_PRODUCERS, drain=drain, headroom=200))

    assert power_target(agent.world, agent.ctx) == target


@pytest.mark.parametrize("headroom, ordered", [(83, ["powr"]), (84, [])])
def test_lean_power_at_a_drain_of_560_a_power_plant_is_ordered_below_84_over(headroom, ordered):
    assert builds(
        ask(upkeep_agent(tuning=LEAN_POWER), drained(400, *FIVE_PRODUCERS, drain=560, headroom=headroom))
    ) == (ordered)


def test_lean_power_the_tesla_target_still_applies_with_two_coils():
    agent = upkeep_agent(tuning=LEAN_POWER)

    orders = ask(agent, drained(300, *SOVIET_BASE, *TESLA_COILS, drain=300, headroom=150, faction="russia"))

    assert power_target(agent.world, agent.ctx) == upkeep_off().headroom_tesla and builds(orders) == ["powr"]


@pytest.mark.parametrize(
    "drain, headroom, ordered",
    [(100, 25, ["powr"]), (430, 70, ["powr"]), (400, 100, ["weap"])],
    ids=["it would leave the base short", "it would leave 40 against 65", "it leaves 70 against 60"],
)
def test_lean_power_a_war_factory_that_would_leave_less_than_the_target_over_gets_a_power_plant_first(
    drain, headroom, ordered
):
    agent = tank_factories_agent(130, 0.9, tuning=LEAN_POWER)

    orders = ask(agent, drained(200, *HOLDING["england"], drain=drain, headroom=headroom, withheld=("fix",)))

    assert builds(orders) == ordered


# -- the economy first and lean power over a whole match -------------------------------------------------------------


@dataclasses.dataclass
class LeanMatch:
    """A whole fake match with `economy_first` and `lean_power` on and the second yard as well, whose MCV the unit
    queues wait for: the match, and the (power provided, power drained) of each state."""

    match: WholeMatch
    power: list[tuple[int, int]]


@pytest.fixture(scope="module")
def economy_first_match() -> LeanMatch:
    power: list[tuple[int, int]] = []

    def note_power(game: FakeRedAlert) -> None:
        economy = game.state()["observation"]["economy"]
        power.append((economy["power_provided"], economy["power_drained"]))

    upkeep = dataclasses.replace(upkeep_off(), economy_first=True, lean_power=True)
    tuning = tuning_off(upkeep=upkeep, expansion=expansion_off(second_yard=True))
    match = play_whole_match(tuning, scenario=[fake_game.at(1, lambda game: game.on_step(note_power))])
    return LeanMatch(match, power)


def test_economy_first_and_lean_power_a_whole_fake_match_runs_without_an_error_and_never_short_of_power(
    economy_first_match,
):
    match = economy_first_match.match

    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.compute_p95_ms <= COMPUTE_P95_MS
    assert economy_first_match.power and all(drained <= provided for provided, drained in economy_first_match.power)


def test_economy_first_in_that_match_each_hold_lasts_500_ticks_at_most_and_the_next_waits_500_more(economy_first_match):
    holds = economy_first_match.match.data_of("economy_hold")
    starts, ends = holds[::2], holds[1::2]

    assert starts and all(event["reason"] == "started" for event in starts)
    assert all(end["reason"] != "started" and end["item"] == start["item"] for start, end in zip(starts, ends))
    assert all(end["tick"] - start["tick"] <= 500 for start, end in zip(starts, ends))
    assert all(start["tick"] - end["tick"] >= 500 for end, start in zip(ends, starts[1:]))


# -- the MCV first: a full hold while the second yard's MCV is due and its money short ------------------------------

MCV_FIRST = dataclasses.replace(
    TWO_REFINERIES_ONLY,
    upkeep=dataclasses.replace(TWO_REFINERIES_ONLY.upkeep, mcv_first=True),
    expansion=dataclasses.replace(TWO_REFINERIES_ONLY.expansion, second_yard=True),
)
MCV_FIRST_AND_ECONOMY_FIRST = dataclasses.replace(
    MCV_FIRST, upkeep=dataclasses.replace(MCV_FIRST.upkeep, economy_first=True)
)
# The second yard on and mcv_first off, and mcv_first on without the second yard.
SECOND_YARD_ONLY = dataclasses.replace(MCV_FIRST, upkeep=TWO_REFINERIES_ONLY.upkeep)
MCV_FIRST_WITHOUT_SECOND_YARD = dataclasses.replace(MCV_FIRST, expansion=expansion_off())
# Short of the MCV's 2,000 above the reserve.
MCV_SHORT = 1500
# A full hold lasts 30 s, 750 ticks, at the most; nothing is held for 20 s, 500 ticks, after it.
FULL_HOLD_TICKS = 750
RELEASE_TICKS = 500


def full_hold(tick: int, reason: str) -> dict:
    """The data of an `economy_hold` event of a full hold for the MCV."""
    return {"tick": tick, "item": "mcv", "reason": reason, "scope": "all"}


def test_mcv_first_with_the_depot_standing_and_the_money_short_a_full_hold_starts_and_is_published(monkeypatch):
    events = economy_holds(monkeypatch)
    agent = upkeep_agent(tuning=MCV_FIRST)

    ask(agent, mixed(cash=MCV_SHORT, tick=1000))

    assert (agent.ctx.mcv_first_due, agent.ctx.full_hold) == (True, True)
    assert events == [full_hold(1000, "started")]


def test_mcv_first_once_the_money_covers_the_mcv_it_is_ordered_and_the_hold_ends_as_ordered(monkeypatch):
    events = economy_holds(monkeypatch)
    agent = upkeep_agent(tuning=MCV_FIRST)
    ask(agent, mixed(cash=MCV_SHORT, tick=1000))

    covered = ask(agent, mixed(cash=MCV_MONEY, tick=1100))

    assert ("mcv", 1) in unit_trains(covered) and agent.ctx.full_hold is False
    assert events == [full_hold(1000, "started"), full_hold(1100, "ordered")]


@pytest.mark.parametrize("tuning", [MCV_FIRST, MCV_FIRST_AND_ECONOMY_FIRST], ids=["mcv_first", "and economy_first"])
def test_mcv_first_a_hold_ends_after_750_ticks_as_timed_out_and_none_starts_for_500_ticks_more(monkeypatch, tuning):
    events = economy_holds(monkeypatch)
    agent = upkeep_agent(tuning=tuning)
    timed_out, again = 1000 + FULL_HOLD_TICKS, 1000 + FULL_HOLD_TICKS + RELEASE_TICKS

    held = {}
    for tick in range(1000, again + 1, 50):
        ask(agent, mixed(cash=MCV_SHORT, tick=tick))
        held[tick] = agent.ctx.full_hold

    assert all(held[tick] for tick in range(1000, timed_out, 50)) and held[again]
    assert not any(held[tick] for tick in range(timed_out, again, 50))
    # With economy_first on as well, the unit queues' own hold never waits for the MCV nor holds in the release.
    assert events == [full_hold(1000, "started"), full_hold(timed_out, "timed_out"), full_hold(again, "started")]


def test_mcv_first_a_hold_whose_mcv_is_wanted_no_longer_ends_as_not_due(monkeypatch):
    events = economy_holds(monkeypatch)
    agent = upkeep_agent(tuning=MCV_FIRST)
    ask(agent, mixed(cash=MCV_SHORT, tick=1000))

    two_yards = ("fact", 40, 56)
    ask(agent, base_state("england", *MIXED_BASE["england"], two_yards, units=harvesters(4), cash=MCV_SHORT, tick=1100))

    assert (agent.ctx.mcv_first_due, agent.ctx.full_hold) == (False, False)
    assert [event["reason"] for event in events] == ["started", "not_due"]


def test_mcv_first_the_time_gate_of_the_second_yard_does_not_delay_the_mcv_once_a_depot_stands(monkeypatch):
    events = economy_holds(monkeypatch)
    agent = upkeep_agent(tuning=MCV_FIRST)
    # 100 s, before second_yard_from_seconds (240 s).
    early = 100 * 25

    ask(agent, mixed(cash=MCV_SHORT, tick=early))
    covered = ask(upkeep_agent(tuning=MCV_FIRST), mixed(cash=MCV_MONEY, tick=early))

    assert agent.ctx.mcv_first_due and ("mcv", 1) in unit_trains(covered)
    assert events == [full_hold(early, "started")]


def mcv_first_base(tick: int, *more_buildings: tuple, cash: int = 1000) -> dict:
    """The holding Allied base at a tick before 210 s with four harvesters and the buildings given, no service depot
    on offer to build."""
    economy = powered(500, 100, cash=cash, harvesters=4)
    return base_state(
        "england", *HOLDING["england"], *more_buildings, units=harvesters(4), economy=economy, tick=tick, cash=cash
    )


def test_mcv_first_a_unit_hold_in_force_when_a_full_hold_starts_gives_way_as_not_due(monkeypatch):
    events = economy_holds(monkeypatch)
    agent = war_factory_agent(MCV_FIRST_AND_ECONOMY_FIRST)

    ask(agent, mcv_first_base(HOLD_STARTS))
    depot = ask(agent, mcv_first_base(HOLD_STARTS + 50, ("fix", 26, 54)))

    assert agent.ctx.full_hold and unit_trains(depot) == []
    assert events == [
        hold(HOLD_STARTS, "weap", "started"),
        full_hold(HOLD_STARTS + 50, "started"),
        hold(HOLD_STARTS + 50, "weap", "not_due"),
    ]


@pytest.mark.parametrize("tuning", [SECOND_YARD_ONLY, MCV_FIRST_WITHOUT_SECOND_YARD], ids=["mcv_first off", "no yard"])
def test_mcv_first_with_the_switch_or_the_second_yard_off_nothing_is_held_or_written(monkeypatch, tuning):
    events = economy_holds(monkeypatch)
    agent = upkeep_agent(tuning=tuning)

    ask(agent, mixed(cash=MCV_SHORT, tick=1000))

    assert (agent.ctx.mcv_first_due, agent.ctx.full_hold) == (False, False) and events == []


def test_mcv_first_the_full_hold_starts_only_when_due_and_not_covered_and_after_the_release(monkeypatch):
    events = economy_holds(monkeypatch)
    hold_for_mcv, tuning = FullHold("mcv"), upkeep_off()

    covered, not_due = hold_for_mcv.holds(True, True, 0, tuning), hold_for_mcv.holds(False, False, 5, tuning)
    started, going_on = hold_for_mcv.holds(True, False, 10, tuning), hold_for_mcv.holds(True, False, 20, tuning)
    hold_for_mcv.settle(True, 20)
    engaged, released = hold_for_mcv.is_engaged(519, tuning), hold_for_mcv.holds(True, False, 519, tuning)

    assert (covered, not_due, started, going_on, engaged, released) == (False, False, True, True, True, False)
    assert not hold_for_mcv.is_engaged(520, tuning) and hold_for_mcv.holds(True, False, 520, tuning)
    assert events == [full_hold(10, "started"), full_hold(20, "ordered"), full_hold(520, "started")]


# -- the MCV first: what a full hold holds, and what still goes ahead --------------------------------------------------

MCV_DEPOT = ("fix", 26, 54)


@pytest.mark.parametrize("cash", [MCV_SHORT, 2000], ids=["1500", "2000"])
def test_mcv_first_during_a_full_hold_no_war_factory_refinery_claim_or_unit_is_ordered(monkeypatch, cash):
    events = economy_holds(monkeypatch)
    monkeypatch.setattr("ucla_agent.games.redalert.upkeep._is_claim_due", lambda ctx: True)

    held = ask(war_factory_agent(MCV_FIRST), mcv_first_base(HOLD_STARTS, MCV_DEPOT, cash=cash))
    free = ask(war_factory_agent(SECOND_YARD_ONLY), mcv_first_base(HOLD_STARTS, MCV_DEPOT, cash=cash))

    assert (builds(held), unit_trains(held)) == ([], [])
    assert builds(free) == ["weap"]
    assert events == [full_hold(HOLD_STARTS, "started")]


def test_mcv_first_during_a_full_hold_a_refinery_claim_the_money_covers_waits(monkeypatch):
    monkeypatch.setattr("ucla_agent.games.redalert.upkeep._is_claim_due", lambda ctx: True)
    # 100 a second is short of a second war factory's 125: the claim is the only building due.
    state = mcv_first_base(HOLD_STARTS, MCV_DEPOT, cash=2000)

    held = ask(tank_factories_agent(100, 0.9, tuning=MCV_FIRST), state)
    free = ask(tank_factories_agent(100, 0.9, tuning=SECOND_YARD_ONLY), state)

    assert builds(held) == [] and builds(free) == ["proc"]


@pytest.mark.parametrize(
    "drained, tuning, ordered",
    [(510, MCV_FIRST, ["powr"]), (490, MCV_FIRST, []), (490, SECOND_YARD_ONLY, ["powr"])],
    ids=["10 short, held", "10 over, held", "10 over, mcv_first off"],
)
def test_mcv_first_during_a_full_hold_a_power_plant_is_ordered_against_low_power_and_not_for_the_target(
    drained, tuning, ordered
):
    agent = upkeep_agent(tuning=tuning)
    economy = powered(500, drained, cash=MCV_SHORT, harvesters=4)

    orders = ask(agent, base_state("england", *MIXED_BASE["england"], units=harvesters(4), economy=economy))

    assert agent.ctx.full_hold is (tuning is MCV_FIRST) and builds(orders) == ordered


def test_mcv_first_base_defense_s_emergency_counter_is_ordered_while_the_full_hold_is_in_force(monkeypatch):
    events = economy_holds(monkeypatch)
    agent = new_agent(BaseDefenseManager(), UpkeepManager(), plan_selector=OpeningThen(HOLD), tuning=MCV_FIRST)
    ask(agent, mixed(cash=MCV_SHORT, tick=1000))
    in_force = agent.ctx.full_hold

    attacked = ask(agent, mixed(cash=MCV_SHORT, tick=1005, visible_enemies=RIFLES))

    counters = [entry.order for entry in agent.ledger.entries if entry.priority == Priority.EMERGENCY]
    assert in_force and {"cmd": "train", "item": "e1", "count": 3} in attacked
    assert {"cmd": "train", "item": "e1", "count": 3} in counters
    # The major threat the counter answers ends the hold in the same cycle: the MCV is no longer wanted.
    assert events == [full_hold(1000, "started"), full_hold(1005, "not_due")]


@pytest.mark.parametrize(
    "count, tuning, trained",
    [(1, MCV_FIRST, 1), (2, MCV_FIRST, 0), (2, SECOND_YARD_ONLY, 1)],
    ids=["1 for 2 refineries, held", "2 for 2 refineries, held", "2 for 2 refineries, mcv_first off"],
)
def test_mcv_first_during_a_full_hold_harvesters_are_replaced_up_to_one_per_refinery(count, tuning, trained):
    state = base_state("england", *MIXED_BASE["england"], units=harvesters(count), cash=MCV_SHORT)

    assert harvesters_trained(ask(upkeep_agent(tuning=tuning), state)) == trained


def russia(seconds: float) -> dict:
    """The holding Soviet base with a service depot at a game time, four harvesters and the MCV's money short."""
    tick = round(seconds * 25)
    return base_state("russia", *MIXED_BASE["russia"], units=harvesters(4), cash=MCV_SHORT, tick=tick)


def england(seconds: float, *buildings: tuple) -> dict:
    """The Allied base of the buildings given, or the holding one with a service depot, at a game time, with four
    harvesters and the MCV's money short."""
    tick = round(seconds * 25)
    return base_state("england", *(buildings or MIXED_BASE["england"]), units=harvesters(4), cash=MCV_SHORT, tick=tick)


WITH_SCOUT_SLOTS = dataclasses.replace(MCV_FIRST, scouting=scouting_off(scout_slots=True))
WITHOUT_THE_PILLBOX = tuple(spec for spec in MIXED_BASE["england"] if spec[0] != "pbox")


@pytest.mark.parametrize(
    "tuning, plan, states, queue, item",
    [
        (MCV_FIRST, HOLD, [england(400)], "Building", "dome"),
        (MCV_FIRST, HOLD, [russia(160)], "Defense", "tsla"),
        (WITH_SCOUT_SLOTS, HOLD, [russia(160)], "Building", "kenn"),
        (MCV_FIRST, HOLD, [england(160), england(161, *WITHOUT_THE_PILLBOX)], "Defense", "pbox"),
        (MCV_FIRST, PRESERVE, [england(160)], "Defense", "pbox"),
    ],
    ids=["radar", "tesla coil", "kennel", "lost pillbox replaced", "preserve defense"],
)
def test_mcv_first_during_a_full_hold_the_radar_kennel_and_upkeep_s_own_defenses_wait(
    tuning, plan, states, queue, item
):
    ordered = {}
    for mcv_first in (True, False):
        switched = dataclasses.replace(tuning, upkeep=dataclasses.replace(tuning.upkeep, mcv_first=mcv_first))
        agent = upkeep_agent(tuning=switched, plan_selector=OpeningThen(plan))
        agent.ctx.income = SpentLately(0, 0, income_per_s=100)
        ordered[mcv_first] = [built(ask(agent, state), queue) for state in states]

    assert [item in items for items in ordered[True]] == [False] * len(states)
    assert ordered[False][-1].count(item) == 1


@pytest.mark.parametrize(
    "tuning, first", [(MCV_FIRST, "mcv"), (SECOND_YARD_ONLY, "weap")], ids=["mcv_first", "mcv_first off"]
)
def test_mcv_first_once_the_money_covers_it_the_mcv_is_upkeep_s_first_order_of_the_cycle(tuning, first):
    # A war factory is due as well, and with the switch off its rule is asked before the MCV's.
    agent = war_factory_agent(tuning)

    ask(agent, mcv_first_base(HOLD_STARTS, MCV_DEPOT, cash=MCV_MONEY))

    proposed = [proposal.item for proposal in agent.ctx.production.proposed()]
    assert proposed[0] == first and {"mcv", "weap"} <= set(proposed)


SCOUT_SLOTS_ON = scouting_off(scout_slots=True)


@pytest.mark.parametrize(
    "tuning, mcvs, jeeps",
    [
        (dataclasses.replace(MCV_FIRST, scouting=SCOUT_SLOTS_ON), 1, 0),
        (dataclasses.replace(SECOND_YARD_ONLY, scouting=SCOUT_SLOTS_ON), 0, 2),
    ],
    ids=["mcv_first", "mcv_first off"],
)
def test_mcv_first_with_a_jeep_upgrade_due_and_the_mcv_due_first_the_vehicle_queue_gets_the_mcv(tuning, mcvs, jeeps):
    agent = new_agent(UpkeepManager(), ScoutingManager(), tuning=tuning)
    rifles = [situations.unit(600 + number, "e1", 18, 46) for number in range(2)]

    orders = ask(agent, base_state("england", *MIXED_BASE["england"], units=rifles, cash=5000))

    # With the switch off the harvester rule, asked before the MCV's, takes the Vehicle queue, and the jeeps follow.
    trained = dict(trains(orders))
    assert agent.ctx.groups.members(SCOUT) == [600, 601]
    assert (trained.get("mcv", 0), trained.get("jeep", 0)) == (mcvs, jeeps)


# -- the economy past four refineries -----------------------------------------------------------------------------------

GROWTH_UPKEEP = dataclasses.replace(upkeep_off(), refinery_growth=True, refineries_max=4, refineries_max_late=4)
# With the war factory's own gates on, so that one more war factory is due whenever the refinery is not.
GROWTH = dataclasses.replace(TANK_FACTORIES, upkeep=GROWTH_UPKEEP)
NO_GROWTH = dataclasses.replace(GROWTH, upkeep=dataclasses.replace(GROWTH_UPKEEP, refinery_growth=False))
# The holding base with four refineries north of the yard; a field west of the yard that one of the base can stand
# against and that no refinery stands against, and one 20 cells south that none can stand against.
GROWTH_BASE = (*HOLDING["england"], THIRD_REFINERY, ("proc", 34, 39))
WEST_GROWTH_FIELD = OrePatch(center=(11, 50), cells=frozenset((x, y) for x in (10, 11, 12) for y in (49, 50, 51)))
SOUTH_FAR_FIELD = OrePatch(center=(22, 68), cells=frozenset((x, y) for x in (21, 22, 23) for y in (67, 68, 69)))
GROWTH_TERRAIN = field_terrain(HOME_FIELD, WEST_GROWTH_FIELD, SOUTH_FAR_FIELD)
# A refinery's price, 1,400, and the production reserve.
GROWTH_PRICE_AND_RESERVE = 1400 + upkeep_off().production_cash_reserve


def growth_base(*extra: tuple, cash: int = 5000, tick: int = 10_000, count: int = 8, **more) -> dict:
    """The base with four refineries and the buildings given besides at a tick (400 s by default), `count`
    harvesters, the money given and no service depot on offer."""
    economy = powered(500, 100, cash=cash, harvesters=count)
    return base_state(
        "england",
        *GROWTH_BASE,
        *extra,
        units=harvesters(count),
        tick=tick,
        economy=economy,
        withheld=("fix",),
        **more,
    )


def growth_agent(tuning: Tuning = GROWTH, terrain: Terrain = GROWTH_TERRAIN, *extra: tuple):
    """An upkeep agent having seen the base once without money, on a map of this terrain, whose income from then on
    calls for one more war factory."""
    agent = upkeep_agent(tuning=tuning)
    ask(agent, growth_base(*extra, cash=0, tick=9975))
    agent.ctx.terrain = terrain
    agent.ctx.income = SpentLately(0, 0, income_per_s=130, busy_share=0.9)
    return agent


@pytest.mark.parametrize(
    "tuning, cash, built",
    [(GROWTH, 5000, ["proc"]), (GROWTH, GROWTH_PRICE_AND_RESERVE, ["proc"]), (NO_GROWTH, 5000, ["weap"])],
    ids=["on", "on, the price and the reserve", "off"],
)
def test_refinery_growth_with_4_refineries_and_an_untaken_field_a_refinery_goes_before_a_due_war_factory(
    tuning, cash, built
):
    agent = growth_agent(tuning)

    orders = ask(agent, growth_base(cash=cash))

    assert agent.ctx.production.standing("proc") == 4 and GROWTH_UPKEEP.refineries_max == 4
    assert builds(orders) == built


def test_refinery_growth_while_the_money_falls_short_of_the_refinery_and_the_reserve_none_is_ordered():
    agent = growth_agent()

    orders = ask(agent, growth_base(cash=GROWTH_PRICE_AND_RESERVE - 1))

    assert "proc" not in builds(orders)


@pytest.mark.parametrize(
    "terrain, extra",
    [
        (field_terrain(HOME_FIELD, SOUTH_FAR_FIELD), ()),
        (GROWTH_TERRAIN, tuple(("proc", 2 + 4 * number, 2) for number in range(4))),
    ],
    ids=["no untaken field a refinery can stand against", "8 refineries"],
)
def test_refinery_growth_with_no_untaken_standable_field_or_8_refineries_none_is_ordered(terrain, extra):
    agent = growth_agent(GROWTH, terrain, *extra)

    orders = ask(agent, growth_base(*extra))

    assert "proc" not in builds(orders) and "weap" in builds(orders)


@pytest.mark.parametrize("reserved, built", [(WEST_GROWTH_FIELD, "weap"), (None, "proc")], ids=["reserved", "not"])
def test_mcv_reserve_no_growth_refinery_is_due_for_the_only_untaken_field_while_it_is_reserved_for_the_mcv(
    reserved, built, monkeypatch
):
    monkeypatch.setattr(ForwardMcv, "reserved_field", lambda self, world, ctx: reserved)
    agent = growth_agent()

    orders = ask(agent, growth_base())

    assert agent.ctx.reserved_field == reserved and built in builds(orders)


def test_refinery_growth_the_growth_refinery_stands_against_the_untaken_field_with_a_refinery_field_event(
    match_record,
):
    agent = growth_agent()
    ordered = builds(ask(agent, growth_base()))
    ask(agent, growth_base(tick=10_025, production=[situations.job("Building", "proc")]))
    on_order = list(agent.ctx.asked_refinery_fields)

    (placed,) = places(ask(agent, growth_base(tick=10_050, production=[ready("Building", "proc")])))

    (event,) = match_record("refinery_field")
    assert ordered == ["proc"] and on_order == [WEST_GROWTH_FIELD]
    assert placement.ore_distance(box_at("proc", tuple(placed["at"])), WEST_GROWTH_FIELD) <= 2
    assert (event["rule"], event["field_center"], event["cell"]) == ("growth", [11, 50], placed["at"])


SIX_REFINERIES = (("proc", 2, 2), ("proc", 6, 2))


@pytest.mark.parametrize(
    "tuning, count, trained",
    [(GROWTH, 7, 1), (GROWTH, 8, 0), (NO_GROWTH, 8, 0), (NO_GROWTH, 7, 1)],
    ids=["on, 7", "on, 8", "off, 8", "off, 7"],
)
def test_harvester_target_with_6_refineries_and_harvesters_max_8_growth_on_or_off_trains_up_to_8(
    tuning, count, trained
):
    agent = upkeep_agent(tuning=tuning)

    orders = ask(agent, growth_base(*SIX_REFINERIES, count=count))

    assert agent.ctx.production.standing("proc") == 6 and tuning.upkeep.harvesters_max == 8
    assert harvesters_trained(orders) == trained


def saving_agent(tuning: Tuning = GROWTH):
    """An upkeep agent with its own income estimate that has seen the base with four refineries without money twice,
    the second time at tick 9,975 on the map with the untaken field west of the yard: a saving starts then."""
    agent = upkeep_agent(tuning=tuning)
    ask(agent, growth_base(cash=0, tick=SAVING_STARTS - 25))
    agent.ctx.terrain = GROWTH_TERRAIN
    ask(agent, growth_base(cash=0, tick=SAVING_STARTS))
    return agent


SAVING_STARTS = 9975
SAVING_TICKS = upkeep_off().growth_saving_seconds * 25


# Short of the refinery and the reserve, and enough for a tank above the reserve.
SHORT_OF_A_REFINERY = 1200


@pytest.mark.parametrize("tuning, held", [(GROWTH, True), (NO_GROWTH, False)], ids=["on", "off"])
def test_refinery_growth_short_of_its_price_the_unit_queues_leave_the_due_growth_refinery_s_price_alone(tuning, held):
    agent = saving_agent(tuning)

    orders = ask(agent, growth_base(cash=SHORT_OF_A_REFINERY))

    assert builds(orders) == [] and (unit_trains(orders) == []) is held


@pytest.mark.parametrize(
    "cash, trains_left", [(GROWTH_PRICE_AND_RESERVE, False), (GROWTH_PRICE_AND_RESERVE + 800, True)]
)
def test_refinery_growth_the_growth_refinery_is_ordered_once_the_money_covers_it_and_units_get_what_is_above(
    cash, trains_left
):
    agent = saving_agent()

    orders = ask(agent, growth_base(cash=cash))

    assert builds(orders) == ["proc"] and (unit_trains(orders) != []) is trains_left


def test_refinery_growth_a_saving_lasts_60_s_at_most_and_the_next_starts_60_s_after_it_ran_out(match_record):
    agent = saving_agent()
    ends = SAVING_STARTS + SAVING_TICKS

    for tick in (ends - 1, ends, ends + SAVING_TICKS - 1, ends + SAVING_TICKS, ends + 2 * SAVING_TICKS):
        ask(agent, growth_base(cash=0, tick=tick))

    events = match_record("refinery_field")
    assert [event["tick"] for event in events] == [ends, ends + 2 * SAVING_TICKS]
    assert events[0] == {
        "tick": ends,
        "cell": None,
        "rule": "growth",
        "field_center": [11, 50],
        "field_cells": 9,
        "served_by": 0,
        "ore_distance_cells": None,
        "saving_timed_out": True,
    }


def test_refinery_growth_once_a_saving_ran_out_the_unit_queues_spend_again(match_record):
    agent = saving_agent()

    held = ask(agent, growth_base(cash=SHORT_OF_A_REFINERY, tick=SAVING_STARTS + SAVING_TICKS - 1))
    free = ask(agent, growth_base(cash=SHORT_OF_A_REFINERY, tick=SAVING_STARTS + SAVING_TICKS))

    assert unit_trains(held) == [] and unit_trains(free) != []
    assert [event["saving_timed_out"] for event in match_record("refinery_field")] == [True]


def test_refinery_growth_with_economy_first_on_a_due_growth_refinery_holds_the_units_as_a_claim_would(monkeypatch):
    events = economy_holds(monkeypatch)
    upkeep = dataclasses.replace(GROWTH_UPKEEP, economy_first=True)
    agent = saving_agent(dataclasses.replace(GROWTH, upkeep=upkeep))

    assert events == [hold(SAVING_STARTS, "proc", "started")]


def endgame_saving_agent():
    """`saving_agent` with its refinery rule at hand: a growth saving started at tick 9,975."""
    manager = UpkeepManager()
    agent = new_agent(manager, plan_selector=OpeningThen(HOLD), tuning=GROWTH)
    ask(agent, growth_base(cash=0, tick=SAVING_STARTS - 25))
    agent.ctx.terrain = GROWTH_TERRAIN
    ask(agent, growth_base(cash=0, tick=SAVING_STARTS))
    return agent, manager._refineries


def growth_base_with_seconds_left(seconds_left: float, **more) -> dict:
    state = growth_base(**more)
    state["time"]["remaining_seconds"] = seconds_left
    return state


# The money below the end-of-match spending's floor of 300.
BELOW_THE_SPEND_FLOOR = 250


@pytest.mark.parametrize(
    "seconds_left, cash, set_aside",
    [(300, SHORT_OF_A_REFINERY, 1400), (100, SHORT_OF_A_REFINERY, 0), (100, BELOW_THE_SPEND_FLOOR, 0)],
    ids=["300 s left", "100 s left", "100 s left, money below the spending's floor"],
)
def test_growth_saving_endgame_a_saving_in_progress_ends_once_the_last_minutes_begin_whatever_the_money(
    seconds_left, cash, set_aside, match_record
):
    agent, refineries = endgame_saving_agent()

    ask(agent, growth_base_with_seconds_left(seconds_left, cash=cash, tick=SAVING_STARTS + 25))

    assert refineries.is_growth_due and refineries.growth_set_aside(agent.world, agent.ctx) == set_aside
    assert match_record("refinery_field") == []


def test_growth_saving_endgame_no_saving_starts_again_in_the_last_minutes_and_the_refinery_is_still_ordered():
    agent, refineries = endgame_saving_agent()
    ask(agent, growth_base_with_seconds_left(100, cash=SHORT_OF_A_REFINERY, tick=SAVING_STARTS + 25))

    held = []
    # A cycle later, and long after a saving that ran out would have let the next one start.
    for tick in (SAVING_STARTS + 50, SAVING_STARTS + 2000):
        ask(agent, growth_base_with_seconds_left(80, cash=SHORT_OF_A_REFINERY, tick=tick))
        held.append(refineries.growth_set_aside(agent.world, agent.ctx))
    ordered = ask(agent, growth_base_with_seconds_left(60, cash=5000, tick=SAVING_STARTS + 2025))

    assert held == [0, 0] and "proc" in builds(ordered)


def square_ore(center_x: int, center_y: int) -> list[tuple[int, int]]:
    """The nine ore cells of a field of three by three cells round its center."""
    return [(x, y) for y in range(center_y - 1, center_y + 2) for x in range(center_x - 1, center_x + 2)]


# Six fields round the own spawn besides its own north of it, each of which the base can come to stand against.
SIX_FIELDS_ORE = [
    *square_ore(36, 46),
    *square_ore(9, 50),
    *square_ore(34, 61),
    *square_ore(13, 31),
    *square_ore(22, 61),
    *square_ore(34, 33),
]
GROWTH_BY_TICK = 15_000
GROWTH_REFINERIES = 5
TICKS_PER_MINUTE = 60 * 25


class GrowthWatch:
    """What a whole match shows of the economy's growth: the tick each count of own refineries first stood, and the
    income per minute the agent measured at each minute."""

    def __init__(self) -> None:
        self.first_tick: dict[int, int] = {}
        self.income_per_minute: list[tuple[int, int | None]] = []

    def __call__(self, agent) -> None:
        tick, income = agent.world.tick, agent.ctx.income.income_per_s
        refineries = sum(1 for building in agent.world.buildings.values() if building.type == "proc")
        self.first_tick.setdefault(refineries, tick)
        if tick // TICKS_PER_MINUTE > len(self.income_per_minute) - 1:
            self.income_per_minute.append((tick, None if income is None else round(income * 60)))


@pytest.fixture(scope="module")
def growth_match() -> tuple[WholeMatch, GrowthWatch]:
    """A whole fake match on a map with six fields round the own spawn besides its own, played with the economy's
    growth past four refineries on."""
    watch = GrowthWatch()
    tuning = tuning_off(upkeep=dataclasses.replace(upkeep_off(), refinery_growth=True))
    match = play_whole_match(tuning, game_config=with_ore(FakeRedAlert().game_config, SIX_FIELDS_ORE), watch=watch)
    return match, watch


def test_refinery_growth_a_whole_fake_match_runs_without_an_error_and_has_5_refineries_by_tick_15000(growth_match):
    match, watch = growth_match
    growth = [event for event in match.data_of("refinery_field") if event["rule"] == "growth"]

    # The fixture's income, so that a saving that ran out can be explained.
    print("income per minute (tick, credits):", watch.income_per_minute)
    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.compute_p95_ms <= COMPUTE_P95_MS
    assert watch.first_tick[GROWTH_REFINERIES] <= GROWTH_BY_TICK
    assert growth and all(event["ore_distance_cells"] <= 2 for event in growth if "saving_timed_out" not in event)


# -- harvesters moved off a dry field, spread by load -------------------------------------------------------------------

BY_LOAD = dataclasses.replace(FRESH_ORE, upkeep=dataclasses.replace(FRESH_ORE.upkeep, fresh_ore_by_load=True))
# The dry field is the home field. Field A east of the base with two refineries against it and four harvesters at work
# by it; field B south-east with one refinery and none; field C north-west with no refinery, the farthest.
FIELD_A = EAST_ORE
A_REFINERIES = (("proc", 36, 45), ("proc", 32, 50))
FIELD_B = OrePatch(center=(36, 56), cells=frozenset((x, y) for x in (35, 36, 37) for y in (55, 56, 57)))
B_REFINERY = (("proc", 39, 52),)
FIELD_C = NORTH_WEST_ORE
AT_WORK_BY_A = (36, 42)
MOVE_TICKS = [4000, 4005, 4010, 4015]


def load_run(terrain: Terrain, refineries: tuple, idle: range, tuning: Tuning = BY_LOAD, busy_by_a: int = 4):
    """The harvest orders of each cycle in which the harvesters `idle` stand by the home field's refinery and
    `busy_by_a` are at work by field A, in a base with the refineries given besides its own two; and the
    `harvesters_moved` events."""
    logged: list[dict] = []
    agent = new_agent(UpkeepManager(), plan_selector=OpeningThen(HOLD), tuning=tuning)
    ask(agent, base_state("england", *TWO_REFINERIES, *refineries, tick=3990))
    agent.ctx.terrain = terrain
    units = [
        *idle_by_the_refinery(idle),
        *(situations.unit(400 + number, "harv", *AT_WORK_BY_A, idle=False) for number in range(busy_by_a)),
    ]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(records, "log", lambda kind, data: logged.append(data) if kind == "harvesters_moved" else None)
        sent = [
            harvest_orders(ask(agent, base_state("england", *TWO_REFINERIES, *refineries, tick=tick, units=units)))
            for tick in MOVE_TICKS
        ]
    return sent, logged


def sent_to(orders: list[dict]) -> dict[int, tuple[int, int]]:
    """The cell each harvester is sent to by orders that carry one."""
    return {unit: tuple(order["to"]) for order in orders if "to" in order for unit in order["units"]}


def field_of_cell(cell: tuple[int, int], *fields: OrePatch) -> OrePatch:
    return next(field for field in fields if cell in field.cells)


def test_fresh_ore_by_load_two_go_to_the_field_under_its_share_and_the_third_to_the_next_under_its_share():
    terrain = fresh_ore_terrain(FIELD_A, FIELD_B, FIELD_C)
    sent, logged = load_run(terrain, (*A_REFINERIES, *B_REFINERY), range(300, 303))

    fields = {unit: field_of_cell(cell, FIELD_A, FIELD_B, FIELD_C) for unit, cell in sent_to(sent[3]).items()}
    assert fields == {300: FIELD_B, 301: FIELD_B, 302: FIELD_C}
    assert logged == [
        {
            "tick": 4015,
            "from_field_center": [22, 40],
            "to_field_center": [36, 56],
            "harvester_ids": [300, 301, 302],
            "reason": "dry",
            "to_field_centers": [[36, 56], [36, 56], [8, 30]],
        }
    ]


def test_fresh_ore_by_load_with_the_switch_off_all_three_go_to_the_best_field_as_today():
    terrain = fresh_ore_terrain(FIELD_A, FIELD_B, FIELD_C)
    sent, logged = load_run(terrain, (*A_REFINERIES, *B_REFINERY), range(300, 303), tuning=FRESH_ORE)

    assert set(sent_to(sent[3]).values()) == {FIELD_A.nearest_cell(BY_THE_REFINERY)}
    assert [event["to_field_center"] for event in logged] == [[33, 47]] and "to_field_centers" not in logged[0]


def test_fresh_ore_by_load_a_field_with_no_refinery_takes_at_most_2():
    # Fields B and C with no refinery: two to the nearer, B, and two to C.
    terrain = fresh_ore_terrain(FIELD_B, FIELD_C)
    sent, _ = load_run(terrain, (), range(300, 304), busy_by_a=0)

    fields = Counter(field_of_cell(cell, FIELD_B, FIELD_C) for cell in sent_to(sent[3]).values())
    assert fields == Counter({FIELD_B: 2, FIELD_C: 2})


def test_fresh_ore_by_load_with_every_field_at_its_share_today_s_field_takes_them_all():
    terrain = fresh_ore_terrain(FIELD_A)
    by_load, _ = load_run(terrain, A_REFINERIES, range(300, 303))
    today, _ = load_run(terrain, A_REFINERIES, range(300, 303), tuning=FRESH_ORE)

    assert sent_to(by_load[3]) == sent_to(today[3]) and set(sent_to(today[3])) == {300, 301, 302}


# Refineries east and west of field B, their footprints 4 cells from its ore: inside the reach a refinery serves a
# field from, `refinery_serves_cells` of [placement] (5), beyond `refinery_ore_cells` (2).
B_REFINERIES_4_CELLS_OFF = (("proc", 42, 56), ("proc", 30, 56))


@pytest.mark.parametrize(("refineries", "share"), [(B_REFINERIES_4_CELLS_OFF[:1], 2), (B_REFINERIES_4_CELLS_OFF, 4)])
def test_fresh_ore_by_load_a_refinery_4_cells_from_a_field_s_ore_within_refinery_serves_cells_counts_toward_its_share(
    refineries, share
):
    terrain = fresh_ore_terrain(FIELD_B, FIELD_C)
    agent = new_agent(UpkeepManager(), plan_selector=OpeningThen(HOLD), tuning=BY_LOAD)
    ask(agent, base_state("england", *TWO_REFINERIES, *refineries, tick=3990))
    agent.ctx.terrain = terrain
    added = [building for building in agent.world.buildings.values() if ("proc", building.x, building.y) in refineries]
    reach = BY_LOAD.placement
    distances = {placement.ore_distance(box_at("proc", footprint_cell(building)), FIELD_B) for building in added}

    sent, _ = load_run(terrain, refineries, range(300, 304), busy_by_a=0)

    fields = Counter(field_of_cell(cell, FIELD_B, FIELD_C) for cell in sent_to(sent[3]).values())
    assert len(added) == len(refineries) and distances == {4}
    assert reach.refinery_ore_cells < 4 <= reach.refinery_serves_cells == 5
    assert harvester_room.serving_counts(agent.world, agent.ctx)[FIELD_B] == len(refineries)
    assert fields[FIELD_B] == share and fields.total() == 4


# Ore east and west of the own spawn besides its own, for harvesters moved off a dry field.
EAST_AND_WEST_ORE = [*FIELD_A.cells, *FIELD_C.cells]
DRY_FROM_SECONDS = 300


def idle_by_the_first_refinery(game: FakeRedAlert) -> None:
    """Leave the two harvesters nearest the first own refinery idle: their re-orders make its field run dry."""
    refinery = next(building for building in game.own_buildings() if building["type"] == "proc")
    harvesters = [unit for unit in game.own_units() if unit["type"] == "harv"]
    nearest = sorted(
        harvesters, key=lambda unit: cells.distance((unit["x"], unit["y"]), (refinery["x"], refinery["y"]))
    )
    for unit in nearest[:2]:
        game.idle_own(unit["id"])


def test_fresh_ore_by_load_a_whole_fake_match_with_the_switch_on_runs_without_an_error_within_the_compute_budget():
    tuning = dataclasses.replace(tuning_off(), upkeep=BY_LOAD.upkeep)
    game_config = with_ore(FakeRedAlert().game_config, EAST_AND_WEST_ORE)
    scenario = [fake_game.at_seconds(DRY_FROM_SECONDS, idle_by_the_first_refinery)]
    match = play_whole_match(tuning, game_config=game_config, scenario=scenario)
    moved = match.data_of("harvesters_moved")

    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.compute_p95_ms <= COMPUTE_P95_MS
    assert moved and all(len(event["to_field_centers"]) == len(event["harvester_ids"]) for event in moved)


# -- the MCV's yard box kept clear --------------------------------------------------------------------------------------

# An MCV's site, and the box its yard unfolds on there: x 68 to 70, y 50 to 53.
MCV_SITE = (69, 51)
MCV_YARD_CELLS = {(x, y) for x in (68, 69, 70) for y in (50, 51, 52, 53)}


@pytest.mark.parametrize(
    "yard, queue, item",
    [((65, 54), "Defense", "pbox"), ((71, 51), "Building", "powr")],
    ids=["pillbox", "power plant"],
)
@pytest.mark.parametrize("kept", [True, False], ids=["mcv site", "no mcv"])
def test_mcv_footprint_with_an_mcv_site_at_69_51_no_building_takes_a_cell_of_its_yard_box(
    yard, queue, item, kept, monkeypatch
):
    # Bases whose pillbox, or power plant, stands in the box when nothing keeps it.
    monkeypatch.setattr(ForwardMcv, "site_box", lambda self, world: expansion.yard_box(MCV_SITE) if kept else None)
    agent = upkeep_agent(tuning=tuning_off())
    base = (("fact", *yard), ("powr", yard[0] + 4, yard[1]))

    (order,) = places(ask(agent, base_state("england", *base, production=[ready(queue, item)], base_center=[*yard])))

    assert expansion.yard_box(MCV_SITE).cells() == sorted(MCV_YARD_CELLS, key=lambda cell: (cell[1], cell[0]))
    assert box_at(item, tuple(order["at"])).holds_any(MCV_YARD_CELLS) is not kept


# -- no refinery for the field reserved for the MCV's site ---------------------------------------------------------------


@pytest.mark.parametrize(
    "tuning, fields",
    [(FIELD_CAP, (HOME_FIELD, REACHABLE_LANE_FIELD)), (NO_CAP, (REACHABLE_LANE_FIELD,))],
    ids=["with the cap, the home field full", "without the cap"],
)
@pytest.mark.parametrize("reserved", [True, False], ids=["reserved", "not reserved"])
def test_reserved_refinery_with_the_only_reachable_field_reserved_no_refinery_is_ordered(
    tuning, fields, reserved, monkeypatch
):
    field = REACHABLE_LANE_FIELD if reserved else None
    monkeypatch.setattr(ForwardMcv, "reserved_field", lambda self, world, ctx: field)
    agent = capped_agent(tuning, field_terrain(*fields))

    orders = ask(agent, grown(cash=2000, seconds=300))

    assert agent.ctx.reserved_field == field and ("proc" in builds(orders)) is not reserved


# A field west of the yard that a refinery of the base can stand against as well.
WEST_OF_THE_YARD = OrePatch(center=(12, 47), cells=frozenset((x, y) for x in (11, 12, 13) for y in (46, 47, 48)))


def test_reserved_refinery_with_another_field_to_stand_against_a_refinery_is_ordered(monkeypatch):
    monkeypatch.setattr(ForwardMcv, "reserved_field", lambda self, world, ctx: REACHABLE_LANE_FIELD)
    agent = capped_agent(NO_CAP, field_terrain(HOME_FIELD, REACHABLE_LANE_FIELD, WEST_OF_THE_YARD))

    assert builds(ask(agent, grown(cash=2000, seconds=300))) == ["proc"]


# -- a finished building held unplaced too long -------------------------------------------------------------------------

READY_REFINERY = [ready("Building", "proc")]


def held_refinery_agent(monkeypatch):
    """An agent under the cap whose refinery, ready from 300 s, has no field to stand against but the lane field
    reserved for the MCV's site, so that it is held back."""
    monkeypatch.setattr(ForwardMcv, "reserved_field", lambda self, world, ctx: REACHABLE_LANE_FIELD)
    return capped_agent(FIELD_CAP, field_terrain(HOME_FIELD, REACHABLE_LANE_FIELD))


def every_2_s(agent, first: int, last: int, **more) -> list[list[dict]]:
    """The orders of the cycles every 2 seconds from `first` to `last`, a refinery ready all along."""
    return [
        ask(agent, grown(cash=2000, seconds=seconds, production=READY_REFINERY, **more))
        for seconds in range(first, last + 1, 2)
    ]


def test_held_cancel_a_refinery_held_500_ticks_is_cancelled_and_the_next_building_is_ordered_within_50_ticks(
    monkeypatch, match_record
):
    agent = held_refinery_agent(monkeypatch)
    # The service depot is on offer: it is the next building, once the Building queue is free.
    held = every_2_s(agent, 300, 318, withheld=())
    (cancelled,) = every_2_s(agent, 320, 320, withheld=())

    after = ask(agent, grown(cash=2000, seconds=322, withheld=()))

    assert [places(orders) + with_command(orders, "cancel", "build") for orders in held] == [[]] * 10
    assert with_command(cancelled, "cancel") == [{"cmd": "cancel", "item": "proc"}] and builds(after) == ["fix"]
    assert match_record("creep_placement") == [
        {
            "tick": 8000,
            "building": "proc",
            "cell": None,
            "reason": "held_cancelled",
            "order": "cancel",
            "held_ticks": 500,
        }
    ]


def test_held_cancel_without_a_cancel_order_the_refinery_is_placed_on_the_free_place_nearest_the_yard(
    monkeypatch, match_record
):
    agent = held_refinery_agent(monkeypatch)
    monkeypatch.delitem(batch.ORDER_FIELDS, "cancel")
    every_2_s(agent, 300, 318)

    (orders,) = every_2_s(agent, 320, 320)

    nearest = placement.candidate_cells("proc", agent.world, agent.ctx.terrain, agent.ctx.tuning.placement)[0]
    assert places(orders) == [{"cmd": "place", "item": "proc", "at": list(nearest)}]
    (event,) = match_record("creep_placement")
    assert (event["order"], event["cell"], event["held_ticks"]) == ("place", list(nearest), 500)


def test_held_cancel_a_pillbox_asked_onto_the_map_again_and_again_is_cancelled_after_500_ticks():
    agent = upkeep_agent()
    pillbox = [ready("Defense", "pbox")]

    asked = [ask(agent, grown(cash=0, seconds=seconds, production=pillbox)) for seconds in range(300, 322, 2)]

    assert [len(places(orders)) for orders in asked[:10]] == [1] * 10 and places(asked[10]) == []
    assert with_command(asked[10], "cancel") == [{"cmd": "cancel", "item": "pbox"}]


# -- a whole match with the MCV stuck and its field reserved ------------------------------------------------------------

# A field east of the base, which no refinery of a base of two refineries serves: the MCV's site, its place (33, 50);
# and one west of it, which the opening's second refinery serves.
EAST_SITE_FIELD = [(x, y) for x in (32, 33, 34) for y in (46, 47, 48)]
WEST_FIELD = [(x, y) for x in (11, 12, 13) for y in (46, 47, 48)]
# A SAM site 10 cells from the MCV's place: near enough to keep the MCV waiting at home with its field reserved, beyond
# the 9.5 cells it covers, and, firing at aircraft alone, no enemy building that makes the site not qualify.
SAM_BY_THE_SITE = (43, 50)


class StuckMcv:
    """The watched game's stall played again: in the cycle the MCV's site field is first reserved, the game makes the
    agent a refinery, as a rule that ordered one for that field did, and an enemy SAM site shows by the site, which keeps
    the MCV waiting at home with its field reserved. Follows, for each item, how long a finished building waits."""

    def __init__(self) -> None:
        self.game: FakeRedAlert | None = None
        self.queued_at: int | None = None
        self.longest_wait = 0
        self._ready_since: dict[str, int] = {}

    def keep(self, game: FakeRedAlert) -> None:
        self.game = game

    def watch(self, agent) -> None:
        tick = agent.world.tick
        if agent.ctx.reserved_field is not None and self.queued_at is None:
            self.queued_at = tick
            self.game.queue_item("proc")
            self.game.place_enemy("sam", *SAM_BY_THE_SITE, visible=True)
        self._ready_since = {item: self._ready_since.get(item, tick) for item in agent.world.ready_to_place}
        self.longest_wait = max([self.longest_wait, *(tick - since for since in self._ready_since.values())])


@pytest.fixture(scope="module")
def stuck_mcv_match() -> tuple[WholeMatch, StuckMcv]:
    """A whole fake match with two refineries at most, the second yard, the safe site and the reserved site field on,
    on a map with the two fields, the watched game's stall played again (`StuckMcv`)."""
    stuck = StuckMcv()
    tuning = tuning_off(
        upkeep=dataclasses.replace(upkeep_off(), refineries_max=2, refineries_max_late=2),
        expansion=expansion_off(second_yard=True, mcv_safe_site=True, mcv_reserve_field=True),
    )
    game_config = with_ore(FakeRedAlert().game_config, EAST_SITE_FIELD + WEST_FIELD)
    match = play_whole_match(tuning, game_config=game_config, scenario=[fake_game.at(0, stuck.keep)], watch=stuck.watch)
    return match, stuck


def test_held_cancel_a_whole_fake_match_with_the_mcv_stuck_and_its_field_reserved_keeps_ordering_buildings(
    stuck_mcv_match,
):
    match, stuck = stuck_mcv_match
    (held,) = [event for event in match.data_of("creep_placement") if event.get("reason") == "held_cancelled"]
    waited = [event for event in match.data_of("expansion") if event["phase"] in ("mcv_waiting", "deployed_home")]
    built = [entry.sent_tick for entry in match.agent.ledger.entries if entry.order["cmd"] == "build"]

    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.compute_p95_ms <= COMPUTE_P95_MS
    assert [event["reason"] for event in waited] == ["defense_near_site", "waited_too_long"]
    assert (held["building"], held["order"], held["held_ticks"]) == ("proc", "cancel", 500)
    assert waited[0]["tick"] < held["tick"] < waited[1]["tick"] and stuck.longest_wait <= 500 + 25
    assert any(tick > held["tick"] for tick in built)


# -- vehicle repair: the Allied mechanics and medics ------------------------------------------------

VEHICLE_REPAIR_ON = dataclasses.replace(
    TWO_REFINERIES_ONLY, combat=dataclasses.replace(TWO_REFINERIES_ONLY.combat, vehicle_repair=True)
)
# The shipped medics_max is 0; the medic tests set 2.
MEDICS_UP_TO_2 = dataclasses.replace(
    VEHICLE_REPAIR_ON, composition=dataclasses.replace(VEHICLE_REPAIR_ON.composition, medics_max=2)
)


def support_trains(units: list[dict], faction: str = "england", tuning: Tuning = VEHICLE_REPAIR_ON, **more) -> list:
    """The unit trains of an upkeep agent's first answer past the opening, for a base with a service depot."""
    return unit_trains(ask(upkeep_agent(tuning=tuning), mixed(faction, units=units, **more)))


def test_vehicle_repair_allies_with_a_service_depot_and_6_main_vehicles_train_a_mechanic_ahead_of_the_unit_queues():
    assert support_trains(soldiers("2tnk", 6)) == [("mech", 1), ("2tnk", 1)]


@pytest.mark.parametrize("standing, trained_mechanics", [(1, 1), (2, 0)], ids=["one standing", "two standing"])
def test_vehicle_repair_with_6_tanks_mechanics_are_trained_up_to_2(standing, trained_mechanics):
    units = [*soldiers("2tnk", 6), *soldiers("mech", standing, first_id=500)]

    trained_now = support_trains(units)

    assert trained_now.count(("mech", 1)) == trained_mechanics


def test_vehicle_repair_no_mechanic_with_1_main_vehicle_without_a_service_depot_for_the_soviets_or_with_it_off():
    one = support_trains(soldiers("2tnk", 1))
    no_depot = unit_trains(
        ask(
            upkeep_agent(tuning=VEHICLE_REPAIR_ON),
            base_state("england", *HOLDING["england"], units=[*harvesters(4), *soldiers("2tnk", 6)], cash=5000),
        )
    )
    soviets = support_trains(soldiers("3tnk", 6), faction="russia")
    switched_off = support_trains(soldiers("2tnk", 6), tuning=TWO_REFINERIES_ONLY)

    assert [entry for entry in [*one, *no_depot, *soviets, *switched_off] if entry[0] in ("mech", "medi")] == []


@pytest.mark.parametrize(
    "standing, trained_mechanics", [(0, 1), (1, 1), (2, 0)], ids=["none standing", "one standing", "two standing"]
)
def test_repair_near_with_a_depot_and_6_main_vehicles_mechanics_are_trained_up_to_2_mobile_and_none_at_base(
    standing, trained_mechanics
):
    units = [*soldiers("2tnk", 6), *soldiers("mech", standing, first_id=500)]

    trained_now = support_trains(units)

    assert trained_now.count(("mech", 1)) == trained_mechanics


@pytest.mark.parametrize("tanks, wanted", [(2, 0), (3, 1), (5, 1), (6, 2), (9, 3), (12, 3), (20, 3)])
def test_army_build_mechanics_wanted_are_0_at_2_tanks_1_at_3_2_at_6_and_3_at_9_and_12_and_never_more(tanks, wanted):
    # One fewer than wanted standing trains one more; as many as wanted standing trains none.
    short = [*soldiers("2tnk", tanks), *soldiers("mech", max(0, wanted - 1), first_id=500)]
    full = [*soldiers("2tnk", tanks), *soldiers("mech", wanted, first_id=500)]

    assert (("mech", 1) in support_trains(short), ("mech", 1) in support_trains(full)) == (wanted > 0, False)


def test_army_build_mechanics_count_main_s_tanks_not_its_artillery():
    units = [*soldiers("2tnk", 2), *soldiers("arty", 4, first_id=520)]

    assert ("mech", 1) not in support_trains(units)
    assert ("mech", 1) in support_trains([*units, *soldiers("2tnk", 1, first_id=540)])


@pytest.mark.parametrize("rifles, medics", [(5, 0), (6, 1), (12, 2), (30, 2)])
def test_vehicle_repair_a_medic_for_every_6_main_infantry_up_to_2_once_the_mechanics_stand(rifles, medics):
    # The home guard takes the first five rifles; the rest are main's.
    guard = Tuning().opening.guard_rifles
    standing = [*soldiers("2tnk", 6), *soldiers("mech", 2, first_id=500), *soldiers("e1", guard + rifles, first_id=600)]
    one_medic = [*standing, *soldiers("medi", 1, first_id=700)]

    first = support_trains(standing, tuning=MEDICS_UP_TO_2)
    second = support_trains(one_medic, tuning=MEDICS_UP_TO_2)

    assert (("medi", 1) in first, ("medi", 1) in second) == (medics >= 1, medics >= 2)


def test_no_medics_with_the_shipped_tuning_and_12_main_infantry_no_medic_is_trained():
    # The home guard takes the first five rifles; the 12 after them are main's, 2 medics' worth up to 2.
    guard = Tuning().opening.guard_rifles
    units = [*soldiers("2tnk", 6), *soldiers("mech", 2, first_id=500), *soldiers("e1", guard + 12, first_id=600)]
    shipped_medics = dataclasses.replace(
        VEHICLE_REPAIR_ON,
        composition=dataclasses.replace(VEHICLE_REPAIR_ON.composition, medics_max=Tuning().composition.medics_max),
    )

    up_to_2 = support_trains(units, tuning=MEDICS_UP_TO_2)
    trained = [support_trains(units, tuning=tuning) for tuning in (shipped_medics, Tuning())]

    assert (Tuning().composition.medics_max, Tuning().combat.vehicle_repair) == (0, True)
    assert ("medi", 1) in up_to_2
    assert [("medi", 1) in trains for trains in trained] == [False, False]


def test_vehicle_repair_no_support_unit_while_the_money_does_not_cover_it_and_the_reserve():
    assert ("mech", 1) not in support_trains(soldiers("2tnk", 6), cash=500 + 299)


# -- the harvester target -------------------------------------------------------------------------------------------

# The switches-off tuning with the shipped harvester keys: 3 a refinery up to 22, 10 cells a harvester, harvesters first;
# the harvester goes first up to the target (3 a refinery), so that the turns leave the target's tests alone.
HARVESTER_TARGET = dataclasses.replace(
    TWO_REFINERIES_ONLY,
    upkeep=dataclasses.replace(
        TWO_REFINERIES_ONLY.upkeep,
        harvesters_per_refinery=3,
        harvesters_max=22,
        harvester_cells_per=10,
        harvesters_first=True,
        harvesters_first_per_refinery=3,
    ),
)
HARVESTERS_FIRST_OFF = dataclasses.replace(
    HARVESTER_TARGET, upkeep=dataclasses.replace(HARVESTER_TARGET.upkeep, harvesters_first=False)
)


def refineries_and_war_factory(refineries: int) -> tuple:
    """The opening's base with so many refineries, the extra ones in a row south of it, and a war factory."""
    more = [("proc", 30 + 4 * number, 60) for number in range(refineries - 1)]
    return (YARD, POWER, BARRACKS, REFINERY, *more, WAR_FACTORY)


@pytest.mark.parametrize("count, trained", [(10, 1), (14, 1), (15, 0)], ids=["10", "14", "15"])
def test_harvester_target_5_refineries_give_15_and_a_harvester_is_queued_below_it(count, trained):
    agent = upkeep_agent(tuning=HARVESTER_TARGET)

    orders = ask(agent, base_state("england", *refineries_and_war_factory(5), units=harvesters(count)))

    assert harvester_target(3, agent.ctx) == 15
    assert harvesters_trained(orders) == trained


@pytest.mark.parametrize("count, trained", [(21, 1), (22, 0), (23, 0)], ids=["21", "22", "23"])
def test_harvester_target_8_refineries_give_22_not_24(count, trained):
    agent = upkeep_agent(tuning=HARVESTER_TARGET)

    orders = ask(agent, base_state("england", *refineries_and_war_factory(8), units=harvesters(count)))

    assert harvester_target(3, agent.ctx) == 22
    assert harvesters_trained(orders) == trained


def test_harvester_target_a_harvester_in_production_counts_against_it():
    in_production = [situations.job("Vehicle", "harv")]
    state = base_state("england", *refineries_and_war_factory(5), units=harvesters(14), production=in_production)

    assert harvesters_trained(ask(upkeep_agent(tuning=HARVESTER_TARGET), state)) == 0


# A field of 32 ore cells north of the opening's refinery, which serves it, and one of 98 east of the base, served by
# the four refineries west and south of it: a share of 2 harvesters and one of 8, 2 for each refinery serving it.
SMALL_FIELD = OrePatch(center=(25, 34), cells=frozenset((x, y) for x in range(22, 30) for y in range(33, 37)))
LARGE_FIELD = OrePatch(center=(50, 39), cells=frozenset((x, y) for x in range(44, 58) for y in range(36, 43)))
TWO_FIELD_BASE = (
    YARD,
    POWER,
    BARRACKS,
    REFINERY,
    WAR_FACTORY,
    ("proc", 40, 33),
    ("proc", 40, 38),
    ("proc", 40, 43),
    ("proc", 46, 44),
)


def field_harvesters(small: int, large: int) -> list[dict]:
    """So many harvesters at work on the small field and on the large one, numbered from 300."""
    on_small = [situations.unit(300 + number, "harv", 23 + number, 34) for number in range(small)]
    on_large = [situations.unit(300 + small + number, "harv", 44 + number, 39) for number in range(large)]
    return [*on_small, *on_large]


def two_field_agent(units: list[dict]):
    """A harvester-target agent that has seen the two-field base with these harvesters and one more in production, on
    the map of the two fields."""
    agent = upkeep_agent(tuning=HARVESTER_TARGET)
    in_production = [situations.job("Vehicle", "harv")]
    ask(agent, base_state("england", *TWO_FIELD_BASE, units=units, tick=4000, production=in_production))
    agent.ctx.terrain = field_terrain(SMALL_FIELD, LARGE_FIELD)
    return agent


def test_harvester_target_a_new_harvester_by_the_full_32_cell_field_goes_to_the_98_cell_field_with_room():
    working = field_harvesters(small=3, large=2)
    agent = two_field_agent(working)
    new = situations.unit(399, "harv", 25, 43)

    orders = ask(agent, base_state("england", *TWO_FIELD_BASE, units=[*working, new], tick=4005))

    (sent,) = harvest_orders(orders)
    assert sent["units"] == [399] and tuple(sent["to"]) in LARGE_FIELD.cells
    assert harvesters_trained(orders) == 1


@pytest.mark.parametrize("large, trained", [(8, 0), (7, 1)], ids=["no room", "room for one"])
def test_harvester_target_below_target_no_harvester_is_trained_while_no_field_has_room(large, trained):
    working = field_harvesters(small=3, large=large)
    agent = two_field_agent(working)

    orders = ask(agent, base_state("england", *TWO_FIELD_BASE, units=working, tick=4005))

    assert len(working) < harvester_target(3, agent.ctx) == 15
    assert harvesters_trained(orders) == trained


@pytest.mark.parametrize(
    "tuning, vehicles",
    [(HARVESTER_TARGET, [("harv", 1)]), (HARVESTERS_FIRST_OFF, [("harv", 1), ("2tnk", 1)])],
    ids=["first", "off"],
)
def test_harvester_target_below_it_the_vehicle_queue_takes_the_harvester_before_a_medium_tank(tuning, vehicles):
    orders = ask(upkeep_agent(tuning=dataclasses.replace(tuning, producers=TANK_FACTORIES_ON)), mixed())

    assert trained(orders, "Vehicle") == vehicles and trained(orders, "Infantry") == [("e1", 3)]


@pytest.mark.parametrize("standing, vehicles", [(4, []), (5, [("2tnk", 1)])], ids=["still below", "at the target"])
def test_harvester_target_a_medium_tank_waits_until_the_harvester_in_production_reaches_it(standing, vehicles):
    in_production = [situations.job("Vehicle", "harv")]
    state = base_state("england", *MIXED_BASE["england"], units=harvesters(standing), production=in_production)

    orders = ask(upkeep_agent(tuning=dataclasses.replace(HARVESTER_TARGET, producers=TANK_FACTORIES_ON)), state)

    assert trained(orders, "Vehicle") == vehicles


# Fields A and B of 9 cells have room for 3 harvesters each at 3 cells a harvester and 3 a refinery; A's 4 at work
# leave it none.
BY_ROOM = dataclasses.replace(
    BY_LOAD, upkeep=dataclasses.replace(BY_LOAD.upkeep, harvester_cells_per=3, field_share_per_refinery=3)
)


def test_harvester_target_harvesters_moved_off_a_dry_field_go_to_the_served_field_with_the_most_room():
    agent = new_agent(UpkeepManager(), plan_selector=OpeningThen(HOLD), tuning=BY_ROOM)
    refineries = (*A_REFINERIES, *B_REFINERY)
    by_a = [situations.unit(400 + number, "harv", *AT_WORK_BY_A, idle=False) for number in range(4)]
    units = [*idle_by_the_refinery(range(300, 303)), *by_a]
    ask(agent, base_state("england", *TWO_REFINERIES, *refineries, tick=3990, units=units))
    agent.ctx.terrain = fresh_ore_terrain(FIELD_A, FIELD_B, FIELD_C)

    # Seen idle from 3,990 on, they are sent back to harvest at 4,005, which marks the home field dry, and moved at 4,010.
    sent = [
        harvest_orders(ask(agent, base_state("england", *TWO_REFINERIES, *refineries, tick=tick, units=units)))
        for tick in (4000, 4005, 4010)
    ]

    fields = {unit: field_of_cell(cell, FIELD_A, FIELD_B, FIELD_C) for unit, cell in sent_to(sent[2]).items()}
    assert fields == {300: FIELD_B, 301: FIELD_B, 302: FIELD_B}


# -- harvesters alternate with the army ------------------------------------------------------------------------------

# The harvester-target tuning with the turns set on, harvesters first below 2 a refinery and a combat value floor of
# 6,000, and the Vehicle queue topped up to 2 places.
TURNS = dataclasses.replace(
    HARVESTER_TARGET,
    upkeep=dataclasses.replace(HARVESTER_TARGET.upkeep, harvesters_first_per_refinery=2, combat_value_floor=6000),
    producers=TANK_FACTORIES_ON,
)
FOUR_REFINERY_BASE = (*MIXED_BASE["england"], ("proc", 30, 60), ("proc", 34, 60))
# Jeeps cost 500: sixteen in main are a combat value of 8,000, six 3,000.
STRONG_MAIN = soldiers("jeep", 16, first_id=500)
WEAK_MAIN = soldiers("jeep", 6, first_id=500)
# Three enemy jeeps, 1,500 together, 20 cells east of the yard.
ENEMY_ARMY = [{"id": 900 + number, "type": "jeep", "x": 42, "y": 48, "hp": 100} for number in range(3)]


def four_refineries(count: int, main: list[dict], tick: int = 10000, **more) -> dict:
    """The four-refinery base with a service depot, so many harvesters at work and main's units."""
    return base_state("england", *FOUR_REFINERY_BASE, units=[*harvesters(count), *main], tick=tick, **more)


def vehicle_kinds(orders: list[dict]) -> list[str]:
    """The Vehicle queue's train orders of a batch, in order, each a harvester or a combat vehicle."""
    return ["harvester" if item == "harv" else "combat" for item, _ in trained(orders, "Vehicle")]


def test_harvesters_alternate_with_4_refineries_and_6_harvesters_a_harvester_is_queued_before_a_tank():
    agent = upkeep_agent(tuning=TURNS)

    orders = ask(agent, four_refineries(6, STRONG_MAIN))

    assert vehicle_kinds(orders) == ["harvester"]
    assert main_combat_value(agent.world, agent.ctx) == 8000


def test_harvesters_alternate_with_9_harvesters_and_8000_in_main_a_tank_follows_a_harvester_and_a_harvester_a_tank():
    agent = upkeep_agent(tuning=TURNS)

    first = ask(agent, four_refineries(9, STRONG_MAIN, tick=10000))
    tank = trained(first, "Vehicle")[-1][0]
    both = [situations.job("Vehicle", "harv"), situations.job("Vehicle", tank)]
    ask(agent, four_refineries(9, STRONG_MAIN, tick=10005, production=both))
    after_tank = ask(agent, four_refineries(10, STRONG_MAIN, tick=10010, production=[situations.job("Vehicle", tank)]))

    assert vehicle_kinds(first) == ["harvester", "combat"]
    assert vehicle_kinds(after_tank) == ["harvester"]


def test_harvesters_alternate_with_3000_in_main_a_tank_is_queued_before_a_harvester_at_9():
    agent = upkeep_agent(tuning=TURNS)

    orders = ask(agent, four_refineries(9, WEAK_MAIN))

    assert vehicle_kinds(orders) == ["combat"]
    assert main_combat_value(agent.world, agent.ctx) == 3000


@pytest.mark.parametrize("seconds_ago, kinds", [(10, ["combat"]), (40, ["harvester", "combat"])], ids=["10 s", "40 s"])
def test_harvesters_alternate_an_enemy_army_of_1500_seen_20_cells_from_the_base_puts_a_tank_first(seconds_ago, kinds):
    agent = upkeep_agent(tuning=TURNS)
    full = [situations.job("Vehicle", "2tnk"), situations.job("Vehicle", "2tnk")]
    ask(agent, four_refineries(9, STRONG_MAIN, tick=10000, production=full, visible_enemies=ENEMY_ARMY))

    later = 10000 + seconds_ago * 25
    orders = ask(agent, four_refineries(9, STRONG_MAIN, tick=later))

    assert vehicle_kinds(orders) == kinds


def test_harvesters_alternate_with_no_combat_vehicle_on_offer_the_due_harvester_takes_the_free_vehicle_queue():
    vehicles = [item.name for item in situations.tracked_items() if item.queue == "Vehicle" and item.name != "harv"]

    orders = ask(upkeep_agent(tuning=TURNS), four_refineries(9, WEAK_MAIN, withheld=vehicles))

    assert vehicle_kinds(orders) == ["harvester"]


@pytest.mark.parametrize(
    "worked, count, queued", [(2, 6, True), (2, 5, False), (3, 6, False)], ids=["2 each", "one short", "3 a refinery"]
)
def test_harvesters_alternate_3_refineries_worked_by_2_harvesters_each_order_the_extra_refinery(worked, count, queued):
    upkeep = upkeep_off(harvesters_per_refinery=3, harvesters_max=22, refinery_worked_harvesters=worked)
    agent = upkeep_agent(tuning=tuning_off(upkeep=upkeep))

    # At 500 s the match allows a 4th refinery; 1,500 is the money it waits for.
    orders = ask(agent, grown(("proc", 30, 60), cash=1500, count=count, seconds=500))

    assert ("proc" in builds(orders)) == queued


# -- a due harvester goes first under the shipped turn keys ----------------------------------------------------------

# The turns with the shipped keys: harvesters first below 99 a refinery, which no harvester target reaches, and a
# combat value floor of 6,000.
SHIPPED_TURNS = dataclasses.replace(
    TURNS,
    upkeep=dataclasses.replace(
        TURNS.upkeep,
        harvesters_first_per_refinery=Tuning().upkeep.harvesters_first_per_refinery,
        combat_value_floor=Tuning().upkeep.combat_value_floor,
    ),
)


@pytest.mark.parametrize("tuning, kinds", [(SHIPPED_TURNS, ["harvester"]), (TURNS, ["combat"])], ids=["99", "2"])
def test_harvester_priority_2_refineries_4_harvesters_and_no_army_queue_the_harvester_first_unless_set_to_2(
    tuning, kinds
):
    agent = upkeep_agent(tuning=tuning)

    orders = ask(agent, mixed())

    assert SHIPPED_TURNS.upkeep.harvesters_first_per_refinery == 99 and SHIPPED_TURNS.upkeep.combat_value_floor == 6000
    assert main_combat_value(agent.world, agent.ctx) == 0
    assert vehicle_kinds(orders) == kinds


@pytest.mark.parametrize("count, kinds", [(21, ["harvester", "combat"]), (22, ["combat"])], ids=["21", "22"])
def test_harvester_priority_at_the_22_harvester_ceiling_no_harvester_is_due_and_a_combat_vehicle_is_queued(
    count, kinds
):
    agent = upkeep_agent(tuning=SHIPPED_TURNS)

    orders = ask(agent, base_state("england", *refineries_and_war_factory(8), units=harvesters(count)))

    # At 21 the harvester queued first reaches the target, and the combat vehicle comes after it.
    assert harvester_target(3, agent.ctx) == 22
    assert vehicle_kinds(orders) == kinds


# -- a dry field needs idle re-sends from several harvesters --------------------------------------------------------


def busy_by_the_refinery(numbers: Iterable[int]) -> list[dict]:
    """Harvesters at work within 8 cells of the refinery: they count among its field's harvesters."""
    return [situations.unit(number, "harv", *BY_THE_REFINERY, idle=False) for number in numbers]


def test_dry_distinct_two_re_sends_of_the_same_harvester_do_not_mark_a_field_with_8_harvesters_dry():
    agent = fresh_agent(fresh_ore_terrain(EAST_ORE))
    units = [*idle_by_the_refinery((300,)), *busy_by_the_refinery(range(301, 308))]

    sent = fresh_run(agent, [4000, 4005, 4010, 4260, 4265, 4270], units)

    assert Tuning().upkeep.dry_distinct_harvesters == 3
    assert sent == [[], [], [{"cmd": "harvest", "units": [300]}], [{"cmd": "harvest", "units": [300]}], [], []]


def test_dry_distinct_re_sends_from_3_different_harvesters_within_the_window_mark_it_dry():
    agent = fresh_agent(fresh_ore_terrain(EAST_ORE))
    units = [*idle_by_the_refinery((300, 301, 302)), *busy_by_the_refinery(range(303, 308))]

    sent = fresh_run(agent, [4000, 4005, 4010, 4015], units)

    (moved,) = sent[3]
    assert sent[2] == [{"cmd": "harvest", "units": [300, 301, 302]}]
    assert moved["to"] == list(EAST_ORE.nearest_cell(BY_THE_REFINERY)) and {300, 301, 302} <= set(moved["units"])


def test_dry_distinct_a_field_with_2_harvesters_both_re_sent_is_marked_dry():
    agent = fresh_agent(fresh_ore_terrain(EAST_ORE))

    sent = fresh_run(agent, [4000, 4005, 4010, 4015], idle_by_the_refinery((300, 301), busy=(302, 303)))

    to = list(EAST_ORE.nearest_cell(BY_THE_REFINERY))
    assert sent[3] == [{"cmd": "harvest", "units": [300, 301], "to": to}]


def test_dry_distinct_a_field_with_2_harvesters_one_of_them_re_sent_twice_is_not_marked_dry():
    agent = fresh_agent(fresh_ore_terrain(EAST_ORE))
    units = [*idle_by_the_refinery((300,)), *busy_by_the_refinery((301,))]

    sent = fresh_run(agent, [4000, 4005, 4010, 4260, 4265, 4270], units)

    assert [orders for orders in sent if orders] == [[{"cmd": "harvest", "units": [300]}]] * 2


# -- the Soviet infantry counter: flamethrowers exempt from no_army_infantry ---------------------------------------

# The shipped army mix and Soviet infantry counter over the no-army-infantry base, without the counter quota.
SOVIET_INFANTRY_COUNTER = dataclasses.replace(
    NO_ARMY_INFANTRY,
    mix=Tuning().mix,
    composition=dataclasses.replace(Tuning().composition, counter_quota=False, infantry_counter_lift=False),
)
# A Soviet base at the dome stage with its flame tower: a service depot and a radar dome beside the holding base.
DOME_STAGE = (*HOLDING["russia"], ("fix", 30, 44), ("dome", 30, 48))
# 30 rifles and 5 medium tanks seen (86% infantry by count), and 5 rifles and 10 medium tanks (33%).
RIFLES_SEEN = [{"id": 900 + n, "type": "e1" if n < 30 else "2tnk", "x": 70, "y": 40, "hp": 100} for n in range(35)]
TANKS_SEEN = [{"id": 900 + n, "type": "e1" if n < 5 else "2tnk", "x": 70, "y": 40, "hp": 100} for n in range(15)]
# Four heavy tanks of main: the army's value, of which the flamethrowers are to have 20 percent.
HEAVY_TANKS = soldiers("3tnk", 4, first_id=500)


def soviet_counter_orders(units: list[dict], enemies: list[dict], **more) -> list[dict]:
    """The orders of an upkeep agent with the Soviet infantry counter on, for the dome-stage base with these own units
    and these enemies in sight."""
    state = base_state("russia", *DOME_STAGE, units=[*harvesters(4), *units], visible_enemies=enemies, **more)
    return ask(upkeep_agent(tuning=SOVIET_INFANTRY_COUNTER), state)


@pytest.mark.parametrize(
    "units, infantry",
    [
        ([*rockets(4), *HEAVY_TANKS], [("e4", 3)]),
        ([*rockets(4)], [("e4", 1)]),
        ([*rockets(2), *HEAVY_TANKS], [("e3", 2)]),
        ([*rockets(4), *HEAVY_TANKS, *soldiers("e4", 5, first_id=600)], []),
        # 4 rockets, 10 heavy tanks and 9 flamers are worth 15,400, of which the rockets' 12 percent is 1,848: 3 more,
        # and the flamers' 20 percent 3,080: 2 more.
        ([*rockets(4), *soldiers("3tnk", 10, first_id=500), *soldiers("e4", 9, first_id=600)], [("e3", 3)]),
    ],
    ids=[
        "rockets kept: flamers",
        "a small army: one flamer",
        "rockets first",
        "flamers at their share",
        "a bigger army: rockets furthest short",
    ],
)
def test_soviet_infantry_counter_no_army_infantry_trains_flamers_up_to_their_share_once_the_rockets_are_kept(
    units, infantry
):
    orders = soviet_counter_orders(units, RIFLES_SEEN)

    assert trained(orders, "Infantry") == infantry


def test_soviet_infantry_counter_no_army_infantry_trains_no_flamer_against_a_tank_heavy_enemy():
    # 4 rockets and 4 heavy tanks are worth 5,800, of which the rockets' 12 percent is 696: the 4 are more.
    orders = soviet_counter_orders([*rockets(4), *HEAVY_TANKS], TANKS_SEEN)

    assert trained(orders, "Infantry") == []


def test_soviet_infantry_counter_no_army_infantry_trains_no_flamer_and_nothing_else_while_e4_is_not_on_offer():
    orders = soviet_counter_orders([*rockets(4), *HEAVY_TANKS], RIFLES_SEEN, withheld=("e4",))

    assert trained(orders, "Infantry") == []


# -- no harvester to an unserved field by enemy buildings ---------------------------------------------------------------

# An enemy refinery 8 cells east of the east field's center, (33, 47); and an own refinery whose nearest field is the
# east field, which then is served.
ENEMY_REFINERY_BY_EAST_ORE = {"id": 903, "type": "proc", "x": 41, "y": 47, "hp": 100}
OWN_REFINERY_BY_EAST_ORE = ("proc", 35, 44)


def moved_with_enemy_refinery(*own: tuple, seen: tuple = (ENEMY_REFINERY_BY_EAST_ORE,)) -> list[dict]:
    """The harvest orders of the cycle that moves two harvesters off the dry home field, in a base with the own
    refineries given besides its two, with the enemy buildings `seen`, on the east and north-west fields' map."""
    agent = new_agent(UpkeepManager(), plan_selector=OpeningThen(HOLD), tuning=FRESH_ORE)
    ask(agent, base_state("england", *TWO_REFINERIES, *own, tick=3990))
    agent.ctx.terrain = fresh_ore_terrain(EAST_ORE, NORTH_WEST_ORE)
    units = idle_by_the_refinery((300, 301))
    observed = {"visible_enemy_buildings": list(seen)}
    sent = [
        harvest_orders(ask(agent, base_state("england", *TWO_REFINERIES, *own, tick=tick, units=units, **observed)))
        for tick in (4000, 4005, 4010, 4015)
    ]
    return sent[3]


def test_enemy_building_near_a_harvester_off_a_dry_field_goes_to_another_field_not_one_8_cells_from_an_enemy_refinery():
    moved = moved_with_enemy_refinery()
    unseen = moved_with_enemy_refinery(seen=())

    assert cells.distance(EAST_ORE.center, (41, 47)) == 8 <= Tuning().expansion.mcv_enemy_building_cells
    assert unseen == [{"cmd": "harvest", "units": [300, 301], "to": list(EAST_ORE.nearest_cell(BY_THE_REFINERY))}]
    assert moved == [{"cmd": "harvest", "units": [300, 301], "to": list(NORTH_WEST_ORE.nearest_cell(BY_THE_REFINERY))}]


def test_enemy_building_near_a_field_an_own_refinery_serves_still_takes_harvesters_by_an_enemy_refinery():
    moved = moved_with_enemy_refinery(OWN_REFINERY_BY_EAST_ORE)

    sent_to = [tuple(order["to"]) for order in moved if "to" in order]
    assert sent_to and all(cell in EAST_ORE.cells for cell in sent_to)


# -- two harvesters a refinery, spread by field ------------------------------------------------------------------------

# The switches-off tuning with the field room on and the shipped harvester keys: 2 a refinery up to 22, 10 cells a
# harvester, a field's share 2 for each refinery serving it, and one rebalance move in 30 s off a field more than 1
# over its share.
FIELD_SHARE = dataclasses.replace(
    TWO_REFINERIES_ONLY,
    upkeep=dataclasses.replace(
        TWO_REFINERIES_ONLY.upkeep,
        harvesters_per_refinery=Tuning().upkeep.harvesters_per_refinery,
        harvesters_max=Tuning().upkeep.harvesters_max,
        harvester_cells_per=Tuning().upkeep.harvester_cells_per,
        field_share_per_refinery=Tuning().upkeep.field_share_per_refinery,
        harvester_rebalance_seconds=Tuning().upkeep.harvester_rebalance_seconds,
        harvester_rebalance_excess=Tuning().upkeep.harvester_rebalance_excess,
    ),
)
# A home field of 100 cells north of the opening's refinery (24, 39), which serves it, and an east field of 33 cells
# served by the refinery 2 cells west of it.
HOME_100 = OrePatch(center=(25, 33), cells=frozenset((x, y) for x in range(20, 30) for y in range(28, 38)))
EAST_33 = OrePatch(center=(55, 41), cells=frozenset((x, y) for x in range(50, 61) for y in range(40, 43)))
EAST_REFINERY = ("proc", 47, 42)
SPREAD_BASE = (YARD, POWER, BARRACKS, REFINERY, EAST_REFINERY, ("powr", 26, 50), WAR_FACTORY)
# Where a truck comes out of the east refinery, and where the home field's trucks work.
BY_THE_EAST_REFINERY = (47, 46)
REBALANCE_TICKS = Tuning().upkeep.harvester_rebalance_seconds * harvester_room.TICKS_PER_SECOND


def home_harvesters(count: int) -> list[dict]:
    """So many harvesters at work on the home field, numbered from 300, the first nearest the east field."""
    return [situations.unit(300 + number, "harv", 29 - number, 33, idle=False) for number in range(count)]


def spread_agent(units: list[dict], tuning: Tuning = FIELD_SHARE):
    """An upkeep agent with the field room on that has seen the two-field base with these harvesters, on the map of
    the two fields."""
    agent = upkeep_agent(tuning=tuning)
    ask(agent, base_state("england", *SPREAD_BASE, units=units, tick=4000))
    agent.ctx.terrain = field_terrain(HOME_100, EAST_33)
    return agent


def test_field_room_each_refinery_serves_its_own_field_and_a_field_s_share_is_2_a_refinery_capped_by_its_size():
    agent = spread_agent(home_harvesters(1))
    room = harvester_room.FieldRoom()
    units = [unit for unit in agent.world.units.values() if unit.type == "harv"]

    serving = harvester_room.serving_counts(agent.world, agent.ctx)
    rooms = room.rooms(units, agent.world, agent.ctx)

    assert serving == Counter({HOME_100: 1, EAST_33: 1})
    upkeep = agent.ctx.tuning.upkeep
    assert harvester_room.field_share(HOME_100, 1, upkeep) == 2 and harvester_room.field_share(EAST_33, 1, upkeep) == 2
    assert harvester_room.field_share(EAST_33, 2, upkeep) == 3 and harvester_room.field_share(EAST_33, 0, upkeep) == 1
    assert rooms == {HOME_100: 1, EAST_33: 2}


def test_harvester_free_truck_of_the_east_refinery_goes_east_not_to_the_100_cell_home_field():
    working = home_harvesters(1)
    agent = spread_agent(working)
    free = situations.unit(399, "harv", *BY_THE_EAST_REFINERY)

    orders = ask(agent, base_state("england", *SPREAD_BASE, units=[*working, free], tick=4005))

    (sent,) = harvest_orders(orders)
    assert sent["units"] == [399] and tuple(sent["to"]) in EAST_33.cells


def test_harvester_new_truck_goes_to_no_dry_field_and_no_dry_field_s_room_trains_one():
    agent = spread_agent(home_harvesters(2))
    units = [unit for unit in agent.world.units.values() if unit.type == "harv"]
    has_room = harvester_room.FieldRoom().has_room(units, agent.world, agent.ctx)
    agent.ctx.dry_fields = frozenset({EAST_33})
    room = harvester_room.FieldRoom()

    sent = room.send_new(units, frozenset({300}), frozenset(), agent.world, agent.ctx)
    dry_has_room = harvester_room.FieldRoom().has_room(units, agent.world, agent.ctx)

    assert [tuple(intent.order["to"]) in HOME_100.cells for intent in sent] == [True]
    assert has_room and not dry_has_room


def test_harvester_target_ships_2_a_refinery_up_to_22():
    shipped = dataclasses.replace(TWO_REFINERIES_ONLY, upkeep=Tuning().upkeep)
    five = upkeep_agent(tuning=shipped)
    twelve = upkeep_agent(tuning=shipped)
    ask(five, base_state("england", *refineries_and_war_factory(5)))
    ask(twelve, base_state("england", *refineries_and_war_factory(12)))

    per_refinery = Tuning().upkeep.harvesters_per_refinery

    assert per_refinery == 2
    assert harvester_target(per_refinery, five.ctx) == 10 and harvester_target(per_refinery, twelve.ctx) == 22


def rebalance_orders(agent, working: list[dict], ticks: Iterable[int]) -> list[list[dict]]:
    """The harvest orders an upkeep agent gives at each tick, in a state of the two-field base with these harvesters."""
    return [harvest_orders(ask(agent, base_state("england", *SPREAD_BASE, units=working, tick=tick))) for tick in ticks]


def test_rebalance_with_4_at_home_and_none_east_one_harvester_moves_east_once(match_record):
    working = home_harvesters(4)
    agent = spread_agent(working)

    orders = rebalance_orders(agent, working, [4005, 4010, 4005 + REBALANCE_TICKS - 1, 4005 + REBALANCE_TICKS])

    (moved,) = orders[0]
    assert moved["units"] == [300] and tuple(moved["to"]) in EAST_33.cells
    assert orders[1:] == [[], [], []]
    assert "harvesters_rebalanced" in records.EVENT_KINDS
    assert match_record("harvesters_rebalanced") == [
        {"tick": 4005, "harvester": 300, "from_field_center": [25, 33], "to_field_center": [55, 41]}
    ]


def test_rebalance_moves_no_second_harvester_within_30_s(match_record):
    working = home_harvesters(5)
    agent = spread_agent(working)

    orders = rebalance_orders(agent, working, [4005, 4010, 4005 + REBALANCE_TICKS - 1, 4005 + REBALANCE_TICKS])

    assert [[order["units"] for order in batch] for batch in orders] == [[[300]], [], [], [[301]]]
    assert [event["tick"] for event in match_record("harvesters_rebalanced")] == [4005, 4005 + REBALANCE_TICKS]


def test_rebalance_with_no_field_over_its_share_by_more_than_1_nothing_moves():
    working = home_harvesters(3)
    agent = spread_agent(working)

    assert rebalance_orders(agent, working, [4005]) == [[]]


def test_rebalance_never_moves_an_excluded_or_dry_bound_harvester():
    working = home_harvesters(4)
    agent = spread_agent(working)
    units = [unit for unit in agent.world.units.values() if unit.type == "harv"]
    room = harvester_room.FieldRoom()
    room.note_cycle(units, agent.world)

    skipping = room.rebalance(units, frozenset({300, 301}), agent.world, agent.ctx)
    none_left = harvester_room.FieldRoom().rebalance(units, frozenset({300, 301, 302, 303}), agent.world, agent.ctx)
    agent.ctx.dry_fields = frozenset({EAST_33})
    to_dry = harvester_room.FieldRoom().rebalance(units, frozenset(), agent.world, agent.ctx)

    assert [intent.order["units"] for intent in skipping] == [[302]]
    assert none_left == [] and to_dry == []


def test_rebalance_leaves_alone_a_harvester_the_move_to_fresh_ore_sent_until_it_arrives():
    working = home_harvesters(5)
    agent = spread_agent(working)
    units = [unit for unit in agent.world.units.values() if unit.type == "harv"]
    room = harvester_room.FieldRoom()
    room.note_cycle(units, agent.world)
    fresh_ore_move = batch.Intent(Priority.PRODUCTION, {"cmd": "harvest", "units": [300], "to": [50, 41]})

    room.pin([fresh_ore_move], agent.ctx)
    moved = room.rebalance(units, frozenset(), agent.world, agent.ctx)

    assert [intent.order["units"] for intent in moved] == [[301]]


# -- truck shares by ore mine ------------------------------------------------------------------------------------------

# The field room's tuning with the truck shares by ore mine on: 2 trucks a mine, 2 for a field without one, one move
# in 10 s.
MINED = dataclasses.replace(
    FIELD_SHARE, upkeep=dataclasses.replace(FIELD_SHARE.upkeep, truck_shares_by_mines=True)
)
HOME_ONE_MINE = dataclasses.replace(HOME_100, mines=1)
HOME_TWO_MINES = dataclasses.replace(HOME_100, mines=2)
EAST_ONE_MINE = dataclasses.replace(EAST_33, mines=1)
# The two-field base before its east refinery stands.
HOME_BASE = tuple(spec for spec in SPREAD_BASE if spec != EAST_REFINERY)
SHARE_MOVE_TICKS = Tuning().upkeep.truck_rebalance_seconds * harvester_room.TICKS_PER_SECOND
# A refinery whose footprint, from (42, 40), stands 6 cells west of the east field's ore: it serves no field, but
# counts against the east field.
SIX_FROM_EAST = ("proc", 43, 41)


def mined_agent(*fields: OrePatch, tuning: Tuning = MINED):
    """An upkeep agent with the truck shares on that has seen the base without its east refinery and with no truck,
    on the map of these fields."""
    agent = upkeep_agent(tuning=tuning)
    ask(agent, base_state("england", *HOME_BASE, units=[], tick=3990))
    agent.ctx.terrain = field_terrain(*fields)
    return agent


def mined_orders(agent, units: list[dict], tick: int, *more_buildings) -> list[dict]:
    """The harvest orders of the agent in a state of the base without its east refinery but the buildings given."""
    return harvest_orders(ask(agent, base_state("england", *HOME_BASE, *more_buildings, units=units, tick=tick)))


def test_share_two_per_mine():
    agent = mined_agent(HOME_ONE_MINE, EAST_ONE_MINE)
    ask(agent, base_state("england", *SPREAD_BASE, units=[], tick=4000))
    ctx = agent.ctx

    shares = [truck_shares.share(dataclasses.replace(EAST_33, mines=mines), ctx) for mines in (1, 2, 0)]
    active = truck_shares.shares(agent.world, ctx)
    ctx.dry_fields = frozenset({EAST_33})

    assert shares == [2, 4, 2] and active == {HOME_ONE_MINE: 2, EAST_ONE_MINE: 2}
    # Dry, a field without a mine takes none; one with a mine keeps its share.
    assert truck_shares.share(EAST_33, ctx) == 0 and truck_shares.share(EAST_ONE_MINE, ctx) == 2


def test_expansion_refinery_6_cells_from_ore_is_active():
    agent = mined_agent(HOME_ONE_MINE, EAST_ONE_MINE)
    ask(agent, base_state("england", *HOME_BASE, SIX_FROM_EAST, units=[], tick=4000))

    corner = footprint_cell(next(b for b in agent.world.buildings.values() if (b.x, b.y) == SIX_FROM_EAST[1:]))
    assert placement.ore_distance(box_at("proc", corner), EAST_33) == 6
    assert EAST_33 not in harvester_room.serving_counts(agent.world, agent.ctx)
    assert truck_shares.shares(agent.world, agent.ctx)[EAST_ONE_MINE] == 2


def test_home_spares_move_to_new_expansion(match_record):
    agent = mined_agent(HOME_ONE_MINE, EAST_ONE_MINE)
    trucks = home_harvesters(5)
    mined_orders(agent, trucks, 4000)

    ticks = [4005, 4006, 4005 + SHARE_MOVE_TICKS - 1, 4005 + SHARE_MOVE_TICKS, 4005 + 2 * SHARE_MOVE_TICKS]
    orders = [mined_orders(agent, trucks, tick, EAST_REFINERY) for tick in ticks]

    moves = [[(order["units"], tuple(order["to"]) in EAST_33.cells) for order in batch] for batch in orders]
    assert moves == [[([300], True)], [], [], [([301], True)], []]
    assert [event["tick"] for event in match_record("harvesters_rebalanced")] == [4005, 4005 + SHARE_MOVE_TICKS]
    assert agent.ctx.truck_fields[300] == EAST_ONE_MINE and agent.ctx.truck_fields[302] == HOME_ONE_MINE


def test_free_truck_stays_at_expansion():
    agent = mined_agent(HOME_TWO_MINES, EAST_ONE_MINE)
    home = home_harvesters(1)
    mined_orders(agent, home, 4000, EAST_REFINERY)
    free = situations.unit(399, "harv", *BY_THE_EAST_REFINERY)

    (sent,) = mined_orders(agent, [*home, free], 4005, EAST_REFINERY)

    # The home field has more room (4 less 1) than the east one (2 less 0); the east one is nearer.
    assert sent["units"] == [399] and tuple(sent["to"]) in EAST_33.cells


def test_idle_resend_keeps_assigned_field():
    agent = mined_agent(HOME_TWO_MINES, EAST_ONE_MINE)
    home = home_harvesters(1)
    mined_orders(agent, home, 4000, EAST_REFINERY)
    mined_orders(agent, [*home, situations.unit(399, "harv", *BY_THE_EAST_REFINERY)], 4005, EAST_REFINERY)
    # Back by the home field, idle: seen idle a third time in a row (it came out idle), it is sent back to harvest.
    idle = situations.unit(399, "harv", 25, 33)

    resent = [mined_orders(agent, [*home, idle], tick, EAST_REFINERY) for tick in (4300, 4301)]

    assert resent[0] == []
    (order,) = resent[1]
    assert order["units"] == [399] and tuple(order["to"]) in EAST_33.cells


def test_never_strips_below_share():
    agent = mined_agent(HOME_ONE_MINE, EAST_ONE_MINE)
    trucks = home_harvesters(2)
    mined_orders(agent, trucks, 4000)

    orders = [mined_orders(agent, trucks, tick, EAST_REFINERY) for tick in (4005, 4005 + SHARE_MOVE_TICKS)]

    assert orders == [[], []]


def test_harvest_orders_send_a_truck_that_works_a_field_to_its_ore_and_the_rest_without_a_cell():
    agent = mined_agent(HOME_ONE_MINE, EAST_ONE_MINE)
    trucks = home_harvesters(2)
    mined_orders(agent, trucks, 4000, EAST_REFINERY)
    agent.ctx.truck_fields = {300: EAST_ONE_MINE}

    orders = truck_shares.harvest_orders([300, 301], agent.world, agent.ctx, Priority.EMERGENCY)
    agent.ctx.truck_fields = {}
    bare = truck_shares.harvest_orders([300, 301], agent.world, agent.ctx, Priority.EMERGENCY)

    assert [(intent.order["units"], intent.order.get("to")) for intent in orders] == [
        ([300], list(EAST_33.nearest_cell((29, 33)))),
        ([301], None),
    ]
    assert [intent.order for intent in bare] == [{"cmd": "harvest", "units": [300, 301]}]
    assert {intent.priority for intent in orders} == {Priority.EMERGENCY}


def test_truck_shares_a_truck_the_move_to_fresh_ore_sends_is_assigned_to_its_new_field():
    agent = mined_agent(HOME_ONE_MINE, EAST_ONE_MINE)
    trucks = home_harvesters(3)
    mined_orders(agent, trucks, 4000, EAST_REFINERY)
    shares = truck_shares.TruckShares()
    units = [unit for unit in agent.world.units.values() if unit.type == "harv"]
    shares.note_cycle(units, agent.world, agent.ctx)
    shares.note_cycle(units, agent.world, agent.ctx)
    moved = batch.Intent(Priority.PRODUCTION, {"cmd": "harvest", "units": [302], "to": [50, 41]})

    shares.pin([moved], agent.ctx)

    assert agent.ctx.truck_fields[302] == EAST_ONE_MINE and agent.ctx.truck_fields[301] == HOME_ONE_MINE
    assert shares.assigned_counts() == Counter({HOME_ONE_MINE: 2, EAST_ONE_MINE: 1})


def test_truck_shares_switch_off_the_room_spreads_as_before():
    off = upkeep_off()

    assert off.truck_shares_by_mines is False and FIELD_SHARE.upkeep.truck_shares_by_mines is False
    agent = spread_agent(home_harvesters(4))
    assert agent.ctx.truck_fields == {}


# -- the rush watch's warning reserve: $1,200 held from the optional buildings, never from harvesters or power -------


def warned(agent):
    """The agent with the rush watch's warning of infantry massing in force, as the scouting manager publishes it."""
    agent.ctx.rush_warning = True
    return agent


@pytest.mark.parametrize("cash, queued", [(700, []), (2099, []), (2100, ["proc"])], ids=["1,500", "2,899", "2,900"])
def test_rush_warning_a_third_refinery_waits_until_the_money_covers_it_and_both_reserves(cash, queued):
    # The refinery's 1,400, the production reserve's 300 and the warning's 1,200: 2,900, ore of 800 included.
    agent = warned(growing_agent())

    assert builds(ask(agent, grown(cash=cash, ore=800, capacity=1000, count=3))) == queued


@pytest.mark.parametrize("cash, queued", [(1500, []), (2999, []), (3000, ["dome"])])
def test_rush_warning_the_radar_waits_until_the_money_covers_it_and_both_reserves_whatever_the_income(cash, queued):
    agent, _ = earning_agent(money=cash)
    warned(agent)

    assert builds(ask(agent, depot_base(cash=cash))) == queued


def test_rush_warning_a_harvester_and_a_power_plant_are_ordered_as_without_it():
    harvester = ask(warned(upkeep_agent()), base_state("england", *TWO_REFINERIES, units=harvesters(3)))
    plant = ask(warned(upkeep_agent()), base_state("england", *BASE, economy=powered(100, 100, cash=1500)))

    assert harvesters_trained(harvester) == 1 and builds(plant) == ["powr"]


def test_rush_warning_the_third_refinery_at_1500_is_ordered_once_the_warning_ends():
    agent = warned(growing_agent())
    held = builds(ask(agent, grown(cash=700, ore=800, capacity=1000, count=3)))

    agent.ctx.rush_warning = False
    released = builds(ask(agent, grown(cash=700, ore=800, capacity=1000, count=3, seconds=301)))

    assert (held, released) == ([], ["proc"])


# -- the factory-first build: upkeep's rules after the opening wait for the depot ----------------------------

FACTORY_FIRST_UNDER_WAY = (YARD, POWER, BARRACKS, REFINERY, WAR_FACTORY, ("powr", 26, 50))
FACTORY_FIRST_DONE = (YARD, POWER, BARRACKS, REFINERY, WAR_FACTORY, ("proc", 18, 39), ("fix", 26, 54))
# A tank force with a depot rush, the second yard's early depot from the start of the match, and the opening's order.
FACTORY_FIRST_RUSHED = dataclasses.replace(
    TANKS_WITH_THIRD_REFINERY,
    opening=opening_off(order=FACTORY_FIRST),
    expansion=expansion_off(second_yard=True, second_yard_from_seconds=0),
)


def ordered_by(order: str, tuning: Tuning = TWO_REFINERIES_ONLY):
    """An upkeep agent under the opening order given."""
    return new_agent(UpkeepManager(), tuning=dataclasses.replace(tuning, opening=opening_off(order=order)))


def test_factory_first_with_the_war_factory_standing_the_opening_s_refinery_goes_before_core_power_and_the_depots():
    agent = new_agent(UpkeepManager(), OpeningManager(), plan_selector=OpeningThen(HOLD), tuning=FACTORY_FIRST_RUSHED)
    # Power to spare from one power plant: neither the power rules nor the opening's own call for a second.
    spare = powered(provided=300, drained=80, cash=5000, harvesters=2)
    state = base_state("england", *FACTORY_FIRST_UNDER_WAY[:-1], tick=7500, units=harvesters(2), economy=spare)

    assert builds(ask(agent, state)) == ["proc"]


def test_factory_first_with_the_depot_standing_the_build_is_complete_and_no_second_power_plant_is_core():
    # Power to spare from one power plant, so that only the core rule could ask for a second.
    spare = powered(provided=300, drained=140, cash=5000, harvesters=2)
    state = base_state("england", *FACTORY_FIRST_DONE, units=harvesters(2), economy=spare)

    assert builds(ask(ordered_by(FACTORY_FIRST), state)) == []
    assert builds(ask(ordered_by(BARRACKS_FIRST), state)) == ["powr"]


@pytest.mark.parametrize(
    "order, standing, trained",
    [(FACTORY_FIRST, 1, 0), (FACTORY_FIRST, 0, 1), (BARRACKS_FIRST, 1, 1)],
    ids=["factory_first with the free harvester", "factory_first with it lost", "barracks_first"],
)
def test_factory_first_harvesters_past_one_a_refinery_wait_for_the_scout_vehicle_the_scouting_manager_claims(
    order, standing, trained
):
    agent = ordered_by(order)
    agent.ctx.claims.hold("Vehicle", "scouting")

    orders = ask(agent, base_state("england", *FACTORY_FIRST_UNDER_WAY, cash=5000, units=harvesters(standing)))

    assert harvesters_trained(orders) == trained


def test_factory_first_once_the_scout_vehicle_has_its_slot_harvesters_are_trained_to_two_a_refinery():
    state = base_state("england", *FACTORY_FIRST_UNDER_WAY, cash=5000, units=harvesters(1))

    orders = ask(ordered_by(FACTORY_FIRST), state)

    assert harvesters_trained(orders) == 1


# -- trucks built only up to the shares -------------------------------------------------------------------------------

CAPPED = dataclasses.replace(MINED, upkeep=dataclasses.replace(MINED.upkeep, truck_cap_by_shares=True))
# Where a truck the east refinery brings stands, and where a new one comes out of the war factory.
FREE_WAIT_TICKS = Tuning().upkeep.free_truck_wait_seconds * harvester_room.TICKS_PER_SECOND
EAST_TRUCKS = [situations.unit(350 + number, "harv", 47, 45 + number, idle=False) for number in range(2)]
TRUCK_JOB = situations.job("Vehicle", "harv")


def truck_trains(agent, units: list[dict], tick: int, *more_buildings, **observation) -> list[tuple[str, int]]:
    """The trucks the agent trains in a state of the base without its east refinery but the buildings given."""
    state = base_state("england", *HOME_BASE, *more_buildings, units=units, tick=tick, **observation)
    return [(item, count) for item, count in trains(ask(agent, state)) if item == "harv"]


def test_truck_cap_is_sum_of_shares():
    full = mined_agent(HOME_TWO_MINES, EAST_ONE_MINE, tuning=CAPPED)
    short = mined_agent(HOME_TWO_MINES, EAST_ONE_MINE, tuning=CAPPED)

    at_cap = truck_trains(full, [*home_harvesters(4), *EAST_TRUCKS], 4000, EAST_REFINERY)
    one_short = truck_trains(short, [*home_harvesters(3), *EAST_TRUCKS], 4000, EAST_REFINERY)

    assert dict(truck_shares.shares(full.world, full.ctx)) == {HOME_TWO_MINES: 4, EAST_ONE_MINE: 2}
    assert at_cap == [] and one_short == [("harv", 1)]


def test_no_truck_for_planned_expansion():
    agent = mined_agent(HOME_ONE_MINE, EAST_ONE_MINE, tuning=CAPPED)
    mcv_out = situations.unit(700, "mcv", 30, 50, idle=False)

    trained = [truck_trains(agent, [*home_harvesters(2), mcv_out], tick) for tick in (4000, 4005)]

    assert trained == [[], []]


def test_pending_free_truck_counts():
    agent = mined_agent(HOME_ONE_MINE, EAST_ONE_MINE, tuning=CAPPED)
    home = home_harvesters(2)
    trained = situations.unit(399, "harv", 22, 56)

    full = truck_trains(agent, home, 4000)
    first = truck_trains(agent, home, 4005, EAST_REFINERY)
    # One truck in production and the free one due: the trucks are at the cap of 4.
    queued = truck_trains(agent, home, 4130, EAST_REFINERY, production=[TRUCK_JOB])
    out = truck_trains(agent, [*home, trained], 4300, EAST_REFINERY)
    # 20 s after the refinery stood, its free truck has not shown up: one more is due.
    second = truck_trains(agent, [*home, trained], 4005 + FREE_WAIT_TICKS, EAST_REFINERY)

    assert (full, first, queued, out, second) == ([], [("harv", 1)], [], [], [("harv", 1)])
    assert agent.ctx.truck_fields[399] == EAST_ONE_MINE


def test_pending_free_truck_seen_counts_no_more():
    agent = mined_agent(HOME_ONE_MINE, EAST_ONE_MINE, tuning=CAPPED)
    home = home_harvesters(2)
    free = situations.unit(398, "harv", *BY_THE_EAST_REFINERY)
    trained = situations.unit(399, "harv", 22, 56)
    truck_trains(agent, home, 4000)

    # The free truck shows up with its refinery: one more is due for the east field's share, and no other.
    with_free = truck_trains(agent, [*home, free], 4005, EAST_REFINERY)
    queued = truck_trains(agent, [*home, free], 4010, EAST_REFINERY, production=[TRUCK_JOB])
    full = truck_trains(agent, [*home, free, trained], 4005 + FREE_WAIT_TICKS, EAST_REFINERY)

    assert (with_free, queued, full) == ([("harv", 1)], [], [])


def test_lost_truck_replaced():
    agent = mined_agent(HOME_ONE_MINE, EAST_ONE_MINE, tuning=CAPPED)
    home = home_harvesters(2)

    full = truck_trains(agent, home, 4000)
    lost = truck_trains(agent, home[:1], 4005)

    assert full == [] and lost == [("harv", 1)]


def test_cap_never_above_22():
    agent = mined_agent(HOME_ONE_MINE, EAST_ONE_MINE, tuning=CAPPED)

    assert truck_shares.cap({HOME_ONE_MINE: 20, EAST_ONE_MINE: 6}, agent.ctx) == 22
    assert truck_shares.cap({HOME_ONE_MINE: 2, EAST_ONE_MINE: 4}, agent.ctx) == 6


def test_truck_cap_switch_off_the_harvester_target_applies():
    one, two = upkeep_agent(tuning=MINED), upkeep_agent(tuning=MINED)

    # 2 a refinery with one refinery: 2 trucks are the target.
    assert MINED.upkeep.truck_cap_by_shares is False
    assert truck_trains(one, home_harvesters(1), 4000) == [("harv", 1)]
    assert truck_trains(two, home_harvesters(2), 4000) == []


# -- the counter quota on the unit queues --------------------------------------------------------------------------

# The two-refinery base with the counter quota on; with no_army_infantry on as well; and with upkeep's defenses only
# against a threat, so that no lost defense of the opening is replaced.
COUNTER_QUOTA = dataclasses.replace(
    TWO_REFINERIES_ONLY,
    composition=dataclasses.replace(TWO_REFINERIES_ONLY.composition, counter_quota=True),
)
COUNTER_QUOTA_HELD = dataclasses.replace(
    COUNTER_QUOTA, upkeep=dataclasses.replace(COUNTER_QUOTA.upkeep, no_army_infantry=True)
)
COUNTER_QUOTA_ON_THREAT_ONLY = dataclasses.replace(
    COUNTER_QUOTA, upkeep=dataclasses.replace(COUNTER_QUOTA.upkeep, defenses_on_threat_only=True)
)
# Six light tanks of main, worth 4,200: the counter units stand below the cap beside them.
LIGHT_TANKS = soldiers("1tnk", 6, first_id=500)


def enemies_seen(**counts: int) -> list[dict]:
    """Enemy units in sight by type, numbered from 900, out east of the base."""
    types = [unit_type for unit_type, count in counts.items() for _ in range(count)]
    return [{"id": 900 + n, "type": unit_type, "x": 70, "y": 40, "hp": 100} for n, unit_type in enumerate(types)]


TEN_ROCKETS_FOUR_TANKS = enemies_seen(e3=10, **{"2tnk": 4})
# The Soviet holding base without its flame tower.
SOVIET_NO_FLAME_TOWER = tuple(building for building in HOLDING["russia"] if building[0] != "ftur")


def test_counter_quota_soviet_flamers_trained_and_never_grenadiers():
    agent = upkeep_agent(tuning=COUNTER_QUOTA)

    orders = ask(agent, holding("russia", units=[*harvesters(4), *LIGHT_TANKS], visible_enemies=TEN_ROCKETS_FOUR_TANKS))

    assert trained(orders, "Infantry") == [("e4", 3)]
    assert "e2" not in [item for item, _ in trains(orders)]


def test_counter_quota_under_no_army_infantry_soviet_flamers_pass():
    units = [*harvesters(4), *rockets(4), *LIGHT_TANKS]

    orders = ask(
        upkeep_agent(tuning=COUNTER_QUOTA_HELD),
        holding("russia", units=units, visible_enemies=enemies_seen(e1=40)),
    )

    # 40 rifles: 10 flamers wanted, which no_army_infantry keeps exempt as it does the mix's.
    assert trained(orders, "Infantry") == [("e4", 3)]


def no_flame_tower(tick: int = 1000) -> dict:
    """The Soviet holding base without its flame tower, with ten rockets and four tanks of the enemy in sight."""
    return base_state(
        "russia", *SOVIET_NO_FLAME_TOWER, units=harvesters(4), visible_enemies=TEN_ROCKETS_FOUR_TANKS, tick=tick
    )


def test_soviet_builds_one_flame_tower_for_flamers():
    agent = upkeep_agent(tuning=COUNTER_QUOTA_ON_THREAT_ONLY)

    first = ask(agent, no_flame_tower())
    goal = agent.ctx.defense_goals.get("ftur")
    second = ask(agent, no_flame_tower(tick=1500))

    assert built(first, "Defense") == ["ftur"] and built(second, "Defense") == []
    # 15 cells from the base's centre (22, 48) toward the enemy's start (74, 48).
    assert goal == (37, 48)
    assert not {"e2", "e4"} & {item for item, _ in trains(first)}


def test_counter_quota_no_flame_tower_without_the_switch():
    off = dataclasses.replace(
        COUNTER_QUOTA_ON_THREAT_ONLY, composition=dataclasses.replace(COUNTER_QUOTA.composition, counter_quota=False)
    )

    assert built(ask(upkeep_agent(tuning=off), no_flame_tower()), "Defense") == []


# Forty enemy rifles seen: 10 anti-infantry units wanted, 5 jeeps and 5 rifles once a war factory stands.
FORTY_RIFLES = enemies_seen(e1=40)


@pytest.mark.parametrize(
    "rifles, vehicle",
    [(9, [("1tnk", 1)]), (6, [("jeep", 1)])],
    ids=["9 rifles of 2,300: 39 percent", "6 rifles of 2,000: 30 percent"],
)
def test_counter_cap_35_percent(rifles, vehicle):
    units = [*harvesters(4), *soldiers("e1", rifles), *soldiers("1tnk", 2, first_id=500)]

    orders = ask(upkeep_agent(tuning=COUNTER_QUOTA), holding(units=units, visible_enemies=FORTY_RIFLES))

    assert trained(orders, "Vehicle") == vehicle


def test_counter_quota_a_vehicle_counter_one_at_a_time_and_the_rifles_short_three_at_a_time():
    orders = ask(upkeep_agent(tuning=COUNTER_QUOTA), holding(visible_enemies=FORTY_RIFLES))

    assert unit_trains(orders) == [("e1", 3), ("jeep", 1)]


def test_counter_quota_units_on_their_way_count_toward_it():
    queued = [situations.job("Infantry", "e1")] * 3
    agent = upkeep_agent(tuning=COUNTER_QUOTA)
    ask(agent, holding(units=[*harvesters(4), *soldiers("e1", 2)], production=queued))

    assert counter_units.shortfalls({"e1": 5, "jeep": 1}, agent.world, agent.ctx) == {"jeep": 1}


def test_counter_quota_under_no_army_infantry_only_rockets_pass():
    enemies = enemies_seen(e1=40, **{"2tnk": 8})
    units = [*harvesters(4), *rockets(4), *LIGHT_TANKS]

    orders = ask(upkeep_agent(tuning=COUNTER_QUOTA_HELD), holding(units=units, visible_enemies=enemies))

    # 8 tanks: 4 rockets wanted, and 4 stand; the rifles wait for no_army_infantry, whatever the mix's rockets do.
    assert "e1" not in dict(trained(orders, "Infantry"))
    assert trained(orders, "Vehicle") == [("jeep", 1)]


def test_counter_quota_under_no_army_infantry_rockets_short_are_trained():
    units = [*harvesters(4), *rockets(4), *LIGHT_TANKS]

    orders = ask(
        upkeep_agent(tuning=COUNTER_QUOTA_HELD), holding(units=units, visible_enemies=enemies_seen(**{"2tnk": 16}))
    )

    # 16 tanks: 8 rockets wanted, 4 stand.
    assert trained(orders, "Infantry") == [("e3", 3)]


# The counter quota under no_army_infantry with the infantry counter lift on.
COUNTER_QUOTA_LIFTED = dataclasses.replace(
    COUNTER_QUOTA_HELD, composition=dataclasses.replace(COUNTER_QUOTA_HELD.composition, infantry_counter_lift=True)
)


def test_no_army_infantry_lets_quota_rifles_through():
    units = [*harvesters(4), *rockets(4), *LIGHT_TANKS]
    # 16 rifles: 4 anti-infantry units wanted, 2 jeeps and 2 rifles. With 30 tanks beside them, 35 percent infantry.
    heavy = holding(units=units, visible_enemies=enemies_seen(e1=16))
    not_heavy = holding(units=units, visible_enemies=enemies_seen(e1=16, **{"2tnk": 30}))

    lifted = ask(upkeep_agent(tuning=COUNTER_QUOTA_LIFTED), heavy)
    no_longer = ask(upkeep_agent(tuning=COUNTER_QUOTA_LIFTED), not_heavy)
    switch_off = ask(upkeep_agent(tuning=COUNTER_QUOTA_HELD), heavy)

    assert trained(lifted, "Infantry") == [("e1", 2)]
    assert "e1" not in dict(trained(no_longer, "Infantry"))
    assert "e1" not in dict(trained(switch_off, "Infantry"))


def test_switch_off_mix_as_before():
    units = [*harvesters(4), *soldiers("e1", 2), *LIGHT_TANKS]
    seen = holding(units=units, visible_enemies=FORTY_RIFLES)

    off = unit_trains(ask(upkeep_agent(), seen))
    unseen = unit_trains(ask(upkeep_agent(tuning=COUNTER_QUOTA), holding(units=units)))

    assert off == unseen == unit_trains(ask(upkeep_agent(), holding(units=units)))
    assert off != unit_trains(ask(upkeep_agent(tuning=COUNTER_QUOTA), seen))


def test_counter_quota_event_when_the_quota_changes(match_record):
    agent = upkeep_agent(tuning=COUNTER_QUOTA)
    ask(agent, holding())
    ask(agent, holding(visible_enemies=FORTY_RIFLES, tick=1100))
    ask(agent, holding(visible_enemies=FORTY_RIFLES, tick=1200))

    events = match_record(counter_units.COUNTER_QUOTA_EVENT)

    assert events == [
        {"tick": 1100, "rockets": 0, "infantry": 40, "tanks": 0, "wanted": {"jeep": 5, "e1": 5}, "capped": False}
    ]
