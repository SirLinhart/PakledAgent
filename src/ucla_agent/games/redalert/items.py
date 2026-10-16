"""What is known about each Red Alert building and unit type.

The platform sends these facts with every match, as the `rules` of its game
reference object, and they are the authority. The tracked file
`config/redalert/items.toml` holds the same facts as the agent last knew
them, for a match that arrives without the reference object or with a value
missing from it. An `ItemTable` is the file's facts with every value the
match gave put over them, so the rest of the agent reads one table and never
asks where a figure came from.

The agent's item table also carries the damage table: how hard each item
hits each armour class, how far, and the armour it wears, as the tracked
file `config/redalert/damage.toml` holds them. Aircraft are apart: only an
anti-air weapon hits one, so an item's damage to an aircraft is that of its
anti-air weapon, at that weapon's range, whatever armour the aircraft wears.
The platform's rules give no
damage figures, so only one value of that table can come from a match: the
range of an item's weapon, `weapon.range` in its rules entry. The rules name
one weapon for each item, and for an item with a separate anti-air weapon it
may be that one: the rocket soldier's is its anti-air rocket, which reaches
7.5 cells where its ground rocket reaches 5. A rules range equal to the
file's anti-air range and not to its ground range is therefore the anti-air
weapon's and replaces the anti-air range alone; any other replaces the
file's ground range. The item table reads a unit's own range from the rules
by the same rule, so the rocket soldier reaches ground targets at 5 cells in
both tables. A table read from `items.toml` alone, as the tools that
compare item facts read it, has an empty damage table.

Reading the files is the only I/O here and happens in the `from_file` and
`from_tracked_files` methods; putting a match's values over a table reads
nothing, so it can be done while a move is being decided.

The game also fields variants of a type under names of their own: the
paratroopers a support power drops are `e1r1` and `e3r1`, rifles and rockets
like any other, and a mod names its own versions of the stock types with a
dot and a suffix, as the FransBots field the harvester `harv.frans` and the
engineer `e6.frans`. A name the table does not list but that is a listed name
followed by such a variant suffix, or by a dot and any suffix, is therefore
looked up as that listed type, so that a variant is worth, fights and reaches
as its base type does. Only those suffixes make a variant: a name that merely
begins like a listed one (the cruiser `ca` and `camera`) is not one, nor is a
dotted name whose part before the dot is not listed (the camera of a spy
plane, `camera.spyplane`). `ItemTable.base_name` gives the listed name a
dotted variant stands for, which the world reads actors under so that every
rule that compares type names takes `harv.frans` for the harvester it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, TypeVar

from ucla_agent.core.config_tables import Table, read_tables, reject_unknown_keys

ITEMS_FILE = Path("config") / "redalert" / "items.toml"
DAMAGE_FILE = Path("config") / "redalert" / "damage.toml"
# The armour classes a weapon's damage is given against, as the damage file names them.
ARMOUR_CLASSES = ("none", "light", "heavy", "wood", "concrete")
NO_ARMOUR = "none"
BUILDINGS_SECTION = "buildings"
UNITS_SECTION = "units"
TICKS_PER_SECOND = 25
BUILD_SECONDS_PER_CREDIT = 0.024

ALLIES = "allies"
SOVIET = "soviet"
FACTION_FAMILIES = {"england": ALLIES, "france": ALLIES, "germany": ALLIES, "russia": SOVIET, "ukraine": SOVIET}
# The barracks only the Allied family can build: seeing it on offer tells the families apart.
ALLIED_BARRACKS = "tent"
# Unit types that do not fight, whatever else they can do.
NON_COMBAT_TYPES = frozenset({"mcv", "harv", "e6", "medi", "mech", "thf", "spy"})
SHIP_QUEUE = "Ship"
# The name of a variant: the name of its type and a variant suffix, as `e1` and `r1` make the paratrooper `e1r1`.
VARIANT_NAME = re.compile(r"(?P<base>.+?)r\d+")
# The name of a mod's variant: the name of its type, a dot and any suffix, as the FransBots' harvester `harv.frans`.
DOTTED_VARIANT_NAME = re.compile(r"(?P<base>[^.]+)\..+")
# The roles of the item table whose weapons hit ground units, and the role of a weapon that hits aircraft.
GROUND_WEAPON_ROLES = frozenset({"anti_infantry", "anti_armor"})
ANTI_AIR_ROLE = "anti_air"

# The engine counts a position in 1/1024 of a cell and a speed in those per tick.
_ENGINE_UNITS_PER_CELL = 1024
# No unit crosses this many cells in a second (the fastest aircraft stays under 6), so a larger figure is
# the engine's own speed and not cells per second.
_MAX_CELLS_PER_SECOND = 10.0
_SPEED_DECIMALS = 2
# (section of a rules object, whether its items are buildings)
_SECTIONS = ((BUILDINGS_SECTION, True), (UNITS_SECTION, False))


@dataclass(frozen=True)
class Item:
    """The facts about one building or unit type. `name` is lowercase."""

    name: str
    is_building: bool
    cost: int = 0
    # Positive provides power, negative drains it.
    power: int = 0
    # The production queue that makes the item; None when it cannot be produced.
    queue: str | None = None
    # As the source names them. The fallback file names the buildings needed besides the queue's own producer.
    prerequisites: tuple[str, ...] = ()
    # The factions that can produce the item; empty when every faction can.
    factions: tuple[str, ...] = ()
    hp: int | None = None
    # Cells per second on clear ground.
    speed: float | None = None
    # Weapon range in cells.
    range: float | None = None
    # How far the unit sees, in cells; None when the source does not say.
    sight: float | None = None
    roles: frozenset[str] = frozenset()
    # The build time the source states; None when it leaves the time to follow from the cost.
    stated_build_time_s: float | None = None

    @property
    def build_time_s(self) -> float:
        """Seconds to build at full power: what the source states, else the usual time for the cost."""
        if self.stated_build_time_s is not None:
            return self.stated_build_time_s
        return self.cost * BUILD_SECONDS_PER_CREDIT

    def buildable_by(self, faction: str) -> bool:
        """Whether a player of `faction` can ever produce the item, prerequisites aside."""
        return self.queue is not None and (not self.factions or faction.lower() in self.factions)


@dataclass(frozen=True)
class Damage:
    """How hard one type hits each armour class and aircraft, how far, the armour it wears and whether it flies.

    The default does no damage, wears armour none and does not fly.
    """

    armour: str = NO_ARMOUR
    # Hit points a second against each armour class, in the engine's hit points; an armour class left out takes none.
    dps: Mapping[str, float] = field(default_factory=dict)
    # Weapon range and the distance inside which the weapon cannot fire, in cells.
    range: float = 0.0
    min_range: float = 0.0
    # Whether the type is an aircraft, which only an anti-air weapon can hit.
    air: bool = False
    # Hit points a second the type's anti-air weapon does to an aircraft, and that weapon's range in cells; 0 without.
    dps_air: float = 0.0
    air_range: float = 0.0

    def dps_against(self, armour: str) -> float:
        """Hit points a second against an armour class; 0 against one the type cannot hurt."""
        return self.dps.get(armour, 0.0)

    def dps_to(self, target: Damage) -> float:
        """Hit points a second against a type of these facts: the anti-air dps against an aircraft, else by armour."""
        return self.dps_air if target.air else self.dps_against(target.armour)

    def range_to(self, target: Damage) -> float:
        """The range of the weapon that hits a type of these facts: the anti-air range against an aircraft."""
        return self.air_range if target.air else self.range

    @property
    def hits_ground(self) -> bool:
        """Whether one of the type's weapons hurts some armour class on the ground."""
        return any(dps > 0 for dps in self.dps.values())


