"""Our infantry spread out, one to a cell, so that one blast does not kill a crowd.

Red Alert lets up to five infantry stand on one cell. A group sent to one
point stacks there, and splash damage (an artillery shell, a V2, a Tesla arc,
a crashing aircraft) then kills many of them at once. With `infantry_spread`
of `[combat]` on, `InfantrySpread`, one for the match on the context, does two
things to the infantry of main and the raid group; the guard's is left where
its manager posts it, and main's while main has artillery or V2, which the
siege places with its screen or its group:

- Standing between the managers and the batch, so that every manager's orders
  pass through it, it splits a MOVE-class `move` or `attack_move` to a point
  that names such infantry: each of them gets a cell of its own near the
  point, the passable cells nearest it, in rings `infantry_spread_cells`
  apart, never under a building, one infantry to a cell, the unit nearest the
  point first. The vehicles of the order, every other unit of it and the unit
  given the point itself keep the order as it was. A unit that stands on its
  own cell is not ordered again; one whose move to it is still in force is
  left to it.
- Asked by the combat manager, it moves main's idle infantry that shares its
  cell with another of our units, and is ordered nothing else by combat in
  the cycle and in contact with no enemy in sight, to the nearest free cell:
  passable, under no building and with none of our units on it. A unit is de-stacked at most once in
  `infantry_spread_recheck_seconds`. An agent with no manager orders nothing.

At most `infantry_spread_max_orders` such orders go out in a cycle, de-stacks
first, then the units that have waited longest for a cell of their own; the
rest keep the order they were given and get their cell in a later cycle.

Never altered: an order of any class but MOVE (attacks on a target, focus
fire, siege fire, the crush run's own orders, base defense's and the
retreats' orders), boarding a transport, an order added to a unit's plan,
the model commander's order and the order the ledger sends again because the
platform lost it.
"""

from __future__ import annotations

import functools
from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING, Callable, Iterable, Mapping

from ucla_agent.games.redalert import placement
from ucla_agent.games.redalert.batch import ACTIVITY_COMMANDS, Intent, Priority
from ucla_agent.games.redalert.groups import MAIN, RAID
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.production import INFANTRY_QUEUE
from ucla_agent.games.redalert.targeting import enemies_in_sight, is_in_contact
from ucla_agent.games.redalert.world import Unit, World

if TYPE_CHECKING:
    from ucla_agent.games.redalert.managers import MatchContext
    from ucla_agent.games.redalert.terrain import Terrain
    from ucla_agent.games.redalert.tuning import CombatTuning

Cell = tuple[int, int]

# The orders to a point that are spread.
SPREAD_COMMANDS = frozenset({"move", "attack_move"})
# The groups whose infantry is spread. The guard's is left where its manager posts it.
SPREAD_GROUPS = frozenset({MAIN, RAID})
# How many rings of cells around a point are looked at for a free one: 9 x 9 cells and more, room for any group.
_MAX_RINGS = 8


