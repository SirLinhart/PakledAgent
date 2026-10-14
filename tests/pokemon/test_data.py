"""The shipped Pokémon game data: the build script that writes it and the lookups the agent reads it through."""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path

import pytest

from ucla_agent.core.config import ConfigError
from ucla_agent.games.pokemon import data

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_FOLDER = REPO_ROOT / "config" / "pokemon" / "data"
BUILD_SCRIPT = "build_pokemon_data.py"
# The untracked poke-env reference copy the build reads; tests never read it except to check the build.
REFERENCE_STATIC = (
    REPO_ROOT / "docs" / "reference" / "research" / "pokemon" / "poke-env" / "src" / "poke_env" / "data" / "static"
)
DATA_FILES = {"species.json", "moves.json", "typechart.json", "natures.json", "LICENSE-DATA.md"}
LOAD_BUDGET_SECONDS = 0.5
SIZE_BUDGET_BYTES = 6 * 1024 * 1024
needs_reference_copy = pytest.mark.skipif(
    not REFERENCE_STATIC.is_dir(), reason="the poke-env reference copy is not on this machine"
)


def _digests(folder: Path) -> dict[str, str]:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in folder.iterdir() if path.is_file()}


# -- the build script --------------------------------------------------------------------------------


def test_the_committed_folder_holds_the_four_tables_and_the_license_note():
    assert {path.name for path in DATA_FOLDER.iterdir()} == DATA_FILES


def test_the_license_note_names_the_source_and_its_mit_license():
    note = (DATA_FOLDER / "LICENSE-DATA.md").read_text(encoding="utf-8")

    assert "https://github.com/hsahovic/poke-env" in note and "MIT License" in note
    assert "Copyright (c) 2019 hsahovic" in note


@needs_reference_copy
def test_running_the_build_script_again_reproduces_the_committed_files_byte_for_byte(tmp_path, run_script):
    completed = run_script(tmp_path, BUILD_SCRIPT, "--source", str(REFERENCE_STATIC), "--out", str(tmp_path / "out"))

    assert completed.returncode == 0, completed.stderr
    assert _digests(tmp_path / "out") == _digests(DATA_FOLDER)


@needs_reference_copy
def test_the_build_is_the_same_bytes_in_memory_as_on_disk(load_script):
    built = load_script(BUILD_SCRIPT).build(REFERENCE_STATIC)

    assert {name: hashlib.sha256(content).hexdigest() for name, content in built.items()} == _digests(DATA_FOLDER)


def test_the_build_script_without_a_source_folder_says_so_and_exits_1(tmp_path, run_script):
    completed = run_script(tmp_path, BUILD_SCRIPT, "--source", str(tmp_path / "missing"), "--out", str(tmp_path))

    assert completed.returncode == 1
    assert "No static data folder" in completed.stderr
    assert list(tmp_path.iterdir()) == []


# -- the lookups -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [("Urshifu-Rapid-Strike", "urshifurapidstrike"), ("Farfetch’d", "farfetchd"), ("Mr. Mime", "mrmime"), ("", "")],
)
def test_to_id_keeps_lowercase_letters_and_digits_only(name, expected):
    assert data.to_id(name) == expected


def test_a_species_resolves_by_display_name_and_by_id_to_the_same_record():
    by_name = data.species("Urshifu-Rapid-Strike")

    assert by_name is not None and by_name == data.species("urshifurapidstrike")
    assert by_name.types == ("Fighting", "Water")
    assert by_name.base_stats["spe"] == 97 and by_name.name == "Urshifu-Rapid-Strike"


def test_a_species_has_its_abilities_in_slot_order_and_its_weight():
    dragonite = data.species("Dragonite")

    assert dragonite.abilities == ("Inner Focus", "Multiscale") and dragonite.weight_kg == 210
    assert set(dragonite.base_stats) == {"hp", "atk", "def", "spa", "spd", "spe"}


def test_surging_strikes_is_a_physical_water_move_of_three_25_power_hits_that_always_crit():
    surging_strikes = data.move("Surging Strikes")

    assert (surging_strikes.type, surging_strikes.category, surging_strikes.base_power) == ("Water", "Physical", 25)
    assert surging_strikes.multihit == 3 and surging_strikes.will_crit is True
    assert {"contact", "protect", "punch"} == surging_strikes.flags


