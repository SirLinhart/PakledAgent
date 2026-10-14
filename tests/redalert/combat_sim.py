"""A small fight model, for testing whom the agent's units shoot at.

The fake game plays no combat, so a test of target choice needs something
that does. `CombatSim` is a field of two sides' actors that move and fire
tick by tick, at the game's 25 ticks a second, in the simplest way that still
lets the choice of targets decide a fight:

- An actor has the hit points and cost of `items.toml`, its speed in cells a
  second (0 for a building), and the armour, weapon range, minimum range and
  dps by armour class of `damage.toml`.
- Damage is continuous: an actor whose target is in range and outside its
  minimum range does its dps against the target's armour, divided by 25, to
  it in every tick. There is no reload, splash or miss. A defense fires like
  a unit; a building that is no defense does not fire. An actor at 0 hit
  points is gone.
- The orders are those of the platform: `attack` a target, `move` to a cell
  and `attack_move` to a cell. Movement is in a straight line at the actor's
  speed, with no pathing and no collision. An attacker closes in on its
  target until the target is in range; an actor on an `attack_move` goes on
  until an enemy is in range and fires at it; an actor on a `move` goes and
  does not fire.
- An idle actor, and every actor of the enemy side whatever its order, fires
  at the nearest enemy in range that it can hurt.
- An enemy actor may be told to guard instead: it stands, firing at what
  comes into its range, until one of ours comes within its guard radius, and
  then closes in on the nearest of ours within it, as an `attack` does. So a
  base's defending army waits by its defenses and comes out to meet an
  attacker.
- Our side is driven by a callback, every 5 ticks: it is handed the fight as
  the agent's `World` sees it and gives back the orders for our units. The
  world is told of the enemy actors destroyed since it last saw the fight, as
  the platform reports kills, and can be told where our base and the enemy's
  start are, as a state of a match tells the agent. The fight counts the
  orders each of our units was given, by command.
- Nothing is random: a fight played twice is the same fight.

`baseline_assign` is the hand-out of targets that the effectiveness-based
assignment replaced, kept here only for a test to compare the two. Nothing
under `src/` uses it.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.fight import Point, worth
from ucla_agent.games.redalert.items import TICKS_PER_SECOND, ItemTable
from ucla_agent.games.redalert.targeting import Assignment, attack_class, targets
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.world import ENEMY_KILLED_EVENT as KILL_EVENT
from ucla_agent.games.redalert.world import EnemyRecord, Unit, World

OURS = "ours"
THEIRS = "theirs"
# How often, in ticks, the callback gives our side its orders: as often as the agent decides.
ORDERS_EVERY_TICKS = 5
FACTION = "england"
# The order of an enemy actor that waits for one of ours to come within its radius: ("guard", radius in cells).
GUARD = "guard"
_PERCENT = 100
_SIDES = (OURS, THEIRS)

# The orders of our side: the platform's own, as the agent sends them.
Drive = Callable[[World], list[Mapping[str, Any]]]


@dataclass(eq=False)
class Actor:
    """One unit or building in the fight."""

    id: int
    side: str
    type: str
    x: float
    y: float
    hp: float
    max_hp: float
    cost: int
    is_building: bool
    # Cells a tick.
    step: float
    armour: str
    range: float
    min_range: float
    dps: Mapping[str, float]
    # (cmd, the target's id or the cell), or None while idle.
    order: tuple[str, Any] | None = None
    # The actor it fires at in this tick, if any.
    firing_at: Actor | None = field(default=None, repr=False)

    @property
    def is_alive(self) -> bool:
        return self.hp > 0

    @property
    def value(self) -> float:
        """What is left of it: its cost times its share of hit points."""
        return self.cost * self.hp / self.max_hp

    def distance_to(self, x: float, y: float) -> float:
        return math.hypot(x - self.x, y - self.y)

    def can_hit(self, other: Actor) -> bool:
        """Whether it does damage to another actor from where both stand: in range, outside its minimum range."""
        distance = self.distance_to(other.x, other.y)
        return self.min_range <= distance <= self.range and self.dps.get(other.armour, 0.0) > 0


def actor(items: ItemTable, actor_id: int, side: str, actor_type: str, cell: tuple[float, float]) -> Actor:
    """An actor of a type at full health, as the item and damage tables give it."""
    facts, damage = items[actor_type], items.damage.get(actor_type)
    return Actor(
        id=actor_id,
        side=side,
        type=actor_type,
        x=cell[0],
        y=cell[1],
        hp=float(facts.hp),
        max_hp=float(facts.hp),
        cost=facts.cost,
        is_building=facts.is_building,
        step=(facts.speed or 0.0) / TICKS_PER_SECOND,
        armour=damage.armour,
        range=damage.range,
        min_range=damage.min_range,
        dps=dict(damage.dps),
    )


@dataclass(frozen=True)
class Outcome:
    """How a fight ended."""

    ticks: int
    # The side left standing, or None when both still stand at the end of the time.
    winner: str | None
    # What each side was worth at the start, and what is left of it: the cost of its living actors times their
    # share of hit points.
    starting_value: Mapping[str, float]
    surviving_value: Mapping[str, float]

    @property
    def net_value(self) -> float:
        """What our side came out with: the enemy's value destroyed minus our own value lost."""
        lost = {side: self.starting_value[side] - self.surviving_value[side] for side in _SIDES}
        return lost[THEIRS] - lost[OURS]


