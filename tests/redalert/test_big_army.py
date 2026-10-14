"""A big main facing an enemy army nobody has seen: PRESS opens on a known base, and the scouts are sent to look."""

from __future__ import annotations

import dataclasses
import random
import re
from pathlib import Path

import pytest

import situations
from situations import ask, base_state
from switches_off import new_agent, scouting_off, tuning_off
from ucla_agent.core.config import ConfigError
from ucla_agent.games.redalert import plans, scout_missions
from ucla_agent.games.redalert.composition import FINISH, HOLD, PRESERVE, PRESS
from ucla_agent.games.redalert.managers import NO_THREAT
from ucla_agent.games.redalert.plans import R2, R3, R6, R7, Situation, script_choice
from ucla_agent.games.redalert.scout_mission_planner import REFRESH, MissionPlanner
from ucla_agent.games.redalert.scout_report import ScoutReport
from ucla_agent.games.redalert.scout_steering import Dangers
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.sight import SightGrid
from ucla_agent.games.redalert.tuning import TUNING_FILE, PlanRulesTuning, Tuning

TICKS_PER_SECOND = 25
RULES = PlanRulesTuning()
ALLIED_BASE = (("fact", 22, 48), ("powr", 19, 48), ("tent", 22, 44), ("proc", 24, 39), ("powr", 26, 50))
WITH_WAR_FACTORY = (*ALLIED_BASE, ("weap", 22, 53))
# Mammoth tanks of 2,000 each: main of 20,000 and of 10,000, and the 4,000 it had when the enemy army was in sight.
MAIN_20000, MAIN_10000, MAIN_4000 = 10, 5, 2
# Eight enemy Mammoth tanks (16,000), last in sight at 650 s: main of 20,000 is no 1.5 times that.
ENEMY_ARMY = [{"id": 500 + number, "type": "4tnk", "x": 74, "y": 40 + number, "hp": 100} for number in range(8)]
# A pillbox remembered at the enemy base: R6(b) does not clean up a defended base.
PILLBOX = {"id": 600, "type": "pbox", "x": 74, "y": 30, "hp": 100}


