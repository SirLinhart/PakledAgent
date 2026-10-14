"""The opening: a rehearsed build that is the same in every match.

The first minutes of a match are not the time to decide anything. The opening
manager plays a fixed build with the names of the agent's own family: the MCV
is deployed where it starts, and then the Building queue makes a power plant,
the barracks, two refineries, a second power plant and the war factory, one
after the other. No model is asked and nothing is weighed. The tuning file
may name another order of the build, which makes the first refinery before
the barracks; the order is the same for the whole match.

Or it may name the order that looks at the map once. A refinery that cannot
stand against its ore field leaves its harvesters a long drive on every trip,
and the engine lets a building stand only close to one that stands. So with
the power plant and the barracks standing or on their way, the opening asks
once whether a refinery could stand against the field the first refinery is
to serve. When it could, or when no field is in reach, the build goes on as
the barracks-first one. When it could not, one more power plant comes first,
placed toward that field to bring the base closer to it, and the build goes
on from there. One `opening_reach` event in the match record says how this
was decided; the build is fixed from then on.

Or it may name the order that puts the war factory first: a power plant, the
barracks, a refinery at the home field, the war factory, the second refinery
and the service depot. The war factory comes early for the scout vehicle the
scout slots order as soon as it stands (see `scout_slots`), and a second
power plant only when the power rules call for one. The barracks still comes
right after the first power plant, for one rifleman: under this order the
opening's only infantry until the depot stands is that one rifleman, trained
as soon as the barracks offers it, and the first scout slot takes it as the
first infantry the base produces. The guard's rifles start once the depot
stands, which completes this build.

With the tuning's `single_scout_rifle` on, the one rifleman is the rule under
every order: once a barracks stands the opening trains one rifleman, the
first scout, and holds every other infantry of its own back (the guard's
rifles, lean or full, and the rifles for `main`) while it keeps the Infantry
queue claimed, until a release: the rush watch's warning or its rush
verdict, a threat at the base, or the service depot standing. Released, it
stays released, and the guard is raised as below. The rocket cover against
armor is never held, and neither is base defense's own infantry. Whether the
hold is on is published in the match context for the scout slots, which
then replace a lost scout rifleman once and keep their other slots for the
faster scouts (see `scout_slots`).

The build is a list of what should stand, not a list of orders to send once.
In every cycle the manager orders the first step that neither stands nor is
on its way, when that step is on offer and the Building queue is free. An
order that was lost is therefore simply ordered again, and a step the base
already has is passed over. The opening never cancels what it started: when
money runs short, production waits. Money, here as in every rule of play, is
what the game can spend: the cash and the ore the refineries hold together.

A building is not ordered when its drain would take the power headroom below
zero; a power plant is ordered in its place. And while the base is under
attack the opening starts nothing new, except a power plant when the base is
short of power.

The build is complete when its last building stands: the war factory, or
the service depot under the factory-first order. What is built after that
is not for the opening to say. Until then the opening publishes the build in
force in every cycle, so that placement knows when a refinery is the next
step.

Next to the build, on the Infantry queue, the opening raises the home guard:
rifle infantry that stay by the base whatever the army does, so that a rush
never meets an empty base. Each guard unit is sent to the guard post, a few
cells in front of the construction yard on the enemy's side, and sent back
there when it has strayed. With the guard complete and the build still under
way, the queue trains a few more rifles for `main`, one at a time and only
while money is plentiful: the buildings come first.

The opening claims the Infantry queue while it has orders of its own for it,
so that routine production does not crowd the guard out, and gives it up
when it has none: with the build complete and the guard at strength. Once
the cover against armor is chosen as well, and the guard holds what that
cover asks of it, the opening is done with the queue for good; a guard unit
lost after that is made up by the next one routine production trains. With
`defense_on_threat` on the cover may never be called for, so the opening is
done with the queue as soon as the build is complete and the guard at
strength, and a cover of rocket infantry chosen later takes it back.

On the Defense queue, which works alongside the Building queue and never
holds a step of the build back, the opening builds one defense against
infantry as soon as the barracks makes it available. It is built once: a
defense that is destroyed later is a matter for the defense of the base, not
for the opening.

A defense against a rush that never comes is money the economy did not get.
So with the tuning's `defense_on_threat` on, the defense against infantry
waits until something calls for it: the rush watch telling a rush, the watch
still unable to tell at its deadline because the enemy base has not been
seen (only with `defense_on_unknown` on as well: a rush guessed is not a
threat seen), or base defense reporting a threat while the build is under way. Once
called for, it stays called for; where it stands and when it may be started
are as without the switch, so a threat seen holds it back until it is over.
With no call, no defense against infantry is bought.

Once a rush is told, or a threat reaches the base before the war factory
stands, the rush counter (see `rush_counter`) has the Defense queue's
defense against infantry: two of them back to back, whatever the threat,
after which the opening's own rule takes it back.

Infantry and a pillbox do not stop tanks. So at a fixed time, or as soon as
an enemy vehicle or a building that makes vehicles is seen, the opening adds
cover against armor. Which cover is decided once, by what the faction can
have at that moment: a gun where there is one and power for it, else rocket
infantry for the home guard, and when not even that is on offer, a second
defense against infantry, with a line in the match record that says so. The
Soviets have no gun for the opening: their defense against armor, the Tesla
coil, costs and drains too much for the first minutes, and is upkeep's.

With `defense_on_threat` on, neither the time nor the sight of an enemy war
factory brings the cover: armor seen somewhere far off is not armor at the
base. The cover falls due when the rush watch tells a rush and enemy vehicles
that fight have been seen, when such a vehicle is seen within
`armor_near_base_cells` of the base, or when base defense reports a major
threat. Without one of these no cover is bought, and the guard's rifles
stand all the same. And a cover that is the gun waits for an enemy vehicle
that fights seen within `armor_near_base_cells` of the base: a gun is armor's
answer at the base, not a guess at what a rush may bring.

Infantry that waits by the base is money the economy and the tanks did not
get. With the tuning's `lean_infantry` on, the guard holds
`lean_guard_rifles` rifles, no rifles are trained for `main`, and the cover
against armor is never rocket infantry: it is the gun, else a second defense
against infantry. A rush is what the guard is for, so once the rush watch
tells one, the guard's full count, the rifles for `main` and the rocket cover
come back for the rest of the match, and an opening that was done with the
Infantry queue takes it back for the guard.
"""

