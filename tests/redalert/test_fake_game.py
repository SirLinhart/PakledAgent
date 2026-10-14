"""The fake Red Alert game: the documented state shape, the platform's answers to orders and the modeled base."""

from __future__ import annotations

import ast
import copy
import gzip
import json
import math
from pathlib import Path

import pytest

import fake_game
from agent.agent import create_agent
from altruagent import WAIT, DecisionContext, GameState, WithReasoning, runner
from fake_game import FakeRedAlert
from ucla_agent.games.redalert.items import ItemTable

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "src"
STEPS_PER_SECOND = 5
MCV_ID = fake_game.FIRST_ACTOR_ID
STATE_FIELDS = {
    "state_version",
    "phase",
    "is_current_actor",
    "pacing",
    "limits",
    "time",
    "map",
    "match_rules",
    "observation",
    "last_orders",
    "recent_order_problems",
    "legal_actions",
}
OBSERVATION_FIELDS = {
    "tick",
    "faction",
    "enemy_faction",
    "economy",
    "military",
    "units",
    "buildings",
    "production",
    "available_production",
    "visible_enemies",
    "visible_enemy_buildings",
    "events",
    "base_center",
}
LEGAL_ACTION_LISTS = {
    "build",
    "train",
    "place",
    "deploy",
    "repair",
    "attack",
    "attack_now",
    "enemy_base",
    "combat_units",
    "idle_units",
    "harvesters",
    "production_buildings",
    "how_to_send",
}


def batch(*orders: dict) -> dict:
    """The action object for a batch of orders."""
    return {"type": "orders", "orders": list(orders)}


def advance(game: FakeRedAlert, steps: int) -> None:
    for _ in range(steps):
        game.step()


def build_steps(game: FakeRedAlert, item: str) -> int:
    """How many steps an item takes to build at full power."""
    return round(game.items[item].build_time_s * STEPS_PER_SECOND)


def observe(game: FakeRedAlert) -> dict:
    return game.state()["observation"]


def own(game: FakeRedAlert, kind: str, actor_type: str) -> list[dict]:
    """The player's units or buildings of one type, as the current observation lists them."""
    return [actor for actor in observe(game)[kind] if actor["type"] == actor_type]


def rows(answer: dict) -> list[tuple]:
    """The (cmd, status, reason) of each result row of an answer or of a `last_orders` object."""
    return [(row["cmd"], row["status"], row["reason"]) for row in answer["results"]]


def construct(game: FakeRedAlert, item: str, at: list[int] | None = None) -> dict:
    """Build an item, wait until it is finished, place it and return the building as the next state lists it."""
    before = {building["id"] for building in observe(game)["buildings"]}
    assert rows(game.play_action(batch({"cmd": "build", "item": item}))) == [("build", "pending", "pending")]
    advance(game, build_steps(game, item))
    place = {"cmd": "place", "item": item} if at is None else {"cmd": "place", "item": item, "at": at}
    assert rows(game.play_action(batch(place))) == [("place", "pending", "pending")]
    game.step()
    (created,) = [building for building in observe(game)["buildings"] if building["id"] not in before]
    return created


def train(game: FakeRedAlert, item: str, count: int = 1) -> list[dict]:
    """Train units, wait until all of them exist and return them as the next state lists them."""
    before = {unit["id"] for unit in observe(game)["units"]}
    game.play_action(batch({"cmd": "train", "item": item, "count": count}))
    advance(game, build_steps(game, item) * count)
    return [unit for unit in observe(game)["units"] if unit["id"] not in before]


@pytest.fixture
def game() -> FakeRedAlert:
    """An Allied match at its first state."""
    return FakeRedAlert("england", "russia")


@pytest.fixture
def base(game: FakeRedAlert) -> FakeRedAlert:
    """An Allied match one step after the MCV was deployed."""
    game.play_action(batch({"cmd": "deploy", "units": [MCV_ID]}))
    game.step()
    return game


@pytest.fixture
def soviet_base() -> FakeRedAlert:
    """A Soviet match one step after the MCV was deployed."""
    game = FakeRedAlert("russia", "england")
    game.play_action(batch({"cmd": "deploy", "units": [MCV_ID]}))
    game.step()
    return game


@pytest.fixture
def barracks_base(base: FakeRedAlert) -> FakeRedAlert:
    """An Allied base with a power plant and a barracks."""
    construct(base, "powr")
    construct(base, "tent")
    return base


# -- the first state -------------------------------------------------------------------------------


def test_the_first_state_has_5000_cash_one_mcv_and_names_it_for_deploy(game):
    state = game.state()

    assert state["observation"]["economy"]["cash"] == 5000
    assert [(unit["id"], unit["type"]) for unit in state["observation"]["units"]] == [(MCV_ID, "mcv")]
    assert (state["phase"], state["is_current_actor"], state["state_version"]) == ("playing", True, 0)
    assert state["legal_actions"]["deploy"] == [MCV_ID]


def test_a_state_carries_every_documented_field(game):
    state = game.state()

    assert STATE_FIELDS <= set(state)
    assert OBSERVATION_FIELDS <= set(state["observation"])
    assert LEGAL_ACTION_LISTS == set(state["legal_actions"])
    assert state["pacing"]["mode"] == "realtime"
    assert state["limits"] == {
        "max_orders_per_batch": 20,
        "max_units_per_order": 50,
        "max_train_count": 10,
        "min_batch_interval_s": 0.2,
        "stale_seconds": 120,
    }
    assert state["map"] == {"id": fake_game.MAP_ID, "width": 96, "height": 96, "bounds": [0, 0, 96, 96]}
    assert state["match_rules"]["starting_cash"] == 5000 and state["match_rules"]["starting_units"] == "none"
    assert (state["your_seat"], [player["faction"] for player in state["players"]]) == ("Multi0", ["england", "russia"])


def test_the_first_state_reports_the_factions_the_spawn_and_the_enemy_start(game):
    state = game.state()
    observation = state["observation"]

    assert (observation["faction"], observation["enemy_faction"]) == ("england", "russia")
    assert (observation["base_center"], state["legal_actions"]["enemy_base"]) == ([22, 48], [74, 48])
    assert (observation["units"][0]["x"], observation["units"][0]["y"]) == (22, 48)
    assert observation["available_production"] == [] and observation["production"] == []
    assert state["last_orders"] is None and state["recent_order_problems"] == []
    assert state["time"] == {"max_minutes": 20, "game_seconds": 0.0, "remaining_seconds": 1200.0}


def test_a_step_is_five_ticks_and_a_fifth_of_a_second(game):
    advance(game, 3)
    state = game.state()

    assert (state["state_version"], state["observation"]["tick"]) == (15, 15)
    assert state["time"]["game_seconds"] == pytest.approx(0.6)
    assert state["time"]["remaining_seconds"] == pytest.approx(1199.4)


def test_each_state_is_a_snapshot_that_later_play_does_not_change(game):
    first = game.state()
    kept = copy.deepcopy(first)

    game.play_action(batch({"cmd": "deploy", "units": [MCV_ID]}))
    advance(game, 2)

    assert first == kept


# -- the game reference ----------------------------------------------------------------------------


def test_the_game_config_rules_are_the_fallback_file_s(game):
    file_table = ItemTable.from_file(REPO_ROOT)
    rules = game.game_config["rules"]

    assert rules["buildings"]["pbox"] == {
        "cost": 600,
        "build_time_s": pytest.approx(14.4),
        "power": -20,
        "prerequisites": ["tent"],
        "queue": "Defense",
        "hp": 40000,
        "speed": None,
        "range": 6.0,
        "factions": ["england", "france", "germany"],
    }
    assert rules["units"]["mcv"]["build_time_s"] == 40.0
    assert set(rules["buildings"]) | set(rules["units"]) == {item.name for item in file_table}
    over_the_file = file_table.with_game_config(game.game_config)
    for item in file_table:
        same = over_the_file[item.name]
        assert same.build_time_s == pytest.approx(item.build_time_s)
        assert (same.cost, same.power, same.queue, same.prerequisites, same.factions) == (
            item.cost,
            item.power,
            item.queue,
            item.prerequisites,
            item.factions,
        )
        assert (same.hp, same.speed, same.range, same.is_building) == (
            item.hp,
            item.speed,
            item.range,
            item.is_building,
        )


def test_the_game_config_has_one_map_with_the_two_spawns_and_an_ore_patch_north_of_each(game):
    arena = game.game_config["rules"]["maps"][fake_game.MAP_ID]
    picture = arena["layout"]["rows"]

    assert game.game_config["maps"] == [fake_game.MAP_ID]
    assert (arena["width"], arena["height"], arena["bounds"]) == (96, 96, [0, 0, 96, 96])
    assert arena["spawns"] == [[22, 48], [74, 48]]
    assert len(picture) == 96 and {len(row) for row in picture} == {96}
    assert (picture[48][22], picture[48][74]) == ("S", "S")
    assert (picture[40][22], picture[40][74], picture[41][23], picture[38][22]) == ("$", "$", "$", ".")
    assert "".join(picture).count("$") == 18


# -- deploy ----------------------------------------------------------------------------------------


def test_deploy_turns_the_mcv_into_a_construction_yard_with_a_new_id(game):
    answer = game.play_action(batch({"cmd": "deploy", "units": [MCV_ID]}))
    game.step()
    state = game.state()
    (yard,) = state["observation"]["buildings"]
    (event,) = state["observation"]["events"]

    assert rows(answer) == [("deploy", "pending", "pending")]
    assert (yard["type"], yard["x"], yard["y"]) == ("fact", 22, 48) and yard["id"] != MCV_ID
    assert state["observation"]["units"] == [] and state["legal_actions"]["deploy"] == []
    assert (event["kind"], event["id"], event["type"], event["seq"]) == ("building_created", yard["id"], "fact", 1)
    assert rows(state["last_orders"]) == [("deploy", "ok", "ok")]


def test_only_an_mcv_can_be_deployed(barracks_base):
    (rifle,) = train(barracks_base, "e1")

    answer = barracks_base.play_action(
        batch({"cmd": "deploy", "units": [rifle["id"]]}, {"cmd": "build", "item": "powr"})
    )

    assert rows(answer)[0] == ("deploy", "rejected", "invalid")
    assert answer["results"][0]["failed"] == [{"unit": rifle["id"], "reason": "invalid"}]


def test_a_deploy_the_scenario_refuses_for_room_is_rejected_invalid_and_the_mcv_stays_until_the_next(game):
    game.refuse_deploy(1)

    game.play_action(batch({"cmd": "deploy", "units": [MCV_ID]}))
    game.step()
    refused = game.state()
    game.play_action(batch({"cmd": "deploy", "units": [MCV_ID]}))
    game.step()
    deployed = game.state()

    assert rows(refused["last_orders"]) == [("deploy", "rejected", "invalid")]
    assert [unit["id"] for unit in refused["observation"]["units"]] == [MCV_ID]
    assert refused["observation"]["buildings"] == []
    assert rows(deployed["last_orders"]) == [("deploy", "ok", "ok")]
    assert [building["type"] for building in deployed["observation"]["buildings"]] == ["fact"]


# -- production ------------------------------------------------------------------------------------


def test_a_power_plant_takes_36_steps_costs_300_in_all_and_adds_100_power_when_placed(base):
    base.play_action(batch({"cmd": "build", "item": "powr"}))
    advance(base, 35)
    (unfinished,) = observe(base)["production"]
    base.step()
    (finished,) = observe(base)["production"]

    assert (unfinished["ready"], unfinished["progress"], unfinished["remaining_s"]) == (False, 0.972, 0.2)
    assert finished == {
        "queue": "Building",
        "item": "powr",
        "progress": 1.0,
        "remaining_s": 0.0,
        "paused": False,
        "ready": True,
    }
    assert observe(base)["economy"]["cash"] == 5000 - 300
    assert base.state()["legal_actions"]["place"] == ["powr"]
    assert observe(base)["events"][-1] == {"seq": 2, "tick": 185, "kind": "production_ready", "type": "powr"}

    answer = base.play_action(batch({"cmd": "place", "item": "powr"}))
    base.step()
    state = base.state()

    assert rows(answer) == [("place", "pending", "pending")] and rows(state["last_orders"]) == [("place", "ok", "ok")]
    assert [building["type"] for building in state["observation"]["buildings"]] == ["fact", "powr"]
    assert (state["observation"]["economy"]["power_provided"], state["observation"]["production"]) == (100, [])
    assert state["observation"]["economy"]["cash"] == 4700
    assert state["observation"]["events"][-1]["kind"] == "building_created"


