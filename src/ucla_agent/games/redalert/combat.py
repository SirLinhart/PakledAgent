"""Combat: how the army, the group `main`, fights.

The combat manager runs after scouting and before the raids. It asks no model:
whether to fight is decided in every cycle on the estimate of the fight
(see `fight`), the ratio of the group's strength to the enemy's around it.

A group is in one of three modes. It engages when it is clearly the stronger,
at a ratio of 1.3 or more, and retreats to the staging point when it is
clearly the weaker, below 0.9; in between it keeps the mode it is in, so that
a fight that is about even does not have it turn back and forth. A new mode is
kept for at least five seconds, whatever the ratio does in between, except
that a group whose ratio falls below 0.5 retreats at once: waiting would cost
it. A group that has retreated and has no enemy near any more holds, at the
staging point, until something new happens. A group that starts the match
holds.

An engaged group with an enemy near spreads its fire over the enemies around
it, the units handed out by `targeting` in every cycle.

main gathers at the staging point, in front of the base on the enemy's side.
It goes forward only as a whole: when it is worth at least the strength the
plan in force asks for (`min_attack_strength`) and most of it, four units in
five, stands at the staging point. Then all of it is sent, by `attack_move`,
on the nearest remembered enemy building that no remembered defense covers,
or the enemy's start when no building is known. An army does not walk into
the fire of defenses it cannot beat: when every known building is covered, it
goes on the nearest only while it is clearly stronger (1.3 or more) than the
defenses that cover that building, each counted at twice its cost; otherwise
it stays at the staging point, or goes back there. A defense whose weapon
hits aircraft alone, the AA gun or the SAM site, covers nothing here. Units
made after main went forward do not trickle after it one by one, to be picked
off on the way: they wait at the staging point until they are worth 1,000
together, and then go after the army as one group, which joins it. An army
that retreats is main at the staging point again, the units that waited there
included.

Base defense comes first. A group it has under `defense_override` defends the
base and engages whatever the estimate says, and a group it has called home
is left to it entirely: the combat manager orders nothing for it until base
defense lets it go.

With `strike_near_expansion` of `[combat]` on, an enemy MCV or yard near our
buildings or harvested fields, or an enemy defense whose reach takes in a
refinery, is struck (see `strike`): the fewest of main's units gathered at
the staging point and nearest it, worth strike_min_ratio against it and the
enemy units in sight beside it and half the enemy army's estimate, leave
main on the strike, and take none of main's orders, nor the commander's,
until the target is destroyed or unseen for 30 seconds, their ratio there or
their own fight falls under strike_abandon_ratio, or base defense calls main
home or takes it over. While main has no such units, main as a whole makes
a target within far_rescue_cells of the base its objective under every
plan, going forward whatever strength the plan asks, and the target is the
commander's objective while the commander commands main; a target farther
off is not struck.

With `strike_overextended` of `[combat]` on, the same strike takes main,
gathered at the staging point, against an enemy refinery or harvesters at an
ore field far from the enemy's start (see `strike` and `overextended`), unless
the model commander's standing action for main is a pull back, a retreat home
or a hold, or its retreat override is on.

With `closing_intercept` of `[base_defense]` on, while the model commander
does not command main and the plan is HOLD or RAID, an enemy army worth 1,000
or more closing within 30 cells on an own yard, the travelling MCV or its
site, or base_center is met by main before base defense would see it (see
`closing_intercept`): main, gathered at the staging point and at least as
strong as the army, attack-moves 8 cells in front of the target toward it;
weaker, it defends a target near the base and holds at the staging point
beyond. Engaged with an enemy near, it fights where it stands. Once the
army is gone or away, or main's ratio there falls under
strike_abandon_ratio, main goes back to the staging point.

A tank that is nearly lost is worth saving even when its group fights on. A
unit that cost 700 or more and is down to a third of its health goes back to
the staging point on its own, unless its group is so much the stronger that
the fight will soon be over, or the enemy stands on its way back. It rejoins
main when it has been repaired to 60%, or after half a minute. With
`vehicle_repair` of `[combat]` on, this is for infantry alone: a vehicle of
main about to be lost goes into repair mode instead (see `repair`), none of
main's units until it rejoins; the moves of the vehicles in repair and of the
Allied support units (see `support`) are the combat manager's.

A retreat is an emergency order: it goes into the batch ahead of anything
else for those units.

FINISH is the attack that ends the match, and the match ends only when the
last enemy building is gone. Under it main goes on the remembered enemy
building nearest to it, defended or not, and when none is left, on any
remembered enemy MCV, which could build a new base. With `nuke_spread` of
`[combat]` on, a remembered enemy missile silo comes first. When neither is known it
sweeps the map for them: the enemy's start, every other start but its own,
then the centers of a 4-by-4 grid over the map, row by row, moving on from a
point once it is there or has stood still short of it for two seconds. On the
way it fights only the enemy units within 6 cells of its path; the rest are
left behind, since only buildings decide the match. With
`sticky_attack_target` of `[combat]` on, main under FINISH keeps one target
instead of the nearest in each cycle, chosen and dropped as `finish_target`
says.

PRESERVE keeps a lead to the end: nothing is risked. main does not go
forward, and every unit of it farther than 15 cells from the center of the
base is sent back to the staging point.

main's artillery and V2s are its siege subgroup, and the rest of it their
screen (see `siege`). While main advances or fights, the siege orders both:
the screen holds a line in front of the long-range units toward the
objective, or toward the enemy units it fights, and the long-range units
fire from their band behind it. main goes forward only when its screen is
worth enough for its long-range units; an army gone forward whose screen is
no longer worth enough comes back to the staging point. When main retreats,
its long-range units retreat with it, and under base defense's override main
fights as one group. With `mode` of `[siege]` at `frontal`, or with no
long-range unit, main fights and advances as one group.

With `forward_hold` of `[combat]` on, main under HOLD that is strong enough
gathers at a forward point on the line toward the enemy instead of at the
staging point (see `forward_hold`); its retreats still go to the staging
point. There it fights only the enemy units no remembered defense covers, and
a unit that has chased one too far from the point is sent back to it. It
falls back to the staging point, a retreat at once, below the retreat ratio
or when an enemy army stronger than it comes at the point.

While the model commander commands main (see `commander`), its standing
action (see `command_army`) is main's objective under every plan, in place
of the modes, the gathering, the advance, the sweep and the forward hold:
`attack` is an attack-move on the enemy group, `siege` the siege of the
defense by the long-range units and their screen, `flank` a move to the side
waypoint and then the march on the objective, `advance` the march on the
objective (with `march_when_clear` as it says), `pull back`, `retreat
home` and `gather` moves, every unit of main to the action's cell, and `hold`
a stop. A flank or an advance by a way through the graph of waypoints (with
`waypoints` of [routing] on, see `waypoints`) keeps the cells of that way
short of the objective as it was when the action was taken: main moves to
each in turn, on to the next once its centroid is within 3 cells of one, and
marches on the objective once none is left; taking such an action writes a
`route` event whose user is the action's kind.
Under attack, flank and advance an enemy near main is fought as an engaged
group fights it, the units handed out over the enemies or ordered by the
siege; a unit worth saving is still sent back on its own. Base defense's
override and recall come first, as always. A task or raid trucks pick of
the commander's sends a detachment out of main (see `command_detachments`):
its units take only the detachment's orders, are among the units sent
forward, and rejoin main when it ends; every detachment ends when the
commander's retreat override pulls main back or base defense calls main home
or takes it over, and the commander's order goes out again to take in the
units that rejoined. The rest of main keeps the standing action, or holds
while there is none, and the question is asked about the rest. A run-over
detachment out is the one crush run: no run of the script's starts meanwhile
(`MatchContext.run_over_out`). While the commander's forward gate is open,
the units joining main go to the staging point and then together to main's
centroid before they take main's orders (see `command_gate`).

With `crush_runs` of `[combat]` on, the agent's tanks run over a tight
cluster of enemy infantry near them instead of shooting it (see `crush`):
main's, called home or not, the guard's and those base defense has taken. The
run is followed before main's recall is looked at, and the tanks on it take no
other order of main's until it ends, but an emergency. The run's tanks are
published in every cycle (`MatchContext.crush_run`), so that no other
manager's order reaches them either.

With `nuke_spread` of `[combat]` on, the combat manager keeps the clock of
every enemy missile silo it has seen, which tells when a nuke is to be
expected (see `nuke`). From a minute before an expected nuke until 20
seconds after it, main does not gather in one clump: wherever it would gather
or stand idle (under PRESERVE, at the gather point under HOLD, at the staging
point, the units waiting there to reinforce the army) its units go to their
places in groups spread around that point instead, and a unit at its place
counts as gathered when main is to go forward. Under PRESERVE a unit far
from the base is sent to its place, whatever it does, rather than to the
staging point. A main that fights is not spread.

With `contact_focus` of `[combat]` on, a unit of main in a fight fires at
the enemy it hurts most whatever main as a whole is told (see `targeting`).
The commander's order, which goes into the batch ahead of every other, leaves
out the units given a targeted attack in the cycle and those in contact
(`targeting.is_in_contact`), so that it does not take them off their targets,
and is due again only when one of the units left in it stands idle. A retreat
keeps every unit and fires nothing: `pull back`, `retreat home`, the
`gather` the retreat override stands for, and the regroup gather after the
commander's forward gate closed for a regroup (see `commander`). Under
attack, flank, advance and
siege the units the focus fire gives no target keep the commander's order
rather than being sent after an enemy out of sight, and the units in contact
fight though no enemy is near main's centroid, but while the siege orders
main's screen and long-range units. Under the commander's hold and
gather, and wherever main gathers or holds by its plan (at the staging point,
at the forward point, the units waiting to reinforce the army, main kept home
under PRESERVE), the units in contact fire at the targets already within
their weapon range and are not sent forward; a hold's stop loses only the
units that fire. An army on its way to its objective by its plan, with no
long-range unit, fights with its units in contact though no enemy is near its
centroid; under FINISH only what stands on its way.

With `forward_staging` of `[combat]` on, main does not walk into the fight
at its objective as its units arrive. Setting out on an objective the enemy
holds, by its plan or under the commander's advance, attack beyond
near_attack_cells, flank on its last leg or siege, it stops at the last cell
of its way outside the enemy's reach and holds there, its units in contact
firing at what is within their weapon range, and goes into no fight from
there until most of it and all of its long-range units are there or the
time allowed has passed (see `forward_staging`). Base defense, the
commander's retreat override, the crush runs and the detachments come first.
While the staging cell stands, the units joining main meet at it, the
plan's reinforcements and the units joining main through the commander's
forward gate alike, and go on together from there.

With `defended_redirect` of `[combat]` on, main does not fight under enemy
defenses it cannot beat (see `defended`). While its objective, the cell of
the commander's forward action or the plan's objective, lies in the coverage
of known enemy ground defenses and main is worth less than
defended_engage_ratio there against them and the enemy units around it, main
sends no order into covered ground: it goes on an uncovered attack point
instead, shells a lone defense, or sieges from beyond the coverage with its
other units screening behind. A strike as a whole, base defense's override
and recall, the retreats and the crush runs come first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import partial
from typing import Callable

from ucla_agent.games.redalert import (
    cells,
    closing_intercept,
    command_army,
    command_detachments,
    coverage,
    crush,
    defended,
    finish_target,
    forward_hold,
    forward_staging,
    kite,
    marching,
    nuke,
    repair,
    siege,
    strike,
    targeting,
    waypoints,
)
from ucla_agent.games.redalert.batch import Intent, Order, Priority
from ucla_agent.games.redalert.command_army import (
    ADVANCE,
    ATTACK,
    FLANK,
    FORWARD_KINDS,
    GATHER,
    PULL_BACK,
    RETREAT_HOME,
    SIEGE,
)
from ucla_agent.games.redalert.command_army import HOLD as HOLD_ACTION
from ucla_agent.games.redalert.command_prompt import Action
from ucla_agent.games.redalert.commander import MAIN_ROLE, Commander
from ucla_agent.games.redalert.composition import FINISH, PRESERVE
from ucla_agent.games.redalert.family import MCV
from ucla_agent.games.redalert.fight import (
    Estimate,
    Point,
    centroid,
    estimate,
    ground_defenses,
    is_counted,
    is_near,
    strength,
    worth,
)
from ucla_agent.games.redalert.groups import GUARD, MAIN
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.tuning import SCREENED, CombatTuning, Tuning
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

MANAGER_NAME = "combat"
ENGAGE = "engage"
HOLD = "hold"
RETREAT = "retreat"
_FULL_HEALTH = 100
# The model commander's standing actions for main that hold it back from an over-extended strike (see `strike`).
HOLDING_BACK = frozenset({PULL_BACK, RETREAT_HOME, HOLD_ACTION})


@dataclass
class GroupMode:
    """The mode of one group and when it last changed. A group starts out holding."""

    mode: str = HOLD
    # The tick of the last change of mode; None while there was none.
    changed_tick: int | None = None

    def follow(self, found: Estimate | None, tick: int, tuning: CombatTuning) -> None:
        """Take the mode the cycle's estimate calls for, within the time a mode is kept; None is no enemy near."""
        if found is None:
            if self.mode == RETREAT and self._has_held(tick, tuning):
                self._change(HOLD, tick)
            return
        ratio = found.ratio
        if ratio < tuning.emergency_ratio:
            self._change(RETREAT, tick)
        elif self._has_held(tick, tuning):
            self._change(_called_for(ratio, self.mode, tuning), tick)

    def fall_back(self, tick: int) -> None:
        """Retreat at once, whatever time the mode has been kept: main falls back from the forward hold."""
        self._change(RETREAT, tick)

    def _has_held(self, tick: int, tuning: CombatTuning) -> bool:
        """Whether the mode has been kept long enough to change it."""
        return self.changed_tick is None or tick - self.changed_tick >= tuning.mode_hold_ticks

    def _change(self, mode: str, tick: int) -> None:
        if mode != self.mode:
            self.mode, self.changed_tick = mode, tick


