"""The crush wave: against an enemy infantry all-in, every fit tank near gathers outside the mass and drives through it
together.

A crush run of the script's (see `crush`) sends three tanks, five at the most, over a cluster of infantry within 3
cells of its centroid, one run at a time and each tank from wherever it stands: against an all-in of 15 to 25
infantry the tanks went in two or three at a time, seconds apart, and were shot one by one. With `crush_wave` of
`[combat]` on (and `crush_runs` on), an all-in is met by one wave of every fit tank near instead.

**The all-in.** The mass is the largest set of the enemy infantry in sight within wave_mass_cells of its centroid
(`crush.largest_cluster`). An all-in is under way when it has wave_min_infantry units or more and its centroid lies
within wave_base_cells of the base's center or within wave_army_cells of one of the crushing vehicles a run may take.

**Forming.** The wave's tanks are those crushing vehicles (`crush.is_crusher`) within wave_reach_cells of the mass's
centroid with crush_min_hp percent of their health or more, wave_max_tanks at the most, the nearest first. The wave
forms only with wave_min_tanks of them, the mass's centroid in the coverage of no remembered ground-firing defense
(`coverage.is_covered`), and a ratio at the centroid (`crush.local_ratio`'s, the wave's tanks counted wherever they
stand) of crush_min_ratio or more; otherwise the script's runs go on as ever. The gather point lies on the ray from the
mass's centroid toward the tanks' centroid, wave_gather_cells from the mass, on open ground; in a defense's coverage it
is moved on along the ray a cell at a time, `GATHER_SHIFT_CELLS` at the most, and with no uncovered cell the wave does
not form. It is fixed when the wave forms. Each tank is moved there at CONTACT in every cycle until it is present,
within wave_gather_radius_cells of it, where it holds.

**Launch.** The wave launches in the first cycle in which wave_ready_share of its tanks still alive and fit are
present (`ready`), or wave_max_wait_seconds after it formed (`timed_out`), always with wave_second_min_tanks present
at the least: with fewer at the time limit it dissolves, its tanks back to their group's orders. Launching, every
present tank is given its crush move in the same cycle: along the axis from the gather point to the mass's centroid as
it stands then, the tanks in their order across the axis on lanes wave_lane_cells apart (wave_mass_cells off the axis
at the most), each to the cell wave_beyond_cells past the mass on its lane, on open ground and pulled back along the
axis out of any defense's coverage; a tank with no such cell is not sent.

**The wave run.** The tanks sent are the one crush run while it lasts (`MatchContext.crush_run`): no tank of it is
released on arrival, it holds at its cell instead, and the run ends for all of them in one cycle, after
wave_run_seconds (`timed_out`), once every tank is at its cell or lost (`arrived`), or once fewer than
crush_min_infantry of the mass's infantry are in sight within wave_mass_cells of its centroid at launch
(`cluster_broken`).

**Stragglers.** The wave's tanks not present at launch, and the fit crushing vehicles that come within
wave_reach_cells of the mass while the first wave forms or runs, are the second wave: they gather at the same point,
timed from the first launch by the same rules, and launch only with wave_second_min_tanks present; no straggler is
ever sent alone. There is at most one second wave to an all-in.

**One crush at a time.** While a wave forms, waits or runs, its tanks and their gather or target cells are the crush
run published: no script run starts, the model commander offers no run-over, base defense and every other manager
give its tanks no order (`crush.run_first`), and repair mode takes none of them; only an emergency of main's reaches a
tank, which leaves the wave. No wave forms while the commander's run-over detachment or a script run is out.

**Records.** Each launch is a `combat` event of reason `crush_wave`: the wave (1 or 2), the mass's size, the gather
cell, the tanks sent, the tanks present and the wave's tanks in all, the seconds waited and how it launched (`ready` or
`timed_out`); a wave that dissolves writes one with `launch` `dissolved`. Each wave run's end writes the run's
`crush_run` event (see `crush`) with its `wave`.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, coverage, crush
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.fight import Point, centroid, ground_defenses, is_near, ratio_against
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.world import Cell, Unit, World

COMBAT_EVENT = "combat"
CRUSH_WAVE = "crush_wave"
# How a wave launched, or that it dissolved.
READY = "ready"
TIMED_OUT = "timed_out"
DISSOLVED = "dissolved"
FIRST = 1
SECOND = 2
# A gather point in a defense's coverage is moved on along its ray this many cells at the most.
GATHER_SHIFT_CELLS = 6
_SECONDS_DECIMALS = 1


@dataclass
class _Forming:
    """A wave gathering: its number, the mass it formed on, its fixed gather point, its tanks, the tick its clock
    started, and the crushing vehicles that came late, which hold at the point for the second wave."""

    number: int
    mass: crush.Cluster
    gather: Cell
    tanks: list[int]
    since: int
    late: list[int] = field(default_factory=list)


@dataclass
class _Running:
    """A wave run: its number, the run's record (`crush.Run`), each tank's target cell, and the mass's centroid at
    launch."""

    number: int
    run: crush.Run
    targets: dict[int, Cell]
    center: Point


def all_in(tanks: Sequence[Unit], world: World, tuning: Tuning) -> crush.Cluster | None:
    """The mass of an infantry all-in under way: the largest set of the enemy infantry in sight within wave_mass_cells
    of its centroid, of wave_min_infantry or more, near the base or our crushing vehicles; None for none."""
    combat = tuning.combat
    infantry = crush.infantry_in_sight(world)
    if len(infantry) < combat.wave_min_infantry:
        return None
    mass = crush.largest_cluster(infantry, combat.wave_mass_cells)
    if mass is None or len(mass.infantry) < combat.wave_min_infantry:
        return None
    middle = mass.centroid
    base = world.base_center
    near_base = base is not None and is_near(base, middle[0], middle[1], combat.wave_base_cells)
    near_tanks = any(
        crush.is_crusher(unit.type, world.items) and is_near(middle, unit.x, unit.y, combat.wave_army_cells)
        for unit in tanks
    )
    return mass if near_base or near_tanks else None


def fit_tanks(middle: Point, tanks: Iterable[Unit], world: World, tuning: Tuning) -> list[Unit]:
    """The crushing vehicles within wave_reach_cells of a point with crush_min_hp or more, the nearest first,
    wave_max_tanks at the most."""
    combat = tuning.combat
    fit = [
        unit
        for unit in tanks
        if crush.is_crusher(unit.type, world.items)
        and unit.hp >= combat.crush_min_hp
        and is_near(middle, unit.x, unit.y, combat.wave_reach_cells)
    ]
    fit.sort(key=lambda unit: (math.hypot(unit.x - middle[0], unit.y - middle[1]), unit.id))
    return fit[: combat.wave_max_tanks]


def is_covered(cell: Point, world: World, tuning: Tuning) -> bool:
    """Whether a remembered ground-firing enemy defense covers a cell; an air-only one covers nothing."""
    return coverage.is_covered(cells.nearest_cell(*cell), ground_defenses(world), world.items, tuning.coverage)


def may_form(mass: crush.Cluster, wave: Sequence[Unit], world: World, tuning: Tuning) -> bool:
    """Whether a wave forms over a mass: wave_min_tanks tanks, the mass uncovered, and a ratio at its centroid of
    crush_min_ratio or more, the wave's tanks counted wherever they stand."""
    combat = tuning.combat
    if len(wave) < combat.wave_min_tanks or is_covered(mass.centroid, world, tuning):
        return False
    return _ratio(mass.centroid, wave, world, tuning) >= combat.crush_min_ratio


