"""The screened place: main's artillery and V2 out of contact stand close behind the screen, on its far side from the
threat.

An artillery piece or a V2 lives by standing where nothing reaches it. The siege's slots (see `siege`) put the
long-range units out of contact a band depth behind the screen toward the objective, and their fall-back stepped them
toward the base: with an enemy coming from behind or from a side, both could leave them unscreened, or step them into
the enemy. With `artillery_screen_place` of `[siege]` on, the long-range units out of contact take a place of their
own instead, while main moves by steps or waits at a step point, holds or gathers, or fights.

**Out of contact.** A long-range unit is out of contact while no enemy unit in sight and no known ground defense is
within its weapon range and two cells (`targeting.is_in_contact`): it has nothing in its band. A unit in contact
fires, closes in and falls back by the siege's rules, but one standing unscreened, on the threat's side of the
screen's centroid (an enemy come from behind), with no enemy close enough to fall back from, is placed as well: it
goes round to the screen's far side. The place applies only while main has a screen unit (one not long-range) within
`SCREEN_NEAR_CELLS` of the long-range units' centroid.

**The threat.** The threat direction runs from the screen's centroid, or while main moves by steps from the current
step point, toward the first of these that exists: the centroid of the visible enemy ground combat units and known
ground defenses (no air-only one) within artillery_threat_cells of a unit of main; on a stepped advance the move's
destination, on a move home or a step back the cell the move came from (the enemy lies behind); the enemy start.

**The place.** The place's center is the screen's centroid (the step point) moved artillery_offset_cells away from the
threat. The units stand on a line through it across the threat direction, slot_spacing_cells apart, the unit nearest
a place taking it first: in their order along the line, so that a turn of the threat swaps no two of them. Each place
is moved to the nearest cell on the map that the terrain does not block, outside the range of every known ground
defense, and taken by no other unit. A unit is sent there by a `move` at CONTACT when its place has moved
slot_resend_cells since its last order or the threat direction has turned artillery_turn_degrees since, at most once a
second (reissue_ticks).

**The kite.** With the switch on, an artillery piece or a V2 that an enemy has come too close to falls back
fallback_step cells straight away from the nearest such enemy, not toward the base (see `away_cell`), to a cell no
nearer to any other visible enemy combat unit than where it stands; with no such cell it steps toward its place.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.fight import Point, centroid, ground_defenses, is_near
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.targeting import is_in_contact
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

# The screen must have a unit this near the long-range units' centroid for them to be placed behind it.
SCREEN_NEAR_CELLS = 10
# A long-range unit this near its place stands at it.
AT_PLACE_CELLS = 1.5
# How far around a place's cell an open cell is sought.
SEARCH_CELLS = 3
# The turns tried, in degrees off straight away from the enemy, for a kite cell no nearer to another enemy.
KITE_TURNS = (0, 30, -30, 60, -60, 90, -90)
# The threat's sources, in the order they are looked for.
ENEMY = "enemy"
MOVE = "move"
ENEMY_START = "enemy_start"


@dataclass(frozen=True)
class Threat:
    """Where the threat lies, the unit vector toward it from the place's reference point, and what it was found by."""

    point: Point
    heading: Point
    source: str


@dataclass(frozen=True)
class Placement:
    """The moves of the long-range units placed in a cycle, and the ids of every unit placed, ordered or not."""

    intents: list[Intent]
    ids: frozenset[int]


@dataclass(frozen=True)
class _Sent:
    """The last order a unit was given to its place: when, to which cell, and the threat direction then."""

    tick: int
    cell: Cell
    heading: Point


def threat(center: Point, main_units: Sequence[Unit], hint: Cell | None, world: World, tuning: Tuning) -> Threat | None:
    """The threat direction from `center`: the enemies near main, else the cell `hint` (the stepped move's destination,
    or the cell a move home came from), else the enemy start; None when none of them lies off the center."""
    reach = tuning.siege.artillery_threat_cells
    near = [
        (record.x, record.y)
        for record in _ground_threats(world)
        if any(is_near((unit.x, unit.y), record.x, record.y, reach) for unit in main_units)
    ]
    candidates = (
        (_mean(near), ENEMY),
        (hint, MOVE),
        (world.enemy_base, ENEMY_START),
    )
    for point, source in candidates:
        heading = None if point is None else _heading(center, point)
        if heading is not None:
            return Threat(point, heading, source)
    return None


