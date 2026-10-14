"""The order ledger: every sent order recorded, and reconciled with what the platform says became of it."""

from __future__ import annotations

import builtins
import json
import socket
import time
from pathlib import Path

import pytest

from altruagent import WAIT, DecisionContext
from fake_game import FakeRedAlert, at, result_object
from ucla_agent.core import records
from ucla_agent.games.redalert.batch import Intent, Limits, Packed, Priority, pack
from ucla_agent.games.redalert.items import ItemTable
from ucla_agent.games.redalert.ledger import LedgerEntry, OrderLedger
from ucla_agent.games.redalert.world import World

REPO_ROOT = Path(__file__).resolve().parents[2]
STATES_FOLDER = REPO_ROOT / "tests" / "fixtures" / "redalert" / "states"
LIMITS = Limits()
START_TICK = 1000
ENEMY_RIFLEMAN = 123
RESULT = {"is_terminal": True, "status": "completed", "returns": {"agent-1": 1.0}, "termination_reason": "time_limit"}
BUILD_POWR = {"cmd": "build", "item": "powr"}
TRAIN_RIFLES = {"cmd": "train", "item": "e1", "count": 2}


def load_state(name: str) -> dict:
    return json.loads((STATES_FOLDER / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def items() -> ItemTable:
    return ItemTable.from_file(REPO_ROOT)


@pytest.fixture(scope="module")
def game_config() -> dict:
    return FakeRedAlert().game_config


def state_at(tick: int, *, last_orders: dict | None = None, problems: tuple[dict, ...] = (), **observation) -> dict:
    """The Allied mid-game fixture state at another tick, with these verdicts and observation fields in place of its own."""
    state = load_state("midgame_allies")
    state["state_version"] = tick
    state["observation"]["tick"] = tick
    state["observation"]["events"] = []
    state["observation"].update(observation)
    state["last_orders"] = last_orders
    state["recent_order_problems"] = list(problems)
    return state


class Cycles:
    """A world and a ledger taken through the cycles of a match by hand."""

    def __init__(self, items: ItemTable, game_config: dict) -> None:
        self.world = World(items, game_config)
        self.ledger = OrderLedger()
        self.see(START_TICK)

    def see(self, tick: int, **state_fields) -> dict:
        """One new state: the world is updated with it and the ledger reconciled."""
        state = state_at(tick, **state_fields)
        self.world.update(state)
        self.ledger.reconcile(self.world, state)
        return state

    def send(self, *orders: dict, priority: Priority = Priority.PRODUCTION, answer: bool = True) -> list[LedgerEntry]:
        """Record a batch of these orders as sent on the current state and, unless told not to, report it accepted."""
        intents = tuple(Intent(priority, order) for order in orders)
        self.ledger.record(Packed(intents, (), 0), self.world, self.world.tick)
        sent = self.ledger.entries[-len(orders) :]
        if answer:
            self.ledger.on_action_result(accepted(*(row(index) for index in range(len(orders)))))
        return sent


@pytest.fixture
def cycles(items: ItemTable, game_config: dict) -> Cycles:
    return Cycles(items, game_config)


def row(index: int, status: str = "pending", reason: str | None = None, cmd: str = "build", **more) -> dict:
    """One result row of the platform."""
    reason = reason or ("pending" if status == "pending" else "ok" if status == "ok" else status)
    return {
        "index": index,
        "cmd": cmd,
        "status": status,
        "reason": reason,
        "detail": None,
        "commands": 1,
        "failed": [],
        **more,
    }


def accepted(*rows: dict) -> dict:
    """The platform's answer to an accepted batch."""
    return {"accepted": True, "status": "in_progress", "pending": True, "results": list(rows)}


def last_orders(batch: int, *rows: dict, pending: bool = False) -> dict:
    """The `last_orders` object of a state for the batch the platform numbers so."""
    return {
        "state_version": 0,
        "batch": batch,
        "carried": 0,
        "pending": pending,
        "applied_at_tick": None,
        "results": list(rows),
    }


def problem(batch: int, index: int, status: str, reason: str, cmd: str = "build") -> dict:
    """One row of a state's `recent_order_problems`."""
    return {"batch": batch, "index": index, "cmd": cmd, "status": status, "reason": reason, "detail": None}


def statuses(entries: list[LedgerEntry]) -> list[tuple]:
    return [(entry.status, entry.reason) for entry in entries]


def counters(ledger: OrderLedger) -> tuple:
    return ledger.orders_sent, dict(ledger.orders_rejected_by_reason), ledger.orders_lost


# -- recording what is sent ------------------------------------------------------------------------


def test_a_new_ledger_has_recorded_nothing():
    ledger = OrderLedger()

    assert (ledger.entries, ledger.orders_sent, ledger.orders_rejected_by_reason) == ([], 0, {})
    assert (ledger.orders_lost, ledger.dropped_invalid) == (0, 0)
    assert (ledger.duplicate_production_orders, ledger.reservations) == (0, [])


def test_every_sent_order_is_recorded_with_batch_number_index_state_version_and_status_pending(cycles):
    first = cycles.send(BUILD_POWR, TRAIN_RIFLES, answer=False)
    cycles.see(START_TICK + 5)
    second = cycles.send({"cmd": "cancel", "item": "fix"}, priority=Priority.POWER, answer=False)

    assert first == [
        LedgerEntry(
            batch=1,
            index=0,
            state_version=START_TICK,
            sent_tick=START_TICK,
            order=BUILD_POWR,
            priority=Priority.PRODUCTION,
        ),
        LedgerEntry(
            batch=1,
            index=1,
            state_version=START_TICK,
            sent_tick=START_TICK,
            order=TRAIN_RIFLES,
            priority=Priority.PRODUCTION,
        ),
    ]
    assert (second[0].batch, second[0].index, second[0].state_version, second[0].priority) == (
        2,
        0,
        1005,
        Priority.POWER,
    )
    assert {entry.status for entry in cycles.ledger.entries} == {"pending"}
    assert (cycles.ledger.orders_sent, len(cycles.ledger.entries)) == (3, 3)


def test_the_state_version_recorded_is_the_one_the_batch_is_sent_with(cycles):
    cycles.ledger.record(Packed((Intent(Priority.PRODUCTION, BUILD_POWR),), (), 0), cycles.world, state_version=41)

    assert (cycles.ledger.entries[0].state_version, cycles.ledger.entries[0].sent_tick) == (41, START_TICK)


def test_a_cycle_that_sends_nothing_takes_no_batch_number_but_its_discarded_intents_are_counted(cycles):
    cycles.ledger.record(Packed((), (), 2), cycles.world, START_TICK)
    cycles.ledger.record(Packed((), (), 1), cycles.world, START_TICK)
    (sent,) = cycles.send(BUILD_POWR)

    assert (cycles.ledger.dropped_invalid, cycles.ledger.orders_sent, sent.batch) == (3, 1, 1)


def test_an_attack_on_a_target_out_of_sight_is_discarded_by_packing_and_counted_by_the_ledger(cycles):
    cycles.see(START_TICK + 5, visible_enemies=[])
    rifles = [unit_id for unit_id, unit in cycles.world.units.items() if unit.type == "e1"]
    attack = Intent(Priority.CONTACT, {"cmd": "attack", "units": rifles, "target": ENEMY_RIFLEMAN})

    packed = pack([attack, Intent(Priority.PRODUCTION, BUILD_POWR)], cycles.world, LIMITS)
    cycles.ledger.record(packed, cycles.world, cycles.world.tick)

    assert cycles.world.enemy_memory[ENEMY_RIFLEMAN].visible is False
    assert cycles.ledger.dropped_invalid == 1
    assert [entry.order for entry in cycles.ledger.entries] == [BUILD_POWR]


# -- the final verdicts of later states ------------------------------------------------------------


def test_a_platform_row_with_status_ok_sets_the_ledger_status_confirmed(cycles):
    (entry,) = cycles.send(BUILD_POWR)

    cycles.see(START_TICK + 5, last_orders=last_orders(1, row(0, "ok")))

    assert (entry.status, entry.reason) == ("confirmed", None)
    assert counters(cycles.ledger) == (1, {}, 0)


@pytest.mark.parametrize(
    "platform_status, reason, ledger_status, counted",
    [
        ("ok", "ok", "confirmed", (1, {}, 0)),
        ("rejected", "not_ready", "rejected", (1, {"not_ready": 1}, 0)),
        ("partial", "not_owned", "partial", (1, {}, 0)),
        ("lost", "not_applied", "lost", (1, {}, 1)),
        ("pending", "pending", "pending", (1, {}, 0)),
    ],
)
def test_the_platform_s_row_statuses_map_to_the_ledger_s(cycles, platform_status, reason, ledger_status, counted):
    (entry,) = cycles.send(BUILD_POWR)

    cycles.see(START_TICK + 5, last_orders=last_orders(1, row(0, platform_status, reason)))

    assert entry.status == ledger_status
    assert entry.reason == (None if ledger_status in ("confirmed", "pending") else reason)
    assert counters(cycles.ledger) == counted


def test_a_pending_row_is_never_taken_for_success(cycles):
    (entry,) = cycles.send(BUILD_POWR)

    for tick in range(START_TICK + 5, START_TICK + 75, 5):
        cycles.see(tick, last_orders=last_orders(1, row(0, "pending"), pending=True))
        assert entry.status == "pending"


def test_the_same_verdict_delivered_in_two_consecutive_states_changes_the_ledger_once(cycles):
    lost, rejected = cycles.send(BUILD_POWR, TRAIN_RIFLES)
    verdict = last_orders(1, row(0, "lost", "not_applied"), row(1, "rejected", "not_ready", cmd="train"))
    problems = (problem(1, 0, "lost", "not_applied"), problem(1, 1, "rejected", "not_ready", cmd="train"))

    cycles.see(START_TICK + 5, last_orders=verdict, problems=problems)
    after_first = (statuses([lost, rejected]), counters(cycles.ledger))
    cycles.see(START_TICK + 10, last_orders=verdict, problems=problems)
    cycles.see(START_TICK + 15, problems=problems)

    assert after_first == ([("lost", "not_applied"), ("rejected", "not_ready")], (2, {"not_ready": 1}, 1))
    assert (statuses([lost, rejected]), counters(cycles.ledger)) == after_first


def test_a_verdict_is_matched_to_its_order_by_batch_and_index(cycles):
    first = cycles.send(BUILD_POWR, TRAIN_RIFLES)
    cycles.see(START_TICK + 5, last_orders=last_orders(1, row(0), row(1), pending=True))
    second = cycles.send({"cmd": "cancel", "item": "fix"}, {"cmd": "build", "item": "tent"})

    cycles.see(
        START_TICK + 10,
        last_orders=last_orders(2, row(0, "ok"), row(1, "rejected", "not_available")),
        problems=(problem(1, 1, "rejected", "not_ready", cmd="train"),),
    )

    assert statuses(first) == [("pending", None), ("rejected", "not_ready")]
    assert statuses(second) == [("confirmed", None), ("rejected", "not_available")]


def test_the_platform_s_batch_number_is_learned_from_the_state_that_first_shows_the_batch(cycles):
    cycles.see(START_TICK + 5, last_orders=last_orders(40, row(0, "rejected", "invalid")))
    (first,) = cycles.send(BUILD_POWR)
    cycles.see(START_TICK + 10, last_orders=last_orders(41, row(0), pending=True))
    (second,) = cycles.send(TRAIN_RIFLES)

    cycles.see(
        START_TICK + 15, last_orders=last_orders(42, row(0, "ok")), problems=(problem(41, 0, "lost", "not_applied"),)
    )

    assert (first.batch, second.batch) == (1, 2)
    assert statuses([first, second]) == [("lost", "not_applied"), ("confirmed", None)]
    assert counters(cycles.ledger) == (2, {}, 1)


def test_rows_of_a_batch_the_ledger_did_not_send_and_rows_without_a_matching_order_change_nothing(cycles):
    (entry,) = cycles.send(BUILD_POWR)

    cycles.see(
        START_TICK + 5,
        last_orders=last_orders(1, row(3, "rejected", "invalid"), row(-1, "lost"), {"status": "lost"}),
        problems=(problem(77, 0, "lost", "not_applied"), problem(1, 9, "rejected", "invalid")),
    )

    assert (entry.status, counters(cycles.ledger)) == ("pending", (1, {}, 0))


def test_a_state_without_verdicts_changes_nothing(cycles):
    (entry,) = cycles.send(BUILD_POWR)

    cycles.see(START_TICK + 5)
    cycles.see(START_TICK + 10, last_orders={"pending": True})

    assert entry.status == "pending"


# -- no verdict in time ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ticks_later, status", [(70, "pending"), (74, "pending"), (75, "confirmed_assumed"), (500, "confirmed_assumed")]
)
def test_an_order_without_a_final_row_after_75_ticks_is_assumed_confirmed(cycles, ticks_later, status):
    (entry,) = cycles.send(BUILD_POWR)

    cycles.see(START_TICK + ticks_later)

    assert (entry.status, entry.reason) == (status, None)
    assert counters(cycles.ledger) == (1, {}, 0)


def test_a_verdict_that_arrives_after_the_assumption_still_counts(cycles):
    lost, rejected, confirmed = cycles.send(BUILD_POWR, TRAIN_RIFLES, {"cmd": "cancel", "item": "fix"})
    cycles.see(START_TICK + 5, last_orders=last_orders(1, row(0), row(1), row(2), pending=True))
    cycles.see(START_TICK + 80)
    assert {entry.status for entry in (lost, rejected, confirmed)} == {"confirmed_assumed"}

    cycles.see(
        START_TICK + 85,
        last_orders=last_orders(1, row(0, "lost", "not_applied"), row(1, "rejected", "not_ready"), row(2, "ok")),
    )
    cycles.see(START_TICK + 400)

    assert statuses([lost, rejected, confirmed]) == [
        ("lost", "not_applied"),
        ("rejected", "not_ready"),
        ("confirmed", None),
    ]
    assert counters(cycles.ledger) == (3, {"not_ready": 1}, 1)


def test_only_orders_still_waiting_are_assumed_confirmed(cycles):
    rejected, waiting = cycles.send(BUILD_POWR, TRAIN_RIFLES)
    cycles.see(START_TICK + 5, last_orders=last_orders(1, row(0, "rejected", "not_available"), row(1), pending=True))

    cycles.see(START_TICK + 75)

    assert statuses([rejected, waiting]) == [("rejected", "not_available"), ("confirmed_assumed", None)]


# -- the platform's immediate answer ---------------------------------------------------------------


def test_the_immediate_answer_ends_what_it_rejects_and_leaves_the_rest_pending(cycles):
    sent = cycles.send(BUILD_POWR, TRAIN_RIFLES, {"cmd": "repair", "building": 103, "on": True}, answer=False)

    cycles.ledger.on_action_result(
        accepted(
            row(0),
            row(1, "rejected", "not_available", cmd="train"),
            row(2, "ok", cmd="repair", detail="already_repairing"),
        )
    )

    assert statuses(sent) == [("pending", None), ("rejected", "not_available"), ("confirmed", None)]
    assert counters(cycles.ledger) == (3, {"not_available": 1}, 0)


def test_an_immediate_rejection_repeated_in_last_orders_is_counted_once(cycles):
    sent = cycles.send(BUILD_POWR, TRAIN_RIFLES, answer=False)
    cycles.ledger.on_action_result(accepted(row(0), row(1, "rejected", "not_available", cmd="train")))

    cycles.see(
        START_TICK + 5, last_orders=last_orders(1, row(0, "ok"), row(1, "rejected", "not_available", cmd="train"))
    )

    assert statuses(sent) == [("confirmed", None), ("rejected", "not_available")]
    assert counters(cycles.ledger) == (2, {"not_available": 1}, 0)


def test_a_batch_refused_for_what_it_held_ends_each_order_with_the_reason_the_platform_names(cycles):
    sent = cycles.send(BUILD_POWR, {"cmd": "move", "units": [1], "to": [5, 5]}, TRAIN_RIFLES, answer=False)
    detail = (
        "No valid order: [0] build: not_available (powr is not in available_production); [1] move: not_owned (no unit)"
    )

    cycles.ledger.on_action_result({"error": "INVALID_ACTION", "detail": detail})

    assert statuses(sent) == [("rejected", "not_available"), ("rejected", "not_owned"), ("rejected", "invalid_action")]
    assert counters(cycles.ledger) == (3, {"not_available": 1, "not_owned": 1, "invalid_action": 1}, 0)


def test_a_batch_refused_for_its_size_ends_every_order_as_rejected_invalid_action(cycles):
    sent = cycles.send(BUILD_POWR, TRAIN_RIFLES, answer=False)

    cycles.ledger.on_action_result({"error": "INVALID_ACTION", "detail": "a batch takes 1-20 orders, got 21"})

    assert statuses(sent) == [("rejected", "invalid_action")] * 2


@pytest.mark.parametrize(
    "error", ["STALE_STATE", "NOT_YOUR_TURN", "RUNTIME_TEMPORARILY_UNAVAILABLE", "GAME_ALREADY_COMPLETE"]
)
def test_a_batch_that_never_arrived_ends_every_order_as_lost(cycles, error):
    sent = cycles.send(BUILD_POWR, TRAIN_RIFLES, answer=False)

    cycles.ledger.on_action_result({"error": error, "detail": "nothing was applied"})

    assert statuses(sent) == [("lost", error.lower())] * 2
    assert counters(cycles.ledger) == (2, {}, 2)


def test_a_refused_batch_gets_no_platform_number_and_the_next_batch_gets_the_next_one(cycles):
    (refused,) = cycles.send(BUILD_POWR, answer=False)
    cycles.ledger.on_action_result({"error": "NOT_YOUR_TURN", "detail": "retry in 150 ms"})
    cycles.see(START_TICK + 5)
    (accepted_next,) = cycles.send(BUILD_POWR)

    cycles.see(START_TICK + 10, last_orders=last_orders(1, row(0, "ok")))

    assert statuses([refused, accepted_next]) == [("lost", "not_your_turn"), ("confirmed", None)]
    assert (refused.batch, accepted_next.batch) == (1, 2)


def test_an_answer_with_nothing_awaiting_one_changes_nothing(cycles):
    cycles.ledger.on_action_result(accepted(row(0, "rejected", "invalid")))
    (entry,) = cycles.send(BUILD_POWR)
    cycles.ledger.on_action_result(accepted(row(0, "rejected", "invalid")))

    assert (entry.status, counters(cycles.ledger)) == ("pending", (1, {}, 0))


def test_without_a_reported_answer_the_next_state_still_gives_the_batch_its_number(cycles):
    (entry,) = cycles.send(BUILD_POWR, answer=False)

    cycles.see(START_TICK + 5, last_orders=last_orders(7, row(0, "rejected", "not_ready")))

    assert (entry.status, entry.reason) == ("rejected", "not_ready")


def test_the_answer_is_written_to_the_match_record_as_an_action_result_event(cycles, run_folder, read_jsonl):
    context = DecisionContext(
        session_id="ledger-session", tournament_id=None, game_type="red_alert", agent_id="agent-1"
    )
    records.start_match(context, None)
    cycles.send(BUILD_POWR, answer=False)
    answer = accepted(row(0))
    refusal = {"error": "INVALID_ACTION", "detail": "No valid order: [0] build: not_available (powr)"}

    cycles.ledger.on_action_result(answer, context)
    cycles.send(BUILD_POWR, answer=False)
    cycles.ledger.on_action_result(refusal, context)
    records.finish_match(RESULT)

    (events_path,) = run_folder.glob("runs/*/ledger-session__seat/events.jsonl")
    events = [line for line in read_jsonl(events_path) if line["kind"] == "action_result"]
    assert [(event["data"]["result"], event["data"]["accepted"]) for event in events] == [
        (answer, True),
        (refusal, False),
    ]


# -- no I/O ----------------------------------------------------------------------------------------


def test_recording_and_reconciling_open_no_file_and_no_connection(cycles, monkeypatch):
    states = [
        state_at(START_TICK + 5, last_orders=last_orders(1, row(0, "ok"), row(1, "lost", "not_applied"))),
        state_at(START_TICK + 100, problems=(problem(1, 1, "lost", "not_applied"),)),
    ]
    calls = []
    real_open = builtins.open
    monkeypatch.setattr(
        builtins, "open", lambda *args, **kwargs: calls.append(("open", args)) or real_open(*args, **kwargs)
    )
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: calls.append(("connect", args)))
    monkeypatch.setattr(time, "sleep", lambda *args: calls.append(("sleep", args)))

    cycles.send(BUILD_POWR, TRAIN_RIFLES)
    for state in states:
        cycles.world.update(state)
        cycles.ledger.reconcile(cycles.world, state)

    assert calls == [] and counters(cycles.ledger) == (2, {}, 1)