def gather_point(middle: Point, wave: Sequence[Unit], world: World, terrain: Terrain, tuning: Tuning) -> Cell | None:
    """The wave's gather point: wave_gather_cells from the mass's centroid toward the tanks' centroid on open ground,
    moved on along that ray a cell at a time out of any defense's coverage, GATHER_SHIFT_CELLS at the most; None when
    none is found."""
    tanks = centroid(list(wave))
    heading = _heading(middle, tanks) or _heading(middle, (middle[0] - 1, middle[1]))
    reach = tuning.combat.wave_gather_cells
    for extra in range(GATHER_SHIFT_CELLS + 1):
        distance = reach + extra
        point = (middle[0] + heading[0] * distance, middle[1] + heading[1] * distance)
        cell = _open(point, world, terrain)
        if cell is not None and not is_covered(cell, world, tuning):
            return cell
    return None


def lane_targets(
    present: Sequence[Unit], gather: Cell, middle: Point, world: World, terrain: Terrain, tuning: Tuning
) -> dict[int, Cell]:
    """Each present tank's target cell, by id: on its lane wave_lane_cells from the next across the axis from the
    gather point to the mass's centroid, wave_beyond_cells past the centroid, on open ground and pulled back along the
    axis out of any defense's coverage; a tank with no such cell is left out."""
    combat = tuning.combat
    axis = _heading(gather, middle) or (1.0, 0.0)
    across = (-axis[1], axis[0])
    ordered = sorted(present, key=lambda unit: ((unit.x - gather[0]) * across[0] + (unit.y - gather[1]) * across[1]))
    half = (len(ordered) - 1) / 2
    targets: dict[int, Cell] = {}
    for index, unit in enumerate(ordered):
        offset = max(-combat.wave_mass_cells, min(combat.wave_mass_cells, (index - half) * combat.wave_lane_cells))
        lane = (middle[0] + across[0] * offset, middle[1] + across[1] * offset)
        cell = _target_on(lane, axis, world, terrain, tuning)
        if cell is not None:
            targets[unit.id] = cell
    return targets


