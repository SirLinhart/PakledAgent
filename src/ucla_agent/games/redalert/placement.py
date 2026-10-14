"""Where a finished building is asked to be placed.

The platform places a building at the cell it is asked for when that cell can
take it, and near the construction yard when it cannot. Left to that, a base
grows without any order to it: a refinery may stand far from the ore, a
building may wall in the war factory's exit. So the agent always asks for a
cell, and this module chooses it.

A cell is a candidate when a building of the item's size, with its top-left
corner there, lies at the right distance from the construction yard, inside
the playable map, on ground that can be built on, clear of every building
that stands, and as close to one of them as the engine demands. Two kinds of
cells are kept free on purpose: those in front of a war factory's exit, and
those on the straight line from a refinery to its ore, which is the way its
harvesters drive.

Among the candidates a refinery takes the one nearest an ore patch that no
refinery serves yet, a defense the one nearest a point in front of the base
on the enemy's side, and any other building the one nearest the construction
yard, which keeps the base compact. An AA defense, once the enemy has shown
air power, stands by the refinery nearest the enemy's start instead: enemy
aircraft go for the harvesters and the refinery they unload at, and that one
is the first they reach.

A refinery aimed at the nearest unserved patch often lands far short of it,
since the engine lets a building stand only close to one that stands, and its
harvesters then drive the difference on every trip. With the tuning file's
choice of the field by reach switched on, a refinery serves the nearest
unserved field it can stand against now, within `refinery_ore_cells` of one
of its ore cells; else it shares a near field that is large enough and not
yet served by as many refineries as a field takes; else it aims at the
nearest unserved field all the same. Every field it may serve lies within
`ore_reach_cells` of the construction yard, and it stands against the field
whenever a candidate can.

A refinery can stand against a field only once the base has grown close to
it. With the tuning file's creep toward ore switched on, while a refinery is
due and no candidate of its reaches the field it is to serve, every other
building but a defense is asked onto the candidate nearest that field's ore,
so that the base walks toward it with each one; once a candidate reaches the
field, the refinery stands against it and the base is compact again. Which
refinery is due is for the caller to say. Creep only chooses among the
candidates: the war factory's exit and the harvesters' lanes stay free. And
it only walks toward a field that some free place in the ring could stand a
refinery against once a building stood near it: when every such place is
built on or out of the ring, a building placed toward the ore would only take
the room the refinery is left with.

A base also grows from field to field. Given a creep target, the next ore
field along the lane toward the enemy (see `creep`), every building but a
defense is asked onto the candidate nearest the target's ore cell nearest the
construction yard, and a refinery against the target's ore once a candidate
reaches it. A base that walks toward a field soon leaves the ring around its
yard behind, so the candidates are then every free place next to a building
that stands as well as those of the ring. A building that is not a refinery
keeps off the places from which a refinery would reach the target's ore while
it has another: those are what the field's refineries will need.

A base can walk from its very first building on as well. With the tuning
file's creep from the opening switched on, the far field is the ore field
whose center is nearest the construction yard, in any direction, that no
refinery stands against nor could stand against from any free place now,
neither in the ring nor next to a building that stands: its nearest ore cell
within `creep_far_max_cells` of the footprint of the nearest own building but
a defense (the creep cap), more than `creep_enemy_clear_cells` from the
enemy's start when that is known, and with no remembered enemy defense
within `creep_defense_clear_cells` of it. Once a refinery can stand against
it, the next such field is the far field. While there is one, every building but a
defense or a refinery is asked onto the place, in the ring or next to a
building that stands, farthest along the straight line from the yard to the
far field's ore cell nearest it, of those whose middle lies within
`creep_line_cells` of the line; with none that close, onto the compact place
nearest the yard, since a place merely nearest that ore cell would string the
base out toward a far field. This comes before the creep toward a refinery's field and the
creep target, from the opening's first building on. The places from which a
refinery would stand against the home field, the field nearest the yard that
a refinery stands or can stand against now, are left to the refineries while
there is another. With the tuning file's continuous creep switched on as well,
the base walks on field after field, the creep cap measured from wherever it
has grown to, and a field placement skipped (see `refused`) is never the far
field. With the tuning
file's remote yard growth switched on as well, a second construction yard
that stands farther than `ring_max_cells` from the first has a far field of
its own by the same rules, from its own middle: never the main base's far
field nor a field placement skipped. The buildings that walk a line then take
the two lines in turn, which the caller says; a line with no place yields to
the other. The places next to a building that stands are those next to every
building, the main base's too, so that a place by the main base that lies far
along the second yard's line was taken for it; with the tuning file's second
line local switched on, that line takes only the places of the second yard's
ring and those next to a building within `second_line_radius_cells` of its
middle.

A line need not be straight. With the tuning file's walked routes switched
on, a rifleman walks to a line's far field first (see `route_walk`), and once
it has arrived the line is the way it walked: the straight piece from the
line's start (the middle of its yard) to the point of the route nearest that
start, then the route from that point on. A building then takes the place
farthest along that way, of those whose middle lies within
`creep_line_cells` of it, the distance along the way being that of the point
of the way nearest the place's middle; with none that close, the compact
place by the yard, as for a straight line. Where a cliff or water
stands between the base and the field, the base so grows round it, the way
the game's own path finding found.

A field the base has reached is claimed by a refinery when upkeep says so
(see `creep`): the refinery stands against it, at the place nearest its ore
cell nearest the yard of those that reach it, in the ring of the yard nearest
the field or next to any building that stands.

A field is taken once an own refinery stands within `refinery_ore_cells` of
it, or a refinery was lately asked onto a place against it or is on order for
it (see `upkeep`). With the tuning file's refinery spread switched on,
refineries go one to a field: a refinery stands against the untaken field
nearest a yard that it can stand against now, placed as a claim is, and a
field gets a second only while there is none, the nearest of those that fewer
than `max_refineries_per_field` refineries serve or are asked against. A field
reserved for the MCV's site (see `expansion`) is kept from refineries by the
caller, which keeps off the places from which one would stand against it
(`reach_boxes`) as it keeps off the places the engine refused.

The engine puts a building elsewhere, without a word, when the cell it is
asked for cannot take it, whatever the map picture shows there. The places
it so refused (see `refused`) are kept off for every building, as ground
nothing can be built on.

The sizes of the buildings are those of the game's rules. The platform does
not send them, and an item this module does not know is given the size of the
largest building, so that it is never asked into too small a gap.
"""

from __future__ import annotations

import dataclasses
import functools
import itertools
import math
from collections import Counter
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Collection, Container, Iterable, Mapping, Sequence

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.anti_air import ANTI_AIR_DEFENSES
from ucla_agent.games.redalert.cells import Bounds
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD, REFINERY, WAR_FACTORY
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import PlacementTuning
from ucla_agent.games.redalert.world import DEFENSE_TYPES, Building, Cell, World

# (width, height) in cells from the top-left cell, the row a building keeps clear in front of it included.
FOOTPRINTS: dict[str, tuple[int, int]] = {
    "fact": (3, 4),
    "powr": (2, 3),
    "apwr": (3, 3),
    "tent": (2, 3),
    "barr": (2, 3),
    "proc": (3, 4),
    "weap": (3, 3),
    "fix": (3, 3),
    "dome": (2, 3),
    "atek": (2, 3),
    "stek": (3, 3),
    "hpad": (2, 3),
    "afld": (3, 2),
    "sam": (2, 1),
    "silo": (1, 1),
    "kenn": (1, 1),
}
LARGEST_FOOTPRINT = (3, 4)
DEFENSE_FOOTPRINT = (1, 1)
# Where the state reports a standing building, right and down of the top-left cell of its footprint: the middle cell of
# the cells the engine gives it, (width // 2, height // 2) of its body without the row it keeps clear in front of it.
# So one right and one down for every building two cells or more across and tall, as the place orders of the watched
# games show for each type placed (docs/COMPONENTS.md), and one right for the SAM site's single row.
REPORTED_OFFSETS: dict[str, tuple[int, int]] = {
    "fact": (1, 1),
    "powr": (1, 1),
    "apwr": (1, 1),
    "tent": (1, 1),
    "barr": (1, 1),
    "proc": (1, 1),
    "weap": (1, 1),
    "fix": (1, 1),
    "dome": (1, 1),
    "atek": (1, 1),
    "stek": (1, 1),
    "hpad": (1, 1),
    "afld": (1, 1),
    "sam": (1, 0),
    "silo": (0, 0),
    "kenn": (0, 0),
}
# The offset of a type the table does not list, by whether its footprint is two cells or more across.
_WIDE_REPORTED_OFFSET = (1, 1)
_NARROW_REPORTED_OFFSET = (0, 0)
# The AA defenses, which stand by the refinery the enemy's aircraft come to first.
ANTI_AIR_DEFENSE_TYPES = frozenset(ANTI_AIR_DEFENSES.values())
# The rules by which a refinery's ore field is chosen by reach, in the order they are tried, as the match record
# names them: an unserved field a refinery can stand against, a large near field it can share, the nearest unserved
# field, and with every field in reach served, the nearest one that still takes another refinery.
FIELD_UNSERVED_REACHED = 1
FIELD_SHARED_REACHED = 2
FIELD_UNSERVED_NEAREST = 3
FIELD_NEAREST_UNDER_CAP = 4
# The rule a refinery's field is given by when the refinery claims a field the base has reached, when refineries go
# one to a field before any field gets a second, and when the economy grows past four refineries.
FIELD_CLAIM = "claim"
FIELD_SPREAD = "spread"
FIELD_GROWTH = "growth"
# How many fields, each at one reach, keep the cells a refinery has to cover part of to reach them worked out: more
# than the fields of any one map.
_REACH_ZONES_KEPT = 64
# The names of the main base's line toward its far field and of the second yard's line toward its own.
MAIN_LINE = "main"
SECOND_LINE = "second"
# The places along the line toward the far field are compared to a millionth of a cell, so that two places as far
# along it are not told apart by a rounding error.
_LINE_DECIMALS = 6
# How far beyond the buildings it grows from, and the cells a building next to one of them takes, a place of the second
# yard's line may lie with `second_line_local` on.
_SECOND_LINE_SLACK_CELLS = 3
# No walked route for any field: the lines toward the far fields are straight.
_NO_ROUTES: Mapping[OrePatch, tuple[Cell, ...]] = MappingProxyType({})