def test_a_move_keeps_its_hit_range_drain_recoil_and_stat_changes():
    assert data.move("Bullet Seed").multihit == (2, 5)
    assert data.move("Giga Drain").drain == (1, 2) and data.move("Flare Blitz").recoil == (33, 100)
    assert data.move("Icy Wind").stat_changes == (data.StatChange("target", 100, {"spe": -1}),)
    assert data.move("Close Combat").stat_changes == (data.StatChange("user", 100, {"def": -1, "spd": -1}),)
    assert data.move("Protect").accuracy is True and data.move("Protect").priority == 4


@pytest.mark.parametrize(
    "attack_type, defender_types, expected",
    [("Water", ["Fire"], 2), ("Ground", ["Flying"], 0), ("Fire", ["Water", "Dragon"], 0.25), ("ice", ["DRAGON"], 2)],
)
def test_type_multiplier_multiplies_the_chart_over_the_defender_types(attack_type, defender_types, expected):
    assert data.type_multiplier(attack_type, defender_types) == expected


def test_adamant_raises_attack_and_lowers_special_attack():
    adamant = data.nature("Adamant")

    assert adamant["atk"] == 1.1 and adamant["spa"] == 0.9
    assert adamant["def"] == adamant["spd"] == adamant["spe"] == 1


@pytest.mark.parametrize(
    "lookup",
    [
        lambda: data.species("Missingmon"),
        lambda: data.move("Hyper Mega Beam"),
        lambda: data.nature("Grumpy"),
        lambda: data.type_multiplier("Sound", ["Fire"]),
        lambda: data.type_multiplier("Fire", ["Fire", "Cosmic"]),
    ],
    ids=["species", "move", "nature", "attacking type", "defending type"],
)
def test_an_unknown_name_gives_none(lookup):
    assert lookup() is None


def test_loading_all_files_takes_under_half_a_second_and_they_total_under_6_mb():
    started = time.perf_counter()
    loaded = data.load(DATA_FOLDER)
    elapsed = time.perf_counter() - started

    assert elapsed < LOAD_BUDGET_SECONDS
    assert sum(path.stat().st_size for path in DATA_FOLDER.iterdir()) < SIZE_BUDGET_BYTES
    assert len(loaded.species) > 1000 and len(loaded.moves) > 900 and len(loaded.type_chart) == 18


def test_the_shipped_data_is_read_once_per_process():
    assert data.shipped() is data.shipped()


def test_the_shipped_data_is_the_package_projects_even_when_another_root_is_named(monkeypatch, tmp_path):
    monkeypatch.setenv("UCLA_AGENT_ROOT", str(tmp_path))
    data.shipped.cache_clear()

    assert data.species("Dragonite") is not None


@pytest.mark.parametrize(
    "file, text, said",
    [
        ("moves.json", "{not json", "moves.json is not valid JSON"),
        ("species.json", '{"bulbasaur": {"name": "Bulbasaur"}}', "species.json has a record the agent cannot read"),
        ("natures.json", '{"adamant": 1.1}', "natures.json has a record the agent cannot read"),
        ("natures.json", '{"adamant": {"atk": 1.1}}', "natures.json has a record the agent cannot read"),
        ("typechart.json", '{"fire": {"water": "half"}}', "typechart.json has a record the agent cannot read"),
    ],
)
def test_a_malformed_table_raises_a_config_error_naming_the_file(tmp_path, file, text, said):
    shutil.copytree(DATA_FOLDER, tmp_path, dirs_exist_ok=True)
    (tmp_path / file).write_text(text, encoding="utf-8")

    with pytest.raises(ConfigError, match=said):
        data.load(tmp_path)


def test_a_missing_table_raises_a_config_error_naming_the_file(tmp_path):
    with pytest.raises(ConfigError, match="species.json was not found"):
        data.load(tmp_path)


def test_the_license_note_is_read_and_a_missing_one_raises_a_config_error(tmp_path):
    assert "MIT License" in data.read_license_note(DATA_FOLDER / data.LICENSE_FILE)
    with pytest.raises(ConfigError, match="LICENSE-DATA.md was not found"):
        data.read_license_note(tmp_path / data.LICENSE_FILE)


def test_a_license_note_that_names_no_mit_license_raises_a_config_error(tmp_path):
    (tmp_path / data.LICENSE_FILE).write_text("# Data\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="names no MIT License"):
        data.read_license_note(tmp_path / data.LICENSE_FILE)


def test_a_move_says_whether_it_has_a_secondary_effect():
    assert data.move("Flamethrower").has_secondary and data.move("Icy Wind").has_secondary
    assert not data.move("Earthquake").has_secondary and not data.move("Close Combat").has_secondary
