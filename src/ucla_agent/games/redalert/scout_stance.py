"""The stance of the scout slots' scouts (`[scouting] scout_cautious`): they hold fire, and are never told to attack.

A scout is worth what it sees, not what it kills. One left in the stance a
unit is trained with fires at whatever comes within its range, stops to duel
it and is lost to it, deep in the enemy's ground. So with the switch on, a
unit that takes a scout slot (see `scout_slots`) is given one `set_stance`
order of `hold_fire`, and one that leaves its slot for main, too hurt to
scout or replaced by the faster scout, is given main's stance back,
`attack_anything`, the stance every unit is trained with. A stance order that
was lost leaves the scout in the stance the state shows: the order is made
again once a state shows the scout in another stance ORDER_AGAIN_TICKS after
the last order for it.

A stance does not keep a unit from carrying out an attack it is told to make,
and any manager may propose one for a unit near an enemy: base defense for a
unit new in the cycle that has not yet taken its slot, main's focus fire for a
unit in contact. So the agent also takes every unit of the scout group out of
the cycle's `attack` and `attack_move` orders (`without_scout_attacks`): a
slot's scout is only ever moved.

A scout released from its slot too hurt to scout goes home first
(`ReturningScouts`): it stays in the scout group, out of every attack, and in
hold_fire, with a move to the staging point; once it stands within
`HOME_CELLS` of the staging point it joins main and gets main's stance. Sent
straight into main, a hurt scout deep in the enemy's ground was handed main's
targets there and lost. One that stands idle short of the staging point is
moved there again ORDER_AGAIN_TICKS after its last move. The scouts on their
way home are published as `MatchContext.returning_scouts`; base defense may
take one within threat_area_cells of a major threat into main at once (see
`base_defense`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.groups import SCOUT
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.tuning import ScoutingTuning
from ucla_agent.games.redalert.world import Cell, Unit, World

# The stance a slot's scout keeps, and the one main's units fight in: the stance every unit is trained with.
HOLD_FIRE = "hold_fire"
MAIN_STANCE = "attack_anything"
# A scout a state shows in another stance this long after its last stance order is ordered to hold fire again.
ORDER_AGAIN_TICKS = 2 * TICKS_PER_SECOND
# The orders a slot's scout is never given: they would have it shoot.
ATTACK_COMMANDS = frozenset({"attack", "attack_move"})
# A scout on its way home joins main once it stands this close to the staging point.
HOME_CELLS = 4


def is_cautious(tuning: ScoutingTuning) -> bool:
    """Whether the slots' scouts are careful: `scout_cautious` with `scout_slots` on."""
    return tuning.scout_slots and tuning.scout_cautious


def stance_order(unit_id: int, stance: str) -> Intent:
    """The order that puts one unit in a stance, at the scouting priority."""
    return Intent(Priority.SCOUT, {"cmd": "set_stance", "units": [unit_id], "stance": stance})


class ScoutStances:
    """The stance orders of one match's slot scouts. See the module's note."""

    def __init__(self) -> None:
        # Unit id -> the tick of the last hold_fire order for each scout holding a slot.
        self._ordered: dict[int, int] = {}
        self._orders: list[Intent] = []

    def hold_fire(self, unit_id: int, tick: int) -> None:
        """Order a unit that has just taken a slot to hold fire."""
        self._ordered[unit_id] = tick
        self._orders.append(stance_order(unit_id, HOLD_FIRE))

    def to_main(self, unit_id: int) -> None:
        """Give a unit that leaves its slot for main main's stance back."""
        self._ordered.pop(unit_id, None)
        self._orders.append(stance_order(unit_id, MAIN_STANCE))

    def keep(self, scouts: Iterable[Unit], tick: int) -> None:
        """Order a scout holding a slot to hold fire again when the state shows it in another stance, its last order
        ORDER_AGAIN_TICKS old or older; forget the scouts that hold no slot any more."""
        held = {scout.id: scout for scout in scouts}
        self._ordered = {unit_id: ordered for unit_id, ordered in self._ordered.items() if unit_id in held}
        for unit_id, ordered in list(self._ordered.items()):
            stance = held[unit_id].stance
            if stance is not None and stance != HOLD_FIRE and tick - ordered >= ORDER_AGAIN_TICKS:
                self.hold_fire(unit_id, tick)

    def orders(self) -> list[Intent]:
        """The stance orders of the cycle, once each."""
        orders, self._orders = self._orders, []
        return orders


@dataclass
class Returning:
    """A scout released from its slot on its way home: its slot's number, its type and the tick of its last move."""

    slot: int
    unit_type: str
    moved_tick: int


class ReturningScouts:
    """The scouts released from their slots on their way to the staging point. See the module's note."""

    def __init__(self) -> None:
        # Unit id -> its way home, for each scout on its way.
        self._returning: dict[int, Returning] = {}

    @property
    def units(self) -> frozenset[int]:
        """The scouts on their way home."""
        return frozenset(self._returning)

    def send_home(self, scout: Unit, slot: int, staging: Cell, tick: int) -> Intent:
        """Put a scout released from a slot on its way home, and its move to the staging point."""
        self._returning[scout.id] = Returning(slot, scout.type, tick)
        return _home_move([scout.id], staging)

    def follow(self, world: World, ctx: MatchContext) -> tuple[dict[int, Returning], list[Intent]]:
        """The scouts that have come home, each taken off its way, and the moves home again of those idle short of it;
        a scout gone, or taken out of the scout group (by base defense), is forgotten. Nothing while the staging point
        is not known."""
        staging = cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells)
        groups = ctx.groups
        self._returning = {
            unit_id: returning
            for unit_id, returning in self._returning.items()
            if unit_id in world.combat_units and groups.group_of(unit_id) == SCOUT
        }
        if staging is None:
            return {}, []
        scouts = {unit_id: world.combat_units[unit_id] for unit_id in self._returning}
        home = {
            unit_id: self._returning.pop(unit_id)
            for unit_id, scout in scouts.items()
            if cells.is_within((scout.x, scout.y), staging, HOME_CELLS)
        }
        again = [
            unit_id
            for unit_id, returning in self._returning.items()
            if scouts[unit_id].idle and world.tick - returning.moved_tick >= ORDER_AGAIN_TICKS
        ]
        for unit_id in again:
            self._returning[unit_id].moved_tick = world.tick
        return home, [_home_move(again, staging)] if again else []


def _home_move(unit_ids: list[int], staging: Cell) -> Intent:
    return Intent(Priority.MOVE, {"cmd": "move", "units": unit_ids, "to": list(staging)})


def without_scout_attacks(intents: Iterable[Intent], ctx: MatchContext) -> list[Intent]:
    """The cycle's intents with the units of the scout group taken out of every attack and attack_move order while the
    slots' scouts are careful; an order left with no unit is dropped. As they are otherwise."""
    intents = list(intents)
    if not is_cautious(ctx.tuning.scouting):
        return intents
    scouts = set(ctx.groups.members(SCOUT))
    if not scouts:
        return intents
    kept = []
    for intent in intents:
        order = intent.order
        if order.get("cmd") not in ATTACK_COMMANDS or not scouts.intersection(order.get("units", ())):
            kept.append(intent)
            continue
        units = [unit_id for unit_id in order["units"] if unit_id not in scouts]
        if units:
            kept.append(replace(intent, order={**order, "units": units}))
    return kept