@dataclass(frozen=True)
class Box:
    """The cells a building covers: a rectangle from its top-left cell."""

    left: int
    top: int
    width: int
    height: int

    @property
    def center(self) -> tuple[float, float]:
        """The middle of the rectangle, in fractions of cells."""
        return self.left + (self.width - 1) / 2, self.top + (self.height - 1) / 2

    def grown(self, margin: int) -> Box:
        """The rectangle with `margin` cells added on every side."""
        return Box(self.left - margin, self.top - margin, self.width + 2 * margin, self.height + 2 * margin)

    def overlaps(self, other: Box) -> bool:
        """Whether the two rectangles share a cell."""
        return (
            self.left < other.left + other.width
            and other.left < self.left + self.width
            and self.top < other.top + other.height
            and other.top < self.top + self.height
        )

    def is_inside(self, bounds: Bounds) -> bool:
        """Whether every cell of the rectangle lies in the playable part of a map."""
        left, top, width, height = bounds
        return (
            left <= self.left
            and top <= self.top
            and self.left + self.width <= left + width
            and self.top + self.height <= top + height
        )

    def cells(self) -> list[Cell]:
        """The cells of the rectangle, row by row."""
        return [
            (x, y) for y in range(self.top, self.top + self.height) for x in range(self.left, self.left + self.width)
        ]

    def holds_any(self, some_cells: Container[Cell]) -> bool:
        """Whether one of the rectangle's cells is among the given."""
        return any(cell in some_cells for cell in self.cells())


@dataclass(frozen=True)
class FieldChoice:
    """The ore field a refinery is to serve, chosen by reach, and why."""

    patch: OrePatch
    # One of the numbered FIELD_ rules, or FIELD_CLAIM.
    rule: int | str
    # How many refineries served the field before this one.
    served_by: int


@dataclass(frozen=True)
class RefineryReach:
    """How near the ore of the field a refinery placed now would serve it can be asked to stand."""

    field: OrePatch
    # The fewest cells, counted as a king moves, between a refinery candidate's footprint and the field's ore; None
    # without a candidate.
    distance_cells: int | None
    # Whether a candidate stands within `refinery_ore_cells` of the field's ore by walking (`reaches`).
    reaches: bool


@dataclass(frozen=True)
class CreepTarget:
    """The ore field the base walks to next along the lane toward the enemy, and its place in that lane."""

    patch: OrePatch
    # The field's place among the fields of the lane that may be targets, the nearest the base along the lane 1.
    lane_rank: int


@dataclass(frozen=True)
class FarField:
    """The ore field the base walks toward in a straight line from the opening on, where the line starts, and the
    home field, whose places are kept for its refineries. See the module's note."""

    patch: OrePatch
    # The middle cell of the first construction yard, else the base's center: what the fields are nearest to, and
    # where the line toward the far field starts.
    origin: Cell
    # None when a refinery stands or can stand against no field now.
    home: OrePatch | None


@dataclass(frozen=True)
class Creep:
    """How a building was placed toward an ore field: that of a refinery that is due and cannot stand against it
    yet, the creep target along the lane, or the far field along the line from the yard."""

    field_center: Cell
    # The field's ore cell nearest the construction yard, which the building is placed nearest.
    ore_cell: Cell
    # How far the building's middle lies from that ore cell at the candidate nearest the yard, where it would stand
    # without creep, and at the candidate it is asked onto.
    distance_before: float
    distance_after: float
    # The creep target's place in the lane, for a building placed toward it; None for the creep toward a due
    # refinery's field and along the line toward the far field.
    lane_rank: int | None = None
    # How far the building's middle lies from the line toward the far field, for a building placed along it; None for
    # any other creep.
    line_distance: float | None = None
    # Which yard's line a building placed along one walked, MAIN_LINE or SECOND_LINE, while a second yard has a line of
    # its own; None for any other creep.
    yard: str | None = None


@dataclass(frozen=True)
class PlacementChoice:
    """The cell a finished building is asked onto, and what chose it beyond the building's own preference."""

    # None when there is no candidate: the platform then chooses a cell near the construction yard itself.
    cell: Cell | None
    # For a refinery whose field is chosen by reach: that field and the rule that chose it.
    field: FieldChoice | None = None
    # For a building placed toward the ore field of a refinery that is due, the creep target or the far field.
    creep: Creep | None = None


def footprint(item: str) -> tuple[int, int]:
    """The (width, height) of a building of a type; the largest there is for a type that is not known."""
    if item in FOOTPRINTS:
        return FOOTPRINTS[item]
    return DEFENSE_FOOTPRINT if item in DEFENSE_TYPES else LARGEST_FOOTPRINT


def box_at(item: str, cell: Cell) -> Box:
    """The cells a building of a type covers with its top-left corner on a cell."""
    return Box(cell[0], cell[1], *footprint(item))


def reported_offset(item: str) -> tuple[int, int]:
    """How far right and down of the top-left cell of its footprint the state reports a standing building of a type:
    as `REPORTED_OFFSETS` gives it; for a type it does not list, one right and one down when the type's footprint is two
    cells or more across, else none."""
    if item in REPORTED_OFFSETS:
        return REPORTED_OFFSETS[item]
    return _WIDE_REPORTED_OFFSET if footprint(item)[0] >= 2 else _NARROW_REPORTED_OFFSET


def footprint_corner(item: str, reported: Cell) -> Cell:
    """The top-left cell of the footprint of a standing building of a type the state reports at a cell: that cell less
    the type's `reported_offset`. A building asked for keeps the cell it was asked onto as its top-left cell."""
    offset_x, offset_y = reported_offset(item)
    return reported[0] - offset_x, reported[1] - offset_y


def standing_box(building: Building) -> Box:
    """The cells a standing building covers: its type's footprint from `footprint_corner` of its reported cell."""
    return box_at(building.type, footprint_corner(building.type, (building.x, building.y)))


def excluded_cells(world: World, terrain: Terrain, tuning: PlacementTuning) -> set[Cell]:
    """The cells kept free on purpose: in front of each war factory's exit, and from each refinery to its ore."""
    excluded: set[Cell] = set()
    for building in world.buildings.values():
        if building.type == WAR_FACTORY:
            excluded.update(_exit_cells(building, tuning.war_factory_exit_cells))
        elif building.type == REFINERY:
            excluded.update(_ore_lane(building, terrain))
    return excluded


@dataclass(frozen=True)
class _Ground:
    """What decides whether a building can be asked onto a place, worked out once for all the places tried."""

    bounds: Bounds
    standing: tuple[Box, ...]
    # The buildings that stand or were asked for, each with the gap around it.
    kept_clear: tuple[Box, ...]
    # How close to a standing building the engine wants a new one.
    reach: int
    unusable: frozenset[Cell]
    # How many rows below the building have to be free as well: those in front of a war factory's exit.
    exit_depth: int

    def takes(self, box: Box) -> bool:
        """Whether a building can be asked to cover the cells of a box."""
        if not self.is_open(box):
            return False
        near = box.grown(self.reach)
        return any(near.overlaps(other) for other in self.standing)

    def is_open(self, box: Box) -> bool:
        """Whether a building could cover the cells of a box if one stood close enough to it."""
        needed = [box, _exit_box(box, self.exit_depth)] if self.exit_depth else [box]
        return box.is_inside(self.bounds) and not any(self._is_taken(part) for part in needed)

    def _is_taken(self, box: Box) -> bool:
        return any(box.overlaps(other) for other in self.kept_clear) or box.holds_any(self.unusable)


