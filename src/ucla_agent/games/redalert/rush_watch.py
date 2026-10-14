"""Telling an early rush from the enemy's first minutes (`[scouting] rush_detection`).

An opening that buys defenses against a rush that never comes has spent its
money on nothing. The rush watch says whether one is coming: from the start
of the match until rush_watch_until_seconds it keeps a verdict, `rush`,
`none` or `unknown`, that the opening reads from the match context.

What gives a rush away is an army on its way, any one of these:

- rush_infantry enemy infantry, or rush_vehicles enemy vehicles that fight,
  seen lately farther than rush_near_base_cells from the enemy's start;
- an enemy army worth rush_approach_value or more seen lately within
  rush_approach_cells of our base.

What the enemy builds at home does not: the Rush and the Normal bot build
their barracks and war factories at the same rates, and an army gathering at
home may be the Normal bot's. So infantry massing at the enemy's start,
rush_mass_infantry or more enemy infantry seen lately within
rush_near_base_cells of it at or before rush_mass_before_seconds, is only a
warning: `MatchContext.rush_warning` is set, which holds cash back from the
optional buildings (see `rush_counter`) and orders nothing. The warning is
given once and ends at the first of the rush verdict, the rush counter
having ordered its defenses (`MatchContext.rush_counter_ordered`), and the
end of the watch.

A rush verdict stays until the watch ends. Without a sign, the verdict is
`none` once the enemy base has been seen, and `unknown` before; a base still
unseen at rush_unknown_deadline_seconds leaves it `unknown` for good. Once
the watch has ended the verdict does not change. Every change of the verdict
is a `rush_verdict` event of the match record, with the signs seen so far;
the warning's start and end are `rush_verdict` events with `warning` true and
false.

An army on its way is seen sooner by a unit standing on its way. For the
watch, one rifleman of `main` (never one of the home guard) is moved into the
`watch` group and stands at watcher_share of the way from our base to the
enemy's start. It goes back to main when the watch ends. One that is lost is
not replaced. The first scout stands at the enemy's exit as well (see
`rush_scout`).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ucla_agent.core import records
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.family import RIFLE_INFANTRY
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.production import INFANTRY_QUEUE, VEHICLE_QUEUE
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.tuning import ScoutingTuning
from ucla_agent.games.redalert.world import Cell, EnemyRecord, World

# The group of the rifleman that watches the way in.
WATCH = "watch"
RUSH_VERDICT_EVENT = "rush_verdict"
# The verdicts.
RUSH = "rush"
NO_RUSH = "none"
UNKNOWN = "unknown"
# The signs of a rush, as a verdict names them, and the sign of the warning.
SIGN_INFANTRY_OUT = "infantry_out"
SIGN_VEHICLES_OUT = "vehicles_out"
SIGN_ARMY_APPROACHING = "army_approaching"
SIGN_INFANTRY_NEAR_BASE = "infantry_near_base"


@dataclass(frozen=True)
class ArmySeen:
    """The enemy units that fight seen lately, as the watch counts them: the infantry within rush_near_base_cells of
    the enemy's start, the infantry and the vehicles farther out, and the worth of those within rush_approach_cells of
    our base."""

    infantry_near_base: int
    infantry_out: int
    vehicles_out: int
    approaching_value: int

    def signs(self, tuning: ScoutingTuning) -> list[str]:
        """The signs of a rush these units give."""
        return [
            sign
            for sign, count, wanted in (
                (SIGN_INFANTRY_OUT, self.infantry_out, tuning.rush_infantry),
                (SIGN_VEHICLES_OUT, self.vehicles_out, tuning.rush_vehicles),
                (SIGN_ARMY_APPROACHING, self.approaching_value, tuning.rush_approach_value),
            )
            if count >= wanted
        ]


class RushWatch:
    """The verdict on an early rush, and the warning of infantry massing, kept for one match. See the module's note."""

    def __init__(self) -> None:
        self.verdict = UNKNOWN
        # The signs seen so far, in the order they were first seen.
        self.signs: list[str] = []
        # Whether the warning of infantry massing at the enemy's start is in force, and whether it was ever given.
        self.warning = False
        self._has_warned = False
        # The enemy base was still unseen at the deadline, so the verdict stays unknown without a sign.
        self._is_unknown_kept = False
        # The rifleman watching the way in; None before one is posted and once it has been released.
        self._watcher: int | None = None
        # Whether a watcher has been posted in this match: one is never replaced.
        self._has_posted = False

    def observe(self, world: World, ctx: MatchContext) -> str:
        """Take in what the cycle's world shows of the enemy, and return the verdict; it no longer changes once the
        watch has ended or while the bases are not known. The warning is kept up to date as well."""
        tuning, seconds = ctx.tuning.scouting, world.game_seconds
        if seconds >= tuning.rush_watch_until_seconds:
            self._end_warning(world)
            return self.verdict
        if world.enemy_base is None or world.base_center is None:
            return self.verdict
        seen = _army_seen(world, tuning)
        self.signs += [sign for sign in seen.signs(tuning) if sign not in self.signs]
        verdict = RUSH if self.signs else self._quiet_verdict(ctx.scout_report.enemy_base_seen, seconds, tuning)
        if verdict != self.verdict:
            records.log(RUSH_VERDICT_EVENT, {"verdict": verdict, "signs": list(self.signs), "tick": world.tick})
        self.verdict = verdict
        self._keep_warning(seen, world, ctx)
        return verdict

    def _keep_warning(self, seen: ArmySeen, world: World, ctx: MatchContext) -> None:
        """End the warning on the rush verdict or once the rush counter has ordered its defenses; give it, once in a
        match, when rush_mass_infantry enemy infantry or more are seen near the enemy's start by
        rush_mass_before_seconds and no rush has been told."""
        if self.verdict == RUSH or ctx.rush_counter_ordered:
            self._end_warning(world)
            return
        tuning = ctx.tuning.scouting
        is_massing = seen.infantry_near_base >= tuning.rush_mass_infantry
        if self._has_warned or not is_massing or world.game_seconds > tuning.rush_mass_before_seconds:
            return
        self.warning = self._has_warned = True
        self._log_warning(world)

    def _end_warning(self, world: World) -> None:
        """End the warning in force, if any, with its `rush_verdict` event."""
        if self.warning:
            self.warning = False
            self._log_warning(world)

    def _log_warning(self, world: World) -> None:
        """Write the `rush_verdict` event of the warning's start or end."""
        records.log(
            RUSH_VERDICT_EVENT,
            {"verdict": self.verdict, "warning": self.warning, "signs": [SIGN_INFANTRY_NEAR_BASE], "tick": world.tick},
        )

    def watcher_intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The watcher's order to stand at watcher_share of the way to the enemy's start, a rifleman of main posted
        first; none once it is lost, and it is released to main when the watch ends."""
        if world.game_seconds >= ctx.tuning.scouting.rush_watch_until_seconds:
            self._release(world, ctx)
            return []
        if not self._has_posted:
            self._watcher = next(
                (unit_id for unit_id in ctx.groups.members(MAIN) if world.units[unit_id].type == RIFLE_INFANTRY), None
            )
            self._post(ctx)
        if self._watcher not in world.combat_units:
            return []
        return [Intent(Priority.SCOUT, {"cmd": "move", "units": [self._watcher], "to": list(_watch_point(world, ctx))})]

    def _post(self, ctx: MatchContext) -> None:
        """Move the rifleman chosen as the watcher, if any, into the watch group."""
        if self._watcher is not None:
            ctx.groups.assign(self._watcher, WATCH)
            self._has_posted = True

    def _release(self, world: World, ctx: MatchContext) -> None:
        """Send the watcher, if it stands, back to main."""
        if self._watcher in world.combat_units:
            ctx.groups.assign(self._watcher, MAIN)
        self._watcher = None

    def _quiet_verdict(self, is_base_seen: bool, seconds: float, tuning: ScoutingTuning) -> str:
        """The verdict without a sign: none once the base is seen, unknown before and for good from the deadline."""
        if not self._is_unknown_kept and not is_base_seen and seconds >= tuning.rush_unknown_deadline_seconds:
            self._is_unknown_kept = True
        return NO_RUSH if is_base_seen and not self._is_unknown_kept else UNKNOWN


def _army_seen(world: World, tuning: ScoutingTuning) -> ArmySeen:
    """The enemy units that fight seen lately, counted near the enemy's start and out of it by the queue that makes
    them, and the worth of those near our base."""
    near_base: Counter[str | None] = Counter()
    out: Counter[str | None] = Counter()
    approaching = 0
    for record in _army(world):
        facts = world.items.get(record.type)
        if facts is None:
            # A type the item table cannot value is worth nothing and of no queue; the world reports it.
            continue
        counts = near_base if _is_near(record, world.enemy_base, tuning.rush_near_base_cells) else out
        counts[facts.queue] += 1
        if _is_near(record, world.base_center, tuning.rush_approach_cells):
            approaching += facts.cost
    return ArmySeen(near_base[INFANTRY_QUEUE], out[INFANTRY_QUEUE], out[VEHICLE_QUEUE], approaching)


def _watch_point(world: World, ctx: MatchContext) -> Cell:
    """The cell watcher_share of the way from our base to the enemy's start, inside the map."""
    base, enemy = world.base_center, world.enemy_base
    share = ctx.tuning.scouting.watcher_share
    return cells.clamped(cells.toward(base, enemy, cells.distance(base, enemy) * share), world.map_bounds)


def _army(world: World) -> list[EnemyRecord]:
    """The remembered enemy units that fight and have been seen lately."""
    return [record for record in world.enemy_memory.values() if is_combat_unit(record) and not record.stale]


def _is_near(record: EnemyRecord, cell: Cell, reach: int) -> bool:
    return cells.is_within((record.x, record.y), cell, reach)
