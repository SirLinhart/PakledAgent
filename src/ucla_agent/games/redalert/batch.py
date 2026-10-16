"""From what the agent wants to do to orders the platform will take.

Each part of the agent proposes `Intent`s: one platform order each, with the
priority class it was proposed under. Before an order is sent it is checked
against the world as it is in this very cycle, because an intent may rest on
an older state: a unit may have died since, an enemy may have left sight, a
building may no longer be on offer. `validated` makes that check. It drops
the unit ids that can no longer take an order and gives the order back as it
may be sent, or nothing when nothing of it can be.

The checks are the ones the platform itself makes (the fields of each
command, own and living units, visible targets, items on offer, cells in the
playable part of the map), so that an order that passes here is not one the
platform refuses for a reason the agent could have seen.

`pack` then fits the orders that passed into one batch. The platform takes at
most 20 orders, 50 units in an order and 200 engine commands in a batch, and
refuses a batch that is over any of them whole, so the most urgent orders go
in first and what does not fit is handed back. Only one kind of order is ever
cut: a unit order for more units than one order may name is sent as several
orders. A unit also gets at most one order in a batch that tells it what to
do next, the most urgent one, because the engine would carry out only the
last of them. With the model commander on, one slot is kept for its order,
which goes in ahead of every other (see `pack`).

The fighting is ordered in three classes, after production: CONTACT for the
units in a fight, SCREEN for the screen in front of the long-range units, and
MOVE for every other movement of the army. In a large battle there are more
of these than a batch holds, and an order for units under fire matters most.
Within CONTACT the long-range units' moves into and back out of their firing
band go first, as they are few and a long-range unit caught inside its
minimum range does no damage at all; then the attacks, on the targets that
are worth the most first, by their cost and the share of their health they
have left. Combat orders that do not fit are dropped, not kept: the next
state is fresh, and the managers propose again what still applies to it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Any, Callable, Iterable, Iterator, Mapping

from ucla_agent.games.redalert.world import World

Order = dict[str, Any]

STANCES = frozenset({"hold_fire", "return_fire", "defend", "attack_anything"})
# cmd -> (required fields, optional fields), as the platform reads an order; any other field makes it invalid.
ORDER_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "move": (frozenset({"units", "to"}), frozenset({"queued"})),
    "attack_move": (frozenset({"units", "to"}), frozenset({"queued"})),
    "attack": (frozenset({"units"}), frozenset({"target", "to", "queued"})),
    "stop": (frozenset({"units"}), frozenset()),
    "deploy": (frozenset({"units"}), frozenset()),
    "unload": (frozenset({"units"}), frozenset()),
    "guard": (frozenset({"units", "target"}), frozenset({"queued"})),
    "enter_transport": (frozenset({"units", "target"}), frozenset({"queued"})),
    "harvest": (frozenset({"units"}), frozenset({"to", "queued"})),
    "set_stance": (frozenset({"units", "stance"}), frozenset()),
    "build": (frozenset({"item"}), frozenset()),
    "train": (frozenset({"item"}), frozenset({"count"})),
    "place": (frozenset({"item"}), frozenset({"at"})),
    "cancel": (frozenset({"item"}), frozenset()),
    "sell": (frozenset({"building"}), frozenset()),
    # The platform lets `on` be left out of these two; the agent always says which way it means.
    "repair": (frozenset({"building", "on"}), frozenset()),
    "power_toggle": (frozenset({"building", "on"}), frozenset()),
    "set_primary": (frozenset({"building"}), frozenset()),
    "rally": (frozenset({"building", "to"}), frozenset()),
}
# Unit orders that may also name the agent's own buildings, such as defenses.
BUILDING_TAKES = frozenset({"attack", "stop", "set_stance", "guard"})
# Unit orders that give a unit something to do in place of what it was doing. Changing a stance does not.
ACTIVITY_COMMANDS = frozenset(
    {"move", "attack_move", "attack", "stop", "deploy", "unload", "guard", "enter_transport", "harvest"}
)
DEFAULT_TRAIN_COUNT = 1


class Priority(IntEnum):
    """The class an intent is proposed under. A lower value is taken into a batch first."""

    # Retreats, harvesters fleeing, attacks in defense of the base.
    EMERGENCY = 0
    # Placing finished buildings.
    PLACEMENT = 1
    # Power plants and switching buildings on and off.
    POWER = 2
    # Build, train, cancel, rally, repair, sell. At most one order a queue in a cycle, so they leave most of a batch.
    PRODUCTION = 3
    # Attacks by units with an enemy within their weapon range and 2 cells; long-range units' firing-band moves and
    # fall-backs.
    CONTACT = 4
    # Screen line positioning moves.
    SCREEN = 5
    # Reinforcement, advance, regroup and recall moves, and the attack-moves and attacks of units not in contact.
    MOVE = 6
    SCOUT = 7


# The classes whose intents that do not fit into a batch are dropped: their managers propose them again from the
# next state while they still apply.
DROPPED_CLASSES = frozenset({Priority.CONTACT, Priority.SCREEN, Priority.MOVE})
# Within CONTACT: the long-range units' moves (any order but an attack on a named target), then the attacks.
_LONG_RANGE_MOVES, _ATTACKS = 0, 1
_PERCENT = 100


@dataclass(frozen=True)
class Intent:
    """One order the agent wants to send, and how much it matters."""

    priority: Priority
    # One platform order, `{"cmd": ..., ...}`.
    order: Mapping[str, Any]
    # Set by the order ledger on an order it proposes again because the platform lost it the first time.
    resend: bool = False
    # The model commander's order (see `commander`), which goes in the batch slot kept for it.
    commander: bool = False
    # An attack the scout slots make for a scout of their own on purpose (see `scout_attacks`), which the attack strip
    # of the careful scouts keeps: every other attack on a scout is stripped.
    scout_rule: bool = False


@dataclass(frozen=True)
class Limits:
    """What the platform accepts in one batch."""

    max_orders_per_batch: int = 20
    max_units_per_order: int = 50
    # An order counts one engine command per unit, `count` commands for a `train`, and one otherwise.
    max_engine_commands: int = 200
    max_train_count: int = 10

    @classmethod
    def from_state(cls, raw_state: Mapping[str, Any]) -> Limits:
        """The limits a state's `limits` object gives, none of them above the usual one.

        A limit the state leaves out, or gives no usable value for, stays
        usual. The usual values are the most the platform documents, and a
        batch over any of them is refused whole, so a state can tighten a
        limit but is never taken at its word for a looser one.
        """
        given = raw_state.get("limits")
        given = given if isinstance(given, Mapping) else {}
        usual = cls()
        return cls(
            **{
                name: min(_positive(given.get(name), getattr(usual, name)), getattr(usual, name))
                for name in cls.__dataclass_fields__
            }
        )


@dataclass(frozen=True)
class Packed:
    """One batch, and what did not go into it."""

    # What goes into the batch, in batch order; each holds one order that is ready to send.
    intents: tuple[Intent, ...]
    # What could be sent but did not fit: dropped from this batch. The ledger carries those of the classes outside
    # `DROPPED_CLASSES` into the next cycle.
    deferred: tuple[Intent, ...]
    # How many intents were discarded because nothing of them could be sent.
    dropped_invalid: int

    @property
    def orders(self) -> list[Mapping[str, Any]]:
        """The orders of the batch, as they go into the action sent to the platform."""
        return [intent.order for intent in self.intents]


def command_count(order: Mapping[str, Any]) -> int:
    """How many engine commands the platform counts an order as: one per unit, `count` for a train, else one."""
    if "units" in order:
        return len(order["units"])
    return order.get("count", DEFAULT_TRAIN_COUNT) if order["cmd"] == "train" else 1


def pack(intents: Iterable[Intent], world: World, limits: Limits) -> Packed:
    """One batch from the given intents, the most urgent first, within the platform's limits.

    Each intent is checked against `world` as it is now. Within a priority
    class the intents keep the order they were given in, but for CONTACT:
    see `packing_rank`. The intents are tried in that order, and one that fits
    the limits left when its turn comes is packed: an order that would take
    the batch past a limit is deferred whole, and a later, smaller one may
    still go in.

    The model commander's order comes before all of them, so that one slot of
    the batch is always its own and the other orders share the rest; with no
    commander order every slot is theirs. A batch holds one commander order at
    most, and it names at most the units one order may: the commander lists
    the units nearest its target first, and their manager orders the rest of
    them the same way in the other slots. What it leaves out is dropped, not
    deferred.
    """
    taken: list[Intent] = []
    deferred: list[Intent] = []
    dropped_invalid = 0
    busy_units: set[int] = set()
    commands = 0
    for intent in _in_packing_order(intents, world, limits):
        order = validated(intent, world, limits)
        if order is None:
            dropped_invalid += 1
            continue
        for part in _parts(_for_free_units(order, busy_units), limits.max_units_per_order):
            needed = command_count(part)
            if len(taken) < limits.max_orders_per_batch and commands + needed <= limits.max_engine_commands:
                taken.append(replace(intent, order=part))
                commands += needed
            else:
                deferred.append(replace(intent, order=part))
    return Packed(tuple(taken), tuple(deferred), dropped_invalid)


def _in_packing_order(intents: Iterable[Intent], world: World, limits: Limits) -> list[Intent]:
    """The intents in the order they are packed in: the first commander intent, cut to the units one order may name,
    then every other intent but the commander's by `packing_rank`."""
    ranked = sorted(intents, key=lambda proposed: packing_rank(proposed, world))
    commander = next((intent for intent in ranked if intent.commander), None)
    others = [intent for intent in ranked if not intent.commander]
    return others if commander is None else [_within_one_order(commander, limits), *others]


