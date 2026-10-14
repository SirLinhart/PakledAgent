"""The Pokémon settings: the `[pokemon]` table of config/agent.toml and config/pokemon/tuning.toml, read strictly."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ucla_agent.core.config import ConfigError
from ucla_agent.games.pokemon.settings import CHOICE_CHAIN, PokemonSettings
from ucla_agent.games.pokemon.tuning import (
    BriefingTuning,
    DraftTuning,
    MaximinTuning,
    PreviewTuning,
    Tuning,
    TurnTuning,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_TOML = Path("config") / "agent.toml"
TUNING_TOML = Path("config") / "pokemon" / "tuning.toml"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A copy of the tracked configuration that a test may edit."""
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    return tmp_path


def _edit(root: Path, relative: Path, old: str, new: str) -> None:
    """Replace the first `old` of the copied file; in agent.toml, from the `[pokemon]` table on, since other tables
    have keys of the same names (`model_briefing` in `[redalert]`)."""
    path = root / relative
    text = path.read_text(encoding="utf-8")
    start = text.index("\n[pokemon]\n") + 1 if relative == AGENT_TOML else 0
    assert old in text[start:]
    path.write_text(text[:start] + text[start:].replace(old, new, 1), encoding="utf-8")


def test_the_tracked_settings_ask_the_model_with_the_requirement_s_deadlines():
    settings = PokemonSettings.from_files(REPO_ROOT)

    assert settings.model_choices is True and settings.top_k == 4
    assert (settings.draft_deadline_ms, settings.preview_deadline_ms, settings.turn_deadline_ms) == (6000, 20000, 20000)
    assert CHOICE_CHAIN == "pokemon_choice"


def test_the_tracked_settings_hold_the_clock_deadline_keys():
    settings = PokemonSettings.from_files(REPO_ROOT)

    assert (settings.decision_margin_seconds, settings.bank_share) == (10, 0.10)
    assert (settings.bank_reserve_seconds, settings.no_clock_deadline_ms) == (90, 5000)


@pytest.mark.parametrize(
    "old, new, said",
    [
        ("decision_margin_seconds = 10\n", "", "decision_margin_seconds in [pokemon] must be a number of at least 0"),
        ("bank_share = 0.10\n", "", "bank_share in [pokemon] must be a number of at least 0"),
        ("bank_reserve_seconds = 90\n", "", "bank_reserve_seconds in [pokemon] must be a number of at least 0"),
        ("no_clock_deadline_ms = 5000\n", "", "no_clock_deadline_ms in [pokemon] must be a whole number"),
        ("bank_reserve_seconds = 90", 'bank_reserve_seconds = "ninety"', "bank_reserve_seconds in [pokemon]"),
        ("bank_share = 0.10", 'bank_share = "a tenth"', "bank_share in [pokemon]"),
        ("decision_margin_seconds = 10", "decision_margin_seconds = true", "decision_margin_seconds in [pokemon]"),
        ("no_clock_deadline_ms = 5000", 'no_clock_deadline_ms = "5s"', "no_clock_deadline_ms in [pokemon]"),
        ("bank_share = 0.10", "bank_share = 0", "bank_share in [pokemon] must be a number above 0 and at most 1"),
        ("bank_share = 0.10", "bank_share = 1.5", "bank_share in [pokemon] must be a number above 0 and at most 1"),
        ("bank_share = 0.10", "bank_share = -0.1", "bank_share in [pokemon]"),
        ("decision_margin_seconds = 10", "decision_margin_seconds = -1", "decision_margin_seconds in [pokemon]"),
        ("bank_reserve_seconds = 90", "bank_reserve_seconds = -5", "bank_reserve_seconds in [pokemon]"),
        ("no_clock_deadline_ms = 5000", "no_clock_deadline_ms = -1", "no_clock_deadline_ms in [pokemon]"),
    ],
    ids=[
        "margin missing",
        "share missing",
        "reserve missing",
        "no-clock deadline missing",
        "reserve not a number",
        "share not a number",
        "margin a boolean",
        "no-clock deadline not a number",
        "share of zero",
        "share above one",
        "negative share",
        "negative margin",
        "negative reserve",
        "negative no-clock deadline",
    ],
)
def test_8_a_clock_deadline_key_missing_or_off_its_schema_is_a_config_error_naming_it(project, old, new, said):
    _edit(project, AGENT_TOML, old, new)

    with pytest.raises(ConfigError) as raised:
        PokemonSettings.from_files(project)

    assert said in str(raised.value)


