"""Growth toward a far field that makes no headway gives the field up.

A base that walks toward its far field (see `placement`) can stall: the
engine puts the buildings asked toward it elsewhere, or a cliff the map
picture does not show stands in the way, and the base keeps aiming at the same
field for minutes. With `creep_stall_skip` of the tuning file's [placement]
switched on, the creep keeper (see `creep`) follows the headway of each line,
the main base's and a second yard's apart.

A line's best distance is the smallest distance from the middle of any own
building but a defense to its far field's ore cell nearest the line's start.
The line makes progress when that distance shrinks by `stall_progress_cells`
or more. A building placed along the line is judged once `promise_ticks` have
passed since it was asked onto the map, by then it stands where the engine
put it: it brought progress when the best distance shrank so much since it
was asked, and a building that lands where it was asked but brings the base
no closer counts as one without. The field is given up when `stall_placements`
buildings in a row brought none, or when `stall_seconds` passed with no
progress since the field became the line's far field or last progressed.

Only build time counts: a base whose Building queue stands empty is not
growing at all, and that is no sign its far field is out of reach. So the
seconds without progress add up only over the cycles in which the Building
queue is active (it holds something queued, being built or waiting to be
placed), and a building placed along the line is judged only in such a
cycle; while the queue stands empty, neither count moves.
"""

from __future__ import annotations

import math

from ucla_agent.core import records
from ucla_agent.games.redalert import placement
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.placement import FarField
from ucla_agent.games.redalert.production import BUILDING_QUEUE
from ucla_agent.games.redalert.tuning import PlacementTuning
from ucla_agent.games.redalert.world import DEFENSE_TYPES, Cell, World

# The match record's event a far field given up is written as, the one each building placed toward a field is, and the
# reason it gives; its distance is written with this many decimal places.
CREEP_PLACEMENT_EVENT = "creep_placement"
STALLED_SKIP_REASON = "stalled_skip"
_DISTANCE_DECIMALS = 2


class LineStall:
    """The headway of one line toward its far field. See the module's note."""

    def __init__(self) -> None:
        # The far field followed; None while the line has none.
        self._field: FarField | None = None
        # The best distance at the field's last progress, or when it became the far field; the ticks the Building queue
        # was active since then, and the tick of the last look.
        self._progress_best = math.inf
        self._active_ticks = 0
        self._looked_at = 0
        # The best distance in the last cycle looked at.
        self.best = math.inf
        # How many buildings placed along the line in a row brought no progress.
        self._misses = 0
        # (tick, best distance then) of each building placed along the line that is not judged yet.
        self._asked: list[tuple[int, float]] = []

    def note_placement(self, field_center: Cell, tick: int) -> None:
        """Note a building asked onto the map along the line toward the field of `field_center` at a tick; one asked
        toward another field than the line's far field now is not counted."""
        if self._field is not None and self._field.patch.center == field_center:
            self._asked.append((tick, self.best))

    def is_stalled(self, field: FarField | None, world: World, tuning: PlacementTuning) -> bool:
        """Whether the line's far field `field` (None without one) is to be given up in this cycle: `stall_placements`
        buildings in a row placed along the line brought no progress, or the Building queue was active for
        `stall_seconds` without progress. The time since the last look counts only when the queue is active now, and
        buildings are judged only then (`is_building_active`)."""
        if not _is_same_line(field, self._field):
            self._follow(field, world)
            return False
        is_active = is_building_active(world)
        elapsed, self._looked_at = world.tick - self._looked_at, world.tick
        self.best = best_distance(world, field)
        if self.best <= self._progress_best - tuning.stall_progress_cells:
            self._progress_best, self._active_ticks, self._misses = self.best, 0, 0
        elif is_active:
            self._active_ticks += elapsed
        if is_active:
            self._judge(world.tick, tuning)
        is_too_long = self._active_ticks >= tuning.stall_seconds * TICKS_PER_SECOND
        return self._misses >= tuning.stall_placements or is_too_long

    def _follow(self, field: FarField | None, world: World) -> None:
        """Start following a line's new far field, or none, from the best distance now."""
        self._field = field
        self.best = math.inf if field is None else best_distance(world, field)
        self._progress_best, self._misses, self._asked = self.best, 0, []
        self._active_ticks, self._looked_at = 0, world.tick

    def _judge(self, tick: int, tuning: PlacementTuning) -> None:
        """Judge each building placed along the line that was asked `promise_ticks` ago or more: one that brought no
        progress since it was asked adds a miss, one that did starts the count afresh."""
        due = [asked for asked in self._asked if tick - asked[0] >= tuning.promise_ticks]
        self._asked = [asked for asked in self._asked if tick - asked[0] < tuning.promise_ticks]
        for _, best_then in due:
            self._misses = 0 if self.best <= best_then - tuning.stall_progress_cells else self._misses + 1


def _is_same_line(field: FarField | None, followed: FarField | None) -> bool:
    """Whether a far field is the one followed, from the same start: its home field may change meanwhile."""
    return (
        field is not None and followed is not None and (field.patch, field.origin) == (followed.patch, followed.origin)
    )


def is_building_active(world: World) -> bool:
    """Whether the Building queue is active: it holds something queued, being built or waiting to be placed."""
    return bool(world.production.get(BUILDING_QUEUE))


def best_distance(world: World, field: FarField) -> float:
    """The smallest distance from the middle of an own building but a defense to the far field's ore cell nearest the
    line's start; infinite without such a building."""
    ore_x, ore_y = field.patch.nearest_cell(field.origin)
    middles = (
        placement.standing_box(building).center
        for building in world.buildings.values()
        if building.type not in DEFENSE_TYPES
    )
    return min((math.hypot(x - ore_x, y - ore_y) for x, y in middles), default=math.inf)


def log_skip(field: FarField, line: str, best: float, world: World) -> None:
    """Write the `creep_placement` event of a far field given up: the field, the reason, the best distance reached and
    the line, `main` or `second`; no building."""
    records.log(
        CREEP_PLACEMENT_EVENT,
        {
            "tick": world.tick,
            "building": None,
            "cell": None,
            "field_center": list(field.patch.center),
            "target_patch_center": list(field.patch.center),
            "reason": STALLED_SKIP_REASON,
            "best_distance_cells": round(best, _DISTANCE_DECIMALS),
            "yard": line,
        },
    )
