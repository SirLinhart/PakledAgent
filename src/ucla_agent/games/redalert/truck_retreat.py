"""Ore trucks that retreat from a threat they cannot crush, with `truck_retreat` of [base_defense] on.

A truck under fire from a tank, an aircraft, a defense or a crowd of
infantry loses its load and soon itself, while a truck can run over a lone
rifleman or two. So in every cycle each own truck, but one on a harvester
crush run and all while parking is in force, looks at the threats near it:
the enemy units seen in the last `REMEMBERED_SECONDS` and the known enemy
ground defenses within `truck_threat_cells` of it, every enemy ground defense
whose weapon range and `DEFENSE_REACH_MARGIN` reach it, and, when base
defense names the truck among the attacked, every enemy unit in sight whose
weapon reaches it the same way, wherever it stands.

The truck cannot crush them when they hold a vehicle, an aircraft or a
defense, more than `truck_crushable_infantry_max` infantry, or two rocket or
shock troopers or more; but one enemy unit worth less than
`truck_harasser_max_value`, neither a defense nor an aircraft, is a lone
harasser it ignores while it has `HALF_HEALTH` or more. It then retreats with
a `move` to its shelter: of the own refineries with an own ground defense
within `SHELTER_DEFENSE_CELLS` and the middles of main and the guard while
each has a unit fit to fight (`fit_hp` of [raids]), the nearest to the truck
of those farther from the threats' middle than the truck is, so that it never
runs toward them; base_center without one. The move is sent again every
`RESEND_TICKS` while the truck stands idle more than `SHELTER_ARRIVED_CELLS`
from its shelter.

Once no threat it cannot crush has been near it for
`truck_retreat_clear_seconds`, it goes back to `harvest` the ore of the field
it worked before the retreat (its truck share's field when it has one), or
without a field or with that field dry, wherever it finds ore, for the move to
fresh ore to take it on. The retreating trucks are published as fleeing
(`ctx.fleeing_harvesters`), which the other truck rules leave alone. Each
retreat and each return is a `base_defense` event, of the reason
`truck_retreat` (the truck, the kinds of threat and the shelter) and
`truck_return` (the truck and the field).
"""

from __future__ import annotations

import math

from dataclasses import dataclass

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, truck_shares
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.family import HARVESTER, REFINERY, ROCKET_INFANTRY
from ucla_agent.games.redalert.far_rescue import BASE_DEFENSE_EVENT
from ucla_agent.games.redalert.fight import centroid
from ucla_agent.games.redalert.groups import GUARD, MAIN
from ucla_agent.games.redalert.items import TICKS_PER_SECOND, is_air_only
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.parking import parking_reason
from ucla_agent.games.redalert.production import INFANTRY_QUEUE
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.terrain import OrePatch
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

# An enemy unit out of sight counts as near a truck for this long after it was last seen there.
REMEMBERED_SECONDS = 10
# An enemy defense threatens a truck its weapon reaches with this many cells to spare; so does an attacker in sight.
DEFENSE_REACH_MARGIN = 1
# The percent of full health below which a truck runs from a lone harasser too.
HALF_HEALTH = 50
# The ground defenses that make a refinery a shelter: not the AA gun nor the SAM site, which shoot only aircraft.
SHELTER_DEFENSES = frozenset({"pbox", "hbox", "gun", "ftur", "tsla"})
SHELTER_DEFENSE_CELLS = 8
# With truck_shelter_safe on, an enemy combat unit seen this lately near a shelter makes it none.
SHELTER_SEEN_SECONDS = 20
# The infantry that kill trucks: two of them are a threat a truck cannot crush.
ANTI_ARMOR_INFANTRY = frozenset({ROCKET_INFANTRY, "shok"})
ANTI_ARMOR_MOST = 1
# How often a retreating truck standing idle short of its shelter is sent again, and how near it counts as there.
RESEND_TICKS = 3 * TICKS_PER_SECOND
SHELTER_ARRIVED_CELLS = 3
# The kinds of threat a `truck_retreat` event names.
VEHICLE, AIRCRAFT, DEFENSE, INFANTRY = "vehicle", "aircraft", "defense", "infantry"
# The reasons of the `base_defense` events.
TRUCK_RETREAT_REASON = "truck_retreat"
TRUCK_RETURN_REASON = "truck_return"