def candidate_cells(
    item: str,
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    promised: Iterable[Box] = (),
    anchor: Cell | None = None,
) -> list[Cell]:
    """The cells a finished building of a type may be asked to be placed at, nearest the construction yard first.

    `promised` are the cells of buildings that were asked for and do not show
    yet: a candidate keeps clear of them as of the buildings that stand. The
    rings are laid around `anchor` when it is given, else around the first
    construction yard.
    """
    anchor = anchor or _anchor(world)
    if anchor is None or world.map_bounds is None:
        return []
    ground = _ground(item, world, terrain, tuning, promised)
    return [cell for cell in _ring_cells(anchor, tuning) if ground.takes(box_at(item, cell))]


def _ground(item: str, world: World, terrain: Terrain, tuning: PlacementTuning, promised: Iterable[Box]) -> _Ground:
    """What decides where a building of a type can be asked onto, on a map whose bounds are known."""
    standing = tuple(standing_box(building) for building in world.buildings.values())
    return _Ground(
        bounds=world.map_bounds,
        standing=standing,
        kept_clear=tuple(box.grown(tuning.gap_cells) for box in (*standing, *promised)),
        reach=tuning.defense_adjacent_cells if item in DEFENSE_TYPES else tuning.adjacent_cells,
        unusable=frozenset(excluded_cells(world, terrain, tuning)) | terrain.blocked,
        exit_depth=tuning.war_factory_exit_cells if item == WAR_FACTORY else 0,
    )


def _ring_cells(anchor: Cell, tuning: PlacementTuning) -> list[Cell]:
    """The cells at the distances from the construction yard a building may be placed at, nearest first."""
    return [(anchor[0] + dx, anchor[1] + dy) for dx, dy in _ring_offsets(tuning.ring_min_cells, tuning.ring_max_cells)]


def preferred_cell(
    item: str,
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    promised: Iterable[Box] = (),
    aa_refinery_cells: int | None = None,
) -> Cell | None:
    """The cell a finished building of a type is asked to be placed at; None when there is no candidate.

    With `aa_refinery_cells` given, an AA defense is kept within that many cells of the refinery nearest the enemy's
    start; without, it is placed as any other defense.
    """
    return choose_placement(item, world, terrain, tuning, promised, aa_refinery_cells).cell


def choose_placement(
    item: str,
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    promised: Iterable[Box] = (),
    aa_refinery_cells: int | None = None,
    toward_ore: bool = False,
    target: CreepTarget | None = None,
    field_cap: bool = False,
    refused: Iterable[Box] = (),
    skipped: Collection[OrePatch] = (),
    second_line: bool = False,
    routes: Mapping[OrePatch, tuple[Cell, ...]] = _NO_ROUTES,
) -> PlacementChoice:
    """The cell a finished building of a type is asked to be placed at, as `preferred_cell` gives it, and what chose
    it: for a refinery whose field is chosen by reach, the field and the rule.

    The cells of the `refused` places, where the engine put a building elsewhere than asked, are ground nothing can
    be built on for every building, in whatever this choice works out: the far field's reach included.

    With `toward_ore`, said while a refinery is due, a building that is neither a refinery nor a defense creeps toward
    that refinery's field until a refinery candidate reaches it. A refinery stands against its field when a candidate
    reaches it, as long as either the choice by reach or the creep toward ore is switched on.

    With a creep `target`, a building that is not a defense creeps toward the target instead, on the candidates of
    the ring and on those next to any building that stands; see `_toward_target`.

    With `field_cap`, a refinery takes only a candidate whose field is served by fewer than `max_refineries_per_field`
    refineries, while there is one.

    With `creep_from_opening` on and a far field, a building that is neither a refinery nor a defense walks the line
    toward it before any of these; see `_along_line`. The `skipped` fields are those placement has skipped, which the
    far field passes over with `creep_continuous` on. With `second_line`, a building walks the second yard's line
    rather than the main base's while there are two. A line whose far field has a route walked to it in `routes`
    follows that route rather than the straight line (see `walked_line`).
    """
    promised = tuple(promised)
    terrain = with_refused(terrain, refused)
    along = _along_line(item, world, terrain, tuning, promised, skipped, second_line, routes)
    if along is not None:
        return along
    anchor = _anchor_for(item, world, terrain, tuning, target)
    if anchor is None:
        return PlacementChoice(None)
    candidates = candidate_cells(item, world, terrain, tuning, promised, anchor)
    cap = _refinery_cap(item, tuning, field_cap)
    candidates = _under_cap(candidates, world, terrain, cap)
    toward_target = _toward_target(item, candidates, anchor, world, terrain, tuning, promised, target, cap)
    if toward_target is not None:
        return toward_target
    if not candidates:
        return PlacementChoice(None)
    if item in ANTI_AIR_DEFENSE_TYPES and aa_refinery_cells is not None:
        return PlacementChoice(_anti_air_cell(item, candidates, anchor, world, terrain, tuning, aa_refinery_cells))
    if _stands_against_field(item, tuning):
        return _refinery_against_field(candidates, anchor, world, terrain, tuning)
    crept = _creep(item, candidates, anchor, world, terrain, tuning, promised) if toward_ore else None
    return crept or PlacementChoice(_cell_toward_goal(item, candidates, anchor, world, terrain, tuning))


def with_refused(terrain: Terrain, refused: Iterable[Box]) -> Terrain:
    """The terrain with the cells of the places the engine refused as ground nothing can be built on; the terrain
    itself when they all are already."""
    refused_cells = frozenset(cell for box in refused for cell in box.cells())
    if refused_cells <= terrain.blocked:
        return terrain
    return Terrain(
        terrain.ore_patches,
        terrain.blocked | refused_cells,
        terrain.unknown_cells,
        terrain.cell_count,
        terrain.impassable,
    )


def refinery_reach(world: World, terrain: Terrain, tuning: PlacementTuning) -> RefineryReach | None:
    """How near the ore of the field it would serve a refinery could be asked to stand now, the field being chosen
    as for a refinery placed now; None without a base or without a field to serve."""
    anchor = _anchor(world)
    if anchor is None:
        return None
    boxes = [box_at(REFINERY, cell) for cell in candidate_cells(REFINERY, world, terrain, tuning)]
    patch = _target_field(anchor, boxes, world, terrain, tuning)
    if patch is None:
        return None
    distance = _nearest_ore_distance(boxes, patch)
    return RefineryReach(patch, distance, _is_reached(patch, boxes, tuning.refinery_ore_cells, terrain))


def far_field(
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    promised: Iterable[Box] = (),
    skipped: Collection[OrePatch] = (),
) -> FarField | None:
    """The ore field the base walks toward from the opening on, with the home field; None without a base, a map or
    a field that may be the far field. See the module's note.

    A refinery stands or could stand against a field now when a refinery that stands, or one at a place in the ring
    or next to a building that stands, clear of the `promised` buildings, lies within `refinery_ore_cells` of the
    field's ore. With `creep_continuous` on, a field of `skipped` is never the far field.
    """
    return far_fields(world, terrain, tuning, promised, skipped)[0]


def far_fields(
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    promised: Iterable[Box] = (),
    skipped: Collection[OrePatch] = (),
) -> tuple[FarField | None, FarField | None]:
    """The far field of the main base, as `far_field` names it, and that of the second yard's line: from the middle of
    the `second_yard`, the field nearest it by the same rules that is neither the main base's far field nor one of
    `skipped`, with the field nearest it a refinery stands or could stand against as its home field. None for either
    without one; for the second always without a second yard."""
    origin = _far_origin(world)
    if origin is None or world.map_bounds is None:
        return None, None
    reached = _reached_fields(list(terrain.ore_patches), world, terrain, tuning, tuple(promised))
    yard = second_yard(world, tuning)
    main_base, second_base = _line_bases(world, yard)
    passed_over = skipped if tuning.creep_continuous else ()
    main = _far_from(origin, reached, main_base, world, terrain, tuning, passed_over)
    if yard is None:
        return main, None
    passed_over = [*skipped, *([] if main is None else [main.patch])]
    yard_middle = _middle_cell(standing_box(yard))
    return main, _far_from(yard_middle, reached, second_base, world, terrain, tuning, passed_over)


def _line_bases(world: World, yard: Building | None) -> tuple[list[Box], list[Box]]:
    """The footprints of the own buildings but defenses each line's creep cap is measured from: all of them for the
    main base's line while there is no second yard; else those nearer the first yard for the main base's line and those
    nearer the `yard` for its own, so that neither line reaches for a field by the other yard."""
    standing = [building for building in world.buildings.values() if building.type not in DEFENSE_TYPES]
    first = _first_yard(world)
    if yard is None or first is None:
        return [standing_box(building) for building in standing], []

    def is_nearer_second(building: Building) -> bool:
        cell = (building.x, building.y)
        return cells.distance(cell, (yard.x, yard.y)) < cells.distance(cell, (first.x, first.y))

    main = [standing_box(building) for building in standing if not is_nearer_second(building)]
    return main, [standing_box(building) for building in standing if is_nearer_second(building)]


