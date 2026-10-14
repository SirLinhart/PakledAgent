"""Build the Pokémon practice card catalog, `config/pokemon/practice_cards.json`, from the cached Smogon usage statistics.

    python scripts/build_pokemon_cards.py [--stats PATH] [--output PATH]

Run it from the repository root once `npm ci` has been run in
`tools/pokemon_sim`: names, types, base stats, move data and set validation
come from Showdown's own dex through `tools/pokemon_sim/dex.js`. The
statistics are the untracked reference copy
`docs/reference/research/pokemon/smogon-stats/gen9vgc2025regi-1630.json`;
only the catalog it writes is tracked, and the tests read only that.

Each card is one fixed set, built the way the platform describes its cards:
for a species, in order of usage, its most used item, ability and spread
(nature and EVs) and its four most used moves, at level 50, with card id
`vgc-<species slug>` (`vgc-urshifu-rapid-strike`). A species is left out when:

- its set fails Showdown's set validator for `gen9vgc2025regi`;
- it shares its base species with a card already taken: Showdown names a
  Pokémon by its base species in battle (`p1: Calyrex`), so two such cards on
  one team could not be told apart;
- its set uses Illusion, Imposter, Transform, Revival Blessing, Sketch or
  Mimic, which change a Pokémon's identity or moves mid-battle in ways the
  practice tables do not follow.

Next to the cards it writes the facts the observation conversion reads:
each card species' every forme (types and base stats), and each card move
plus Struggle and Recharge (type, category, power, accuracy, priority,
target). Running it again on the same statistics writes the same bytes.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from ucla_agent.core.console import print_line

FORMAT = "gen9vgc2025regi"
STATS_PATH = Path("docs") / "reference" / "research" / "pokemon" / "smogon-stats" / f"{FORMAT}-1630.json"
OUTPUT_PATH = Path("config") / "pokemon" / "practice_cards.json"
DEX_SCRIPT = Path("tools") / "pokemon_sim" / "dex.js"
CARD_COUNT = 72
LEVEL = 50
MOVES_PER_CARD = 4
CARD_ID_PREFIX = "vgc-"
# Usage tables list an empty move slot and a missing item under these keys.
NO_ENTRY = frozenset({"", "nothing"})
STAT_ORDER = ("hp", "atk", "def", "spa", "spd", "spe")
UNFOLLOWED_ABILITIES = frozenset({"illusion", "imposter"})
UNFOLLOWED_MOVES = frozenset({"transform", "revivalblessing", "sketch", "mimic"})
# A request offers Struggle when no move is usable, and Recharge after a move like Hyper Beam; no
# card holds either. Showdown's dex knows Struggle as a move, and Recharge only inside its requests.
ALWAYS_LISTED_MOVES = ("struggle",)
RECHARGE_FACTS = {
    "name": "Recharge",
    "type": "NORMAL",
    "category": "STATUS",
    "base_power": 0,
    "accuracy": 1.0,
    "priority": 0,
    "target": "self",
}
PERCENT = 100
JSON_INDENT = 2

# A dex query (see tools/pokemon_sim/dex.js) and its answer.
DexLookup = Callable[[dict[str, Any]], dict[str, Any]]


def card_slug(species_name: str) -> str:
    """`Urshifu-Rapid-Strike` -> `urshifu-rapid-strike`: lower case, every run of other characters one hyphen."""
    return re.sub(r"[^a-z0-9]+", "-", species_name.lower()).strip("-")


def most_used(weights: dict[str, float], count: int = 1) -> list[str]:
    """The `count` keys of a usage table with the highest weights, ties by key; empty slots left out."""
    ranked = sorted((key for key in weights if key not in NO_ENTRY), key=lambda key: (-weights[key], key))
    return ranked[:count]


def spread_of(spread: str) -> tuple[str, dict[str, int]]:
    """`Adamant:4/252/0/0/0/252` -> (`Adamant`, {"hp": 4, "atk": 252, "spe": 252}); zero EVs left out."""
    nature, values = spread.split(":")
    evs = {stat: int(value) for stat, value in zip(STAT_ORDER, values.split("/")) if int(value)}
    return nature, evs


def usage_sets(stats: dict[str, Any]) -> list[dict[str, Any]]:
    """Each species' most used set in usage order, with Smogon's ids for item, ability and moves."""
    data = stats["data"]
    ranked = sorted(data, key=lambda species: (-data[species]["usage"], species))
    sets = []
    for species in ranked:
        entry = data[species]
        items = most_used(entry["Items"])
        nature, evs = spread_of(most_used(entry["Spreads"])[0])
        sets.append(
            {
                "species": species,
                "item": items[0] if items else "",
                "ability": most_used(entry["Abilities"])[0],
                "nature": nature,
                "evs": evs,
                "moves": most_used(entry["Moves"], MOVES_PER_CARD),
            }
        )
    return sets


def _follows(usage_set: dict[str, Any]) -> bool:
    """Whether the practice tables can follow this set through a battle."""
    return usage_set["ability"] not in UNFOLLOWED_ABILITIES and UNFOLLOWED_MOVES.isdisjoint(usage_set["moves"])


def _named_set(usage_set: dict[str, Any], names: dict[str, Any]) -> dict[str, Any]:
    """The usage set with Showdown's display names, as the platform shows a card's set."""
    return {
        "species": names["species"][usage_set["species"]]["name"],
        "item": names["items"][usage_set["item"]]["name"] if usage_set["item"] else "",
        "ability": names["abilities"][usage_set["ability"]]["name"],
        "nature": names["natures"][usage_set["nature"]]["name"],
        "level": LEVEL,
        "evs": usage_set["evs"],
        "ivs": {},
        "moves": [names["moves"][move]["name"] for move in usage_set["moves"]],
    }


def _without_empty_ivs(card_set: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in card_set.items() if key != "ivs" or value}


def _names_query(sets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "species": sorted({usage_set["species"] for usage_set in sets}),
        "items": sorted({usage_set["item"] for usage_set in sets if usage_set["item"]}),
        "abilities": sorted({usage_set["ability"] for usage_set in sets}),
        "natures": sorted({usage_set["nature"] for usage_set in sets}),
        "moves": sorted({move for usage_set in sets for move in usage_set["moves"]}),
    }


def _known(usage_set: dict[str, Any], names: dict[str, Any]) -> bool:
    """Whether Showdown's dex knows every name of the set and the set has four moves."""
    looked_up = [
        names["species"][usage_set["species"]],
        names["abilities"][usage_set["ability"]],
        names["natures"][usage_set["nature"]],
        *(names["moves"][move] for move in usage_set["moves"]),
    ]
    if usage_set["item"]:
        looked_up.append(names["items"][usage_set["item"]])
    return len(usage_set["moves"]) == MOVES_PER_CARD and all(entry["exists"] for entry in looked_up)


