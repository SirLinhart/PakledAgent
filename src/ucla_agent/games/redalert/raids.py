"""Raids: hit-and-run strikes on the enemy's economy by a small, fast group.

The raid manager runs last, after combat. It forms and directs the group
`raid` while the plan in force raids: RAID, or PRESS, which raids alongside
main's attack. A group that took long to form is not broken up for FINISH or
HOLD: through either, however long it lasts, it stays the raid group, full or
half formed, and waits at the staging point, and it raids again when a
raiding plan comes back. Under any other plan there is no raid group, and the
units it held go back to main at the staging point. With raids switched off
(`[raids].enabled`) there is no raid group under any plan.

A standing raid group (`[raids].standing`) raids alongside main whatever main
does: under every plan after the opening but PRESERVE, that is HOLD, RAID,
PRESS, STRIKE and FINISH. Under PRESERVE, with the lead to keep, a raid lost
would be a loss the enemy scores: a raid under way is called back, and the
group waits at the staging point, kept, until a raiding plan comes back.
Under OPENING there is no raid group.

The raid group is made of a fixed quota of fast units of the agent's family.
The Allies raid with four light tanks, or four jeeps when no light tank can
be had; with `allied_raid_light_tanks` they raid with `raid_vehicles` light
tanks and nothing else, from the time a war factory stands, and a jeep is
never trained for the raid nor taken into it. The Soviets raid on foot with
six grenadiers, and once a war factory stands, with three grenadiers and
`raid_mixed_vehicles` flak trucks, none as the tuning file ships; with
`soviet_raid` at "none" they do not raid at all, and with "apc_rockets"
they raid with `raid_apcs` APCs carrying `raid_apc_rockets` rocket soldiers
each, from the time a war factory and a barracks stand; a rocket aboard an
APC of the group counts toward the quota; it boards at the staging point and
rides to its target (see `apc_raid`). While the world gives the family no
quota there is no raid group. Heavy tanks never raid: they are too
slow to run. The production rule of the RAID plan makes the
quota's units; the raid manager takes them from main as they come, the
nearest to the staging point first, but never a unit of main's army that has
gone forward and never a damaged one. The home guard is never touched, so a
raid never weakens the base. A unit of a type the quota no longer asks for
(the quota changes when the war factory is built or lost) goes back to main.
The production rule counts toward the quota only the units the raid can have
(`quota_units`): the raid group's own, and those of main it could take at
once, fit and at the staging point. A unit of main gone forward or damaged is
not counted, so production does not stop while the group cannot be filled.
And what is made for the raid goes to it: while the raid manager forms or
leads the group, a new unit of a type the group is short of joins it the
moment it appears (see `groups`), before the combat manager, which runs
first, can send it after main's army. One that joins while a raid is under
way waits for the raid at the staging point.

The group gathers at the staging point and is ready once it holds its full
quota there. A ready group is sent at an enemy harvester, or failing one for
long, at a refinery it can destroy quickly, that no remembered enemy
defense covers (see `raid_targets`), by a way that crosses no covered ground
(see `raid_routes`; with `waypoints` of [routing] on, through the graph of
waypoints when the straight line is covered); while there is no such target,
it stays. It moves together to each cell of its way in turn, and from the
approach cell attacks its target while the target is in sight, or moves on
its last known cell with `attack_move` while it is not. Each way through the
graph a raid sets out on, or goes on to its next target by, is written as a
`route` event (see `waypoints`).

A raid runs; it does not fight it out. In every cycle it breaks off as soon as
any of these holds: the estimate of its fight is below 0.7, its units are
down to half their health on average, a unit of it stands on ground a
remembered defense covers, it has lost half the units it set out with, or it
has stood at its approach cell for ten seconds without its target in sight.
The estimate's threshold (`raid_break_off_ratio`) is below the one main
retreats at: by the enemy's harvesters a raid nearly always meets some of
the enemy, and a raid that turned from every fight about even would come
home without a kill.
A raid that breaks off goes back to the staging point and is not sent out
again for twenty seconds; its units below 60% health leave it for main, and
it is filled up again before it goes. A raid that has destroyed its target
goes on to the next one without going back, as long as nothing calls for it
to break off. A target is destroyed on the evidence the world model takes for
it: an enemy it has forgotten, or one among those it saw killed in sight
(`World.killed_in_sight`), which it forgets in the same update. The raid
remembers the targets it destroyed all the same and never goes after them
again.

Each raid is written to the match record as two `raid` events: one when it
sets out (its target and the target's type, how many units it has and what
they cost), and one when it ends (why, how many targets it destroyed, and how
many of its units it lost and what they cost); the events of an APC raid say
too how many APCs and rockets set out and how many of the rockets were lost.
It ends when it breaks off, when it has destroyed its target and no other is
left to go on to, or when the plan stops raiding; an APC raid also when base
defense calls the group home (`recalled`), and only once its APCs leave for
the staging point with the rockets aboard.

With `raid_exposed_trucks` on, the raid opportunities scouting notes
(`ctx.exposed_trucks`, see `exposed_trucks`) launch raids of the raid
manager's own, whether the script or the model commands main: enemy
harvesters seen lately (`exposed_raid_seen_seconds`) by a guard worth little
(`exposed_raid_guard_max`) are income a raid can take before help comes. While
no raid is out, a ready group sets out at the harvester of the freshest such
opportunity a safe way leads to (`raid_targets.exposed`), before any target
`raid_targets.ranked` gives, by the way any raid takes. Under HOLD and FINISH,
the plans that keep the raid group, it raids on exposed trucks alone: it is
formed from the units on hand, as `launch_blocker` counts them for RAID, only
while such a raid could set out now, and a raid on exposed trucks under way
goes on through them. Under the plans that have no raid group it launches
none. Its `raid` start event gives the reason `exposed_trucks`. It breaks
off, goes on after a kill and comes back as any raid does.

With `launchable_only`, RAID is open only while a raid could set out now
(`launch_blocker`), which the plan rules ask before they open it: a raid
target is known; the search a ready group makes for one (`_aim`), from the
staging point, finds a target the raids have not destroyed yet with a safe
way to it; and the units on hand fill the quota. The units on hand of a type
are the raid group's own, wherever they are, and those of main that the
raid could borrow now (`on_hand`): fit, neither gone forward with main's
army nor chasing, in no fight, and within `borrow_home_cells` of the base's
center or of the staging point; never more than the quota wants of the
type. The home guard and the escort are groups of their own, so none of
theirs ever counts. And the raid takes from main those units on hand, the
nearest to the staging point first, and no other: when RAID is chosen, the
ones the plan rules counted join the group in that same cycle, and the raid
sets out as soon as they have gathered. When no raid has set out
`launch_within_ticks` after RAID was chosen, the raid group goes back to
main as it does out of a raiding plan, and RAID is shut, "raid did not set
out", for `resend_after_ticks`: a RAID chosen is a raid that sets out, or
RAID closes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, coverage, raid_routes, raid_targets, targeting, waypoints
from ucla_agent.games.redalert.apc_raid import (
    OUT,
    RELOADING,
    RIDING,
    Boarding,
    Ride,
    Unloads,
    apcs_of,
    at_point,
    is_loaded,
    is_unfit,
    rockets_of,
    unload_route,
)
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.cargo import UNLOAD, CargoUnit
from ucla_agent.games.redalert.composition import FINISH, HOLD, PRESERVE, raid_quota, raiding_plans
from ucla_agent.games.redalert.composition import RAID as RAID_PLAN
from ucla_agent.games.redalert.family import APC
from ucla_agent.games.redalert.fight import centroid, estimate, is_near
from ucla_agent.games.redalert.groups import MAIN, RAID
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.tuning import CompositionTuning, RaidTuning
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

if TYPE_CHECKING:
    # The match context builds its composition provider on `quota_units`, so it is named here for the types only.
    from ucla_agent.games.redalert.managers import MatchContext

MANAGER_NAME = "raids"
# The plans through which the raid group is kept, waiting, when a raiding plan gives way to them, and those through
# which a standing raid group is.
KEEPING_PLANS = frozenset({FINISH, HOLD})
STANDING_KEEPING_PLANS = frozenset({PRESERVE})
# The kind of the match record's events of a raid, and the two phases of a raid they record.
RAID_EVENT = "raid"
START, END = "start", "end"
# Why a raid set out at its target, when it was not the ranking's choice: an opportunity of exposed trucks.
EXPOSED_TRUCKS = "exposed_trucks"
# Why a raid ends: its break-off rules, ...
LOSSES, HEALTH, OUTMATCHED, COVERED, TARGET_UNSEEN = "losses", "health", "outmatched", "covered", "target_unseen"
# ... no target left after a kill, and the plan no longer raiding; and an APC raid called home by base defense.
NO_TARGET, PLAN_CHANGED, RECALLED = "no_target", "plan_changed", "recalled"
# Why a raid could not set out now, in the words the plan record and the briefing give (see `launch_blocker`); the
# shortfall of units goes on with what the quota needs and what is on hand.
NOT_SET_OUT = "raid did not set out"
NO_TARGET_KNOWN = "no raid target known"
NO_SAFE_ROUTE = "no safe route to a raid target"
NO_UNITS_ON_HAND = "no units for a raid on hand"


def quota(world: World, tuning: CompositionTuning) -> dict[str, int]:
    """How many units of each type the raid group of the agent's family is made of, as the world stands; none while
    it has no raid (see `composition.raid_quota`)."""
    return raid_quota(world.family, world, tuning)


def quota_units(ctx: MatchContext, world: World, unit_type: str) -> int:
    """How many units of a type count toward the raid quota: the raid group's, and those it could take from main now.

    A unit of main counts only while the raid could take it at once: fit, not gone forward with main's army, and
    within `gathered_cells` of the staging point.
    """
    tuning = ctx.tuning.raids
    point = cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells)
    at_hand = 0
    if point is not None:
        candidates = _fit_in_main(unit_type, world, ctx)
        at_hand = sum(1 for unit in candidates if is_near(point, unit.x, unit.y, tuning.gathered_cells))
    return ctx.groups.count(RAID, unit_type) + at_hand


def launch_blocker(world: World, ctx: MatchContext, target_known: bool) -> str | None:
    """Why a raid could not set out now, the first that holds of: no raid set out in time after RAID was last chosen
    (RAID then stays shut until `ctx.raid_shut_until`), no raid target known (`target_known` says whether
    `raid_targets` gives one), no safe way from the staging point to a target the raids have not destroyed for the
    units on hand, and too few units on hand for the quota, with what it needs and what there is; None when one could.

    The way is looked for as a ready group looks for it (`_aim`), from the staging point; for an APC raid, by the way
    that ends where the APCs unload.
    """
    if world.tick < ctx.raid_shut_until:
        return NOT_SET_OUT
    if not target_known:
        return NO_TARGET_KNOWN
    tuning = ctx.tuning
    point = cells.staging_point(world, tuning.upkeep.staging_distance_cells)
    if point is None:
        return NO_SAFE_ROUTE
    wanted = quota(world, tuning.composition)
    counted = _counted(wanted, point, world, ctx)
    raiders = [unit for units in counted.values() for unit in units]
    route_tuning = unload_route(tuning.raids) if APC in wanted else tuning.raids
    if _aim(raiders, point, point, world, ctx, ctx.raids_destroyed, route_tuning, is_taken=False) is None:
        return NO_SAFE_ROUTE
    have = {unit_type: len(units) for unit_type, units in counted.items()}
    if all(have[unit_type] >= count for unit_type, count in wanted.items()):
        return None
    return f"{NO_UNITS_ON_HAND}: need {_quota_words(wanted, wanted)}, have {_quota_words(have, wanted)}"


def on_hand(unit_type: str, point: Cell, world: World, ctx: MatchContext) -> list[Unit]:
    """The units of a type the raid may borrow from main now, the nearest to the staging point `point` first: fit,
    neither gone forward with main's army nor chasing, in no fight, and within `borrow_home_cells` of the base's
    center or of the staging point.

    A unit is in a fight while an enemy that fights in sight, or a remembered defense, is within its weapon's range and
    two cells (`targeting.is_in_contact`): taken from a fight, it would leave main short there.
    """
    reach = ctx.tuning.raids.borrow_home_cells
    # The staging point is known only while the base's center is.
    homes = (world.base_center, point)
    in_sight = (record for record in world.enemy_memory.values() if record.visible and is_combat_unit(record))
    enemies = [*in_sight, *world.known_enemy_defenses]
    near = [
        unit
        for unit in _fit_in_main(unit_type, world, ctx)
        if any(is_near(home, unit.x, unit.y, reach) for home in homes)
        and not targeting.is_in_contact(unit, enemies, world)
    ]
    return sorted(near, key=lambda unit: cells.distance((unit.x, unit.y), point))


def _counted(wanted: dict[str, int], point: Cell, world: World, ctx: MatchContext) -> dict[str, list[Unit | CargoUnit]]:
    """The units a raid could set out with now, by the quota's types, at most the quota of each: the raid group's own,
    in the field or aboard a transport, then those of main on hand."""
    groups = ctx.groups
    own = [
        *(world.combat_units[unit_id] for unit_id in groups.members(RAID)),
        *(world.aboard(unit_id) for unit_id in groups.aboard(RAID)),
    ]
    return {
        unit_type: [*(unit for unit in own if unit.type == unit_type), *on_hand(unit_type, point, world, ctx)][:count]
        for unit_type, count in wanted.items()
    }


def _quota_words(counts: dict[str, int], wanted: dict[str, int]) -> str:
    """How many units of each of the quota's types, in its order, as "2 apc + 10 e3"; a type it wants none of is left
    out."""
    return " + ".join(f"{counts[unit_type]} {unit_type}" for unit_type, count in wanted.items() if count > 0)


@dataclass
class Mission:
    """A raid under way: its target, the cells it still has to go to, the units it set out with and its kills."""

    target: int
    # The cells still to reach, in turn, the approach cell last; empty once the raid attacks.
    legs: list[Cell]
    # The tick the raid set out for the first of `legs`, or reached its approach cell once there are none.
    leg_tick: int
    # The units the raid set out with, by id, each with what it cost; a raid going on to a next target keeps them.
    crew: dict[int, int]
    # The targets the raid has destroyed since it set out.
    kills: int = 0
    # The APCs and rockets of an APC raid, and what the rockets do; None for any other raid.
    ride: Ride | None = None
    # Whether it set out at exposed trucks, which it goes on raiding under a plan that keeps the raid group.
    exposed: bool = False

    def aim_at(self, target: EnemyRecord, legs: list[Cell], tick: int) -> None:
        """Go on to a target, by these cells, from this tick."""
        self.target, self.legs, self.leg_tick = target.id, legs, tick


class RaidManager:
    """Forms the raid group and directs it. See the module's note."""

    name = MANAGER_NAME

    def __init__(self) -> None:
        # Whether the raid group held its full quota at the staging point in the latest cycle.
        self.ready = False
        self.mission: Mission | None = None
        # No raid is sent out before this tick: the time a raid that broke off waits.
        self._resend_tick = 0
        # The targets raids have destroyed. A unit stays in the enemy memory as last seen, dead or not.
        self._destroyed: set[int] = set()
        # The APC raid's boarding orders and unload orders; see `apc_raid`.
        self._boarding = Boarding()
        self._unloads = Unloads()
        # With `launchable_only`: the plan of the latest cycle, and the tick RAID was chosen at while no raid has been
        # out under it since; None while no raid is waited for.
        self._plan_seen: str | None = None
        self._raid_chosen_tick: int | None = None

    @property
    def target(self) -> int | None:
        """The id of the enemy the raid is after; None while no raid is under way."""
        return None if self.mission is None else self.mission.target

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The raid group's orders for this cycle; none while base defense has it, but for an APC raid under way,
        which comes home aboard its APCs.

        Publishes the units of an APC raid under way, which base defense leaves to this manager when it calls the
        raid group home, and the targets raids have destroyed, which the plan rules pass over. With `launchable_only`,
        a RAID under which no raid has set out `launch_within_ticks` after it was chosen gives the raid group back to
        main and is shut for `resend_after_ticks` (`ctx.raid_shut_until`).
        """
        orders = self._give_up(world, ctx) if self._has_missed_launch(world, ctx) else self._orders(world, ctx)
        is_riding = self.mission is not None and self.mission.ride is not None
        ctx.raid_homebound = frozenset(self.mission.crew) if is_riding else frozenset()
        ctx.raids_destroyed = frozenset(self._destroyed)
        return orders

    def _has_missed_launch(self, world: World, ctx: MatchContext) -> bool:
        """With `launchable_only`, whether no raid has been out under RAID for more than `launch_within_ticks` since
        RAID was chosen; the wait starts as the plan turns to RAID and ends with a raid out or another plan."""
        tuning = ctx.tuning.raids
        was_raid, self._plan_seen = self._plan_seen == RAID_PLAN, ctx.plan
        if not tuning.launchable_only or ctx.plan != RAID_PLAN or self.mission is not None:
            self._raid_chosen_tick = None
        elif not was_raid:
            self._raid_chosen_tick = world.tick
        chosen = self._raid_chosen_tick
        return chosen is not None and world.tick - chosen > tuning.launch_within_ticks

    def _give_up(self, world: World, ctx: MatchContext) -> list[Intent]:
        """No raid set out in time under RAID: the raid group back to main by the way it goes back out of a raiding
        plan, and RAID shut for `resend_after_ticks`, which the plan rules read in the next cycle."""
        tuning = ctx.tuning
        self._raid_chosen_tick = None
        ctx.groups.set_raid_quota({})
        ctx.raid_shut_until = world.tick + tuning.raids.resend_after_ticks
        point = cells.staging_point(world, tuning.upkeep.staging_distance_cells)
        return [] if point is None else _disband(world, ctx, point)

    def _orders(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The raid group's orders for this cycle. See `intents`."""
        groups = ctx.groups
        # New units go to the raid group only while this manager forms or leads it.
        groups.set_raid_quota({})
        point = cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells)
        self.ready = False
        if point is None:
            return []
        called_home = self._called_home(world, ctx)
        if called_home is not None:
            return self._bring_home(called_home, point, world, ctx)
        if not self._raids_now(point, world, ctx):
            return self._stand_down(point, world, ctx)
        wanted = quota(world, ctx.tuning.composition)
        if not wanted:
            # No raid as the world stands, such as the Allied raid of light tanks before a war factory: no group.
            self._end(world, PLAN_CHANGED)
            return _disband(world, ctx, point)
        if groups.is_recalled(RAID) or groups.is_any_overridden(RAID):
            return []
        # A unit of the quota's types made while the group is short of it joins the group as it appears, in the next
        # cycle's bookkeeping: combat, which runs first, would otherwise send it after main's army gone forward.
        groups.set_raid_quota(wanted)
        if self.mission is None:
            self._form(wanted, point, world, ctx)
        members = [world.combat_units[unit_id] for unit_id in groups.members(RAID)]
        if self.mission is None:
            return [*_gather(members, point, ctx.tuning.raids), *self._board(wanted, members, point, world, ctx)]
        return self._lead(members, point, world, ctx)

    def _called_home(self, world: World, ctx: MatchContext) -> str | None:
        """Why an APC raid under way must come home before anything else: the plan no longer raids or the world gives
        no raid quota, or base defense has the group; None while it need not, or no APC raid is under way."""
        if self.mission is None or self.mission.ride is None:
            return None
        groups = ctx.groups
        if not self._raids_under_plan(ctx):
            return PLAN_CHANGED
        if not quota(world, ctx.tuning.composition):
            return PLAN_CHANGED
        return RECALLED if groups.is_recalled(RAID) or groups.is_any_overridden(RAID) else None

    def _raids_now(self, point: Cell, world: World, ctx: MatchContext) -> bool:
        """Whether the raid group raids in this cycle: under the plan in force (`_raids_under_plan`), or under a plan
        that keeps it while a raid on exposed trucks could set out now (`_could_launch_exposed`)."""
        return self._raids_under_plan(ctx) or self._could_launch_exposed(point, world, ctx)

    def _raids_under_plan(self, ctx: MatchContext) -> bool:
        """Whether the plan in force lets the raid group raid: a raiding plan, or, for a raid on exposed trucks under
        way, a plan that keeps the raid group with `raid_exposed_trucks` on."""
        tuning = ctx.tuning.raids
        if ctx.plan in raiding_plans(tuning.enabled, tuning.standing):
            return True
        return self.mission is not None and self.mission.exposed and _keeps_exposed_raids(ctx)

    def _could_launch_exposed(self, point: Cell, world: World, ctx: MatchContext) -> bool:
        """With no raid out and `raid_exposed_trucks` on, under a plan that keeps the raid group, whether a raid on
        exposed trucks could set out now by the rules `launch_blocker` takes for RAID: the units on hand fill the quota,
        and a way that crosses no covered cell leads from the staging point to the harvester of an opportunity."""
        if self.mission is not None or not _keeps_exposed_raids(ctx):
            return False
        tuning = ctx.tuning
        targets = raid_targets.exposed(world, ctx.exposed_trucks, tuning, self._destroyed)
        wanted = quota(world, tuning.composition)
        crew = _crew_on_hand(wanted, point, world, ctx) if targets and wanted else None
        if crew is None:
            return False
        route_tuning = _route_tuning(wanted, tuning.raids)
        aim = _aim(crew, point, point, world, ctx, self._destroyed, route_tuning, is_taken=False, targets=targets)
        return aim is not None

    def _bring_home(self, reason: str, point: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """An APC raid called home: its rockets out board again, and its APCs come home once they have."""
        crew, ride = self.mission.crew, self.mission.ride
        members = [world.combat_units[unit_id] for unit_id in ctx.groups.members(RAID) if unit_id in crew]
        if ride.phase != RELOADING:
            self._boarding.forget()
        ride.go_home(reason, world.tick)
        return self._reload(members, point, world, ctx)

    def _stand_down(self, point: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """Out of a raiding plan, or with raids off: no raid; the group waits at the staging point through the plans
        that keep it while raids are on."""
        # With raids off no raid ever sets out: a raid that ends here ends with its plan.
        self._end(world, PLAN_CHANGED)
        if ctx.plan not in _keeping_plans(ctx.tuning.raids):
            return _disband(world, ctx, point)
        groups = ctx.groups
        if groups.is_recalled(RAID) or groups.is_any_overridden(RAID):
            return []
        members = [world.combat_units[unit_id] for unit_id in groups.members(RAID)]
        away = sorted(
            unit.id for unit in members if not is_near(point, unit.x, unit.y, ctx.tuning.raids.gathered_cells)
        )
        return [Intent(Priority.MOVE, {"cmd": "move", "units": away, "to": list(point)})] if away else []

    def _form(self, wanted: dict[str, int], point: Cell, world: World, ctx: MatchContext) -> None:
        """Fill the group to its quota, and send it out once it is ready and has a target it can reach safely."""
        _keep_to(wanted, world, ctx)
        _recruit(wanted, point, world, ctx)
        members = [world.combat_units[unit_id] for unit_id in ctx.groups.members(RAID)]
        self.ready = _is_ready(members, wanted, point, world, ctx)
        if not self.ready or world.tick < self._resend_tick:
            return
        self.mission = self._send_out(wanted, members, point, world, ctx)
        if self.mission is not None:
            # A raid that sets out ends the wait, though it may break off in this same cycle.
            self._raid_chosen_tick = None

    def _send_out(
        self, wanted: dict[str, int], members: list[Unit], point: Cell, world: World, ctx: MatchContext
    ) -> Mission | None:
        """The raid the ready group sets out on: with `raid_exposed_trucks` on, at the harvester of the freshest
        opportunity a safe way leads to (`raid_targets.exposed`); else, under a raiding plan, at the most wanted target
        a safe way leads to; None while there is none. An APC raid sets out with its rockets aboard, by a way that ends
        `unload_cells` short of its target."""
        crew, start, ride = _crew_of(wanted, members, world)
        tuning = ctx.tuning.raids
        route_tuning = _route_tuning(wanted, tuning)
        if tuning.raid_exposed_trucks:
            targets = raid_targets.exposed(world, ctx.exposed_trucks, ctx.tuning, self._destroyed)
            aim = _aim(crew, start, point, world, ctx, self._destroyed, route_tuning, targets=targets)
            if aim is not None:
                return _set_out(*aim, crew, world, ride, exposed=True)
        if ctx.plan not in raiding_plans(tuning.enabled, tuning.standing):
            # A plan that keeps the raid group sends it at exposed trucks alone.
            return None
        aim = _aim(crew, start, point, world, ctx, self._destroyed, route_tuning)
        return None if aim is None else _set_out(*aim, crew, world, ride)

    def _board(
        self, wanted: dict[str, int], members: list[Unit], point: Cell, world: World, ctx: MatchContext
    ) -> list[Intent]:
        """The orders of an APC raid group that forms, at the staging point: the damaged out of it, the rockets into
        its APCs; none for another raid group. See `apc_raid`."""
        if APC not in wanted:
            return []
        tuning = ctx.tuning
        fit_hp = tuning.raids.fit_hp
        here = at_point(members, point, tuning.raids.gathered_cells)
        unfit = [apc.id for apc in apcs_of(here) if is_unfit(apc, world, fit_hp)]
        for unit in here:
            # A damaged APC leaves only once its rockets are out of it.
            if unit.hp < fit_hp and not world.cargo.get(unit.id):
                ctx.groups.assign(unit.id, MAIN)
        fit = [unit for unit in here if unit.hp >= fit_hp and unit.id not in unfit]
        boarding = self._boarding.orders(rockets_of(fit), apcs_of(fit), tuning.composition.raid_apc_rockets, world)
        carrying = [apc_id for apc_id in unfit if world.cargo.get(apc_id)]
        return [*self._unloads.orders(carrying, world.tick, tuning.loop.reissue_after_ticks), *boarding]

    def _lead(self, members: list[Unit], point: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """The orders of the group while a raid is under way: the raid's for the units it set out with, and for those
        that joined the group since, which are not sent after it alone, a wait at the staging point."""
        crew = [unit for unit in members if unit.id in self.mission.crew]
        joined = [unit for unit in members if unit.id not in self.mission.crew]
        lead = self._raid_on if self.mission.ride is None else self._ride_on
        return [*lead(crew, point, world, ctx), *_gather(joined, point, ctx.tuning.raids)]

    def _raid_on(self, members: list[Unit], point: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """The orders of a raid under way: on along its way and at its target, unless it has to break off."""
        mission = self.mission
        target = world.enemy_memory.get(mission.target)
        reason = _break_off_reason(members, [unit.hp for unit in members], mission, target, world, ctx)
        if reason is not None:
            return self._break_off(members, point, world, ctx, reason)
        if target is None or target.id in world.killed_in_sight:
            # A kill: on to the next target without going back, when there is one a safe way leads to.
            self._destroyed.add(mission.target)
            mission.kills += 1
            aim = _aim(members, _start_of(members), point, world, ctx, self._destroyed, ctx.tuning.raids)
            if aim is None:
                return self._break_off(members, point, world, ctx, NO_TARGET)
            target, legs = aim
            mission.aim_at(target, legs, world.tick)
        return _onward(members, mission, target, world, ctx.tuning.raids)

    def _ride_on(self, members: list[Unit], point: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """The orders of an APC raid under way: the APCs on along their way with the rockets aboard, the rockets out
        at the target, and the rockets aboard again before the APCs go on or home. See `apc_raid`."""
        mission = self.mission
        target = world.enemy_memory.get(mission.target)
        self._judge(members, target, point, world, ctx)
        phase = mission.ride.phase
        if phase == RELOADING:
            return self._reload(members, point, world, ctx)
        if phase == RIDING:
            return self._ride(members, target, world, ctx)
        return self._fight(members, target, world, ctx)

    def _judge(
        self, members: list[Unit], target: EnemyRecord | None, point: Cell, world: World, ctx: MatchContext
    ) -> None:
        """Have the rockets of an APC raid board again when it must break off, to go home, or when it has destroyed
        its target, to go on to the next one a safe way leads to, or home when there is none."""
        mission, ride = self.mission, self.mission.ride
        if ride.phase != RELOADING:
            # What the rockets were told as they rode or fought is no boarding: they board afresh.
            self._boarding.forget()
        apcs = apcs_of(members)
        crew_hp = _crew_hp(mission, world, ctx)
        reason = _break_off_reason(members, crew_hp, mission, target, world, ctx) if apcs else LOSSES
        if reason is not None:
            ride.go_home(reason, world.tick)
            return
        if ride.phase == RELOADING or not (target is None or target.id in world.killed_in_sight):
            return
        self._destroyed.add(mission.target)
        mission.kills += 1
        aim = _aim(members, _start_of(apcs), point, world, ctx, self._destroyed, unload_route(ctx.tuning.raids))
        if aim is None:
            ride.go_home(NO_TARGET, world.tick)
        else:
            ride.go_on(aim, world.tick)

    def _reload(self, members: list[Unit], point: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """The rockets out boarding the crew's APCs again; once every one is aboard, or the APCs have waited
        `reload_wait_ticks` for them, a rocket still out goes home alone and the APCs leave."""
        ride, tuning = self.mission.ride, ctx.tuning
        apcs, rockets = apcs_of(members), rockets_of(members)
        has_waited = world.tick - ride.reload_tick >= tuning.raids.reload_wait_ticks
        if rockets and apcs and not has_waited:
            return self._boarding.orders(rockets, apcs, tuning.composition.raid_apc_rockets, world)
        return [*_send_home(rockets, point, ctx), *self._leave(apcs, point, world, ctx)]

    def _leave(self, apcs: list[Unit], point: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """The APCs leave with the rockets aboard: on to the next target by its way, or home, where the raid ends."""
        mission, ride = self.mission, self.mission.ride
        if ride.onward is None:
            return self._break_off(apcs, point, world, ctx, ride.ending)
        target, legs = ride.onward
        mission.aim_at(target, legs, world.tick)
        ride.phase, ride.onward = RIDING, None
        return self._ride(apcs, target, world, ctx)

    def _ride(self, members: list[Unit], target: EnemyRecord, world: World, ctx: MatchContext) -> list[Intent]:
        """The APCs on to the next cell of their way together, the rockets aboard; at the way's end, the rockets out."""
        mission, apcs, tuning = self.mission, apcs_of(members), ctx.tuning.raids
        if mission.legs and _has_reached(apcs, mission, world.tick, tuning):
            mission.legs, mission.leg_tick = mission.legs[1:], world.tick
        if mission.legs:
            apc_ids = sorted(apc.id for apc in apcs)
            return [Intent(Priority.MOVE, {"cmd": "move", "units": apc_ids, "to": list(mission.legs[0])})]
        mission.ride.phase = OUT
        return self._fight(members, target, world, ctx)

    def _fight(self, members: list[Unit], target: EnemyRecord, world: World, ctx: MatchContext) -> list[Intent]:
        """The rockets out at the target: an `unload` for an APC that still carries some, their attack on the target,
        and a move to them for an APC more than `unload_cells` behind them."""
        apcs, rockets, tuning = apcs_of(members), rockets_of(members), ctx.tuning
        carrying = [apc.id for apc in apcs if world.cargo.get(apc.id)]
        unloads = self._unloads.orders(carrying, world.tick, tuning.loop.reissue_after_ticks)
        if not rockets:
            return unloads
        middle = centroid(rockets)
        behind = sorted(apc.id for apc in apcs if not is_near(middle, apc.x, apc.y, tuning.raids.unload_cells))
        to_them = list(cells.nearest_cell(*middle))
        follow = [Intent(Priority.MOVE, {"cmd": "move", "units": behind, "to": to_them})] if behind else []
        return [*unloads, *_strike(rockets, target, world), *follow]

    def _break_off(
        self, members: list[Unit], point: Cell, world: World, ctx: MatchContext, reason: str
    ) -> list[Intent]:
        """End the raid: the group back to the staging point, its damaged units out of it, a wait before the next.

        An APC that carries rockets leaves the group only once they are out of it, at the staging point.
        """
        tuning = ctx.tuning.raids
        self._end(world, reason)
        self._resend_tick = world.tick + tuning.resend_after_ticks
        for unit in members:
            if unit.hp < tuning.fit_hp and not world.cargo.get(unit.id):
                ctx.groups.assign(unit.id, MAIN)
        unit_ids = sorted(unit.id for unit in members)
        return [Intent(Priority.MOVE, {"cmd": "move", "units": unit_ids, "to": list(point)})] if unit_ids else []

    def _end(self, world: World, reason: str) -> None:
        """End the raid under way, if there is one, with a `raid` end event: why, its kills and what it lost.

        A unit it set out with is lost once the world no longer has it, in the field or aboard a transport: the
        platform reports a unit that boards as lost, though it is not.
        """
        mission, self.mission = self.mission, None
        if mission is None:
            return
        lost = [
            unit_id for unit_id in mission.crew if unit_id not in world.combat_units and world.aboard(unit_id) is None
        ]
        data = {
            "phase": END,
            "tick": world.tick,
            "game_seconds": world.game_seconds,
            "target": mission.target,
            "reason": reason,
            "kills": mission.kills,
            "units_lost": len(lost),
            "value_lost": sum(mission.crew[unit_id] for unit_id in lost),
        }
        records.log(RAID_EVENT, {**data, **_ride_figures(mission.ride, lost)})


def _set_out(
    target: EnemyRecord,
    legs: list[Cell],
    crew: list[Unit | CargoUnit],
    world: World,
    ride: Ride | None = None,
    *,
    exposed: bool = False,
) -> Mission:
    """A raid setting out at a target with this crew, by these cells, with a `raid` start event; one set out at exposed
    trucks gives the reason `exposed_trucks`."""
    costs = {unit.id: _cost(unit.type, world) for unit in crew}
    data = {
        "phase": START,
        "tick": world.tick,
        "game_seconds": world.game_seconds,
        "target": target.id,
        "target_type": target.type,
        "group_size": len(costs),
        "value": sum(costs.values()),
    }
    reason = {"reason": EXPOSED_TRUCKS} if exposed else {}
    records.log(RAID_EVENT, {**data, **_ride_figures(ride, []), **reason})
    return Mission(target.id, legs, world.tick, costs, ride=ride, exposed=exposed)


def _ride_figures(ride: Ride | None, lost: list[int]) -> dict[str, int]:
    """What a `raid` event of an APC raid says besides any raid's: how many APCs and rockets set out, and how many of
    the rockets are lost; nothing for another raid."""
    if ride is None:
        return {}
    return {"apcs": len(ride.apcs), "rockets": len(ride.rockets), "rockets_lost": len(set(lost) & set(ride.rockets))}


def _send_home(rockets: list[Unit], point: Cell, ctx: MatchContext) -> list[Intent]:
    """Rockets left out of their APCs: to the staging point alone, and out of the raid group into main."""
    for rocket in rockets:
        ctx.groups.assign(rocket.id, MAIN)
    unit_ids = sorted(rocket.id for rocket in rockets)
    return [Intent(Priority.MOVE, {"cmd": "move", "units": unit_ids, "to": list(point)})] if unit_ids else []


def _crew_hp(mission: Mission, world: World, ctx: MatchContext) -> list[int]:
    """The health of every unit a raid set out with that is still in its group: in the field as it is, aboard an APC
    as it was when it boarded."""
    groups = ctx.groups
    in_field = [world.combat_units[unit_id].hp for unit_id in groups.members(RAID) if unit_id in mission.crew]
    aboard = [world.aboard(unit_id).hp for unit_id in groups.aboard(RAID) if unit_id in mission.crew]
    return [*in_field, *aboard]


def _cost(unit_type: str, world: World) -> int:
    """What a unit of a type costs; 0 for a type the item table does not know."""
    facts = world.items.get(unit_type)
    return 0 if facts is None else facts.cost


def _onward(
    members: list[Unit], mission: Mission, target: EnemyRecord, world: World, tuning: RaidTuning
) -> list[Intent]:
    """The order of a raid on its way: to the next cell of its way, or at its target from the approach cell."""
    if mission.legs and _has_reached(members, mission, world.tick, tuning):
        mission.legs, mission.leg_tick = mission.legs[1:], world.tick
    if mission.legs:
        unit_ids = sorted(unit.id for unit in members)
        return [Intent(Priority.MOVE, {"cmd": "move", "units": unit_ids, "to": list(mission.legs[0])})]
    return _strike(members, target, world)


def _strike(members: list[Unit], target: EnemyRecord, world: World) -> list[Intent]:
    """The order of units at their target: an attack on it while it is in sight, else an attack-move on its last
    known cell. The attack is a CONTACT order once one of them is in contact with an enemy in sight; see
    `targeting`."""
    unit_ids = sorted(unit.id for unit in members)
    if target.visible:
        in_sight = [record for record in world.enemy_memory.values() if record.visible]
        priority = targeting.attack_class(members, in_sight, world)
        return [Intent(priority, {"cmd": "attack", "units": unit_ids, "target": target.id})]
    return [Intent(Priority.MOVE, {"cmd": "attack_move", "units": unit_ids, "to": [target.x, target.y]})]


def _break_off_reason(
    members: list[Unit],
    crew_hp: list[int],
    mission: Mission,
    target: EnemyRecord | None,
    world: World,
    ctx: MatchContext,
) -> str | None:
    """Why a raid must break off: losses, its health, its estimate, covered ground or its target out of sight; None
    while nothing calls for it.

    `members` are the raid's units in the field, and `crew_hp` the health of each unit it set out with that it still
    has, in the field or aboard an APC: the losses and the health are of the crew, the rest of what is in the field.
    """
    tuning = ctx.tuning
    if len(crew_hp) <= len(mission.crew) * tuning.raids.lost_share:
        return LOSSES
    if sum(crew_hp) / len(crew_hp) < tuning.raids.break_off_hp:
        return HEALTH
    found = estimate(members, world, tuning)
    if found is not None and found.ratio < tuning.raids.raid_break_off_ratio:
        return OUTMATCHED
    defenses = world.known_enemy_defenses
    if any(coverage.is_covered((unit.x, unit.y), defenses, world.items, tuning.coverage) for unit in members):
        return COVERED
    return TARGET_UNSEEN if _has_lost_sight(target, mission, world.tick, tuning.raids) else None


def _has_lost_sight(target: EnemyRecord | None, mission: Mission, tick: int, tuning: RaidTuning) -> bool:
    """Whether a raid at its approach cell has not seen its target there for `target_unseen_ticks`."""
    if target is None or target.visible or mission.legs:
        return False
    return tick - max(mission.leg_tick, target.last_seen_tick) >= tuning.target_unseen_ticks


def _aim(
    raiders: list[Unit | CargoUnit],
    start: Cell,
    point: Cell,
    world: World,
    ctx: MatchContext,
    destroyed: set[int],
    route_tuning: RaidTuning,
    *,
    is_taken: bool = True,
    targets: list[EnemyRecord] | None = None,
) -> tuple[EnemyRecord, list[Cell]] | None:
    """The most wanted target of these raiders that a safe way leads to from `start`, and the cells of that way; None
    when there is none. The targets are `targets` in their order, or else those `raid_targets.ranked` gives. Targets
    already destroyed are passed over; the way is taken by `route_tuning`, and with `waypoints` of [routing] on through
    the graph of waypoints when the straight line is covered. A way through the graph that `is_taken` is written as a
    `route` event."""
    tuning = ctx.tuning
    router = ctx.waypoints.route if tuning.routing.waypoints else None

    def is_covered(cell: Cell) -> bool:
        return coverage.is_covered(cell, world.known_enemy_defenses, world.items, tuning.coverage)

    if targets is None:
        targets = raid_targets.ranked(world, start, raiders, tuning, ctx.exposed_trucks)
    for target in (record for record in targets if record.id not in destroyed):
        way = raid_routes.route(start, point, (target.x, target.y), world.map_bounds, is_covered, route_tuning, router)
        if way is None:
            continue
        if is_taken and way.waypoint_route is not None:
            waypoints.log_route(waypoints.RAID, way.waypoint_route, world.tick)
        return target, list(way.legs)
    return None


def _start_of(units: list[Unit]) -> Cell:
    """The cell a group's way starts from: the one nearest the middle of its units."""
    return cells.nearest_cell(*centroid(units))


def _crew_of(
    wanted: dict[str, int], members: list[Unit], world: World
) -> tuple[list[Unit | CargoUnit], Cell, Ride | None]:
    """What a ready group sets out with: its crew, the cell its way starts from and, for an APC raid, its ride. The
    crew of an APC raid is its APCs and the rockets aboard them, and its way starts from the middle of the APCs."""
    if APC not in wanted:
        return list(members), _start_of(members), None
    apcs = apcs_of(members)
    aboard = [unit for apc in apcs for unit in world.cargo.get(apc.id, ())]
    ride = Ride(tuple(apc.id for apc in apcs), tuple(unit.id for unit in aboard))
    return [*apcs, *aboard], _start_of(apcs), ride


def _route_tuning(wanted: dict[str, int], tuning: RaidTuning) -> RaidTuning:
    """The raid tuning a raid of this quota takes its way by: an APC raid's ends where its APCs unload."""
    return unload_route(tuning) if APC in wanted else tuning


def _crew_on_hand(
    wanted: dict[str, int], point: Cell, world: World, ctx: MatchContext
) -> list[Unit | CargoUnit] | None:
    """The units a raid of this quota could set out with now (`_counted`); None while they do not fill it."""
    counted = _counted(wanted, point, world, ctx)
    if any(len(counted[unit_type]) < count for unit_type, count in wanted.items()):
        return None
    return [unit for units in counted.values() for unit in units]


def _keeps_exposed_raids(ctx: MatchContext) -> bool:
    """Whether, with raids and `raid_exposed_trucks` on, the plan in force is one that keeps the raid group, under
    which it raids on exposed trucks alone."""
    tuning = ctx.tuning.raids
    return tuning.enabled and tuning.raid_exposed_trucks and ctx.plan in KEEPING_PLANS


def _has_reached(members: list[Unit], mission: Mission, tick: int, tuning: RaidTuning) -> bool:
    """Whether the raid is at the cell it is on its way to, or has stood still on the way long enough to give it up."""
    middle = centroid(members)
    leg = mission.legs[0]
    is_there = is_near(middle, leg[0], leg[1], tuning.reached_cells)
    has_stopped = all(unit.idle for unit in members) and tick - mission.leg_tick >= tuning.leg_patience_ticks
    return is_there or has_stopped


def _keeping_plans(tuning: RaidTuning) -> frozenset[str]:
    """The plans through which the raid group is kept at the staging point: none with raids off, PRESERVE for a
    standing raid group, else FINISH and HOLD."""
    if not tuning.enabled:
        return frozenset()
    return STANDING_KEEPING_PLANS if tuning.standing else KEEPING_PLANS


def _disband(world: World, ctx: MatchContext, point: Cell) -> list[Intent]:
    """Give every unit of the raid group back to main, those aboard its APCs too, and send those away from the staging
    point there; an APC of it that carries rockets lets them out where it stands."""
    groups = ctx.groups
    members = [world.combat_units[unit_id] for unit_id in groups.members(RAID)]
    for unit_id in groups.roster(RAID):
        groups.assign(unit_id, MAIN)
    carrying = sorted(unit.id for unit in members if world.cargo.get(unit.id))
    away = [
        unit.id
        for unit in members
        if unit.id not in carrying and not is_near(point, unit.x, unit.y, ctx.tuning.raids.gathered_cells)
    ]
    moves = [Intent(Priority.MOVE, {"cmd": "move", "units": away, "to": list(point)})] if away else []
    return [*moves, Intent(Priority.MOVE, {"cmd": UNLOAD, "units": carrying})] if carrying else moves


def _keep_to(wanted: dict[str, int], world: World, ctx: MatchContext) -> None:
    """Give back to main the members the quota does not ask for: of another type, or more than it wants of one."""
    groups, kept = ctx.groups, dict.fromkeys(wanted, 0)
    for unit_id in groups.members(RAID):
        unit_type = world.combat_units[unit_id].type
        if kept.get(unit_type, 0) < wanted.get(unit_type, 0):
            kept[unit_type] += 1
        else:
            groups.assign(unit_id, MAIN)


def _recruit(wanted: dict[str, int], point: Cell, world: World, ctx: MatchContext) -> None:
    """Take from main the units the quota is short of, the nearest to the staging point first (see `_recruits`)."""
    groups = ctx.groups
    for unit_type, count in wanted.items():
        short = count - groups.count(RAID, unit_type)
        for unit in _recruits(unit_type, point, world, ctx)[: max(short, 0)]:
            groups.assign(unit.id, RAID)


def _recruits(unit_type: str, point: Cell, world: World, ctx: MatchContext) -> list[Unit]:
    """The units of a type the raid may take from main, the nearest to the staging point first: the fit ones not in
    the army, wherever they stand; with `launchable_only`, those on hand (`on_hand`), the ones the plan rules counted
    when they opened RAID, so that the raid sets out as soon as they have gathered."""
    if ctx.tuning.raids.launchable_only:
        return on_hand(unit_type, point, world, ctx)
    return sorted(_fit_in_main(unit_type, world, ctx), key=lambda unit: cells.distance((unit.x, unit.y), point))


def _fit_in_main(unit_type: str, world: World, ctx: MatchContext) -> list[Unit]:
    """The units of a type the raid may take from main, wherever they stand: fit, and not in main's army."""
    fit_hp = ctx.tuning.raids.fit_hp
    members = (world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN) if unit_id not in ctx.army)
    return [unit for unit in members if unit.type == unit_type and unit.hp >= fit_hp]


def _is_ready(members: list[Unit], wanted: dict[str, int], point: Cell, world: World, ctx: MatchContext) -> bool:
    """Whether the group holds its full quota, every unit of it within reach of the staging point; the APC raid group
    once its APCs there hold their rockets."""
    tuning = ctx.tuning
    gathered = all(is_near(point, unit.x, unit.y, tuning.raids.gathered_cells) for unit in members)
    if APC in wanted:
        return gathered and is_loaded(apcs_of(members), wanted[APC], tuning.composition.raid_apc_rockets, world)
    return gathered and len(members) == sum(wanted.values())


def _gather(members: list[Unit], point: Cell, tuning: RaidTuning) -> list[Intent]:
    """A `move` to the staging point for the members that stand idle away from it."""
    astray = [unit.id for unit in members if unit.idle and not is_near(point, unit.x, unit.y, tuning.gathered_cells)]
    return [Intent(Priority.MOVE, {"cmd": "move", "units": astray, "to": list(point)})] if astray else []
