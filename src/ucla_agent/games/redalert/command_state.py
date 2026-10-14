"""What the model commander's questions say of the whole match: the scout's full-state sections and main's military state.

The small model the commander asks (see `commander`) reads, besides what is
near the units it commands, the whole picture the agent has of the match.

**The scout's sections.** The scout's question (see `command_scout`) starts
its user message with these sections, the ones that change least first, so
that a model server reusing the unchanged beginning of a prompt reuses most of
it from one question to the next. Each section is a heading and one compact
line per item, every position a cell, or the heading and `none`:

- `ore fields:` each field the terrain knows (`Terrain.ore_patches`), the
  nearest our base first: its center, how many cells it had at the start of
  the match, and who serves it: `dry` for a field the move to fresh ore marks
  dry (`ctx.dry_fields`), else `served by us`, `served by enemy` or both
  (`served by us, enemy`) when a refinery of that side counts against it as
  `refinery_fields.field_counted_from` counts one of ours, else `served by
  none`;
- `enemy buildings:` the remembered enemy buildings but the defenses, in
  groups of those within `BUILDING_GROUP_CELLS` of the group's first (see
  `clustered`), the group nearest our base first: the types counted, the
  group's center, and `in sight` or how long ago one was last seen, in steps
  of `AGE_STEP_SECONDS`;
- `enemy defenses:` every remembered enemy defense, the nearest our base
  first: its type, its cell and its weapon range, or `(air only)` for one
  that fires at aircraft alone;
- `enemy groups:` the remembered enemy combat units in clusters within
  `ENEMY_GROUP_CELLS` of the cluster's first, the nearest the scout first:
  the types worth most first, the center, what they are worth (cost times
  health), `in sight` or how long ago one was last seen, and `moving` or
  `still` (see `is_moving`).

They are built once for a question (`full_state`) and written at a level of
detail (`FullState.lines`): the question's cuts drop the enemy buildings,
then the ore fields, then the enemy groups (see `command_prompt.DETAILS`).

**main's military state.** main's question (see `command_army`) reads the
match as a commander of the army does, and nothing of the economy: no money,
income, harvesters of ours, ore fields or our buildings but as bases. The
state is built once for a question (`military_state`), the same structure
written as text for the small model (`MilitaryState.lines`) and as a JSON
object for a systemone server (`MilitaryState.as_json`). The model is poor at
geometry and favors what is listed first, so every relation is worked out
here and stated per item, and the items that matter most come first.

*Clusters.* Actors are joined by single link at `cluster_cells` of
`[commander]` (see `command_targets.single_link`): two of one cluster when a
chain of them, each within that many cells of the next, joins them, so that a
sprawled base or a stretched army is one cluster and two far apart are two.
The enemy's buildings make up the enemy bases (`E1`, `E2`...), ours our bases
(`B1`...), and the remembered enemy combat units the enemy forces (`F1`...).
A cluster of buildings without a construction yard whose center lies within
`base_merge_cells` of `[commander]` of the center of one with a yard of the
same side is part of the nearest such base (`base_clusters`): a refinery a
little past `cluster_cells` from its yard is no base of its own. One with its
own yard never merges, and one farther off stays apart.
The base nearest a side's start (the enemy's start, our base's center) is that
side's `main`; another with a construction yard is an `expansion`, which an
MCV deployed; the rest are `outpost`s. A cluster keeps its number from one
question to the next while its center has moved less than `cluster_cells`
(`ClusterLabels`); a new one takes the lowest number free. A merged base is
numbered by its yard cluster's own center, so it keeps that cluster's label.

*Where.* Every item says how far it is, in cells, and in which of the eight
directions (`N`, `NE`... with y growing southward) from main's body center,
with its cell after: `12 cells SE (48,84)`. main's own line says where it
stands from our base's center. By that distance an item is in the near band
(at most `NEAR_CELLS`), the mid band (at most `MID_CELLS`) or the far band:
a near item is written in full, a mid one in one line without the types, a
far one compactly (its name, distance, direction and strength). Within each
band the beatable items come first, then the nearest.

*Strength.* An actor's ground strength is the fight model's (see `fight`):
a unit's cost times its share of health, a defense's defense_weight of
`[combat]` times its cost; one that fires at aircraft alone (the AA gun, the
SAM site: `items.is_air_only`) is 0, and an air-only defense is noted by name
apart. A base says its buildings, how many and their summed cost, the ground
strength of its defenses, its guard (the ground strength of the side's combat
units within `GUARD_CELLS` of any of its buildings) and its exposed
harvesters (those of the side within `GUARD_CELLS` of it that no ground
defense of the side covers, `coverage.is_covered`). One of ours says which
enemy forces close on it (`command_threat.ClosingWatch.closing_on`, as the
`enemy army closing` threat finds one). An enemy force says its ground
strength and units, its crushable infantry, its anti-tank infantry
(`crush.ANTI_TANK_INFANTRY`), its armor (the units the Vehicle queue makes),
whether it moves toward us (our base's center or main, the nearer), away or
is still (`is_moving`'s sample), how long since one of it was seen, and the
base of ours it closes on.

*Relations.* Each enemy base and force says `within reach of <defense>: yes`
for the nearest remembered ground defense whose weapon range and 2 cells
reach its center (`command_targets.reaches`), or `within defense reach: no`
(`exposed` for a base); `in range: yes` when one of main's body stands within
its own weapon range of one of its actors, else `no`; and the fight facts:
main's body weighed against it (`fight.weigh`: a force with the ground
defenses that reach it, a base with its guard and those defenses), the plain
ratio, the crush facts and the crush-adjusted ratio, and `beatable: yes` at a
crush-adjusted ratio of `BEATABLE_RATIO` or more, or with no enemy there.

*Crush.* Its crushable infantry is the largest cluster of its infantry within
crush_cluster_cells of `[combat]` that a crush run would qualify on by size
(`crush.largest_cluster`, `crush.min_infantry`); `crush.planned` gives the
tanks it takes. While main has as many crushing vehicles at crush_min_hp of
`[combat]` or more (`crush.is_crusher`) in its body and stragglers, that
infantry counts at crush_discount of `[commander]` of its strength in the
crush-adjusted ratio, else in full.

*Our forces* are listed by what they do, each a line of the types by count
(the most worth first), their strength, average health and place: `main` the
question's body; `reinforcing` main's stragglers and the units of the group
main on their way to it, with the seconds the slowest needs; `staging` the
reinforcements waiting at the staging point; `retreating` the units withdrawn
on their own; `repairing` the vehicles in repair mode, with an estimate of
when they are back (`REPAIR_HP_PER_SECOND` up to repair_done_hp of
`[combat]`) while a mechanic of ours, mobile or at the base, or our service
depot stands within repair_mechanic_reach_cells of `[combat]` of the most
damaged of them, else `waiting at staging`, since with nothing to repair it
the vehicle does not heal; `detached` each commander's detachment with its
task and target, the crush run's tanks and the strike force; `raid` the raid
group, `guard` the home guard with the threat it answers, and `scouts`. Each
but main and the detachments is split into clusters as above.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable, Mapping, Sequence, TypeVar

from ucla_agent.games.redalert import cells, coverage, crush, placement
from ucla_agent.games.redalert.command_prompt import NONE, Detail, at, by_value, counts, named, seconds_ago
from ucla_agent.games.redalert.command_targets import range_cells, reaches, single_link
from ucla_agent.games.redalert.command_threat import CLOSING_CELLS, CLOSING_VALUE, ClosingWatch
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD, HARVESTER, REFINERY, SERVICE_DEPOT
from ucla_agent.games.redalert.fight import ground_defenses, strength, weigh, worth
from ucla_agent.games.redalert.groups import GUARD, MAIN, RAID, SCOUT
from ucla_agent.games.redalert.items import TICKS_PER_SECOND, is_air_only
from ucla_agent.games.redalert.managers import DetachedUnits, MatchContext
from ucla_agent.games.redalert.production import INFANTRY_QUEUE, VEHICLE_QUEUE
from ucla_agent.games.redalert.refinery_fields import REPORTED_OFFSET, field_counted_from
from ucla_agent.games.redalert.scout_missions import UNKNOWN_SPEED_CELLS_PER_SECOND, enemy_start
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.support import MECHANIC
from ucla_agent.games.redalert.terrain import OrePatch
from ucla_agent.games.redalert.tuning import CommanderTuning
from ucla_agent.games.redalert.world import DEFENSE_TYPES, Cell, EnemyRecord, Unit, World

# How near a group's first actor another stands to be of the group, in the scout's sections.
BUILDING_GROUP_CELLS = 10
ENEMY_GROUP_CELLS = 8
# A group whose centroid moved more than this is moving.
MOVING_CELLS = 1
# How long before the latest sighting of a group the closing watch's sample it is compared with was taken.
MOVING_LOOKBACK_TICKS = TICKS_PER_SECOND
# The ages of the sections are given in steps of this many seconds, so that they change seldom.
AGE_STEP_SECONDS = 30
RANGE_DECIMALS = 1
RATIO_DECIMALS = 1
IN_SIGHT = "in sight"
MOVING, STILL = "moving", "still"
TOWARD, AWAY = "toward us", "away"
US, ENEMY, DRY = "us", "enemy", "dry"
OURS = "ours"
# The bands of detail by distance from main's body center, and how far each reaches.
NEAR, MID, FAR = "near", "mid", "far"
NEAR_CELLS = 15
MID_CELLS = 35
_BAND_ORDER = {NEAR: 0, MID: 1, FAR: 2}
# The units of a side within this many cells of any of a base's buildings are its guard.
GUARD_CELLS = 10
# An enemy item is beatable at a crush-adjusted ratio of at least this.
BEATABLE_RATIO = 1.2
# How fast a vehicle in repair mode heals beside a mechanic, in percent of its health a second: the rate measured
# for a vehicle standing still.
REPAIR_HP_PER_SECOND = 2.0
# What a repairing group with no mechanic or service depot in reach says in place of when it is back.
WAITING_AT_STAGING = "waiting at staging"
# The kinds of base.
MAIN_BASE, EXPANSION, OUTPOST = "main", "expansion", "outpost"
# The eight directions, clockwise from the north; y grows southward.
DIRECTIONS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
_DEGREES_PER_DIRECTION = 45
HERE = "here"
# What our forces are doing, in the order they are listed.
MAIN_STATUS = "main"
REINFORCING = "reinforcing"
STAGING = "staging"
RETREATING = "retreating"
REPAIRING = "repairing"
DETACHED = "detached"
RAID_STATUS = "raid"
GUARD_STATUS = "guard"
SCOUTS = "scouts"
# The words a detachment's task is told in.
TASK_WORDS = {"run_over": "run over", "kill_defense": "kill", "hit_group": "hit", "raid_trucks": "raid trucks"}
CRUSH_WORDS = "crush run"
STRIKE_WORDS = "strike"
YES, NO = "yes", "no"
EXPOSED = "exposed"
# How a line of our forces names its target, by status; `target` for the others.
TARGET_WORDS = {GUARD_STATUS: "answers", SCOUTS: "doing", RAID_STATUS: "doing"}
IDLE, BUSY = "idle", "busy"
# What a unit's last order has it doing, by command; one with a cell says `to (x,y)` after it.
ORDER_WORDS = {
    "move": "move",
    "attack_move": "attack-move",
    "attack": "attack",
    "stop": "hold",
    "deploy": "deploy",
    "unload": "unload",
    "guard": "guard",
    "enter_transport": "board",
    "harvest": "harvest",
}
# What the state says a unit does, for one never told anything.
ACTIVITY_WORDS = {"FindAndDeliverResources": "harvest", "Move": "move", "AttackMoveActivity": "attack-move"}
_PERCENT = 100

_Placed = TypeVar("_Placed", EnemyRecord, Unit)


# -- the scout's sections ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FullState:
    """The lines of each full-state section of the scout's question, its heading first."""

    ore_fields: tuple[str, ...]
    enemy_buildings: tuple[str, ...]
    enemy_defenses: tuple[str, ...]
    enemy_groups: tuple[str, ...]

    def lines(self, detail: Detail) -> list[str]:
        """The sections at a level of detail, in order: the ore fields, the enemy buildings and defenses and the enemy
        groups, each that the level keeps."""
        kept = (
            (self.ore_fields, detail.ore_fields),
            (self.enemy_buildings, detail.enemy_buildings),
            (self.enemy_defenses, detail.enemy_defenses),
            (self.enemy_groups, detail.enemy_groups),
        )
        return [line for section, is_kept in kept if is_kept for line in section]