def choose_cards(stats: dict[str, Any], dex: DexLookup, count: int = CARD_COUNT) -> list[dict[str, Any]]:
    """The first `count` usage sets, in usage order, that Showdown accepts, one per base species (see the module note)."""
    candidates = [usage_set for usage_set in usage_sets(stats) if _follows(usage_set)]
    names = dex(_names_query(candidates))
    candidates = [usage_set for usage_set in candidates if _known(usage_set, names)]
    named = [_named_set(usage_set, names) for usage_set in candidates]
    # The validator gives a set without `ivs` perfect IVs, as the battle does for an empty `ivs`; handed the
    # empty object itself, it fails every legendary's "at least three perfect IVs" check.
    problems = dex({"format": FORMAT, "sets": [_without_empty_ivs(card_set) for card_set in named]})["problems"]
    cards: list[dict[str, Any]] = []
    taken_numbers: set[int] = set()
    for usage_set, card_set, set_problems in zip(candidates, named, problems):
        number = names["species"][usage_set["species"]]["num"]
        if set_problems or number in taken_numbers:
            continue
        taken_numbers.add(number)
        cards.append({"card_id": CARD_ID_PREFIX + card_slug(card_set["species"]), **card_set})
        if len(cards) == count:
            break
    return cards


def species_facts(cards: list[dict[str, Any]], dex: DexLookup) -> dict[str, Any]:
    """Types and base stats of every forme of each card's species, by species id."""
    card_species = dex({"format": FORMAT, "species": [card["species"] for card in cards]})["species"]
    formes = sorted({forme for entry in card_species.values() for forme in entry["formes"]})
    facts = dex({"format": FORMAT, "species": formes})["species"]
    return {
        entry["id"]: {
            "name": entry["name"],
            "types": [kind.upper() for kind in entry["types"]],
            "base_stats": entry["base_stats"],
        }
        for entry in sorted(facts.values(), key=lambda entry: entry["id"])
    }


def move_facts(cards: list[dict[str, Any]], dex: DexLookup) -> dict[str, Any]:
    """Each card move, Struggle and Recharge: type, category, power, accuracy as a fraction, priority and target."""
    names = sorted({move for card in cards for move in card["moves"]} | set(ALWAYS_LISTED_MOVES))
    looked_up = dex({"format": FORMAT, "moves": names})["moves"]
    facts = {
        entry["id"]: {
            "name": entry["name"],
            "type": entry["type"].upper(),
            "category": entry["category"].upper(),
            "base_power": entry["base_power"],
            # Showdown's `true` is a move that cannot miss.
            "accuracy": 1.0 if entry["accuracy"] is True else entry["accuracy"] / PERCENT,
            "priority": entry["priority"],
            "target": entry["target"],
        }
        for entry in looked_up.values()
    }
    facts["recharge"] = dict(RECHARGE_FACTS)
    return dict(sorted(facts.items()))


def build_catalog(stats: dict[str, Any], dex: DexLookup, count: int = CARD_COUNT) -> dict[str, Any]:
    """The whole catalog: where it comes from, the cards in usage order, the species facts and the move facts."""
    cards = choose_cards(stats, dex, count)
    info = stats["info"]
    return {
        "format": FORMAT,
        "source": {
            "statistics": f"Smogon usage statistics {info['metagame']}-{int(info['cutoff'])}",
            "battles": info["number of battles"],
            "rule": "each species' most used item, ability, spread and four moves, at level 50",
        },
        "cards": cards,
        "species": species_facts(cards, dex),
        "moves": move_facts(cards, dex),
    }


def node_dex(query: dict[str, Any]) -> dict[str, Any]:
    """Ask `tools/pokemon_sim/dex.js`; it needs Node and the installed pokemon-showdown package."""
    completed = subprocess.run(
        ["node", str(DEX_SCRIPT)],
        input=json.dumps(query),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{DEX_SCRIPT} failed (run `npm ci` in tools/pokemon_sim first): {completed.stderr}")
    return json.loads(completed.stdout)


def catalog_text(catalog: dict[str, Any]) -> str:
    """The catalog as written: indented JSON, names as they are, one newline at the end."""
    return json.dumps(catalog, indent=JSON_INDENT, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stats", type=Path, default=STATS_PATH, help="the Smogon usage statistics (JSON)")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="where the catalog is written")
    arguments = parser.parse_args(argv)
    stats = json.loads(arguments.stats.read_text(encoding="utf-8"))
    catalog = build_catalog(stats, node_dex)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(catalog_text(catalog), encoding="utf-8", newline="\n")
    print_line(f"wrote {len(catalog['cards'])} cards to {arguments.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
