"""The settings a Werewolf agent plays a match with: the `[werewolf]` table of `config/agent.toml` and the tuning file.

Two switches say how far a model takes part. With `model_choices` on, the
`werewolf_choice` chain may pick a vote, kill or investigation among the
script's candidates; with `model_messages` on, the `werewolf_message` chain
writes the chat messages. Each request has its deadline, after which the
script's own choice or message stands.

The agent takes the settings as one object, so a test or the practice
benchmark can switch the models off or shorten the deadlines without editing
a tracked file. The table is read strictly, like every tracked setting:
a key nobody reads, a switch that is not true or false, or a deadline out of
bounds is a `ConfigError` that names the file, the table and the key. Reading
the files is the only I/O here and happens in `from_files`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ucla_agent.core.config import AGENT_CONFIG_FILE_NAME, CONFIG_DIR_NAME, load_agent_config
from ucla_agent.core.config_tables import Table
from ucla_agent.games.werewolf.tuning import Tuning

WEREWOLF_TABLE = "werewolf"
# The chains of `[decide.chains]` the agent asks: for a choice among the script's candidates, and for a message.
CHOICE_CHAIN = "werewolf_choice"
MESSAGE_CHAIN = "werewolf_message"
# The platform gives a seat 120 s to move before it moves for it; a deadline is held well inside that.
MAX_DEADLINE_MS = 60_000


@dataclass(frozen=True)
class WerewolfSettings:
    """The model switches and deadlines of `[werewolf]`, and the tuning of `config/werewolf/tuning.toml`."""

    model_choices: bool
    model_messages: bool
    choice_deadline_ms: int
    message_deadline_ms: int
    tuning: Tuning

    @classmethod
    def from_files(cls, root: Path) -> WerewolfSettings:
        """The settings the tracked files under `root` hold. Raises `ConfigError` when either is unusable."""
        path = root / CONFIG_DIR_NAME / AGENT_CONFIG_FILE_NAME
        table = Table(path, WEREWOLF_TABLE, load_agent_config(root).get(WEREWOLF_TABLE))
        settings = cls(
            model_choices=table.boolean("model_choices"),
            model_messages=table.boolean("model_messages"),
            choice_deadline_ms=table.integer("choice_deadline_ms", minimum=1, maximum=MAX_DEADLINE_MS),
            message_deadline_ms=table.integer("message_deadline_ms", minimum=1, maximum=MAX_DEADLINE_MS),
            tuning=Tuning.from_file(root),
        )
        table.reject_unread_keys()
        return settings
