"""The enemy's nuke: when its missile silo is expected to fire, so that main is not standing in one clump then.

An enemy missile silo fires a nuclear missile each time it has charged, and
the enemy aims it where much of ours stands close together: an army standing
idle in one clump at home loses most of itself to one missile. So with
`nuke_spread` of `[combat]` on, the combat manager keeps a clock for every
enemy silo it has seen.

**The silo.** The item table has no entry for the silo, so it is named here,
`MISSILE_SILO`, and a variant of it is known by its name beginning so.

**The clock.** The silo is often seen only some time after it was finished,
and it has been charging since; the clock takes it to have been standing for
`nuke_build_ticks` when it is first seen, which errs early. The first launch
is expected at that first sighting + `nuke_charge_ticks` -
`nuke_build_ticks`, and a launch every `nuke_charge_ticks` after it.
Spreading is in force from `SPREAD_BEFORE_TICKS` (a minute) before an
expected launch of any silo still remembered until `SPREAD_AFTER_TICKS` (20
seconds) after it. A silo that leaves the enemy memory, destroyed or seen
gone, keeps no clock.

**Spreading.** While spreading is in force, main does not gather in one
clump: wherever it would gather or stand idle, at a point, its units go to
`nuke_spread_groups` cells around that point instead, one group to a cell.
The cells are the open ones (in the map, on ground the terrain does not
block) nearest to the point, each at least `nuke_spread_cells` from the
others and at least `nuke_keep_off_cells` from the center of our densest
cluster of structures, where the enemy is likeliest to aim: of the own
buildings within `CLUSTER_CELLS` of the one that has the most of them, their
centroid. The units are handed out over the cells in turn, each cell taking
its nearest share of the units left, so that a unit standing at its cell
stays with it from cycle to cycle. A unit within half `nuke_spread_cells` of
its cell is at its place: it is not sent there again, and counts as gathered.

**Record.** Each start and each end of spreading is a `combat` event with
the reason `nuke_spread`: the tick, whether spreading is now in force, the
expected launch it is for and the ids of the silos remembered.
"""

from __future__ import annotations

import functools
import math

from ucla_agent.core import records
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.fight import Point
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import CombatTuning
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

# The enemy's missile silo, which the item table does not list; its variants' names begin so.
MISSILE_SILO = "mslo"
# Main is spread from this many ticks before an expected launch until this many after it.
SPREAD_BEFORE_TICKS = 1500
SPREAD_AFTER_TICKS = 500
COMBAT_EVENT = "combat"
NUKE_SPREAD = "nuke_spread"
# Our structures within this many cells of one make up its cluster.
CLUSTER_CELLS = 5


def is_silo(actor_type: str) -> bool:
    """Whether an actor's type is the missile silo or a variant of it."""
    return actor_type.lower().startswith(MISSILE_SILO)


def known_silos(world: World) -> list[EnemyRecord]:
    """The remembered enemy missile silos."""
    return [record for record in world.enemy_memory.values() if record.is_building and is_silo(record.type)]


def launch_due(first_seen: int, tick: int, tuning: CombatTuning) -> int | None:
    """The expected launch of a silo first seen at `first_seen` whose spreading time holds `tick`; None while the
    tick is in no silo's spreading time."""
    first_launch = first_seen + tuning.nuke_charge_ticks - tuning.nuke_build_ticks
    since = tick - (first_launch - SPREAD_BEFORE_TICKS)
    if since < 0:
        return None
    launches, into = divmod(since, tuning.nuke_charge_ticks)
    if into > SPREAD_BEFORE_TICKS + SPREAD_AFTER_TICKS:
        return None
    return first_launch + launches * tuning.nuke_charge_ticks


class NukeWatch:
    """The clock of every enemy silo seen, and whether main is to be spread in this cycle. See the module's note."""

    def __init__(self) -> None:
        # Silo id -> the tick it was first seen.
        self._first_seen: dict[int, int] = {}
        # The expected launch spreading is in force for; None while it is not.
        self.launch_tick: int | None = None
        # The tick and the point the groups' cells were last sought for, and those cells.
        self._spots_for: tuple[int, Cell] | None = None
        self._spots: list[Cell] = []

    @property
    def is_spreading(self) -> bool:
        """Whether main is to be spread in this cycle."""
        return self.launch_tick is not None

    def follow(self, world: World, tuning: CombatTuning) -> None:
        """Bring the clocks up to date with the silos remembered, and start or end spreading, each a `combat` event.
        Nothing is followed with `nuke_spread` off."""
        if not tuning.nuke_spread:
            return
        silos = known_silos(world)
        self._first_seen = {silo.id: self._first_seen.get(silo.id, silo.last_seen_tick) for silo in silos}
        launch = self._launch_due(world.tick, tuning)
        if launch is not None and self.launch_tick is None:
            _log(launch, True, silos, world)
        elif launch is None and self.launch_tick is not None:
            _log(self.launch_tick, False, silos, world)
        self.launch_tick = launch

    def _launch_due(self, tick: int, tuning: CombatTuning) -> int | None:
        """The earliest expected launch of the silos remembered whose spreading time holds `tick`; None for none."""
        due = (launch_due(first_seen, tick, tuning) for first_seen in self._first_seen.values())
        return min((launch for launch in due if launch is not None), default=None)

    def gather(
        self,
        units: list[Unit],
        point: Cell,
        world: World,
        terrain: Terrain,
        tuning: CombatTuning,
        recalled: frozenset[int] = frozenset(),
    ) -> list[Intent]:
        """A `move` to its place in the groups spread around `point` for each unit away from it that stands idle or is
        among `recalled`, which go there whatever they do; one order for each place."""
        placed, reach = self._places(units, point, world, terrain, tuning), _place_reach(tuning)
        going: dict[Cell, list[int]] = {}
        for unit in units:
            place = placed[unit.id]
            if (unit.idle or unit.id in recalled) and not cells.is_within((unit.x, unit.y), place, reach):
                going.setdefault(place, []).append(unit.id)
        return [Intent(Priority.MOVE, {"cmd": "move", "units": ids, "to": list(place)}) for place, ids in going.items()]

    def at_places(
        self, units: list[Unit], point: Cell, world: World, terrain: Terrain, tuning: CombatTuning
    ) -> list[Unit]:
        """The units standing at their places in the groups spread around `point`."""
        placed, reach = self._places(units, point, world, terrain, tuning), _place_reach(tuning)
        return [unit for unit in units if cells.is_within((unit.x, unit.y), placed[unit.id], reach)]

    def _places(
        self, units: list[Unit], point: Cell, world: World, terrain: Terrain, tuning: CombatTuning
    ) -> dict[int, Cell]:
        """Each unit's place, by id: the cells of the groups are sought once a cycle for a point, and with none to be
        found the units gather at the point itself."""
        if self._spots_for != (world.tick, point):
            self._spots_for, self._spots = (world.tick, point), spread_cells(point, world, terrain, tuning) or [point]
        return places(units, self._spots)


