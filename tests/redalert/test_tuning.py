"""The agent's thresholds: the built-in values, and the strict reading of the tracked tuning file."""

from __future__ import annotations

import builtins
import dataclasses
import re
import shutil
from pathlib import Path

import pytest

from ucla_agent.core.config import ConfigError
from ucla_agent.games.redalert.settings import RedAlertSettings
from ucla_agent.games.redalert.tuning import (
    TUNING_FILE,
    BaseDefenseTuning,
    CombatTuning,
    CompositionTuning,
    ExpansionTuning,
    LoopTuning,
    MixRow,
    MixTuning,
    OpeningTuning,
    PlacementTuning,
    PlanRulesTuning,
    ProducersTuning,
    RaidTuning,
    ScoutingTuning,
    TerrainTuning,
    Tuning,
    UpkeepTuning,
    WorldTuning,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def tuning_root(tmp_path: Path) -> Path:
    """A folder standing in for the repository root, with a copy of the tracked tuning file."""
    target = tmp_path / TUNING_FILE
    target.parent.mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / TUNING_FILE, target)
    return tmp_path


def rewrite(root: Path, old: str, new: str) -> None:
    """Replace one piece of text in the tuning file under `root`; the piece has to be there."""
    path = root / TUNING_FILE
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_the_tracked_file_holds_exactly_the_built_in_values():
    assert Tuning.from_file(REPO_ROOT) == Tuning()


def test_the_built_in_values_are_the_defaults_the_requirements_give():
    tuning = Tuning()

    assert (tuning.loop.timing_window_ticks, tuning.loop.reissue_after_ticks) == (250, 75)
    assert tuning.loop.reasoning_summary_chars == 200
    assert (tuning.opening.guard_rifles, tuning.opening.guard_radius_cells, tuning.opening.guard_rockets) == (5, 8, 4)
    assert (tuning.opening.anti_armor_at_seconds, tuning.opening.infantry_min_cash) == (150, 100)
    assert (tuning.opening.main_rifles, tuning.opening.main_rifle_min_cash) == (6, 400)
    upkeep = tuning.upkeep
    assert (upkeep.headroom_early, upkeep.headroom_mid, upkeep.headroom_large, upkeep.headroom_tesla) == (
        20,
        60,
        100,
        160,
    )
    assert (upkeep.headroom_mid_from_seconds, upkeep.headroom_large_producers, upkeep.headroom_tesla_count) == (
        360,
        5,
        2,
    )
    assert tuning.upkeep.advanced_power_min_cash == 500
    assert (tuning.upkeep.soviet_tesla_coils, tuning.upkeep.preserve_anti_armor) == (2, True)
    assert (upkeep.park_harvesters, upkeep.park_harvesters_seconds_left, upkeep.park_distance_cells) == (False, 120, 8)
    assert (tuning.upkeep.harvesters_per_refinery, tuning.upkeep.harvesters_max) == (2, 22)
    assert (tuning.upkeep.extra_refinery_min_cash, tuning.upkeep.refineries_late_after_seconds) == (1500, 480)
    assert tuning.upkeep.refinery_full_share == 0.75
    assert (tuning.upkeep.refineries_max, tuning.upkeep.refineries_max_late) == (6, 6)
    assert (tuning.upkeep.harvester_idle_states, tuning.upkeep.harvest_reorder_ticks) == (3, 250)
    assert (tuning.upkeep.production_cash_reserve, tuning.upkeep.infantry_at_a_time) == (300, 3)
    assert (tuning.upkeep.tech_min_cash, tuning.upkeep.tech_min_harvesters) == (1500, 4)
    assert (tuning.upkeep.repair_below_hp, tuning.upkeep.repair_min_cash) == (75, 300)
    assert (tuning.upkeep.repair_stop_above_hp, tuning.upkeep.repair_stop_below_cash) == (95, 100)
    assert (tuning.upkeep.staging_distance_cells, tuning.upkeep.low_power_wait_seconds) == (10, 10)
    assert (tuning.upkeep.power_down_enemy_radius_cells, tuning.upkeep.power_restore_headroom) == (10, 40)
    assert (tuning.placement.ring_min_cells, tuning.placement.ring_max_cells) == (2, 12)
    assert (tuning.placement.defense_radius_cells, tuning.placement.defense_distance_cells) == (6, 5)
    assert tuning.placement.war_factory_exit_cells == 2
    assert (tuning.placement.gap_cells, tuning.placement.adjacent_cells) == (1, 2)
    assert tuning.placement.defense_adjacent_cells == 7
    assert (tuning.composition.raid_vehicles, tuning.composition.raid_infantry) == (4, 6)
    assert (tuning.composition.raid_mixed_vehicles, tuning.composition.raid_mixed_infantry) == (0, 3)
    assert (tuning.raids.gathered_cells, tuning.raids.fit_hp) == (6, 60)
    assert (tuning.coverage.margin_cells, tuning.coverage.unknown_range_cells) == (2, 7)
    assert (tuning.base_defense.alarm_radius_cells, tuning.base_defense.threat_area_cells) == (12, 15)
    assert (tuning.base_defense.minor_max_cost, tuning.base_defense.threat_end_ticks) == (600, 125)
    assert (tuning.base_defense.response_radius_cells, tuning.base_defense.arrived_cells) == (25, 2)
    assert (tuning.base_defense.harvester_flee_cells, tuning.base_defense.harvester_safe_cells) == (6, 10)
    assert tuning.base_defense.harvester_return_ticks == 125
    assert (tuning.base_defense.counter_at_a_time, tuning.base_defense.extra_defense_seconds) == (3, 120)
    assert tuning.base_defense.repair_min_cash == 100
    assert tuning.scouting.base_seen_cells == 15
    assert (tuning.scouting.withdraw_cells, tuning.scouting.withdraw_below_hp) == (8, 50)
    assert (tuning.scouting.arrival_cells, tuning.scouting.circle_radius_cells) == (10, 10)
    assert (tuning.scouting.reached_cells, tuning.scouting.stuck_ticks) == (2, 125)
    assert (tuning.scouting.stale_after_ticks, tuning.scouting.new_scout_seconds) == (2250, 60)
    assert (tuning.scouting.replace_after_seconds, tuning.scouting.blind_losses_before_pause) == (30, 2)
    assert (tuning.scouting.pause_seconds, tuning.scouting.side_offset_cells) == (60, 12)
    combat = tuning.combat
    assert (combat.contact_cells, combat.own_reach_cells, combat.defense_margin_cells) == (12, 8, 1)
    assert (combat.defense_weight, combat.unseen_factor, combat.ratio_without_enemy) == (2.0, 1.2, 99.0)
    assert (combat.matchup_factor, combat.dominant_share) == (0.5, 0.6)
    assert (combat.engage_ratio, combat.retreat_ratio, combat.emergency_ratio) == (1.3, 0.9, 0.5)
    assert combat.mode_hold_ticks == 125


@pytest.mark.parametrize("given, read", [("1.4", 1.4), ("2", 2.0)], ids=["a fraction", "a whole number"])
def test_a_threshold_whose_built_in_value_is_a_fraction_reads_any_number(tuning_root, given, read):
    rewrite(tuning_root, "engage_ratio = 1.3", f"engage_ratio = {given}")

    engage_ratio = Tuning.from_file(tuning_root).combat.engage_ratio

    assert engage_ratio == read and isinstance(engage_ratio, float)


@pytest.mark.parametrize("given", ['"1.3"', "-1.3", "true"], ids=["text", "a negative number", "true"])
def test_a_fraction_threshold_that_is_not_a_number_of_zero_or_more_is_named(tuning_root, given):
    rewrite(tuning_root, "engage_ratio = 1.3", f"engage_ratio = {given}")

    with pytest.raises(ConfigError, match=r"engage_ratio in \[combat\] must be a number of at least 0"):
        Tuning.from_file(tuning_root)


def test_a_value_of_the_file_replaces_the_built_in_one(tuning_root):
    rewrite(tuning_root, "guard_rifles = 5", "guard_rifles = 7")

    tuning = Tuning.from_file(tuning_root)

    assert tuning.opening == dataclasses.replace(OpeningTuning(), guard_rifles=7)
    assert (tuning.loop, tuning.upkeep) == (LoopTuning(), UpkeepTuning())
    assert (tuning.placement, tuning.composition) == (PlacementTuning(), CompositionTuning())


def test_the_thresholds_cannot_be_changed_once_read():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Tuning().opening.guard_rifles = 9


def test_a_missing_file_is_a_config_error_that_names_it(tmp_path):
    with pytest.raises(ConfigError) as raised:
        Tuning.from_file(tmp_path)

    assert str(tmp_path / TUNING_FILE) in str(raised.value)


def test_a_file_that_is_not_toml_is_a_config_error(tuning_root):
    rewrite(tuning_root, "[loop]", "[loop")

    with pytest.raises(ConfigError, match="not valid TOML"):
        Tuning.from_file(tuning_root)


@pytest.mark.parametrize(
    "old, new, named",
    [
        ("guard_rifles = 5\n", "", "guard_rifles in [opening]"),
        ("harvesters_max = 22", 'harvesters_max = "twelve"', "harvesters_max in [upkeep]"),
        ("ring_max_cells = 12", "ring_max_cells = 12.5", "ring_max_cells in [placement]"),
        ("raid_vehicles = 4", "raid_vehicles = true", "raid_vehicles in [composition]"),
        ("timing_window_ticks = 250", "timing_window_ticks = -250", "timing_window_ticks in [loop]"),
        ("soviet_tesla_coils = 2\n", "", "soviet_tesla_coils in [upkeep]"),
        ("soviet_tesla_coils = 2", "soviet_tesla_coils = -1", "soviet_tesla_coils in [upkeep]"),
    ],
    ids=["a missing key", "text", "a fraction", "true", "a negative number", "no tesla coils", "negative coils"],
)
def test_a_key_that_is_missing_or_not_a_whole_number_of_zero_or_more_is_named(tuning_root, old, new, named):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError) as raised:
        Tuning.from_file(tuning_root)

    assert named in str(raised.value) and str(tuning_root / TUNING_FILE) in str(raised.value)


def test_no_tesla_coils_in_the_file_switch_the_soviet_coils_off(tuning_root):
    rewrite(tuning_root, "soviet_tesla_coils = 2", "soviet_tesla_coils = 0")

    assert Tuning.from_file(tuning_root).upkeep == dataclasses.replace(UpkeepTuning(), soviet_tesla_coils=0)


def test_preserve_anti_armor_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "preserve_anti_armor = true", "preserve_anti_armor = false")

    assert Tuning.from_file(tuning_root).upkeep == dataclasses.replace(UpkeepTuning(), preserve_anti_armor=False)


@pytest.mark.parametrize(
    "new", ["", "preserve_anti_armor = 1\n", 'preserve_anti_armor = "true"\n'], ids=["missing", "a number", "text"]
)
def test_a_preserve_anti_armor_switch_that_is_missing_or_not_true_or_false_is_named(tuning_root, new):
    rewrite(tuning_root, "preserve_anti_armor = true\n", new)

    with pytest.raises(ConfigError, match=r"preserve_anti_armor in \[upkeep\] must be true or false"):
        Tuning.from_file(tuning_root)


def test_on_threat_only_upkeep_defenses_ship_on_with_12_side_cells_and_false_in_the_file_switches_them_off(tuning_root):
    rewrite(tuning_root, "defenses_on_threat_only = true", "defenses_on_threat_only = false")
    rewrite(tuning_root, "tesla_side_cells = 12", "tesla_side_cells = 20")

    for shipped in (Tuning().upkeep, Tuning.from_file(REPO_ROOT).upkeep):
        assert (shipped.defenses_on_threat_only, shipped.tesla_side_cells) == (True, 12)
    switched = dataclasses.replace(UpkeepTuning(), defenses_on_threat_only=False, tesla_side_cells=20)
    assert Tuning.from_file(tuning_root).upkeep == switched


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("defenses_on_threat_only = true\n", "", r"defenses_on_threat_only in \[upkeep\] must be true or false"),
        (
            "defenses_on_threat_only = true",
            'defenses_on_threat_only = "false"',
            r"defenses_on_threat_only in \[upkeep\] must be true or false",
        ),
        ("tesla_side_cells = 12\n", "", r"tesla_side_cells in \[upkeep\] must be a whole number"),
        ("tesla_side_cells = 12", "tesla_side_cells = -12", r"tesla_side_cells in \[upkeep\] must be a whole number"),
        ("tesla_side_cells = 12", "tesla_side_cells = 1.5", r"tesla_side_cells in \[upkeep\] must be a whole number"),
    ],
    ids=["switch missing", "switch text", "cells missing", "negative cells", "cells a fraction"],
)
def test_on_threat_only_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_harvester_parking_ships_off_and_true_in_the_file_switches_it_on(tuning_root):
    rewrite(tuning_root, "park_harvesters = false", "park_harvesters = true")

    assert Tuning.from_file(REPO_ROOT).upkeep.park_harvesters is False
    assert Tuning.from_file(tuning_root).upkeep == dataclasses.replace(UpkeepTuning(), park_harvesters=True)


@pytest.mark.parametrize(
    "new", ["", "park_harvesters = 0\n", 'park_harvesters = "false"\n'], ids=["missing", "a number", "text"]
)
def test_a_park_harvesters_switch_that_is_missing_or_not_true_or_false_is_named(tuning_root, new):
    rewrite(tuning_root, "park_harvesters = false\n", new)

    with pytest.raises(ConfigError, match=r"park_harvesters in \[upkeep\] must be true or false"):
        Tuning.from_file(tuning_root)


@pytest.mark.parametrize(
    "old, new",
    [
        ("park_harvesters_seconds_left = 120", "park_harvesters_seconds_left = -1"),
        ("park_distance_cells = 8", "park_distance_cells = 2.5"),
    ],
    ids=["negative seconds", "a fraction of cells"],
)
def test_a_parking_threshold_that_is_not_a_whole_number_of_zero_or_more_is_named(tuning_root, old, new):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=rf"{old.split(' = ')[0]} in \[upkeep\]"):
        Tuning.from_file(tuning_root)


def test_the_field_choice_by_reach_ships_off_with_its_values_and_true_in_the_file_switches_it_on(tuning_root):
    rewrite(tuning_root, "refinery_field_by_reach = false", "refinery_field_by_reach = true")

    placement = Tuning.from_file(REPO_ROOT).placement
    assert placement == PlacementTuning() and placement.refinery_field_by_reach is False
    assert (placement.refinery_ore_cells, placement.max_refineries_per_field) == (2, 1)
    assert placement.shared_field_min_cells == 24
    assert Tuning.from_file(tuning_root).placement == dataclasses.replace(
        PlacementTuning(), refinery_field_by_reach=True
    )


def test_one_per_field_ships_one_refinery_a_field_no_second_on_a_known_map_and_a_refinery_serving_within_5(
    tuning_root,
):
    rewrite(tuning_root, "refinery_second_min_cells = 9999", "refinery_second_min_cells = 80")
    rewrite(tuning_root, "refinery_serves_cells = 5", "refinery_serves_cells = 2")

    for shipped in (PlacementTuning(), Tuning.from_file(REPO_ROOT).placement):
        keys = (shipped.max_refineries_per_field, shipped.refinery_second_min_cells, shipped.refinery_serves_cells)
        assert keys == (1, 9999, 5) and shipped.refinery_unserved_only is True
    changed = dataclasses.replace(PlacementTuning(), refinery_second_min_cells=80, refinery_serves_cells=2)
    assert Tuning.from_file(tuning_root).placement == changed


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("refinery_serves_cells = 5\n", "", r"refinery_serves_cells in \[placement\] must be a whole number"),
        ("refinery_serves_cells = 5", "refinery_serves_cells = 2.5", r"refinery_serves_cells in \[placement\] must"),
        ("refinery_serves_cells = 5", "refinery_serves_cells = -1", r"refinery_serves_cells in \[placement\] must"),
    ],
    ids=["missing", "a fraction", "negative"],
)
def test_one_per_field_a_serves_cells_key_that_is_missing_or_not_a_whole_number_is_named(
    tuning_root, old, new, message
):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_site_army_the_value_and_the_hold_ship_at_600_and_30_built_in_and_in_the_tracked_file(tuning_root):
    rewrite(tuning_root, "mcv_site_army_value = 600", "mcv_site_army_value = 900")

    for shipped in (ExpansionTuning(), Tuning.from_file(REPO_ROOT).expansion):
        assert (shipped.mcv_site_army_value, shipped.mcv_site_hold_seconds) == (600, 30)
    assert Tuning.from_file(tuning_root).expansion == dataclasses.replace(ExpansionTuning(), mcv_site_army_value=900)


def test_one_per_field_the_count_reach_and_the_stuck_second_ship_at_8_and_80_built_in_and_in_the_tracked_file(
    tuning_root,
):
    rewrite(tuning_root, "refinery_second_when_stuck_cells = 80", "refinery_second_when_stuck_cells = 9999")

    for shipped in (PlacementTuning(), Tuning.from_file(REPO_ROOT).placement):
        assert (shipped.refinery_counts_cells, shipped.refinery_second_when_stuck_cells) == (8, 80)
        assert shipped.refinery_second_min_cells == 9999
    changed = dataclasses.replace(PlacementTuning(), refinery_second_when_stuck_cells=9999)
    assert Tuning.from_file(tuning_root).placement == changed


@pytest.mark.parametrize(
    "new",
    ["", "refinery_field_by_reach = 1\n", 'refinery_field_by_reach = "true"\n'],
    ids=["missing", "a number", "text"],
)
def test_a_field_choice_by_reach_switch_that_is_missing_or_not_true_or_false_is_named(tuning_root, new):
    rewrite(tuning_root, "refinery_field_by_reach = false\n", new)

    with pytest.raises(ConfigError, match=r"refinery_field_by_reach in \[placement\] must be true or false"):
        Tuning.from_file(tuning_root)


@pytest.mark.parametrize(
    "old, new",
    [
        ("refinery_ore_cells = 2", "refinery_ore_cells = -2"),
        ("max_refineries_per_field = 1", "max_refineries_per_field = 1.5"),
        ("shared_field_min_cells = 24", "shared_field_min_cells = true"),
        ("shared_field_min_cells = 24\n", ""),
    ],
    ids=["negative cells", "a fraction of refineries", "true for cells", "missing"],
)
def test_a_field_choice_threshold_that_is_not_a_whole_number_of_zero_or_more_is_named(tuning_root, old, new):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=rf"{old.split(' = ')[0]} in \[placement\]"):
        Tuning.from_file(tuning_root)


def test_the_creep_toward_ore_ships_off_and_true_in_the_file_switches_it_on(tuning_root):
    rewrite(tuning_root, "creep_toward_ore = false", "creep_toward_ore = true")

    assert Tuning.from_file(REPO_ROOT).placement.creep_toward_ore is False
    assert Tuning.from_file(tuning_root).placement == dataclasses.replace(PlacementTuning(), creep_toward_ore=True)


@pytest.mark.parametrize(
    "new", ["", "creep_toward_ore = 0\n", 'creep_toward_ore = "false"\n'], ids=["missing", "a number", "text"]
)
def test_a_creep_toward_ore_switch_that_is_missing_or_not_true_or_false_is_named(tuning_root, new):
    rewrite(tuning_root, "creep_toward_ore = false\n", new)

    with pytest.raises(ConfigError, match=r"creep_toward_ore in \[placement\] must be true or false"):
        Tuning.from_file(tuning_root)


