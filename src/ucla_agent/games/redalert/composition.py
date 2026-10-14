"""The plans the agent plays by: what the unit queues make under each, and how strong main must be to attack.

The agent is always in one plan, and the plan says what the Infantry and the
Vehicle queue produce. `CompositionTable` answers that for every plan. An
answer gives each queue's items and the share of the army's value each of
them is to have; the rule that feeds the queues trains, in each, the item the
army is furthest short of.

The shares come from the army mix of the tuning file (`ArmyMix`): a row for
each family and tech stage, after the strongest mixes of the matchup
reference. The stage is the highest whose buildings stand: the base, then a
service depot, which opens the heavier tanks, then a radar as well, which
opens the artillery. A Soviet tech center opens one stage more, with the
Mammoth tank; only the Soviet tank rows have a row for it, and every other
set plays its radar row there. The file holds two sets of rows, and
`main_force` says which the main force is built from: the mixed rows, heavy
in infantry until the service depot, or the tank rows, built around the
medium or heavy tank with artillery or V2s behind it. Both are shifted
alike. Against the enemy army seen the row shifts toward what beats it. An army mostly of vehicles moves
share from the rifles to the rockets, for the Soviets as well: their
grenadiers throw too short and too slow to stop armor;
an army mostly of infantry moves share from the main tank to the rifles and,
once there is artillery, to the artillery. The shift is decided again only now and then, so that the
mix does not swing with every unit that comes into sight, and a small army
shifts nothing. While more than a few enemy aircraft are seen (see
`anti_air`) the row shifts toward anti-air as well, after the counter shift:
for the Soviets from the main tank to the flak truck, the only vehicle of
theirs that hits an aircraft (`air_flak_points`, which the tuning file may
set to 0 to leave the rockets alone to answer aircraft); and the rockets gain
`aa_squad_points` for each rocket squad `anti_air` adds, the row then scaled
back to 100 points, so that the rest keep their proportions. No share goes
below nothing or above the most a share may be.

A Soviet main force of the tank rows has one more answer to massed infantry,
which the table gives with `soviet_infantry_counter` on: from the dome stage
on, while infantry make up at least `enemy_infantry_share` of the enemy
units that fight seen in the last two minutes, counted by unit, the heavy
tank gives up `flamers_share` points to the flamethrowers and
`v2_infantry_bonus` more to the V2s. A flamethrower needs a flame tower, and
nothing builds one for it: while none is on offer, its points go to the V2s
as well. Each point moved is taken from the heavy tank, so the row still
holds 100, and no share goes above the most a share may be.

What a queue cannot train is left out of the answer, and its share goes to
the rest of the same queue: an item not on offer, an item on the list of
those the mix never builds (they cost more than they are worth in a fight,
or do not fight), and an item the item table does not put in that queue,
which keeps aircraft and ships out.

With the tank rows in force and `main_vehicles_need_depot` on, the Vehicle
queue trains nothing of the mix until a service depot stands: the base
rows' light tanks and flak trucks are no main tanks, and the money goes to
the depot first. The raid quota is filled from the row's vehicle share all
the same, so light tanks come only through it.

- OPENING: nothing. The opening has its own orders for the Infantry queue
  (the home guard, then rifles for `main`), which it issues itself, and
  builds no vehicle before the war factory stands.
- HOLD, STRIKE and FINISH: the mix.
- RAID and PRESS: the mix, but the raid quota (`raid_quota`) is filled
  first: while it is short of an item, the first such item of each queue
  takes the whole share of that queue, a vehicle only while it is on offer. The quota counts only
  the units the raid can have (`raid_units`): a unit of main gone forward or
  damaged is not one of them, and production goes on until the raid group
  can be filled. With raids switched off (`[raids].enabled`), they make the
  mix alone, and nothing is trained for a raid. The quota's items are trained
  for the raid even when the mix never builds them, such as the APCs of the
  Soviet APC raid. A standing raid group
  (`[raids].standing`) raids under HOLD, STRIKE and FINISH as well, and under
  each of them too the quota is filled first. An answer says when the
  Infantry queue's item is the quota's (`raid_infantry`), so that a rule that
  holds back the army's infantry leaves the raid's alone.
- PRESERVE: the mix's infantry and no vehicle; the money goes to defenses.

`AttackStrengths` gives the strength `main` must be worth before it goes
forward under each plan: more than any army is worth under the plans that do
not attack, so that it never does.

Which plan is in force is the plan selector's to say (see `plans`).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Mapping

from ucla_agent.games.redalert.anti_air import AirWatch, is_air_threat, rocket_squads_wanted
from ucla_agent.games.redalert.family import (
    APC,
    FAMILIES,
    RADAR,
    RIFLE_INFANTRY,
    ROCKET_INFANTRY,
    SERVICE_DEPOT,
    WAR_FACTORY,
    Family,
)
from ucla_agent.games.redalert.items import ALLIES, SOVIET, TICKS_PER_SECOND
from ucla_agent.games.redalert.production import INFANTRY_QUEUE, VEHICLE_QUEUE
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.tuning import (
    APC_RAID,
    MIX_ROW_POINTS,
    MIX_STAGES,
    NO_RAID,
    TANKS,
    TECH_STAGE,
    AntiAirTuning,
    CompositionTuning,
    MixRow,
    MixTuning,
    PlanRulesTuning,
)
from ucla_agent.games.redalert.world import World

OPENING = "OPENING"
HOLD = "HOLD"
RAID = "RAID"
PRESS = "PRESS"
STRIKE = "STRIKE"
FINISH = "FINISH"
PRESERVE = "PRESERVE"
# The plans under which the raid group raids, and production keeps its quota filled ...
RAIDING_PLANS = frozenset({RAID, PRESS})
# ... and those of a standing raid group, which raids alongside main whatever main does: every plan after the opening
# but PRESERVE, under which a raid lost would be a loss the enemy scores.
STANDING_RAIDING_PLANS = frozenset({HOLD, RAID, PRESS, STRIKE, FINISH})
BASE_STAGE, DEPOT_STAGE, RADAR_STAGE = MIX_STAGES
# The Soviet tech center, which opens the stage above the radar's.
TECH_CENTER = TECH_STAGE
# The stages at which the shift against infantry moves share to the artillery as well: those with it on offer.
_ARTILLERY_STAGES = frozenset({RADAR_STAGE, TECH_STAGE})
# The counter shifts, by the names the timing event gives them: none, against vehicles, against infantry.
NO_SHIFT = "none"
VEHICLE_SHIFT = "vehicles"
INFANTRY_SHIFT = "infantry"
# The air shift by the same name, and what joins it to a counter shift in force with it: `vehicles+air`.
AIR_SHIFT = "air"
_SHIFT_JOINER = "+"
# What the mix never trains, whatever a row says. The raid quota's own items are the raid's to train.
NEVER_BUILT = frozenset(
    {
        "dog",
        "jeep",
        "apc",
        "stnk",
        "ctnk",
        "ttnk",
        "e7",
        "shok",
        "e6",
        "medi",
        "mech",
        "spy",
        "thf",
        "mnly",
        "truk",
        "qtnk",
        "dtrk",
        "mgg",
        "mrj",
    }
)
# The Soviet grenadier, the infantry of the Soviet raid on foot.
_GRENADIER = "e2"
# The Soviet flak truck, which gains from the main tank in the shift toward anti-air.
_FLAK_TRUCK = "ftrk"
# The infantry of the raid quotas: the grenadiers that raid on foot, and the rockets the APC raid carries.
_RAID_INFANTRY = frozenset({_GRENADIER, ROCKET_INFANTRY})
# The Soviet flamethrower, which the infantry counter adds against massed infantry while a flame tower puts it on offer.
FLAMETHROWER = "e4"
# How far back the enemy units seen count toward the infantry counter: the army the enemy fields now, not the opening's.
_INFANTRY_SEEN_SECONDS = 120

# How many units of a type count toward the raid quota: called as `raid_units(world, unit_type)`.
RaidUnits = Callable[[World, str], int]
# An item of a row, by the queue that trains it: (queue, item).
_Slot = tuple[str, str]
# A row as points of the army's value, by queue and item.
_Points = dict[str, dict[str, float]]


@dataclass(frozen=True)
class _Counters:
    """The items between which a family's counter shift moves share, besides the rifles and the main tank."""

    # Against vehicles the rifles give up their points to these, in equal parts.
    anti_armor: tuple[str, ...]
    # Against infantry at the radar stage the main tank gives up points to this as well.
    artillery: str
    # Against air the main tank gives up points to this vehicle; None for a family whose vehicles of the mix cannot
    # hit an aircraft.
    flak: str | None


