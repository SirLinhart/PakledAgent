"""The agent's picture of one Red Alert match, brought up to date from every state it receives.

A `World` is created empty when a match starts and `update` is called once
for each state, in the order the states arrive. Everything else the agent
decides from reads the world, not the raw state: the world has the names in
lower case, the wrecks left out, the queues sorted, and the figures that have
to be worked out (the family of the faction, the enemy's start cell) worked
out once. A type that is a mod's dotted variant of a listed one is read
under the listed name (`ItemTable.base_name`): the FransBots' `harv.frans` is
a harvester to every rule, `harv`.

The world also remembers what the states no longer show. An enemy that goes
out of sight stays in `enemy_memory` as it was last seen: out of sight is not
destroyed. An enemy is forgotten only on evidence that it is gone; a unit
without such evidence, which may have walked anywhere, is marked stale after
half a minute and kept.

Enemies leave sight all the time without being gone: the unit that saw them
walks on or dies. Evidence must therefore be more than an enemy out of
sight. The platform reports a kill with a count and nothing about what was
killed, so a kill is taken as the end of the enemies, units and buildings
alike, that left sight with it only when the kills reported account for
every enemy that left sight in that state (`killed_in_sight`); when the unit
that saw them dies, more leave sight than were killed, and none is taken as
killed. A unit killed in sight is forgotten at once, so that no rule counts
it as a live enemy for the half minute before it would go stale. A building
has one more kind of evidence, since it does not walk away: an own unit near
its cell does not see it at once, so the building must stay absent while an
own unit stands near it for a moment.

An update reads the state object it is given and nothing else: no file, no
network, no model. Everything a world knows is on the instance, so a new
match gets a new world and nothing of the last match is left.

A type of actor that the item table cannot value, being neither listed nor a
variant of a listed type, is worth nothing to every rule that weighs actors
but the fight estimate, which counts a unit of it at `unknown_unit_value` (see
`fight`). The match record says so once for each such type.

A unit that boards a transport leaves the state's units and is reported lost
although it lives on aboard. The world tells such a unit from a lost one by
the agent's own orders, which the agent hands it with `note_orders`, and
keeps it in `cargo` until it comes out again or its transport is lost; see
`cargo`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping

from ucla_agent.core import records
from ucla_agent.games.redalert.cargo import CargoBook, CargoUnit
from ucla_agent.games.redalert.items import (
    ALLIED_BARRACKS,
    ALLIES,
    FACTION_FAMILIES,
    NON_COMBAT_TYPES,
    SHIP_QUEUE,
    SOVIET,
    TICKS_PER_SECOND,
    ItemTable,
)
from ucla_agent.games.redalert.tuning import WorldTuning

# A destroyed vehicle leaves a wreck whose type ends like this; it takes no orders and is no target.
WRECK_SUFFIX = "husk"
# The queues whose finished items wait until they are placed.
PLACED_QUEUES = frozenset({"Building", "Defense"})
# The enemy buildings that shoot.
DEFENSE_TYPES = frozenset({"pbox", "hbox", "gun", "ftur", "tsla", "agun", "sam"})
# An enemy unit not seen for this long (30 seconds) may be anywhere by now.
STALE_AFTER_TICKS = 750
# An own unit this close to the cell of a building would see the building if it still stood ...
SEEN_GONE_WITHIN_CELLS = 4
# ... once it has stood there this long (2 seconds): in the engine's states sight lags a unit's own cell.
SEEN_GONE_AFTER_TICKS = 50
ENEMY_KILLED_EVENT = "enemy_killed"
# The number of kills an `enemy_killed` event reports when it names none.
_KILLS_PER_EVENT = 1
_ERROR_SOURCE = "redalert.world"
# The lists of an observation that show enemies, and whether each holds buildings.
_ENEMY_LISTS = (("visible_enemies", False), ("visible_enemy_buildings", True))
_BOUNDS_LENGTH = 4

Cell = tuple[int, int]


@dataclass(frozen=True, slots=True)
class Unit:
    """One of the agent's own units, as the latest state shows it."""

    id: int
    type: str
    x: int
    y: int
    # Percent of full health.
    hp: int
    idle: bool
    activity: str | None
    stance: str | None


