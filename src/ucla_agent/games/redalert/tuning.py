"""The thresholds the Red Alert agent plays by.

Every number a rule of play compares against has a name here: how many rifle
infantry the home guard holds, how much power must be left over before a power
plant is ordered, how far from the construction yard a building may be placed.
The tracked file `config/redalert/tuning.toml` holds the values in force, so
that a run's recorded configuration says how the agent played.

A threshold named for cash is compared with the money the game can spend,
which is the cash and the ore the refineries hold together. Most thresholds
are whole numbers; a ratio or a factor, such as the strength ratio at which a
group engages, is a fraction; a threshold that differs for each one more
of a kind, such as the income each extra barracks needs, is a list of whole
numbers; a setting that picks one of a few ways of playing, such as the
order of the opening's build, is one of their names; a switch that turns a
way of playing on or off, such as aiming only anti-air weapons at aircraft,
is true or false; the characters of the map picture a building can stand on
are a string of them; and the army mix is a row for each family and tech
stage, which gives each item of the two unit queues its share of the army's
value in points of 100. The mix holds two sets of such rows, the mixed rows
and the tank rows, and a setting says which of them the main force is built
from.

The values built into the classes below are the same as the tracked file's. A
match played where the file cannot be read (another working directory, a file
damaged since the readiness check) is played with them, and the agent says so
in the match record; it does not give the match up over a setting.

Reading the file is the only I/O here and happens in `Tuning.from_file`.
"""

from __future__ import annotations

import math
from dataclasses import Field, dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping

from ucla_agent.core.config_tables import Table, read_tables, reject_unknown_keys
from ucla_agent.games.redalert.items import ALLIES, SOVIET

TUNING_FILE = Path("config") / "redalert" / "tuning.toml"
# The orders the opening's build can be played in: the barracks right after the first power plant, a refinery, or
# the barracks and then, by whether the first refinery can stand against its ore field, the refinery or one more
# power plant toward that field; or the war factory before the second refinery, with the service depot as the
# build's last step.
BARRACKS_FIRST = "barracks_first"
REFINERY_FIRST = "refinery_first"
REACH_FIRST = "reach_first"
FACTORY_FIRST = "factory_first"
OPENING_ORDERS = (BARRACKS_FIRST, REFINERY_FIRST, REACH_FIRST, FACTORY_FIRST)
# The Soviet raids the composition can field: grenadiers on foot (with flak trucks once a war factory stands), rocket
# soldiers carried by APCs, or none at all.
INFANTRY_RAID = "infantry"
APC_RAID = "apc_rockets"
NO_RAID = "none"
SOVIET_RAIDS = (INFANTRY_RAID, APC_RAID, NO_RAID)
# How main's artillery and V2 fight: from their firing band behind a screen, or going in with the rest of main.
SCREENED = "screened"
FRONTAL = "frontal"
SIEGE_MODES = (SCREENED, FRONTAL)
# The faster units the Soviet scout slots may take: the dog, from a kennel, or the flak truck, from the war factory.
SOVIET_SCOUT_UNITS = ("dog", "ftrk")
# The key of a field's metadata that holds the names a setting may take, and the one that holds the least and the most
# a whole-number setting may be when it is bounded above or below 0.
_CHOICES = "choices"
_RANGE = "range"
# What a setting whose built-in value is a string must hold in the file.
_CHARACTERS = "a string of at least one character"
# The tech stages of the army mix, the lowest first: the base, then each named for the building that opens it, the
# service depot and the radar.
MIX_STAGES = ("base", "fix", "dome")
# The stage above the radar's, named for the tech center that opens the Mammoth tank. Only the Soviet tank rows have a
# row for it; every other set of rows plays its radar row there.
TECH_STAGE = "stek"
# The families the army mix has a row for at every stage.
MIX_FAMILIES = (ALLIES, SOVIET)
# The stages of the tank rows by family, where they differ from MIX_STAGES: the Soviets have the tech center's row too.
_TANK_ROW_STAGES = {SOVIET: (*MIX_STAGES, TECH_STAGE)}
# The two tables of a row of the army mix: the items of the Infantry queue and of the Vehicle queue, with their shares.
INFANTRY_SHARES = "infantry"
VEHICLE_SHARES = "vehicle"
# What the shares of a row of the army mix add up to: they are points of the army's value.
MIX_ROW_POINTS = 100
# What the main force is built from: the mixed rows, infantry-heavy until the service depot, or the tank rows, built
# around the tanks. "tanks" is also the subtable of [mix] that holds the tank rows.
MIXED = "mixed"
TANKS = "tanks"
MAIN_FORCES = (MIXED, TANKS)
# The fields of the army mix's thresholds that hold its rows, which are read from the subtables of [mix]: the mixed
# rows from [mix.<family>.<stage>], the tank rows from [mix.tanks.<family>.<stage>].
_MIX_ROWS = "rows"
_MIX_TANK_ROWS = "tank_rows"
_ROW_FIELDS = (_MIX_ROWS, _MIX_TANK_ROWS)


@dataclass(frozen=True)
class LoopTuning:
    """The control loop: how often it reports, and when an order or an answer is too old to count."""

    timing_window_ticks: int = 250
    reissue_after_ticks: int = 75
    reasoning_summary_chars: int = 200
    choice_max_age_ticks: int = 250


@dataclass(frozen=True)
class OpeningTuning:
    """The opening: its build order, the home guard, the anti-armor cover, the cash the infantry queue may use,
    whether its defenses wait for a threat, and whether it trains a lean guard."""

    order: str = field(default=BARRACKS_FIRST, metadata={_CHOICES: OPENING_ORDERS})
    guard_rifles: int = 5
    guard_radius_cells: int = 8
    guard_post_cells: int = 6
    guard_reorder_ticks: int = 75
    anti_armor_at_seconds: int = 150
    guard_rockets: int = 4
    infantry_min_cash: int = 100
    main_rifles: int = 6
    main_rifle_min_cash: int = 400
    deploy_retry_ticks: int = 75
    # With the switch on, the opening buys its defenses only against a rush the watch tells or a threat it sees, an
    # enemy vehicle that fights within armor_near_base_cells of the base among them; a verdict still unknown at its
    # deadline calls for the defense against infantry only with defense_on_unknown on as well. The opening gun waits
    # for an enemy vehicle seen within armor_near_base_cells.
    defense_on_threat: bool = True
    defense_on_unknown: bool = False
    armor_near_base_cells: int = 18
    # The rush counter (see `rush_counter`): on the rush verdict, or a threat at the base before a war factory stands,
    # rush_counter_defenses defenses against infantry back to back (0: off); rush_reserve_cash held from the optional
    # buildings while the rush watch warns; infantry against an infantry threat while main has fewer than
    # rush_min_tanks tanks; main kept home until rush_hold_until_seconds; and with rush_harvester_crush on, a harvester
    # run over an infantry cluster at the base when no tank or APC is in reach.
    rush_counter_defenses: int = 2
    rush_reserve_cash: int = 1200
    rush_min_tanks: int = 3
    rush_hold_until_seconds: int = 330
    rush_harvester_crush: bool = True
    # With the switch on and no rush told, the opening trains lean_guard_rifles rifles for the guard, none for main and
    # no rocket infantry for the cover against armor: one guard rifle at home until the rush watch releases the full
    # guard.
    lean_infantry: bool = True
    lean_guard_rifles: int = 1
    # With the switch on, once a barracks stands the opening trains one rifleman, the first scout, and no other
    # infantry but its rocket cover until a rush warning or verdict, a threat at the base or the service depot.
    single_scout_rifle: bool = False
    # With the switch on, when the base's center and the enemy's start lie more than large_map_base_cells apart, every
    # order but factory_first builds a power plant, two refineries, the barracks, a power plant and the war factory; a
    # rush seen before the barracks stands or is on its way brings the barracks-first build back for the match.
    large_map_economy_first: bool = True
    large_map_base_cells: int = 70


@dataclass(frozen=True)
class UpkeepTuning:
    """Base upkeep: power, harvesters and where they park late in a match, the production reserve, the refineries and
    the fields they claim, tech, the Soviet Tesla coils, the defense a preserved lead adds, whether defenses wait for a
    threat, repair and the low-power emergency."""

    headroom_early: int = 20
    headroom_mid: int = 60
    headroom_large: int = 100
    headroom_tesla: int = 160
    headroom_mid_from_seconds: int = 360
    headroom_large_producers: int = 5
    headroom_tesla_count: int = 2
    # With the switch on, the power target is the larger of headroom_early and headroom_share of the planned drain, in
    # place of headroom_mid and headroom_large; headroom_tesla still applies.
    lean_power: bool = True
    headroom_share: float = 0.15
    advanced_power_min_cash: int = 500
    # The harvester target, the one cap on harvesters: min(harvesters_max, harvesters_per_refinery x refineries).
    harvesters_per_refinery: int = 2
    harvesters_max: int = 22
    # A served field's room: its share, max(1, min(field_share_per_refinery x the refineries serving it, floor(ore
    # cells / harvester_cells_per))), less its harvesters; 0 switches it off.
    harvester_cells_per: int = 10
    # With the room on, at most once every harvester_rebalance_seconds one harvester of a field working more than
    # harvester_rebalance_excess over its share is moved to the served field, not dry, with the most room.
    harvester_rebalance_seconds: int = 30
    harvester_rebalance_excess: int = 1
    # With the switch on, the Vehicle queue takes turns while a harvester is due: the harvester first below
    # harvesters_first_per_refinery x refineries; combat vehicles first while main's combat value is under
    # combat_value_floor or an enemy army is near the base; else a harvester and a combat vehicle alternate. 99 a
    # refinery is more than the harvester target ever reaches, so a due harvester always goes first.
    harvesters_first: bool = True
    harvesters_first_per_refinery: int = 99
    combat_value_floor: int = 6000
    # The extra refinery's trigger counts a refinery worked once this many harvesters a refinery stand.
    refinery_worked_harvesters: int = 2
    harvester_idle_states: int = 3
    harvest_reorder_ticks: int = 250
    production_cash_reserve: int = 300
    infantry_at_a_time: int = 3
    # With the switch on and a war factory standing, the unit queues train no rifles: rocket soldiers while fewer than
    # rockets_kept stand in main, the guard and the escort, then the rocket soldiers (and the Soviet counter's
    # flamethrowers) the army is short of the share of its value the mix gives them; no barracks is added then either.
    no_army_infantry: bool = True
    rockets_kept: int = 4
    # With the switch on, after the opening the unit queues wait, economy_wait_seconds at the most, while a refinery
    # claim, one more war factory or the second yard's MCV is due and the money does not cover it yet, and are then
    # free for economy_release_seconds at the least.
    economy_first: bool = True
    economy_wait_seconds: int = 20
    economy_release_seconds: int = 20
    # With the switch on (and second_yard of [expansion]), once a service depot stands the MCV is bought first: while
    # its money is short, upkeep holds every order but power against low power, harvesters up to one a refinery and
    # the base's emergency, mcv_first_wait_seconds at the most, then economy_release_seconds free.
    mcv_first: bool = True
    mcv_first_wait_seconds: int = 30
    tech_min_cash: int = 1500
    tech_min_harvesters: int = 4
    repair_below_hp: int = 75
    repair_min_cash: int = 300
    repair_stop_above_hp: int = 95
    repair_stop_below_cash: int = 100
    repair_resend_ticks: int = 250
    # With the switch on, a defense's repair is switched on below repair_stop_above_hp in place of repair_below_hp.
    repair_defenses_early: bool = True
    switch_again_after_ticks: int = 75
    staging_distance_cells: int = 10
    low_power_wait_seconds: int = 10
    power_down_enemy_radius_cells: int = 10
    power_restore_headroom: int = 40
    defense_replace_seconds: int = 60
    extra_refinery_min_cash: int = 1500
    refinery_full_share: float = 0.75
    refinery_saving_from_seconds: int = 360
    refineries_max: int = 6
    refineries_max_late: int = 6
    refineries_late_after_seconds: int = 480
    refinery_field_cap: bool = True
    # With the switch on, each far field the base reaches is claimed by a refinery ordered as soon as the money covers
    # it, past the other refinery gates, while fewer than claim_refineries_max refineries stand or are on their way.
    refinery_claims_reached: bool = True
    claim_refineries_max: int = 8
    # With the switch on, once 4 refineries stand a refinery for an untaken field one can stand against is ordered as
    # soon as the money covers it, ahead of more producers, up to growth_refineries_max; the unit queues leave its price
    # alone for growth_saving_seconds at most.
    refinery_growth: bool = True
    growth_refineries_max: int = 8
    growth_saving_seconds: int = 60
    dome_from_seconds: int = 360
    soviet_tesla_coils: int = 2
    preserve_anti_armor: bool = True
    # With the switch on, upkeep's defenses answer only a real threat: no routine replacement, a Tesla coil for each
    # vehicle attack on a side more than tesla_side_cells from every coil.
    defenses_on_threat_only: bool = True
    tesla_side_cells: int = 12
    park_harvesters: bool = False
    park_harvesters_seconds_left: int = 120
    park_distance_cells: int = 8
    harvesters_to_fresh_ore: bool = True
    dry_idle_count: int = 2
    dry_window_seconds: int = 60
    dry_near_cells: int = 8
    fresh_ore_enemy_clear_cells: int = 12
    fresh_ore_defense_clear_cells: int = 10
    # A field is marked dry only when its idle re-sends come from this many different harvesters, or from all its
    # harvesters when it has fewer.
    dry_distinct_harvesters: int = 3
    # With the switch on, a field with an ore mine is never marked dry, a truck back from a retreat within 10 s is no
    # sign of a dry field, and no truck is sent to a field no refinery of ours serves.
    dry_field_guard: bool = True
    # With the switch on (and harvesters_to_fresh_ore), each harvester moved off a dry field goes to the nearest safe
    # field whose load is below field_share_per_refinery for each refinery serving it, at least one.
    fresh_ore_by_load: bool = True
    field_share_per_refinery: int = 2
    # With the switch on, a field with an own refinery counted against it takes trucks_per_mine trucks for each ore mine
    # it holds, trucks_mineless_field without one (none while dry); every truck keeps the field it is assigned to and is
    # sent back to its ore, a new one goes to the nearest short field, and one truck of a field over its share moves to
    # a short field at most once every truck_rebalance_seconds and at once when a new refinery stands.
    truck_shares_by_mines: bool = True
    trucks_per_mine: int = 2
    trucks_mineless_field: int = 2
    truck_rebalance_seconds: int = 10
    # With the switch on, a truck is trained only while the trucks standing, in production and due free from a refinery
    # that stood less than free_truck_wait_seconds ago are fewer than the active fields' shares together (harvesters_max
    # at the most) and a field is short of its share.
    truck_cap_by_shares: bool = True
    free_truck_wait_seconds: int = 20
    # With the switch on, once the opening's build is complete the spending focus is decided every focus_every_ticks
    # from the enemy army estimate E against our army A (see `spending_focus`): military from E >= military_focus_ratio
    # x A until E <= military_focus_end_ratio x A, economy from A >= economy_focus_ratio x E or a greedy enemy, a focus
    # other than military kept focus_hold_seconds at least. Military trains the artillery first while main and the
    # guard hold fewer than military_focus_first_artillery, and adds a defense toward the enemy every
    # military_focus_defense_seconds, military_focus_defenses_max a period.
    spending_focus: bool = True
    focus_every_ticks: int = 250
    military_focus_ratio: float = 1.2
    military_focus_end_ratio: float = 1.0
    economy_focus_ratio: float = 1.5
    focus_hold_seconds: int = 30
    military_focus_first_artillery: int = 2
    military_focus_defense_seconds: int = 60
    military_focus_defenses_max: int = 2
    # With the switch on, the military focus starts at once, whatever the balance says, while all our combat units but
    # aircraft are worth less than max(recovery_enemy_share x E, recovery_min_army), have lost recovery_drop_share of
    # their highest worth in recovery_drop_seconds, or a combat unit or defense was lost under a threat within
    # recovery_attack_seconds; it holds until none of those has held for recovery_clear_seconds.
    military_recovery: bool = True
    recovery_min_army: int = 800
    recovery_enemy_share: float = 0.8
    recovery_drop_share: float = 0.4
    recovery_drop_seconds: int = 120
    recovery_attack_seconds: int = 60
    recovery_clear_seconds: int = 30
    # The losses at the base under a threat that start the recovery, and E held to recovery_estimate_seen_share times
    # the enemy combat units seen within recovery_seen_seconds.
    recovery_loss_value: int = 1000
    recovery_estimate_seen_share: float = 1.5
    recovery_seen_seconds: int = 180
    # With the switch on, the spending focus weighs E as the recovery holds it, and the military focus holds a new MCV
    # back only while our army is below that E.
    focus_capped_estimate: bool = True
    # With the switch on, the canary's death while an attack is out, or the armies crossing, starts the canary alarm
    # (see `canary_alarm`): a defense on the Defense queue whenever it is free, placed toward the cell
    # canary_defense_goal_cells from the base toward the enemy's start, while the unit queues and the optional buildings
    # wait; it ends once the threat is beaten, gone canary_alarm_quiet_seconds after the start with none closing, main
    # is home, or canary_alarm_max_seconds after the start.
    canary_alarm: bool = True
    canary_defense_goal_cells: int = 8
    canary_alarm_quiet_seconds: int = 75
    canary_alarm_max_seconds: int = 180


