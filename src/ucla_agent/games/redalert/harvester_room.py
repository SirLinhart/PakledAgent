"""The harvester target, and the room the fields the own refineries serve have for more harvesters.

The target is min(`harvesters_max`, `harvesters_per_refinery` x the own
refineries standing): the one cap on harvesters. The harvesters standing and
those in production count against it, the free one each refinery brings
included.

With `harvester_cells_per` of [upkeep] above 0, a field one of the own
refineries serves (the field whose ore lies nearest it, as its harvesters
work, by the one rule refinery placement counts a field's refineries by:
`refinery_fields.served_field`) has a share of
max(1, min(`field_share_per_refinery` x the refineries serving it,
floor(its ore cells / `harvester_cells_per`))) harvesters, and room for its
share less those working it. A harvester works the served field it stands
nearest to, but one sent to a field counts there until it arrives. A new
harvester, a refinery's free one included, and one moved off a dry field, goes
to the field with the most room; with no field room left for one more, no
harvester is trained. No harvester is sent to a field marked dry, and a dry
field's room trains none. The ore cells are those of the start of the match:
the state says nothing of what is left, and a field that has run dry is the
move to fresh ore's to find. On a map whose fields are not known no field is
served, and the room does not hold anything back.

A share keeps a field from taking more harvesters than its refineries unload,
but harvesters already at work stay where they are: so at most once every
`harvester_rebalance_seconds`, one harvester of a field working more than
`harvester_rebalance_excess` over its share is moved to the served field, not
dry, with the most room (`FieldRoom.rebalance`), each move written as a
`harvesters_rebalanced` event.
"""

from __future__ import annotations

import functools
import math
from collections import Counter
from typing import Iterable

from ucla_agent.core import records
from ucla_agent.games.redalert import refinery_fields
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.family import HARVESTER, REFINERY
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.terrain import OrePatch
from ucla_agent.games.redalert.tuning import UpkeepTuning
from ucla_agent.games.redalert.world import Cell, Unit, World

# A harvester sent to a field has arrived once it stands this close to one of its cells.
_ARRIVED_CELLS = 3
HARVESTERS_REBALANCED_EVENT = "harvesters_rebalanced"


def harvester_target(per_refinery: int, ctx: MatchContext) -> int:
    """The most harvesters wanted: `harvesters_max`, or fewer with fewer refineries, `per_refinery` each."""
    return min(ctx.tuning.upkeep.harvesters_max, per_refinery * ctx.production.standing(REFINERY))


def harvesters_counted(ctx: MatchContext) -> int:
    """The harvesters counted against the target: those standing and those on their way."""
    return ctx.production.units(HARVESTER) + ctx.production.pending(HARVESTER)


