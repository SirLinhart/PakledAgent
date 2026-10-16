"""The parts of the agent that decide what to order, and what they share.

The agent's play is divided among managers, each with one concern: keeping
the base running, playing the opening, defending, scouting, fighting. In
every cycle each manager looks at the world and proposes intents, and the
loop turns what they proposed into one batch.

The managers always run in the same order, which is fixed here and not chosen
by anything at run time: base defense first, so that everything after it
knows of a threat, then upkeep, the opening, scouting, combat and raids. A
manager is registered under the name of its slot. A slot that has no manager
is simply left out, so that a manager written later takes its place without
any change to the loop or to the managers around it.

What the managers of one match share is in its `MatchContext`: the settings,
the order ledger, the groups, the terrain, the plan in force, the opening's
build in force, the creep target, the fields placement skipped, the fields
refineries claim, the fields refineries were asked against lately, the field
kept for the MCV's site, the routes walked to the far fields, the view of
production that is made afresh for every cycle, and the rolling estimate of
income and spending, the watch on the enemy's air and the graph of waypoints
that every cycle brings up to date. Managers speak to each other only through
it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Callable, Iterable, Iterator, Mapping, Protocol

from ucla_agent.core.decide import ChoiceRequest, ChoiceResult
from ucla_agent.games.redalert import raids
from ucla_agent.games.redalert.anti_air import AirWatch
from ucla_agent.games.redalert.batch import Intent
from ucla_agent.games.redalert.composition import OPENING, ArmyMix, AttackStrengths, Composition, CompositionTable
from ucla_agent.games.redalert.family import RIFLE_INFANTRY
from ucla_agent.games.redalert.groups import Groups
from ucla_agent.games.redalert.income import IncomeEstimate
from ucla_agent.games.redalert.ledger import LedgerEntry, OrderLedger
from ucla_agent.games.redalert.model_history import ModelHistory
from ucla_agent.games.redalert.placement import Box, CreepTarget, RefusedPlace
from ucla_agent.games.redalert.production import Production
from ucla_agent.games.redalert.scout_report import ScoutReport
from ucla_agent.games.redalert.spread import InfantrySpread
from ucla_agent.games.redalert.targeting import TargetMemory
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.waypoints import Waypoints
from ucla_agent.games.redalert.world import Cell, EnemyRecord, World

if TYPE_CHECKING:
    # The commander reads the match context, so the context names its type only for the annotation.
    from ucla_agent.games.redalert.commander import Commander
    from ucla_agent.games.redalert.failed_attack import FailedAttack
    from ucla_agent.games.redalert.exposed_trucks import ExposedTrucks

# The slots of the managers, in the order they run.
MANAGER_ORDER = ("base_defense", "upkeep", "opening", "scouting", "combat", "raids")
# How big the threat to the base is, as the base-defense manager publishes it.
NO_THREAT = "none"
MINOR_THREAT = "minor"
MAJOR_THREAT = "major"
# The spending focus upkeep decides (see `spending_focus`): the money to the army, to the economy, or spent as always.
# Named here, where the rules that read it find it without an import of the module that decides it.
MILITARY_FOCUS = "military"
ECONOMY_FOCUS = "economy"
BALANCED_FOCUS = "balanced"


class Manager(Protocol):
    """One concern of the agent's play."""

    # The slot the manager runs in: one of `MANAGER_ORDER`.
    name: str

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """What the manager wants ordered in this cycle."""


class Chooser(Protocol):
    """What the loop needs of the asynchronous chooser: neither call ever waits for a decision source."""

    def submit(self, request: ChoiceRequest) -> bool:
        """Start asking; False when an earlier question is still open."""

    def poll(self, current_state_tag: str) -> ChoiceResult | None:
        """The result of the question in flight, once; None while there is none to hand over."""


class ManagerList:
    """The managers of one agent, kept in the fixed order whatever order they were registered in."""

    def __init__(self, managers: Iterable[Manager] = ()) -> None:
        self._by_slot: dict[str, Manager] = {}
        for manager in managers:
            self.register(manager)

    def register(self, manager: Manager) -> None:
        """Put a manager into the slot it names. Raises `ValueError` for an unknown slot or one already taken."""
        if manager.name not in MANAGER_ORDER:
            raise ValueError(f"'{manager.name}' is not a manager slot; the slots are {', '.join(MANAGER_ORDER)}")
        if manager.name in self._by_slot:
            raise ValueError(f"the manager slot '{manager.name}' is already taken")
        self._by_slot[manager.name] = manager

    def __iter__(self) -> Iterator[Manager]:
        return (self._by_slot[slot] for slot in MANAGER_ORDER if slot in self._by_slot)