@dataclass(frozen=True)
class IncomeTuning:
    """The income estimate: the window it is taken over, how often it is renewed, and the investment test."""

    income_window_seconds: int = 60
    income_min_window_seconds: int = 20
    income_update_ticks: int = 250
    invest_income_share: float = 0.5
    finished_progress: float = 0.9


@dataclass(frozen=True)
class ProducersTuning:
    """Extra barracks and war factories: when the queues are busy enough, the income each one more needs, and the
    switch that brings war factories sooner and keeps the Vehicle queue filled."""

    barracks_from_seconds: int = 210
    busy_share: float = 0.85
    # The income a second the 2nd to the 7th infantry producer needs, in that order.
    barracks_income: tuple[int, ...] = (100, 100, 150, 150, 170, 170)
    barracks_float: int = 1500
    late_barracks_float: int = 2500
    # The income a second the 2nd to the 4th war factory needs, in that order.
    factories_income: tuple[int, ...] = (125, 150, 185)
    float_seconds: int = 30
    max_infantry_producers: int = 7
    max_war_factories: int = 4
    # Money the producers bank and do not spend: the second war factory once the money above the production reserve
    # has stayed at least bank_factory_money for bank_factory_seconds, whatever the income; and a war factory, a
    # refinery or a power plant once the Building queue has held nothing for idle_queue_seconds with at least
    # idle_bank_money above the reserve.
    bank_factory_money: int = 2500
    bank_factory_seconds: int = 30
    idle_bank_money: int = 3000
    # With the switch on, money of no_float_cash or more held for no_float_seconds buys one more barracks or war
    # factory at once, whatever the plan, focus or recovery (see `bank_spending`).
    no_float: bool = True
    no_float_cash: int = 1500
    no_float_seconds: int = 20
    idle_queue_seconds: int = 30
    # Ore near the storage cap: once the ore held has stayed at least storage_spend_share of what the refineries and
    # silos can hold for storage_spend_seconds, one more war factory every storage_spend_seconds up to
    # storage_war_factories_max, and the unit queues never wait for the economy's next item.
    storage_spend_share: float = 0.6
    storage_spend_seconds: int = 20
    storage_war_factories_max: int = 6
    # With the switch on, one more war factory comes on gates of its own, ahead of one more barracks, and the Vehicle
    # queue is kept filled.
    tank_factories: bool = True
    # The income a second the 2nd to the 4th war factory needs with the switch on, in that order.
    tank_factories_income: tuple[int, ...] = (90, 120, 150)
    tank_factories_busy_share: float = 0.6
    tank_factories_from_seconds: int = 300
    # The vehicles the Vehicle queue is kept filled with: at least one, and no more than one train order can ask for.
    tank_factories_queued: int = field(default=2, metadata={_RANGE: (1, 10)})
    # With the switch on, a Soviet base with 2 or more war factories orders one tech center (stek) once the spendable
    # money has stayed at least soviet_tech_min_money for soviet_tech_seconds; it opens the Mammoth tank.
    soviet_tech_center: bool = True
    soviet_tech_min_money: int = 4000
    soviet_tech_seconds: int = 30
    # With the switch on, every rule that orders a war factory beyond the first does so only while the vehicle income
    # (credits a second) is at least war_factory_load_share of what the Vehicle queue spends building without pause at
    # the war factories standing or on their way (see war_factory_load).
    war_factory_load_gate: bool = True
    war_factory_load_share: float = 0.8
    # With the switch on (and war_factory_load_gate), the load gate is the producers' trigger for a war factory beyond
    # the first from war_factory_on_load_from_seconds on, in place of the income steps (see war_factory_load).
    war_factory_on_load: bool = False
    war_factory_on_load_from_seconds: int = 180


@dataclass(frozen=True)
class PlacementTuning:
    """Where buildings go: the rings around the construction yard and what is kept free or preferred."""

    ring_min_cells: int = 2
    ring_max_cells: int = 12
    gap_cells: int = 1
    adjacent_cells: int = 2
    defense_adjacent_cells: int = 7
    war_factory_exit_cells: int = 2
    defense_radius_cells: int = 6
    defense_distance_cells: int = 5
    # With the switch on, a ground defense for the main base goes in the line across the lane toward its goal (or the
    # lane threats came by with the fewest defenses), defense_line_front_cells beyond the base's outermost building
    # that way, slots defense_line_spacing_cells apart across the lane (see `defense_line`); defense_front then places
    # none.
    defense_line_across_lane: bool = True
    # With the switch on, an unserved field smaller than mcv_site_min_ore_cells of [expansion] takes a refinery while
    # it holds an ore mine or refinery_small_field_cells ore cells, and floating cash buys no producer before it.
    refinery_small_fields: bool = True
    refinery_small_field_cells: int = 20
    defense_line_front_cells: int = 3
    defense_line_spacing_cells: int = 3
    # With the switch on and the enemy's start known, a ground defense with no goal of its own goes to the place nearest
    # the point defense_front_cells beyond the base's building farthest toward the enemy, halfway to the middle of the
    # base's ground defenses on the enemy's side when there are any, among those within defense_cluster_cells of it
    # first; never behind the yard while the enemy's side has a place.
    defense_front: bool = True
    defense_front_cells: int = 3
    defense_cluster_cells: int = 6
    ore_reach_cells: int = 20
    place_again_after_ticks: int = 50
    promise_ticks: int = 75
    # A finished building left unplaced this long is cancelled, or placed anywhere it can stand without a cancel order,
    # so that its queue takes the next building.
    held_cancel_ticks: int = 500
    refinery_field_by_reach: bool = False
    refinery_ore_cells: int = 2
    # At most this many own refineries serve an ore field: one, so that each field's ore is shared by no two.
    max_refineries_per_field: int = 1
    shared_field_min_cells: int = 24
    creep_toward_ore: bool = False
    creep_next_patch: bool = False
    creep_lane_cells: int = 12
    creep_max_share: float = 0.6
    creep_enemy_clear_cells: int = 25
    creep_defense_clear_cells: int = 10
    adjacent_patch_cells: int = 6
    creep_from_opening: bool = True
    creep_line_cells: int = 2
    # The creep cap: the far field's nearest ore cell lies within this many cells of the nearest own building but a
    # defense, with creep_continuous on or off.
    creep_far_max_cells: int = 30
    # With the switch on (and creep_from_opening), the base walks on field after field within the creep cap of wherever
    # it has grown to, and a field placement skipped is never the far field.
    creep_continuous: bool = True
    # With the switch on (and creep_from_opening), a second yard far from the first grows toward a far field of its own,
    # and the buildings that walk a line take the two lines in turn.
    remote_yard_growth: bool = True
    # With the switch on, a building that shows up more than refused_miss_cells from the cell it was asked onto makes
    # that place unusable, at most refused_spots_max places kept; refused_misses_to_skip such misses in a row toward the
    # far field skip that field.
    refused_detection: bool = True
    refused_miss_cells: int = 3
    refused_misses_to_skip: int = 2
    refused_spots_max: int = 64
    # With the switch on, a field is taken once a refinery stands or is asked against it, and after the opening a
    # refinery goes to an untaken field it can stand against before any field gets a second; a taken field is never
    # claimed.
    refinery_spread: bool = True
    # With the switch on (and creep_from_opening), a far field the base gets no stall_progress_cells closer to within
    # stall_placements creep placements or stall_seconds is skipped.
    creep_stall_skip: bool = True
    stall_placements: int = 3
    stall_progress_cells: int = 2
    stall_seconds: int = 120
    # With the switch on (and remote_yard_growth), the second yard's line takes only places of that yard's ring or
    # next to own buildings within second_line_radius_cells of its middle.
    second_line_local: bool = True
    second_line_radius_cells: int = 15
    # With the switch on (and creep_from_opening), an idle rifleman walks to each far field first: the base grows along
    # the route it walked, and a field it cannot reach within walk_timeout_seconds, or only by a way longer than
    # walk_detour_factor times the straight distance, is skipped. A point of the route is kept every walk_sample_cells.
    walked_routes: bool = False
    walk_timeout_seconds: int = 60
    walk_detour_factor: float = 2.0
    walk_sample_cells: int = 2
    # With walked_routes on: a field toward which walker_deaths_to_skip walkers were killed is skipped; no walker is sent
    # while an enemy combat unit seen within walker_enemy_seen_seconds stood near the way, nor to a field within
    # walker_enemy_refinery_cells of a remembered enemy refinery, which is skipped.
    walker_deaths_to_skip: int = 2
    walker_enemy_seen_seconds: int = 60
    walker_enemy_refinery_cells: int = 12
    # With the switch on, every refinery after the opening's goes to a field no own refinery serves (each serves the
    # field whose ore lies nearest its footprint) and that is not dry, with at least mcv_site_min_ore_cells ore cells; a
    # second only on a field of at least refinery_second_min_cells, refinery_second_spacing_cells from its other
    # refinery, so 9999 gives no field of a known map a second; none within a known enemy ground defense's range plus
    # refinery_enemy_defense_margin_cells.
    refinery_unserved_only: bool = True
    refinery_second_min_cells: int = 9999
    refinery_second_spacing_cells: int = 6
    refinery_enemy_defense_margin_cells: int = 2
    # A refinery of ours serves the field whose ore lies nearest its footprint within this many cells, counted as a king
    # moves, and none farther: refinery placement and the harvester room count a field's refineries so.
    refinery_serves_cells: int = 5
    # For one refinery a field, a refinery of ours that serves no field counts against the field whose ore lies nearest
    # its footprint within this many cells: its harvesters work that field all the same.
    refinery_counts_cells: int = 8
    # With refinery_unserved_only on, when no unserved field can be stood against, a field of at least this many ore
    # cells served once takes a second refinery, refinery_second_spacing_cells from every other: the one exception to
    # one refinery a field.
    refinery_second_when_stuck_cells: int = 80


