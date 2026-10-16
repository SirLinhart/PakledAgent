"""The Red Alert control loop: every fresh state answered with one legal batch or WAIT."""

from __future__ import annotations

import builtins
import dataclasses
import gc
import itertools
import os
import socket
import threading
import time
import tokenize
import urllib.request
from pathlib import Path

import httpx
import pytest

import situations
from altruagent import WAIT, WithReasoning, runner
from fake_game import MAP_ID, FakeRedAlert
from situations import ScriptedManager, StubChooser, ask, fixture_state, new_agent, own_agent
from switches_off import settings_models_off
from ucla_agent.core import paths, records, stats
from ucla_agent.core.decide import TIMEOUT, AsyncChooser, ChoiceRequest, DecisionSource, Option, build_chain
from ucla_agent.core.decide.journal import SourceCall
from ucla_agent.games.redalert import agent as agent_module
from ucla_agent.games.redalert.agent import RedAlertAgent
from ucla_agent.games.redalert.batch import Intent, Priority, command_count
from ucla_agent.games.redalert.composition import HOLD, OPENING
from ucla_agent.games.redalert.items import Item, ItemTable
from ucla_agent.games.redalert.opening import OpeningManager
from ucla_agent.games.redalert.tuning import LoopTuning, Tuning
from ucla_agent.games.redalert.world import World

REPO_ROOT = Path(__file__).resolve().parents[2]
# The rifle infantry of the Allied mid-game fixture, and the cell its attacks go to.
RIFLES = [109, 110, 111, 112, 113, 114]
OWN_UNITS = [105, *RIFLES, 115, 116, 117, 118, 119, 120]
GOAL = [60, 48]


def proposing(name: str, *orders: dict, priority: Priority = Priority.MOVE) -> ScriptedManager:
    """A manager that proposes the same orders in every cycle."""
    return ScriptedManager(name, lambda world, ctx: [Intent(priority, order) for order in orders])


def march(units: list[int], to: list[int] = GOAL) -> dict:
    return {"cmd": "attack_move", "units": units, "to": to}


def single_orders(cmd: str, unit_ids: list[int], **fields) -> list[dict]:
    """One order of a command for each of the units."""
    return [{"cmd": cmd, "units": [unit_id], **fields} for unit_id in unit_ids]


def not_playing(phase: str, **fields) -> dict:
    """A state before or after the game runs, as the platform sends it: no observation."""
    return {"session_id": "s", "game_type": "red_alert", "state_version": 0, "phase": phase, **fields}


# -- when there is nothing to order ----------------------------------------------------------------


@pytest.mark.parametrize("phase", ["waiting_for_agents", "queued", "starting", "finished"])
def test_a_state_whose_phase_is_not_playing_returns_wait(phase):
    agent = new_agent(proposing("combat", march(RIFLES)))

    decision = agent.choose_action(situations.GameState.from_mcp_state(not_playing(phase)), situations.context())

    assert decision is WAIT and agent.ledger.entries == []


def test_a_terminal_state_returns_wait_whatever_its_phase():
    agent = new_agent(proposing("combat", march(RIFLES)))
    state = fixture_state("midgame_allies")
    state["is_terminal"] = True

    assert ask(agent, state) == [] and agent.ledger.entries == []


def test_a_playing_state_without_an_observation_returns_wait():
    manager = proposing("combat", march(RIFLES))
    agent = new_agent(manager)

    assert ask(agent, not_playing("playing")) == []
    assert manager.cycles == 0


def test_with_no_manager_registered_every_state_is_answered_with_wait():
    agent = new_agent()

    decisions = [
        agent.choose_action(
            situations.GameState.from_mcp_state(fixture_state("midgame_allies", tick)), situations.context()
        )
        for tick in (5760, 5765, 5770)
    ]

    assert decisions == [WAIT] * 3 and agent.ledger.orders_sent == 0


def test_managers_that_propose_nothing_give_wait():
    agent = new_agent(ScriptedManager("upkeep"), ScriptedManager("opening"))

    assert ask(agent, fixture_state("midgame_allies")) == []


# -- the agent's own managers ----------------------------------------------------------------------


def test_the_agent_plays_with_its_own_managers_in_the_fixed_order_unless_others_are_given():
    own = ["base_defense", "upkeep", "opening", "scouting", "combat", "raids"]

    assert [manager.name for manager in own_agent().managers] == own
    assert [manager.name for manager in new_agent().managers] == []


def test_a_first_playing_state_with_one_mcv_is_answered_with_a_batch_whose_first_order_deploys_it():
    agent = own_agent()

    decision = agent.choose_action(
        situations.GameState.from_mcp_state(fixture_state("opening_mcv")), situations.context()
    )

    assert decision.action["orders"][0] == {"cmd": "deploy", "units": [100]}
    assert decision.reasoning_summary == "OPENING: deploy 1"


# -- from intents to a batch -----------------------------------------------------------------------


def test_what_the_managers_propose_comes_back_as_one_batch_with_a_reasoning_summary():
    agent = new_agent(
        proposing("combat", march(RIFLES[:2]), {"cmd": "stop", "units": RIFLES[2:3]}),
        proposing("upkeep", {"cmd": "build", "item": "powr"}, priority=Priority.PRODUCTION),
    )

    decision = agent.choose_action(
        situations.GameState.from_mcp_state(fixture_state("midgame_allies")), situations.context()
    )

    assert isinstance(decision, WithReasoning)
    assert decision.action == {
        "type": "orders",
        "orders": [{"cmd": "build", "item": "powr"}, march(RIFLES[:2]), {"cmd": "stop", "units": RIFLES[2:3]}],
    }
    assert decision.reasoning_summary == "HOLD: build 1, attack_move 1, stop 1"


def test_the_reasoning_summary_is_cut_to_the_length_the_tuning_gives():
    tuning = Tuning(loop=LoopTuning(reasoning_summary_chars=12))
    agent = new_agent(proposing("combat", march(RIFLES)), tuning=tuning)

    decision = agent.choose_action(
        situations.GameState.from_mcp_state(fixture_state("midgame_allies")), situations.context()
    )

    assert decision.reasoning_summary == "HOLD: attack"