class CombatSim:
    """A fight between our side, driven by a callback, and the enemy side. See the module's note."""

    def __init__(
        self,
        items: ItemTable,
        actors: list[Actor],
        drive: Drive,
        bounds: tuple[int, int, int, int],
        base_center: tuple[int, int] | None = None,
        enemy_base: tuple[int, int] | None = None,
    ) -> None:
        """A fight about to start: `bounds` (x, y, width, height) is the playable map the world model is told of.

        `base_center` and `enemy_base`, when given, are where the world model is told our base and the enemy's start
        are.
        """
        self._actors = {one.id: one for one in actors}
        self._drive = drive
        self._bounds = bounds
        self._base_center = base_center
        self._enemy_base = enemy_base
        self._world = World(items)
        self.tick = 0
        self._starting_value = self._value()
        # Our actor id -> how many orders of each command it was given.
        self.orders_received: dict[int, Counter[str]] = {}
        # How many enemy actors were gone when our side last saw the fight, and the number of the last event shown.
        self._enemies_gone = 0
        self._event_seq = 0

    def living(self, side: str) -> list[Actor]:
        """The actors of a side still standing, by id."""
        return [one for one in self._actors.values() if one.side == side and one.is_alive]

    def run(self, max_ticks: int) -> Outcome:
        """Play the fight until one side is gone or `max_ticks` have passed."""
        while self.tick < max_ticks and all(self.living(side) for side in _SIDES):
            if self.tick % ORDERS_EVERY_TICKS == 0:
                self._take_orders()
            self._step()
            self.tick += 1
        standing = [side for side in _SIDES if self.living(side)]
        winner = standing[0] if len(standing) == 1 else None
        return Outcome(self.tick, winner, self._starting_value, self._value())

    def _value(self) -> dict[str, float]:
        """What each side's living actors are worth together."""
        return {side: sum(one.value for one in self.living(side)) for side in _SIDES}

    def _take_orders(self) -> None:
        """Show our side the fight as the agent's world model sees it, and give our units the orders it returns."""
        self._world.update(self._state())
        for order in self._drive(self._world):
            for unit in self._ours_named(order["units"]):
                unit.order = _sim_order(order)
                self.orders_received.setdefault(unit.id, Counter())[order["cmd"]] += 1

    def _ours_named(self, actor_ids: list[int]) -> list[Actor]:
        """The actors of our side still standing among those an order names; an order names no other."""
        named = (self._actors.get(actor_id) for actor_id in actor_ids)
        return [one for one in named if one is not None and one.side == OURS and one.is_alive]

    def _state(self) -> dict[str, Any]:
        """The fight as a state the platform would send our side: every enemy in sight, and the kills since the last.

        The kills are one `enemy_killed` event with their count, as the
        platform reports them, so that the world forgets a building destroyed.
        """
        theirs = self.living(THEIRS)
        gone = sum(1 for one in self._actors.values() if one.side == THEIRS and not one.is_alive)
        events = []
        if gone > self._enemies_gone:
            self._event_seq += 1
            events.append(
                {"seq": self._event_seq, "tick": self.tick, "kind": KILL_EVENT, "count": gone - self._enemies_gone}
            )
        self._enemies_gone = gone
        observation = {
            "tick": self.tick,
            "faction": FACTION,
            "units": [_own(one) for one in self.living(OURS) if not one.is_building],
            "buildings": [],
            "visible_enemies": [_shown(one) for one in theirs if not one.is_building],
            "visible_enemy_buildings": [_shown(one) for one in theirs if one.is_building],
            "events": events,
        }
        state: dict[str, Any] = {
            "phase": "playing",
            "state_version": self.tick,
            "map": {"bounds": list(self._bounds)},
            "observation": observation,
        }
        if self._base_center is not None:
            observation["base_center"] = list(self._base_center)
        if self._enemy_base is not None:
            state["legal_actions"] = {"enemy_base": list(self._enemy_base)}
        return state

    def _step(self) -> None:
        """One tick: every actor picks what it fires at and moves, then all the damage is done at once."""
        actors = sorted((one for one in self._actors.values() if one.is_alive), key=lambda one: one.id)
        for one in actors:
            one.firing_at = self._act(one)
        for one in actors:
            target = one.firing_at
            if target is not None and one.can_hit(target):
                target.hp -= one.dps[target.armour] / TICKS_PER_SECOND

    def _act(self, one: Actor) -> Actor | None:
        """Move an actor as its order says and give the actor it fires at in this tick, if any."""
        if one.side == THEIRS:
            return self._enemy_act(one)
        if one.order is None:
            return self._nearest_in_range(one)
        cmd, aim = one.order
        if cmd == "attack":
            return self._attack(one, self._actors.get(aim))
        if cmd == "move":
            self._move_to(one, aim)
            return None
        return self._attack_move(one, aim)

    def _attack_move(self, one: Actor, cell: tuple[float, float]) -> Actor | None:
        """Fire at the nearest enemy in range, or go on to the cell while none is."""
        nearest = self._nearest_in_range(one)
        if nearest is None:
            self._move_to(one, cell)
        return nearest

    def _enemy_act(self, one: Actor) -> Actor | None:
        """The enemy side fires at the nearest of ours in range, and goes on to its order's cell while none is.

        A guard closes in on the nearest of ours within its radius instead, and stands while none is.
        """
        if one.order is None:
            return self._nearest_in_range(one)
        cmd, aim = one.order
        if cmd == GUARD:
            near = [enemy for enemy in self.living(OURS) if one.distance_to(enemy.x, enemy.y) <= aim]
            nearest = min(near, key=lambda enemy: (one.distance_to(enemy.x, enemy.y), enemy.id), default=None)
            return self._nearest_in_range(one) if nearest is None else self._attack(one, nearest)
        return self._attack_move(one, aim)

    def _attack(self, one: Actor, target: Actor | None) -> Actor | None:
        """Close in on an attack's target until it is in range; an attack whose target is gone leaves the actor idle."""
        if target is None or not target.is_alive:
            one.order = None
            return self._nearest_in_range(one)
        if one.distance_to(target.x, target.y) > one.range:
            self._move_to(one, (target.x, target.y), stop_short=one.range)
        return target

    def _nearest_in_range(self, one: Actor) -> Actor | None:
        """The nearest enemy an actor can hurt from where it stands; the lowest id of equally near ones."""
        other = THEIRS if one.side == OURS else OURS
        hittable = [enemy for enemy in self.living(other) if one.can_hit(enemy)]
        return min(hittable, key=lambda enemy: (one.distance_to(enemy.x, enemy.y), enemy.id), default=None)

    def _move_to(self, one: Actor, cell: tuple[float, float], stop_short: float = 0.0) -> None:
        """One tick's straight step toward a cell, ending `stop_short` cells before it; an order to move ends there."""
        distance = one.distance_to(cell[0], cell[1])
        travel = min(one.step, max(distance - stop_short, 0.0))
        if distance > 0:
            one.x += (cell[0] - one.x) * travel / distance
            one.y += (cell[1] - one.y) * travel / distance
        if one.order is not None and one.order[0] != "attack" and distance <= one.step:
            one.order = None


