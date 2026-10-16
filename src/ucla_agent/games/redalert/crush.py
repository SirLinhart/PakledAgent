"""Crush runs: own tanks driven through a tight cluster of enemy infantry to run it over instead of shooting it.

A tank fires slowly at infantry and does it little harm, but it kills every
soldier it drives over. Enemy infantry that bunch up in front of the agent's
tanks are therefore worth running over. With `crush_runs` of `[combat]` on,
the combat manager looks for such a cluster in every cycle, before it looks at
whether base defense has called main home.

**The crushing vehicles.** Every item whose roles in the item table include
`tank`, and the APC, which crushes infantry though it is no tank: the
vehicles the estimate of a fight counts as hurting infantry by crushing it. A
run may take any of them that is main's (whether base defense has called main
home or not, but those of the commander's raid detachment, those joining main
through the commander's forward gate and those sent back on their own), the
home guard's, or one base defense has taken to answer a threat: the tanks near
a cluster are often the ones defending the base.

**The cluster.** Among the enemy infantry in sight, the largest set within
`crush_cluster_cells` of its centroid, sought around each infantry unit in
turn: the units within reach of it, then those within reach of their
centroid. It is run over when all of these hold:
- it has at least `crush_min_infantry` units, or `crush_at_min_infantry`
  for an anti-tank-heavy cluster: one whose rocket soldiers and shock
  troopers (`ANTI_TANK_INFANTRY`: e3 and shok, the Tesla infantry) make up
  `crush_at_share` or more of it. Those kill tanks on the way in, so such a
  cluster is run over sooner and with more tanks, not left to shoot;
- its centroid is within `crush_reach_cells` of one of the crushing
  vehicles a run may take;
- no remembered enemy defense that fires at ground units covers the centroid
  (its range and the margin of `[coverage]`), since a tank that runs in there
  is shot at as it goes; an AA gun or a SAM site, whose weapon hits aircraft
  alone, covers nothing here;
- the agent has the advantage around it: the ratio of the own combat units
  within 15 cells of the centroid, tanks included, to the enemy combat units
  in sight within 15 cells of it, each side weighed as in the estimate of a
  fight (`fight.ratio_against`), is at least `crush_min_ratio` (1.0).

**The tanks.** A cluster's planned count (`planned`) is one tank for every
`crush_per_tanks` infantry of it, `crush_max_tanks` at the most, or for an
anti-tank-heavy cluster one for every `crush_at_per_tanks` infantry,
`crush_at_max_tanks` at the most: 3 for 12 rifles, 5 for 12 infantry of which
4 are rocket soldiers. A run sends that many of the crushing vehicles a run
may take, never fewer than `crush_min_tanks` (`committed`): the nearest to its
centroid first among those within `crush_reach_cells` of it with
`crush_min_hp` percent of their health or more, the eligible ones: a tank
nearly lost is not sent into the enemy, and one farther off would not get
there in time. Each runs to the cell `BEYOND_CELLS` past the centroid on the
line from where it stands, so that its way passes through the cluster. The
cell is kept inside the map and, when the terrain blocks it, moved to the
nearest cell it does not block (the terrain counts ore as blocked too); a
tank with no such cell near is passed over for the next nearest. A run starts
only when its whole committed count is found so, all of them ordered in the
same cycle, and with fewer none goes: one tank at a time is shot on its way
in, a lone tank crushes a soldier or two before it is lost, and a group short
of its count loses more tanks than the infantry it crushes is worth.

**The run.** One run is in force at a time, and a run-over detachment of the
model commander's counts as that one run and comes first: while the commander
has one out (`MatchContext.run_over_out`) no run starts, and while a run is in
force (`MatchContext.crush_run` holds a tank) the commander offers none. The
detachment keeps its own size, `run_cells` with no count given:
min(crush_max_tanks, ceil(infantry / crush_per_tanks)) of the tanks, with no
minimum and no wait for its full count, both being the script's alone. The run's tanks
get a `move` to their
cells at CONTACT in every cycle, and no other order of main's: they are left
out of its focus fire and taken out of its marches, gathering, screen, siege
and forward hold, and out of the model commander's order. Only an emergency
of main's still reaches them, main's retreat or a badly damaged unit's
withdrawal, since an emergency comes ahead of everything else. No other
manager's order reaches them: the combat manager publishes the tanks whose
run move holds in the cycle, those no emergency of main's names, each with
its cell (`MatchContext.crush_run`). Base defense, which runs first, tells the
tanks of the run as it stood in the cycle before nothing, and the agent takes
the tanks of the cycle's run out of every order but their run move
(`run_first`): base defense's attack-move in the cycle a run starts, a
recall, a raid's move and an order the ledger carries over alike. A tank
leaves the run when it is within ARRIVED_CELLS of its cell or is no longer
one a run may take.
The run ends when RUN_TICKS (8 seconds) have passed since it started, when
fewer of its cluster's infantry than it qualified at (`min_infantry`) are
still in sight within `crush_cluster_cells` of the centroid it started on,
or when none of its tanks is left on its way. In the cycle it ends its tanks
take their group's orders again; a new run may start from the next cycle.
Other vehicles near the run keep their focus targets: only the run's tanks
are taken out of main's orders.

**Record.** Each run, when it ends, is one `combat` event with the reason
`crush_run`: the tick, the tick it started, the cell of the cluster's
centroid, the cluster's size when it started, the ids of the tanks sent, how
many of the cluster's infantry were killed during the run (those that left
sight in a state whose reported kills account for everything that left it,
`infantry_killed`) and what they cost (`infantry_killed_cost`), how many of
the tanks sent were lost meanwhile (`tanks_lost`: no longer among the own
units), the trade (`trade`: the infantry's cost less the lost tanks' cost),
and why it ended (`arrived`, `cluster_broken` or `timed_out`). A lost tank is
worth it when the crush pays for it.

**Skips.** A cluster a run could be wanted over but that no run started over
is told as well, so that a match record says why: in a cycle in which no run
starts, the largest cluster of enemy infantry in sight, when it has as many
units as it would qualify at (`min_infantry`) and lies within
`crush_reach_cells` of any own crushing vehicle, whatever its group, is a
`combat` event with the reason
`crush_skipped`, unless it shares infantry with the run in force, which is
running it over. The event gives the tick, the cell of the cluster's centroid,
its size and the first gate that kept a run from starting, taken in this
order: `run_active` (a run is in force, or the commander's run-over
detachment is out), `no_tank_in_main` (none of the
crushing vehicles a run may take is within reach), `tank_hp` (none of them has
`crush_min_hp`), `too_few_tanks` (fewer than `crush_min_tanks` of them have
it), `short_group` (fewer than its committed count have it), `ratio` (the
fight around it is below `crush_min_ratio`),
`cover` (a remembered defense that fires at ground units covers the
centroid), and `other` (none of these, such as no open cell to run to). A
cluster is told at most once in SKIP_REPEAT_TICKS (10 seconds) for its
centroid rounded to SKIP_CELLS (4) cells. Telling it changes nothing a run
does.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, coverage
from ucla_agent.games.redalert.batch import ACTIVITY_COMMANDS, Intent, Order, Priority
from ucla_agent.games.redalert.fight import Point, centroid, ground_defenses, is_near, ratio_against
from ucla_agent.games.redalert.items import TICKS_PER_SECOND, ItemTable
from ucla_agent.games.redalert.production import INFANTRY_QUEUE
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

# The role of the item table's vehicles that crush infantry, and the one vehicle without it that does too.
TANK_ROLE = "tank"
APC = "apc"
# The infantry that kills tanks: the rocket soldiers, and the shock troopers, the infantry that carries a Tesla gun.
ANTI_TANK_INFANTRY = frozenset({"e3", "shok"})
# The fight around a cluster is weighed among the units within this many cells of its centroid.
RATIO_REACH_CELLS = 15
# A tank runs to the cell this many cells past the cluster's centroid, on the far side from where it stands.
BEYOND_CELLS = 2
# How far from that cell, when the terrain blocks it, an open cell is looked for.
OPEN_CELL_SEARCH_CELLS = 3
# A tank within this many cells of its cell has reached it.
ARRIVED_CELLS = 1
# A run lasts 8 seconds at the most: a tank that has not got through by then is held up, and goes back to the fight.
RUN_TICKS = 8 * TICKS_PER_SECOND
COMBAT_EVENT = "combat"
CRUSH_RUN = "crush_run"
# Why a run ended: none of its tanks is left on its way, too few of its cluster stayed together, or its time ran out.
ARRIVED = "arrived"
CLUSTER_BROKEN = "cluster_broken"
TIMED_OUT = "timed_out"
CRUSH_SKIPPED = "crush_skipped"
# Why no run started over a cluster near an own crushing vehicle: the gates, in the order they are taken.
RUN_ACTIVE = "run_active"
NO_TANK_IN_MAIN = "no_tank_in_main"
TANK_HP = "tank_hp"
TOO_FEW_TANKS = "too_few_tanks"
SHORT_GROUP = "short_group"
RATIO = "ratio"
COVER = "cover"
OTHER = "other"
# A skipped cluster is told at most once in this long for its centroid rounded to SKIP_CELLS cells.
SKIP_CELLS = 4
SKIP_REPEAT_TICKS = 10 * TICKS_PER_SECOND


@dataclass(frozen=True)
class Cluster:
    """A tight cluster of enemy infantry in sight: its units and their centroid."""

    infantry: tuple[EnemyRecord, ...]
    centroid: Point


@dataclass
class Run:
    """A crush run in force: the cluster it runs over, the tick it started, the tanks still on their way, each with
    the cell it runs to, the cluster's infantry killed so far, and the tanks sent lost so far."""

    cluster: Cluster
    started_tick: int
    going: dict[int, Cell]
    # The tanks sent, in the order they were chosen.
    tanks: tuple[int, ...] = ()
    killed: set[int] = field(default_factory=set)
    # What each tank sent cost, by id, taken when it was sent: a lost tank is no longer in the world to look up.
    costs: dict[int, int] = field(default_factory=dict)
    lost: set[int] = field(default_factory=set)

    def orders(self) -> list[Intent]:
        """A `move` at CONTACT for the tanks still on their way, one order for each cell they run to."""
        by_cell: dict[Cell, list[int]] = {}
        for unit_id, cell in self.going.items():
            by_cell.setdefault(cell, []).append(unit_id)
        return [
            Intent(Priority.CONTACT, {"cmd": "move", "units": units, "to": list(cell)})
            for cell, units in by_cell.items()
        ]

    def keep_up(self, tanks: dict[int, Unit], world: World) -> None:
        """Count the cluster's infantry killed and the tanks sent lost in the latest state, and let go of the tanks
        that have reached their cell or are no longer among the units a run may take, `tanks`."""
        self.killed |= world.killed_in_sight & {record.id for record in self.cluster.infantry}
        self.lost |= {unit_id for unit_id in self.costs if unit_id not in world.units}
        self.going = {
            unit_id: cell
            for unit_id, cell in self.going.items()
            if unit_id in tanks and not is_near(cell, tanks[unit_id].x, tanks[unit_id].y, ARRIVED_CELLS)
        }

    def ended(self, world: World, tuning: CombatTuning) -> str | None:
        """Why the run is over in this cycle; None while it goes on."""
        if world.tick - self.started_tick >= RUN_TICKS:
            return TIMED_OUT
        if self._staying(world, tuning) < min_infantry(self.cluster, tuning):
            return CLUSTER_BROKEN
        return None if self.going else ARRIVED

    def _staying(self, world: World, tuning: CombatTuning) -> int:
        """How many of the cluster's infantry are still in sight within crush_cluster_cells of its centroid."""
        members = {record.id for record in self.cluster.infantry}
        near = _within(infantry_in_sight(world), self.cluster.centroid, tuning.crush_cluster_cells)
        return sum(1 for record in near if record.id in members)


