"""One seat's battle log: the board, HP, status, boosts, what Pokémon reveal, the field, and the hidden lineup.

The lines are Showdown protocol lines as `tools/pokemon_sim/sim.js` hands a
seat its log (p1's view: its own HP exact, p2's out of 100); the opening is
the one real Showdown produced for the team preview probe in
`test_pokemon_sim.py`.
"""

from __future__ import annotations

import pytest

from ucla_agent.practice.pokemon.battle_log import BattleLog, board_position, effect_name, pokemon_key

PREVIEW = [
    "|player|p1|alpha||",
    "|gametype|doubles",
    "|clearpoke",
    "|poke|p1|Incineroar, L50, M|",
    "|poke|p2|Urshifu-*, L50, F|",
    "|poke|p2|Amoonguss, L50, F|",
    "|teampreview|4",
]
OPENING = [
    "|",
    "|teamsize|p1|4",
    "|teamsize|p2|4",
    "|start",
    "|switch|p1a: Urshifu|Urshifu-Rapid-Strike, L50, F|175/175",
    "|switch|p1b: Incineroar|Incineroar, L50, M|202/202",
    "|switch|p2a: Incineroar|Incineroar, L50, F|100/100",
    "|switch|p2b: Urshifu|Urshifu-Rapid-Strike, L50, F|100/100",
    "|-ability|p2a: Incineroar|Intimidate|boost",
    "|-unboost|p1a: Urshifu|atk|1",
    "|-unboost|p1b: Incineroar|atk|1",
    "|turn|1",
]


@pytest.fixture
def opened() -> BattleLog:
    log = BattleLog("p1")
    log.read(PREVIEW + OPENING)
    return log


def test_the_opening_puts_four_pokemon_on_the_board_with_their_hp_as_the_seat_sees_it(opened):
    assert opened.turn == 1
    assert opened.positions == {
        "p1a": "p1: Urshifu",
        "p1b": "p1: Incineroar",
        "p2a": "p2: Incineroar",
        "p2b": "p2: Urshifu",
    }
    own, foe = opened.active("p1a"), opened.active("p2b")
    assert (own.species, own.hp, own.max_hp) == ("Urshifu-Rapid-Strike", 175, 175)
    assert (foe.species, foe.hp, foe.max_hp, foe.side) == ("Urshifu-Rapid-Strike", 100, 100, "p2")


def test_team_preview_reveals_no_opposing_pokemon_until_it_stands_on_the_board():
    log = BattleLog("p1")
    log.read(PREVIEW)

    assert log.seen_on("p2") == [] and log.active("p2a") is None

    log.read(OPENING)
    assert [seen.name for seen in log.seen_on("p2")] == ["Incineroar", "Urshifu"]


def test_intimidate_reveals_its_ability_and_lowers_attack(opened):
    assert opened.active("p2a").ability == "intimidate"
    assert opened.active("p1a").boosts == {"atk": -1}


def test_damage_heal_status_and_faint_follow_the_conditions(opened):
    opened.read(
        [
            "|move|p2b: Urshifu|Surging Strikes|p1b: Incineroar",
            "|-damage|p1b: Incineroar|100/202",
            "|-enditem|p1b: Incineroar|Sitrus Berry|[eat]",
            "|-heal|p1b: Incineroar|150/202|[from] item: Sitrus Berry",
            "|-status|p2a: Incineroar|par",
            "|-damage|p2a: Incineroar|44/100 par",
            "|-damage|p1b: Incineroar|0 fnt",
            "|faint|p1b: Incineroar",
        ]
    )

    fallen, foe = opened.active("p1b"), opened.active("p2a")
    assert (fallen.hp, fallen.fainted, fallen.status, fallen.item) == (0, True, None, "")
    assert (foe.hp, foe.status, foe.fainted) == (44, "par", False)
    assert opened.active("p2b").moves == ["surgingstrikes"]
    opened.read(["|-curestatus|p2a: Incineroar|par|[msg]"])
    assert opened.active("p2a").status is None


def test_a_switch_takes_the_position_clears_the_leaving_pokemon_s_boosts_and_keeps_it_known(opened):
    opened.read(["|switch|p1a: Amoonguss|Amoonguss, L50, F|221/221"])

    assert opened.active("p1a").name == "Amoonguss"
    urshifu = opened.pokemon["p1: Urshifu"]
    assert (urshifu.active, urshifu.boosts) == (False, {})
    assert [seen.name for seen in opened.seen_on("p1")] == ["Urshifu", "Incineroar", "Amoonguss"]


