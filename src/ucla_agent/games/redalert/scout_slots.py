"""The scout slots (`[scouting] scout_slots`): a few designated scouts kept for the whole match.

One scout at a time, sent on a mission when the picture goes stale, scouts
too seldom, and a mission that drives at the enemy's start is lost to the
first defender on the way. With the switch on, scouting is instead
scout_slot_count slots, each held by one scout of the `scout` group for as
long as it lives, and the scouting manager sends no missions.

The slots are filled by the first infantry the base produces, whatever
trained them: the first scout_slot_count own infantry units that fight seen
in the match are taken out of the guard or main into the scout group, and
the guard, which counts its own members, trains its rifles again. A unit is
new in the cycle it is first seen; one that comes out of a transport under a
new id is not.

A rifleman is slow. Once the family's faster scout can be trained (the
Allied jeep, once a war factory stands; the Soviet unit soviet_scout_unit
names: the dog, once the kennel upkeep builds after the opening stands, or
the flak truck, faster still, once a war factory stands), one is trained for
each slot a slower unit holds, never while the opening's build is under way.
The next new unit of that type takes the slot, and the unit it takes over
from goes to main: the slot is never empty while the faster unit is on its
way. For the Allies the jeep waits as well, upgrade and replacement alike,
until ALLIED_FAST_SCOUT_WAR_FACTORIES war factories stand or the game time
reaches allied_fast_scout_after_seconds: a lone war factory's first minutes
go to the tanks that meet the first rush, and the slots keep their riflemen,
an emptied one refilled by a rifleman from the barracks within the wait the
replacements keep. The Soviet scouts are not held back. While a slot waits
for a unit it ordered, the scouting manager claims that unit's queue, so
that the unit queues of upkeep leave the queue to it and no hold of
`economy_first` keeps it waiting. An order that has shown
nowhere for two seconds, in a queue or on its way, was lost or refused and
may be made again.

Under the opening's factory-first order (see `opening`), the first slot is
the rifleman's the opening trains as soon as the barracks stands, and the
second waits for the scout vehicle: as soon as a war factory stands, while
the opening's build is under way, one jeep (Allies, whatever
allied_fast_scout_after_seconds says) or one flak truck (Soviets, whatever
soviet_scout_unit says) is ordered for the first slot emptied by a loss,
else the first slot never held, and takes it. The scouting manager claims
the Vehicle queue for it from the first cycle until it comes, so that it is
the queue's first order (see `upkeep`). Until it comes no infantry takes a
slot but the first, and until the build is complete no slot orders a
rifleman: a rifleman scout lost before then is not replaced, and the vehicle
takes its slot. The rules above apply from then on.

While the opening holds back every infantry but its scout rifleman
(`single_scout_rifle` of [opening], see `opening`), under any order, only the
first slot takes infantry, the rifleman; a slot after the first that was
never held waits for the family's faster scout (the jeep, the dog) and takes
it once it is open, as an upgrade would; and the slots order one replacement
rifleman at most, for the first slot, before rush_watch_until_seconds.

With `mcv_first` of [upkeep] switched on, the second yard's MCV comes before
the scouts on the Vehicle queue, which makes both: while upkeep says the MCV
is due first, no jeep or flak truck is ordered for a slot and the claim on
the Vehicle queue is given up even while a slot waits for one. A dog, made
on the Infantry queue, is still ordered. While upkeep's full hold for the
MCV's money is in force, a slot emptied by a loss waits too, unless every
slot is empty: then the rifleman is ordered as before.

A scout gone from the world (but for one aboard a transport) is lost, and
its slot is empty; so is the slot of a scout below scout_release_below_hp
(wide_release_below_hp with `scout_wide_berth` on, see `scout_berth`), which
is too hurt to scout: it is sent to the staging point and joins main.
An empty slot is refilled by one order of the fastest of the faster scout
and the rifleman that can be trained now, an order for one slot at a time at
least scout_replace_floor_seconds after the last such order; but while every
slot is empty and a barracks stands, a rifleman is ordered at once, whatever
the wait. The next new unit of the type ordered takes the slot.

With `scout_spend_cap` on as well, scouting spends less. The wait between
two replacement orders is capped_replace_seconds instead, and the rifleman
ordered at once needs every slot to be empty with no unit on its way to any
of them, ordered in an earlier cycle or for another slot in the same one:
one scout coming keeps the scouting going, and the wait is there so that
scouts lost one after another are not bought back one after another. The
slots also order at most fast_scouts_max fast scouts (the jeep, the dog,
the flak truck) in the match, upgrades and replacements alike, each order
counted, one made again after it was lost included. Past that no slot is
upgraded and a lost scout is replaced by a rifleman: a fast scout is a
good buy while there are few, but scouts the enemy keeps picking off are
an expense that does not stop.

Every scout is steered in every cycle (see `scout_steering`): to the place
worth a look that it can reach safely, away from danger, never idle for
long. Each scout's move is a `move` order at the scouting priority, and at
most scout_orders_per_batch of them go in one cycle, the most urgent first:
a flight, then a move the scout has not been sent yet, then one again for a
scout that stands idle. Where the scouts are heading is published as
`scout_destinations` of the match context.

Every unit that takes a slot is written to the match record as a
`scout_slot` event: its kind (`assigned` for the first infantry, `upgraded`
for the faster unit, which also names the unit it `replaces`, `replaced` for
a unit that refills an empty slot), the slot and the unit's id and type. A
scout lost to its slot is a `lost` event with the reason, `killed` when it
is gone from the world and `released` when it was too hurt. Each change of a
scout's destination is a `destination` event with the cell, who chose it
(`script`, or `model`) and the reason. With `scout_wide_berth` on, each place
a scout was hurt (see `scout_berth`) is a `hurt` event with the cell and the
scout's health; it is noted before a scout too hurt to scout is released, so
that the other scouts keep away from it as well. With `scout_spend_cap` on,
each fast scout the cap refuses is a `capped` event with the item refused
and the order (`upgrade` or `replacement`), an upgrade once for each scout
whose upgrade is refused.

With `scout_missions` on, the scouts go on the script's missions instead of
from place to place (see `scout_missions` and `scout_mission_planner`): in
every cycle each scout in the field is given its mission, or keeps the one it
has, and is steered to the mission's cell by its way, where it stands and
watches (see `scout_steering`). Each mission a scout is given is a
`destination` event with the cell, `mission` as who chose it, why the last
one ended, the mission's kind and its score. No model is asked where the
scouts go, whatever `model_scout_choice` says.

With `scout_cautious` on, the scouts are careful. A unit that takes a slot
is ordered to hold fire, and one that leaves it for main replaced by the
faster scout gets main's stance back (see `scout_stance`). One released too
hurt to scout goes home first: it stays in the scout group under hold fire
with a move to the staging point, and joins main with main's stance once it
stands within 4 cells of it, a `scout_slot` event of kind `returned` (see
`scout_stance.ReturningScouts`).
A scout that was hit or is outclassed by the enemy in sight retreats (see
`scout_retreat`): its retreat is its move while it lasts, the most urgent of
the scouts', its mission kept for it, and is a `retreat` event with its
cause, the cell it stood on, the cell it is sent to, its health and the cell
of the enemy it is sent away from (null when it goes back along its way).
The cell a scout was hit on becomes a hurt area (see `scout_berth`).

While the model commander steers the first scout (see `commander`), it is
the scout of the lowest slot holding one in the field. In every cycle its
question is asked (see `command_scout`), and while its standing action is in
force the scout is steered by it, not by the missions or the places, which
the other scouts keep: a destination is its course's (by a safe way when
there is one), and a hold leaves it standing for its three seconds. A scout
that must flee flees, and no commander order goes out for it in that cycle.
Its other order, a stop or a move to the next cell of its way, goes in the
commander's slot when the commander says it is due. A slot the commander no
longer steers, after a hold ran out and after the handover to main, has its
course handed back to the script.

With `rush_detection` and `rush_scout_direct` on, the first slot's scout is
steered by the rush scout rule until rush_watch_until_seconds (see
`rush_scout`): through the enemy base and then to the exit post, in place
of its missions or places and of the commander's question, which steers the
next slot's scout meanwhile. Its retreats are the rule's to take, and it is
released below scout_release_below_hp whatever the wide berth says. Its
course is handed back to the script once the watch ends.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Collection

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, command_scout
from ucla_agent.games.redalert.batch import Intent, Order, Priority
from ucla_agent.games.redalert.command_prompt import Action
from ucla_agent.games.redalert.command_scout import follow, is_held
from ucla_agent.games.redalert.commander import SCOUT_ROLE, STOP
from ucla_agent.games.redalert.family import FAMILIES, RIFLE_INFANTRY, WAR_FACTORY
from ucla_agent.games.redalert.groups import MAIN, SCOUT
from ucla_agent.games.redalert.items import SOVIET, TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.production import INFANTRY_QUEUE, VEHICLE_QUEUE
from ucla_agent.games.redalert.rush_scout import RUSH_SCOUT, RUSH_SCOUT_SLOT, RushScout
from ucla_agent.games.redalert.scout_berth import HurtAreas, release_below_hp
from ucla_agent.games.redalert.scout_choice import ANSWERED, ScoutQuestion, take_answer
from ucla_agent.games.redalert.scout_mission_planner import Assignment, MissionPlanner
from ucla_agent.games.redalert.scout_missions import Mission
from ucla_agent.games.redalert.scout_retreat import HIT, Begun, Retreats
from ucla_agent.games.redalert.scout_stance import Returning, ReturningScouts, ScoutStances, is_cautious
from ucla_agent.games.redalert.scout_steering import (
    COMMANDER,
    FLEE_URGENCY,
    MISSION,
    Course,
    Dangers,
    Move,
    steer,
    steer_held,
    steer_to_mission,
)
from ucla_agent.games.redalert.sight import SightGrid
from ucla_agent.games.redalert.tuning import FACTORY_FIRST, SOVIET_SCOUT_UNITS, ScoutingTuning
from ucla_agent.games.redalert.world import Cell, Unit, World

SCOUT_SLOT_EVENT = "scout_slot"
# The kinds of a `scout_slot` event: a slot taken by one of the first infantry, by the faster unit trained for it, and
# by the unit trained to refill it; and a slot whose scout was lost to it.
ASSIGNED = "assigned"
UPGRADED = "upgraded"
REPLACED = "replaced"
LOST = "lost"
# The kind of a `scout_slot` event that says where a slot's scout goes next, and the one that says where it was hurt.
DESTINATION = "destination"
HURT = "hurt"
# The kind of a `scout_slot` event of a scout that retreats (see `scout_retreat`).
RETREAT = "retreat"
# The kind of a `scout_slot` event of a released scout come home and joined to main (see `scout_stance`).
RETURNED = "returned"
# The kind of a `scout_slot` event of a fast scout the spend cap refused, and the orders it may refuse.
CAPPED = "capped"
UPGRADE = "upgrade"
REPLACEMENT = "replacement"
# Why a `lost` event says a scout was lost to its slot: it is gone from the world, or so hurt it was released.
KILLED = "killed"
RELEASED = "released"
# The faster unit the Allied scouts become; the Soviet one is the setting soviet_scout_unit.
ALLIED_FAST_SCOUT = "jeep"
# The Soviet scout vehicle of the factory-first opening, from the war factory; the Allied one is the jeep.
SOVIET_SCOUT_VEHICLE = "ftrk"
# This many standing war factories let the Allied slots order the jeep before allied_fast_scout_after_seconds.
ALLIED_FAST_SCOUT_WAR_FACTORIES = 2
# The fast scouts the slots may order, of which the spend cap counts every one ordered.
FAST_SCOUTS = frozenset({ALLIED_FAST_SCOUT, *SOVIET_SCOUT_UNITS})
# An order for a slot that has shown nowhere this long was lost or refused, and may be made again.
ORDER_LOST_TICKS = 2 * TICKS_PER_SECOND
# The queues the slots' units are trained in, which the scouting manager claims while a slot waits for one.
_UNIT_QUEUES = (INFANTRY_QUEUE, VEHICLE_QUEUE)
# The decimals a mission's score is written with in a `destination` event.
_SCORE_DECIMALS = 4


@dataclass
class Slot:
    """One scout slot and the scout that holds it."""

    # The slot's number, from 1.
    number: int
    unit_id: int | None = None
    unit_type: str | None = None
    # Whether a unit has held the slot: only a slot never held is filled by the first infantry produced.
    was_held: bool = False
    # The type of the unit ordered for the slot that has not arrived yet; None while no order is out.
    awaiting: str | None = None
    # The last tick the order for the slot was made or seen on its way.
    order_seen_tick: int = 0
    # Where the slot's scout is going and by which way.
    course: Course = field(default_factory=Course)
    # The questions the slot puts to a model where its scout goes next; None while no model is asked.
    question: ScoutQuestion | None = None
    # The scout whose upgrade the spend cap refused, which is written once; None while none was refused.
    capped_unit: int | None = None

    def take(self, unit: Unit) -> None:
        """Have a unit hold the slot, on a course of its own; an order out for it has arrived."""
        self.unit_id, self.unit_type, self.was_held, self.awaiting = unit.id, unit.type, True, None
        self.course = Course()

    def order(self, item: str, tick: int) -> None:
        """Note the order of a unit for the slot."""
        self.awaiting, self.order_seen_tick = item, tick


class ScoutSlots:
    """The designated scouts of one match. See the module's note."""

    def __init__(self, claimant: str) -> None:
        # The manager the slots order for, as it claims queues.
        self._claimant = claimant
        self._slots: list[Slot] = []
        # Every own unit that fights seen in this match: a unit not among them is new.
        self._seen: set[int] = set()
        # The tick of the last order of a unit to replace a scout lost; None before the first.
        self._replaced_tick: int | None = None
        # How many fast scouts the slots have ordered in this match, counted while `scout_spend_cap` is on.
        self._fast_ordered = 0
        # Where the scouts were hurt lately, kept while `scout_wide_berth` is on.
        self._hurt = HurtAreas()
        # The scouts' missions, while `scout_missions` is on.
        self._planner = MissionPlanner()
        # The scouts' stance orders, their retreats and the released scouts on their way home, while `scout_cautious`
        # is on.
        self._stances = ScoutStances()
        self._retreats = Retreats()
        self._returning = ReturningScouts()
        # Under the factory-first opening: whether its scout vehicle has taken a slot. And under the hold of
        # `single_scout_rifle`: whether the one replacement rifleman has been ordered.
        self._has_vehicle_come = False
        self._has_replaced_rifle = False
        # The first slot's pass through the enemy base and its watch at the enemy's exit, while `rush_scout_direct`
        # is on and the rush watch lasts.
        self._rush = RushScout()

    def intents(self, world: World, ctx: MatchContext, sight: SightGrid) -> list[Intent]:
        """Account for the scouts lost and those too hurt to scout, fill the slots from the cycle's new units, order
        what they wait for and steer the scouts by what our units have had in sight; the orders of the cycle. The
        scouts' destinations are published on the match context."""
        if not self._slots:
            self._slots = _new_slots(ctx)
        self._account(world)
        self._note_hurt(world, ctx)
        orders = self._release(world, ctx) + self._bring_home(world, ctx)
        for unit in self._new_units(world):
            self._take(unit, world, ctx)
        if is_cautious(ctx.tuning.scouting):
            holding = [world.combat_units[slot.unit_id] for slot in self._held(world)]
            returning = [world.combat_units[unit_id] for unit_id in self._returning.units]
            self._stances.keep([*holding, *returning], world.tick)
        self._give_up_lost_orders(world, ctx)
        vehicle = self._opening_vehicle(world, ctx)
        wanted = vehicle + self._upgrades(world, ctx) + self._replacements(world, ctx, [slot for slot, _ in vehicle])
        orders += self._train(self._within_budget(wanted, world, ctx), world, ctx)
        self._claim_queues(world, ctx)
        orders += self._steer(world, ctx, sight)
        if ctx.tuning.scouting.scout_wide_berth:
            self._hurt.keep_health(world.combat_units[slot.unit_id] for slot in self._held(world))
        ctx.scout_destinations = tuple(
            slot.course.destination for slot in self._slots if slot.course.destination is not None
        )
        ctx.returning_scouts = self._returning.units
        return orders + self._stances.orders()

    def _account(self, world: World) -> None:
        """Empty the slot of every scout gone from the world, but for one aboard a transport, with a `lost` event."""
        for slot in self._slots:
            if (
                slot.unit_id is not None
                and slot.unit_id not in world.combat_units
                and world.aboard(slot.unit_id) is None
            ):
                self._lose(slot, KILLED)

    def _lose(self, slot: Slot, reason: str) -> None:
        """Empty a slot whose scout is lost to it, with the `lost` event that says why."""
        _log(LOST, slot, reason=reason)
        slot.unit_id = slot.unit_type = None
        slot.course = Course()

    def _note_hurt(self, world: World, ctx: MatchContext) -> None:
        """With `scout_wide_berth` on, record a hurt area where each scout hurt since the last state stands, each with
        a `hurt` event."""
        tuning = ctx.tuning.scouting
        if not tuning.scout_wide_berth:
            return
        for slot in self._held(world):
            scout = world.combat_units[slot.unit_id]
            if self._hurt.note(scout, world.tick, tuning):
                _log(HURT, slot, cell=[scout.x, scout.y], hp=scout.hp)

    def _held(self, world: World) -> list[Slot]:
        """The slots whose scout is in the world, not aboard a transport."""
        return [slot for slot in self._slots if slot.unit_id in world.combat_units]

    def _release(self, world: World, ctx: MatchContext) -> list[Intent]:
        """Send every scout below the release threshold (see `scout_berth`) to the staging point and into main: its slot
        counts as lost. With `scout_cautious` on, it joins main only once it is home (see `_bring_home`)."""
        tuning = ctx.tuning.scouting
        rush_slot = self._rush_slot(world, ctx)
        staging = cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells)
        orders = []
        for slot in self._slots:
            scout = world.combat_units.get(slot.unit_id)
            # The rush scout keeps no wide berth (see `rush_scout`), nor its higher threshold.
            below = tuning.scout_release_below_hp if slot is rush_slot else release_below_hp(tuning)
            if scout is None or scout.hp >= below:
                continue
            if is_cautious(ctx.tuning.scouting):
                orders.append(self._returning.send_home(scout, slot.number, staging, world.tick))
            else:
                self._to_main(scout.id, ctx)
                orders.append(Intent(Priority.MOVE, {"cmd": "move", "units": [scout.id], "to": list(staging)}))
            self._lose(slot, RELEASED)
        return orders

    def _bring_home(self, world: World, ctx: MatchContext) -> list[Intent]:
        """With `scout_cautious` on, move every released scout that has come home into main, in main's stance, with a
        `returned` event; the moves home again of those idle short of it."""
        home, moves = self._returning.follow(world, ctx)
        for unit_id, returning in home.items():
            self._to_main(unit_id, ctx)
            _log_returned(unit_id, returning)
        return moves

    def _new_units(self, world: World) -> list[Unit]:
        """The own units that fight seen for the first time in this cycle, in the order of their ids; a unit that came
        out of a transport under a new id is not new."""
        not_new = self._seen | set(world.taken_over.values())
        new = [unit for unit_id, unit in world.combat_units.items() if unit_id not in not_new]
        self._seen.update(world.combat_units)
        return sorted(new, key=lambda unit: unit.id)

    def _take(self, unit: Unit, world: World, ctx: MatchContext) -> None:
        """Move a new unit into the slot that waits for its type, else, for infantry, into the first slot never held."""
        awaiting = next((slot for slot in self._slots if slot.awaiting == unit.type), None)
        if awaiting is not None:
            self._has_vehicle_come = self._has_vehicle_come or unit.type == _scout_vehicle(world)
            self._take_over(awaiting, unit, world, ctx)
            return
        facts = world.items.get(unit.type)
        # The slots after the first wait for the factory-first opening's scout vehicle while it is to come, and for
        # the faster scout while the opening holds infantry back.
        is_first_only = self._is_vehicle_wanted(ctx) or ctx.scout_rifle_hold
        open_to_infantry = self._slots[:1] if is_first_only else self._slots
        first = next((slot for slot in open_to_infantry if not slot.was_held and slot.awaiting is None), None)
        if first is not None and facts is not None and facts.queue == INFANTRY_QUEUE:
            self._fill(first, unit, ASSIGNED, world, ctx)

    def _take_over(self, slot: Slot, unit: Unit, world: World, ctx: MatchContext) -> None:
        """Have the unit a slot waited for hold it: an empty slot is refilled (a slot never held is assigned it), and
        from a slot held the unit that held it goes to main."""
        replaced = slot.unit_id
        if replaced is None:
            self._fill(slot, unit, REPLACED if slot.was_held else ASSIGNED, world, ctx)
            return
        self._to_main(replaced, ctx)
        self._fill(slot, unit, UPGRADED, world, ctx, replaces=replaced)

    def _fill(self, slot: Slot, unit: Unit, kind: str, world: World, ctx: MatchContext, **more: int) -> None:
        """Have a unit hold a slot, in the scout group, and write the `scout_slot` event of the kind given; with
        `scout_cautious` on, the unit is ordered to hold fire."""
        ctx.groups.assign(unit.id, SCOUT)
        slot.take(unit)
        _log(kind, slot, **more)
        if is_cautious(ctx.tuning.scouting):
            self._stances.hold_fire(unit.id, world.tick)

    def _to_main(self, unit_id: int, ctx: MatchContext) -> None:
        """Move a unit leaving its slot into main; with `scout_cautious` on, it gets main's stance back."""
        ctx.groups.assign(unit_id, MAIN)
        if is_cautious(ctx.tuning.scouting):
            self._stances.to_main(unit_id)

    def _give_up_lost_orders(self, world: World, ctx: MatchContext) -> None:
        """Forget the orders for slots that have shown nowhere for ORDER_LOST_TICKS: of the slots waiting for a type,
        as many as there are of that type on their way are still waiting, the lowest numbers first."""
        tick = world.tick
        for item in {slot.awaiting for slot in self._slots if slot.awaiting is not None}:
            waiting = [slot for slot in self._slots if slot.awaiting == item]
            on_its_way = ctx.production.pending(item)
            for slot in waiting[:on_its_way]:
                slot.order_seen_tick = tick
            for slot in waiting[on_its_way:]:
                if tick - slot.order_seen_tick >= ORDER_LOST_TICKS:
                    slot.awaiting = None

    def _is_vehicle_wanted(self, ctx: MatchContext) -> bool:
        """Whether the factory-first opening's scout vehicle is still to come: the order is factory_first, the opening's
        build is under way and the vehicle has taken no slot."""
        is_under_way = ctx.tuning.opening.order == FACTORY_FIRST and bool(ctx.opening_build.steps)
        return is_under_way and not self._has_vehicle_come

    def _opening_vehicle(self, world: World, ctx: MatchContext) -> list[tuple[Slot, str]]:
        """The factory-first opening's scout vehicle while it is wanted, as soon as a war factory offers it and none is
        on its way: for the first slot emptied by a loss, else the first slot never held."""
        vehicle = _scout_vehicle(world)
        if not self._is_vehicle_wanted(ctx) or vehicle not in world.available_production:
            return []
        if any(slot.awaiting == vehicle for slot in self._slots):
            return []
        emptied = [slot for slot in self._slots if slot.was_held and slot.unit_id is None and slot.awaiting is None]
        never_held = [slot for slot in self._slots if not slot.was_held and slot.awaiting is None]
        return [(slot, vehicle) for slot in [*emptied, *never_held][:1]]

    def _upgrades(self, world: World, ctx: MatchContext) -> list[tuple[Slot, str]]:
        """The family's faster scout for every slot a slower unit holds with no order out, once it can be trained, the
        opening's build is complete and, for the Allies, the jeep is open (see `_is_fast_scout_open`); none on the
        Vehicle queue while the second yard's MCV is due first."""
        fast = _fast_scout(world, ctx)
        if fast not in world.available_production or ctx.opening_build.steps or _waits_for_mcv(fast, world, ctx):
            return []
        if not _is_fast_scout_open(world, ctx):
            return []
        return [(slot, fast) for slot in self._slots if slot.awaiting is None and _is_upgraded(slot, fast, world, ctx)]

    def _replacements(self, world: World, ctx: MatchContext, taken: list[Slot]) -> list[tuple[Slot, str]]:
        """A unit for each slot emptied by a loss with no order out and not `taken` by another order of the cycle, while
        one may be ordered: the fastest scout that can be trained once the replacement floor has passed since the last,
        else a rifleman while every slot is empty and a barracks stands."""
        emptied = [
            slot
            for slot in self._slots
            if slot.was_held and slot.unit_id is None and slot.awaiting is None and slot not in taken
        ]
        replacements: list[tuple[Slot, str]] = []
        for slot in emptied:
            item = self._replacement(slot, world, ctx, [ordered for ordered, _ in replacements])
            if item is None:
                break
            replacements.append((slot, item))
            self._replaced_tick = world.tick
            self._has_replaced_rifle = self._has_replaced_rifle or (ctx.scout_rifle_hold and item == RIFLE_INFANTRY)
        return replacements

    def _replacement(self, slot: Slot, world: World, ctx: MatchContext, ordered: list[Slot]) -> str | None:
        """The unit to order for a slot emptied by a loss now, after those ordered for the slots `ordered` in this
        cycle; None while none may be ordered. While a full hold for the second yard's MCV is in force (see
        `economy_hold`), only the rifleman of a base with every slot empty; and a rifleman only while one may be
        ordered for the slot (see `_allows_rifle`)."""
        tuning = ctx.tuning.scouting
        allows_rifle = self._allows_rifle(slot, world, ctx)
        floor_ticks = _replace_floor_seconds(tuning) * TICKS_PER_SECOND
        is_floor_over = self._replaced_tick is None or world.tick - self._replaced_tick >= floor_ticks
        if is_floor_over and not ctx.full_hold:
            offered = _replacements_on_offer(world, ctx, allows_rifle)
            return max(offered, key=lambda item: _speed(item, world), default=None)
        has_barracks = ctx.production.standing(FAMILIES[world.family].barracks) > 0
        is_rifle_due = self._is_every_slot_empty(ordered, tuning) and has_barracks and allows_rifle
        return RIFLE_INFANTRY if is_rifle_due else None

    def _allows_rifle(self, slot: Slot, world: World, ctx: MatchContext) -> bool:
        """Whether a rifleman may be ordered for a slot: while the opening holds infantry back (`single_scout_rifle`),
        only the one replacement of the first slot's rifleman before rush_watch_until_seconds; with the switch off,
        never while the factory-first opening's build is under way, whose only infantry is its rifleman scout."""
        if ctx.scout_rifle_hold:
            is_in_time = world.game_seconds < ctx.tuning.scouting.rush_watch_until_seconds
            return slot.number == 1 and is_in_time and not self._has_replaced_rifle
        is_factory_first_build = ctx.tuning.opening.order == FACTORY_FIRST and bool(ctx.opening_build.steps)
        return ctx.tuning.opening.single_scout_rifle or not is_factory_first_build

    def _is_every_slot_empty(self, ordered: list[Slot], tuning: ScoutingTuning) -> bool:
        """Whether no slot holds a scout; with `scout_spend_cap` on, also none waits for a unit ordered for it, in an
        earlier cycle or for the slots `ordered` in this one."""
        is_empty = all(slot.unit_id is None for slot in self._slots)
        if not tuning.scout_spend_cap:
            return is_empty
        return is_empty and not ordered and all(slot.awaiting is None for slot in self._slots)

    def _within_budget(self, wanted: list[tuple[Slot, str]], world: World, ctx: MatchContext) -> list[tuple[Slot, str]]:
        """The units the slots want that may be ordered: with `scout_spend_cap` on, fast scouts only while fewer than
        fast_scouts_max have been ordered in the match, counted here in the order wanted; each one refused past that
        with what is ordered in its place."""
        tuning = ctx.tuning.scouting
        if not tuning.scout_spend_cap:
            return wanted
        allowed: list[tuple[Slot, str]] = []
        for slot, item in wanted:
            if item not in FAST_SCOUTS:
                allowed.append((slot, item))
            elif self._fast_ordered < tuning.fast_scouts_max:
                self._fast_ordered += 1
                allowed.append((slot, item))
            else:
                allowed += _refused(slot, item, world)
        return allowed

    def _train(self, wanted: list[tuple[Slot, str]], world: World, ctx: MatchContext) -> list[Intent]:
        """One order for each type of unit the slots want, of as many as want it; each slot then waits for its unit."""
        for slot, item in wanted:
            slot.order(item, world.tick)
        counts = Counter(item for _, item in wanted)
        return [ctx.production.train(item, count, Priority.PRODUCTION) for item, count in counts.items()]

    def _steer(self, world: World, ctx: MatchContext, sight: SightGrid) -> list[Intent]:
        """The move of each scout in the field (see `scout_steering`), at most scout_orders_per_batch of them, the
        most urgent first, each change of destination written as a `destination` event; first of all, while the model
        commander steers a scout, the commander's order for it, when one is due (see `command_scout`). With
        `scout_cautious` on, a retreating scout's move is its retreat's (see `scout_retreat`)."""
        retreats = self._retreat_moves(world, ctx)
        rush_slot = self._rush_slot(world, ctx)
        dangers = Dangers(world, ctx, self._hurt.live(world.tick, ctx.tuning.scouting))
        offered = self._planner.missions(world, ctx, sight) if self._needs_missions(ctx) else []
        commanded = self._command(offered, sight, dangers, world, ctx, rush_slot)
        if commanded is not None and commanded[0].number in retreats:
            commanded = None
        steered = [slot for slot in self._slots if slot.number not in retreats and slot is not rush_slot]
        free = [slot for slot in steered if commanded is None or slot is not commanded[0]]
        _release(free)
        if ctx.tuning.scouting.scout_missions:
            moves = self._mission_moves(free, offered, dangers, world, ctx, retreating=retreats.keys())
        else:
            moves = self._place_moves(free, sight, dangers, world, ctx)
        moves += [
            (retreats[slot.number], slot) for slot in self._slots if slot.number in retreats and slot is not rush_slot
        ]
        moves += self._rush_moves(rush_slot, retreats, world, ctx)
        ordered = [] if commanded is None else self._commanded_moves(*commanded, moves, dangers, world, ctx)
        # A stable sort: of moves as urgent, the lower slot's goes first, and one left out is still urgent next cycle.
        moves.sort(key=lambda pair: pair[0].urgency)
        sent = moves[: ctx.tuning.scouting.scout_orders_per_batch]
        for move, slot in sent:
            slot.course.sent = move.cell
        return ordered + [
            Intent(Priority.SCOUT, {"cmd": "move", "units": [slot.unit_id], "to": list(move.cell)})
            for move, slot in sent
        ]

    def _retreat_moves(self, world: World, ctx: MatchContext) -> dict[int, Move]:
        """With `scout_cautious` on, the retreat move of each scout in the field that retreats in this cycle, by its
        slot's number; each retreat begun a `retreat` event, the cell a scout was hit on made a hurt area."""
        tuning = ctx.tuning.scouting
        if not is_cautious(tuning):
            return {}
        held = {slot.number: slot for slot in self._held(world)}
        scouts = {number: world.combat_units[slot.unit_id] for number, slot in held.items()}
        moves, begun = self._retreats.update(scouts, world, ctx)
        for started in begun:
            scout = scouts[started.slot]
            if started.retreat.cause == HIT:
                self._hurt.mark((scout.x, scout.y), world.tick, tuning)
            _log_retreat(held[started.slot], started, scout)
        return moves

    def _rush_slot(self, world: World, ctx: MatchContext) -> Slot | None:
        """The first slot, while its scout is in the field and the rush scout rule steers it (see `rush_scout`);
        None otherwise."""
        slot = self._slots[RUSH_SCOUT_SLOT - 1]
        is_held = slot.unit_id in world.combat_units
        return slot if is_held and self._rush.is_steering(world, ctx) else None

    def _rush_moves(
        self, slot: Slot | None, retreats: dict[int, Move], world: World, ctx: MatchContext
    ) -> list[tuple[Move, Slot]]:
        """The rush scout's move in this cycle, with its slot: none without a slot it steers or while it stands at the
        post."""
        if slot is None:
            return []
        scout = world.combat_units[slot.unit_id]
        move = self._rush.move(scout, retreats.get(slot.number), slot.course, world, ctx)
        return [] if move is None else [(move, slot)]

    @staticmethod
    def _needs_missions(ctx: MatchContext) -> bool:
        """Whether the cycle's missions are wanted: by the scouts' missions, or by the commander's question."""
        commander = ctx.commander
        return ctx.tuning.scouting.scout_missions or (commander is not None and commander.role == SCOUT_ROLE)

    def _command(
        self,
        offered: list[Mission],
        sight: SightGrid,
        dangers: Dangers,
        world: World,
        ctx: MatchContext,
        rush_slot: Slot | None = None,
    ) -> tuple[Slot, Action] | None:
        """While the model commander steers the scout of the lowest slot that holds one in the field, the slot the
        rush scout rule steers aside (`rush_slot`): its question of the cycle put and the answer that arrived taken;
        that slot and its standing action while the action is in force (a hold lasts HOLD_SECONDS), else None, and None
        while the commander steers no scout."""
        commander = ctx.commander
        held = [slot for slot in self._held(world) if slot is not rush_slot]
        if commander is None or commander.role != SCOUT_ROLE or not held:
            return None
        slot = held[0]
        scout = world.combat_units[slot.unit_id]
        current = commander.current((scout.x, scout.y))
        question = command_scout.question(scout, dangers, sight, offered, commander.picks, world, ctx, current)
        action = commander.take(SCOUT_ROLE, question.actions, world, command_scout.situation(scout, dangers, ctx))
        commander.ask(SCOUT_ROLE, question, world)
        is_over = action is not None and action.cell is None and not is_held(action, commander.taken_tick, world)
        return None if action is None or is_over else (slot, action)

    def _commanded_moves(
        self,
        slot: Slot,
        action: Action,
        moves: list[tuple[Move, Slot]],
        dangers: Dangers,
        world: World,
        ctx: MatchContext,
    ) -> list[Intent]:
        """Steer the commanded scout by its standing action, its move added to `moves`; the commander's order for it
        when one is due, none while it flees."""
        scout = world.combat_units[slot.unit_id]
        if action.cell is None:
            move = steer_held(slot.course, scout, dangers, world, ctx)
        else:
            follow(slot.course, action, scout, dangers, world, ctx)
            move = steer_to_mission(slot.course, scout, dangers, world, ctx)
        if move is not None:
            moves.append((move, slot))
        intent = ctx.commander.order(SCOUT_ROLE, _commanded_order(scout, move, action), world)
        return [] if intent is None else [intent]

    def _place_moves(
        self, slots: list[Slot], sight: SightGrid, dangers: Dangers, world: World, ctx: MatchContext
    ) -> list[tuple[Move, Slot]]:
        """The move of the scout in the field of each slot given going from place to place, with its slot."""
        moves: list[tuple[Move, Slot]] = []
        for slot in slots:
            scout = world.combat_units.get(slot.unit_id)
            move = None if scout is None else self._steer_one(slot, scout, sight, dangers, world, ctx)
            if move is not None:
                moves.append((move, slot))
        return moves

    def _mission_moves(
        self,
        slots: list[Slot],
        offered: list[Mission],
        dangers: Dangers,
        world: World,
        ctx: MatchContext,
        *,
        retreating: Collection[int] = (),
    ) -> list[tuple[Move, Slot]]:
        """The move of the scout in the field of each slot given on its mission, among the missions `offered`, with
        its slot; each mission given a `destination` event. The slots `retreating` keep their missions for later."""
        numbers = {slot.number for slot in slots}
        held = {slot.number: slot for slot in self._held(world) if slot.number in numbers}
        scouts = {number: world.combat_units[slot.unit_id] for number, slot in held.items()}
        moves: list[tuple[Move, Slot]] = []
        plan = self._planner.plan(scouts, offered, dangers, world, ctx, paused=retreating)
        for number, assignment in plan.items():
            slot = held[number]
            _follow_mission(slot, assignment, world.tick)
            move = steer_to_mission(slot.course, scouts[number], dangers, world, ctx)
            if move is not None:
                moves.append((move, slot))
        return moves

    def _steer_one(
        self, slot: Slot, scout: Unit, sight: SightGrid, dangers: Dangers, world: World, ctx: MatchContext
    ) -> Move | None:
        """One slot's scout in the field this cycle: a model's answer taken, its move, the `destination` event of a
        change, and the next question to the model when one is due; the move, None while it is to stay."""
        avoid = [other.course.destination for other in self._slots if other is not slot and other.course.destination]
        if slot.question is not None:
            self._take_answer(slot, scout, avoid, sight, dangers, world, ctx)
        move = steer(slot.course, scout, avoid, sight, dangers, world, ctx)
        change = None if move is None else move.change
        if change is not None:
            _log_destination(slot, change)
        if slot.question is not None and slot.question.is_due(slot.course, change is not None, world, ctx):
            slot.question.ask(scout, avoid, sight, dangers, world, ctx)
        return move

    def _take_answer(
        self,
        slot: Slot,
        scout: Unit,
        avoid: list[Cell],
        sight: SightGrid,
        dangers: Dangers,
        world: World,
        ctx: MatchContext,
    ) -> None:
        """Make the place a model has just named, when it is still a safe candidate, the slot scout's destination."""
        cell = slot.question.answer(world)
        if cell is not None and take_answer(slot.course, cell, scout, avoid, sight, dangers, world, ctx):
            _log_destination(slot, ANSWERED)

    def _claim_queues(self, world: World, ctx: MatchContext) -> None:
        """Claim the queue of every unit a slot waits for, and the Vehicle queue while the factory-first opening's scout
        vehicle is to come, and give up the claim on the other unit queue; and on the Vehicle queue while the second
        yard's MCV is due first, so that upkeep orders the MCV on it."""
        waited = {world.items[slot.awaiting].queue for slot in self._slots if slot.awaiting is not None}
        if self._is_vehicle_wanted(ctx):
            waited.add(VEHICLE_QUEUE)
        if ctx.mcv_first_due:
            waited.discard(VEHICLE_QUEUE)
        for queue in _UNIT_QUEUES:
            if queue in waited:
                ctx.claims.hold(queue, self._claimant)
            else:
                ctx.claims.release(queue, self._claimant)


