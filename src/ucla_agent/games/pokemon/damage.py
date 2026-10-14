"""Damage estimate: the range of damage one move does from one Pokémon to another, Gen 9 doubles.

`estimate(attacker, move, defender, field)` follows the game's damage formula
step by step, with its 4096-based modifiers and rounding, the way the Smogon
damage calculator does: stats from base stats, EVs, IVs and nature at the
Pokémon's level (50 by default); stat stages; the base power and its
modifiers; spread reduction; weather; critical hits; the 16 random rolls
(85-100%); same-type bonus; type effectiveness; burn; the final modifiers.
It is an estimate for scoring choices: Showdown resolves the real battle.

Modeled:

- STAB ×1.5, ×2.0 with Adaptability; type effectiveness from the type chart;
- spread moves ×0.75 when `Field.spread` says the move hits more than one target;
- Sun and Rain on Fire and Water moves (×1.5 boosted, ×0.5 weakened);
- Electric, Grassy and Psychic Terrain ×1.3 on moves of their type from a
  grounded user (not Flying, not Levitate);
- burn halving physical damage, except with Guts;
- Reflect and Light Screen ×2/3 (the doubles value), not on a critical hit;
- multi-hit moves: a fixed count, and 2-5 hits counted as 3;
- always-crit moves: ×1.5, ignoring the attacker's negative attack stages and
  the defender's positive defense stages;
- items: Choice Band, Choice Specs, Life Orb, Expert Belt, the type-boosting
  items (×1.2, e.g. Mystic Water, Charcoal), Assault Vest, Eviolite (assumed
  held by a Pokémon that can still evolve);
- abilities: Adaptability, Huge Power, Pure Power, Technician, Tough Claws,
  Sheer Force, Hustle (power only), Guts, Multiscale and Shadow Shield at full
  HP (first hit only), Thick Fat, Filter, Solid Rock, Prism Armor, Fluffy, Ice
  Scales, and the immunities of Levitate, Flash Fire, Well-Baked Body, Water
  Absorb, Storm Drain, Dry Skin, Volt Absorb, Lightning Rod, Motor Drive, Sap
  Sipper and Earth Eater;
- status moves and fixed-damage moves (base power 0) give 0.

Not modeled: random critical hits; moves whose power, type or stat depends on
the situation use their listed base power, type and the usual stats (Knock
Off's item bonus, Facade, Hex, Acrobatics, Weather Ball, Terrain Pulse, Rising
Voltage, Expanding Force, Eruption and Water Spout, Triple Axel's rising power,
Tera Blast, Body Press, Foul Play, Psyshock); weight-based moves (Low Kick,
Grass Knot, Heavy Slam, Heat Crash) and fixed-damage moves (Seismic Toss, Night
Shade, Super Fang, Ruination, Final Gambit, Endeavor) give 0; Terastallization
(`BattleMon.types` can stand in for a changed typing, STAB follows it); every
other ability, including Intimidate itself (pass its stage drop in
`BattleMon.boosts`), the Ruin abilities, Protosynthesis, Quark Drive, Mold
Breaker, Unaware, Tinted Lens, Sniper, Supreme Overlord, the -ate abilities,
Iron Fist, Strong Jaw, Sharpness, Reckless, Sand Force, Solar Power, Fur Coat,
Heatproof, Wonder Guard, Bulletproof, Soundproof and Friend Guard; every other
item, including resist berries, Gems, Punching Glove, Utility Umbrella and Air
Balloon; sand's Rock Sp. Def and snow's Ice Defense boosts, Harsh Sunshine and
Heavy Rain, Misty Terrain, Grassy Terrain's weaker Earthquake, Psychic
Terrain's priority block, Aurora Veil, Gravity, Helping Hand and Protect;
Parental Bond, Skill Link, and stat changes between the hits of a multi-hit
move. The KO chance of a multi-hit move counts the 16 rolls with every hit on
the same roll.

Pure Python with no I/O beyond the one data load of `data.shipped()`; one
estimate takes well under a millisecond.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from typing import Mapping

from ucla_agent.games.pokemon import data

DEFAULT_LEVEL = 50
DEFAULT_IV = 31
NEUTRAL_NATURE = "Serious"
PHYSICAL = "Physical"
SPECIAL = "Special"
STATUS = "Status"
ROLLS = range(85, 101)
ROLL_COUNT = len(ROLLS)
PERCENT = 100
MAX_STAGE = 6
# 2-5 hit moves are counted as hitting 3 times, the calculator's default.
VARIABLE_HITS_ASSUMED = 3
TECHNICIAN_MAX_POWER = 60
SHEDINJA_BASE_HP = 1
CRIT_MULTIPLIER = 1.5
HUSTLE_MULTIPLIER = 1.5
BURN_DIVISOR = 2

# The game's modifiers are fixed-point numbers over 4096 (12 bits).
FIXED_POINT_BITS = 12
ONE = 4096
HALF = 2048
TWO_THIRDS = 2732
THREE_QUARTERS = 3072
POINT_TWO = 4915
LIFE_ORB = 5324
POINT_THREE = 5325
ONE_AND_HALF = 6144
DOUBLE = 8192
# The bounds the game clamps each chain of modifiers to.
BASE_POWER_BOUNDS = (41, 2097152)
STAT_BOUNDS = (410, 131072)
FINAL_BOUNDS = (41, 131072)

SUN = frozenset({"sun", "sunnyday"})
RAIN = frozenset({"rain", "raindance"})
TERRAIN_TYPES = {
    "electric": "Electric",
    "electricterrain": "Electric",
    "grassy": "Grass",
    "grassyterrain": "Grass",
    "psychic": "Psychic",
    "psychicterrain": "Psychic",
}
TYPE_BOOSTING_ITEMS = {
    "blackbelt": "Fighting",
    "blackglasses": "Dark",
    "charcoal": "Fire",
    "dragonfang": "Dragon",
    "fairyfeather": "Fairy",
    "hardstone": "Rock",
    "magnet": "Electric",
    "metalcoat": "Steel",
    "miracleseed": "Grass",
    "mysticwater": "Water",
    "nevermeltice": "Ice",
    "poisonbarb": "Poison",
    "sharpbeak": "Flying",
    "silkscarf": "Normal",
    "silverpowder": "Bug",
    "softsand": "Ground",
    "spelltag": "Ghost",
    "twistedspoon": "Psychic",
}
IMMUNITY_ABILITIES = {
    "levitate": "Ground",
    "eartheater": "Ground",
    "flashfire": "Fire",
    "wellbakedbody": "Fire",
    "waterabsorb": "Water",
    "stormdrain": "Water",
    "dryskin": "Water",
    "voltabsorb": "Electric",
    "lightningrod": "Electric",
    "motordrive": "Electric",
    "sapsipper": "Grass",
}
DOUBLED_ATTACK_ABILITIES = frozenset({"hugepower", "purepower"})
FULL_HP_HALVING_ABILITIES = frozenset({"multiscale", "shadowshield"})
SUPER_EFFECTIVE_FILTERS = frozenset({"filter", "solidrock", "prismarmor"})
THICK_FAT_TYPES = frozenset({"Fire", "Ice"})
BURN = "brn"


@dataclass(frozen=True)
class BattleMon:
    """A Pokémon as the estimate sees it: its set, its current HP and status, and its stat stages.

    Missing EVs are 0 and missing IVs 31; `current_hp` None means full HP;
    `types` None means the species' own types; `status` is a Showdown status
    id ("brn", "par", ...); `boosts` maps a stat ("atk", "def", "spa", "spd")
    to its stage, -6 to 6.
    """

    species: str
    level: int = DEFAULT_LEVEL
    item: str | None = None
    ability: str | None = None
    nature: str = NEUTRAL_NATURE
    evs: Mapping[str, int] = dataclasses.field(default_factory=dict)
    ivs: Mapping[str, int] = dataclasses.field(default_factory=dict)
    current_hp: int | None = None
    status: str | None = None
    boosts: Mapping[str, int] = dataclasses.field(default_factory=dict)
    types: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Field:
    """The battle conditions of one hit: weather, terrain, whether it is a spread hit, the defender's screens."""

    weather: str | None = None
    terrain: str | None = None
    spread: bool = False
    reflect: bool = False
    light_screen: bool = False


