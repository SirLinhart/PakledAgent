"""The base's walk from its home ore field to the next field along the lane toward the enemy, field after field.

A base that keeps to the field its first refinery serves is left with a
field that runs dry. With `creep_next_patch` of the tuning file's
[placement] switched on, the base walks on once the home field has as many
refineries as a field takes, standing or on their way: its next creep target
is the field of the lane toward the enemy nearest the base. While there is a
target, placement asks every building but a defense onto the place nearest
the target's ore, so that the base grows toward it, and a refinery against
it once one can stand there (see `placement`). When the target has its
refineries, the next field of the lane is the target, and so on until none
is left.

The lane is the ground between the base and the enemy's start that the
base can grow into. A field lies in it when its center is within
`creep_lane_cells` of the straight line from the base's center to the
enemy's start, at most `creep_max_share` of the way along it and more than
`creep_enemy_clear_cells` from the enemy's start, and no remembered enemy
defense stands within `creep_defense_clear_cells` of one of its cells. The
home field is never a target, nor a field so close to it that a refinery by
one would serve the other: none of its cells may lie within
`adjacent_patch_cells` of a cell of the home field.

What is read: the map's picture of the ore at the start of the match, the
own refineries, the remembered enemy defenses and the enemy's start. The
home field is the field the first refinery to stand serves, and stays so for
the match.

With `creep_from_opening` switched on, the creep target is the far field
instead whenever there is one, from the first cycle of the match: the
nearest field out of a refinery's range, in any direction, with no lane (see
`placement`). It is named first in its place, as the one field the base
walks to, and it is what the second yard and the forward hold go by. With
`creep_continuous` switched on as well, the far field may lie any distance
away and is never a field placement skipped.

The far field moves on as soon as a refinery could stand against it, before
one does. With `refinery_claims_reached` of [upkeep] switched on, the field
the base so reached is kept as a claim field, for a refinery to claim (see
`upkeep`): until an own refinery stands within `refinery_ore_cells` of it, a
remembered enemy defense stands within `creep_defense_clear_cells` of it, or
no free place lets a refinery stand against it any more. The claim fields
are kept oldest first. A second yard's own far field (see `placement`, with
`remote_yard_growth` on) is followed the same way: a field the second yard's
line reaches is a claim field like one the main base's line reaches, after
any older one. The second yard's far field is never the creep target. With
`refinery_spread` of [placement] switched on, a claim field that is taken (a
refinery stands, was asked or is on order against it; see `placement`) is
dropped for the rest of the match, so that no field is claimed twice.

With `creep_stall_skip` of [placement] switched on, the far field of a line
the base makes no headway toward is given up (see `creep_stall`): it joins
the fields placement skipped, before the far field is worked out in the
cycle, so that the next one is named at once. With `walked_routes` of
[placement] switched on, the far field of each line is what a rifleman walks
to first (see `route_walk`), the second yard's line's included.
"""

from __future__ import annotations

import functools
import math
from typing import Collection

from ucla_agent.games.redalert import cells, placement
from ucla_agent.games.redalert.creep_stall import LineStall, log_skip
from ucla_agent.games.redalert.family import REFINERY
from ucla_agent.games.redalert.placement import CreepTarget
from ucla_agent.games.redalert.production import Production
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import PlacementTuning
from ucla_agent.games.redalert.world import Cell, World

# The first place in the lane.
_FIRST_RANK = 1
# How many fields, each at one distance, keep the cells around them worked out: more than the home fields of a match.
_AROUND_KEPT = 8


def lane_fields(world: World, terrain: Terrain, tuning: PlacementTuning) -> list[OrePatch]:
    """The ore fields in the lane toward the enemy, the nearest the base along the line first; none while the base's
    center or the enemy's start is not known. See the module's note."""
    start, end = world.base_center, world.enemy_base
    if start is None or end is None or start == end:
        return []
    length = cells.distance(start, end)
    in_lane = []
    for patch in terrain.ore_patches:
        along, off = _along_and_off(patch.center, start, end, length)
        is_on_the_way = 0 <= along <= tuning.creep_max_share * length and off <= tuning.creep_lane_cells
        is_clear = not cells.is_within(patch.center, end, tuning.creep_enemy_clear_cells)
        if is_on_the_way and is_clear and not placement.is_defended(patch, world, tuning.creep_defense_clear_cells):
            in_lane.append((along, patch.center[1], patch.center[0], patch))
    return [entry[-1] for entry in sorted(in_lane, key=lambda entry: entry[:3])]


