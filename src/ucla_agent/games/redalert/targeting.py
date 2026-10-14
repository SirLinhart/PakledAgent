"""Which enemy each unit of an engaged group shoots at.

Each unit of an engaged group is given the enemy it hurts most, so that a
rocket soldier fires at a tank and a rifle at infantry, and the group's fire
is spread so that no enemy gets more than it takes to kill it while another
the unit could fight gets nothing.

The candidates are the enemies within 12 cells of the group's centroid that
can be shot at: buildings, and units that are neither stale nor a support
power's; a manager may narrow them further, as the forward hold does (see
`forward_hold`). With `contact_focus` of `[combat]` on, the 12 cells around
the centroid are not used: a unit's candidates are the enemies within the
range of its weapon that hits them and `focus_unit_reach_cells` more of the
unit itself, and the group's are those of all its units, so that a unit in a
fight is given a target there however far its group's centroid stands from
it. Only those in sight can be attacked. A unit and an enemy in sight
make a pairing worth the damage the unit does a second to the enemy's armour
(the damage table), divided by the hit points the enemy has left, times the
focus weight of the enemy's class; and half that when the enemy is beyond the
unit's weapon range, so that a unit prefers what it can hit already to an
enemy across the fight, which it would walk to under fire without firing. With
`contact_focus` on, a pairing with a ground enemy is worth that times the
unit's damage to the enemy's armour over its mean damage across the five
armour classes, raised to `counter_power`: a rocket soldier, whose rockets do
nearly twice their mean to a tank and under a fifth of it to a rifle, takes
the tank, though the rifle would die sooner; with `counter_power` 2 a medium
tank takes a full heavy tank over a half-dead rocket soldier. Of pairings
worth the same, the closer enemy is preferred. A unit that cannot hurt an
enemy, or has it inside its minimum range, is not paired with it at all; nor
with a ground enemy whose armour it does less than `focus_min_effectiveness`
of its mean damage across the armour classes to: a rifle never fires on a
heavy tank.

Only an anti-air weapon hits an aircraft, whatever armour it wears: against
an enemy the damage table marks as an aircraft, a unit's damage is that of
its anti-air weapon and its range that weapon's range. A unit without one,
such as a rifle or a tank, is never paired with an aircraft: it keeps to the
ground targets it can hurt.

The classes, the most urgent first: an enemy defense whose weapon hurts one
of the group's units (by the damage table: an anti-aircraft gun or a SAM site
only aircraft) while one of them stands within its weapon range and
`defense_threat_margin_cells`, as a Tesla coil firing on the group does, so
that the group kills what kills it before it shoots a power plant; an enemy
unit that can hurt what the
group is mostly made of, infantry or vehicles, and is nearly dead; the
harvesters, which are the enemy's income; the other enemy units that fight,
and the armed defenses out of the group's reach; the buildings that make the enemy's economy and army (refineries, power,
barracks, war factory, construction yard, in that order); everything else;
and the aircraft that carry no weapon, which hurt nothing and are taken only
when no other target is in reach. The weights come from `[combat]` of the
tuning file. With its `air_targeting` off, an aircraft is weighed as an enemy
the damage table does not list, in the class of any other unit.

The units are handed out by class: the vehicles other than artillery and V2
first, so that the tanks pick their targets before anything else, then the
artillery and V2, then the infantry; within a class greedily, the unit whose
best pairing is worth the most first. Each is given the most valuable of its
targets that is not yet covered: a target is covered once the units given it
would do, in `focus_window_seconds`, `overkill_factor` times the hit points it
has left, so that a full heavy tank takes about five medium tanks and the rest
go on to the next. More would be wasted on an enemy about to die anyway. A unit whose targets are all covered
joins the one it is worth the most against. With `contact_focus` on, a unit
whose best target is covered moves to another only when that one is worth at
least `spill_floor` (a quarter) of its best, and joins its best otherwise: a
rifle whose enemy rifle is covered is of more use firing on it as well than
on a harvester it hardly scratches.

A unit keeps the target it was given in the cycle before, while that target
is in sight and not covered twice over, unless another target is worth
`retarget_gain` times as much to it: a unit given a new target in every cycle
never fires at any of them. For `focus_min_hold_seconds` after it was first
given it, a unit keeps its target whatever else is worth more, while the
target is in sight and within the unit's weapon range: a unit that turns its
gun every second fires at nothing. What each unit was given, and since when,
is one memory for the match (`TargetMemory`, `MatchContext.target_memory`),
shared by main's focus fire, its fire while it holds and base defense's, so
that a unit passed from one manager to another keeps its target.

Artillery and V2 (`groups.SIEGE_TYPES`) fire from where they stand: they
are paired only with the targets within their weapon range, at no lowered
value beyond it, and never sent after an enemy. They are made to fire on
infantry in groups: a pairing with an enemy infantry unit is worth 1 more
time for each other enemy infantry unit within `arty_splash_cells` of it,
which their shells hit as well. While a crush run is on (the combat
manager's, or the commander's run-over detachment), the tanks run the
infantry over and the artillery and V2 take the enemy vehicles first: a
stopped one (`EnemyRecord.still`, standing where it stood in the state
before) before one on the move, then the infantry, then the rest.

One attack is ordered for each target, with all the units given it. The
units that can hurt no enemy in sight move with `attack_move` on the first
target given out that is not an aircraft, fighting whatever they meet; with
none given out, or no enemy in sight at all, they move so on the last known
cell of the ground enemy whose class matters most. Never on an aircraft's
cell: they would follow planes they cannot hit, away from the ground fight.
While only aircraft are in reach, they are given no order. A manager may ask
for no such move at all (`chase`), as combat does under a commanded action,
whose own order those units keep; and for fire on the targets already within
the units' weapon range only (`in_range_only`), as for a group that holds
where it stands.

With `tank_charge_artillery` of `[combat]` on, a unit given an enemy unit
that has a minimum range (an artillery piece, a V2) is not given the attack
while it is one of our vehicles other than artillery and V2 and stands
farther than that minimum range less one cell from it: it is given a `move`
onto the target's cell instead, so that it drives in without stopping at its
own weapon range, where it would be a still target for the shells, and it
attacks once inside, where the enemy's weapon cannot fire. The move is sent
again only when the target has moved more than CHARGE_RECAST_CELLS since the
last one, and it counts as engaging the target (`engaged`). What each unit is
given is unchanged; only the order.

An attack is a CONTACT order when one of its units has an enemy within its
weapon range and two cells, an aircraft within its anti-air range where that
is longer: that unit is in the fight, or a step from it. Otherwise the
attack, like every `attack_move`, only brings units closer, and is a MOVE
order.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Set
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Mapping, NamedTuple

from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.family import (
    CONSTRUCTION_YARD,
    HARVESTER,
    POWER_PLANT,
    REFINERY,
    WAR_FACTORY,
)
from ucla_agent.games.redalert.fight import ANTI_INFANTRY, ANTI_VEHICLE, Point, is_near, worth
from ucla_agent.games.redalert.groups import SIEGE_TYPES
from ucla_agent.games.redalert.items import ARMOUR_CLASSES, NO_DAMAGE, TICKS_PER_SECOND, Damage
from ucla_agent.games.redalert.production import INFANTRY_QUEUE, VEHICLE_QUEUE
from ucla_agent.games.redalert.scout_report import SUPPORT_POWER_ACTORS, is_combat_unit
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

_PERCENT = 100
# The buildings that make the enemy's economy and army, the one that matters most first.
CORE_BUILDINGS = (REFINERY, POWER_PLANT, "tent", "barr", WAR_FACTORY, CONSTRUCTION_YARD)
# The priority classes, the most urgent first.
THREAT_DEFENSE, WEAK_THREAT, HARVESTERS, FIGHTERS, CORE, OTHER, HARMLESS_AIR = range(7)
# A unit is in contact with an enemy within its weapon range and this many cells more.
CONTACT_MARGIN_CELLS = 2
# A unit keeps its target until the units given it would do this many times what covers it.
STICKY_COVERAGE = 2
# The hit points left counted for a known enemy shown at 0%: one shot finishes it.
_MIN_HP_LEFT = 1.0
# The order the units of a group are handed out in, by class: vehicles, then artillery and V2, then infantry.
_VEHICLES_FIRST, _LONG_RANGE_NEXT, _INFANTRY_LAST = range(3)
# The tiers of the targets of an artillery piece or a V2 while a crush run is on, the one it takes first 0.
_STOPPED_VEHICLE_TIER, _MOVING_VEHICLE_TIER, _INFANTRY_TIER, _OTHER_TIER = range(4)
# A vehicle charging an enemy artillery piece or V2 is sent to its cell again only once it has moved farther than this.
CHARGE_RECAST_CELLS = 2


@dataclass(frozen=True)
class Assignment:
    """The units given one enemy to attack."""

    target: EnemyRecord
    attackers: tuple[int, ...]


@dataclass(eq=False)
class _Target:
    """An enemy in sight the group may attack, as the assignment weighs it, and the units given it so far."""

    record: EnemyRecord
    # Its damage facts as the assignment weighs them: the armour it wears and whether it is an aircraft.
    facts: Damage
    # What a pairing with it is worth for each hit point a second done to it: its focus weight over its hit points left.
    worth_per_dps: float
    # The damage over the focus window at which it is covered.
    needed: float
    # Whether the item table gives the hit points of its type; without them it is counted at unknown_target_hp.
    hp_known: bool
    # The damage over the focus window of the units given it so far.
    given: float = 0.0
    attackers: list[int] = field(default_factory=list)

    @property
    def is_covered(self) -> bool:
        return self.given >= self.needed


class _Pairing(NamedTuple):
    """One of a unit's possible targets. Pairings sort in the order a unit prefers them: by tier, by value, then the
    closer."""

    # The tier of the target in the unit's order of preference, the first 0: always 0 but for an artillery piece or a V2
    # while a crush run is on (see `_crush_tier`).
    tier: int
    # Minus the value of the pairing, so that the most valuable sorts first.
    rank: float
    distance_squared: float
    # The target's place among the enemies in sight, which settles the order of pairings alike in all else.
    place: int
    target: _Target
    # The damage over the focus window the unit does to the target.
    damage: float
    # Whether the target stands within the range of the unit's weapon that hits it.
    in_range: bool

    @property
    def value(self) -> float:
        return -self.rank


# A target a unit of a type can hurt: (value in range, place, target, damage in the window, weapon range squared, reach
# squared within which it is a candidate, tier).
_Hurt = tuple[float, int, _Target, float, float, float, int]


def dominant_class(units: list[Unit], world: World) -> str:
    """What a group is mostly worth: infantry or vehicles, as the queue that makes them; vehicles on a tie."""
    worth_by_queue: Counter[str] = Counter()
    for unit in units:
        facts = world.items.get(unit.type)
        if facts is not None and facts.queue in (INFANTRY_QUEUE, VEHICLE_QUEUE):
            worth_by_queue[facts.queue] += worth(unit.type, unit.hp, world)
    return INFANTRY_QUEUE if worth_by_queue[INFANTRY_QUEUE] > worth_by_queue[VEHICLE_QUEUE] else VEHICLE_QUEUE


def priority_class(
    record: EnemyRecord, group_class: str, world: World, tuning: Tuning, units: Iterable[Unit] = ()
) -> int:
    """How much killing an enemy matters to a group mostly of `group_class`, of `units`: 0 is the most, 6 the least.
    A building is weighed as an armed defense only against the `units` given."""
    base = _base_type(record.type, world)
    if record.is_building:
        defense = _defense_class(record, units, world, tuning.combat)
        if defense is not None:
            return defense
        return CORE if base in CORE_BUILDINGS else OTHER
    if _is_harmless_air(record.type, world, tuning.combat):
        return HARMLESS_AIR
    hurts = ANTI_VEHICLE if group_class == VEHICLE_QUEUE else ANTI_INFANTRY
    if base in hurts and record.hp <= tuning.combat.weak_hp:
        return WEAK_THREAT
    if base == HARVESTER:
        return HARVESTERS
    return FIGHTERS if is_combat_unit(record) else OTHER


def focus_weight(rank: int, tuning: CombatTuning) -> float:
    """The weight a pairing with an enemy of a priority class is worth."""
    weights = {
        THREAT_DEFENSE: tuning.focus_weight_threat_defense,
        WEAK_THREAT: tuning.focus_weight_weak_threat,
        HARVESTERS: tuning.focus_weight_harvester,
        FIGHTERS: tuning.focus_weight_fighter,
        CORE: tuning.focus_weight_core,
        OTHER: tuning.focus_weight_other,
        HARMLESS_AIR: tuning.focus_weight_harmless_air,
    }
    return weights[rank]


def targets(
    units: list[Unit],
    middle: Point,
    world: World,
    tuning: Tuning,
    admit: Callable[[EnemyRecord], bool] | None = None,
    in_range_only: bool = False,
    around_middle: bool = False,
) -> list[EnemyRecord]:
    """The enemies around a group that it may attack, in the order killing them matters; only those `admit` lets in,
    when it is given, and with `in_range_only` those already within the weapon range of one of its units. With
    `around_middle` they are those within contact_cells of `middle` whatever contact_focus says: the siege's
    long-range units, which fire from a band of their own, take theirs so."""
    group_class = dominant_class(units, world)
    extra = None if around_middle else _extra_reach(tuning.combat, in_range_only)
    is_around = _around(units, middle, world, tuning.combat, extra)
    near = [
        record
        for record in world.enemy_memory.values()
        if _is_target(record) and is_around(record) and (admit is None or admit(record))
    ]

    def order(record: EnemyRecord) -> tuple:
        rank = priority_class(record, group_class, world, tuning, units)
        core_order = CORE_BUILDINGS.index(_base_type(record.type, world)) if rank == CORE else 0
        distance = math.hypot(record.x - middle[0], record.y - middle[1])
        return rank, core_order, record.hp, distance, record.id

    return sorted(near, key=order)


def assign(
    attackers: list[Unit],
    ranked: list[EnemyRecord],
    world: World,
    tuning: Tuning,
    current: Mapping[int, int] | None = None,
    in_range_only: bool = False,
    held: Set[int] = frozenset(),
    crushing: bool = False,
) -> list[Assignment]:
    """Hand the attackers out over the enemies in sight among `ranked`, by the value of each pairing.

    `current` gives the target each unit was given before, by unit id; a unit
    keeps it as the module's note says, and whatever else is worth more while
    it is one of `held`, whose hold on its target is not over, and the target
    is within its weapon range. With contact_focus on, or with
    `in_range_only`, a unit is paired only with the targets within its reach
    (see `_extra_reach`), and artillery and V2 always only with those within
    their weapon range; with `crushing`, while a crush run is on, those take
    the enemy vehicles first. The assignments come in the order their targets
    were first given out; a unit with no possible target is in none of them.
    """
    combat = tuning.combat
    current = current or {}
    group_class = dominant_class(attackers, world)
    in_sight = [_target(record, group_class, attackers, world, tuning) for record in ranked if record.visible]
    given_out: list[_Target] = []
    extra = _extra_reach(combat, in_range_only)
    for unit_id, possible in _walk(attackers, in_sight, world, combat, extra, crushing):
        chosen = _choose(possible, current.get(unit_id), unit_id in held, combat)
        if not chosen.target.attackers:
            given_out.append(chosen.target)
        chosen.target.given += chosen.damage
        chosen.target.attackers.append(unit_id)
    return [Assignment(target.record, tuple(target.attackers)) for target in given_out]


class TargetMemory:
    """The target each own unit was last given by a focus fire, and the tick it was first given it.

    One for the match (`MatchContext.target_memory`), shared by the focus fires that hand out main's and the base's
    units, so that a unit passed from one manager to another keeps its target and its hold on it.
    """

    def __init__(self) -> None:
        # Unit id -> the id of the enemy it was last given and the tick it was first given it.
        self._given: dict[int, tuple[int, int]] = {}

    def targets(self) -> dict[int, int]:
        """The id of the enemy each unit was last given, by unit id."""
        return {unit_id: target for unit_id, (target, _) in self._given.items()}

    def held(self, tick: int, hold_ticks: int) -> frozenset[int]:
        """The units whose hold on their target is not over at `tick`: given it less than `hold_ticks` before."""
        return frozenset(unit_id for unit_id, (_, since) in self._given.items() if tick - since < hold_ticks)

    def note(self, units: Iterable[Unit], assignments: Iterable[Assignment], world: World) -> None:
        """Take in the targets a focus fire gave `units` in this cycle: a unit given the target it had keeps the tick
        it was first given it, one given another starts its hold now, one given none forgets its own. The units no
        longer in the world are forgotten."""
        given = {unit_id: assignment.target.id for assignment in assignments for unit_id in assignment.attackers}
        for unit in units:
            target, before = given.get(unit.id), self._given.pop(unit.id, None)
            if target is not None:
                since = before[1] if before is not None and before[0] == target else world.tick
                self._given[unit.id] = (target, since)
        self._given = {unit_id: entry for unit_id, entry in self._given.items() if unit_id in world.units}


class FocusFire:
    """An engaged group's targets, remembered from one cycle to the next so that each unit keeps its own: in the
    memory given, which several focus fires may share, or in one of its own."""

    def __init__(self, memory: TargetMemory | None = None) -> None:
        self._memory = TargetMemory() if memory is None else memory
        # Unit id -> the target it charges and the cell it was last sent to (see `tank_charge_artillery`).
        self._charges: dict[int, tuple[int, Cell]] = {}

    def orders(
        self,
        units: list[Unit],
        middle: Point,
        world: World,
        tuning: Tuning,
        admit: Callable[[EnemyRecord], bool] | None = None,
        chase: bool = True,
        in_range_only: bool = False,
        crushing: bool = False,
    ) -> list[Intent]:
        """The orders of a group engaged around `middle`, where there is an enemy to attack, among the enemies `admit`
        lets in when it is given, and with `in_range_only` among those already within a unit's weapon range; with
        `crushing`, while a crush run is on, its artillery and V2 take the enemy vehicles first.

        `attack` for the units given an enemy in sight; with `chase`, `attack_move` on the first ground enemy for the
        others but artillery and V2, none for them while only aircraft are in reach.
        """
        ranked = targets(units, middle, world, tuning, admit, in_range_only)
        memory = self._memory
        held = memory.held(world.tick, tuning.combat.focus_min_hold_seconds * TICKS_PER_SECOND)
        assignments = assign(units, ranked, world, tuning, memory.targets(), in_range_only, held, crushing)
        memory.note(units, assignments, world)
        by_id = {unit.id: unit for unit in units}
        charges = self._charge_cells(assignments, by_id, world, tuning.combat)
        sent = [
            intent
            for assignment in assignments
            for intent in _assignment_orders(assignment, by_id, charges, ranked, world)
        ]
        return [*sent, *_chase(units, assignments, ranked, world, tuning.combat)] if chase else sent

    def _charge_cells(
        self, assignments: list[Assignment], by_id: dict[int, Unit], world: World, tuning: CombatTuning
    ) -> dict[int, Cell]:
        """With `tank_charge_artillery` on, the cell each charging unit is sent to (see `charging`): the target's, or
        the one it was sent to last while the target has moved no more than CHARGE_RECAST_CELLS from it."""
        cells: dict[int, Cell] = {}
        for assignment in assignments if tuning.tank_charge_artillery else ():
            target = assignment.target
            for unit_id in charging([by_id[unit_id] for unit_id in assignment.attackers], target, world):
                cells[unit_id] = self._recast(unit_id, target)
        self._charges = {unit_id: self._charges[unit_id] for unit_id in cells}
        return cells

    def _recast(self, unit_id: int, target: EnemyRecord) -> Cell:
        """The cell a unit charging `target` is sent to, noted as its last."""
        cell = (target.x, target.y)
        last = self._charges.get(unit_id)
        if last is not None and last[0] == target.id and is_near(last[1], *cell, CHARGE_RECAST_CELLS):
            cell = last[1]
        self._charges[unit_id] = (target.id, cell)
        return cell


def charging(attackers: Iterable[Unit], target: EnemyRecord, world: World) -> list[int]:
    """The attackers that charge their target rather than attack it: our vehicles other than artillery and V2, when
    the target is a vehicle with a minimum range (artillery, V2), while they stand farther than that minimum range
    less 1 cell from it."""
    min_range = world.items.damage.get(target.type).min_range
    if min_range <= 0 or target.is_building or not _is_vehicle(target.type, world):
        return []
    reach = (min_range - 1) ** 2
    return [
        unit.id
        for unit in attackers
        if _is_charger(unit.type, world) and (unit.x - target.x) ** 2 + (unit.y - target.y) ** 2 > reach
    ]


def _is_charger(unit_type: str, world: World) -> bool:
    """Whether a unit of a type charges an enemy artillery piece or V2: a vehicle, not artillery or V2 itself."""
    return _is_vehicle(unit_type, world) and unit_type not in SIEGE_TYPES


def _is_vehicle(unit_type: str, world: World) -> bool:
    """Whether the War Factory's queue makes a type, by the item table."""
    facts = world.items.get(unit_type)
    return facts is not None and facts.queue == VEHICLE_QUEUE


