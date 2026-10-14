"""One seat of a practice game, played by `altruagent.runner.run_game` as if it were an `MCPGameSession`.

The vendored runner drives a match through eight methods of its MCP session.
`PracticeSeatSession` has the same eight, with the same arguments and answers,
over a `WerewolfEngine` in this process: the agent under test plays the
driven seat through its real runner loop, and the other six seats are
in-process opponents.

Nothing waits. Before it answers any call of the driven seat, and again after
the driven seat's own move or message, the session applies the engine's
clocks and lets every opponent act, one step at a time, until the game waits
on the driven seat or is over. So a whole game runs on the runner's own
thread with no sleeps, and `wait_for_update` returns at once: with
`updated: true` when something changed since the caller's last state, and
the unchanged state otherwise.

An opponent is given its own seat's state object, built exactly as the
driven seat's is, with that seat's whole transcript added under `messages`
(what `get_messages` answers the seat), and nothing else: the platform
empties `new_messages` for the vote, and a player re-reads the discussion
with `get_messages`, a call an in-process opponent cannot make.

A rule the driven seat breaks comes back as the runner's `MCPToolError` with
the platform's error code; a rule an opponent breaks is a bug in the opponent
and raises `OpponentError`.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Protocol

from altruagent import TERMINATE_MESSAGING, GameState
from altruagent.mcp_transport import MCPToolError
from altruagent.runner import MessageDecision

from ucla_agent.practice.werewolf import view
from ucla_agent.practice.werewolf.engine import (
    CHAT,
    INVALID_ACTION,
    INVALID_CONTENT,
    SEATS,
    TERMINATE,
    Message,
    RuleError,
    WerewolfEngine,
)

STALE_STATE = "STALE_STATE"
DEFAULT_SESSION_ID = "practice-werewolf"


class Opponent(Protocol):
    """An in-process player of one seat: it is handed that seat's state object and transcript, and nothing else."""

    def choose_action(self, view: dict[str, Any]) -> int:
        """The seat to point at (or 7, abstain), one of the state's legal actions."""

    def choose_message(self, view: dict[str, Any]) -> MessageDecision:
        """A chat for the open window, or `TERMINATE_MESSAGING` once the seat has nothing more to say."""


class OpponentError(RuntimeError):
    """An opponent answered with something the rules refuse: a bug in that opponent, not in the driven seat."""