# -- production reservations -----------------------------------------------------------------------

NOTHING_QUEUED: list[dict] = []


def job(queue: str, item: str, progress: float = 0.1, ready: bool = False) -> dict:
    """One entry of a state's `production` list."""
    return {"queue": queue, "item": item, "progress": progress, "remaining_s": 5.0, "paused": False, "ready": ready}


def build(item: str, priority: Priority = Priority.PRODUCTION) -> Intent:
    return Intent(priority, {"cmd": "build", "item": item})


def train(item: str, count: int = 1, priority: Priority = Priority.PRODUCTION) -> Intent:
    return Intent(priority, {"cmd": "train", "item": item, "count": count})


def sent_orders(cycles: Cycles, *intents: Intent) -> list[dict]:
    """One whole cycle on the current state: admit, pack, record, the batch reported accepted. Returns what was sent."""
    packed = pack(cycles.ledger.admit(intents, cycles.world), cycles.world, LIMITS)
    cycles.ledger.record(packed, cycles.world, cycles.world.tick)
    if packed.orders:
        cycles.ledger.on_action_result(accepted(*(row(index) for index in range(len(packed.orders)))))
    return list(packed.orders)


@pytest.fixture
def idle_base(cycles: Cycles) -> Cycles:
    """The cycles of a match whose production queues are all empty at the start tick."""
    cycles.see(START_TICK, production=NOTHING_QUEUED)
    return cycles