def test_the_cost_of_an_item_is_paid_evenly_while_it_is_built(base):
    base.play_action(batch({"cmd": "build", "item": "powr"}))

    advance(base, 18)

    assert observe(base)["economy"]["cash"] == 5000 - 150
    assert observe(base)["production"][0]["progress"] == 0.5


def test_production_pauses_at_zero_cash_and_goes_on_when_cash_returns(base):
    base.play_action(batch({"cmd": "build", "item": "powr"}))
    advance(base, 6)
    base.cash = 0

    advance(base, 10)
    stalled = observe(base)["production"][0]
    base.cash = 1000
    advance(base, 30)

    assert (stalled["progress"], stalled["paused"], stalled["ready"]) == (pytest.approx(6 / 36, abs=0.001), True, False)
    assert observe(base)["production"][0]["ready"] is True
    assert observe(base)["economy"]["cash"] == 1000 - 250


def test_cash_that_covers_part_of_a_step_buys_that_part(base):
    base.play_action(batch({"cmd": "build", "item": "powr"}))
    base.cash = 4

    base.step()

    assert observe(base)["economy"]["cash"] == 0
    assert observe(base)["production"][0]["progress"] == pytest.approx(4 / 300, abs=0.001)
    assert observe(base)["production"][0]["paused"] is False


def test_placing_before_the_item_is_finished_is_rejected_not_ready(base):
    base.play_action(batch({"cmd": "build", "item": "powr"}))
    advance(base, 10)

    answer = base.play_action(batch({"cmd": "place", "item": "powr"}, {"cmd": "build", "item": "powr"}))
    alone = base.play_action(batch({"cmd": "place", "item": "powr"}))

    assert rows(answer) == [("place", "rejected", "not_ready"), ("build", "pending", "pending")]
    assert alone == {"error": "INVALID_ACTION", "detail": "No valid order: [0] place: not_ready (powr is not finished)"}


def test_a_heavy_tank_cannot_be_trained_without_a_service_depot(soviet_base):
    construct(soviet_base, "powr")
    construct(soviet_base, "proc")
    construct(soviet_base, "weap")
    without_depot = soviet_base.play_action(batch({"cmd": "train", "item": "3tnk"}, {"cmd": "train", "item": "ftrk"}))
    available_before = set(observe(soviet_base)["available_production"])

    construct(soviet_base, "fix")
    with_depot = soviet_base.play_action(batch({"cmd": "train", "item": "3tnk"}))

    assert rows(without_depot) == [("train", "rejected", "not_available"), ("train", "pending", "pending")]
    assert without_depot["results"][0]["detail"] == "3tnk is not in available_production"
    assert {"ftrk", "apc", "harv"} <= available_before and "3tnk" not in available_before
    assert rows(with_depot) == [("train", "pending", "pending")]


def test_each_family_is_offered_its_own_buildings_once_their_prerequisites_stand(base, soviet_base):
    assert observe(base)["available_production"] == ["powr"]
    construct(base, "powr")
    construct(soviet_base, "powr")

    allied, soviet = set(observe(base)["available_production"]), set(observe(soviet_base)["available_production"])

    assert {"powr", "tent", "proc"} <= allied and {"powr", "barr", "proc", "kenn"} <= soviet
    assert "barr" not in allied and "tent" not in soviet
    assert not {"weap", "pbox", "e1", "fact"} & allied


def test_the_legal_action_lists_split_what_is_available_into_build_and_train(barracks_base):
    legal = barracks_base.state()["legal_actions"]

    assert {"powr", "proc", "pbox", "gun"} <= set(legal["build"]) and {"e1", "e3", "medi"} <= set(legal["train"])
    assert set(legal["build"]) | set(legal["train"]) == set(observe(barracks_base)["available_production"])
    assert not set(legal["build"]) & set(legal["train"])
    assert [building["type"] for building in legal["production_buildings"]] == ["fact", "tent"]


def test_a_queue_works_on_one_item_at_a_time_and_the_queues_work_side_by_side(barracks_base):
    cash = observe(barracks_base)["economy"]["cash"]
    barracks_base.play_action(batch({"cmd": "train", "item": "e1", "count": 3}, {"cmd": "build", "item": "pbox"}))

    advance(barracks_base, 6)
    halfway = {(job["queue"], job["progress"]) for job in observe(barracks_base)["production"]}
    advance(barracks_base, 6)
    after_one = observe(barracks_base)
    advance(barracks_base, 24)
    after_three = observe(barracks_base)

    assert halfway == {("Infantry", 0.5), ("Infantry", 0.0), ("Defense", round(6 / 72, 3))}
    assert [unit["type"] for unit in after_one["units"]] == ["e1"]
    assert [job["item"] for job in after_one["production"]] == ["pbox", "e1", "e1"]
    assert [unit["type"] for unit in after_three["units"]] == ["e1", "e1", "e1"]
    assert after_three["economy"]["cash"] == cash - 300 - 300
    assert [event["kind"] for event in after_three["events"]].count("unit_created") == 3


def test_a_finished_building_waits_in_its_queue_and_holds_up_the_next_one(base):
    base.play_action(batch({"cmd": "build", "item": "powr"}, {"cmd": "build", "item": "powr"}))

    advance(base, 36 + 10)
    waiting = observe(base)["production"]
    base.play_action(batch({"cmd": "place", "item": "powr"}))
    advance(base, 10)

    assert [(job["ready"], job["progress"]) for job in waiting] == [(True, 1.0), (False, 0.0)]
    assert [(job["ready"], job["progress"]) for job in observe(base)["production"]] == [(False, round(10 / 36, 3))]


def test_a_new_unit_appears_within_two_cells_of_the_building_that_made_it(barracks_base):
    (barracks,) = own(barracks_base, "buildings", "tent")

    rifles = train(barracks_base, "e1", 5)

    assert len(rifles) == 5
    for rifle in rifles:
        assert max(abs(rifle["x"] - barracks["x"]), abs(rifle["y"] - barracks["y"])) in (1, 2)
        assert (rifle["idle"], rifle["act"], rifle["hp"], rifle["stance"]) == (True, None, 100, "attack_anything")


def test_the_seed_decides_where_new_units_appear():
    def rifle_cells(seed: int) -> list[tuple[int, int]]:
        game = FakeRedAlert("england", "russia", seed=seed)
        game.play_action(batch({"cmd": "deploy", "units": [MCV_ID]}))
        game.step()
        construct(game, "powr")
        construct(game, "tent")
        return [(rifle["x"], rifle["y"]) for rifle in train(game, "e1", 6)]

    assert rifle_cells(7) == rifle_cells(7)
    assert rifle_cells(7) != rifle_cells(8)


def test_cancel_takes_the_newest_item_of_that_type_out_and_gives_the_money_back(base):
    base.play_action(batch({"cmd": "build", "item": "powr"}, {"cmd": "build", "item": "powr"}))
    advance(base, 18)

    first = base.play_action(batch({"cmd": "cancel", "item": "powr"}))
    base.step()
    progress_after_first = [job["progress"] for job in observe(base)["production"]]
    second = base.play_action(batch({"cmd": "cancel", "item": "powr"}))
    base.step()
    nothing_left = base.play_action(batch({"cmd": "cancel", "item": "powr"}, {"cmd": "build", "item": "powr"}))

    assert rows(first) == rows(second) == [("cancel", "pending", "pending")]
    assert progress_after_first == [round(19 / 36, 3)]
    assert observe(base)["economy"]["cash"] == 5000 and observe(base)["production"][0]["progress"] == 0.0
    assert rows(nothing_left)[0] == ("cancel", "rejected", "not_available")


def test_a_second_cancel_of_the_only_queued_item_in_one_batch_is_refused_by_the_game(base):
    base.play_action(batch({"cmd": "build", "item": "powr"}))

    answer = base.play_action(batch({"cmd": "cancel", "item": "powr"}, {"cmd": "cancel", "item": "powr"}))
    base.step()

    assert rows(answer) == [("cancel", "pending", "pending")] * 2
    assert rows(base.state()["last_orders"]) == [("cancel", "ok", "ok"), ("cancel", "rejected", "not_available")]
    assert observe(base)["production"] == []


# -- power -----------------------------------------------------------------------------------------


def test_power_is_what_the_switched_on_buildings_provide_and_drain(barracks_base):
    economy = observe(barracks_base)["economy"]
    (plant,) = own(barracks_base, "buildings", "powr")

    barracks_base.play_action(batch({"cmd": "power_toggle", "building": plant["id"], "on": False}))
    barracks_base.step()
    dark = observe(barracks_base)

    assert (economy["power_provided"], economy["power_drained"]) == (100, 20)
    assert (dark["economy"]["power_provided"], dark["economy"]["power_drained"]) == (0, 20)
    assert {b["type"]: (b["powered"], b["powered_down"]) for b in dark["buildings"]} == {
        "fact": (True, False),
        "powr": (False, True),
        "tent": (False, False),
    }
    assert dark["events"][-1] == {
        "seq": dark["events"][-1]["seq"],
        "tick": dark["tick"],
        "kind": "low_power",
        "power_provided": 0,
        "power_drained": 20,
    }


def test_with_power_negative_a_300_credit_item_takes_108_steps(barracks_base):
    (plant,) = own(barracks_base, "buildings", "powr")
    barracks_base.play_action(batch({"cmd": "power_toggle", "building": plant["id"], "on": False}))
    barracks_base.step()

    barracks_base.play_action(batch({"cmd": "build", "item": "powr"}))
    advance(barracks_base, 107)
    (unfinished,) = observe(barracks_base)["production"]
    barracks_base.step()
    (finished,) = observe(barracks_base)["production"]

    assert (unfinished["ready"], unfinished["remaining_s"]) == (False, 0.2)
    assert (finished["ready"], finished["progress"]) == (True, 1.0)


def test_power_coming_back_is_reported_once(barracks_base):
    (plant,) = own(barracks_base, "buildings", "powr")
    barracks_base.play_action(batch({"cmd": "power_toggle", "building": plant["id"], "on": False}))
    advance(barracks_base, 3)
    barracks_base.play_action(batch({"cmd": "power_toggle", "building": plant["id"], "on": True}))
    advance(barracks_base, 3)

    kinds = [event["kind"] for event in observe(barracks_base)["events"]]

    assert (kinds.count("low_power"), kinds.count("power_restored")) == (1, 1)
    assert observe(barracks_base)["economy"]["power_provided"] == 100


# -- harvesters and income -------------------------------------------------------------------------


def harvesters_in(observation: dict) -> list[dict]:
    return [unit for unit in observation["units"] if unit["type"] == "harv"]


def test_a_refinery_comes_with_one_harvester_and_room_for_2000_ore_and_each_harvester_earns_5_ore_a_step(base):
    construct(base, "powr")
    without_refinery = observe(base)["economy"]
    base.step()
    assert observe(base)["economy"] == without_refinery

    refinery = construct(base, "proc")
    one = observe(base)
    base.step()
    a_step_later = observe(base)["economy"]
    construct(base, "proc")
    two = observe(base)
    base.step()

    (harvester,) = harvesters_in(one)
    held = (
        without_refinery["resource_capacity"],
        one["economy"]["resource_capacity"],
        two["economy"]["resource_capacity"],
    )
    assert max(abs(harvester["x"] - refinery["x"]), abs(harvester["y"] - refinery["y"])) in (1, 2)
    assert (harvester["idle"], harvester["act"], one["economy"]["harvesters"]) == (False, "harvest", 1)
    assert held == (0, 2000, 4000)
    assert (a_step_later["ore"], a_step_later["cash"]) == (one["economy"]["ore"] + 5, one["economy"]["cash"])
    assert (two["economy"]["harvesters"], len(harvesters_in(two))) == (2, 2)
    assert observe(base)["economy"]["ore"] == two["economy"]["ore"] + 10
    assert base.state()["legal_actions"]["harvesters"] == [unit["id"] for unit in two["units"]]


