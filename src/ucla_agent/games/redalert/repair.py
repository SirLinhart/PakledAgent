"""Vehicle repair: a vehicle of main about to be lost leaves main for a repair zone, and rejoins it once healed.

A tank that has lost most of its health is worth saving, and on this platform
it is repaired only by a mechanic standing idle near it (see `support`): a
service depot does not repair a vehicle that moves onto it. So with
`vehicle_repair` of `[combat]` on, the combat manager takes such a vehicle out
of main and sends it where it can be repaired, and lets it rejoin once it has
been. It takes the place of the withdrawal of `combat` for vehicles; infantry
keep that.

**The damage rate.** Every own vehicle's health is kept over the last
repair_window_seconds: its damage rate is the hp it lost in that time, the
first health kept less the latest, per second; never below 0.

**Into repair.** A vehicle of main, not on a crush run, a detachment or a
strike, that cost repair_min_cost or more goes into repair mode when its hp is
repair_below_hp or less; when it is repair_early_hp or less while a mobile
mechanic stands within repair_mechanic_reach_cells of it, since a mechanic
that near heals it well before it is nearly lost; or when it is
repair_ttd_max_hp or less and its hp over its damage rate, the seconds it has
left at that rate, is repair_ttd_seconds or less. None does while base
defense has main under its override or called home, or while no repair zone
is known. A vehicle that
rejoined main unhealed from the staging point goes into repair again only once
its hp has fallen below what it rejoined at. Each vehicle that goes into
repair mode is taken out of main's members while the commander's forward gate
is open, and its worth when the gate opened out of main's worth then (see
`command_gate`): it does not close the gate by itself, and it rejoins main
through the units joining it.

**In repair.** A vehicle in repair mode is none of main's units: it gets
none of main's orders, the commander's retreat override and regroup gather
included, and counts in none of main's figures (the gate, the question, the
body, the estimate of the fight). With a mobile mechanic within
repair_mechanic_reach_cells of it, it goes to the nearest one's cell by an
emergency move, and stops on it or a cell beside it (`MECHANIC_CELLS`). A
mechanic heals only a vehicle that stands still beside it, so the vehicle
keeps that mechanic and gets no other move while it is sent beside it and its
hp rose within the last `STILL_SECONDS`; once its hp has not risen for that
long since it rose or was last sent, it is sent to the nearest mobile mechanic
in reach again. With none in reach, it is kept within `ZONE_CELLS` of its
repair zone, the first of: the centroid of the mobile mechanics within 25
cells of main's centroid; the base repair point while a base mechanic lives;
else the staging point (the Soviets have no mechanics). It is sent there by an
emergency move when it stands farther, and stands idle, was never sent, or
was sent to a cell `ZONE_CELLS` or more from the zone now. While base defense
has main called home, or the vehicle under its override, it is base defense's
and gets no move.

**Rejoining.** A vehicle in repair rejoins main at repair_done_hp. Repairing
at the staging point, with no mechanic, it also rejoins once it stands within
`ZONE_CELLS` of the point while main has no enemy near (no estimate of its
fight), or `STAGING_REJOIN_SECONDS` after it went into repair. One that is no
longer main's, lost or taken by another group, leaves repair mode.

Each vehicle going into repair mode writes a `combat` event of reason `repair`
and phase `enter`, with its hp, damage rate, zone and the nearest mobile
mechanic within repair_mechanic_reach_cells (None for none); each leaving it
one of phase `exit` with its hp and how it ended: `healed`, `waited` or `gone`.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable, Mapping

from ucla_agent.core import records
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.fight import estimate, worth
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.production import VEHICLE_QUEUE
from ucla_agent.games.redalert.support import SupportUnits, is_of_queue
from ucla_agent.games.redalert.tuning import CombatTuning
from ucla_agent.games.redalert.world import Cell, Unit, World

COMBAT_EVENT = "combat"
REPAIR = "repair"
ENTER = "enter"
EXIT = "exit"
# The repair zones, in the order they are preferred.
MOBILE_ZONE = "mobile"
BASE_ZONE = "base"
STAGING_ZONE = "staging"
# How a vehicle left repair mode: healed to repair_done_hp, done waiting at the staging point, or no longer main's.
HEALED = "healed"
WAITED = "waited"
GONE = "gone"
# A vehicle in repair is kept within this many cells of its zone.
ZONE_CELLS = 2
# A vehicle in repair with a mobile mechanic in reach is kept within this many cells of it ...
MECHANIC_CELLS = 1
# ... and sent beside the nearest one again once its hp has not risen for this long.
STILL_SECONDS = 5
_STILL_TICKS = STILL_SECONDS * TICKS_PER_SECOND
# A vehicle repairing at the staging point with no mechanic rejoins main this long after it went into repair.
STAGING_REJOIN_SECONDS = 90
_STAGING_REJOIN_TICKS = STAGING_REJOIN_SECONDS * TICKS_PER_SECOND
_FULL_HEALTH = 100
_RATE_DECIMALS = 1


def is_vehicle(unit_type: str, world: World) -> bool:
    """Whether a type of unit is a vehicle: one the Vehicle queue makes."""
    return is_of_queue(unit_type, VEHICLE_QUEUE, world)


class DamageRates:
    """Every own vehicle's health over the last repair_window_seconds, and the hp it lost per second in that time."""

    def __init__(self) -> None:
        # Vehicle id -> (tick, hp) as seen in the cycles of the window, the oldest first.
        self._seen: dict[int, deque[tuple[int, int]]] = {}

    def note(self, vehicles: Iterable[Unit], tick: int, window_ticks: int) -> None:
        """Keep each vehicle's health of this cycle, and forget what is older than the window and the vehicles gone."""
        seen: dict[int, deque[tuple[int, int]]] = {}
        for unit in vehicles:
            history = self._seen.get(unit.id, deque())
            history.append((tick, unit.hp))
            while tick - history[0][0] > window_ticks:
                history.popleft()
            seen[unit.id] = history
        self._seen = seen

    def rate(self, unit_id: int) -> float:
        """The hp a vehicle lost per second over the window: the first health kept less the latest, over the seconds
        between them; 0 for one seen in a single cycle or that lost none."""
        history = self._seen.get(unit_id)
        if not history:
            return 0.0
        (first_tick, first_hp), (last_tick, last_hp) = history[0], history[-1]
        seconds = (last_tick - first_tick) / TICKS_PER_SECOND
        return max(0.0, (first_hp - last_hp) / seconds) if seconds > 0 else 0.0