def _assignment_orders(
    assignment: Assignment, by_id: dict[int, Unit], charges: dict[int, Cell], ranked: list[EnemyRecord], world: World
) -> list[Intent]:
    """The orders of one assignment: an attack for the units that fire on the target, and a move to its cell for
    those that charge it, one for each cell."""
    firing = [unit_id for unit_id in assignment.attackers if unit_id not in charges]
    by_cell: dict[Cell, list[int]] = {}
    for unit_id in assignment.attackers:
        if unit_id in charges:
            by_cell.setdefault(charges[unit_id], []).append(unit_id)
    orders = [
        Intent(
            attack_class([by_id[unit_id] for unit_id in firing], ranked, world),
            {"cmd": "attack", "units": firing, "target": assignment.target.id},
        )
    ]
    orders = orders if firing else []
    return orders + [
        Intent(Priority.MOVE, {"cmd": "move", "units": unit_ids, "to": list(cell)})
        for cell, unit_ids in by_cell.items()
    ]


def _chase(
    units: list[Unit], assignments: list[Assignment], ranked: list[EnemyRecord], world: World, tuning: CombatTuning
) -> list[Intent]:
    """An `attack_move` on the first ground enemy, of those given out and then of `ranked`, for the units given no
    target but artillery and V2, which are sent after no enemy; none while only aircraft are in reach."""
    attacking = {unit_id for assignment in assignments for unit_id in assignment.attackers}
    rest = [unit.id for unit in units if unit.id not in attacking and unit.type not in SIEGE_TYPES]
    ground = _first_ground_enemy([*(assignment.target for assignment in assignments), *ranked], world, tuning)
    if not rest or ground is None:
        return []
    return [Intent(Priority.MOVE, {"cmd": "attack_move", "units": rest, "to": [ground.x, ground.y]})]


