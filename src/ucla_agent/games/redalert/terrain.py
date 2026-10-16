"""What the map looked like before the match began: where the ore lies and where nothing can be built.

The platform's game reference object carries a text picture of each map, one
character per cell. Two things in it matter to where a base is laid out: the
ore, which a refinery should stand next to, and the ground no building can
stand on. Both are read from the picture once, when a match starts, and kept
for the match.

The picture shows the start of the match. Ore is harvested and grows back, so
a patch is a place where ore was and is likely to be, not a count of what is
left. A reference object without a picture gives a terrain that knows
nothing, and a base is then laid out by distance alone.

A building can stand only on a cell whose character is one of the buildable
characters the tuning file lists. Every other cell is blocked, whatever the
platform's legend calls its character, so a structure, a wall, a bridge or a
character no legend explains is never offered as a place to build. Ore is
blocked even when its character is listed as buildable. A picture made
mostly of characters the agent does not know, neither buildable nor water,
rock, trees, rough ground or ore, would leave little to build on, and the
terrain says so with an error for the agent to report.

A unit can walk every cell but water, rock or cliff and trees. Every other
character, ore, rough ground, a bridge, a beach and a character the agent does
not know included, counts as passable, and so does a cell off the picture.
How far a building stands from a field's ore by walking (`walk_distances`) is
the fewest steps, a step to any of the eight cells around, from an ore cell of
the field over passable cells: a refinery or a yard 2 cells from the ore in a
straight line may be 11 cells away by walking round a cliff.

Nothing here reads a file: the picture is in the object the runtime hands the
agent with its first state.
"""

from __future__ import annotations

import dataclasses
import functools
from collections import Counter, deque
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Collection, Iterable, Mapping

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.tuning import TerrainTuning
from ucla_agent.games.redalert.world import Cell

# The picture's characters for ore, gems and ore mines, as the platform's legend gives them, and the ore mine's alone.
ORE_CHARACTERS = frozenset("$*M")
MINE_CHARACTERS = frozenset("M")
# Water, rock, trees and rough ground: with the ore, the ground known to be one a building cannot be placed on.
BLOCKED_CHARACTERS = ORE_CHARACTERS | frozenset("~^T,")
# Water, rock or cliff, and trees: the ground no unit can walk on.
IMPASSABLE_CHARACTERS = frozenset("~^T")
# The characters a building can stand on when none are given: those of the tuning file's built-in values.
BUILDABLE_CHARACTERS = TerrainTuning().buildable_characters
# A cell and the eight around it: ore cells that touch, also at a corner, belong to one patch.
_NEIGHBOURS = tuple((dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dx, dy) != (0, 0))
# The decimal places a share of the picture is reported with.
_SHARE_DECIMALS = 3
# How many walk-distance maps, each of one field to one depth, are kept: more than the fields of any map at the few
# depths asked for.
_WALK_MAPS_KEPT = 256


class TerrainUnknownCharacters(Exception):
    """Characters the agent does not know cover more of a map picture than the tuning file allows.

    It is never raised: the agent logs it as a handled error when the match
    starts and plays on with those cells blocked.
    """


@dataclass(frozen=True)
class OrePatch:
    """One connected field of ore, gems and mines."""

    # The middle of the field's cells; the cell itself need not hold ore.
    center: Cell
    # The cells of the field at the start of the match: where a refinery has to stand near for its harvesters'
    # sake, whatever has been harvested from them since.
    cells: frozenset[Cell]
    # How many ore mines the field holds: groups of mine cells that touch, also at a corner, read from the picture. A
    # mine grows the ore back, so it is what a field's harvesters are counted by. It says nothing of which field a
    # patch is, so two patches of the same cells are the same field whatever their mines.
    mines: int = dataclasses.field(default=0, compare=False)

    @property
    def size(self) -> int:
        """How many cells the field had at the start of the match."""
        return len(self.cells)

    def nearest_cell(self, point: Cell) -> Cell:
        """The cell of the field nearest a point; of several as near, the upper, then the left one."""
        return min(self.cells, key=lambda cell: (cells.distance(cell, point), cell[1], cell[0]))