def test_a_bank_share_of_one_and_a_zero_margin_reserve_and_no_clock_deadline_are_accepted(project):
    _edit(project, AGENT_TOML, "bank_share = 0.10", "bank_share = 1")
    _edit(project, AGENT_TOML, "decision_margin_seconds = 10", "decision_margin_seconds = 2.5")
    _edit(project, AGENT_TOML, "bank_reserve_seconds = 90", "bank_reserve_seconds = 0")
    _edit(project, AGENT_TOML, "no_clock_deadline_ms = 5000", "no_clock_deadline_ms = 0")

    settings = PokemonSettings.from_files(project)

    assert (settings.bank_share, settings.decision_margin_seconds) == (1, 2.5)
    assert (settings.bank_reserve_seconds, settings.no_clock_deadline_ms) == (0, 0)


def test_the_tracked_settings_ship_the_model_briefing_on():
    assert PokemonSettings.from_files(REPO_ROOT).model_briefing is True


def test_the_model_briefing_switch_off_is_read(project):
    _edit(project, AGENT_TOML, "model_briefing = true", "model_briefing = false")

    assert PokemonSettings.from_files(project).model_briefing is False


@pytest.mark.parametrize(
    "new",
    ["", 'model_briefing = "yes"', "model_briefing = 1"],
    ids=["missing", "a string", "a number"],
)
def test_a_model_briefing_switch_missing_or_not_true_or_false_is_a_config_error_naming_it(project, new):
    _edit(project, AGENT_TOML, "model_briefing = true", new)

    with pytest.raises(ConfigError, match=r"model_briefing in \[pokemon\] must be true or false"):
        PokemonSettings.from_files(project)


def test_the_tracked_tuning_holds_every_weight_of_the_three_tables():
    tuning = Tuning.from_file(REPO_ROOT)

    assert isinstance(tuning.draft, DraftTuning) and tuning.draft.denial_weight == 0.3
    assert isinstance(tuning.preview, PreviewTuning) and tuning.preview.lead_weight == 0.5
    assert isinstance(tuning.turn, TurnTuning) and tuning.turn.ko_bonus == 80.0


@pytest.mark.parametrize(
    "relative, old, new, said",
    [
        (AGENT_TOML, "top_k = 4", "top_k = 10", "top_k in [pokemon]"),
        (AGENT_TOML, "draft_deadline_ms = 6000", "draft_deadline_ms = 15000", "draft_deadline_ms in [pokemon]"),
        (AGENT_TOML, "turn_deadline_ms = 20000", "turn_deadline_ms = 0", "turn_deadline_ms in [pokemon]"),
        (AGENT_TOML, "model_choices = true\n# How many", 'model_choices = "yes"\n# How many', "model_choices"),
        (AGENT_TOML, "top_k = 4", "top_k = 4\nmodel_count = 2", "[pokemon] has no setting named model_count"),
        (TUNING_TOML, "ko_bonus = 80.0", "ko_bonus = -5", "ko_bonus in [turn]"),
        (TUNING_TOML, "lead_weight = 0.5", 'lead_weight = "half"', "lead_weight in [preview]"),
        (TUNING_TOML, "denial_weight = 0.3", "denial_wieght = 0.3", "denial_weight in [draft]"),
        (TUNING_TOML, "[turn]", "[battle]\nx = 1\n\n[turn]", "the file has no setting named battle"),
    ],
    ids=[
        "too many candidates",
        "draft deadline past the clock",
        "zero deadline",
        "switch not a boolean",
        "unknown pokemon key",
        "negative weight",
        "weight not a number",
        "misspelled weight",
        "unknown table",
    ],
)
def test_a_setting_off_its_schema_is_a_config_error_naming_the_key(project, relative, old, new, said):
    _edit(project, relative, old, new)

    with pytest.raises(ConfigError) as raised:
        PokemonSettings.from_files(project)

    assert said in str(raised.value)


def test_a_missing_pokemon_table_is_a_config_error(project):
    _edit(project, AGENT_TOML, "[pokemon]", "[pokemon_off]")

    with pytest.raises(ConfigError, match=r"must have a \[pokemon\] table"):
        PokemonSettings.from_files(project)


def test_a_missing_tuning_file_is_a_config_error(project):
    (project / TUNING_TOML).unlink()

    with pytest.raises(ConfigError, match="tuning.toml was not found"):
        Tuning.from_file(project)


# -- [maximin] ---------------------------------------------------------------------------------------