@dataclass(frozen=True)
class ExpansionTuning:
    """The second construction yard: whether the base gets one early, when its depot and MCV come, how the MCV is
    escorted to its site, waits and deploys, and whether its site is by a field placement skipped."""

    second_yard: bool = True
    second_yard_from_seconds: int = 240
    second_yard_min_cash: int = 2000
    mcv_escort_strength: int = 1500
    mcv_deploy_cells: int = 4
    mcv_wait_ticks: int = 1500
    mcv_to_skipped_fields: bool = False
    # With the switch on, the MCV deploys by the nearest field no refinery serves within mcv_site_max_cells of the main
    # yard that a route clear of enemy defenses, and of enemy units seen in the last mcv_enemy_seen_ticks by their range
    # and mcv_danger_margin_cells, reaches; at home without one. One hurt or near danger on its way is sited again.
    mcv_safe_site: bool = True
    mcv_site_max_cells: int = 30
    mcv_enemy_seen_ticks: int = 1500
    mcv_danger_margin_cells: int = 3
    # With the switch on (and mcv_safe_site), the site's field is never taken, a claim field or the creep target's,
    # those mcv_refinery_spacing_cells or more from every own refinery first; a site taken meanwhile is chosen again.
    mcv_new_area: bool = True
    mcv_refinery_spacing_cells: int = 12
    # With mcv_new_area on, a field the site lasts by, tried before any other, has at least mcv_site_min_ore_cells ore
    # cells, no own refinery within mcv_refinery_spacing_cells of its center and no own harvester harvesting it.
    mcv_site_min_ore_cells: int = 30
    # With the switch on, the field of the MCV's site is reserved from the cycle the site is chosen (when the MCV is
    # ordered) until it deploys, is lost or is sent home: no refinery is asked against it and it is no claim field.
    mcv_reserve_field: bool = True
    # With the switch on, from the cycle an MCV's site is chosen until it deploys or is lost (60 s with no site, 120 s at
    # the most), no refinery is ordered for a field one of ours serves already; one for an unserved field still is.
    hold_refinery_for_mcv: bool = True
    # With the switch on, an MCV kept at home only for want of its escort for short_escort_wait_ticks in a row sets out
    # with the escort it has, or with none on a safe route.
    mcv_short_escort: bool = True
    # With the switch on, the second MCV left with no safe site on its way (home_fallback) waits by home and looks for a
    # site again from where it stands, deploying at home only after second_mcv_wait_seconds without one.
    second_mcv_waits: bool = True
    second_mcv_wait_seconds: int = 90
    # With the switch on, an expansion yard's refinery claim lasts until a refinery of ours stands counted against its
    # field (or the yard is lost): ordered only for a place placement would use, placed from the new yard with units
    # moved off its spot, a refused spot skipped and tried again new_yard_refused_retry_seconds after its refusal.
    new_yard_refinery_until_standing: bool = True
    # With the switch on, the refinery a new yard claims comes first: the military focus holds it back no more, the
    # unit queues' hold for its money does not time out, floating cash buys no producer before it, and each cycle it
    # waits is written (new_yard_waiting).
    new_yard_refinery_first: bool = True
    new_yard_refused_retry_seconds: int = 60
    # With the switch on, an expansion MCV deploys only where a refinery serving its site's field fits within
    # mcv_refinery_fit_cells of the yard box, looked for up to mcv_fit_search_cells walking steps from the ore, checked
    # again before the deploy; a field with no such place is passed over.
    mcv_refinery_fit: bool = True
    mcv_refinery_fit_cells: int = 4
    mcv_fit_search_cells: int = 8
    short_escort_wait_ticks: int = 500
    # With the switch on, the yards allowed are 1 + income / yards_income_per_yard, max_yards at the most; an MCV, the
    # second yard's as a further one, opens a far field while the yards and MCVs are fewer, from
    # second_yard_from_seconds on and while more than repeat_mcv_cutoff_seconds of the match are left.
    mcv_repeat: bool = True
    yards_income_per_yard: int = 40
    max_yards: int = 6
    repeat_mcv_cutoff_seconds: int = 180
    # With mcv_repeat on, every MCV's site is by the qualifying field nearest by a safe route whose center lies within
    # mcv_far_site_max_cells of the nearest own yard, in place of mcv_site_max_cells.
    mcv_far_site_max_cells: int = 50
    # From this game second on, with one own yard and no MCV, the second MCV is ordered ahead of more war factories
    # and every building but power once a far field qualifies and the money covers it, whatever the income.
    second_mcv_due_seconds: int = 300
    # A site within this many cells of a known enemy construction yard, refinery or ground defense does not qualify,
    # and an MCV whose site becomes one is sited again; the move to fresh ore sends no harvester to a field no own
    # refinery serves whose center lies this near a known enemy building.
    mcv_enemy_building_cells: int = 14
    # With mcv_safe_site or mcv_repeat on, a site qualifies only when its distance from the base's center is at most
    # this share of its distance from the enemy's start; with that start unknown, every site does. Over 1, so that an
    # untouched field in the middle of the map qualifies.
    mcv_own_side_share: float = 1.1
    # While the MCV travels, enemy combat units seen within 10 s standing within 15 cells of its site and worth together
    # at least mcv_site_army_value make it hold short, 15 cells from them; after mcv_site_hold_seconds with them still
    # there it is sited again, the field passed over, or goes home without a site.
    mcv_site_army_value: int = 600
    mcv_site_hold_seconds: int = 30
    # With the switch on, the MCV's site is ranked by route length (far_site) or distance from the main yard (safe_site)
    # plus a penalty of up to mcv_line_penalty_cells for a field ahead of the base near the line to the enemy's start,
    # falling to none at mcv_line_clear_cells off the line; it only ranks, never excludes a field.
    mcv_line_penalty: bool = True
    mcv_line_penalty_cells: int = 20
    mcv_line_clear_cells: int = 15
    # With the switch on, the MCV waits, holds short or is sited again only for ground enemy units seen in 10 s within
    # mcv_route_threat_cells of its remaining route or 15 cells of its site worth more than mcv_escort_win_ratio times
    # its escort (mcv_site_army_value then unused), and a site chosen again lies within mcv_resite_max_cells of route.
    mcv_escort_reliance: bool = True
    mcv_route_threat_cells: int = 10
    mcv_escort_win_ratio: float = 1.0
    mcv_resite_max_cells: int = 25


@dataclass(frozen=True)
class CompositionTuning:
    """The raid quotas that the raiding plans fill before anything else, and the Allied support units (see `support`)."""

    # With the switch on and main built around tanks, the Vehicle queue trains no vehicle of the army mix until a
    # service depot stands; the raid quota is still filled.
    main_vehicles_need_depot: bool = True
    # The Allied raid's size: never more than a dozen vehicles, and never none.
    raid_vehicles: int = field(default=4, metadata={_RANGE: (1, 12)})
    allied_raid_light_tanks: bool = False
    raid_infantry: int = 6
    raid_mixed_vehicles: int = 0
    raid_mixed_infantry: int = 3
    soviet_raid: str = field(default=INFANTRY_RAID, metadata={_CHOICES: SOVIET_RAIDS})
    # The APC raid: so many APCs, each carrying so many rocket soldiers; an APC holds five.
    raid_apcs: int = field(default=2, metadata={_RANGE: (1, 4)})
    raid_apc_rockets: int = field(default=5, metadata={_RANGE: (1, 5)})
    # With vehicle_repair of [combat] on, the Allies train a mechanic for every mechanics_per_tanks of main's tanks, up
    # to mechanics_max; of them mechanics_base stand at the base repair point and the rest trail main. And a medic for
    # every medics_per_infantry of main's infantry, up to medics_max.
    mechanics_per_tanks: int = field(default=3, metadata={_RANGE: (1, math.inf)})
    mechanics_max: int = 3
    mechanics_base: int = 0
    medics_per_infantry: int = field(default=6, metadata={_RANGE: (1, math.inf)})
    # No medic: vehicle repair is what matters, not going further into infantry.
    medics_max: int = 0
    # With the switch on, a Soviet main force of the tank rows, from the dome stage on, answers an enemy whose units
    # that fight seen in the last two minutes are at least enemy_infantry_share infantry: flamers_share points of the
    # row go to the flamethrowers (to the V2s while none is on offer) and v2_infantry_bonus more to the V2s, both from
    # the heavy tank (see `composition`).
    soviet_infantry_counter: bool = True
    enemy_infantry_share: float = 0.6
    flamers_share: int = field(default=20, metadata={_RANGE: (0, 95)})
    v2_infantry_bonus: int = field(default=15, metadata={_RANGE: (0, 95)})
    # With the switch on, the enemy units that fight seen in the last counter_seen_seconds turn into counter-unit
    # quotas the unit queues fill first (see `composition`): an anti-infantry unit for each anti_infantry_per_rockets
    # rocket soldiers and each anti_infantry_per_infantry other infantry while the enemy is infantry-heavy (at least
    # anti_infantry_min_infantry of them and anti_infantry_enemy_share of the units counted), and a rocket soldier for
    # each anti_armor_per_tanks tanks; none while the counter units are worth counter_max_share of our army.
    counter_quota: bool = True
    counter_seen_seconds: int = 120
    anti_infantry_per_rockets: int = field(default=2, metadata={_RANGE: (1, math.inf)})
    anti_infantry_per_infantry: int = field(default=4, metadata={_RANGE: (1, math.inf)})
    anti_infantry_min_infantry: int = 6
    anti_infantry_enemy_share: float = 0.4
    anti_armor_per_tanks: int = field(default=2, metadata={_RANGE: (1, math.inf)})
    counter_max_share: float = 0.35
    # With the switch on, while the enemy is infantry-heavy as the counter quota has it, the war factory's block on
    # counter infantry is lifted: base defense trains the family's counter infantry against an infantry threat, and
    # no_army_infantry of [upkeep] lets the quota's infantry through up to its shortfall.
    infantry_counter_lift: bool = True
    # With the switch on, while the approach alarm is in force (see `approach_alarm`), approach_min_infantry of
    # [base_defense] infantry counts as infantry-heavy, and the quota's counters are trained in the opening and
    # through upkeep's economy holds.
    counter_on_approach: bool = True
    # With the switch on, the counter_max_share cap leaves riflemen, the base army's own infantry, out of the counters.
    counter_cap_skips_rifles: bool = True
    # With the switch on, the Allied anti-infantry counter is jeeps for the larger half once a war factory stands.
    counter_prefer_jeeps: bool = True
    # With the switch on, the quota wants 2 fast hunters (Allies light tank, else jeep; Soviets heavy tank) for each
    # enemy artillery piece or V2 seen within counter_seen_seconds.
    anti_artillery_quota: bool = True


@dataclass(frozen=True)
class MixRow:
    """One row of the army mix: each item of the two unit queues and its share of the army's value, in points."""

    infantry: Mapping[str, int]
    vehicle: Mapping[str, int]


def _built_in_rows() -> dict[str, dict[str, MixRow]]:
    """The rows of the army mix, by family and tech stage: the strongest mixes of the matchup reference."""
    base, depot, radar = MIX_STAGES
    return {
        ALLIES: {
            base: MixRow(infantry={"e1": 50, "e3": 15}, vehicle={"1tnk": 35}),
            depot: MixRow(infantry={"e1": 40, "e3": 15}, vehicle={"2tnk": 45}),
            radar: MixRow(infantry={"e1": 30, "e3": 10}, vehicle={"2tnk": 30, "arty": 30}),
        },
        SOVIET: {
            base: MixRow(infantry={"e1": 50, "e2": 10, "e3": 25}, vehicle={"ftrk": 15}),
            depot: MixRow(infantry={"e1": 35, "e2": 10, "e3": 15}, vehicle={"3tnk": 40}),
            radar: MixRow(infantry={"e1": 35, "e2": 5, "e3": 10}, vehicle={"3tnk": 25, "v2rl": 25}),
        },
    }