def test_a_sent_build_or_train_opens_a_reservation_with_queue_item_count_and_cost(idle_base):
    sent_orders(idle_base, build("powr"), train("e1", 3), build("pbox"))

    held = [(r.queue, r.item, r.count, r.cost, r.held) for r in idle_base.ledger.reservations]
    assert held == [
        ("Building", "powr", 1, 300, ("build", "Building")),
        ("Infantry", "e1", 3, 300, ("train", "e1")),
        ("Defense", "pbox", 1, 600, ("build", "Defense")),
    ]
    assert [r.entry for r in idle_base.ledger.reservations] == idle_base.ledger.entries
    assert idle_base.ledger.duplicate_production_orders == 0


def test_no_second_building_queue_order_goes_out_before_the_first_shows_in_production(idle_base):
    first = sent_orders(idle_base, build("powr"))
    idle_base.see(START_TICK + 5, production=NOTHING_QUEUED)

    second = sent_orders(idle_base, build("tent"))

    assert first == [{"cmd": "build", "item": "powr"}] and second == []
    assert idle_base.ledger.duplicate_production_orders == 0
    assert [entry.order["item"] for entry in idle_base.ledger.entries] == ["powr"]


def test_the_reservation_resolves_once_the_item_appears_in_production_for_its_queue(idle_base):
    sent_orders(idle_base, build("powr"))
    idle_base.see(START_TICK + 5, production=NOTHING_QUEUED)
    held_back = sent_orders(idle_base, build("tent"))

    idle_base.see(START_TICK + 10, production=[job("Building", "powr")])
    allowed = sent_orders(idle_base, build("tent"))

    assert (held_back, allowed) == ([], [{"cmd": "build", "item": "tent"}])
    assert [r.item for r in idle_base.ledger.reservations] == ["tent"]
    assert idle_base.ledger.duplicate_production_orders == 0


@pytest.mark.parametrize("ticks_later, allowed", [(70, False), (74, False), (75, True)])
def test_the_reservation_resolves_75_ticks_after_sending_when_the_item_never_appears(idle_base, ticks_later, allowed):
    sent_orders(idle_base, build("powr"))

    idle_base.see(START_TICK + ticks_later, production=NOTHING_QUEUED)
    second = sent_orders(idle_base, build("tent"))

    assert (second == [{"cmd": "build", "item": "tent"}]) is allowed
    assert idle_base.ledger.duplicate_production_orders == 0


def test_an_item_in_another_queue_s_production_does_not_resolve_the_reservation(idle_base):
    sent_orders(idle_base, build("powr"))

    idle_base.see(START_TICK + 5, production=[job("Defense", "powr"), job("Building", "tent")])

    assert [r.item for r in idle_base.ledger.reservations] == ["powr"]


def test_a_created_actor_of_the_item_resolves_the_reservation(idle_base):
    sent_orders(idle_base, train("e1"), train("e3"))
    created = {"seq": 1, "tick": START_TICK + 3, "kind": "unit_created", "id": 900, "type": "E1", "x": 21, "y": 49}
    lost = {"seq": 2, "tick": START_TICK + 3, "kind": "unit_lost", "id": 901, "type": "e3", "x": 21, "y": 49}

    idle_base.see(START_TICK + 5, production=NOTHING_QUEUED, events=[created, lost])

    assert [r.item for r in idle_base.ledger.reservations] == ["e3"]


def test_the_state_an_order_was_made_from_seen_again_does_not_resolve_its_reservation(cycles):
    cycles.see(START_TICK, production=[job("Infantry", "e1", progress=0.9)])
    sent_orders(cycles, train("e3"), build("powr"))
    sent_before = len(cycles.ledger.entries)

    cycles.see(START_TICK, production=[job("Infantry", "e3"), job("Building", "powr")])
    again = sent_orders(cycles, train("e3"), build("powr"))

    assert (again, len(cycles.ledger.entries), sent_before) == ([], 2, 2)
    assert len(cycles.ledger.reservations) == 2


@pytest.mark.parametrize("verdict", ["rejected", "lost"])
def test_a_rejected_or_lost_production_order_frees_its_reservation_at_once(idle_base, verdict):
    sent_orders(idle_base, build("powr"))

    idle_base.see(
        START_TICK + 5, production=NOTHING_QUEUED, last_orders=last_orders(1, row(0, verdict, "not_available"))
    )
    second = sent_orders(idle_base, build("tent"))

    assert second == [{"cmd": "build", "item": "tent"}]
    assert [r.item for r in idle_base.ledger.reservations] == ["tent"]