# What the damage table gives for a type it lists neither as itself nor as a variant: no damage, armour none.
NO_DAMAGE = Damage()


class DamageTable:
    """Damage facts by type name. Names are matched without regard to case, variants as their base type."""

    def __init__(self, damage: Mapping[str, Damage]) -> None:
        self._damage = {name.lower(): facts for name, facts in damage.items()}
        # Name the table does not list -> the facts of the type it is a variant of; filled as names are asked.
        self._variants: dict[str, Damage] = {}

    @classmethod
    def from_file(cls, root: Path) -> DamageTable:
        """The table held by `config/redalert/damage.toml` under `root`.

        The file is read strictly: when it is missing, or an item has a key
        that is unknown, missing or of the wrong kind, an armour class that
        is not one of the five, or dps that leave one out, this raises
        `ConfigError` naming the file, the item and the key.
        """
        path = root / DAMAGE_FILE
        tables = read_tables(path)
        return cls({name: _damage_from_file(Table(path, name, values)) for name, values in tables.items()})

    def with_game_config(self, game_config: Any) -> DamageTable:
        """A table in which the weapon range a game's reference object gives in its `rules` replaces a range.

        Only `weapon.range` of an entry is read: the rules hold no damage
        figures. It replaces the anti-air range when it is that weapon's and
        the ground range otherwise; see `_with_weapon_range`. A missing
        reference object, or a range that is not a number, changes nothing.
        No file is read.
        """
        rules = _mapping(_mapping(game_config).get("rules"))
        damage = dict(self._damage)
        for section, _ in _SECTIONS:
            for name, entry in _mapping(rules.get(section)).items():
                weapon_range = _number(_mapping(_mapping(entry).get("weapon")).get("range"))
                if weapon_range is not None:
                    key = str(name).lower()
                    damage[key] = _with_weapon_range(damage.get(key, NO_DAMAGE), weapon_range)
        return DamageTable(damage)

    def ground_range(self, name: str, weapon_range: float) -> float:
        """The ground range a type has when a game's rules give its weapon `weapon_range`, by the rule
        `with_game_config` follows: this table's ground range when that is the anti-air weapon's, else `weapon_range`.
        """
        return _with_weapon_range(self._damage.get(name.lower(), NO_DAMAGE), weapon_range).range

    def get(self, name: str) -> Damage:
        """The facts about a type, or of the type it is a variant of; `NO_DAMAGE` when the table knows neither."""
        key = name.lower()
        known = self._damage.get(key)
        if known is not None:
            return known
        if key not in self._variants:
            self._variants[key] = _variant_of(key, self._damage) or NO_DAMAGE
        return self._variants[key]

    def __len__(self) -> int:
        return len(self._damage)


