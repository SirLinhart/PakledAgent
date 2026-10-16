"""The ground defenses of the main base in a line across the attack lane, on the base's edge
(`[placement] defense_line_across_lane`).

A defense ordered toward a cell (`MatchContext.defense_goals`: base defense's
extra defense, the canary and approach alarms, the spending focus's and the
counter quota's defenses, a Tesla coil against a vehicle attack) was placed at
the candidate nearest that cell, and the cell was often a few cells out from
the base's centre: in the live games of 20261016 the approach alarm's third
and fourth pillboxes went up between our power plant and our refinery, deep
inside the base, while the base reached 9 cells farther toward the enemy; and
the defenses of every rule bunched on one front point (`defense_front`) while
a power plant on another lane died out of their reach.

With the switch on, a ground defense (an anti-air one is left alone) whose
cell is nearer the main yard than any other yard of ours, and one ordered
toward no cell at all, but one whose goal is a place of its own (the harass
tower's, the endgame lead's: `MatchContext.exact_defense_goals`), is placed toward its slot on the line across the lane
instead (`line_slot`). The lane runs from our base's centre toward the cell
the defense was ordered toward; with none, toward the lane threats came by
(`LaneMemory`, one cell for each lane LANE_DEGREES apart) or the enemy's
start, whichever line holds the fewest of our ground defenses, so that every
lane enemies have used gets its line. The front point lies
defense_line_front_cells beyond our building farthest out along the lane, of
those but the defenses within MAIN_BASE_CELLS of the centre (an expansion far out is not the
main base), worked out again for every defense, so that the line follows the
base's edge as it grows toward the enemy. The slots lie across the lane
through it, 0, +s, -s, +2s, -2s ... cells from it (s =
defense_line_spacing_cells), and a defense takes the first slot no own ground
defense stands within s - 1 cells of. Placement then takes the candidate
nearest the slot, as it did the cell.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.defense_cap import GROUND_DEFENSES, MAIN_BASE_CELLS
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.scout_missions import enemy_start
from ucla_agent.games.redalert.world import DEFENSE_TYPES, Cell, World

# The most slots tried on each side of the front point: a line wider than this is no longer across one lane.
MAX_SLOTS_PER_SIDE = 4
# Two threats whose cells lie this far apart in direction, seen from our base's centre, came by two lanes.
LANE_DEGREES = 45


class LaneMemory:
    """The cells threats to our base were declared against, one for each lane (LANE_DEGREES apart at least, seen from
    our base's centre), followed by upkeep in every cycle."""

    def __init__(self) -> None:
        self.cells: list[Cell] = []

    def note(self, world: World, ctx: MatchContext) -> None:
        """Take in the threat base defense published in the cycle: a new lane, or the newest cell of its lane."""
        cell = ctx.threat_cell
        if cell is None or ctx.threat_aircraft_only or world.base_center is None or cell == world.base_center:
            return
        heading = _heading(world.base_center, cell)
        self.cells = [
            known for known in self.cells if _turn(_heading(world.base_center, known), heading) >= LANE_DEGREES
        ]
        self.cells.append(cell)


def is_on(item: str, ctx: MatchContext) -> bool:
    """Whether a defense of a type is placed on the line: the switch is on and it is a ground defense."""
    return ctx.tuning.placement.defense_line_across_lane and item in GROUND_DEFENSES


def lane_goal(goal: Cell | None, world: World, ctx: MatchContext, lanes: LaneMemory) -> Cell | None:
    """The cell the lane runs toward: the defense's own goal; else, of the lanes threats came by and the enemy's start,
    the one whose line has the fewest of our ground defenses; None for a goal by another yard of ours, which the line
    of the main base is not for."""
    if world.base_center is None:
        return None
    if goal is not None:
        return goal if _is_main_yards(goal, world) else None
    start = enemy_start(world)
    candidates = [cell for cell in [*lanes.cells, start] if cell is not None and _is_main_yards(cell, world)]
    return min(candidates, key=lambda cell: _defenses_on_line(cell, world, ctx), default=None)


def _defenses_on_line(target: Cell, world: World, ctx: MatchContext) -> int:
    """How many of our ground defenses stand within the line's reach of its front point toward a cell."""
    front = _front(target, world, ctx)
    reach = ctx.tuning.placement.defense_line_spacing_cells * (MAX_SLOTS_PER_SIDE + 1)
    return sum(
        1
        for building in world.buildings.values()
        if building.type in GROUND_DEFENSES and cells.is_within((building.x, building.y), front, reach)
    )


def _heading(origin: Cell, cell: Cell) -> float:
    return math.degrees(math.atan2(cell[1] - origin[1], cell[0] - origin[0]))


def _turn(first: float, second: float) -> float:
    """The angle between two headings, in degrees, 0 to 180."""
    return abs((first - second + 180) % 360 - 180)


def line_slot(target: Cell, world: World, ctx: MatchContext) -> Cell:
    """The slot on the line across the lane toward `target` (see the module's note) a defense goes to."""
    tuning, base = ctx.tuning.placement, world.base_center
    span = cells.distance(base, target)
    if span == 0:
        return target
    along = ((target[0] - base[0]) / span, (target[1] - base[1]) / span)
    across = (-along[1], along[0])
    front = _front(target, world, ctx)
    standing = [(building.x, building.y) for building in world.buildings.values() if building.type in GROUND_DEFENSES]
    spacing = tuning.defense_line_spacing_cells
    for offset in _offsets(spacing):
        slot = cells.nearest_cell(front[0] + across[0] * offset, front[1] + across[1] * offset)
        if not any(cells.is_within(slot, defense, spacing - 1) for defense in standing):
            return cells.clamped(slot, world.map_bounds) if world.map_bounds else slot
    return cells.nearest_cell(*front)


def _front(target: Cell, world: World, ctx: MatchContext) -> tuple[float, float]:
    """The front point of the line toward a cell: defense_line_front_cells beyond our main-base building farthest out
    toward it."""
    base = world.base_center
    span = cells.distance(base, target) or 1.0
    along = ((target[0] - base[0]) / span, (target[1] - base[1]) / span)
    # The defenses are the line, not the edge it covers.
    main_base = [
        building
        for building in world.buildings.values()
        if building.type not in DEFENSE_TYPES and cells.is_within((building.x, building.y), base, MAIN_BASE_CELLS)
    ]
    outermost = max(
        ((building.x - base[0]) * along[0] + (building.y - base[1]) * along[1] for building in main_base), default=0.0
    )
    reach = max(0.0, outermost) + ctx.tuning.placement.defense_line_front_cells
    return base[0] + along[0] * reach, base[1] + along[1] * reach


def _offsets(spacing: int) -> list[int]:
    """The offsets of the slots across the lane: 0, +s, -s, +2s, -2s ..., MAX_SLOTS_PER_SIDE a side."""
    offsets = [0]
    for step in range(1, MAX_SLOTS_PER_SIDE + 1):
        offsets += [step * spacing, -step * spacing]
    return offsets


def _is_main_yards(cell: Cell, world: World) -> bool:
    """Whether a cell lies nearer the main yard (the one nearest our base's centre) than any other yard of ours; true
    while we have no yard."""
    yards = [(building.x, building.y) for building in world.buildings.values() if building.type == CONSTRUCTION_YARD]
    if not yards:
        return True
    main = min(yards, key=lambda yard: cells.distance(yard, world.base_center))
    nearest = min(yards, key=lambda yard: cells.distance(yard, cell))
    return nearest == main or math.isclose(cells.distance(nearest, cell), cells.distance(main, cell))
