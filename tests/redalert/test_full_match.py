"""Whole fake matches played by the complete agent: the standard scenario for both families, and the hostile interface.

Each match is played the way the vendored runtime plays one: the dispatcher
that `agent.create_agent()` returns gets a `red_alert` context with the game's
reference object, every state of the match and the answer to every batch, and
the match ends through the runtime's final-state function, which the agent's
result hook closes the match record from. The agent is the one that plays on
the platform, with every manager, the script's plan rules and model plan
choice off, reading the tracked files under `config/`, copied byte for byte
into a temporary project root so that the records land there.

Two things differ from a real worker process, both because the fake plays 20
minutes of game in a few seconds. The record writer's queue bound and the
wait for it at the close are sized for five states a second; here the states
come over a hundred times as fast, so the bound is lifted and the close waits
for the writer to finish, and the record holds every event. And whatever the
earlier tests of the session left in memory is frozen out of garbage
collection during the match, so that the collector's pauses are the ones the
agent's own objects cause, as in a worker process of its own.

A passing match shows that the agent behaves as specified in a known
situation. The fake game has no combat, so it is never evidence that the
agent wins.
"""

from __future__ import annotations

import gc
import itertools
import json
from collections import Counter
import math
import re
import shutil
import statistics
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx
import pytest

from agent.agent import create_agent
from altruagent import WithReasoning, runner
from fake_game import CONSTRUCTION_YARD, MAP_ID, TICKS_PER_STEP, FakeRedAlert, FakeRun, ScenarioStep, at_seconds
from local_chain import build_local_chain
from scenarios import early_rush, hostile_interface, standard
from scenarios.early_rush import EarlyRushScenario
from scenarios.hostile_interface import HostileInterfaceScenario
from scenarios.standard import StandardScenario
from switches_off import tuning_off, write_model_switches_off, write_switches_off
from ucla_agent.core import paths, records, secrets
from ucla_agent.core.dispatch import Dispatcher
from ucla_agent.core.secrets import REDACTED, SECRET_PATTERNS, secret_values
from ucla_agent.core.stats import percentile
from ucla_agent.games.redalert import agent as redalert_agent
from ucla_agent.games.redalert import clock, placement, plans, targeting, upkeep
from ucla_agent.games.redalert.batch import Priority
from ucla_agent.games.redalert.family import HARVESTER, REFINERY
from ucla_agent.games.redalert.groups import MAIN, RAID, SCOUT
from ucla_agent.games.redalert.items import SOVIET, TICKS_PER_SECOND
from ucla_agent.games.redalert.ledger import CONFIRMED, LOST, REJECTED
from ucla_agent.games.redalert.opening import OpeningManager
from ucla_agent.games.redalert.parking import PARKED_WITHIN_CELLS
from ucla_agent.games.redalert.plans import R1, R6
from ucla_agent.games.redalert.production import DEFENSE_QUEUE
from ucla_agent.games.redalert.scout_choice import QUESTION as SCOUT_QUESTION
from ucla_agent.games.redalert.scout_slots import FAST_SCOUTS, ScoutSlots
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.sight import SIGHT_CELLS, squares_within
from ucla_agent.games.redalert.tuning import FACTORY_FIRST, TUNING_FILE, Tuning

REPO_ROOT = Path(__file__).resolve().parents[2]
FAMILIES = ("england", "russia")
# queue.Queue takes a bound of 0 as none at all.
NO_QUEUE_BOUND = 0
CLOSE_WAIT_SECONDS = 300.0
SECRET_ENV_FOR_TESTS = {
    "ALTRUAGENT_OFFICIAL_AGENT_KEY": "not-an-agent-key",
    "ALTRUAGENT_CONTROL_URL": "http://127.0.0.1:9",
}
RECORD_KINDS = {"game_config", "state", "plan_change", "action", "action_result", "timing", "ledger_summary", "result"}
MAX_ORDERS, MAX_UNITS, MAX_COMMANDS = 20, 50, 200
MAX_LOW_POWER_SECONDS, MAX_READY_UNPLACED_SECONDS = 20, 10
ANSWER_WITHIN_TICKS = 2 * TICKS_PER_STEP
RAID_BY_SECONDS = 480
FINAL_SECONDS = 180
DEFENSE_KEEP_OUT_CELLS = 8
KILLS_BY_SECONDS, KILLS_AT_LEAST = 480, 1100
# The compute bound of a whole match: p95 at most 50 ms, the 99.5th percentile at most 150 ms, the single slowest cycle
# at most 300 ms. A lone garbage-collection pause on a loaded workstation can pass 150 ms while every other cycle stays
# far below it.
COMPUTE_P95_MS, COMPUTE_P995_MS, COMPUTE_MAX_MS = 50, 150, 300
P95, P995 = 95, 99.5
# Every part of the compute bound kept.
WITHIN_BUDGET = (True, True, True)
# Harvester parking as the tracked tuning file ships it, and switched on; the faction the parked match is played for.
PARKING_SHIPPED, PARKING_ON = "park_harvesters = false\n", "park_harvesters = true\n"
PARKED_FACTION = "england"
PARK_SECONDS_LEFT = 120
# The creep toward ore as the tracked tuning file ships it, and switched on.
CREEP_SHIPPED, CREEP_ON = "creep_toward_ore = false\n", "creep_toward_ore = true\n"
# The creep to the next field along the lane as the tracked tuning file ships it, and switched on; the faction it is
# played for, and the cells of a field of ore in the lane 14 cells east of the own spawn that its map gains.
NEXT_PATCH_SHIPPED, NEXT_PATCH_ON = "creep_next_patch = false\n", "creep_next_patch = true\n"
NEXT_PATCH_FACTION = "england"
LANE_ORE = [(x, y) for y in (45, 46, 47) for x in (35, 36, 37)]
# The army mix's main force as the project root's tuning file has it, mixed, and built around tanks; and for each
# family's start the tank its main force makes once the service depot stands.
MAIN_FORCE_MIXED, MAIN_FORCE_TANKS = 'main_force = "mixed"\n', 'main_force = "tanks"\n'
HEAVIER_TANK = {"england": "2tnk", "russia": "3tnk"}
SERVICE_DEPOT = "fix"
# The standing raid group as the tracked tuning file ships it, and switched on.
STANDING_SHIPPED, STANDING_ON = "standing = false\n", "standing = true\n"
# The plans the raid group raids under only when it is standing.
ONLY_STANDING_PLANS = {"HOLD", "STRIKE", "FINISH"}
# Scouting again whenever the picture of the enemy goes stale as the tracked tuning file ships it, on; the indirect
# way as the file ships it and switched on; the faction the match is played for, and the scout missions it sends at
# the least.
RESCOUT_SHIPPED = "rescout = true\n"
INDIRECT_SHIPPED, INDIRECT_ON = "indirect_route = false\n", "indirect_route = true\n"
SCOUTING_FACTION = "england"
SCOUT_MISSIONS_AT_LEAST = 4
SCOUT_REASONS = {"initial", "stale_base", "stale_army", "stale_field"}
# The Soviet raid as the tracked tuning file ships it, and of rockets carried by APCs; the faction it is played for.
SOVIET_RAID_SHIPPED, SOVIET_RAID_APCS = 'soviet_raid = "infantry"\n', 'soviet_raid = "apc_rockets"\n'
APC_FACTION = "russia"
# RAID open only while a raid could set out, switched off and on; and how soon after
# the wait for a raid runs out the plan leaves RAID: the raid manager shuts RAID in one cycle, the plan rules close it
# in the next.
LAUNCHABLE_OFF, LAUNCHABLE_ON = "launchable_only = false\n", "launchable_only = true\n"
RAID_CLOSES_WITHIN_TICKS = 2 * TICKS_PER_STEP
# The lean opening's infantry switched off and on; the most rifles it then trains.
LEAN_OFF, LEAN_ON = "lean_infantry = false\n", "lean_infantry = true\n"
LEAN_OPENING_RIFLES = 3
# The scout slots switched off and on; the slots are filled by this time of the
# match; with the lean opening as well, the most rifles the opening trains, two of them the slots'.
SLOTS_OFF, SLOTS_ON = "scout_slots = false\n", "scout_slots = true\n"
SLOTS_FILLED_BY_SECONDS = 90
LEAN_SLOTS_OPENING_RIFLES = 5
LEAN_SLOTS_FACTION = "england"
# The time by which the map the scouts have seen is compared, 10:00, and the faction of the matches compared.
EXPLORED_BY_TICK = 600 * TICKS_PER_SECOND
# What a faction's match saw by 10:00 with the scout slots on, filled as their fixture plays it.
EXPLORED_WITH_SLOTS: dict[str, int] = {}
# The model's choice of the scouts' destinations as the tracked config/agent.toml ships it, and switched on; the faction
# the match is played for.
SCOUT_CHOICE_SHIPPED, SCOUT_CHOICE_ON = "model_scout_choice = false\n", "model_scout_choice = true\n"
SCOUT_CHOICE_FACTION = "england"
# The commands that name units, and what each order of a batch expands to in the engine.
UNIT_COMMANDS = {"move", "attack_move", "attack", "stop", "deploy", "unload", "guard", "enter_transport", "harvest"}
# Model plan choice as every copy of the tracked config/agent.toml below writes it, off with the model commander so
# that no match reaches a model server, and switched on; the faction it is played for, and the source and role the
# plan chain asks, built by local_chain: the shipped chain asks the systemone role jev9b, which no stand-in here is.
AGENT_CONFIG_FILE = Path("config") / "agent.toml"
PLAN_CHOICE_OFF, PLAN_CHOICE_ON = "model_plan_choice = false\n", "model_plan_choice = true\n"
PLAN_CHOICE_FACTION = "england"
PLAN_SOURCE, PLAN_ROLE = "local:small", "small"
CHAIN_START_MS = 20
# Shaped like an agent API key: the stand-in model writes it, and the agent's own key, into every answer. Built in
# parts, so that this tracked file holds no string the secret scan would report.
LEAKED_SHAPED_KEY = "sk_" + "agent_" + "stub0123456789"
# What a model is never sent, and so what no io may hold: the request headers and the credentials they carry.
HEADER_NAMES = ("x-client", "authorization", "x-api-key")


@dataclass(frozen=True)
class Cycle:
    """One request for a move: the state's tick, what the agent sent, and what the test needs to judge it by."""

    tick: int
    plan: str
    raid: frozenset[int]
    # The enemy units in sight, and the cell of every enemy actor in sight, by id.
    enemy_units_in_sight: frozenset[int]
    enemy_cells: dict[int, tuple[int, int]]
    kills_cost: int
    orders: list[dict[str, Any]]


class Observer:
    """Stands where the runtime's agent stands: hands every call on to the dispatcher and notes each cycle."""

    def __init__(self, dispatcher: Dispatcher) -> None:
        self.dispatcher = dispatcher
        self.cycles: list[Cycle] = []

    def choose_action(self, state: Any, context: Any) -> Any:
        decision = self.dispatcher.choose_action(state, context)
        observation = state.raw["observation"]
        agent = self.dispatcher.decision_maker
        in_sight = [*observation["visible_enemies"], *observation["visible_enemy_buildings"]]
        self.cycles.append(
            Cycle(
                tick=state.raw["state_version"],
                plan=agent.current_plan,
                raid=frozenset(agent.ctx.groups.members(RAID)),
                enemy_units_in_sight=frozenset(enemy["id"] for enemy in observation["visible_enemies"]),
                enemy_cells={enemy["id"]: (enemy["x"], enemy["y"]) for enemy in in_sight},
                kills_cost=observation["military"]["kills_cost"],
                orders=decision.action["orders"] if isinstance(decision, WithReasoning) else [],
            )
        )
        return decision

    def on_action_result(self, result: dict, context: Any) -> None:
        self.dispatcher.on_action_result(result, context)


@dataclass
class PlayedMatch:
    """Everything one whole fake match left: the game, the run, the agent, the cycles, the record and its metrics."""

    faction: str
    game: FakeRedAlert
    run: FakeRun
    agent: Any
    cycles: list[Cycle]
    events: list[dict[str, Any]]
    # The scenario object, which knows the ids of the attackers, the raid kills and the unit that vanished.
    scenario: Any = None
    metrics: dict[str, Any] | None = None

    def data_of(self, kind: str) -> list[dict[str, Any]]:
        return [event["data"] for event in self.events if event["kind"] == kind]


@contextmanager
def match_process(root: Path) -> Iterator[None]:
    """The process state of one match played in `root`: a fresh record, the runtime unhooked afterwards.

    The test session's own fixtures are made for one test function each; a
    match is shared by the tests of its module, so it sets up the same state
    here and undoes it all afterwards.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(paths.ROOT_ENV, str(root))
        patch.setenv(records.STRICT_EVENT_KINDS_ENV, "1")
        for name, value in SECRET_ENV_FOR_TESTS.items():
            patch.setenv(name, value)
        patch.setattr(records, "_process", records._ProcessRecord())
        patch.setattr(records, "MAX_QUEUED_EVENTS", NO_QUEUE_BOUND)
        patch.setattr(records, "CLOSE_TIMEOUT_SECONDS", CLOSE_WAIT_SECONDS)
        patch.setattr(runner, "_terminal_game_state", runner._terminal_game_state)
        patch.setattr(secrets, "_redaction", None)
        gc.collect()
        gc.freeze()
        try:
            yield
        finally:
            gc.unfreeze()


def project_root(tmp_path_factory: pytest.TempPathFactory, name: str) -> Path:
    """A temporary project root holding a copy of the tracked `config/`, its tuning file with the shipped switch set
    off and its agent settings with the model switches off."""
    root = tmp_path_factory.mktemp(name)
    shutil.copytree(REPO_ROOT / "config", root / "config")
    write_switches_off(root / TUNING_FILE)
    write_model_switches_off(root / AGENT_CONFIG_FILE)
    return root


def play(
    root: Path,
    new_game: Callable[[Callable[[], Any]], FakeRedAlert],
    faction: str,
    steps: int,
    observe: Callable[[Dispatcher], Observer] = Observer,
) -> PlayedMatch:
    """Play a whole fake match with the dispatcher `create_agent()` returns, noted by the observer `observe` makes of
    it, and read back its record."""
    with match_process(root):
        observer = observe(create_agent())
        game = new_game(lambda: observer.dispatcher.decision_maker)
        run = game.run(observer, steps)
    (events_file,) = root.glob("runs/*/*/events.jsonl")
    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines()]
    return PlayedMatch(faction, game, run, observer.dispatcher.decision_maker, observer.cycles, events)


def raid_of(decision_maker: Callable[[], Any]) -> Callable[[], list[int]]:
    """The units of the agent's raid group at the moment of asking; none before the agent was made."""

    def members() -> list[int]:
        agent = decision_maker()
        return [] if agent is None else agent.ctx.groups.members(RAID)

    return members


