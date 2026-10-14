"""The plan choice briefing: the six plain-words sections a model reads, their cap, and the request they go in."""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
import statistics
import time
from pathlib import Path

import httpx
import pytest

from situations import StubChooser, ask, base_state, fixture_state, unit
from switches_off import combat_off, new_agent, plan_rules_off, raids_off, settings_models_off, tuning_off
from ucla_agent.core.decide import ChoiceRequest, LocalModelSource, Option, local
from ucla_agent.games.redalert import briefing as briefing_module
from ucla_agent.games.redalert import compact_prompt, plans
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.briefing import CUT_NOTE, GLOSSARY_KEPT_TYPES, MAX_BRIEFING_CHARS, Briefing
from ucla_agent.games.redalert.compact_prompt import CompactPrompt
from ucla_agent.games.redalert.composition import HOLD, OPENING, PRESS, RAID
from ucla_agent.games.redalert.glossary import GLOSSARY_FILE, Glossary
from ucla_agent.games.redalert.groups import MAIN as MAIN_GROUP
from ucla_agent.games.redalert.groups import RAID as RAID_GROUP
from ucla_agent.games.redalert.managers import MAJOR_THREAT, NO_THREAT
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.settings import RedAlertSettings
from ucla_agent.games.redalert.shorthand import summarize
from ucla_agent.games.redalert.tuning import CompositionTuning, Tuning

REPO_ROOT = Path(__file__).resolve().parents[2]
TICKS_PER_SECOND = 25
MATCH_SECONDS = 1200
YARD = ("fact", 22, 48)
REFINERY = ("proc", 24, 39)
ALLIED_BASE = (YARD, ("powr", 19, 48), ("tent", 22, 44), REFINERY, ("powr", 26, 50), ("weap", 22, 53))
SOVIET_BASE = (YARD, ("powr", 19, 48), ("barr", 22, 44), REFINERY, ("powr", 26, 50), ("weap", 22, 53))
# The enemy's start, 52 cells east of the base's center at (22, 48).
ENEMY_START = (74, 48)
SECTION_TITLES = ("## Situation", "## Our forces", "## Enemy as known", "## Comparison", "## Glossary", "## Options")
# The building time a briefing may take, and how many times it is timed to find it.
MAX_BUILD_MS = 5.0
TIMINGS = 21


@pytest.fixture(scope="module")
def glossary() -> Glossary:
    return Glossary.from_file(REPO_ROOT)


def foe(enemy_id: int, enemy_type: str, x: int = ENEMY_START[0], y: int = ENEMY_START[1], hp: int = 100) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": hp}


def at(
    seconds: float,
    *,
    units: list | tuple = (),
    enemies: list | tuple = (),
    enemy_buildings: list | tuple = (),
    kills: int = 0,
    deaths: int = 0,
    base: tuple = ALLIED_BASE,
    faction: str = "england",
    remaining: float | None = None,
) -> dict:
    """A playing state of a base at a game second, with the own units, the enemies in sight and the kills given."""
    state = base_state(
        faction,
        *base,
        tick=round(seconds * TICKS_PER_SECOND),
        units=list(units),
        visible_enemies=list(enemies),
        visible_enemy_buildings=list(enemy_buildings),
        military={"kills_cost": kills, "deaths_cost": deaths, "assets_value": 0, "army_value": 0},
    )
    left = MATCH_SECONDS - seconds if remaining is None else remaining
    state["time"] = {"max_minutes": 20, "game_seconds": seconds, "remaining_seconds": left}
    return state


def briefing_after(*states: dict, tuning: Tuning | None = None, max_chars: int = MAX_BRIEFING_CHARS):
    """The briefing an agent with base defense and scouting writes after taking in the states, with the plans open
    as the plan rules open them, and the agent."""
    agent = new_agent(BaseDefenseManager(), ScoutingManager(), tuning=tuning)
    for state in states:
        ask(agent, state)
    return write(agent, max_chars), agent


def write(agent, max_chars: int = MAX_BRIEFING_CHARS) -> str:
    situation = agent._plan_selector.situation
    rules = agent.ctx.tuning.plan_rules
    eligible = plans.open_plans(situation, rules, agent.ctx.plan == PRESS)
    return Briefing(Glossary.from_file(REPO_ROOT), max_chars).write(agent.world, agent.ctx, situation, eligible)


def section(text: str, title: str) -> str:
    """The lines of one section of a briefing, its title left out."""
    start = text.index(f"## {title}\n") + len(title) + 4
    end = text.find("\n## ", start)
    return text[start : len(text) if end < 0 else end]


# -- where the threat is ---------------------------------------------------------------------------


def test_threat_cell_a_major_threat_publishes_the_threatened_building_s_cell_and_none_once_the_base_is_quiet():
    agent = new_agent(BaseDefenseManager())
    rifles = [foe(500 + number, "e1", 24, 30 + number) for number in range(8)]

    ask(agent, base_state("england", *ALLIED_BASE, tick=1000, visible_enemies=rifles))
    during = (agent.ctx.threat_level, agent.ctx.threat_cell)
    ask(agent, base_state("england", *ALLIED_BASE, tick=1200))
    after = (agent.ctx.threat_level, agent.ctx.threat_cell)

    assert (during, after) == ((MAJOR_THREAT, REFINERY[1:]), (NO_THREAT, None))


# -- item 2: both armies by type, the glossary, the ratio, the ages and the threat -------------------

# Ours: two light tanks, a medium tank at 60%, four rifles (the home guard) and two rocket soldiers.
MIXED_OWN = (
    unit(300, "1tnk", 32, 48),
    unit(301, "1tnk", 33, 48),
    unit(302, "2tnk", 32, 49, hp=60),
    *[unit(303 + number, "e1", 26, 46 + number) for number in range(4)],
    unit(307, "e3", 32, 50),
    unit(308, "e3", 33, 50),
)
# Theirs: three rifles at our refinery, a heavy tank at their start and a pillbox by it.
RIFLES_AT_REFINERY = [foe(510 + number, "e1", 24, 31 + number) for number in range(3)]
HEAVY_TANK = foe(500, "3tnk")
PILLBOX = foe(600, "pbox", 74, 44)


