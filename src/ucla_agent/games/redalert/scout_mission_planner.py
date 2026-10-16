"""Which mission each designated scout is on (`[scouting] scout_missions`), and how long it keeps it.

The missions, their scores and how the scouts are assigned them together are
`scout_missions`'. A scout keeps the mission it was given until one of these:

- it has watched the place: it has stood within scout_reached_cells of the
  mission's cell for mission_observe_seconds (`reached`);
- the mission turns unsafe: no safe way to the cell is left from where the
  scout stands (`unsafe`), or a danger comes so near that the scout flees it
  (`fled`, see `scout_steering`);
- it lost health since the last state (`hurt`): that mission is then not
  given to any scout for mission_blacklist_seconds, since what hurt the scout
  is likely still there;
- its slot is held by another unit (`new`);
- another mission scores at least mission_switch_margin times its own for it
  (`switched`). A kept mission counts for that many times its score in the
  assignment, so a scout is not turned from one place to the next by small
  changes in the scores, and a better mission for the other scout can still
  move it.

With `mission_hold` on, no slot is offered a mission another slot's scout
holds, by its key or its cell: two scouts never go for the same place, and a
mission is not taken from the scout on it, which then took another and was
turned back the next cycle (in the live games of 20261016 a jeep's
destination swapped every cycle between the enemy main's edge and an
expansion watch another jeep was on). And a mission held for less than
mission_min_hold_seconds is left only for one that scores
mission_early_switch_ratio times its own or more, or once it ends as above.

A kept mission is scored by what was known of its place when the scout was
given it: once the scout is there the place is in sight, and it is to watch
it all the same. A scout that fled is next offered only the missions it has a
way to that keeps the flee distance from every danger, so that it does not
turn straight back into what it fled.

With `scout_cautious` on, every way keeps the flee distance (see
`scout_missions.options`), and a flight is remembered longer: the slot is
offered keep-off ways only, and never the mission its scout fled, until the
scout stands within scout_reached_cells of the cell of a mission it was given
since, or FLEE_MEMORY_SECONDS pass since its last flight. A scout that fled
the edge of a danger is otherwise given the same mission back as soon as it is
clear, and runs into the same danger again. Off, the flight is forgotten once
the slot takes a mission.

The planner also keeps the clock the lane mission's priority reads: the last
tick an enemy unit that fights was in sight, or the first tick it planned at
while none has been.

A big army that waits for an enemy army nobody sees idles while the enemy
grows its own (see `plans`). So while the plan rules report main big and the
enemy army unseen (`big_army_unseen_enemy` of the context), the planner forces
a refresh, at most once every REFRESH_EVERY_SECONDS: in that cycle the attack
lane and the enemy main's edge are forced missions, which the scouts are given
before any other (see `scout_missions.assign`), the change `refresh`. A scout
keeps a forced mission as forced until it ends as any mission does; later
scores do not switch it. A forced mission offered for the place a scout
already watches is taken over by that scout. With scout_missions off nothing
is forced.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Collection, Mapping

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.scout_berth import flee_cells
from ucla_agent.games.redalert.scout_missions import Mission, Option, assign, forced, missions, options
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.scout_stance import is_cautious
from ucla_agent.games.redalert.scout_steering import FLED, NEW, REACHED, UNSAFE, Dangers
from ucla_agent.games.redalert.sight import SightGrid
from ucla_agent.games.redalert.world import Cell, Unit, World

# Why a scout's mission changed, besides the reasons of `scout_steering`: it was hurt, or a better one came.
HURT = "hurt"
SWITCHED = "switched"
# Why a scout was given a forced mission (see the module's note), and how often a refresh may force them.
REFRESH = "refresh"
REFRESH_EVERY_SECONDS = 60
# With `scout_cautious` on, a flight is remembered at most this long after it: long enough for a scout to reach
# another mission, after which the danger it fled has likely moved or been seen anew.
FLEE_MEMORY_SECONDS = 60


@dataclass
class _Held:
    """The mission a slot's scout is on, as it was given, and what the planner follows of it."""

    mission: Mission
    unit_id: int
    # The scout's health in the last state.
    hp: int
    # The first tick of the scout's stay at the mission's cell; None while it is not there.
    watched_since: int | None = None
    # The tick the slot was given the mission.
    since: int = 0


@dataclass(frozen=True)
class _Flight:
    """The last flight of a slot's scout: when it fled, and the key of the mission it fled, None when it had none."""

    tick: int
    mission: tuple[str, Cell] | None


@dataclass(frozen=True)
class Assignment:
    """A slot's mission in a cycle, as the option it takes, and the reason for a mission that is new to it."""

    # The mission and its way; None while the scout has none.
    option: Option | None
    # NEW, REACHED, UNSAFE, FLED, HURT or SWITCHED for a scout given another mission or none in this cycle; None while
    # it keeps the one it has.
    change: str | None = None