class Terrain:
    """The ore patches of a map, the cells nothing can be built on and the unknown characters of its picture."""

    def __init__(
        self,
        ore_patches: Iterable[OrePatch] = (),
        blocked: Iterable[Cell] = (),
        unknown_cells: Mapping[str, int] | None = None,
        cell_count: int = 0,
        impassable: Iterable[Cell] = (),
    ) -> None:
        # In the order their first cell appears in the picture, row by row.
        self.ore_patches = tuple(ore_patches)
        self.blocked = frozenset(blocked)
        # The cells no unit can walk on: water, rock or cliff, and trees.
        self.impassable = frozenset(impassable)
        # How many cells show each character of the picture that is neither buildable nor known to be blocked ...
        self.unknown_cells = dict(unknown_cells or {})
        # ... out of how many cells the picture has.
        self.cell_count = cell_count

    @classmethod
    def from_game_config(
        cls, game_config: Any, map_id: Any, buildable_characters: str = BUILDABLE_CHARACTERS
    ) -> Terrain:
        """The terrain the game reference object pictures for a map; an empty one when it pictures none.

        A cell is blocked unless its character is one of `buildable_characters`
        and not ore.
        """
        maps = _mapping(_mapping(_mapping(game_config).get("rules")).get("maps"))
        layout = _mapping(_mapping(maps.get(map_id)).get("layout"))
        rows, origin = layout.get("rows"), layout.get("origin")
        if not isinstance(rows, list) or not isinstance(origin, (list, tuple)) or len(origin) != 2:
            return cls()
        ore = _cells_showing(rows, origin, ORE_CHARACTERS)
        mines = _patches(_cells_showing(rows, origin, MINE_CHARACTERS))
        patches = [_with_mines(patch, mines) for patch in _patches(ore)]
        shown = Counter("".join(str(row) for row in rows))
        blocking = frozenset(shown) - (frozenset(buildable_characters) - ORE_CHARACTERS)
        unknown = {character: shown[character] for character in sorted(blocking - BLOCKED_CHARACTERS)}
        impassable = _cells_showing(rows, origin, IMPASSABLE_CHARACTERS)
        return cls(patches, _cells_showing(rows, origin, blocking), unknown, sum(shown.values()), impassable)

    def unknown_characters_error(self, warn_share: float) -> TerrainUnknownCharacters | None:
        """The error to report when the unknown characters together cover more than `warn_share` of the picture.

        None when they cover that share or less, or when the terrain was read from no picture.
        """
        if not self.unknown_cells:
            return None
        # One division of whole numbers: a share exactly at `warn_share` compares equal to it.
        share = sum(self.unknown_cells.values()) / self.cell_count
        if share <= warn_share:
            return None
        listed = ", ".join(
            f"{character!r} {count / self.cell_count:.{_SHARE_DECIMALS}f}"
            for character, count in self.unknown_cells.items()
        )
        return TerrainUnknownCharacters(
            f"characters the agent does not know cover {share:.{_SHARE_DECIMALS}f} of the map picture, more than "
            f"{warn_share}, and their cells are blocked: {listed}"
        )

    def nearest_patch(self, cell: Cell, among: Iterable[OrePatch] | None = None) -> OrePatch | None:
        """The patch whose center is closest to a cell, of those given or of all; None when there is none."""
        candidates = self.ore_patches if among is None else among
        return min(candidates, key=lambda patch: cells.distance(patch.center, cell), default=None)

    def walk_distances(self, patch: OrePatch, depth: int) -> Mapping[Cell, int]:
        """How many steps each cell within `depth` steps of a field's ore lies from it by walking: a step to any of the
        eight cells around, over passable cells only, 0 on an ore cell. A cell farther or cut off is not in it."""
        return _walk_distances(self.impassable, patch, depth)

    def walk_zone(self, patch: OrePatch, reach: int) -> frozenset[Cell]:
        """The cells within `reach` steps of a field's ore by walking (`walk_distances`)."""
        return _walk_zone(self.impassable, patch, reach)

    def walked_distance(self, footprint: Collection[Cell], patch: OrePatch, depth: int) -> int | None:
        """The fewest steps by walking from a field's ore to a cell of a footprint (`walk_distances`); None when no cell
        of it lies within `depth` steps, as when a cliff keeps it from the ore."""
        distances = self.walk_distances(patch, depth)
        return min((distances[cell] for cell in footprint if cell in distances), default=None)

    def walk_steps(self, start: Cell, end: Cell, max_steps: int) -> int | None:
        """The fewest steps from `start` to `end` by walking: a step to any of the eight cells around, over passable
        cells only, `end` itself taken as reached whatever it is; None when it is not reached within `max_steps`, so
        that no search floods farther than that."""
        if not self.impassable:
            # On open ground the fewest steps of eight directions is the larger of the two spans.
            steps = max(abs(end[0] - start[0]), abs(end[1] - start[1]))
            return steps if steps <= max_steps else None
        if start == end:
            return 0
        steps_to = {start: 0}
        frontier = deque([start])
        while frontier:
            x, y = frontier.popleft()
            steps = steps_to[(x, y)] + 1
            for dx, dy in _NEIGHBOURS:
                cell = (x + dx, y + dy)
                if cell == end:
                    return steps
                # A cell from which `end` lies farther than the steps left is no use: the search keeps to the cells
                # that can still be on a walk within the cap, never the whole map.
                is_hopeless = steps + max(abs(end[0] - cell[0]), abs(end[1] - cell[1])) > max_steps
                if cell not in steps_to and cell not in self.impassable and not is_hopeless:
                    steps_to[cell] = steps
                    frontier.append(cell)
        return None