def test_lane_the_creep_to_the_next_field_ships_off_with_its_lane_values_and_true_in_the_file_switches_it_on(
    tuning_root,
):
    rewrite(tuning_root, "creep_next_patch = false", "creep_next_patch = true")

    placement = Tuning.from_file(REPO_ROOT).placement
    assert placement.creep_next_patch is False
    assert (placement.creep_lane_cells, placement.creep_max_share, placement.adjacent_patch_cells) == (12, 0.6, 6)
    assert (placement.creep_enemy_clear_cells, placement.creep_defense_clear_cells) == (25, 10)
    assert Tuning.from_file(tuning_root).placement == dataclasses.replace(PlacementTuning(), creep_next_patch=True)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("creep_next_patch = false\n", "", "creep_next_patch in \\[placement\\] must be true or false"),
        (
            "creep_next_patch = false",
            "creep_next_patch = 1",
            "creep_next_patch in \\[placement\\] must be true or false",
        ),
        ("creep_lane_cells = 12", "creep_lane_cells = -1", "creep_lane_cells in \\[placement\\]"),
        ("creep_max_share = 0.6", 'creep_max_share = "most"', "creep_max_share in \\[placement\\]"),
        ("creep_enemy_clear_cells = 25\n", "", "creep_enemy_clear_cells in \\[placement\\]"),
        (
            "creep_defense_clear_cells = 10",
            "creep_defense_clear_cells = 2.5",
            "creep_defense_clear_cells in \\[placement\\]",
        ),
        ("adjacent_patch_cells = 6", "adjacent_patch_cells = true", "adjacent_patch_cells in \\[placement\\]"),
    ],
    ids=["switch missing", "switch a number", "negative lane", "share text", "clear missing", "fraction", "true cells"],
)
def test_lane_a_creep_to_the_next_field_key_that_is_missing_or_of_the_wrong_kind_is_named(
    tuning_root, old, new, message
):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_far_field_the_creep_from_the_opening_ships_on_with_its_line_values_and_false_in_the_file_switches_it_off(
    tuning_root,
):
    rewrite(tuning_root, "creep_from_opening = true", "creep_from_opening = false")

    placement = Tuning.from_file(REPO_ROOT).placement
    assert placement.creep_from_opening is True
    assert (placement.creep_line_cells, placement.creep_far_max_cells) == (2, 30)
    assert Tuning.from_file(tuning_root).placement == dataclasses.replace(PlacementTuning(), creep_from_opening=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("creep_from_opening = true\n", "", "creep_from_opening in \\[placement\\] must be true or false"),
        (
            "creep_from_opening = true",
            'creep_from_opening = "true"',
            "creep_from_opening in \\[placement\\] must be true or false",
        ),
        ("creep_line_cells = 2", "creep_line_cells = -2", "creep_line_cells in \\[placement\\]"),
        ("creep_line_cells = 2\n", "", "creep_line_cells in \\[placement\\]"),
        ("creep_far_max_cells = 30", "creep_far_max_cells = 30.5", "creep_far_max_cells in \\[placement\\]"),
        ("creep_far_max_cells = 30", "creep_far_max_cells = true", "creep_far_max_cells in \\[placement\\]"),
    ],
    ids=["switch missing", "switch text", "negative band", "band missing", "fraction of cells", "true cells"],
)
def test_far_field_a_creep_from_the_opening_key_that_is_missing_or_of_the_wrong_kind_is_named(
    tuning_root, old, new, message
):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_refused_detection_and_mcv_to_skipped_fields_ship_on_with_their_values_and_false_in_the_file_switches_them_off(
    tuning_root,
):
    rewrite(tuning_root, "refused_detection = true", "refused_detection = false")
    rewrite(tuning_root, "mcv_to_skipped_fields = false", "mcv_to_skipped_fields = true")

    for shipped in (Tuning(), Tuning.from_file(REPO_ROOT)):
        placement = shipped.placement
        assert placement.refused_detection is True and shipped.expansion.mcv_to_skipped_fields is False
        assert (placement.refused_miss_cells, placement.refused_misses_to_skip, placement.refused_spots_max) == (
            3,
            2,
            64,
        )
    switched = Tuning.from_file(tuning_root)
    assert switched.placement == dataclasses.replace(PlacementTuning(), refused_detection=False)
    assert switched.expansion == dataclasses.replace(ExpansionTuning(), mcv_to_skipped_fields=True)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("refused_detection = true\n", "", r"refused_detection in \[placement\] must be true or false"),
        (
            "refused_detection = true",
            "refused_detection = 1",
            r"refused_detection in \[placement\] must be true or false",
        ),
        ("refused_miss_cells = 3\n", "", r"refused_miss_cells in \[placement\] must be a whole number"),
        (
            "refused_miss_cells = 3",
            "refused_miss_cells = -3",
            r"refused_miss_cells in \[placement\] must be a whole number",
        ),
        ("refused_misses_to_skip = 2", "refused_misses_to_skip = 1.5", r"refused_misses_to_skip in \[placement\]"),
        ("refused_spots_max = 64", 'refused_spots_max = "64"', r"refused_spots_max in \[placement\]"),
        (
            "mcv_to_skipped_fields = false\n",
            "",
            r"mcv_to_skipped_fields in \[expansion\] must be true or false",
        ),
        (
            "mcv_to_skipped_fields = false",
            'mcv_to_skipped_fields = "false"',
            r"mcv_to_skipped_fields in \[expansion\] must be true or false",
        ),
    ],
    ids=[
        "switch missing",
        "switch a number",
        "miss cells missing",
        "negative miss cells",
        "misses a fraction",
        "spots text",
        "mcv switch missing",
        "mcv switch text",
    ],
)
def test_refused_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_continuous_creep_ships_on_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "creep_continuous = true", "creep_continuous = false")

    assert Tuning().placement.creep_continuous is True
    assert Tuning.from_file(REPO_ROOT).placement.creep_continuous is True
    assert Tuning.from_file(tuning_root).placement == dataclasses.replace(PlacementTuning(), creep_continuous=False)


@pytest.mark.parametrize(
    "old, new",
    [("creep_continuous = true\n", ""), ("creep_continuous = true", 'creep_continuous = "true"')],
    ids=["missing", "text"],
)
def test_continuous_a_creep_continuous_switch_that_is_missing_or_not_true_or_false_is_named(tuning_root, old, new):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=r"creep_continuous in \[placement\] must be true or false"):
        Tuning.from_file(tuning_root)


def test_claim_refinery_claims_ship_on_with_8_refineries_at_most_and_false_in_the_file_switches_them_off(tuning_root):
    rewrite(tuning_root, "refinery_claims_reached = true", "refinery_claims_reached = false")

    for shipped in (Tuning().upkeep, Tuning.from_file(REPO_ROOT).upkeep):
        assert (shipped.refinery_claims_reached, shipped.claim_refineries_max) == (True, 8)
    switched = dataclasses.replace(UpkeepTuning(), refinery_claims_reached=False)
    assert Tuning.from_file(tuning_root).upkeep == switched


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("refinery_claims_reached = true\n", "", r"refinery_claims_reached in \[upkeep\] must be true or false"),
        (
            "refinery_claims_reached = true",
            "refinery_claims_reached = 1",
            r"refinery_claims_reached in \[upkeep\] must be true or false",
        ),
        ("claim_refineries_max = 8\n", "", r"claim_refineries_max in \[upkeep\] must be a whole number"),
        (
            "claim_refineries_max = 8",
            "claim_refineries_max = 8.5",
            r"claim_refineries_max in \[upkeep\] must be a whole number",
        ),
        (
            "claim_refineries_max = 8",
            "claim_refineries_max = -8",
            r"claim_refineries_max in \[upkeep\] must be a whole number",
        ),
    ],
    ids=["switch missing", "switch a number", "most missing", "most a fraction", "most negative"],
)
def test_claim_a_refinery_claims_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_refinery_growth_ships_on_with_8_refineries_and_a_60_s_saving_and_false_in_the_file_switches_it_off(
    tuning_root,
):
    rewrite(tuning_root, "refinery_growth = true", "refinery_growth = false")

    for shipped in (Tuning().upkeep, Tuning.from_file(REPO_ROOT).upkeep):
        assert (shipped.refinery_growth, shipped.growth_refineries_max, shipped.growth_saving_seconds) == (True, 8, 60)
    assert Tuning.from_file(tuning_root).upkeep == dataclasses.replace(UpkeepTuning(), refinery_growth=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("refinery_growth = true\n", "", r"refinery_growth in \[upkeep\] must be true or false"),
        ("refinery_growth = true", 'refinery_growth = "on"', r"refinery_growth in \[upkeep\] must be true or false"),
        ("growth_refineries_max = 8\n", "", r"growth_refineries_max in \[upkeep\] must be a whole number"),
        (
            "growth_refineries_max = 8",
            "growth_refineries_max = 8.5",
            r"growth_refineries_max in \[upkeep\] must be a whole number",
        ),
        (
            "growth_saving_seconds = 60",
            "growth_saving_seconds = -60",
            r"growth_saving_seconds in \[upkeep\] must be a whole number",
        ),
        ("growth_saving_seconds = 60\n", "", r"growth_saving_seconds in \[upkeep\] must be a whole number"),
    ],
    ids=["switch missing", "switch text", "most missing", "most a fraction", "saving negative", "saving missing"],
)
def test_refinery_growth_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_harvester_target_ships_2_a_refinery_up_to_22_with_10_cells_a_harvester_and_harvesters_first_on(tuning_root):
    rewrite(tuning_root, "harvesters_first = true", "harvesters_first = false")
    rewrite(tuning_root, "harvester_cells_per = 10", "harvester_cells_per = 0")

    for shipped in (Tuning().upkeep, Tuning.from_file(REPO_ROOT).upkeep):
        harvester_keys = (shipped.harvesters_per_refinery, shipped.harvesters_max, shipped.harvester_cells_per)
        assert harvester_keys == (2, 22, 10) and shipped.harvesters_first is True
        assert shipped.field_share_per_refinery == 2
    switched = dataclasses.replace(UpkeepTuning(), harvesters_first=False, harvester_cells_per=0)
    assert Tuning.from_file(tuning_root).upkeep == switched


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("harvesters_first = true\n", "", r"harvesters_first in \[upkeep\] must be true or false"),
        ("harvesters_first = true", "harvesters_first = 1", r"harvesters_first in \[upkeep\] must be true or false"),
        ("harvester_cells_per = 10\n", "", r"harvester_cells_per in \[upkeep\] must be a whole number"),
        ("harvester_cells_per = 10", "harvester_cells_per = 2.5", r"harvester_cells_per in \[upkeep\] must be a whole"),
        ("harvester_cells_per = 10", "harvester_cells_per = -1", r"harvester_cells_per in \[upkeep\] must be a whole"),
    ],
    ids=["switch missing", "switch a number", "cells missing", "cells a fraction", "cells negative"],
)
def test_harvester_target_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_rebalance_ships_one_move_in_30_s_off_a_field_more_than_1_over_its_share(tuning_root):
    rewrite(tuning_root, "harvester_rebalance_seconds = 30", "harvester_rebalance_seconds = 10")
    rewrite(tuning_root, "harvester_rebalance_excess = 1", "harvester_rebalance_excess = 0")

    for shipped in (Tuning().upkeep, Tuning.from_file(REPO_ROOT).upkeep):
        assert (shipped.harvester_rebalance_seconds, shipped.harvester_rebalance_excess) == (30, 1)
    changed = dataclasses.replace(UpkeepTuning(), harvester_rebalance_seconds=10, harvester_rebalance_excess=0)
    assert Tuning.from_file(tuning_root).upkeep == changed


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("harvester_rebalance_seconds = 30\n", "", r"harvester_rebalance_seconds in \[upkeep\] must be a whole"),
        ("harvester_rebalance_seconds = 30", "harvester_rebalance_seconds = 0.5", r"harvester_rebalance_seconds in"),
        ("harvester_rebalance_excess = 1", "harvester_rebalance_excess = -1", r"harvester_rebalance_excess in"),
    ],
    ids=["seconds missing", "seconds a fraction", "excess negative"],
)
def test_rebalance_a_key_that_is_missing_or_not_a_whole_number_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_harvester_priority_ships_99_a_refinery_first_a_6000_floor_and_a_refinery_worked_at_2(tuning_root):
    rewrite(tuning_root, "harvesters_first_per_refinery = 99", "harvesters_first_per_refinery = 2")
    rewrite(tuning_root, "combat_value_floor = 6000", "combat_value_floor = 0")
    rewrite(tuning_root, "refinery_worked_harvesters = 2", "refinery_worked_harvesters = 3")

    for shipped in (Tuning().upkeep, Tuning.from_file(REPO_ROOT).upkeep):
        keys = (shipped.harvesters_first_per_refinery, shipped.combat_value_floor, shipped.refinery_worked_harvesters)
        assert keys == (99, 6000, 2) and shipped.harvesters_first is True
    changed = dataclasses.replace(
        UpkeepTuning(), harvesters_first_per_refinery=2, combat_value_floor=0, refinery_worked_harvesters=3
    )
    assert Tuning.from_file(tuning_root).upkeep == changed


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("harvesters_first_per_refinery = 99\n", "", r"harvesters_first_per_refinery in \[upkeep\] must be a whole"),
        ("combat_value_floor = 6000", "combat_value_floor = 6000.5", r"combat_value_floor in \[upkeep\] must be a"),
        ("combat_value_floor = 6000", "combat_value_floor = true", r"combat_value_floor in \[upkeep\] must be a whole"),
        ("refinery_worked_harvesters = 2\n", "", r"refinery_worked_harvesters in \[upkeep\] must be a whole number"),
        ("refinery_worked_harvesters = 2", "refinery_worked_harvesters = -3", r"refinery_worked_harvesters in \[upk"),
    ],
    ids=["first missing", "floor a fraction", "floor a switch", "worked missing", "worked negative"],
)
def test_harvesters_alternate_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_no_army_infantry_ships_on_keeping_4_rockets_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "no_army_infantry = true", "no_army_infantry = false")

    for shipped in (Tuning().upkeep, Tuning.from_file(REPO_ROOT).upkeep):
        assert (shipped.no_army_infantry, shipped.rockets_kept) == (True, 4)
    switched = dataclasses.replace(UpkeepTuning(), no_army_infantry=False)
    assert Tuning.from_file(tuning_root).upkeep == switched


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("no_army_infantry = true\n", "", r"no_army_infantry in \[upkeep\] must be true or false"),
        (
            "no_army_infantry = true",
            'no_army_infantry = "no"',
            r"no_army_infantry in \[upkeep\] must be true or false",
        ),
        ("rockets_kept = 4\n", "", r"rockets_kept in \[upkeep\] must be a whole number"),
        ("rockets_kept = 4", "rockets_kept = 4.5", r"rockets_kept in \[upkeep\] must be a whole number"),
        ("rockets_kept = 4", "rockets_kept = -1", r"rockets_kept in \[upkeep\] must be a whole number"),
    ],
    ids=["switch missing", "switch text", "rockets missing", "rockets a fraction", "rockets negative"],
)
def test_no_army_infantry_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_economy_first_and_lean_power_ship_on_with_their_built_in_values_and_false_in_the_file_switches_them_off(
    tuning_root,
):
    rewrite(tuning_root, "economy_first = true", "economy_first = false")
    rewrite(tuning_root, "lean_power = true", "lean_power = false")

    for shipped in (Tuning().upkeep, Tuning.from_file(REPO_ROOT).upkeep):
        assert (shipped.economy_first, shipped.economy_wait_seconds, shipped.economy_release_seconds) == (True, 20, 20)
        assert (shipped.lean_power, shipped.headroom_share) == (True, 0.15)
    switched = dataclasses.replace(UpkeepTuning(), economy_first=False, lean_power=False)
    assert Tuning.from_file(tuning_root).upkeep == switched


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("economy_first = true\n", "", r"economy_first in \[upkeep\] must be true or false"),
        ("economy_first = true", "economy_first = 0", r"economy_first in \[upkeep\] must be true or false"),
        ("economy_wait_seconds = 20\n", "", r"economy_wait_seconds in \[upkeep\] must be a whole number"),
        (
            "economy_wait_seconds = 20",
            "economy_wait_seconds = 20.5",
            r"economy_wait_seconds in \[upkeep\] must be a whole number",
        ),
        (
            "economy_release_seconds = 20",
            "economy_release_seconds = -20",
            r"economy_release_seconds in \[upkeep\] must be a whole number",
        ),
        ("lean_power = true\n", "", r"lean_power in \[upkeep\] must be true or false"),
        ("lean_power = true", 'lean_power = "false"', r"lean_power in \[upkeep\] must be true or false"),
        ("headroom_share = 0.15\n", "", r"headroom_share in \[upkeep\] must be a number of at least 0"),
        (
            "headroom_share = 0.15",
            "headroom_share = -0.15",
            r"headroom_share in \[upkeep\] must be a number of at least 0",
        ),
        (
            "headroom_share = 0.15",
            'headroom_share = "15%"',
            r"headroom_share in \[upkeep\] must be a number of at least 0",
        ),
    ],
    ids=[
        "economy switch missing",
        "economy switch a number",
        "wait missing",
        "wait a fraction",
        "release negative",
        "lean switch missing",
        "lean switch text",
        "share missing",
        "share negative",
        "share text",
    ],
)
def test_economy_first_or_lean_power_a_key_that_is_missing_or_of_the_wrong_kind_is_named(
    tuning_root, old, new, message
):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_mcv_safe_site_ships_on_with_30_cells_1500_ticks_and_3_cells_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "mcv_safe_site = true", "mcv_safe_site = false")

    for shipped in (Tuning().expansion, Tuning.from_file(REPO_ROOT).expansion):
        assert shipped.mcv_safe_site is True
        assert (shipped.mcv_site_max_cells, shipped.mcv_enemy_seen_ticks, shipped.mcv_danger_margin_cells) == (
            30,
            1500,
            3,
        )
    assert Tuning.from_file(tuning_root).expansion == dataclasses.replace(ExpansionTuning(), mcv_safe_site=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("mcv_safe_site = true\n", "", r"mcv_safe_site in \[expansion\] must be true or false"),
        ("mcv_safe_site = true", "mcv_safe_site = 1", r"mcv_safe_site in \[expansion\] must be true or false"),
        ("mcv_site_max_cells = 30\n", "", r"mcv_site_max_cells in \[expansion\] must be a whole number"),
        (
            "mcv_site_max_cells = 30",
            "mcv_site_max_cells = 30.5",
            r"mcv_site_max_cells in \[expansion\] must be a whole number",
        ),
        (
            "mcv_enemy_seen_ticks = 1500",
            "mcv_enemy_seen_ticks = -1",
            r"mcv_enemy_seen_ticks in \[expansion\] must be a whole number",
        ),
        (
            "mcv_danger_margin_cells = 3",
            'mcv_danger_margin_cells = "3"',
            r"mcv_danger_margin_cells in \[expansion\] must be a whole number",
        ),
    ],
    ids=["switch missing", "switch a number", "cells missing", "cells a fraction", "ticks negative", "margin text"],
)
def test_mcv_safe_site_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_mcv_new_area_ships_on_with_12_cells_from_every_refinery_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "mcv_new_area = true", "mcv_new_area = false")

    for shipped in (Tuning().expansion, Tuning.from_file(REPO_ROOT).expansion):
        assert (shipped.mcv_new_area, shipped.mcv_refinery_spacing_cells) == (True, 12)
    assert Tuning.from_file(tuning_root).expansion == dataclasses.replace(ExpansionTuning(), mcv_new_area=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("mcv_new_area = true\n", "", r"mcv_new_area in \[expansion\] must be true or false"),
        ("mcv_new_area = true", "mcv_new_area = 0", r"mcv_new_area in \[expansion\] must be true or false"),
        (
            "mcv_refinery_spacing_cells = 12\n",
            "",
            r"mcv_refinery_spacing_cells in \[expansion\] must be a whole number",
        ),
        (
            "mcv_refinery_spacing_cells = 12",
            "mcv_refinery_spacing_cells = 12.5",
            r"mcv_refinery_spacing_cells in \[expansion\] must be a whole number",
        ),
        (
            "mcv_refinery_spacing_cells = 12",
            "mcv_refinery_spacing_cells = -12",
            r"mcv_refinery_spacing_cells in \[expansion\] must be a whole number",
        ),
    ],
    ids=["switch missing", "switch a number", "spacing missing", "spacing a fraction", "spacing negative"],
)
def test_mcv_new_area_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_lasting_field_mcv_site_min_ore_cells_ships_30_and_the_file_s_value_is_read(tuning_root):
    rewrite(tuning_root, "mcv_site_min_ore_cells = 30", "mcv_site_min_ore_cells = 60")

    for shipped in (Tuning().expansion, Tuning.from_file(REPO_ROOT).expansion):
        assert shipped.mcv_site_min_ore_cells == 30
    assert Tuning.from_file(tuning_root).expansion == dataclasses.replace(ExpansionTuning(), mcv_site_min_ore_cells=60)


@pytest.mark.parametrize(
    "old, new",
    [
        ("mcv_site_min_ore_cells = 30\n", ""),
        ("mcv_site_min_ore_cells = 30", "mcv_site_min_ore_cells = 30.5"),
        ("mcv_site_min_ore_cells = 30", "mcv_site_min_ore_cells = -30"),
        ("mcv_site_min_ore_cells = 30", 'mcv_site_min_ore_cells = "30"'),
    ],
    ids=["missing", "a fraction", "negative", "text"],
)
def test_lasting_field_mcv_site_min_ore_cells_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=r"mcv_site_min_ore_cells in \[expansion\] must be a whole number"):
        Tuning.from_file(tuning_root)


def test_mcv_reserve_field_ships_on_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "mcv_reserve_field = true", "mcv_reserve_field = false")

    for shipped in (Tuning().expansion, Tuning.from_file(REPO_ROOT).expansion):
        assert shipped.mcv_reserve_field is True
    assert Tuning.from_file(tuning_root).expansion == dataclasses.replace(ExpansionTuning(), mcv_reserve_field=False)


@pytest.mark.parametrize(
    "old, new",
    [("mcv_reserve_field = true\n", ""), ("mcv_reserve_field = true", 'mcv_reserve_field = "false"')],
    ids=["switch missing", "switch text"],
)
def test_mcv_reserve_field_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=r"mcv_reserve_field in \[expansion\] must be true or false"):
        Tuning.from_file(tuning_root)


def test_hold_refinery_for_mcv_ships_on_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "hold_refinery_for_mcv = true", "hold_refinery_for_mcv = false")

    for shipped in (Tuning().expansion, Tuning.from_file(REPO_ROOT).expansion):
        assert shipped.hold_refinery_for_mcv is True
    off = dataclasses.replace(ExpansionTuning(), hold_refinery_for_mcv=False)
    assert Tuning.from_file(tuning_root).expansion == off


@pytest.mark.parametrize(
    "old, new",
    [("hold_refinery_for_mcv = true\n", ""), ("hold_refinery_for_mcv = true", 'hold_refinery_for_mcv = "false"')],
    ids=["switch missing", "switch text"],
)
def test_hold_refinery_for_mcv_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=r"hold_refinery_for_mcv in \[expansion\] must be true or false"):
        Tuning.from_file(tuning_root)