def is_in_contact(unit: Unit, enemies: Iterable[EnemyRecord], world: World) -> bool:
    """Whether one of the enemies is within a unit's weapon range (from the damage table) and 2 cells.

    Against an aircraft the range is that of the unit's anti-air weapon where it reaches farther, so that a rocket
    soldier firing on a helicopter 7 cells off is in the fight.
    """
    damage = world.items.damage
    facts = damage.get(unit.type)
    ground = (facts.range + CONTACT_MARGIN_CELLS) ** 2
    air = (max(facts.range, facts.air_range) + CONTACT_MARGIN_CELLS) ** 2
    # Only a unit whose anti-air weapon outranges its ground weapons needs to know which enemies fly.
    if air == ground:
        return any((record.x - unit.x) ** 2 + (record.y - unit.y) ** 2 <= ground for record in enemies)
    return any(
        (record.x - unit.x) ** 2 + (record.y - unit.y) ** 2 <= (air if damage.get(record.type).air else ground)
        for record in enemies
    )


def enemies_in_sight(world: World) -> list[EnemyRecord]:
    """The enemies in sight that can be shot at, the ones a unit is in contact with."""
    return [record for record in world.enemy_memory.values() if record.visible and _is_target(record)]


def in_contact(units: Iterable[Unit], world: World) -> list[Unit]:
    """The units in contact with an enemy in sight that can be shot at (see `is_in_contact`)."""
    enemies = enemies_in_sight(world)
    return [unit for unit in units if enemies and is_in_contact(unit, enemies, world)]


