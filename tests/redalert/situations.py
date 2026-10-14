"""Hand-built situations for testing the Red Alert agent one cycle at a time.

A test of a rule of play needs a state that shows exactly the situation the
rule is about, an agent to hand it to, and the orders that came back. The
states here are the fixture states under `tests/fixtures/redalert/states/`
with the fields a test names put in place of the fixture's own, so a test
says only what matters to it.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any, Callable, Iterable

from altruagent import WAIT, DecisionContext, GameState, WithReasoning
from fake_game import QUEUE_PRODUCERS
from ucla_agent.games.redalert.agent import RedAlertAgent
from ucla_agent.games.redalert.batch import Intent
from ucla_agent.games.redalert.items import ItemTable
from ucla_agent.games.redalert.settings import RedAlertSettings
from ucla_agent.games.redalert.tuning import Tuning

REPO_ROOT = Path(__file__).resolve().parents[2]
STATES_FOLDER = REPO_ROOT / "tests" / "fixtures" / "redalert" / "states"
SESSION_ID = "situation-session"
AGENT_ID = "agent-1"
# What one refinery can hold of the ore its harvesters bring in.
ORE_PER_REFINERY = 2000
RESULT = {"is_terminal": True, "status": "completed", "returns": {AGENT_ID: 1.0}, "termination_reason": "time_limit"}


@functools.lru_cache(maxsize=None)
def tracked_items() -> ItemTable:
    """The agent's item table of the tracked `config/redalert/` files. A table is never changed, so one is shared."""
    return ItemTable.from_tracked_files(REPO_ROOT)


@functools.lru_cache(maxsize=None)
def _fixture_text(name: str) -> str:
    return (STATES_FOLDER / f"{name}.json").read_text(encoding="utf-8")


def fixture_state(name: str, tick: int | None = None, **observation: Any) -> dict[str, Any]:
    """A fixture state, at another tick when one is given, with these observation fields in place of its own.

    It carries no events and no verdicts unless they are given: what a
    fixture happened to hold of either would make every test start with a
    history it did not ask for.
    """
    state = json.loads(_fixture_text(name))
    if tick is not None:
        state["state_version"] = state["observation"]["tick"] = tick
    state["last_orders"], state["recent_order_problems"] = None, []
    state["observation"]["events"] = []
    state["observation"].update(observation)
    return state


def economy(cash: int, provided: int = 200, drained: int = 100, harvesters: int = 1, ore: int = 0) -> dict[str, int]:
    """The `economy` object of an observation: the cash, and the ore that one refinery or more hold."""
    return {
        "cash": cash,
        "ore": ore,
        "power_provided": provided,
        "power_drained": drained,
        "resource_capacity": max(ORE_PER_REFINERY, ore),
        "harvesters": harvesters,
    }


def unit(unit_id: int, unit_type: str, x: int = 20, y: int = 48, *, idle: bool = True, hp: int = 100) -> dict[str, Any]:
    """One own unit as an observation lists it."""
    activity = None if idle else "move"
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": hp, "act": activity, "idle": idle, "stance": None}


def building(building_id: int, building_type: str, x: int, y: int, **fields: Any) -> dict[str, Any]:
    """One own building as an observation lists it: undamaged, powered and idle unless told otherwise."""
    shown = {"hp": 100, "powered": True, "powered_down": False, "repairing": False, "producing": None, "rally": None}
    return {"id": building_id, "type": building_type, "x": x, "y": y, **shown, **fields}


def job(queue: str, item: str, *, ready: bool = False, remaining_s: float = 5.0) -> dict[str, Any]:
    """One item of a production queue as an observation lists it."""
    progress = 1.0 if ready else 0.5
    return {
        "queue": queue,
        "item": item,
        "progress": progress,
        "remaining_s": 0.0 if ready else remaining_s,
        "paused": False,
        "ready": ready,
    }


def base_state(
    faction: str,
    *buildings: tuple[str, int, int],
    tick: int = 1000,
    cash: int = 5000,
    ore: int = 0,
    withheld: Iterable[str] = (),
    **observation: Any,
) -> dict[str, Any]:
    """A playing state of a faction's base with these buildings (type, x, y), numbered from 101.

    Power and `available_production` follow from the buildings as they do in
    the fake game, less the items named in `withheld`; units and production
    are empty unless given. The money is `cash` and, held by the refineries,
    `ore`.
    """
    items = tracked_items()
    standing = {building_type for building_type, _, _ in buildings}
    powers = [items[building_type].power for building_type, _, _ in buildings]
    available = [
        item.name
        for item in items
        if item.buildable_by(faction)
        and item.name not in withheld
        and not standing.isdisjoint(QUEUE_PRODUCERS.get(item.queue, ()))
        and standing.issuperset(item.prerequisites)
    ]
    shown = {
        "faction": faction,
        "buildings": [building(101 + number, *spec) for number, spec in enumerate(buildings)],
        "units": [],
        "production": [],
        "available_production": available,
        "economy": economy(
            cash, sum(power for power in powers if power > 0), -sum(power for power in powers if power < 0), 0, ore
        ),
    }
    return fixture_state("opening_mcv", tick, **{**shown, **observation})


