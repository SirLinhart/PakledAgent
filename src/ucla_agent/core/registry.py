"""Which games the agent can play.

This is the only module under `ucla_agent.core` that imports a game area, so
the rest of the core stays free of game code.
"""

from __future__ import annotations

from typing import Any, Callable

from ucla_agent.games.pokemon.agent import PokemonAgent
from ucla_agent.games.redalert.agent import RedAlertAgent
from ucla_agent.games.werewolf.agent import WerewolfAgent

# Platform game type -> zero-argument factory for that game's decision-maker.
GAME_FACTORIES: dict[str, Callable[[], Any]] = {
    "red_alert": RedAlertAgent,
    "werewolf": WerewolfAgent,
    "pokemon_vgc_doubles_draft": PokemonAgent,
}


def supported_games() -> list[str]:
    """The registered platform game types, sorted."""
    return sorted(GAME_FACTORIES)
