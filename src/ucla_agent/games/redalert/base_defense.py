"""Base defense: noticing an attack on the base the moment it starts, and answering it in the same cycle.

The base-defense manager runs first in every cycle, so that every manager after
it knows whether the base is under attack. It asks no model and waits for
nothing: an attack is answered with what the state in hand shows.

A threat is declared when the state shows any of three things: an enemy unit
that fights is in sight and close to one of the agent's buildings; the
platform reports an own building or harvester under attack; or one of them
has lost hp since the last state while such an enemy is close to it. The
first sign of a threat names what it is against: the actor reported under
attack, else the one that lost hp, else the building nearest to the enemy.
The threat stays against that actor's cell while the attack goes on around
it, and moves only when a sign comes from beyond the threat area: in a fight
one building after another is hit, and the defenders are not to be sent from
one to the next.

How big a threat is decides how it is answered. Its size is what the enemy
units that fight and are in sight in the threat area cost: the area around
the centre of the base and around the threatened actor. One enemy unit that
is cheap enough is a minor threat; anything more is major, and a threat that
was major once stays major until it is over. A threat is over once no enemy
unit that fights has been in sight near the base or any own building for a
few seconds; a fresh sign of an attack counts as such a sighting, so that an
enemy shooting from out of sight does not end the threat while it shoots.

A threat led by vehicles (most of its enemies' worth is in vehicles) that has
hit an own building or harvester is a vehicle attack. The match keeps the
cells of its vehicle attacks (`MatchContext.vehicle_attacks`), one for each
threat, from the first cycle in which it was both, whatever its size: a Tesla
coil that waits for a real threat is placed by them.

Every order that answers a threat goes at emergency priority, ahead of
everything else in the batch, and a unit given one is told nothing else in
that batch. A minor threat is the home guard's alone: it attacks the enemy
unit while that unit is in sight near the centre of the base, and no other
group is touched. A major threat draws in the guard and every unit that
fights near the base, all of them sent to the threatened actor's cell with
`attack_move`; their groups fight it out whatever the estimate of the fight
says. With `[scouting] scout_slots` on, the designated scouts are never drawn
in: they scout whatever the base faces. A scout released from its slot on its
way home under hold fire (see `scout_stance`) within threat_area_cells of the
threat's cell is taken into main at once, in main's stance, and answers as a
unit of main. Nor is a unit walking a route to an
ore field (see `route_walk`), for any threat, nor a tank on a crush run (see
`crush`): it keeps to its run, and is sent once the run lets it go. A unit is
told where to go once in a threat, and again only when the
threat moves elsewhere: telling it again while it fights would pull it off the
enemy it is fighting. With `contact_focus` of `[combat]` on, the units that
have arrived at the threatened cell or are in contact with an enemy in sight
(`targeting.is_in_contact`) are handed out over the enemies as an engaged
group is (see `targeting`), each told to attack its target once, and again
only when its target changes, and sent after no enemy beyond their reach; the
others are sent to the cell by `attack_move`. The groups farther out (`main` and a raid) are called home only when
the threat is worth more than the units already answering it, counted at
their cost and remaining health; they go to the staging point, and from there
they are near enough to join in. An APC raid under way comes home aboard its
APCs, its rockets boarding them first: the raid manager brings it home, and
its units (`MatchContext.raid_homebound`) are not called home here. When the threat is over the marks are
cleared, and each unit that was called home gets back the last order it had
been given before, which is what it was doing when the base called.

With `harvester_group_response` on, a threat that has hit a harvester away
from the base, under a plan that keeps main at home, is main's alone to
answer (see `harvester_response`): the guard and the units near the base are
told nothing for it and stay home, and nothing is called home. While main
answers a major one and is strong enough for the threat's enemies, no
counter infantry is trained for it.

With `keep_attack_army` on, main on the attack is left to it. While the plan
is PRESS, STRIKE or FINISH, no unit of main answers a major threat and main
is not called home, unless the threat is worth more than the home guard (at
its cost and remaining health) and the own standing defenses within the
threat area of its cell together: what is at home holds the base, and the
attack that would end the match is not given up for a raid on it. A threat
worth more than that is answered as without the switch. The override of a
fight's estimate is then set on the units answering a threat, and on the
members of a group called home, one by one (see `groups`), not on whole
groups: the other members of a group keep the orders their manager gives
them. A threat whose enemies have all been aircraft, from the first cycle it
shows one until one on the ground is seen in it, is answered only by the
units whose weapon hits aircraft (and the agent's own aircraft); a ground
unit that cannot hit them is told nothing for it, and no group is called
home for it. The AA defenses fire at them as always.

While the model commander commands main (see `commander`), main is left to
its commanded action under every plan as `keep_attack_army` leaves it to its
attack: no unit of main answers a major threat and main is not called home,
unless the threat is worth more than the home guard and the own standing
defenses within the threat area of its cell together. One that is worth more
takes main as without the commander, whatever main was commanded.

With `commit_on_cross` of `[combat]` on, main committed to the base trade
once the armies crossed (see `cross_commit`) is never taken nor called home,
whatever the threat is worth, and answers no harvester threat. The canary
(see `canary`) answers no threat.

With `canary_alarm` of `[upkeep]` on, base defense keeps the canary alarm
(see `canary_alarm`) right after it has published the threat, and while the
alarm is in force orders a defense whenever the Defense queue is free, after
its own extra defense: the anti-infantry or the anti-armor one by what the
enemy combat units seen in the last minute were mostly worth, the anti-armor
one when none was seen, placed toward the alarm's goal cell on the side of
the base facing the enemy's start. A Tesla coil that would leave less power
than upkeep's power target waits, the power plant ordered first.

With `keep_mcv_escort` on and `mcv_escort_reliance` of [expansion], the
escort of an MCV on its way to its site is not drawn into a threat on the
base, however near the base it passes: taken off it, the MCV waits for an
escort it no longer has (in the live games of 20261016 a travelling MCV and
its escort stopped and turned back when the base was hit). Only a base truly
losing takes it: no other unit answers the threat, and the own defenses near
the threat are worth less than escort_needed_ratio of it. A `base_defense`
event of reason `escort_kept` says so, at most once every ESCORT_KEPT_SECONDS.

With `approach_alarm` on, base defense keeps the approach alarm (see
`approach_alarm`) right after the canary alarm: an enemy army seen coming for
the base from up to approach_alarm_cells out, before any threat is declared.
While it is in force and the canary alarm is not, a defense is ordered whenever
the Defense queue is free, as many as the group's worth calls for: the
anti-infantry one against an infantry-led group, else the anti-armor one, a
Tesla coil waiting for its power plant, placed toward the alarm's goal cell on
the side of the base facing the group.

Far from the base, main is kept out of a rescue it would lose (see
`far_rescue`): a threat more than far_rescue_cells from the centre of the
base, where main is estimated under far_rescue_min_ratio or a known enemy
yard or ground defense stands near, draws no unit of main in and does not
call main home, whatever the plan or the commander leaves main to; the units
of main already sent to it get an emergency move back to the staging point in
the cycle main is kept out, but those a strike, a crush run or a repair
retreat holds, which are called back once let go. The guard, the other units
near the base and the raid group answer it as they would.

Harvesters are the income and never fight. Whatever the threat, a harvester
with an enemy unit that fights close to it runs to the nearest own defense,
or to the centre of the base when none stands, and stays there until no such
enemy has been near it for a few seconds; then it is sent back to harvest.
It is sent to its shelter once, like a unit to a threat.

While a major threat lasts the base also makes what answers it. The Infantry
queue is the manager's: it trains the counter to the class of enemy that
makes up most of the threat's worth, three at a time, rifles against
infantry and rockets against vehicles. Once a war factory stands under a main
force of tanks (`main_force` of the army mix) and main holds four combat units
or more, an infantry threat (infantry more than half of what its enemies that
are infantry or vehicles are worth) gets no counter and the Infantry queue is
given up: tanks crush infantry, and rifles bought against rifles starve them of
money. One `base_defense` event with reason `counter_skipped_tanks` says so for
each threat. While the enemy is infantry-heavy and `infantry_counter_lift` of
[composition] is on, that block is lifted: the threat gets the family's
counter infantry, the Soviet flamethrower while it is on offer, and a
`base_defense` event with reason `counter_lifted_infantry_heavy` for each
threat instead (see `counter_units`). While the rush watch says rush and main holds fewer than
rush_min_tanks tanks (`[opening]`), the counter is trained all the same: the
rush wave comes before the tanks do. The Defense queue adds a defense of the
kind the threat calls for, at most one a minute, so that a long siege does
not swallow all the money. A Tesla coil is held to the power target upkeep
keeps for its own buildings: when it would leave less power over than that,
the power plant is ordered first. These are base defense's own orders, so
the rule by which upkeep holds unit production back while it saves for a
lost building does not hold them back.

A building under attack is repaired whatever its health, as long as there is
a little money: upkeep waits until a building is badly damaged, which is too
late for one that is being shot at. One whose health is above the point where
upkeep switches repair off again is left alone, since the two would only
switch it back and forth.

With `harass_tower` on, the manager also puts up a cheap ground defense in
range of a known enemy building that stands near the base (see
`harass_tower`), after what answers a threat has had the Defense queue.

With `stepped_return` of `[combat]` on, main called home from 15 cells or
more comes home in steps (see `stepping`): its units are moved to each step
point in turn rather than in one long move, so that it arrives together.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ucla_agent.core import records
from ucla_agent.games.redalert import (
    approach_alarm,
    canary_alarm,
    cells,
    defense_cap,
    kite,
    stepping,
    targeting,
    truck_retreat,
    truck_shares,
)
from ucla_agent.games.redalert.batch import ACTIVITY_COMMANDS, Intent, Priority
from ucla_agent.games.redalert.commander import MAIN_ROLE
from ucla_agent.games.redalert.composition import FINISH, PRESS, STRIKE
from ucla_agent.games.redalert.counter_units import is_counter_lifted, lifted_infantry_counter
from ucla_agent.games.redalert.crush import TANK_ROLE
from ucla_agent.games.redalert.expansion import ESCORT
from ucla_agent.games.redalert.family import FAMILIES, HARVESTER, MCV, RIFLE_INFANTRY, ROCKET_INFANTRY, WAR_FACTORY
from ucla_agent.games.redalert.far_rescue import BASE_DEFENSE_EVENT, FarRescue
from ucla_agent.games.redalert.fight import centroid
from ucla_agent.games.redalert.groups import CANARY, GUARD, MAIN, RAID, SCOUT, SIEGE_TYPES
from ucla_agent.games.redalert.harass_tower import HarassTowers
from ucla_agent.games.redalert.harvester_crush import HarvesterCrush
from ucla_agent.games.redalert.harvester_response import HarvesterResponse
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.ledger import LOST, REJECTED, OrderLedger
from ucla_agent.games.redalert.managers import MAJOR_THREAT, MINOR_THREAT, NO_THREAT, MatchContext
from ucla_agent.games.redalert.production import DEFENSE_QUEUE, INFANTRY_QUEUE, VEHICLE_QUEUE
from ucla_agent.games.redalert.rush_watch import RUSH
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.scout_stance import MAIN_STANCE, stance_order
from ucla_agent.games.redalert.tuning import TANKS, BaseDefenseTuning
from ucla_agent.games.redalert.upkeep import power_plant_when_free, power_target, repairs_on_in_force
from ucla_agent.games.redalert.world import DEFENSE_TYPES, Building, Cell, EnemyRecord, Unit, World

MANAGER_NAME = "base_defense"
UNDER_ATTACK_EVENT = "under_attack"
# What tells main's stepped way home while it is called home apart (see `stepping`).
CALLED_HOME = "called_home"
# The groups that may be called home from afar to defend the base.
RECALLABLE_GROUPS = (MAIN, RAID)
# With `keep_attack_army` on: the plans under which main is on the attack, and the groups called home while main is
# left to it.
ATTACK_PLANS = frozenset({PRESS, STRIKE, FINISH})
_RECALLABLE_BUT_MAIN = (RAID,)
_PERCENT = 100
# What the Infantry queue trains against a threat of each class (the queue that makes it), the first on offer.
_COUNTERS = {INFANTRY_QUEUE: (RIFLE_INFANTRY,), VEHICLE_QUEUE: (ROCKET_INFANTRY, RIFLE_INFANTRY)}
# The Tesla coil, the Soviet defense against vehicles, drains so much power that it is built only when the base's
# power target is left over after it: the spare power of a grown base is not there to buy coils.
_POWER_HUNGRY_DEFENSES = frozenset({"tsla"})
# How near a threat's ground enemies our ground strength counts against theirs, for an extra defense.
LOCAL_STRENGTH_CELLS = 12
# With home_defenses_in_reach on, a defense holds the base for main only where its weapon range and this many cells
# reach an enemy of the threat: one a cell or so short of them may still be closed on.
HOME_DEFENSE_REACH_MARGIN_CELLS = 1
# The fewest combat units main holds for a tank army to be left the answer to an infantry threat: a base with no army
# yet still trains counter rifles against a rush.
COUNTER_MIN_MAIN_UNITS = 4
# The reason of the `base_defense` event written when an infantry threat's counters are left to the tank army.
COUNTER_SKIPPED_TANKS = "counter_skipped_tanks"
# And the reason written when that is lifted, the enemy being infantry-heavy, and the counter infantry is trained.
COUNTER_LIFTED = "counter_lifted_infantry_heavy"
# The reason of the `base_defense` event of an MCV's escort kept on its way, and how often it is written.
ESCORT_KEPT = "escort_kept"
ESCORT_KEPT_SECONDS = 30
# The rules base defense orders defenses by, as a `defense_capped` event names them (see `defense_cap`).
EXTRA_DEFENSE_RULE = "extra_defense"
APPROACH_RULE = "approach_alarm"


@dataclass
class Threat:
    """An attack on the base, from the cycle it is declared until it is over."""

    # The cell of the own building or harvester the threat is against.
    cell: Cell
    # The visible enemy units that fight and are in the threat area, as of the latest cycle.
    enemies: list[EnemyRecord]
    # What `enemies` cost together.
    size: int
    # Major from the first cycle it was, until the threat is over.
    is_major: bool
    # The own buildings and harvesters reported under attack, or that lost hp near an enemy, in the latest cycle.
    attacked: list[int]
    # Whether every enemy seen in the threat area so far has been an aircraft: False from the first cycle one on the
    # ground is, None while none has been seen.
    aircraft_only: bool | None = None

    def is_air_raid(self, tuning: BaseDefenseTuning) -> bool:
        """Whether the threat is answered as a raid of aircraft alone: `keep_attack_army` is on and every enemy seen in
        it has been an aircraft."""
        return tuning.keep_attack_army and self.aircraft_only is True


def enemy_combat_units(world: World) -> list[EnemyRecord]:
    """The enemy units in sight that fight."""
    return [record for record in world.enemy_memory.values() if record.visible and is_combat_unit(record)]


def _is_near_any(cell: Cell, others: Iterable[Cell], reach: int) -> bool:
    return any(cells.is_within(cell, other, reach) for other in others)


def _cell_of(record: EnemyRecord) -> Cell:
    return record.x, record.y


class ThreatWatch:
    """Declares a threat, follows it from cycle to cycle and says when it is over. See the module's note."""

    def __init__(self) -> None:
        self.threat: Threat | None = None
        # Own building or harvester id -> its hp in the previous state.
        self._hp: dict[int, int] = {}
        # The last tick an enemy unit that fights was in sight near the base, or a threat was detected.
        self._last_alarm_tick = 0
        # Whether the threat in force has been recorded as a vehicle attack, which it is once at the most.
        self._is_attack_recorded = False

    def update(self, world: World, tuning: BaseDefenseTuning) -> Threat | None:
        """Bring the threat up to date with the cycle's state; the threat in force, or None when there is none."""
        hostile = enemy_combat_units(world)
        attacked = self._attacked(world, hostile, tuning.alarm_radius_cells)
        signalled = _actor_cell(attacked[0], world) if attacked else _building_nearest(hostile, world, tuning)
        if signalled is not None or _is_near_base(hostile, world, tuning.threat_area_cells):
            self._last_alarm_tick = world.tick
        if signalled is None and (self.threat is None or world.tick - self._last_alarm_tick >= tuning.threat_end_ticks):
            self.threat = None
            self._is_attack_recorded = False
            return None
        self._follow(self._cell_against(signalled, tuning), hostile, attacked, world, tuning)
        return self.threat

    def record_vehicle_attack(self, world: World, vehicle_attacks: list[Cell]) -> None:
        """Add the cell of the threat in force to `vehicle_attacks` in the first cycle in which it is led by vehicles
        and has hit an own building or harvester; a threat is added once at the most, whatever its size."""
        threat = self.threat
        if threat is None or self._is_attack_recorded or not threat.attacked:
            return
        if largest_class(threat.enemies, world) == VEHICLE_QUEUE:
            vehicle_attacks.append(threat.cell)
            self._is_attack_recorded = True

    def _cell_against(self, signalled: Cell | None, tuning: BaseDefenseTuning) -> Cell:
        """The cell the threat is against: the one in force, unless a sign came from beyond the area around it."""
        if self.threat is None:
            return signalled
        if signalled is None or cells.is_within(signalled, self.threat.cell, tuning.threat_area_cells):
            return self.threat.cell
        return signalled

    def _follow(
        self, cell: Cell, hostile: list[EnemyRecord], attacked: list[int], world: World, tuning: BaseDefenseTuning
    ) -> None:
        """Declare the threat against a cell, or carry the one in force on with what the cycle shows."""
        centres = _and_base_center(world, [cell])
        enemies = [enemy for enemy in hostile if _is_near_any(_cell_of(enemy), centres, tuning.threat_area_cells)]
        size = sum(_cost(enemy.type, world) for enemy in enemies)
        is_minor = len(enemies) <= 1 and size <= tuning.minor_max_cost
        was_major = self.threat is not None and self.threat.is_major
        aircraft_only = _aircraft_only(None if self.threat is None else self.threat.aircraft_only, enemies, world)
        self.threat = Threat(cell, enemies, size, was_major or not is_minor, attacked, aircraft_only)

    def _attacked(self, world: World, hostile: list[EnemyRecord], reach: int) -> list[int]:
        """The own buildings and harvesters under attack: those the newest events name, then those hit near an enemy.

        Every call also notes the hp of each, for the next call to compare with.
        """
        hp_before, actors = self._hp, _guarded_actors(world)
        self._hp = {actor_id: hp for actor_id, (hp, _) in actors.items()}
        named = [event["id"] for event in reversed(world.events_new) if _names_guarded(event, actors)]
        enemy_cells = [_cell_of(enemy) for enemy in hostile]
        hit = [
            actor_id
            for actor_id, (hp, cell) in actors.items()
            if hp < hp_before.get(actor_id, hp) and _is_near_any(cell, enemy_cells, reach)
        ]
        return list(dict.fromkeys([*named, *hit]))


