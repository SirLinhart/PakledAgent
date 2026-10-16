"""The approach alarm (`[base_defense] approach_alarm`): defenses and counters ordered when an enemy army is seen
heading for our base, before it arrives.

Base defense declares a threat only once an enemy that fights stands within
alarm_radius_cells of a building or threat_area_cells of the base's centre, and
its counters and its one extra defense answer only that threat. An army a scout
sees marching on the base from 30 or 40 cells out is answered by nothing until it
hits, which is too late for a defense to stand. The alarm answers the sighting.

**Start**: the enemy units that fight on the ground, last seen within
approach_lookback_seconds and within approach_alarm_cells of base_center, are
large (approach_min_infantry infantry or more, or worth approach_min_value and
approach_min_share of our combat units' worth or more) and come our way: their
centroid is nearer our base than the centroid of the same units in the oldest
sample of the lookback by APPROACH_STEP_CELLS or more, or it lies on our side of
the map (nearer our base than the enemy's start). The alarm's group is those
units; while it is in force the large groups seen within reach join it.

**While in force** (`MatchContext.approach_alarm`), base defense orders a
defense whenever the Defense queue is free, at emergency priority and so free of
the holds upkeep and the opening put on their own defenses
(`defenses_on_threat_only`, `defense_on_threat`): the anti-infantry one while
infantry is half or more of the group's worth, else the anti-armor one, a Tesla
coil waiting for its power plant; placed toward `goal_cell`, approach_goal_cells
from our base toward the group's centroid. One defense for each
approach_value_per_defense of the group's worth, rounded up, approach_defenses_max
at the most. The unit queues train the counter quota first meanwhile (see
`counter_units`).

**End** (the first that holds): `destroyed`, no unit of the group is
remembered; `gone`, none in sight within approach_alarm_cells of the base for
approach_gone_seconds; `timeout`, approach_max_seconds since the start.
`base_defense` events of reason `approach_alarm`, phases `start` and `end`, with
the group's worth, composition and distance from base_center.
"""

from __future__ import annotations

import math
from collections import Counter, deque

from ucla_agent.core import records
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.fight import centroid
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.production import INFANTRY_QUEUE
from ucla_agent.games.redalert.scout_missions import enemy_start
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.world import Cell, EnemyRecord, World

BASE_DEFENSE_EVENT = "base_defense"
APPROACH_ALARM = "approach_alarm"
START = "start"
END = "end"
# Why the alarm ends.
DESTROYED = "destroyed"
GONE = "gone"
TIMEOUT = "timeout"
# The least the group's centroid must have come nearer our base over the lookback to count as coming our way: a group
# standing still wobbles by a cell as its units shuffle.
APPROACH_STEP_CELLS = 1.0
_PERCENT = 100
# The samples kept: a minute, longer than any lookback the alarm is tuned to.
_LOOKBACK_KEPT_TICKS = 60 * TICKS_PER_SECOND