def test_maximin_the_tracked_tuning_holds_the_four_keys_of_the_maximin_table():
    maximin = Tuning.from_file(REPO_ROOT).maximin

    assert isinstance(maximin, MaximinTuning) and isinstance(maximin.enabled, bool)
    assert (maximin.our_top, maximin.their_top, maximin.draft_lookahead) == (8, 8, True)


def test_maximin_the_tracked_tuning_ships_maximin_on_by_the_benchmark_s_rule():
    assert Tuning.from_file(REPO_ROOT).maximin.enabled is True


def test_maximin_a_tuning_made_in_code_has_maximin_off():
    tracked = Tuning.from_file(REPO_ROOT)

    made = Tuning(draft=tracked.draft, preview=tracked.preview, turn=tracked.turn)

    assert made.maximin == MaximinTuning(enabled=False, our_top=8, their_top=8, draft_lookahead=True)


def test_maximin_switched_on_in_the_file_is_read(project):
    text = (project / TUNING_TOML).read_text(encoding="utf-8")
    enabled = next(line for line in text.splitlines() if line.startswith("enabled = "))
    _edit(project, TUNING_TOML, enabled, "enabled = true")
    _edit(project, TUNING_TOML, "our_top = 8", "our_top = 5")

    maximin = Tuning.from_file(project).maximin

    assert (maximin.enabled, maximin.our_top, maximin.their_top) == (True, 5, 8)


@pytest.mark.parametrize(
    "old, new, said",
    [
        ("their_top = 8\n", "", "their_top in [maximin] must be a whole number of at least 1"),
        ("our_top = 8", "our_top = 0", "our_top in [maximin] must be a whole number of at least 1"),
        ("their_top = 8", "their_top = 2.5", "their_top in [maximin] must be a whole number"),
        ("draft_lookahead = true", 'draft_lookahead = "yes"', "draft_lookahead in [maximin] must be true or false"),
        ("draft_lookahead = true", "draft_lookahead = true\nrounds = 2", "[maximin] has no setting named rounds"),
        ("[maximin]", "[minimax]", "the file has no setting named minimax"),
    ],
    ids=["missing count", "zero count", "fractional count", "switch not a boolean", "unknown key", "renamed table"],
)
def test_maximin_a_key_off_its_schema_is_a_config_error_naming_it(project, old, new, said):
    _edit(project, TUNING_TOML, old, new)

    with pytest.raises(ConfigError) as raised:
        Tuning.from_file(project)

    assert said in str(raised.value)


def test_maximin_a_missing_enabled_switch_is_a_config_error_naming_it(project):
    text = (project / TUNING_TOML).read_text(encoding="utf-8")
    enabled = next(line for line in text.splitlines() if line.startswith("enabled = "))
    _edit(project, TUNING_TOML, f"{enabled}\n", "")

    with pytest.raises(ConfigError, match=r"enabled in \[maximin\] must be true or false"):
        Tuning.from_file(project)


def test_maximin_a_file_without_the_maximin_table_is_a_config_error(project):
    text = (project / TUNING_TOML).read_text(encoding="utf-8")
    (project / TUNING_TOML).write_text(text[: text.index("\n[maximin]\n") + 1], encoding="utf-8")

    with pytest.raises(ConfigError, match=r"must have a \[maximin\] table"):
        Tuning.from_file(project)


# -- [briefing] --------------------------------------------------------------------------------------


def test_briefing_the_tracked_tuning_and_a_tuning_made_in_code_show_the_last_two_turns():
    tracked = Tuning.from_file(REPO_ROOT)

    made = Tuning(draft=tracked.draft, preview=tracked.preview, turn=tracked.turn)

    assert tracked.briefing == made.briefing == BriefingTuning(recent_turns=2)


def test_briefing_zero_recent_turns_is_read_as_the_section_switched_off(project):
    _edit(project, TUNING_TOML, "recent_turns = 2", "recent_turns = 0")

    assert Tuning.from_file(project).briefing.recent_turns == 0


@pytest.mark.parametrize(
    "old, new, said",
    [
        ("recent_turns = 2", "recent_turns = -1", "recent_turns in [briefing] must be a whole number of at least 0"),
        ("recent_turns = 2\n", "", "recent_turns in [briefing] must be a whole number"),
        ("recent_turns = 2", "recent_turns = 2\nlines = 3", "[briefing] has no setting named lines"),
    ],
    ids=["negative count", "missing count", "unknown key"],
)
def test_briefing_a_key_off_its_schema_is_a_config_error_naming_it(project, old, new, said):
    _edit(project, TUNING_TOML, old, new)

    with pytest.raises(ConfigError) as raised:
        Tuning.from_file(project)

    assert said in str(raised.value)