@pytest.fixture(scope="module", params=FAMILIES)
def standard_match(request, tmp_path_factory, load_script) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario for a family's start, and the report's metrics for it."""
    root = project_root(tmp_path_factory, f"standard-{request.param}")
    scenarios: list[StandardScenario] = []

    def new_game(decision_maker: Callable[[], Any]) -> FakeRedAlert:
        scenarios.append(StandardScenario(raid_of(decision_maker)))
        return scenarios[0].new_game(request.param)

    played = play(root, new_game, request.param, standard.STEPS)
    played.scenario = scenarios[0]
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(paths.ROOT_ENV, str(root))
        (folder,) = root.glob("runs/*/*__*")
        assert load_script("report.py").main([str(folder)]) == 0
    played.metrics = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))
    return played


# -- the standard scenario, for each family ----------------------------------------------------------------


def test_1_no_exception_escapes_in_6000_steps_and_no_error_is_unhandled(standard_match):
    errors = standard_match.data_of("error")

    assert len(standard_match.cycles) == standard.STEPS and standard_match.run.final_state.is_terminal
    assert [error for error in errors if error["type"] == "unhandled"] == []


def commands_of(order: dict[str, Any]) -> int:
    """The engine commands an order expands to, at most: one per unit, one per item trained, else one."""
    if order["cmd"] in UNIT_COMMANDS:
        return len(order["units"])
    return order.get("count", 1)


def test_2_no_batch_is_refused_and_every_batch_is_within_20_orders_50_units_and_200_commands(standard_match):
    batches = [batch["orders"] for batch in standard_match.run.batches]

    assert standard_match.run.invalid_actions == 0 and standard_match.game.invalid_actions == 0
    assert batches and all(1 <= len(orders) <= MAX_ORDERS for orders in batches)
    assert all(len(order.get("units", ())) <= MAX_UNITS for orders in batches for order in orders)
    assert max(sum(commands_of(order) for order in orders) for orders in batches) <= MAX_COMMANDS


def test_3_no_production_order_is_sent_twice(standard_match):
    assert standard_match.agent.ledger.duplicate_production_orders == 0
    assert standard_match.metrics["duplicate_production_orders"] == 0


def test_4_low_power_lasts_under_20_s_and_a_finished_building_waits_under_10_s(standard_match):
    assert standard_match.metrics["low_power_seconds"] < MAX_LOW_POWER_SECONDS
    assert standard_match.metrics["ready_unplaced_seconds"] < MAX_READY_UNPLACED_SECONDS


@pytest.mark.parametrize("attack", [0, 1], ids=["eight rifles at 90 s", "four tanks at 300 s"])
def test_5_each_attack_is_answered_by_an_emergency_order_within_2_steps_of_an_attacker_first_seen(
    standard_match, attack
):
    attackers = set(standard_match.scenario.attacks[attack].ids)
    first_seen = next(cycle.tick for cycle in standard_match.cycles if cycle.enemy_units_in_sight & attackers)

    answers = [
        entry
        for entry in standard_match.agent.ledger.entries
        if entry.priority == Priority.EMERGENCY and first_seen <= entry.sent_tick <= first_seen + ANSWER_WITHIN_TICKS
    ]

    assert len(attackers) > 0 and answers


def r6_held(change: dict[str, Any]) -> bool:
    """Whether R6 held on the figures a plan_change event logged, under the plan rules the whole matches play."""
    figures = {key: value for key, value in change["inputs"].items() if key != "threat_level"}
    return plans._finishes(plans.Situation(**figures), tuning_off().plan_rules)


def test_6_the_plans_start_from_opening_raid_before_480_s_and_the_last_180_s_preserve_unless_r6_finishes(
    standard_match,
):
    changes = standard_match.data_of("plan_change")
    final_tick = (standard.MATCH_SECONDS - FINAL_SECONDS) * TICKS_PER_SECOND
    final_cycles = [cycle for cycle in standard_match.cycles if cycle.tick >= final_tick]
    final_plans = {cycle.plan for cycle in final_cycles}
    # The changes that set the final cycles' plans: the last one before the last 180 s, and those within them.
    in_force = [change for change in changes if change["tick"] < final_tick][-1:]
    in_force += [change for change in changes if change["tick"] >= final_tick]
    finishes = [change for change in in_force if change["new"] == "FINISH"]

    assert (changes[0]["old"], changes[0]["new"], changes[0]["rule"]) == (None, "OPENING", R1)
    assert any(change["new"] == "RAID" and change["game_seconds"] < RAID_BY_SECONDS for change in changes)
    assert len(final_cycles) == FINAL_SECONDS * TICKS_PER_SECOND // TICKS_PER_STEP
    assert final_plans <= {"PRESERVE", "FINISH"} and bool(finishes) == ("FINISH" in final_plans)
    # finish_over_preserve on: every FINISH of the last 180 s was set by R6 on figures R6 held on.
    assert [(change["rule"], r6_held(change)) for change in finishes] == [(R6, True)] * len(finishes)


def cells_ordered(order: dict[str, Any], cycle: Cycle) -> list[tuple[int, int]]:
    """The cells an order sends its units to: its `to`, or where the enemy it targets stands."""
    cells = [tuple(order["to"])] if "to" in order else []
    if "target" in order:
        cells.append(cycle.enemy_cells[order["target"]])
    return cells


def test_7_no_raid_unit_is_ordered_near_the_pillbox_and_a_raid_kill_comes_by_480_s(standard_match):
    near_the_pillbox = [
        (cycle.tick, order)
        for cycle in standard_match.cycles
        for order in cycle.orders
        if cycle.raid & set(order.get("units", ()))
        for cell in cells_ordered(order, cycle)
        if math.dist(cell, standard.DEFENSE_CELL) <= DEFENSE_KEEP_OUT_CELLS
    ]
    raid_orders = [
        order for cycle in standard_match.cycles for order in cycle.orders if cycle.raid & set(order.get("units", ()))
    ]
    kills_by_then = max(
        cycle.kills_cost for cycle in standard_match.cycles if cycle.tick <= KILLS_BY_SECONDS * TICKS_PER_SECOND
    )

    assert raid_orders and near_the_pillbox == []
    assert kills_by_then >= KILLS_AT_LEAST


def test_8_the_record_holds_every_kind_of_event_without_a_gap_and_the_report_gives_a_win(standard_match):
    events = standard_match.events

    assert RECORD_KINDS <= {event["kind"] for event in events}
    assert [event["seq"] for event in events] == list(range(len(events)))
    assert events[-1]["kind"] == "result" and events[-1]["data"]["dropped_events"] == 0
    assert standard_match.metrics["outcome"] == "win"


def test_9_compute_ms_keeps_the_compute_bound(standard_match):
    compute_ms = [action["compute_ms"] for action in standard_match.data_of("action")]

    assert len(compute_ms) == standard.STEPS
    assert compute_bound_kept(compute_ms) == WITHIN_BUDGET


# -- the standard scenario with the tuning file as it ships ----------------------------------------------------


@pytest.fixture(scope="module", params=FAMILIES)
def shipped_match(request, tmp_path_factory) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario for a family's start, with a copy of the tracked `config/`
    as it ships, the shipped switch set on, but for the model switches, off."""
    faction = request.param
    root = tmp_path_factory.mktemp(f"shipped-{faction}")
    shutil.copytree(REPO_ROOT / "config", root / "config")
    write_model_switches_off(root / AGENT_CONFIG_FILE)
    scenarios: list[StandardScenario] = []

    def new_game(decision_maker: Callable[[], Any]) -> FakeRedAlert:
        scenarios.append(StandardScenario(raid_of(decision_maker)))
        return scenarios[0].new_game(faction)

    played = play(root, new_game, faction, standard.STEPS)
    played.scenario = scenarios[0]
    return played


def test_shipped_a_whole_match_with_the_shipped_tuning_runs_without_an_unhandled_error_or_a_refused_batch(
    shipped_match,
):
    errors = shipped_match.data_of("error")

    assert shipped_match.agent.ctx.tuning == Tuning()
    assert len(shipped_match.cycles) == standard.STEPS and shipped_match.run.final_state.is_terminal
    assert [error for error in errors if error["type"] == "unhandled"] == []
    assert shipped_match.run.invalid_actions == 0 and shipped_match.game.invalid_actions == 0


def test_shipped_compute_ms_keeps_the_compute_bound_with_the_shipped_tuning(
    shipped_match,
):
    compute_ms = [action["compute_ms"] for action in shipped_match.data_of("action")]

    assert len(compute_ms) == standard.STEPS
    assert compute_bound_kept(compute_ms) == WITHIN_BUDGET


def test_shipped_soviet_infantry_counter_no_slot_is_held_by_a_flak_truck_but_the_opening_s_and_a_kennel_is_built(
    shipped_match,
):
    taken = [data for data in shipped_match.data_of("scout_slot") if data["kind"] in SLOT_TAKEN]
    held = {data["unit_type"] for data in taken}
    is_soviet = shipped_match.agent.world.family == SOVIET
    # Under factory_first the Soviet opening's scout vehicle is the one flak truck to hold a slot.
    opening_flak = int(is_soviet and shipped_match.agent.ctx.tuning.opening.order == FACTORY_FIRST)

    assert held and sum(data["unit_type"] == "ftrk" for data in taken) == opening_flak
    if is_soviet:
        assert held <= {"e1", "dog", "ftrk"} and "kenn" in items_ordered(shipped_match, "build")


# The army build of the tank rows, by count: per 12 tanks, 4 artillery or V2s and 8 rocket soldiers, and how far a
# whole match's counts may stray from it.
BUILD_TANKS = 12
BUILD_PER_12_TANKS = {"support": 4, "rockets": 8}
BUILD_TOLERANCE = 0.25
# Each family's tanks and artillery of the build.
BUILD_UNITS = {"england": (("2tnk",), "arty"), "russia": (("3tnk", "4tnk"), "v2rl")}
POWER_PLANTS = frozenset({"powr", "apwr"})


def confirmed(played: PlayedMatch, command: str) -> list[Any]:
    """The ledger entries of a command (`build`, `train`, `place`) the game confirmed, in the order they were sent."""
    entries = [entry for entry in played.agent.ledger.entries if entry.order["cmd"] == command]
    return sorted((entry for entry in entries if entry.status == CONFIRMED), key=lambda entry: entry.sent_tick)


def first_placed_at(played: PlayedMatch, item: str) -> int:
    """The tick the first building of a type was placed at."""
    return min(entry.sent_tick for entry in confirmed(played, "place") if entry.order["item"] == item)


def build_trained(played: PlayedMatch) -> Counter[str]:
    """The units trained from the service depot's placing to the last `clock_seconds` of the match: the army build's
    span, before the clock rules play the end (PRESERVE trains infantry alone, and the end-of-match spending takes the
    money left on whatever fights)."""
    start = first_placed_at(played, "fix")
    end = played.cycles[-1].tick - Tuning().plan_rules.clock_seconds * TICKS_PER_SECOND
    trained: Counter[str] = Counter()
    for entry in confirmed(played, "train"):
        if start <= entry.sent_tick < end:
            trained[entry.order["item"]] += entry.order.get("count", 1)
    return trained


def test_shipped_army_build_the_depot_is_the_first_building_after_the_war_factory_but_power_plants_and_a_step(
    shipped_match,
):
    war_factory_at = first_placed_at(shipped_match, "weap")
    items = shipped_match.agent.world.items
    after = [
        entry.order["item"]
        for entry in confirmed(shipped_match, "build")
        if entry.sent_tick > war_factory_at and items[entry.order["item"]].queue == "Building"
    ]
    # The factory-first build makes its second refinery between the war factory and the depot.
    expected = ["proc", "fix"] if shipped_match.agent.ctx.tuning.opening.order == FACTORY_FIRST else ["fix"]

    assert [item for item in after if item not in POWER_PLANTS][: len(expected)] == expected


def test_shipped_army_build_tanks_artillery_or_v2s_and_rockets_are_trained_12_4_8_within_25_percent(shipped_match):
    trained = build_trained(shipped_match)
    tank_types, support = BUILD_UNITS[shipped_match.faction]
    tanks = sum(trained[tank] for tank in tank_types)
    per_12_tanks = {"support": trained[support] * BUILD_TANKS / tanks, "rockets": trained["e3"] * BUILD_TANKS / tanks}

    assert tanks >= BUILD_TANKS
    for unit, wanted in BUILD_PER_12_TANKS.items():
        assert abs(per_12_tanks[unit] - wanted) <= BUILD_TOLERANCE * wanted, (unit, dict(trained))
    # No rifle once the war factory stands, as no_army_infantry ships.
    assert trained["e1"] == 0


# The kinds of scout_slot event of a unit taking a slot.
SLOT_TAKEN = frozenset({"assigned", "upgraded", "replaced"})
# The most destinations one scout may be given in any 60 s of a match: more is a scout turned back and forth.
SCOUT_SWITCHES_PER_MINUTE_MAX = 10


