"""Scouting: keeping the agent's knowledge of the enemy fresh enough to plan on.

The scouting manager runs after the base is looked after and before the army
moves. In every cycle it brings the scout report up to date from the enemy
memory and publishes it on the match context, where the rules that weigh the
enemy read it.

Knowledge is gathered by one scout at a time, a unit moved into the `scout`
group for its mission. The first is the first rifle infantry trained beyond
the home guard. A fast unit that can scout (the Allied jeep, the Soviet
dog) takes over from that rifle as soon as there is one that is not
raiding, and the rifle goes to the home guard. A Soviet scout is never a
flak truck, which costs too much to lose on a scouting run.

The scout is sent to the enemy's start. Scouting is about the enemy base and
its approaches, not about the map: near the base it circles it once, through
four points around it, and then comes back to the staging point and is
released to `main`. A scout does not fight its way in. When a remembered enemy
defense covers where it stands, or it has lost half its health, it withdraws a
short way toward home and comes back. A point the scout cannot reach, where it
stands idle for a while, is given up for the next.

Knowledge goes stale when no enemy building has been seen for a minute and
a half. Then, when no scout is out, the cheapest idle unit that fights and is
neither in the home guard nor raiding becomes the next scout; a new scout is
sent at most once a minute. A scout lost before an enemy building was seen on
its mission found nothing and is replaced once, half a minute later. When two
are lost that way in a row the way in is evidently guarded, so no scout is
sent for a minute, and the match record says so. The scout sent after that
is the fastest idle unit that fights (a jeep or a flak truck, a light tank,
before any infantry), and it goes in by a waypoint 12 cells to the side of
its direct line to the enemy's start, not the way the two before it died.

With `[scouting] rescout` on, the picture goes stale by three clocks of what
our units have had in sight instead: the enemy base area, the enemy army and
the far ore fields (see `rescout`). The first clock that has run out is the
reason of the mission, and no new scout is sent while base defense reports a
major threat. The scout is then a jeep, else a dog, else a rifle, that
stands idle or waits with main at its gathering point: fast and cheap. A
mission then lasts two minutes at the most, and a scout that base defense
takes for the base is lost to its mission; either way the unit goes back to
main.

Scouting orders go at the lowest priority of all: any other order for the
scout in the same batch wins.

Under PRESERVE, which keeps a lead to the end of the match, no scout is sent
and one in the field comes back to the staging point: a scout lost then is a
loss the enemy scores.

With `[scouting] indirect_route` on, a scout does not drive straight at the
enemy's start: it goes the indirect way of `scout_route`, by one side of the
line to the enemy on one mission and by the other on the next, past the ore
fields near its way that have gone unseen and round the far side of the enemy
base, and home by the other side. The withdrawal from danger is the same. A
scout that is hit hard in one state, its health down by scout_hit_hp_drop or
more, does not press on to the waypoint it was heading to: it goes on to the
next one that lies farther from what hit it than it stands.

With `[scouting] rush_detection` on, the manager also keeps the rush watch
(see `rush_watch`) and publishes its verdict and its warning on the match
context for the opening and upkeep.

With `[scouting] exposed_trucks` on, the manager also notes the enemy
harvesters our units see at work far from the enemy's start, with little
guard and no defense over them, as raid opportunities (see `exposed_trucks`),
and publishes them on the match context as `exposed_trucks` in every cycle.
It orders nothing for them.

With `rescout` or `indirect_route` on, the match record follows each mission
with `scout` events: when it is sent, each waypoint its scout reaches, and
whether it ended done or lost, each with what is then known of the enemy.

With `[scouting] scout_slots` on, none of the missions above is sent, none
of their riflemen is trained, and the scouting is the scout slots' (see
`scout_slots`): a few designated scouts kept for the whole match. The report,
the record of what has been in sight and the rush watch are kept as with the
switch off.

With the slots off and the model commander steering the first scout (see
`commander`), the scout it steers is the scout of the mission in the field.
Its question is asked in every cycle (see `command_scout`), and while the
standing action is in force the scout goes to the action's cell and stands
there, or holds where it is for three seconds, in place of following its
route, which it takes up again once the commander no longer steers it. A
scout withdrawing from danger goes on withdrawing whatever the model says,
and the mission ends as it would without the commander.
"""