@pytest.fixture
def refinery_base(base: FakeRedAlert) -> FakeRedAlert:
    """A base with a power plant and one refinery whose harvester is gone: 2,000 ore can be held and none comes in."""
    construct(base, "powr")
    construct(base, "proc")
    (harvester,) = own(base, "units", "harv")
    base.kill_own(harvester["id"])
    return base


def money(game: FakeRedAlert) -> tuple[int, int]:
    """The player's (cash, ore)."""
    economy = observe(game)["economy"]
    return economy["cash"], economy["ore"]


def test_ore_stops_at_what_the_refineries_can_hold_and_what_is_earned_beyond_that_is_lost(base):
    construct(base, "powr")
    construct(base, "proc")
    base.ore = 1990

    advance(base, 3)

    economy = observe(base)["economy"]
    assert (economy["ore"], economy["resource_capacity"], economy["cash"]) == (2000, 2000, 5000 - 300 - 1400)


def test_the_income_of_a_step_is_stored_before_the_step_s_production_is_paid_for(base):
    construct(base, "powr")
    construct(base, "proc")
    base.cash, base.ore = 0, 0

    base.play_action(batch({"cmd": "build", "item": "powr"}))
    advance(base, 36)

    # One harvester earns 5 a step and a power plant costs 300 over 36 steps: the income alone does not keep it going.
    job = observe(base)["production"][0]
    assert money(base) == (0, 0)
    assert (job["ready"], job["progress"]) == (False, pytest.approx(180 / 300, abs=0.01))


def test_with_no_cash_and_600_ore_a_300_credit_item_is_built_and_paid_for_from_the_ore(refinery_base):
    refinery_base.cash, refinery_base.ore = 0, 600

    refinery_base.play_action(batch({"cmd": "build", "item": "powr"}))
    advance(refinery_base, build_steps(refinery_base, "powr"))

    job = observe(refinery_base)["production"][0]
    assert (job["ready"], job["paused"]) == (True, False)
    assert money(refinery_base) == (0, 300)


def test_spending_draws_on_the_ore_first_and_on_the_cash_for_what_the_ore_does_not_cover(refinery_base):
    refinery_base.cash, refinery_base.ore = 1000, 100

    refinery_base.play_action(batch({"cmd": "build", "item": "powr"}))
    advance(refinery_base, 6)
    after_50 = money(refinery_base)
    advance(refinery_base, 12)
    after_150 = money(refinery_base)
    advance(refinery_base, 18)

    assert (after_50, after_150, money(refinery_base)) == ((1000, 50), (950, 0), (800, 0))


def test_production_pauses_only_when_the_cash_and_the_ore_are_both_gone(refinery_base):
    refinery_base.cash, refinery_base.ore = 0, 50
    refinery_base.play_action(batch({"cmd": "build", "item": "powr"}))

    advance(refinery_base, 6)
    on_the_last_ore = observe(refinery_base)["production"][0]
    advance(refinery_base, 4)
    stalled = observe(refinery_base)["production"][0]
    refinery_base.ore = 400
    advance(refinery_base, 30)

    assert (on_the_last_ore["paused"], money(refinery_base)) == (False, (0, 150))
    assert (stalled["paused"], stalled["progress"]) == (True, pytest.approx(6 / 36, abs=0.001))
    assert observe(refinery_base)["production"][0]["ready"] is True


def test_ore_that_a_lost_refinery_held_is_lost_with_it(refinery_base):
    refinery_base.ore = 1500
    (refinery,) = own(refinery_base, "buildings", "proc")

    refinery_base.kill_own(refinery["id"])
    refinery_base.step()

    economy = observe(refinery_base)["economy"]
    assert (economy["ore"], economy["resource_capacity"]) == (0, 0)


def test_a_harvester_that_was_moved_is_idle_until_it_is_told_to_harvest_again(base):
    construct(base, "powr")
    construct(base, "proc")
    (harvester,) = own(base, "units", "harv")

    base.play_action(batch({"cmd": "move", "units": [harvester["id"]], "to": [harvester["x"] + 1, harvester["y"]]}))
    advance(base, 5)
    moved = own(base, "units", "harv")[0]
    base.play_action(batch({"cmd": "harvest", "units": [harvester["id"]], "to": [22, 40]}))
    advance(base, 60)
    sent_to_the_ore = own(base, "units", "harv")[0]

    assert (moved["idle"], moved["act"]) == (True, None)
    assert (sent_to_the_ore["x"], sent_to_the_ore["y"]) == (22, 40)
    assert (sent_to_the_ore["idle"], sent_to_the_ore["act"]) == (False, "harvest")


# -- movement --------------------------------------------------------------------------------------


@pytest.fixture
def fast_rifles() -> FakeRedAlert:
    """An Allied base with a barracks whose rifle infantry walk 2.0 cells a second."""
    quick = ItemTable.from_file(REPO_ROOT).with_game_config({"rules": {"units": {"e1": {"speed": 2.0}}}})
    game = FakeRedAlert("england", "russia", items=quick)
    game.play_action(batch({"cmd": "deploy", "units": [MCV_ID]}))
    game.step()
    construct(game, "powr")
    construct(game, "tent")
    return game


def test_a_unit_10_cells_from_its_goal_at_2_cells_a_second_arrives_after_25_steps(fast_rifles):
    (rifle,) = train(fast_rifles, "e1")
    goal = [rifle["x"] + 10, rifle["y"]]

    fast_rifles.play_action(batch({"cmd": "move", "units": [rifle["id"]], "to": goal}))
    advance(fast_rifles, 12)
    (halfway,) = own(fast_rifles, "units", "e1")
    advance(fast_rifles, 12)
    (nearly_there,) = own(fast_rifles, "units", "e1")
    fast_rifles.step()
    (arrived,) = own(fast_rifles, "units", "e1")

    # 12 steps at 2.0 cells a second are 4.8 cells, shown as the nearest cell.
    assert (halfway["x"], halfway["y"], halfway["idle"], halfway["act"]) == (rifle["x"] + 5, rifle["y"], False, "move")
    assert (nearly_there["idle"], nearly_there["act"]) == (False, "move")
    assert ([arrived["x"], arrived["y"]], arrived["idle"], arrived["act"]) == (goal, True, None)


def test_a_unit_walks_in_a_straight_line_at_its_table_speed(barracks_base):
    (rifle,) = train(barracks_base, "e1")

    barracks_base.play_action(
        batch({"cmd": "attack_move", "units": [rifle["id"]], "to": [rifle["x"] + 30, rifle["y"] + 40]})
    )
    advance(barracks_base, 50)
    (walking,) = own(barracks_base, "units", "e1")

    # 50 steps at 1.32 cells a second are 13.2 cells along a 3-4-5 line: 7.92 across and 10.56 down.
    assert (walking["x"], walking["y"]) == (rifle["x"] + 8, rifle["y"] + 11)
    assert (walking["idle"], walking["act"]) == (False, "attack_move")


def test_stop_ends_a_walk_and_set_stance_changes_the_stance(barracks_base):
    (rifle,) = train(barracks_base, "e1")
    barracks_base.play_action(batch({"cmd": "move", "units": [rifle["id"]], "to": [60, 60]}))
    advance(barracks_base, 5)

    barracks_base.play_action(
        batch(
            {"cmd": "stop", "units": [rifle["id"]]},
            {"cmd": "set_stance", "units": [rifle["id"]], "stance": "hold_fire"},
        )
    )
    barracks_base.step()
    (stopped,) = own(barracks_base, "units", "e1")
    advance(barracks_base, 5)

    assert (stopped["idle"], stopped["act"], stopped["stance"]) == (True, None, "hold_fire")
    assert own(barracks_base, "units", "e1")[0]["x"] == stopped["x"]


# -- the military values ---------------------------------------------------------------------------


def test_army_value_is_the_cost_of_the_combat_units_and_assets_value_the_cost_of_everything(barracks_base):
    construct(barracks_base, "proc")
    train(barracks_base, "e1", 3)
    train(barracks_base, "e3")
    train(barracks_base, "medi")

    military = observe(barracks_base)["military"]

    assert military["army_value"] == 3 * 100 + 300
    assert military["assets_value"] == (2000 + 300 + 500 + 1400) + 1100 + 3 * 100 + 300 + 200
    assert (military["kills_cost"], military["deaths_cost"]) == (0, 0)


def test_the_ready_made_attack_sends_the_idle_fighters_to_the_enemy_start(barracks_base):
    assert barracks_base.state()["legal_actions"]["attack_now"] is None
    rifles = train(barracks_base, "e1", 2)
    train(barracks_base, "medi")

    legal = barracks_base.state()["legal_actions"]

    assert legal["attack_now"] == {"cmd": "attack_move", "units": [rifle["id"] for rifle in rifles], "to": [74, 48]}
    assert legal["combat_units"] == [rifle["id"] for rifle in rifles]
    assert len(legal["idle_units"]) == 3

    barracks_base.play_action(batch(legal["attack_now"]))
    barracks_base.step()
    marching = barracks_base.state()["legal_actions"]

    assert (marching["combat_units"], len(marching["idle_units"])) == ([], 1)
    assert marching["attack_now"]["units"] == [rifle["id"] for rifle in rifles]


# -- transports ------------------------------------------------------------------------------------

APC_CELL = (40, 40)
# Six cells within 2 of the APC's cell, where rocket soldiers stand ready to board.
BESIDE_THE_APC = [(40, 42), (41, 41), (39, 41), (42, 40), (38, 40), (40, 38)]


def rockets_at(game: FakeRedAlert, cells: list[tuple[int, int]]) -> list[int]:
    """Put a rocket soldier of the player's on each cell and return their ids."""
    return [game.add_own("e3", x, y) for x, y in cells]


def board(game: FakeRedAlert, units: list[int], transport: int) -> dict:
    """Order the units into the transport and return the answer."""
    return game.play_action(batch({"cmd": "enter_transport", "units": units, "target": transport}))


def lost_ids(observation: dict) -> list[int]:
    """The ids of the `unit_lost` events in the event window, oldest first."""
    return [event["id"] for event in observation["events"] if event["kind"] == "unit_lost"]


@pytest.fixture
def apc_base(soviet_base: FakeRedAlert) -> tuple[FakeRedAlert, int]:
    """A Soviet base with an APC of the player's standing on `APC_CELL`, and the APC's id."""
    return soviet_base, soviet_base.add_own("apc", *APC_CELL)


def test_add_own_puts_a_new_unit_such_as_a_transport_on_the_cell_and_reports_it_created(soviet_base):
    apc = soviet_base.add_own("APC", *APC_CELL)
    observation = observe(soviet_base)

    (listed,) = [unit for unit in observation["units"] if unit["id"] == apc]
    assert (listed["type"], listed["x"], listed["y"], listed["idle"]) == ("apc", 40, 40, True)
    assert observation["events"][-1] == {
        "seq": observation["events"][-1]["seq"],
        "tick": observation["tick"],
        "kind": "unit_created",
        "id": apc,
        "type": "apc",
        "x": 40,
        "y": 40,
    }


