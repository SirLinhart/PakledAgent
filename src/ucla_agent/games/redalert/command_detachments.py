"""The model commander's detachments: a few of main's units the script runs on one job apart from the rest of main.

When the model picks a task for main (`run over`, `kill`, `hit`; see
`command_tasks`) or `raid trucks` (see `command_army`), the commander keeps
the pick apart from main's standing action (see `commander`), and the combat
manager forms a detachment from it, at most detachments_max of `[commander]`
out at once. While that many are out, main's question offers no task and no
raid trucks. A raid is one kind of detachment, with its own rules (see
`command_raid`); a task detachment is made of the units its option named
that are still main's and on no other detachment, and the script runs it
until it ends:

- **run over:** the named tanks are driven through the cluster as a crush run
  drives them (see `crush`: each to the cell past the cluster's centroid, let
  go once there, the run over once none is left on its way, its cluster
  broken or its 8 seconds out), whatever `crush_runs` says. While one is out
  it is the one crush run: the combat manager starts none of the script's
  (`MatchContext.run_over_out`);
- **kill defense:** the named long-range units fire on the defense from their
  firing band (`siege.Siege.fire_on`);
- **hit group:** the named units are handed targets among the group's units
  alone, as the focus fire hands them out (`targeting.FocusFire`, with no
  chase), and those given none attack-move to the group's centroid.

**Ending.** A task detachment ends, and its units rejoin main, on the first
of these, as the reason of its `task_end` event says:

- `done`: its target is destroyed or gone (no unit of the group is
  remembered as one that counts in a fight, the defense seen destroyed: no
  longer remembered, having been in sight in the cycle before), or the run is
  over by its cluster broken or its tanks through;
- `failed`: half its units are lost, or its own chance against the target,
  as the task option weighs it (`command_tasks.task_chance`, with the units
  still out), has stayed under task_fail_chance for task_fail_ticks. A kill
  defense fails as well once its defense has left the enemy memory out of
  sight, which no kill of it shows (a building is forgotten when an own unit
  near its cell does not see it), and once either of these has held for
  task_fail_ticks in a row: the firing band of every one of its units
  against the defense empty (see `command_tasks.has_band`), or the defense
  not seen for kill_defense_seen_seconds of `[commander]`. Each has a clock
  of its own, so that one giving way to the other starts afresh. Artillery
  whose band is empty
  only stands idle far from its target, and a defense unseen that long may
  be gone;
- `timeout`: the run's 8 seconds, kill_defense_seconds, or
  hit_group_seconds have passed.

**One detachment a target.** A detachment is keyed by the ids of the
enemies it is on (`targets`): a task's, those its option named (the
cluster's infantry, the defense, the group's units); a raid's, the
harvesters of its opportunity. While it is out, main's question offers no
task and no raid trucks on any of them (see `command_tasks`, `command_army`),
and since a pick is taken only while the question of the cycle it arrives in
offers it (see `commander`), no second detachment of any kind forms on an
enemy one is on.

**Isolation.** The units of a detachment are main's but take none of main's
orders while it is out: the commander's question for main, its forward gate's
body and value, `fight_here`, main's commanded orders, the gathering and the
joining units leave them out. Only two things take them back early, every
detachment ending at once: the script's retreat override, after which they go
to the staging point with main (`pulled_back`); and base defense calling
main home or taking it over against a major threat (`recalled`). A task whose
units have all left main otherwise ends `recalled` too.

**Records.** A task detachment is a `commander` event of kind `task_start`
when it forms, with its kind (`task`), its target's cell and enemies (`aim`),
its units and our chance, and one of kind `task_end` when it ends, with its
kind, the reason, and the value it killed (the enemies it was on seen
destroyed while it was out: those that left the enemy memory in a cycle after
one they were in sight in) and lost (its units that died). A raid's events
are its own (`raid`, `rejoined`).

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Protocol, Sequence

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, crush, siege, targeting
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.command_army import RAID_TRUCKS
from ucla_agent.games.redalert.command_prompt import Action
from ucla_agent.games.redalert.command_raid import TruckRaid
from ucla_agent.games.redalert.command_targets import LocalTarget, defense_target, group_target
from ucla_agent.games.redalert.command_tasks import KILL_DEFENSE, RUN_OVER, has_band, is_seen_lately, task_chance
from ucla_agent.games.redalert.commander import COMMANDER_EVENT, DROPPED, MAIN_ROLE, NOT_OFFERED, TaskPick
from ucla_agent.games.redalert.fight import centroid, is_counted, worth
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import DetachedUnits, MatchContext
from ucla_agent.games.redalert.world import EnemyRecord, Unit, World

# The kinds of a detachment's `commander` events, and why a task detachment ends.
TASK_START = "task_start"
TASK_END = "task_end"
DONE = "done"
FAILED = "failed"
TIMEOUT = "timeout"
PULLED_BACK = "pulled_back"
RECALLED = "recalled"
_FULL_HEALTH = 100


class Detachment(Protocol):
    """A detachment in force, of whatever kind."""

    kind: str

    @property
    def units(self) -> frozenset[int]:
        """Its units out of main."""

    @property
    def targets(self) -> frozenset[int]:
        """The ids of the enemies it is on."""

    def follow(self, main: Mapping[int, Unit], world: World, ctx: MatchContext) -> str | None:
        """Bring it up to date with this cycle and end it when it is over; the reason it ended, or None."""

    def end(self, reason: str, world: World) -> None:
        """Write the event of its end, for a reason."""

    def orders(self, main: Mapping[int, Unit], world: World, ctx: MatchContext) -> list[Intent]:
        """Its units' orders in this cycle."""


