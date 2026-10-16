"""The first scout's look at the enemy base and its watch at the enemy's exit (`[scouting] rush_scout_direct`).

A scout that stands off the enemy's start sees nothing of what is trained
there, and a rush is told by the army it sends. So with `rush_detection` and
`rush_scout_direct` on, the scout of the first scout slot, whatever unit
holds it, is steered by this rule until rush_watch_until_seconds, in place
of the slots' missions and places:

- **The pass.** It drives straight at the enemy's start until it has been
  within rush_scout_pass_cells of it, so that the construction yard and what
  stands around it come into its sight. A scout hit on the way (its health
  down by scout_retreat_hp_drop or more since the last state) ends the pass
  there, and so does one that has been PASS_LEG_SECONDS within
  rush_exit_post_cells of the enemy's start without getting through, or that
  stands idle short of it for as long: buildings in the way stop it.
- **The post.** Then, and until the watch ends, it stands at the exit post:
  the cell rush_exit_post_cells from the enemy's start on the straight line
  toward our base_center, inside the map, or the nearest cell on that line
  toward our base that the terrain lets it stand on. An army that leaves for
  our base passes it, and the rush watch sees it leave. It is sent back to
  the post whenever it is not there. With `sight_by_type` on, the post is
  where the scout sees the exit: its sight less 1 beyond the exit cell
  rush_exit_watch_cells from the enemy's start on that line, moved out along
  it to the first cell outside the reach of every known enemy defense (see
  `coverage`). When it was moved out, the exit lies beyond the scout's sight:
  it peeks in to the post in sight for scout_peek_hold_seconds of every
  scout_peek_seconds, and backs off to the safe post again.

With `infantry_scout_run_through` on, an infantry scout (a rifleman, a dog)
makes the pass as a run through the enemy base instead: riflemen are cheap
and slow, and one that turns back at the first shot has seen nothing. It
walks three legs, the entry cell, the enemy's start and the exit cell beyond
it, each leg over once the scout is within scout_reached_cells of it (the
start within rush_scout_pass_cells), or has stood idle RUN_STUCK_SECONDS short
of it: buildings in the way stop it. The approach turns with every run of
the match, direct, left, right (`APPROACHES`), the first by the match's
session id, so that the enemy does not see it come the same way each time:
direct enters rush_exit_post_cells in front of the start on the line from
our base and leaves scout_run_exit_cells behind it; a flank enters
scout_run_side_cells to one side of that front cell and leaves as far to the
other side behind the start. A hit or a retreat does not end the run: it
holds fire and keeps moving until the exit cell, then stands at the post.
With `scout_loss_memory` on, a run whose scout is lost on the way is over: the
next scout's run takes the next approach, and of the approaches from there on
the first whose legs keep scout_lost_avoid_cells from every cell a scout of
the slots was lost on (`lost_cells`, kept by `scout_slots`).
With `run_through_guard` on as well, the run has one owner: the slots' retreats
and bait shots leave its scout alone (see `scout_slots`). A scout hit with no
enemy in sight within SUSPECT_SIGHT_CELLS of it marks a suspected defense
SUSPECT_AHEAD_CELLS ahead of it toward its leg (a pillbox outranges a
rifleman's sight), kept with the lost cells; a scout that stands on one cell
STILL_HIT_SECONDS while losing health goes on to its next leg at once, never
idle under fire. The run passes the enemy's start only while no scout was
lost nor a defense suspected within CORE_LOST_CELLS of it; else it passes the
first of the cells PASS_OFFSET_CELLS to either side of it or before it that
keeps PASS_OFFSET_CELLS from every such cell. After CORE_LOSSES_MAX scouts
lost in the enemy's core within RUN_PAUSE_SECONDS, no infantry run is made
for RUN_PAUSE_SECONDS: the rush scout stands at the post. In the live games
of 20261016 one pillbox, never seen, killed three scouts that all ran at the
enemy's start, and a run-through scout stood under fire while its retreat
and its run gave it opposite orders.
A vehicle scout makes the pass as before. Each run is two `scout_slot`
events of kind `run_through`, phases `start` (with its approach) and `done`.

The pass and the post override the keep-off ground and the wide berth the
slots' scouts otherwise keep: the scout is sent into the enemy base on
purpose. It still holds fire (see `scout_stance`). Once it has stood at the
post, a retreat of the cautious scouts (see `scout_retreat`) steps it back
and it returns when the retreat is over; on the pass a retreat other than
the hit is not taken, and the hit sends it to the post rather than home. Its
slot releases it only below scout_release_below_hp, the wide berth's higher
threshold aside. The model commander's scout question does not take that
slot until the watch ends (see `scout_slots`). From rush_watch_until_seconds
the slot's scout goes on the slots' missions like the others. The unit that
stood at the post is remembered (`post_unit`), so that the canary rule can
take a rifleman that stood there (see `canary`).
"""