@dataclass
class _Retreat:
    """A truck on its retreat: its shelter, the field it worked, the last tick a threat it cannot crush was near it
    and the tick it was last sent to its shelter."""

    shelter: Cell
    field: OrePatch | None
    threatened_at: int
    sent_at: int


def is_on(ctx: MatchContext) -> bool:
    """Whether trucks retreat from threats they cannot crush: `truck_retreat` of [base_defense] is on."""
    return ctx.tuning.base_defense.truck_retreat


class TruckRetreat:
    """Sends the trucks threatened by what they cannot crush to shelter and back to their field. See the module's
    note."""

    def __init__(self) -> None:
        # Truck id -> its retreat, while it lasts.
        self._retreats: dict[int, _Retreat] = {}

    def intents(self, world: World, ctx: MatchContext, attacked: frozenset[int]) -> list[Intent]:
        """The retreats, resends and returns of this cycle, the trucks named in `attacked` threatened by every attacker
        in reach; publishes the retreating trucks as `ctx.fleeing_harvesters`."""
        trucks = [unit for unit in world.units.values() if unit.type == HARVESTER]
        self._retreats = {truck: retreat for truck, retreat in self._retreats.items() if truck in world.units}
        orders: list[Intent] = []
        if parking_reason(world, ctx) is None:
            for truck in trucks:
                if truck.id not in ctx.harvester_crush:
                    orders += self._step(truck, threats_to(truck, world, ctx, truck.id in attacked), world, ctx)
        ctx.fleeing_harvesters = frozenset(self._retreats)
        return orders

    def _step(self, truck: Unit, threats: list[EnemyRecord], world: World, ctx: MatchContext) -> list[Intent]:
        """A truck's order of the cycle: its retreat when a threat it cannot crush is near (`must_retreat`), its move
        sent again while it is on its retreat, or its return once it has been clear long enough."""
        retreat = self._retreats.get(truck.id)
        if must_retreat(truck, threats, world, ctx):
            if retreat is None:
                return self._start(truck, threats, world, ctx)
            retreat.threatened_at = world.tick
        elif retreat is not None:
            clear_ticks = ctx.tuning.base_defense.truck_retreat_clear_seconds * TICKS_PER_SECOND
            if world.tick - retreat.threatened_at >= clear_ticks:
                return self._return(truck, retreat, world, ctx)
        return [] if retreat is None else self._resend(truck, retreat, world.tick)

    def _start(self, truck: Unit, threats: list[EnemyRecord], world: World, ctx: MatchContext) -> list[Intent]:
        """The move of a truck that starts its retreat to its shelter (`shelter_of`), with its `truck_retreat` event;
        none without a shelter."""
        shelter = shelter_of(truck, threats, world, ctx)
        if shelter is None:
            return []
        field = ctx.truck_fields.get(truck.id) or ctx.terrain.nearest_patch((truck.x, truck.y))
        self._retreats[truck.id] = _Retreat(shelter, field, world.tick, world.tick)
        data = {
            "tick": world.tick,
            "reason": TRUCK_RETREAT_REASON,
            "harvester": truck.id,
            "threats": sorted({kind_of(threat, world) for threat in threats}),
            "shelter": list(shelter),
        }
        records.log(BASE_DEFENSE_EVENT, data)
        return [_move(truck.id, shelter)]

    @staticmethod
    def _resend(truck: Unit, retreat: _Retreat, tick: int) -> list[Intent]:
        """The move sent again to a retreating truck standing idle short of its shelter, once in `RESEND_TICKS`."""
        is_short = not cells.is_within((truck.x, truck.y), retreat.shelter, SHELTER_ARRIVED_CELLS)
        if not truck.idle or not is_short or tick - retreat.sent_at < RESEND_TICKS:
            return []
        retreat.sent_at = tick
        return [_move(truck.id, retreat.shelter)]

    def _return(self, truck: Unit, retreat: _Retreat, world: World, ctx: MatchContext) -> list[Intent]:
        """The `harvest` of a truck whose retreat is over: to the ore of its field, nearest it, unless that field is dry
        or there is none, then wherever it finds ore; with its `truck_return` event."""
        del self._retreats[truck.id]
        field = None if retreat.field in ctx.dry_fields else retreat.field
        data = {
            "tick": world.tick,
            "reason": TRUCK_RETURN_REASON,
            "harvester": truck.id,
            "field_center": None if field is None else list(field.center),
        }
        records.log(BASE_DEFENSE_EVENT, data)
        if field is None:
            return truck_shares.harvest_orders([truck.id], world, ctx, Priority.EMERGENCY)
        cell = field.nearest_cell((truck.x, truck.y))
        return [Intent(Priority.EMERGENCY, {"cmd": "harvest", "units": [truck.id], "to": list(cell)})]