def test_an_order_rejected_in_the_immediate_answer_frees_its_reservation_for_the_next_cycle(idle_base):
    packed = pack(idle_base.ledger.admit([build("powr")], idle_base.world), idle_base.world, LIMITS)
    idle_base.ledger.record(packed, idle_base.world, START_TICK)
    idle_base.ledger.on_action_result(accepted(row(0, "rejected", "not_available")))

    idle_base.see(START_TICK + 5, production=NOTHING_QUEUED)

    assert idle_base.ledger.reservations == [] and sent_orders(idle_base, build("tent")) == [
        {"cmd": "build", "item": "tent"}
    ]


def test_a_build_holds_its_own_queue_only_and_a_train_its_own_item_only(idle_base):
    sent_orders(idle_base, build("powr"), train("e1", 2))
    idle_base.see(START_TICK + 5, production=NOTHING_QUEUED)

    second = sent_orders(idle_base, build("tent"), build("pbox"), train("e1"), train("e3"), train("1tnk"))

    assert second == [
        {"cmd": "build", "item": "pbox"},
        {"cmd": "train", "item": "e3", "count": 1},
        {"cmd": "train", "item": "1tnk", "count": 1},
    ]
    assert idle_base.ledger.duplicate_production_orders == 0


def test_of_two_builds_for_one_queue_in_one_cycle_the_more_urgent_goes_out(idle_base):
    sent = sent_orders(idle_base, build("tent"), build("powr", Priority.POWER), build("gun"), build("pbox"))

    assert sent == [{"cmd": "build", "item": "powr"}, {"cmd": "build", "item": "gun"}]
    assert idle_base.ledger.duplicate_production_orders == 0


def test_of_two_trains_of_one_item_in_one_cycle_the_first_goes_out(idle_base):
    sent = sent_orders(idle_base, train("e1", 3), train("e1", 5), train("E1"), train("e3"))

    assert sent == [{"cmd": "train", "item": "e1", "count": 3}, {"cmd": "train", "item": "e3", "count": 1}]


def test_an_intent_for_an_item_that_is_not_on_offer_holds_nothing_back(idle_base):
    sent = sent_orders(idle_base, build("barr", Priority.POWER), build("powr"))

    assert sent == [{"cmd": "build", "item": "powr"}]
    assert (idle_base.ledger.dropped_invalid, [r.item for r in idle_base.ledger.reservations]) == (1, ["powr"])


def test_intents_that_are_not_production_orders_pass_admission_in_priority_order(idle_base):
    rifles = [unit_id for unit_id, unit in idle_base.world.units.items() if unit.type == "e1"]
    march = Intent(Priority.MOVE, {"cmd": "attack_move", "units": rifles, "to": [40, 48]})
    place = Intent(Priority.PLACEMENT, {"cmd": "place", "item": "pbox"})
    repair = Intent(Priority.PRODUCTION, {"cmd": "repair", "building": 103, "on": True})
    proposed = [march, repair, build("powr"), place]

    admitted = idle_base.ledger.admit(proposed, idle_base.world)

    assert admitted == [place, repair, build("powr"), march]
    assert proposed == [march, repair, build("powr"), place] and idle_base.ledger.reservations == []


def test_admission_alone_reserves_nothing(idle_base):
    idle_base.ledger.admit([build("powr")], idle_base.world)

    assert idle_base.ledger.admit([build("tent")], idle_base.world) == [build("tent")]


def test_a_production_order_sent_past_admission_while_one_is_unresolved_is_counted_as_a_duplicate(idle_base):
    idle_base.send({"cmd": "build", "item": "powr"}, {"cmd": "train", "item": "e1", "count": 2})
    idle_base.see(START_TICK + 5, production=NOTHING_QUEUED)

    idle_base.send({"cmd": "build", "item": "tent"}, {"cmd": "train", "item": "e1"}, {"cmd": "train", "item": "e3"})

    assert idle_base.ledger.duplicate_production_orders == 2
    assert [r.item for r in idle_base.ledger.reservations] == ["powr", "e1", "tent", "e1", "e3"]


def test_an_item_the_table_does_not_know_has_a_queue_of_its_own_and_no_cost(idle_base):
    idle_base.see(START_TICK, production=NOTHING_QUEUED, available_production=["powr", "prototype"])

    sent = sent_orders(idle_base, build("prototype"), build("powr"))

    assert sent == [{"cmd": "build", "item": "prototype"}, {"cmd": "build", "item": "powr"}]
    assert [(r.queue, r.cost) for r in idle_base.ledger.reservations] == [("prototype", 0), ("Building", 300)]


# -- a full queue ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ticks_after_the_rejection, blocked", [(0, True), (45, True), (49, True), (50, False), (200, False)]
)
def test_a_train_rejected_not_ready_blocks_its_queue_for_50_ticks(idle_base, ticks_after_the_rejection, blocked):
    sent_orders(idle_base, train("e1", 5))
    rejected_at = START_TICK + 5
    idle_base.see(
        rejected_at, production=NOTHING_QUEUED, last_orders=last_orders(1, row(0, "rejected", "not_ready", cmd="train"))
    )
    at_once = sent_orders(idle_base, train("e3"))

    idle_base.see(rejected_at + ticks_after_the_rejection, production=NOTHING_QUEUED)
    later = sent_orders(idle_base, train("e3"), train("medi"))

    assert at_once == []
    assert (later == []) is blocked
    assert blocked or later == [
        {"cmd": "train", "item": "e3", "count": 1},
        {"cmd": "train", "item": "medi", "count": 1},
    ]


def test_a_blocked_queue_does_not_hold_back_the_other_queues(idle_base):
    sent_orders(idle_base, train("e1"))
    idle_base.see(
        START_TICK + 5,
        production=NOTHING_QUEUED,
        last_orders=last_orders(1, row(0, "rejected", "not_ready", cmd="train")),
    )

    sent = sent_orders(idle_base, train("e3"), train("1tnk"), build("powr"), build("pbox"))

    assert sent == [
        {"cmd": "train", "item": "1tnk", "count": 1},
        {"cmd": "build", "item": "powr"},
        {"cmd": "build", "item": "pbox"},
    ]


def test_a_build_rejected_not_ready_blocks_the_building_queue(idle_base):
    sent_orders(idle_base, build("powr"))
    idle_base.see(
        START_TICK + 5, production=NOTHING_QUEUED, last_orders=last_orders(1, row(0, "rejected", "not_ready"))
    )

    blocked = sent_orders(idle_base, build("tent"), build("pbox"))
    idle_base.see(START_TICK + 55, production=NOTHING_QUEUED)
    free = sent_orders(idle_base, build("tent"))

    assert blocked == [{"cmd": "build", "item": "pbox"}] and free == [{"cmd": "build", "item": "tent"}]


@pytest.mark.parametrize("reason", ["not_available", "invalid", "unknown_actor"])
def test_a_rejection_for_another_reason_blocks_nothing(idle_base, reason):
    sent_orders(idle_base, train("e1"))

    idle_base.see(
        START_TICK + 5, production=NOTHING_QUEUED, last_orders=last_orders(1, row(0, "rejected", reason, cmd="train"))
    )

    assert sent_orders(idle_base, train("e3")) == [{"cmd": "train", "item": "e3", "count": 1}]


def test_a_place_rejected_not_ready_blocks_no_queue(cycles):
    cycles.send({"cmd": "place", "item": "pbox"})

    cycles.see(
        START_TICK + 5,
        production=NOTHING_QUEUED,
        last_orders=last_orders(1, row(0, "rejected", "not_ready", cmd="place")),
    )

    assert sent_orders(cycles, build("pbox"), build("powr")) == [
        {"cmd": "build", "item": "pbox"},
        {"cmd": "build", "item": "powr"},
    ]


# -- a lost order is proposed again, once ----------------------------------------------------------


def rifles_of(cycles: Cycles) -> list[int]:
    return [unit_id for unit_id, unit in cycles.world.units.items() if unit.type == "e1"]


def test_a_lost_verdict_proposes_the_order_again_exactly_once_at_its_original_priority(idle_base):
    retreat = {"cmd": "move", "units": rifles_of(idle_base), "to": [22, 48]}
    idle_base.send(retreat, priority=Priority.EMERGENCY)

    idle_base.see(
        START_TICK + 5, production=NOTHING_QUEUED, last_orders=last_orders(1, row(0, "lost", "not_applied", cmd="move"))
    )
    first = idle_base.ledger.admit([], idle_base.world)
    second = idle_base.ledger.admit([], idle_base.world)
    idle_base.see(
        START_TICK + 10,
        production=NOTHING_QUEUED,
        last_orders=last_orders(1, row(0, "lost", "not_applied", cmd="move")),
    )
    third = idle_base.ledger.admit([], idle_base.world)

    assert first == [Intent(Priority.EMERGENCY, retreat, resend=True)]
    assert (second, third) == ([], [])
    assert idle_base.ledger.orders_lost == 1


