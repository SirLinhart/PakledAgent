"""Hand-built Pokémon states in the platform guide's shapes (pokemon.md, sections 2, 4, 6 and 7), for the tests.

`card` is a draft card as `available_cards` lists it, and `memory_of` a draft
memory holding our cards and theirs, as after a draft. `pokemon` is a
Pokémon summary of a doubles observation; `doubles` builds a `moving`
observation and its `doubles_turn` template from both sides' actives and each
slot's options (`move`, `switch`, `PASS`), filling `available_moves` and
`available_switches` the way the practice harness does. `envelope` wraps an
observation and its legal actions into the state object the runner hands the
agent.
"""

from __future__ import annotations

from typing import Any, Sequence

from altruagent import GameState

from ucla_agent.games.pokemon import data
from ucla_agent.games.pokemon.draft import DraftMemory
from ucla_agent.games.pokemon.observe import Reader

US, THEM = "agent_us", "agent_them"
SESSION_ID = "pokemon-test"
BOOST_STATS = ("accuracy", "atk", "def", "evasion", "spa", "spd", "spe")
PERCENT = 100
PASS = {"type": "pass"}
TARGET_LEGEND = {
    "1": "opponent position A",
    "2": "opponent position B",
    "-1": "your position A (slot 0)",
    "-2": "your position B (slot 1)",
    "0": "no target needed (self, field or spread move)",
}


def card(
    species: str,
    moves: Sequence[str],
    item: str = "Leftovers",
    ability: str | None = None,
    nature: str = "Serious",
    evs: dict[str, int] | None = None,
) -> dict[str, Any]:
    """A draft card for `species` (card id `vgc-<species id>`), its ability the species' first unless given."""
    return {
        "card_id": f"vgc-{data.to_id(species)}",
        "species": species,
        "item": item,
        "ability": ability or data.species(species).abilities[0],
        "nature": nature,
        "level": 50,
        "evs": evs if evs is not None else {"hp": 252, "atk": 128, "spa": 128},
        "ivs": {},
        "moves": list(moves),
    }


def memory_of(ours: Sequence[dict[str, Any]], theirs: Sequence[dict[str, Any]]) -> DraftMemory:
    """The draft memory after a draft in which we took `ours` and the opponent `theirs`, all seen in the pool."""
    memory = DraftMemory()
    memory.update({"available_cards": [*ours, *theirs], "rosters": {US: [], THEM: []}}, US, Reader())
    rosters = {
        seat: [{"card_id": entry["card_id"], "species": entry["species"]} for entry in held]
        for seat, held in ((US, ours), (THEM, theirs))
    }
    memory.update({"available_cards": [], "available_card_ids": [], "rosters": rosters}, US, Reader())
    return memory


def pokemon(
    species: str,
    hp: float = 1.0,
    *,
    status: str | None = None,
    boosts: dict[str, int] | None = None,
    fainted: bool = False,
    active: bool = True,
) -> dict[str, Any]:
    """A Pokémon summary of a doubles observation, its HP out of 100 and `hp` its share left."""
    record = data.species(species)
    return {
        "species": data.to_id(species),
        "name": record.name,
        "types": [kind.upper() for kind in record.types],
        "base_stats": dict(record.base_stats),
        "level": 50,
        "current_hp": round(hp * PERCENT),
        "max_hp": PERCENT,
        "current_hp_fraction": hp,
        "status": status,
        "stats": None,
        "item": None,
        "ability": None,
        "moves": [],
        "boosts": {stat: (boosts or {}).get(stat, 0) for stat in BOOST_STATS},
        "fainted": fainted,
        "active": active,
        "revealed": True,
        "is_terastallized": False,
    }


def move(move_id: str, targets: Sequence[int]) -> dict[str, Any]:
    """A move option of a slot's template."""
    return {"type": "move", "move_id": data.to_id(move_id), "name": data.move(move_id).name, "targets": list(targets)}


def switch(species: str) -> dict[str, Any]:
    """A switch option of a slot's template."""
    return {"type": "switch", "species": data.to_id(species), "name": data.species(species).name}


