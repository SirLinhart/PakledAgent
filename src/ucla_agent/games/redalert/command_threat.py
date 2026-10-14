"""What threatens the army the model commander commands: an attack on the base, an enemy army closing, a lost fight.

The commander keeps main doing something useful: its defensive actions (pull
back, retreat home and hold; see `command_army`) are offered only while one of
three things holds, and main's question names the first that does on its
`threat:` line:

- `base attacked`: base defense has a threat in force (`threat_active`);
- `enemy army closing at (x,y)`: the enemy combat units seen within the last
  `CLOSING_SEEN_SECONDS` that stand within `CLOSING_CELLS` of base_center are
  worth `CLOSING_VALUE` or more together, and their centroid, the cell named,
  is closer to base_center than the centroid of the same units
  `CLOSING_LOOKBACK_SECONDS` before. A group none of whose units was seen then
  counts as closing: it came out of the dark;
- `losing here`: main's fight where it stands (`fight_here` of
  `command_army`, which counts the enemy defenses that reach it) is under
  `LOSING_RATIO`; with no enemy near there is no fight, and no loss;

and `none` while none does.

Only the units within `CLOSING_CELLS` of the base make up the group, so that
an enemy army far off on the other side of the map does not pull the centroid
of one near the base away from it. Where they were before is kept by the
closing watch (`ClosingWatch`), which the commander brings up to date in every
cycle. The same watch finds an army closing on any cell (`ClosingWatch.closing_on`),
which the closing-army intercept of the combat manager reads (see
`closing_intercept`).

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Iterable, Mapping

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.command_prompt import NONE, at
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.world import Cell, EnemyRecord, World

CLOSING_VALUE = 1000
CLOSING_CELLS = 20
CLOSING_SEEN_SECONDS = 10
CLOSING_LOOKBACK_SECONDS = 15
LOSING_RATIO = 1.0
# The threats the `threat:` line names, the first that holds named.
BASE_ATTACKED = "base attacked"
ARMY_CLOSING = "enemy army closing"
LOSING_HERE = "losing here"
_SEEN_TICKS = CLOSING_SEEN_SECONDS * TICKS_PER_SECOND
_LOOKBACK_TICKS = CLOSING_LOOKBACK_SECONDS * TICKS_PER_SECOND


@dataclass(frozen=True)
class Threat:
    """What threatens main in a cycle: one of the threats the `threat:` line names, or `none`, and for an enemy army
    closing, the cell of its centroid."""

    kind: str
    cell: Cell | None = None

    @property
    def line(self) -> str:
        """The question's `threat:` line."""
        return f"threat: {self.kind}" if self.cell is None else f"threat: {self.kind} at {at(self.cell)}"

    @property
    def is_threatened(self) -> bool:
        """Whether any threat holds, which offers main its defensive actions."""
        return self.kind != NONE


@dataclass(frozen=True)
class ClosingGroup:
    """An enemy army closing on a cell: its units, what they cost together and their centroid."""

    records: tuple[EnemyRecord, ...]
    value: int
    centroid: tuple[float, float]


@dataclass(frozen=True)
class _Sample:
    """The cells of the enemy combat units in sight at a tick, by id."""

    tick: int
    cells: Mapping[int, Cell]


class ClosingWatch:
    """Where the enemy combat units in sight stood over the last CLOSING_LOOKBACK_SECONDS, and the enemy army closing on
    the base. See the module's note."""

    def __init__(self) -> None:
        # At most one sample a second, the oldest first: the latest at least the lookback old, and those since.
        self._samples: deque[_Sample] = deque()

    def note(self, world: World) -> None:
        """Take in the cells of the enemy combat units in sight, a second or more after the last sample; forget the
        samples older than the one the lookback reads."""
        if self._samples and world.tick - self._samples[-1].tick < TICKS_PER_SECOND:
            return
        seen = {
            record.id: (record.x, record.y)
            for record in world.enemy_memory.values()
            if record.visible and is_combat_unit(record)
        }
        self._samples.append(_Sample(world.tick, seen))
        horizon = world.tick - _LOOKBACK_TICKS
        while len(self._samples) > 1 and self._samples[1].tick <= horizon:
            self._samples.popleft()

    def closing(self, world: World) -> Cell | None:
        """The cell of the centroid of the enemy army closing on the base (see the module's note); None for none, and
        while our base is not known."""
        base = world.base_center
        if base is None:
            return None
        group = self.closing_on(base, CLOSING_CELLS, CLOSING_VALUE, world)
        return None if group is None else cells.nearest_cell(*group.centroid)

    def closing_on(self, cell: Cell, reach: float, least: int, world: World) -> ClosingGroup | None:
        """The enemy army closing on a cell: the enemy combat units seen within the last CLOSING_SEEN_SECONDS that
        stand within `reach` cells of it, worth `least` or more together, whose centroid is closer to it than the
        centroid of the same units CLOSING_LOOKBACK_SECONDS before, or none of which was seen then; None otherwise."""
        group = [
            record
            for record in world.enemy_memory.values()
            if is_combat_unit(record)
            and world.tick - record.last_seen_tick <= _SEEN_TICKS
            and cells.is_within((record.x, record.y), cell, reach)
        ]
        value = sum(_cost(record, world) for record in group)
        if not group or value < least:
            return None
        now = _centroid((record.x, record.y) for record in group)
        before = self._before(world.tick)
        earlier = [before.cells[record.id] for record in group if record.id in before.cells] if before else []
        if earlier and _distance(_centroid(earlier), cell) <= _distance(now, cell):
            return None
        return ClosingGroup(tuple(group), value, now)

    def _before(self, tick: int) -> _Sample | None:
        """The latest sample taken at least CLOSING_LOOKBACK_SECONDS before a tick; None while there is none."""
        return next((sample for sample in reversed(self._samples) if sample.tick <= tick - _LOOKBACK_TICKS), None)

    def cells_at_or_before(self, tick: int) -> Mapping[int, Cell]:
        """The cells of the enemy combat units in sight in the latest sample taken at or before a tick, by id; none
        while no sample is that old."""
        found = next((sample for sample in reversed(self._samples) if sample.tick <= tick), None)
        return {} if found is None else found.cells


def threat(world: World, is_base_attacked: bool, watch: ClosingWatch, fight_ratio: float | None) -> Threat:
    """The first threat to main that holds: the base attacked, an enemy army closing, main losing its fight where it
    stands (`fight_ratio`, None while no enemy is near); `none` otherwise."""
    if is_base_attacked:
        return Threat(BASE_ATTACKED)
    closing_at = watch.closing(world)
    if closing_at is not None:
        return Threat(ARMY_CLOSING, closing_at)
    if fight_ratio is not None and fight_ratio < LOSING_RATIO:
        return Threat(LOSING_HERE)
    return Threat(NONE)


def _cost(record: EnemyRecord, world: World) -> int:
    facts = world.items.get(record.type)
    return facts.cost if facts is not None else 0


def _centroid(points: Iterable[Cell]) -> tuple[float, float]:
    listed = list(points)
    return sum(x for x, _ in listed) / len(listed), sum(y for _, y in listed) / len(listed)


def _distance(point: tuple[float, float], cell: Cell) -> float:
    return math.hypot(point[0] - cell[0], point[1] - cell[1])