@dataclass(frozen=True)
class OpeningBuild:
    """The opening's build in force, as the opening manager publishes it in every cycle for the managers after it
    and, in the next cycle, before it."""

    # What the Building queue makes in the opening, in order; none once the build is complete.
    steps: tuple[str, ...] = ()
    # Whether the build holds a power plant to be placed toward the first refinery's ore field.
    creeps: bool = False


class QueueClaims:
    """Which managers want a production queue left to them.

    The rule that keeps the unit queues busy feeds a queue only while no other
    manager claims it, so a manager with orders of its own for a queue (the
    opening raising the home guard, a defense training counters) is not
    crowded out by routine production.
    """

    def __init__(self) -> None:
        self._claimants: dict[str, set[str]] = {}

    def hold(self, queue: str, claimant: str) -> None:
        """Claim a queue for a manager; claiming twice changes nothing."""
        self._claimants.setdefault(queue, set()).add(claimant)

    def release(self, queue: str, claimant: str) -> None:
        """Give up a manager's claim on a queue; a claim it does not hold changes nothing."""
        self._claimants.get(queue, set()).discard(claimant)

    def is_held_by_other(self, queue: str, claimant: str) -> bool:
        """Whether a manager other than `claimant` claims a queue."""
        return bool(self._claimants.get(queue, set()) - {claimant})


@dataclass(frozen=True)
class DetachedUnits:
    """A detachment of the model commander's out of main: its kind, its units and the enemies it is on."""

    kind: str
    units: frozenset[int]
    targets: frozenset[int]