def place_cells(
    units: Sequence[Unit], center: Point, found: Threat, world: World, terrain: Terrain, tuning: Tuning
) -> dict[int, Cell]:
    """Each unit's place, by id: on the line across the threat direction artillery_offset_cells from `center` away
    from the threat, slot_spacing_cells apart, handed out in the units' order along the line (see `_across_order`),
    each an open cell out of every known ground defense's range taken by no other."""
    siege = tuning.siege
    heading = found.heading
    offset, spacing = siege.artillery_offset_cells, siege.slot_spacing_cells
    middle = (center[0] - heading[0] * offset, center[1] - heading[1] * offset)
    across = (-heading[1], heading[0])
    half = (len(units) - 1) / 2
    shifts = [(index - half) * spacing for index in range(len(units))]
    points = [(middle[0] + across[0] * shift, middle[1] + across[1] * shift) for shift in shifts]
    places: list[Cell | None] = []
    taken: set[Cell] = set()
    for point in points:
        cell = _open_place(cells.nearest_cell(*point), taken, world, terrain)
        if cell is not None:
            taken.add(cell)
        places.append(cell)
    return _across_order(units, places, middle, across)


def away_cell(
    unit: Unit,
    closing: Sequence[EnemyRecord],
    world: World,
    step: int,
    terrain: Terrain | None = None,
    place: Cell | None = None,
) -> Cell | None:
    """The kite fall-back of a long-range unit: `step` cells (fallback_step) straight away from the nearest enemy of `closing`,
    turned as little as `KITE_TURNS` allows to a cell on open ground no nearer to any other visible enemy combat unit
    than the unit's own; else `step` cells toward its `place`; None when it has none."""
    here = (unit.x, unit.y)
    nearest = min(closing, key=lambda record: (cells.distance(here, (record.x, record.y)), record.id))
    others = [
        (record.x, record.y)
        for record in world.enemy_memory.values()
        if record.visible and is_combat_unit(record) and record.id != nearest.id
    ]
    away = _heading((nearest.x, nearest.y), here) or (1.0, 0.0)
    for degrees in KITE_TURNS:
        turned = _turned(away, degrees)
        target = cells.nearest_cell(here[0] + turned[0] * step, here[1] + turned[1] * step)
        cell = _open_place(target, set(), world, terrain, defenses=False)
        if cell is not None and cell != here and _no_nearer(cell, here, others):
            return cell
    if place is None or place == here:
        return None
    return _open_place(cells.toward(here, place, step), set(), world, terrain, defenses=False)