def threats_to(truck: Unit, world: World, ctx: MatchContext, is_attacked: bool) -> list[EnemyRecord]:
    """The threats near a truck (see the module's note): the enemy units seen lately and the known enemy ground
    defenses within `truck_threat_cells`, the ground defenses whose weapon reaches it, and while it is `is_attacked`
    the enemy units in sight whose weapon reaches it."""
    here, reach = (truck.x, truck.y), ctx.tuning.base_defense.truck_threat_cells
    seen_since = world.tick - REMEMBERED_SECONDS * TICKS_PER_SECOND
    units = [
        record
        for record in world.enemy_memory.values()
        if is_combat_unit(record)
        and record.last_seen_tick >= seen_since
        and (cells.is_within(here, _cell(record), reach) or _is_attacker(record, here, is_attacked, world))
    ]
    defenses = [
        record
        for record in world.known_enemy_defenses
        if not is_air_only(record.type, world.items)
        and (cells.is_within(here, _cell(record), reach) or _reaches(record, here, world))
    ]
    return [*units, *defenses]


def _is_attacker(record: EnemyRecord, here: Cell, is_attacked: bool, world: World) -> bool:
    """Whether an enemy unit counts as the attacker of an attacked truck at a cell: it is in sight and its weapon
    reaches the truck."""
    return is_attacked and record.visible and _reaches(record, here, world)


def must_retreat(truck: Unit, threats: list[EnemyRecord], world: World, ctx: MatchContext) -> bool:
    """Whether a truck retreats from its threats: it cannot crush them (a vehicle, an aircraft or a defense among
    them, more than `truck_crushable_infantry_max` infantry, or two anti-armor infantry or more), and they are no lone
    harasser it ignores (one unit worth less than `truck_harasser_max_value`, neither a defense nor an aircraft, while
    the truck has `HALF_HEALTH` or more)."""
    tuning = ctx.tuning.base_defense
    kinds = [kind_of(threat, world) for threat in threats]
    anti_armor = sum(1 for threat in threats if world.items.base_name(threat.type) in ANTI_ARMOR_INFANTRY)
    is_heavy = any(kind != INFANTRY for kind in kinds)
    is_crowd = kinds.count(INFANTRY) > tuning.truck_crushable_infantry_max or anti_armor > ANTI_ARMOR_MOST
    is_uncrushable = is_heavy or is_crowd
    return is_uncrushable and not _is_lone_harasser(truck, threats, kinds, world, ctx)


def _is_lone_harasser(
    truck: Unit, threats: list[EnemyRecord], kinds: list[str], world: World, ctx: MatchContext
) -> bool:
    """Whether the threats are one unit, neither a defense nor an aircraft, worth less than
    `truck_harasser_max_value`, and the truck has `HALF_HEALTH` or more."""
    if len(threats) != 1 or kinds[0] in (DEFENSE, AIRCRAFT) or truck.hp < HALF_HEALTH:
        return False
    facts = world.items.get(threats[0].type)
    return facts is not None and facts.cost < ctx.tuning.base_defense.truck_harasser_max_value


def kind_of(threat: EnemyRecord, world: World) -> str:
    """What a threat is: a defense, an aircraft, infantry or else a vehicle."""
    if threat.is_building:
        return DEFENSE
    if world.items.damage.get(threat.type).air:
        return AIRCRAFT
    facts = world.items.get(threat.type)
    return INFANTRY if facts is not None and facts.queue == INFANTRY_QUEUE else VEHICLE


