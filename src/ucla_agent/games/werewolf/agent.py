"""Werewolf decision-maker: the seat's ledger and beliefs kept up to date, and each move and message decided from them.

The runtime calls `choose_message` once per change while a discussion window
is open and `choose_action` when our seat is to vote, kill or investigate.
Every call first takes the state into the ledger and infers the roles; the
first state of each discussion window also writes the day's `werewolf_ledger`
event. A move is then decided by `decide` and a message by `chat`.

The settings are read once, when the agent is made, as are the two chains of
decision sources: one agent plays one match, so a source's per-match counts
(Claude's token cap) are the match's. At the end of the match each chain
writes what its sources used.

An eliminated seat is never asked by the runtime; asked anyway, it
terminates every window and plays the first legal action without asking a
model. Whatever goes wrong inside a call is recorded as an `unhandled` error
and answered with something the platform takes: the first legal action that
does not abstain, or a terminate.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import TracebackType
from typing import Any

from altruagent import TERMINATE_MESSAGING, DecisionContext, GameState, LegalAction
from altruagent.runner import MessageDecision

from ucla_agent.core import records
from ucla_agent.core.config import ConfigError
from ucla_agent.core.decide import ChainSource, DecisionSource, build_chain
from ucla_agent.core.paths import repository_root
from ucla_agent.games.werewolf.chat import Chat
from ucla_agent.games.werewolf.decide import decide_action, legal_action
from ucla_agent.games.werewolf.inference import Beliefs, infer
from ucla_agent.games.werewolf.ledger import Ledger
from ucla_agent.games.werewolf.settings import CHOICE_CHAIN, MESSAGE_CHAIN, WerewolfSettings

LEDGER_EVENT = "werewolf_ledger"
# The type of an `error` event for a failure caught only at the outermost level of a call.
UNHANDLED_ERROR = "unhandled"
_CHAIN_ERROR_SOURCE = "werewolf.chains"
_PROBABILITY_DECIMALS = 4
_NS_PER_MS = 1_000_000
_MS_DECIMALS = 3


class WerewolfAgent:
    """Decision-maker for one Werewolf match. See the module's note."""

    def __init__(
        self,
        settings: WerewolfSettings | None = None,
        *,
        choice_source: DecisionSource | None = None,
        message_source: DecisionSource | None = None,
    ) -> None:
        """An agent for a match about to start.

        `settings` default to those of the tracked files. A source left out
        is the chain `config/agent.toml` names for it when its switch is on,
        and a chain of no source when it is off; tests give stubs.
        """
        self._settings = WerewolfSettings.from_files(repository_root()) if settings is None else settings
        self._choice_source = choice_source or _chain(CHOICE_CHAIN, self._settings.model_choices)
        self._message_source = message_source or _chain(MESSAGE_CHAIN, self._settings.model_messages)
        self._ledger = Ledger(self._settings.tuning.chat)
        self._chat = Chat(self._settings, self._message_source)
        records.on_match_end(self.finalize)

    def choose_action(self, state: GameState, _context: DecisionContext) -> LegalAction:
        """Our vote, kill or investigation: one of `state.legal_actions`, whatever goes wrong."""
        entered_ns = time.perf_counter_ns()
        try:
            beliefs = self._take(state)
            if self._ledger.facts().eliminated:
                action = state.legal_actions[0]
            else:
                action = decide_action(state, self._ledger, beliefs, self._settings, self._choice_source)
        except Exception as exc:  # noqa: BLE001 - the runtime gets a legal move whatever went wrong in making one
            _log_error("choose_action", exc)
            action = legal_action(state, ())
        compute_ms = round((time.perf_counter_ns() - entered_ns) / _NS_PER_MS, _MS_DECIMALS)
        records.log_action(state.state_version, action.action_id, compute_ms)
        return action

    def choose_message(self, state: GameState, _context: DecisionContext) -> MessageDecision:
        """This call's chat, or terminate: always terminate for an eliminated seat or when anything goes wrong."""
        try:
            beliefs = self._take(state)
            return self._chat.decide(self._ledger, beliefs, state.raw.get("messaging_seconds_left"))
        except Exception as exc:  # noqa: BLE001 - closing our part of the window is always an answer the runtime takes
            _log_error("choose_message", exc)
            return TERMINATE_MESSAGING

    def finalize(self) -> None:
        """Have both chains write what their sources used in the match; called once, when the match record closes."""
        self._choice_source.write_usage_event()
        self._message_source.write_usage_event()

    def _take(self, state: GameState) -> Beliefs:
        """Record the state, take it into the ledger and infer the roles; on a new day, write the day's ledger."""
        records.log_state(state.raw)
        self._ledger.update(state)
        beliefs = infer(self._ledger, self._settings.tuning.inference)
        if self._ledger.is_new_day():
            records.log(LEDGER_EVENT, ledger_event(self._ledger, beliefs))
        return beliefs


def ledger_event(ledger: Ledger, beliefs: Beliefs) -> dict[str, Any]:
    """The day's `werewolf_ledger` event: the engine facts, our private facts, the claims and each seat's beliefs."""
    facts, private = ledger.facts(), ledger.private()
    return {
        "window": ledger.window_index,
        "day": facts.day,
        "facts": {
            "seat": facts.seat,
            "living": list(facts.living),
            "deaths": [
                {"day": death.day, "seat": death.seat, "role": death.role, "cause": death.cause}
                for death in facts.deaths
            ],
            "votes": [{"day": vote.day, "votes": vote.votes, "eliminated": vote.eliminated} for vote in facts.votes],
        },
        "private": {"role": private.role, "allies": list(private.allies), "results": private.results},
        "claims": [
            {
                "kind": claim.kind,
                "seat": claim.seat,
                "target": claim.target,
                "result": claim.result,
                "day": claim.day,
                "window": claim.window,
            }
            for claim in ledger.claims()
        ],
        "p_wolf": {seat: round(share, _PROBABILITY_DECIMALS) for seat, share in beliefs.p_wolf.items()},
        "p_seer": {seat: round(share, _PROBABILITY_DECIMALS) for seat, share in beliefs.p_seer.items()},
    }


def _chain(name: str, switched_on: bool) -> ChainSource:
    """The chain `config/agent.toml` names, when its switch is on; a chain of no source when it is off or unusable."""
    if switched_on:
        try:
            return build_chain(name)
        except ConfigError as exc:
            _log_error(_CHAIN_ERROR_SOURCE, exc, records.HANDLED_ERROR)
    return ChainSource([], name=name)


def _log_error(where: str, error: Exception, kind: str = UNHANDLED_ERROR) -> None:
    """Record a failure the agent played on from: where it was caught, what it was and where it was raised.

    Only an exception that was raised and caught is given here, so it always has a traceback.
    """
    records.log(
        "error",
        {
            "where": where,
            "type": kind,
            "exception": type(error).__name__,
            "message": f"{error}{_raised_at(error.__traceback__)}",
        },
    )


def _raised_at(trace: TracebackType) -> str:
    """Where a caught exception was raised: the file name, line and function of the last frame of its traceback."""
    while trace.tb_next is not None:
        trace = trace.tb_next
    code = trace.tb_frame.f_code
    return f" (raised at {Path(code.co_filename).name}:{trace.tb_lineno} in {code.co_name})"