def _built_in_tank_rows() -> dict[str, dict[str, MixRow]]:
    """The tank rows of the army mix, by family and tech stage: the medium tank (Allies) or the heavy tank (Soviets) is
    the bread and butter, with artillery or V2s to back it up and rocket soldiers against aircraft and for fire
    support. The base rows, of light tanks and flak trucks, stay loadable, but with `main_vehicles_need_depot` on no
    main vehicle is built before the service depot; light tanks then come only through the raid quota. From the depot
    on a row's points are the value of 12 tanks, 4 artillery or V2s and 8 rocket soldiers at their costs, so that the
    units come out 12 : 4 : 8 by count (tanks to artillery 3 : 1, tanks to rocket soldiers 3 : 2); a Soviet tech
    center splits the tanks 2 : 1 between the heavy and the Mammoth tank."""
    base, depot, radar = MIX_STAGES
    # 12 x 850, 4 x 850 and 8 x 300 of 16,000 credits.
    allied = MixRow(infantry={"e3": 15}, vehicle={"2tnk": 64, "arty": 21})
    # 12 x 1,150, 4 x 900 and 8 x 300 of 19,800 credits.
    soviet = MixRow(infantry={"e3": 12}, vehicle={"3tnk": 70, "v2rl": 18})
    return {
        ALLIES: {
            base: MixRow(infantry={"e3": 5}, vehicle={"1tnk": 95}),
            depot: allied,
            radar: allied,
        },
        SOVIET: {
            base: MixRow(infantry={"e3": 5}, vehicle={"ftrk": 95}),
            depot: soviet,
            radar: soviet,
            # 8 x 1,150, 4 x 2,000, 4 x 900 and 8 x 300 of 23,200 credits.
            TECH_STAGE: MixRow(infantry={"e3": 10}, vehicle={"3tnk": 40, "4tnk": 34, "v2rl": 16}),
        },
    }


@dataclass(frozen=True)
class MixTuning:
    """The army mix: a row of shares for each family and tech stage, in a mixed and a tank set of which `main_force`
    picks one, and when and how far it shifts toward what beats the enemy army seen."""

    main_force: str = field(default=TANKS, metadata={_CHOICES: MAIN_FORCES})
    counter_min_value: int = 1500
    counter_every_ticks: int = 250
    vehicle_heavy_share: float = 0.5
    infantry_heavy_share: float = 0.7
    vehicle_counter_points: int = 15
    infantry_counter_points: int = 10
    artillery_counter_points: int = 5
    # The most points one item may hold in a row, after any shift: a tank row gives one vehicle 95.
    max_share: int = 95
    # Family -> tech stage -> row, of the mixed rows and of the tank rows.
    rows: Mapping[str, Mapping[str, MixRow]] = field(default_factory=_built_in_rows)
    tank_rows: Mapping[str, Mapping[str, MixRow]] = field(default_factory=_built_in_tank_rows)


@dataclass(frozen=True)
class BaseDefenseTuning:
    """Base defense: when an attack is a threat to the base, how big it is, who answers it and when it is over."""

    alarm_radius_cells: int = 12
    threat_area_cells: int = 15
    minor_max_cost: int = 600
    threat_end_ticks: int = 125
    response_radius_cells: int = 25
    arrived_cells: int = 2
    # With the switch on, a responder sent at an enemy keeps that attack while the enemy is in sight, rather than be
    # sent back to the threatened cell once its walk to the enemy takes it out of arrival and of contact.
    keep_attack_in_sight: bool = True
    harvester_flee_cells: int = 6
    harvester_safe_cells: int = 10
    harvester_return_ticks: int = 125
    counter_at_a_time: int = 3
    # The extra defense of a major threat: at most one per extra_defense_seconds and one per threat, only for ground
    # enemies that hit an own building or stand within extra_defense_near_cells of a main-base building, and only
    # while our ground strength near them is below extra_defense_strength_share times theirs.
    extra_defense_seconds: int = 120
    extra_defense_near_cells: int = 12
    extra_defense_strength_share: float = 1.5
    repair_min_cash: int = 100
    # With the switch on, a harvester attacked away from the base under HOLD, RAID or PRESERVE is answered by main
    # alone, for at most harvester_response_seconds.
    harvester_group_response: bool = True
    harvester_response_seconds: int = 60
    # With the switch on, main on the attack (PRESS, STRIKE, FINISH) is left to it unless a threat outweighs the guard
    # and the defenses near it, the override is set on the answering units alone, and enemy aircraft alone move no
    # ground unit that cannot hit them.
    keep_attack_army: bool = True
    # With the switch on, the defenses that hold the base for main are only those whose weapon reaches the threat's
    # enemies.
    home_defenses_in_reach: bool = True
    # With the switch on, a cheap ground defense goes up in range of a known enemy building near our base, at most
    # harass_tower_max at a time and one per enemy building.
    harass_tower: bool = True
    harass_tower_max: int = 2
    # main is kept out of the answer to a threat more than far_rescue_cells from base_center while its estimate there
    # is under far_rescue_min_ratio, or a known enemy yard or ground defense stands within 14 cells of the threat.
    far_rescue_cells: int = 20
    far_rescue_min_ratio: float = 1.0
    # With the switch on, an enemy army worth closing_min_value or more closing within closing_cells of an own yard,
    # the travelling MCV or its site, or base_center makes main intercept it intercept_front_cells in front of the
    # target while main is at least closing_min_ratio as strong, and defend the target otherwise (see
    # `closing_intercept`).
    closing_intercept: bool = True
    closing_min_value: int = 1000
    closing_cells: int = 30
    intercept_front_cells: int = 8
    closing_min_ratio: float = 1.0
    # With the switch on, a truck retreats only from threats within truck_threat_cells (or whose weapon reaches it) it
    # cannot crush: a vehicle, an aircraft, a defense, more than truck_crushable_infantry_max infantry or two anti-armor
    # infantry, but no lone harasser worth less than truck_harasser_max_value while it has half its health; it goes to
    # a shelter away from them and back to its field's ore after truck_retreat_clear_seconds clear.
    truck_retreat: bool = True
    truck_threat_cells: int = 10
    truck_crushable_infantry_max: int = 3
    truck_harasser_max_value: int = 400
    truck_retreat_clear_seconds: int = 8
    # With the switch on, a truck's shelter has no enemy combat unit seen in the last 20 s nor enemy ground defense
    # within truck_shelter_clear_cells, does not lie beyond the threat from our base, and is the safe one nearest
    # base_center; base_center with none.
    truck_shelter_safe: bool = True
    truck_shelter_clear_cells: int = 10
    # With the switch on (and truck_retreat), the harvester crush takes no retreating truck, runs over no group a truck
    # must retreat from, and sends no truck back to harvest while such threats are near it.
    crush_respects_retreat: bool = True
    # With the switch on (and mcv_escort_reliance of [expansion]), the escort of an MCV on its way answers no threat on
    # the base unless no other unit answers it and the defenses near it are worth less than escort_needed_ratio of it.
    keep_mcv_escort: bool = True
    escort_needed_ratio: float = 0.7
    # With the switch on, the rules that order ground defenses for the main base share one cap (see `defense_cap`):
    # none while defense_cap_main stand or are on their way before defense_cap_until_seconds, nor while
    # defense_cap_ratio_min or more are worth defense_cap_army_ratio times our combat units, nor toward a cell with
    # defense_cap_crowd within defense_cap_crowd_cells; the canary alarm's money then goes to the army.
    defense_cap: bool = True
    defense_cap_main: int = 4
    defense_cap_until_seconds: int = 900
    defense_cap_ratio_min: int = 3
    defense_cap_army_ratio: float = 1.5
    defense_cap_crowd: int = 2
    defense_cap_crowd_cells: int = 8
    # With the switch on, an enemy ground group seen within approach_alarm_cells of base_center in the last
    # approach_lookback_seconds, of approach_min_infantry infantry or worth approach_min_value and approach_min_share
    # of our combat units, coming our way, starts the approach alarm (see `approach_alarm`): a defense whenever the
    # Defense queue is free toward it, one per approach_value_per_defense of its worth up to approach_defenses_max,
    # placed approach_goal_cells out toward it, and the counter quota first; it ends destroyed, gone
    # approach_gone_seconds or after approach_max_seconds.
    approach_alarm: bool = True
    approach_alarm_cells: int = 45
    approach_lookback_seconds: int = 10
    approach_min_infantry: int = 5
    approach_min_value: int = 700
    approach_min_share: float = 0.8
    approach_value_per_defense: int = 1500
    approach_defenses_max: int = 3
    approach_goal_cells: int = 8
    approach_gone_seconds: int = 30
    approach_max_seconds: int = 120


