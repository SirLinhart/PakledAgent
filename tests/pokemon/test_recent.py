"""The "Recent turns" section read from a Showdown protocol log: a real match's turns, the cap and unreadable logs."""

from __future__ import annotations

import json
from pathlib import Path

from ucla_agent.games.pokemon.recent import CUT_NOTE, SECTION_TITLE, our_side, recent_turns

REPO_ROOT = Path(__file__).resolve().parents[2]
MATCH = json.loads((REPO_ROOT / "tests" / "fixtures" / "pokemon" / "match_0556bdb3_turn5.json").read_text("utf-8"))
MATCH_OBSERVATION = {
    "team": {name: {} for name in MATCH["team"]},
    "opponent_team": {name: {} for name in MATCH["opponent_team"]},
}
TURN_3 = (
    "Turn 3: ours Landorus used Protect; ours Lucario used Close Combat on theirs Archaludon (super effective, "
    "100→16%); ours Lucario 49→40% from Life Orb; theirs Cresselia used Icy Wind on ours Landorus (protected), ours "
    "Lucario (resisted, 40→31%); theirs Archaludon used Flash Cannon on ours Landorus (protected); theirs Archaludon "
    "16→22% from Leftovers."
)
TURN_4 = (
    "Turn 4: ours Landorus used Protect; ours Lucario used Protect; theirs Cresselia used Icy Wind on ours Lucario "
    "(protected), ours Landorus (protected); theirs Archaludon used Draco Meteor on ours Landorus (protected); theirs "
    "Archaludon 22→28% from Leftovers."
)


def test_match_0556bdb3_at_turn_5_shows_turns_3_and_4_with_every_action_and_result():
    section = recent_turns(MATCH["protocol_log"], our_side(MATCH_OBSERVATION), 2)

    assert MATCH["turn"] == 5
    assert section == "\n".join([SECTION_TITLE, TURN_3, TURN_4])


def test_our_side_is_read_from_the_team_keys_else_the_other_side_of_the_opponent_s():
    assert our_side(MATCH_OBSERVATION) == "p2"
    assert our_side({"opponent_team": {"p2: Lucario": {}}}) == "p1"
    assert our_side({"team": {}, "opponent_team": "none"}) is None


def test_a_section_over_its_cap_leaves_the_oldest_actions_out_first_and_says_so():
    full = recent_turns(MATCH["protocol_log"], "p2", 4, max_chars=10_000)
    capped = recent_turns(MATCH["protocol_log"], "p2", 4)
    short = recent_turns(MATCH["protocol_log"], "p2", 2, max_chars=400)

    assert full.splitlines()[1].startswith("Turn 1: ")
    assert len(full) > 1200 >= len(capped)
    assert capped.endswith(TURN_4) and CUT_NOTE in capped.splitlines()[1]
    assert len(short) <= 400 and short.splitlines()[-1] == TURN_4
    assert short.splitlines()[1] == f"Turn 3: {CUT_NOTE}theirs Archaludon 16→22% from Leftovers."


def test_misses_failures_criticals_weather_fields_and_side_conditions_are_noted_and_upkeep_weather_is_not():
    log = [
        "|switch|p1a: Pikachu|Pikachu, L50|100/100",
        "|switch|p2a: Snorlax|Snorlax, L50|200/200",
        "|turn|1",
        "|-weather|RainDance",
        "|-fieldstart|move: Trick Room",
        "|-sidestart|p2: someone|move: Tailwind",
        "|move|p1a: Pikachu|Thunderbolt|p2a: Snorlax|[miss]",
        "|-miss|p1a: Pikachu|p2a: Snorlax",
        "|move|p2a: Snorlax|Body Slam|p1a: Pikachu",
        "|-crit|p1a: Pikachu",
        "|-damage|p1a: Pikachu|20/100",
        "|move|p2a: Snorlax|Yawn|p1a: Pikachu",
        "|-fail|p1a: Pikachu",
        "|-heal|p2a: Snorlax|200/200",
        "|-weather|RainDance|[upkeep]",
        "|-sideend|p1: other|Reflect",
        "|-fieldend|move: Trick Room",
        "|-weather|none",
        "|turn|2",
    ]

    section = recent_turns(log, "p2", 2)

    assert section.splitlines() == [
        SECTION_TITLE,
        "Turn 1: weather RainDance; Trick Room started; ours Tailwind started; theirs Pikachu used Thunderbolt on ours "
        "Snorlax (missed); ours Snorlax used Body Slam on theirs Pikachu (critical hit, 100→20%); ours Snorlax used Yawn "
        "on theirs Pikachu (failed); ours Snorlax 100→100%; theirs Reflect ended; Trick Room ended; weather ended.",
    ]


def test_no_completed_turn_gives_an_empty_section():
    assert recent_turns(MATCH["protocol_log"][: MATCH["protocol_log"].index("|turn|1") + 1], "p2", 2) == ""
    assert recent_turns([], "p2", 2) == ""


def test_an_unreadable_log_or_an_unknown_side_gives_none_and_never_raises():
    assert recent_turns("|turn|1", "p2", 2) is None
    assert recent_turns(["|turn|1", 3], "p2", 2) is None
    assert recent_turns(["|turn|1", "|move|nobody|Tackle|", "|turn|2"], "p2", 2) is None
    assert recent_turns(["|turn|one"], "p2", 2) is None
    assert recent_turns(["|switch|p1a: Pikachu|Pikachu|lots"], "p2", 2) is None
    assert recent_turns(MATCH["protocol_log"], None, 2) is None
