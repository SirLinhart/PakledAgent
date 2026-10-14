"""The driven seat of a Pokémon practice match, played by `altruagent.runner.run_game` as if it were an `MCPGameSession`.

`PokemonSeatSession` has the methods the vendored runner calls on its MCP
session, with the same arguments and answers, over a `PokemonPracticeMatch`
in this process: the agent under test plays the driven seat through its real
runner loop, and the other seat is an in-process opponent.

Nothing waits. Before it answers any call, and again after the driven seat's
own move, the session lets the opponent take every decision it has and then
lets the draft clock pick for the driven seat if its 15 seconds have passed,
until the match waits on the driven seat or is over. So `wait_for_update`
returns at once: with `updated: true` when something changed since the
caller's last state, and the unchanged state otherwise.

The opponent is handed its own seat's state object, built exactly as the
driven seat's is (with its legal actions), and nothing else. A rule the
driven seat breaks comes back as the runner's `MCPToolError` with the
platform's error code; a rule the opponent breaks is a bug in the opponent
and raises `OpponentError`. Pokémon has no messaging: `send_message` and
`get_messages` answer `RUNTIME_UNAVAILABLE`, as the platform does.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Protocol

from altruagent import GameState
from altruagent.mcp_transport import MCPToolError

from ucla_agent.practice.pokemon.draft import DRAFT_PHASE
from ucla_agent.practice.pokemon.match import PokemonPracticeMatch
from ucla_agent.practice.pokemon.rules import RUNTIME_UNAVAILABLE, RuleError

DEFAULT_SESSION_ID = "practice-pokemon"


class Opponent(Protocol):
    """An in-process player of the other seat: it is handed that seat's state object and nothing else."""

    def choose(self, state: Mapping[str, Any]) -> Mapping[str, Any]:
        """The structured action (`draft_pick`, `select_lineup` or `doubles_turn`) for a state with a decision."""


class OpponentError(RuntimeError):
    """The opponent answered with something the rules refuse: a bug in that opponent, not in the driven seat."""


class PracticeStalled(RuntimeError):
    """Neither seat has a decision and the match is not over: nothing could ever wake the driven seat."""


class PokemonSeatSession:
    """The driven seat of a practice match, with the methods `run_game` calls on an `MCPGameSession`."""

    def __init__(
        self, match: PokemonPracticeMatch, seat: int, opponent: Opponent, session_id: str = DEFAULT_SESSION_ID
    ) -> None:
        self.session_id = session_id
        self.seat = seat
        self.decisions = 0
        self._match = match
        self._opponent = opponent

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

        `since_message_seq` and `timeout_seconds` are accepted for the
        runner's sake: there are no messages, and with the opponent settled
        nothing more can change while waiting.
        """
        self._settle()
        state = self._state()
        if not (state["is_terminal"] or state["is_current_actor"] or self._match.has_decision(1 - self.seat)):
            raise PracticeStalled(f"seat {self.seat} waits but no seat has a decision: {state['observation']}")
        state["updated"] = _changed(state, since_version, since_is_current_actor, since_phase)
        return GameState.from_mcp_state(state)

    def get_legal_actions(self) -> dict[str, Any]:
        """`get_legal_actions`: the actions the seat may take now, none when it has no decision."""
        self._settle()
        return self._match.legal_actions_payload(self.seat, self.session_id)

    def play_action(
        self,
        *,
        action_id: str | None = None,
        action: dict | None = None,
        state_version: int,
        reasoning_summary: str | None = None,
    ) -> dict[str, Any]:
        """`play_action`: apply the seat's move; the answer carries the seat's next state while the match goes on."""
        self._settle()
        drafting = self._match.phase(self.seat) == DRAFT_PHASE
        with _as_tool_errors():
            self._match.play(self.seat, state_version, action_id, action, reasoning_summary)
        self.decisions += 1
        self._settle()
        state = self._state()
        answer = {
            "accepted": True,
            "session_id": self.session_id,
            "state_version": state["state_version"],
            "status": state["status"],
            "next_actions": state["next_actions"],
        }
        if drafting:
            answer.update(self._draft_reply())
        if not state["is_terminal"]:
            answer["state"] = state
        return answer

    def send_message(self, **_message: Any) -> dict[str, Any]:
        """`send_message`: Pokémon has no messaging."""
        raise _tool_error(RUNTIME_UNAVAILABLE, "Pokémon has no messaging")

    def get_messages(self, **_since: Any) -> dict[str, Any]:
        """`get_messages`: Pokémon has no messaging."""
        raise _tool_error(RUNTIME_UNAVAILABLE, "Pokémon has no messaging")

    def resign(self) -> dict[str, Any]:
        """`resign`: forfeit the match at once; answers with the `get_result` object."""
        self._settle()
        with _as_tool_errors():
            self._match.resign(self.seat)
        return self._match.result(self.seat, self.session_id)

    def get_result(self) -> dict[str, Any]:
        """`get_result`: the returns once the match is over, nulls while it runs."""
        self._settle()
        return self._match.result(self.seat, self.session_id)

    # -- the opponent's decisions and the draft clock ----------------------------------------------

    def _settle(self) -> None:
        """Let the opponent decide, and the draft clock pick, until the match waits on the driven seat or is over."""
        other = 1 - self.seat
        while True:
            if self._match.has_decision(other):
                self._let_opponent_act(other)
            elif not self._match.apply_timeouts():
                return

    def _let_opponent_act(self, other: int) -> None:
        state = self._match.state(other, self.session_id)
        action = self._opponent.choose(state)
        try:
            self._match.play(other, state["state_version"], action=action)
        except RuleError as exc:
            raise OpponentError(f"the opponent broke a rule with {action!r}: {exc}") from exc

    def _state(self) -> dict[str, Any]:
        return self._match.state(self.seat, self.session_id)

    def _draft_reply(self) -> dict[str, Any]:
        """What the guide says a pick's reply shows: the picks left, the next drafter, whether the battle starts."""
        draft = self._match.draft
        return {
            "picks_remaining": len(draft.order) - len(draft.picks),
            "current_seat": draft.current_seat,
            "draft_complete": draft.is_complete,
            "battle_starting": draft.is_complete,
        }


def _changed(
    state: Mapping[str, Any], since_version: int, since_is_current_actor: bool | None, since_phase: str | None
) -> bool:
    """Whether the state differs from what the caller saw; a `since_*` value left out is not compared."""
    seen = (since_version, since_is_current_actor, since_phase)
    now = (state["state_version"], state["is_current_actor"], state["phase"])
    return state["is_terminal"] or any(old is not None and old != new for old, new in zip(seen, now))


def _tool_error(code: str, detail: str) -> MCPToolError:
    return MCPToolError(f"{code}: {detail}", status_code=None, error_code=code, detail=detail)


@contextmanager
def _as_tool_errors() -> Iterator[None]:
    """Raise a rule the driven seat broke as the runner's `MCPToolError`, with the platform's code."""
    try:
        yield
    except RuleError as exc:
        raise _tool_error(exc.code, exc.detail) from exc
