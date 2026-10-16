"""Run-level and per-match records under `runs/`.

Every line is one JSON object. Run-level lines go to
`runs/<UTC yyyymmdd>/run.jsonl`; the events of the match a process plays go to
`runs/<UTC yyyymmdd>/<session_id>__<seat>/events.jsonl`, with the match's
fixed facts next to it in `meta.json`. The folder is per seat because the
seats of a self-play match share one session id. When the agent is restarted
during a match, the new process writes `events.<n>.jsonl` and `meta.<n>.json`
next to the earlier attempt's files and never opens those. Records are written
only: the agent never reads one back. When `UCLA_AGENT_RUNS_DIR` names a
folder (`paths.runs_folder`), the dated folders go under it in place of
`runs/`.

Match events are written by a background thread: `log` only stamps an event
and puts it on a queue. Recording therefore costs a decision a few
microseconds, and a slow or failing disk never delays or stops play; what
cannot be written is dropped and counted.

A record is closed at most once. The runtime never calls the agent with a
terminal state, so `start_match` hooks the runtime's result (`runtime_hooks`)
to `finish_match`, which ends the file with a `result` event. A process that
exits without having seen a result ends its file with a `match_end` event
instead. A process that is killed ends its file with neither.

Code that runs inside `suppressed()` records nothing from its thread: a
practice table plays other seats with decision-makers of their own on the
recorded seat's thread, and what they log is about their seats, not this one.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import queue
import threading
import time
from datetime import datetime, timezone
from multiprocessing import util as multiprocessing_util
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, TextIO

from ucla_agent.core import match_meta, runtime_hooks
from ucla_agent.core.paths import repository_root, runs_folder
from ucla_agent.core.record_files import (
    FIRST_ATTEMPT,
    attempt_of_events_file,
    events_file_name,
    match_folder_name,
    meta_file_name,
    seat_label,
)
from ucla_agent.core.secrets import redact

RUNS_DIR_NAME = "runs"
RUN_FILE_NAME = "run.jsonl"
# Every kind of event a match record may hold. The list is closed, so that a
# reader of any record knows all the kinds it can meet.
EVENT_KINDS = frozenset(
    {
        "game_config",
        "state",
        "decision",
        "text_decision",
        "claude_token_cap",
        "claude_usage",
        "plan_change",
        "plan_rule_blocked",
        "raid",
        "harvesters_parked",
        "harvesters_moved",
        "harvesters_rebalanced",
        "expansion",
        "refinery_field",
        "creep_placement",
        "placement_refused",
        "route_walk",
        "route",
        "economy_hold",
        "economy",
        "opening_reach",
        "opening_map_size",
        "scout",
        "scout_slot",
        "rush_verdict",
        "enemy_estimate",
        "clock",
        "spending_focus",
        "counter_quota",
        "base_defense",
        "anti_air",
        "hold_position",
        "siege",
        "attack_target",
        "combat",
        "commander",
        "action",
        "action_result",
        "timing",
        "ledger_summary",
        "werewolf_ledger",
        "werewolf_inference_reset",
        "werewolf_chat",
        "werewolf_decision",
        "pokemon_decision",
        "fallback",
        "unsupported_game",
        "message_in",
        "message_out",
        "error",
        "result",
        "match_end",
    }
)
# Set to "1" by the test configuration: logging a kind outside the list then
# raises, so a misspelled kind fails a test instead of reaching a record.
STRICT_EVENT_KINDS_ENV = "UCLA_AGENT_STRICT_EVENT_KINDS"
_STRICT_EVENT_KINDS_ON = "1"
HANDLED_ERROR = "handled"
PROCESS_EXIT_REASON = "process_exit"
NEXT_MATCH_REASON = "next_match"
_NO_RESULT_HOOK_MESSAGE = (
    f"altruagent.runner has no {runtime_hooks.HOOKED_FUNCTION_NAME}: this match's result cannot be recorded, "
    "and its record will end with match_end"
)
# Zero or above puts the finalizer in multiprocessing's first pass at process
# exit, before it stops the helper threads and processes it manages itself.
_EXIT_FINALIZER_PRIORITY = 0
# Games whose states are written in full only once in every FULL_STATE_EVERY.
THINNED_GAME_TYPES = frozenset({"red_alert"})
FULL_STATE_EVERY = 5
MAX_QUEUED_EVENTS = 10_000
# How long closing a record may wait for the writer thread to finish the queue.
CLOSE_TIMEOUT_SECONDS = 2.0
_DATE_FOLDER_FORMAT = "%Y%m%d"
_MILLISECONDS_PER_SECOND = 1000
_ROUND_TRIP_DECIMALS = 1
# Never goes backwards and resolves well under a millisecond on every
# platform; time.monotonic() ticks only every 15.6 ms on Windows, too coarse
# for latencies of a few tens of milliseconds.
_elapsed_clock = time.perf_counter
_FILE_ENCODING = "utf-8"

# An event as it waits on the queue: kind, data, UTC time, milliseconds since match start.
_QueuedEvent = tuple[str, dict[str, Any], str, int]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _date_folder(moment: datetime) -> Path:
    return runs_folder(repository_root() / RUNS_DIR_NAME) / moment.strftime(_DATE_FOLDER_FORMAT)


def _serialize(line: dict[str, Any]) -> str:
    """One record line as JSON text with secrets redacted. Every line of every record leaves through here."""
    # repr for anything JSON cannot encode: a record line is evidence, so an
    # unexpected object is written as text instead of failing the write.
    return redact(json.dumps(line, default=repr)) + "\n"


def _open_for_append(path: Path) -> TextIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    return open(path, "a", encoding=_FILE_ENCODING)


class EventWriter:
    """Appends events to one JSON Lines file from a background thread.

    `submit` never blocks and never raises: the event goes on a bounded queue
    and the thread writes it, redacted, as the next line of the file. An event
    that does not fit on the queue, or cannot be written, is dropped and
    counted in `dropped_events`. A line gets its `seq` when it is written, so
    `seq` has no gaps within the file whatever was dropped.
    """

    def __init__(self, open_file: Callable[[], TextIO]) -> None:
        """`open_file` is called on the writer thread: once as it starts, and again before a line when no file is open."""
        self._open_file = open_file
        self._file: TextIO | None = None
        self._queue: queue.Queue[_QueuedEvent | None] = queue.Queue(maxsize=MAX_QUEUED_EVENTS)
        self._started = _elapsed_clock()
        self._next_seq = 0
        self._dropped_events = 0
        self._dropped_lock = threading.Lock()
        self._closing = threading.Event()
        self._last_event: _QueuedEvent | None = None
        self._thread = threading.Thread(target=self._write_until_closed, name="record-writer", daemon=True)
        self._thread.start()

    @property
    def dropped_events(self) -> int:
        """How many events were dropped so far, for a full queue or a failed write."""
        return self._dropped_events

    def submit(self, kind: str, data: dict[str, Any]) -> None:
        """Queue one event for writing. `data` is serialized later, so the caller must not change it afterwards."""
        if self._closing.is_set():
            return
        try:
            self._queue.put_nowait(self._stamped(kind, data))
        except queue.Full:
            self._count_dropped()

    def close(self, last_kind: str | None = None, last_data: dict[str, Any] | None = None) -> None:
        """Write everything queued, then one last event when `last_kind` is given, and close the file.

        The last event also carries `dropped_events`. Returns after at most
        `CLOSE_TIMEOUT_SECONDS`, written or not. Later calls, and later
        `submit` calls, do nothing.
        """
        if self._closing.is_set():
            return
        if last_kind is not None:
            self._last_event = self._stamped(last_kind, last_data or {})
        self._closing.set()
        # Wakes the thread when it waits on an empty queue. On a full queue it
        # is busy, and sees the flag once the queue is written out.
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)
        self._thread.join(CLOSE_TIMEOUT_SECONDS)

    def _stamped(self, kind: str, data: dict[str, Any]) -> _QueuedEvent:
        elapsed_ms = int((_elapsed_clock() - self._started) * _MILLISECONDS_PER_SECOND)
        return kind, data, _timestamp(_utc_now()), elapsed_ms

    def _count_dropped(self) -> None:
        with self._dropped_lock:
            self._dropped_events += 1

    def _write_until_closed(self) -> None:
        # Opened before any event arrives, so the file is on disk from the
        # start of a match even when nothing is logged for a while. A failure
        # here is tried again by the first event's write.
        with contextlib.suppress(Exception):
            self._file = self._open_file()
        while not (self._closing.is_set() and self._queue.empty()):
            event = self._queue.get()
            if event is not None:
                self._write(*event)
        if self._last_event is not None:
            kind, data, timestamp, elapsed_ms = self._last_event
            self._write(kind, {**data, "dropped_events": self._dropped_events}, timestamp, elapsed_ms)
        self._close_file()

    def _write(self, kind: str, data: dict[str, Any], timestamp: str, elapsed_ms: int) -> None:
        line = {"seq": self._next_seq, "t": timestamp, "mono_ms": elapsed_ms, "kind": kind, "data": data}
        try:
            text = _serialize(line)
            if self._file is None:
                self._file = self._open_file()
            self._file.write(text)
            # Flushed line by line: a worker that is killed loses at most the line being written.
            self._file.flush()
        except Exception:  # noqa: BLE001 - whatever keeps a line from being written, play goes on without it
            self._count_dropped()
            self._close_file()
            return
        self._next_seq += 1

    def _close_file(self) -> None:
        if self._file is not None:
            file, self._file = self._file, None
            with contextlib.suppress(OSError):
                file.close()


class _AttemptFiles:
    """The files of this process's attempt at a match, created on the writer thread.

    A restarted agent resumes a match in a new process. That process takes
    the next attempt number and writes its own events file next to the earlier
    attempt's; it finds the number by listing the folder's names and opens no
    file that is already there.
    """

    def __init__(self, folder: Path, describe: Callable[[int], dict[str, Any]]) -> None:
        """`describe` returns the meta file content for an attempt number; it runs on the writer thread."""
        self._folder = folder
        self._describe = describe
        self._attempt: int | None = None
        self._meta_written = False

    def open_events_file(self) -> TextIO:
        """This attempt's events file, open for writing, once its meta file is on disk next to it.

        The first call creates both files; a later call reopens the events
        file to append, and writes the meta file if that failed before.
        """
        events_file = self._create_events_file() if self._attempt is None else self._reopen_events_file()
        if not self._meta_written:
            try:
                self._write_meta_file()
            except OSError:
                events_file.close()
                raise
        return events_file

    def _create_events_file(self) -> TextIO:
        self._folder.mkdir(parents=True, exist_ok=True)
        while True:
            attempt = self._free_attempt()
            try:
                # Exclusive creation: two processes that list the folder at the
                # same moment cannot end up writing one file.
                events_file = open(self._folder / events_file_name(attempt), "x", encoding=_FILE_ENCODING)
            except FileExistsError:
                continue
            self._attempt = attempt
            return events_file

    def _reopen_events_file(self) -> TextIO:
        return open(self._folder / events_file_name(self._attempt), "a", encoding=_FILE_ENCODING)

    def _write_meta_file(self) -> None:
        with open(self._folder / meta_file_name(self._attempt), "w", encoding=_FILE_ENCODING) as meta_file:
            meta_file.write(_serialize(self._describe(self._attempt)))
        self._meta_written = True

    def _free_attempt(self) -> int:
        """One more than the number of events files in the folder, moved past any attempt whose names are taken."""
        names = set(os.listdir(self._folder))
        attempt = FIRST_ATTEMPT + sum(1 for name in names if attempt_of_events_file(name) is not None)
        while not names.isdisjoint((events_file_name(attempt), meta_file_name(attempt))):
            attempt += 1
        return attempt


def _seat_played(context: Any, state: Any) -> str:
    """The label of the seat this process plays: the game's name for it when the first state gives one, else its position.

    Red Alert names the seat in every state (`your_seat`, for example
    `Multi0`); for a game that does not, the runtime's 0-based seat position
    is the label, and `seat` when even that is unknown.
    """
    raw_state = getattr(state, "raw", None)
    named_by_the_game = raw_state.get("your_seat") if isinstance(raw_state, dict) else None
    return seat_label(named_by_the_game, context.seat_position)


class _StateLog:
    """Decides what each `state` event of a match holds, and remembers the last state seen.

    Red Alert sends about five states a second, each listing every unit and
    building; written in full they would make up nearly all of a record. A
    Red Alert state is therefore written in full at least once in every
    `FULL_STATE_EVERY` states and whenever the verdicts on the agent's orders
    changed, and the states between keep only the figures that change with
    every tick. Every other game's states are written in full.
    """

    def __init__(self, game_type: str | None, first_state: Any) -> None:
        self._thinned = game_type in THINNED_GAME_TYPES
        self._summaries_since_full = 0
        self._last_verdicts: tuple[Any, Any] | None = None
        self.last_state_version = getattr(first_state, "state_version", None)
        self.last_phase = getattr(first_state, "phase", None)

    def event_data(self, raw_state: dict[str, Any], annotations: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """The data of the `state` event for the next state of the match, with `annotations` added to it."""
        self.last_state_version = raw_state.get("state_version")
        self.last_phase = raw_state.get("phase")
        data = {"state_version": self.last_state_version, "phase": self.last_phase}
        if self._is_written_in_full(raw_state):
            data["state"] = raw_state
        else:
            data.update(_state_summary(raw_state))
        data.update(annotations or {})
        return data

    def _is_written_in_full(self, raw_state: dict[str, Any]) -> bool:
        verdicts = (raw_state.get("last_orders"), raw_state.get("recent_order_problems"))
        verdicts_changed = verdicts != self._last_verdicts
        self._last_verdicts = verdicts
        in_full = (
            not self._thinned
            or verdicts_changed
            or self._summaries_since_full >= FULL_STATE_EVERY - 1
            or not isinstance(raw_state.get("observation"), dict)
        )
        self._summaries_since_full = 0 if in_full else self._summaries_since_full + 1
        return in_full


def _state_summary(raw_state: dict[str, Any]) -> dict[str, Any]:
    """What a thinned state keeps: the figures that change with every tick, and those a reader of a live match shows.

    The trade balance, the share of the map explored and the clock are kept so
    that a match can be followed from its thinned states alone, without
    waiting for the next one written in full.
    """
    observation = raw_state["observation"]
    military = observation.get("military")
    return {
        "economy": observation.get("economy"),
        "military": military,
        "units_count": len(observation.get("units") or ()),
        "buildings_count": len(observation.get("buildings") or ()),
        "visible_enemies_count": len(observation.get("visible_enemies") or ()),
        "trade_balance": _trade_balance(military),
        "explored_percent": observation.get("explored_percent"),
        "time": raw_state.get("time"),
    }


def _trade_balance(military: Any) -> float | None:
    """The value destroyed minus the value lost (`kills_cost - deaths_cost`); None when the figures do not give both."""
    if not isinstance(military, dict):
        return None
    kills, deaths = military.get("kills_cost"), military.get("deaths_cost")
    if not all(isinstance(figure, (int, float)) for figure in (kills, deaths)):
        return None
    return kills - deaths


class _ProcessRecord:
    """What this process has opened. A worker process plays exactly one match."""

    def __init__(self) -> None:
        self.run_path: Path | None = None
        self.writer: EventWriter | None = None
        self.state_log: _StateLog | None = None
        self.last_action_at: float | None = None
        self.end_callbacks: list[Callable[[], None]] = []
        self.finished = False


_process = _ProcessRecord()
_exit_finalizer_registered = False
# `active` is true on a thread while `suppressed()` runs there.
_suppression = threading.local()


@contextlib.contextmanager
def suppressed() -> Iterator[None]:
    """Record nothing that the current thread logs while the block runs; other threads record as before.

    Inside the block `log`, `log_state`, `log_action` and `log_action_result`
    write nothing and leave the open record as it was (its last state and its
    round-trip mark), and `on_match_end` registers nothing. A practice table
    runs the other seats' decision-makers on the recorded seat's thread, and
    what they would record belongs to their seats, so it is written nowhere.
    """
    was_suppressed = _is_suppressed()
    _suppression.active = True
    try:
        yield
    finally:
        _suppression.active = was_suppressed


def _is_suppressed() -> bool:
    return getattr(_suppression, "active", False)


def _recording() -> bool:
    """Whether an event logged now is written: a record is open and the current thread is not suppressed."""
    return _process.writer is not None and not _is_suppressed()


def append_run_line(kind: str, data: dict[str, Any]) -> None:
    """Append one run-level line (such as `run_started`) to `runs/<UTC yyyymmdd>/run.jsonl`."""
    moment = _utc_now()
    if _process.run_path is None:
        # Chosen once: a run that passes midnight UTC keeps its start and stop lines in one file.
        _process.run_path = _date_folder(moment) / RUN_FILE_NAME
    with _open_for_append(_process.run_path) as run_file:
        run_file.write(_serialize({"t": _timestamp(moment), "kind": kind, "data": data}))


def start_match(context: Any, state: Any) -> None:
    """Open this process's match record; a later call in the same process changes nothing.

    Callers pass the context and the state that the match's first call arrived
    with (the state is None when that call carried none). The record goes to
    the folder of the session and of the seat this process plays. Nothing is
    created on disk on the calling thread: the writer thread creates the
    attempt's events file and meta file as it starts.
    """
    if _process.writer is not None:
        return
    started = _utc_now()
    root = repository_root()
    seat = _seat_played(context, state)
    attempt_files = _AttemptFiles(
        _date_folder(started) / match_folder_name(context.session_id, seat),
        lambda attempt: match_meta.build(context, seat, attempt, _timestamp(started), root),
    )
    _process.state_log = _StateLog(context.game_type, state)
    _process.writer = EventWriter(attempt_files.open_events_file)
    _register_exit_finalizer()
    if not runtime_hooks.install(finish_match):
        log("error", _error_data("runtime_hooks", "AttributeError", _NO_RESULT_HOOK_MESSAGE))


def log(kind: str, data: dict[str, Any]) -> None:
    """Record one event of the match this process plays; does nothing before `start_match`.

    Returns at once and never raises for a full queue or a failing disk: the
    event is written by the writer thread. `data` is handed over, so the
    caller must not change it afterwards. `kind` is one of `EVENT_KINDS`; any
    other kind is recorded as an `error` event in its place, and raises
    `ValueError` under the test configuration. Writes nothing inside `suppressed()`.
    """
    if kind not in EVENT_KINDS:
        kind, data = _undefined_kind_event(kind)
    if _recording():
        _process.writer.submit(kind, data)


def log_state(raw_state: dict[str, Any], annotations: Mapping[str, Any] | None = None) -> None:
    """Record the `state` event for one state object as received (`state.raw`); does nothing before `start_match`.

    The event holds `state_version`, `phase` and the whole object under
    `state`. In a Red Alert match most states hold, in place of the whole
    object, `economy`, `military`, the counts of units, buildings and visible
    enemies, `trade_balance`, `explored_percent` and `time` (see `_StateLog`).
    `annotations` are what the decision-maker knows of the match beyond the
    state itself (Red Alert: the plan in force); they are added to the event
    whether the state is written in full or not.
    """
    if _process.state_log is not None and not _is_suppressed():
        log("state", _process.state_log.event_data(raw_state, annotations))


def log_action(
    state_version: int,
    action: Any,
    compute_ms: float,
    *,
    order_count: int | None = None,
    command_count: int | None = None,
) -> None:
    """Record the `action` event for the object about to be returned to the runtime; does nothing before `start_match`.

    Call it as the last thing before `choose_action` returns: the next
    `log_action_result` measures the round trip from the moment of this call.
    `action` is the exact object returned, and `compute_ms` the time since
    `choose_action` was entered. A game whose actions are batches of orders
    gives how many orders the batch holds and how many engine commands they
    count as (`order_count`, `command_count`, both 0 for a wait), so that a
    reader can weigh each batch against the platform's limits without the
    game's code; each one given is added to the event.
    """
    if _recording():
        _process.last_action_at = _elapsed_clock()
        data = {"state_version": state_version, "action": action, "compute_ms": compute_ms}
        counts = {"order_count": order_count, "command_count": command_count}
        data.update({name: count for name, count in counts.items() if count is not None})
        log("action", data)


def log_action_result(result: dict[str, Any]) -> None:
    """Record the `action_result` event for the object the runtime passed to `on_action_result`.

    Adds `accepted`, true unless the object carries an `error` key, and
    `round_trip_ms`, the time since the last `log_action` (null when no action
    was logged). Does nothing before `start_match`.
    """
    if _recording():
        log("action_result", {"result": result, "accepted": "error" not in result, "round_trip_ms": _round_trip_ms()})


def _round_trip_ms() -> float | None:
    if _process.last_action_at is None:
        return None
    return round((_elapsed_clock() - _process.last_action_at) * _MILLISECONDS_PER_SECOND, _ROUND_TRIP_DECIMALS)


def _undefined_kind_event(kind: str) -> tuple[str, dict[str, Any]]:
    """The event recorded in place of one whose kind is not defined; raises instead under the test configuration."""
    message = f"'{kind}' is not a defined event kind"
    if os.environ.get(STRICT_EVENT_KINDS_ENV) == _STRICT_EVENT_KINDS_ON:
        raise ValueError(message)
    return "error", _error_data("records.log", "ValueError", message)


def _error_data(where: str, exception: str, message: str) -> dict[str, Any]:
    """The data of an `error` event for a failure the agent handled and played on from."""
    return {"where": where, "type": HANDLED_ERROR, "exception": exception, "message": message}


def on_match_end(callback: Callable[[], None]) -> None:
    """Register a callable to run once, when the match record is closed; callbacks run in the order registered.

    A decision-maker registers the function that logs its end-of-match
    summary. The runtime never calls the agent with a terminal state, so this
    is the one moment such a summary can be written. Registers nothing inside
    `suppressed()`.
    """
    if not _is_suppressed():
        _process.end_callbacks.append(callback)


def finish_match(result: Any) -> None:
    """Close this process's match record with the match's result. Only the first call does anything.

    Runs the end-of-match callbacks, then writes the `result` event (the
    result object as received, `ended_at`, `dropped_events`) and closes the
    file, waiting at most `CLOSE_TIMEOUT_SECONDS` for the writer thread.
    """
    _end_match("result", {"result": result, "ended_at": _timestamp(_utc_now())})


def _end_match(last_kind: str, last_data: dict[str, Any]) -> None:
    """Run the end-of-match callbacks and close the record with one last event; only the first call does anything."""
    if _process.finished:
        return
    _process.finished = True
    _run_end_callbacks()
    if _process.writer is not None:
        _process.writer.close(last_kind, last_data)


def _run_end_callbacks() -> None:
    """Run each end-of-match callback in order; one that raises is recorded as an error and the rest still run."""
    for callback in _process.end_callbacks:
        try:
            callback()
        except Exception as exc:  # noqa: BLE001 - one failing summary must not cost the others or the record's ending
            log("error", _error_data("match_end_callback", type(exc).__name__, str(exc)))


def reset_for_next_match() -> None:
    """Close a record still open with a `match_end` event and forget it, so the next `start_match` opens a new one.

    A worker process plays one match and never calls this. A harness that
    plays one match after another in a single process calls it between them,
    since `start_match` opens only the first record of a process.
    """
    global _process
    _close_unfinished(NEXT_MATCH_REASON)
    _process = _ProcessRecord()


def _finalize_at_process_exit() -> None:
    """Close a record that is still open when the process exits, with a `match_end` event where the result would be.

    That is the ending of a match whose result this process never saw: the
    agent was stopped, or the runtime gave the match up. Does nothing when the
    record was closed before, or when this process opened none.
    """
    _close_unfinished(PROCESS_EXIT_REASON)


def _close_unfinished(reason: str) -> None:
    """End a record still open with a `match_end` event giving `reason`; does nothing for a closed record or none."""
    if _process.state_log is not None:
        last_seen = _process.state_log
        _end_match(
            "match_end",
            {
                "reason": reason,
                "last_state_version": last_seen.last_state_version,
                "last_phase": last_seen.last_phase,
            },
        )


def _register_exit_finalizer() -> None:
    """Have `_finalize_at_process_exit` run however this process ends by itself; registers once per process.

    `atexit` covers a process that exits the interpreter. The runtime's worker
    processes never do: multiprocessing ends them with `os._exit` once its own
    finalizers have run, and that is where the second registration runs.
    """
    global _exit_finalizer_registered
    if not _exit_finalizer_registered:
        _exit_finalizer_registered = True
        atexit.register(_finalize_at_process_exit)
        multiprocessing_util.Finalize(None, _finalize_at_process_exit, exitpriority=_EXIT_FINALIZER_PRIORITY)
