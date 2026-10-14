"""The estimate of a fight: what one group of the agent's units is worth against the enemy around it.

Whether a group engages, holds or retreats, whether a raid breaks off, is
decided on one figure: the ratio of the group's strength to the enemy's
strength around it. A unit's strength is its cost weighted by the share of
its health it has left.

The estimate is made around the group's centroid. Of the group it counts the
units close to the centroid: a straggler far behind takes no part in the
fight. Of the enemy it counts the units that fight and are in sight or were
seen lately (a unit unseen for long may be anywhere, and is not counted),
and each remembered defense that can shoot at the centroid, at twice its
cost, since a defense hits hard and cannot be chased away; a defense whose
weapon hits aircraft alone, the AA gun or the SAM site (`items.is_air_only`),
cannot shoot at a ground group, and is no defense here. When any of what
is counted is out of sight, the enemy's strength is raised by a fifth: what
is not seen may have grown.

A group that cannot hurt what the enemy fields is worth half as much. Rifles
and grenadiers do little to tanks, and rockets and cannons little to a swarm
of infantry; such a group against an enemy whose strength is mostly of the
class it cannot hurt has its strength halved. Tanks are among the units that
hurt infantry: they crush it.

Enemy aircraft are weighed apart, since only an anti-air weapon hits one. An
aircraft that carries no weapon, a transport, is not counted: it hurts
nothing (the support powers' planes are not counted in the first place). One
that does counts its strength raised by `air_unanswered_weight` times the
share of the group's strength in units without an anti-air weapon, so that a
group that cannot shoot back faces it at up to twice its cost. With
`air_targeting` off in `[combat]`, an aircraft counts as any other unit.

An enemy unit of a type the item table cannot value, neither listed nor a
variant of a listed type (the FransBots' technicians `tecn` and `tecn2`),
counts at `unknown_unit_value` in `[combat]` times its share of health, in
neither unit queue's class; the damage table gives such a type armour none,
which is what infantry wears. Only the fight's weighing of enemy units counts
it so: `worth` and `strength` value such a type at 0, as the production and
targeting tables do.

No estimate is made for a group with no enemy near; there is no fight.
A group can also be weighed against given enemies wherever they stand
(`ratio_against`), the way the estimate weighs a group and its enemies, to
know before it sets out whether it can take them; and against given enemies
and defenses together (`weigh`), each defense counted as in the estimate.
Against one target, an enemy group or a defense, only the units of the group
that can hurt it are weighed (`ratio_against_target`): a unit whose weapons
do no damage to the target, by the damage table, adds nothing to taking it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from ucla_agent.games.redalert.items import is_air_only
from ucla_agent.games.redalert.production import INFANTRY_QUEUE, VEHICLE_QUEUE
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning
from ucla_agent.games.redalert.world import EnemyRecord, Unit, World

_PERCENT = 100
# Units that do little against vehicles: a group of nothing else is no match for armor.
SMALL_ARMS = frozenset({"e1", "e2"})
# Units that hurt infantry: the machine guns, the grenades, the flak, and the tanks that crush it.
ANTI_INFANTRY = frozenset({"e1", "e2", "jeep", "apc", "ftrk", "1tnk", "2tnk", "3tnk", "4tnk"})
# Units that hurt vehicles: the rockets and the tank guns.
ANTI_VEHICLE = frozenset({"e3", "1tnk", "2tnk", "3tnk", "4tnk"})

Point = tuple[float, float]


@dataclass(frozen=True)
class Estimate:
    """The estimate of one group's fight, as of one cycle."""

    # The group's strength, after the matchup factor.
    own: float
    # The enemy's strength, after the factor for what is out of sight.
    enemy: float
    ratio: float
    centroid: Point
    # The group's units that were counted: those near the centroid.
    counted: tuple[int, ...]
    # The enemy units that were counted.
    enemies: tuple[EnemyRecord, ...]


def centroid(units: Iterable[Unit]) -> Point:
    """The mean cell of some units; there has to be one at least."""
    cells = [(unit.x, unit.y) for unit in units]
    return sum(x for x, _ in cells) / len(cells), sum(y for _, y in cells) / len(cells)


def is_near(point: Point, x: float, y: float, reach: float) -> bool:
    """Whether a cell is within `reach` cells of a point."""
    return math.hypot(point[0] - x, point[1] - y) <= reach


def worth(unit_type: str, hp: int, world: World) -> float:
    """What an actor is worth in a fight: its cost weighted by its share of health; 0 for a type of no known cost."""
    facts = world.items.get(unit_type)
    return 0.0 if facts is None else facts.cost * hp / _PERCENT