@dataclass(frozen=True, slots=True)
class Building:
    """One of the agent's own buildings, as the latest state shows it."""

    id: int
    type: str
    x: int
    y: int
    # Percent of full health.
    hp: int
    # It works: neither switched off nor disabled by low power.
    powered: bool
    # The agent switched it off.
    powered_down: bool
    repairing: bool
    producing: str | None
    rally: Cell | None


@dataclass(frozen=True, slots=True)
class ProductionItem:
    """One item in a production queue."""

    queue: str
    item: str
    progress: float
    remaining_s: float
    paused: bool
    ready: bool


@dataclass(frozen=True, slots=True)
class Economy:
    """The agent's money, power and harvesters.

    The game keeps money in two places: `cash`, and `ore`, which is what the
    harvesters have brought in and the refineries hold, up to
    `resource_capacity`. It pays from both, so what the agent has to spend is
    `spendable`, and that is the figure every rule about money reads.
    """

    cash: int = 0
    ore: int = 0
    resource_capacity: int = 0
    power_provided: int = 0
    power_drained: int = 0
    harvesters: int = 0

    @property
    def spendable(self) -> int:
        """The money the game can spend: the cash and the stored ore together."""
        return self.cash + self.ore


@dataclass(frozen=True, slots=True)
class Military:
    """The values the platform's tiebreak is decided on, and the worth of the army."""

    kills_cost: int = 0
    deaths_cost: int = 0
    assets_value: int = 0
    army_value: int = 0


@dataclass(slots=True)
class EnemyRecord:
    """An enemy unit or building, as it was the last time a state showed it."""

    id: int
    type: str
    x: int
    y: int
    # Percent of full health.
    hp: int
    is_building: bool
    last_seen_tick: int
    # It is in the latest state.
    visible: bool = True
    # A unit that has been out of sight so long that its place is no longer worth counting on.
    stale: bool = False
    # In sight in the state before as well, on the same cell: it has not moved since.
    still: bool = False


def is_wreck(actor: Mapping[str, Any]) -> bool:
    """Whether an actor of a state is a wreck: marked as one, or of a wreck's type."""
    return bool(actor.get("wreck")) or str(actor.get("type", "")).lower().endswith(WRECK_SUFFIX)