def second_yard(world: World, tuning: PlacementTuning) -> Building | None:
    """The construction yard a second line of growth starts from: with `creep_from_opening` and `remote_yard_growth`
    on, the first own yard that stands more than `ring_max_cells` from the first; None with either off and without
    one."""
    first = _first_yard(world)
    if not (tuning.creep_from_opening and tuning.remote_yard_growth) or first is None:
        return None
    return next(
        (
            building
            for building in world.buildings.values()
            if building.type == CONSTRUCTION_YARD
            and not cells.is_within((building.x, building.y), (first.x, first.y), tuning.ring_max_cells)
        ),
        None,
    )


def _far_from(
    origin: Cell,
    reached: set[OrePatch],
    base: list[Box],
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    passed_over: Collection[OrePatch],
) -> FarField | None:
    """The far field from a point: the field nearest it that no refinery can stand against now and that may be the far
    field, its creep cap measured from the `base` (`_may_be_far_field`), with the `reached` field nearest it as the
    home field; None without such a field."""
    nearest_first = sorted(terrain.ore_patches, key=lambda patch: cells.distance(patch.center, origin))
    home = next((patch for patch in nearest_first if patch in reached), None)
    target = next(
        (
            patch
            for patch in nearest_first
            if patch not in reached and _may_be_far_field(patch, origin, base, world, tuning, passed_over)
        ),
        None,
    )
    return None if target is None else FarField(target, origin, home)


def is_defended(patch: OrePatch, world: World, reach: int) -> bool:
    """Whether a remembered enemy defense stands within `reach` cells of one of a field's cells."""
    return any(
        cells.is_within(patch.nearest_cell((record.x, record.y)), (record.x, record.y), reach)
        for record in world.known_enemy_defenses
    )


def reaches(box: Box, patch: OrePatch, reach: int, terrain: Terrain | None = None) -> bool:
    """Whether a building covering a box stands within `reach` cells of an ore cell of a field, counted as a king
    moves: from the nearest cell of the box to the nearest ore cell. With the `terrain`, by walking (see
    `Terrain.walk_distances`): a box 2 cells from the ore across a cliff is no nearer than the walk round it."""
    return box.holds_any(_reach_zone(patch, reach) if terrain is None else terrain.walk_zone(patch, reach))


def fields_against(box: Box, terrain: Terrain, reach: int) -> tuple[OrePatch, ...]:
    """The ore fields a building covering a box stands against: those it stands within `reach` cells of by walking
    (`reaches`)."""
    return tuple(patch for patch in terrain.ore_patches if reaches(box, patch, reach, terrain))


def reach_boxes(patch: OrePatch, reach: int) -> tuple[Box, ...]:
    """A box of one cell on each cell within `reach` of a field's ore: kept off as places the engine refused are, they
    leave a refinery no place from which it stands against the field (`reaches`)."""
    return tuple(Box(x, y, 1, 1) for x, y in sorted(_reach_zone(patch, reach)))


def taken_fields(
    world: World, terrain: Terrain, tuning: PlacementTuning, asked: Iterable[OrePatch]
) -> frozenset[OrePatch]:
    """The fields that are taken: an own refinery stands within `refinery_ore_cells` of their ore, or they are among
    the `asked` fields, those a refinery was asked onto a place against or is on order for lately."""
    standing = _covered(_standing_refineries(world))
    zones = {patch: terrain.walk_zone(patch, tuning.refinery_ore_cells) for patch in terrain.ore_patches}
    stood_against = (patch for patch, zone in zones.items() if not standing.isdisjoint(zone))
    return frozenset((*stood_against, *asked))


def ore_distance(box: Box, patch: OrePatch) -> int:
    """How many cells, counted as a king moves, lie from the nearest cell of a box to the nearest ore cell of a field:
    0 when the box covers ore, 1 when it touches it."""
    right, bottom = box.left + box.width - 1, box.top + box.height - 1
    return min(max(box.left - x, x - right, box.top - y, y - bottom, 0) for x, y in patch.cells)


def _cell_toward_goal(
    item: str, candidates: list[Cell], anchor: Cell, world: World, terrain: Terrain, tuning: PlacementTuning
) -> Cell:
    """The candidate nearest the point a building of a type should stand near; the first when there is none.

    A defense keeps within `defense_radius_cells` of the construction yard while a candidate there is free.
    """
    goal = _goal(item, anchor, world, terrain, tuning)
    if goal is None:
        return candidates[0]
    if item in DEFENSE_TYPES:
        candidates = [
            cell for cell in candidates if cells.is_within(cell, anchor, tuning.defense_radius_cells)
        ] or candidates
    # The first of several equally near is taken, which is the one nearest the construction yard.
    return min(candidates, key=lambda cell: _distance_to(box_at(item, cell), goal))


def _anti_air_cell(
    item: str,
    candidates: list[Cell],
    anchor: Cell,
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    reach: int,
) -> Cell:
    """The cell of an AA defense: of the candidates within `reach` of the refinery nearest the enemy's start, the one
    nearest that refinery; the cell of any other defense when no candidate is that near or there is no such refinery.
    """
    refinery = _exposed_refinery(world)
    near = [] if refinery is None else [cell for cell in candidates if cells.is_within(cell, refinery, reach)]
    if not near:
        return _cell_toward_goal(item, candidates, anchor, world, terrain, tuning)
    return min(near, key=lambda cell: _distance_to(box_at(item, cell), refinery))


def _exposed_refinery(world: World) -> Cell | None:
    """The middle cell of the own refinery nearest the enemy's start; None without a refinery or a known start."""
    enemy_start = world.enemy_base
    middles = [
        _middle_cell(standing_box(building)) for building in world.buildings.values() if building.type == REFINERY
    ]
    if enemy_start is None or not middles:
        return None
    return min(middles, key=lambda middle: cells.distance(middle, enemy_start))


def _goal(item: str, anchor: Cell, world: World, terrain: Terrain, tuning: PlacementTuning) -> Cell | None:
    """The point a building of a type should stand as near to as it can; None when the construction yard is it."""
    if item == REFINERY:
        patch = _patch_to_serve(anchor, world, terrain, tuning.ore_reach_cells)
        return patch.center if patch is not None else None
    if item in DEFENSE_TYPES and world.enemy_base is not None:
        return cells.toward(anchor, world.enemy_base, tuning.defense_distance_cells)
    return None


def _patch_to_serve(anchor: Cell, world: World, terrain: Terrain, reach: int) -> OrePatch | None:
    """The ore patch a new refinery should serve: the nearest in reach that has none, else the nearest in reach."""
    in_reach = [patch for patch in terrain.ore_patches if cells.is_within(patch.center, anchor, reach)]
    served = [patch_of(refinery, terrain) for refinery in world.buildings.values() if refinery.type == REFINERY]
    unserved = [patch for patch in in_reach if patch not in served]
    return terrain.nearest_patch(anchor, unserved or in_reach)


def _stands_against_field(item: str, tuning: PlacementTuning) -> bool:
    """Whether a building is a refinery to be placed against its field whenever a candidate reaches it: so it is
    with the choice by reach or the creep toward ore switched on."""
    return item == REFINERY and (tuning.refinery_field_by_reach or tuning.creep_toward_ore)


def _refinery_against_field(
    candidates: list[Cell], anchor: Cell, world: World, terrain: Terrain, tuning: PlacementTuning
) -> PlacementChoice:
    """A refinery's cell, and its field when that is chosen by reach: against the field when a candidate reaches it,
    else as near it as the base allows; the candidate nearest the construction yard when there is no field."""
    boxes = [box_at(REFINERY, cell) for cell in candidates]
    if not tuning.refinery_field_by_reach:
        patch = _patch_to_serve(anchor, world, terrain, tuning.ore_reach_cells)
        cell = (
            candidates[0]
            if patch is None
            else _cell_against(patch, candidates, boxes, anchor, terrain, tuning, by_reach=False)
        )
        return PlacementChoice(cell)
    field = _field_by_reach(anchor, boxes, world, terrain, tuning)
    if field is None:
        return PlacementChoice(candidates[0])
    return PlacementChoice(_cell_against(field.patch, candidates, boxes, anchor, terrain, tuning, by_reach=True), field)


