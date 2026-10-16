"""The canary alarm (`[upkeep] canary_alarm`): the money turned to defenses while the enemy's army heads for our base.

The canary's death while main's attack is out (see `canary`), or the armies
crossing (see `cross_commit`), says that the enemy's army is on its way to a
base that main is not there to hold. So the alarm puts the money into
defenses facing that army until the threat is beaten or gone.

**Start** (`MatchContext.canary_alarm`, its tick and its reason): in the
cycle a canary lost while an attack was out is seen (`canary_lost`), or a new
crossing of the armies (`armies_crossed`). A canary lost at home starts
nothing; an alarm in force is not started again and keeps its tick.

**While in force**, base defense orders a defense whenever the Defense queue
is free (see `base_defense`): the anti-infantry or the anti-armor one by what
the enemy combat units seen within SEEN_SECONDS were mostly worth (the
anti-armor one when none was seen), placed toward `goal_cell`,
canary_defense_goal_cells from our base on the line toward the enemy's
start. Upkeep's unit queues train nothing and no harvester is trained but one
for a standing refinery that has none, and no MCV, refinery beyond the
second, barracks or war factory beyond the first, radar or tech building is
ordered (`held`). Power plants, a lost core building, repairs and the
defenses go on. The alarm orders nothing for main.

**End** (the first that holds): `beaten`, a major threat declared since the
start is over; `gone`, canary_alarm_quiet_seconds since the start with no
major threat declared and no enemy army closing on our base (as
`ClosingWatch.closing_on` with closing_cells and closing_min_value of
`[base_defense]`); `army_home`, no attack out and main's centroid within
retreat_home_min_cells of `[commander]` of our base; `timeout`,
canary_alarm_max_seconds since the start. Production resumes in the same
cycle. `base_defense` events of reason `canary_alarm`, phases `start` and
`end`.
"""

from __future__ import annotations

from typing import Iterable

from ucla_agent.core import records
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.batch import Intent
from ucla_agent.games.redalert.command_threat import ClosingWatch
from ucla_agent.games.redalert.family import BARRACKS_TYPES, HARVESTER, RADAR, REFINERY, WAR_FACTORY
from ucla_agent.games.redalert.fight import centroid
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MAJOR_THREAT, NO_THREAT, MatchContext
from ucla_agent.games.redalert.rush_counter import TECH_BUILDINGS
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.world import Cell, EnemyRecord, World

BASE_DEFENSE_EVENT = "base_defense"
CANARY_ALARM = "canary_alarm"
START = "start"
END = "end"
# Why the alarm starts, and why it ends.
CANARY_LOST = "canary_lost"
ARMIES_CROSSED = "armies_crossed"
BEATEN = "beaten"
GONE = "gone"
ARMY_HOME = "army_home"
TIMEOUT = "timeout"
# The enemy combat units seen this lately tell which defense the alarm buys.
SEEN_SECONDS = 60
# The buildings held while the alarm is in force, each by how many of it may stand or be on their way.
HELD_BUILDINGS = {
    REFINERY: 2,
    WAR_FACTORY: 1,
    RADAR: 0,
    **dict.fromkeys(BARRACKS_TYPES, 1),
    **dict.fromkeys(TECH_BUILDINGS, 0),
}
TRAIN = "train"
BUILD = "build"


