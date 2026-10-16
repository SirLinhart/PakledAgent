"""Harvester parking: where the harvesters wait, away from the enemy, while their losses would cost the match.

A harvester is the dearest unit most bases have, and it works where the
enemy comes looking for one. In the last minutes of a match a harvester lost
counts against the trade balance and the ore it would still bring in can
hardly be spent, so the harvesters are better off waiting out of reach.

With upkeep's `park_harvesters` switch on, they park in the last
`park_harvesters_seconds_left` seconds of the match, or earlier while the plan
is PRESERVE and base defense reports a major threat: a lead that an attack on
the base could still turn is kept by not feeding the attack harvesters. A
match whose remaining time is not known parks only for that threat. The
switch ships off.

They wait at the parking cell: on the far side of the base from the enemy's
start, `park_distance_cells` behind the construction yard on the straight line
from that start through the yard, the one nearest the base's center when
there are two. A base whose yard is lost is measured from
the middle of its refineries instead, where the harvesters already go. The
cell has to be one a harvester can stand on: in the playable part of the map,
on ground the terrain does not block and outside every own building. When the
point behind the base is not, the nearest such cell to it is taken. With no
enemy start known, or neither a yard nor a refinery left, there is no parking
cell, and the harvesters keep mining.

Each harvester is told once to move there, all of them in one order, and no
harvester is told to harvest while they are parked. One that stands within a
few cells of the cell already is parked as it is. The orders go in the
production class, the class of upkeep's other harvester orders: an order that
does not fit into a batch waits for the next, and base defense's emergency
orders go first. A harvester with an enemy at its heels flees as base defense
sends it, and once base defense has sent it back to work it is told to park
again, a cycle later, so that the two orders do not meet in one batch.

Parking for a threat can end before the last minutes: the threat is over, or
the lead is no longer preserved. Then, in the very cycle, every harvester but
those fleeing is told to harvest, in one order of the same class: those still
on their way would otherwise drive on to the parking cell and stand there.

Each start of parking is written to the match record as one
`harvesters_parked` event with its reason: `time_left` when the end of the
match is near, whatever else holds, else `preserve_major_threat`.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, placement, truck_shares
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.composition import PRESERVE
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD, REFINERY
from ucla_agent.games.redalert.managers import MAJOR_THREAT, MatchContext
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.world import Building, Cell, Unit, World

PARKED_EVENT = "harvesters_parked"
# Why the harvesters park: the end of the match is near, or a lead is preserved against a major threat.
TIME_LEFT = "time_left"
PRESERVE_MAJOR_THREAT = "preserve_major_threat"
# A harvester this near the parking cell is parked already, and one sent to a cell this near it was sent there: a
# harvester does not stop on the very cell, and a cell that shifts a little as buildings come and go is the same place.
PARKED_WITHIN_CELLS = 3
# How far from the point behind the base the nearest open cell is looked for, in multiples of the point's distance
# from the base: the base itself lies that distance away and has open ground round its buildings, so the search
# reaches past the base before it gives up.
_SEARCH_DISTANCE_FACTOR = 2


@dataclass(frozen=True)
class ParkingOrders:
    """What parking calls for in a cycle: its orders, the harvesters no other order may set to work, and those its
    orders set to work."""

    intents: list[Intent]
    # Every harvester while they are parked: none is told to harvest then.
    held: frozenset[int]
    # The harvesters told to harvest in the cycle parking ends.
    released: frozenset[int]


class Parking:
    """Sends the harvesters to the parking cell while parking is called for, and back to work when it ends. See the
    module's note."""

    def __init__(self) -> None:
        # Harvester id -> the parking cell it was sent to.
        self._sent: dict[int, Cell] = {}
        # The harvesters base defense had fleeing in the previous cycle: one it has let go since was sent back to work
        # in this cycle, at emergency priority, and is told to park in the next.
        self._fleeing_before: frozenset[int] = frozenset()
        # Whether the harvesters were parked in the previous cycle.
        self._is_parked = False

    def orders(self, harvesters: list[Unit], world: World, ctx: MatchContext) -> ParkingOrders:
        """The move to the parking cell of every harvester not parked or on its way there, while parking is called
        for; `harvest` for every harvester in the cycle it ends; nothing, and no harvester held, while it is not."""
        fleeing = ctx.fleeing_harvesters | self._fleeing_before
        self._fleeing_before = ctx.fleeing_harvesters
        at_hand = [unit for unit in harvesters if unit.id not in fleeing]
        parked_at = _parking_now(world, ctx)
        was_parked, self._is_parked = self._is_parked, parked_at is not None
        if parked_at is None:
            self._sent = {}
            return _back_to_work(at_hand, world, ctx) if was_parked else ParkingOrders([], frozenset(), frozenset())
        reason, cell = parked_at
        if not was_parked:
            _log_parked(reason, cell, len(harvesters), world, ctx)
        to_send = self._to_send(at_hand, cell)
        moves = [Intent(Priority.PRODUCTION, {"cmd": "move", "units": to_send, "to": list(cell)})] if to_send else []
        return ParkingOrders(moves, frozenset(unit.id for unit in harvesters), frozenset())

    def _to_send(self, harvesters: list[Unit], cell: Cell) -> list[int]:
        """The harvesters of those at hand that are neither parked nor on their way, noted as sent from now on.

        A harvester not at hand, fled or lost, is forgotten: one that fled is
        sent again once it is back, as its flight took it off the way.
        """
        self._sent = {unit.id: self._sent[unit.id] for unit in harvesters if unit.id in self._sent}
        to_send = [unit.id for unit in harvesters if not self._is_parked_or_on_its_way(unit, cell)]
        self._sent.update(dict.fromkeys(to_send, cell))
        return to_send

    def _is_parked_or_on_its_way(self, harvester: Unit, cell: Cell) -> bool:
        """Whether a harvester stands near the parking cell, or was sent to about the same cell."""
        sent = self._sent.get(harvester.id)
        is_sent = sent is not None and cells.is_within(sent, cell, PARKED_WITHIN_CELLS)
        return is_sent or cells.is_within((harvester.x, harvester.y), cell, PARKED_WITHIN_CELLS)


