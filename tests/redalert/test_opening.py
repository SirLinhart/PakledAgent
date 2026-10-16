"""The opening: the rehearsed build, the home guard, the early defense and the anti-armor cover."""

from __future__ import annotations

import copy
import dataclasses
import shutil
from collections import Counter
from pathlib import Path

import pytest

import fake_game
import situations
from fake_game import FakeRedAlert
from situations import ask, base_state, with_command
from switches_off import new_agent, opening_off, own_agent, settings_models_off, tuning_off
from ucla_agent.check import local
from ucla_agent.check.line import FAIL, PASS
from ucla_agent.core.config import ConfigError
from ucla_agent.games.redalert import cells, placement
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.family import FAMILIES
from ucla_agent.games.redalert.items import ALLIES, SOVIET, ItemTable
from ucla_agent.games.redalert.opening import OpeningManager, build_steps, is_build_complete
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import (
    BARRACKS_FIRST,
    FACTORY_FIRST,
    REACH_FIRST,
    REFINERY_FIRST,
    TUNING_FILE,
    OpeningTuning,
    Tuning,
)
from ucla_agent.games.redalert.upkeep import UpkeepManager
from whole_match import COMPUTE_P95_MS, WHOLE_MATCH_STEPS, WholeMatch, play_whole_match

REPO_ROOT = Path(__file__).resolve().parents[2]

YARD = ("fact", 22, 48)
POWER = ("powr", 19, 48)
# The buildings of a finished opening but the war factory, for each faction, in the order they are built.
OPENING = {
    "england": (YARD, POWER, ("tent", 22, 44), ("proc", 24, 39), ("proc", 18, 39), ("powr", 26, 48)),
    "russia": (YARD, POWER, ("barr", 22, 44), ("proc", 24, 39), ("proc", 18, 39), ("powr", 26, 48)),
}
WAR_FACTORY = ("weap", 15, 48)
DEFENSE = {"england": "pbox", "russia": "ftur"}


def opening_agent(**more):
    """An agent that plays with the opening manager alone."""
    return new_agent(OpeningManager(), **more)


def built(orders: list[dict], queue: str) -> list[str]:
    """The items of the build orders of a batch that go into a queue."""
    items = situations.tracked_items()
    return [order["item"] for order in with_command(orders, "build") if items[order["item"]].queue == queue]


def builds(orders: list[dict]) -> list[str]:
    """The items a batch orders for the Building queue."""
    return built(orders, "Building")


def defenses(orders: list[dict]) -> list[str]:
    """The items a batch orders for the Defense queue."""
    return built(orders, "Defense")


# -- deploying the MCV -----------------------------------------------------------------------------


def test_the_mcv_is_deployed_where_it_stands_in_the_first_state():
    assert ask(opening_agent(), situations.fixture_state("opening_mcv")) == [{"cmd": "deploy", "units": [100]}]


@pytest.mark.parametrize("ticks_later, deployed_again", [(5, False), (70, False), (75, True)])
def test_a_deploy_that_came_to_nothing_is_ordered_again_after_75_ticks(ticks_later, deployed_again):
    agent = opening_agent()
    ask(agent, situations.fixture_state("opening_mcv", 0))

    orders = ask(agent, situations.fixture_state("opening_mcv", ticks_later))

    assert orders == ([{"cmd": "deploy", "units": [100]}] if deployed_again else [])


def test_an_mcv_is_not_deployed_while_a_construction_yard_stands():
    state = base_state("england", YARD, units=[situations.unit(150, "mcv", 30, 48)])

    assert with_command(ask(opening_agent(), state), "deploy", "move") == []


def test_without_an_mcv_and_without_a_construction_yard_there_is_nothing_to_order():
    assert ask(opening_agent(), base_state("england", units=[situations.unit(150, "harv")])) == []


def test_the_mcv_is_never_ordered_to_do_anything_but_deploy():
    agent = opening_agent()

    orders = [order for tick in range(0, 400, 5) for order in ask(agent, situations.fixture_state("opening_mcv", tick))]

    assert {order["cmd"] for order in orders} == {"deploy"} and len(orders) == 6


# -- the six steps ---------------------------------------------------------------------------------


def test_the_build_is_the_same_for_both_families_but_for_the_barracks():
    assert build_steps(FAMILIES[ALLIES]) == ("powr", "tent", "proc", "proc", "powr", "weap")
    assert build_steps(FAMILIES[SOVIET]) == ("powr", "barr", "proc", "proc", "powr", "weap")


@pytest.mark.parametrize("faction", ["england", "russia"])
@pytest.mark.parametrize("standing", range(1, 7))
def test_each_step_is_ordered_once_the_steps_before_it_stand(faction, standing):
    steps = build_steps(FAMILIES[{"england": ALLIES, "russia": SOVIET}[faction]])

    orders = ask(opening_agent(), base_state(faction, *OPENING[faction][:standing]))

    assert builds(orders) == [steps[standing - 1]]


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_with_the_war_factory_standing_the_build_is_complete(faction):
    assert builds(ask(opening_agent(), base_state(faction, *OPENING[faction], WAR_FACTORY))) == []


def test_a_step_is_not_ordered_while_the_building_queue_holds_an_item():
    queued = [situations.job("Building", "tent")]
    waiting = [situations.job("Building", "tent", ready=True)]

    assert ask(opening_agent(), base_state("england", YARD, POWER, production=queued)) == []
    assert ask(opening_agent(), base_state("england", YARD, POWER, production=waiting)) == []


def test_a_step_is_not_ordered_again_before_the_first_order_shows_in_the_queue():
    agent = opening_agent()

    first = ask(agent, base_state("england", YARD, tick=1000))
    second = ask(agent, base_state("england", YARD, tick=1005))

    assert (builds(first), second) == (["powr"], [])
    assert agent.ledger.duplicate_production_orders == 0


def test_the_next_step_follows_in_the_state_that_shows_the_last_one_placed():
    agent = opening_agent()
    ask(agent, base_state("england", YARD, tick=1000))
    ask(agent, base_state("england", YARD, tick=1005, production=[situations.job("Building", "powr")]))

    orders = ask(agent, base_state("england", YARD, POWER, tick=1010))

    assert builds(orders) == ["tent"]


def test_a_step_that_stands_already_is_passed_over():
    with_a_second_plant_early = (YARD, POWER, ("powr", 26, 48), ("tent", 22, 44), ("proc", 24, 39), ("proc", 18, 39))

    assert builds(ask(opening_agent(), base_state("england", *with_a_second_plant_early))) == ["weap"]


def test_a_building_of_the_build_that_was_lost_before_the_war_factory_stands_is_built_again():
    without_barracks = (YARD, POWER, ("proc", 24, 39), ("proc", 18, 39), ("powr", 26, 48))

    assert builds(ask(opening_agent(), base_state("russia", *without_barracks))) == ["barr"]


def test_a_step_that_is_not_on_offer_waits_and_no_later_step_is_taken_in_its_place():
    assert ask(opening_agent(), base_state("england", YARD, POWER, withheld=["tent"])) == []


def test_with_every_step_standing_or_on_its_way_nothing_is_ordered_even_when_the_queue_looks_free():
    # Without item facts the ledger cannot tell which queue the ordered war factory holds.
    agent = situations.RedAlertAgent(
        items=ItemTable([]),
        tuning=situations.Tuning(),
        chooser=situations.StubChooser(),
        managers=[OpeningManager()],
        settings=settings_models_off(),
    )

    first = ask(agent, base_state("england", *OPENING["england"], tick=4000))
    second = ask(agent, base_state("england", *OPENING["england"], tick=4005))

    assert (builds(first), builds(second)) == (["weap"], [])


def test_the_build_is_not_taken_up_again_once_the_war_factory_has_stood():
    agent = opening_agent()
    ask(agent, base_state("england", *OPENING["england"], WAR_FACTORY, tick=4000))

    after_losses = ask(agent, base_state("england", YARD, POWER, ("tent", 22, 44), tick=4005))

    assert builds(after_losses) == []


@pytest.mark.parametrize("order", [BARRACKS_FIRST, REFINERY_FIRST])
def test_the_opening_publishes_the_build_in_force_in_every_cycle_and_none_once_the_war_factory_has_stood(order):
    agent = opening_agent(tuning=tuning_off(opening=opening_off(order=order)))

    ask(agent, base_state("england", YARD, POWER, tick=4000))
    under_way = agent.ctx.opening_build.steps
    ask(agent, base_state("england", *OPENING["england"], WAR_FACTORY, tick=4005))
    complete = agent.ctx.opening_build.steps
    ask(agent, base_state("england", YARD, POWER, tick=4010))

    assert under_way == build_steps(FAMILIES[ALLIES], order)
    assert complete == () and agent.ctx.opening_build.steps == ()


def test_before_the_opening_s_first_cycle_no_build_is_published():
    assert new_agent().ctx.opening_build.steps == ()


# -- power, threats and cash -----------------------------------------------------------------------


def test_a_step_that_would_take_the_headroom_below_zero_is_replaced_by_a_power_plant():
    drained_90 = (YARD, POWER, ("tent", 22, 44), ("proc", 24, 39), ("pbox", 27, 48), ("hbox", 27, 46))
    agent = opening_agent()

    orders = ask(agent, base_state("england", *drained_90))

    assert orders[0] == {"cmd": "build", "item": "powr"} and builds(orders) == ["powr"]
    assert agent.ledger.entries[0].priority.name == "POWER"


def test_a_step_that_cannot_be_powered_waits_when_no_power_plant_is_on_offer():
    drained_90 = (YARD, POWER, ("tent", 22, 44), ("proc", 24, 39), ("pbox", 27, 48), ("hbox", 27, 46))

    assert builds(ask(opening_agent(), base_state("england", *drained_90, withheld=["powr"]))) == []


def test_a_step_that_uses_the_headroom_up_exactly_is_ordered():
    drained_70 = (YARD, POWER, ("tent", 22, 44), ("proc", 24, 39), ("pbox", 27, 48))

    assert builds(ask(opening_agent(), base_state("england", *drained_70))) == ["proc"]


def test_while_a_threat_is_active_no_new_step_is_started():
    agent = opening_agent()
    agent.ctx.threat_active = True

    assert ask(agent, base_state("england", YARD, POWER)) == []


def test_while_a_threat_is_active_a_power_plant_is_still_ordered_when_the_headroom_is_negative():
    short_of_power = (
        YARD,
        POWER,
        ("tent", 22, 44),
        ("proc", 24, 39),
        ("proc", 18, 39),
        ("pbox", 27, 48),
        ("gun", 27, 46),
    )
    agent = opening_agent()
    agent.ctx.threat_active = True

    assert ask(agent, base_state("england", *short_of_power)) == [{"cmd": "build", "item": "powr"}]


def test_when_the_threat_is_over_the_build_goes_on():
    agent = opening_agent()
    agent.ctx.threat_active = True
    ask(agent, base_state("england", YARD, POWER, tick=1000))
    agent.ctx.threat_active = False

    assert builds(ask(agent, base_state("england", YARD, POWER, tick=1005))) == ["tent"]


def test_the_opening_orders_a_step_whatever_the_cash_and_never_cancels_what_waits_for_cash():
    stalled = {**situations.job("Building", "tent"), "paused": True}

    broke = ask(opening_agent(), base_state("england", YARD, POWER, cash=0))
    waiting = ask(opening_agent(), base_state("england", YARD, POWER, cash=0, production=[stalled]))

    assert builds(broke) == ["tent"] and waiting == []


def test_the_opening_asks_no_decision_source():
    chooser = situations.StubChooser()
    agent = opening_agent(chooser=chooser)

    for standing in range(0, 7):
        ask(agent, base_state("england", *OPENING["england"][:standing], tick=1000 + standing * 500))

    assert chooser.submitted == []


# -- the home guard --------------------------------------------------------------------------------

BARRACKS_BASE = (YARD, POWER, ("tent", 22, 44))
# The base center is the yard at (22, 48) and the enemy starts at (74, 48): the post is 6 cells to the east.
GUARD_POST = [28, 48]


def rifles(count: int, first_id: int = 200, x: int = 22, y: int = 46, *, idle: bool = True) -> list[dict]:
    return [situations.unit(first_id + number, "e1", x, y, idle=idle) for number in range(count)]


def trains(orders: list[dict]) -> list[tuple[str, int]]:
    """The (item, count) of the train orders of a batch."""
    return [(order["item"], order["count"]) for order in with_command(orders, "train")]


def test_as_soon_as_the_barracks_stands_five_rifles_are_trained_for_the_home_guard():
    assert trains(ask(opening_agent(), base_state("england", *BARRACKS_BASE))) == [("e1", 5)]
    assert trains(ask(opening_agent(), base_state("russia", YARD, POWER, ("barr", 22, 44)))) == [("e1", 5)]


def test_no_rifle_is_trained_before_the_barracks_stands():
    assert trains(ask(opening_agent(), base_state("england", YARD, POWER))) == []