from __future__ import annotations

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, placement
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.family import (
    CONSTRUCTION_YARD,
    FAMILIES,
    MCV,
    POWER_PLANT,
    REFINERY,
    RIFLE_INFANTRY,
    ROCKET_INFANTRY,
    SERVICE_DEPOT,
    WAR_FACTORY,
    Family,
)
from ucla_agent.games.redalert.groups import GUARD, MAIN
from ucla_agent.games.redalert.items import NON_COMBAT_TYPES
from ucla_agent.games.redalert.managers import MAJOR_THREAT, MatchContext, OpeningBuild
from ucla_agent.games.redalert.production import (
    BUILDING_QUEUE,
    DEFENSE_QUEUE,
    INFANTRY_QUEUE,
    VEHICLE_QUEUE,
    Production,
)
from ucla_agent.games.redalert.rush_counter import RushDefenses
from ucla_agent.games.redalert.rush_watch import RUSH, UNKNOWN
from ucla_agent.games.redalert.tuning import (
    BARRACKS_FIRST,
    FACTORY_FIRST,
    REACH_FIRST,
    REFINERY_FIRST,
    OpeningTuning,
)
from ucla_agent.games.redalert.world import Cell, EnemyRecord, World

# What the cover against armor can be made of.
_GUN = "gun"
_ROCKETS = "rockets"
_SECOND_DEFENSE = "second_defense"
# The enemy buildings that mean vehicles are coming.
_ARMOR_BUILDINGS = frozenset({WAR_FACTORY, SERVICE_DEPOT})
_ERROR_SOURCE = "redalert.opening"
# How many steps of the reach-first build come before the reach decision: the power plant and the barracks.
_STEPS_BEFORE_REACH = 2
# The match record's event for the reach decision of the reach-first build.
OPENING_REACH_EVENT = "opening_reach"