_COUNTERS = {
    ALLIES: _Counters(anti_armor=(ROCKET_INFANTRY,), artillery="arty", flak=None),
    SOVIET: _Counters(anti_armor=(ROCKET_INFANTRY,), artillery="v2rl", flak=_FLAK_TRUCK),
}


@dataclass(frozen=True)
class Composition:
    """What the two unit queues make under a plan: each queue's items, with the share of the army's value each is to
    have, and whether the Infantry queue's item is the raid quota's."""

    infantry: Mapping[str, float] = field(default_factory=dict)
    vehicle: Mapping[str, float] = field(default_factory=dict)
    # Whether the Infantry queue makes the raid quota's item, which the army mix does not decide.
    raid_infantry: bool = False

    @property
    def vehicle_share(self) -> float:
        """The Vehicle queue's share of the army's value, and so of the unit spending: its items' shares added up."""
        return sum(self.vehicle.values())


def raiding_plans(enabled: bool, standing: bool) -> frozenset[str]:
    """The plans under which the raid group raids: none with raids off, every plan after the opening but PRESERVE for
    a standing raid group, else RAID and PRESS."""
    if not enabled:
        return frozenset()
    return STANDING_RAIDING_PLANS if standing else RAIDING_PLANS


def mix_stage(world: World) -> str:
    """The tech stage of the army mix: the highest whose buildings stand, a tech center above all."""
    if _stands(world, TECH_CENTER):
        return TECH_STAGE
    if not _stands(world, SERVICE_DEPOT):
        return BASE_STAGE
    return RADAR_STAGE if _stands(world, RADAR) else DEPOT_STAGE