class Detachments:
    """The commander's detachments out of main. See the module's note."""

    def __init__(self) -> None:
        self._out: list[Detachment] = []
        # The tick of the latest pick seen, so that a pick forms one detachment at the most.
        self._seen_tick: int | None = None

    @property
    def units(self) -> frozenset[int]:
        """The units of every detachment out."""
        return frozenset().union(*(detachment.units for detachment in self._out))

    @property
    def targets(self) -> frozenset[int]:
        """The ids of the enemies every detachment out is on."""
        return frozenset().union(*(detachment.targets for detachment in self._out))

    @property
    def count(self) -> int:
        """How many detachments are out."""
        return len(self._out)

    @property
    def out(self) -> tuple[DetachedUnits, ...]:
        """Every detachment out: its kind, its units and the enemies it is on, in the order they formed."""
        return tuple(DetachedUnits(detachment.kind, detachment.units, detachment.targets) for detachment in self._out)

    @property
    def is_running_over(self) -> bool:
        """Whether a run-over detachment is out, which counts as the one crush run."""
        return any(detachment.kind == RUN_OVER for detachment in self._out)

    def follow(
        self, pick: TaskPick | None, main: Mapping[int, Unit], taken_back: str | None, world: World, ctx: MatchContext
    ) -> bool:
        """Bring the detachments up to date in this cycle, main's units being `main`: every one ended when main is
        taken back (`taken_back`, the reason), else one formed at a new pick while fewer than detachments_max are out
        and those that are over ended. Whether one ended."""
        fresh = pick if pick is not None and pick.tick != self._seen_tick else None
        if pick is not None:
            self._seen_tick = pick.tick
        if taken_back is not None:
            for detachment in self._out:
                detachment.end(taken_back, world)
            ended, self._out = bool(self._out), []
            return ended
        if fresh is not None and self.count < ctx.tuning.commander.detachments_max:
            self._form(fresh, main, world, ctx)
        going_on = [detachment for detachment in self._out if detachment.follow(main, world, ctx) is None]
        ended, self._out = len(going_on) < len(self._out), going_on
        return ended

    def orders(self, main: Mapping[int, Unit], world: World, ctx: MatchContext) -> list[Intent]:
        """The orders of every detachment's units in this cycle."""
        return [intent for detachment in self._out for intent in detachment.orders(main, world, ctx)]

    def _form(self, pick: TaskPick, main: Mapping[int, Unit], world: World, ctx: MatchContext) -> None:
        """A detachment of a pick from main's units on no detachment, when there is one to form. A run over picked
        while a crush run of the script's is in force is dropped, with a `dropped` event: the question that offered it
        saw the run of an earlier cycle, and two runs may not overlap."""
        if pick.action.kind == RUN_OVER and ctx.crush_run:
            _log_dropped(pick, world)
            return
        busy = self.units
        free = {unit_id: unit for unit_id, unit in main.items() if unit_id not in busy}
        formed = (
            TruckRaid.formed(pick, free, world)
            if pick.action.kind == RAID_TRUCKS
            else Task.formed(pick.action, free, world, ctx)
        )
        if formed is not None:
            self._out.append(formed)


