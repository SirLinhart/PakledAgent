"""The harvester crush (`[opening] rush_harvester_crush`): a harvester run over enemy infantry at the base.

A rush of infantry that reaches the base before the first tank does meets
rifles and a pillbox or two, and loses few men to them. A harvester is a
heavy vehicle: driven through a tight cluster of infantry it crushes those it
drives over, as a tank does. So when nothing else can, a harvester does:

- **When.** The rush counter is called for (see `rush_counter`): the rush
  verdict holds, or base defense reports a threat at the base before a war
  factory stands.
- **Over what.** The largest cluster of enemy infantry in sight, by the
  crush runs' cluster rules (`crush.largest_cluster`, crush_cluster_cells of
  [combat]), with as many infantry as a crush run qualifies at
  (`crush.min_infantry`), its centroid within NEAR_BASE_CELLS of base_center,
  no own tank or APC within crush_reach_cells of it (they run it over
  themselves, see `crush`), and its infantry worth half a harvester or more:
  a lost harvester costs more than a soldier or two crushed. Nothing else
  may be handling it: no own defense of DEFENSES has it within its weapon
  range and a cell, and our units that fight within OWN_UNITS_CELLS of its
  centroid are worth less than its infantry.
- **Which harvester.** The nearest to the centroid with MIN_HP percent of its
  health or more.
- **The run.** The harvester is sent to the centroid's cell with a `move` at
  EMERGENCY in every cycle while the run lasts; it is published as
  `MatchContext.harvester_crush`, and the agent takes it out of every other
  order (`crush.run_first`), so the harvesters' flight from enemies, their
  rebalance and the orders that send them back to harvest do not reach it.
  The run ends when the harvester is within crush ARRIVED_CELLS of its cell,
  when fewer of the cluster's infantry than it qualified at are in sight
  within crush_cluster_cells of the centroid, when RUN_SECONDS have passed,
  or when the harvester is lost. Then it is ordered to harvest again, and
  once more RESEND_SECONDS later when it stands idle or is not harvesting
  then. One harvester runs at a time; a new run may start from the next
  cycle.

Each run, when it ends, is a `combat` event with the reason
`harvester_crush`: the tick, the tick it started, the centroid's cell, the
cluster's size, the harvester, whether it was lost, and why the run ended.
This path is separate from the tank and APC crush runs, which never take a
harvester.
"""

from __future__ import annotations

from dataclasses import dataclass

from ucla_agent.core import records
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.crush import (
    ARRIVED_CELLS,
    COMBAT_EVENT,
    Cluster,
    infantry_in_sight,
    is_crusher,
    largest_cluster,
    min_infantry,
)
from ucla_agent.games.redalert.family import HARVESTER
from ucla_agent.games.redalert.fight import is_near
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.rush_counter import is_counter_called
from ucla_agent.games.redalert.world import Cell, Unit, World

HARVESTER_CRUSH_REASON = "harvester_crush"
# A cluster is run over when its centroid lies this close to base_center.
NEAR_BASE_CELLS = 15
# A harvester is sent with this much of its health, in percent, or more.
MIN_HP = 50
# A run lasts this long at the most.
RUN_SECONDS = 15
# The harvest order is sent once more this long after a run ended, to a harvester idle or not harvesting by then.
RESEND_SECONDS = 10
# The activity the state shows for a harvester at work.
HARVESTING = "FindAndDeliverResources"
# The own defenses that handle a cluster within their weapon range and a cell.
DEFENSES = frozenset({"pbox", "hbox", "gun", "ftur", "tsla"})
DEFENSE_MARGIN_CELLS = 1
# Our units that fight this close to a cluster's centroid handle it when they are worth as much as its infantry.
OWN_UNITS_CELLS = 6
# Why a run ended.
ARRIVED = "arrived"
CLUSTER_BROKEN = "cluster_broken"
TIMED_OUT = "timed_out"
HARVESTER_LOST = "harvester_lost"


@dataclass(frozen=True)
class HarvesterRun:
    """A harvester crush in force: the harvester, the cell it runs to, the cluster, when it started and the fewest
    infantry the cluster qualified at."""

    harvester: int
    cell: Cell
    cluster: Cluster
    started_tick: int
    qualified_at: int


class HarvesterCrush:
    """The harvester crush of one match. See the module's note."""

    def __init__(self) -> None:
        self._run: HarvesterRun | None = None
        # The harvester of the last run that ended and the tick it ended at, until its harvest order is checked again.
        self._ended: tuple[int, int] | None = None

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The cycle's order for the harvester crush: the run's move while it lasts, the harvest order in the cycle it
        ends and again RESEND_SECONDS later when needed, the move of a run that starts; the run published on the match
        context."""
        ctx.harvester_crush = {}
        if self._run is not None:
            return self._follow(world, ctx)
        resent = self._resend(world)
        if not ctx.tuning.opening.rush_harvester_crush or not is_counter_called(ctx) or world.base_center is None:
            return resent
        self._run = _start(world, ctx)
        return resent if self._run is None else resent + self._move(ctx)

    def _resend(self, world: World) -> list[Intent]:
        """The harvest order sent once more RESEND_SECONDS after the last run ended, when its harvester stands idle or
        is not harvesting; none else."""
        if self._ended is None or world.tick - self._ended[1] < RESEND_SECONDS * TICKS_PER_SECOND:
            return []
        harvester_id, self._ended = self._ended[0], None
        harvester = world.units.get(harvester_id)
        if harvester is None or (not harvester.idle and harvester.activity == HARVESTING):
            return []
        return [Intent(Priority.EMERGENCY, {"cmd": "harvest", "units": [harvester_id]})]

    def _follow(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The run's move while it lasts; once it has ended, its event and the harvester's order to harvest again."""
        run = self._run
        ended = _ended(run, world, ctx)
        if ended is None:
            return self._move(ctx)
        self._run = None
        _log(run, ended, world)
        if ended == HARVESTER_LOST:
            return []
        self._ended = (run.harvester, world.tick)
        return [Intent(Priority.EMERGENCY, {"cmd": "harvest", "units": [run.harvester]})]

    def _move(self, ctx: MatchContext) -> list[Intent]:
        """The run's move to its cell, the run published on the match context."""
        run = self._run
        ctx.harvester_crush = {run.harvester: run.cell}
        return [Intent(Priority.EMERGENCY, {"cmd": "move", "units": [run.harvester], "to": list(run.cell)})]