from __future__ import annotations

from dataclasses import dataclass

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, command_scout, coverage, raid_routes, scout_route
from ucla_agent.games.redalert.batch import Intent, Order, Priority
from ucla_agent.games.redalert.command_prompt import Action
from ucla_agent.games.redalert.commander import SCOUT_ROLE, STOP
from ucla_agent.games.redalert.composition import PRESERVE
from ucla_agent.games.redalert.expansion import ESCORT
from ucla_agent.games.redalert.exposed_trucks import ExposedTruckWatch
from ucla_agent.games.redalert.family import RIFLE_INFANTRY
from ucla_agent.games.redalert.groups import GUARD, MAIN, RAID, SCOUT
from ucla_agent.games.redalert.items import ALLIES, SOVIET, TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MAJOR_THREAT, MatchContext
from ucla_agent.games.redalert.rescout import STALE_BASE, ScoutTrainer, StaleIntel
from ucla_agent.games.redalert.rush_watch import WATCH, RushWatch
from ucla_agent.games.redalert.scout_mission_planner import MissionPlanner
from ucla_agent.games.redalert.scout_report import EnemyIntel, ScoutReport
from ucla_agent.games.redalert.scout_slots import ScoutSlots
from ucla_agent.games.redalert.scout_steering import Dangers
from ucla_agent.games.redalert.tuning import ScoutingTuning
from ucla_agent.games.redalert.world import Cell, Unit, World

MANAGER_NAME = "scouting"
# The fast unit each family scouts with when it has one.
SCOUT_VEHICLES = {ALLIES: "jeep", SOVIET: "dog"}
# With `rescout` on, the types a scout is chosen from, the most wanted first: fast, cheap and expendable.
RESCOUT_TYPES = (SCOUT_VEHICLES[ALLIES], SCOUT_VEHICLES[SOVIET], RIFLE_INFANTRY)
# The stages of a mission: on the way to the enemy base, circling it, and on the way back; or, with
# `indirect_route` on, going round it the indirect way.
APPROACH = "approach"
CIRCLE = "circle"
RETURN = "return"
AROUND = "around"
# The reason of the first scout's mission, and of a vehicle's that takes over from it.
INITIAL = "initial"
SCOUT_EVENT = "scout"
# The phases of a mission a `scout` event records: sent, at a waypoint, and how it ended: its scout released, or
# lost to it, killed or taken by base defense.
SENT = "sent"
WAYPOINT = "waypoint"
DONE = "done"
LOST = "lost"
# The groups a new scout is never taken from: the escort of the MCV on its way to a second yard and the rush
# watcher among them.
_NOT_SCOUTING = frozenset({GUARD, RAID, SCOUT, ESCORT, WATCH})
_ERROR_SOURCE = "redalert.scouting"


@dataclass
class Mission:
    """The scout in the field and how far along its mission it is."""

    unit_id: int
    # The tick the unit became the scout.
    assigned_tick: int
    # Why the mission was sent: INITIAL, or the reason the picture of the enemy was stale.
    reason: str
    unit_type: str
    # Where the scout was last seen, and its health then.
    cell: Cell
    hp: int
    stage: str
    # The cells the scout still has to reach, in order; it is on its way to the first.
    route: list[Cell]
    # The tick the scout was first sent toward the first cell of the route.
    leg_tick: int
    # The scout is the first rifle beyond the guard, which a scouting vehicle takes over from.
    is_first_rifle: bool = False

    def follow(self, stage: str, route: list[Cell], tick: int) -> None:
        """Go on with the mission at another stage, along another route."""
        self.stage, self.route, self.leg_tick = stage, route, tick


