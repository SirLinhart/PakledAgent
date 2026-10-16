"""Part of main's idle units sent to a fight of ours that is going our way (`[combat] reinforce_winning`).

Units of ours fighting away from main, a raid's rockets on an enemy refinery
or a group that met the enemy, are left to it while main stands idle near:
a fight won slowly, or a refinery left standing. So with `reinforce_winning`
on the combat manager watches the fights of the own units that are not
main's idle units:

- **A fight to join.** An own combat unit in contact with an enemy in sight
  (`targeting.in_contact`), a unit or a building, makes a fight at its cell:
  the own units in contact within reinforce_winning_local_cells of it, against
  the enemy units that fight within that reach and the ground defenses that
  reach the cell (`fight.weigh`), and the enemy's other buildings there at
  their cost times their share of health. It goes our way when nothing there
  fights back or our side is at least reinforce_winning_ratio as strong, and
  is worth joining when the enemy that fights there is worth
  reinforce_winning_min_value or more, or an enemy building stands there.
- **The helpers.** main's units that stand idle within reinforce_winning_cells
  of it, while main is home (no attack out, none gone forward) and the base
  is under no major threat, the nearest first: reinforce_winning_share of
  them, rounded down, so that main never sends its last idle unit. A part of
  main is sent, not just enough: a fight joined in force is won before the
  enemy can turn it, and a refinery falls fast. They attack-move to the
  fight's cell, again whenever one stands idle away from it; one idle there
  with no enemy unit in reach attacks the nearest enemy building there, so
  that a refinery is finished.
- **The end.** The job ends `done` once no enemy unit, defense or building is
  left there, `losing` once our side there, the helpers in, is under
  reinforce_winning_stop_ratio as strong as what fights back, `lost` with no
  helper left, or `timeout` after reinforce_winning_max_seconds; the helpers
  are main's again and gather with it. One job at a time; the helpers take no
  order of main's meanwhile.

Each start and end is a `combat` event of reason `reinforce_winning`.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, targeting
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.command_targets import reaches
from ucla_agent.games.redalert.fight import ground_defenses, is_counted, weigh, worth
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MAJOR_THREAT, MatchContext
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.world import DEFENSE_TYPES, Cell, EnemyRecord, Unit, World

COMBAT_EVENT = "combat"
REINFORCE_WINNING = "reinforce_winning"
# Why a job ends.
DONE, LOSING, LOST, TIMEOUT = "done", "losing", "lost", "timeout"
# A helper this near the fight's cell has arrived there.
ARRIVED_CELLS = 4


@dataclass(frozen=True)
class Fight:
    """A fight of ours: its cell, our side's strength there, what fights back, and the enemy's buildings' worth."""

    cell: Cell
    own: float
    enemy: float
    buildings: float

    @property
    def total(self) -> float:
        return self.enemy + self.buildings


@dataclass
class Job:
    """The helpers sent to a fight: its cell, their ids and the tick they set out."""

    cell: Cell
    units: frozenset[int]
    since: int