class Job(Protocol):
    """What one kind of task does with its units: when it is over, what it is against, and the orders."""

    def units(self, alive: frozenset[int]) -> frozenset[int]:
        """Its units out of main, of the named ones still alive."""

    def follow(self, active: Mapping[int, Unit], world: World, ctx: MatchContext) -> str | None:
        """DONE, FAILED or TIMEOUT when the job is over by its own rules in this cycle, `active` its units in main;
        None otherwise."""

    def target(self, world: World, ctx: MatchContext) -> LocalTarget:
        """Its target as it stands now, while the job is not over."""

    def limit_ticks(self, ctx: MatchContext) -> int | None:
        """How long it may last; None when its own rules end it in time."""

    def orders(self, units: list[Unit], world: World, ctx: MatchContext) -> list[Intent]:
        """Its units' orders in this cycle."""


class Task:
    """A task detachment: the units named by a picked task option, on its job, and what it has killed and lost."""

    def __init__(self, kind: str, job: Job, units: Sequence[Unit], aim: Mapping[int, str], tick: int) -> None:
        self.kind = kind
        self._job = job
        # The units named, by id, with their types, and those of them still alive.
        self._named = {unit.id: unit.type for unit in units}
        self._alive = frozenset(self._named)
        # The enemies it is on, and those of them still remembered, by id, with their types, and of these the ones in
        # sight in the latest cycle.
        self.targets = frozenset(aim)
        self._remaining = dict(aim)
        self._in_sight: frozenset[int] = frozenset()
        self._started = tick
        # The first tick of the cycles in a row in which its chance was under task_fail_chance; None for none.
        self._low_since: int | None = None
        self._killed = 0.0
        self._lost = 0.0

    @classmethod
    def formed(cls, action: Action, free: Mapping[int, Unit], world: World, ctx: MatchContext) -> Task:
        """The detachment of a task option picked in this cycle, with a `task_start` event. The option was built in
        this cycle on main's units on no detachment (`free`), so its units and its target are there."""
        named = [free[unit_id] for unit_id in action.units]
        job = _job(action, named, world, ctx)
        # A run over takes those of the named tanks its run sends through the cluster: all of them, as the option was
        # built on the run's own choice of tanks.
        held = job.units(frozenset(unit.id for unit in named))
        units = [unit for unit in named if unit.id in held]
        aim = {enemy_id: world.enemy_memory[enemy_id].type for enemy_id in action.aim}
        task = cls(action.kind, job, units, aim, world.tick)
        event = {
            "kind": TASK_START,
            "task": action.kind,
            "target": list(action.cell),
            "aim": sorted(aim),
            "units": sorted(task.units),
            "chance": action.chance,
            "tick": world.tick,
        }
        records.log(COMMANDER_EVENT, event)
        return task

    @property
    def units(self) -> frozenset[int]:
        """Its units out of main: those named and alive that its job still holds."""
        return self._job.units(self._alive)

    def follow(self, main: Mapping[int, Unit], world: World, ctx: MatchContext) -> str | None:
        """Count what it killed and lost, and end it when it is done, has failed or has run out of time, or when none
        of its units is left in main; the reason, or None while it goes on."""
        self._count(world)
        active = {unit_id: main[unit_id] for unit_id in self.units if unit_id in main}
        reason = self._job.follow(active, world, ctx) or self._failed(active, world, ctx) or self._timed_out(world, ctx)
        if reason is None and not active:
            reason = RECALLED
        if reason is not None:
            self.end(reason, world)
        return reason

    def end(self, reason: str, world: World) -> None:
        """Write the `task_end` event, for a reason."""
        event = {
            "kind": TASK_END,
            "task": self.kind,
            "reason": reason,
            "killed": round(self._killed),
            "lost": round(self._lost),
            "tick": world.tick,
        }
        records.log(COMMANDER_EVENT, event)

    def orders(self, main: Mapping[int, Unit], world: World, ctx: MatchContext) -> list[Intent]:
        units = [main[unit_id] for unit_id in sorted(self.units) if unit_id in main]
        return self._job.orders(units, world, ctx) if units else []

    def _count(self, world: World) -> None:
        """Count the value of the named units that died and of the enemies it is on seen destroyed: gone from the enemy
        memory after a cycle they were in sight in."""
        dead = [unit_id for unit_id in self._alive if unit_id not in world.units]
        self._lost += sum(worth(self._named[unit_id], _FULL_HEALTH, world) for unit_id in dead)
        self._alive = self._alive.difference(dead)
        memory = world.enemy_memory
        gone = {enemy_id: self._remaining.pop(enemy_id) for enemy_id in list(self._remaining) if enemy_id not in memory}
        destroyed = [enemy_type for enemy_id, enemy_type in gone.items() if enemy_id in self._in_sight]
        self._killed += sum(worth(enemy_type, _FULL_HEALTH, world) for enemy_type in destroyed)
        self._in_sight = frozenset(enemy_id for enemy_id in self._remaining if memory[enemy_id].visible)

    def _failed(self, active: Mapping[int, Unit], world: World, ctx: MatchContext) -> str | None:
        """FAILED once half the named units are lost, or once its chance has stayed under task_fail_chance for
        task_fail_ticks; None otherwise."""
        if 2 * (len(self._named) - len(self._alive)) >= len(self._named):
            return FAILED
        if not active:
            return None
        tuning = ctx.tuning.commander
        target = self._job.target(world, ctx)
        if task_chance(self.kind, list(active.values()), target, world, ctx) >= tuning.task_fail_chance:
            self._low_since = None
            return None
        if self._low_since is None:
            self._low_since = world.tick
        return FAILED if world.tick - self._low_since >= tuning.task_fail_ticks else None

    def _timed_out(self, world: World, ctx: MatchContext) -> str | None:
        limit = self._job.limit_ticks(ctx)
        return TIMEOUT if limit is not None and world.tick - self._started >= limit else None