def full_state(
    world: World, ctx: MatchContext, names: Mapping[str, str], focus: Cell, *, closing: ClosingWatch | None = None
) -> FullState:
    """The full-state sections of a question about the units at `focus` (see the module's note), `closing` the
    closing watch whether an enemy group moves is read from (every group still without it)."""
    home = world.base_center or focus
    return FullState(
        ore_fields=_section("ore fields:", ore_field_lines(world, ctx, home)),
        enemy_buildings=_section("enemy buildings:", enemy_building_lines(world, names, home)),
        enemy_defenses=_section("enemy defenses:", enemy_defense_lines(world, ctx, names, home)),
        enemy_groups=_enemy_groups_section(world, names, focus, closing or ClosingWatch()),
    )


def _section(heading: str, items: list[str]) -> tuple[str, ...]:
    """A section: its heading and a line for each item, or the heading and `none`."""
    return (heading, *items) if items else (f"{heading} {NONE}",)


def ore_field_lines(world: World, ctx: MatchContext, home: Cell) -> list[str]:
    """A line for each ore field, the nearest `home` first: its center, its cells and who serves it."""
    ours = _fields_served(
        ((building.x, building.y) for building in world.buildings.values() if building.type == REFINERY), ctx
    )
    theirs = _fields_served(
        (
            (record.x, record.y)
            for record in world.enemy_memory.values()
            if record.is_building and record.type == REFINERY
        ),
        ctx,
    )
    fields = sorted(ctx.terrain.ore_patches, key=lambda field: (cells.distance(home, field.center), field.center))
    return [
        f"{at(field.center)} {field.size} cells, {_served_by(field, ours, theirs, ctx.dry_fields)}" for field in fields
    ]


def _fields_served(reported: Iterable[Cell], ctx: MatchContext) -> set[OrePatch]:
    """The fields refineries standing at these reported cells count against, as one of ours counts against a field
    (`refinery_fields.field_counted_from`)."""
    terrain, tuning = ctx.terrain, ctx.tuning.placement
    boxes = (placement.box_at(REFINERY, (x - REPORTED_OFFSET[0], y - REPORTED_OFFSET[1])) for x, y in reported)
    return {field for box in boxes if (field := field_counted_from(box, terrain, tuning)) is not None}


def _served_by(field: OrePatch, ours: set[OrePatch], theirs: set[OrePatch], dry: frozenset[OrePatch]) -> str:
    """Who serves a field: `dry` for one marked dry, else the sides whose refineries count against it, or none."""
    if field in dry:
        return DRY
    sides = [side for side, served in ((US, ours), (ENEMY, theirs)) if field in served]
    return "served by " + (", ".join(sides) or NONE)


