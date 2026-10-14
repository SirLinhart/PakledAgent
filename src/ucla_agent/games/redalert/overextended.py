"""The enemy's over-extended ore fields: a refinery or harvesters far from the enemy's start and from its yards.

An enemy that mines a field far from its base leaves its refinery and its
harvesters there out of reach of its own army and defenses. With
`strike_overextended` of `[combat]` on, such a field is a target of main's
(see `strike`), the figures those of `[combat]`:

- **At a field:** a known enemy refinery whose footprint (its reported cell
  less `refinery_fields.REPORTED_OFFSET`) lies within `FIELD_CELLS` of an ore
  cell of one of the map's fields (`terrain.OrePatch`), or an enemy harvester
  seen less than `UNSEEN_SECONDS` ago standing that near one.
- **Over-extended:** it stands more than overextended_min_enemy_start_cells
  from the enemy's start (`scout_missions.enemy_start`) and from every known
  enemy construction yard, and no known enemy ground defense
  (`fight.ground_defenses`) stands within overextended_defense_clear_cells
  of it.

The over-extended refineries and harvesters of one field make one target,
its refinery first, then its harvesters by id, while one of them was in
sight less than `UNSEEN_SECONDS` ago; the targets come the one nearest the
center of our base first. A field's actors are found again in
every cycle (`at_field`) with the defense rule left out, so that a strike on
a field already under way weighs a defense raised there instead of losing
its target.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ucla_agent.games.redalert import cells, placement
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD, HARVESTER, REFINERY
from ucla_agent.games.redalert.fight import ground_defenses
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.placement import Box
from ucla_agent.games.redalert.refinery_fields import REPORTED_OFFSET
from ucla_agent.games.redalert.scout_missions import enemy_start
from ucla_agent.games.redalert.terrain import OrePatch
from ucla_agent.games.redalert.world import Cell, EnemyRecord, World

# A refinery or harvester stands at a field within this many cells of one of its ore cells, counted as a king moves.
FIELD_CELLS = 3
# A refinery or harvester unseen this long is no longer at its field.
UNSEEN_SECONDS = 20
_HARVESTER_BOX = (1, 1)


@dataclass(frozen=True)
class FieldTarget:
    """An over-extended field: the field, and the enemy refinery and harvesters there, the refinery first."""

    field: OrePatch
    records: tuple[EnemyRecord, ...]

    @property
    def lead(self) -> EnemyRecord:
        """The actor the target is named by: its refinery, else its harvester of the lowest id."""
        return self.records[0]

    @property
    def cell(self) -> Cell:
        """Where the target stands: its lead's cell."""
        return self.lead.x, self.lead.y


def targets(world: World, ctx: MatchContext) -> list[FieldTarget]:
    """The over-extended fields (see the module's note), the nearest to the center of our base first, then by the id
    of their lead; none with strike_overextended off or while the enemy's start is not known."""
    if not ctx.tuning.combat.strike_overextended or enemy_start(world) is None:
        return []
    clear = ctx.tuning.combat.overextended_defense_clear_cells
    defenses = [(defense.x, defense.y) for defense in ground_defenses(world)]
    found = []
    for field in ctx.terrain.ore_patches:
        records = [
            record
            for record in at_field(field, world, ctx)
            if not any(cells.is_within((record.x, record.y), defense, clear) for defense in defenses)
        ]
        if any(is_seen_lately(record, world) for record in records):
            found.append(FieldTarget(field, tuple(records)))
    home = world.base_center
    return sorted(found, key=lambda target: (_distance(target.cell, home), target.lead.id))


def at_field(field: OrePatch, world: World, ctx: MatchContext) -> list[EnemyRecord]:
    """The known enemy refineries and the enemy harvesters seen less than UNSEEN_SECONDS ago at a field, more than
    overextended_min_enemy_start_cells from the enemy's start and every known enemy yard, the refineries first, then
    by id; the defense rule is not asked here. None while the enemy's start is not known."""
    start = enemy_start(world)
    if start is None:
        return []
    least = ctx.tuning.combat.overextended_min_enemy_start_cells
    homes = [
        start,
        *((record.x, record.y) for record in world.enemy_memory.values() if record.type == CONSTRUCTION_YARD),
    ]
    found = [
        record
        for record in world.enemy_memory.values()
        if (record.type == REFINERY or (record.type == HARVESTER and is_seen_lately(record, world)))
        and _stands_at(record, field)
        and all(cells.distance((record.x, record.y), home) > least for home in homes)
    ]
    return sorted(found, key=lambda record: (record.type != REFINERY, record.id))


def is_seen_lately(record: EnemyRecord, world: World) -> bool:
    """Whether an enemy was in sight less than UNSEEN_SECONDS ago."""
    return world.tick - record.last_seen_tick < UNSEEN_SECONDS * TICKS_PER_SECOND


def _stands_at(record: EnemyRecord, field: OrePatch) -> bool:
    """Whether a refinery's footprint, or a harvester's cell, lies within FIELD_CELLS of an ore cell of a field."""
    if record.type == REFINERY:
        box = placement.box_at(REFINERY, (record.x - REPORTED_OFFSET[0], record.y - REPORTED_OFFSET[1]))
    else:
        box = Box(record.x, record.y, *_HARVESTER_BOX)
    return placement.reaches(box, field, FIELD_CELLS)


def _distance(cell: Cell, home: Cell | None) -> float:
    """How far a cell is from the center of our base; 0 while it is not known."""
    return 0.0 if home is None else math.dist(cell, home)
