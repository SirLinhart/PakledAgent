"""How many ore trucks each field takes, by its ore mines, with `truck_shares_by_mines` of [upkeep] on.

A mine grows the ore of its field back, so a field's harvesters are counted
by its mines, not by the refineries standing against it nor by its size: the
share of a field is `trucks_per_mine` for each mine it holds (see
`terrain.OrePatch.mines`), `trucks_mineless_field` for a field without one,
and none for a field without a mine that the move to fresh ore marks dry. A
field takes trucks only while it is active: an own refinery stands counted
against it (`refinery_fields.field_counted_from`: serving it within
`refinery_serves_cells`, else within `refinery_counts_cells`), so an
expansion refinery standing a little farther from its ore still has its
field worked.

Every truck has a field it is assigned to, kept until the truck is lost or
moved to another field by this rule or the move to fresh ore: arriving there,
or being sent off it to shelter or to park, does not end it. Every order that
sends a truck back to harvest names an ore cell of that field (`to`), so that
a truck sent back after fleeing or idling works its own field again and
unloads at the refinery by it, not wherever it stands. A truck seen for the
first time, a refinery's free one included, goes to the nearest active field
short of its share; with none short, to the nearest active field. While an
active field is short, one truck of a field over its share, the one nearest
the short field, moves there at most once every `truck_rebalance_seconds`,
and at once in the cycle a new own refinery stands; a field is never taken
below its share, and spares stay where they are while no field is short. No
truck is sent to a dry field.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Mapping

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, placement, refinery_fields
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.family import REFINERY
from ucla_agent.games.redalert.harvester_room import HARVESTERS_REBALANCED_EVENT
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.terrain import OrePatch
from ucla_agent.games.redalert.world import Building, Cell, Unit, World

# A new truck this close to a refinery that has just stood is its free one.
FREE_TRUCK_CELLS = 6


def is_on(ctx: MatchContext) -> bool:
    """Whether truck shares go by ore mines: `truck_shares_by_mines` of [upkeep] is on."""
    return ctx.tuning.upkeep.truck_shares_by_mines


def active_fields(world: World, ctx: MatchContext) -> Counter[OrePatch]:
    """The fields an own refinery stands counted against (`refinery_fields.field_counted_from`), each with how many,
    in the order of their first refinery."""
    tuning, terrain = ctx.tuning.placement, ctx.terrain
    fields = (
        refinery_fields.field_counted_from(
            placement.box_at(REFINERY, refinery_fields.footprint_cell(building)), terrain, tuning
        )
        for building in world.buildings.values()
        if building.type == REFINERY
    )
    return Counter(field for field in fields if field is not None)


def share(field: OrePatch, ctx: MatchContext) -> int:
    """How many trucks an active field takes: `trucks_per_mine` for each of its mines; without a mine,
    `trucks_mineless_field`, or none while the field is marked dry."""
    upkeep = ctx.tuning.upkeep
    if field.mines:
        return upkeep.trucks_per_mine * field.mines
    return 0 if field in ctx.dry_fields else upkeep.trucks_mineless_field


def shares(world: World, ctx: MatchContext) -> dict[OrePatch, int]:
    """The share of each active field (`active_fields`)."""
    return {field: share(field, ctx) for field in active_fields(world, ctx)}


def cap(field_shares: Mapping[OrePatch, int], ctx: MatchContext) -> int:
    """The most trucks wanted with `truck_cap_by_shares` on: the shares of the active fields together,
    `harvesters_max` at the most."""
    return min(ctx.tuning.upkeep.harvesters_max, sum(field_shares.values()))


def harvest_orders(unit_ids: Iterable[int], world: World, ctx: MatchContext, priority: Priority) -> list[Intent]:
    """The `harvest` orders that send trucks back to work: each truck with a field it works (`ctx.truck_fields`) to
    the ore cell of that field nearest it, the trucks to one cell in one order; the rest in one order without a cell,
    to harvest wherever they find ore."""
    by_cell: dict[Cell, list[int]] = {}
    bare: list[int] = []
    for unit_id in unit_ids:
        field, unit = ctx.truck_fields.get(unit_id), world.units.get(unit_id)
        if field is None or unit is None:
            bare.append(unit_id)
        else:
            by_cell.setdefault(field.nearest_cell((unit.x, unit.y)), []).append(unit_id)
    orders = [Intent(priority, {"cmd": "harvest", "units": ids, "to": list(cell)}) for cell, ids in by_cell.items()]
    return [*orders, Intent(priority, {"cmd": "harvest", "units": bare})] if bare else orders


class TruckShares:
    """The field each truck is assigned to, the shares of the active fields in this cycle, the fields new trucks go
    to and the trucks moved to a field short of its share. See the module's note."""

    def __init__(self) -> None:
        # Truck id -> the field it is assigned to.
        self._assigned: dict[int, OrePatch] = {}
        # The ids of the trucks and of the own refineries seen in the last cycle; None before the first.
        self._seen: frozenset[int] | None = None
        self._refineries: frozenset[int] | None = None
        # Whether an own refinery stands in this cycle that did not in the last.
        self._is_refinery_new = False
        # The tick a truck was last moved to a short field; None before one was.
        self._moved_at: int | None = None
        self._shares: dict[OrePatch, int] = {}

    @property
    def field_shares(self) -> Mapping[OrePatch, int]:
        """The share of each active field in this cycle."""
        return self._shares

    def assigned_counts(self) -> Counter[OrePatch]:
        """How many trucks are assigned to each field."""
        return Counter(self._assigned.values())

    def note_cycle(self, harvesters: list[Unit], world: World, ctx: MatchContext) -> frozenset[int]:
        """Start a cycle: work the shares out, forget the trucks that are gone, give each truck seen before but
        assigned to no field the active field nearest it, note whether a new own refinery stands, and publish the
        field each truck works (`ctx.truck_fields`); the ids of the trucks new since the last cycle (none in the
        first)."""
        ids = frozenset(unit.id for unit in harvesters)
        new = frozenset() if self._seen is None else ids - self._seen
        self._seen = ids
        refineries = frozenset(building.id for building in world.buildings.values() if building.type == REFINERY)
        self._is_refinery_new = self._refineries is not None and not refineries <= self._refineries
        self._refineries = refineries
        self._shares = shares(world, ctx)
        self._assigned = {truck: field for truck, field in self._assigned.items() if truck in ids}
        for unit in harvesters:
            if unit.id not in self._assigned and unit.id not in new and self._shares:
                self._assigned[unit.id] = _nearest((unit.x, unit.y), self._shares)
        self._publish(ctx)
        return new

    def pin(self, moves: Iterable[Intent], ctx: MatchContext) -> None:
        """Assign each truck a `harvest` order sends to an ore cell to that cell's field: the move to fresh ore's."""
        for intent in moves:
            cell = tuple(intent.order["to"])
            field = next((field for field in ctx.terrain.ore_patches if cell in field.cells), None)
            if field is not None:
                self._assigned.update(dict.fromkeys(intent.order["units"], field))
        self._publish(ctx)

    def send_new(
        self, harvesters: list[Unit], new: frozenset[int], excluded: frozenset[int], world: World, ctx: MatchContext
    ) -> list[Intent]:
        """`harvest` to an ore cell for each new truck but those `excluded`: of the active field short of its share
        nearest it, else of the nearest active field, never a dry one; each assigned there. None for a truck with no
        such field."""
        by_cell: dict[Cell, list[int]] = {}
        for unit in harvesters:
            if unit.id not in new or unit.id in excluded:
                continue
            here = (unit.x, unit.y)
            open_fields = [field for field in self._shares if field not in ctx.dry_fields]
            short = [field for field in open_fields if self._shortfalls()[field] >= 1]
            field = _nearest(here, short or open_fields)
            if field is None:
                continue
            self._assigned[unit.id] = field
            by_cell.setdefault(field.nearest_cell(here), []).append(unit.id)
        self._publish(ctx)
        return [
            Intent(Priority.PRODUCTION, {"cmd": "harvest", "units": units, "to": list(cell)})
            for cell, units in by_cell.items()
        ]

    def rebalance(
        self, harvesters: list[Unit], excluded: frozenset[int], world: World, ctx: MatchContext
    ) -> list[Intent]:
        """Once every `truck_rebalance_seconds`, and in the cycle a new own refinery stands, `harvest` to the field most
        short of its share, not dry, for the truck nearest it of those not `excluded` assigned to a field over its
        share, with a `harvesters_rebalanced` event. Nothing while no field is short or none is over."""
        window = ctx.tuning.upkeep.truck_rebalance_seconds * TICKS_PER_SECOND
        is_due = self._is_refinery_new or self._moved_at is None or world.tick - self._moved_at >= window
        shortfalls = self._shortfalls()
        short = [field for field in self._shares if field not in ctx.dry_fields and shortfalls[field] >= 1]
        movable = [
            unit
            for unit in harvesters
            if unit.id not in excluded and unit.id in self._assigned and shortfalls[self._assigned[unit.id]] < 0
        ]
        if not is_due or not short or not movable:
            return []
        target = max(short, key=lambda field: shortfalls[field])
        unit = min(movable, key=lambda truck: cells.distance(_cell_of(truck), target.nearest_cell(_cell_of(truck))))
        return [self._moved(unit, target, world, ctx)]

    def _moved(self, unit: Unit, target: OrePatch, world: World, ctx: MatchContext) -> Intent:
        """The order that moves a truck to a short field, assigned there from now on, with its event."""
        source = self._assigned[unit.id]
        self._assigned[unit.id] = target
        self._moved_at = world.tick
        self._publish(ctx)
        data = {
            "tick": world.tick,
            "harvester": unit.id,
            "from_field_center": list(source.center),
            "to_field_center": list(target.center),
        }
        records.log(HARVESTERS_REBALANCED_EVENT, data)
        cell = target.nearest_cell(_cell_of(unit))
        return Intent(Priority.PRODUCTION, {"cmd": "harvest", "units": [unit.id], "to": list(cell)})

    def _shortfalls(self) -> Counter[OrePatch]:
        """How many trucks each field is short of its share: its share (none for a field that is not active) less the
        trucks assigned to it, below 0 for a field over its share."""
        shortfalls = Counter(self._shares)
        shortfalls.subtract(self.assigned_counts())
        return shortfalls

    def _publish(self, ctx: MatchContext) -> None:
        """Publish the field each truck works as `ctx.truck_fields`: the field it is assigned to while that is active
        and not dry."""
        ctx.truck_fields = {
            truck: field
            for truck, field in self._assigned.items()
            if field in self._shares and field not in ctx.dry_fields
        }


