"""An air attack on a remote base: one AA defense put up at that base, unless the aircraft is destroyed first.

Our base may stand in more than one place: a second construction yard, or a
cluster of buildings grown far from the main yard. An enemy helicopter or
plane that hits such a remote base finds nothing there to shoot back, and the
AA defenses of the count rule (see `anti_air`) wait for several aircraft and
stand by the main base. So with `remote_base_aa` of `[anti_air]` on, an air
attack on a remote base is answered at that base.

A remote base is one of our bases as main's military state clusters them
(`command_state.base_clusters`) that does not hold the main yard (the first
construction yard) and either has a yard of its own or has its center
`remote_base_cells` or more from the main yard. It is air-attacked when an
enemy combat aircraft in sight stands within AIR_ATTACK_CELLS of one of its
buildings while one of its buildings, or one of our units within
AIR_ATTACK_CELLS of them, has lost hp since the cycle before. The base is then
marked, unless an AA defense already stands within COVERED_CELLS of the
building or unit hit, or the AA defense this rule put up there still stands.

While the mark holds, one AA defense of the family (the agun, the sam) is
ordered for the base, on the Defense queue once it is free, and with no
radar standing the radar first, on the Building queue. The answer to an
attack waits for no quiet base, no reserve, no hold on the economy and no
count of aircraft: only for the money for its price, and for the power plant
first when it would leave less than the power target over. It is placed on
the first free candidate laid out around the building hit that lies within
NEAR_CELLS of one of the base's buildings. One the engine put elsewhere
leaves the base without its defense, and it is asked once more, at the next
candidate, since the place refused is kept off (see `refused`).

The mark is cleared once the AA defense stands at the base. It is cleared, and
the AA defense on its way canceled (or not started), when the aircraft that
attacked is destroyed before the defense is placed and no other enemy combat
aircraft has been seen near the base in the last QUIET_SECONDS: the attack is
over, and the money is better kept. A base keeps the AA defense of this rule;
only once that is destroyed does a new attack have another put up. The count
rule counts only the AA defenses that are not this rule's (`counted`).

Until a radar stands the AA defense cannot come, so meanwhile a squad of
SQUAD_SIZE rocket soldiers is sent to the cell hit of the first base marked:
idle ones of the guard and of main, the nearest first, taken into a group of
their own (AIR_SQUAD) so that no other manager calls them away, and never one
on a job of its own (a detachment, a strike, a crush run, a repair retreat,
a route walk) or one base defense answers a threat with. None is taken while
main holds home against a rush (see `rush_counter`). A squad short of rockets
has the rest trained once a mark, and takes them as they come. The squad
stays at the base while a mark holds, a radar standing or not, and goes
back to the groups it came from once none does.

Each mark, order, placing and cancel is an `anti_air` event with reason
`remote_base_attack`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, placement
from ucla_agent.games.redalert.anti_air import ANTI_AIR_DEFENSES, is_combat_aircraft
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.command_state import BaseCluster, base_clusters
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD, RADAR, ROCKET_INFANTRY
from ucla_agent.games.redalert.groups import GUARD, MAIN
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.placement import Box
from ucla_agent.games.redalert.production import BUILDING_QUEUE, DEFENSE_QUEUE, INFANTRY_QUEUE
from ucla_agent.games.redalert.rush_counter import is_holding_home
from ucla_agent.games.redalert.world import Building, Cell, EnemyRecord, Unit, World

ANTI_AIR_EVENT = "anti_air"
REMOTE_BASE_ATTACK = "remote_base_attack"
MARKED, ORDERED, PLACED, CANCELLED = "mark", "order", "placed", "cancel"
# An enemy combat aircraft this close to one of a base's buildings attacks it, when the base loses hp meanwhile.
AIR_ATTACK_CELLS = 8
# The AA defense stands within this many cells of one of the base's buildings.
NEAR_CELLS = 4
# An AA defense this close to the building hit covers the base already.
COVERED_CELLS = 6
# The attack is over when no other enemy combat aircraft was seen near the base in this many seconds.
QUIET_SECONDS = 30
# A defense the engine put elsewhere is asked once more: twice in all.
MOST_ASKS = 2
# How long a defense that has left its queue may take to show as a building before it counts as lost (3 seconds).
SHOW_GRACE_TICKS = 75
_ANTI_AIR_TYPES = frozenset(ANTI_AIR_DEFENSES.values())
# The group of the rocket squad sent to a marked base, and how many rocket soldiers it holds.
AIR_SQUAD = "air_squad"
SQUAD_SIZE = 5
# A squad member this close to the cell hit is at the base.
AT_BASE_CELLS = 4
# The manager whose rule this is, as the queue claims name it.
_CLAIMANT = "upkeep"

BuildFor = Callable[[str, World, MatchContext], list[Intent]]


@dataclass
class _Mark:
    """A remote base marked as air-attacked: its buildings as last seen, the cell hit, the aircraft that attacked, the
    aircraft seen near it by the tick they were last seen, and the AA defense ordered for it."""

    buildings: tuple[Building, ...]
    hit: Cell
    attacker: int
    near: dict[int, int]
    # Whether an AA defense ordered for the base is on its way, and the AA defenses that stood when it was ordered.
    ordered: bool = False
    before: frozenset[int] = frozenset()
    # How many times the AA defense was ordered, and the last tick it was seen on its way.
    asks: int = 0
    on_way_at: int = 0
    members: frozenset[int] = field(init=False)

    def __post_init__(self) -> None:
        self.members = frozenset(building.id for building in self.buildings)

    def follow(self, base: BaseCluster) -> None:
        """Take in the base's buildings as they stand now."""
        self.buildings = tuple(base.members)
        self.members = frozenset(building.id for building in self.buildings)