def _within_one_order(intent: Intent, limits: Limits) -> Intent:
    """An intent whose order names at most the units one order may, the first of them."""
    units = intent.order.get("units")
    if units is None or len(units) <= limits.max_units_per_order:
        return intent
    return replace(intent, order={**intent.order, "units": list(units[: limits.max_units_per_order])})


def packing_rank(intent: Intent, world: World) -> tuple[int, int, float]:
    """The key intents are packed in the order of, the lowest first: the class, then the place within CONTACT.

    Within CONTACT a long-range unit's move into or back out of its firing band
    (any order but an attack on a named target) goes first, then the attacks
    by the remaining value of their target, its cost times its share of
    health, the highest first. In any other class every intent has the same
    place, so the class keeps the order its intents were proposed in.
    """
    order = intent.order
    is_attack = order.get("cmd") == "attack" and "target" in order
    if intent.priority != Priority.CONTACT or not is_attack:
        return intent.priority, _LONG_RANGE_MOVES, 0.0
    return intent.priority, _ATTACKS, -_remaining_value(order["target"], world)


def _remaining_value(enemy_id: Any, world: World) -> float:
    """What a remembered enemy is still worth: its cost times its share of health; 0 when it is not known."""
    record = world.enemy_memory.get(enemy_id)
    facts = None if record is None else world.items.get(record.type)
    return 0.0 if facts is None else facts.cost * record.hp / _PERCENT