def test_second_mcv_waits_ships_on_with_90_s_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "second_mcv_waits = true", "second_mcv_waits = false")

    for shipped in (Tuning().expansion, Tuning.from_file(REPO_ROOT).expansion):
        assert (shipped.second_mcv_waits, shipped.second_mcv_wait_seconds) == (True, 90)
    off = dataclasses.replace(ExpansionTuning(), second_mcv_waits=False)
    assert Tuning.from_file(tuning_root).expansion == off


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("second_mcv_waits = true\n", "", r"second_mcv_waits in \[expansion\] must be true or false"),
        ("second_mcv_wait_seconds = 90\n", "", r"second_mcv_wait_seconds in \[expansion\]"),
        ("second_mcv_wait_seconds = 90", "second_mcv_wait_seconds = 9.5", r"second_mcv_wait_seconds in \[expansion\]"),
    ],
    ids=["switch missing", "seconds missing", "fraction of seconds"],
)
def test_second_mcv_waits_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_mcv_short_escort_ships_on_with_500_ticks_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "mcv_short_escort = true", "mcv_short_escort = false")

    for shipped in (Tuning().expansion, Tuning.from_file(REPO_ROOT).expansion):
        assert (shipped.mcv_short_escort, shipped.short_escort_wait_ticks) == (True, 500)
    assert Tuning.from_file(tuning_root).expansion == dataclasses.replace(ExpansionTuning(), mcv_short_escort=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("mcv_short_escort = true\n", "", r"mcv_short_escort in \[expansion\] must be true or false"),
        (
            "mcv_short_escort = true",
            "mcv_short_escort = 1",
            r"mcv_short_escort in \[expansion\] must be true or false",
        ),
        ("short_escort_wait_ticks = 500\n", "", r"short_escort_wait_ticks in \[expansion\] must be a whole number"),
        (
            "short_escort_wait_ticks = 500",
            "short_escort_wait_ticks = 500.5",
            r"short_escort_wait_ticks in \[expansion\] must be a whole number",
        ),
        (
            "short_escort_wait_ticks = 500",
            "short_escort_wait_ticks = -500",
            r"short_escort_wait_ticks in \[expansion\] must be a whole number",
        ),
    ],
    ids=["switch missing", "switch a number", "wait missing", "wait a fraction", "wait negative"],
)
def test_mcv_short_escort_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_mcv_repeat_ships_on_with_6_yards_and_a_180_s_cutoff_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "mcv_repeat = true", "mcv_repeat = false")

    for shipped in (Tuning().expansion, Tuning.from_file(REPO_ROOT).expansion):
        assert (shipped.mcv_repeat, shipped.max_yards, shipped.repeat_mcv_cutoff_seconds) == (True, 6, 180)
    assert Tuning.from_file(tuning_root).expansion == dataclasses.replace(ExpansionTuning(), mcv_repeat=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("mcv_repeat = true\n", "", r"mcv_repeat in \[expansion\] must be true or false"),
        ("mcv_repeat = true", 'mcv_repeat = "off"', r"mcv_repeat in \[expansion\] must be true or false"),
        ("max_yards = 6\n", "", r"max_yards in \[expansion\] must be a whole number"),
        ("max_yards = 6", "max_yards = 6.5", r"max_yards in \[expansion\] must be a whole number"),
        (
            "repeat_mcv_cutoff_seconds = 180",
            "repeat_mcv_cutoff_seconds = -180",
            r"repeat_mcv_cutoff_seconds in \[expansion\] must be a whole number",
        ),
    ],
    ids=["switch missing", "switch text", "yards missing", "yards a fraction", "cutoff negative"],
)
def test_mcv_repeat_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_far_fields_yards_income_per_yard_ships_40_in_the_built_ins_and_the_file_and_is_read(tuning_root):
    rewrite(tuning_root, "yards_income_per_yard = 40", "yards_income_per_yard = 60")

    for shipped in (Tuning().expansion, Tuning.from_file(REPO_ROOT).expansion):
        assert (shipped.yards_income_per_yard, shipped.max_yards) == (40, 6)
    assert Tuning.from_file(tuning_root).expansion == dataclasses.replace(ExpansionTuning(), yards_income_per_yard=60)


@pytest.mark.parametrize(
    "old, new",
    [("yards_income_per_yard = 40\n", ""), ("yards_income_per_yard = 40", "yards_income_per_yard = 40.5")],
    ids=["missing", "a fraction"],
)
def test_far_fields_yards_income_per_yard_missing_or_a_fraction_is_named(tuning_root, old, new):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=r"yards_income_per_yard in \[expansion\] must be a whole number"):
        Tuning.from_file(tuning_root)


def test_far_fields_mcv_far_site_max_cells_ships_50_in_the_built_ins_and_the_file_and_is_read(tuning_root):
    rewrite(tuning_root, "mcv_far_site_max_cells = 50", "mcv_far_site_max_cells = 40")

    for shipped in (Tuning().expansion, Tuning.from_file(REPO_ROOT).expansion):
        assert shipped.mcv_far_site_max_cells == 50
    assert Tuning.from_file(tuning_root).expansion == dataclasses.replace(ExpansionTuning(), mcv_far_site_max_cells=40)


@pytest.mark.parametrize(
    "old, new",
    [("mcv_far_site_max_cells = 50\n", ""), ("mcv_far_site_max_cells = 50", "mcv_far_site_max_cells = 50.5")],
    ids=["missing", "a fraction"],
)
def test_far_fields_mcv_far_site_max_cells_missing_or_a_fraction_is_named(tuning_root, old, new):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=r"mcv_far_site_max_cells in \[expansion\] must be a whole number"):
        Tuning.from_file(tuning_root)


def test_mcv_first_ships_on_waiting_30_s_at_most_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "mcv_first = true", "mcv_first = false")

    for shipped in (Tuning().upkeep, Tuning.from_file(REPO_ROOT).upkeep):
        assert (shipped.mcv_first, shipped.mcv_first_wait_seconds) == (True, 30)
    assert Tuning.from_file(tuning_root).upkeep == dataclasses.replace(UpkeepTuning(), mcv_first=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("mcv_first = true\n", "", r"mcv_first in \[upkeep\] must be true or false"),
        ("mcv_first = true", 'mcv_first = "false"', r"mcv_first in \[upkeep\] must be true or false"),
        ("mcv_first_wait_seconds = 30\n", "", r"mcv_first_wait_seconds in \[upkeep\] must be a whole number"),
        (
            "mcv_first_wait_seconds = 30",
            "mcv_first_wait_seconds = 30.5",
            r"mcv_first_wait_seconds in \[upkeep\] must be a whole number",
        ),
        (
            "mcv_first_wait_seconds = 30",
            "mcv_first_wait_seconds = -30",
            r"mcv_first_wait_seconds in \[upkeep\] must be a whole number",
        ),
    ],
    ids=["switch missing", "switch text", "wait missing", "wait a fraction", "wait negative"],
)
def test_mcv_first_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_remote_yard_growth_ships_on_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "remote_yard_growth = true", "remote_yard_growth = false")

    assert Tuning().placement.remote_yard_growth is True
    assert Tuning.from_file(REPO_ROOT).placement.remote_yard_growth is True
    assert Tuning.from_file(tuning_root).placement == dataclasses.replace(PlacementTuning(), remote_yard_growth=False)


@pytest.mark.parametrize(
    "old, new",
    [("remote_yard_growth = true\n", ""), ("remote_yard_growth = true", 'remote_yard_growth = "true"')],
    ids=["missing", "text"],
)
def test_remote_yard_a_growth_switch_that_is_missing_or_not_true_or_false_is_named(tuning_root, old, new):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=r"remote_yard_growth in \[placement\] must be true or false"):
        Tuning.from_file(tuning_root)


def test_refinery_spread_ships_on_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "refinery_spread = true", "refinery_spread = false")

    assert Tuning().placement.refinery_spread is True
    assert Tuning.from_file(REPO_ROOT).placement.refinery_spread is True
    assert Tuning.from_file(tuning_root).placement == dataclasses.replace(PlacementTuning(), refinery_spread=False)


@pytest.mark.parametrize(
    "old, new",
    [("refinery_spread = true\n", ""), ("refinery_spread = true", "refinery_spread = 1")],
    ids=["missing", "a number"],
)
def test_refinery_spread_a_switch_that_is_missing_or_not_true_or_false_is_named(tuning_root, old, new):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=r"refinery_spread in \[placement\] must be true or false"):
        Tuning.from_file(tuning_root)


def test_creep_stall_skip_ships_on_with_3_placements_2_cells_and_120_s_and_false_in_the_file_switches_it_off(
    tuning_root,
):
    rewrite(tuning_root, "creep_stall_skip = true", "creep_stall_skip = false")

    for shipped in (Tuning().placement, Tuning.from_file(REPO_ROOT).placement):
        stall = (
            shipped.creep_stall_skip,
            shipped.stall_placements,
            shipped.stall_progress_cells,
            shipped.stall_seconds,
        )
        assert stall == (True, 3, 2, 120)
    assert Tuning.from_file(tuning_root).placement == dataclasses.replace(PlacementTuning(), creep_stall_skip=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("creep_stall_skip = true\n", "", r"creep_stall_skip in \[placement\] must be true or false"),
        (
            "creep_stall_skip = true",
            "creep_stall_skip = 1",
            r"creep_stall_skip in \[placement\] must be true or false",
        ),
        ("stall_placements = 3\n", "", r"stall_placements in \[placement\] must be a whole number"),
        ("stall_placements = 3", "stall_placements = 3.5", r"stall_placements in \[placement\] must be a whole number"),
        (
            "stall_progress_cells = 2",
            "stall_progress_cells = -2",
            r"stall_progress_cells in \[placement\] must be a whole number",
        ),
        ("stall_seconds = 120", 'stall_seconds = "120"', r"stall_seconds in \[placement\] must be a whole number"),
    ],
    ids=["switch missing", "switch a number", "placements missing", "placements a fraction", "cells negative", "text"],
)
def test_creep_stall_skip_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_second_line_local_ships_on_with_15_cells_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "second_line_local = true", "second_line_local = false")

    for shipped in (Tuning().placement, Tuning.from_file(REPO_ROOT).placement):
        assert (shipped.second_line_local, shipped.second_line_radius_cells) == (True, 15)
    assert Tuning.from_file(tuning_root).placement == dataclasses.replace(PlacementTuning(), second_line_local=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("second_line_local = true\n", "", r"second_line_local in \[placement\] must be true or false"),
        (
            "second_line_local = true",
            'second_line_local = "false"',
            r"second_line_local in \[placement\] must be true or false",
        ),
        ("second_line_radius_cells = 15\n", "", r"second_line_radius_cells in \[placement\] must be a whole number"),
        (
            "second_line_radius_cells = 15",
            "second_line_radius_cells = 15.5",
            r"second_line_radius_cells in \[placement\] must be a whole number",
        ),
        (
            "second_line_radius_cells = 15",
            "second_line_radius_cells = -15",
            r"second_line_radius_cells in \[placement\] must be a whole number",
        ),
    ],
    ids=["switch missing", "switch text", "radius missing", "radius a fraction", "radius negative"],
)
def test_second_line_local_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_walked_route_ships_off_with_60_s_a_detour_of_2_and_2_cell_points_and_true_in_the_file_switches_it_on(
    tuning_root,
):
    rewrite(tuning_root, "walked_routes = false", "walked_routes = true")

    for shipped in (Tuning().placement, Tuning.from_file(REPO_ROOT).placement):
        walk = (
            shipped.walked_routes,
            shipped.walk_timeout_seconds,
            shipped.walk_detour_factor,
            shipped.walk_sample_cells,
        )
        assert walk == (False, 60, 2.0, 2)
    assert Tuning.from_file(tuning_root).placement == dataclasses.replace(PlacementTuning(), walked_routes=True)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("walked_routes = false\n", "", r"walked_routes in \[placement\] must be true or false"),
        ("walked_routes = false", "walked_routes = 0", r"walked_routes in \[placement\] must be true or false"),
        ("walk_timeout_seconds = 60\n", "", r"walk_timeout_seconds in \[placement\] must be a whole number"),
        (
            "walk_timeout_seconds = 60",
            "walk_timeout_seconds = 60.5",
            r"walk_timeout_seconds in \[placement\] must be a whole number",
        ),
        (
            "walk_detour_factor = 2.0",
            "walk_detour_factor = -2.0",
            r"walk_detour_factor in \[placement\] must be a number of at least 0",
        ),
        (
            "walk_detour_factor = 2.0",
            'walk_detour_factor = "2"',
            r"walk_detour_factor in \[placement\] must be a number of at least 0",
        ),
        (
            "walk_sample_cells = 2",
            "walk_sample_cells = -2",
            r"walk_sample_cells in \[placement\] must be a whole number",
        ),
    ],
    ids=[
        "switch missing",
        "switch a number",
        "timeout missing",
        "timeout a fraction",
        "detour negative",
        "text",
        "cells",
    ],
)
def test_walked_route_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_held_cancel_a_finished_building_is_cancelled_after_500_ticks_and_the_file_s_value_is_read(tuning_root):
    rewrite(tuning_root, "held_cancel_ticks = 500", "held_cancel_ticks = 250")

    assert Tuning().placement.held_cancel_ticks == Tuning.from_file(REPO_ROOT).placement.held_cancel_ticks == 500
    assert Tuning.from_file(tuning_root).placement == dataclasses.replace(PlacementTuning(), held_cancel_ticks=250)


@pytest.mark.parametrize(
    "new", ["", "held_cancel_ticks = 500.5\n", "held_cancel_ticks = -1\n"], ids=["missing", "a fraction", "negative"]
)
def test_held_cancel_a_key_that_is_missing_or_not_a_whole_number_of_zero_or_more_is_named(tuning_root, new):
    rewrite(tuning_root, "held_cancel_ticks = 500\n", new)

    with pytest.raises(ConfigError, match=r"held_cancel_ticks in \[placement\] must be a whole number"):
        Tuning.from_file(tuning_root)


def test_walker_safety_2_deaths_60_s_and_12_cells_ship_and_the_file_s_values_are_read(tuning_root):
    rewrite(tuning_root, "walker_deaths_to_skip = 2", "walker_deaths_to_skip = 3")

    for shipped in (Tuning().placement, Tuning.from_file(REPO_ROOT).placement):
        safety = (shipped.walker_deaths_to_skip, shipped.walker_enemy_seen_seconds, shipped.walker_enemy_refinery_cells)
        assert safety == (2, 60, 12)
    assert Tuning.from_file(tuning_root).placement == dataclasses.replace(PlacementTuning(), walker_deaths_to_skip=3)


@pytest.mark.parametrize(
    "old, new",
    [
        ("walker_deaths_to_skip = 2\n", ""),
        ("walker_enemy_seen_seconds = 60", "walker_enemy_seen_seconds = 60.5"),
        ("walker_enemy_refinery_cells = 12", "walker_enemy_refinery_cells = -12"),
    ],
    ids=["deaths missing", "seconds a fraction", "cells negative"],
)
def test_walker_safety_a_key_that_is_missing_or_not_a_whole_number_of_zero_or_more_is_named(tuning_root, old, new):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=rf"{old.split()[0]} in \[placement\] must be a whole number"):
        Tuning.from_file(tuning_root)


def test_field_cap_the_cap_of_refineries_to_a_field_ships_on_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "refinery_field_cap = true", "refinery_field_cap = false")

    assert Tuning.from_file(REPO_ROOT).upkeep.refinery_field_cap is True
    assert Tuning.from_file(tuning_root).upkeep == dataclasses.replace(UpkeepTuning(), refinery_field_cap=False)


@pytest.mark.parametrize(
    "new", ["", "refinery_field_cap = 2\n", 'refinery_field_cap = "on"\n'], ids=["missing", "a number", "text"]
)
def test_field_cap_a_switch_that_is_missing_or_not_true_or_false_is_named(tuning_root, new):
    rewrite(tuning_root, "refinery_field_cap = true\n", new)

    with pytest.raises(ConfigError, match=r"refinery_field_cap in \[upkeep\] must be true or false"):
        Tuning.from_file(tuning_root)


def test_fresh_ore_the_move_to_fresh_ore_ships_on_with_its_values_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "harvesters_to_fresh_ore = true", "harvesters_to_fresh_ore = false")

    upkeep = Tuning.from_file(REPO_ROOT).upkeep
    assert upkeep.harvesters_to_fresh_ore is True
    assert (upkeep.dry_idle_count, upkeep.dry_window_seconds, upkeep.dry_near_cells) == (2, 60, 8)
    assert (upkeep.fresh_ore_enemy_clear_cells, upkeep.fresh_ore_defense_clear_cells) == (12, 10)
    assert Tuning.from_file(tuning_root).upkeep == dataclasses.replace(UpkeepTuning(), harvesters_to_fresh_ore=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("harvesters_to_fresh_ore = true\n", "", "harvesters_to_fresh_ore in \\[upkeep\\] must be true or false"),
        ("harvesters_to_fresh_ore = true", "harvesters_to_fresh_ore = 0", "harvesters_to_fresh_ore in \\[upkeep\\]"),
        ("dry_idle_count = 2", "dry_idle_count = -2", "dry_idle_count in \\[upkeep\\]"),
        ("dry_window_seconds = 60\n", "", "dry_window_seconds in \\[upkeep\\]"),
        ("dry_near_cells = 8", "dry_near_cells = 8.5", "dry_near_cells in \\[upkeep\\]"),
        ("fresh_ore_enemy_clear_cells = 12", 'fresh_ore_enemy_clear_cells = "far"', "fresh_ore_enemy_clear_cells"),
        ("fresh_ore_defense_clear_cells = 10", "fresh_ore_defense_clear_cells = true", "fresh_ore_defense_clear_cells"),
    ],
    ids=["switch missing", "switch a number", "negative count", "window missing", "fraction", "text", "true cells"],
)
def test_fresh_ore_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_fresh_ore_by_load_ships_on_with_a_share_of_2_a_refinery_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "fresh_ore_by_load = true", "fresh_ore_by_load = false")

    for shipped in (Tuning().upkeep, Tuning.from_file(REPO_ROOT).upkeep):
        assert (shipped.fresh_ore_by_load, shipped.field_share_per_refinery) == (True, 2)
    assert Tuning.from_file(tuning_root).upkeep == dataclasses.replace(UpkeepTuning(), fresh_ore_by_load=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("fresh_ore_by_load = true\n", "", r"fresh_ore_by_load in \[upkeep\] must be true or false"),
        (
            "fresh_ore_by_load = true",
            "fresh_ore_by_load = 1",
            r"fresh_ore_by_load in \[upkeep\] must be true or false",
        ),
        ("field_share_per_refinery = 2\n", "", r"field_share_per_refinery in \[upkeep\] must be a whole number"),
        (
            "field_share_per_refinery = 2",
            "field_share_per_refinery = 2.5",
            r"field_share_per_refinery in \[upkeep\] must be a whole number",
        ),
        (
            "field_share_per_refinery = 2",
            "field_share_per_refinery = -2",
            r"field_share_per_refinery in \[upkeep\] must be a whole number",
        ),
    ],
    ids=["switch missing", "switch a number", "share missing", "share a fraction", "share negative"],
)
def test_fresh_ore_by_load_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_second_yard_the_expansion_table_ships_on_with_its_values_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "second_yard = true", "second_yard = false")

    expansion = Tuning.from_file(REPO_ROOT).expansion
    assert expansion == ExpansionTuning() and expansion.second_yard is True
    assert (expansion.second_yard_from_seconds, expansion.second_yard_min_cash) == (240, 2000)
    assert (expansion.mcv_escort_strength, expansion.mcv_deploy_cells, expansion.mcv_wait_ticks) == (1500, 4, 1500)
    assert Tuning.from_file(tuning_root).expansion == ExpansionTuning(second_yard=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("second_yard = true\n", "", "second_yard in \\[expansion\\] must be true or false"),
        ("second_yard = true", 'second_yard = "yes"', "second_yard in \\[expansion\\] must be true or false"),
        ("second_yard_from_seconds = 240", "second_yard_from_seconds = -1", "second_yard_from_seconds in"),
        ("second_yard_min_cash = 2000\n", "", "second_yard_min_cash in \\[expansion\\]"),
        ("mcv_escort_strength = 1500", "mcv_escort_strength = 1500.5", "mcv_escort_strength in \\[expansion\\]"),
        ("mcv_deploy_cells = 4", "mcv_deploy_cells = true", "mcv_deploy_cells in \\[expansion\\]"),
        ("mcv_wait_ticks = 1500", 'mcv_wait_ticks = "long"', "mcv_wait_ticks in \\[expansion\\]"),
        ("mcv_wait_ticks = 1500", "mcv_wait_ticks = 1500\nmcv_speed = 2", "mcv_speed"),
    ],
    ids=["switch missing", "switch text", "negative", "missing", "fraction", "true cells", "text", "unknown key"],
)
def test_second_yard_a_key_that_is_missing_unknown_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_second_yard_a_file_without_the_expansion_table_is_a_config_error_that_names_it(tuning_root):
    path = tuning_root / TUNING_FILE
    text = path.read_text(encoding="utf-8")
    start, end = text.index("[expansion]"), text.index("[composition]")
    path.write_text(text[:start] + text[end:], encoding="utf-8")

    with pytest.raises(ConfigError, match=r"must have a \[expansion\] table"):
        Tuning.from_file(tuning_root)