def _move_details(option: dict[str, Any]) -> dict[str, Any]:
    record = data.move(option["move_id"])
    return {
        "id": record.id,
        "name": record.name,
        "type": record.type.upper(),
        "category": record.category.upper(),
        "base_power": record.base_power,
        "accuracy": 1.0 if record.accuracy is True else record.accuracy / PERCENT,
        "priority": record.priority,
        "current_pp": 8,
        "max_pp": 8,
        "target": record.target,
        "targets": option["targets"],
    }


def doubles(
    ours: Sequence[dict[str, Any] | None],
    theirs: Sequence[dict[str, Any] | None],
    options: Sequence[Sequence[dict[str, Any]]],
    *,
    reserves: Sequence[dict[str, Any]] = (),
    **board: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A `moving` observation and its `doubles_turn` template: our actives by slot, theirs by position A and B,
    each slot's options; `reserves` are the summaries of the Pokémon the switch options name, and `board` sets the
    observation's other fields (`turn`, `weather`, `fields`, `force_switch`, the side conditions)."""
    observation = {
        "phase": "moving",
        "is_doubles": True,
        "turn": 1,
        "weather": {},
        "fields": {},
        "active_pokemon": list(ours),
        "opponent_active_pokemon": list(theirs),
        "team": _team("p1", [*ours, *reserves]),
        "opponent_team": _team("p2", theirs),
        "available_moves": [_of_kind(slot, "move", _move_details) for slot in options],
        "available_switches": [_of_kind(slot, "switch", _reserve_of(reserves)) for slot in options],
        "force_switch": [False, False],
        "side_conditions": {},
        "opponent_side_conditions": {},
        "can_tera": [False, False],
        **board,
    }
    template = {
        "type": "doubles_turn",
        "instructions": "Submit one choice per slot together.",
        "slots": [_slot(index, entry, slot) for index, (entry, slot) in enumerate(zip(ours, options))],
        "target_legend": dict(TARGET_LEGEND),
    }
    return observation, template


def _team(side: str, entries: Sequence[dict[str, Any] | None]) -> dict[str, dict[str, Any]]:
    return {f"{side}: {entry['name']}": entry for entry in entries if entry is not None}


def _of_kind(options: Sequence[dict[str, Any]], kind: str, made: Any) -> list[dict[str, Any]]:
    return [made(option) for option in options if option["type"] == kind]


def _reserve_of(reserves: Sequence[dict[str, Any]]) -> Any:
    by_species = {entry["species"]: entry for entry in reserves}
    return lambda option: by_species[option["species"]]


def _slot(index: int, entry: dict[str, Any] | None, options: Sequence[dict[str, Any]]) -> dict[str, Any]:
    active = {"species": entry["species"], "name": entry["name"]} if entry else None
    return {"slot": index, "board_position": -(index + 1), "active": active, "options": list(options)}


def legal_action(template: dict[str, Any], action_id: str, state_version: int = 0) -> dict[str, Any]:
    """The one legal action of a team preview or doubles turn, its `input.action` the template."""
    return {
        "action_id": action_id,
        "label": action_id,
        "input": {"session_id": SESSION_ID, "action_id": action_id, "state_version": state_version, "action": template},
    }


def envelope(phase: str, observation: Any, actions: Sequence[dict[str, Any]], state_version: int = 0) -> GameState:
    """The runner's state for an observation and its legal actions, in the guide's envelope (section 2)."""
    return GameState.from_mcp_state(
        {
            "session_id": SESSION_ID,
            "game_type": "pokemon_vgc_doubles_draft",
            "runtime_adapter": "pokemon",
            "status": "in_progress",
            "state_version": state_version,
            "phase": phase,
            "observation": observation,
            "is_terminal": False,
            "is_current_actor": True,
            "current_actor": {"agent_id": US, "position": 0},
            "messaging_enabled": False,
            "legal_actions": {"session_id": SESSION_ID, "state_version": state_version, "actions": list(actions)},
        }
    )