def _sim_order(order: Mapping[str, Any]) -> tuple[str, Any]:
    """An order the platform takes, as an actor holds it. Raises for any other command than the three."""
    cmd = order["cmd"]
    if cmd == "attack":
        return cmd, order["target"]
    if cmd in ("move", "attack_move"):
        return cmd, (float(order["to"][0]), float(order["to"][1]))
    raise ValueError(f"the fight model takes attack, move and attack_move orders, not {cmd}")


def _cell(one: Actor) -> tuple[int, int]:
    return math.floor(one.x + 0.5), math.floor(one.y + 0.5)


def _hp_percent(one: Actor) -> int:
    """The health a state shows: a whole percent, never 0 for an actor still standing."""
    return math.ceil(one.hp / one.max_hp * _PERCENT)


def _own(one: Actor) -> dict[str, Any]:
    x, y = _cell(one)
    idle = one.order is None
    return {"id": one.id, "type": one.type, "x": x, "y": y, "hp": _hp_percent(one), "idle": idle, "act": None}


def _shown(one: Actor) -> dict[str, Any]:
    x, y = _cell(one)
    return {"id": one.id, "type": one.type, "x": x, "y": y, "hp": _hp_percent(one)}


def baseline_assign(attackers: list[Unit], ranked: list[EnemyRecord], world: World, tuning: Tuning) -> list[Assignment]:
    """The replaced hand-out: to each enemy in sight in the order of `ranked`, the nearest free attackers.

    An enemy gets attackers until what they cost together reaches
    `overkill_factor` times what it is still worth, its cost weighted by its
    share of health; the next ones go to the next enemy.
    """
    free = list(attackers)
    assignments = []
    for target in (record for record in ranked if record.visible):
        enough = tuning.combat.overkill_factor * worth(target.type, target.hp, world)
        given: list[Unit] = []
        cost = 0.0
        free.sort(key=lambda unit: math.hypot(unit.x - target.x, unit.y - target.y), reverse=True)
        while free and cost < enough:
            given.append(free.pop())
            cost += worth(given[-1].type, _PERCENT, world)
        if given:
            assignments.append(Assignment(target, tuple(unit.id for unit in given)))
    return assignments