class CanaryAlarm:
    """The canary alarm of one match: when it starts and ends. See the module's note."""

    def __init__(self) -> None:
        # The canary loss and the crossing last taken in, by their ticks; None before any.
        self._lost_seen: int | None = None
        self._crossed_seen: int | None = None
        # Whether a major threat has been declared since the alarm started.
        self._was_major = False
        self._closing = ClosingWatch()

    def follow(self, world: World, ctx: MatchContext) -> None:
        """Bring the alarm up to date in a cycle (see the module's note), published on the match context."""
        self._closing.note(world)
        if not ctx.tuning.upkeep.canary_alarm:
            ctx.canary_alarm = False
            return
        reason = self._started_by(ctx)
        if reason is not None and not ctx.canary_alarm:
            ctx.canary_alarm, ctx.canary_alarm_since, ctx.canary_alarm_reason = True, world.tick, reason
            self._was_major = False
            _log(START, world.tick, why=reason)
        if not ctx.canary_alarm:
            return
        self._was_major = self._was_major or ctx.threat_level == MAJOR_THREAT
        ended = self._ended_by(world, ctx)
        if ended is not None:
            ctx.canary_alarm, ctx.canary_alarm_ended = False, ended
            _log(END, world.tick, why=ended, since=ctx.canary_alarm_since)

    def _started_by(self, ctx: MatchContext) -> str | None:
        """What starts the alarm in this cycle: a canary loss while an attack was out, or a new crossing; None for
        nothing. Each is taken in once."""
        reason = None
        lost = ctx.canary_lost_tick
        if lost is not None and lost != self._lost_seen:
            self._lost_seen = lost
            reason = CANARY_LOST if ctx.canary_lost_while_out else None
        crossed = ctx.armies_crossed_tick
        if crossed is not None and crossed != self._crossed_seen:
            self._crossed_seen = crossed
            reason = reason or ARMIES_CROSSED
        return reason

    def _ended_by(self, world: World, ctx: MatchContext) -> str | None:
        """Why the alarm ends in this cycle; None while it holds."""
        tuning = ctx.tuning
        elapsed = world.tick - ctx.canary_alarm_since
        if self._was_major and ctx.threat_level == NO_THREAT:
            return BEATEN
        quiet_ticks = tuning.upkeep.canary_alarm_quiet_seconds * TICKS_PER_SECOND
        if elapsed >= quiet_ticks and not self._was_major and not self._is_closing(world, ctx):
            return GONE
        if not ctx.attack_out and _is_main_home(world, ctx):
            return ARMY_HOME
        return TIMEOUT if elapsed >= tuning.upkeep.canary_alarm_max_seconds * TICKS_PER_SECOND else None

    def _is_closing(self, world: World, ctx: MatchContext) -> bool:
        """Whether an enemy army is closing on our base."""
        tuning = ctx.tuning.base_defense
        found = self._closing.closing_on(world.base_center, tuning.closing_cells, tuning.closing_min_value, world)
        return found is not None


def goal_cell(world: World, ctx: MatchContext) -> Cell:
    """The cell a defense of the alarm is placed toward: canary_defense_goal_cells from our base toward the enemy's
    start."""
    return cells.toward(world.base_center, world.enemy_base, ctx.tuning.upkeep.canary_defense_goal_cells)


def seen_enemies(world: World) -> list[EnemyRecord]:
    """The enemy combat units seen within SEEN_SECONDS, which tell which defense the alarm buys."""
    seen_ticks = SEEN_SECONDS * TICKS_PER_SECOND
    return [
        record
        for record in world.enemy_memory.values()
        if is_combat_unit(record) and world.tick - record.last_seen_tick <= seen_ticks
    ]


def held(intents: Iterable[Intent], ctx: MatchContext) -> list[Intent]:
    """Upkeep's intents of a cycle without what the alarm in force holds: every unit trained but a harvester for a
    standing refinery that has none, and the buildings of HELD_BUILDINGS beyond what may stand; all of them while it is
    not in force."""
    intents = list(intents)
    if not ctx.canary_alarm:
        return intents
    return [intent for intent in intents if not _is_held(intent.order, ctx)]


def _is_held(order: dict, ctx: MatchContext) -> bool:
    """Whether the alarm holds an order back."""
    production, item = ctx.production, order.get("item")
    if order.get("cmd") == TRAIN:
        if item != HARVESTER:
            return True
        # The order is counted among those on their way already: before it, the trucks fewer than the refineries.
        before = production.units(HARVESTER) + production.pending(HARVESTER) - order.get("count", 1)
        return before >= production.standing(REFINERY)
    most = HELD_BUILDINGS.get(item)
    return order.get("cmd") == BUILD and most is not None and production.planned(item) > most


def _is_main_home(world: World, ctx: MatchContext) -> bool:
    """Whether main's centroid stands within retreat_home_min_cells of our base; true for a main with no unit."""
    units = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
    if not units:
        return True
    middle = cells.nearest_cell(*centroid(units))
    return cells.is_within(middle, world.base_center, ctx.tuning.commander.retreat_home_min_cells)


def _log(phase: str, tick: int, **data: object) -> None:
    """Write one `base_defense` event of reason `canary_alarm`."""
    records.log(BASE_DEFENSE_EVENT, {"tick": tick, "reason": CANARY_ALARM, "phase": phase, **data})
