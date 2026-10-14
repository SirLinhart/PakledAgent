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
  the post whenever it is not there.

The pass and the post override the keep-off ground and the wide berth the
slots' scouts otherwise keep: the scout is sent into the enemy base on
purpose. It still holds fire (see `scout_stance`). Once it has stood at the
post, a retreat of the cautious scouts (see `scout_retreat`) steps it back
and it returns when the retreat is over; on the pass a retreat other than
the hit is not taken, and the hit sends it to the post rather than home. Its
slot releases it only below scout_release_below_hp, the wide berth's higher
threshold aside. The model commander's scout question does not take that
slot until the watch ends (see `scout_slots`). From rush_watch_until_seconds
the slot's scout goes on the slots' missions like the others.
"""

from __future__ import annotations

from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.scout_steering import CHANGE_URGENCY, IDLE_URGENCY, KEEP_URGENCY, Course, Move
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.world import Cell, Unit, World

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
        # Whether a scout has stood at the post: from then on a retreat steps it back.
        self._has_stood = False

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
        if self.stage == PASS and (is_hit or self._is_pass_over(scout, course, world, ctx)):
            self.stage = POST
        if self.stage == PASS:
            return _sent(world.enemy_base, course, scout, world.tick)
        post = exit_post(world, ctx.terrain, ctx.tuning.scouting.rush_exit_post_cells)
        at_post = cells.is_within((scout.x, scout.y), post, ctx.tuning.scouting.scout_reached_cells)
        self._has_stood = self._has_stood or at_post
        if retreat is not None and self._has_stood:
            return retreat
        if at_post and scout.idle:
            course.go(post, [post], RUSH_SCOUT, world.tick)
            return None
        return _sent(post, course, scout, world.tick)

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


def _sent(cell: Cell, course: Course, scout: Unit, tick: int) -> Move:
    """The move to a cell, the scout's course set on it: urgent when the scout was not sent there yet, then when it
    stands idle short of it."""
    course.go(cell, [cell], RUSH_SCOUT, tick)
    if cell != course.sent:
        return Move(cell, CHANGE_URGENCY)
    return Move(cell, IDLE_URGENCY if scout.idle else KEEP_URGENCY)