def test_boosts_add_up_clamp_at_six_and_clear(opened):
    opened.read(["|-boost|p2b: Urshifu|spe|4", "|-boost|p2b: Urshifu|spe|4", "|-unboost|p2b: Urshifu|def|1"])
    assert opened.active("p2b").boosts == {"spe": 6, "def": -1}

    opened.read(["|-clearnegativeboost|p2b: Urshifu"])
    assert opened.active("p2b").boosts == {"spe": 6}
    opened.read(["|-setboost|p2b: Urshifu|atk|6", "|-clearboost|p2b: Urshifu"])
    assert opened.active("p2b").boosts == {}
    opened.read(["|-boost|p2a: Incineroar|def|1", "|-clearallboost"])
    assert all(seen.boosts == {} for seen in opened.pokemon.values())


def test_items_and_abilities_are_revealed_by_their_own_lines_and_by_from_tags(opened):
    opened.read(
        [
            "|-damage|p2b: Urshifu|80/100|[from] item: Life Orb",
            "|-damage|p2b: Urshifu|70/100|[from] item: Rocky Helmet|[of] p1b: Incineroar",
            "|-item|p2a: Incineroar|Choice Scarf|[from] ability: Frisk|[of] p1a: Urshifu",
        ]
    )

    assert opened.active("p2b").item == "lifeorb"
    assert opened.active("p1b").item == "rockyhelmet"
    assert opened.active("p2a").item == "choicescarf"
    assert opened.active("p1a").ability == "frisk"
    assert opened.active("p2b").ability is None


def test_a_move_another_move_called_is_not_revealed_as_the_user_s_own(opened):
    opened.read(["|move|p2a: Incineroar|Copycat|", "|move|p2a: Incineroar|Tackle|p1a: Urshifu|[from] move: Copycat"])

    assert opened.active("p2a").moves == ["copycat"]


def test_weather_fields_and_side_conditions_use_poke_env_names_and_the_turn_they_started(opened):
    opened.read(
        [
            "|turn|2",
            "|-weather|RainDance|[from] ability: Drizzle|[of] p2a: Incineroar",
            "|-fieldstart|move: Grassy Terrain|[from] ability: Grassy Surge|[of] p1b: Incineroar",
            "|-fieldstart|move: Trick Room|[of] p1a: Urshifu",
            "|-sidestart|p2: Incineroar|move: Tailwind",
            "|-sidestart|p1: Urshifu|Spikes",
            "|-sidestart|p1: Urshifu|Spikes",
            "|turn|3",
            "|-weather|RainDance|[upkeep]",
            "|-fieldstart|move: Psychic Terrain",
        ]
    )

    assert opened.weather == {"RAINDANCE": 2}
    assert opened.fields == {"TRICK_ROOM": 2, "PSYCHIC_TERRAIN": 3}
    assert opened.side_conditions == {"p1": {"SPIKES": 2}, "p2": {"TAILWIND": 2}}
    assert opened.active("p2a").ability == "drizzle"

    opened.read(["|-weather|none", "|-fieldend|move: Trick Room", "|-sideend|p2: Incineroar|move: Tailwind"])
    assert (opened.weather, opened.fields, opened.side_conditions["p2"]) == ({}, {"PSYCHIC_TERRAIN": 3}, {})


def test_ally_switch_swaps_the_two_positions_of_a_side(opened):
    opened.read(["|swap|p1a: Urshifu|1"])

    assert opened.positions["p1a"] == "p1: Incineroar" and opened.positions["p1b"] == "p1: Urshifu"


def test_a_forme_change_and_terastallization_are_followed(opened):
    opened.read(["|detailschange|p2a: Incineroar|Incineroar-Mega, L50", "|-terastallize|p2b: Urshifu|Water"])

    assert opened.active("p2a").species == "Incineroar-Mega"
    assert opened.active("p2b").terastallized is True


def test_the_end_of_the_battle_names_the_winner_or_a_tie(opened):
    opened.read(["|win|alpha"])
    assert (opened.ended, opened.winner) == (True, "alpha")

    tied = BattleLog("p2")
    tied.read(["|tie"])
    assert (tied.ended, tied.winner) == (True, None)


@pytest.mark.parametrize(
    ("reference", "key", "position"),
    [("p1a: Urshifu", "p1: Urshifu", "p1a"), ("p2: Mr. Rime", "p2: Mr. Rime", None), ("p2b: A: B", "p2: A: B", "p2b")],
)
def test_a_reference_gives_its_pokemon_and_its_board_position(reference, key, position):
    assert pokemon_key(reference) == key
    assert board_position(reference) == position


@pytest.mark.parametrize(
    ("text", "name"),
    [("move: Grassy Terrain", "GRASSY_TERRAIN"), ("RainDance", "RAINDANCE"), ("Light Screen", "LIGHT_SCREEN")],
)
def test_an_effect_s_name_is_poke_env_s_enum_name(text, name):
    assert effect_name(text) == name
