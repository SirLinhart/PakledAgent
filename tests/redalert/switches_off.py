"""The Red Alert tuning with the shipped switch set off, for the tests whose expectations were worked out without it.

The shipped configuration turns a set of script switches on and raises a few values with them (the head of
`config/redalert/tuning.toml` says which). A test that builds its tuning from the groups below plays every one of those
switches off, the barracks-first opening with its guard's rifles from the barracks on, the dog as the Soviet scout
unit, 3 refineries (4 late), 2 harvesters a refinery (a refinery worked at 2) and 8 at the most with no field room, 2
refineries a field and a second refinery on a field of 80 ore cells or more, and the mixed main force with shares of at
most 60 and tank rows that keep 20 points of infantry, with only the settings it names changed.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

import situations
from ucla_agent.games.redalert.agent import RedAlertAgent
from ucla_agent.games.redalert.settings import RedAlertSettings
from ucla_agent.games.redalert.tuning import (
    BARRACKS_FIRST,
    CombatTuning,
    ExpansionTuning,
    MixRow,
    MixTuning,
    OpeningTuning,
    PlacementTuning,
    PlanRulesTuning,
    ProducersTuning,
    RaidTuning,
    ScoutingTuning,
    Tuning,
    UpkeepTuning,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
# The rush counter off as well: its two defenses on a threat before the war factory, and the harvester crush. And
# the barracks-first build, which the opening's tests were worked out on.
OPENING_OFF = {
    "order": BARRACKS_FIRST,
    "single_scout_rifle": False,
    "defense_on_threat": False,
    "lean_infantry": False,
    "defense_on_unknown": True,
    "rush_counter_defenses": 0,
    "rush_harvester_crush": False,
    "large_map_economy_first": False,
}
UPKEEP_OFF = {
    "lean_power": False,
    "harvesters_per_refinery": 2,
    "harvesters_max": 8,
    "harvester_cells_per": 0,
    "refinery_worked_harvesters": 2,
    "harvesters_first": False,
    "no_army_infantry": False,
    "economy_first": False,
    "mcv_first": False,
    "refineries_max": 3,
    "refineries_max_late": 4,
    "refinery_field_cap": False,
    "refinery_claims_reached": False,
    "refinery_growth": False,
    "defenses_on_threat_only": False,
    "harvesters_to_fresh_ore": False,
    "fresh_ore_by_load": False,
    "truck_shares_by_mines": False,
    "truck_cap_by_shares": False,
    "canary_alarm": False,
    "dry_field_guard": False,
}
# A defense repaired below repair_below_hp like any other building.
UPKEEP_OFF["repair_defenses_early"] = False
# The war factory load gate off: the tests of the rules that add war factories (the producer and tank-factory income
# steps, the bank, the idle bank and the storage rule) keep testing each rule's own terms; and the gate as the trigger
# (war_factory_on_load) off, so that a test that switches the gate on still tests the income steps behind it.
PRODUCERS_OFF = {
    "tank_factories": False,
    "soviet_tech_center": False,
    "war_factory_load_gate": False,
    "war_factory_on_load": False,
    "no_float": False,
}
PLACEMENT_OFF = {
    "creep_from_opening": False,
    "creep_continuous": False,
    "remote_yard_growth": False,
    "refused_detection": False,
    "refinery_spread": False,
    "refinery_unserved_only": False,
    "creep_stall_skip": False,
    "second_line_local": False,
    "max_refineries_per_field": 2,
    "refinery_second_min_cells": 80,
    "defense_front": False,
    "defense_line_across_lane": False,
    "refinery_small_fields": False,
}
EXPANSION_OFF = {
    "second_yard": False,
    "mcv_safe_site": False,
    "mcv_new_area": False,
    "mcv_reserve_field": False,
    "hold_refinery_for_mcv": False,
    "mcv_short_escort": False,
    "second_mcv_waits": False,
    "new_yard_refinery_until_standing": False,
    "new_yard_refinery_first": False,
    "mcv_refinery_fit": False,
    "mcv_repeat": False,
}
BASE_DEFENSE_OFF = {
    "harvester_group_response": False,
    "keep_attack_army": False,
    "harass_tower": False,
    "truck_retreat": False,
    "truck_shelter_safe": False,
    "approach_alarm": False,
    "crush_respects_retreat": False,
    "defense_cap": False,
    "keep_mcv_escort": False,
}
# The responders sent back to the threatened cell whatever enemy they were last sent at.
BASE_DEFENSE_OFF["keep_attack_in_sight"] = False
# Every defense near the threatened cell holding the base for main, whether it reaches the enemies or not.
BASE_DEFENSE_OFF["home_defenses_in_reach"] = False
SCOUTING_OFF = {
    "rush_detection": False,
    "scout_slots": False,
    "scout_spend_cap": False,
    "soviet_scout_unit": "dog",
    "scout_wide_berth": False,
    "scout_missions": False,
    "scout_cautious": False,
    "allied_fast_scout_after_seconds": 0,
    "canary_post": False,
    "sight_by_type": False,
    "infantry_scout_run_through": False,
    "mission_hold": False,
    "watch_posts_in_sight": False,
    "scout_loss_memory": False,
    "run_through_guard": False,
    "retreat_to_kill_zone": False,
}
# The slot scouts' own attacks off: the bait shot of a retreat, the hunt of a stray scout and the harass of a base.
SCOUTING_OFF |= {"scout_bait_shot": False, "scout_stray_hunt": False, "scout_base_harass": False}
COMBAT_OFF = {
    "sticky_attack_target": False,
    "contact_focus": False,
    "crush_runs": False,
    "forward_staging": False,
    "vehicle_repair": False,
    "defended_redirect": False,
    "stepped_advance": False,
    "stepped_return": False,
    "met_enemy_decision": False,
    "moving_returns_fire": False,
    "flank_approach": False,
    "commit_on_cross": False,
}
# The units joining main sent after it by their own rules while it is away (see `reinforce_hold`).
REINFORCE_HOLD_OFF = {"reinforce_hold_home": False}
COMBAT_OFF.update(REINFORCE_HOLD_OFF)
# The script's crush runs alone against an infantry all-in, with no wave of every tank near (see `crush_wave`).
CRUSH_WAVE_OFF = {"crush_wave": False}
COMBAT_OFF.update(CRUSH_WAVE_OFF)
# The staging point where it always stood, and the gathering moves plain moves (see `safe_staging`).
SAFE_STAGING_OFF = {"gather_attack_move": False, "staging_clear_of_enemies": False, "staging_behind_defenses": False}
COMBAT_OFF.update(SAFE_STAGING_OFF)
SIEGE_OFF = {"march_when_clear": False}
# The long-range units out of contact at the siege's slots, and their fall-back toward the base (see `screen_place`).
SCREEN_PLACE_OFF = {"artillery_screen_place": False}
SIEGE_OFF.update(SCREEN_PLACE_OFF)
RAIDS_OFF = {"launchable_only": False, "raid_exposed_trucks": False}
# The commander's picks free of main's stepped moves.
COMMANDER_OFF = {"step_commit": False, "continue_option": False}
# The retreat override weighing the units left at main's body alone while a detachment is out.
COMMANDER_OFF["override_counts_detachments"] = False
# The forward gate weighing main's units on hand alone, its units away on a job left out.
COMMANDER_OFF["gate_counts_away"] = False
# The forward gate weighing the enemy peak as it is.
COMMANDER_OFF["gate_capped_estimate"] = False
# No script pick while the model is silent: the commander keeps its standing action.
COMMANDER_OFF["script_fallback"] = False
# A scout pick kept commit_seconds like any other.
COMMANDER_OFF["scout_commit"] = False
# The R6(b) cleanup FINISH with main worth 3000, the value the plan tests were worked out with (8000 ships).
PLAN_RULES_OFF = {"model_plan_hold": False, "cleanup_min_strength": 3000}
# The end-of-match spending with the Defense queue among its queues whatever the base saw.
CLOCK_OFF = {"endgame_defenses": True}
# The tank rows that keep 20 points of infantry, the rifles and the rockets 10 or more each, with the Soviets' base row
# the same as their mixed one.
TANK_ROWS_OFF = {
    "allies": {
        "base": MixRow(infantry={"e1": 25, "e3": 15}, vehicle={"1tnk": 60}),
        "fix": MixRow(infantry={"e1": 15, "e3": 15}, vehicle={"2tnk": 60, "1tnk": 10}),
        "dome": MixRow(infantry={"e1": 10, "e3": 10}, vehicle={"2tnk": 50, "arty": 30}),
    },
    "soviet": {
        "base": MixRow(infantry={"e1": 50, "e2": 10, "e3": 25}, vehicle={"ftrk": 15}),
        "fix": MixRow(infantry={"e1": 15, "e2": 10, "e3": 15}, vehicle={"3tnk": 60}),
        "dome": MixRow(infantry={"e1": 10, "e2": 5, "e3": 10}, vehicle={"3tnk": 50, "v2rl": 25}),
        # The tech center's row the same as the radar's: no Mammoth tank.
        "stek": MixRow(infantry={"e1": 10, "e2": 5, "e3": 10}, vehicle={"3tnk": 50, "v2rl": 25}),
    },
}
MIX_OFF = {"main_force": "mixed", "max_share": 60, "tank_rows": TANK_ROWS_OFF}
# The base rows' vehicles trained for main before the service depot, and no Soviet infantry counter.
COMPOSITION_OFF = {"main_vehicles_need_depot": False, "soviet_infantry_counter": False}
# No enemy army estimate, no spending focus, no counter quota and the war factory's block on counter infantry kept.
PLAN_RULES_OFF["enemy_army_estimate"] = False
# A FINISH held no longer than any plan R6 changed to (see `plans`).
PLAN_RULES_OFF["finish_commit"] = False
UPKEEP_OFF["spending_focus"] = False
# The spending focus decided by the balance alone, with no military recovery on our losses.
UPKEEP_OFF["military_recovery"] = False
UPKEEP_OFF["focus_capped_estimate"] = False
COMPOSITION_OFF.update(counter_quota=False, infantry_counter_lift=False)
# The counter quota as it was before the approach alarm: capped by the rifles, no jeeps preferred, held by the opening.
COMPOSITION_OFF.update(counter_on_approach=False, counter_cap_skips_rifles=False, counter_prefer_jeeps=False)
COMPOSITION_OFF["anti_artillery_quota"] = False
# No endgame lead defenses, no site line penalty, no MCV escort reliance and no rally covering the expansions.
CLOCK_OFF["endgame_lead_defenses"] = False
EXPANSION_OFF.update(mcv_line_penalty=False, mcv_escort_reliance=False)
COMBAT_OFF["expansion_cover_rally"] = False
# A met fight on the enemies near the step's way alone, its units with no target there left as they are.
COMBAT_OFF["met_fight_group_reach"] = False
# No failed attack remembered and no attack weighed with the defenses at its objective (see `failed_attack`).
COMBAT_OFF["failed_attack_memory"] = False
# No idle unit of main sent to a fight of ours going our way (see `reinforce_winning`).
COMBAT_OFF["reinforce_winning"] = False
# The nuke clock alone: no spreading held past a launch, no home guard spread, no silo struck (see `nuke`).
COMBAT_OFF["nuke_follow_up"] = False
# No answer to enemy artillery but focus fire's (see `artillery_answer`).
COMBAT_OFF["artillery_answer"] = False
COMBAT_OFF["artillery_unseen_answer"] = False
# No strike on an enemy expansion nothing defends (see `exposed_expansion`).
COMBAT_OFF["strike_exposed_expansion"] = False
# No unit of main's called back from a chase alone into the enemy's base (see `chase_leash`).
COMBAT_OFF["chase_leash"] = False
# The force of an over-extended strike that ended goes home (see `strike`).
COMBAT_OFF["overextended_chain"] = False
# No counterattack after a won defense or a regroup (see `counterattack`).
COMBAT_OFF["counterattack"] = False
# The strike near us sized by half the enemy army's peak too, from staging alone, on targets seen lately.
COMBAT_OFF["strike_near_by_defenses"] = False
# No open-building strike from the idle units at home (see `strike`).
COMBAT_OFF["strike_near_open_building"] = False
# A stepped move started afresh by every new plan, and arrival within step_arrive_cells for any group.
COMBAT_OFF["step_plan_continuity"] = False


def opening_off(**values: Any) -> OpeningTuning:
    """The opening's thresholds with its shipped switches off, and `values` in place of theirs."""
    return OpeningTuning(**{**OPENING_OFF, **values})


