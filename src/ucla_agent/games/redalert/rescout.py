"""Scouting again whenever the picture of the enemy has gone stale (`[scouting] rescout`).

Without the switch a new scout is due only when no enemy building anywhere
has been seen for a while, and any building the army or a harvester passes
keeps that clock from running out. With it, three clocks are kept instead,
each of what our own units have or have not had in sight:

- the enemy base area: no square of the sight grid within base_seen_cells of
  the enemy's start seen for rescout_base_seconds;
- the enemy army: no enemy unit that fights in sight for rescout_army_seconds;
- the far ore fields: an ore field more than 25 cells from our base out of
  sight for rescout_fields_seconds. The fields nearer home are watched by the
  base itself.

A scout is due when one of them has run out, and the first that has, in that
order, is the reason of its mission. A clock of something never seen runs
from the first state of the match.

When a scout has been due for scout_train_after_seconds and no unit could be
found to send, one rifleman is trained for it, unless one is on its way
already or another manager has claimed the Infantry queue. The wait starts
again with the order, so no more than one is trained at a time.
"""

from __future__ import annotations

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.family import RIFLE_INFANTRY
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.own_fields import not_own
from ucla_agent.games.redalert.production import INFANTRY_QUEUE
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.sight import SightGrid
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import ScoutingTuning
from ucla_agent.games.redalert.world import World

# The reasons a stale picture gives a mission.
STALE_BASE = "stale_base"
STALE_ARMY = "stale_army"
STALE_FIELD = "stale_field"
# An ore field whose middle lies farther than this from our base is watched for staleness.
FAR_FIELD_CELLS = 25


def far_fields(world: World, terrain: Terrain) -> list[OrePatch]:
    """The ore fields whose middles lie more than FAR_FIELD_CELLS from our base; none while the base is not known."""
    if world.base_center is None:
        return []
    return [
        patch for patch in terrain.ore_patches if not cells.is_within(patch.center, world.base_center, FAR_FIELD_CELLS)
    ]


class StaleIntel:
    """The clocks of what has gone unseen, kept for one match. See the module's note."""

    def __init__(self) -> None:
        self.sight = SightGrid()
        # The tick of the first state taken in: the clock of what has never been seen runs from it.
        self._first_tick: int | None = None
        # The last tick an enemy unit that fights was in sight.
        self._army_seen_tick: int | None = None

    def observe(self, world: World) -> None:
        """Take in what our units have in sight in the cycle's state, and whether an enemy unit that fights is."""
        self.sight.observe(world)
        if self._first_tick is None:
            self._first_tick = world.tick
        if any(record.visible and is_combat_unit(record) for record in world.enemy_memory.values()):
            self._army_seen_tick = world.tick

    def reason(self, world: World, terrain: Terrain, tuning: ScoutingTuning) -> str | None:
        """The first clock that has run out, as the reason of a mission; None while none has. Asked once the enemy's
        start is known, after `observe` has taken in the cycle's state."""
        sight = self.sight
        if self._has_run_out(
            sight.last_seen_near(world.enemy_base, tuning.base_seen_cells), world.tick, tuning.rescout_base_seconds
        ):
            return STALE_BASE
        if self._has_run_out(self._army_seen_tick, world.tick, tuning.rescout_army_seconds):
            return STALE_ARMY
        fields_seconds = tuning.rescout_fields_seconds
        if any(
            self._has_run_out(sight.last_seen(field.center), world.tick, fields_seconds)
            for field in not_own(far_fields(world, terrain), world, tuning.own_field_cells)
        ):
            return STALE_FIELD
        return None

    def _has_run_out(self, seen_tick: int | None, tick: int, seconds: int) -> bool:
        """Whether something last seen at `seen_tick`, or never, has been out of sight `seconds` or longer."""
        since = self._first_tick if seen_tick is None else seen_tick
        return tick - since >= seconds * TICKS_PER_SECOND


class ScoutTrainer:
    """The rifleman trained for scouting when a scout has been due for a while with no unit to send. See the module's
    note."""

    def __init__(self, claimant: str) -> None:
        # The manager the trainer orders for, as it claims queues.
        self._claimant = claimant
        # The tick since which a scout has been due with no unit to send, or the last rifleman was ordered.
        self._wanting_since: int | None = None

    def intents(self, is_wanting: bool, world: World, ctx: MatchContext) -> list[Intent]:
        """The order to train one rifleman, once a scout has been wanted long enough; nothing while none is wanted."""
        if not is_wanting:
            self._wanting_since = None
            return []
        tick = world.tick
        if self._wanting_since is None:
            self._wanting_since = tick
        waited = tick - self._wanting_since >= ctx.tuning.scouting.scout_train_after_seconds * TICKS_PER_SECOND
        if not waited or not self._can_train(world, ctx):
            return []
        self._wanting_since = tick
        return [ctx.production.train(RIFLE_INFANTRY, 1, Priority.PRODUCTION)]

    def _can_train(self, world: World, ctx: MatchContext) -> bool:
        """Whether a rifleman can be ordered: on offer, none on its way, and the Infantry queue claimed by no other."""
        return (
            RIFLE_INFANTRY in world.available_production
            and ctx.production.pending(RIFLE_INFANTRY) == 0
            and not ctx.claims.is_held_by_other(INFANTRY_QUEUE, self._claimant)
        )