def _target_on(lane: Point, axis: Point, world: World, terrain: Terrain, tuning: Tuning) -> Cell | None:
    """The target cell on a lane: wave_beyond_cells past the mass along the axis, pulled back a cell at a time to the
    first open cell out of any defense's coverage, no farther back than the lane's start; None for none."""
    beyond = tuning.combat.wave_beyond_cells
    for back in range(beyond + tuning.combat.wave_gather_cells + 1):
        reach = beyond - back
        cell = _open((lane[0] + axis[0] * reach, lane[1] + axis[1] * reach), world, terrain)
        if cell is not None and not is_covered(cell, world, tuning):
            return cell
    return None


class CrushWave:
    """The wave of tanks forming, waiting or running over an infantry all-in. See the module's note."""

    def __init__(self) -> None:
        self._forming: _Forming | None = None
        self._runs: list[_Running] = []
        # The tanks launched on the all-in under way, which no later wave of it takes again.
        self._launched: set[int] = set()
        # Whether the wave ended in the latest cycle: no other crush starts in that cycle.
        self.ended_now = False

    @property
    def is_active(self) -> bool:
        """Whether a wave forms, waits or runs: it is the one crush run."""
        return self._forming is not None or bool(self._runs)

    @property
    def units(self) -> frozenset[int]:
        """The tanks of the wave forming and of its runs, which take no order of main's but an emergency."""
        return frozenset(self._cells())

    def held(self, orders: Iterable[Intent]) -> dict[int, Cell]:
        """The wave's tanks, each with its gather or target cell, but those an emergency among main's `orders` names,
        which leave the wave."""
        urgent = {
            unit_id
            for intent in orders
            if intent.priority == Priority.EMERGENCY
            for unit_id in intent.order.get("units", ())
        }
        self._release(urgent)
        return self._cells()

    def follow(
        self, tanks: Sequence[Unit], world: World, tuning: Tuning, terrain: Terrain, blocked: bool
    ) -> list[Intent]:
        """The wave's orders in this cycle, the own units a crush run may take given: its runs kept up and ended, the
        wave forming kept up and launched or dissolved, or a wave formed over an all-in unless another crush is out
        (`blocked`); none with crush_wave or crush_runs off. Whether the wave ended in the cycle is `ended_now`."""
        if not (tuning.combat.crush_wave and tuning.combat.crush_runs):
            self._forming, self._runs = None, []
            return []
        was_active = self.is_active
        by_id = {unit.id: unit for unit in tanks}
        self._keep_up_runs(by_id, world, tuning)
        if self._forming is not None:
            self._keep_up_forming(tanks, by_id, world, tuning, terrain)
        elif not self._runs and not blocked:
            self._form(tanks, world, tuning, terrain)
        self.ended_now = was_active and not self.is_active
        return self._orders(by_id)

    def _form(self, tanks: Sequence[Unit], world: World, tuning: Tuning, terrain: Terrain) -> None:
        """Form the first wave over an all-in under way when it may (see `may_form`) and has a gather point."""
        mass = all_in(tanks, world, tuning)
        if mass is None:
            return
        wave = fit_tanks(mass.centroid, tanks, world, tuning)
        if not may_form(mass, wave, world, tuning):
            return
        gather = gather_point(mass.centroid, wave, world, terrain, tuning)
        if gather is not None:
            self._forming = _Forming(FIRST, mass, gather, [unit.id for unit in wave], world.tick)

    def _keep_up_forming(
        self, tanks: Sequence[Unit], by_id: Mapping[int, Unit], world: World, tuning: Tuning, terrain: Terrain
    ) -> None:
        """The wave forming brought up to date: its tanks gone or unfit dropped, the late ones noted, and its launch
        or dissolution when due."""
        forming, combat = self._forming, tuning.combat
        fit = {unit.id for unit in fit_tanks(forming.mass.centroid, tanks, world, tuning)}
        is_fit = {unit_id for unit_id, unit in by_id.items() if unit.hp >= combat.crush_min_hp}
        forming.tanks = [unit_id for unit_id in forming.tanks if unit_id in is_fit]
        taken = set(forming.tanks) | set(forming.late) | self._launched
        forming.late = [unit_id for unit_id in forming.late if unit_id in is_fit]
        newcomers = [unit_id for unit_id in fit if unit_id not in taken]
        if forming.number == SECOND:
            forming.tanks.extend(newcomers[: max(0, combat.wave_max_tanks - len(forming.tanks))])
        else:
            forming.late.extend(newcomers[: max(0, combat.wave_max_tanks - len(forming.late))])
        present = [by_id[unit_id] for unit_id in forming.tanks if self._is_present(by_id[unit_id], tuning)]
        reason = self._launch_reason(forming, present, world.tick, tuning)
        if reason == DISSOLVED:
            self._dissolve(forming, world)
        elif reason is not None:
            self._launch(forming, present, by_id, reason, world, tuning, terrain)

    def _dissolve(self, forming: _Forming, world: World) -> None:
        """The wave forming dissolves, its tanks back to their group's orders, with a `crush_wave` event when it had
        any."""
        if forming.tanks:
            _log_launch(forming, [], [], DISSOLVED, world)
        self._forming = None

    def _is_present(self, unit: Unit, tuning: Tuning) -> bool:
        """Whether a tank of the wave forming stands within wave_gather_radius_cells of its gather point."""
        gather = self._forming.gather
        return is_near(gather, unit.x, unit.y, tuning.combat.wave_gather_radius_cells)

    @staticmethod
    def _launch_reason(forming: _Forming, present: list[Unit], tick: int, tuning: Tuning) -> str | None:
        """Why the wave forming launches in this cycle (READY, TIMED_OUT), or DISSOLVED at the time limit with too
        few present; None while it waits."""
        combat = tuning.combat
        enough = len(present) >= combat.wave_second_min_tanks
        if enough and len(present) >= combat.wave_ready_share * len(forming.tanks):
            return READY
        if tick - forming.since < combat.wave_max_wait_seconds * TICKS_PER_SECOND:
            return None
        return TIMED_OUT if enough else DISSOLVED

    def _launch(
        self,
        forming: _Forming,
        present: list[Unit],
        by_id: Mapping[int, Unit],
        reason: str,
        world: World,
        tuning: Tuning,
        terrain: Terrain,
    ) -> None:
        """Launch the present tanks of the wave forming on their lanes through the mass as it stands now, in one
        cycle, with the `crush_wave` event; the rest of its tanks and the late ones the second wave after a first."""
        mass = self._mass_now(forming, tuning, world)
        targets = lane_targets(present, forming.gather, mass.centroid, world, terrain, tuning)
        if len(targets) < tuning.combat.wave_second_min_tanks:
            # Too few have a cell to run to: the wave waits, and dissolves at its time limit.
            if reason == TIMED_OUT:
                self._dissolve(forming, world)
            return
        sent = [unit_id for unit_id in forming.tanks if unit_id in targets]
        costs = {unit_id: crush.cost(by_id[unit_id].type, world.items) for unit_id in sent}
        run = crush.Run(mass, world.tick, dict(targets), tuple(sent), costs=costs)
        self._runs.append(_Running(forming.number, run, targets, mass.centroid))
        self._launched.update(sent)
        _log_launch(forming, sent, present, reason, world, mass)
        left = [unit_id for unit_id in [*forming.tanks, *forming.late] if unit_id not in targets]
        left = left[: tuning.combat.wave_max_tanks]
        self._forming = None
        if forming.number == FIRST:
            self._forming = _Forming(SECOND, forming.mass, forming.gather, left, world.tick)

    @staticmethod
    def _mass_now(forming: _Forming, tuning: Tuning, world: World) -> crush.Cluster:
        """The mass as it stands now: the largest within wave_mass_cells in sight near the one the wave formed on, or
        that one when none is."""
        reach = tuning.combat.wave_mass_cells
        found = crush.largest_cluster(crush.infantry_in_sight(world), reach)
        middle = forming.mass.centroid
        if found is None or not is_near(middle, found.centroid[0], found.centroid[1], tuning.combat.wave_reach_cells):
            return forming.mass
        return found

    def _keep_up_runs(self, by_id: Mapping[int, Unit], world: World, tuning: Tuning) -> None:
        """The wave runs brought up to date: the mass's infantry killed and the tanks lost counted, the tanks gone
        from the units a run may take let go, and every run that is over ended with its `crush_run` event."""
        kept = []
        for running in self._runs:
            run = running.run
            run.killed |= world.killed_in_sight & {record.id for record in run.cluster.infantry}
            run.lost |= {unit_id for unit_id in run.costs if unit_id not in world.units}
            running.targets = {unit_id: cell for unit_id, cell in running.targets.items() if unit_id in by_id}
            ended = _ended(running, by_id, world, tuning)
            if ended is None:
                kept.append(running)
            else:
                crush.log_run(run, ended, world, wave=running.number)
        self._runs = kept
        if self._forming is not None and self._forming.number == SECOND and not self._runs and not self._forming.tanks:
            self._forming = None
        if self._forming is None and not self._runs:
            self._launched = set()

    def _orders(self, by_id: Mapping[int, Unit]) -> list[Intent]:
        """A `move` at CONTACT for the tanks of the wave forming not present to its gather point, and for the tanks of
        its runs not at their target cell to it; one order to each cell."""
        going: dict[int, Cell] = {}
        forming = self._forming
        if forming is not None:
            for unit_id in [*forming.tanks, *forming.late]:
                unit = by_id.get(unit_id)
                if unit is not None and not is_near(forming.gather, unit.x, unit.y, 0):
                    going[unit_id] = forming.gather
        for running in self._runs:
            for unit_id, cell in running.targets.items():
                unit = by_id[unit_id]
                if not is_near(cell, unit.x, unit.y, crush.ARRIVED_CELLS):
                    going[unit_id] = cell
        by_cell: dict[Cell, list[int]] = {}
        for unit_id, cell in going.items():
            by_cell.setdefault(cell, []).append(unit_id)
        return [
            Intent(Priority.CONTACT, {"cmd": "move", "units": ids, "to": list(cell)}) for cell, ids in by_cell.items()
        ]

    def _cells(self) -> dict[int, Cell]:
        """Each tank of the wave with its gather or target cell."""
        held: dict[int, Cell] = {}
        forming = self._forming
        if forming is not None:
            held.update(dict.fromkeys([*forming.tanks, *forming.late], forming.gather))
        for running in self._runs:
            held.update(running.targets)
        return held

    def _release(self, unit_ids: set[int]) -> None:
        """Let tanks leave the wave: an emergency of main's took them."""
        if not unit_ids:
            return
        forming = self._forming
        if forming is not None:
            forming.tanks = [unit_id for unit_id in forming.tanks if unit_id not in unit_ids]
            forming.late = [unit_id for unit_id in forming.late if unit_id not in unit_ids]
        for running in self._runs:
            running.targets = {unit_id: cell for unit_id, cell in running.targets.items() if unit_id not in unit_ids}


