"""Match records written the way the agent writes them, for the feed's tests to read."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STARTED_AT = "2026-10-10T18:00:00.000Z"
AGENT_ID = "local-Multi0"


def event_line(seq: int, kind: str, data: dict[str, Any], *, mono_ms: int | None = None) -> str:
    """One record line as the record writer writes it, newline included."""
    elapsed = seq * 100 if mono_ms is None else mono_ms
    line = {"seq": seq, "t": f"2026-10-10T18:00:{seq % 60:02d}.000Z", "mono_ms": elapsed, "kind": kind, "data": data}
    return json.dumps(line) + "\n"


def meta(game_type: str = "red_alert", *, started_at: str = STARTED_AT, attempt: int = 1) -> dict[str, Any]:
    """A meta file's content for one attempt at a match."""
    return {
        "session_id": "game1-sidestep-1",
        "game_type": game_type,
        "context": "testing",
        "agent_id": AGENT_ID,
        "seat": "Multi0",
        "attempt": attempt,
        "started_at": started_at,
    }


def thinned_state(tick: int, **figures: Any) -> dict[str, Any]:
    """The data of a thinned Red Alert state event, with the header figures; `figures` replace any of them."""
    data = {
        "state_version": tick,
        "phase": "playing",
        "economy": {"cash": 3200, "ore": 150, "harvesters": 2},
        "military": {"kills_cost": 1500, "deaths_cost": 600, "assets_value": 9000, "army_value": 4100},
        "units_count": 14,
        "buildings_count": 9,
        "visible_enemies_count": 3,
        "trade_balance": 900,
        "explored_percent": 23.4,
        "time": {"max_minutes": 20.0, "game_seconds": tick / 25, "remaining_seconds": 1200 - tick / 25},
        "plan": "RAID",
        "enemy_army_value": 2600,
    }
    data.update(figures)
    return data


def model_io(question: str) -> dict[str, Any]:
    """The io of a local model's plan choice: what was sent to the model and what came back."""
    return {
        "system": "You choose the plan of a Red Alert army. Answer with JSON: choice and reason.",
        "user": f"t=180/1020 me=allies en=soviet cash=3200\n{question}\nOptions: HOLD, RAID",
        "response_format": {"type": "json_schema", "json_schema": {"name": "choice"}},
        "params": {"model": "small", "temperature": 0, "max_tokens": 64},
        "raw_output": '{"choice": "RAID", "reason": "their harvesters are open"}',
        "reasoning_text": None,
    }


def plan_decision(tick: int, *, answer: str = "RAID", outcome: str = "used", latency_ms: int = 180) -> dict[str, Any]:
    """The data of a decision event for a Red Alert plan choice made by the small local model."""
    return {
        "state_version": str(tick),
        "source": "local:small",
        "request_summary": "Choose the plan of our Red Alert army. [HOLD, RAID]",
        "answer": answer,
        "latency_ms": latency_ms,
        "outcome": outcome,
        "confidence": None,
        "reason": None if outcome == "used" else outcome,
        "model_reason": "their harvesters are open",
        "io": model_io("Choose the plan of our Red Alert army."),
    }


def red_alert_events() -> list[tuple[str, dict[str, Any]]]:
    """A short Red Alert match as its record holds it: config, states, decisions with io, plan changes, a raid,
    batches with their answers, and the result."""
    return [
        ("game_config", {"game_type": "red_alert"}),
        ("state", thinned_state(4000, plan="HOLD")),
        ("plan_change", {"old": "OPENING", "new": "HOLD", "rule": "R4", "tick": 4000, "game_seconds": 160.0}),
        ("action", {"state_version": 4000, "action": "WAIT", "compute_ms": 0.4, "order_count": 0, "command_count": 0}),
        ("decision", plan_decision(4075)),
        ("state", thinned_state(4100)),
        ("plan_change", {"old": "HOLD", "new": "RAID", "rule": "model", "tick": 4100, "game_seconds": 164.0}),
        (
            "raid",
            {
                "phase": "start",
                "tick": 4110,
                "game_seconds": 164.4,
                "target": 812,
                "target_type": "harv",
                "group_size": 2,
                "value": 1600,
            },
        ),
        (
            "action",
            {
                "state_version": 4105,
                "action": {"type": "orders", "orders": [{"cmd": "move", "units": [301, 302], "to": [40, 22]}]},
                "compute_ms": 1.1,
                "order_count": 1,
                "command_count": 2,
            },
        ),
        ("action_result", {"result": {"accepted": True, "accepted_orders": 1}, "accepted": True, "round_trip_ms": 2}),
        ("decision", plan_decision(4300, answer="PRESS", outcome="timeout", latency_ms=1500)),
        ("fallback", {"intended_source": "local:small", "reason": "timeout", "used": "script"}),
        ("state", thinned_state(4400, economy={"cash": 2800, "ore": 150, "harvesters": 2})),
        ("error", {"where": "redalert.world", "type": "handled", "exception": "KeyError", "message": "'tecn2'"}),
    ]


def append_line(folder: Path, text: str, *, events_file: str = "events.jsonl") -> None:
    """Append text to a match folder's events file, as the record writer does."""
    with open(folder / events_file, "ab") as events:
        events.write(text.encode("utf-8"))