@pytest.mark.parametrize(
    "in_guard, in_queue, trained",
    [(0, 0, [("e1", 5)]), (2, 0, [("e1", 3)]), (1, 2, [("e1", 2)]), (4, 1, []), (0, 5, [])],
    ids=["none yet", "two stand", "one stands and two are queued", "the last is queued", "all are queued"],
)
def test_only_the_rifles_the_guard_still_lacks_are_trained(in_guard, in_queue, trained):
    queue = [situations.job("Infantry", "e1") for _ in range(in_queue)]
    state = base_state("england", *BARRACKS_BASE, units=rifles(in_guard), production=queue)

    assert trains(ask(opening_agent(), state)) == trained


def test_the_first_five_rifles_are_the_home_guard_and_the_rest_join_main():
    agent = opening_agent()

    ask(agent, base_state("england", *BARRACKS_BASE, units=rifles(7)))

    assert agent.ctx.groups.members("guard") == [200, 201, 202, 203, 204]
    assert agent.ctx.groups.members("main") == [205, 206]


def test_a_guard_rifle_that_is_lost_is_trained_again():
    agent = opening_agent()
    ask(agent, base_state("england", *BARRACKS_BASE, units=rifles(5), tick=1000, cash=300))

    orders = ask(agent, base_state("england", *BARRACKS_BASE, units=rifles(4), tick=1005, cash=300))

    assert trains(orders) == [("e1", 1)]


@pytest.mark.parametrize("cash, trained", [(99, []), (100, [("e1", 5)])])
def test_infantry_training_pauses_while_cash_is_below_100(cash, trained):
    assert trains(ask(opening_agent(), base_state("england", *BARRACKS_BASE, cash=cash))) == trained


def test_infantry_training_pauses_while_a_threat_is_active():
    agent = opening_agent()
    agent.ctx.threat_active = True

    assert trains(ask(agent, base_state("england", *BARRACKS_BASE))) == []


# -- rifles for main -------------------------------------------------------------------------------


@pytest.mark.parametrize("cash, trained", [(399, []), (400, [("e1", 1)]), (5000, [("e1", 1)])])
def test_with_the_guard_complete_one_rifle_at_a_time_is_trained_for_main_while_cash_is_at_least_400(cash, trained):
    state = base_state("england", *BARRACKS_BASE, units=rifles(5), cash=cash)

    assert trains(ask(opening_agent(), state)) == trained


def test_the_next_rifle_for_main_waits_until_the_infantry_queue_is_free():
    busy = base_state("england", *BARRACKS_BASE, units=rifles(6), production=[situations.job("Infantry", "e1")])

    assert trains(ask(opening_agent(), busy)) == []


@pytest.mark.parametrize("in_main, trained", [(5, [("e1", 1)]), (6, []), (9, [])])
def test_main_gets_six_rifles_from_the_opening_and_no_more(in_main, trained):
    state = base_state("england", *BARRACKS_BASE, units=rifles(5 + in_main))

    assert trains(ask(opening_agent(), state)) == trained


def test_no_rifle_is_trained_for_main_once_the_war_factory_stands():
    state = base_state("england", *OPENING["england"], WAR_FACTORY, units=rifles(5))

    assert trains(ask(opening_agent(), state)) == []


# -- the guard post --------------------------------------------------------------------------------


def moves(orders: list[dict]) -> list[dict]:
    return with_command(orders, "move")


def test_each_new_guard_unit_is_sent_to_the_post_in_front_of_the_base_and_units_of_main_are_not():
    orders = ask(opening_agent(), base_state("england", *BARRACKS_BASE, units=rifles(7)))

    assert moves(orders) == [{"cmd": "move", "units": [200, 201, 202, 203, 204], "to": GUARD_POST}]


def test_the_post_lies_toward_the_enemy_wherever_the_enemy_starts():
    state = base_state("england", *BARRACKS_BASE, units=rifles(1))
    state["legal_actions"]["enemy_base"] = [22, 8]

    assert moves(ask(opening_agent(), state)) == [{"cmd": "move", "units": [200], "to": [22, 42]}]


def test_a_guard_unit_is_sent_to_the_post_once():
    agent = opening_agent()
    ask(agent, base_state("england", *BARRACKS_BASE, units=rifles(2), tick=1000))
    at_the_post = rifles(2, x=28, y=48)

    later = [
        ask(agent, base_state("england", *BARRACKS_BASE, units=at_the_post, tick=tick)) for tick in (1005, 1100, 1400)
    ]

    assert [moves(orders) for orders in later] == [[], [], []]


def test_only_the_guard_unit_that_joined_since_is_sent():
    agent = opening_agent()
    ask(agent, base_state("england", *BARRACKS_BASE, units=rifles(2), tick=1000))

    orders = ask(agent, base_state("england", *BARRACKS_BASE, units=rifles(3), tick=1005))

    assert moves(orders) == [{"cmd": "move", "units": [202], "to": GUARD_POST}]


@pytest.mark.parametrize(
    "x, idle, ticks_later, sent_back",
    [(31, True, 75, True), (31, True, 70, False), (31, False, 75, False), (30, True, 75, False)],
    ids=["idle 9 cells out", "too soon", "still on the move", "idle 8 cells out"],
)
def test_a_guard_unit_that_idles_more_than_8_cells_from_the_base_is_sent_back_after_75_ticks(
    x, idle, ticks_later, sent_back
):
    agent = opening_agent()
    ask(agent, base_state("england", *BARRACKS_BASE, units=rifles(1), tick=1000))
    strayed = rifles(1, x=x, y=48, idle=idle)

    orders = ask(agent, base_state("england", *BARRACKS_BASE, units=strayed, tick=1000 + ticks_later))

    assert moves(orders) == ([{"cmd": "move", "units": [200], "to": GUARD_POST}] if sent_back else [])


def test_no_unit_is_sent_while_the_place_the_enemy_starts_at_is_not_known():
    state = base_state("england", *BARRACKS_BASE, units=rifles(2))
    state["legal_actions"]["enemy_base"] = None

    assert moves(ask(opening_agent(), state)) == []


# -- the claim on the Infantry queue ---------------------------------------------------------------


def infantry_is_claimed(agent) -> bool:
    return agent.ctx.claims.is_held_by_other("Infantry", "upkeep")


def test_the_opening_claims_the_infantry_queue_from_its_first_cycle():
    agent = opening_agent()

    ask(agent, situations.fixture_state("opening_mcv"))

    assert infantry_is_claimed(agent)


def test_the_claim_is_given_up_once_the_war_factory_stands_and_the_guard_holds_its_five_rifles():
    agent = opening_agent()
    finished = (*OPENING["england"], WAR_FACTORY)

    ask(agent, base_state("england", *OPENING["england"], units=rifles(5), tick=3000))
    held_before_the_war_factory = infantry_is_claimed(agent)
    ask(agent, base_state("england", *finished, units=rifles(4), tick=3600))
    held_with_the_guard_short = infantry_is_claimed(agent)
    ask(agent, base_state("england", *finished, units=rifles(5), tick=3605))

    assert (held_before_the_war_factory, held_with_the_guard_short, infantry_is_claimed(agent)) == (True, True, False)


def test_a_guard_rifle_lost_before_the_cover_is_chosen_is_trained_again_and_the_queue_is_claimed_for_it():
    agent = opening_agent()
    finished = (*OPENING["england"], WAR_FACTORY)
    ask(agent, base_state("england", *finished, units=rifles(5), tick=3600))
    given_up = not infantry_is_claimed(agent)

    orders = ask(agent, base_state("england", *finished, units=rifles(3), tick=3605))

    assert given_up and trains(orders) == [("e1", 2)] and infantry_is_claimed(agent)


def test_once_the_cover_is_chosen_and_the_guard_stands_the_opening_trains_no_more_infantry():
    agent = opening_agent()
    finished = (*OPENING["england"], WAR_FACTORY)
    ask(agent, base_state("england", *finished, units=rifles(5), tick=3800))

    orders = ask(agent, base_state("england", *finished, units=rifles(3), tick=3805))

    assert trains(orders) == [] and not infantry_is_claimed(agent)


def test_with_the_queue_given_up_the_opening_trains_nothing_for_main():
    agent = opening_agent()
    finished = (*OPENING["england"], WAR_FACTORY)

    orders = [ask(agent, base_state("england", *finished, units=rifles(5), tick=tick)) for tick in (3600, 3700)]

    assert [trains(batch) for batch in orders] == [[], []]


# -- the anti-armor cover --------------------------------------------------------------------------

COVER_TICK = 3750
FINISHED = {faction: (*OPENING[faction], WAR_FACTORY, (DEFENSE[faction], 27, 48)) for faction in OPENING}


def enemy(enemy_id: int, enemy_type: str, x: int = 40, y: int = 48) -> dict:
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


def cover_agent(faction: str, tick: int = 3000, **more):
    """An agent that has seen the finished opening of a faction, with its five guard rifles, before the cover is due."""
    agent = opening_agent(**more)
    ask(agent, base_state(faction, *FINISHED[faction], units=rifles(5), tick=tick))
    return agent


def cover_errors(match_record) -> list[dict]:
    return [error for error in match_record("error") if error["exception"] == "AntiArmorShortfall"]


@pytest.mark.parametrize(
    "tick, covered", [(3745, []), (3750, ["gun"]), (4000, ["gun"])], ids=["149.8 s", "150 s", "160 s"]
)
def test_the_allied_cover_is_a_gun_and_starts_at_150_seconds_of_game_time(tick, covered):
    agent = cover_agent("england")

    orders = ask(agent, base_state("england", *FINISHED["england"], units=rifles(5), tick=tick))

    assert (defenses(orders), trains(orders)) == (covered, [])


def test_the_soviet_cover_is_four_rocket_infantry_for_the_home_guard():
    agent = cover_agent("russia")

    orders = ask(agent, base_state("russia", *FINISHED["russia"], units=rifles(5), tick=COVER_TICK))

    assert (defenses(orders), trains(orders)) == ([], [("e3", 4)])


@pytest.mark.parametrize(
    "seen, started",
    [
        ({"visible_enemies": [enemy(900, "1tnk")]}, True),
        ({"visible_enemies": [enemy(900, "ftrk")]}, True),
        ({"visible_enemy_buildings": [enemy(900, "weap", 70, 50)]}, True),
        ({"visible_enemy_buildings": [enemy(900, "fix", 70, 50)]}, True),
        ({"visible_enemies": [enemy(900, "e1"), enemy(901, "harv"), enemy(902, "mcv")]}, False),
        ({"visible_enemy_buildings": [enemy(900, "barr", 70, 50), enemy(901, "proc", 72, 42)]}, False),
    ],
    ids=[
        "a light tank",
        "a flak truck",
        "a war factory",
        "a service depot",
        "infantry and unarmed vehicles",
        "other buildings",
    ],
)
def test_the_first_sighting_of_enemy_armor_starts_the_cover_before_its_time(seen, started):
    agent = cover_agent("russia", tick=2495)

    orders = ask(agent, base_state("russia", *FINISHED["russia"], units=rifles(5), tick=2500, **seen))

    assert trains(orders) == ([("e3", 4)] if started else [])


def test_armor_that_was_seen_and_went_out_of_sight_has_started_the_cover_all_the_same():
    agent = opening_agent()
    ask(agent, base_state("russia", YARD, POWER, tick=500, visible_enemies=[enemy(900, "1tnk")]))

    with_barracks = ask(agent, base_state("russia", YARD, POWER, ("barr", 22, 44), units=rifles(5), tick=600))

    assert trains(with_barracks) == [("e3", 4)]


def test_the_cover_is_not_chosen_before_the_barracks_stands():
    agent = opening_agent()

    orders = ask(agent, base_state("england", YARD, POWER, tick=COVER_TICK))

    assert (defenses(orders), trains(orders)) == ([], [])


def test_allies_train_rocket_infantry_when_there_is_no_power_for_a_gun():
    agent = cover_agent("england")
    short = situations.economy(5000, provided=200, drained=161)

    orders = ask(agent, base_state("england", *FINISHED["england"], units=rifles(5), tick=COVER_TICK, economy=short))

    assert (defenses(orders), trains(orders)) == ([], [("e3", 4)])


def test_allies_build_the_gun_when_the_headroom_after_it_is_exactly_zero():
    agent = cover_agent("england")
    exact = situations.economy(5000, provided=200, drained=160)

    orders = ask(agent, base_state("england", *FINISHED["england"], units=rifles(5), tick=COVER_TICK, economy=exact))

    assert defenses(orders) == ["gun"]


def test_allies_train_rocket_infantry_when_the_gun_is_not_on_offer():
    agent = cover_agent("england")
    state = base_state("england", *FINISHED["england"], units=rifles(5), tick=COVER_TICK, withheld=["gun"])

    assert trains(ask(agent, state)) == [("e3", 4)]


