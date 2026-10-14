"""Where a refinery goes with `refinery_unserved_only` of [placement] on: to a field no refinery of ours serves.

A refinery placed by the nearest field's center lands on the home fields again
and again, and a base whose refineries share two fields mines them out while
the fields around it stay untouched. So with the switch on, each refinery of
ours serves one field (`served_field`): the field whose ore lies nearest its
footprint by walking round cliffs, water and trees (see `terrain`), the one
whose center lies nearer on a tie,
when that ore lies within `refinery_serves_cells` of it; farther from every
field it serves none. That is the field its harvesters work, and the harvester
room (`harvester_room`) counts a field's refineries by the same rule, so that
the two never disagree. A refinery the engine put a cell or two farther from
the ore than it was asked onto still serves the field, so the field gets no
other. A refinery asked onto a place lately, or on order for a field, counts
as standing against it. Every refinery after the opening's is placed:

1. against an unserved field with at least `mcv_site_min_ore_cells` of
   [expansion] ore cells, from a place that serves that field: the fields
   whose nearest own yard is an expansion yard (not the first) come first,
   then the field nearest an own yard;
2. only when no unserved field can be stood against, as the second refinery of
   a field served once with at least `refinery_second_min_cells` ore cells,
   its middle at least `refinery_second_spacing_cells` from the middle of every
   other refinery of ours, so that the two do not share one patch of ore (the
   value shipped is larger than any known map's field, so that a field gets
   one refinery);
3. failing that, the base is stuck: as the second refinery of a field served
   once with at least `refinery_second_when_stuck_cells` ore cells, spaced the
   same way, of the rule `second_when_stuck`. A large field's ore pays for two
   refineries' harvesters, and a base held at its refineries for want of an
   unserved field earns nothing more. This is the one exception to one
   refinery a field;
4. otherwise nowhere: no refinery is ordered, and a `refinery_field` event of
   the reason `waiting_unserved` says so, at most once a minute.

For one refinery a field, a refinery of ours that serves no field counts
against the field whose ore lies nearest its footprint within
`refinery_counts_cells` (`field_counted_from`): the opening places its refineries
toward a field as near as the base reaches, and the harvesters of one that
stands farther than `refinery_serves_cells` from the field still work it. Were
it not counted, the field would count as unserved and take another refinery.
The unserved test, the opening's place and a new yard's claim count so
(`counts`); the harvester room keeps to the field each serves.

A field the move to fresh ore marks dry (`ctx.dry_fields`) is never chosen: its
harvesters would find nothing to load. A refinery that is lost leaves its field
unserved, so that one may be built against it again.

A refinery is never placed with its footprint within a known enemy ground
defense's range plus `refinery_enemy_defense_margin_cells`: its harvesters
would unload under fire.

The served test and the spacing test take an own refinery's footprint from its
reported cell less `REPORTED_OFFSET`: the state reports a building of two
cells or more across at the middle cell of its footprint, one cell right and
one down of its top-left cell, also when the engine put it elsewhere than
asked. Taken from the reported cell, a refinery would count as a cell farther
from a field's ore than it stands.

The opening's refineries keep their place, so that the first minutes are as
measured: while fewer than two own refineries stand or are planned, a refinery
is placed as without the switch, but one whose place would count against a
field another refinery of ours counts against, or a dry field, goes against
an unserved field as in 1. when one can be stood against; only without one is
it moved to a place against its field spaced as a second is, whatever the
field's size, when there is one: the refinery stands built, and goes somewhere.
"""

from __future__ import annotations

import functools
import math
from collections import Counter
from typing import Callable, Iterable

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, placement
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD, MCV, REFINERY
from ucla_agent.games.redalert.items import TICKS_PER_SECOND, is_air_only
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.placement import Box, PlacementChoice
from ucla_agent.games.redalert.production import Production
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import PlacementTuning
from ucla_agent.games.redalert.world import Building, Cell, World

