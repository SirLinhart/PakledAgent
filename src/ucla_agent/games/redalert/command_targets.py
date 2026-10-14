"""The local targets of the model commander's question for main: the enemy near main's body, and our chance against it.

A small model told only how strong main is against everything near it at
once cannot tell a fight it should take from one it should not. So main's
question (see `command_army`) lists, after `fight_here:`, the targets near
main's body one by one, each with our chance against it, the figures those
of `[commander]`:

- **Groups.** The remembered enemy units that count in a fight (see
  `fight.is_counted`: in sight, or seen lately), but aircraft, within
  target_reach_cells of the body's center, joined by single link: two units
  are of one group when a chain of units, each within target_group_cells of
  the next, joins them. Each group is a target, at its centroid.
- **Defenses.** Each remembered enemy defense that fires at ground units
  (`fight.ground_defenses`; never the AA gun or the SAM site) within
  target_reach_cells of the center is a target of its own.

They are sorted by their distance from the body's center, and the first
targets_max are kept, named T1, T2 and so on in that order.

- **The over-extended field.** With `strike_overextended` of `[combat]` on,
  the first over-extended field (see `overextended`), the one main's strike
  would take first, comes after them however far it is: its refinery and
  harvesters, at its lead's cell, weighed as the strike weighs it
  (`field_weighed_with`).

**Our chance.** Against a target, the body's units that can hurt it (see
`fight.ratio_against_target`, the damage table's word) are weighed against
what stands with the target, each side as the estimate of a fight weighs it:

- a group, its units, and each ground defense whose weapon range and
  `DEFENSE_REACH_MARGIN_CELLS` reach the group's centroid, at defense_weight
  of `[combat]` times its cost;
- a defense, itself at defense_weight times its cost, the enemy units within
  `DEFENSE_SUPPORT_CELLS` of it, and each other ground defense whose weapon
  range and `DEFENSE_REACH_MARGIN_CELLS` reach it: defenses that stand
  together are taken together, one under the fire of the others.

The ratio r found is the chance round(100 r^2 / (1 + r^2)) percent, to the
nearest multiple of `CHANCE_STEP`, kept between `MIN_CHANCE` and
`MAX_CHANCE`: even odds at a ratio of 1, and never a certainty either way.

The block reads `targets:` and one line for each target,

    T1: 6 rifle infantry, 2 light tank at (52,47), 9 cells, value 1,800, win 80%
    T2: pillbox at (60,44), 12 cells, win 35%

the group's units by type, its cell, its distance and what its units are
worth (cost times health); or `targets: none`. The over-extended field
reads

    T3: over-extended field: 1 ore refinery, 2 ore truck at (74,13), 55 cells, win 80%

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Sequence

from ucla_agent.games.redalert import cells, overextended
from ucla_agent.games.redalert.command_prompt import NONE, Detail, at, counts, named
from ucla_agent.games.redalert.fight import Point, centroid, ground_defenses, is_counted, ratio_against_target, strength
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

# The kinds of target.
GROUP = "group"
DEFENSE = "defense"
FIELD = "field"
# A defense counts where its weapon range and this many cells reach.
DEFENSE_REACH_MARGIN_CELLS = 2
# A defense target is weighed with the enemy units within this many cells of it.
DEFENSE_SUPPORT_CELLS = 8
# The chance is a whole multiple of CHANCE_STEP percent from MIN_CHANCE to MAX_CHANCE.
CHANCE_STEP = 5
MIN_CHANCE = 5
MAX_CHANCE = 95
_PERCENT = 100


@dataclass(frozen=True)
class LocalTarget:
    """A target near main's body: an enemy group or a defense, what it is weighed with, and our chance against it."""

    kind: str
    # The actors of the target: the group's units, or the defense alone.
    members: tuple[EnemyRecord, ...]
    # Its cell (a group's centroid, a defense's own) and its distance from the body's center, in cells.
    cell: Cell
    distance: float
    # What its strength counts: enemy units, and defenses at defense_weight times their cost.
    enemies: tuple[EnemyRecord, ...]
    defenses: tuple[EnemyRecord, ...]
    # Our win chance against it, in percent.
    chance: int = 0


