"""What the agent knows of a Pokémon's set, and the damage one known set does to another.

A set is known exactly from its draft card (species, item, ability, nature,
EVs, IVs, level and four moves). Both drafted rosters are public, so the
opponent's sets are as exactly known as ours once their cards were seen in the
pool. When no card was seen (a card the opponent took before our first look
at the pool), the set is made from the facts an observation shows: species,
item, ability, the moves it lists and level, with a neutral nature and EVs
spread evenly. A set with no move known is assumed to attack with one
80-power move of each of its types, physical or special by its better
attacking stat: a typical strong same-type attack, so its threat is neither
ignored nor guessed high.

Names from the platform (display names or Showdown ids) are kept as Showdown
ids, the spelling team preview and switches use. A species the data does not
know gives no set at all.
"""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ucla_agent.games.pokemon import damage, data

# Move ids and the ability the policies treat as tactics of their own.
FAKE_OUT = "fakeout"
TAILWIND = "tailwind"
TRICK_ROOM = "trickroom"
REDIRECTION_MOVES = frozenset({"followme", "ragepowder"})
PROTECT_MOVES = frozenset(
    {"protect", "detect", "spikyshield", "kingsshield", "banefulbunker", "silktrap", "burningbulwark", "obstruct"}
)
INTIMIDATE = "intimidate"
# Showdown target types of a move that hits more than one Pokémon at once.
SPREAD_TARGETS = frozenset({"allAdjacentFoes", "allAdjacent"})

DEFAULT_LEVEL = damage.DEFAULT_LEVEL
MAX_LEVEL = 100
DEFAULT_IV = damage.DEFAULT_IV
NEUTRAL_NATURE = damage.NEUTRAL_NATURE
# 508 of the 510 EVs a set may hold, spread evenly over the six stats: no guess at where an unknown set invests.
ASSUMED_EVS_PER_STAT = 84
ASSUMED_EVS = tuple((stat, ASSUMED_EVS_PER_STAT) for stat in data.STATS)
ASSUMED_MOVE_POWER = 80
ASSUMED_MOVE_PREFIX = "assumed"
PERCENT_CAP = 100.0
HP = "hp"
SHEDINJA_BASE_HP = 1
_BEST_DAMAGE_CACHE = 8192


