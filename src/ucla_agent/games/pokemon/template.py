"""The template-only fallback, and the check of an action against its template.

When a state cannot be read as the guide describes it, the agent still
answers, with an action built from the legal actions alone by the starter
smoke agent's rule (the practice opponent `first` plays the same rule):

- a draft pick: the first card offered, as the first legal action's
  `input.action` names it; when that names no card, the first id of the
  observation's `available_card_ids`;
- `select_lineup`: the template roster's first 4 species, leading with its
  first 2;
- `doubles_turn`: for each slot, `pass` when that is its only option; else its
  first move, aimed at the first opponent target it lists (else its first
  target); else the first switch the other slot has not taken; else `pass`.
  A slot the template does not list passes;
- any other template: the first legal action as it is listed;
- no legal action at all: wait.

`is_legal` checks a `select_lineup` or `doubles_turn` action against its
template, so the agent never sends one the platform would refuse.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from altruagent import WAIT, GameState, LegalAction

from ucla_agent.games.pokemon import data

DRAFT_PICK = "draft_pick"
SELECT_LINEUP = "select_lineup"
DOUBLES_TURN = "doubles_turn"
MOVE, SWITCH, PASS = "move", "switch", "pass"
BRING_COUNT = 4
LEAD_COUNT = 2
SLOT_KEYS = ("slot_0", "slot_1")
NO_TARGET = 0
PASS_CHOICE = {"type": PASS}


def template_of(action: LegalAction) -> Mapping[str, Any] | None:
    """A legal action's `input.action`: a draft pick, or the lineup or turn template; None when it has none."""
    found = action.input.get("action") if isinstance(action.input, Mapping) else None
    return found if isinstance(found, Mapping) else None


def fallback(state: GameState) -> Any:
    """The template-only action for a state (see the module note)."""
    if not state.legal_actions:
        return WAIT
    first = state.legal_actions[0]
    template = template_of(first)
    builder = _BUILDERS.get(template.get("type")) if template is not None else None
    return builder(state, template) or first if builder is not None else first


def _draft(state: GameState, template: Mapping[str, Any]) -> dict[str, Any] | None:
    card_id = template.get("card_id")
    if isinstance(card_id, str):
        return {"type": DRAFT_PICK, "card_id": card_id}
    observation = state.raw.get("observation")
    listed = observation.get("available_card_ids") if isinstance(observation, Mapping) else None
    first = next((entry for entry in listed if isinstance(entry, str)), None) if isinstance(listed, list) else None
    return {"type": DRAFT_PICK, "card_id": first} if first is not None else None


def _lineup(_state: GameState, template: Mapping[str, Any]) -> dict[str, Any] | None:
    roster = template.get("roster")
    if not isinstance(roster, list) or len(roster) < BRING_COUNT:
        return None
    return {"type": SELECT_LINEUP, "bring": list(roster[:BRING_COUNT]), "leads": list(roster[:LEAD_COUNT])}


def _turn(_state: GameState, template: Mapping[str, Any]) -> dict[str, Any]:
    by_index = _slots_by_index(template)
    first = _slot_choice(by_index.get(0, []), taken=None)
    second = _slot_choice(by_index.get(1, []), taken=first.get("species"))
    return {"type": DOUBLES_TURN, SLOT_KEYS[0]: first, SLOT_KEYS[1]: second}


def _slots_by_index(template: Mapping[str, Any]) -> dict[int, list[Mapping[str, Any]]]:
    """Each listed slot's options, by slot number; a slot entry without its number counts by its place."""
    slots = template.get("slots")
    found = {}
    for place, slot in enumerate(slots if isinstance(slots, list) else []):
        if isinstance(slot, Mapping):
            options = slot.get("options")
            found[slot.get("slot", place)] = (
                [option for option in options if isinstance(option, Mapping)] if isinstance(options, list) else []
            )
    return found


def _slot_choice(options: Sequence[Mapping[str, Any]], taken: str | None) -> dict[str, Any]:
    kinds = [option.get("type") for option in options]
    move = next((option for option in options if option.get("type") == MOVE), None)
    if kinds != [PASS] and move is not None:
        return {"type": MOVE, "move_id": move.get("move_id"), "target": _first_target(move)}
    switch = next(
        (option for option in options if option.get("type") == SWITCH and option.get("species") != taken), None
    )
    return {"type": SWITCH, "species": switch.get("species")} if switch is not None else dict(PASS_CHOICE)


def _first_target(option: Mapping[str, Any]) -> int:
    """The first opponent target an option lists, else its first target, else none."""
    targets = [target for target in option.get("targets") or [NO_TARGET] if isinstance(target, int)] or [NO_TARGET]
    return next((target for target in targets if target > 0), targets[0])


_BUILDERS = {DRAFT_PICK: _draft, SELECT_LINEUP: _lineup, DOUBLES_TURN: _turn}


# -- legality ---------------------------------------------------------------------------------------


def is_legal(template: Mapping[str, Any], action: Any) -> bool:
    """Whether a `select_lineup` or `doubles_turn` action keeps its template's rules; False for any other."""
    checker = _CHECKERS.get(template.get("type"))
    return (
        isinstance(action, Mapping)
        and action.get("type") == template.get("type")
        and checker is not None
        and checker(template, action)
    )


def _species_ids(names: Any, count: int, allowed: set[str]) -> list[str] | None:
    """`count` different species ids among `allowed`, read from a list of names; None when they are not."""
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        return None
    ids = [data.to_id(name) for name in names]
    return ids if len(ids) == count == len(set(ids)) and set(ids) <= allowed else None


def _lineup_is_legal(template: Mapping[str, Any], action: Mapping[str, Any]) -> bool:
    roster = template.get("roster")
    allowed = {data.to_id(name) for name in roster if isinstance(name, str)} if isinstance(roster, list) else set()
    bring = _species_ids(action.get("bring"), BRING_COUNT, allowed)
    return bring is not None and _species_ids(action.get("leads"), LEAD_COUNT, set(bring)) is not None


def _turn_is_legal(template: Mapping[str, Any], action: Mapping[str, Any]) -> bool:
    by_index = _slots_by_index(template)
    choices = [action.get(key) for key in SLOT_KEYS]
    offered = all(_is_offered(by_index.get(index, []), choice) for index, choice in enumerate(choices))
    return offered and not _clash(choices)


def _is_offered(options: Sequence[Mapping[str, Any]], choice: Any) -> bool:
    return isinstance(choice, Mapping) and any(_matches(option, choice) for option in options)


def _matches(option: Mapping[str, Any], choice: Mapping[str, Any]) -> bool:
    """Whether a slot's choice is this option: the same move at one of its targets, the same switch, or pass."""
    if option.get("type") != choice.get("type"):
        return False
    if choice["type"] == MOVE:
        return option.get("move_id") == choice.get("move_id") and _target_listed(option, choice)
    return choice["type"] == PASS or option.get("species") == choice.get("species")


def _target_listed(option: Mapping[str, Any], choice: Mapping[str, Any]) -> bool:
    return choice.get("target", NO_TARGET) in (option.get("targets") or [NO_TARGET])


def _clash(choices: Sequence[Mapping[str, Any]]) -> bool:
    """Both slots passing, or both switching into one species."""
    kinds = [choice.get("type") for choice in choices]
    if kinds == [PASS, PASS]:
        return True
    species = {data.to_id(str(choice.get("species"))) for choice in choices}
    return kinds == [SWITCH, SWITCH] and len(species) == 1


_CHECKERS = {SELECT_LINEUP: _lineup_is_legal, DOUBLES_TURN: _turn_is_legal}