def test_the_rocket_infantry_trained_for_the_cover_joins_the_home_guard_and_only_the_missing_are_trained():
    agent = cover_agent("russia")
    ask(agent, base_state("russia", *FINISHED["russia"], units=rifles(5), tick=COVER_TICK))
    rockets = [situations.unit(300 + number, "e3") for number in range(2)]
    queue = [situations.job("Infantry", "e3")]

    orders = ask(
        agent, base_state("russia", *FINISHED["russia"], units=[*rifles(5), *rockets], production=queue, tick=3900)
    )

    assert agent.ctx.groups.members("guard") == [200, 201, 202, 203, 204, 300, 301]
    assert trains(orders) == [("e3", 1)]


def test_rocket_infantry_is_not_trained_while_cash_is_below_100_or_a_threat_is_active():
    broke, threatened = cover_agent("russia"), cover_agent("russia")
    threatened.ctx.threat_active = True

    assert (
        trains(ask(broke, base_state("russia", *FINISHED["russia"], units=rifles(5), tick=COVER_TICK, cash=99))) == []
    )
    assert trains(ask(threatened, base_state("russia", *FINISHED["russia"], units=rifles(5), tick=COVER_TICK))) == []


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_without_rocket_infantry_and_gun_on_offer_the_cover_is_a_second_defense_and_the_shortfall_is_logged_once(
    faction, match_record
):
    agent = cover_agent(faction)
    without = {"withheld": ["e3", "gun"], "units": rifles(5)}

    orders = ask(agent, base_state(faction, *FINISHED[faction], tick=COVER_TICK, **without))
    for tick in range(COVER_TICK + 5, COVER_TICK + 200, 5):
        ask(
            agent,
            base_state(
                faction,
                *FINISHED[faction],
                tick=tick,
                production=[situations.job("Defense", DEFENSE[faction])],
                **without,
            ),
        )

    (shortfall,) = cover_errors(match_record)
    assert (defenses(orders), trains(orders)) == ([DEFENSE[faction]], [])
    assert (shortfall["where"], shortfall["type"]) == ("redalert.opening", "handled")
    assert shortfall["message"].endswith(f"the cover is one more {DEFENSE[faction]}")


def test_with_the_second_defense_standing_no_third_is_built():
    agent = cover_agent("england")
    without = {"withheld": ["e3", "gun"], "units": rifles(5)}
    ask(agent, base_state("england", *FINISHED["england"], tick=COVER_TICK, **without))

    orders = ask(agent, base_state("england", *FINISHED["england"], ("pbox", 27, 46), tick=4200, **without))

    assert defenses(orders) == []


def test_the_cover_is_chosen_once_and_the_gun_is_built_once():
    agent = cover_agent("england")
    with_gun = (*FINISHED["england"], ("gun", 27, 46))
    ask(agent, base_state("england", *FINISHED["england"], units=rifles(5), tick=COVER_TICK))
    ask(agent, base_state("england", *with_gun, units=rifles(5), tick=4300))

    without_power = situations.economy(5000, provided=100, drained=160)
    after_its_loss = ask(
        agent, base_state("england", *FINISHED["england"], units=rifles(5), tick=4305, economy=without_power)
    )

    assert (defenses(after_its_loss), trains(after_its_loss)) == ([], [])


def test_the_gun_waits_for_the_defense_queue_and_comes_after_the_defense_against_infantry():
    base = (YARD, POWER, ("tent", 22, 44), ("powr", 26, 48))
    agent = opening_agent()
    sighted = {"visible_enemies": [enemy(900, "1tnk")], "units": rifles(5)}

    both_wanted = ask(agent, base_state("england", *base, tick=600, **sighted))
    pbox_queued = ask(
        agent, base_state("england", *base, tick=605, production=[situations.job("Defense", "pbox")], **sighted)
    )
    pbox_placed = ask(agent, base_state("england", *base, ("pbox", 27, 48), tick=1000, **sighted))

    assert (defenses(both_wanted), defenses(pbox_queued), defenses(pbox_placed)) == (["pbox"], [], ["gun"])


def test_the_gun_is_not_started_while_a_threat_is_active():
    agent = cover_agent("england")
    agent.ctx.threat_active = True

    assert defenses(ask(agent, base_state("england", *FINISHED["england"], units=rifles(5), tick=COVER_TICK))) == []


# -- the claim on the Infantry queue and the cover -------------------------------------------------


def test_a_cover_that_needs_no_infantry_leaves_the_queue_given_up():
    agent = cover_agent("england", tick=3700)
    given_up_before = not infantry_is_claimed(agent)

    orders = ask(agent, base_state("england", *FINISHED["england"], units=rifles(5), tick=COVER_TICK))

    assert given_up_before and defenses(orders) == ["gun"] and not infantry_is_claimed(agent)


def test_with_rocket_infantry_as_cover_the_queue_is_claimed_again_until_the_guard_holds_four():
    agent = cover_agent("russia")
    rockets = [situations.unit(300 + number, "e3") for number in range(4)]
    held = [infantry_is_claimed(agent)]

    for tick, units in ((COVER_TICK, rifles(5)), (4400, [*rifles(5), *rockets[:3]]), (4600, [*rifles(5), *rockets])):
        ask(agent, base_state("russia", *FINISHED["russia"], units=units, tick=tick))
        held.append(infantry_is_claimed(agent))

    # Given up with the build complete, taken back when the cover asks for rockets, given up when four stand.
    assert held == [False, True, True, False]


# -- money is the cash and the ore together --------------------------------------------------------


@pytest.mark.parametrize("cash, ore, trained", [(0, 99, []), (0, 100, [("e1", 5)]), (60, 40, [("e1", 5)])])
def test_infantry_training_goes_on_while_the_cash_and_the_ore_together_are_100(cash, ore, trained):
    assert trains(ask(opening_agent(), base_state("england", *BARRACKS_BASE, cash=cash, ore=ore))) == trained


@pytest.mark.parametrize("cash, ore, trained", [(0, 399, []), (0, 400, [("e1", 1)]), (150, 250, [("e1", 1)])])
def test_a_rifle_for_main_is_trained_while_the_cash_and_the_ore_together_are_400(cash, ore, trained):
    state = base_state("england", *BARRACKS_BASE, units=rifles(5), cash=cash, ore=ore)

    assert trains(ask(opening_agent(), state)) == trained


# -- the whole opening against the fake game -------------------------------------------------------

STATES_PER_SECOND = 5
BARRACKS_OF = {"england": "tent", "russia": "barr"}
ENEMY_OF = {"england": "russia", "russia": "england"}
# The buildings the opening makes in its two side queues, which are not steps of the build.
DEFENSE_QUEUE_ITEMS = ("pbox", "ftur", "gun")


@dataclasses.dataclass
class Played:
    """A fake match the agent played with its own managers."""

    faction: str
    agent: situations.RedAlertAgent
    states: list[dict]
    # The orders the agent answered each state with, by the tick of the state.
    orders_at: dict[int, list[dict]]
    refused_batches: int

    def at(self, seconds: float) -> dict:
        """The observation of the state at a game time."""
        return self.states[round(seconds * STATES_PER_SECOND)]["observation"]

    def guard(self, unit_type: str) -> list[int]:
        """The ids of the home guard's units of a type, as the agent's bookkeeping has them at the end of the run."""
        units = self.agent.world.units
        return [unit_id for unit_id in self.agent.ctx.groups.members("guard") if units[unit_id].type == unit_type]


def play(faction: str, seconds: int, scenario: tuple = (), order: str = BARRACKS_FIRST) -> Played:
    """Play the agent with its own managers against the fake game for so many seconds of game time."""
    game = FakeRedAlert(faction, ENEMY_OF[faction], scenario=scenario)
    recording = situations.Recording(own_agent(tuning=tuning_off(opening=opening_off(order=order))))
    run = game.run(recording, seconds * STATES_PER_SECOND + 1, keep_states=True)
    return Played(faction, recording.agent, run.states, recording.orders_at, run.invalid_actions)


@pytest.fixture(scope="module", params=["england", "russia"])
def opening(request) -> Played:
    """The first 230 seconds of a match with nothing in it but the agent's own play, once as Allies, once as Soviets."""
    return play(request.param, 230)


def appearances(states: list[dict]) -> list[tuple[float, dict]]:
    """Every own building of a run with the game time of the first state that shows it, in the order of appearance."""
    first: dict[int, tuple[float, dict]] = {}
    for state in states:
        observation = state["observation"]
        for building in observation["buildings"]:
            first.setdefault(building["id"], (observation["tick"] / 25, building))
    return list(first.values())


def first_of(played: Played, building_type: str) -> tuple[float, dict]:
    """The game time at which the first building of a type appeared, and the building."""
    return next(seen for seen in appearances(played.states) if seen[1]["type"] == building_type)


def distance_between(first: dict, second: dict) -> float:
    return cells.distance((first["x"], first["y"]), (second["x"], second["y"]))


def longest_wait(states: list[dict]) -> int:
    """The largest number of consecutive states in which one finished building waited to be placed."""
    longest, waiting = 0, {}
    for state in states:
        ready = {(job["queue"], job["item"]) for job in state["observation"]["production"] if job["ready"]}
        waiting = {key: waiting.get(key, 0) + 1 for key in ready}
        longest = max([longest, *waiting.values()])
    return longest


def test_the_buildings_are_created_in_the_order_of_the_build(opening):
    built = [building["type"] for _, building in appearances(opening.states)]
    steps = [name for name in built if name not in DEFENSE_QUEUE_ITEMS]
    # The build ends with the war factory; a power plant upkeep orders after it for the power target is allowed.
    after_the_build = steps.index("weap") + 1

    assert steps[:after_the_build] == ["fact", "powr", BARRACKS_OF[opening.faction], "proc", "proc", "powr", "weap"]
    assert set(steps[after_the_build:]) <= {"powr", "apwr"}


def test_the_anti_infantry_defense_stands_by_45_seconds_within_6_cells_of_the_construction_yard(opening):
    seconds, defense = first_of(opening, DEFENSE[opening.faction])
    _, yard = first_of(opening, "fact")

    assert seconds <= 45
    assert distance_between(defense, yard) <= 6


def test_the_home_guard_holds_five_rifles_by_60_seconds_and_all_are_within_8_cells_of_the_yard_at_90(opening):
    guard = opening.guard("e1")
    _, yard = first_of(opening, "fact")
    at_60 = {unit["id"] for unit in opening.at(60)["units"]}
    at_90 = [unit for unit in opening.at(90)["units"] if unit["id"] in guard]

    assert len(guard) == 5 and set(guard) <= at_60
    assert len(at_90) == 5 and max(distance_between(unit, yard) for unit in at_90) <= 8


def test_the_war_factory_stands_by_210_seconds(opening):
    seconds, _ = first_of(opening, "weap")

    assert seconds <= 210


def test_power_drained_never_exceeds_power_provided(opening):
    short = [state["state_version"] for state in opening.states if _is_short_of_power(state["observation"]["economy"])]

    assert short == []


def _is_short_of_power(economy: dict) -> bool:
    return economy["power_drained"] > economy["power_provided"]


def test_no_finished_building_waits_unplaced_for_more_than_two_states(opening):
    assert 1 <= longest_wait(opening.states) <= 2


def test_by_200_seconds_the_anti_armor_cover_stands(opening):
    buildings = building_types_at(opening, 200)
    at_200 = {unit["id"] for unit in opening.at(200)["units"]}
    rockets = [unit_id for unit_id in opening.guard("e3") if unit_id in at_200]

    assert ("gun" in buildings, len(rockets)) == {"england": (True, 0), "russia": (False, 4)}[opening.faction]


def test_no_production_order_is_duplicated_and_no_batch_or_order_is_refused(opening):
    ledger = opening.agent.ledger

    assert ledger.duplicate_production_orders == 0
    assert (opening.refused_batches, ledger.orders_rejected_by_reason, ledger.orders_lost) == (0, {}, 0)


def test_the_mcv_is_deployed_on_the_cell_it_starts_on(opening):
    start = opening.states[0]["observation"]["units"][0]
    _, yard = first_of(opening, "fact")
    sent_to_it = [
        order for orders in opening.orders_at.values() for order in orders if start["id"] in order.get("units", ())
    ]

    assert sent_to_it == [{"cmd": "deploy", "units": [start["id"]]}]
    assert (yard["x"], yard["y"]) == (start["x"], start["y"])


def test_cash_never_runs_out_before_the_war_factory_stands(opening):
    seconds, _ = first_of(opening, "weap")
    cash = [state["observation"]["economy"]["cash"] for state in opening.states[: round(seconds * STATES_PER_SECOND)]]

    assert min(cash) > 0


def building_types_at(played: Played, seconds: float) -> list[str]:
    return [building["type"] for building in played.at(seconds)["buildings"]]


def first_sighting(played: Played) -> int:
    """The tick of the first state that shows an enemy unit."""
    return next(state["state_version"] for state in played.states if state["observation"]["visible_enemies"])


def orders_before(played: Played, tick: int) -> list[dict]:
    return [order for sent_at, orders in played.orders_at.items() if sent_at < tick for order in orders]


