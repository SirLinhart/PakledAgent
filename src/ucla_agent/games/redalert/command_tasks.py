"""The task options of the model commander's question for main: a few of main's units sent on one target near it.

The targets block of main's question (see `command_targets`) tells the
model what is near main and our chance against each. A task option turns one
of those targets into a job for the units of main that suit it, which the
script then runs to its end apart from the rest of main. There are three
kinds, each under a letter of its own (`TASK_IDS`):

- **J, run over** (`RUN_OVER`): a group target holding at least
  crush_min_infantry of `[combat]` enemy infantry in sight within
  crush_cluster_cells of their centroid (the largest such cluster, as
  `crush.largest_cluster` finds it). Its tanks are those a crush run would
  send over that cluster (`crush.run_cells`): main's crushing vehicles at
  crush_min_hp or more within crush_reach_cells of it, the nearest first,
  min(crush_max_tanks, ceil(infantry / crush_per_tanks)) of them at most,
  whatever `crush_runs` says. Never offered while a script crush run is in
  force (`MatchContext.crush_run`), which counts as the one run.
- **K, kill defense** (`KILL_DEFENSE`): a defense target seen less than
  kill_defense_seen_seconds of `[commander]` ago, with every artillery piece
  and V2 launcher of main whose firing band against it is not empty
  (`siege.firing_band` for its type, which every known ground defense near
  the defense narrows). A unit whose band is empty, standing nowhere it could
  fire from out of every defense's reach, is not named, and with none named
  K is not offered: artillery sent on a defense another covers stood idle
  far from it. They fire on it from their band, so our chance counts the
  defense at nothing: only the enemy units within 8 cells of it
  (`command_targets.DEFENSE_SUPPORT_CELLS`) stand against them.
- **L, hit group** (`HIT_GROUP`): a group target, with up to
  `HIT_GROUP_UNITS` of main's units that counter it, the nearest to it
  first: a unit counters a group when the damage it does a second to the
  armour most of the group's units wear is at least `COUNTER_FACTOR` times
  its mean over the five armour classes of the damage table. Offered only
  while main's body without those units still holds its fight where it
  stands (`rest_holds`, which main's question gives; see
  `command_army.rest_holds`): taking the counters out of a main barely
  winning left the rest losing, and the script then pulled main and the
  task back together.

A kind is built only on a listed target; while main's forward gate is closed
(see `command_gate`) only on a target within near_attack_cells of
`[commander]` of main's centroid. Our chance of a task is that of the units
it names against its target (`command_targets.chance_of`; for K with the
defense at nothing), and a task is offered only at a chance of at least
`MIN_TASK_CHANCE`. Of each kind the one with the highest chance is offered,
of two alike the one on the nearer target. At most `TASKS_MAX` are offered,
in the order J, L, K.

Each line names its target, its units and our chance. A hit names every
type of its group, the most worth first, and at the last detail level of
main's question the first `command_prompt.SHORT_LIST_TYPES` of them and how
many more (`+2 more`); its own units by their `LINE_TYPES` most numerous
types:

    J: run over 8 infantry at (51,47) with 2 tanks, win 80%
    K: kill pillbox at (60,47) with 2 artillery, win 95%
    L: hit 4 light tank at (52,47) with 6 rocket soldier, win 60%

The units a task option names are main's, and none of them is already out on
a task: main's question is asked about the units of main left to it. Nor is
a task option offered on an enemy a detachment out is on already, whatever
its kind (`targeted`, the ids of their targets; see `command_detachments`):
a second detachment on the same defense or group only splits the fire of
the first, and leaves main the weaker for it.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from collections import Counter
from typing import Callable, Mapping, Sequence

from ucla_agent.games.redalert import cells, crush, siege
from ucla_agent.games.redalert.command_prompt import SHORT_LIST_TYPES, Action, at, by_value, counts, named
from ucla_agent.games.redalert.command_targets import DEFENSE, GROUP, LocalTarget, chance_of, win_chance
from ucla_agent.games.redalert.fight import ratio_against_target, strength, worth
from ucla_agent.games.redalert.groups import SIEGE_TYPES
from ucla_agent.games.redalert.items import ARMOUR_CLASSES, TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

RUN_OVER = "run_over"
KILL_DEFENSE = "kill_defense"
HIT_GROUP = "hit_group"
# The kinds of task, in the order they are kept when more are offered than TASKS_MAX.
TASK_KINDS = (RUN_OVER, HIT_GROUP, KILL_DEFENSE)
TASK_IDS = {RUN_OVER: "J", KILL_DEFENSE: "K", HIT_GROUP: "L"}
TASKS_MAX = 2
MIN_TASK_CHANCE = 50
HIT_GROUP_UNITS = 8
COUNTER_FACTOR = 1.5
# How many unit types a hit's line names of its own units.
LINE_TYPES = 2
ARTILLERY = "arty"
V2 = "v2rl"


def task_actions(
    targets: Sequence[LocalTarget],
    units: Sequence[Unit],
    middle: Cell,
    is_gate_open: bool,
    names: Mapping[str, str],
    world: World,
    ctx: MatchContext,
    targeted: frozenset[int] = frozenset(),
    *,
    rest_holds: Callable[[frozenset[int]], bool],
) -> list[Action]:
    """The task options on the listed targets for main's units (`units`, its centroid `middle`), in the order J, L,
    K, at most TASKS_MAX of them; with main's forward gate closed only on targets within near_attack_cells of
    `middle`, none on an enemy of `targeted`, which a detachment out is on, and an L only while `rest_holds` holds
    for its units' ids. See the module's note."""
    reach = ctx.tuning.commander.near_attack_cells
    qualifying = [target for target in targets if is_gate_open or cells.is_within(middle, target.cell, reach)]
    builders = {
        RUN_OVER: lambda target: _run_over(target, units, world, ctx),
        HIT_GROUP: lambda target: _hit_group(target, units, names, world, ctx, rest_holds),
        KILL_DEFENSE: lambda target: _kill_defense(target, units, names, world, ctx),
    }
    if ctx.crush_run:
        # A script run is in force, and it counts as the one run: no run-over detachment while it holds.
        del builders[RUN_OVER]
    offered = [_best(builder, qualifying, targeted) for builder in builders.values()]
    found = [action for action in offered if action is not None]
    return sorted(found, key=lambda action: TASK_KINDS.index(action.kind))[:TASKS_MAX]


def _best(
    build: Callable[[LocalTarget], Action | None], targets: Sequence[LocalTarget], targeted: frozenset[int]
) -> Action | None:
    """Of the options a builder makes on the targets, nearest first, but those on an enemy of `targeted`, the one with
    the highest chance (the nearer of two alike), when that chance is at least MIN_TASK_CHANCE; None otherwise."""
    built = [
        action
        for action in (build(target) for target in targets)
        if action is not None and targeted.isdisjoint(action.aim)
    ]
    best = max(built, key=lambda action: action.chance, default=None)
    return best if best is not None and best.chance >= MIN_TASK_CHANCE else None


def _run_over(target: LocalTarget, units: Sequence[Unit], world: World, ctx: MatchContext) -> Action | None:
    """J on a group target: its cluster of infantry in sight, and the tanks a crush run would send through it."""
    if target.kind != GROUP:
        return None
    combat = ctx.tuning.combat
    members = {record.id for record in target.members}
    infantry = [record for record in crush.infantry_in_sight(world) if record.id in members]
    cluster = crush.largest_cluster(infantry, combat.crush_cluster_cells)
    if cluster is None or len(cluster.infantry) < combat.crush_min_infantry:
        return None
    going = crush.run_cells(cluster, list(units), world, ctx.terrain, combat)
    if not going:
        return None
    tanks = [unit for unit in units if unit.id in going]
    cell = cells.nearest_cell(*cluster.centroid)
    chance = task_chance(RUN_OVER, tanks, target, world, ctx)
    return Action(
        kind=RUN_OVER,
        key=(RUN_OVER,),
        label=f"run over {at(cell)}",
        head=f"run over {len(cluster.infantry)} infantry at {at(cell)} with {len(going)} tanks, win {chance}%",
        cell=cell,
        units=tuple(going),
        aim=tuple(record.id for record in cluster.infantry),
        chance=chance,
    )


def _kill_defense(
    target: LocalTarget, units: Sequence[Unit], names: Mapping[str, str], world: World, ctx: MatchContext
) -> Action | None:
    """K on a defense target seen lately: main's long-range units that have a firing band against it, against the
    enemy units around it alone."""
    if target.kind != DEFENSE:
        return None
    (defense,) = target.members
    if not is_seen_lately(defense, world, ctx):
        return None
    present = {unit.type for unit in units if unit.type in SIEGE_TYPES}
    banded = {unit_type for unit_type in present if has_band(unit_type, defense, world, ctx)}
    long_range = [unit for unit in units if unit.type in banded]
    if not long_range:
        return None
    chance = task_chance(KILL_DEFENSE, long_range, target, world, ctx)
    name = named(defense.type, names)
    return Action(
        kind=KILL_DEFENSE,
        key=(KILL_DEFENSE, defense.id),
        label=f"kill {name} {at(target.cell)}",
        head=f"kill {name} at {at(target.cell)} with {len(long_range)} {_long_range_name(long_range)}, win {chance}%",
        cell=target.cell,
        units=tuple(unit.id for unit in long_range),
        aim=(defense.id,),
        chance=chance,
    )


def _hit_group(
    target: LocalTarget,
    units: Sequence[Unit],
    names: Mapping[str, str],
    world: World,
    ctx: MatchContext,
    rest_holds: Callable[[frozenset[int]], bool],
) -> Action | None:
    """L on a group target: up to HIT_GROUP_UNITS of main's units that counter it, the nearest first, while main holds
    its fight without them (`rest_holds`)."""
    if target.kind != GROUP:
        return None
    armour = majority_armour(target.members, world)
    counters = sorted(
        (unit for unit in units if is_counter(unit.type, armour, world)),
        key=lambda unit: (cells.distance((unit.x, unit.y), target.cell), unit.id),
    )[:HIT_GROUP_UNITS]
    if not counters or not rest_holds(frozenset(unit.id for unit in counters)):
        return None
    chance = task_chance(HIT_GROUP, counters, target, world, ctx)
    valued = [(named(record.type, names), worth(record.type, record.hp, world)) for record in target.members]
    hitting = counts((named(unit.type, names) for unit in counters), LINE_TYPES)
    rest = f"at {at(target.cell)} with {hitting}, win {chance}%"
    return Action(
        kind=HIT_GROUP,
        key=(HIT_GROUP,),
        label=f"hit group {at(target.cell)}",
        head=f"hit {by_value(valued)} {rest}",
        cell=target.cell,
        short_head=f"hit {by_value(valued, SHORT_LIST_TYPES, with_rest=True)} {rest}",
        units=tuple(unit.id for unit in counters),
        aim=tuple(record.id for record in target.members),
        chance=chance,
    )


def task_chance(kind: str, units: Sequence[Unit], target: LocalTarget, world: World, ctx: MatchContext) -> int:
    """Our chance of a task of a kind with the units given against its target: a kill defense's counts the defense
    at nothing, the enemy units around it alone; the others' as main's question weighs the target."""
    if kind == KILL_DEFENSE:
        return win_chance(ratio_against_target(units, target.members, world, ctx.tuning, target.enemies))
    return chance_of(units, target, world, ctx)


def has_band(unit_type: str, defense: EnemyRecord, world: World, ctx: MatchContext) -> bool:
    """Whether a long-range unit of a type has a firing band against a defense: somewhere it could fire on it from out
    of the reach of every known ground defense near it (see `siege.firing_band`)."""
    return not siege.firing_band(unit_type, (defense.x, defense.y), world, ctx.tuning.siege).is_empty


def is_seen_lately(defense: EnemyRecord, world: World, ctx: MatchContext) -> bool:
    """Whether a defense was in sight less than kill_defense_seen_seconds of `[commander]` ago."""
    return world.tick - defense.last_seen_tick < ctx.tuning.commander.kill_defense_seen_seconds * TICKS_PER_SECOND


def majority_armour(members: Sequence[EnemyRecord], world: World) -> str:
    """The armour class most of a group's units wear; of two worn by as many, the one worth more, then by name."""
    damage = world.items.damage
    worn: Counter[str] = Counter()
    worth: dict[str, float] = {}
    for record in members:
        armour = damage.get(record.type).armour
        worn[armour] += 1
        worth[armour] = worth.get(armour, 0.0) + strength((record,), world)
    return max(worn, key=lambda armour: (worn[armour], worth[armour], armour))


def is_counter(unit_type: str, armour: str, world: World) -> bool:
    """Whether a unit of a type counters an armour class: the damage it does a second to it is more than none and at
    least COUNTER_FACTOR times its mean over the armour classes of the damage table."""
    facts = world.items.damage.get(unit_type)
    done = facts.dps_against(armour)
    mean = sum(facts.dps_against(each) for each in ARMOUR_CLASSES) / len(ARMOUR_CLASSES)
    return done > 0 and done >= COUNTER_FACTOR * mean


def _long_range_name(long_range: Sequence[Unit]) -> str:
    """What a kill defense's units are called on its line: artillery, V2, or both."""
    types = {unit.type for unit in long_range}
    if types == {ARTILLERY}:
        return "artillery"
    return "V2" if types == {V2} else "artillery and V2"
