"""The early retreat of the scout slots' scouts (`[scouting] scout_cautious`): back at the first hit or sign of a fight.

A scout that flees only once danger is near, or turns away only after a hard
hit, keeps driving into what it cannot see from afar: an army beyond our
units' sight, a defense not yet remembered, an enemy base. It is lost before
it turns. So with the switch on, a scout of the scout slots (see
`scout_slots`) retreats at once for either of two causes:

- `hit`: its health dropped by scout_retreat_hp_drop or more since the last
  state. Whatever hit it may be out of sight, so it does not wait to see it;
- `outclassed`: the enemy units that fight in sight within
  scout_outclass_cells of it are worth more than it is, before any of them
  has hit it. A unit is worth its cost by the share of its health it has
  left, as in the estimate of a fight (see `fight`); a scout holds fire (see
  `scout_stance`), so it is a unit that cannot hurt what it meets, and is
  worth matchup_factor of that, as such a group is in a fight. A jeep, worth
  250 so, retreats from three rifles, worth 300, and not from two.

A retreating scout is sent directly away from the nearest enemy in sight (an
enemy unit that fights or a defense), or with none in sight back along its
way: toward where it stood scout_retreat_seconds ago, or the staging point
when it has not moved since. It is sent as far as it goes in
scout_retreat_seconds at its speed, inside the map, and the retreat lasts
that long. A cause that holds again while it retreats keeps it retreating
for scout_retreat_seconds from then; the scout is sent anew away from the
nearest enemy in sight when that is another enemy or it has reached the cell
it was sent to. Once over, the scout takes up its mission again.

The retreat's move is a flight: it is the most urgent of the scouts' moves,
and counts within scout_orders_per_batch. Each retreat begun is one
`scout_slot` event of kind `retreat` (see `scout_slots`).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Mapping

from ucla_agent.games.redalert import cells, fight
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.scout_steering import FLEE_URGENCY, Move
from ucla_agent.games.redalert.world import DEFENSE_TYPES, Cell, EnemyRecord, Unit, World

# The causes of a retreat.
HIT = "hit"
OUTCLASSED = "outclassed"
# The speed a scout is taken to have when the item table gives none, in cells a second: no faster than a rifleman.
UNKNOWN_SPEED_CELLS_PER_SECOND = 1.0


@dataclass
class Retreat:
    """One scout's retreat: why, where it is sent, until when, and the enemy it is sent away from."""

    cause: str
    to: Cell
    until_tick: int
    # The id of the enemy in sight it is sent away from; None when it goes back along its way.
    enemy_id: int | None
    # The enemy's cell when the scout was sent away from it.
    enemy_cell: Cell | None


@dataclass(frozen=True)
class Begun:
    """A retreat begun in a cycle, by the slot whose scout retreats."""

    slot: int
    retreat: Retreat