def one_order_of_each_command() -> list[dict]:
    """Eighteen orders the Allied mid-game fixture allows, no two of the same command and no unit ordered twice."""
    return [
        {"cmd": "set_stance", "units": [109], "stance": "defend"},
        {"cmd": "stop", "units": [110]},
        {"cmd": "unload", "units": [111]},
        {"cmd": "deploy", "units": [112]},
        {"cmd": "harvest", "units": [105]},
        {"cmd": "move", "units": [113], "to": GOAL},
        {"cmd": "guard", "units": [114], "target": 101},
        {"cmd": "attack_move", "units": [115], "to": GOAL},
        {"cmd": "attack", "units": [116], "to": GOAL},
        {"cmd": "enter_transport", "units": [117], "target": 119},
        {"cmd": "repair", "building": 101, "on": True},
        {"cmd": "power_toggle", "building": 102, "on": False},
        {"cmd": "rally", "building": 103, "to": GOAL},
        {"cmd": "set_primary", "building": 103},
        {"cmd": "build", "item": "dome"},
        {"cmd": "train", "item": "e1"},
        {"cmd": "place", "item": "pbox"},
        {"cmd": "cancel", "item": "fix"},
    ]


def test_the_summary_of_a_batch_with_eighteen_different_commands_is_cut_to_200_characters():
    orders = one_order_of_each_command()
    agent = new_agent(proposing("upkeep", *orders, priority=Priority.PRODUCTION))

    decision = agent.choose_action(
        situations.GameState.from_mcp_state(fixture_state("midgame_allies")), situations.context()
    )

    assert decision.action["orders"] == orders
    assert len(decision.reasoning_summary) == 200
    assert decision.reasoning_summary.startswith("HOLD: set_stance 1, stop 1, unload 1, deploy 1, harvest 1, move 1")


def test_the_managers_run_in_the_fixed_order_in_every_cycle():
    ran = []
    managers = [
        ScriptedManager(name, lambda world, ctx, name=name: ran.append(name) or ())
        for name in ("raids", "opening", "combat", "upkeep", "base_defense", "scouting")
    ]
    agent = new_agent(*managers)

    ask(agent, fixture_state("midgame_allies", 5760))
    ask(agent, fixture_state("midgame_allies", 5765))

    assert ran == ["base_defense", "upkeep", "opening", "scouting", "combat", "raids"] * 2


def test_every_manager_gets_the_updated_world_and_the_match_context():
    seen = []
    agent = new_agent(
        ScriptedManager("upkeep", lambda world, ctx: seen.append((world, ctx, world.tick, ctx.plan)) or ())
    )

    ask(agent, fixture_state("midgame_allies", 6000))

    assert seen == [(agent.world, agent.ctx, 6000, HOLD)]
    assert seen[0][1].production.standing("powr") == 2 and seen[0][1].groups.count("guard", "e1") == 5


def test_an_intent_the_world_no_longer_allows_is_discarded_and_counted():
    gone, not_offered = march([999]), {"cmd": "build", "item": "tsla"}
    agent = new_agent(proposing("combat", gone, not_offered, march(RIFLES)))

    assert ask(agent, fixture_state("midgame_allies")) == [march(RIFLES)]
    assert agent.ledger.dropped_invalid == 2


def test_every_order_of_a_batch_is_recorded_in_the_ledger_with_the_state_version_it_goes_out_with():
    agent = new_agent(proposing("combat", march(RIFLES)), proposing("upkeep", {"cmd": "build", "item": "dome"}))

    ask(agent, fixture_state("midgame_allies", 6000))

    recorded = [(entry.batch, entry.index, entry.state_version, entry.order["cmd"]) for entry in agent.ledger.entries]
    assert recorded == [(1, 0, 6000, "build"), (1, 1, 6000, "attack_move")]
    assert [reservation.item for reservation in agent.ledger.reservations] == ["dome"]


def test_what_does_not_fit_into_a_batch_goes_out_in_the_next_cycle_even_when_nothing_new_is_proposed():
    stops, stances = single_orders("stop", OWN_UNITS), single_orders("set_stance", OWN_UNITS[:9], stance="defend")
    # A class the ledger carries over: what does not fit of the combat classes is dropped instead.
    once = iter([[Intent(Priority.SCOUT, order) for order in (*stops, *stances)]])
    agent = new_agent(ScriptedManager("combat", lambda world, ctx: next(once, ())))

    first = ask(agent, fixture_state("midgame_allies", 6000))
    second = ask(agent, fixture_state("midgame_allies", 6005))
    third = ask(agent, fixture_state("midgame_allies", 6010))

    assert first == [*stops, *stances[:7]]
    assert (second, third) == (stances[7:], [])


def test_a_production_order_is_not_sent_twice_while_the_first_is_unresolved():
    agent = new_agent(proposing("upkeep", {"cmd": "build", "item": "dome"}, priority=Priority.PRODUCTION))
    without_queue = {"production": []}

    first = ask(agent, fixture_state("midgame_allies", 6000, **without_queue))
    second = ask(agent, fixture_state("midgame_allies", 6005, **without_queue))

    assert (first, second) == ([{"cmd": "build", "item": "dome"}], [])
    assert agent.ledger.duplicate_production_orders == 0


# -- the platform's limits -------------------------------------------------------------------------


def test_a_state_that_overstates_the_limits_still_gets_a_batch_within_20_orders():
    orders = [*single_orders("stop", OWN_UNITS), *single_orders("set_stance", OWN_UNITS, stance="defend")]
    state = fixture_state("midgame_allies")
    state["limits"] = {"max_orders_per_batch": 100, "max_units_per_order": 500, "max_engine_commands": 5000}
    agent = new_agent(proposing("combat", *orders))

    assert ask(agent, state) == orders[:20] and len(orders) == 26


def test_a_state_that_lowers_a_limit_is_obeyed():
    orders = single_orders("stop", RIFLES)
    state = fixture_state("midgame_allies")
    state["limits"] = {"max_orders_per_batch": 4}
    agent = new_agent(proposing("combat", *orders))

    assert ask(agent, state) == orders[:4]


# -- orders that change nothing --------------------------------------------------------------------


def moving(*unit_ids: int) -> list[dict]:
    """The units of the Allied fixture, those named on the move and the others idle."""
    units = fixture_state("midgame_allies")["observation"]["units"]
    return [{**unit, "idle": unit["id"] not in unit_ids} for unit in units]


def test_an_attack_move_the_units_already_hold_is_not_repeated_in_the_next_state():
    agent = new_agent(proposing("combat", march(RIFLES)))

    first = ask(agent, fixture_state("midgame_allies", 6000))
    second = ask(agent, fixture_state("midgame_allies", 6005, units=moving(*RIFLES)))

    assert first == [march(RIFLES)]
    assert second == [] and agent.ledger.orders_sent == 1


def test_only_the_units_that_stand_idle_are_ordered_again():
    agent = new_agent(proposing("combat", march(RIFLES)))
    ask(agent, fixture_state("midgame_allies", 6000))

    again = ask(agent, fixture_state("midgame_allies", 6005, units=moving(*RIFLES[:4])))

    assert again == [march(RIFLES[4:])]