def engaged(units: Iterable[Unit], intents: Iterable[Intent], world: World) -> frozenset[int]:
    """The ids of the units given an attack on a named enemy among `intents`, or a charge on an enemy artillery piece
    or V2 (`is_charge`), and of the units in contact."""
    attacking = {
        unit_id
        for intent in intents
        if (intent.order.get("cmd") == "attack" and "target" in intent.order) or is_charge(intent.order, world)
        for unit_id in intent.order["units"]
    }
    return frozenset(attacking.union(unit.id for unit in in_contact(units, world)))


def is_charge(order: Mapping, world: World) -> bool:
    """Whether an order is a charge: a `move` to within CHARGE_RECAST_CELLS of an enemy vehicle in sight that has a
    minimum range."""
    if order.get("cmd") != "move" or "to" not in order:
        return False
    cell = order["to"]
    return any(
        world.items.damage.get(record.type).min_range > 0
        and _is_vehicle(record.type, world)
        and is_near(cell, record.x, record.y, CHARGE_RECAST_CELLS)
        for record in enemies_in_sight(world)
    )


def attack_class(attackers: Iterable[Unit], enemies: list[EnemyRecord], world: World) -> Priority:
    """The class of an attack: CONTACT when one of its units is in contact with one of the enemies, else MOVE."""
    return Priority.CONTACT if any(is_in_contact(unit, enemies, world) for unit in attackers) else Priority.MOVE