@pytest.mark.parametrize(
    "faction, cover",
    [("england", {"cmd": "build", "item": "gun"}), ("russia", {"cmd": "train", "item": "e3", "count": 4})],
)
def test_an_enemy_tank_shown_at_100_seconds_starts_the_anti_armor_cover_within_two_states(faction, cover):
    # Shown far from the base: a tank at the base would be a threat, and while there is one the opening starts nothing.
    tank = fake_game.at_seconds(100, lambda game: game.place_enemy("1tnk", 60, 48, visible=True))

    played = play(faction, 110, scenario=(tank,))

    seen_at = first_sighting(played)
    within_two_states = [*played.orders_at[seen_at], *played.orders_at[seen_at + 5]]
    assert seen_at == 2500
    assert cover in within_two_states and cover not in orders_before(played, seen_at)


class ProposingOpening(OpeningManager):
    """The opening manager, keeping every order it proposes."""

    def __init__(self) -> None:
        super().__init__()
        self.proposed: list[dict] = []

    def intents(self, world, ctx):
        intents = super().intents(world, ctx)
        self.proposed += [dict(intent.order) for intent in intents]
        return intents


SOVIET_OPENING_SECONDS = 300


def test_over_a_whole_soviet_opening_the_opening_orders_no_tesla_coil_before_or_after_the_war_factory():
    opening = ProposingOpening()
    game = FakeRedAlert("russia", "england")

    run = game.run(
        new_agent(UpkeepManager(), opening), SOVIET_OPENING_SECONDS * STATES_PER_SECOND + 1, keep_states=True
    )

    observations = [state["observation"] for state in run.states]
    war_factory_at = next(seen["tick"] for seen in observations if "weap" in building_types(seen))
    offered_at = next(seen["tick"] for seen in observations if "tsla" in seen["available_production"])
    assert war_factory_at <= offered_at < SOVIET_OPENING_SECONDS * 25
    assert [order for order in opening.proposed if order.get("item") == "tsla"] == []
    # The cover against armor the opening chose is rocket infantry for the home guard.
    assert any(order["cmd"] == "train" and order["item"] == "e3" for order in opening.proposed)


def building_types(observation: dict) -> list[str]:
    return [building["type"] for building in observation["buildings"]]


def withhold_rockets_and_gun(game: FakeRedAlert) -> None:
    game.remove_available("e3")
    game.remove_available("gun")


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_with_rocket_infantry_and_gun_off_offer_a_second_defense_is_built_and_one_shortfall_is_logged(
    faction, match_record
):
    played = play(faction, 200, scenario=(fake_game.at(0, withhold_rockets_and_gun),))

    standing = building_types_at(played, 200)
    shortfalls = [error["exception"] for error in match_record("error")]
    assert standing.count(DEFENSE[faction]) == 2 and "gun" not in standing
    assert shortfalls == ["AntiArmorShortfall"] and played.guard("e3") == []


# -- the order of the build in the tuning file -----------------------------------------------------

TRACKED_ORDER = f'order = "{OpeningTuning().order}"'


def config_with_order(root: Path, order_line: str) -> Path:
    """A copy of the tracked `config/` under `root`, with the `[opening].order` line of the tuning file replaced."""
    shutil.copytree(REPO_ROOT / "config", root / "config")
    path = root / TUNING_FILE
    text = path.read_text(encoding="utf-8")
    assert text.count(TRACKED_ORDER) == 1
    path.write_text(text.replace(TRACKED_ORDER, order_line), encoding="utf-8")
    return root


def test_the_tracked_order_is_the_built_in_one_and_one_of_the_four():
    assert Tuning.from_file(REPO_ROOT).opening.order == OpeningTuning().order
    assert OpeningTuning().order in (BARRACKS_FIRST, REFINERY_FIRST, REACH_FIRST, FACTORY_FIRST)


def test_refinery_first_is_read_from_the_file_and_the_config_check_passes(tmp_path):
    root = config_with_order(tmp_path, 'order = "refinery_first"')

    assert Tuning.from_file(root).opening == dataclasses.replace(OpeningTuning(), order=REFINERY_FIRST)
    assert local.check_config(root).status == PASS


@pytest.mark.parametrize(
    "order_line",
    ['order = "tank_rush"', 'order = "Refinery_First"', "order = 1", ""],
    ids=["an unknown order", "a known order in other letters", "a number", "no order"],
)
def test_an_order_that_is_not_one_of_the_four_makes_the_loader_raise_and_the_config_check_fail(tmp_path, order_line):
    root = config_with_order(tmp_path, order_line)
    named = 'order in [opening] must be "barracks_first" or "refinery_first" or "reach_first" or "factory_first"'

    with pytest.raises(ConfigError) as raised:
        Tuning.from_file(root)
    line = local.check_config(root)

    assert named in str(raised.value) and str(root / TUNING_FILE) in str(raised.value)
    assert line.status == FAIL and named in line.detail


# -- the refinery-first build ----------------------------------------------------------------------

REFINERY_FIRST_STEPS = {
    "england": ("powr", "proc", "tent", "proc", "powr", "weap"),
    "russia": ("powr", "proc", "barr", "proc", "powr", "weap"),
}
# The buildings of a finished refinery-first opening, each where a step of it is placed, in the order of the build.
REFINERY_FIRST_BUILT = {
    faction: (POWER, ("proc", 24, 39), (BARRACKS_OF[faction], 22, 44), ("proc", 18, 39), ("powr", 26, 48), WAR_FACTORY)
    for faction in REFINERY_FIRST_STEPS
}
FAMILY_OF = {"england": FAMILIES[ALLIES], "russia": FAMILIES[SOVIET]}
# The steps of the barracks-first build after its barracks.
OPENING_TAIL = ("proc", "proc", "powr", "weap")


def refinery_first_agent():
    """An agent that plays with the opening manager alone, on the refinery-first build."""
    return opening_agent(tuning=tuning_off(opening=opening_off(order=REFINERY_FIRST)))


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_the_default_order_is_barracks_first_and_refinery_first_moves_the_barracks_behind_the_first_refinery(faction):
    names = FAMILY_OF[faction]

    assert build_steps(names) == build_steps(names, BARRACKS_FIRST) == ("powr", BARRACKS_OF[faction], *OPENING_TAIL)
    assert build_steps(names, REFINERY_FIRST) == REFINERY_FIRST_STEPS[faction]


@pytest.mark.parametrize("order", [BARRACKS_FIRST, REFINERY_FIRST])
@pytest.mark.parametrize("faction", ["england", "russia"])
def test_every_order_starts_with_a_power_plant_and_holds_the_same_six_buildings(faction, order):
    steps = build_steps(FAMILY_OF[faction], order)

    assert steps[0] == "powr"
    assert sorted(steps) == sorted(build_steps(FAMILY_OF[faction]))


def test_an_order_that_is_not_one_of_the_three_has_no_build():
    with pytest.raises(KeyError):
        build_steps(FAMILIES[ALLIES], "tank_rush")


@pytest.mark.parametrize("faction", ["england", "russia"])
@pytest.mark.parametrize("standing", range(0, 7))
def test_under_refinery_first_the_one_building_order_is_the_step_after_those_that_stand(faction, standing):
    built = REFINERY_FIRST_BUILT[faction][:standing]

    orders = ask(refinery_first_agent(), base_state(faction, YARD, *built))

    # The step after the n that stand, and none once the war factory, the sixth, stands.
    assert builds(orders) == list(REFINERY_FIRST_STEPS[faction][standing : standing + 1])


@pytest.fixture(scope="module", params=["england", "russia"])
def refinery_first(request) -> Played:
    """The first 230 seconds of a match played on the refinery-first build, once as Allies, once as Soviets."""
    return play(request.param, 230, order=REFINERY_FIRST)


def test_under_refinery_first_the_buildings_are_created_in_the_order_of_that_build(refinery_first):
    built = [building["type"] for _, building in appearances(refinery_first.states)]
    steps = [name for name in built if name not in DEFENSE_QUEUE_ITEMS]
    after_the_build = steps.index("weap") + 1

    assert steps[:after_the_build] == ["fact", *REFINERY_FIRST_STEPS[refinery_first.faction]]


def test_refinery_first_places_the_first_refinery_at_least_10_seconds_before_barracks_first(refinery_first):
    barracks_first = play(refinery_first.faction, 90)

    earlier, _ = first_of(refinery_first, "proc")
    later, _ = first_of(barracks_first, "proc")

    assert later - earlier >= 10


def first_state_with(played: Played, building_type: str) -> int:
    """The tick of the first state that shows an own building of a type."""
    return next(
        state["state_version"]
        for state in played.states
        if any(building["type"] == building_type for building in state["observation"]["buildings"])
    )


def ticks_ordering(played: Played, item: str) -> list[int]:
    """The ticks of the states the agent answered with a build order for an item."""
    return [
        tick
        for tick, orders in played.orders_at.items()
        if any(order["cmd"] == "build" and order.get("item") == item for order in orders)
    ]


def test_under_refinery_first_the_defense_against_infantry_is_ordered_only_once_the_barracks_stands(refinery_first):
    faction = refinery_first.faction
    barracks_tick = first_state_with(refinery_first, BARRACKS_OF[faction])

    ordered_at = ticks_ordering(refinery_first, DEFENSE[faction])

    assert ordered_at and min(ordered_at) >= barracks_tick


def test_under_refinery_first_power_drained_never_exceeds_power_provided(refinery_first):
    economies = [(state["state_version"], state["observation"]["economy"]) for state in refinery_first.states]

    assert [tick for tick, economy in economies if _is_short_of_power(economy)] == []


def test_under_refinery_first_no_finished_building_waits_unplaced_for_more_than_two_states(refinery_first):
    assert 1 <= longest_wait(refinery_first.states) <= 2


# -- the order in the match record -----------------------------------------------------------------

# A fake match of 500 ticks: the states of ticks 0 to 500, five ticks apart, which is two timing windows.
STATES_OF_TWO_WINDOWS = 101


@pytest.mark.parametrize("has_game_config", [True, False], ids=["with a game config", "without a game config"])
@pytest.mark.parametrize("order", [BARRACKS_FIRST, REFINERY_FIRST])
def test_every_timing_event_records_the_order_and_the_game_config_event_stays_as_it_came(
    match_record, order, has_game_config
):
    game = FakeRedAlert()
    agent = opening_agent(tuning=tuning_off(opening=opening_off(order=order)))

    game.run(agent, STATES_OF_TWO_WINDOWS, situations.context(game.game_config if has_game_config else None))

    lines = match_record()
    timings = [line["data"] for line in lines if line["kind"] == "timing"]
    logged_configs = [line["data"] for line in lines if line["kind"] == "game_config"]
    assert [timing["opening_order"] for timing in timings] == [order, order]
    assert logged_configs == ([game.game_config] if has_game_config else [])


# -- the reach-first build -------------------------------------------------------------------------

TENT = ("tent", 22, 44)
REACH_FIRST_TUNING = tuning_off(opening=opening_off(order=REACH_FIRST))


def ore_patch(left: int, top: int, width: int = 3, height: int = 3) -> OrePatch:
    """The ore patch of a rectangle of ore cells from its top-left cell, its center the middle of them."""
    field = frozenset((x, y) for y in range(top, top + height) for x in range(left, left + width))
    middle = (sum(x for x, _ in field) / len(field), sum(y for _, y in field) / len(field))
    return OrePatch(center=cells.nearest_cell(*middle), cells=field)


def terrain_of(patch: OrePatch) -> Terrain:
    return Terrain([patch], patch.cells)


# A field north of the yard that a refinery by the yard and the first power plant stands 1 cell from ...
NEAR_FIELD = ore_patch(21, 40)
# ... and one south of it that the nearest such refinery stands 6 cells from.
FAR_FIELD = ore_patch(21, 61)
FAR_ORE = (22, 61)


def reach_first_agent(terrain: Terrain, *managers, tuning: Tuning = REACH_FIRST_TUNING):
    """An agent on the reach-first build, with these managers, that has seen a lone construction yard on a map of
    this terrain, and ordered nothing for it."""
    agent = new_agent(*managers, tuning=tuning)
    ask(agent, base_state("england", YARD, tick=990, withheld=["powr"]))
    agent.ctx.terrain = terrain
    return agent


def barracks_on_its_way(tick: int = 995) -> dict:
    """The state in which the reach decision is due: the power plant stands and the barracks is in its queue."""
    return base_state("england", YARD, POWER, tick=tick, production=[situations.job("Building", "tent")])


def test_reach_with_a_refinery_candidate_1_cell_from_the_ore_the_build_is_barracks_first_s(match_record):
    agent = reach_first_agent(terrain_of(NEAR_FIELD), OpeningManager())

    ask(agent, barracks_on_its_way())
    third = builds(ask(agent, base_state("england", YARD, POWER, TENT, tick=1000)))

    (event,) = match_record("opening_reach")
    assert (
        agent.ctx.opening_build.steps
        == ("powr", "tent", "proc", "proc", "powr", "weap")
        == build_steps(FAMILIES[ALLIES], BARRACKS_FIRST)
    )
    assert third == ["proc"] and agent.ctx.opening_build.creeps is False
    assert event == {"tick": 995, "refinery_reaches": True, "field_center": [22, 41], "distance_cells": 1}


