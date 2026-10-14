"""Write the synthetic Red Alert match record the metrics tests are checked against.

    python scripts/make_metrics_fixture.py [folder]

Run it from the repository root. Without a folder it rewrites
`tests/fixtures/records/redalert_synthetic/`, whose two files (`events.jsonl`
and `meta.json`) are committed. The record is made up, not played: 200 events
of a 198-second match laid out so that every figure the report tool computes
from it is known in advance. Running the script again writes the same bytes.

What is built in (the constants below say where each one comes from):

- a win by time limit; 40 batches sent, 38 accepted and 2 refused whole;
- the order ledger's final counters: 118 orders sent, rejected
  `{"not_available": 2, "not_ready": 1}`, 1 lost, 2 dropped as invalid,
  0 duplicate production orders;
- 4 decisions, of which 3 fell back (timeout, error, stale);
- low power for 12.0 game seconds, a finished building left unplaced for 4.0;
- money (cash and ore together) at least 1,500 up to 86 game seconds and from
  138, none at all for 8.0 game seconds, and the refinery full of ore for 38.0;
- a final trade balance of 1500 (kills 2900, deaths 1400);
- the plans OPENING, HOLD and RAID, entered at 0, 70 and 120 game seconds.

One state is recorded every 2 game seconds. As in a real record, only some
states are written in full: every fifth one, and two more around the unplaced
building.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ucla_agent.core.console import print_line
from ucla_agent.core.record_files import EVENTS_FILE_NAME, META_FILE_NAME
from ucla_agent.core.secrets import redact

DEFAULT_FOLDER = Path("tests") / "fixtures" / "records" / "redalert_synthetic"
SESSION_ID = "redalert-synthetic"
AGENT_ID = "agent-synthetic"
OPPONENT_ID = "agent-opponent"
# The seat the record is of, as the game names it in every state and as the meta file labels it.
SEAT = "Multi0"
STARTED = datetime(2026, 10, 1, 18, 0, 0, tzinfo=timezone.utc)

# -- the clock: one state every 50 ticks (2 game seconds), 100 states ------------------------------
TICKS_PER_SECOND = 25
MS_PER_TICK = 1000 // TICKS_PER_SECOND
TICKS_PER_STATE = 50
STATE_COUNT = 100
FULL_STATE_EVERY = 5
MATCH_SECONDS_LIMIT = 1200

# -- states: each series is (first state index, value from that state on) --------------------------
POWER_PROVIDED = ((0, 0), (5, 100), (46, 200))
# Drain passes the 100 provided from state 40 to state 45: six states, 12.0 game seconds of low power.
POWER_DRAINED = ((0, 0), (10, 20), (20, 50), (25, 70), (35, 100), (40, 130))
# One harvester is lost at state 70 and replaced at state 75; none exists before state 20.
HARVESTERS = ((0, 0), (20, 1), (50, 2), (70, 1), (75, 2))
# The one refinery stands from state 20 and holds 2,000 ore.
RESOURCE_CAPACITY = ((0, 0), (20, 2000))
# The money is spent down to nothing for states 56 to 59 (8.0 game seconds), and what is earned from state 60 is
# ore up to the refinery's 2,000, which it holds from state 80 to the end: 19 intervals of 2 s, 38.0 game seconds.
MONEY_GONE_STATES = range(56, 60)
EARNING_FROM_STATE = 60
DEATHS_COST = ((0, 0), (60, 300), (70, 1400))
KILLS_COST_BEFORE_THE_LAST_FIGHT = ((0, 0), (55, 600), (65, 1500))
LAST_FIGHT_STATE = 80
FINAL_TRADE_BALANCE = 1500
# (first state index, id, type): the first defense stands at state 25 (50 s), the war factory at state 35 (70 s).
BUILDINGS = (
    (1, 300, "fact"),
    (5, 301, "powr"),
    (10, 302, "tent"),
    (20, 303, "proc"),
    (25, 304, "pbox"),
    (35, 305, "weap"),
    (46, 306, "powr"),
)
# (queue, first state index, last state index, item): when each production queue holds an item.
PRODUCTION = (
    ("Building", 5, 40, "proc"),
    ("Defense", 20, 25, "pbox"),
    ("Infantry", 15, 95, "e1"),
    ("Vehicle", 40, 95, "1tnk"),
)
# The Building queue's item is finished and unplaced in the full states 30 and 31; state 32 is written in
# full as well, so the record shows it placed 4.0 game seconds after it was first seen ready.
READY_UNPLACED_STATES = (30, 31)
EXTRA_FULL_STATES = (31, 32)
ENEMIES_SEEN_FROM_STATE = 50
VISIBLE_ENEMY_COUNT = 3
MAX_INFANTRY = 8

# -- cycles: one batch after every odd state up to state 79 ----------------------------------------
BATCH_COUNT = 40
REFUSED_BATCHES = (12, 30)
SLOW_BATCH = 37
SLOW_ROUND_TRIP_MS = 900.0
WAIT_STATES = (85, 95)
ACTION_DELAY_MS = 10

# -- everything else, as (milliseconds since match start, kind, data) -------------------------------
GAME_CONFIG = {
    "rules": {
        "buildings": {
            "powr": {"cost": 300, "power": 100, "queue": "Building", "prerequisites": []},
            "pbox": {"cost": 600, "power": -20, "queue": "Defense", "prerequisites": ["tent"]},
            "weap": {"cost": 2000, "power": -30, "queue": "Building", "prerequisites": ["proc"]},
        },
        "units": {
            "e1": {"cost": 100, "queue": "Infantry", "prerequisites": ["tent"]},
            "harv": {"cost": 1100, "queue": "Vehicle", "prerequisites": ["proc"]},
        },
        "maps": {"sidestep": {"bounds": [2, 2, 96, 96], "spawns": [[22, 48], [74, 48]]}},
    },
    "match_rules": {"starting_cash": 5000, "starting_units": "none"},
}
RESULT = {
    "session_id": SESSION_ID,
    "is_terminal": True,
    "status": "completed",
    "returns": {AGENT_ID: 1.0, OPPONENT_ID: 0.0},
    "your_return": 1.0,
    "winner_agent_id": AGENT_ID,
    "termination_reason": "time_limit",
    "failure_reason": None,
}
RESULT_MS = 199_000


def _decision(tick: int, source: str, answer: str | None, latency_ms: int, outcome: str) -> dict[str, Any]:
    return {
        "state_version": tick,
        "source": source,
        "request_summary": "plan: HOLD or RAID",
        "answer": answer,
        "latency_ms": latency_ms,
        "outcome": outcome,
    }


def _fallback(intended_source: str, reason: str) -> dict[str, Any]:
    return {"intended_source": intended_source, "reason": reason, "used": "script"}


def _plan_change(tick: int, old: str | None, new: str, rule: str) -> dict[str, Any]:
    game_seconds = tick / TICKS_PER_SECOND
    return {"state_version": tick, "game_seconds": game_seconds, "old": old, "new": new, "rule": rule, "inputs": {}}


def _timing(from_tick: int, to_tick: int, ticks_per_call_mean: float, ticks_per_call_max: int) -> dict[str, Any]:
    return {
        "from_tick": from_tick,
        "to_tick": to_tick,
        "compute_ms": {"mean": 4.5, "max": 8.0},
        "round_trip_ms": {"mean": 260.0, "max": 340.0},
        "state_age_ms": {"mean": 205.0, "max": 480.0},
        "ticks_per_call": {"mean": ticks_per_call_mean, "max": ticks_per_call_max},
    }


def _ledger(orders_sent: int, rejected: dict[str, int], lost: int, dropped_invalid: int) -> dict[str, Any]:
    return {
        "orders_sent": orders_sent,
        "orders_rejected_by_reason": rejected,
        "orders_lost": lost,
        "dropped_invalid": dropped_invalid,
        "duplicate_production_orders": 0,
    }


OTHER_EVENTS = (
    (0, "game_config", GAME_CONFIG),
    (1, "plan_change", _plan_change(0, None, "OPENING", "R1")),
    (31_500, "decision", _decision(750, "local:fast", None, 1500, "timeout")),
    (31_501, "fallback", _fallback("local:fast", "timeout")),
    (50_003, "timing", _timing(0, 1250, 5.0, 8)),
    (50_080, "decision", _decision(1250, "jev", None, 80, "error")),
    (50_081, "fallback", _fallback("jev", "error")),
    (70_002, "plan_change", _plan_change(1750, "OPENING", "HOLD", "R7")),
    (90_005, "error", {"where": "upkeep", "type": "handled", "exception": "KeyError", "message": "'fix'"}),
    (100_003, "timing", _timing(1250, 2500, 6.0, 12)),
    (100_004, "ledger_summary", _ledger(60, {"not_available": 1}, 0, 1)),
    (119_420, "decision", _decision(2975, "local:fast", "RAID", 420, "used")),
    (120_002, "plan_change", _plan_change(3000, "HOLD", "RAID", "model")),
    (150_003, "timing", _timing(2500, 3750, 5.5, 9)),
    (150_900, "decision", _decision(3750, "local:fast", "PRESS", 900, "stale_discarded")),
    (150_901, "fallback", _fallback("local:fast", "stale_discarded")),
    (198_500, "ledger_summary", _ledger(118, {"not_available": 2, "not_ready": 1}, 1, 2)),
)


# -- states ----------------------------------------------------------------------------------------


def _at(index: int, series: tuple[tuple[int, int], ...]) -> int:
    """The value a step series has at a state index."""
    return [value for first_index, value in series if index >= first_index][-1]


def _money(index: int) -> int:
    """Spent down to 600 by state 55 and to nothing for states 56 to 59, earned back from state 60.

    It is at least 1,500 up to state 43 and from state 69.
    """
    if index in MONEY_GONE_STATES:
        return 0
    return max(5000 - 80 * index, 600) if index < EARNING_FROM_STATE else 600 + 100 * (index - EARNING_FROM_STATE)


def _ore(index: int) -> int:
    """What was earned from state 60 on is ore, as far as the refinery holds it."""
    earned = _money(index) - _money(EARNING_FROM_STATE) if index >= EARNING_FROM_STATE else 0
    return min(earned, _at(index, RESOURCE_CAPACITY))


def _economy(index: int) -> dict[str, int]:
    ore = _ore(index)
    return {
        "cash": _money(index) - ore,
        "ore": ore,
        "power_provided": _at(index, POWER_PROVIDED),
        "power_drained": _at(index, POWER_DRAINED),
        "resource_capacity": _at(index, RESOURCE_CAPACITY),
        "harvesters": _at(index, HARVESTERS),
    }


def _military(index: int, final_trade_balance: int) -> dict[str, int]:
    deaths_cost = _at(index, DEATHS_COST)
    kills_cost = _at(index, KILLS_COST_BEFORE_THE_LAST_FIGHT)
    if index >= LAST_FIGHT_STATE:
        # No unit is lost after the last fight, so this is the balance the match ends with.
        kills_cost = deaths_cost + final_trade_balance
    return {
        "kills_cost": kills_cost,
        "deaths_cost": deaths_cost,
        "assets_value": 2000 + 40 * index,
        "army_value": 100 * _infantry_count(index),
    }


def _infantry_count(index: int) -> int:
    return min(index // 10, MAX_INFANTRY)


def _units(index: int) -> list[dict[str, Any]]:
    if index == 0:
        return [{"id": 100, "type": "mcv", "x": 22, "y": 48, "hp": 100, "idle": True}]
    infantry = [
        {"id": 200 + number, "type": "e1", "x": 24 + number, "y": 50, "hp": 100, "idle": False}
        for number in range(_infantry_count(index))
    ]
    harvesters = [
        {"id": 250 + number, "type": "harv", "x": 20, "y": 40 + number, "hp": 100, "idle": False}
        for number in range(_at(index, HARVESTERS))
    ]
    return infantry + harvesters


def _buildings(index: int) -> list[dict[str, Any]]:
    return [
        {"id": building_id, "type": building_type, "x": 20 + position, "y": 46, "hp": 100}
        for position, (first_index, building_id, building_type) in enumerate(BUILDINGS)
        if index >= first_index
    ]


def _production(index: int) -> list[dict[str, Any]]:
    queued = [
        {"queue": queue, "item": item, "progress": 0.5, "remaining_s": 10.0, "paused": False, "ready": False}
        for queue, first_index, last_index, item in PRODUCTION
        if first_index <= index <= last_index
    ]
    if index in READY_UNPLACED_STATES:
        queued[0].update(item="weap", progress=1.0, remaining_s=0.0, ready=True)
    return queued


def _visible_enemies(index: int) -> list[dict[str, Any]]:
    count = VISIBLE_ENEMY_COUNT if index >= ENEMIES_SEEN_FROM_STATE else 0
    return [{"id": 900 + number, "type": "e1", "x": 60 + number, "y": 48, "hp": 100} for number in range(count)]


def _full_state(index: int, final_trade_balance: int) -> dict[str, Any]:
    tick = index * TICKS_PER_STATE
    game_seconds = tick / TICKS_PER_SECOND
    return {
        "session_id": SESSION_ID,
        "game_type": "red_alert",
        "status": "in_progress",
        "state_version": tick,
        "phase": "playing",
        "is_terminal": False,
        "is_current_actor": True,
        "your_seat": SEAT,
        "pacing": {"mode": "realtime"},
        "time": {"game_seconds": game_seconds, "remaining_seconds": MATCH_SECONDS_LIMIT - game_seconds},
        "map": {"id": "sidestep", "width": 100, "height": 100, "bounds": [2, 2, 96, 96]},
        "observation": {
            "tick": tick,
            "faction": "england",
            "enemy_faction": "russia",
            "economy": _economy(index),
            "military": _military(index, final_trade_balance),
            "units": _units(index),
            "buildings": _buildings(index),
            "production": _production(index),
            "available_production": ["powr", "tent", "proc", "weap", "pbox", "e1", "1tnk", "harv"],
            "visible_enemies": _visible_enemies(index),
            "visible_enemy_buildings": [],
            "base_center": [22, 48],
            "events": [],
        },
        "last_orders": None,
        "recent_order_problems": [],
    }


def _state_data(index: int, final_trade_balance: int) -> dict[str, Any]:
    """The data of the `state` event of one state, in full or thinned as the record writer would write it."""
    state = _full_state(index, final_trade_balance)
    data: dict[str, Any] = {"state_version": state["state_version"], "phase": state["phase"]}
    if index % FULL_STATE_EVERY == 0 or index in EXTRA_FULL_STATES:
        data["state"] = state
        return data
    observation = state["observation"]
    data.update(
        economy=observation["economy"],
        military=observation["military"],
        units_count=len(observation["units"]),
        buildings_count=len(observation["buildings"]),
        visible_enemies_count=len(observation["visible_enemies"]),
    )
    return data


# -- cycles ----------------------------------------------------------------------------------------


def compute_ms(batch: int) -> float:
    """The compute time recorded for a batch: 2.0 to 8.0 ms in a fixed pattern."""
    return 2.0 + (batch * 7 % 13) * 0.5


def round_trip_ms(batch: int) -> float:
    """The round trip recorded for a batch: 180 to 340 ms in a fixed pattern, with one slow batch."""
    return SLOW_ROUND_TRIP_MS if batch == SLOW_BATCH else 180.0 + (batch * 11 % 17) * 10.0


def _batch_events(batch: int) -> list[tuple[int, str, dict[str, Any]]]:
    """The `action` and `action_result` events of one batch, sent after an odd state."""
    state_index = 2 * batch + 1
    tick = state_index * TICKS_PER_STATE
    sent_ms = tick * MS_PER_TICK + ACTION_DELAY_MS
    orders = [{"cmd": "train", "item": "e1", "count": 1 + batch % 3}] if batch else [{"cmd": "deploy", "units": [100]}]
    if batch in REFUSED_BATCHES:
        answer: dict[str, Any] = {"error": "INVALID_ACTION", "detail": "[0] train: not_available"}
    else:
        answer = {"accepted": True, "state_version": tick, "pending": True, "accepted_orders": 1, "rejected_orders": 0}
    action = {"state_version": tick, "action": {"type": "orders", "orders": orders}, "compute_ms": compute_ms(batch)}
    result = {"result": answer, "accepted": "error" not in answer, "round_trip_ms": round_trip_ms(batch)}
    return [(sent_ms, "action", action), (sent_ms + int(round_trip_ms(batch)), "action_result", result)]


def _wait_event(state_index: int) -> tuple[int, str, dict[str, Any]]:
    tick = state_index * TICKS_PER_STATE
    return tick * MS_PER_TICK + ACTION_DELAY_MS, "action", {"state_version": tick, "action": "WAIT", "compute_ms": 1.0}


# -- the record ------------------------------------------------------------------------------------


def build_events(final_trade_balance: int = FINAL_TRADE_BALANCE) -> list[dict[str, Any]]:
    """The 200 lines of the synthetic record, in file order. `final_trade_balance` moves only the last fight's kills."""
    events = list(OTHER_EVENTS)
    events += [
        (index * TICKS_PER_STATE * MS_PER_TICK, "state", _state_data(index, final_trade_balance))
        for index in range(STATE_COUNT)
    ]
    for batch in range(BATCH_COUNT):
        events += _batch_events(batch)
    events += [_wait_event(state_index) for state_index in WAIT_STATES]
    events.append((RESULT_MS, "result", {"result": RESULT, "ended_at": _timestamp(RESULT_MS), "dropped_events": 0}))
    # A stable sort on the time alone: events of the same millisecond stay in the order they were added.
    events.sort(key=lambda event: event[0])
    return [
        {"seq": seq, "t": _timestamp(elapsed_ms), "mono_ms": elapsed_ms, "kind": kind, "data": data}
        for seq, (elapsed_ms, kind, data) in enumerate(events)
    ]