def target_facts(enemy_type: str, world: World, tuning: CombatTuning) -> Damage:
    """The damage facts an enemy of a type is weighed by: its row of the damage table, but none for an aircraft
    while `air_targeting` is off."""
    facts = world.items.damage.get(enemy_type)
    return NO_DAMAGE if facts.air and not tuning.air_targeting else facts


def _target(record: EnemyRecord, group_class: str, units: list[Unit], world: World, tuning: Tuning) -> _Target:
    """An enemy in sight as the assignment of `units` weighs it."""
    table_hp = _table_hp(record.type, world)
    hp_left = _hp_left(record, table_hp, tuning.combat)
    weight = focus_weight(priority_class(record, group_class, world, tuning, units), tuning.combat)
    return _Target(
        record,
        target_facts(record.type, world, tuning.combat),
        weight / hp_left,
        tuning.combat.overkill_factor * hp_left,
        table_hp is not None,
    )


def _defense_class(record: EnemyRecord, units: Iterable[Unit], world: World, tuning: CombatTuning) -> int | None:
    """The class of an enemy building as a defense against `units`: THREAT_DEFENSE while one of the units its weapon
    hurts stands within that weapon's range of it and `defense_threat_margin_cells`, FIGHTERS when none does; None when
    its weapons hurt none of them (an unarmed building, or an anti-aircraft one against units on the ground)."""
    damage = world.items.damage
    weapon = damage.get(record.type)
    is_armed = False
    for unit in units:
        facts = damage.get(unit.type)
        if weapon.dps_to(facts) <= 0:
            continue
        is_armed = True
        reach = weapon.range_to(facts) + tuning.defense_threat_margin_cells
        if (unit.x - record.x) ** 2 + (unit.y - record.y) ** 2 <= reach * reach:
            return THREAT_DEFENSE
    return FIGHTERS if is_armed else None