class CrushRuns:
    """The crush run the agent's tanks are on, one at a time. See the module's note."""

    def __init__(self) -> None:
        # None while no run is in force.
        self._run: Run | None = None
        # The tick each skipped cluster was last told at, by its centroid rounded to SKIP_CELLS cells.
        self._told: dict[Cell, int] = {}

    @property
    def units(self) -> frozenset[int]:
        """The tanks on their way through a cluster in the latest cycle, which take no order of main's but an
        emergency."""
        return frozenset(() if self._run is None else self._run.going)

    @property
    def sent(self) -> frozenset[int]:
        """Every tank of the run in force, on its way or arrived, which repair does not take while the run lasts."""
        return frozenset(() if self._run is None else self._run.tanks)

    def held(self, orders: list[Intent]) -> dict[int, Cell]:
        """The tanks of the run in force whose run move holds in this cycle, each with the cell it runs to: all but
        those an emergency among main's `orders` names; none while no run is in force."""
        if self._run is None:
            return {}
        urgent = {
            unit_id
            for intent in orders
            if intent.priority == Priority.EMERGENCY
            for unit_id in intent.order.get("units", ())
        }
        return {unit_id: cell for unit_id, cell in self._run.going.items() if unit_id not in urgent}

    def follow(
        self, tanks: list[Unit], world: World, tuning: Tuning, terrain: Terrain, run_over_out: bool
    ) -> list[Intent]:
        """The run's orders in this cycle, with the own units a run may take given: the run in force kept up, or ended
        with none, else one started on a qualifying cluster unless the commander's run-over detachment is out
        (`run_over_out`), which counts as the one run; none with crush_runs off. A cluster no run starts over is told
        as skipped."""
        if not tuning.combat.crush_runs:
            return []
        in_force = self._run
        if in_force is not None:
            self._keep_up(tanks, world, tuning.combat)
        elif not run_over_out:
            self._run = _start(tanks, world, tuning, terrain)
        if in_force is not None or self._run is None:
            self._note_skip(tanks, in_force, run_over_out, world, tuning)
        return [] if self._run is None else self._run.orders()

    def _note_skip(
        self, tanks: list[Unit], in_force: Run | None, run_over_out: bool, world: World, tuning: Tuning
    ) -> None:
        """Tell the cluster no run started over in this cycle (`skipped_cluster`), with the first gate that failed
        (`skip_gate`), unless one at its rounded centroid was told within SKIP_REPEAT_TICKS; the commander's run-over
        detachment out is a run in force."""
        cluster = skipped_cluster(in_force, world, tuning)
        if cluster is None:
            return
        tick = world.tick
        self._told = {cell: told for cell, told in self._told.items() if tick - told < SKIP_REPEAT_TICKS}
        rounded = _rounded(cluster.centroid)
        if rounded in self._told:
            return
        self._told[rounded] = tick
        is_running = in_force is not None or run_over_out
        _log_skip(cluster, skip_gate(cluster, tanks, is_running, world, tuning), world)

    def _keep_up(self, tanks: list[Unit], world: World, tuning: CombatTuning) -> None:
        """Let go of the run's tanks that are through, and end the run when it is over."""
        run = self._run
        run.keep_up({unit.id: unit for unit in tanks}, world)
        ended = run.ended(world, tuning)
        if ended is not None:
            log_run(run, ended, world)
            self._run = None


