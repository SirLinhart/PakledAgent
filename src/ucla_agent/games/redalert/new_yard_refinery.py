"""Where the refinery an expansion yard claims goes, with `new_yard_refinery_until_standing` of [expansion] on.

A yard that deploys by the field of its site earns nothing until a refinery
stands against that field, and the engine may refuse the place the refinery
is asked onto and put it at home instead. So with the switch on the claim
(see `expansion`) lasts until a refinery of ours stands that counts against
the field (`refinery_fields.field_counted_from`), or the yard is lost: a
refinery proposed, ordered or placed ends nothing, and one put elsewhere
leaves the claim open.

Its refinery has one place, worked out the same way when it is ordered and
when it is placed (`ClaimedSpots`): against the field, from the places of the
new yard's ring and those next to a building, off the places asked for
lately, the places the engine refused and the ground within reach of a known
enemy ground defense, and counting against the field. A refinery is ordered
only while such a place exists, so that none waits unplaced and is cancelled
over and over. A refused place is skipped for the next one; with every place
against the field refused, those refused `new_yard_refused_retry_seconds` or
more ago are tried again, the oldest first, for the engine may have refused
a place only for a unit that stood on it.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from ucla_agent.core import records
from ucla_agent.games.redalert import placement, refinery_fields
from ucla_agent.games.redalert.family import REFINERY
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.placement import Box, PlacementChoice, RefusedPlace
from ucla_agent.games.redalert.terrain import OrePatch
from ucla_agent.games.redalert.world import Cell, Unit, World

# The rule, and the reason, of the refinery claimed for the field a new expansion yard deployed by; the reason of each
# later placement of the same claim; and the reason written once a refinery stands against the field.
NEW_YARD_RULE = "new_yard"
NEW_YARD_RETRY_REASON = "new_yard_retry"
NEW_YARD_STANDING_REASON = "new_yard_standing"
# How far around the place the own units are moved off before the refinery is placed: the engine wants the place clear.
UNITS_CLEAR_CELLS = 1
# How long the place order waits for the units to move off; it is sent anyway after that.
UNITS_CLEAR_TICKS = 5 * TICKS_PER_SECOND


def is_on(ctx: MatchContext) -> bool:
    """Whether a new yard's claim lasts until its refinery stands: `new_yard_refinery_until_standing` is on."""
    return ctx.tuning.expansion.new_yard_refinery_until_standing


def has_standing_refinery(field: OrePatch, world: World, ctx: MatchContext) -> bool:
    """Whether an own refinery stands that counts against a field (`refinery_fields.counts`, nothing asked counted)."""
    return refinery_fields.counts(world, ctx, ())[field] > 0


class ClaimedSpots:
    """The place of the refinery claimed for a new yard's field (see the module's note), kept while what it is worked
    out from stays the same: it is asked for in every cycle the claim lasts, and the places by a yard take a few
    milliseconds in a large base."""

    def __init__(self) -> None:
        self._inputs: tuple[object, ...] | None = None
        self._choice: PlacementChoice | None = None

    def spot(
        self, world: World, ctx: MatchContext, promised: Iterable[Box], refused: Sequence[RefusedPlace]
    ) -> PlacementChoice | None:
        """The place against `ctx.new_yard_field` from the yard at `ctx.new_yard_cell`, off the `promised` and
        `refused` places, with those refused long enough ago let back one at a time, the oldest first, when no other
        is left; None without a claim or a place."""
        field, yard = ctx.new_yard_field, ctx.new_yard_cell
        if field is None or yard is None:
            return None
        promised = tuple(promised)
        retry_from = world.tick - ctx.tuning.expansion.new_yard_refused_retry_seconds * TICKS_PER_SECOND
        oldest_first = sorted(refused, key=lambda place: place.tick)
        retried = tuple(place.box for place in oldest_first if place.tick <= retry_from)
        inputs = (
            field,
            yard,
            ctx.terrain,
            ctx.tuning.placement,
            world.map_bounds,
            tuple(sorted((building.type, building.x, building.y) for building in world.buildings.values())),
            tuple(sorted((record.type, record.x, record.y) for record in world.known_enemy_defenses)),
            promised,
            tuple(place.box for place in refused),
            retried,
        )
        if inputs != self._inputs:
            self._inputs = inputs
            self._choice = _first_free(field, yard, world, ctx, promised, [place.box for place in refused], retried)
        return self._choice


def _first_free(
    field: OrePatch,
    yard: Cell,
    world: World,
    ctx: MatchContext,
    promised: tuple[Box, ...],
    kept_off: list[Box],
    retried: tuple[Box, ...],
) -> PlacementChoice | None:
    """The place against the field off every refused place, else with the `retried` ones let back one more at a
    time; None when none is free."""
    choice = _against(field, yard, world, ctx, promised, kept_off)
    for box in retried:
        if choice is not None:
            return choice
        kept_off.remove(box)
        choice = _against(field, yard, world, ctx, promised, kept_off)
    return choice


def _against(
    field: OrePatch, yard: Cell, world: World, ctx: MatchContext, promised: tuple[Box, ...], kept_off: list[Box]
) -> PlacementChoice | None:
    """The place by the yard against the field that counts against it (`field_counted_from`), off the `promised` and
    `kept_off` places and the ground within reach of a known enemy ground defense; None without one."""
    tuning = ctx.tuning.placement
    terrain = placement.with_refused(ctx.terrain, (*kept_off, *refinery_fields.defended_boxes(world, ctx)))
    places = placement.RefineryPlaces(world, terrain, tuning, promised)

    def counts_against(box: Box) -> bool:
        return refinery_fields.field_counted_from(box, ctx.terrain, tuning) == field

    return places.against(field, NEW_YARD_RULE, keep=counts_against, anchor=yard)


def units_on(cell: Cell, world: World) -> list[Unit]:
    """The own units that stand on a refinery's place at a cell or within `UNITS_CLEAR_CELLS` of it."""
    area = placement.box_at(REFINERY, cell).grown(UNITS_CLEAR_CELLS)
    return [unit for unit in world.units.values() if area.holds_any({(unit.x, unit.y)})]


def log_standing(field: OrePatch, world: World) -> None:
    """Write the `refinery_field` event of the reason `new_yard_standing`: a refinery stands against the claimed field,
    which ends the claim."""
    data = {
        "tick": world.tick,
        "cell": None,
        "rule": NEW_YARD_RULE,
        "field_center": list(field.center),
        "field_cells": field.size,
        "served_by": None,
        "ore_distance_cells": None,
        "reason": NEW_YARD_STANDING_REASON,
    }
    records.log(refinery_fields.REFINERY_FIELD_EVENT, data)
