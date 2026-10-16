"""The harassment tower: a cheap ground defense put up in range of an enemy building that stands near our base.

An enemy building our base could reach with a defense placed at its edge is a
target that never moves away: a pillbox or a gun (a flame tower or a Tesla
coil for the Soviets) there wears it down for the price of the defense. With
`[base_defense] harass_tower` on, the base-defense manager looks for such a
target every few seconds while the Defense queue is free. A target is a known
enemy building within the range of one of the family's two cheap ground
defenses of a cell where the base can place that defense (the candidates
laid out around each own construction yard) that lies within far_rescue_cells
of `[base_defense]` of the centre of the base. A tower out by a far yard
stands where the enemy attacks with more than main may be sent to answer
(see `far_rescue`): it is lost there, and the money is not in the defenses
at home. No tower goes up while the centre of the base is not known. The
cell is passed over when an enemy army worth `ARMY_WORTH_LIMIT` or more
stands within `ARMY_NEAR_CELLS` of it, which would kill the tower before it
paid off, or when a known enemy
ground defense reaches within its range plus `ENEMY_DEFENSE_MARGIN_CELLS` of
it, a duel the tower would lose. The defense against infantry is tried
before the one against armor, and of the cells that pass, the one nearest
the yard is taken.

The defense is ordered with that cell as its goal (`MatchContext.defense_goals`),
so that upkeep places it there, and a `base_defense` event of reason
`harass_tower` is written. At most `harass_tower_max` towers stand or are on
their way at a time, and an enemy building is the target of one tower in the
match at the most. The tower is our own defense: it is no enemy object, and
nothing that strikes at enemy objects near our base takes it for one.
"""

from __future__ import annotations

from dataclasses import dataclass

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, placement
from ucla_agent.games.redalert.batch import Intent
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD, FAMILIES
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.production import DEFENSE_QUEUE
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.world import DEFENSE_TYPES, Cell, EnemyRecord, World

BASE_DEFENSE_EVENT = "base_defense"
HARASS_TOWER_REASON = "harass_tower"
# An enemy army worth this much within ARMY_NEAR_CELLS of a tower's cell would kill the tower before it paid off.
ARMY_WORTH_LIMIT = 1000
ARMY_NEAR_CELLS = 10
# A tower keeps this many cells beyond the range of a known enemy ground defense, so that it starts no duel.
ENEMY_DEFENSE_MARGIN_CELLS = 2
# How often the map is looked at for a target, in ticks (5 seconds): the candidate cells are many, the targets rare.
LOOK_EVERY_TICKS = 125
# A tower counts as standing when a defense of its type stands this close to the cell it was ordered for.
STANDS_AT_CELLS = 1


@dataclass(frozen=True)
class Tower:
    """A harassment tower ordered: its item, the cell it was ordered for and the enemy building it is against."""

    item: str
    cell: Cell
    target_id: int


class HarassTowers:
    """Orders a harassment tower when a target is in reach. See the module's note."""

    def __init__(self) -> None:
        # The towers ordered in this match, the newest last.
        self._towers: list[Tower] = []
        # The tick the map was last looked at for a target; None before the first look.
        self._looked_at: int | None = None

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for one harassment tower when one is due and a target is in reach; none with the switch off."""
        tuning = ctx.tuning.base_defense
        if not tuning.harass_tower or not self._is_look_due(world) or not ctx.production.is_queue_free(DEFENSE_QUEUE):
            return []
        self._looked_at = world.tick
        if len(self._standing_or_on_way(world, ctx)) >= tuning.harass_tower_max:
            return []
        targeted = {tower.target_id for tower in self._towers}
        found = _find_tower(world, ctx, [record for record in _enemy_buildings(world) if record.id not in targeted])
        if found is None:
            return []
        tower, target = found
        self._towers.append(tower)
        ctx.defense_goals[tower.item] = tower.cell
        ctx.exact_defense_goals.add(tower.item)
        _log(tower, target, world)
        return [ctx.production.build(tower.item)]

    def _is_look_due(self, world: World) -> bool:
        return self._looked_at is None or world.tick - self._looked_at >= LOOK_EVERY_TICKS

    def _standing_or_on_way(self, world: World, ctx: MatchContext) -> list[Tower]:
        """The towers that stand at their cells, and the newest while its item is on its way."""
        newest = self._towers[-1] if self._towers else None
        return [
            tower
            for tower in self._towers
            if _stands(tower, world) or (tower is newest and ctx.production.pending(tower.item) > 0)
        ]


def _stands(tower: Tower, world: World) -> bool:
    """Whether an own defense of the tower's type stands at the cell it was ordered for."""
    return any(
        building.type == tower.item and cells.is_within((building.x, building.y), tower.cell, STANDS_AT_CELLS)
        for building in world.buildings.values()
    )