def without(units: frozenset[int], intents: list[Intent]) -> list[Intent]:
    """main's intents with `units` taken out of every one that is no emergency; one left with no unit is dropped."""
    kept = []
    for intent in intents:
        order = intent.order if intent.priority == Priority.EMERGENCY else order_without(intent.order, units)
        if order is intent.order:
            kept.append(intent)
        elif order is not None:
            kept.append(replace(intent, order=order))
    return kept


def order_without(order: Order | None, units: frozenset[int]) -> Order | None:
    """A unit order with `units` taken out of it: the order itself when it names none of them, None when it names no
    other unit, and None for none."""
    if order is None or units.isdisjoint(order["units"]):
        return order
    left = [unit_id for unit_id in order["units"] if unit_id not in units]
    return {**order, "units": left} if left else None


def run_first(intents: Iterable[Intent], run: Mapping[int, Cell]) -> list[Intent]:
    """The intents of a cycle with the tanks of the crush run (`run`, each with the cell it runs to) taken out of every
    order that gives a unit something to do but the run's own move to a tank's cell, whoever proposed it; an order left
    with no unit is dropped, and every other intent is kept as it was given."""
    if not run:
        return list(intents)
    kept = []
    for intent in intents:
        order = intent.order
        if order.get("cmd") not in ACTIVITY_COMMANDS:
            kept.append(intent)
            continue
        units = [unit_id for unit_id in order["units"] if unit_id not in run or _is_run_move(order, run[unit_id])]
        if len(units) == len(order["units"]):
            kept.append(intent)
        elif units:
            kept.append(replace(intent, order={**order, "units": units}))
    return kept


