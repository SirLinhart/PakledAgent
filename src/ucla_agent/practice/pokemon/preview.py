"""Team preview (pokemon.md, section 6): Showdown's team-preview request to the platform's observation and template.

Each seat sees both full rosters: its own as Showdown's request gives it (in
draft order), and the opponent's six drafted cards, which the draft made
public. The one legal action, `select_lineup`, carries a template listing the
seat's roster species ids. The seat answers with 4 different species to
bring and 2 of those to lead; case and punctuation are ignored. Showdown
takes that as `team` followed by the roster positions, leads first.

Species are named by Showdown id (`urshifurapidstrike`), items, abilities
and moves too, as the guide's examples show the species. Types and base
stats come from the catalog's species facts: Showdown's request gives
neither.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ucla_agent.practice.pokemon.cards import Card, CardCatalog, to_id
from ucla_agent.practice.pokemon.rules import INVALID_ACTION, RuleError

PREVIEW_PHASE = "team_preview"
LINEUP_TYPE = "select_lineup"
BRING_COUNT = 4
LEAD_COUNT = 2
LEVEL_PREFIX = "L"
INSTRUCTIONS = (
    f"Bring {BRING_COUNT} different species from `roster` and lead with {LEAD_COUNT} of them: "
    f'play_action(action={{"type": "{LINEUP_TYPE}", "bring": [{BRING_COUNT} species], '
    f'"leads": [{LEAD_COUNT} of those]}}). Case and punctuation are ignored.'
)


def species_of(details: str) -> str:
    """The species in a Showdown `details` string: `Urshifu-Rapid-Strike, L50, F` -> `Urshifu-Rapid-Strike`."""
    return details.split(",")[0]


def level_of(details: str) -> int:
    """The level in a `details` string; Showdown leaves out level 100."""
    levels = [part.strip()[1:] for part in details.split(",")[1:] if part.strip().startswith(LEVEL_PREFIX)]
    return int(levels[0]) if levels else 100


def own_roster_entry(pokemon: Mapping[str, Any], catalog: CardCatalog) -> dict[str, Any]:
    """One Pokémon of the seat's own roster, from the request's `side.pokemon` entry."""
    species = species_of(pokemon["details"])
    facts = catalog.species[to_id(species)]
    return {
        "species": to_id(species),
        "name": species,
        "types": list(facts.types),
        "base_stats": dict(facts.base_stats),
        "level": level_of(pokemon["details"]),
        "max_hp": int(pokemon["condition"].split("/")[1].split(" ")[0]),
        "stats": dict(pokemon["stats"]),
        "item": pokemon["item"],
        "ability": pokemon["ability"],
        "moves": list(pokemon["moves"]),
    }


def opponent_roster_entry(card: Card, catalog: CardCatalog) -> dict[str, Any]:
    """One Pokémon of the opponent's roster: its drafted card, public since the draft."""
    facts = catalog.species[card.species_id]
    return {
        "species": card.species_id,
        "name": card.species,
        "types": list(facts.types),
        "base_stats": dict(facts.base_stats),
        "level": card.level,
        "item": to_id(card.item),
        "ability": to_id(card.ability),
        "moves": [to_id(move) for move in card.moves],
    }


def roster_ids(request: Mapping[str, Any]) -> list[str]:
    """The species ids of the seat's roster, in the request's order (the order Showdown numbers them)."""
    return [to_id(species_of(pokemon["details"])) for pokemon in request["side"]["pokemon"]]


def preview_observation(
    request: Mapping[str, Any], opponent_cards: Sequence[Card], catalog: CardCatalog
) -> dict[str, Any]:
    """The seat's team preview observation, built from its own team-preview request and the opponent's cards."""
    return {
        "phase": PREVIEW_PHASE,
        "battle_format": catalog.battle_format,
        "waiting_for_action": True,
        "your_roster": [own_roster_entry(pokemon, catalog) for pokemon in request["side"]["pokemon"]],
        "opponent_roster": [opponent_roster_entry(card, catalog) for card in opponent_cards],
    }


def lineup_legal_action(request: Mapping[str, Any], session_id: str, state_version: int) -> dict[str, Any]:
    """The single `select_lineup` legal action, whose `input.action` is the template to fill in."""
    return {
        "action_id": LINEUP_TYPE,
        "label": f"Choose {BRING_COUNT} of your roster to bring and {LEAD_COUNT} of them to lead",
        "input": {
            "session_id": session_id,
            "action_id": LINEUP_TYPE,
            "state_version": state_version,
            "action": {
                "type": LINEUP_TYPE,
                "roster": roster_ids(request),
                "bring_count": BRING_COUNT,
                "lead_count": LEAD_COUNT,
                "instructions": INSTRUCTIONS,
            },
        },
    }


def lineup_choice(action: Mapping[str, Any] | None, request: Mapping[str, Any]) -> str:
    """Showdown's `team` choice for a `select_lineup` action, leads first; INVALID_ACTION for a lineup it breaks."""
    if not isinstance(action, Mapping) or action.get("type") != LINEUP_TYPE:
        raise RuleError(INVALID_ACTION, f"team preview needs a structured {LINEUP_TYPE} action, not {action!r}")
    roster = roster_ids(request)
    bring = _species_list(action.get("bring"), BRING_COUNT, "bring", roster)
    leads = _species_list(action.get("leads"), LEAD_COUNT, "leads", bring)
    order = leads + [species for species in bring if species not in leads]
    return "team " + "".join(str(roster.index(species) + 1) for species in order)


def _species_list(names: Any, count: int, field: str, allowed: Sequence[str]) -> list[str]:
    """`count` different species ids from `allowed`, read from the names given; INVALID_ACTION otherwise."""
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise RuleError(INVALID_ACTION, f"{field} must be a list of {count} species, not {names!r}")
    species = [to_id(name) for name in names]
    if len(set(species) & set(allowed)) != count or len(species) != count:
        raise RuleError(INVALID_ACTION, f"{field} must be {count} different species out of {list(allowed)}")
    return species