def test_an_order_lost_a_second_time_is_counted_and_not_proposed_a_third_time(idle_base):
    sent_orders(idle_base, build("powr", Priority.POWER))
    idle_base.see(START_TICK + 5, production=NOTHING_QUEUED, last_orders=last_orders(1, row(0, "lost", "not_applied")))
    resent = sent_orders(idle_base)
    entries = idle_base.ledger.entries

    idle_base.see(START_TICK + 10, production=NOTHING_QUEUED, last_orders=last_orders(2, row(0, "lost", "not_applied")))
    after_the_second_loss = sent_orders(idle_base)

    assert resent == [{"cmd": "build", "item": "powr"}] and after_the_second_loss == []
    assert [(entry.batch, entry.resend, entry.priority, entry.status) for entry in entries] == [
        (1, False, Priority.POWER, "lost"),
        (2, True, Priority.POWER, "lost"),
    ]
    assert (
        idle_base.ledger.orders_lost,
        idle_base.ledger.orders_sent,
        idle_base.ledger.duplicate_production_orders,
    ) == (2, 2, 0)


def test_the_orders_of_a_batch_that_never_arrived_are_all_proposed_again_once(idle_base):
    march = {"cmd": "attack_move", "units": rifles_of(idle_base), "to": [40, 48]}
    idle_base.send(march, priority=Priority.MOVE, answer=False)
    idle_base.ledger.on_action_result(
        {"error": "RUNTIME_TEMPORARILY_UNAVAILABLE", "detail": "the game server is restarting"}
    )
    idle_base.send({"cmd": "build", "item": "powr"}, answer=False)
    idle_base.ledger.on_action_result({"error": "NOT_YOUR_TURN", "detail": "retry in 120 ms"})

    idle_base.see(START_TICK + 5, production=NOTHING_QUEUED)
    again = sent_orders(idle_base)

    assert again == [{"cmd": "build", "item": "powr"}, march]
    assert [entry.resend for entry in idle_base.ledger.entries] == [False, False, True, True]
    assert sent_orders(idle_base) == []


def test_a_batch_refused_for_what_it_held_is_not_proposed_again(idle_base):
    idle_base.send({"cmd": "build", "item": "powr"}, answer=False)
    idle_base.ledger.on_action_result(
        {"error": "INVALID_ACTION", "detail": "No valid order: [0] build: not_available (powr)"}
    )

    idle_base.see(START_TICK + 5, production=NOTHING_QUEUED)

    assert idle_base.ledger.admit([], idle_base.world) == []


def test_a_fresh_proposal_of_a_lost_production_order_and_its_resend_give_one_order(idle_base):
    sent_orders(idle_base, build("powr"))
    idle_base.see(START_TICK + 5, production=NOTHING_QUEUED, last_orders=last_orders(1, row(0, "lost", "not_applied")))

    sent = sent_orders(idle_base, build("powr"))

    assert sent == [{"cmd": "build", "item": "powr"}]
    assert (idle_base.ledger.entries[-1].resend, idle_base.ledger.duplicate_production_orders) == (False, 0)


def test_a_fresh_order_for_the_units_of_a_lost_order_wins_over_its_resend(idle_base):
    rifles = rifles_of(idle_base)
    idle_base.send({"cmd": "attack_move", "units": rifles, "to": [70, 48]}, priority=Priority.MOVE)
    idle_base.see(START_TICK + 5, production=NOTHING_QUEUED, last_orders=last_orders(1, row(0, "lost", "not_applied")))
    regroup = Intent(Priority.MOVE, {"cmd": "move", "units": rifles[:4], "to": [30, 48]})

    sent = sent_orders(idle_base, regroup)

    assert sent == [regroup.order, {"cmd": "attack_move", "units": rifles[4:], "to": [70, 48]}]


def test_a_resend_is_checked_against_the_new_world_like_any_intent(idle_base):
    rifles = rifles_of(idle_base)
    idle_base.send(
        {"cmd": "move", "units": rifles[:1], "to": [40, 48]},
        {"cmd": "stop", "units": rifles[1:]},
        priority=Priority.MOVE,
    )
    survivors = [unit for unit in load_state("midgame_allies")["observation"]["units"] if unit["id"] != rifles[0]]
    verdict = last_orders(1, row(0, "lost", "not_applied"), row(1, "lost", "not_applied"))

    idle_base.see(START_TICK + 5, production=NOTHING_QUEUED, last_orders=verdict, units=survivors)
    sent = sent_orders(idle_base)

    assert sent == [{"cmd": "stop", "units": rifles[1:]}]
    assert idle_base.ledger.dropped_invalid == 1


def test_an_intent_is_no_resend_unless_the_ledger_marks_it():
    assert Intent(Priority.MOVE, {"cmd": "stop", "units": [1]}).resend is False
    assert Intent(Priority.MOVE, {"cmd": "stop", "units": [1]}) != Intent(
        Priority.MOVE, {"cmd": "stop", "units": [1]}, resend=True
    )


# -- what packing deferred is carried into the next cycle ------------------------------------------


def test_intents_that_did_not_fit_are_proposed_again_in_the_next_cycle(idle_base):
    rifles = rifles_of(idle_base)
    one_order_a_batch = Limits(max_orders_per_batch=1)
    retreat = Intent(Priority.EMERGENCY, {"cmd": "move", "units": rifles[:2], "to": [22, 48]})
    place = Intent(Priority.PLACEMENT, {"cmd": "repair", "building": 103, "on": True})
    scout = Intent(Priority.SCOUT, {"cmd": "move", "units": rifles[2:3], "to": [80, 20]})

    sent = []
    for proposed in ([scout, place, retreat], [], [], []):
        packed = pack(idle_base.ledger.admit(proposed, idle_base.world), idle_base.world, one_order_a_batch)
        idle_base.ledger.record(packed, idle_base.world, idle_base.world.tick)
        sent.append(list(packed.orders))
        idle_base.see(idle_base.world.tick + 5, production=NOTHING_QUEUED)

    assert sent == [[retreat.order], [place.order], [scout.order], []]
    assert [(entry.batch, entry.priority) for entry in idle_base.ledger.entries] == [
        (1, Priority.EMERGENCY),
        (2, Priority.PLACEMENT),
        (3, Priority.SCOUT),
    ]


def test_a_carried_intent_and_a_fresh_one_that_says_the_same_give_one_order(cycles):
    place = Intent(Priority.PLACEMENT, {"cmd": "place", "item": "pbox", "at": [26, 50]})
    cancel = Intent(Priority.PRODUCTION, {"cmd": "cancel", "item": "fix"})
    packed = pack(cycles.ledger.admit([place, cancel], cycles.world), cycles.world, Limits(max_orders_per_batch=0))
    cycles.ledger.record(packed, cycles.world, START_TICK)
    assert (packed.orders, list(packed.deferred)) == ([], [place, cancel])

    sent = sent_orders(cycles, place, cancel)

    assert sent == [place.order, cancel.order]


def test_a_fresh_order_for_a_unit_wins_over_a_carried_one_of_the_same_priority(idle_base):
    rifles = rifles_of(idle_base)
    old = Intent(Priority.SCOUT, {"cmd": "move", "units": rifles[:2], "to": [70, 48]})
    packed = pack(idle_base.ledger.admit([old], idle_base.world), idle_base.world, Limits(max_orders_per_batch=0))
    idle_base.ledger.record(packed, idle_base.world, START_TICK)
    new = Intent(Priority.SCOUT, {"cmd": "move", "units": rifles[:1], "to": [30, 48]})

    sent = sent_orders(idle_base, new)

    assert sent == [new.order, {"cmd": "move", "units": rifles[1:2], "to": [70, 48]}]


def test_a_carried_intent_is_dropped_once_the_world_no_longer_allows_it(cycles):
    place = Intent(Priority.PLACEMENT, {"cmd": "place", "item": "pbox"})
    packed = pack(cycles.ledger.admit([place], cycles.world), cycles.world, Limits(max_orders_per_batch=0))
    cycles.ledger.record(packed, cycles.world, START_TICK)

    cycles.see(START_TICK + 5, production=NOTHING_QUEUED)
    sent = sent_orders(cycles)

    assert sent == [] and cycles.ledger.dropped_invalid == 1
    assert cycles.ledger.admit([], cycles.world) == []


def test_two_proposals_of_one_cycle_that_say_exactly_the_same_give_one_intent(idle_base):
    repair = {"cmd": "repair", "building": 103, "on": True}

    admitted = idle_base.ledger.admit(
        [
            Intent(Priority.PRODUCTION, repair),
            Intent(Priority.EMERGENCY, dict(repair)),
            Intent(Priority.PRODUCTION, {**repair, "on": False}),
        ],
        idle_base.world,
    )

    assert admitted == [Intent(Priority.EMERGENCY, repair), Intent(Priority.PRODUCTION, {**repair, "on": False})]


# -- a placement the game finds no cell for --------------------------------------------------------

