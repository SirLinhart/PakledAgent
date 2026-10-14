"""The models' recent picks and the framing of their questions: the plan, commander and scout questions."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

import situations
from situations import StubChooser, ask, base_state, unit
from switches_off import new_agent, scouting_off, tuning_off
from ucla_agent.core.config import ConfigError, history_picks, load_agent_config
from ucla_agent.core.decide import USED, ChoiceResult, local
from ucla_agent.games.redalert import command_army, command_prompt, command_scout, model_history, plans, scout_choice
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.briefing import Briefing
from ucla_agent.games.redalert.compact_prompt import CompactPrompt, prompt_chars
from ucla_agent.games.redalert.composition import HOLD, PRESS, RAID
from ucla_agent.games.redalert.glossary import Glossary
from ucla_agent.games.redalert.model_history import ModelHistory
from ucla_agent.games.redalert.scout_steering import MODEL, SCRIPT, UNSAFE, Course, Dangers
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.settings import RedAlertSettings
from ucla_agent.games.redalert.sight import SightGrid
from ucla_agent.games.redalert.world import Unit

REPO_ROOT = Path(__file__).resolve().parents[2]
TICKS_PER_SECOND = 25
ALLIED_BASE = (
    ("fact", 22, 48),
    ("powr", 19, 48),
    ("tent", 22, 44),
    ("proc", 24, 39),
    ("powr", 26, 50),
    ("weap", 22, 53),
)
SCOUT_BASE = (("fact", 22, 48), ("powr", 19, 52), ("tent", 18, 44))
RIFLES = tuple(unit(300 + number, "e1", 26, 46 + number) for number in range(4))
RAID_TARGET = {"id": 450, "type": "harv", "x": 60, "y": 30, "hp": 100}


def at(seconds: int, *, kills: int = 0, deaths: int = 0) -> dict:
    """A playing state of an Allied base with four rifles at a game second, with the value killed and lost given; an
    uncovered enemy harvester in sight keeps RAID open."""
    state = base_state(
        "england",
        *ALLIED_BASE,
        tick=seconds * TICKS_PER_SECOND,
        units=list(RIFLES),
        visible_enemies=[RAID_TARGET],
        military={"kills_cost": kills, "deaths_cost": deaths, "assets_value": 0, "army_value": 0},
    )
    state["time"] = {"max_minutes": 20, "game_seconds": seconds, "remaining_seconds": 1200 - seconds}
    return state


def picked_twice():
    """An agent that took in three states 20 s apart, the model having picked RAID after the first and PRESS after
    the second, and the plan rules' army ratio after each state."""
    agent = new_agent(BaseDefenseManager(), ScoutingManager())
    ratios = []
    for seconds, kills, deaths, plan in ((600, 1000, 500, RAID), (620, 1800, 700, PRESS), (640, 2300, 900, None)):
        ask(agent, at(seconds, kills=kills, deaths=deaths))
        ratios.append(agent.ctx.model_history.ratio)
        if plan is not None:
            agent.ctx.model_history.note_plan(plan, agent.world)
    return agent, ratios


def two_picks_lines(ratios: list[float]) -> list[str]:
    """The `recent picks:` lines of `picked_twice` at its last state."""
    first, second, now = (f"{ratio:.1f}" for ratio in ratios)
    return [
        "recent picks:",
        f"- 20 s ago: PRESS -> since: killed 500, lost 200, army ratio {second} -> {now}",
        f"- 40 s ago: RAID -> since: killed 1,300, lost 400, army ratio {first} -> {now}",
    ]


def briefing_of(agent, max_chars: int = 9000) -> str:
    situation = agent._plan_selector.situation
    eligible = plans.open_plans(situation, agent.ctx.tuning.plan_rules, agent.ctx.plan == PRESS)
    return Briefing(Glossary.from_file(REPO_ROOT), max_chars).write(agent.world, agent.ctx, situation, eligible)