def test_shipped_scout_jitter_no_scout_has_more_than_10_destination_switches_in_any_60_s(shipped_match):
    switches: dict[int, list[int]] = {}
    for tick, data in events_at(shipped_match, "scout_slot"):
        if data["kind"] == "destination":
            switches.setdefault(data["unit_id"], []).append(tick)
    window = 60 * TICKS_PER_SECOND

    busiest = max(
        sum(1 for later in ticks if tick <= later < tick + window) for ticks in switches.values() for tick in ticks
    )

    assert busiest <= SCOUT_SWITCHES_PER_MINUTE_MAX


# -- the standard scenario with the focus fire in contact as it ships, and switched off ---------------------------

# The focus fire in contact as the tracked tuning file ships it, and switched off.
CONTACT_FOCUS_SHIPPED, CONTACT_FOCUS_OFF = "contact_focus = true\n", "contact_focus = false\n"
# The fake map's one ore field has 9 cells, room for one harvester by the shipped harvester_cells_per, which keeps the
# economy at the two free harvesters and leaves main too few units to show the focus; the room is off in these matches.
ROOM_SHIPPED, ROOM_OFF = "harvester_cells_per = 10\n", "harvester_cells_per = 0\n"


@dataclass
class ContactCount:
    """main's unit-cycles in contact with an enemy in sight over a match, and those of them under a targeted attack."""

    in_contact: int = 0
    targeted: int = 0

    @property
    def share(self) -> float:
        return self.targeted / self.in_contact


class ContactObserver(Observer):
    """An `Observer` that counts, after each cycle, main's units in contact with an enemy in sight
    (`targeting.is_in_contact`) and those of them whose last order sent is an attack on an enemy still in sight, or a
    charge onto an enemy artillery piece or V2 in sight (`targeting.is_charge`), which engages it as an attack does."""

    def __init__(self, dispatcher: Dispatcher, count: ContactCount) -> None:
        super().__init__(dispatcher)
        self.count = count
        # Unit id -> the last order sent to it.
        self._last_order: dict[int, dict[str, Any]] = {}

    def choose_action(self, state: Any, context: Any) -> Any:
        decision = super().choose_action(state, context)
        for order in self.cycles[-1].orders:
            self._last_order.update(dict.fromkeys(order.get("units", ()), order))
        agent = self.dispatcher.decision_maker
        world = agent.world
        enemies = targeting.enemies_in_sight(world)
        for unit_id in agent.ctx.groups.members(MAIN) if enemies else ():
            unit = world.combat_units.get(unit_id)
            if unit is not None and targeting.is_in_contact(unit, enemies, world):
                self.count.in_contact += 1
                last = self._last_order.get(unit_id)
                self.count.targeted += is_attack_in_sight(last, world) or (
                    last is not None and targeting.is_charge(last, world)
                )
        return decision


def is_attack_in_sight(order: dict[str, Any] | None, world: Any) -> bool:
    """Whether an order is an attack on a named enemy that is in sight."""
    if order is None or order["cmd"] != "attack" or "target" not in order:
        return False
    record = world.enemy_memory.get(order["target"])
    return record is not None and record.visible


@dataclass
class ContactMatch:
    """A whole match on the standard scenario and the count of main's unit-cycles in contact over it."""

    played: PlayedMatch
    count: ContactCount


def contact_match(tmp_path_factory: pytest.TempPathFactory, faction: str, contact_focus: str) -> ContactMatch:
    """A whole match for a faction's start with a copy of the tracked `config/` as it ships but for the focus fire in
    contact's line, `contact_focus`, and the harvester room and the model switches, off, counting main's unit-cycles in
    contact."""
    root = tmp_path_factory.mktemp(f"contact-focus-{faction}")
    shutil.copytree(REPO_ROOT / "config", root / "config")
    write_model_switches_off(root / AGENT_CONFIG_FILE)
    tuning_file = root / TUNING_FILE
    text = tuning_file.read_text(encoding="utf-8")
    assert text.count(CONTACT_FOCUS_SHIPPED) == 1 and text.count(ROOM_SHIPPED) == 1
    text = text.replace(ROOM_SHIPPED, ROOM_OFF)
    tuning_file.write_text(text.replace(CONTACT_FOCUS_SHIPPED, contact_focus), encoding="utf-8")
    count = ContactCount()

    def new_game(decision_maker: Callable[[], Any]) -> FakeRedAlert:
        return StandardScenario(raid_of(decision_maker)).new_game(faction)

    played = play(root, new_game, faction, standard.STEPS, lambda dispatcher: ContactObserver(dispatcher, count))
    return ContactMatch(played, count)


@pytest.fixture(scope="module", params=FAMILIES)
def contact_focus_matches(request, tmp_path_factory) -> tuple[ContactMatch, ContactMatch]:
    """The standard match for a family's start with the tracked config as it ships, the focus fire in contact on, and
    the same match with it off."""
    return (
        contact_match(tmp_path_factory, request.param, CONTACT_FOCUS_SHIPPED),
        contact_match(tmp_path_factory, request.param, CONTACT_FOCUS_OFF),
    )


def test_contact_focus_a_whole_match_with_the_shipped_tuning_runs_without_an_unhandled_error_within_the_budget(
    contact_focus_matches,
):
    shipped, _ = contact_focus_matches

    assert shipped.played.agent.ctx.tuning.combat.contact_focus is True
    assert_whole_within_budget(shipped.played)


def test_contact_focus_more_of_main_s_unit_cycles_in_contact_have_a_targeted_attack_than_with_it_off(
    contact_focus_matches,
):
    shipped, off = contact_focus_matches

    assert off.played.agent.ctx.tuning.combat.contact_focus is False
    assert shipped.count.in_contact > 0 and off.count.in_contact > 0
    assert shipped.count.share > off.count.share


# -- the standard scenario with harvester parking switched on ---------------------------------------------------


@pytest.fixture(scope="module")
def parked_match(tmp_path_factory) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario for the Allied start, with the copy of the tracked tuning
    file switching harvester parking on."""
    root = project_root(tmp_path_factory, "parked")
    tuning_file = root / TUNING_FILE
    tuning_file.write_text(
        tuning_file.read_text(encoding="utf-8").replace(PARKING_SHIPPED, PARKING_ON), encoding="utf-8"
    )
    scenarios: list[StandardScenario] = []

    def new_game(decision_maker: Callable[[], Any]) -> FakeRedAlert:
        scenarios.append(StandardScenario(raid_of(decision_maker)))
        return scenarios[0].new_game(PARKED_FACTION)

    played = play(root, new_game, PARKED_FACTION, standard.STEPS)
    played.scenario = scenarios[0]
    return played


def test_park_a_whole_match_with_parking_on_runs_without_an_unhandled_error_or_a_refused_batch(parked_match):
    errors = parked_match.data_of("error")

    assert parked_match.agent.ctx.tuning.upkeep.park_harvesters
    assert len(parked_match.cycles) == standard.STEPS and parked_match.run.final_state.is_terminal
    assert [error for error in errors if error["type"] == "unhandled"] == []
    assert parked_match.run.invalid_actions == 0 and parked_match.game.invalid_actions == 0


def test_park_the_harvesters_park_once_in_the_last_120_s_and_upkeep_tells_none_to_harvest_after(parked_match):
    (parked,) = parked_match.data_of("harvesters_parked")
    after = [entry for entry in parked_match.agent.ledger.entries if entry.sent_tick >= parked["tick"]]
    moves = [entry for entry in after if entry.order["cmd"] == "move" and entry.order["to"] == parked["cell"]]
    upkeep_harvests = [
        entry for entry in after if entry.order["cmd"] == "harvest" and entry.priority > Priority.EMERGENCY
    ]
    harvesters = [unit for unit in parked_match.agent.world.units.values() if unit.type == HARVESTER]

    assert parked["reason"] == "time_left" and parked["remaining_seconds"] <= PARK_SECONDS_LEFT
    assert moves and {entry.priority for entry in moves} == {Priority.PRODUCTION} and upkeep_harvests == []
    assert harvesters and all(math.dist((unit.x, unit.y), parked["cell"]) <= PARKED_WITHIN_CELLS for unit in harvesters)


def test_park_compute_ms_keeps_the_compute_bound_with_parking_on(parked_match):
    compute_ms = [action["compute_ms"] for action in parked_match.data_of("action")]

    assert len(compute_ms) == standard.STEPS
    assert compute_bound_kept(compute_ms) == WITHIN_BUDGET


# -- the standard scenario with the creep toward ore switched on ------------------------------------------------


@pytest.fixture(scope="module")
def creep_match(standard_match, tmp_path_factory) -> PlayedMatch:
    """The standard match of the family `standard_match` was played for, played again with the copy of the tracked
    tuning file switching the creep toward ore on."""
    faction = standard_match.faction
    root = project_root(tmp_path_factory, f"creep-{faction}")
    tuning_file = root / TUNING_FILE
    shipped = tuning_file.read_text(encoding="utf-8")
    assert shipped.count(CREEP_SHIPPED) == 1
    tuning_file.write_text(shipped.replace(CREEP_SHIPPED, CREEP_ON), encoding="utf-8")
    scenarios: list[StandardScenario] = []

    def new_game(decision_maker: Callable[[], Any]) -> FakeRedAlert:
        scenarios.append(StandardScenario(raid_of(decision_maker)))
        return scenarios[0].new_game(faction)

    played = play(root, new_game, faction, standard.STEPS)
    played.scenario = scenarios[0]
    return played


def refinery_ore_distances(played: PlayedMatch) -> list[int]:
    """How many cells, counted as a king moves, each refinery standing at the end of a match lies from the nearest
    ore cell of the map."""
    patches = played.agent.ctx.terrain.ore_patches
    boxes = [
        placement.box_at(REFINERY, (building["x"], building["y"]))
        for building in played.game.own_buildings()
        if building["type"] == REFINERY
    ]
    return [min(placement.ore_distance(box, patch) for patch in patches) for box in boxes]


def test_creep_a_whole_match_with_creep_on_runs_without_an_unhandled_error_or_a_refused_batch(creep_match):
    errors = creep_match.data_of("error")

    assert creep_match.agent.ctx.tuning.placement.creep_toward_ore
    assert len(creep_match.cycles) == standard.STEPS and creep_match.run.final_state.is_terminal
    assert [error for error in errors if error["type"] == "unhandled"] == []
    assert creep_match.run.invalid_actions == 0 and creep_match.game.invalid_actions == 0


def test_creep_compute_ms_keeps_the_compute_bound_with_creep_on(creep_match):
    compute_ms = [action["compute_ms"] for action in creep_match.data_of("action")]

    assert len(compute_ms) == standard.STEPS
    assert compute_bound_kept(compute_ms) == WITHIN_BUDGET


def test_creep_the_median_refinery_stands_no_farther_from_its_ore_than_with_creep_off(creep_match, standard_match):
    with_creep, without = refinery_ore_distances(creep_match), refinery_ore_distances(standard_match)

    assert len(with_creep) >= 2 and len(without) >= 2
    assert statistics.median(with_creep) <= statistics.median(without)


# -- the standard scenario with the creep to the next field along the lane, on a map with a field in the lane --------


def with_lane_ore(game: FakeRedAlert) -> FakeRedAlert:
    """The game with a field of ore in the lane toward the enemy pictured on its map, where the agent's terrain reads
    it; the fake's own income does not depend on where the ore lies."""
    layout = game.game_config["rules"]["maps"][MAP_ID]["layout"]
    rows = [list(row) for row in layout["rows"]]
    for x, y in LANE_ORE:
        rows[y][x] = "$"
    layout["rows"] = ["".join(row) for row in rows]
    return game


@pytest.fixture(scope="module")
def next_patch_match(tmp_path_factory) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario, on a map with a field in the lane, with the copy of the
    tracked tuning file switching the creep to the next field on."""
    return switched_match(
        tmp_path_factory, "next-patch", NEXT_PATCH_FACTION, NEXT_PATCH_SHIPPED, NEXT_PATCH_ON, with_lane_ore
    )


def test_next_patch_a_whole_match_with_the_creep_to_the_next_field_runs_without_an_unhandled_error(next_patch_match):
    errors = next_patch_match.data_of("error")

    assert next_patch_match.agent.ctx.tuning.placement.creep_next_patch
    assert len(next_patch_match.cycles) == standard.STEPS and next_patch_match.run.final_state.is_terminal
    assert [error for error in errors if error["type"] == "unhandled"] == []
    assert next_patch_match.run.invalid_actions == 0 and next_patch_match.game.invalid_actions == 0


def test_next_patch_compute_ms_keeps_the_compute_bound(next_patch_match):
    compute_ms = [action["compute_ms"] for action in next_patch_match.data_of("action")]

    assert len(compute_ms) == standard.STEPS
    assert compute_bound_kept(compute_ms) == WITHIN_BUDGET


def test_next_patch_the_refineries_stand_on_at_least_2_fields(next_patch_match):
    terrain = next_patch_match.agent.ctx.terrain
    refineries = [building for building in next_patch_match.agent.world.buildings.values() if building.type == REFINERY]

    assert len({placement.patch_of(refinery, terrain) for refinery in refineries}) >= 2


# -- the standard scenario with the main force built around tanks --------------------------------------------------


@pytest.fixture(scope="module", params=FAMILIES)
def tanks_match(request, tmp_path_factory) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario for a family's start, with the copy of the tracked tuning
    file building the main force around tanks."""
    faction = request.param
    root = project_root(tmp_path_factory, f"tanks-{faction}")
    tuning_file = root / TUNING_FILE
    shipped = tuning_file.read_text(encoding="utf-8")
    assert shipped.count(MAIN_FORCE_MIXED) == 1
    tuning_file.write_text(shipped.replace(MAIN_FORCE_MIXED, MAIN_FORCE_TANKS), encoding="utf-8")
    scenarios: list[StandardScenario] = []

    def new_game(decision_maker: Callable[[], Any]) -> FakeRedAlert:
        scenarios.append(StandardScenario(raid_of(decision_maker)))
        return scenarios[0].new_game(faction)

    played = play(root, new_game, faction, standard.STEPS)
    played.scenario = scenarios[0]
    return played