class WinningReinforce:
    """The one job of helpers out, if any. See the module's note."""

    def __init__(self) -> None:
        self.job: Job | None = None

    @property
    def units(self) -> frozenset[int]:
        """The helpers out, which take no order of main's."""
        return frozenset() if self.job is None else self.job.units

    def follow(self, idle: Sequence[Unit], is_home: bool, world: World, ctx: MatchContext) -> list[Intent]:
        """The helpers' orders of this cycle: the job out followed, or one started with `idle`, main's idle units free
        to help while `is_home`; none with reinforce_winning off."""
        if not ctx.tuning.combat.reinforce_winning:
            return []
        if self.job is not None:
            return self._follow_job(world, ctx)
        if not is_home or ctx.threat_level == MAJOR_THREAT or not idle:
            return []
        return self._start(idle, world, ctx)

    def _start(self, idle: Sequence[Unit], world: World, ctx: MatchContext) -> list[Intent]:
        """Start a job on the first fight to join that has helpers within reach, with its event and the orders."""
        tuning = ctx.tuning.combat
        others = [unit for unit in world.combat_units.values() if unit.id not in {helper.id for helper in idle}]
        for fight in fights(others, world, ctx):
            reach = tuning.reinforce_winning_cells
            near = [unit for unit in idle if cells.is_within((unit.x, unit.y), fight.cell, reach)]
            helpers = _helpers(fight, near, ctx)
            if helpers:
                self.job = Job(fight.cell, frozenset(unit.id for unit in helpers), world.tick)
                _log(world.tick, "start", fight, units=sorted(self.job.units))
                return self._orders(world)
        return []

    def _follow_job(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The job's orders, or none once it ends (with its event)."""
        job = self.job
        job.units = frozenset(
            unit_id for unit_id in job.units if unit_id in world.combat_units and not ctx.groups.is_overridden(unit_id)
        )
        helpers = [world.combat_units[unit_id] for unit_id in sorted(job.units)]
        fight = weighed(job.cell, helpers, world, ctx)
        why = _ended(job, fight, helpers, world, ctx)
        if why is not None:
            _log(world.tick, "end", fight, why=why, units=sorted(job.units))
            self.job = None
            return []
        return self._orders(world)

    def _orders(self, world: World) -> list[Intent]:
        """An attack-move to the fight's cell for each helper idle away from it or just sent, and an attack on the
        nearest enemy building there for each idle at it with no enemy unit in its reach."""
        job = self.job
        helpers = [world.combat_units[unit_id] for unit_id in sorted(job.units)]
        is_new = job.since == world.tick
        going = [
            unit.id
            for unit in helpers
            if is_new or (unit.idle and not cells.is_within((unit.x, unit.y), job.cell, ARRIVED_CELLS))
        ]
        orders = [Intent(Priority.MOVE, {"cmd": "attack_move", "units": going, "to": list(job.cell)})] if going else []
        return [*orders, *_finishing(helpers, going, job.cell, world)]


def fights(units: Sequence[Unit], world: World, ctx: MatchContext) -> list[Fight]:
    """The fights of these units that go our way and are worth joining, the most worth first: one at the cell of each
    unit in contact with an enemy in sight, those within reinforce_winning_local_cells of a fight's cell its own."""
    tuning = ctx.tuning.combat
    reach = tuning.reinforce_winning_local_cells
    in_contact = targeting.in_contact(units, world)
    found: list[Fight] = []
    for unit in sorted(in_contact, key=lambda unit: unit.id):
        if any(cells.is_within((unit.x, unit.y), fight.cell, reach) for fight in found):
            continue
        cell = (unit.x, unit.y)
        side = [other for other in in_contact if cells.is_within((other.x, other.y), cell, reach)]
        found.append(weighed(cell, side, world, ctx))
    winning = [fight for fight in found if _goes_our_way(fight, ctx) and _is_worth_joining(fight, ctx)]
    return sorted(winning, key=lambda fight: -fight.total)


def weighed(cell: Cell, own: Sequence[Unit], world: World, ctx: MatchContext) -> Fight:
    """A fight at a cell: `own` against the enemy units that fight within reinforce_winning_local_cells of it and the
    ground defenses that reach it, and the enemy's other buildings there at their worth."""
    reach = ctx.tuning.combat.reinforce_winning_local_cells
    enemies = [
        record
        for record in world.enemy_memory.values()
        if not record.is_building
        and is_counted(record)
        and is_combat_unit(record)
        and cells.is_within((record.x, record.y), cell, reach)
    ]
    defenses = [defense for defense in ground_defenses(world) if reaches(defense, cell, world, ctx)]
    ours, enemy = weigh(list(own), enemies, world, ctx.tuning, defenses)
    buildings = sum(worth(record.type, record.hp, world) for record in _buildings_near(cell, world, reach))
    return Fight(cell, ours, enemy, buildings)


def _goes_our_way(fight: Fight, ctx: MatchContext) -> bool:
    """Whether nothing there fights back, or our side is at least reinforce_winning_ratio as strong."""
    return fight.enemy <= 0 or fight.own >= ctx.tuning.combat.reinforce_winning_ratio * fight.enemy


def _is_worth_joining(fight: Fight, ctx: MatchContext) -> bool:
    """Whether the enemy that fights there is worth reinforce_winning_min_value or more, or an enemy building stands
    there to be finished."""
    return fight.enemy >= ctx.tuning.combat.reinforce_winning_min_value or fight.buildings > 0


def _helpers(fight: Fight, near: Sequence[Unit], ctx: MatchContext) -> list[Unit]:
    """The idle units sent to a fight: the nearest reinforce_winning_share of those near, rounded down."""
    most = math.floor(ctx.tuning.combat.reinforce_winning_share * len(near))
    return sorted(near, key=lambda unit: (cells.distance((unit.x, unit.y), fight.cell), unit.id))[:most]


def _ended(job: Job, fight: Fight, helpers: Sequence[Unit], world: World, ctx: MatchContext) -> str | None:
    """Why a job ends in this cycle; None while it goes on."""
    tuning = ctx.tuning.combat
    if not helpers:
        return LOST
    if fight.total <= 0:
        return DONE
    if world.tick - job.since >= tuning.reinforce_winning_max_seconds * TICKS_PER_SECOND:
        return TIMEOUT
    reach = tuning.reinforce_winning_local_cells
    in_contact = targeting.in_contact([unit for unit in world.combat_units.values() if unit.id not in job.units], world)
    others = [unit for unit in in_contact if cells.is_within((unit.x, unit.y), job.cell, reach)]
    ours = weighed(job.cell, [*helpers, *others], world, ctx)
    if ours.enemy > 0 and ours.own < tuning.reinforce_winning_stop_ratio * ours.enemy:
        return LOSING
    return None


def _finishing(helpers: Sequence[Unit], going: Sequence[int], cell: Cell, world: World) -> list[Intent]:
    """An attack on the nearest enemy building near the fight's cell for each helper idle there, not just sent, with
    no enemy unit in contact: units at rest leave a building alone."""
    enemies = [record for record in targeting.enemies_in_sight(world) if not record.is_building]
    buildings = [record for record in _buildings_near(cell, world, ARRIVED_CELLS * 2) if record.visible]
    orders = []
    for unit in helpers:
        if unit.id in going or not unit.idle or not buildings or targeting.is_in_contact(unit, enemies, world):
            continue
        nearest = min(buildings, key=lambda record: (math.hypot(record.x - unit.x, record.y - unit.y), record.id))
        orders.append(Intent(Priority.CONTACT, {"cmd": "attack", "units": [unit.id], "target": nearest.id}))
    return orders


def _buildings_near(cell: Cell, world: World, reach: int) -> list[EnemyRecord]:
    """The remembered enemy buildings but the defenses within `reach` of a cell."""
    return [
        record
        for record in world.enemy_memory.values()
        if record.is_building
        and record.type not in DEFENSE_TYPES
        and cells.is_within((record.x, record.y), cell, reach)
    ]


def _log(tick: int, phase: str, fight: Fight, **data: object) -> None:
    """Write one `combat` event of reason reinforce_winning."""
    records.log(
        COMBAT_EVENT,
        {
            "tick": tick,
            "reason": REINFORCE_WINNING,
            "phase": phase,
            "cell": list(fight.cell),
            "own": round(fight.own),
            "enemy": round(fight.enemy),
            "buildings": round(fight.buildings),
            **data,
        },
    )