def upkeep_off(**values: Any) -> UpkeepTuning:
    """Base upkeep's thresholds with its shipped switches off and its earlier caps, and `values` in place of theirs."""
    return UpkeepTuning(**{**UPKEEP_OFF, **values})


def producers_off(**values: Any) -> ProducersTuning:
    """The producers' thresholds with the tank factories and the Soviet tech center off, and `values` in place of
    theirs."""
    return ProducersTuning(**{**PRODUCERS_OFF, **values})


def placement_off(**values: Any) -> PlacementTuning:
    """Placement's thresholds with its shipped switches off, and `values` in place of theirs."""
    return PlacementTuning(**{**PLACEMENT_OFF, **values})


def expansion_off(**values: Any) -> ExpansionTuning:
    """The second yard's thresholds with its shipped switches off, and `values` in place of theirs."""
    return ExpansionTuning(**{**EXPANSION_OFF, **values})


def mix_off(**values: Any) -> MixTuning:
    """The army mix with the mixed main force, shares of at most 60 and the tank rows above, and `values`."""
    return MixTuning(**{**MIX_OFF, **values})


def scouting_off(**values: Any) -> ScoutingTuning:
    """Scouting's thresholds with its shipped switches off and the dog as the Soviet scout unit, and `values`."""
    return ScoutingTuning(**{**SCOUTING_OFF, **values})