class InfantrySpread:
    """Gives the infantry of our groups a cell each. See the module's note."""

    def __init__(self) -> None:
        # Unit id -> the point it was last ordered to and the cell of its own it was given there.
        self._given: dict[int, tuple[Cell, Cell]] = {}
        # Unit id -> the tick since which it has waited for a cell of its own.
        self._waiting: dict[int, int] = {}
        # Unit id -> the tick of its last de-stack.
        self._destacked: dict[int, int] = {}
        # The tick of the last de-stack asked for, and how many moves it gave.
        self._destack_tick, self._destack_count = -1, 0

    def apply(
        self, intents: Iterable[Intent], world: World, ctx: MatchContext, is_new: Callable[[Intent], bool]
    ) -> list[Intent]:
        """The cycle's intents with the infantry of main and the raid group spread; as they are with the switch off.

        `is_new` says whether an intent would tell its unit something new, which
        an order still in force does not (see `arbitration.RepeatFilter`).
        """
        intents = list(intents)
        tuning = ctx.tuning.combat
        if not tuning.infantry_spread:
            return intents
        members = _members(world, ctx)
        self._forget_gone(world)
        if not members:
            return intents
        cycle = _Cycle(world, ctx.terrain, self._budget_left(world, tuning), tuning.infantry_spread_cells)
        cycle.taken.update(cell for _, cell in self._given.values())
        return [part for intent in intents for part in self._spread(intent, members, cycle, is_new)]

    def destacks(
        self, units: Mapping[int, Unit], intents: Iterable[Intent], world: World, ctx: MatchContext
    ) -> list[Intent]:
        """Moves for the idle infantry among `units` that shares its cell with another of our units and is ordered
        nothing by `intents`, each to the nearest free cell, the longest not de-stacked first, a unit at most once in
        infantry_spread_recheck_seconds; none with the switch off. The combat manager asks for main's; what this
        gives counts against the cycle's infantry_spread_max_orders."""
        tuning = ctx.tuning.combat
        self._destack_tick, self._destack_count = world.tick, 0
        if not tuning.infantry_spread:
            return []
        ordered = {
            unit_id
            for intent in intents
            if intent.order.get("cmd") in ACTIVITY_COMMANDS
            for unit_id in intent.order.get("units", ())
        }
        infantry = {unit_id: unit for unit_id, unit in _members(world, ctx).items() if unit_id in units}
        cycle = _Cycle(world, ctx.terrain, tuning.infantry_spread_max_orders, tuning.infantry_spread_cells)
        cycle.taken.update(cell for _, cell in self._given.values())
        recheck_ticks = tuning.infantry_spread_recheck_seconds * TICKS_PER_SECOND
        moves = self._destacks(infantry, ordered, cycle, recheck_ticks)
        self._destack_count = len(moves)
        return moves

    def _budget_left(self, world: World, tuning: CombatTuning) -> int:
        """The spread orders the cycle has left after the de-stack moves given in it."""
        given = self._destack_count if self._destack_tick == world.tick else 0
        return tuning.infantry_spread_max_orders - given

    def _forget_gone(self, world: World) -> None:
        for record in (self._given, self._waiting, self._destacked):
            for unit_id in [unit_id for unit_id in record if unit_id not in world.units]:
                del record[unit_id]

    def _spread(
        self, intent: Intent, members: Mapping[int, Unit], cycle: _Cycle, is_new: Callable[[Intent], bool]
    ) -> list[Intent]:
        """One intent as it goes out: the infantry it moves each sent to a cell of its own, as many as the cycle has
        orders left for, and the rest of its units under the order as it was."""
        if not _is_spread(intent):
            return [intent]
        order = intent.order
        # A unit named twice in an order is sent once.
        infantry = list({unit_id: members[unit_id] for unit_id in order["units"] if unit_id in members}.values())
        if not infantry:
            return [intent]
        point = (int(order["to"][0]), int(order["to"][1]))
        pending = [unit for unit in infantry if not self._is_settled(intent, unit, point, is_new)]
        alone = self._own_cells(intent, point, pending, cycle)
        for unit in pending:
            self._forget_other_point(unit.id, point)
        return _rest(intent, infantry, pending, alone) + alone

    def _is_settled(self, intent: Intent, unit: Unit, point: Cell, is_new: Callable[[Intent], bool]) -> bool:
        """Whether a unit has its own cell at the point and stands on it, or its move there is in force."""
        given = self._given.get(unit.id)
        if given is None or given[0] != point:
            return False
        return (unit.x, unit.y) == given[1] or not is_new(_alone(intent, unit.id, given[1]))

    def _own_cells(self, intent: Intent, point: Cell, pending: list[Unit], cycle: _Cycle) -> list[Intent]:
        """The orders that send the units waiting longest, as many as the cycle has orders left for, each to a cell of
        its own at the point: the one it was given there, or the free cell nearest the point, the unit nearest first."""
        pending = sorted(pending, key=lambda unit: (self._waiting.setdefault(unit.id, cycle.world.tick), unit.id))
        chosen = sorted(pending[: cycle.budget], key=lambda unit: (_distance_squared(unit, point), unit.id))
        alone = []
        for unit in chosen:
            given = self._given.get(unit.id)
            cell = given[1] if given is not None and given[0] == point else cycle.take_near(point)
            if cell is not None:
                self._given[unit.id] = (point, cell)
                self._waiting.pop(unit.id, None)
                # The unit given the point itself goes there under the order as it was, with no order of its own.
                if cell != point:
                    cycle.budget -= 1
                    alone.append(_alone(intent, unit.id, cell))
        return alone

    def _forget_other_point(self, unit_id: int, point: Cell) -> None:
        """Let go of the cell a unit was given at another point than the one it is now sent to."""
        given = self._given.get(unit_id)
        if given is not None and given[0] != point:
            del self._given[unit_id]

    def _destacks(
        self, members: Mapping[int, Unit], ordered: set[int], cycle: _Cycle, recheck_ticks: int
    ) -> list[Intent]:
        """Moves for the idle infantry that shares its cell with another of our units, ordered nothing else in the
        cycle and not de-stacked within `recheck_ticks`: each to the nearest free cell, the longest not de-stacked
        first. A unit on the cell of its own it was given stays, and the other one moves."""
        on_cell = Counter((unit.x, unit.y) for unit in cycle.world.units.values())
        stacked = [
            unit
            for unit in members.values()
            if unit.id not in ordered and on_cell[unit.x, unit.y] > 1 and self._may_destack(unit, cycle, recheck_ticks)
        ]
        tick = cycle.world.tick
        stacked.sort(key=lambda unit: (self._destacked.get(unit.id, -1), unit.id))
        cycle.taken.update(on_cell)
        moves = []
        for unit in stacked[: max(cycle.budget, 0)]:
            cell = cycle.take_near((unit.x, unit.y))
            if cell is not None:
                cycle.budget -= 1
                self._destacked[unit.id] = tick
                self._given.pop(unit.id, None)
                moves.append(Intent(Priority.MOVE, {"cmd": "move", "units": [unit.id], "to": list(cell)}))
        return moves

    def _may_destack(self, unit: Unit, cycle: _Cycle, recheck_ticks: int) -> bool:
        """Whether a unit may be moved off a shared cell: idle, not on the cell of its own it was given, in contact
        with no enemy in sight (an idle unit in contact fires on its own, and a move would stop it), and not
        de-stacked within `recheck_ticks`."""
        if not unit.idle or self._given.get(unit.id, (None, None))[1] == (unit.x, unit.y):
            return False
        if is_in_contact(unit, cycle.enemies, cycle.world):
            return False
        return unit.id not in self._destacked or cycle.world.tick - self._destacked[unit.id] >= recheck_ticks