from __future__ import annotations

import math
import zlib
from typing import Sequence

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, coverage
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.production import INFANTRY_QUEUE
from ucla_agent.games.redalert.scout_steering import CHANGE_URGENCY, IDLE_URGENCY, KEEP_URGENCY, Course, Move
from ucla_agent.games.redalert.sight import sight_of
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.world import DEFENSE_TYPES, Cell, Unit, World

# The slot whose scout makes the pass and stands at the post.
RUSH_SCOUT_SLOT = 1
# Who chose a course: this rule.
RUSH_SCOUT = "rush_scout"
# The stages of the rule: on the pass through the enemy base, and at the exit post.
PASS = "pass"
POST = "post"
# A pass that has gone on this long within the post's distance of the enemy's start, or stood idle this long short of
# it, is over.
PASS_LEG_SECONDS = 30
# A run's leg is over once its scout has stood idle this long short of it: a building or the edge of the map stops it.
RUN_STUCK_SECONDS = 5
# The approaches of an infantry run through the enemy base, in their turn.
DIRECT = "direct"
LEFT = "left"
RIGHT = "right"
APPROACHES = (DIRECT, LEFT, RIGHT)
# Which side of the line from our base each approach enters on: left is +1 across, right -1.
_SIDES = {DIRECT: 0, LEFT: 1, RIGHT: -1}
SCOUT_SLOT_EVENT = "scout_slot"
RUN_THROUGH = "run_through"
RUN_START = "start"
RUN_DONE = "done"
# With run_through_guard on (see the module's note): a hit with no enemy in sight this near suspects a defense this
# far ahead; a scout standing still this long while losing health moves on; the enemy's start is passed only with no
# loss within CORE_LOST_CELLS, else a cell PASS_OFFSET_CELLS from it; CORE_LOSSES_MAX losses there pause the runs.
SUSPECT_SIGHT_CELLS = 8
SUSPECT_AHEAD_CELLS = 5
STILL_HIT_SECONDS = 2
CORE_LOST_CELLS = 12
PASS_OFFSET_CELLS = 10
CORE_LOSSES_MAX = 2
RUN_PAUSE_SECONDS = 240


