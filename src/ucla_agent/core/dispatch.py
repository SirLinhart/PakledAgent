"""Hands each match to the decision-maker of its game.

The vendored runtime builds one agent per match, in that match's own worker
process, and calls `choose_action`, `choose_message` and `on_action_result` on
it. The dispatcher is that agent: on its first call it finds out which game
the match is and from then on passes every call to that game's
decision-maker. It decides nothing about play itself.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from altruagent import TERMINATE_MESSAGING, WAIT, DecisionContext, GameState
from altruagent.runner import Decision, MessageDecision

from ucla_agent.core import records, registry


class UnsupportedGameAgent:
    """Decision-maker for a game that has none registered: answers with something the runtime always accepts."""

    def choose_action(self, state: GameState, _context: DecisionContext) -> Decision:
        """The first legal action, or `WAIT` when the game lists none."""
        return state.legal_actions[0] if state.legal_actions else WAIT

    def choose_message(self, _state: GameState, _context: DecisionContext) -> MessageDecision:
        """Vote to close the messaging round."""
        return TERMINATE_MESSAGING


class Dispatcher:
    """The agent for one match: forwards the runtime's calls to the decision-maker of the match's game."""

    def __init__(self, factories: Mapping[str, Callable[[], Any]] | None = None) -> None:
        """`factories` maps a game type to its decision-maker's factory; left out, the registered games are played.

        Naming them lets a harness play a game with a decision-maker other
        than the registered one, or with one for a game not registered yet.
        """
        self._decision_maker: Any = None
        self._factories = factories

    @property
    def decision_maker(self) -> Any:
        """The decision-maker of the match's game, made on the first call; None before it."""
        return self._decision_maker

    def choose_action(self, state: GameState, context: DecisionContext) -> Decision:
        """Forward a request for a move."""
        return self._decision_maker_for(context, state).choose_action(state, context)

    # The runtime looks for the two optional hooks below on the agent object
    # before the game is known, so the dispatcher always has them and applies
    # the runtime's own default when the game's decision-maker lacks one.

    def choose_message(self, state: GameState, context: DecisionContext) -> MessageDecision:
        """Forward a request for a chat message; close the round when the game's decision-maker has no say."""
        choose_message = getattr(self._decision_maker_for(context, state), "choose_message", None)
        return choose_message(state, context) if choose_message else TERMINATE_MESSAGING

    def on_action_result(self, result: dict, context: DecisionContext) -> None:
        """Forward the platform's answer to a move, when the game's decision-maker wants it."""
        on_action_result = getattr(self._decision_maker_for(context, None), "on_action_result", None)
        if on_action_result:
            on_action_result(result, context)

    def _decision_maker_for(self, context: DecisionContext, state: GameState | None) -> Any:
        if self._decision_maker is None:
            records.start_match(context, state)
            factories = registry.GAME_FACTORIES if self._factories is None else self._factories
            factory = factories.get(context.game_type)
            if factory is None:
                records.log("unsupported_game", {"game_type": context.game_type})
                factory = UnsupportedGameAgent
            self._decision_maker = factory()
        return self._decision_maker