def _back_to_work(harvesters: list[Unit], world: World, ctx: MatchContext) -> ParkingOrders:
    """`harvest` for every harvester at hand as parking ends, those still on their way to the cell as well as those
    standing there: one on its way does not stand idle, and the idle-harvester rule would leave it parked. A truck
    that works a field of its truck share is sent to that field (`truck_shares.harvest_orders`)."""
    ids = [unit.id for unit in harvesters]
    intents = truck_shares.harvest_orders(ids, world, ctx, Priority.PRODUCTION) if ids else []
    return ParkingOrders(intents, frozenset(), frozenset(ids))


def _parking_now(world: World, ctx: MatchContext) -> tuple[str, Cell] | None:
    """Why and where the harvesters park in this cycle; None while parking is not called for, or there is no parking
    cell."""
    reason = parking_reason(world, ctx)
    if reason is None:
        return None
    cell = parking_cell(world, ctx.terrain, ctx.tuning.upkeep.park_distance_cells)
    return None if cell is None else (reason, cell)


def _log_parked(reason: str, cell: Cell, harvesters: int, world: World, ctx: MatchContext) -> None:
    """Write one `harvesters_parked` event: why and where the harvesters park, how many there are, and the match as it
    stands."""
    records.log(
        PARKED_EVENT,
        {
            "reason": reason,
            "cell": list(cell),
            "harvesters": harvesters,
            "tick": world.tick,
            "game_seconds": world.game_seconds,
            "remaining_seconds": world.remaining_seconds,
            "plan": ctx.plan,
            "threat_level": ctx.threat_level,
        },
    )


def parking_reason(world: World, ctx: MatchContext) -> str | None:
    """Why the harvesters are to park now, TIME_LEFT before PRESERVE_MAJOR_THREAT; None while the switch is off or
    neither holds."""
    tuning = ctx.tuning.upkeep
    if not tuning.park_harvesters:
        return None
    remaining = world.remaining_seconds
    if remaining is not None and remaining <= tuning.park_harvesters_seconds_left:
        return TIME_LEFT
    if ctx.plan == PRESERVE and ctx.threat_level == MAJOR_THREAT:
        return PRESERVE_MAJOR_THREAT
    return None


def parking_cell(world: World, terrain: Terrain, distance_cells: int) -> Cell | None:
    """The cell the harvesters wait at, `distance_cells` behind the base from the enemy's start, or the nearest open
    cell to it; None without an enemy start, a yard or a refinery, or an open cell near enough. See the module's note.
    """
    origin = _origin(world)
    if origin is None or world.enemy_base is None:
        return None
    point = cells.toward(origin, world.enemy_base, -distance_cells)
    covered = _covered_cells(world)
    nearest_first = (
        (point[0] + dx, point[1] + dy) for dx, dy in _offsets_within(_SEARCH_DISTANCE_FACTOR * distance_cells)
    )
    return next((cell for cell in nearest_first if _is_passable(cell, world, terrain, covered)), None)


def _origin(world: World) -> Cell | None:
    """The middle of the construction yard, of several the one nearest the base's center; without one, the middle of
    the refineries; None with neither."""
    yards = [building for building in world.buildings.values() if building.type == CONSTRUCTION_YARD]
    if yards:
        return _middle(_nearest_yard(yards, world.base_center))
    refineries = [_middle(building) for building in world.buildings.values() if building.type == REFINERY]
    if not refineries:
        return None
    return cells.nearest_cell(
        sum(x for x, _ in refineries) / len(refineries), sum(y for _, y in refineries) / len(refineries)
    )


def _nearest_yard(yards: list[Building], center: Cell | None) -> Building:
    """The yard nearest the base's center; the first when the center is not known."""
    if center is None:
        return yards[0]
    # The first of several as near is taken, which is the first yard.
    return min(yards, key=lambda yard: cells.distance((yard.x, yard.y), center))


def _middle(building: Building) -> Cell:
    """The middle cell of the cells a building covers."""
    return cells.nearest_cell(*placement.standing_box(building).center)


def _covered_cells(world: World) -> set[Cell]:
    """The cells the own buildings cover."""
    return {cell for building in world.buildings.values() for cell in placement.standing_box(building).cells()}


def _is_passable(cell: Cell, world: World, terrain: Terrain, covered: set[Cell]) -> bool:
    """Whether a harvester can wait on a cell: in the map, on ground the terrain does not block, under no building.

    The terrain counts ore as blocked too, which keeps a waiting harvester off
    a field it would start to mine.
    """
    return world.in_bounds(*cell) and cell not in terrain.blocked and cell not in covered


@functools.lru_cache(maxsize=None)
def _offsets_within(reach: int) -> tuple[Cell, ...]:
    """The offsets of the cells at most `reach` cells from a cell, nearest first, then by row and by column."""
    offsets = [
        (dx, dy)
        for dy in range(-reach, reach + 1)
        for dx in range(-reach, reach + 1)
        if dx * dx + dy * dy <= reach * reach
    ]
    return tuple(sorted(offsets, key=lambda offset: (offset[0] ** 2 + offset[1] ** 2, offset[1], offset[0])))