def shelter_of(truck: Unit, threats: list[EnemyRecord], world: World, ctx: MatchContext) -> Cell | None:
    """Where a retreating truck goes: of the own refineries with an own ground defense within
    `SHELTER_DEFENSE_CELLS` and the middles of main and the guard with a unit fit to fight, the nearest the truck of
    those farther from the threats' middle than the truck; base_center without one."""
    here = (truck.x, truck.y)
    middle = centroid_of(threats)
    defenses = [(building.x, building.y) for building in world.buildings.values() if building.type in SHELTER_DEFENSES]
    refineries = [
        (building.x, building.y)
        for building in world.buildings.values()
        if building.type == REFINERY and _is_any_within((building.x, building.y), defenses, SHELTER_DEFENSE_CELLS)
    ]
    candidates = [*refineries, *_army_middles(world, ctx)]
    away = [cell for cell in candidates if cells.distance(cell, middle) > cells.distance(here, middle)]
    if ctx.tuning.base_defense.truck_shelter_safe and world.base_center is not None:
        # A shelter by the enemy, or beyond the threat from our base, is none (see `_is_safe_shelter`).
        safe = [cell for cell in away if _is_safe_shelter(cell, here, middle, world, ctx)]
        return min(safe, key=lambda cell: cells.distance(cell, world.base_center), default=world.base_center)
    return min(away, key=lambda cell: cells.distance(cell, here), default=world.base_center)


def _is_safe_shelter(cell: Cell, here: Cell, middle: tuple[float, float], world: World, ctx: MatchContext) -> bool:
    """With truck_shelter_safe on, whether a shelter is one: no enemy combat unit seen within SHELTER_SEEN_SECONDS
    nor enemy ground defense remembered within truck_shelter_clear_cells of it, and, lying farther from base_center
    than the truck, not with the threats' middle nearer base_center than it: the truck would run past them."""
    reach = ctx.tuning.base_defense.truck_shelter_clear_cells
    since = world.tick - SHELTER_SEEN_SECONDS * TICKS_PER_SECOND
    for record in world.enemy_memory.values():
        is_near_fighter = is_combat_unit(record) and record.last_seen_tick >= since
        is_defense = record.is_building and record.type in SHELTER_DEFENSES
        if (is_near_fighter or is_defense) and cells.is_within(cell, (record.x, record.y), reach):
            return False
    base = world.base_center
    is_beyond = cells.distance(cell, base) > cells.distance(here, base)
    return not (is_beyond and math.dist(middle, base) < cells.distance(cell, base))


def centroid_of(threats: list[EnemyRecord]) -> tuple[float, float]:
    """The middle of the threats' cells."""
    return sum(threat.x for threat in threats) / len(threats), sum(threat.y for threat in threats) / len(threats)


def _army_middles(world: World, ctx: MatchContext) -> list[Cell]:
    """The middle cells of main and the guard, each while it has a unit fit to fight (`fit_hp` of [raids])."""
    fit_hp, middles = ctx.tuning.raids.fit_hp, []
    for group in (MAIN, GUARD):
        units = [world.combat_units[unit_id] for unit_id in ctx.groups.members(group)]
        if any(unit.hp >= fit_hp for unit in units):
            middles.append(cells.nearest_cell(*centroid(units)))
    return middles


def _is_any_within(cell: Cell, others: list[Cell], reach: int) -> bool:
    """Whether one of the other cells lies within `reach` of a cell."""
    return any(cells.is_within(cell, other, reach) for other in others)


def _reaches(record: EnemyRecord, here: Cell, world: World) -> bool:
    """Whether an enemy's ground weapon reaches a cell with `DEFENSE_REACH_MARGIN` to spare."""
    facts = world.items.get(record.type)
    reach = None if facts is None else facts.range
    return reach is not None and cells.is_within(here, _cell(record), reach + DEFENSE_REACH_MARGIN)


def _cell(record: EnemyRecord) -> Cell:
    return record.x, record.y


def _move(truck_id: int, shelter: Cell) -> Intent:
    """The emergency move of a truck to its shelter."""
    return Intent(Priority.EMERGENCY, {"cmd": "move", "units": [truck_id], "to": list(shelter)})