def _live(actors: Any) -> Iterator[Mapping[str, Any]]:
    """The actors of one of a state's lists that are not wrecks."""
    return (actor for actor in actors or () if not is_wreck(actor))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _cell(value: Any) -> Cell | None:
    """A cell from the `[x, y]` a state gives, or None when the state gives none."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), int(value[1])
    return None


def _cells(values: Any) -> tuple[Cell, ...]:
    return tuple(cell for cell in map(_cell, values or ()) if cell is not None)


def _any_within(cells: Iterable[Cell], record: EnemyRecord, reach: int) -> bool:
    """Whether one of the cells is within `reach` cells of where an enemy was last seen."""
    return any((x - record.x) ** 2 + (y - record.y) ** 2 <= reach * reach for x, y in cells)


def _farthest(cells: Iterable[Cell], origin: Cell) -> Cell:
    return max(cells, key=lambda cell: (cell[0] - origin[0]) ** 2 + (cell[1] - origin[1]) ** 2)


class World:
    """What the agent knows about the match it is playing. See the module's note."""

    def __init__(
        self, items: ItemTable, game_config: Mapping[str, Any] | None = None, tuning: WorldTuning | None = None
    ) -> None:
        """An empty world. `game_config` is the game's reference object, whose map data gives the spawns; `tuning`
        says how the world follows transports, by default as the built-in values do."""
        self.items = items
        self._maps = _mapping(_mapping(_mapping(game_config).get("rules")).get("maps"))
        self._ship_types = frozenset(item.name for item in items if item.queue == SHIP_QUEUE)
        self.tick = 0
        self.game_seconds = 0.0
        self.remaining_seconds: float | None = None
        self.phase: str | None = None
        self.faction: str | None = None
        self.enemy_faction: str | None = None
        # `allies` or `soviet`: which of the two sets of buildings and units the faction has.
        self.family: str | None = None
        # The playable part of the map as (x, y, width, height).
        self.map_bounds: tuple[int, int, int, int] | None = None
        self.base_center: Cell | None = None
        self.enemy_base: Cell | None = None
        self.spawns: tuple[Cell, ...] = ()
        self.economy = Economy()
        self.military = Military()
        # Own units and buildings by id; a wreck is in neither.
        self.units: dict[int, Unit] = {}
        self.buildings: dict[int, Building] = {}
        # The own units that fight, by id.
        self.combat_units: dict[int, Unit] = {}
        # The items of each production queue that has any, in queue order.
        self.production: dict[str, list[ProductionItem]] = {}
        self.available_production: frozenset[str] = frozenset()
        # The finished buildings and defenses that wait to be placed.
        self.ready_to_place: tuple[str, ...] = ()
        # The events of the latest state that no earlier update has seen, oldest first.
        self.events_new: list[dict[str, Any]] = []
        # Whether event numbers were missed on the way to the newest one, so that events were lost.
        self.events_gap = False
        self._last_event_seq: int | None = None
        # Every enemy unit and building seen in this match and not seen destroyed, by id.
        self.enemy_memory: dict[int, EnemyRecord] = {}
        # The remembered enemy buildings that shoot, in sight or not.
        self.known_enemy_defenses: list[EnemyRecord] = []
        self._enemies_in_sight: frozenset[int] = frozenset()
        # The enemies that left sight in the latest state as kills were reported that account for all of them.
        # Every one of them, unit or building, is forgotten in that update.
        self.killed_in_sight: frozenset[int] = frozenset()
        # Remembered building id -> the tick since which it has been absent with an own unit near its cell.
        self._absent_near_since: dict[int, int] = {}
        # The types of actor this match has shown, each looked up in the item table once.
        self._types_seen: set[str] = set()
        self._cargo_book = CargoBook(tuning or WorldTuning())
        # Transport id -> the own units aboard it, in the order they boarded; a transport with none is not listed.
        self.cargo: dict[int, tuple[CargoUnit, ...]] = {}
        # The units that were aboard a transport lost in the latest state: they are lost with it.
        self.cargo_lost: tuple[CargoUnit, ...] = ()
        # In the latest state: the id of a unit that was aboard -> the new id it came out of its transport under.
        self.taken_over: dict[int, int] = {}

    @property
    def trade_balance(self) -> int:
        """The value destroyed minus the value lost: the first figure of the platform's tiebreak."""
        return self.military.kills_cost - self.military.deaths_cost

    def in_bounds(self, x: int, y: int) -> bool:
        """Whether a cell lies in the playable part of the map. False while the map is not known."""
        if self.map_bounds is None:
            return False
        left, top, width, height = self.map_bounds
        return left <= x < left + width and top <= y < top + height

    def note_orders(self, orders: Iterable[Mapping[str, Any]]) -> None:
        """Take in the orders the agent sends with the latest state: its boarding and unloading orders are what the
        world tells cargo from lost units by."""
        self._cargo_book.note_orders(orders, self.tick)

    def boarding_due(self) -> list[dict[str, Any]]:
        """The `enter_transport` orders to send again now, one for each transport: a unit still walking to its
        transport a whole boarding window after it was last ordered in would be taken for lost when it boards."""
        return self._cargo_book.due(self.tick)

    def aboard(self, unit_id: int) -> CargoUnit | None:
        """An own unit aboard a transport, as last seen before it boarded; None for a unit that is not aboard."""
        return self._cargo_book.aboard(unit_id)

    def transport_of(self, unit_id: int) -> int | None:
        """The id of the transport an own unit is aboard; None for a unit that is not aboard."""
        return self._cargo_book.transport_of(unit_id)

    def update(self, raw_state: Mapping[str, Any]) -> None:
        """Take in one state, as received. A state without an observation (before the game runs) sets the phase only."""
        self.phase = raw_state.get("phase")
        observation = raw_state.get("observation")
        if not isinstance(observation, Mapping):
            return
        units_before = self.units
        self._read_clock(raw_state, observation)
        self._read_map(_mapping(raw_state.get("map")))
        self._read_figures(observation)
        self._read_own_actors(observation)
        self._read_production(observation)
        self._read_sides(observation)
        self._read_bases(observation, _mapping(raw_state.get("legal_actions")))
        self._read_events(observation)
        self._follow_cargo(units_before)
        self._remember_enemies(observation)
        self._report_unknown_types()

    def _follow_cargo(self, units_before: Mapping[int, Unit]) -> None:
        """Bring the cargo up to date from the units of the state before: who boarded, who came out, what was lost."""
        book = self._cargo_book
        book.update(self.tick, units_before, self.units, self.events_new)
        self.cargo, self.cargo_lost, self.taken_over = book.cargo(), book.lost, book.taken_over

    def _report_unknown_types(self) -> None:
        """Log, the first time it is shown, each type of actor in the state that the item table cannot value."""
        shown = {actor.type for actors in (self.units, self.buildings) for actor in actors.values()}
        shown.update(self.enemy_memory[enemy_id].type for enemy_id in self._enemies_in_sight)
        for actor_type in shown - self._types_seen:
            self._types_seen.add(actor_type)
            if self.items.get(actor_type) is None:
                _log_unknown_type(actor_type)

    def _remember_enemies(self, observation: Mapping[str, Any]) -> None:
        """Bring the enemy memory up to date: what is in sight, what is seen to be gone, what went out of sight."""
        in_sight_before = self._enemies_in_sight
        self._enemies_in_sight = frozenset(self._see_enemies(observation))
        self._forget_killed_in_sight(in_sight_before)
        self._forget_buildings_seen_gone()
        for record in self.enemy_memory.values():
            if record.id not in self._enemies_in_sight:
                record.visible = False
                record.stale = not record.is_building and self.tick - record.last_seen_tick >= STALE_AFTER_TICKS
        self.known_enemy_defenses = [
            record for record in self.enemy_memory.values() if record.is_building and record.type in DEFENSE_TYPES
        ]

    def _see_enemies(self, observation: Mapping[str, Any]) -> set[int]:
        """Remember every enemy actor the state shows as it is now, and return the ids of those in sight."""
        in_sight = set()
        for name, holds_buildings in _ENEMY_LISTS:
            for raw in observation.get(name) or ():
                if is_wreck(raw):
                    # The wreck of something remembered: whatever it was, it is no longer a unit or a target.
                    self.enemy_memory.pop(raw["id"], None)
                    continue
                before = self.enemy_memory.get(raw["id"])
                self.enemy_memory[raw["id"]] = EnemyRecord(
                    id=raw["id"],
                    type=self.items.base_name(str(raw["type"])),
                    x=raw["x"],
                    y=raw["y"],
                    hp=raw.get("hp", 0),
                    is_building=holds_buildings,
                    last_seen_tick=self.tick,
                    still=before is not None and before.visible and (before.x, before.y) == (raw["x"], raw["y"]),
                )
                in_sight.add(raw["id"])
        return in_sight

    def _buildings_out_of_sight(self) -> list[EnemyRecord]:
        in_sight = self._enemies_in_sight
        return [record for record in self.enemy_memory.values() if record.is_building and record.id not in in_sight]

    def _forget_killed_in_sight(self, in_sight_before: frozenset[int]) -> None:
        """Drop the remembered enemies, units and buildings alike, that were in the last state, are not in this one,
        and the kills reported in between account for: every one that left sight, or none. See the module's note."""
        left_sight = in_sight_before - self._enemies_in_sight
        killed = sum(
            event.get("count", _KILLS_PER_EVENT) for event in self.events_new if event.get("kind") == ENEMY_KILLED_EVENT
        )
        self.killed_in_sight = left_sight if 0 < len(left_sight) <= killed else frozenset()
        for enemy_id in self.killed_in_sight:
            # A wreck shown in its place has already taken it out of the memory.
            self.enemy_memory.pop(enemy_id, None)

    def _forget_buildings_seen_gone(self) -> None:
        """Drop the remembered buildings out of sight that an own unit has stood so close to for a while that it would
        see them if they still stood. A building does not walk away; see the module's note."""
        own_cells = {(unit.x, unit.y) for unit in self.units.values()}
        absent_near_since = {}
        for record in self._buildings_out_of_sight():
            if _any_within(own_cells, record, SEEN_GONE_WITHIN_CELLS):
                since = self._absent_near_since.get(record.id, self.tick)
                if self.tick - since >= SEEN_GONE_AFTER_TICKS:
                    del self.enemy_memory[record.id]
                else:
                    absent_near_since[record.id] = since
        self._absent_near_since = absent_near_since

    def _read_events(self, observation: Mapping[str, Any]) -> None:
        """Pick out the events numbered above the last one seen, and note whether a number was skipped on the way.

        The platform numbers a seat's events one by one and shows only the
        last 20, so a skipped number means events scrolled out between two
        states. The first window of a match has nothing before it to be
        missed; only a number skipped inside it counts.
        """
        last = self._last_event_seq
        new = [dict(event) for event in observation.get("events") or () if last is None or event["seq"] > last]
        gap = False
        for event in new:
            gap = gap or (last is not None and event["seq"] - last > 1)
            last = event["seq"]
        self.events_new, self.events_gap, self._last_event_seq = new, gap, last

    def _read_clock(self, raw_state: Mapping[str, Any], observation: Mapping[str, Any]) -> None:
        self.tick = observation.get("tick", raw_state.get("state_version", self.tick))
        self.game_seconds = self.tick / TICKS_PER_SECOND
        self.remaining_seconds = _mapping(raw_state.get("time")).get("remaining_seconds")

    def _read_map(self, state_map: Mapping[str, Any]) -> None:
        """The playable bounds from the state, else from the game reference, and the reference's spawns for this map."""
        known = _mapping(self._maps.get(state_map.get("id")))
        bounds = state_map.get("bounds") or known.get("bounds")
        if isinstance(bounds, (list, tuple)) and len(bounds) == _BOUNDS_LENGTH:
            self.map_bounds = (int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3]))
        self.spawns = _cells(known.get("spawns")) or self.spawns

    def _read_figures(self, observation: Mapping[str, Any]) -> None:
        economy, military = _mapping(observation.get("economy")), _mapping(observation.get("military"))
        self.economy = Economy(
            cash=economy.get("cash", 0),
            ore=economy.get("ore", 0),
            resource_capacity=economy.get("resource_capacity", 0),
            power_provided=economy.get("power_provided", 0),
            power_drained=economy.get("power_drained", 0),
            harvesters=economy.get("harvesters", 0),
        )
        self.military = Military(
            kills_cost=military.get("kills_cost", 0),
            deaths_cost=military.get("deaths_cost", 0),
            assets_value=military.get("assets_value", 0),
            army_value=military.get("army_value", 0),
        )

    def _read_own_actors(self, observation: Mapping[str, Any]) -> None:
        items = self.items
        self.units = {raw["id"]: _unit(raw, items) for raw in _live(observation.get("units"))}
        self.buildings = {raw["id"]: _building(raw, items) for raw in _live(observation.get("buildings"))}
        excluded, ships = NON_COMBAT_TYPES, self._ship_types
        self.combat_units = {
            unit_id: unit
            for unit_id, unit in self.units.items()
            if unit.type not in excluded and unit.type not in ships
        }

    def _read_production(self, observation: Mapping[str, Any]) -> None:
        queues: dict[str, list[ProductionItem]] = {}
        for raw in observation.get("production") or ():
            item = _production_item(raw)
            queues.setdefault(item.queue, []).append(item)
        self.production = queues
        self.available_production = frozenset(
            str(name).lower() for name in observation.get("available_production") or ()
        )
        self.ready_to_place = tuple(
            item.item for queue in sorted(PLACED_QUEUES & queues.keys()) for item in queues[queue] if item.ready
        )

    def _read_sides(self, observation: Mapping[str, Any]) -> None:
        """The two factions, and the family of the agent's own."""
        faction, enemy_faction = observation.get("faction"), observation.get("enemy_faction")
        self.faction = str(faction).lower() if faction else self.faction
        self.enemy_faction = str(enemy_faction).lower() if enemy_faction else self.enemy_faction
        # A faction the table does not know is told apart by what it is offered: only Allies can build a tent.
        offered_family = ALLIES if ALLIED_BARRACKS in self.available_production else SOVIET
        self.family = FACTION_FAMILIES.get(self.faction, offered_family)

    def _read_bases(self, observation: Mapping[str, Any], legal_actions: Mapping[str, Any]) -> None:
        """Where the agent's base is, and the enemy's start: as the state says, else the spawn farthest from the base."""
        self.base_center = _cell(observation.get("base_center"))
        named = _cell(legal_actions.get("enemy_base"))
        if named is not None:
            self.enemy_base = named
        elif self.enemy_base is None and self.spawns and self.base_center is not None:
            self.enemy_base = _farthest(self.spawns, self.base_center)