def _called_for(ratio: float, mode: str, tuning: CombatTuning) -> str:
    """The mode a ratio calls for: engage when clearly stronger, retreat when clearly weaker, else the mode in force."""
    if ratio >= tuning.engage_ratio:
        return ENGAGE
    if ratio < tuning.retreat_ratio:
        return RETREAT
    return mode


class Withdrawals:
    """The badly damaged expensive units of a group sent back to the staging point on their own."""

    def __init__(self) -> None:
        # Unit id -> the tick it was sent back.
        self._since: dict[int, int] = {}

    @property
    def units(self) -> frozenset[int]:
        """The units sent back that have not rejoined their group."""
        return frozenset(self._since)

    def keep_up(self, group: dict[int, Unit], tick: int, tuning: CombatTuning) -> None:
        """Let rejoin the units healed enough or away long enough; forget those no longer in the group."""
        self._since = {
            unit_id: since
            for unit_id, since in self._since.items()
            if unit_id in group and group[unit_id].hp < tuning.rejoin_hp and tick - since < tuning.rejoin_after_ticks
        }

    def send_back(
        self, units: list[Unit], found: Estimate | None, point: Cell, world: World, tuning: Tuning
    ) -> list[Intent]:
        """An emergency `move` to the staging point for the units of a group that should not stay in its fight."""
        if found is None or found.ratio >= tuning.combat.withdraw_below_ratio:
            return []
        hostile = [(record.x, record.y) for record in world.enemy_memory.values() if is_counted(record)]
        leaving = [unit.id for unit in units if _should_leave(unit, point, hostile, world, tuning.combat)]
        self._since.update(dict.fromkeys(leaving, world.tick))
        return [Intent(Priority.EMERGENCY, {"cmd": "move", "units": leaving, "to": list(point)})] if leaving else []


def _should_leave(unit: Unit, point: Cell, hostile: list[Cell], world: World, tuning: CombatTuning) -> bool:
    """Whether a unit is worth saving, badly damaged, and has a way back clear of the enemy for its first cells; with
    vehicle_repair on, never a vehicle, which goes into repair mode instead (see `repair`)."""
    if tuning.vehicle_repair and repair.is_vehicle(unit.type, world):
        return False
    if worth(unit.type, _FULL_HEALTH, world) < tuning.withdraw_min_cost or unit.hp >= tuning.withdraw_below_hp:
        return False
    way_back = cells.line((unit.x, unit.y), point)[1 : 1 + tuning.withdraw_path_cells]
    return not any(cells.is_within(cell, enemy, tuning.withdraw_clear_cells) for cell in way_back for enemy in hostile)