def enemy_building_lines(world: World, names: Mapping[str, str], home: Cell) -> list[str]:
    """A line for each group of remembered enemy buildings but the defenses, the nearest `home` first."""
    defenses = {record.id for record in world.known_enemy_defenses}
    buildings = [record for record in world.enemy_memory.values() if record.is_building and record.id not in defenses]
    groups = _by_distance(clustered(buildings, BUILDING_GROUP_CELLS), home)
    return [
        f"{counts(named(record.type, names) for record in group)} at {at(_center(group))}, {_age(group, world)}"
        for group in groups
    ]


def enemy_defense_lines(world: World, ctx: MatchContext, names: Mapping[str, str], home: Cell) -> list[str]:
    """A line for each remembered enemy defense, the nearest `home` first: its type, its cell and its reach."""
    defenses = sorted(world.known_enemy_defenses, key=lambda record: (cells.distance(home, _cell(record)), record.id))
    return [f"{named(record.type, names)} {at(_cell(record))} {_reach(record, world, ctx)}" for record in defenses]


def _reach(defense: EnemyRecord, world: World, ctx: MatchContext) -> str:
    """`(air only)` for a defense that fires at aircraft alone, else `range <cells>`."""
    if is_air_only(defense.type, world.items):
        return "(air only)"
    return f"range {round(range_cells(defense.type, world, ctx), RANGE_DECIMALS):g}"


def _enemy_groups_section(
    world: World, names: Mapping[str, str], focus: Cell, closing: ClosingWatch
) -> tuple[str, ...]:
    """The enemy groups section."""
    units = [record for record in world.enemy_memory.values() if is_combat_unit(record)]
    groups = _by_distance(clustered(units, ENEMY_GROUP_CELLS), focus)
    return _section("enemy groups:", [_enemy_group(group, names, world, closing) for group in groups])


def _enemy_group(group: Sequence[EnemyRecord], names: Mapping[str, str], world: World, closing: ClosingWatch) -> str:
    valued = [(named(record.type, names), worth(record.type, record.hp, world)) for record in group]
    motion = MOVING if is_moving(group, closing) else STILL
    value = round(strength(group, world))
    return f"{by_value(valued)} at {at(_center(group))}, value {value}, {_age(group, world)}, {motion}"


def is_moving(group: Sequence[EnemyRecord], closing: ClosingWatch) -> bool:
    """Whether an enemy group moves: the centroid of its units moved more than MOVING_CELLS from where the same units
    stood in the closing watch's latest sample at least MOVING_LOOKBACK_TICKS before one of them was last seen."""
    moved = _moved(group, closing)
    return moved is not None and cells.distance(*moved) > MOVING_CELLS


def _moved(group: Sequence[EnemyRecord], closing: ClosingWatch) -> tuple[Point, Point] | None:
    """Where the units of a group that the closing watch's sample holds stand now and stood then (see `is_moving`),
    their centroids; None while the sample holds none of them."""
    last = max(record.last_seen_tick for record in group)
    before = closing.cells_at_or_before(last - MOVING_LOOKBACK_TICKS)
    then = [before[record.id] for record in group if record.id in before]
    if not then:
        return None
    return _mean(_cell(record) for record in group if record.id in before), _mean(then)


def task(unit: Unit, world: World, ctx: MatchContext, names: Mapping[str, str]) -> str:
    """What a unit of ours is doing: `idle` for one the state shows idle, else its last order that gave it something
    to do (`MatchContext.unit_orders`), `attack <type> (x,y)` or `move to (x,y)` and so on; for one never told
    anything, what the state says it does, else `busy`."""
    if unit.idle:
        return IDLE
    entry = ctx.unit_orders.get(unit.id)
    if entry is None:
        return ACTIVITY_WORDS.get(unit.activity or "", BUSY)
    order = entry.order
    words = ORDER_WORDS.get(order["cmd"], order["cmd"])
    target = world.enemy_memory.get(order.get("target")) if order.get("target") is not None else None
    if target is not None:
        return f"{words} {named(target.type, names)} {at(_cell(target))}"
    cell = order.get("to")
    return f"{words} to {at((cell[0], cell[1]))}" if cell else words


def clustered(placed: Iterable[_Placed], reach: float) -> list[tuple[_Placed, ...]]:
    """The actors in clusters: each, by id, joins the first cluster whose first actor stands within `reach` cells of
    it, else starts one. The clusters come in the order of their first actor."""
    found: list[list[_Placed]] = []
    for actor in sorted(placed, key=lambda actor: actor.id):
        here = _cell(actor)
        home = next((group for group in found if cells.is_within(_cell(group[0]), here, reach)), None)
        if home is None:
            found.append([actor])
        else:
            home.append(actor)
    return [tuple(group) for group in found]


def _by_distance(groups: list[tuple[_Placed, ...]], cell: Cell) -> list[tuple[_Placed, ...]]:
    """Groups sorted by the distance of their center from a cell, the nearest first; of two as near, as they came."""
    return sorted(groups, key=lambda group: cells.distance(cell, _center(group)))


def _age(group: Sequence[EnemyRecord], world: World) -> str:
    """`in sight` for a group one of which is, else how long ago one was last seen, in AGE_STEP_SECONDS steps."""
    if any(record.visible for record in group):
        return IN_SIGHT
    seconds = seconds_ago(max(record.last_seen_tick for record in group), world)
    return f"seen {max(AGE_STEP_SECONDS, round(seconds / AGE_STEP_SECONDS) * AGE_STEP_SECONDS)}s ago"


# -- main's military state -----------------------------------------------------------------------------------------

Point = tuple[float, float]


class ClusterLabels:
    """The numbers of the clusters of each kind (`B`, `E`, `F`), kept from one question to the next: a cluster keeps
    its number while its center has moved less than the reach given since the last question; a new one takes the
    lowest number free."""

    def __init__(self) -> None:
        self._centers: dict[str, dict[int, Point]] = {}

    def numbers(self, kind: str, centers: Sequence[Point], reach: float, *, keep: bool = True) -> list[int]:
        """The number of each cluster of a kind, by its center, the nearest pairs of an old center and a new one
        within `reach` of each other matched first, the others the lowest numbers free in turn; kept for the next
        question unless not `keep`."""
        matched = _matched(centers, self._centers.get(kind, {}), reach)
        free = (number for number in itertools.count(1) if number not in matched.values())
        numbers = [matched[index] if index in matched else next(free) for index in range(len(centers))]
        if keep:
            self._centers[kind] = dict(zip(numbers, centers))
        return numbers


def _matched(centers: Sequence[Point], previous: Mapping[int, Point], reach: float) -> dict[int, int]:
    """The old number of each new center that keeps one, by the new center's index: the nearest pairs of an old
    center and a new one less than `reach` apart first, each number and each center matched once."""
    pairs = sorted(
        (math.dist(center, old), index, number)
        for index, center in enumerate(centers)
        for number, old in previous.items()
        if math.dist(center, old) < reach
    )
    found: dict[int, int] = {}
    for _, index, number in pairs:
        if index not in found and number not in found.values():
            found[index] = number
    return found


@dataclass(frozen=True)
class Place:
    """Where an item stands from a point: its cell, how many cells away and in which of the eight directions."""

    pos: Cell
    dist: int
    dir: str

    @property
    def band(self) -> str:
        """The band of detail the item is written at: NEAR, MID or FAR."""
        if self.dist <= NEAR_CELLS:
            return NEAR
        return MID if self.dist <= MID_CELLS else FAR

    def text(self) -> str:
        """`12 cells SE (48,84)`, or `here (48,84)`."""
        return f"{HERE} {at(self.pos)}" if self.dist == 0 else f"{self.dist} cells {self.dir} {at(self.pos)}"

    def as_json(self) -> dict[str, Any]:
        return {"pos": list(self.pos), "dist": self.dist, "dir": self.dir}


def place(origin: Cell, cell: Cell) -> Place:
    """Where a cell stands from an origin."""
    return Place(cell, round(cells.distance(origin, cell)), direction(origin, cell))