class ItemTable:
    """Item facts by type name, and the damage table. Names are matched without regard to case."""

    def __init__(self, items: Iterable[Item], damage: DamageTable | None = None) -> None:
        """A table of `items`; one made without a damage table has an empty one, by which nothing does damage."""
        self._items = {item.name: item for item in items}
        self.damage = DamageTable({}) if damage is None else damage
        # Name the table does not list -> the facts of the type it is a variant of, or None; filled as names are asked.
        self._variants: dict[str, Item | None] = {}

    @classmethod
    def from_file(cls, root: Path) -> ItemTable:
        """The table held by `config/redalert/items.toml` under `root`, with an empty damage table.

        The file is read strictly: when it is missing, or an item has a key
        that is unknown, missing or of the wrong kind, this raises
        `ConfigError` naming the file, the item and the key.
        """
        path = root / ITEMS_FILE
        tables = read_tables(path)
        reject_unknown_keys(path, "the file", tables, {section for section, _ in _SECTIONS})
        items = []
        for section, is_building in _SECTIONS:
            section_table = Table(path, section, tables.get(section))
            items += [_item_from_file(section_table.subtable(name), name, is_building) for name in section_table.keys()]
        return cls(items)

    @classmethod
    def from_tracked_files(cls, root: Path) -> ItemTable:
        """The agent's table: `items.toml` under `root`, carrying the damage table of `damage.toml` under it.

        Both files are read strictly, `items.toml` first; see `from_file` and `DamageTable.from_file`.
        """
        return cls(cls.from_file(root), DamageTable.from_file(root))

    def with_game_config(self, game_config: Any) -> ItemTable:
        """A table in which every value the game's reference object gives in its `rules` replaces this table's.

        Items the rules add are added. A reference object that is missing, or
        a value of a kind that cannot be the fact it is named after, changes
        nothing. The damage table takes the weapon ranges the rules give. No
        file is read.

        A unit's range is read by the damage table's rule (see
        `DamageTable.ground_range`), so that a rules range that is the anti-air
        weapon's leaves the unit the ground range the damage table keeps and
        the two tables agree. A building's range is the rules value as given:
        it is the distance scouts and raids keep from a defense, and an anti-air
        defense keeps the reach of its one weapon.
        """
        rules = _mapping(_mapping(game_config).get("rules"))
        items = dict(self._items)
        for section, is_building in _SECTIONS:
            for name, entry in _mapping(rules.get(section)).items():
                key = str(name).lower()
                known = items.get(key) or Item(name=key, is_building=is_building)
                facts = _stated_facts(_mapping(entry))
                if "range" in facts and not is_building:
                    facts["range"] = self.damage.ground_range(key, facts["range"])
                items[key] = replace(known, is_building=is_building, **facts)
        return ItemTable(items.values(), self.damage.with_game_config(game_config))

    def get(self, name: str) -> Item | None:
        """The facts about a type, or of the type it is a variant of; None when the table knows neither."""
        key = name.lower()
        known = self._items.get(key)
        if known is not None:
            return known
        if key not in self._variants:
            self._variants[key] = _variant_of(key, self._items)
        return self._variants[key]

    def base_name(self, name: str) -> str:
        """The name, in lower case, that a type is read under: the listed type a dotted variant the table does not
        list stands for (`harv.frans` -> `harv`), else the name itself, `e1r1` and an unknown name included."""
        key = name.lower()
        if key in self._items or "." not in key:
            return key
        dotted = DOTTED_VARIANT_NAME.fullmatch(key)
        return dotted.group("base") if dotted is not None and dotted.group("base") in self._items else key

    def __getitem__(self, name: str) -> Item:
        return self._items[name.lower()]

    def __iter__(self) -> Iterator[Item]:
        return iter(self._items.values())

    def __len__(self) -> int:
        return len(self._items)