def test_five_units_within_2_cells_board_their_transport_in_the_next_step_each_reported_lost_once(apc_base):
    game, apc = apc_base
    rockets = rockets_at(game, BESIDE_THE_APC[:5])
    army_before = observe(game)["military"]["army_value"]

    answer = board(game, rockets, apc)
    game.step()
    observation = observe(game)

    assert rows(answer) == [("enter_transport", "pending", "pending")]
    assert rows(game.state()["last_orders"]) == [("enter_transport", "ok", "ok")]
    assert [unit["id"] for unit in observation["units"] if unit["type"] == "e3"] == []
    assert lost_ids(observation) == rockets and game.aboard(apc) == rockets
    # The units aboard are alive and the player's own: the army keeps its value and nothing is lost.
    assert (army_before, observation["military"]["army_value"]) == (850 + 5 * 300, 850 + 5 * 300)
    assert (observation["military"]["deaths_cost"], observation["military"]["units_lost"]) == (0, 0)


def test_a_unit_ordered_into_a_transport_10_cells_away_walks_to_it_and_boards_once_within_2_cells(apc_base):
    game, apc = apc_base
    (rocket,) = rockets_at(game, [(50, 40)])

    board(game, [rocket], apc)
    advance(game, 28)
    (walking,) = own(game, "units", "e3")
    game.step()

    # At 1.32 cells a second a rocket soldier covers 0.264 cells a step: 7.39 cells after 28 steps leave it on the
    # cell 3 from the APC, and 7.66 after 29 put it on the cell 2 from it.
    assert (walking["x"], walking["y"], walking["act"], walking["idle"]) == (43, 40, "enter_transport", False)
    assert own(game, "units", "e3") == [] and game.aboard(apc) == [rocket]
    assert lost_ids(observe(game)) == [rocket]


def test_a_transport_takes_five_and_a_sixth_unit_stops_beside_it_without_boarding(apc_base):
    game, apc = apc_base
    rockets = rockets_at(game, BESIDE_THE_APC)

    board(game, rockets, apc)
    game.step()
    advance(game, 5)
    observation = observe(game)

    (sixth,) = own(game, "units", "e3")
    assert game.aboard(apc) == rockets[:5] and lost_ids(observation) == rockets[:5]
    assert (sixth["id"], sixth["act"], sixth["idle"]) == (rockets[5], None, True)


def test_unload_puts_the_transport_s_cargo_on_the_free_cells_next_to_it_under_the_same_ids(apc_base):
    game, apc = apc_base
    rockets = rockets_at(game, BESIDE_THE_APC[:5])
    board(game, rockets, apc)
    game.step()
    game.play_action(batch({"cmd": "move", "units": [apc], "to": [60, 40]}))
    advance(game, 40)
    (moved,) = own(game, "units", "apc")
    # A rifleman stands on the cell nearest the APC's, which unloading must leave to him.
    rifle = game.add_own("e1", moved["x"], moved["y"] - 1)
    events_before = observe(game)["events"]

    answer = game.play_action(batch({"cmd": "unload", "units": [apc]}))
    game.step()
    observation = observe(game)

    unloaded = {unit["id"]: (unit["x"] - moved["x"], unit["y"] - moved["y"]) for unit in own(game, "units", "e3")}
    (apc_now,) = own(game, "units", "apc")
    assert rows(answer) == [("unload", "pending", "pending")]
    assert (moved["x"], moved["y"]) == (60, 40) and game.aboard(apc) == []
    assert unloaded == dict(zip(rockets, [(-1, 0), (1, 0), (0, 1), (-1, -1), (1, -1)]))
    assert (apc_now["x"], apc_now["y"], apc_now["idle"]) == (60, 40, True)
    assert rifle in {unit["id"] for unit in observation["units"]}
    assert observation["events"] == events_before


def test_unload_keeps_aboard_the_units_for_which_no_cell_next_to_the_transport_is_free(soviet_base):
    # In the map's corner only five cells within 2 of the APC's cell are on the map.
    apc = soviet_base.add_own("apc", 0, 0)
    rockets = rockets_at(soviet_base, [(1, 0), (0, 1), (1, 1), (2, 0), (0, 2)])
    board(soviet_base, rockets, apc)
    soviet_base.step()
    rifle = soviet_base.add_own("e1", 1, 0)

    soviet_base.play_action(batch({"cmd": "unload", "units": [apc]}))
    left_aboard = soviet_base.aboard(apc)
    soviet_base.play_action(batch({"cmd": "move", "units": [rifle], "to": [10, 10]}))
    # Three steps take the rifleman off the cell he held.
    advance(soviet_base, 3)
    soviet_base.play_action(batch({"cmd": "unload", "units": [apc]}))

    assert left_aboard == rockets[4:] and soviet_base.aboard(apc) == []
    assert {unit["id"] for unit in own(soviet_base, "units", "e3")} == set(rockets)


def test_unload_of_a_transport_that_carries_nothing_changes_nothing(apc_base):
    game, apc = apc_base
    game.play_action(batch({"cmd": "move", "units": [apc], "to": [60, 40]}))
    game.play_action(batch({"cmd": "unload", "units": [apc]}))
    game.step()

    (apc_now,) = own(game, "units", "apc")
    assert (apc_now["act"], game.aboard(apc)) == ("move", [])


def test_a_unit_aboard_a_transport_takes_no_order_and_is_skipped_as_not_owned(apc_base):
    game, apc = apc_base
    (rocket,) = rockets_at(game, BESIDE_THE_APC[:1])
    board(game, [rocket], apc)
    game.step()

    answer = game.play_action(batch({"cmd": "move", "units": [apc, rocket], "to": [50, 50]}))

    (result,) = answer["results"]
    assert (result["status"], result["commands"], result["failed"]) == (
        "pending",
        1,
        [{"unit": rocket, "reason": "not_owned"}],
    )


@pytest.mark.parametrize("target", ["e1", "fact"], ids=["a rifleman", "the construction yard"])
def test_enter_transport_into_an_own_actor_that_is_not_a_transport_is_rejected_invalid(soviet_base, target):
    (rocket,) = rockets_at(soviet_base, [(30, 30)])
    target_id = (
        own(soviet_base, "buildings", "fact")[0]["id"] if target == "fact" else soviet_base.add_own("e1", 31, 30)
    )

    answer = board(soviet_base, [rocket], target_id)

    assert answer["error"] == "INVALID_ACTION" and f"target {target_id} is not a transport" in answer["detail"]


def test_a_transport_cannot_enter_a_transport(apc_base):
    game, apc = apc_base
    other = game.add_own("apc", 42, 40)
    (rocket,) = rockets_at(game, BESIDE_THE_APC[:1])

    answer = board(game, [other, rocket], apc)
    game.step()

    assert answer["results"][0]["failed"] == [{"unit": other, "reason": "invalid"}]
    assert game.aboard(apc) == [rocket] and len(own(game, "units", "apc")) == 2


def test_killing_a_loaded_transport_kills_its_cargo_and_reports_only_the_transport_lost(apc_base):
    game, apc = apc_base
    rockets = rockets_at(game, BESIDE_THE_APC[:5])
    board(game, rockets, apc)
    game.step()
    events_before = len(observe(game)["events"])

    game.kill_own(apc)
    observation = observe(game)

    assert (observation["military"]["deaths_cost"], observation["military"]["units_lost"]) == (850 + 5 * 300, 6)
    assert observation["military"]["army_value"] == 0 and game.aboard(apc) == []
    assert len(observation["events"]) == events_before + 1 and lost_ids(observation) == [*rockets, apc]


def test_removing_a_loaded_transport_takes_its_cargo_with_it_without_a_loss(apc_base):
    game, apc = apc_base
    rockets = rockets_at(game, BESIDE_THE_APC[:2])
    board(game, rockets, apc)
    game.step()

    game.remove_own(apc)
    military = observe(game)["military"]

    assert (military["army_value"], military["deaths_cost"], game.aboard(apc)) == (0, 0, [])


def test_a_unit_walking_to_a_transport_stops_when_the_transport_is_lost_or_it_is_ordered_elsewhere(apc_base):
    game, apc = apc_base
    walker, turned = rockets_at(game, [(50, 40), (50, 44)])
    board(game, [walker, turned], apc)
    advance(game, 5)

    game.play_action(batch({"cmd": "move", "units": [turned], "to": [50, 60]}))
    game.kill_own(apc)
    advance(game, 2)

    acts = {unit["id"]: unit["act"] for unit in own(game, "units", "e3")}
    assert acts == {walker: None, turned: "move"}


# -- placement -------------------------------------------------------------------------------------


def test_a_building_goes_to_the_wanted_cell_when_it_is_free_and_within_16_cells_of_the_yard(base):
    plant = construct(base, "powr", at=[30, 40])
    at_the_edge = construct(base, "powr", at=[38, 48])

    assert (plant["x"], plant["y"]) == (30, 40)
    assert (at_the_edge["x"], at_the_edge["y"]) == (38, 48)


@pytest.mark.parametrize(
    "wanted", [[39, 48], [22, 48], [23, 49], [90, 90]], ids=["too far", "the yard", "next to the yard", "far away"]
)
def test_a_building_goes_to_the_free_cell_nearest_the_yard_when_the_wanted_cell_cannot_be_used(base, wanted):
    plant = construct(base, "powr", at=wanted)

    assert (plant["x"], plant["y"]) == (22, 46)


def test_buildings_placed_without_a_cell_never_touch(base):
    for _ in range(6):
        construct(base, "powr")

    cells = [(building["x"], building["y"]) for building in observe(base)["buildings"]]

    assert len(set(cells)) == 7
    for cell in cells:
        for other in cells:
            assert cell == other or max(abs(cell[0] - other[0]), abs(cell[1] - other[1])) >= 2


def test_a_second_place_of_the_same_finished_item_in_one_batch_is_refused_by_the_game(base):
    base.play_action(batch({"cmd": "build", "item": "powr"}))
    advance(base, 36)

    answer = base.play_action(batch({"cmd": "place", "item": "powr"}, {"cmd": "place", "item": "powr"}))
    base.step()
    state = base.state()

    assert rows(answer) == [("place", "pending", "pending")] * 2
    assert rows(state["last_orders"]) == [("place", "ok", "ok"), ("place", "rejected", "not_ready")]
    assert len(own(base, "buildings", "powr")) == 1


# -- the answer to a batch -------------------------------------------------------------------------


def test_a_batch_of_21_orders_is_refused_whole_and_counted(base):
    answer = base.play_action(batch(*[{"cmd": "build", "item": "powr"}] * 21))

    assert answer == {"error": "INVALID_ACTION", "detail": "a batch takes 1-20 orders, got 21"}
    assert base.invalid_actions == 1
    assert observe(base)["production"] == [] and base.state()["last_orders"]["batch"] == 1


def test_a_batch_of_20_orders_is_accepted(base):
    answer = base.play_action(batch(*[{"cmd": "build", "item": "powr"}] * 20))

    assert (answer["accepted"], answer["accepted_orders"], answer["rejected_orders"]) == (True, 20, 0)
    assert base.invalid_actions == 0 and len(observe(base)["production"]) == 20


@pytest.mark.parametrize(
    "action",
    [None, [], {"orders": []}, {"type": "orders"}, {"type": "orders", "orders": []}, {"type": "move", "orders": [{}]}],
    ids=["nothing", "a list", "no type", "no orders", "an empty batch", "another type"],
)
def test_an_action_that_is_not_a_batch_of_orders_is_refused(base, action):
    answer = base.play_action(action)

    assert answer["error"] == "INVALID_ACTION" and base.invalid_actions == 1


def test_a_batch_that_expands_to_more_than_200_engine_commands_is_refused(barracks_base):
    construct(barracks_base, "proc")
    rifles = [rifle["id"] for rifle in train(barracks_base, "e1", 10)] + [
        r["id"] for r in train(barracks_base, "e1", 1)
    ]
    trains = [{"cmd": "train", "item": "e1", "count": 10}] * 19
    queued_before = len(observe(barracks_base)["production"])

    over = barracks_base.play_action(batch(*trains, {"cmd": "stop", "units": rifles}))
    at_the_limit = barracks_base.play_action(batch(*trains, {"cmd": "stop", "units": rifles[:10]}))

    assert over == {"error": "INVALID_ACTION", "detail": "the batch expands to 201 engine commands; the limit is 200"}
    assert barracks_base.invalid_actions == 1
    assert at_the_limit["accepted"] is True
    assert len(observe(barracks_base)["production"]) == queued_before + 190