def direction(origin: Cell, cell: Cell) -> str:
    """Which of the eight directions a cell lies in from an origin, `here` for the origin itself; y grows
    southward."""
    east, south = cell[0] - origin[0], cell[1] - origin[1]
    if east == south == 0:
        return HERE
    bearing = math.degrees(math.atan2(east, -south)) % 360
    return DIRECTIONS[round(bearing / _DEGREES_PER_DIRECTION) % len(DIRECTIONS)]


@dataclass(frozen=True)
class Fight:
    """main's body against an enemy item: the plain ratio and the crush-adjusted one (None for no enemy), the
    crushable infantry, the tanks a run over it takes and the tanks main has fit to run it over."""

    ratio: float | None
    crush_ratio: float | None
    crushable: int = 0
    planned: int = 0
    tanks: int = 0

    @property
    def is_discounted(self) -> bool:
        """Whether its crushable infantry counts at crush_discount: there is some, and tanks enough to run it over."""
        return self.crushable > 0 and self.tanks >= self.planned

    @property
    def is_beatable(self) -> bool:
        """A crush-adjusted ratio of BEATABLE_RATIO or more, or no enemy at all."""
        return self.crush_ratio is None or self.crush_ratio >= BEATABLE_RATIO

    def text(self) -> str:
        """`fight 0.9x; crush: 12 crushable, plan 3 tanks, have 6, infantry at 1/3; crush-adjusted 2.1x; beatable:
        yes`, the crush facts only with crushable infantry."""
        parts = [f"fight {_ratio(self.ratio)}"]
        if self.crushable:
            counted = "infantry at 1/3" if self.is_discounted else "infantry in full"
            parts.append(f"crush: {self.crushable} crushable, plan {self.planned} tanks, have {self.tanks}, {counted}")
        parts.append(f"crush-adjusted {_ratio(self.crush_ratio)}")
        parts.append(f"beatable: {_yes(self.is_beatable)}")
        return "; ".join(parts)

    def as_json(self) -> dict[str, Any]:
        found: dict[str, Any] = {
            "ratio": _rounded(self.ratio),
            "crush_ratio": _rounded(self.crush_ratio),
            "beatable": self.is_beatable,
        }
        if self.crushable:
            found.update(planned_tanks=self.planned, tanks=self.tanks, crush_discounted=self.is_discounted)
        return found


@dataclass(frozen=True)
class Reach:
    """Whether an enemy item lies within reach of a ground defense (the nearest such, by name; None for none) and
    within the weapon range of one of main's body."""

    defense: str | None
    in_range: bool

    def text(self, *, is_base: bool = False) -> str:
        """`within reach of flame tower: yes; in range: no`; a base no ground defense reaches is `exposed` besides."""
        exposed = f", {EXPOSED}" if is_base else ""
        reach = f"within reach of {self.defense}: {YES}" if self.defense else f"within defense reach: {NO}{exposed}"
        return f"{reach}; in range: {_yes(self.in_range)}"

    def as_json(self) -> dict[str, Any]:
        return {"within_defense_reach": self.defense is not None, "reached_by": self.defense, "in_range": self.in_range}


@dataclass(frozen=True)
class BaseState:
    """A base of either side (see the module's note). `reach` and `fight` are an enemy base's alone."""

    id: str
    side: str
    kind: str
    place: Place
    # The buildings' plain names with what each cost, the defenses' with their ground strength.
    buildings: tuple[tuple[str, float], ...]
    defenses: tuple[tuple[str, float], ...]
    air_only: tuple[str, ...]
    guard: int
    exposed_harvesters: int
    closing: tuple[str, ...] = ()
    reach: Reach | None = None
    fight: Fight | None = None
    # The cells of its buildings, and of its construction yard (its center without one), where an attack on it goes.
    cells: tuple[Cell, ...] = ()
    yard: Cell = (0, 0)

    @property
    def building_value(self) -> int:
        return round(sum(value for _, value in self.buildings))

    @property
    def def_ground(self) -> int:
        return round(sum(value for _, value in self.defenses))

    @property
    def is_beatable(self) -> bool:
        return self.fight is not None and self.fight.is_beatable

    def text(self, detail: Detail) -> str:
        """The base's line at its band: in full near, in one line without the types mid, compactly far."""
        head = f"{self.id} {self.kind}, {self.place.text()}"
        if self.place.band == FAR:
            closing = f", closing: {', '.join(self.closing)}" if self.closing else ""
            return f"{head}, strength {self.def_ground + self.guard}{closing}"
        is_near = self.place.band == NEAR
        types = f" ({by_value(self.buildings, detail.building_types)})" if is_near and detail.building_types else ""
        defense_types = f" ({by_value(self.defenses)})" if is_near and self.defenses else ""
        parts = [
            f"{head}: {_counted(len(self.buildings), 'building')} value {self.building_value}{types}",
            f"ground defense {self.def_ground}{defense_types}",
            f"air-only {counts(self.air_only) or NONE}",
            f"guard {self.guard}",
            f"exposed harvesters {self.exposed_harvesters}",
        ]
        if self.side == OURS:
            parts.append(f"closing: {', '.join(self.closing) or NONE}")
        else:
            parts.extend((self.reach.text(is_base=True), self.fight.text()))
        return "; ".join(parts)

    def as_json(self) -> dict[str, Any]:
        found = {
            "id": self.id,
            "side": self.side,
            "kind": self.kind,
            **self.place.as_json(),
            "buildings": len(self.buildings),
            "building_value": self.building_value,
            "building_types": _type_counts(self.buildings),
            "def_ground": self.def_ground,
            "def_air_only": list(self.air_only),
            "guard": self.guard,
            "exposed_harvesters": self.exposed_harvesters,
            "closing": list(self.closing),
        }
        if self.reach is not None and self.fight is not None:
            found.update(self.reach.as_json(), **self.fight.as_json())
        return found


@dataclass(frozen=True)
class ForceState:
    """An enemy force (see the module's note)."""

    id: str
    place: Place
    # Each unit's plain name with its ground strength.
    units: tuple[tuple[str, float], ...]
    crushable: int
    anti_tank: int
    armor: int
    motion: str
    seen_s: int
    reach: Reach
    fight: Fight
    closing_on: str | None = None
    # The ids of its units.
    ids: frozenset[int] = frozenset()

    @property
    def strength(self) -> int:
        return round(sum(value for _, value in self.units))

    @property
    def is_beatable(self) -> bool:
        return self.fight.is_beatable

    def text(self, detail: Detail) -> str:
        """The force's line at its band."""
        head = f"{self.id}, {self.place.text()}"
        if self.place.band == FAR:
            return f"{head}, strength {self.strength}, {self.motion}"
        types = f" ({by_value(self.units, detail.unit_types)})" if self.place.band == NEAR else ""
        seen = IN_SIGHT if self.seen_s == 0 else f"seen {self.seen_s}s ago"
        closing = f"; closing on {self.closing_on}" if self.closing_on else ""
        return "; ".join(
            (
                f"{head}: {_counted(len(self.units), 'unit')} strength {self.strength}{types}",
                f"{self.crushable} crushable, {self.anti_tank} anti-tank, {self.armor} armor",
                f"{self.motion}, {seen}{closing}",
                self.reach.text(),
                self.fight.text(),
            )
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            **self.place.as_json(),
            "strength": self.strength,
            "units": len(self.units),
            "unit_types": _type_counts(self.units),
            "crushable": self.crushable,
            "anti_tank": self.anti_tank,
            "armor": self.armor,
            "motion": self.motion,
            "seen_s": self.seen_s,
            "closing_on": self.closing_on,
            **self.reach.as_json(),
            **self.fight.as_json(),
        }


