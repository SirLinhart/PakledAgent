"""Our own ore fields, which no scout is sent to (`[scouting] own_field_cells`).

A field with one of our buildings by it, a refinery, a second yard or any
other, is watched by that building: a scout sent there sees nothing our base
does not, and leaves the fields that matter unseen. So an ore field is ours
when one of our buildings stands within own_field_cells of the field's
center or of its nearest ore cell, and no scout mission, rescout, expansion
watch, scout place or commander scout option goes to it: the scouting rules
leave it out as a field just seen. Fields by the enemy's start are watched
as before (see `scout_missions`).
"""

from __future__ import annotations

from typing import Iterable

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.terrain import OrePatch
from ucla_agent.games.redalert.world import Building, World


def not_own(patches: Iterable[OrePatch], world: World, reach: int) -> list[OrePatch]:
    """The fields of `patches` that are not ours: no own building within `reach` cells of their center or ore."""
    buildings = list(world.buildings.values())
    return [patch for patch in patches if not _is_own(patch, buildings, reach)]


def is_own(patch: OrePatch, world: World, reach: int) -> bool:
    """Whether an own building stands within `reach` cells of a field's center or of its nearest ore cell."""
    return _is_own(patch, list(world.buildings.values()), reach)


def _is_own(patch: OrePatch, buildings: list[Building], reach: int) -> bool:
    """Whether one of `buildings` stands within `reach` of a field's center or ore; the ore cells are looked at only
    for a building that could be that close, within `reach` and the field's extent of its center."""
    extent = max((cells.distance(ore, patch.center) for ore in patch.cells), default=0.0)
    near = [(building.x, building.y) for building in buildings]
    near = [cell for cell in near if cells.is_within(cell, patch.center, reach + extent)]
    return any(_is_by(patch, cell, reach) for cell in near)


def _is_by(patch: OrePatch, cell: tuple[int, int], reach: int) -> bool:
    """Whether a cell lies within `reach` of a field's center or of one of its ore cells."""
    if cells.is_within(cell, patch.center, reach):
        return True
    return any(cells.is_within(cell, ore, reach) for ore in patch.cells)