# The rules a refinery's field is given by, as its `refinery_field` event names them: an unserved field, and the
# second refinery of a large field served once.
FIELD_UNSERVED = "unserved"
FIELD_SECOND = "second"
# The rule of a second refinery on a large field while no unserved field can be stood against.
FIELD_SECOND_WHEN_STUCK = "second_when_stuck"
# The `refinery_field` event's reason while no refinery is ordered for want of a field.
WAITING_UNSERVED_REASON = "waiting_unserved"
REFINERY_FIELD_EVENT = "refinery_field"
# How many refineries the opening builds: while fewer stand or are planned, a refinery keeps the opening's place.
OPENING_REFINERIES = 2
# A `waiting_unserved` event is written at most once in this many ticks while the wait lasts.
WAITING_EVERY_TICKS = 60 * TICKS_PER_SECOND
# The `refinery_field` event's reason, once a window, while a refinery for a field one of ours serves waits for an MCV
# expansion (`McvExpansionWindow`).
HELD_FOR_MCV_REASON = "held_for_mcv"
# An expansion window lasts this long at the most from the cycle it opens, and ends this long after the last cycle with
# a site chosen or an MCV on order or out.
HOLD_MAX_TICKS = 120 * TICKS_PER_SECOND
HOLD_NO_SITE_TICKS = 60 * TICKS_PER_SECOND
# Where the state reports a refinery from the top-left cell of its footprint: the footprint's middle cell, as placement
# gives it for every type.
REPORTED_OFFSET: Cell = placement.reported_offset(REFINERY)
# How many boxes the field each serves is kept for: a match asks after a few hundred places at the most.
_SERVED_CACHE_SIZE = 4096


def is_on(ctx: MatchContext) -> bool:
    """Whether refineries go to unserved fields: `refinery_unserved_only` of [placement] is on."""
    return ctx.tuning.placement.refinery_unserved_only


def is_opening(production: Production, *, placing: bool) -> bool:
    """Whether the refinery ordered now, or with `placing` the one placed now, is one of the opening's: fewer than
    `OPENING_REFINERIES` other own refineries stand or are planned."""
    others = production.planned(REFINERY) - (1 if placing else 0)
    return others < OPENING_REFINERIES


def served(world: World, terrain: Terrain, reach: int, asked: Iterable[OrePatch]) -> Counter[OrePatch]:
    """How many refineries of ours serve each field: those standing that serve it (`served_field`, within `reach`),
    and one for each time it is among the `asked` fields."""
    standing = (
        served_field(building, terrain, reach) for building in world.buildings.values() if building.type == REFINERY
    )
    return Counter(field for field in standing if field is not None) + Counter(asked)


def served_field(refinery: Building, terrain: Terrain, reach: int) -> OrePatch | None:
    """The field an own refinery serves: the one a refinery covering its footprint (`footprint_cell`) serves (see
    `field_served_from`)."""
    return field_served_from(placement.box_at(REFINERY, footprint_cell(refinery)), terrain, reach)


def counts(world: World, ctx: MatchContext, asked: Iterable[OrePatch]) -> Counter[OrePatch]:
    """How many refineries of ours each field has for one refinery a field: those that serve it and the `asked` ones
    (`served`), and each standing refinery that serves no field but counts against it (`field_counted_from`)."""
    tuning, terrain = ctx.tuning.placement, ctx.terrain
    boxes = [
        placement.box_at(REFINERY, footprint_cell(building))
        for building in world.buildings.values()
        if building.type == REFINERY
    ]
    beyond = [
        field_counted_from(box, terrain, tuning)
        for box in boxes
        if field_served_from(box, terrain, tuning.refinery_serves_cells) is None
    ]
    standing = served(world, terrain, tuning.refinery_serves_cells, asked)
    return standing + Counter(field for field in beyond if field is not None)


def field_counted_from(box: Box, terrain: Terrain, tuning: PlacementTuning) -> OrePatch | None:
    """The field a refinery covering a box counts against for one refinery a field: the one it serves
    (`field_served_from`, within `refinery_serves_cells`); serving none, the field whose ore lies nearest it within
    `refinery_counts_cells`, whose ore its harvesters work all the same; None farther from every field."""
    served_now = field_served_from(box, terrain, tuning.refinery_serves_cells)
    return served_now or field_served_from(box, terrain, tuning.refinery_counts_cells)