def mixed_briefing(**more):
    first = at(600, units=MIXED_OWN, enemies=[HEAVY_TANK, *RIFLES_AT_REFINERY], enemy_buildings=[PILLBOX], **more)
    then = at(612, units=MIXED_OWN, enemies=RIFLES_AT_REFINERY, kills=1250, deaths=800)
    return briefing_after(first, then)


def test_briefing_2_names_each_type_in_words_with_its_glossary_line_the_ratio_the_ages_and_the_threat(glossary):
    text, agent = mixed_briefing()
    items, situation = agent.world.items, agent._plan_selector.situation

    assert [line for line in text.splitlines() if line.startswith("## ")] == list(SECTION_TITLES)
    for code in ("1tnk", "2tnk", "e1", "e3", "3tnk", "pbox"):
        assert f"- {glossary.line(code, items)}" in section(text, "Glossary"), code
    ours = section(text, "Our forces")
    assert "- 2 × Light Tank (1tnk): 1,400, health 100%" in ours
    assert "- 1 × Medium Tank (2tnk): 850, health 60%" in ours
    assert "- 4 × Rifle Infantry (e1): 400, health 100%" in ours and "- 2 × Rocket Soldier (e3): 600" in ours
    assert "guard 4 units, strength 400; main 5 units, strength 2,510; raid empty; scout empty" in ours
    enemy = section(text, "Enemy as known")
    assert "- 1 × Heavy Tank (3tnk): 1,150, last seen 12 s ago" in enemy
    assert "- 3 × Rifle Infantry (e1): 300, in sight now" in enemy
    assert "- 1 × Pillbox (pbox): 1 near their base" in enemy
    assert "Their base: seen; they started 52 cells east of our base's center." in enemy
    assert (
        "Last seen: an enemy building was last in sight 12 s ago; an enemy unit that fights is in sight now." in enemy
    )
    # Main's two light tanks, a medium tank at 60% and two rockets against the 1,450 of the enemy army seen lately.
    assert (situation.main_strength, situation.enemy_combat_value) == (2510, 1450)
    assert "Main's strength 2,510 against the enemy army seen in the last 30 s, worth 1,450: ratio 1.73." in text
    assert "Threat to our base: major, against our Ore Refinery (proc), 9 cells north of our base's center; " in text
    assert "enemy in sight there: 3 × Rifle Infantry (e1), worth 300." in text


def test_briefing_situation_gives_the_time_the_sides_the_plan_the_balance_the_money_and_the_power():
    text, _ = mixed_briefing()

    assert section(text, "Situation").splitlines()[:3] + section(text, "Situation").splitlines()[4:] == [
        "Time: 10:12 played, 9:48 left.",
        "Sides: we play England (Allies), the enemy Russia (Soviets).",
        "Plan in force: HOLD, set by the script's choice.",
        "Trade balance: +450 (we destroyed 1,250 of theirs and lost 800 of ours).",
        "Money: 5,000 to spend. Income: not known yet.",
        "Power: 200 made, 80 used, 120 spare.",
        "recent picks: none",
    ]


def test_briefing_comparison_gives_the_rules_weighing_their_thresholds_and_how_unseen_enemies_count():
    text, agent = mixed_briefing()
    situation = agent._plan_selector.situation

    comparison = section(text, "Comparison")
    assert (
        f"The plan rules weigh our army (the units that fight) at 3,250 against the enemy army at 1,450: ratio "
        f"{situation.ratio:.2f}. PRESS needs a ratio of 1.5 and an army of 3,000; the script holds below 1 and "
        f"presses from 2." in comparison
    )
    assert "an enemy unit not seen for 30 s drops out of the army seen. The enemy army is known while" in comparison
    assert "unknown, it is taken to be as strong as ours. It is known now." in comparison


# -- item 3: what an open plan would do, and why a closed one is closed ------------------------------

# Four medium tanks and eleven rifles, 4,400 of units that fight: the home guard takes five rifles and the scout one,
# so main holds the tanks and five rifles, 3,900.
STRONG_OWN = (
    *[unit(300 + number, "2tnk", 32, 46 + number) for number in range(4)],
    *[unit(310 + number, "e1", 26, 40 + number) for number in range(11)],
)


def test_briefing_3_with_press_open_and_raid_closed_press_gives_its_numbers_and_raid_its_reason():
    text, agent = briefing_after(at(600, units=STRONG_OWN, enemies=[HEAVY_TANK], enemy_buildings=[PILLBOX]))
    situation = agent._plan_selector.situation

    assert plans.open_plans(situation, agent.ctx.tuning.plan_rules) == [HOLD, PRESS]
    options = section(text, "Options").splitlines()
    assert options == [
        "- HOLD: main (9 units, strength 3,900) stays home and defends while the economy and the army grow.",
        "- RAID closed: no target a raid may hit: no enemy harvester seen in the last 30 s outside their defenses' "
        "reach, and no refinery the raid group could destroy once no harvester has been seen for 1:00.",
        "- PRESS: main (9 units, strength 3,900) attacks toward the enemy base once its strength is at least 3,000; "
        "known enemy defenses: 1 × Pillbox (pbox); the raids go on.",
    ]


def test_briefing_before_nine_minutes_press_is_closed_by_the_early_attack_guard_and_its_own_time():
    text, _ = briefing_after(at(300, units=STRONG_OWN, enemies=[HEAVY_TANK]))

    assert (
        "- PRESS closed: early-attack guard until 9:00 unless the enemy commits its army (an attack on our base "
        "beaten off, or 1,500 of theirs destroyed; 0 so far); not before 6:00." in section(text, "Options")
    )