@dataclass(frozen=True)
class ScoutingTuning:
    """Scouting: what counts as having seen the enemy base, and how the scout goes about its mission."""

    base_seen_cells: int = 15
    withdraw_cells: int = 8
    withdraw_below_hp: int = 50
    arrival_cells: int = 10
    circle_radius_cells: int = 10
    reached_cells: int = 2
    stuck_ticks: int = 125
    stale_after_ticks: int = 2250
    new_scout_seconds: int = 60
    replace_after_seconds: int = 30
    blind_losses_before_pause: int = 2
    pause_seconds: int = 60
    side_offset_cells: int = 12
    # With the switch on, a scout is sent again whenever the enemy base area, the enemy army or a far ore field has
    # gone unseen this long, and a mission ends after scout_mission_max_seconds.
    rescout: bool = True
    rescout_base_seconds: int = 90
    rescout_army_seconds: int = 60
    rescout_fields_seconds: int = 180
    scout_mission_max_seconds: int = 120
    # With a scout due and no unit to send for this long, one rifleman is trained for it.
    scout_train_after_seconds: int = 30
    # With the switch on, a scout goes by a side waypoint, unseen ore fields near its way and the enemy base's flank,
    # views the base from scout_view_cells and comes home by the other side.
    indirect_route: bool = False
    scout_side_cells: int = 14
    scout_side_share: float = 0.4
    scout_field_cells: int = 15
    scout_fields_max: int = 2
    scout_view_cells: int = 14
    scout_min_enemy_cells: int = 9
    scout_hit_hp_drop: int = 10
    # With the switch on, a watch on the first rush_watch_until_seconds gives a verdict of rush, none or unknown, from
    # what the scout and a watcher at watcher_share of the way to the enemy see.
    rush_detection: bool = True
    rush_watch_until_seconds: int = 300
    rush_unknown_deadline_seconds: int = 150
    rush_barracks: int = 2
    rush_barracks_before_seconds: int = 240
    rush_early_factory_seconds: int = 180
    rush_infantry: int = 6
    rush_vehicles: int = 2
    rush_near_base_cells: int = 20
    rush_approach_value: int = 300
    rush_approach_cells: int = 30
    watcher_share: float = 0.4
    # With rush_scout_direct on, the first slot's scout passes within rush_scout_pass_cells of the enemy's start and then
    # stands rush_exit_post_cells from it toward our base until the watch ends; rush_mass_infantry enemy infantry near
    # the enemy's start by rush_mass_before_seconds is a warning, not a verdict.
    rush_scout_direct: bool = True
    rush_scout_pass_cells: int = 5
    rush_exit_post_cells: int = 13
    # With sight_by_type on, a unit sees as far as the item table says (4 cells for most infantry, the fallback), and
    # the exit post stands sight - 1 beyond the exit cell rush_exit_watch_cells from the enemy's start, moved out of
    # known enemy defense reach; moved out, the scout peeks in to the post in sight for scout_peek_hold_seconds every
    # scout_peek_seconds.
    sight_by_type: bool = True
    rush_exit_watch_cells: int = 8
    scout_peek_seconds: int = 15
    scout_peek_hold_seconds: int = 4
    # With the switch on, an infantry rush scout runs through the enemy base, hits and all: in at the entry cell
    # (scout_run_side_cells off the line for a flank), over the enemy's start and out scout_run_exit_cells behind it,
    # the approach turning direct, left, right with every run, the first by the session id.
    infantry_scout_run_through: bool = True
    scout_run_side_cells: int = 12
    scout_run_exit_cells: int = 10
    # With the switch on, the main-edge and home-field watch posts stand a rifleman's sight less 1 from what they watch
    # toward our base, out of known enemy defense reach (see `scout_missions.watch_post`).
    watch_posts_in_sight: bool = True
    # With the switch on, where a slot's scout was killed is remembered: a hurt area, the rush scout's next run comes
    # by an approach keeping scout_lost_avoid_cells off it, and the slot is refilled scout_slot_replace_seconds later.
    scout_loss_memory: bool = True
    scout_lost_avoid_cells: int = 8
    scout_slot_replace_seconds: int = 120
    # With the switch on, the infantry run through the enemy base has one owner, suspects an unseen defense ahead of a
    # scout hit with no enemy in sight, moves on from a cell it is shot on, passes beside the enemy's start once a scout
    # was lost by it, and pauses after two losses in the enemy's core (see `rush_scout`).
    run_through_guard: bool = True
    # With the switch on, a scout chased within 35 cells of our base retreats into our kill zone: in front of our
    # nearest ground defense or main's middle on the enemy's side (see `scout_retreat.kill_zone`).
    retreat_to_kill_zone: bool = True
    rush_mass_infantry: int = 8
    rush_mass_before_seconds: int = 200
    # An ore field with one of our buildings within this many cells of its center or ore is ours, and no scout is sent
    # to it (see `own_fields`).
    own_field_cells: int = 12
    # With the switch on, scout_slot_count scouts are kept for the whole match: the first infantry trained, a faster
    # unit once one can be trained, and a new one for each lost, ordered at least scout_replace_floor_seconds apart.
    scout_slots: bool = True
    scout_slot_count: int = field(default=2, metadata={_RANGE: (1, 4)})
    scout_replace_floor_seconds: int = 20
    # With the switch on as well, the slots spend less: replacements at least capped_replace_seconds apart, and at most
    # fast_scouts_max fast scouts (jeeps, flak trucks, dogs) ordered in a match, riflemen after that.
    scout_spend_cap: bool = True
    capped_replace_seconds: int = 60
    fast_scouts_max: int = 6
    # The faster unit the Soviet slots train: "dog", a rifle while no kennel puts one on offer, or "ftrk", which needs
    # no kennel but is no scout to lose.
    soviet_scout_unit: str = field(default="dog", metadata={_CHOICES: SOVIET_SCOUT_UNITS})
    # The Allied slots get no jeep, upgrade or replacement, until 2 war factories stand or the game reaches this many
    # seconds: a lone war factory's first minutes go to the tanks that meet the first rush. Riflemen fill them till then.
    allied_fast_scout_after_seconds: int = field(default=300, metadata={_RANGE: (0, math.inf)})
    # How the slots' scouts are steered: a cell within an enemy unit's range and scout_danger_margin_cells is
    # dangerous; a scout flees one within scout_flee_cells; a way passing within scout_center_clear_cells of the map's
    # center goes round by a side; a destination within scout_reached_cells is reached; at most scout_orders_per_batch
    # scout orders go in one batch; and a scout below scout_release_below_hp leaves its slot.
    scout_danger_margin_cells: int = 3
    scout_flee_cells: int = 6
    scout_center_clear_cells: int = 12
    scout_reached_cells: int = 3
    scout_orders_per_batch: int = field(default=2, metadata={_RANGE: (1, 20)})
    scout_release_below_hp: int = field(default=40, metadata={_RANGE: (0, 100)})
    # With the switch on, the scouts keep a wider berth: wide_danger_margin_cells, wide_flee_cells and
    # wide_release_below_hp stand for the three above; and a scout's hp dropping by hurt_min_drop or more between two
    # states makes the ground within hurt_area_cells of where it stood dangerous for hurt_avoid_seconds, at most
    # hurt_areas_max such areas at a time.
    scout_wide_berth: bool = True
    wide_danger_margin_cells: int = 6
    wide_flee_cells: int = 10
    hurt_area_cells: int = 10
    hurt_avoid_seconds: int = 60
    wide_release_below_hp: int = field(default=60, metadata={_RANGE: (0, 100)})
    hurt_min_drop: int = 5
    hurt_areas_max: int = 8
    # With model_scout_choice of [redalert] on as well, each scout's next destination is asked of the red_alert_scout
    # chain among the script's best scout_choice_options places, answered within scout_choice_deadline_ms, and asked
    # again once the scout's pick is scout_choice_every_ticks old.
    scout_choice_options: int = field(default=6, metadata={_RANGE: (2, 9)})
    scout_choice_deadline_ms: int = field(default=1500, metadata={_RANGE: (1, math.inf)})
    scout_choice_every_ticks: int = 125
    # With the switch on and scout_slots on, the slots' scouts take the script's missions, scored by what each would
    # tell over the time it takes: a scout keeps its mission until it has watched the place mission_observe_seconds,
    # the mission turns unsafe or another scores mission_switch_margin times as much, and a mission it was hurt on is
    # not given again for mission_blacklist_seconds. A model is not asked where the scouts go.
    scout_missions: bool = True
    mission_switch_margin: float = 1.25
    # With the switch on, no slot is offered a mission another slot's scout holds (by key or cell), and a mission held
    # less than mission_min_hold_seconds gives way only to one scoring mission_early_switch_ratio times its own.
    mission_hold: bool = True
    mission_min_hold_seconds: int = 20
    mission_early_switch_ratio: float = 1.5
    mission_blacklist_seconds: int = 90
    mission_observe_seconds: int = 10
    # With the switch on and scout_slots on, the slots' scouts hold fire and get no attack order. A scout retreats for
    # scout_retreat_seconds, directly away from the nearest enemy in sight, once its health drops by
    # scout_retreat_hp_drop or more between two states or the enemy units in sight within scout_outclass_cells are worth
    # more than it; and the ground within scout_enemy_base_cells of a seen enemy building that is not a wall is
    # dangerous to it.
    scout_cautious: bool = True
    scout_retreat_hp_drop: int = field(default=1, metadata={_RANGE: (1, 100)})
    scout_outclass_cells: int = 10
    scout_enemy_base_cells: int = 8
    scout_retreat_seconds: int = field(default=8, metadata={_RANGE: (1, math.inf)})
    # With the switch on, the enemy harvesters in sight at an ore field more than truck_field_min_cells from the enemy's
    # start, where the enemy units that fight within truck_guard_cells of the field are worth at most
    # truck_guard_max_value and no remembered defense covers its center, are recorded as a raid opportunity, kept until
    # truck_seen_seconds after they were last seen. It only records.
    exposed_trucks: bool = True
    truck_field_min_cells: int = 20
    truck_guard_max_value: int = 600
    truck_guard_cells: int = 8
    truck_seen_seconds: int = 60
    # With the switch on and scout_cautious on, a slot scout that begins a hit or outclassed retreat first fires one
    # `attack` at the nearest enemy unit in sight within its range that it can hurt, so that some of the enemy peels off
    # to chase it; its retreat move waits scout_bait_shot_seconds, and it makes no other such shot for
    # scout_bait_cooldown_seconds.
    scout_bait_shot: bool = True
    scout_bait_shot_seconds: int = field(default=1, metadata={_RANGE: (1, math.inf)})
    scout_bait_cooldown_seconds: int = field(default=15, metadata={_RANGE: (0, math.inf)})
    # With the switch on and scout_cautious on, a slot scout hunts a lone enemy unit it kills within
    # stray_hunt_max_kill_seconds and stray_hunt_win_ratio times as fast as it is killed, no other enemy unit that fights
    # within stray_hunt_lone_cells, both farther than a defense's cover or the enemy base's ground and
    # stray_hunt_margin_cells; for at most stray_hunt_max_seconds, until it has lost stray_hunt_max_hp_loss points of
    # health, and a target whose hunt ended other than killed is not hunted again for stray_hunt_retry_seconds.
    scout_stray_hunt: bool = True
    stray_hunt_win_ratio: float = field(default=2.0, metadata={_RANGE: (1, math.inf)})
    stray_hunt_max_kill_seconds: int = 10
    stray_hunt_lone_cells: int = 10
    stray_hunt_margin_cells: int = 4
    stray_hunt_max_seconds: int = 20
    stray_hunt_max_hp_loss: int = field(default=30, metadata={_RANGE: (1, 100)})
    stray_hunt_retry_seconds: int = 30
    # With the switch on and scout_cautious on, a slot scout within harass_trigger_cells of the enemy's start or a seen
    # enemy building, with no remembered ground defense and no counted enemy unit that fights within
    # harass_clear_cells, attacks the enemies within harass_target_cells it can hurt, engineers, trucks and the
    # infantry that does not fight, power plants, then other buildings; for at most harass_max_seconds, ending once it
    # has had no target for harass_idle_seconds, and it begins no other harass for harass_cooldown_seconds.
    scout_base_harass: bool = True
    harass_trigger_cells: int = 20
    harass_clear_cells: int = 20
    harass_target_cells: int = 12
    harass_max_seconds: int = 45
    harass_idle_seconds: int = 3
    harass_cooldown_seconds: int = 60
    # With the switch on, every attack that sets out posts one canary rifleman on the straight line from the enemy's
    # start toward our base: at the first cell canary_post_cells or more from the enemy's start, short of
    # canary_home_min_cells from our base, that is passable, outside every known ground defense's coverage plus
    # canary_clear_margin_cells and farther than canary_building_clear_cells from every known enemy building. It is the
    # rush watch's post rifleman while it lives, else one rifleman trained; it holds there and only reports its death
    # (see `canary`).
    canary_post: bool = True
    canary_post_cells: int = 22
    canary_home_min_cells: int = 15
    canary_clear_margin_cells: int = 2
    canary_building_clear_cells: int = 10


@dataclass(frozen=True)
class CoverageTuning:
    """The cells a remembered enemy defense covers: its weapon range and a margin, kept out of by scouts and raids."""

    margin_cells: int = 2
    unknown_range_cells: int = 7


