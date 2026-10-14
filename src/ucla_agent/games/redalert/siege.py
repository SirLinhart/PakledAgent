"""The siege: main's artillery and V2 fire from their firing band, behind a screen of main's other units.

An artillery piece (range 12) or a V2 launcher (range 10) outranges every
defense and every unit that can reach it, and has almost no armour: it wins
by firing from where nothing can hit it back, and loses the moment anything
closes in. So main's long-range units, its siege subgroup, are kept apart from
the rest of main, its screen, and ordered by their own rules.

**The firing band.** A long-range unit fires on a target from a distance in
its band: no farther than its weapon range less half a cell, and no nearer
than its minimum range and a cell, nor than the range and half a cell of any
known enemy defense that could reach it there. A defense that stands off the
target counts with its distance from it added, so that no cell of the band,
on whatever side, is in its reach. A target whose band is empty is not
engaged.

**Fire.** The long-range units are handed out over the enemies in sight
around them as the focus-fire assignment hands out a group (see `targeting`),
with the band in place of the weapon range and a defense weighted 1.5; having
no anti-air weapon, neither is ever given an aircraft. A unit whose target
stands in its band attacks it, and does not move while it does: moving to
the middle of the band and back would only cost it shots. A unit outside its
band moves to the nearest cell of the band's middle, and with no target in
sight it does so against main's objective.

**One defense.** Long-range units sent out of main to kill one defense (see
`command_detachments`) fire on that defense alone, from their band, with no
screen: each attacks it while it is in sight and stands in its band, moves
into the band otherwise, and is given nothing while the other defenses around
leave its band empty.

**Falling back.** A long-range unit that an enemy unit has come within its
minimum range and two cells of moves four cells toward the base, alone: the
screen holds. An artillery piece falls back once while the enemy stays that
close and then fires, since turning away and back takes it longer than its
reload. A V2, which turns in a second against a reload of 8.6, falls back
again each time it has stood and fired, while every enemy that close is
infantry, which it outruns. A fall-back is carried out to its end before the
unit is given anything else: cut short, it leaves the unit as close as it
was.

**The screen.** While main advances or fights, its screen holds a line a
few cells ahead of the siege subgroup's centroid toward main's objective (2
for artillery, 5 for V2s), and at least a cell outside the range of every
known enemy defense. It fights only the enemy units it is in contact with
(within its weapon range and two cells, `targeting.is_in_contact`), and
never a building: the defenses are the long-range units' to take. A unit
holding the line is sent to it once for each place the line moves to. No
long-range unit is sent ahead of the line, nor ahead of the screen itself, so
that main advances screen first.

**Marching while clear.** A screen that stands at a line a few cells ahead of
the long-range units, while they in turn wait for the screen, moves main a
fraction of a cell a second. So a march with no enemy near (see `march`)
goes otherwise: the screen attack-moves on the objective itself, sent again
only when it stands idle away from it or the objective changes, and the
long-range units out of contact follow it to their slots.

**Slots.** A long-range unit is out of contact while no enemy unit in sight
and no known defense is within its weapon range and two cells: it has nothing
to fire on or close in on. Sent all toward one point a step ahead of their own
centroid, such units jam on it, crawling into each other and ordered again
each second. So each one out of contact is given a cell of its own, its slot,
on a line across the approach toward the objective, `slot_spacing_cells`
apart and centered on the subgroup's centroid, the unit nearest a slot taking
it first. It moves straight there, and is sent there again only once the slot
has moved `slot_resend_cells` from the cell it was sent to, or once it has
stood idle `slot_idle_ticks` and stands away from it; never within a second
of its last order.

On a march the line lies the band depth (the middle of the type's band with
no defense near, the farthest of the subgroup's types) behind the screen's
front: the screen's unit farthest ahead toward the objective, of those within
8 cells of the screen's centroid, so that a straggler or a part split off
does not move it. With no screen, the front is the subgroup's centroid moved
toward the objective by the band depth, and the units hold where they stand.
The slots take the place of the line's hold on a march.

While the screen holds its line, the line stands where the long-range units
put it, a few cells ahead of their centroid; a slot line measured back from
the screen would take both back, a step at a time. So there the slots lie as
deep as a unit on the approach is sent to otherwise: the middle of the
objective's band short of it, no farther ahead than the line nor the screen,
and with no screen at all unheld; a unit standing in its band of the
objective already stays there. A unit in contact fires, closes in and falls
back as below, whatever the slots.

**Screen before advance.** Long-range units go forward from the staging point
only with a screen worth at least as much as themselves (Allies) or twice as
much (Soviets). Without it they stay at the staging point with main, firing
on what comes into their band.

**Orders.** A long-range unit on its way to a cell is sent to another only
when that one differs by two cells (its slot by the slots' own rule), and is
given at most one order a second: the orders of a unit that moves every cycle
never take effect. It is never sent to a cell in a known enemy defense's
range. Only `move` and `attack` are used. A long-range unit's moves and
attacks are CONTACT orders, the screen's line moves SCREEN orders.

**Air-only defenses.** A defense whose weapon hits aircraft alone (by the
item table's roles, `items.is_air_only`: anti-air and neither anti-infantry
nor anti-armor, as the AA gun and the SAM site) is no defense to any of
this: it narrows no band, pushes back no screen line and closes no cell, even
when the game's rules give it a weapon range.

The values are those of `[siege]` in the tuning file.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import NamedTuple

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.fight import Point, centroid, ground_defenses, strength
from ucla_agent.games.redalert.groups import SIEGE_TYPES
from ucla_agent.games.redalert.items import SOVIET
from ucla_agent.games.redalert.production import INFANTRY_QUEUE
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.targeting import (
    STICKY_COVERAGE,
    assign,
    attack_class,
    dominant_class,
    focus_weight,
    is_in_contact,
    priority_class,
    target_facts,
    targets,
)
from ucla_agent.games.redalert.tuning import SiegeTuning, Tuning
from ucla_agent.games.redalert.world import DEFENSE_TYPES, Cell, EnemyRecord, Unit, World

# The long-range unit that may fall back again and again: it turns in 1 s against a reload of 8.6 s.
KITING_TYPE = "v2rl"
_PERCENT = 100
# The hit points left counted for a known enemy shown at 0%: one shot finishes it.
_MIN_HP_LEFT = 1.0
# The screen line is sought this many cells nearer at a time when a defense reaches the place first chosen.
_LINE_STEP_CELLS = 0.5
# How far past the limit a cell's depth may come out by the rounding of floating-point arithmetic alone.
_DEPTH_TOLERANCE = 1e-9
# The screen's units within this many cells of its centroid are its body; the front leaves the others out.
SCREEN_BODY_CELLS = 8
# A long-range unit within this many cells of its slot stands at it: on the cell, or on one of the eight around it
# while another unit holds the cell.
_AT_SLOT_CELLS = 1.5
# How far around a slot's cell a free one is sought when another slot or the map's edge has taken it.
_SLOT_SEARCH_CELLS = 3


@dataclass(frozen=True)
class Band:
    """The distances from a target between which a long-range unit fires on it, in cells."""

    near: float
    far: float

    @property
    def is_empty(self) -> bool:
        return self.near > self.far

    @property
    def middle(self) -> float:
        return (self.near + self.far) / 2

    def holds(self, distance: float) -> bool:
        """Whether a unit this far from the target is in the band."""
        return self.near <= distance <= self.far


@dataclass(frozen=True)
class Line:
    """Where the screen stands: `depth` cells from the siege subgroup's centroid `origin` along `heading`."""

    origin: Point
    # The unit vector from the centroid toward the objective.
    heading: Point
    cell: Cell

    def depth_of(self, point: Point) -> float:
        """How far ahead of the centroid toward the objective a point lies."""
        return _depth(point, self.origin, self.heading)