def test_briefing_a_raid_target_opens_raid_with_the_group_its_quota_and_the_targets():
    harvester = foe(450, "harv", 60, 30)
    text, _ = briefing_after(at(600, units=STRONG_OWN, enemies=[HEAVY_TANK, harvester]))

    assert (
        "- RAID: the raid group (4 × Light Tank (1tnk); empty now) sets out once it is complete and hits the nearest "
        "of 1 targets no known defense covers (1 × Harvester (harv)); main (9 units, strength 3,900) stays home."
        in section(text, "Options")
    )
    assert "Other enemy units seen: 1 × Harvester (harv), in sight now." in text


@pytest.mark.parametrize(
    "tuning, base, faction, raid_words",
    [
        (tuning_off(raids=raids_off(enabled=False)), ALLIED_BASE, "england", "raids are switched off"),
        (tuning_off(), ALLIED_BASE[:-1], "england", "no War Factory (weap) stands to make the raid group in"),
        (
            tuning_off(composition=dataclasses.replace(CompositionTuning(), soviet_raid="none")),
            SOVIET_BASE,
            "russia",
            "our side fields no raid group now",
        ),
    ],
    ids=["raids off", "no war factory", "no raid quota"],
)
def test_briefing_raid_closed_names_the_rule_that_keeps_it_shut(tuning, base, faction, raid_words):
    text, _ = briefing_after(at(600, base=base, faction=faction), tuning=tuning)

    assert f"- RAID closed: {raid_words}; no target a raid may hit" in section(text, "Options")


def test_briefing_press_closed_by_a_small_army_and_by_an_unknown_enemy_army_names_both_figures():
    text, _ = briefing_after(at(600, units=STRONG_OWN[:2]))

    assert (
        "- PRESS closed: our army is worth 1,700 as the plan rules weigh it, under the 3,000 needed; our army "
        "(1,700) is under 1.5 times the enemy's as the plan rules weigh it (1,700), the enemy army being unknown and "
        "so taken to be as strong as ours." in section(text, "Options")
    )


# -- item 4 and the trimming order -----------------------------------------------------------------

OWN_BUILDINGS = (
    ("fact", 22, 48),
    ("powr", 19, 48),
    ("apwr", 16, 48),
    ("tent", 22, 44),
    ("proc", 24, 39),
    ("weap", 22, 53),
    ("fix", 26, 56),
    ("dome", 18, 54),
    ("silo", 28, 40),
    ("pbox", 30, 44),
    ("gun", 30, 52),
    ("hpad", 14, 44),
)
OWN_UNIT_TYPES = ("e1", "e3", "medi", "jeep", "1tnk", "2tnk", "arty", "harv", "mcv", "truk")
ENEMY_UNIT_TYPES = ("e2", "e4", "dog", "thf", "3tnk", "4tnk", "apc", "ftrk", "v2rl", "yak", "mig")
ENEMY_BUILDING_TYPES = ("barr", "kenn", "stek", "afld", "ftur", "tsla", "sam")


