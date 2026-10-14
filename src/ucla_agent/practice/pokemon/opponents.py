"""The practice opponents of a Pokémon match: `random`, `first` and `self`.

Each opponent is handed its seat's state object (the platform's envelope,
with its legal actions) and answers with the structured action to send:

- `first` plays the starter's smoke agent rule
  (`examples/smoke_agent.py`): the first card offered; the first four of the
  roster, leading with the first two; for each slot the first move, aimed at
  its first opponent target (else its first target), else the first switch
  the other slot has not taken, else pass.
- `random` makes a seeded random legal choice in every phase: a card
  offered, four of the six with two of them leading, and for each slot a
  random option and target, never two switches into one Pokémon nor two
  passes.
- `self` is the agent itself, a `PokemonAgent` with its model switches off,
  asked inside `records.suppressed()` so that nothing it logs reaches the
  driven seat's record. `PokemonAgent` is imported when a `self` opponent is
  made, not before, so the other two work without it.

`PolicyAgent` plays an opponent kind's rule as the driven seat's
decision-maker, over the runner's `GameState`, for baselines such as
`first` against `random`.
"""

from __future__ import annotations

import dataclasses
import random
from typing import Any, Callable, Mapping, Sequence

from altruagent import DecisionContext, GameState, LegalAction, WithReasoning

from ucla_agent.core import records
from ucla_agent.core.paths import repository_root

RANDOM = "random"
FIRST = "first"
SELF = "self"
MOVE, SWITCH, PASS = "move", "switch", "pass"
DRAFT_PICK = "draft_pick"
SELECT_LINEUP = "select_lineup"
DOUBLES_TURN = "doubles_turn"
BRING_COUNT = 4
LEAD_COUNT = 2
NO_TARGET = 0
MODEL_SWITCH_PREFIX = "model"
GAME_TYPE = "pokemon_vgc_doubles_draft"
_RANDOM_SEED = "pokemon-random-opponent:{seed}"

PASS_CHOICE = {"type": PASS}


def template_of(state: Mapping[str, Any]) -> Mapping[str, Any]:
    """The `input.action` of the state's first legal action: a draft pick, or the lineup or turn template."""
    return state["legal_actions"]["actions"][0]["input"]["action"]


def option_choice(option: Mapping[str, Any], target: int) -> dict[str, Any]:
    """A template option as the slot's answer, a move aimed at `target`."""
    if option["type"] == MOVE:
        return {"type": MOVE, "move_id": option["move_id"], "target": target}
    if option["type"] == SWITCH:
        return {"type": SWITCH, "species": option["species"]}
    return dict(PASS_CHOICE)


def _species(choice: Mapping[str, Any]) -> str | None:
    return choice.get("species") if choice["type"] == SWITCH else None


class FirstOpponent:
    """The starter smoke agent's rule: the first legal option everywhere (see the module note)."""

    def choose(self, state: Mapping[str, Any]) -> dict[str, Any]:
        template = template_of(state)
        if template["type"] == SELECT_LINEUP:
            roster = template["roster"]
            return {"type": SELECT_LINEUP, "bring": roster[:BRING_COUNT], "leads": roster[:LEAD_COUNT]}
        if template["type"] == DOUBLES_TURN:
            slot_0 = _first_slot_choice(template["slots"][0]["options"], taken=None)
            slot_1 = _first_slot_choice(template["slots"][1]["options"], taken=_species(slot_0))
            return {"type": DOUBLES_TURN, "slot_0": slot_0, "slot_1": slot_1}
        return dict(template)


def _first_slot_choice(options: Sequence[Mapping[str, Any]], taken: str | None) -> dict[str, Any]:
    move = next((option for option in options if option["type"] == MOVE), None)
    if move is not None:
        targets = move["targets"]
        return option_choice(move, next((target for target in targets if target > 0), targets[0]))
    switch = next((option for option in options if option["type"] == SWITCH and option["species"] != taken), None)
    return option_choice(switch, NO_TARGET) if switch is not None else dict(PASS_CHOICE)


