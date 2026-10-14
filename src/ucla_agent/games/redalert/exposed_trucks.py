"""Exposed trucks: enemy harvesters at work far from the enemy's start, with little guard and no defense over them.

Harvesters working a far ore field are the enemy's income where it is least
protected: a few fast units can take them before help comes, while the same
harvesters by the enemy's yard work among its army and its defenses. Our units
see such harvesters as they go, the scouts above all; with `[scouting]
exposed_trucks` on, the scouting manager notes them as raid opportunities.

The enemy harvesters in sight are grouped by the ore field they stand on or
within FIELD_REACH_CELLS of, the nearest such field: a harvester waits and
turns at the edge of the field it works. A field gives an opportunity when all
of these hold:

- its center lies more than truck_field_min_cells from the enemy's start;
- the enemy units that fight within truck_guard_cells of a cell of the field,
  as remembered and not stale, are worth at most truck_guard_max_value;
- no remembered enemy defense covers its center (see `coverage`).

An opportunity (`ExposedTrucks`) says where the field is (its center), how many
harvesters were in sight there and which, what the guard was worth, the tick
they were seen and where the harvester nearest our base stood: the one a raid
from our side reaches first. Nothing is noted while the enemy's start or our
base is not known.

The watch (`ExposedTruckWatch`) keeps the opportunities from cycle to cycle,
one for each field. An opportunity is kept until truck_seen_seconds after its
harvesters were last seen exposed; seeing them so again renews it with what is
seen then. It is dropped at once when a remembered defense comes to cover the
field or the guard by it grows past truck_guard_max_value, so that none is ever
offered under a known defense. The opportunities kept are given the newest
first.

Each new opportunity, one for a field no opportunity is kept for, is written
to the match record as a `scout_slot` event of kind `exposed_trucks`, with the
field, the harvesters' count, the guard's worth, the tick and the nearest
harvester's cell. A field seen again while its opportunity is kept writes
nothing.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, coverage, raid_targets
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.scout_slots import SCOUT_SLOT_EVENT
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import ScoutingTuning, Tuning
from ucla_agent.games.redalert.world import Cell, EnemyRecord, World

# The kind of the `scout_slot` event of a new opportunity.
EXPOSED_TRUCKS = "exposed_trucks"
# A harvester counts for an ore field it stands on or is this close to.
FIELD_REACH_CELLS = 3
# How many ore fields' extents are kept: every field of a few maps.
_EXTENTS_KEPT = 256


@dataclass(frozen=True)
class ExposedTrucks:
    """One raid opportunity: enemy harvesters seen at an ore field far from the enemy's start, with little guard and
    under no remembered defense."""

    # The center of the ore field.
    field: Cell
    # How many enemy harvesters were in sight there, and their ids, the lowest first.
    harvester_count: int
    harvester_ids: tuple[int, ...]
    # What the enemy units that fight within truck_guard_cells of the field were worth.
    guard_value: int
    # The tick the harvesters were in sight there.
    seen_tick: int
    # Where the harvester nearest our base stood.
    nearest_harvester: Cell


class ExposedTruckWatch:
    """The raid opportunities seen and still fresh, one for each ore field, kept from cycle to cycle. See the module's
    note."""

    def __init__(self) -> None:
        # The opportunities kept, by their ore field.
        self._kept: dict[OrePatch, ExposedTrucks] = {}

    def observe(self, world: World, terrain: Terrain, tuning: Tuning) -> tuple[ExposedTrucks, ...]:
        """Take in the cycle's world: drop the opportunities gone stale or no longer exposed, then take in those sighted
        now, a field seen again renewed; the opportunities kept, the newest first."""
        self._kept = {patch: kept for patch, kept in self._kept.items() if _is_kept(patch, kept, world, tuning)}
        found = sighted(world, terrain, tuning)
        for patch, opportunity in found.items():
            if patch not in self._kept:
                _log_new(opportunity)
        self._kept.update(found)
        return tuple(sorted(self._kept.values(), key=lambda kept: (-kept.seen_tick, kept.field)))


def sighted(world: World, terrain: Terrain, tuning: Tuning) -> dict[OrePatch, ExposedTrucks]:
    """The opportunities of this cycle, by their ore field: one for each field whose enemy harvesters in sight are
    exposed now; none while the enemy's start or our base is not known."""
    if world.enemy_base is None or world.base_center is None:
        return {}
    found = {
        patch: _opportunity(patch, harvesters, world, tuning)
        for patch, harvesters in _harvesters_by_field(world, terrain).items()
    }
    return {patch: opportunity for patch, opportunity in found.items() if opportunity is not None}