def build_meta() -> dict[str, Any]:
    """The meta file of the synthetic record."""
    return {
        "session_id": SESSION_ID,
        "game_type": "red_alert",
        "tournament_id": None,
        "context": "testing",
        "agent_id": AGENT_ID,
        "seat": SEAT,
        "attempt": 1,
        "started_at": _timestamp(0),
        "commit": "0" * 40,
        "config_hashes": {"config/agent.toml": "0" * 64},
        "models": [],
        "decision_services": [],
    }


def write(folder: Path, final_trade_balance: int = FINAL_TRADE_BALANCE) -> None:
    """Write `events.jsonl` and `meta.json` of the synthetic record into `folder`, replacing what is there."""
    folder.mkdir(parents=True, exist_ok=True)
    lines = [_json_line(line) for line in build_events(final_trade_balance)]
    # Bytes, so the files are the same on every platform: no newline translation.
    (folder / EVENTS_FILE_NAME).write_bytes("".join(lines).encode("ascii"))
    (folder / META_FILE_NAME).write_bytes(_json_line(build_meta()).encode("ascii"))


def _json_line(content: dict[str, Any]) -> str:
    return redact(json.dumps(content)) + "\n"


def _timestamp(elapsed_ms: int) -> str:
    moment = STARTED + timedelta(milliseconds=elapsed_ms)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main(arguments: list[str]) -> int:
    """Write the fixture into the folder named on the command line, or into the committed fixture folder."""
    folder = Path(arguments[0]) if arguments else DEFAULT_FOLDER
    write(folder)
    print_line(f"Wrote {EVENTS_FILE_NAME} and {META_FILE_NAME} to {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