def build_steps(names: Family, order: str = BARRACKS_FIRST, reaches: bool = True) -> tuple[str, ...]:
    """What the Building queue makes in the opening, in order, for a family and an order of the build.

    The barracks-first and refinery-first builds are made of the same six buildings; only the two after the first
    power plant change places. The reach-first build is the barracks-first one when the first refinery `reaches` its
    ore field, and holds one more power plant, right after the barracks, when it does not. The factory-first build
    makes the war factory before the second refinery, holds no second power plant (the power rules order one when
    power is short) and ends with the service depot.
    """
    if order == FACTORY_FIRST:
        return (POWER_PLANT, names.barracks, REFINERY, WAR_FACTORY, REFINERY, SERVICE_DEPOT)
    if order == REACH_FIRST:
        toward_ore = () if reaches else (POWER_PLANT,)
        return (POWER_PLANT, names.barracks, *toward_ore, REFINERY, REFINERY, POWER_PLANT, WAR_FACTORY)
    second_and_third = {BARRACKS_FIRST: (names.barracks, REFINERY), REFINERY_FIRST: (REFINERY, names.barracks)}
    return (POWER_PLANT, *second_and_third[order], REFINERY, POWER_PLANT, WAR_FACTORY)


def is_build_complete(production: Production, order: str) -> bool:
    """Whether the last building of the opening's build under an order stands: the war factory, or the service depot
    under factory_first."""
    return production.standing(SERVICE_DEPOT if order == FACTORY_FIRST else WAR_FACTORY) > 0