def combat_off(**values: Any) -> CombatTuning:
    """The fighting thresholds with the sticky attack target, the focus fire in contact, the crush runs, the forward
    staging, the vehicle repair and the defended redirect off, and `values` in place of theirs."""
    return CombatTuning(**{**COMBAT_OFF, **values})


def raids_off(**values: Any) -> RaidTuning:
    """The raids' thresholds with RAID open on a target alone and no raid launched on exposed trucks, and `values` in
    place of theirs."""
    return RaidTuning(**{**RAIDS_OFF, **values})


def plan_rules_off(**values: Any) -> PlanRulesTuning:
    """The plan rules with the model plan hold off, and `values` in place of theirs."""
    return PlanRulesTuning(**{**PLAN_RULES_OFF, **values})


# The settings above by the group of the tuning that holds them.
GROUPS_OFF = {
    "opening": OPENING_OFF,
    "upkeep": UPKEEP_OFF,
    "producers": PRODUCERS_OFF,
    "placement": PLACEMENT_OFF,
    "expansion": EXPANSION_OFF,
    "mix": MIX_OFF,
    "composition": COMPOSITION_OFF,
    "base_defense": BASE_DEFENSE_OFF,
    "scouting": SCOUTING_OFF,
    "combat": COMBAT_OFF,
    "siege": SIEGE_OFF,
    "raids": RAIDS_OFF,
    "plan_rules": PLAN_RULES_OFF,
    "clock": CLOCK_OFF,
    "commander": COMMANDER_OFF,
}