def _new_slots(ctx: MatchContext) -> list[Slot]:
    """The match's slots, each with its own chooser's questions when a model is asked where the scouts go: never with
    `scout_missions` on."""
    tuning = ctx.tuning.scouting
    count, choosers = tuning.scout_slot_count, () if tuning.scout_missions else ctx.scout_choosers
    return [
        Slot(number, question=ScoutQuestion(choosers[number - 1]) if choosers else None)
        for number in range(1, count + 1)
    ]


def _release(slots: list[Slot]) -> None:
    """Hand back to the script the course of each of these slots that the model commander or the rush scout rule set:
    a slot they do not steer in this cycle is steered as the scouting switches say."""
    for slot in slots:
        if slot.course.source in (COMMANDER, RUSH_SCOUT):
            slot.course = Course()


def _commanded_order(scout: Unit, move: Move | None, action: Action) -> Order | None:
    """The order the commanded scout's standing action stands for: a stop while it is held, else a move to the next
    cell of its way; none while it flees, which overrides the model, or stands at its destination."""
    if move is not None and move.urgency == FLEE_URGENCY:
        return None
    if action.cell is None:
        return {"cmd": STOP, "units": [scout.id]}
    return None if move is None else {"cmd": "move", "units": [scout.id], "to": list(move.cell)}