def _ended(running: _Running, by_id: Mapping[int, Unit], world: World, tuning: Tuning) -> str | None:
    """Why a wave run is over in this cycle; None while it goes on."""
    combat, run = tuning.combat, running.run
    if world.tick - run.started_tick >= combat.wave_run_seconds * TICKS_PER_SECOND:
        return crush.TIMED_OUT
    members = {record.id for record in run.cluster.infantry}
    staying = [
        record
        for record in crush.infantry_in_sight(world)
        if record.id in members and is_near(running.center, record.x, record.y, combat.wave_mass_cells)
    ]
    if len(staying) < combat.crush_min_infantry:
        return crush.CLUSTER_BROKEN
    arrived = all(
        is_near(cell, by_id[unit_id].x, by_id[unit_id].y, crush.ARRIVED_CELLS)
        for unit_id, cell in running.targets.items()
    )
    return crush.ARRIVED if arrived else None


def _ratio(middle: Point, wave: Sequence[Unit], world: World, tuning: Tuning) -> float:
    """`crush.local_ratio` at a point with the wave's tanks counted wherever they stand."""
    reach = crush.RATIO_REACH_CELLS
    own = {unit.id: unit for unit in world.combat_units.values() if is_near(middle, unit.x, unit.y, reach)}
    own.update((unit.id, unit) for unit in wave)
    enemies = [
        record
        for record in world.enemy_memory.values()
        if record.visible and is_combat_unit(record) and is_near(middle, record.x, record.y, reach)
    ]
    return ratio_against(list(own.values()), enemies, world, tuning)