def test_tank_factories_the_switch_ships_on_with_its_values_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "tank_factories = true", "tank_factories = false")

    producers = Tuning.from_file(REPO_ROOT).producers
    assert producers == ProducersTuning() and producers.tank_factories is True
    assert producers.tank_factories_income == (90, 120, 150)
    assert (producers.tank_factories_busy_share, producers.tank_factories_from_seconds) == (0.6, 300)
    assert producers.tank_factories_queued == 2
    assert Tuning.from_file(tuning_root).producers == ProducersTuning(tank_factories=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("tank_factories = true\n", "", r"tank_factories in \[producers\] must be true or false"),
        ("tank_factories = true", "tank_factories = 1", r"tank_factories in \[producers\] must be true or false"),
        ("tank_factories_income = [90, 120, 150]\n", "", r"tank_factories_income in \[producers\]"),
        ("tank_factories_income = [90, 120, 150]", "tank_factories_income = [90, -1]", "a list of whole numbers"),
        ("tank_factories_busy_share = 0.6", 'tank_factories_busy_share = "most"', "tank_factories_busy_share in"),
        ("tank_factories_busy_share = 0.6", "tank_factories_busy_share = -0.6", "a number of at least 0"),
        ("tank_factories_from_seconds = 300\n", "", r"tank_factories_from_seconds in \[producers\]"),
        ("tank_factories_from_seconds = 300", "tank_factories_from_seconds = 300.5", "tank_factories_from_seconds"),
        ("tank_factories_queued = 2", "tank_factories_queued = 0", "at least 1 and at most 10"),
        ("tank_factories_queued = 2", "tank_factories_queued = 11", "at least 1 and at most 10"),
        ("tank_factories_queued = 2\n", "", r"tank_factories_queued in \[producers\]"),
    ],
    ids=[
        "switch missing",
        "switch a number",
        "incomes missing",
        "negative income",
        "share text",
        "negative share",
        "seconds missing",
        "fraction seconds",
        "none queued",
        "eleven queued",
        "queued missing",
    ],
)
def test_tank_factories_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_bank_spending_the_four_keys_ship_with_their_values_and_a_value_in_the_file_replaces_one(tuning_root):
    rewrite(tuning_root, "idle_bank_money = 3000", "idle_bank_money = 4000")

    producers = Tuning.from_file(REPO_ROOT).producers
    assert producers == ProducersTuning()
    assert (producers.bank_factory_money, producers.bank_factory_seconds) == (2500, 30)
    assert (producers.idle_bank_money, producers.idle_queue_seconds) == (3000, 30)
    assert Tuning.from_file(tuning_root).producers == ProducersTuning(idle_bank_money=4000)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("bank_factory_money = 2500\n", "", r"bank_factory_money in \[producers\] must be a whole number"),
        ("bank_factory_money = 2500", "bank_factory_money = -1", r"bank_factory_money in \[producers\]"),
        ("bank_factory_seconds = 30\n", "", r"bank_factory_seconds in \[producers\]"),
        ("bank_factory_seconds = 30", "bank_factory_seconds = 30.5", r"bank_factory_seconds in \[producers\]"),
        ("idle_bank_money = 3000\n", "", r"idle_bank_money in \[producers\]"),
        ("idle_bank_money = 3000", 'idle_bank_money = "lots"', r"idle_bank_money in \[producers\]"),
        ("idle_queue_seconds = 30\n", "", r"idle_queue_seconds in \[producers\]"),
        ("idle_queue_seconds = 30", "idle_queue_seconds = true", r"idle_queue_seconds in \[producers\]"),
    ],
    ids=[
        "bank money missing",
        "negative bank money",
        "bank seconds missing",
        "fraction bank seconds",
        "idle money missing",
        "idle money text",
        "idle seconds missing",
        "idle seconds a switch",
    ],
)
def test_bank_spending_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_storage_near_cap_the_three_keys_ship_with_their_values_and_a_value_in_the_file_replaces_one(tuning_root):
    rewrite(tuning_root, "storage_war_factories_max = 6", "storage_war_factories_max = 5")

    producers = Tuning.from_file(REPO_ROOT).producers
    assert producers == ProducersTuning()
    assert (producers.storage_spend_share, producers.storage_spend_seconds) == (0.6, 20)
    assert producers.storage_war_factories_max == 6
    assert Tuning.from_file(tuning_root).producers == ProducersTuning(storage_war_factories_max=5)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("storage_spend_share = 0.6\n", "", r"storage_spend_share in \[producers\] must be a number"),
        ("storage_spend_share = 0.6", 'storage_spend_share = "most"', r"storage_spend_share in \[producers\]"),
        ("storage_spend_share = 0.6", "storage_spend_share = -0.6", "a number of at least 0"),
        ("storage_spend_seconds = 20\n", "", r"storage_spend_seconds in \[producers\] must be a whole number"),
        ("storage_spend_seconds = 20", "storage_spend_seconds = 20.5", r"storage_spend_seconds in \[producers\]"),
        ("storage_war_factories_max = 6\n", "", r"storage_war_factories_max in \[producers\]"),
        ("storage_war_factories_max = 6", "storage_war_factories_max = true", r"storage_war_factories_max in"),
    ],
    ids=[
        "share missing",
        "share text",
        "negative share",
        "seconds missing",
        "fraction seconds",
        "most missing",
        "most a switch",
    ],
)
def test_storage_near_cap_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_war_factory_load_gate_ships_on_with_a_share_of_0_8_and_the_file_s_values_are_read(tuning_root):
    rewrite(tuning_root, "war_factory_load_gate = true", "war_factory_load_gate = false")
    rewrite(tuning_root, "war_factory_load_share = 0.8", "war_factory_load_share = 0.75")

    for shipped in (Tuning().producers, Tuning.from_file(REPO_ROOT).producers):
        assert (shipped.war_factory_load_gate, shipped.war_factory_load_share) == (True, 0.8)
    switched = dataclasses.replace(ProducersTuning(), war_factory_load_gate=False, war_factory_load_share=0.75)
    assert Tuning.from_file(tuning_root).producers == switched


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("war_factory_load_gate = true\n", "", r"war_factory_load_gate in \[producers\] must be true or false"),
        ("war_factory_load_gate = true", "war_factory_load_gate = 1", r"war_factory_load_gate in \[producers\]"),
        ("war_factory_load_share = 0.8\n", "", r"war_factory_load_share in \[producers\] must be a number"),
        ("war_factory_load_share = 0.8", 'war_factory_load_share = "most"', r"war_factory_load_share in"),
        ("war_factory_load_share = 0.8", "war_factory_load_share = -0.8", "a number of at least 0"),
    ],
    ids=["switch missing", "switch a number", "share missing", "share text", "negative share"],
)
def test_war_factory_load_gate_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_war_factory_on_load_ships_off_from_180_s_and_the_file_s_values_are_read(tuning_root):
    rewrite(tuning_root, "war_factory_on_load = false", "war_factory_on_load = true")
    rewrite(tuning_root, "war_factory_on_load_from_seconds = 180", "war_factory_on_load_from_seconds = 240")

    for shipped in (Tuning().producers, Tuning.from_file(REPO_ROOT).producers):
        assert (shipped.war_factory_on_load, shipped.war_factory_on_load_from_seconds) == (False, 180)
    switched = dataclasses.replace(ProducersTuning(), war_factory_on_load=True, war_factory_on_load_from_seconds=240)
    assert Tuning.from_file(tuning_root).producers == switched


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("war_factory_on_load = false\n", "", r"war_factory_on_load in \[producers\] must be true or false"),
        ("war_factory_on_load = false", "war_factory_on_load = 1", r"war_factory_on_load in \[producers\]"),
        ("war_factory_on_load_from_seconds = 180\n", "", r"war_factory_on_load_from_seconds in \[producers\]"),
        ("war_factory_on_load_from_seconds = 180", "war_factory_on_load_from_seconds = 180.5", "on_load_from_seconds"),
        ("war_factory_on_load_from_seconds = 180", "war_factory_on_load_from_seconds = -1", "on_load_from_seconds"),
    ],
    ids=["switch missing", "switch a number", "seconds missing", "fraction seconds", "negative seconds"],
)
def test_war_factory_on_load_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_rescout_the_switch_ships_on_with_its_values_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "rescout = true", "rescout = false")

    scouting = Tuning.from_file(REPO_ROOT).scouting
    assert scouting == ScoutingTuning() and scouting.rescout is True
    assert (scouting.rescout_base_seconds, scouting.rescout_army_seconds, scouting.rescout_fields_seconds) == (
        90,
        60,
        180,
    )
    assert (scouting.scout_mission_max_seconds, scouting.scout_train_after_seconds) == (120, 30)
    assert Tuning.from_file(tuning_root).scouting == ScoutingTuning(rescout=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("rescout = true\n", "", r"rescout in \[scouting\] must be true or false"),
        ("rescout = true", "rescout = 1", r"rescout in \[scouting\] must be true or false"),
        ("rescout_base_seconds = 90\n", "", r"rescout_base_seconds in \[scouting\]"),
        ("rescout_army_seconds = 60", "rescout_army_seconds = -1", r"rescout_army_seconds in \[scouting\]"),
        ("rescout_fields_seconds = 180", "rescout_fields_seconds = 1.5", r"rescout_fields_seconds in \[scouting\]"),
        ("scout_mission_max_seconds = 120\n", "", r"scout_mission_max_seconds in \[scouting\]"),
        ("scout_train_after_seconds = 30", 'scout_train_after_seconds = "30"', r"scout_train_after_seconds in"),
    ],
    ids=["switch missing", "switch a number", "base missing", "army negative", "fields fraction", "mission", "train"],
)
def test_rescout_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_indirect_the_route_switch_ships_off_with_its_values_and_true_in_the_file_switches_it_on(tuning_root):
    rewrite(tuning_root, "indirect_route = false", "indirect_route = true")

    scouting = Tuning.from_file(REPO_ROOT).scouting
    assert scouting == ScoutingTuning() and scouting.indirect_route is False
    assert (scouting.scout_side_cells, scouting.scout_side_share, scouting.scout_field_cells) == (14, 0.4, 15)
    assert (scouting.scout_fields_max, scouting.scout_view_cells, scouting.scout_min_enemy_cells) == (2, 14, 9)
    assert scouting.scout_hit_hp_drop == 10
    assert Tuning.from_file(tuning_root).scouting == ScoutingTuning(indirect_route=True)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("indirect_route = false\n", "", r"indirect_route in \[scouting\] must be true or false"),
        ("indirect_route = false", 'indirect_route = "no"', r"indirect_route in \[scouting\] must be true or false"),
        ("scout_side_cells = 14\n", "", r"scout_side_cells in \[scouting\]"),
        ("scout_side_share = 0.4", 'scout_side_share = "most"', r"scout_side_share in \[scouting\]"),
        ("scout_side_share = 0.4", "scout_side_share = -0.4", "a number of at least 0"),
        ("scout_field_cells = 15", "scout_field_cells = 1.5", r"scout_field_cells in \[scouting\]"),
        ("scout_fields_max = 2\n", "", r"scout_fields_max in \[scouting\]"),
        ("scout_view_cells = 14", "scout_view_cells = -14", r"scout_view_cells in \[scouting\]"),
        ("scout_min_enemy_cells = 9\n", "", r"scout_min_enemy_cells in \[scouting\]"),
        ("scout_hit_hp_drop = 10", "scout_hit_hp_drop = true", r"scout_hit_hp_drop in \[scouting\]"),
    ],
    ids=[
        "switch missing",
        "switch text",
        "side missing",
        "share text",
        "negative share",
        "field fraction",
        "fields missing",
        "negative view",
        "min missing",
        "drop a switch",
    ],
)
def test_indirect_a_route_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_rush_detection_ships_on_with_its_values_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "rush_detection = true", "rush_detection = false")

    scouting = Tuning.from_file(REPO_ROOT).scouting
    assert scouting == ScoutingTuning() and scouting.rush_detection is True
    assert (scouting.rush_watch_until_seconds, scouting.rush_unknown_deadline_seconds) == (300, 150)
    assert (scouting.rush_barracks, scouting.rush_barracks_before_seconds, scouting.rush_early_factory_seconds) == (
        2,
        240,
        180,
    )
    assert (scouting.rush_infantry, scouting.rush_vehicles, scouting.rush_near_base_cells) == (6, 2, 20)
    assert (scouting.rush_approach_value, scouting.rush_approach_cells, scouting.watcher_share) == (1000, 30, 0.4)
    assert Tuning.from_file(tuning_root).scouting == ScoutingTuning(rush_detection=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("rush_detection = true", "", r"rush_detection in \[scouting\] must be true or false"),
        ("rush_detection = true", "rush_detection = 0", r"rush_detection in \[scouting\] must be true or false"),
        ("rush_watch_until_seconds = 300", "", r"rush_watch_until_seconds in \[scouting\]"),
        ("rush_unknown_deadline_seconds = 150", "rush_unknown_deadline_seconds = 1.5", "rush_unknown_deadline"),
        ("rush_barracks = 2", "rush_barracks = -2", r"rush_barracks in \[scouting\]"),
        ("rush_barracks_before_seconds = 240", "", r"rush_barracks_before_seconds in \[scouting\]"),
        ("rush_early_factory_seconds = 180", 'rush_early_factory_seconds = "180"', "rush_early_factory_seconds"),
        ("rush_infantry = 6", "", r"rush_infantry in \[scouting\]"),
        ("rush_vehicles = 2", "rush_vehicles = true", r"rush_vehicles in \[scouting\]"),
        ("rush_near_base_cells = 20", "", r"rush_near_base_cells in \[scouting\]"),
        ("rush_approach_value = 1000", "rush_approach_value = -1", r"rush_approach_value in \[scouting\]"),
        ("rush_approach_cells = 30", "", r"rush_approach_cells in \[scouting\]"),
        ("watcher_share = 0.4", 'watcher_share = "most"', r"watcher_share in \[scouting\]"),
        ("watcher_share = 0.4", "watcher_share = -0.4", "a number of at least 0"),
    ],
    ids=[
        "switch missing",
        "switch a number",
        "watch missing",
        "deadline fraction",
        "negative barracks",
        "barracks seconds missing",
        "factory seconds text",
        "infantry missing",
        "vehicles a switch",
        "near base missing",
        "negative value",
        "approach missing",
        "share text",
        "negative share",
    ],
)
def test_rush_detection_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_scout_slots_ship_on_with_two_slots_and_a_20_s_floor_and_false_in_the_file_switches_them_off(tuning_root):
    rewrite(tuning_root, "scout_slots = true", "scout_slots = false")

    scouting = Tuning.from_file(REPO_ROOT).scouting
    assert scouting == ScoutingTuning() and scouting.scout_slots is True
    assert (scouting.scout_slot_count, scouting.scout_replace_floor_seconds) == (2, 20)
    assert Tuning.from_file(tuning_root).scouting == ScoutingTuning(scout_slots=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("scout_slots = true\n", "", r"scout_slots in \[scouting\] must be true or false"),
        ("scout_slots = true", "scout_slots = 0", r"scout_slots in \[scouting\] must be true or false"),
        ("scout_slot_count = 2\n", "", r"scout_slot_count in \[scouting\]"),
        ("scout_slot_count = 2", "scout_slot_count = 0", r"scout_slot_count in \[scouting\]"),
        ("scout_slot_count = 2", "scout_slot_count = 5", r"scout_slot_count in \[scouting\]"),
        ("scout_replace_floor_seconds = 20\n", "", r"scout_replace_floor_seconds in \[scouting\]"),
        ("scout_replace_floor_seconds = 20", "scout_replace_floor_seconds = 2.5", "scout_replace_floor_seconds"),
    ],
    ids=["switch missing", "switch a number", "count missing", "no slot", "five slots", "floor missing", "floor half"],
)
def test_scout_slots_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_scout_spend_cap_ships_on_with_a_60_s_floor_and_6_fast_scouts_and_false_in_the_file_switches_it_off(
    tuning_root,
):
    rewrite(tuning_root, "scout_spend_cap = true", "scout_spend_cap = false")

    scouting = Tuning.from_file(REPO_ROOT).scouting
    assert scouting == ScoutingTuning() and scouting.scout_spend_cap is True
    assert (scouting.capped_replace_seconds, scouting.fast_scouts_max) == (60, 6)
    assert Tuning.from_file(tuning_root).scouting == ScoutingTuning(scout_spend_cap=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("scout_spend_cap = true\n", "", r"scout_spend_cap in \[scouting\] must be true or false"),
        ("scout_spend_cap = true", "scout_spend_cap = 0", r"scout_spend_cap in \[scouting\] must be true or false"),
        ("capped_replace_seconds = 60\n", "", r"capped_replace_seconds in \[scouting\]"),
        ("capped_replace_seconds = 60", "capped_replace_seconds = 60.5", r"capped_replace_seconds in \[scouting\]"),
        ("fast_scouts_max = 6\n", "", r"fast_scouts_max in \[scouting\]"),
        ("fast_scouts_max = 6", "fast_scouts_max = -6", r"fast_scouts_max in \[scouting\]"),
    ],
    ids=["switch missing", "switch a number", "floor missing", "floor a fraction", "max missing", "negative max"],
)
def test_scout_spend_cap_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_harvester_group_response_ships_on_with_60_s_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "harvester_group_response = true", "harvester_group_response = false")

    defense = Tuning.from_file(REPO_ROOT).base_defense
    assert defense == BaseDefenseTuning() and defense.harvester_group_response is True
    assert defense.harvester_response_seconds == 60
    assert Tuning.from_file(tuning_root).base_defense == BaseDefenseTuning(harvester_group_response=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        (
            "harvester_group_response = true\n",
            "",
            r"harvester_group_response in \[base_defense\] must be true or false",
        ),
        (
            "harvester_group_response = true",
            'harvester_group_response = "false"',
            r"harvester_group_response in \[base_defense\] must be true or false",
        ),
        ("harvester_response_seconds = 60\n", "", r"harvester_response_seconds in \[base_defense\]"),
        ("harvester_response_seconds = 60", "harvester_response_seconds = -1", r"harvester_response_seconds"),
        ("harvester_response_seconds = 60", "harvester_response_seconds = 1.5", r"harvester_response_seconds"),
    ],
    ids=["switch missing", "switch a string", "seconds missing", "negative seconds", "seconds a fraction"],
)
def test_harvester_group_response_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_keep_attack_army_ships_on_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "keep_attack_army = true", "keep_attack_army = false")

    defense = Tuning.from_file(REPO_ROOT).base_defense
    assert defense == BaseDefenseTuning() and defense.keep_attack_army is True
    assert Tuning.from_file(tuning_root).base_defense == BaseDefenseTuning(keep_attack_army=False)


@pytest.mark.parametrize(
    "new",
    ["", 'keep_attack_army = "false"\n', "keep_attack_army = 0\n"],
    ids=["missing", "a string", "a number"],
)
def test_keep_attack_army_a_switch_that_is_missing_or_not_true_or_false_is_named(tuning_root, new):
    rewrite(tuning_root, "keep_attack_army = true\n", new)

    with pytest.raises(ConfigError, match=r"keep_attack_army in \[base_defense\] must be true or false"):
        Tuning.from_file(tuning_root)


def test_steering_the_scouts_keys_ship_with_the_values_the_requirement_gives_and_a_value_of_the_file_is_read(
    tuning_root,
):
    rewrite(tuning_root, "scout_flee_cells = 6", "scout_flee_cells = 8")

    scouting = Tuning.from_file(REPO_ROOT).scouting
    assert scouting == ScoutingTuning()
    assert (scouting.scout_danger_margin_cells, scouting.scout_flee_cells, scouting.scout_center_clear_cells) == (
        3,
        6,
        12,
    )
    assert (scouting.scout_reached_cells, scouting.scout_orders_per_batch, scouting.scout_release_below_hp) == (
        3,
        2,
        40,
    )
    assert Tuning.from_file(tuning_root).scouting == ScoutingTuning(scout_flee_cells=8)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("scout_danger_margin_cells = 3\n", "", r"scout_danger_margin_cells in \[scouting\]"),
        ("scout_flee_cells = 6", "scout_flee_cells = 6.5", r"scout_flee_cells in \[scouting\]"),
        (
            "scout_center_clear_cells = 12",
            "scout_center_clear_cells = -12",
            r"scout_center_clear_cells in \[scouting\]",
        ),
        ("scout_reached_cells = 3\n", "", r"scout_reached_cells in \[scouting\]"),
        ("scout_orders_per_batch = 2", "scout_orders_per_batch = 0", r"scout_orders_per_batch in \[scouting\]"),
        ("scout_orders_per_batch = 2", "scout_orders_per_batch = 21", r"scout_orders_per_batch in \[scouting\]"),
        ("scout_release_below_hp = 40", "scout_release_below_hp = 101", r"scout_release_below_hp in \[scouting\]"),
        ("scout_release_below_hp = 40", "scout_release_below_hp = true", r"scout_release_below_hp in \[scouting\]"),
    ],
    ids=[
        "margin missing",
        "flee fraction",
        "negative clear",
        "reached missing",
        "no order",
        "21 orders",
        "hp over 100",
        "hp a switch",
    ],
)
def test_steering_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


SOVIET_SCOUT_UNIT_SHIPPED = 'soviet_scout_unit = "dog"'


def test_soviet_scout_unit_ships_the_dog_and_the_flak_truck_in_the_file_is_read(tuning_root):
    rewrite(tuning_root, SOVIET_SCOUT_UNIT_SHIPPED, 'soviet_scout_unit = "ftrk"')

    scouting = Tuning.from_file(REPO_ROOT).scouting
    assert scouting == ScoutingTuning() and scouting.soviet_scout_unit == "dog"
    assert Tuning.from_file(tuning_root).scouting == ScoutingTuning(soviet_scout_unit="ftrk")