class RunOver:
    """A run-over job: the named tanks driven through the cluster by a crush run."""

    def __init__(self, run: crush.Run) -> None:
        self._run = run

    def units(self, alive: frozenset[int]) -> frozenset[int]:
        return alive.intersection(self._run.going)

    def follow(self, active: Mapping[int, Unit], world: World, ctx: MatchContext) -> str | None:
        """The run kept up with its tanks in main: TIMEOUT once its time is out, DONE once its cluster broke or no
        tank is left on its way."""
        self._run.keep_up(dict(active), world)
        ended = self._run.ended(world, ctx.tuning.combat)
        if ended is None:
            return None
        return TIMEOUT if ended == crush.TIMED_OUT else DONE

    def target(self, world: World, ctx: MatchContext) -> LocalTarget:
        # A run goes on only while crush_min_infantry of its cluster stay in sight: the target is never empty.
        return group_target(_remembered((record.id for record in self._run.cluster.infantry), world), world, ctx)

    def limit_ticks(self, ctx: MatchContext) -> int | None:
        return None

    def orders(self, units: list[Unit], world: World, ctx: MatchContext) -> list[Intent]:
        return self._run.orders()


class KillDefense:
    """A kill-defense job: the named long-range units firing on one defense from their band, whether the defense was
    in sight, and since when their bands have been empty and the defense unseen."""

    def __init__(self, defense_id: int) -> None:
        self._defense_id = defense_id
        self._siege = siege.Siege()
        # Whether the defense was in sight in the latest cycle it was remembered in, set in every such cycle.
        self._was_in_sight = False
        # The first tick of the cycles in a row in which every unit's band was empty, and of those in which the defense
        # had not been seen for kill_defense_seen_seconds; None while it is not so.
        self._band_empty_since: int | None = None
        self._unseen_since: int | None = None

    def units(self, alive: frozenset[int]) -> frozenset[int]:
        return alive

    def follow(self, active: Mapping[int, Unit], world: World, ctx: MatchContext) -> str | None:
        """DONE once the defense has left the enemy memory after a cycle in sight; FAILED once it has left it out of
        sight, or once every unit's band has been empty, or the defense unseen kill_defense_seen_seconds, for
        task_fail_ticks in a row; None otherwise."""
        defense = world.enemy_memory.get(self._defense_id)
        if defense is None:
            return DONE if self._was_in_sight else FAILED
        self._was_in_sight = defense.visible
        tick = world.tick
        self._band_empty_since = _since(self._band_empty_since, _bands_empty(defense, active, world, ctx), tick)
        self._unseen_since = _since(self._unseen_since, not is_seen_lately(defense, world, ctx), tick)
        held = [since for since in (self._band_empty_since, self._unseen_since) if since is not None]
        return FAILED if any(tick - since >= ctx.tuning.commander.task_fail_ticks for since in held) else None

    def target(self, world: World, ctx: MatchContext) -> LocalTarget:
        return defense_target(world.enemy_memory[self._defense_id], world, ctx)

    def limit_ticks(self, ctx: MatchContext) -> int | None:
        return ctx.tuning.commander.kill_defense_seconds * TICKS_PER_SECOND

    def orders(self, units: list[Unit], world: World, ctx: MatchContext) -> list[Intent]:
        return self._siege.fire_on(units, world.enemy_memory[self._defense_id], world, ctx.tuning)


