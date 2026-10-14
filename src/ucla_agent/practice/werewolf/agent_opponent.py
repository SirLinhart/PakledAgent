"""The Werewolf agent itself as a practice opponent; six of them make the `self` mix.

`AgentOpponent` plays one opponent seat with a `WerewolfAgent` of its own:
its own ledger, chat memory and decision chains, shared with no other seat.
Its model switches are always off, so a table of them needs no model server;
the rest of its settings are the ones it is given, the tracked files' by
default.

The practice table hands an opponent its seat's state object, a dict, and
the agent reads an `altruagent.GameState`, made here with
`GameState.from_mcp_state` as the runtime makes one from an MCP answer. Its
`new_messages` are the seat's whole transcript (`messages`): on the platform
the runner keeps handing a seat that has terminated every later message of
the window, while the practice table asks an opponent nothing more once it
has terminated, so the transcript is how it reads what was said after that.
The ledger takes each message once, by `seq`.

The opponent runs on the driven seat's thread. What its agent logs (states,
moves, chats, ledgers, errors, its end-of-match usage) is about its own seat,
so the agent is made and asked inside `records.suppressed()` and none of it
is written anywhere.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Mapping

from altruagent import DecisionContext, GameState
from altruagent.runner import MessageDecision

from ucla_agent.core import records
from ucla_agent.core.paths import repository_root
from ucla_agent.games.werewolf.agent import WerewolfAgent
from ucla_agent.games.werewolf.settings import WerewolfSettings
from ucla_agent.practice.werewolf.view import GAME_TYPE, TRANSCRIPT_FIELD, seat_name

NEW_MESSAGES_FIELD = "new_messages"


def models_off(settings: WerewolfSettings) -> WerewolfSettings:
    """`settings` with both model switches off: every move and message is then the agent's script's."""
    return dataclasses.replace(settings, model_choices=False, model_messages=False)


class AgentOpponent:
    """A `WerewolfAgent` with its models off playing an opponent seat; see the module's note."""

    def __init__(self, settings: WerewolfSettings | None = None) -> None:
        """`settings` default to those of the tracked files; their model switches are turned off either way."""
        played = WerewolfSettings.from_files(repository_root()) if settings is None else settings
        with records.suppressed():
            self._agent = WerewolfAgent(settings=models_off(played))

    def choose_action(self, view: Mapping[str, Any]) -> int:
        """The agent's kill, investigation or vote for the seat's state object, as the seat it points at."""
        state = agent_state(view)
        with records.suppressed():
            action = self._agent.choose_action(state, _context(state))
        return int(action.action_id)

    def choose_message(self, view: Mapping[str, Any]) -> MessageDecision:
        """The agent's chat or terminate for the seat's state object."""
        state = agent_state(view)
        with records.suppressed():
            return self._agent.choose_message(state, _context(state))


def agent_state(view: Mapping[str, Any]) -> GameState:
    """The `GameState` the agent reads for an opponent's state object: the seat's transcript as `new_messages`."""
    return GameState.from_mcp_state({**view, NEW_MESSAGES_FIELD: view[TRANSCRIPT_FIELD]})


def _context(state: GameState) -> DecisionContext:
    """The context the runtime would hand the agent of this seat; a practice table belongs to no tournament."""
    seat = state.raw["your_position"]
    return DecisionContext(
        session_id=state.session_id,
        tournament_id=None,
        game_type=GAME_TYPE,
        agent_id=seat_name(seat),
        seat_position=seat,
    )