def test_reach_with_the_nearest_candidate_6_cells_from_the_ore_a_power_plant_toward_it_comes_third(match_record):
    agent = reach_first_agent(terrain_of(FAR_FIELD), UpkeepManager(), OpeningManager())

    ask(agent, barracks_on_its_way())
    third = builds(ask(agent, base_state("england", YARD, POWER, TENT, tick=1000)))
    ready = [situations.job("Building", "powr", ready=True)]
    (placed,) = with_command(ask(agent, base_state("england", YARD, POWER, TENT, tick=1005, production=ready)), "place")

    (event,) = match_record("opening_reach")
    world, tuning = agent.world, agent.ctx.tuning.placement
    barracks_first_cell = placement.preferred_cell("powr", world, terrain_of(FAR_FIELD), tuning)
    assert agent.ctx.opening_build.steps == ("powr", "tent", "powr", "proc", "proc", "powr", "weap")
    assert third == ["powr"] and agent.ctx.opening_build.creeps is True
    assert event == {"tick": 995, "refinery_reaches": False, "field_center": [22, 62], "distance_cells": 6}
    assert cells.distance(tuple(placed["at"]), FAR_ORE) < cells.distance(barracks_first_cell, FAR_ORE)
    assert tuning.creep_toward_ore is False


@pytest.mark.parametrize("faction", ["england", "russia"])
@pytest.mark.parametrize("reaches", [True, False])
def test_reach_the_build_holds_at_most_seven_steps_and_never_a_refinery_before_the_barracks(faction, reaches):
    steps = build_steps(FAMILY_OF[faction], REACH_FIRST, reaches)

    assert len(steps) == (6 if reaches else 7) and steps.count("powr") == (2 if reaches else 3)
    assert steps[:2] == ("powr", BARRACKS_OF[faction]) and steps.index("proc") > 1


def test_reach_the_decision_is_taken_once_and_a_later_change_of_the_reach_does_not_change_the_build(match_record):
    agent = reach_first_agent(terrain_of(FAR_FIELD), OpeningManager())
    ask(agent, barracks_on_its_way())
    decided = agent.ctx.opening_build.steps

    # A power plant by the far field now gives a refinery candidate within a cell of its ore.
    ask(agent, base_state("england", YARD, POWER, TENT, ("powr", 22, 53), tick=1000))
    ask(agent, base_state("england", YARD, POWER, TENT, ("powr", 22, 53), tick=1005))

    reach_now = placement.refinery_reach(agent.world, agent.ctx.terrain, agent.ctx.tuning.placement)
    assert reach_now.reaches and decided == agent.ctx.opening_build.steps
    assert [event["refinery_reaches"] for event in match_record("opening_reach")] == [False]


def test_reach_no_decision_is_taken_before_the_barracks_is_on_its_way(match_record):
    agent = reach_first_agent(terrain_of(FAR_FIELD), OpeningManager())

    orders = ask(agent, base_state("england", YARD, POWER, tick=995))

    assert builds(orders) == ["tent"] and agent.ctx.opening_build.steps == ("powr", "tent")
    assert match_record("opening_reach") == []


def test_reach_with_no_field_in_ore_reach_the_decision_is_that_the_refinery_reaches(match_record):
    agent = reach_first_agent(Terrain([ore_patch(21, 25)]), OpeningManager())

    ask(agent, barracks_on_its_way())

    (event,) = match_record("opening_reach")
    assert event == {"tick": 995, "refinery_reaches": True, "field_center": None, "distance_cells": None}
    assert agent.ctx.opening_build.steps == build_steps(FAMILIES[ALLIES], BARRACKS_FIRST)


@pytest.mark.parametrize(
    "order, steps",
    [
        (BARRACKS_FIRST, ("powr", "tent", "proc", "proc", "powr", "weap")),
        (REFINERY_FIRST, ("powr", "proc", "tent", "proc", "powr", "weap")),
    ],
)
def test_reach_the_other_orders_give_today_s_builds_and_take_no_reach_decision(order, steps, match_record):
    agent = reach_first_agent(
        terrain_of(FAR_FIELD), OpeningManager(), tuning=tuning_off(opening=opening_off(order=order))
    )

    ask(agent, barracks_on_its_way())

    assert build_steps(FAMILIES[ALLIES], order) == steps == agent.ctx.opening_build.steps
    assert agent.ctx.opening_build.creeps is False and match_record("opening_reach") == []


def test_reach_first_is_read_from_the_file_and_the_config_check_passes(tmp_path):
    root = config_with_order(tmp_path, 'order = "reach_first"')

    assert Tuning.from_file(root).opening == dataclasses.replace(OpeningTuning(), order=REACH_FIRST)
    assert local.check_config(root).status == PASS


def with_own_ore_at(game_config: dict, patch: OrePatch) -> dict:
    """A copy of the fake game's reference whose map pictures the own spawn's ore at a patch's cells in place of 8
    cells north of the spawn."""
    moved = copy.deepcopy(game_config)
    layout = moved["rules"]["maps"][fake_game.MAP_ID]["layout"]
    rows = [list(row) for row in layout["rows"]]
    for x in range(21, 24):
        for y in range(39, 42):
            rows[y][x] = "."
    for x, y in patch.cells:
        rows[y][x] = "$"
    layout["rows"] = ["".join(row) for row in rows]
    return moved


@pytest.fixture(
    scope="module",
    params=[("england", None), ("russia", None), ("england", FAR_FIELD)],
    ids=["allies", "soviets", "allies with the ore 6 cells out of reach"],
)
def reach_first_match(request) -> WholeMatch:
    """A whole fake match played on the reach-first build: on the fake game's map once as Allies and once as Soviets,
    and once as Allies with the own ore moved south of the spawn, where the first refinery cannot reach it."""
    faction, own_ore = request.param
    game_config = None if own_ore is None else with_own_ore_at(FakeRedAlert().game_config, own_ore)
    return play_whole_match(REACH_FIRST_TUNING, faction, game_config)


def test_reach_a_whole_fake_match_on_the_reach_first_build_runs_without_an_error_within_the_compute_budget(
    reach_first_match,
):
    assert len(reach_first_match.compute_ms) == WHOLE_MATCH_STEPS and reach_first_match.data_of("error") == []
    assert reach_first_match.compute_p95_ms <= COMPUTE_P95_MS


def test_reach_in_a_whole_fake_match_the_buildings_are_created_in_the_order_the_reach_decision_gave(
    reach_first_match,
):
    (decision,) = reach_first_match.data_of("opening_reach")
    faction = reach_first_match.game.faction
    built = [building["type"] for building in reach_first_match.game.own_buildings()]
    steps = [name for name in built if name not in DEFENSE_QUEUE_ITEMS]
    after_the_build = steps.index("weap") + 1

    expected = build_steps(FAMILY_OF[faction], REACH_FIRST, decision["refinery_reaches"])
    assert steps[:after_the_build] == ["fact", *expected]
    assert decision["refinery_reaches"] is (decision["field_center"] == [22, 40])
    # Only the power plant the build holds for the first refinery's field is placed toward it: the creep is off.
    crept = [event["building"] for event in reach_first_match.data_of("creep_placement")]
    assert crept == ([] if decision["refinery_reaches"] else ["powr"])


# -- the large map's economy-first build (large_map_economy_first) ---------------------------------------------------

LARGE_MAP_ON = opening_off(large_map_economy_first=True)
# The base's center, from which the enemy's start lies 88 cells east on a large map like shadowfiend-ii.
LARGE_MAP_CENTER = [10, 10]
SHADOWFIEND_ENEMY = [98, 10]
LARGE_MAP_BUILD = {
    "england": ("powr", "proc", "proc", "tent", "powr", "weap"),
    "russia": ("powr", "proc", "proc", "barr", "powr", "weap"),
}


def large_map_agent(opening: OpeningTuning = LARGE_MAP_ON):
    """An agent that plays with the opening manager alone, its opening tuning `opening`."""
    return opening_agent(tuning=tuning_off(opening=opening))


def sized_state(faction: str, enemy_base, *buildings, tick: int = 1000, **observation) -> dict:
    """A state of a base with these buildings, its center at LARGE_MAP_CENTER and the enemy's start at `enemy_base`."""
    state = base_state(faction, *buildings, tick=tick, base_center=LARGE_MAP_CENTER, **observation)
    state["legal_actions"]["enemy_base"] = enemy_base
    return state


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_large_map_build_puts_two_refineries_before_barracks(faction, match_record):
    agent = large_map_agent()

    ask(agent, sized_state(faction, SHADOWFIEND_ENEMY, YARD))

    names = FAMILIES[ALLIES if faction == "england" else SOVIET]
    assert agent.ctx.opening_build.steps == LARGE_MAP_BUILD[faction] == build_steps(names, large_map=True)
    assert match_record("opening_map_size") == [{"tick": 1000, "distance": 88.0, "large": True}]


@pytest.mark.parametrize("distance", [52, 57], ids=["sidestep", "trapped"])
def test_small_maps_keep_barracks_first(distance, match_record):
    agent = large_map_agent()

    ask(agent, sized_state("england", [10 + distance, 10], YARD))

    assert agent.ctx.opening_build.steps == ("powr", "tent", "proc", "proc", "powr", "weap")
    assert match_record("opening_map_size") == [{"tick": 1000, "distance": float(distance), "large": False}]


@pytest.mark.parametrize(
    "enemy_base, distance, large",
    [([80, 10], 70.0, False), ([80, 19], 70.6, True)],
    ids=["exactly 70", "70.58"],
)
def test_threshold_is_strict(enemy_base, distance, large, match_record):
    agent = large_map_agent()

    ask(agent, sized_state("england", enemy_base, YARD))

    expected = LARGE_MAP_BUILD["england"] if large else build_steps(FAMILIES[ALLIES])
    assert agent.ctx.opening_build.steps == expected
    assert match_record("opening_map_size") == [{"tick": 1000, "distance": distance, "large": large}]


# The power plant and the first refinery of the large map's build standing: the second refinery is next without a rush.
FIRST_REFINERY_STANDING = (YARD, POWER, ("proc", 24, 39))


@pytest.mark.parametrize(
    "signal, value",
    [("rush_warning", True), ("rush_verdict", "rush"), ("threat_active", True)],
    ids=["rush warning", "rush verdict", "threat of ground units at the base"],
)
def test_rush_warning_pulls_barracks_forward(signal, value, match_record):
    calm, rushed = large_map_agent(), large_map_agent()
    setattr(rushed.ctx, signal, value)

    without_rush = builds(ask(calm, sized_state("england", SHADOWFIEND_ENEMY, *FIRST_REFINERY_STANDING)))
    with_rush = builds(ask(rushed, sized_state("england", SHADOWFIEND_ENEMY, *FIRST_REFINERY_STANDING)))
    rushed.ctx.threat_active = False
    after_threat = builds(ask(rushed, sized_state("england", SHADOWFIEND_ENEMY, *FIRST_REFINERY_STANDING, tick=1005)))

    # A threat at the base starts no step while it lasts; the barracks comes as soon as it is over.
    assert without_rush == ["proc"]
    assert (after_threat if signal == "threat_active" else with_rush) == ["tent"]
    assert rushed.ctx.opening_build.steps == build_steps(FAMILIES[ALLIES], BARRACKS_FIRST)
    assert [event for event in match_record("opening_map_size") if event.get("pulled")] == [
        {"tick": 1000, "distance": 88.0, "large": True, "pulled": True}
    ]


def test_rush_a_threat_of_aircraft_alone_or_a_barracks_on_its_way_pulls_nothing(match_record):
    agent = large_map_agent()
    ask(agent, sized_state("england", SHADOWFIEND_ENEMY, YARD, POWER))
    agent.ctx.threat_active = agent.ctx.threat_aircraft_only = True
    ask(agent, sized_state("england", SHADOWFIEND_ENEMY, YARD, POWER, tick=1005))
    on_its_way = [situations.job("Building", "tent")]
    agent.ctx.threat_active = agent.ctx.threat_aircraft_only = False
    agent.ctx.rush_warning = True
    ask(agent, sized_state("england", SHADOWFIEND_ENEMY, YARD, POWER, tick=1010, production=on_its_way))

    assert agent.ctx.opening_build.steps == LARGE_MAP_BUILD["england"]
    assert [event for event in match_record("opening_map_size") if event.get("pulled")] == []


def test_large_map_switch_off(match_record):
    agent = large_map_agent(opening_off())

    ask(agent, sized_state("england", SHADOWFIEND_ENEMY, YARD))

    today = ("powr", "tent", "proc", "proc", "powr", "weap")
    assert agent.ctx.opening_build.steps == build_steps(FAMILIES[ALLIES]) == today
    assert match_record("opening_map_size") == []


