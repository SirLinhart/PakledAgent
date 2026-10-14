"""The readiness checks that need nothing beyond the machine: config, secrets_scan, games and records."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ucla_agent.check import local
from ucla_agent.check.line import FAIL, PASS, CheckLine
from ucla_agent.core import registry
from ucla_agent.core.records import EventWriter

# Shaped like an Official Agent Key (eak_live_ and 64 hex characters), and no real one.
KEY_SHAPED = "eak_live_" + "0123456789abcdef" * 4


def test_a_check_line_reads_status_name_and_detail():
    assert CheckLine(PASS, "records", "fine").text() == "PASS records: fine"


# -- config -----------------------------------------------------------------------------------------


def test_the_tracked_configuration_passes(checked_project):
    assert local.check_config(checked_project) == CheckLine(
        PASS, "config", "14 files under config/ parse and pass their schema"
    )


def test_one_fault_reached_by_two_readers_is_named_once(checked_project):
    models = checked_project / "config" / "models.toml"
    models.write_text(models.read_text(encoding="utf-8").replace("port = 11500", "port = 0"), encoding="utf-8")

    line = local.check_config(checked_project)

    assert line.status == FAIL and line.detail.count("port in [server]") == 1


@pytest.mark.parametrize(
    "file, old, new, said",
    [
        ("config/models.toml", "required = false", "requird = false", "required in [roles.fast]"),
        ("config/redalert/tuning.toml", "[", "[[", "is not valid TOML"),
        ("config/agent.toml", '["systemone:jev9b"]', '["local:huge"]', "names the role huge"),
        ("config/agent.toml", "model_plan_choice = true", 'model_plan_choice = "no"', "model_plan_choice"),
        ("config/agent.toml", "autostart = true", "autostart = 0", "autostart"),
        ("config/agent.toml", 'autostart_roles = ["strong", "jev9b"]', 'autostart_roles = "strong"', "autostart_roles"),
        ("config/werewolf/tuning.toml", "injection_attempt = 1.5", "injection_attempt = 0", "injection_attempt"),
        ("config/agent.toml", "model_choices = true", 'model_choices = "yes"', "model_choices in [werewolf]"),
        ("config/pokemon/data/moves.json", '"surgingstrikes":{', '"surgingstrikes":', "is not valid JSON"),
        (
            "config/pokemon/data/natures.json",
            '"adamant":{"atk"',
            '"adamant":{"attack"',
            "a record the agent cannot read",
        ),
        (
            "config/pokemon/practice_cards.json",
            '"level": 50',
            '"level": 100',
            "is not a card catalog the practice harness can read",
        ),
        ("config/pokemon/tuning.toml", "ko_bonus = 80.0", "ko_bonus = -1", "ko_bonus in [turn]"),
        ("config/agent.toml", "top_k = 4", "top_k = 0", "top_k in [pokemon]"),
        ("config/redalert/glossary.toml", 'side = "Soviets"', 'side = "Russia"', "side in [barr]"),
    ],
    ids=[
        "misspelled key",
        "not TOML",
        "chain of an unknown role",
        "red alert switch",
        "agent table",
        "autostart roles not a list",
        "werewolf",
        "werewolf switch",
        "pokemon table not JSON",
        "pokemon record off its shape",
        "practice card off its rules",
        "pokemon tuning",
        "pokemon settings",
        "red alert glossary",
    ],
)
def test_a_file_off_its_schema_fails_and_is_named(checked_project, file, old, new, said):
    path = checked_project / file
    path.write_text(path.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")

    line = local.check_config(checked_project)

    assert line.status == FAIL and said in line.detail and path.name in line.detail


def test_a_missing_file_and_a_file_nothing_reads_both_fail(checked_project):
    (checked_project / "config" / "redalert" / "items.toml").unlink()
    (checked_project / "config" / "notes.txt").write_text("hello", encoding="utf-8")

    line = local.check_config(checked_project)

    assert line.status == FAIL
    assert "config/notes.txt is read by nothing" in line.detail and "items.toml was not found" in line.detail


def test_a_missing_pokemon_data_table_fails_and_is_named(checked_project):
    (checked_project / "config" / "pokemon" / "data" / "species.json").unlink()

    line = local.check_config(checked_project)

    assert line.status == FAIL and "species.json was not found" in line.detail


def test_a_missing_pokemon_practice_card_catalog_fails_and_is_named(checked_project):
    (checked_project / "config" / "pokemon" / "practice_cards.json").unlink()

    line = local.check_config(checked_project)

    assert line.status == FAIL and "practice_cards.json was not found" in line.detail


def test_pokemon_data_whose_license_note_names_no_mit_license_fails_and_is_named(checked_project):
    (checked_project / "config" / "pokemon" / "data" / "LICENSE-DATA.md").write_text("# Data\n", encoding="utf-8")

    line = local.check_config(checked_project)

    assert line.status == FAIL and "LICENSE-DATA.md names no MIT License" in line.detail


@pytest.mark.parametrize(
    "name, text, said",
    [
        ("rules-20261008.json", '{"game_type": "red_alert", "rules": {"units": {}}}', None),
        ("rules-20261008.json", "{not json", "config/redalert/captured/rules-20261008.json is not valid JSON"),
        ("rules-20261008.json", '{"maps": []}', "is no captured game reference: it has no rules table"),
        ("rules-latest.json", '{"rules": {}}', "config/redalert/captured/rules-latest.json is read by nothing"),
    ],
    ids=["captured reference", "not JSON", "no rules", "not a capture's name"],
)
def test_a_rules_file_captured_from_a_match_is_reference_material_that_has_to_parse(checked_project, name, text, said):
    captured = checked_project / "config" / "redalert" / "captured"
    captured.mkdir()
    (captured / name).write_text(text, encoding="utf-8")

    line = local.check_config(checked_project)

    if said is None:
        assert line == CheckLine(PASS, "config", "15 files under config/ parse and pass their schema")
    else:
        assert line.status == FAIL and said in line.detail


# -- secrets_scan -----------------------------------------------------------------------------------


def test_a_repository_without_a_secret_passes(checked_project):
    assert local.check_secrets_scan(checked_project) == CheckLine(
        PASS, "secrets_scan", "no file git tracks holds a secret"
    )


def test_a_tracked_secret_fails_naming_its_place_and_never_its_value(checked_project, git):
    (checked_project / "leak.txt").write_text(f"one\nkey = {KEY_SHAPED}\n", encoding="utf-8")
    git(checked_project, "add", "leak.txt")

    line = local.check_secrets_scan(checked_project)

    assert line == CheckLine(FAIL, "secrets_scan", "1 line(s) of tracked files hold a secret: leak.txt:2")


def test_a_scan_that_cannot_list_the_files_fails_with_what_it_said(checked_project, monkeypatch):
    # With nothing on the search path but Python's own folder, the scan cannot run git.
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent))

    line = local.check_secrets_scan(checked_project)

    assert line.status == FAIL and line.detail.startswith("scripts/scan_secrets.py exited with code 2: Could not list")


# -- games ------------------------------------------------------------------------------------------


def test_red_alert_werewolf_and_pokemon_are_ready_and_honor_of_kings_is_not_available(checked_project):
    found = local.check_games()

    assert found.ready == ("red_alert", "werewolf", "pokemon_vgc_doubles_draft")
    assert found.line == CheckLine(
        PASS,
        "games",
        "red_alert ready, werewolf ready, pokemon_vgc_doubles_draft ready, honor_of_kings not available",
    )


def test_a_registered_factory_that_raises_fails_and_its_game_is_not_ready(checked_project, monkeypatch):
    def broken():
        raise RuntimeError("no rules file")

    monkeypatch.setitem(registry.GAME_FACTORIES, "werewolf", broken)

    found = local.check_games()

    assert found.line.status == FAIL and found.ready == ("red_alert", "pokemon_vgc_doubles_draft")
    assert "werewolf could not be made (RuntimeError: no rules file)" in found.line.detail


# -- records ----------------------------------------------------------------------------------------


def probe_lines(root) -> list[dict]:
    return [json.loads(line) for line in (root / local.PROBE_FILE).read_text(encoding="utf-8").splitlines()]


def test_a_probe_event_is_written_through_the_record_writer_and_read_back(run_folder):
    line = local.check_records(run_folder)

    (probe,) = probe_lines(run_folder)
    assert line == CheckLine(PASS, "records", "a probe event was written to runs/.check/probe.jsonl and read back")
    assert probe["seq"] == 0 and probe["kind"] == "probe" and len(probe["data"]["probe"]) == 32


def test_each_run_leaves_only_its_own_probe(run_folder):
    local.check_records(run_folder)
    first = probe_lines(run_folder)

    local.check_records(run_folder)

    (second,) = probe_lines(run_folder)
    assert first[0]["data"] != second["data"]


def test_a_probe_the_writer_dropped_fails(run_folder, monkeypatch):
    monkeypatch.setattr(EventWriter, "submit", lambda self, kind, data: None)

    line = local.check_records(run_folder)

    assert line == CheckLine(FAIL, "records", "the probe event did not reach runs/.check/probe.jsonl")


def test_a_probe_folder_that_cannot_be_made_fails(run_folder):
    (run_folder / "runs").mkdir()
    (run_folder / "runs" / ".check").write_text("a file where the folder should be", encoding="utf-8")

    line = local.check_records(run_folder)

    assert line.status == FAIL and line.detail.startswith("runs/.check/probe.jsonl could not be written or read:")