def test_a_batch_in_which_no_order_is_valid_is_refused_with_each_reason(base):
    answer = base.play_action(batch({"cmd": "train", "item": "e1"}, {"cmd": "move", "units": [999], "to": [5, 5]}))

    assert answer == {
        "error": "INVALID_ACTION",
        "detail": "No valid order: [0] train: not_available (e1 is not in available_production); "
        "[1] move: not_owned (no unit in this order can take it)",
    }
    assert base.invalid_actions == 1


def test_the_answer_has_the_platform_s_fields_and_no_batch_number(base):
    answer = base.play_action(batch({"cmd": "build", "item": "powr"}, {"cmd": "train", "item": "3tnk"}))

    assert answer == {
        "accepted": True,
        "session_id": fake_game.SESSION_ID,
        "status": "in_progress",
        "state_version": 5,
        "pending": True,
        "applied_at_tick": None,
        "accepted_orders": 1,
        "rejected_orders": 1,
        "next_batch_allowed_in_ms": 200,
        "results": [
            {
                "index": 0,
                "cmd": "build",
                "status": "pending",
                "reason": "pending",
                "detail": None,
                "commands": 1,
                "failed": [],
            },
            {
                "index": 1,
                "cmd": "train",
                "status": "rejected",
                "reason": "not_available",
                "detail": "3tnk is not in available_production",
                "commands": 0,
                "failed": [],
            },
        ],
    }


def test_last_orders_is_pending_until_the_next_state_and_final_after_it(base):
    base.play_action(batch({"cmd": "build", "item": "powr"}, {"cmd": "train", "item": "3tnk"}))
    in_flight = base.state()["last_orders"]
    base.step()
    landed = base.state()["last_orders"]

    assert (in_flight["batch"], in_flight["state_version"], in_flight["pending"], in_flight["applied_at_tick"]) == (
        2,
        5,
        True,
        None,
    )
    assert rows(in_flight) == [("build", "pending", "pending"), ("train", "rejected", "not_available")]
    assert (landed["batch"], landed["pending"], landed["applied_at_tick"]) == (2, False, 10)
    assert rows(landed) == [("build", "ok", "ok"), ("train", "rejected", "not_available")]
    assert (landed["accepted_orders"], landed["rejected_orders"]) == (1, 1)


def test_recent_order_problems_lists_the_problem_rows_of_settled_batches(barracks_base):
    (rifle,) = train(barracks_base, "e1")
    barracks_base.play_action(batch({"cmd": "train", "item": "3tnk"}, {"cmd": "stop", "units": [rifle["id"], 999]}))
    assert barracks_base.state()["recent_order_problems"] == []
    barracks_base.step()
    batch_number = barracks_base.state()["last_orders"]["batch"]

    assert barracks_base.state()["recent_order_problems"] == [
        {
            "batch": batch_number,
            "index": 0,
            "cmd": "train",
            "status": "rejected",
            "reason": "not_available",
            "detail": "3tnk is not in available_production",
        },
        {"batch": batch_number, "index": 1, "cmd": "stop", "status": "partial", "reason": "not_owned", "detail": None},
    ]


def test_recent_order_problems_keeps_five_batches_and_ten_rows(base):
    refused = {"cmd": "train", "item": "3tnk"}
    for _ in range(7):
        base.play_action(batch({"cmd": "build", "item": "powr"}, refused, refused, refused))
        base.step()

    problems = base.state()["recent_order_problems"]
    newest = base.state()["last_orders"]["batch"]

    assert len(problems) == 10
    assert sorted({problem["batch"] for problem in problems}) == [newest - 3, newest - 2, newest - 1, newest]
    assert [problem["batch"] for problem in problems] == sorted(problem["batch"] for problem in problems)


def test_a_batch_sent_before_the_last_one_landed_moves_that_one_s_problems_to_the_recent_list(base):
    base.play_action(batch({"cmd": "build", "item": "powr"}, {"cmd": "train", "item": "3tnk"}))
    base.play_action(batch({"cmd": "build", "item": "powr"}))
    state = base.state()

    assert (state["last_orders"]["batch"], state["last_orders"]["pending"]) == (3, True)
    assert [(problem["batch"], problem["cmd"]) for problem in state["recent_order_problems"]] == [(2, "train")]


def test_every_order_carried_out_is_recorded(base):
    order = {"cmd": "build", "item": "powr"}

    base.play_action(batch(order, {"cmd": "train", "item": "3tnk"}))

    assert base.recorded_orders == [
        {"tick": 0, "batch": 1, "index": 0, "order": {"cmd": "deploy", "units": [MCV_ID]}},
        {"tick": 5, "batch": 2, "index": 0, "order": order},
    ]


# -- the checks on single orders -------------------------------------------------------------------


@pytest.mark.parametrize(
    "order, detail",
    [
        ("build powr", "an order must be an object"),
        ({"item": "powr"}, "unknown cmd None"),
        ({"cmd": "construct", "item": "powr"}, "unknown cmd 'construct'"),
        ({"cmd": "build", "item": "powr", "count": 2}, "unknown field 'count'"),
        ({"cmd": "build"}, "missing field 'item'"),
        ({"cmd": "build", "item": 7}, "item must be a name"),
        ({"cmd": "train", "item": "e1", "count": 11}, "count must be 1-10"),
        ({"cmd": "train", "item": "e1", "count": 0}, "count must be 1-10"),
        ({"cmd": "train", "item": "e1", "count": True}, "count must be 1-10"),
        ({"cmd": "move", "units": [], "to": [5, 5]}, "units must be 1-50 distinct integer ids"),
        ({"cmd": "move", "units": [1, 1], "to": [5, 5]}, "units must be 1-50 distinct integer ids"),
        ({"cmd": "move", "units": list(range(51)), "to": [5, 5]}, "units must be 1-50 distinct integer ids"),
        ({"cmd": "move", "units": ["a"], "to": [5, 5]}, "units must be 1-50 distinct integer ids"),
        ({"cmd": "move", "units": [1], "to": [96, 5]}, "to must be a cell [x, y] on the map, got [96, 5]"),
        ({"cmd": "move", "units": [1], "to": [5]}, "to must be a cell [x, y] on the map, got [5]"),
        ({"cmd": "move", "units": [1], "to": [5, 5], "queued": "yes"}, "queued must be true or false"),
        ({"cmd": "attack", "units": [1]}, "attack takes exactly one of target and to"),
        ({"cmd": "attack", "units": [1], "target": 5, "to": [5, 5]}, "attack takes exactly one of target and to"),
        ({"cmd": "attack", "units": [1], "target": "x"}, "target must be an actor id"),
        (
            {"cmd": "set_stance", "units": [1], "stance": "berserk"},
            "stance must be one of hold_fire, return_fire, defend, attack_anything",
        ),
        ({"cmd": "place", "item": "powr", "at": [-1, 5]}, "at must be a cell [x, y] on the map, got [-1, 5]"),
    ],
)
def test_a_malformed_order_is_rejected_invalid_while_the_rest_of_the_batch_runs(barracks_base, order, detail):
    answer = barracks_base.play_action(batch(order, {"cmd": "build", "item": "powr"}))

    assert (answer["results"][0]["status"], answer["results"][0]["reason"]) == ("rejected", "invalid")
    assert answer["results"][0]["detail"] == detail
    assert rows(answer)[1] == ("build", "pending", "pending")


def test_units_that_are_not_the_player_s_are_skipped_and_the_order_runs_with_the_rest(barracks_base):
    first, second = train(barracks_base, "e1", 2)

    answer = barracks_base.play_action(
        batch({"cmd": "move", "units": [first["id"], 999, second["id"]], "to": [30, 30]})
    )
    barracks_base.step()
    final = barracks_base.state()["last_orders"]["results"][0]

    assert (answer["results"][0]["status"], answer["results"][0]["commands"]) == ("pending", 2)
    assert answer["results"][0]["failed"] == [{"unit": 999, "reason": "not_owned"}]
    assert (final["status"], final["reason"], final["failed"]) == (
        "partial",
        "not_owned",
        [{"unit": 999, "reason": "not_owned"}],
    )
    assert [unit["act"] for unit in own(barracks_base, "units", "e1")] == ["move", "move"]


def test_a_building_takes_stop_stance_attack_and_guard_but_not_a_move(barracks_base):
    (rifle,) = train(barracks_base, "e1")
    (barracks,) = own(barracks_base, "buildings", "tent")

    answer = barracks_base.play_action(
        batch(
            {"cmd": "move", "units": [barracks["id"]], "to": [30, 30]},
            {"cmd": "move", "units": [barracks["id"], rifle["id"]], "to": [30, 30]},
            {"cmd": "stop", "units": [barracks["id"]]},
            {"cmd": "set_stance", "units": [barracks["id"]], "stance": "defend"},
            {"cmd": "guard", "units": [rifle["id"]], "target": barracks["id"]},
            {"cmd": "guard", "units": [rifle["id"]], "target": 999},
        )
    )

    assert rows(answer) == [
        ("move", "rejected", "is_building"),
        ("move", "pending", "pending"),
        ("stop", "pending", "pending"),
        ("set_stance", "pending", "pending"),
        ("guard", "pending", "pending"),
        ("guard", "rejected", "not_owned"),
    ]
    assert answer["results"][1]["failed"] == [{"unit": barracks["id"], "reason": "is_building"}]


def test_an_attack_on_something_that_is_not_in_sight_is_rejected(barracks_base):
    (rifle,) = train(barracks_base, "e1")

    answer = barracks_base.play_action(
        batch(
            {"cmd": "attack", "units": [rifle["id"]], "target": 4242},
            {"cmd": "attack", "units": [rifle["id"]], "to": [40, 48]},
        )
    )
    barracks_base.step()

    assert rows(answer) == [("attack", "rejected", "target_not_visible"), ("attack", "pending", "pending")]
    assert own(barracks_base, "units", "e1")[0]["act"] == "attack"


def test_orders_for_a_building_need_one_of_the_player_s_buildings(barracks_base):
    (rifle,) = train(barracks_base, "e1")
    (plant,) = own(barracks_base, "buildings", "powr")
    (barracks,) = own(barracks_base, "buildings", "tent")

    answer = barracks_base.play_action(
        batch(
            {"cmd": "repair", "building": 999},
            {"cmd": "sell", "building": rifle["id"]},
            {"cmd": "repair", "building": "powr"},
            {"cmd": "repair", "building": plant["id"], "on": "yes"},
            {"cmd": "rally", "building": plant["id"], "to": [30, 30]},
            {"cmd": "set_primary", "building": plant["id"]},
            {"cmd": "rally", "building": barracks["id"], "to": [30, 96]},
            {"cmd": "rally", "building": barracks["id"], "to": [30, 30]},
            {"cmd": "set_primary", "building": barracks["id"]},
            {"cmd": "sell", "building": plant["id"]},
        )
    )
    barracks_base.step()

    assert rows(answer) == [
        ("repair", "rejected", "not_owned"),
        ("sell", "rejected", "not_owned"),
        ("repair", "rejected", "not_owned"),
        ("repair", "rejected", "invalid"),
        ("rally", "rejected", "invalid"),
        ("set_primary", "rejected", "invalid"),
        ("rally", "rejected", "invalid"),
        ("rally", "pending", "pending"),
        ("set_primary", "pending", "pending"),
        ("sell", "pending", "pending"),
    ]
    assert own(barracks_base, "buildings", "tent")[0]["rally"] == [30, 30]
    assert len(own(barracks_base, "buildings", "powr")) == 1


