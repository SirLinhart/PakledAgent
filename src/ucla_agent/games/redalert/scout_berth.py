"""The berth a designated scout keeps from danger (`[scouting] scout_wide_berth`).

The scouts of the scout slots (see `scout_slots`) keep clear of danger by
three margins: how far beyond an enemy unit's weapon range its ground counts
as dangerous, how near danger may come before a scout flees and how far it
then goes, and how hurt a scout may be before it leaves its slot. A scout
lost to the enemy sees nothing more and must be trained again, and the
margins `scout_steering` reads as shipped let a scout come close enough to be
caught by what it could have kept away from. With the wide berth on, each
margin takes its wider value: an enemy unit's ground reaches
wide_danger_margin_cells beyond its range, a scout flees danger within
wide_flee_cells and goes that far, and a scout below wide_release_below_hp
leaves its slot for main while it can still get home.

A scout is also hurt by what is not in sight: a defense not yet remembered,
an army beyond our units' sight. So with the wide berth on, a scout whose
health drops by hurt_min_drop or more between two states marks the cell it
stands on as a hurt area. For hurt_avoid_seconds the ground within
hurt_area_cells of it is dangerous to every scout, as a defense's cover is:
the ways keep out of it, a scout near it flees, and no scout is sent there
nor asks a model about it. Only the hurt_areas_max latest are kept, so that a
scout hurt over and over on its way home does not shut the whole map.

With `scout_cautious` on, a scout that retreats because it was hit (see
`scout_retreat`) marks the cell it was hit on as a hurt area as well, however
little health it lost, so that it takes up its mission around that ground.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.tuning import ScoutingTuning
from ucla_agent.games.redalert.world import Cell, Unit


def danger_margin_cells(tuning: ScoutingTuning) -> int:
    """How far beyond an enemy unit's weapon range its ground is dangerous to a scout."""
    return tuning.wide_danger_margin_cells if tuning.scout_wide_berth else tuning.scout_danger_margin_cells


def flee_cells(tuning: ScoutingTuning) -> int:
    """How near danger may come before a scout flees it, and how far the scout then goes; a way that keeps this far
    from every danger is taken before one that does not."""
    return tuning.wide_flee_cells if tuning.scout_wide_berth else tuning.scout_flee_cells


def release_below_hp(tuning: ScoutingTuning) -> int:
    """The health, in percent, below which a scout leaves its slot for main."""
    return tuning.wide_release_below_hp if tuning.scout_wide_berth else tuning.scout_release_below_hp


@dataclass(frozen=True)
class HurtArea:
    """The cell a scout stood on when it was hurt, and the tick it was."""

    cell: Cell
    tick: int


class HurtAreas:
    """Where the scouts of one match were hurt lately. See the module's note."""

    def __init__(self) -> None:
        self._areas: list[HurtArea] = []
        # Unit id -> the health of each scout that held a slot at the end of the last state.
        self._hp: dict[int, int] = {}

    def note(self, scout: Unit, tick: int, tuning: ScoutingTuning) -> bool:
        """Whether a scout's health dropped by hurt_min_drop or more since the last state; if it did, the cell the scout
        stands on becomes a hurt area, the oldest given up past hurt_areas_max."""
        last = self._hp.get(scout.id)
        if last is None or last - scout.hp < tuning.hurt_min_drop:
            return False
        self.mark((scout.x, scout.y), tick, tuning)
        return True

    def mark(self, cell: Cell, tick: int, tuning: ScoutingTuning) -> None:
        """Make a cell a hurt area from a tick, the oldest given up past hurt_areas_max; once only for the same cell and
        tick."""
        area = HurtArea(cell, tick)
        if area in self._areas:
            return
        kept = [kept for kept in self._areas if _is_live(kept, tick, tuning)] + [area]
        self._areas = kept[max(0, len(kept) - tuning.hurt_areas_max) :]

    def keep_health(self, scouts: Iterable[Unit]) -> None:
        """Keep the health of the scouts that hold the slots once this state's are filled: the next state's is set
        against it, so that a scout that took its slot in this state is not missed."""
        self._hp = {scout.id: scout.hp for scout in scouts}

    def live(self, tick: int, tuning: ScoutingTuning) -> tuple[Cell, ...]:
        """The cells of the hurt areas recorded less than hurt_avoid_seconds ago, the oldest first."""
        return tuple(area.cell for area in self._areas if _is_live(area, tick, tuning))


def _is_live(area: HurtArea, tick: int, tuning: ScoutingTuning) -> bool:
    """Whether a hurt area was recorded less than hurt_avoid_seconds before a tick."""
    return tick - area.tick < tuning.hurt_avoid_seconds * TICKS_PER_SECOND