def _follow_mission(slot: Slot, assignment: Assignment, tick: int) -> None:
    """Set a slot's course on its mission's cell by the mission's way, with a `destination` event for a mission it was
    given in this cycle; with no mission, give up the destination it had."""
    option, course = assignment.option, slot.course
    if option is None:
        if course.destination is not None:
            course.give_up(assignment.change or course.reason)
        return
    course.go(option.mission.cell, list(option.legs), MISSION, tick)
    if assignment.change is not None:
        _log(
            DESTINATION,
            slot,
            cell=list(option.mission.cell),
            source=MISSION,
            reason=assignment.change,
            mission=option.mission.kind,
            score=round(option.score, _SCORE_DECIMALS),
        )


def _log_retreat(slot: Slot, begun: Begun, scout: Unit) -> None:
    """Write the `retreat` event of a slot's scout: why, where it stood and is sent, its health, and the cell of the
    enemy it is sent away from (None when it goes back along its way)."""
    retreat = begun.retreat
    enemy = None if retreat.enemy_cell is None else list(retreat.enemy_cell)
    _log(RETREAT, slot, cause=retreat.cause, cell=[scout.x, scout.y], to=list(retreat.to), hp=scout.hp, enemy=enemy)


def _log_returned(unit_id: int, returning: Returning) -> None:
    """Write the `returned` event of a released scout come home and joined to main."""
    records.log(
        SCOUT_SLOT_EVENT,
        {"kind": RETURNED, "slot": returning.slot, "unit_id": unit_id, "unit_type": returning.unit_type},
    )