@dataclass(frozen=True)
class SlotLine:
    """The line the long-range units out of contact take their slots on: across `heading`, the unit vector of the
    approach toward the objective, through `middle`."""

    middle: Point
    heading: Point


class _Pairing(NamedTuple):
    """A long-range unit's possible target. Pairings sort the most valuable first, then the nearest."""

    rank: float
    distance_squared: float
    target: EnemyRecord
    band: Band
    # The damage over the focus window the unit does to the target.
    damage: float

    @property
    def value(self) -> float:
        return -self.rank


@dataclass(frozen=True)
class _Sent:
    """The last order the siege gave a unit."""

    tick: int
    # The cell of a move, None for an attack.
    cell: Cell | None = None
    # The target of an attack, None for a move.
    target: int | None = None
    is_fallback: bool = False
    # Whether the move was to the unit's slot.
    is_slot: bool = False


def firing_band(unit_type: str, target: Point, world: World, tuning: SiegeTuning) -> Band:
    """The band from which a long-range unit of a type fires on a target, clear of every known enemy defense."""
    open_band = _open_band(unit_type, world, tuning)
    near, far = open_band.near, open_band.far
    for defense, weapon_range in _shooting_defenses(world):
        reach = weapon_range + tuning.band_defense_margin
        apart = math.hypot(defense.x - target[0], defense.y - target[1])
        # Only a defense whose reach meets the band's disc could have a unit of the band in its range.
        if apart < reach + far:
            near = max(near, reach + apart)
    return Band(near, far)


def band_depth(unit_types: Collection[str], world: World, tuning: SiegeTuning) -> float:
    """How far behind the screen's front the slots of long-range units of these types lie on a march: the middle of the
    band with no defense near, the farthest of the types'."""
    return max(_open_band(unit_type, world, tuning).middle for unit_type in unit_types)


def screen_front(screen: list[Unit], origin: Point, heading: Point) -> float:
    """How far ahead of `origin` along `heading` the screen's front stands: its unit farthest that way of those within
    `SCREEN_BODY_CELLS` of its centroid, stragglers left out; the one nearest the centroid when none is that near."""
    middle = centroid(screen)
    body = [unit for unit in screen if cells.is_within((unit.x, unit.y), middle, SCREEN_BODY_CELLS)]
    if not body:
        body = [min(screen, key=lambda unit: ((unit.x - middle[0]) ** 2 + (unit.y - middle[1]) ** 2, unit.id))]
    return max(_depth((unit.x, unit.y), origin, heading) for unit in body)


def march_slot_line(
    siege_units: list[Unit], screen: list[Unit], aim: Point, world: World, tuning: SiegeTuning
) -> SlotLine | None:
    """The slot line of a march on `aim`: the band depth behind the screen's front, across the approach from the
    siege subgroup's centroid. With no screen the front is the centroid moved toward `aim` by the band depth. None
    when the subgroup stands on `aim`."""
    origin = centroid(siege_units)
    heading = _heading(origin, aim)
    if heading is None:
        return None
    depth = band_depth({unit.type for unit in siege_units}, world, tuning)
    front = screen_front(screen, origin, heading) if screen else depth
    return _slot_line_at(origin, heading, front - depth)


