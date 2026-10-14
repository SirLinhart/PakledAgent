"""The settings a Pokémon agent plays a match with: the `[pokemon]` table of `config/agent.toml` and the tuning file.

With `model_choices` on, the `pokemon_choice` chain may choose among the
script's best `top_k` candidates of a decision (a draft card, a lineup, a
turn), each phase with its own deadline, after which the script's best
stands. At Team Preview and in battle the observation's clock cuts that
deadline further: `decision_margin_seconds` are kept back from the decision's
clock, a battle decision spends at most `bank_share` of the bank and never
takes it below `bank_reserve_seconds`, and before Showdown has reported the
clock a decision gets at most `no_clock_deadline_ms`. With `model_briefing`
on, the model is asked with the full briefing of `briefing` as the context,
and with the one-line summary of the rosters or the board when it is off.
With `model_decides` on, the model is offered the script's best
`decide_top_k` candidates instead, described without the script's scores and
listed in a shuffled order, and writes a reason of at most
`decide_reason_words` words before its choice. The agent takes the settings as
one object, so a test or the practice harness can switch the model off or
shorten a deadline without editing a tracked file.

The table is read strictly, like every tracked setting: a key nobody reads, a
switch that is not true or false, or a count or deadline out of bounds is a
`ConfigError` naming the file, the table and the key. Reading the files is
the only I/O here and happens in `from_files`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ucla_agent.core.config import AGENT_CONFIG_FILE_NAME, CONFIG_DIR_NAME, load_agent_config
from ucla_agent.core.config_tables import Table
from ucla_agent.games.pokemon.tuning import Tuning

POKEMON_TABLE = "pokemon"
# The chain of `[decide.chains]` asked to choose among the script's candidates.
CHOICE_CHAIN = "pokemon_choice"
# Option ids run from 1 to 9 after their phase's letter, two characters, short enough for the model's answer.
MAX_TOP_K = 9
# A draft pick has 15 s on the platform's clock; the script, the request and the answer's journey need the rest.
MAX_DRAFT_DEADLINE_MS = 12_000
# A lineup has 85 s and a battle decision 50 s before the platform plays a random choice; the observation's clock
# holds a deadline inside them, so this bound only keeps a configured deadline within reason.
MAX_BATTLE_DEADLINE_MS = 60_000
# A model that decides is offered at least two candidates, or there is nothing to decide.
MIN_DECIDE_TOP_K = 2
# Its answer may take 160 tokens: a reason of at most this many words, at about two tokens a word, leaves room in them
# for the answer object and the id.
MAX_DECIDE_REASON_WORDS = 60


@dataclass(frozen=True)
class PokemonSettings:
    """The model switches, candidate counts and deadlines of `[pokemon]`, and the tuning of
    `config/pokemon/tuning.toml`."""

    model_choices: bool
    top_k: int
    draft_deadline_ms: int
    preview_deadline_ms: int
    turn_deadline_ms: int
    decision_margin_seconds: float
    bank_share: float
    bank_reserve_seconds: float
    no_clock_deadline_ms: int
    model_briefing: bool
    model_decides: bool
    decide_top_k: int
    decide_reason_words: int
    tuning: Tuning

    @classmethod
    def from_files(cls, root: Path) -> PokemonSettings:
        """The settings the tracked files under `root` hold. Raises `ConfigError` when either is unusable."""
        path = root / CONFIG_DIR_NAME / AGENT_CONFIG_FILE_NAME
        table = Table(path, POKEMON_TABLE, load_agent_config(root).get(POKEMON_TABLE))
        settings = cls(
            model_choices=table.boolean("model_choices"),
            top_k=table.integer("top_k", minimum=1, maximum=MAX_TOP_K),
            draft_deadline_ms=table.integer("draft_deadline_ms", minimum=1, maximum=MAX_DRAFT_DEADLINE_MS),
            preview_deadline_ms=table.integer("preview_deadline_ms", minimum=1, maximum=MAX_BATTLE_DEADLINE_MS),
            turn_deadline_ms=table.integer("turn_deadline_ms", minimum=1, maximum=MAX_BATTLE_DEADLINE_MS),
            decision_margin_seconds=table.number("decision_margin_seconds"),
            bank_share=_share(table, "bank_share"),
            bank_reserve_seconds=table.number("bank_reserve_seconds"),
            no_clock_deadline_ms=table.integer("no_clock_deadline_ms", minimum=0, maximum=MAX_BATTLE_DEADLINE_MS),
            model_briefing=table.boolean("model_briefing"),
            model_decides=table.boolean("model_decides"),
            decide_top_k=table.integer("decide_top_k", minimum=MIN_DECIDE_TOP_K, maximum=MAX_TOP_K),
            decide_reason_words=table.integer("decide_reason_words", minimum=1, maximum=MAX_DECIDE_REASON_WORDS),
            tuning=Tuning.from_file(root),
        )
        table.reject_unread_keys()
        return settings


def _share(table: Table, key: str) -> float:
    """A number above 0 and at most 1: a share of zero would never let a model spend the bank, one above 1 more than
    the bank holds."""
    share = table.number(key)
    if not 0 < share <= 1:
        raise table.error(key, "a number above 0 and at most 1")
    return share