class RushScout:
    """The pass and the post of the first slot's scout, kept for one match. See the module's note."""

    def __init__(self) -> None:
        self.stage = PASS
        # The tick the pass came within the post's distance of the enemy's start, and the tick its scout began to
        # stand idle short of it after it was sent there; None before, and the second while it is on the move.
        self._near_tick: int | None = None
        self._idle_tick: int | None = None
        # The scout's health in the last state, by unit id.
        self._hp: dict[int, int] = {}
        # Whether a scout has stood at the post: from then on a retreat steps it back. And the last unit that stood
        # there; None before any did.
        self._has_stood = False
        self.post_unit: int | None = None
        # The legs of the infantry run in force, the one being walked and since when its scout stands idle short of
        # it; and how many runs the match has made, which turns the approach.
        self._legs: list[Cell] = []
        self._leg = 0
        self._stuck_since: int | None = None
        self.runs = 0
        # The scout of the run in force, and the cells the slots' scouts were lost on (see `scout_slots`).
        self._runner: int | None = None
        self.lost_cells: list[Cell] = []
        # The tick each cell of lost_cells was noted at, and where and since when, with what health, the runner stands.
        self._lost_ticks: list[int] = []
        self._still: tuple[Cell, int, int] | None = None
        self._still_tick = 0

    def note_lost(self, cell: Cell, tick: int) -> None:
        """Note a cell a scout of the slots was lost on, or a defense is suspected on, at a tick."""
        self.lost_cells.append(cell)
        self._lost_ticks.append(tick)

    def is_running(self, scout: Unit, world: World, ctx: MatchContext) -> bool:
        """With run_through_guard on, whether the rule's run owns its scout now: on the pass, an infantry runner,
        and the runs not paused; the slots' retreats and attacks leave it alone then."""
        is_guarded = ctx.tuning.scouting.run_through_guard
        return (
            is_guarded and self.stage == PASS and is_runner(scout.type, world, ctx) and not self._is_paused(world, ctx)
        )

    def _is_paused(self, world: World, ctx: MatchContext) -> bool:
        """With run_through_guard on, whether CORE_LOSSES_MAX of the cells noted within RUN_PAUSE_SECONDS lie within
        CORE_LOST_CELLS of the enemy's start: the runs wait."""
        if not ctx.tuning.scouting.run_through_guard or world.enemy_base is None:
            return False
        since = world.tick - RUN_PAUSE_SECONDS * TICKS_PER_SECOND
        core = [
            cell
            for cell, tick in zip(self.lost_cells, self._lost_ticks)
            if tick >= since and cells.is_within(cell, world.enemy_base, CORE_LOST_CELLS)
        ]
        return len(core) >= CORE_LOSSES_MAX

    @staticmethod
    def is_steering(world: World, ctx: MatchContext) -> bool:
        """Whether the rule steers the first slot's scout in this cycle: both switches on, the watch not over, and the
        map known."""
        tuning = ctx.tuning.scouting
        is_on = tuning.rush_detection and tuning.rush_scout_direct
        is_known = world.enemy_base is not None and world.base_center is not None and world.map_bounds is not None
        return is_on and is_known and world.game_seconds < tuning.rush_watch_until_seconds

    def move(self, scout: Unit, retreat: Move | None, course: Course, world: World, ctx: MatchContext) -> Move | None:
        """The scout's move in this cycle, its course set on the pass's or the post's cell; None while it stands at the
        post. `retreat` is its retreat's move, if it retreats."""
        is_hit = self._is_hit(scout, ctx)
        if self.stage == PASS and is_runner(scout.type, world, ctx) and self._is_paused(world, ctx):
            self.stage = POST
        if self.stage == PASS and is_runner(scout.type, world, ctx):
            run = self._run(scout, course, world, ctx, is_hit)
            if run is not None:
                return run
            self.stage = POST
        if self.stage == PASS and (is_hit or self._is_pass_over(scout, course, world, ctx)):
            self.stage = POST
        if self.stage == PASS:
            return _sent(world.enemy_base, course, scout, world.tick)
        post = watch_post(scout.type, world, ctx)
        at_post = cells.is_within((scout.x, scout.y), post, ctx.tuning.scouting.scout_reached_cells)
        self._note_stood(scout.id, at_post)
        if retreat is not None and self._has_stood:
            return retreat
        if at_post and scout.idle:
            course.go(post, [post], RUSH_SCOUT, world.tick)
            return None
        return _sent(post, course, scout, world.tick)

    def _run(self, scout: Unit, course: Course, world: World, ctx: MatchContext, is_hit: bool = False) -> Move | None:
        """The infantry scout's move on its run through the enemy base (see the module's note), `is_hit` saying it lost
        health since the last state; None once the run is over, the exit cell reached."""
        if self._legs and self._runner != scout.id and ctx.tuning.scouting.scout_loss_memory:
            # The run's scout was lost on the way: the next comes another way.
            self._legs, self.runs = [], self.runs + 1
        if not self._legs:
            approach = self._approach_clear_of_losses(world, ctx)
            self._legs, self._leg, self._stuck_since = run_legs(approach, world, ctx, self.lost_cells), 0, None
            self._runner = scout.id
            _log_run(RUN_START, scout, world.tick, approach=approach, legs=[list(leg) for leg in self._legs])
        if ctx.tuning.scouting.run_through_guard:
            self._guard(scout, is_hit, world)
        while self._leg < len(self._legs) and (
            self._is_leg_over(scout, course, world, ctx) or self._is_hit_still(scout)
        ):
            self._leg, self._stuck_since, self._still = self._leg + 1, None, None
        if self._leg < len(self._legs):
            return _sent(self._legs[self._leg], course, scout, world.tick)
        self._legs, self.runs = [], self.runs + 1
        _log_run(RUN_DONE, scout, world.tick)
        return None

    def _guard(self, scout: Unit, is_hit: bool, world: World) -> None:
        """Note a defense suspected ahead of a runner hit with no enemy in sight near it, and follow where and with
        what health it stands (see the module's note)."""
        here = (scout.x, scout.y)
        if is_hit and self._leg < len(self._legs):
            seen = any(
                record.visible and cells.is_within((record.x, record.y), here, SUSPECT_SIGHT_CELLS)
                for record in world.enemy_memory.values()
            )
            if not seen:
                self.note_lost(cells.toward(here, self._legs[self._leg], SUSPECT_AHEAD_CELLS), world.tick)
        if self._still is None or self._still[0] != here:
            self._still = (here, world.tick, scout.hp)
        self._still_tick = world.tick

    def _is_hit_still(self, scout: Unit) -> bool:
        """Whether the runner has stood on one cell STILL_HIT_SECONDS while losing health: it moves on."""
        if self._still is None:
            return False
        cell, since, hp = self._still
        is_still = cell == (scout.x, scout.y) and self._still_tick - since >= STILL_HIT_SECONDS * TICKS_PER_SECOND
        return is_still and scout.hp < hp

    def _approach_clear_of_losses(self, world: World, ctx: MatchContext) -> str:
        """The approach of the next run: the one its turn gives, else with scout_loss_memory on the first after it
        whose legs keep scout_lost_avoid_cells from every cell a scout was lost on; its turn's when none does."""
        turn = approach_of(self.runs, ctx.session_id)
        tuning = ctx.tuning.scouting
        if not tuning.scout_loss_memory or not self.lost_cells:
            return turn
        for step in range(len(APPROACHES)):
            approach = approach_of(self.runs + step, ctx.session_id)
            legs = run_legs(approach, world, ctx, self.lost_cells)
            if not any(_passes_near(legs, lost, tuning.scout_lost_avoid_cells) for lost in self.lost_cells):
                self.runs += step
                return approach
        return turn

    def _is_leg_over(self, scout: Unit, course: Course, world: World, ctx: MatchContext) -> bool:
        """Whether the leg being walked is over: its scout is within scout_reached_cells of it (of the enemy's start,
        rush_scout_pass_cells), or has stood idle RUN_STUCK_SECONDS short of it once sent there."""
        tuning, leg, position = ctx.tuning.scouting, self._legs[self._leg], (scout.x, scout.y)
        reach = tuning.rush_scout_pass_cells if leg == world.enemy_base else tuning.scout_reached_cells
        if cells.is_within(position, leg, reach):
            return True
        is_stuck = scout.idle and course.sent == leg
        self._stuck_since = (self._stuck_since or world.tick) if is_stuck else None
        return self._stuck_since is not None and world.tick - self._stuck_since >= RUN_STUCK_SECONDS * TICKS_PER_SECOND

    def _note_stood(self, unit_id: int, at_post: bool) -> None:
        """Note a unit standing at the post: a scout has stood there, and that unit last."""
        if at_post:
            self._has_stood, self.post_unit = True, unit_id

    def _is_hit(self, scout: Unit, ctx: MatchContext) -> bool:
        """Whether the scout's health fell by scout_retreat_hp_drop or more since the last state."""
        last, self._hp = self._hp.get(scout.id), {scout.id: scout.hp}
        return last is not None and last - scout.hp >= ctx.tuning.scouting.scout_retreat_hp_drop

    def _is_pass_over(self, scout: Unit, course: Course, world: World, ctx: MatchContext) -> bool:
        """Whether the pass is over: the scout has been within rush_scout_pass_cells of the enemy's start, or
        PASS_LEG_SECONDS have gone by since it came within the post's distance of it, or since it began to stand idle
        short of it once sent there."""
        tuning, position, tick = ctx.tuning.scouting, (scout.x, scout.y), world.tick
        if cells.is_within(position, world.enemy_base, tuning.rush_scout_pass_cells):
            return True
        if self._near_tick is None and cells.is_within(position, world.enemy_base, tuning.rush_exit_post_cells):
            self._near_tick = tick
        is_stuck = scout.idle and course.sent == world.enemy_base
        self._idle_tick = (self._idle_tick or tick) if is_stuck else None
        leg_ticks = PASS_LEG_SECONDS * TICKS_PER_SECOND
        return any(start is not None and tick - start >= leg_ticks for start in (self._near_tick, self._idle_tick))


