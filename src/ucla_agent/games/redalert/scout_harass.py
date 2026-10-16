"""The harass of an undefended base (`[scouting] scout_base_harass`): a slot scout shoots what it finds unguarded.

A careful scout (see `scout_stance`) treats an enemy base's ground as danger
and only looks at it. A base with no defense and no army at home is worth more
than a look: shots at its engineers, trucks and power plants force the enemy
to react, to pull units home or build defenses, whatever they kill. So with
the switch on as well as `scout_cautious`, a slot scout harasses such a base.

- **When.** The scout stands within harass_trigger_cells of the enemy's start
  or of a remembered enemy building but a wall; no remembered ground defense
  (`fight.ground_defenses`) and no enemy unit that fights and counts in a fight
  (`fight.is_counted`: in sight or seen lately) stands within
  harass_clear_cells of it; and it has a target.
- **Targets.** The enemies in sight within harass_target_cells of the scout
  that it can hurt (`fight.can_hurt`), but walls and those a remembered ground
  defense covers (`coverage.is_covered`), by tier: engineers first; then ore
  trucks and the infantry that does not fight; then power plants; then every
  other building. Within a tier the nearest to the scout, ties by the lower
  id. A unit that fights is no target: it ends the harass. A dog hurts no
  truck nor building, so it harasses infantry alone.
- **Who.** A slot scout in the field that is not retreating, hunting (see
  `scout_hunt`) or the rush scout while its rule steers it, and whose last
  harass ended harass_cooldown_seconds ago or more.

While it harasses, its order is one `attack` on its chosen target, made anew
when the target changes (the chosen one is no target any more, or one of a
better tier is) and again every HARASS_REORDER_SECONDS while the scout stands
idle; its steering, its mission (kept for later) and the model commander's
order are set aside, the enemy base's ground is no danger to it, and its
retreat's causes are not taken. It stays in hold_fire.

The harass ends, judged in every cycle in this order: `defense` once a ground
defense is remembered within harass_clear_cells; `enemy_unit` once an enemy
unit that fights and counts is; `hit` once its health dropped by
scout_retreat_hp_drop or more since the last state; `timeout` after
harass_max_seconds; `no_target` once it has had no target for
harass_idle_seconds. After `defense` and `enemy_unit` the scout begins a
retreat for cause `outclassed`, after `hit` for cause `hit`, each with the
bait shot (see `scout_bait`); after `timeout` and `no_target` it takes up its
mission again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from ucla_agent.games.redalert import cells, coverage, fight
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.scout_retreat import HIT, OUTCLASSED
from ucla_agent.games.redalert.scout_steering import WALL_TYPES, enemy_base_cells
from ucla_agent.games.redalert.world import EnemyRecord, Unit, World

# The target tiers, the first harassed first: engineers; ore trucks and the infantry that does not fight; power
# plants. Every other building is the last tier.
ENGINEERS = frozenset({"e6"})
SOFT_UNITS = frozenset({"harv", "medi", "mech", "spy", "thf"})
POWER_PLANTS = frozenset({"powr", "apwr"})
_TIERS = (ENGINEERS, SOFT_UNITS, POWER_PLANTS)
BUILDING_TIER = len(_TIERS) + 1
# A harassing scout that stands idle is ordered to attack its target again this long after its last order.
HARASS_REORDER_SECONDS = 2
# Why a harass ended.
DEFENSE = "defense"
ENEMY_UNIT = "enemy_unit"
HIT_END = "hit"
TIMEOUT = "timeout"
NO_TARGET = "no_target"
# The ends after which the scout retreats, with the retreat's cause; after the others it takes up its mission again.
RETREAT_CAUSES = {DEFENSE: OUTCLASSED, ENEMY_UNIT: OUTCLASSED, HIT_END: HIT}


def tier(record: EnemyRecord) -> int | None:
    """The tier of an enemy as a harass target, 1 the first; None for an enemy that is no target, a unit that
    fights."""
    found = next((number for number, types in enumerate(_TIERS, start=1) if record.type in types), None)
    if found is None and record.is_building:
        return BUILDING_TIER
    return found


def harass_targets(scout: Unit, world: World, ctx: MatchContext) -> list[EnemyRecord]:
    """The enemies a harassing scout may attack now (see the module's note), the first tier first, within a tier the
    nearest, ties by the lower id."""
    tuning, position = ctx.tuning, (scout.x, scout.y)
    defenses = fight.ground_defenses(world)
    found = [
        record
        for record in world.enemy_memory.values()
        if record.visible
        and tier(record) is not None
        and record.type not in WALL_TYPES
        and cells.is_within(position, (record.x, record.y), tuning.scouting.harass_target_cells)
        and fight.can_hurt(scout.type, record.type, world)
        and not coverage.is_covered((record.x, record.y), defenses, world.items, tuning.coverage)
    ]
    return sorted(found, key=lambda record: (tier(record), cells.distance(position, (record.x, record.y)), record.id))


def is_by_enemy_base(scout: Unit, world: World, ctx: MatchContext) -> bool:
    """Whether a scout stands within harass_trigger_cells of the enemy's start or of a remembered enemy building but a
    wall."""
    position, reach = (scout.x, scout.y), ctx.tuning.scouting.harass_trigger_cells
    bases = [*enemy_base_cells(world), *([] if world.enemy_base is None else [world.enemy_base])]
    return any(cells.is_within(position, base, reach) for base in bases)


def has_defense_near(scout: Unit, world: World, ctx: MatchContext) -> bool:
    """Whether a remembered ground defense stands within harass_clear_cells of a scout."""
    position, reach = (scout.x, scout.y), ctx.tuning.scouting.harass_clear_cells
    return any(cells.is_within(position, (defense.x, defense.y), reach) for defense in fight.ground_defenses(world))


def has_enemy_unit_near(scout: Unit, world: World, ctx: MatchContext) -> bool:
    """Whether an enemy unit that fights and counts in a fight stands within harass_clear_cells of a scout."""
    position, reach = (scout.x, scout.y), ctx.tuning.scouting.harass_clear_cells
    return any(
        fight.is_counted(record) and cells.is_within(position, (record.x, record.y), reach)
        for record in world.enemy_memory.values()
    )


@dataclass
class Harass:
    """One scout's harass: when it began, its chosen target, the attack orders it made and when its target was
    lost."""

    started_tick: int
    # The chosen target; None while the scout has none.
    target_id: int | None
    # The target and tick of the last attack order; None until the first went out.
    ordered_target: int | None = None
    ordered_tick: int | None = None
    orders: int = 0
    # The tick from which the scout has had no target; None while it has one.
    no_target_tick: int | None = None


@dataclass(frozen=True)
class Ended:
    """A harass that ended in a cycle: the slot whose scout harassed, the harass and why it ended."""

    slot: int
    harass: Harass
    reason: str


class Harasses:
    """The harasses of one match's slot scouts. See the module's note."""

    def __init__(self) -> None:
        # Unit id -> its harass, while one lasts.
        self._active: dict[int, Harass] = {}
        # Unit id -> the tick its last harass ended.
        self._ended_tick: dict[int, int] = {}

    @property
    def harassing(self) -> frozenset[int]:
        """The scouts that harass."""
        return frozenset(self._active)

    def end(
        self,
        scouts: Mapping[int, Unit],
        last_hp: Callable[[int], int | None],
        world: World,
        ctx: MatchContext,
    ) -> list[Ended]:
        """End the harasses whose end has come, of the slots' scouts in the field (`scouts`, by slot number), each one
        with why, by each scout's health in the last state (`last_hp`); the chosen target of each that goes on is
        brought up to date. A harass whose scout is no longer in the field is forgotten."""
        live = {scout.id for scout in scouts.values()}
        self._active = {unit_id: harass for unit_id, harass in self._active.items() if unit_id in live}
        ended = []
        for number, scout in scouts.items():
            harass = self._active.get(scout.id)
            reason = None if harass is None else _end_reason(scout, harass, last_hp(scout.id), world, ctx)
            if reason is not None:
                del self._active[scout.id]
                self._ended_tick[scout.id] = world.tick
                ended.append(Ended(number, harass, reason))
        return ended

    def start(self, scout: Unit, world: World, ctx: MatchContext) -> tuple[Harass, EnemyRecord] | None:
        """Have a scout that may harass begin a harass when the switch is on, its cooldown is over and the base by it
        is open to one (see the module's note); the harass begun and its first target, None for none."""
        tuning = ctx.tuning.scouting
        last = self._ended_tick.get(scout.id)
        is_cooling = last is not None and world.tick - last < tuning.harass_cooldown_seconds * TICKS_PER_SECOND
        if not tuning.scout_base_harass or is_cooling or scout.id in self._active:
            return None
        if not is_by_enemy_base(scout, world, ctx) or has_defense_near(scout, world, ctx):
            return None
        found = [] if has_enemy_unit_near(scout, world, ctx) else harass_targets(scout, world, ctx)
        if not found:
            return None
        harass = Harass(world.tick, found[0].id)
        self._active[scout.id] = harass
        return harass, found[0]

    def due(self, scout: Unit, world: World) -> EnemyRecord | None:
        """The chosen target of a harassing scout whose attack order is due in this cycle: none was made on it yet, or
        the scout stands idle HARASS_REORDER_SECONDS after the last; None when none is due or the scout does not
        harass."""
        harass = self._active.get(scout.id)
        if harass is None or harass.target_id is None:
            return None
        last = harass.ordered_tick
        is_again = scout.idle and last is not None and world.tick - last >= HARASS_REORDER_SECONDS * TICKS_PER_SECOND
        is_due = harass.ordered_target != harass.target_id or is_again
        return world.enemy_memory[harass.target_id] if is_due else None

    def ordered(self, unit_id: int, tick: int) -> None:
        """Note that a harassing scout's attack order on its chosen target went out at a tick."""
        harass = self._active.get(unit_id)
        if harass is not None:
            harass.ordered_target, harass.ordered_tick = harass.target_id, tick
            harass.orders += 1


def _end_reason(scout: Unit, harass: Harass, last_hp: int | None, world: World, ctx: MatchContext) -> str | None:
    """Why a harass ends in this cycle, the first that holds in the order of the module's note; None while it goes on,
    its chosen target brought up to date."""
    tuning = ctx.tuning.scouting
    if has_defense_near(scout, world, ctx):
        return DEFENSE
    if has_enemy_unit_near(scout, world, ctx):
        return ENEMY_UNIT
    if last_hp is not None and last_hp - scout.hp >= tuning.scout_retreat_hp_drop:
        return HIT_END
    if world.tick - harass.started_tick >= tuning.harass_max_seconds * TICKS_PER_SECOND:
        return TIMEOUT
    _choose(harass, harass_targets(scout, world, ctx), world.tick)
    no_target_ticks = tuning.harass_idle_seconds * TICKS_PER_SECOND
    if harass.no_target_tick is not None and world.tick - harass.no_target_tick >= no_target_ticks:
        return NO_TARGET
    return None


def _choose(harass: Harass, found: list[EnemyRecord], tick: int) -> None:
    """Keep a harass's chosen target while it is still a target and none of a better tier is, else take the first
    target; with none, note since when the scout has had none."""
    if not found:
        harass.target_id = None
        harass.no_target_tick = harass.no_target_tick if harass.no_target_tick is not None else tick
        return
    harass.no_target_tick = None
    kept = next((record for record in found if record.id == harass.target_id), None)
    if kept is None or tier(found[0]) < tier(kept):
        harass.target_id = found[0].id