def field_served_from(box: Box, terrain: Terrain, reach: int) -> OrePatch | None:
    """The field a refinery covering a box serves: the one whose ore lies nearest the box by walking
    (`Terrain.walked_distance`, a king's moves on open ground), the one whose center lies nearer the box's middle on a
    tie, when that ore lies within `reach` of it; None when no field's does, and on a map whose fields are not known. A
    refinery across a cliff from a field's ore serves it no more than its harvesters can drive to it."""
    return _nearest_field(box, terrain, reach)


@functools.lru_cache(maxsize=_SERVED_CACHE_SIZE)
def _nearest_field(box: Box, terrain: Terrain, reach: int) -> OrePatch | None:
    """`field_served_from` for a terrain, worked out once for each box."""
    middle, footprint = box.center, box.cells()
    distances = {field: terrain.walked_distance(footprint, field, reach) for field in terrain.ore_patches}
    within = [field for field, distance in distances.items() if distance is not None]
    return min(within, key=lambda field: (distances[field], math.dist(middle, field.center)), default=None)


def choose(
    world: World,
    ctx: MatchContext,
    promised: Iterable[Box] = (),
    refused: Iterable[Box] = (),
    asked: Iterable[OrePatch] = (),
    asked_cells: Iterable[Cell] = (),
) -> PlacementChoice | None:
    """Where the next refinery goes (see the module's note): an unserved field's place, else a second refinery's on a
    field of `refinery_second_min_cells`, else, stuck, one on a field of `refinery_second_when_stuck_cells`; None when
    none has a place. The `asked` fields and the middles of the refineries asked onto a place (`asked_cells`) count as
    refineries standing; no place is taken from the `promised` and `refused` ones, nor within reach of a known enemy
    ground defense, and no dry field is chosen."""
    tuning = ctx.tuning.placement
    terrain = placement.with_refused(ctx.terrain, (*refused, *_defended_boxes(world, ctx)))
    asked = list(asked)
    field_counts = counts(world, ctx, asked)
    places = placement.RefineryPlaces(world, terrain, tuning, tuple(promised))
    choice = _unserved_place(field_counts, places, world, ctx)
    if choice is not None:
        return choice
    big = _served_once(field_counts, tuning.refinery_second_min_cells, ctx)
    second = _second(big, places, world, ctx, asked_cells)
    if second is not None:
        return second
    stuck = _served_once(field_counts, tuning.refinery_second_when_stuck_cells, ctx)
    return _second(stuck, places, world, ctx, asked_cells, FIELD_SECOND_WHEN_STUCK)


def _served_once(field_counts: Counter[OrePatch], least: int, ctx: MatchContext) -> list[OrePatch]:
    """The fields that are not dry with one refinery of ours and at least `least` ore cells."""
    return [
        field
        for field in ctx.terrain.ore_patches
        if field_counts[field] == 1 and field.size >= least and field not in ctx.dry_fields
    ]


def spaced_opening(
    today: PlacementChoice,
    world: World,
    ctx: MatchContext,
    promised: Iterable[Box] = (),
    refused: Iterable[Box] = (),
    asked: Iterable[OrePatch] = (),
    asked_cells: Iterable[Cell] = (),
) -> PlacementChoice:
    """The place of one of the opening's refineries: `today`'s while it counts against no field (`field_counted_from`),
    or against one that no refinery of ours counts against and that is not dry; else an unserved field's place as
    `choose` gives one, when there is one; else, for a field that is not dry, `today`'s when it lies
    `refinery_second_spacing_cells` from every other refinery of ours, or the place against that field spaced as a
    second is, whatever the field's size, when there is one."""
    tuning = ctx.tuning.placement
    if today.cell is None:
        return today
    box = placement.box_at(REFINERY, today.cell)
    field_counts = counts(world, ctx, asked)
    field = field_counted_from(box, ctx.terrain, tuning)
    if field is None or (not field_counts[field] and field not in ctx.dry_fields):
        return today
    terrain = placement.with_refused(ctx.terrain, refused)
    places = placement.RefineryPlaces(world, terrain, tuning, tuple(promised))
    unserved = _unserved_place(field_counts, places, world, ctx)
    if unserved is not None:
        return unserved
    others = _other_middles(world, asked_cells)
    if field in ctx.dry_fields or _is_spaced(box, others, tuning.refinery_second_spacing_cells):
        return today
    return _second([field], places, world, ctx, asked_cells) or today