def slot_cells(units: list[Unit], line: SlotLine, world: World, tuning: SiegeTuning) -> dict[int, Cell]:
    """Each unit's slot on the line, by unit id: one cell a unit, `slot_spacing_cells` apart along the line and
    centered on its middle, the unit nearest a slot taking it first. No two units are given the same cell."""
    across = (-line.heading[1], line.heading[0])
    middle_index = (len(units) - 1) / 2
    offsets = [(index - middle_index) * tuning.slot_spacing_cells for index in range(len(units))]
    points = [(line.middle[0] + across[0] * offset, line.middle[1] + across[1] * offset) for offset in offsets]
    slots = _distinct_cells(points, world)
    pairs = sorted(
        ((unit.x - slot[0]) ** 2 + (unit.y - slot[1]) ** 2, unit.id, index)
        for unit in units
        for index, slot in enumerate(slots)
        if slot is not None
    )
    chosen: dict[int, Cell] = {}
    taken: set[int] = set()
    for _, unit_id, index in pairs:
        if unit_id not in chosen and index not in taken:
            chosen[unit_id] = slots[index]
            taken.add(index)
    return chosen


def has_screen(siege_units: list[Unit], screen: list[Unit], world: World, tuning: SiegeTuning) -> bool:
    """Whether a screen is worth enough for the siege subgroup to go forward behind it."""
    ratio = tuning.screen_ratio_soviet if world.family == SOVIET else tuning.screen_ratio_allies
    return strength(screen, world) >= ratio * strength(siege_units, world)


def screen_line(siege_units: list[Unit], aim: Point, world: World, tuning: SiegeTuning) -> Line | None:
    """Where the screen stands in front of the siege subgroup, toward `aim`; None when the subgroup is at `aim`.

    `screen_gap` cells ahead of the subgroup's centroid, or nearer where a
    known enemy defense reaches that far, so that the cell is at least
    `screen_defense_margin` outside every defense's range. When no cell from
    the gap back to as far behind the centroid is clear, there is no line.
    """
    origin = centroid(siege_units)
    span = math.hypot(aim[0] - origin[0], aim[1] - origin[1])
    if span == 0:
        return None
    heading = ((aim[0] - origin[0]) / span, (aim[1] - origin[1]) / span)
    gap = max(_screen_gap(unit.type, tuning) for unit in siege_units)
    defenses = _shooting_defenses(world)
    steps = math.floor(2 * gap / _LINE_STEP_CELLS)
    for step in range(steps + 1):
        depth = gap - step * _LINE_STEP_CELLS
        cell = _clamped(cells.nearest_cell(origin[0] + heading[0] * depth, origin[1] + heading[1] * depth), world)
        if all(
            cells.distance(cell, (defense.x, defense.y)) >= weapon_range + tuning.screen_defense_margin
            for defense, weapon_range in defenses
        ):
            return Line(origin, heading, cell)
    return None


class _Advance(NamedTuple):
    """Where the long-range units are moving toward, and the screen's line they may not be sent ahead of."""

    aim: Point
    # None for long-range units with no screen.
    line: Line | None
    # The depth along the line no cell a long-range unit is sent to lies beyond; None with no line.
    limit: float | None