def _is_harmless_air(enemy_type: str, world: World, tuning: CombatTuning) -> bool:
    """Whether an enemy of a type is weighed as an aircraft that carries no weapon."""
    facts = target_facts(enemy_type, world, tuning)
    return facts.air and not facts.hits_ground


def _table_hp(enemy_type: str, world: World) -> int | None:
    """The hit points the item table gives an enemy's type; None when it gives none."""
    facts = world.items.get(enemy_type)
    return None if facts is None else facts.hp


def _hp_left(record: EnemyRecord, table_hp: int | None, tuning: CombatTuning) -> float:
    """The hit points an enemy has left: its type's `table_hp` times its share of health, at least 1.

    An enemy of a type the item table gives no hp for (a wall, a civilian
    building) counts `unknown_target_hp`, so that it is attacked only when
    nothing known is in reach: counted at nothing, it would outrank every
    enemy that is.
    """
    if table_hp is None:
        return float(tuning.unknown_target_hp)
    return max(table_hp * record.hp / _PERCENT, _MIN_HP_LEFT)


def _extra_reach(tuning: CombatTuning, in_range_only: bool) -> float | None:
    """How far beyond the range of the weapon that hits it an enemy may stand from a unit to be one of its candidate
    targets: nothing with `in_range_only`, focus_unit_reach_cells with contact_focus on; None, no limit of the unit's
    own, with contact_focus off, the candidates being those around the group's centroid."""
    if in_range_only:
        return 0.0
    return float(tuning.focus_unit_reach_cells) if tuning.contact_focus else None


def _around(
    units: list[Unit], middle: Point, world: World, tuning: CombatTuning, extra: float | None
) -> Callable[[EnemyRecord], bool]:
    """Whether an enemy is around a group: within the reach of one of its units, the range of its weapon that hits the
    enemy and `extra` (see `_extra_reach`), or with no reach of the units' own (None), within contact_cells of the
    group's centroid `middle`."""
    if extra is None:
        return lambda record: is_near(middle, record.x, record.y, tuning.contact_cells)
    damage = world.items.damage
    # Unit type -> the reach squared of its units against a ground enemy and against an aircraft.
    reach: dict[str, tuple[float, float]] = {}
    for unit_type in {unit.type for unit in units}:
        facts = damage.get(unit_type)
        # Artillery and V2 fire only on what is within their weapon range.
        own_extra = 0.0 if unit_type in SIEGE_TYPES else extra
        reach[unit_type] = ((facts.range + own_extra) ** 2, (facts.air_range + own_extra) ** 2)

    def is_within_reach(record: EnemyRecord) -> bool:
        side = int(target_facts(record.type, world, tuning).air)
        return any((record.x - unit.x) ** 2 + (record.y - unit.y) ** 2 <= reach[unit.type][side] for unit in units)

    return is_within_reach