def _log_destination(slot: Slot, reason: str) -> None:
    """Write the `destination` event of a slot's scout: its new destination, who chose it and why."""
    course = slot.course
    _log(DESTINATION, slot, cell=list(course.destination), source=course.source, reason=reason)


def _refused(slot: Slot, item: str, world: World) -> list[tuple[Slot, str]]:
    """A fast scout the spend cap refuses a slot, with its `capped` event: a slot held is not upgraded, written once for
    each scout that holds it; an empty one is refilled by a rifleman, when one can be trained, in its place."""
    if slot.unit_id is not None:
        if slot.capped_unit != slot.unit_id:
            slot.capped_unit = slot.unit_id
            _log(CAPPED, slot, refused=item, order=UPGRADE)
        return []
    _log(CAPPED, slot, refused=item, order=REPLACEMENT)
    return [(slot, RIFLE_INFANTRY)] if RIFLE_INFANTRY in world.available_production else []


def _replace_floor_seconds(tuning: ScoutingTuning) -> int:
    """How long after the last replacement order the next may be made: capped_replace_seconds with `scout_spend_cap`
    on, else scout_replace_floor_seconds."""
    return tuning.capped_replace_seconds if tuning.scout_spend_cap else tuning.scout_replace_floor_seconds


def _fast_scout(world: World, ctx: MatchContext) -> str:
    """The faster unit the family's scouts become: the Allied jeep, or the Soviet unit soviet_scout_unit names."""
    return ctx.tuning.scouting.soviet_scout_unit if world.family == SOVIET else ALLIED_FAST_SCOUT