def local_targets(body: Sequence[Unit], center: Cell, world: World, ctx: MatchContext) -> tuple[LocalTarget, ...]:
    """The targets near main's body (`body`, its center `center`), the nearest first, at most targets_max of them, each
    with our chance against it. See the module's note."""
    tuning = ctx.tuning.commander
    reach = tuning.target_reach_cells
    hostile = ground_enemies(world)
    near = [record for record in hostile if cells.is_within(center, _cell(record), reach)]
    grounded = ground_defenses(world)
    groups = [_group(members, grounded, world, ctx) for members in single_link(near, tuning.target_group_cells)]
    defenses = [
        _defense(defense, hostile, grounded, world, ctx)
        for defense in grounded
        if cells.is_within(center, _cell(defense), reach)
    ]
    placed = [replace(target, distance=math.dist(center, centroid(target.members))) for target in [*groups, *defenses]]
    found = sorted(
        placed, key=lambda target: (target.distance, target.kind, min(record.id for record in target.members))
    )
    kept = [*found[: tuning.targets_max], *_field(center, world, ctx)]
    return tuple(replace(target, chance=chance_of(body, target, world, ctx)) for target in kept)


def field_weighed_with(cell: Cell, world: World, ctx: MatchContext) -> tuple[list[EnemyRecord], list[EnemyRecord]]:
    """What the refinery and harvesters of an over-extended field are weighed with, their lead at `cell`: the enemy
    units in sight within DEFENSE_SUPPORT_CELLS of it, and every ground defense whose weapon range and
    DEFENSE_REACH_MARGIN_CELLS reach it."""
    support = [
        record
        for record in ground_enemies(world)
        if record.visible and cells.is_within(cell, _cell(record), DEFENSE_SUPPORT_CELLS)
    ]
    covering = [defense for defense in ground_defenses(world) if reaches(defense, cell, world, ctx)]
    return support, covering


def ground_enemies(world: World) -> list[EnemyRecord]:
    """The remembered enemy units that count in a fight, but aircraft: those a group target is made of."""
    return [
        record
        for record in world.enemy_memory.values()
        if is_counted(record) and not world.items.damage.get(record.type).air
    ]


def group_target(members: Sequence[EnemyRecord], world: World, ctx: MatchContext) -> LocalTarget:
    """A group target of some enemy units, as main's question weighs one, with no distance and no chance."""
    return _group(tuple(members), ground_defenses(world), world, ctx)


def defense_target(defense: EnemyRecord, world: World, ctx: MatchContext) -> LocalTarget:
    """A defense target, as main's question weighs one, with no distance and no chance."""
    return _defense(defense, ground_enemies(world), ground_defenses(world), world, ctx)


def single_link(records: Sequence[EnemyRecord], link: float) -> list[tuple[EnemyRecord, ...]]:
    """The records in groups joined by single link: two of one group when a chain of records, each within `link`
    cells of the next, joins them. The groups come in the order of their first record, each in the order its records
    were joined."""
    left = list(records)
    groups = []
    while left:
        group = [left.pop(0)]
        joined = 0
        # Each record joined is looked around in turn, so that the chain grows until no record left is near it.
        while joined < len(group):
            here = _cell(group[joined])
            near = [other for other in left if cells.is_within(here, _cell(other), link)]
            near_ids = {other.id for other in near}
            left = [other for other in left if other.id not in near_ids]
            group.extend(near)
            joined += 1
        groups.append(tuple(group))
    return groups


def win_chance(ratio: float) -> int:
    """Our chance against a target, in percent, at a ratio of strengths: round(100 r^2 / (1 + r^2)) to the nearest
    multiple of CHANCE_STEP, a half up, kept between MIN_CHANCE and MAX_CHANCE."""
    squared = ratio * ratio
    percent = _PERCENT * squared / (1 + squared)
    stepped = math.floor(percent / CHANCE_STEP + 0.5) * CHANCE_STEP
    return min(MAX_CHANCE, max(MIN_CHANCE, stepped))