def _open(point: Point, world: World, terrain: Terrain) -> Cell | None:
    """The open cell nearest to a point on the map (see `crush._open_cell`); None for none."""
    cell = cells.nearest_cell(*point)
    if world.map_bounds is not None:
        cell = cells.clamped(cell, world.map_bounds)
    return crush.open_cell(cell, world, terrain)


def _heading(origin: Point, aim: Point) -> Point | None:
    """The unit vector from `origin` toward `aim`; None when the two are one point."""
    span = math.hypot(aim[0] - origin[0], aim[1] - origin[1])
    if span == 0:
        return None
    return (aim[0] - origin[0]) / span, (aim[1] - origin[1]) / span


def _log_launch(
    forming: _Forming,
    sent: list[int],
    present: list[Unit],
    launch: str,
    world: World,
    mass: crush.Cluster | None = None,
) -> None:
    """Write the `crush_wave` event of a launch or a dissolution."""
    data = {
        "tick": world.tick,
        "reason": CRUSH_WAVE,
        "wave": forming.number,
        "mass": len((mass or forming.mass).infantry),
        "gather": list(forming.gather),
        "tanks": sent,
        "present": len(present),
        "total": len(forming.tanks),
        "waited_s": round((world.tick - forming.since) / TICKS_PER_SECOND, _SECONDS_DECIMALS),
        "launch": launch,
    }
    records.log(COMBAT_EVENT, data)