def compact_of(agent, prompt: CompactPrompt | None = None):
    situation = agent._plan_selector.situation
    writer = prompt or CompactPrompt()
    return writer.request(agent.world, agent.ctx, situation, [HOLD, RAID, PRESS], 1500, str(agent.world.tick))


# -- the tracker and the framing ---------------------------------------------------------------------------------------


def test_history_the_framing_is_within_600_characters_and_says_the_tournament_the_doctrine_and_to_learn():
    framing = model_history.FRAMING

    assert len(framing) <= model_history.MAX_FRAMING_CHARS == 600
    assert framing == f"{model_history.TOURNAMENT} {model_history.DOCTRINE} {model_history.LEARN}"
    assert "another AI agent in a tournament" in framing and "tiebreak (value traded)" in framing
    for rule in (
        "never attack into defenses we cannot beat",
        "retreat and regroup when outnumbered",
        "tanks crush infantry and kill tanks",
        "artillery fires from behind a screen",
        "punish exposed expansions and harvesters",
        "keep a lead late",
    ):
        assert rule in framing


def test_history_each_system_text_carries_the_framing_but_the_compact_prompt_s():
    briefed = plans.plan_prompt(briefed=True)

    assert model_history.FRAMING in briefed and model_history.FRAMING in command_army.SYSTEM_TEXT
    assert model_history.FRAMING in scout_choice.QUESTION
    assert model_history.TOURNAMENT in command_scout.SYSTEM_TEXT and model_history.LEARN in command_scout.SYSTEM_TEXT
    assert model_history.DOCTRINE not in command_scout.SYSTEM_TEXT
    compact = compact_of(new_agent_after(at(600)))
    assert model_history.TOURNAMENT not in compact.system_text + local.choice_prompt(compact)


def new_agent_after(*states: dict):
    agent = new_agent(BaseDefenseManager(), ScoutingManager())
    for state in states:
        ask(agent, state)
    return agent


def test_history_a_new_match_s_context_holds_no_pick_of_the_match_before():
    agent, _ = picked_twice()

    again = new_agent_after(at(600))

    assert len(agent.ctx.model_history.plan_picks) == 2
    assert again.ctx.model_history.plan_picks == ModelHistory().plan_picks
    assert compact_of(again).context.splitlines()[-1] == "recent picks: none"


def test_history_keeps_history_picks_and_shows_the_newest_first():
    history = ModelHistory(2)
    agent = new_agent_after(at(600))

    for plan in (HOLD, RAID, PRESS):
        history.note_plan(plan, agent.world)

    assert [pick.label for pick in history.plan_picks] == [RAID, PRESS]
    assert [line.split(": ", 1)[1].split(" ->")[0] for line in history.plan_lines(agent.world)[1:]] == [PRESS, RAID]


# -- the plan questions ------------------------------------------------------------------------------------------------


def test_history_after_two_plan_picks_the_briefing_lists_both_newest_first_with_age_killed_lost_and_ratio():
    agent, ratios = picked_twice()

    text = briefing_of(agent)

    lines = text.splitlines()
    start = lines.index("recent picks:")
    assert lines[start : start + 3] == two_picks_lines(ratios)
    assert start < lines.index("## Our forces")


def test_history_with_no_picks_yet_each_plan_question_says_recent_picks_none():
    agent = new_agent_after(at(600))

    assert "\nrecent picks: none\n" in briefing_of(agent)
    assert compact_of(agent).context.splitlines()[-1] == "recent picks: none"


def test_history_the_compact_prompt_lists_the_picks_after_its_keys_within_its_cap():
    agent, ratios = picked_twice()

    request = compact_of(agent)

    assert request.context.splitlines()[-3:] == two_picks_lines(ratios)
    assert prompt_chars(request) <= 1500
    assert "Doctrine" not in request.system_text + local.choice_prompt(request)


def test_history_over_its_cap_the_compact_prompt_cuts_the_oldest_pick_first_and_keeps_its_plans_lines():
    agent, ratios = picked_twice()
    full = compact_of(agent)

    request = compact_of(agent, CompactPrompt(max_chars=prompt_chars(full) - 1))

    assert request.context.splitlines()[-2:] == two_picks_lines(ratios)[:2]
    assert request.options == full.options