def target_fields(home: OrePatch, world: World, terrain: Terrain, tuning: PlacementTuning) -> list[OrePatch]:
    """The fields of the lane that may be creep targets, in the lane's order: neither the home field nor one with a
    cell within `adjacent_patch_cells` of a home cell."""
    near_home = _cells_around(home, tuning.adjacent_patch_cells)
    return [
        patch for patch in lane_fields(world, terrain, tuning) if patch != home and near_home.isdisjoint(patch.cells)
    ]


class NextPatch:
    """Keeps the home field and names the creep target in each cycle. See the module's note."""

    def __init__(self) -> None:
        self._home: OrePatch | None = None
        # Whether the home field has had as many refineries as a field takes, standing or on their way: from then on
        # the base creeps.
        self._has_started = False

    def target(
        self, world: World, terrain: Terrain, production: Production, tuning: PlacementTuning
    ) -> CreepTarget | None:
        """The creep target now; None with the switch off, before the home field has its refineries, and once no
        field of the lane is left that takes another refinery."""
        if not tuning.creep_next_patch:
            return None
        self._home = self._home or _first_refinery_field(world, terrain)
        if self._home is None:
            return None
        served = placement.refineries_serving(world, terrain)
        planned_home = served[self._home] + production.pending(REFINERY)
        self._has_started = self._has_started or planned_home >= tuning.max_refineries_per_field
        if not self._has_started:
            return None
        fields = target_fields(self._home, world, terrain, tuning)
        return next(
            (
                CreepTarget(patch, rank)
                for rank, patch in enumerate(fields, start=_FIRST_RANK)
                if served[patch] < tuning.max_refineries_per_field
            ),
            None,
        )


class NearestFarField:
    """Names the far field as the creep target in each cycle. See the module's note.

    Where a refinery could stand is looked for around every building, which takes a few milliseconds in a large
    base, while what decides it changes only now and then; so the far field is worked out again only when the own
    buildings, the enemy's start or remembered defenses, the base's center, the map, the terrain, the fields placement
    skipped or the tuning differ from the last cycle's. The claim fields are looked at again then too: what decides
    them is the same.
    """

    def __init__(self) -> None:
        self._inputs: tuple[object, ...] | None = None
        self._target: CreepTarget | None = None
        # The far fields of the main base's line and of the second yard's, the second followed while the claims are kept
        # or stalls are watched.
        self._far: placement.FarField | None = None
        self._second: placement.FarField | None = None
        # The far fields the base reached that a refinery is to claim, the oldest first.
        self.claim_fields: list[OrePatch] = []
        # The claim fields dropped because they were taken, which are never claimed again.
        self._dropped: set[OrePatch] = set()
        # With `creep_stall_skip` on, the headway of each line toward its far field.
        self._stalls = {placement.MAIN_LINE: LineStall(), placement.SECOND_LINE: LineStall()}

    def note_line_placement(self, line: str | None, field_center: Cell, tick: int) -> None:
        """Note a building asked onto the map at a tick along a line toward the far field of `field_center`: the second
        yard's with `line` SECOND_LINE, else the main base's."""
        self._stalls[line or placement.MAIN_LINE].note_placement(field_center, tick)

    def skip_stalled(self, world: World, tuning: PlacementTuning, skipped: list[OrePatch]) -> None:
        """With `creep_from_opening` and `creep_stall_skip` on, add to the `skipped` fields the far field of each line
        that stalled (see `creep_stall`), written as a `creep_placement` event of the reason `stalled_skip`; the far
        field is worked out again in this cycle then. A field skipped already is not skipped again."""
        if not (tuning.creep_from_opening and tuning.creep_stall_skip):
            return
        for line, field in ((placement.MAIN_LINE, self._far), (placement.SECOND_LINE, self._second)):
            stall = self._stalls[line]
            if stall.is_stalled(field, world, tuning) and field.patch not in skipped:
                skipped.append(field.patch)
                log_skip(field, line, stall.best, world)
                self._inputs = None

    def lines(self) -> list[tuple[str, placement.FarField]]:
        """The far field of each line as the last cycle named it, the main base's line first, each with the line's
        name; a line without a far field is left out. The second yard's is followed only with the claims kept, stalls
        watched or routes walked."""
        lines = ((placement.MAIN_LINE, self._far), (placement.SECOND_LINE, self._second))
        return [(line, field) for line, field in lines if field is not None]

    def drop(self, taken: Collection[OrePatch]) -> None:
        """Drop from the claim fields, for the rest of the match, those that are `taken`: a refinery stands, was asked
        or is on order against them, so that a field is never claimed twice."""
        self._dropped.update(field for field in self.claim_fields if field in taken)
        self.claim_fields = [field for field in self.claim_fields if field not in taken]

    def target(
        self,
        world: World,
        terrain: Terrain,
        tuning: PlacementTuning,
        skipped: Collection[OrePatch] = (),
        claims: bool = False,
    ) -> CreepTarget | None:
        """The far field as the creep target, first in its place; None with the switch off and without a far field.
        The far field passes over the `skipped` fields with `creep_continuous` on. With `claims`, the claim fields are
        kept up to date as well."""
        if not tuning.creep_from_opening:
            return None
        inputs = _far_field_inputs(world, terrain, tuning, skipped)
        if inputs != self._inputs:
            field = placement.far_field(world, terrain, tuning, (), skipped)
            target = None if field is None else CreepTarget(field.patch, _FIRST_RANK)
            is_second_followed = claims or tuning.creep_stall_skip or tuning.walked_routes
            second = _second_far_field(world, terrain, tuning, skipped) if is_second_followed else None
            if claims:
                moves = [(_patch(self._target), _patch(target)), (_patch(self._second), _patch(second))]
                self._keep_claims(moves, world, terrain, tuning)
            self._inputs, self._target, self._far, self._second = inputs, target, field, second
        return self._target

    def _keep_claims(
        self,
        moves: list[tuple[OrePatch | None, OrePatch | None]],
        world: World,
        terrain: Terrain,
        tuning: PlacementTuning,
    ) -> None:
        """Add each far field the base has just moved on from, the main base's line first, to the claim fields, and keep
        of them only those a refinery is still to claim (`placement.claimable`): a field the base moved on from for
        any other reason than that a refinery could now stand against it is not claimable. Each move is the far field
        of a line before and now."""
        fields = list(self.claim_fields)
        for left, now in moves:
            if left is not None and left != now and left not in fields and left not in self._dropped:
                fields.append(left)
        self.claim_fields = placement.claimable(fields, world, terrain, tuning)