def test_repair_and_power_toggle_that_change_nothing_are_answered_ok_at_once(barracks_base):
    (plant,) = own(barracks_base, "buildings", "powr")
    plant_id = plant["id"]

    unchanged = barracks_base.play_action(
        batch(
            {"cmd": "repair", "building": plant_id, "on": False},
            {"cmd": "power_toggle", "building": plant_id, "on": True},
        )
    )
    changed = barracks_base.play_action(
        batch({"cmd": "repair", "building": plant_id}, {"cmd": "power_toggle", "building": plant_id})
    )
    barracks_base.step()
    switched_off = own(barracks_base, "buildings", "powr")[0]
    again = barracks_base.play_action(
        batch(
            {"cmd": "repair", "building": plant_id, "on": True},
            {"cmd": "power_toggle", "building": plant_id, "on": False},
        )
    )

    assert [(row["status"], row["detail"], row["commands"]) for row in unchanged["results"]] == [
        ("ok", "not_repairing", 0),
        ("ok", "already_on", 0),
    ]
    assert (unchanged["accepted"], unchanged["accepted_orders"]) == (True, 2)
    assert rows(changed) == [("repair", "pending", "pending"), ("power_toggle", "pending", "pending")]
    assert (switched_off["repairing"], switched_off["powered_down"]) == (True, True)
    assert [(row["status"], row["detail"]) for row in again["results"]] == [
        ("ok", "already_repairing"),
        ("ok", "already_off"),
    ]


def test_an_mcv_named_twice_in_one_batch_is_deployed_once(game):
    answer = game.play_action(batch({"cmd": "deploy", "units": [MCV_ID]}, {"cmd": "deploy", "units": [MCV_ID]}))
    game.step()

    assert rows(answer) == [("deploy", "pending", "pending")] * 2
    assert [building["type"] for building in observe(game)["buildings"]] == ["fact"]


# -- scenarios: enemy actors and what the player sees of them --------------------------------------


def cells_apart(one: dict, other: dict) -> float:
    return math.hypot(one["x"] - other["x"], one["y"] - other["y"])


def test_an_enemy_20_cells_away_is_hidden_until_an_own_unit_comes_within_8_cells(barracks_base):
    (rifle,) = train(barracks_base, "e1")
    enemy_id = barracks_base.place_enemy("e1", rifle["x"] + 30, rifle["y"])
    (enemy,) = barracks_base.enemies()
    own_actors = observe(barracks_base)["units"] + observe(barracks_base)["buildings"]
    assert min(cells_apart(actor, enemy) for actor in own_actors) >= 20
    assert observe(barracks_base)["visible_enemies"] == [] and enemy["visible"] is False

    barracks_base.play_action(batch({"cmd": "move", "units": [rifle["id"]], "to": [enemy["x"], enemy["y"]]}))
    seen_at = None
    for step in range(1, 200):
        barracks_base.step()
        state = barracks_base.state()
        (walker,) = [unit for unit in state["observation"]["units"] if unit["id"] == rifle["id"]]
        in_sight = cells_apart(walker, enemy) <= 8
        assert bool(state["observation"]["visible_enemies"]) is in_sight
        if in_sight:
            seen_at = step
            break

    # A unit is shown on the cell nearest to it, so it is 8 cells from the enemy once it has walked 21.5 of
    # the 30: at 1.32 cells a second, 0.264 a step, that is after 82 steps.
    assert seen_at == 82
    assert state["observation"]["visible_enemies"] == [
        {"id": enemy_id, "type": "e1", "x": enemy["x"], "y": enemy["y"], "hp": 100}
    ]
    assert state["observation"]["events"][-1] == {
        "seq": state["observation"]["events"][-1]["seq"],
        "tick": state["observation"]["tick"],
        "kind": "enemy_spotted",
        "id": enemy_id,
        "type": "e1",
        "x": enemy["x"],
        "y": enemy["y"],
    }
    assert state["legal_actions"]["attack"] == [enemy_id]


def test_an_enemy_is_seen_from_exactly_8_cells_by_a_unit_or_a_building(base):
    at_eight = base.place_enemy("e1", 30, 48)
    beyond = base.place_enemy("e1", 31, 48)
    diagonal = base.place_enemy("e1", 27, 54)

    visible = {enemy["id"] for enemy in observe(base)["visible_enemies"]}

    # The construction yard stands on (22, 48); (27, 54) is 7.8 cells from it.
    assert visible == {at_eight, diagonal} and beyond not in visible


def test_a_scenario_can_force_an_enemy_visible_or_hidden(base):
    far = base.place_enemy("e1", 80, 80, visible=True)
    near = base.place_enemy("e1", 24, 48, visible=False)
    assert [enemy["id"] for enemy in observe(base)["visible_enemies"]] == [far]

    base.show_enemy(far, None)
    base.show_enemy(near, None)

    assert [enemy["id"] for enemy in observe(base)["visible_enemies"]] == [near]
    assert {enemy["id"]: enemy["visible"] for enemy in base.enemies()} == {far: False, near: True}


def test_enemy_buildings_and_units_are_listed_apart_and_can_be_moved_damaged_and_removed(base):
    pillbox = base.place_enemy("pbox", 26, 48)
    tank = base.place_enemy("2tnk", 26, 50, hp=80)
    observation = observe(base)
    assert observation["visible_enemy_buildings"] == [{"id": pillbox, "type": "pbox", "x": 26, "y": 48, "hp": 100}]
    assert observation["visible_enemies"] == [{"id": tank, "type": "2tnk", "x": 26, "y": 50, "hp": 80}]
    assert base.state()["legal_actions"]["attack"] == [tank, pillbox]

    base.move_enemy(tank, 20, 44)
    base.damage_enemy(tank, 35)
    base.remove_enemy(pillbox)
    observation = observe(base)

    assert observation["visible_enemies"] == [{"id": tank, "type": "2tnk", "x": 20, "y": 44, "hp": 35}]
    assert observation["visible_enemy_buildings"] == []
    assert base.enemies() == [
        {"id": tank, "type": "2tnk", "x": 20, "y": 44, "hp": 35, "building": False, "visible": True}
    ]


def test_a_wreck_is_marked_never_offered_as_a_target_and_cannot_be_attacked(barracks_base):
    (rifle,) = train(barracks_base, "e1")
    wreck = barracks_base.place_enemy("2tnk.husk", 24, 50)
    tank = barracks_base.place_enemy("2tnk", 24, 52)
    barracks_base.step()
    state = barracks_base.state()

    answer = barracks_base.play_action(
        batch(
            {"cmd": "attack", "units": [rifle["id"]], "target": wreck},
            {"cmd": "attack", "units": [rifle["id"]], "target": tank},
        )
    )

    assert {"id": wreck, "type": "2tnk.husk", "x": 24, "y": 50, "hp": 100, "wreck": True} in state["observation"][
        "visible_enemies"
    ]
    assert state["legal_actions"]["attack"] == [tank]
    assert [event["id"] for event in state["observation"]["events"] if event["kind"] == "enemy_spotted"] == [tank]
    assert rows(answer) == [("attack", "rejected", "wreck"), ("attack", "pending", "pending")]
    assert answer["results"][0]["detail"] == f"{wreck} is a wreck"


def test_a_unit_ordered_to_attack_walks_after_its_target_and_idles_when_the_target_is_gone(barracks_base):
    (rifle,) = train(barracks_base, "e1")
    target = barracks_base.place_enemy("harv", rifle["x"] + 4, rifle["y"])

    barracks_base.play_action(batch({"cmd": "attack", "units": [rifle["id"]], "target": target}))
    advance(barracks_base, 20)
    (on_target,) = own(barracks_base, "units", "e1")
    barracks_base.move_enemy(target, rifle["x"] + 4, rifle["y"] + 3)
    advance(barracks_base, 15)
    (followed,) = own(barracks_base, "units", "e1")
    barracks_base.remove_enemy(target)
    barracks_base.step()
    (done,) = own(barracks_base, "units", "e1")

    assert (on_target["x"], on_target["y"], on_target["act"], on_target["idle"]) == (
        rifle["x"] + 4,
        rifle["y"],
        "attack",
        False,
    )
    assert (followed["x"], followed["y"], followed["act"]) == (rifle["x"] + 4, rifle["y"] + 3, "attack")
    assert (done["act"], done["idle"]) == (None, True)


def test_the_ready_made_attack_goes_for_the_nearest_enemy_in_sight(barracks_base):
    rifles = train(barracks_base, "e1", 2)
    barracks_base.place_enemy("e1", 60, 60, visible=True)
    near = barracks_base.place_enemy("pbox", rifles[0]["x"] + 5, rifles[0]["y"])

    attack_now = barracks_base.state()["legal_actions"]["attack_now"]

    assert attack_now == {
        "cmd": "attack_move",
        "units": [rifle["id"] for rifle in rifles],
        "to": [rifles[0]["x"] + 5, rifles[0]["y"]],
    }
    assert near in barracks_base.state()["legal_actions"]["attack"]


# -- scenarios: the player's own losses ------------------------------------------------------------


def test_killing_an_own_unit_removes_it_adds_its_cost_to_deaths_cost_and_reports_the_loss(barracks_base):
    first, second = train(barracks_base, "e3", 2)
    army_before = observe(barracks_base)["military"]["army_value"]

    barracks_base.kill_own(first["id"])
    observation = observe(barracks_base)

    assert [unit["id"] for unit in observation["units"]] == [second["id"]]
    assert (observation["military"]["deaths_cost"], observation["military"]["units_lost"]) == (300, 1)
    assert (army_before, observation["military"]["army_value"]) == (600, 300)
    assert observation["events"][-1] == {
        "seq": observation["events"][-1]["seq"],
        "tick": observation["tick"],
        "kind": "unit_lost",
        "id": first["id"],
        "type": "e3",
        "x": first["x"],
        "y": first["y"],
    }


def test_killing_an_own_building_reports_a_building_lost_and_takes_its_power_away(barracks_base):
    (plant,) = own(barracks_base, "buildings", "powr")

    barracks_base.kill_own(plant["id"])
    barracks_base.step()
    observation = observe(barracks_base)

    assert (observation["military"]["deaths_cost"], observation["military"]["buildings_lost"]) == (300, 1)
    assert (observation["economy"]["power_provided"], observation["economy"]["power_drained"]) == (0, 20)
    assert [event["kind"] for event in observation["events"]][-2:] == ["building_lost", "low_power"]
    assert "tent" not in observation["available_production"]


def test_an_own_actor_that_does_not_exist_cannot_be_killed(base):
    with pytest.raises(KeyError):
        base.kill_own(4242)


def test_damaged_buildings_are_offered_for_repair_lowest_first(barracks_base):
    (plant,) = own(barracks_base, "buildings", "powr")
    (barracks,) = own(barracks_base, "buildings", "tent")
    (rifle,) = train(barracks_base, "e1")

    barracks_base.damage_own(plant["id"], 70)
    barracks_base.damage_own(barracks["id"], 40)
    barracks_base.damage_own(rifle["id"], 55)
    assert barracks_base.state()["legal_actions"]["repair"] == [barracks["id"], plant["id"]]
    barracks_base.play_action(batch({"cmd": "repair", "building": barracks["id"], "on": True}))
    barracks_base.step()

    assert barracks_base.state()["legal_actions"]["repair"] == [plant["id"]]
    assert own(barracks_base, "buildings", "tent")[0]["hp"] == 40 and own(barracks_base, "units", "e1")[0]["hp"] == 55


def test_with_no_actor_left_there_is_no_base_and_a_finished_building_cannot_be_placed(base):
    (yard,) = observe(base)["buildings"]
    base.play_action(batch({"cmd": "build", "item": "powr"}))
    advance(base, 36)

    base.kill_own(yard["id"])
    answer = base.play_action(batch({"cmd": "place", "item": "powr"}))
    base.step()
    state = base.state()

    assert rows(answer) == [("place", "pending", "pending")]
    assert rows(state["last_orders"]) == [("place", "rejected", "invalid")]
    assert state["observation"]["base_center"] is None and state["observation"]["buildings"] == []


# -- scenarios: events and the kill figure ---------------------------------------------------------


