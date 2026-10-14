"""The target main goes on under FINISH with `sticky_attack_target` of `[combat]` on.

Without the switch, main under FINISH goes in each cycle on the remembered
enemy building nearest to it. An army that falls back a few cells then finds
another building nearer and turns to it, and a building seen minutes ago,
which may be gone since, pulls it off a way it had half made. With the
switch on, main keeps one target.

**Choosing.** The target is chosen among the remembered enemy buildings, or
with none of them among the remembered enemy MCVs, which could build a new
base. The enemy's production (construction yard, war factory, barracks,
airfield and helipad) comes first, however long ago it was seen: a yard
seen a minute and a half ago is still where the match is decided, and a
tesla coil seen just now is not. Within production, and then among the other
buildings, a sighting within `stale_target_seconds` is fresh and comes
before any older one; the nearest to main's centroid comes first among the
fresh, and the one seen last, then the nearest, among the older.

**Keeping.** The target is kept from cycle to cycle wherever main goes, so
a pullback never changes it. It is dropped when it is no longer in the enemy
memory, which covers both a building destroyed and one whose cell was seen
empty (reason `lost`), and a new one is chosen. While it is not a production
building, an enemy production building that comes within `contact_cells` of
main's centroid takes its place, the nearest of them if several do (reason
`production_in_contact`): the enemy's production is what the attack is for.

**The missile silo.** With `nuke_spread` of `[combat]` on, a remembered
enemy missile silo (see `nuke`) comes before everything else: it is chosen
first, the nearest if there are several, and it takes the place of a kept
target that is no silo as soon as it is known (reason `missile_silo`); a
kept silo is not given up for production in contact. Each nuke it fires can
take a whole army.

**Record.** Each change of target is an `attack_target` event: the tick, the
old and the new target, each its id, type and cell or null for none, and the
reason, `none` when main had no target before.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ucla_agent.core import records
from ucla_agent.games.redalert import nuke
from ucla_agent.games.redalert.anti_air import AIR_PRODUCERS
from ucla_agent.games.redalert.family import BARRACKS_TYPES, CONSTRUCTION_YARD, MCV, WAR_FACTORY
from ucla_agent.games.redalert.fight import Point, centroid
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.tuning import CombatTuning
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

# The enemy buildings that make its army: the ones whose loss ends what the enemy can field.
PRODUCTION_TYPES = frozenset({CONSTRUCTION_YARD, WAR_FACTORY, *BARRACKS_TYPES, *AIR_PRODUCERS})
ATTACK_TARGET_EVENT = "attack_target"
# The reasons an `attack_target` event gives: main had no target, its target left the enemy memory, an enemy
# production building came into contact while the target was not one, or an enemy missile silo is known while the
# target is no silo.
NO_TARGET = "none"
LOST = "lost"
PRODUCTION_IN_CONTACT = "production_in_contact"
MISSILE_SILO_KNOWN = "missile_silo"
# The ranks of the targets, the first chosen first: a missile silo with `nuke_spread` on, production, the rest.
SILO_RANK, PRODUCTION_RANK, OTHER_RANK = range(3)


@dataclass(frozen=True)
class Kept:
    """The target main keeps under FINISH: a remembered enemy actor by its id, with its type and its cell."""

    id: int
    type: str
    cell: Cell

    @classmethod
    def of(cls, record: EnemyRecord) -> Kept:
        """The target a remembered enemy actor makes, where it was last seen."""
        return cls(record.id, record.type, (record.x, record.y))


def follow(kept: Kept | None, army: list[Unit], world: World, tuning: CombatTuning) -> Kept | None:
    """The target main keeps in this cycle: `kept` while it is remembered and neither a missile silo nor a production
    building in contact is to take its place, else a new choice; None when nothing is left to choose. Each change is
    an event."""
    middle = centroid(army)
    record = None if kept is None else world.enemy_memory.get(kept.id)
    if record is None:
        return _change(kept, choose(middle, world, tuning), NO_TARGET if kept is None else LOST, world)
    replacement, reason = _replacement(record, middle, world, tuning)
    if replacement is not None:
        return _change(kept, replacement, reason, world)
    # The cell is read afresh: an MCV kept as the target may have moved since it was chosen.
    return Kept.of(record)


def _replacement(
    kept: EnemyRecord, middle: Point, world: World, tuning: CombatTuning
) -> tuple[EnemyRecord | None, str]:
    """What takes the place of a kept target still remembered, and the reason: the nearest missile silo for a target
    of a lower rank, else production in contact for one of the lowest; None for nothing."""
    rank = _rank(kept, tuning)
    silo = _nearest_silo(middle, world) if rank > SILO_RANK and tuning.nuke_spread else None
    if silo is not None or rank < OTHER_RANK:
        return silo, MISSILE_SILO_KNOWN
    return _production_in_contact(middle, world, tuning), PRODUCTION_IN_CONTACT


def is_production(record: EnemyRecord) -> bool:
    """Whether a remembered enemy actor is one of the enemy's production buildings."""
    return record.is_building and record.type in PRODUCTION_TYPES


