"""The canary rifleman (`[scouting] canary_post`): one cheap unit posted between the bases while main attacks.

An army out on the attack cannot see the enemy's army leave for our base by
another way. So on every attack that sets out (see `attack_approach`) one
rifleman stands on the straight line from the enemy's start toward our
base, where that army would pass it, and its death is the warning.

**The post** (`post_cell`): walking that line from the enemy's start E toward
our base_center B, the first cell canary_post_cells, then one cell farther
and so on up to |EB| less canary_home_min_cells, that is inside the map, not
impassable, outside the coverage of every known enemy ground defense (its
reach, see `coverage`) plus canary_clear_margin_cells, and farther than
canary_building_clear_cells from every known enemy building; the midpoint of
E and B when none is. It is worked out afresh each time an attack sets out.

**Who is the canary**, in the cycle the attack sets out: the canary posted
before, while it lives; else the rush watch's exit-post rifleman (see
`rush_scout`), taken out of its group (its scout slot is emptied); else one
rifleman is ordered on the Infantry queue, claimed for that one order, and
the next new rifleman to come out is the canary. An order not seen in a queue
or on its way for ORDER_LOST_SECONDS may be placed again, never two at a
time, and none while the canary alarm is in force (see `canary_alarm`). There
is never more than one canary; a canary lost is replaced only when the next
attack sets out. There is none before the opening's build is complete, nor
while our base or the enemy's start is unknown.

**Its conduct.** The canary is in the group `canary`, which no other
manager orders. It is moved to its post, again whenever it stands farther
than POST_CELLS from it, holds fire as the scouts do (see `scout_stance`),
and stays there after the attack ends until it is killed or moved to the
next post.

**Its death** is published: `MatchContext.canary_lost_tick`, the tick of the
first state its id is missing from our units (not by boarding a transport),
and whether an attack was out then (`canary_lost_while_out`). `scout_slot`
events of kind `canary` and phase `posted` (cell, unit id, source `kept`,
`rush_post` or `trained`) and `lost` (cell, tick).
"""

from __future__ import annotations

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, coverage
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.family import RIFLE_INFANTRY
from ucla_agent.games.redalert.fight import ground_defenses
from ucla_agent.games.redalert.groups import CANARY, SCOUT
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.opening import is_build_complete
from ucla_agent.games.redalert.production import INFANTRY_QUEUE
from ucla_agent.games.redalert.scout_stance import HOLD_FIRE, stance_order
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.world import Cell, World

SCOUT_SLOT_EVENT = "scout_slot"
CANARY_KIND = "canary"
# The phases of a `canary` event, and where a canary posted came from.
POSTED = "posted"
LOST = "lost"
KEPT = "kept"
RUSH_POST = "rush_post"
TRAINED = "trained"
# Who claims the Infantry queue for the canary's rifleman.
CLAIMANT = "canary"
# An order of the canary's rifleman not seen in a queue or on its way this long may be placed again.
ORDER_LOST_SECONDS = 2
# The canary farther than this from its post is sent there again.
POST_CELLS = 2


def post_cell(world: World, terrain: Terrain, ctx: MatchContext) -> Cell:
    """The canary's post on the line from the enemy's start toward our base (see the module's note)."""
    tuning = ctx.tuning.scouting
    enemy, base, bounds = world.enemy_base, world.base_center, world.map_bounds
    farthest = int(cells.distance(enemy, base)) - tuning.canary_home_min_cells
    clear = _Clear(world, terrain, ctx)
    for along in range(tuning.canary_post_cells, farthest + 1):
        cell = cells.toward(enemy, base, along)
        if clear.holds(cell):
            return cell
    middle = cells.nearest_cell((enemy[0] + base[0]) / 2, (enemy[1] + base[1]) / 2)
    return middle if bounds is None else cells.clamped(middle, bounds)


class _Clear:
    """What a cell must be clear of to be the canary's post: off the map's edge, impassable ground, the known ground
    defenses' coverage and its margin, and the known enemy buildings."""

    def __init__(self, world: World, terrain: Terrain, ctx: MatchContext) -> None:
        tuning = ctx.tuning
        margin = tuning.scouting.canary_clear_margin_cells
        self._bounds, self._impassable = world.map_bounds, terrain.impassable
        self._defenses = [
            ((record.x, record.y), coverage.reach(record.type, world.items, tuning.coverage) + margin)
            for record in ground_defenses(world)
        ]
        self._buildings = [(record.x, record.y) for record in world.enemy_memory.values() if record.is_building]
        self._building_cells = tuning.scouting.canary_building_clear_cells

    def holds(self, cell: Cell) -> bool:
        """Whether a cell is clear for the post."""
        if self._bounds is not None and cells.clamped(cell, self._bounds) != cell:
            return False
        if cell in self._impassable:
            return False
        if any(cells.is_within(cell, at, reach) for at, reach in self._defenses):
            return False
        return not any(cells.is_within(cell, at, self._building_cells) for at in self._buildings)