def test_the_same_order_goes_out_again_once_75_ticks_have_passed():
    agent = new_agent(proposing("combat", march(RIFLES)))
    ask(agent, fixture_state("midgame_allies", 6000))

    early = ask(agent, fixture_state("midgame_allies", 6070, units=moving(*RIFLES)))
    late = ask(agent, fixture_state("midgame_allies", 6075, units=moving(*RIFLES)))

    assert (early, late) == ([], [march(RIFLES)])


def test_an_order_to_another_cell_is_sent_at_once():
    cells = iter([GOAL, [30, 40]])
    agent = new_agent(ScriptedManager("combat", lambda world, ctx: [Intent(Priority.MOVE, march(RIFLES, next(cells)))]))
    ask(agent, fixture_state("midgame_allies", 6000))

    assert ask(agent, fixture_state("midgame_allies", 6005, units=moving(*RIFLES))) == [march(RIFLES, [30, 40])]


# -- the first call of a match ---------------------------------------------------------------------


def test_the_first_call_takes_the_item_facts_and_the_map_from_the_game_reference():
    game_config = FakeRedAlert().game_config
    game_config["rules"]["units"]["e1"]["cost"] = 123
    agent = new_agent()

    ask(agent, fixture_state("opening_mcv"), situations.context(game_config))

    assert agent.world.items["e1"].cost == 123 and agent.world.items["powr"].cost == 300
    assert agent.world.spawns == ((22, 48), (74, 48))
    assert [patch.center for patch in agent.ctx.terrain.ore_patches] == [(22, 40), (74, 40)]


def test_without_a_game_reference_the_agent_plays_by_the_tracked_item_facts_and_knows_no_terrain():
    agent = new_agent()

    ask(agent, fixture_state("opening_mcv"))

    assert agent.world.items["e1"].cost == 100 and agent.ctx.terrain.ore_patches == ()
    assert agent.world.enemy_base == (74, 48)


def test_an_agent_given_no_item_facts_takes_them_all_from_the_game_reference():
    agent = RedAlertAgent(items=ItemTable([]), tuning=Tuning(), chooser=StubChooser(), settings=settings_models_off())

    ask(agent, fixture_state("opening_mcv"), situations.context(FakeRedAlert().game_config))

    assert agent.world.items["weap"] == Item(
        "weap",
        True,
        cost=2000,
        power=-30,
        queue="Building",
        prerequisites=("proc",),
        hp=150000,
        stated_build_time_s=48.0,
    )


def test_the_game_reference_is_logged_once_as_the_first_event(match_record):
    game_config = FakeRedAlert().game_config
    agent = new_agent()

    ask(agent, fixture_state("opening_mcv", 0), situations.context(game_config))
    ask(agent, fixture_state("opening_mcv", 5), situations.context(game_config))

    lines = match_record()
    assert [line["kind"] for line in lines].count("game_config") == 1
    assert lines[0]["kind"] == "game_config" and lines[0]["data"]["maps"] == [MAP_ID]


def test_no_game_config_event_is_logged_when_the_context_carries_no_reference(match_record):
    ask(new_agent(), fixture_state("opening_mcv"))

    assert match_record("game_config") == []


# -- the plan --------------------------------------------------------------------------------------


def test_the_plan_is_opening_until_a_war_factory_stands_and_the_script_s_discretionary_choice_after():
    agent = new_agent()
    before = agent.current_plan

    ask(agent, fixture_state("base_under_attack"))
    without_war_factory = agent.current_plan
    ask(agent, fixture_state("midgame_allies"))

    # A war factory stands, no raid target is known and it is not yet 300 s: only HOLD is open.
    assert (before, without_war_factory, agent.current_plan) == (OPENING, OPENING, HOLD)


def test_the_threat_flag_is_off_unless_a_manager_sets_it():
    agent = new_agent(ScriptedManager("base_defense", lambda world, ctx: setattr(ctx, "threat_active", True) or ()))
    before = agent.threat_active

    ask(agent, fixture_state("midgame_allies"))

    assert (before, agent.threat_active) == (False, True)


def test_the_chooser_is_polled_once_in_every_playing_cycle_with_the_tick_as_the_state_tag():
    chooser = StubChooser()
    agent = new_agent(chooser=chooser)

    ask(agent, not_playing("waiting_for_agents"))
    ask(agent, fixture_state("midgame_allies", 6000))
    ask(agent, fixture_state("midgame_allies", 6005))

    assert chooser.polled == ["6000", "6005"] and chooser.submitted == []


def test_the_plan_selector_given_decides_the_plan_from_the_world_the_context_and_the_chooser_s_answer():
    class Selector:
        def __init__(self) -> None:
            self.seen = []

        def select(self, world, ctx, choice):
            self.seen.append((world.tick, ctx.groups.count("guard", "e1"), choice))
            return "PRESS" if choice == "an answer" else "RAID"

    selector, chooser = Selector(), StubChooser(None, "an answer")
    plans = []
    manager = ScriptedManager(
        "combat", lambda world, ctx: plans.append(ctx.plan) or [Intent(Priority.MOVE, march(RIFLES[:1]))]
    )
    agent = new_agent(manager, chooser=chooser, plan_selector=selector)

    first = agent.choose_action(
        situations.GameState.from_mcp_state(fixture_state("midgame_allies", 6000)), situations.context()
    )
    ask(agent, fixture_state("midgame_allies", 6005))

    assert selector.seen == [(6000, 5, None), (6005, 5, "an answer")]
    assert plans == ["RAID", "PRESS"] and agent.current_plan == "PRESS"
    assert first.reasoning_summary == "RAID: attack_move 1"


# -- the platform's answer and the end of the match ------------------------------------------------


def accepted(*statuses: str) -> dict:
    rows = [{"index": index, "cmd": "x", "status": status, "reason": status} for index, status in enumerate(statuses)]
    return {"accepted": True, "status": "in_progress", "pending": True, "results": rows}


def test_the_platform_s_answer_reaches_the_ledger():
    agent = new_agent(proposing("combat", march(RIFLES), {"cmd": "stop", "units": [119]}))
    ask(agent, fixture_state("midgame_allies"))

    agent.on_action_result(accepted("pending", "rejected"), situations.context())

    assert [entry.status for entry in agent.ledger.entries] == ["pending", "rejected"]
    assert agent.ledger.orders_rejected_by_reason == {"rejected": 1}