class MissionPlanner:
    """The missions of one match's designated scouts. See the module's note."""

    def __init__(self) -> None:
        # Slot number -> the mission its scout is on.
        self._held: dict[int, _Held] = {}
        # Slot number -> why its last mission ended, for the next mission's change.
        self._ended: dict[int, str] = {}
        # Slot number -> the last flight of its scout, while it is remembered (see the module's note).
        self._fled: dict[int, _Flight] = {}
        # Mission key -> the tick from which it may be given again.
        self._blacklist: dict[tuple[str, Cell], int] = {}
        self._army_seen_tick: int | None = None
        # The tick of the last refresh; None while there was none.
        self._refreshed_tick: int | None = None
        # The tick of the cycle being planned.
        self._tick = 0

    def missions(self, world: World, ctx: MatchContext, sight: SightGrid) -> list[Mission]:
        """The missions of the cycle (see `scout_missions.missions`), the clock of the enemy army brought up to date;
        the attack lane and the enemy main's edge forced when a refresh is due (see the module's note)."""
        if self._army_seen_tick is None or _is_army_in_sight(world):
            self._army_seen_tick = world.tick
        found = missions(world, ctx, sight, self._army_seen_tick)
        if not self._is_refresh_due(world.tick, ctx):
            return found
        self._refreshed_tick = world.tick
        return forced(found)

    def _is_refresh_due(self, tick: int, ctx: MatchContext) -> bool:
        """Whether a refresh is due: scout_missions on, the big army facing an unseen enemy army, and no refresh within
        REFRESH_EVERY_SECONDS."""
        is_wanted = ctx.tuning.scouting.scout_missions and ctx.big_army_unseen_enemy
        last = self._refreshed_tick
        return is_wanted and (last is None or tick - last >= REFRESH_EVERY_SECONDS * TICKS_PER_SECOND)

    def plan(
        self,
        scouts: Mapping[int, Unit],
        offered: list[Mission],
        dangers: Dangers,
        world: World,
        ctx: MatchContext,
        *,
        paused: Collection[int] = (),
    ) -> dict[int, Assignment]:
        """The mission of each slot's scout in the field (`scouts`, by slot number) in this cycle, of the missions
        `offered`; a slot whose scout flees has none. The slots `paused`, whose scouts retreat (see `scout_retreat`),
        keep the missions they hold until their scouts are planned for again."""
        for slot in set(self._held) - set(scouts) - set(paused):
            del self._held[slot]
        self._tick = world.tick
        fleeing = [slot for slot, scout in scouts.items() if self._review(slot, scout, dangers, world, ctx)]
        free = {slot: scout for slot, scout in scouts.items() if slot not in fleeing}
        blacklisted = self._live_blacklist(world.tick)
        offered_to = {
            slot: self._options(slot, scout, self._open(slot, offered, ctx), blacklisted, dangers, world, ctx)
            for slot, scout in free.items()
        }
        chosen = assign(list(offered_to.values()))
        remembers = is_cautious(ctx.tuning.scouting)
        planned = {
            slot: self._take(slot, choice, free[slot], remembers_flight=remembers)
            for slot, choice in zip(offered_to, chosen)
        }
        return {**planned, **{slot: Assignment(None, FLED) for slot in fleeing}}

    def _review(self, slot: int, scout: Unit, dangers: Dangers, world: World, ctx: MatchContext) -> bool:
        """End a slot's mission when its scout watched the place long enough, was hurt or is another unit, and when it
        must flee; whether it must."""
        held = self._held.get(slot)
        self._forget_flight(slot, held, scout, world, ctx)
        ending = None if held is None else self._ending(held, scout, world, ctx)
        if ending is not None:
            self._end(slot, ending)
        nearest = dangers.nearest((scout.x, scout.y))
        must_flee = nearest is not None and nearest[1] <= flee_cells(ctx.tuning.scouting)
        if must_flee:
            self._note_flight(slot, world.tick)
            self._end(slot, FLED)
        return must_flee

    def _note_flight(self, slot: int, tick: int) -> None:
        """Remember a slot's scout fleeing at a tick, and the mission it fled: the one it holds, else the one of the
        flight it is still in."""
        held, last = self._held.get(slot), self._fled.get(slot)
        fled = held.mission.key if held is not None else None if last is None else last.mission
        self._fled[slot] = _Flight(tick, fled)

    def _forget_flight(self, slot: int, held: _Held | None, scout: Unit, world: World, ctx: MatchContext) -> None:
        """With `scout_cautious` on, forget a slot's last flight once its scout stands at the cell of the mission it
        was given since, or FLEE_MEMORY_SECONDS have passed."""
        flight = self._fled.get(slot)
        if flight is None or not is_cautious(ctx.tuning.scouting):
            return
        is_over = world.tick - flight.tick >= FLEE_MEMORY_SECONDS * TICKS_PER_SECOND
        reached = ctx.tuning.scouting.scout_reached_cells
        is_there = held is not None and cells.is_within((scout.x, scout.y), held.mission.cell, reached)
        if is_over or is_there:
            del self._fled[slot]

    def _ending(self, held: _Held, scout: Unit, world: World, ctx: MatchContext) -> str | None:
        """Why a held mission ends now: another unit holds the slot, its scout was hurt (the mission blacklisted), or it
        has watched the place long enough; None while it goes on."""
        tuning = ctx.tuning.scouting
        if scout.id != held.unit_id:
            return NEW
        if scout.hp < held.hp:
            self._blacklist[held.mission.key] = world.tick + tuning.mission_blacklist_seconds * TICKS_PER_SECOND
            return HURT
        if not cells.is_within((scout.x, scout.y), held.mission.cell, tuning.scout_reached_cells):
            held.watched_since = None
            return None
        held.watched_since = world.tick if held.watched_since is None else held.watched_since
        return REACHED if world.tick - held.watched_since >= tuning.mission_observe_seconds * TICKS_PER_SECOND else None

    def _options(
        self,
        slot: int,
        scout: Unit,
        offered: list[Mission],
        blacklisted: set[tuple[str, Cell]],
        dangers: Dangers,
        world: World,
        ctx: MatchContext,
    ) -> list[Option]:
        """The options of a slot's scout: the missions offered that it may take and, while it holds one, its own as it
        was given, weighed mission_switch_margin times its score; a held mission with no safe way left ends. With
        `scout_cautious` on, the mission its scout fled is left out while the flight is remembered."""
        held, flight = self._held.get(slot), self._fled.get(slot)
        fled = flight.mission if flight is not None and is_cautious(ctx.tuning.scouting) else None
        others = [
            mission
            for mission in offered
            if (held is None or mission.key != held.mission.key) and (fled is None or mission.key != fled)
        ]
        found = options(scout, others, dangers, world, ctx, blacklisted=blacklisted, must_keep_off=flight is not None)
        if held is None:
            return found
        own = next((mission for mission in offered if mission.forced and mission.key == held.mission.key), held.mission)
        # With `scout_cautious` on, `options` routes the kept mission by the keep-off margin as well
        # (`route` with must_keep_off=True): a way that is no longer clear by the flee distance ends it as unsafe.
        kept = options(scout, [own], dangers, world, ctx)
        if not kept:
            self._end(slot, UNSAFE)
            return found
        margin = ctx.tuning.scouting.mission_switch_margin
        found = self._early_switches(held, found, kept[0].score, world.tick, ctx)
        return [*found, *(dataclasses.replace(option, weight=option.score * margin) for option in kept)]

    def _open(self, slot: int, offered: list[Mission], ctx: MatchContext) -> list[Mission]:
        """The missions a slot may be offered: with mission_hold on, none another slot's scout holds, by its key or its
        cell; all of them with it off."""
        if not ctx.tuning.scouting.mission_hold:
            return offered
        others = [held.mission for other, held in self._held.items() if other != slot]
        keys, places = {mission.key for mission in others}, {mission.cell for mission in others}
        return [mission for mission in offered if mission.key not in keys and mission.cell not in places]

    @staticmethod
    def _early_switches(
        held: _Held, found: list[Option], own_score: float, tick: int, ctx: MatchContext
    ) -> list[Option]:
        """The options a held mission may give way to: with mission_hold on, while it is held for less than
        mission_min_hold_seconds, only those scoring mission_early_switch_ratio times its own or more; all of them
        once it is held longer, or with the switch off."""
        tuning = ctx.tuning.scouting
        is_young = tick - held.since < tuning.mission_min_hold_seconds * TICKS_PER_SECOND
        if not (tuning.mission_hold and is_young):
            return found
        return [option for option in found if option.score >= tuning.mission_early_switch_ratio * own_score]

    def _take(self, slot: int, choice: Option | None, scout: Unit, *, remembers_flight: bool) -> Assignment:
        """A slot's scout on the option chosen for it, a new mission held from now on; why its mission changed. A new
        mission ends the memory of the scout's last flight unless the memory `remembers_flight` (see the module's
        note)."""
        held = self._held.get(slot)
        if held is not None and choice is not None and choice.mission.key == held.mission.key:
            # The kept mission, forced now when a refresh forced its place.
            held.hp, held.mission = scout.hp, choice.mission
            return Assignment(choice)
        if choice is None:
            # Its own mission, if it had one, went to another scout that had nothing else.
            self._end(slot, SWITCHED)
            return Assignment(None, None if held is None else SWITCHED)
        ended = self._ended.pop(slot, NEW)
        self._held[slot] = _Held(choice.mission, scout.id, scout.hp, since=self._tick)
        if not remembers_flight:
            self._fled.pop(slot, None)
        if choice.mission.forced:
            return Assignment(choice, REFRESH)
        return Assignment(choice, ended if held is None else SWITCHED)

    def _end(self, slot: int, reason: str) -> None:
        """End a slot's mission, if it holds one, for a reason the next mission's change gives."""
        self._held.pop(slot, None)
        self._ended[slot] = reason

    def _live_blacklist(self, tick: int) -> set[tuple[str, Cell]]:
        """The keys of the missions blacklisted at a tick; those whose time is over are forgotten."""
        self._blacklist = {key: until for key, until in self._blacklist.items() if tick < until}
        return set(self._blacklist)


def _is_army_in_sight(world: World) -> bool:
    """Whether an enemy unit that fights is in sight in the world's state."""
    return any(record.visible and is_combat_unit(record) for record in world.enemy_memory.values())