def test_tanks_a_whole_match_with_tanks_runs_without_an_unhandled_error_or_a_refused_batch(tanks_match):
    errors = tanks_match.data_of("error")

    assert tanks_match.agent.ctx.tuning.mix.main_force == "tanks"
    assert len(tanks_match.cycles) == standard.STEPS and tanks_match.run.final_state.is_terminal
    assert [error for error in errors if error["type"] == "unhandled"] == []
    assert tanks_match.run.invalid_actions == 0 and tanks_match.game.invalid_actions == 0


def test_tanks_compute_ms_keeps_the_compute_bound_with_tanks(tanks_match):
    compute_ms = [action["compute_ms"] for action in tanks_match.data_of("action")]

    assert len(compute_ms) == standard.STEPS
    assert compute_bound_kept(compute_ms) == WITHIN_BUDGET


def depot_placed_at(played: PlayedMatch) -> int:
    """The tick the service depot was placed at: the first place order for it that the game confirmed."""
    return min(
        entry.sent_tick
        for entry in played.agent.ledger.entries
        if entry.order["cmd"] == "place" and entry.order["item"] == SERVICE_DEPOT and entry.status == CONFIRMED
    )


def test_tanks_the_main_force_trains_its_heavier_tank_once_the_rushed_service_depot_stands(tanks_match):
    # The unit queues save for the depot rushed after the war factory, so the base row's light tanks have little time.
    depot_at = depot_placed_at(tanks_match)
    heavy_ticks = train_ticks(tanks_match, HEAVIER_TANK[tanks_match.faction])

    assert heavy_ticks and min(heavy_ticks) > depot_at


# -- the standard scenario with a standing raid group -------------------------------------------------------------


def switched_match(
    tmp_path_factory,
    name: str,
    faction: str,
    shipped: str,
    switched: str,
    change_game: Callable[[FakeRedAlert], FakeRedAlert] = lambda game: game,
) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario for a faction's start, with the copy of the tracked tuning
    file holding `switched` in place of the `shipped` text, which it holds once; the game as `change_game` makes it
    of the scenario's."""
    root = project_root(tmp_path_factory, name)
    tuning_file = root / TUNING_FILE
    text = tuning_file.read_text(encoding="utf-8")
    assert text.count(shipped) == 1
    tuning_file.write_text(text.replace(shipped, switched), encoding="utf-8")
    scenarios: list[StandardScenario] = []

    def new_game(decision_maker: Callable[[], Any]) -> FakeRedAlert:
        scenarios.append(StandardScenario(raid_of(decision_maker)))
        return change_game(scenarios[0].new_game(faction))

    played = play(root, new_game, faction, standard.STEPS)
    played.scenario = scenarios[0]
    return played


@pytest.fixture(scope="module", params=FAMILIES)
def standing_match(request, tmp_path_factory) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario for a family's start, with the copy of the tracked tuning
    file switching the standing raid group on."""
    faction = request.param
    return switched_match(tmp_path_factory, f"standing-{faction}", faction, STANDING_SHIPPED, STANDING_ON)


def test_standing_a_whole_match_with_a_standing_raid_group_runs_without_an_unhandled_error_or_a_refused_batch(
    standing_match,
):
    errors = standing_match.data_of("error")

    assert standing_match.agent.ctx.tuning.raids.standing
    assert len(standing_match.cycles) == standard.STEPS and standing_match.run.final_state.is_terminal
    assert [error for error in errors if error["type"] == "unhandled"] == []
    assert standing_match.run.invalid_actions == 0 and standing_match.game.invalid_actions == 0


def test_standing_compute_ms_keeps_the_compute_bound_with_a_standing_raid_group(
    standing_match,
):
    compute_ms = [action["compute_ms"] for action in standing_match.data_of("action")]

    assert len(compute_ms) == standard.STEPS
    assert compute_bound_kept(compute_ms) == WITHIN_BUDGET


def test_standing_a_raid_is_out_under_a_plan_only_a_standing_raid_group_raids_under(standing_match):
    raids = standing_match.data_of("raid")
    starts = [raid["tick"] for raid in raids if raid["phase"] == "start"]
    ends = [raid["tick"] for raid in raids if raid["phase"] == "end"]
    # A raid is out from the cycle it starts in to the one it ends in, which for a raid that is not standing is the
    # first cycle under a plan it does not raid under; the last raid may still be out when the match ends.
    spans = list(zip(starts, [*ends, math.inf]))
    plans_out = {
        cycle.plan for cycle in standing_match.cycles if any(start <= cycle.tick < end for start, end in spans)
    }

    assert plans_out & ONLY_STANDING_PLANS


# -- the standard scenario with the Soviet raid of rockets carried by APCs -----------------------------------------


@pytest.fixture(scope="module")
def apc_match(tmp_path_factory) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario for the Soviet start, with the copy of the tracked tuning
    file switching the Soviet raid to rockets carried by APCs."""
    return switched_match(tmp_path_factory, "apc-russia", APC_FACTION, SOVIET_RAID_SHIPPED, SOVIET_RAID_APCS)


def test_apc_a_whole_soviet_match_with_the_apc_raid_runs_without_an_unhandled_error_or_a_refused_batch(apc_match):
    errors = apc_match.data_of("error")

    assert apc_match.agent.ctx.tuning.composition.soviet_raid == "apc_rockets"
    assert len(apc_match.cycles) == standard.STEPS and apc_match.run.final_state.is_terminal
    assert [error for error in errors if error["type"] == "unhandled"] == []
    assert apc_match.run.invalid_actions == 0 and apc_match.game.invalid_actions == 0


def test_apc_compute_ms_keeps_the_compute_bound_with_the_apc_raid(apc_match):
    compute_ms = [action["compute_ms"] for action in apc_match.data_of("action")]

    assert len(compute_ms) == standard.STEPS
    assert compute_bound_kept(compute_ms) == WITHIN_BUDGET


def raid_unloads(played: PlayedMatch) -> list[tuple[int, dict[str, Any]]]:
    """Every `unload` order sent to units of the raid group, with the tick it was sent at."""
    return [
        (cycle.tick, order)
        for cycle in played.cycles
        for order in cycle.orders
        if order["cmd"] == "unload" and cycle.raid & set(order["units"])
    ]


def test_apc_the_match_records_an_apc_raid_whose_apcs_unload(apc_match):
    starts = [raid for raid in apc_match.data_of("raid") if raid["phase"] == "start"]
    unloads = raid_unloads(apc_match)

    assert starts and starts[0]["apcs"] == 2 and starts[0]["rockets"] == 10
    assert unloads and unloads[0][0] > starts[0]["tick"]


# -- the standard scenario with RAID open only while a raid could set out -------------------------------------------


@pytest.fixture(scope="module", params=FAMILIES)
def launchable_match(request, tmp_path_factory) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario for a family's start, with the copy of the tracked tuning
    file switching on RAID only while a raid could set out now."""
    faction = request.param
    return switched_match(tmp_path_factory, f"launchable-{faction}", faction, LAUNCHABLE_OFF, LAUNCHABLE_ON)


def test_launchable_a_whole_match_with_raid_only_when_launchable_runs_without_an_unhandled_error_or_a_refused_batch(
    launchable_match,
):
    errors = launchable_match.data_of("error")

    assert launchable_match.agent.ctx.tuning.raids.launchable_only
    assert len(launchable_match.cycles) == standard.STEPS and launchable_match.run.final_state.is_terminal
    assert [error for error in errors if error["type"] == "unhandled"] == []
    assert launchable_match.run.invalid_actions == 0 and launchable_match.game.invalid_actions == 0


def test_launchable_compute_ms_keeps_the_compute_bound(launchable_match):
    compute_ms = [action["compute_ms"] for action in launchable_match.data_of("action")]

    assert len(compute_ms) == standard.STEPS
    assert compute_bound_kept(compute_ms) == WITHIN_BUDGET


def test_launchable_every_raid_chosen_has_a_raid_out_within_1500_ticks_or_raid_closes(
    launchable_match,
):
    window = launchable_match.agent.ctx.tuning.raids.launch_within_ticks
    changes = launchable_match.data_of("plan_change")
    raids = launchable_match.data_of("raid")
    starts = [raid["tick"] for raid in raids if raid["phase"] == "start"]
    ends = [raid["tick"] for raid in raids if raid["phase"] == "end"]
    # A raid is out from the cycle it starts in to the one it ends in; the last may still be out when the match ends.
    spans = list(zip(starts, [*ends, math.inf]))
    chosen = [change["tick"] for change in changes if change["new"] == "RAID"]
    left = [change["tick"] for change in changes if change["old"] == "RAID"]
    # A RAID is answered by a raid that starts within the window, or one already out when RAID is chosen (sent under
    # PRESS); else by the plan leaving RAID, which it does in the cycle after the one in which the wait ran out.
    unanswered = [
        tick
        for tick in chosen
        if not any(start <= tick + window and end >= tick for start, end in spans)
        and not any(tick < leave <= tick + window + RAID_CLOSES_WITHIN_TICKS for leave in left)
    ]

    assert window == 1500 and chosen and starts
    assert unanswered == []


# -- the standard scenario with the lean opening's infantry ----------------------------------------------------------


@dataclass
class LeanMatch:
    """A whole match with the lean opening's infantry, and every train order its opening manager proposed."""

    played: PlayedMatch
    opening_trains: list[dict[str, Any]]


@pytest.fixture(scope="module", params=FAMILIES)
def lean_match(request, tmp_path_factory) -> LeanMatch:
    """One whole 20-minute match on the standard scenario for a family's start, with the copy of the tracked tuning
    file switching the lean opening's infantry on; the opening manager's proposals are noted as it makes them."""
    faction, trains = request.param, []
    own_intents = OpeningManager.intents

    def noting_trains(manager: OpeningManager, world: Any, ctx: Any) -> list[Any]:
        intents = own_intents(manager, world, ctx)
        trains.extend(intent.order for intent in intents if intent.order["cmd"] == "train")
        return intents

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(OpeningManager, "intents", noting_trains)
        played = switched_match(tmp_path_factory, f"lean-{faction}", faction, LEAN_OFF, LEAN_ON)
    return LeanMatch(played, trains)


def test_lean_a_whole_match_with_the_lean_opening_runs_without_an_unhandled_error_or_a_refused_batch(lean_match):
    played = lean_match.played
    errors = played.data_of("error")

    assert played.agent.ctx.tuning.opening.lean_infantry
    assert len(played.cycles) == standard.STEPS and played.run.final_state.is_terminal
    assert [error for error in errors if error["type"] == "unhandled"] == []
    assert played.run.invalid_actions == 0 and played.game.invalid_actions == 0


def test_lean_compute_ms_keeps_the_compute_bound(lean_match):
    compute_ms = [action["compute_ms"] for action in lean_match.played.data_of("action")]

    assert len(compute_ms) == standard.STEPS
    assert compute_bound_kept(compute_ms) == WITHIN_BUDGET


def test_lean_the_opening_orders_three_rifles_at_most_and_no_other_infantry(lean_match):
    trains = lean_match.opening_trains
    rifles = sum(order["count"] for order in trains if order["item"] == "e1")

    assert trains and {order["item"] for order in trains} == {"e1"}
    assert rifles <= LEAN_OPENING_RIFLES


# -- the standard scenario with the scout sent again on a stale picture and by the indirect way ---------------------


@pytest.fixture(scope="module")
def scouting_match(tmp_path_factory) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario for the Allied start, with the copy of the tracked tuning
    file keeping scouting again on a stale picture on, as shipped, and switching on the indirect way."""
    root = project_root(tmp_path_factory, "scouting")
    tuning_file = root / TUNING_FILE
    text = tuning_file.read_text(encoding="utf-8")
    assert text.count(RESCOUT_SHIPPED) == 1 and text.count(INDIRECT_SHIPPED) == 1
    tuning_file.write_text(text.replace(INDIRECT_SHIPPED, INDIRECT_ON), encoding="utf-8")
    scenarios: list[StandardScenario] = []

    def new_game(decision_maker: Callable[[], Any]) -> FakeRedAlert:
        scenarios.append(StandardScenario(raid_of(decision_maker)))
        return scenarios[0].new_game(SCOUTING_FACTION)

    played = play(root, new_game, SCOUTING_FACTION, standard.STEPS)
    played.scenario = scenarios[0]
    return played


def test_scouting_a_whole_match_with_rescout_and_the_indirect_way_runs_without_an_unhandled_error(scouting_match):
    errors = scouting_match.data_of("error")
    scouting = scouting_match.agent.ctx.tuning.scouting

    assert scouting.rescout and scouting.indirect_route
    assert len(scouting_match.cycles) == standard.STEPS and scouting_match.run.final_state.is_terminal
    assert [error for error in errors if error["type"] == "unhandled"] == []
    assert scouting_match.run.invalid_actions == 0 and scouting_match.game.invalid_actions == 0


def test_scouting_at_least_4_scout_missions_are_sent_in_the_20_minutes(scouting_match):
    events = scouting_match.data_of("scout")
    sent = [event for event in events if event["phase"] == "sent"]

    assert len(sent) >= SCOUT_MISSIONS_AT_LEAST
    assert {event["reason"] for event in events} <= SCOUT_REASONS
    assert {event["phase"] for event in events} >= {"sent", "waypoint", "done"}


def test_scouting_compute_ms_keeps_the_compute_bound_with_rescout(scouting_match):
    compute_ms = [action["compute_ms"] for action in scouting_match.data_of("action")]

    assert len(compute_ms) == standard.STEPS
    assert compute_bound_kept(compute_ms) == WITHIN_BUDGET


# -- the standard scenario with the scout slots ----------------------------------------------------------------------


def events_at(played: PlayedMatch, kind: str) -> list[tuple[int, dict[str, Any]]]:
    """The data of every event of a kind with the tick of the cycle that wrote it: the `state_version` of the cycle's
    `action` event, which is the last event of a cycle."""
    timed: list[tuple[int, dict[str, Any]]] = []
    waiting: list[dict[str, Any]] = []
    for event in played.events:
        if event["kind"] == kind:
            waiting.append(event["data"])
        elif event["kind"] == "action":
            timed += [(event["data"]["state_version"], data) for data in waiting]
            waiting = []
    return timed


@contextmanager
def squares_seen_by_ten_minutes() -> Iterator[set[tuple[int, int]]]:
    """The squares of the sight grid (see `sight`) within sight of one of the agent's units up to 10:00 of the match
    played meanwhile, as its scouting manager's cycles see the units; the fake game says nothing of the map explored."""
    seen: set[tuple[int, int]] = set()
    own_intents = ScoutingManager.intents

    def seeing(manager: ScoutingManager, world: Any, ctx: Any) -> list[Any]:
        if world.tick <= EXPLORED_BY_TICK:
            for unit in world.units.values():
                seen.update(squares_within((unit.x, unit.y), SIGHT_CELLS))
        return own_intents(manager, world, ctx)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ScoutingManager, "intents", seeing)
        yield seen