class McvExpansionWindow:
    """Whether an MCV expansion is under way, with `hold_refinery_for_mcv` of [expansion] on: from the cycle the next
    MCV's site is chosen (`ctx.repeat_mcv_site`), through the MCV's order and its way, until it deploys or is lost,
    `HOLD_NO_SITE_TICKS` pass with no site chosen and no MCV on order or out, or `HOLD_MAX_TICKS` pass since the window
    opened. An expansion whose window ran out of time opens none again until it is over, so that the window always
    ends. The money of a refinery for a field one of ours serves already is the MCV's while it lasts."""

    def __init__(self) -> None:
        # The tick the window opened at; None while it is closed.
        self.opened_at: int | None = None
        # The last tick with a site chosen or an MCV on order or out, and whether an MCV was out in the window.
        self._active_at = 0
        self._has_mcv_out = False
        # Whether the window ran out of time while the expansion still goes on.
        self._is_spent = False

    def is_open(self, world: World, ctx: MatchContext) -> bool:
        """Whether the window is open in this cycle, opening or closing it as the expansion goes."""
        if not ctx.tuning.expansion.hold_refinery_for_mcv:
            self.opened_at = None
            return False
        is_mcv_out = any(unit.type == MCV for unit in world.units.values())
        is_active = ctx.repeat_mcv_site is not None or ctx.production.pending(MCV) > 0 or is_mcv_out
        if is_active:
            self._active_at = world.tick
        if self.opened_at is not None and self._has_ended(world.tick, is_mcv_out):
            self.opened_at = None
        elif self.opened_at is None:
            self._open_when(is_active, is_mcv_out, world.tick)
        self._has_mcv_out = self._has_mcv_out or is_mcv_out
        return self.opened_at is not None

    def _has_ended(self, tick: int, is_mcv_out: bool) -> bool:
        """Whether the open window ends now: its MCV deployed or was lost, it ran out of time (which leaves the
        expansion spent), or no site was chosen and no MCV was on order or out for `HOLD_NO_SITE_TICKS`."""
        self._is_spent = tick - self.opened_at > HOLD_MAX_TICKS
        is_mcv_gone = self._has_mcv_out and not is_mcv_out
        return is_mcv_gone or self._is_spent or tick - self._active_at > HOLD_NO_SITE_TICKS

    def _open_when(self, is_active: bool, is_mcv_out: bool, tick: int) -> None:
        """Open the window when an expansion is under way and has not spent its window."""
        self._is_spent = self._is_spent and is_active
        if is_active and not self._is_spent:
            self.opened_at, self._has_mcv_out = tick, is_mcv_out