@dataclass(frozen=True)
class CombatTuning:
    """Fighting: the estimate of a fight, when a group engages or retreats, how main goes forward and picks targets."""

    contact_cells: int = 12
    own_reach_cells: int = 8
    defense_margin_cells: int = 1
    defense_weight: float = 2.0
    unseen_factor: float = 1.2
    matchup_factor: float = 0.5
    dominant_share: float = 0.6
    ratio_without_enemy: float = 99.0
    # An enemy unit of a type the item table cannot value counts in the fight estimate and its ratios at this cost.
    unknown_unit_value: int = field(default=300, metadata={_RANGE: (1, math.inf)})
    engage_ratio: float = 1.3
    retreat_ratio: float = 0.9
    emergency_ratio: float = 0.5
    mode_hold_ticks: int = 125
    gathered_cells: int = 6
    gathered_share: float = 0.8
    reinforcement_strength: int = 1000
    weak_hp: int = 40
    overkill_factor: float = 1.2
    focus_weight_weak_threat: float = 1.0
    focus_weight_harvester: float = 1.2
    focus_weight_fighter: float = 1.0
    focus_weight_core: float = 0.6
    focus_weight_other: float = 0.4
    # An armed enemy defense whose weapon hurts one of a group's units, with one of them within its weapon range and
    # defense_threat_margin_cells, is weighed this, above every other class.
    focus_weight_threat_defense: float = 2.0
    defense_threat_margin_cells: int = 1
    focus_window_seconds: int = 6
    retarget_gain: float = 2.5
    # A unit keeps the target it was given this long whatever else is worth more, while the target is in sight and
    # within its weapon range.
    focus_min_hold_seconds: int = 2
    out_of_range_factor: float = 0.5
    unknown_target_hp: int = 1_000_000
    air_targeting: bool = True
    focus_weight_harmless_air: float = 0.05
    air_unanswered_weight: float = 1.0
    # With the switch on, a unit's candidate targets are the enemies within its weapon range and focus_unit_reach_cells,
    # a unit whose best target is covered moves to another only when that one is worth spill_floor of the best, the
    # commander's order leaves the units in contact alone, and main holding or gathering and base defense's responders
    # fire at what they are in contact with. The value of a pairing with a ground target is then multiplied by the
    # unit's damage against the target's armour over its mean damage across the armour classes, to the counter_power.
    contact_focus: bool = True
    # With the switch on, a tank or APC whose target is an enemy artillery piece or V2 drives onto its cell until it is
    # inside that weapon's minimum range less a cell, and only then attacks (see `targeting`).
    tank_charge_artillery: bool = True
    # With the switch on, our ground units near an enemy artillery piece or V2 in sight charge it, fire back on it or
    # leave its range (see `artillery_answer`).
    artillery_answer: bool = True
    artillery_escort_cells: int = 6
    artillery_leave_margin_cells: int = 2
    # With the switch on, a unit hit by a shooter out of sight answers the artillery remembered near it, or steps away
    # from the enemy's start, one vehicle toward it to see the shooter (see `artillery_answer`).
    artillery_unseen_answer: bool = True
    artillery_memory_seconds: int = 30
    artillery_memory_cells: int = 15
    artillery_unseen_step_cells: int = 6
    artillery_unseen_hold_seconds: int = 10
    # With the switch on, main's failed attacks are remembered and an attack on an objective is gated on them and on
    # the defenses that reach it (see `failed_attack`).
    failed_attack_memory: bool = True
    failed_attack_near_cells: int = 20
    failed_attack_loss_share: float = 0.5
    failed_attack_block_cells: int = 15
    failed_attack_force_ratio: float = 1.5
    failed_attack_enemy_ratio: float = 1.3
    failed_attack_memory_seconds: int = 240
    attack_min_objective_ratio: float = 1.2
    attack_leg_cells: int = 10
    # With the switch on, part of main's idle units joins a fight of ours going our way (see `reinforce_winning`).
    reinforce_winning: bool = True
    reinforce_winning_cells: int = 35
    reinforce_winning_local_cells: int = 10
    reinforce_winning_ratio: float = 1.3
    reinforce_winning_min_value: int = 1500
    reinforce_winning_share: float = 0.5
    reinforce_winning_stop_ratio: float = 1.0
    reinforce_winning_max_seconds: int = 60
    focus_unit_reach_cells: int = 4
    spill_floor: float = 0.25
    counter_power: float = 2.0
    # A unit is not paired with a ground target whose armour it does less than this share of its mean damage across the
    # armour classes to: a rifle, at a fifth of its mean against heavy armour, never fires on a heavy tank.
    focus_min_effectiveness: float = 0.25
    # An enemy infantry unit is worth 1 more to an artillery piece or a V2 for each other enemy infantry unit within
    # this many cells of it, which the shell hits as well.
    arty_splash_cells: float = 1.5
    # With the switch on, the infantry of main, the raid group and the guard sent to a point is given a cell each,
    # in rings infantry_spread_cells apart, and idle infantry sharing a cell is moved apart at most once in
    # infantry_spread_recheck_seconds; at most infantry_spread_max_orders such orders a cycle (see `spread`).
    infantry_spread: bool = True
    infantry_spread_cells: int = field(default=1, metadata={_RANGE: (1, math.inf)})
    infantry_spread_recheck_seconds: int = 5
    infantry_spread_max_orders: int = 8
    withdraw_min_cost: int = 700
    withdraw_below_hp: int = 35
    withdraw_below_ratio: float = 2.0
    withdraw_path_cells: int = 6
    withdraw_clear_cells: int = 3
    rejoin_hp: int = 60
    rejoin_after_ticks: int = 750
    # With the switch on, a vehicle of main worth repair_min_cost or more leaves main for a repair zone (see `repair`)
    # at repair_below_hp or less, or at repair_ttd_max_hp or less when the hp it lost per second over the last
    # repair_window_seconds would have it lost within repair_ttd_seconds, and rejoins main at repair_done_hp. For
    # vehicles it takes the place of the withdrawal above. It also leaves main at repair_early_hp or less while a
    # mobile mechanic stands within repair_mechanic_reach_cells of it, and then repairs beside the nearest such one.
    vehicle_repair: bool = True
    repair_window_seconds: int = field(default=5, metadata={_RANGE: (1, math.inf)})
    repair_ttd_seconds: int = 8
    repair_ttd_max_hp: int = field(default=70, metadata={_RANGE: (0, 100)})
    repair_below_hp: int = field(default=35, metadata={_RANGE: (0, 100)})
    repair_min_cost: int = 700
    repair_done_hp: int = field(default=90, metadata={_RANGE: (1, 100)})
    repair_early_hp: int = field(default=60, metadata={_RANGE: (0, 100)})
    repair_mechanic_reach_cells: int = 15
    sweep_grid_size: int = 4
    sweep_reached_cells: int = 6
    sweep_patience_ticks: int = 50
    sweep_engage_cells: int = 6
    # With the switch on, main under HOLD gathers at forward_hold_share of the way to the enemy (or
    # forward_hold_cover_cells beyond a farther second yard or creep target field) once worth forward_hold_min_strength,
    # clear of every remembered defense by forward_hold_defense_clear_cells, and chases at most forward_hold_chase_cells
    # from there.
    forward_hold: bool = False
    forward_hold_share: float = 0.4
    forward_hold_min_strength: int = 1500
    forward_hold_cover_cells: int = 6
    forward_hold_chase_cells: int = 10
    forward_hold_defense_clear_cells: int = 4
    # With the switch on, main under FINISH keeps one target until it leaves the enemy memory or an enemy production
    # building comes into contact, chosen production first, and within production and then among the rest, a building
    # seen within stale_target_seconds before any older sighting.
    sticky_attack_target: bool = True
    stale_target_seconds: int = 60
    # With the switch on, a cluster of crush_min_infantry or more enemy infantry in sight within crush_cluster_cells of
    # its centroid, within crush_reach_cells of one of the own tanks or APCs a run may take (main's, the guard's and
    # base defense's responders), under no remembered defense that fires at ground units and at a ratio of
    # crush_min_ratio or more around it, has its planned count of those within crush_reach_cells at crush_min_hp or
    # more, one for each crush_per_tanks infantry and crush_max_tanks at the most, never fewer than crush_min_tanks,
    # driven through it together; a run starts only with that whole count of them, and with fewer the cluster is told
    # skipped with the gate short_group. A cluster whose rocket soldiers and shock troopers make up
    # crush_at_share or more of it is anti-tank-heavy: it qualifies from crush_at_min_infantry and takes one for each
    # crush_at_per_tanks infantry, crush_at_max_tanks at the most. The tank counts are divided by crush_per_tanks and
    # crush_at_per_tanks, so neither is 0.
    crush_runs: bool = True
    crush_min_infantry: int = 4
    crush_cluster_cells: int = 3
    crush_reach_cells: int = 10
    crush_per_tanks: int = field(default=3, metadata={_RANGE: (1, math.inf)})
    crush_max_tanks: int = 3
    crush_min_hp: int = field(default=50, metadata={_RANGE: (0, 100)})
    crush_min_ratio: float = 1.0
    crush_min_tanks: int = 2
    crush_at_share: float = 0.33
    crush_at_per_tanks: int = field(default=2, metadata={_RANGE: (1, math.inf)})
    crush_at_max_tanks: int = 5
    crush_at_min_infantry: int = 3
    # With the switches on (see `safe_staging`): the gathering moves to the staging or gather point are attack moves;
    # the staging point is moved back toward our base out of every remembered enemy combat unit's weapon range and
    # staging_enemy_margin_cells; and while main is not on the attack the staging and gather points are held
    # staging_behind_cells behind our front ground defense when out ahead of it or staging_defense_cells from every one.
    gather_attack_move: bool = True
    staging_clear_of_enemies: bool = True
    staging_enemy_margin_cells: int = 4
    staging_behind_defenses: bool = True
    staging_defense_cells: int = 8
    staging_behind_cells: int = 4
    # With the switch on (and crush_runs on), an enemy infantry all-in, wave_min_infantry or more within
    # wave_mass_cells of their centroid, that centroid within wave_base_cells of the base's center or wave_army_cells
    # of our crushing vehicles, is met by one wave: the fit crushing vehicles within wave_reach_cells (wave_min_tanks
    # to wave_max_tanks) gather wave_gather_cells outside the mass (present within wave_gather_radius_cells), launch
    # at wave_ready_share present or after wave_max_wait_seconds on lanes wave_lane_cells apart to wave_beyond_cells
    # past the mass, a run of wave_run_seconds at most, the stragglers as one second wave of wave_second_min_tanks or
    # more (see `crush_wave`).
    crush_wave: bool = True
    wave_min_infantry: int = 8
    wave_mass_cells: int = 6
    wave_base_cells: int = 25
    wave_army_cells: int = 12
    wave_reach_cells: int = 16
    wave_min_tanks: int = 3
    wave_max_tanks: int = 10
    wave_gather_cells: int = 8
    wave_gather_radius_cells: int = 3
    wave_ready_share: float = 0.75
    wave_max_wait_seconds: int = 6
    wave_lane_cells: int = 2
    wave_beyond_cells: int = 4
    wave_run_seconds: int = 10
    wave_second_min_tanks: int = 2
    # With the switch on, a remembered enemy missile silo is main's first FINISH target and a raid's first target, and
    # around each launch its first sighting lets one expect (that sighting + nuke_charge_ticks - nuke_build_ticks, then
    # every nuke_charge_ticks), main gathering or standing idle is split into nuke_spread_groups groups at least
    # nuke_spread_cells apart, each at least nuke_keep_off_cells from our densest cluster of structures. A launch comes
    # at most every nuke_charge_ticks, so it is at least 1, and so is the count of groups.
    nuke_spread: bool = True
    nuke_charge_ticks: int = field(default=13500, metadata={_RANGE: (1, math.inf)})
    nuke_build_ticks: int = 1500
    nuke_spread_groups: int = field(default=3, metadata={_RANGE: (1, math.inf)})
    nuke_spread_cells: int = 8
    nuke_keep_off_cells: int = 12
    # With the switch on, spreading holds past an expected launch until an impact (our losses of nuke_impact_lost_value
    # or more in one state) restarts the clock, the home guard spreads too, and a known silo is struck (see `nuke`).
    nuke_follow_up: bool = True
    nuke_impact_lost_value: int = 3000
    # With the switch on, main setting out on an objective with an enemy defense or combat unit near it stops at the
    # last cell of its way outside every enemy's range plus staging_margin_cells, and goes on once staging_body_share of
    # its units stand within staging_body_cells of that cell and its long-range units within 4 more, or after
    # staging_timeout_seconds; the units joining it meet it there.
    forward_staging: bool = True
    staging_margin_cells: int = 3
    staging_body_share: float = 0.75
    staging_body_cells: int = 6
    staging_timeout_seconds: int = 25
    # With the switch on, an enemy MCV or construction yard seen within expansion_near_cells of an own building or of a
    # field our harvesters harvest, and an enemy ground defense whose range plus defense_near_refinery_margin_cells
    # reaches an own refinery, must die: main's nearest units worth strike_min_ratio against it strike it at once (see
    # `strike`).
    strike_near_expansion: bool = True
    expansion_near_cells: int = 15
    defense_near_refinery_margin_cells: int = 4
    strike_min_ratio: float = 1.5
    # With the switch on, the strike near us is sized by its target's defenses alone, from main gathered at staging or
    # idle anywhere, on yards and defenses however long ago seen; main as a whole strikes within
    # strike_near_whole_cells of our base, a defense it cannot outweigh is sieged by its long-range units, and a strike
    # on a building times out after strike_near_max_seconds (see `strike`).
    strike_near_by_defenses: bool = True
    strike_near_whole_cells: int = 25
    strike_near_max_seconds: int = 120
    # With the switch on, a must-kill enemy yard no enemy ground defense reaches is struck from main's gathered and idle
    # home units, sized by its hit points, and not called off on its ratio there for strike_near_grace_seconds.
    strike_near_open_building: bool = True
    strike_near_home_cells: int = 20
    strike_near_grace_seconds: int = 30
    # With the switch on, an enemy refinery or harvesters at an ore field more than overextended_min_enemy_start_cells
    # from the enemy's start and every known enemy yard, with no known enemy ground defense within
    # overextended_defense_clear_cells, is struck by main when it is worth strike_min_ratio there (see `strike` and
    # `overextended`).
    strike_overextended: bool = True
    overextended_min_enemy_start_cells: int = 25
    overextended_defense_clear_cells: int = 8
    # With the switch on, the force of an over-extended strike that ended destroyed or unseen goes on to the exposed
    # expansion building nearest it within overextended_chain_cells rather than home (see `strike`).
    overextended_chain: bool = True
    overextended_chain_cells: int = 40
    # With the switch on, main strikes out at once after a won defense or a regroup at home, when it is worth
    # counterattack_ratio times the enemy army now (see `counterattack` and `strike`).
    counterattack: bool = True
    counterattack_min_kills: int = 1000
    counterattack_window_seconds: int = 10
    counterattack_ratio: float = 1.2
    counterattack_seen_share: float = 1.5
    counterattack_seen_seconds: int = 180
    counterattack_seconds: int = 90
    # With the switch on, a known enemy refinery or yard more than exposed_expansion_start_cells from the enemy's start,
    # and the buildings within exposed_expansion_building_cells of it, that no ground defense reaches and no enemy unit
    # seen in the last exposed_expansion_unit_seconds stands within exposed_expansion_clear_cells of, is struck by a
    # force of main gathered at home worth strike_min_ratio there (see `exposed_expansion` and `strike`).
    strike_exposed_expansion: bool = True
    exposed_expansion_start_cells: int = 20
    exposed_expansion_building_cells: int = 8
    exposed_expansion_clear_cells: int = 10
    exposed_expansion_unit_seconds: int = 30
    exposed_expansion_max_seconds: int = 120
    exposed_expansion_chain_cells: int = 30
    exposed_expansion_kill_seconds: int = 60
    exposed_expansion_kill_share: float = 2.0
    # With the switch on, a unit of main's chasing one target alone into the enemy's base is called back to main (see
    # `chase_leash`).
    chase_leash: bool = True
    chase_leash_cells: int = 15
    chase_leash_base_cells: int = 25
    chase_leash_near_cells: int = 6
    chase_leash_min_units: int = 4
    # With the switch on, main whose objective lies within the weapon range and defended_margin_cells of a known enemy
    # ground defense, and is worth less than defended_engage_ratio there against those defenses and the enemy units
    # near it, goes into no covered ground: it takes an uncovered enemy yard, refinery, production building or
    # harvester, or a lone defense, instead, or sieges from beyond the coverage, a fast unit darting to the front for
    # vision every vision_dart_seconds (see `defended`).
    defended_redirect: bool = True
    defended_engage_ratio: float = 1.2
    defended_margin_cells: int = 2
    vision_dart_seconds: int = 30
    # With the switches on, main's advances (stepped_advance) and its moves home (stepped_return) on a destination
    # step_min_move_cells or more from its centroid go in steps of step_cells: each step point snapped to an open cell
    # within step_snap_cells and kept out of every known ground defense's range plus staging_margin_cells, the next
    # step ordered once step_arrive_share of the group stands within step_arrive_cells of it with every long-range
    # unit within step_long_range_extra_cells more, step_straggler_seconds after half of it arrived, or step_max_seconds
    # after the step was ordered (see `stepping`).
    stepped_advance: bool = True
    stepped_return: bool = True
    step_cells: int = field(default=10, metadata={_RANGE: (1, math.inf)})
    step_min_move_cells: int = 15
    step_arrive_cells: int = 4
    step_arrive_share: float = 0.8
    step_long_range_extra_cells: int = 3
    step_straggler_seconds: int = 12
    step_max_seconds: int = 40
    step_snap_cells: int = 3
    # With the switch on, a group on a stepped advance meeting enemies (counted ground units within met_cells of one of
    # its units, ground defenses reaching one) fights them at met_fight_ratio or more, chasing at most met_chase_cells
    # off the step's way and stepping on met_clear_seconds after the last is gone, and steps back to the step's start
    # otherwise; a decision is kept met_decision_seconds, a fight turning to a step back under met_abandon_ratio. A
    # step back steps on once no counted enemy has stood within step_back_clear_cells of the abandoned step point for
    # step_back_clear_seconds, or at met_resume_ratio against the enemies met (see `stepping`).
    # With the switch on, a stepped move under way keeps its steps for a new plan of the same kind whose destination
    # lies within step_cells of its own, or comes less than step_replan_seconds after it was planned; and a group of
    # step_large_group_units or more counts as arrived within step_large_arrive_extra_cells more (see `stepping`).
    step_plan_continuity: bool = True
    step_replan_seconds: int = 5
    step_large_group_units: int = 15
    step_large_arrive_extra_cells: int = 2
    met_enemy_decision: bool = True
    met_cells: int = 10
    met_fight_ratio: float = 1.0
    met_abandon_ratio: float = 0.7
    met_decision_seconds: int = 6
    met_chase_cells: int = 5
    # With the switch on, a met fight also takes the met enemies within caught_group_cells of the group's centroid, and
    # each of its units given no target attack-moves on the nearest met enemy in sight (see `combat`).
    met_fight_group_reach: bool = True
    met_clear_seconds: int = 3
    step_back_clear_cells: int = 15
    step_back_clear_seconds: int = 8
    met_resume_ratio: float = 1.2
    # With the switch on, main on a move home, a step back or between step points is caught once a visible enemy has had
    # one of its units within its weapon range and 1 in every cycle for caught_confirm_seconds, or stands within
    # route_block_cells of its way; it then fights whatever its ratio, its units within caught_group_cells of its
    # centroid on the enemies as near, until caught_clear_seconds clear (see `stepping`).
    moving_returns_fire: bool = True
    caught_confirm_seconds: int = 2
    caught_group_cells: int = 12
    caught_chase_cells: int = 5
    caught_clear_seconds: int = 3
    route_block_cells: int = 4
    # With the switch on, while an expansion yard stands and the plan is OPENING (a war factory standing), HOLD or RAID,
    # main gathers and the producers rally on the line to the enemy expansion_cover_forward_cells ahead of the
    # farthest-forward expansion, within expansion_cover_reach_cells of every expansion, held back as the forward hold
    # is (see `forward_hold.cover_point`).
    expansion_cover_rally: bool = True
    expansion_cover_forward_cells: int = 6
    expansion_cover_reach_cells: int = 18
    # With the switch on, while main's army stands more than reinforce_away_cells from its home gather point (the
    # forward hold's gather point, else the staging point), the units joining it are held there and go to main only as
    # one group worth reinforce_group_min_value or more, by steps with stepped_advance on (see `reinforce_hold`). Ships
    # off: it split the army in a live game while the hold group re-stepped every cycle.
    reinforce_hold_home: bool = False
    reinforce_away_cells: int = 30
    reinforce_group_min_value: int = 3000
    # With the switch on, an attack setting out is routed by one of three approaches from main's centroid to its
    # objective (see `attack_approach`): direct, or by a side cell flank_side_cells off the line flank_lead_cells
    # short of the objective, the side blocked at the map edge, on impassable ground, with no walk within its step cap
    # or longer than flank_max_length_ratio times the direct walk. A side is taken only while the direct approach is in
    # a ground defense's coverage and its exposure (the covering defenses at defense_weight times their cost, the enemy
    # combat units seen within flank_army_seen_seconds and flank_army_cells of it) is flank_better_share of the direct
    # one's or less; none for an objective within flank_min_distance_cells, worked out again once the objective moves
    # more than flank_recompute_cells while the attack is out.
    flank_approach: bool = True
    flank_recompute_cells: int = 10
    flank_min_distance_cells: int = 20
    flank_lead_cells: int = 12
    flank_side_cells: int = 14
    flank_max_length_ratio: float = 1.8
    flank_army_seen_seconds: int = 30
    flank_army_cells: int = 6
    flank_better_share: float = 0.6
    # With the switch on, main on an attack whose centroid is nearer the enemy's start than our base while the enemy's
    # army heads for ours (the canary lost, an army closing on the base, enemy combat units seen within
    # cross_seen_seconds worth cross_army_value nearer our base, or a major threat) commits to the base trade unless
    # the reverse route is clear (no enemy seen within cross_route_clear_cells, no defense covering it), their base
    # costly and its artillery short of cross_siege_min_long_range and cross_siege_per_defense of the covering
    # defenses: then it goes home (see `cross_commit`).
    commit_on_cross: bool = True
    cross_seen_seconds: int = 10
    cross_army_value: int = 1000
    cross_route_clear_cells: int = 6
    cross_siege_min_long_range: int = 3
    cross_siege_per_defense: float = 0.5


