"""The rush counter: what the base does once a rush is told, without spending on it before.

A rush met by one infantry defense is lost far more often than one met by
two, and a defense bought on a guess is money the economy did not get. So
nothing is bought up front; the counter answers the rush once it is seen
(see `rush_watch`):

- **Two defenses** (`[opening] rush_counter_defenses`, 0 switches them
  off). In the cycle the rush verdict is `rush`, or base defense reports a
  threat at the base before a war factory stands (the wave arriving with no
  verdict), the opening orders its defense against infantry (the pillbox,
  or the flame tower) until rush_counter_defenses of them stand or are on
  their way: the next as soon as the Defense queue is free again, whatever
  the threat and outside base defense's one extra defense a minute. Those
  standing count, and the counter orders rush_counter_defenses at the most
  in a match. Before the barracks stands none is on offer, and none is
  ordered. Once all its orders are out, `MatchContext.rush_counter_ordered`
  says so, which ends the rush watch's warning.
- **The warning's reserve** (`[opening] rush_reserve_cash`). While the rush
  watch warns of infantry massing at the enemy's start, upkeep holds
  rush_reserve_cash on top of the production reserve from the optional
  buildings, as it holds the production reserve: refineries beyond the
  second, barracks and war factories beyond the first, the radar and the
  tech buildings. Harvesters, power plants, the service depot, defenses, the
  opening's steps and units are never held: the counter's defenses spend it.
  The hold ends with the warning.
- **Main holds home** (`[opening] rush_hold_until_seconds`). While the rush
  verdict holds and the game time is below rush_hold_until_seconds, the
  agent keeps main within HOLD_CELLS of base_center (`held_home`): main's
  units are taken out of every move, attack-move and attack that would take
  them farther, whoever proposed it (the model commander, the forward hold's
  staging, intercepts, strikes, presses and raids alike), and those standing
  farther out with no order of the cycle are moved back to the hold point.
  Base defense's answers and crush runs inside that area go out as they are.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from ucla_agent.games.redalert import cells, defense_cap
from ucla_agent.games.redalert.batch import ACTIVITY_COMMANDS, Intent, Order, Priority
from ucla_agent.games.redalert.family import BARRACKS_TYPES, RADAR, REFINERY, WAR_FACTORY
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.production import DEFENSE_QUEUE
from ucla_agent.games.redalert.rush_watch import RUSH
from ucla_agent.games.redalert.world import Cell, World

# The rule the counter's defenses are, as a `defense_capped` event names it (see `defense_cap`).
RUSH_COUNTER = "rush_counter"

# How far from base_center main is kept while it holds home.
HOLD_CELLS = 20
# The orders that send a unit somewhere: those main is taken out of when they lead away from home.
_GOING_COMMANDS = frozenset({"move", "attack_move", "attack"})
# The tech buildings the warning's reserve is held from, whatever stands.
TECH_BUILDINGS = frozenset({"atek", "stek"})
# The optional buildings the warning's reserve is held from, each once this many of it stand or are on their way.
_HELD_FROM = {
    REFINERY: 2,
    WAR_FACTORY: 1,
    RADAR: 0,
    **dict.fromkeys(BARRACKS_TYPES, 1),
    **dict.fromkeys(TECH_BUILDINGS, 0),
}


class RushDefenses:
    """The counter's defenses against infantry, kept for one match. See the module's note."""

    def __init__(self) -> None:
        # Whether the counter has been called for; once called it stays so.
        self._is_called = False
        # How many defenses the counter has ordered in this match.
        self._ordered = 0

    def intents(self, item: str, world: World, ctx: MatchContext) -> list[Intent] | None:
        """The counter's order for the defense against infantry `item` in this cycle, or none while it waits for the
        Defense queue, the item or the power; None while the counter is not in force (not called for, switched off,
        or with all its defenses ordered), when the opening's own rule decides."""
        wanted = ctx.tuning.opening.rush_counter_defenses
        self._is_called = self._is_called or (wanted > 0 and is_counter_called(ctx))
        if not self._is_called or ctx.rush_counter_ordered:
            return None
        production = ctx.production
        if production.planned(item) >= wanted or self._ordered >= wanted:
            ctx.rush_counter_ordered = True
            return None
        can_start = item in world.available_production and production.is_queue_free(DEFENSE_QUEUE)
        if not can_start or not production.can_power(item) or not defense_cap.allows(item, RUSH_COUNTER, world, ctx):
            return []
        self._ordered += 1
        return [production.build(item)]