@dataclass(frozen=True)
class PokemonSet:
    """One Pokémon's set, hashable: Showdown ids for species, item (`""` for none) and ability (`""` when unknown),
    the nature's name, level, EVs and IVs as (stat, value) pairs, and the ids of its known moves."""

    species: str
    item: str
    ability: str
    nature: str
    level: int
    evs: tuple[tuple[str, int], ...]
    ivs: tuple[tuple[str, int], ...]
    moves: tuple[str, ...]

    @property
    def record(self) -> data.Species:
        """The species' data record; every set is made for a species the data knows."""
        return data.species(self.species)

    @property
    def types(self) -> tuple[str, ...]:
        return self.record.types

    def stat(self, name: str) -> int:
        """The stat (`hp`, `atk`, `def`, `spa`, `spd`, `spe`) at the set's level, before stages and items."""
        base = self.record.base_stats[name]
        core = 2 * base + dict(self.ivs).get(name, DEFAULT_IV) + dict(self.evs).get(name, 0) // 4
        if name == HP:
            return 1 if base == SHEDINJA_BASE_HP else core * self.level // 100 + self.level + 10
        return math.floor((core * self.level // 100 + 5) * data.nature(self.nature)[name])

    def max_hp(self) -> int:
        return self.stat(HP)

    def has_move(self, move_ids: Iterable[str]) -> bool:
        """Whether the set knows any of these moves."""
        return not set(self.moves).isdisjoint(move_ids)

    def move_records(self) -> tuple[data.Move, ...]:
        """The records of its known moves, or the assumed same-type attacks when none is known."""
        if self.moves:
            return tuple(data.move(move_id) for move_id in self.moves)
        physical = self.record.base_stats["atk"] >= self.record.base_stats["spa"]
        return tuple(assumed_move(move_type, physical) for move_type in self.types)

    def battle_mon(
        self, hp_fraction: float = 1.0, status: str | None = None, boosts: Mapping[str, int] | None = None
    ) -> damage.BattleMon:
        """The set as the damage estimate takes it, at a share of its maximum HP, with a status and stat stages."""
        return damage.BattleMon(
            species=self.species,
            level=self.level,
            item=self.item or None,
            ability=self.ability or None,
            nature=self.nature,
            evs=dict(self.evs),
            ivs=dict(self.ivs),
            current_hp=None if hp_fraction >= 1 else current_hp(self.max_hp(), hp_fraction),
            status=status,
            boosts=dict(boosts or {}),
        )


def current_hp(max_hp: int, hp_fraction: float) -> int:
    """HP from a share of the maximum: at least 1 while any share is left, 0 once none is."""
    return max(1, round(max_hp * hp_fraction)) if hp_fraction > 0 else 0


@functools.cache
def assumed_move(move_type: str, physical: bool) -> data.Move:
    """The attack an unknown set is assumed to have of one of its types (see the module note)."""
    category = damage.PHYSICAL if physical else damage.SPECIAL
    return data.Move(
        id=f"{ASSUMED_MOVE_PREFIX}{data.to_id(move_type)}",
        name=f"{move_type} attack",
        type=move_type,
        category=category,
        base_power=ASSUMED_MOVE_POWER,
        accuracy=True,
        priority=0,
        target="normal",
        flags=frozenset(),
        multihit=None,
        drain=None,
        recoil=None,
        stat_changes=(),
        has_secondary=False,
        will_crit=False,
    )


def from_card(entry: Any) -> PokemonSet | None:
    """The set of a draft card as `available_cards` lists it; None when it names no species the data knows."""
    if not isinstance(entry, Mapping):
        return None
    return from_facts(
        entry.get("species"),
        item=entry.get("item"),
        ability=entry.get("ability"),
        moves=entry.get("moves"),
        level=entry.get("level"),
        nature=entry.get("nature"),
        evs=entry.get("evs"),
        ivs=entry.get("ivs"),
    )


def from_facts(
    species: Any,
    *,
    item: Any = None,
    ability: Any = None,
    moves: Any = None,
    level: Any = None,
    nature: Any = None,
    evs: Any = None,
    ivs: Any = None,
) -> PokemonSet | None:
    """A set from whatever is known of it; a value that is missing or unusable takes the module note's assumption.

    None when `species` names no species the data knows.
    """
    if not isinstance(species, str) or data.species(species) is None:
        return None
    return PokemonSet(
        species=data.to_id(species),
        item=_id_or_none(item),
        ability=_id_or_none(ability),
        nature=_nature(nature),
        level=_level(level),
        evs=_stat_pairs(evs, ASSUMED_EVS),
        ivs=_stat_pairs(ivs, ()),
        moves=_known_moves(moves),
    )


def _id_or_none(name: Any) -> str:
    """A name as its Showdown id; `""` (none, or unknown) for anything that is not a name."""
    return data.to_id(name) if isinstance(name, str) else ""


def _nature(name: Any) -> str:
    return name if isinstance(name, str) and data.nature(name) is not None else NEUTRAL_NATURE


def _level(level: Any) -> int:
    return level if _whole(level) and 1 <= level <= MAX_LEVEL else DEFAULT_LEVEL


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _stat_pairs(values: Any, default: tuple[tuple[str, int], ...]) -> tuple[tuple[str, int], ...]:
    """A card's EVs or IVs as sorted (stat, value) pairs; `default` when it lists none as a mapping."""
    if not isinstance(values, Mapping):
        return default
    return tuple(sorted((stat, value) for stat, value in values.items() if stat in data.STATS and _whole(value)))


def _known_moves(moves: Any) -> tuple[str, ...]:
    """The ids of the listed moves the data knows, in order, each once."""
    if not isinstance(moves, (list, tuple)):
        return ()
    ids = (data.to_id(name) for name in moves if isinstance(name, str) and data.move(name) is not None)
    return tuple(dict.fromkeys(ids))


def type_effect(attack_type: str, defender: PokemonSet) -> float:
    """How effective a move of `attack_type` is on the defender: its types, and 0 when its ability makes it immune."""
    if damage.IMMUNITY_ABILITIES.get(defender.ability) == attack_type:
        return 0.0
    multiplier = data.type_multiplier(attack_type, defender.types)
    return 1.0 if multiplier is None else multiplier


def is_damaging(move: data.Move) -> bool:
    """Whether a move deals damage by its power (the damage estimate gives 0 for every other move)."""
    return move.category != damage.STATUS and move.base_power > 0


@functools.lru_cache(maxsize=_BEST_DAMAGE_CACHE)
def best_damage(attacker: PokemonSet, defender: PokemonSet) -> float:
    """The most damage any of the attacker's moves does to the defender at full HP, one target, no field: the
    highest `max_pct`, capped at 100."""
    attacking, defending = attacker.battle_mon(), defender.battle_mon()
    best = max(
        (damage.estimate(attacking, move, defending).max_pct for move in attacker.move_records() if is_damaging(move)),
        default=0.0,
    )
    return min(PERCENT_CAP, best)