def test_a_scenario_emits_under_attack_low_power_and_enemy_killed_events_and_adds_kills(base):
    (yard,) = observe(base)["buildings"]
    base.damage_own(yard["id"], 90)

    base.emit_under_attack(yard["id"])
    base.emit_event("low_power", power_provided=0, power_drained=20)
    base.emit_event("enemy_killed", count=2)
    base.add_kills(1100)
    base.add_kills(100)
    observation = observe(base)

    assert [
        {key: event[key] for key in event if key not in ("seq", "tick")} for event in observation["events"][-3:]
    ] == [
        {"kind": "under_attack", "id": yard["id"], "type": "fact", "x": 22, "y": 48, "hp": 90},
        {"kind": "low_power", "power_provided": 0, "power_drained": 20},
        {"kind": "enemy_killed", "count": 2},
    ]
    assert [event["seq"] for event in observation["events"]] == [1, 2, 3, 4]
    assert observation["military"]["kills_cost"] == 1200


def test_skipped_event_numbers_leave_a_gap_in_seq(base):
    base.emit_event("enemy_killed", count=1)
    base.skip_event_seq(3)
    base.emit_event("enemy_killed", count=1)

    assert [event["seq"] for event in observe(base)["events"]] == [1, 2, 6]


def test_the_event_window_holds_the_last_20_events(base):
    for _ in range(25):
        base.emit_event("enemy_killed", count=1)

    assert [event["seq"] for event in observe(base)["events"]] == list(range(7, 27))


# -- scenarios: forced verdicts --------------------------------------------------------------------


def test_a_lost_batch_is_answered_as_usual_and_its_orders_are_lost_in_the_next_state(base):
    base.lose_batch()

    answer = base.play_action(batch({"cmd": "build", "item": "powr"}, {"cmd": "train", "item": "3tnk"}))
    base.step()
    state = base.state()

    assert rows(answer) == [("build", "pending", "pending"), ("train", "rejected", "not_available")]
    assert rows(state["last_orders"]) == [("build", "lost", "not_applied"), ("train", "rejected", "not_available")]
    assert state["last_orders"]["pending"] is False
    assert state["observation"]["production"] == [] and state["observation"]["economy"]["cash"] == 5000
    assert [(problem["cmd"], problem["status"]) for problem in state["recent_order_problems"]] == [
        ("build", "lost"),
        ("train", "rejected"),
    ]
    assert len(base.recorded_orders) == 1

    base.play_action(batch({"cmd": "build", "item": "powr"}))
    base.step()

    assert rows(base.state()["last_orders"]) == [("build", "ok", "ok")]


def test_a_batch_can_be_lost_by_its_number(base):
    base.lose_batch(3)

    for _ in range(3):
        base.play_action(batch({"cmd": "build", "item": "powr"}))
        base.step()

    assert (base.state()["last_orders"]["batch"], rows(base.state()["last_orders"])) == (4, [("build", "ok", "ok")])
    assert [problem["batch"] for problem in base.state()["recent_order_problems"]] == [3]
    assert len(observe(base)["production"]) == 2


def test_a_full_queue_rejects_the_next_production_order_not_ready_in_the_next_state(barracks_base):
    barracks_base.refuse_production("Infantry")

    answer = barracks_base.play_action(
        batch({"cmd": "train", "item": "e1", "count": 3}, {"cmd": "build", "item": "pbox"})
    )
    barracks_base.step()
    state = barracks_base.state()
    barracks_base.play_action(batch({"cmd": "train", "item": "e1"}))
    barracks_base.step()

    assert rows(answer) == [("train", "pending", "pending"), ("build", "pending", "pending")]
    assert rows(state["last_orders"]) == [("train", "rejected", "not_ready"), ("build", "ok", "ok")]
    assert [job["item"] for job in state["observation"]["production"]] == ["pbox"]
    assert rows(barracks_base.state()["last_orders"]) == [("train", "ok", "ok")]


def test_a_refused_placement_is_rejected_invalid_and_the_item_stays_ready(base):
    base.play_action(batch({"cmd": "build", "item": "powr"}))
    advance(base, 36)
    base.refuse_place("powr", times=2)

    verdicts = []
    for _ in range(3):
        base.play_action(batch({"cmd": "place", "item": "powr", "at": [26, 48]}))
        base.step()
        verdicts.append((rows(base.state()["last_orders"]), base.state()["legal_actions"]["place"]))

    assert verdicts == [
        ([("place", "rejected", "invalid")], ["powr"]),
        ([("place", "rejected", "invalid")], ["powr"]),
        ([("place", "ok", "ok")], []),
    ]
    assert [(building["x"], building["y"]) for building in own(base, "buildings", "powr")] == [(26, 48)]


def test_an_item_taken_out_of_available_production_cannot_be_ordered(barracks_base):
    assert "e3" in observe(barracks_base)["available_production"]

    barracks_base.remove_available("E3")
    answer = barracks_base.play_action(batch({"cmd": "train", "item": "e3"}, {"cmd": "train", "item": "e1"}))

    assert "e3" not in observe(barracks_base)["available_production"]
    assert "e3" not in barracks_base.state()["legal_actions"]["train"]
    assert rows(answer) == [("train", "rejected", "not_available"), ("train", "pending", "pending")]


# -- scenarios: timed steps, callbacks and the end of the match ------------------------------------


def test_scenario_steps_are_applied_in_the_step_they_name(game):
    seen = []
    scenario = [
        fake_game.at(0, lambda fake: seen.append(("start", fake.tick))),
        fake_game.at(3, lambda fake: seen.append(("third step", fake.tick))),
        fake_game.at(3, lambda fake: seen.append(("third step again", fake.tick))),
        fake_game.at_seconds(1, lambda fake: seen.append(("one second", fake.tick))),
        fake_game.at_seconds(0.4, lambda fake: fake.place_enemy("e1", 24, 48)),
    ]
    scripted = FakeRedAlert("england", "russia", scenario=scenario)
    assert seen == [("start", 0)]

    advance(scripted, 1)
    hidden = observe(scripted)["visible_enemies"]
    advance(scripted, 5)

    assert seen == [("start", 0), ("third step", 15), ("third step again", 15), ("one second", 25)]
    assert hidden == [] and len(observe(scripted)["visible_enemies"]) == 1
    assert [(event["kind"], event["tick"]) for event in observe(scripted)["events"]] == [("enemy_spotted", 10)]


def test_an_enemy_placed_in_sight_at_the_start_is_spotted_in_the_first_state():
    scripted = FakeRedAlert(
        "england", "russia", scenario=[fake_game.at(0, lambda fake: fake.place_enemy("e1", 25, 48))]
    )

    (event,) = scripted.state()["observation"]["events"]

    assert (event["kind"], event["tick"], event["seq"]) == ("enemy_spotted", 0, 1)


def test_a_callback_runs_every_step_and_can_react_to_where_the_own_units_are(barracks_base):
    (rifle,) = train(barracks_base, "e1")
    harvester = barracks_base.place_enemy("harv", rifle["x"] + 12, rifle["y"])
    calls = []

    def raid(fake: FakeRedAlert) -> None:
        calls.append(fake.tick)
        target = next((enemy for enemy in fake.enemies() if enemy["id"] == harvester), None)
        if target and any(cells_apart(unit, target) <= 5 for unit in fake.own_units()):
            fake.remove_enemy(harvester)
            fake.emit_event("enemy_killed", count=1)
            fake.add_kills(1100)

    barracks_base.on_step(raid)
    barracks_base.play_action(batch({"cmd": "move", "units": [rifle["id"]], "to": [rifle["x"] + 12, rifle["y"]]}))
    started = barracks_base.tick
    advance(barracks_base, 30)
    observation = observe(barracks_base)

    assert calls == list(range(started + 5, started + 155, 5))
    assert barracks_base.enemies() == [] and observation["military"]["kills_cost"] == 1100
    # At 0.264 cells a step the rifleman is shown 8 cells from the harvester after 14 steps (3.5 cells walked)
    # and 5 cells from it after 25 (6.5 cells walked).
    assert [(event["kind"], event["tick"]) for event in observation["events"][-2:]] == [
        ("enemy_spotted", started + 14 * 5),
        ("enemy_killed", started + 25 * 5),
    ]


def test_a_scenario_reads_the_player_s_units_and_buildings_as_the_observation_lists_them(barracks_base):
    train(barracks_base, "e1", 2)
    observation = observe(barracks_base)

    assert barracks_base.own_units() == observation["units"]
    assert barracks_base.own_buildings() == observation["buildings"]


@pytest.mark.parametrize(
    "own_return, outcome, winner_seat, winner_agent",
    [
        (1.0, "win", "Multi0", fake_game.AGENT_ID),
        (0.0, "loss", "Multi1", fake_game.OPPONENT_ID),
        (0.5, "draw", None, None),
    ],
    ids=["win", "loss", "draw"],
)
def test_a_scenario_ends_the_match_with_a_result_object(base, own_return, outcome, winner_seat, winner_agent):
    result = fake_game.result_object(own_return, "time_limit")
    scripted = FakeRedAlert("england", "russia", scenario=[fake_game.at(4, lambda fake: fake.end_match(result))])
    advance(scripted, 3)
    running = scripted.state()

    advance(scripted, 1)
    ended = scripted.state()
    advance(scripted, 5)

    assert (running["is_terminal"], running["phase"], "result" in running) == (False, "playing", False)
    assert (ended["is_terminal"], ended["phase"], ended["status"], ended["is_current_actor"]) == (
        True,
        "finished",
        "completed",
        False,
    )
    assert ended["result"] == {
        "outcome": outcome,
        "reason": "time_limit",
        "winner_seat": winner_seat,
        "returns": {fake_game.AGENT_ID: own_return, fake_game.OPPONENT_ID: 1.0 - own_return},
    }
    assert (result["winner_agent_id"], result["your_return"], result["is_terminal"]) == (winner_agent, own_return, True)
    assert scripted.state() == ended and ended["state_version"] == 20


def test_a_finished_match_offers_nothing_to_do_and_takes_no_orders(base):
    base.end_match(fake_game.result_object(1.0, "victory"))
    legal = base.state()["legal_actions"]

    answer = base.play_action(batch({"cmd": "build", "item": "powr"}))

    assert {name: legal[name] for name in fake_game.LEGAL_ACTION_LISTS} == {
        name: [] for name in fake_game.LEGAL_ACTION_LISTS
    }
    assert (legal["attack_now"], legal["enemy_base"]) == (None, [74, 48])
    assert answer == {"error": "GAME_ALREADY_COMPLETE", "detail": "The match is over"}
    assert base.invalid_actions == 0 and observe(base)["production"] == []


def test_a_result_without_a_return_for_the_player_is_no_result(base):
    base.end_match({"is_terminal": True, "status": "completed", "returns": {}, "termination_reason": "engine_crashed"})

    assert base.state()["result"] == {
        "outcome": "no_result",
        "reason": "engine_crashed",
        "winner_seat": None,
        "returns": {},
    }


# -- a whole match under `run` ---------------------------------------------------------------------

OPENING_BUILD_LIST = ("powr", "tent", "proc")


class BuildListPlayer:
    """Deploys, builds and places a fixed list of buildings and trains rifle infantry, from the legal-action lists."""

    def __init__(self, reasoning: str | None = None) -> None:
        self.to_build = list(OPENING_BUILD_LIST)
        self.reasoning = reasoning
        self.seen: list[tuple] = []
        self.answers: list[tuple] = []

    def choose_action(self, state: GameState, context: DecisionContext):
        self.seen.append((state.state_version, state.is_terminal, len(state.legal_actions), context))
        orders = self._orders(state.raw["legal_actions"], state.raw["observation"]["production"])
        if not orders:
            return WAIT
        action = batch(*orders)
        return WithReasoning(action, self.reasoning) if self.reasoning else action

    def _orders(self, legal: dict, production: list[dict]) -> list[dict]:
        orders = [{"cmd": "deploy", "units": legal["deploy"]}] if legal["deploy"] else []
        orders += [{"cmd": "place", "item": item} for item in legal["place"]]
        if self._wants_to_build(legal, production):
            orders.append({"cmd": "build", "item": self.to_build.pop(0)})
        if "e1" in legal["train"] and not _queued(production, "Infantry"):
            orders.append({"cmd": "train", "item": "e1", "count": 2})
        return orders

    def _wants_to_build(self, legal: dict, production: list[dict]) -> bool:
        return bool(self.to_build) and self.to_build[0] in legal["build"] and not _queued(production, "Building")

    def on_action_result(self, result: dict, context: DecisionContext) -> None:
        self.answers.append((result, context))


