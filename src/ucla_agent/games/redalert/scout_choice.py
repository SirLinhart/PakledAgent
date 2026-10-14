"""Asking a model where a designated scout goes next (`[redalert] model_scout_choice`).

The script picks a scout's next destination by how long each place has gone
unseen (see `scout_targets`). A model can weigh more than that: what was seen
at each place last, how far it is, where the other scout is going. With the
switch on and the scout slots on, each slot has a chooser of its own over the
`red_alert_scout` chain, apart from the plan's, so that no question waits for
another.

A slot asks when its scout takes a new destination (it reached the last, or
the way there became unsafe) and when its scout's pick is
scout_choice_every_ticks old, one question at a time. The options are the
script's best scout_choice_options places with a safe way from the scout,
the place the other scout is heading for and, with `scout_wide_berth` on, the
places near where a scout was hurt lately left out, numbered from 1. Each says
which area it is, its cell, how far it is from the scout, how long it has
gone unseen and what was last seen within 10 cells of it: buildings and units
by type, and defenses. The context says what the scout is, its health and
cell, where the enemy base is and where the other scout is heading; then the
model's recent picks for this scout, and those whose scout was lost, each
with what became of it and what was seen meanwhile (`recent picks:`, see
`model_history`). The question carries the framing of `model_history`
whole. A destination the model chose is settled when the scout's course
drops it, by why (`reached`; `retreated` for a way become unsafe or a
flight; `stuck`; else `redirected`), when a later answer of the model sends
the scout elsewhere (`reached` when it stands there, else `redirected`), and
as `lost` once its scout is gone. The model
writes a reason of at most 20 words before its choice, in at most 64 tokens,
and the answer is not waited for past scout_choice_deadline_ms.

The scout never waits for the model: the script's choice stands until an
answer arrives. A used answer that is fresh (no older than the loop's
choice_max_age_ticks, by the chooser's staleness rule) and names a place that
is still a candidate with a safe way from where the scout stands now becomes
its destination, chosen by the model; any other answer changes nothing. Each
question and answer is written to the match record by the decision record.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from ucla_agent.core.decide import USED, ChoiceRequest, Option
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.managers import Chooser, MatchContext
from ucla_agent.games.redalert.model_history import (
    FRAMING,
    LOST,
    REACHED,
    REDIRECTED,
    RETREATED,
    STUCK,
    ModelHistory,
)
from ucla_agent.games.redalert.scout_steering import FLED, MODEL, UNSAFE, Course, Dangers, route
from ucla_agent.games.redalert.scout_steering import REACHED as STEERING_REACHED
from ucla_agent.games.redalert.scout_steering import STUCK as STEERING_STUCK
from ucla_agent.games.redalert.scout_targets import Candidate, candidates
from ucla_agent.games.redalert.sight import SightGrid
from ucla_agent.games.redalert.world import DEFENSE_TYPES, Cell, EnemyRecord, Unit, World

QUESTION = (
    "You steer one scout in a real-time strategy match. It is to see as much of the enemy as it can and stay alive. "
    f"{FRAMING} Which place should it look at next?"
)
# What became of a destination the model chose, by why the scout's course dropped it (see `scout_steering`).
OUTCOMES = {STEERING_REACHED: REACHED, UNSAFE: RETREATED, FLED: RETREATED, STEERING_STUCK: STUCK}
# The longest reason the model writes before its choice, and the most tokens its answer may take.
REASON_WORDS = 20
MAX_ANSWER_TOKENS = 64
# What was last seen near a place is what is remembered within this many cells of it.
SEEN_NEAR_CELLS = 10
# The reason a `destination` event gives for a destination a model chose.
ANSWERED = "answered"


class ScoutQuestion:
    """The questions one scout slot puts to its chooser, one at a time. See the module's note."""

    def __init__(self, chooser: Chooser) -> None:
        self._chooser = chooser
        # Option id -> the cell it names, of the question in flight; empty while none is.
        self._options: dict[str, Cell] = {}
        # The tick the last question was put.
        self._asked_tick: int | None = None
        # The destination the model chose that the scout was heading for in the last cycle; None for none.
        self._heading: Cell | None = None

    def answer(self, world: World) -> Cell | None:
        """The cell the answer that has just arrived names, when it was used and is fresh; None otherwise, and while no
        answer has arrived."""
        result = self._chooser.poll(str(world.tick))
        if result is None:
            return None
        options, self._options = self._options, {}
        return options.get(result.option_id) if result.outcome == USED else None

    def is_due(self, course: Course, changed: bool, world: World, ctx: MatchContext) -> bool:
        """Whether to ask now: no question is in flight, and the scout has just taken a new destination or its pick is
        scout_choice_every_ticks old. Asked in every cycle of the scout in the field, it first settles the model's pick
        the scout no longer heads for (see `_follow`)."""
        self._follow(course, world, ctx.model_history)
        if self._options or course.destination is None:
            return False
        last = max(course.chosen_tick, self._asked_tick or 0)
        return changed or world.tick - last >= ctx.tuning.scouting.scout_choice_every_ticks

    def ask(
        self, scout: Unit, others: list[Cell], sight: SightGrid, dangers: Dangers, world: World, ctx: MatchContext
    ) -> None:
        """Put the question of a scout's next destination to the chooser, among the script's best places it has a safe
        way to; nothing when there is none."""
        offered = _offered(scout, others, sight, dangers, world, ctx)
        if not offered:
            return
        tuning, history = ctx.tuning.scouting, ctx.model_history
        _settle_lost(history, world)
        request = ChoiceRequest(
            question=QUESTION,
            options=[
                Option(str(number), _description(candidate, scout, world))
                for number, candidate in enumerate(offered, start=1)
            ],
            context="\n".join([_context(scout, others, world), *history.scout_lines(scout.id, world)]),
            deadline_ms=tuning.scout_choice_deadline_ms,
            state_tag=str(world.tick),
            reason_first=True,
            reason_words=REASON_WORDS,
            max_answer_tokens=MAX_ANSWER_TOKENS,
        )
        if self._chooser.submit(request):
            self._options = {str(number): candidate.cell for number, candidate in enumerate(offered, start=1)}
            self._asked_tick = world.tick

    def _follow(self, course: Course, world: World, history: ModelHistory) -> None:
        """Settle the model's pick the scout was heading for in the last cycle once its course has dropped it, by why it
        was dropped; and note the destination the model chose that it heads for now."""
        heading, self._heading = self._heading, course.destination if course.source == MODEL else None
        if heading is not None and heading != course.destination:
            _settle(history, heading, OUTCOMES.get(course.reason, REDIRECTED), world)


