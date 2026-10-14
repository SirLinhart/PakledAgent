"""The economy target of the model commander's question for main: the enemy refinery or harvesters worth most per cell.

Main's question (see `command_army`) offers one action on the enemy's
economy while there is a target for it. Its candidates are each remembered
enemy refinery, and each group of remembered enemy harvesters not stale (in
sight, or seen lately enough for their place to count on), joined by single
link at target_group_cells of `[commander]` (see
`command_targets.single_link`). A candidate is left out when:

- the cell of any of its actors lies in the coverage of a remembered enemy
  defense that fires at ground units (`coverage.is_covered` over
  `fight.ground_defenses`: its weapon range and margin_cells of `[coverage]`);
- a detachment of the commander's is on one of its actors;
- main's body is weighed under the least ratio given against it: the units of
  the body that can hurt it against the enemy units that count in a fight,
  but aircraft, within `command_targets.DEFENSE_SUPPORT_CELLS` of it, as
  `fight.ratio_against_target` weighs them (ratio_without_enemy of `[combat]`
  with none there; 0 with no unit that can hurt it).

Of those left, the target is the one worth most per cell of the way from the
body's center: what its actors are worth (cost times health) over its
distance, taken as 1 cell at least; of two alike, the one of the lowest id.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

from ucla_agent.games.redalert import cells, coverage
from ucla_agent.games.redalert.command_prompt import counts, named
from ucla_agent.games.redalert.command_targets import DEFENSE_SUPPORT_CELLS as SUPPORT_CELLS
from ucla_agent.games.redalert.command_targets import ground_enemies, single_link
from ucla_agent.games.redalert.family import HARVESTER, REFINERY
from ucla_agent.games.redalert.fight import centroid, ground_defenses, ratio_against_target, strength
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World


@dataclass(frozen=True)
class EconomyTarget:
    """An enemy refinery or harvester group main may attack: what it is called, its actors, its cell (a group's
    centroid), its distance from the body's center, what it is worth, the body's ratio against it, and whether enemy
    units stand by it."""

    name: str
    members: tuple[EnemyRecord, ...]
    cell: Cell
    distance: float
    value: float
    ratio: float
    is_guarded: bool


def economy_target(
    body: Sequence[Unit],
    center: Cell,
    names: Mapping[str, str],
    world: World,
    ctx: MatchContext,
    least_ratio: float,
    targeted: frozenset[int] = frozenset(),
) -> EconomyTarget | None:
    """The economy target of main's body (`body`, its center `center`) at a ratio of `least_ratio` or more, none of
    whose actors is of `targeted`; None for none. See the module's note."""
    defenses = ground_defenses(world)
    found = [
        _weighed(members, body, center, names, world, ctx)
        for members in _candidates(world, ctx)
        if targeted.isdisjoint(record.id for record in members) and not _is_covered(members, defenses, world, ctx)
    ]
    able = [target for target in found if target.ratio >= least_ratio]
    return min(
        able,
        key=lambda target: (-target.value / max(1.0, target.distance), min(record.id for record in target.members)),
        default=None,
    )


def _is_covered(
    members: Sequence[EnemyRecord], defenses: Sequence[EnemyRecord], world: World, ctx: MatchContext
) -> bool:
    """Whether the cell of any of a candidate's actors lies in the coverage of one of the ground defenses given."""
    return any(coverage.is_covered(_cell(record), defenses, world.items, ctx.tuning.coverage) for record in members)


def _candidates(world: World, ctx: MatchContext) -> list[tuple[EnemyRecord, ...]]:
    """Each remembered enemy refinery alone, and the remembered harvesters not stale in groups by single link."""
    memory = world.enemy_memory.values()
    refineries = [(record,) for record in memory if record.type == REFINERY]
    harvesters = [record for record in memory if record.type == HARVESTER and not record.stale]
    return [*refineries, *single_link(harvesters, ctx.tuning.commander.target_group_cells)]


def _weighed(
    members: tuple[EnemyRecord, ...],
    body: Sequence[Unit],
    center: Cell,
    names: Mapping[str, str],
    world: World,
    ctx: MatchContext,
) -> EconomyTarget:
    """A candidate as a target: its name, cell, distance, value, and the body's ratio against the enemy by it."""
    cell = cells.nearest_cell(*centroid(members))
    support = [record for record in ground_enemies(world) if cells.is_within(cell, _cell(record), SUPPORT_CELLS)]
    ratio = ratio_against_target(body, members, world, ctx.tuning, support)
    first = members[0]
    name = named(first.type, names) if first.type == REFINERY else counts(named(item.type, names) for item in members)
    distance = math.dist(center, cell)
    return EconomyTarget(name, members, cell, distance, strength(members, world), ratio, bool(support))


def _cell(record: EnemyRecord) -> Cell:
    return record.x, record.y