def test_factory_first_unchanged_on_large_map(match_record):
    agent = large_map_agent(opening_off(large_map_economy_first=True, order=FACTORY_FIRST))

    ask(agent, sized_state("england", SHADOWFIEND_ENEMY, YARD))

    assert agent.ctx.opening_build.steps == build_steps(FAMILIES[ALLIES], FACTORY_FIRST)
    assert match_record("opening_map_size") == []


@pytest.mark.parametrize("order", [REFINERY_FIRST, REACH_FIRST])
def test_large_map_the_refinery_first_and_reach_first_orders_play_the_large_map_build(order):
    agent = large_map_agent(opening_off(large_map_economy_first=True, order=order))

    ask(agent, sized_state("england", SHADOWFIEND_ENEMY, YARD))

    assert agent.ctx.opening_build.steps == LARGE_MAP_BUILD["england"]


def test_large_map_the_decision_waits_for_the_enemy_s_start_and_is_made_once(match_record):
    agent = large_map_agent()

    ask(agent, sized_state("england", None, YARD, tick=1000))
    waiting = agent.ctx.opening_build.steps
    ask(agent, sized_state("england", SHADOWFIEND_ENEMY, YARD, tick=1005))
    ask(agent, sized_state("england", [20, 10], YARD, tick=1010))

    assert waiting == build_steps(FAMILIES[ALLIES])
    assert agent.ctx.opening_build.steps == LARGE_MAP_BUILD["england"]
    assert match_record("opening_map_size") == [{"tick": 1005, "distance": 88.0, "large": True}]


def test_large_map_the_build_is_complete_once_the_war_factory_stands():
    agent = large_map_agent()
    standing = (YARD, POWER, ("proc", 24, 39), ("proc", 18, 39), ("tent", 22, 44), ("powr", 26, 48), WAR_FACTORY)

    ask(agent, sized_state("england", SHADOWFIEND_ENEMY, *standing))

    assert agent.ctx.opening_build.steps == ()


# -- the defenses bought on a threat only ----------------------------------------------------------

ON_THREAT = tuning_off(opening=opening_off(defense_on_threat=True))
TICKS_PER_SECOND = 25
# The base of each faction with its barracks standing, the defense against infantry on offer.
BARRACKS_BASE_OF = {"england": BARRACKS_BASE, "russia": (YARD, POWER, ("barr", 22, 44))}


def on_threat_agent(verdict: str | None, **more):
    """An agent that plays the opening alone with `defense_on_threat` on, the rush watch's verdict pinned."""
    agent = opening_agent(tuning=more.pop("tuning", ON_THREAT), **more)
    agent.ctx.rush_verdict = verdict
    return agent


def at_second(faction: str, seconds: float, *buildings: tuple[str, int, int], **observation) -> dict:
    """A state of a faction's base at a game time; the base with its barracks unless buildings are given."""
    standing = buildings or BARRACKS_BASE_OF[faction]
    return base_state(faction, *standing, tick=round(seconds * TICKS_PER_SECOND), **observation)


def set_threat(agent, level: str) -> None:
    """Report a threat of a level on the match context, as base defense does in each cycle; "none" for none."""
    agent.ctx.threat_active, agent.ctx.threat_level = level != "none", level


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_on_threat_with_the_verdict_none_and_no_threat_no_defense_against_infantry_is_ordered(faction):
    agent = on_threat_agent("none")

    batches = [ask(agent, at_second(faction, seconds)) for seconds in (30, 120, 150, 200, 299.8)]

    assert [DEFENSE[faction] in defenses(orders) for orders in batches] == [False] * 5
    assert trains(batches[0]) == [("e1", 5)]


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_on_threat_the_verdict_rush_at_120_s_orders_the_defense_against_infantry_at_once(faction):
    agent = on_threat_agent("none")
    before = ask(agent, at_second(faction, 119.8))

    agent.ctx.rush_verdict = "rush"
    orders = ask(agent, at_second(faction, 120))

    assert (defenses(before), defenses(orders)) == ([], [DEFENSE[faction]])


@pytest.mark.parametrize(
    "seconds, ordered", [(149.8, False), (150, True), (160, True)], ids=["149.8 s", "150 s", "160 s"]
)
def test_on_threat_the_verdict_still_unknown_at_150_s_orders_the_defense_against_infantry(seconds, ordered):
    agent = on_threat_agent("unknown")

    orders = ask(agent, at_second("russia", seconds))

    assert defenses(orders) == (["ftur"] if ordered else [])


@pytest.mark.parametrize("level", ["minor", "major"])
@pytest.mark.parametrize("faction", ["england", "russia"])
def test_on_threat_a_threat_during_the_opening_orders_the_defense_against_infantry_once_it_is_over(faction, level):
    agent = on_threat_agent(None)
    quiet = ask(agent, at_second(faction, 60))
    set_threat(agent, level)
    during = ask(agent, at_second(faction, 61))

    set_threat(agent, "none")
    after = [ask(agent, at_second(faction, seconds)) for seconds in (64, 64.2)]

    assert (defenses(quiet), defenses(during)) == ([], [])
    assert [defenses(orders) for orders in after] == [[DEFENSE[faction]], []]


def test_on_threat_a_threat_after_the_war_factory_stands_does_not_call_for_the_opening_s_defense():
    finished = (*OPENING["england"], WAR_FACTORY)
    agent = on_threat_agent(None)
    ask(agent, at_second("england", 220, *finished, units=rifles(5)))
    set_threat(agent, "minor")
    ask(agent, at_second("england", 230, *finished, units=rifles(5)))

    set_threat(agent, "none")
    orders = ask(agent, at_second("england", 240, *finished, units=rifles(5)))

    assert defenses(orders) == []


def test_on_threat_with_rush_detection_off_no_verdict_calls_for_the_defense_and_a_threat_still_does():
    agent = on_threat_agent(None)
    late = ask(agent, at_second("england", 200))
    set_threat(agent, "minor")
    ask(agent, at_second("england", 201))

    set_threat(agent, "none")
    after_the_threat = ask(agent, at_second("england", 205))

    assert (defenses(late), defenses(after_the_threat)) == ([], ["pbox"])


def test_on_threat_the_defense_called_for_stands_where_it_stands_without_the_switch():
    ready = [situations.job("Defense", "pbox", ready=True)]
    switched_on = new_agent(UpkeepManager(), OpeningManager(), tuning=ON_THREAT)
    switched_on.ctx.rush_verdict = "rush"
    shipped = new_agent(UpkeepManager(), OpeningManager())

    placed = [
        with_command(ask(agent, at_second("england", 100, production=ready)), "place")
        for agent in (switched_on, shipped)
    ]

    assert placed[0] == placed[1] and len(placed[0]) == 1


ENEMY_TANK_15_CELLS_OUT = enemy(900, "1tnk", 37, 48)
ENEMY_TANK_35_CELLS_OUT = enemy(900, "1tnk", 57, 48)


def finished_on_threat(faction: str, verdict: str | None):
    """An agent with `defense_on_threat` on that has seen a faction's finished opening, its defense against infantry
    and its five guard rifles at 90 s, with the rush watch's verdict pinned."""
    agent = on_threat_agent(verdict)
    ask(agent, at_second(faction, 90, *FINISHED[faction], units=rifles(5)))
    return agent


def cover_ordered(orders: list[dict]) -> tuple[list[str], list[tuple[str, int]]]:
    """What a batch orders on the Defense queue and trains."""
    return defenses(orders), trains(orders)


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_on_threat_without_a_trigger_no_cover_falls_due_at_150_s_or_later(faction):
    agent = finished_on_threat(faction, "none")

    batches = [ask(agent, at_second(faction, seconds, *FINISHED[faction], units=rifles(5))) for seconds in (150, 400)]

    assert [cover_ordered(orders) for orders in batches] == [([], []), ([], [])]


def test_on_threat_the_verdict_unknown_at_150_s_orders_the_defense_against_infantry_but_no_gun():
    agent = on_threat_agent("unknown")
    finished = (*OPENING["england"], WAR_FACTORY)

    at_150 = ask(agent, at_second("england", 150, *finished, units=rifles(5)))
    later = [
        ask(agent, at_second("england", seconds, *finished, ("pbox", 27, 48), units=rifles(5)))
        for seconds in (160, 300)
    ]

    assert cover_ordered(at_150) == (["pbox"], [])
    assert [cover_ordered(orders) for orders in later] == [([], []), ([], [])]


@pytest.mark.parametrize(
    "faction, cover", [("england", (["gun"], [])), ("russia", ([], [("e3", 4)]))], ids=["allies", "soviets"]
)
def test_on_threat_an_enemy_tank_seen_15_cells_from_the_base_center_orders_the_gun_or_the_rockets(faction, cover):
    agent = finished_on_threat(faction, "none")

    near = ask(
        agent, at_second(faction, 100, *FINISHED[faction], units=rifles(5), visible_enemies=[ENEMY_TANK_15_CELLS_OUT])
    )

    assert cover_ordered(near) == cover


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_on_threat_an_enemy_tank_seen_35_cells_from_the_base_center_orders_no_cover(faction):
    agent = finished_on_threat(faction, "none")

    far = ask(
        agent, at_second(faction, 100, *FINISHED[faction], units=rifles(5), visible_enemies=[ENEMY_TANK_35_CELLS_OUT])
    )

    assert cover_ordered(far) == ([], [])


@pytest.mark.parametrize(
    "verdict, seen, covered",
    [
        ("rush", {"visible_enemies": [enemy(900, "2tnk", 60, 30)]}, True),
        ("rush", {"visible_enemies": [enemy(900, "e1", 40, 48), enemy(901, "harv", 60, 30)]}, False),
        ("rush", {"visible_enemy_buildings": [enemy(900, "weap", 70, 50)]}, False),
        ("none", {"visible_enemies": [enemy(900, "2tnk", 60, 30)]}, False),
        ("unknown", {"visible_enemies": [enemy(900, "2tnk", 60, 30)]}, False),
    ],
    ids=[
        "rush and a tank far off",
        "rush and only infantry and a harvester",
        "rush and only a war factory",
        "none and a tank far off",
        "unknown and a tank far off",
    ],
)
def test_on_threat_a_rush_verdict_with_an_enemy_vehicle_that_fights_seen_anywhere_orders_the_cover(
    verdict, seen, covered
):
    agent = finished_on_threat("russia", verdict)

    orders = ask(agent, at_second("russia", 100, *FINISHED["russia"], units=rifles(5), **seen))

    assert cover_ordered(orders) == (([], [("e3", 4)]) if covered else ([], []))


def test_on_threat_a_tank_seen_near_the_base_and_gone_since_has_called_for_the_cover_all_the_same():
    agent = on_threat_agent("none")
    ask(agent, at_second("russia", 20, YARD, POWER, visible_enemies=[ENEMY_TANK_15_CELLS_OUT]))

    with_barracks = ask(agent, at_second("russia", 40, *BARRACKS_BASE_OF["russia"], units=rifles(5)))

    assert trains(with_barracks) == [("e3", 4)]


@pytest.mark.parametrize("level, covered", [("major", True), ("minor", False)])
def test_on_threat_a_major_threat_orders_the_cover_once_it_is_over_and_a_minor_one_does_not(level, covered):
    agent = finished_on_threat("russia", "none")
    set_threat(agent, level)
    during = ask(agent, at_second("russia", 100, *FINISHED["russia"], units=rifles(5)))

    set_threat(agent, "none")
    after = ask(agent, at_second("russia", 110, *FINISHED["russia"], units=rifles(5)))

    assert cover_ordered(during) == ([], [])
    assert cover_ordered(after) == (([], [("e3", 4)]) if covered else ([], []))


def test_on_threat_the_guard_forms_and_the_opening_gives_the_infantry_queue_up_without_a_cover():
    agent = on_threat_agent("none")
    first = ask(agent, at_second("russia", 30))
    ask(agent, at_second("russia", 90, *FINISHED["russia"], units=rifles(5)))
    given_up = not infantry_is_claimed(agent)

    one_lost = ask(agent, at_second("russia", 120, *FINISHED["russia"], units=rifles(4)))

    assert trains(first) == [("e1", 5)] and given_up
    assert trains(one_lost) == [] and not infantry_is_claimed(agent)


def test_on_threat_a_cover_of_rockets_chosen_after_the_queue_was_given_up_takes_it_back_for_the_guard():
    agent = finished_on_threat("russia", "none")
    set_threat(agent, "major")
    ask(agent, at_second("russia", 120, *FINISHED["russia"], units=rifles(4)))
    held_during = infantry_is_claimed(agent)

    set_threat(agent, "none")
    after = ask(agent, at_second("russia", 130, *FINISHED["russia"], units=rifles(4)))
    rockets = [situations.unit(300 + number, "e3") for number in range(4)]
    complete = ask(agent, at_second("russia", 200, *FINISHED["russia"], units=[*rifles(5), *rockets]))

    assert held_during and sorted(trains(after)) == [("e1", 1), ("e3", 4)]
    assert trains(complete) == [] and not infantry_is_claimed(agent)


