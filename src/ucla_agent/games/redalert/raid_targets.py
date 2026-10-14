"""What a raid goes after: the enemy's harvesters, where no defense covers them.

A raid is a few fast units, too few to fight their way past a defense. So a
raid is sent only at a target that no remembered enemy defense covers, that
is, whose cell lies outside every remembered defense's weapon range and a
margin (see `coverage`). It goes after the enemy's income where a few units
can take it quickly: the harvesters. Of two it takes the one nearest to the
group.

A harvester is looked for where it was last seen, and only while that is
recent: a harvester unseen for half a minute is somewhere else by now.

A refinery stays where it was seen, but it takes a raid long to destroy, and
a raid still at it when the enemy's army comes loses more than it gains. So a
refinery is a target only as a fallback: when no enemy harvester has been
seen for a minute (`refinery_after_ticks`), and the group's damage over
twenty seconds (`refinery_kill_seconds`) reaches the hit points the refinery
has left. A raid therefore never goes after a refinery while a harvester has
been seen lately, let alone while one it could reach is known. Power plants
and the other buildings are no targets.

With `nuke_spread` of `[combat]` on, an enemy missile silo no defense
covers (see `nuke`) is the first target of all, the nearest first: each nuke
it fires can take a whole army, and a silo far from the enemy's defenses is
one a raid may reach.

With `[raids] prefer_exposed_trucks` on, the harvesters of the raid
opportunities scouting has noted (see `exposed_trucks`) come before every
other harvester, the nearest of them first: harvesters working far from the
enemy's start, with little guard and no defense over them, are the ones a few
units can take before help comes. With the switch off the opportunities change
nothing here.

The opportunities a raid is launched on with `[raids] raid_exposed_trucks`
on (`exposed`) are those of them guarded by little enough
(`exposed_raid_guard_max`) and seen lately enough
(`exposed_raid_seen_seconds`), the freshest first. Of each, the raid goes for
the harvester it saw that is still remembered, not yet destroyed by a raid
and under no defense, nearest where the one nearest our base stood: the cell
the opportunity names is where it was, the record is what a raid can attack.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Collection, Iterable

from ucla_agent.games.redalert import coverage, nuke
from ucla_agent.games.redalert.family import HARVESTER, REFINERY
from ucla_agent.games.redalert.fight import Point
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.tuning import RaidTuning, Tuning
from ucla_agent.games.redalert.world import EnemyRecord, Unit, World

if TYPE_CHECKING:
    # The opportunities are noted by this module's kinds of target (`exposed_trucks` imports it), so the ranking names
    # their type only for the annotation.
    from ucla_agent.games.redalert.exposed_trucks import ExposedTrucks

# The kinds of raid target, the most wanted first.
SILOS, HARVESTERS, REFINERIES = range(3)
_PERCENT = 100


def kind(record: EnemyRecord, world: World) -> int | None:
    """Which kind of raid target a remembered enemy is; None for one a raid does not go after."""
    base = _base_type(record, world)
    if record.is_building and nuke.is_silo(record.type):
        return SILOS
    if record.is_building:
        return REFINERIES if base == REFINERY else None
    return HARVESTERS if base == HARVESTER and not record.stale else None


def is_covered(record: EnemyRecord, world: World, tuning: Tuning) -> bool:
    """Whether a remembered enemy defense covers where an enemy was last seen."""
    return coverage.is_covered((record.x, record.y), world.known_enemy_defenses, world.items, tuning.coverage)


def ranked(
    world: World, origin: Point, raiders: list[Unit], tuning: Tuning, exposed: Iterable[ExposedTrucks] = ()
) -> list[EnemyRecord]:
    """The targets these raiders may be sent at (`targets`), the harvesters first and the nearest to `origin` among
    equals; with `prefer_exposed_trucks` on, the harvesters of the opportunities `exposed` before the other
    harvesters."""
    is_preferring = tuning.raids.prefer_exposed_trucks
    preferred = {harvester for each in exposed for harvester in each.harvester_ids} if is_preferring else set()

    def order(record: EnemyRecord) -> tuple[int | None, bool, float, int]:
        distance = math.hypot(record.x - origin[0], record.y - origin[1])
        return kind(record, world), record.id not in preferred, distance, record.id

    return sorted(targets(world, [unit.type for unit in raiders], tuning), key=order)


def exposed(
    world: World, opportunities: Iterable[ExposedTrucks], tuning: Tuning, destroyed: Collection[int] = ()
) -> list[EnemyRecord]:
    """The harvesters a raid is launched at, one for each opportunity guarded by at most exposed_raid_guard_max and
    seen within exposed_raid_seen_seconds, the freshest first: the one of its `harvester_ids` still remembered, not in
    `destroyed` and uncovered, nearest the cell of its `nearest_harvester`; an opportunity with none is passed over."""
    raids = tuning.raids
    seen_from = world.tick - raids.exposed_raid_seen_seconds * TICKS_PER_SECOND
    fresh = [
        opportunity
        for opportunity in opportunities
        if opportunity.guard_value <= raids.exposed_raid_guard_max and opportunity.seen_tick >= seen_from
    ]
    newest_first = sorted(fresh, key=lambda opportunity: -opportunity.seen_tick)
    found = (_exposed_harvester(opportunity, world, tuning, destroyed) for opportunity in newest_first)
    return [record for record in found if record is not None]


def _exposed_harvester(
    opportunity: ExposedTrucks, world: World, tuning: Tuning, destroyed: Collection[int]
) -> EnemyRecord | None:
    """The harvester of an opportunity a raid goes for (see `exposed`); None when none of its harvesters is."""
    memory = world.enemy_memory
    remembered = (memory[harvester] for harvester in opportunity.harvester_ids if harvester in memory)
    open_to_raid = [
        record for record in remembered if record.id not in destroyed and not is_covered(record, world, tuning)
    ]
    cell_x, cell_y = opportunity.nearest_harvester
    return min(
        open_to_raid, key=lambda record: (math.hypot(record.x - cell_x, record.y - cell_y), record.id), default=None
    )


def targets(world: World, raider_types: list[str], tuning: Tuning) -> list[EnemyRecord]:
    """The remembered enemies a raid of units of these types, one type for each unit, may be sent at.

    The uncovered missile silos with `nuke_spread` on, the uncovered harvesters, and an uncovered refinery only when
    no harvester has been seen for `refinery_after_ticks` and the raiders can destroy it within
    `refinery_kill_seconds`.
    """
    is_refinery_due = _has_seen_no_harvester_for(world, tuning.raids.refinery_after_ticks)
    return [
        record
        for record in world.enemy_memory.values()
        if _is_eligible(record, raider_types, is_refinery_due, world, tuning)
    ]


def _is_eligible(
    record: EnemyRecord, raider_types: list[str], is_refinery_due: bool, world: World, tuning: Tuning
) -> bool:
    """Whether a raid of units of these types may be sent at a remembered enemy (see `targets`)."""
    target_kind = kind(record, world)
    if target_kind is None or is_covered(record, world, tuning):
        return False
    if target_kind == SILOS:
        return tuning.combat.nuke_spread
    if target_kind == REFINERIES:
        return is_refinery_due and _can_destroy(record, raider_types, world, tuning.raids)
    return True


def _has_seen_no_harvester_for(world: World, ticks: int) -> bool:
    """Whether no enemy harvester has been in sight within the last `ticks`; true when none ever was."""
    last_seen = [
        record.last_seen_tick
        for record in world.enemy_memory.values()
        if not record.is_building and _base_type(record, world) == HARVESTER
    ]
    return not last_seen or world.tick - max(last_seen) >= ticks


def _can_destroy(building: EnemyRecord, raider_types: list[str], world: World, tuning: RaidTuning) -> bool:
    """Whether the raiders' damage over `refinery_kill_seconds` reaches the hit points a building has left.

    The damage is the damage table's, each raider's against the building's armour. A building of a type the item
    table gives no hit points for is never known to be within their reach.
    """
    facts = world.items.get(building.type)
    if facts is None or facts.hp is None:
        return False
    armour = world.items.damage.get(building.type)
    damage_per_second = sum(world.items.damage.get(raider_type).dps_to(armour) for raider_type in raider_types)
    return damage_per_second * tuning.refinery_kill_seconds >= facts.hp * building.hp / _PERCENT


def _base_type(record: EnemyRecord, world: World) -> str:
    """The type a remembered enemy's type is a variant of, or the type itself."""
    facts = world.items.get(record.type)
    return record.type if facts is None else facts.name
