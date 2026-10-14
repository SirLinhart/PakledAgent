"""The feed page's header: where the match stands, from the latest state of its record, and how it ended.

A Red Alert state is in the record either in full or thinned (see
`ucla_agent.core.records`); both give the same figures here. A thinned state
names its trade balance, its explored share and its clock itself; a full one
holds them in the state object. The plan in force and the enemy army value
seen are the agent's own annotations of every state. A figure the record does
not give is None, for the page to show as unknown.

The result is read from the event that closed the record: the `result` event,
scored for the agent as the report tool scores it, or the `match_end` of a
process that never saw one. Other games show their result only.
"""

from __future__ import annotations

from typing import Any

from ucla_agent.core import metrics
from ucla_agent.core.stats import numbers
from ucla_agent.feed.reader import RED_ALERT, TICKS_PER_SECOND

RESULT_KIND = "result"


def header_fields(
    game_type: str | None, latest_state: dict[str, Any] | None, closing: dict[str, Any] | None, agent_id: Any
) -> dict[str, Any]:
    """The header of a match: its game type and result, and for Red Alert the figures of its latest state."""
    fields: dict[str, Any] = {"game_type": game_type, "result": _result(closing, agent_id)}
    if game_type == RED_ALERT:
        fields.update(_red_alert_figures(latest_state or {}))
    return fields


def _red_alert_figures(state: dict[str, Any]) -> dict[str, Any]:
    """The figures of a Red Alert state event's data, whether the state was written in full or thinned."""
    full_state = _mapping(state.get("state"))
    observation = _mapping(full_state.get("observation")) if full_state else state
    clock = _mapping(state.get("time") or full_state.get("time"))
    economy, military = _mapping(observation.get("economy")), _mapping(observation.get("military"))
    return {
        "game_seconds": _game_seconds(clock, state.get("state_version")),
        "remaining_seconds": clock.get("remaining_seconds"),
        "cash": economy.get("cash"),
        "trade_balance": state.get("trade_balance", _trade_balance(military)),
        "army_value": military.get("army_value"),
        "enemy_army_value": state.get("enemy_army_value"),
        "harvesters": economy.get("harvesters"),
        "explored_percent": observation.get("explored_percent"),
        "plan": state.get("plan"),
    }


def _game_seconds(clock: dict[str, Any], state_version: Any) -> float | None:
    """The game clock: the state's own, else its version, which in Red Alert is its tick."""
    if numbers([clock.get("game_seconds")]):
        return clock["game_seconds"]
    return state_version / TICKS_PER_SECOND if numbers([state_version]) else None


def _trade_balance(military: dict[str, Any]) -> float | None:
    """The value destroyed minus the value lost; None when the figures do not give both."""
    kills, deaths = military.get("kills_cost"), military.get("deaths_cost")
    return kills - deaths if len(numbers([kills, deaths])) == 2 else None


def _result(closing: dict[str, Any] | None, agent_id: Any) -> dict[str, Any] | None:
    """How the match ended: its outcome for the agent and the reason; None while the record is open."""
    if closing is None:
        return None
    data = closing["data"]
    if closing["kind"] == RESULT_KIND:
        result = _mapping(data.get("result"))
        return {"outcome": metrics.outcome_of(result, agent_id), "reason": result.get("termination_reason")}
    return {"outcome": metrics.NO_RESULT, "reason": data.get("reason")}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