class ApproachAlarm:
    """The approach alarm of one match: when it starts and ends, and how many defenses it has ordered. See the
    module's note."""

    def __init__(self) -> None:
        # One sample a second of the cells of the enemy ground fighters in sight, the oldest first.
        self._samples: deque[tuple[int, dict[int, Cell]]] = deque()
        # The ids of the alarm's group, and the last tick one of them was in sight within reach of our base.
        self._group: set[int] = set()
        self._last_near = 0
        # The defenses ordered for the alarm in force, and how many its group calls for.
        self.ordered = 0
        self._wanted = 0

    def follow(self, world: World, ctx: MatchContext) -> None:
        """Bring the alarm up to date in a cycle (see the module's note), published on the match context."""
        self._note(world)
        tuning = ctx.tuning.base_defense
        if not tuning.approach_alarm or world.base_center is None:
            ctx.approach_alarm = False
            return
        group = self._approaching(world, ctx)
        if ctx.approach_alarm:
            self._carry(group, world, ctx)
        elif group:
            self._start(group, world, ctx)

    def is_defense_due(self) -> bool:
        """Whether the alarm in force still calls for a defense."""
        return self.ordered < self._wanted

    def _note(self, world: World) -> None:
        """Take in the cells of the enemy ground fighters in sight, a second or more after the last sample; forget the
        samples older than _LOOKBACK_KEPT_TICKS."""
        if self._samples and world.tick - self._samples[-1][0] < TICKS_PER_SECOND:
            return
        seen = {record.id: (record.x, record.y) for record in _ground_fighters(world) if record.visible}
        self._samples.append((world.tick, seen))
        while len(self._samples) > 1 and world.tick - self._samples[0][0] > _LOOKBACK_KEPT_TICKS:
            self._samples.popleft()

    def _approaching(self, world: World, ctx: MatchContext) -> list[EnemyRecord]:
        """The large enemy group within reach of our base that comes our way; empty for none."""
        tuning = ctx.tuning.base_defense
        since = world.tick - tuning.approach_lookback_seconds * TICKS_PER_SECOND
        group = [
            record
            for record in _ground_fighters(world)
            if record.last_seen_tick >= since
            and cells.is_within((record.x, record.y), world.base_center, tuning.approach_alarm_cells)
        ]
        if not group or not _is_large(group, world, ctx):
            return []
        return group if self._comes_our_way(group, world, since) else []

    def _comes_our_way(self, group: list[EnemyRecord], world: World, since: int) -> bool:
        """Whether a group's centroid came APPROACH_STEP_CELLS nearer our base since the oldest sample of the lookback
        that saw any of it, or lies nearer our base than the enemy's start."""
        base = world.base_center
        now = centroid(group)
        start = enemy_start(world)
        if start is not None and _distance(now, base) < _distance(now, start):
            return True
        ids = {record.id for record in group}
        earlier = next(
            (
                [cell for unit_id, cell in seen.items() if unit_id in ids]
                for tick, seen in self._samples
                if since <= tick < world.tick and ids.intersection(seen)
            ),
            None,
        )
        if not earlier:
            return False
        before = (sum(x for x, _ in earlier) / len(earlier), sum(y for _, y in earlier) / len(earlier))
        return _distance(before, base) - _distance(now, base) >= APPROACH_STEP_CELLS

    def _start(self, group: list[EnemyRecord], world: World, ctx: MatchContext) -> None:
        """Start the alarm on a group."""
        ctx.approach_alarm, ctx.approach_alarm_since = True, world.tick
        self._group, self._last_near, self.ordered, self._wanted = set(), world.tick, 0, 0
        self._take(group, world, ctx)
        _log(START, world, group, self._wanted)

    def _take(self, group: list[EnemyRecord], world: World, ctx: MatchContext) -> None:
        """Join a group to the alarm's, and publish where the alarm's group stands and what it calls for."""
        tuning = ctx.tuning.base_defense
        self._group.update(record.id for record in group)
        ctx.approach_alarm_cell = cells.nearest_cell(*centroid(group))
        ctx.approach_alarm_infantry = _is_infantry_led(group, world)
        wanted = math.ceil(_worth(group, world) / tuning.approach_value_per_defense)
        self._wanted = max(self._wanted, min(tuning.approach_defenses_max, max(1, wanted)))

    def _carry(self, group: list[EnemyRecord], world: World, ctx: MatchContext) -> None:
        """Carry the alarm in force on with the cycle's sighting, and end it when its group is destroyed, gone or the
        alarm has run its time."""
        tuning = ctx.tuning.base_defense
        if group:
            self._take(group, world, ctx)
        members = [world.enemy_memory[unit_id] for unit_id in self._group if unit_id in world.enemy_memory]
        reach = tuning.approach_alarm_cells
        if any(
            record.visible and cells.is_within((record.x, record.y), world.base_center, reach) for record in members
        ):
            self._last_near = world.tick
        ended = None
        if not members:
            ended = DESTROYED
        elif world.tick - self._last_near >= tuning.approach_gone_seconds * TICKS_PER_SECOND:
            ended = GONE
        elif world.tick - ctx.approach_alarm_since >= tuning.approach_max_seconds * TICKS_PER_SECOND:
            ended = TIMEOUT
        if ended is not None:
            ctx.approach_alarm = False
            _log(END, world, members, self.ordered, why=ended, since=ctx.approach_alarm_since)


def goal_cell(world: World, ctx: MatchContext) -> Cell:
    """The cell a defense of the alarm is placed toward: approach_goal_cells from our base toward the group."""
    target = ctx.approach_alarm_cell or world.enemy_base or world.base_center
    return cells.toward(world.base_center, target, ctx.tuning.base_defense.approach_goal_cells)


def _ground_fighters(world: World) -> list[EnemyRecord]:
    """The remembered enemy units that fight and do not fly."""
    return [
        record
        for record in world.enemy_memory.values()
        if is_combat_unit(record) and not world.items.damage.get(record.type).air
    ]


def _is_large(group: list[EnemyRecord], world: World, ctx: MatchContext) -> bool:
    """Whether a group is large enough to raise the alarm: approach_min_infantry infantry or more, or worth
    approach_min_value and approach_min_share of what our combat units are worth (cost by health) or more."""
    tuning = ctx.tuning.base_defense
    infantry = sum(1 for record in group if _queue(record.type, world) == INFANTRY_QUEUE)
    if infantry >= tuning.approach_min_infantry:
        return True
    worth = _worth(group, world)
    ours = sum(_cost(unit.type, world) * unit.hp / _PERCENT for unit in world.combat_units.values())
    return worth >= tuning.approach_min_value and worth >= tuning.approach_min_share * ours


def _is_infantry_led(group: list[EnemyRecord], world: World) -> bool:
    """Whether infantry is half or more of what a group is worth."""
    infantry = sum(_cost(record.type, world) for record in group if _queue(record.type, world) == INFANTRY_QUEUE)
    return 2 * infantry >= _worth(group, world)


def _worth(group: list[EnemyRecord], world: World) -> int:
    return sum(_cost(record.type, world) for record in group)


def _cost(item: str, world: World) -> int:
    facts = world.items.get(item)
    return facts.cost if facts is not None else 0


def _queue(item: str, world: World) -> str | None:
    facts = world.items.get(item)
    return facts.queue if facts is not None else None


def _distance(point: tuple[float, float], cell: Cell) -> float:
    return math.hypot(point[0] - cell[0], point[1] - cell[1])


def _log(phase: str, world: World, group: list[EnemyRecord], defenses: int, **more: object) -> None:
    """Write one `base_defense` event of reason `approach_alarm`: the group's worth, composition and distance."""
    middle = centroid(group) if group else None
    distance = None if middle is None else round(_distance(middle, world.base_center), 1)
    records.log(
        BASE_DEFENSE_EVENT,
        {
            "tick": world.tick,
            "reason": APPROACH_ALARM,
            "phase": phase,
            "value": _worth(group, world),
            "composition": dict(Counter(record.type for record in group)),
            "distance": distance,
            "defenses": defenses,
            **more,
        },
    )
