"""The compact shorthand: the state summary a model reads, and the command shorthand turned into orders."""

from __future__ import annotations

import builtins
import random
import time
from pathlib import Path

import pytest

import situations
from situations import ask, fixture_state, new_agent
from switches_off import SWITCHES_OFF
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.batch import Limits, Priority, pack
from ucla_agent.games.redalert.groups import Groups
from ucla_agent.games.redalert.managers import MAJOR_THREAT, NO_THREAT
from ucla_agent.games.redalert.scout_report import EnemyIntel, ScoutReport
from ucla_agent.games.redalert.shorthand import MAX_SUMMARY_CHARS, Commands, summarize, translate
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.world import World

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "redalert"
SUMMARY_KEYS = (
    "t=",
    "me=",
    "en=",
    "cash=",
    "pw=",
    "harv=",
    "bal=",
    "army=",
    "assets=",
    "grp",
    "guard=",
    "main=",
    "raid=",
    "bld",
    "seen=",
    "def=",
    "veh=",
    "age=",
    "threat=",
    "plan=",
)
# summarize and translate each take less than this, in milliseconds.
MAX_MS = 5


def midgame() -> tuple[World, ScoutReport, Groups]:
    """The Allied mid-game fixture as a world, the report its enemies make, and its groups with a guard of 5 rifles."""
    world = World(situations.tracked_items())
    world.update(fixture_state("midgame_allies"))
    groups = Groups()
    groups.set_guard_quota("e1", 5)
    groups.update(world)
    return world, EnemyIntel().observe(world, Tuning().scouting), groups


# -- the summary -----------------------------------------------------------------------------------


def test_the_summary_of_the_mid_game_fixture_is_exactly_the_stored_9_lines():
    world, report, groups = midgame()

    text = summarize(world, report, "RAID", groups, NO_THREAT)

    assert text + "\n" == (FIXTURES / "summary_midgame.txt").read_text(encoding="ascii")
    assert len(text.splitlines()) == 9


def test_with_60_building_types_the_summary_is_cut_to_900_characters_at_its_bld_line_and_ends_with_the_plan():
    world, report, groups = midgame()
    buildings = [situations.building(500 + number, f"building_type_{number:02}", 30, 30) for number in range(60)]
    world.update(fixture_state("midgame_allies", buildings=buildings))

    text = summarize(world, report, "PRESERVE", groups, MAJOR_THREAT)

    lines = text.splitlines()
    assert len(text) <= MAX_SUMMARY_CHARS and lines[-1] == "plan=PRESERVE" and len(lines) == 9
    assert lines[5].startswith("bld building_type_00x1 ") and 0 < lines[5].count("x1") < 60
    assert lines[7] == "threat=major"


def test_the_summary_is_plain_ascii_and_every_figure_a_whole_number():
    world, report, groups = midgame()
    world.update(fixture_state("midgame_allies", buildings=[situations.building(500, "café", 30, 30)]))

    text = summarize(world, report, "HOLD", groups, NO_THREAT)

    assert text.isascii() and "bld caf?x1" in text and "." not in text


def test_before_anything_is_known_the_enemy_line_says_so():
    world, _, groups = midgame()

    text = summarize(world, ScoutReport(), "OPENING", groups, NO_THREAT)

    assert "en seen=0 def=0 army=0 harv=0 veh=0 age=-1" in text.splitlines()


def test_an_enemy_faction_the_state_does_not_name_is_unknown():
    world, report, groups = midgame()
    world.enemy_faction = None

    assert summarize(world, report, "HOLD", groups, NO_THREAT).splitlines()[1] == "me=allies en=unknown"


def test_the_summary_names_every_key_and_reads_no_file_in_under_5_ms(monkeypatch):
    world, report, groups = midgame()
    opened = []
    monkeypatch.setattr(builtins, "open", lambda *arguments, **options: opened.append(arguments))

    started = time.perf_counter()
    texts = [summarize(world, report, "RAID", groups, NO_THREAT) for _ in range(100)]
    mean_ms = (time.perf_counter() - started) * 1000 / len(texts)

    assert opened == [] and mean_ms < MAX_MS
    assert all(key in texts[0] for key in SUMMARY_KEYS)