def forty_types() -> tuple[str, list[dict], list[dict]]:
    """A briefing of 40 types: 12 own building types, 10 own unit types, 11 enemy unit types and 7 enemy building
    types, three of each unit; half the enemy unit types were last seen 200 s before, the other half are in sight."""
    units = [
        unit(300 + 3 * index + copy, unit_type, 30 + copy, 40 + index)
        for index, unit_type in enumerate(OWN_UNIT_TYPES)
        for copy in range(3)
    ]
    enemies = [
        foe(500 + 3 * index + copy, unit_type, 70 + copy, 30 + index)
        for index, unit_type in enumerate(ENEMY_UNIT_TYPES)
        for copy in range(3)
    ]
    buildings = [
        foe(700 + index, building_type, 76, 36 + 2 * index) for index, building_type in enumerate(ENEMY_BUILDING_TYPES)
    ]
    early = at(400, units=units, enemies=enemies[: len(enemies) // 2], enemy_buildings=buildings, base=OWN_BUILDINGS)
    late = at(600, units=units, enemies=enemies[len(enemies) // 2 :], base=OWN_BUILDINGS, kills=3000, deaths=1000)
    return early, late


def test_briefing_4_a_40_type_briefing_is_at_most_9000_characters_with_the_options_whole():
    early, late = forty_types()

    text, agent = briefing_after(early, late)

    types = {
        actor.type
        for actors in (agent.world.units, agent.world.buildings, agent.world.enemy_memory)
        for actor in actors.values()
    }
    assert len(types) == 40
    assert len(text) <= MAX_BRIEFING_CHARS and text.endswith(section(text, "Options"))
    assert len(section(text, "Options").splitlines()) == 3


def glossary_lines(text: str) -> list[str]:
    return section(text, "Glossary").splitlines()


@pytest.fixture(scope="module")
def trimmed() -> dict[str, str]:
    """The 40-type briefing untrimmed, then under caps each one character shorter than the briefing before: the
    trimming's three steps, one at a time."""
    early, late = forty_types()
    full, agent = briefing_after(early, late, max_chars=10**6)
    shorter = write(agent, len(full) - 1)
    summed_up = write(agent, len(shorter) - 1)
    return {"full": full, "shorter": shorter, "summed_up": summed_up, "cut": write(agent, len(summed_up) - 1)}


def test_briefing_trimming_untrimmed_has_a_glossary_line_for_each_of_the_40_types(trimmed):
    assert len(glossary_lines(trimmed["full"])) == 40
    assert "more types last seen over 120 s ago" not in trimmed["full"]


def test_briefing_trimming_first_keeps_the_glossary_lines_of_the_20_most_valuable_types(trimmed):
    shorter = trimmed["shorter"]

    assert len(glossary_lines(shorter)) == GLOSSARY_KEPT_TYPES and "more types last seen" not in shorter
    # Three Mammoth Tanks at 6,000 lead; three rifles at 300 are left out.
    assert glossary_lines(shorter)[0].startswith("- Mammoth Tank (4tnk)")
    assert "(e1)" not in section(shorter, "Glossary")


def test_briefing_trimming_then_sums_up_the_enemy_types_last_seen_over_120_s_ago_in_one_line(trimmed):
    assert "- 4 more types last seen over 120 s ago: 12 units worth 5,400" in trimmed["summed_up"]
    assert len(trimmed["summed_up"]) < len(trimmed["shorter"])


def test_briefing_trimming_last_cuts_at_a_whole_line_before_the_options_with_a_note(trimmed):
    summed_up, cut = trimmed["summed_up"], trimmed["cut"]
    kept = cut[: cut.index(CUT_NOTE)]

    assert len(cut) <= len(summed_up) - 1
    assert summed_up.startswith(kept) and kept.endswith("\n")
    assert cut.endswith(f"{CUT_NOTE}\n\n## Options\n{section(summed_up, 'Options')}")


def test_briefing_cut_to_a_cap_with_no_room_before_the_options_keeps_only_the_note_and_the_options():
    early, late = forty_types()
    _, agent = briefing_after(early, late)
    options = "## Options\n" + section(write(agent), "Options")

    text = write(agent, len(options) + len(CUT_NOTE) + 3)

    assert text == f"\n{CUT_NOTE}\n\n{options}"


# -- item 7 ------------------------------------------------------------------------------------------


def test_briefing_7_writing_a_40_type_briefing_takes_under_5_ms():
    early, late = forty_types()
    _, agent = briefing_after(early, late)
    situation = agent._plan_selector.situation
    eligible = plans.open_plans(situation, agent.ctx.tuning.plan_rules)
    writer = Briefing(Glossary.from_file(REPO_ROOT))

    timings = []
    for _ in range(TIMINGS):
        started = time.perf_counter()
        writer.write(agent.world, agent.ctx, situation, eligible)
        timings.append((time.perf_counter() - started) * 1000)

    assert statistics.median(timings) < MAX_BUILD_MS, timings


# -- the other figures -------------------------------------------------------------------------------


def test_briefing_with_no_threat_no_clock_and_short_power_says_so():
    state = at(600, remaining=None)
    state["time"]["remaining_seconds"] = None
    state["observation"]["economy"].update(power_provided=100, power_drained=160)

    text, _ = briefing_after(state)

    situation = section(text, "Situation")
    assert "Time: 10:00 played, not known left." in situation and "Threat to our base: none." in situation
    assert "Power: 100 made, 160 used, short by 60, so everything is built slower." in situation


def test_briefing_with_no_enemy_seen_says_so_in_each_line():
    text, _ = briefing_after(at(600))

    enemy = section(text, "Enemy as known")
    assert enemy.splitlines() == [
        "Enemy army seen: none so far.",
        "Enemy buildings seen: none.",
        "Enemy defenses seen: none.",
        "Their base: not found yet; they started 52 cells east of our base's center.",
        "Last seen: no enemy building has been seen yet; no enemy unit that fights has been seen yet.",
    ]
    assert "Main's strength 0; the enemy army seen in the last 30 s is worth nothing, so there is no ratio" in text
    assert "against the enemy army at 0: ratio 99.00." in text


def test_briefing_once_the_enemy_is_out_of_sight_says_how_long_ago_each_was_last_seen():
    first = at(600, units=MIXED_OWN, enemies=[HEAVY_TANK, *RIFLES_AT_REFINERY], enemy_buildings=[PILLBOX])
    then = at(612, units=MIXED_OWN, enemies=RIFLES_AT_REFINERY)

    text, _ = briefing_after(first, then, at(630, units=MIXED_OWN))

    enemy = section(text, "Enemy as known")
    assert "- 3 × Rifle Infantry (e1): 300, last seen 18 s ago" in enemy
    assert (
        "Last seen: an enemy building was last in sight 30 s ago; an enemy unit that fights was last in sight 18 s ago."
        in enemy
    )


def test_briefing_a_defense_away_from_their_base_is_forward_with_its_distance_from_ours():
    turret = foe(600, "gun", 42, 48)

    text, _ = briefing_after(at(600, enemy_buildings=[turret]))

    assert "- 1 × Turret (gun): 1 forward, 20 cells from our base" in section(text, "Enemy as known")


def test_briefing_with_the_guards_off_and_forward_hold_on_words_the_rules_as_they_then_are():
    rules = dataclasses.replace(plan_rules_off(), blind_press_guard=False, early_attack_guard=False)
    tuning = tuning_off(plan_rules=rules, combat=dataclasses.replace(combat_off(), forward_hold=True))

    text, _ = briefing_after(at(600, units=STRONG_OWN, enemies=[HEAVY_TANK]), tuning=tuning)

    assert "The plan rules weigh our army (every unit, harvesters included)" in text
    assert "drops out of the army seen, and the plan rules weigh only what is seen." in text
    assert "- HOLD: main (9 units, strength 3,900) holds a forward point between the bases" in text


def test_briefing_an_assumed_share_other_than_1_is_given_as_times_ours():
    tuning = tuning_off(plan_rules=dataclasses.replace(plan_rules_off(), assumed_enemy_share=0.5))

    text, _ = briefing_after(at(600), tuning=tuning)

    assert "unknown, it is taken to be 0.5 times as strong as ours. It is not known now." in text


@pytest.mark.parametrize(
    "rule, words",
    [
        (plans.R1, "the opening, which lasts until a war factory stands"),
        (plans.R2, "the clock rule that keeps a lead in the last 3:00"),
        (plans.R3, "the clock rule that holds at an even balance in the last 3:00"),
        (plans.R4, "the clock rule that presses when behind in the last 2:00"),
        (plans.R5, "the early strike on an undefended base"),
        (plans.R6, "the finishing attack"),
        (plans.MODEL, "a model's choice"),
        (plans.SPARRING, "a sparring pin"),
        (None, "no rule yet"),
    ],
)
def test_briefing_names_the_rule_that_set_the_plan_in_force_in_words(rule, words):
    _, agent = briefing_after(at(600))
    agent.ctx.plan_rule = rule

    assert f", set by {words}." in write(agent)


def test_briefing_a_threat_against_a_cell_with_nothing_of_ours_and_no_enemy_in_sight_names_the_cell():
    _, agent = briefing_after(at(600))
    agent.ctx.threat_level, agent.ctx.threat_cell = MAJOR_THREAT, (22, 30)

    assert (
        "Threat to our base: major, against the cell 22,30, 18 cells north of our base's center; no enemy in sight"
        in write(agent)
    )


def test_briefing_a_threat_at_the_base_s_center_and_a_world_without_one_are_worded_as_such():
    _, agent = briefing_after(at(600))
    agent.ctx.threat_level, agent.ctx.threat_cell = MAJOR_THREAT, (22, 48)
    at_center = write(agent)
    agent.world.base_center = agent.world.enemy_base = None
    without = write(agent)

    assert "against our Construction Yard (fact), at our base's center;" in at_center
    assert "against our Construction Yard (fact), at 22,48;" in without
    assert "Their base: not found yet; where they started is not known." in without


def test_briefing_a_type_the_glossary_does_not_explain_is_named_by_its_code_without_a_glossary_line():
    shock = foe(520, "shok", 70, 48)

    text, _ = briefing_after(at(600, enemies=[shock]))

    assert "- 1 × shok: 0, in sight now" in text and "(shok)" not in text


def test_briefing_income_once_known_is_given_per_minute():
    _, agent = briefing_after(at(600))
    agent.ctx.income.income_per_s = 12.5

    assert "Income: 750 per minute." in write(agent)


def test_briefing_an_unknown_faction_is_said_to_be_unknown():
    _, agent = briefing_after(at(600))
    agent.world.enemy_faction = None
    agent.world.faction = "atlantis"

    assert "Sides: we play Atlantis (an unknown side), the enemy an unknown faction." in write(agent)


def test_briefing_a_defense_seen_with_no_base_center_known_stands_away_from_their_base():
    _, agent = briefing_after(at(600, enemy_buildings=[foe(600, "gun", 42, 48)]))
    agent.world.base_center = None

    assert "- 1 × Turret (gun): 1 away from their base" in write(agent)


def test_briefing_module_writes_its_figures_and_reads_nothing(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("the briefing opened a file")

    _, agent = briefing_after(at(600))
    writer = Briefing(Glossary.from_file(REPO_ROOT))
    monkeypatch.setattr("builtins.open", refuse)

    assert writer.write(agent.world, agent.ctx, agent._plan_selector.situation, [HOLD]).startswith("## Situation")
    assert briefing_module.MAX_BRIEFING_CHARS == 9000 and RAID in briefing_module.DISCRETIONARY_PLANS


# -- items 5 and 6: the request the briefing goes in -------------------------------------------------

BRIEFED = RedAlertSettings(model_plan_choice=True, model_briefing=True)
SUMMARIZED = RedAlertSettings(model_plan_choice=True)
# An enemy harvester no defense covers: with it RAID is open beside HOLD at 280 s, when PRESS is not yet.
HARVESTER = foe(450, "harv", 60, 30)
ASKED_AT_SECONDS = 280
ENDPOINT_URL = "http://127.0.0.1:11500"
MODEL_REASON = "No threat to our base and an enemy harvester open; our army is not yet strong enough to press."


def asked(settings: RedAlertSettings):
    """The plan choice an agent of the plan rules alone puts at 280 s with HOLD and RAID open, and the agent."""
    chooser = StubChooser()
    agent = new_agent(settings=settings, chooser=chooser)
    ask(agent, at(ASKED_AT_SECONDS, enemies=[HARVESTER]))
    (request,) = chooser.submitted
    return request, agent


def test_briefing_5_with_the_switch_on_a_model_is_asked_with_the_briefing_its_question_and_a_reason_first():
    request, _ = asked(BRIEFED)

    assert request.option_ids() == [HOLD, RAID]
    assert request.context.startswith("## Situation\nTime: 4:40 played, 15:20 left.\n")
    assert "Plan in force: OPENING, set by no rule yet." in request.context
    assert "\n- RAID: the raid group " in request.context and "\n- PRESS closed: early-attack guard" in request.context
    assert request.question == plans.plan_prompt(briefed=True)
    assert request.question.startswith("Choose the plan of our Red Alert army. The briefing above")
    assert (request.reason_first, request.reason_words, request.max_answer_tokens) == (True, 30, 96)


def test_briefing_5_a_stubbed_model_receives_the_briefing_as_the_context_with_a_reason_first_schema():
    request, _ = asked(BRIEFED)
    bodies = []

    def answer(sent: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(sent.content))
        content = json.dumps({"reason": MODEL_REASON, "choice": RAID})
        return httpx.Response(
            200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
        )

    source = LocalModelSource("small", base_url=ENDPOINT_URL, reasoning="off", transport=httpx.MockTransport(answer))

    result = source.choose(request)

    (body,) = bodies
    system, user = (message["content"] for message in body["messages"])
    schema = body["response_format"]["json_schema"]["schema"]
    assert user == local.choice_prompt(request) and user.startswith(f"{request.context}\n\n{request.question}")
    assert (system, body["max_tokens"]) == (local.reason_first_system_prompt(30), 96)
    assert list(schema["properties"]) == ["reason", "choice"] and schema["required"] == ["reason", "choice"]
    assert schema["properties"]["choice"] == {"enum": [HOLD, RAID]}
    assert (result.option_id, result.outcome, result.model_reason) == (RAID, "used", MODEL_REASON)


def test_briefing_6_with_the_switch_off_the_request_is_today_s_with_the_summary_as_the_context(monkeypatch):
    written = []

    def summarizing(*figures):
        written.append(summarize(*figures))
        return written[-1]

    monkeypatch.setattr(plans, "summarize", summarizing)

    request, agent = asked(SUMMARIZED)

    ctx = agent.ctx
    (summary,) = written
    assert summary == summarize(agent.world, ctx.scout_report, OPENING, ctx.groups, ctx.threat_level)
    assert request == ChoiceRequest(
        question=plans.plan_prompt(),
        options=[Option(plan, plans.PLAN_DESCRIPTIONS[plan]) for plan in (HOLD, RAID)],
        context=f"{summary}\nrecent picks: none",
        deadline_ms=1500,
        state_tag=str(ASKED_AT_SECONDS * TICKS_PER_SECOND),
    )
    assert (request.reason_first, request.max_answer_tokens) == (False, None)


def test_briefing_the_tracked_settings_brief_a_model_once_model_plan_choice_is_switched_on():
    tracked = RedAlertSettings.from_file(REPO_ROOT)

    # The plan's chooser is the stub; the commander, which would have a chooser of its own, is off.
    request, _ = asked(dataclasses.replace(settings_models_off(), model_plan_choice=True))

    assert tracked.model_briefing and request.reason_first and request.context.startswith("## Situation")


def test_briefing_with_the_glossary_unusable_the_agent_says_so_and_the_model_reads_the_summary(match_record):
    # The record's folder stands in for the repository root, and holds no glossary.
    request, _ = asked(BRIEFED)

    (error,) = match_record("error")
    assert (error["where"], error["exception"]) == ("redalert.config", "ConfigError")
    assert request.context.splitlines()[-2:] == ["plan=OPENING", "recent picks: none"]
    assert request.question == plans.plan_prompt() and not request.reason_first


def test_briefing_with_its_question_unreadable_the_agent_says_so_and_asks_nobody(match_record, monkeypatch, run_folder):
    # The record's folder stands in for the repository root: the glossary is copied into it, the question is not there.
    (run_folder / GLOSSARY_FILE).parent.mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / GLOSSARY_FILE, run_folder / GLOSSARY_FILE)
    monkeypatch.setattr(plans, "BRIEFING_PROMPT_FILE", run_folder / "missing.txt")
    chooser = StubChooser()
    agent = new_agent(settings=BRIEFED, chooser=chooser)

    ask(agent, at(ASKED_AT_SECONDS, enemies=[HARVESTER]))

    (error,) = match_record("error")
    assert (error["where"], error["exception"], chooser.submitted) == ("redalert.config", "FileNotFoundError", [])


def test_briefing_with_model_plan_choice_off_no_glossary_is_read_and_nobody_is_asked(monkeypatch):
    read = []
    monkeypatch.setattr(Glossary, "from_file", classmethod(lambda cls, root: read.append(root)))
    chooser = StubChooser()
    agent = new_agent(settings=RedAlertSettings(model_briefing=True), chooser=chooser)

    ask(agent, at(ASKED_AT_SECONDS, enemies=[HARVESTER]))

    assert (read, chooser.submitted) == ([], [])


# -- a RAID closed because no raid could set out now (launchable) ------------------------------------

LAUNCHABLE = tuning_off(raids=raids_off(launchable_only=True))
# Seven rifles for the home guard and the scout, and four light tanks of main at the staging point (32, 48).
RIFLES = tuple(unit(310 + number, "e1", 26, 40 + number) for number in range(7))
FOUR_LIGHT_TANKS = tuple(unit(300 + number, "1tnk", 32, 46 + number) for number in range(4))
# The third and fourth 30 cells forward of the staging point: not on hand.
TWO_LIGHT_TANKS_ON_HAND = (*FOUR_LIGHT_TANKS[:2], unit(302, "1tnk", 62, 46), unit(303, "1tnk", 62, 47))
UNCOVERED_HARVESTER = foe(450, "harv", 60, 48)
# Two pillboxes that cover the straight way to the harvester and both ways round.
EVERY_WAY_COVERED = (foe(600, "pbox", 42, 40), foe(601, "pbox", 42, 56))


@pytest.mark.parametrize(
    "tanks, enemies, buildings, raid_line",
    [
        (FOUR_LIGHT_TANKS, (), (), "- RAID closed: no raid target known."),
        (FOUR_LIGHT_TANKS, (UNCOVERED_HARVESTER,), EVERY_WAY_COVERED, "- RAID closed: no safe route to a raid target."),
        (
            TWO_LIGHT_TANKS_ON_HAND,
            (UNCOVERED_HARVESTER,),
            (),
            "- RAID closed: no units for a raid on hand: need 4 1tnk, have 2 1tnk.",
        ),
    ],
    ids=["no target", "no safe route", "2 of 4 on hand"],
)
def test_launchable_briefing_raid_closed_names_the_part_of_the_launch_check_that_fails(
    tanks, enemies, buildings, raid_line
):
    state = at(600, units=(*RIFLES, *tanks), enemies=enemies, enemy_buildings=buildings)

    text, agent = briefing_after(state, tuning=LAUNCHABLE)

    assert raid_line in section(text, "Options").splitlines()
    assert raid_line[len("- RAID closed: ") : -1] == agent._plan_selector.situation.raid_launch_blocker


def test_launchable_briefing_with_four_light_tanks_on_hand_and_a_target_raid_is_open_and_with_the_switch_off_too():
    state = at(600, units=(*RIFLES, *FOUR_LIGHT_TANKS), enemies=[UNCOVERED_HARVESTER])
    short = at(600, units=(*RIFLES, *TWO_LIGHT_TANKS_ON_HAND), enemies=[UNCOVERED_HARVESTER])

    on, _ = briefing_after(state, tuning=LAUNCHABLE)
    off, _ = briefing_after(short)

    assert all("\n- RAID: the raid group (4 × Light Tank (1tnk)" in section(text, "Options") for text in (on, off))


# -- the compact prompt ----------------------------------------------------------------------------

# Five recorded states, from the opening to a base under attack.
RECORDED_STATES = ("opening_mcv", "midgame_allies", "midgame_soviet", "base_under_attack", "wreck")
ALL_PLANS = [HOLD, RAID, PRESS]
# The user message of a compact prompt holds the key lines, `recent picks: none` before the model's first pick, and the
# heading before its plans' lines.
COMPACT_HEAD_LINES = len(compact_prompt.KEYS) + 2


def agent_after(*states: dict):
    """An agent with base defense and scouting that has taken in the states."""
    agent = new_agent(BaseDefenseManager(), ScoutingManager())
    for state in states:
        ask(agent, state)
    return agent


def compact_of(agent, eligible: list[str] | None = None, prompt: CompactPrompt | None = None) -> ChoiceRequest:
    """The compact plan question of the agent's last cycle, offering `eligible` or the plans open as the plan rules
    open them."""
    situation = agent._plan_selector.situation
    offered = eligible or plans.open_plans(situation, agent.ctx.tuning.plan_rules, agent.ctx.plan == PRESS)
    writer = prompt or CompactPrompt()
    return writer.request(agent.world, agent.ctx, situation, offered, 1500, str(agent.world.tick))


def compact_after(*states: dict, eligible: list[str] | None = None, prompt: CompactPrompt | None = None):
    """The compact plan question an agent with base defense and scouting puts after taking in the states, and the
    agent."""
    agent = agent_after(*states)
    return compact_of(agent, eligible, prompt), agent


def user_lines(request: ChoiceRequest) -> list[str]:
    """The lines of the user message a model source sends for the request."""
    return local.choice_prompt(request).splitlines()


def values_of(request: ChoiceRequest) -> dict[str, str]:
    """Each key of a compact prompt -> its value."""
    return dict(line.split("=", 1) for line in request.context.splitlines() if "=" in line)


@pytest.mark.parametrize("name", RECORDED_STATES)
@pytest.mark.parametrize("offered", [None, ALL_PLANS], ids=["the open plans", "all three plans"])
def test_compact_prompt_1_a_recorded_state_is_within_1500_characters_with_every_key_in_order_and_its_plans(
    name, offered
):
    request, agent = compact_after(fixture_state(name), eligible=offered)

    situation = agent._plan_selector.situation
    expected = offered or plans.open_plans(situation, agent.ctx.tuning.plan_rules, agent.ctx.plan == PRESS)
    lines = user_lines(request)
    assert compact_prompt.prompt_chars(request) <= compact_prompt.MAX_PROMPT_CHARS
    assert [line.partition("=")[0] for line in lines[: len(compact_prompt.KEYS)]] == list(compact_prompt.KEYS)
    assert lines[len(compact_prompt.KEYS) : COMPACT_HEAD_LINES] == ["recent picks: none", "OPEN:"]
    assert [line.partition(":")[0] for line in lines[COMPACT_HEAD_LINES:]] == expected == request.option_ids()


def test_compact_prompt_1_the_plans_lines_are_the_fixed_table_s_for_the_open_plans_only():
    request, _ = compact_after(at(ASKED_AT_SECONDS, enemies=[HARVESTER]))

    assert request.option_ids() == [HOLD, RAID]
    assert user_lines(request)[COMPACT_HEAD_LINES:] == [
        "HOLD: Keep main home; grow forces but concede map pressure.",
        "RAID: Send the raid group at exposed harvesters; main holds.",
    ]
    assert set(compact_prompt.OPTION_LINES) == {HOLD, RAID, PRESS}
    assert all(len(line.split()) <= 15 for line in compact_prompt.OPTION_LINES.values())


def test_compact_prompt_the_request_has_the_system_text_48_answer_tokens_and_no_reason_first():
    request, _ = compact_after(at(ASKED_AT_SECONDS, enemies=[HARVESTER]))

    assert request.system_text == (
        "Choose one OPEN plan. Use only supplied facts. Enemy counts may be stale; unknown is not zero. Prefer viable "
        'pressure when home is safe; HOLD must have a concrete benefit. Return only {"choice":"ID","reason":"up to 12 '
        'words"}.'
    )
    assert (request.answer_tokens, request.reason_first, request.question) == (48, False, "OPEN:")
    assert (request.deadline_ms, request.state_tag) == (1500, str(ASKED_AT_SECONDS * TICKS_PER_SECOND))


# Ours: seven rifles (the guard and the scout) and four light tanks (the raid group); theirs: a harvester no defense
# covers and a refinery, both in sight at 600 s and out of it 20 s later.
ECONOMY_OWN = (*RIFLES, *FOUR_LIGHT_TANKS)
ENEMY_REFINERY = foe(601, "proc", 74, 40)


def test_compact_prompt_the_values_are_the_cycle_s_own_figures():
    seen = at(600, units=ECONOMY_OWN, enemies=[UNCOVERED_HARVESTER], enemy_buildings=[ENEMY_REFINERY])
    agent = agent_after(seen, at(620, units=ECONOMY_OWN))
    for tank in FOUR_LIGHT_TANKS:
        agent.ctx.groups.assign(tank["id"], RAID_GROUP)

    request = compact_of(agent)

    situation, rules = agent._plan_selector.situation, agent.ctx.tuning.plan_rules
    values = values_of(request)
    assert (values["left_s"], values["base_threat"]) == ("580", "none")
    assert values["army_value"] == str(plans.weighed_army(situation, rules))
    assert values["enemy_estimate"] == str(round(plans.weighed_enemy(situation, rules)))
    assert values["army_ratio"] == f"{situation.ratio:.2f}"
    assert (values["cash"], values["our_refineries"], values["our_harvesters"]) == ("5000", "1", "0")
    assert (values["enemy_refineries"], values["enemy_harvesters"], values["enemy_economy_age_s"]) == ("1", "1", "20")
    raid_value = 4 * agent.world.items["1tnk"].cost
    assert (values["raid_ready"], values["raid_value"], values["raid_targets"]) == ("yes", str(raid_value), "1")
    assert values["raid_target_age_s"] == "20"
    agent.ctx.groups.assign(FOUR_LIGHT_TANKS[0]["id"], MAIN_GROUP)
    assert (values_of(compact_of(agent))["raid_ready"], values_of(compact_of(agent))["raid_value"]) == (
        "no",
        str(3 * agent.world.items["1tnk"].cost),
    )


def test_compact_prompt_before_anything_is_known_the_values_say_unknown_never_and_none():
    request, agent = compact_after(at(100))

    values = values_of(request)
    assert (values["income_per_min"], values["enemy_estimate_kind"]) == ("unknown", "assumed")
    assert (values["last_enemy_army_seen_s"], values["enemy_economy_age_s"]) == ("never", "never")
    assert (values["raid_ready"], values["raid_target_age_s"], values["raid_targets"]) == ("no", "none", "0")
    assert values["press_block"] == "early_attack_guard,press_too_early,army_too_small"
    no_clock = dataclasses.replace(agent._plan_selector.situation, remaining_seconds=None)
    assert compact_prompt.state_values(agent.world, agent.ctx, no_clock)["left_s"] == "unknown"


def test_compact_prompt_with_press_open_press_block_is_none_and_the_enemy_seen_lately_is_weighed_at_its_peak():
    tanks = tuple(unit(320 + number, "2tnk", 32, 40 + number) for number in range(8))

    request, _ = compact_after(at(600, units=(*RIFLES, *tanks), enemies=[foe(500, "e1", 60, 48)], kills=2000))

    values = values_of(request)
    assert PRESS in request.option_ids()
    assert (values["press_block"], values["enemy_estimate_kind"], values["last_enemy_army_seen_s"]) == (
        "none",
        "seen_peak",
        "0",
    )


def _answering(choice: str, bodies: list[dict]) -> LocalModelSource:
    """A local source whose model server answers `choice` with a short reason, noting each body it is sent."""

    def answer(sent: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(sent.content))
        content = json.dumps({"choice": choice, "reason": "harvester exposed and home is safe"})
        return httpx.Response(200, json={"choices": [{"index": 0, "message": {"content": content}}]})

    transport = httpx.MockTransport(answer)
    return LocalModelSource("small", base_url=ENDPOINT_URL, reasoning="off", choice_reasons=True, transport=transport)


def test_compact_prompt_2_the_schema_puts_choice_first_enum_constrained_and_a_closed_plan_is_rejected():
    request, _ = compact_after(at(ASKED_AT_SECONDS, enemies=[HARVESTER]))
    bodies: list[dict] = []

    used = _answering(RAID, bodies).choose(request)
    closed = _answering(PRESS, bodies).choose(request)

    body = bodies[0]
    schema = body["response_format"]["json_schema"]["schema"]
    assert list(schema["properties"]) == ["choice", "reason"] and schema["required"] == ["choice"]
    assert schema["properties"]["choice"] == {"enum": [HOLD, RAID]}
    assert (body["messages"][0]["content"], body["max_tokens"]) == (compact_prompt.SYSTEM_TEXT, 48)
    assert body["messages"][1]["content"] == local.choice_prompt(request)
    assert (used.option_id, used.outcome, used.model_reason) == (RAID, "used", "harvester exposed and home is safe")
    assert (closed.option_id, closed.outcome, closed.reason) == (None, "invalid", "not_an_option")


def test_compact_prompt_3_no_glossary_line_unit_name_or_unit_code_appears_in_the_prompt(glossary):
    state = at(600, units=MIXED_OWN, enemies=[HEAVY_TANK, *RIFLES_AT_REFINERY, HARVESTER], enemy_buildings=[PILLBOX])

    request, agent = compact_after(state, eligible=ALL_PLANS)

    text = f"{request.system_text}\n{local.choice_prompt(request)}"
    items = agent.world.items
    codes = {item.name for item in items}
    names = [entry.name for entry in (glossary.get(code) for code in codes) if entry is not None]
    lines = [line for line in (glossary.line(code, items) for code in codes) if line is not None]
    assert not set(re.split(r"[^a-z0-9]+", text.lower())) & codes
    assert [name for name in names if name in text] == []
    assert [line for line in lines if line in text] == []


def test_compact_prompt_over_the_cap_the_plans_lines_are_cut_to_10_words_and_every_key_and_plan_stays():
    long_lines = {plan: f"{plan} " + " ".join(f"word{number}" for number in range(30)) for plan in ALL_PLANS}
    full, _ = compact_after(at(600), eligible=ALL_PLANS, prompt=CompactPrompt(option_lines=long_lines))
    cap = compact_prompt.prompt_chars(full) - 1

    request, _ = compact_after(at(600), eligible=ALL_PLANS, prompt=CompactPrompt(cap, long_lines))

    plan_lines = user_lines(request)[COMPACT_HEAD_LINES:]
    assert compact_prompt.prompt_chars(request) <= cap
    assert request.context == full.context and request.option_ids() == ALL_PLANS
    assert [len(line.partition(": ")[2].split()) for line in plan_lines] == [10, 10, 10]
    assert plan_lines[0] == "HOLD: HOLD word0 word1 word2 word3 word4 word5 word6 word7 word8"


def test_compact_prompt_still_over_the_cap_with_the_plans_lines_cut_is_an_error():
    with pytest.raises(ValueError, match="over its cap of 500"):
        compact_after(at(600), eligible=ALL_PLANS, prompt=CompactPrompt(max_chars=500))


def test_compact_prompt_reads_nothing(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("the compact prompt opened a file")

    _, agent = compact_after(at(600))
    monkeypatch.setattr("builtins.open", refuse)

    request = CompactPrompt().request(agent.world, agent.ctx, agent._plan_selector.situation, [HOLD], 1500, "1")
    assert request.option_ids() == [HOLD]