@pytest.fixture(scope="module", params=FAMILIES)
def slots_match(request, tmp_path_factory) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario for a family's start, with the copy of the tracked tuning
    file switching the scout slots on; the squares its units saw by 10:00 are noted."""
    faction = request.param
    with squares_seen_by_ten_minutes() as seen:
        played = switched_match(tmp_path_factory, f"slots-{faction}", faction, SLOTS_OFF, SLOTS_ON)
    EXPLORED_WITH_SLOTS[faction] = len(seen)
    return played


@pytest.fixture(scope="module", params=FAMILIES)
def explored_without_slots(request, tmp_path_factory) -> tuple[str, int]:
    """A family, and how many squares its units saw by 10:00 of a whole match on the standard scenario as the tracked
    files ship, with the scout slots off."""
    faction = request.param
    scenarios: list[StandardScenario] = []

    def new_game(decision_maker: Callable[[], Any]) -> FakeRedAlert:
        scenarios.append(StandardScenario(raid_of(decision_maker)))
        return scenarios[0].new_game(faction)

    with squares_seen_by_ten_minutes() as seen:
        played = play(project_root(tmp_path_factory, f"no-slots-{faction}"), new_game, faction, standard.STEPS)
    assert not played.agent.ctx.tuning.scouting.scout_slots
    return faction, len(seen)


def test_scout_slots_a_whole_match_runs_without_an_unhandled_error_or_a_refused_batch(slots_match):
    errors = slots_match.data_of("error")

    assert slots_match.agent.ctx.tuning.scouting.scout_slots
    assert len(slots_match.cycles) == standard.STEPS and slots_match.run.final_state.is_terminal
    assert [error for error in errors if error["type"] == "unhandled"] == []
    assert slots_match.run.invalid_actions == 0 and slots_match.game.invalid_actions == 0


def test_scout_slots_compute_ms_keeps_the_compute_bound(slots_match):
    compute_ms = [action["compute_ms"] for action in slots_match.data_of("action")]

    assert len(compute_ms) == standard.STEPS
    assert compute_bound_kept(compute_ms) == WITHIN_BUDGET


def test_scout_slots_both_slots_are_filled_within_the_first_90_s_and_hold_scouts_to_the_end(slots_match):
    assigned = [(tick, data) for tick, data in events_at(slots_match, "scout_slot") if data["kind"] == "assigned"]
    scouts = slots_match.agent.ctx.groups.members(SCOUT)
    slot_count = slots_match.agent.ctx.tuning.scouting.scout_slot_count

    assert sorted(data["slot"] for _, data in assigned) == list(range(1, slot_count + 1))
    assert max(tick for tick, _ in assigned) <= SLOTS_FILLED_BY_SECONDS * TICKS_PER_SECOND
    assert len(scouts) == slot_count


def test_steering_the_scouts_take_one_destination_after_another_and_no_batch_carries_more_than_2_scout_orders(
    slots_match,
):
    events = [data for data in slots_match.data_of("scout_slot") if data["kind"] == "destination"]
    per_batch = Counter(entry.batch for entry in slots_match.agent.ledger.entries if entry.priority == Priority.SCOUT)
    orders_per_batch = slots_match.agent.ctx.tuning.scouting.scout_orders_per_batch

    assert len(events) > 20 and {event["reason"] for event in events} >= {"new", "reached"}
    assert {event["source"] for event in events} == {"script"}
    assert per_batch and max(per_batch.values()) <= orders_per_batch == 2


def test_steering_the_map_seen_by_10_00_is_at_least_that_of_the_same_match_with_the_scout_slots_off(
    slots_match, explored_without_slots
):
    faction, without = explored_without_slots

    assert EXPLORED_WITH_SLOTS[faction] >= without > 0


class StubScoutModel:
    """Stands in for the local model server the scout chain asks: every answer gives a reason, then the second option
    (the first when there is one only), so that the model's choice differs from the script's, which is option 1."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.transport = httpx.MockTransport(self.answer)

    def answer(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        options = body["response_format"]["json_schema"]["schema"]["properties"]["choice"]["enum"]
        content = json.dumps({"reason": "It has gone unseen the longest.", "choice": options[min(1, len(options) - 1)]})
        self.bodies.append(body)
        return httpx.Response(
            200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
        )


@dataclass
class ScoutChoiceMatch:
    """A match with the scout slots and the model's choice of their destinations on, and the stand-in model."""

    played: PlayedMatch
    model: StubScoutModel


@pytest.fixture(scope="module")
def scout_choice_match(tmp_path_factory) -> ScoutChoiceMatch:
    """One whole 20-minute match on the standard scenario for the Allied start, with the copies of the tracked files
    switching the scout slots and the model's choice of their destinations on, the scout chain asking the stand-in
    model."""
    root = project_root(tmp_path_factory, "scout-choice")
    for name, shipped, switched in (
        (AGENT_CONFIG_FILE, SCOUT_CHOICE_SHIPPED, SCOUT_CHOICE_ON),
        (TUNING_FILE, SLOTS_OFF, SLOTS_ON),
    ):
        text = (root / name).read_text(encoding="utf-8")
        assert text.count(shipped) == 1
        (root / name).write_text(text.replace(shipped, switched), encoding="utf-8")
    model = StubScoutModel()
    scenarios: list[StandardScenario] = []

    def new_game(decision_maker: Callable[[], Any]) -> FakeRedAlert:
        scenarios.append(StandardScenario(raid_of(decision_maker)))
        return scenarios[0].new_game(SCOUT_CHOICE_FACTION)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(redalert_agent, "build_chain", lambda name: build_local_chain(name, transport=model.transport))
        played = play(root, new_game, SCOUT_CHOICE_FACTION, standard.STEPS)
    played.scenario = scenarios[0]
    return ScoutChoiceMatch(played, model)


def test_scout_choice_a_whole_match_asking_a_model_where_the_scouts_go_runs_without_an_unhandled_error(
    scout_choice_match,
):
    played = scout_choice_match.played
    errors = played.data_of("error")

    assert len(played.agent.ctx.scout_choosers) == 2 and played.agent.ctx.tuning.scouting.scout_slots
    assert len(played.cycles) == standard.STEPS and played.run.final_state.is_terminal
    assert [error for error in errors if error["type"] == "unhandled"] == []
    assert played.run.invalid_actions == 0 and played.game.invalid_actions == 0


def test_scout_choice_compute_ms_keeps_the_compute_bound(scout_choice_match):
    compute_ms = [action["compute_ms"] for action in scout_choice_match.played.data_of("action")]

    assert len(compute_ms) == standard.STEPS
    assert compute_bound_kept(compute_ms) == WITHIN_BUDGET


def test_scout_choice_every_scout_question_is_recorded_with_its_io_and_the_model_s_picks_are_taken(scout_choice_match):
    played, model = scout_choice_match.played, scout_choice_match.model
    decisions = [data for data in played.data_of("decision") if data["request_summary"].startswith(SCOUT_QUESTION)]
    picked = [
        data for data in played.data_of("scout_slot") if data["kind"] == "destination" and data["source"] == "model"
    ]

    # A question still in flight when the match ends is never handed over, and so never recorded.
    assert decisions and len(model.bodies) - 2 <= len(decisions) <= len(model.bodies)
    assert all(decision["io"]["raw_output"] and decision["io"]["user"] for decision in decisions)
    assert {decision["outcome"] for decision in decisions} <= {"used", "stale_discarded"}
    assert picked and {data["reason"] for data in picked} == {"answered"}


@dataclass
class LeanSlotsMatch:
    """A whole match with the lean opening's infantry and the scout slots, and every train order its opening manager
    proposed."""

    played: PlayedMatch
    opening_trains: list[dict[str, Any]]


@pytest.fixture(scope="module")
def lean_slots_match(tmp_path_factory) -> LeanSlotsMatch:
    """One whole 20-minute match on the standard scenario for the Allied start, with the copy of the tracked tuning
    file switching the lean opening's infantry and the scout slots on; the opening manager's proposals are noted."""
    root = project_root(tmp_path_factory, "lean-slots")
    tuning_file = root / TUNING_FILE
    text = tuning_file.read_text(encoding="utf-8")
    assert text.count(LEAN_OFF) == 1 and text.count(SLOTS_OFF) == 1
    tuning_file.write_text(text.replace(LEAN_OFF, LEAN_ON).replace(SLOTS_OFF, SLOTS_ON), encoding="utf-8")
    trains: list[dict[str, Any]] = []
    own_intents = OpeningManager.intents

    def noting_trains(manager: OpeningManager, world: Any, ctx: Any) -> list[Any]:
        intents = own_intents(manager, world, ctx)
        trains.extend(intent.order for intent in intents if intent.order["cmd"] == "train")
        return intents

    scenarios: list[StandardScenario] = []

    def new_game(decision_maker: Callable[[], Any]) -> FakeRedAlert:
        scenarios.append(StandardScenario(raid_of(decision_maker)))
        return scenarios[0].new_game(LEAN_SLOTS_FACTION)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(OpeningManager, "intents", noting_trains)
        played = play(root, new_game, LEAN_SLOTS_FACTION, standard.STEPS)
    played.scenario = scenarios[0]
    return LeanSlotsMatch(played, trains)


def test_scout_slots_with_the_lean_opening_the_opening_orders_5_rifles_at_most_and_two_rifles_take_the_slots(
    lean_slots_match,
):
    played = lean_slots_match.played
    rifles = sum(order["count"] for order in lean_slots_match.opening_trains if order["item"] == "e1")
    assigned = [data for data in played.data_of("scout_slot") if data["kind"] == "assigned"]

    assert played.agent.ctx.tuning.opening.lean_infantry and played.agent.ctx.tuning.scouting.scout_slots
    assert [error for error in played.data_of("error") if error["type"] == "unhandled"] == []
    assert rifles <= LEAN_SLOTS_OPENING_RIFLES
    assert [data["unit_type"] for data in assigned] == ["e1", "e1"]


def switched_root(tmp_path_factory, name: str, switches: dict[str, str]) -> Path:
    """A temporary project root whose copy of the tracked tuning file holds each switched text of `switches` in place
    of the shipped text it is keyed by, which the file holds once."""
    root = project_root(tmp_path_factory, name)
    tuning_file = root / TUNING_FILE
    text = tuning_file.read_text(encoding="utf-8")
    for shipped, switched in switches.items():
        assert text.count(shipped) == 1
        text = text.replace(shipped, switched)
    tuning_file.write_text(text, encoding="utf-8")
    return root


def standard_match_in(root: Path, faction: str) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario for a faction's start, played in `root`."""
    scenarios: list[StandardScenario] = []

    def new_game(decision_maker: Callable[[], Any]) -> FakeRedAlert:
        scenarios.append(StandardScenario(raid_of(decision_maker)))
        return scenarios[0].new_game(faction)

    played = play(root, new_game, faction, standard.STEPS)
    played.scenario = scenarios[0]
    return played


def compute_bound_kept(compute_ms: list[float]) -> tuple[bool, bool, bool]:
    """Whether a whole match's cycles kept each part of the compute bound: p95, the 99.5th percentile and the single
    slowest cycle."""
    return (
        percentile(compute_ms, P95) <= COMPUTE_P95_MS,
        percentile(compute_ms, P995) <= COMPUTE_P995_MS,
        max(compute_ms) <= COMPUTE_MAX_MS,
    )


def unhandled_errors(played: PlayedMatch) -> list[dict[str, Any]]:
    """The match's `error` events of an unhandled error."""
    return [error for error in played.data_of("error") if error["type"] == "unhandled"]


def assert_whole_within_budget(played: PlayedMatch) -> None:
    """The match ran its 6,000 steps to the end with no unhandled error and no refused batch, every cycle within the
    compute budget."""
    compute_ms = [action["compute_ms"] for action in played.data_of("action")]
    assert (len(played.cycles), played.run.final_state.is_terminal) == (standard.STEPS, True)
    assert (unhandled_errors(played), played.run.invalid_actions, played.game.invalid_actions) == ([], 0, 0)
    assert compute_bound_kept(compute_ms) == WITHIN_BUDGET


# -- the standard scenario with the Soviet scout slots held by flak trucks ------------------------------------------

# The Soviet scout unit as the project root's tuning file has it, the dog, and the flak truck; the faction its match
# is played for.
SOVIET_SCOUT_UNIT_DOG, SOVIET_SCOUT_UNIT_FTRK = 'soviet_scout_unit = "dog"\n', 'soviet_scout_unit = "ftrk"\n'
FLAK_SCOUTS_FACTION = "russia"
KENNEL = "kenn"


@pytest.fixture(scope="module")
def flak_scouts_match(tmp_path_factory) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario for the Soviet start, with the copy of the tracked tuning
    file switching the scout slots on and making flak trucks the Soviet scout unit."""
    switches = {SLOTS_OFF: SLOTS_ON, SOVIET_SCOUT_UNIT_DOG: SOVIET_SCOUT_UNIT_FTRK}
    return standard_match_in(switched_root(tmp_path_factory, "flak-scouts", switches), FLAK_SCOUTS_FACTION)


def test_soviet_scout_unit_a_whole_russia_match_with_flak_truck_scouts_runs_within_the_compute_budget(
    flak_scouts_match,
):
    scouting = flak_scouts_match.agent.ctx.tuning.scouting

    assert scouting.scout_slots and scouting.soviet_scout_unit == "ftrk"
    assert_whole_within_budget(flak_scouts_match)


def items_ordered(played: PlayedMatch, command: str) -> set[str]:
    """The items of every order of a command (`build`, `train`) the agent sent in the match."""
    return {order["item"] for cycle in played.cycles for order in cycle.orders if order["cmd"] == command}


def slot_unit_types(played: PlayedMatch, kind: str) -> set[str]:
    """The unit types of the match's `scout_slot` events of a kind."""
    return {data["unit_type"] for data in played.data_of("scout_slot") if data["kind"] == kind}


def test_soviet_scout_unit_the_flak_truck_match_orders_no_kennel_and_trains_flak_trucks_for_the_slots(
    flak_scouts_match,
):
    built, trained = items_ordered(flak_scouts_match, "build"), items_ordered(flak_scouts_match, "train")

    assert (KENNEL in built, "dog" in trained, "ftrk" in trained) == (False, False, True)
    assert slot_unit_types(flak_scouts_match, "upgraded") == {"ftrk"}
    assert len(flak_scouts_match.agent.ctx.groups.members(SCOUT)) <= 2


# -- the standard scenario with the scout slots keeping a wide berth ------------------------------------------------

# The scouts' wide berth switched off and on; the least share of the squares the scout
# slots saw by 10:00 without it that the match with it sees as well.
WIDE_BERTH_OFF, WIDE_BERTH_ON = "scout_wide_berth = false\n", "scout_wide_berth = true\n"
WIDE_BERTH_EXPLORED_SHARE = 0.8


@dataclass
class WideBerthMatch:
    """A whole match with the scout slots and their wide berth on, the squares its units saw by 10:00, and those of the
    same match without the wide berth."""

    played: PlayedMatch
    explored: int
    explored_without: int


@pytest.fixture(scope="module")
def wide_berth_match(slots_match, tmp_path_factory) -> WideBerthMatch:
    """For the family of `slots_match`, one whole 20-minute match on the standard scenario with the copy of the tracked
    tuning file switching the scout slots and their wide berth on, set against `slots_match`, which has the berth off.
    """
    faction = slots_match.faction
    switches = {SLOTS_OFF: SLOTS_ON, WIDE_BERTH_OFF: WIDE_BERTH_ON}
    with squares_seen_by_ten_minutes() as seen:
        played = standard_match_in(switched_root(tmp_path_factory, f"wide-berth-{faction}", switches), faction)
    return WideBerthMatch(played, len(seen), EXPLORED_WITH_SLOTS[faction])


def test_wide_berth_a_whole_match_with_the_scout_slots_keeping_a_wide_berth_runs_within_the_compute_budget(
    wide_berth_match,
):
    scouting = wide_berth_match.played.agent.ctx.tuning.scouting

    assert scouting.scout_slots and scouting.scout_wide_berth
    assert_whole_within_budget(wide_berth_match.played)


def test_wide_berth_the_squares_seen_by_10_00_are_at_least_80_percent_of_the_same_match_without_it(wide_berth_match):
    seen, seen_without = wide_berth_match.explored, wide_berth_match.explored_without

    assert seen >= WIDE_BERTH_EXPLORED_SHARE * seen_without > 0


# -- the standard scenario with the scout slots' spending capped ----------------------------------------------------

# The scout spend cap switched off and on; the faction its match is played for, whose
# fast scout is the jeep.
SPEND_CAP_OFF, SPEND_CAP_ON = "scout_spend_cap = false\n", "scout_spend_cap = true\n"
SPEND_CAP_FACTION = "england"


@dataclass
class SpendCapMatch:
    """A whole match with the scout slots and their spend cap on, and the type of every unit the slots ordered in it."""

    played: PlayedMatch
    slot_orders: list[str]


def noting_slot_orders(noted: list[str]) -> Callable[..., list[Any]]:
    """The scout slots' training of the units they want, the type of each unit ordered noted."""
    own_train = ScoutSlots._train

    def train(self: Any, wanted: list[tuple[Any, str]], world: Any, ctx: Any) -> list[Any]:
        noted.extend(item for _, item in wanted)
        return own_train(self, wanted, world, ctx)

    return train


@pytest.fixture(scope="module")
def spend_cap_match(tmp_path_factory) -> SpendCapMatch:
    """One whole 20-minute match on the standard scenario for the Allied start, with the copy of the tracked tuning
    file switching the scout slots and their spend cap on; every unit the slots order is noted."""
    noted: list[str] = []
    root = switched_root(tmp_path_factory, "spend-cap", {SLOTS_OFF: SLOTS_ON, SPEND_CAP_OFF: SPEND_CAP_ON})
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ScoutSlots, "_train", noting_slot_orders(noted))
        played = standard_match_in(root, SPEND_CAP_FACTION)
    return SpendCapMatch(played, noted)