def test_history_over_its_cap_the_briefing_cuts_the_oldest_pick_before_the_glossary():
    agent, ratios = picked_twice()
    full = briefing_of(agent)

    cut = briefing_of(agent, max_chars=len(full) - 1)

    assert two_picks_lines(ratios)[1] in cut and two_picks_lines(ratios)[2] not in cut
    assert cut.split("## Glossary")[1] == full.split("## Glossary")[1]


def test_history_a_model_answer_the_plan_rules_take_is_a_plan_pick():
    chooser = StubChooser(None, ChoiceResult(HOLD, None, "stub:model", 400, USED))
    agent = new_agent(settings=RedAlertSettings(model_plan_choice=True), chooser=chooser)

    ask(agent, at(280))
    ask(agent, at(281, kills=300))

    (pick,) = agent.ctx.model_history.plan_picks
    assert (pick.label, pick.tick, pick.kills_cost) == (HOLD, 281 * TICKS_PER_SECOND, 300)
    assert agent.current_plan == HOLD and pick.ratio == agent._plan_selector.situation.ratio


# -- the commander's questions ----------------------------------------------------------------------------------------


def test_history_a_commander_question_falls_back_to_the_short_system_text_when_no_level_fits_with_the_framing():
    lines = ["t=00:00"]
    actions = (command_prompt.Action("hold", ("hold",), "hold", "hold", id="A"),)
    full = command_prompt.fitted(command_army.SYSTEM_TEXT, lambda detail: lines, actions, 0, 10_000)
    cap = prompt_chars(full.request) - 1

    short = command_prompt.fitted(
        command_army.SYSTEM_TEXT, lambda detail: lines, actions, 0, cap, None, command_army.SHORT_SYSTEM_TEXT
    )

    assert short.request.system_text == command_army.SHORT_SYSTEM_TEXT and prompt_chars(short.request) <= cap
    assert model_history.FRAMING not in command_army.SHORT_SYSTEM_TEXT
    with pytest.raises(ValueError):
        command_prompt.fitted(command_army.SYSTEM_TEXT, lambda detail: lines, actions, 0, cap)


def test_history_a_commander_question_cuts_its_picks_before_any_other_detail():
    first, *rest = command_prompt.DETAILS

    assert [detail.picks for detail in command_prompt.DETAILS[:3]] == [None, 2, 1]
    assert all(dataclasses.replace(detail, picks=None) == first for detail in rest[:2])


# -- the scout question ------------------------------------------------------------------------------------------------

SLOTS = dataclasses.replace(tuning_off(), scouting=scouting_off(scout_slots=True))
SCOUT = Unit(id=300, type="e1", x=24, y=48, hp=100, idle=False, activity="move", stance=None)
PLACE = (60, 60)


def scout_world():
    """An agent with scout slots that took in one state of a base, the scout of `SCOUT` among its units."""
    agent = new_agent(ScoutingManager(), tuning=SLOTS)
    ask(agent, base_state("england", *SCOUT_BASE, tick=1000, units=[situations.unit(300, "e1", 24, 48)]))
    return agent


def scout_context(agent) -> list[str]:
    """The lines of the context of the scout question a slot asks about `SCOUT`."""
    chooser = StubChooser()
    dangers = Dangers(agent.world, agent.ctx)
    scout_choice.ScoutQuestion(chooser).ask(SCOUT, [], SightGrid(), dangers, agent.world, agent.ctx)
    (request,) = chooser.submitted
    return request.context.splitlines()


def test_history_with_no_scout_pick_yet_the_scout_question_says_recent_picks_none():
    assert scout_context(scout_world())[-1] == "recent picks: none"