def _start(world: World, ctx: MatchContext) -> HarvesterRun | None:
    """A run over the cluster that qualifies in this cycle, with the harvester sent; None for none."""
    combat = ctx.tuning.combat
    cluster = largest_cluster(infantry_in_sight(world), combat.crush_cluster_cells)
    if cluster is None or not _qualifies(cluster, world, ctx):
        return None
    harvester = _nearest_fit_harvester(cluster, world)
    if harvester is None:
        return None
    cell = cells.clamped((round(cluster.centroid[0]), round(cluster.centroid[1])), world.map_bounds)
    return HarvesterRun(harvester.id, cell, cluster, world.tick, min_infantry(cluster, combat))


def _qualifies(cluster: Cluster, world: World, ctx: MatchContext) -> bool:
    """Whether a cluster is one to run a harvester over: enough infantry, near the base, no own crushing vehicle in
    reach of it, and worth half a harvester or more."""
    combat = ctx.tuning.combat
    middle = cluster.centroid
    if len(cluster.infantry) < min_infantry(cluster, combat):
        return False
    if not is_near(middle, *world.base_center, NEAR_BASE_CELLS):
        return False
    if any(
        is_crusher(unit.type, world.items) and is_near(middle, unit.x, unit.y, combat.crush_reach_cells)
        for unit in world.combat_units.values()
    ):
        return False
    worth = sum(_cost(record.type, world) for record in cluster.infantry)
    return 2 * worth >= _cost(HARVESTER, world) and not _is_handled(cluster, worth, world)


def _is_handled(cluster: Cluster, worth: int, world: World) -> bool:
    """Whether something else handles a cluster: an own defense of DEFENSES with its centroid within its weapon range
    and DEFENSE_MARGIN_CELLS, or our units that fight within OWN_UNITS_CELLS of it worth as much as its infantry."""
    middle = cluster.centroid
    if any(
        building.type in DEFENSES
        and is_near(middle, building.x, building.y, world.items.damage.get(building.type).range + DEFENSE_MARGIN_CELLS)
        for building in world.buildings.values()
    ):
        return True
    near = [unit for unit in world.combat_units.values() if is_near(middle, unit.x, unit.y, OWN_UNITS_CELLS)]
    return sum(_cost(unit.type, world) for unit in near) >= worth


def _nearest_fit_harvester(cluster: Cluster, world: World) -> Unit | None:
    """The own harvester nearest the cluster's centroid with MIN_HP percent of its health or more; None for none."""
    fit = [unit for unit in world.units.values() if unit.type == HARVESTER and unit.hp >= MIN_HP]
    x, y = cluster.centroid
    return min(fit, key=lambda unit: ((unit.x - x) ** 2 + (unit.y - y) ** 2, unit.id), default=None)


def _ended(run: HarvesterRun, world: World, ctx: MatchContext) -> str | None:
    """Why a run ends in this cycle, or None while it lasts."""
    harvester = world.units.get(run.harvester)
    if harvester is None:
        return HARVESTER_LOST
    if cells.is_within((harvester.x, harvester.y), run.cell, ARRIVED_CELLS):
        return ARRIVED
    if world.tick - run.started_tick >= RUN_SECONDS * TICKS_PER_SECOND:
        return TIMED_OUT
    ids = {record.id for record in run.cluster.infantry}
    reach = ctx.tuning.combat.crush_cluster_cells
    staying = [
        record
        for record in infantry_in_sight(world)
        if record.id in ids and is_near(run.cluster.centroid, record.x, record.y, reach)
    ]
    return CLUSTER_BROKEN if len(staying) < run.qualified_at else None


def _cost(item: str, world: World) -> int:
    facts = world.items.get(item)
    return 0 if facts is None else facts.cost


def _log(run: HarvesterRun, ended: str, world: World) -> None:
    """Write the `combat` event of a harvester crush that ended."""
    records.log(
        COMBAT_EVENT,
        {
            "reason": HARVESTER_CRUSH_REASON,
            "tick": world.tick,
            "started_tick": run.started_tick,
            "cell": list(run.cell),
            "cluster_size": len(run.cluster.infantry),
            "harvester": run.harvester,
            "harvester_lost": ended == HARVESTER_LOST,
            "ended": ended,
        },
    )