@pytest.mark.parametrize(
    "new",
    ['soviet_scout_unit = "jeep"', 'soviet_scout_unit = "Ftrk"', "soviet_scout_unit = 1", ""],
    ids=["another unit", "case", "number", "missing"],
)
def test_soviet_scout_unit_a_value_other_than_dog_or_ftrk_is_rejected_naming_the_key(tuning_root, new):
    rewrite(tuning_root, SOVIET_SCOUT_UNIT_SHIPPED, new)

    with pytest.raises(ConfigError, match=r'soviet_scout_unit in \[scouting\] must be "dog" or "ftrk"'):
        Tuning.from_file(tuning_root)


def test_wide_berth_ships_on_with_its_values_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "scout_wide_berth = true", "scout_wide_berth = false")

    scouting = Tuning.from_file(REPO_ROOT).scouting
    assert scouting == ScoutingTuning() and scouting.scout_wide_berth is True
    wide = (scouting.wide_danger_margin_cells, scouting.wide_flee_cells, scouting.wide_release_below_hp)
    hurt = (scouting.hurt_area_cells, scouting.hurt_avoid_seconds, scouting.hurt_min_drop, scouting.hurt_areas_max)
    assert (wide, hurt) == ((6, 10, 60), (10, 60, 5, 8))
    assert Tuning.from_file(tuning_root).scouting == ScoutingTuning(scout_wide_berth=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("scout_wide_berth = true\n", "", r"scout_wide_berth in \[scouting\] must be true or false"),
        ("scout_wide_berth = true", "scout_wide_berth = 1", r"scout_wide_berth in \[scouting\] must be true or false"),
        ("wide_danger_margin_cells = 6\n", "", r"wide_danger_margin_cells in \[scouting\]"),
        ("wide_flee_cells = 10", "wide_flee_cells = 10.5", r"wide_flee_cells in \[scouting\]"),
        ("hurt_area_cells = 10", "hurt_area_cells = -10", r"hurt_area_cells in \[scouting\]"),
        ("hurt_avoid_seconds = 60\n", "", r"hurt_avoid_seconds in \[scouting\]"),
        ("wide_release_below_hp = 60", "wide_release_below_hp = 101", r"wide_release_below_hp in \[scouting\]"),
        ("hurt_min_drop = 5", 'hurt_min_drop = "5"', r"hurt_min_drop in \[scouting\]"),
        ("hurt_areas_max = 8\n", "", r"hurt_areas_max in \[scouting\]"),
    ],
    ids=[
        "switch missing",
        "switch a number",
        "margin missing",
        "flee fraction",
        "negative area",
        "avoid missing",
        "hp over 100",
        "drop a string",
        "areas missing",
    ],
)
def test_wide_berth_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_model_plan_hold_ships_on_with_750_ticks_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "model_plan_hold = true", "model_plan_hold = false")

    rules = Tuning.from_file(REPO_ROOT).plan_rules
    assert rules == PlanRulesTuning() and (rules.model_plan_hold, rules.model_plan_hold_ticks) == (True, 750)
    assert Tuning.from_file(tuning_root).plan_rules == PlanRulesTuning(model_plan_hold=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("model_plan_hold = true\n", "", r"model_plan_hold in \[plan_rules\] must be true or false"),
        ("model_plan_hold = true", "model_plan_hold = 1", r"model_plan_hold in \[plan_rules\] must be true or false"),
        ("model_plan_hold_ticks = 750\n", "", r"model_plan_hold_ticks in \[plan_rules\]"),
        ("model_plan_hold_ticks = 750", "model_plan_hold_ticks = 7.5", r"model_plan_hold_ticks in \[plan_rules\]"),
        ("model_plan_hold_ticks = 750", "model_plan_hold_ticks = -750", r"model_plan_hold_ticks in \[plan_rules\]"),
    ],
    ids=["switch missing", "switch a number", "ticks missing", "ticks a fraction", "ticks negative"],
)
def test_model_plan_hold_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_scout_choice_keys_ship_with_6_options_a_1500_ms_deadline_and_125_ticks_and_a_value_of_the_file_is_read(
    tuning_root,
):
    rewrite(tuning_root, "scout_choice_options = 6", "scout_choice_options = 4")

    scouting = Tuning.from_file(REPO_ROOT).scouting
    assert scouting == ScoutingTuning()
    assert (scouting.scout_choice_options, scouting.scout_choice_deadline_ms, scouting.scout_choice_every_ticks) == (
        6,
        1500,
        125,
    )
    assert Tuning.from_file(tuning_root).scouting == ScoutingTuning(scout_choice_options=4)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("scout_choice_options = 6\n", "", r"scout_choice_options in \[scouting\]"),
        ("scout_choice_options = 6", "scout_choice_options = 1", r"scout_choice_options in \[scouting\]"),
        ("scout_choice_options = 6", "scout_choice_options = 10", r"scout_choice_options in \[scouting\]"),
        (
            "scout_choice_deadline_ms = 1500",
            "scout_choice_deadline_ms = 0",
            r"scout_choice_deadline_ms in \[scouting\]",
        ),
        ("scout_choice_every_ticks = 125\n", "", r"scout_choice_every_ticks in \[scouting\]"),
        ("scout_choice_every_ticks = 125", "scout_choice_every_ticks = 12.5", r"scout_choice_every_ticks in"),
    ],
    ids=["options missing", "one option", "ten options", "no time", "every missing", "every fraction"],
)
def test_scout_choice_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_scout_missions_ships_on_with_a_margin_of_1_25_90_s_and_10_s_and_the_file_s_values_are_read(tuning_root):
    rewrite(tuning_root, "scout_missions = true", "scout_missions = false")
    rewrite(tuning_root, "mission_switch_margin = 1.25", "mission_switch_margin = 1.5")

    scouting = Tuning.from_file(REPO_ROOT).scouting
    assert scouting == ScoutingTuning() and scouting.scout_missions is True
    assert (scouting.mission_switch_margin, scouting.mission_blacklist_seconds, scouting.mission_observe_seconds) == (
        1.25,
        90,
        10,
    )
    switched = Tuning.from_file(tuning_root).scouting
    assert switched == ScoutingTuning(scout_missions=False, mission_switch_margin=1.5)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("scout_missions = true\n", "", r"scout_missions in \[scouting\] must be true or false"),
        ("scout_missions = true", 'scout_missions = "false"', r"scout_missions in \[scouting\] must be true or false"),
        ("mission_switch_margin = 1.25\n", "", r"mission_switch_margin in \[scouting\]"),
        ("mission_switch_margin = 1.25", 'mission_switch_margin = "1.25"', r"mission_switch_margin in \[scouting\]"),
        ("mission_blacklist_seconds = 90\n", "", r"mission_blacklist_seconds in \[scouting\]"),
        ("mission_blacklist_seconds = 90", "mission_blacklist_seconds = 1.5", r"mission_blacklist_seconds in"),
        ("mission_observe_seconds = 10\n", "", r"mission_observe_seconds in \[scouting\]"),
        ("mission_observe_seconds = 10", "mission_observe_seconds = -1", r"mission_observe_seconds in"),
    ],
    ids=[
        "switch missing",
        "switch a string",
        "margin missing",
        "margin a string",
        "blacklist missing",
        "blacklist a fraction",
        "observe missing",
        "observe negative",
    ],
)
def test_scout_missions_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_exposed_trucks_ships_on_with_20_cells_a_600_guard_within_8_cells_and_60_s_and_false_in_the_file_is_read(
    tuning_root,
):
    rewrite(tuning_root, "exposed_trucks = true", "exposed_trucks = false")
    rewrite(tuning_root, "truck_guard_max_value = 600", "truck_guard_max_value = 900")

    for shipped in (Tuning().scouting, Tuning.from_file(REPO_ROOT).scouting):
        guard = (shipped.truck_guard_max_value, shipped.truck_guard_cells)
        assert (shipped.exposed_trucks, shipped.truck_field_min_cells, guard, shipped.truck_seen_seconds) == (
            True,
            20,
            (600, 8),
            60,
        )
    switched = Tuning.from_file(tuning_root).scouting
    assert switched == ScoutingTuning(exposed_trucks=False, truck_guard_max_value=900)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("exposed_trucks = true\n", "", r"exposed_trucks in \[scouting\] must be true or false"),
        ("exposed_trucks = true", "exposed_trucks = 1", r"exposed_trucks in \[scouting\] must be true or false"),
        ("truck_field_min_cells = 20\n", "", r"truck_field_min_cells in \[scouting\] must be a whole number"),
        ("truck_guard_max_value = 600", "truck_guard_max_value = 600.5", r"truck_guard_max_value in \[scouting\]"),
        ("truck_guard_cells = 8", "truck_guard_cells = -8", r"truck_guard_cells in \[scouting\] must be a whole"),
        ("truck_seen_seconds = 60", "truck_seen_seconds = true", r"truck_seen_seconds in \[scouting\] must be a whole"),
    ],
    ids=[
        "switch missing",
        "switch a number",
        "cells missing",
        "guard a fraction",
        "negative reach",
        "seconds a switch",
    ],
)
def test_exposed_trucks_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


SHIPPED_ORDER_LINE = f'order = "{OpeningTuning().order}"'


def test_the_opening_order_ships_as_built_in_and_reach_first_in_the_file_is_read(tuning_root):
    rewrite(tuning_root, SHIPPED_ORDER_LINE, 'order = "reach_first"')

    assert Tuning.from_file(REPO_ROOT).opening.order == OpeningTuning().order
    assert Tuning.from_file(tuning_root).opening == dataclasses.replace(OpeningTuning(), order="reach_first")


@pytest.mark.parametrize(
    "new",
    ['order = "reach_second"', 'order = "Reach_First"', "order = 3", ""],
    ids=["unknown", "case", "number", "missing"],
)
def test_an_opening_order_that_is_not_one_of_the_four_raises_naming_the_key(tuning_root, new):
    rewrite(tuning_root, SHIPPED_ORDER_LINE, new)

    with pytest.raises(
        ConfigError,
        match=r'order in \[opening\] must be "barracks_first" or "refinery_first" or "reach_first" or "factory_first"',
    ):
        Tuning.from_file(tuning_root)


def test_defense_on_threat_ships_on_with_18_cells_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "defense_on_threat = true", "defense_on_threat = false")

    opening = Tuning.from_file(REPO_ROOT).opening
    assert opening == OpeningTuning() and opening.defense_on_threat is True
    assert opening.armor_near_base_cells == 18
    assert Tuning.from_file(tuning_root).opening == OpeningTuning(defense_on_threat=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("defense_on_threat = true", "", r"defense_on_threat in \[opening\] must be true or false"),
        (
            "defense_on_threat = true",
            "defense_on_threat = 1",
            r"defense_on_threat in \[opening\] must be true or false",
        ),
        ("armor_near_base_cells = 18", "", r"armor_near_base_cells in \[opening\]"),
        ("armor_near_base_cells = 18", "armor_near_base_cells = -18", r"armor_near_base_cells in \[opening\]"),
        ("armor_near_base_cells = 18", "armor_near_base_cells = 2.5", r"armor_near_base_cells in \[opening\]"),
        ("armor_near_base_cells = 18", "armor_near_base_cells = true", r"armor_near_base_cells in \[opening\]"),
    ],
    ids=["switch missing", "switch a number", "cells missing", "negative cells", "cells fraction", "cells a switch"],
)
def test_defense_on_threat_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_lean_the_lean_opening_ships_on_with_3_guard_rifles_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "lean_infantry = true", "lean_infantry = false")
    rewrite(tuning_root, "lean_guard_rifles = 3", "lean_guard_rifles = 2")

    for shipped in (Tuning().opening, Tuning.from_file(REPO_ROOT).opening):
        assert (shipped.lean_infantry, shipped.lean_guard_rifles) == (True, 3)
    switched = dataclasses.replace(OpeningTuning(), lean_infantry=False, lean_guard_rifles=2)
    assert Tuning.from_file(tuning_root).opening == switched


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("lean_infantry = true\n", "", r"lean_infantry in \[opening\] must be true or false"),
        ("lean_infantry = true", "lean_infantry = 0", r"lean_infantry in \[opening\] must be true or false"),
        ("lean_guard_rifles = 3\n", "", r"lean_guard_rifles in \[opening\] must be a whole number"),
        ("lean_guard_rifles = 3", "lean_guard_rifles = -3", r"lean_guard_rifles in \[opening\] must be a whole number"),
        (
            "lean_guard_rifles = 3",
            "lean_guard_rifles = 2.5",
            r"lean_guard_rifles in \[opening\] must be a whole number",
        ),
    ],
    ids=["switch missing", "switch a number", "rifles missing", "negative rifles", "rifles a fraction"],
)
def test_lean_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_forward_hold_ships_off_with_its_five_keys_and_true_in_the_file_switches_it_on(tuning_root):
    rewrite(tuning_root, "forward_hold = false", "forward_hold = true")

    combat = Tuning.from_file(REPO_ROOT).combat
    assert combat == CombatTuning() and combat.forward_hold is False
    assert (combat.forward_hold_share, combat.forward_hold_min_strength) == (0.4, 1500)
    cells = (combat.forward_hold_cover_cells, combat.forward_hold_chase_cells, combat.forward_hold_defense_clear_cells)
    assert cells == (6, 10, 4)
    assert Tuning.from_file(tuning_root).combat == CombatTuning(forward_hold=True)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("forward_hold = false", "", r"forward_hold in \[combat\] must be true or false"),
        ("forward_hold = false", "forward_hold = 1", r"forward_hold in \[combat\] must be true or false"),
        ("forward_hold_share = 0.4", "", r"forward_hold_share in \[combat\]"),
        ("forward_hold_share = 0.4", "forward_hold_share = -0.4", r"forward_hold_share in \[combat\]"),
        ("forward_hold_min_strength = 1500", "forward_hold_min_strength = 1500.5", r"forward_hold_min_strength in"),
        ("forward_hold_cover_cells = 6", "forward_hold_cover_cells = -6", r"forward_hold_cover_cells in \[combat\]"),
        ("forward_hold_chase_cells = 10", "", r"forward_hold_chase_cells in \[combat\]"),
        (
            "forward_hold_defense_clear_cells = 4",
            "forward_hold_defense_clear_cells = true",
            r"forward_hold_defense_clear_cells in \[combat\]",
        ),
    ],
    ids=[
        "switch missing",
        "switch a number",
        "share missing",
        "negative share",
        "strength fraction",
        "negative cover cells",
        "chase cells missing",
        "clear cells a switch",
    ],
)
def test_forward_hold_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_sticky_attack_target_ships_on_with_60_s_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "sticky_attack_target = true", "sticky_attack_target = false")

    combat = Tuning.from_file(REPO_ROOT).combat
    assert combat == CombatTuning() and (combat.sticky_attack_target, combat.stale_target_seconds) == (True, 60)
    assert Tuning.from_file(tuning_root).combat == CombatTuning(sticky_attack_target=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("sticky_attack_target = true\n", "", r"sticky_attack_target in \[combat\] must be true or false"),
        ("sticky_attack_target = true", "sticky_attack_target = 0", r"sticky_attack_target in \[combat\] must be"),
        ("stale_target_seconds = 60\n", "", r"stale_target_seconds in \[combat\] must be a whole number"),
        ("stale_target_seconds = 60", "stale_target_seconds = 60.5", r"stale_target_seconds in \[combat\] must be"),
        ("stale_target_seconds = 60", "stale_target_seconds = -60", r"stale_target_seconds in \[combat\] must be"),
    ],
    ids=["switch missing", "switch a number", "seconds missing", "seconds a fraction", "negative seconds"],
)
def test_sticky_attack_target_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_crush_run_ships_on_with_4_infantry_a_ratio_of_1_and_2_tanks_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "crush_runs = true", "crush_runs = false")

    for shipped in (Tuning().combat, Tuning.from_file(REPO_ROOT).combat):
        cluster = (shipped.crush_min_infantry, shipped.crush_cluster_cells, shipped.crush_reach_cells)
        run = (shipped.crush_per_tanks, shipped.crush_max_tanks, shipped.crush_min_hp, shipped.crush_min_ratio)
        anti_tank = (shipped.crush_at_share, shipped.crush_at_per_tanks, shipped.crush_at_max_tanks)
        group = (shipped.crush_min_tanks, shipped.crush_at_min_infantry)
        assert (shipped.crush_runs, cluster, run) == (True, (4, 3, 10), (3, 3, 50, 1.0))
        assert (group, anti_tank) == ((2, 3), (0.33, 2, 5))
    assert Tuning.from_file(tuning_root).combat == CombatTuning(crush_runs=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("crush_runs = true\n", "", r"crush_runs in \[combat\] must be true or false"),
        ("crush_runs = true", "crush_runs = 0", r"crush_runs in \[combat\] must be true or false"),
        ("crush_min_infantry = 4", "crush_min_infantry = 4.5", r"crush_min_infantry in \[combat\] must be a whole"),
        ("crush_cluster_cells = 3\n", "", r"crush_cluster_cells in \[combat\] must be a whole number"),
        ("crush_reach_cells = 10", "crush_reach_cells = -10", r"crush_reach_cells in \[combat\] must be a whole"),
        ("crush_per_tanks = 3", "crush_per_tanks = 0", r"crush_per_tanks in \[combat\] must be a whole number"),
        ("crush_max_tanks = 3", "crush_max_tanks = true", r"crush_max_tanks in \[combat\] must be a whole number"),
        ("crush_min_hp = 50", "crush_min_hp = 101", r"crush_min_hp in \[combat\] must be a whole number"),
        ("crush_min_ratio = 1.0", "crush_min_ratio = -1.0", r"crush_min_ratio in \[combat\] must be a number"),
        ("crush_min_ratio = 1.0", 'crush_min_ratio = "1.0"', r"crush_min_ratio in \[combat\] must be a number"),
        ("crush_min_tanks = 2\n", "", r"crush_min_tanks in \[combat\] must be a whole number"),
        ("crush_at_share = 0.33", 'crush_at_share = "0.33"', r"crush_at_share in \[combat\] must be a number"),
        ("crush_at_per_tanks = 2", "crush_at_per_tanks = 0", r"crush_at_per_tanks in \[combat\] must be a whole"),
        ("crush_at_max_tanks = 5", "crush_at_max_tanks = 5.5", r"crush_at_max_tanks in \[combat\] must be a whole"),
        ("crush_at_min_infantry = 3\n", "", r"crush_at_min_infantry in \[combat\] must be a whole number"),
    ],
    ids=[
        "switch missing",
        "switch a number",
        "infantry a fraction",
        "cluster cells missing",
        "negative reach",
        "no infantry per tank",
        "tanks a switch",
        "hp over 100",
        "negative ratio",
        "ratio text",
        "min tanks missing",
        "anti-tank share text",
        "no infantry per tank against anti-tank",
        "anti-tank max a fraction",
        "anti-tank min infantry missing",
    ],
)
def test_crush_run_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_nuke_spread_ships_on_with_its_five_values_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "nuke_spread = true", "nuke_spread = false")

    for shipped in (Tuning().combat, Tuning.from_file(REPO_ROOT).combat):
        clock = (shipped.nuke_charge_ticks, shipped.nuke_build_ticks)
        groups = (shipped.nuke_spread_groups, shipped.nuke_spread_cells, shipped.nuke_keep_off_cells)
        assert (shipped.nuke_spread, clock, groups) == (True, (13500, 1500), (3, 8, 12))
    assert Tuning.from_file(tuning_root).combat == CombatTuning(nuke_spread=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("nuke_spread = true\n", "", r"nuke_spread in \[combat\] must be true or false"),
        ("nuke_spread = true", "nuke_spread = 1", r"nuke_spread in \[combat\] must be true or false"),
        ("nuke_charge_ticks = 13500", "nuke_charge_ticks = 0", r"nuke_charge_ticks in \[combat\] must be a whole"),
        ("nuke_build_ticks = 1500\n", "", r"nuke_build_ticks in \[combat\] must be a whole number"),
        ("nuke_spread_groups = 3", "nuke_spread_groups = 0", r"nuke_spread_groups in \[combat\] must be a whole"),
        ("nuke_spread_cells = 8", "nuke_spread_cells = 8.5", r"nuke_spread_cells in \[combat\] must be a whole"),
        ("nuke_keep_off_cells = 12", "nuke_keep_off_cells = -12", r"nuke_keep_off_cells in \[combat\] must be a"),
    ],
    ids=[
        "switch missing",
        "switch a number",
        "no charge time",
        "build time missing",
        "no group",
        "spread a fraction",
        "negative keep-off",
    ],
)
def test_nuke_spread_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_march_when_clear_ships_on_with_5_cells_in_15_s_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "march_when_clear = true", "march_when_clear = false")

    siege = Tuning.from_file(REPO_ROOT).siege
    assert siege == Tuning().siege and siege.march_when_clear is True
    assert (siege.march_progress_cells, siege.march_progress_seconds) == (5, 15)
    assert Tuning.from_file(tuning_root).siege == dataclasses.replace(Tuning().siege, march_when_clear=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("march_when_clear = true\n", "", r"march_when_clear in \[siege\] must be true or false"),
        ("march_when_clear = true", 'march_when_clear = "false"', r"march_when_clear in \[siege\] must be true"),
        ("march_progress_cells = 5\n", "", r"march_progress_cells in \[siege\]"),
        ("march_progress_cells = 5", "march_progress_cells = 2.5", r"march_progress_cells in \[siege\]"),
        ("march_progress_seconds = 15", "march_progress_seconds = -15", r"march_progress_seconds in \[siege\]"),
        ("march_progress_seconds = 15", "march_progress_seconds = true", r"march_progress_seconds in \[siege\]"),
    ],
    ids=["switch missing", "switch a string", "cells missing", "cells a fraction", "negative seconds", "a switch"],
)
def test_march_when_clear_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_march_hysteresis_ships_clear_beyond_15_cells_and_blocked_within_12_and_the_file_s_values_replace_them(
    tuning_root,
):
    rewrite(tuning_root, "march_clear_cells = 15", "march_clear_cells = 20")

    for shipped in (Tuning().siege, Tuning.from_file(REPO_ROOT).siege):
        assert (shipped.march_clear_cells, shipped.march_blocked_cells) == (15, 12)
    assert Tuning.from_file(tuning_root).siege == dataclasses.replace(Tuning().siege, march_clear_cells=20)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("march_clear_cells = 15\n", "", r"march_clear_cells in \[siege\] must be a whole number"),
        ("march_clear_cells = 15", "march_clear_cells = 15.5", r"march_clear_cells in \[siege\] must be a whole"),
        ("march_blocked_cells = 12\n", "", r"march_blocked_cells in \[siege\] must be a whole number"),
        ("march_blocked_cells = 12", "march_blocked_cells = -12", r"march_blocked_cells in \[siege\] must be a"),
    ],
    ids=["clear missing", "clear a fraction", "blocked missing", "negative blocked"],
)
def test_march_hysteresis_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_artillery_slots_ship_2_cells_apart_sent_again_at_3_cells_or_50_idle_ticks_and_the_file_s_values_replace_them(
    tuning_root,
):
    rewrite(tuning_root, "slot_idle_ticks = 50", "slot_idle_ticks = 75")

    for shipped in (Tuning().siege, Tuning.from_file(REPO_ROOT).siege):
        assert (shipped.slot_spacing_cells, shipped.slot_resend_cells, shipped.slot_idle_ticks) == (2, 3, 50)
    assert Tuning.from_file(tuning_root).siege == dataclasses.replace(Tuning().siege, slot_idle_ticks=75)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("slot_spacing_cells = 2\n", "", r"slot_spacing_cells in \[siege\] must be a whole number"),
        ("slot_spacing_cells = 2", "slot_spacing_cells = 2.5", r"slot_spacing_cells in \[siege\] must be a whole"),
        ("slot_resend_cells = 3\n", "", r"slot_resend_cells in \[siege\] must be a whole number"),
        ("slot_resend_cells = 3", "slot_resend_cells = -3", r"slot_resend_cells in \[siege\] must be a whole"),
        ("slot_idle_ticks = 50\n", "", r"slot_idle_ticks in \[siege\] must be a whole number"),
        ("slot_idle_ticks = 50", "slot_idle_ticks = true", r"slot_idle_ticks in \[siege\] must be a whole"),
    ],
    ids=["spacing missing", "spacing a fraction", "resend missing", "negative resend", "idle missing", "idle a switch"],
)
def test_artillery_slots_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_forward_staging_ships_on_with_a_3_cell_margin_75_percent_within_6_cells_and_25_s_and_false_switches_it_off(
    tuning_root,
):
    rewrite(tuning_root, "forward_staging = true", "forward_staging = false")

    for shipped in (Tuning().combat, Tuning.from_file(REPO_ROOT).combat):
        body = (shipped.staging_body_share, shipped.staging_body_cells)
        staging = (shipped.forward_staging, shipped.staging_margin_cells, body, shipped.staging_timeout_seconds)
        assert staging == (True, 3, (0.75, 6), 25)
    assert Tuning.from_file(tuning_root).combat == CombatTuning(forward_staging=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("forward_staging = true\n", "", r"forward_staging in \[combat\] must be true or false"),
        ("forward_staging = true", "forward_staging = 1", r"forward_staging in \[combat\] must be true or false"),
        ("staging_margin_cells = 3\n", "", r"staging_margin_cells in \[combat\] must be a whole number"),
        ("staging_margin_cells = 3", "staging_margin_cells = 2.5", r"staging_margin_cells in \[combat\] must be a"),
        ("staging_body_share = 0.75", "staging_body_share = -0.75", r"staging_body_share in \[combat\] must be a"),
        ("staging_body_share = 0.75", 'staging_body_share = "0.75"', r"staging_body_share in \[combat\] must be a"),
        ("staging_body_cells = 6", "staging_body_cells = -6", r"staging_body_cells in \[combat\] must be a whole"),
        ("staging_timeout_seconds = 25\n", "", r"staging_timeout_seconds in \[combat\] must be a whole number"),
        ("staging_timeout_seconds = 25", "staging_timeout_seconds = true", r"staging_timeout_seconds in \[combat\]"),
    ],
    ids=[
        "switch missing",
        "switch a number",
        "margin missing",
        "margin a fraction",
        "negative share",
        "share text",
        "negative body cells",
        "timeout missing",
        "timeout a switch",
    ],
)
def test_forward_staging_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_strike_near_ships_on_at_15_cells_a_4_cell_refinery_margin_and_1_5_and_false_switches_it_off(tuning_root):
    rewrite(tuning_root, "strike_near_expansion = true", "strike_near_expansion = false")

    for shipped in (Tuning().combat, Tuning.from_file(REPO_ROOT).combat):
        strike = (shipped.expansion_near_cells, shipped.defense_near_refinery_margin_cells, shipped.strike_min_ratio)
        assert (shipped.strike_near_expansion, strike) == (True, (15, 4, 1.5))
    assert Tuning.from_file(tuning_root).combat == CombatTuning(strike_near_expansion=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("strike_near_expansion = true\n", "", r"strike_near_expansion in \[combat\] must be true or false"),
        ("strike_near_expansion = true", "strike_near_expansion = 1", r"strike_near_expansion in \[combat\] must be"),
        ("expansion_near_cells = 15\n", "", r"expansion_near_cells in \[combat\] must be a whole number"),
        ("expansion_near_cells = 15", "expansion_near_cells = 1.5", r"expansion_near_cells in \[combat\] must be a"),
        ("defense_near_refinery_margin_cells = 4", "defense_near_refinery_margin_cells = -4", r"defense_near_refinery"),
        ("strike_min_ratio = 1.5\n", "", r"strike_min_ratio in \[combat\] must be a"),
        ("strike_min_ratio = 1.5", 'strike_min_ratio = "1.5"', r"strike_min_ratio in \[combat\] must be a"),
    ],
    ids=[
        "switch missing",
        "switch a number",
        "cells missing",
        "cells a fraction",
        "negative margin",
        "ratio missing",
        "ratio text",
    ],
)
def test_strike_near_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_a_list_of_the_file_replaces_the_built_in_one(tuning_root):
    rewrite(tuning_root, "factories_income = [125, 150, 185]", "factories_income = [1, 2, 3]")

    assert Tuning.from_file(tuning_root).producers.factories_income == (1, 2, 3)