def _replacements_on_offer(world: World, ctx: MatchContext, allows_rifle: bool) -> list[str]:
    """The units on offer a slot emptied by a loss may be refilled with: the family's faster scout once it is open (see
    `_is_fast_scout_open`), and the rifleman when it `allows_rifle`."""
    wanted = (_fast_scout(world, ctx), RIFLE_INFANTRY) if _is_fast_scout_open(world, ctx) else (RIFLE_INFANTRY,)
    allowed = [item for item in wanted if item != RIFLE_INFANTRY or allows_rifle]
    return [item for item in allowed if item in world.available_production]


def _is_upgraded(slot: Slot, fast: str, world: World, ctx: MatchContext) -> bool:
    """Whether a slot is to take the family's faster scout: a slower unit holds it; or, while the opening holds
    infantry back (`single_scout_rifle`), it is a slot after the first that was never held, which no infantry takes."""
    if slot.unit_type is not None:
        return _speed(slot.unit_type, world) < _speed(fast, world)
    return ctx.scout_rifle_hold and not slot.was_held and slot.number > 1


def _scout_vehicle(world: World) -> str:
    """The factory-first opening's scout vehicle: the Allied jeep, or the Soviet flak truck."""
    return SOVIET_SCOUT_VEHICLE if world.family == SOVIET else ALLIED_FAST_SCOUT