def field_share(field: OrePatch, serving: int, tuning: UpkeepTuning) -> int:
    """How many harvesters a served field takes: `field_share_per_refinery` for each of the `serving` refineries, no
    more than one for each `harvester_cells_per` of its ore cells, and one at the least."""
    by_refineries = tuning.field_share_per_refinery * serving
    return max(1, min(by_refineries, field.size // tuning.harvester_cells_per))


class FieldRoom:
    """The room of each served field in the current cycle, worked out once a cycle when first asked for and taken
    from as harvesters are sent; the fields new harvesters are sent to, and the harvesters moved off a field over its
    share. See the module's note."""

    def __init__(self) -> None:
        # The ids of the harvesters seen in the last cycle; None before the first.
        self._seen: frozenset[int] | None = None
        # Harvester id -> the field it was sent to, until it arrives there.
        self._sent: dict[int, OrePatch] = {}
        self._rooms: dict[OrePatch, int] | None = None
        self._tick = -1
        # The tick a harvester was last moved off a field over its share; None before one was.
        self._rebalanced_at: int | None = None

    def note_cycle(self, harvesters: list[Unit], world: World) -> frozenset[int]:
        """Start a cycle: forget the rooms of the last and the harvesters that are gone or have arrived; the ids of
        the harvesters that are new since the last cycle (none in the first)."""
        ids = frozenset(unit.id for unit in harvesters)
        new = frozenset() if self._seen is None else ids - self._seen
        self._seen, self._rooms, self._tick = ids, None, world.tick
        by_id = {unit.id: unit for unit in harvesters}
        self._sent = {
            harvester: field
            for harvester, field in self._sent.items()
            if harvester in by_id and not _has_arrived(by_id[harvester], field)
        }
        return new

    def rooms(self, harvesters: list[Unit], world: World, ctx: MatchContext) -> dict[OrePatch, int]:
        """The room of each served field in this cycle, less what was taken from it since it was worked out."""
        if self._rooms is None or self._tick != world.tick:
            self._rooms, self._tick = self._worked_out(harvesters, world, ctx), world.tick
        return self._rooms

    def take(self, field: OrePatch) -> None:
        """Count one more harvester sent to a field in this cycle."""
        if self._rooms is not None and field in self._rooms:
            self._rooms[field] -= 1

    def pin(self, moves: Iterable[Intent], ctx: MatchContext) -> None:
        """Count each harvester a `harvest` order sends to an ore cell at that cell's field until it arrives there,
        as one the room sent: the move to fresh ore's, which the rebalance then leaves alone."""
        for intent in moves:
            cell = tuple(intent.order["to"])
            field = next((field for field in ctx.terrain.ore_patches if cell in field.cells), None)
            if field is not None:
                self._sent.update(dict.fromkeys(intent.order["units"], field))

    def has_room(self, harvesters: list[Unit], world: World, ctx: MatchContext) -> bool:
        """Whether the served fields that are not dry have room for one more harvester besides those on their way;
        always with the room switched off or no served field known."""
        if ctx.tuning.upkeep.harvester_cells_per == 0:
            return True
        rooms = self.rooms(harvesters, world, ctx)
        if not rooms:
            return True
        return sum(max(0, rooms[field]) for field in _open(rooms, ctx)) > ctx.production.pending(HARVESTER)

    def send_new(
        self, harvesters: list[Unit], new: frozenset[int], excluded: frozenset[int], world: World, ctx: MatchContext
    ) -> list[Intent]:
        """With the room switched on, `harvest` with a `to` cell for each new harvester, but those `excluded`: the
        cell of the field, not dry, with the most room nearest it. None for a harvester while no field has room."""
        to_send = [unit for unit in harvesters if unit.id in new and unit.id not in excluded]
        if ctx.tuning.upkeep.harvester_cells_per == 0 or not to_send:
            return []
        # The new ones are counted where they are sent, not by the war factory or refinery they came out of.
        rooms = self.rooms([unit for unit in harvesters if unit.id not in new], world, ctx)
        by_cell: dict[Cell, list[int]] = {}
        for unit in to_send:
            here = (unit.x, unit.y)
            field = roomiest(rooms, here, _open(rooms, ctx))
            if field is None:
                continue
            self.take(field)
            self._sent[unit.id] = field
            by_cell.setdefault(field.nearest_cell(here), []).append(unit.id)
        return [
            Intent(Priority.PRODUCTION, {"cmd": "harvest", "units": units, "to": list(cell)})
            for cell, units in by_cell.items()
        ]

    def rebalance(
        self, harvesters: list[Unit], excluded: frozenset[int], world: World, ctx: MatchContext
    ) -> list[Intent]:
        """With the room switched on and no harvester moved within `harvester_rebalance_seconds`, `harvest` with a
        `to` cell for one harvester of the field furthest over its share by more than `harvester_rebalance_excess`:
        the one nearest the field, not dry, with the most room, sent to that field's ore cell nearest it and counted
        there until it arrives, with a `harvesters_rebalanced` event. The `excluded` harvesters and those on their way
        to a field are never moved; nothing while no field is over its share or none has room."""
        tuning = ctx.tuning.upkeep
        if tuning.harvester_cells_per == 0 or self._is_cooling_down(world.tick, tuning):
            return []
        rooms = self.rooms(harvesters, world, ctx)
        fields = list(rooms)
        movable = [unit for unit in harvesters if unit.id not in excluded and unit.id not in self._sent]
        # The field furthest over its share first: the least room.
        over = sorted((field for field in fields if -rooms[field] > tuning.harvester_rebalance_excess), key=rooms.get)
        for source in over:
            target = roomiest(rooms, source.center, [field for field in _open(rooms, ctx) if field != source])
            working = [unit for unit in movable if self._field_of(unit, fields) == source]
            if target is None or not working:
                continue
            unit = min(working, key=lambda harvester: _distance_to(target, (harvester.x, harvester.y)))
            return [self._moved(unit, source, target, world)]
        return []

    def _is_cooling_down(self, tick: int, tuning: UpkeepTuning) -> bool:
        """Whether a harvester was moved off a field over its share within `harvester_rebalance_seconds`."""
        cooldown = tuning.harvester_rebalance_seconds * TICKS_PER_SECOND
        return self._rebalanced_at is not None and tick - self._rebalanced_at < cooldown

    def _moved(self, unit: Unit, source: OrePatch, target: OrePatch, world: World) -> Intent:
        """The order that moves a harvester from a field over its share to the target field, counted there from now
        on, with its `harvesters_rebalanced` event."""
        self.take(target)
        if self._rooms is not None:
            self._rooms[source] += 1
        self._sent[unit.id] = target
        self._rebalanced_at = world.tick
        cell = target.nearest_cell((unit.x, unit.y))
        data = {
            "tick": world.tick,
            "harvester": unit.id,
            "from_field_center": list(source.center),
            "to_field_center": list(target.center),
        }
        records.log(HARVESTERS_REBALANCED_EVENT, data)
        return Intent(Priority.PRODUCTION, {"cmd": "harvest", "units": [unit.id], "to": list(cell)})

    def _worked_out(self, harvesters: list[Unit], world: World, ctx: MatchContext) -> dict[OrePatch, int]:
        """The room of each served field: its share of harvesters (`field_share`) less those working it."""
        serving = serving_counts(world, ctx)
        if not serving:
            return {}
        fields = list(serving)
        working = Counter(self._field_of(unit, fields) for unit in harvesters)
        tuning = ctx.tuning.upkeep
        return {field: field_share(field, serving[field], tuning) - working[field] for field in fields}

    def _field_of(self, unit: Unit, fields: list[OrePatch]) -> OrePatch:
        """The served field a harvester works: the one it was sent to until it arrives, else the nearest."""
        sent = self._sent.get(unit.id)
        if sent is not None and sent in fields:
            return sent
        here = (unit.x, unit.y)
        return min(fields, key=lambda field: _distance_to(field, here))


def serving_counts(world: World, ctx: MatchContext) -> Counter[OrePatch]:
    """How many own refineries serve each field one serves (`refinery_fields.served_field`), as their harvesters work
    them, the fields in the order of their first refinery."""
    reach = ctx.tuning.placement.refinery_serves_cells
    fields = (
        refinery_fields.served_field(building, ctx.terrain, reach)
        for building in world.buildings.values()
        if building.type == REFINERY
    )
    return Counter(field for field in fields if field is not None)


def served_fields(world: World, ctx: MatchContext) -> list[OrePatch]:
    """The fields the own refineries serve (`serving_counts`)."""
    return list(serving_counts(world, ctx))


def roomiest(rooms: dict[OrePatch, int], here: Cell, among: Iterable[OrePatch] | None = None) -> OrePatch | None:
    """The field with the most room of those given, or of all, the nearest to `here` on a tie; None when none has
    room."""
    candidates = [field for field in (rooms if among is None else among) if rooms.get(field, 0) > 0]
    return min(candidates, key=lambda field: (-rooms[field], _distance_to(field, here)), default=None)


def _open(rooms: dict[OrePatch, int], ctx: MatchContext) -> list[OrePatch]:
    """The served fields harvesters may be sent to: those not marked dry (`ctx.dry_fields`)."""
    return [field for field in rooms if field not in ctx.dry_fields]


def _distance_to(field: OrePatch, here: Cell) -> float:
    """How far a cell lies from the box around a field's cells, in cells: 0 inside it."""
    left, top, right, bottom = _bounds(field)
    return math.hypot(max(left - here[0], 0, here[0] - right), max(top - here[1], 0, here[1] - bottom))


@functools.lru_cache(maxsize=256)
def _bounds(field: OrePatch) -> tuple[int, int, int, int]:
    """The leftmost, topmost, rightmost and bottommost cell coordinates of a field: a map's fields never change, so
    they are worked out once each."""
    xs = [x for x, _ in field.cells]
    ys = [y for _, y in field.cells]
    return min(xs), min(ys), max(xs), max(ys)


def _has_arrived(unit: Unit, field: OrePatch) -> bool:
    """Whether a harvester stands within `_ARRIVED_CELLS` of the box around a field's cells."""
    return _distance_to(field, (unit.x, unit.y)) <= _ARRIVED_CELLS
