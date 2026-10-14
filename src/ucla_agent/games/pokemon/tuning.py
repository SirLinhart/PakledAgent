"""The weights the Pokémon agent scores its choices by, read from `config/pokemon/tuning.toml`.

The tracked file is the only place these values live: the draft's score
weights and bonuses in `[draft]`, team preview's in `[preview]`, the doubles
turn's in `[turn]`, the switch and sizes of maximin play in `[maximin]` and
how many past turns the model's battle briefing shows in `[briefing]`. A
run records the file's hash, so a setting that is silently ignored or
replaced would make the record say something the agent did not do. The file
is therefore read strictly, key by key, through `core.config_tables.Table`:
every weight is a number of at least 0 (a malus or penalty is a size that is
taken off), `[maximin]`'s switches are true or false and its counts whole
numbers of at least 1, `[briefing]`'s count a whole number of at least 0,
and a key that is missing, of another kind or not one of these fields is a
`ConfigError` naming the file, the table and the key.

`MaximinTuning`'s own defaults have maximin off, so a `Tuning` made in code
without it plays the script's own ranking.

Reading the file is the only I/O here and happens in `Tuning.from_file`.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import TypeVar

from ucla_agent.core.config_tables import Table, read_tables, reject_unknown_keys

TUNING_FILE = Path("config") / "pokemon" / "tuning.toml"
DRAFT_TABLE = "draft"
PREVIEW_TABLE = "preview"
TURN_TABLE = "turn"
MAXIMIN_TABLE = "maximin"
BRIEFING_TABLE = "briefing"
# How many joint actions of ours and of the opponent's a turn's maximin pairs, unless the file says otherwise.
DEFAULT_MAXIMIN_TOP = 8
# How many of the last completed turns the battle briefing's Recent turns section shows, unless the file says otherwise.
DEFAULT_RECENT_TURNS = 2


@dataclass(frozen=True)
class DraftTuning:
    """The draft's score: weights of power, bulk, speed and denial, and the support and synergy bonuses."""

    power_weight: float
    bulk_weight: float
    speed_weight: float
    fake_out_bonus: float
    tailwind_bonus: float
    trick_room_bonus: float
    redirection_bonus: float
    intimidate_bonus: float
    protect_bonus: float
    spread_move_bonus: float
    resist_bonus: float
    new_type_bonus: float
    shared_weakness_malus: float
    denial_weight: float


@dataclass(frozen=True)
class PreviewTuning:
    """Team preview's score: the weight of the leads' matchup and the bonus of a lead with speed control."""

    lead_weight: float
    speed_control_bonus: float


@dataclass(frozen=True)
class TurnTuning:
    """A doubles turn's option scores: the bonuses of a knockout and each tactic, and the penalties."""

    ko_bonus: float
    fake_out_bonus: float
    protect_bonus: float
    speed_control_bonus: float
    switch_bonus: float
    switch_penalty: float
    overkill_malus: float


@dataclass(frozen=True)
class MaximinTuning:
    """Maximin play: whether each phase ranks our options by their worst case against the opponent's best replies,
    how many joint actions of ours and of theirs a turn pairs, and whether a draft pick looks one pick ahead."""

    enabled: bool = False
    our_top: int = DEFAULT_MAXIMIN_TOP
    their_top: int = DEFAULT_MAXIMIN_TOP
    draft_lookahead: bool = True

    @classmethod
    def from_table(cls, table: Table) -> MaximinTuning:
        """The `[maximin]` table, read strictly: two switches and two counts of at least 1, and no other key."""
        tuning = cls(
            enabled=table.boolean("enabled"),
            our_top=table.integer("our_top", minimum=1),
            their_top=table.integer("their_top", minimum=1),
            draft_lookahead=table.boolean("draft_lookahead"),
        )
        table.reject_unread_keys()
        return tuning


@dataclass(frozen=True)
class BriefingTuning:
    """The model's battle briefing: how many of the last completed turns its Recent turns section shows (0: none)."""

    recent_turns: int = DEFAULT_RECENT_TURNS

    @classmethod
    def from_table(cls, table: Table) -> BriefingTuning:
        """The `[briefing]` table, read strictly: a count of at least 0, and no other key."""
        tuning = cls(recent_turns=table.integer("recent_turns", minimum=0))
        table.reject_unread_keys()
        return tuning


_Weights = TypeVar("_Weights", DraftTuning, PreviewTuning, TurnTuning)


@dataclass(frozen=True)
class Tuning:
    """Every table of the Pokémon tuning file."""

    draft: DraftTuning
    preview: PreviewTuning
    turn: TurnTuning
    maximin: MaximinTuning = MaximinTuning()
    briefing: BriefingTuning = BriefingTuning()

    @classmethod
    def from_file(cls, root: Path) -> Tuning:
        """The settings held by `config/pokemon/tuning.toml` under `root`.

        Raises `ConfigError`, naming the file, the table and the key, when the
        file is missing or not TOML, lacks a table or key, or holds a table,
        key or value it should not.
        """
        path = root / TUNING_FILE
        tables = read_tables(path)
        reject_unknown_keys(
            path, "the file", tables, {DRAFT_TABLE, PREVIEW_TABLE, TURN_TABLE, MAXIMIN_TABLE, BRIEFING_TABLE}
        )
        return cls(
            draft=_numbers(Table(path, DRAFT_TABLE, tables.get(DRAFT_TABLE)), DraftTuning),
            preview=_numbers(Table(path, PREVIEW_TABLE, tables.get(PREVIEW_TABLE)), PreviewTuning),
            turn=_numbers(Table(path, TURN_TABLE, tables.get(TURN_TABLE)), TurnTuning),
            maximin=MaximinTuning.from_table(Table(path, MAXIMIN_TABLE, tables.get(MAXIMIN_TABLE))),
            briefing=BriefingTuning.from_table(Table(path, BRIEFING_TABLE, tables.get(BRIEFING_TABLE))),
        )


def _numbers(table: Table, kind: type[_Weights]) -> _Weights:
    """One table read into `kind`: a number of at least 0 for each of its fields, and no other key."""
    values = {field.name: float(table.number(field.name)) for field in fields(kind)}
    table.reject_unread_keys()
    return kind(**values)
