"""Base defense keeps main out of a rescue far from the base that it would lose (`far_rescue_cells` and
`far_rescue_min_ratio` of `[base_defense]`).

A building far from the centre of the base, a yard out at an expansion, is
where the enemy attacks with more than main can take, or right next to the
enemy's own base. main called the whole way out to it walks into a fight it
loses there, and the base is left without it. So main is kept out of the
answer to a threat whose cell lies more than far_rescue_cells from the centre
of the base when either holds:

- main is estimated to lose there: main, every unit of it wherever it stands,
  weighed against the threat's enemies in sight within threat_area_cells of
  the threat's cell and the enemy ground defenses that reach that cell (see
  `fight.weigh`; a defense at defense_weight times its cost), is less than
  far_rescue_min_ratio as strong as they are. With no enemy in sight there,
  there is nothing to weigh, and the rescue may go;
- a known enemy construction yard or ground defense stands within
  ENEMY_BASE_NEAR_CELLS of the threat's cell: the enemy's base is next to it,
  whatever main is worth.

Kept out, main is neither drawn into the answer nor called home for it: it
stays under its plan, or the commander's order. The guard, the other units
near the base, the raid group and the defenses answer as they do without the
rule, and a threat within far_rescue_cells of the centre of the base is
answered as before. Once main is kept out of a threat, it stays out of it
while the threat stays against the same cell: enemies that step out of sight
do not make the rescue a winning one.

The units of main already sent to the threat, by base defense or by the
harvester response (each notes them, `FarRescue.note_sent`), are called back
in the cycle main is kept out: each gets an emergency move to the staging
point (`recall_order`), once: a unit left without an order fights on where
main is estimated to lose, alone. A unit a strike, a crush run or a repair
retreat holds (`MatchContext.crush_run` and `MatchContext.combat_held`) keeps
its own order, and is called back in the first cycle after it is let go.

The first time in a threat that main is kept out of an answer it would have
taken part in, a `base_defense` event of reason `far_rescue_declined` names
the threat's cell, its distance from the centre of the base, main's ratio
there (None with no enemy in sight), the enemy yard or ground defense near
it (None when there is none) and the ids of the units called back in that
cycle (`recalled`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

from ucla_agent.core import records
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.command_targets import reaches
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD
from ucla_agent.games.redalert.fight import ground_defenses, weigh
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.world import Cell, EnemyRecord, World

if TYPE_CHECKING:
    from ucla_agent.games.redalert.base_defense import Threat

BASE_DEFENSE_EVENT = "base_defense"
FAR_RESCUE_DECLINED = "far_rescue_declined"
# How near a threatened building an enemy construction yard or ground defense puts it next to the enemy's base.
ENEMY_BASE_NEAR_CELLS = 14
_RATIO_DIGITS = 2
_DISTANCE_DIGITS = 1


@dataclass(frozen=True)
class Decline:
    """Why main is kept out of a threat's answer: its ratio there and the enemy building near the threat's cell."""

    # main's strength against the threat's enemies there and the defenses reaching it; None with no enemy in sight.
    ratio: float | None
    # The known enemy construction yard or ground defense nearest the threat's cell within ENEMY_BASE_NEAR_CELLS.
    enemy_building: EnemyRecord | None


class FarRescue:
    """Whether main is kept out of the answer to the threat in force. See the module's note."""

    def __init__(self) -> None:
        # The cell of the threat main is kept out of; None while main is not kept out of the threat in force.
        self._declined_cell: Cell | None = None
        # Whether the far_rescue_declined event has been written for the threat in force.
        self._is_written = False
        # The units sent to the threat in force and not called back yet.
        self._sent: set[int] = set()

    def forget(self) -> None:
        """Forget the threat that is over: the next one is weighed afresh, and written once again."""
        self._declined_cell, self._is_written, self._sent = None, False, set()

    def note_sent(self, unit_ids: Iterable[int]) -> None:
        """Note units sent to the threat in force, so that those of main are called back once main is kept out."""
        self._sent.update(unit_ids)

    def take_recall(self, world: World, ctx: MatchContext) -> list[int]:
        """The units of main sent to the threat in force that go back to the staging point now that main is kept out
        of it, each handed out once: every one alive and in main but those a strike, a crush run or a repair retreat
        holds, which keep their own orders until they are let go."""
        recalled = self._recallable(world, ctx)
        self._sent.difference_update(recalled)
        return recalled

    def _recallable(self, world: World, ctx: MatchContext) -> list[int]:
        groups, held = ctx.groups, ctx.combat_held
        return sorted(
            unit_id
            for unit_id in self._sent
            if unit_id in world.combat_units
            and groups.group_of(unit_id) == MAIN
            and unit_id not in ctx.crush_run
            and unit_id not in held
        )

    def keeps_main_out(self, threat: Threat, world: World, ctx: MatchContext) -> bool:
        """Whether main is kept out of the answer to a threat it would otherwise take part in; the first time in a
        threat, the far_rescue_declined event is written."""
        if threat.cell == self._declined_cell:
            return True
        decline = declines(threat, world, ctx)
        if decline is None:
            self._declined_cell = None
            return False
        self._declined_cell = threat.cell
        if not self._is_written:
            self._is_written = True
            _log(threat.cell, decline, self._recallable(world, ctx), world)
        return True