def is_counter_called(ctx: MatchContext) -> bool:
    """Whether the rush counter is called for in this cycle: the rush verdict, or a threat at the base before a war
    factory stands."""
    return ctx.rush_verdict == RUSH or (ctx.threat_active and ctx.production.standing(WAR_FACTORY) == 0)


def warning_reserve(item: str, ctx: MatchContext) -> int:
    """The money held from a building on top of the production reserve: rush_reserve_cash while the rush watch warns
    and the building is an optional one beyond what the base keeps of it; 0 otherwise."""
    held_from = _HELD_FROM.get(item)
    if not ctx.rush_warning or held_from is None or ctx.production.planned(item) < held_from:
        return 0
    return ctx.tuning.opening.rush_reserve_cash


def is_holding_home(world: World, ctx: MatchContext) -> bool:
    """Whether main holds home in this cycle: the rush verdict, before rush_hold_until_seconds, with the base known."""
    is_early = world.game_seconds < ctx.tuning.opening.rush_hold_until_seconds
    return ctx.rush_verdict == RUSH and is_early and world.base_center is not None


def held_home(intents: Iterable[Intent], world: World, ctx: MatchContext) -> list[Intent]:
    """The cycle's intents with main kept home while it holds (`is_holding_home`): main's units taken out of every
    move, attack-move and attack whose cell or target lies farther than HOLD_CELLS from base_center, whoever proposed
    it, an order left with no unit dropped; and a move to the hold point for each of main's units standing farther out
    with no order of the cycle. As they are otherwise."""
    intents = list(intents)
    if not is_holding_home(world, ctx):
        return intents
    main = set(ctx.groups.members(MAIN))
    kept: list[Intent] = []
    ordered: set[int] = set()
    for intent in intents:
        order = intent.order
        units = order.get("units", ())
        if order.get("cmd") not in _GOING_COMMANDS or main.isdisjoint(units) or _is_home(order, world):
            kept.append(intent)
            ordered.update(units if order.get("cmd") in ACTIVITY_COMMANDS else ())
            continue
        others = [unit_id for unit_id in units if unit_id not in main]
        if others:
            kept.append(replace(intent, order={**order, "units": others}))
    return kept + _brought_home(main - ordered, world, ctx)


def _is_home(order: Order, world: World) -> bool:
    """Whether an order keeps its units home: its cell, or its target's last known cell, lies within HOLD_CELLS of
    base_center; an attack on a target not remembered is left to the order's own checks."""
    if "to" in order:
        cell = tuple(order["to"])
    else:
        record = world.enemy_memory.get(order.get("target"))
        if record is None:
            return True
        cell = (record.x, record.y)
    return cells.is_within(cell, world.base_center, HOLD_CELLS)


def _brought_home(units: set[int], world: World, ctx: MatchContext) -> list[Intent]:
    """One move to the hold point for those of `units` that stand farther than HOLD_CELLS from base_center."""
    out = sorted(
        unit_id
        for unit_id in units
        if unit_id in world.units
        and not cells.is_within((world.units[unit_id].x, world.units[unit_id].y), world.base_center, HOLD_CELLS)
    )
    if not out:
        return []
    return [Intent(Priority.MOVE, {"cmd": "move", "units": out, "to": list(_hold_point(world, ctx))})]


def _hold_point(world: World, ctx: MatchContext) -> Cell:
    """Where main gathers while it holds: the staging point when it lies within HOLD_CELLS of base_center, else
    base_center."""
    staging = cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells)
    if staging is not None and cells.is_within(staging, world.base_center, HOLD_CELLS):
        return staging
    return world.base_center