class OpeningManager:
    """Plays the opening build. See the module's note."""

    name = "opening"

    def __init__(self) -> None:
        # The tick the deploy order last went out at.
        self._deploy_sent_tick: int | None = None
        self._is_build_complete = False
        # The opening has no more orders of its own for the Infantry queue, unless a cover of rockets is chosen later.
        self._is_infantry_done = False
        # Guard unit id -> the tick it was last sent to the guard post at.
        self._sent_to_post: dict[int, int] = {}
        # Whether the defense against infantry has been called for (with `defense_on_threat` off, from the first
        # cycle on); and the most defenses against infantry that have stood at once.
        self._is_defense_called = False
        self._defenses_seen = 0
        # The rush counter's two defenses against infantry (see `rush_counter`).
        self._rush_defenses = RushDefenses()
        # The cover against armor: whether it has fallen due, what it is made of once that is decided, and
        # whether the gun it may consist of has stood; and, with `defense_on_threat` on, whether an enemy vehicle seen
        # near the base has called for that gun.
        self._is_cover_due = False
        self._is_gun_called = False
        self._cover: str | None = None
        self._has_gun_stood = False
        # Under the reach-first build: whether the first refinery could stand against its ore field, once decided.
        self._reaches: bool | None = None
        # With `lean_infantry` on: whether the rush watch has told a rush, which lifts the lean counts for good.
        self._is_rush_told = False
        # Under factory_first, or with `single_scout_rifle` on: whether a rifleman has shown, the scout the opening
        # trains first; and whether the hold on the other infantry has been released, for good.
        self._has_rifleman_shown = False
        self._is_hold_released = False

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The orders of the opening for this cycle."""
        names = FAMILIES[world.family]
        self._note_rush(ctx)
        self._note_hold(ctx)
        self._choose_cover(world, ctx, names)
        steps = self._build_in_force(world, ctx, names)
        # The step of the build comes first: what it takes of the power is then no longer there for a defense.
        # And the defense against infantry comes before the gun: both are made in the Defense queue.
        return [
            *self._deploy(world, ctx),
            *self._next_building(world, ctx, steps),
            *self._defense(world, ctx, names),
            *self._gun(world, ctx, names),
            *self._infantry(world, ctx),
            *self._guard_post_orders(world, ctx),
        ]

    def _deploy(self, world: World, ctx: MatchContext) -> list[Intent]:
        """Deploy the MCV where it stands while there is no construction yard, and again when nothing came of it."""
        if ctx.production.standing(CONSTRUCTION_YARD):
            return []
        vehicles = [unit.id for unit in world.units.values() if unit.type == MCV]
        sent = self._deploy_sent_tick
        if not vehicles or (sent is not None and world.tick - sent < ctx.tuning.opening.deploy_retry_ticks):
            return []
        self._deploy_sent_tick = world.tick
        return [Intent(Priority.PLACEMENT, {"cmd": "deploy", "units": vehicles[:1]})]

    def _build_in_force(self, world: World, ctx: MatchContext, names: Family) -> tuple[str, ...]:
        """The steps of the build still to be played, published in the match context: none once it is complete."""
        self._is_build_complete = self._is_build_complete or is_build_complete(ctx.production, ctx.tuning.opening.order)
        steps = () if self._is_build_complete else self._steps(world, ctx, names)
        ctx.opening_build = OpeningBuild(steps, creeps=self._reaches is False)
        return steps

    def _steps(self, world: World, ctx: MatchContext, names: Family) -> tuple[str, ...]:
        """The steps of the build in its order; under reach_first, the power plant and the barracks alone until both
        stand or are on their way, and from then on the build the reach decision made once gives."""
        order = ctx.tuning.opening.order
        if order != REACH_FIRST:
            return build_steps(names, order)
        if self._reaches is None:
            head = build_steps(names, REACH_FIRST)[:_STEPS_BEFORE_REACH]
            if first_missing(head, ctx.production) is not None:
                return head
            self._reaches = _decide_reach(world, ctx)
        return build_steps(names, REACH_FIRST, self._reaches)

    def _next_building(self, world: World, ctx: MatchContext, steps: tuple[str, ...]) -> list[Intent]:
        """The next step of the build, or the power plant that has to come before it."""
        production = ctx.production
        if not steps or not production.is_queue_free(BUILDING_QUEUE):
            return []
        if ctx.threat_active:
            return _power_plant(world, production) if production.headroom() < 0 else []
        step = first_missing(steps, production)
        if step is None or step not in world.available_production:
            return []
        return [production.build(step)] if production.can_power(step) else _power_plant(world, production)

    def _defense(self, world: World, ctx: MatchContext, names: Family) -> list[Intent]:
        """The defense against infantry, until as many have stood as the opening builds."""
        production, item = ctx.production, names.anti_infantry_defense
        self._is_defense_called = self._is_defense_called or _is_defense_called_for(world, ctx, self._is_build_complete)
        self._defenses_seen = max(self._defenses_seen, production.standing(item))
        countered = self._rush_defenses.intents(item, world, ctx)
        if countered is not None:
            return countered
        wanted = self._defenses_wanted()
        if self._defenses_seen >= wanted or production.planned(item) >= wanted or ctx.threat_active:
            return []
        can_start = item in world.available_production and production.is_queue_free(DEFENSE_QUEUE)
        return [production.build(item)] if can_start and production.can_power(item) else []

    def _defenses_wanted(self) -> int:
        """How many defenses against infantry the opening builds: the one it opens with, once that is called for, and
        one more when the cover against armor is a second defense."""
        return int(self._is_defense_called) + int(self._cover == _SECOND_DEFENSE)

    def _choose_cover(self, world: World, ctx: MatchContext, names: Family) -> None:
        """Decide, once it is due and the barracks stands, what the cover against armor is made of."""
        tuning = ctx.tuning.opening
        self._is_cover_due = self._is_cover_due or _is_cover_due_now(world, ctx)
        # Before the barracks stands neither a gun nor rocket infantry can be on offer, whatever the faction.
        if self._cover is not None or not self._is_cover_due or not ctx.production.standing(names.barracks):
            return
        self._cover = _cover_on_offer(world, ctx.production, names, allows_rockets=not self._is_lean(ctx))
        if self._cover == _ROCKETS:
            ctx.groups.set_guard_quota(ROCKET_INFANTRY, tuning.guard_rockets)
            # The guard's rockets are the opening's to train, even once it was done with the Infantry queue.
            self._is_infantry_done = False
        elif self._cover == _SECOND_DEFENSE and ROCKET_INFANTRY not in world.available_production:
            # A second defense the lean counts chose over rocket infantry on offer is no shortfall.
            _log_shortfall(names)

    def _gun(self, world: World, ctx: MatchContext, names: Family) -> list[Intent]:
        """The defense against armor, when that is what the cover is, until one has stood."""
        production, item = ctx.production, names.opening_anti_armor_defense
        self._is_gun_called = self._is_gun_called or _is_gun_called_for(world, ctx.tuning.opening)
        if self._cover != _GUN or self._has_gun_stood or ctx.threat_active or not self._is_gun_called:
            return []
        self._has_gun_stood = production.standing(item) > 0
        can_start = item in world.available_production and production.is_queue_free(DEFENSE_QUEUE)
        if production.planned(item) or not can_start or not production.can_power(item):
            return []
        return [production.build(item)]

    def _infantry(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The opening's own orders for the Infantry queue: the home guard's infantry, then rifles for `main`."""
        tuning = ctx.tuning.opening
        if self._is_infantry_done:
            return []
        if self._is_rifleman_only(ctx):
            ctx.claims.hold(INFANTRY_QUEUE, self.name)
            rockets = self._missing_from_guard(ctx, tuning)[ROCKET_INFANTRY]
            return self._rifleman(world, ctx) + _trains_for_guard(world, ctx.production, {ROCKET_INFANTRY: rockets})
        missing = self._missing_from_guard(ctx, tuning)
        if self._is_build_complete and not any(missing.values()):
            # The opening wants nothing of the queue now, so routine production has it. Until the cover is
            # chosen that can change once more: a cover of rocket infantry takes the queue back for the guard.
            # With defense_on_threat on, the cover may never come, and that cover takes the queue back itself.
            self._is_infantry_done = self._cover is not None or ctx.tuning.opening.defense_on_threat
            ctx.claims.release(INFANTRY_QUEUE, self.name)
            return []
        ctx.claims.hold(INFANTRY_QUEUE, self.name)
        if ctx.threat_active or world.economy.spendable < tuning.infantry_min_cash:
            return []
        return _trains_for_guard(world, ctx.production, missing) or self._rifle_for_main(world, ctx, tuning)

    def _is_rifleman_only(self, ctx: MatchContext) -> bool:
        """Whether the opening's orders for the Infantry queue are the rifleman scout and the rocket cover alone: while
        the hold of `single_scout_rifle` is on; with the switch off, under factory_first until the depot stands and
        completes the build."""
        if ctx.tuning.opening.single_scout_rifle:
            return ctx.scout_rifle_hold
        return ctx.tuning.opening.order == FACTORY_FIRST and not self._is_build_complete

    def _note_hold(self, ctx: MatchContext) -> None:
        """Release the hold of `single_scout_rifle` for good on the rush watch's warning or rush verdict, a threat at
        the base or the service depot standing, and publish whether the hold is on."""
        is_rush = ctx.rush_warning or ctx.rush_verdict == RUSH
        is_release = is_rush or ctx.threat_active or ctx.production.standing(SERVICE_DEPOT) > 0
        self._is_hold_released = self._is_hold_released or is_release
        ctx.scout_rifle_hold = ctx.tuning.opening.single_scout_rifle and not self._is_hold_released

    def _rifleman(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The one rifleman of the factory-first build, as soon as the barracks offers it; none once a rifleman has
        shown or while one is on its way, so that only an order that was lost is made again."""
        production = ctx.production
        self._has_rifleman_shown = self._has_rifleman_shown or production.units(RIFLE_INFANTRY) > 0
        can_train = RIFLE_INFANTRY in world.available_production and production.is_queue_free(INFANTRY_QUEUE)
        if self._has_rifleman_shown or production.pending(RIFLE_INFANTRY) or not can_train:
            return []
        return [production.train(RIFLE_INFANTRY)]

    def _missing_from_guard(self, ctx: MatchContext, tuning: OpeningTuning) -> dict[str, int]:
        """How many of each kind of infantry the home guard is short of, those in training not taken off."""
        groups = ctx.groups
        rockets_wanted = tuning.guard_rockets if self._cover == _ROCKETS else 0
        guard_rifles, _ = self._rifles_wanted(ctx)
        return {
            RIFLE_INFANTRY: max(0, guard_rifles - groups.count(GUARD, RIFLE_INFANTRY)),
            ROCKET_INFANTRY: max(0, rockets_wanted - groups.count(GUARD, ROCKET_INFANTRY)),
        }

    def _rifle_for_main(self, world: World, ctx: MatchContext, tuning: OpeningTuning) -> list[Intent]:
        """One more rifle for `main`: with the guard's rifles standing, the build under way and money plentiful."""
        production, groups = ctx.production, ctx.groups
        guard_rifles, main_rifles = self._rifles_wanted(ctx)
        if self._is_build_complete or groups.count(GUARD, RIFLE_INFANTRY) < guard_rifles:
            return []
        rifles = groups.count(MAIN, RIFLE_INFANTRY) + production.pending(RIFLE_INFANTRY)
        can_train = RIFLE_INFANTRY in world.available_production and production.is_queue_free(INFANTRY_QUEUE)
        if rifles >= main_rifles or world.economy.spendable < tuning.main_rifle_min_cash or not can_train:
            return []
        return [production.train(RIFLE_INFANTRY)]

    def _rifles_wanted(self, ctx: MatchContext) -> tuple[int, int]:
        """How many rifles the home guard holds and how many the opening trains for `main`: the lean guard and none
        for `main` while the lean counts are in force."""
        tuning = ctx.tuning.opening
        if self._is_lean(ctx):
            return tuning.lean_guard_rifles, 0
        return tuning.guard_rifles, tuning.main_rifles

    def _is_lean(self, ctx: MatchContext) -> bool:
        """Whether the lean counts are in force: `lean_infantry` is on and the rush watch has told no rush."""
        return ctx.tuning.opening.lean_infantry and not self._is_rush_told

    def _note_rush(self, ctx: MatchContext) -> None:
        """Lift the lean counts for good in the cycle the rush watch tells a rush: the opening takes the Infantry
        queue back, to raise the guard to its full count."""
        if self._is_lean(ctx) and ctx.rush_verdict == RUSH:
            self._is_rush_told = True
            self._is_infantry_done = False

    def _guard_post_orders(self, world: World, ctx: MatchContext) -> list[Intent]:
        """Send to the guard post every guard unit that is new, and every one that stands idle out of reach."""
        tuning = ctx.tuning.opening
        post = _guard_post(world, tuning)
        if post is None:
            return []
        to_send = [unit_id for unit_id in ctx.groups.members(GUARD) if self._belongs_at_post(unit_id, world, tuning)]
        if not to_send:
            return []
        self._sent_to_post.update(dict.fromkeys(to_send, world.tick))
        return [Intent(Priority.MOVE, {"cmd": "move", "units": to_send, "to": list(post)})]

    def _belongs_at_post(self, unit_id: int, world: World, tuning: OpeningTuning) -> bool:
        """Whether a guard unit has to be told to go to the post: it never was, or it idles too far from the base."""
        sent = self._sent_to_post.get(unit_id)
        if sent is None:
            return True
        unit = world.units[unit_id]
        is_astray = unit.idle and not cells.is_within((unit.x, unit.y), world.base_center, tuning.guard_radius_cells)
        return is_astray and world.tick - sent >= tuning.guard_reorder_ticks


def _decide_reach(world: World, ctx: MatchContext) -> bool:
    """Whether the first refinery can stand against the ore field it is to serve: a refinery candidate is within
    `refinery_ore_cells` of the field's ore, or no field is in reach. Writes the one `opening_reach` event."""
    reach = placement.refinery_reach(world, ctx.terrain, ctx.tuning.placement)
    reaches = reach is None or reach.reaches
    records.log(
        OPENING_REACH_EVENT,
        {
            "tick": world.tick,
            "refinery_reaches": reaches,
            "field_center": None if reach is None else list(reach.field.center),
            "distance_cells": None if reach is None else reach.distance_cells,
        },
    )
    return reaches


def _is_defense_called_for(world: World, ctx: MatchContext, is_build_complete: bool) -> bool:
    """Whether the defense against infantry is called for in this cycle: always with `defense_on_threat` off; with it
    on, by a rush the watch tells, by a verdict still unknown at its deadline, or by a threat while the build is under
    way, the unknown verdict only with `defense_on_unknown` on. With `rush_detection` off there is no verdict, and
    only a threat calls for it."""
    if not ctx.tuning.opening.defense_on_threat:
        return True
    verdict, deadline = ctx.rush_verdict, ctx.tuning.scouting.rush_unknown_deadline_seconds
    is_unknown_at_deadline = (
        ctx.tuning.opening.defense_on_unknown and verdict == UNKNOWN and world.game_seconds >= deadline
    )
    return verdict == RUSH or is_unknown_at_deadline or (ctx.threat_active and not is_build_complete)


def _guard_post(world: World, tuning: OpeningTuning) -> Cell | None:
    """Where the home guard stands: in front of the base on the enemy's side. None while that side is not known."""
    if world.base_center is None or world.enemy_base is None or world.map_bounds is None:
        return None
    return cells.clamped(cells.toward(world.base_center, world.enemy_base, tuning.guard_post_cells), world.map_bounds)


def _cover_on_offer(world: World, production: Production, names: Family, allows_rockets: bool) -> str:
    """What the cover against armor is made of, by what can be had now: a gun, rocket infantry when they are
    `allows_rockets`, or neither."""
    gun = names.opening_anti_armor_defense
    if gun is not None and gun in world.available_production and production.can_power(gun):
        return _GUN
    is_rockets = allows_rockets and ROCKET_INFANTRY in world.available_production
    return _ROCKETS if is_rockets else _SECOND_DEFENSE


def _trains_for_guard(world: World, production: Production, missing: dict[str, int]) -> list[Intent]:
    """The training orders for what the home guard is short of, is on offer and is not in training already."""
    to_order = {
        item: short - production.pending(item) for item, short in missing.items() if item in world.available_production
    }
    return [production.train(item, count) for item, count in to_order.items() if count > 0]


def _is_cover_due_now(world: World, ctx: MatchContext) -> bool:
    """Whether the cover against armor falls due in this cycle. With `defense_on_threat` off: at
    `anti_armor_at_seconds`, or once enemy armor or a building that makes it has been seen. With it on: once the rush
    watch tells a rush and an enemy vehicle that fights has been seen, once such a vehicle is seen within
    `armor_near_base_cells` of the base, or under a major threat."""
    tuning = ctx.tuning.opening
    if not tuning.defense_on_threat:
        return world.game_seconds >= tuning.anti_armor_at_seconds or _has_seen_armor(world)
    vehicles = _combat_vehicles_seen(world)
    is_near_base = any(_is_near_base(record, world, tuning.armor_near_base_cells) for record in vehicles)
    return (ctx.rush_verdict == RUSH and bool(vehicles)) or is_near_base or ctx.threat_level == MAJOR_THREAT


def _is_gun_called_for(world: World, tuning: OpeningTuning) -> bool:
    """Whether the opening gun may be bought in this cycle: always with `defense_on_threat` off; with it on, once an
    enemy vehicle that fights is seen within `armor_near_base_cells` of the base."""
    if not tuning.defense_on_threat:
        return True
    reach = tuning.armor_near_base_cells
    return any(_is_near_base(record, world, reach) for record in _combat_vehicles_seen(world))


def _has_seen_armor(world: World) -> bool:
    """Whether an enemy vehicle that fights, a war factory or a service depot was seen in this match."""
    return any(_is_armor(record.type, world) for record in world.enemy_memory.values())


def _combat_vehicles_seen(world: World) -> list[EnemyRecord]:
    """The enemy vehicles that fight seen in this match, each where it was last seen."""
    return [record for record in world.enemy_memory.values() if _is_combat_vehicle(record.type, world)]


def _is_near_base(record: EnemyRecord, world: World, reach: int) -> bool:
    """Whether an enemy actor was last seen within `reach` cells of the base; never while the base has no center."""
    return world.base_center is not None and cells.is_within((record.x, record.y), world.base_center, reach)


def _is_armor(enemy_type: str, world: World) -> bool:
    return enemy_type in _ARMOR_BUILDINGS or _is_combat_vehicle(enemy_type, world)


def _is_combat_vehicle(enemy_type: str, world: World) -> bool:
    facts = world.items.get(enemy_type)
    return facts is not None and facts.queue == VEHICLE_QUEUE and enemy_type not in NON_COMBAT_TYPES


def _log_shortfall(names: Family) -> None:
    """Say in the match record that there is nothing against armor to be had, and what is built in its place."""
    records.log(
        "error",
        {
            "where": _ERROR_SOURCE,
            "type": records.HANDLED_ERROR,
            "exception": "AntiArmorShortfall",
            "message": (
                f"No anti-armor defense and no {ROCKET_INFANTRY} in available_production; "
                f"the cover is one more {names.anti_infantry_defense}"
            ),
        },
    )


def first_missing(steps: tuple[str, ...], production: Production) -> str | None:
    """The first step of a build of which fewer stand or are on their way than the build has asked for by then."""
    for position, item in enumerate(steps):
        if production.planned(item) < steps[: position + 1].count(item):
            return item
    return None


def _power_plant(world: World, production: Production) -> list[Intent]:
    """A power plant ahead of everything else in the Building queue, when one is on offer."""
    if POWER_PLANT not in world.available_production:
        return []
    return [production.build(POWER_PLANT, Priority.POWER)]