@pytest.mark.parametrize(
    "given",
    ['[100, "x"]', "[100, -1]", "[1.5]", "100"],
    ids=["text in it", "a negative number in it", "a fraction in it", "not a list"],
)
def test_a_list_threshold_that_is_not_whole_numbers_of_zero_or_more_is_named(tuning_root, given):
    rewrite(tuning_root, "barracks_income = [100, 100, 150, 150, 170, 170]", f"barracks_income = {given}")

    with pytest.raises(
        ConfigError, match=r"barracks_income in \[producers\] must be a list of whole numbers of at least 0"
    ):
        Tuning.from_file(tuning_root)


def test_a_key_no_threshold_is_named_after_is_rejected(tuning_root):
    rewrite(tuning_root, "guard_rifles = 5", "guard_rifles = 5\nguard_riffles = 6")

    with pytest.raises(ConfigError, match=r"\[opening\] has no setting named guard_riffles"):
        Tuning.from_file(tuning_root)


def test_a_table_no_group_is_named_after_is_rejected(tuning_root):
    rewrite(tuning_root, "[composition]", "[plans]\nhold_ticks = 250\n\n[composition]")

    with pytest.raises(ConfigError, match="the file has no setting named plans"):
        Tuning.from_file(tuning_root)


def test_a_missing_table_is_a_config_error_that_names_it(tuning_root):
    path = tuning_root / TUNING_FILE
    path.write_text(path.read_text(encoding="utf-8").split("[composition]")[0], encoding="utf-8")

    with pytest.raises(ConfigError, match=r"must have a \[composition\] table"):
        Tuning.from_file(tuning_root)


def test_the_terrain_table_holds_the_buildable_characters_and_the_unknown_share_the_requirement_gives():
    expected = TerrainTuning(buildable_characters=".=S", unknown_block_warn_share=0.2)

    assert Tuning().terrain == expected and Tuning.from_file(REPO_ROOT).terrain == expected


def test_the_buildable_characters_are_read_from_the_file(tuning_root):
    rewrite(tuning_root, 'buildable_characters = ".=S"', 'buildable_characters = ".="')

    terrain = Tuning.from_file(tuning_root).terrain

    assert terrain == TerrainTuning(buildable_characters=".=", unknown_block_warn_share=0.2)


@pytest.mark.parametrize(
    "new, expected",
    [
        ('buildable_characters = ""', "a string of at least one character"),
        ("buildable_characters = 1", "a string"),
        ('buildable_characters = [".", ","]', "a string"),
        ("", "a string"),
    ],
    ids=["empty", "a number", "a list", "missing"],
)
def test_buildable_characters_that_are_empty_missing_or_not_a_string_fail_the_loader(tuning_root, new, expected):
    rewrite(tuning_root, 'buildable_characters = ".=S"', new)

    with pytest.raises(ConfigError) as raised:
        Tuning.from_file(tuning_root)

    assert str(raised.value).endswith(f"buildable_characters in [terrain] must be {expected}.")


def test_an_unknown_share_that_is_not_a_number_of_zero_or_more_fails_the_loader(tuning_root):
    rewrite(tuning_root, "unknown_block_warn_share = 0.2", "unknown_block_warn_share = -0.2")

    with pytest.raises(ConfigError, match=r"unknown_block_warn_share in \[terrain\] must be a number of at least 0"):
        Tuning.from_file(tuning_root)


# -- the world model's transports ------------------------------------------------------------------


def test_the_world_table_holds_a_board_window_of_100_ticks_and_a_transport_capacity_of_5():
    expected = WorldTuning(board_window_ticks=100, transport_capacity=5)

    assert Tuning().world == expected and Tuning.from_file(REPO_ROOT).world == expected


def test_the_board_window_and_the_transport_capacity_are_read_from_the_file(tuning_root):
    rewrite(tuning_root, "board_window_ticks = 100", "board_window_ticks = 150")
    rewrite(tuning_root, "transport_capacity = 5", "transport_capacity = 4")

    assert Tuning.from_file(tuning_root).world == WorldTuning(board_window_ticks=150, transport_capacity=4)


@pytest.mark.parametrize(
    "old, new, named",
    [
        ("board_window_ticks = 100\n", "", "board_window_ticks in [world]"),
        ("board_window_ticks = 100", "board_window_ticks = -100", "board_window_ticks in [world]"),
        ("transport_capacity = 5", "transport_capacity = 2.5", "transport_capacity in [world]"),
        ("transport_capacity = 5", 'transport_capacity = "five"', "transport_capacity in [world]"),
        ("transport_capacity = 5", "transport_capacity = 5\ncargo_capacity = 5", "[world] has no setting named"),
    ],
    ids=["a missing window", "a negative window", "a fraction of a unit", "text", "an unknown key"],
)
def test_a_world_key_that_is_missing_unknown_or_not_a_whole_number_of_zero_or_more_is_named(
    tuning_root, old, new, named
):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError) as raised:
        Tuning.from_file(tuning_root)

    assert named in str(raised.value) and str(tuning_root / TUNING_FILE) in str(raised.value)


def test_a_file_without_the_world_table_is_a_config_error_that_names_it(tuning_root):
    path = tuning_root / TUNING_FILE
    path.write_text(path.read_text(encoding="utf-8").split("[world]")[0], encoding="utf-8")

    with pytest.raises(ConfigError, match=r"must have a \[world\] table"):
        Tuning.from_file(tuning_root)


# -- the raids -------------------------------------------------------------------------------------


def test_standing_the_raid_group_ships_off_and_true_in_the_file_switches_it_on(tuning_root):
    rewrite(tuning_root, "standing = false", "standing = true")

    assert Tuning().raids.standing is False and Tuning.from_file(REPO_ROOT).raids.standing is False
    assert Tuning.from_file(tuning_root).raids == dataclasses.replace(RaidTuning(), standing=True)


@pytest.mark.parametrize("new", ["", "standing = 1\n", 'standing = "true"\n'], ids=["missing", "a number", "text"])
def test_standing_a_switch_that_is_missing_or_not_true_or_false_is_named(tuning_root, new):
    rewrite(tuning_root, "standing = false\n", new)

    with pytest.raises(ConfigError, match=r"standing in \[raids\] must be true or false"):
        Tuning.from_file(tuning_root)


def test_prefer_exposed_trucks_ships_off_and_true_in_the_file_switches_it_on(tuning_root):
    rewrite(tuning_root, "prefer_exposed_trucks = false", "prefer_exposed_trucks = true")

    assert Tuning().raids.prefer_exposed_trucks is False and Tuning.from_file(REPO_ROOT).raids == RaidTuning()
    assert Tuning.from_file(tuning_root).raids == RaidTuning(prefer_exposed_trucks=True)


@pytest.mark.parametrize("new", ["", "prefer_exposed_trucks = 0\n"], ids=["missing", "a number"])
def test_prefer_exposed_trucks_a_switch_that_is_missing_or_not_true_or_false_is_named(tuning_root, new):
    rewrite(tuning_root, "prefer_exposed_trucks = false\n", new)

    with pytest.raises(ConfigError, match=r"prefer_exposed_trucks in \[raids\] must be true or false"):
        Tuning.from_file(tuning_root)


def test_exposed_raid_ships_on_with_a_200_guard_and_20_s_and_the_file_s_values_are_read(tuning_root):
    rewrite(tuning_root, "raid_exposed_trucks = true", "raid_exposed_trucks = false")
    rewrite(tuning_root, "exposed_raid_guard_max = 200", "exposed_raid_guard_max = 300")
    rewrite(tuning_root, "exposed_raid_seen_seconds = 20", "exposed_raid_seen_seconds = 30")

    for shipped in (Tuning().raids, Tuning.from_file(REPO_ROOT).raids):
        assert (shipped.raid_exposed_trucks, shipped.exposed_raid_guard_max, shipped.exposed_raid_seen_seconds) == (
            True,
            200,
            20,
        )
    switched = RaidTuning(raid_exposed_trucks=False, exposed_raid_guard_max=300, exposed_raid_seen_seconds=30)
    assert Tuning.from_file(tuning_root).raids == switched


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("raid_exposed_trucks = true\n", "", r"raid_exposed_trucks in \[raids\] must be true or false"),
        ("raid_exposed_trucks = true", "raid_exposed_trucks = 1", r"raid_exposed_trucks in \[raids\] must be true or"),
        ("exposed_raid_guard_max = 200\n", "", r"exposed_raid_guard_max in \[raids\] must be a whole number"),
        ("exposed_raid_guard_max = 200", "exposed_raid_guard_max = 200.5", r"exposed_raid_guard_max in \[raids\]"),
        (
            "exposed_raid_seen_seconds = 20",
            "exposed_raid_seen_seconds = -20",
            r"exposed_raid_seen_seconds in \[raids\]",
        ),
    ],
    ids=["switch missing", "switch a number", "guard missing", "guard a fraction", "seconds negative"],
)
def test_exposed_raid_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_launchable_raid_only_ships_on_borrowing_within_15_cells_and_launching_within_1500_ticks(tuning_root):
    rewrite(tuning_root, "launchable_only = true", "launchable_only = false")
    rewrite(tuning_root, "borrow_home_cells = 15", "borrow_home_cells = 20")
    rewrite(tuning_root, "launch_within_ticks = 1500", "launch_within_ticks = 750")

    for shipped in (Tuning().raids, Tuning.from_file(REPO_ROOT).raids):
        assert (shipped.launchable_only, shipped.borrow_home_cells, shipped.launch_within_ticks) == (True, 15, 1500)
    switched = dataclasses.replace(RaidTuning(), launchable_only=False, borrow_home_cells=20, launch_within_ticks=750)
    assert Tuning.from_file(tuning_root).raids == switched


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("launchable_only = true\n", "", "launchable_only in \\[raids\\] must be true or false"),
        ("launchable_only = true", 'launchable_only = "true"', "launchable_only in \\[raids\\] must be true or false"),
        ("borrow_home_cells = 15", "borrow_home_cells = -1", "borrow_home_cells in \\[raids\\] must be a whole number"),
        ("launch_within_ticks = 1500\n", "", "launch_within_ticks in \\[raids\\] must be a whole number"),
        (
            "launch_within_ticks = 1500",
            "launch_within_ticks = 1.5",
            "launch_within_ticks in \\[raids\\] must be a whole",
        ),
    ],
    ids=["switch missing", "switch text", "negative cells", "window missing", "window a fraction"],
)
def test_launchable_a_launch_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


# -- the raid quotas -------------------------------------------------------------------------------


def test_core_tank_mix_main_vehicles_need_depot_ships_on_and_false_in_the_file_switches_it_off(tuning_root):
    rewrite(tuning_root, "main_vehicles_need_depot = true", "main_vehicles_need_depot = false")

    assert Tuning().composition.main_vehicles_need_depot is True
    assert Tuning.from_file(REPO_ROOT).composition.main_vehicles_need_depot is True
    assert Tuning.from_file(tuning_root).composition == CompositionTuning(main_vehicles_need_depot=False)


@pytest.mark.parametrize(
    "new",
    ["", "main_vehicles_need_depot = 1\n", 'main_vehicles_need_depot = "yes"\n'],
    ids=["missing", "number", "text"],
)
def test_core_tank_mix_a_main_vehicles_need_depot_that_is_missing_or_not_true_or_false_is_named(tuning_root, new):
    rewrite(tuning_root, "main_vehicles_need_depot = true\n", new)

    with pytest.raises(ConfigError, match=r"main_vehicles_need_depot in \[composition\] must be true or false"):
        Tuning.from_file(tuning_root)


def test_core_tank_mix_the_tracked_file_with_the_stek_row_and_the_switch_loads_strictly_and_equals_the_built_ins():
    tracked = Tuning.from_file(REPO_ROOT)

    assert tracked == Tuning()
    assert tracked.composition.main_vehicles_need_depot is True
    assert tracked.mix.tank_rows["soviet"]["stek"] == MixRow(
        infantry={"e3": 10}, vehicle={"3tnk": 40, "4tnk": 34, "v2rl": 16}
    )


def test_core_tank_mix_the_soviet_tech_center_keys_ship_on_with_4000_and_30_s_and_the_file_s_values_are_read(
    tuning_root,
):
    rewrite(tuning_root, "soviet_tech_center = true", "soviet_tech_center = false")
    rewrite(tuning_root, "soviet_tech_min_money = 4000", "soviet_tech_min_money = 5000")
    rewrite(tuning_root, "soviet_tech_seconds = 30", "soviet_tech_seconds = 45")

    for producers in (Tuning().producers, Tuning.from_file(REPO_ROOT).producers):
        assert (producers.soviet_tech_center, producers.soviet_tech_min_money, producers.soviet_tech_seconds) == (
            True,
            4000,
            30,
        )
    assert Tuning.from_file(tuning_root).producers == ProducersTuning(
        soviet_tech_center=False, soviet_tech_min_money=5000, soviet_tech_seconds=45
    )


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("soviet_tech_center = true\n", "", r"soviet_tech_center in \[producers\] must be true or false"),
        ("soviet_tech_center = true", "soviet_tech_center = 1", r"soviet_tech_center in \[producers\] must be true"),
        ("soviet_tech_min_money = 4000", "soviet_tech_min_money = -1", r"soviet_tech_min_money in \[producers\]"),
        ("soviet_tech_seconds = 30", "soviet_tech_seconds = 1.5", r"soviet_tech_seconds in \[producers\]"),
    ],
    ids=["switch missing", "switch a number", "negative money", "fraction seconds"],
)
def test_core_tank_mix_a_soviet_tech_center_key_that_is_missing_or_of_the_wrong_kind_is_named(
    tuning_root, old, new, message
):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_light_tank_raid_ships_off_and_true_in_the_file_switches_it_on(tuning_root):
    rewrite(tuning_root, "allied_raid_light_tanks = false", "allied_raid_light_tanks = true")

    assert Tuning().composition.allied_raid_light_tanks is False
    assert Tuning.from_file(REPO_ROOT).composition.allied_raid_light_tanks is False
    assert Tuning.from_file(tuning_root).composition == CompositionTuning(allied_raid_light_tanks=True)


@pytest.mark.parametrize(
    "new", ["", "allied_raid_light_tanks = 1\n", 'allied_raid_light_tanks = "yes"\n'], ids=["missing", "number", "text"]
)
def test_light_tank_raid_a_switch_that_is_missing_or_not_true_or_false_is_named(tuning_root, new):
    rewrite(tuning_root, "allied_raid_light_tanks = false\n", new)

    with pytest.raises(ConfigError, match=r"allied_raid_light_tanks in \[composition\] must be true or false"):
        Tuning.from_file(tuning_root)


@pytest.mark.parametrize("size", [1, 6, 8, 12])
def test_light_tank_raid_vehicles_from_1_to_12_are_read(tuning_root, size):
    rewrite(tuning_root, "raid_vehicles = 4", f"raid_vehicles = {size}")

    assert Tuning.from_file(tuning_root).composition.raid_vehicles == size


SOVIET_RAID_SHIPPED = 'soviet_raid = "infantry"\n'