class Siege:
    """The orders of main's siege subgroup and screen, and what it remembers of them. See the module's note."""

    def __init__(self) -> None:
        # Unit id -> the last order given it.
        self._sent: dict[int, _Sent] = {}
        # Unit id -> the id of the enemy it was given in the latest cycle.
        self._targets: dict[int, int] = {}
        # Screen unit id -> the id of the enemy in contact it was given in the latest cycle.
        self._screen_targets: dict[int, int] = {}
        # Screen unit id -> the line cell it was last sent to, while it holds the line.
        self._line_sent: dict[int, Cell] = {}
        # Screen unit id -> the objective it was last sent to attack-move on, while the screen marches.
        self._march_sent: dict[int, Cell] = {}
        # The long-range units that have fallen back since an enemy came close, and that it has not left.
        self._fell_back: set[int] = set()
        # Long-range unit id -> the tick it was first seen idle since it last moved or was sent to its slot.
        self._idle_since: dict[int, int] = {}

    def orders(
        self,
        siege_units: list[Unit],
        screen: list[Unit],
        aim: Point | None,
        may_advance: bool,
        world: World,
        tuning: Tuning,
    ) -> list[Intent]:
        """The orders of main's siege subgroup and screen, advancing or fighting toward `aim`.

        `may_advance` is whether the long-range units may move toward their
        targets at all; they fire and fall back either way. With no `aim` the
        screen holds no line and they do not move toward a target; neither do
        they with a screen that finds no line clear of the enemy's defenses.
        Long-range units with no screen at all move into their band unheld.
        The units out of contact move to their slots, as deep as they would
        be sent toward `aim`, but for those standing in its band already (see
        the module's note).
        """
        self._forget_gone(siege_units)
        self._march_sent = {}
        line = _line_for(siege_units, screen, aim, world, tuning.siege)
        advance = _advance_for(screen, aim, line) if may_advance else None
        slots = None if advance is None else _held_slots(siege_units, advance, world, tuning.siege)
        screen_orders = self._screen_orders(screen, line, world, tuning)
        return [*screen_orders, *self._long_range_orders(siege_units, advance, world, tuning, slots)]

    def march(
        self, siege_units: list[Unit], screen: list[Unit], objective: Cell, world: World, tuning: Tuning
    ) -> list[Intent]:
        """The orders of main's siege subgroup and screen marching on `objective` with no enemy near.

        The screen attack-moves on the objective; the long-range units out of
        contact move to their slots behind the screen's front, and those in
        contact advance as in `orders`, but held back by the line alone.
        """
        self._forget_gone(siege_units)
        self._screen_targets, self._line_sent = {}, {}
        line = _line_for(siege_units, screen, objective, world, tuning.siege)
        advance = _advance_for(screen, objective, line, held_by_screen=False)
        slot_line = None if advance is None else march_slot_line(siege_units, screen, objective, world, tuning.siege)
        slots = None if slot_line is None else _Slots(slot_line, siege_units)
        screen_orders = self._screen_march(screen, objective, tuning)
        return [*screen_orders, *self._long_range_orders(siege_units, advance, world, tuning, slots)]

    def fire_on(self, siege_units: list[Unit], defense: EnemyRecord, world: World, tuning: Tuning) -> list[Intent]:
        """The stand-off fire of long-range units on one defense, with no screen: each attacks it from its band while
        the defense is in sight, or moves into the band, and falls back from an enemy unit come close as ever. A unit
        whose band is empty, closed by the other defenses around, is given nothing."""
        self._forget_gone(siege_units)
        enemies = [record for record in world.enemy_memory.values() if record.visible and is_combat_unit(record)]
        aim = (defense.x, defense.y)
        intents = []
        for unit in siege_units:
            band = firing_band(unit.type, aim, world, tuning.siege)
            pairing = None if band.is_empty or not defense.visible else _pairing_on(unit, defense, band, world, tuning)
            advance = None if band.is_empty else _Advance(aim, None, None)
            intents.append(self._unit_order(unit, pairing, enemies, advance, world, tuning.siege))
        return [intent for intent in intents if intent is not None]

    def _forget_gone(self, siege_units: list[Unit]) -> None:
        """Forget what the long-range units no longer in the subgroup were ordered."""
        present = {unit.id for unit in siege_units}
        self._sent = {unit_id: last for unit_id, last in self._sent.items() if unit_id in present}
        self._idle_since = {unit_id: tick for unit_id, tick in self._idle_since.items() if unit_id in present}
        self._fell_back &= present

    def _screen_march(self, screen: list[Unit], objective: Cell, tuning: Tuning) -> list[Intent]:
        """An `attack_move` on the objective for the screen units not sent there yet, and for those standing idle
        farther from it than `gathered_cells`: their order did not take them there."""
        sent, self._march_sent = self._march_sent, dict.fromkeys((unit.id for unit in screen), objective)
        reach = tuning.combat.gathered_cells
        going = [
            unit.id
            for unit in screen
            if sent.get(unit.id) != objective or (unit.idle and not cells.is_within((unit.x, unit.y), objective, reach))
        ]
        order = {"cmd": "attack_move", "units": going, "to": list(objective)}
        return [Intent(Priority.MOVE, order)] if going else []

    def _long_range_orders(
        self,
        siege_units: list[Unit],
        advance: _Advance | None,
        world: World,
        tuning: Tuning,
        slots: _Slots | None = None,
    ) -> list[Intent]:
        """Each long-range unit's order of the cycle, for those that get one: those of `slots` out of contact take their
        slots on its line."""
        self._note_idle(siege_units, world.tick)
        chosen = self._assign(siege_units, world, tuning)
        enemies = [record for record in world.enemy_memory.values() if record.visible and is_combat_unit(record)]
        taken = None if slots is None else _slots_out_of_contact(slots, enemies, world, tuning.siege)
        intents = (
            self._unit_order(unit, chosen.get(unit.id), enemies, advance, world, tuning.siege, taken)
            for unit in siege_units
        )
        return [intent for intent in intents if intent is not None]

    def _note_idle(self, siege_units: list[Unit], tick: int) -> None:
        """Note since when each long-range unit has stood idle; a unit on the move has not."""
        for unit in siege_units:
            if unit.idle:
                self._idle_since.setdefault(unit.id, tick)
            else:
                self._idle_since.pop(unit.id, None)

    def _unit_order(
        self,
        unit: Unit,
        pairing: _Pairing | None,
        enemies: list[EnemyRecord],
        advance: _Advance | None,
        world: World,
        tuning: SiegeTuning,
        slots: Mapping[int, Cell | None] | None = None,
    ) -> Intent | None:
        """A long-range unit's order: fall back, move to its slot when it is one of `slots`, the units out of contact
        (None for one given no free cell), fire from its band, or move into it; None to go on as it is."""
        if self._is_falling_back(unit):
            # A fall-back cut short leaves the unit as close to the enemy as it was.
            return None
        closing = closing_in(unit, enemies, world, tuning)
        if closing and self._may_fall_back(unit, closing, world):
            return self._fall_back(unit, closing, world, tuning)
        if not closing:
            self._fell_back.discard(unit.id)
        if slots is not None and unit.id in slots:
            return self._to_slot(unit, slots[unit.id], world, tuning)
        return self._fire_or_close_in(unit, pairing, advance, world, tuning)

    def _to_slot(self, unit: Unit, slot: Cell | None, world: World, tuning: SiegeTuning) -> Intent | None:
        """A move straight to a long-range unit's slot, when it may be sent there (see `_may_send_to_slot`)."""
        if not _is_open(slot, unit, world) or not self._may_send_to_slot(unit, slot, world, tuning):
            return None
        self._sent[unit.id] = _Sent(world.tick, cell=slot, is_slot=True)
        self._idle_since.pop(unit.id, None)
        return Intent(Priority.CONTACT, {"cmd": "move", "units": [unit.id], "to": list(slot)})

    def _may_send_to_slot(self, unit: Unit, slot: Cell, world: World, tuning: SiegeTuning) -> bool:
        """Whether a long-range unit may be sent to its slot: a second after its last order, when that was no move to
        its slot, the slot has moved `slot_resend_cells` from the cell sent, or the unit has stood idle
        `slot_idle_ticks` and stands away from it."""
        last = self._sent.get(unit.id)
        if last is None:
            return True
        if world.tick - last.tick < tuning.reissue_ticks:
            return False
        if not last.is_slot or cells.distance(slot, last.cell) >= tuning.slot_resend_cells:
            return True
        idle_since = self._idle_since.get(unit.id)
        has_stood = idle_since is not None and world.tick - idle_since >= tuning.slot_idle_ticks
        return has_stood and not cells.is_within((unit.x, unit.y), slot, _AT_SLOT_CELLS)

    def _is_falling_back(self, unit: Unit) -> bool:
        """Whether a long-range unit is still on the way of its last order, a fall-back."""
        last = self._sent.get(unit.id)
        return last is not None and last.is_fallback and not unit.idle

    def _fire_or_close_in(
        self, unit: Unit, pairing: _Pairing | None, advance: _Advance | None, world: World, tuning: SiegeTuning
    ) -> Intent | None:
        """An attack on the unit's target when it stands in the band, else a move into the band while it may."""
        if pairing is not None and pairing.band.holds(math.sqrt(pairing.distance_squared)):
            return self._attack(unit, pairing.target, world, tuning)
        return None if advance is None else self._into_band(unit, pairing, advance, world, tuning)

    def _may_fall_back(self, unit: Unit, closing: list[EnemyRecord], world: World) -> bool:
        """Whether a unit with enemies close falls back: once, or a V2 again from infantry once it stands and fires."""
        if unit.id not in self._fell_back:
            return True
        return unit.type == KITING_TYPE and unit.idle and all(_is_infantry(record, world) for record in closing)

    def _fall_back(self, unit: Unit, closing: list[EnemyRecord], world: World, tuning: SiegeTuning) -> Intent | None:
        intent = self._move(unit, fallback_cell(unit, closing, world, tuning), world, tuning, is_fallback=True)
        if intent is not None:
            self._fell_back.add(unit.id)
        return intent

    def _into_band(
        self, unit: Unit, pairing: _Pairing | None, advance: _Advance, world: World, tuning: SiegeTuning
    ) -> Intent | None:
        """A move into the band of the unit's target, or of main's objective while it has none in sight.

        Against an objective whose band is empty the unit goes straight for
        it, as far as the screen lets it: the defense that empties the band is
        then soon in sight, a target with a band of its own.
        """
        if pairing is not None:
            goal, band = (pairing.target.x, pairing.target.y), pairing.band
        else:
            goal, band = advance.aim, firing_band(unit.type, advance.aim, world, tuning)
        if _stands_in(band, unit, goal):
            return None
        radius = 0.0 if band.is_empty else band.middle
        return self._move(unit, _band_cell(unit, goal, radius, advance, world), world, tuning)

    def _move(
        self, unit: Unit, cell: Cell | None, world: World, tuning: SiegeTuning, is_fallback: bool = False
    ) -> Intent | None:
        """A move for a long-range unit, within the reissue limits and to no cell in a known defense's range."""
        if not _is_open(cell, unit, world) or not _may_move_again(unit, cell, self._sent.get(unit.id), world, tuning):
            return None
        self._sent[unit.id] = _Sent(world.tick, cell=cell, is_fallback=is_fallback)
        return Intent(Priority.CONTACT, {"cmd": "move", "units": [unit.id], "to": list(cell)})

    def _attack(self, unit: Unit, target: EnemyRecord, world: World, tuning: SiegeTuning) -> Intent | None:
        """An attack for a long-range unit in its band, unless it is busy with the same one or was ordered lately."""
        last = self._sent.get(unit.id)
        if last is not None:
            is_recent = world.tick - last.tick < tuning.reissue_ticks
            if is_recent or (last.target == target.id and not unit.idle):
                return None
        self._sent[unit.id] = _Sent(world.tick, target=target.id)
        return Intent(Priority.CONTACT, {"cmd": "attack", "units": [unit.id], "target": target.id})

    def _assign(self, siege_units: list[Unit], world: World, tuning: Tuning) -> dict[int, _Pairing]:
        """Each long-range unit's target among the enemies in sight around the subgroup, by unit id.

        The focus-fire assignment's rule, with the band in place of the weapon
        range: the units whose best pairing is worth the most go first, each to
        its best target not yet covered, keeping the one it had while that is
        not covered twice over and no other is worth `retarget_gain` times as
        much.
        """
        combat = tuning.combat
        middle = centroid(siege_units)
        ranked = [
            record for record in targets(siege_units, middle, world, tuning, around_middle=True) if record.visible
        ]
        group_class = dominant_class(siege_units, world)
        bands = {
            unit_type: {
                record.id: firing_band(unit_type, (record.x, record.y), world, tuning.siege) for record in ranked
            }
            for unit_type in {unit.type for unit in siege_units}
        }
        walk = []
        for unit in siege_units:
            possible = _pairings(unit, ranked, bands[unit.type], group_class, world, tuning)
            if possible:
                walk.append((possible[0].rank, unit.id, possible))
        walk.sort(key=lambda entry: entry[:2])
        needed = {record.id: combat.overkill_factor * _hp_left(record, world, tuning) for record in ranked}
        given = dict.fromkeys(needed, 0.0)
        chosen: dict[int, _Pairing] = {}
        for _, unit_id, possible in walk:
            pick = _choose(possible, self._targets.get(unit_id), given, needed, combat.retarget_gain)
            given[pick.target.id] += pick.damage
            chosen[unit_id] = pick
        self._targets = {unit_id: pairing.target.id for unit_id, pairing in chosen.items()}
        return chosen

    def _screen_orders(self, screen: list[Unit], line: Line | None, world: World, tuning: Tuning) -> list[Intent]:
        """The screen's orders: attacks for the units in contact with an enemy unit, a move to the line for the rest.

        A unit holding the line is sent to it when the line has moved since,
        or when it stands idle farther from it than `gathered_cells`: its
        order did not take it there.
        """
        attacks = self._screen_attacks(screen, world, tuning)
        holding = [unit for unit in screen if unit.id not in self._screen_targets]
        line_sent, self._line_sent = self._line_sent, {}
        if line is None:
            return attacks
        self._line_sent = {unit.id: line.cell for unit in holding}
        reach = tuning.combat.gathered_cells
        # A unit is sent to a line once: those it reached stand spread around its cell, idle, and are left so.
        going = [
            unit.id
            for unit in holding
            if line_sent.get(unit.id) != line.cell
            or (unit.idle and not cells.is_within((unit.x, unit.y), line.cell, reach))
        ]
        moves = [Intent(Priority.SCREEN, {"cmd": "move", "units": going, "to": list(line.cell)})] if going else []
        return [*attacks, *moves]

    def _screen_attacks(self, screen: list[Unit], world: World, tuning: Tuning) -> list[Intent]:
        """Attacks for the screen's units in contact with an enemy unit, handed out over the enemy units in contact."""
        units = [record for record in world.enemy_memory.values() if record.visible and not record.is_building]
        engaged = [unit for unit in screen if is_in_contact(unit, units, world)]
        current, self._screen_targets = self._screen_targets, {}
        if not engaged:
            return []
        near = {record.id for record in units if any(is_in_contact(unit, (record,), world) for unit in engaged)}
        ranked = [record for record in targets(engaged, centroid(engaged), world, tuning) if record.id in near]
        by_id = {unit.id: unit for unit in engaged}
        sent = []
        for assignment in assign(engaged, ranked, world, tuning, current):
            self._screen_targets.update(dict.fromkeys(assignment.attackers, assignment.target.id))
            order = {"cmd": "attack", "units": list(assignment.attackers), "target": assignment.target.id}
            attackers = [by_id[unit_id] for unit_id in assignment.attackers]
            sent.append(Intent(attack_class(attackers, ranked, world), order))
        return sent


