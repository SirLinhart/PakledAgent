"""The staging point kept clear of the enemy and behind our defenses, and the moves to it that fight back
(`[combat] staging_clear_of_enemies`, `staging_behind_defenses`, `gather_attack_move`).

The staging point is the cell staging_distance_cells from our base toward the
enemy's start (`cells.staging_point`): main gathers there, the joiners meet
there, the new units rally there. An enemy that camps near it kills them one
by one as they arrive, and a unit on a plain `move` does not fire back. In the
live games of 20261016 three units sent to a staging point 3 cells from an
enemy camp arrived one at a time and died (12 lost, 2 killed); and a staging
group stood far out ahead of our defenses, which could not cover it.

With `staging_clear_of_enemies` on, the point is moved back toward our base,
a cell at a time, to the first cell that no remembered enemy combat unit (not
stale) reaches: its weapon range and staging_enemy_margin_cells; our base's
centre when no cell short of it is clear.

With `staging_behind_defenses` on, while main is not on the attack (the plan
is none of PRESS, STRIKE, FINISH and no attack is out), the point, and the
gather point main publishes (`MatchContext.gather_point`: the forward hold's,
the cover rally's), is kept by our ground defenses: when it lies farther out
along the lane toward the enemy's start than our front ground defense (the
one farthest out that way within MAIN_BASE_CELLS of our base's centre), or
more than staging_defense_cells from every ground defense of ours, it is moved
to the cell staging_behind_cells behind that front defense, toward our base's
centre; it stays where it is while we have no ground defense.

With `gather_attack_move` on, the moves that gather units at the staging point,
main's gather point or the joiners' meeting cell are `attack_move`
(`gather_command`): what is met on the way is fought, not walked past. A
retreat's moves stay plain moves.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.composition import FINISH, PRESS, STRIKE
from ucla_agent.games.redalert.defense_cap import GROUND_DEFENSES, MAIN_BASE_CELLS
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.world import Cell, World

MOVE = "move"
ATTACK_MOVE = "attack_move"
# The plans under which main is on the attack, whose staging point is not held behind the defenses.
ATTACKING_PLANS = frozenset({PRESS, STRIKE, FINISH})


def staging_point(world: World, tuning: Tuning, ctx: MatchContext | None = None) -> Cell | None:
    """The staging point (`cells.staging_point`), with staging_clear_of_enemies on moved back toward our base out of
    every remembered enemy combat unit's reach and the margin, and given the match context, held behind our defenses
    while main is not on the attack (`behind_defenses`); None while the staging point is not known."""
    point = cells.staging_point(world, tuning.upkeep.staging_distance_cells)
    if point is None:
        return None
    if ctx is not None:
        point = behind_defenses(point, world, ctx)
    if not tuning.combat.staging_clear_of_enemies:
        return point
    threats = [
        ((record.x, record.y), world.items.damage.get(record.type).range + tuning.combat.staging_enemy_margin_cells)
        for record in world.enemy_memory.values()
        if is_combat_unit(record) and not record.stale
    ]
    base = world.base_center
    for back in range(math.ceil(cells.distance(point, base)) + 1):
        cell = cells.toward(point, base, back)
        if not any(cells.is_within(cell, enemy, reach) for enemy, reach in threats):
            return cell
    return base


def behind_defenses(point: Cell, world: World, ctx: MatchContext) -> Cell:
    """A point main gathers at, held by our ground defenses while main is not on the attack (see the module's note);
    the point as it is with staging_behind_defenses off, on the attack, or with no ground defense of ours."""
    tuning = ctx.tuning.combat
    base, enemy = world.base_center, world.enemy_base
    if not tuning.staging_behind_defenses or ctx.attack_out or ctx.plan in ATTACKING_PLANS:
        return point
    if base is None or enemy is None or base == enemy:
        return point
    defenses = [
        (building.x, building.y)
        for building in world.buildings.values()
        if building.type in GROUND_DEFENSES and cells.is_within((building.x, building.y), base, MAIN_BASE_CELLS)
    ]
    if not defenses:
        return point
    span = cells.distance(base, enemy)
    along = ((enemy[0] - base[0]) / span, (enemy[1] - base[1]) / span)

    def out(cell: Cell) -> float:
        return (cell[0] - base[0]) * along[0] + (cell[1] - base[1]) * along[1]

    front = max(defenses, key=out)
    is_covered = any(cells.is_within(point, defense, tuning.staging_defense_cells) for defense in defenses)
    if out(point) <= out(front) and is_covered:
        return point
    return cells.toward(front, base, tuning.staging_behind_cells)


def gather_command(tuning: Tuning) -> str:
    """The command of a move that gathers units at the staging or gather point: `attack_move` with gather_attack_move
    on, else `move`."""
    return ATTACK_MOVE if tuning.combat.gather_attack_move else MOVE