class Sweep:
    """Where main looks for the enemy under FINISH while no enemy building or MCV is known, and how far it has got."""

    def __init__(self) -> None:
        # The position of the point the army is going to, in the order the points are visited.
        self._index = 0
        # The tick the army set out for that point; None while it is not sweeping.
        self._leg_tick: int | None = None

    def current(self, world: World, tuning: CombatTuning) -> Cell:
        """The point the sweep is at; after the last point it starts over from the first."""
        points = sweep_points(world, tuning.sweep_grid_size)
        return points[self._index % len(points)]

    def follow(self, army: list[Unit], world: World, tuning: CombatTuning) -> None:
        """Move on to the next point once the army is at the one it is going to, or has stood still short of it."""
        tick = world.tick
        if self._leg_tick is None:
            self._leg_tick = tick
        point = self.current(world, tuning)
        is_there = is_near(centroid(army), point[0], point[1], tuning.sweep_reached_cells)
        has_stopped = all(unit.idle for unit in army) and tick - self._leg_tick >= tuning.sweep_patience_ticks
        if is_there or has_stopped:
            self._index += 1
            self._leg_tick = tick

    def pause(self) -> None:
        """Stop sweeping for a while: there is something to attack. The sweep goes on later from where it was."""
        self._leg_tick = None