def _creep(
    item: str,
    candidates: list[Cell],
    anchor: Cell,
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    promised: tuple[Box, ...],
) -> PlacementChoice | None:
    """A building's cell while the base creeps toward a due refinery's field: the candidate whose middle is nearest
    the field's ore cell nearest the construction yard. None for a refinery or a defense, without a field, and once
    a refinery candidate reaches the field."""
    if item == REFINERY or item in DEFENSE_TYPES:
        return None
    refinery_cells = candidate_cells(REFINERY, world, terrain, tuning, promised, anchor)
    refinery_boxes = [box_at(REFINERY, cell) for cell in refinery_cells]
    patch = _target_field(anchor, refinery_boxes, world, terrain, tuning)
    if patch is None or _is_reached(patch, refinery_boxes, tuning.refinery_ore_cells, terrain):
        return None
    if not _has_room_against(patch, anchor, world, terrain, tuning, promised):
        return None
    ore = patch.nearest_cell(anchor)
    # The first of several equally near is taken, which is the one nearest the construction yard.
    cell = min(candidates, key=lambda candidate: _distance_to(box_at(item, candidate), ore))
    before, after = (_distance_to(box_at(item, place), ore) for place in (candidates[0], cell))
    return PlacementChoice(cell, creep=Creep(patch.center, ore, before, after))


def _toward_target(
    item: str,
    ring: list[Cell],
    anchor: Cell,
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    promised: tuple[Box, ...],
    target: CreepTarget | None,
    cap: int | None = None,
) -> PlacementChoice | None:
    """A building's cell while the base creeps toward the creep target: for a refinery, the candidate that reaches the
    target's ore nearest the reachable ore cell nearest the yard, None while no candidate reaches it; for any other
    building but a defense, the candidate whose middle is nearest the target's ore cell nearest the yard. None without
    a target and for a defense, which keeps its own goal.

    The candidates are those of the ring and every place next to a building that stands, so that the base can grow
    out of the ring toward the target. A building that is not a refinery keeps off the places from which a refinery
    would reach the target's ore while any other place is free: those are the refineries' to take. With a `cap`, a
    refinery keeps to the places whose field fewer refineries than the cap serve, while there are any.
    """
    if target is None or item in DEFENSE_TYPES:
        return None
    candidates = _under_cap(
        _creep_candidates(item, ring, anchor, world, terrain, tuning, promised), world, terrain, cap
    )
    boxes = [box_at(item, cell) for cell in candidates]
    reach = tuning.refinery_ore_cells
    reaching = [index for index, box in enumerate(boxes) if reaches(box, target.patch, reach, terrain)]
    if item == REFINERY:
        picked = _refinery_toward(target.patch, boxes, reaching, anchor, tuning.refinery_ore_cells)
    else:
        picked = _building_toward(target.patch, boxes, reaching, anchor)
    if picked is None:
        return None
    best, ore = picked
    creep = Creep(
        target.patch.center, ore, _distance_to(boxes[0], ore), _distance_to(boxes[best], ore), target.lane_rank
    )
    return PlacementChoice(candidates[best], creep=creep)


def _along_line(
    item: str,
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    promised: tuple[Box, ...],
    skipped: Collection[OrePatch],
    second_line: bool = False,
    routes: Mapping[OrePatch, tuple[Cell, ...]] = _NO_ROUTES,
) -> PlacementChoice | None:
    """A building's cell while the base walks toward the far field: see `_walk_line`, each line along the route walked
    to its far field in `routes` when there is one. With a second yard's line as well (`far_fields`), a building
    takes that line first when `second_line` says so and the main line first otherwise; a line with no candidate near
    it yields to the other, and with none near either the building takes the compact place by the yard (`_compact`).
    None with `creep_from_opening` off, for a refinery or a defense, and without a far field."""
    if not tuning.creep_from_opening or item == REFINERY or item in DEFENSE_TYPES:
        return None
    main, second = far_fields(world, terrain, tuning, promised, skipped)
    lines = _lines(world, tuning, main, second)
    if not lines:
        return None
    if second_line:
        lines.reverse()
    walked = (
        _walk_line(item, field, anchor, yard, world, terrain, tuning, promised, routes.get(field.patch))
        for field, anchor, yard in lines
    )
    home = (main or second).home
    return next((choice for choice in walked if choice is not None), None) or _compact(
        item, world, terrain, tuning, promised, home
    )


def _compact(
    item: str,
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    promised: tuple[Box, ...],
    home: OrePatch | None,
) -> PlacementChoice:
    """Where a building goes while no line toward a far field has a place near it: the place nearest the first
    construction yard, of its ring, else of those next to a building that stands, the places from which a refinery
    would stand against the `home` field left out while there are others; left to the platform without one."""
    anchor = _anchor(world)
    ring = candidate_cells(item, world, terrain, tuning, promised, anchor)
    places = ring or _creep_candidates(item, ring, anchor, world, terrain, tuning, promised)
    free = _off_home_refinery_places(places, home, tuning.refinery_ore_cells, terrain)
    return PlacementChoice(free[0] if free else None)


def _lines(
    world: World, tuning: PlacementTuning, main: FarField | None, second: FarField | None
) -> list[tuple[FarField, Cell | None, str | None]]:
    """The lines a building may walk, the main base's first, each with its far field, the yard its ring lies around
    and, while the second yard's line is there, which of the two it is; a line without a far field is left out."""
    if second is None:
        return [] if main is None else [(main, _anchor(world), None)]
    yard = second_yard(world, tuning)
    lines = [(main, _anchor(world), MAIN_LINE), (second, (yard.x, yard.y), SECOND_LINE)]
    return [line for line in lines if line[0] is not None]


def _walk_line(
    item: str,
    field: FarField,
    anchor: Cell | None,
    yard: str | None,
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    promised: tuple[Box, ...],
    route: tuple[Cell, ...] | None = None,
) -> PlacementChoice | None:
    """A building's cell along the line from a far field's origin to its ore cell nearest the origin, or along the
    `walked_line` of the route walked to the field when there is one: of the candidates of the ring around `anchor` and
    next to any building that stands, the one `_farthest_along` that line, the places from which a refinery would
    stand against the home field left out while there are others; None without a candidate near the line. Its creep
    names the `yard` whose line it walked, if any. With `second_line_local` on, the second yard's line takes only the
    candidates `_second_line_local` leaves it."""
    ring = candidate_cells(item, world, terrain, tuning, promised, anchor)
    if yard == SECOND_LINE and tuning.second_line_local:
        candidates = _second_line_local(item, ring, anchor, world, terrain, tuning, promised)
    else:
        candidates = _creep_candidates(item, ring, anchor, world, terrain, tuning, promised)
    if not candidates:
        return None
    ore = field.patch.nearest_cell(field.origin)
    free = _off_home_refinery_places(candidates, field.home, tuning.refinery_ore_cells, terrain)
    line = (field.origin, ore) if route is None else walked_line(field.origin, route)
    along = _farthest_along(item, free, line, tuning.creep_line_cells)
    if along is None:
        return None
    cell, off = along
    before, after = (_distance_to(box_at(item, place), ore) for place in (candidates[0], cell))
    return PlacementChoice(cell, creep=Creep(field.patch.center, ore, before, after, line_distance=off, yard=yard))


def _second_line_local(
    item: str,
    ring: list[Cell],
    anchor: Cell,
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    promised: tuple[Box, ...],
) -> list[Cell]:
    """The candidates of the second yard's line with `second_line_local` on: those of the ring around the yard
    (`anchor`) and those next to an own building whose middle lies within `second_line_radius_cells` of the yard's
    middle, never one next to the main base; and of them none more than `second_line_radius_cells` + `adjacent_cells`
    + `_SECOND_LINE_SLACK_CELLS` from the yard."""
    yard_middle = _middle_cell(box_at(CONSTRUCTION_YARD, footprint_corner(CONSTRUCTION_YARD, anchor)))
    local = [
        box
        for box in (standing_box(building) for building in world.buildings.values())
        if cells.is_within(_middle_cell(box), yard_middle, tuning.second_line_radius_cells)
    ]
    candidates = _creep_candidates(item, ring, anchor, world, terrain, tuning, promised, local)
    farthest = tuning.second_line_radius_cells + tuning.adjacent_cells + _SECOND_LINE_SLACK_CELLS
    return [cell for cell in candidates if cells.is_within(cell, anchor, farthest)]


def _off_home_refinery_places(
    candidates: list[Cell], home: OrePatch | None, reach: int, terrain: Terrain
) -> list[Cell]:
    """The candidates from which a refinery would not stand within `reach` of the home field's ore; all of them
    without a home field and when every one of them would."""
    if home is None:
        return candidates
    free = [cell for cell in candidates if not reaches(box_at(REFINERY, cell), home, reach, terrain)]
    return free or candidates


def walked_line(origin: Cell, route: Sequence[Cell]) -> tuple[Cell, ...]:
    """The way a line toward a far field takes with a route walked to it: from the line's start `origin` straight to
    the point of the route nearest it, of several as near the first, then through the route's points from there on."""
    nearest = min(range(len(route)), key=lambda index: cells.distance(route[index], origin))
    return (origin, *route[nearest:])