_Facts = TypeVar("_Facts")


def is_air_only(name: str, items: ItemTable) -> bool:
    """Whether a type fires at aircraft alone: its item's roles include anti-air and neither ground role (the AA gun
    and the SAM site). A type the item table does not know is taken to fire at the ground."""
    facts = items.get(name)
    return facts is not None and ANTI_AIR_ROLE in facts.roles and not facts.roles & GROUND_WEAPON_ROLES


def _variant_of(name: str, listed: Mapping[str, _Facts]) -> _Facts | None:
    """The facts of the listed type that `name` is that type's name and a variant suffix after (`e1r1`), or that
    type's name, a dot and any suffix after (`harv.frans`); None for none."""
    for pattern in (VARIANT_NAME, DOTTED_VARIANT_NAME):
        variant = pattern.fullmatch(name)
        facts = None if variant is None else listed.get(variant.group("base"))
        if facts is not None:
            return facts
    return None


def _with_weapon_range(facts: Damage, weapon_range: float) -> Damage:
    """The facts of a type with the range its rules entry gives its weapon put in the place of the weapon's range.

    The rules name one weapon for a type that has two. A range equal to the
    type's anti-air range and not to its ground range is the anti-air
    weapon's, as the rocket soldier's 7.5 is, and the anti-air range already
    holds it: taken for the ground range it would have the type reach ground
    targets its ground weapon cannot, and give a type with an anti-air weapon
    alone a ground range. Any other range sets the ground range.
    """
    is_anti_air_range = facts.air_range > 0 and weapon_range == facts.air_range != facts.range
    return facts if is_anti_air_range else replace(facts, range=weapon_range)