def choose(middle: Point, world: World, tuning: CombatTuning) -> EnemyRecord | None:
    """The remembered enemy building, else MCV, main should take as its target from `middle`; None for neither.

    Production first, however old its sighting; within production and then
    among the rest, a fresh sighting before any older one, the nearest first
    among the fresh, and the one seen last, then the nearest, among the older.
    """
    fresh_from = world.tick - tuning.stale_target_seconds * TICKS_PER_SECOND
    return min(candidates(world), key=lambda record: _order(record, middle, fresh_from, tuning), default=None)


def candidates(world: World) -> list[EnemyRecord]:
    """What a target is chosen among: the remembered enemy buildings, else the remembered enemy MCVs."""
    remembered = world.enemy_memory.values()
    return [record for record in remembered if record.is_building] or [
        record for record in remembered if record.type == MCV
    ]


def _order(record: EnemyRecord, middle: Point, fresh_from: int, tuning: CombatTuning) -> tuple[int, bool, int, float]:
    """Where a candidate target stands in the order of choice: the smaller, the sooner it is chosen."""
    is_fresh = record.last_seen_tick >= fresh_from
    # Among the fresh the tick a building was seen does not count, only its distance.
    seen_order = 0 if is_fresh else -record.last_seen_tick
    return _rank(record, tuning), not is_fresh, seen_order, _distance(record, middle)


def _rank(record: EnemyRecord, tuning: CombatTuning) -> int:
    """A target's rank: a missile silo with `nuke_spread` on, then production, then the rest."""
    if tuning.nuke_spread and record.is_building and nuke.is_silo(record.type):
        return SILO_RANK
    return PRODUCTION_RANK if is_production(record) else OTHER_RANK


def _nearest_silo(middle: Point, world: World) -> EnemyRecord | None:
    """The remembered enemy missile silo nearest to main's centroid; None for none."""
    return min(nuke.known_silos(world), key=lambda record: _distance(record, middle), default=None)


def _production_in_contact(middle: Point, world: World, tuning: CombatTuning) -> EnemyRecord | None:
    """The remembered enemy production building nearest to main's centroid within `contact_cells` of it; None for
    none."""
    near = [
        record
        for record in world.enemy_memory.values()
        if is_production(record) and _distance(record, middle) <= tuning.contact_cells
    ]
    return min(near, key=lambda record: _distance(record, middle), default=None)


def _change(old: Kept | None, record: EnemyRecord | None, reason: str, world: World) -> Kept | None:
    """Make a remembered enemy actor, or none, the target, with an `attack_target` event when that changes it."""
    new = None if record is None else Kept.of(record)
    if new != old:
        data = {"tick": world.tick, "old": _described(old), "new": _described(new), "reason": reason}
        records.log(ATTACK_TARGET_EVENT, data)
    return new


def _described(target: Kept | None) -> dict[str, Any] | None:
    """A target as an event gives it: its id, type and cell; None for none."""
    return None if target is None else {"id": target.id, "type": target.type, "cell": list(target.cell)}


def _distance(record: EnemyRecord, middle: Point) -> float:
    return math.hypot(record.x - middle[0], record.y - middle[1])