@dataclass(frozen=True)
class OwnForce:
    """Some of our units by what they do: the status, each unit's plain name with its strength, their average
    health, where they stand, and for the statuses that have one the seconds to main or back, and the target."""

    status: str
    units: tuple[tuple[str, float], ...]
    hp: int
    place: Place
    eta_s: int | None = None
    target: str | None = None

    @property
    def strength(self) -> int:
        return round(sum(value for _, value in self.units))

    def text(self, detail: Detail) -> str:
        """`staging: 2 units (2 medium tank) strength 1700, hp 100%, 22 cells NW (33,47)`, with what more its status
        says: the seconds to main or back from repair, and the target (for the guard, the threat it answers; for the
        scouts and the raid group, what most of them are doing)."""
        where = f"{self.place.text()} from base" if self.status == MAIN_STATUS else self.place.text()
        types = by_value(self.units, detail.unit_types)
        parts = [
            f"{self.status}: {_counted(len(self.units), 'unit')} ({types}) strength {self.strength}, hp {self.hp}%"
        ]
        parts.append(where)
        if self.eta_s is not None:
            parts.append(f"back in {self.eta_s}s" if self.status == REPAIRING else f"eta {self.eta_s}s")
        elif self.status == REPAIRING:
            parts.append(WAITING_AT_STAGING)
        if self.target is not None:
            parts.append(f"{TARGET_WORDS.get(self.status, 'target')} {self.target}")
        return ", ".join(parts)

    def as_json(self) -> dict[str, Any]:
        found = {
            "status": self.status,
            "units": _type_counts(self.units),
            "strength": self.strength,
            "hp": self.hp,
            **self.place.as_json(),
        }
        if self.eta_s is not None:
            found["eta_s"] = self.eta_s
        elif self.status == REPAIRING:
            found["waiting_at_staging"] = True
        if self.target is not None:
            found["target"] = self.target
        return found


@dataclass(frozen=True)
class MilitaryState:
    """main's military state in a cycle: our bases, the enemy's, the enemy forces and our forces, each in the order
    the question lists them (see the module's note)."""

    our_bases: tuple[BaseState, ...]
    enemy_bases: tuple[BaseState, ...]
    enemy_forces: tuple[ForceState, ...]
    our_forces: tuple[OwnForce, ...]

    def lines(self, detail: Detail) -> list[str]:
        """The four sections at a level of detail: the far band, then the mid band, left out as the level says, each
        section saying how many items it leaves out."""
        return [
            *_banded("our bases:", self.our_bases, detail),
            *_banded("enemy bases:", self.enemy_bases, detail),
            *_banded("enemy forces:", self.enemy_forces, detail),
            *_section("our forces:", [force.text(detail) for force in self.our_forces]),
        ]

    def as_json(self) -> dict[str, Any]:
        """The same facts as a JSON object, every item at its band's detail and the far ones with their fight too."""
        return {
            "bases": [base.as_json() for base in (*self.our_bases, *self.enemy_bases)],
            "enemy_forces": [force.as_json() for force in self.enemy_forces],
            "our_forces": [force.as_json() for force in self.our_forces],
        }

    def expansions(self) -> list[BaseState]:
        """The enemy expansions main can beat, the highest crush-adjusted ratio first (no enemy highest), then the
        nearest."""
        found = [base for base in self.enemy_bases if base.kind == EXPANSION and base.is_beatable]
        return sorted(found, key=lambda base: (-_ratio_rank(base.fight.crush_ratio), base.place.dist, base.id))


def _banded(heading: str, items: Sequence[BaseState | ForceState], detail: Detail) -> tuple[str, ...]:
    """A banded section: the items the level keeps, then `+N farther` for those it leaves out."""
    shown = [item for item in items if _is_shown(item.place.band, detail)]
    left = len(items) - len(shown)
    lines = [item.text(detail) for item in shown] + ([f"+{left} farther"] if left else [])
    return (heading, *lines) if items else (f"{heading} {NONE}",)


def _is_shown(band: str, detail: Detail) -> bool:
    return band == NEAR or (band == MID and detail.mid_band) or (band == FAR and detail.far_band)


@dataclass(frozen=True)
class Army:
    """What of main the military state is told from: the question's units, its body and the body's center, its
    stragglers and the reinforcements waiting at the staging point."""

    units: tuple[Unit, ...]
    body: tuple[Unit, ...]
    center: Cell
    stragglers: tuple[Unit, ...] = ()
    waiting: tuple[Unit, ...] = ()


def military_state(
    army: Army,
    names: Mapping[str, str],
    world: World,
    ctx: MatchContext,
    closing: ClosingWatch | None = None,
    labels: ClusterLabels | None = None,
    *,
    keep_labels: bool = True,
) -> MilitaryState:
    """main's military state in this cycle (see the module's note); `closing` the closing watch the enemy forces'
    motion and closing are read from (none closing and all still without it), `labels` the cluster numbers of the
    last question (numbered afresh without), kept for the next one unless not `keep_labels`."""
    watch, labels = closing or ClosingWatch(), labels or ClusterLabels()
    reach = ctx.tuning.commander.cluster_cells
    our_bases = base_clusters(world.buildings.values(), ctx.tuning.commander)
    our_clusters = [base.members for base in our_bases]
    our_numbers = labels.numbers("B", [base.label_center for base in our_bases], reach, keep=keep_labels)
    closing_on = {
        f"B{number}": found
        for number, group in zip(our_numbers, our_clusters)
        if (found := watch.closing_on(_center(group), CLOSING_CELLS, CLOSING_VALUE, world)) is not None
    }
    forces = _enemy_forces(army, names, world, ctx, watch, labels, closing_on, keep_labels)
    main = _nearest(our_clusters, world.base_center)
    ours = [
        _our_base(f"B{number}", group, group is main, army, forces, names, world, ctx)
        for number, group in zip(our_numbers, our_clusters)
    ]
    theirs = enemy_bases(army, names, world, ctx, labels, keep=keep_labels)
    return MilitaryState(
        tuple(sorted(ours, key=lambda base: (base.place.dist, base.id))),
        tuple(sorted(theirs, key=_rank)),
        tuple(sorted(forces, key=_rank)),
        tuple(our_forces(army, names, world, ctx)),
    )


def _rank(item: BaseState | ForceState) -> tuple[int, bool, int, str]:
    """How an enemy item ranks in its section: by band, the beatable first, then the nearest."""
    return _BAND_ORDER[item.place.band], not item.is_beatable, item.place.dist, item.id


def _our_base(
    label: str,
    group: Sequence[Any],
    is_main: bool,
    army: Army,
    forces: Sequence[ForceState],
    names: Mapping[str, str],
    world: World,
    ctx: MatchContext,
) -> BaseState:
    """One of our bases: its kind by our base's center, what its buildings and defenses are worth, its guard of our
    combat units but main's, its harvesters no ground defense of ours covers and the enemy forces closing on it."""
    harvesters = [unit for unit in world.units.values() if unit.type == HARVESTER and _is_by(unit, group)]
    return BaseState(
        id=label,
        side=OURS,
        kind=_kind(group, is_main),
        place=place(army.center, _center(group)),
        buildings=_valued_buildings(group, names, world),
        defenses=_defense_strengths(group, names, world, ctx),
        air_only=_air_only(group, names, world),
        guard=round(strength(_our_guard(group, army, world), world)),
        exposed_harvesters=len(_exposed(harvesters, _our_ground_defenses(world), world, ctx)),
        closing=tuple(force.id for force in forces if force.closing_on == label),
        cells=tuple(_cell(building) for building in group),
        yard=_yard(group),
    )


def _our_ground_defenses(world: World) -> list[Any]:
    """Our defenses that fire at ground units."""
    return [
        building
        for building in world.buildings.values()
        if building.type in DEFENSE_TYPES and not is_air_only(building.type, world.items)
    ]