class ScreenPlacer:
    """The places of main's long-range units out of contact, and the orders given to them. See the module's note."""

    def __init__(self) -> None:
        # Unit id -> the last order it was given to its place.
        self._sent: dict[int, _Sent] = {}
        # Unit id -> its place in the latest cycle it was placed in.
        self.places: dict[int, Cell] = {}
        self._seen: set[int] = set()

    def begin_cycle(self) -> None:
        """A cycle starts: the units placed in it are noted afresh."""
        self._seen = set()

    def end_cycle(self) -> None:
        """A cycle ends: a unit not placed in it is forgotten, and given its place afresh when next placed."""
        self._sent = {unit_id: sent for unit_id, sent in self._sent.items() if unit_id in self._seen}
        self.places = {unit_id: cell for unit_id, cell in self.places.items() if unit_id in self._seen}

    def place(
        self,
        long_range: Sequence[Unit],
        screen: Sequence[Unit],
        world: World,
        terrain: Terrain,
        tuning: Tuning,
        center: Point | None = None,
        hint: Cell | None = None,
        enemy_only: bool = False,
    ) -> Placement | None:
        """The placement of the long-range units out of contact or unscreened behind the screen (see `to_place`), the
        screen's centroid or `center` (a step point) the place's reference, `hint` the threat's cell after the enemies
        near; None with the switch off, no unit to place, no screen unit near them or no threat direction, and with
        `enemy_only` while no enemy is near main."""
        if not tuning.siege.artillery_screen_place or not long_range or not screen:
            return None
        if not has_screen_near(long_range, screen):
            return None
        reference = centroid(list(screen)) if center is None else center
        found = threat(reference, [*long_range, *screen], hint, world, tuning)
        if found is None or (enemy_only and found.source != ENEMY):
            return None
        free = to_place(long_range, reference, found, world, tuning)
        if not free:
            return None
        places = place_cells(free, reference, found, world, terrain, tuning)
        self._seen.update(unit.id for unit in free)
        self.places.update(places)
        intents = [
            intent
            for intent in (self._order(unit, places.get(unit.id), found.heading, world, tuning) for unit in free)
            if intent is not None
        ]
        return Placement(intents, frozenset(unit.id for unit in free))

    def _order(self, unit: Unit, cell: Cell | None, heading: Point, world: World, tuning: Tuning) -> Intent | None:
        """A unit's move to its place when it is due one (see `_is_due`); None otherwise."""
        if cell is None or not self._is_due(unit, cell, heading, world.tick, tuning):
            return None
        self._sent[unit.id] = _Sent(world.tick, cell, heading)
        if (unit.x, unit.y) == cell:
            return None
        return Intent(Priority.CONTACT, {"cmd": "move", "units": [unit.id], "to": list(cell)})

    def _is_due(self, unit: Unit, cell: Cell, heading: Point, tick: int, tuning: Tuning) -> bool:
        """Whether a unit is sent to its place: never sent there yet (and not standing there idle), or a second after
        its last order with the place moved slot_resend_cells since or the threat turned artillery_turn_degrees
        since."""
        siege = tuning.siege
        last = self._sent.get(unit.id)
        if last is None:
            return not (unit.idle and is_near(cell, unit.x, unit.y, AT_PLACE_CELLS))
        since = tick - last.tick
        if since < siege.reissue_ticks:
            return False
        if cells.distance(cell, last.cell) >= siege.slot_resend_cells:
            return True
        return turn_degrees(heading, last.heading) >= siege.artillery_turn_degrees


def to_place(long_range: Sequence[Unit], reference: Point, found: Threat, world: World, tuning: Tuning) -> list[Unit]:
    """The long-range units given a place: those out of contact, and those standing unscreened on the threat's side of
    the reference point with no enemy within their minimum range and fallback_margin, which they fall back from
    instead."""
    free = {unit.id for unit in out_of_contact(long_range, world)}
    heading = found.heading
    enemies = [record for record in world.enemy_memory.values() if record.visible and is_combat_unit(record)]
    damage = world.items.damage
    placed = []
    for unit in long_range:
        ahead = (unit.x - reference[0]) * heading[0] + (unit.y - reference[1]) * heading[1]
        reach = damage.get(unit.type).min_range + tuning.siege.fallback_margin
        is_kiting = any(is_near((unit.x, unit.y), record.x, record.y, reach) for record in enemies)
        if unit.id in free or (ahead > 0 and not is_kiting):
            placed.append(unit)
    return placed


def out_of_contact(units: Sequence[Unit], world: World) -> list[Unit]:
    """The long-range units with no enemy unit in sight and no known ground defense within their weapon range and two
    cells."""
    near = [
        *(record for record in world.enemy_memory.values() if record.visible and is_combat_unit(record)),
        *ground_defenses(world),
    ]
    return [unit for unit in units if not is_in_contact(unit, near, world)]


def has_screen_near(long_range: Sequence[Unit], screen: Sequence[Unit]) -> bool:
    """Whether a screen unit stands within SCREEN_NEAR_CELLS of the long-range units' centroid."""
    middle = centroid(list(long_range))
    return any(is_near(middle, unit.x, unit.y, SCREEN_NEAR_CELLS) for unit in screen)


