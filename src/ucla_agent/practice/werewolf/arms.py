"""Who plays our seat in each arm of the Werewolf practice benchmark.

- `baseline`: the practice table's `RecordVoter`, the plain scripted player,
  reading our seat's state object exactly as it does in an opponent's seat:
  with the seat's whole message history under `messages`. Our seat's state
  carries only the open window's `new_messages`, empty at the vote, so the
  seat keeps that history itself from every state it is handed.
- `agent-script`: `WerewolfAgent` with `model_choices` and `model_messages`
  off, so every move and message is its script's.
- `agent-model`: `WerewolfAgent` with the tracked configuration, whose chains
  ask the local model server.

The settings are read once, when the arm is made, so every game of a run
plays with the same ones; a run may give another tuning in place of the
tracked tuning file's, which the `self` mix's copies then play too
(`opponent_factories`). The benchmark never asks Claude: an `agent-model`
arm is refused while `[claude] enabled` is true in `config/agent.toml`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from altruagent import DecisionContext, GameState
from altruagent.runner import MessageDecision

from ucla_agent.core import records
from ucla_agent.core.decide import settings as decide_settings
from ucla_agent.games.werewolf.agent import WerewolfAgent
from ucla_agent.games.werewolf.settings import CHOICE_CHAIN, MESSAGE_CHAIN, WerewolfSettings
from ucla_agent.games.werewolf.tuning import Tuning
from ucla_agent.practice.werewolf.agent_opponent import NEW_MESSAGES_FIELD, AgentOpponent, models_off
from ucla_agent.practice.werewolf.mixes import OPPONENT_FACTORIES, WEREWOLF_AGENT
from ucla_agent.practice.werewolf.opponents import RecordVoter
from ucla_agent.practice.werewolf.seat import Opponent
from ucla_agent.practice.werewolf.view import TRANSCRIPT_FIELD

BASELINE, AGENT_SCRIPT, AGENT_MODEL = "baseline", "agent-script", "agent-model"
ARMS = (BASELINE, AGENT_SCRIPT, AGENT_MODEL)
RECORD_VOTER_NAME = "record_voter"
AGENT_NAME = "WerewolfAgent"
_NS_PER_MS = 1_000_000
_MS_DECIMALS = 3


class ArmRefused(Exception):
    """An arm cannot be played as the configuration stands; the message says why."""


@dataclass(frozen=True)
class Arm:
    """One arm: its name, the factory of our seat's decision-maker, the settings the results record, and the
    model server roles it asks."""

    name: str
    factory: Callable[[], Any]
    settings: dict[str, Any]
    model_roles: frozenset[str] = frozenset()


class RecordVoterSeat:
    """`RecordVoter` as the decision-maker of the driven seat, with each move recorded as the agent records its own.

    `RecordVoter` is handed the seat's state object with every message the
    seat has read so far under `messages`, as an opponent's view has it.
    """

    def __init__(self) -> None:
        self._player = RecordVoter()
        self._read_by_seq: dict[int, Mapping[str, Any]] = {}

    def choose_action(self, state: GameState, _context: DecisionContext) -> int:
        """`RecordVoter`'s kill, investigation or vote for the seat's state object."""
        entered_ns = time.perf_counter_ns()
        action = self._player.choose_action(self._view(state))
        compute_ms = round((time.perf_counter_ns() - entered_ns) / _NS_PER_MS, _MS_DECIMALS)
        records.log_action(state.state_version, str(action), compute_ms)
        return action

    def choose_message(self, state: GameState, _context: DecisionContext) -> MessageDecision:
        """`RecordVoter`'s one chat of the window, its seer claim or its vote, then terminate."""
        return self._player.choose_message(self._view(state))

    def _view(self, state: GameState) -> dict[str, Any]:
        """The state object with the seat's message history: each message of `new_messages` kept once, by `seq`."""
        for message in state.raw.get(NEW_MESSAGES_FIELD) or ():
            self._read_by_seq.setdefault(message["seq"], message)
        history = [self._read_by_seq[seq] for seq in sorted(self._read_by_seq)]
        return {**state.raw, TRANSCRIPT_FIELD: history}


def make_arm(name: str, root: Path, tuning: Tuning | None = None) -> Arm:
    """The arm named `name`, with the settings of the tracked files under `root`; a `tuning` given replaces the
    tracked tuning file's for the agent arms.

    Raises `ValueError` for a name that is not an arm, `ConfigError` for an
    unusable configuration and `ArmRefused` for an `agent-model` arm while
    Claude is switched on.
    """
    if name == BASELINE:
        return Arm(name, RecordVoterSeat, {"decision_maker": RECORD_VOTER_NAME})
    if name not in ARMS:
        raise ValueError(f"no arm is named {name!r}; the arms are {list(ARMS)}")
    played = played_settings(root, tuning)
    if name == AGENT_SCRIPT:
        scripted = models_off(played)
        return Arm(name, lambda: WerewolfAgent(settings=scripted), _described(scripted))
    return Arm(name, lambda: WerewolfAgent(settings=played), _described(played), _model_roles(root))


def opponent_factories(root: Path, tuning: Tuning | None = None) -> dict[str, Callable[[], Opponent]]:
    """The table's opponent factories for a run: the `self` mix's copies play the settings of the tracked files under
    `root`, with `tuning` in place of the tracked tuning file's when given, and their models off."""
    copies = models_off(played_settings(root, tuning))
    return {**OPPONENT_FACTORIES, WEREWOLF_AGENT: lambda: AgentOpponent(copies)}


def played_settings(root: Path, tuning: Tuning | None = None) -> WerewolfSettings:
    """The settings of the tracked files under `root`, with `tuning` in place of the tracked tuning when given."""
    tracked = WerewolfSettings.from_files(root)
    return tracked if tuning is None else replace(tracked, tuning=tuning)


def _described(settings: WerewolfSettings) -> dict[str, Any]:
    """What the results record of a `WerewolfAgent` arm's settings: the model switches and their deadlines."""
    return {
        "decision_maker": AGENT_NAME,
        "model_choices": settings.model_choices,
        "model_messages": settings.model_messages,
        "choice_deadline_ms": settings.choice_deadline_ms,
        "message_deadline_ms": settings.message_deadline_ms,
    }


def _model_roles(root: Path) -> frozenset[str]:
    """The model server roles the agent's two chains name; refuses while Claude is switched on."""
    decide = decide_settings.load(root)
    if decide.claude.enabled:
        raise ArmRefused(
            "[claude] enabled is true in config/agent.toml, and the practice benchmark never asks Claude: "
            "set it to false for the run"
        )
    entries = [entry for chain in (CHOICE_CHAIN, MESSAGE_CHAIN) for entry in decide.chains.get(chain, ())]
    prefix = decide_settings.LOCAL_ENTRY_PREFIX
    return frozenset(entry.removeprefix(prefix) for entry in entries if entry.startswith(prefix))