def _our_guard(group: Sequence[Any], army: Army, world: World) -> list[Unit]:
    """Our combat units by a base of ours (see `_is_by`) but those of main's question."""
    asked = {unit.id for unit in army.units}
    return [unit for unit in world.combat_units.values() if unit.id not in asked and _is_by(unit, group)]


def _exposed(harvesters: Iterable[Any], defenses: Sequence[Any], world: World, ctx: MatchContext) -> list[Any]:
    """The harvesters none of the ground defenses given covers (`coverage.is_covered`)."""
    tuning = ctx.tuning.coverage
    return [actor for actor in harvesters if not coverage.is_covered(_cell(actor), defenses, world.items, tuning)]


def _air_only(group: Sequence[Any], names: Mapping[str, str], world: World) -> tuple[str, ...]:
    """The plain names of a base's defenses that fire at aircraft alone."""
    return tuple(named(building.type, names) for building in group if _is_air_defense(building, world))


def enemy_bases(
    army: Army, names: Mapping[str, str], world: World, ctx: MatchContext, labels: ClusterLabels, *, keep: bool = True
) -> list[BaseState]:
    """The enemy bases: the remembered enemy buildings by single link, a cluster without a yard merged into the
    nearest base with one (see `base_clusters`), each with its kind, strength, relations and fight, numbered by
    `labels` and the numbers kept for the next question unless not `keep`."""
    reach = ctx.tuning.commander.cluster_cells
    buildings = (record for record in world.enemy_memory.values() if record.is_building)
    bases = base_clusters(buildings, ctx.tuning.commander)
    groups = [base.members for base in bases]
    numbers = labels.numbers("E", [base.label_center for base in bases], reach, keep=keep)
    main = _nearest(groups, enemy_start(world))
    return [
        _enemy_base(f"E{number}", group, group is main, army, names, world, ctx)
        for number, group in zip(numbers, groups)
    ]


@dataclass(frozen=True)
class BaseCluster:
    """A side's base as its buildings stand: its members, and the center it is numbered by (its yard cluster's own
    for a base other clusters merged into)."""

    members: tuple[Any, ...]
    label_center: Point


def base_clusters(buildings: Iterable[Any], commander: CommanderTuning) -> list[BaseCluster]:
    """A side's buildings as bases: clusters by single link at cluster_cells of [commander], each without a
    construction yard whose center lies within base_merge_cells of the center of one with a yard joining the nearest
    such one (of two as near, the first). The bases come in the order of their clusters, a merged one at its yard
    cluster's place."""
    groups = single_link(sorted(buildings, key=_id), commander.cluster_cells)
    centers = [_mean(map(_cell, group)) for group in groups]
    yards = [index for index, group in enumerate(groups) if _has_yard(group)]
    joined: dict[int, list[Any]] = {index: list(groups[index]) for index in yards}
    absorbed: set[int] = set()
    for index, group in enumerate(groups):
        reached = [yard for yard in yards if math.dist(centers[index], centers[yard]) <= commander.base_merge_cells]
        if index not in joined and reached:
            joined[min(reached, key=lambda yard: math.dist(centers[index], centers[yard]))].extend(group)
            absorbed.add(index)
    return [
        BaseCluster(tuple(joined.get(index, group)), centers[index])
        for index, group in enumerate(groups)
        if index not in absorbed
    ]


def _has_yard(group: Sequence[Any]) -> bool:
    return any(building.type == CONSTRUCTION_YARD for building in group)


def _nearest(groups: Sequence[tuple[Any, ...]], start: Cell | None) -> tuple[Any, ...] | None:
    """The cluster whose center is nearest a side's start, its main base; None without a start or a cluster."""
    if start is None:
        return None
    return min(groups, key=lambda group: cells.distance(start, _center(group)), default=None)


def _enemy_base(
    label: str,
    group: tuple[EnemyRecord, ...],
    is_main: bool,
    army: Army,
    names: Mapping[str, str],
    world: World,
    ctx: MatchContext,
) -> BaseState:
    """An enemy base (see the module's note): main's body weighed against its guard that counts in a fight and the
    ground defenses that reach it, its own among them."""
    center = _center(group)
    guard = _enemies_by(group, is_combat_unit, world)
    reaching = _reaching(center, world, ctx, own=frozenset(record.id for record in group))
    return BaseState(
        id=label,
        side=ENEMY,
        kind=_kind(group, is_main),
        place=place(army.center, center),
        buildings=_valued_buildings(group, names, world),
        defenses=_defense_strengths(group, names, world, ctx),
        air_only=_air_only(group, names, world),
        guard=round(sum(_ground_strength(record, world) for record in guard)),
        exposed_harvesters=len(_exposed(_enemies_by(group, _is_harvester, world), ground_defenses(world), world, ctx)),
        reach=_reach_of(center, group, army, names, world, ctx),
        fight=fight_against(army, _fresh(guard), reaching, world, ctx),
        cells=tuple(_cell(record) for record in group),
        yard=_yard(group),
    )


def _enemies_by(group: Sequence[EnemyRecord], kind: Callable[[EnemyRecord], bool], world: World) -> list[EnemyRecord]:
    """The remembered enemies of a kind by a base (see `_is_by`)."""
    return [record for record in world.enemy_memory.values() if kind(record) and _is_by(record, group)]


def _reaching(center: Cell, world: World, ctx: MatchContext, own: frozenset[int] = frozenset()) -> list[EnemyRecord]:
    """The ground defenses whose weapon range and 2 cells reach a cell (`command_targets.reaches`), and those of `own`
    whatever their reach."""
    return [defense for defense in ground_defenses(world) if defense.id in own or reaches(defense, center, world, ctx)]


def _fresh(records: Iterable[EnemyRecord]) -> list[EnemyRecord]:
    """The remembered enemies that count in a fight's weighing (see `_is_fresh`)."""
    return [record for record in records if _is_fresh(record)]


def _is_harvester(record: EnemyRecord) -> bool:
    return not record.is_building and record.type == HARVESTER


def _is_fresh(record: EnemyRecord) -> bool:
    """Whether a remembered enemy counts in a fight: in sight, or seen lately enough for its place to count on."""
    return record.visible or not record.stale


def _enemy_forces(
    army: Army,
    names: Mapping[str, str],
    world: World,
    ctx: MatchContext,
    watch: ClosingWatch,
    labels: ClusterLabels,
    closing_on: Mapping[str, Any],
    keep: bool,
) -> list[ForceState]:
    """The enemy forces: the remembered enemy combat units by single link (see the module's note)."""
    reach = ctx.tuning.commander.cluster_cells
    units = sorted((record for record in world.enemy_memory.values() if is_combat_unit(record)), key=_id)
    groups = single_link(units, reach)
    numbers = labels.numbers("F", [_mean(map(_cell, group)) for group in groups], reach, keep=keep)
    return [
        _enemy_force(f"F{number}", group, army, names, world, ctx, watch, closing_on)
        for number, group in zip(numbers, groups)
    ]


def _enemy_force(
    label: str,
    group: tuple[EnemyRecord, ...],
    army: Army,
    names: Mapping[str, str],
    world: World,
    ctx: MatchContext,
    watch: ClosingWatch,
    closing_on: Mapping[str, Any],
) -> ForceState:
    """An enemy force: main's body weighed against its units that count in a fight and the ground defenses that reach
    its center; the base of ours it closes on is the first whose closing group holds one of its units."""
    center = _center(group)
    ids = frozenset(record.id for record in group)
    fight = fight_against(army, _fresh(group), _reaching(center, world, ctx), world, ctx)
    return ForceState(
        id=label,
        place=place(army.center, center),
        units=tuple((named(record.type, names), _ground_strength(record, world)) for record in group),
        crushable=fight.crushable,
        anti_tank=sum(record.type in crush.ANTI_TANK_INFANTRY for record in group),
        armor=sum(_queue(record.type, world) == VEHICLE_QUEUE for record in group),
        motion=_motion(group, watch, [army.center, *([world.base_center] if world.base_center else [])]),
        seen_s=_seen(group, world),
        reach=_reach_of(center, group, army, names, world, ctx),
        fight=fight,
        closing_on=_closing_base(ids, closing_on),
        ids=ids,
    )