def _for_free_units(order: Order, busy_units: set[int]) -> Order | None:
    """An order narrowed to the units no more urgent order has already told what to do; None when none is left.

    Only orders that replace what a unit is doing compete for it, and a
    queued order does not replace anything. The units of the order returned
    are marked busy, whether or not the order then fits into the batch: a
    less urgent order must not send them elsewhere in the meantime.
    """
    if order["cmd"] not in ACTIVITY_COMMANDS or order.get("queued"):
        return order
    free = [unit_id for unit_id in order["units"] if unit_id not in busy_units]
    busy_units.update(free)
    return {**order, "units": free} if free else None


def _parts(order: Order | None, max_units: int) -> Iterator[Order]:
    """The orders one order is sent as: itself, or consecutive orders of at most `max_units` units each."""
    if order is None:
        return
    units = order.get("units", ())
    if len(units) <= max_units:
        yield order
        return
    for start in range(0, len(units), max_units):
        yield {**order, "units": units[start : start + max_units]}


def _is_whole_number(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive(value: Any, otherwise: int) -> int:
    return value if _is_whole_number(value) and value > 0 else otherwise


def validated(intent: Intent, world: World, limits: Limits) -> Order | None:
    """The order of an intent as it may be sent in this cycle, or None when nothing of it can be.

    The order comes back as a new object: a unit order holds only the ids
    that are the agent's own and alive (wrecks are neither), without
    repeats, and an item name is in lower case.
    """
    order = dict(intent.order)
    fields = ORDER_FIELDS.get(order.get("cmd"))
    if fields is None or not fields[0] <= order.keys() - {"cmd"} <= fields[0] | fields[1]:
        return None
    if "item" in order:
        if not isinstance(order["item"], str):
            return None
        order["item"] = order["item"].lower()
    if "units" in order:
        order["units"] = _own_living(order, world)
    return order if _CHECKS[order["cmd"]](order, world, limits) else None


def _own_living(order: Order, world: World) -> list[int]:
    """The ids of a unit order that can take it now: own living units, and own buildings for the orders they take."""
    takes_buildings = order["cmd"] in BUILDING_TAKES
    return [
        actor_id
        for actor_id in dict.fromkeys(order["units"])
        if actor_id in world.units or (takes_buildings and actor_id in world.buildings)
    ]


def _is_cell_in_bounds(world: World, cell: Any) -> bool:
    return isinstance(cell, (list, tuple)) and len(cell) == 2 and world.in_bounds(cell[0], cell[1])


def _is_own_actor(world: World, actor_id: Any) -> bool:
    return actor_id in world.units or actor_id in world.buildings


def _is_enemy_in_sight(world: World, enemy_id: Any) -> bool:
    record = world.enemy_memory.get(enemy_id)
    return record is not None and record.visible


def _can_go(order: Order, world: World, _limits: Limits) -> bool:
    """A unit order needs someone to take it, and a cell, when it names one, in the playable part of the map."""
    return bool(order["units"]) and ("to" not in order or _is_cell_in_bounds(world, order["to"]))


def _can_attack(order: Order, world: World, limits: Limits) -> bool:
    """An attack names either an enemy that is in sight now or a cell to fire at, not both."""
    if ("target" in order) == ("to" in order):
        return False
    return _can_go(order, world, limits) and ("target" not in order or _is_enemy_in_sight(world, order["target"]))


def _can_accompany(order: Order, world: World, limits: Limits) -> bool:
    """Guarding and boarding need one of the agent's own actors as their target."""
    return _can_go(order, world, limits) and _is_own_actor(world, order["target"])


def _can_set_stance(order: Order, world: World, limits: Limits) -> bool:
    return _can_go(order, world, limits) and order["stance"] in STANCES


def _can_produce(order: Order, world: World, limits: Limits) -> bool:
    count = order.get("count", DEFAULT_TRAIN_COUNT)
    countable = _is_whole_number(count) and 1 <= count <= limits.max_train_count
    return countable and order["item"] in world.available_production


def _can_place(order: Order, world: World, _limits: Limits) -> bool:
    return order["item"] in world.ready_to_place and ("at" not in order or _is_cell_in_bounds(world, order["at"]))


def _can_cancel(order: Order, world: World, _limits: Limits) -> bool:
    return any(queued.item == order["item"] for queue in world.production.values() for queued in queue)


def _is_own_building(order: Order, world: World, _limits: Limits) -> bool:
    return order["building"] in world.buildings


def _can_switch(order: Order, world: World, limits: Limits) -> bool:
    """Repair and power orders say which way they switch, so that sending one twice never undoes it."""
    return _is_own_building(order, world, limits) and isinstance(order["on"], bool)


def _can_rally(order: Order, world: World, limits: Limits) -> bool:
    return _is_own_building(order, world, limits) and _is_cell_in_bounds(world, order["to"])


# cmd -> whether an order with the right fields, its unit ids already narrowed down, can be sent as the world is now.
_CHECKS: dict[str, Callable[[Order, World, Limits], bool]] = {
    "move": _can_go,
    "attack_move": _can_go,
    "attack": _can_attack,
    "stop": _can_go,
    "deploy": _can_go,
    "unload": _can_go,
    "guard": _can_accompany,
    "enter_transport": _can_accompany,
    "harvest": _can_go,
    "set_stance": _can_set_stance,
    "build": _can_produce,
    "train": _can_produce,
    "place": _can_place,
    "cancel": _can_cancel,
    "sell": _is_own_building,
    "repair": _can_switch,
    "power_toggle": _can_switch,
    "set_primary": _is_own_building,
    "rally": _can_rally,
}