class UnservedPlaces:
    """The place the next refinery the economy orders would take, kept while what it is worked out from stays the
    same, and the `waiting_unserved` events written while there is none.

    Upkeep asks for it in every cycle a refinery could be due, and the places
    by every building take a few milliseconds in a large base. While an MCV
    expansion is under way (`McvExpansionWindow`), a refinery for a field one
    of ours serves already (`second`, `second_when_stuck`) waits: there is
    none to order, and one `held_for_mcv` event says so for the window. A
    refinery for an unserved field is never held.
    """

    def __init__(self) -> None:
        self._inputs: tuple[object, ...] | None = None
        self._choice: PlacementChoice | None = None
        # The tick the last `waiting_unserved` event was written at; None before one was.
        self._written_at: int | None = None
        self._window = McvExpansionWindow()
        # The refinery held for the MCV in the last look, and the opening tick of the window it was written for.
        self._held: PlacementChoice | None = None
        self._held_written_for: int | None = None

    def choice(self, world: World, ctx: MatchContext) -> PlacementChoice | None:
        """Where the next refinery would go now (`placed`); None while it is held for an MCV expansion: a refinery for
        a field one of ours serves already, while the expansion window is open."""
        is_expanding = self._window.is_open(world, ctx)
        choice = self.placed(world, ctx)
        is_held = is_expanding and choice is not None and choice.field.rule != FIELD_UNSERVED
        self._held = choice if is_held else None
        return None if is_held else choice

    def placed(self, world: World, ctx: MatchContext) -> PlacementChoice | None:
        """Where the next refinery would go now (`choose`), the places against the field reserved for the MCV's site
        kept off; None when it has nowhere to go."""
        reserved = ctx.reserved_field
        inputs = (
            ctx.terrain,
            ctx.tuning.placement,
            ctx.tuning.expansion.mcv_site_min_ore_cells,
            world.map_bounds,
            tuple(sorted((building.type, building.x, building.y) for building in world.buildings.values())),
            tuple(sorted((record.type, record.x, record.y) for record in world.known_enemy_defenses)),
            tuple(field.center for field in ctx.asked_refinery_fields),
            tuple(ctx.asked_refinery_cells),
            None if reserved is None else reserved.center,
            tuple(sorted(field.center for field in ctx.dry_fields)),
        )
        if inputs != self._inputs:
            reach = ctx.tuning.placement.refinery_ore_cells
            kept_off = () if reserved is None else placement.reach_boxes(reserved, reach)
            self._inputs = inputs
            self._choice = choose(world, ctx, (), kept_off, ctx.asked_refinery_fields, ctx.asked_refinery_cells)
        return self._choice

    def note_waiting(self, world: World) -> None:
        """Write a `refinery_field` event of the reason `held_for_mcv` for a refinery held in the last look, once for
        each expansion window; else one of the reason `waiting_unserved`, unless one was written within
        `WAITING_EVERY_TICKS`."""
        if self._held is not None:
            self._note_held(world)
            return
        if self._written_at is not None and world.tick - self._written_at < WAITING_EVERY_TICKS:
            return
        self._written_at = world.tick
        data = {
            "tick": world.tick,
            "cell": None,
            "rule": None,
            "field_center": None,
            "field_cells": None,
            "served_by": None,
            "ore_distance_cells": None,
            "reason": WAITING_UNSERVED_REASON,
        }
        records.log(REFINERY_FIELD_EVENT, data)


    def _note_held(self, world: World) -> None:
        """Write the `held_for_mcv` event of the refinery held, unless this window has one."""
        if self._held_written_for == self._window.opened_at:
            return
        self._held_written_for = self._window.opened_at
        held, field = self._held, self._held.field
        data = {
            "tick": world.tick,
            "cell": list(held.cell),
            "rule": field.rule,
            "field_center": list(field.patch.center),
            "field_cells": field.patch.size,
            "served_by": field.served_by,
            "ore_distance_cells": placement.ore_distance(placement.box_at(REFINERY, held.cell), field.patch),
            "reason": HELD_FOR_MCV_REASON,
        }
        records.log(REFINERY_FIELD_EVENT, data)


def _unserved_place(
    counts: Counter[OrePatch], places: placement.RefineryPlaces, world: World, ctx: MatchContext
) -> PlacementChoice | None:
    """The place against the first unserved field in turn (`_in_turn`), of at least `mcv_site_min_ore_cells` of
    [expansion] ore cells and not dry, that one of the `places` reaches and serves (`field_served_from`); None without
    one. A place that reaches a field but serves a nearer one would leave the field unserved and call for yet another
    refinery."""
    least = ctx.tuning.expansion.mcv_site_min_ore_cells
    unserved = [
        field
        for field in ctx.terrain.ore_patches
        if not counts[field] and field.size >= least and field not in ctx.dry_fields
    ]
    reach = ctx.tuning.placement.refinery_serves_cells
    for field in _in_turn(unserved, world):
        choice = places.against(field, FIELD_UNSERVED, keep=_serving(field, ctx.terrain, reach))
        if choice is not None:
            return choice
    return None