def _closing_base(ids: frozenset[int], closing_on: Mapping[str, Any]) -> str | None:
    """The first base of ours whose closing group holds one of these enemies; None for none."""
    return next((base for base, closing in closing_on.items() if ids & _ids(closing.records)), None)


def fight_against(
    army: Army, enemies: Sequence[EnemyRecord], defenses: Sequence[EnemyRecord], world: World, ctx: MatchContext
) -> Fight:
    """main's body against some enemy units and ground defenses (`fight.weigh`): the plain ratio, and the ratio with
    the crushable infantry among the units at crush_discount of [commander] while main has tanks enough to run it over
    (see the module's note); a ratio is None while the enemy is worth nothing."""
    body = list(army.body)
    crushable, planned = crushable_infantry(enemies, world, ctx)
    tanks = crushing_tanks(army, world, ctx)
    ratio = _ratio_of(weigh(body, enemies, world, ctx.tuning, defenses))
    fight = Fight(ratio, ratio, len(crushable), planned, tanks)
    if not fight.is_discounted:
        return fight
    discount = ctx.tuning.commander.crush_discount
    ids = _ids(crushable)
    adjusted = [replace(record, hp=record.hp * discount) if record.id in ids else record for record in enemies]
    return replace(fight, crush_ratio=_ratio_of(weigh(body, adjusted, world, ctx.tuning, defenses)))


def crushable_infantry(
    enemies: Sequence[EnemyRecord], world: World, ctx: MatchContext
) -> tuple[tuple[EnemyRecord, ...], int]:
    """The crushable infantry among some enemy units, the largest cluster of their infantry a crush run would qualify
    on by size (none otherwise), and the tanks `crush.planned` gives for it (0 for none)."""
    combat = ctx.tuning.combat
    infantry = [record for record in enemies if not record.is_building and _is_infantry(record, world)]
    cluster = crush.largest_cluster(infantry, combat.crush_cluster_cells)
    if cluster is None or len(cluster.infantry) < crush.min_infantry(cluster, combat):
        return (), 0
    return cluster.infantry, crush.planned(cluster, combat)


def crushing_tanks(army: Army, world: World, ctx: MatchContext) -> int:
    """How many of main's crushing vehicles, its body's and stragglers', have crush_min_hp of [combat] or more."""
    least = ctx.tuning.combat.crush_min_hp
    units = (*army.body, *army.stragglers)
    return sum(1 for unit in units if crush.is_crusher(unit.type, world.items) and unit.hp >= least)


def our_forces(army: Army, names: Mapping[str, str], world: World, ctx: MatchContext) -> list[OwnForce]:
    """Our forces by what they do (see the module's note), in the order of the statuses."""
    units, reach, center = world.combat_units, ctx.tuning.commander.cluster_cells, army.center
    found = [_own(MAIN_STATUS, army.body, world.base_center or center, names, world)]
    found += [
        replace(_own(REINFORCING, group, center, names, world), eta_s=_eta(group, center, world))
        for group in _clusters([*army.stragglers, *_joining(army, world, ctx)], reach)
    ]
    found += [_own(STAGING, group, center, names, world) for group in _clusters(army.waiting, reach)]
    found += [_own(RETREATING, group, center, names, world) for group in _clusters(_of(ctx.withdrawn, units), reach)]
    found += [
        replace(_own(REPAIRING, group, center, names, world), eta_s=_back_in(group, world, ctx))
        for group in _clusters(_of(ctx.repairing, units), reach)
    ]
    return [*found, *_detached(army, names, world, ctx), *_grouped(army, names, world, ctx)]


def _joining(army: Army, world: World, ctx: MatchContext) -> list[Unit]:
    """The units of the group main on their way to it: none of its question, of the reinforcements waiting, out on
    main's orders, on a detachment, a crush run or a strike, in repair or withdrawn on their own."""
    away = {unit.id for unit in (*army.units, *army.waiting)} | _detached_ids(ctx) | set(ctx.crush_run)
    away |= ctx.army | ctx.combat_held | ctx.repairing | ctx.withdrawn
    return _of((unit_id for unit_id in ctx.groups.members(MAIN) if unit_id not in away), world.combat_units)


def _detached_ids(ctx: MatchContext) -> set[int]:
    """The units of every detachment of the commander's out."""
    return {unit_id for detachment in ctx.detachments for unit_id in detachment.units}


def _detached(army: Army, names: Mapping[str, str], world: World, ctx: MatchContext) -> list[OwnForce]:
    """The detachments of the commander's, each with its task and target, then the crush run's tanks and the strike
    force (main's units a strike holds, those in repair aside), each with what most of them are doing."""
    units, center = world.combat_units, army.center
    found = [
        replace(_own(DETACHED, members, center, names, world), target=_task_text(detachment, names, world))
        for detachment in ctx.detachments
        if (members := _of(detachment.units, units))
    ]
    striking = set(ctx.combat_held) - set(ctx.repairing) - _detached_ids(ctx)
    for words, ids in ((CRUSH_WORDS, set(ctx.crush_run)), (STRIKE_WORDS, striking)):
        members = _of(ids, units)
        if members:
            aim = f"{words}: {_common_task(members, world, ctx, names)}"
            found.append(replace(_own(DETACHED, members, center, names, world), target=aim))
    return found


def _grouped(army: Army, names: Mapping[str, str], world: World, ctx: MatchContext) -> list[OwnForce]:
    """The raid group, the home guard and the scouts, each in clusters: the guard with the threat it answers, the
    others with what most of them are doing."""
    found = []
    for status, group_name in ((RAID_STATUS, RAID), (GUARD_STATUS, GUARD), (SCOUTS, SCOUT)):
        members = _of(ctx.groups.members(group_name), world.combat_units)
        for group in _clusters(members, ctx.tuning.commander.cluster_cells):
            aim = _guard_target(ctx) if status == GUARD_STATUS else _common_task(group, world, ctx, names)
            found.append(replace(_own(status, group, army.center, names, world), target=aim))
    return found


def _task_text(detachment: DetachedUnits, names: Mapping[str, str], world: World) -> str:
    """A detachment's task in words and its target (see `_target_text`)."""
    return _target_text(detachment.targets, TASK_WORDS.get(detachment.kind, detachment.kind), names, world)


def _own(status: str, group: Sequence[Unit], origin: Cell, names: Mapping[str, str], world: World) -> OwnForce:
    """Some of our units under a status, placed from `origin`."""
    valued = tuple((named(unit.type, names), worth(unit.type, unit.hp, world)) for unit in group)
    hp = round(sum(unit.hp for unit in group) / len(group))
    return OwnForce(status, valued, hp, place(origin, _center(group)))


def _eta(group: Sequence[Unit], center: Cell, world: World) -> int:
    """About how many seconds the slowest of some units needs to reach main's body center."""
    slowest = min(_speed(unit.type, world) for unit in group)
    return round(cells.distance(_center(group), center) / slowest)


def _back_in(group: Sequence[Unit], world: World, ctx: MatchContext) -> int | None:
    """About how many seconds the most damaged of some vehicles in repair needs to reach repair_done_hp of [combat],
    or None while no mechanic of ours or service depot stands within repair_mechanic_reach_cells of it."""
    combat = ctx.tuning.combat
    worst = min(group, key=lambda unit: (unit.hp, unit.id))
    repairers = [
        *(unit for unit in world.units.values() if unit.type == MECHANIC),
        *(building for building in world.buildings.values() if building.type == SERVICE_DEPOT),
    ]
    if not any(
        cells.is_within(_cell(worst), _cell(repairer), combat.repair_mechanic_reach_cells) for repairer in repairers
    ):
        return None
    return round(max(0, combat.repair_done_hp - worst.hp) / REPAIR_HP_PER_SECOND)