def exit_post(world: World, terrain: Terrain, post_cells: int) -> Cell:
    """The cell `post_cells` from the enemy's start toward our base_center, inside the map; when the terrain does not
    let a unit stand there, the nearest cell on that line toward our base that it does, else the cell itself."""
    enemy, base = world.enemy_base, world.base_center
    length = cells.distance(enemy, base)
    for along in range(post_cells, max(post_cells, int(length)) + 1):
        cell = cells.clamped(cells.toward(enemy, base, along), world.map_bounds)
        if cell not in terrain.impassable:
            return cell
    return cells.clamped(cells.toward(enemy, base, post_cells), world.map_bounds)


def is_runner(scout_type: str, world: World, ctx: MatchContext) -> bool:
    """Whether a scout of a type runs through the enemy base: infantry_scout_run_through is on and the Infantry queue
    makes it."""
    facts = world.items.get(scout_type)
    return ctx.tuning.scouting.infantry_scout_run_through and facts is not None and facts.queue == INFANTRY_QUEUE


def approach_of(runs: int, session_id: str | None) -> str:
    """The approach of a match's run after `runs` runs: turning through APPROACHES, the first by the session id (the
    first approach without one)."""
    first = zlib.crc32(session_id.encode("utf-8")) if session_id else 0
    return APPROACHES[(first + runs) % len(APPROACHES)]