def _farthest_along(item: str, candidates: list[Cell], line: Sequence[Cell], band: int) -> tuple[Cell, float] | None:
    """The candidate a building takes along a line through the cells of `line`, and how far its middle lies from the
    line: of those whose middle lies within `band` cells of it, the one farthest along it, of several as far the one
    nearest the line, the first of several as good; None with none that close. A place off the line that is merely
    nearest the field's ore would string the base out toward it (see `_along_line`)."""
    placed = [(cell, *_along_and_off_way(box_at(item, cell).center, line)) for cell in candidates]
    in_band = [entry for entry in placed if entry[2] <= band]
    if not in_band:
        return None
    cell, _, off = min(in_band, key=lambda entry: (-round(entry[1], _LINE_DECIMALS), round(entry[2], _LINE_DECIMALS)))
    return cell, off


def _along_and_off_way(point: tuple[float, float], line: Sequence[Cell]) -> tuple[float, float]:
    """How far along a line through the cells of `line` the point of the line nearest a point lies, and how far that
    point is from the point, in cells; of several points of the line as near, the one least far along it."""
    nearest_off, nearest_along, walked = math.inf, 0.0, 0.0
    for start, end in itertools.pairwise(line):
        along, off = _along_and_off(point, start, end)
        if off < nearest_off:
            nearest_off, nearest_along = off, walked + along
        walked += cells.distance(start, end)
    return nearest_along, nearest_off


def _along_and_off(point: tuple[float, float], start: Cell, end: Cell) -> tuple[float, float]:
    """How far along the straight line from `start` to `end` a point lies, and how far from the line, in cells: a
    point beyond either end counts as at that end, and its distance is to that end."""
    span_x, span_y = end[0] - start[0], end[1] - start[1]
    rel_x, rel_y = point[0] - start[0], point[1] - start[1]
    length_squared = span_x * span_x + span_y * span_y
    share = min(max((rel_x * span_x + rel_y * span_y) / length_squared, 0.0), 1.0) if length_squared else 0.0
    return share * math.sqrt(length_squared), math.hypot(rel_x - share * span_x, rel_y - share * span_y)


def _refinery_toward(
    patch: OrePatch, boxes: list[Box], reaching: list[int], anchor: Cell, reach: int
) -> tuple[int, Cell] | None:
    """Which of the boxes a refinery takes to stand against a field, and the ore cell it is placed nearest: of those
    that reach the field, the one nearest the reachable ore cell nearest the yard; None when none reaches it."""
    if not reaching:
        return None
    ore = _reachable_ore_cell(patch, [boxes[index] for index in reaching], anchor, reach)
    return min(reaching, key=lambda index: _distance_to(boxes[index], ore)), ore


def _building_toward(patch: OrePatch, boxes: list[Box], reaching: list[int], anchor: Cell) -> tuple[int, Cell] | None:
    """Which of the boxes a building that is not a refinery takes to creep toward a field, and the ore cell it is
    placed nearest: the one nearest the field's ore cell nearest the yard, of those from which no refinery would reach
    the field while there are any; None without a box."""
    if not boxes:
        return None
    ore = patch.nearest_cell(anchor)
    for_refineries = set(reaching)
    left_free = [index for index in range(len(boxes)) if index not in for_refineries]
    return min(left_free or reaching, key=lambda index: _distance_to(boxes[index], ore)), ore


def _refinery_cap(item: str, tuning: PlacementTuning, field_cap: bool) -> int | None:
    """How many refineries a field a refinery may stand against has at most: `max_refineries_per_field` with the
    cap on; None for any other building or with the cap off."""
    return tuning.max_refineries_per_field if field_cap and item == REFINERY else None


def _under_cap(candidates: list[Cell], world: World, terrain: Terrain, cap: int | None) -> list[Cell]:
    """The refinery candidates whose field fewer than `cap` refineries serve; all of them without a cap or when no
    candidate's field is under it, since a refinery that is finished has to be placed."""
    if cap is None:
        return candidates
    served = refineries_serving(world, terrain)
    under = [cell for cell in candidates if (field := field_at(cell, terrain)) is not None and served[field] < cap]
    return under or candidates


def _creep_candidates(
    item: str,
    ring: list[Cell],
    anchor: Cell,
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    promised: tuple[Box, ...],
    near: Collection[Box] | None = None,
) -> list[Cell]:
    """The cells a building may be asked onto while the base creeps toward its target: the ring's candidates and
    every place next to a building that stands, or next to one of those `near` when given, nearest the construction
    yard first."""
    if world.map_bounds is None:
        return []
    ground = _ground(item, world, terrain, tuning, promised)
    width, height = footprint(item)
    # Every top-left cell from which a building of this size would come within the reach of one that stands.
    around = {
        (x, y)
        for box in (ground.standing if near is None else near)
        for y in range(box.top - ground.reach - height + 1, box.top + box.height + ground.reach)
        for x in range(box.left - ground.reach - width + 1, box.left + box.width + ground.reach)
    }
    near = set(ring) | {cell for cell in around.difference(ring) if ground.takes(box_at(item, cell))}
    # Ordered as the ring is, so that of several places as near the target the one nearest the yard is taken.
    return sorted(near, key=lambda cell: _ring_order(cell, anchor))


def _ring_order(cell: Cell, anchor: Cell) -> tuple[int, int, int]:
    """Where a cell comes in the order of the rings around the construction yard: by distance, then row and column."""
    dx, dy = cell[0] - anchor[0], cell[1] - anchor[1]
    return dx * dx + dy * dy, dy, dx


def _reached_fields(
    patches: list[OrePatch],
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    promised: tuple[Box, ...],
) -> set[OrePatch]:
    """The fields a refinery stands against, or could stand against now from a place in the ring or next to a
    building that stands. A field built up all round once its refineries stand has no free place left by it, and is
    no farther out of their range for that."""
    places = _refinery_places(world, terrain, tuning, promised)
    covered = _covered((*places, *_standing_refineries(world)))
    reach = tuning.refinery_ore_cells
    return {patch for patch in patches if not covered.isdisjoint(terrain.walk_zone(patch, reach))}


def claim_placement(
    field: OrePatch,
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    promised: Iterable[Box] = (),
    refused: Iterable[Box] = (),
) -> PlacementChoice | None:
    """The cell a refinery that claims a field is asked onto, with the field and the rule FIELD_CLAIM, as
    `stand_against` gives it."""
    return stand_against(field, world, terrain, tuning, promised, refused, FIELD_CLAIM)


def stand_against(
    field: OrePatch,
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    promised: Iterable[Box] = (),
    refused: Iterable[Box] = (),
    rule: str = FIELD_CLAIM,
) -> PlacementChoice | None:
    """The cell a refinery is asked onto to stand against a field, with the field and `rule`: of the places in the ring
    of the construction yard nearest the field and next to every building that stands, clear of the `promised` and
    `refused` places, the one `_refinery_toward` gives; None when none of them reaches the field."""
    places = RefineryPlaces(world, with_refused(terrain, refused), tuning, tuple(promised))
    return places.against(field, rule)


def spread_placement(
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    asked: Collection[OrePatch],
    promised: Iterable[Box] = (),
    refused: Iterable[Box] = (),
    rule: str = FIELD_SPREAD,
    untaken_only: bool = False,
) -> PlacementChoice | None:
    """The cell a refinery is asked onto while refineries go one to a field, with its field and `rule`: against the
    field nearest an own construction yard that is not taken (`taken_fields`, the `asked` fields among them) and that a
    refinery can stand against now; with none, and unless `untaken_only`, against the nearest that fewer than
    `max_refineries_per_field` refineries serve or are asked against. A refinery stands against a field as
    `stand_against` places it. None without a base or such a field."""
    if _anchor(world) is None:
        return None
    terrain = with_refused(terrain, refused)
    nearest_first = sorted(terrain.ore_patches, key=lambda patch: _yard_distance(patch, world))
    taken = taken_fields(world, terrain, tuning, asked)
    load = refineries_serving(world, terrain) + Counter(asked)
    untaken = (patch for patch in nearest_first if patch not in taken)
    under_cap = (patch for patch in nearest_first if load[patch] < tuning.max_refineries_per_field)
    places = RefineryPlaces(world, terrain, tuning, tuple(promised))
    fields = untaken if untaken_only else itertools.chain(untaken, under_cap)
    choices = (places.against(field, rule) for field in fields)
    return next((choice for choice in choices if choice is not None), None)


def _yard_distance(patch: OrePatch, world: World) -> float:
    """How far a field's center lies from the own construction yard nearest it; from the base's center without one."""
    yard_cells = [(b.x, b.y) for b in world.buildings.values() if b.type == CONSTRUCTION_YARD] or [world.base_center]
    return min(cells.distance(patch.center, yard) for yard in yard_cells)