READY_PILLBOX = [
    {"queue": "Defense", "item": "pbox", "progress": 1.0, "remaining_s": 0.0, "paused": False, "ready": True}
]
PLACE_AT = {"cmd": "place", "item": "pbox", "at": [26, 50]}
PLACE_ANYWHERE = {"cmd": "place", "item": "pbox"}
CANCEL_PILLBOX = {"cmd": "cancel", "item": "pbox"}
INVALID = ("rejected", "invalid")


def place_wanted() -> Intent:
    """The `place` a manager proposes in every cycle in which the pillbox waits."""
    return Intent(Priority.PLACEMENT, dict(PLACE_AT))


def verdict(batch: int, status: str, reason: str) -> dict:
    return last_orders(batch, row(0, status, reason, cmd="place"))


@pytest.fixture
def refused_once(cycles: Cycles) -> Cycles:
    """A match in which the first `place` of the waiting pillbox was sent at the start tick and rejected `invalid` 5 ticks later."""
    cycles.see(START_TICK, production=READY_PILLBOX)
    assert sent_orders(cycles, place_wanted()) == [PLACE_AT]
    cycles.see(START_TICK + 5, production=READY_PILLBOX, last_orders=verdict(1, *INVALID))
    return cycles


def test_after_an_invalid_place_no_place_of_the_item_goes_out_for_50_ticks(refused_once):
    sent = []
    for tick in range(START_TICK + 5, START_TICK + 55, 5):
        refused_once.see(tick, production=READY_PILLBOX)
        sent += sent_orders(refused_once, place_wanted(), Intent(Priority.PLACEMENT, dict(PLACE_ANYWHERE)))

    assert sent == []
    assert [entry.order for entry in refused_once.ledger.entries] == [PLACE_AT]


def test_the_place_is_retried_once_without_a_cell_50_ticks_after_the_rejection(refused_once):
    refused_once.see(START_TICK + 50, production=READY_PILLBOX)
    too_early = sent_orders(refused_once, place_wanted())

    refused_once.see(START_TICK + 55, production=READY_PILLBOX)
    retry = sent_orders(refused_once, place_wanted())
    refused_once.see(
        START_TICK + 60, production=READY_PILLBOX, last_orders=last_orders(2, row(0, cmd="place"), pending=True)
    )
    while_the_retry_is_in_flight = sent_orders(refused_once, place_wanted())

    assert (too_early, retry, while_the_retry_is_in_flight) == ([], [PLACE_ANYWHERE], [])
    assert "at" not in refused_once.ledger.entries[-1].order
    assert refused_once.ledger.entries[-1].priority == Priority.PLACEMENT


def test_the_retry_goes_out_even_when_nobody_proposes_the_place_any_more(refused_once):
    refused_once.see(START_TICK + 55, production=READY_PILLBOX)

    assert sent_orders(refused_once) == [PLACE_ANYWHERE]


def test_a_second_invalid_gives_one_cancel_one_handled_error_event_and_no_third_place(
    refused_once, run_folder, read_jsonl
):
    context = DecisionContext(session_id="place-session", tournament_id=None, game_type="red_alert", agent_id="agent-1")
    records.start_match(context, None)
    refused_once.see(START_TICK + 55, production=READY_PILLBOX)
    assert sent_orders(refused_once, place_wanted()) == [PLACE_ANYWHERE]

    refused_once.see(START_TICK + 60, production=READY_PILLBOX, last_orders=verdict(2, *INVALID))
    after_the_second_refusal = sent_orders(refused_once, place_wanted())
    later = []
    for tick in range(START_TICK + 65, START_TICK + 400, 5):
        refused_once.see(tick, production=READY_PILLBOX, last_orders=verdict(2, *INVALID))
        later += sent_orders(refused_once, place_wanted())
    records.finish_match(RESULT)

    (events_path,) = run_folder.glob("runs/*/place-session__seat/events.jsonl")
    errors = [line["data"] for line in read_jsonl(events_path) if line["kind"] == "error"]
    assert after_the_second_refusal == [CANCEL_PILLBOX] and later == []
    assert [entry.order for entry in refused_once.ledger.entries] == [PLACE_AT, PLACE_ANYWHERE, CANCEL_PILLBOX]
    assert refused_once.ledger.entries[-1].priority == Priority.PRODUCTION
    assert errors == [
        {
            "where": "redalert.ledger",
            "type": "handled",
            "exception": "PlacementRefused",
            "message": "The game found no cell for pbox twice; the item is cancelled",
        }
    ]
    assert refused_once.ledger.orders_rejected_by_reason == {"invalid": 2}


def test_once_the_cancelled_item_is_gone_a_new_one_of_its_type_can_be_placed_again(refused_once):
    refused_once.see(START_TICK + 55, production=READY_PILLBOX)
    sent_orders(refused_once, place_wanted())
    refused_once.see(START_TICK + 60, production=READY_PILLBOX, last_orders=verdict(2, *INVALID))
    assert sent_orders(refused_once, place_wanted()) == [CANCEL_PILLBOX]

    refused_once.see(START_TICK + 65, production=NOTHING_QUEUED, last_orders=last_orders(3, row(0, "ok", cmd="cancel")))
    nothing_waits = sent_orders(refused_once)
    refused_once.see(START_TICK + 500, production=READY_PILLBOX)
    rebuilt = sent_orders(refused_once, place_wanted())

    assert (nothing_waits, rebuilt) == ([], [PLACE_AT])


def test_a_retry_that_places_the_building_ends_the_matter(refused_once):
    refused_once.see(START_TICK + 55, production=READY_PILLBOX)
    sent_orders(refused_once, place_wanted())

    refused_once.see(START_TICK + 60, production=READY_PILLBOX, last_orders=verdict(2, "ok", "ok"))
    second_pillbox = sent_orders(refused_once, place_wanted())

    assert second_pillbox == [PLACE_AT]
    assert refused_once.ledger.orders_rejected_by_reason == {"invalid": 1}


def test_a_retry_that_is_lost_is_sent_again_without_a_cell(refused_once):
    refused_once.see(START_TICK + 55, production=READY_PILLBOX)
    sent_orders(refused_once, place_wanted())

    refused_once.see(START_TICK + 60, production=READY_PILLBOX, last_orders=verdict(2, "lost", "not_applied"))
    again = sent_orders(refused_once, place_wanted())
    refused_once.see(START_TICK + 65, production=READY_PILLBOX, last_orders=verdict(3, *INVALID))
    given_up = sent_orders(refused_once, place_wanted())

    assert (again, given_up) == ([PLACE_ANYWHERE], [CANCEL_PILLBOX])
    assert [entry.resend for entry in refused_once.ledger.entries] == [False, False, False, False]


def test_an_item_that_stops_waiting_before_the_retry_is_due_is_let_go(refused_once):
    refused_once.see(START_TICK + 30, production=NOTHING_QUEUED)
    refused_once.see(START_TICK + 55, production=READY_PILLBOX)

    assert sent_orders(refused_once, place_wanted()) == [PLACE_AT]


def test_a_place_already_in_flight_when_the_first_was_refused_is_not_taken_for_the_retry(cycles):
    cycles.see(START_TICK, production=READY_PILLBOX)
    sent_orders(cycles, place_wanted())
    cycles.see(START_TICK + 5, production=READY_PILLBOX, last_orders=last_orders(1, row(0, cmd="place"), pending=True))
    sent_orders(cycles, Intent(Priority.PLACEMENT, dict(PLACE_ANYWHERE)))

    cycles.see(
        START_TICK + 10,
        production=READY_PILLBOX,
        last_orders=verdict(2, *INVALID),
        problems=(problem(1, 0, "rejected", "invalid", cmd="place"),),
    )
    held_back = sent_orders(cycles, place_wanted())
    cycles.see(START_TICK + 60, production=READY_PILLBOX)
    retry = sent_orders(cycles, place_wanted())

    assert (held_back, retry) == ([], [PLACE_ANYWHERE])
    assert cycles.ledger.orders_rejected_by_reason == {"invalid": 2}


def test_a_refused_placement_holds_back_no_other_item_s_place(cycles):
    both_ready = [*READY_PILLBOX, {**READY_PILLBOX[0], "queue": "Building", "item": "powr"}]
    cycles.see(START_TICK, production=both_ready)
    sent_orders(cycles, place_wanted())
    cycles.see(START_TICK + 5, production=both_ready, last_orders=verdict(1, *INVALID))

    sent = sent_orders(
        cycles, place_wanted(), Intent(Priority.PLACEMENT, {"cmd": "place", "item": "powr", "at": [24, 44]})
    )

    assert sent == [{"cmd": "place", "item": "powr", "at": [24, 44]}]


@pytest.mark.parametrize("reason", ["not_ready", "unknown_actor"])
def test_a_place_rejected_for_another_reason_is_simply_proposed_again(cycles, reason):
    cycles.see(START_TICK, production=READY_PILLBOX)
    sent_orders(cycles, place_wanted())

    cycles.see(START_TICK + 5, production=READY_PILLBOX, last_orders=verdict(1, "rejected", reason))
    again = sent_orders(cycles, place_wanted())

    assert again == [PLACE_AT] and cycles.ledger.admit([], cycles.world) == []