def _is_kept(patch: OrePatch, kept: ExposedTrucks, world: World, tuning: Tuning) -> bool:
    """Whether an opportunity is kept on: its harvesters seen within truck_seen_seconds, and exposed still with the
    guard about the field now."""
    if world.tick - kept.seen_tick >= tuning.scouting.truck_seen_seconds * TICKS_PER_SECOND:
        return False
    return _is_exposed(patch, _guard_value(patch, world, tuning.scouting), world, tuning)


def _harvesters_by_field(world: World, terrain: Terrain) -> dict[OrePatch, list[EnemyRecord]]:
    """The enemy harvesters in sight by the ore field each stands on or by; one near no field is left out."""
    by_field: dict[OrePatch, list[EnemyRecord]] = {}
    for record in world.enemy_memory.values():
        if not record.visible or raid_targets.kind(record, world) != raid_targets.HARVESTERS:
            continue
        patch = _field_of((record.x, record.y), terrain)
        if patch is not None:
            by_field.setdefault(patch, []).append(record)
    return by_field


def _opportunity(patch: OrePatch, harvesters: list[EnemyRecord], world: World, tuning: Tuning) -> ExposedTrucks | None:
    """The opportunity of a field's harvesters in sight; None when the field lies within truck_field_min_cells of the
    enemy's start or its harvesters are not exposed."""
    if cells.is_within(patch.center, world.enemy_base, tuning.scouting.truck_field_min_cells):
        return None
    guard = _guard_value(patch, world, tuning.scouting)
    if not _is_exposed(patch, guard, world, tuning):
        return None
    nearest = min(harvesters, key=lambda record: (cells.distance((record.x, record.y), world.base_center), record.id))
    return ExposedTrucks(
        field=patch.center,
        harvester_count=len(harvesters),
        harvester_ids=tuple(sorted(record.id for record in harvesters)),
        guard_value=guard,
        seen_tick=world.tick,
        nearest_harvester=(nearest.x, nearest.y),
    )


def _guard_value(patch: OrePatch, world: World, tuning: ScoutingTuning) -> int:
    """What the enemy units that fight within truck_guard_cells of an ore field are worth, as remembered and not
    stale."""
    return sum(
        _cost(record.type, world)
        for record in world.enemy_memory.values()
        if is_combat_unit(record)
        and not record.stale
        and _is_near((record.x, record.y), patch, tuning.truck_guard_cells)
    )


def _is_exposed(patch: OrePatch, guard: int, world: World, tuning: Tuning) -> bool:
    """Whether an ore field's harvesters are exposed with this guard about them: one worth at most
    truck_guard_max_value, and no remembered enemy defense covering the field's center."""
    if guard > tuning.scouting.truck_guard_max_value:
        return False
    return not coverage.is_covered(patch.center, world.known_enemy_defenses, world.items, tuning.coverage)


def _field_of(cell: Cell, terrain: Terrain) -> OrePatch | None:
    """The ore field a cell lies on or within FIELD_REACH_CELLS of, the nearest of two; None when it is near none."""
    near = [patch for patch in terrain.ore_patches if _is_near(cell, patch, FIELD_REACH_CELLS)]
    return min(near, key=lambda patch: cells.distance(cell, patch.nearest_cell(cell)), default=None)


def _is_near(cell: Cell, patch: OrePatch, reach: float) -> bool:
    """Whether a cell lies within `reach` of a cell of an ore field."""
    # The circle about the field's center that holds all its cells rules most cells out at one comparison, without a
    # look at every cell of the field.
    if not cells.is_within(cell, patch.center, _extent(patch) + reach):
        return False
    return cells.is_within(cell, patch.nearest_cell(cell), reach)


@functools.lru_cache(maxsize=_EXTENTS_KEPT)
def _extent(patch: OrePatch) -> float:
    """How far the cell of an ore field farthest from its center lies from it."""
    return max(cells.distance(patch.center, cell) for cell in patch.cells)


def _cost(item: str, world: World) -> int:
    facts = world.items.get(item)
    return facts.cost if facts is not None else 0


def _log_new(opportunity: ExposedTrucks) -> None:
    """Write the `scout_slot` event of a new opportunity: the field, the harvesters' count, the guard's worth, the tick
    and the nearest harvester's cell."""
    records.log(
        SCOUT_SLOT_EVENT,
        {
            "kind": EXPOSED_TRUCKS,
            "field": list(opportunity.field),
            "harvesters": opportunity.harvester_count,
            "guard_value": opportunity.guard_value,
            "tick": opportunity.seen_tick,
            "nearest_harvester": list(opportunity.nearest_harvester),
        },
    )