class ArmyMix:
    """The army mix of the tuning file: a family's row at the base's stage, shifted toward what beats the enemy army
    seen, and toward anti-air once the enemy has shown air power. See the module's note."""

    def __init__(self, tuning: MixTuning, anti_air: AntiAirTuning, air: AirWatch) -> None:
        """A mix of the [mix] thresholds that shifts by the [anti_air] ones while `air` has seen enemy air."""
        self._tuning = tuning
        self._anti_air = anti_air
        self._air = air
        self._shift = NO_SHIFT
        # The tick the shift in force was decided at; None before the first time.
        self._decided_at: int | None = None

    @property
    def is_tank_force(self) -> bool:
        """Whether the main force is built from the tank rows."""
        return self._tuning.main_force == TANKS

    @property
    def max_share(self) -> int:
        """The most points one item may hold in a row."""
        return self._tuning.max_share

    def counter_shift(self, world: World) -> str:
        """The counter shift in force, decided anew from the enemy army seen once `counter_every_ticks` have passed."""
        tuning = self._tuning
        if self._decided_at is None or world.tick - self._decided_at >= tuning.counter_every_ticks:
            self._shift = _counter_shift(world, tuning)
            self._decided_at = world.tick
        return self._shift

    def shifts(self, world: World) -> str:
        """The shifts in force, by the names the timing event gives them: the counter shift alone, `air` alone, or
        the two joined (`vehicles+air`)."""
        counter = self.counter_shift(world)
        if not is_air_threat(self._air, world.tick, self._anti_air):
            return counter
        return AIR_SHIFT if counter == NO_SHIFT else f"{counter}{_SHIFT_JOINER}{AIR_SHIFT}"

    def points(self, family: str, world: World) -> _Points:
        """The family's row, of the rows the main force is built from, at the stage its base has reached (the radar
        row at the tech center's stage for a set without a row there), with the counter shift in force and then,
        while more than a few enemy aircraft are seen, the air shift and the rocket squads it adds, in points by
        queue."""
        tuning, stage = self._tuning, mix_stage(world)
        rows = _rows_in_force(tuning)[family]
        row = rows.get(stage, rows[RADAR_STAGE])
        points = {INFANTRY_QUEUE: dict(row.infantry), VEHICLE_QUEUE: dict(row.vehicle)}
        moves = _moves(self.counter_shift(world), family, stage, tuning)
        is_air = is_air_threat(self._air, world.tick, self._anti_air)
        if is_air:
            moves += _air_moves(family, self._anti_air)
        for giver, receiver, wanted in moves:
            _move(points, giver, receiver, wanted, tuning.max_share)
        squads = rocket_squads_wanted(self._air, world.tick, self._anti_air) if is_air else 0
        if squads:
            _add_rockets(points, squads * self._anti_air.aa_squad_points, tuning.max_share)
        return points


def _rows_in_force(tuning: MixTuning) -> Mapping[str, Mapping[str, MixRow]]:
    """The rows the main force is built from, by family and stage: the tank rows with `main_force` "tanks", else the
    mixed rows."""
    return tuning.tank_rows if tuning.main_force == TANKS else tuning.rows