def _enemy_buildings(world: World) -> list[EnemyRecord]:
    """The enemy buildings known, each where it was last seen."""
    return [record for record in world.enemy_memory.values() if record.is_building]


def _find_tower(world: World, ctx: MatchContext, targets: list[EnemyRecord]) -> tuple[Tower, EnemyRecord] | None:
    """The first tower that can stand in range of a target, the defense against infantry tried first; None while none
    can."""
    yards = [(building.x, building.y) for building in world.buildings.values() if building.type == CONSTRUCTION_YARD]
    names = FAMILIES[world.family]
    for item in (names.anti_infantry_defense, names.anti_armor_defense):
        if item not in world.available_production or not ctx.production.can_power(item):
            continue
        found = _tower_of(item, yards, targets, world, ctx)
        if found is not None:
            return found
    return None


def _tower_of(
    item: str, yards: list[Cell], targets: list[EnemyRecord], world: World, ctx: MatchContext
) -> tuple[Tower, EnemyRecord] | None:
    """A tower of one item: of the cells laid out around a yard near a target, the first, nearest that yard, in range
    of a target and safe (see `_is_safe`); None while there is none."""
    reach = world.items.damage.get(item).range
    tuning = ctx.tuning.placement
    for yard in yards:
        near = [record for record in targets if cells.is_within(_cell_of(record), yard, tuning.ring_max_cells + reach)]
        if not near:
            continue
        for cell in placement.candidate_cells(item, world, ctx.terrain, tuning, (), yard):
            if not _is_near_home(cell, world, ctx):
                continue
            target = next((record for record in near if cells.is_within(cell, _cell_of(record), reach)), None)
            if target is not None and _is_safe(cell, world):
                return Tower(item, cell, target.id), target
    return None


def _is_near_home(cell: Cell, world: World, ctx: MatchContext) -> bool:
    """Whether a tower's cell lies within far_rescue_cells of the centre of the base, where main answers an attack on
    it; never while the centre is not known."""
    centre = world.base_center
    return centre is not None and cells.is_within(cell, centre, ctx.tuning.base_defense.far_rescue_cells)


def _is_safe(cell: Cell, world: World) -> bool:
    """Whether a tower at a cell is out of the reach of the enemy: no enemy army worth ARMY_WORTH_LIMIT or more within
    ARMY_NEAR_CELLS of it, and no known enemy ground defense whose range plus ENEMY_DEFENSE_MARGIN_CELLS reaches it."""
    army = sum(
        _cost(record.type, world)
        for record in world.enemy_memory.values()
        if is_combat_unit(record) and not record.stale and cells.is_within(cell, _cell_of(record), ARMY_NEAR_CELLS)
    )
    if army >= ARMY_WORTH_LIMIT:
        return False
    return not any(
        cells.is_within(cell, _cell_of(record), _ground_range(record.type, world) + ENEMY_DEFENSE_MARGIN_CELLS)
        for record in world.enemy_memory.values()
        if record.is_building and record.type in DEFENSE_TYPES and _ground_range(record.type, world) > 0
    )


def _ground_range(item: str, world: World) -> float:
    """The range of a defense's weapon against ground targets; 0 for one whose weapon hits aircraft alone."""
    facts = world.items.damage.get(item)
    return facts.range if any(dps > 0 for dps in facts.dps.values()) else 0.0


def _cost(item: str, world: World) -> int:
    facts = world.items.get(item)
    return facts.cost if facts is not None else 0


def _cell_of(record: EnemyRecord) -> Cell:
    return record.x, record.y


def _log(tower: Tower, target: EnemyRecord, world: World) -> None:
    """Write the `base_defense` event of a harassment tower ordered."""
    records.log(
        BASE_DEFENSE_EVENT,
        {
            "reason": HARASS_TOWER_REASON,
            "tick": world.tick,
            "item": tower.item,
            "cell": list(tower.cell),
            "target": {"id": target.id, "type": target.type, "cell": [target.x, target.y]},
        },
    )