def test_a_place_refused_in_the_immediate_answer_is_retried_50_ticks_after_the_state_it_was_sent_on(cycles):
    cycles.see(START_TICK, production=READY_PILLBOX)
    packed = pack(cycles.ledger.admit([place_wanted()], cycles.world), cycles.world, LIMITS)
    cycles.ledger.record(packed, cycles.world, START_TICK)
    cycles.ledger.on_action_result(accepted(row(0, "rejected", "invalid", cmd="place")))

    cycles.see(START_TICK + 45, production=READY_PILLBOX)
    before = sent_orders(cycles, place_wanted())
    cycles.see(START_TICK + 50, production=READY_PILLBOX)
    after = sent_orders(cycles, place_wanted())

    assert (before, after) == ([], [PLACE_ANYWHERE])


# -- the summary -----------------------------------------------------------------------------------

SUMMARY_KEYS = {
    "orders_sent",
    "orders_rejected_by_reason",
    "orders_lost",
    "dropped_invalid",
    "duplicate_production_orders",
}


def played_a_little(cycles: Cycles) -> dict:
    """Take a ledger through a few cycles with every kind of outcome and return the summary it should then give."""
    cycles.see(START_TICK, production=NOTHING_QUEUED)
    cycles.ledger.record(Packed((), (), 2), cycles.world, START_TICK)
    cycles.send({"cmd": "build", "item": "powr"}, {"cmd": "train", "item": "e1"}, {"cmd": "build", "item": "tent"})
    verdicts = last_orders(
        1, row(0, "lost", "not_applied"), row(1, "rejected", "not_ready"), row(2, "rejected", "not_available")
    )
    cycles.see(START_TICK + 5, production=NOTHING_QUEUED, last_orders=verdicts)
    cycles.send({"cmd": "train", "item": "e3"})
    return {
        "orders_sent": 4,
        "orders_rejected_by_reason": {"not_ready": 1, "not_available": 1},
        "orders_lost": 1,
        "dropped_invalid": 2,
        "duplicate_production_orders": 1,
    }


def ledger_summaries(events_path: Path, read_jsonl) -> list[dict]:
    return [line["data"] for line in read_jsonl(events_path) if line["kind"] == "ledger_summary"]


@pytest.fixture
def open_record(run_folder: Path):
    """A match record that is open for the test; `closed()` ends it and returns the path of its events file."""
    context = DecisionContext(
        session_id="summary-session", tournament_id=None, game_type="red_alert", agent_id="agent-1"
    )
    records.start_match(context, None)

    def closed() -> Path:
        records.finish_match(RESULT)
        (events_path,) = run_folder.glob("runs/*/summary-session__seat/events.jsonl")
        return events_path

    return closed


def test_the_summary_of_a_new_ledger_has_the_five_counters_at_zero():
    assert OrderLedger().summary() == {
        "orders_sent": 0,
        "orders_rejected_by_reason": {},
        "orders_lost": 0,
        "dropped_invalid": 0,
        "duplicate_production_orders": 0,
    }


def test_the_summary_gives_the_five_counters_as_they_stand(cycles):
    expected = played_a_little(cycles)

    summary = cycles.ledger.summary()
    summary["orders_rejected_by_reason"]["invented"] = 9

    assert expected == cycles.ledger.summary() and set(expected) == SUMMARY_KEYS
    assert "invented" not in cycles.ledger.orders_rejected_by_reason


def test_a_terminal_state_produces_exactly_one_ledger_summary_event_with_the_five_counters(
    cycles, open_record, read_jsonl
):
    expected = played_a_little(cycles)
    terminal = state_at(START_TICK + 10, production=NOTHING_QUEUED)
    terminal.update(is_terminal=True, phase="finished", status="completed")

    cycles.world.update(terminal)
    cycles.ledger.reconcile(cycles.world, terminal)
    cycles.ledger.reconcile(cycles.world, terminal)
    cycles.ledger.finalize()

    summaries = ledger_summaries(open_record(), read_jsonl)
    assert summaries == [expected]
    assert set(summaries[0]) == SUMMARY_KEYS


@pytest.mark.parametrize(
    "ending", [{"is_terminal": True}, {"phase": "finished"}], ids=["is_terminal", "phase finished"]
)
def test_a_state_is_terminal_by_its_flag_or_by_its_phase(cycles, open_record, read_jsonl, ending):
    state = state_at(START_TICK + 5)
    state.update(ending)

    cycles.ledger.reconcile(cycles.world, state)

    assert len(ledger_summaries(open_record(), read_jsonl)) == 1


def test_a_state_of_a_running_match_writes_no_summary(cycles, open_record, read_jsonl):
    played_a_little(cycles)

    assert ledger_summaries(open_record(), read_jsonl) == []


def test_finalize_writes_the_summary_once_however_often_it_is_called(cycles, open_record, read_jsonl):
    expected = played_a_little(cycles)

    for _ in range(3):
        cycles.ledger.finalize()
    cycles.send({"cmd": "build", "item": "powr"})
    cycles.ledger.finalize()

    assert ledger_summaries(open_record(), read_jsonl) == [expected]


def test_finalize_registered_for_the_end_of_the_match_writes_the_summary_before_the_result(
    cycles, open_record, read_jsonl
):
    expected = played_a_little(cycles)
    records.on_match_end(cycles.ledger.finalize)

    kinds = [line["kind"] for line in read_jsonl(open_record())]

    assert kinds[-2:] == ["ledger_summary", "result"]
    assert kinds.count("ledger_summary") == 1 and expected == cycles.ledger.summary()


def test_finalize_without_an_open_record_writes_nothing_and_raises_nothing(cycles, run_folder):
    cycles.ledger.finalize()

    assert not (run_folder / "runs").exists()


# -- the world, the ledger and the packer against the fake game ------------------------------------

BUILD_LISTS = {
    "england": ("powr", "tent", "proc", "powr", "weap"),
    "russia": ("powr", "barr", "proc", "powr", "weap"),
}
DEFENSES = {"england": "pbox", "russia": "ftur"}
RIFLES_WANTED = 8
# Where the player would like its buildings, as offsets from the base, nearest first.
WANTED_OFFSETS = ((4, 0), (-4, 0), (0, 4), (0, -4), (4, 4), (-4, 4), (4, -4), (-4, -4), (8, 0), (0, 8))
PLACED_QUEUES = ("Building", "Defense")


class ScriptedPlayer:
    """Plays an opening through the world model, the ledger and the packer, in the order the agent's loop will.

    Its rules are deliberately simple: deploy once; in each of the Building,
    Defense and Infantry queues ask for the next thing whenever the world
    shows that queue empty; place a finished building once when it is first
    seen waiting. With `lag` the player decides on the state before last, as
    it would when the platform's view trails the game: its view of a queue is
    then still empty in the cycle after it ordered, so it asks again, and it
    is the ledger's reservation that keeps the second order from going out.
    """

    def __init__(self, items: ItemTable, faction: str, lag: int = 0) -> None:
        self.items = items
        self.build_list = BUILD_LISTS[faction]
        self.defense = DEFENSES[faction]
        self.ledger = OrderLedger()
        self.world: World | None = None
        self.limits = Limits()
        self.proposed_production = 0
        self._recent_states: list[dict] = []
        self._lag = lag
        self._deploy_sent = False
        self._placing: set[str] = set()

    def choose_action(self, state, context):
        if self.world is None:
            self.world = World(self.items.with_game_config(context.game_config), context.game_config)
        self._recent_states = [*self._recent_states, state.raw][-(self._lag + 1) :]
        if len(self._recent_states) <= self._lag:
            return WAIT
        seen = self._recent_states[0]
        self.world.update(seen)
        self.ledger.reconcile(self.world, seen)
        self.limits = Limits.from_state(seen)
        packed = pack(self.ledger.admit(self._intents(), self.world), self.world, self.limits)
        self.ledger.record(packed, self.world, state.state_version)
        self._remember_what_went_out(packed)
        return {"type": "orders", "orders": packed.orders} if packed.orders else WAIT

    def on_action_result(self, result: dict, context) -> None:
        self.ledger.on_action_result(result, context)

    def _remember_what_went_out(self, packed: Packed) -> None:
        for order in packed.orders:
            if order["cmd"] == "deploy":
                self._deploy_sent = True
            elif order["cmd"] == "place":
                self._placing.add(order["item"])

    def _intents(self) -> list[Intent]:
        production = [*self._build_intents(), *self._train_intents()]
        self.proposed_production += len(production)
        return [*self._deploy_intents(), *self._placement_intents(), *production]

    def _deploy_intents(self) -> list[Intent]:
        vehicles = [unit.id for unit in self.world.units.values() if unit.type == "mcv"]
        if not vehicles or self._deploy_sent:
            return []
        return [Intent(Priority.PRODUCTION, {"cmd": "deploy", "units": vehicles})]

    def _placement_intents(self) -> list[Intent]:
        """A `place` for each finished building that was not yet seen waiting; one is asked for once."""
        self._placing &= set(self.world.ready_to_place)
        waiting = [item for item in self.world.ready_to_place if item not in self._placing]
        return [
            Intent(Priority.PLACEMENT, {"cmd": "place", "item": item, "at": self._wanted_cell()}) for item in waiting
        ]

    def _build_intents(self) -> list[Intent]:
        wanted = [item for item in (self._next_building(), self._next_defense()) if item is not None]
        return [Intent(Priority.PRODUCTION, {"cmd": "build", "item": item}) for item in wanted]

    def _train_intents(self) -> list[Intent]:
        world = self.world
        if "e1" not in world.available_production or "Infantry" in world.production:
            return []
        if len(world.units) >= RIFLES_WANTED:
            return []
        return [Intent(Priority.PRODUCTION, {"cmd": "train", "item": "e1", "count": 2})]

    def _wanted_cell(self) -> list[int]:
        base_x, base_y = self.world.base_center
        taken = {(building.x, building.y) for building in self.world.buildings.values()}
        cells = [(base_x + dx, base_y + dy) for dx, dy in WANTED_OFFSETS]
        return list(next(cell for cell in cells if cell not in taken))

    def _have(self, item: str) -> int:
        """How many of an item stand or are in a queue, as the world shows it."""
        standing = sum(1 for building in self.world.buildings.values() if building.type == item)
        queued = sum(1 for queue in PLACED_QUEUES for job in self.world.production.get(queue, ()) if job.item == item)
        return standing + queued

    def _next_building(self) -> str | None:
        """The first building of the list that is still missing, when the Building queue is empty and it is on offer."""
        if "Building" in self.world.production:
            return None
        for position, item in enumerate(self.build_list):
            if self._have(item) < self.build_list[: position + 1].count(item):
                return item if item in self.world.available_production else None
        return None

    def _next_defense(self) -> str | None:
        on_offer = self.defense in self.world.available_production
        return (
            self.defense
            if on_offer and "Defense" not in self.world.production and not self._have(self.defense)
            else None
        )