def seen_army_value(world: World) -> Counter[str | None]:
    """The enemy army seen: what its remembered units that fight and are not stale cost, by the queue that makes them."""
    value: Counter[str | None] = Counter()
    for record in world.enemy_memory.values():
        facts = world.items.get(record.type)
        if facts is not None and is_combat_unit(record) and not record.stale:
            value[facts.queue] += facts.cost
    return value


def is_infantry_heavy(world: World, share: float) -> bool:
    """Whether infantry make up at least `share` of the enemy units that fight seen in the last two minutes, by count;
    never while none has been seen."""
    since = world.tick - _INFANTRY_SEEN_SECONDS * TICKS_PER_SECOND
    seen = infantry = 0
    for record in world.enemy_memory.values():
        facts = world.items.get(record.type)
        if facts is None or not is_combat_unit(record) or record.last_seen_tick < since:
            continue
        seen += 1
        infantry += facts.queue == INFANTRY_QUEUE
    return seen > 0 and infantry >= share * seen


def _counter_infantry(points: _Points, world: World, tuning: CompositionTuning, max_share: int) -> None:
    """Move the Soviet infantry counter's points from the heavy tank: `v2_infantry_bonus` to the V2s, and
    `flamers_share` to the flamethrowers while they are on offer, else to the V2s as well."""
    heavy_tank = (VEHICLE_QUEUE, FAMILIES[SOVIET].heavy_vehicle)
    v2s = (VEHICLE_QUEUE, _COUNTERS[SOVIET].artillery)
    flamers = (INFANTRY_QUEUE, FLAMETHROWER) if _can_train(FLAMETHROWER, INFANTRY_QUEUE, world) else v2s
    _move(points, heavy_tank, v2s, float(tuning.v2_infantry_bonus), max_share)
    _move(points, heavy_tank, flamers, float(tuning.flamers_share), max_share)


def _counter_shift(world: World, tuning: MixTuning) -> str:
    """The shift the enemy army seen calls for, by its value."""
    value = seen_army_value(world)
    worth = sum(value.values())
    if worth < tuning.counter_min_value:
        return NO_SHIFT
    if value[VEHICLE_QUEUE] >= tuning.vehicle_heavy_share * worth:
        return VEHICLE_SHIFT
    return INFANTRY_SHIFT if value[INFANTRY_QUEUE] >= tuning.infantry_heavy_share * worth else NO_SHIFT


def _moves(shift: str, family: str, stage: str, tuning: MixTuning) -> list[tuple[_Slot, _Slot, float]]:
    """The moves of share a counter shift makes: from which item, to which, and how many points at most.

    Only the main tank gives up points against infantry: a stage without it
    in its row moves nothing then.
    """
    rifles, counters = (INFANTRY_QUEUE, RIFLE_INFANTRY), _COUNTERS[family]
    if shift == VEHICLE_SHIFT:
        each = tuning.vehicle_counter_points / len(counters.anti_armor)
        return [(rifles, (INFANTRY_QUEUE, item), each) for item in counters.anti_armor]
    if shift == NO_SHIFT:
        return []
    main_tank = (VEHICLE_QUEUE, FAMILIES[family].heavy_vehicle)
    moves = [(main_tank, rifles, float(tuning.infantry_counter_points))]
    if stage in _ARTILLERY_STAGES:
        moves.append((main_tank, (VEHICLE_QUEUE, counters.artillery), float(tuning.artillery_counter_points)))
    return moves


def _air_moves(family: str, tuning: AntiAirTuning) -> list[tuple[_Slot, _Slot, float]]:
    """The move of share the air shift makes: from the main tank to the flak vehicle of a family that has one; none for
    a family without.

    A row without the main tank in it, which at the base stage has the flak truck for its vehicle, moves nothing then.
    """
    flak = _COUNTERS[family].flak
    if flak is None:
        return []
    main_tank = (VEHICLE_QUEUE, FAMILIES[family].heavy_vehicle)
    return [(main_tank, (VEHICLE_QUEUE, flak), float(tuning.air_flak_points))]