class _Cycle:
    """The cells of one cycle: those taken by a unit's own cell, and the orders the cycle has left for the spread."""

    def __init__(self, world: World, terrain: Terrain, budget: int, step: int) -> None:
        self.world = world
        self.budget = budget
        self._step = step
        self._impassable = terrain.impassable
        self.taken: set[Cell] = set(_building_cells(world))
        self.enemies = enemies_in_sight(world)

    def take_near(self, point: Cell) -> Cell | None:
        """The free cell nearest a point, in rings `step` cells apart, now taken; None when the rings hold none."""
        for dx, dy in _ring_offsets(_MAX_RINGS):
            cell = (point[0] + dx * self._step, point[1] + dy * self._step)
            if cell not in self.taken and cell not in self._impassable and self.world.in_bounds(*cell):
                self.taken.add(cell)
                return cell
        return None


def _is_spread(intent: Intent) -> bool:
    """Whether an intent is an order to a point the spread may split. See the module's note for those it may not."""
    order = intent.order
    return (
        order.get("cmd") in SPREAD_COMMANDS
        and "to" in order
        and not order.get("queued")
        and intent.priority == Priority.MOVE
        and not intent.commander
        and not intent.resend
    )


def _members(world: World, ctx: MatchContext) -> dict[int, Unit]:
    """Our infantry units of main and the raid group, by id; none of main's while main has artillery or V2, whose
    screen or group the siege places as it means to."""
    groups = SPREAD_GROUPS - {MAIN} if ctx.groups.siege_members(MAIN) else SPREAD_GROUPS
    members = {}
    for unit_id, unit in world.units.items():
        facts = world.items.get(unit.type)
        if facts is not None and facts.queue == INFANTRY_QUEUE and ctx.groups.group_of(unit_id) in groups:
            members[unit_id] = unit
    return members


def _rest(intent: Intent, infantry: list[Unit], pending: list[Unit], alone: list[Intent]) -> list[Intent]:
    """The intent for the units it names that are neither settled at their own cell nor sent to it alone: the
    vehicles, and the infantry still waiting for a cell of its own; none when no unit is left."""
    settled = {unit.id for unit in infantry} - {unit.id for unit in pending}
    left_out = settled | {part.order["units"][0] for part in alone}
    rest = [unit_id for unit_id in intent.order["units"] if unit_id not in left_out]
    return [replace(intent, order={**intent.order, "units": rest})] if rest else []


def _alone(intent: Intent, unit_id: int, cell: Cell) -> Intent:
    """The intent's order for one unit, to a cell of its own."""
    return replace(intent, order={**intent.order, "units": [unit_id], "to": list(cell)})


def _distance_squared(unit: Unit, point: Cell) -> int:
    return (unit.x - point[0]) ** 2 + (unit.y - point[1]) ** 2


def _building_cells(world: World) -> Iterable[Cell]:
    """The cells our buildings cover, and those of the enemy buildings remembered."""
    for building in world.buildings.values():
        yield from placement.standing_box(building).cells()
    for record in world.enemy_memory.values():
        if record.is_building:
            corner = placement.footprint_corner(record.type, (record.x, record.y))
            yield from placement.box_at(record.type, corner).cells()


@functools.lru_cache(maxsize=None)
def _ring_offsets(rings: int) -> tuple[Cell, ...]:
    """The offsets of the cells within `rings` rings of a cell: by ring, then nearest, then by row and by column."""
    offsets = [(dx, dy) for dy in range(-rings, rings + 1) for dx in range(-rings, rings + 1)]
    return tuple(
        sorted(offsets, key=lambda offset: (max(map(abs, offset)), offset[0] ** 2 + offset[1] ** 2, *offset[::-1]))
    )