class Retreats:
    """The retreats of one match's slot scouts. See the module's note."""

    def __init__(self) -> None:
        # Unit id -> its retreat, while one lasts.
        self._active: dict[int, Retreat] = {}
        # Unit id -> its health in the last state.
        self._hp: dict[int, int] = {}
        # Unit id -> (tick, cell) of the states of the last scout_retreat_seconds, the oldest first.
        self._trail: dict[int, deque[tuple[int, Cell]]] = {}

    def update(
        self, scouts: Mapping[int, Unit], world: World, ctx: MatchContext
    ) -> tuple[dict[int, Move], list[Begun]]:
        """Bring the retreats of the slots' scouts in the field (`scouts`, by slot number) up to date: the move of each
        scout that retreats in this cycle, by its slot, and the retreats begun in it."""
        tuning, tick = ctx.tuning.scouting, world.tick
        lasting = tuning.scout_retreat_seconds * TICKS_PER_SECOND
        live = {scout.id for scout in scouts.values()}
        self._active = {unit_id: retreat for unit_id, retreat in self._active.items() if unit_id in live}
        self._trail = {unit_id: trail for unit_id, trail in self._trail.items() if unit_id in live}
        moves: dict[int, Move] = {}
        begun: list[Begun] = []
        for number, scout in scouts.items():
            retreat = self._retreat(scout, world, ctx)
            if retreat is not None and retreat is not self._active.get(scout.id):
                begun.append(Begun(number, retreat))
            self._note(scout, tick, lasting)
            if retreat is None:
                self._active.pop(scout.id, None)
                continue
            self._active[scout.id] = retreat
            moves[number] = Move(retreat.to, FLEE_URGENCY)
        return moves, begun

    def _retreat(self, scout: Unit, world: World, ctx: MatchContext) -> Retreat | None:
        """The scout's retreat in this cycle: begun or kept on for a cause that holds, else kept until it is over; None
        while it does not retreat."""
        tuning, tick, position = ctx.tuning.scouting, world.tick, (scout.x, scout.y)
        until = tick + tuning.scout_retreat_seconds * TICKS_PER_SECOND
        retreat, cause = self._active.get(scout.id), self._cause(scout, world, ctx)
        if cause is None:
            return None if retreat is None or tick >= retreat.until_tick else retreat
        enemy = _nearest_threat(position, world)
        if retreat is None:
            enemy_id, enemy_cell = _identity(enemy)
            return Retreat(cause, self._away(scout, enemy, world, ctx), until, enemy_id, enemy_cell)
        retreat.until_tick = until
        is_there = cells.is_within(position, retreat.to, tuning.scout_reached_cells)
        if enemy is not None and (enemy.id != retreat.enemy_id or is_there):
            retreat.to = self._away(scout, enemy, world, ctx)
            retreat.enemy_id, retreat.enemy_cell = _identity(enemy)
        return retreat

    def _cause(self, scout: Unit, world: World, ctx: MatchContext) -> str | None:
        """Why the scout retreats in this cycle: HIT, OUTCLASSED, or None for neither."""
        last = self._hp.get(scout.id)
        if last is not None and last - scout.hp >= ctx.tuning.scouting.scout_retreat_hp_drop:
            return HIT
        return OUTCLASSED if is_outclassed(scout, world, ctx) else None

    def _away(self, scout: Unit, enemy: EnemyRecord | None, world: World, ctx: MatchContext) -> Cell:
        """The cell the scout retreats to: directly away from the enemy given, else back along its way, as far as it
        goes in scout_retreat_seconds, inside the map."""
        position = (scout.x, scout.y)
        reach = _speed(scout.type, world) * ctx.tuning.scouting.scout_retreat_seconds
        if enemy is not None:
            to = cells.toward(position, (enemy.x, enemy.y), -reach)
        else:
            to = cells.toward(position, self._way_back(scout, world, ctx), reach)
        if to == position:
            to = cells.toward(position, world.base_center, reach)
        return cells.clamped(to, world.map_bounds)

    def _way_back(self, scout: Unit, world: World, ctx: MatchContext) -> Cell:
        """Where the scout came from: where it stood scout_retreat_seconds ago, or the staging point when it stood
        where it stands."""
        position = (scout.x, scout.y)
        trail = self._trail.get(scout.id)
        came_from = trail[0][1] if trail else position
        if came_from != position:
            return came_from
        return cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells)

    def _note(self, scout: Unit, tick: int, lasting: int) -> None:
        """Keep the scout's health and cell of this state, its cells of the last `lasting` ticks only."""
        self._hp[scout.id] = scout.hp
        trail = self._trail.setdefault(scout.id, deque())
        trail.append((tick, (scout.x, scout.y)))
        while trail and tick - trail[0][0] > lasting:
            trail.popleft()


def is_outclassed(scout: Unit, world: World, ctx: MatchContext) -> bool:
    """Whether the enemy units that fight in sight within scout_outclass_cells of a scout are worth more than it is
    worth holding fire: its fight worth at matchup_factor."""
    position, reach = (scout.x, scout.y), ctx.tuning.scouting.scout_outclass_cells
    near = [
        record
        for record in world.enemy_memory.values()
        if record.visible and is_combat_unit(record) and cells.is_within(position, (record.x, record.y), reach)
    ]
    enemy = sum(fight.worth(record.type, record.hp, world) for record in near)
    return enemy > fight.worth(scout.type, scout.hp, world) * ctx.tuning.combat.matchup_factor


def _nearest_threat(position: Cell, world: World) -> EnemyRecord | None:
    """The enemy in sight nearest a cell that can hurt a scout: a unit that fights or a defense; None when none is."""
    threats = [
        record
        for record in world.enemy_memory.values()
        if record.visible and (is_combat_unit(record) or record.type in DEFENSE_TYPES)
    ]
    return min(threats, key=lambda record: cells.distance(position, (record.x, record.y)), default=None)


def _identity(enemy: EnemyRecord | None) -> tuple[int | None, Cell | None]:
    """The id and cell of the enemy a scout is sent away from; None for both when it goes back along its way."""
    return (None, None) if enemy is None else (enemy.id, (enemy.x, enemy.y))


def _speed(unit_type: str, world: World) -> float:
    """How fast a unit of a type moves, in cells a second; UNKNOWN_SPEED_CELLS_PER_SECOND when the item table does not
    say."""
    facts = world.items.get(unit_type)
    return facts.speed if facts is not None and facts.speed else UNKNOWN_SPEED_CELLS_PER_SECOND
