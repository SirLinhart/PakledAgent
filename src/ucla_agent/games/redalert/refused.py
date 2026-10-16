"""The places the engine would not put a building, noticed from where the building shows up instead.

The platform puts a finished building on the cell it is asked for when that
cell can take it; when it cannot, it puts the building elsewhere near the
construction yard, and no refusal comes back. A cell the map picture shows
free can still be refused: one behind a cliff the picture does not show, for
example. Placement, asked again, would choose the same cell, and a base that
walks toward an ore field would stop growing at the cliff. So with
`refused_detection` of the tuning file's [placement] switched on, the upkeep
placement rule hands every place it asks a building onto to `RefusedPlaces`,
which compares it with where the building shows up.

A building of the same item that shows up within `promise_ticks` of the
asking, more than `refused_miss_cells` from the cell it was asked onto,
counted as a king moves, is a miss. The place it was asked onto is then kept
off for every building from now on (see `placement`), the last
`refused_spots_max` such places at most, and the miss is written to the match
record as one `placement_refused` event: the item, the cell asked for, the
cell it stands on, how far apart they are, the center of the far field the
building was placed toward along the line, if it was, and whether that field
is skipped.

A field the engine keeps refusing places toward lies where the base cannot
grow, behind the cliff. Each miss of a building placed along the line toward
the far field counts against that field, and after `refused_misses_to_skip`
misses in a row the field is skipped: it joins the match's skipped fields,
which the far field passes over with `creep_continuous` on and the second
yard goes to with `mcv_to_skipped_fields` on. A building placed toward the
field that shows up where it was asked starts the count afresh.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass

from ucla_agent.core import records
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.placement import Box, RefusedPlace
from ucla_agent.games.redalert.world import Building, Cell, World

PLACEMENT_REFUSED_EVENT = "placement_refused"


@dataclass(frozen=True)
class _Asked:
    """The place a building of an item was last asked onto."""

    tick: int
    box: Box
    # The center of the far field the building was placed toward along the line; None for any other placement.
    toward: Cell | None


class RefusedPlaces:
    """Notices the buildings the engine put elsewhere than asked, and keeps the places it refused. See the module's
    note."""

    def __init__(self) -> None:
        # Item -> the place a building of it was last asked onto, until one shows up.
        self._asked: dict[str, _Asked] = {}
        # The ids of the own buildings at the last look; None before the first.
        self._seen: frozenset[int] | None = None
        # The places refused, the oldest first, and the tick each was last refused at.
        self._refused: deque[Box] = deque()
        self._refused_at: dict[Box, int] = {}
        # The center of a far field -> how many buildings placed toward it in a row the engine put elsewhere.
        self._misses: Counter[Cell] = Counter()

    @property
    def boxes(self) -> tuple[Box, ...]:
        """The places refused that are kept off, the oldest first."""
        return tuple(self._refused)

    @property
    def places(self) -> tuple[RefusedPlace, ...]:
        """The places refused that are kept off, each with the tick it was last refused at, the oldest first."""
        return tuple(RefusedPlace(box, self._refused_at[box]) for box in self._refused)

    def asked(self, item: str, box: Box | None, tick: int, toward: Cell | None) -> None:
        """Note the place a building of an item is asked onto at a tick, and the far field's center when it is placed
        toward one; a building asked onto no place in particular has nothing to be compared with."""
        if box is None:
            self._asked.pop(item, None)
        else:
            self._asked[item] = _Asked(tick, box, toward)

    def notice(self, world: World, ctx: MatchContext) -> None:
        """Compare each own building that showed up since the last look with the place its item was last asked onto
        within `promise_ticks`, and keep the place of each miss."""
        seen = self._seen
        self._seen = frozenset(world.buildings)
        if seen is None:
            return
        for building in world.buildings.values():
            asked = self._asked.pop(building.type, None) if building.id not in seen else None
            if asked is not None and world.tick - asked.tick <= ctx.tuning.placement.promise_ticks:
                self._compare(building, asked, world, ctx)

    def _compare(self, building: Building, asked: _Asked, world: World, ctx: MatchContext) -> None:
        """Keep the place a building was asked onto, count the miss against its far field and write it, when it
        stands too far from it; start the far field's count afresh when it stands where it was asked."""
        tuning = ctx.tuning.placement
        distance = _cells_apart((building.x, building.y), (asked.box.left, asked.box.top))
        if distance <= tuning.refused_miss_cells:
            self._misses.pop(asked.toward, None)
            return
        self._refused.append(asked.box)
        self._refused_at[asked.box] = world.tick
        while len(self._refused) > tuning.refused_spots_max:
            dropped = self._refused.popleft()
            if dropped not in self._refused:
                del self._refused_at[dropped]
        _log_miss(building, asked, distance, self._count_miss(asked.toward, ctx), world)

    def _count_miss(self, toward: Cell | None, ctx: MatchContext) -> bool:
        """Count a miss against the far field a building was placed toward, skipping the field once its misses in a
        row reach `refused_misses_to_skip`; whether the field is skipped, False for a building placed toward none."""
        if toward is None:
            return False
        self._misses[toward] += 1
        field = next(patch for patch in ctx.terrain.ore_patches if patch.center == toward)
        if self._misses[toward] >= ctx.tuning.placement.refused_misses_to_skip and field not in ctx.skipped_fields:
            ctx.skipped_fields.append(field)
        return field in ctx.skipped_fields


def _cells_apart(cell: Cell, other: Cell) -> int:
    """How many cells lie between two cells, counted as a king moves."""
    return max(abs(cell[0] - other[0]), abs(cell[1] - other[1]))


def _log_miss(building: Building, asked: _Asked, distance: int, skipped: bool, world: World) -> None:
    """Write one `placement_refused` event."""
    records.log(
        PLACEMENT_REFUSED_EVENT,
        {
            "tick": world.tick,
            "item": building.type,
            "asked_cell": [asked.box.left, asked.box.top],
            "actual_cell": [building.x, building.y],
            "distance_cells": distance,
            "target_patch_center": None if asked.toward is None else list(asked.toward),
            "skipped": skipped,
        },
    )