def _serving(field: OrePatch, terrain: Terrain, reach: int) -> Callable[[Box], bool]:
    """Whether a refinery covering a box would serve the field (`field_served_from`)."""
    return lambda box: field_served_from(box, terrain, reach) == field


def _in_turn(fields: list[OrePatch], world: World) -> list[OrePatch]:
    """The fields in the turn a refinery tries them: those whose nearest own yard is an expansion yard first, then the
    rest, each nearest its nearest own yard first."""
    yards = [(building.x, building.y) for building in world.buildings.values() if building.type == CONSTRUCTION_YARD]
    if not yards:
        return sorted(fields, key=lambda field: _distance(field.center, [world.base_center]))
    first = yards[0]

    def turn(field: OrePatch) -> tuple[bool, float]:
        nearest = min(yards, key=lambda yard: cells.distance(yard, field.center))
        return nearest == first, cells.distance(nearest, field.center)

    return sorted(fields, key=turn)


def _second(
    fields: list[OrePatch],
    places: placement.RefineryPlaces,
    world: World,
    ctx: MatchContext,
    asked_cells: Iterable[Cell],
    rule: str = FIELD_SECOND,
) -> PlacementChoice | None:
    """The place of a second refinery, of the `rule` given, against the first of the fields, nearest an own yard first,
    that has one whose middle lies `refinery_second_spacing_cells` or more from every other refinery of ours; None
    without one."""
    spacing = ctx.tuning.placement.refinery_second_spacing_cells
    others = _other_middles(world, asked_cells)
    for field in _in_turn(fields, world):
        choice = places.against(field, rule, keep=lambda box: _is_spaced(box, others, spacing))
        if choice is not None:
            return choice
    return None


def _is_spaced(box: Box, middles: list[tuple[float, float]], spacing: int) -> bool:
    """Whether the middle of a box lies `spacing` cells or more from each of the middles."""
    x, y = box.center
    return all(math.hypot(x - other_x, y - other_y) >= spacing for other_x, other_y in middles)


def _other_middles(world: World, asked_cells: Iterable[Cell]) -> list[tuple[float, float]]:
    """The middles of the own refineries that stand (`_refinery_boxes`), and those of the refineries asked onto a place
    lately."""
    return [*(box.center for box in _refinery_boxes(world)), *((float(x), float(y)) for x, y in asked_cells)]


def _refinery_boxes(world: World) -> list[Box]:
    """The footprints of the own refineries that stand (`footprint_cell`)."""
    return [
        placement.box_at(REFINERY, footprint_cell(building))
        for building in world.buildings.values()
        if building.type == REFINERY
    ]


def footprint_cell(refinery: Building) -> Cell:
    """The top-left cell of an own refinery's footprint: its reported cell less `REPORTED_OFFSET`."""
    return placement.footprint_corner(refinery.type, (refinery.x, refinery.y))


def _defended_boxes(world: World, ctx: MatchContext) -> list[Box]:
    """A box of one cell on each cell within a known enemy ground defense's range plus
    `refinery_enemy_defense_margin_cells`: kept off as places the engine refused are, they leave a refinery no place
    whose footprint lies that near."""
    margin, unknown = ctx.tuning.placement.refinery_enemy_defense_margin_cells, ctx.tuning.coverage.unknown_range_cells
    boxes = []
    for defense in world.known_enemy_defenses:
        if is_air_only(defense.type, world.items):
            continue
        facts = world.items.get(defense.type)
        reach = margin + (unknown if facts is None or facts.range is None else facts.range)
        span = math.floor(reach)
        boxes += [
            Box(defense.x + dx, defense.y + dy, 1, 1)
            for dy in range(-span, span + 1)
            for dx in range(-span, span + 1)
            if cells.is_within((defense.x + dx, defense.y + dy), (defense.x, defense.y), reach)
        ]
    return boxes


def _distance(cell: Cell, others: list[Cell | None]) -> float:
    """How far a cell lies from the nearest of the others that are known; endlessly far from none."""
    return min((cells.distance(cell, other) for other in others if other is not None), default=math.inf)