ON_THREAT_OPENING_SECONDS = 300
EARLY_DEFENSES = {"pbox", "ftur", "gun"}


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_on_threat_with_the_verdict_none_and_no_threat_no_early_defense_is_ordered_by_300_s_and_the_guard_forms(
    faction,
):
    agent = new_agent(UpkeepManager(), OpeningManager(), tuning=ON_THREAT)
    agent.ctx.rush_verdict = "none"
    recording = situations.Recording(agent)

    run = FakeRedAlert(faction, ENEMY_OF[faction]).run(
        recording, ON_THREAT_OPENING_SECONDS * STATES_PER_SECOND + 1, keep_states=True
    )

    ordered = {order["item"] for orders in recording.orders_at.values() for order in with_command(orders, "build")}
    standing = building_types(run.states[-1]["observation"])
    guard = Counter(agent.world.units[unit_id].type for unit_id in agent.ctx.groups.members("guard"))
    assert run.states[-1]["observation"]["tick"] >= ON_THREAT_OPENING_SECONDS * TICKS_PER_SECOND
    assert ordered & EARLY_DEFENSES == set() and set(standing) & EARLY_DEFENSES == set()
    assert "weap" in standing and guard == {"e1": 5}


# -- the lean opening's infantry -------------------------------------------------------------------

LEAN = tuning_off(opening=opening_off(lean_infantry=True))
LEAN_OPENING_SECONDS = 300


def lean_agent(verdict: str | None = None):
    """An agent that plays the opening alone with `lean_infantry` on, the rush watch's verdict pinned."""
    agent = opening_agent(tuning=LEAN)
    agent.ctx.rush_verdict = verdict
    return agent


def lean_cover_agent(faction: str):
    """An agent with `lean_infantry` on that has seen the finished opening of a faction, with its five guard rifles,
    before the cover is due."""
    return cover_agent(faction, tuning=LEAN)


@pytest.mark.parametrize("verdict", [None, "none", "unknown"], ids=["no watch", "no rush", "unknown"])
@pytest.mark.parametrize("faction", ["england", "russia"])
def test_lean_without_a_rush_the_opening_trains_one_rifle_for_the_guard_and_none_for_main(faction, verdict):
    none_yet = ask(lean_agent(verdict), at_second(faction, 30))
    one_stands = ask(lean_agent(verdict), at_second(faction, 30, units=rifles(1), cash=5000))
    more_stand = ask(lean_agent(verdict), at_second(faction, 30, units=rifles(5), cash=5000))

    assert [trains(orders) for orders in (none_yet, one_stands, more_stand)] == [[("e1", 1)], [], []]


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_lean_in_a_whole_opening_against_the_fake_game_the_opening_trains_one_rifle_and_no_rocket_infantry(faction):
    opening = ProposingOpening()
    agent = new_agent(UpkeepManager(), opening, tuning=LEAN)

    run = FakeRedAlert(faction, ENEMY_OF[faction]).run(
        situations.Recording(agent), LEAN_OPENING_SECONDS * STATES_PER_SECOND + 1, keep_states=True
    )

    trained = Counter()
    for order in with_command(opening.proposed, "train"):
        trained[order["item"]] += order["count"]
    assert run.states[-1]["observation"]["tick"] >= LEAN_OPENING_SECONDS * TICKS_PER_SECOND
    assert trained == {"e1": 1} and "weap" in building_types(run.states[-1]["observation"])


@pytest.mark.parametrize(
    "faction, economy, cover",
    [
        ("russia", situations.economy(5000), (["ftur"], [])),
        ("england", situations.economy(5000, provided=200, drained=161), (["pbox"], [])),
        ("england", situations.economy(5000, provided=200, drained=160), (["gun"], [])),
    ],
    ids=["soviet", "allied without power for a gun", "allied with power for a gun"],
)
def test_lean_the_cover_is_never_rocket_infantry_and_a_second_defense_in_their_place_is_no_shortfall(
    faction, economy, cover, match_record
):
    agent = lean_cover_agent(faction)

    orders = ask(agent, base_state(faction, *FINISHED[faction], units=rifles(5), tick=COVER_TICK, economy=economy))

    assert (defenses(orders), trains(orders)) == cover
    assert cover_errors(match_record) == []


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_lean_with_a_rush_told_the_guard_wants_five_rifles_and_main_its_rifles_as_today(faction):
    none_yet = ask(lean_agent("rush"), at_second(faction, 30))
    guard_stands = ask(lean_agent("rush"), at_second(faction, 30, units=rifles(5), cash=5000))
    main_full = ask(lean_agent("rush"), at_second(faction, 30, units=rifles(11), cash=5000))

    assert [trains(orders) for orders in (none_yet, guard_stands, main_full)] == [[("e1", 5)], [("e1", 1)], []]


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_lean_a_rush_told_after_the_opening_was_done_with_the_queue_takes_it_back_for_five_rifles(faction):
    agent = lean_agent("none")
    ask(agent, base_state(faction, *FINISHED[faction], units=rifles(3), tick=COVER_TICK))
    done = trains(ask(agent, base_state(faction, *FINISHED[faction], units=rifles(3), tick=COVER_TICK + 5)))
    given_up = not infantry_is_claimed(agent)

    agent.ctx.rush_verdict = "rush"
    orders = ask(agent, base_state(faction, *FINISHED[faction], units=rifles(3), tick=COVER_TICK + 10))

    assert done == [] and given_up
    assert trains(orders) == [("e1", 2)] and infantry_is_claimed(agent)


def test_lean_with_a_rush_told_before_the_cover_the_soviet_cover_is_rocket_infantry_as_today():
    agent = lean_cover_agent("russia")
    agent.ctx.rush_verdict = "rush"

    orders = ask(agent, base_state("russia", *FINISHED["russia"], units=rifles(5), tick=COVER_TICK))

    assert (defenses(orders), trains(orders)) == ([], [("e3", 4)])


# -- reactive defenses: the opening's defenses wait for a threat seen ------------------------------------------------

# The opening as it ships: defenses on a threat only, no defense for an unknown verdict, the gun for armor near the base.
SHIPPED_OPENING = tuning_off(opening=OpeningTuning())
ENEMY_TANK_25_CELLS_OUT = enemy(900, "1tnk", 47, 48)


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_reactive_defenses_the_verdict_unknown_at_150_s_with_no_enemy_seen_buys_no_opening_pillbox(faction):
    agent = on_threat_agent("unknown", tuning=SHIPPED_OPENING)

    batches = [ask(agent, at_second(faction, seconds)) for seconds in (150, 160, 299.8)]

    assert SHIPPED_OPENING.opening.defense_on_unknown is False
    assert [defenses(orders) for orders in batches] == [[], [], []]


def test_reactive_defenses_with_defense_on_unknown_on_the_verdict_unknown_at_150_s_buys_the_pillbox():
    switched_on = tuning_off(opening=OpeningTuning(defense_on_unknown=True))
    agent = on_threat_agent("unknown", tuning=switched_on)

    assert defenses(ask(agent, at_second("england", 150))) == ["pbox"]


@pytest.mark.parametrize(
    "tank, gun",
    [(ENEMY_TANK_15_CELLS_OUT, ["gun"]), (ENEMY_TANK_25_CELLS_OUT, [])],
    ids=["15 cells out", "25 cells out"],
)
def test_reactive_defenses_the_opening_gun_needs_an_enemy_vehicle_within_18_cells_of_the_base(tank, gun):
    agent = on_threat_agent("none", tuning=SHIPPED_OPENING)
    ask(agent, at_second("england", 90, *FINISHED["england"], units=rifles(3)))

    orders = ask(agent, at_second("england", 100, *FINISHED["england"], units=rifles(3), visible_enemies=[tank]))

    assert defenses(orders) == gun


def test_reactive_defenses_a_rush_and_a_tank_far_off_make_the_cover_due_but_the_gun_waits_for_one_near_the_base():
    # The rush counter's second pillbox aside (see test_rush_counter.py).
    agent = on_threat_agent("rush", tuning=tuning_off(opening=OpeningTuning(rush_counter_defenses=0)))
    ask(agent, at_second("england", 90, *FINISHED["england"], units=rifles(5)))
    far_off = ask(
        agent,
        at_second("england", 100, *FINISHED["england"], units=rifles(5), visible_enemies=[enemy(900, "2tnk", 60, 30)]),
    )

    near = ask(
        agent, at_second("england", 110, *FINISHED["england"], units=rifles(5), visible_enemies=[ENEMY_TANK_15_CELLS_OUT])
    )

    assert (defenses(far_off), defenses(near)) == ([], ["gun"])


# -- the factory-first build -----------------------------------------------------------------------

FACTORY_FIRST_STEPS = {
    "england": ("powr", "tent", "proc", "weap", "proc", "fix"),
    "russia": ("powr", "barr", "proc", "weap", "proc", "fix"),
}
SECOND_POWER = ("powr", 26, 48)
DEPOT = ("fix", 26, 44)
# The buildings of a finished factory-first opening, each where a step of it is placed, in the order they come: the
# power plant after the war factory is the power rule's, since the second refinery would take the headroom below zero.
FACTORY_FIRST_BUILT = {
    faction: (
        POWER,
        (BARRACKS_OF[faction], 22, 44),
        ("proc", 24, 39),
        WAR_FACTORY,
        SECOND_POWER,
        ("proc", 18, 39),
        DEPOT,
    )
    for faction in FACTORY_FIRST_STEPS
}
# What the opening orders next with so many of those buildings standing.
FACTORY_FIRST_NEXT = {faction: (*steps[:4], "powr", *steps[4:]) for faction, steps in FACTORY_FIRST_STEPS.items()}
FACTORY_FIRST_TUNING = tuning_off(opening=opening_off(order=FACTORY_FIRST))
DEFENSE_DRAIN = 20


def factory_first_agent():
    """An agent that plays with the opening manager alone, on the factory-first build."""
    return opening_agent(tuning=FACTORY_FIRST_TUNING)


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_factory_first_makes_the_barracks_and_the_first_refinery_then_the_war_factory_the_second_refinery_and_the_depot(
    faction,
):
    assert build_steps(FAMILY_OF[faction], FACTORY_FIRST) == FACTORY_FIRST_STEPS[faction]


@pytest.mark.parametrize("faction", ["england", "russia"])
@pytest.mark.parametrize("standing", range(7))
def test_factory_first_orders_the_step_after_those_that_stand_and_a_power_plant_before_the_second_refinery(
    faction, standing
):
    built = FACTORY_FIRST_BUILT[faction][:standing]

    orders = ask(factory_first_agent(), base_state(faction, YARD, *built))

    assert builds(orders) == [FACTORY_FIRST_NEXT[faction][standing]]