def _line_for(
    siege_units: list[Unit], screen: list[Unit], aim: Point | None, world: World, tuning: SiegeTuning
) -> Line | None:
    """The screen's line toward `aim`; None with no aim or no screen to hold it."""
    return None if aim is None or not screen else screen_line(siege_units, aim, world, tuning)


def _advance_for(
    screen: list[Unit], aim: Point | None, line: Line | None, held_by_screen: bool = True
) -> _Advance | None:
    """How long-range units free to advance move toward `aim`: held behind the line, and behind the screen too unless
    `held_by_screen` is false, or unheld with no screen at all; None with no aim, or with a screen that holds no
    line."""
    if aim is None or (screen and line is None):
        return None
    if line is None:
        return _Advance(aim, None, None)
    limit = line.depth_of(line.cell)
    if held_by_screen:
        limit = min(limit, line.depth_of(centroid(screen)))
    return _Advance(aim, line, limit)


class _Slots(NamedTuple):
    """The line long-range units out of contact take their slots on in a cycle, and the units that may take one."""

    line: SlotLine
    units: list[Unit]


def _held_slots(siege_units: list[Unit], advance: _Advance, world: World, tuning: SiegeTuning) -> _Slots | None:
    """The slots while the screen holds its line, for the long-range units not yet in their band of the aim, which
    stay where they are. The line lies as deep along the approach as a unit on it is sent toward the aim: the middle of
    the aim's band short of it (the aim itself when that band is empty; of the subgroup's types, the one whose middle
    lies farthest), held back to the advance's limit. None when the subgroup stands on the aim."""
    aim = advance.aim
    origin = centroid(siege_units)
    heading = _heading(origin, aim)
    if heading is None:
        return None
    bands = {unit_type: firing_band(unit_type, aim, world, tuning) for unit_type in {unit.type for unit in siege_units}}
    radius = max(0.0 if band.is_empty else band.middle for band in bands.values())
    depth = math.hypot(aim[0] - origin[0], aim[1] - origin[1]) - radius
    if advance.limit is not None:
        depth = min(depth, advance.limit)
    going = [unit for unit in siege_units if not _stands_in(bands[unit.type], unit, aim)]
    return _Slots(_slot_line_at(origin, heading, depth), going)


