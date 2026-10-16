"""The model commander: its settings, its scout and army questions, the reserved order slot and the commanded units."""

from __future__ import annotations

import dataclasses
import functools
import itertools
import json
import math
import re
import shutil
import time
from pathlib import Path

import httpx
import pytest

import situations
import whole_match
from fake_game import FakeRedAlert, at_seconds
from local_chain import build_local_chain
from situations import ask, base_state
from switches_off import APPROACH_OFF, reinforce_hold_off, safe_staging_off, screen_place_off, stepping_off
from ucla_agent.core.config import ConfigError
from ucla_agent.core.decide import TIMEOUT, USED, ChainSource, ChoiceResult, local
from ucla_agent.core.decide import journal
from ucla_agent.core.decide import systemone
from ucla_agent.core.decide.choice import SINGLE_OPTION
from ucla_agent.games.redalert import agent as agent_module
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert import (
    command_army,
    command_gate,
    command_prompt,
    command_retreat,
    command_scout,
    command_state,
    command_targets,
    command_tasks,
    command_threat,
    fight,
    model_history,
    scout_steering,
    siege,
)
from ucla_agent.games.redalert.batch import Priority
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.command_army import ARMY_IDS
from ucla_agent.games.redalert.command_prompt import Action, Pick
from ucla_agent.games.redalert.command_raid import TruckRaid
from ucla_agent.games.redalert.command_threat import ClosingWatch
from ucla_agent.games.redalert.commander import Commander, TaskPick
from ucla_agent.games.redalert.compact_prompt import prompt_chars
from ucla_agent.games.redalert.exposed_trucks import ExposedTrucks
from ucla_agent.games.redalert.failed_attack import FailedAttack
from ucla_agent.games.redalert.glossary import Glossary
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.ledger import LedgerEntry
from ucla_agent.games.redalert.scout_mission_planner import MissionPlanner
from ucla_agent.games.redalert.scout_report import EnemyIntel
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.settings import RedAlertSettings
from ucla_agent.games.redalert.sight import SightGrid
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import TUNING_FILE, CommanderTuning, RoutingTuning, ScoutingTuning, Tuning
from ucla_agent.games.redalert.world import Unit


def new_agent(*managers, tuning: Tuning | None = None, **more):
    """`situations.new_agent` with main's stepped movement, the screened place of the long-range units, the safe
    staging and the commander's continue option off, as these tests were worked out."""
    given = safe_staging_off(screen_place_off(stepping_off(tuning)))
    commander = dataclasses.replace(given.commander, continue_option=False)
    return situations.new_agent(*managers, tuning=dataclasses.replace(given, commander=commander), **more)


REPO_ROOT = Path(__file__).resolve().parents[2]
# The fake arena's two starts, ours by the base at (22, 48).
TWO_STARTS = {"rules": {"maps": {"fake-arena": {"bounds": [0, 0, 96, 96], "spawns": [[22, 48], [74, 48]]}}}}
# The scout slots and their missions on, the wide berth off: a danger reaches a unit's range and 3 cells; and the
# careful scouts off, so that a seen enemy building is no danger and the enemy main's edge stays where it is. The rush
# scout's pass and post off, which keep the first slot's scout from the commander for the first 300 s.
# The watch posts as the scout question's tests were worked out on: the main's edge at its edge cell (see
# test_watch_posts for the posts in sight).
SCOUTING = dataclasses.replace(
    Tuning(),
    scouting=ScoutingTuning(
        scout_slots=True,
        scout_missions=True,
        scout_wide_berth=False,
        scout_cautious=False,
        rush_scout_direct=False,
        watch_posts_in_sight=False,
    ),
)
YARD, POWER, BARRACKS = ("fact", 22, 48), ("powr", 19, 52), ("tent", 18, 44)
SCOUT_ID = 300
# Ore fields: far from both bases, by our yard, by the enemy's start, far to the north.
FIELDS = ((60, 80), (30, 52), (70, 40), (40, 14))


# -- settings ----------------------------------------------------------------------------------------


def test_commander_settings_the_switch_ships_on_is_off_built_in_and_the_table_holds_its_values_as_built_in():
    tuning = Tuning.from_file(REPO_ROOT).commander

    assert RedAlertSettings.from_file(REPO_ROOT).model_commander is True
    assert RedAlertSettings().model_commander is False
    assert (
        tuning
        == CommanderTuning()
        == CommanderTuning(
            6, 25, 4, 6, 4000, 0.8, 0.75, 0.5, 10, 8, 0.9, 25, 1.2, 0.5, 20, 4, 4, 2, 20, 50, 45, 30, 20, 14, 8, 0.333
        )
    )
    # The retreat override's keys are the [commander] table's, not those of [combat], whose retreat_ratio is 0.9 too.
    assert (tuning.retreat_ratio, tuning.retreat_confirm_ticks, tuning.attack_min_army_share) == (0.9, 25, 0.5)
    assert (tuning.cluster_cells, tuning.base_merge_cells, tuning.regroup_block_ratio) == (8, 16, 1.5)


@pytest.mark.parametrize(
    "old, new, named",
    [
        ("history_size = 4\n", "", "history_size in [commander]"),
        ("options_max = 6", "options_max = 7", "options_max in [commander]"),
        ("options_max = 6", "options_max = 1", "options_max in [commander]"),
        ("handover_combat_units = 6", "handover_combat_units = 6.5", "handover_combat_units in [commander]"),
        ("answer_max_age_ticks = 25", "answer_max_age_ticks = 25\nanswer_max_age = 25", "answer_max_age"),
        ("go_min_value = 4000\n", "", "go_min_value in [commander]"),
        ("commit_seconds = 8", "commit_seconds = 8.5", "commit_seconds in [commander]"),
        # [combat] has a retreat_ratio of 0.9 too: the one of [commander] is the one before retreat_confirm_ticks.
        (
            "retreat_ratio = 0.9\nretreat_confirm",
            "retreat_ratio = -0.9\nretreat_confirm",
            "retreat_ratio in [commander]",
        ),
        ("attack_min_army_share = 0.5\n", "", "attack_min_army_share in [commander]"),
        ("attack_min_army_share = 0.5", "attack_min_army_share = -0.5", "attack_min_army_share in [commander]"),
        # [combat] has a met_resume_ratio of 1.2: the line of [commander] is the one that starts with the key.
        ("\nresume_ratio = 1.2\n", "\n", "resume_ratio in [commander]"),
        ("target_reach_cells = 20\n", "", "target_reach_cells in [commander]"),
        ("target_group_cells = 4", "target_group_cells = 4.5", "target_group_cells in [commander]"),
        ("targets_max = 4", "targets_max = -1", "targets_max in [commander]"),
        ("detachments_max = 2\n", "", "detachments_max in [commander]"),
        ("task_fail_chance = 20", "task_fail_chance = 101", "task_fail_chance in [commander]"),
        ("hit_group_seconds = 30", "hit_group_seconds = 30.5", "hit_group_seconds in [commander]"),
        ("kill_defense_seen_seconds = 20\n", "", "kill_defense_seen_seconds in [commander]"),
        (
            "kill_defense_seen_seconds = 20",
            "kill_defense_seen_seconds = 20.5",
            "kill_defense_seen_seconds in [commander]",
        ),
        ("retreat_home_min_cells = 14\n", "", "retreat_home_min_cells in [commander]"),
        ("retreat_home_min_cells = 14", "retreat_home_min_cells = 14.5", "retreat_home_min_cells in [commander]"),
        ("cluster_cells = 8\n", "", "cluster_cells in [commander]"),
        ("cluster_cells = 8", "cluster_cells = 0", "cluster_cells in [commander]"),
        ("crush_discount = 0.333\n", "", "crush_discount in [commander]"),
        ("base_merge_cells = 16\n", "", "base_merge_cells in [commander]"),
        ("base_merge_cells = 16", "base_merge_cells = -1", "base_merge_cells in [commander]"),
        ("regroup_block_ratio = 1.5\n", "", "regroup_block_ratio in [commander]"),
        ("regroup_block_ratio = 1.5", "regroup_block_ratio = -1.5", "regroup_block_ratio in [commander]"),
    ],
    ids=[
        "a missing key",
        "more than 6 options",
        "fewer than 2 options",
        "a fraction",
        "an unknown key",
        "go_min_value missing",
        "commit_seconds a fraction",
        "retreat_ratio negative",
        "attack_min_army_share missing",
        "attack_min_army_share negative",
        "resume_ratio missing",
        "target_reach_cells missing",
        "target_group_cells a fraction",
        "targets_max negative",
        "detachments_max missing",
        "task_fail_chance over 100",
        "hit_group_seconds a fraction",
        "kill_defense_seen_seconds missing",
        "kill_defense_seen_seconds a fraction",
        "retreat_home_min_cells missing",
        "retreat_home_min_cells a fraction",
        "cluster_cells missing",
        "cluster_cells 0",
        "crush_discount missing",
        "base_merge_cells missing",
        "base_merge_cells negative",
        "regroup_block_ratio missing",
        "regroup_block_ratio negative",
    ],
)
def test_commander_settings_a_key_that_is_missing_unknown_or_out_of_bounds_is_named(tmp_path, old, new, named):
    target = tmp_path / TUNING_FILE
    target.parent.mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / TUNING_FILE, target)
    text = target.read_text(encoding="utf-8")
    assert text.count(old) == 1
    target.write_text(text.replace(old, new), encoding="utf-8")

    with pytest.raises(ConfigError) as raised:
        Tuning.from_file(tmp_path)

    assert named in str(raised.value)


# -- the scout's question -------------------------------------------------------------------------------


def enemy(enemy_id: int, enemy_type: str, x: int, y: int, hp: int = 100) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": hp}


@dataclasses.dataclass
class ScoutScene:
    """A world and its match context in one cycle, and the scout's question asked in it."""

    agent: situations.RedAlertAgent
    sight: SightGrid
    question: command_prompt.Question

    @property
    def lines(self) -> list[str]:
        """The user message as a source lays it out, line by line."""
        return local.choice_prompt(self.question.request).splitlines()

    @property
    def actions(self) -> tuple[Action, ...]:
        return self.question.actions

    @property
    def scout(self):
        return self.agent.world.combat_units[SCOUT_ID]

    def dangers(self) -> scout_steering.Dangers:
        return scout_steering.Dangers(self.agent.world, self.agent.ctx)


def scout_scene(
    *,
    tick: int = 6000,
    scout: tuple[int, int] = (40, 40),
    picks: tuple[Pick, ...] = (),
    confirmed=False,
    standing: str | None = None,
    taken_tick: int = 0,
    **observation,
) -> ScoutScene:
    """The scout's question for a jeep at a cell, with the four ore fields known and the sight of the scout alone;
    with `standing`, the scout's standing action is the one of that kind its question offers, taken at `taken_tick`."""
    agent = new_agent(tuning=SCOUTING)
    jeep = situations.unit(SCOUT_ID, "jeep", *scout)
    state = base_state("england", YARD, POWER, BARRACKS, tick=tick, units=[jeep], **observation)
    ask(agent, state, situations.context(TWO_STARTS))
    world, ctx = agent.world, agent.ctx
    ctx.terrain = Terrain([OrePatch(center=cell, cells=frozenset({cell})) for cell in FIELDS])
    ctx.scout_report = dataclasses.replace(EnemyIntel().observe(world, ctx.tuning.scouting), enemy_base_seen=confirmed)
    sight = SightGrid()
    sight.observe(world)
    offered = MissionPlanner().missions(world, ctx, sight)
    dangers = scout_steering.Dangers(world, ctx)
    jeep_unit = world.combat_units[SCOUT_ID]
    asked = command_scout.question(jeep_unit, dangers, sight, offered, picks, world, ctx)
    if standing is not None:
        action = next(action for action in asked.actions if action.kind == standing)
        current = command_prompt.Current(action, taken_tick, scout)
        asked = command_scout.question(jeep_unit, dangers, sight, offered, picks, world, ctx, current)
    return ScoutScene(agent, sight, asked)


FOUR_PICKS = (
    Pick("B", "enemy main edge (60,60)", 5000, 0, 0),
    Pick("A", "hold", 5500, 0, 0),
    Pick("C", "attack lane (48,48)", 5800, 0, 0),
    Pick("D", "back off", 5900, 0, 0),
)
BUSY = {
    "visible_enemies": [enemy(610, "e1", 56, 36), enemy(611, "e1", 57, 37), enemy(612, "2tnk", 60, 44)],
    "visible_enemy_buildings": [
        enemy(600, "pbox", 66, 44),
        enemy(601, "pbox", 66, 52),
        enemy(620, "fact", 74, 48),
        enemy(621, "powr", 76, 50),
        enemy(622, "weap", 72, 52),
        enemy(623, "proc", 70, 40),
    ],
}
# The full-state sections the scout's question holds, the stable ones first: no section of our groups.
SCOUT_SECTIONS = ("ore fields:", "enemy buildings:", "enemy defenses:", "enemy groups:")
LINE_ORDER = (
    *SCOUT_SECTIONS,
    "our_base=",
    "scout=",
    "danger_now:",
    "known_defense:",
    "intel:",
    "current:",
    "last_picks:",
    "t=",
    "ACTIONS:",
)
EARLY_LINES = [*SCOUT_SECTIONS, "our_base=", "scout=", "danger_now:", "intel:", "current:", "t=", "ACTIONS:"]


def state_lines(scene: ScoutScene) -> list[str]:
    """The prefixes of the state lines of a question, in order, up to `ACTIONS:`."""
    lines = scene.lines
    return [prefix for line in lines[: lines.index("ACTIONS:") + 1] for prefix in LINE_ORDER if line.startswith(prefix)]


def line_of(lines: list[str], prefix: str) -> str:
    """The one line of a question that starts with a prefix."""
    (found,) = [line for line in lines if line.startswith(prefix)]
    return found


def head_of(description: str) -> str:
    """An option's line without the consequence that ends it."""
    return description.split(f" {command_prompt.CONSEQUENCE_MARK} ")[0]


@pytest.mark.parametrize(
    "scene, expected_lines",
    [
        (lambda: scout_scene(tick=500, scout=(30, 48)), EARLY_LINES),
        (lambda: scout_scene(picks=FOUR_PICKS, visible_enemy_buildings=[enemy(600, "pbox", 66, 44)]), list(LINE_ORDER)),
        (lambda: scout_scene(scout=(50, 40), picks=FOUR_PICKS, confirmed=True, **BUSY), list(LINE_ORDER)),
    ],
    ids=["early, nothing seen", "a pillbox known and four picks", "a busy front"],
)
def test_scout_prompt_three_states_fit_2500_characters_with_every_line_in_order_and_2_to_6_options(
    scene, expected_lines
):
    built = scene()

    assert prompt_chars(built.question.request) <= 2500 == command_scout.MAX_PROMPT_CHARS
    assert built.question.request.system_text == command_scout.SYSTEM_TEXT
    assert built.question.request.system_text.endswith('Reply {"choice":"ID"}.')
    assert state_lines(built) == expected_lines
    assert 2 <= len(built.actions) <= 6
    ids = [option.id for option in built.question.request.options]
    assert ids == sorted(ids) == [command_scout.SCOUT_IDS[action.kind] for action in built.actions]


def test_scout_prompt_the_lines_say_the_clock_the_bases_the_scout_the_dangers_and_the_intel():
    refinery = enemy(624, "proc", 61, 78)
    busy = {**BUSY, "visible_enemy_buildings": [*BUSY["visible_enemy_buildings"], refinery]}
    lines = scout_scene(scout=(50, 40), picks=FOUR_PICKS, confirmed=True, **busy).lines

    bases = lines.index("our_base=(22,48) enemy_base=(74,48) confirmed")
    # The jeep's own sight, 7 cells, as the item table gives it (sight_by_type).
    assert lines[bases + 1] == "scout=jeep hp=100% at (50,40) sight=7"
    assert lines[bases + 2].startswith("danger_now: 2 e1 at (56,36) dist=7 (range 5)")
    assert lines[bases + 3].startswith("known_defense: pbox (66,44) range 6")
    intel = lines[bases + 4]
    assert intel.startswith("intel: enemy main seen 0s ago")
    assert "enemy army seen 0s ago at (58,39) value 1050" in intel and "field (60,80) refinery 0s ago" in intel
    assert lines[lines.index("ACTIONS:") - 1].startswith("t=04:00 left=")


@pytest.mark.parametrize("distance, offered", [(18, True), (19, False)], ids=["edge 10 cells off", "edge 11 cells off"])
def test_scout_prompt_hold_is_always_offered_and_back_off_exactly_while_danger_is_within_10_cells(distance, offered):
    # A pillbox reaches its range of 6 and the coverage margin of 2: its danger ends 8 cells from it.
    built = scout_scene(scout=(66 - distance, 44), visible_enemy_buildings=[enemy(600, "pbox", 66, 44)])
    kinds = [action.kind for action in built.actions]

    assert (kinds[0], built.actions[0].id) == ("hold", "A")
    assert ("back_off" in kinds) is offered
    assert not offered or (built.actions[1].id, built.actions[1].cell) == ("B", (66 - distance - 6, 44))


def test_scout_prompt_without_danger_the_options_are_hold_then_the_missions_by_score_highest_first():
    built = scout_scene(tick=500, scout=(30, 48))
    world, ctx = built.agent.world, built.agent.ctx
    missions = {mission.key: mission for mission in MissionPlanner().missions(world, ctx, built.sight)}

    # The enemy home field (G) is listed after the attack lane (F) by its letter, whatever its score.
    scores = [
        command_scout._scored(missions[action.key], built.scout, built.dangers(), built.sight, world, ctx)[0]
        for action in built.actions[1:]
        if action.kind != "enemy_home_field"
    ]

    assert [action.kind for action in built.actions][:1] == ["hold"]
    assert "back_off" not in [action.kind for action in built.actions]
    assert scores == sorted(scores, reverse=True) and len(scores) >= 2


def test_scout_prompt_options_max_cuts_the_missions_with_the_lowest_scores():
    built = scout_scene(tick=500, scout=(30, 48))
    world, ctx = built.agent.world, built.agent.ctx
    ctx.tuning = dataclasses.replace(ctx.tuning, commander=CommanderTuning(options_max=2))
    offered = MissionPlanner().missions(world, ctx, built.sight)

    two = command_scout.scout_actions(built.scout, built.dangers(), built.sight, offered, world, ctx)

    assert [action.key for action in two] == [action.key for action in built.actions[:2]]


def test_scout_prompt_a_mission_with_no_safe_way_is_offered_as_passes_danger():
    # Pillboxes on both sides of the scout's way to the enemy main's edge leave it no safe way there.
    covering = [enemy(600, "pbox", 54, 56), enemy(601, "pbox", 44, 54), enemy(602, "pbox", 60, 66)]
    built = scout_scene(scout=(40, 40), visible_enemy_buildings=covering)
    edge = next(action for action in built.actions if action.kind == "main_edge")

    assert "path passes danger" in edge.head and edge.cell == (60, 60)


def test_scout_prompt_last_picks_lists_the_history_picks_3_of_the_four_picks_newest_first_with_their_ages(monkeypatch):
    built = scout_scene(picks=FOUR_PICKS)
    world, history = built.agent.world, built.agent.ctx.model_history

    fullest = command_prompt.picks_line(
        FOUR_PICKS, command_prompt.DETAILS[0], world, with_result=False, history=history
    )

    assert fullest == "last_picks: D: back off, 4s ago; C: attack lane (48,48), 8s ago; A: hold, 20s ago"
    # Within the cap of 2,500 characters the question lists them all; over a cap the picks are cut first, the oldest
    # first.
    assert line_of(built.lines, "last_picks:") == fullest and prompt_chars(built.question.request) <= 2500
    monkeypatch.setattr(command_scout, "MAX_PROMPT_CHARS", prompt_chars(built.question.request) - 1)
    capped = scout_scene(picks=FOUR_PICKS)
    assert line_of(capped.lines, "last_picks:") == "last_picks: D: back off, 4s ago; C: attack lane (48,48), 8s ago"


def test_scout_prompt_the_answer_schema_s_enum_is_the_offered_ids_with_no_reason_in_16_tokens():
    built = scout_scene(picks=FOUR_PICKS, **BUSY)
    request = built.question.request

    body = local.build_body("fast", request, with_reason=request.asks_reason_after(True))

    schema = body["response_format"]["json_schema"]["schema"]
    assert schema["properties"] == {"choice": {"enum": [action.id for action in built.actions]}}
    assert (body["max_tokens"], request.with_reason, request.deadline_ms) == (16, False, 800)


def test_scout_prompt_holds_no_glossary_and_no_recommendation():
    built = scout_scene(picks=FOUR_PICKS, **BUSY)
    text = built.question.request.system_text + local.choice_prompt(built.question.request)

    assert not re.search(r"strong vs|weak vs|recommend|best|should", text, re.IGNORECASE)


def test_scout_prompt_choosing_a_destination_sets_the_scout_s_destination_to_its_cell_and_steers_it_there():
    built = scout_scene(tick=500, scout=(30, 48))
    world, ctx = built.agent.world, built.agent.ctx
    course = scout_steering.Course()
    chosen = next(action for action in built.actions if action.cell is not None)

    command_scout.follow(course, chosen, built.scout, built.dangers(), world, ctx)
    move = scout_steering.steer_to_mission(course, built.scout, built.dangers(), world, ctx)

    assert (course.destination, course.source) == (chosen.cell, "commander")
    assert course.legs[-1] == chosen.cell and move.cell == course.legs[0]
    legs, chosen_tick = list(course.legs), course.chosen_tick
    command_scout.follow(course, chosen, built.scout, built.dangers(), world, ctx)
    assert (course.legs, course.chosen_tick) == (legs, chosen_tick)


def test_scout_prompt_a_held_scout_is_left_alone_for_3_s_even_idle_but_flees_a_danger_near_it():
    quiet = scout_scene(tick=500, scout=(30, 48))
    hold = quiet.actions[0]
    tank = scout_scene(tick=500, scout=(30, 48), visible_enemies=[enemy(612, "2tnk", 36, 48)])

    alone = scout_steering.steer_held(
        scout_steering.Course(), quiet.scout, quiet.dangers(), quiet.agent.world, quiet.agent.ctx
    )
    fled = scout_steering.steer_held(
        scout_steering.Course(), tank.scout, tank.dangers(), tank.agent.world, tank.agent.ctx
    )

    assert alone is None and fled is not None and fled.urgency == scout_steering.FLEE_URGENCY
    assert [command_scout.is_held(hold, taken, quiet.agent.world) for taken in (426, 425, 500)] == [True, False, True]
    assert command_scout.is_held(None, 500, quiet.agent.world) is False


def test_scout_prompt_a_question_no_level_fits_is_an_error():
    built = scout_scene(picks=FOUR_PICKS, **BUSY)

    with pytest.raises(ValueError, match="over 400"):
        command_prompt.fitted(command_scout.SYSTEM_TEXT, lambda detail: ["t=00:00"], built.actions, 0, 400)


# -- the army's question ------------------------------------------------------------------------------

NAMES = command_prompt.plain_names(Glossary.from_file(REPO_ROOT))
# march_when_clear off: an advance is the plain attack-move on the objective. And vehicle_repair off: main's tanks
# losing health stay main's, so that what main is worth falls with them.
ARMY = dataclasses.replace(
    Tuning(),
    siege=dataclasses.replace(Tuning().siege, march_when_clear=False),
    combat=dataclasses.replace(Tuning().combat, vehicle_repair=False),
)
# The same with forward staging off, for the tests of a forward action carried out straight on.
UNSTAGED_ARMY = dataclasses.replace(ARMY, combat=dataclasses.replace(ARMY.combat, forward_staging=False))
STAGING = (32, 48)


class FixedPlan:
    """A plan selector that always sets one plan."""

    def __init__(self, plan: str) -> None:
        self.plan = plan

    def select(self, world, ctx, choice) -> str:
        return self.plan