def test_the_platform_s_answer_is_logged_once_as_an_action_result(match_record):
    agent = new_agent(proposing("combat", march(RIFLES)))
    ask(agent, fixture_state("midgame_allies"))

    agent.on_action_result(accepted("pending"), situations.context())

    (logged,) = match_record("action_result")
    assert logged["result"] == accepted("pending") and logged["accepted"] is True


def test_the_agent_has_no_say_in_a_messaging_round():
    assert not hasattr(new_agent(), "choose_message")


def test_the_end_of_the_match_logs_the_ledger_s_counters_once_before_the_result(match_record):
    agent = new_agent(proposing("combat", march(RIFLES)))
    ask(agent, fixture_state("midgame_allies"))
    agent.finalize()

    lines = match_record()

    assert [line["kind"] for line in lines][-2:] == ["ledger_summary", "result"]
    assert [line["kind"] for line in lines].count("ledger_summary") == 1
    assert lines[-2]["data"] == {**agent.ledger.summary(), "orders_sent": 1}


# -- what every call and every window of play leaves in the record ---------------------------------


def kinds(lines: list[dict]) -> list[str]:
    return [line["kind"] for line in lines]


def test_a_call_that_sends_a_batch_logs_the_state_and_then_the_exact_action_with_its_compute_time(match_record):
    agent = new_agent(proposing("combat", march(RIFLES)))

    decision = agent.choose_action(
        situations.GameState.from_mcp_state(fixture_state("midgame_allies", 6000)), situations.context()
    )

    # The first playing cycle of a match brings the enemy army estimate up to date (the fixture shows enemy units) and
    # sets its first plan.
    state, estimate, plan_change, action, _summary, _result = match_record()
    assert (state["kind"], estimate["kind"], plan_change["kind"], action["kind"]) == (
        "state",
        "enemy_estimate",
        "plan_change",
        "action",
    )
    assert (state["data"]["state_version"], state["data"]["phase"]) == (6000, "playing")
    assert action["data"]["state_version"] == 6000 and action["data"]["action"] == decision.action
    assert 0 <= action["data"]["compute_ms"] < 150


def test_a_call_that_waits_logs_the_state_and_the_action_wait(match_record):
    agent = new_agent()

    ask(agent, not_playing("waiting_for_agents"))
    ask(agent, fixture_state("midgame_allies", 6000))

    lines = match_record()
    assert kinds(lines) == [
        "state",
        "action",
        "state",
        "enemy_estimate",
        "plan_change",
        "action",
        "ledger_summary",
        "result",
    ]
    assert [line["data"]["action"] for line in lines if line["kind"] == "action"] == ["WAIT", "WAIT"]
    assert [line["data"]["phase"] for line in lines if line["kind"] == "state"] == ["waiting_for_agents", "playing"]


def test_every_action_event_counts_the_orders_of_its_batch_and_the_engine_commands_they_make(match_record):
    agent = new_agent(proposing("combat", march(RIFLES), {"cmd": "stop", "units": [105]}))

    decision = ask(agent, fixture_state("midgame_allies", 6000))
    ask(new_agent(), fixture_state("midgame_allies", 6005))

    sent, waited = match_record("action")
    assert (sent["order_count"], sent["command_count"]) == (len(decision), sum(map(command_count, decision)))
    assert (sent["order_count"], sent["command_count"]) == (2, len(RIFLES) + 1)
    assert (waited["action"], waited["order_count"], waited["command_count"]) == ("WAIT", 0, 0)


def test_every_state_event_names_the_plan_in_force_and_the_enemy_army_value_seen_as_the_state_arrived(match_record):
    agent = own_agent()
    after_each_call = []

    for tick in (6000, 6005, 6010):
        ask(agent, fixture_state("midgame_allies", tick))
        after_each_call.append((agent.current_plan, agent.ctx.scout_report.enemy_combat_value))

    states = match_record("state")
    assert [(state["plan"], state["enemy_army_value"]) for state in states] == [(OPENING, 0), *after_each_call[:2]]
    assert after_each_call[0][1] > 0


def test_the_compute_time_covers_the_whole_call(match_record):
    def slow(world, ctx):
        started = time.perf_counter()
        while time.perf_counter() - started < 0.02:
            pass
        return ()

    ask(new_agent(ScriptedManager("upkeep", slow)), fixture_state("midgame_allies"))

    (action,) = match_record("action")
    assert 20 <= action["compute_ms"] < 150