def _is_run_move(order: Mapping[str, Any], cell: Cell) -> bool:
    """Whether an order is a crush run's move to a cell: a `move` there that replaces what its units do."""
    return order["cmd"] == "move" and list(order["to"]) == list(cell) and not order.get("queued")


def is_crusher(unit_type: str, items: ItemTable) -> bool:
    """Whether a type of vehicle crushes infantry: a tank of the item table, or the APC."""
    facts = items.get(unit_type)
    return facts is not None and (TANK_ROLE in facts.roles or facts.name == APC)


def qualifying_cluster(tanks: list[Unit], world: World, tuning: Tuning) -> Cluster | None:
    """The cluster of enemy infantry the crushing vehicles among the units a run may take, `tanks`, are to run over in
    this cycle; None for none. See the module's note."""
    combat = tuning.combat
    crushers = [unit for unit in tanks if is_crusher(unit.type, world.items)]
    infantry = infantry_in_sight(world)
    if not crushers or len(infantry) < _fewest_infantry(combat):
        return None
    cluster = largest_cluster(infantry, combat.crush_cluster_cells)
    return cluster if _qualifies(cluster, crushers, world, tuning) else None


def infantry_in_sight(world: World) -> list[EnemyRecord]:
    """The enemy infantry in the latest state: the units in sight that the Infantry queue makes."""
    return [record for record in world.enemy_memory.values() if record.visible and _is_infantry(record, world.items)]