def test_apc_raid_the_soviet_raid_ships_infantry_with_two_apcs_of_five_rockets_unloading_3_cells_off_waiting_75_ticks():
    for tuning in (Tuning(), Tuning.from_file(REPO_ROOT)):
        composition, raids = tuning.composition, tuning.raids
        assert (composition.soviet_raid, composition.raid_apcs, composition.raid_apc_rockets) == ("infantry", 2, 5)
        assert (raids.unload_cells, raids.reload_wait_ticks) == (3, 75)


@pytest.mark.parametrize("raid", ["apc_rockets", "none"])
def test_apc_raid_the_other_soviet_raids_are_read_from_the_file(tuning_root, raid):
    rewrite(tuning_root, SOVIET_RAID_SHIPPED, f'soviet_raid = "{raid}"\n')

    assert Tuning.from_file(tuning_root).composition == CompositionTuning(soviet_raid=raid)


@pytest.mark.parametrize(
    "new",
    ['soviet_raid = "apc"\n', 'soviet_raid = "Infantry"\n', "soviet_raid = 1\n", ""],
    ids=["unknown", "case", "a number", "missing"],
)
def test_apc_raid_a_soviet_raid_that_is_not_one_of_the_three_raises_naming_the_key(tuning_root, new):
    rewrite(tuning_root, SOVIET_RAID_SHIPPED, new)

    with pytest.raises(
        ConfigError, match=r'soviet_raid in \[composition\] must be "infantry" or "apc_rockets" or "none"'
    ):
        Tuning.from_file(tuning_root)


@pytest.mark.parametrize(
    "old, new",
    [
        ("raid_apcs = 2", "raid_apcs = 0"),
        ("raid_apcs = 2", "raid_apcs = 5"),
        ("raid_apc_rockets = 5", "raid_apc_rockets = 0"),
        ("raid_apc_rockets = 5", "raid_apc_rockets = 6"),
        ("raid_apcs = 2", "raid_apcs = 1.5"),
    ],
    ids=["no APC", "five APCs", "no rocket", "six rockets", "half an APC"],
)
def test_apc_raid_apcs_outside_1_to_4_or_rockets_outside_1_to_5_raise_naming_the_key(tuning_root, old, new):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=rf"{old.split(' = ')[0]} in \[composition\] must be a whole number"):
        Tuning.from_file(tuning_root)


@pytest.mark.parametrize("apcs, rockets", [(1, 1), (4, 5), (3, 4)])
def test_apc_raid_apcs_from_1_to_4_and_rockets_from_1_to_5_are_read(tuning_root, apcs, rockets):
    rewrite(tuning_root, "raid_apcs = 2", f"raid_apcs = {apcs}")
    rewrite(tuning_root, "raid_apc_rockets = 5", f"raid_apc_rockets = {rockets}")

    composition = Tuning.from_file(tuning_root).composition
    assert (composition.raid_apcs, composition.raid_apc_rockets) == (apcs, rockets)


def test_apc_raid_the_unload_distance_and_the_reload_wait_are_read_from_the_file(tuning_root):
    rewrite(tuning_root, "unload_cells = 3", "unload_cells = 4")
    rewrite(tuning_root, "reload_wait_ticks = 75", "reload_wait_ticks = 100")

    assert Tuning.from_file(tuning_root).raids == RaidTuning(unload_cells=4, reload_wait_ticks=100)


@pytest.mark.parametrize(
    "old, new",
    [("unload_cells = 3", "unload_cells = -3"), ("reload_wait_ticks = 75\n", "")],
    ids=["negative cells", "missing wait"],
)
def test_apc_raid_an_unload_or_reload_key_that_is_missing_or_negative_is_named(tuning_root, old, new):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=rf"{old.split(' = ')[0]} in \[raids\] must be a whole number of at least 0"):
        Tuning.from_file(tuning_root)


# -- the army mix ----------------------------------------------------------------------------------

THE_REQUIREMENT_S_ROWS = {
    "allies": {
        "base": MixRow(infantry={"e1": 50, "e3": 15}, vehicle={"1tnk": 35}),
        "fix": MixRow(infantry={"e1": 40, "e3": 15}, vehicle={"2tnk": 45}),
        "dome": MixRow(infantry={"e1": 30, "e3": 10}, vehicle={"2tnk": 30, "arty": 30}),
    },
    "soviet": {
        "base": MixRow(infantry={"e1": 50, "e2": 10, "e3": 25}, vehicle={"ftrk": 15}),
        "fix": MixRow(infantry={"e1": 35, "e2": 10, "e3": 15}, vehicle={"3tnk": 40}),
        "dome": MixRow(infantry={"e1": 35, "e2": 5, "e3": 10}, vehicle={"3tnk": 25, "v2rl": 25}),
    },
}
# The Soviet rows of the tuning file's commented alternative with flak filler.
FLAK_SOVIET_ROWS = {
    "base": MixRow(infantry={"e1": 45, "e2": 15, "e3": 10}, vehicle={"ftrk": 30}),
    "fix": MixRow(infantry={"e1": 35, "e2": 15, "e3": 10}, vehicle={"3tnk": 40}),
    "dome": MixRow(infantry={"e1": 30, "e2": 15}, vehicle={"3tnk": 30, "v2rl": 25}),
}
FLAK_NOTE = "# An alternative to the Soviet rows above"


def test_the_mix_rows_are_the_requirement_s_six_built_in_and_in_the_tracked_file():
    assert Tuning().mix.rows == THE_REQUIREMENT_S_ROWS
    assert Tuning.from_file(REPO_ROOT).mix.rows == THE_REQUIREMENT_S_ROWS


def test_the_soviet_army_ships_without_flak_truck_filler_in_the_raid_and_the_air_shift():
    for tuning in (Tuning(), Tuning.from_file(REPO_ROOT)):
        assert (tuning.composition.raid_mixed_vehicles, tuning.anti_air.air_flak_points) == (0, 0)


def switch_to_the_flak_alternative(root: Path) -> None:
    """Put the commented Soviet rows of the tuning file under `root` in place of the ones in force, with three flak
    trucks in the mixed raid quota and ten points in the air shift, as the alternative's comment says it is played."""
    path = root / TUNING_FILE
    text = path.read_text(encoding="utf-8")
    marks = ("[mix.soviet.base]", FLAK_NOTE, "[base_defense]")
    shipped_from, note_at, rows_end = (text.index(mark) for mark in marks)
    commented = text[text.index("# [mix.soviet.base]", note_at) : rows_end].splitlines(keepends=True)
    alternative = "".join(line.removeprefix("#").removeprefix(" ") for line in commented)
    path.write_text(text[:shipped_from] + alternative + text[rows_end:], encoding="utf-8")
    rewrite(root, "raid_mixed_vehicles = 0", "raid_mixed_vehicles = 3")
    rewrite(root, "air_flak_points = 0", "air_flak_points = 10")


def test_the_alternative_with_flak_filler_in_the_tracked_file_loads_once_switched_in(tuning_root):
    switch_to_the_flak_alternative(tuning_root)

    tuning = Tuning.from_file(tuning_root)

    assert tuning.mix == MixTuning(rows={"allies": THE_REQUIREMENT_S_ROWS["allies"], "soviet": FLAK_SOVIET_ROWS})
    assert tuning.composition == dataclasses.replace(CompositionTuning(), raid_mixed_vehicles=3)
    assert tuning.anti_air.air_flak_points == 10


def test_the_mix_thresholds_are_the_requirement_s_built_in_and_in_the_tracked_file():
    for mix in (Tuning().mix, Tuning.from_file(REPO_ROOT).mix):
        assert (mix.counter_min_value, mix.counter_every_ticks, mix.max_share) == (1500, 250, 95)
        assert (mix.vehicle_heavy_share, mix.infantry_heavy_share) == (0.5, 0.7)
        assert (mix.vehicle_counter_points, mix.infantry_counter_points, mix.artillery_counter_points) == (15, 10, 5)


def test_every_built_in_row_has_shares_of_at_most_60_that_add_up_to_100():
    for family_rows in Tuning().mix.rows.values():
        for row in family_rows.values():
            shares = [*row.infantry.values(), *row.vehicle.values()]
            assert sum(shares) == 100 and max(shares) <= Tuning().mix.max_share


def test_a_row_of_the_file_replaces_the_built_in_one(tuning_root):
    rewrite(tuning_root, "infantry = { e1 = 50, e3 = 15 }", "infantry = { e1 = 40, e3 = 25 }")

    mix = Tuning.from_file(tuning_root).mix

    assert mix.rows["allies"]["base"] == MixRow(infantry={"e1": 40, "e3": 25}, vehicle={"1tnk": 35})
    assert mix.rows["soviet"] == THE_REQUIREMENT_S_ROWS["soviet"]


def test_a_threshold_of_the_mix_is_read_from_the_file(tuning_root):
    rewrite(tuning_root, "counter_min_value = 1500", "counter_min_value = 2000")

    assert Tuning.from_file(tuning_root).mix == MixTuning(counter_min_value=2000)


@pytest.mark.parametrize(
    "new, total",
    [("infantry = { e1 = 50, e3 = 10 }", 95), ("infantry = { e1 = 50, e3 = 15, e2 = 5 }", 105)],
    ids=["95", "105"],
)
def test_a_row_whose_shares_do_not_add_up_to_100_fails_the_loader_naming_its_table(tuning_root, new, total):
    rewrite(tuning_root, "infantry = { e1 = 50, e3 = 15 }", new)

    with pytest.raises(ConfigError) as raised:
        Tuning.from_file(tuning_root)

    assert str(raised.value).endswith(
        f"the shares in [mix.allies.base] must be points that add up to 100, not {total}."
    )
    assert str(tuning_root / TUNING_FILE) in str(raised.value)


@pytest.mark.parametrize(
    "share",
    ["96", "-5", "50.5", '"50"'],
    ids=["above the most of 95", "a negative number", "a fraction", "text"],
)
def test_a_share_that_is_not_a_whole_number_from_0_to_95_fails_the_loader_naming_its_item(tuning_root, share):
    rewrite(tuning_root, "infantry = { e1 = 50, e3 = 15 }", f"infantry = {{ e1 = {share}, e3 = 15 }}")

    with pytest.raises(
        ConfigError, match=r"e1 in \[mix\.allies\.base\.infantry\] must be a whole number of at least 0 and at most 95"
    ):
        Tuning.from_file(tuning_root)


def test_the_most_a_share_may_be_is_the_file_s_max_share(tuning_root):
    rewrite(tuning_root, "max_share = 95", "max_share = 45")

    with pytest.raises(ConfigError, match=r"e1 in \[mix\.allies\.base\.infantry\] must be .* at most 45"):
        Tuning.from_file(tuning_root)


SOVIET_DOME_ROW = "[mix.soviet.dome]\ninfantry = { e1 = 35, e2 = 5, e3 = 10 }\nvehicle = { 3tnk = 25, v2rl = 25 }\n"


@pytest.mark.parametrize(
    "old, missing",
    [(SOVIET_DOME_ROW, "[mix.soviet.dome]"), ("vehicle = { 1tnk = 35 }\n", "[mix.allies.base.vehicle]")],
    ids=["a stage", "a queue's table"],
)
def test_a_missing_stage_or_queue_table_of_the_mix_is_a_config_error_that_names_it(tuning_root, old, missing):
    rewrite(tuning_root, old, "")

    with pytest.raises(ConfigError, match=re.escape(f"must have a {missing} table")):
        Tuning.from_file(tuning_root)


def test_a_mix_without_the_rows_of_a_family_is_a_config_error_that_names_them(tuning_root):
    path = tuning_root / TUNING_FILE
    text = path.read_text(encoding="utf-8")
    soviet_rows = text[text.index("[mix.soviet.base]") : text.index("[base_defense]")]
    path.write_text(text.replace(soviet_rows, ""), encoding="utf-8")

    with pytest.raises(ConfigError, match=r"must have a \[mix\.soviet\] table"):
        Tuning.from_file(tuning_root)


FULL_ROW = "infantry = { e1 = 60 }\nvehicle = { 2tnk = 40 }\n\n"


@pytest.mark.parametrize(
    "old, new, named",
    [
        (
            "[mix.allies.base]",
            f"[mix.allies.tech]\n{FULL_ROW}[mix.allies.base]",
            "[mix.allies] has no setting named tech",
        ),
        ("[mix.allies.base]", f"[mix.ukraine.base]\n{FULL_ROW}[mix.allies.base]", "[mix] has no setting named ukraine"),
        (
            "vehicle = { 1tnk = 35 }",
            "vehicle = { 1tnk = 35 }\nair = { heli = 0 }",
            "[mix.allies.base] has no setting named air",
        ),
    ],
    ids=["a stage", "a family", "a table of a row"],
)
def test_a_stage_family_or_row_table_the_mix_does_not_have_is_rejected(tuning_root, old, new, named):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=re.escape(named)):
        Tuning.from_file(tuning_root)


# -- the tank rows of the army mix -------------------------------------------------------------------

# From the depot on, 12 tanks : 4 artillery or V2s : 8 rocket soldiers by count, as points of their value.
ALLIED_BUILD = MixRow(infantry={"e3": 15}, vehicle={"2tnk": 64, "arty": 21})
SOVIET_BUILD = MixRow(infantry={"e3": 12}, vehicle={"3tnk": 70, "v2rl": 18})
THE_REQUIREMENT_S_TANK_ROWS = {
    "allies": {
        "base": MixRow(infantry={"e3": 5}, vehicle={"1tnk": 95}),
        "fix": ALLIED_BUILD,
        "dome": ALLIED_BUILD,
    },
    "soviet": {
        "base": MixRow(infantry={"e3": 5}, vehicle={"ftrk": 95}),
        "fix": SOVIET_BUILD,
        "dome": SOVIET_BUILD,
        "stek": MixRow(infantry={"e3": 10}, vehicle={"3tnk": 40, "4tnk": 34, "v2rl": 16}),
    },
}
MAIN_FORCE_SHIPPED = 'main_force = "tanks"'
# The rows of the tank set share their infantry text, so a row is found by its table's header.
ALLIED_TANK_BASE_ROW = "[mix.tanks.allies.base]\ninfantry = { e3 = 5 }\nvehicle = { 1tnk = 95 }\n"
SOVIET_TANK_FIX_ROW = "[mix.tanks.soviet.fix]\ninfantry = { e3 = 12 }\nvehicle = { 3tnk = 70, v2rl = 18 }\n"
SOVIET_TANK_STEK_ROW = (
    "[mix.tanks.soviet.stek]\ninfantry = { e3 = 10 }\nvehicle = { 3tnk = 40, 4tnk = 34, v2rl = 16 }\n"
)


def test_core_tank_mix_the_tank_rows_are_the_requirement_s_seven_built_in_and_in_the_tracked_file():
    assert Tuning().mix.tank_rows == THE_REQUIREMENT_S_TANK_ROWS
    assert Tuning.from_file(REPO_ROOT).mix.tank_rows == THE_REQUIREMENT_S_TANK_ROWS


def test_mix_main_force_ships_tanks_and_mixed_in_the_file_is_read(tuning_root):
    rewrite(tuning_root, MAIN_FORCE_SHIPPED, 'main_force = "mixed"')

    assert Tuning().mix.main_force == Tuning.from_file(REPO_ROOT).mix.main_force == "tanks"
    assert Tuning.from_file(tuning_root).mix == MixTuning(main_force="mixed")


@pytest.mark.parametrize(
    "new",
    ['main_force = "heavy"', 'main_force = "Tanks"', "main_force = 1", ""],
    ids=["unknown", "case", "a number", "missing"],
)
def test_mix_a_main_force_that_is_not_mixed_or_tanks_raises_naming_the_key(tuning_root, new):
    rewrite(tuning_root, MAIN_FORCE_SHIPPED, new)

    with pytest.raises(ConfigError, match=r'main_force in \[mix\] must be "mixed" or "tanks"'):
        Tuning.from_file(tuning_root)


def test_mix_every_tank_row_has_shares_of_at_most_max_share_that_add_up_to_100():
    for family_rows in Tuning().mix.tank_rows.values():
        for row in family_rows.values():
            shares = [*row.infantry.values(), *row.vehicle.values()]
            assert sum(shares) == 100 and max(shares) <= Tuning().mix.max_share


def test_army_build_the_base_rows_keep_5_points_of_rockets_and_the_later_rows_rockets_alone_for_infantry():
    for family_rows in Tuning().mix.tank_rows.values():
        for stage, row in family_rows.items():
            assert row.infantry == {"e3": 5} if stage == "base" else set(row.infantry) == {"e3"}


def test_core_tank_mix_no_light_tank_or_flak_truck_past_the_base_row_and_the_mammoth_only_in_the_soviet_stek_row():
    for tuning in (Tuning(), Tuning.from_file(REPO_ROOT)):
        for family, family_rows in tuning.mix.tank_rows.items():
            for stage, row in family_rows.items():
                named = {*row.infantry, *row.vehicle}
                assert stage == "base" or named.isdisjoint({"1tnk", "ftrk"})
                assert ("4tnk" in named) == ((family, stage) == ("soviet", "stek"))


def test_mix_a_tank_row_of_the_file_replaces_the_built_in_one_and_leaves_the_mixed_rows(tuning_root):
    rewrite(
        tuning_root,
        ALLIED_TANK_BASE_ROW,
        "[mix.tanks.allies.base]\ninfantry = { e1 = 10, e3 = 5 }\nvehicle = { 1tnk = 85 }\n",
    )

    mix = Tuning.from_file(tuning_root).mix

    assert mix.tank_rows["allies"]["base"] == MixRow(infantry={"e1": 10, "e3": 5}, vehicle={"1tnk": 85})
    assert mix.rows == THE_REQUIREMENT_S_ROWS


@pytest.mark.parametrize(
    "new, total",
    [("infantry = { e3 = 0 }", 95), ("infantry = { e1 = 5, e3 = 5 }", 105)],
    ids=["95", "105"],
)
def test_mix_a_tank_row_whose_shares_do_not_add_up_to_100_fails_the_loader_naming_its_table(tuning_root, new, total):
    rewrite(tuning_root, ALLIED_TANK_BASE_ROW, f"[mix.tanks.allies.base]\n{new}\nvehicle = {{ 1tnk = 95 }}\n")

    with pytest.raises(ConfigError) as raised:
        Tuning.from_file(tuning_root)

    assert str(raised.value).endswith(
        f"the shares in [mix.tanks.allies.base] must be points that add up to 100, not {total}."
    )
    assert str(tuning_root / TUNING_FILE) in str(raised.value)


def test_mix_a_tank_share_above_max_share_fails_the_loader_naming_its_item(tuning_root):
    rewrite(
        tuning_root, SOVIET_TANK_FIX_ROW, "[mix.tanks.soviet.fix]\ninfantry = { e3 = 4 }\nvehicle = { 3tnk = 96 }\n"
    )

    with pytest.raises(
        ConfigError,
        match=r"3tnk in \[mix\.tanks\.soviet\.fix\.vehicle\] must be a whole number of at least 0 and at most 95",
    ):
        Tuning.from_file(tuning_root)


SOVIET_TANK_DOME_ROW = "[mix.tanks.soviet.dome]\ninfantry = { e3 = 12 }\nvehicle = { 3tnk = 70, v2rl = 18 }\n"


@pytest.mark.parametrize(
    "old, new, named",
    [
        (SOVIET_TANK_DOME_ROW, "", "must have a [mix.tanks.soviet.dome] table"),
        (SOVIET_TANK_STEK_ROW, "", "must have a [mix.tanks.soviet.stek] table"),
        (
            "[mix.tanks.allies.base]",
            f"[mix.tanks.allies.stek]\n{FULL_ROW}[mix.tanks.allies.base]",
            "[mix.tanks.allies] has no setting named stek",
        ),
        (
            "\n[mix.soviet.base]",
            f"\n[mix.soviet.stek]\n{FULL_ROW}[mix.soviet.base]",
            "[mix.soviet] has no setting named stek",
        ),
        (
            "[mix.tanks.allies.base]",
            f"[mix.tanks.ukraine.base]\n{FULL_ROW}[mix.tanks.allies.base]",
            "[mix.tanks] has no setting named ukraine",
        ),
        (
            "[mix.tanks.allies.base]",
            f"[mix.tanks.allies.tech]\n{FULL_ROW}[mix.tanks.allies.base]",
            "[mix.tanks.allies] has no setting named tech",
        ),
    ],
    ids=[
        "a missing stage",
        "a missing soviet stek row",
        "an allied stek row",
        "a mixed soviet stek row",
        "an unknown family",
        "an unknown stage",
    ],
)
def test_mix_a_tank_stage_or_family_missing_or_unknown_is_a_config_error_that_names_it(tuning_root, old, new, named):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=re.escape(named)):
        Tuning.from_file(tuning_root)


def test_mix_a_file_without_the_tank_rows_is_a_config_error_that_names_their_table(tuning_root):
    path = tuning_root / TUNING_FILE
    text = path.read_text(encoding="utf-8")
    tank_rows = text[text.index("[mix.tanks.allies.base]") : text.index("[mix.allies.base]")]
    path.write_text(text.replace(tank_rows, ""), encoding="utf-8")

    with pytest.raises(ConfigError, match=r"must have a \[mix\.tanks\] table"):
        Tuning.from_file(tuning_root)


def test_built_in_values_are_made_without_opening_a_file(monkeypatch):
    opened = []
    monkeypatch.setattr(builtins, "open", lambda *arguments, **options: opened.append(arguments))

    assert Tuning().upkeep.tech_min_cash == 1500
    assert opened == []


# -- the shipped configuration -------------------------------------------------------------------