def test_scout_spend_cap_a_whole_match_with_the_scout_slots_spending_capped_runs_within_the_compute_budget(
    spend_cap_match,
):
    scouting = spend_cap_match.played.agent.ctx.tuning.scouting

    assert scouting.scout_slots and scouting.scout_spend_cap
    assert_whole_within_budget(spend_cap_match.played)


def test_scout_spend_cap_the_slots_order_at_most_6_fast_scouts_in_the_whole_match(spend_cap_match):
    fast = [item for item in spend_cap_match.slot_orders if item in FAST_SCOUTS]

    assert fast and len(fast) <= spend_cap_match.played.agent.ctx.tuning.scouting.fast_scouts_max == 6


# -- the standard scenario with a harvester raided away from the base, main answering ----------------------------

# The harvester group response switched off and on; the faction its match is played
# for. Near the end of the match, under PRESERVE (or HOLD under the raid's major threat), three enemy tanks raid an own
# harvester 25 cells from the construction yard: for 20 s it is reported under attack each second while it is within
# their reach, which it runs out of, then they leave.
HARVESTER_RESPONSE_OFF = "harvester_group_response = false\n"
HARVESTER_RESPONSE_ON = "harvester_group_response = true\n"
# R2's PRESERVE before R6 in the last three minutes, so that the match ends under the plan above and not FINISH.
FINISH_OVER_PRESERVE_ON, FINISH_OVER_PRESERVE_OFF = "finish_over_preserve = true\n", "finish_over_preserve = false\n"
HARVESTER_RESPONSE_FACTION = "england"
HARVESTER_RAID_SECONDS = 1060
HARVESTER_RAID_OFFSET = (15, -20)
HARVESTER_RAIDERS = 3
HARVESTER_RAID_LASTS_SECONDS = 20
HARVESTER_RAIDER_REACH_CELLS = 6


class HarvesterRaidScenario(StandardScenario):
    """The standard scenario with an own harvester raided away from the base near the end of the match."""

    def __init__(self, raid_members: Callable[[], Any]) -> None:
        super().__init__(raid_members)
        self.harvester_id: int | None = None
        self.raider_ids: list[int] = []
        # The cell the raiders stand by; None before the raid.
        self.raid_cell: tuple[int, int] | None = None

    def steps(self) -> list[ScenarioStep]:
        """The standard steps, and the raid's: the harvester and its raiders placed, a hit each second, the leaving."""
        start, lasts = HARVESTER_RAID_SECONDS, HARVESTER_RAID_LASTS_SECONDS
        hits = [at_seconds(start + second, self._hit) for second in range(1, lasts + 1)]
        return [
            *super().steps(),
            at_seconds(start, self._raid),
            *hits,
            at_seconds(start + lasts + 1, self._leave_harvester),
        ]

    def _raid(self, game: FakeRedAlert) -> None:
        yard = next(building for building in game.own_buildings() if building["type"] == CONSTRUCTION_YARD)
        x, y = yard["x"] + HARVESTER_RAID_OFFSET[0], yard["y"] + HARVESTER_RAID_OFFSET[1]
        self.harvester_id, self.raid_cell = game.add_own(HARVESTER, x, y), (x + 3, y)
        self.raider_ids = [game.place_enemy("1tnk", x + 3, y + number) for number in range(HARVESTER_RAIDERS)]

    def _hit(self, game: FakeRedAlert) -> None:
        """Report the harvester under attack while it stands within the raiders' reach."""
        harvester = next(unit for unit in game.own_units() if unit["id"] == self.harvester_id)
        if (
            math.hypot(harvester["x"] - self.raid_cell[0], harvester["y"] - self.raid_cell[1])
            <= HARVESTER_RAIDER_REACH_CELLS
        ):
            game.emit_under_attack(self.harvester_id)

    def _leave_harvester(self, game: FakeRedAlert) -> None:
        for enemy_id in self.raider_ids:
            game.remove_enemy(enemy_id)


@pytest.fixture(scope="module")
def harvester_response_match(tmp_path_factory) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario for the Allied start with a harvester raided away from the
    base near its end, the copy of the tracked tuning file switching the harvester group response on."""
    switches = {HARVESTER_RESPONSE_OFF: HARVESTER_RESPONSE_ON, FINISH_OVER_PRESERVE_ON: FINISH_OVER_PRESERVE_OFF}
    root = switched_root(tmp_path_factory, "harvester-response", switches)
    scenarios: list[HarvesterRaidScenario] = []

    def new_game(decision_maker: Callable[[], Any]) -> FakeRedAlert:
        scenarios.append(HarvesterRaidScenario(raid_of(decision_maker)))
        return scenarios[0].new_game(HARVESTER_RESPONSE_FACTION)

    played = play(root, new_game, HARVESTER_RESPONSE_FACTION, standard.STEPS)
    played.scenario = scenarios[0]
    return played


def test_harvester_group_response_a_whole_match_with_main_answering_a_harvester_raid_runs_within_the_compute_budget(
    harvester_response_match,
):
    assert harvester_response_match.agent.ctx.tuning.base_defense.harvester_group_response
    assert_whole_within_budget(harvester_response_match)


def test_harvester_group_response_main_is_sent_to_the_raided_harvester_and_let_go_when_the_raid_is_over(
    harvester_response_match,
):
    outcomes = [event["outcome"] for event in harvester_response_match.data_of("base_defense")]

    assert outcomes[0] == "sent" and outcomes[-1] == "ended" and set(outcomes) <= {"sent", "arrived", "ended"}


# -- the standard scenario with main kept on the attack ---------------------------------------------------------------

# Keeping main on the attack switched off and on; the faction its match is played for.
KEEP_ATTACK_ARMY_OFF, KEEP_ATTACK_ARMY_ON = "keep_attack_army = false\n", "keep_attack_army = true\n"
KEEP_ATTACK_ARMY_FACTION = "england"


@pytest.fixture(scope="module")
def keep_attack_army_match(tmp_path_factory) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario for the Allied start, with the copy of the tracked tuning
    file switching keep_attack_army on."""
    root = switched_root(tmp_path_factory, "keep-attack-army", {KEEP_ATTACK_ARMY_OFF: KEEP_ATTACK_ARMY_ON})
    return standard_match_in(root, KEEP_ATTACK_ARMY_FACTION)


def test_keep_attack_army_a_whole_match_with_main_kept_on_the_attack_runs_within_the_compute_budget(
    keep_attack_army_match,
):
    assert keep_attack_army_match.agent.ctx.tuning.base_defense.keep_attack_army
    assert_whole_within_budget(keep_attack_army_match)


# -- the standard scenario with the scout slots on scripted missions ------------------------------------------------

# The scout missions switched off and on; the kinds a mission may be of.
SCOUT_MISSIONS_OFF, SCOUT_MISSIONS_ON = "scout_missions = false\n", "scout_missions = true\n"
MISSION_KINDS = {"expansion_watch", "enemy_home_field", "main_edge", "expansion_approach", "attack_lane"}


@pytest.fixture(scope="module", params=FAMILIES)
def missions_match(request, tmp_path_factory) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario for a family's start, with the copy of the tracked tuning
    file switching the scout slots and their scripted missions on."""
    faction = request.param
    switches = {SLOTS_OFF: SLOTS_ON, SCOUT_MISSIONS_OFF: SCOUT_MISSIONS_ON}
    return standard_match_in(switched_root(tmp_path_factory, f"missions-{faction}", switches), faction)


def test_scout_missions_a_whole_match_with_the_scouts_on_missions_runs_within_the_compute_budget(missions_match):
    scouting = missions_match.agent.ctx.tuning.scouting

    assert scouting.scout_slots and scouting.scout_missions
    assert_whole_within_budget(missions_match)


def test_scout_missions_every_destination_of_the_whole_match_is_a_mission_s(missions_match):
    events = [data for data in missions_match.data_of("scout_slot") if data["kind"] == "destination"]

    assert events and {event["source"] for event in events} == {"mission"}
    assert {event["mission"] for event in events} <= MISSION_KINDS and all(event["score"] >= 0 for event in events)


# -- the opening's defenses bought on a threat only, with the rush watch -------------------------------------------

# The opening's defenses on a threat only and the rush watch, switched off and on;
# the faction the standard match is played for.
DEFENSE_ON_THREAT_OFF, DEFENSE_ON_THREAT_ON = "defense_on_threat = false\n", "defense_on_threat = true\n"
RUSH_DETECTION_OFF, RUSH_DETECTION_ON = "rush_detection = false\n", "rush_detection = true\n"
ON_THREAT_FACTION = "england"
ANTI_INFANTRY_DEFENSE = {"england": "pbox", "russia": "ftur"}
RUSH_VERDICT = "rush"


def with_defense_on_threat(root: Path) -> Path:
    """`root`, its copy of the tracked tuning file switching the opening's defenses on a threat only and the rush
    watch on."""
    tuning_file = root / TUNING_FILE
    text = tuning_file.read_text(encoding="utf-8")
    assert text.count(DEFENSE_ON_THREAT_OFF) == 1 and text.count(RUSH_DETECTION_OFF) == 1
    switched = text.replace(DEFENSE_ON_THREAT_OFF, DEFENSE_ON_THREAT_ON)
    tuning_file.write_text(switched.replace(RUSH_DETECTION_OFF, RUSH_DETECTION_ON), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def on_threat_match(tmp_path_factory) -> PlayedMatch:
    """One whole 20-minute match on the standard scenario for the Allied start, with the copy of the tracked tuning
    file switching on the opening's defenses on a threat only and the rush watch."""
    root = with_defense_on_threat(project_root(tmp_path_factory, "on-threat"))
    scenarios: list[StandardScenario] = []

    def new_game(decision_maker: Callable[[], Any]) -> FakeRedAlert:
        scenarios.append(StandardScenario(raid_of(decision_maker)))
        return scenarios[0].new_game(ON_THREAT_FACTION)

    played = play(root, new_game, ON_THREAT_FACTION, standard.STEPS)
    played.scenario = scenarios[0]
    return played