def largest_cluster(infantry: list[EnemyRecord], reach: float) -> Cluster | None:
    """The largest set of the infantry within `reach` cells of a centroid, sought around each unit in turn (the units
    within reach of it, then those within reach of their centroid), the first found of several as large; None for no
    infantry."""
    found = (_within(infantry, centroid(_within(infantry, (seed.x, seed.y), reach)), reach) for seed in infantry)
    largest = max(found, key=len, default=None)
    return None if largest is None else Cluster(tuple(largest), centroid(largest))


def local_ratio(middle: Point, world: World, tuning: Tuning) -> float:
    """The ratio of the own combat units within RATIO_REACH_CELLS of a point to the enemy combat units in sight within
    as many cells of it, weighed as `fight.ratio_against` weighs them."""
    own = [unit for unit in world.combat_units.values() if is_near(middle, unit.x, unit.y, RATIO_REACH_CELLS)]
    enemies = [
        record
        for record in world.enemy_memory.values()
        if record.visible and is_combat_unit(record) and is_near(middle, record.x, record.y, RATIO_REACH_CELLS)
    ]
    return ratio_against(own, enemies, world, tuning)


def is_anti_tank_heavy(cluster: Cluster, tuning: CombatTuning) -> bool:
    """Whether a cluster's rocket soldiers and shock troopers (ANTI_TANK_INFANTRY) make up crush_at_share or more of
    its infantry."""
    heavy = sum(1 for record in cluster.infantry if record.type in ANTI_TANK_INFANTRY)
    return heavy >= tuning.crush_at_share * len(cluster.infantry)


def min_infantry(cluster: Cluster, tuning: CombatTuning) -> int:
    """The fewest infantry a cluster is run over at: crush_at_min_infantry when it is anti-tank-heavy,
    crush_min_infantry otherwise."""
    return tuning.crush_at_min_infantry if is_anti_tank_heavy(cluster, tuning) else tuning.crush_min_infantry


