"""The Red Alert settings of `config/agent.toml`: the model switches, the briefing switch, the plan prompt and the
sparring seat plans, read strictly."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from ucla_agent.core.config import ConfigError
from ucla_agent.games.redalert.settings import RedAlertSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS = "[models]\nautostart = false\n\n"
SWITCHES_OFF = (
    "[redalert]\nmodel_plan_choice = false\nmodel_commands = false\nmodel_briefing = false\nmodel_scout_choice = false\n"
    'model_commander = false\nplan_prompt = "briefing"\n'
)


def root_with(tmp_path: Path, text: str) -> Path:
    """A folder standing in for the repository root, whose `config/agent.toml` holds `text` after `[models]`."""
    path = tmp_path / "config" / "agent.toml"
    path.parent.mkdir(parents=True)
    path.write_text(MODELS + text, encoding="utf-8")
    return tmp_path


def test_the_tracked_file_turns_the_model_plan_choice_the_commander_and_the_briefing_on_and_pins_no_seat():
    assert RedAlertSettings.from_file(REPO_ROOT) == RedAlertSettings(
        model_plan_choice=True,
        model_commands=False,
        model_briefing=True,
        model_scout_choice=False,
        model_commander=True,
        plan_prompt="briefing",
        seat_plans={},
    )


def test_the_built_in_settings_turn_every_model_switch_and_the_briefing_off():
    assert RedAlertSettings() == RedAlertSettings(
        model_plan_choice=False,
        model_commands=False,
        model_briefing=False,
        model_scout_choice=False,
        model_commander=False,
        plan_prompt="briefing",
        seat_plans={},
    )


def test_plan_prompt_the_tracked_file_ships_briefing_as_built_in():
    assert RedAlertSettings.from_file(REPO_ROOT).plan_prompt == RedAlertSettings().plan_prompt == "briefing"


@pytest.mark.parametrize("value", ["briefing", "compact"])
def test_plan_prompt_briefing_and_compact_are_read(tmp_path, value):
    root = root_with(tmp_path, SWITCHES_OFF.replace('"briefing"', f'"{value}"'))

    assert RedAlertSettings.from_file(root).plan_prompt == value


def test_the_switches_and_the_seat_plans_of_the_file_are_read(tmp_path):
    root = root_with(
        tmp_path,
        "[redalert]\nmodel_plan_choice = true\nmodel_commands = true\nmodel_briefing = true\nmodel_scout_choice = true\n"
        'model_commander = true\nplan_prompt = "compact"\n\n[redalert.sparring]\nseat_plans = {Multi0 = "PRESS", Multi1 = "HOLD"}\n',
    )

    settings = RedAlertSettings.from_file(root)

    assert (settings.model_plan_choice, settings.model_commands, settings.model_briefing) == (True, True, True)
    assert (settings.model_scout_choice, settings.model_commander, settings.plan_prompt) == (True, True, "compact")
    assert dict(settings.seat_plans) == {"Multi0": "PRESS", "Multi1": "HOLD"}


def test_the_sparring_table_may_be_left_out(tmp_path):
    assert RedAlertSettings.from_file(root_with(tmp_path, SWITCHES_OFF)) == RedAlertSettings()


@pytest.mark.parametrize(
    "text, named",
    [
        ("[redalert]\nmodel_plan_choice = 1\nmodel_commands = false\n", "model_plan_choice in [redalert]"),
        ("[redalert]\nmodel_plan_choice = false\n", "model_commands in [redalert]"),
        ("[redalert]\nmodel_plan_choice = false\nmodel_commands = false\n", "model_briefing in [redalert]"),
        (SWITCHES_OFF.replace("model_briefing = false", 'model_briefing = "on"'), "model_briefing in [redalert]"),
        (SWITCHES_OFF.replace("model_scout_choice = false\n", ""), "model_scout_choice in [redalert]"),
        (
            SWITCHES_OFF.replace("model_scout_choice = false", "model_scout_choice = 0"),
            "model_scout_choice in [redalert]",
        ),
        (SWITCHES_OFF.replace("model_commander = false\n", ""), "model_commander in [redalert]"),
        (SWITCHES_OFF.replace("model_commander = false", 'model_commander = "no"'), "model_commander in [redalert]"),
        (SWITCHES_OFF.replace('plan_prompt = "briefing"\n', ""), "plan_prompt in [redalert]"),
        (SWITCHES_OFF.replace('"briefing"', '"short"'), 'plan_prompt in [redalert] must be "briefing" or "compact"'),
        (SWITCHES_OFF.replace('"briefing"', "true"), "plan_prompt in [redalert]"),
        (SWITCHES_OFF + "model_comands = true\n", "[redalert] has no setting named model_comands"),
        (SWITCHES_OFF + '[redalert.sparring]\nseat_plans = {Multi1 = "STRIKE"}\n', "Multi1 in [redalert.sparring."),
        (SWITCHES_OFF + "[redalert.sparring]\nseat_plan = {}\n", "[redalert.sparring] has no setting named seat_plan"),
        ("[decide]\n", "must have a [redalert] table"),
    ],
    ids=[
        "a switch that is not true or false",
        "a missing switch",
        "a missing briefing switch",
        "a briefing switch that is not true or false",
        "a missing scout choice switch",
        "a scout choice switch that is a number",
        "a missing commander switch",
        "a commander switch that is a string",
        "a missing plan prompt",
        "a plan prompt other than briefing or compact",
        "a plan prompt that is not a string",
        "an unknown key",
        "a plan no seat may be pinned to",
        "an unknown sparring key",
        "no table",
    ],
)
def test_a_setting_that_cannot_be_used_is_a_config_error_that_names_the_file_and_the_key(tmp_path, text, named):
    root = root_with(tmp_path, text)

    with pytest.raises(ConfigError) as raised:
        RedAlertSettings.from_file(root)

    assert named in str(raised.value) and str(root / "config" / "agent.toml") in str(raised.value)


def test_a_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="agent.toml was not found"):
        RedAlertSettings.from_file(tmp_path)


def test_the_settings_cannot_be_changed_once_read():
    settings = RedAlertSettings.from_file(REPO_ROOT)

    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.model_plan_choice = True
    with pytest.raises(TypeError):
        settings.seat_plans["Multi0"] = "RAID"