def _aircraft_only(was: bool | None, enemies: list[EnemyRecord], world: World) -> bool | None:
    """Whether a threat's enemies have all been aircraft, as it was (`was`) and with the cycle's `enemies`: unchanged
    once one on the ground was seen, or while none is in sight."""
    if was is False or not enemies:
        return was
    return all(world.items.damage.get(enemy.type).air for enemy in enemies)


def _and_base_center(world: World, centres: list[Cell]) -> list[Cell]:
    """The cells given, and the centre of the base when the state names it."""
    return centres if world.base_center is None else [world.base_center, *centres]


def _is_near_base(hostile: list[EnemyRecord], world: World, reach: int) -> bool:
    """Whether an enemy unit that fights is in sight near the centre of the base or near an own building."""
    guarded = _and_base_center(world, [(building.x, building.y) for building in world.buildings.values()])
    return any(_is_near_any(_cell_of(enemy), guarded, reach) for enemy in hostile)


def _guarded_actors(world: World) -> dict[int, tuple[int, Cell]]:
    """The own buildings and harvesters, the actors an attack on which is a threat: id -> (hp, cell)."""
    actors = {building.id: (building.hp, (building.x, building.y)) for building in world.buildings.values()}
    actors.update((unit.id, (unit.hp, (unit.x, unit.y))) for unit in world.units.values() if unit.type == HARVESTER)
    return actors