def planned(cluster: Cluster, tuning: CombatTuning) -> int:
    """The planned count of a cluster's run: one tank for each crush_at_per_tanks infantry, crush_at_max_tanks at the
    most, when it is anti-tank-heavy, one for each crush_per_tanks, crush_max_tanks at the most, otherwise."""
    count = len(cluster.infantry)
    if is_anti_tank_heavy(cluster, tuning):
        return min(tuning.crush_at_max_tanks, math.ceil(count / tuning.crush_at_per_tanks))
    return min(tuning.crush_max_tanks, math.ceil(count / tuning.crush_per_tanks))


def committed(cluster: Cluster, tuning: CombatTuning) -> int:
    """How many tanks a run of the script's sends over a cluster, all of them or none: its planned count, never fewer
    than crush_min_tanks."""
    return max(tuning.crush_min_tanks, planned(cluster, tuning))


def _fewest_infantry(tuning: CombatTuning) -> int:
    """The fewest infantry any cluster is run over at, anti-tank-heavy or not."""
    return min(tuning.crush_min_infantry, tuning.crush_at_min_infantry)


def run_cells(
    cluster: Cluster,
    tanks: list[Unit],
    world: World,
    terrain: Terrain,
    tuning: CombatTuning,
    count: int | None = None,
) -> dict[int, Cell]:
    """The tanks among the units a run may take, `tanks`, that run over a cluster, by id, each with the cell it runs
    to: `count` of them at the most, or with no count given one for each crush_per_tanks of its infantry,
    crush_max_tanks at the most (the commander's run-over detachment's size), the nearest to its centroid first among
    the crushing vehicles within crush_reach_cells of it at crush_min_hp or more, a tank with no cell to run to passed
    over. See the module's note."""
    middle = cluster.centroid
    fit = sorted(
        (unit for unit in _crushers_near(middle, tanks, world, tuning) if unit.hp >= tuning.crush_min_hp),
        key=lambda unit: math.hypot(unit.x - middle[0], unit.y - middle[1]),
    )
    if count is None:
        count = min(tuning.crush_max_tanks, math.ceil(len(cluster.infantry) / tuning.crush_per_tanks))
    going = ((unit.id, far_side(unit, middle, world, terrain)) for unit in fit)
    return dict(itertools.islice(((unit_id, cell) for unit_id, cell in going if cell is not None), count))


def far_side(unit: Unit, middle: Point, world: World, terrain: Terrain) -> Cell | None:
    """The cell a tank runs to through a cluster: BEYOND_CELLS past its centroid on the line from the tank, kept
    inside the map and moved to the nearest cell the terrain does not block; None when no such cell is near."""
    span_x, span_y = middle[0] - unit.x, middle[1] - unit.y
    length = math.hypot(span_x, span_y)
    # A tank standing on the centroid has no far side: it runs to the centroid itself.
    share = BEYOND_CELLS / length if length > 0 else 0.0
    beyond = cells.nearest_cell(middle[0] + span_x * share, middle[1] + span_y * share)
    return open_cell(cells.clamped(beyond, world.map_bounds), world, terrain)


def open_cell(cell: Cell, world: World, terrain: Terrain) -> Cell | None:
    """The cell nearest to `cell`, itself first, within OPEN_CELL_SEARCH_CELLS of it, that lies in the map and that
    the terrain does not block; None for none."""
    near = ((cell[0] + dx, cell[1] + dy) for dx, dy in _NEAREST_FIRST)
    return next((found for found in near if world.in_bounds(*found) and found not in terrain.blocked), None)


def _start(tanks: list[Unit], world: World, tuning: Tuning, terrain: Terrain) -> Run | None:
    """A run over the cluster the crushing vehicles among `tanks` are to run over in this cycle, its `committed`
    tanks; None for none, or for fewer than the committed count to send."""
    combat = tuning.combat
    cluster = qualifying_cluster(tanks, world, tuning)
    if cluster is None:
        return None
    wanted = committed(cluster, combat)
    going = run_cells(cluster, tanks, world, terrain, combat, wanted)
    if len(going) < wanted:
        return None
    costs = {unit.id: cost(unit.type, world.items) for unit in tanks if unit.id in going}
    return Run(cluster, world.tick, going, tuple(going), costs=costs)