@dataclass
class _Repairing:
    """A vehicle in repair mode: the tick it went in; its hp of the latest cycle; the tick its hp last rose or it was
    last sent, whichever is later; the cell it was last sent to, None while never sent; and the mobile mechanic it
    goes to, None for none."""

    since: int
    hp: int
    still_since: int
    sent: Cell | None = None
    mechanic: int | None = None


class VehicleRepair:
    """main's vehicles in repair mode, their damage rates, their repair zone and the support units. See the module's
    note."""

    def __init__(self) -> None:
        self._rates = DamageRates()
        self._repairing: dict[int, _Repairing] = {}
        # Vehicle id -> the hp it rejoined main at unhealed from the staging point.
        self._rejoined_hp: dict[int, int] = {}
        # The kind and cell of the repair zone of the latest cycle; None while none is known.
        self._zone: tuple[str, Cell] | None = None
        self.support = SupportUnits()

    @property
    def units(self) -> frozenset[int]:
        """The vehicles in repair mode, none of main's units while they are."""
        return frozenset(self._repairing)

    def follow(self, main: Mapping[int, Unit], busy: frozenset[int], world: World, ctx: MatchContext) -> None:
        """Bring repair mode up to date with this cycle: the damage rates and the support units noted, the repair zone
        chosen, the vehicles healed or done waiting let rejoin main and those about to be lost taken out of it. `main`
        is main's units of the cycle, those in repair mode among them; `busy` those on a crush run, a detachment or a
        strike."""
        tuning = ctx.tuning.combat
        vehicles = [unit for unit in world.combat_units.values() if is_vehicle(unit.type, world)]
        self._rates.note(vehicles, world.tick, tuning.repair_window_seconds * TICKS_PER_SECOND)
        self.support.follow(world, ctx.tuning.composition)
        if not tuning.vehicle_repair:
            return
        rest = [unit for unit_id, unit in main.items() if unit_id not in self._repairing]
        self._zone = self._choose_zone(rest, world, ctx)
        self._let_rejoin(rest, world, ctx)
        self._note_health(world)
        self._take(rest, busy, world, ctx)

    def _note_health(self, world: World) -> None:
        """Note each vehicle in repair's hp of this cycle, and the tick when it rose."""
        for unit_id, state in self._repairing.items():
            hp = world.combat_units[unit_id].hp
            if hp > state.hp:
                state.still_since = world.tick
            state.hp = hp

    def _choose_zone(self, main: list[Unit], world: World, ctx: MatchContext) -> tuple[str, Cell] | None:
        """The repair zone of this cycle: the mobile mechanics near main, else the base repair point with a base
        mechanic, else the staging point; None while not even the staging point is known."""
        mobile = self.support.mobile_zone(main, world)
        if mobile is not None:
            return MOBILE_ZONE, mobile
        base = self.support.base_zone(world)
        if base is not None:
            return BASE_ZONE, base
        point = cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells)
        return None if point is None else (STAGING_ZONE, point)

    def _let_rejoin(self, main: list[Unit], world: World, ctx: MatchContext) -> None:
        """End repair mode for the vehicles that are no longer main's, are healed, or are done waiting at the staging
        point; `main` is main's units not in repair."""
        tuning = ctx.tuning.combat
        self._rejoined_hp = {unit_id: hp for unit_id, hp in self._rejoined_hp.items() if unit_id in world.combat_units}
        for unit_id, state in list(self._repairing.items()):
            unit = world.combat_units.get(unit_id)
            if unit is None or ctx.groups.group_of(unit_id) != MAIN:
                self._end(unit_id, GONE, unit, state, world)
            elif unit.hp >= tuning.repair_done_hp:
                self._end(unit_id, HEALED, unit, state, world)
            elif self._is_done_waiting(unit, state, main, world, ctx):
                self._rejoined_hp[unit_id] = unit.hp
                self._end(unit_id, WAITED, unit, state, world)

    def _is_done_waiting(
        self, unit: Unit, state: _Repairing, main: list[Unit], world: World, ctx: MatchContext
    ) -> bool:
        """Whether a vehicle repairing at the staging point with no mechanic is to rejoin main: it stands there while
        main has no enemy near, or it went into repair STAGING_REJOIN_SECONDS ago."""
        if self._zone is None or self._zone[0] != STAGING_ZONE:
            return False
        if world.tick - state.since >= _STAGING_REJOIN_TICKS:
            return True
        is_there = cells.is_within((unit.x, unit.y), self._zone[1], ZONE_CELLS)
        return is_there and (not main or estimate(main, world, ctx.tuning) is None)

    def _take(self, main: list[Unit], busy: frozenset[int], world: World, ctx: MatchContext) -> None:
        """Put into repair mode the vehicles of main about to be lost (see `_is_due`), each with a `combat` event,
        and take them out of main's members of the commander's forward gate; none while base defense has main or no
        repair zone is known."""
        groups = ctx.groups
        if self._zone is None or groups.has_defense_override(MAIN) or groups.is_recalled(MAIN):
            return
        tuning = ctx.tuning.combat
        taken = [unit for unit in main if unit.id not in busy and self._is_due(unit, world, tuning)]
        for unit in taken:
            self._enter(unit, self._zone[0], world, tuning)
        if taken and ctx.commander is not None:
            ctx.commander.gate.release(unit.id for unit in taken)

    def _enter(self, unit: Unit, zone: str, world: World, tuning: CombatTuning) -> None:
        """Put a vehicle into repair mode, with a `combat` event."""
        self._repairing[unit.id] = _Repairing(world.tick, unit.hp, world.tick)
        self._rejoined_hp.pop(unit.id, None)
        mechanic = self._nearest_mechanic(unit, world, tuning)
        rate = round(self._rates.rate(unit.id), _RATE_DECIMALS)
        _log(ENTER, unit, world.tick, rate=rate, zone=zone, mechanic=None if mechanic is None else mechanic.id)

    def _is_due(self, unit: Unit, world: World, tuning: CombatTuning) -> bool:
        """Whether a unit of main goes into repair mode: a vehicle that cost repair_min_cost or more, at
        repair_below_hp or less, at repair_early_hp or less with a mobile mechanic within
        repair_mechanic_reach_cells, or at repair_ttd_max_hp or less with repair_ttd_seconds or less left at its damage
        rate; one that rejoined unhealed only once below the hp it rejoined at."""
        if not _is_worth_repairing(unit, world, tuning) or self._is_back_unhealed(unit):
            return False
        if unit.hp <= tuning.repair_below_hp:
            return True
        # A mechanic near heals a vehicle still worth saving long before it is nearly lost.
        if unit.hp <= tuning.repair_early_hp and self._nearest_mechanic(unit, world, tuning) is not None:
            return True
        rate = self._rates.rate(unit.id)
        return rate > 0 and unit.hp <= tuning.repair_ttd_max_hp and unit.hp / rate <= tuning.repair_ttd_seconds

    def _is_back_unhealed(self, unit: Unit) -> bool:
        """Whether a vehicle rejoined main unhealed from the staging point and has lost no hp since."""
        rejoined = self._rejoined_hp.get(unit.id)
        return rejoined is not None and unit.hp >= rejoined

    def _nearest_mechanic(self, unit: Unit, world: World, tuning: CombatTuning) -> Unit | None:
        """The mobile mechanic nearest a vehicle within repair_mechanic_reach_cells of it; None for none."""
        return self.support.nearest_mobile((unit.x, unit.y), tuning.repair_mechanic_reach_cells, world)

    def _end(self, unit_id: int, ended: str, unit: Unit | None, state: _Repairing, world: World) -> None:
        """Take a vehicle out of repair mode, with a `combat` event."""
        del self._repairing[unit_id]
        seconds = (world.tick - state.since) // TICKS_PER_SECOND
        data = {"tick": world.tick, "reason": REPAIR, "phase": EXIT, "unit": unit_id, "ended": ended}
        records.log(COMBAT_EVENT, {**data, "hp": None if unit is None else unit.hp, "seconds": seconds})

    def orders(self, main: list[Unit], world: World, ctx: MatchContext) -> list[Intent]:
        """The orders of this cycle: the moves of the vehicles in repair (see `_moves`), none while base defense has
        main called home; and the support units' moves (see `support`), none for a mobile mechanic a vehicle in repair
        goes to. `main` is main's units not in repair. None with vehicle_repair off."""
        if not ctx.tuning.combat.vehicle_repair:
            return []
        moves = [] if ctx.groups.is_recalled(MAIN) else self._moves(world, ctx)
        return [*moves, *self.support.orders(main, world, self._held())]

    def _moves(self, world: World, ctx: MatchContext) -> list[Intent]:
        """An emergency move for each vehicle in repair due one (see `_move_of`), the vehicles sent to one cell in one
        order; none for a vehicle base defense has under its override."""
        going: dict[Cell, list[int]] = {}
        for unit_id, state in self._repairing.items():
            if ctx.groups.is_overridden(unit_id):
                continue
            cell = self._move_of(world.combat_units[unit_id], state, world, ctx.tuning.combat)
            if cell is not None:
                state.sent, state.still_since = cell, world.tick
                going.setdefault(cell, []).append(unit_id)
        return [
            Intent(Priority.EMERGENCY, {"cmd": "move", "units": ids, "to": list(cell)}) for cell, ids in going.items()
        ]

    def _held(self) -> frozenset[int]:
        """The mobile mechanics the vehicles in repair go to: each stands still to repair its vehicle rather than trail
        main, until that vehicle leaves repair mode."""
        return frozenset(state.mechanic for state in self._repairing.values() if state.mechanic is not None)

    def _move_of(self, unit: Unit, state: _Repairing, world: World, tuning: CombatTuning) -> Cell | None:
        """The cell a vehicle in repair is sent to in this cycle; None for no move. With a mobile mechanic in reach
        (see `_mechanic_of`) it is that mechanic's cell, when the vehicle was never sent beside it or its hp has stood
        still for STILL_SECONDS: a vehicle beside its mechanic whose hp rises is left where it is. Else it is the
        repair zone, when `_is_move_due`."""
        mechanic = self._mechanic_of(unit, state, world, tuning)
        state.mechanic = None if mechanic is None else mechanic.id
        if mechanic is None:
            return self._zone_move(unit, state)
        cell = (mechanic.x, mechanic.y)
        is_due = state.sent is None or not _is_beside(state.sent, cell) or _is_still(state, world.tick)
        return cell if is_due else None

    def _zone_move(self, unit: Unit, state: _Repairing) -> Cell | None:
        """The repair zone's cell when a vehicle in repair is due a move there (see `_is_move_due`); None otherwise or
        while no zone is known."""
        if self._zone is None or not _is_move_due(unit, state, self._zone[1]):
            return None
        return self._zone[1]

    def _mechanic_of(self, unit: Unit, state: _Repairing, world: World, tuning: CombatTuning) -> Unit | None:
        """The mobile mechanic a vehicle in repair goes to: the one it went to while that one stays within
        repair_mechanic_reach_cells and the vehicle's hp has not stood still for STILL_SECONDS; else the nearest
        within that reach; None for none."""
        kept = None if state.mechanic is None else self.support.mobile(state.mechanic, world)
        reach = tuning.repair_mechanic_reach_cells
        is_kept = kept is not None and cells.is_within((kept.x, kept.y), (unit.x, unit.y), reach)
        if is_kept and not _is_still(state, world.tick):
            return kept
        return self._nearest_mechanic(unit, world, tuning)