def _names_guarded(event: dict, actors: dict[int, tuple[int, Cell]]) -> bool:
    return event.get("kind") == UNDER_ATTACK_EVENT and event.get("id") in actors


def _actor_cell(actor_id: int, world: World) -> Cell:
    actor = world.buildings.get(actor_id) or world.units[actor_id]
    return actor.x, actor.y


def _building_nearest(hostile: list[EnemyRecord], world: World, tuning: BaseDefenseTuning) -> Cell | None:
    """The cell of the own building nearest to an enemy that fights, when one is within the alarm radius of it."""
    reach = tuning.alarm_radius_cells
    pairs = [
        (cells.distance((building.x, building.y), _cell_of(enemy)), (building.x, building.y))
        for building in world.buildings.values()
        for enemy in hostile
        if cells.is_within((building.x, building.y), _cell_of(enemy), reach)
    ]
    return min(pairs)[1] if pairs else None


def _cost(item: str, world: World) -> int:
    facts = world.items.get(item)
    return facts.cost if facts is not None else 0


def strength(units: Iterable[Unit | Building], world: World) -> float:
    """What units, or defenses, are worth in a fight: the cost of each, weighted by the share of its health it has
    left."""
    return sum(_cost(unit.type, world) * unit.hp / _PERCENT for unit in units)


