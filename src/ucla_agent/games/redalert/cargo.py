"""The agent's units aboard its transports, told from its own orders and the states.

The platform takes a unit that boards a transport out of the state's units
and reports it lost with a `unit_lost` event, although it lives on aboard; an
`unload` brings it back under the same id. No state says that a unit went
aboard. The only evidence is the agent's own order: a unit ordered into a
transport that is gone from the units soon after, while the transport still
stands, went aboard. So the book keeps the boarding orders the agent sends
and takes a unit gone from the units as boarded only on such an order sent
at most `board_window_ticks` before, onto a transport that stands and has
room for it. A unit that goes without one is lost, as it always was, and a
unit is never both aboard and lost. Without a boarding order the book holds
nothing and changes nothing.

A boarding order stands until the unit boards, a later order gives the unit
something else to do, or the transport is lost. A unit may walk longer than
the window to its transport; the order is then sent again (`due`), and the
window runs from the latest order.

A unit leaves the cargo when it is in the state's units again. The engine
keeps a unit's id when it unloads it; should it give one a new id, the new
unit of the same type next to the transport after an unload is taken to be
it (`taken_over`). Cargo that has come out neither way within the window
after the unload is no longer counted aboard. And when the transport is
lost, its cargo is lost with it (`lost`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from ucla_agent.games.redalert.tuning import WorldTuning

if TYPE_CHECKING:
    # The world model holds the book, so its unit type is named here for the types only.
    from ucla_agent.games.redalert.world import Unit

ENTER_TRANSPORT = "enter_transport"
UNLOAD = "unload"
UNIT_LOST_EVENT = "unit_lost"
# The kind a `unit_lost` event of a unit that boarded is given instead, so that nothing counts it as a loss.
BOARDED_EVENT = "boarded"
# A unit that came out of a transport under a new id stands at most this far from it.
UNLOADED_WITHIN_CELLS = 2
# The unit orders that leave a unit doing what it was doing; any other one, unless queued, replaces a boarding order.
_KEEPS_ACTIVITY = frozenset({"set_stance"})


@dataclass(frozen=True, slots=True)
class CargoUnit:
    """One of the agent's units aboard a transport, as it was last seen before it boarded."""

    id: int
    type: str
    # Percent of full health.
    hp: int


@dataclass(frozen=True, slots=True)
class _Boarding:
    """An order that sent a unit into a transport and still stands."""

    transport: int
    # The tick of the state the latest such order was sent with.
    tick: int
    # The order's place among the boarding orders sent, so that of more units than fit those ordered first board.
    sequence: int


def _within(unit: Unit, other: Unit, reach: int) -> bool:
    return (unit.x - other.x) ** 2 + (unit.y - other.y) ** 2 <= reach * reach