@dataclass(frozen=True)
class SiegeTuning:
    """The siege: the artillery and V2 of main firing from their band behind a screen of main's other units."""

    mode: str = field(default=SCREENED, metadata={_CHOICES: SIEGE_MODES})
    band_inner_margin: float = 0.5
    band_min_range_margin: int = 1
    band_defense_margin: float = 0.5
    screen_gap_arty: int = 2
    screen_gap_v2rl: int = 5
    screen_defense_margin: int = 1
    defense_priority_weight: float = 1.5
    fallback_margin: int = 2
    fallback_step: int = 4
    reissue_cells: int = 2
    reissue_ticks: int = 25
    screen_ratio_allies: float = 1.0
    screen_ratio_soviet: float = 1.0
    # With the switch on, main's screened advance under PRESS, STRIKE or FINISH that is clear of the enemy has the
    # screen attack-move on the objective and the long-range units follow, held back by the line alone; one whose
    # centroid gains less than march_progress_cells toward the objective in march_progress_seconds goes frontal until
    # contact or a new plan. The advance becomes clear once no enemy is within march_clear_cells of any of its units,
    # and stops being clear once one comes within march_blocked_cells.
    march_when_clear: bool = True
    march_progress_cells: int = 5
    march_progress_seconds: int = 15
    march_clear_cells: int = 15
    march_blocked_cells: int = 12
    # A long-range unit with no enemy within its weapon range and two cells moves to a slot of its own on a line across
    # the approach, slot_spacing_cells from the next; it is sent there again once the slot has moved slot_resend_cells
    # or it has stood idle slot_idle_ticks away from it.
    slot_spacing_cells: int = 2
    slot_resend_cells: int = 3
    slot_idle_ticks: int = 50
    # With the switch on, main's long-range units out of contact, with a screen unit within 10 cells of them, stand on
    # a line artillery_offset_cells behind the screen's centroid (the step point while main steps) on the far side
    # from the threat: the enemies within artillery_threat_cells of main, else the stepped move's destination or the
    # cell a move home came from, else the enemy start; sent again once the place moved slot_resend_cells or the threat
    # turned artillery_turn_degrees. A kite fall-back steps away from the enemy, not toward the base (see
    # `screen_place`).
    artillery_screen_place: bool = True
    artillery_offset_cells: int = 3
    artillery_threat_cells: int = 15
    artillery_turn_degrees: int = 45


@dataclass(frozen=True)
class RaidTuning:
    """Raids: whether the agent raids, and under which plans, when the raid group is ready, which units are fit to raid,
    what it goes after, the way it takes to its target, when it breaks off, and where an APC raid unloads and how long
    its APCs wait for the rockets to board again; and whether RAID is open only while a raid could set out now, which
    main units it may borrow and how soon after RAID is chosen a raid has to set out."""

    enabled: bool = True
    standing: bool = False
    launchable_only: bool = True
    borrow_home_cells: int = 15
    launch_within_ticks: int = 1500
    gathered_cells: int = 6
    fit_hp: int = 60
    approach_cells: int = 8
    side_offset_cells: int = 12
    reached_cells: int = 3
    leg_patience_ticks: int = 50
    refinery_after_ticks: int = 1500
    refinery_kill_seconds: int = 20
    raid_break_off_ratio: float = 0.7
    break_off_hp: int = 50
    lost_share: float = 0.5
    target_unseen_ticks: int = 250
    resend_after_ticks: int = 500
    unload_cells: int = 3
    reload_wait_ticks: int = 75
    # With the switch on, the harvesters of the raid opportunities scouting records (`exposed_trucks` of [scouting])
    # are a raid's first targets.
    prefer_exposed_trucks: bool = False
    # With the switch on, a raid opportunity guarded by at most exposed_raid_guard_max and seen within
    # exposed_raid_seen_seconds launches the raid at its harvester whenever no raid is out and a group could set out,
    # under HOLD and FINISH too.
    raid_exposed_trucks: bool = True
    exposed_raid_guard_max: int = 200
    exposed_raid_seen_seconds: int = 20


@dataclass(frozen=True)
class AntiAirTuning:
    """The answer to enemy air: whether there is one, how many enemy combat aircraft seen lately start it, the rocket
    squads and flak the army mix adds, and the AA defenses kept once a radar stands, how many and where."""

    enabled: bool = True
    # The extra answer is in force while aa_min_aircraft or more enemy combat aircraft were seen in the last
    # aa_window_seconds.
    aa_min_aircraft: int = field(default=3, metadata={_RANGE: (1, math.inf)})
    aa_window_seconds: int = 180
    # The mix adds a rocket squad, aa_squad_points of the row, for every aa_aircraft_per_squad aircraft beyond
    # aa_free_aircraft, up to aa_squads_max; and moves air_flak_points from the Soviet main tank to the flak truck.
    aa_free_aircraft: int = 2
    aa_aircraft_per_squad: int = field(default=2, metadata={_RANGE: (1, math.inf)})
    aa_squads_max: int = 3
    aa_squad_points: int = 8
    air_flak_points: int = 0
    aa_defenses: int = 1
    aa_defenses_many: int = 2
    aa_many_aircraft: int = 6
    aa_refinery_cells: int = 6
    # With remote_base_aa on, an enemy combat aircraft that hits one of our bases away from the main yard (its center
    # remote_base_cells or more from it, or with a yard of its own) gets one AA defense put up at that base (see
    # `remote_air`).
    remote_base_aa: bool = True
    remote_base_cells: int = 15


@dataclass(frozen=True)
class PlanRulesTuning:
    """The plan rules: the late clock, when STRIKE and FINISH apply, when PRESS is open and how long a choice holds.

    And the strength main needs before it goes forward under each plan, and the ratios the script's discretionary
    choice turns on.
    """

    opening_attack_strength: int = 99_999
    hold_attack_strength: int = 99_999
    raid_attack_strength: int = 99_999
    press_attack_strength: int = 3000
    strike_attack_strength: int = 1200
    finish_attack_strength: int = 1200
    preserve_attack_strength: int = 99_999
    clock_seconds: int = 180
    press_late_seconds: int = 120
    late_press_guard: bool = True
    clock_balance_margin: int = 500
    clock_switch_ticks: int = 250
    # In the last clock_seconds R6's FINISH comes before R2 and R3, and stays until R6 has not applied for
    # clock_switch_ticks (see `plans`).
    finish_over_preserve: bool = True
    strike_until_seconds: int = 240
    strike_max_enemy_value: int = 500
    strike_min_strength: int = 1200
    strike_abandon_ratio: float = 0.9
    finish_from_seconds: int = 480
    finish_min_army: int = 3000
    finish_army_ratio: float = 1.3
    finish_defense_weight: float = 2.0
    finish_heavy_share: float = 0.3
    cleanup_from_seconds: int = 360
    cleanup_min_strength: int = 8000
    cleanup_unseen_ticks: int = 1500
    press_from_seconds: int = 360
    press_min_army: int = 3000
    press_army_ratio: float = 1.5
    press_unseen_base_seconds: int = 300
    enemy_army_fresh_ticks: int = 3000
    assumed_enemy_share: float = 1.0
    # While main is worth big_army_value, no enemy unit of its army has been in sight for unknown_army_seconds and the
    # enemy base is known, an enemy army not known is weighed at main's worth over big_army_assumed_ratio (see `plans`).
    big_army_value: int = 15000
    unknown_army_seconds: int = 120
    big_army_assumed_ratio: float = 1.5
    blind_press_guard: bool = True
    early_attack_guard: bool = True
    early_press_until_seconds: int = 540
    early_press_kill_value: int = 1500
    default_weaker_ratio: float = 1.0
    default_press_ratio: float = 2.0
    answer_hold_ticks: int = 250
    plan_hold_ticks: int = 750
    finish_rest_ticks: int = 750
    # With the switch on, a FINISH R6 opened stays finish_commit_seconds unless main falls under
    # finish_commit_value_share of its strength then or base defense calls it home (see `plans`).
    finish_commit: bool = True
    finish_commit_seconds: int = 90
    finish_commit_value_share: float = 0.8
    choice_every_ticks: int = 125
    choice_deadline_ms: int = 1500
    model_plan_hold: bool = True
    model_plan_hold_ticks: int = 750
    # With the switch on, the enemy army is estimated once a cycle (see `enemy_estimate`): what was last seen of it, grown
    # by its estimated income since (estimate_income_per_harvester a second for each enemy harvester while our own
    # income gives no rate, estimate_army_spend_share of it on the army) for estimate_growth_max_seconds after an enemy
    # army unit was last in sight, less what we killed.
    enemy_army_estimate: bool = True
    estimate_income_per_harvester: float = 8.0
    estimate_army_spend_share: float = 0.5
    estimate_growth_max_seconds: int = 240