class CombatResponse:
    """Who answers a threat and what they are told, and who was called home. See the module's note."""

    def __init__(self, far_rescue: FarRescue) -> None:
        # Unit id -> the last order it was given before its group was called home, without its unit ids.
        self._before_recall: dict[int, dict[str, Any]] = {}
        # Unit id -> where it was last sent in the threat in force: a cell, or the id of the enemy to attack.
        self._sent: dict[int, Cell | int] = {}
        # Whether main is kept out of a far rescue it would lose; shared with main's answer to a harvester threat.
        self._far_rescue = far_rescue
        # main's way home in steps while it is called home (see `stepping`).
        self._home_steps = stepping.Stepper()

    def intents(self, threat: Threat | None, world: World, ctx: MatchContext) -> list[Intent]:
        """The orders that answer the threat in force, or give back what the groups called home were doing."""
        self._home_steps.begin_cycle()
        orders = self._answer(threat, world, ctx)
        self._home_steps.end_cycle()
        return orders

    def _answer(self, threat: Threat | None, world: World, ctx: MatchContext) -> list[Intent]:
        """The orders of `intents`, main's way home in steps followed in the cycles it is called home."""
        if threat is None:
            self._sent = {}
            return self._release(world, ctx)
        # A tank on a crush run is told nothing, and is sent where the threat says once the run lets it go.
        self._sent = {unit_id: aim for unit_id, aim in self._sent.items() if unit_id not in ctx.crush_run}
        if threat.is_major:
            return self._major(threat, world, ctx)
        return self._minor(threat, world, ctx)

    def _minor(self, threat: Threat, world: World, ctx: MatchContext) -> list[Intent]:
        """The home guard's attack on the one enemy unit of a minor threat, while it is in sight near the base."""
        reach = ctx.tuning.base_defense.threat_area_cells
        targets = [
            enemy
            for enemy in threat.enemies
            if world.base_center is not None and cells.is_within(_cell_of(enemy), world.base_center, reach)
        ]
        if not targets:
            return []
        target = targets[0].id
        able = [
            unit_id
            for unit_id in ctx.groups.members(GUARD)
            if _may_answer(unit_id, threat, world, ctx) and _may_attack_from_here(unit_id, targets[0], world)
        ]
        guard = self._not_yet_sent(able, target, ctx.tuning.base_defense)
        return [Intent(Priority.EMERGENCY, {"cmd": "attack", "units": guard, "target": target})] if guard else []

    def _major(self, threat: Threat, world: World, ctx: MatchContext) -> list[Intent]:
        """Every unit near the base, and the guard, to the threatened cell; the groups far out home when outmatched.
        With `keep_attack_army` on, main on the attack takes no part unless the threat outweighs what is at home, and a
        raid of aircraft alone draws in only the units that can hit them and calls nobody home. main kept out of a far
        rescue takes no part either, and its units sent to the threat go back to the staging point."""
        taken = _take_returning_scouts(threat, world, ctx)
        main_answers, pulled_back = self._main_answers_here(threat, world, ctx)
        responders = _responders(threat, main_answers, world, ctx)
        if threat.size > strength(responders.values(), world):
            self._recall(_recallable(threat, main_answers, ctx), ctx)
        _override_responders(responders, ctx)
        called_home = list(dict.fromkeys([*_called_home(responders, world, ctx), *pulled_back]))
        tuning = ctx.tuning.base_defense
        attacks, firing = self._focus_fire(responders, threat, world, ctx)
        # Artillery and V2 hold their place and fire on what is in their range: they are sent to no threat.
        arriving = [
            unit.id
            for unit in responders.values()
            if unit.id not in firing
            and unit.type not in SIEGE_TYPES
            and not _has_arrived(unit, threat.cell, tuning)
            and not self._keeps_attacking(unit.id, world, tuning)
        ]
        point = cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells) or threat.cell
        moving_in = self._not_yet_sent(arriving, threat.cell, tuning)
        self._far_rescue.note_sent([*moving_in, *(unit_id for intent in attacks for unit_id in intent.order["units"])])
        return [
            *taken,
            *attacks,
            *_unit_order("attack_move", moving_in, threat.cell),
            *self._home_moves(called_home, point, world, ctx),
        ]

    def _home_moves(self, called_home: list[int], point: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """The emergency moves to the staging point of the units called home that are not yet sent there: with
        stepped_return of `[combat]` on, main's from afar in steps, each step to its step point (see `stepping`)."""
        tuning = ctx.tuning
        main = [world.combat_units[unit_id] for unit_id in called_home if ctx.groups.group_of(unit_id) == MAIN]
        move = None
        if tuning.combat.stepped_return and main:
            plan = stepping.Plan((CALLED_HOME, point), stepping.HOME, point)
            move = self._home_steps.follow(plan, main, world, ctx.terrain, tuning)
        if move is None:
            return _unit_order("move", self._not_yet_sent(called_home, point, tuning.base_defense), point)
        group = [unit for unit in main if unit.id in move.group]
        going = move.due(
            dict.fromkeys((unit.id for unit in group), move.point), group, world.tick, tuning.combat.step_arrive_cells
        )
        rest = [unit_id for unit_id in called_home if unit_id not in move.group]
        return [
            *_unit_order("move", going, move.point),
            *_unit_order("move", self._not_yet_sent(rest, point, tuning.base_defense), point),
        ]

    def _main_answers_here(self, threat: Threat, world: World, ctx: MatchContext) -> tuple[bool, list[int]]:
        """Whether main may answer a major threat (see `_main_answers`) and is not kept out of it as a far rescue it
        would lose (see `far_rescue`), weighed only while main has a unit; and the units of main kept out that were
        sent to the threat already, which go back to the staging point."""
        if not _main_answers(threat, world, ctx):
            return False, []
        main = ctx.groups.members(MAIN)
        if not (main and self._far_rescue.keeps_main_out(threat, world, ctx)):
            return True, []
        return False, self._far_rescue.take_recall(world, ctx)

    def _focus_fire(
        self, responders: dict[int, Unit], threat: Threat, world: World, ctx: MatchContext
    ) -> tuple[list[Intent], frozenset[int]]:
        """With contact_focus on, the emergency attacks of the units answering a major threat that have arrived at the
        threatened cell or are in contact, each for the units not yet sent at its target, and every unit given a
        target; none with it off. An artillery piece or a V2 with an enemy come within its minimum range and the margin
        steps back toward the base instead (see `kite`), and is sent at its target afresh once it fires again."""
        if not ctx.tuning.combat.contact_focus:
            return [], frozenset()
        tuning = ctx.tuning.base_defense
        steps, stepping = self._step_back(responders, world, ctx)
        engaged = _engaged([unit for unit in responders.values() if unit.id not in stepping], threat, world, tuning)
        if not engaged:
            return steps, stepping
        focus = targeting.FocusFire(ctx.target_memory)
        crushing = bool(ctx.crush_run) or ctx.run_over_out
        given = focus.orders(engaged, centroid(engaged), world, ctx.tuning, chase=False, crushing=crushing)
        attacks = []
        for intent in given:
            order = intent.order
            # A tank charging an enemy artillery piece or V2 is given a move to its cell, not an attack on it (see
            # `targeting.charging`).
            aim = order["target"] if order["cmd"] == "attack" else tuple(order["to"])
            fresh = self._not_yet_sent(list(order["units"]), aim, tuning)
            if fresh:
                attacks.append(Intent(Priority.EMERGENCY, {**order, "units": fresh}))
        return [*steps, *attacks], stepping.union(unit_id for intent in given for unit_id in intent.order["units"])

    def _step_back(
        self, responders: dict[int, Unit], world: World, ctx: MatchContext
    ) -> tuple[list[Intent], frozenset[int]]:
        """The fall-back moves of the artillery and V2 among the responders that an enemy has come close to (see
        `kite`), and the units given one, each of which is sent at its target afresh once it fires again."""
        steps = kite.fall_back_moves(responders.values(), world, ctx.tuning)
        stepping = kite.stepping(steps)
        for unit_id in stepping:
            self._sent.pop(unit_id, None)
        return steps, stepping

    def _keeps_attacking(self, unit_id: int, world: World, tuning: BaseDefenseTuning) -> bool:
        """With keep_attack_in_sight on, whether a unit was last sent at an enemy that is still in sight: it keeps
        that attack rather than be sent back to the threatened cell. Walking to its target takes it out of arrival and
        of contact, and a move back there followed by a fresh attack once it arrived had it walk to and fro."""
        target = self._sent.get(unit_id)
        if not tuning.keep_attack_in_sight or not isinstance(target, int):
            return False
        record = world.enemy_memory.get(target)
        return record is not None and record.visible

    def _not_yet_sent(self, unit_ids: list[int], aim: Cell | int, tuning: BaseDefenseTuning) -> list[int]:
        """The units not yet sent where `aim` says in this threat, noted as sent there from now on.

        A cell within the arrival distance of the one a unit was sent to counts
        as the same.
        """
        fresh = [unit_id for unit_id in unit_ids if not _is_same_aim(self._sent.get(unit_id), aim, tuning)]
        self._sent.update(dict.fromkeys(fresh, aim))
        return fresh

    def _recall(self, recallable: Iterable[str], ctx: MatchContext) -> None:
        """Call home the groups far out that are not called home already, remembering what their units were doing."""
        groups = ctx.groups
        for group in recallable:
            if not groups.is_recalled(group):
                groups.recall(group)
                self._before_recall.update(_last_orders(set(groups.members(group)) - ctx.raid_homebound, ctx.ledger))
                _override_recalled(group, ctx)

    def _release(self, world: World, ctx: MatchContext) -> list[Intent]:
        """Clear the marks of a threat that is over, and give each unit called home its last order before the recall."""
        released = ctx.groups.release_defense()
        before, self._before_recall = self._before_recall, {}
        units: dict[str, list[int]] = {}
        orders: dict[str, dict[str, Any]] = {}
        for unit_id, order in before.items():
            if ctx.groups.group_of(unit_id) in released:
                wording = repr(sorted(order.items()))
                orders.setdefault(wording, order)
                units.setdefault(wording, []).append(unit_id)
        # The end of a recall: what each unit is given back moves it out again, from the base.
        return [Intent(Priority.MOVE, {**orders[wording], "units": ids}) for wording, ids in units.items()]


def _take_returning_scouts(threat: Threat, world: World, ctx: MatchContext) -> list[Intent]:
    """Take each scout on its way home (see `scout_stance`) within threat_area_cells of a major threat's cell into main
    at once: the stance orders that give them main's stance."""
    reach, groups = ctx.tuning.base_defense.threat_area_cells, ctx.groups
    taken = [
        unit_id
        for unit_id in sorted(ctx.returning_scouts)
        if unit_id in world.combat_units
        and groups.group_of(unit_id) == SCOUT
        and cells.is_within((world.combat_units[unit_id].x, world.combat_units[unit_id].y), threat.cell, reach)
    ]
    for unit_id in taken:
        groups.assign(unit_id, MAIN)
    return [stance_order(unit_id, MAIN_STANCE) for unit_id in taken]


def _override_responders(responders: Iterable[int], ctx: MatchContext) -> None:
    """Put the units answering a major threat under base defense's override: with `keep_attack_army` on, those units
    alone, so that the rest of each group keeps its orders; else each one's whole group."""
    groups = ctx.groups
    if ctx.tuning.base_defense.keep_attack_army:
        groups.set_unit_override(responders)
        return
    for unit_id in responders:
        groups.set_defense_override(groups.group_of(unit_id))


def _override_recalled(group: str, ctx: MatchContext) -> None:
    """Put a group called home under base defense's override: with `keep_attack_army` on, its members one by one;
    else the group."""
    groups = ctx.groups
    if ctx.tuning.base_defense.keep_attack_army:
        groups.set_unit_override(groups.roster(group))
    else:
        groups.set_defense_override(group)


def _called_home(responders: dict[int, Unit], world: World, ctx: MatchContext) -> list[int]:
    """The units of the groups called home that are not near enough to answer the threat yet, but those the raid
    manager brings home itself and the tanks on a crush run."""
    groups, homebound = ctx.groups, ctx.raid_homebound
    return [
        unit_id
        for unit_id in world.combat_units
        if unit_id not in responders
        and unit_id not in homebound
        and unit_id not in ctx.crush_run
        and groups.is_recalled(groups.group_of(unit_id))
    ]


def _responders(threat: Threat, main_answers: bool, world: World, ctx: MatchContext) -> dict[int, Unit]:
    """The units that answer a major threat: the home guard, and every unit that fights near the centre of the base;
    with `scout_slots` on, never a designated scout, which scouts whatever the base faces; no unit of main unless
    `main_answers`; and of a raid of aircraft alone, only the units that may answer it."""
    groups, reach = ctx.groups, ctx.tuning.base_defense.response_radius_cells
    centre = world.base_center or threat.cell
    # The canary holds its post whatever the base faces (see `canary`).
    kept_out = {SCOUT if ctx.tuning.scouting.scout_slots else None, None if main_answers else MAIN, CANARY}
    found = {
        unit_id: unit
        for unit_id, unit in world.combat_units.items()
        if (groups.group_of(unit_id) == GUARD or cells.is_within((unit.x, unit.y), centre, reach))
        and groups.group_of(unit_id) not in kept_out
        and _may_answer(unit_id, threat, world, ctx)
    }
    escort = {unit_id for unit_id in found if groups.group_of(unit_id) == ESCORT}
    if escort and _keeps_escort(threat, len(found) - len(escort), world, ctx):
        return {unit_id: unit for unit_id, unit in found.items() if unit_id not in escort}
    return found


def _keeps_escort(threat: Threat, others: int, world: World, ctx: MatchContext) -> bool:
    """Whether the escort of an MCV on its way stays with it rather than answer a threat (see the module's note):
    keep_mcv_escort and mcv_escort_reliance are on, an MCV of ours is out, and the base is not truly losing (`others`
    units answer it, or the defenses near its cell are worth escort_needed_ratio of it). Writes the `escort_kept`
    event, at most once every ESCORT_KEPT_SECONDS."""
    tuning = ctx.tuning
    is_on = tuning.base_defense.keep_mcv_escort and tuning.expansion.mcv_escort_reliance
    if not is_on or not any(unit.type == MCV for unit in world.units.values()):
        return False
    defenses = _home_strength(threat, world, ctx)
    if others == 0 and defenses < tuning.base_defense.escort_needed_ratio * threat.size:
        return False
    last = ctx.escort_kept_tick
    if last is None or world.tick - last >= ESCORT_KEPT_SECONDS * TICKS_PER_SECOND:
        ctx.escort_kept_tick = world.tick
        records.log(
            BASE_DEFENSE_EVENT,
            {"tick": world.tick, "reason": ESCORT_KEPT, "threat_size": threat.size, "others": others},
        )
    return True


def _engaged(responders: list[Unit], threat: Threat, world: World, tuning: BaseDefenseTuning) -> list[Unit]:
    """The responders that have arrived at the threatened cell or are in contact with an enemy in sight."""
    in_contact = {unit.id for unit in targeting.in_contact(responders, world)}
    return [unit for unit in responders if unit.id in in_contact or _has_arrived(unit, threat.cell, tuning)]


def _may_attack_from_here(unit_id: int, enemy: EnemyRecord, world: World) -> bool:
    """Whether a unit may be ordered to attack an enemy: any may but an artillery piece or a V2 the enemy stands
    beyond the weapon range of, which holds its place rather than walk after it."""
    unit = world.units.get(unit_id)
    if unit is None or unit.type not in SIEGE_TYPES:
        return True
    weapon_range = world.items.damage.get(unit.type).range
    return (enemy.x - unit.x) ** 2 + (enemy.y - unit.y) ** 2 <= weapon_range * weapon_range


def _may_answer(unit_id: int, threat: Threat, world: World, ctx: MatchContext) -> bool:
    """Whether a unit may answer a threat: any may but one walking a route to an ore field (see `route_walk`), which
    keeps to its walk as a scout keeps to its scouting, and a tank on a crush run, which keeps to its run; and of a raid
    of aircraft alone (see `Threat.is_air_raid`) only one whose weapon hits aircraft, as the damage table gives it, or
    one that flies itself: a ground unit that cannot hit them would only stand under them."""
    if ctx.groups.is_walking(unit_id) or unit_id in ctx.crush_run:
        return False
    if not threat.is_air_raid(ctx.tuning.base_defense):
        return True
    facts = world.items.damage.get(world.combat_units[unit_id].type)
    return facts.air or facts.dps_air > 0


def _recallable(threat: Threat, main_answers: bool, ctx: MatchContext) -> tuple[str, ...]:
    """The groups that are called home when the units answering a major threat are outweighed: main and the raid
    group; the raid group alone while main is left to its attack; none for a raid of aircraft alone, which the groups
    far out could not catch."""
    if threat.is_air_raid(ctx.tuning.base_defense):
        return ()
    return RECALLABLE_GROUPS if main_answers else _RECALLABLE_BUT_MAIN


def _main_answers(threat: Threat, world: World, ctx: MatchContext) -> bool:
    """Whether main may answer a major threat, near the base or called home: always with `keep_attack_army` off or
    under a plan that keeps main at home; under PRESS, STRIKE and FINISH only when the threat is worth more than the
    guard and the defenses near it, which hold the base on their own while main attacks. While the model commander
    commands main, under every plan, as under those three."""
    if ctx.main_committed:
        # main committed to the base trade once the armies crossed is never taken nor called home (see `cross_commit`).
        return False
    commander = ctx.commander
    is_commanded = commander is not None and commander.role == MAIN_ROLE
    is_attacking = ctx.tuning.base_defense.keep_attack_army and ctx.plan in ATTACK_PLANS
    if not (is_commanded or is_attacking):
        return True
    return threat.size > _home_strength(threat, world, ctx)


def _home_strength(threat: Threat, world: World, ctx: MatchContext) -> float:
    """What the home guard and the own standing defenses within threat_area_cells of the threat's cell are worth in a
    fight; with home_defenses_in_reach on, only the defenses that reach one of its enemies (see
    `_reaches_an_enemy`)."""
    tuning = ctx.tuning.base_defense
    guard = [world.combat_units[unit_id] for unit_id in ctx.groups.members(GUARD)]
    defenses = [
        building
        for building in world.buildings.values()
        if building.type in DEFENSE_TYPES
        and cells.is_within((building.x, building.y), threat.cell, tuning.threat_area_cells)
        and (not tuning.home_defenses_in_reach or _reaches_an_enemy(building, threat.enemies, world))
    ]
    return strength(guard, world) + strength(defenses, world)


def _reaches_an_enemy(defense: Building, enemies: Iterable[EnemyRecord], world: World) -> bool:
    """Whether a defense's weapon range and HOME_DEFENSE_REACH_MARGIN_CELLS reach an enemy: five defenses 8 cells off
    the enemies killing a power plant, out of their range, held the base for main by their cost alone and never
    fired."""
    reach = world.items.damage.get(defense.type).range + HOME_DEFENSE_REACH_MARGIN_CELLS
    return any(cells.distance((defense.x, defense.y), (enemy.x, enemy.y)) <= reach for enemy in enemies)


def _has_arrived(unit: Unit, cell: Cell, tuning: BaseDefenseTuning) -> bool:
    """Whether a unit stands idle on a cell already, so that sending it there is no use."""
    return unit.idle and cells.is_within((unit.x, unit.y), cell, tuning.arrived_cells)


def _is_same_aim(sent: Cell | int | None, aim: Cell | int, tuning: BaseDefenseTuning) -> bool:
    """Whether a unit sent where `sent` says has been sent where `aim` says: the same enemy, or about the same cell."""
    if isinstance(sent, tuple) and isinstance(aim, tuple):
        return cells.is_within(sent, aim, tuning.arrived_cells)
    return sent == aim


def _unit_order(cmd: str, unit_ids: list[int], cell: Cell) -> list[Intent]:
    """An emergency order of a command that sends units to a cell; none for no unit."""
    return [Intent(Priority.EMERGENCY, {"cmd": cmd, "units": unit_ids, "to": list(cell)})] if unit_ids else []


def _last_orders(unit_ids: set[int], ledger: OrderLedger) -> dict[int, dict[str, Any]]:
    """The last order in force that gave each of the units something to do, without its unit ids.

    A unit that was never given one is left out. The ledger keeps its orders
    in the order they were sent, so it is read from the newest back.
    """
    found: dict[int, dict[str, Any]] = {}
    for entry in reversed(ledger.entries):
        if len(found) == len(unit_ids):
            break
        if _is_in_force_activity(entry.order, entry.status):
            for unit_id in unit_ids.intersection(entry.order["units"]).difference(found):
                found[unit_id] = {key: value for key, value in entry.order.items() if key != "units"}
    return found


def _is_in_force_activity(order: Mapping[str, Any], status: str) -> bool:
    """Whether an order gave its units something to do in place of what they did, and was neither rejected nor lost."""
    return order["cmd"] in ACTIVITY_COMMANDS and not order.get("queued") and status not in (REJECTED, LOST)


class HarvesterFlight:
    """Sends a harvester with an enemy at its heels to shelter, and back to work once the enemy has gone; with
    `truck_retreat` on, a truck only from threats it cannot crush, away from them (see `truck_retreat`)."""

    def __init__(self) -> None:
        # Fleeing harvester id -> the last tick an enemy unit that fights was in sight near it.
        self._last_chased: dict[int, int] = {}
        # Fleeing harvester id -> the shelter it was sent to.
        self._shelter_of: dict[int, Cell] = {}
        self._retreat = truck_retreat.TruckRetreat()

    def intents(self, world: World, ctx: MatchContext, attacked: frozenset[int] = frozenset()) -> list[Intent]:
        """The moves to shelter and the returns to work of this cycle, the harvesters `attacked` in the threat in force
        threatened by their attackers wherever they stand with `truck_retreat` on; publishes which harvesters are
        fleeing."""
        if truck_retreat.is_on(ctx):
            return self._retreat.intents(world, ctx, attacked)
        tuning = ctx.tuning.base_defense
        enemy_cells = [_cell_of(enemy) for enemy in enemy_combat_units(world)]
        harvesters = [unit for unit in world.units.values() if unit.type == HARVESTER]
        chased = _within(harvesters, enemy_cells, tuning.harvester_flee_cells)
        near = _within(harvesters, enemy_cells, tuning.harvester_safe_cells)
        back = self._note_chases(
            [unit.id for unit in harvesters],
            [unit.id for unit in chased],
            [unit.id for unit in near],
            world.tick,
            tuning,
        )
        ctx.fleeing_harvesters = frozenset(self._last_chased)
        harvest = truck_shares.harvest_orders(back, world, ctx, Priority.EMERGENCY) if back else []
        return [*self._to_shelter(chased, world, tuning), *harvest]

    def _to_shelter(self, harvesters: list[Unit], world: World, tuning: BaseDefenseTuning) -> list[Intent]:
        """A `move` for each harvester to the own defense nearest to it, but those sent there or standing there."""
        defenses = [(building.x, building.y) for building in world.buildings.values() if building.type in DEFENSE_TYPES]
        shelters = defenses or _and_base_center(world, [])
        sent: dict[Cell, list[int]] = {}
        for unit in harvesters:
            cell = (unit.x, unit.y)
            shelter = min(shelters, key=lambda other: cells.distance(cell, other), default=None)
            if shelter is None or _is_same_aim(self._shelter_of.get(unit.id), shelter, tuning):
                continue
            self._shelter_of[unit.id] = shelter
            if not cells.is_within(cell, shelter, tuning.arrived_cells):
                sent.setdefault(shelter, []).append(unit.id)
        return [_unit_order("move", ids, shelter)[0] for shelter, ids in sent.items()]

    def _note_chases(
        self, alive: list[int], chased: list[int], near: list[int], tick: int, tuning: BaseDefenseTuning
    ) -> list[int]:
        """Note which harvesters an enemy is chasing or still near, and return those it has left long enough."""
        fleeing = {unit_id: self._last_chased[unit_id] for unit_id in alive if unit_id in self._last_chased}
        fleeing.update(dict.fromkeys([*chased, *(unit_id for unit_id in near if unit_id in fleeing)], tick))
        back = [unit_id for unit_id, last in fleeing.items() if tick - last >= tuning.harvester_return_ticks]
        self._last_chased = {unit_id: last for unit_id, last in fleeing.items() if unit_id not in back}
        self._shelter_of = _kept(self._shelter_of, self._last_chased)
        return back


def _kept(shelters: dict[int, Cell], fleeing: dict[int, int]) -> dict[int, Cell]:
    """The shelters of the harvesters that are still fleeing."""
    return {unit_id: cell for unit_id, cell in shelters.items() if unit_id in fleeing}


def _within(units: list[Unit], enemy_cells: list[Cell], reach: int) -> list[Unit]:
    """The units that have an enemy within `reach` cells."""
    return [unit for unit in units if _is_near_any((unit.x, unit.y), enemy_cells, reach)]


class CounterProduction:
    """What the base makes while a major threat lasts: counter infantry, and an extra defense now and then."""

    def __init__(self) -> None:
        # The class of enemy the threat in force is mostly worth, as the queue that makes it; kept while the
        # threat's enemies are out of sight.
        self._threat_class: str | None = None
        # The tick the last extra defense was ordered at.
        self._extra_defense_at: int | None = None
        # Whether an extra defense was ordered for the threat in force, which gets one at the most.
        self._is_threat_served = False
        # The reasons written of the threat in force's counters (left to the tank army, or lifted), once a threat each.
        self._logged: set[str] = set()
        # Whether the threat in force's counters are trained because the tank army's block is lifted, this cycle.
        self._is_lifted = False

    def intents(self, threat: Threat | None, world: World, ctx: MatchContext, holds_counters: bool) -> list[Intent]:
        """The orders that answer a major threat, but for counter infantry while `holds_counters` (see
        `harvester_response`) or while the tank army is left an infantry threat, the Infantry queue then given up;
        with no major threat in force, nothing, and the Infantry queue given up."""
        if threat is None:
            self._is_threat_served = False
            self._logged = set()
        if threat is None or not threat.is_major:
            ctx.claims.release(INFANTRY_QUEUE, MANAGER_NAME)
            self._threat_class = None
            return []
        self._threat_class = largest_class(threat.enemies, world) or self._threat_class
        trains_counters = not (holds_counters or self._leaves_to_tanks(threat, world, ctx))
        if trains_counters:
            ctx.claims.hold(INFANTRY_QUEUE, MANAGER_NAME)
        else:
            ctx.claims.release(INFANTRY_QUEUE, MANAGER_NAME)
        if self._threat_class is None:
            return []
        counters = _counters(self._threat_class, world, ctx, self._is_lifted) if trains_counters else []
        return [*counters, *self._extra_defense(threat, self._threat_class, world, ctx)]

    def _leaves_to_tanks(self, threat: Threat, world: World, ctx: MatchContext) -> bool:
        """Whether the threat in force is an infantry threat left to the tank army, with no counter trained for it (see
        `_has_tank_army`), but while the counter is lifted (`is_counter_lifted`). Writes the `base_defense` event of
        either the first time for a threat."""
        self._is_lifted = False
        if self._threat_class != INFANTRY_QUEUE or not _has_tank_army(world, ctx):
            return False
        self._is_lifted = is_counter_lifted(world, ctx)
        self._log_once(COUNTER_LIFTED if self._is_lifted else COUNTER_SKIPPED_TANKS, threat, world, ctx)
        return not self._is_lifted

    def _log_once(self, reason: str, threat: Threat, world: World, ctx: MatchContext) -> None:
        """Write the `base_defense` event of the threat's counters with `reason`, unless the threat had it written."""
        if reason in self._logged:
            return
        self._logged.add(reason)
        records.log(
            BASE_DEFENSE_EVENT,
            {
                "reason": reason,
                "tick": world.tick,
                "threat_cell": list(threat.cell),
                "threat_size": threat.size,
                "main_units": len(ctx.groups.members(MAIN)),
            },
        )

    def _extra_defense(self, threat: Threat, threat_class: str, world: World, ctx: MatchContext) -> list[Intent]:
        """The defense the threat calls for (see `_calls_for_defense`), when the Defense queue is free, none was
        ordered for the threat yet and none within extra_defense_seconds; its goal is the threat's ground enemy nearest
        the centre of the base, so that upkeep places it on the side of the base facing the threat.

        A power-hungry defense that would leave less than the power target over
        waits, and the power plant is ordered in its place.
        """
        production = ctx.production
        item = _extra_defense_item(threat_class, world.family)
        can_start = item in world.available_production and production.is_queue_free(DEFENSE_QUEUE)
        if self._is_threat_served or not (self._is_due(world, ctx) and can_start):
            return []
        facing = _calls_for_defense(threat, world, ctx.tuning.base_defense)
        if facing is None or not defense_cap.allows(item, EXTRA_DEFENSE_RULE, world, ctx, facing):
            return []
        if item in _POWER_HUNGRY_DEFENSES and not production.can_power(item, power_target(world, ctx)):
            return power_plant_when_free(world, ctx)
        self._extra_defense_at, self._is_threat_served = world.tick, True
        ctx.defense_goals[item] = facing
        return [production.build(item, Priority.EMERGENCY)]

    def _is_due(self, world: World, ctx: MatchContext) -> bool:
        """Whether extra_defense_seconds have passed since the last extra defense was ordered, or none was."""
        wait_ticks = ctx.tuning.base_defense.extra_defense_seconds * TICKS_PER_SECOND
        return self._extra_defense_at is None or world.tick - self._extra_defense_at >= wait_ticks


def _calls_for_defense(threat: Threat, world: World, tuning: BaseDefenseTuning) -> Cell | None:
    """The cell an extra defense faces when a threat calls for one, else None. It calls for one when its ground
    enemies hit an own building or stand within extra_defense_near_cells of a main-base building, and our ground
    strength within LOCAL_STRENGTH_CELLS of them is below extra_defense_strength_share times theirs: a threat that only
    hit harvesters far out, or only of aircraft, is for the army, and a defense is no use where the army there already
    wins."""
    ground = [enemy for enemy in threat.enemies if not world.items.damage.get(enemy.type).air]
    if not ground:
        return None
    has_hit_building = any(actor_id in world.buildings for actor_id in threat.attacked)
    if not (has_hit_building or _is_near_main_base(ground, world, tuning)):
        return None
    if _ground_strength_near(ground, world) >= tuning.extra_defense_strength_share * strength(ground, world):
        return None
    centre = world.base_center or threat.cell
    return min((_cell_of(enemy) for enemy in ground), key=lambda cell: cells.distance(cell, centre))


def _is_near_main_base(enemies: list[EnemyRecord], world: World, tuning: BaseDefenseTuning) -> bool:
    """Whether an enemy stands within extra_defense_near_cells of a main-base building: an own building within
    threat_area_cells of the centre of the base (any own building while the state names no centre)."""
    centre = world.base_center
    main = [
        (building.x, building.y)
        for building in world.buildings.values()
        if centre is None or cells.is_within((building.x, building.y), centre, tuning.threat_area_cells)
    ]
    return any(_is_near_any(_cell_of(enemy), main, tuning.extra_defense_near_cells) for enemy in enemies)


def _ground_strength_near(enemies: list[EnemyRecord], world: World) -> float:
    """What our units and defenses that fight on the ground within LOCAL_STRENGTH_CELLS of the enemies are worth."""
    near = [_cell_of(enemy) for enemy in enemies]
    own = [*world.combat_units.values(), *world.buildings.values()]
    fighting = [
        actor
        for actor in own
        if _fights_on_ground(actor.type, world) and _is_near_any((actor.x, actor.y), near, LOCAL_STRENGTH_CELLS)
    ]
    return strength(fighting, world)


def _fights_on_ground(item: str, world: World) -> bool:
    """Whether a unit or building fights on the ground: it does not fly and its weapon hits ground targets."""
    facts = world.items.damage.get(item)
    return not facts.air and any(dps > 0 for dps in facts.dps.values())


def largest_class(enemies: list[EnemyRecord], world: World) -> str | None:
    """Infantry or vehicles, as the queue that makes them: what the enemies are mostly worth; vehicles on a tie. None
    when none of them is either."""
    worth: Counter[str] = Counter()
    for enemy in enemies:
        facts = world.items.get(enemy.type)
        if facts is not None and facts.queue in _COUNTERS:
            worth[facts.queue] += facts.cost
    if not worth:
        return None
    return INFANTRY_QUEUE if _is_infantry_threat(worth) else VEHICLE_QUEUE


def _is_infantry_threat(worth: Counter[str]) -> bool:
    """Whether a threat is infantry-class, by what its enemies in sight that are infantry or vehicles are worth, by the
    queue that makes them: infantry is more than half of it. A mixed threat with vehicles at half or more is
    vehicle-class."""
    return 2 * worth[INFANTRY_QUEUE] > worth[INFANTRY_QUEUE] + worth[VEHICLE_QUEUE]


def _has_tank_army(world: World, ctx: MatchContext) -> bool:
    """Whether an infantry threat is left to the tank army: a war factory stands, the main force is tanks (tanks crush
    infantry), main holds COUNTER_MIN_MAIN_UNITS combat units or more, and no rush is told while main holds fewer
    than rush_min_tanks tanks; either exception lets the counter be trained."""
    return (
        ctx.production.standing(WAR_FACTORY) > 0
        and ctx.tuning.mix.main_force == TANKS
        and len(ctx.groups.members(MAIN)) >= COUNTER_MIN_MAIN_UNITS
        and not _is_rush_short_of_tanks(world, ctx)
    )


def _is_rush_short_of_tanks(world: World, ctx: MatchContext) -> bool:
    """Whether the rush watch says rush while main holds fewer than rush_min_tanks tanks."""
    if ctx.rush_verdict != RUSH:
        return False
    tanks = sum(1 for unit_id in ctx.groups.members(MAIN) if _is_tank(world.units[unit_id].type, world))
    return tanks < ctx.tuning.opening.rush_min_tanks


def _is_tank(unit_type: str, world: World) -> bool:
    """Whether a unit type is a tank by the item table's roles."""
    facts = world.items.get(unit_type)
    return facts is not None and TANK_ROLE in facts.roles


def _extra_defense_item(threat_class: str, family: str) -> str:
    """The defense a family builds against a threat of a class: its anti-infantry defense, or its anti-armor one."""
    names = FAMILIES[family]
    return names.anti_infantry_defense if threat_class == INFANTRY_QUEUE else names.anti_armor_defense


def _alarm_defense(world: World, ctx: MatchContext) -> list[Intent]:
    """While the canary alarm is in force (see `canary_alarm`), the defense it orders when the Defense queue is free:
    the one against the class the enemy combat units seen lately were mostly worth, the anti-armor one when none was
    seen, on offer, placed toward the alarm's goal cell; a Tesla coil that would leave less than the power target
    waits for the power plant, ordered first."""
    production = ctx.production
    if not ctx.canary_alarm or not production.is_queue_free(DEFENSE_QUEUE):
        return []
    threat_class = largest_class(canary_alarm.seen_enemies(world), world) or VEHICLE_QUEUE
    item = _extra_defense_item(threat_class, world.family)
    goal = canary_alarm.goal_cell(world, ctx)
    if item not in world.available_production or not defense_cap.allows(
        item, canary_alarm.CANARY_ALARM, world, ctx, goal
    ):
        return []
    if item in _POWER_HUNGRY_DEFENSES and not production.can_power(item, power_target(world, ctx)):
        return power_plant_when_free(world, ctx)
    ctx.defense_goals[item] = goal
    return [production.build(item, Priority.EMERGENCY)]


def _approach_defense(world: World, ctx: MatchContext, alarm: approach_alarm.ApproachAlarm) -> list[Intent]:
    """While the approach alarm is in force and the canary alarm, which orders a defense on every free Defense queue
    itself, is not (see `approach_alarm`): the defense it still calls for when the Defense queue is free, the
    anti-infantry one against an infantry-led group, else the anti-armor one, on offer, placed toward the alarm's goal
    cell; a Tesla coil that would leave less than the power target waits for the power plant, ordered first."""
    production = ctx.production
    if not ctx.approach_alarm or ctx.canary_alarm or not alarm.is_defense_due():
        return []
    if not production.is_queue_free(DEFENSE_QUEUE):
        return []
    item = _extra_defense_item(INFANTRY_QUEUE if ctx.approach_alarm_infantry else VEHICLE_QUEUE, world.family)
    goal = approach_alarm.goal_cell(world, ctx)
    if item not in world.available_production or not defense_cap.allows(item, APPROACH_RULE, world, ctx, goal):
        return []
    if item in _POWER_HUNGRY_DEFENSES and not production.can_power(item, power_target(world, ctx)):
        return power_plant_when_free(world, ctx)
    alarm.ordered += 1
    ctx.defense_goals[item] = goal
    return [production.build(item, Priority.EMERGENCY)]


def _counters(threat_class: str, world: World, ctx: MatchContext, is_lifted: bool) -> list[Intent]:
    """The training of the counter to a threat's class, so many at a time, when the Infantry queue is free: while the
    tank army's block `is_lifted`, the family's counter infantry (`lifted_infantry_counter`)."""
    production = ctx.production
    items = (lifted_infantry_counter(world),) if is_lifted else _COUNTERS[threat_class]
    item = next((item for item in items if item in world.available_production), None)
    if item is None or not production.is_queue_free(INFANTRY_QUEUE):
        return []
    return [production.train(item, ctx.tuning.base_defense.counter_at_a_time, Priority.EMERGENCY)]


def _repairs(threat: Threat | None, world: World, ctx: MatchContext) -> list[Intent]:
    """A `repair` on for each building under attack that is not being repaired, while 100 or more can be spent."""
    if threat is None or world.economy.spendable < ctx.tuning.base_defense.repair_min_cash:
        return []
    upkeep = ctx.tuning.upkeep
    in_force = repairs_on_in_force(ctx.ledger, world.tick, upkeep.repair_resend_ticks)
    attacked = [world.buildings[actor_id] for actor_id in threat.attacked if actor_id in world.buildings]
    return [
        Intent(Priority.EMERGENCY, {"cmd": "repair", "building": building.id, "on": True})
        for building in attacked
        if building.id not in in_force and _may_repair(building, upkeep.repair_stop_above_hp)
    ]


def _may_repair(building: Building, stop_above_hp: int) -> bool:
    """Whether repair may be switched on for a building: it is off, and upkeep would not switch it straight off."""
    return not building.repairing and building.hp <= stop_above_hp


def _is_ground_building_hit(threat: Threat | None, world: World) -> bool:
    """Whether the threat in force hit an own building in this cycle and is not of aircraft alone: a building shot from
    out of sight counts, since what outranges sight is artillery on the ground."""
    if threat is None or threat.aircraft_only is True:
        return False
    return any(actor_id in world.buildings for actor_id in threat.attacked)


def _level(threat: Threat | None) -> str:
    """How big a threat is, as the match context publishes it."""
    if threat is None:
        return NO_THREAT
    return MAJOR_THREAT if threat.is_major else MINOR_THREAT


class BaseDefenseManager:
    """Watches for attacks on the base and answers them. See the module's note."""

    name = MANAGER_NAME

    def __init__(self) -> None:
        self._watch = ThreatWatch()
        # One judgment of a far rescue for the threat in force, whichever answer would take main to it.
        self._far_rescue = FarRescue()
        self._harvester_response = HarvesterResponse(self._far_rescue)
        self._response = CombatResponse(self._far_rescue)
        self._flight = HarvesterFlight()
        self._production = CounterProduction()
        self._harass = HarassTowers()
        # A harvester run over enemy infantry at the base under a rush (see `harvester_crush`).
        self._harvester_crush = HarvesterCrush()
        # The money turned to defenses while the enemy's army heads for our base (see `canary_alarm`).
        self._alarm = canary_alarm.CanaryAlarm()
        # The defenses ordered while an enemy army is seen coming for the base (see `approach_alarm`).
        self._approach = approach_alarm.ApproachAlarm()

    @property
    def threat(self) -> Threat | None:
        """The threat in force, or None while the base is not under attack."""
        return self._watch.threat

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """Bring the threat up to date, record a vehicle attack, publish the threat's flag, size and cell and the tick
        of a ground hit on a building, answer it (main alone, for a harvester threat away from the base that
        `harvester_response` handles), and shelter the harvesters."""
        threat = self._watch.update(world, ctx.tuning.base_defense)
        if threat is None:
            self._far_rescue.forget()
        self._watch.record_vehicle_attack(world, ctx.vehicle_attacks)
        ctx.threat_active = threat is not None
        ctx.threat_level = _level(threat)
        ctx.threat_cell = None if threat is None else threat.cell
        ctx.threat_aircraft_only = threat is not None and threat.aircraft_only is True
        # A defense ordered toward a cell keeps its goal while it is on its way, until upkeep has placed it.
        ctx.defense_goals = {item: cell for item, cell in ctx.defense_goals.items() if ctx.production.pending(item)}
        if _is_ground_building_hit(threat, world):
            ctx.ground_hit_tick = world.tick
        self._alarm.follow(world, ctx)
        self._approach.follow(world, ctx)
        main_answer = None if ctx.main_committed else self._harvester_response.intents(threat, world, ctx)
        holds_counters = self._harvester_response.holds_counters(threat, world, ctx)
        return [
            *(self._response.intents(threat, world, ctx) if main_answer is None else main_answer),
            *self._flight.intents(world, ctx, frozenset(() if threat is None else threat.attacked)),
            *self._production.intents(threat, world, ctx, holds_counters),
            *_alarm_defense(world, ctx),
            *_approach_defense(world, ctx, self._approach),
            *self._harass.intents(world, ctx),
            *_repairs(threat, world, ctx),
            *self._harvester_crush.intents(world, ctx),
        ]