class PracticeSeatSession:
    """The driven seat of a practice game, with the methods `run_game` calls on an `MCPGameSession`."""

    def __init__(
        self,
        engine: WerewolfEngine,
        seat: int,
        opponents: Mapping[int, Opponent],
        session_id: str = DEFAULT_SESSION_ID,
    ) -> None:
        """`opponents` has one player for each of the six seats other than `seat`."""
        others = set(SEATS) - {seat}
        if seat not in SEATS or set(opponents) != others:
            raise ValueError(
                f"seat {seat} needs one opponent in each of seats {sorted(others)}, got {sorted(opponents)}"
            )
        self.session_id = session_id
        self.seat = seat
        self._engine = engine
        self._opponents = dict(opponents)

    def get_state(self) -> GameState:
        """`get_game_state`: the seat's state, with `legal_actions` when it can act."""
        self._settle()
        return GameState.from_mcp_state(self._state())

    def wait_for_update(
        self,
        *,
        since_version: int,
        since_message_seq: int | None = None,
        since_is_current_actor: bool | None = None,
        since_phase: str | None = None,
        timeout_seconds: float | None = None,
    ) -> GameState:
        """`wait_for_update`, answered at once; `updated` says whether anything changed since the `since_*` values.

        `timeout_seconds` is accepted for the runner's sake: with the
        opponents already settled, nothing more can change while waiting.
        """
        self._settle()
        state = self._state()
        state["updated"] = self._changed(state, since_version, since_message_seq, since_is_current_actor, since_phase)
        return GameState.from_mcp_state(state)

    def get_legal_actions(self) -> dict[str, Any]:
        """`get_legal_actions`: the actions the seat may take now, empty when it is not its turn."""
        self._settle()
        return view.legal_actions_payload(self._engine, self.seat, self.session_id)

    def play_action(
        self,
        *,
        action_id: str | None = None,
        action: dict | None = None,
        state_version: int,
        reasoning_summary: str | None = None,
    ) -> dict[str, Any]:
        """`play_action`: apply the seat's move; the answer carries the seat's next state while the game goes on.

        A move against an out-of-date `state_version` gets `STALE_STATE`, also
        when the inactivity auto-action has just taken the seat's turn.
        `reasoning_summary` is accepted and not kept: no spectator reads a
        practice game.
        """
        self._settle()
        if state_version != self._engine.state_version:
            raise _tool_error(
                STALE_STATE, f"state_version {state_version} is not the current {self._engine.state_version}"
            )
        with _as_tool_errors():
            self._engine.play(self.seat, _action_number(action_id, action))
        self._settle()
        state = self._state()
        answer = {
            "accepted": True,
            "session_id": self.session_id,
            "state_version": self._engine.state_version,
            "status": state["status"],
            "next_actions": state["next_actions"],
        }
        if not self._engine.is_over:
            answer["state"] = state
        return answer

    def send_message(
        self, *, message_type: str, content: str | None = None, recipients: list[int] | None = None
    ) -> dict[str, Any]:
        """`send_message`: a chat (`[]` broadcast, `[seat]` private) or a terminate in the open window."""
        self._settle()
        with _as_tool_errors():
            message = self._post(message_type, content, recipients)
        self._settle()
        state = self._state()
        return {
            "accepted": True,
            "session_id": self.session_id,
            "phase": state["phase"],
            "terminated_messaging": state["terminated_messaging"],
            "new_messages": state["new_messages"],
            "message": None if message is None else view.envelope(message, self.session_id),
            "remaining": {"this_turn": self._engine.chats_left(self.seat), "this_window": None, "agent_total": None},
            "next_actions": state["next_actions"],
        }

    def get_messages(self, *, since: int = -1) -> dict[str, Any]:
        """`get_messages`: every message the seat may read with a `seq` above `since`."""
        self._settle()
        state = self._state()
        return {
            "session_id": self.session_id,
            "messages": view.visible_messages(self._engine, self.seat, self.session_id, since),
            "phase": state["phase"],
            "terminated_messaging": state["terminated_messaging"],
        }

    def resign(self) -> dict[str, Any]:
        """`resign`: end the game with the seat's side losing; answers with the `get_result` object."""
        self._settle()
        with _as_tool_errors():
            self._engine.resign(self.seat)
        return view.result_payload(self._engine, self.seat, self.session_id)

    def get_result(self) -> dict[str, Any]:
        """`get_result`: the scores once the game is over, nulls while it runs."""
        self._settle()
        return view.result_payload(self._engine, self.seat, self.session_id)

    # -- the opponents' turns --------------------------------------------------------------------

    def _settle(self) -> None:
        """Apply the clocks and let the opponents act until the game waits on the driven seat or is over."""
        while True:
            self._engine.apply_timeouts()
            if not self._step_an_opponent():
                return

    def _step_an_opponent(self) -> bool:
        """Let one opponent take one step, if the game is waiting on one; whether one did."""
        engine = self._engine
        if engine.is_over:
            return False
        if engine.window is not None:
            speaker = next((seat for seat in engine.window_waiting_on() if seat in self._opponents), None)
            if speaker is not None:
                self._let_speak(speaker)
            return speaker is not None
        actor = engine.current_actor()
        if actor in self._opponents:
            self._let_act(actor)
        return actor in self._opponents

    def _let_act(self, seat: int) -> None:
        action = self._opponents[seat].choose_action(self._opponent_view(seat))
        with _opponent_errors(seat):
            self._engine.play(seat, action)

    def _let_speak(self, seat: int) -> None:
        decision = self._opponents[seat].choose_message(self._opponent_view(seat))
        with _opponent_errors(seat):
            if decision is TERMINATE_MESSAGING:
                self._engine.terminate(seat)
            else:
                self._engine.send_chat(seat, decision.content, decision.recipients)

    # -- the driven seat -------------------------------------------------------------------------

    def _post(self, message_type: str, content: str | None, recipients: list[int] | None) -> Message | None:
        if message_type == CHAT:
            return self._engine.send_chat(self.seat, content, recipients)
        if message_type == TERMINATE:
            return self._engine.terminate(self.seat)
        raise RuleError(INVALID_CONTENT, f"message_type is {CHAT} or {TERMINATE}, not {message_type!r}")

    def _state(self) -> dict[str, Any]:
        return self._view_of(self.seat)

    def _view_of(self, seat: int) -> dict[str, Any]:
        return view.state_payload(self._engine, seat, self.session_id)

    def _opponent_view(self, seat: int) -> dict[str, Any]:
        """The seat's state object with its whole transcript, which a player at the table reads with `get_messages`."""
        return {
            **self._view_of(seat),
            view.TRANSCRIPT_FIELD: view.visible_messages(self._engine, seat, self.session_id),
        }

    def _changed(
        self,
        state: dict[str, Any],
        since_version: int,
        since_message_seq: int | None,
        since_is_current_actor: bool | None,
        since_phase: str | None,
    ) -> bool:
        """Whether the state differs from what the caller saw; a `since_*` value left out is not compared."""
        if state["is_terminal"] or state["state_version"] != since_version:
            return True
        if since_is_current_actor is not None and state["is_current_actor"] != since_is_current_actor:
            return True
        if since_phase is not None and state["phase"] != since_phase:
            return True
        return since_message_seq is not None and bool(
            view.visible_messages(self._engine, self.seat, self.session_id, since_message_seq)
        )


def _action_number(action_id: str | None, action: dict | None) -> int:
    """The seat number an `action_id` names; a structured `action` is not a Werewolf move."""
    if action is not None or not isinstance(action_id, str) or not (action_id.isascii() and action_id.isdigit()):
        raise RuleError(INVALID_ACTION, f"a Werewolf action is a seat number as a string, not {action_id or action!r}")
    return int(action_id)


def _tool_error(code: str, detail: str) -> MCPToolError:
    return MCPToolError(f"{code}: {detail}", status_code=None, error_code=code, detail=detail)


@contextmanager
def _as_tool_errors() -> Iterator[None]:
    """Raise a rule the driven seat broke as the runner's `MCPToolError`, with the platform's code."""
    try:
        yield
    except RuleError as exc:
        raise _tool_error(exc.code, exc.detail) from exc


@contextmanager
def _opponent_errors(seat: int) -> Iterator[None]:
    try:
        yield
    except RuleError as exc:
        raise OpponentError(f"the opponent in seat {seat} broke a rule: {exc}") from exc