def _log(launch_tick: int, is_spreading: bool, silos: list[EnemyRecord], world: World) -> None:
    """Write a start or an end of spreading as a `combat` event."""
    data = {
        "tick": world.tick,
        "reason": NUKE_SPREAD,
        "spreading": is_spreading,
        "launch_tick": launch_tick,
        "silos": sorted(silo.id for silo in silos),
    }
    records.log(COMBAT_EVENT, data)


def structure_cluster(world: World) -> Point | None:
    """The center of our densest cluster of structures: the centroid of the own buildings within `CLUSTER_CELLS` of
    the one that has the most of them; None with no building."""
    spots = [(building.x, building.y) for building in world.buildings.values()]
    clusters = [[other for other in spots if cells.is_within(spot, other, CLUSTER_CELLS)] for spot in spots]
    densest = max(clusters, key=len, default=None)
    if densest is None:
        return None
    return sum(x for x, _ in densest) / len(densest), sum(y for _, y in densest) / len(densest)


def spread_cells(point: Cell, world: World, terrain: Terrain, tuning: CombatTuning) -> list[Cell]:
    """The cells main's groups gather at around `point`, at most `nuke_spread_groups`: the open cells nearest to it, each
    at least `nuke_spread_cells` from those taken before it and at least `nuke_keep_off_cells` from the center of our
    densest cluster of structures. Fewer when the map has no more such cells near."""
    cluster = structure_cluster(world)
    taken: list[Cell] = []
    for offset in _nearest_first(_search_cells(tuning)):
        cell = (point[0] + offset[0], point[1] + offset[1])
        if _may_take(cell, taken, cluster, world, terrain, tuning):
            taken.append(cell)
            if len(taken) == tuning.nuke_spread_groups:
                break
    return taken


def places(units: list[Unit], spots: list[Cell]) -> dict[int, Cell]:
    """Main's units handed out over the cells its groups gather at: each cell in turn takes its nearest share of the
    units left, so that units already standing at a cell stay with it."""
    left = sorted(units, key=lambda unit: unit.id)
    placed: dict[int, Cell] = {}
    for index, spot in enumerate(spots):
        share = math.ceil(len(left) / (len(spots) - index))
        nearest = sorted(left, key=lambda unit: (cells.distance((unit.x, unit.y), spot), unit.id))[:share]
        placed.update(dict.fromkeys((unit.id for unit in nearest), spot))
        left = [unit for unit in left if unit.id not in placed]
    return placed


def _may_take(
    cell: Cell, taken: list[Cell], cluster: Point | None, world: World, terrain: Terrain, tuning: CombatTuning
) -> bool:
    """Whether a cell may be one that a group gathers at: open, far enough from the structures and from the others."""
    if not world.in_bounds(*cell) or cell in terrain.blocked:
        return False
    if cluster is not None and math.dist(cell, cluster) < tuning.nuke_keep_off_cells:
        return False
    return all(cells.distance(cell, other) >= tuning.nuke_spread_cells for other in taken)


def _search_cells(tuning: CombatTuning) -> int:
    """How far from the point the cells of the groups are sought: far enough to clear the structures and to set every
    group apart from the others."""
    return tuning.nuke_keep_off_cells + tuning.nuke_spread_cells * tuning.nuke_spread_groups


@functools.cache
def _nearest_first(reach: int) -> tuple[Cell, ...]:
    """The offsets of the cells within a square of `reach` cells around a point, the nearest to it first."""
    offsets = [(x, y) for x in range(-reach, reach + 1) for y in range(-reach, reach + 1)]
    return tuple(sorted(offsets, key=lambda offset: (offset[0] ** 2 + offset[1] ** 2, offset[1], offset[0])))


def _place_reach(tuning: CombatTuning) -> float:
    """How near its group's cell a unit is at its place: half the distance between two groups, so that no unit is at
    the place of two."""
    return tuning.nuke_spread_cells / 2