def chance_of(units: Iterable[Unit], target: LocalTarget, world: World, ctx: MatchContext) -> int:
    """The chance of the units given against a target: those that can hurt it weighed against what its strength
    counts."""
    ratio = ratio_against_target(units, target.members, world, ctx.tuning, target.enemies, target.defenses)
    return win_chance(ratio)


def targets_lines(targets: Sequence[LocalTarget], names: Mapping[str, str], world: World, detail: Detail) -> list[str]:
    """The `targets:` block of main's question: its heading and a line for each target, or `targets: none`."""
    if not targets:
        return [f"targets: {NONE}"]
    return [
        "targets:",
        *(f"T{number}: {_text(target, names, world, detail)}" for number, target in enumerate(targets, 1)),
    ]


def reaches(defense: EnemyRecord, point: Point, world: World, ctx: MatchContext) -> bool:
    """Whether a defense's weapon range and DEFENSE_REACH_MARGIN_CELLS reach a point."""
    reach = range_cells(defense.type, world, ctx) + DEFENSE_REACH_MARGIN_CELLS
    return math.hypot(point[0] - defense.x, point[1] - defense.y) <= reach


def range_cells(unit_type: str, world: World, ctx: MatchContext) -> float:
    """How far a type shoots, in cells, as the item table says; the range taken for an unknown defense otherwise."""
    facts = world.items.get(unit_type)
    return facts.range if facts is not None and facts.range is not None else ctx.tuning.coverage.unknown_range_cells


def _group(
    members: tuple[EnemyRecord, ...], grounded: list[EnemyRecord], world: World, ctx: MatchContext
) -> LocalTarget:
    """A group target: its units, weighed with the ground defenses that reach its centroid."""
    middle = centroid(members)
    covering = tuple(defense for defense in grounded if reaches(defense, middle, world, ctx))
    return LocalTarget(GROUP, members, cells.nearest_cell(*middle), 0.0, members, covering)


def _defense(
    defense: EnemyRecord, hostile: list[EnemyRecord], grounded: list[EnemyRecord], world: World, ctx: MatchContext
) -> LocalTarget:
    """A defense target: the defense, weighed with the enemy units within DEFENSE_SUPPORT_CELLS of it and the other
    ground defenses that reach it."""
    cell = _cell(defense)
    support = tuple(record for record in hostile if cells.is_within(cell, _cell(record), DEFENSE_SUPPORT_CELLS))
    covering = tuple(other for other in grounded if other.id != defense.id and reaches(other, cell, world, ctx))
    return LocalTarget(DEFENSE, (defense,), cell, 0.0, support, (defense, *covering))


def _field(center: Cell, world: World, ctx: MatchContext) -> list[LocalTarget]:
    """The first over-extended field as a target, at its distance from the body's center `center`; none while there is
    none."""
    fields = overextended.targets(world, ctx)
    if not fields:
        return []
    first = fields[0]
    enemies, defenses = field_weighed_with(first.cell, world, ctx)
    distance = math.dist(center, first.cell)
    return [LocalTarget(FIELD, first.records, first.cell, distance, tuple(enemies), tuple(defenses))]


def _text(target: LocalTarget, names: Mapping[str, str], world: World, detail: Detail) -> str:
    """A target's line after its name: what it is, its cell and distance, a group's value, and our chance."""
    distance = round(target.distance)
    if target.kind == FIELD:
        actors = counts((named(record.type, names) for record in target.members), detail.unit_types)
        return f"over-extended field: {actors} at {at(target.cell)}, {distance} cells, win {target.chance}%"
    if target.kind == DEFENSE:
        (defense,) = target.members
        return f"{named(defense.type, names)} at {at(target.cell)}, {distance} cells, win {target.chance}%"
    by_type = counts((named(record.type, names) for record in target.members), detail.unit_types)
    value = round(strength(target.members, world))
    return f"{by_type} at {at(target.cell)}, {distance} cells, value {value:,}, win {target.chance}%"


def _cell(record: EnemyRecord) -> Cell:
    return record.x, record.y