@pytest.mark.parametrize("state, level", [("base_under_attack", MAJOR_THREAT), ("midgame_allies", NO_THREAT)])
def test_the_base_defense_manager_publishes_how_big_the_threat_is(state, level):
    agent = new_agent(BaseDefenseManager())

    ask(agent, fixture_state(state))

    assert agent.ctx.threat_level == level


def test_one_cheap_enemy_near_the_base_is_a_minor_threat():
    agent = new_agent(BaseDefenseManager())
    rifle = {"id": 900, "type": "e1", "x": 25, "y": 48, "hp": 100}

    ask(agent, fixture_state("midgame_allies", visible_enemies=[rifle], visible_enemy_buildings=[]))

    assert agent.ctx.threat_level == "minor"


# -- the prompt of the plan choice -----------------------------------------------------------------

PROMPT_FILE = Path(__file__).resolve().parents[2] / "src" / "ucla_agent" / "games" / "redalert" / "prompts"


def test_the_plan_choice_prompt_is_a_tracked_file_of_at_most_1200_characters_naming_every_key_and_plan():
    text = (PROMPT_FILE / "plan_choice.txt").read_text(encoding="utf-8")

    assert len(text) <= 1200
    assert [key for key in SUMMARY_KEYS if key not in text] == []
    assert all(f"{plan}:" in text for plan in ("HOLD", "RAID", "PRESS"))


# -- command shorthand -----------------------------------------------------------------------------

BASE = (("fact", 22, 48), ("powr", 19, 48), ("tent", 22, 44), ("proc", 24, 39), ("powr", 26, 50), ("weap", 22, 53))
STAGING = (32, 48)
ENEMY_START = [74, 48]