def _is_fast_scout_open(world: World, ctx: MatchContext) -> bool:
    """Whether the slots may order the family's faster scout: the Soviets' always; the Allied jeep once
    ALLIED_FAST_SCOUT_WAR_FACTORIES war factories stand or the game time reaches allied_fast_scout_after_seconds."""
    if world.family == SOVIET:
        return True
    is_late = world.game_seconds >= ctx.tuning.scouting.allied_fast_scout_after_seconds
    return is_late or ctx.production.standing(WAR_FACTORY) >= ALLIED_FAST_SCOUT_WAR_FACTORIES


def _waits_for_mcv(item: str, world: World, ctx: MatchContext) -> bool:
    """Whether a unit made on the Vehicle queue (the jeep, the flak truck) waits because the second yard's MCV, which
    that queue makes, is due first (`mcv_first` of [upkeep]); a dog, made on the Infantry queue, never does."""
    return ctx.mcv_first_due and world.items[item].queue == VEHICLE_QUEUE


def _speed(item: str, world: World) -> float:
    """How fast a unit of a type moves; 0 for one the item table gives no speed for."""
    facts = world.items.get(item)
    return facts.speed if facts is not None and facts.speed is not None else 0.0


def _log(kind: str, slot: Slot, **more: int | float | str | list[int] | None) -> None:
    """Write one `scout_slot` event of a kind about a slot and the unit that holds it, with what more is given."""
    records.log(
        SCOUT_SLOT_EVENT,
        {"kind": kind, "slot": slot.number, "unit_id": slot.unit_id, "unit_type": slot.unit_type, **more},
    )