def _stands_in(band: Band, unit: Unit, aim: Point) -> bool:
    """Whether a unit stands in its band of an aim that band is not empty for."""
    return not band.is_empty and band.holds(math.hypot(unit.x - aim[0], unit.y - aim[1]))


def _slots_out_of_contact(
    slots: _Slots, enemies: list[EnemyRecord], world: World, tuning: SiegeTuning
) -> dict[int, Cell | None]:
    """The slots of the units of `slots` out of contact, by unit id: no enemy unit in sight nor known defense within
    their weapon range and two cells. A unit given no free cell has None."""
    near = [*enemies, *(defense for defense, _ in _shooting_defenses(world))]
    free = [unit for unit in slots.units if not is_in_contact(unit, near, world)]
    chosen = slot_cells(free, slots.line, world, tuning)
    return {unit.id: chosen.get(unit.id) for unit in free}


def _slot_line_at(origin: Point, heading: Point, depth: float) -> SlotLine:
    """The slot line `depth` cells ahead of `origin` along `heading`."""
    return SlotLine((origin[0] + heading[0] * depth, origin[1] + heading[1] * depth), heading)


def _heading(origin: Point, aim: Point) -> Point | None:
    """The unit vector from `origin` toward `aim`; None when the two are one point."""
    span = math.hypot(aim[0] - origin[0], aim[1] - origin[1])
    if span == 0:
        return None
    return (aim[0] - origin[0]) / span, (aim[1] - origin[1]) / span