@dataclass(frozen=True)
class ClockTuning:
    """Clock play: spending the money at the end, and keeping a lead: the groups home, a defense now and then."""

    preserve_recall_cells: int = 15
    spend_remaining_seconds: int = 120
    spend_floor_cash: int = 300
    preserve_defense_seconds: int = 60
    preserve_defense_min_cash: int = 600
    # With the switch off, the end-of-match spending buys no defense unless a ground enemy hit an own building within
    # the last minute.
    endgame_defenses: bool = False
    # With the switch on, in the last endgame_lead_seconds, ahead on the trade balance by endgame_lead_margin with our
    # combat army and the enemy army estimate both worth endgame_heavy_army_value and no FINISH in sight, the plan is
    # PRESERVE by R2E: every spare credit buys the strongest defense, up to endgame_lead_defenses_max, within
    # endgame_defense_cluster_cells of the cell endgame_defense_forward_cells from base_center toward the enemy, where
    # main gathers and engages only enemies within endgame_army_leash_cells (see `clock`).
    endgame_lead_defenses: bool = True
    endgame_lead_seconds: int = 300
    endgame_lead_margin: int = 1000
    endgame_heavy_army_value: int = 5000
    endgame_lead_defenses_max: int = 15
    endgame_defense_forward_cells: int = 10
    endgame_defense_cluster_cells: int = 7
    endgame_army_leash_cells: int = 8


@dataclass(frozen=True)
class TerrainTuning:
    """The map picture: the characters a building can stand on, and how much unknown ground is reported."""

    # Clear ground, road and a spawn, the only ground the engine places a building on; every other character of the
    # picture, rough ground among them, is blocked.
    buildable_characters: str = ".=S"
    unknown_block_warn_share: float = 0.2


@dataclass(frozen=True)
class WorldTuning:
    """The world model's account of transports: how long a boarding order and an unload are taken to hold, and how
    many units a transport carries."""

    board_window_ticks: int = 100
    transport_capacity: int = 5


@dataclass(frozen=True)
class CommanderTuning:
    """The model commander: when it hands over from the first scout to main, how old an answer may be, how many of its
    last picks its prompt shows, and how many actions it offers; when main's forward gate opens and closes, how near an
    attack is while it is closed, how long a pick is kept, when the script pulls main back from a lost fight, which
    targets near main its question lists, and how many task detachments go out and when they end."""

    handover_combat_units: int = 6
    answer_max_age_ticks: int = 25
    history_size: int = 4
    # At most six actions, at least two of them active ones (see `command_army`).
    options_max: int = field(default=6, metadata={_RANGE: (2, 6)})
    # The forward gate opens on main's value, its share of the enemy estimate and the share of it gathered in its body,
    # and closes once main's value falls below regroup_value_share of its value when it opened (see `command_gate`).
    go_min_value: int = 4000
    go_enemy_share: float = 0.8
    go_body_share: float = 0.75
    regroup_value_share: float = 0.5
    near_attack_cells: int = 10
    commit_seconds: int = 8
    # The script pulls main back once its fight stays under retreat_ratio this long, until resume_ratio (see
    # `command_retreat`).
    retreat_ratio: float = 0.9
    retreat_confirm_ticks: int = 25
    resume_ratio: float = 1.2
    # While main is worth less than this share of the enemy estimate, main is offered nothing on a target far from the
    # base's center (see `command_army`).
    attack_min_army_share: float = 0.5
    # main's question lists at most targets_max targets within target_reach_cells of its body's center, the nearest
    # first: the enemy groups, whose units are joined by single link at target_group_cells, and the ground defenses
    # (see `command_targets`).
    target_reach_cells: int = 20
    target_group_cells: int = 4
    targets_max: int = 4
    # At most detachments_max task detachments are out of main at once; one fails once its own chance stays under
    # task_fail_chance percent for task_fail_ticks, and a kill defense and a hit group end after kill_defense_seconds
    # and hit_group_seconds (see `command_detachments`).
    detachments_max: int = 2
    task_fail_chance: int = field(default=20, metadata={_RANGE: (0, 100)})
    task_fail_ticks: int = 50
    kill_defense_seconds: int = 45
    hit_group_seconds: int = 30
    # A kill defense is offered only on a defense seen less than kill_defense_seen_seconds ago, and fails once its
    # target has gone unseen that long for task_fail_ticks (see `command_tasks`, `command_detachments`).
    kill_defense_seen_seconds: int = 20
    # Retreat home is offered only while main's centroid is farther than this from the base's center: from nearer,
    # main is home already (see `command_army`). Beyond the staging point, staging_distance_cells of [upkeep] out.
    retreat_home_min_cells: int = 14
    # main's question clusters bases and forces by single link at cluster_cells, and counts an enemy force's crushable
    # infantry at crush_discount of its strength while main has the tanks its crush run takes (see `command_state`).
    cluster_cells: int = field(default=8, metadata={_RANGE: (1, math.inf)})
    crush_discount: float = 0.333
    # A cluster of buildings without a construction yard whose center lies within base_merge_cells of the center of
    # one with a yard of the same side is part of the nearest such base: a refinery a little past cluster_cells from
    # its yard is no base of its own (see `command_state`).
    base_merge_cells: int = field(default=16, metadata={_RANGE: (0, math.inf)})
    # While the gate is open and main as a whole is worth regroup_block_ratio of a seen enemy estimate or more, with
    # no fight lost lately and no major threat on the base, main is not offered to gather (see `command_army`).
    regroup_block_ratio: float = field(default=1.5, metadata={_RANGE: (0, math.inf)})
    # With the switch on and stepped_advance of [combat] on, main's stepped move keeps its plan against a pick of the
    # same destination, other picks wait for the step's release, and the retreat override steps main back one step
    # without closing the forward gate (see `commander`).
    step_commit: bool = True
    # With the switch on, a standing action still sound that the fresh list leaves off is offered again as the
    # continue option, letter Z (see `command_prompt`, `command_army`, `command_scout`).
    continue_option: bool = True
    # With the switch on, the retreat override weighs main's fight with the units of its detachments out (see
    # `commander`).
    override_counts_detachments: bool = True
    # With the switch on, main's forward gate weighs main as a whole with its units away on a job of their own (the
    # detachments', the strike force's, the helpers'), when it opens and while it stays open (see `commander`).
    gate_counts_away: bool = True
    # With the switch on, the forward gate weighs main against the enemy estimate held as the military recovery holds
    # it: at most recovery_estimate_seen_share times the enemy units seen within recovery_seen_seconds (see `commander`).
    gate_capped_estimate: bool = True
    # With the switch on, the commander picks by script once no model answer has been used for
    # script_fallback_seconds, until one is again (see `commander`).
    script_fallback: bool = True
    script_fallback_seconds: int = 8
    # With the switch on, a scout pick with a destination is kept scout_commit_seconds rather than commit_seconds (see
    # `commander`).
    scout_commit: bool = True
    scout_commit_seconds: int = 20


@dataclass(frozen=True)
class RoutingTuning:
    """Waypoint routing: the switch of the graph of waypoints the raids, the scouts, the commander's flank and the MCV
    go by, how far apart two of its waypoints may be joined, and how far a join keeps off covered ground."""

    # With the switch on, the agent keeps the graph and its users go by it (see `waypoints`).
    waypoints: bool = False
    edge_max_cells: int = 30
    edge_clear_margin_cells: int = 2


@dataclass(frozen=True)
class Tuning:
    """Every threshold of the agent, one group per table of the tuning file."""

    loop: LoopTuning = field(default_factory=LoopTuning)
    opening: OpeningTuning = field(default_factory=OpeningTuning)
    upkeep: UpkeepTuning = field(default_factory=UpkeepTuning)
    income: IncomeTuning = field(default_factory=IncomeTuning)
    producers: ProducersTuning = field(default_factory=ProducersTuning)
    placement: PlacementTuning = field(default_factory=PlacementTuning)
    expansion: ExpansionTuning = field(default_factory=ExpansionTuning)
    composition: CompositionTuning = field(default_factory=CompositionTuning)
    mix: MixTuning = field(default_factory=MixTuning)
    base_defense: BaseDefenseTuning = field(default_factory=BaseDefenseTuning)
    scouting: ScoutingTuning = field(default_factory=ScoutingTuning)
    coverage: CoverageTuning = field(default_factory=CoverageTuning)
    combat: CombatTuning = field(default_factory=CombatTuning)
    siege: SiegeTuning = field(default_factory=SiegeTuning)
    raids: RaidTuning = field(default_factory=RaidTuning)
    anti_air: AntiAirTuning = field(default_factory=AntiAirTuning)
    plan_rules: PlanRulesTuning = field(default_factory=PlanRulesTuning)
    clock: ClockTuning = field(default_factory=ClockTuning)
    terrain: TerrainTuning = field(default_factory=TerrainTuning)
    world: WorldTuning = field(default_factory=WorldTuning)
    commander: CommanderTuning = field(default_factory=CommanderTuning)
    routing: RoutingTuning = field(default_factory=RoutingTuning)

    @classmethod
    def from_file(cls, root: Path) -> Tuning:
        """The thresholds held by `config/redalert/tuning.toml` under `root`.

        The file is read strictly: when it is missing, or a table has a key
        that is unknown, missing or not a whole number of zero or more (one
        within its bounds, for a threshold that has bounds; a
        number of zero or more, for a threshold whose built-in value is a
        fraction; a list of whole numbers of zero or more, for one whose
        built-in value is a list; one of its names, for a setting that has
        a few; true or false, for a switch; a string that is not empty, for
        one whose built-in value is a string), this raises `ConfigError`
        naming the file, the table and the key. So it does when the army mix,
        in its mixed rows or its tank rows, lacks a row for a family and stage
        or has one for another, a row holds other tables than the two queues',
        a share is not a whole number from 0 to `max_share`, or a row's shares
        do not add up to 100.
        """
        path = root / TUNING_FILE
        tables = read_tables(path)
        groups = fields(cls)
        reject_unknown_keys(path, "the file", tables, {group.name for group in groups})
        # A group's default factory is its class, which is what one table of the file is read into.
        return cls(
            **{
                group.name: _section(group.default_factory, Table(path, group.name, tables.get(group.name)))
                for group in groups
            }
        )


def _section(section_type: Any, table: Table) -> Any:
    """One table of the file as its group of thresholds, every key of the table accounted for."""
    if section_type is MixTuning:
        return _mix(table)
    values = {threshold.name: _value(threshold, table) for threshold in fields(section_type)}
    table.reject_unread_keys()
    return section_type(**values)


def _mix(table: Table) -> MixTuning:
    """The [mix] table: its thresholds, the mixed rows held by its subtables, one for each family and in it each
    stage, and the tank rows held the same way by its subtable [mix.tanks]."""
    thresholds = {
        threshold.name: _value(threshold, table) for threshold in fields(MixTuning) if threshold.name not in _ROW_FIELDS
    }
    max_share = thresholds["max_share"]
    rows = _rows(table, max_share)
    tanks = table.subtable(TANKS)
    tank_rows = _rows(tanks, max_share, _TANK_ROW_STAGES)
    tanks.reject_unread_keys()
    table.reject_unread_keys()
    return MixTuning(rows=rows, tank_rows=tank_rows, **thresholds)


def _rows(
    table: Table, max_share: int, stages: Mapping[str, tuple[str, ...]] | None = None
) -> dict[str, dict[str, MixRow]]:
    """A set of rows of the army mix: one subtable of `table` for each family, each holding its rows, at the stages
    `stages` gives the family, else at MIX_STAGES."""
    stages = stages or {}
    return {
        family: _family_rows(table.subtable(family), max_share, stages.get(family, MIX_STAGES))
        for family in MIX_FAMILIES
    }


def _family_rows(table: Table, max_share: int, stages: tuple[str, ...]) -> dict[str, MixRow]:
    """A family's rows of the army mix, one subtable for each of its tech stages and no other."""
    rows = {stage: _mix_row(table.subtable(stage), max_share) for stage in stages}
    table.reject_unread_keys()
    return rows


def _mix_row(table: Table, max_share: int) -> MixRow:
    """A row of the army mix: the two queues' tables of shares and no other, the shares adding up to 100 points."""
    row = MixRow(
        infantry=_shares(table.subtable(INFANTRY_SHARES), max_share),
        vehicle=_shares(table.subtable(VEHICLE_SHARES), max_share),
    )
    table.reject_unread_keys()
    total = sum(row.infantry.values()) + sum(row.vehicle.values())
    if total != MIX_ROW_POINTS:
        raise table.error("the shares", f"points that add up to {MIX_ROW_POINTS}, not {total}")
    return row


def _shares(table: Table, max_share: int) -> dict[str, int]:
    """A queue's items in a row of the army mix, each with its share: a whole number of points from 0 to the most."""
    return {item: table.integer(item, minimum=0, maximum=max_share) for item in table.keys()}


def _value(threshold: Field, table: Table) -> str | bool | int | float | tuple[int, ...]:
    """A threshold's value in the file: one of its names, true or false, a string, a list, a fraction or a whole
    number."""
    choices = threshold.metadata.get(_CHOICES)
    if choices is not None:
        return table.one_of(threshold.name, choices)
    # Before the whole numbers: a bool is a kind of int in Python, and true is no count.
    if isinstance(threshold.default, bool):
        return table.boolean(threshold.name)
    if isinstance(threshold.default, str):
        return _characters(threshold.name, table)
    if isinstance(threshold.default, tuple):
        return table.integers(threshold.name, minimum=0)
    if isinstance(threshold.default, float):
        return float(table.number(threshold.name))
    minimum, maximum = threshold.metadata.get(_RANGE, (0, math.inf))
    return table.integer(threshold.name, minimum=minimum, maximum=maximum)


def _characters(key: str, table: Table) -> str:
    """A string setting of the file, which must hold at least one character."""
    characters = table.text(key)
    if not characters:
        raise table.error(key, _CHARACTERS)
    return characters