def recall_order(unit_ids: list[int], world: World, ctx: MatchContext) -> list[Intent]:
    """The emergency move to the staging point of the units called back from a threat main is kept out of; none for
    no unit, or while the staging point is not known."""
    point = cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells)
    if not unit_ids or point is None:
        return []
    return [Intent(Priority.EMERGENCY, {"cmd": "move", "units": unit_ids, "to": list(point)})]


def declines(threat: Threat, world: World, ctx: MatchContext) -> Decline | None:
    """Why main is kept out of a threat's answer, or None when it is not: the threat's cell lies more than
    far_rescue_cells from the centre of the base, and main is under far_rescue_min_ratio there or the enemy's base
    stands near it."""
    tuning = ctx.tuning.base_defense
    centre = world.base_center
    if centre is None or cells.is_within(threat.cell, centre, tuning.far_rescue_cells):
        return None
    ratio = local_ratio(threat, world, ctx)
    near = enemy_base_near(threat.cell, world)
    is_losing = ratio is not None and ratio < tuning.far_rescue_min_ratio
    return Decline(ratio, near) if is_losing or near is not None else None


def local_ratio(threat: Threat, world: World, ctx: MatchContext) -> float | None:
    """main's strength against the threat's enemies in sight within threat_area_cells of its cell and the enemy
    ground defenses that reach the cell, as `fight.weigh` weighs them; None with no enemy that counts in sight
    there."""
    reach = ctx.tuning.base_defense.threat_area_cells
    enemies = [enemy for enemy in threat.enemies if cells.is_within((enemy.x, enemy.y), threat.cell, reach)]
    if not enemies:
        return None
    covering = [defense for defense in ground_defenses(world) if reaches(defense, threat.cell, world, ctx)]
    main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
    own, enemy = weigh(main, enemies, world, ctx.tuning, covering)
    return own / enemy if enemy > 0 else None


def enemy_base_near(cell: Cell, world: World) -> EnemyRecord | None:
    """The known enemy construction yard or ground defense nearest a cell within ENEMY_BASE_NEAR_CELLS of it; None
    when there is none."""
    yards = [
        record for record in world.enemy_memory.values() if record.is_building and record.type == CONSTRUCTION_YARD
    ]
    near = [
        record
        for record in [*yards, *ground_defenses(world)]
        if cells.is_within((record.x, record.y), cell, ENEMY_BASE_NEAR_CELLS)
    ]
    return min(near, key=lambda record: cells.distance((record.x, record.y), cell), default=None)


def _log(cell: Cell, decline: Decline, recalled: list[int], world: World) -> None:
    """Write the `base_defense` event of main kept out of a far rescue, with the units called back from it."""
    building = decline.enemy_building
    records.log(
        BASE_DEFENSE_EVENT,
        {
            "reason": FAR_RESCUE_DECLINED,
            "tick": world.tick,
            "threat_cell": list(cell),
            "distance_cells": round(cells.distance(cell, world.base_center), _DISTANCE_DIGITS),
            "ratio": None if decline.ratio is None else round(decline.ratio, _RATIO_DIGITS),
            "enemy_building": (
                None
                if building is None
                else {"id": building.id, "type": building.type, "cell": [building.x, building.y]}
            ),
            "recalled": recalled,
        },
    )
