"""Allied support units: the mechanics that repair main's vehicles and the medics that heal its infantry.

The platform has no order that repairs a unit, and a service depot does not
repair a vehicle that moves onto it. A mechanic does: standing idle on its
default stance, it repairs the damaged vehicles near it with its weapon, as a
medic heals the infantry near it. So with `vehicle_repair` of `[combat]` on,
the Allies keep a few of each where main's damaged units come to them (see
`repair`). The Soviets have neither.

**Training.** A rule of upkeep before the unit queues (`SupportTraining`)
gives the Infantry queue a support unit while the queue is free, outside the
opening, a full hold for the MCV and the saving for a core building or a
refinery, when the money covers the unit and the production reserve. A
mechanic comes first: one for every mechanics_per_tanks of main's tanks, up to
mechanics_max, while fewer stand or are on their way; a mechanic needs a
service depot, and is on offer only once one stands. Then a medic, one for
every medics_per_infantry of main's infantry, up to medics_max, all of
`[composition]`. Only a unit on offer is trained (`available_production`).

**Roles.** Each mechanic keeps the role it was given when first seen: mobile
while fewer than mechanics_max - mechanics_base are, else base while fewer
than mechanics_base are, else mobile. With mechanics_base 0 every mechanic is
mobile.

**Places.** The mobile mechanics and the medics trail main: `TRAIL_CELLS`
behind the centroid of main's long-range units (its slot line), or of main
when it has none, on the side away from the enemy's start. The base mechanics
stand at the base repair point: `BESIDE_CELLS` from the service depot nearest
the base's center, on its side away from the enemy's start. A support unit is
sent to its place by a move when it stands farther than `KEEP_CELLS` from it
and stands idle, has never been sent, or was sent to a cell `KEEP_CELLS` or
more from its place now. Nothing else is ever ordered for it, no attack and no
stance, so that idle it heals what is near. A mobile mechanic that a vehicle
in repair goes to (see `repair`) is held: it is sent nowhere until that
vehicle leaves repair mode, so that it stands still and repairs it. Support
units are in no group: main's orders never reach them.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from collections import Counter
from typing import Callable

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.defended import TANK_TYPES
from ucla_agent.games.redalert.composition import OPENING
from ucla_agent.games.redalert.family import SERVICE_DEPOT
from ucla_agent.games.redalert.fight import centroid, is_near
from ucla_agent.games.redalert.groups import MAIN, SIEGE_TYPES
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.production import INFANTRY_QUEUE
from ucla_agent.games.redalert.tuning import CompositionTuning
from ucla_agent.games.redalert.world import Cell, Unit, World

MECHANIC = "mech"
MEDIC = "medi"
# A mechanic's role: trailing main, or standing at the base repair point.
MOBILE = "mobile"
BASE = "base"
# The mobile mechanics and the medics keep this many cells behind main's long-range units, or main.
TRAIL_CELLS = 3
# The base repair point lies this many cells from the service depot.
BESIDE_CELLS = 3
# A support unit within this many cells of its place is there.
KEEP_CELLS = 2
# The mobile mechanics are a repair zone while one of them stands within this many cells of main's centroid.
MOBILE_REACH_CELLS = 25


def is_of_queue(unit_type: str, queue: str, world: World) -> bool:
    """Whether a type of unit is made in a queue: a vehicle in the Vehicle queue, infantry in the Infantry queue."""
    facts = world.items.get(unit_type)
    return facts is not None and facts.queue == queue


def main_units(world: World, ctx: MatchContext) -> list[Unit]:
    """main's units in the field."""
    return [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]