def _add_rockets(points: _Points, wanted: float, max_share: int) -> None:
    """Add up to `wanted` points to the rockets and scale the row back to 100 points, so that every other item keeps
    its proportion to the rest; no more than leaves the rockets at most `max_share` once scaled."""
    taken = points[INFANTRY_QUEUE].get(ROCKET_INFANTRY, 0)
    added = max(0.0, min(wanted, _rocket_room(taken, max_share)))
    points[INFANTRY_QUEUE][ROCKET_INFANTRY] = taken + added
    scale = MIX_ROW_POINTS / (MIX_ROW_POINTS + added)
    for queue_points in points.values():
        for item in queue_points:
            queue_points[item] *= scale


def _rocket_room(taken: float, max_share: int) -> float:
    """The most points that may be added to rockets holding `taken` before the row is scaled back to 100: (taken +
    added) / (100 + added) may reach max_share / 100 at most; at a max_share of 100 any number may be added."""
    if max_share >= MIX_ROW_POINTS:
        return math.inf
    return MIX_ROW_POINTS * (max_share - taken) / (MIX_ROW_POINTS - max_share)


def _move(points: _Points, giver: _Slot, receiver: _Slot, wanted: float, max_share: int) -> None:
    """Move up to `wanted` points from one item to another: no more than the giver has or the receiver may take."""
    (giver_queue, giver_item), (receiver_queue, receiver_item) = giver, receiver
    given = points[giver_queue].get(giver_item, 0)
    taken = points[receiver_queue].get(receiver_item, 0)
    moved = min(wanted, given, max_share - taken)
    points[giver_queue][giver_item] = given - moved
    points[receiver_queue][receiver_item] = taken + moved


def _trainable(points: Mapping[str, float], queue: str, world: World) -> dict[str, float]:
    """A queue's part of a row as shares of the army's value, with only the items the queue can train in it.

    The points of an item left out are spread over the queue's other items in
    proportion to theirs; when none is left, the queue trains nothing.
    """
    kept = {item: share for item, share in points.items() if share > 0 and _can_train(item, queue, world)}
    kept_points, queue_points = sum(kept.values()), sum(points.values())
    return {item: share / kept_points * queue_points / MIX_ROW_POINTS for item, share in kept.items()}


def _can_train(item: str, queue: str, world: World) -> bool:
    """Whether the mix may train an item in a queue: on offer, not on the never-built list, and made by that queue."""
    facts = world.items.get(item)
    is_of_queue = facts is not None and facts.queue == queue
    return is_of_queue and item in world.available_production and item not in NEVER_BUILT


class CompositionTable:
    """The production of each plan. Called as `table(plan, family, world)`."""

    def __init__(
        self,
        tuning: CompositionTuning,
        mix: ArmyMix,
        raid_units: RaidUnits,
        raids_enabled: bool,
        standing: bool = False,
    ) -> None:
        """A table over an army mix; `raid_units` says how many units of a type the raid quota counts. With
        `raids_enabled` false no raid quota is made, and with `standing` the quota is filled first under every plan a
        standing raid group raids in."""
        self._tuning = tuning
        self._mix = mix
        self._raid_units = raid_units
        self._raiding_plans = raiding_plans(raids_enabled, standing)

    def __call__(self, plan: str, family: str, world: World) -> Composition:
        """What the Infantry and Vehicle queues make under a plan, for a family, as the world stands."""
        if plan == OPENING:
            return Composition()
        points = self._mix.points(family, world)
        if self._counters_infantry(family, world):
            _counter_infantry(points, world, self._tuning, self._mix.max_share)
        infantry = _trainable(points[INFANTRY_QUEUE], INFANTRY_QUEUE, world)
        if plan == PRESERVE:
            return Composition(infantry=infantry)
        vehicle = {} if self._holds_main_vehicles(world) else _trainable(points[VEHICLE_QUEUE], VEHICLE_QUEUE, world)
        if plan not in self._raiding_plans:
            return Composition(infantry=infantry, vehicle=vehicle)
        wanted = raid_quota(family, world, self._tuning)
        raid_infantry = _quota(self._short_of(wanted, INFANTRY_QUEUE, world), points[INFANTRY_QUEUE])
        return Composition(
            infantry=raid_infantry or infantry,
            vehicle=_quota(self._short_of(wanted, VEHICLE_QUEUE, world), points[VEHICLE_QUEUE]) or vehicle,
            raid_infantry=bool(raid_infantry),
        )

    def _counters_infantry(self, family: str, world: World) -> bool:
        """Whether the Soviet infantry counter is in force: `soviet_infantry_counter` on, a Soviet main force of the
        tank rows at the dome stage or above, and an infantry-heavy enemy seen."""
        tuning = self._tuning
        is_soviet_tanks = tuning.soviet_infantry_counter and family == SOVIET and self._mix.is_tank_force
        if not is_soviet_tanks or mix_stage(world) not in _ARTILLERY_STAGES:
            return False
        return is_infantry_heavy(world, tuning.enemy_infantry_share)

    def _holds_main_vehicles(self, world: World) -> bool:
        """Whether the Vehicle queue trains no vehicle of the mix: `main_vehicles_need_depot` on, main built around
        tanks and no service depot standing. The raid quota takes the row's vehicle share all the same."""
        is_held = self._tuning.main_vehicles_need_depot and self._mix.is_tank_force
        return is_held and not _stands(world, SERVICE_DEPOT)

    def _short_of(self, wanted: Mapping[str, int], queue: str, world: World) -> str | None:
        """The first item of the raid quota that a queue makes and the quota is short of; None when there is none.

        A vehicle is the quota's only while it is on offer: the Allied quota names the light tank when neither raid
        vehicle can be had, and the Vehicle queue then makes the mix.
        """
        for item, count in wanted.items():
            if _raid_queue(item) != queue:
                continue
            if queue == VEHICLE_QUEUE and item not in world.available_production:
                continue
            if self._raid_units(world, item) < count:
                return item
        return None