class RandomOpponent:
    """A seeded random legal choice in every phase (see the module note)."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(_RANDOM_SEED.format(seed=seed))

    def choose(self, state: Mapping[str, Any]) -> dict[str, Any]:
        template = template_of(state)
        if template["type"] == SELECT_LINEUP:
            bring = self._rng.sample(list(template["roster"]), BRING_COUNT)
            return {"type": SELECT_LINEUP, "bring": bring, "leads": self._rng.sample(bring, LEAD_COUNT)}
        if template["type"] == DOUBLES_TURN:
            return self._turn(template["slots"])
        return dict(self._rng.choice(state["legal_actions"]["actions"])["input"]["action"])

    def _turn(self, slots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        slot_0 = self._slot_choice(slots[0]["options"])
        others = [option for option in slots[1]["options"] if _allowed_beside(option, slot_0)]
        return {"type": DOUBLES_TURN, "slot_0": slot_0, "slot_1": self._slot_choice(others)}

    def _slot_choice(self, options: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        option = self._rng.choice(list(options))
        targets = option.get("targets") or [NO_TARGET]
        return option_choice(option, self._rng.choice(list(targets)))


def _allowed_beside(option: Mapping[str, Any], other: Mapping[str, Any]) -> bool:
    """Whether slot 1 may take `option` beside slot 0's choice: not the same switch, not a second pass."""
    if option["type"] == PASS:
        return other["type"] != PASS
    return option["type"] != SWITCH or option["species"] != _species(other)


def models_off(settings: Any) -> Any:
    """The agent's settings with every model switch (a true/false field named `model...`) off."""
    switches = {
        field.name: False
        for field in dataclasses.fields(settings)
        if field.name.startswith(MODEL_SWITCH_PREFIX) and isinstance(getattr(settings, field.name), bool)
    }
    return dataclasses.replace(settings, **switches)


class SelfOpponent:
    """The Pokémon agent itself, models off, recording nothing (see the module note)."""

    def __init__(self, seed: int | None = None) -> None:
        """`seed` is accepted like the other kinds'; the agent's script has no randomness of its own to seed."""
        from ucla_agent.games.pokemon.agent import PokemonAgent
        from ucla_agent.games.pokemon.settings import PokemonSettings

        with records.suppressed():
            self._agent = PokemonAgent(settings=models_off(PokemonSettings.from_files(repository_root())))

    def choose(self, state: Mapping[str, Any]) -> dict[str, Any]:
        game_state = GameState.from_mcp_state(dict(state))
        with records.suppressed():
            decision = self._agent.choose_action(game_state, _context(state))
        return structured_action(decision, game_state)


def _context(state: Mapping[str, Any]) -> DecisionContext:
    actor = state.get("current_actor") or {}
    return DecisionContext(
        session_id=state["session_id"],
        tournament_id=None,
        game_type=GAME_TYPE,
        agent_id=str(actor.get("agent_id", "")),
        seat_position=actor.get("position"),
    )


def structured_action(decision: Any, state: GameState) -> dict[str, Any]:
    """An agent's decision as the structured action it stands for: a dict as it is, a legal action's template."""
    if isinstance(decision, WithReasoning):
        decision = decision.action
    if isinstance(decision, dict):
        return decision
    if isinstance(decision, str):
        decision = next(action for action in state.legal_actions if action.action_id == decision)
    if isinstance(decision, LegalAction):
        return dict(decision.input["action"])
    raise ValueError(f"the agent answered {decision!r}, which no practice opponent can send")


OPPONENT_FACTORIES: dict[str, Callable[[int | None], Any]] = {
    RANDOM: RandomOpponent,
    FIRST: lambda _seed=None: FirstOpponent(),
    SELF: SelfOpponent,
}


def make_opponent(kind: str, seed: int | None = None) -> Any:
    """An opponent of a kind of `OPPONENT_FACTORIES`, seeded where the kind uses randomness."""
    if kind not in OPPONENT_FACTORIES:
        raise ValueError(f"unknown opponent kind {kind!r}: one of {sorted(OPPONENT_FACTORIES)}")
    return OPPONENT_FACTORIES[kind](seed)


class PolicyAgent:
    """An opponent kind's rule as the driven seat's decision-maker: `choose_action` over the runner's `GameState`."""

    def __init__(self, kind: str, seed: int | None = None) -> None:
        self._policy = make_opponent(kind, seed)

    def choose_action(self, state: GameState, _context: DecisionContext) -> dict[str, Any]:
        return self._policy.choose(state.raw)