@pytest.mark.parametrize(
    "reason, outcome",
    [("reached", "reached"), (UNSAFE, "retreated"), ("fled", "retreated"), ("stuck", "stuck"), ("new", "redirected")],
)
def test_history_a_scout_pick_its_course_drops_is_settled_by_why(reason, outcome):
    agent = scout_world()
    world, ctx = agent.world, agent.ctx
    course, question = Course(), scout_choice.ScoutQuestion(StubChooser())
    ctx.model_history.note_scout("enemy main edge (60,60)", SCOUT.id, PLACE, world)
    course.go(PLACE, [PLACE], MODEL, world.tick)
    question.is_due(course, False, world, ctx)

    course.give_up(reason)
    course.go((40, 14), [(40, 14)], SCRIPT, world.tick)
    question.is_due(course, True, world, ctx)

    assert scout_context(agent)[-2:] == [
        "recent picks:",
        f"- 0 s ago: enemy main edge (60,60) -> {outcome}; saw nothing new",
    ]


def test_history_a_scout_pick_whose_scout_is_gone_is_lost_and_shown_to_every_scout():
    agent = scout_world()
    agent.ctx.model_history.note_scout("expansion (40,14)", 999, (40, 14), agent.world)

    assert scout_context(agent)[-2:] == ["recent picks:", "- 0 s ago: expansion (40,14) -> lost; saw nothing new"]


def test_history_a_scout_pick_still_on_its_way_is_en_route_with_the_enemies_first_seen_since():
    agent = scout_world()
    agent.ctx.model_history.note_scout("enemy main edge (60,60)", SCOUT.id, PLACE, agent.world)
    harvester = {"id": 700, "type": "harv", "x": 70, "y": 20, "hp": 100}
    buildings = [{"id": 701 + number, "type": "powr", "x": 76, "y": 50 + 3 * number, "hp": 100} for number in range(2)]
    seen = base_state(
        "england",
        *SCOUT_BASE,
        tick=1250,
        units=[situations.unit(300, "e1", 24, 48)],
        visible_enemies=[harvester],
        visible_enemy_buildings=buildings,
    )
    ask(agent, seen)

    assert (
        scout_context(agent)[-1] == "- 10 s ago: enemy main edge (60,60) -> en route; saw 2 new buildings, 1 new unit"
    )


def test_history_a_model_answer_the_slot_takes_is_a_scout_pick_settled_reached_once_the_scout_stands_there():
    choosers = (StubChooser(), StubChooser())
    agent = new_agent(ScoutingManager(), tuning=SLOTS, scout_choosers=choosers)
    scouts = [situations.unit(300, "e1", 24, 48), situations.unit(301, "e1", 24, 40)]
    ask(agent, base_state("england", *SCOUT_BASE, tick=1000, units=scouts))
    first = choosers[0]
    (option,) = [option for option in first.submitted[0].options if option.id == "3"]
    first.results.append(ChoiceResult("3", None, "red_alert_scout", 40, USED))

    ask(agent, base_state("england", *SCOUT_BASE, tick=1005, units=scouts))
    (pick,) = agent.ctx.model_history.scout_picks
    there = [situations.unit(300, "e1", *pick.cell), scouts[1]]
    ask(agent, base_state("england", *SCOUT_BASE, tick=1100, units=there))

    assert (pick.unit_id, pick.outcome) == (300, "reached")
    assert option.description.startswith(pick.label.split(" (")[0])
    assert scout_context(agent)[-1] == f"- 3 s ago: {pick.label} -> reached; saw nothing new"


# -- the [models] key ----------------------------------------------------------------------------------------------------


def test_history_the_shipped_configuration_shows_3_picks_and_the_settings_carry_them():
    shipped = load_agent_config(REPO_ROOT)

    assert history_picks(shipped) == 3 == RedAlertSettings.from_file(REPO_ROOT).history_picks


@pytest.mark.parametrize("value", ["-1", "true", '"3"', "2.5"])
def test_history_picks_that_is_not_a_whole_number_of_0_or_more_is_a_config_error(tmp_path, value):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agent.toml").write_text(f"[models]\nautostart = false\nhistory_picks = {value}\n")

    with pytest.raises(ConfigError, match="history_picks in its \\[models\\] table"):
        load_agent_config(tmp_path)
