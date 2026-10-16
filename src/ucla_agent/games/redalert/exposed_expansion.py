"""The known enemy expansions nothing defends (`[combat] strike_exposed_expansion`), which a strike goes for.

Live (sidestep, rush, 17:00): the enemy's second yard and its refinery stood
38 cells from our base with no defense and no unit near, the commander's
question offered them as "unguarded", the model gave no answer for ten
minutes and main stood gathered at home. The strike near us takes only an
expansion beside our buildings and the over-extended strike only a refinery
far from every enemy yard, so nothing went for a yard away from the enemy's
start. With the switch on, those are targets:

- **An expansion:** a known enemy refinery or construction yard more than
  exposed_expansion_start_cells from the enemy's start, and every other known
  enemy building (but a defense) within exposed_expansion_building_cells of
  one. Buildings never move, so a building last seen long ago counts: the
  enemy memory forgets it once one of our units sees it gone.
- **Nothing defends it:** no known enemy ground defense whose weapon range
  reaches it (`command_targets.reaches`), and no enemy unit that fights seen
  within exposed_expansion_clear_cells of it in the last
  exposed_expansion_unit_seconds.

The targets are taken the construction yards first (a yard down stops the
whole expansion: it builds nothing again), then the nearest to a cell (our
base's center for a strike from home, main's centroid for a strike from where
main stands, the strike force's centroid for its next target), then by id. A force can
finish an expansion (`can_finish`) when its damage (the damage table's, each
unit's against each building's armour) does exposed_expansion_kill_share
times the hit points the buildings within exposed_expansion_building_cells of
the target have left in exposed_expansion_kill_seconds: a force sized to bring
them down quickly, not to plink at a yard while the enemy comes back.
Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.command_targets import reaches
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD, REFINERY
from ucla_agent.games.redalert.fight import Point, ground_defenses
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.scout_missions import enemy_start
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.world import DEFENSE_TYPES, Cell, EnemyRecord, Unit, World

# The enemy buildings an expansion is made of.
ANCHOR_TYPES = frozenset({REFINERY, CONSTRUCTION_YARD})
_PERCENT = 100


def targets(origin: Point | None, world: World, ctx: MatchContext) -> list[EnemyRecord]:
    """The buildings of the known enemy expansions nothing defends (see the module's note), the yards first, then the
    nearest to `origin` (by id alone without one), then by id; none with strike_exposed_expansion off or while the
    enemy's start is not known."""
    tuning = ctx.tuning.combat
    start = enemy_start(world)
    if not tuning.strike_exposed_expansion or start is None:
        return []
    buildings = [record for record in world.enemy_memory.values() if record.is_building]
    anchors = [
        _cell(record)
        for record in buildings
        if record.type in ANCHOR_TYPES and cells.distance(_cell(record), start) > tuning.exposed_expansion_start_cells
    ]
    reach = tuning.exposed_expansion_building_cells
    found = [
        record
        for record in buildings
        if record.type not in DEFENSE_TYPES
        and any(cells.is_within(_cell(record), anchor, reach) for anchor in anchors)
        and is_undefended(_cell(record), world, ctx)
    ]
    return sorted(found, key=lambda record: (record.type != CONSTRUCTION_YARD, _away(origin, record), record.id))


def _away(origin: Point | None, record: EnemyRecord) -> float:
    """How far a record stands from a point; 0 without one."""
    return 0.0 if origin is None else math.dist(origin, _cell(record))


def can_finish(force: list[Unit], target: EnemyRecord, world: World, ctx: MatchContext) -> bool:
    """Whether a force's damage does exposed_expansion_kill_share times the hit points left of the known enemy
    buildings but defenses within exposed_expansion_building_cells of a target, itself among them, in
    exposed_expansion_kill_seconds (see the module's note). A building the item table gives no hit points for counts
    none; one the force cannot hurt, past finishing."""
    tuning = ctx.tuning.combat
    reach = tuning.exposed_expansion_building_cells
    damage = world.items.damage
    seconds = 0.0
    for record in world.enemy_memory.values():
        facts = world.items.get(record.type)
        if not record.is_building or record.type in DEFENSE_TYPES or facts is None or facts.hp is None:
            continue
        if not cells.is_within(_cell(record), _cell(target), reach):
            continue
        armour = damage.get(record.type)
        per_second = sum(damage.get(unit.type).dps_to(armour) for unit in force)
        if per_second <= 0:
            return False
        seconds += facts.hp * record.hp / _PERCENT / per_second
    return seconds * tuning.exposed_expansion_kill_share <= tuning.exposed_expansion_kill_seconds


def is_undefended(cell: Cell, world: World, ctx: MatchContext) -> bool:
    """Whether nothing defends a cell: no known enemy ground defense reaches it, and no enemy unit that fights was seen
    within exposed_expansion_clear_cells of it in the last exposed_expansion_unit_seconds."""
    if any(reaches(defense, cell, world, ctx) for defense in ground_defenses(world)):
        return False
    tuning = ctx.tuning.combat
    lately = world.tick - tuning.exposed_expansion_unit_seconds * TICKS_PER_SECOND
    return not any(
        is_combat_unit(record)
        and record.last_seen_tick >= lately
        and cells.is_within(cell, _cell(record), tuning.exposed_expansion_clear_cells)
        for record in world.enemy_memory.values()
    )


def _cell(record: EnemyRecord) -> Cell:
    return record.x, record.y