def final_verdicts(states: list[dict]) -> dict[tuple[int, int], tuple[str, str]]:
    """What the fake game answered in the end for every order it showed a final row for, by (batch, index)."""
    answered = {}
    for state in states:
        shown = state["last_orders"]
        if shown is not None and not shown["pending"]:
            for final in shown["results"]:
                answered[shown["batch"], final["index"]] = (final["status"], final["reason"])
    return answered


def assert_within_the_limits(batches: list[dict]) -> None:
    for sent in batches:
        orders = sent["orders"]
        assert 1 <= len(orders) <= 20
        assert all(len(order.get("units", ())) <= 50 for order in orders)
        assert sum(len(order["units"]) if "units" in order else order.get("count", 1) for order in orders) <= 200


def assert_the_ledger_matches_the_fake(player: ScriptedPlayer, game: FakeRedAlert, played) -> None:
    """The ledger's counters and statuses against what the fake game answered and carried out."""
    ledger, answered = player.ledger, final_verdicts(played.states)
    rejected = [reason for status, reason in answered.values() if status == "rejected"]
    assert ledger.orders_sent == sum(len(sent["orders"]) for sent in played.batches) == len(ledger.entries)
    assert ledger.orders_rejected_by_reason == {reason: rejected.count(reason) for reason in set(rejected)}
    assert ledger.orders_lost == sum(1 for status, _ in answered.values() if status == "lost")
    for entry in ledger.entries:
        status, _ = answered.get((entry.batch, entry.index), ("pending", None))
        assert entry.status == {"ok": "confirmed"}.get(status, status), (entry.batch, entry.index)
    assert len(game.recorded_orders) == sum(1 for status, _ in answered.values() if status in ("ok", "partial"))


@pytest.mark.parametrize("faction, enemy_faction", [("england", "russia"), ("russia", "england")])
@pytest.mark.parametrize("lag", [0, 1], ids=["current view", "view one state behind"])
def test_an_opening_played_through_world_ledger_and_packer_is_never_refused_and_never_orders_twice(
    items, faction, enemy_faction, lag
):
    game = FakeRedAlert(faction, enemy_faction)
    player = ScriptedPlayer(items, faction, lag=lag)

    played = game.run(player, 900, keep_states=True)

    buildings = [building["type"] for building in game.state()["observation"]["buildings"]]
    sent_production = sum(1 for entry in player.ledger.entries if entry.order["cmd"] in ("build", "train"))
    assert played.invalid_actions == 0 and game.invalid_actions == 0
    assert_within_the_limits(played.batches)
    assert player.ledger.duplicate_production_orders == 0
    assert [name for name in buildings if name != player.defense] == ["fact", *BUILD_LISTS[faction]]
    assert buildings.count(player.defense) == 1
    assert player.ledger.orders_rejected_by_reason == {} and player.ledger.orders_lost == 0
    assert (player.proposed_production > sent_production) is bool(lag)
    assert_the_ledger_matches_the_fake(player, game, played)
    assert player.world.family == {"england": "allies", "russia": "soviet"}[faction]
    assert player.limits == Limits()


def test_a_lost_batch_a_full_queue_and_a_refused_placement_are_all_recovered_from(items):
    result = result_object(1.0, "time_limit")
    scenario = [
        at(0, lambda fake: fake.refuse_place("proc", times=2)),
        at(1, lambda fake: fake.lose_batch()),
        at(40, lambda fake: fake.lose_batch()),
        at(120, lambda fake: fake.refuse_production("Infantry")),
        at(1100, lambda fake: fake.end_match(result)),
    ]
    game = FakeRedAlert("england", "russia", scenario=scenario)
    player = ScriptedPlayer(items, "england")

    played = game.run(player, 1200, keep_states=True)

    ledger = player.ledger
    buildings = [building["type"] for building in game.state()["observation"]["buildings"]]
    lost = [entry for entry in ledger.entries if entry.status == "lost"]
    resent = [entry for entry in ledger.entries if entry.resend]
    orders = [(entry.order["cmd"], entry.order.get("item")) for entry in ledger.entries]
    refused_places = [entry for entry in ledger.entries if (entry.order["cmd"], entry.status) == ("place", "rejected")]
    assert (played.invalid_actions, played.final_state.is_terminal) == (0, True)
    assert_within_the_limits(played.batches)
    assert ledger.duplicate_production_orders == 0
    assert ledger.orders_lost == len(lost) == 2
    assert [(entry.order["cmd"], entry.order["item"]) for entry in lost] == [("build", "powr"), ("place", "tent")]
    for entry in lost:
        again = [later for later in ledger.entries if later.batch == entry.batch + 1 and later.order == entry.order]
        assert [later.status for later in again] == ["confirmed"]
    # The player asked for the power plant again itself; the placement it had asked for once came back from the ledger.
    assert [entry.order for entry in resent] == [lost[1].order]
    assert ledger.orders_rejected_by_reason == {"invalid": 2, "not_ready": 1}
    assert ["at" in entry.order for entry in refused_places] == [True, False]
    assert refused_places[1].sent_tick - refused_places[0].sent_tick >= 50
    assert [orders.count((cmd, "proc")) for cmd in ("build", "place", "cancel")] == [2, 3, 1]
    assert sorted(buildings) == sorted(["fact", "powr", "tent", "proc", "powr", "weap", "pbox"])
    assert_the_ledger_matches_the_fake(player, game, played)
    assert ledger.summary() == {
        "orders_sent": len(ledger.entries),
        "orders_rejected_by_reason": {"invalid": 2, "not_ready": 1},
        "orders_lost": 2,
        "dropped_invalid": 0,
        "duplicate_production_orders": 0,
    }


def test_a_recorded_run_of_the_scripted_player_ends_with_one_ledger_summary_and_the_result(
    items, run_folder, read_jsonl
):
    context = DecisionContext(
        session_id="played-session", tournament_id=None, game_type="red_alert", agent_id="agent-1"
    )
    result = result_object(0.0, "victory")
    game = FakeRedAlert("russia", "england", scenario=[at(60, lambda fake: fake.end_match(result))])
    player = ScriptedPlayer(items, "russia")
    records.start_match(context, None)
    records.on_match_end(player.ledger.finalize)

    played = game.run(player, 200, game.context())
    terminal = game.state()
    player.world.update(terminal)
    player.ledger.reconcile(player.world, terminal)
    records.finish_match(game.result())

    (events_path,) = run_folder.glob("runs/*/played-session__seat/events.jsonl")
    lines = read_jsonl(events_path)
    summaries = [line["data"] for line in lines if line["kind"] == "ledger_summary"]
    answers = [line["data"] for line in lines if line["kind"] == "action_result"]
    assert summaries == [player.ledger.summary()] and summaries[0]["orders_sent"] == len(player.ledger.entries) > 0
    assert len(answers) == len(played.batches) and all(answer["accepted"] for answer in answers)
    assert lines[-1]["kind"] == "result" and lines[-1]["data"]["result"] == result