def _damage_from_file(table: Table) -> Damage:
    """One item of the damage file: its armour and whether it flies, for an item that fights on the ground its range,
    minimum range and dps, and for one with an anti-air weapon that weapon's dps and range."""
    armour = table.one_of("armour", ARMOUR_CLASSES)
    air = table.boolean("air", False)
    anti_air: dict[str, float] = {}
    if "dps_air" in table.keys():
        anti_air = {"dps_air": float(table.number("dps_air")), "air_range": float(table.number("air_range"))}
    if "dps" not in table.keys():
        table.reject_unread_keys()
        return Damage(armour, air=air, **anti_air)
    dps_table = table.subtable("dps")
    dps = {armour_class: float(dps_table.number(armour_class)) for armour_class in ARMOUR_CLASSES}
    dps_table.reject_unread_keys()
    damage = Damage(armour, dps, float(table.number("range")), float(table.number("min_range")), air, **anti_air)
    table.reject_unread_keys()
    return damage


def _item_from_file(table: Table, name: str, is_building: bool) -> Item:
    """One item of the fallback file, every key of its table accounted for."""
    item = Item(
        name=name.lower(),
        is_building=is_building,
        cost=table.integer("cost", minimum=0),
        power=table.integer("power", 0),
        queue=table.text("queue", None),
        prerequisites=_lowercase(table.texts("prerequisites")),
        factions=_lowercase(table.texts("factions", ())),
        hp=table.integer("hp", minimum=1),
        speed=table.number("speed", None),
        range=table.number("range", None),
        sight=table.number("sight", None),
        roles=frozenset(table.texts("roles")),
        stated_build_time_s=table.number("build_time_s", None),
    )
    table.reject_unread_keys()
    return item


def _stated_facts(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The facts a rules object's entry for one item states, as `Item` fields; what it leaves out is left out."""
    facts = {field: value for field, key, read in _RULE_FACTS if (value := read(entry.get(key))) is not None}
    if "range" not in facts:
        weapon_range = _number(_mapping(entry.get("weapon")).get("range"))
        if weapon_range is not None:
            facts["range"] = weapon_range
    return facts


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _whole_number(value: Any) -> int | None:
    # bool is a kind of int in Python, and `true` is not a cost.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: Any) -> float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _names(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)) or not all(isinstance(name, str) for name in value):
        return None
    return _lowercase(value)


def _lowercase(names: Iterable[str]) -> tuple[str, ...]:
    return tuple(name.lower() for name in names)


def _cells_per_second(value: Any) -> float | None:
    """A speed from a rules object in cells per second, whichever of the two units it was given in."""
    speed = _number(value)
    if speed is None:
        return None
    if speed > _MAX_CELLS_PER_SECOND:
        speed = speed * TICKS_PER_SECOND / _ENGINE_UNITS_PER_CELL
    return round(speed, _SPEED_DECIMALS)


# (the `Item` field, its key in a rules object's entry, how a value of the right kind is read from it)
_RULE_FACTS: tuple[tuple[str, str, Callable[[Any], Any]], ...] = (
    ("cost", "cost", _whole_number),
    ("power", "power", _whole_number),
    ("queue", "queue", _text),
    ("prerequisites", "prerequisites", _names),
    ("factions", "factions", _names),
    ("hp", "hp", _whole_number),
    ("speed", "speed", _cells_per_second),
    ("range", "range", _number),
    ("sight", "sight", _number),
    ("stated_build_time_s", "build_time_s", _number),
)