class RemoteBaseAntiAir:
    """Puts up one AA defense at a remote base an enemy aircraft attacks. See the module's note."""

    def __init__(self, build_within_power: BuildFor, defense_within_power: BuildFor) -> None:
        # Upkeep's orders for a building and for a defense, each with the power plant first when the power is short.
        self._build_within_power = build_within_power
        self._defense_within_power = defense_within_power
        # The hp of each own building and unit in the cycle before.
        self._hp: dict[int, int] = {}
        self._marks: list[_Mark] = []
        # The ids of the AA defenses this rule put up.
        self._own: set[int] = set()
        # The AA defense canceled in this cycle, which is not placed.
        self._withheld: frozenset[str] = frozenset()
        self._squad = _RocketSquad()

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The orders that answer an air attack on a remote base in this cycle: the radar or the AA defense, or the
        cancel of the AA defense on its way, and the rocket squad's; none with `remote_base_aa` or the answer to enemy
        air off."""
        self._withheld = frozenset()
        tuning = ctx.tuning.anti_air
        if not (tuning.enabled and tuning.remote_base_aa):
            return []
        hurt = self._hurt(world)
        bases = remote_bases(world, ctx)
        self._follow(bases, world)
        self._mark_new(bases, hurt, world)
        answers = [intent for mark in list(self._marks) for intent in self._answer(mark, world, ctx)]
        target = self._marks[0].hit if self._marks else None
        return [*answers, *self._squad.intents(target, world, ctx)]

    def withholds(self, item: str) -> bool:
        """Whether a finished building is kept from being placed in this cycle: the AA defense canceled in it."""
        return item in self._withheld

    def choice(
        self, item: str, world: World, ctx: MatchContext, promised: Sequence[Box], refused: Iterable[Box]
    ) -> placement.PlacementChoice | None:
        """Where the AA defense on its way to a marked base is placed: the first free candidate laid out around the
        building hit within NEAR_CELLS of one of the base's buildings, or no cell with none; None for any other
        building, which is placed as before."""
        mark = next((mark for mark in self._marks if mark.ordered), None)
        if mark is None or world.family is None or item != ANTI_AIR_DEFENSES[world.family]:
            return None
        terrain = placement.with_refused(ctx.terrain, refused)
        laid_out = placement.candidate_cells(item, world, terrain, ctx.tuning.placement, promised, mark.hit)
        spots = [(building.x, building.y) for building in mark.buildings]
        near = (cell for cell in laid_out if any(cells.is_within(cell, spot, NEAR_CELLS) for spot in spots))
        return placement.PlacementChoice(next(near, None))

    def counted(self, world: World) -> int:
        """The AA defenses of this rule, which the count rule leaves out: those it put up that stand, and those on
        their way."""
        standing = sum(1 for building_id in self._own if building_id in world.buildings)
        return standing + sum(1 for mark in self._marks if mark.ordered)

    def _hurt(self, world: World) -> list[tuple[Cell, bool]]:
        """The cells of the own buildings and units that lost hp since the cycle before, each with whether it is a
        building; the hp of this cycle kept for the next."""
        actors = [*world.buildings.values(), *world.units.values()]
        hurt = [
            ((actor.x, actor.y), actor.id in world.buildings)
            for actor in actors
            if actor.hp < self._hp.get(actor.id, actor.hp)
        ]
        self._hp = {actor.id: actor.hp for actor in actors}
        return hurt

    def _follow(self, bases: list[BaseCluster], world: World) -> None:
        """Bring each mark's base up to date, and note the aircraft near it; a mark whose base is gone, or no longer
        remote, is dropped."""
        kept = []
        for mark in self._marks:
            base = next((base for base in bases if mark.members & {building.id for building in base.members}), None)
            if base is None:
                continue
            mark.follow(base)
            for aircraft in _aircraft_near(mark.buildings, world):
                mark.near[aircraft.id] = world.tick
            kept.append(mark)
        self._marks = kept

    def _mark_new(self, bases: list[BaseCluster], hurt: list[tuple[Cell, bool]], world: World) -> None:
        """Mark each remote base not marked yet that an enemy combat aircraft attacks in this cycle and that is not
        covered (`_is_covered`)."""
        for base in bases:
            ids = {building.id for building in base.members}
            if any(mark.members & ids for mark in self._marks):
                continue
            aircraft = _aircraft_near(base.members, world)
            hit = _hit(base.members, hurt) if aircraft else None
            if hit is None or self._is_covered(base.members, hit, world):
                continue
            attacker = min(aircraft, key=lambda record: cells.distance((record.x, record.y), hit))
            mark = _Mark(tuple(base.members), hit, attacker.id, {record.id: world.tick for record in aircraft})
            self._marks.append(mark)
            _log(MARKED, mark, world, aircraft_type=attacker.type)

    def _is_covered(self, buildings: Iterable[Building], hit: Cell, world: World) -> bool:
        """Whether a base has its AA defense already: one stands within COVERED_CELLS of the cell hit, or the one this
        rule put up stands at the base."""
        near = any(
            building.type in _ANTI_AIR_TYPES and cells.is_within((building.x, building.y), hit, COVERED_CELLS)
            for building in world.buildings.values()
        )
        return near or any(building.id in self._own for building in buildings)

    def _answer(self, mark: _Mark, world: World, ctx: MatchContext) -> list[Intent]:
        """A mark's orders in this cycle: none once its AA defense stands, the cancel once the attack is over, else
        the radar or the AA defense while one is due."""
        defense = ANTI_AIR_DEFENSES[world.family]
        if mark.ordered and self._is_placed(mark, defense, world, ctx):
            return []
        if self._is_over(mark, world):
            return self._cancel(mark, defense, world, ctx)
        if not mark.ordered and (mark.asks >= MOST_ASKS or self._is_covered(mark.buildings, mark.hit, world)):
            self._marks.remove(mark)
            return []
        if ctx.production.standing(RADAR) == 0:
            return self._radar(mark, world, ctx)
        return [] if mark.ordered else self._defense(mark, defense, world, ctx)

    def _is_placed(self, mark: _Mark, defense: str, world: World, ctx: MatchContext) -> bool:
        """Whether the AA defense ordered for a mark stands at its base, which clears the mark; a defense no longer on
        its way that has not shown at the base after SHOW_GRACE_TICKS was lost or placed elsewhere, and is ordered
        again while the mark has asks left."""
        standing = _anti_air_ids(world)
        placed = (standing - mark.before) & mark.members
        if placed:
            self._own.update(placed)
            self._marks.remove(mark)
            _log(PLACED, mark, world, item=defense, building=min(placed))
            return True
        if ctx.production.pending(defense) or defense in world.ready_to_place:
            mark.on_way_at = world.tick
        elif world.tick - mark.on_way_at >= SHOW_GRACE_TICKS:
            mark.ordered = False
        return False

    def _is_over(self, mark: _Mark, world: World) -> bool:
        """Whether the attack on a marked base is over: the aircraft that attacked is destroyed, and no other was seen
        near the base in the last QUIET_SECONDS."""
        if mark.attacker in world.enemy_memory:
            return False
        since = world.tick - QUIET_SECONDS * TICKS_PER_SECOND
        return all(tick < since for aircraft, tick in mark.near.items() if aircraft != mark.attacker)

    def _cancel(self, mark: _Mark, defense: str, world: World, ctx: MatchContext) -> list[Intent]:
        """Clear a mark whose attack is over, with the cancel of its AA defense while that is in a queue, which is not
        placed in this cycle."""
        self._marks.remove(mark)
        queued = any(job.item == defense for queue in world.production.values() for job in queue)
        _log(CANCELLED, mark, world, item=defense if mark.ordered else None)
        if not (mark.ordered and queued):
            return []
        self._withheld = self._withheld | {defense}
        return [Intent(Priority.PRODUCTION, {"cmd": "cancel", "item": defense})]

    def _radar(self, mark: _Mark, world: World, ctx: MatchContext) -> list[Intent]:
        """The radar the AA defense waits for, or the power plant before it, while none is on its way, it is on offer,
        the Building queue is free and the money covers its price."""
        production = ctx.production
        if production.planned(RADAR) or not _can_start(RADAR, BUILDING_QUEUE, world, ctx):
            return []
        orders = self._build_within_power(RADAR, world, ctx)
        if any(intent.order.get("item") == RADAR for intent in orders):
            _log(ORDERED, mark, world, item=RADAR)
        return orders

    def _defense(self, mark: _Mark, defense: str, world: World, ctx: MatchContext) -> list[Intent]:
        """The AA defense of a mark, or the power plant before it, when it is on offer, the Defense queue is free and
        the money covers its price."""
        if not _can_start(defense, DEFENSE_QUEUE, world, ctx):
            return []
        orders = self._defense_within_power(defense, world, ctx)
        if any(intent.order.get("item") == defense for intent in orders):
            mark.ordered, mark.before, mark.on_way_at = True, _anti_air_ids(world), world.tick
            mark.asks += 1
            _log(ORDERED, mark, world, item=defense)
        return orders


class _RocketSquad:
    """The rocket soldiers sent to a marked base until a radar stands. See the module's note."""

    def __init__(self) -> None:
        # Squad member -> the group it was taken from.
        self._taken_from: dict[int, str] = {}
        # The target the rockets were last trained for, and the units that stood when they were: a rocket that shows
        # after is a fresh one, taken whether idle or not.
        self._trained_for: Cell | None = None
        self._units_before: frozenset[int] = frozenset()

    def intents(self, target: Cell | None, world: World, ctx: MatchContext) -> list[Intent]:
        """The squad's orders in this cycle toward the cell hit of the first marked base; with no mark, the squad
        sent back to its groups and no order."""
        if target is None:
            self._release(ctx)
            return []
        if ctx.production.standing(RADAR) == 0:
            self._take(target, world, ctx)
        orders = self._train(target, world, ctx) if ctx.production.standing(RADAR) == 0 else []
        return [*orders, *self._send(target, world, ctx)]

    def _take(self, target: Cell, world: World, ctx: MatchContext) -> None:
        """Take into the squad, while it is short and main does not hold home, the rockets of the guard and of main
        that are free (`_is_free`): the fresh ones trained for it, then the idle ones nearest the target."""
        short = SQUAD_SIZE - len(ctx.groups.roster(AIR_SQUAD))
        if short <= 0 or is_holding_home(world, ctx):
            return
        free = [unit for unit in _rockets(world, ctx) if _is_free(unit, ctx)]
        fresh = [] if self._trained_for is None else [unit for unit in free if unit.id not in self._units_before]
        for unit in [*fresh, *_idle_nearest(free, fresh, target)][:short]:
            self._taken_from[unit.id] = ctx.groups.group_of(unit.id) or MAIN
            ctx.groups.assign(unit.id, AIR_SQUAD)

    def _train(self, target: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """The rockets the squad is short of, trained once for a target, while the Infantry queue is free and no
        other manager claims it, they are on offer and the money covers them."""
        short = SQUAD_SIZE - len(ctx.groups.roster(AIR_SQUAD))
        production = ctx.production
        if short <= 0 or self._trained_for == target or ROCKET_INFANTRY not in world.available_production:
            return []
        is_free = production.is_queue_free(INFANTRY_QUEUE) and not ctx.claims.is_held_by_other(
            INFANTRY_QUEUE, _CLAIMANT
        )
        if not is_free or world.economy.spendable < production.cost(ROCKET_INFANTRY) * short:
            return []
        self._trained_for, self._units_before = target, frozenset(world.units)
        return [production.train(ROCKET_INFANTRY, short)]

    @staticmethod
    def _send(target: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """An attack-move to the target for the squad's members away from it; the order ledger does not send it again
        to one on its way there."""
        going = [
            unit_id
            for unit_id in ctx.groups.members(AIR_SQUAD)
            if not cells.is_within((world.units[unit_id].x, world.units[unit_id].y), target, AT_BASE_CELLS)
        ]
        if not going:
            return []
        return [Intent(Priority.MOVE, {"cmd": "attack_move", "units": going, "to": list(target)})]

    def _release(self, ctx: MatchContext) -> None:
        """Send the squad's members back to the groups they came from."""
        for unit_id in ctx.groups.roster(AIR_SQUAD):
            ctx.groups.assign(unit_id, self._taken_from.pop(unit_id, MAIN))
        self._taken_from.clear()
        self._trained_for = None


def _idle_nearest(free: list[Unit], fresh: list[Unit], target: Cell) -> list[Unit]:
    """The idle units of `free` that are not `fresh`, the nearest the target first."""
    taken = {unit.id for unit in fresh}
    idle = [unit for unit in free if unit.idle and unit.id not in taken]
    return sorted(idle, key=lambda unit: cells.distance((unit.x, unit.y), target))


def _rockets(world: World, ctx: MatchContext) -> list[Unit]:
    """The rocket soldiers of the guard and of main in the field."""
    members = [*ctx.groups.members(GUARD), *ctx.groups.members(MAIN)]
    return [world.units[unit_id] for unit_id in members if world.units[unit_id].type == ROCKET_INFANTRY]


def _is_free(unit: Unit, ctx: MatchContext) -> bool:
    """Whether a unit is on no job of its own: no detachment, strike, crush run, repair, withdrawal or route walk, and
    no threat base defense has it answer."""
    detached = any(unit.id in detachment.units for detachment in ctx.detachments)
    held = unit.id in ctx.combat_held or unit.id in ctx.crush_run or unit.id in ctx.repairing
    busy = unit.id in ctx.withdrawn or ctx.groups.is_walking(unit.id) or ctx.groups.is_overridden(unit.id)
    return not (detached or held or busy)


def remote_bases(world: World, ctx: MatchContext) -> list[BaseCluster]:
    """Our remote bases: those of main's military state that do not hold the main yard and either have a yard of
    their own or have their center `remote_base_cells` or more from the main yard; none without a yard."""
    yards = [building for building in world.buildings.values() if building.type == CONSTRUCTION_YARD]
    if not yards:
        return []
    main, far = yards[0], ctx.tuning.anti_air.remote_base_cells
    return [
        base
        for base in base_clusters(world.buildings.values(), ctx.tuning.commander)
        if all(building.id != main.id for building in base.members)
        and (
            any(building.type == CONSTRUCTION_YARD for building in base.members)
            or math.dist(base.label_center, (main.x, main.y)) >= far
        )
    ]


def _aircraft_near(buildings: Iterable[Building], world: World) -> list[EnemyRecord]:
    """The enemy combat aircraft in sight within AIR_ATTACK_CELLS of one of the buildings."""
    spots = [(building.x, building.y) for building in buildings]
    return [
        record
        for record in world.enemy_memory.values()
        if record.visible
        and not record.is_building
        and is_combat_aircraft(record.type, world)
        and any(cells.is_within((record.x, record.y), spot, AIR_ATTACK_CELLS) for spot in spots)
    ]


def _hit(buildings: Sequence[Building], hurt: list[tuple[Cell, bool]]) -> Cell | None:
    """The cell of the base's building that lost hp, else of the own unit within AIR_ATTACK_CELLS of one of its
    buildings that did; None when none did."""
    spots = {(building.x, building.y) for building in buildings}
    in_base = [cell for cell, is_building in hurt if is_building and cell in spots]
    near = [
        cell
        for cell, is_building in hurt
        if not is_building and any(cells.is_within(cell, spot, AIR_ATTACK_CELLS) for spot in spots)
    ]
    return next(iter([*in_base, *near]), None)


def _anti_air_ids(world: World) -> frozenset[int]:
    return frozenset(building.id for building in world.buildings.values() if building.type in _ANTI_AIR_TYPES)


def _can_start(item: str, queue: str, world: World, ctx: MatchContext) -> bool:
    """Whether an item of the answer can be ordered now: on offer, its queue free and its price covered."""
    production = ctx.production
    is_open = item in world.available_production and production.is_queue_free(queue)
    return is_open and world.economy.spendable >= production.cost(item)


def _log(action: str, mark: _Mark, world: World, **more: object) -> None:
    """Write one `anti_air` event of the remote base answer: what was done, the cell hit and the aircraft that
    attacked."""
    data = {
        "tick": world.tick,
        "reason": REMOTE_BASE_ATTACK,
        "action": action,
        "cell": list(mark.hit),
        "attacker": mark.attacker,
        **more,
    }
    records.log(ANTI_AIR_EVENT, data)