def switched_off(tuning: Tuning) -> Tuning:
    """`tuning`, such as the tracked file's, with the shipped switch set off and the settings above as they give."""
    groups = {group: dataclasses.replace(getattr(tuning, group), **off) for group, off in GROUPS_OFF.items()}
    return dataclasses.replace(tuning, **groups)


SWITCHES_OFF = switched_off(Tuning())


def tuning_off(**groups: Any) -> Tuning:
    """Every threshold with the shipped switch set off, and the groups `groups` names in place of theirs."""
    return dataclasses.replace(SWITCHES_OFF, **groups)


def _toml_value(value: object) -> str:
    """A switch or setting of the groups above as the tuning file writes it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return f'"{value}"' if isinstance(value, str) else str(value)


def _tank_row_table(family: str, stage: str, row: MixRow) -> str:
    """A tank row's table as the tuning file writes it: the rows share their infantry text, so a row is found by it."""

    def shares(points: dict[str, int]) -> str:
        if not points:
            return "{}"
        return "{ " + ", ".join(f"{item} = {share}" for item, share in points.items()) + " }"

    return f"[mix.tanks.{family}.{stage}]\ninfantry = {shares(row.infantry)}\nvehicle = {shares(row.vehicle)}\n"


def write_switches_off(tuning_file: Path) -> None:
    """Rewrite a copy of the tracked tuning file so that it holds the shipped switch set off, as `SWITCHES_OFF` does."""
    text = tuning_file.read_text(encoding="utf-8")
    off = {key: value for group in GROUPS_OFF.values() for key, value in group.items() if key != "tank_rows"}
    for key, value in off.items():
        text, count = re.subn(rf"^{key} = .*$", f"{key} = {_toml_value(value)}", text, flags=re.M)
        assert count == 1, key
    for family, rows in Tuning().mix.tank_rows.items():
        for stage, row in rows.items():
            shipped = _tank_row_table(family, stage, row)
            assert shipped in text, shipped
            text = text.replace(shipped, _tank_row_table(family, stage, TANK_ROWS_OFF[family][stage]))
    tuning_file.write_text(text, encoding="utf-8", newline="\n")