# The switches that ship on as the watched game named at the head of the tracked file played them, by table.
SHIPPED_SWITCHES_ON = {
    "siege": ("march_when_clear",),
    "base_defense": ("keep_attack_army", "harvester_group_response"),
    "combat": ("sticky_attack_target",),
    "scouting": ("scout_missions", "scout_slots", "scout_wide_berth", "scout_spend_cap", "rush_detection"),
    "plan_rules": ("model_plan_hold",),
    "upkeep": (
        "mcv_first",
        "refinery_growth",
        "fresh_ore_by_load",
        "no_army_infantry",
        "defenses_on_threat_only",
        "economy_first",
        "lean_power",
        "refinery_claims_reached",
        "refinery_field_cap",
        "harvesters_to_fresh_ore",
    ),
    "expansion": (
        "mcv_safe_site",
        "mcv_new_area",
        "mcv_reserve_field",
        "mcv_repeat",
        "mcv_short_escort",
        "second_yard",
    ),
    "placement": (
        "refinery_spread",
        "creep_stall_skip",
        "second_line_local",
        "creep_from_opening",
        "creep_continuous",
        "refused_detection",
        "remote_yard_growth",
    ),
    "opening": ("lean_infantry", "defense_on_threat"),
    "raids": ("launchable_only",),
    "producers": ("tank_factories",),
}
# The other settings that game played, by table and key; its tank rows are THE_REQUIREMENT_S_TANK_ROWS.
SHIPPED_VALUES = {
    ("upkeep", "refineries_max"): 6,
    ("upkeep", "refineries_max_late"): 6,
    ("upkeep", "harvesters_max"): 22,
    ("mix", "main_force"): "tanks",
    ("mix", "max_share"): 95,
}
SHIPPED_SWITCH_COUNT = 37


def _setting(tuning: Tuning, table: str, key: str) -> object:
    """The value `tuning` holds for a key of a table of the tuning file."""
    return getattr(getattr(tuning, table), key)


def _built_in_and_tracked() -> tuple[Tuning, Tuning]:
    return Tuning(), Tuning.from_file(REPO_ROOT)


def test_shipped_the_tracked_file_equals_the_built_ins():
    assert Tuning.from_file(REPO_ROOT) == Tuning()


def test_shipped_every_switch_of_the_watched_game_is_on_built_in_and_in_the_tracked_file():
    switches = [(table, key) for table, keys in SHIPPED_SWITCHES_ON.items() for key in keys]

    assert len(set(switches)) == SHIPPED_SWITCH_COUNT
    for tuning in _built_in_and_tracked():
        assert {switch: _setting(tuning, *switch) for switch in switches} == dict.fromkeys(switches, True)


def test_shipped_the_values_and_tank_rows_of_the_watched_game_are_built_in_and_in_the_tracked_file():
    for tuning in _built_in_and_tracked():
        assert {setting: _setting(tuning, *setting) for setting in SHIPPED_VALUES} == SHIPPED_VALUES
        assert tuning.mix.tank_rows == THE_REQUIREMENT_S_TANK_ROWS


def test_shipped_the_model_plan_choice_and_commander_of_config_agent_toml_are_on_and_the_scout_choice_off():
    settings = RedAlertSettings.from_file(REPO_ROOT)

    assert (settings.model_plan_choice, settings.model_scout_choice, settings.model_commander) == (True, False, True)
    assert settings.plan_prompt == "briefing"


# -- reactive defenses -----------------------------------------------------------------------------------------------


def test_reactive_defenses_the_new_and_changed_keys_ship_with_their_values_built_in_and_in_the_tracked_file():
    shipped = Tuning.from_file(REPO_ROOT)

    for tuning in (shipped, Tuning()):
        assert tuning.clock.endgame_defenses is False
        assert tuning.opening.defense_on_unknown is False and tuning.opening.armor_near_base_cells == 18
        defense = tuning.base_defense
        assert (defense.extra_defense_seconds, defense.extra_defense_near_cells) == (120, 12)
        assert defense.extra_defense_strength_share == 1.5
        assert (defense.harass_tower, defense.harass_tower_max) == (True, 2)


@pytest.mark.parametrize(
    "old, new, read",
    [
        ("endgame_defenses = false", "endgame_defenses = true", lambda tuning: tuning.clock.endgame_defenses),
        ("defense_on_unknown = false", "defense_on_unknown = true", lambda tuning: tuning.opening.defense_on_unknown),
        ("harass_tower = true", "harass_tower = false", lambda tuning: not tuning.base_defense.harass_tower),
    ],
    ids=["endgame_defenses", "defense_on_unknown", "harass_tower"],
)
def test_reactive_defenses_each_switch_is_read_from_the_file(tuning_root, old, new, read):
    rewrite(tuning_root, old, new)

    assert read(Tuning.from_file(tuning_root))


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("endgame_defenses = false\n", "", r"endgame_defenses in \[clock\] must be true or false"),
        ("endgame_defenses = false", "endgame_defenses = 0", r"endgame_defenses in \[clock\] must be true or false"),
        ("defense_on_unknown = false\n", "", r"defense_on_unknown in \[opening\] must be true or false"),
        ("extra_defense_near_cells = 12", "extra_defense_near_cells = -1", r"extra_defense_near_cells in \[base_"),
        ("extra_defense_near_cells = 12", "extra_defense_near_cells = 1.5", r"extra_defense_near_cells"),
        ("extra_defense_strength_share = 1.5\n", "", r"extra_defense_strength_share in \[base_defense\]"),
        ("extra_defense_strength_share = 1.5", "extra_defense_strength_share = -1.5", r"extra_defense_strength_share"),
        ("harass_tower = true", 'harass_tower = "true"', r"harass_tower in \[base_defense\] must be true or false"),
        ("harass_tower_max = 2\n", "", r"harass_tower_max in \[base_defense\]"),
        ("harass_tower_max = 2", "harass_tower_max = true", r"harass_tower_max in \[base_defense\]"),
    ],
    ids=[
        "endgame switch missing",
        "endgame switch a number",
        "unknown switch missing",
        "negative near cells",
        "near cells a fraction",
        "share missing",
        "negative share",
        "harass switch a string",
        "harass max missing",
        "harass max a switch",
    ],
)
def test_reactive_defenses_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


# -- vehicle repair and the Allied support units -----------------------------------------------------


VEHICLE_REPAIR_COMBAT = {
    "vehicle_repair": True,
    "repair_window_seconds": 5,
    "repair_ttd_seconds": 8,
    "repair_ttd_max_hp": 70,
    "repair_below_hp": 35,
    "repair_min_cost": 700,
    "repair_done_hp": 90,
}
VEHICLE_REPAIR_COMPOSITION = {
    "mechanics_per_tanks": 3,
    "mechanics_max": 3,
    "mechanics_base": 0,
    "medics_per_infantry": 6,
    "medics_max": 0,
}


def test_vehicle_repair_ships_on_with_its_values_built_in_and_in_the_tracked_file():
    for tuning in (Tuning(), Tuning.from_file(REPO_ROOT)):
        assert {key: getattr(tuning.combat, key) for key in VEHICLE_REPAIR_COMBAT} == VEHICLE_REPAIR_COMBAT
        assert {key: getattr(tuning.composition, key) for key in VEHICLE_REPAIR_COMPOSITION} == (
            VEHICLE_REPAIR_COMPOSITION
        )


def test_vehicle_repair_false_and_other_values_in_the_file_are_read(tuning_root):
    rewrite(tuning_root, "vehicle_repair = true", "vehicle_repair = false")
    rewrite(tuning_root, "repair_done_hp = 90", "repair_done_hp = 80")
    rewrite(tuning_root, "medics_max = 0", "medics_max = 3")

    tuning = Tuning.from_file(tuning_root)

    assert (tuning.combat.vehicle_repair, tuning.combat.repair_done_hp, tuning.composition.medics_max) == (False, 80, 3)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("vehicle_repair = true\n", "", r"vehicle_repair in \[combat\] must be true or false"),
        ("vehicle_repair = true", "vehicle_repair = 1", r"vehicle_repair in \[combat\] must be true or false"),
        ("repair_window_seconds = 5", "repair_window_seconds = 0", r"repair_window_seconds in \[combat\]"),
        ("repair_ttd_seconds = 8\n", "", r"repair_ttd_seconds in \[combat\]"),
        ("repair_ttd_max_hp = 70", "repair_ttd_max_hp = 101", r"repair_ttd_max_hp in \[combat\]"),
        ("repair_below_hp = 35", "repair_below_hp = 3.5", r"repair_below_hp in \[combat\]"),
        ("repair_min_cost = 700", "repair_min_cost = -1", r"repair_min_cost in \[combat\]"),
        ("repair_done_hp = 90", "repair_done_hp = 0", r"repair_done_hp in \[combat\]"),
        ("mechanics_max = 3\n", "", r"mechanics_max in \[composition\]"),
        ("mechanics_base = 0", "mechanics_base = true", r"mechanics_base in \[composition\]"),
        ("mechanics_base = 0", "mechanics_base = -1", r"mechanics_base in \[composition\]"),
        ("mechanics_per_tanks = 3", "mechanics_per_tanks = 0", r"mechanics_per_tanks in \[composition\]"),
        ("medics_per_infantry = 6", "medics_per_infantry = 0", r"medics_per_infantry in \[composition\]"),
        ("medics_max = 0", 'medics_max = "0"', r"medics_max in \[composition\]"),
    ],
    ids=[
        "switch missing",
        "switch a number",
        "window of no second",
        "ttd missing",
        "ttd hp over 100",
        "below hp a fraction",
        "negative min cost",
        "done hp of 0",
        "max missing",
        "base a switch",
        "negative base",
        "a mechanic per no tank",
        "medics per no infantry",
        "medics max a string",
    ],
)
def test_vehicle_repair_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


# -- repair toward near mechanics ------------------------------------------------------------------


def test_repair_near_ships_60_hp_and_15_cells_built_in_and_in_the_tracked_file():
    for tuning in (Tuning(), Tuning.from_file(REPO_ROOT)):
        assert (tuning.combat.repair_early_hp, tuning.combat.repair_mechanic_reach_cells) == (60, 15)


def test_repair_near_other_values_in_the_file_are_read(tuning_root):
    rewrite(tuning_root, "repair_early_hp = 60", "repair_early_hp = 0")
    rewrite(tuning_root, "repair_mechanic_reach_cells = 15", "repair_mechanic_reach_cells = 20")

    tuning = Tuning.from_file(tuning_root)

    assert (tuning.combat.repair_early_hp, tuning.combat.repair_mechanic_reach_cells) == (0, 20)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("repair_early_hp = 60\n", "", r"repair_early_hp in \[combat\]"),
        ("repair_early_hp = 60", "repair_early_hp = 101", r"repair_early_hp in \[combat\]"),
        ("repair_mechanic_reach_cells = 15\n", "", r"repair_mechanic_reach_cells in \[combat\]"),
        ("repair_mechanic_reach_cells = 15", "repair_mechanic_reach_cells = 1.5", r"repair_mechanic_reach_cells in"),
    ],
    ids=["early hp missing", "early hp over 100", "reach missing", "reach a fraction"],
)
def test_repair_near_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


# -- the unknown unit's value in a fight -------------------------------------------------------------


def test_unknown_unit_value_is_300_built_in_and_in_the_tracked_file():
    for tuning in (Tuning(), Tuning.from_file(REPO_ROOT)):
        assert tuning.combat.unknown_unit_value == 300


def test_unknown_unit_value_in_the_file_is_read(tuning_root):
    rewrite(tuning_root, "unknown_unit_value = 300", "unknown_unit_value = 450")

    assert Tuning.from_file(tuning_root).combat.unknown_unit_value == 450


@pytest.mark.parametrize(
    "value", [None, "0", "-300", "2.5", "true"], ids=["missing", "zero", "negative", "a fraction", "a boolean"]
)
def test_unknown_unit_value_that_is_missing_or_no_positive_whole_number_is_named(tuning_root, value):
    rewrite(tuning_root, "unknown_unit_value = 300\n", "" if value is None else f"unknown_unit_value = {value}\n")

    with pytest.raises(ConfigError, match=r"unknown_unit_value in \[combat\]"):
        Tuning.from_file(tuning_root)


# -- the Soviet infantry counter -------------------------------------------------------------------


def test_soviet_infantry_counter_ships_on_at_0_6_20_and_15_built_in_and_in_the_tracked_file():
    for tuning in (Tuning(), Tuning.from_file(REPO_ROOT)):
        composition = tuning.composition
        shipped = (composition.enemy_infantry_share, composition.flamers_share, composition.v2_infantry_bonus)
        assert composition.soviet_infantry_counter and shipped == (0.6, 20, 15)


def test_soviet_infantry_counter_other_values_in_the_file_are_read(tuning_root):
    rewrite(tuning_root, "soviet_infantry_counter = true", "soviet_infantry_counter = false")
    rewrite(tuning_root, "enemy_infantry_share = 0.6", "enemy_infantry_share = 0.75")
    rewrite(tuning_root, "flamers_share = 20", "flamers_share = 0")
    rewrite(tuning_root, "v2_infantry_bonus = 15", "v2_infantry_bonus = 30")

    composition = Tuning.from_file(tuning_root).composition

    read = (composition.enemy_infantry_share, composition.flamers_share, composition.v2_infantry_bonus)
    assert not composition.soviet_infantry_counter and read == (0.75, 0, 30)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("soviet_infantry_counter = true\n", "", r"soviet_infantry_counter in \[composition\]"),
        ("soviet_infantry_counter = true", "soviet_infantry_counter = 1", r"soviet_infantry_counter in"),
        ("enemy_infantry_share = 0.6\n", "", r"enemy_infantry_share in \[composition\]"),
        ("flamers_share = 20", "flamers_share = 96", r"flamers_share in \[composition\]"),
        ("v2_infantry_bonus = 15", "v2_infantry_bonus = 1.5", r"v2_infantry_bonus in \[composition\]"),
    ],
    ids=["switch missing", "switch a number", "share missing", "flamers over 95", "bonus a fraction"],
)
def test_soviet_infantry_counter_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


# -- careful scouts ---------------------------------------------------------------------------------------------------


def test_scout_cautious_ships_on_with_its_keys_built_in_and_in_the_tracked_file_and_false_switches_it_off(tuning_root):
    rewrite(tuning_root, "scout_cautious = true", "scout_cautious = false")

    for scouting in (ScoutingTuning(), Tuning.from_file(REPO_ROOT).scouting):
        retreat = (scouting.scout_retreat_hp_drop, scouting.scout_outclass_cells, scouting.scout_retreat_seconds)
        assert scouting.scout_cautious is True and scouting.scout_enemy_base_cells == 8
        assert retreat == (1, 10, 8)
        # The retreat's drop is a key of its own: the wide berth's hurt areas keep theirs.
        assert scouting.hurt_min_drop == 5
    assert Tuning.from_file(tuning_root).scouting == ScoutingTuning(scout_cautious=False)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("scout_cautious = true\n", "", r"scout_cautious in \[scouting\] must be true or false"),
        ("scout_retreat_hp_drop = 1", "scout_retreat_hp_drop = 0", r"scout_retreat_hp_drop in \[scouting\]"),
        ("scout_outclass_cells = 10\n", "", r"scout_outclass_cells in \[scouting\]"),
        ("scout_enemy_base_cells = 8", "scout_enemy_base_cells = 8.5", r"scout_enemy_base_cells in \[scouting\]"),
        ("scout_retreat_seconds = 8", "scout_retreat_seconds = 0", r"scout_retreat_seconds in \[scouting\]"),
    ],
    ids=["switch missing", "drop of 0", "outclass cells missing", "base cells a fraction", "no retreat time"],
)
def test_scout_cautious_a_key_that_is_missing_or_out_of_its_range_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_allied_fast_scout_after_seconds_ships_at_300_built_in_and_in_the_tracked_file_and_the_file_s_value_counts(
    tuning_root,
):
    rewrite(tuning_root, "allied_fast_scout_after_seconds = 300", "allied_fast_scout_after_seconds = 0")

    assert ScoutingTuning().allied_fast_scout_after_seconds == 300
    assert Tuning.from_file(REPO_ROOT).scouting.allied_fast_scout_after_seconds == 300
    assert Tuning.from_file(tuning_root).scouting == ScoutingTuning(allied_fast_scout_after_seconds=0)


@pytest.mark.parametrize(
    "old, new",
    [
        ("allied_fast_scout_after_seconds = 300\n", ""),
        ("allied_fast_scout_after_seconds = 300", "allied_fast_scout_after_seconds = -1"),
    ],
    ids=["missing", "negative"],
)
def test_allied_fast_scout_after_seconds_missing_or_negative_is_named(tuning_root, old, new):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=r"allied_fast_scout_after_seconds in \[scouting\]"):
        Tuning.from_file(tuning_root)


def test_rush_scout_and_counter_keys_ship_with_their_values_built_in_and_in_the_tracked_file():
    shipped = Tuning.from_file(REPO_ROOT)

    for tuning in (shipped, Tuning()):
        scouting, opening = tuning.scouting, tuning.opening
        assert (scouting.rush_scout_direct, scouting.rush_scout_pass_cells, scouting.rush_exit_post_cells) == (
            True,
            5,
            13,
        )
        assert (scouting.rush_mass_infantry, scouting.rush_mass_before_seconds) == (8, 200)
        assert (opening.rush_counter_defenses, opening.rush_reserve_cash, opening.rush_min_tanks) == (2, 1200, 3)
        assert (opening.rush_hold_until_seconds, opening.rush_harvester_crush) == (330, True)
        assert opening.defense_on_unknown is False


@pytest.mark.parametrize(
    "old, new, read",
    [
        ("rush_scout_direct = true", "rush_scout_direct = false", lambda tuning: not tuning.scouting.rush_scout_direct),
        ("rush_exit_post_cells = 13", "rush_exit_post_cells = 10", lambda tuning: tuning.scouting.rush_exit_post_cells),
        ("rush_mass_infantry = 8", "rush_mass_infantry = 9", lambda tuning: tuning.scouting.rush_mass_infantry == 9),
        ("rush_counter_defenses = 2", "rush_counter_defenses = 0", lambda t: t.opening.rush_counter_defenses == 0),
        ("rush_reserve_cash = 1200", "rush_reserve_cash = 900", lambda t: t.opening.rush_reserve_cash == 900),
        ("rush_hold_until_seconds = 330", "rush_hold_until_seconds = 1", lambda t: t.opening.rush_hold_until_seconds),
        ("rush_harvester_crush = true", "rush_harvester_crush = false", lambda t: not t.opening.rush_harvester_crush),
    ],
    ids=["direct", "post", "mass", "defenses", "reserve", "hold", "harvester crush"],
)
def test_rush_scout_and_counter_each_key_is_read_from_the_file(tuning_root, old, new, read):
    rewrite(tuning_root, old, new)

    assert read(Tuning.from_file(tuning_root))


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("rush_scout_direct = true", "rush_scout_direct = 1", r"rush_scout_direct in \[scouting\] must be true or"),
        ("rush_scout_pass_cells = 5\n", "", r"rush_scout_pass_cells in \[scouting\]"),
        ("rush_mass_before_seconds = 200", "rush_mass_before_seconds = 1.5", r"rush_mass_before_seconds"),
        ("rush_min_tanks = 3\n", "", r"rush_min_tanks in \[opening\]"),
        ("rush_harvester_crush = true", 'rush_harvester_crush = "yes"', r"rush_harvester_crush in \[opening\]"),
    ],
    ids=["direct a number", "pass missing", "mass seconds fraction", "min tanks missing", "crush text"],
)
def test_rush_scout_and_counter_a_key_that_is_missing_or_of_the_wrong_kind_is_named(tuning_root, old, new, message):
    rewrite(tuning_root, old, new)

    with pytest.raises(ConfigError, match=message):
        Tuning.from_file(tuning_root)


def test_tank_charge_artillery_ships_on_built_in_and_in_the_tracked_file_and_false_in_the_file_switches_it_off(
    tuning_root,
):
    rewrite(tuning_root, "tank_charge_artillery = true", "tank_charge_artillery = false")

    assert Tuning.from_file(REPO_ROOT).combat.tank_charge_artillery is True
    assert CombatTuning().tank_charge_artillery is True
    assert Tuning.from_file(tuning_root).combat.tank_charge_artillery is False


@pytest.mark.parametrize("new", ["", "tank_charge_artillery = 1"], ids=["missing", "a number"])
def test_tank_charge_artillery_missing_or_not_a_switch_is_named(tuning_root, new):
    rewrite(tuning_root, "tank_charge_artillery = true", new)

    with pytest.raises(ConfigError, match=r"tank_charge_artillery in \[combat\] must be true or false"):
        Tuning.from_file(tuning_root)


def test_own_field_cells_ships_12_built_in_and_in_the_tracked_file_and_the_file_s_value_is_read(tuning_root):
    rewrite(tuning_root, "own_field_cells = 12", "own_field_cells = 9")

    assert Tuning.from_file(REPO_ROOT).scouting.own_field_cells == ScoutingTuning().own_field_cells == 12
    assert Tuning.from_file(tuning_root).scouting.own_field_cells == 9


@pytest.mark.parametrize("new", ["", "own_field_cells = 1.5"], ids=["missing", "a fraction"])
def test_own_field_cells_missing_or_not_a_whole_number_is_named(tuning_root, new):
    rewrite(tuning_root, "own_field_cells = 12", new)

    with pytest.raises(ConfigError, match=r"own_field_cells in \[scouting\]"):
        Tuning.from_file(tuning_root)
