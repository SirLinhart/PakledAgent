"""The counterattack (`[combat] counterattack`): main pushes out at once after a won defense or a regroup at home.

Live (sidestep, rush, 17:00; shadowfiend-ii, rush, 10:00): main beat an enemy
attack on the base, or regrouped at home, with an army well above what the
enemy had left, and then stood at the staging point under HOLD or the
commander's gather. The moment the enemy's attack is spent is the best time
to attack. With the switch on, the strike (see `strike`) is told when one is
due (`CounterWatch.follow`):

- **A won defense.** A defense lasts while base defense reports a threat or
  the approach alarm is in force (`MatchContext.threat_active`,
  `MatchContext.approach_alarm`). When it ends, the enemy cost killed in it
  (`world.military`) is counterattack_min_kills or more and more than the own
  cost lost in it.
- **A regroup over.** While the model commander regroups main (its regroup
  gather, `Commander.is_regrouping`), main stands gathered at the staging
  point: once in each regroup.

A counterattack due is started within counterattack_window_seconds, while the
base is under no threat and no approach alarm is in force, and main's
strength (`fight.strength`) is at least counterattack_ratio times what the
enemy army is now (`enemy_now`): the enemy army's estimate
(`MatchContext.enemy_army_estimate`), held to counterattack_seen_share times
the cost of the enemy units that fight seen within counterattack_seen_seconds,
since an estimate grown on unseen income alone keeps main home for nothing.

Its target (`targets`) is an exposed enemy expansion first (see
`exposed_expansion`), else the enemy building nearest our base that no known
enemy ground defense reaches: the base's least defended edge.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math

from ucla_agent.games.redalert import exposed_expansion
from ucla_agent.games.redalert.command_targets import reaches
from ucla_agent.games.redalert.fight import Point, ground_defenses, worth
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import NO_THREAT, MatchContext
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.world import DEFENSE_TYPES, EnemyRecord, World

# Why a counterattack is due.
WON_DEFENSE = "won_defense"
REGROUPED = "regrouped"
_PERCENT = 100


class CounterWatch:
    """The defense in force and the regroup told, and the counterattack due. See the module's note."""

    def __init__(self) -> None:
        # The tick the defense in force began at, and the enemy cost killed and own cost lost before it; None while
        # there is none.
        self._since: int | None = None
        self._kills = 0
        self._deaths = 0
        # Whether the regroup in force was told already.
        self._regroup_told = False
        # Why a counterattack is due and the tick it is due until; None while none is.
        self.due: str | None = None
        self._due_until = 0

    def follow(self, gathered: bool, world: World, ctx: MatchContext) -> None:
        """Bring the watch up to date in this cycle, `gathered` whether main stands gathered at the staging point: a
        counterattack due once a defense is won or a regroup is over, until counterattack_window_seconds after."""
        tuning = ctx.tuning.combat
        if not tuning.counterattack:
            return
        if self.due is not None and world.tick > self._due_until:
            self.due = None
        reason = self._defense_won(world, ctx) or self._regroup_over(gathered, ctx)
        if reason is not None:
            self.due, self._due_until = reason, world.tick + tuning.counterattack_window_seconds * TICKS_PER_SECOND

    def taken(self) -> None:
        """The counterattack due was started."""
        self.due = None

    def _defense_won(self, world: World, ctx: MatchContext) -> str | None:
        """WON_DEFENSE in the cycle a defense won ends; None otherwise."""
        military = world.military
        if ctx.threat_active or ctx.approach_alarm:
            if self._since is None:
                self._since, self._kills, self._deaths = world.tick, military.kills_cost, military.deaths_cost
            return None
        if self._since is None:
            return None
        self._since = None
        killed, lost = military.kills_cost - self._kills, military.deaths_cost - self._deaths
        return WON_DEFENSE if killed >= ctx.tuning.combat.counterattack_min_kills and lost < killed else None

    def _regroup_over(self, gathered: bool, ctx: MatchContext) -> str | None:
        """REGROUPED in the first cycle main stands gathered at the staging point in a regroup of the commander's;
        None otherwise."""
        commander = ctx.commander
        if commander is None or not commander.is_regrouping:
            self._regroup_told = False
            return None
        if not gathered or self._regroup_told:
            return None
        self._regroup_told = True
        return REGROUPED


def may_start(main_strength: float, world: World, ctx: MatchContext) -> bool:
    """Whether a counterattack due may start: no threat on the base, no approach alarm, and main worth
    counterattack_ratio times what the enemy army is now."""
    if ctx.threat_level != NO_THREAT or ctx.threat_active or ctx.approach_alarm:
        return False
    return main_strength >= ctx.tuning.combat.counterattack_ratio * enemy_now(world, ctx)


def enemy_now(world: World, ctx: MatchContext) -> float:
    """What the enemy army is now: its estimate, held to counterattack_seen_share times the cost of the enemy units
    that fight seen within counterattack_seen_seconds; that alone while there is no estimate."""
    tuning = ctx.tuning.combat
    lately = world.tick - tuning.counterattack_seen_seconds * TICKS_PER_SECOND
    seen = sum(
        worth(record.type, _PERCENT, world)
        for record in world.enemy_memory.values()
        if is_combat_unit(record) and record.last_seen_tick >= lately
    )
    held = tuning.counterattack_seen_share * seen
    estimate = ctx.enemy_army_estimate
    return held if estimate is None else min(estimate, held)


def targets(origin: Point | None, world: World, ctx: MatchContext) -> list[EnemyRecord]:
    """A counterattack's targets in order: the exposed expansions' buildings (see `exposed_expansion`), then the enemy
    buildings but defenses no known enemy ground defense reaches, the nearest to `origin` first, then by id."""
    exposed = exposed_expansion.targets(origin, world, ctx)
    taken = {record.id for record in exposed}
    defenses = ground_defenses(world)
    open_buildings = [
        record
        for record in world.enemy_memory.values()
        if record.is_building
        and record.type not in DEFENSE_TYPES
        and record.id not in taken
        and not any(reaches(defense, (record.x, record.y), world, ctx) for defense in defenses)
    ]
    open_buildings.sort(key=lambda record: (_away(origin, record), record.id))
    return [*exposed, *open_buildings]


def _away(origin: Point | None, record: EnemyRecord) -> float:
    """How far a record stands from a point; 0 without one."""
    return 0.0 if origin is None else math.dist(origin, (record.x, record.y))