def tanks(count: int, first_id: int = 300) -> list[dict]:
    """Light tanks in a block east of the staging point."""
    return [situations.unit(first_id + number, "1tnk", 34 + number % 10, 40 + number // 10) for number in range(count)]


def guard_rifles() -> list[dict]:
    return [situations.unit(200 + number, "e1", 26, 44 + number) for number in range(5)]


def world_with(units: list[dict], enemies: list[dict] = (), buildings: list[dict] = ()) -> tuple[World, Groups]:
    """An Allied base past its opening with these units (the first five rifles are the home guard) and enemies."""
    world = World(situations.tracked_items())
    world.update(
        situations.base_state(
            "england",
            *BASE,
            units=units,
            visible_enemies=list(enemies),
            visible_enemy_buildings=list(buildings),
        )
    )
    groups = Groups()
    groups.set_guard_quota("e1", 5)
    groups.update(world)
    return world, groups


def commands(request_plan=lambda plan: True, **more) -> Commands:
    return Commands(staging=STAGING, request_plan=request_plan, **more)


def translated(text: str, world: World, groups: Groups, **more) -> tuple[list[dict], list[tuple[str, str]]]:
    """The orders of the intents a text translates into, and its rejected lines as (line, reason)."""
    intents, rejected = translate(text, world, groups, commands(**more))
    return [dict(intent.order) for intent in intents], [(refusal.line, refusal.reason) for refusal in rejected]


def enemy(enemy_id: int, enemy_type: str, x: int, y: int) -> dict:
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


def test_am_main_ebase_with_7_units_in_main_is_one_attack_move_of_those_7_to_the_enemy_start():
    world, groups = world_with([*guard_rifles(), *tanks(7)])

    intents, rejected = translate("am main ebase", world, groups, commands())

    assert rejected == [] and [intent.priority for intent in intents] == [Priority.MOVE]
    assert [dict(intent.order) for intent in intents] == [
        {"cmd": "attack_move", "units": list(range(300, 307)), "to": ENEMY_START}
    ]


def test_a_group_of_120_units_is_ordered_in_orders_of_at_most_50():
    world, groups = world_with(tanks(120))

    orders, _ = translated("AM MAIN EBASE", world, groups)

    assert [len(order["units"]) for order in orders] == [50, 50, 20]
    assert sorted(unit for order in orders for unit in order["units"]) == list(range(300, 420))


def raiders(groups: Groups) -> None:
    for unit_id in (300, 301):
        groups.assign(unit_id, "raid")


def test_atk_raid_harv_attacks_the_enemy_harvester_in_sight_and_with_none_in_sight_is_rejected_as_no_target():
    world, groups = world_with(tanks(4), enemies=[enemy(700, "harv", 60, 30)])
    raiders(groups)
    blind, blind_groups = world_with(tanks(4))
    raiders(blind_groups)

    assert translated("atk raid harv", world, groups) == ([{"cmd": "attack", "units": [300, 301], "target": 700}], [])
    assert translated("atk raid harv", blind, blind_groups) == ([], [("atk raid harv", "no_target")])


@pytest.mark.parametrize(
    "harvester_cell, priority",
    [((60, 30), Priority.MOVE), ((38, 42), Priority.CONTACT)],
    ids=["far from the raid", "within a light tank's range and 2 cells"],
)
def test_an_atk_is_a_contact_order_when_a_unit_of_the_group_is_in_contact_and_every_other_command_a_move(
    harvester_cell, priority
):
    world, groups = world_with(tanks(4), enemies=[enemy(700, "harv", *harvester_cell)])
    raiders(groups)

    attacks, _ = translate("atk raid harv", world, groups, commands())
    others, _ = translate("am raid ebase\nmv raid home\nret raid\nhold raid", world, groups, commands())

    assert [intent.priority for intent in attacks] == [priority]
    assert {intent.priority for intent in others} == {Priority.MOVE} and len(others) == 5


@pytest.mark.parametrize(
    "line, orders, reason",
    [
        ("tr e1 5", [{"cmd": "train", "item": "e1", "count": 5}], None),
        ("tr e1 11", [], "bad_count"),
        ("tr e1 0", [], "bad_count"),
        ("tr e1 x", [], "bad_count"),
        ("tr zzz 1", [], "not_available"),
        ("tr powr 1", [], "not_a_unit"),
        ("bd powr", [{"cmd": "build", "item": "powr"}], None),
        ("bd e1", [], "not_a_building"),
    ],
)
def test_production_lines_train_or_build_only_what_is_on_offer_in_its_queue(line, orders, reason):
    world, groups = world_with([])

    assert translated(line, world, groups) == (orders, [] if reason is None else [(line, reason)])


def test_mv_main_to_a_cell_off_the_map_is_rejected_as_out_of_bounds_and_one_on_it_is_moved_to():
    world, groups = world_with(tanks(2))

    assert translated("mv main 9999,9999", world, groups) == ([], [("mv main 9999,9999", "out_of_bounds")])
    assert translated("mv main 40,41", world, groups)[0] == [{"cmd": "move", "units": [300, 301], "to": [40, 41]}]


def test_of_9_lines_the_first_8_are_translated_and_the_9th_rejected():
    world, groups = world_with(tanks(2))
    text = "\n".join(["mv main 40,41"] * 8 + ["mv main 50,50"])

    orders, rejected = translated(text, world, groups)

    assert len(orders) == 8 and rejected == [("mv main 50,50", "too_many_lines")]


def test_all_never_takes_in_a_guard_unit_a_harvester_or_the_mcv():
    units = [*guard_rifles(), situations.unit(400, "harv"), situations.unit(401, "mcv"), *tanks(3)]
    world, groups = world_with(units)
    groups.assign(302, "raid")

    orders, _ = translated("hold all", world, groups)

    assert orders[0] == {"cmd": "stop", "units": [300, 301, 302]}


def test_plan_press_while_press_is_not_open_leaves_the_plan_as_it_is_and_plan_hold_takes_hold():
    agent = new_agent(tuning=SWITCHES_OFF)
    # At 280 s with an uncovered enemy harvester in sight, RAID and HOLD are open and PRESS is not.
    harvester = [enemy(700, "harv", 60, 30)]
    ask(agent, situations.base_state("england", *BASE, tick=7000, visible_enemies=harvester))
    world, groups = agent.world, agent.ctx.groups
    request = agent._plan_selector.request

    refused = translated("plan PRESS", world, groups, request_plan=request)
    ask(agent, situations.base_state("england", *BASE, tick=7005, visible_enemies=harvester))
    unchanged = agent.current_plan
    taken = translated("plan hold", world, groups, request_plan=request)
    ask(agent, situations.base_state("england", *BASE, tick=7010, visible_enemies=harvester))

    assert refused == ([], [("plan PRESS", "plan_not_open")]) and unchanged == "RAID"
    assert taken == ([], []) and agent.current_plan == "HOLD"


@pytest.mark.parametrize(
    "line, reason",
    [
        ("mv main 40,41 and on and on and on and on", "too_long"),
        ("mv  main 40,41", "malformed"),
        ("mv main", "malformed"),
        ("go main 40,41", "unknown_command"),
        ("mv army 40,41", "unknown_group"),
        ("mv raid 40,41", "empty_group"),
        ("mv main there", "unknown_place"),
        ("mv main 4,4,4", "unknown_place"),
        ("am main eharv", "no_place"),
        ("atk main tanks", "unknown_class"),
        ("plan STRIKE", "unknown_plan"),
    ],
)
def test_a_line_that_cannot_be_an_order_is_rejected_with_its_reason(line, reason):
    world, groups = world_with(tanks(2))

    assert translated(line, world, groups) == ([], [(line, reason)])


def test_a_place_the_world_does_not_know_yet_is_rejected_as_no_place():
    world, groups = world_with(tanks(1))
    world.base_center = None

    assert translated("mv main home", world, groups) == ([], [("mv main home", "no_place")])


def test_the_places_name_the_base_the_staging_point_and_the_nearest_remembered_enemy_economy():
    buildings = [enemy(710, "proc", 70, 52), enemy(711, "proc", 50, 60), enemy(712, "apwr", 76, 40)]
    world, groups = world_with(tanks(1), enemies=[enemy(700, "harv", 60, 30)], buildings=buildings)
    places = ["home", "stage", "eharv", "eproc", "epow"]

    targets = [translated(f"mv main {place}", world, groups)[0][0]["to"] for place in places]

    assert targets == [[22, 48], [32, 48], [60, 30], [50, 60], [76, 40]]


def test_ret_moves_the_group_to_the_staging_point_and_hold_stops_it_and_sets_it_to_defend():
    world, groups = world_with(tanks(2))

    assert translated("ret main\nhold main", world, groups) == (
        [
            {"cmd": "move", "units": [300, 301], "to": [32, 48]},
            {"cmd": "stop", "units": [300, 301]},
            {"cmd": "set_stance", "units": [300, 301], "stance": "defend"},
        ],
        [],
    )


@pytest.mark.parametrize(
    "line, target",
    [("atk main inf", 720), ("atk main veh", 721), ("atk main def", 730), ("atk main bld", 731)],
)
def test_each_class_names_the_nearest_enemy_in_sight_of_its_kind(line, target):
    enemies = [enemy(720, "e1", 50, 50), enemy(721, "3tnk", 52, 50)]
    buildings = [enemy(730, "ftur", 70, 50), enemy(731, "powr", 40, 60)]
    world, groups = world_with(tanks(1), enemies=enemies, buildings=buildings)

    assert translated(line, world, groups)[0] == [{"cmd": "attack", "units": [300], "target": target}]


def test_lines_a_model_wrote_are_taken_only_when_the_settings_allow_it():
    world, groups = world_with(tanks(1))

    off, refused = translate("mv main 40,41", world, groups, commands(), from_model=True)
    on, _ = translate("mv main 40,41", world, groups, commands(model_commands=True), from_model=True)

    assert (off, [refusal.reason for refusal in refused], len(on)) == ([], ["model_commands_off"], 1)


def test_rejected_lines_are_written_to_the_record_as_invalid_decisions(match_record):
    world, groups = world_with(tanks(1))

    translate("tr zzz 1\nmv main 40,41\nbd e1", world, groups, commands())

    decisions = match_record("decision")
    assert [(event["request_summary"], event["outcome"], event["reason"]) for event in decisions] == [
        ("tr zzz 1", "invalid", "not_available"),
        ("bd e1", "invalid", "not_a_building"),
    ]
    assert {event["source"] for event in decisions} == {"shorthand"}


def test_what_shorthand_orders_passes_the_script_s_own_validation_and_takes_under_5_ms_without_a_file(monkeypatch):
    world, groups = world_with([*guard_rifles(), *tanks(60)], enemies=[enemy(700, "harv", 60, 30)])
    text = "am all ebase\nmv guard home\natk main harv\nret main\nhold all\ntr e1 5\nbd powr\nplan RAID"
    opened = []
    monkeypatch.setattr(builtins, "open", lambda *arguments, **options: opened.append(arguments))

    started = time.perf_counter()
    results = [translate(text, world, groups, commands()) for _ in range(50)]
    mean_ms = (time.perf_counter() - started) * 1000 / len(results)

    intents, rejected = results[0]
    packed = pack(intents, world, Limits())
    assert rejected == [] and packed.dropped_invalid == 0 and opened == [] and mean_ms < MAX_MS


def test_a_number_written_with_other_than_ascii_digits_is_rejected_and_never_raises():
    world, groups = world_with(tanks(2))
    superscript_two, arabic_five = "²", "٥"

    assert translated("mv main --5,3", world, groups) == ([], [("mv main --5,3", "unknown_place")])
    assert translated(f"tr e1 {superscript_two}", world, groups) == ([], [(f"tr e1 {superscript_two}", "bad_count")])
    assert translated(f"mv main {arabic_five},3", world, groups) == (
        [],
        [(f"mv main {arabic_five},3", "unknown_place")],
    )


def test_a_bad_line_among_good_ones_costs_only_itself():
    world, groups = world_with(tanks(2))
    text = "\n".join(["mv main 40,41", "tr e1 ²", "mv main --5,3", "tr e1 5"])

    orders, rejected = translated(text, world, groups)

    assert orders == [{"cmd": "move", "units": [300, 301], "to": [40, 41]}, {"cmd": "train", "item": "e1", "count": 5}]
    assert [reason for _, reason in rejected] == ["bad_count", "unknown_place"]


def test_translate_never_raises_on_any_text():
    world, groups = world_with(tanks(2), enemies=[enemy(700, "harv", 60, 30)])
    # Command words, digits of several scripts, signs, and every kind of space and line break.
    alphabet = "amvtrkbdhol plancegs0123456789,-+.²٥Ⅰé \t\n\r"
    chooser = random.Random(7)

    for _ in range(2000):
        text = "".join(chooser.choice(alphabet) for _ in range(chooser.randint(0, 60)))
        intents, rejected = translate(text, world, groups, commands())
        assert isinstance(intents, list) and isinstance(rejected, list)


@pytest.mark.parametrize(
    "line, held, orders, reason",
    [
        ("tr e1 1", True, [], "infantry_held"),
        ("tr e1 1", False, [{"cmd": "train", "item": "e1", "count": 1}], None),
        ("tr 1tnk 1", True, [{"cmd": "train", "item": "1tnk", "count": 1}], None),
    ],
    ids=["infantry held", "infantry not held", "a vehicle while held"],
)
def test_a_train_line_for_infantry_is_refused_while_the_opening_holds_infantry_back(line, held, orders, reason):
    world, groups = world_with([])

    assert translated(line, world, groups, infantry_held=held) == (orders, [] if reason is None else [(line, reason)])