def is_counted(record: EnemyRecord) -> bool:
    """Whether a remembered enemy counts in a fight: a unit that fights, in sight or seen lately."""
    return is_combat_unit(record) and (record.visible or not record.stale)


def estimate(units: list[Unit], world: World, tuning: Tuning) -> Estimate | None:
    """The estimate of a group's fight; None when no enemy unit that counts and no remembered defense is near it."""
    combat = tuning.combat
    middle = centroid(units)
    hostile = _near(middle, _fighting(world, combat), combat)
    defenses = _near(middle, ground_defenses(world), combat)
    if not hostile and not defenses:
        return None
    counted = _gathered(middle, units, combat)
    shooting = _shooting_at(middle, defenses, world, tuning)
    by_class = _enemy_by_class(hostile, world, _air_factor(counted, world, combat), combat)
    enemy = sum(by_class.values()) + combat.defense_weight * sum(_cost(record, world) for record in shooting)
    own = strength(counted, world) * _matchup(counted, by_class, enemy, combat)
    if not all(record.visible for record in [*hostile, *shooting]):
        enemy *= combat.unseen_factor
    ratio = own / enemy if enemy > 0 else combat.ratio_without_enemy
    return Estimate(own, enemy, ratio, middle, tuple(unit.id for unit in counted), tuple(hostile))


def _fighting(world: World, tuning: CombatTuning) -> list[EnemyRecord]:
    """The remembered enemy units that count in a fight, but the aircraft that hurt nothing."""
    return [
        record
        for record in world.enemy_memory.values()
        if is_counted(record) and not _is_harmless_air(record, world, tuning)
    ]


def ground_defenses(world: World) -> list[EnemyRecord]:
    """The known enemy defenses that may fire at ground units: all but the air-only ones."""
    return [defense for defense in world.known_enemy_defenses if not is_air_only(defense.type, world.items)]


def ratio_against(units: list[Unit], enemies: Iterable[EnemyRecord], world: World, tuning: Tuning) -> float:
    """The ratio of a group's strength to that of some enemy units, wherever both stand: each side weighed as in the
    estimate of a fight, every unit of the group counted, and no defense. ratio_without_enemy when the enemy units are
    worth nothing; a group that is to go and fight enemies elsewhere is weighed against them before it is there."""
    own, enemy = weigh(units, enemies, world, tuning)
    return own / enemy if enemy > 0 else tuning.combat.ratio_without_enemy


def can_hurt(unit_type: str, enemy_type: str, world: World) -> bool:
    """Whether a unit of a type does damage to an enemy of a type, by the damage table: its anti-air weapon to an
    aircraft, its ground weapons by armour to the rest."""
    damage = world.items.damage
    return damage.get(unit_type).dps_to(damage.get(enemy_type)) > 0


def ratio_against_target(
    units: Iterable[Unit],
    target: Iterable[EnemyRecord],
    world: World,
    tuning: Tuning,
    enemies: Iterable[EnemyRecord] = (),
    defenses: Iterable[EnemyRecord] = (),
) -> float:
    """The ratio of the strength of the units given that can hurt a target, one of the actors that make it up
    (`target`), to the strength of the enemy units and the defenses it is weighed with, each side weighed as `weigh`
    weighs it (a defense at defense_weight times its cost). 0 when none of the units can hurt the target;
    ratio_without_enemy when what it is weighed with is worth nothing."""
    aimed = list(target)
    hurting = [unit for unit in units if any(can_hurt(unit.type, record.type, world) for record in aimed)]
    if not hurting:
        return 0.0
    own, enemy = weigh(hurting, enemies, world, tuning, defenses)
    return own / enemy if enemy > 0 else tuning.combat.ratio_without_enemy


def weigh(
    units: list[Unit],
    enemies: Iterable[EnemyRecord],
    world: World,
    tuning: Tuning,
    defenses: Iterable[EnemyRecord] = (),
) -> tuple[float, float]:
    """A group's strength and the enemy's, wherever both stand: every unit of the group counted, the enemy units and
    the defenses given weighed as the estimate of a fight weighs them (a defense at `defense_weight` times its cost),
    the enemy raised by `unseen_factor` when any of them is out of sight. The enemy is 0 when it is worth nothing."""
    combat = tuning.combat
    hostile = [record for record in enemies if not _is_harmless_air(record, world, combat)]
    shooting = list(defenses)
    by_class = _enemy_by_class(hostile, world, _air_factor(units, world, combat), combat)
    enemy = sum(by_class.values()) + combat.defense_weight * sum(_cost(record, world) for record in shooting)
    if enemy <= 0:
        return strength(units, world), 0.0
    own = strength(units, world) * _matchup(units, by_class, enemy, combat)
    if not all(record.visible for record in [*hostile, *shooting]):
        enemy *= combat.unseen_factor
    return own, enemy


