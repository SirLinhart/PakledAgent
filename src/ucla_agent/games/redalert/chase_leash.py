"""The leash on main's units chasing alone into the enemy's base (`[combat] chase_leash`).

Live (shadowfiend-ii, rush, 8:50): a rocket soldier and a flamethrower of
main's were ordered onto an enemy jeep in a fight on the way; the jeep fled
home and the two chased it 40 cells into the enemy's base while main stood
where the fight had been. An attack order holds a unit on its target however
far it runs. With the switch on, in every cycle a unit of main's (the strike
force's are left alone: an exposed expansion is struck on purpose) is called
back when all of these hold:

- it is carrying out an attack (`ATTACK_ACTIVITY`): chasing one target;
- it stands more than chase_leash_cells from the centroid of main's units;
- it stands within chase_leash_base_cells of the enemy's start;
- fewer than chase_leash_min_units of main's units, itself among them, stand
  within chase_leash_near_cells of it: it is alone, or nearly.

The units called back are moved to the cell of main's centroid at once
(emergency), which ends their chase; each call is a `combat` event of reason
`chase_leash` with the units and the cell. Nothing here reads a file, a clock
or the network.
"""

from __future__ import annotations

import math
from typing import Sequence

from ucla_agent.core import records
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.fight import Point, centroid
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.scout_missions import enemy_start
from ucla_agent.games.redalert.tuning import CombatTuning
from ucla_agent.games.redalert.world import Cell, Unit, World

COMBAT_EVENT = "combat"
CHASE_LEASH = "chase_leash"
# The engine's activity of a unit carrying out an attack order on one target.
ATTACK_ACTIVITY = "Attack"


def leash(units: Sequence[Unit], world: World, ctx: MatchContext) -> list[Intent]:
    """The emergency move back to main's centroid of main's units (`units`) chasing alone into the enemy's base (see
    the module's note), with its event; none with chase_leash off, while the enemy's start is not known or none is."""
    tuning = ctx.tuning.combat
    start = enemy_start(world)
    if not tuning.chase_leash or start is None or not units:
        return []
    middle = centroid(units)
    called = sorted(unit.id for unit in units if _is_chasing_alone(unit, units, middle, start, tuning))
    if not called:
        return []
    cell = cells.nearest_cell(*middle)
    records.log(COMBAT_EVENT, {"tick": world.tick, "reason": CHASE_LEASH, "units": called, "cell": list(cell)})
    return [Intent(Priority.EMERGENCY, {"cmd": "move", "units": called, "to": list(cell)})]


def _is_chasing_alone(unit: Unit, units: Sequence[Unit], middle: Point, start: Cell, tuning: CombatTuning) -> bool:
    """Whether a unit chases a target alone, far from main and near the enemy's start."""
    here = (unit.x, unit.y)
    if unit.activity != ATTACK_ACTIVITY or math.dist(here, middle) <= tuning.chase_leash_cells:
        return False
    if not cells.is_within(here, start, tuning.chase_leash_base_cells):
        return False
    near = sum(1 for other in units if cells.is_within(here, (other.x, other.y), tuning.chase_leash_near_cells))
    return near < tuning.chase_leash_min_units