def _guard_target(ctx: MatchContext) -> str:
    """The threat the home guard answers: base defense's threat in force and its cell, or none."""
    if not ctx.threat_active:
        return NONE
    return "threat" if ctx.threat_cell is None else f"threat at {at(ctx.threat_cell)}"


def _common_task(group: Sequence[Unit], world: World, ctx: MatchContext, names: Mapping[str, str]) -> str:
    """What most of some units are doing (see `task`); of two as common, the first by its words."""
    tasks = Counter(task(unit, world, ctx, names) for unit in group)
    return min(tasks, key=lambda words: (-tasks[words], words))


def _target_text(targets: Iterable[int], words: str, names: Mapping[str, str], world: World) -> str:
    """`run over 12 rifle infantry (55,70)`: a task's words, then the enemies it is on that are remembered, counted,
    and their centroid."""
    aimed = [world.enemy_memory[enemy_id] for enemy_id in sorted(targets) if enemy_id in world.enemy_memory]
    if not aimed:
        return words
    return f"{words} {counts(named(record.type, names) for record in aimed)} {at(_center(aimed))}"


def _clusters(units: Iterable[Unit], reach: float) -> list[tuple[Unit, ...]]:
    """Some of our units by single link, by id."""
    return single_link(sorted(units, key=_id), reach)


def _of(ids: Iterable[int], units: Mapping[int, Unit]) -> list[Unit]:
    """The units of these ids that stand, by id."""
    return [units[unit_id] for unit_id in sorted(ids) if unit_id in units]


def _speed(unit_type: str, world: World) -> float:
    """How many cells a second a type goes: its item's speed, else the speed taken for an unknown one."""
    facts = world.items.get(unit_type)
    return facts.speed if facts is not None and facts.speed else UNKNOWN_SPEED_CELLS_PER_SECOND


def _reach_of(
    center: Cell, group: Sequence[EnemyRecord], army: Army, names: Mapping[str, str], world: World, ctx: MatchContext
) -> Reach:
    """The nearest ground defense whose reach takes in an item's center, and whether one of main's body stands
    within its own weapon range of one of the item's actors."""
    reaching = [defense for defense in ground_defenses(world) if reaches(defense, center, world, ctx)]
    nearest = min(reaching, key=lambda defense: (cells.distance(center, _cell(defense)), defense.id), default=None)
    in_range = any(
        cells.is_within(_cell(unit), _cell(record), range_cells(unit.type, world, ctx))
        for unit in army.body
        for record in group
    )
    return Reach(None if nearest is None else named(nearest.type, names), in_range)


def _motion(group: Sequence[EnemyRecord], watch: ClosingWatch, homes: Sequence[Cell]) -> str:
    """`still`, or `toward us` when the group moved (see `is_moving`) nearer the nearer of our base's center and
    main, else `away`."""
    moved = _moved(group, watch)
    if moved is None or cells.distance(*moved) <= MOVING_CELLS or not homes:
        return STILL
    now, then = moved
    nearest = min(homes, key=lambda home: cells.distance(now, home))
    return TOWARD if cells.distance(now, nearest) < cells.distance(then, nearest) else AWAY


def _seen(group: Sequence[EnemyRecord], world: World) -> int:
    """How many seconds since one of a group was last seen; 0 while one is in sight."""
    if any(record.visible for record in group):
        return 0
    return seconds_ago(max(record.last_seen_tick for record in group), world)


def _kind(group: Sequence[Any], is_main: bool) -> str:
    """A base's kind: main for the one nearest its side's start, else an expansion with a construction yard, else an
    outpost."""
    if is_main:
        return MAIN_BASE
    return EXPANSION if _has_yard(group) else OUTPOST


def _yard(group: Sequence[Any]) -> Cell:
    """The cell of a base's construction yard of the lowest id, else its center."""
    yards = sorted((building for building in group if building.type == CONSTRUCTION_YARD), key=_id)
    return _cell(yards[0]) if yards else _center(group)


def _valued_buildings(group: Sequence[Any], names: Mapping[str, str], world: World) -> tuple[tuple[str, float], ...]:
    """Each building's plain name with what it cost."""
    return tuple((named(building.type, names), worth(building.type, _PERCENT, world)) for building in group)


def _defense_strengths(
    group: Sequence[Any], names: Mapping[str, str], world: World, ctx: MatchContext
) -> tuple[tuple[str, float], ...]:
    """Each defense of a group that fires at ground units, by plain name, with its ground strength: defense_weight of
    [combat] times its cost, as the fight model weighs one."""
    weight = ctx.tuning.combat.defense_weight
    return tuple(
        (named(building.type, names), weight * worth(building.type, _PERCENT, world))
        for building in group
        if building.type in DEFENSE_TYPES and not is_air_only(building.type, world.items)
    )


def _is_air_defense(building: Any, world: World) -> bool:
    return building.type in DEFENSE_TYPES and is_air_only(building.type, world.items)


def _ground_strength(record: EnemyRecord, world: World) -> float:
    """An enemy unit's ground strength: its worth, 0 for one that fires at aircraft alone."""
    return 0.0 if is_air_only(record.type, world.items) else worth(record.type, record.hp, world)


def _is_infantry(record: EnemyRecord, world: World) -> bool:
    return record.type in crush.ANTI_TANK_INFANTRY or _queue(record.type, world) == INFANTRY_QUEUE


def _queue(actor_type: str, world: World) -> str | None:
    facts = world.items.get(actor_type)
    return None if facts is None else facts.queue


def _is_by(actor: Any, group: Sequence[Any]) -> bool:
    """Whether an actor stands within GUARD_CELLS of any of a group's buildings."""
    return any(cells.is_within(_cell(actor), _cell(building), GUARD_CELLS) for building in group)


def _ratio_of(weighed: tuple[float, float]) -> float | None:
    own, enemy = weighed
    return own / enemy if enemy > 0 else None


def _ratio(ratio: float | None) -> str:
    return "no enemy" if ratio is None else f"{ratio:.{RATIO_DECIMALS}f}x"


def _rounded(ratio: float | None) -> float | None:
    return None if ratio is None else round(ratio, RATIO_DECIMALS)


def _ratio_rank(ratio: float | None) -> float:
    return math.inf if ratio is None else ratio


def _counted(number: int, noun: str) -> str:
    """`1 building`, `3 buildings`."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _yes(holds: bool) -> str:
    return YES if holds else NO


def _type_counts(valued: Iterable[tuple[str, float]]) -> dict[str, int]:
    """Types counted from (name, value) pairs, in the order `by_value` lists them."""
    listed = list(valued)
    numbers = Counter(name for name, _ in listed)
    totals: defaultdict[str, float] = defaultdict(float)
    for name, value in listed:
        totals[name] += value
    return {name: numbers[name] for name in sorted(numbers, key=lambda name: (-totals[name], -numbers[name], name))}


def _ids(records: Iterable[EnemyRecord]) -> set[int]:
    return {record.id for record in records}


def _id(actor: Any) -> int:
    return actor.id


def _center(group: Sequence[Any]) -> Cell:
    return cells.nearest_cell(*_mean(_cell(actor) for actor in group))


def _mean(points: Iterable[Cell]) -> tuple[float, float]:
    listed = list(points)
    return sum(x for x, _ in listed) / len(listed), sum(y for _, y in listed) / len(listed)


def _cell(actor: Any) -> Cell:
    return actor.x, actor.y