def _near(point: Point, records: list[EnemyRecord], tuning: CombatTuning) -> list[EnemyRecord]:
    """The remembered enemies within the contact distance of a point."""
    return [record for record in records if is_near(point, record.x, record.y, tuning.contact_cells)]


def _gathered(point: Point, units: list[Unit], tuning: CombatTuning) -> list[Unit]:
    """The units close enough to a group's centroid to take part in its fight."""
    return [unit for unit in units if is_near(point, unit.x, unit.y, tuning.own_reach_cells)]


def _shooting_at(point: Point, defenses: list[EnemyRecord], world: World, tuning: Tuning) -> list[EnemyRecord]:
    return [record for record in defenses if _can_shoot_at(record, point, world, tuning)]


def strength(units: Iterable[Unit], world: World) -> float:
    """What units are worth in a fight together."""
    return sum(worth(unit.type, unit.hp, world) for unit in units)


def _cost(record: EnemyRecord, world: World) -> float:
    """What a remembered enemy cost, whatever its health."""
    return worth(record.type, _PERCENT, world)


def _can_shoot_at(defense: EnemyRecord, point: Point, world: World, tuning: Tuning) -> bool:
    """Whether a remembered defense reaches a point: its range (or the one taken when unknown) and a cell."""
    facts = world.items.get(defense.type)
    weapon_range = facts.range if facts is not None and facts.range is not None else tuning.coverage.unknown_range_cells
    return is_near(point, defense.x, defense.y, weapon_range + tuning.combat.defense_margin_cells)


def _enemy_by_class(
    hostile: list[EnemyRecord], world: World, air_factor: float, tuning: CombatTuning
) -> dict[str | None, float]:
    """What the enemy units are worth, by the queue that makes them; None for those of neither unit queue.

    An aircraft is worth `air_factor` times its strength while air targeting is on, and a unit of a type the
    item table cannot value unknown_unit_value times its share of health.
    """
    by_class: dict[str | None, float] = {}
    for record in hostile:
        facts = world.items.get(record.type)
        queue = facts.queue if facts is not None and facts.queue in (INFANTRY_QUEUE, VEHICLE_QUEUE) else None
        if facts is None and not record.is_building:
            value = tuning.unknown_unit_value * record.hp / _PERCENT
        else:
            value = worth(record.type, record.hp, world)
        if tuning.air_targeting and world.items.damage.get(record.type).air:
            value *= air_factor
        by_class[queue] = by_class.get(queue, 0.0) + value
    return by_class


def _is_harmless_air(record: EnemyRecord, world: World, tuning: CombatTuning) -> bool:
    """Whether an enemy is an aircraft without a weapon, which is not counted while air targeting is on."""
    facts = world.items.damage.get(record.type)
    return tuning.air_targeting and facts.air and not facts.hits_ground


def _air_factor(counted: list[Unit], world: World, tuning: CombatTuning) -> float:
    """The factor on an armed enemy aircraft's strength: 1 + `air_unanswered_weight` x the share of the group's
    strength in units without an anti-air weapon (all of it for a group worth nothing)."""
    total = strength(counted, world)
    answering = strength((unit for unit in counted if world.items.damage.get(unit.type).dps_air > 0), world)
    unanswered_share = 1.0 - answering / total if total > 0 else 1.0
    return 1.0 + tuning.air_unanswered_weight * unanswered_share


def _matchup(counted: list[Unit], by_class: dict[str | None, float], enemy: float, tuning: CombatTuning) -> float:
    """The factor on a group's strength: lowered when it cannot hurt the class most of the enemy's strength is of."""
    if enemy <= 0:
        return 1.0
    types = {unit.type for unit in counted}
    mostly_vehicles = by_class.get(VEHICLE_QUEUE, 0.0) > tuning.dominant_share * enemy
    mostly_infantry = by_class.get(INFANTRY_QUEUE, 0.0) > tuning.dominant_share * enemy
    if (mostly_vehicles and types <= SMALL_ARMS) or (mostly_infantry and not types & ANTI_INFANTRY):
        return tuning.matchup_factor
    return 1.0