def log_run(run: Run, ended: str, world: World, **more: int) -> None:
    """Write the `combat` event of a run that has ended, with its trade: the infantry it killed and what they cost,
    the tanks it lost, and the one cost less the other; and the fields `more` gives, a crush wave's number (see
    `crush_wave`)."""
    killed_cost = sum(cost(record.type, world.items) for record in run.cluster.infantry if record.id in run.killed)
    lost_cost = sum(run.costs[unit_id] for unit_id in run.lost)
    data = {
        "tick": world.tick,
        "reason": CRUSH_RUN,
        "started_tick": run.started_tick,
        "cell": list(cells.nearest_cell(*run.cluster.centroid)),
        "cluster": len(run.cluster.infantry),
        "tanks": list(run.tanks),
        "infantry_killed": len(run.killed),
        "infantry_killed_cost": killed_cost,
        "tanks_lost": len(run.lost),
        "trade": killed_cost - lost_cost,
        "ended": ended,
        **more,
    }
    records.log(COMBAT_EVENT, data)


def cost(actor_type: str, items: ItemTable) -> int:
    """What an actor of a type costs; 0 for a type the item table does not know."""
    facts = items.get(actor_type)
    return 0 if facts is None else facts.cost


def skipped_cluster(in_force: Run | None, world: World, tuning: Tuning) -> Cluster | None:
    """The cluster a run could be wanted over in a cycle in which none started: the largest of the enemy infantry in
    sight, with as many units as it qualifies at (`min_infantry`) or more, within crush_reach_cells of any own crushing vehicle; None for none,
    and for the cluster of the run in force, which shares infantry with it."""
    combat = tuning.combat
    infantry = infantry_in_sight(world)
    is_enough = len(infantry) >= _fewest_infantry(combat)
    cluster = largest_cluster(infantry, combat.crush_cluster_cells) if is_enough else None
    if cluster is None or len(cluster.infantry) < min_infantry(cluster, combat):
        return None
    if not _is_near_own_crusher(cluster.centroid, world, combat.crush_reach_cells):
        return None
    is_run_over = in_force is not None and not _ids(cluster).isdisjoint(_ids(in_force.cluster))
    return None if is_run_over else cluster


def skip_gate(cluster: Cluster, tanks: list[Unit], is_running: bool, world: World, tuning: Tuning) -> str:
    """The first gate that kept a run from starting over a cluster, with the own units a run may take given:
    RUN_ACTIVE (`is_running`: a run in force, or the commander's run-over detachment out), NO_TANK_IN_MAIN, TANK_HP,
    TOO_FEW_TANKS, SHORT_GROUP, RATIO and COVER in that order, else OTHER."""
    if is_running:
        return RUN_ACTIVE
    return _tank_gate(cluster, tanks, world, tuning.combat) or _fight_gate(cluster, world, tuning) or OTHER


def _tank_gate(cluster: Cluster, tanks: list[Unit], world: World, tuning: CombatTuning) -> str | None:
    """NO_TANK_IN_MAIN when none of the crushing vehicles among `tanks` is within crush_reach_cells of a cluster's
    centroid, TANK_HP when none of those within it has crush_min_hp, TOO_FEW_TANKS when fewer than crush_min_tanks of
    them have it, SHORT_GROUP when fewer than its committed count have it; None when none of these holds."""
    near = _crushers_near(cluster.centroid, tanks, world, tuning)
    if not near:
        return NO_TANK_IN_MAIN
    fit = sum(1 for unit in near if unit.hp >= tuning.crush_min_hp)
    if not fit:
        return TANK_HP
    if fit < tuning.crush_min_tanks:
        return TOO_FEW_TANKS
    return SHORT_GROUP if fit < committed(cluster, tuning) else None


