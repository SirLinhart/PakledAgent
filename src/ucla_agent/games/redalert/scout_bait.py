"""The bait shot of a slot scout's retreat (`[scouting] scout_bait_shot`): it fires once, then runs.

A careful scout (see `scout_stance`) that turns away from what hit or
outclasses it turns away silently, and the enemy goes on to where it was
going. One shot at that enemy first makes part of it peel off and chase the
scout, away from there: the scout is bait. So with the switch on as well as
`scout_cautious`, a slot scout whose retreat begins (see `scout_retreat`,
cause `hit` or `outclassed`) fires once before it runs. Its order of that
cycle is one `attack` on the nearest enemy in sight that is a unit that fights
(never a building, so never a defense: a defense cannot chase), that it can
hurt (`fight.can_hurt`) and that stands within its weapon range against it,
ties by the lower id; within range, so the order never drives the scout toward
the enemy. The retreat's move is held back until scout_bait_shot_seconds after
the shot, and the retreat lasts as long as it would have. With no such enemy
no shot is made and the retreat runs as it would have. A scout that made a
bait shot makes no other for scout_bait_cooldown_seconds.

The scout stays in hold_fire: a stance only governs what a unit shoots at of
its own accord, not an attack it is ordered to make.
"""

from __future__ import annotations

from ucla_agent.games.redalert import cells, fight
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.world import EnemyRecord, Unit, World


def bait_target(scout: Unit, world: World) -> EnemyRecord | None:
    """The enemy a retreating scout's bait shot is fired at: of the enemy units that fight in sight, those it can hurt
    and has within its weapon range against them, the nearest, ties by the lower id; None when there is none."""
    damage = world.items.damage
    weapon, position = damage.get(scout.type), (scout.x, scout.y)
    within = [
        record
        for record in world.enemy_memory.values()
        if record.visible
        and is_combat_unit(record)
        and fight.can_hurt(scout.type, record.type, world)
        and cells.is_within(position, (record.x, record.y), weapon.range_to(damage.get(record.type)))
    ]
    return min(within, key=lambda record: (cells.distance(position, (record.x, record.y)), record.id), default=None)


class BaitShots:
    """The bait shots of one match's slot scouts. See the module's note."""

    def __init__(self) -> None:
        # Unit id -> (tick, target id) of the scout's last bait shot.
        self._shots: dict[int, tuple[int, int]] = {}

    def shoot(self, scout: Unit, world: World, ctx: MatchContext) -> EnemyRecord | None:
        """The target of the bait shot of a scout whose retreat begins in this cycle, the shot noted; None when no shot
        is made: the switch is off, the scout's last shot is not scout_bait_cooldown_seconds old, or no enemy is a
        target."""
        tuning = ctx.tuning.scouting
        if not tuning.scout_bait_shot:
            return None
        last = self._shots.get(scout.id)
        cooldown_ticks = tuning.scout_bait_cooldown_seconds * TICKS_PER_SECOND
        if last is not None and world.tick - last[0] < cooldown_ticks:
            return None
        target = bait_target(scout, world)
        if target is not None:
            self._shots[scout.id] = (world.tick, target.id)
        return target

    def shot_now(self, unit_id: int, world: World) -> EnemyRecord | None:
        """The target of the bait shot a scout makes in this cycle; None when it makes none."""
        last = self._shots.get(unit_id)
        return world.enemy_memory[last[1]] if last is not None and last[0] == world.tick else None

    def holds_back(self, unit_id: int, world: World, ctx: MatchContext) -> bool:
        """Whether a scout's move is still held back after its bait shot: less than scout_bait_shot_seconds ago."""
        last = self._shots.get(unit_id)
        held_ticks = ctx.tuning.scouting.scout_bait_shot_seconds * TICKS_PER_SECOND
        return last is not None and world.tick < last[0] + held_ticks