def sweep_points(world: World, grid_size: int) -> list[Cell]:
    """The points a sweep visits, in order: the enemy's start, the other starts but the agent's own, then a grid.

    The grid is `grid_size` by `grid_size` cells of the map's playable part,
    whose centers are visited row by row from the top left.
    """
    own_start = min(world.spawns, key=lambda spawn: cells.distance(spawn, world.base_center), default=None)
    others = [spawn for spawn in world.spawns if spawn not in (world.enemy_base, own_start)]
    left, top, width, height = world.map_bounds
    grid = [
        (left + (2 * column + 1) * width // (2 * grid_size), top + (2 * row + 1) * height // (2 * grid_size))
        for row in range(grid_size)
        for column in range(grid_size)
    ]
    return [world.enemy_base, *others, *grid]


class CombatManager:
    """Directs `main` by the estimate of its fight. See the module's note."""

    name = MANAGER_NAME

    def __init__(self) -> None:
        self._main = GroupMode()
        self._is_overridden = False
        # The units of main sent forward; empty while main waits at the staging point.
        self._army: set[int] = set()
        self._withdrawals = Withdrawals()
        # main's vehicles in repair mode, and the support units that repair them.
        self._repair = repair.VehicleRepair()
        self._sweep = Sweep()
        # The target main keeps under FINISH with sticky_attack_target on; None while it has none.
        self._finish_target: finish_target.Kept | None = None
        self._siege = siege.Siege()
        self._march_watch = marching.MarchWatch()
        self._hold = forward_hold.ForwardHold()
        self._staging = forward_staging.ForwardStaging()
        self._crush = crush.CrushRuns()
        self._nuke = nuke.NukeWatch()
        # The units of main sent after an enemy from the forward point in the latest cycle.
        self._chasing: set[int] = set()
        # The estimate of main's fight in the latest cycle; None when there was no enemy near or no main.
        self.estimate: Estimate | None = None
        # The model commander's standing action for main, and whether main has passed the side waypoint of a flank;
        # None while the commander gives main none. And the cells of a flank's way through the graph of waypoints main
        # has still to pass, short of the objective; none for any other action.
        self._commanded: Action | None = None
        self._flanked = False
        self._flank_legs: list[Cell] = []
        # The units of main the commander's task and raid picks send out on a job of their own.
        self._detached = command_detachments.Detachments()
        # The strike on an enemy expansion or defense near us, whose units take none of main's orders.
        self._strike = strike.Strike()
        # main's answer to an enemy army closing on a yard, the travelling MCV or its site, or the base.
        self._intercept = closing_intercept.ClosingIntercept()
        # main's verdict while its objective is defended beyond what it can beat (see `defended`).
        self._defended = defended.DefendedWatch()
        # The one order the standing action stands for in the latest cycle, for main's units nearest its target
        # first; None while there is none to give.
        self.commanded_order: Order | None = None

    @property
    def mode(self) -> str:
        """main's mode in the latest cycle: engage while base defense overrides the estimate."""
        return ENGAGE if self._is_overridden else self._main.mode

    def command(self, action: Action | None) -> None:
        """Take the model commander's standing action for main (see `command_army`), which is main's objective from
        this cycle on in place of its plan-driven movement; None for none. A flank that is new starts at its side
        waypoint, or at the first cell of its way through the graph of waypoints."""
        if self._is_new(action):
            self._flanked = False
            self._flank_legs = [] if action is None or action.route is None else list(action.route.legs[:-1])
        self._commanded = action

    def _is_new(self, action: Action | None) -> bool:
        """Whether an action is another than the standing one: none, or of another key."""
        return action is None or self._commanded is None or action.key != self._commanded.key

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The orders for main in this cycle, and the crush run's, which may take any own tank (see `crush`); none of
        main's while base defense has called it home."""
        groups = ctx.groups
        self.estimate, self._is_overridden, self.commanded_order = None, groups.has_defense_override(MAIN), None
        members = self._members(world, ctx)
        ctx.repairing, ctx.withdrawn = self._repair.units, self._withdrawals.units
        ctx.detachments = self._detached.out
        self._army &= members.keys()
        self._chasing = set()
        self._nuke.follow(world, ctx.tuning.combat)
        self._staging.begin_cycle()
        point = cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells)
        struck = self._follow_strike(members, point, world, ctx)
        if point is not None and members:
            self._ask_commander(members, world, ctx)
        unstruck = {unit_id: unit for unit_id, unit in members.items() if unit_id not in self._strike.units}
        self._follow_detachments(unstruck, world, ctx)
        rest = self._without_joiners(self._without_detached(members), ctx)
        closing_orders = self._follow_intercept(rest, point, world, ctx)
        tanks = self._crush_tanks(rest, world, ctx)
        runs = self._crush.follow(tanks, world, ctx.tuning, ctx.terrain, ctx.run_over_out)
        orders = [] if point is None or not rest or groups.is_recalled(MAIN) else self._orders(rest, point, world, ctx)
        self._staging.end_cycle()
        ctx.crush_run = self._crush.held(orders)
        detached_orders = self._detached.orders(members, world, ctx)
        strike_orders = [*struck, *self._strike.orders(members, world, ctx), *closing_orders]
        join_orders = [] if groups.is_recalled(MAIN) else self._join_orders(world, ctx)
        ctx.army = frozenset(self._army | self._chasing | self._away)
        ctx.gather_point = self._hold.cell
        ctx.combat_held = self._strike.units | self._repair.units
        # The moves of the vehicles in repair mode and of the support units go after main's (see `repair`).
        orders = [*orders, *self._repair.orders(list(members.values()), world, ctx)]
        intents = [*self._commander_intent(world, ctx), *detached_orders, *strike_orders, *join_orders, *runs, *orders]
        # main's idle infantry standing on one cell with another of ours is moved apart, none while base defense has
        # called main home (see `spread`).
        stacked = {} if self._is_overridden else members
        return intents + ctx.infantry_spread.destacks(stacked, intents, world, ctx)

    def _members(self, world: World, ctx: MatchContext) -> dict[int, Unit]:
        """main's units in this cycle by id, but those base defense took on their own, those sent back on their own and
        the vehicles in repair mode (see `repair`), whose rejoining is brought up to date first."""
        groups = ctx.groups
        # A unit base defense took on its own is base defense's while the threat lasts: the rest of main is the plan's.
        main = {
            unit_id: world.combat_units[unit_id]
            for unit_id in groups.members(MAIN)
            if self._is_overridden or not groups.is_overridden(unit_id)
        }
        self._withdrawals.keep_up(main, world.tick, ctx.tuning.combat)
        main = {unit_id: unit for unit_id, unit in main.items() if unit_id not in self._withdrawals.units}
        self._repair.follow(main, self._crush.units | self._away, world, ctx)
        return {unit_id: unit for unit_id, unit in main.items() if unit_id not in self._repair.units}

    def _crush_tanks(self, rest: dict[int, Unit], world: World, ctx: MatchContext) -> list[Unit]:
        """The own units a crush run may take (see `crush`): main's but the detachments' and the joiners' (`rest`),
        then the guard's and every other unit base defense has under its override, main's among them; none sent back
        on its own or walking a route."""
        groups, withdrawn = ctx.groups, self._withdrawals.units
        others = [
            unit
            for unit_id, unit in world.combat_units.items()
            if unit_id not in rest
            and unit_id not in withdrawn
            and not groups.is_walking(unit_id)
            and (groups.group_of(unit_id) == GUARD or groups.is_overridden(unit_id))
        ]
        return [*rest.values(), *others]

    def _ask_commander(self, members: dict[int, Unit], world: World, ctx: MatchContext) -> None:
        """While the model commander commands main: main's question of the cycle put about its units but the
        detachments' (none of them offered while detachments_max are out, nor on an enemy a detachment is on), the
        answer that arrived taken, and the standing action made main's objective (see `command_army`); nothing while
        the detachments hold every unit of main."""
        commander = ctx.commander
        units = list(self._without_detached(members).values())
        if commander is None or commander.role != MAIN_ROLE or not units:
            return
        detached = self._detached
        may_detach = detached.count < ctx.tuning.commander.detachments_max
        standing = commander.command_main(units, self._next_leg(), world, ctx, self._away, may_detach, detached.targets)
        if standing is not None and standing.route is not None and self._is_new(standing):
            waypoints.log_route(standing.kind, standing.route, world.tick)
        self.command(standing)

    def _next_leg(self) -> Cell | None:
        """The cell main is on its way to by the legs of its standing action while it has not passed them; None
        otherwise."""
        action = self._commanded
        if action is None or action.via is None or self._flanked:
            return None
        return self._commanded_target(action)

    def _follow_detachments(self, members: dict[int, Unit], world: World, ctx: MatchContext) -> None:
        """Bring the detachments up to date with the commander's task pick (see `command_detachments`): every one
        ends when the commander's retreat override or base defense takes main back; the commander's order goes out
        again when a detachment's units rejoin main; a run-over detachment out is the one crush run
        (`MatchContext.run_over_out`); and while one is out, the rest of main holds when the commander gives it no
        standing action."""
        commander = ctx.commander
        if commander is None:
            return
        pick = commander.task
        if self._detached.follow(pick, members, self._taken_back(commander, ctx), world, ctx):
            commander.resend()
        ctx.run_over_out = self._detached.is_running_over
        if self._detached.units and self._commanded is None:
            self.command(command_army.hold_action())

    def _taken_back(self, commander: Commander, ctx: MatchContext) -> str | None:
        """Why main is taken back from its detachments in this cycle: the commander's retreat override pulls it back,
        or base defense has called it home or taken it over; None while neither holds."""
        if commander.retreat.is_active:
            return command_detachments.PULLED_BACK
        if self._is_overridden or ctx.groups.is_recalled(MAIN):
            return command_detachments.RECALLED
        return None

    def _without_detached(self, members: dict[int, Unit]) -> dict[int, Unit]:
        """main's units but the detachments' and the strike force's."""
        away = self._away
        return {unit_id: unit for unit_id, unit in members.items() if unit_id not in away}

    @property
    def _away(self) -> frozenset[int]:
        """main's units out of it on a job of their own: the detachments' and the strike force's."""
        return self._detached.units | self._strike.units

    def _follow_strike(
        self, members: dict[int, Unit], point: Cell | None, world: World, ctx: MatchContext
    ) -> list[Intent]:
        """Bring the strike on an enemy expansion or defense near us, or on an over-extended field, up to date (see
        `strike`) with main's units on no detachment of the commander's, ended early when base defense calls main home
        or takes it over, and no over-extended strike started while the model commander holds main back
        (`_held_back`); the moves of the units of a strike abandoned (main's, for main as a whole), or of an
        over-extended strike that ended, in this cycle. The target of a strike with no strike force is published as main's objective
        (`MatchContext.strike_target`); once main as a whole strikes no more, it is main at the staging point again,
        which sets out afresh as its plan says. Followed whether the staging point `point` is known or not, so that a
        strike whose target is gone ends in any cycle."""
        detached = self._detached.units
        free = {unit_id: unit for unit_id, unit in members.items() if unit_id not in detached}
        taken_back = self._is_overridden or ctx.groups.is_recalled(MAIN)
        was_striking = ctx.strike_target is not None
        struck = self._strike.follow(free, taken_back, point, world, ctx, held_back=self._held_back(ctx))
        ctx.strike_target = self._strike.objective(world)
        if was_striking and ctx.strike_target is None:
            self._army = set()
        return struck

    def _held_back(self, ctx: MatchContext) -> bool:
        """Whether the model commander holds main back from an over-extended strike: its standing action for main is a
        pull back, a retreat home or a hold, or its retreat override is on."""
        commander, action = ctx.commander, self._commanded
        if commander is None:
            return False
        return commander.retreat.is_active or (action is not None and action.kind in HOLDING_BACK)

    def _follow_intercept(
        self, rest: dict[int, Unit], point: Cell | None, world: World, ctx: MatchContext
    ) -> list[Intent]:
        """Bring main's answer to an enemy army closing up to date (see `closing_intercept`) with its units on no
        detachment, strike or join (`rest`): ended when a strike is in force, base defense calls main home or takes it
        over, or the model commander commands main. The emergency move to the staging point of main's units once it
        ends in this cycle but by a recall; none otherwise, and none while the point is not known. Where the travelling
        MCV holds meanwhile is published as `MatchContext.mcv_halt`."""
        commander = ctx.commander
        blocked = (
            self._is_overridden
            or ctx.groups.is_recalled(MAIN)
            or self._strike.target_id is not None
            or (commander is not None and commander.role == MAIN_ROLE)
        )
        outcome = self._intercept.follow(rest, point, blocked, world, ctx)
        ctx.mcv_halt = self._intercept.mcv_halt
        if outcome is None:
            return []
        self._army = set()
        if outcome == closing_intercept.RECALLED or point is None:
            return []
        return _to_staging(list(rest.values()), point, ctx.tuning.combat)

    @staticmethod
    def _without_joiners(members: dict[int, Unit], ctx: MatchContext) -> dict[int, Unit]:
        """main's units but those joining it while the commander's forward gate is open (see `command_gate`)."""
        commander = ctx.commander
        joining = frozenset() if commander is None else commander.gate.joiners.ids
        return {unit_id: unit for unit_id, unit in members.items() if unit_id not in joining}

    def _join_orders(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The moves of the units joining main while the commander's forward gate is open, but the detachments', to
        main's staging cell while it has one (see `forward_staging`)."""
        commander = ctx.commander
        if commander is None:
            return []
        return commander.gate.joiners.orders(self._away, world, ctx, self._staging.cell)

    def _commander_intent(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The commander's order for main in its slot, when the standing action was carried out in this cycle and the
        order is due; none otherwise, as while base defense has main."""
        commander = ctx.commander
        intent = None if commander is None else commander.order(MAIN_ROLE, self.commanded_order, world)
        return [] if intent is None else [intent]

    @property
    def withdrawn(self) -> frozenset[int]:
        """The units of main on their way back on their own, or waiting at the staging point to rejoin."""
        return self._withdrawals.units

    @property
    def repairing(self) -> frozenset[int]:
        """main's vehicles in repair mode (see `repair`), none of main's units while they are."""
        return self._repair.units

    def _orders(self, members: dict[int, Unit], point: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """main's orders by its mode, but for the tanks on the crush run, which take no other order of main's but an
        emergency, the model commander's included (see `crush`)."""
        orders = self._main_orders(members, point, world, ctx)
        running = self._crush.units
        self.commanded_order = crush.order_without(self.commanded_order, running)
        return crush.without(running, orders)

    def _main_orders(self, members: dict[int, Unit], point: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """main's orders by its mode, and while it does not retreat, to gather, advance or reinforce."""
        tuning = ctx.tuning
        army, _ = self._split(members)
        self.estimate = estimate(army, world, tuning)
        if self._is_overridden:
            return [] if self.estimate is None else self._focus_orders(army, self.estimate.centroid, world, ctx)
        if self._intercept.response is not None:
            return self._intercepting(list(members.values()), point, world, ctx)
        if self._commanded is not None:
            return self._commanded_orders(members, point, world, ctx)
        self._main.follow(self.estimate, world.tick, tuning.combat)
        gather = self._gather_point(members, point, world, ctx)
        if self._main.mode == RETREAT:
            self._army = set()
            return _to_staging(list(members.values()), point, tuning.combat)
        withdrawals = self._withdrawals.send_back(army, self.estimate, point, world, tuning)
        gone = self._withdrawals.units
        self._army -= gone
        army, waiting = self._split({unit_id: unit for unit_id, unit in members.items() if unit_id not in gone})
        return [*withdrawals, *self._movement(army, waiting, point, gather, world, ctx)] if army else withdrawals

    def _intercepting(self, units: list[Unit], point: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """main on its answer to an enemy army closing (see `closing_intercept`): engaged with an enemy near, it fights
        where it stands, sent after no enemy while it holds; otherwise it attack-moves to the intercept cell or to the
        target it defends, or holds at the staging point."""
        response = self._intercept.response
        tuning = ctx.tuning.combat
        self._main.follow(self.estimate, world.tick, tuning)
        is_holding = response.kind == closing_intercept.HOLD_AT_STAGING
        self._army = set() if is_holding else {unit.id for unit in units}
        if self._main.mode == ENGAGE and self.estimate is not None:
            return self._fight(units, world, ctx, response.cell, chase=not is_holding)
        if is_holding:
            return _to_staging(units, point, tuning, Priority.MOVE)
        return _march(units, response.cell, ctx)

    def _commanded_orders(self, members: dict[int, Unit], point: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """main's orders under the commander's standing action: a unit worth saving sent back on its own as ever, and
        every other unit as the action says, or as `defended` says while a forward action's cell is defended beyond
        what main can beat."""
        units = list(members.values())
        withdrawals = self._withdrawals.send_back(units, self.estimate, point, world, ctx.tuning)
        gone = self._withdrawals.units
        commanded = [unit for unit in units if unit.id not in gone]
        action = self._commanded
        self._army = {unit.id for unit in commanded} if action.kind in FORWARD_KINDS else set()
        if not commanded:
            return withdrawals
        self._pass_flank_waypoint(action, commanded, ctx)
        aim = action.cell if action.kind in FORWARD_KINDS else None
        defended_orders = self._defended_orders(commanded, aim, world, ctx)
        if defended_orders is not None:
            return [*withdrawals, *defended_orders]
        held = self._commanded_staging(action, commanded, world, ctx)
        if held is not None:
            return [*withdrawals, *self._hold_at(commanded, held, world, ctx)]
        ordered = self._ordered_units(action, commanded, ctx)
        self.commanded_order = _group_order(action, self._commanded_target(action), ordered, ctx)
        return [*withdrawals, *self._carry_out(action, commanded, world, ctx)]

    def _pass_flank_waypoint(self, action: Action, units: list[Unit], ctx: MatchContext) -> None:
        """Note when main on a flank has reached its side waypoint, or on a flank or an advance the last cell of its way
        through the graph of waypoints: from then on it goes on the objective. A cell of the way is passed once main's
        centroid is within `waypoints.REACHED_CELLS` of it, or once every unit stands idle within gathered_cells of it,
        which no order moves on (`_not_there`)."""
        if action.via is None:
            return
        middle, tuning = centroid(units), ctx.tuning.combat
        if self._flank_legs:
            leg = self._flank_legs[0]
            if is_near(middle, leg[0], leg[1], waypoints.REACHED_CELLS) or not _not_there(units, leg, tuning):
                self._flank_legs.pop(0)
            self._flanked = not self._flank_legs
        elif is_near(middle, action.via[0], action.via[1], tuning.gathered_cells):
            self._flanked = True

    def _commanded_staging(self, action: Action, units: list[Unit], world: World, ctx: MatchContext) -> Cell | None:
        """The staging cell main holds at under a standing action (see `forward_staging`): an advance, a flank on its
        last leg, a siege, or an attack on a group farther than near_attack_cells of `[commander]` from main's centroid
        when it sets out; on the way to the cell the action sends main to now. The objective is the action while the
        commander's forward gate stays as it is. None while main goes on as the action says."""
        if not ctx.tuning.combat.forward_staging or not self._stages(action):
            return None
        commander = ctx.commander
        objective = (action.key, commander is not None and commander.gate.is_open)
        reach = ctx.tuning.commander.near_attack_cells
        may_start = action.kind != ATTACK or not is_near(centroid(units), action.cell[0], action.cell[1], reach)
        target = self._commanded_target(action)
        return self._staging.follow(objective, units, target, action.cell, may_start, world, ctx)

    def _stages(self, action: Action) -> bool:
        """Whether main stages on its way under a standing action: an advance, an attack, a siege, or a flank once it
        is on its last leg."""
        return action.kind in (ADVANCE, ATTACK, SIEGE) or (action.kind == FLANK and self._marches(action))

    def _commanded_target(self, action: Action) -> Cell | None:
        """Where the standing action sends main now: the next cell of its way not yet passed, else a flank's side
        waypoint while it is not passed, else the action's cell."""
        if action.via is None or self._flanked:
            return action.cell
        return self._flank_legs[0] if self._flank_legs else action.via

    def _marches(self, action: Action) -> bool:
        """Whether main marches on the objective under a standing action: an advance or a flank, past the cells of its
        way when it has them."""
        return action.kind in (ADVANCE, FLANK) and (action.via is None or self._flanked)

    def _ordered_units(self, action: Action, units: list[Unit], ctx: MatchContext) -> list[Unit]:
        """The units a standing action's one order is for: all of main's, but its long-range units while a siege or a
        march has them fire from their band, which the siege orders as before."""
        if action.kind != SIEGE and not self._marches(action):
            return units
        long_range, screen = _subgroups(units, ctx)
        return screen if long_range else units

    def _carry_out(self, action: Action, units: list[Unit], world: World, ctx: MatchContext) -> list[Intent]:
        """The orders that carry out a standing action: an enemy near fought under attack, flank and advance, a siege
        by the long-range units, the march on the objective, or the action's one order. With contact_focus on, the
        units in contact fight under a forward action though no enemy is near main's centroid, the fight sends no unit
        after an enemy out of sight, and the commander's order leaves out the units engaged, but under a retreat."""
        contact_focus = ctx.tuning.combat.contact_focus
        if action.kind in FORWARD_KINDS and self.estimate is not None:
            fight = self._fight(units, world, ctx, action.cell, chase=not contact_focus)
            self._leave_engaged(action, units, fight, world, ctx)
            return fight
        fire = self._contact_fire(action, units, world, ctx) if contact_focus else []
        self._leave_engaged(action, units, fire, world, ctx)
        long_range, screen = _subgroups(units, ctx)
        if action.kind == SIEGE and long_range:
            return [*fire, *self._siege.orders(long_range, screen, action.cell, True, world, ctx.tuning)]
        if self._marches(action):
            return _fire_then(fire, self._march(units, action.cell, world, ctx))
        return [*fire, *([] if self.commanded_order is None else [Intent(Priority.MOVE, self.commanded_order)])]

    def _contact_fire(self, action: Action, units: list[Unit], world: World, ctx: MatchContext) -> list[Intent]:
        """The focus fire of the units in contact under a forward action, which sends none of them after an enemy, but
        while the siege orders main; under hold and a gather that is no retreat, on the targets within their weapon
        range alone; none under a retreat."""
        if action.kind in (HOLD_ACTION, GATHER) and not _is_retreat(action, ctx):
            return self._hold_fire_of(units, world, ctx)[0]
        if action.kind not in FORWARD_KINDS:
            return []
        long_range, _ = _subgroups(units, ctx)
        if long_range and (action.kind == SIEGE or self._marches(action)):
            # The siege orders the screen's fights as well as the long-range units' fire.
            return []
        engaged = targeting.in_contact(units, world)
        return self._focus_orders(engaged, centroid(engaged), world, ctx, chase=False) if engaged else []

    def _leave_engaged(
        self, action: Action, units: list[Unit], intents: list[Intent], world: World, ctx: MatchContext
    ) -> None:
        """With contact_focus on, take out of the commander's order the units given a targeted attack among `intents`
        and the units in contact, unless the action is a retreat, which keeps every unit. A hold's stop loses only the
        units given an attack: it holds the others in contact where they stand."""
        if not ctx.tuning.combat.contact_focus or _is_retreat(action, ctx):
            return
        in_contact = [] if action.kind == HOLD_ACTION else units
        # An artillery piece or a V2 stepping back from an enemy come close is out of the order as well (see `kite`).
        left_out = targeting.engaged(in_contact, intents, world) | kite.stepping(intents)
        self.commanded_order = crush.order_without(self.commanded_order, left_out)

    def _gather_point(self, members: dict[int, Unit], point: Cell, world: World, ctx: MatchContext) -> Cell:
        """Where main gathers in this cycle: the staging point, or with the forward hold on where the hold says; main
        retreats at once when it falls back from the forward point."""
        if not ctx.tuning.combat.forward_hold:
            return point
        if self._hold.follow(list(members.values()), self.estimate, point, world, ctx):
            self._main.fall_back(world.tick)
        return self._hold.cell

    def _split(self, members: dict[int, Unit]) -> tuple[list[Unit], list[Unit]]:
        """main as its army and the units waiting at the staging point; all of it is the army while it waits there.

        Once main has gone forward, the fight is the army's; the units made since wait at the staging point.
        """
        army = [unit for unit_id, unit in members.items() if unit_id in self._army] or list(members.values())
        return army, [unit for unit_id, unit in members.items() if unit_id not in self._army]

    def _movement(
        self, army: list[Unit], waiting: list[Unit], point: Cell, gather: Cell, world: World, ctx: MatchContext
    ) -> list[Intent]:
        """The orders of a main that does not retreat: to gather at `gather` or go forward, and to send the
        reinforcements. main as a whole striking a must-kill target goes on it under every plan (see `strike`)."""
        striking = ctx.strike_target is not None
        if ctx.plan == PRESERVE and not striking:
            return self._preserve([*army, *waiting], point, world, ctx)
        if self._army and self._hold.is_under_hold and not striking:
            # The forward hold sends main nowhere under HOLD: an army gone forward under another plan comes back.
            self._army = set()
            return _to_staging([*army, *waiting], gather, ctx.tuning.combat, Priority.MOVE)
        objective = self._objective(army, world, ctx)
        # An engaged group with an enemy near fights where it is: it is neither gathered nor marched off.
        is_fighting = self._is_fighting(army, objective, ctx)
        if not self._army:
            if self._hold.is_forward and not striking:
                return self._hold_forward(army, is_fighting, gather, world, ctx)
            if is_fighting:
                return self._fight(army, world, ctx, objective)
            return self._advance_or_gather(army, objective, gather, world, ctx)
        return self._forward(army, waiting, objective, is_fighting, point, world, ctx)

    def _is_fighting(self, army: list[Unit], objective: Cell | None, ctx: MatchContext) -> bool:
        """Whether main engages an enemy near that is one to fight."""
        estimated = self.estimate
        return self._main.mode == ENGAGE and estimated is not None and _is_in_the_way(estimated, army, objective, ctx)

    def _forward(
        self,
        army: list[Unit],
        waiting: list[Unit],
        objective: Cell | None,
        is_fighting: bool,
        point: Cell,
        world: World,
        ctx: MatchContext,
    ) -> list[Intent]:
        """The orders of an army gone forward: to fight, go on to its objective or come back, and the reinforcements.
        An army holding at its staging cell (see `forward_staging`) goes into no fight from there, and one whose
        objective is defended beyond what it can beat goes as `defended` says."""
        defended_orders = self._defended_orders(army, objective, world, ctx)
        if defended_orders is not None:
            return [*self._reinforce(waiting, army, point, world, ctx), *defended_orders]
        held = None if objective is None else self._staged(army, objective, world, ctx)
        if is_fighting and held is None:
            # A fight is no stall of the advance, which is watched afresh once it goes on.
            self._march_watch.reset()
            return [*self._reinforce(waiting, army, point, world, ctx), *self._fight(army, world, ctx, objective)]
        if objective is None or not self._is_screened(army, world, ctx):
            # Nothing is left that the army can take, or too little screen is left for its long-range units to go
            # on behind: it is main at the staging point again, which stages afresh when it next sets out.
            self._army = set()
            self._march_watch.reset()
            self._staging.stop()
            return _to_staging(army, point, ctx.tuning.combat, Priority.MOVE)
        reinforcing = self._reinforce(waiting, army, point, world, ctx)
        if held is not None:
            # Holding short of the objective is no stall of the advance either.
            self._march_watch.reset()
            return [*reinforcing, *self._hold_at(army, held, world, ctx)]
        return [*reinforcing, *self._march_fighting(army, objective, world, ctx)]

    def _staged(self, army: list[Unit], objective: Cell, world: World, ctx: MatchContext) -> Cell | None:
        """The staging cell main holds at on its plan's way to its objective, the straight way (see
        `forward_staging`); None while it goes on, and always with `forward_staging` off."""
        if not ctx.tuning.combat.forward_staging:
            return None
        return self._staging.follow(objective, army, objective, objective, True, world, ctx)

    def _hold_at(self, units: list[Unit], cell: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """main on its way to its staging cell and holding there: its units in contact fire at what is within their
        weapon range and are not sent forward; its long-range units go to their slots behind the screen (see
        `siege.Siege.march`), and the rest attack-move to the cell, but those standing idle there."""
        fire, rest = self._hold_fire_of(units, world, ctx)
        long_range, screen = _subgroups(rest, ctx)
        if long_range:
            return [*fire, *self._siege.march(long_range, screen, cell, world, ctx.tuning)]
        return [*fire, *_march(rest, cell, ctx)]

    def _defended_orders(
        self, units: list[Unit], objective: Cell | None, world: World, ctx: MatchContext
    ) -> list[Intent] | None:
        """With defended_redirect on, main's orders while its objective is defended beyond what it can beat (see
        `defended`): its redirect, or its gathering, shelling or siege with the screen's units in contact firing at
        what is within their weapon range, none of them into covered ground; None while main goes on as ever, and
        always while it strikes as a whole."""
        if not ctx.tuning.combat.defended_redirect or objective is None or ctx.strike_target is not None:
            self._defended.stop()
            return None
        long_range, screen = _subgroups(units, ctx)
        verdict = self._defended.follow(units, long_range, objective, world, ctx)
        if verdict is None:
            return None
        fire: list[Intent] = []
        if verdict.kind != defended.REDIRECT or verdict.is_gathering:
            fire, screen = self._hold_fire_of(screen, world, ctx)
        orders = [*fire, *self._defended.orders(verdict, long_range, screen, world, ctx)]
        return defended.held_off(orders, world, ctx)

    def _preserve(self, units: list[Unit], point: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """main kept home: what is far from the base is sent to the staging point, the rest gathers or fights. While a
        nuke is expected and main does not fight, each unit goes to its place in the groups spread around the staging
        point instead, a far one whatever it does."""
        self._army = set()
        tuning = ctx.tuning.combat
        far, near = _far_and_near(units, world, ctx.tuning.clock.preserve_recall_cells)
        is_fighting = self._main.mode == ENGAGE and self.estimate is not None and near
        if self._nuke.is_spreading and not is_fighting:
            return self._nuke.gather(units, point, world, ctx.terrain, tuning, frozenset(unit.id for unit in far))
        home = self._fight(near, world, ctx, None) if is_fighting else self._gather_main(near, point, world, ctx)
        return [*_to_staging(far, point, tuning, Priority.MOVE), *home]

    def _objective(self, army: list[Unit], world: World, ctx: MatchContext) -> Cell | None:
        """Where the army goes: the must-kill target main as a whole strikes; under FINISH what is left of the enemy, or
        the sweep's next point; else see below."""
        target = ctx.strike_target
        if target is not None:
            return target.x, target.y
        if ctx.plan != FINISH:
            return _objective(army, world, ctx)
        target = self._last_target(army, world, ctx)
        if target is not None:
            self._sweep.pause()
            return target
        if self._army:
            self._sweep.follow(army, world, ctx.tuning.combat)
        return self._sweep.current(world, ctx.tuning.combat)

    def _last_target(self, army: list[Unit], world: World, ctx: MatchContext) -> Cell | None:
        """What is left of the enemy that main goes on under FINISH: with `sticky_attack_target` on the target it keeps
        (see `finish_target`), else the nearest remembered enemy building or MCV; None for none."""
        tuning = ctx.tuning.combat
        if not tuning.sticky_attack_target:
            return _nearest_target(army, world, tuning)
        self._finish_target = finish_target.follow(self._finish_target, army, world, tuning)
        return None if self._finish_target is None else self._finish_target.cell

    def _fight(
        self, units: list[Unit], world: World, ctx: MatchContext, objective: Cell | None, chase: bool = True
    ) -> list[Intent]:
        """The orders of a group that engages: its units handed out over the enemies around it, those given none sent
        after the first ground enemy with `chase` (see `targeting.FocusFire`).

        With long-range units among them, the siege orders them and their
        screen toward the enemy units around in sight; when none is, toward
        the objective, or with none toward the nearest remembered defense
        around, which the other units would attack.
        """
        long_range, screen = _subgroups(units, ctx)
        if not long_range:
            return self._focus_orders(units, self.estimate.centroid, world, ctx, chase=chase)
        in_sight = [record for record in self.estimate.enemies if record.visible]
        aim = centroid(in_sight) if in_sight else objective or _nearest_defense(self.estimate.centroid, world, ctx)
        may_advance = siege.has_screen(long_range, screen, world, ctx.tuning.siege)
        return self._siege.orders(long_range, screen, aim, may_advance, world, ctx.tuning)

    def _hold_forward(
        self, army: list[Unit], is_fighting: bool, point: Cell, world: World, ctx: MatchContext
    ) -> list[Intent]:
        """main at the forward point: the units that chased too far go back to it, the rest fight the enemy units it
        may chase there, or gather at it; it never advances from there."""
        tuning = ctx.tuning.combat
        back, staying = self._hold.leash(army, point, tuning)
        returning = _to_staging(back, point, tuning, Priority.MOVE)
        fight = self._hold_fight(staying, world, ctx) if is_fighting and staying else []
        if fight:
            self._chasing = {unit.id for unit in staying}
            return [*returning, *fight]
        return [*returning, *self._gather_main(staying, point, world, ctx)]

    def _hold_fight(self, units: list[Unit], world: World, ctx: MatchContext) -> list[Intent]:
        """The focus fire of main at the forward point, over the enemies around it that the forward hold may chase."""
        admit = partial(forward_hold.may_chase, world=world, tuning=ctx.tuning)
        return self._focus_orders(units, self.estimate.centroid, world, ctx, admit)

    def _focus_orders(
        self,
        units: list[Unit],
        middle: Point,
        world: World,
        ctx: MatchContext,
        admit: Callable[[EnemyRecord], bool] | None = None,
        chase: bool = True,
        in_range_only: bool = False,
    ) -> list[Intent]:
        """The focus fire of the units given that are on no crush run, which is left out of it, with `chase` and
        `in_range_only` as `targeting.FocusFire` takes them, over the match's one memory of the targets given, the
        artillery taking the enemy vehicles first while a crush run is on; none when every one is on one. An artillery
        piece or a V2 with an enemy come within its minimum range and the margin steps back toward the base instead
        (see `kite`)."""
        running = self._crush.units
        free = [unit for unit in units if unit.id not in running]
        steps = kite.fall_back_moves(free, world, ctx.tuning)
        stepping = kite.stepping(steps)
        firing = [unit for unit in free if unit.id not in stepping]
        if not firing:
            return steps
        focus = targeting.FocusFire(ctx.target_memory)
        crushing = bool(running) or ctx.run_over_out
        return [*steps, *focus.orders(firing, middle, world, ctx.tuning, admit, chase, in_range_only, crushing)]

    def _hold_fire_of(self, units: list[Unit], world: World, ctx: MatchContext) -> tuple[list[Intent], list[Unit]]:
        """With contact_focus on, the attacks of the units in contact on the targets already within their weapon
        range, which send them nowhere, and the units given none; with it off, no attack and all of the units."""
        engaged = targeting.in_contact(units, world) if ctx.tuning.combat.contact_focus else []
        if not engaged:
            return [], units
        fire = self._focus_orders(engaged, centroid(engaged), world, ctx, chase=False, in_range_only=True)
        firing = {unit_id for intent in fire for unit_id in intent.order["units"]}
        return fire, [unit for unit in units if unit.id not in firing]

    def _advance_or_gather(
        self, units: list[Unit], objective: Cell | None, point: Cell, world: World, ctx: MatchContext
    ) -> list[Intent]:
        """Send main on its objective when it is strong enough (whatever its strength while it strikes a must-kill
        target as a whole), screened and gathered at `point`, where it gathers, else gather it there."""
        gathered = self._gathered(units, point, world, ctx)
        is_strong = ctx.strike_target is not None or strength(units, world) >= ctx.min_attack_strength(ctx.plan)
        is_strong = is_strong and self._is_screened(units, world, ctx)
        if not is_strong or len(gathered) < ctx.tuning.combat.gathered_share * len(units) or objective is None:
            return self._gather_main(units, point, world, ctx)
        self._army = {unit.id for unit in units}
        defended_orders = self._defended_orders(units, objective, world, ctx)
        if defended_orders is not None:
            return defended_orders
        held = self._staged(units, objective, world, ctx)
        if held is not None:
            return self._hold_at(units, held, world, ctx)
        return self._march_fighting(units, objective, world, ctx)

    def _gathered(self, units: list[Unit], point: Cell, world: World, ctx: MatchContext) -> list[Unit]:
        """main's units gathered at `point`: near it, or while a nuke is expected at their places around it."""
        tuning = ctx.tuning.combat
        if self._nuke.is_spreading:
            return self._nuke.at_places(units, point, world, ctx.terrain, tuning)
        return [unit for unit in units if is_near(point, unit.x, unit.y, tuning.gathered_cells)]

    def _gather_main(self, units: list[Unit], point: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """A `move` for main's units standing idle away from where they gather: `point`, or while a nuke is expected
        their places in the groups spread around it (see `nuke`); with contact_focus on, the units in contact fire at
        what is within their weapon range instead (see `_hold_fire_of`)."""
        fire, units = self._hold_fire_of(units, world, ctx)
        tuning = ctx.tuning.combat
        if self._nuke.is_spreading:
            return [*fire, *self._nuke.gather(units, point, world, ctx.terrain, tuning)]
        return [*fire, *_gather(units, point, tuning)]

    def _march_fighting(self, army: list[Unit], objective: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """The army on its way to its objective (see `_march`). With contact_focus on and no long-range unit among it,
        whose siege would order the screen's fights, its units in contact fight on the way though no enemy is near its
        centroid, sent after no enemy; under FINISH only what stands on its way."""
        long_range, _ = _subgroups(army, ctx)
        engaged = [] if long_range or not ctx.tuning.combat.contact_focus else targeting.in_contact(army, world)
        if not engaged:
            return self._march(army, objective, world, ctx)
        admit = _on_the_way(army, objective, ctx) if ctx.plan == FINISH else None
        fire = self._focus_orders(engaged, centroid(engaged), world, ctx, admit, chase=False)
        return _fire_then(fire, self._march(army, objective, world, ctx))

    def _march(self, army: list[Unit], objective: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """The army on its way to its objective: as one group, or its long-range units behind their screen, which with
        `march_when_clear` on marches on the objective while no enemy is near, and goes as one group once a clear
        advance has stalled (see `marching`)."""
        long_range, screen = _subgroups(army, ctx)
        if not long_range:
            return _march(army, objective, ctx)
        mode = self._march_watch.mode(army, objective, world, ctx) if ctx.tuning.siege.march_when_clear else None
        if mode == marching.FRONTAL:
            return _march(army, objective, ctx)
        if mode == marching.MARCH:
            return self._siege.march(long_range, screen, objective, world, ctx.tuning)
        return self._siege.orders(long_range, screen, objective, True, world, ctx.tuning)

    @staticmethod
    def _is_screened(units: list[Unit], world: World, ctx: MatchContext) -> bool:
        """Whether main's long-range units among some of its units have screen enough to go forward; true without,
        and always under FINISH, which ends the match only by going on: there main neither waits at the staging
        point for a screen nor comes back to it once its screen wears down, the siege still placing its long-range
        units."""
        if ctx.plan == FINISH:
            return True
        long_range, screen = _subgroups(units, ctx)
        return siege.has_screen(long_range, screen, world, ctx.tuning.siege)

    def _reinforce(
        self, waiting: list[Unit], army: list[Unit], point: Cell, world: World, ctx: MatchContext
    ) -> list[Intent]:
        """Send the units waiting at the staging point after the army together, once they are worth enough; while main
        has a staging cell, they meet at the cell instead (see `_join_at`)."""
        if self._staging.cell is not None:
            return self._join_at(waiting, army, self._staging.cell, world, ctx)
        if strength(waiting, world) < ctx.tuning.combat.reinforcement_strength:
            return self._gather_main(waiting, point, world, ctx)
        self._army.update(unit.id for unit in waiting)
        meeting = cells.clamped(cells.nearest_cell(*centroid(army)), world.map_bounds)
        order = {"cmd": "attack_move", "units": [unit.id for unit in waiting], "to": list(meeting)}
        return [Intent(Priority.MOVE, order)]

    def _join_at(
        self, waiting: list[Unit], army: list[Unit], cell: Cell, world: World, ctx: MatchContext
    ) -> list[Intent]:
        """The units made since main went forward while main has a staging cell: sent to the cell, whether they stand
        idle or not, and those standing there on to the army together once worth reinforcement_strength, or after
        the wait of `forward_staging.JoinWait`; a unit in contact fires at what is within its weapon range instead."""
        tuning = ctx.tuning.combat
        fire, units = self._hold_fire_of(waiting, world, ctx)
        there = [unit for unit in units if is_near(cell, unit.x, unit.y, tuning.gathered_cells)]
        is_enough = strength(there, world) >= tuning.reinforcement_strength
        going_on = [unit.id for unit in self._staging.joining.going_on(there, is_enough, world.tick)]
        standing_there = {unit.id for unit in there}
        astray = [unit.id for unit in units if unit.id not in standing_there]
        moves = [Intent(Priority.MOVE, {"cmd": "move", "units": astray, "to": list(cell)})] if astray else []
        if not going_on:
            return [*fire, *moves]
        self._army.update(going_on)
        meeting = cells.clamped(cells.nearest_cell(*centroid(army)), world.map_bounds)
        return [*fire, *moves, Intent(Priority.MOVE, {"cmd": "attack_move", "units": going_on, "to": list(meeting)})]


def _group_order(action: Action, target: Cell | None, units: list[Unit], ctx: MatchContext) -> Order | None:
    """The one order a standing action stands for. Under hold, a stop for the units that do not stand idle; else, for
    the units not standing idle at the target already, the nearest to it first, a move under a pull back, a retreat
    home and on the way to a flank's side waypoint, an attack-move otherwise. None when no unit needs it."""
    if action.kind == HOLD_ACTION:
        moving = [unit.id for unit in units if not unit.idle]
        return {"cmd": "stop", "units": moving} if moving else None
    going = _not_there(units, target, ctx.tuning.combat)
    if not going:
        return None
    cmd = "attack_move" if action.kind in FORWARD_KINDS and target == action.cell else "move"
    return {"cmd": cmd, "units": [unit.id for unit in going], "to": list(target)}


def _fire_then(fire: list[Intent], orders: list[Intent]) -> list[Intent]:
    """The attacks of the units in contact, then the other orders without those units: an attack the loop holds back as
    one still in force must not leave its units to a march in the same batch, which would take them off their
    targets."""
    firing = frozenset(unit_id for intent in fire for unit_id in intent.order["units"])
    return [*fire, *crush.without(firing, orders)]


def _is_retreat(action: Action, ctx: MatchContext) -> bool:
    """Whether a standing action is a retreat, whose order keeps every unit of main: a pull back, a retreat home, or the
    gather the commander's retreat override or its regroup stands for."""
    if action.kind in (PULL_BACK, RETREAT_HOME):
        return True
    commander = ctx.commander
    is_pulled = commander is not None and (commander.retreat.is_active or commander.is_regrouping)
    return action.kind == GATHER and is_pulled


def _not_there(units: list[Unit], target: Cell, tuning: CombatTuning) -> list[Unit]:
    """The units that do not stand idle within gathered_cells of a target, the nearest to it first."""
    going = [unit for unit in units if not (unit.idle and is_near(target, unit.x, unit.y, tuning.gathered_cells))]
    return sorted(going, key=lambda unit: math.hypot(unit.x - target[0], unit.y - target[1]))


def _nearest_defense(middle: Point, world: World, ctx: MatchContext) -> Cell | None:
    """The remembered enemy defense that fires at ground units nearest to a point, within the contact distance of it;
    None for none."""
    near = [
        (math.hypot(record.x - middle[0], record.y - middle[1]), record.x, record.y)
        for record in ground_defenses(world)
    ]
    within = [entry for entry in near if entry[0] <= ctx.tuning.combat.contact_cells]
    return min(within)[1:] if within else None


def _subgroups(units: list[Unit], ctx: MatchContext) -> tuple[list[Unit], list[Unit]]:
    """main's long-range units among some of its units, and the rest, their screen; none is long-range in `frontal`."""
    if ctx.tuning.siege.mode != SCREENED:
        return [], units
    long_range = set(ctx.groups.siege_members(MAIN))
    return [unit for unit in units if unit.id in long_range], [unit for unit in units if unit.id not in long_range]


def _far_and_near(units: list[Unit], world: World, reach: float) -> tuple[list[Unit], list[Unit]]:
    """The units farther than `reach` from the center of the base, and the rest."""
    far = [unit for unit in units if not cells.is_within((unit.x, unit.y), world.base_center, reach)]
    # By id: comparing units themselves would weigh every unit against every far one, field by field.
    far_ids = {unit.id for unit in far}
    return far, [unit for unit in units if unit.id not in far_ids]


def _gather(units: list[Unit], point: Cell, tuning: CombatTuning) -> list[Intent]:
    """A `move` to the point main gathers at for the units that stand idle away from it."""
    astray = [unit.id for unit in units if unit.idle and not is_near(point, unit.x, unit.y, tuning.gathered_cells)]
    return [Intent(Priority.MOVE, {"cmd": "move", "units": astray, "to": list(point)})] if astray else []


def _march(army: list[Unit], objective: Cell, ctx: MatchContext) -> list[Intent]:
    """An `attack_move` for the army toward its objective, but for the units that stand idle there already."""
    reach = ctx.tuning.combat.gathered_cells
    going = [unit.id for unit in army if not (unit.idle and is_near(objective, unit.x, unit.y, reach))]
    return [Intent(Priority.MOVE, {"cmd": "attack_move", "units": going, "to": list(objective)})] if going else []


def _objective(army: list[Unit], world: World, ctx: MatchContext) -> Cell | None:
    """Where the army goes: the nearest remembered enemy building no defense covers, else the enemy's start.

    When every known building is covered, the nearest one, if the army is
    clearly stronger than the defenses that cover it; None when it is not.
    """
    middle = centroid(army)
    buildings = sorted(
        (record for record in world.enemy_memory.values() if record.is_building),
        key=lambda record: math.hypot(record.x - middle[0], record.y - middle[1]),
    )
    if not buildings:
        return world.enemy_base
    defenses, tuning = ground_defenses(world), ctx.tuning
    for record in buildings:
        if not coverage.is_covered((record.x, record.y), defenses, world.items, tuning.coverage):
            return record.x, record.y
    nearest = buildings[0]
    return (nearest.x, nearest.y) if _can_take(army, nearest, world, tuning) else None


def _nearest_target(army: list[Unit], world: World, tuning: CombatTuning) -> Cell | None:
    """The remembered enemy missile silo nearest to the army with `nuke_spread` on, else the nearest remembered enemy
    building, else the nearest remembered enemy MCV; None for none."""
    remembered = world.enemy_memory.values()
    silos = nuke.known_silos(world) if tuning.nuke_spread else []
    buildings = [record for record in remembered if record.is_building]
    targets = silos or buildings or [record for record in remembered if record.type == MCV]
    if not targets:
        return None
    middle = centroid(army)
    nearest = min(targets, key=lambda record: math.hypot(record.x - middle[0], record.y - middle[1]))
    return nearest.x, nearest.y


def _is_in_the_way(found: Estimate, army: list[Unit], objective: Cell | None, ctx: MatchContext) -> bool:
    """Whether an enemy near the army is one to fight: any is, but under FINISH only one close to the army's path."""
    if ctx.plan != FINISH or objective is None:
        return True
    return any(map(_on_the_way(army, objective, ctx), found.enemies))


def _on_the_way(army: list[Unit], objective: Cell, ctx: MatchContext) -> Callable[[EnemyRecord], bool]:
    """Whether an enemy stands on the army's way to its objective: within sweep_engage_cells of the straight way from
    its centroid."""
    start, reach = centroid(army), ctx.tuning.combat.sweep_engage_cells
    return lambda record: distance_to_path((record.x, record.y), start, objective) <= reach


def distance_to_path(point: Point, start: Point, end: Point) -> float:
    """How far a point is from the straight way between two others, in cells."""
    span_x, span_y = end[0] - start[0], end[1] - start[1]
    length_squared = span_x * span_x + span_y * span_y
    if length_squared == 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    # The share of the way at which the point is nearest to it, kept between the two ends.
    share = max(0.0, min(1.0, ((point[0] - start[0]) * span_x + (point[1] - start[1]) * span_y) / length_squared))
    return math.hypot(point[0] - (start[0] + share * span_x), point[1] - (start[1] + share * span_y))


def _can_take(army: list[Unit], building: EnemyRecord, world: World, tuning: Tuning) -> bool:
    """Whether the army is clearly stronger than the remembered defenses that cover a building.

    Each defense counts at `defense_weight` times its cost, and the lot is
    raised by `unseen_factor` when one of them is out of sight, as in the
    estimate of a fight.
    """
    cell = (building.x, building.y)
    covering = [
        defense
        for defense in ground_defenses(world)
        if cells.is_within(cell, (defense.x, defense.y), coverage.reach(defense.type, world.items, tuning.coverage))
    ]
    combat = tuning.combat
    enemy = combat.defense_weight * sum(worth(defense.type, _FULL_HEALTH, world) for defense in covering)
    if not all(defense.visible for defense in covering):
        enemy *= combat.unseen_factor
    return strength(army, world) >= combat.engage_ratio * enemy


def _to_staging(
    units: list[Unit], point: Cell, tuning: CombatTuning, priority: Priority = Priority.EMERGENCY
) -> list[Intent]:
    """A `move` to a point, the staging point or main's gather point, for the units not there yet: an emergency,
    unless another priority is given."""
    away = [unit.id for unit in units if not is_near(point, unit.x, unit.y, tuning.gathered_cells)]
    return [Intent(priority, {"cmd": "move", "units": away, "to": list(point)})] if away else []