def _depth(point: Point, origin: Point, heading: Point) -> float:
    """How far ahead of `origin` along `heading` a point lies."""
    return (point[0] - origin[0]) * heading[0] + (point[1] - origin[1]) * heading[1]


def _distinct_cells(points: list[Point], world: World) -> list[Cell | None]:
    """The cell each point lies nearest to on the map, each a different one: a point whose cell an earlier one took
    gets the nearest free cell around it, or None when none is near."""
    taken: set[Cell] = set()
    chosen: list[Cell | None] = []
    for point in points:
        cell = _clamped(cells.nearest_cell(*point), world)
        if cell in taken:
            cell = _free_cell_near(cell, taken, world)
        if cell is not None:
            taken.add(cell)
        chosen.append(cell)
    return chosen


def _free_cell_near(cell: Cell, taken: set[Cell], world: World) -> Cell | None:
    """The cell on the map nearest to `cell`, within `_SLOT_SEARCH_CELLS` of it, that is not taken; None for none."""
    reach = _SLOT_SEARCH_CELLS
    around = sorted(
        (column * column + row * row, column, row)
        for column in range(-reach, reach + 1)
        for row in range(-reach, reach + 1)
        if 0 < column * column + row * row <= reach * reach
    )
    for _, column, row in around:
        candidate = (cell[0] + column, cell[1] + row)
        if candidate not in taken and _clamped(candidate, world) == candidate:
            return candidate
    return None


def _is_open(cell: Cell | None, unit: Unit, world: World) -> bool:
    """Whether a cell is one a long-range unit may be sent to: not its own, on the map, out of every known defense's
    range."""
    if cell is None or cell == (unit.x, unit.y):
        return False
    return world.in_bounds(*cell) and not _in_defense_range(cell, world)


def _may_move_again(unit: Unit, cell: Cell, last: _Sent | None, world: World, tuning: SiegeTuning) -> bool:
    """Whether a long-range unit may be sent to a cell: a second after its last order, and while it is still on its
    way to the cell of a move, only to a cell `reissue_cells` from that one."""
    if last is None:
        return True
    if world.tick - last.tick < tuning.reissue_ticks:
        return False
    is_on_its_way = last.cell is not None and not unit.idle
    return not is_on_its_way or cells.distance(cell, last.cell) >= tuning.reissue_cells


def _choose(
    possible: list[_Pairing], current: int | None, given: dict[int, float], needed: dict[int, float], gain: float
) -> _Pairing:
    """A unit's target: its best one not yet covered, else its best; but the one it had while that still holds."""
    free = next((pairing for pairing in possible if given[pairing.target.id] < needed[pairing.target.id]), possible[0])
    for pairing in possible:
        target_id = pairing.target.id
        if target_id == current and given[target_id] <= STICKY_COVERAGE * needed[target_id]:
            return pairing if free.value < gain * pairing.value else free
    return free


def _pairings(
    unit: Unit, ranked: list[EnemyRecord], bands: dict[int, Band], group_class: str, world: World, tuning: Tuning
) -> list[_Pairing]:
    """A long-range unit's possible targets, the one it prefers first: those it hurts whose band is not empty.

    A pairing is worth the damage a second the unit does to the target, as
    the focus-fire assignment counts it (none to an aircraft, which only an
    anti-air weapon hits), over the hit points it has left, times the
    target's focus weight (`defense_priority_weight` for a defense), and
    `out_of_range_factor` of that while the unit stands outside the band.
    """
    combat = tuning.combat
    damage = world.items.damage.get(unit.type)
    possible = []
    for target in ranked:
        dps = damage.dps_to(target_facts(target.type, world, combat))
        band = bands[target.id]
        if dps <= 0 or band.is_empty:
            continue
        distance_squared = (target.x - unit.x) ** 2 + (target.y - unit.y) ** 2
        factor = 1.0 if band.holds(math.sqrt(distance_squared)) else combat.out_of_range_factor
        value = dps * _weight(target, group_class, world, tuning) / _hp_left(target, world, tuning) * factor
        possible.append(_Pairing(-value, distance_squared, target, band, dps * combat.focus_window_seconds))
    possible.sort(key=lambda pairing: (pairing.rank, pairing.distance_squared, pairing.target.id))
    return possible


def _pairing_on(unit: Unit, defense: EnemyRecord, band: Band, world: World, tuning: Tuning) -> _Pairing:
    """A long-range unit's pairing with the one defense it is to fire on, from where it stands."""
    dps = world.items.damage.get(unit.type).dps_to(target_facts(defense.type, world, tuning.combat))
    distance_squared = (defense.x - unit.x) ** 2 + (defense.y - unit.y) ** 2
    return _Pairing(-dps, distance_squared, defense, band, dps * tuning.combat.focus_window_seconds)


