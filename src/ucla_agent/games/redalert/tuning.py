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
    # no rocket infantry for the cover against armor.
    lean_infantry: bool = True
    lean_guard_rifles: int = 3
    # With the switch on, once a barracks stands the opening trains one rifleman, the first scout, and no other
    # infantry but its rocket cover until a rush warning or verdict, a threat at the base or the service depot.
    single_scout_rifle: bool = False


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
    # With the switch on (and harvesters_to_fresh_ore), each harvester moved off a dry field goes to the nearest safe
    # field whose load is below field_share_per_refinery for each refinery serving it, at least one.
    fresh_ore_by_load: bool = True
    field_share_per_refinery: int = 2


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
    rush_approach_value: int = 1000
    rush_approach_cells: int = 30
    watcher_share: float = 0.4
    # With rush_scout_direct on, the first slot's scout passes within rush_scout_pass_cells of the enemy's start and then
    # stands rush_exit_post_cells from it toward our base until the watch ends; rush_mass_infantry enemy infantry near
    # the enemy's start by rush_mass_before_seconds is a warning, not a verdict.
    rush_scout_direct: bool = True
    rush_scout_pass_cells: int = 5
    rush_exit_post_cells: int = 13
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
    # With the switch on, an enemy refinery or harvesters at an ore field more than overextended_min_enemy_start_cells
    # from the enemy's start and every known enemy yard, with no known enemy ground defense within
    # overextended_defense_clear_cells, is struck by main when it is worth strike_min_ratio there (see `strike` and
    # `overextended`).
    strike_overextended: bool = True
    overextended_min_enemy_start_cells: int = 25
    overextended_defense_clear_cells: int = 8
    # With the switch on, main whose objective lies within the weapon range and defended_margin_cells of a known enemy
    # ground defense, and is worth less than defended_engage_ratio there against those defenses and the enemy units
    # near it, goes into no covered ground: it takes an uncovered enemy yard, refinery, production building or
    # harvester, or a lone defense, instead, or sieges from beyond the coverage, a fast unit darting to the front for
    # vision every vision_dart_seconds (see `defended`).
    defended_redirect: bool = True
    defended_engage_ratio: float = 1.2
    defended_margin_cells: int = 2
    vision_dart_seconds: int = 30


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
    choice_every_ticks: int = 125
    choice_deadline_ms: int = 1500
    model_plan_hold: bool = True
    model_plan_hold_ticks: int = 750


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