@pytest.fixture(scope="module", params=FAMILIES)
def early_rush_match(request, tmp_path_factory) -> PlayedMatch:
    """One whole 20-minute match on the early-rush scenario for a family's start, with the copy of the tracked tuning
    file switching on the opening's defenses on a threat only and the rush watch."""
    root = with_defense_on_threat(project_root(tmp_path_factory, f"early-rush-{request.param}"))
    scenario = EarlyRushScenario()
    played = play(root, lambda _decision_maker: scenario.new_game(request.param), request.param, standard.STEPS)
    played.scenario = scenario
    return played


def assert_whole_and_within_budget(played: PlayedMatch) -> None:
    """The match ran its 6,000 steps to the end with both switches on, no unhandled error and no refused batch, every
    cycle within the compute budget."""
    tuning = played.agent.ctx.tuning
    compute_ms = [action["compute_ms"] for action in played.data_of("action")]
    assert tuning.opening.defense_on_threat and tuning.scouting.rush_detection
    assert len(played.cycles) == standard.STEPS and played.run.final_state.is_terminal
    assert [error for error in played.data_of("error") if error["type"] == "unhandled"] == []
    assert played.run.invalid_actions == 0 and played.game.invalid_actions == 0
    assert compute_bound_kept(compute_ms) == WITHIN_BUDGET


def first_rush_verdict_tick(played: PlayedMatch) -> int:
    """The tick of the first `rush_verdict` event that says rush."""
    return next(event["tick"] for event in played.data_of("rush_verdict") if event["verdict"] == RUSH_VERDICT)


def first_order_tick(played: PlayedMatch, item: str) -> int:
    """The tick of the first state the agent answered with a build order for an item."""
    return next(
        cycle.tick
        for cycle in played.cycles
        if any(order["cmd"] == "build" and order["item"] == item for order in cycle.orders)
    )


def test_on_threat_a_whole_standard_match_runs_without_an_unhandled_error_within_the_compute_budget(on_threat_match):
    assert_whole_and_within_budget(on_threat_match)


def test_on_threat_in_the_standard_match_no_pillbox_is_bought_before_the_eight_rifles_at_90_s_give_a_rush(
    on_threat_match,
):
    rush_at = first_rush_verdict_tick(on_threat_match)
    rifles_arrive = on_threat_match.scenario.attacks[0].arrives_seconds * TICKS_PER_SECOND
    verdicts_before = [event["verdict"] for event in on_threat_match.data_of("rush_verdict") if event["tick"] < rush_at]

    # Until the rifles come the enemy base is seen with no sign of a rush; the first pillbox answers the rifles.
    assert rush_at == rifles_arrive and verdicts_before == ["none"]
    assert first_order_tick(on_threat_match, ANTI_INFANTRY_DEFENSE[ON_THREAT_FACTION]) >= rush_at


def test_on_threat_a_whole_early_rush_match_runs_without_an_unhandled_error_within_the_compute_budget(
    early_rush_match,
):
    assert_whole_and_within_budget(early_rush_match)


def test_on_threat_the_early_rush_is_told_and_its_defense_bought_and_standing_before_it_reaches_the_base(
    early_rush_match,
):
    scenario = early_rush_match.scenario
    defense = ANTI_INFANTRY_DEFENSE[early_rush_match.faction]
    rush_at = first_rush_verdict_tick(early_rush_match)
    ordered_at = first_order_tick(early_rush_match, defense)

    assert early_rush.SETS_OUT_SECONDS * TICKS_PER_SECOND <= rush_at < ordered_at < scenario.arrived_tick
    assert defense in scenario.defenses_at_arrival


# -- the standard scenario with upkeep's defenses bought only against a real threat ----------------------------------

ON_THREAT_ONLY_OFF, ON_THREAT_ONLY_ON = "defenses_on_threat_only = false\n", "defenses_on_threat_only = true\n"
# The rules that order a defense other than for the end of the match or a major threat, with what each waits for
# with the switch on: nothing (no rule replaces a lost defense then), a recorded vehicle attack, an enemy combat
# aircraft seen, and a threat in force.
DEFENSE_RULES_WAIT_FOR = {
    upkeep.Defenses: lambda order: False,
    upkeep.TeslaCoils: lambda order: order.vehicle_attacks > 0,
    upkeep.AntiAirDefenses: lambda order: order.aircraft_seen > 0,
    clock.PreserveDefense: lambda order: order.threat_active,
}


@dataclass(frozen=True)
class DefenseOrder:
    """A defense one of the defense rules proposed, and what the match context held in that cycle."""

    rule: type
    tick: int
    item: str
    threat_active: bool
    vehicle_attacks: int
    aircraft_seen: int


@dataclass
class OnThreatOnlyMatch:
    """A whole match with upkeep's defenses bought only against a real threat, and every defense the defense rules
    proposed in it."""

    played: PlayedMatch
    defense_orders: list[DefenseOrder]


def noting_defenses(rule: type, noted: list[DefenseOrder]) -> Callable[..., list[Any]]:
    """The intents of a defense rule, each defense among them noted with what the match context held."""
    own_intents = rule.intents

    def intents(self: Any, world: Any, ctx: Any) -> list[Any]:
        proposed = own_intents(self, world, ctx)
        for intent in proposed:
            facts = world.items.get(intent.order.get("item", ""))
            if intent.order["cmd"] == "build" and facts is not None and facts.queue == DEFENSE_QUEUE:
                noted.append(
                    DefenseOrder(
                        rule,
                        world.tick,
                        intent.order["item"],
                        ctx.threat_active,
                        len(ctx.vehicle_attacks),
                        ctx.air_watch.aircraft_seen,
                    )
                )
        return proposed

    return intents


@pytest.fixture(scope="module", params=FAMILIES)
def on_threat_only_match(request, tmp_path_factory) -> OnThreatOnlyMatch:
    """One whole 20-minute match on the standard scenario for a family's start, with the copy of the tracked tuning
    file switching on upkeep's defenses only against a real threat together with the opening's defenses on a threat
    and the rush watch, as the watch command sets them; every defense the defense rules propose is noted."""
    faction, noted = request.param, []
    root = with_defense_on_threat(project_root(tmp_path_factory, f"on-threat-only-{faction}"))
    tuning_file = root / TUNING_FILE
    text = tuning_file.read_text(encoding="utf-8")
    assert text.count(ON_THREAT_ONLY_OFF) == 1
    tuning_file.write_text(text.replace(ON_THREAT_ONLY_OFF, ON_THREAT_ONLY_ON), encoding="utf-8")
    scenarios: list[StandardScenario] = []

    def new_game(decision_maker: Callable[[], Any]) -> FakeRedAlert:
        scenarios.append(StandardScenario(raid_of(decision_maker)))
        return scenarios[0].new_game(faction)

    with pytest.MonkeyPatch.context() as patch:
        for rule in DEFENSE_RULES_WAIT_FOR:
            patch.setattr(rule, "intents", noting_defenses(rule, noted))
        played = play(root, new_game, faction, standard.STEPS)
    played.scenario = scenarios[0]
    return OnThreatOnlyMatch(played, noted)


def test_on_threat_only_a_whole_standard_match_runs_without_an_unhandled_error_within_the_compute_budget(
    on_threat_only_match,
):
    played = on_threat_only_match.played

    assert played.agent.ctx.tuning.upkeep.defenses_on_threat_only
    assert_whole_and_within_budget(played)


def test_on_threat_only_no_defense_rule_orders_a_defense_without_what_it_waits_for(on_threat_only_match):
    orders = on_threat_only_match.defense_orders

    # The scenario's eight rifles and then its four tanks attack the base: only the tanks are a vehicle attack.
    assert len(on_threat_only_match.played.agent.ctx.vehicle_attacks) == 1
    assert [order for order in orders if not DEFENSE_RULES_WAIT_FOR[order.rule](order)] == []


# -- the standard scenario with model plan choice on, the model a stand-in ----------------------------------------


