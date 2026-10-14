"""The model commander's question for the first scout: what the scout faces, what is known, and its checked actions.

Until the handover to main (see `commander`) the commander steers one scout.
Its question is written as `command_prompt` says, held to `MAX_PROMPT_CHARS`
characters with its system text (`SYSTEM_TEXT`: the scout's task, then the
tournament and the learning from recent picks of `model_history`, without
the army's doctrine; `SHORT_SYSTEM_TEXT` without them, should nothing else
fit), in these lines, those that change least first, a line left out only
when it would be empty:

- the full-state sections of `command_state`: the ore fields,
  the enemy buildings, the enemy defenses and the enemy groups, the nearest
  the scout first;
- `our_base=(x,y) enemy_base=(x,y) <confirmed|spawn>`: the enemy's start is
  known from the map's spawns from the first state (see
  `scout_missions.enemy_start`), and `confirmed` once the scout report says
  its base was seen;
- `scout=<type> hp=<n>% at (x,y) sight=<cells>`;
- `danger_now:` the enemy units that fight in sight within
  `DANGER_NOW_CELLS` of the scout, by type: how many, where the nearest
  stands, how far it is and how far it shoots; the nearest type first, or
  `none`;
- `known_defense:` the remembered enemy defenses nearest the scout, each
  with its weapon range;
- `intel:` when an enemy building by the enemy's start was last in sight and
  the remembered enemy buildings by type; when an enemy unit that fights was
  last in sight, where, and what the enemy army is worth; and the ore fields
  where the enemy may expand (those `scout_missions` watches), the nearest
  the enemy's start first, each never seen, empty or with an enemy refinery,
  and how long ago;
- `current:` the scout's standing action (see `command_prompt`);
- `last_picks:` the commander's last picks for the scout, the newest first,
  each with its age;
- `t=<mm:ss> left=<mm:ss>`: the match clock and what is left of it, last;
- `ACTIONS:` and one line for each action: the verb, the cell, what the
  place is, the length of the way there in cells, and the path: `clear`
  (a safe way, around every danger, to a place once in sight), `unknown` (a
  safe way to a place never in sight) or `passes danger` (no safe way: the
  scout would go straight); then what it shows and how long it has gone
  unseen. A way out of the danger the scout stands in is judged by the
  dangers beyond it: every way out crosses that.

The actions, kept in this order up to options_max of `[commander]` and then
listed by their letters (`SCOUT_IDS`):

- `hold` (A), always: the scout stays where it is for `HOLD_SECONDS`;
- `back off` (B), while a dangerous cell (see `scout_steering`) lies within
  `BACK_OFF_DANGER_CELLS` of the scout: to the cell scout_flee_cells
  straight away from the nearest danger;
- the script's scout missions (see `scout_missions`) by their score for this
  scout, the highest first, the best of each kind alone: the enemy main's
  edge (C), an enemy expansion field (D), our expansion's approach (E),
  the attack lane (F) and an enemy home field (G); the travel of an unsafe one
  is its straight line, and one the scout stands at already is left out.

There is no glossary and no recommendation of the script's.

How the actions are carried out: a destination action (`follow`) makes its
cell the scout's destination, by a safe way when there is one, and scout
steering takes the scout there and keeps it safe; `hold` is a stop, and
steering leaves a held scout alone (see `is_held`).

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from collections import defaultdict
from functools import partial
from typing import Sequence

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.command_state import FullState, full_state
from ucla_agent.games.redalert.command_prompt import (
    NEVER,
    NONE,
    Action,
    Current,
    Detail,
    Pick,
    Question,
    Situation,
    at,
    clock,
    counts,
    current_line,
    fitted,
    lettered,
    picks_line,
    seconds_ago,
)
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD, REFINERY
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.own_fields import not_own
from ucla_agent.games.redalert.model_history import LEARN, TOURNAMENT
from ucla_agent.games.redalert.scout_missions import (
    ATTACK_LANE,
    ENEMY_HOME_FIELD,
    EXPANSION_APPROACH,
    EXPANSION_WATCH,
    MAIN_EDGE,
    UNKNOWN_SPEED_CELLS_PER_SECOND,
    WATCH_FIELD_FROM_ENEMY_CELLS,
    WATCH_FIELD_FROM_YARD_CELLS,
    Mission,
    enemy_start,
    score,
)
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.scout_steering import COMMANDER, Course, Dangers, away_from, fled_from, route
from ucla_agent.games.redalert.sight import SIGHT_CELLS, SightGrid
from ucla_agent.games.redalert.terrain import OrePatch
from ucla_agent.games.redalert.world import Building, Cell, EnemyRecord, Unit, World

_TASK = (
    "You steer our scout in Red Alert 1v1. Each turn pick ONE action ID. Goal: learn enemy army, expansions, tech; "
    "keep the scout alive."
)
_REPLY = 'Reply {"choice":"ID"}.'
SYSTEM_TEXT = f"{_TASK} {TOURNAMENT} {LEARN} {_REPLY}"
SHORT_SYSTEM_TEXT = f"{_TASK} {_REPLY}"
MAX_PROMPT_CHARS = 2500
# The kinds of action besides the missions'.
HOLD = "hold"
BACK_OFF = "back_off"
HOLD_SECONDS = 3
HOLD_TICKS = HOLD_SECONDS * TICKS_PER_SECOND
# Back off is offered while a dangerous cell lies this near the scout.
BACK_OFF_DANGER_CELLS = 10
# danger_now lists the enemy units in sight this near the scout.
DANGER_NOW_CELLS = 20
# An enemy refinery this near an ore field's middle stands on that field.
FIELD_REFINERY_CELLS = 8
# The enemy army is where its units seen within this long of the last sighting of one were.
ARMY_SIGHTING_TICKS = 2 * TICKS_PER_SECOND
CLEAR, UNKNOWN_PATH, PASSES_DANGER = "clear", "unknown", "passes danger"
# A weapon range is written to a tenth of a cell.
RANGE_DECIMALS = 1
# The letter of each kind of the scout's action.
SCOUT_IDS = {
    HOLD: "A",
    BACK_OFF: "B",
    MAIN_EDGE: "C",
    EXPANSION_WATCH: "D",
    EXPANSION_APPROACH: "E",
    ATTACK_LANE: "F",
    ENEMY_HOME_FIELD: "G",
}
# What each kind of mission watches, as an action names its place, and what being there shows.
MISSION_PLACES = {
    EXPANSION_WATCH: "expansion field",
    MAIN_EDGE: "enemy main edge",
    EXPANSION_APPROACH: "our expansion approach",
    ATTACK_LANE: "attack lane",
    ENEMY_HOME_FIELD: "enemy home field",
}
MISSION_SHOWS = {
    EXPANSION_WATCH: "expansions",
    MAIN_EDGE: "tech",
    EXPANSION_APPROACH: "raiders",
    ATTACK_LANE: "army route",
    ENEMY_HOME_FIELD: "enemy mining",
}


def question(
    scout: Unit,
    dangers: Dangers,
    sight: SightGrid,
    offered: Sequence[Mission],
    picks: Sequence[Pick],
    world: World,
    ctx: MatchContext,
    current: Current | None = None,
) -> Question:
    """The commander's question for a scout in this cycle, among the missions `offered`, `current` the scout's
    standing action (none without) (see the module's note).

    Asked once the map, both bases and the enemy's start are known. Raises `ValueError` when it cannot be held to
    `MAX_PROMPT_CHARS`.
    """
    actions = scout_actions(scout, dangers, sight, offered, world, ctx)
    closing = None if ctx.commander is None else ctx.commander.closing
    state = full_state(world, ctx, {}, (scout.x, scout.y), closing=closing)
    lines_at = partial(_lines, scout, sight, state, picks, current_line(current, actions, world), world, ctx)
    return fitted(SYSTEM_TEXT, lines_at, actions, world.tick, MAX_PROMPT_CHARS, current, SHORT_SYSTEM_TEXT)


def scout_actions(
    scout: Unit, dangers: Dangers, sight: SightGrid, offered: Sequence[Mission], world: World, ctx: MatchContext
) -> tuple[Action, ...]:
    """The scout's actions, lettered by kind: hold, back off while danger is near, then the best mission of each kind
    by score, highest first, up to options_max."""
    position = (scout.x, scout.y)
    fixed = [_hold(position)]
    back_off = _back_off(position, dangers, sight, world, ctx)
    if back_off is not None:
        fixed.append(back_off)
    room = max(0, ctx.tuning.commander.options_max - len(fixed))
    missions = _mission_actions(scout, dangers.leaving(position), sight, offered, world, ctx)
    return lettered([*fixed, *missions[:room]], SCOUT_IDS)


def situation(scout: Unit, dangers: Dangers, ctx: MatchContext) -> Situation:
    """The scout's situation for the commander's commitment to its standing action: where it stands, and whether it
    must flee a danger (see `scout_steering.fled_from`)."""
    position = (scout.x, scout.y)
    return Situation(position, fled_from(position, dangers, ctx) is not None)


def is_held(action: Action | None, taken_tick: int, world: World) -> bool:
    """Whether a scout is held in place: its standing action is `hold`, taken less than HOLD_SECONDS ago."""
    return action is not None and action.kind == HOLD and world.tick - taken_tick < HOLD_TICKS


def follow(course: Course, action: Action, scout: Unit, dangers: Dangers, world: World, ctx: MatchContext) -> None:
    """Make a destination action's cell the scout's destination, by a safe way when there is one and else straight;
    a course headed there for the commander already is kept."""
    if course.destination == action.cell and course.source == COMMANDER:
        return
    position = (scout.x, scout.y)
    way = route(position, action.cell, dangers.leaving(position), world, ctx, keeps_from_center=False)
    course.go(action.cell, way or [action.cell], COMMANDER, world.tick)


def _hold(position: Cell) -> Action:
    return Action(
        kind=HOLD,
        key=(HOLD,),
        label=HOLD,
        head=f"hold {at(position)} here {HOLD_SECONDS}s, 0 cells, path {CLEAR}",
        detail="current view",
    )


def _back_off(position: Cell, dangers: Dangers, sight: SightGrid, world: World, ctx: MatchContext) -> Action | None:
    """Back off from the nearest danger, while a dangerous cell lies within BACK_OFF_DANGER_CELLS; None otherwise."""
    nearest = dangers.nearest(position)
    if nearest is None or nearest[1] > BACK_OFF_DANGER_CELLS:
        return None
    cell = away_from(position, nearest[0], world, ctx)
    way = route(position, cell, dangers.leaving(position), world, ctx, keeps_from_center=False)
    length, path = _length(position, way or [cell]), _path(way, cell, sight)
    return Action(
        kind=BACK_OFF,
        key=(BACK_OFF,),
        label="back off",
        head=f"back off {at(cell)} from danger, {length} cells, path {path}",
        detail="safety",
        cell=cell,
    )


def _mission_actions(
    scout: Unit, dangers: Dangers, sight: SightGrid, offered: Sequence[Mission], world: World, ctx: MatchContext
) -> list[Action]:
    """An action for the best-scored mission of each kind offered that the scout does not stand at, the best score
    first; `dangers` are those its ways keep clear of."""
    position, reached = (scout.x, scout.y), ctx.tuning.scouting.scout_reached_cells
    scored = [
        _scored(mission, scout, dangers, sight, world, ctx)
        for mission in offered
        if not cells.is_within(position, mission.cell, reached)
    ]
    best: dict[str, Action] = {}
    for _, action in sorted(scored, key=lambda pair: -pair[0]):
        best.setdefault(action.kind, action)
    return list(best.values())


def _scored(
    mission: Mission, scout: Unit, dangers: Dangers, sight: SightGrid, world: World, ctx: MatchContext
) -> tuple[float, Action]:
    """A mission as an action of the scout's, with the score it has for the scout: by its safe way, or straight."""
    position = (scout.x, scout.y)
    way = route(position, mission.cell, dangers, world, ctx, keeps_from_center=False)
    length = _length(position, way or [mission.cell])
    worth = score(mission, length / _speed(scout.type, world), world.tick, ctx.tuning.scouting)
    place, cell = MISSION_PLACES[mission.kind], mission.cell
    action = Action(
        kind=mission.kind,
        key=mission.key,
        label=f"{place} {at(cell)}",
        head=f"watch {at(cell)} {place}, {length} cells, path {_path(way, cell, sight)}",
        detail=f"{MISSION_SHOWS[mission.kind]}, {_unseen(mission.seen_tick, world)}",
        cell=cell,
    )
    return worth, action


def _path(way: list[Cell] | None, cell: Cell, sight: SightGrid) -> str:
    """How the way to a cell is: no safe way, a safe way to a place never in sight, or a safe way to a known place."""
    if way is None:
        return PASSES_DANGER
    return UNKNOWN_PATH if sight.last_seen(cell) is None else CLEAR


def _unseen(seen_tick: int | None, world: World) -> str:
    return f"{NEVER} seen" if seen_tick is None else f"unseen {seconds_ago(seen_tick, world)}s"


def _length(start: Cell, way: list[Cell]) -> int:
    """How long a way is from `start` through its cells, in whole cells."""
    stops = [start, *way]
    return round(sum(cells.distance(first, second) for first, second in zip(stops, stops[1:])))


def _speed(unit_type: str, world: World) -> float:
    """How fast a unit of a type moves, in cells a second; as a rifleman when the item table does not say."""
    facts = world.items.get(unit_type)
    return facts.speed if facts is not None and facts.speed else UNKNOWN_SPEED_CELLS_PER_SECOND


def _lines(
    scout: Unit,
    sight: SightGrid,
    state: FullState,
    picks: Sequence[Pick],
    current: str,
    world: World,
    ctx: MatchContext,
    detail: Detail,
) -> list[str]:
    """The state lines of the question at a level of detail, in order, the empty ones left out: the full-state
    sections the level keeps, the scout's lines, the picks and the clock."""
    lines = [
        *state.lines(detail),
        _bases(world, ctx),
        f"scout={scout.type} hp={scout.hp}% at {at((scout.x, scout.y))} sight={SIGHT_CELLS}",
        _danger_now(scout, world, ctx, detail),
        _known_defense(scout, world, ctx, detail),
        _intel(sight, world, ctx, detail),
        current,
        picks_line(picks, detail, world, with_result=False, history=ctx.model_history),
        clock(world),
    ]
    return [line for line in lines if line]


def _bases(world: World, ctx: MatchContext) -> str:
    seen = "confirmed" if ctx.scout_report.enemy_base_seen else "spawn"
    return f"our_base={at(world.base_center)} enemy_base={at(enemy_start(world))} {seen}"


def _danger_now(scout: Unit, world: World, ctx: MatchContext, detail: Detail) -> str:
    """The enemy units that fight in sight near the scout by type, the nearest type first; `none` for none."""
    position = (scout.x, scout.y)
    by_type: dict[str, list[EnemyRecord]] = defaultdict(list)
    for record in world.enemy_memory.values():
        if record.visible and is_combat_unit(record) and cells.is_within(position, _cell(record), DANGER_NOW_CELLS):
            by_type[record.type].append(record)
    groups = sorted(by_type.values(), key=lambda group: min(cells.distance(position, _cell(r)) for r in group))
    parts = [_group(group, position, world, ctx) for group in groups[: detail.groups]]
    return "danger_now: " + ("; ".join(parts) or NONE)


def _group(group: list[EnemyRecord], position: Cell, world: World, ctx: MatchContext) -> str:
    """Enemy units of one type: how many, where the nearest stands, how far it is and how far it shoots."""
    nearest = min(group, key=lambda record: cells.distance(position, _cell(record)))
    distance = round(cells.distance(position, _cell(nearest)))
    reach = _weapon_range(nearest.type, world, ctx)
    return f"{len(group)} {nearest.type} at {at(_cell(nearest))} dist={distance} (range {reach})"


def _known_defense(scout: Unit, world: World, ctx: MatchContext, detail: Detail) -> str | None:
    """The remembered enemy defenses nearest the scout, with their range; None while none is known."""
    position = (scout.x, scout.y)
    nearest = sorted(world.known_enemy_defenses, key=lambda record: cells.distance(position, _cell(record)))
    shown = [f"{record.type} {at(_cell(record))} range {_weapon_range(record.type, world, ctx)}" for record in nearest]
    return "known_defense: " + "; ".join(shown[: detail.defenses]) if shown else None


def _weapon_range(unit_type: str, world: World, ctx: MatchContext) -> str:
    """How far a type shoots, in cells, as the item table says; the range taken for an unknown defense otherwise."""
    facts = world.items.get(unit_type)
    reach = facts.range if facts is not None and facts.range is not None else ctx.tuning.coverage.unknown_range_cells
    return f"{round(reach, RANGE_DECIMALS):g}"


def _intel(sight: SightGrid, world: World, ctx: MatchContext, detail: Detail) -> str:
    """What is known of the enemy main, its army and the fields it may expand to."""
    fields = [_field(patch, sight, world) for patch in _watched_fields(world, ctx)[: detail.fields]]
    return "intel: " + "; ".join([_enemy_main(world, ctx, detail), _enemy_army(world, ctx), *fields])


def _enemy_main(world: World, ctx: MatchContext, detail: Detail) -> str:
    """When an enemy building by the enemy's start was last in sight, and the remembered enemy buildings by type."""
    start, reach = enemy_start(world), ctx.tuning.scouting.base_seen_cells
    buildings = [record for record in world.enemy_memory.values() if record.is_building]
    by_start = [record.last_seen_tick for record in buildings if cells.is_within(start, _cell(record), reach)]
    seen = f"{NEVER} seen" if not by_start else f"seen {seconds_ago(max(by_start), world)}s ago"
    counted = counts((record.type for record in buildings), detail.building_types) if detail.building_types else ""
    return f"enemy main {seen}" + (f" ({counted})" if counted else "")


def _enemy_army(world: World, ctx: MatchContext) -> str:
    """When an enemy unit that fights was last in sight, where the units seen about then were, and what the enemy
    army is worth; `never seen` before one was."""
    units = [record for record in world.enemy_memory.values() if is_combat_unit(record)]
    if not units:
        return f"enemy army {NEVER} seen"
    last = max(record.last_seen_tick for record in units)
    recent = [_cell(record) for record in units if last - record.last_seen_tick <= ARMY_SIGHTING_TICKS]
    middle = cells.nearest_cell(sum(x for x, _ in recent) / len(recent), sum(y for _, y in recent) / len(recent))
    value = ctx.scout_report.enemy_combat_value
    return f"enemy army seen {seconds_ago(last, world)}s ago at {at(middle)} value {value}"


def _watched_fields(world: World, ctx: MatchContext) -> list[OrePatch]:
    """The ore fields the enemy may expand to: more than WATCH_FIELD_FROM_YARD_CELLS from every own construction yard
    and WATCH_FIELD_FROM_ENEMY_CELLS from the enemy's start, and none of our own (see `own_fields`), the nearest the
    enemy's start first."""
    start = enemy_start(world)
    yards = [_cell(building) for building in world.buildings.values() if building.type == CONSTRUCTION_YARD]
    yards = yards or [world.base_center]
    fields = [
        patch
        for patch in not_own(ctx.terrain.ore_patches, world, ctx.tuning.scouting.own_field_cells)
        if not any(cells.is_within(patch.center, yard, WATCH_FIELD_FROM_YARD_CELLS) for yard in yards)
        and not cells.is_within(patch.center, start, WATCH_FIELD_FROM_ENEMY_CELLS)
    ]
    return sorted(fields, key=lambda patch: cells.distance(patch.center, start))


def _field(patch: OrePatch, sight: SightGrid, world: World) -> str:
    """An ore field as intel gives it: with an enemy refinery seen on it, empty when last in sight, or never seen."""
    refineries = [
        record.last_seen_tick
        for record in world.enemy_memory.values()
        if record.type == REFINERY and cells.is_within(patch.center, _cell(record), FIELD_REFINERY_CELLS)
    ]
    if refineries:
        return f"field {at(patch.center)} refinery {seconds_ago(max(refineries), world)}s ago"
    seen = sight.last_seen(patch.center)
    return f"field {at(patch.center)} " + (
        f"{NEVER} seen" if seen is None else f"empty {seconds_ago(seen, world)}s ago"
    )


def _cell(actor: EnemyRecord | Unit | Building) -> Cell:
    return actor.x, actor.y