class SupportTraining:
    """An upkeep rule: a mechanic or a medic for the Infantry queue while one is wanted. See the module's note."""

    def __init__(
        self, saved_for: Callable[[World, MatchContext], str | None], is_saving: Callable[[World, MatchContext], bool]
    ) -> None:
        # The core building the money is saved for, if any, and whether it is saved for one more refinery.
        self._saved_for = saved_for
        self._is_saving = is_saving

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The training order for the support unit wanted next, when the Infantry queue may take it now."""
        if not self._may_train(world, ctx):
            return []
        item = next_support_item(world, ctx)
        if (
            item is None
            or world.economy.spendable < ctx.production.cost(item) + ctx.tuning.upkeep.production_cash_reserve
        ):
            return []
        return [ctx.production.train(item)]

    def _may_train(self, world: World, ctx: MatchContext) -> bool:
        """Whether a support unit may be trained in this cycle: vehicle_repair is on, the opening is over, no full hold
        and no saving for a core building or a refinery is in force, and the Infantry queue is free."""
        if not ctx.tuning.combat.vehicle_repair or ctx.plan == OPENING or ctx.full_hold:
            return False
        if not ctx.production.is_queue_free(INFANTRY_QUEUE):
            return False
        return self._saved_for(world, ctx) is None and not self._is_saving(world, ctx)


def next_support_item(world: World, ctx: MatchContext) -> str | None:
    """The support unit to train next: a mechanic while fewer stand or are on their way than are wanted, else a medic;
    None while neither is wanted or on offer."""
    main = main_units(world, ctx)
    if _is_short_of(MECHANIC, mechanics_wanted(main, world, ctx), world, ctx):
        return MECHANIC
    if _is_short_of(MEDIC, medics_wanted(main, world, ctx), world, ctx):
        return MEDIC
    return None


def mechanics_wanted(main: list[Unit], world: World, ctx: MatchContext) -> int:
    """How many mechanics the base is to have: one for every mechanics_per_tanks of main's tanks, a variant as its
    type, up to mechanics_max."""
    tuning = ctx.tuning.composition
    tanks = sum(1 for unit in main if _type_name(unit.type, world) in TANK_TYPES)
    return min(tuning.mechanics_max, tanks // tuning.mechanics_per_tanks)


def _type_name(unit_type: str, world: World) -> str | None:
    """The type the item table knows a unit type as, a variant as its type; None for one it does not know."""
    facts = world.items.get(unit_type)
    return None if facts is None else facts.name


def medics_wanted(main: list[Unit], world: World, ctx: MatchContext) -> int:
    """How many medics the base is to have: one for every medics_per_infantry of main's infantry, up to medics_max."""
    tuning = ctx.tuning.composition
    infantry = sum(1 for unit in main if is_of_queue(unit.type, INFANTRY_QUEUE, world))
    return min(tuning.medics_max, infantry // tuning.medics_per_infantry)


def _is_short_of(item: str, wanted: int, world: World, ctx: MatchContext) -> bool:
    """Whether a support unit on offer has fewer standing or on their way than `wanted`."""
    production = ctx.production
    return item in world.available_production and production.units(item) + production.pending(item) < wanted


def trail_point(main: list[Unit], world: World) -> Cell | None:
    """Where the mobile mechanics and the medics trail main: TRAIL_CELLS behind the centroid of its long-range units, or
    of main when it has none, on the side away from the enemy's start; None without main or a known enemy start."""
    if not main or world.enemy_base is None or world.map_bounds is None:
        return None
    long_range = [unit for unit in main if unit.type in SIEGE_TYPES]
    anchor = cells.nearest_cell(*centroid(long_range or main))
    return cells.clamped(cells.toward(anchor, world.enemy_base, -TRAIL_CELLS), world.map_bounds)


def base_repair_point(world: World) -> Cell | None:
    """The base repair point: BESIDE_CELLS from the service depot nearest the base's center, on its side away from the
    enemy's start; None while no service depot stands or the map is not known."""
    depots = [building for building in world.buildings.values() if building.type == SERVICE_DEPOT]
    if not depots or world.base_center is None or world.enemy_base is None or world.map_bounds is None:
        return None
    depot = min(depots, key=lambda building: cells.distance((building.x, building.y), world.base_center))
    beside = cells.toward((depot.x, depot.y), world.enemy_base, -BESIDE_CELLS)
    return cells.clamped(beside, world.map_bounds)


class SupportUnits:
    """The Allied mechanics and medics in the field: each mechanic's role, and the moves that keep them in their
    places. See the module's note."""

    def __init__(self) -> None:
        # Mechanic id -> MOBILE or BASE, in the order the mechanics were first seen.
        self._roles: dict[int, str] = {}
        # Support unit id -> the cell it was last sent to.
        self._sent: dict[int, Cell] = {}

    def follow(self, world: World, tuning: CompositionTuning) -> None:
        """Forget the support units that are gone and give each new mechanic its role."""
        mechanics = sorted(unit.id for unit in world.units.values() if unit.type == MECHANIC)
        alive = set(mechanics)
        self._roles = {unit_id: role for unit_id, role in self._roles.items() if unit_id in alive}
        for unit_id in mechanics:
            if unit_id not in self._roles:
                self._roles[unit_id] = _role_for_new(Counter(self._roles.values()), tuning)
        self._sent = {unit_id: cell for unit_id, cell in self._sent.items() if unit_id in world.units}

    def mechanics(self, role: str, world: World) -> list[Unit]:
        """The mechanics of a role, in the order they were first seen."""
        return [world.units[unit_id] for unit_id, of_role in self._roles.items() if of_role == role]

    def mobile(self, unit_id: int, world: World) -> Unit | None:
        """A mobile mechanic by its id; None once it is lost, or for a unit that is none."""
        return world.units[unit_id] if self._roles.get(unit_id) == MOBILE else None

    def nearest_mobile(self, cell: Cell, reach: float, world: World) -> Unit | None:
        """The mobile mechanic nearest a cell, within `reach` cells of it; the first seen of two as near; None for
        none."""
        near = [unit for unit in self.mechanics(MOBILE, world) if cells.is_within((unit.x, unit.y), cell, reach)]
        return min(near, key=lambda unit: cells.distance((unit.x, unit.y), cell), default=None)

    def mobile_zone(self, main: list[Unit], world: World) -> Cell | None:
        """The centroid of the mobile mechanics within MOBILE_REACH_CELLS of main's centroid; None for none."""
        if not main:
            return None
        middle = centroid(main)
        near = [unit for unit in self.mechanics(MOBILE, world) if is_near(middle, unit.x, unit.y, MOBILE_REACH_CELLS)]
        return cells.nearest_cell(*centroid(near)) if near else None

    def base_zone(self, world: World) -> Cell | None:
        """The base repair point while a base mechanic lives; None otherwise."""
        return base_repair_point(world) if self.mechanics(BASE, world) else None

    def orders(self, main: list[Unit], world: World, held: frozenset[int]) -> list[Intent]:
        """The moves that keep the mobile mechanics but those `held` and the medics at main's trail point, and the base
        mechanics at the base repair point; `main` is main's units of the cycle, `held` the mobile mechanics vehicles
        in repair go to, which stand still to repair them."""
        medics = [unit for unit in world.units.values() if unit.type == MEDIC]
        trailing = [*(unit for unit in self.mechanics(MOBILE, world) if unit.id not in held), *medics]
        return [
            *self._keep(trailing, trail_point(main, world)),
            *self._keep(self.mechanics(BASE, world), base_repair_point(world)),
        ]

    def _keep(self, units: list[Unit], place: Cell | None) -> list[Intent]:
        """A move to `place` for the units of `units` due one (see `_is_due`); none without a place."""
        if place is None:
            return []
        going = [unit.id for unit in units if self._is_due(unit, place)]
        self._sent.update(dict.fromkeys(going, place))
        return [Intent(Priority.MOVE, {"cmd": "move", "units": going, "to": list(place)})] if going else []

    def _is_due(self, unit: Unit, place: Cell) -> bool:
        """Whether a support unit away from its place is to be sent there: it stands idle, was never sent, or was sent
        to a cell KEEP_CELLS or more from the place now."""
        if cells.is_within((unit.x, unit.y), place, KEEP_CELLS):
            return False
        sent = self._sent.get(unit.id)
        return unit.idle or sent is None or not cells.is_within(sent, place, KEEP_CELLS)


def _role_for_new(counts: Counter[str], tuning: CompositionTuning) -> str:
    """The role of a mechanic first seen: mobile while fewer than mechanics_max - mechanics_base are, else base while
    fewer than mechanics_base are, else mobile."""
    if counts[MOBILE] >= tuning.mechanics_max - tuning.mechanics_base and counts[BASE] < tuning.mechanics_base:
        return BASE
    return MOBILE