class CargoBook:
    """The units aboard each of the agent's transports in one match. See the module's note."""

    def __init__(self, tuning: WorldTuning) -> None:
        self._window = tuning.board_window_ticks
        self._capacity = tuning.transport_capacity
        # Unit id -> the boarding order that stands for it.
        self._boarding: dict[int, _Boarding] = {}
        self._boardings_sent = 0
        # Transport id -> the units aboard it by id, in the order they boarded.
        self._aboard: dict[int, dict[int, CargoUnit]] = {}
        # Unit id aboard -> the tick of the state an unload of its transport was sent with.
        self._unloading: dict[int, int] = {}
        # The units that were aboard a transport lost in the latest state.
        self.lost: tuple[CargoUnit, ...] = ()
        # In the latest state: the id of a unit that was aboard -> the new id it came out under.
        self.taken_over: dict[int, int] = {}

    def cargo(self) -> dict[int, tuple[CargoUnit, ...]]:
        """Transport id -> the units aboard it, in the order they boarded; a transport with none is not listed."""
        return {transport: tuple(units.values()) for transport, units in self._aboard.items() if units}

    def transport_of(self, unit_id: int) -> int | None:
        """The transport a unit is aboard; None for a unit that is not aboard any."""
        return next((transport for transport, units in self._aboard.items() if unit_id in units), None)

    def aboard(self, unit_id: int) -> CargoUnit | None:
        """A unit aboard a transport, as last seen before it boarded; None for a unit that is not aboard any."""
        transport = self.transport_of(unit_id)
        return None if transport is None else self._aboard[transport][unit_id]

    def due(self, tick: int) -> list[dict[str, Any]]:
        """The `enter_transport` orders to send again at `tick`: one for each transport, for the units still outside
        on a boarding order that has stood a whole window since it was last sent."""
        overdue: dict[int, list[int]] = {}
        for unit_id, order in self._boarding.items():
            if tick - order.tick >= self._window:
                overdue.setdefault(order.transport, []).append(unit_id)
        return [
            {"cmd": ENTER_TRANSPORT, "units": sorted(units), "target": transport}
            for transport, units in overdue.items()
        ]

    def note_orders(self, orders: Iterable[Mapping[str, Any]], tick: int) -> None:
        """Take in the orders sent with the state of `tick`: who was ordered into a transport, which transports
        unload, and who was given something else to do."""
        for order in orders:
            cmd, unit_ids = order.get("cmd"), order.get("units", ())
            if cmd == ENTER_TRANSPORT:
                for unit_id in unit_ids:
                    self._boarding[unit_id] = _Boarding(order["target"], tick, self._boardings_sent)
                    self._boardings_sent += 1
            elif cmd == UNLOAD:
                for transport in unit_ids:
                    self._unloading.update(dict.fromkeys(self._aboard.get(transport, ()), tick))
            elif cmd not in _KEEPS_ACTIVITY and not order.get("queued"):
                for unit_id in unit_ids:
                    self._boarding.pop(unit_id, None)

    def update(
        self, tick: int, before: Mapping[int, Unit], now: Mapping[int, Unit], events: Iterable[dict[str, Any]]
    ) -> None:
        """Follow the cargo from the units of the state before to those of the state of `tick`.

        The `unit_lost` events of the units aboard become `boarded` events naming their transport.
        """
        self.lost = self._lose_with_lost_transports(now)
        self.taken_over = {}
        self._let_out(tick, before, now)
        self._take_aboard(tick, before, now)
        self._boarding = {
            unit_id: order for unit_id, order in self._boarding.items() if unit_id in now and order.transport in now
        }
        for event in events:
            transport = self.transport_of(event.get("id")) if event.get("kind") == UNIT_LOST_EVENT else None
            if transport is not None:
                event.update(kind=BOARDED_EVENT, transport=transport)

    def _lose_with_lost_transports(self, now: Mapping[int, Unit]) -> tuple[CargoUnit, ...]:
        """Take the cargo of each transport that is gone out of the book, and return it: it is lost with it."""
        gone = [transport for transport in self._aboard if transport not in now]
        lost = tuple(unit for transport in gone for unit in self._aboard.pop(transport).values())
        for unit in lost:
            self._unloading.pop(unit.id, None)
        return lost

    def _let_out(self, tick: int, before: Mapping[int, Unit], now: Mapping[int, Unit]) -> None:
        """Take out of the cargo the units that came out: in the units again, or as a new unit of their type next
        to their transport after an unload; and those unloaded that came out neither way within the window."""
        aboard = {unit_id for units in self._aboard.values() for unit_id in units}
        newcomers = [unit for unit_id, unit in now.items() if unit_id not in before and unit_id not in aboard]
        for transport, units in self._aboard.items():
            for unit_id in [unit_id for unit_id in units if unit_id in now]:
                self._leave(units, unit_id)
            for newcomer in [unit for unit in newcomers if _within(unit, now[transport], UNLOADED_WITHIN_CELLS)]:
                self._take_over(units, newcomer, newcomers)
            for unit_id in [unit_id for unit_id in units if tick - self._unloading.get(unit_id, tick) > self._window]:
                self._leave(units, unit_id)
        self._aboard = {transport: units for transport, units in self._aboard.items() if units}

    def _take_over(self, units: dict[int, CargoUnit], newcomer: Unit, newcomers: list[Unit]) -> None:
        """Have a new unit next to a transport that unloads take the place of a unit of its type it unloaded."""
        of_its_type = (unit for unit in units.values() if unit.type == newcomer.type and unit.id in self._unloading)
        unloaded = next(of_its_type, None)
        if unloaded is not None:
            self.taken_over[unloaded.id] = newcomer.id
            self._leave(units, unloaded.id)
            newcomers.remove(newcomer)

    def _leave(self, units: dict[int, CargoUnit], unit_id: int) -> None:
        del units[unit_id]
        self._unloading.pop(unit_id, None)

    def _take_aboard(self, tick: int, before: Mapping[int, Unit], now: Mapping[int, Unit]) -> None:
        """Put aboard each unit gone from the units on a boarding order of the window, onto a transport that stands
        and has room, the units ordered in first first."""
        gone = sorted(
            (unit_id for unit_id in before if unit_id not in now and unit_id in self._boarding),
            key=lambda unit_id: self._boarding[unit_id].sequence,
        )
        for unit_id in gone:
            order = self._boarding[unit_id]
            units = self._aboard.get(order.transport, {})
            if tick - order.tick <= self._window and order.transport in now and len(units) < self._capacity:
                unit = before[unit_id]
                self._aboard.setdefault(order.transport, {})[unit_id] = CargoUnit(unit_id, unit.type, unit.hp)
