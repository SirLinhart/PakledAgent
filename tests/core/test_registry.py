"""The game registry: which platform game types have a decision-maker."""

from __future__ import annotations

from ucla_agent.core import registry
from ucla_agent.games.pokemon.agent import PokemonAgent
from ucla_agent.games.redalert.agent import RedAlertAgent
from ucla_agent.games.werewolf.agent import WerewolfAgent


def test_red_alert_werewolf_and_pokemon_are_the_registered_games():
    assert registry.supported_games() == ["pokemon_vgc_doubles_draft", "red_alert", "werewolf"]
    assert registry.GAME_FACTORIES == {
        "red_alert": RedAlertAgent,
        "werewolf": WerewolfAgent,
        "pokemon_vgc_doubles_draft": PokemonAgent,
    }


def test_supported_games_are_sorted(monkeypatch):
    monkeypatch.setitem(registry.GAME_FACTORIES, "honor_of_kings", object)

    assert registry.supported_games() == ["honor_of_kings", "pokemon_vgc_doubles_draft", "red_alert", "werewolf"]


def test_each_factory_call_makes_a_new_decision_maker(run_folder):
    # Made in a folder with no config/, the agents play on the built-in settings, which ask no model.
    factory = registry.GAME_FACTORIES["red_alert"]

    first, second = factory(), factory()

    assert isinstance(first, RedAlertAgent) and isinstance(second, RedAlertAgent)
    assert first is not second