def _second_far_field(
    world: World, terrain: Terrain, tuning: PlacementTuning, skipped: Collection[OrePatch]
) -> placement.FarField | None:
    """The second yard's far field; None without a second yard, whose far fields are then not worked out at all."""
    if placement.second_yard(world, tuning) is None:
        return None
    return placement.far_fields(world, terrain, tuning, (), skipped)[1]


def _patch(target: CreepTarget | placement.FarField | None) -> OrePatch | None:
    """The field of a creep target or a far field; None without one."""
    return None if target is None else target.patch


def _far_field_inputs(
    world: World, terrain: Terrain, tuning: PlacementTuning, skipped: Collection[OrePatch]
) -> tuple[object, ...]:
    """What the far field is worked out from, as one value that tells two cycles apart when it differs."""
    return (
        terrain,
        tuning,
        tuple(patch.center for patch in skipped),
        world.map_bounds,
        world.base_center,
        world.enemy_base,
        tuple(sorted((building.type, building.x, building.y) for building in world.buildings.values())),
        tuple(sorted((record.x, record.y) for record in world.known_enemy_defenses)),
    )


def _first_refinery_field(world: World, terrain: Terrain) -> OrePatch | None:
    """The field the oldest standing refinery serves; None without a refinery or a field."""
    refineries = [building for building in world.buildings.values() if building.type == REFINERY]
    if not refineries:
        return None
    return placement.patch_of(min(refineries, key=lambda building: building.id), terrain)


def _along_and_off(point: Cell, start: Cell, end: Cell, length: float) -> tuple[float, float]:
    """How far along the line from `start` to `end` a point lies, and how far off it, in cells."""
    span_x, span_y = end[0] - start[0], end[1] - start[1]
    rel_x, rel_y = point[0] - start[0], point[1] - start[1]
    return (rel_x * span_x + rel_y * span_y) / length, abs(rel_x * span_y - rel_y * span_x) / length


@functools.lru_cache(maxsize=_AROUND_KEPT)
def _cells_around(patch: OrePatch, reach: int) -> frozenset[Cell]:
    """The cells within `reach` of a cell of a field, by straight-line distance."""
    span = math.ceil(reach)
    around = [
        (dx, dy) for dy in range(-span, span + 1) for dx in range(-span, span + 1) if dx * dx + dy * dy <= reach * reach
    ]
    return frozenset((x + dx, y + dy) for x, y in patch.cells for dx, dy in around)