def take_answer(
    course: Course,
    cell: Cell,
    scout: Unit,
    others: list[Cell],
    sight: SightGrid,
    dangers: Dangers,
    world: World,
    ctx: MatchContext,
) -> bool:
    """Make the cell a model chose the scout's destination, when it is still a candidate the scout is not at already,
    with a safe way from where it stands; whether it became a new destination. A new one is one of the model's scout
    picks (see `model_history`)."""
    position = (scout.x, scout.y)
    offered = {candidate.cell: candidate for candidate in candidates(world, ctx, sight, others, dangers.hurt)}
    if cell not in offered:
        return False
    if cells.is_within(position, cell, ctx.tuning.scouting.scout_reached_cells):
        return False
    way = route(position, cell, dangers, world, ctx)
    if way is None:
        return False
    is_new = cell != course.destination
    if is_new:
        _note_pick(course, offered[cell], scout, world, ctx)
    course.go(cell, way, MODEL, world.tick)
    return is_new


def _note_pick(course: Course, candidate: Candidate, scout: Unit, world: World, ctx: MatchContext) -> None:
    """Keep a new destination the model chose as a scout pick; the model's pick the scout was heading for is settled
    first, reached when the scout stands at it, else redirected."""
    history, heading = ctx.model_history, course.destination
    if course.source == MODEL and heading is not None:
        is_there = cells.is_within((scout.x, scout.y), heading, ctx.tuning.scouting.scout_reached_cells)
        _settle(history, heading, REACHED if is_there else REDIRECTED, world)
    x, y = candidate.cell
    history.note_scout(f"{candidate.area} ({x},{y})", scout.id, candidate.cell, world)


def _settle(history: ModelHistory, cell: Cell, outcome: str, world: World) -> None:
    """Settle the open scout picks of a cell with an outcome."""
    for pick in history.open_scout_picks():
        if pick.cell == cell:
            pick.settle(outcome, world)


def _settle_lost(history: ModelHistory, world: World) -> None:
    """Settle the open scout picks whose scout is gone as lost."""
    for pick in history.open_scout_picks():
        if pick.unit_id not in world.combat_units:
            pick.settle(LOST, world)


def _offered(
    scout: Unit, others: list[Cell], sight: SightGrid, dangers: Dangers, world: World, ctx: MatchContext
) -> list[Candidate]:
    """The script's best scout_choice_options places with a safe way from the scout, the other scouts' destinations
    and the hurt areas left out."""
    position, wanted = (scout.x, scout.y), ctx.tuning.scouting.scout_choice_options
    offered: list[Candidate] = []
    for candidate in candidates(world, ctx, sight, others, dangers.hurt):
        if route(position, candidate.cell, dangers, world, ctx) is not None:
            offered.append(candidate)
        if len(offered) == wanted:
            break
    return offered


def _description(candidate: Candidate, scout: Unit, world: World) -> str:
    """An option as the model reads it: the area, its cell, how far, how long unseen and what was last seen there."""
    x, y = candidate.cell
    distance = round(cells.distance(candidate.cell, (scout.x, scout.y)))
    unseen = round(candidate.unseen_seconds)
    return (
        f"{candidate.area} at ({x}, {y}), {distance} cells away, unseen for {unseen} s; "
        f"last seen there: {_seen_near(candidate.cell, world)}"
    )


def _seen_near(cell: Cell, world: World) -> str:
    """What is remembered of the enemy within SEEN_NEAR_CELLS of a cell: buildings, units and defenses by type."""
    near = [
        record for record in world.enemy_memory.values() if cells.is_within((record.x, record.y), cell, SEEN_NEAR_CELLS)
    ]
    parts = [
        f"{name} {_by_type(records)}"
        for name, records in (
            ("buildings", [record for record in near if record.is_building and record.type not in DEFENSE_TYPES]),
            ("units", [record for record in near if not record.is_building]),
            ("defenses", [record for record in near if record.type in DEFENSE_TYPES]),
        )
        if records
    ]
    return "; ".join(parts) if parts else "nothing"


def _by_type(records: Iterable[EnemyRecord]) -> str:
    """Remembered enemies counted by type, as `e1 3, harv 1`, the types in alphabetical order."""
    counts = Counter(record.type for record in records)
    return ", ".join(f"{item} {count}" for item, count in sorted(counts.items()))


def _context(scout: Unit, others: list[Cell], world: World) -> str:
    """What the model is told of the situation: the scout, the enemy base and where the other scouts are heading."""
    heading = "; ".join(f"({x}, {y})" for x, y in others) or "nowhere yet"
    enemy_x, enemy_y = world.enemy_base
    return (
        f"Scout: {scout.type} at ({scout.x}, {scout.y}), {scout.hp}% health. "
        f"Enemy base: ({enemy_x}, {enemy_y}). The other scouts are heading to: {heading}."
    )