def baseline_orders(units: list[Unit], middle: Point, world: World, tuning: Tuning) -> list[Intent]:
    """The orders of an engaged group under the replaced hand-out: as `targeting.FocusFire.orders` gives them.

    One attack for each target given out, in its class by contact, and an
    `attack_move` on the first target for the units left over. It remembers
    nothing from one cycle to the next. Its candidates are those of the
    replaced rule, the enemies within contact_cells of the group's centroid,
    whatever contact_focus says: the per-unit reach may leave none while an
    enemy stands within contact_cells of it.
    """
    ranked = targets(units, middle, world, tuning, around_middle=True)
    assignments = baseline_assign(units, ranked, world, tuning)
    by_id = {unit.id: unit for unit in units}
    sent = [
        Intent(
            attack_class([by_id[unit_id] for unit_id in assignment.attackers], ranked, world),
            {"cmd": "attack", "units": list(assignment.attackers), "target": assignment.target.id},
        )
        for assignment in assignments
    ]
    given = {unit_id for assignment in assignments for unit_id in assignment.attackers}
    rest = [unit.id for unit in units if unit.id not in given]
    if rest:
        best = assignments[0].target if assignments else ranked[0]
        sent.append(Intent(Priority.MOVE, {"cmd": "attack_move", "units": rest, "to": [best.x, best.y]}))
    return sent