def test_a_call_the_world_could_not_read_still_logs_its_state_and_the_action_wait(match_record, monkeypatch):
    monkeypatch.setattr(World, "update", lambda self, raw: 1 // 0)

    ask(new_agent(), fixture_state("midgame_allies"))

    assert kinds(match_record()) == ["state", "error", "action", "ledger_summary", "result"]


def test_a_terminal_state_logs_the_ledger_s_summary_and_nothing_else(match_record):
    agent = new_agent(proposing("combat", march(RIFLES)))
    ask(agent, fixture_state("midgame_allies", 6000))
    agent.on_action_result(accepted("pending"), situations.context())
    final = fixture_state("midgame_allies", 6005)
    final.update(phase="finished", is_terminal=True)
    final["last_orders"] = {
        "batch": 1,
        "pending": False,
        "results": [{"index": 0, "status": "rejected", "reason": "invalid"}],
    }

    orders = ask(agent, final)

    lines = match_record()
    assert orders == [] and kinds(lines) == [
        "state",
        "enemy_estimate",
        "plan_change",
        "action",
        "action_result",
        "ledger_summary",
        "result",
    ]
    assert lines[5]["data"]["orders_rejected_by_reason"] == {"invalid": 1}


def test_a_terminal_state_that_cannot_be_read_is_answered_with_wait_and_the_failure_logged(match_record, monkeypatch):
    monkeypatch.setattr(World, "update", lambda self, raw: 1 // 0)
    final = fixture_state("midgame_allies")
    final["is_terminal"] = True

    orders = ask(new_agent(), final)

    error, _summary, _result = match_record()
    assert orders == [] and what_went_wrong(error["data"]) == ("choose_action", "unhandled", "ZeroDivisionError")


def test_a_terminal_state_seen_first_logs_no_game_config_and_no_state(match_record):
    final = fixture_state("opening_mcv")
    final["is_terminal"] = True

    ask(new_agent(), final, situations.context(FakeRedAlert().game_config))

    assert kinds(match_record()) == ["ledger_summary", "result"]


def deploying_once() -> ScriptedManager:
    """A manager that deploys the fake game's MCV in the first state and proposes nothing after."""
    order = Intent(Priority.PRODUCTION, {"cmd": "deploy", "units": [100]})
    return ScriptedManager("opening", lambda world, ctx: [order] if world.tick == 0 else [])


def events_after(lines: list[dict], kind: str) -> list[str]:
    """The kind of the event that follows each event of a kind."""
    return [later["kind"] for earlier, later in zip(lines, lines[1:]) if earlier["kind"] == kind]


def test_a_fake_match_of_1000_ticks_logs_four_timing_events(match_record):
    game = FakeRedAlert()

    game.run(new_agent(deploying_once()), 201, situations.context(game.game_config))

    timings = match_record("timing")
    assert [(timing["from_tick"], timing["to_tick"]) for timing in timings] == [
        (0, 250),
        (250, 500),
        (500, 750),
        (750, 1000),
    ]
    assert [timing["ticks_per_call"] for timing in timings] == [{"mean": 5.0, "max": 5}] * 4


def test_every_timing_event_is_followed_by_the_ledger_s_counters(match_record):
    game = FakeRedAlert()

    game.run(new_agent(deploying_once()), 201, situations.context(game.game_config))

    lines = match_record()
    assert events_after(lines, "timing") == ["ledger_summary"] * 4
    assert kinds(lines).count("ledger_summary") == 5 and kinds(lines)[-2:] == ["ledger_summary", "result"]


def test_a_timing_event_holds_the_compute_time_the_wait_for_states_and_the_round_trips_of_its_window(match_record):
    game = FakeRedAlert()
    cells = itertools.count(30)
    walking = ScriptedManager(
        "combat", lambda world, ctx: [Intent(Priority.MOVE, {"cmd": "move", "units": [100], "to": [next(cells), 48]})]
    )

    game.run(new_agent(walking), 51, situations.context(game.game_config))

    (timing,) = match_record("timing")
    assert 0 < timing["compute_ms"]["mean"] <= timing["compute_ms"]["max"] < 150
    assert 0 < timing["state_age_ms"]["mean"] <= timing["state_age_ms"]["max"]
    assert 0 < timing["round_trip_ms"]["mean"] <= timing["round_trip_ms"]["max"]


def test_a_window_without_an_answered_batch_has_no_round_trip_figure(match_record):
    game = FakeRedAlert()

    game.run(new_agent(), 51, situations.context(game.game_config))

    (timing,) = match_record("timing")
    assert timing["round_trip_ms"] == {"mean": None, "max": None}


def test_the_periodic_summary_holds_the_ledger_s_counters_as_they_stand_then(match_record):
    game = FakeRedAlert()

    game.run(new_agent(deploying_once()), 51, situations.context(game.game_config))

    periodic, closing = match_record("ledger_summary")
    assert (
        periodic
        == closing
        == {
            "orders_sent": 1,
            "orders_rejected_by_reason": {},
            "orders_lost": 0,
            "dropped_invalid": 0,
            "duplicate_production_orders": 0,
        }
    )


EARLY_BASE = (("fact", 20, 48), ("weap", 22, 53))
TECH_BASE = (*EARLY_BASE, ("fix", 26, 54), ("dome", 30, 50))
# Four 2tnk and four e1 seen far from the base: $3,800, 89% of it in vehicles.
TANK_ARMY = [
    {"id": 900 + number, "type": item, "x": 74, "y": 40 + number, "hp": 100}
    for number, item in enumerate(("2tnk",) * 4 + ("e1",) * 4)
]


def test_every_timing_event_records_the_mix_stage_and_the_counter_shift(match_record):
    agent = new_agent()

    for tick, buildings, enemies in [(1000, EARLY_BASE, []), (1250, EARLY_BASE, []), (1500, TECH_BASE, TANK_ARMY)]:
        ask(agent, situations.base_state("england", *buildings, tick=tick, visible_enemies=enemies))

    timings = match_record("timing")
    assert [(timing["mix_stage"], timing["counter_shift"]) for timing in timings] == [
        ("base", "none"),
        ("dome", "vehicles"),
    ]


def test_the_mix_stage_of_a_timing_event_is_fix_with_a_service_depot_alone(match_record):
    agent = new_agent()

    for tick in (1000, 1250):
        ask(agent, situations.base_state("russia", *EARLY_BASE, ("fix", 26, 54), tick=tick))

    (timing,) = match_record("timing")
    assert (timing["mix_stage"], timing["counter_shift"]) == ("fix", "none")


def test_air_a_timing_event_names_the_air_shift_alone_and_joined_to_the_counter_shift(match_record):
    agent = new_agent()
    # Three yaks, the fewest enemy combat aircraft the shift answers, worth 4,050; six more 2tnk keep the vehicles above
    # half of what is seen.
    yaks = [{"id": 950 + number, "type": "yak", "x": 74, "y": 30 + number, "hp": 100} for number in range(3)]
    more_tanks = [{"id": 960 + number, "type": "2tnk", "x": 74, "y": 50 + number, "hp": 100} for number in range(6)]

    for tick, enemies in [(1000, []), (1250, yaks), (1500, [*TANK_ARMY, *more_tanks, *yaks])]:
        ask(agent, situations.base_state("england", *TECH_BASE, tick=tick, visible_enemies=enemies))

    timings = match_record("timing")
    assert [timing["counter_shift"] for timing in timings] == ["air", "vehicles+air"]


def test_no_timing_event_is_logged_before_the_game_is_playing(match_record):
    agent = new_agent()

    for _ in range(100):
        ask(agent, not_playing("waiting_for_agents"))

    assert match_record("timing") == []


# -- failures that must not stop play --------------------------------------------------------------


def failing(name: str, error: Exception) -> ScriptedManager:
    """A manager that raises in every cycle."""

    def fail(world, ctx):
        raise error

    return ScriptedManager(name, fail)


def what_went_wrong(error: dict) -> tuple[str, str, str]:
    return error["where"], error["type"], error["exception"]


def test_a_manager_that_raises_costs_only_its_own_orders_and_is_named_in_one_handled_error(match_record):
    agent = new_agent(
        proposing("upkeep", {"cmd": "build", "item": "dome"}, priority=Priority.PRODUCTION),
        failing("opening", KeyError("fix")),
        proposing("combat", march(RIFLES)),
    )

    orders = ask(agent, fixture_state("midgame_allies"))

    (error,) = match_record("error")
    assert orders == [{"cmd": "build", "item": "dome"}, march(RIFLES)]
    assert what_went_wrong(error) == ("opening", "handled", "KeyError")
    assert error["message"].startswith("'fix' (raised at test_loop.py:") and error["message"].endswith(" in fail)")


def test_where_a_manager_s_failure_was_raised_is_recorded_without_any_file_being_read_or_looked_at(
    match_record, monkeypatch
):
    agent = new_agent(failing("opening", KeyError("fix")))
    touched: list[tuple[str, int]] = []
    # The standard listing of a traceback stats a source file and reads it through `tokenize.open`.
    for owner, name in ((builtins, "open"), (os, "stat"), (tokenize, "open")):
        _spy_on(monkeypatch, owner, name, touched)

    ask(agent, fixture_state("midgame_allies"))
    during_the_cycle = _made_on_this_thread(touched)

    (error,) = match_record("error")
    assert during_the_cycle == []
    assert error["message"].startswith("'fix' (raised at test_loop.py:") and error["message"].endswith(" in fail)")


def test_an_error_that_was_never_raised_is_logged_with_its_message_alone(match_record):
    agent_module._log_error("plan", "handled", RuntimeError("never raised"))

    (error,) = match_record("error")
    assert (what_went_wrong(error), error["message"]) == (("plan", "handled", "RuntimeError"), "never raised")


def test_a_manager_that_fails_in_every_cycle_is_reported_each_time_and_the_others_keep_playing(match_record):
    cells = iter([[60, 48], [61, 48], [62, 48]])
    marching = ScriptedManager("combat", lambda world, ctx: [Intent(Priority.MOVE, march(RIFLES, next(cells)))])
    agent = new_agent(failing("upkeep", RuntimeError("down")), marching)

    batches = [ask(agent, fixture_state("midgame_allies", tick)) for tick in (6000, 6005, 6010)]

    assert batches == [[march(RIFLES, [60, 48])], [march(RIFLES, [61, 48])], [march(RIFLES, [62, 48])]]
    assert [what_went_wrong(error) for error in match_record("error")] == [("upkeep", "handled", "RuntimeError")] * 3


def test_with_the_world_update_raising_the_call_returns_wait_and_logs_the_error(match_record, monkeypatch):
    agent = new_agent(proposing("combat", march(RIFLES)))
    monkeypatch.setattr(World, "update", lambda self, raw: 1 // 0)

    decision = agent.choose_action(
        situations.GameState.from_mcp_state(fixture_state("midgame_allies")), situations.context()
    )

    (error,) = match_record("error")
    assert decision is WAIT and agent.ledger.entries == []
    assert what_went_wrong(error) == ("world", "handled", "ZeroDivisionError")


def test_the_state_after_one_the_world_could_not_read_is_played(monkeypatch):
    agent = new_agent(proposing("combat", march(RIFLES)))
    read, unreadable = World.update, iter([ValueError("unreadable")])

    def update(self, raw):
        for error in unreadable:
            raise error
        read(self, raw)

    monkeypatch.setattr(World, "update", update)

    first = ask(agent, fixture_state("midgame_allies", 6000))
    second = ask(agent, fixture_state("midgame_allies", 6005))

    assert (first, second) == ([], [march(RIFLES)])


class BrokenSelector:
    def select(self, world, ctx, choice):
        raise LookupError("no plan")


class BrokenChooser(StubChooser):
    def poll(self, current_state_tag):
        raise OSError("socket closed")


@pytest.mark.parametrize(
    "broken, exception",
    [({"plan_selector": BrokenSelector()}, "LookupError"), ({"chooser": BrokenChooser()}, "OSError")],
    ids=["the plan selector", "the chooser"],
)
def test_when_the_plan_step_fails_the_plan_stays_what_it_was_and_the_managers_still_run(
    match_record, broken, exception
):
    agent = new_agent(proposing("combat", march(RIFLES)), **broken)

    orders = ask(agent, fixture_state("midgame_allies"))

    (error,) = match_record("error")
    assert orders == [march(RIFLES)] and agent.current_plan == OPENING
    assert what_went_wrong(error) == ("plan", "handled", exception)


def test_a_failure_outside_any_manager_is_logged_as_unhandled_and_answered_with_wait(match_record, monkeypatch):
    agent = new_agent(proposing("combat", march(RIFLES)))
    monkeypatch.setattr(agent.ledger, "reconcile", lambda world, raw: [][0])

    decision = agent.choose_action(
        situations.GameState.from_mcp_state(fixture_state("midgame_allies")), situations.context()
    )

    (error,) = match_record("error")
    assert decision is WAIT
    assert what_went_wrong(error) == ("choose_action", "unhandled", "IndexError")
    assert "(raised at test_loop.py:" in error["message"]


def test_the_state_after_an_unhandled_failure_is_played(monkeypatch):
    agent = new_agent(proposing("combat", march(RIFLES)))
    admit, failures = agent.ledger.admit, iter([TypeError("not an intent")])

    def admit_after_failing_once(intents, world):
        for error in failures:
            raise error
        return admit(intents, world)

    monkeypatch.setattr(agent.ledger, "admit", admit_after_failing_once)

    first = ask(agent, fixture_state("midgame_allies", 6000))
    second = ask(agent, fixture_state("midgame_allies", 6005))

    assert (first, second) == ([], [march(RIFLES)])


def test_a_failure_in_taking_in_the_platform_s_answer_is_logged_as_unhandled_and_does_not_raise(
    match_record, monkeypatch
):
    agent = new_agent()
    monkeypatch.setattr(agent.ledger, "on_action_result", lambda result, context: result["missing"])

    agent.on_action_result({"accepted": True}, situations.context())

    (error,) = match_record("error")
    assert what_went_wrong(error) == ("on_action_result", "unhandled", "KeyError")


def test_failures_are_survived_without_an_open_record_too(run_folder):
    agent = new_agent(failing("upkeep", RuntimeError("down")), proposing("combat", march(RIFLES)))

    assert ask(agent, fixture_state("midgame_allies")) == [march(RIFLES)]
    assert not (run_folder / "runs").exists()


# -- an agent made with nothing given --------------------------------------------------------------


def _refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("no model server in the tests", request=request)


@pytest.fixture
def offline_chains(monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent's chains built as the tracked config/agent.toml lists them, each source over a transport that refuses
    every request: an agent made in the repository with nothing given reaches no model server."""
    refusing = httpx.MockTransport(_refuse)
    monkeypatch.setattr(agent_module, "build_chain", lambda name: build_chain(name, transport=refusing))


def test_an_agent_made_in_the_repository_reads_the_tracked_item_facts_and_thresholds(monkeypatch, offline_chains):
    monkeypatch.chdir(REPO_ROOT)

    agent = RedAlertAgent()

    assert agent.ctx.tuning == Tuning.from_file(REPO_ROOT)
    assert agent.world.items["weap"].cost == 2000 and len(agent.world.items) > 50
    assert isinstance(agent.ctx.chooser, AsyncChooser)


def test_an_agent_made_where_no_configuration_is_plays_on_built_in_thresholds_and_says_so(match_record):
    agent = RedAlertAgent()

    orders = ask(agent, fixture_state("opening_mcv"), situations.context(FakeRedAlert().game_config))

    errors = match_record("error")
    assert orders == [{"cmd": "deploy", "units": [100]}]
    assert agent.ctx.tuning == Tuning() and agent.world.items["weap"].cost == 2000
    assert [(error["where"], error["type"], error["exception"]) for error in errors] == [
        ("redalert.config", "handled", "ConfigError")
    ] * 3
    assert ["tuning.toml" in errors[0]["message"], "items.toml" in errors[1]["message"]] == [True, True]
    assert "agent.toml" in errors[2]["message"]


def test_the_chooser_made_by_default_hands_nothing_over_when_nothing_was_asked(monkeypatch, offline_chains):
    monkeypatch.chdir(REPO_ROOT)
    agent = RedAlertAgent()

    assert agent.ctx.chooser.poll("100") is None


def test_two_agents_share_no_world_ledger_or_context():
    first, second = new_agent(proposing("combat", march(RIFLES))), new_agent()

    ask(first, fixture_state("midgame_allies"))

    assert second.world.tick == 0 and second.ledger.entries == [] and second.ctx is not first.ctx
    assert first.ledger.orders_sent == 1


# -- transports ------------------------------------------------------------------------------------

APC_CELL = (40, 40)
# Cells within 2 of the APC's, from which a rocket soldier boards in the next step.
BESIDE_THE_APC = [(40, 42), (41, 41), (39, 41), (42, 40), (38, 40)]


def boarding_while_outside(rockets: list[int], apc: int) -> ScriptedManager:
    """A raid manager that orders the rockets still in the units into the APC, in every cycle."""

    def propose(world: World, _ctx) -> list[Intent]:
        outside = [rocket for rocket in rockets if rocket in world.units]
        order = {"cmd": "enter_transport", "units": outside, "target": apc}
        return [Intent(Priority.MOVE, order)] if outside else []

    return ScriptedManager("raids", propose)


def test_cargo_the_agent_hands_its_world_every_batch_it_sends_so_rockets_that_board_an_apc_are_aboard():
    game = FakeRedAlert("russia", "england")
    apc = game.add_own("apc", *APC_CELL)
    rockets = [game.add_own("e3", x, y) for x, y in BESIDE_THE_APC]
    agent = new_agent(boarding_while_outside(rockets, apc))

    game.run(agent, 3)

    assert [unit.id for unit in agent.world.cargo[apc]] == rockets
    assert [entry.order["cmd"] for entry in agent.ledger.entries] == ["enter_transport"]


def test_cargo_a_rocket_still_walking_to_its_apc_100_ticks_after_it_was_ordered_in_is_ordered_again_and_boards():
    game = FakeRedAlert("russia", "england")
    apc = game.add_own("apc", *APC_CELL)
    # 10 cells from the APC at 0.264 cells a step, the rocket soldier comes within 2 cells in the 29th step.
    rocket = game.add_own("e3", APC_CELL[0] + 10, APC_CELL[1])
    order = {"cmd": "enter_transport", "units": [rocket], "target": apc}
    agent = new_agent(
        ScriptedManager("raids", lambda world, ctx: [Intent(Priority.MOVE, order)] if world.tick == 0 else [])
    )

    game.run(agent, 35)

    assert [(entry.sent_tick, entry.order) for entry in agent.ledger.entries] == [(0, order), (100, order)]
    assert [unit.id for unit in agent.world.cargo[apc]] == [rocket] and game.aboard(apc) == [rocket]


def test_cargo_the_agent_s_world_takes_aboard_as_many_as_the_tuning_it_plays_with_lets_a_transport_carry():
    game = FakeRedAlert("russia", "england")
    apc = game.add_own("apc", *APC_CELL)
    rockets = [game.add_own("e3", x, y) for x, y in BESIDE_THE_APC]
    tuning = dataclasses.replace(Tuning(), world=dataclasses.replace(Tuning().world, transport_capacity=2))
    agent = new_agent(boarding_while_outside(rockets, apc), tuning=tuning)

    game.run(agent, 3)

    assert [unit.id for unit in agent.world.cargo[apc]] == rockets[:2]


# -- the agent's own managers in the loop ----------------------------------------------------------


def assert_within_the_limits(batches: list[list[dict]]) -> None:
    """Every batch holds 1 to 20 orders, none for more than 50 units, and expands to at most 200 engine commands."""
    for orders in batches:
        assert 1 <= len(orders) <= 20
        assert max(len(order.get("units", ())) for order in orders) <= 50
        assert sum(command_count(order) for order in orders) <= 200


def test_with_the_opening_manager_patched_to_raise_the_batch_still_holds_the_upkeep_manager_s_orders(
    match_record, monkeypatch
):
    def broken(self, world, ctx):
        raise RuntimeError("no build today")

    monkeypatch.setattr(OpeningManager, "intents", broken)
    waiting = situations.base_state(
        "england", ("fact", 22, 48), production=[situations.job("Building", "powr", ready=True)]
    )

    orders = ask(own_agent(), waiting)

    (error,) = match_record("error")
    assert orders == [{"cmd": "place", "item": "powr", "at": [25, 48]}]
    assert what_went_wrong(error) == ("opening", "handled", "RuntimeError")
    assert error["message"].startswith("no build today (raised at test_loop.py:")


def test_every_batch_of_a_fake_match_played_by_the_agent_s_own_managers_is_within_the_limits():
    game = FakeRedAlert("russia", "england")

    played = game.run(own_agent(), 5 * 300)

    assert_within_the_limits([batch["orders"] for batch in played.batches])
    assert played.invalid_actions == 0 and len(played.batches) >= 40


class TimingOutSource(DecisionSource):
    """A decision source that never answers in time."""

    name = "stub:slow"

    def __init__(self) -> None:
        self.asked = 0

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        self.asked += 1
        return [SourceCall(self.name, answer=None, confidence=None, latency_ms=request.deadline_ms, outcome=TIMEOUT)]


class AskingSelector:
    """A plan selector that asks the chooser in every cycle and plays OPENING until a war factory stands, then HOLD."""

    def __init__(self) -> None:
        self.answers: list = []

    def select(self, world, ctx, choice):
        if choice is not None:
            self.answers.append(choice)
        options = [Option("HOLD", "stay home"), Option("RAID", "raid the harvesters")]
        ctx.chooser.submit(ChoiceRequest("Which plan?", options, "", deadline_ms=1500, state_tag=str(world.tick)))
        return HOLD if ctx.production.standing("weap") else OPENING


def test_with_a_decision_source_that_times_out_on_every_call_500_states_are_all_answered_with_a_batch_or_wait():
    source, selector = TimingOutSource(), AskingSelector()
    chooser = AsyncChooser(source, lambda asked_tick, tick: False)
    agent = RedAlertAgent(
        items=situations.tracked_items(),
        tuning=Tuning(),
        chooser=chooser,
        plan_selector=selector,
        settings=settings_models_off(),
    )
    game = FakeRedAlert("england", "russia")

    played = game.run(agent, 500)

    assert played.waits + len(played.batches) == 500 and played.invalid_actions == 0
    assert source.asked > 10 and len(selector.answers) > 10
    assert {(answer.outcome, answer.option_id) for answer in selector.answers} == {("timeout", None)}
    assert agent.current_plan == OPENING and agent.ctx.production.standing("proc") == 2


# -- the long replay -------------------------------------------------------------------------------

# What can wait on a network, a disk or a clock: (where it lives, its name).
BLOCKING_CALLS = (
    (socket.socket, "connect"),
    (urllib.request, "urlopen"),
    (httpx.Client, "send"),
    (builtins, "open"),
    (time, "sleep"),
)


@dataclasses.dataclass
class Replay:
    """What replaying the long scripted match through the agent's loop showed."""

    # The compute time of every call as the agent logged it, and as the replay itself measured it.
    logged_ms: list[float]
    measured_ms: list[float]
    errors: list[dict]
    batches: list[list[dict]]
    # The calls of `BLOCKING_CALLS` made on the thread that called `choose_action`, during the replay.
    blocking_calls: list[str]
    # Whether the spy noticed a file read and a sleep made on that thread on purpose, after the replay.
    spy_is_live: bool


def _opens_for_reading(arguments: tuple, options: dict) -> bool:
    mode = arguments[1] if len(arguments) > 1 else options.get("mode", "r")
    return "r" in mode or "+" in mode


def _spy_on(patch: pytest.MonkeyPatch, owner, name: str, seen: list[tuple[str, int]]) -> None:
    """Replace a function that can block by one that notes the call and the thread it was made on, and then calls it."""
    real = getattr(owner, name)

    def spy(*arguments, **options):
        if name != "open" or _opens_for_reading(arguments, options):
            seen.append((name, threading.get_ident()))
        return real(*arguments, **options)

    patch.setattr(owner, name, spy)


def _made_on_this_thread(seen: list[tuple[str, int]]) -> list[str]:
    return [name for name, thread in seen if thread == threading.get_ident()]


def _block_on_purpose() -> None:
    time.sleep(0)
    with open(os.devnull, encoding="utf-8"):
        pass


def _open_a_record(patch: pytest.MonkeyPatch, folder: Path, logged: list[tuple[str, dict]]) -> None:
    """Open a match record under a folder as a match would have one, and note every event logged to it."""
    patch.chdir(folder)
    patch.setenv(paths.ROOT_ENV, str(folder))
    patch.setattr(records, "_process", records._ProcessRecord())
    patch.setattr(runner, "_terminal_game_state", runner._terminal_game_state)
    real_log = records.log
    patch.setattr(records, "log", lambda kind, data: logged.append((kind, data)) or real_log(kind, data))
    records.start_match(situations.context(), None)


def _play_back(agent: RedAlertAgent, states: list[dict]) -> tuple[list[float], list[list[dict]]]:
    """Hand every state to the agent in turn: how long each call took in milliseconds, and the orders of each batch."""
    context = situations.context()
    measured_ms, batches = [], []
    for raw in states:
        state = situations.GameState.from_mcp_state(raw)
        started = time.perf_counter_ns()
        decision = agent.choose_action(state, context)
        measured_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        if decision is not WAIT:
            batches.append(situations.orders_of(decision))
    return measured_ms, batches


def _events(logged: list[tuple[str, dict]], wanted: str) -> list[dict]:
    return [data for kind, data in logged if kind == wanted]


@pytest.fixture(scope="module")
def replay(long_match_states: list[dict], tmp_path_factory: pytest.TempPathFactory) -> Replay:
    """The long scripted match replayed once through a fresh agent, with a match record open as in a real match."""
    seen: list[tuple[str, int]] = []
    logged: list[tuple[str, dict]] = []
    with pytest.MonkeyPatch.context() as patch:
        _open_a_record(patch, tmp_path_factory.mktemp("replay"), logged)
        agent = own_agent()
        for owner, name in BLOCKING_CALLS:
            _spy_on(patch, owner, name, seen)
        # The replay keeps 3,200 whole states in memory, which a match never does. Left to the collector, a
        # pass over all of them lands in one call of the loop; set aside, the collector sees only what the
        # agent itself allocates, which is what the latency budget is about.
        gc.collect()
        gc.freeze()
        try:
            measured_ms, batches = _play_back(agent, long_match_states)
        finally:
            gc.unfreeze()
        during_the_replay = _made_on_this_thread(seen)
        _block_on_purpose()
        noticed_after = _made_on_this_thread(seen)[len(during_the_replay) :]
        records.finish_match(situations.RESULT)
    return Replay(
        logged_ms=[action["compute_ms"] for action in _events(logged, "action")],
        measured_ms=measured_ms,
        errors=_events(logged, "error"),
        batches=batches,
        blocking_calls=during_the_replay,
        spy_is_live=noticed_after == ["sleep", "open"],
    )


def test_the_replay_is_of_more_than_3000_states_with_at_least_150_own_units_at_its_peak(replay, long_match_states):
    most_units = max(len(raw["observation"]["units"]) for raw in long_match_states)

    assert len(replay.logged_ms) == len(replay.measured_ms) == len(long_match_states) > 3000
    assert most_units >= 150


def test_over_the_replay_compute_ms_is_at_most_50_at_the_95th_percentile_and_150_at_the_maximum(replay):
    assert stats.percentile(replay.logged_ms, 95) <= 50 and max(replay.logged_ms) <= 150
    assert stats.percentile(replay.measured_ms, 95) <= 50 and max(replay.measured_ms) <= 150


def test_the_replay_logs_no_unhandled_error_and_no_manager_fails(replay):
    assert [error for error in replay.errors if error["type"] == "unhandled"] == []
    assert replay.errors == []


def test_during_the_replay_no_network_call_file_read_or_sleep_is_made_on_the_thread_that_answers(replay):
    assert replay.blocking_calls == []
    assert replay.spy_is_live


def test_every_batch_of_the_replay_is_within_20_orders_50_units_and_200_commands(replay):
    assert_within_the_limits(replay.batches)
    assert len(replay.batches) > 100
