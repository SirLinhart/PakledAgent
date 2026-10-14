"""The practice card catalog: fixed VGC sets to draft, with the species and move facts the observations show.

`config/pokemon/practice_cards.json` is built once by
`scripts/build_pokemon_cards.py` and tracked. Its cards are the platform's
card shape (species, item, ability, nature, level, EVs, IVs, four moves);
next to them it holds, by Showdown id, the types and base stats of every
forme of each card species, and the type, category, power, accuracy,
priority and target of each card move (plus Struggle and Recharge, which a
request can offer). Showdown's requests give none of those facts, and the
platform's observations show them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ucla_agent.core.paths import repository_root

CATALOG_PATH = Path("config") / "pokemon" / "practice_cards.json"
MIN_CARDS = 60
LEVEL = 50
MOVES_PER_CARD = 4
_NOT_ID_CHARACTERS = re.compile(r"[^a-z0-9]+")


class CatalogError(ValueError):
    """The catalog file breaks one of its rules."""


def to_id(name: str) -> str:
    """Showdown's id of a name: lower case, letters and digits only (`Urshifu-Rapid-Strike` -> `urshifurapidstrike`)."""
    return _NOT_ID_CHARACTERS.sub("", name.lower())


@dataclass(frozen=True)
class Card:
    """One fixed set of the draft pool, as the platform shows a card."""

    card_id: str
    species: str
    item: str
    ability: str
    nature: str
    level: int
    evs: Mapping[str, int]
    ivs: Mapping[str, int]
    moves: tuple[str, ...]

    @property
    def species_id(self) -> str:
        """The species' Showdown id, the one team preview and switches name it by."""
        return to_id(self.species)

    def draft_entry(self) -> dict[str, Any]:
        """The card as a draft observation lists it under `available_cards`."""
        return {
            "card_id": self.card_id,
            "species": self.species,
            "item": self.item,
            "ability": self.ability,
            "nature": self.nature,
            "level": self.level,
            "evs": dict(self.evs),
            "ivs": dict(self.ivs),
            "moves": list(self.moves),
        }

    def showdown_set(self) -> dict[str, Any]:
        """The set handed to Showdown's battle: no nickname, so the battle names it by its base species."""
        return {
            "name": "",
            "species": self.species,
            "item": self.item,
            "ability": self.ability,
            "nature": self.nature,
            "level": self.level,
            "evs": dict(self.evs),
            "ivs": dict(self.ivs),
            "moves": list(self.moves),
        }


@dataclass(frozen=True)
class SpeciesFacts:
    """A species' display name, types (upper case, as the platform writes them) and base stats."""

    name: str
    types: tuple[str, ...]
    base_stats: Mapping[str, int]


@dataclass(frozen=True)
class MoveFacts:
    """A move's display name, type, category, base power, accuracy (a fraction), priority and Showdown target."""

    name: str
    type: str
    category: str
    base_power: int
    accuracy: float
    priority: int
    target: str


@dataclass(frozen=True)
class CardCatalog:
    """The cards in catalog order (most used first) and the facts the observations look up."""

    battle_format: str
    cards: Mapping[str, Card]
    species: Mapping[str, SpeciesFacts]
    moves: Mapping[str, MoveFacts]

    def card(self, card_id: str) -> Card:
        """The card with this id; KeyError for an id the catalog does not hold."""
        return self.cards[card_id]


def load_catalog(path: Path | None = None) -> CardCatalog:
    """Read the catalog file, by default the tracked one under the project root, and check its rules."""
    data = json.loads((path or repository_root() / CATALOG_PATH).read_text(encoding="utf-8"))
    cards = [_card(entry) for entry in data["cards"]]
    _check_cards(cards)
    return CardCatalog(
        battle_format=data["format"],
        cards={card.card_id: card for card in cards},
        species={
            species_id: SpeciesFacts(facts["name"], tuple(facts["types"]), dict(facts["base_stats"]))
            for species_id, facts in data["species"].items()
        },
        moves={move_id: MoveFacts(**facts) for move_id, facts in data["moves"].items()},
    )


def _card(entry: Mapping[str, Any]) -> Card:
    return Card(
        card_id=entry["card_id"],
        species=entry["species"],
        item=entry["item"],
        ability=entry["ability"],
        nature=entry["nature"],
        level=entry["level"],
        evs=dict(entry["evs"]),
        ivs=dict(entry["ivs"]),
        moves=tuple(entry["moves"]),
    )


def _check_cards(cards: list[Card]) -> None:
    """At least `MIN_CARDS` cards, each id once, every card at level 50 with four moves."""
    if len(cards) < MIN_CARDS:
        raise CatalogError(f"the catalog holds {len(cards)} cards, fewer than {MIN_CARDS}")
    ids = [card.card_id for card in cards]
    if len(set(ids)) != len(ids):
        repeated = sorted({card_id for card_id in ids if ids.count(card_id) > 1})
        raise CatalogError(f"card ids {repeated} appear more than once")
    for card in cards:
        _check_card(card)


def _check_card(card: Card) -> None:
    if card.level != LEVEL or len(card.moves) != MOVES_PER_CARD:
        raise CatalogError(f"card {card.card_id} is not a level {LEVEL} set with {MOVES_PER_CARD} moves")