class RefineryPlaces:
    """Where a refinery is asked onto to stand against a field: the places of the ring of the construction yard nearest
    the field and next to every building that stands, clear of the `promised` places, worked out once for each yard."""

    def __init__(self, world: World, terrain: Terrain, tuning: PlacementTuning, promised: tuple[Box, ...]) -> None:
        self._world, self._terrain, self._tuning, self._promised = world, terrain, tuning, promised
        # The cell of a yard -> the places a refinery could take by it, nearest the yard first, and their boxes.
        self._by_yard: dict[Cell, tuple[list[Cell], list[Box]]] = {}

    def against(self, field: OrePatch, rule: str, keep: Callable[[Box], bool] | None = None) -> PlacementChoice | None:
        """The place `_refinery_toward` gives against a field, with the field and the rule, of those whose box `keep`
        keeps when given; None when no such place reaches the field, and without a base."""
        anchor = _yard_nearest(self._world, field)
        if anchor is None:
            return None
        candidates, boxes = self._by(anchor)
        reach = self._tuning.refinery_ore_cells
        reaching = [
            index
            for index, box in enumerate(boxes)
            if reaches(box, field, reach, self._terrain) and (keep is None or keep(box))
        ]
        picked = _refinery_toward(field, boxes, reaching, anchor, reach)
        if picked is None:
            return None
        served_by = refineries_serving(self._world, self._terrain)[field]
        return PlacementChoice(candidates[picked[0]], FieldChoice(field, rule, served_by))

    def _by(self, anchor: Cell) -> tuple[list[Cell], list[Box]]:
        """The places a refinery could take by a yard, and their boxes."""
        if anchor not in self._by_yard:
            world, terrain, tuning, promised = self._world, self._terrain, self._tuning, self._promised
            ring = candidate_cells(REFINERY, world, terrain, tuning, promised, anchor)
            candidates = _creep_candidates(REFINERY, ring, anchor, world, terrain, tuning, promised)
            self._by_yard[anchor] = (candidates, [box_at(REFINERY, cell) for cell in candidates])
        return self._by_yard[anchor]


def _yard_nearest(world: World, field: OrePatch) -> Cell | None:
    """The cell of the construction yard nearest a field's ore cell nearest the first yard, the first yard of several
    as near; as `_anchor` with fewer than two yards."""
    yard_cells = [
        (building.x, building.y) for building in world.buildings.values() if building.type == CONSTRUCTION_YARD
    ]
    if len(yard_cells) < 2:
        return _anchor(world)
    ore = field.nearest_cell(yard_cells[0])
    return min(yard_cells, key=lambda yard: cells.distance(yard, ore))


def claimable(fields: Iterable[OrePatch], world: World, terrain: Terrain, tuning: PlacementTuning) -> list[OrePatch]:
    """Of the fields given, in their order, those a refinery could be asked to stand against now from a free place in
    the ring or next to a building that stands, that no own refinery stands within `refinery_ore_cells` of yet, and
    that no remembered enemy defense stands within `creep_defense_clear_cells` of; none without a base."""
    fields = list(fields)
    if not fields or _anchor(world) is None:
        return []
    standing = _covered(_standing_refineries(world))
    free = _covered(_refinery_places(world, terrain, tuning, ()))
    zones = {field: terrain.walk_zone(field, tuning.refinery_ore_cells) for field in fields}
    return [
        field
        for field in fields
        if standing.isdisjoint(zones[field])
        and not free.isdisjoint(zones[field])
        and not is_defended(field, world, tuning.creep_defense_clear_cells)
    ]


def _refinery_places(world: World, terrain: Terrain, tuning: PlacementTuning, promised: tuple[Box, ...]) -> list[Cell]:
    """The places a refinery could be asked onto now, clear of the `promised` buildings: those of the ring around the
    first construction yard and every place next to a building that stands."""
    anchor = _anchor(world)
    ring = candidate_cells(REFINERY, world, terrain, tuning, promised, anchor)
    return _creep_candidates(REFINERY, ring, anchor, world, terrain, tuning, promised)


def _standing_refineries(world: World) -> list[Cell]:
    """The top-left cells of the footprints of the own refineries that stand."""
    return [
        footprint_corner(building.type, (building.x, building.y))
        for building in world.buildings.values()
        if building.type == REFINERY
    ]


def _covered(places: Iterable[Cell]) -> frozenset[Cell]:
    """The cells refineries at the places would cover. A refinery stands within reach of a field when it covers a cell
    of the field's reach zone, so the cells all of them cover answer for every field at once."""
    return frozenset(cell for place in places for cell in box_at(REFINERY, place).cells())


def _may_be_far_field(
    patch: OrePatch,
    origin: Cell,
    base: list[Box],
    world: World,
    tuning: PlacementTuning,
    passed_over: Collection[OrePatch],
) -> bool:
    """Whether a field no refinery can stand against may be the far field: not one of those `passed_over`, within the
    creep cap of the `base` (`is_within_creep_cap`, measured from `origin` without a building), more than
    `creep_enemy_clear_cells` from the enemy's start when that is known, and with no remembered enemy defense within
    `creep_defense_clear_cells` of it."""
    enemy = world.enemy_base
    is_clear_of_enemy = enemy is None or not cells.is_within(patch.center, enemy, tuning.creep_enemy_clear_cells)
    return (
        patch not in passed_over
        and is_within_creep_cap(patch, base, origin, tuning.creep_far_max_cells)
        and is_clear_of_enemy
        and not is_defended(patch, world, tuning.creep_defense_clear_cells)
    )


def is_within_creep_cap(patch: OrePatch, base: Collection[Box], origin: Cell, cap: int) -> bool:
    """Whether a field's nearest ore cell lies within `cap` cells, counted as a king moves, of the nearest footprint of
    the `base`, the own buildings but defenses of a line; of `origin` without one. The base walks on from wherever it
    has grown to, never toward a field this far beyond its edge, whatever `creep_continuous` says: a target 40 cells
    off strings buildings out across the map."""
    return any(ore_distance(box, patch) <= cap for box in base or [Box(origin[0], origin[1], 1, 1)])


def _has_room_against(
    patch: OrePatch, anchor: Cell, world: World, terrain: Terrain, tuning: PlacementTuning, promised: tuple[Box, ...]
) -> bool:
    """Whether a free place in the ring could take a refinery against a field once a building stood near it."""
    ground = _ground(REFINERY, world, terrain, tuning, promised)
    zone = terrain.walk_zone(patch, tuning.refinery_ore_cells)
    boxes = (box_at(REFINERY, cell) for cell in _ring_cells(anchor, tuning))
    return any(box.holds_any(zone) and ground.is_open(box) for box in boxes)


def _target_field(
    anchor: Cell, refinery_boxes: list[Box], world: World, terrain: Terrain, tuning: PlacementTuning
) -> OrePatch | None:
    """The field a refinery covering one of the boxes would serve now: by reach when that choice is switched on,
    else the nearest field in reach that no refinery serves, else the nearest in reach."""
    if not tuning.refinery_field_by_reach:
        return _patch_to_serve(anchor, world, terrain, tuning.ore_reach_cells)
    field = _field_by_reach(anchor, refinery_boxes, world, terrain, tuning)
    return None if field is None else field.patch


def _field_by_reach(
    anchor: Cell, boxes: list[Box], world: World, terrain: Terrain, tuning: PlacementTuning
) -> FieldChoice | None:
    """The field a refinery covering one of the boxes is to serve, by the first FIELD_ rule that gives one; None when
    no field within `ore_reach_cells` of the construction yard takes another refinery."""
    in_reach = [patch for patch in terrain.ore_patches if cells.is_within(patch.center, anchor, tuning.ore_reach_cells)]
    served = refineries_serving(world, terrain)
    reached = [patch for patch in in_reach if _is_reached(patch, boxes, tuning.refinery_ore_cells, terrain)]
    for rule, fields in _fields_by_rule(in_reach, reached, served, tuning):
        nearest = terrain.nearest_patch(anchor, fields)
        if nearest is not None:
            return FieldChoice(nearest, rule, served[nearest])
    return None


def _fields_by_rule(
    in_reach: list[OrePatch], reached: list[OrePatch], served: Counter[OrePatch], tuning: PlacementTuning
) -> tuple[tuple[int, list[OrePatch]], ...]:
    """Each FIELD_ rule, in the order they are tried, with the fields in reach it allows."""
    return (
        (FIELD_UNSERVED_REACHED, [patch for patch in reached if not served[patch]]),
        (FIELD_SHARED_REACHED, [patch for patch in reached if _can_share(patch, served, tuning)]),
        (FIELD_UNSERVED_NEAREST, [patch for patch in in_reach if not served[patch]]),
        (FIELD_NEAREST_UNDER_CAP, [patch for patch in in_reach if served[patch] < tuning.max_refineries_per_field]),
    )


def _can_share(patch: OrePatch, served: Counter[OrePatch], tuning: PlacementTuning) -> bool:
    """Whether a served field takes one more refinery: fewer serve it than a field takes, and it is large enough."""
    return served[patch] < tuning.max_refineries_per_field and patch.size >= tuning.shared_field_min_cells