@pytest.mark.parametrize("faction", ["england", "russia"])
@pytest.mark.parametrize("standing", range(1, 6))
def test_factory_first_a_power_plant_goes_ahead_of_any_step_that_would_take_the_headroom_below_zero(faction, standing):
    built = (*FACTORY_FIRST_BUILT[faction][:4], ("proc", 18, 39))[:standing]
    step = FACTORY_FIRST_STEPS[faction][standing]
    items = situations.tracked_items()
    headroom = sum(items[building_type].power for building_type, _, _ in (YARD, *built))
    short_by = headroom + items[step].power + 1
    drains = [(DEFENSE[faction], 30 + number, 50) for number in range(max(0, -(-short_by // DEFENSE_DRAIN)))]
    agent = factory_first_agent()

    orders = ask(agent, base_state(faction, YARD, *built, *drains))

    assert orders[0] == {"cmd": "build", "item": "powr"} and builds(orders) == ["powr"]
    assert agent.ledger.entries[0].priority.name == "POWER"


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_factory_first_the_build_goes_on_past_the_war_factory_and_is_complete_once_the_depot_stands(faction):
    agent = factory_first_agent()

    ask(agent, base_state(faction, YARD, *FACTORY_FIRST_BUILT[faction][:4], tick=4000))
    with_war_factory = agent.ctx.opening_build.steps
    ask(agent, base_state(faction, YARD, *FACTORY_FIRST_BUILT[faction], tick=4005))
    complete = agent.ctx.opening_build.steps
    ask(agent, base_state(faction, YARD, POWER, tick=4010))

    assert with_war_factory == FACTORY_FIRST_STEPS[faction]
    assert complete == () and agent.ctx.opening_build.steps == ()


@pytest.mark.parametrize(
    "order, last, complete",
    [
        (FACTORY_FIRST, WAR_FACTORY, False),
        (FACTORY_FIRST, DEPOT, True),
        (BARRACKS_FIRST, WAR_FACTORY, True),
        (REFINERY_FIRST, WAR_FACTORY, True),
        (REACH_FIRST, WAR_FACTORY, True),
        (BARRACKS_FIRST, DEPOT, False),
    ],
)
def test_the_build_is_complete_once_its_last_building_stands_the_depot_under_factory_first(order, last, complete):
    agent = opening_agent()
    ask(agent, base_state("england", YARD, POWER, last))

    assert is_build_complete(agent.ctx.production, order) is complete


def test_factory_first_is_read_from_the_file_and_the_config_check_passes(tmp_path):
    root = config_with_order(tmp_path, 'order = "factory_first"')

    assert Tuning.from_file(root).opening == dataclasses.replace(OpeningTuning(), order=FACTORY_FIRST)
    assert local.check_config(root).status == PASS


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_factory_first_one_rifleman_is_trained_as_soon_as_the_barracks_stands_and_the_infantry_queue_is_claimed(
    faction,
):
    agent = factory_first_agent()

    before = ask(agent, base_state(faction, YARD, POWER, tick=1000))
    orders = ask(agent, base_state(faction, YARD, *FACTORY_FIRST_BUILT[faction][:2], tick=1005))

    assert trains(before) == [] and trains(orders) == [("e1", 1)]
    assert infantry_is_claimed(agent)


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_factory_first_once_the_rifleman_has_shown_no_more_infantry_is_trained_before_the_depot_even_once_it_is_lost(
    faction,
):
    agent = factory_first_agent()
    under_way = FACTORY_FIRST_BUILT[faction][:4]

    shown = ask(agent, base_state(faction, YARD, *under_way, tick=1000, units=rifles(1)))
    lost = ask(agent, base_state(faction, YARD, *under_way, tick=1005))

    assert trains(shown) == trains(lost) == []
    assert infantry_is_claimed(agent)


def test_factory_first_the_rifleman_is_not_ordered_again_while_one_is_on_its_way():
    queued = [situations.job("Infantry", "e1")]
    built = FACTORY_FIRST_BUILT["england"][:2]

    orders = ask(factory_first_agent(), base_state("england", YARD, *built, production=queued))

    assert trains(orders) == []


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_factory_first_the_guard_s_rifles_start_once_the_depot_stands(faction):
    agent = factory_first_agent()
    ask(agent, base_state(faction, YARD, *FACTORY_FIRST_BUILT[faction][:6], tick=1000, units=rifles(1)))

    orders = ask(agent, base_state(faction, YARD, *FACTORY_FIRST_BUILT[faction], tick=1005, units=rifles(1)))

    assert trains(orders) == [("e1", 4)]


# -- the factory-first build in a whole fake match -----------------------------------------------------------------

# The shipped tuning on the factory-first build, whatever order the tracked file ships.
FACTORY_FIRST_SHIPPED = dataclasses.replace(Tuning(), opening=dataclasses.replace(OpeningTuning(), order=FACTORY_FIRST))
SCOUT_VEHICLE_OF = {"england": "jeep", "russia": "ftrk"}
POWER_PRIORITY = "POWER"


@pytest.fixture(scope="module", params=["england", "russia"])
def factory_first_match(request) -> WholeMatch:
    """A whole fake match on the factory-first build with the shipped tuning otherwise, as Allies and as Soviets."""
    return play_whole_match(FACTORY_FIRST_SHIPPED, request.param)


def sent(played: WholeMatch, command: str) -> list:
    """The ledger entries of a command the agent sent, in the order sent."""
    return sorted(
        (entry for entry in played.agent.ledger.entries if entry.order["cmd"] == command),
        key=lambda entry: entry.sent_tick,
    )


def first_sent_at(played: WholeMatch, command: str, item: str) -> int:
    """The tick the first order of a command for an item was sent at."""
    return next(entry.sent_tick for entry in sent(played, command) if entry.order["item"] == item)


def test_factory_first_a_whole_fake_match_runs_without_an_error_within_the_compute_budget(factory_first_match):
    assert len(factory_first_match.compute_ms) == WHOLE_MATCH_STEPS and factory_first_match.data_of("error") == []
    assert factory_first_match.compute_p95_ms <= COMPUTE_P95_MS


def test_factory_first_in_a_whole_fake_match_the_building_orders_follow_the_build_a_power_plant_only_when_short(
    factory_first_match,
):
    items = situations.tracked_items()
    building = [entry for entry in sent(factory_first_match, "build") if items[entry.order["item"]].queue == "Building"]
    to_depot = building[: next(index for index, entry in enumerate(building) if entry.order["item"] == "fix") + 1]
    steps = [entry.order["item"] for entry in to_depot if entry.order["item"] != "powr"]
    later_power = [entry.priority.name for entry in to_depot[1:] if entry.order["item"] == "powr"]

    assert to_depot[0].order["item"] == "powr"
    assert steps == list(FACTORY_FIRST_STEPS[factory_first_match.game.faction][1:])
    assert all(priority == POWER_PRIORITY for priority in later_power)


def test_factory_first_in_a_whole_fake_match_one_rifleman_is_trained_before_the_depot_and_is_the_first_scout(
    factory_first_match,
):
    depot_at = first_sent_at(factory_first_match, "place", "fix")
    rifles = [entry for entry in sent(factory_first_match, "train") if entry.order["item"] == "e1"]
    taken = [event for event in factory_first_match.data_of("scout_slot") if event["kind"] == "assigned"]
    (rifleman,) = [event["unit_id"] for event in taken if event["slot"] == 1]
    first_move = next(entry for entry in sent(factory_first_match, "move") if rifleman in entry.order["units"])
    world = factory_first_match.agent.world

    assert sum(entry.order["count"] for entry in rifles if entry.sent_tick < depot_at) == 1
    assert [event["unit_type"] for event in taken if event["slot"] == 1] == ["e1"]
    assert cells.distance(tuple(first_move.order["to"]), world.enemy_base) < cells.distance(
        world.base_center, world.enemy_base
    )


def test_factory_first_in_a_whole_fake_match_the_vehicle_queue_s_first_unit_is_the_scout_vehicle_in_the_second_slot(
    factory_first_match,
):
    items = situations.tracked_items()
    faction = factory_first_match.game.faction
    war_factory_at = first_sent_at(factory_first_match, "place", "weap")
    vehicles = [
        entry.order["item"]
        for entry in sent(factory_first_match, "train")
        if items[entry.order["item"]].queue == "Vehicle" and entry.sent_tick >= war_factory_at
    ]
    second = [event for event in factory_first_match.data_of("scout_slot") if event["slot"] == 2]

    assert vehicles[0] == SCOUT_VEHICLE_OF[faction]
    assert (second[0]["kind"], second[0]["unit_type"]) == ("assigned", SCOUT_VEHICLE_OF[faction])


# -- one scout rifleman until a release (single_scout_rifle) ---------------------------------------------------------

SINGLE_RIFLE = {
    order: tuning_off(opening=opening_off(order=order, single_scout_rifle=True))
    for order in (BARRACKS_FIRST, FACTORY_FIRST)
}
UNDER_WAY = {BARRACKS_FIRST: (*OPENING["england"], WAR_FACTORY), FACTORY_FIRST: FACTORY_FIRST_BUILT["england"][:4]}


def single_rifle_agent(order: str = BARRACKS_FIRST):
    """An agent that plays with the opening manager alone, one scout rifleman held to until a release."""
    return opening_agent(tuning=SINGLE_RIFLE[order])


@pytest.mark.parametrize("order", [BARRACKS_FIRST, FACTORY_FIRST])
def test_single_rifle_one_rifleman_once_the_barracks_stands_and_none_more_through_the_war_factory(order):
    agent = single_rifle_agent(order)

    first = ask(agent, base_state("england", YARD, *BARRACKS_BASE[1:], tick=1000))
    shown = ask(agent, base_state("england", YARD, *UNDER_WAY[order], tick=1005, units=rifles(1)))
    lost = ask(agent, base_state("england", YARD, *UNDER_WAY[order], tick=1010))

    assert (trains(first), trains(shown), trains(lost)) == ([("e1", 1)], [], [])
    assert agent.ctx.scout_rifle_hold and infantry_is_claimed(agent)


@pytest.mark.parametrize("order", [BARRACKS_FIRST, FACTORY_FIRST])
@pytest.mark.parametrize("release", ["warning", "verdict", "depot"])
def test_single_rifle_a_rush_warning_the_verdict_or_the_depot_at_150_s_starts_the_guard_s_rifles_in_the_next_cycle(
    order, release
):
    agent = single_rifle_agent(order)
    ask(agent, base_state("england", YARD, *UNDER_WAY[order], tick=3725, units=rifles(1)))
    buildings = (*UNDER_WAY[order], DEPOT) if release == "depot" else UNDER_WAY[order]
    agent.ctx.rush_warning = release == "warning"
    agent.ctx.rush_verdict = "rush" if release == "verdict" else "unknown"

    orders = ask(agent, base_state("england", YARD, *buildings, tick=3750, units=rifles(1)))

    assert not agent.ctx.scout_rifle_hold and ("e1", 4) in trains(orders)


def test_single_rifle_a_threat_at_the_base_releases_the_hold_for_good_and_the_guard_forms_once_it_is_over():
    agent = single_rifle_agent()
    ask(agent, base_state("england", YARD, *UNDER_WAY[BARRACKS_FIRST], tick=1000, units=rifles(1)))
    agent.ctx.threat_active = True
    under_threat = ask(agent, base_state("england", YARD, *UNDER_WAY[BARRACKS_FIRST], tick=1005, units=rifles(1)))
    agent.ctx.threat_active = False

    after = ask(agent, base_state("england", YARD, *UNDER_WAY[BARRACKS_FIRST], tick=1010, units=rifles(1)))

    assert trains(under_threat) == [] and trains(after) == [("e1", 4)]
    assert not agent.ctx.scout_rifle_hold


def test_single_rifle_the_soviet_rocket_cover_is_not_held():
    tuning = tuning_off(opening=opening_off(single_scout_rifle=True))
    agent = opening_agent(tuning=tuning)
    ask(agent, base_state("russia", YARD, POWER, ("barr", 22, 44), tick=1000, units=rifles(1)))

    orders = ask(agent, base_state("russia", YARD, POWER, ("barr", 22, 44), tick=3800, units=rifles(1)))

    assert agent.ctx.scout_rifle_hold and trains(orders) == [("e3", 4)]


@pytest.mark.parametrize("order, trained", [(BARRACKS_FIRST, [("e1", 5)]), (FACTORY_FIRST, [("e1", 1)])])
def test_single_rifle_off_each_order_trains_today_s_count_once_the_barracks_stands(order, trained):
    tuning = tuning_off(opening=opening_off(order=order, single_scout_rifle=False))

    orders = ask(opening_agent(tuning=tuning), base_state("england", *BARRACKS_BASE))

    assert trains(orders) == trained


def test_single_rifle_ships_the_same_in_the_built_ins_and_the_tracked_file():
    assert Tuning.from_file(REPO_ROOT).opening.single_scout_rifle is OpeningTuning().single_scout_rifle


# The shipped tuning on each order with single_scout_rifle on, whatever the tracked file ships.
SHIPPED_ON = {
    order: dataclasses.replace(
        Tuning(), opening=dataclasses.replace(OpeningTuning(), order=order, single_scout_rifle=True)
    )
    for order in (BARRACKS_FIRST, FACTORY_FIRST)
}


@pytest.fixture(scope="module", params=[(BARRACKS_FIRST, "england"), (BARRACKS_FIRST, "russia")])
def barracks_first_match(request) -> WholeMatch:
    """A whole fake match on the barracks-first build with the shipped tuning otherwise, as Allies and as Soviets."""
    order, faction = request.param
    return play_whole_match(SHIPPED_ON[order], faction)


def test_single_rifle_in_a_whole_fake_match_on_barracks_first_one_e1_is_trained_before_the_depot_and_is_the_scout(
    barracks_first_match,
):
    match = barracks_first_match
    depot_at = first_sent_at(match, "place", "fix")
    trained = [entry for entry in sent(match, "train") if entry.order["item"] == "e1" and entry.sent_tick < depot_at]
    first_slot = [event for event in match.data_of("scout_slot") if event["slot"] == 1 and event["kind"] == "assigned"]

    assert sum(entry.order["count"] for entry in trained) == 1
    assert [event["unit_type"] for event in first_slot] == ["e1"]
    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.compute_p95_ms <= COMPUTE_P95_MS


ENEMY_RIFLES_NEAR = [{"id": 500 + number, "type": "e1", "x": 30, "y": 44 + number, "hp": 100} for number in range(8)]


@pytest.mark.parametrize("order", [BARRACKS_FIRST, FACTORY_FIRST])
def test_single_rifle_a_threat_at_120_s_lets_base_defense_s_counter_train_infantry_and_releases_the_hold(order):
    agent = new_agent(BaseDefenseManager(), OpeningManager(), tuning=SINGLE_RIFLE[order])
    ask(agent, base_state("england", YARD, *UNDER_WAY[order], tick=2950, units=rifles(1)))

    orders = ask(
        agent,
        base_state("england", YARD, *UNDER_WAY[order], tick=3000, units=rifles(1), visible_enemies=ENEMY_RIFLES_NEAR),
    )

    assert ("e1", 3) in trains(orders) and agent.ctx.threat_active and not agent.ctx.scout_rifle_hold