class HitGroup:
    """A hit-group job: the named units' fire handed out over the group's units alone."""

    def __init__(self, group: Sequence[int]) -> None:
        self._group = tuple(group)
        self._focus = targeting.FocusFire()

    def units(self, alive: frozenset[int]) -> frozenset[int]:
        return alive

    def follow(self, active: Mapping[int, Unit], world: World, ctx: MatchContext) -> str | None:
        return None if _remembered(self._group, world) else DONE

    def target(self, world: World, ctx: MatchContext) -> LocalTarget:
        return group_target(_remembered(self._group, world), world, ctx)

    def limit_ticks(self, ctx: MatchContext) -> int | None:
        return ctx.tuning.commander.hit_group_seconds * TICKS_PER_SECOND

    def orders(self, units: list[Unit], world: World, ctx: MatchContext) -> list[Intent]:
        """Attacks on the group's units for the units that have one in reach, an attack-move to the group's centroid
        for the others."""
        members = _remembered(self._group, world)
        aimed = frozenset(record.id for record in members)
        fire = self._focus.orders(
            units, centroid(units), world, ctx.tuning, lambda record: record.id in aimed, chase=False
        )
        firing = {unit_id for intent in fire for unit_id in intent.order["units"]}
        rest = [unit.id for unit in units if unit.id not in firing]
        middle = list(cells.nearest_cell(*centroid(members)))
        moving = [Intent(Priority.MOVE, {"cmd": "attack_move", "units": rest, "to": middle})] if rest else []
        return [*fire, *moving]


def _bands_empty(defense: EnemyRecord, active: Mapping[int, Unit], world: World, ctx: MatchContext) -> bool:
    """Whether the firing band of every one of a kill defense's units against their defense is empty."""
    return not any(has_band(unit_type, defense, world, ctx) for unit_type in {unit.type for unit in active.values()})


def _since(since: int | None, holds: bool, tick: int) -> int | None:
    """The first tick of the cycles in a row a condition has held in, brought up to a cycle in which it holds or not;
    None while it does not."""
    if not holds:
        return None
    return tick if since is None else since


def _job(action: Action, units: list[Unit], world: World, ctx: MatchContext) -> Job:
    """The job of a task option with its units: a run over's run starts over the cluster's infantry in sight."""
    if action.kind == KILL_DEFENSE:
        (defense_id,) = action.aim
        return KillDefense(defense_id)
    if action.kind != RUN_OVER:
        return HitGroup(action.aim)
    infantry = tuple(world.enemy_memory[enemy_id] for enemy_id in action.aim)
    cluster = crush.Cluster(infantry, centroid(infantry))
    going = crush.run_cells(cluster, units, world, ctx.terrain, ctx.tuning.combat)
    return RunOver(crush.Run(cluster, world.tick, going, tuple(going)))


def _log_dropped(pick: TaskPick, world: World) -> None:
    """Write the `dropped` event of a pick no longer offered, as the commander writes one for an answer it drops."""
    action = pick.action
    event = {
        "kind": DROPPED,
        "role": MAIN_ROLE,
        "action_id": action.id,
        "action": action.head,
        "tick": world.tick,
        "reason": NOT_OFFERED,
    }
    records.log(COMMANDER_EVENT, event)


def _remembered(enemy_ids: Iterable[int], world: World) -> list[EnemyRecord]:
    """The enemies of these ids the enemy memory still holds as units that count in a fight."""
    memory = world.enemy_memory
    return [memory[enemy_id] for enemy_id in enemy_ids if enemy_id in memory and is_counted(memory[enemy_id])]