@functools.lru_cache(maxsize=_WALK_MAPS_KEPT)
def _walk_distances(impassable: frozenset[Cell], patch: OrePatch, depth: int) -> Mapping[Cell, int]:
    """The walk distance of each cell within `depth` steps of a field's ore, by a search outward from its ore cells
    over the cells not `impassable`, worked out once for each field and depth."""
    distances = dict.fromkeys(patch.cells, 0)
    frontier = deque(patch.cells)
    while frontier:
        x, y = frontier.popleft()
        steps = distances[(x, y)] + 1
        if steps > depth:
            continue
        for dx, dy in _NEIGHBOURS:
            cell = (x + dx, y + dy)
            if cell not in distances and cell not in impassable:
                distances[cell] = steps
                frontier.append(cell)
    return MappingProxyType(distances)


@functools.lru_cache(maxsize=_WALK_MAPS_KEPT)
def _walk_zone(impassable: frozenset[Cell], patch: OrePatch, reach: int) -> frozenset[Cell]:
    """The cells within `reach` steps of a field's ore by walking, worked out once for each field and reach."""
    return frozenset(_walk_distances(impassable, patch, reach))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _cells_showing(rows: list[Any], origin: Any, characters: frozenset[str]) -> list[Cell]:
    """The cells whose character in the picture is one of the given, row by row."""
    left, top = int(origin[0]), int(origin[1])
    return [
        (left + column, top + line)
        for line, row in enumerate(rows)
        for column, character in enumerate(str(row))
        if character in characters
    ]


def _patches(ore: list[Cell]) -> list[OrePatch]:
    """The ore cells sorted into fields of cells that touch."""
    unsorted = set(ore)
    patches = []
    for cell in ore:
        if cell in unsorted:
            patches.append(_patch_of(_take_field(cell, unsorted)))
    return patches


def _take_field(start: Cell, unsorted: set[Cell]) -> list[Cell]:
    """Take the cells connected to `start` out of the unsorted ones and return them."""
    unsorted.discard(start)
    field, frontier = [start], [start]
    while frontier:
        x, y = frontier.pop()
        touching = [(x + dx, y + dy) for dx, dy in _NEIGHBOURS if (x + dx, y + dy) in unsorted]
        unsorted.difference_update(touching)
        field += touching
        frontier += touching
    return field


def _with_mines(patch: OrePatch, mines: list[OrePatch]) -> OrePatch:
    """The patch with the count of the mines given that lie in it: a mine's cells are ore, so each lies in one patch."""
    held = sum(1 for mine in mines if not mine.cells.isdisjoint(patch.cells))
    return dataclasses.replace(patch, mines=held)


def _patch_of(field: list[Cell]) -> OrePatch:
    middle_x = sum(x for x, _ in field) / len(field)
    middle_y = sum(y for _, y in field) / len(field)
    return OrePatch(center=cells.nearest_cell(middle_x, middle_y), cells=frozenset(field))