def _crushers_near(middle: Point, units: list[Unit], world: World, tuning: CombatTuning) -> list[Unit]:
    """The crushing vehicles among some units that stand within crush_reach_cells of a point."""
    return [
        unit
        for unit in units
        if is_near(middle, unit.x, unit.y, tuning.crush_reach_cells) and is_crusher(unit.type, world.items)
    ]


def _fight_gate(cluster: Cluster, world: World, tuning: Tuning) -> str | None:
    """RATIO when the fight around a cluster is below crush_min_ratio, COVER when a remembered defense that fires at
    ground units covers its centroid; None when neither holds."""
    middle = cluster.centroid
    if local_ratio(middle, world, tuning) < tuning.combat.crush_min_ratio:
        return RATIO
    return COVER if coverage.is_covered(middle, ground_defenses(world), world.items, tuning.coverage) else None


def _is_near_own_crusher(middle: Point, world: World, reach: float) -> bool:
    """Whether one of the own crushing vehicles, of any group, stands within `reach` cells of a point."""
    return any(
        is_near(middle, unit.x, unit.y, reach) and is_crusher(unit.type, world.items)
        for unit in world.combat_units.values()
    )


def _ids(cluster: Cluster) -> set[int]:
    """The ids of a cluster's infantry."""
    return {record.id for record in cluster.infantry}


def _rounded(point: Point) -> Cell:
    """A point rounded to the nearest corner of the grid of SKIP_CELLS cells."""
    return round(point[0] / SKIP_CELLS) * SKIP_CELLS, round(point[1] / SKIP_CELLS) * SKIP_CELLS


def _log_skip(cluster: Cluster, gate: str, world: World) -> None:
    """Write the `combat` event of a skipped cluster: the tick, the cell of its centroid, its size and the gate."""
    data = {
        "tick": world.tick,
        "reason": CRUSH_SKIPPED,
        "cell": list(cells.nearest_cell(*cluster.centroid)),
        "cluster": len(cluster.infantry),
        "gate": gate,
    }
    records.log(COMBAT_EVENT, data)


def _qualifies(cluster: Cluster, crushers: list[Unit], world: World, tuning: Tuning) -> bool:
    """Whether a cluster is to be run over: large enough for its kind, near a crushing vehicle, under no defense that
    fires at ground units and in a fight the agent has the advantage in."""
    combat, middle = tuning.combat, cluster.centroid
    return (
        len(cluster.infantry) >= min_infantry(cluster, combat)
        and any(is_near(middle, unit.x, unit.y, combat.crush_reach_cells) for unit in crushers)
        and not coverage.is_covered(middle, ground_defenses(world), world.items, tuning.coverage)
        and local_ratio(middle, world, tuning) >= combat.crush_min_ratio
    )


def _is_infantry(record: EnemyRecord, items: ItemTable) -> bool:
    """Whether a remembered enemy is a soldier: of a type the Infantry queue makes, or anti-tank infantry, the shock
    trooper among it, which the item table does not list."""
    facts = items.get(record.type)
    return record.type in ANTI_TANK_INFANTRY or (facts is not None and facts.queue == INFANTRY_QUEUE)


def _within(enemies: list[EnemyRecord], middle: Point, reach: float) -> list[EnemyRecord]:
    """The remembered enemies among some within `reach` cells of a point."""
    return [record for record in enemies if is_near(middle, record.x, record.y, reach)]


def _nearest_first(reach: int) -> tuple[Cell, ...]:
    """The offsets of the cells at most `reach` cells from a cell, nearest first, then by row and by column."""
    offsets = [(dx, dy) for dy in range(-reach, reach + 1) for dx in range(-reach, reach + 1)]
    return tuple(sorted(((dx, dy) for dx, dy in offsets if dx * dx + dy * dy <= reach * reach), key=_offset_order))


def _offset_order(offset: Cell) -> tuple[int, int, int]:
    dx, dy = offset
    return dx * dx + dy * dy, dy, dx


_NEAREST_FIRST = _nearest_first(OPEN_CELL_SEARCH_CELLS)