def _raid_queue(item: str) -> str:
    """The queue that trains an item of a raid quota: the Infantry queue its grenadiers and rocket soldiers, the
    Vehicle queue every other item of it."""
    return INFANTRY_QUEUE if item in _RAID_INFANTRY else VEHICLE_QUEUE


def raid_quota(family: str, world: World, tuning: CompositionTuning) -> dict[str, int]:
    """How many units of each type the raid group of a family is made of, as the world stands; none while it has no
    raid."""
    names = FAMILIES[family]
    if names.raid_infantry is None:
        return _allied_quota(names, world, tuning)
    return _soviet_quota(names, world, tuning)


def _allied_quota(names: Family, world: World, tuning: CompositionTuning) -> dict[str, int]:
    """The Allied raid: `raid_vehicles` light tanks once a war factory stands with `allied_raid_light_tanks`, else
    as many of the first raid vehicle on offer, the light tank while neither is."""
    if tuning.allied_raid_light_tanks:
        return {names.light_vehicle: tuning.raid_vehicles} if _stands(world, WAR_FACTORY) else {}
    offered = [item for item in names.raid_vehicles if item in world.available_production]
    return {(offered or names.raid_vehicles)[0]: tuning.raid_vehicles}


def _soviet_quota(names: Family, world: World, tuning: CompositionTuning) -> dict[str, int]:
    """The Soviet raid `soviet_raid` picks: none at all; rocket soldiers carried by APCs, once a war factory and a
    barracks stand; or grenadiers, which raid with flak trucks once a war factory stands."""
    raid, has_factory = tuning.soviet_raid, _stands(world, WAR_FACTORY)
    if raid == NO_RAID:
        return {}
    if raid == APC_RAID:
        is_open = has_factory and _stands(world, names.barracks)
        return {APC: tuning.raid_apcs, ROCKET_INFANTRY: tuning.raid_apcs * tuning.raid_apc_rockets} if is_open else {}
    if has_factory:
        return {names.light_vehicle: tuning.raid_mixed_vehicles, names.raid_infantry: tuning.raid_mixed_infantry}
    return {names.raid_infantry: tuning.raid_infantry}


def _quota(item: str | None, points: Mapping[str, float]) -> dict[str, float]:
    """A raid quota's item with the whole share of its queue in the row; nothing while the quota is met."""
    return {} if item is None else {item: sum(points.values()) / MIX_ROW_POINTS}


class AttackStrengths:
    """The strength main needs before it goes forward under each plan. Called as `strengths(plan)`."""

    def __init__(self, tuning: PlanRulesTuning) -> None:
        self._by_plan = {
            OPENING: tuning.opening_attack_strength,
            HOLD: tuning.hold_attack_strength,
            RAID: tuning.raid_attack_strength,
            PRESS: tuning.press_attack_strength,
            STRIKE: tuning.strike_attack_strength,
            FINISH: tuning.finish_attack_strength,
            PRESERVE: tuning.preserve_attack_strength,
        }

    def __call__(self, plan: str) -> int:
        """The strength main needs under a plan."""
        return self._by_plan[plan]


def _stands(world: World, building_type: str) -> bool:
    return any(building.type == building_type for building in world.buildings.values())