def run_legs(approach: str, world: World, ctx: MatchContext, lost: Sequence[Cell] = ()) -> list[Cell]:
    """The entry cell, the pass cell (the enemy's start, or with run_through_guard on and a cell `lost` near it the
    first offset cell clear of them) and the exit cell of a run by an approach (see the module's note), inside the
    map; an entry or exit the terrain does not let a unit stand on is moved toward the start until it does."""
    tuning, start, base = ctx.tuning.scouting, world.enemy_base, world.base_center
    length = cells.distance(start, base) or 1.0
    along = ((base[0] - start[0]) / length, (base[1] - start[1]) / length)
    # Across the line is its direction turned a quarter: (x, y) -> (y, -x).
    across = (along[1], -along[0])
    side = _SIDES[approach] * tuning.scout_run_side_cells

    def at(forward: float, sideways: float) -> Cell:
        point = (
            start[0] + forward * along[0] + sideways * across[0],
            start[1] + forward * along[1] + sideways * across[1],
        )
        cell = cells.clamped(cells.nearest_cell(*point), world.map_bounds)
        return _standable(cell, start, ctx)

    middle = start
    if tuning.run_through_guard and any(cells.is_within(cell, start, CORE_LOST_CELLS) for cell in lost):
        offsets = [at(0, PASS_OFFSET_CELLS), at(0, -PASS_OFFSET_CELLS), at(PASS_OFFSET_CELLS, 0)]
        clear = (cell for cell in offsets if all(not cells.is_within(cell, other, PASS_OFFSET_CELLS) for other in lost))
        middle = next(clear, start)
    return [at(tuning.rush_exit_post_cells, side), middle, at(-tuning.scout_run_exit_cells, -side)]


