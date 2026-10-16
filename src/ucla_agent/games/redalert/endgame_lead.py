"""The endgame lead: in a late stalemate ahead on the tiebreak, the lead is kept behind defenses between the bases.

A match that runs its full time is decided on the trade balance first. Ahead
by `endgame_lead_margin` or more in the last `endgame_lead_seconds`, with
both armies heavy (ours, main, guard and escort at cost, and the enemy army
estimate both worth `endgame_heavy_army_value`) and no knockout in sight
(R6 not applying), an even trade only puts the lead at risk. So with
`endgame_lead_defenses` of `[clock]` on, the plan rules set PRESERVE by the
rule R2E (`applies`), kept until the balance has stood at 0 or below for
`clock_switch_ticks` or R6 applies (see `plans`). While it is in force
(`is_in_force`), every spare credit goes to the strongest defense, clustered
around the lead point (`lead_point`): the cell `endgame_defense_forward_cells`
from the base's center toward the enemy's start (see `clock`); main gathers
there and engages only enemies within `endgame_army_leash_cells` of it (see
`combat`), and the producers rally there.

Nothing here orders anything; it reads the world and the match context.
"""

from __future__ import annotations

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.expansion import ESCORT
from ucla_agent.games.redalert.groups import GUARD, MAIN
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.tuning import ClockTuning
from ucla_agent.games.redalert.world import Cell, World

R2E = "R2E"
# The groups whose units make up our army against the enemy's: those that fight for the base, the MCV's escort
# among them.
ARMY_GROUPS = (MAIN, GUARD, ESCORT)


def is_in_force(ctx: MatchContext) -> bool:
    """Whether the endgame lead is kept in this cycle: the switch on and the plan set by R2E."""
    return ctx.tuning.clock.endgame_lead_defenses and ctx.plan_rule == R2E


def applies(
    remaining_seconds: float | None, balance: int, army: int, enemy_estimate: float | None, tuning: ClockTuning
) -> bool:
    """Whether R2E starts, R6 aside: at most `endgame_lead_seconds` left, the trade balance `endgame_lead_margin` or
    more, and our army and the enemy army estimate (known) both worth `endgame_heavy_army_value` or more."""
    is_late = remaining_seconds is not None and remaining_seconds <= tuning.endgame_lead_seconds
    heavy = tuning.endgame_heavy_army_value
    is_heavy = army >= heavy and enemy_estimate is not None and enemy_estimate >= heavy
    return is_late and balance >= tuning.endgame_lead_margin and is_heavy


def army_value(world: World, ctx: MatchContext) -> int:
    """What our army costs: the units of main, the guard and the escort, at full health."""
    units = [world.combat_units[unit_id] for group in ARMY_GROUPS for unit_id in ctx.groups.members(group)]
    return sum(world.items[unit.type].cost for unit in units if world.items.get(unit.type) is not None)


def lead_point(world: World, tuning: ClockTuning) -> Cell | None:
    """The cell the lead's defenses cluster around and main gathers at: `endgame_defense_forward_cells` from the
    base's center toward the enemy's start, on the map; None while either is unknown."""
    base, enemy = world.base_center, world.enemy_base
    if base is None or enemy is None:
        return None
    point = cells.toward(base, enemy, tuning.endgame_defense_forward_cells)
    return point if world.map_bounds is None else cells.clamped(point, world.map_bounds)