def turn_degrees(first: Point, second: Point) -> float:
    """The angle between two unit vectors, in degrees."""
    dot = max(-1.0, min(1.0, first[0] * second[0] + first[1] * second[1]))
    return math.degrees(math.acos(dot))


def _ground_threats(world: World) -> list[EnemyRecord]:
    """The visible enemy ground combat units and the known ground defenses."""
    damage = world.items.damage
    units = [
        record
        for record in world.enemy_memory.values()
        if record.visible and is_combat_unit(record) and not damage.get(record.type).air
    ]
    return [*units, *ground_defenses(world)]


def _mean(points: Sequence[Point]) -> Point | None:
    """The centroid of some points; None for none."""
    if not points:
        return None
    return sum(point[0] for point in points) / len(points), sum(point[1] for point in points) / len(points)


def _heading(origin: Point, aim: Point) -> Point | None:
    """The unit vector from `origin` toward `aim`; None when the two are one point."""
    span = math.hypot(aim[0] - origin[0], aim[1] - origin[1])
    if span == 0:
        return None
    return (aim[0] - origin[0]) / span, (aim[1] - origin[1]) / span


def _turned(vector: Point, degrees: float) -> Point:
    """A vector turned by an angle in degrees."""
    angle = math.radians(degrees)
    cosine, sine = math.cos(angle), math.sin(angle)
    return vector[0] * cosine - vector[1] * sine, vector[0] * sine + vector[1] * cosine


def _no_nearer(cell: Cell, here: Cell, others: Sequence[Cell]) -> bool:
    """Whether a cell lies no nearer to any of `others` than `here` does."""
    return all(cells.distance(cell, other) >= cells.distance(here, other) for other in others)


def _open_place(
    cell: Cell, taken: Collection[Cell], world: World, terrain: Terrain | None, defenses: bool = True
) -> Cell | None:
    """The cell nearest to `cell`, itself first, within SEARCH_CELLS of it, that lies on the map, that the terrain does
    not block, that no other unit has taken and, with `defenses`, that lies out of every known ground defense's range;
    None for none."""
    blocked = frozenset() if terrain is None else terrain.blocked
    reach = [] if not defenses else _defense_reach(world)
    for dx, dy in _SEARCH_ORDER:
        found = (cell[0] + dx, cell[1] + dy)
        if not world.in_bounds(*found) or found in blocked or found in taken:
            continue
        if not any(cells.distance(found, origin) < weapon_range for origin, weapon_range in reach):
            return found
    return None


def _defense_reach(world: World) -> list[tuple[Cell, float]]:
    """Each known ground defense's cell with its weapon range."""
    damage = world.items.damage
    known = (((record.x, record.y), damage.get(record.type).range) for record in ground_defenses(world))
    return [(cell, weapon_range) for cell, weapon_range in known if weapon_range > 0]


def _across_order(
    units: Sequence[Unit], places: Sequence[Cell | None], middle: Point, across: Point
) -> dict[int, Cell]:
    """The places handed out over the units in their order along `across` from `middle`, by unit id: the unit
    farthest to one side takes the place farthest that way, so that a turn of the threat moves each unit's place a
    little and swaps none."""
    ordered = sorted(
        units, key=lambda unit: ((unit.x - middle[0]) * across[0] + (unit.y - middle[1]) * across[1], unit.id)
    )
    return {unit.id: place for unit, place in zip(ordered, places) if place is not None}


def _search_order(reach: int) -> tuple[Cell, ...]:
    """The offsets within `reach` cells, the nearest first."""
    span = range(-reach, reach + 1)
    offsets = [(dx, dy) for dx in span for dy in span if dx * dx + dy * dy <= reach * reach]
    return tuple(sorted(offsets, key=lambda offset: (offset[0] ** 2 + offset[1] ** 2, offset)))


_SEARCH_ORDER = _search_order(SEARCH_CELLS)
