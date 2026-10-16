"""What our own units have had in sight: the last tick each part of the map was seen.

The enemy memory says what has been seen of the enemy. Scouting also needs to
know what has been looked at and found empty, or not looked at for a while:
the enemy's base area, the ore fields out on the map. The states do not say
what the agent's units see, so sight is taken as a circle around each of our
units: with `sight_by_type` of [scouting] on, of the unit's own sight as the
item table gives it (the engine's RevealsShroud range: 4 cells for most
infantry, 5 to 7 for the vehicles), FALLBACK_SIGHT_CELLS when the table does
not say; with it off, of SIGHT_CELLS for every unit, which overstates what an
infantryman sees.

Sight is held on a coarse grid of squares 4 cells wide, not per cell: a square
counts as seen in a state when its middle lies within the sight radius of one
of our units in that state. A cell's last sighting is then that of its
square. The grid is kept for one match and only grows.
"""

from __future__ import annotations

import functools
import math

from ucla_agent.games.redalert.items import ItemTable
from ucla_agent.games.redalert.world import Cell, World

# The width of a square of the grid, in cells.
SQUARE_CELLS = 4
# How far one of our units is taken to see with sight_by_type off, in cells.
SIGHT_CELLS = 6
# How far a unit the item table gives no sight for is taken to see with it on: the engine's infantry sight.
FALLBACK_SIGHT_CELLS = 4
# The middle of a square, measured from its first cell.
_SQUARE_MIDDLE = (SQUARE_CELLS - 1) / 2

Square = tuple[int, int]


def square_of(cell: Cell) -> Square:
    """The square of the grid a cell lies in."""
    return cell[0] // SQUARE_CELLS, cell[1] // SQUARE_CELLS


def sight_of(unit_type: str, items: ItemTable, by_type: bool) -> float:
    """How far a unit of a type is taken to see, in cells: its own sight with `by_type` (FALLBACK_SIGHT_CELLS when the
    table gives none), SIGHT_CELLS without."""
    if not by_type:
        return SIGHT_CELLS
    facts = items.get(unit_type)
    return facts.sight if facts is not None and facts.sight is not None else FALLBACK_SIGHT_CELLS


@functools.lru_cache(maxsize=None)
def _seen_from(offset_x: int, offset_y: int, radius: float) -> tuple[Square, ...]:
    """The squares, relative to its own, whose middles a unit that sees `radius` cells sees from a cell this far into
    its square."""
    squares_reached = math.ceil(radius) // SQUARE_CELLS + 1
    reach = range(-squares_reached, squares_reached + 1)
    return tuple(
        (across, down)
        for down in reach
        for across in reach
        if (across * SQUARE_CELLS + _SQUARE_MIDDLE - offset_x) ** 2
        + (down * SQUARE_CELLS + _SQUARE_MIDDLE - offset_y) ** 2
        <= radius * radius
    )


@functools.lru_cache(maxsize=None)
def squares_within(cell: Cell, radius: float) -> tuple[Square, ...]:
    """The squares whose middles lie within `radius` cells of a cell."""
    first_x, first_y = square_of((math.floor(cell[0] - radius), math.floor(cell[1] - radius)))
    last_x, last_y = square_of((math.ceil(cell[0] + radius), math.ceil(cell[1] + radius)))
    return tuple(
        (square_x, square_y)
        for square_y in range(first_y, last_y + 1)
        for square_x in range(first_x, last_x + 1)
        if (square_x * SQUARE_CELLS + _SQUARE_MIDDLE - cell[0]) ** 2
        + (square_y * SQUARE_CELLS + _SQUARE_MIDDLE - cell[1]) ** 2
        <= radius * radius
    )


class SightGrid:
    """The last tick each square of the map was in our units' sight, for one match. See the module's note."""

    def __init__(self) -> None:
        self._last_seen: dict[Square, int] = {}

    def observe(self, world: World, by_type: bool = False) -> None:
        """Mark every square one of our units sees in the world's state as seen at its tick, each unit seeing as far
        as `sight_of` says."""
        tick, last_seen = world.tick, self._last_seen
        seers = {(unit.x, unit.y, sight_of(unit.type, world.items, by_type)) for unit in world.units.values()}
        for x, y, radius in seers:
            own_x, own_y = square_of((x, y))
            for across, down in _seen_from(x - own_x * SQUARE_CELLS, y - own_y * SQUARE_CELLS, radius):
                last_seen[own_x + across, own_y + down] = tick

    def last_seen(self, cell: Cell) -> int | None:
        """The last tick a cell's square was in sight; None when it never was."""
        return self._last_seen.get(square_of(cell))

    def last_seen_near(self, cell: Cell, radius: float) -> int | None:
        """The last tick any square whose middle lies within `radius` cells of a cell was in sight; None when none
        ever was."""
        last_seen = self._last_seen
        return max((last_seen[square] for square in squares_within(cell, radius) if square in last_seen), default=None)