def tanks(count: int, x: int = 44, y: int = 46, first: int = 400, unit_type: str = "3tnk", **fields) -> list[dict]:
    """`count` tanks of a type in a column of three from a cell, numbered from `first`."""
    return [situations.unit(first + n, unit_type, x + n % 3, y + n // 3, **fields) for n in range(count)]


@dataclasses.dataclass
class ArmyScene:
    """A Soviet base with main in the field, a cycle played, and main's question asked in it."""

    agent: situations.RedAlertAgent
    combat: CombatManager
    state: dict
    question: command_prompt.Question

    @property
    def lines(self) -> list[str]:
        return local.choice_prompt(self.question.request).splitlines()

    def action(self, kind: str) -> Action:
        return next(action for action in self.question.actions if action.kind == kind)

    def kinds(self) -> list[str]:
        return [action.kind for action in self.question.actions]


def army_scene(
    units: list[dict],
    *,
    plan: str = "PRESS",
    picks: tuple[Pick, ...] = (),
    peak: command_army.EnemyPeak | None = None,
    tick: int = 12500,
    threat_active: bool = False,
    exposed_trucks: tuple[ExposedTrucks, ...] = (),
    tuning: Tuning = ARMY,
    game_config: dict = TWO_STARTS,
    standing: str | None = None,
    taken_tick: int = 0,
    **observation,
) -> ArmyScene:
    """main of these units in a Soviet base under a plan, one cycle played with the combat manager alone, and the
    question asked with base defense's threat in force or not and the raid opportunities given; with `standing`,
    main's standing action is the one of that kind its question offers, taken at `taken_tick`."""
    combat = CombatManager()
    agent = new_agent(combat, tuning=tuning, plan_selector=FixedPlan(plan))
    state = base_state("russia", YARD, POWER, ("barr", 18, 44), tick=tick, units=units, **observation)
    ask(agent, state, situations.context(game_config))
    world, ctx = agent.world, agent.ctx
    ctx.threat_active, ctx.exposed_trucks = threat_active, exposed_trucks
    main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
    found = command_army.brief(main, NAMES, peak or command_army.EnemyPeak(), world, ctx)
    actions = command_army.army_actions(found, True, NAMES, world, ctx)
    current = None
    if standing is not None:
        action = next(action for action in actions if action.kind == standing)
        current = command_prompt.Current(action, taken_tick, found.view.center)
    asked = command_army.asked(found, actions, picks, NAMES, world, ctx, current)
    return ArmyScene(agent, combat, state, asked)


ARMY_PICKS = (
    Pick("D", "advance to war factory", 12000, 1000, 100),
    Pick("E", "pull back", 12200, 1000, 300),
    Pick("A", "attack (55,47)", 12300, 1200, 300),
    Pick("C", "flank to war factory", 12400, 1500, 400),
)
FRONT = {
    "visible_enemies": [enemy(610 + n, "2tnk", 55, 46 + n) for n in range(5)]
    + [enemy(630 + n, "e1", 56, 44 + n) for n in range(3)],
    "visible_enemy_buildings": [
        enemy(600, "pbox", 66, 44),
        enemy(601, "gun", 66, 52),
        enemy(602, "pbox", 63, 40),
        enemy(640, "fact", 74, 48),
        enemy(641, "weap", 72, 52),
    ],
    "military": {"kills_cost": 2000, "deaths_cost": 700, "assets_value": 20000, "army_value": 12000},
}
MIXED_ARMY = [
    *tanks(8, hp=80),
    *[situations.unit(420 + n, "e3", 42, 44 + n) for n in range(4)],
    *[situations.unit(430 + n, "v2rl", 40, 48 + n) for n in range(2)],
    *tanks(3, 26, 50, first=440),
]
# The military state's sections of main's question, the stable ones first.
ARMY_SECTIONS = ("our bases:", "enemy bases:", "enemy forces:", "our forces:")
ARMY_LINES = (
    *ARMY_SECTIONS,
    "enemy_total_est:",
    "fight_here:",
    "targets:",
    "threat:",
    "objective:",
    "current:",
    "last_picks:",
    "score:",
    "ACTIONS:",
)


def army_lines(scene: ArmyScene) -> list[str]:
    lines = scene.lines
    return [prefix for line in lines[: lines.index("ACTIONS:") + 1] for prefix in ARMY_LINES if line.startswith(prefix)]


@pytest.mark.parametrize(
    "scene, with_picks, option_count",
    [
        (lambda: army_scene(tanks(6, 30, 47), tick=3000), False, 3),
        (lambda: army_scene(tanks(8), picks=ARMY_PICKS, visible_enemies=FRONT["visible_enemies"]), True, 3),
        (
            lambda: army_scene(
                MIXED_ARMY, picks=ARMY_PICKS, peak=command_army.EnemyPeak(2950, 12450), threat_active=True, **FRONT
            ),
            True,
            6,
        ),
    ],
    ids=["at home, nothing seen", "an enemy group in the field", "a busy front with V2s and defenses, base attacked"],
)
def test_army_prompt_three_states_fit_6000_characters_with_every_line_in_order_and_2_to_6_options(
    scene, with_picks, option_count
):
    built = scene()

    assert prompt_chars(built.question.request) <= 6000 == command_army.MAX_PROMPT_CHARS
    assert built.question.request.system_text == command_army.SYSTEM_TEXT
    assert army_lines(built) == [prefix for prefix in ARMY_LINES if with_picks or prefix != "last_picks:"]
    assert len(built.question.actions) == option_count
    assert all(len(head_of(option.description).split()) <= 15 for option in built.question.request.options)


def test_army_prompt_the_lines_say_the_army_its_reinforcements_the_enemy_the_estimate_and_the_objective():
    every = army_scene(MIXED_ARMY, picks=ARMY_PICKS, peak=command_army.EnemyPeak(2950, 12450), **FRONT).lines
    lines = every[every.index("enemy_total_est: 2950 (seen peak 2s ago), unplaced 0") :]

    assert (
        every[every.index("ACTIONS:") - 1] == "score: lead +1300 (killed 2000 lost 700) t=08:20 left=20:00 plan=PRESS"
    )
    # By total value: 8 heavy tanks at 80%, then 2 V2s (1,800) before 4 rocket soldiers (1,200).
    assert line_of(every, "main: ").startswith(
        "main: 14 units (8 heavy tank, 2 v2 rocket launcher, 4 rocket soldier) strength 10360, hp 89%, "
    )
    assert line_of(every, "reinforcing: ") == (
        "reinforcing: 3 units (3 heavy tank) strength 3450, hp 100%, 16 cells W (27,50), eta 10s"
    )
    assert line_of(every, "F1, ").startswith("F1, 12 cells E (55,47): 8 units strength 4550 (5 medium tank, 3 rifle ")
    assert lines[1].startswith("fight_here: we are ")
    assert lines[2:4] == ["targets:", "T1: 5 medium tank, 3 rifle infantry at (55,47), 12 cells, value 4,550, win 85%"]
    assert lines[4] == "threat: none"
    assert lines[5].startswith("objective: construction yard (74,48), ")
    assert lines[5].endswith("direct path in pillbox range")


def test_army_prompt_before_any_enemy_is_seen_the_objective_is_the_enemy_start_and_the_estimate_assumed():
    lines = army_scene(tanks(6, 30, 47), tick=3000).lines

    assert "objective: enemy start (74,48), 43 cells; direct path clear" in lines
    assert "fight_here: no enemy" in lines and "enemy_total_est: 6900 (assumed), unplaced 6900" in lines


def test_strike_near_the_target_main_strikes_as_a_whole_is_the_objective_and_the_advance_s_destination():
    # The MCV stands far from the base: only the strike target published makes it the objective, not the yard.
    shown = {"visible_enemies": [enemy(701, "mcv", 44, 30)], "visible_enemy_buildings": [enemy(700, "fact", 74, 48)]}
    scene = army_scene(tanks(6, 30, 47), tick=3000, **shown)
    world, ctx = scene.agent.world, scene.agent.ctx
    main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
    before = command_army.brief(main, NAMES, command_army.EnemyPeak(), world, ctx).goal.cell
    ctx.strike_target = world.enemy_memory[701]

    found = command_army.brief(main, NAMES, command_army.EnemyPeak(), world, ctx)
    actions = command_army.army_actions(found, True, NAMES, world, ctx)

    assert (before, found.goal.cell) == ((74, 48), (44, 30))
    assert next(action for action in actions if action.kind == "advance").cell == (44, 30)


@pytest.mark.parametrize("own, offered", [(2, False), (3, True)], ids=["0.4", "0.6"])
def test_army_prompt_retreat_home_and_hold_are_offered_in_a_fight_under_1_0_and_an_attack_at_0_4_is_not(own, offered):
    # Light tanks against light tanks in sight: the estimate is the count of ours over theirs.
    enemies = [enemy(610 + n, "1tnk", 52, 46 + n) for n in range(5)]
    built = army_scene(tanks(own, 40, 47, unit_type="1tnk"), visible_enemies=enemies)

    assert built.kinds()[:3] == ["pull_back", "retreat_home", "hold"]
    assert ("attack" in built.kinds()) is offered
    assert not offered or "fight 0.6x" in built.action("attack").head


def test_army_prompt_an_attack_at_exactly_0_5_is_offered():
    enemies = [enemy(610 + n, "1tnk", 52, 46 + n) for n in range(4)]

    assert "attack" in army_scene(tanks(2, 40, 47, unit_type="1tnk"), visible_enemies=enemies).kinds()


def test_army_prompt_flank_while_the_straight_way_is_in_a_defense_s_range_and_advance_while_it_is_clear():
    covered = army_scene(tanks(6, 40, 47), visible_enemy_buildings=FRONT["visible_enemy_buildings"])
    clear = army_scene(
        tanks(6, 40, 47),
        visible_enemy_buildings=[enemy(641, "weap", 72, 52)],
        visible_enemies=[enemy(610 + n, "1tnk", 57, 46 + n) for n in range(2)],
    )

    flank = covered.action("flank")
    assert covered.kinds() == ["gather", "flank"] and flank.cell == (74, 48) and flank.via is not None
    assert clear.kinds() == ["gather", "attack", "advance", "hit_group"] and clear.action("advance").cell == (72, 52)


def test_army_prompt_the_objective_is_the_finish_target_s_fresh_production_first_whatever_the_plan():
    buildings = [enemy(640, "powr", 60, 48), enemy(641, "weap", 72, 52)]
    for plan in ("HOLD", "RAID", "PRESS", "FINISH"):
        built = army_scene(tanks(6, 40, 47), plan=plan, visible_enemy_buildings=buildings)
        assert built.action("advance").cell == (72, 52)


def test_army_prompt_last_picks_give_the_value_killed_and_lost_since_each_pick_newest_first():
    built = army_scene(tanks(8), picks=ARMY_PICKS, **FRONT)
    (picked,) = [line for line in built.lines if line.startswith("last_picks:")]

    assert picked.startswith(
        "last_picks: C: flank to war factory, 4s ago -> killed 500 lost 300; A: attack (55,47), 8s ago -> killed 800 "
        "lost 400"
    )


def test_army_prompt_last_picks_give_the_army_ratio_then_and_now_after_the_value_killed_and_lost():
    picks = (Pick("A", "attack (55,47)", 12300, 1200, 300, 1.4), Pick("C", "flank to war factory", 12400, 1500, 400))
    scene = army_scene(tanks(8), picks=picks, **FRONT)
    world, ctx = scene.agent.world, scene.agent.ctx
    ctx.model_history.ratio = 1.1
    main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]

    request = command_army.question(main, picks, NAMES, command_army.EnemyPeak(), world, ctx).request

    (picked,) = [line for line in local.choice_prompt(request).splitlines() if line.startswith("last_picks:")]
    assert picked == (
        "last_picks: C: flank to war factory, 4s ago -> killed 500 lost 300; "
        "A: attack (55,47), 8s ago -> killed 800 lost 400 ratio 1.4 -> 1.1"
    )


def test_army_prompt_the_answer_schema_is_the_offered_ids_without_a_reason_and_no_glossary_or_recommendation():
    built = army_scene(MIXED_ARMY, picks=ARMY_PICKS, **FRONT)
    request = built.question.request
    body = local.build_body("fast", request, with_reason=request.asks_reason_after(True))
    text = request.system_text + local.choice_prompt(request)

    assert body["response_format"]["json_schema"]["schema"]["properties"] == {"choice": {"enum": list("DEFHI")}}
    assert (body["max_tokens"], request.deadline_ms) == (16, 800)
    assert not re.search(r"strong vs|weak vs|recommend|best|should", text, re.IGNORECASE)


def test_army_prompt_the_enemy_peak_keeps_the_largest_value_and_when_it_was_seen():
    peak = command_army.EnemyPeak()
    for value, tick in ((500, 100), (2000, 200), (1500, 300)):
        peak.note(value, tick)

    assert (peak.value, peak.tick) == (2000, 200)


# -- the army question's gaps ------------------------------------------------------------------------------------------

# A scene like the snapshot of game 20261013T173137Z at 08:45 (docs/samples): main of 14 rifles, 4 rocket soldiers,
# 2 medium tanks, a light tank and an artillery piece about (46,68); the home guard's 5 rifles and 2 idle medium tanks
# of main's group by the staging point (32,48), which the question is not about; a heavy tank, a flak truck and a
# rocket soldier at (52,68) and a SAM site at (51,75); a flame tower and a tesla coil to the east, covering a service
# depot; and an ore refinery at (70,64) that neither covers. The snapshot's eight ore fields, our refineries on two of
# them with 4 and 2 harvesters, a mechanic behind main and two scouts, one at staging and one far to the north-east.
SNAPSHOT_GUARD = [situations.unit(490 + n, "e1", 29 + n % 2, 45) for n in range(5)]
SNAPSHOT_MAIN = [
    *[situations.unit(500 + n, "e1", 44 + n % 5, 66 + n // 5) for n in range(14)],
    *[situations.unit(520 + n, "e3", 46 + n, 70) for n in range(4)],
    situations.unit(530, "2tnk", 48, 67),
    situations.unit(531, "2tnk", 47, 68),
    situations.unit(532, "1tnk", 49, 68),
    situations.unit(533, "arty", 42, 65),
]
STAGED_TANKS = [situations.unit(540, "2tnk", 32, 48), situations.unit(541, "2tnk", 33, 49)]
SNAPSHOT_ENEMIES = [enemy(700, "3tnk", 52, 68), enemy(701, "ftrk", 52, 69, hp=95), enemy(702, "e3", 53, 68, hp=67)]
SNAPSHOT_REFINERY = enemy(713, "proc", 70, 64)
SNAPSHOT_DEFENSES = [enemy(710, "sam", 51, 75, hp=55), enemy(711, "ftur", 69, 53), enemy(712, "tsla", 68, 46)]
SNAPSHOT_BUILDINGS = [*SNAPSHOT_DEFENSES, SNAPSHOT_REFINERY, enemy(714, "fix", 68, 55)]
SNAPSHOT_PICKS = (
    Pick("D", "gather", 11500, 6000, 7000, 0.6),
    Pick("H", "attack (40,50)", 12000, 8000, 7200, 0.9),
    Pick("I", "advance to sam site", 12336, 8900, 7600, 1.0),
)
SNAPSHOT_TICK = 13136


def ore_field(center: tuple[int, int], size: int) -> OrePatch:
    """An ore field of `size` cells in rows of a square about its center."""
    width = math.isqrt(size - 1) + 1
    left, top = center[0] - width // 2, center[1] - width // 2
    return OrePatch(center=center, cells=frozenset((left + n % width, top + n // width) for n in range(size)))


SNAPSHOT_FIELDS = tuple(
    ore_field(center, size)
    for center, size in (
        ((33, 41), 100),
        ((13, 76), 47),
        ((6, 53), 33),
        ((44, 84), 37),
        ((64, 57), 100),
        ((91, 44), 33),
        ((84, 21), 47),
        ((53, 13), 37),
    )
)
SNAPSHOT_REFINERIES = (("proc", 28, 42), ("proc", 16, 79))
HARVESTING = {"idle": False, "act": "FindAndDeliverResources"}
SNAPSHOT_OTHERS = [
    *[{**situations.unit(550 + n, "harv", 31 + n, 40), **HARVESTING} for n in range(4)],
    *[{**situations.unit(554 + n, "harv", 13 + n, 77), **HARVESTING} for n in range(2)],
    situations.unit(556, "mech", 36, 61, idle=False),
]
SNAPSHOT_SCOUTS = [situations.unit(560, "jeep", 34, 45), situations.unit(561, "jeep", 72, 18, idle=False)]
# The last orders of the scout driving home and of the mechanic following main.
SNAPSHOT_ORDERS = {
    561: {"cmd": "move", "units": [561], "to": [32, 48]},
    556: {"cmd": "move", "units": [556], "to": [37, 63]},
}
# The length of the fake match, in seconds: 20 minutes.
MATCH_SECONDS = 1200


def fake_match_state(tick: int) -> dict:
    """The state of a fake match of MATCH_SECONDS at a tick, its clock as the game's own gives it."""
    game = FakeRedAlert()
    game.tick = tick
    return game.state()


def snapshot_scene(
    *,
    staged: list[dict] = STAGED_TANKS,
    enemies: list[dict] = SNAPSHOT_ENEMIES,
    buildings: list[dict] = SNAPSHOT_BUILDINGS,
    picks: tuple[Pick, ...] = SNAPSHOT_PICKS,
    peak: int = 6650,
    is_gate_open: bool = True,
    threat_active: bool = False,
    in_question: bool = False,
    army: frozenset[int] = frozenset(),
    tuning: Tuning = ARMY,
    fields: tuple[OrePatch, ...] = SNAPSHOT_FIELDS,
    orders: dict[int, dict] = SNAPSHOT_ORDERS,
    closing: ClosingWatch | None = None,
    repairing: tuple[dict, ...] = (),
    labels: command_state.ClusterLabels | None = None,
    others: list[dict] = SNAPSHOT_OTHERS,
    own_buildings: tuple[tuple[str, int, int], ...] = (),
) -> ArmyScene:
    """The snapshot's scene under HOLD, main's question asked with the gate open or closed and base defense's threat in
    force or not; the units `staged` main's group's but, unless `in_question`, not of the question; `army` the units
    out on main's orders; main's standing action the advance, when offered, taken 32 s before. The terrain knows the
    ore fields given; the two jeeps are scouts; `orders` are the last orders sent to the units they name, and `closing`
    the closing watch of the commander. The units `repairing` are main's vehicles in repair mode, and `labels` the
    commander's cluster numbers. The clock is a fake match's of 1,200 s at the snapshot's tick. `others` are our
    harvesters and mechanic, and `own_buildings` stand in our base besides the snapshot's."""
    combat = CombatManager()
    agent = new_agent(combat, tuning=tuning, plan_selector=FixedPlan("HOLD"))
    military = {"kills_cost": 9050, "deaths_cost": 7600, "assets_value": 36150, "army_value": 16150}
    shown = {"visible_enemies": enemies, "visible_enemy_buildings": buildings, "military": military}
    units = [*SNAPSHOT_GUARD, *SNAPSHOT_MAIN, *staged, *repairing, *others, *SNAPSHOT_SCOUTS]
    base = (YARD, POWER, ("barr", 18, 44), *SNAPSHOT_REFINERIES, *own_buildings)
    state = base_state("russia", *base, tick=SNAPSHOT_TICK, units=units, **shown)
    state["time"] = fake_match_state(SNAPSHOT_TICK)["time"]
    ask(agent, state, situations.context(TWO_STARTS))
    world, ctx = agent.world, agent.ctx
    ctx.model_history.ratio, ctx.threat_active, ctx.army = 1.2, threat_active, army
    ctx.repairing = frozenset(unit["id"] for unit in repairing)
    ctx.terrain = Terrain(fields)
    for scout in SNAPSHOT_SCOUTS:
        ctx.groups.assign(scout["id"], "scout")
    sent = {
        unit_id: LedgerEntry(0, 0, SNAPSHOT_TICK, SNAPSHOT_TICK, order, Priority.MOVE)
        for unit_id, order in orders.items()
    }
    ctx.unit_orders = {**ctx.unit_orders, **sent}
    left_out = (set() if in_question else {unit["id"] for unit in staged}) | ctx.repairing
    main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN) if unit_id not in left_out]
    estimate = command_army.EnemyPeak(peak, SNAPSHOT_TICK - 103 * 25)
    found = command_army.brief(main, NAMES, estimate, world, ctx, closing=closing, labels=labels)
    actions = command_army.army_actions(found, is_gate_open, NAMES, world, ctx)
    advance = next((action for action in actions if action.kind == "advance"), None)
    current = None if advance is None else command_prompt.Current(advance, SNAPSHOT_TICK - 32 * 25, found.view.center)
    return ArmyScene(agent, combat, state, command_army.asked(found, actions, picks, NAMES, world, ctx, current))


def letters(scene: ArmyScene) -> str:
    return "".join(action.id for action in scene.question.actions)


def lettered_action(scene: ArmyScene, letter: str) -> Action:
    return next(action for action in scene.question.actions if action.id == letter)


def test_army_gaps_the_snapshot_question_names_the_army_by_value_the_reinforcements_the_sam_and_the_economy_target():
    scene = snapshot_scene()
    lines = scene.lines

    assert prompt_chars(scene.question.request) <= 6000
    assert letters(scene) == "DEHILM"
    assert line_of(lines, "main: ").startswith(
        "main: 22 units (2 medium tank, 14 rifle infantry, 4 rocket soldier, 1 artillery, 1 light tank) strength 5850, "
    )
    assert line_of(lines, "staging: ").startswith("staging: 2 units (2 medium tank) strength 1700, hp 100%, ")
    assert "; ground defense 0; air-only 1 sam site; " in line_of(lines, "E1 outpost, 9 cells SE (51,75): ")
    assert "E: siege sam site (51,75), 9 cells (air only) -> exposed target" in lines
    attack = "H: attack 1 heavy tank, 1 flak truck, 1 rocket soldier at (52,68), 6 cells, fight 3.0x -> exposed target"
    assert attack in lines
    assert "L: hit 1 heavy tank, 1 flak truck, 1 rocket soldier at (52,68) with 4 rocket soldier, 2 medium tank, " in (
        "\n".join(lines)
    )
    assert "M: attack economy: ore refinery at (70,64), 24 cells, value 1400, unguarded -> exposed target" in lines


def test_army_gaps_a_question_too_long_with_the_framing_drops_it_and_keeps_the_fullest_state_with_one_pick(monkeypatch):
    # Under a cap just below the snapshot's question at its least detail with the framing, it is written with the short
    # system text at the fullest level that lists one pick and fits.
    with monkeypatch.context() as least_only:
        least_only.setattr(command_army, "ARMY_DETAILS", command_prompt.ARMY_DETAILS[-1:])
        least = prompt_chars(snapshot_scene().question.request)
    monkeypatch.setattr(command_army, "MAX_PROMPT_CHARS", least - 1)

    scene = snapshot_scene()
    request = scene.question.request

    assert request.system_text == command_army.SHORT_SYSTEM_TEXT and prompt_chars(request) < least
    assert re.fullmatch(r"last_picks: I: advance to sam site, 32s ago -> [^;]+", line_of(scene.lines, "last_picks:"))
    assert command_prompt.short_text_details(command_prompt.DETAILS) == command_prompt.DETAILS[2:]
    assert command_prompt.short_text_details(command_prompt.ARMY_DETAILS) == command_prompt.ARMY_DETAILS[4:]
    assert all(detail.picks == 1 for detail in command_prompt.ARMY_DETAILS[4:])


STAGING_THIRD = "staging: 3 units (3 medium tank) strength 2550"


@pytest.mark.parametrize(
    "scene, line",
    [
        (lambda: snapshot_scene(staged=[*STAGED_TANKS, situations.unit(542, "2tnk", 38, 48)]), STAGING_THIRD),
        (
            lambda: snapshot_scene(staged=[*STAGED_TANKS, situations.unit(542, "2tnk", 39, 48)]),
            "staging: 2 units (2 medium tank) strength 1700",
        ),
        (lambda: snapshot_scene(army=frozenset({540})), "staging: 1 unit (1 medium tank) strength 850"),
        (lambda: snapshot_scene(staged=[]), None),
    ],
    ids=["a third at 6 cells", "a third at 7 cells", "one out on main's orders", "none"],
)
def test_army_gaps_reinforcements_count_main_s_units_within_6_cells_of_staging_the_question_is_not_about(scene, line):
    # The home guard's 5 rifles stand within 6 cells of the staging point too, and are no reinforcements.
    staging = [found.split(", hp ")[0] for found in scene().lines if found.startswith("staging: ")]

    assert staging == ([] if line is None else [line])


def test_army_gaps_main_s_own_units_at_staging_are_stragglers_not_reinforcements():
    lines = snapshot_scene(in_question=True).lines

    assert line_of(lines, "main: ").startswith("main: 22 units ")
    assert re.fullmatch(
        r"reinforcing: 2 units \(2 medium tank\) strength 1700, hp 100%, \d+ cells NW \(\d+,\d+\), eta \d+s",
        line_of(lines, "reinforcing: "),
    )
    assert not any(found.startswith("staging: ") for found in lines)


@pytest.mark.parametrize("defense, name", [("sam", "sam site"), ("agun", "aa gun")])
def test_army_gaps_an_air_only_defense_is_marked_in_place_of_its_range_and_a_ground_one_keeps_its_range(defense, name):
    buildings = [enemy(710, defense, 51, 75), enemy(720, "pbox", 40, 75), SNAPSHOT_REFINERY]

    lines = snapshot_scene(buildings=buildings).lines

    (air_only,) = [found for found in lines if " (51,75): " in found]
    (pillbox,) = [found for found in lines if " (40,75): " in found]
    assert f"; ground defense 0; air-only 1 {name}; " in air_only
    assert "; ground defense 1200 (1 pillbox); air-only none; " in pillbox


def test_army_gaps_an_attack_names_every_type_and_at_the_last_level_the_three_worth_most_and_how_many_more():
    five = [
        enemy(700, "3tnk", 52, 68),
        enemy(701, "ftrk", 52, 69),
        enemy(702, "e3", 53, 68),
        enemy(703, "e1", 53, 69),
        enemy(704, "1tnk", 51, 68),
    ]
    attack = snapshot_scene(enemies=five).action("attack")

    names = "1 heavy tank, 1 light tank, 1 flak truck"
    assert attack.line(command_prompt.DETAILS[0]).startswith(f"attack {names}, 1 rocket soldier, 1 rifle infantry at ")
    assert attack.line(command_prompt.DETAILS[-2]) == attack.line(command_prompt.DETAILS[0])
    assert attack.line(command_prompt.DETAILS[-1]).startswith(f"attack {names}, +2 more at (52,68), ")


def test_army_gaps_a_busy_snapshot_question_with_four_picks_fits_6000_characters_and_offers_gather():
    many = [
        *SNAPSHOT_ENEMIES,
        enemy(703, "e1", 53, 69),
        enemy(704, "1tnk", 51, 68),
        enemy(705, "jeep", 54, 67),
        enemy(706, "apc", 54, 69),
        enemy(707, "2tnk", 58, 66),
    ]
    buildings = [*SNAPSHOT_BUILDINGS, enemy(720, "pbox", 40, 75), enemy(721, "gun", 58, 76)]
    picks = (Pick("A", "pull back", 11000, 5000, 6000, 0.5), *SNAPSHOT_PICKS)

    scene = snapshot_scene(enemies=many, buildings=buildings, picks=picks, threat_active=True)

    assert prompt_chars(scene.question.request) <= 6000
    assert "gather" in scene.kinds() and len(scene.question.actions) == 6


@pytest.mark.parametrize(
    "buildings",
    [
        [*SNAPSHOT_DEFENSES, enemy(713, "proc", 69, 57)],
        [*SNAPSHOT_DEFENSES, enemy(713, "proc", 69, 57), enemy(715, "harv", 66, 58)],
    ],
    ids=["a refinery the flame tower covers", "and a harvester by it"],
)
def test_army_gaps_no_economy_option_on_a_target_a_ground_defense_covers(buildings):
    assert "M" not in letters(snapshot_scene(buildings=buildings))


def test_army_gaps_no_economy_option_while_the_forward_gate_is_closed():
    assert "M" not in letters(snapshot_scene(is_gate_open=False))


@pytest.mark.parametrize("refinery, offered", [((70, 64), False), ((30, 56), True)], ids=["far", "near home"])
def test_army_gaps_while_main_is_behind_the_economy_option_is_offered_only_near_home(refinery, offered):
    buildings = [*SNAPSHOT_DEFENSES, enemy(713, "proc", *refinery)]

    assert ("M" in letters(snapshot_scene(buildings=buildings, peak=30000))) is offered


@pytest.mark.parametrize("guards, offered", [(1, True), (12, False)], ids=["a light tank", "12 heavy tanks"])
def test_army_gaps_the_economy_option_needs_a_ratio_of_0_5_against_the_enemy_by_it(guards, offered):
    unit_type = "1tnk" if guards == 1 else "3tnk"
    by_it = [enemy(800 + n, unit_type, 72 + n % 3, 66 + n // 3) for n in range(guards)]

    scene = snapshot_scene(enemies=[*SNAPSHOT_ENEMIES, *by_it])

    assert ("M" in letters(scene)) is offered
    line = r"attack economy: ore refinery at \(70,64\), 24 cells, value 1400, fight \d+\.\dx"
    assert not offered or re.fullmatch(line, lettered_action(scene, "M").head)


def test_army_gaps_the_economy_target_is_the_one_worth_most_per_cell_harvesters_before_a_farther_refinery():
    # 2,200 at 12 cells against the refinery's 1,400 at 24; weighed with the flak truck and rocket soldier by them.
    harvesters = [enemy(730, "harv", 56, 74), enemy(731, "harv", 56, 76)]

    economy = lettered_action(snapshot_scene(enemies=[*SNAPSHOT_ENEMIES, *harvesters]), "M")

    assert re.fullmatch(r"attack economy: 2 harvester at \(56,75\), 12 cells, value 2200, fight \d+\.\dx", economy.head)
    assert (economy.kind, economy.offer_kind, economy.cell) == ("attack", "attack_economy", (56, 75))


def test_army_gaps_with_every_place_taken_the_economy_option_replaces_the_last_active_action_kept():
    without = snapshot_scene(buildings=SNAPSHOT_DEFENSES, threat_active=True)
    with_it = snapshot_scene(threat_active=True)

    assert (letters(without), letters(with_it)) == ("ABCDIL", "ABCDIM")


def test_army_gaps_the_economy_option_is_carried_out_as_the_attack_an_attack_move_on_its_target():
    scene = snapshot_scene(enemies=[], tuning=UNSTAGED_ARMY)
    economy = lettered_action(scene, "M")
    scene.combat.command(economy)
    state = {**scene.state, "state_version": SNAPSHOT_TICK + 5}
    state["observation"] = {**scene.state["observation"], "tick": SNAPSHOT_TICK + 5}

    ask(scene.agent, state)

    order = scene.combat.commanded_order
    assert economy.kind == command_army.ATTACK and (order["cmd"], order["to"]) == ("attack_move", [70, 64])


# -- the full-state question ------------------------------------------------------------------------------------------


def section(lines: list[str], heading: str) -> list[str]:
    """The item lines of a section of a question, up to the next heading or the question's own lines."""
    start = lines.index(heading) + 1
    own = ("our_base=", "enemy_total_est:")
    return list(itertools.takewhile(lambda line: not line.endswith(":") and not line.startswith(own), lines[start:]))


def test_full_state_the_snapshot_question_has_the_full_system_text_and_every_section_within_6000_characters():
    scene = snapshot_scene()
    lines = scene.lines
    request = scene.question.request

    assert request.system_text == command_army.SYSTEM_TEXT and model_history.DOCTRINE in request.system_text
    assert prompt_chars(request) <= 6000
    assert [prefix for prefix in ARMY_LINES if any(line.startswith(prefix) for line in lines)] == list(ARMY_LINES)
    assert section(lines, "our bases:") == SNAPSHOT_OUR_BASES
    assert section(lines, "enemy bases:") == SNAPSHOT_ENEMY_BASES
    assert section(lines, "enemy forces:") == SNAPSHOT_ENEMY_FORCES
    assert section(lines, "our forces:") == SNAPSHOT_OUR_FORCES
    # The estimate of 6,650 less the 1,921 of the one force listed.
    assert line_of(lines, "enemy_total_est:") == "enemy_total_est: 6650 (seen peak 103s ago), unplaced 4729"


def test_full_state_the_clock_comes_after_the_stable_sections_and_the_picks_just_before_the_actions():
    army = snapshot_scene().lines
    scout = scout_scene(scout=(50, 40), picks=FOUR_PICKS, confirmed=True, **BUSY).lines

    for lines, sections in ((army, ARMY_SECTIONS), (scout, SCOUT_SECTIONS)):
        clock = lines.index("ACTIONS:") - 1
        assert lines[clock].startswith(("t=", "score: ")) and lines[clock - 1].startswith("last_picks:")
        assert lines[0].startswith(sections)
        assert max(index for index, line in enumerate(lines) if line.startswith(sections)) < clock
    assert not any(line.startswith("our forces:") for line in scout)


def test_full_state_the_snapshot_at_08_45_of_a_1200_s_match_reads_11_14_left():
    scene = snapshot_scene()
    lines, score = scene.lines, scene.question.request.state_object["score"]

    assert lines[lines.index("ACTIONS:") - 1].endswith(" t=08:45 left=11:14 plan=HOLD")
    assert (score["t"], score["left"]) == ("08:45", "11:14")


@pytest.mark.parametrize("tick", [0, 7500, SNAPSHOT_TICK, 29990])
def test_full_state_left_is_the_match_length_less_t_as_a_fake_match_s_state_gives_it(tick):
    state = fake_match_state(tick)
    agent = new_agent(CombatManager(), plan_selector=FixedPlan("HOLD"))
    ask(agent, state, situations.context(TWO_STARTS))
    seconds = agent.world.game_seconds

    assert state["time"]["max_minutes"] * 60 == MATCH_SECONDS and seconds == tick / 25
    assert command_army.score_line(agent.world, agent.ctx).endswith(
        f" t={command_prompt.mm_ss(seconds)} left={command_prompt.mm_ss(MATCH_SECONDS - seconds)} plan=HOLD"
    )


def test_full_state_the_scout_question_names_the_enemy_defenses_and_groups_within_2500_characters():
    built = scout_scene(scout=(50, 40), picks=FOUR_PICKS, confirmed=True, **BUSY)
    lines = built.lines

    assert prompt_chars(built.question.request) <= 2500
    assert section(lines, "enemy defenses:") == ["pbox (66,44) range 6", "pbox (66,52) range 6"]
    assert section(lines, "enemy groups:") == [
        "2 e1 at (57,37), value 200, in sight, still",
        "1 2tnk at (60,44), value 850, in sight, still",
    ]


BUSY_FIELDS = (*SNAPSHOT_FIELDS, ore_field((30, 70), 40), ore_field((70, 85), 40))
# Eight enemy groups of three, each more than 8 cells from the others, and six defenses.
BUSY_GROUPS = [
    enemy(800 + 3 * group + n, unit_type, x + n, y)
    for group, (x, y) in enumerate(((52, 68), (62, 72), (72, 66), (40, 80), (60, 30), (75, 40), (85, 55), (50, 50)))
    for n, unit_type in enumerate(("3tnk", "e3", "ftrk"))
]
BUSY_DEFENSES = [*SNAPSHOT_DEFENSES, enemy(720, "pbox", 40, 75), enemy(721, "gun", 58, 76), enemy(722, "tsla", 80, 50)]
BUSY_PICKS = (Pick("A", "pull back", 11000, 5000, 6000, 0.5), *SNAPSHOT_PICKS)


def listed(lines: list[str], heading: str) -> int:
    """How many items a banded section holds, those it writes and those it says it leaves out (`+2 farther`)."""
    found = section(lines, heading)
    left = sum(int(line.split()[0][1:]) for line in found if line.endswith(" farther"))
    return len(found) - (1 if left else 0) + left


def test_full_state_a_busy_scene_of_8_enemy_groups_6_defenses_and_4_picks_fits_6000_with_gather():
    scene = snapshot_scene(
        enemies=BUSY_GROUPS,
        buildings=[*BUSY_DEFENSES, SNAPSHOT_REFINERY, enemy(714, "fix", 68, 55), enemy(730, "weap", 80, 45)],
        picks=BUSY_PICKS,
        fields=BUSY_FIELDS,
        threat_active=True,
    )
    lines = scene.lines
    request = scene.question.request

    assert prompt_chars(request) <= 6000 and request.system_text == command_army.SYSTEM_TEXT
    assert listed(lines, "enemy forces:") == 8 == len(request.state_object["enemy_forces"])
    assert "gather" in scene.kinds()


# An enemy force far to the north-east, more than 35 cells from main: the far band.
FAR_FORCE = [enemy(760, "3tnk", 80, 30), enemy(761, "3tnk", 81, 30)]


def test_full_state_over_the_cap_the_far_band_goes_first_then_the_mid_band_then_the_picks(monkeypatch):
    def shown(scene: ArmyScene) -> tuple[int, tuple[str, ...]]:
        lines = scene.lines
        picks = line_of(lines, "last_picks:").count(";") + 1
        items = [line for heading in ARMY_SECTIONS[:3] for line in section(lines, heading)]
        distances = [int(found.group(1)) for line in items if (found := re.search(r", (\d+) cells ", line))]
        bands = ("near", "mid", "far")
        return picks, tuple(band for band, low in zip(bands, (0, 16, 36)) if any(low <= dist for dist in distances))

    steps = []
    scene = snapshot_scene(enemies=[*SNAPSHOT_ENEMIES, *FAR_FORCE])
    for _ in range(5):
        steps.append(shown(scene))
        monkeypatch.setattr(command_army, "MAX_PROMPT_CHARS", prompt_chars(scene.question.request) - 1)
        scene = snapshot_scene(enemies=[*SNAPSHOT_ENEMIES, *FAR_FORCE])

    assert steps == [(3, ("near", "mid", "far")), (3, ("near", "mid")), (3, ("near",)), (2, ("near",)), (1, ("near",))]
    assert scene.question.request.system_text == command_army.SYSTEM_TEXT


@pytest.mark.parametrize(
    "dry, refinery, served",
    [
        (False, None, "served by us"),
        (False, enemy(740, "proc", 37, 37), "served by us, enemy"),
        (True, None, "dry"),
    ],
    ids=["ours", "ours and the enemy's", "dry"],
)
def test_full_state_an_ore_field_is_served_by_the_sides_whose_refineries_count_against_it_or_dry(dry, refinery, served):
    scene = snapshot_scene(buildings=[*SNAPSHOT_BUILDINGS, *([refinery] if refinery else [])])
    world, ctx = scene.agent.world, scene.agent.ctx
    ctx.dry_fields = frozenset({SNAPSHOT_FIELDS[0]}) if dry else frozenset()

    lines = command_state.ore_field_lines(world, ctx, world.base_center)

    assert lines[0] == f"(33,41) 100 cells, {served}"


def tank_pair(x: int) -> list[dict]:
    """Two medium tanks in a column from (x, 46)."""
    return [enemy(600 + n, "2tnk", x, 46 + n) for n in range(2)]


@pytest.mark.parametrize(
    "sightings, line",
    [
        (((1000, tank_pair(50)), (1025, tank_pair(48))), "2 medium tank at (48,47), value 1700, in sight, moving"),
        (((1000, tank_pair(50)), (1025, tank_pair(49))), "2 medium tank at (49,47), value 1700, in sight, still"),
        (((1025, tank_pair(48)),), "2 medium tank at (48,47), value 1700, in sight, still"),
        (((1000, tank_pair(50)), (2000, [])), "2 medium tank at (50,47), value 1700, seen 30s ago, still"),
        (((1000, tank_pair(50)), (2250, [])), "2 medium tank at (50,47), value 1700, seen 60s ago, still"),
    ],
    ids=["2 cells in a second", "1 cell", "first seen", "40 s ago", "50 s ago"],
)
def test_full_state_an_enemy_group_moves_when_its_centroid_moved_over_1_cell_and_its_age_is_in_30_s_steps(
    sightings, line
):
    agent, watch = closing_watch(*sightings)

    state = command_state.full_state(agent.world, agent.ctx, NAMES, (22, 48), closing=watch)

    assert state.enemy_groups == ("enemy groups:", line)


# -- the military state of main's question ---------------------------------------------------------------------------

# The snapshot's question as main reads it: our bases, the enemy's, the enemy forces and ours.
SNAPSHOT_OUR_BASES = [
    "B1 main, 32 cells NW (22,47): 4 buildings value 4200; ground defense 0; air-only none; guard 2700; exposed harvesters 4; closing: none",
    "B2 outpost, 32 cells W (16,79): 1 building value 1400; ground defense 0; air-only none; guard 0; exposed harvesters 2; closing: none",
]
SNAPSHOT_ENEMY_BASES = [
    "E1 outpost, 9 cells SE (51,75): 1 building value 700 (1 sam site); ground defense 0; air-only 1 sam site; guard 1921; exposed harvesters 0; within defense reach: no, exposed; in range: no; fight 3.0x; crush-adjusted 3.0x; beatable: yes",
    "E3 outpost, 24 cells E (70,64): 1 building value 1400; ground defense 0; air-only none; guard 0; exposed harvesters 0; within defense reach: no, exposed; in range: no; fight no enemy; crush-adjusted no enemy; beatable: yes",
    "E2 main, 28 cells NE (68,51): 3 buildings value 3000; ground defense 3600; air-only none; guard 0; exposed harvesters 0; within reach of flame tower: yes; in range: no; fight 1.6x; crush-adjusted 1.6x; beatable: yes",
]
SNAPSHOT_ENEMY_FORCES = [
    "F1, 6 cells E (52,68): 3 units strength 1921 (1 heavy tank, 1 flak truck, 1 rocket soldier); 0 crushable, 1 anti-tank, 2 armor; still, in sight; within defense reach: no; in range: yes; fight 3.0x; crush-adjusted 3.0x; beatable: yes",
]
SNAPSHOT_OUR_FORCES = [
    "main: 22 units (2 medium tank, 14 rifle infantry, 4 rocket soldier, 1 artillery, 1 light tank) strength 5850, hp 100%, 31 cells SE (46,68) from base",
    "staging: 2 units (2 medium tank) strength 1700, hp 100%, 23 cells NW (33,49)",
    "guard: 5 units (5 rifle infantry) strength 500, hp 100%, 29 cells NW (29,45), answers none",
    "scouts: 1 unit (1 ranger) strength 500, hp 100%, 26 cells NW (34,45), doing idle",
    "scouts: 1 unit (1 ranger) strength 500, hp 100%, 56 cells NE (72,18), doing move to (32,48)",
]
# An enemy expansion 20 cells south of main's body, its center 17 cells from the SAM site so that the two stay apart
# bases at base_merge_cells 16, and a medium tank of main in repair by the mechanic.
EXPANSION = [enemy(750, "fact", 40, 87), enemy(751, "proc", 42, 90), enemy(752, "apwr", 38, 85)]
REPAIRING_TANK = (situations.unit(545, "2tnk", 36, 62, hp=35),)


def expansion_scene(**options) -> ArmyScene:
    """The snapshot's scene with an enemy expansion and a medium tank of main in repair."""
    return snapshot_scene(buildings=[*SNAPSHOT_BUILDINGS, *EXPANSION], repairing=REPAIRING_TANK, **options)


def test_military_state_the_snapshot_with_an_expansion_and_a_repairing_tank_says_every_fact_and_no_economy():
    scene = expansion_scene()
    lines = scene.lines

    assert prompt_chars(scene.question.request) <= 6000
    assert not re.search(r"^(money|income|cash|harvesters?|ore fields?)\b", "\n".join(lines), re.MULTILINE)
    assert not any("harvest" in line for line in section(lines, "our forces:"))
    assert line_of(lines, "E4 expansion, ").startswith(
        "E4 expansion, 20 cells S (40,87): 3 buildings value 3900; ground defense 0; air-only none; guard 0; "
    )
    assert "; ground defense 0; air-only 1 sam site; " in line_of(lines, "E1 outpost, 9 cells SE (51,75): ")
    assert "; ground defense 3600; air-only none; " in line_of(lines, "E2 main, 28 cells NE (68,51): ")
    assert line_of(lines, "F1, ").startswith(
        "F1, 6 cells E (52,68): 3 units strength 1921 (1 heavy tank, 1 flak truck, 1 rocket soldier); 0 crushable, "
        "1 anti-tank, 2 armor; still, in sight; "
    )
    assert line_of(lines, "repairing: ") == (
        "repairing: 1 unit (1 medium tank) strength 298, hp 35%, 12 cells NW (36,62), back in 28s"
    )
    assert line_of(lines, "staging: ").startswith("staging: 2 units (2 medium tank) strength 1700, hp 100%, ")
    assert (
        lines[lines.index("ACTIONS:") - 1] == "score: lead +1450 (killed 9050 lost 7600) t=08:45 left=11:14 plan=HOLD"
    )


def test_military_state_every_option_ends_with_a_consequence_and_x_attacks_the_expansion():
    scene = expansion_scene()
    options = scene.question.request.options

    assert lettered_action(scene, "X").head == (
        "attack enemy expansion E4 at (40,87), 20 cells, 3 buildings value 3900, unguarded"
    )
    assert all(len(option.description.split(f" {command_prompt.CONSEQUENCE_MARK} ")) == 2 for option in options)
    assert "X: attack enemy expansion E4 at (40,87), 20 cells, 3 buildings value 3900, unguarded -> exposed target" in (
        scene.lines
    )
    assert "D: gather at staging (32,48), 24 cells -> covers B1, joins 2 at staging" in scene.lines


def test_military_state_x_takes_a_place_before_a_generic_target_and_m_on_the_expansion_is_not_offered():
    # Every place taken: X takes the hit's place; the economy target is the expansion's refinery, which X names.
    assert (letters(snapshot_scene(threat_active=True)), letters(expansion_scene(threat_active=True))) == (
        "ABCDIM",
        "ABCDIX",
    )
    assert "M" not in letters(expansion_scene()) and "X" in letters(expansion_scene())


def test_military_state_no_x_while_the_expansion_cannot_be_beaten_or_the_gate_is_closed_and_it_is_far():
    guarded = [*SNAPSHOT_ENEMIES, *[enemy(770 + n, "4tnk", 43 + n % 3, 90 + n // 3) for n in range(6)]]

    assert "X" not in letters(expansion_scene(enemies=guarded))
    assert "X" not in letters(expansion_scene(is_gate_open=False))


# The snapshot's harvesters without the mechanic, which stands a cell from the repairing tank at (36,62).
NO_MECHANIC = [unit for unit in SNAPSHOT_OTHERS if unit["type"] != "mech"]


@pytest.mark.parametrize(
    "others, own_buildings, said",
    [
        (SNAPSHOT_OTHERS, (), "back in 28s"),
        (NO_MECHANIC, (), "waiting at staging"),
        ([*NO_MECHANIC, situations.unit(556, "mech", 21, 62)], (), "back in 28s"),
        ([*NO_MECHANIC, situations.unit(556, "mech", 20, 62)], (), "waiting at staging"),
        (NO_MECHANIC, (("fix", 36, 47),), "back in 28s"),
        (NO_MECHANIC, (("fix", 36, 46),), "waiting at staging"),
    ],
    ids=["mechanic beside it", "neither", "mechanic 15 cells", "mechanic 16 cells", "depot 15 cells", "depot 16 cells"],
)
def test_military_state_a_repairing_tank_is_back_in_at_2_hp_a_second_only_with_a_mechanic_or_depot_within_15_cells(
    others, own_buildings, said
):
    scene = expansion_scene(others=others, own_buildings=own_buildings)
    forces = scene.question.request.state_object["our_forces"]
    (repairing,) = [force for force in forces if force["status"] == "repairing"]

    # From 35% to repair_done_hp 90 at 2% a second: 27.5 s.
    assert line_of(scene.lines, "repairing: ") == (
        f"repairing: 1 unit (1 medium tank) strength 298, hp 35%, 12 cells NW (36,62), {said}"
    )
    eta, waiting = repairing.get("eta_s"), repairing.get("waiting_at_staging", False)
    assert (eta, waiting) == ((28, False) if said.startswith("back in") else (None, True))


# Twelve rifles in a tight cluster 9 cells east of main's tanks.
TWELVE_RIFLES = [enemy(900 + n, "e1", 52 + n % 4, 46 + n // 4) for n in range(12)]


@pytest.mark.parametrize("count, counted", [(6, "infantry at 1/3"), (2, "infantry in full")], ids=["6 tanks", "2"])
def test_military_state_crushable_infantry_counts_at_a_third_while_main_has_the_tanks_its_run_takes(count, counted):
    rifles = [situations.unit(420 + n, "e1", 42, 44 + n) for n in range(6)]
    scene = army_scene([*tanks(count, unit_type="2tnk"), *rifles], visible_enemies=TWELVE_RIFLES)
    (force,) = scene.question.request.state_object["enemy_forces"]

    assert f"; crush: 12 crushable, plan 3 tanks, have {count}, {counted}; " in line_of(scene.lines, "F1, ")
    assert (force["crushable"], force["planned_tanks"], force["tanks"]) == (12, 3, count)
    assert (force["crush_ratio"] > force["ratio"]) is (count == 6)


# How the text writes a boolean of the JSON object.
YES_NO = {True: "yes", False: "no"}


def test_military_state_the_json_object_for_systemone_holds_the_facts_of_the_bases_lines():
    scene = expansion_scene()
    lines, state = scene.lines, scene.question.request.state_object

    assert json.loads(json.dumps(state)) == state
    for base in state["bases"]:
        line = line_of(lines, f"{base['id']} {base['kind']}, ")
        assert f", {base['dist']} cells {base['dir']} ({base['pos'][0]},{base['pos'][1]}): " in line
        assert f" value {base['building_value']}" in line and f"; ground defense {base['def_ground']}" in line
        assert f"; guard {base['guard']};" in line and f"; exposed harvesters {base['exposed_harvesters']}" in line
        if base["side"] == "enemy":
            assert f"; in range: {YES_NO[base['in_range']]};" in line
            assert ("; within reach of " in line) is base["within_defense_reach"]
            assert line.endswith(f"; beatable: {YES_NO[base['beatable']]}")


def test_military_state_the_json_object_for_systemone_holds_the_facts_of_the_forces_lines():
    scene = expansion_scene()
    lines, state = scene.lines, scene.question.request.state_object

    for force in state["enemy_forces"]:
        line = line_of(lines, f"{force['id']}, ")
        assert f"strength {force['strength']}" in line and f"{force['crushable']} crushable" in line
        assert f"; {force['motion']}, in sight;" in line and f"; in range: {YES_NO[force['in_range']]};" in line
    assert [(force["status"], force["strength"], force["hp"]) for force in state["our_forces"]] == [
        (line.split(":")[0], int(line.split(" strength ")[1].split(",")[0]), int(line.split(" hp ")[1].split("%")[0]))
        for line in section(lines, "our forces:")
    ]


def test_military_state_the_json_object_for_systemone_holds_the_score_the_estimate_and_the_current_line():
    scene = expansion_scene()
    lines, state = scene.lines, scene.question.request.state_object

    assert state["score"] == {"lead": 1450, "killed": 9050, "lost": 7600, "t": "08:45", "left": "11:14", "plan": "HOLD"}
    assert state["enemy_estimate"] == {"value": 6650, "assumed": False, "seen_s": 103, "unplaced": 4729}
    assert state["current"] == line_of(lines, "current:").removeprefix("current: ")


def test_military_state_the_options_keep_one_letter_order_in_the_text_and_in_the_systemone_criteria():
    first, second = expansion_scene(threat_active=True), expansion_scene(threat_active=True)
    request = first.question.request
    ids = [option.id for option in request.options]
    body = systemone.build_body(request)

    assert ids == sorted(ids) == [option.id for option in second.question.request.options]
    assert list(body["questions"]["action"]["criteria"]) == ids and body["state"] == request.state_object
    assert request.state_object == second.question.request.state_object


def test_military_state_a_cluster_keeps_its_number_while_its_center_moves_under_8_cells_and_a_new_one_takes_the_lowest():
    labels = command_state.ClusterLabels()

    assert labels.numbers("F", [(10, 10), (40, 40)], 8) == [1, 2]
    # The second moved 2 cells and keeps 2; the first is gone, so the new one takes 1.
    assert labels.numbers("F", [(40, 42), (70, 70)], 8) == [2, 1]
    # 8 cells is too far: a new number.
    assert labels.numbers("F", [(48, 42)], 8) == [1]


@dataclasses.dataclass(frozen=True)
class Placed:
    """A building of either side as the military state reads one."""

    id: int
    type: str
    x: int
    y: int


def merged(*buildings: Placed) -> list[list[int]]:
    """The ids of each base the buildings make at the built-in [commander] thresholds."""
    return [[member.id for member in base.members] for base in command_state.base_clusters(buildings, ARMY.commander)]


def test_military_state_a_refinery_8_5_cells_from_our_yard_is_part_of_b1_and_one_31_cells_off_stays_an_outpost():
    lines = snapshot_scene().lines

    assert round(cells.distance(YARD[1:], SNAPSHOT_REFINERIES[0][1:]), 1) == 8.5
    assert line_of(lines, "B1 ").startswith("B1 main, 32 cells NW (22,47): 4 buildings value 4200; ")
    assert line_of(lines, "B2 ").startswith("B2 outpost, 32 cells W (16,79): 1 building value 1400; ")
    assert not any(line.startswith("B3 ") for line in lines)


def test_military_state_a_yardless_cluster_joins_the_nearest_base_with_a_yard_within_16_cells_and_no_other():
    yard, power = Placed(1, "fact", 20, 20), Placed(2, "powr", 18, 20)
    expansion = Placed(3, "fact", 40, 20)

    # 15.6 cells from the main's center and 13.5 from the expansion's: the nearer.
    assert merged(yard, power, expansion, Placed(4, "proc", 31, 30)) == [[1, 2], [3, 4]]
    # 17 cells from the main and 27 from the expansion: an outpost.
    assert merged(yard, power, expansion, Placed(4, "proc", 19, 37)) == [[1, 2], [3], [4]]
    # An expansion 12 cells from the main keeps its own yard and stays a base of its own.
    assert merged(yard, power, Placed(3, "fact", 31, 20)) == [[1, 2], [3]]
    # No yard on that side: nothing merges.
    assert merged(power, Placed(4, "proc", 28, 20)) == [[2], [4]]


def test_military_state_a_merged_base_keeps_its_yard_cluster_s_label_however_far_the_merge_moves_its_center():
    yard = Placed(5, "fact", 20, 20)
    refineries = [Placed(1, "proc", 34, 20), Placed(2, "proc", 34, 22), Placed(3, "proc", 36, 21)]
    labels = command_state.ClusterLabels()
    # The yard stood alone as B2 at the last question; its center and the merged base's are 11 cells apart.
    labels.numbers("B", [(60, 60), (20, 20)], 8)

    (base,) = command_state.base_clusters([yard, *refineries], ARMY.commander)

    assert base.label_center == (20, 20) and [member.id for member in base.members] == [5, 1, 2, 3]
    assert labels.numbers("B", [base.label_center], 8) == [2]


def test_military_state_the_commander_s_labels_keep_an_enemy_force_s_number_as_it_moves():
    labels = command_state.ClusterLabels()
    snapshot_scene(labels=labels)
    moved = [{**record, "x": record["x"] + 3} for record in SNAPSHOT_ENEMIES]
    # A new force whose ids come first, far to the north-east.
    newcomers = [enemy(690, "3tnk", 80, 30), enemy(691, "3tnk", 81, 30)]

    lines = snapshot_scene(enemies=[*newcomers, *moved], labels=labels).lines

    assert line_of(lines, "F1, ").startswith("F1, 9 cells E (55,68): 3 units ")
    assert line_of(lines, "F2, ").startswith("F2, ")


def test_military_state_the_scouts_say_what_most_of_them_are_doing_and_an_attack_names_its_target():
    attack = {"cmd": "attack", "units": [561], "target": 700}
    lines = snapshot_scene(orders={**SNAPSHOT_ORDERS, 561: attack}).lines

    assert "scouts: 1 unit (1 ranger) strength 500, hp 100%, 56 cells NE (72,18), doing attack heavy tank (52,68)" in (
        lines
    )


# -- the commanded objective ----------------------------------------------------------------------------


def commanded_orders(scene: ArmyScene, kind: str, *, tick: int = 12505, **observation) -> list[dict]:
    """The orders of the next cycle with main given the scene's action of a kind as its standing action."""
    scene.combat.command(scene.action(kind))
    state = {**scene.state, "state_version": tick}
    state["observation"] = {**scene.state["observation"], "tick": tick, **observation}
    return ask(scene.agent, state)


def unit_ids(units: list[dict]) -> list[int]:
    return [unit["id"] for unit in units]


@pytest.mark.parametrize("plan", ["HOLD", "PRESERVE", "PRESS", "FINISH"])
def test_army_prompt_advance_marches_every_unit_of_main_on_the_objective_under_every_plan(plan):
    army = tanks(6, 40, 47)
    scene = army_scene(army, plan=plan, visible_enemy_buildings=[enemy(641, "weap", 72, 52)])

    orders = commanded_orders(scene, "advance")

    assert [(order["cmd"], order["to"], sorted(order["units"])) for order in orders] == [
        ("attack_move", [72, 52], unit_ids(army))
    ]
    assert scene.combat.commanded_order["cmd"] == "attack_move" and scene.agent.ctx.army == set(unit_ids(army))


def test_army_prompt_attack_is_an_attack_move_on_the_group_and_fights_it_once_in_contact():
    army = tanks(6, 40, 47)
    far = [enemy(610 + n, "1tnk", 57, 46 + n) for n in range(2)]
    scene = army_scene(army, visible_enemies=far, tuning=UNSTAGED_ARMY)
    target = scene.action("attack").cell

    away = commanded_orders(scene, "attack")
    close = [enemy(610 + n, "1tnk", 46, 46 + n) for n in range(2)]
    fighting = commanded_orders(scene, "attack", tick=12510, visible_enemies=close)

    assert [(order["cmd"], order["to"]) for order in away] == [("attack_move", list(target))]
    assert {order["cmd"] for order in fighting} == {"attack"}


def test_army_prompt_siege_has_the_long_range_units_fire_from_their_band_and_the_one_order_leaves_them_out():
    army = [*tanks(4, 40, 47), *[situations.unit(430 + n, "v2rl", 38, 48 + n) for n in range(2)]]
    scene = army_scene(army, visible_enemy_buildings=[enemy(600, "pbox", 60, 47)], tuning=UNSTAGED_ARMY)

    orders = commanded_orders(scene, "siege")

    v2s = {430, 431}
    assert any(v2s & set(order["units"]) for order in orders)
    assert not v2s & set(scene.combat.commanded_order["units"])
    assert scene.combat.commanded_order["to"] == [60, 47]


def test_army_prompt_flank_moves_main_to_the_side_waypoint_then_marches_on_the_objective():
    # Without the open construction yard every building is covered, and the objective is the war factory.
    covered = [building for building in FRONT["visible_enemy_buildings"] if building["type"] != "fact"]
    scene = army_scene(tanks(6, 40, 47), visible_enemy_buildings=covered)
    flank = scene.action("flank")
    at_waypoint = tanks(6, flank.via[0] - 1, flank.via[1] - 1)

    first = commanded_orders(scene, "flank")
    then = commanded_orders(scene, "flank", tick=12510, units=at_waypoint)

    assert [(order["cmd"], order["to"]) for order in first] == [("move", list(flank.via))]
    assert [(order["cmd"], order["to"]) for order in then] == [("attack_move", [72, 52])]


@pytest.mark.parametrize("kind", ["pull_back", "retreat_home"])
def test_army_prompt_pull_back_and_retreat_home_move_every_unit_of_main_to_the_action_s_cell(kind):
    army = [*tanks(6), *tanks(3, 26, 50, first=440)]
    scene = army_scene(army, threat_active=True, visible_enemies=FRONT["visible_enemies"])

    orders = commanded_orders(scene, kind)

    cell = scene.action(kind).cell
    assert kind != "retreat_home" or cell == STAGING
    assert [(order["cmd"], order["to"]) for order in orders] == [("move", list(cell))]
    assert set(orders[0]["units"]) <= set(unit_ids(army)) and scene.agent.ctx.army == set()


def test_army_prompt_hold_stops_the_units_that_move_and_orders_nothing_once_all_stand():
    army = tanks(6, 40, 47, idle=False)
    scene = army_scene(army, threat_active=True)

    moving = commanded_orders(scene, "hold")
    standing = commanded_orders(scene, "hold", tick=12510, units=tanks(6, 40, 47))

    assert [(order["cmd"], sorted(order["units"])) for order in moving] == [("stop", unit_ids(army))]
    assert standing == [] and scene.combat.commanded_order is None


def test_army_prompt_without_a_standing_action_main_plays_its_plan_as_before():
    scene = army_scene(tanks(6, 40, 47), plan="HOLD")

    orders = commanded_orders(scene, "advance")
    scene.combat.command(None)
    state = {**scene.state, "state_version": 12510}
    state["observation"] = {**scene.state["observation"], "tick": 12510}
    after = ask(scene.agent, state)

    assert orders and all(order.get("to") != [72, 52] for order in after) and scene.combat.commanded_order is None


def test_army_prompt_siege_needs_a_known_defense_as_well_as_long_range_units_and_names_are_codes_without_a_glossary():
    v2s = [situations.unit(430 + n, "v2rl", 38, 48 + n) for n in range(2)]

    without_defense = army_scene([*tanks(4, 40, 47), *v2s])
    without_v2s = army_scene(tanks(4, 40, 47), visible_enemy_buildings=[enemy(600, "pbox", 60, 47)])

    assert "siege" not in without_defense.kinds() and "siege" not in without_v2s.kinds()
    assert command_prompt.plain_names(None) == {} and command_prompt.named("3tnk", {}) == "3tnk"


# -- the commander's order with the focus fire in contact ------------------------------------------------------------

WAR_FACTORY = enemy(641, "weap", 72, 52)
# An enemy rifle 4.1 cells from the heavy tank at (42, 47): the three tanks at y 47 are in contact with it.
CONTACT_RIFLE = enemy(610, "e1", 46, 46)
NEAR_TANKS, FAR_TANKS = [400, 401, 402], [440, 441, 442]
ARMY_FOCUS_OFF = dataclasses.replace(ARMY, combat=dataclasses.replace(ARMY.combat, contact_focus=False))


def commanded_main(units: list[dict], kind: str, *, tuning: Tuning = ARMY, **observation) -> ArmyScene:
    """main of these units with a commander that commands it, its forward gate open and its standing action the one
    of a kind main's question offers."""
    scene = army_scene(units, tuning=tuning, **observation)
    commander = Commander(situations.StubChooser(), CommanderTuning())
    commander.role, commander.standing = "main", scene.action(kind)
    commander.gate.is_open = True
    scene.agent.ctx.commander = commander
    return scene


def next_cycle(scene: ArmyScene, tick: int, **observation) -> list[dict]:
    """The orders of a later cycle of the scene's state, with the observation's fields given replaced."""
    state = {**scene.state, "state_version": tick}
    state["observation"] = {**scene.state["observation"], "tick": tick, **observation}
    return ask(scene.agent, state)


def split_army(far_y: int) -> list[dict]:
    """Three heavy tanks at y 47 (400-402) and three at `far_y` (440-442), all from x 40."""
    return [*tanks(3, 40, 47), *tanks(3, 40, far_y, first=440)]


@pytest.mark.parametrize("far_y", [56, 70], ids=["enemy near the centroid", "enemy 13.5 cells from the centroid"])
def test_contact_focus_under_a_commanded_advance_a_unit_in_contact_gets_its_attack_and_is_not_in_the_commander_s_order(
    far_y,
):
    scene = commanded_main(split_army(far_y), "advance", visible_enemy_buildings=[WAR_FACTORY])

    orders = next_cycle(scene, 12505, visible_enemies=[CONTACT_RIFLE])

    commander_order, *others = orders
    assert (commander_order["cmd"], commander_order["to"], sorted(commander_order["units"])) == (
        "attack_move",
        [72, 52],
        FAR_TANKS,
    )
    assert [(order["cmd"], sorted(order["units"]), order["target"]) for order in others] == [
        ("attack", NEAR_TANKS, 610)
    ]
    assert sorted(scene.combat.commanded_order["units"]) == FAR_TANKS


def test_contact_focus_off_the_commander_s_advance_takes_every_unit_and_the_attack_is_dropped():
    scene = commanded_main(split_army(56), "advance", tuning=ARMY_FOCUS_OFF, visible_enemy_buildings=[WAR_FACTORY])

    orders = next_cycle(scene, 12505, visible_enemies=[CONTACT_RIFLE])

    assert [(order["cmd"], sorted(order["units"])) for order in orders] == [("attack_move", NEAR_TANKS + FAR_TANKS)]


def test_contact_focus_under_a_commanded_action_the_units_with_no_target_keep_its_order_not_a_chase_out_of_sight():
    # An enemy rifle seen at (40, 64) south of the far tanks, out of sight in the cycle: with the chase, the far tanks
    # would be sent after it.
    scene = commanded_main(split_army(56), "advance", visible_enemy_buildings=[WAR_FACTORY])
    next_cycle(scene, 12503, visible_enemies=[CONTACT_RIFLE, enemy(611, "e1", 40, 64)])

    orders = next_cycle(scene, 12505, visible_enemies=[CONTACT_RIFLE])

    assert all(order.get("to") != [40, 64] for order in orders)
    assert sorted(orders[0]["units"]) == FAR_TANKS and orders[0]["to"] == [72, 52]


def test_contact_focus_the_commander_s_order_is_due_again_only_for_a_unit_left_in_it_standing_idle():
    moving = [*tanks(3, 40, 47, idle=False), *tanks(3, 40, 56, first=440, idle=False)]
    idle_in_contact = [*tanks(3, 40, 47), *tanks(3, 40, 56, first=440, idle=False)]
    on = commanded_main(moving, "advance", visible_enemy_buildings=[WAR_FACTORY])
    off = commanded_main(moving, "advance", tuning=ARMY_FOCUS_OFF, visible_enemy_buildings=[WAR_FACTORY])
    for scene in (on, off):
        next_cycle(scene, 12505, units=moving)

    again_on = next_cycle(on, 12510, units=idle_in_contact, visible_enemies=[CONTACT_RIFLE])
    again_off = next_cycle(off, 12510, units=idle_in_contact, visible_enemies=[CONTACT_RIFLE])

    # Five ticks after it was sent: the idle tanks in contact make it due only while they are in it.
    assert [order["cmd"] for order in again_on] == ["attack"]
    assert [order["cmd"] for order in again_off] == ["attack_move"]


@pytest.mark.parametrize("kind", ["pull_back", "retreat_home"])
def test_contact_focus_under_a_pull_back_or_a_retreat_home_a_unit_in_contact_moves_and_gets_no_attack(kind):
    scene = commanded_main(split_army(56), kind, threat_active=True, visible_enemies=FRONT["visible_enemies"])

    orders = next_cycle(scene, 12505, visible_enemies=[CONTACT_RIFLE])

    assert [(order["cmd"], sorted(order["units"])) for order in orders] == [("move", NEAR_TANKS + FAR_TANKS)]
    assert orders[0]["to"] == list(scene.action(kind).cell)


def test_contact_focus_under_the_retreat_override_a_unit_in_contact_moves_with_main_and_gets_no_attack():
    # Ten heavy tanks 12 cells east keep main's fight lost, so that the override holds on.
    losing = [CONTACT_RIFLE, *[enemy(620 + n, "3tnk", 54, 44 + n) for n in range(10)]]
    scene = commanded_main(split_army(56), "gather", visible_enemy_buildings=[WAR_FACTORY])
    scene.agent.ctx.commander.retreat.is_active = True

    orders = next_cycle(scene, 12505, visible_enemies=losing)

    assert [(order["cmd"], sorted(order["units"])) for order in orders] == [("move", NEAR_TANKS + FAR_TANKS)]
    assert scene.agent.ctx.commander.retreat.is_active


def test_contact_focus_under_a_commanded_siege_a_screen_unit_in_contact_keeps_the_siege_s_attack_not_the_order():
    v2s = [situations.unit(430 + n, "v2rl", 38, 48 + n) for n in range(2)]
    screen = [*tanks(3, 40, 47), *tanks(1, 40, 56, first=440)]
    scene = commanded_main([*screen, *v2s], "siege", visible_enemy_buildings=[enemy(600, "pbox", 60, 47)])

    orders = next_cycle(scene, 12505, visible_enemies=[CONTACT_RIFLE])

    commander_order, *others = orders
    assert (commander_order["cmd"], commander_order["units"]) == ("attack_move", [440])
    attacks = [order for order in others if order["cmd"] == "attack" and order["target"] == 610]
    assert sorted(unit for order in attacks for unit in order["units"]) == NEAR_TANKS


# Under hold and gather: an enemy rifle 4 cells from the tank at (42, 47), in its range of 4.75, and one 6 cells from
# the tank at (42, 56), in contact with it but out of its range.
IN_RANGE_RIFLE, OUT_OF_RANGE_RIFLE = enemy(610, "e1", 46, 47), enemy(611, "e1", 48, 56)


def test_contact_focus_under_a_commanded_hold_a_unit_with_an_enemy_in_range_gets_an_attack_and_no_stop():
    moving = [*tanks(3, 40, 47, idle=False), *tanks(3, 40, 56, first=440, idle=False)]
    scene = commanded_main(moving, "hold", threat_active=True)

    orders = next_cycle(scene, 12505, units=moving, visible_enemies=[IN_RANGE_RIFLE, OUT_OF_RANGE_RIFLE])

    stop, attack = orders
    assert (stop["cmd"], sorted(stop["units"])) == ("stop", [400, 401, 440, 441, 442])
    assert (attack["cmd"], attack["units"], attack["target"]) == ("attack", [402], 610)


def test_contact_focus_off_under_a_commanded_hold_every_moving_unit_is_stopped_and_none_fires():
    moving = [*tanks(3, 40, 47, idle=False), *tanks(3, 40, 56, first=440, idle=False)]
    scene = commanded_main(moving, "hold", tuning=ARMY_FOCUS_OFF, threat_active=True)

    orders = next_cycle(scene, 12505, units=moving, visible_enemies=[IN_RANGE_RIFLE, OUT_OF_RANGE_RIFLE])

    assert [(order["cmd"], sorted(order["units"])) for order in orders] == [("stop", NEAR_TANKS + FAR_TANKS)]


def test_contact_focus_under_a_commanded_gather_a_unit_in_range_fires_and_the_units_in_contact_are_not_moved():
    scene = commanded_main(split_army(56), "gather", visible_enemy_buildings=[WAR_FACTORY])

    orders = next_cycle(scene, 12505, visible_enemies=[IN_RANGE_RIFLE, OUT_OF_RANGE_RIFLE])

    # The tanks at (40, 47), (41, 47) and (42, 56) are 6, 5 and 6 cells from a rifle: in contact and out of range,
    # they are neither moved nor sent at it.
    gather, attack = orders
    assert (gather["cmd"], sorted(gather["units"]), gather["to"]) == ("move", [440, 441], [32, 48])
    assert (attack["cmd"], attack["units"], attack["target"]) == ("attack", [402], 610)


# -- the commander: asking, the handover, the answers --------------------------------------------------


def commander_events(match_record) -> list[dict]:
    """The `commander` events of the match record, but the summary the commander writes when the match ends."""
    return [event for event in match_record("commander") if event["kind"] != "summary"]


class AnsweringChooser:
    """A chooser that asks nobody: it keeps what it is asked, and its next poll hands over the result a test gave."""

    def __init__(self) -> None:
        self.submitted: list = []
        self._ready = None

    def submit(self, request) -> bool:
        self.submitted.append(request)
        return True

    def poll(self, current_state_tag: str):
        result, self._ready = self._ready, None
        return result

    def answer(self, letter: str | None, outcome: str = USED) -> None:
        """Have the next poll hand over an answer naming a letter, or a result of another outcome."""
        self._ready = ChoiceResult(letter, None, "red_alert_commander", 40, outcome)


@dataclasses.dataclass
class Commanding:
    """A commander over a stub chooser in a match whose base shows the units given, and its world and context."""

    commander: Commander
    chooser: AnsweringChooser
    agent: situations.RedAlertAgent
    question: command_prompt.Question

    def cycle(self, tick: int, *, units: list[dict] | None = None, offered=None, situation=None) -> Action | None:
        """One cycle at a tick: the state taken in, the commander's cycle begun, the scout's answer taken against the
        actions offered (the question's when none are given) in the situation given (none known without) and the
        question asked; the standing action."""
        state = base_state("england", YARD, POWER, BARRACKS, tick=tick, units=units or SIX_UNITS[:3])
        ask(self.agent, state)
        world, ctx = self.agent.world, self.agent.ctx
        self.commander.begin_cycle(world, ctx)
        standing = self.commander.take(
            self.commander.role, self.question.actions if offered is None else offered, world, situation
        )
        self.commander.ask(self.commander.role, self.question, world)
        return standing


SIX_UNITS = tanks(6, 30, 47, unit_type="2tnk")


# The commander these tests were worked out on: no continue option, a scout pick kept commit_seconds like any other,
# and no script fallback for the stub chooser's silence.
LEGACY_SCOUT = CommanderTuning(continue_option=False, scout_commit=False, script_fallback=False)


def commanding(tuning: CommanderTuning = LEGACY_SCOUT) -> Commanding:
    """A commander over a stub chooser, before its first cycle, with a scout's question to ask."""
    chooser = AnsweringChooser()
    agent = new_agent(tuning=SCOUTING)
    ask(
        agent,
        base_state("england", YARD, POWER, BARRACKS, tick=900, units=SIX_UNITS[:3]),
        situations.context(TWO_STARTS),
    )
    question = scout_scene(tick=500, scout=(30, 48)).question
    return Commanding(Commander(chooser, tuning), chooser, agent, question)


def test_commander_core_one_request_is_in_flight_and_the_next_is_asked_in_the_cycle_after_an_answer():
    scene = commanding()

    asked = []
    for tick in (1000, 1005):
        scene.cycle(tick)
        asked.append(len(scene.chooser.submitted))
    scene.chooser.answer("A")
    scene.cycle(1010)
    asked.append(len(scene.chooser.submitted))
    scene.cycle(1015)
    asked.append(len(scene.chooser.submitted))
    scene.chooser.answer(None, TIMEOUT)
    scene.cycle(1020)
    scene.cycle(1025)
    asked.append(len(scene.chooser.submitted))

    assert asked == [1, 1, 1, 2, 3]
    assert all(request.with_reason is False and request.answer_tokens == 16 for request in scene.chooser.submitted)


def test_commander_core_a_fresh_answer_naming_an_offered_action_is_the_standing_action_with_an_event_and_a_pick(
    match_record,
):
    scene = commanding()
    scene.cycle(1000)
    chosen = scene.question.actions[1]
    scene.chooser.answer(chosen.id)

    standing = scene.cycle(1025)

    assert standing == chosen and scene.commander.taken_tick == 1025
    assert [(pick.id, pick.label, pick.tick) for pick in scene.commander.picks] == [(chosen.id, chosen.label, 1025)]
    assert commander_events(match_record) == [
        {"kind": "action", "role": "scout", "action_id": chosen.id, "action": chosen.head, "tick": 1025}
    ]


def test_commander_core_a_pick_keeps_the_army_ratio_the_plan_rules_published_in_the_match_s_model_history():
    scene = commanding()
    scene.cycle(1000)
    scene.chooser.answer(scene.question.actions[1].id)

    scene.cycle(1025)

    published = scene.agent.ctx.model_history.ratio
    assert published is not None and [pick.ratio for pick in scene.commander.picks] == [published]


@pytest.mark.parametrize("age, used", [(25, True), (26, False)], ids=["25 ticks", "26 ticks"])
def test_commander_core_an_answer_older_than_25_ticks_is_dropped_as_stale(match_record, age, used):
    scene = commanding()
    scene.cycle(1000)
    scene.chooser.answer("A")

    standing = scene.cycle(1000 + age)

    assert (standing is not None) is used
    events = commander_events(match_record)
    assert used or [(event["kind"], event["reason"]) for event in events] == [("dropped", "stale")]


def test_commander_core_an_answer_naming_an_action_no_longer_offered_is_dropped_and_the_standing_action_kept(
    match_record,
):
    scene = commanding()
    scene.cycle(1000)
    scene.chooser.answer("A")
    first = scene.cycle(1005)
    scene.cycle(1010)
    scene.chooser.answer(scene.question.actions[1].id)

    standing = scene.cycle(1015, offered=(scene.question.actions[0],))

    assert standing == first == scene.question.actions[0]
    assert [event["kind"] for event in commander_events(match_record)] == ["action", "dropped"]


def test_commander_core_hands_over_to_main_at_6_combat_units_and_scouts_do_not_count(match_record):
    scene = commanding()
    scene.cycle(1000, units=SIX_UNITS[:5])
    scout_role = scene.commander.role
    scene.agent.ctx.groups.assign(SIX_UNITS[0]["id"], "scout")
    scene.cycle(1005, units=SIX_UNITS)
    still_scout = scene.commander.role
    scene.cycle(1010, units=[*SIX_UNITS, *tanks(1, 34, 47, first=500, unit_type="2tnk")])
    scene.cycle(1015, units=SIX_UNITS[:2])

    assert (scout_role, still_scout, scene.commander.role) == ("scout", "scout", "main")
    assert commander_events(match_record) == [{"kind": "handover", "role": "main", "tick": 1010, "combat_units": 6}]


def test_commander_core_after_the_handover_an_answer_to_the_scout_s_question_is_not_offered(match_record):
    scene = commanding()
    scene.cycle(1000, units=SIX_UNITS[:3])
    scene.chooser.answer("A")

    scene.cycle(1005, units=SIX_UNITS)

    assert scene.commander.standing is None and scene.commander.take("scout", scene.question.actions, None) is None
    assert [(event["kind"], event.get("reason")) for event in commander_events(match_record)] == [
        ("handover", None),
        ("dropped", "not_offered"),
    ]


def test_commander_core_the_last_4_picks_are_kept_the_oldest_dropped_first():
    # A pick is kept against no answer: each of these answers is taken.
    scene = commanding(CommanderTuning(commit_seconds=0, scout_commit=False))
    tick = 1000
    letters = [scene.question.actions[index].id for index in (0, 1, 2, 0, 1)]
    for letter in letters:
        scene.cycle(tick)
        scene.chooser.answer(letter)
        tick += 5
        scene.cycle(tick)
        tick += 5

    assert [pick.id for pick in scene.commander.picks] == letters[1:]


def test_commander_core_the_order_is_due_on_a_new_action_on_idle_and_25_ticks_after_it_was_last_sent():
    scene = commanding()
    world = scene.agent.world
    scene.cycle(1000)
    scene.chooser.answer(scene.question.actions[1].id)
    scene.cycle(1005)
    order = {"cmd": "move", "units": [SIX_UNITS[0]["id"]], "to": [40, 40]}
    moving = tanks(3, 30, 47, unit_type="2tnk", idle=False)

    due = []
    for tick, units in ((1005, moving), (1010, moving), (1029, moving), (1030, moving), (1035, SIX_UNITS[:3])):
        ask(scene.agent, base_state("england", YARD, POWER, BARRACKS, tick=tick, units=units))
        intent = scene.commander.order("scout", order, world)
        due.append(intent is not None and intent.commander and intent.priority == Priority.SCOUT)

    assert due == [True, False, False, True, True]
    assert scene.commander.order("main", order, world) is None and scene.commander.order("scout", None, world) is None


def test_commander_core_off_no_commander_is_made_and_on_it_asks_its_own_chain_with_answers_stale_after_25_ticks(
    monkeypatch,
):
    built: list[str] = []
    made: list[tuple] = []
    monkeypatch.setattr(agent_module, "build_chain", lambda name: built.append(name) or ChainSource([], name=name))
    monkeypatch.setattr(agent_module, "AsyncChooser", lambda source, is_stale: made.append((source.name, is_stale)))

    off = situations.RedAlertAgent(items=situations.tracked_items(), tuning=SCOUTING, settings=RedAlertSettings())
    asked_before = len(made)
    on = situations.RedAlertAgent(
        items=situations.tracked_items(), tuning=SCOUTING, settings=RedAlertSettings(model_commander=True)
    )

    assert off.ctx.commander is None and isinstance(on.ctx.commander, Commander)
    assert built == ["red_alert_commander"] and on.ctx.commander.names["3tnk"] == "heavy tank"
    # The plan's chooser and the commander's, each over a chain of its own.
    (plan_chain, _), (commander_chain, stale) = made[asked_before:]
    assert on.ctx.commander.role == "scout" and (plan_chain, commander_chain) == (
        "red_alert_plan",
        "red_alert_commander",
    )
    assert (stale("1000", "1025"), stale("1000", "1026")) == (False, True)


# -- the commanded scout ---------------------------------------------------------------------------------

TWO_SCOUTS = (situations.unit(300, "e1", 24, 48), situations.unit(301, "e1", 24, 40))
COMMANDER_ON = RedAlertSettings(model_commander=True)


def recorded_batches(monkeypatch) -> list[list]:
    """The intents of every batch the agent packs from now on, as packing gave them."""
    batches: list[list] = []
    real_pack = agent_module.pack

    def recording(intents, world, limits):
        packed = real_pack(intents, world, limits)
        batches.append(list(packed.intents))
        return packed

    monkeypatch.setattr(agent_module, "pack", recording)
    return batches


def commander_orders(batch: list) -> list[dict]:
    """The orders of a batch that went in the commander's slot."""
    return [intent.order for intent in batch if intent.commander]


@dataclasses.dataclass
class ScoutCommand:
    """An agent with the scouting manager and the model commander on over a stub chooser."""

    agent: situations.RedAlertAgent
    chooser: AnsweringChooser

    def cycle(self, tick: int, units=TWO_SCOUTS, **observation) -> list[dict]:
        state = base_state("england", YARD, POWER, BARRACKS, tick=tick, units=list(units), **observation)
        return ask(self.agent, state, situations.context(TWO_STARTS))

    @property
    def commander(self) -> Commander:
        return self.agent.ctx.commander

    def answer_second(self) -> str:
        """Answer the last question with the letter of its second action, the first after hold; that letter."""
        letter = self.chooser.submitted[-1].options[1].id
        self.chooser.answer(letter)
        return letter


def scout_command(tuning: Tuning = SCOUTING) -> ScoutCommand:
    chooser = AnsweringChooser()
    agent = new_agent(ScoutingManager(), tuning=tuning, settings=COMMANDER_ON, commander_chooser=chooser)
    return ScoutCommand(agent, chooser)


def orders_for(orders: list[dict], unit_id: int) -> list[tuple]:
    """The command and cell of each order that names a unit."""
    return [(order["cmd"], order.get("to")) for order in orders if unit_id in order.get("units", ())]


def test_scout_control_the_first_slot_s_scout_is_asked_about_and_a_destination_goes_in_the_commander_slot(
    monkeypatch, match_record
):
    batches = recorded_batches(monkeypatch)
    scene = scout_command()
    scene.cycle(1000)
    second = scene.answer_second()

    scene.cycle(1005)

    request, standing = scene.chooser.submitted[0], scene.commander.standing
    assert request.options[0].description.startswith("hold (24,48)") and standing.id == second
    assert standing.cell in scene.agent.ctx.scout_destinations
    (commanded,) = commander_orders(batches[-1])
    assert (commanded["cmd"], commanded["units"]) == ("move", [300])
    events = match_record()
    played = [event["data"] for event in events if event["kind"] == "commander" and event["data"]["kind"] != "summary"]
    assert [data["action_id"] for data in played] == [second]
    others = [event["data"] for event in events if event["kind"] == "scout_slot" and event["data"]["unit_id"] == 301]
    assert any(data["kind"] == "destination" and data["source"] == "mission" for data in others)


def test_scout_control_with_the_rush_scout_on_the_commander_steers_the_second_slot_and_the_first_makes_its_pass(
    monkeypatch,
):
    batches = recorded_batches(monkeypatch)
    # The pass straight at the enemy's start, not the infantry run's entry leg (see test_scout_run_through).
    scouting = dataclasses.replace(SCOUTING.scouting, rush_scout_direct=True, infantry_scout_run_through=False)
    scene = scout_command(dataclasses.replace(SCOUTING, scouting=scouting))
    scene.cycle(1000)
    scene.answer_second()

    orders = scene.cycle(1005)

    (commanded,) = commander_orders(batches[-1])
    assert commanded["units"] == [301]
    assert ("move", [74, 48]) in orders_for(orders, 300) or scene.agent.ctx.scout_destinations[0] == (74, 48)


def test_scout_control_a_hold_stops_the_scout_and_leaves_it_alone_3_s_then_the_script_steers_it_again(monkeypatch):
    batches = recorded_batches(monkeypatch)
    scene = scout_command()
    scene.cycle(1000)
    scene.chooser.answer("A")

    held = scene.cycle(1005)
    standing_idle = scene.cycle(1010)
    over = scene.cycle(1080)

    assert commander_orders(batches[1]) == [{"cmd": "stop", "units": [300]}]
    assert orders_for(held, 300) == [("stop", None)] and orders_for(standing_idle, 300) == []
    assert [cmd for cmd, _ in orders_for(over, 300)] == ["move"] and commander_orders(batches[-1]) == []


def test_scout_control_a_scout_that_must_flee_flees_and_no_commander_order_goes_out_for_it(monkeypatch):
    batches = recorded_batches(monkeypatch)
    scene = scout_command()
    scene.cycle(1000)
    scene.answer_second()
    scene.cycle(1005)

    fled = scene.cycle(1010, visible_enemies=[enemy(612, "2tnk", 28, 48)])

    (move,) = orders_for(fled, 300)
    assert move[0] == "move" and move[1][0] < 24
    assert commander_orders(batches[-1]) == []


def test_scout_control_after_the_handover_the_scout_returns_to_its_mission_and_no_scout_question_is_asked(
    match_record,
):
    scene = scout_command()
    scene.cycle(1000)
    scene.answer_second()
    scene.cycle(1005)
    asked = len(scene.chooser.submitted)

    scene.cycle(1010, units=[*TWO_SCOUTS, *tanks(6, 30, 47, unit_type="2tnk")])
    scene.cycle(1015, units=[*TWO_SCOUTS, *tanks(6, 30, 47, unit_type="2tnk")])

    events = match_record()
    kinds = [event["data"]["kind"] for event in events if event["kind"] == "commander"]
    kinds.remove("summary")
    first_scout = [
        event["data"] for event in events if event["kind"] == "scout_slot" and event["data"]["unit_id"] == 300
    ]
    assert kinds == ["action", "handover"] and len(scene.chooser.submitted) == asked
    assert first_scout[-1]["kind"] == "destination" and first_scout[-1]["source"] == "mission"


RIFLES = tuple(situations.unit(200 + n, "e1", 20, 48) for n in range(6))
# The first rifle beyond the five of the guard scouts; the handover waits for one combat unit more than they are.
ONE_SCOUT = dataclasses.replace(
    Tuning(),
    scouting=ScoutingTuning(scout_slots=False, rescout=False),
    commander=CommanderTuning(handover_combat_units=7),
)


def test_scout_control_with_the_slots_off_the_mission_s_scout_goes_where_the_model_says_until_it_must_withdraw():
    scene = scout_command(ONE_SCOUT)
    scene.cycle(1000, units=RIFLES)
    request = scene.chooser.submitted[0]
    scene.answer_second()

    sent = scene.cycle(1005, units=RIFLES)
    standing = scene.commander.standing
    there = scene.cycle(1010, units=[*RIFLES[:5], situations.unit(205, "e1", *standing.cell)])
    covered = scene.cycle(1015, units=RIFLES, visible_enemy_buildings=[enemy(600, "pbox", 24, 48)])

    assert request.options[0].description.startswith("hold (20,48)")
    assert orders_for(sent, 205) == [("move", list(standing.cell))] and orders_for(there, 205) == []
    assert orders_for(covered, 205) and orders_for(covered, 205)[0][1] != list(standing.cell)


def test_scout_control_with_the_slots_off_a_hold_keeps_the_mission_s_scout_where_it_is_3_s():
    scene = scout_command(ONE_SCOUT)
    scene.cycle(1000, units=RIFLES)
    scene.chooser.answer("A")

    held = scene.cycle(1005, units=RIFLES)
    after = scene.cycle(1085, units=RIFLES)

    assert orders_for(held, 205) == [("stop", None)]
    assert orders_for(after, 205) == [("move", [74, 48])]


# -- the commanded main ------------------------------------------------------------------------------------

ALLIED_TANKS = tuple(tanks(6, 31, 47, unit_type="2tnk"))


@dataclasses.dataclass
class MainCommand:
    """An agent with the model commander on over a stub chooser and the managers given, and the batches it packs."""

    agent: situations.RedAlertAgent
    chooser: AnsweringChooser
    batches: list[list]

    def cycle(self, tick: int, units=ALLIED_TANKS, buildings=(YARD, POWER, BARRACKS), **observation) -> list[dict]:
        state = base_state("england", *buildings, tick=tick, units=list(units), **observation)
        return ask(self.agent, state, situations.context(TWO_STARTS))

    def answer(self, starting: str) -> None:
        """Answer the last question with the letter of its option whose line starts so."""
        options = self.chooser.submitted[-1].options
        self.chooser.answer(next(option.id for option in options if option.description.startswith(starting)))


def main_command(monkeypatch, *managers, plan: str = "HOLD", tuning: Tuning = ARMY) -> MainCommand:
    batches = recorded_batches(monkeypatch)
    chooser = AnsweringChooser()
    agent = new_agent(
        *managers, tuning=tuning, settings=COMMANDER_ON, commander_chooser=chooser, plan_selector=FixedPlan(plan)
    )
    return MainCommand(agent, chooser, batches)


def test_main_control_before_6_combat_units_the_requests_are_the_scout_s_and_from_the_handover_main_s(
    monkeypatch, match_record
):
    scene = main_command(monkeypatch, ScoutingManager(), CombatManager())
    scene.cycle(1000, units=TWO_SCOUTS)
    scene.chooser.answer(None, TIMEOUT)
    scene.cycle(1005, units=[*TWO_SCOUTS, *ALLIED_TANKS])
    scene.cycle(1010, units=[*TWO_SCOUTS, *ALLIED_TANKS])

    texts = [request.system_text for request in scene.chooser.submitted]
    assert texts == [command_scout.SYSTEM_TEXT, command_army.SYSTEM_TEXT]
    assert [(event["kind"], event["tick"]) for event in commander_events(match_record)] == [
        ("handover", 1005),
        ("gate", 1005),
    ]


def test_main_control_an_army_answer_puts_main_s_order_in_the_reserved_slot_nearest_the_objective_first_when_due(
    monkeypatch, match_record
):
    scene = main_command(monkeypatch, CombatManager(), plan="HOLD")
    building = {"visible_enemy_buildings": [enemy(641, "weap", 72, 52)]}
    scene.cycle(1000, **building)
    scene.answer("advance")

    scene.cycle(1005, **building)
    moving = tanks(6, 33, 47, unit_type="2tnk", idle=False)
    later = [scene.cycle(tick, units=moving, **building) for tick in (1010, 1029, 1030)]

    (order,) = commander_orders(scene.batches[1])
    by_id = {unit["id"]: unit for unit in ALLIED_TANKS}
    distances = [cells.distance((by_id[unit_id]["x"], by_id[unit_id]["y"]), (72, 52)) for unit_id in order["units"]]
    assert (order["cmd"], order["to"]) == ("attack_move", [72, 52]) and sorted(order["units"]) == unit_ids(ALLIED_TANKS)
    assert distances == sorted(distances)
    assert [len(commander_orders(batch)) for batch in scene.batches[2:]] == [0, 0, 1] and len(later) == 3
    assert commander_events(match_record)[-1]["kind"] == "action"


THREAT = {"visible_enemies": [enemy(700, "3tnk", 14, 56)]}
# A heavy tank within 20 cells of the base's center but beyond its alarm radius: an enemy army closing, which offers
# hold and retreat home.
CLOSING = {"visible_enemies": [enemy(701, "3tnk", 40, 40)]}
TURRETS = (("gun", 17, 56), ("gun", 21, 56))
# main stands at the staging point, 10 cells from the base's center: retreat home is offered there only with
# retreat_home_min_cells under 10.
RETREAT_AT_STAGING = dataclasses.replace(ARMY, commander=CommanderTuning(retreat_home_min_cells=8))


@pytest.mark.parametrize("starting", ["hold", "retreat home"])
@pytest.mark.parametrize("turrets, taken", [((), True), (TURRETS, False)], ids=["beyond them", "within them"])
def test_main_control_a_major_threat_beyond_the_guard_and_static_defenses_takes_a_commanded_hold_or_retreat_home(
    monkeypatch, starting, turrets, taken
):
    scene = main_command(monkeypatch, BaseDefenseManager(), CombatManager(), plan="HOLD", tuning=RETREAT_AT_STAGING)
    buildings = (YARD, POWER, BARRACKS, *turrets)
    scene.cycle(1000, buildings=buildings, **CLOSING)
    scene.answer(starting)
    scene.cycle(1005, buildings=buildings, **CLOSING)

    orders = scene.cycle(1010, buildings=buildings, **THREAT)

    main_orders = [order for order in orders if set(order.get("units", ())) & set(unit_ids(ALLIED_TANKS))]
    assert scene.agent.ctx.commander.standing.label == starting and scene.agent.ctx.threat_level == "major"
    assert bool(main_orders) is taken
    assert not taken or {order["cmd"] for order in main_orders} == {"attack_move"}
    assert commander_orders(scene.batches[-1]) == []


# -- a whole match with the commander on ------------------------------------------------------------------


class FirstOptionModel:
    """Stands in for the local model server the commander's chain asks: every answer is the first option offered."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self.transport = httpx.MockTransport(self.answer)

    def answer(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        first = body["response_format"]["json_schema"]["schema"]["properties"]["choice"]["enum"][0]
        self.bodies.append(body)
        content = json.dumps({"choice": first})
        return httpx.Response(
            200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
        )


@dataclasses.dataclass
class CommandedMatch:
    """A whole fake match played with the model commander on, and the stand-in model it asked."""

    played: whole_match.WholeMatch
    model: FirstOptionModel


class AnsweringAtOnce:
    """A chooser that asks its source as the question is submitted and hands the answer over at the next poll,
    recording both as every chooser does.

    The fake game plays far faster than the platform's 25 ticks a second, and the agent's own chooser asks on a
    thread: an answer it waited for would arrive many fake ticks after its question, which the stand-in model,
    answering at once, never does in a real game.
    """

    def __init__(self, source) -> None:
        self._source = source
        self._asked = None

    def submit(self, request) -> bool:
        if self._asked is not None:
            return False
        self._asked = request, self._source.ask(request)
        return True

    def poll(self, current_state_tag: str):
        if self._asked is None:
            return None
        (request, calls), self._asked = self._asked, None
        journal.record(request, calls, self._source.name)
        return journal.result_of(calls, self._source.name)


def _commanded_agent(model: FirstOptionModel, tuning: Tuning, plan_selector=None) -> situations.RedAlertAgent:
    """The agent as it plays a match, with the model commander switched on over the commander chain, built as the
    tracked files say but asking the local role small (the shipped chain asks the systemone role jev9b), its local
    model the stand-in."""
    chain = build_local_chain("red_alert_commander", transport=model.transport)
    return situations.RedAlertAgent(
        items=situations.tracked_items(),
        tuning=tuning,
        chooser=situations.StubChooser(),
        settings=COMMANDER_ON,
        plan_selector=plan_selector,
        commander_chooser=AnsweringAtOnce(chain),
    )


@pytest.fixture(scope="module")
def commanded_match() -> CommandedMatch:
    """One whole 20-minute fake match for the Allied start with the shipped tuning, the model commander on and its
    chain asking the stand-in model."""
    model = FirstOptionModel()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(situations, "own_agent", functools.partial(_commanded_agent, model))
        played = whole_match.play_whole_match(Tuning())
    return CommandedMatch(played, model)


def test_commander_whole_match_runs_without_an_unhandled_error_or_a_refused_batch(commanded_match):
    played = commanded_match.played
    errors = played.data_of("error")

    assert played.agent.ctx.commander is not None and len(played.compute_ms) == whole_match.WHOLE_MATCH_STEPS
    assert [error for error in errors if error["type"] == "unhandled"] == []
    assert played.run.invalid_actions == 0 and played.game.invalid_actions == 0


def test_commander_whole_match_compute_p95_is_at_most_50_ms(commanded_match):
    assert commanded_match.played.compute_p95_ms <= whole_match.COMPUTE_P95_MS


def test_commander_whole_match_writes_actions_for_the_scout_and_for_main_and_one_handover(commanded_match):
    events = commanded_match.played.data_of("commander")
    actions = [event for event in events if event["kind"] == "action"]

    assert [event["role"] for event in events if event["kind"] == "handover"] == ["main"]
    assert {"scout", "main"} <= {event["role"] for event in actions}
    handover = next(index for index, event in enumerate(events) if event["kind"] == "handover")
    assert all(event["role"] == "scout" for event in events[:handover] if event["kind"] == "action")


def test_commander_whole_match_every_request_asks_a_letter_alone_in_16_tokens_and_is_recorded(commanded_match):
    bodies = commanded_match.model.bodies
    # A question with one option is answered by the chain itself and recorded, with no request to the model.
    recorded = commanded_match.played.data_of("decision")
    decisions = [decision for decision in recorded if decision["reason"] != SINGLE_OPTION]

    assert bodies and all(body["max_tokens"] == 16 for body in bodies)
    assert all(list(body["response_format"]["json_schema"]["schema"]["properties"]) == ["choice"] for body in bodies)
    assert {body["messages"][0]["content"] for body in bodies} == {command_scout.SYSTEM_TEXT, command_army.SYSTEM_TEXT}
    assert len(bodies) - 1 <= len(decisions) <= len(bodies)
    caps = {command_scout.SYSTEM_TEXT: 2500, command_army.SYSTEM_TEXT: 6000}
    assert all(
        len(body["messages"][0]["content"]) + len(body["messages"][1]["content"])
        <= caps[body["messages"][0]["content"]]
        for body in bodies
    )


# -- the active options: the threat ------------------------------------------------------------------------


def closing_watch(*sightings: tuple[int, list[dict]]) -> tuple[situations.RedAlertAgent, ClosingWatch]:
    """An agent's world taken through a state at each tick given, showing the enemies given, and a closing watch that
    noted each."""
    agent, watch = new_agent(), ClosingWatch()
    for tick, enemies in sightings:
        state = base_state("england", YARD, POWER, BARRACKS, tick=tick, visible_enemies=enemies)
        ask(agent, state, situations.context(TWO_STARTS))
        watch.note(agent.world)
    return agent, watch


def column(x: int, count: int = 2, unit_type: str = "2tnk", first: int = 600) -> list[dict]:
    """`count` enemies of a type in a column from (x, 46), numbered from `first`."""
    return [enemy(first + n, unit_type, x, 46 + n) for n in range(count)]


@pytest.mark.parametrize(
    "sightings, closing_at",
    [
        (((1000, column(50)), (1375, column(38))), (38, 47)),
        (((1000, column(36)), (1375, column(38))), None),
        (((1375, column(38)),), (38, 47)),
        (((1000, column(30)), (1400, column(50)), (1775, column(38))), (38, 47)),
        (((1375, column(38, count=1)),), None),
        (((1375, column(38, count=10, unit_type="e1")),), (38, 51)),
        (((1375, column(38, count=9, unit_type="e1")),), None),
        (((1375, column(43)),), None),
        (((1000, column(38)), (1250, [])), (38, 47)),
        (((1000, column(38)), (1251, [])), None),
    ],
    ids=[
        "closer than 15 s before",
        "farther than 15 s before",
        "no earlier sighting",
        "the latest sample 15 s old is compared",
        "worth 850",
        "worth exactly 1000",
        "worth 900",
        "21 cells from the base",
        "seen exactly 10 s ago",
        "seen 251 ticks ago",
    ],
)
def test_active_options_an_enemy_army_closes_worth_1000_within_20_cells_seen_10_s_and_nearer_than_15_s_before(
    sightings, closing_at
):
    agent, watch = closing_watch(*sightings)

    assert agent.world.base_center == (22, 48)
    assert watch.closing(agent.world) == closing_at


LOSING = 0.9
EVEN = 1.0


@pytest.mark.parametrize(
    "attacked, sightings, found, line",
    [
        (True, ((1375, column(38)),), LOSING, "threat: base attacked"),
        (False, ((1375, column(38)),), LOSING, "threat: enemy army closing at (38,47)"),
        (False, ((1375, column(50)),), LOSING, "threat: losing here"),
        (False, ((1375, column(50)),), EVEN, "threat: none"),
        (False, (), None, "threat: none"),
    ],
    ids=["base attacked first", "then an army closing", "then a fight under 1.0", "a fight at 1.0", "nothing"],
)
def test_active_options_the_threat_is_the_first_of_base_attacked_army_closing_and_losing_here_or_none(
    attacked, sightings, found, line
):
    agent, watch = closing_watch(*sightings) if sightings else closing_watch((1375, []))

    found_threat = command_threat.threat(agent.world, attacked, watch, found)

    assert found_threat.line == line and found_threat.is_threatened is (line != "threat: none")


def test_active_options_the_commander_notes_its_closing_watch_every_cycle():
    scene = commanding()
    world, ctx = scene.agent.world, scene.agent.ctx
    for tick, x in ((1000, 36), (1375, 38)):
        state = base_state("england", YARD, POWER, BARRACKS, tick=tick, units=SIX_UNITS[:3], visible_enemies=column(x))
        ask(scene.agent, state)
        scene.commander.begin_cycle(world, ctx)

    assert scene.commander.closing.closing(world) is None and ClosingWatch().closing(world) == (38, 47)


ARMY_SYSTEM_TEXT = (
    "You command our main army in Red Alert 1v1. Each turn pick ONE action ID. Keep the army busy when we are even or "
    "winning: attack, siege, advance, flank or raid. Our opponent is another AI agent in a tournament; a 20-minute "
    "match not won outright goes to the tiebreak (value traded). Doctrine: economy and map control early; never "
    "attack into defenses we cannot beat; retreat and regroup when outnumbered; tanks crush infantry and kill tanks; "
    "artillery fires from behind a screen; punish exposed expansions and harvesters; keep a lead late. Learn from "
    'recent picks: repeat what worked, change what lost value. Reply {"choice":"ID"}.'
)
FIVE_LIGHT_TANKS = [enemy(610 + n, "1tnk", 52, 46 + n) for n in range(5)]


@pytest.mark.parametrize(
    "units, threat_active, observation, line",
    [
        (tanks(6, 40, 47), False, {}, "threat: none"),
        (tanks(6, 40, 47), True, {}, "threat: base attacked"),
        (tanks(6, 50, 20), False, {"visible_enemies": column(36)}, "threat: enemy army closing at (36,47)"),
        (tanks(2, 40, 47, unit_type="1tnk"), False, {"visible_enemies": FIVE_LIGHT_TANKS}, "threat: losing here"),
    ],
    ids=["none", "base attacked", "an army closing", "losing here"],
)
def test_active_options_the_army_prompt_says_the_threat_after_fight_here_under_the_army_system_text(
    units, threat_active, observation, line
):
    built = army_scene(units, threat_active=threat_active, **observation)
    lines = built.lines

    assert built.question.request.system_text == command_army.SYSTEM_TEXT == ARMY_SYSTEM_TEXT
    targets = next(index for index, text in enumerate(lines) if text.startswith("targets:"))
    assert line in lines and lines[targets - 1].startswith("fight_here:")
    assert all(text.startswith("T") for text in lines[targets + 1 : lines.index(line)])
    assert prompt_chars(built.question.request) <= 6000


DEFENSIVE = ["pull_back", "retreat_home", "hold"]


def test_active_options_with_no_threat_no_army_closing_and_a_fight_at_2_0_no_defensive_action_and_2_active_ones():
    built = army_scene(tanks(8), visible_enemies=FRONT["visible_enemies"])

    assert {"fight_here: we are 2.0x", "threat: none"} <= set(built.lines)
    # A hit would take all eight tanks, leaving no body to hold the fight: none is offered.
    assert built.kinds() == ["gather", "attack", "advance"]


def test_active_options_with_nothing_seen_the_flank_makes_the_second_active_action_beside_the_advance():
    built = army_scene(tanks(6, 30, 47), tick=3000)

    assert built.kinds() == ["gather", "flank", "advance"]
    assert built.action("flank").cell == built.action("advance").cell


@pytest.mark.parametrize(
    "units, threat_active, observation, active",
    [
        (tanks(6, 40, 47), True, {}, ["flank", "advance"]),
        (tanks(6, 50, 20), False, {"visible_enemies": column(36)}, ["flank", "advance"]),
        (tanks(3, 40, 47, unit_type="1tnk"), False, {"visible_enemies": FIVE_LIGHT_TANKS}, ["attack", "advance"]),
    ],
    ids=["base attacked", "an army closing", "a fight at 0.6"],
)
def test_active_options_under_each_threat_pull_back_retreat_home_and_hold_come_first_then_the_active_ones(
    units, threat_active, observation, active
):
    built = army_scene(units, threat_active=threat_active, **observation)

    assert built.kinds() == [*DEFENSIVE, "gather", *active]
    assert [action.id for action in built.question.actions] == [
        "A",
        "B",
        "C",
        "D",
        *[ARMY_IDS[kind] for kind in active],
    ]


def asked_with(scene: ArmyScene, options_max: int) -> list[str]:
    """The kinds of the actions of main's question asked again in the scene's cycle with options_max given."""
    world, ctx = scene.agent.world, scene.agent.ctx
    ctx.tuning = dataclasses.replace(ctx.tuning, commander=CommanderTuning(options_max=options_max))
    main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
    asked = command_army.question(main, (), NAMES, command_army.EnemyPeak(), world, ctx)
    return [action.kind for action in asked.actions]


@pytest.mark.parametrize(
    "options_max, kinds",
    [
        (6, [*DEFENSIVE, "gather", "attack", "advance"]),
        (4, ["pull_back", "retreat_home", "gather", "advance"]),
        (3, ["pull_back", "gather", "advance"]),
        (2, ["gather", "advance"]),
    ],
)
def test_active_options_options_max_leaves_2_places_to_gather_and_the_active_actions_the_advance_among_them(
    options_max, kinds
):
    scene = army_scene(MIXED_ARMY, threat_active=True, **FRONT)

    assert asked_with(scene, options_max) == kinds


def defensive_kinds(scene: ArmyScene, min_cells: int) -> tuple[int, list[str]]:
    """How far main's centroid stands from the base's center, its home, and the kinds of main's actions asked again in
    the scene's cycle with retreat_home_min_cells given."""
    world, ctx = scene.agent.world, scene.agent.ctx
    ctx.tuning = dataclasses.replace(ctx.tuning, commander=CommanderTuning(retreat_home_min_cells=min_cells))
    main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
    found = command_army.brief(main, NAMES, command_army.EnemyPeak(), world, ctx)
    actions = command_army.army_actions(found, True, NAMES, world, ctx)
    return round(cells.distance(found.centroid, world.base_center)), [action.kind for action in actions]


# main 3 cells beside the base's center at (22,48), more than 8 cells from the staging point at (32,48), where the pull
# back applies; and 20 cells out toward the enemy, beyond the 14 cells of retreat_home_min_cells.
@pytest.mark.parametrize(
    "units, distance, kinds",
    [(tanks(3, 21, 51), 3, ["pull_back", "hold"]), (tanks(3, 41, 48), 20, DEFENSIVE)],
    ids=["3 cells from home", "20 cells from home"],
)
def test_retreat_home_away_with_the_base_attacked_retreat_home_is_offered_only_more_than_14_cells_from_home(
    units, distance, kinds
):
    built = army_scene(units, threat_active=True)
    asked = defensive_kinds(built, CommanderTuning().retreat_home_min_cells)

    assert asked == (distance, [*kinds, "gather", "flank", "advance"])
    assert built.kinds()[: len(kinds)] == kinds


@pytest.mark.parametrize(
    "x, min_cells, offered",
    [(34, 14, False), (35, 14, False), (36, 14, True), (41, 20, False), (41, 19, True)],
    ids=["13 cells of 14", "14 cells of 14", "15 cells of 14", "20 cells of 20", "20 cells of 19"],
)
def test_retreat_home_away_is_never_offered_within_retreat_home_min_cells_of_home(x, min_cells, offered):
    distance, kinds = defensive_kinds(army_scene(tanks(3, x, 48), threat_active=True), min_cells)

    assert distance == x + 1 - 22
    assert ("retreat_home" in kinds) is offered and "hold" in kinds


# Two harvesters seen 4 s before the army scenes' tick at the ore field far to the south, with no guard; and an older
# opportunity at the field by the enemy's start.
FAR_TRUCKS = ExposedTrucks(
    field=(60, 80),
    harvester_count=2,
    harvester_ids=(650, 651),
    guard_value=0,
    seen_tick=12400,
    nearest_harvester=(58, 78),
)
OLDER_TRUCKS = dataclasses.replace(FAR_TRUCKS, field=(40, 14), harvester_ids=(652,), seen_tick=12000)


def test_active_options_the_freshest_exposed_trucks_are_offered_as_raid_trucks_first_of_the_active_actions():
    built = army_scene(tanks(8), visible_enemies=FRONT["visible_enemies"], exposed_trucks=(FAR_TRUCKS, OLDER_TRUCKS))
    raid = built.action("raid_trucks")

    assert built.kinds() == ["gather", "raid_trucks", "attack", "advance"]
    assert (raid.id, raid.head, raid.label) == (
        "G",
        "raid 2 trucks at (58,78), guard 0, seen 4s ago",
        "raid trucks (58,78)",
    )
    assert (raid.key, raid.cell) == (("raid_trucks", (60, 80)), (58, 78))


@pytest.mark.parametrize(
    "hp, exposed, offered",
    [(50, (FAR_TRUCKS,), True), (49, (FAR_TRUCKS,), False), (100, (), False)],
    ids=["units at 50%", "units at 49%", "no opportunity"],
)
def test_active_options_raid_trucks_is_offered_while_an_opportunity_is_noted_and_a_unit_of_main_is_at_half_health(
    hp, exposed, offered
):
    built = army_scene(tanks(6, 40, 47, hp=hp), exposed_trucks=exposed)

    assert ("raid_trucks" in built.kinds()) is offered and "advance" in built.kinds()


# -- falling behind: nothing far from home while main is worth under half the enemy estimate ----------------------

# Worth 4,150: three heavy tanks and a light tank, their centroid about 25 cells out from the base's center at (22,48).
BEHIND_MAIN = [*tanks(3, 46, 47), situations.unit(403, "1tnk", 46, 48)]
# Two enemy light tanks 30 cells out from the base's center, 5 cells beyond main: 4,150 against 1,400.
FAR_PAIR = [enemy(610, "1tnk", 52, 48), enemy(611, "1tnk", 52, 48)]


def peak_of(value: int) -> command_army.EnemyPeak:
    """The enemy army seen worth `value` at its peak, 20 s before the army scenes' tick."""
    return command_army.EnemyPeak(value, 12000)


@pytest.mark.parametrize(
    "peak, is_behind",
    [(13450, True), (8302, True), (8300, False)],
    ids=["4,150 against 13,450", "4,150 just under half of 8,302", "4,150 half of 8,300"],
)
def test_retreat_sooner_main_under_half_the_enemy_estimate_is_offered_no_attack_30_cells_out_but_retreat_and_gather(
    peak, is_behind
):
    built = army_scene(BEHIND_MAIN, threat_active=True, peak=peak_of(peak), visible_enemies=FAR_PAIR)
    # The first word of each option line of the prompt.
    verbs = {option.description.split()[0] for option in built.question.request.options}

    assert line_of(built.lines, "main: ").startswith("main: 4 units (") and " strength 4150, " in line_of(
        built.lines, "main: "
    )
    assert line_of(built.lines, "enemy_total_est:").startswith(f"enemy_total_est: {peak} (seen peak 20s ago), ")
    assert {"retreat_home", "gather"} <= set(built.kinds()) and {"retreat", "gather"} <= verbs
    assert ("attack" in verbs) is not is_behind
    assert ("attack" in built.kinds()) is not is_behind and ("advance" in built.kinds()) is not is_behind


def test_retreat_sooner_the_attack_offered_at_half_the_estimate_is_on_the_pair_30_cells_from_the_base_s_center():
    built = army_scene(BEHIND_MAIN, threat_active=True, peak=peak_of(8300), visible_enemies=FAR_PAIR)
    world = built.agent.world

    assert built.action("attack").cell == (52, 48) and cells.distance(world.base_center, (52, 48)) == 30


@pytest.mark.parametrize("x, offered", [(37, True), (38, False)], ids=["15 cells from home", "16 cells from home"])
def test_retreat_sooner_while_main_is_behind_an_attack_within_15_cells_of_the_base_s_center_is_still_offered(
    x, offered
):
    near_home = [*tanks(3, 30, 47), situations.unit(403, "1tnk", 30, 48)]
    built = army_scene(near_home, peak=peak_of(13450), visible_enemies=[enemy(610, "1tnk", x, 48)])

    assert ("attack" in built.kinds()) is offered and "advance" not in built.kinds() and "gather" in built.kinds()


@pytest.mark.parametrize("peak, attack", [(13450, False), (8300, True)], ids=["behind", "at half the estimate"])
def test_retreat_sooner_while_the_gate_is_closed_the_near_attack_30_cells_out_is_withheld_too(peak, attack):
    scene = army_scene(BEHIND_MAIN, visible_enemies=FAR_PAIR)
    world, ctx = scene.agent.world, scene.agent.ctx
    main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
    found = command_army.brief(main, NAMES, peak_of(peak), world, ctx)

    kinds = [action.kind for action in command_army.army_actions(found, False, NAMES, world, ctx)]

    assert kinds[0] == "gather" and ("attack" in kinds) is attack


def test_retreat_sooner_an_assumed_estimate_never_puts_main_behind():
    # At 40% main is worth 1,660, under half the assumed 4,150, the full cost of our own army.
    damaged = [*tanks(3, 46, 47, hp=40), situations.unit(403, "1tnk", 46, 48, hp=40)]
    built = army_scene(damaged, visible_enemies=FAR_PAIR)

    assert line_of(built.lines, "enemy_total_est:").startswith("enemy_total_est: 4150 (assumed), unplaced ")
    assert {"attack", "advance"} <= set(built.kinds())


# -- the active options: the raid detachment ------------------------------------------------------------------

HARVESTERS = [enemy(650, "harv", 58, 78), enemy(651, "harv", 60, 80)]
FRESH_TRUCKS = dataclasses.replace(FAR_TRUCKS, seen_tick=1000)
# Six medium tanks, two light tanks and a light tank at 40%: the light tanks are the fastest, the damaged one unfit.
RAIDING_MAIN = [
    *ALLIED_TANKS,
    *tanks(2, 31, 49, first=500, unit_type="1tnk"),
    situations.unit(502, "1tnk", 33, 49, hp=40),
]
RAIDERS = [400, 401, 402, 403, 500, 501]


def raid_command(monkeypatch, units=RAIDING_MAIN, *, standing: str | None = None) -> MainCommand:
    """main commanded with the raid opportunity of FRESH_TRUCKS noted and its harvesters in sight, its standing action
    the one whose line starts with `standing` when given, and the raid trucks action the answer of the last question."""
    scene = main_command(monkeypatch, CombatManager(), plan="HOLD")
    scene.agent.ctx.exposed_trucks = (FRESH_TRUCKS,)
    scene.cycle(990, units=units, visible_enemies=HARVESTERS)
    if standing is not None:
        scene.answer(standing)
        scene.cycle(995, units=units, visible_enemies=HARVESTERS)
    scene.cycle(1000, units=units, visible_enemies=HARVESTERS)
    scene.answer("raid")
    return scene


def test_active_options_choosing_raid_trucks_sends_the_6_fastest_fit_units_by_attack_move_to_the_harvester_cell(
    monkeypatch, match_record
):
    scene = raid_command(monkeypatch)

    orders = scene.cycle(1005, units=RAIDING_MAIN, visible_enemies=HARVESTERS)

    assert [(order["cmd"], sorted(order["units"]), order["to"]) for order in orders] == [
        ("attack_move", RAIDERS, [58, 78])
    ]
    assert scene.agent.ctx.army == set(RAIDERS) and scene.agent.ctx.commander.standing.kind == "gather"
    assert commander_events(match_record)[-1] == {
        "kind": "raid",
        "units": RAIDERS,
        "field": [60, 80],
        "target": [58, 78],
        "tick": 1005,
    }


def test_active_options_with_fewer_than_6_fit_units_the_raid_takes_them_all(monkeypatch):
    damaged = [situations.unit(unit["id"], "2tnk", unit["x"], unit["y"], hp=40) for unit in ALLIED_TANKS[2:]]
    # Four damaged tanks more keep main worth the 4,000 the forward gate opens at.
    more = [situations.unit(510 + n, "2tnk", 31 + n % 3, 49 + n // 3, hp=40) for n in range(4)]
    units = [*ALLIED_TANKS[:2], *damaged, *more]
    scene = raid_command(monkeypatch, units)

    orders = scene.cycle(1005, units=units, visible_enemies=HARVESTERS)

    (raid,) = [order for order in orders if order["to"] == [58, 78]]
    assert sorted(raid["units"]) == [400, 401]


def test_active_options_the_rest_of_main_keeps_the_standing_action_while_the_detachment_raids(monkeypatch):
    scene = raid_command(monkeypatch, standing="advance")

    orders = scene.cycle(1005, units=RAIDING_MAIN, visible_enemies=HARVESTERS)

    by_target = {tuple(order["to"]): set(order["units"]) for order in orders}
    assert scene.agent.ctx.commander.standing.kind == "advance"
    assert by_target[(58, 78)] == set(RAIDERS) and by_target[(74, 48)] == {404, 405, 502}


def wreck(enemy_id: int) -> dict:
    """The wreck of an enemy harvester, which the world forgets it by."""
    return {**enemy(enemy_id, "harv", 58, 78), "wreck": True}


AT_THE_TRUCKS = [
    *[situations.unit(unit_id, "2tnk", 57, 77) for unit_id in RAIDERS[:4]],
    *[situations.unit(unit_id, "1tnk", 57, 77) for unit_id in RAIDERS[4:]],
    *[unit for unit in RAIDING_MAIN if unit["id"] not in RAIDERS],
]
THE_REST = [unit for unit in RAIDING_MAIN if unit["id"] not in RAIDERS]


@pytest.mark.parametrize(
    "tick, units, visible, exposed, reason",
    [
        (1010, RAIDING_MAIN, HARVESTERS, (FRESH_TRUCKS,), None),
        (1010, RAIDING_MAIN, [wreck(650), wreck(651)], (FRESH_TRUCKS,), "destroyed"),
        (1010, RAIDING_MAIN, [wreck(650)], (FRESH_TRUCKS,), None),
        (1010, AT_THE_TRUCKS, [], (FRESH_TRUCKS,), "gone"),
        (1010, RAIDING_MAIN, [], (FRESH_TRUCKS,), None),
        (1010, RAIDING_MAIN, HARVESTERS, (), "expired"),
        (2504, RAIDING_MAIN, HARVESTERS, (FRESH_TRUCKS,), None),
        (2505, RAIDING_MAIN, HARVESTERS, (FRESH_TRUCKS,), "timed_out"),
        (1010, THE_REST, HARVESTERS, (FRESH_TRUCKS,), "no_units"),
    ],
    ids=[
        "raiding on",
        "both harvesters destroyed",
        "one harvester left",
        "harvesters gone from the target",
        "not there yet, harvesters out of sight",
        "the opportunity expired",
        "59.96 s after the pick",
        "60 s after the pick",
        "no raider left",
    ],
)
def test_active_options_the_detachment_rejoins_main_when_its_harvesters_are_destroyed_or_gone_expired_or_after_60_s(
    monkeypatch, match_record, tick, units, visible, exposed, reason
):
    scene = raid_command(monkeypatch)
    scene.cycle(1005, units=RAIDING_MAIN, visible_enemies=HARVESTERS)
    scene.agent.ctx.exposed_trucks = exposed

    orders = scene.cycle(tick, units=units, visible_enemies=visible)

    rejoined = [event["reason"] for event in commander_events(match_record) if event["kind"] == "rejoined"]
    raiding = [order for order in orders if order.get("to") == [58, 78]]
    assert rejoined == ([] if reason is None else [reason])
    assert bool(raiding) is (reason is None) and bool(scene.agent.ctx.army & set(RAIDERS)) is (reason is None)


# -- fight_here: the enemy defenses counted ----------------------------------------------------------------------

TWO_MEDIUM_TANKS = [enemy(610 + n, "2tnk", 52, 46 + n) for n in range(2)]


def fight_line(scene: ArmyScene) -> str:
    (line,) = [line for line in scene.lines if line.startswith("fight_here:")]
    return line


def main_view(scene: ArmyScene) -> command_army.ArmyView:
    world, ctx = scene.agent.world, scene.agent.ctx
    return command_army.army_view([world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)])


def test_fight_here_our_4_tanks_next_to_a_pillbox_show_a_ratio_below_that_without_it():
    pillbox = [enemy(600, "pbox", 50, 46)]
    without = army_scene(tanks(4), visible_enemies=TWO_MEDIUM_TANKS)
    beside = army_scene(tanks(4), visible_enemies=TWO_MEDIUM_TANKS, visible_enemy_buildings=pillbox)

    # 4 heavy tanks (4,600) against 2 medium tanks (1,700), and the pillbox at twice its cost (1,200).
    assert (fight_line(without), fight_line(beside)) == ("fight_here: we are 2.7x", "fight_here: we are 1.6x")


@pytest.mark.parametrize(
    "buildings, line, threat_line",
    [
        ([], "fight_here: no enemy", "threat: none"),
        ([enemy(600, "agun", 48, 46)], "fight_here: no enemy", "threat: none"),
        ([enemy(600, "pbox", 50, 46)], "fight_here: we are 1.9x", "threat: none"),
        (
            [enemy(600 + n, "pbox", 50, 45 + n) for n in range(3)],
            "fight_here: we are 0.6x",
            "threat: losing here",
        ),
    ],
    ids=["nothing near", "an AA gun alone", "a pillbox alone", "three pillboxes"],
)
def test_fight_here_reads_no_enemy_without_one_counts_a_ground_defense_alone_and_losing_here_reads_it(
    buildings, line, threat_line
):
    # Two heavy tanks (2,300) against pillboxes each counted at twice its cost (1,200).
    built = army_scene(tanks(2, 45, 46), visible_enemy_buildings=buildings)

    assert fight_line(built) == line and threat_line in built.lines
    assert "99.0x" not in fight_line(built)


@pytest.mark.parametrize(
    "pillbox_x, next_leg, counted",
    [(53, None, True), (54, None, False), (60, (54, 47), True), (60, (51, 47), False)],
    ids=["8 cells from the centre", "9 cells from the centre", "6 cells from the next leg", "9 cells from the leg"],
)
def test_fight_here_counts_a_ground_defense_whose_range_and_2_cells_reach_the_center_or_the_next_leg(
    pillbox_x, next_leg, counted
):
    scene = army_scene(tanks(1, 45, 47), visible_enemy_buildings=[enemy(600, "pbox", pillbox_x, 47)])
    view = main_view(scene)

    ratio = command_army.fight_here(view, next_leg, scene.agent.world, scene.agent.ctx)

    assert view.center == (45, 47)
    assert ratio == (pytest.approx(1150 / 1200) if counted else None)


# -- the objective: uncovered buildings first ---------------------------------------------------------------------

# A war factory with a pillbox beside it, and a construction yard farther off with nothing near.
COVERED_FACTORY = [enemy(641, "weap", 60, 47), enemy(600, "pbox", 62, 47)]
OPEN_YARD = enemy(640, "fact", 74, 48)


@pytest.mark.parametrize(
    "buildings, objective",
    [
        ([*COVERED_FACTORY, OPEN_YARD], "objective: construction yard (74,48), "),
        ([*COVERED_FACTORY, OPEN_YARD, enemy(601, "pbox", 72, 48)], "objective: war factory (60,47), "),
        ([enemy(642, "powr", 50, 47), enemy(641, "weap", 72, 52)], "objective: war factory (72,52), "),
        ([enemy(642, "powr", 56, 47), enemy(643, "powr", 50, 47)], "objective: power plant (50,47), "),
        ([enemy(641, "weap", 60, 47), enemy(600, "agun", 61, 47), OPEN_YARD], "objective: war factory (60,47), "),
    ],
    ids=[
        "a covered factory and an open yard",
        "both covered",
        "production first among the open",
        "the nearest among the open",
        "an AA gun covers nothing",
    ],
)
def test_objective_uncovered_buildings_come_first_production_then_nearest_else_the_stateless_choice(
    buildings, objective
):
    lines = army_scene(tanks(6, 40, 47), visible_enemy_buildings=buildings).lines

    assert any(line.startswith(objective) for line in lines)


def test_objective_uncovered_the_direct_path_is_judged_by_ground_defenses_alone():
    aa_gun = army_scene(tanks(6, 40, 47), visible_enemy_buildings=[enemy(600, "agun", 55, 48), OPEN_YARD])
    pillbox = army_scene(tanks(6, 40, 47), visible_enemy_buildings=[enemy(600, "pbox", 55, 48), OPEN_YARD])

    assert "objective: construction yard (74,48), 33 cells; direct path clear" in aa_gun.lines
    assert "objective: construction yard (74,48), 33 cells; direct path in pillbox range" in pillbox.lines


# -- advance on a defended path ------------------------------------------------------------------------------------

# A pillbox in range of main's straight way to a war factory, and two ore fields south of the way: the graph of
# waypoints goes round by them.
ROUTED_BUILDINGS = [enemy(600, "pbox", 56, 48), enemy(641, "weap", 72, 52)]
ROUTE_FIELDS = [(44, 64), (60, 64)]
ROUTING_ON = dataclasses.replace(ARMY, routing=RoutingTuning(waypoints=True))
ARTILLERY = [situations.unit(430 + n, "arty", 38, 48 + n) for n in range(2)]


def routed_scene(units: list[dict], tuning: Tuning) -> ArmyScene:
    """main's question with the pillbox on its straight way to the war factory, on the fake map with the two fields."""
    game_config = whole_match.with_ore(FakeRedAlert().game_config, ROUTE_FIELDS)
    return army_scene(units, tuning=tuning, game_config=game_config, visible_enemy_buildings=ROUTED_BUILDINGS)


def test_advance_route_with_waypoints_on_advance_goes_via_the_route_s_first_leg_and_main_moves_to_it(match_record):
    scene = routed_scene(tanks(6, 40, 47), ROUTING_ON)
    advance = scene.action("advance")
    commander = Commander(situations.StubChooser(), CommanderTuning())
    commander.role, commander.standing = "main", advance
    scene.agent.ctx.commander = commander
    state = {**scene.state, "state_version": 12505}
    state["observation"] = {**scene.state["observation"], "tick": 12505}

    orders = ask(scene.agent, state)

    assert (advance.via, advance.route.legs, advance.cell) == ((44, 64), ((44, 64), (60, 64), (72, 52)), (72, 52))
    assert advance.head == "advance via (44,64) to war factory (72,52), 49 cells"
    assert [(order["cmd"], order["to"]) for order in orders] == [("move", [44, 64])]
    (written,) = match_record("route")
    assert (written["user"], written["goal"]) == ("advance", [72, 52])


def test_advance_route_main_marches_on_the_objective_once_past_the_cells_of_the_route():
    scene = routed_scene(tanks(6, 40, 47), ROUTING_ON)
    scene.combat.command(scene.action("advance"))

    commanded_orders(scene, "advance", tick=12505, units=tanks(6, 43, 63, idle=False))
    at_last_leg = commanded_orders(scene, "advance", tick=12510, units=tanks(6, 59, 63, idle=False))

    assert [(order["cmd"], order["to"]) for order in at_last_leg] == [("attack_move", [72, 52])]


@pytest.mark.parametrize(
    "units, advance_offered",
    [(tanks(6, 40, 47), False), ([*tanks(4, 40, 47), *ARTILLERY], True)],
    ids=["no artillery", "artillery in main"],
)
def test_advance_route_with_waypoints_off_advance_is_left_out_without_artillery_while_flank_is_offered(
    units, advance_offered
):
    scene = routed_scene(units, ARMY)

    assert "flank" in scene.kinds() and ("advance" in scene.kinds()) is advance_offered
    assert not advance_offered or scene.action("advance").head.endswith("path in pillbox range")


def test_advance_route_advance_stays_on_a_defended_path_when_no_other_active_action_is_offered():
    # main stands on the war factory: there is no side to flank by, nothing to attack and nothing to siege with.
    scene = army_scene(
        tanks(6, 71, 51), visible_enemy_buildings=[enemy(600, "pbox", 74, 52), enemy(641, "weap", 72, 52)]
    )

    assert scene.kinds() == ["gather", "advance"] and scene.action("advance").head.endswith("path in pillbox range")


# -- the forward gate ----------------------------------------------------------------------------------------------

# The first word of each kind's line in main's question.
VERBS = {
    "pull": "pull_back",
    "retreat": "retreat_home",
    "hold:": "hold",
    "gather": "gather",
    "siege": "siege",
    "flank": "flank",
    "raid": "raid_trucks",
    "attack": "attack",
    "advance": "advance",
    "run": "run_over",
    "kill": "kill_defense",
    "hit": "hit_group",
}
FORWARD = {"advance", "flank", "siege", "raid_trucks"}


@dataclasses.dataclass
class GatedMain:
    """An agent with the combat manager alone and a commander over a stub chooser that commands main from the start."""

    agent: situations.RedAlertAgent
    chooser: AnsweringChooser

    @property
    def commander(self) -> Commander:
        return self.agent.ctx.commander

    def cycle(self, tick: int, units: list[dict], **observation) -> list[dict]:
        state = base_state("russia", YARD, POWER, ("barr", 18, 44), tick=tick, units=units, **observation)
        return ask(self.agent, state, situations.context(TWO_STARTS))

    def offered(self) -> list[str]:
        """The kinds of the actions of the last question asked."""
        return [VERBS[option.description.split()[0]] for option in self.chooser.submitted[-1].options]

    def answer(self, kind: str) -> None:
        """Answer the last question with the letter of its action of a kind."""
        options = self.chooser.submitted[-1].options
        self.chooser.answer(next(option.id for option in options if VERBS[option.description.split()[0]] == kind))


# The stub chooser answers only when a test says: no script fallback for its silence but in the tests of the fallback.
SILENT_STUB = CommanderTuning(script_fallback=False)


def gated_main(
    *,
    peak: command_army.EnemyPeak | None = None,
    exposed_trucks=(),
    tuning: Tuning = ARMY,
    commanding: CommanderTuning | None = None,
) -> GatedMain:
    chooser = AnsweringChooser()
    agent = new_agent(CombatManager(), tuning=tuning, plan_selector=FixedPlan("PRESS"))
    commander = Commander(chooser, commanding or SILENT_STUB, NAMES)
    commander.role = "main"
    commander.peak = peak or command_army.EnemyPeak()
    agent.ctx.commander = commander
    agent.ctx.exposed_trucks = exposed_trucks
    return GatedMain(agent, chooser)


def unit(unit_id: int, unit_type: str, x: int, y: int = 47, **fields) -> dict:
    return situations.unit(unit_id, unit_type, x, y, **fields)


def five_units(near: list[str], far: str, first: int) -> list[dict]:
    """Four units of the types given in a block from (40, 46), and one 13 cells east of it, numbered from `first`:
    main's centroid is (43.2, 46.4), the four within 6 cells of it and the one 9.8 cells off, a body share of 0.8."""
    block = [unit(first + n, unit_type, 40 + n % 3, 46 + n // 3) for n, unit_type in enumerate(near)]
    return [*block, unit(first + 4, far, 53)]


# Worth 5,000: three heavy tanks and a medium tank in main's body, and a light tank away from it.
FIVE_THOUSAND = five_units(["3tnk", "3tnk", "3tnk", "2tnk"], "1tnk", 430)
# Worth 4,000: three medium tanks and a heavy tank in main's body, and a rocket soldier away from it.
FOUR_THOUSAND = five_units(["2tnk", "2tnk", "2tnk", "3tnk"], "e3", 440)
# Worth 3,000 in one block: a V2, a heavy tank, a light tank at half health and two rocket soldiers.
THREE_THOUSAND = [
    unit(400, "v2rl", 40),
    unit(401, "3tnk", 41),
    unit(402, "1tnk", 42, hp=50),
    unit(403, "e3", 41, 48),
    unit(404, "e3", 40, 48),
]
# Worth 9,200 at a body share of 0.5: four heavy tanks at (40, 47) and two 12 cells either side of them.
HALF_GATHERED = [
    *[unit(400 + n, "3tnk", 40, 47) for n in range(4)],
    *[unit(410 + n, "3tnk", 28, 47) for n in range(2)],
    *[unit(412 + n, "3tnk", 52, 47) for n in range(2)],
]
EXPOSED = (dataclasses.replace(FAR_TRUCKS, seen_tick=1000),)


@pytest.mark.parametrize("units", [THREE_THOUSAND, HALF_GATHERED], ids=["value 3,000", "body share 0.5"])
def test_forward_gate_closed_main_is_offered_gather_and_no_advance_flank_siege_or_raid_trucks(units):
    scene = gated_main(exposed_trucks=EXPOSED)

    scene.cycle(1000, units, visible_enemy_buildings=[enemy(600, "pbox", 56, 48), enemy(641, "weap", 72, 52)])

    assert scene.commander.gate.is_open is False
    assert "gather" in scene.offered() and not FORWARD & set(scene.offered())


@pytest.mark.parametrize("distance, offered", [(8, True), (10, True), (11, False), (15, False)])
def test_forward_gate_closed_an_attack_is_offered_only_on_a_group_within_10_cells_of_main_s_centroid(distance, offered):
    scene = gated_main()

    scene.cycle(1000, THREE_THOUSAND, visible_enemies=[enemy(610, "e1", 41 + distance, 47)])

    assert ("attack" in scene.offered()) is offered and "gather" in scene.offered()


def test_forward_gate_closed_under_a_threat_the_defensive_options_the_near_attack_and_gather_are_offered():
    scene = gated_main()

    scene.cycle(1000, THREE_THOUSAND, visible_enemies=[enemy(610 + n, "1tnk", 49, 47 + n) for n in range(5)])

    assert scene.offered() == ["pull_back", "retreat_home", "hold", "gather", "attack"]


@pytest.mark.parametrize(
    "units, peak, gate",
    [
        (FIVE_THOUSAND, command_army.EnemyPeak(5000, 900), (True, 5000, 0.8, 5000, False)),
        (FOUR_THOUSAND, None, (True, 4000, 0.8, 4000, True)),
    ],
    ids=["5,000 against 5,000", "4,000 against an assumed estimate"],
)
def test_forward_gate_opens_on_value_body_share_and_the_enemy_estimate_with_a_gate_event_and_advance_offered(
    match_record, units, peak, gate
):
    scene = gated_main(peak=peak)

    scene.cycle(1000, units)

    gates = [event for event in commander_events(match_record) if event["kind"] == "gate"]
    assert scene.commander.gate.is_open and "advance" in scene.offered()
    fields = ("open", "value", "body_share", "enemy_estimate", "assumed")
    assert [tuple(event[field] for field in fields) for event in gates] == [gate]


@pytest.mark.parametrize(
    "units, peak",
    [(FIVE_THOUSAND, command_army.EnemyPeak(6300, 900)), (FOUR_THOUSAND, command_army.EnemyPeak(5001, 900))],
    ids=["5,000 against 6,300", "4,000 against 5,001"],
)
def test_forward_gate_stays_shut_for_main_worth_under_0_8_of_the_enemy_estimate_seen(match_record, units, peak):
    scene = gated_main(peak=peak)

    scene.cycle(1000, units)

    assert scene.commander.gate.is_open is False and "advance" not in scene.offered()
    assert [event for event in commander_events(match_record) if event["kind"] == "gate"] == []


def measured(value: float, share: float = 0.8, estimate: int = 5000, assumed: bool = False):
    return command_gate.Measure(value, share, estimate, assumed)


def test_forward_gate_after_opening_at_5000_a_value_of_2400_closes_it_and_2600_does_not():
    gate, tuning = command_gate.ForwardGate(), CommanderTuning()

    opened = gate.follow(measured(5000), False, tuning)
    kept = gate.follow(measured(2600, share=0.3), False, tuning)
    closed = gate.follow(measured(2400), False, tuning)
    reopened = gate.follow(measured(4800, estimate=6100), False, tuning)

    assert (opened.is_open, kept, closed.is_open, closed.reason, reopened) == (True, None, False, "regroup", None)


def test_forward_gate_the_script_pulling_main_back_closes_the_gate_and_keeps_it_closed():
    gate, tuning = command_gate.ForwardGate(), CommanderTuning()
    gate.follow(measured(5000), False, tuning)

    closed = gate.follow(measured(5000), True, tuning)
    held = gate.follow(measured(5000), True, tuning)

    assert (closed.is_open, closed.reason, held, gate.is_open) == (False, "pulled_back", None, False)


def test_forward_gate_gather_stands_from_the_handover_and_a_standing_advance_gives_way_to_it_when_the_gate_closes():
    scene = gated_main()
    orders = scene.cycle(1000, THREE_THOUSAND)
    first = scene.commander.standing
    scene.chooser.answer(None, TIMEOUT)
    scene.cycle(1005, FOUR_THOUSAND)
    scene.cycle(1010, FOUR_THOUSAND)
    scene.answer("advance")
    scene.cycle(1015, FOUR_THOUSAND)
    advancing = scene.commander.standing
    # Worth 1,275, under half the 4,000 main was worth when the gate opened.
    shrunk = [{**tank, "hp": 50} for tank in FOUR_THOUSAND[:3]]

    scene.cycle(1020, shrunk)

    assert first.kind == "gather" and [(order["cmd"], order["to"]) for order in orders] == [("move", [32, 48])]
    assert advancing.kind == "advance" and scene.commander.gate.is_open is False
    assert scene.commander.standing.kind == "gather"


def test_forward_gate_the_handover_counts_main_alone(match_record):
    scene = commanding()
    scene.cycle(1000, units=SIX_UNITS[:4])
    scene.agent.ctx.groups.assign(SIX_UNITS[0]["id"], "guard")
    scene.agent.ctx.groups.assign(SIX_UNITS[1]["id"], "escort")
    # Six combat units: two of main's own, one each of the guard and the escort, and two new ones of main's.
    scene.cycle(1005, units=SIX_UNITS)
    split = scene.commander.role
    more = tanks(2, 34, 47, first=500, unit_type="2tnk")
    scene.cycle(1010, units=[*SIX_UNITS, *more])

    assert (split, scene.commander.role) == ("scout", "main")
    assert commander_events(match_record) == [{"kind": "handover", "role": "main", "tick": 1010, "combat_units": 6}]


# -- the forward gate: units joining main while it is open ----------------------------------------------------------

STAGING_SQUAD = [unit(500 + n, "2tnk", 31 + n % 2, 47 + n // 2) for n in range(4)]
SQUAD_IDS = {tank["id"] for tank in STAGING_SQUAD}


def opened_gate() -> GatedMain:
    """main of FIVE_THOUSAND commanded, its forward gate opened in a first cycle at tick 1000."""
    scene = gated_main()
    scene.cycle(1000, FIVE_THOUSAND)
    assert scene.commander.gate.is_open
    return scene


def naming(orders: list[dict], ids: set[int]) -> list[tuple]:
    """The command, the units of `ids` and the cell of each order that names any of them."""
    return [
        (order["cmd"], sorted(set(order["units"]) & ids), order["to"]) for order in orders if ids & set(order["units"])
    ]


def test_forward_gate_a_unit_joining_main_while_the_gate_is_open_is_moved_to_the_staging_point():
    scene = opened_gate()

    orders = scene.cycle(1005, [*FIVE_THOUSAND, unit(500, "2tnk", 20, 52)])

    assert naming(orders, {500}) == [("move", [500], [32, 48])]
    assert scene.commander.gate.joiners.ids == {500}


def test_forward_gate_four_joining_units_at_the_staging_point_are_moved_together_to_main_s_centroid():
    scene = opened_gate()

    three = scene.cycle(1005, [*FIVE_THOUSAND, *STAGING_SQUAD[:3]])
    four = scene.cycle(1010, [*FIVE_THOUSAND, *STAGING_SQUAD])

    assert naming(three, SQUAD_IDS) == []
    assert naming(four, SQUAD_IDS) == [("attack_move", sorted(SQUAD_IDS), [43, 46])]


@pytest.mark.parametrize("tick, sent", [(1754, False), (1755, True)], ids=["29.96 s after", "30 s after"])
def test_forward_gate_joining_units_at_the_staging_point_go_on_30_s_after_the_first_arrived(tick, sent):
    scene = opened_gate()
    two = STAGING_SQUAD[:2]
    scene.cycle(1005, [*FIVE_THOUSAND, *two])

    orders = scene.cycle(tick, [*FIVE_THOUSAND, *two])

    assert naming(orders, {500, 501}) == ([("attack_move", [500, 501], [43, 46])] if sent else [])


def test_forward_gate_a_sent_unit_within_6_cells_of_main_s_centroid_joins_main_and_one_standing_short_is_sent_again():
    scene = opened_gate()
    scene.cycle(1005, [*FIVE_THOUSAND, *STAGING_SQUAD])
    arrived = [unit(500, "2tnk", 45, 47), unit(501, "2tnk", 46, 47)]
    short = [unit(502, "2tnk", 34, 47), unit(503, "2tnk", 35, 47)]

    orders = scene.cycle(1010, [*FIVE_THOUSAND, *arrived, *short])
    scene.cycle(1015, [*FIVE_THOUSAND, *arrived, *short])

    assert naming(orders, {502, 503}) == [("attack_move", [502, 503], [43, 46])]
    assert scene.commander.gate.joiners.ids == {502, 503}


def test_forward_gate_when_it_closes_every_joining_unit_is_main_again():
    scene = opened_gate()
    scene.cycle(1005, [*FIVE_THOUSAND, *STAGING_SQUAD[:2]])
    # Worth 510, 2,210 with the two joining 12 cells off: the gate closes, as main as a whole is worth less than half
    # the 5,000 it opened at.
    shrunk = [{**tank, "hp": 10} for tank in FIVE_THOUSAND]

    orders = scene.cycle(1010, [*shrunk, *STAGING_SQUAD[:2]])

    assert scene.commander.gate.is_open is False and scene.commander.gate.joiners.ids == frozenset()
    assert scene.commander.standing.kind == "gather" and naming(orders, {500, 501}) == []


# -- no home regroup while winning ---------------------------------------------------------------------------------

# The watched game's enemy estimate: 4,750, seen.
SEEN_4750 = command_army.EnemyPeak(4750, 12000)


def own(unit_id: int, unit_type: str, x: int, y: int = 47, hp: int = 100) -> Unit:
    return Unit(unit_id, unit_type, x, y, hp, True, None, None)


# main's front worth 1,695: a light tank, a medium tank and two rifles, one at 45%.
WINNING_FRONT = [own(1, "1tnk", 40), own(2, "2tnk", 41), own(3, "e1", 42), own(4, "e1", 43, hp=45)]
# What main spent since the gate opened, 18,362 of heavy tanks beside the front, so that it opened at 20,057.
WINNING_SPENT = [
    *[own(10 + n, "4tnk", 40 + n % 4, 46 + n // 4) for n in range(9)],
    own(19, "4tnk", 44, 48, hp=18),
    own(20, "e1", 44, 46, hp=2),
]


def reinforcements(x: int) -> list[Unit]:
    """18,305 of units joining main at x: nine heavy tanks, a rocket soldier and a rifle at 5%."""
    return [own(30 + n, "4tnk", x + n % 3, 46 + n // 3) for n in range(9)] + [
        own(39, "e3", x, 50),
        own(40, "e1", x, 51, hp=5),
    ]


def whole_main_gate(joining: list[Unit]) -> tuple[command_gate.ForwardGate, command_gate.GateChange | None]:
    """A gate opened at tick 1000 on the front and what main spent since, 20,057 against the seen 4,750, and what it
    does at tick 1005 with the front alone left of main and the units `joining` it."""
    scene = army_scene(tanks(2))
    world, ctx = scene.agent.world, scene.agent.ctx
    gate, tuning = command_gate.ForwardGate(), CommanderTuning()
    opening = gate.split([*WINNING_FRONT, *WINNING_SPENT], 1000)
    opened = gate.follow(command_gate.measure(opening, 4750, False, world, ctx), False, tuning)
    assert opened is not None and opened.is_open and opened.measure.value == 20057
    front = gate.split([*WINNING_FRONT, *joining], 1005)
    assert front == WINNING_FRONT
    return gate, gate.follow(command_gate.measure(front, 4750, False, world, ctx, gate.joining_near()), False, tuning)


def test_no_home_regroup_the_gate_with_1695_at_the_front_and_20000_of_main_within_30_cells_stays_open():
    gate, change = whole_main_gate(reinforcements(60))

    assert (change, gate.is_open, len(gate.joining_near())) == (None, True, 11)


def test_no_home_regroup_the_gate_closes_for_a_regroup_once_main_as_a_whole_falls_below_half_its_opening_value():
    # The units joining 38 cells off count for nothing: main as a whole is the front's 1,695.
    gate, far = whole_main_gate(reinforcements(80))
    # Within 30 cells, a heavy tank: main as a whole is 3,695, under half the 20,057 and under 0.8 of the 4,750.
    _, near = whole_main_gate([own(30, "4tnk", 60)])

    assert (far.is_open, far.reason, far.measure.value, gate.is_open) == (False, "regroup", 1695, False)
    assert (near.is_open, near.reason, near.measure.value) == (False, "regroup", 3695)


@pytest.mark.parametrize("capped, opens", [(True, True), (False, False)], ids=["on", "off"])
def test_gate_capped_estimate_an_estimate_grown_to_6850_with_1200_seen_lets_main_of_4750_through(capped, opens):
    # Four rocket soldiers in sight, 1,200: the estimate held to 1.5 times that, 1,800, against the 6,850 grown unseen.
    rockets = [{"id": 700 + n, "type": "e3", "x": 60, "y": 40 + n, "hp": 100} for n in range(4)]
    scene = army_scene(tanks(2), visible_enemies=rockets)
    world, ctx = scene.agent.world, scene.agent.ctx
    ctx.enemy_army_estimate = 6850.0
    tuning = CommanderTuning(gate_capped_estimate=capped)
    main = [own(1, "4tnk", 40), own(2, "4tnk", 41), own(3, "2tnk", 42)]

    enemy = Commander(None, tuning)._gate_estimate(6850, False, world, ctx)
    change = command_gate.ForwardGate().follow(command_gate.measure(main, enemy, False, world, ctx), False, tuning)

    assert enemy == (1800 if capped else 6850)
    assert (change is not None and change.is_open) is opens


@pytest.mark.parametrize("counts_away, expected", [(True, None), (False, "regroup")], ids=["on", "off"])
def test_gate_counts_away_main_s_units_out_on_a_detachment_are_no_value_lost(counts_away, expected):
    # The gate opened on the front and the heavy tanks, 20,057; the heavy tanks then left on a detachment.
    scene = army_scene(tanks(2))
    world, ctx = scene.agent.world, scene.agent.ctx
    tuning = CommanderTuning(gate_counts_away=counts_away)
    gate, commander = command_gate.ForwardGate(), Commander(None, tuning)
    opening = gate.split([*WINNING_FRONT, *WINNING_SPENT], 1000)
    gate.follow(command_gate.measure(opening, 4750, False, world, ctx), False, tuning)
    world.combat_units.update({unit.id: unit for unit in WINNING_SPENT})
    away = frozenset(unit.id for unit in WINNING_SPENT)
    front = gate.split(WINNING_FRONT, 1005, away)

    counted = commander._counted(gate.joining_near(), away, world)
    change = gate.follow(command_gate.measure(front, 4750, False, world, ctx, counted), False, tuning)

    assert (None if change is None else change.reason) == expected


def test_no_home_regroup_the_gate_keeps_its_body_share_test_and_whole_main_over_0_8_of_the_estimate_stays_open():
    scene = army_scene(tanks(2))
    world, ctx = scene.agent.world, scene.agent.ctx
    gate, tuning = command_gate.ForwardGate(), CommanderTuning()
    # Half of main 40 cells off: a body share of 0.5 opens no gate, whatever main is worth.
    scattered = gate.split([*WINNING_FRONT, *[own(10 + n, "4tnk", 80 + n, 47) for n in range(4)]], 1000)
    shut = gate.follow(command_gate.measure(scattered, 4750, False, world, ctx), False, tuning)
    gate.follow(
        command_gate.measure(gate.split([*WINNING_FRONT, *WINNING_SPENT], 1005), 4750, False, world, ctx), False, tuning
    )
    # Main as a whole 4,000, under half the 20,057 it opened at but over 0.8 of the 4,750: the gate stays open.
    kept = gate.follow(command_gate.Measure(4000, 1.0, 4750, False), False, tuning)

    assert (shut, gate.is_open, kept) == (None, True, None)


def regroup_actions(regroup: command_army.Regroup | None, *, is_gate_open: bool = True, **context) -> list[Action]:
    """main of eight heavy tanks about (45,47) against the seen 4,750, its actions with main as a whole as `regroup`
    says, the match context's fields as `context` gives them."""
    scene = army_scene(tanks(8), peak=SEEN_4750)
    world, ctx = scene.agent.world, scene.agent.ctx
    for name, value in context.items():
        setattr(ctx, name, value)
    main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
    found = command_army.brief(main, NAMES, SEEN_4750, world, ctx)
    return list(command_army.army_actions(found, is_gate_open, NAMES, world, ctx, regroup=regroup))


def gather_of(actions: list[Action]) -> Action | None:
    return next((action for action in actions if action.kind == "gather"), None)


def test_no_home_regroup_main_worth_20000_against_4750_with_no_lost_fight_is_offered_no_gather_but_two_actions():
    actions = regroup_actions(command_army.Regroup(20000))

    assert gather_of(actions) is None and [action.kind for action in actions] == ["flank", "advance"]


@pytest.mark.parametrize(
    "regroup, context",
    [
        (command_army.Regroup(6000), {}),
        (command_army.Regroup(20000, lost_lately=True), {}),
        (command_army.Regroup(20000), {"threat_level": "major"}),
        (command_army.Regroup(20000, pulled_back=True), {}),
        (None, {}),
    ],
    ids=["main worth 6000", "a fight lost lately", "a major threat on the base", "the override pulls main", "none"],
)
def test_no_home_regroup_gather_is_offered_while_main_is_not_winning_forward(regroup, context):
    assert gather_of(regroup_actions(regroup, **context)) is not None


def test_no_home_regroup_gather_is_offered_with_the_gate_closed_or_an_assumed_estimate():
    closed = regroup_actions(command_army.Regroup(20000), is_gate_open=False)
    scene = army_scene(tanks(8))
    world, ctx = scene.agent.world, scene.agent.ctx
    main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
    found = command_army.brief(main, NAMES, command_army.EnemyPeak(), world, ctx)
    assumed = command_army.army_actions(found, True, NAMES, world, ctx, regroup=command_army.Regroup(20000))

    assert found.estimate.is_assumed
    assert gather_of(closed).head.startswith("gather at staging ") and gather_of(assumed).head.startswith("gather at ")


def test_no_home_regroup_a_regroup_at_1_2_of_the_enemy_gathers_at_main_s_centroid_and_at_0_8_at_staging():
    ahead = gather_of(regroup_actions(command_army.Regroup(1.2 * 4750)))
    behind = gather_of(regroup_actions(command_army.Regroup(0.8 * 4750)))

    # Eight heavy tanks in a column of three from (44,46): their centroid is (45,47).
    assert (ahead.cell, ahead.head) == ((45, 47), "gather forward at (45,47), 0 cells")
    assert (behind.cell, behind.head) == ((32, 48), "gather at staging (32,48), 13 cells")


def test_no_home_regroup_the_value_weighted_centroid_or_the_body_nearer_the_objective():
    scene = army_scene([*tanks(4, unit_type="4tnk"), *tanks(4, 30, 47, first=410, unit_type="e1")], peak=SEEN_4750)
    world, ctx = scene.agent.world, scene.agent.ctx
    main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
    found = command_army.brief(main, NAMES, SEEN_4750, world, ctx)
    ahead = gather_of(command_army.army_actions(found, True, NAMES, world, ctx, regroup=command_army.Regroup(6000)))

    # The heavy tanks, worth 8,000 by 400 of rifles, weigh the centroid to (44,46), or their own center, the nearer
    # the enemy's start.
    assert ahead.cell in {found.view.center, (44, 46)}
    assert cells.distance(ahead.cell, found.goal.cell) <= cells.distance(found.centroid, found.goal.cell)


def test_no_home_regroup_the_commander_offers_main_ahead_a_forward_gather_and_carries_it_out_there():
    scene = gated_main(peak=command_army.EnemyPeak(4000, 900))
    scene.cycle(1000, FIVE_THOUSAND)
    question = scene.chooser.submitted[-1]
    gather = next(option for option in question.options if option.description.startswith("gather"))
    scene.answer("gather")

    orders = scene.cycle(1005, FIVE_THOUSAND)

    assert scene.commander.gate.is_open and gather.description.startswith("gather forward at (43,46)")
    assert scene.commander.standing.kind == "gather" and scene.commander.standing.cell == (43, 46)
    assert any(order["to"] == [43, 46] for order in orders if "to" in order)


# -- the retreat override ------------------------------------------------------------------------------------------

# Nine heavy tanks in sight beside main of FIVE_THOUSAND: main's fight there is 5,000 against 10,350, 0.48.
NINE_HEAVY = [enemy(610 + n, "3tnk", 50 + n % 3, 44 + n // 3) for n in range(9)]


def test_retreat_override_main_s_fight_below_0_9_in_every_cycle_for_25_ticks_takes_main_and_resets_otherwise():
    override, tuning = command_retreat.RetreatOverride(), CommanderTuning()

    early = [override.follow(0.85, False, tick, tuning) for tick in range(1000, 1025, 5)]
    taken = override.follow(0.85, False, 1025, tuning)
    reset = command_retreat.RetreatOverride()
    broken = [reset.follow(ratio, False, tick, tuning) for tick, ratio in ((1000, 0.85), (1015, 0.9), (1025, 0.85))]
    lost_sight = [reset.follow(ratio, False, tick, tuning) for tick, ratio in ((1040, None), (1050, 0.85))]

    assert early == [None] * 5 and taken == "taken" and override.is_active
    assert broken == [None, None, None] and lost_sight == [None, None] and reset.is_active is False


@pytest.mark.parametrize("ratio, taken", [(0.85, True), (0.89, True), (0.9, False), (0.95, False)])
def test_retreat_override_a_fight_under_0_9_for_25_ticks_takes_main_and_one_at_0_9_or_more_never_does(ratio, taken):
    override, tuning = command_retreat.RetreatOverride(), CommanderTuning()

    happened = [override.follow(ratio, False, tick, tuning) for tick in range(1000, 1105, 5)]

    assert happened == ([None] * 5 + ["taken"] + [None] * 15 if taken else [None] * 21)
    assert override.is_active is taken


@pytest.mark.parametrize(
    "ratio, is_home, reason",
    [(1.2, False, "resumed"), (1.19, False, None), (None, False, "no_enemy"), (0.5, True, "home")],
    ids=["fight at 1.2", "fight at 1.19", "no enemy", "home"],
)
def test_retreat_override_lets_main_go_at_1_2_with_no_enemy_or_home(ratio, is_home, reason):
    override, tuning = command_retreat.RetreatOverride(), CommanderTuning()
    for tick in range(1000, 1055, 5):
        override.follow(0.5, False, tick, tuning)

    assert override.follow(ratio, is_home, 1060, tuning) == reason
    assert override.is_active is (reason is None)


def advancing_main(monkeypatch) -> tuple[GatedMain, list[list]]:
    """main of FIVE_THOUSAND commanded, its gate open, advance its standing action from tick 1005, and the batches."""
    batches = recorded_batches(monkeypatch)
    scene = opened_gate()
    scene.answer("advance")
    scene.cycle(1005, FIVE_THOUSAND)
    assert scene.commander.standing.kind == "advance"
    return scene, batches


def overridden(match_record) -> list[tuple]:
    """The `override_retreat` events and the gate's closings of the match record, kind and figure or reason."""
    events = commander_events(match_record)
    return [
        (event["kind"], event.get("ratio", event.get("reason")))
        for event in events
        if event["kind"] == "override_retreat" or (event["kind"] == "gate" and not event["open"])
    ]


@pytest.mark.parametrize(
    "force, offered",
    [(5000, ["gather", "flank"]), (3000, ["gather", "flank", "advance"])],
    ids=["the same force", "1.5x the failed force"],
)
def test_failed_attack_memory_an_advance_on_the_start_main_failed_at_is_not_offered_but_its_flank_is(
    match_record, force, offered
):
    scene = opened_gate()
    scene.chooser.answer(None, TIMEOUT)
    # main of 5,000 failed on the enemy start; the combat manager publishes its memory at the end of each cycle.
    for tick in (1005, 1010):
        scene.agent.ctx.failed_attacks = (FailedAttack((74, 48), force, 1000, "direct", 1000, frozenset()),)
        scene.cycle(tick, FIVE_THOUSAND)

    assert scene.offered() == offered
    blocked = [event for event in match_record("combat") if event["reason"] == "attack_blocked_failed"]
    expected = [("advance", "failed_here")] if force == 5000 else []
    assert [(event["user"], event["why"]) for event in blocked] == expected


def test_retreat_override_a_fight_at_0_5_for_25_ticks_moves_main_to_the_staging_point_through_the_commander_slot(
    monkeypatch, match_record
):
    scene, batches = advancing_main(monkeypatch)

    for tick in range(1010, 1040, 5):
        scene.cycle(tick, FIVE_THOUSAND, visible_enemies=NINE_HEAVY)

    assert overridden(match_record) == [("override_retreat", 0.48), ("gate", "pulled_back")]
    assert scene.commander.retreat.is_active and scene.commander.standing.kind == "gather"
    assert [(order["cmd"], order["to"]) for order in commander_orders(batches[-1])] == [("move", [32, 48])]


# Five heavy tanks and a light tank at 19% in sight beside main of FIVE_THOUSAND: 5,000 against 5,883, 0.85.
LOST_AT_0_85 = [*[enemy(610 + n, "3tnk", 50 + n % 3, 44 + n // 3) for n in range(5)], enemy(615, "1tnk", 52, 45, hp=19)]
# Four heavy tanks and a medium tank at 78%: 5,000 against 5,263, 0.95.
HELD_AT_0_95 = [*[enemy(610 + n, "3tnk", 50 + n % 3, 44 + n // 3) for n in range(4)], enemy(615, "2tnk", 51, 45, hp=78)]


def fight_of(scene: GatedMain, units: list[dict]) -> float:
    """main's fight where it stands, `fight_here` of its question, to two decimals."""
    world, ctx = scene.agent.world, scene.agent.ctx
    main = [world.combat_units[item["id"]] for item in units]
    return round(command_army.fight_here(command_army.army_view(main), None, world, ctx), 2)


@pytest.mark.parametrize(
    "enemies, ratio, is_taken", [(LOST_AT_0_85, 0.85, True), (HELD_AT_0_95, 0.95, False)], ids=["0.85", "0.95"]
)
def test_retreat_override_a_fight_at_0_85_for_25_ticks_moves_main_to_the_staging_point_and_one_at_0_95_does_not(
    monkeypatch, match_record, enemies, ratio, is_taken
):
    scene, batches = advancing_main(monkeypatch)

    for tick in range(1010, 1040, 5):
        scene.cycle(tick, FIVE_THOUSAND, visible_enemies=enemies)

    taken = [("override_retreat", ratio), ("gate", "pulled_back")]
    assert fight_of(scene, FIVE_THOUSAND) == ratio
    assert overridden(match_record) == (taken if is_taken else [])
    assert scene.commander.retreat.is_active is is_taken
    assert scene.commander.standing.kind == ("gather" if is_taken else "advance")
    orders = [(order["cmd"], order["to"]) for order in commander_orders(batches[-1])]
    assert (("move", [32, 48]) in orders) is is_taken


def test_retreat_override_a_fight_at_0_85_for_20_ticks_leaves_main_to_its_action(monkeypatch, match_record):
    scene, _ = advancing_main(monkeypatch)

    for tick in range(1010, 1035, 5):
        scene.cycle(tick, FIVE_THOUSAND, visible_enemies=LOST_AT_0_85)

    assert overridden(match_record) == []
    assert scene.commander.retreat.is_active is False and scene.commander.standing.kind == "advance"


def taken_main(monkeypatch) -> GatedMain:
    """main of FIVE_THOUSAND pulled out of its fight with NINE_HEAVY by the override at tick 1035."""
    scene, _ = advancing_main(monkeypatch)
    for tick in range(1010, 1065, 5):
        scene.cycle(tick, FIVE_THOUSAND, visible_enemies=NINE_HEAVY)
    assert scene.commander.retreat.is_active
    return scene


def test_retreat_override_while_it_holds_an_answer_is_not_carried_out_and_the_decision_record_says_so(
    monkeypatch, match_record
):
    scene = taken_main(monkeypatch)
    scene.chooser.answer(None, TIMEOUT)
    scene.cycle(1065, FIVE_THOUSAND, visible_enemies=NINE_HEAVY)
    scene.cycle(1070, FIVE_THOUSAND, visible_enemies=NINE_HEAVY)
    scene.answer("hold")

    scene.cycle(1075, FIVE_THOUSAND, visible_enemies=NINE_HEAVY)

    assert scene.commander.standing.kind == "gather"
    fallbacks = match_record("fallback")
    assert fallbacks[-1] == {"intended_source": "red_alert_commander", "reason": "override_retreat", "used": "script"}
    actions = [event["action"] for event in commander_events(match_record) if event["kind"] == "action"]
    assert len(actions) == 1 and actions[0].startswith("advance")


def test_retreat_override_an_advance_answered_before_the_override_is_not_carried_out_once_it_holds(monkeypatch):
    scene, _ = advancing_main(monkeypatch)
    for tick in range(1010, 1030, 5):
        scene.cycle(tick, FIVE_THOUSAND, visible_enemies=LOST_AT_0_85)
    scene.chooser.answer(None, TIMEOUT)
    scene.cycle(1030, FIVE_THOUSAND, visible_enemies=LOST_AT_0_85)
    scene.answer("advance")

    scene.cycle(1035, FIVE_THOUSAND, visible_enemies=LOST_AT_0_85)

    assert scene.commander.retreat.is_active and scene.commander.standing.kind == "gather"


def tank_wreck(enemy_id: int) -> dict:
    """The wreck of a heavy tank, which the world forgets it by."""
    return {**enemy(enemy_id, "3tnk", 50, 44), "wreck": True}


AT_HOME = [
    unit(430 + n, kind, 31 + n % 3, 47 + n // 3) for n, kind in enumerate(["3tnk", "3tnk", "3tnk", "2tnk", "1tnk"])
]
BY_HOME = [enemy(610 + n, "3tnk", 38 + n % 3, 44 + n // 3) for n in range(9)]


@pytest.mark.parametrize(
    "units, enemies, reason",
    [
        (FIVE_THOUSAND, [*NINE_HEAVY[:3], *[tank_wreck(613 + n) for n in range(6)]], "resumed"),
        (FIVE_THOUSAND, [tank_wreck(610 + n) for n in range(9)], "no_enemy"),
        (AT_HOME, BY_HOME, "home"),
    ],
    ids=["fight at 1.4", "no enemy near", "within 6 cells of the staging point"],
)
def test_retreat_override_ends_at_1_2_with_no_enemy_near_or_home_and_gather_stands(
    monkeypatch, match_record, units, enemies, reason
):
    scene = taken_main(monkeypatch)

    scene.cycle(1065, units, visible_enemies=enemies)

    assert scene.commander.retreat.is_active is False and scene.commander.standing.kind == "gather"
    assert commander_events(match_record)[-1]["kind"] in ("override_end", "gate")
    ends = [event for event in commander_events(match_record) if event["kind"] == "override_end"]
    assert [(event["reason"], event["tick"]) for event in ends] == [(reason, 1065)]


def test_retreat_override_the_army_request_s_system_text_values_regrouping():
    scene = opened_gate()

    assert scene.chooser.submitted[-1].system_text == ARMY_SYSTEM_TEXT == command_army.SYSTEM_TEXT


# -- the pick commitment -----------------------------------------------------------------------------------------------

AWAY = command_prompt.Situation((0, 0), False)


def picked_then_answered(
    answer_tick: int, index: int = 2, situation=AWAY, offered=None, tuning: CommanderTuning = LEGACY_SCOUT
) -> Commanding:
    """The scout's second action picked at tick 1005, then a question asked 20 ticks before `answer_tick` and answered
    at `answer_tick` with the letter of its action at `index`, taken against the actions offered in the situation
    given."""
    scene = commanding(tuning)
    letter = scene.question.actions[index].id
    scene.cycle(1000, situation=AWAY)
    scene.chooser.answer(scene.question.actions[1].id)
    scene.cycle(1005, situation=AWAY)
    scene.chooser.answer(None, TIMEOUT)
    scene.cycle(answer_tick - 25, situation=AWAY)
    scene.cycle(answer_tick - 20, situation=AWAY)
    scene.chooser.answer(letter)
    scene.cycle(answer_tick, situation=situation, offered=offered)
    return scene


def test_commitment_an_answer_for_another_action_3_s_after_a_pick_is_not_carried_out_and_recorded_as_committed(
    match_record,
):
    scene = picked_then_answered(1080)
    scene.commander.finalize()
    scene.commander.finalize()

    picked = scene.question.actions[1]
    assert scene.commander.standing == picked and scene.commander.taken_tick == 1005
    assert scene.commander.committed_answers == 1 and [pick.id for pick in scene.commander.picks] == [picked.id]
    assert match_record("fallback")[-1] == {
        "intended_source": "red_alert_commander",
        "reason": "committed",
        "used": "script",
    }
    assert [event["kind"] for event in match_record("commander")] == ["action", "summary"]
    assert match_record("commander")[-1] == {"kind": "summary", "committed_answers": 1}


@pytest.mark.parametrize("answer_tick, taken", [(1204, False), (1205, True)], ids=["7.96 s", "8 s"])
def test_commitment_the_same_answer_8_s_after_the_pick_is_carried_out(answer_tick, taken):
    scene = picked_then_answered(answer_tick)

    assert scene.commander.standing == scene.question.actions[2 if taken else 1]
    assert scene.commander.committed_answers == (0 if taken else 1)


# The stub chooser is silent between the answers a test gives: no script fallback.
SCOUT_COMMIT = CommanderTuning(continue_option=False, script_fallback=False)


@pytest.mark.parametrize(
    "answer_tick, taken", [(1205, False), (1504, False), (1505, True)], ids=["8 s", "19.96 s", "20 s"]
)
def test_scout_commit_a_scout_pick_with_a_destination_is_kept_20_s_and_told_as_scout_commit(
    match_record, answer_tick, taken
):
    scene = picked_then_answered(answer_tick, tuning=SCOUT_COMMIT)

    assert scene.commander.standing == scene.question.actions[2 if taken else 1]
    reasons = [event["reason"] for event in match_record("fallback")]
    assert ("scout_commit" in reasons) is (not taken)


def test_scout_commit_an_answer_naming_a_back_off_is_carried_out_at_once():
    actions = commanding().question.actions
    backing_off = tuple(
        dataclasses.replace(action, kind="back_off") if action == actions[2] else action for action in actions
    )

    scene = picked_then_answered(1080, offered=backing_off, tuning=SCOUT_COMMIT)

    assert scene.commander.standing.kind == "back_off"


def test_commitment_an_answer_3_s_after_a_pick_is_carried_out_once_the_scout_is_within_3_cells_of_its_destination():
    destination = commanding().question.actions[1].cell
    there = command_prompt.Situation((destination[0] + 3, destination[1]), False)
    short = command_prompt.Situation((destination[0] + 4, destination[1]), False)

    arrived, on_the_way = picked_then_answered(1080, situation=there), picked_then_answered(1080, situation=short)

    actions = arrived.question.actions
    assert arrived.commander.standing == actions[2] and on_the_way.commander.standing == actions[1]


def test_commitment_an_answer_3_s_after_a_pick_is_carried_out_once_the_standing_action_is_no_longer_offered():
    actions = commanding().question.actions
    without_picked = tuple(action for action in actions if action != actions[1])

    scene = picked_then_answered(1080, offered=without_picked)

    assert scene.commander.standing == actions[2] and scene.commander.committed_answers == 0


def test_commitment_a_danger_flight_of_the_scout_releases_its_standing_action():
    scene = picked_then_answered(1030, situation=command_prompt.Situation((0, 0), True))

    assert scene.commander.standing == scene.question.actions[2]


def test_commitment_a_danger_flight_of_the_scout_overrides_a_committed_destination_at_once(monkeypatch):
    batches = recorded_batches(monkeypatch)
    scene = scout_command()
    scene.cycle(1000)
    scene.answer_second()
    scene.cycle(1005)

    fled = scene.cycle(1010, visible_enemies=[enemy(612, "2tnk", 28, 48)])

    (move,) = orders_for(fled, 300)
    assert move[0] == "move" and move[1][0] < 24 and commander_orders(batches[-1]) == []


def test_commitment_an_answer_naming_the_standing_action_refreshes_nothing(match_record):
    scene = picked_then_answered(1080, index=1)

    assert scene.commander.taken_tick == 1005 and len(scene.commander.picks) == 1
    assert scene.commander.committed_answers == 0
    assert [event["kind"] for event in commander_events(match_record)] == ["action"]


@pytest.mark.parametrize("threat_active, taken", [(False, False), (True, True)], ids=["no threat", "base attacked"])
def test_commitment_main_s_pick_gives_way_3_s_later_when_the_threat_changed_from_none_to_base_attacked(
    threat_active, taken
):
    scene = opened_gate()
    scene.answer("advance")
    scene.cycle(1005, FIVE_THOUSAND)
    scene.chooser.answer(None, TIMEOUT)
    scene.cycle(1055, FIVE_THOUSAND)
    scene.agent.ctx.threat_active = threat_active
    scene.cycle(1060, FIVE_THOUSAND)
    scene.answer("gather")

    scene.cycle(1080, FIVE_THOUSAND)

    assert scene.commander.standing.kind == ("gather" if taken else "advance")


# A rifle 8 cells from main's centroid: an attack is offered whether the gate is open or closed.
NEAR_RIFLE = [enemy(610, "e1", 51, 46)]


@pytest.mark.parametrize("closes, taken", [(False, False), (True, True)], ids=["gate open", "gate closed"])
def test_commitment_main_s_pick_gives_way_3_s_later_when_the_gate_closed(closes, taken):
    scene = opened_gate()
    scene.cycle(1001, FIVE_THOUSAND, visible_enemies=NEAR_RIFLE)
    scene.chooser.answer(None, TIMEOUT)
    scene.cycle(1002, FIVE_THOUSAND, visible_enemies=NEAR_RIFLE)
    scene.cycle(1003, FIVE_THOUSAND, visible_enemies=NEAR_RIFLE)
    scene.answer("attack")
    scene.cycle(1005, FIVE_THOUSAND, visible_enemies=NEAR_RIFLE)
    picked = scene.commander.standing
    units = [{**tank, "hp": 40} for tank in FIVE_THOUSAND] if closes else FIVE_THOUSAND
    scene.chooser.answer(None, TIMEOUT)
    scene.cycle(1055, units, visible_enemies=NEAR_RIFLE)
    scene.cycle(1060, units, visible_enemies=NEAR_RIFLE)
    scene.answer("gather")

    scene.cycle(1080, units, visible_enemies=NEAR_RIFLE)

    assert picked.kind == "attack" and scene.commander.gate.is_open is not closes
    assert scene.commander.standing.kind == ("gather" if taken else "attack")


def test_commitment_the_script_s_gather_gives_way_to_the_first_pick_at_once():
    scene = gated_main()
    scene.cycle(1000, FIVE_THOUSAND)
    scene.answer("advance")

    scene.cycle(1005, FIVE_THOUSAND)

    assert scene.commander.standing.kind == "advance" and scene.commander.committed_answers == 0


def test_commitment_the_match_record_ends_with_the_commander_s_summary_of_committed_answers(match_record):
    scene = scout_command()
    scene.cycle(1000)

    assert match_record("commander") == [{"kind": "summary", "committed_answers": 0}]


# -- the current line --------------------------------------------------------------------------------------------------


def current_of(lines: list[str]) -> str:
    (line,) = [line for line in lines if line.startswith("current:")]
    return line


def marked(question: command_prompt.Question) -> list[str]:
    """The letters of the options marked as the standing action."""
    return [option.id for option in question.request.options if head_of(option.description).endswith(" (current)")]


@pytest.mark.parametrize(
    "scene, line, budget",
    [
        (lambda: scout_scene(tick=500, scout=(30, 48)), "current: none", 2500),
        (
            lambda: scout_scene(picks=FOUR_PICKS, standing="main_edge", taken_tick=5875, **BUSY),
            "current: {id} enemy main edge to (60,60), 28 cells left, taken 5s ago",
            2500,
        ),
        (
            lambda: scout_scene(scout=(50, 40), picks=FOUR_PICKS, standing="hold", taken_tick=5950, **BUSY),
            "current: {id} hold to (50,40), 0 cells left, taken 2s ago",
            2500,
        ),
        (lambda: army_scene(tanks(6, 30, 47), tick=3000), "current: none", 6000),
        (
            lambda: army_scene(MIXED_ARMY, picks=ARMY_PICKS, standing="advance", taken_tick=12400, **FRONT),
            "current: {id} advance to construction yard to (74,48), 31 cells left, taken 4s ago",
            6000,
        ),
        (
            lambda: army_scene(tanks(8), picks=ARMY_PICKS, standing="attack", taken_tick=12450, **FRONT),
            "current: {id} attack to (55,47), 10 cells left, taken 2s ago",
            6000,
        ),
    ],
    ids=[
        "scout, none",
        "scout, enemy main edge",
        "scout, hold",
        "army, none",
        "army, advance",
        "army, attack on the group's centroid",
    ],
)
def test_current_line_says_the_standing_action_its_cell_cells_left_and_age_and_marks_one_option(scene, line, budget):
    built = scene()
    request = built.question.request
    lines = local.choice_prompt(request).splitlines()
    current = current_of(lines)
    ids = marked(built.question)

    assert current == line.format(id=ids[0] if ids else "")
    assert len(ids) == (0 if line == "current: none" else 1)
    # The picks, if any, then the clock line just before the actions.
    assert lines[lines.index(current) + 1].startswith(("last_picks:", "t=", "score:"))
    assert prompt_chars(request) <= budget


def test_current_line_the_commander_asks_with_main_s_standing_action_marked():
    scene = gated_main()
    scene.cycle(1000, FIVE_THOUSAND)
    first = current_of(local.choice_prompt(scene.chooser.submitted[-1]).splitlines())
    scene.answer("advance")
    scene.cycle(1005, FIVE_THOUSAND)
    scene.cycle(1010, FIVE_THOUSAND)

    second = scene.chooser.submitted[-1]

    assert first.startswith("current: ") and " gather to (32,48), " in first
    assert " advance to enemy start to (74,48), " in current_of(local.choice_prompt(second).splitlines())
    heads = [head_of(option.description) for option in second.options]
    assert [head.split()[0] for head in heads if head.endswith("(current)")] == ["advance"]


# -- the fixed action IDs ------------------------------------------------------------------------------------------


def ids_of(question: command_prompt.Question) -> list[str]:
    return [option.id for option in question.request.options]


def enum_of(question: command_prompt.Question) -> list[str]:
    body = local.build_body("fast", question.request, with_reason=question.request.asks_reason_after(True))
    return body["response_format"]["json_schema"]["schema"]["properties"]["choice"]["enum"]


@pytest.mark.parametrize("threat_active", [False, True], ids=["no threat", "base attacked"])
def test_stable_ids_advance_is_always_i_and_attack_always_h_and_the_enum_is_the_ids_present(threat_active):
    built = army_scene(MIXED_ARMY, threat_active=threat_active, **FRONT)

    assert (built.action("advance").id, built.action("attack").id) == ("I", "H")
    assert enum_of(built.question) == ids_of(built.question) == sorted(ids_of(built.question))
    assert ("A" in ids_of(built.question)) is threat_active


def test_stable_ids_with_all_ten_eligible_and_options_max_6_the_kept_are_a_b_c_d_i_l():
    # Four more V2 launchers, which with the rocket soldiers hold main's fight while a hit takes its eight tanks.
    v2s = [situations.unit(450 + n, "v2rl", 39, 44 + n) for n in range(4)]
    scene = army_scene([*MIXED_ARMY, *v2s], threat_active=True, exposed_trucks=(FAR_TRUCKS,), **FRONT)
    world, ctx = scene.agent.world, scene.agent.ctx
    main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
    found = command_army.brief(main, NAMES, command_army.EnemyPeak(), world, ctx)

    every = command_army.army_actions(found, True, NAMES, world, dataclasses.replace(ctx, tuning=wider(ctx, 10)))
    kept = command_army.army_actions(found, True, NAMES, world, ctx)

    assert [action.id for action in every] == list("ABCDEFGHIL")
    # Gather, which the cut to six would leave out after the attack, takes the last place instead.
    assert [(action.id, action.kind) for action in kept] == [
        ("A", "pull_back"),
        ("B", "retreat_home"),
        ("C", "hold"),
        ("D", "gather"),
        ("I", "advance"),
        ("L", "hit_group"),
    ]


def wider(ctx, options_max: int) -> Tuning:
    """The context's tuning with options_max given, beyond the file's bounds."""
    return dataclasses.replace(ctx.tuning, commander=dataclasses.replace(ctx.tuning.commander, options_max=options_max))


def test_stable_ids_while_the_gate_is_closed_gather_is_always_kept():
    near = [enemy(610 + n, "1tnk", 48, 46 + n) for n in range(2)]
    scene = army_scene(tanks(2, 40, 47, unit_type="1tnk"), threat_active=True, visible_enemies=near)
    world, ctx = scene.agent.world, scene.agent.ctx
    main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
    found = command_army.brief(main, NAMES, command_army.EnemyPeak(), world, ctx)
    narrow = dataclasses.replace(ctx, tuning=wider(ctx, 2))

    closed = command_army.army_actions(found, False, NAMES, world, narrow)
    full = command_army.army_actions(found, False, NAMES, world, ctx)

    assert [(action.id, action.kind) for action in closed] == [("D", "gather"), ("H", "attack")]
    assert [action.id for action in full] == ["A", "B", "C", "D", "H"]


@pytest.mark.parametrize("distance", [18, 30], ids=["danger near", "no danger"])
def test_stable_ids_the_scout_s_kinds_keep_their_letters_with_back_off_or_without(distance):
    built = scout_scene(scout=(66 - distance, 44), visible_enemy_buildings=[enemy(600, "pbox", 66, 44)])
    letters = {action.kind: action.id for action in built.actions}

    assert letters == {kind: command_scout.SCOUT_IDS[kind] for kind in letters}
    assert ("back_off" in letters) is (distance == 18) and letters["hold"] == "A"
    assert enum_of(built.question) == ids_of(built.question) == sorted(ids_of(built.question))


def test_stable_ids_two_enemy_expansion_field_candidates_yield_one_option_d_the_higher_scored():
    built = scout_scene(tick=500, scout=(30, 48))
    world, ctx = built.agent.world, built.agent.ctx
    fields = [
        mission for mission in MissionPlanner().missions(world, ctx, built.sight) if mission.kind == "expansion_watch"
    ]
    scored = [
        command_scout._scored(mission, built.scout, built.dangers(), built.sight, world, ctx) for mission in fields
    ]
    best = max(scored, key=lambda pair: pair[0])[1]

    (offered,) = [action for action in built.actions if action.kind == "expansion_watch"]
    assert len(fields) >= 2 and (offered.id, offered.key) == ("D", best.key)


def test_commitment_an_answer_naming_the_script_s_gather_makes_it_the_model_s_pick(match_record):
    scene = gated_main()
    scene.cycle(1000, FIVE_THOUSAND)
    scene.answer("gather")

    scene.cycle(1005, FIVE_THOUSAND)

    assert scene.commander.standing.kind == "gather" and scene.commander.taken_tick == 1005
    assert [(pick.id, pick.label) for pick in scene.commander.picks] == [("D", "gather")]
    assert [event["kind"] for event in commander_events(match_record)] == ["gate", "action"]


# Four pillboxes and no enemy unit: two heavy tanks beside them lose their fight there, 2,300 against 4,800, 0.48.
FOUR_PILLBOXES = [enemy(600 + n, "pbox", 36 + n % 2, 41 + n // 2) for n in range(4)]
TWO_HEAVY = [unit(450, "3tnk", 41, 43), unit(451, "3tnk", 42, 43)]
# Four heavy tanks more join the two: 6,900 against 4,800, 1.44.
SIX_HEAVY = [*TWO_HEAVY, *[unit(452 + n, "3tnk", 41 + n % 2, 44 + n // 2) for n in range(4)]]
# The two 11 cells and more from every pillbox, and 20 from the staging point: no enemy at all.
OUT_OF_REACH = [unit(450, "3tnk", 40, 30), unit(451, "3tnk", 41, 30)]
# The two 3 cells from the staging point (32, 48), the pillboxes still reaching them.
AT_STAGING = [unit(450, "3tnk", 34, 46), unit(451, "3tnk", 35, 46)]


def pillbox_loss() -> GatedMain:
    """main of two heavy tanks commanded beside FOUR_PILLBOXES from tick 1000, taken by the override at 1025."""
    scene = gated_main()
    for tick in range(1000, 1055, 5):
        scene.cycle(tick, TWO_HEAVY, visible_enemy_buildings=FOUR_PILLBOXES)
    return scene


def test_retreat_override_a_fight_lost_to_defenses_alone_takes_main_and_holds_it_while_they_reach(match_record):
    scene = pillbox_loss()

    for tick in range(1055, 1105, 5):
        scene.cycle(tick, TWO_HEAVY, visible_enemy_buildings=FOUR_PILLBOXES)

    events = commander_events(match_record)
    assert scene.commander.retreat.is_active and scene.commander.standing.kind == "gather"
    assert [(event["ratio"], event["tick"]) for event in events if event["kind"] == "override_retreat"] == [
        (0.48, 1025)
    ]
    assert "override_end" not in [event["kind"] for event in events]


@pytest.mark.parametrize(
    "units, reason",
    [(SIX_HEAVY, "resumed"), (OUT_OF_REACH, "no_enemy"), (AT_STAGING, "home")],
    ids=["fight at 1.44", "no enemy at all", "within 6 cells of the staging point"],
)
def test_retreat_override_a_fight_lost_to_defenses_alone_ends_at_1_2_with_no_enemy_at_all_or_home(
    match_record, units, reason
):
    scene = pillbox_loss()
    scene.cycle(1060, TWO_HEAVY, visible_enemy_buildings=FOUR_PILLBOXES)
    held = scene.commander.retreat.is_active

    scene.cycle(1065, units, visible_enemy_buildings=FOUR_PILLBOXES)

    ends = [
        (event["reason"], event["tick"]) for event in commander_events(match_record) if event["kind"] == "override_end"
    ]
    assert held and ends == [(reason, 1065)] and scene.commander.retreat.is_active is False
    assert scene.commander.standing.kind == "gather"


# -- the targets block ---------------------------------------------------------------------------------------------

# Four medium tanks, their body's center at (41,47).
FOUR_MEDIUM = tanks(4, 40, 47, unit_type="2tnk")
TWO_LIGHT_TANKS = [enemy(610 + n, "1tnk", 52, 46 + n) for n in range(2)]
# Four pillboxes side by side 11 cells from the body's center, each within the others' range.
FOUR_BOXES = [enemy(600 + n, "pbox", 52 + n % 2, 45 + n // 2) for n in range(4)]


def targets_of(scene: ArmyScene) -> list[str]:
    """The `targets:` block of the scene's question: its heading line and the lines after it that name a target."""
    lines = scene.lines
    start = next(index for index, line in enumerate(lines) if line.startswith("targets:"))
    return [lines[start], *[line for line in lines[start + 1 :] if re.match(r"T\d+: ", line)]]


def brief_of(scene: ArmyScene) -> command_army.ArmyBrief:
    world, ctx = scene.agent.world, scene.agent.ctx
    main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
    return command_army.brief(main, NAMES, command_army.EnemyPeak(), world, ctx)


def test_targets_block_three_rifles_2_cells_apart_and_a_tank_10_cells_away_form_two_groups():
    rifles = [enemy(620 + n, "e1", 46 + 2 * n, 47) for n in range(3)]
    scene = army_scene(FOUR_MEDIUM, visible_enemies=[*rifles, enemy(630, "3tnk", 60, 47)])

    found = brief_of(scene).targets

    assert [(target.kind, sorted(record.id for record in target.members)) for target in found] == [
        ("group", [620, 621, 622]),
        ("group", [630]),
    ]
    assert targets_of(scene)[1].startswith("T1: 3 rifle infantry at (48,47), 7 cells, value 300, win ")
    assert targets_of(scene)[2].startswith("T2: 1 heavy tank at (60,47), 19 cells, value 1,150, win ")


def test_targets_block_single_link_joins_a_chain_whose_ends_are_farther_apart_than_the_link():
    chain = [enemy(620 + n, "e1", 46 + 3 * n, 47) for n in range(4)]
    world = army_scene(FOUR_MEDIUM, visible_enemies=chain).agent.world

    groups = command_targets.single_link([world.enemy_memory[620 + n] for n in range(4)], 4)
    apart = command_targets.single_link([world.enemy_memory[620], world.enemy_memory[622]], 4)

    assert [[record.id for record in group] for group in groups] == [[620, 621, 622, 623]]
    assert [[record.id for record in group] for group in apart] == [[620], [622]]


def test_targets_block_with_6_targets_in_reach_the_4_nearest_are_listed_nearest_first():
    units = [enemy(620, "e1", 46, 47), enemy(621, "1tnk", 52, 40), enemy(622, "e3", 41, 65)]
    buildings = [enemy(600, "pbox", 49, 47), enemy(601, "gun", 30, 36), enemy(602, "pbox", 59, 47)]
    scene = army_scene(FOUR_MEDIUM, visible_enemies=units, visible_enemy_buildings=buildings)

    found = brief_of(scene).targets
    lines = targets_of(scene)

    assert [round(target.distance) for target in found] == [5, 8, 13, 16]
    assert [line.split(" at ")[0] for line in lines] == [
        "targets:",
        "T1: 1 rifle infantry",
        "T2: pillbox",
        "T3: 1 light tank",
        "T4: turret",
    ]


def test_targets_block_an_aa_gun_is_not_listed_and_a_pillbox_is():
    scene = army_scene(FOUR_MEDIUM, visible_enemy_buildings=[enemy(600, "agun", 48, 47), enemy(601, "pbox", 52, 47)])

    assert targets_of(scene) == ["targets:", "T1: pillbox at (52,47), 11 cells, win 90%"]


def test_targets_block_4_medium_tanks_win_80_at_least_against_2_light_tanks_and_35_at_most_against_4_pillboxes():
    against_tanks = brief_of(army_scene(FOUR_MEDIUM, visible_enemies=TWO_LIGHT_TANKS)).targets
    against_boxes = brief_of(army_scene(FOUR_MEDIUM, visible_enemy_buildings=FOUR_BOXES)).targets

    # 3,400 against 1,400: r 2.43, 85.5%; and 3,400 against the four pillboxes together at twice their cost, 4,800.
    assert [target.chance for target in against_tanks] == [85]
    assert len(against_boxes) == 4 and all(target.chance <= 35 for target in against_boxes)
    assert [target.chance for target in against_boxes] == [35] * 4


def test_targets_block_a_group_counts_the_ground_defenses_that_reach_its_centroid():
    alone = brief_of(army_scene(FOUR_MEDIUM, visible_enemies=TWO_LIGHT_TANKS)).targets[0]
    covered = brief_of(
        army_scene(FOUR_MEDIUM, visible_enemies=TWO_LIGHT_TANKS, visible_enemy_buildings=[enemy(600, "pbox", 58, 46)])
    ).targets

    (group,) = [target for target in covered if target.kind == "group"]
    assert [record.id for record in group.defenses] == [600] and group.chance < alone.chance


def test_targets_block_only_the_units_that_can_hurt_a_target_are_weighed_against_it():
    dogs = [situations.unit(450 + n, "dog", 41, 49) for n in range(4)]
    with_dogs = brief_of(army_scene([*FOUR_MEDIUM, *dogs], visible_enemies=TWO_LIGHT_TANKS)).targets
    world = army_scene(FOUR_MEDIUM, visible_enemies=TWO_LIGHT_TANKS).agent.world
    tank = [world.enemy_memory[610]]

    assert [target.chance for target in with_dogs] == [85]
    assert fight.ratio_against_target([], tank, world, ARMY, tank) == 0.0
    assert fight.ratio_against_target([world.combat_units[400]], tank, world, ARMY) == 99.0


@pytest.mark.parametrize(
    "ratio, chance",
    [(0.0, 5), (0.2, 5), (0.5, 20), (0.708, 35), (1.0, 50), (1.5, 70), (2.43, 85), (4.0, 95), (99.0, 95)],
)
def test_targets_block_the_chance_is_a_multiple_of_5_between_5_and_95(ratio, chance):
    assert command_targets.win_chance(ratio) == chance


def test_targets_block_with_nothing_in_reach_the_block_reads_targets_none():
    scene = army_scene(FOUR_MEDIUM, visible_enemies=[enemy(620, "e1", 63, 47)])

    assert targets_of(scene) == ["targets: none"]


@pytest.mark.parametrize(
    "scene",
    [
        lambda: army_scene(tanks(6, 30, 47), tick=3000),
        lambda: army_scene(
            MIXED_ARMY,
            picks=ARMY_PICKS,
            visible_enemies=[*TWO_LIGHT_TANKS, *[enemy(620 + n, "e1", 46 + 5 * n, 56) for n in range(4)]],
            visible_enemy_buildings=FOUR_BOXES,
        ),
        lambda: army_scene(
            MIXED_ARMY, picks=ARMY_PICKS, peak=command_army.EnemyPeak(2950, 12450), threat_active=True, **FRONT
        ),
    ],
    ids=["nothing in reach", "four targets of six with picks", "a busy front, base attacked"],
)
def test_targets_block_the_prompt_stays_within_6000_characters(scene):
    built = scene()

    assert prompt_chars(built.question.request) <= 6000
    assert 1 <= len(targets_of(built)) <= 5


def test_targets_block_computing_the_targets_takes_at_most_2_ms_a_question():
    scene = army_scene(
        MIXED_ARMY,
        visible_enemies=[*FRONT["visible_enemies"], *[enemy(620 + n, "e1", 46 + 5 * n, 56) for n in range(4)]],
        visible_enemy_buildings=[*FRONT["visible_enemy_buildings"], *FOUR_BOXES],
    )
    world, ctx = scene.agent.world, scene.agent.ctx
    view = main_view(scene)
    rounds = 50

    started = time.perf_counter()
    for _ in range(rounds):
        command_targets.local_targets(view.body, view.center, world, ctx)
    elapsed_ms = (time.perf_counter() - started) * 1000 / rounds

    assert elapsed_ms <= 2.0


# -- the task options ----------------------------------------------------------------------------------------------

TWO_MEDIUM = tanks(2, 40, 47, unit_type="2tnk")
# The script's crush runs off, so that no run of its own is in force over the rifles before main's tanks.
NO_RUNS = dataclasses.replace(ARMY, combat=dataclasses.replace(ARMY.combat, crush_runs=False))
# Eight rifles around (47,47), all within 1.5 cells of it: 6 cells from the medium tank at (41,47).
EIGHT_RIFLES = [
    enemy(620 + n, "e1", 46 + dx, 46 + dy)
    for n, (dx, dy) in enumerate((dx, dy) for dy in range(3) for dx in range(3) if (dx, dy) != (1, 1))
]
TWO_ARTILLERY = [situations.unit(430 + n, "arty", 38, 48 + n) for n in range(2)]
ROCKETS_AND_RIFLES = [
    *[situations.unit(460 + n, "e3", 40 + n % 3, 46 + n // 3) for n in range(6)],
    *[situations.unit(470 + n, "e1", 40 + n, 49) for n in range(4)],
]


def light_tanks(hp: int, x: int = 52) -> list[dict]:
    """Four enemy light tanks in a column from (x, 46) at a share of health."""
    return [enemy(640 + n, "1tnk", x, 46 + n, hp) for n in range(4)]


def task_lines(scene: ArmyScene) -> list[str]:
    """The lines of the scene's question's task options, letter first."""
    return [f"{action.id}: {action.head}" for action in scene.question.actions if action.id in "JKL"]


def closed_gate_tasks(scene: ArmyScene) -> list[Action]:
    """The task options of main's actions in the scene's cycle with main's forward gate closed."""
    world, ctx = scene.agent.world, scene.agent.ctx
    actions = command_army.army_actions(brief_of(scene), False, NAMES, world, ctx)
    return [action for action in actions if action.kind in command_tasks.TASK_KINDS]


def tasks_of(scene: ArmyScene, keep=lambda target: True, targeted: frozenset[int] = frozenset()) -> list[Action]:
    """The task options for main's units in the scene's cycle with the forward gate open, on its targets that `keep`
    keeps and none on `targeted`, the rest of main's body weighed as main's question weighs it."""
    world, ctx = scene.agent.world, scene.agent.ctx
    found = brief_of(scene)
    units = [*found.view.body, *found.view.reinforcements]
    targets = [target for target in found.targets if keep(target)]
    return command_tasks.task_actions(
        targets,
        units,
        found.centroid,
        True,
        NAMES,
        world,
        ctx,
        targeted,
        rest_holds=lambda named: command_army.rest_holds(found, named, world, ctx),
    )


def test_task_options_eight_rifles_6_cells_from_two_healthy_medium_tanks_offer_j_with_the_2_tanks():
    scene = army_scene(TWO_MEDIUM, visible_enemies=EIGHT_RIFLES, tuning=NO_RUNS)

    run_over = scene.action("run_over")

    assert task_lines(scene) == ["J: run over 8 infantry at (47,47) with 2 tanks, win 80%"]
    assert sorted(run_over.units) == unit_ids(TWO_MEDIUM) and sorted(run_over.aim) == list(range(620, 628))
    assert (run_over.id, run_over.key, run_over.chance) == ("J", ("run_over",), 80)


@pytest.mark.parametrize(
    "units, hp, tanks_named",
    [(TWO_MEDIUM, 49, []), (tanks(4, 40, 47, unit_type="2tnk"), 100, [402, 401, 400])],
    ids=["tanks under crush_min_hp", "three of four tanks: ceil(8/3)"],
)
def test_task_options_j_takes_tanks_at_crush_min_hp_up_to_ceil_infantry_over_crush_per_tanks(units, hp, tanks_named):
    scene = army_scene([{**unit, "hp": hp} for unit in units], visible_enemies=EIGHT_RIFLES, tuning=NO_RUNS)

    named_tanks = [list(action.units) for action in scene.question.actions if action.kind == "run_over"]

    assert named_tanks == ([tanks_named] if tanks_named else [])


def test_task_options_j_is_not_offered_while_a_script_crush_run_is_in_force():
    running = army_scene(tanks(3, 40, 47, unit_type="2tnk"), visible_enemies=EIGHT_RIFLES)
    scene = army_scene(TWO_MEDIUM, visible_enemies=EIGHT_RIFLES, tuning=NO_RUNS)
    world, ctx = scene.agent.world, scene.agent.ctx
    ctx.crush_run = {400: (49, 47)}

    actions = command_army.army_actions(brief_of(scene), True, NAMES, world, ctx)

    # With crush_runs on the script's own run took three medium tanks, the planned min(3, ceil(8 / 3)) of the eight,
    # over the rifles in the scene's cycle.
    assert running.agent.ctx.crush_run and "run_over" not in running.kinds()
    assert "run_over" not in [action.kind for action in actions]


@pytest.mark.parametrize(
    "units, offered",
    [([*tanks(4, 40, 47), *TWO_ARTILLERY], True), (tanks(4, 40, 47), False)],
    ids=["two artillery in main", "no artillery"],
)
def test_task_options_a_pillbox_offers_k_with_main_s_2_artillery_and_none_without_artillery(units, offered):
    scene = army_scene(units, visible_enemy_buildings=[enemy(600, "pbox", 55, 47)])

    assert task_lines(scene) == (["K: kill pillbox at (55,47) with 2 artillery, win 95%"] if offered else [])
    assert not offered or (scene.action("kill_defense").units, scene.action("kill_defense").key) == (
        (430, 431),
        ("kill_defense", 600),
    )


def test_task_options_k_counts_the_defense_at_nothing_but_the_enemy_units_within_8_cells_of_it():
    guards = [enemy(650 + n, "3tnk", 57, 46 + n) for n in range(3)]
    scene = army_scene(
        [*tanks(4, 40, 47), *TWO_ARTILLERY],
        visible_enemy_buildings=[enemy(600, "pbox", 55, 47)],
        visible_enemies=guards,
    )

    # 1,700 of artillery against 3,450 of heavy tanks beside the pillbox: 0.49, 20%.
    assert "kill_defense" not in scene.kinds()
    assert command_targets.win_chance(1700 / 3450) == 20


@pytest.mark.parametrize("hp, offered", [(50, True), (100, False)], ids=["light tanks at half health", "at full"])
def test_task_options_four_light_tanks_offer_l_with_the_6_rocket_soldiers_and_none_under_50_percent(hp, offered):
    # The light tanks 17 cells from the body's center: no enemy within 15 cells of the rifles left with main.
    scene = army_scene(ROCKETS_AND_RIFLES, visible_enemies=light_tanks(hp, x=58))

    # 1,800 of rockets against 1,400 (at half health) or 2,800: 62% and 29%.
    assert task_lines(scene) == (["L: hit 4 light tank at (58,48) with 6 rocket soldier, win 60%"] if offered else [])
    assert not offered or sorted(scene.action("hit_group").units) == list(range(460, 466))


def test_task_options_l_names_at_most_8_counter_units_the_nearest_first():
    rockets = [situations.unit(460 + n, "e3", 40 + n % 5, 46 + n // 5) for n in range(10)]
    scene = army_scene(rockets, visible_enemies=light_tanks(50, x=58))

    named_units = scene.action("hit_group").units

    assert len(named_units) == 8 and set(named_units) == set(range(460, 470)) - {460, 465}


@pytest.mark.parametrize(
    "unit_type, armour, counters",
    [
        ("e3", "heavy", True),
        ("e1", "heavy", False),
        ("e1", "none", True),
        ("2tnk", "heavy", True),
        ("arty", "heavy", False),
    ],
)
def test_task_options_a_counter_does_1_5_times_its_mean_damage_over_the_armour_classes(unit_type, armour, counters):
    world = army_scene(TWO_MEDIUM).agent.world

    assert command_tasks.is_counter(unit_type, armour, world) is counters


@pytest.mark.parametrize("distance, gate_open, offered", [(15, True, True), (15, False, False), (8, False, True)])
def test_task_options_while_the_gate_is_closed_a_target_15_cells_away_gets_no_task_option(distance, gate_open, offered):
    units = [*tanks(4, 40, 47), *TWO_ARTILLERY]
    scene = army_scene(units, visible_enemy_buildings=[enemy(600, "pbox", 40 + distance, 47)])

    found = scene.question.actions if gate_open else closed_gate_tasks(scene)

    assert ("kill_defense" in [action.kind for action in found]) is offered


def every_kind_eligible() -> ArmyScene:
    """main of two medium tanks, two artillery pieces, six rocket soldiers and four V2 launchers, with eight rifles
    before its tanks, four light tanks at half health north of it and a pillbox north-east: a run over, a hit and a kill
    are each eligible, the artillery and the V2s holding main's fight while the hit's units are out."""
    units = [
        *TWO_MEDIUM,
        *TWO_ARTILLERY,
        *ROCKETS_AND_RIFLES[:6],
        *[situations.unit(450 + n, "v2rl", 37, 46 + n) for n in range(4)],
    ]
    north = [enemy(640 + n, "1tnk", 40, 36 + n, 50) for n in range(4)]
    return army_scene(
        units,
        visible_enemies=[*EIGHT_RIFLES, *north],
        visible_enemy_buildings=[enemy(600, "pbox", 55, 40)],
        tuning=NO_RUNS,
    )


def test_task_options_with_all_kinds_eligible_at_most_2_task_options_appear_j_first(monkeypatch):
    scene = every_kind_eligible()
    ctx = scene.agent.ctx

    offered = [action.kind for action in scene.question.actions if action.kind in command_tasks.TASK_KINDS]
    monkeypatch.setattr(command_tasks, "TASKS_MAX", 3)
    every = tasks_of(scene)

    assert [action.kind for action in every] == ["run_over", "hit_group", "kill_defense"]
    assert offered == ["run_over", "hit_group"]
    assert all(set(action.units) <= set(ctx.groups.members(MAIN)) for action in every)


def test_task_options_a_picked_task_is_the_task_pick_and_leaves_main_s_standing_action():
    scene = gated_main(tuning=NO_RUNS)
    units = [*TWO_MEDIUM, *tanks(4, 39, 49, first=410)]
    scene.cycle(1000, units, visible_enemies=EIGHT_RIFLES)
    scene.chooser.answer("J")

    scene.cycle(1005, units, visible_enemies=EIGHT_RIFLES)

    commander = scene.commander
    assert commander.standing.kind == "gather" and commander.task.action.kind == "run_over"
    assert commander.task.tick == 1005 and [pick.id for pick in commander.picks] == ["J"]


# -- the task detachments ------------------------------------------------------------------------------------------

# main of two medium tanks before the eight rifles and four heavy tanks beside them, worth 6,300 and gathered.
J_MAIN = [*TWO_MEDIUM, *tanks(4, 39, 49, first=410)]
# Four heavy tanks and two artillery pieces 10 cells from a pillbox at (59,47), within their firing band.
FAR_BOX = enemy(600, "pbox", 59, 47)
K_MAIN = [*tanks(4, 44, 47), *[situations.unit(480 + n, "arty", 49, 47 + n) for n in range(2)]]
ARTILLERY_IDS = {480, 481}
ROCKET_IDS = set(range(460, 466))
# Four light tanks at half health 4 cells east of the rocket soldiers, and an enemy rifle north of these, no part of
# their group.
HALF_LIGHT = light_tanks(50, x=45)
OUTSIDER = enemy(690, "e1", 40, 41)


def orders_naming(orders: list[dict], ids: set[int]) -> list[dict]:
    """The orders that name any of the units of `ids`."""
    return [order for order in orders if ids & set(order.get("units", ()))]


def task_events(match_record, kind: str) -> list[dict]:
    return [event for event in commander_events(match_record) if event["kind"] == kind]


def detached_main(
    units: list[dict],
    kind: str,
    *,
    tick: int = 1000,
    tuning: Tuning = ARMY,
    commanding: CommanderTuning | None = None,
    **observation,
) -> GatedMain:
    """main of these units commanded, its question asked at `tick` and answered with its option of a task kind, which
    the cycle 5 ticks later takes: a detachment is out."""
    scene = gated_main(tuning=tuning, commanding=commanding)
    scene.cycle(tick, units, **observation)
    scene.answer(kind)
    scene.cycle(tick + 5, units, **observation)
    assert scene.commander.task.action.kind == kind
    return scene


def test_task_detachment_a_chosen_j_moves_the_named_tanks_through_the_cluster_and_an_advance_leaves_them(
    match_record,
):
    scene = gated_main(tuning=NO_RUNS)
    scene.cycle(1000, J_MAIN, visible_enemies=EIGHT_RIFLES)
    scene.answer("run_over")
    running = scene.cycle(1005, J_MAIN, visible_enemies=EIGHT_RIFLES)
    named_tanks = set(scene.commander.task.action.units)
    scene.cycle(1010, J_MAIN, visible_enemies=EIGHT_RIFLES)
    scene.answer("advance")
    advancing = scene.cycle(1015, J_MAIN, visible_enemies=EIGHT_RIFLES)
    run_cells = {unit_id: order["to"] for order in running for unit_id in order["units"] if unit_id in named_tanks}
    there = [
        {**unit, "x": run_cells[unit["id"]][0], "y": run_cells[unit["id"]][1]} if unit["id"] in named_tanks else unit
        for unit in J_MAIN
    ]
    rejoined = scene.cycle(1020, there, visible_enemies=EIGHT_RIFLES)

    assert len(named_tanks) == 3 and scene.commander.standing.kind == "advance"
    assert {order["cmd"] for order in orders_naming(running, named_tanks)} == {"move"}
    assert set(run_cells) == named_tanks and all(cell[0] >= 48 for cell in run_cells.values())
    assert all(
        (order["cmd"], order["to"]) == ("move", run_cells[order["units"][0]])
        for order in orders_naming(advancing, named_tanks)
    )
    assert {order["cmd"] for order in orders_naming(rejoined, named_tanks)} == {"attack"}
    (start,) = task_events(match_record, "task_start")
    assert (start["task"], start["units"], start["target"], start["aim"]) == (
        "run_over",
        sorted(named_tanks),
        [47, 47],
        list(range(620, 628)),
    )
    assert [(end["task"], end["reason"], end["tick"]) for end in task_events(match_record, "task_end")] == [
        ("run_over", "done", 1020)
    ]


def test_task_detachment_a_run_over_out_is_the_one_crush_run_and_no_script_run_starts(match_record):
    # Eight rifles under a pillbox before three heavy tanks: no script run, but a run over is offered.
    covered = EIGHT_RIFLES
    nine_more = [enemy(700 + n, "e1", 35 + n % 3, 63 + n // 3) for n in range(9)]
    # The nine, before three medium tanks, are planned min(3, ceil(9 / 3)) = 3.
    units = [*tanks(3, 40, 47), *tanks(3, 36, 60, first=420, unit_type="2tnk")]
    box = {"visible_enemy_buildings": [enemy(600, "pbox", 52, 47)]}
    scenes = []
    for picked in (True, False):
        scene = commanded_main(units, "gather", visible_enemies=covered, **box)
        if picked:
            scene.agent.ctx.commander.task = TaskPick(scene.action("run_over"), 12500)
        next_cycle(scene, 12505, visible_enemies=[*covered, *nine_more])
        scenes.append(scene)
    detached, alone = scenes

    assert detached.agent.ctx.run_over_out and not detached.agent.ctx.crush_run
    assert not alone.agent.ctx.run_over_out and set(alone.agent.ctx.crush_run) == {420, 421, 422}
    skipped = [event["gate"] for event in match_record("combat") if event.get("reason") == "crush_skipped"]
    assert "run_active" in skipped


def test_task_detachment_a_run_over_picked_while_a_script_crush_run_is_in_force_is_dropped(match_record):
    scene = commanded_main(TWO_MEDIUM, "gather", tuning=NO_RUNS, visible_enemies=EIGHT_RIFLES)
    ctx = scene.agent.ctx
    run_over = scene.action("run_over")
    ctx.commander.task = TaskPick(run_over, 12500)
    # A script run that started after the question offered the run over.
    ctx.crush_run = {400: (49, 47)}

    orders = next_cycle(scene, 12505, visible_enemies=EIGHT_RIFLES)

    assert ctx.run_over_out is False and ctx.army.isdisjoint({400, 401})
    assert not [order for order in orders if order["cmd"] == "move" and order.get("to") == [49, 47]]
    assert task_events(match_record, "task_start") == []
    assert task_events(match_record, "dropped") == [
        {
            "kind": "dropped",
            "role": "main",
            "action_id": "J",
            "action": run_over.head,
            "tick": 12505,
            "reason": "not_offered",
        }
    ]


def test_task_detachment_a_chosen_l_attacks_the_group_s_units_alone(match_record):
    scene = detached_main(L_MAIN, "hit_group", visible_enemies=[*HALF_LIGHT, OUTSIDER])

    orders = scene.cycle(1010, L_MAIN, visible_enemies=[*HALF_LIGHT, OUTSIDER])

    named_units = set(scene.commander.task.action.units)
    attacks = [order for order in orders if order["cmd"] == "attack" and named_units & set(order["units"])]
    assert named_units == ROCKET_IDS
    assert {unit_id for order in attacks for unit_id in order["units"]} == ROCKET_IDS
    assert {order["target"] for order in attacks} <= {640, 641, 642, 643}
    assert all(order["cmd"] == "attack" for order in orders if named_units & set(order["units"]))


def test_task_detachment_a_chosen_k_fires_the_artillery_on_the_defense_from_beyond_its_range():
    scene = gated_main()
    scene.cycle(1000, K_MAIN, visible_enemy_buildings=[FAR_BOX])
    scene.answer("kill_defense")

    orders = scene.cycle(1005, K_MAIN, visible_enemy_buildings=[FAR_BOX])

    firing = [order for order in orders if ARTILLERY_IDS & set(order["units"])]
    by_id = {unit["id"]: unit for unit in K_MAIN}
    assert sorted((order["cmd"], order["target"], *order["units"]) for order in firing) == [
        ("attack", 600, 480),
        ("attack", 600, 481),
    ]
    assert all(cells.distance((by_id[unit_id]["x"], by_id[unit_id]["y"]), (59, 47)) > 6 for unit_id in ARTILLERY_IDS)


def test_task_detachment_the_retreat_override_moves_the_detachment_with_main_to_the_staging_point(
    monkeypatch, match_record
):
    batches = recorded_batches(monkeypatch)
    scene = detached_main(K_MAIN, "kill_defense", visible_enemy_buildings=[FAR_BOX])
    # Nine heavy tanks west of main: its four tanks lose there, 4,600 against 10,350, and none of the nine is near
    # the pillbox.
    west = [enemy(610 + n, "3tnk", 36 + n % 3, 44 + n // 3) for n in range(9)]
    for tick in range(1010, 1065, 5):
        scene.cycle(tick, K_MAIN, visible_enemy_buildings=[FAR_BOX], visible_enemies=west)

    (order,) = commander_orders(batches[-1])
    assert scene.commander.retreat.is_active and (order["cmd"], order["to"]) == ("move", [32, 48])
    assert ARTILLERY_IDS <= set(order["units"])
    assert [(end["task"], end["reason"]) for end in task_events(match_record, "task_end")] == [
        ("kill_defense", "pulled_back")
    ]


# Four medium tanks west of main's V2 launchers: those alone weigh 0.83 against them, with the rocket soldiers out on
# the hit 1.21.
WEST_MEDIUM = [enemy(700 + n, "2tnk", 35 + n % 3, 45 + n // 3) for n in range(4)]


@pytest.mark.parametrize("counts, pulled_back", [(True, False), (False, True)], ids=["switch on", "switch off"])
def test_override_counts_detachments_main_left_light_by_a_hit_is_weighed_with_it_and_not_pulled_back(
    match_record, counts, pulled_back
):
    commanding = dataclasses.replace(CommanderTuning(), override_counts_detachments=counts)
    scene = detached_main(L_MAIN, "hit_group", commanding=commanding, visible_enemies=HALF_LIGHT)

    for tick in range(1010, 1065, 5):
        scene.cycle(tick, L_MAIN, visible_enemies=[*HALF_LIGHT, *WEST_MEDIUM])

    assert scene.commander.retreat.is_active is pulled_back
    ended = [(end["task"], end["reason"]) for end in task_events(match_record, "task_end")]
    assert ended == ([("hit_group", "pulled_back")] if pulled_back else [])


def wrecked(enemies: list[dict]) -> list[dict]:
    return [{**record, "wreck": True} for record in enemies]


# A pillbox covering the four light tanks, 8.5 cells from the body's center: it stands with them, not against main.
COVERING_BOX = enemy(601, "pbox", 48, 50)
# The rocket soldiers and rifles, and four V2 launchers that counter nothing the light tanks wear: what stays with main
# while the rockets are out keeps main's fight above the retreat override's ratio.
L_MAIN = [*ROCKETS_AND_RIFLES, *[situations.unit(480 + n, "v2rl", 38, 46 + n) for n in range(4)]]
THREE_LOST = [unit for unit in L_MAIN if unit["id"] not in {460, 461, 462}]


@pytest.mark.parametrize(
    "ticks, units, observation, reason, killed, lost",
    [
        ((1010,), L_MAIN, {"visible_enemies": wrecked(HALF_LIGHT)}, "done", 2800, 0),
        ((1010,), THREE_LOST, {}, "failed", 0, 900),
        (
            tuple(range(1010, 1065, 5)),
            L_MAIN,
            {"visible_enemies": light_tanks(100, x=45), "visible_enemy_buildings": [COVERING_BOX]},
            "failed",
            0,
            0,
        ),
        ((1755,), L_MAIN, {}, "timeout", 0, 0),
    ],
    ids=["the group destroyed", "half the units lost", "the chance under 20% for 50 ticks", "30 s out"],
)
def test_task_detachment_done_failed_and_timeout_end_it_and_its_units_take_main_s_orders_again(
    match_record, ticks, units, observation, reason, killed, lost
):
    scene = detached_main(L_MAIN, "hit_group", visible_enemies=HALF_LIGHT)
    seen = {"visible_enemies": HALF_LIGHT, **observation}
    for tick in ticks:
        scene.cycle(tick, units, **seen)
    held = scene.agent.ctx.army
    scene.chooser.answer(None, TIMEOUT)
    scene.cycle(ticks[-1] + 5, units, **seen)
    scene.cycle(ticks[-1] + 10, units, **seen)

    asked = local.choice_prompt(scene.chooser.submitted[-1])
    in_main = [unit for unit in units if unit["id"] in scene.agent.ctx.groups.members(MAIN)]
    assert not held & ROCKET_IDS and f"main: {len(in_main)} units" in asked
    assert ROCKET_IDS & {unit["id"] for unit in in_main} == ROCKET_IDS & {unit["id"] for unit in units}
    (end,) = task_events(match_record, "task_end")
    assert (end["task"], end["reason"], end["tick"], end["killed"], end["lost"]) == (
        "hit_group",
        reason,
        ticks[-1],
        killed,
        lost,
    )


def test_task_detachment_base_defense_calling_main_home_ends_every_detachment(match_record):
    scene = detached_main(K_MAIN, "kill_defense", visible_enemy_buildings=[FAR_BOX])
    scene.agent.ctx.groups.recall(MAIN)

    orders = scene.cycle(1010, K_MAIN, visible_enemy_buildings=[FAR_BOX])

    assert naming(orders, ARTILLERY_IDS) == []
    assert [(end["task"], end["reason"]) for end in task_events(match_record, "task_end")] == [
        ("kill_defense", "recalled")
    ]


# main of K_MAIN and six rocket soldiers, a pillbox, four light tanks at half health south-east of main and a raid
# opportunity: a kill, a hit and raid trucks are offered.
FULL_MAIN = [*K_MAIN, *[situations.unit(460 + n, "e3", 44 + n % 3, 49 + n // 3) for n in range(6)]]
SOUTH_LIGHT = [enemy(640 + n, "1tnk", 52, 52 + n, 50) for n in range(4)]


def test_task_detachment_with_2_detachments_out_no_task_or_raid_trucks_option_is_offered():
    scene = gated_main(exposed_trucks=EXPOSED)
    seen = {"visible_enemies": SOUTH_LIGHT, "visible_enemy_buildings": [FAR_BOX]}
    scene.cycle(1000, FULL_MAIN, **seen)
    first = scene.offered()
    scene.answer("kill_defense")
    scene.cycle(1005, FULL_MAIN, **seen)
    scene.cycle(1010, FULL_MAIN, **seen)
    second = scene.offered()
    scene.answer("hit_group")
    scene.cycle(1015, FULL_MAIN, **seen)
    scene.cycle(1020, FULL_MAIN, **seen)

    out = set(scene.agent.ctx.army)
    assert {"kill_defense", "hit_group", "raid_trucks"} <= set(first)
    assert {"hit_group", "raid_trucks"} <= set(second) and "kill_defense" not in second
    assert not {"run_over", "kill_defense", "hit_group", "raid_trucks"} & set(scene.offered())
    assert ARTILLERY_IDS <= out and len(out) == 10


def test_task_detachment_whose_units_base_defense_took_one_by_one_ends_recalled(match_record):
    scene = detached_main(K_MAIN, "kill_defense", visible_enemy_buildings=[FAR_BOX])
    scene.agent.ctx.groups.set_unit_override(ARTILLERY_IDS)

    scene.cycle(1010, K_MAIN, visible_enemy_buildings=[FAR_BOX])

    assert [(end["task"], end["reason"]) for end in task_events(match_record, "task_end")] == [
        ("kill_defense", "recalled")
    ]


# -- the kill defense's checks ------------------------------------------------------------------------------------

BOX_AT_55 = enemy(600, "pbox", 55, 47)
K_UNITS = [*tanks(4, 40, 47), *TWO_ARTILLERY]


def kill_keys(actions) -> list[tuple]:
    return [action.key for action in actions if action.kind == "kill_defense"]


def test_kill_defense_checks_a_pillbox_with_a_tesla_coil_5_cells_beside_it_gets_no_k():
    alone = army_scene(K_UNITS, visible_enemy_buildings=[BOX_AT_55])
    covered = army_scene(K_UNITS, visible_enemy_buildings=[BOX_AT_55, enemy(601, "tsla", 60, 47)])
    world, ctx = covered.agent.world, covered.agent.ctx

    # The coil's reach, 7 and 0.5 cells, 5 cells off the pillbox: no cell within 11.5 of it is out of the coil's reach.
    assert siege.firing_band("arty", (55, 47), world, ctx.tuning.siege).is_empty
    assert kill_keys(alone.question.actions) == [("kill_defense", 600)]
    assert ("kill_defense", 600) not in kill_keys(covered.question.actions)


def test_kill_defense_checks_k_names_only_the_units_whose_band_is_not_empty():
    v2s = [situations.unit(450 + n, "v2rl", 38, 45 + n) for n in range(2)]
    # A tesla coil 3 cells off the pillbox: the artillery's band runs from 10.5 to 11.5 cells, a V2's would end at 9.5.
    scene = army_scene([*K_UNITS, *v2s], visible_enemy_buildings=[BOX_AT_55, enemy(601, "tsla", 58, 47)])
    world, ctx = scene.agent.world, scene.agent.ctx

    (kill,) = tasks_of(scene, keep=lambda target: target.members[0].id == 600)

    assert not siege.firing_band("arty", (55, 47), world, ctx.tuning.siege).is_empty
    assert siege.firing_band("v2rl", (55, 47), world, ctx.tuning.siege).is_empty
    assert kill.units == (430, 431) and "with 2 artillery" in kill.head


@pytest.mark.parametrize("seconds, offered", [(30, False), (19, True)], ids=["seen 30 s ago", "seen 19 s ago"])
def test_kill_defense_checks_a_pillbox_last_seen_30_s_ago_gets_no_k(seconds, offered):
    seen_tick = 12500 - seconds * 25
    scene = army_scene(K_UNITS, tick=seen_tick, visible_enemy_buildings=[BOX_AT_55])
    next_cycle(scene, 12500, visible_enemy_buildings=[])
    world, ctx = scene.agent.world, scene.agent.ctx

    actions = command_army.army_actions(brief_of(scene), True, NAMES, world, ctx)

    box = world.enemy_memory[600]
    assert (box.last_seen_tick, box.visible) == (seen_tick, False)
    assert (("kill_defense", 600) in kill_keys(actions)) is offered


# main of two artillery pieces alone, worth 1,700: its forward gate stays closed, and the pillbox 9 cells east of its
# centroid is near enough for a task and out of its reach (6 and 2 cells) from the centroid.
TWO_GUNS = [situations.unit(480 + n, "arty", 40, 47 + n) for n in range(2)]
TWO_MORE_GUNS = [situations.unit(482 + n, "arty", 40, 49 + n) for n in range(2)]
BOX_AT_49 = enemy(600, "pbox", 49, 47)


def test_kill_defense_checks_with_a_k_detachment_out_on_a_pillbox_k_on_it_is_not_offered_again(match_record):
    scene = gated_main()
    seen = {"visible_enemy_buildings": [BOX_AT_49]}
    scene.cycle(1000, TWO_GUNS, **seen)
    first = scene.offered()
    scene.answer("kill_defense")
    scene.cycle(1005, TWO_GUNS, **seen)
    scene.cycle(1010, [*TWO_GUNS, *TWO_MORE_GUNS], **seen)
    world, ctx = scene.agent.world, scene.agent.ctx
    free = [world.combat_units[unit_id] for unit_id in (482, 483)]
    found = command_army.brief(free, NAMES, command_army.EnemyPeak(), world, ctx)

    unheld = command_army.army_actions(found, False, NAMES, world, ctx)
    held = command_army.army_actions(found, False, NAMES, world, ctx, targeted=frozenset({600}))

    (start,) = task_events(match_record, "task_start")
    assert "kill_defense" in first and start["aim"] == [600] and start["units"] == [480, 481]
    assert not scene.commander.gate.is_open and "kill_defense" not in scene.offered()
    assert kill_keys(unheld) == [("kill_defense", 600)] and kill_keys(held) == []


def test_kill_defense_checks_no_task_option_is_offered_on_an_enemy_a_detachment_is_on():
    scene = army_scene(ROCKETS_AND_RIFLES, visible_enemies=light_tanks(50, x=58))

    free = tasks_of(scene)
    held = tasks_of(scene, targeted=frozenset({641}))

    assert [action.kind for action in free] == ["hit_group"] and held == []


def kill_ends(match_record) -> list[tuple]:
    return [(end["task"], end["reason"], end["tick"], end["killed"]) for end in task_events(match_record, "task_end")]


def test_kill_defense_checks_a_k_detachment_whose_pillbox_goes_unseen_20_s_and_50_ticks_ends_failed(match_record):
    # The pillbox was last seen at 1005, when the detachment formed: unseen 20 s from 1505, failed 50 ticks later.
    scene = detached_main(K_MAIN, "kill_defense", visible_enemy_buildings=[FAR_BOX])
    for tick in (1010, 1500, 1505, 1554):
        scene.cycle(tick, K_MAIN)
    going_on = scene.agent.ctx.army & ARTILLERY_IDS
    scene.cycle(1555, K_MAIN)

    assert going_on == ARTILLERY_IDS and scene.agent.world.enemy_memory[600].last_seen_tick == 1005
    assert kill_ends(match_record) == [("kill_defense", "failed", 1555, 0)]


def test_kill_defense_checks_a_k_detachment_whose_pillbox_a_tesla_coil_covers_ends_failed_50_ticks_later(match_record):
    # A tesla coil 5 cells beyond the pillbox: the band of the artillery against it is empty from 1010 on.
    covered = {"visible_enemy_buildings": [FAR_BOX, enemy(601, "tsla", 64, 47)]}
    scene = detached_main(K_MAIN, "kill_defense", visible_enemy_buildings=[FAR_BOX])
    for tick in (1010, 1055):
        scene.cycle(tick, K_MAIN, **covered)
    going_on = scene.agent.ctx.army & ARTILLERY_IDS
    scene.cycle(1060, K_MAIN, **covered)

    assert going_on == ARTILLERY_IDS and kill_ends(match_record) == [("kill_defense", "failed", 1060, 0)]


def test_kill_defense_checks_30_ticks_unseen_then_30_ticks_with_an_empty_band_do_not_fail_a_k_detachment(match_record):
    # Unseen 20 s from 1505 to 1534; seen again from 1535 with a tesla coil 5 cells beyond it, emptying the band.
    covered = {"visible_enemy_buildings": [FAR_BOX, enemy(601, "tsla", 64, 47)]}
    scene = detached_main(K_MAIN, "kill_defense", visible_enemy_buildings=[FAR_BOX])
    for tick in (1010, 1505, 1520, 1534):
        scene.cycle(tick, K_MAIN)
    for tick in (1535, 1550, 1564):
        scene.cycle(tick, K_MAIN, **covered)
    going_on = scene.agent.ctx.army & ARTILLERY_IDS
    scene.cycle(1585, K_MAIN, **covered)

    assert going_on == ARTILLERY_IDS and kill_ends(match_record) == [("kill_defense", "failed", 1585, 0)]


# A harvester of ours 2 cells from the pillbox: the world forgets a building out of sight that an own unit stands that
# near for 50 ticks.
NEAR_BOX = situations.unit(700, "harv", 57, 47)


@pytest.mark.parametrize(
    "later, reason, killed",
    [
        ([(1010, [*K_MAIN, NEAR_BOX], {}), (1060, [*K_MAIN, NEAR_BOX], {})], "failed", 0),
        ([(1010, K_MAIN, {"visible_enemy_buildings": wrecked([FAR_BOX])})], "done", 600),
    ],
    ids=["forgotten out of sight", "its wreck seen where it stood in sight"],
)
def test_kill_defense_checks_a_target_that_leaves_memory_unseen_ends_failed_not_done(
    match_record, later, reason, killed
):
    scene = detached_main(K_MAIN, "kill_defense", visible_enemy_buildings=[FAR_BOX])
    for tick, units, observation in later:
        scene.cycle(tick, units, **observation)

    assert 600 not in scene.agent.world.enemy_memory
    assert kill_ends(match_record) == [("kill_defense", reason, later[-1][0], killed)]


# Another raid opportunity, a harvester at another ore field.
OTHER_TRUCKS = dataclasses.replace(
    FAR_TRUCKS, field=(40, 14), harvester_count=1, harvester_ids=(652,), nearest_harvester=(40, 16)
)


def test_kill_defense_checks_a_raid_is_on_its_harvesters_and_no_raid_trucks_is_offered_on_them_while_it_is_out():
    built = army_scene(tanks(8), visible_enemies=FRONT["visible_enemies"], exposed_trucks=(FAR_TRUCKS, OTHER_TRUCKS))
    world, ctx = built.agent.world, built.agent.ctx
    raid = built.action("raid_trucks")
    main = {unit_id: world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)}
    out = TruckRaid.formed(TaskPick(raid, 12500), main, world)

    again = command_army.army_actions(brief_of(built), True, NAMES, world, ctx, targeted=out.targets)

    assert raid.aim == (650, 651) and out.targets == {650, 651}
    assert [action.head for action in again if action.kind == "raid_trucks"] == [
        "raid 1 trucks at (40,16), guard 0, seen 4s ago"
    ]


# -- the hit group's rest check -----------------------------------------------------------------------------------

# Four heavy tanks, the counters a hit on enemy tanks names, and artillery that counters none of them.
FOUR_HEAVY = [situations.unit(400 + n, "3tnk", 40 + n % 2, 46 + n // 2) for n in range(4)]
THREE_GUNS = [situations.unit(430 + n, "arty", 38, 46 + n) for n in range(3)]
TEN_GUNS = [situations.unit(430 + n, "arty", 40 + n % 2, 48 + n // 2) for n in range(10)]
# Four light tanks east of main and two heavy tanks south-east of them, a group of their own: 5,100 together.
EAST_LIGHT = [enemy(640 + n, "1tnk", 52, 46 + n) for n in range(4)]
SOUTH_HEAVY = [enemy(650 + n, "3tnk", 50 + n, 56) for n in range(2)]


def rest_of(scene: ArmyScene, kind: str = "hit_group") -> float | None:
    """fight_here of main's body without the units of the scene's task option of a kind, built with no rest check, at
    the rest's center."""
    world, ctx = scene.agent.world, scene.agent.ctx
    found = brief_of(scene)
    units = [*found.view.body, *found.view.reinforcements]
    every = command_tasks.task_actions(
        found.targets, units, found.centroid, True, NAMES, world, ctx, rest_holds=lambda named: True
    )
    (task,) = [action for action in every if action.kind == kind]
    rest = tuple(unit for unit in found.view.body if unit.id not in task.units)
    view = command_army.ArmyView(rest, found.view.reinforcements, cells.nearest_cell(*fight.centroid(rest)))
    return command_army.fight_here(view, None, world, ctx)


@pytest.mark.parametrize(
    "units, whole, rest, offered",
    [([*FOUR_HEAVY, *THREE_GUNS], 1.40, 0.65, False), ([*FOUR_HEAVY, *TEN_GUNS], 2.57, 1.67, True)],
    ids=["7 units at 1.4x, the rest under 1.0", "14 units at 2.6x, the rest at 1.7x"],
)
def test_hit_group_rest_l_is_offered_only_while_main_s_body_without_its_units_reads_1_0_or_more(
    units, whole, rest, offered
):
    scene = army_scene(units, visible_enemies=[*EAST_LIGHT, *SOUTH_HEAVY])

    assert (brief_of(scene).fight, rest_of(scene)) == (pytest.approx(whole, abs=0.01), pytest.approx(rest, abs=0.01))
    assert ("hit_group" in scene.kinds()) is offered


@pytest.mark.parametrize(
    "enemies, chance, offered",
    [
        ([enemy(640 + n, "1tnk", 58, 46 + n) for n in range(4)], 75, True),
        ([enemy(640 + n, "3tnk", 58, 46 + n) for n in range(5)], 40, False),
    ],
    ids=["four light tanks, 75%", "five heavy tanks, 40%"],
)
def test_hit_group_rest_with_no_enemy_near_the_rest_l_is_offered_at_its_own_chance_of_50_or_more(
    enemies, chance, offered
):
    # The enemy 17 cells and more from the body's center: listed as a target, but no enemy within 15 cells of it.
    scene = army_scene([*FOUR_HEAVY, *THREE_GUNS], visible_enemies=enemies)
    world, ctx = scene.agent.world, scene.agent.ctx
    found = brief_of(scene)
    (target,) = found.targets
    heavy = [unit for unit in found.view.body if unit.type == "3tnk"]

    assert found.fight is None and command_tasks.task_chance("hit_group", heavy, target, world, ctx) == chance
    assert ("hit_group" in scene.kinds()) is offered and (not offered or rest_of(scene) is None)


def test_hit_group_rest_an_l_answer_the_rest_of_main_no_longer_bears_is_dropped_not_offered(match_record):
    scene = gated_main()
    units = [*FOUR_HEAVY, *TEN_GUNS]
    scene.cycle(1000, units, visible_enemies=[*EAST_LIGHT, *SOUTH_HEAVY])
    hit = next(option for option in scene.chooser.submitted[-1].options if option.id == "L")
    scene.answer("hit_group")
    # Four more heavy tanks join the two: the guns left with main would stand at 8,500 against 9,700.
    more_heavy = [enemy(652 + n, "3tnk", 50 + n % 2, 57 + n // 2) for n in range(4)]

    scene.cycle(1005, units, visible_enemies=[*EAST_LIGHT, *SOUTH_HEAVY, *more_heavy])

    assert scene.commander.task is None and not scene.agent.ctx.army & {400, 401, 402, 403}
    assert task_events(match_record, "task_start") == []
    assert task_events(match_record, "dropped") == [
        {
            "kind": "dropped",
            "role": "main",
            "action_id": "L",
            "action": head_of(hit.description),
            "tick": 1005,
            "reason": "not_offered",
        }
    ]


# -- the regroup gather -------------------------------------------------------------------------------------------

# Eight heavy tanks by the staging point, worth 9,200: main's forward gate opens on them.
EIGHT_HOME = tanks(8, 33, 46)
DEEP_IDS = {400, 401, 402, 403}
# Two enemy rifles in the enemy's base, 1 to 3 cells east of four tanks of main there: in their weapon range.
DEEP_RIFLES = [enemy(610 + n, "e1", 71, 47 + n) for n in range(2)]
ENEMY_YARD = enemy(640, "fact", 74, 48)


def deep_tanks(hp: int = 100) -> list[dict]:
    """The first four of EIGHT_HOME in the enemy's base, at a share of health."""
    return tanks(4, 68, 47, hp=hp)


def opened_on_eight() -> GatedMain:
    scene = gated_main()
    scene.cycle(1000, EIGHT_HOME)
    assert scene.commander.gate.is_open
    return scene


def regrouped() -> tuple[GatedMain, list[dict]]:
    """main's gate opened on EIGHT_HOME, then closed for a regroup at 1005, when four of the tanks stand at 80% in the
    enemy's base, the other four lost: 3,680, under half of 9,200; and the orders of that cycle."""
    scene = opened_on_eight()
    orders = scene.cycle(1005, deep_tanks(80), visible_enemies=DEEP_RIFLES, visible_enemy_buildings=[ENEMY_YARD])
    return scene, orders


def test_regroup_gather_after_a_regroup_close_4_tanks_in_contact_in_the_enemy_base_move_home_and_get_no_attack(
    match_record,
):
    scene, orders = regrouped()

    commander = scene.commander
    assert not commander.gate.is_open and commander.is_regrouping and commander.standing.kind == "gather"
    assert naming(orders, DEEP_IDS) == [("move", sorted(DEEP_IDS), [32, 48])]
    assert not [order for order in orders if order["cmd"] == "attack"]


def test_regroup_gather_a_gather_the_model_picks_while_the_gate_is_open_leaves_units_in_contact_firing():
    scene = opened_on_eight()
    scene.answer("gather")

    orders = scene.cycle(1005, [*deep_tanks(), *EIGHT_HOME[4:]], visible_enemies=DEEP_RIFLES)

    commander = scene.commander
    firing = {unit_id for order in orders if order["cmd"] == "attack" for unit_id in order["units"]}
    assert commander.gate.is_open and not commander.is_regrouping and commander.standing.kind == "gather"
    assert firing and firing <= DEEP_IDS
    assert not [order for order in orders if order["cmd"] == "move" and firing & set(order["units"])]


def test_regroup_gather_the_event_marks_the_kind_regroup_after_the_gate_s_closing(match_record):
    scene, _ = regrouped()
    gather = scene.commander.standing

    events = [event for event in commander_events(match_record) if event["tick"] == 1005]

    assert [(event["kind"], event.get("reason")) for event in events] == [("gate", "regroup"), ("regroup", None)]
    assert events[1] == {"kind": "regroup", "role": "main", "action_id": "D", "action": gather.head, "tick": 1005}
    assert gather.head.startswith("gather at staging (32,48)")


def test_regroup_gather_an_answer_arriving_as_the_gate_closes_for_a_regroup_is_not_carried_out_and_no_pick(
    match_record,
):
    # Two rifles 6 cells from the eight tanks at home: an attack is offered at 1000, and again on the deep rifles at 1005.
    scene = gated_main()
    scene.cycle(1000, EIGHT_HOME, visible_enemies=[enemy(620 + n, "e1", 41, 46 + n) for n in range(2)])
    assert scene.commander.gate.is_open
    scene.answer("attack")

    scene.cycle(1005, deep_tanks(80), visible_enemies=DEEP_RIFLES, visible_enemy_buildings=[ENEMY_YARD])

    commander = scene.commander
    assert (commander.standing.kind, commander.is_regrouping, list(commander.picks)) == ("gather", True, [])
    assert [event["kind"] for event in commander_events(match_record) if event["tick"] == 1005] == ["gate", "regroup"]
    assert match_record("fallback")[-1] == {
        "intended_source": "red_alert_commander",
        "reason": "regroup",
        "used": "script",
    }


def test_regroup_gather_ends_when_another_picked_action_is_carried_out_or_the_gate_opens():
    picked, _ = regrouped()
    picked.chooser.answer(None, TIMEOUT)
    seen = {"visible_enemies": DEEP_RIFLES, "visible_enemy_buildings": [ENEMY_YARD]}
    for tick in (1010, 1015):
        picked.cycle(tick, deep_tanks(80), **seen)
    picked.answer("attack")
    picked.cycle(1020, deep_tanks(80), **seen)
    opened, _ = regrouped()
    # The counterattack off: with it, main gathered at staging in the regroup would strike out instead.
    ctx = opened.agent.ctx
    ctx.tuning = dataclasses.replace(ctx.tuning, combat=dataclasses.replace(ctx.tuning.combat, counterattack=False))
    # The four tanks back by the staging point with four more: 8,280, all of it gathered.
    opened.cycle(1010, [*tanks(4, 33, 46, hp=80), *EIGHT_HOME[4:]])

    assert (picked.commander.standing.kind, picked.commander.is_regrouping) == ("attack", False)
    assert (opened.commander.gate.is_open, opened.commander.is_regrouping) == (True, False)


def test_regroup_gather_over_at_staging_main_ahead_counterattacks_the_enemy_yard_no_defense_reaches(match_record):
    scene, _ = regrouped()

    # The four tanks back by the staging point with four more, 8,280, against two rifles seen: the regroup is over.
    orders = scene.cycle(1010, [*tanks(4, 33, 46, hp=80), *EIGHT_HOME[4:]])

    (manager,) = [manager for manager in scene.agent.managers if isinstance(manager, CombatManager)]
    counter = [data for data in match_record("combat") if data["reason"] == "counterattack"]
    assert manager._strike.target_id == 640 and [data["phase"] for data in counter] == ["start", "due"]
    assert counter[1]["why"] == "regrouped"
    assert [order for order in orders if order["cmd"] == "attack_move" and order["to"] == [74, 48]]


def silent_main(script_fallback: bool = True) -> GatedMain:
    """main's gate opened on EIGHT_HOME at tick 1000 under PRESS, the enemy yard known, and the model silent since."""
    scene = gated_main(commanding=CommanderTuning(script_fallback=script_fallback))
    scene.cycle(1000, EIGHT_HOME, visible_enemy_buildings=[ENEMY_YARD])
    assert scene.commander.gate.is_open
    return scene


def test_script_fallback_after_8_s_without_a_model_answer_main_takes_a_forward_action_by_script(match_record):
    scene = silent_main()
    scene.cycle(1000 + 8 * 25 - 5, EIGHT_HOME)
    before = (scene.commander.is_scripted, scene.commander.standing.kind)

    scene.cycle(1000 + 8 * 25, EIGHT_HOME)

    commander = scene.commander
    assert before == (False, "gather")
    assert commander.is_scripted and commander.standing.kind in ("attack_expansion", "advance", "attack")
    events = [event for event in commander_events(match_record) if event["kind"] in ("script_fallback", "action")]
    assert events[0] == {"kind": "script_fallback", "phase": "start", "role": "main", "tick": 1200}
    assert events[1]["source"] == "script"


def test_script_fallback_a_model_answer_used_ends_it(match_record):
    scene = silent_main()
    scene.cycle(1200, EIGHT_HOME)
    # The answer to the question of tick 1000 is too old to be carried out, but the model is heard from again.
    scene.answer("gather")

    scene.cycle(1205, EIGHT_HOME)

    assert scene.commander.is_scripted is False
    phases = [event["phase"] for event in commander_events(match_record) if event["kind"] == "script_fallback"]
    assert phases == ["start", "end"]


def test_script_fallback_with_the_switch_off_main_keeps_its_gather():
    scene = silent_main(script_fallback=False)

    scene.cycle(1200, EIGHT_HOME)

    assert (scene.commander.is_scripted, scene.commander.standing.kind) == (False, "gather")


def test_script_fallback_under_hold_the_script_picks_gather_or_hold():
    hold = gated_main(commanding=CommanderTuning())
    hold.agent._plan_selector = FixedPlan("HOLD")
    hold.cycle(1000, EIGHT_HOME, visible_enemy_buildings=[ENEMY_YARD])

    hold.cycle(1200, EIGHT_HOME)

    assert hold.commander.is_scripted and hold.commander.standing.kind in ("gather", "hold")


# -- a whole match with the task options answered ------------------------------------------------------------------

# The letters the stand-in model answers with first: the task options (run over, kill, hit), then main's advance.
TASK_LETTERS = ("J", "K", "L", "I")


class TaskFirstModel(FirstOptionModel):
    """Stands in for the local model server the commander's chain asks: every answer is the first task option offered
    (run over, kill or hit), else main's advance, so that main meets the enemy, else the first option."""

    def answer(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        offered = body["response_format"]["json_schema"]["schema"]["properties"]["choice"]["enum"]
        choice = next((letter for letter in TASK_LETTERS if letter in offered), offered[0])
        self.bodies.append(body)
        content = json.dumps({"choice": choice})
        return httpx.Response(
            200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
        )


# When the targets of the task options show up, always in sight, on main's way from the staging point to the enemy:
# four light tanks at half health, a pillbox, and eight rifles under another pillbox 20 cells from the first, too far
# to empty its firing band.
TARGETS_AT_SECONDS = 300
RIFLE_BLOCK = [(dx, dy) for dy in range(3) for dx in range(3) if (dx, dy) != (1, 1)]


def three_guard_rifles(tuning: Tuning) -> Tuning:
    """A tuning with the lean opening's guard of three rifles, the economy the task match's targets were placed for:
    with one rifle main meets no hit group in it."""
    return dataclasses.replace(tuning, opening=dataclasses.replace(tuning.opening, lean_guard_rifles=3))


def task_targets(game: FakeRedAlert) -> None:
    for n in range(4):
        game.place_enemy("1tnk", 46, 46 + n, hp=50, visible=True)
    game.place_enemy("pbox", 56, 40, visible=True)
    for dx, dy in RIFLE_BLOCK:
        game.place_enemy("e1", 56 + dx, 54 + dy, visible=True)
    game.place_enemy("pbox", 60, 60, visible=True)


@pytest.fixture(scope="module")
def tasked_match() -> CommandedMatch:
    """One whole 20-minute fake match for the Allied start with the shipped tuning but the reinforcements held home
    (with which main meets no group to hit in this match), the attack's approach and the commit when the armies cross
    and the military recovery (the match these task options were worked out on), the model commander on, the targets
    of TARGETS_AT_SECONDS placed, and the stand-in model answering the first task option whenever one is offered."""
    model = TaskFirstModel()
    scenario = [at_seconds(TARGETS_AT_SECONDS, task_targets)]
    shipped = Tuning()
    tuning = dataclasses.replace(
        shipped,
        combat=dataclasses.replace(shipped.combat, **APPROACH_OFF),
        upkeep=dataclasses.replace(shipped.upkeep, military_recovery=False),
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(situations, "own_agent", functools.partial(_commanded_agent, model))
        played = whole_match.play_whole_match(reinforce_hold_off(three_guard_rifles(tuning)), scenario=scenario)
    return CommandedMatch(played, model)


def test_task_detachment_whole_match_runs_without_an_unhandled_error_or_a_refused_batch(tasked_match):
    played = tasked_match.played

    assert played.agent.ctx.commander is not None and len(played.compute_ms) == whole_match.WHOLE_MATCH_STEPS
    assert [error for error in played.data_of("error") if error["type"] == "unhandled"] == []
    assert played.run.invalid_actions == 0 and played.game.invalid_actions == 0


def test_task_detachment_whole_match_compute_p95_is_at_most_50_ms(tasked_match):
    assert tasked_match.played.compute_p95_ms <= whole_match.COMPUTE_P95_MS


def test_task_detachment_whole_match_task_options_were_picked_and_their_detachments_ended(tasked_match):
    bodies = tasked_match.model.bodies
    events = tasked_match.played.data_of("commander")
    starts = [event for event in events if event["kind"] == "task_start"]
    ends = [event for event in events if event["kind"] == "task_end"]
    army = [body for body in bodies if body["messages"][0]["content"] == command_army.SYSTEM_TEXT]

    # How many task detachments are out after each commander event.
    out = itertools.accumulate((event["kind"] == "task_start") - (event["kind"] == "task_end") for event in events)
    assert all(len(body["messages"][0]["content"]) + len(body["messages"][1]["content"]) <= 6000 for body in army)
    assert starts and len(starts) - 2 <= len(ends) <= len(starts)
    assert (
        {"kill_defense", "hit_group"}
        <= {event["task"] for event in starts}
        <= {"run_over", "kill_defense", "hit_group"}
    )
    assert {event["reason"] for event in ends} <= {"done", "failed", "timeout", "pulled_back", "recalled"}
    assert max(out) <= 2


# -- forward staging under the commander's forward actions ---------------------------------------------------------

# Two enemy light tanks in sight 4 cells short of the enemy's start, main's objective while no building is known: their
# reach, 4.75 and 3, starts at column 63 of main's way there.
AT_THE_START = [enemy(610 + n, "1tnk", 70, 47 + 2 * n) for n in range(2)]


def test_forward_staging_an_advance_stops_main_out_of_the_reach_of_enemy_tanks_at_the_objective_without_its_order():
    army = tanks(6, 40, 47)
    scene = army_scene(army, visible_enemies=AT_THE_START)

    orders = commanded_orders(scene, "advance", visible_enemies=AT_THE_START)

    assert scene.action("advance").cell == (74, 48)
    assert orders == [{"cmd": "attack_move", "units": unit_ids(army), "to": [62, 48]}]
    assert scene.combat.commanded_order is None


@pytest.mark.parametrize(
    "x, orders",
    [(48, [("attack", 4), ("attack", 2)]), (57, [("attack_move", 6)])],
    ids=["7 cells off", "16 cells off"],
)
def test_forward_staging_an_attack_beyond_10_cells_stages_main_and_one_within_10_attacks_at_once(x, orders):
    group = [enemy(610 + n, "1tnk", x, 46 + n) for n in range(2)]
    scene = army_scene(tanks(6, 40, 47), visible_enemies=group)

    sent = commanded_orders(scene, "attack", visible_enemies=group)

    # Which tanks take which light tank is the focus fire's pairing (see `targeting`); all six go, 4 and 2 or together.
    assert [(order["cmd"], len(order["units"])) for order in sent] == orders
    assert sorted(unit for order in sent for unit in order["units"]) == list(range(400, 406))
    staged = [order["to"] for order in sent if order["cmd"] == "attack_move"]
    assert all(math.dist(cell, (x, 46)) >= 4.75 + 3 for cell in staged)


def test_forward_staging_a_siege_stages_main_9_cells_from_the_pillbox_with_the_v2s_behind_the_screen():
    army = [*tanks(4, 40, 47), *[situations.unit(430 + n, "v2rl", 38, 48 + n) for n in range(2)]]
    scene = army_scene(army, visible_enemy_buildings=[enemy(600, "pbox", 60, 47)])

    orders = commanded_orders(scene, "siege")

    assert {"cmd": "attack_move", "units": [400, 401, 402, 403], "to": [51, 47]} in orders
    v2_moves = [order["to"] for order in orders if set(order["units"]) <= {430, 431}]
    assert len(v2_moves) == 2 and all(cell[0] < 40 for cell in v2_moves)


def test_forward_staging_a_flank_stages_main_on_its_last_leg_only():
    covered = [building for building in FRONT["visible_enemy_buildings"] if building["type"] != "fact"]
    scene = army_scene(tanks(6, 40, 47), visible_enemy_buildings=covered)
    flank = scene.action("flank")
    # Half of main at the side waypoint, half 8 cells behind it: main's centroid within 6 cells of the waypoint.
    x, y = flank.via
    spread = [*tanks(3, x - 1, y - 1), *tanks(3, x - 9, y - 1, first=403)]

    first = commanded_orders(scene, "flank")
    last_leg = commanded_orders(scene, "flank", tick=12510, units=spread)

    # The last cell of the way on (72, 52) out of the gun turret's reach at (66, 52), 6.5 and 3.
    assert [(order["cmd"], order["to"]) for order in first] == [("move", list(flank.via))]
    assert last_leg == [{"cmd": "attack_move", "units": [403, 404, 405], "to": [58, 58]}]


def test_forward_staging_off_an_advance_marches_on_the_objective_through_the_enemy_tanks():
    army = tanks(6, 40, 47)
    scene = army_scene(army, visible_enemies=AT_THE_START, tuning=UNSTAGED_ARMY)

    orders = commanded_orders(scene, "advance", visible_enemies=AT_THE_START)

    assert orders == [{"cmd": "attack_move", "units": unit_ids(army), "to": [74, 48]}]


# -- forward staging: the units joining main through the gate meet at the staging cell -----------------------------


def staging_gate() -> GatedMain:
    """main of FIVE_THOUSAND commanded, its forward gate opened at tick 1000 and its advance on the enemy's start, where
    AT_THE_START stand, staged at (62, 47) from tick 1005."""
    scene = gated_main()
    scene.cycle(1000, FIVE_THOUSAND, visible_enemies=AT_THE_START)
    scene.answer("advance")
    scene.cycle(1005, FIVE_THOUSAND, visible_enemies=AT_THE_START)
    assert scene.commander.standing.kind == "advance"
    return scene


def test_forward_staging_a_unit_joining_main_through_the_open_gate_is_moved_to_the_staging_cell():
    scene = staging_gate()

    orders = scene.cycle(1010, [*FIVE_THOUSAND, unit(500, "2tnk", 20, 52)], visible_enemies=AT_THE_START)

    assert naming(orders, {500}) == [("move", [500], [62, 47])]


def test_forward_staging_joining_units_worth_1000_at_the_staging_cell_go_on_together_and_one_of_850_waits():
    scene = staging_gate()
    medium, light = unit(500, "2tnk", 61, 47), unit(501, "1tnk", 61, 49)

    alone = scene.cycle(1010, [*FIVE_THOUSAND, medium], visible_enemies=AT_THE_START)
    together = scene.cycle(1015, [*FIVE_THOUSAND, medium, light], visible_enemies=AT_THE_START)

    assert naming(alone, {500}) == []
    assert naming(together, {500, 501}) == [("attack_move", [500, 501], [43, 46])]


# -- step commit: the commander's picks and the retreat override with main's stepped movement -------------------------

# The shipped stepped movement and step commit on, march_when_clear and vehicle_repair off as in ARMY.
STEPPED_ARMY = ARMY
# An enemy war factory 40 cells east of the staging point: the advance's objective, far enough to step.
FAR_FACTORY = {"visible_enemy_buildings": [enemy(641, "weap", 72, 48)]}
FIRST_STEP = (42, 48)


def stepped_command(monkeypatch, tuning: Tuning = STEPPED_ARMY) -> tuple[MainCommand, CombatManager]:
    """A commanded main over a stub chooser with the combat manager, its stepped movement as the tuning gives it."""
    batches = recorded_batches(monkeypatch)
    chooser = AnsweringChooser()
    combat = CombatManager()
    agent = situations.new_agent(
        combat, tuning=tuning, settings=COMMANDER_ON, commander_chooser=chooser, plan_selector=FixedPlan("HOLD")
    )
    return MainCommand(agent, chooser, batches), combat


def tanks_near(x: int, y: int, *, idle: bool = True) -> list[dict]:
    """main's six medium tanks in a block around a cell."""
    return tanks(6, x - 1, y - 1, unit_type="2tnk", idle=idle)


def advancing(monkeypatch, tuning: Tuning = STEPPED_ARMY, **more) -> tuple[MainCommand, CombatManager]:
    """main picked to advance on the war factory and sent on its first step, at tick 1005."""
    scene, combat = stepped_command(monkeypatch, tuning)
    scene.cycle(1000, units=tanks_near(32, 48), **FAR_FACTORY, **more)
    scene.answer("advance")
    scene.cycle(1005, units=tanks_near(32, 48), **FAR_FACTORY, **more)
    return scene, combat


def step_orders(batch: list, cell) -> list[dict]:
    return [intent.order for intent in batch if intent.order.get("to") == list(cell)]


def step_events(match_record) -> list[dict]:
    return [event for event in match_record("combat") if event["reason"] == "step"]


def fallbacks(match_record) -> list[str]:
    return [event["reason"] for event in match_record(journal.FALLBACK_KIND)]


def test_step_commit_held_until_release_a_gather_mid_step_waits_for_the_step_s_release(monkeypatch, match_record):
    scene, combat = advancing(monkeypatch)
    scene.cycle(1010, units=tanks_near(37, 48, idle=False), **FAR_FACTORY)
    scene.answer("gather")

    scene.cycle(1015, units=tanks_near(38, 48, idle=False), **FAR_FACTORY)
    held = scene.agent.ctx.commander.standing.kind
    scene.cycle(1020, units=tanks_near(*FIRST_STEP), **FAR_FACTORY)

    assert held == "advance" and scene.agent.ctx.commander.standing.kind == "gather"
    assert "step_commit" in fallbacks(match_record)


def test_step_commit_same_destination_keeps_plan_an_answer_naming_the_advance_mid_step_changes_nothing(
    monkeypatch, match_record
):
    scene, combat = advancing(monkeypatch)
    scene.cycle(1010, units=tanks_near(37, 48, idle=False), **FAR_FACTORY)
    scene.answer("advance")

    scene.cycle(1015, units=tanks_near(38, 48, idle=False), **FAR_FACTORY)

    move = combat._stepper.move
    assert (move.index, move.point, move.ordered_tick) == (1, FIRST_STEP, 1005)
    assert step_orders(scene.batches[-1], FIRST_STEP) == []
    assert [event["phase"] for event in step_events(match_record)] == ["start"]
    assert "step_commit" not in fallbacks(match_record)


# A heavy tank 18 cells north of the base's center, beyond its alarm radius and far from main: an enemy army closing,
# which offers hold.
CLOSING_NORTH = {"visible_enemies": [enemy(701, "3tnk", 22, 30)]}


def test_step_commit_hold_at_step_point_a_hold_mid_step_stops_nobody_before_the_release(monkeypatch):
    scene, combat = advancing(monkeypatch, **CLOSING_NORTH)
    scene.cycle(1010, units=tanks_near(37, 48, idle=False), **CLOSING_NORTH, **FAR_FACTORY)
    scene.answer("hold")

    before = scene.cycle(1015, units=tanks_near(38, 48, idle=False), **CLOSING_NORTH, **FAR_FACTORY)
    held = scene.agent.ctx.commander.standing.kind
    at_point = scene.cycle(1020, units=tanks_near(*FIRST_STEP), **CLOSING_NORTH, **FAR_FACTORY)

    assert [order for order in before if order["cmd"] == "stop"] == [] and held == "advance"
    assert scene.agent.ctx.commander.standing.kind == "hold"
    assert [order for order in at_point if order["cmd"] in ("move", "attack_move")] == []


def test_step_commit_override_steps_back_to_the_step_start_and_leaves_the_gate_open(monkeypatch, match_record):
    scene, combat = advancing(monkeypatch)
    heavy = {"visible_enemies": [enemy(700 + n, "3tnk", 46 + n % 2, 47 + n // 2) for n in range(8)]}
    for tick in range(1010, 1045, 5):
        scene.cycle(tick, units=tanks_near(36, 48, idle=False), **heavy, **FAR_FACTORY)

    (override,) = [event for event in commander_events(match_record) if event["kind"] == "override_retreat"]
    gates = [event for event in commander_events(match_record) if event["kind"] == "gate"]
    assert override["step_back"] is True and override["cell"] == [32, 48]
    assert [gate["open"] for gate in gates] == [True]
    moved_back = [order for batch in scene.batches for order in step_orders(batch, (32, 48))]
    assert moved_back and all(order["cmd"] == "move" for order in moved_back)


def test_step_commit_off_the_override_sends_main_to_the_staging_point_and_closes_the_gate(monkeypatch, match_record):
    off = dataclasses.replace(STEPPED_ARMY, commander=dataclasses.replace(STEPPED_ARMY.commander, step_commit=False))
    scene, combat = advancing(monkeypatch, off)
    heavy = {"visible_enemies": [enemy(700 + n, "3tnk", 46 + n % 2, 47 + n // 2) for n in range(8)]}
    for tick in range(1010, 1045, 5):
        scene.cycle(tick, units=tanks_near(36, 48, idle=False), **heavy, **FAR_FACTORY)

    (override,) = [event for event in commander_events(match_record) if event["kind"] == "override_retreat"]
    gates = [event for event in commander_events(match_record) if event["kind"] == "gate"]
    assert "step_back" not in override
    assert [(gate["open"], gate["reason"]) for gate in gates][:2] == [(True, None), (False, "pulled_back")]


def test_step_commit_threat_change_immediate_a_base_threat_mid_step_lets_retreat_home_be_taken_at_once(monkeypatch):
    scene, combat = advancing(monkeypatch)
    scene.cycle(1010, units=tanks_near(37, 48, idle=False), **THREAT, **FAR_FACTORY)
    scene.answer("retreat home")

    scene.cycle(1015, units=tanks_near(38, 48, idle=False), **THREAT, **FAR_FACTORY)

    assert scene.agent.ctx.commander.standing.kind == "retreat_home"


def test_step_commit_tuning_ships_on_built_in_and_in_the_tracked_file():
    assert CommanderTuning().step_commit is True and Tuning.from_file(REPO_ROOT).commander.step_commit is True


# -- reinforcements held home while main is away (reinforce_hold_home) -------------------------------------------------

# FIVE_THOUSAND moved 29 cells east: main's centroid (72.2, 46.4), 40 cells from the staging point (32, 48).
FAR_FIVE_THOUSAND = [{**tank, "x": tank["x"] + 29} for tank in FIVE_THOUSAND]
# Four light tanks at the staging point, 2,800 together: short of a group of their own.
LIGHT_SQUAD = [unit(500 + n, "1tnk", 31 + n % 2, 47 + n // 2) for n in range(4)]
LIGHT_IDS = {tank["id"] for tank in LIGHT_SQUAD}


def opened_hold_gate() -> GatedMain:
    """`opened_gate` with the hold on, which ships off."""
    on = dataclasses.replace(ARMY, combat=dataclasses.replace(ARMY.combat, reinforce_hold_home=True))
    scene = gated_main(tuning=on)
    scene.cycle(1000, FIVE_THOUSAND)
    assert scene.commander.gate.is_open
    return scene


def combat_of(scene: GatedMain) -> CombatManager:
    return next(manager for manager in scene.agent.managers if isinstance(manager, CombatManager))


def test_reinforce_hold_gate_joiners_four_at_the_staging_point_are_not_sent_to_main_40_cells_away_after_30_s():
    scene = opened_hold_gate()
    scene.cycle(1005, [*FAR_FIVE_THOUSAND, *LIGHT_SQUAD])

    cycles = [scene.cycle(tick, [*FAR_FIVE_THOUSAND, *LIGHT_SQUAD]) for tick in (1010, 1760, 1765, 1800)]

    assert not any(command == "attack_move" for orders in cycles for command, _, _ in naming(orders, LIGHT_IDS))
    assert scene.commander.gate.joiners.ids == LIGHT_IDS


def test_reinforce_hold_gate_joiners_gather_at_the_forward_gather_point_while_one_is_published():
    scene = opened_hold_gate()
    combat_of(scene)._hold.cell = (40, 40)

    orders = scene.cycle(1005, [*FAR_FIVE_THOUSAND, *LIGHT_SQUAD])

    assert naming(orders, LIGHT_IDS) == [("move", sorted(LIGHT_IDS), [40, 40])]


def test_reinforce_hold_gate_joiners_worth_3000_set_out_and_join_main_once_within_6_cells():
    scene = opened_hold_gate()
    heavy = [unit(500 + n, "3tnk", 31 + n % 2, 47 + n // 2) for n in range(3)]
    scene.cycle(1005, [*FAR_FIVE_THOUSAND, *heavy])
    arrived = [unit(500 + n, "3tnk", 71 + n, 46) for n in range(3)]

    scene.cycle(1010, [*FAR_FIVE_THOUSAND, *arrived])
    scene.cycle(1015, [*FAR_FIVE_THOUSAND, *arrived])

    # 3,450 together: they set out as a group, and once within 6 cells of main's centroid they are main.
    assert scene.commander.gate.joiners.ids == frozenset()