def _passes_near(legs: list[Cell], cell: Cell, reach: float) -> bool:
    """Whether a run's way, its legs walked in turn but the enemy's start the run is to see, passes within `reach` of
    a cell."""
    return any(
        cells.is_within(step, cell, reach)
        for first, second in zip(legs, legs[1:])
        for step in cells.line(first, second)
        if step != legs[1]
    )


def _standable(cell: Cell, start: Cell, ctx: MatchContext) -> Cell:
    """`cell`, or the first cell from it toward `start` that the terrain lets a unit stand on; `start` when none is."""
    for step in range(int(cells.distance(cell, start)) + 1):
        moved = cells.toward(cell, start, step)
        if moved not in ctx.terrain.impassable:
            return moved
    return start


def _log_run(phase: str, scout: Unit, tick: int, **more: object) -> None:
    """Write one `scout_slot` event of kind `run_through`."""
    event = {
        "kind": RUN_THROUGH,
        "phase": phase,
        "tick": tick,
        "slot": RUSH_SCOUT_SLOT,
        "unit_id": scout.id,
        "unit_type": scout.type,
    }
    records.log(SCOUT_SLOT_EVENT, {**event, **more})


def watch_post(scout_type: str, world: World, ctx: MatchContext) -> Cell:
    """Where a scout of a type stands to watch the enemy's exit in this cycle: the exit post of rush_exit_post_cells
    with sight_by_type off; with it on, the post in sight of the exit cell (`posts`), or while that is in reach of a
    known enemy defense, the safe post but for scout_peek_hold_seconds of every scout_peek_seconds."""
    tuning = ctx.tuning.scouting
    if not tuning.sight_by_type:
        return exit_post(world, ctx.terrain, tuning.rush_exit_post_cells)
    in_sight, safe = posts(scout_type, world, ctx)
    peek_ticks = tuning.scout_peek_seconds * TICKS_PER_SECOND
    is_peeking = world.tick % peek_ticks < tuning.scout_peek_hold_seconds * TICKS_PER_SECOND
    return in_sight if is_peeking else safe


def posts(scout_type: str, world: World, ctx: MatchContext) -> tuple[Cell, Cell]:
    """The post from which a scout of a type sees the exit cell rush_exit_watch_cells from the enemy's start (its sight
    less 1 beyond it), and the first post from there out toward our base outside the reach of every known enemy
    defense (`coverage.is_covered`); the post in sight itself when it is outside, or when no post short of our base
    is."""
    tuning = ctx.tuning.scouting
    sees = sight_of(scout_type, world.items, by_type=True)
    near = tuning.rush_exit_watch_cells + max(0, math.floor(sees) - 1)
    in_sight = exit_post(world, ctx.terrain, near)
    defenses = [record for record in world.enemy_memory.values() if record.is_building and record.type in DEFENSE_TYPES]
    farthest = math.floor(cells.distance(world.enemy_base, world.base_center))
    for along in range(near, farthest + 1):
        post = exit_post(world, ctx.terrain, along)
        if not coverage.is_covered(post, defenses, world.items, ctx.tuning.coverage):
            return in_sight, post
    return in_sight, in_sight


def _sent(cell: Cell, course: Course, scout: Unit, tick: int) -> Move:
    """The move to a cell, the scout's course set on it: urgent when the scout was not sent there yet, then when it
    stands idle short of it."""
    course.go(cell, [cell], RUSH_SCOUT, tick)
    if cell != course.sent:
        return Move(cell, CHANGE_URGENCY)
    return Move(cell, IDLE_URGENCY if scout.idle else KEEP_URGENCY)