def _walk(
    attackers: list[Unit],
    in_sight: list[_Target],
    world: World,
    tuning: CombatTuning,
    extra: float | None,
    crushing: bool = False,
) -> list[tuple[int, list[_Pairing]]]:
    """The units that can hurt a target in sight, by id with their possible pairings, in the order they are handed out.

    By class, the vehicles but artillery and V2 first, then those, then the infantry (see `_hand_out_class`); within a
    class the unit whose best pairing is worth the most goes first; of equal worth, the lower id.
    """
    by_type: dict[str, list[_Hurt]] = {}
    walk: list[tuple[int, float, int, list[_Pairing]]] = []
    for unit in attackers:
        if unit.type not in by_type:
            by_type[unit.type] = _hurt(unit.type, in_sight, world, tuning, extra, crushing)
        possible = _possible(by_type[unit.type], unit, world.items.damage.get(unit.type), tuning)
        if possible:
            walk.append((_hand_out_class(unit.type, world), possible[0].rank, unit.id, possible))
    walk.sort(key=lambda entry: entry[:3])
    return [(unit_id, possible) for _, _, unit_id, possible in walk]


def _hand_out_class(unit_type: str, world: World) -> int:
    """Where units of a type come in the order a group is handed out: the vehicles (and anything else that is neither
    infantry nor artillery or V2) first, artillery and V2 next, infantry last: the tanks take the targets their guns
    are made for before the rest fills in what is left."""
    if unit_type in SIEGE_TYPES:
        return _LONG_RANGE_NEXT
    facts = world.items.get(unit_type)
    return _INFANTRY_LAST if facts is not None and facts.queue == INFANTRY_QUEUE else _VEHICLES_FIRST


def _hurt(
    unit_type: str,
    in_sight: list[_Target],
    world: World,
    tuning: CombatTuning,
    extra: float | None,
    crushing: bool = False,
) -> list[_Hurt]:
    """The targets a unit of a type can hurt, wherever it stands, with the value of each in range (the counter factor
    included), the range of the weapon that hits each and the reach within which it is a candidate: that range and
    `extra` more, any distance for None.

    Against an aircraft that is the anti-air weapon, against the rest the ground weapons. A ground target whose armour
    the type does less than `focus_min_effectiveness` of its mean damage across the armour classes to is left out.
    What depends on where each unit stands, whether a target is within that range or reach or inside the minimum
    range, is left to `_possible`. Artillery and V2 reach no farther than their weapon range, an infantry target is
    worth more to them for the infantry around it (see `_splash_factor`), and with `crushing` they take the targets
    by the tiers of `_crush_tier`.
    """
    damage = world.items.damage.get(unit_type)
    by_armour = [damage.dps_against(armour) for armour in ARMOUR_CLASSES]
    mean_dps, least_dps = sum(by_armour) / len(by_armour), min(by_armour)
    floor_dps = tuning.focus_min_effectiveness * mean_dps
    is_long_range = unit_type in SIEGE_TYPES
    splash = _splash_counts(in_sight, world, tuning) if is_long_range else {}
    hurt = []
    for place, target in enumerate(in_sight):
        dps = damage.dps_to(target.facts)
        if _hurts(dps, target, floor_dps):
            weapon_range = damage.range_to(target.facts)
            reach = _reach_squared(weapon_range, is_long_range, extra)
            done = dps * tuning.focus_window_seconds
            value = dps * target.worth_per_dps * _counter_factor(dps, mean_dps, least_dps, target, tuning)
            value *= 1 + splash.get(target.record.id, 0)
            tier = _crush_tier(target.record, world) if is_long_range and crushing else 0
            hurt.append((value, place, target, done, weapon_range * weapon_range, reach, tier))
    return hurt


def _reach_squared(weapon_range: float, is_long_range: bool, extra: float | None) -> float:
    """The distance squared within which a target is a unit's candidate: the weapon range for artillery and V2, else
    that range and `extra`, any distance for None."""
    if is_long_range:
        return weapon_range * weapon_range
    return math.inf if extra is None else (weapon_range + extra) ** 2


def _splash_counts(in_sight: list[_Target], world: World, tuning: CombatTuning) -> dict[int, int]:
    """For each enemy infantry unit among `in_sight`, by id, how many other enemy infantry units in sight stand within
    `arty_splash_cells` of it: a shell on it hits them as well, so it is worth that many times more to an artillery
    piece or a V2."""
    infantry = [record for record in world.enemy_memory.values() if _is_infantry_in_sight(record, world)]
    reach = tuning.arty_splash_cells * tuning.arty_splash_cells
    return {
        target.record.id: _near_count(target.record, infantry, reach)
        for target in in_sight
        if _is_infantry_in_sight(target.record, world)
    }


def _near_count(record: EnemyRecord, others: list[EnemyRecord], reach_squared: float) -> int:
    """How many of `others` but the enemy itself stand within the square root of `reach_squared` of it."""
    return sum(
        1
        for other in others
        if other.id != record.id and (other.x - record.x) ** 2 + (other.y - record.y) ** 2 <= reach_squared
    )


def _is_infantry_in_sight(record: EnemyRecord, world: World) -> bool:
    """Whether an enemy is an infantry unit in sight."""
    return record.visible and not record.is_building and _is_infantry(record.type, world)


def _crush_tier(record: EnemyRecord, world: World) -> int:
    """The tier of a target to an artillery piece or a V2 while a crush run is on, the first 0: a stopped enemy
    vehicle, one on the move, an infantry unit, anything else."""
    if record.is_building:
        return _OTHER_TIER
    facts = world.items.get(record.type)
    queue = None if facts is None else facts.queue
    if queue == VEHICLE_QUEUE:
        return _STOPPED_VEHICLE_TIER if record.still else _MOVING_VEHICLE_TIER
    return _INFANTRY_TIER if queue == INFANTRY_QUEUE else _OTHER_TIER