@dataclass(frozen=True)
class DamageRange:
    """The damage over the 16 rolls, in HP and in percent of the defender's max HP (above 100 when it overkills).

    `ko_chance` is the share of rolls that reach the defender's current HP;
    `hits` is the number of hits counted, 0 for a move that deals no damage
    by its power.
    """

    min_damage: int
    max_damage: int
    min_pct: float
    max_pct: float
    ko_chance: float
    hits: int


@dataclass(frozen=True)
class _Side:
    """One Pokémon resolved against the data: its record, typing, nature and normalized item and ability ids."""

    mon: BattleMon
    species: data.Species
    types: tuple[str, ...]
    nature: Mapping[str, float]
    item: str
    ability: str


def _resolve(mon: BattleMon) -> _Side:
    species = data.species(mon.species)
    if species is None:
        raise ValueError(f"unknown species {mon.species!r}")
    nature = data.nature(mon.nature)
    if nature is None:
        raise ValueError(f"unknown nature {mon.nature!r}")
    types = mon.types if mon.types is not None else species.types
    return _Side(mon, species, types, nature, data.to_id(mon.item or ""), data.to_id(mon.ability or ""))


def _resolve_move(move: str | data.Move) -> data.Move:
    if isinstance(move, data.Move):
        return move
    found = data.move(move)
    if found is None:
        raise ValueError(f"unknown move {move!r}")
    return found