class ScoutingManager:
    """Keeps the picture of the enemy fresh with one scout at a time. See the module's note."""

    name = MANAGER_NAME

    def __init__(self) -> None:
        self._intel = EnemyIntel()
        # What has gone unseen, kept while `rescout` is on, and the rifle trained when no scout can be found.
        self._stale = StaleIntel()
        self._trainer = ScoutTrainer(MANAGER_NAME)
        # The verdict on an early rush, kept while `rush_detection` is on.
        self._rush_watch = RushWatch()
        # The designated scouts, which do all the scouting while `scout_slots` is on.
        self._slots = ScoutSlots(MANAGER_NAME)
        self._mission: Mission | None = None
        # Whether this match has had a scout: the first rifle beyond the guard becomes one only before any other.
        self._has_scouted = False
        # The tick the last new scout was sent out at.
        self._sent_tick: int | None = None
        # Scouts lost in a row before an enemy building was seen on their mission.
        self._blind_losses = 0
        # The tick from which a scout lost that way may be replaced, and the reason of the mission it goes on; None
        # when none is to be.
        self._replace_from: int | None = None
        self._replace_reason = STALE_BASE
        # No scout is sent before this tick.
        self._paused_until = 0
        # The next scout is the first after a pause: the fastest, and by a side way.
        self._is_after_pause = False
        # The side of the line to the enemy the mission in the field goes by the indirect way: 1 or -1, changed for
        # every new mission.
        self._side = -1
        # The missions the model commander's question offers the scout in the field, and the clock they read.
        self._planner = MissionPlanner()
        # The raid opportunities seen, kept while `exposed_trucks` is on.
        self._trucks = ExposedTruckWatch()

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """Publish the cycle's scout report, keep the scout slots with `scout_slots` on or else find a scout when one is
        due and lead the one in the field, and with `rush_detection` on post the rush watcher."""
        tuning = ctx.tuning.scouting
        ctx.scout_report = self._intel.observe(world, tuning)
        self._observe(world, ctx)
        if not _is_map_known(world):
            return []
        orders = (
            self._slots.intents(world, ctx, self._stale.sight) if tuning.scout_slots else self._missions(world, ctx)
        )
        return orders + self._rush_watch.watcher_intents(world, ctx) if tuning.rush_detection else orders

    def _missions(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The orders of the one-scout missions: a rifleman trained for a scout due with none to send, and the scout's
        next order."""
        if self._mission is not None:
            self._account(world, ctx)
        unanswered = self._direct(world, ctx)
        orders = self._trainer.intents(ctx.tuning.scouting.rescout and unanswered is not None, world, ctx)
        if self._mission is not None:
            orders += self._lead(world, ctx)
        return orders

    def _observe(self, world: World, ctx: MatchContext) -> None:
        """Take in the cycle's state: what has gone unseen, while `rescout`, `indirect_route` or `scout_slots` is on,
        the rush verdict while `rush_detection` is, and the raid opportunities while `exposed_trucks` is; the last two
        published on the match context."""
        tuning = ctx.tuning.scouting
        if tuning.rescout or tuning.indirect_route or tuning.scout_slots:
            self._stale.observe(world)
        if tuning.rush_detection:
            ctx.rush_verdict = self._rush_watch.observe(world, ctx)
            ctx.rush_warning = self._rush_watch.warning
        if tuning.exposed_trucks:
            ctx.exposed_trucks = self._trucks.observe(world, ctx.terrain, ctx.tuning)

    def _account(self, world: World, ctx: MatchContext) -> None:
        """Account for the scout in the field: lost, or with `rescout` on, out too long or taken by base defense, when
        its mission ends and it goes back to main."""
        mission = self._mission
        scout = world.combat_units.get(mission.unit_id)
        if scout is None:
            _log_scout(LOST, mission, ctx)
            self._note_loss(world, ctx.tuning.scouting)
            return
        mission.cell = (scout.x, scout.y)
        ending = _ending(mission, world, ctx) if ctx.tuning.scouting.rescout else None
        if ending is not None:
            _log_scout(ending, mission, ctx)
            ctx.groups.assign(mission.unit_id, MAIN)
            self._mission = None

    def _direct(self, world: World, ctx: MatchContext) -> str | None:
        """Call the scout home under PRESERVE, else send one when due or let a vehicle take over; the reason of a
        scout due that no unit could be found for, else None."""
        if ctx.plan == PRESERVE:
            self._call_home(world, ctx)
            return None
        if self._mission is None:
            return self._send_new(world, ctx)
        self._hand_over_to_vehicle(world, ctx)
        return None

    def _call_home(self, world: World, ctx: MatchContext) -> None:
        """Turn the scout in the field, if any, back to the staging point, unless it is on its way home already."""
        if self._mission is not None and self._mission.stage != RETURN:
            self._mission.follow(RETURN, [_staging_point(world, ctx)], world.tick)

    def _note_loss(self, world: World, tuning: ScoutingTuning) -> None:
        """Account for the scout lost: one that saw no enemy building is replaced, two such pause the scouting."""
        lost, self._mission = self._mission, None
        seen = self._intel.last_building_seen_tick
        if seen is not None and seen >= lost.assigned_tick:
            self._blind_losses = 0
            return
        self._blind_losses += 1
        if self._blind_losses < tuning.blind_losses_before_pause:
            self._replace_from = world.tick + tuning.replace_after_seconds * TICKS_PER_SECOND
            self._replace_reason = lost.reason
            return
        self._paused_until = world.tick + tuning.pause_seconds * TICKS_PER_SECOND
        self._blind_losses, self._replace_from = 0, None
        self._is_after_pause = True
        _log_pause(tuning)

    def _send_new(self, world: World, ctx: MatchContext) -> str | None:
        """Send a new scout when one is due: the first rifle beyond the guard, a replacement, or one to refresh. The
        reason of one due that no unit could be found for, else None."""
        unit_id = None if self._has_scouted else _first_rifle_beyond_the_guard(world, ctx)
        reason, is_first_rifle = INITIAL, unit_id is not None
        if unit_id is None:
            reason = self._due_reason(world, ctx)
            unit_id = None if reason is None else self._choose(world, ctx)
            if unit_id is None:
                return reason
        self._side = -self._side
        self._start(unit_id, reason, world, ctx, is_first_rifle=is_first_rifle, by_side=self._is_after_pause)
        self._sent_tick, self._replace_from, self._is_after_pause = world.tick, None, False
        return None

    def _choose(self, world: World, ctx: MatchContext) -> int | None:
        """The unit to send as the next scout: with `rescout` on the most wanted of the scouting types, else the
        fastest idle one after a pause and the cheapest otherwise."""
        if ctx.tuning.scouting.rescout:
            return _preferred_scout(world, ctx)
        return _fastest_idle(world, ctx) if self._is_after_pause else _cheapest_idle(world, ctx)

    def _due_reason(self, world: World, ctx: MatchContext) -> str | None:
        """Why a new scout is due now: the lost one's reason for a replacement, else the stale picture's; None while
        none is."""
        tuning, tick = ctx.tuning.scouting, world.tick
        if not self._is_free(tick, ctx):
            return None
        if self._replace_from is not None:
            return self._replace_reason if tick >= self._replace_from else None
        if tuning.rescout:
            return self._stale.reason(world, ctx.terrain, tuning)
        last_seen = self._intel.last_building_seen_tick or 0
        return STALE_BASE if tick - last_seen >= tuning.stale_after_ticks else None

    def _is_free(self, tick: int, ctx: MatchContext) -> bool:
        """Whether a new scout may be sent: not paused, a minute from the last, and with `rescout` not under a major
        threat."""
        tuning = ctx.tuning.scouting
        if tick < self._paused_until or (tuning.rescout and ctx.threat_level == MAJOR_THREAT):
            return False
        return self._sent_tick is None or tick - self._sent_tick >= tuning.new_scout_seconds * TICKS_PER_SECOND

    def _start(
        self,
        unit_id: int,
        reason: str,
        world: World,
        ctx: MatchContext,
        *,
        is_first_rifle: bool = False,
        by_side: bool = False,
    ) -> None:
        """Move a unit into the scout group and send it on its route: the indirect way with `indirect_route` on,
        else to the enemy's start, by a side waypoint when asked."""
        ctx.groups.assign(unit_id, SCOUT)
        unit = world.units[unit_id]
        if ctx.tuning.scouting.indirect_route:
            stage, route = AROUND, scout_route.indirect_route(world, ctx, self._side, self._stale.sight)
        else:
            stage, route = APPROACH, [world.enemy_base]
            if by_side:
                route.insert(0, _side_waypoint(unit, world, ctx))
        self._mission = Mission(
            unit_id=unit_id,
            assigned_tick=world.tick,
            reason=reason,
            unit_type=unit.type,
            cell=(unit.x, unit.y),
            hp=unit.hp,
            stage=stage,
            route=route,
            leg_tick=world.tick,
            is_first_rifle=is_first_rifle,
        )
        self._has_scouted = True
        _log_scout(SENT, self._mission, ctx)

    def _hand_over_to_vehicle(self, world: World, ctx: MatchContext) -> None:
        """Let a scouting vehicle of `main` take over from the first rifle beyond the guard, which joins the guard."""
        if not self._mission.is_first_rifle:
            return
        vehicle = SCOUT_VEHICLES[world.family]
        taking_over = next((uid for uid in ctx.groups.members(MAIN) if world.units[uid].type == vehicle), None)
        if taking_over is not None:
            _log_scout(DONE, self._mission, ctx)
            ctx.groups.assign(self._mission.unit_id, GUARD)
            self._start(taking_over, INITIAL, world, ctx)

    def _lead(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The scout's next order: on along its route, or away from danger; nothing once it has been released. While
        the model commander steers it, as its standing action says."""
        tuning, mission = ctx.tuning.scouting, self._mission
        scout = world.units[mission.unit_id]
        _turn(mission, scout, world, ctx)
        commanded = self._commanded_lead(mission, scout, world, ctx)
        if commanded is not None:
            return commanded
        if _has_reached(scout, mission, world.tick, tuning):
            mission.follow(mission.stage, mission.route[1:], world.tick)
            _log_scout(WAYPOINT if mission.route else DONE, mission, ctx)
        if not mission.route:
            ctx.groups.assign(scout.id, MAIN)
            self._mission = None
            return []
        return [Intent(Priority.SCOUT, {"cmd": "move", "units": [scout.id], "to": list(mission.route[0])})]

    def _commanded_lead(self, mission: Mission, scout: Unit, world: World, ctx: MatchContext) -> list[Intent] | None:
        """While the model commander steers the scout in the field: its question of the cycle put and the answer that
        arrived taken; then, while the standing action is in force and the scout is not withdrawing from danger,
        which overrides the model, the scout's orders by that action, the commander's order among them when it is due.
        None while the scout follows its mission."""
        commander, sight = ctx.commander, self._stale.sight
        if commander is None or commander.role != SCOUT_ROLE:
            return None
        offered = self._planner.missions(world, ctx, sight)
        dangers = Dangers(world, ctx)
        current = commander.current((scout.x, scout.y))
        question = command_scout.question(scout, dangers, sight, offered, commander.picks, world, ctx, current)
        action = commander.take(SCOUT_ROLE, question.actions, world, command_scout.situation(scout, dangers, ctx))
        commander.ask(SCOUT_ROLE, question, world)
        if action is None or mission.stage == RETURN or not _is_in_force(action, commander.taken_tick, world):
            return None
        order = _commanded_order(scout, action, ctx)
        intent = commander.order(SCOUT_ROLE, order, world)
        moves = [] if order is None or order["cmd"] == STOP else [Intent(Priority.SCOUT, order)]
        return ([] if intent is None else [intent]) + moves


def _is_in_force(action: Action, taken_tick: int, world: World) -> bool:
    """Whether a standing action steers the scout in this cycle: a destination always, a hold for its seconds."""
    return action.cell is not None or command_scout.is_held(action, taken_tick, world)


def _commanded_order(scout: Unit, action: Action, ctx: MatchContext) -> Order | None:
    """The order a commanded scout's standing action stands for: a stop while held, else a move to the action's cell;
    none once it stands there, watching."""
    if action.cell is None:
        return {"cmd": STOP, "units": [scout.id]}
    if cells.is_within((scout.x, scout.y), action.cell, ctx.tuning.scouting.reached_cells):
        return None
    return {"cmd": "move", "units": [scout.id], "to": list(action.cell)}


def _turn(mission: Mission, scout: Unit, world: World, ctx: MatchContext) -> None:
    """Change a mission's way for what its scout meets: away from danger, round the enemy base once there, or past
    the waypoint it was heading to when it is hit hard on the indirect way."""
    tuning = ctx.tuning.scouting
    hp_before, mission.hp = mission.hp, scout.hp
    if mission.stage != RETURN and _is_in_danger(scout, world, ctx):
        withdrawn = cells.clamped(
            cells.toward((scout.x, scout.y), world.base_center, tuning.withdraw_cells), world.map_bounds
        )
        mission.follow(RETURN, [withdrawn, _staging_point(world, ctx)], world.tick)
    elif mission.stage == APPROACH and cells.is_within((scout.x, scout.y), world.enemy_base, tuning.arrival_cells):
        mission.follow(
            CIRCLE, [*_circle(world.enemy_base, world.map_bounds, tuning), _staging_point(world, ctx)], world.tick
        )
    elif mission.stage == AROUND and hp_before - scout.hp >= tuning.scout_hit_hp_drop:
        mission.follow(AROUND, _away_from_attacker(scout, mission.route, world), world.tick)


def _away_from_attacker(scout: Unit, route: list[Cell], world: World) -> list[Cell]:
    """The route on from the first waypoint after the next that lies farther from the attacker, the nearest enemy in
    sight, than the scout stands; from the last, home, when none does. Without an enemy in sight, the route after the
    next waypoint; the route as it is when the next waypoint is the last."""
    if len(route) <= 1:
        return route
    rest, position = route[1:], (scout.x, scout.y)
    in_sight = [(record.x, record.y) for record in world.enemy_memory.values() if record.visible]
    if not in_sight:
        return rest
    attacker = min(in_sight, key=lambda cell: cells.distance(cell, position))
    standing = cells.distance(attacker, position)
    farther = next(
        (number for number, waypoint in enumerate(rest) if cells.distance(waypoint, attacker) > standing),
        len(rest) - 1,
    )
    return rest[farther:]


def _ending(mission: Mission, world: World, ctx: MatchContext) -> str | None:
    """How a mission with `rescout` on ends now: LOST when base defense has the scout under its override, DONE once it
    has lasted scout_mission_max_seconds; None while it goes on."""
    if ctx.groups.is_overridden(mission.unit_id):
        return LOST
    is_over = world.tick - mission.assigned_tick >= ctx.tuning.scouting.scout_mission_max_seconds * TICKS_PER_SECOND
    return DONE if is_over else None


def _is_map_known(world: World) -> bool:
    """Whether the world knows where both bases are and the bounds of the map, which every route is drawn on."""
    return world.enemy_base is not None and world.base_center is not None and world.map_bounds is not None


def _circle(centre: Cell, bounds: cells.Bounds, tuning: ScoutingTuning) -> list[Cell]:
    """The four points a scout circles a base through: north, east, south and west of it, inside the map."""
    x, y = centre
    radius = tuning.circle_radius_cells
    return [
        cells.clamped(point, bounds) for point in ((x, y - radius), (x + radius, y), (x, y + radius), (x - radius, y))
    ]


def _is_in_danger(scout: Unit, world: World, ctx: MatchContext) -> bool:
    """Whether a scout must withdraw: a remembered enemy defense covers its cell, or it has lost half its health."""
    tuning = ctx.tuning
    is_covered = coverage.is_covered((scout.x, scout.y), world.known_enemy_defenses, world.items, tuning.coverage)
    return is_covered or scout.hp < tuning.scouting.withdraw_below_hp


def _has_reached(scout: Unit, mission: Mission, tick: int, tuning: ScoutingTuning) -> bool:
    """Whether the scout is at the first cell of its route, or has stood idle there so long that it cannot get there."""
    is_there = cells.is_within((scout.x, scout.y), mission.route[0], tuning.reached_cells)
    return is_there or (scout.idle and tick - mission.leg_tick >= tuning.stuck_ticks)


def _first_rifle_beyond_the_guard(world: World, ctx: MatchContext) -> int | None:
    """The first rifle infantry that joined `main`: the first one trained beyond the home guard's."""
    return next((unit_id for unit_id in ctx.groups.members(MAIN) if world.units[unit_id].type == RIFLE_INFANTRY), None)


def _preferred_scout(world: World, ctx: MatchContext) -> int | None:
    """The jeep, else the flak truck, else the rifle (the first of a type) in no group a scout is never taken from
    that stands idle or waits with main; None when there is none."""
    groups = ctx.groups
    candidates = [
        unit
        for unit in world.combat_units.values()
        if unit.type in RESCOUT_TYPES and groups.group_of(unit.id) not in _NOT_SCOUTING and _is_waiting(unit, ctx)
    ]
    chosen = min(candidates, key=lambda unit: (RESCOUT_TYPES.index(unit.type), unit.id), default=None)
    return None if chosen is None else chosen.id


def _is_waiting(unit: Unit, ctx: MatchContext) -> bool:
    """Whether a unit is free to scout: it stands idle, or it is of main and the combat manager has sent it neither
    forward nor after an enemy from the forward hold, so it waits at main's gathering point (the staging point, or
    the forward point)."""
    return unit.idle or (ctx.groups.group_of(unit.id) == MAIN and unit.id not in ctx.army)


def _cheapest_idle(world: World, ctx: MatchContext) -> int | None:
    """The cheapest idle unit that fights and is in no group a scout is never taken from; None when there is none."""
    groups = ctx.groups
    idle = [unit for unit in world.combat_units.values() if unit.idle and groups.group_of(unit.id) not in _NOT_SCOUTING]
    if not idle:
        return None
    return min(idle, key=lambda unit: (_cost(unit.type, world), unit.id)).id


def _fastest_idle(world: World, ctx: MatchContext) -> int | None:
    """The fastest idle unit that fights and is in no group a scout is never taken from; the cheaper of two as fast."""
    groups = ctx.groups
    idle = [unit for unit in world.combat_units.values() if unit.idle and groups.group_of(unit.id) not in _NOT_SCOUTING]
    if not idle:
        return None
    return min(idle, key=lambda unit: (-_speed(unit.type, world), _cost(unit.type, world), unit.id)).id


def _side_waypoint(scout: Unit, world: World, ctx: MatchContext) -> Cell:
    """The cell to one side of the scout's direct line to the enemy's start that no remembered defense covers.

    The first side when both or neither are covered.
    """
    tuning = ctx.tuning
    sides = raid_routes.side_waypoints(
        (scout.x, scout.y), world.enemy_base, world.map_bounds, tuning.scouting.side_offset_cells
    )
    defenses = world.known_enemy_defenses
    clear = [side for side in sides if not coverage.is_covered(side, defenses, world.items, tuning.coverage)]
    return (clear or sides)[0]


def _speed(item: str, world: World) -> float:
    facts = world.items.get(item)
    return facts.speed if facts is not None and facts.speed is not None else 0.0


def _cost(item: str, world: World) -> int:
    facts = world.items.get(item)
    return facts.cost if facts is not None else 0


def _log_scout(phase: str, mission: Mission, ctx: MatchContext) -> None:
    """Write a `scout` event of a mission, with what is known of the enemy, while `rescout` or `indirect_route` is
    on."""
    tuning = ctx.tuning.scouting
    if tuning.rescout or tuning.indirect_route:
        records.log(
            SCOUT_EVENT,
            {
                "phase": phase,
                "reason": mission.reason,
                "unit_type": mission.unit_type,
                "cell": list(mission.cell),
                "seen": _seen(ctx.scout_report),
            },
        )


def _seen(report: ScoutReport) -> dict[str, object]:
    """What a `scout` event says is known of the enemy: its buildings by type, its army's worth and its defenses."""
    return {
        "buildings": dict(report.enemy_buildings),
        "army_value": report.enemy_combat_value,
        "defenses": report.enemy_defense_count,
    }


def _log_pause(tuning: ScoutingTuning) -> None:
    """Say in the match record that scouts keep being lost before they see anything, and that scouting pauses."""
    records.log(
        "error",
        {
            "where": _ERROR_SOURCE,
            "type": records.HANDLED_ERROR,
            "exception": "ScoutsLost",
            "message": (
                f"{tuning.blind_losses_before_pause} scouts in a row were lost before an enemy building was seen; "
                f"no scout is sent for {tuning.pause_seconds} s"
            ),
        },
    )


def _staging_point(world: World, ctx: MatchContext) -> Cell:
    return cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells)