def tanks(count: int) -> list[dict]:
    """Own Mammoth tanks at home, which main takes."""
    return [situations.unit(300 + number, "4tnk", 32 + number % 3, 48 + number // 3) for number in range(count)]


def at(
    seconds: float, main: int, *, enemies: list | tuple = (), balance: int = -5000, remaining: float = 400.0
) -> dict:
    """A state at a game second with main of `main` Mammoth tanks, the enemies in sight given and the pillbox
    remembered, behind on the trade balance by default so that R6(a) does not finish."""
    state = base_state(
        "england",
        *WITH_WAR_FACTORY,
        tick=round(seconds * TICKS_PER_SECOND),
        units=tanks(main),
        visible_enemies=list(enemies),
        visible_enemy_buildings=[PILLBOX],
        military={"kills_cost": max(balance, 0), "deaths_cost": max(-balance, 0), "assets_value": 0, "army_value": 0},
    )
    state["time"] = {"max_minutes": 20, "game_seconds": seconds, "remaining_seconds": remaining}
    state["your_seat"] = "Multi0"
    return state


def plans_agent(*, base_seen: bool = True, tuning: Tuning | None = None):
    """An agent with no manager, so that only the plan selector acts; the enemy base counts as seen when asked."""
    agent = new_agent(tuning=tuning or tuning_off())
    if base_seen:
        agent.ctx.scout_report = ScoutReport(enemy_base_seen=True)
    return agent


def after(agent, *states: dict) -> tuple[str, str, bool]:
    """The plan and rule in force after the states, and whether the plan rules report the big army facing an unseen
    enemy army."""
    for state in states:
        ask(agent, state)
    return agent.current_plan, agent.ctx.plan_rule, agent.ctx.big_army_unseen_enemy


def seen_then_not(
    main: int, unseen_seconds: float, *, base_seen: bool = True, tuning: Tuning | None = None, **more
) -> tuple[str, str, bool]:
    """The plan, rule and flag `unseen_seconds` after the enemy army was last in sight at 650 s, main then of 4,000."""
    agent = plans_agent(base_seen=base_seen, tuning=tuning)
    first = at(650, MAIN_4000, enemies=ENEMY_ARMY, **more)
    return after(agent, first, at(650 + unseen_seconds, main, **more))


# -- the plan rules ----------------------------------------------------------------------------------------------------


def test_big_army_main_of_20000_with_no_enemy_army_seen_for_150_s_and_the_base_known_presses_by_r7():
    assert seen_then_not(MAIN_20000, 150) == (PRESS, R7, True)


def test_big_army_the_press_weighs_the_unseen_army_at_main_over_1_5_in_the_plan_change(match_record):
    seen_then_not(MAIN_20000, 150)

    change = match_record("plan_change")[-1]
    inputs = change["inputs"]
    assert (change["new"], change["rule"], inputs["main_strength"], inputs["enemy_army_known"]) == (
        PRESS,
        R7,
        20_000,
        False,
    )
    assert (inputs["enemy_army_unseen_ticks"], inputs["ratio"]) == (3750, pytest.approx(1.5))


@pytest.mark.parametrize(
    "main, unseen_seconds, base_seen, expected",
    [
        (MAIN_10000, 150, True, (HOLD, R7, False)),
        (MAIN_20000, 100, True, (HOLD, R7, False)),
        (MAIN_20000, 150, False, (HOLD, R7, True)),
    ],
    ids=["main of 10,000", "100 s since the last sighting", "the enemy base unknown"],
)
def test_big_army_main_of_10000_100_s_unseen_or_an_unknown_base_changes_nothing_and_holds(
    main, unseen_seconds, base_seen, expected
):
    assert seen_then_not(main, unseen_seconds, base_seen=base_seen) == expected


# The plan rules with R2 and R3 before R6 in the last three minutes (finish_over_preserve off).
CLOCK_FIRST = tuning_off(plan_rules=dataclasses.replace(tuning_off().plan_rules, finish_over_preserve=False))


@pytest.mark.parametrize(
    "balance, tuning, expected",
    [
        (2000, CLOCK_FIRST, (PRESERVE, R2, True)),
        (0, CLOCK_FIRST, (HOLD, R3, True)),
        (2000, None, (FINISH, R6, True)),
        (0, None, (FINISH, R6, True)),
    ],
    ids=["a lead", "an even balance", "a lead, R6 first", "an even balance, R6 first"],
)
def test_big_army_in_the_last_2_minutes_no_big_army_press_the_clock_rules_or_r6_before_them_decide(
    balance, tuning, expected
):
    # Main of 20,000 against a remembered pillbox: R6(a) holds once the balance is not negative.
    assert seen_then_not(MAIN_20000, 150, balance=balance, remaining=100, tuning=tuning) == expected


def big_situation(
    main: int, unseen_seconds: float | None, *, base_seen: bool = True, known: bool = False, recent: int = 0
) -> Situation:
    """A hand-built situation at 800 s with main and the own army worth `main`, an even balance, nothing to raid, the
    enemy army last in sight `unseen_seconds` ago (None: never) and known or not, its ratio weighed as the shipped
    rules weigh it."""
    unseen = None if unseen_seconds is None else round(unseen_seconds * TICKS_PER_SECOND)
    seen = Situation(
        tick=20_000,
        game_seconds=800.0,
        remaining_seconds=400.0,
        trade_balance=0,
        army_value=main,
        main_strength=main,
        enemy_combat_value=0,
        enemy_units=0,
        enemy_buildings=3,
        enemy_defenses=1,
        defense_cost=400,
        base_seen=base_seen,
        has_war_factory=True,
        can_raid=False,
        raid_target_known=False,
        enemy_unseen_ticks=unseen,
        scout_active=False,
        enemy_army_known=known,
        recent_enemy_army_value=recent,
        combat_army_value=main,
        enemy_committed=True,
        enemy_army_unseen_ticks=unseen,
    )
    return dataclasses.replace(seen, ratio=main / plans.weighed_enemy(seen, RULES))


def table(seen: Situation, rules: PlanRulesTuning = RULES) -> tuple[list[str], str]:
    eligible = plans.open_plans(seen, rules)
    return eligible, script_choice(seen, NO_THREAT, eligible, rules)


@pytest.mark.parametrize(
    "main, unseen_seconds, base_seen, ratio, expected",
    [
        (20_000, 150, True, 1.5, ([HOLD, PRESS], PRESS)),
        (20_000, None, True, 1.5, ([HOLD, PRESS], PRESS)),
        (15_000, 120, True, 1.5, ([HOLD, PRESS], PRESS)),
        (14_999, 150, True, 1.0, ([HOLD], HOLD)),
        (20_000, 119.96, True, 1.0, ([HOLD], HOLD)),
        (10_000, 150, True, 1.0, ([HOLD], HOLD)),
        (20_000, 100, True, 1.0, ([HOLD], HOLD)),
        (20_000, 150, False, 1.0, ([HOLD], HOLD)),
    ],
    ids=[
        "20,000 and 150 s",
        "20,000 and never seen",
        "exactly 15,000 and 120 s",
        "14,999",
        "2,999 ticks",
        "10,000",
        "100 s",
        "base unknown",
    ],
)
def test_big_army_hand_built_press_opens_and_the_table_presses_only_at_15000_unseen_120_s_on_a_known_base(
    main, unseen_seconds, base_seen, ratio, expected
):
    seen = big_situation(main, unseen_seconds, base_seen=base_seen)

    assert (table(seen), seen.ratio) == (expected, pytest.approx(ratio))


def test_big_army_a_known_enemy_army_is_weighed_as_before_at_the_most_it_was_lately():
    seen = big_situation(20_000, 150, known=True, recent=16_000)

    assert (plans.weighed_enemy(seen, RULES), table(seen)) == (16_000, ([HOLD], HOLD))


def test_big_army_the_unseen_army_weighs_main_over_the_ratio_and_a_ratio_of_0_weighs_nothing_from_main():
    no_ratio = dataclasses.replace(RULES, big_army_assumed_ratio=0.0)
    seen = big_situation(20_000, 150)

    assert plans.weighed_enemy_value(20_000, 0, 0, False, RULES, 20_000) == pytest.approx(20_000 / 1.5)
    assert plans.assumed_main(20_000, 3750, True, no_ratio) is None
    assert plans.weighed_enemy(seen, no_ratio) == 20_000 and table(seen, no_ratio)[0] == [HOLD]


def test_big_army_press_never_opens_with_an_unknown_enemy_base_across_200_random_big_situations():
    chance = random.Random(33)
    for _ in range(200):
        seen = big_situation(chance.randrange(0, 60_000, 50), chance.choice([None, 0, 100, 120, 150, 600]))

        assert PRESS not in plans.open_plans(dataclasses.replace(seen, base_seen=False), RULES)


# -- the tuning keys ---------------------------------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_big_army_the_keys_ship_at_15000_120_and_1_5_built_in_and_in_the_tracked_file():
    for rules in (PlanRulesTuning(), Tuning.from_file(REPO_ROOT).plan_rules):
        assert (rules.big_army_value, rules.unknown_army_seconds, rules.big_army_assumed_ratio) == (15_000, 120, 1.5)


def write_tuning_with(root: Path, key: str, value: str | None) -> None:
    """Write the tracked tuning file under `root` with one key's line set to a value, or left out for None."""
    target = root / TUNING_FILE
    target.parent.mkdir(parents=True)
    replacement = "" if value is None else f"{key} = {value}"
    text = (REPO_ROOT / TUNING_FILE).read_text(encoding="utf-8")
    target.write_text(re.sub(rf"^{key} = .*$", replacement, text, count=1, flags=re.MULTILINE), encoding="utf-8")


@pytest.mark.parametrize(
    "key, value, expected",
    [
        ("big_army_value", None, "a whole number"),
        ("big_army_value", "-1", "a whole number"),
        ("unknown_army_seconds", "1.5", "a whole number"),
        ("big_army_assumed_ratio", None, "a number of at least 0"),
        ("big_army_assumed_ratio", "-0.5", "a number of at least 0"),
    ],
    ids=["value missing", "negative value", "fractional seconds", "ratio missing", "negative ratio"],
)
def test_big_army_keys_missing_or_of_the_wrong_kind_are_config_errors_naming_them(tmp_path, key, value, expected):
    write_tuning_with(tmp_path, key, value)

    with pytest.raises(ConfigError, match=rf"{key} in \[plan_rules\] must be {expected}"):
        Tuning.from_file(tmp_path)


# -- the scouts' refresh -----------------------------------------------------------------------------------------------

MISSIONS = dataclasses.replace(tuning_off(), scouting=scouting_off(scout_slots=True, scout_missions=True))
NO_MISSIONS = dataclasses.replace(tuning_off(), scouting=scouting_off(scout_slots=True, scout_missions=False))
# The fake arena's two starts, ours by the base at (22, 48), the enemy's at (74, 48).
TWO_STARTS = {"rules": {"maps": {"fake-arena": {"bounds": [0, 0, 96, 96], "spawns": [[22, 48], [74, 48]]}}}}
# The enemy main's edge and the lane's middle on that map.
MAIN_EDGE_CELL, LANE_CELL = (60, 60), (48, 48)
# A jeep scout, and two places 12 cells from it.
JEEP_AT, SOUTH, NORTH = (30, 48), (30, 60), (30, 36)


def scout_base(tick: int, **observation) -> dict:
    """An Allied base of the construction yard, a power plant and a barracks, with what the test adds."""
    return base_state("england", ("fact", 22, 48), ("powr", 19, 52), ("tent", 18, 44), tick=tick, **observation)


def refreshes(planner: MissionPlanner, agent, ticks: list[int], *, big: bool = True) -> list[set[str]]:
    """The kinds of the forced missions the planner finds at each tick, the plan rules reporting the big army facing
    an unseen enemy army or not."""
    found = []
    for tick in ticks:
        ask(agent, scout_base(tick), situations.context(TWO_STARTS))
        agent.ctx.big_army_unseen_enemy = big
        missions = planner.missions(agent.world, agent.ctx, SightGrid())
        found.append({mission.kind for mission in missions if mission.forced})
    return found


def test_big_army_scout_mission_refresh_forces_the_lane_and_the_main_edge_once_every_60_s():
    agent, planner = new_agent(tuning=MISSIONS), MissionPlanner()

    found = refreshes(planner, agent, [1000, 1005, 2499, 2500])

    assert found == [{"attack_lane", "main_edge"}, set(), set(), {"attack_lane", "main_edge"}]


@pytest.mark.parametrize("tuning, big", [(MISSIONS, False), (NO_MISSIONS, True)], ids=["no big army", "missions off"])
def test_big_army_scout_mission_refresh_forces_nothing_without_the_big_army_or_with_scout_missions_off(tuning, big):
    agent, planner = new_agent(tuning=tuning), MissionPlanner()

    assert refreshes(planner, agent, [1000, 2500], big=big) == [set(), set()]


def mission(kind: str, cell: tuple[int, int], priority: int, *, forced: bool = False) -> scout_missions.Mission:
    return scout_missions.Mission(kind, cell, priority, None, forced)


def planned(agent, planner: MissionPlanner, offered: list, tick: int) -> tuple:
    """Show the agent its base with the jeep scout of slot 1 at a tick, and the cell of its mission among those
    offered, with why it changed."""
    ask(agent, scout_base(tick, units=[situations.unit(400, "jeep", *JEEP_AT)]))
    scout = {1: agent.world.combat_units[400]}
    assignment = planner.plan(scout, offered, Dangers(agent.world, agent.ctx), agent.world, agent.ctx)[1]
    return None if assignment.option is None else assignment.option.mission.cell, assignment.change


def test_big_army_scout_mission_a_forced_lane_turns_a_scout_off_a_far_better_watch_and_is_kept_after_the_refresh():
    agent, planner = new_agent(tuning=MISSIONS), MissionPlanner()
    watch = mission("expansion_watch", SOUTH, 10)

    first = planned(agent, planner, [watch], 1000)
    refreshed = planned(agent, planner, [watch, mission("attack_lane", NORTH, 1, forced=True)], 1005)
    kept = planned(agent, planner, [watch, mission("attack_lane", NORTH, 1)], 1010)

    # Without the refresh the lane, worth a tenth of the watch, would never take the scout from it.
    assert (first, refreshed, kept) == ((SOUTH, "new"), (NORTH, REFRESH), (NORTH, None))


def test_big_army_scout_mission_assign_gives_forced_missions_before_the_scouts_busy_and_the_worth():
    lane = scout_missions.Option(mission("attack_lane", NORTH, 1, forced=True), (NORTH,), 0.01, 0.01)
    watch = scout_missions.Option(mission("expansion_watch", SOUTH, 10), (SOUTH,), 5.0, 5.0)

    assert [option.mission.cell for option in scout_missions.assign([[watch, lane]])] == [NORTH]


def test_big_army_scout_mission_with_main_of_20000_never_facing_an_enemy_the_two_scouts_are_sent_on_a_refresh(
    match_record,
):
    agent = new_agent(ScoutingManager(), tuning=MISSIONS)
    scouts = [situations.unit(300, "e1", 24, 48), situations.unit(301, "e1", 24, 40)]
    army = [situations.unit(310 + number, "4tnk", 32 + number % 3, 52 + number // 3) for number in range(MAIN_20000)]

    ask(agent, scout_base(1000, units=[*scouts, *army]))

    events = [event for event in match_record("scout_slot") if event["kind"] == "destination"]
    assert agent.ctx.big_army_unseen_enemy
    assert sorted((event["mission"], tuple(event["cell"]), event["reason"]) for event in events) == [
        ("attack_lane", LANE_CELL, REFRESH),
        ("main_edge", MAIN_EDGE_CELL, REFRESH),
    ]