@dataclass
class StubModel:
    """Stands in for the model server: answers every plan choice with the first plan its schema allows, followed by
    a note that leaks the agent's key and a key-shaped string, and remembers each request body and answer."""

    bodies: list[dict[str, Any]] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.transport = httpx.MockTransport(self.answer)

    def answer(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        first_plan = body["response_format"]["json_schema"]["schema"]["properties"]["choice"]["enum"][0]
        leaked = f"{SECRET_ENV_FOR_TESTS['ALTRUAGENT_OFFICIAL_AGENT_KEY']} {LEAKED_SHAPED_KEY}"
        content = json.dumps({"choice": first_plan, "note": leaked})
        self.bodies.append(body)
        self.answers.append(content)
        return httpx.Response(
            200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
        )


@dataclass
class ModelChoiceMatch:
    """A match played with model plan choice on: what it left, the stand-in model, and its record as written."""

    played: PlayedMatch
    model: StubModel
    record_text: str


@pytest.fixture(scope="module")
def plan_choice_match(tmp_path_factory) -> ModelChoiceMatch:
    """One whole 20-minute match on the standard scenario for the Allied start, with the copy of the tracked
    `config/agent.toml` switching model plan choice on and the plan chain asking the stand-in model."""
    root = project_root(tmp_path_factory, "plan-choice")
    agent_config = root / AGENT_CONFIG_FILE
    text = agent_config.read_text(encoding="utf-8")
    assert text.count(PLAN_CHOICE_OFF) == 1
    agent_config.write_text(text.replace(PLAN_CHOICE_OFF, PLAN_CHOICE_ON), encoding="utf-8")
    model = StubModel()
    scenarios: list[StandardScenario] = []

    def new_game(decision_maker: Callable[[], Any]) -> FakeRedAlert:
        scenarios.append(StandardScenario(raid_of(decision_maker)))
        return scenarios[0].new_game(PLAN_CHOICE_FACTION)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(redalert_agent, "build_chain", lambda name: build_local_chain(name, transport=model.transport))
        played = play(root, new_game, PLAN_CHOICE_FACTION, standard.STEPS)
    played.scenario = scenarios[0]
    (events_file,) = root.glob("runs/*/*/events.jsonl")
    return ModelChoiceMatch(played, model, events_file.read_text(encoding="utf-8"))


def test_io_a_whole_match_with_model_plan_choice_on_runs_without_an_unhandled_error_or_a_refused_batch(
    plan_choice_match,
):
    played = plan_choice_match.played
    errors = played.data_of("error")

    assert len(played.cycles) == standard.STEPS and played.run.final_state.is_terminal
    assert [error for error in errors if error["type"] == "unhandled"] == []
    assert played.run.invalid_actions == 0 and played.game.invalid_actions == 0


def test_io_every_plan_decision_records_the_messages_sent_the_settings_and_the_raw_answer(plan_choice_match):
    played, model = plan_choice_match.played, plan_choice_match.model
    decisions = [data for data in played.data_of("decision") if data["source"] == PLAN_SOURCE]
    deadline_ms = played.agent.ctx.tuning.plan_rules.choice_deadline_ms

    # A question still in flight when the match ends is never handed over, and so never recorded.
    assert decisions and len(model.bodies) - 1 <= len(decisions) <= len(model.bodies)
    for decision, body, answer in zip(decisions, model.bodies, model.answers):
        system, user = body["messages"]
        assert (decision["io"]["system"], decision["io"]["user"]) == (system["content"], user["content"])
        assert decision["io"]["response_format"] == body["response_format"]
        params = decision["io"]["params"]
        assert {key: params[key] for key in ("model_role", "temperature", "max_tokens", "reasoning")} == {
            "model_role": PLAN_ROLE,
            "temperature": 0,
            "max_tokens": body["max_tokens"],
            "reasoning": "off",
        }
        # The chain hands its one source what is left of the deadline when the source's turn comes.
        assert deadline_ms - CHAIN_START_MS <= params["deadline_ms"] <= deadline_ms
        written = answer.replace(SECRET_ENV_FOR_TESTS["ALTRUAGENT_OFFICIAL_AGENT_KEY"], REDACTED)
        assert decision["io"]["raw_output"] == written.replace(LEAKED_SHAPED_KEY, REDACTED)
        assert decision["answer"] == json.loads(answer)["choice"]


def test_io_no_line_of_the_record_matches_the_secret_scan_s_rules_and_none_holds_a_request_header(plan_choice_match):
    secret = re.compile("|".join([*map(re.escape, secret_values(SECRET_ENV_FOR_TESTS)), *SECRET_PATTERNS]))
    lines = plan_choice_match.record_text.splitlines()

    assert [number for number, line in enumerate(lines, start=1) if secret.search(line)] == []
    # The stand-in model wrote both keys into every answer: the record holds them, redacted.
    assert REDACTED in plan_choice_match.record_text
    assert not any(name in line.lower() for line in lines for name in HEADER_NAMES)


def test_io_compute_ms_keeps_the_compute_bound_with_model_plan_choice_on(
    plan_choice_match,
):
    compute_ms = [action["compute_ms"] for action in plan_choice_match.played.data_of("action")]

    assert len(compute_ms) == standard.STEPS
    assert compute_bound_kept(compute_ms) == WITHIN_BUDGET


# -- the standard scenario with model plan choice on and the model's plan held, the model a stand-in -----------------

# The hold of a plan the model chose switched off and on; the plans the stand-in
# model answers with, in turn; and what the plan record names a model's change and the early end of its hold.
MODEL_HOLD_OFF, MODEL_HOLD_ON = "model_plan_hold = false\n", "model_plan_hold = true\n"
ALTERNATING_PLANS = ("RAID", "PRESS", "HOLD")
MODEL_RULE, HOLD_ENDED = "model", "model_hold_ended"


class AlternatingModel:
    """Stands in for the model server: answers each plan choice with the next of RAID, PRESS and HOLD, in turn, that
    its schema allows, and counts the questions."""

    def __init__(self) -> None:
        self.asked = 0
        self._turn = itertools.cycle(ALTERNATING_PLANS)
        self.transport = httpx.MockTransport(self.answer)

    def answer(self, request: httpx.Request) -> httpx.Response:
        allowed = json.loads(request.content)["response_format"]["json_schema"]["schema"]["properties"]["choice"][
            "enum"
        ]
        # HOLD is always open, so the turn always comes to a plan allowed.
        plan = next(plan for plan in self._turn if plan in allowed)
        self.asked += 1
        content = json.dumps({"reason": "the stand-in's turn", "choice": plan})
        message = {"role": "assistant", "content": content}
        return httpx.Response(200, json={"choices": [{"index": 0, "message": message}]})


@dataclass
class ModelHoldMatch:
    """A match played with model plan choice and the model's hold on: what it left, and the stand-in model."""

    played: PlayedMatch
    model: AlternatingModel


@pytest.fixture(scope="module")
def model_hold_match(tmp_path_factory) -> ModelHoldMatch:
    """One whole 20-minute match on the standard scenario for the Allied start, with the copies of the tracked files
    switching model plan choice and the model's hold on, and the plan chain asking the alternating stand-in."""
    root = switched_root(tmp_path_factory, "model-hold", {MODEL_HOLD_OFF: MODEL_HOLD_ON})
    agent_config = root / AGENT_CONFIG_FILE
    text = agent_config.read_text(encoding="utf-8")
    assert text.count(PLAN_CHOICE_OFF) == 1
    agent_config.write_text(text.replace(PLAN_CHOICE_OFF, PLAN_CHOICE_ON), encoding="utf-8")
    model = AlternatingModel()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(redalert_agent, "build_chain", lambda name: build_local_chain(name, transport=model.transport))
        played = standard_match_in(root, PLAN_CHOICE_FACTION)
    return ModelHoldMatch(played, model)


def model_change_ticks(played: PlayedMatch) -> list[int]:
    """The ticks of the match's plan changes a model's answer made."""
    return [change["tick"] for change in played.data_of("plan_change") if change["rule"] == MODEL_RULE]


def is_unexplained(before: int, after: int, ends: list[int], hold_ticks: int) -> bool:
    """Whether two plan changes a model's answer made, at `before` and `after`, are less than `hold_ticks` apart with
    no early end of the model's hold, of those at `ends`, after the first and by the second."""
    return after - before < hold_ticks and not any(before < end <= after for end in ends)


def close_changes_without_an_end(played: PlayedMatch, hold_ticks: int) -> list[tuple[int, int]]:
    """Each two plan changes in a row a model's answer made that `is_unexplained` holds of."""
    changes = model_change_ticks(played)
    ends = [event["tick"] for event in played.data_of("plan_rule_blocked") if event["reason"] == HOLD_ENDED]
    pairs = zip(changes, changes[1:])
    return [(before, after) for before, after in pairs if is_unexplained(before, after, ends, hold_ticks)]


def test_model_plan_hold_a_whole_match_with_the_hold_and_an_alternating_model_runs_within_the_compute_budget(
    model_hold_match,
):
    assert_whole_within_budget(model_hold_match.played)


def test_model_plan_hold_the_stand_in_was_asked_and_its_answers_changed_the_plan_more_than_once(model_hold_match):
    assert model_hold_match.model.asked > 0 and len(model_change_ticks(model_hold_match.played)) >= 2


def test_model_plan_hold_no_two_model_changes_are_closer_than_750_ticks_without_an_early_end_between(
    model_hold_match,
):
    played = model_hold_match.played
    hold_ticks = played.agent.ctx.tuning.plan_rules.model_plan_hold_ticks

    assert hold_ticks == 750 and close_changes_without_an_end(played, hold_ticks) == []


# -- the hostile interface ------------------------------------------------------------------------------------------


@pytest.fixture(scope="module", params=FAMILIES)
def hostile_match(request, tmp_path_factory) -> PlayedMatch:
    """1,500 steps of the hostile-interface scenario for a family's start."""
    scenario = HostileInterfaceScenario()
    played = play(
        project_root(tmp_path_factory, f"hostile-{request.param}"),
        lambda _decision_maker: scenario.new_game(request.param),
        request.param,
        hostile_interface.STEPS,
    )
    played.scenario = scenario
    return played


def test_hostile_no_exception_escapes_and_nothing_is_ordered_twice(hostile_match):
    assert len(hostile_match.cycles) == hostile_interface.STEPS
    assert [error for error in hostile_match.data_of("error") if error["type"] == "unhandled"] == []
    assert hostile_match.agent.ledger.duplicate_production_orders == 0


def test_hostile_each_order_of_a_lost_batch_is_sent_again_exactly_once(hostile_match):
    entries = hostile_match.agent.ledger.entries
    lost = [entry for entry in entries if entry.status == LOST]
    window = hostile_match.agent.ctx.tuning.loop.reissue_after_ticks

    def sent_again(lost_entry) -> int:
        # The loss shows in the next state; within the reissue window the agent's own rules repeat nothing.
        seen_lost_at = lost_entry.sent_tick + TICKS_PER_STEP
        return sum(
            1
            for entry in entries
            if entry.order == lost_entry.order and seen_lost_at <= entry.sent_tick < seen_lost_at + window
        )

    assert len({entry.batch for entry in lost}) == len(hostile_interface.LOST_BATCH_STEPS)
    assert [sent_again(entry) for entry in lost] == [1] * len(lost)


def test_hostile_a_placement_refused_twice_is_cancelled_with_one_handled_error_and_built_and_placed_again(
    hostile_match,
):
    item = hostile_interface.REFUSED_PLACE_ITEM
    entries = [entry for entry in hostile_match.agent.ledger.entries if entry.order.get("item") == item]
    refused = [entry for entry in entries if entry.order["cmd"] == "place" and entry.status == REJECTED]
    (cancel,) = [entry for entry in entries if entry.order["cmd"] == "cancel"]
    # The ledger's own errors; other handled errors (the opening missing the withdrawn e3) say nothing of this.
    errors = [error for error in hostile_match.data_of("error") if error["where"] == "redalert.ledger"]
    built_again = [entry for entry in entries if entry.order["cmd"] == "build" and entry.sent_tick > cancel.sent_tick]
    placed_again = [entry for entry in entries if entry.order["cmd"] == "place" and entry.sent_tick > cancel.sent_tick]

    assert [entry.reason for entry in refused] == ["invalid", "invalid"]
    assert refused[1].sent_tick < cancel.sent_tick
    assert [(error["type"], error["exception"]) for error in errors] == [("handled", "PlacementRefused")]
    assert item in errors[0]["message"]
    assert built_again and placed_again[0].status == "confirmed"
    assert item in {building["type"] for building in hostile_match.game.own_buildings()}


def train_ticks(played: PlayedMatch, item: str) -> list[int]:
    return [
        entry.sent_tick
        for entry in played.agent.ledger.entries
        if entry.order["cmd"] == "train" and entry.order["item"] == item
    ]


def test_hostile_once_e3_is_withdrawn_it_is_never_trained(hostile_match, tmp_path_factory):
    withdrawn_at = hostile_interface.WITHDRAWN_STEP * TICKS_PER_STEP
    control = HostileInterfaceScenario()

    def without_the_withdrawal(_decision_maker) -> FakeRedAlert:
        kept = [timed for timed in control.steps() if timed.step != hostile_interface.WITHDRAWN_STEP]
        game = FakeRedAlert(hostile_match.faction, scenario=kept)
        game.on_step(control.vanish_a_moving_unit)
        return game

    left_on_offer = play(
        project_root(tmp_path_factory, "control"),
        without_the_withdrawal,
        hostile_match.faction,
        hostile_interface.STEPS,
    )

    assert [tick for tick in train_ticks(hostile_match, "e3") if tick >= withdrawn_at] == []
    # The same match with e3 left on offer trains it after that step: the rule above is not met by chance.
    assert [tick for tick in train_ticks(left_on_offer, "e3") if tick >= withdrawn_at]


def test_hostile_a_unit_that_vanished_is_forgotten_and_never_ordered_again(hostile_match):
    unit_id, gone_at = hostile_match.scenario.vanished
    later = [entry for entry in hostile_match.agent.ledger.entries if entry.sent_tick >= gone_at]

    assert unit_id not in hostile_match.agent.world.units
    assert not [entry for entry in later if unit_id in entry.order.get("units", ())]


# -- the standard scenario with the tuning as it ships, its careful scouts on and off ---------------------------------

# The careful scouts as the tracked tuning file ships them, and switched off; the faction of the matches compared; the
# least share of the squares seen by 10:00 with them off that the match with them sees as well.
SCOUT_CAUTIOUS_SHIPPED, SCOUT_CAUTIOUS_OFF = "scout_cautious = true\n", "scout_cautious = false\n"
CAUTIOUS_FACTION = "england"
CAUTIOUS_EXPLORED_SHARE = 0.8
# The orders that would have a unit shoot, which a slot's scout is never given.
ATTACK_COMMANDS = frozenset({"attack", "attack_move"})


@dataclass
class CautiousMatches:
    """A whole match with the tuning as it ships, careful scouts on, and the squares its units saw by 10:00; and the
    squares the same match saw with them off."""

    played: PlayedMatch
    explored: int
    explored_without: int


def shipped_root_switching(tmp_path_factory, name: str, shipped: str, switched: str) -> Path:
    """A temporary project root holding a copy of the tracked `config/` as it ships, but for `switched` in place of the
    text `shipped`, which its tuning file holds once, and the model switches, off."""
    root = tmp_path_factory.mktemp(name)
    shutil.copytree(REPO_ROOT / "config", root / "config")
    write_model_switches_off(root / AGENT_CONFIG_FILE)
    tuning_file = root / TUNING_FILE
    text = tuning_file.read_text(encoding="utf-8")
    assert text.count(shipped) == 1
    tuning_file.write_text(text.replace(shipped, switched), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def cautious_matches(tmp_path_factory) -> CautiousMatches:
    """Two whole 20-minute matches on the standard scenario for the Allied start, from the same seed, with the tracked
    files as they ship: the careful scouts on, and switched off; the squares each saw by 10:00 are noted."""
    played: dict[str, PlayedMatch] = {}
    explored: dict[str, int] = {}
    for switch, name in ((SCOUT_CAUTIOUS_SHIPPED, "cautious-on"), (SCOUT_CAUTIOUS_OFF, "cautious-off")):
        root = shipped_root_switching(tmp_path_factory, name, SCOUT_CAUTIOUS_SHIPPED, switch)
        with squares_seen_by_ten_minutes() as seen:
            played[switch] = standard_match_in(root, CAUTIOUS_FACTION)
        explored[switch] = len(seen)
    return CautiousMatches(
        played[SCOUT_CAUTIOUS_SHIPPED], explored[SCOUT_CAUTIOUS_SHIPPED], explored[SCOUT_CAUTIOUS_OFF]
    )


def test_scout_cautious_a_whole_match_with_the_shipped_tuning_runs_within_the_compute_budget(cautious_matches):
    tuning = cautious_matches.played.agent.ctx.tuning

    assert tuning == Tuning() and tuning.scouting.scout_cautious
    assert_whole_within_budget(cautious_matches.played)


def test_scout_cautious_the_squares_seen_by_10_00_are_at_least_80_percent_of_the_same_match_with_it_off(
    cautious_matches, record_property
):
    seen, seen_without = cautious_matches.explored, cautious_matches.explored_without
    record_property("squares_seen_cautious_on", seen)
    record_property("squares_seen_cautious_off", seen_without)

    assert seen >= CAUTIOUS_EXPLORED_SHARE * seen_without > 0


def attacks_on_slot_scouts(played: PlayedMatch) -> tuple[set[int], list[dict[str, Any]]]:
    """Every unit that took a scout slot in a match, and each attack or attack_move order sent while a unit it names
    held its slot: from the `scout_slot` events and the batches, in the order the record holds them."""
    holding: set[int] = set()
    took: set[int] = set()
    found: list[dict[str, Any]] = []
    for event in played.events:
        data = event["data"]
        if event["kind"] == "scout_slot" and data["kind"] in SLOT_TAKEN:
            holding.add(data["unit_id"])
            took.add(data["unit_id"])
            holding.discard(data.get("replaces"))
        elif event["kind"] == "scout_slot" and data["kind"] == "lost":
            holding.discard(data["unit_id"])
        elif event["kind"] == "action" and isinstance(data.get("action"), dict):
            orders = data["action"].get("orders", [])
            found += [order for order in orders if order["cmd"] in ATTACK_COMMANDS and holding & set(order["units"])]
    return took, found


def test_scout_cautious_no_unit_is_given_an_attack_while_it_holds_a_scout_slot_and_each_is_set_to_hold_fire(
    cautious_matches,
):
    played = cautious_matches.played
    took, attacks = attacks_on_slot_scouts(played)
    held_fire = {
        unit_id
        for data in played.data_of("action")
        if isinstance(data.get("action"), dict)
        for order in data["action"].get("orders", [])
        if order["cmd"] == "set_stance" and order["stance"] == "hold_fire"
        for unit_id in order["units"]
    }

    assert took and attacks == []
    assert took <= held_fire