def event(seq: int, kind: str, tick: int = 0, **details: Any) -> dict[str, Any]:
    """One event of an observation's window."""
    return {"seq": seq, "tick": tick, "kind": kind, **details}


def context(game_config: dict[str, Any] | None = None) -> DecisionContext:
    """The context the runtime hands a decision-maker for a Red Alert match."""
    return DecisionContext(
        session_id=SESSION_ID, tournament_id=None, game_type="red_alert", agent_id=AGENT_ID, game_config=game_config
    )


def orders_of(decision: Any) -> list[dict[str, Any]]:
    """The orders of what `choose_action` returned: those of its batch, or none for `WAIT`."""
    if decision is WAIT:
        return []
    batch = decision.action if isinstance(decision, WithReasoning) else decision
    return list(batch["orders"])


def ask(agent: Any, raw_state: dict[str, Any], match_context: DecisionContext | None = None) -> list[dict[str, Any]]:
    """Hand one state to an agent the way the runtime does and return the orders it answers with."""
    return orders_of(agent.choose_action(GameState.from_mcp_state(raw_state), match_context or context()))


def with_command(orders: Iterable[dict[str, Any]], *commands: str) -> list[dict[str, Any]]:
    """The orders of the given commands, in the order they were sent."""
    return [order for order in orders if order["cmd"] in commands]


class StubChooser:
    """A chooser that asks nobody: it notes what it is asked and hands over the results a test gave it, one a poll."""

    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.submitted: list[Any] = []
        self.polled: list[str] = []

    def submit(self, request: Any) -> bool:
        self.submitted.append(request)
        return True

    def poll(self, current_state_tag: str) -> Any:
        self.polled.append(current_state_tag)
        return self.results.pop(0) if self.results else None


class OpeningThen:
    """A plan rule for a test of something other than the plans: OPENING until a war factory has stood, then `plan`."""

    def __init__(self, plan: str) -> None:
        self.plan = plan
        self._is_opening_over = False

    def select(self, world: Any, ctx: Any, choice: Any) -> str:
        self._is_opening_over = self._is_opening_over or ctx.production.standing("weap") > 0
        return self.plan if self._is_opening_over else "OPENING"


class ScriptedManager:
    """A manager whose proposals a test writes: `propose(world, ctx)` gives the intents of a cycle."""

    def __init__(self, name: str, propose: Callable[[Any, Any], Iterable[Intent]] = lambda world, ctx: ()) -> None:
        self.name = name
        self.cycles = 0
        self._propose = propose

    def intents(self, world: Any, ctx: Any) -> list[Intent]:
        self.cycles += 1
        return list(self._propose(world, ctx))


class Recording:
    """An agent wrapped so that what it answers each state with is kept by the state's tick: its orders, and its plan."""

    def __init__(self, agent: RedAlertAgent) -> None:
        self.agent = agent
        self.orders_at: dict[int, list[dict[str, Any]]] = {}
        self.plan_at: dict[int, str] = {}

    def choose_action(self, state: GameState, match_context: DecisionContext) -> Any:
        decision = self.agent.choose_action(state, match_context)
        self.orders_at[state.state_version] = orders_of(decision)
        self.plan_at[state.state_version] = self.agent.current_plan
        return decision

    def on_action_result(self, result: dict[str, Any], match_context: DecisionContext) -> None:
        self.agent.on_action_result(result, match_context)


def new_agent(
    *managers: Any,
    chooser: Any = None,
    tuning: Tuning | None = None,
    settings: RedAlertSettings | None = None,
    **more: Any,
) -> RedAlertAgent:
    """An agent with the tracked item facts, built-in thresholds and settings, a stub chooser and the managers given."""
    return RedAlertAgent(
        items=tracked_items(),
        tuning=tuning or Tuning(),
        chooser=chooser or StubChooser(),
        managers=managers,
        settings=settings or RedAlertSettings(),
        **more,
    )


def own_agent(
    chooser: Any = None,
    tuning: Tuning | None = None,
    settings: RedAlertSettings | None = None,
    plan_selector: Any = None,
) -> RedAlertAgent:
    """An agent as it plays a match, with its own managers, on the tracked item facts and a stub chooser; with its own
    plan rules unless a test gives it a plan selector."""
    return RedAlertAgent(
        items=tracked_items(),
        tuning=tuning or Tuning(),
        chooser=chooser or StubChooser(),
        settings=settings or RedAlertSettings(),
        plan_selector=plan_selector,
    )
