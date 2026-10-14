"""What the agent knows of the enemy, summed up once a cycle for every rule that weighs the enemy.

The world remembers every enemy unit and building it has seen (its enemy
memory). The scout report is the summary the rules of play read from it: how
many defenses the enemy has, what its army is worth, where its harvesters
work, whether its base has been found.

The report is made afresh in every cycle from the memory as it then stands,
so it never says more than this match has shown. A few facts do not fade with
the memory, because what they say stays true: that the enemy base has been
seen, that the enemy fields vehicles, and when an enemy building was last in
sight. Those the tracker keeps from cycle to cycle.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD, HARVESTER, SERVICE_DEPOT, WAR_FACTORY
from ucla_agent.games.redalert.items import NON_COMBAT_TYPES
from ucla_agent.games.redalert.production import VEHICLE_QUEUE
from ucla_agent.games.redalert.tuning import ScoutingTuning
from ucla_agent.games.redalert.world import Cell, EnemyRecord, World

# The enemy buildings that mean vehicles, whether or not one has been seen.
VEHICLE_BUILDINGS = frozenset({WAR_FACTORY, SERVICE_DEPOT})
# What an enemy's support powers put on the map that a state lists among its units but that fights nothing: the
# spy plane, the camera it leaves, and the plane that drops paratroopers with its camera.
SUPPORT_POWER_ACTORS = frozenset({"u2", "camera", "camera.spyplane", "badr", "camera.paradrop"})


@dataclass(frozen=True)
class ScoutReport:
    """What is known of the enemy, as of one cycle."""

    enemy_base_seen: bool = False
    # Remembered enemy building type -> how many are remembered.
    enemy_buildings: dict[str, int] = field(default_factory=dict)
    enemy_defense_count: int = 0
    # The summed cost of the remembered enemy units that fight and are not stale.
    enemy_combat_value: int = 0
    # Where each remembered enemy harvester was last seen.
    enemy_harvester_cells: list[Cell] = field(default_factory=list)
    # An enemy vehicle that fights, a war factory or a service depot has been seen in this match.
    enemy_has_vehicles: bool = False
    # The last tick an enemy building was in sight; None while none has been.
    last_building_seen_tick: int | None = None


def is_combat_unit(record: EnemyRecord) -> bool:
    """Whether a remembered enemy actor is a unit that fights."""
    return not record.is_building and record.type not in NON_COMBAT_TYPES | SUPPORT_POWER_ACTORS


class EnemyIntel:
    """The facts about the enemy that this match has shown, and the report made from them. See the module's note."""

    def __init__(self) -> None:
        self.last_building_seen_tick: int | None = None
        self._is_base_seen = False
        self._has_vehicles = False

    def observe(self, world: World, tuning: ScoutingTuning) -> ScoutReport:
        """Take in what the cycle's world shows of the enemy, and return the report as it stands now."""
        buildings_in_sight = []
        for record in world.enemy_memory.values():
            if not record.visible:
                continue
            if record.is_building:
                buildings_in_sight.append(record)
            self._has_vehicles = self._has_vehicles or _means_vehicles(record, world)
        if buildings_in_sight:
            self.last_building_seen_tick = world.tick
        self._is_base_seen = self._is_base_seen or any(
            _is_part_of_base(record, world, tuning) for record in buildings_in_sight
        )
        return self._report(world)

    def _report(self, world: World) -> ScoutReport:
        remembered = world.enemy_memory.values()
        return ScoutReport(
            enemy_base_seen=self._is_base_seen,
            enemy_buildings=dict(Counter(record.type for record in remembered if record.is_building)),
            enemy_defense_count=len(world.known_enemy_defenses),
            enemy_combat_value=sum(
                _cost(record.type, world) for record in remembered if is_combat_unit(record) and not record.stale
            ),
            enemy_harvester_cells=[(record.x, record.y) for record in remembered if record.type == HARVESTER],
            enemy_has_vehicles=self._has_vehicles,
            last_building_seen_tick=self.last_building_seen_tick,
        )


def _is_part_of_base(building: EnemyRecord, world: World, tuning: ScoutingTuning) -> bool:
    """Whether an enemy building shows the enemy base: a construction yard, or a building near the enemy's start."""
    near_start = world.enemy_base is not None and cells.is_within(
        (building.x, building.y), world.enemy_base, tuning.base_seen_cells
    )
    return building.type == CONSTRUCTION_YARD or near_start


def _means_vehicles(record: EnemyRecord, world: World) -> bool:
    """Whether an enemy actor shows the enemy fields vehicles: a vehicle that fights, or a building that makes them."""
    if record.is_building:
        return record.type in VEHICLE_BUILDINGS
    facts = world.items.get(record.type)
    return facts is not None and facts.queue == VEHICLE_QUEUE and is_combat_unit(record)


def _cost(item: str, world: World) -> int:
    facts = world.items.get(item)
    return facts.cost if facts is not None else 0