class Canary:
    """The canary of one match. See the module's note."""

    def __init__(self) -> None:
        self.unit_id: int | None = None
        self.post: Cell | None = None
        # The set-out tick of the attack the canary was last posted for; None before the first.
        self._posted_for: int | None = None
        # Whether a rifleman is awaited for the canary, and the last tick its order was placed or seen on its way.
        self._awaiting = False
        self._order_seen_tick = 0
        # The own riflemen of the cycle before, so that a new one is told apart.
        self._rifles: set[int] = set()
        # The cell the canary was last sent to.
        self._sent: Cell | None = None

    def intents(self, world: World, ctx: MatchContext, rush_post_unit: int | None = None) -> list[Intent]:
        """The canary's orders of the cycle: its death noted, a new attack set out given a canary at its post, the
        rifleman awaited taken in or ordered, and the canary moved to its post. `rush_post_unit` is the unit that stood
        at the rush watch's exit post, if any."""
        if not ctx.tuning.scouting.canary_post:
            ctx.claims.release(INFANTRY_QUEUE, CLAIMANT)
            return []
        self._account(world, ctx)
        orders: list[Intent] = []
        if self._is_due(world, ctx):
            self._posted_for = ctx.attack_out_since
            self.post = post_cell(world, ctx.terrain, ctx)
            orders += self._choose(world, ctx, rush_post_unit)
        orders += self._take_new(world, ctx)
        orders += self._order_rifle(world, ctx)
        orders += self._to_post(world)
        self._rifles = {unit_id for unit_id, unit in world.combat_units.items() if unit.type == RIFLE_INFANTRY}
        return orders

    def _account(self, world: World, ctx: MatchContext) -> None:
        """Publish the canary's death, the first state its id is missing from our units but aboard a transport."""
        unit_id = self.unit_id
        if unit_id is None or unit_id in world.combat_units or world.aboard(unit_id) is not None:
            return
        ctx.canary_lost_tick, ctx.canary_lost_while_out = world.tick, ctx.attack_out
        _log(LOST, cell=None if self.post is None else list(self.post), tick=world.tick, unit_id=unit_id)
        self.unit_id, self._sent = None, None

    def _is_due(self, world: World, ctx: MatchContext) -> bool:
        """Whether an attack has set out that the canary was not posted for, with both bases known and the opening's
        build complete."""
        if not ctx.attack_out or ctx.attack_out_since == self._posted_for:
            return False
        if world.base_center is None or world.enemy_base is None:
            return False
        return is_build_complete(ctx.production, ctx.tuning.opening.order)

    def _choose(self, world: World, ctx: MatchContext, rush_post_unit: int | None) -> list[Intent]:
        """Who is the canary for the attack set out: the canary alive, else the rush post's rifleman, else a rifleman
        awaited. The stance order of a unit made the canary."""
        if self.unit_id is not None:
            self._sent = None
            _log(POSTED, cell=list(self.post), unit_id=self.unit_id, source=KEPT, tick=world.tick)
            return []
        rifle = world.combat_units.get(rush_post_unit) if rush_post_unit is not None else None
        if rifle is not None and rifle.type == RIFLE_INFANTRY:
            return self._make_canary(rifle.id, RUSH_POST, world, ctx)
        self._awaiting, self._order_seen_tick = True, world.tick - ORDER_LOST_SECONDS * TICKS_PER_SECOND
        return []

    def _take_new(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The next new rifleman, while one is awaited, made the canary."""
        if not self._awaiting:
            return []
        new = sorted(
            unit_id
            for unit_id, unit in world.combat_units.items()
            if unit.type == RIFLE_INFANTRY and unit_id not in self._rifles and ctx.groups.group_of(unit_id) != SCOUT
        )
        if not new:
            return []
        self._awaiting = False
        ctx.claims.release(INFANTRY_QUEUE, CLAIMANT)
        return self._make_canary(new[0], TRAINED, world, ctx)

    def _order_rifle(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order of the rifleman awaited, on the Infantry queue claimed for it, when none is seen on its way for
        ORDER_LOST_SECONDS; none while the canary alarm is in force."""
        if not self._awaiting or ctx.canary_alarm:
            ctx.claims.release(INFANTRY_QUEUE, CLAIMANT)
            return []
        production = ctx.production
        if production.pending(RIFLE_INFANTRY) > 0:
            self._order_seen_tick = world.tick
            return []
        if world.tick - self._order_seen_tick < ORDER_LOST_SECONDS * TICKS_PER_SECOND:
            return []
        if RIFLE_INFANTRY not in world.available_production:
            return []
        ctx.claims.hold(INFANTRY_QUEUE, CLAIMANT)
        self._order_seen_tick = world.tick
        return [production.train(RIFLE_INFANTRY, 1, Priority.PRODUCTION)]

    def _make_canary(self, unit_id: int, source: str, world: World, ctx: MatchContext) -> list[Intent]:
        """Move a unit into the canary group, with its `posted` event and its order to hold fire."""
        ctx.groups.assign(unit_id, CANARY)
        self.unit_id, self._sent = unit_id, None
        _log(POSTED, cell=list(self.post), unit_id=unit_id, source=source, tick=world.tick)
        return [stance_order(unit_id, HOLD_FIRE)]

    def _to_post(self, world: World) -> list[Intent]:
        """The canary's move to its post: when it was not sent there, or stands idle farther than POST_CELLS from it."""
        canary = None if self.unit_id is None else world.combat_units.get(self.unit_id)
        if canary is None or self.post is None:
            return []
        is_there = cells.is_within((canary.x, canary.y), self.post, POST_CELLS)
        if self._sent == self.post and (is_there or not canary.idle):
            return []
        if is_there:
            self._sent = self.post
            return []
        self._sent = self.post
        return [Intent(Priority.MOVE, {"cmd": "move", "units": [canary.id], "to": list(self.post)})]


def _log(phase: str, **data: object) -> None:
    """Write one `scout_slot` event of kind `canary`."""
    records.log(SCOUT_SLOT_EVENT, {"kind": CANARY_KIND, "phase": phase, **data})
