"""One cap on the ground defenses every rule orders for the main base (`[base_defense] defense_cap`).

Base defense's extra defense, the canary and approach alarms, the spending
focus's defenses, the rush counter and upkeep's replacement of a lost defense
each order defenses by their own rule, and together they built far more than
the base needed: in the live games of 20261016 a base weakened by repeated
attacks stood behind five pillboxes and a turret while its army was gone, the
money that bought them lost to the army, and artillery would have shelled them
at leisure.

With the switch on, such a rule orders a ground defense (not an anti-air one)
only while `allows` says so. It refuses one, a `base_defense` event of reason
`defense_capped` saying which rule and why (at most once per rule and cause
in LOG_EVERY_SECONDS), while any of these holds:

- `main_cap`: before defense_cap_until_seconds, defense_cap_main ground
  defenses or more stand within MAIN_BASE_CELLS of our base's centre or are on
  their way;
- `outweighs_army`: defense_cap_ratio_min of them or more, and what they cost
  is defense_cap_army_ratio times what our combat units cost or more: a
  weakened base needs an army back, not one more defense;
- `crowded`: the defense is ordered toward a cell that defense_cap_crowd of
  our ground defenses stand within defense_cap_crowd_cells of already.

The endgame lead's defenses (see `clock`), the harass tower and the flame
tower that puts the flamethrower on offer keep their own rules. While the cap
is full (`is_full`), the canary alarm holds the unit queues no more: its money
goes to the army.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from ucla_agent.core import records
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.anti_air import ANTI_AIR_DEFENSES
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.world import DEFENSE_TYPES, Cell, World

GROUND_DEFENSES = DEFENSE_TYPES - frozenset(ANTI_AIR_DEFENSES.values())
BASE_DEFENSE_EVENT = "base_defense"
DEFENSE_CAPPED = "defense_capped"
MAIN_CAP = "main_cap"
OUTWEIGHS_ARMY = "outweighs_army"
CROWDED = "crowded"
# The buildings within this many cells of our base's centre are the main base the cap counts the defenses of.
MAIN_BASE_CELLS = 20
# A refusal of the same rule for the same cause is written at most this often.
LOG_EVERY_SECONDS = 30


def allows(item: str, rule: str, world: World, ctx: MatchContext, goal: Cell | None = None) -> bool:
    """Whether a rule may order a defense of a type now, toward `goal` when it has one (see the module's note); a
    refusal is written as a `defense_capped` event."""
    if not ctx.tuning.base_defense.defense_cap or item not in GROUND_DEFENSES or world.base_center is None:
        return True
    cause = _capped_by(world, ctx, goal)
    if cause is None:
        return True
    _log(rule, cause, item, world, ctx)
    return False


def is_full(world: World, ctx: MatchContext) -> bool:
    """Whether the cap refuses any more ground defense for the main base (by `main_cap` or `outweighs_army`)."""
    if not ctx.tuning.base_defense.defense_cap or world.base_center is None:
        return False
    return _capped_by(world, ctx, None) is not None


def _capped_by(world: World, ctx: MatchContext, goal: Cell | None) -> str | None:
    """Why the cap refuses a ground defense in this cycle; None while it allows one."""
    tuning = ctx.tuning.base_defense
    standing = [
        building
        for building in world.buildings.values()
        if building.type in GROUND_DEFENSES
        and cells.is_within((building.x, building.y), world.base_center, MAIN_BASE_CELLS)
    ]
    count = len(standing) + sum(ctx.production.pending(item) for item in GROUND_DEFENSES)
    if world.game_seconds < tuning.defense_cap_until_seconds and count >= tuning.defense_cap_main:
        return MAIN_CAP
    worth = sum(_cost(building.type, world) for building in standing)
    army = sum(_cost(unit.type, world) for unit in world.combat_units.values())
    if count >= tuning.defense_cap_ratio_min and worth >= tuning.defense_cap_army_ratio * army:
        return OUTWEIGHS_ARMY
    if goal is not None:
        near = sum(1 for b in standing if cells.is_within((b.x, b.y), goal, tuning.defense_cap_crowd_cells))
        if near >= tuning.defense_cap_crowd:
            return CROWDED
    return None


def _cost(item: str, world: World) -> int:
    facts = world.items.get(item)
    return facts.cost if facts is not None else 0


def _log(rule: str, cause: str, item: str, world: World, ctx: MatchContext) -> None:
    """Write the `defense_capped` event of a refusal, unless the same rule and cause had one written lately."""
    key = (rule, cause)
    last = ctx.defense_capped_at.get(key)
    if last is not None and world.tick - last < LOG_EVERY_SECONDS * TICKS_PER_SECOND:
        return
    ctx.defense_capped_at[key] = world.tick
    event = {"tick": world.tick, "reason": DEFENSE_CAPPED, "rule": rule, "cause": cause, "item": item}
    records.log(BASE_DEFENSE_EVENT, event)