# The model switches of [redalert] that the tracked config/agent.toml ships on.
MODEL_SWITCHES = ("model_plan_choice", "model_commander")
# Each of those switches' lines as it ships and as the script-only setting: a match played from a copy of the tracked
# config/ with them off asks no model at all.
MODEL_SWITCHES_ON_OFF = {f"{switch} = true\n": f"{switch} = false\n" for switch in MODEL_SWITCHES}


def write_model_switches_off(agent_config: Path) -> None:
    """Rewrite a copy of the tracked `config/agent.toml` so that no model chooses the plan or commands: the script
    plays alone, and the match never reaches a model server."""
    text = agent_config.read_text(encoding="utf-8")
    for shipped, off in MODEL_SWITCHES_ON_OFF.items():
        assert text.count(shipped) == 1, shipped
        text = text.replace(shipped, off)
    agent_config.write_text(text, encoding="utf-8", newline="\n")


def settings_models_off(root: Path = REPO_ROOT) -> RedAlertSettings:
    """The `[redalert]` settings of `config/agent.toml` under `root`, the tracked file's unless a test names another
    root, with the model switches off as `write_model_switches_off` writes them: an agent built in the test process
    with them plays script-only and builds no client of a model server."""
    return dataclasses.replace(RedAlertSettings.from_file(root), **dict.fromkeys(MODEL_SWITCHES, False))


def new_agent(*managers: Any, tuning: Tuning | None = None, **more: Any) -> RedAlertAgent:
    """`situations.new_agent`, its thresholds those with the shipped switch set off unless a test gives a tuning."""
    return situations.new_agent(*managers, tuning=tuning or SWITCHES_OFF, **more)


def own_agent(chooser: Any = None, tuning: Tuning | None = None, **more: Any) -> RedAlertAgent:
    """`situations.own_agent`, its thresholds those with the shipped switch set off unless a test gives a tuning."""
    return situations.own_agent(chooser, tuning=tuning or SWITCHES_OFF, **more)


# main's stepped movement off, out and home (see `stepping`): the tests of main's movement worked out before it keep
# testing one order to the destination.
STEPPING_OFF = {
    "stepped_advance": False,
    "stepped_return": False,
    "met_enemy_decision": False,
    "moving_returns_fire": False,
}


# The attack's approach and the commit when the armies cross off as well (see `attack_approach`, `cross_commit`): those
# tests send main straight on its objective.
APPROACH_OFF = {"flank_approach": False, "commit_on_cross": False}


def stepping_off(tuning: Tuning | None = None) -> Tuning:
    """`tuning`, the built-in thresholds when none is given, with main's stepped movement, the attack's approach and the
    commit when the armies cross off."""
    given = tuning or Tuning()
    return dataclasses.replace(given, combat=dataclasses.replace(given.combat, **STEPPING_OFF, **APPROACH_OFF))


def screen_place_off(tuning: Tuning | None = None) -> Tuning:
    """`tuning`, the built-in thresholds when none is given, with the screened place of the long-range units off."""
    given = tuning or Tuning()
    return dataclasses.replace(given, siege=dataclasses.replace(given.siege, **SCREEN_PLACE_OFF))


def crush_wave_off(tuning: Tuning | None = None) -> Tuning:
    """`tuning`, the built-in thresholds when none is given, with the script's crush runs alone against an infantry
    all-in."""
    given = tuning or Tuning()
    return dataclasses.replace(given, combat=dataclasses.replace(given.combat, **CRUSH_WAVE_OFF))


def reinforce_hold_off(tuning: Tuning | None = None) -> Tuning:
    """`tuning`, the built-in thresholds when none is given, with the units joining main sent after it by their own
    rules while it is away."""
    given = tuning or Tuning()
    return dataclasses.replace(given, combat=dataclasses.replace(given.combat, **REINFORCE_HOLD_OFF))


def safe_staging_off(tuning: Tuning | None = None) -> Tuning:
    """`tuning`, the built-in thresholds when none is given, with the staging point where it always stood and the
    gathering moves plain moves."""
    given = tuning or Tuning()
    return dataclasses.replace(given, combat=dataclasses.replace(given.combat, **SAFE_STAGING_OFF))