@dataclass
class MatchContext:
    """What the managers of one match share. A new match gets a new context."""

    tuning: Tuning
    ledger: OrderLedger
    chooser: Chooser
    # One chooser for each scout slot, asked where its scout goes next; none while no model is asked (see
    # `scout_choice`). Each is apart from the plan's `chooser`, so that no question waits for another.
    scout_choosers: tuple[Chooser, ...] = ()
    # The model commander, which picks one checked action a batch for the first scout, then for main, over a chooser
    # of its own; None with `[redalert] model_commander` off (see `commander`).
    commander: Commander | None = None
    # The models' recent picks of the match and the plan rules' army ratio of the cycle, which the plan, commander and
    # scout questions show (see `model_history`).
    model_history: ModelHistory = field(default_factory=ModelHistory)
    terrain: Terrain = field(default_factory=Terrain)
    groups: Groups = field(default_factory=Groups)
    claims: QueueClaims = field(default_factory=QueueClaims)
    # The plan in force, set by the loop before the managers run.
    plan: str = OPENING
    # The rule of the plan selector that set the plan in force (`R1` to `R7`, `model`, `sparring`); None before
    # the first plan is selected.
    plan_rule: str | None = None
    # Whether main is worth big_army_value of [plan_rules] and no enemy unit of its army has been in sight for
    # unknown_army_seconds, set by the plan selector before the managers run; the scout missions then look for that
    # army (see `scout_mission_planner`).
    big_army_unseen_enemy: bool = False
    # The seat the agent plays (`Multi0`, `Multi1`) and whether the match is a test or a tournament match, as the
    # first state and the runtime say; None until the match has begun.
    seat: str | None = None
    match_kind: str | None = None
    # The platform's id of the match's session, taken in with the first state; None before it and in a test without.
    session_id: str | None = None
    # Set by the base-defense manager while the base is under attack; the opening starts nothing new then.
    threat_active: bool = False
    # The opening's build in force, published by the opening manager; none before its first cycle.
    opening_build: OpeningBuild = field(default_factory=OpeningBuild)
    # And how big the threat is: NO_THREAT, MINOR_THREAT or MAJOR_THREAT.
    threat_level: str = NO_THREAT
    # And the cell of the own building or harvester the threat is against; None while there is no threat.
    threat_cell: Cell | None = None
    # The last tick an own building was hit in a threat not of aircraft alone, set by the base-defense manager; None
    # while none was.
    ground_hit_tick: int | None = None
    # Whether every enemy seen in the threat in force has been an aircraft, set by the base-defense manager; False while
    # there is no threat.
    threat_aircraft_only: bool = False
    # The cell each defense the base-defense manager ordered is to stand nearest, by item, while it is on its way: the
    # side of the base facing a threat, or a place in range of an enemy building. Upkeep's placement reads it.
    defense_goals: dict[str, Cell] = field(default_factory=dict)
    # The items of `defense_goals` whose goal is a place of its own (the harass tower's in range of an enemy building,
    # the endgame lead's point), which the line across the lane leaves as it is (see `defense_line`).
    exact_defense_goals: set[str] = field(default_factory=set)
    # The last tick base defense wrote that an MCV's escort was kept on its way (see `base_defense`); None before.
    escort_kept_tick: int | None = None
    # Whether the spending focus holds a new MCV back (see `spending_focus`), published by upkeep in every cycle.
    mcv_held_by_focus: bool = False
    # The cells of the match's vehicle attacks on the base, in the order they came: one for each threat that was led
    # by vehicles and hit an own building or harvester, added by the base-defense manager.
    vehicle_attacks: list[Cell] = field(default_factory=list)
    # The harvesters the base-defense manager has sent to shelter, which it sends back to work itself.
    fleeing_harvesters: frozenset[int] = frozenset()
    # With `truck_shares_by_mines` of [upkeep] on, the field each truck works: the one it is assigned to, while that
    # field is active and not dry (see `truck_shares`). Every order that sends a truck back to harvest names an ore cell
    # of it. Published by upkeep in every cycle; base defense, which runs first, reads that of the cycle before. Empty
    # with the switch off.
    truck_fields: Mapping[int, OrePatch] = field(default_factory=dict)
    # The ore field the base creeps toward, published by upkeep in every cycle: the far field with `creep_from_opening`
    # on while there is one, else the next field along the lane to the enemy; None with both off and while there is
    # none.
    creep_target: CreepTarget | None = None
    # The far fields placement has skipped, in the order they were skipped: the engine kept putting the buildings
    # placed toward them elsewhere. Added to by upkeep with `refused_detection` on; none ever with it off.
    skipped_fields: list[OrePatch] = field(default_factory=list)
    # The far fields the base reached that a refinery is to claim, the oldest first, published by upkeep in every cycle
    # with `refinery_claims_reached` on; none ever with it off.
    claim_fields: list[OrePatch] = field(default_factory=list)
    # Whether a refinery claim is due: a claim field, and fewer than `claim_refineries_max` refineries standing or on
    # their way, so that a claim that could not be ordered is never due. Published by upkeep in every cycle.
    refinery_claim_due: bool = False
    # The ore fields the refineries asked onto the map within `promise_ticks` stand against, and the field a claim or
    # growth refinery on order is for: with a refinery standing against them, the fields that are taken. Published by
    # upkeep at the start of every cycle, before any of its rules is asked, with `refinery_spread` of [placement],
    # `refinery_growth` of [upkeep] or `mcv_new_area` of [expansion] on; none with all three off. With them, the
    # middle cells of those refineries' places.
    asked_refinery_fields: list[OrePatch] = field(default_factory=list)
    asked_refinery_cells: list[Cell] = field(default_factory=list)
    # With `mcv_first` of [upkeep] and `second_yard` of [expansion] on: whether the second yard's MCV is due first, and
    # whether the full hold for its money is in force (see `economy_hold`). Published by upkeep at the start of every
    # cycle, before any of its rules is asked; always false with either switch off.
    mcv_first_due: bool = False
    full_hold: bool = False
    # With `mcv_reserve_field` of [expansion] on: the field of the MCV's site, while the MCV is on order, out and
    # waiting or on its way, which no refinery is asked against and no claim is for (see `expansion`). Published by
    # upkeep at the start of every cycle, before any of its rules is asked; None with the switch off and while there is
    # no such MCV.
    reserved_field: OrePatch | None = None
    # With `mcv_repeat` of [expansion] on: where a further MCV deploys, and the field it deploys by, while one may be
    # wanted or one out is to be sited (see `expansion`). Published by upkeep once a cycle, right after the fields
    # refineries were asked against; None with the switch off and while there is no such site.
    repeat_mcv_site: Cell | None = None
    repeat_mcv_field: OrePatch | None = None
    # The middle of the yard box of the site the travelling MCV goes to (see `expansion.ForwardMcv.site_box`), which
    # the closing-army intercept watches (see `closing_intercept`). Published by upkeep at the start of every cycle;
    # None while no MCV lives with a site it has not deployed at.
    mcv_site: Cell | None = None
    # Where the travelling MCV holds instead of going on to its site, while main defends or holds against an enemy army
    # closing on the MCV or its site (see `closing_intercept`): its cell as the hold began, or base_center once the
    # army keeps closing on it. Published by the combat manager in every cycle; the MCV's rule (see `expansion`), which
    # runs before it, reads that of the cycle before. None while the MCV does not hold.
    mcv_halt: Cell | None = None
    # With `second_yard` of [expansion] on: the field of the site an expansion yard deployed by, while the one refinery
    # claimed for it is due (see `expansion`): fewer than `max_refineries_per_field` refineries serve it or are asked
    # against it, and fewer than `claim_refineries_max` refineries stand or are on their way. Published by upkeep at the
    # start of every cycle, before any of its rules is asked; None while there is none.
    new_yard_field: OrePatch | None = None
    # With `new_yard_refinery_until_standing` of [expansion] on, the cell of the yard whose claim `new_yard_field` is,
    # by which its refinery is placed; None while there is no claim and with the switch off.
    new_yard_cell: Cell | None = None
    # The places the engine refused a building on (see `refused`), each with the tick it was noticed at, the oldest
    # first, and the places asked for lately with the MCV's yard box: placement keeps a building off both, and so does
    # the refinery order with `new_yard_refinery_until_standing` of [expansion] on. Published by upkeep's placement at
    # the start of every cycle and again once it has noticed the refusals of the cycle.
    refused_places: tuple[RefusedPlace, ...] = ()
    promised_places: tuple[Box, ...] = ()
    # With `second_yard` and `mcv_new_area` of [expansion] on: the fields an own harvester's last cell on ore lies in,
    # which an MCV's site lasts by none of (see `expansion`); none with either switch off. And the fields marked dry
    # (see `fresh_ore`), which no site lasts by, no refinery is placed against (see `refinery_fields`) and no harvester
    # is sent to. Both published by upkeep at the start of every cycle, before any of its rules is asked.
    harvested_fields: frozenset[OrePatch] = frozenset()
    dry_fields: frozenset[OrePatch] = frozenset()
    # The ways walked to the far fields, each from where its walker started to where it arrived (see `route_walk`).
    # Published by upkeep in every cycle with `walked_routes` and `creep_from_opening` of [placement] on; none with
    # either off.
    walked_routes: tuple[tuple[Cell, ...], ...] = ()
    # The match's graph of waypoints (see `waypoints`), brought up to date by `begin_cycle` with `waypoints` of
    # [routing] on; empty with it off.
    waypoints: Waypoints = field(default_factory=Waypoints)
    # What is known of the enemy, published by the scouting manager in every cycle.
    scout_report: ScoutReport = field(default_factory=ScoutReport)
    # What the enemy army is likely worth now, in credits (see `enemy_estimate`), brought up to date by the plan rules
    # once a cycle with `enemy_army_estimate` of [plan_rules] on; None with it off and before an enemy army unit was
    # seen.
    enemy_army_estimate: float | None = None
    # The spending focus (MILITARY_FOCUS, ECONOMY_FOCUS or BALANCED_FOCUS), decided by upkeep at the start of its cycle
    # with `spending_focus` of [upkeep] on (see `spending_focus`); balanced with it off and before the opening is done.
    spending_focus: str = BALANCED_FOCUS
    # The rush watch's verdict on the enemy's first minutes ("rush", "none" or "unknown"), published by the scouting
    # manager in every cycle; None with `[scouting] rush_detection` off.
    rush_verdict: str | None = None
    # Whether the opening holds back every infantry but its scout rifleman and its rocket cover (`single_scout_rifle`
    # of [opening]), published by the opening manager in every cycle; the scout slots read it. False with the switch
    # off and once a release came.
    scout_rifle_hold: bool = False
    # Whether the rush watch warns of enemy infantry massing at the enemy's start (see `rush_watch`), published by the
    # scouting manager in every cycle; upkeep, which runs first, reads that of the cycle before. False with
    # `[scouting] rush_detection` off.
    rush_warning: bool = False
    # Whether the rush counter has ordered all its defenses (see `rush_counter`), set by the opening, which ends the
    # warning; False until then and with the counter off.
    rush_counter_ordered: bool = False
    # Where the designated scouts are heading, published by the scouting manager in every cycle with
    # `[scouting] scout_slots` on; none with it off.
    scout_destinations: tuple[Cell, ...] = ()
    # The scouts released from their slots on their way home under hold fire, which join main once there (see
    # `scout_stance`), published by the scouting manager in every cycle with `[scouting] scout_cautious` on; none with
    # it off. Base defense, which runs first, reads those of the cycle before.
    returning_scouts: frozenset[int] = frozenset()
    # The raid opportunities seen: enemy harvesters at an ore field far from the enemy's start, with little guard and
    # under no remembered defense (see `exposed_trucks`), the newest first, each kept until truck_seen_seconds after it
    # was last seen. Each is an `ExposedTrucks`: `field`, the field's center cell; `harvester_count` and
    # `harvester_ids`, the harvesters in sight there; `guard_value`, the worth of the enemy units that fight by the
    # field; `seen_tick`, when they were last seen; `nearest_harvester`, the cell of the one nearest our base. Published
    # by the scouting manager in every cycle with `[scouting] exposed_trucks` on; none with it off.
    exposed_trucks: tuple[ExposedTrucks, ...] = ()
    # The strength main needs before it advances under a plan: called as `min_attack_strength(plan)`.
    min_attack_strength: Callable[[str], int] = field(init=False)
    # Unit id -> the last order the agent sent that gave the unit something to do, as the ledger recorded it (see
    # `arbitration.RepeatFilter`); the agent's own record, kept up to date as each batch is sent. The model commander's
    # questions say from it what our groups are doing (see `command_state`).
    unit_orders: Mapping[int, LedgerEntry] = field(default_factory=dict)
    # The units of main the combat manager has sent forward, or after an enemy from the forward hold, published in
    # every cycle; empty while main waits.
    army: frozenset[int] = frozenset()
    # Where main gathers, published by the combat manager in every cycle it orders main with `forward_hold` of [combat]
    # on: the forward point or the staging point. None with the switch off, and before the first such cycle.
    gather_point: Cell | None = None
    # The must-kill target main as a whole strikes, having no strike force of its own to send (see `strike`): main's
    # objective under every plan, and the commander's objective. Published by the combat manager in every cycle before
    # the commander's question; None while there is none.
    strike_target: EnemyRecord | None = None
    # The tanks on the crush run whose run move holds, each with the cell it runs to (see `crush`), published by the
    # combat manager in every cycle; empty while no run is in force. Base defense, which runs first, reads the run of
    # the cycle before and tells its tanks nothing; the agent takes the tanks out of every other order of the cycle;
    # the commander offers no run-over detachment while it holds a tank.
    crush_run: Mapping[int, Cell] = field(default_factory=dict)
    # The harvester on a harvester crush with the cell it runs to (see `harvester_crush`), published by base
    # defense in every cycle; the agent takes it out of every other order. Empty while none runs.
    harvester_crush: Mapping[int, Cell] = field(default_factory=dict)
    # The units of main a strike or a repair retreat holds (see `strike`, `repair`), published by the combat manager in
    # every cycle. Base defense, which runs first, reads those of the cycle before: a unit it calls back from a far
    # rescue it keeps main out of keeps its order while one of them holds it (see `far_rescue`).
    combat_held: frozenset[int] = frozenset()
    # Whether the model commander has a run-over detachment out (see `command_detachments`), set in every cycle while
    # the commander is on: it counts as the one crush run, so the combat manager starts none meanwhile.
    run_over_out: bool = False
    # main's vehicles in repair mode (see `repair`), its units withdrawn on their own (see `combat`) and the model
    # commander's detachments out (see `command_detachments`), published by the combat manager in every cycle before
    # the commander's question, which lists our forces by what they do (see `command_state`).
    repairing: frozenset[int] = frozenset()
    withdrawn: frozenset[int] = frozenset()
    detachments: tuple[DetachedUnits, ...] = ()
    # The target each own unit was last given by a focus fire, and since when (see `targeting`): one memory shared by
    # main's focus fire, its fire while it holds and base defense's, so that a unit passed between them keeps it.
    target_memory: TargetMemory = field(default_factory=TargetMemory)
    # Main's and the raid group's infantry spread one to a cell (see `spread`).
    infantry_spread: InfantrySpread = field(default_factory=InfantrySpread)
    # The units of an APC raid under way, which the raid manager brings home aboard its APCs itself when the raid
    # group is called home; published by it in every cycle, and left out of base defense's recall.
    raid_homebound: frozenset[int] = frozenset()
    # The enemies raids have destroyed, which no raid goes after again; published by the raid manager in every cycle,
    # for the plan rules to pass over when they look for a raid that could set out.
    raids_destroyed: frozenset[int] = frozenset()
    # With `[raids] launchable_only`, RAID is shut before this tick: no raid set out in time after RAID was chosen.
    # Published by the raid manager when that happens; 0 while it never has.
    raid_shut_until: int = 0
    # The enemy air seen so far in the match, taken in by `begin_cycle`; none ever with `[anti_air].enabled` false.
    air_watch: AirWatch = field(init=False)
    # The army mix the composition provider answers from, which keeps the counter shift in force.
    army_mix: ArmyMix = field(init=False)
    # What the unit queues make under a plan: called as `composition(plan, family, world)`.
    composition: Callable[[str, str, World], Composition] = field(init=False)
    # Whether main's attack is out, the tick it set out and its objective (see `attack_approach`), published by the
    # combat manager in every cycle while flank_approach or commit_on_cross of [combat], canary_post of [scouting] or
    # canary_alarm of [upkeep] is on; the managers before it read those of the cycle before. False and None otherwise.
    attack_out: bool = False
    attack_out_since: int | None = None
    attack_objective: Cell | None = None
    # The side cell of the attack's approach that main is still to pass; None for none.
    attack_side_cell: Cell | None = None
    # main's failed attacks remembered, and whether PRESS's objective is blocked by them or its defenses (see
    # `failed_attack`), published by the combat manager; the plans read those of the cycle before.
    failed_attacks: tuple[FailedAttack, ...] = ()
    press_blocked: bool = False
    # Whether main is spread against an expected nuke in this cycle (see `nuke`), published by the combat manager; the
    # managers before it read the cycle before's.
    nuke_spreading: bool = False
    # The tick the canary was found lost (see `canary`) and whether an attack was out then, published by the scouting
    # manager; None while no canary was lost.
    canary_lost_tick: int | None = None
    canary_lost_while_out: bool = False
    # Whether the canary alarm is in force (see `canary_alarm`), since which tick and why it started, and why the last
    # one ended; published by upkeep at the start of every cycle.
    canary_alarm: bool = False
    canary_alarm_since: int | None = None
    canary_alarm_reason: str | None = None
    canary_alarm_ended: str | None = None
    # Whether the approach alarm is in force (see `approach_alarm`), since which tick, where its group's centroid was
    # last seen and whether infantry leads it; published by base defense at the start of every cycle.
    approach_alarm: bool = False
    approach_alarm_since: int | None = None
    approach_alarm_cell: Cell | None = None
    approach_alarm_infantry: bool = False
    # The tick each refusal of the defense cap (see `defense_cap`) was last written, by its rule and cause.
    defense_capped_at: dict[tuple[str, str], int] = field(default_factory=dict)
    # Whether main's attack has crossed the enemy's army, the tick that was seen, and whether main is committed to the
    # base trade (see `cross_commit`), published by the combat manager in every cycle; base defense and upkeep read
    # those of the cycle before.
    armies_crossed: bool = False
    armies_crossed_tick: int | None = None
    main_committed: bool = False
    # The cycle's view of production; made by `begin_cycle`, so there is none before the first cycle.
    production: Production = field(init=False)
    # The match's rolling income, spending and queue utilization, brought up to date by `begin_cycle`.
    income: IncomeEstimate = field(init=False)

    def __post_init__(self) -> None:
        self.air_watch = AirWatch(self.tuning.anti_air.enabled)
        self.army_mix = ArmyMix(self.tuning.mix, self.tuning.anti_air, self.air_watch)
        # The raid quota counts what the raid can have of this match's groups, as they stand when it is asked.
        raid_units, tuning = partial(raids.quota_units, self), self.tuning
        self.composition = CompositionTable(
            tuning.composition, self.army_mix, raid_units, tuning.raids.enabled, tuning.raids.standing
        )
        self.min_attack_strength = AttackStrengths(self.tuning.plan_rules)
        self.income = IncomeEstimate(self.tuning)
        # Set before any state is seen, so that the guard is made up of the rifles already there when a
        # match is taken up midway.
        self.groups.set_guard_quota(RIFLE_INFANTRY, self.tuning.opening.guard_rifles)

    def begin_cycle(self, world: World) -> None:
        """Make what every manager of a cycle reads from the new state: the groups, production, the income and the
        enemy air seen; with `waypoints` of [routing] on, the graph of waypoints; and with the commander on, whom it
        commands and the answer that has arrived."""
        self.groups.update(world)
        self.production = Production(world, self.ledger)
        self.income.update(world)
        self.air_watch.observe(world)
        if self.tuning.routing.waypoints:
            self.waypoints.update(world, self.terrain, self.walked_routes, self.tuning)
        if self.commander is not None:
            self.commander.begin_cycle(world, self)