def _is_infantry(enemy_type: str, world: World) -> bool:
    facts = world.items.get(enemy_type)
    return facts is not None and facts.queue == INFANTRY_QUEUE


def _hurts(dps: float, target: _Target, floor_dps: float) -> bool:
    """Whether a unit doing `dps` to a target is paired with it at all: it does damage, and to a ground target at
    least `floor_dps`, its share `focus_min_effectiveness` of the unit's mean damage."""
    return dps > 0 and (target.facts.air or dps >= floor_dps)


def _counter_factor(dps: float, mean_dps: float, least_dps: float, target: _Target, tuning: CombatTuning) -> float:
    """What a pairing's value is multiplied by for how well the unit's weapons suit the target's armour, with
    contact_focus on: the unit's `dps` against that armour over its `mean_dps` across the armour classes, raised to
    counter_power.

    Weapons made for an armour make a unit worth more against it than its damage alone says: a rocket soldier takes a
    tank over a rifle its rockets hardly scratch, though the rifle would die sooner. The factor is 1 with the switch
    off, and against an aircraft, which only an anti-air weapon hits whatever it wears. A target of a type without
    known hit points is counted at the unit's `least_dps`, so that the factor never lifts it over a known target that
    is worth more without the factor: like the hit points it is counted at, this keeps it last. A unit that does no
    damage on the ground hurts no ground target, so the mean is never 0 here.
    """
    if not tuning.contact_focus or target.facts.air:
        return 1.0
    counted = dps if target.hp_known else least_dps
    return (counted / mean_dps) ** tuning.counter_power


def _possible(hurt: list[_Hurt], unit: Unit, damage: Damage, tuning: CombatTuning) -> list[_Pairing]:
    """A unit's pairings from where it stands, the one it prefers first.

    None with a target inside its minimum range or beyond its reach; the value of one with a target beyond the range of
    the weapon that hits it is lowered by `out_of_range_factor`.
    """
    floor = damage.min_range * damage.min_range
    possible = []
    for value, place, target, done, in_range, reach, tier in hurt:
        distance_squared = (target.record.x - unit.x) ** 2 + (target.record.y - unit.y) ** 2
        if floor <= distance_squared <= reach:
            is_in_range = distance_squared <= in_range
            factor = 1.0 if is_in_range else tuning.out_of_range_factor
            possible.append(_Pairing(tier, -value * factor, distance_squared, place, target, done, is_in_range))
    possible.sort()
    return possible


def _choose(possible: list[_Pairing], current: int | None, is_held: bool, tuning: CombatTuning) -> _Pairing:
    """The target a unit is given, out of its possible ones, the most valuable first.

    The best one not yet covered, or the best of all when every one is
    covered, or with contact_focus on when the best one not covered is worth
    less than `spill_floor` of the best; but the target it had before, while
    that is not covered twice over and the other is not worth `retarget_gain`
    times as much, and whatever else while the unit's hold on it is not over
    (`is_held`) and it stands within the unit's weapon range. A target kept
    so is never one of a later tier than the other (see `_crush_tier`).
    """
    holding = _held(possible, current) if is_held else None
    if holding is not None:
        return holding
    best = possible[0]
    free = next((pairing for pairing in possible if not pairing.target.is_covered), best)
    if tuning.contact_focus and free.value < tuning.spill_floor * best.value:
        free = best
    kept = None if current is None else next(_kept(possible, current), None)
    return kept if kept is not None and _keeps(kept, free, tuning) else free


def _keeps(kept: _Pairing, free: _Pairing, tuning: CombatTuning) -> bool:
    """Whether a unit keeps the target it had (`kept`) over the one it would be given (`free`): one of no later tier,
    while the other is not worth `retarget_gain` times as much."""
    return kept.tier <= free.tier and free.value < tuning.retarget_gain * kept.value


def _held(possible: list[_Pairing], current: int | None) -> _Pairing | None:
    """The pairing with the target a unit had before while that stands within its weapon range; None otherwise."""
    return next((pairing for pairing in possible if pairing.target.record.id == current and pairing.in_range), None)


def _kept(possible: list[_Pairing], current: int) -> Iterator[_Pairing]:
    """The pairing with the target a unit had before, while it is possible and not covered twice over."""
    for pairing in possible:
        target = pairing.target
        if target.record.id == current and target.given <= STICKY_COVERAGE * target.needed:
            yield pairing


def _is_target(record: EnemyRecord) -> bool:
    """Whether an enemy can be shot at: a building, or a unit that is not stale and is not a support power's."""
    return record.is_building or (not record.stale and record.type not in SUPPORT_POWER_ACTORS)


def _first_ground_enemy(enemies: list[EnemyRecord], world: World, tuning: CombatTuning) -> EnemyRecord | None:
    """The first of the enemies that is not an aircraft, as the assignment weighs it; None when all of them are."""
    return next((record for record in enemies if not target_facts(record.type, world, tuning).air), None)


def _base_type(name: str, world: World) -> str:
    """The type an enemy's type is a variant of, or the type itself."""
    facts = world.items.get(name)
    return name if facts is None else facts.name
