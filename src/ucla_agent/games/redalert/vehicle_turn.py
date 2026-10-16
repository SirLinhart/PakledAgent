"""Whose turn the Vehicle queue's next item is while a harvester is due: the harvester's or a combat vehicle's.

The Vehicle queue builds in order, so a combat vehicle queued ahead of a due
harvester comes out first, and the other way round. With `harvesters_first`
of [upkeep] on, the queue takes turns between the due harvester (see
`harvester_room` for when one is due) and the unit queues' combat vehicles:

1. While the harvesters standing or in production are fewer than
   `harvesters_first_per_refinery` x the own refineries standing, the
   harvester goes first: no combat vehicle is queued while it is due.
2. Else, while main's combat value is under `combat_value_floor`, or an enemy
   army worth `ENEMY_NEAR_VALUE` or more was seen within `ENEMY_NEAR_CELLS`
   of an own building in the last `ENEMY_NEAR_SECONDS`, combat vehicles go
   first. Main's combat value is what its members are worth in a fight (their
   cost weighted by their health); harvesters, MCVs and the support units
   are in no group, so none of them counts.
3. Else the two alternate: after a harvester the next vehicle is a combat
   vehicle, and after a combat vehicle a harvester.

Under the economy focus of upkeep's spending (see `spending_focus`) the due
harvester goes first as in 1: the money is to grow the economy then.

A harvester on a combat vehicle's turn is not lost: when no combat vehicle
comes in a cycle (the unit queues wait for the economy, or the budget does
not run to one) and the Vehicle queue stands free, the due harvester takes
the queue rather than leave it idle. The harvester target and cap are those
of `harvester_room`, whatever the turn.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.family import REFINERY
from ucla_agent.games.redalert.fight import strength, worth
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.harvester_room import harvesters_counted
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import ECONOMY_FOCUS, MatchContext
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.world import Cell, World

# An enemy army near the base: worth this much or more, seen within this many cells of an own building, this lately.
ENEMY_NEAR_VALUE = 1000
ENEMY_NEAR_CELLS = 25
ENEMY_NEAR_SECONDS = 30
# A remembered enemy is weighed at full health: what it cost.
_FULL_HEALTH = 100


class VehicleTurn:
    """Whose turn the Vehicle queue's next item is, and what was queued last under the turns. See the module's note."""

    def __init__(self) -> None:
        # Whether the last vehicle queued under the turns was a harvester; before the first, a harvester's turn.
        self._last_was_harvester = False

    def is_harvester_turn(self, world: World, ctx: MatchContext) -> bool:
        """Whether a due harvester goes into the Vehicle queue ahead of combat vehicles now: below the first
        threshold and under the economy focus always, while combat vehicles go first never, else when a combat vehicle
        went in last."""
        if is_below_first(ctx) or ctx.spending_focus == ECONOMY_FOCUS:
            return True
        if is_combat_first(world, ctx):
            return False
        return not self._last_was_harvester

    def note_harvester(self) -> None:
        """Note a harvester queued: under the alternation, a combat vehicle is next."""
        self._last_was_harvester = True

    def note_combat(self) -> None:
        """Note a combat vehicle queued: under the alternation, a harvester is next."""
        self._last_was_harvester = False


def is_below_first(ctx: MatchContext) -> bool:
    """Whether the harvesters standing or in production are fewer than `harvesters_first_per_refinery` x the own
    refineries standing."""
    per_refinery = ctx.tuning.upkeep.harvesters_first_per_refinery
    return harvesters_counted(ctx) < per_refinery * ctx.production.standing(REFINERY)


def is_combat_first(world: World, ctx: MatchContext) -> bool:
    """Whether combat vehicles go first: main's combat value is under `combat_value_floor`, or an enemy army is near
    the base (`is_enemy_army_near`)."""
    return main_combat_value(world, ctx) < ctx.tuning.upkeep.combat_value_floor or is_enemy_army_near(world)


def main_combat_value(world: World, ctx: MatchContext) -> float:
    """What main's members in the field are worth in a fight together."""
    return strength((world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)), world)


def is_enemy_army_near(world: World) -> bool:
    """Whether the enemy combat units seen within `ENEMY_NEAR_CELLS` of an own building in the last
    `ENEMY_NEAR_SECONDS`, where they were last seen, cost `ENEMY_NEAR_VALUE` or more together."""
    horizon = world.tick - ENEMY_NEAR_SECONDS * TICKS_PER_SECOND
    own = [(building.x, building.y) for building in world.buildings.values()]
    near = [
        record
        for record in world.enemy_memory.values()
        if is_combat_unit(record) and record.last_seen_tick >= horizon and _is_near_any((record.x, record.y), own)
    ]
    return sum(worth(record.type, _FULL_HEALTH, world) for record in near) >= ENEMY_NEAR_VALUE


def _is_near_any(cell: Cell, own: list[Cell]) -> bool:
    """Whether a cell is within `ENEMY_NEAR_CELLS` of any of the own buildings' cells."""
    return any(cells.is_within(cell, building, ENEMY_NEAR_CELLS) for building in own)