class FreeTrucks:
    """The free trucks the own refineries still bring, with `truck_cap_by_shares` of [upkeep] on: a refinery that has
    stood for less than `free_truck_wait_seconds` brings one, until a new truck shows up within `FREE_TRUCK_CELLS`
    of it. A truck bought for a refinery that brings one would stand on top of the free one."""

    def __init__(self) -> None:
        # Refinery id -> the tick it was first seen, for the refineries that came after the first cycle and whose free
        # truck has not shown up; and the ids of the refineries and trucks of the last cycle, None before the first.
        self._since: dict[int, int] = {}
        self._refineries: frozenset[int] | None = None
        self._trucks: frozenset[int] | None = None

    def note_cycle(self, harvesters: list[Unit], world: World) -> None:
        """Note the refineries new since the last cycle, and each refinery a new truck shows up by: its free one."""
        standing = {building.id: building for building in world.buildings.values() if building.type == REFINERY}
        trucks = frozenset(unit.id for unit in harvesters)
        if self._refineries is not None:
            self._since.update(dict.fromkeys(set(standing) - self._refineries, world.tick))
        new_trucks = [unit for unit in harvesters if self._trucks is not None and unit.id not in self._trucks]
        self._refineries, self._trucks = frozenset(standing), trucks
        self._since = {refinery: tick for refinery, tick in self._since.items() if refinery in standing}
        for unit in new_trucks:
            here = _cell_of(unit)
            near = [
                refinery for refinery in self._since if cells.is_within(here, _cell(standing, refinery), FREE_TRUCK_CELLS)
            ]
            if near:
                del self._since[min(near, key=lambda refinery: cells.distance(here, _cell(standing, refinery)))]

    def pending(self, world: World, ctx: MatchContext) -> Counter[OrePatch | None]:
        """The free trucks still due, by the field each refinery counts against (None for one that counts against
        none): one for each refinery seen less than `free_truck_wait_seconds` ago whose truck has not shown up."""
        wait = ctx.tuning.upkeep.free_truck_wait_seconds * TICKS_PER_SECOND
        tuning, terrain = ctx.tuning.placement, ctx.terrain
        return Counter(
            refinery_fields.field_counted_from(
                placement.box_at(REFINERY, refinery_fields.footprint_cell(world.buildings[refinery])), terrain, tuning
            )
            for refinery, tick in self._since.items()
            if world.tick - tick < wait
        )


def _cell(buildings: Mapping[int, Building], building_id: int) -> Cell:
    """The cell of one of the buildings."""
    building = buildings[building_id]
    return building.x, building.y


def _cell_of(unit: Unit) -> Cell:
    """A unit's cell."""
    return unit.x, unit.y


def _nearest(cell: Cell, fields: Iterable[OrePatch]) -> OrePatch | None:
    """The field whose ore cell nearest a cell lies nearest it; None of none."""
    return min(fields, key=lambda field: cells.distance(cell, field.nearest_cell(cell)), default=None)