def poke_round(value: float) -> int:
    """The game's rounding: half rounds down, anything above half rounds up."""
    return math.ceil(value) if value % 1 > 0.5 else math.floor(value)


def chain(modifiers: list[int], bounds: tuple[int, int]) -> int:
    """Several 4096-based modifiers combined into one, each step rounded half up, then clamped to `bounds`."""
    combined = ONE
    for modifier in modifiers:
        combined = (combined * modifier + HALF) >> FIXED_POINT_BITS
    return max(bounds[0], min(combined, bounds[1]))


def _apply(value: int, modifiers: list[int], bounds: tuple[int, int]) -> int:
    return max(1, poke_round(value * chain(modifiers, bounds) / ONE))


def _max_hp(side: _Side) -> int:
    """The HP stat at the Pokémon's level."""
    base = side.species.base_stats["hp"]
    if base == SHEDINJA_BASE_HP:
        return 1
    mon = side.mon
    core = 2 * base + mon.ivs.get("hp", DEFAULT_IV) + mon.evs.get("hp", 0) // 4
    return core * mon.level // 100 + mon.level + 10


def _stat(side: _Side, stat: str) -> int:
    mon = side.mon
    core = 2 * side.species.base_stats[stat] + mon.ivs.get(stat, DEFAULT_IV) + mon.evs.get(stat, 0) // 4
    return math.floor((core * mon.level // 100 + 5) * side.nature[stat])


def _staged(stat: int, stage: int) -> int:
    """A stat at stat stage n: ×(2+n)/2 when raised, ×2/(2-n) when lowered."""
    stage = max(-MAX_STAGE, min(MAX_STAGE, stage))
    return stat * max(2, 2 + stage) // max(2, 2 - stage)


def _attack_modifiers(attacker: _Side, defender: _Side, move: data.Move) -> list[int]:
    physical = move.category == PHYSICAL
    modifiers = []
    if physical and attacker.ability in DOUBLED_ATTACK_ABILITIES:
        modifiers.append(DOUBLE)
    if physical and attacker.ability == "guts" and attacker.mon.status:
        modifiers.append(ONE_AND_HALF)
    if defender.ability == "thickfat" and move.type in THICK_FAT_TYPES:
        modifiers.append(HALF)
    if attacker.item == ("choiceband" if physical else "choicespecs"):
        modifiers.append(ONE_AND_HALF)
    return modifiers


def _attack(attacker: _Side, defender: _Side, move: data.Move, crit: bool) -> int:
    """The attacking stat after stages, Hustle and the attack modifiers; a crit ignores a lowered stage."""
    physical = move.category == PHYSICAL
    stat = "atk" if physical else "spa"
    stage = attacker.mon.boosts.get(stat, 0)
    attack = _staged(_stat(attacker, stat), 0 if crit and stage < 0 else stage)
    if physical and attacker.ability == "hustle":
        # Hustle multiplies the stat itself, before and apart from the chained attack modifiers.
        attack = poke_round(attack * HUSTLE_MULTIPLIER)
    return _apply(attack, _attack_modifiers(attacker, defender, move), STAT_BOUNDS)


def _defense(defender: _Side, move: data.Move, crit: bool) -> int:
    physical = move.category == PHYSICAL
    stat = "def" if physical else "spd"
    stage = defender.mon.boosts.get(stat, 0)
    defense = _staged(_stat(defender, stat), 0 if crit and stage > 0 else stage)
    modifiers = []
    if defender.item == "eviolite" or (defender.item == "assaultvest" and not physical):
        modifiers.append(ONE_AND_HALF)
    return _apply(defense, modifiers, STAT_BOUNDS)


def _grounded(side: _Side) -> bool:
    return "Flying" not in side.types and side.ability != "levitate"


def _terrain_boosts(attacker: _Side, move: data.Move, field: Field) -> bool:
    terrain_type = TERRAIN_TYPES.get(data.to_id(field.terrain or ""))
    return terrain_type == move.type and _grounded(attacker)


def _ability_power_modifiers(attacker: _Side, move: data.Move) -> list[int]:
    modifiers = []
    if attacker.ability == "technician" and move.base_power <= TECHNICIAN_MAX_POWER:
        modifiers.append(ONE_AND_HALF)
    sheer_force = attacker.ability == "sheerforce" and move.has_secondary
    if sheer_force or (attacker.ability == "toughclaws" and "contact" in move.flags):
        modifiers.append(POINT_THREE)
    return modifiers


def _base_power(attacker: _Side, move: data.Move, field: Field) -> int:
    """The move's power after terrain, the attacker's ability and a type-boosting item, in the game's order."""
    modifiers = [POINT_THREE] if _terrain_boosts(attacker, move, field) else []
    modifiers += _ability_power_modifiers(attacker, move)
    if TYPE_BOOSTING_ITEMS.get(attacker.item) == move.type:
        modifiers.append(POINT_TWO)
    return _apply(move.base_power, modifiers, BASE_POWER_BOUNDS)


def _weather_modifier(move_type: str, weather: str | None) -> int:
    weather_id = data.to_id(weather or "")
    boosted = (weather_id in SUN and move_type == "Fire") or (weather_id in RAIN and move_type == "Water")
    weakened = (weather_id in SUN and move_type == "Water") or (weather_id in RAIN and move_type == "Fire")
    return ONE_AND_HALF if boosted else HALF if weakened else ONE


def _base_damage(level: int, power: int, attack: int, defense: int, move: data.Move, field: Field) -> int:
    """The damage before the random roll: the game's formula, then spread, weather and a critical hit, in order."""
    damage = (2 * level // 5 + 2) * power * attack // defense // 50 + 2
    if field.spread:
        damage = poke_round(damage * THREE_QUARTERS / ONE)
    weather = _weather_modifier(move.type, field.weather)
    if weather != ONE:
        damage = poke_round(damage * weather / ONE)
    if move.will_crit:
        damage = math.floor(damage * CRIT_MULTIPLIER)
    return damage


def _stab(attacker: _Side, move: data.Move) -> int:
    if move.type not in attacker.types:
        return ONE
    return DOUBLE if attacker.ability == "adaptability" else ONE_AND_HALF


def _screen_modifier(move: data.Move, field: Field) -> list[int]:
    screened = (move.category == PHYSICAL and field.reflect) or (move.category == SPECIAL and field.light_screen)
    return [TWO_THIRDS] if screened and not move.will_crit else []


def _full_hp_halves(defender: _Side, first_hit: bool) -> bool:
    """Multiscale and Shadow Shield halve the first hit taken at full HP."""
    if defender.ability not in FULL_HP_HALVING_ABILITIES or not first_hit:
        return False
    return defender.mon.current_hp is None or defender.mon.current_hp >= _max_hp(defender)


def _kind_halves(defender: _Side, move: data.Move) -> bool:
    """Fluffy halves contact moves and Ice Scales special ones."""
    fluffy_contact = defender.ability == "fluffy" and "contact" in move.flags
    return fluffy_contact or (defender.ability == "icescales" and move.category == SPECIAL)


def _defender_modifiers(defender: _Side, move: data.Move, effectiveness: float, first_hit: bool) -> list[int]:
    applies = (
        (HALF, _full_hp_halves(defender, first_hit)),
        (HALF, _kind_halves(defender, move)),
        (THREE_QUARTERS, defender.ability in SUPER_EFFECTIVE_FILTERS and effectiveness > 1),
        (DOUBLE, defender.ability == "fluffy" and move.type == "Fire"),
    )
    return [modifier for modifier, applied in applies if applied]


def _item_modifier(attacker: _Side, effectiveness: float) -> list[int]:
    if attacker.item == "expertbelt" and effectiveness > 1:
        return [POINT_TWO]
    return [LIFE_ORB] if attacker.item == "lifeorb" else []


@dataclass(frozen=True)
class _Hit:
    """What every hit of one use of a move shares: its damage before the rolls and the modifiers applied per roll."""

    base: int
    stab: int
    effectiveness: float
    burned: bool


def _rolls(hit: _Hit, final: int) -> list[int]:
    """The damage of one hit at each of the 16 random rolls, `final` being the hit's chained final modifier."""
    damages = []
    for roll in ROLLS:
        damage = hit.base * roll // 100
        rounded = poke_round(damage * hit.stab / ONE) if hit.stab != ONE else damage
        damage = math.floor(rounded * hit.effectiveness)
        if hit.burned:
            damage //= BURN_DIVISOR
        damages.append(poke_round(max(1, damage * final / ONE)))
    return damages


def _final_modifier(attacker: _Side, defender: _Side, move: data.Move, hit: _Hit, field: Field, first: bool) -> int:
    """The chained final modifiers of one hit, in the game's order: screens, the defender's ability, the item."""
    modifiers = [
        *_screen_modifier(move, field),
        *_defender_modifiers(defender, move, hit.effectiveness, first),
        *_item_modifier(attacker, hit.effectiveness),
    ]
    return chain(modifiers, FINAL_BOUNDS)


def _total_rolls(attacker: _Side, defender: _Side, move: data.Move, hit: _Hit, field: Field, hits: int) -> list[int]:
    """The damage of all hits at each roll; only the first hit of a multi-hit move meets Multiscale."""
    first = _rolls(hit, _final_modifier(attacker, defender, move, hit, field, first=True))
    if hits == 1:
        return first
    later = _rolls(hit, _final_modifier(attacker, defender, move, hit, field, first=False))
    return [first_hit + (hits - 1) * later_hit for first_hit, later_hit in zip(first, later)]


def _hit_count(move: data.Move) -> int:
    if move.multihit is None:
        return 1
    return move.multihit if isinstance(move.multihit, int) else VARIABLE_HITS_ASSUMED


def _effectiveness(move: data.Move, defender: _Side) -> float:
    """The type multiplier, 0 when an ability makes the defender immune to the move's type."""
    if IMMUNITY_ABILITIES.get(defender.ability) == move.type:
        return 0.0
    multiplier = data.type_multiplier(move.type, defender.types)
    return 1.0 if multiplier is None else multiplier


def _burned(attacker: _Side, move: data.Move) -> bool:
    return attacker.mon.status == BURN and move.category == PHYSICAL and attacker.ability != "guts"


def _no_damage(hits: int) -> DamageRange:
    return DamageRange(0, 0, 0.0, 0.0, 0.0, hits)


def estimate(attacker: BattleMon, move: str | data.Move, defender: BattleMon, field: Field = Field()) -> DamageRange:
    """The damage `move` (a name or a record) does from `attacker` to `defender` under `field`.

    Raises ValueError for an unknown species, nature or move name.
    """
    attacking, defending, used = _resolve(attacker), _resolve(defender), _resolve_move(move)
    if used.category == STATUS or used.base_power == 0:
        return _no_damage(0)
    hits = _hit_count(used)
    effectiveness = _effectiveness(used, defending)
    if effectiveness == 0:
        return _no_damage(hits)
    power = _base_power(attacking, used, field)
    attack = _attack(attacking, defending, used, used.will_crit)
    defense = _defense(defending, used, used.will_crit)
    base = _base_damage(attacker.level, power, attack, defense, used, field)
    hit = _Hit(base, _stab(attacking, used), effectiveness, _burned(attacking, used))
    totals = _total_rolls(attacking, defending, used, hit, field, hits)
    defender_hp = _max_hp(defending)
    current_hp = defender_hp if defender.current_hp is None else defender.current_hp
    return DamageRange(
        min_damage=totals[0],
        max_damage=totals[-1],
        min_pct=PERCENT * totals[0] / defender_hp,
        max_pct=PERCENT * totals[-1] / defender_hp,
        ko_chance=sum(total >= current_hp for total in totals) / ROLL_COUNT,
        hits=hits,
    )