def refineries_serving(world: World, terrain: Terrain) -> Counter[OrePatch]:
    """How many of the standing refineries serve each field."""
    return Counter(patch_of(building, terrain) for building in world.buildings.values() if building.type == REFINERY)


def _is_reached(patch: OrePatch, boxes: list[Box], reach: int, terrain: Terrain) -> bool:
    """Whether a refinery covering one of the boxes would stand within `reach` of the field's ore."""
    return any(reaches(box, patch, reach, terrain) for box in boxes)


def _cell_against(
    patch: OrePatch,
    candidates: list[Cell],
    boxes: list[Box],
    anchor: Cell,
    terrain: Terrain,
    tuning: PlacementTuning,
    *,
    by_reach: bool,
) -> Cell:
    """The candidate a refinery takes to stand against a field: of those within `refinery_ore_cells` of its ore by
    walking, or of all when none is, the one nearest the field's ore cell nearest the construction yard that one of
    them reaches when the field is chosen by reach, else the one nearest the field's center."""
    reach = tuning.refinery_ore_cells
    reaching = [index for index, box in enumerate(boxes) if reaches(box, patch, reach, terrain)]
    goal = _reachable_ore_cell(patch, [boxes[index] for index in reaching], anchor, reach) if by_reach else patch.center
    # The first of several equally near is taken, which is the one nearest the construction yard.
    best = min(reaching or range(len(candidates)), key=lambda index: _distance_to(boxes[index], goal))
    return candidates[best]


def _reachable_ore_cell(patch: OrePatch, reaching: list[Box], anchor: Cell, reach: int) -> Cell:
    """The ore cell of a field nearest the construction yard that a refinery covering one of the boxes stands within
    `reach` of; the field's ore cell nearest the yard when none is that near."""
    reachable = frozenset(cell for box in reaching for cell in box.grown(reach).cells() if cell in patch.cells)
    return (dataclasses.replace(patch, cells=reachable) if reachable else patch).nearest_cell(anchor)


def _nearest_ore_distance(boxes: list[Box], patch: OrePatch) -> int | None:
    """The fewest cells, counted as a king moves, between one of the boxes and the field's ore; None without a box."""
    xs, ys = [x for x, _ in patch.cells], [y for _, y in patch.cells]
    extent = Box(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
    best: int | None = None
    # A box is never nearer the ore than the rectangle around the field, so once that bound reaches the best found,
    # no box after it can do better.
    for box in sorted(boxes, key=lambda box: _cells_apart(box, extent)):
        if best is not None and _cells_apart(box, extent) >= best:
            break
        distance = ore_distance(box, patch)
        best = distance if best is None else min(best, distance)
    return best


def _cells_apart(box: Box, other: Box) -> int:
    """How many cells, counted as a king moves, lie from the nearest cell of one box to the nearest of the other."""
    across = max(other.left - (box.left + box.width - 1), box.left - (other.left + other.width - 1), 0)
    down = max(other.top - (box.top + box.height - 1), box.top - (other.top + other.height - 1), 0)
    return max(across, down)


@functools.lru_cache(maxsize=_REACH_ZONES_KEPT)
def _reach_zone(patch: OrePatch, reach: int) -> frozenset[Cell]:
    """The cells within `reach` of an ore cell of a field, counted as a king moves."""
    around = range(-reach, reach + 1)
    return frozenset((x + dx, y + dy) for x, y in patch.cells for dy in around for dx in around)


def field_at(cell: Cell, terrain: Terrain) -> OrePatch | None:
    """The ore field a refinery with its top-left corner on a cell would serve: the one nearest to its middle."""
    return terrain.nearest_patch(_middle_cell(box_at(REFINERY, cell)))


def patch_of(refinery: Building, terrain: Terrain) -> OrePatch | None:
    """The ore patch a refinery's harvesters work: the one nearest to it."""
    return terrain.nearest_patch(_middle_cell(standing_box(refinery)))


def _ore_lane(refinery: Building, terrain: Terrain) -> list[Cell]:
    """The cells on the straight line from a refinery to the ore patch its harvesters work."""
    patch = patch_of(refinery, terrain)
    if patch is None:
        return []
    return cells.line(_middle_cell(standing_box(refinery)), patch.center)


def _exit_cells(war_factory: Building, depth: int) -> list[Cell]:
    """The cells in front of a standing war factory's exit."""
    return _exit_box(standing_box(war_factory), depth).cells()


def _exit_box(box: Box, depth: int) -> Box:
    """The cells in front of the exit of a war factory that covers `box`: `depth` rows below it, as wide as it is."""
    return Box(box.left, box.top + box.height, box.width, depth)


def _anchor(world: World) -> Cell | None:
    """The cell a base is laid out around: the first construction yard's, else the base's center."""
    yard = _first_yard(world)
    return (yard.x, yard.y) if yard is not None else world.base_center


def _far_origin(world: World) -> Cell | None:
    """Where the line toward the far field starts: the middle cell of the first construction yard, else the base's
    center."""
    yard = _first_yard(world)
    return world.base_center if yard is None else _middle_cell(standing_box(yard))


def _first_yard(world: World) -> Building | None:
    return next((building for building in world.buildings.values() if building.type == CONSTRUCTION_YARD), None)


def _anchor_for(
    item: str, world: World, terrain: Terrain, tuning: PlacementTuning, target: CreepTarget | None
) -> Cell | None:
    """The cell a building of a type is laid out around: with two construction yards or more, the yard nearest the
    building's goal (its field, the creep target or the defense's point), the first without one; else as `_anchor`."""
    yard_cells = [(b.x, b.y) for b in world.buildings.values() if b.type == CONSTRUCTION_YARD]
    if len(yard_cells) < 2:
        return _anchor(world)
    goal = _anchor_goal(item, yard_cells, world, terrain, tuning, target)
    if goal is None:
        return yard_cells[0]
    # The first of several as near is taken, which is the first yard.
    return min(yard_cells, key=lambda yard: cells.distance(yard, goal))


def _anchor_goal(
    item: str,
    yard_cells: list[Cell],
    world: World,
    terrain: Terrain,
    tuning: PlacementTuning,
    target: CreepTarget | None,
) -> Cell | None:
    """The point the yard a building is laid out around is chosen by: the creep target's ore for a building that
    creeps toward it, the center of the field a refinery is to serve, the defense's point; None for any other."""
    first = yard_cells[0]
    if target is not None and item not in DEFENSE_TYPES:
        return target.patch.nearest_cell(first)
    if item == REFINERY:
        return _field_by_yards(yard_cells, world, terrain, tuning)
    if item in DEFENSE_TYPES and world.enemy_base is not None:
        return cells.toward(first, world.enemy_base, tuning.defense_distance_cells)
    return None


def _field_by_yards(yard_cells: list[Cell], world: World, terrain: Terrain, tuning: PlacementTuning) -> Cell | None:
    """The center of the field a refinery is to serve with several yards: of the fields each yard would have its
    refinery serve, an unserved one before a served one, the nearer its yard first; None without a field in reach."""
    served = refineries_serving(world, terrain)
    fields = [
        (served[patch] > 0, cells.distance(yard, patch.center), patch.center)
        for yard in yard_cells
        if (patch := _patch_to_serve(yard, world, terrain, tuning.ore_reach_cells)) is not None
    ]
    return min(fields)[-1] if fields else None


def open_cells(
    item: str, places: Iterable[Cell], world: World, terrain: Terrain, tuning: PlacementTuning
) -> list[Cell]:
    """The places a building of a type could stand with its top-left corner on, in the order given: inside the map,
    off the ground nothing can be built on and the cells kept free, and clear of the buildings that stand; none while
    the map's bounds are not known. Unlike a candidate, a place need not be next to a building."""
    if world.map_bounds is None:
        return []
    ground = _ground(item, world, terrain, tuning, ())
    return [cell for cell in places if ground.is_open(box_at(item, cell))]


def _middle_cell(box: Box) -> Cell:
    return cells.nearest_cell(*box.center)


def _distance_to(box: Box, goal: Cell) -> float:
    middle_x, middle_y = box.center
    return ((middle_x - goal[0]) ** 2 + (middle_y - goal[1]) ** 2) ** 0.5


@functools.lru_cache(maxsize=None)
def _ring_offsets(nearest: int, farthest: int) -> tuple[Cell, ...]:
    """The offsets of the cells whose distance from a cell is between two numbers of cells, nearest first."""
    offsets = [
        (dx, dy)
        for dy in range(-farthest, farthest + 1)
        for dx in range(-farthest, farthest + 1)
        if nearest * nearest <= dx * dx + dy * dy <= farthest * farthest
    ]
    return tuple(sorted(offsets, key=lambda offset: (offset[0] ** 2 + offset[1] ** 2, offset[1], offset[0])))