def _weight(target: EnemyRecord, group_class: str, world: World, tuning: Tuning) -> float:
    """The focus weight of a target to a long-range unit: `defense_priority_weight` for a defense, else its class's."""
    if _base_type(target.type, world) in DEFENSE_TYPES:
        return tuning.siege.defense_priority_weight
    return focus_weight(priority_class(target, group_class, world, tuning), tuning.combat)


def _band_cell(unit: Unit, goal: Point, radius: float, advance: _Advance, world: World) -> Cell | None:
    """The cell `radius` from `goal` nearest to the unit, held back so that it lies no farther ahead than the limit.

    None for a unit standing on the goal itself.
    """
    away_x, away_y = unit.x - goal[0], unit.y - goal[1]
    distance = math.hypot(away_x, away_y)
    if distance == 0:
        return None
    x, y = goal[0] + away_x / distance * radius, goal[1] + away_y / distance * radius
    line, limit = advance.line, advance.limit
    if line is None:
        return _clamped(cells.nearest_cell(x, y), world)
    beyond = line.depth_of((x, y)) - limit
    if beyond > 0:
        x, y = x - line.heading[0] * beyond, y - line.heading[1] * beyond
    # Of the cells around the point, the nearest that is not ahead of the limit: one of them never is.
    corners = {(column, row) for column in (math.floor(x), math.ceil(x)) for row in (math.floor(y), math.ceil(y))}
    behind = [corner for corner in corners if line.depth_of(corner) <= limit + _DEPTH_TOLERANCE]
    cell = min(behind, key=lambda corner: ((corner[0] - x) ** 2 + (corner[1] - y) ** 2, corner))
    return _clamped(cell, world)


def closing_in(unit: Unit, enemies: Iterable[EnemyRecord], world: World, tuning: SiegeTuning) -> list[EnemyRecord]:
    """The enemies within a long-range unit's minimum range and `fallback_margin`: those it falls back from."""
    reach = world.items.damage.get(unit.type).min_range + tuning.fallback_margin
    return [record for record in enemies if cells.is_within((unit.x, unit.y), (record.x, record.y), reach)]


def fall_backs(units: Iterable[Unit], enemies: list[EnemyRecord], world: World, tuning: SiegeTuning) -> dict[int, Cell]:
    """Each long-range unit (artillery, V2) among `units` that one of `enemies` has come within its minimum range and
    `fallback_margin` of, by id, with the cell it falls back to (see `fallback_cell`)."""
    stepping = {}
    for unit in units:
        closing = closing_in(unit, enemies, world, tuning) if unit.type in SIEGE_TYPES else []
        if closing:
            stepping[unit.id] = fallback_cell(unit, closing, world, tuning)
    return stepping


def fallback_cell(unit: Unit, closing: list[EnemyRecord], world: World, tuning: SiegeTuning) -> Cell:
    """`fallback_step` cells toward the base; straight away from the nearest enemy when the base is not known."""
    here = (unit.x, unit.y)
    if world.base_center is not None and world.base_center != here:
        return _clamped(cells.toward(here, world.base_center, tuning.fallback_step), world)
    nearest = min(closing, key=lambda record: cells.distance(here, (record.x, record.y)))
    return _clamped(cells.toward(here, (nearest.x, nearest.y), -tuning.fallback_step), world)


def _shooting_defenses(world: World) -> list[tuple[EnemyRecord, float]]:
    """The known enemy defenses that fire at ground units, each with its weapon range. An air-only defense is left
    out even when the game's rules give it a weapon range: that weapon reaches only aircraft."""
    known = ((defense, world.items.damage.get(defense.type).range) for defense in ground_defenses(world))
    return [(defense, weapon_range) for defense, weapon_range in known if weapon_range > 0]


def _in_defense_range(cell: Cell, world: World) -> bool:
    """Whether a cell is inside the weapon range of a known enemy defense."""
    return any(
        cells.distance(cell, (defense.x, defense.y)) < weapon_range
        for defense, weapon_range in _shooting_defenses(world)
    )


def _open_band(unit_type: str, world: World, tuning: SiegeTuning) -> Band:
    """The band of a long-range unit of a type with no defense near the target."""
    damage = world.items.damage.get(unit_type)
    return Band(damage.min_range + tuning.band_min_range_margin, damage.range - tuning.band_inner_margin)


def _screen_gap(unit_type: str, tuning: SiegeTuning) -> int:
    """How far ahead of long-range units of a type their screen stands."""
    return tuning.screen_gap_v2rl if unit_type == KITING_TYPE else tuning.screen_gap_arty


def _is_infantry(record: EnemyRecord, world: World) -> bool:
    facts = world.items.get(record.type)
    return facts is not None and facts.queue == INFANTRY_QUEUE


def _hp_left(record: EnemyRecord, world: World, tuning: Tuning) -> float:
    """An enemy's hit points left, as the focus-fire assignment counts them: at least 1, `unknown_target_hp` when
    its type has no known hit points."""
    facts = world.items.get(record.type)
    if facts is None or facts.hp is None:
        return float(tuning.combat.unknown_target_hp)
    return max(facts.hp * record.hp / _PERCENT, _MIN_HP_LEFT)


def _base_type(name: str, world: World) -> str:
    """The type an enemy's type is a variant of, or the type itself."""
    facts = world.items.get(name)
    return name if facts is None else facts.name


def _clamped(cell: Cell, world: World) -> Cell:
    """The cell of the playable map nearest to a cell, while the map's bounds are known."""
    return cell if world.map_bounds is None else cells.clamped(cell, world.map_bounds)
