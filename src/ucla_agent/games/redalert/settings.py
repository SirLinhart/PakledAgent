"""The Red Alert settings of `config/agent.toml`: which choices a model may make, and the sparring plans.

Two switches say how far a model takes part in a Red Alert match. With
`model_plan_choice` on, the plan selector asks the `red_alert_plan` chain to
choose among the discretionary plans; with `model_commands` on, command
shorthand written by a model is turned into orders. A third says what a model
choosing the plan reads: with `model_briefing` on, the plain-words briefing of
`briefing`, answered with a reason before the choice; off, the compact summary
of `shorthand`. With `model_scout_choice` on, the `red_alert_scout` chain is
asked where each scout of the scout slots goes next (see `scout_choice`); it
changes nothing while `[scouting] scout_slots` is off. With `model_commander`
on, the `red_alert_commander` chain is asked every batch to pick one checked
action for the first scout, then for main (see `commander`). All five are off
unless the tracked file turns them on.

`plan_prompt` says how the plan question is put to a model: "briefing", as
`model_briefing` says above, or "compact", a prompt of at most 1,500
characters with the state as `key=value` lines and one line for each open
plan, answered with the choice first (see `compact_prompt`), whatever
`model_briefing` says. It is "briefing" unless the tracked file says
otherwise, and changes nothing while `model_plan_choice` is off.

How many of a model's own recent picks each question shows is
`history_picks` of the `[models]` table (see `model_history`), 3 unless the
tracked file says otherwise.

For sparring between two copies of the agent, `[redalert.sparring]` may pin
the discretionary plan of a seat (`seat_plans = {Multi1 = "HOLD"}`), so that
the two seats play different plans. The pin holds in test matches only.

The table is read strictly, like every tracked setting: a key nobody reads, a
switch that is not true or false, or a seat plan that is not one of the
discretionary plans is a `ConfigError` that names the file, the table and the
key. Reading the file is the only I/O here and happens in `from_file`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from ucla_agent.core.config import (
    AGENT_CONFIG_FILE_NAME,
    CONFIG_DIR_NAME,
    DEFAULT_HISTORY_PICKS,
    history_picks,
    load_agent_config,
)
from ucla_agent.core.config_tables import Table
from ucla_agent.games.redalert.composition import HOLD, PRESS, RAID

REDALERT_TABLE = "redalert"
SPARRING_TABLE = "sparring"
SEAT_PLANS_TABLE = "seat_plans"
# The plans a seat may be pinned to: the ones the discretionary choice chooses among.
DISCRETIONARY_PLANS = (HOLD, RAID, PRESS)
# How the plan question is put to a model: as `model_briefing` says, or as the compact prompt.
BRIEFING_PROMPT = "briefing"
COMPACT_PROMPT = "compact"
PLAN_PROMPTS = (BRIEFING_PROMPT, COMPACT_PROMPT)


@dataclass(frozen=True)
class RedAlertSettings:
    """The `[redalert]` table: the model switches, the plan prompt, and the plan each seat is pinned to while
    sparring."""

    model_plan_choice: bool = False
    model_commands: bool = False
    model_briefing: bool = False
    model_scout_choice: bool = False
    model_commander: bool = False
    plan_prompt: str = BRIEFING_PROMPT
    # Seat name -> the discretionary plan it is pinned to in test matches.
    seat_plans: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    # How many of a model's own recent picks each question shows: `[models] history_picks` (see `model_history`).
    history_picks: int = DEFAULT_HISTORY_PICKS

    @classmethod
    def from_file(cls, root: Path) -> RedAlertSettings:
        """The settings `config/agent.toml` under `root` holds. Raises `ConfigError` when they are unusable."""
        path = root / CONFIG_DIR_NAME / AGENT_CONFIG_FILE_NAME
        config = load_agent_config(root)
        table = Table(path, REDALERT_TABLE, config.get(REDALERT_TABLE))
        sparring = table.subtable(SPARRING_TABLE, optional=True)
        seats = sparring.subtable(SEAT_PLANS_TABLE, optional=True)
        settings = cls(
            model_plan_choice=table.boolean("model_plan_choice"),
            model_commands=table.boolean("model_commands"),
            model_briefing=table.boolean("model_briefing"),
            model_scout_choice=table.boolean("model_scout_choice"),
            model_commander=table.boolean("model_commander"),
            plan_prompt=table.one_of("plan_prompt", PLAN_PROMPTS),
            seat_plans=MappingProxyType({seat: seats.one_of(seat, DISCRETIONARY_PLANS) for seat in seats.keys()}),
            history_picks=history_picks(config),
        )
        table.reject_unread_keys()
        sparring.reject_unread_keys()
        return settings