def _log_unknown_type(actor_type: str) -> None:
    """Say in the match record that a type of actor can be valued neither as itself nor as a variant of a known type."""
    records.log(
        "error",
        {
            "where": _ERROR_SOURCE,
            "type": records.HANDLED_ERROR,
            "exception": "UnknownItemType",
            "message": (
                f"the item table knows no type '{actor_type}' nor one it is a variant of; it is valued at 0, "
                "and a unit of it at [combat] unknown_unit_value in a fight estimate"
            ),
        },
    )


def _unit(raw: Mapping[str, Any], items: ItemTable) -> Unit:
    return Unit(
        id=raw["id"],
        type=items.base_name(str(raw["type"])),
        x=raw["x"],
        y=raw["y"],
        hp=raw.get("hp", 0),
        idle=bool(raw.get("idle")),
        activity=raw.get("act"),
        stance=raw.get("stance"),
    )


def _building(raw: Mapping[str, Any], items: ItemTable) -> Building:
    producing = raw.get("producing")
    return Building(
        id=raw["id"],
        type=items.base_name(str(raw["type"])),
        x=raw["x"],
        y=raw["y"],
        hp=raw.get("hp", 0),
        powered=bool(raw.get("powered", True)),
        powered_down=bool(raw.get("powered_down")),
        repairing=bool(raw.get("repairing")),
        producing=str(producing).lower() if producing else None,
        rally=_cell(raw.get("rally")),
    )


def _production_item(raw: Mapping[str, Any]) -> ProductionItem:
    return ProductionItem(
        queue=str(raw["queue"]),
        item=str(raw["item"]).lower(),
        progress=raw.get("progress", 0.0),
        remaining_s=raw.get("remaining_s", 0.0),
        paused=bool(raw.get("paused")),
        ready=bool(raw.get("ready")),
    )
