"""The ground the enemy's defenses hold: the cells a scout or a raid keeps out of.

A remembered enemy defense covers every cell within its weapon range plus a
margin. The margin is for safety: a unit that stops at the edge of a range
has already been shot at on its way there. The range is the item table's;
for a defense the table gives no range for, a range that errs on the far side
is taken instead.

These are the distances of avoidance. The estimate of a fight counts a
defense from a shorter distance, its range and one cell, because that is
where the defense can shoot; that rule is the combat manager's and not this.
"""

from __future__ import annotations

from typing import Iterable

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.items import ItemTable
from ucla_agent.games.redalert.tuning import CoverageTuning
from ucla_agent.games.redalert.world import Cell, EnemyRecord


def reach(defense_type: str, items: ItemTable, tuning: CoverageTuning) -> float:
    """How far a defense of a type covers, in cells: its weapon range (or the one taken when unknown) and the margin."""
    facts = items.get(defense_type)
    weapon_range = facts.range if facts is not None and facts.range is not None else tuning.unknown_range_cells
    return weapon_range + tuning.margin_cells


def is_covered(cell: Cell, defenses: Iterable[EnemyRecord], items: ItemTable, tuning: CoverageTuning) -> bool:
    """Whether any of the remembered defenses given covers a cell."""
    return any(
        cells.is_within(cell, (defense.x, defense.y), reach(defense.type, items, tuning)) for defense in defenses
    )
