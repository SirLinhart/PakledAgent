"""The readiness check `fallback`: the Red Alert decision-maker plays on when no model and no service answers.

The decision-maker is made as a match would make it, from the tracked
configuration, with model plan choice on, and with a chooser whose every
decision source raises. It is handed the opening of a match, one MCV on the
map and the game running, and has 100 ms to answer it with a batch the
platform would take. Nothing is sent anywhere and no match record is opened.
"""

from __future__ import annotations

import dataclasses
import time
from collections import Counter
from pathlib import Path
from typing import Any

from altruagent import WAIT, DecisionContext, GameState, WithReasoning
from ucla_agent.check.line import FAIL, PASS, CheckLine
from ucla_agent.core.config import ConfigError
from ucla_agent.core.decide import AsyncChooser, ChoiceRequest, DecisionSource
from ucla_agent.core.decide.journal import SourceCall
from ucla_agent.games.redalert.agent import RedAlertAgent
from ucla_agent.games.redalert.settings import RedAlertSettings

FALLBACK = "fallback"
ANSWER_LIMIT_MS = 100
_MS_PER_SECOND = 1000
CHECK_ID = "readiness-check"
GAME_TYPE = "red_alert"
OWN_SEAT = "Multi0"
# The opening: one MCV at its spawn on a 96-by-96 map, 5,000 credits and nothing else.
MCV_ID = 100
SPAWN = (22, 48)
ENEMY_SPAWN = (74, 48)
MAP_SIZE = 96
MAP_ID = "readiness-arena"
STARTING_CASH = 5000
MCV_VALUE = 2000
MAX_MINUTES = 20
SECONDS_PER_MINUTE = 60
LIMITS = {
    "max_orders_per_batch": 20,
    "max_units_per_order": 50,
    "max_train_count": 10,
    "min_batch_interval_s": 0.2,
    "stale_seconds": 120,
}


class ForcedFailure(DecisionSource):
    """A decision source that is down: every call raises, as when no model or service can be reached."""

    name = "forced_failure"

    def ask(self, request: ChoiceRequest) -> list[SourceCall]:
        """Raise, whatever was asked."""
        raise ConnectionError("every decision source is made to fail by the readiness check")


def opening_state() -> dict[str, Any]:
    """The first state of a Red Alert match as the platform sends it: one MCV at its spawn, phase `playing`."""
    mcv = {"id": MCV_ID, "type": "mcv", "x": SPAWN[0], "y": SPAWN[1], "hp": 100, "act": None, "idle": True}
    observation = {
        "tick": 0,
        "faction": "england",
        "enemy_faction": "russia",
        "economy": {"cash": STARTING_CASH, "ore": 0, "power_provided": 0, "power_drained": 0, "resource_capacity": 0},
        "military": {"kills_cost": 0, "deaths_cost": 0, "assets_value": MCV_VALUE, "army_value": 0},
        "units": [{**mcv, "stance": "attack_anything"}],
        "buildings": [],
        "production": [],
        "available_production": [],
        "visible_enemies": [],
        "visible_enemy_buildings": [],
        "map": {"id": MAP_ID, "width": MAP_SIZE, "height": MAP_SIZE},
        "base_center": list(SPAWN),
        "explored_percent": 0.0,
        "events": [],
    }
    return {
        "session_id": CHECK_ID,
        "game_type": GAME_TYPE,
        "status": "in_progress",
        "state_version": 0,
        "phase": "playing",
        "is_terminal": False,
        "is_current_actor": True,
        "your_seat": OWN_SEAT,
        "limits": dict(LIMITS),
        "time": {
            "max_minutes": MAX_MINUTES,
            "game_seconds": 0.0,
            "remaining_seconds": MAX_MINUTES * SECONDS_PER_MINUTE,
        },
        "map": {"id": MAP_ID, "width": MAP_SIZE, "height": MAP_SIZE, "bounds": [0, 0, MAP_SIZE, MAP_SIZE]},
        "observation": observation,
        "last_orders": None,
        "recent_order_problems": [],
        "legal_actions": {"deploy": [MCV_ID], "idle_units": [MCV_ID], "enemy_base": list(ENEMY_SPAWN)},
    }


def check_fallback(root: Path) -> CheckLine:
    """With every decision source failing, Red Alert answers the opening with a batch within 100 ms."""
    state = opening_state()
    agent = RedAlertAgent(settings=_with_model_plan_choice(root), chooser=AsyncChooser(ForcedFailure(), _never_stale))
    context = DecisionContext(session_id=CHECK_ID, tournament_id=None, game_type=GAME_TYPE, agent_id=CHECK_ID)
    started = time.perf_counter()
    try:
        decision = agent.choose_action(GameState.from_mcp_state(state), context)
    except Exception as exc:  # noqa: BLE001 - a decision-maker that raises is exactly what this check is for
        return CheckLine(FAIL, FALLBACK, f"the Red Alert decision-maker raised {type(exc).__name__}: {exc}")
    elapsed_ms = (time.perf_counter() - started) * _MS_PER_SECOND
    action = decision.action if isinstance(decision, WithReasoning) else decision
    problem = _problem_with(action, state["limits"])
    if problem is not None:
        return CheckLine(FAIL, FALLBACK, f"with every decision source failing, the Red Alert decision-maker {problem}")
    if elapsed_ms > ANSWER_LIMIT_MS:
        return CheckLine(
            FAIL, FALLBACK, f"the Red Alert decision-maker took {elapsed_ms:.1f} ms, over {ANSWER_LIMIT_MS}"
        )
    commands = ", ".join(f"{cmd} {count}" for cmd, count in Counter(order["cmd"] for order in action["orders"]).items())
    return CheckLine(
        PASS,
        FALLBACK,
        f"with every decision source failing, Red Alert answered the opening with {commands} in {elapsed_ms:.1f} ms",
    )


def _with_model_plan_choice(root: Path) -> RedAlertSettings:
    """The tracked Red Alert settings with model plan choice on; the built-in ones, as the agent takes, if unusable."""
    try:
        settings = RedAlertSettings.from_file(root)
    except ConfigError:
        settings = RedAlertSettings()
    return dataclasses.replace(settings, model_plan_choice=True)


def _never_stale(_asked_tag: str, _current_tag: str) -> bool:
    return False


def _problem_with(action: Any, limits: dict[str, int]) -> str | None:
    """What keeps an action from being a batch the platform would take; None when nothing does."""
    if action is WAIT:
        return "answered WAIT"
    orders = action.get("orders") if isinstance(action, dict) and action.get("type") == "orders" else None
    if not isinstance(orders, list) or not 1 <= len(orders) <= limits["max_orders_per_batch"]:
        return f"answered {action!r}, not a batch of 1 to {limits['max_orders_per_batch']} orders"
    if not all(isinstance(order, dict) and isinstance(order.get("cmd"), str) for order in orders):
        return "answered an order that names no command"
    if any(len(order.get("units", ())) > limits["max_units_per_order"] for order in orders):
        return f"answered an order for more than {limits['max_units_per_order']} units"
    return None