def _is_beside(first: Cell, second: Cell) -> bool:
    """Whether two cells are at most MECHANIC_CELLS apart along each axis: a diagonal neighbor is beside, since the
    platform stops a vehicle sent onto a mechanic's cell on any of its neighbors."""
    return max(abs(first[0] - second[0]), abs(first[1] - second[1])) <= MECHANIC_CELLS


def _is_still(state: _Repairing, tick: int) -> bool:
    """Whether a vehicle in repair has gained no hp and been sent nowhere for STILL_SECONDS."""
    return tick - state.still_since >= _STILL_TICKS


def _is_worth_repairing(unit: Unit, world: World, tuning: CombatTuning) -> bool:
    """Whether a unit is a vehicle that cost repair_min_cost or more."""
    return is_vehicle(unit.type, world) and worth(unit.type, _FULL_HEALTH, world) >= tuning.repair_min_cost


def _is_move_due(unit: Unit, state: _Repairing, zone: Cell) -> bool:
    """Whether a vehicle in repair is to be sent to its zone: it stands farther than ZONE_CELLS from it, and stands
    idle, was never sent, or was sent to a cell ZONE_CELLS or more from the zone now."""
    if cells.is_within((unit.x, unit.y), zone, ZONE_CELLS):
        return False
    return unit.idle or state.sent is None or not cells.is_within(state.sent, zone, ZONE_CELLS)


def _log(phase: str, unit: Unit, tick: int, **more: object) -> None:
    """Write one `combat` event of reason `repair`."""
    data = {"tick": tick, "reason": REPAIR, "phase": phase, "unit": unit.id, "type": unit.type, "hp": unit.hp}
    records.log(COMBAT_EVENT, {**data, **more})