def _queued(production: list[dict], queue: str) -> bool:
    return any(job["queue"] == queue for job in production)


def test_run_asks_for_a_move_on_every_state_and_reports_each_answer(game):
    player = BuildListPlayer()

    played = game.run(player, 300)

    assert [version for version, *_ in player.seen] == list(range(0, 1500, 5))
    assert {(terminal, actions) for _, terminal, actions, _ in player.seen} == {(False, 0)}
    assert all(context == game.context() for *_, context in player.seen)
    assert played.batches[0] == batch({"cmd": "deploy", "units": [MCV_ID]})
    assert [answer for answer, _ in player.answers] == played.answers
    assert all(context == game.context() for _, context in player.answers)
    assert all(answer["accepted"] for answer in played.answers) and played.invalid_actions == 0
    assert played.waits == 300 - len(played.batches) and len(played.batches) == len(played.answers) > 10
    assert [building["type"] for building in observe(game)["buildings"]] == ["fact", "powr", "tent", "proc"]
    assert len(own(game, "units", "e1")) >= 10 and game.tick == 1500


def test_run_hands_the_decision_maker_the_state_as_the_runtime_wraps_it(game):
    wrapped = []

    class Watcher:
        def choose_action(self, state: GameState, _context: DecisionContext):
            wrapped.append(state)
            return WAIT

    first_state = game.state()
    game.run(Watcher(), 2)

    assert isinstance(wrapped[0], GameState)
    assert wrapped[0].raw == first_state and wrapped[0].legal_actions == []
    assert (wrapped[0].state_version, wrapped[0].phase, wrapped[0].is_current_actor) == (0, "playing", True)
    assert (wrapped[1].state_version, wrapped[1].raw["observation"]["tick"]) == (5, 5)


def test_the_default_context_is_a_red_alert_context_with_the_fake_s_game_config(game):
    context = game.context()

    assert (context.session_id, context.game_type, context.agent_id) == (
        fake_game.SESSION_ID,
        "red_alert",
        fake_game.AGENT_ID,
    )
    assert (context.tournament_id, context.seat_position) == (None, 0)
    assert context.game_config is game.game_config


def test_run_passes_on_the_context_it_is_given(game):
    player = BuildListPlayer()
    context = DecisionContext(session_id="another", tournament_id="cup", game_type="red_alert", agent_id="agent-9")

    game.run(player, 3, context)

    assert all(seen_context is context for *_, seen_context in player.seen)
    assert all(answer_context is context for _, answer_context in player.answers) and len(player.answers) == 2


def test_a_batch_wrapped_with_reasoning_is_sent_as_the_batch(game):
    played = game.run(BuildListPlayer(reasoning="Deploy the MCV."), 1)

    assert played.batches == [batch({"cmd": "deploy", "units": [MCV_ID]})]
    assert [building["type"] for building in observe(game)["buildings"]] == ["fact"]


def test_run_works_for_a_decision_maker_without_a_result_hook(game):
    class Deployer:
        def choose_action(self, state: GameState, _context: DecisionContext):
            deploy = state.raw["legal_actions"]["deploy"]
            return batch({"cmd": "deploy", "units": deploy}) if deploy else WAIT

    played = game.run(Deployer(), 4)

    assert (len(played.batches), played.waits) == (1, 3)
    assert [building["type"] for building in observe(game)["buildings"]] == ["fact"]


def test_a_refused_batch_is_reported_to_the_decision_maker_and_counted_for_the_run(base):
    class Flooder:
        answers: list[dict] = []

        def choose_action(self, _state: GameState, _context: DecisionContext):
            return batch(*[{"cmd": "build", "item": "powr"}] * 21)

        def on_action_result(self, result: dict, _context: DecisionContext) -> None:
            self.answers.append(result)

    base.play_action(batch(*[{"cmd": "build", "item": "powr"}] * 21))
    played = base.run(Flooder(), 3)

    assert Flooder.answers == [{"error": "INVALID_ACTION", "detail": "a batch takes 1-20 orders, got 21"}] * 3
    assert (played.invalid_actions, base.invalid_actions, played.waits) == (3, 4, 0)


def test_run_keeps_every_state_when_asked(game):
    kept = FakeRedAlert("england", "russia").run(BuildListPlayer(), 40, keep_states=True)
    not_kept = game.run(BuildListPlayer(), 40)

    assert [state["state_version"] for state in kept.states] == list(range(0, 205, 5))
    assert kept.states[-1] == game.state() and kept.states[0]["observation"]["units"][0]["type"] == "mcv"
    assert not_kept.states == [] and not_kept.batches == kept.batches


def test_run_ends_through_the_runtime_s_final_state_function_and_never_through_the_agent(game, monkeypatch):
    result = fake_game.result_object(1.0, "time_limit")
    scripted = FakeRedAlert("england", "russia", scenario=[fake_game.at(6, lambda fake: fake.end_match(result))])
    original, calls = runner._terminal_game_state, []

    def spy(last_state: GameState, given_result: dict) -> GameState:
        calls.append((last_state, given_result))
        return original(last_state, given_result)

    monkeypatch.setattr(runner, "_terminal_game_state", spy)
    player = BuildListPlayer()

    played = scripted.run(player, 50, keep_states=True)

    ((last_state, given_result),) = calls
    assert given_result is result and last_state.raw == scripted.state()
    assert (last_state.is_terminal, last_state.phase, last_state.state_version) == (True, "finished", 30)
    assert [version for version, *_ in player.seen] == [0, 5, 10, 15, 20, 25]
    assert not any(terminal for _, terminal, *_ in player.seen)
    assert played.final_state.is_terminal and played.final_state.termination_reason == "time_limit"
    assert played.final_state.returns == {fake_game.AGENT_ID: 1.0, fake_game.OPPONENT_ID: 0.0}
    assert len(played.states) == 7 and played.states[-1]["is_terminal"] is True


def test_a_run_that_runs_out_of_steps_ends_with_the_result_of_a_match_still_in_progress(game, monkeypatch):
    given = []
    original = runner._terminal_game_state
    monkeypatch.setattr(
        runner, "_terminal_game_state", lambda state, result: given.append(result) or original(state, result)
    )

    played = game.run(BuildListPlayer(), 5)

    assert given == [game.result()]
    assert given[0] == {
        "session_id": fake_game.SESSION_ID,
        "is_terminal": False,
        "status": "in_progress",
        "returns": None,
        "your_return": None,
        "winner_agent_id": None,
        "termination_reason": None,
        "failure_reason": None,
    }
    assert (played.final_state.is_terminal, played.final_state.returns, played.final_state.state_version) == (
        False,
        None,
        25,
    )


def test_a_run_of_the_dispatcher_writes_a_match_record_that_ends_with_the_result(run_folder, read_jsonl):
    result = fake_game.result_object(1.0, "time_limit")
    scripted = FakeRedAlert("england", "russia", scenario=[fake_game.at(3, lambda fake: fake.end_match(result))])

    played = scripted.run(create_agent(), 10, scripted.context())

    (events_path,) = run_folder.glob(f"runs/*/{fake_game.SESSION_ID}__{fake_game.OWN_SEAT}/events.jsonl")
    lines = read_jsonl(events_path)
    assert [line["kind"] for line in lines[-2:]] == ["ledger_summary", "result"]
    assert lines[-1]["data"]["result"] == result
    assert (played.waits + len(played.batches), played.final_state.termination_reason) == (3, "time_limit")


def test_two_runs_of_the_same_scenario_and_seed_give_the_same_batches_and_final_state():
    def play(seed: int) -> tuple[fake_game.FakeRun, dict]:
        scenario = [
            fake_game.at(60, lambda fake: fake.place_enemy("e1", 30, 48)),
            fake_game.at(120, lambda fake: fake.lose_batch()),
            fake_game.at_seconds(40, lambda fake: fake.refuse_production("Infantry")),
            fake_game.at_seconds(70, lambda fake: fake.end_match(fake_game.result_object(0.5, "time_limit"))),
        ]
        scripted = FakeRedAlert("russia", "england", scenario=scenario, seed=seed)
        player = BuildListPlayer()
        player.to_build = ["powr", "barr", "proc"]
        return scripted.run(player, 400, keep_states=True), scripted.state()

    first, first_final = play(7)
    second, second_final = play(7)
    other_seed, _ = play(8)

    assert first.batches == second.batches and first.answers == second.answers
    assert first_final == second_final and first.states == second.states
    assert first.final_state == second.final_state
    assert len(first.states) == 351 and first_final["is_terminal"] is True
    assert other_seed.states != first.states and len(first.batches) > 10


# -- the long replay -------------------------------------------------------------------------------

LONG_MATCH_MIN_STATES = 3000
LONG_MATCH_MIN_PEAK_UNITS = 150
LONG_MATCH_MAX_SECONDS = 20


def test_the_long_match_has_3000_states_a_peak_of_150_own_units_and_takes_under_20_s_to_generate(
    long_match_states, long_match_generation_seconds
):
    peak = max(len(state["observation"]["units"]) for state in long_match_states)

    assert len(long_match_states) >= LONG_MATCH_MIN_STATES
    assert peak >= LONG_MATCH_MIN_PEAK_UNITS
    assert long_match_generation_seconds < LONG_MATCH_MAX_SECONDS


# -- a replay fixture from a match record ----------------------------------------------------------

# Put together at run time, so that this file holds no literal the tracked-file scan would report.
RECORDED_KEY = "eak_" + "live_" + "fakegamerecord0123456789"
RECORDED_STATES = 6


def test_record_to_fixture_writes_a_gzip_replay_without_the_key_the_record_holds(game, run_folder, run_script):
    folder = run_folder / "runs" / "20261007" / f"{fake_game.SESSION_ID}__{fake_game.OWN_SEAT}"
    folder.mkdir(parents=True)
    lines = []
    for seq in range(RECORDED_STATES):
        state = game.state()
        state["players"][0]["name"] = f"agent signed in with {RECORDED_KEY}"
        data = {"state_version": state["state_version"], "phase": state["phase"], "state": state}
        lines.append({"seq": seq, "t": "2026-10-07T12:00:00.000Z", "mono_ms": seq * 200, "kind": "state", "data": data})
        game.step()
    (folder / "events.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")

    completed = run_script(run_folder, "record_to_fixture.py", str(folder), "sample")

    replay_path = run_folder / "tests" / "fixtures" / "redalert" / "replays" / "sample.jsonl.gz"
    text = gzip.decompress(replay_path.read_bytes()).decode("utf-8")
    replay = [json.loads(line) for line in text.splitlines()]
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith(f"Wrote {replay_path}: 6 of 6 full states, every state kept (N = 1)")
    assert RECORDED_KEY in (folder / "events.jsonl").read_text(encoding="utf-8")
    assert RECORDED_KEY not in text and RECORDED_KEY.encode() not in replay_path.read_bytes()
    assert [state["state_version"] for state in replay] == [0, 5, 10, 15, 20, 25]
    assert {state["players"][0]["name"] for state in replay} == {"agent signed in with [REDACTED]"}


# -- where the fake game may be used ---------------------------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def test_no_module_under_src_imports_the_fake_game():
    importers = [
        path.relative_to(SOURCE_ROOT).as_posix()
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if any(name.split(".")[-1] == "fake_game" for name in _imported_modules(path))
    ]

    assert importers == []


def test_the_fake_game_says_it_is_no_evidence_that_the_agent_wins():
    assert "implements the documented state and order contract" in fake_game.__doc__
    assert "not\nevidence that the agent wins" in fake_game.__doc__
    assert "simulates no combat and no opponent" in " ".join(fake_game.__doc__.split())
