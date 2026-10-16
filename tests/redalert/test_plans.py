"""The plan selector: the script's rules R1 to R7, STRIKE and FINISH, the discretionary choice and its records."""

from __future__ import annotations

import dataclasses
import itertools
import random
import re
from pathlib import Path
from typing import Any

import pytest

import situations
from altruagent import DecisionContext
from switches_off import new_agent, own_agent, plan_rules_off, raids_off, tuning_off
from ucla_agent.core.config import ConfigError
from ucla_agent.core.decide import AsyncChooser, ChainSource, ChoiceResult
from ucla_agent.games.redalert import agent as agent_module
from ucla_agent.games.redalert import compact_prompt
from ucla_agent.games.redalert import plans as plans_module
from ucla_agent.games.redalert.agent import RedAlertAgent
from situations import StubChooser, ask, base_state
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.composition import FINISH, HOLD, OPENING, PRESERVE, PRESS, RAID, STRIKE
from ucla_agent.games.redalert.glossary import Glossary
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.groups import RAID as RAID_GROUP
from ucla_agent.games.redalert.managers import MAJOR_THREAT, MINOR_THREAT, NO_THREAT
from ucla_agent.games.redalert.plans import (
    MODEL,
    R1,
    R2,
    R3,
    R4,
    R5,
    R6,
    R7,
    SPARRING,
    Situation,
    plan_prompt,
    script_choice,
)
from ucla_agent.games.redalert.raids import RaidManager
from ucla_agent.games.redalert.scout_report import ScoutReport
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.settings import RedAlertSettings
from ucla_agent.games.redalert.tuning import TUNING_FILE, PlanRulesTuning, RaidTuning, Tuning
from ucla_agent.games.redalert.upkeep import UpkeepManager
from ucla_agent.games.redalert.world import Unit

TICKS_PER_SECOND = 25
# The modules of the Red Alert agent; batch.py names every command the platform takes, sell included.
REDALERT_SOURCES = sorted((situations.REPO_ROOT / "src" / "ucla_agent" / "games" / "redalert").glob("*.py"))
YARD = ("fact", 22, 48)
ALLIED_BASE = (YARD, ("powr", 19, 48), ("tent", 22, 44), ("proc", 24, 39), ("powr", 26, 50), ("weap", 22, 53))
SOVIET_BASE = (YARD, ("powr", 19, 48), ("barr", 22, 44), ("proc", 24, 39), ("powr", 26, 50), ("weap", 22, 53))
# Far from the agent's base, where no own unit sees anything.
ENEMY_CELL = (74, 48)


def own(unit_id: int, unit_type: str, x: int = 32, y: int = 48, hp: int = 100) -> dict:
    """One own unit as an observation lists it, idle."""
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": hp, "act": None, "idle": True, "stance": None}


def foe(enemy_id: int, enemy_type: str, x: int = ENEMY_CELL[0], y: int = ENEMY_CELL[1]) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


def rifles(count: int, first_id: int = 500, x: int = ENEMY_CELL[0]) -> list[dict]:
    """Enemy rifle infantry, 100 each, in a column at `x`."""
    return [foe(first_id + number, "e1", x, 40 + number) for number in range(count)]


# An enemy harvester no defense covers: a raid target, so that RAID is open.
RAID_TARGET = foe(450, "harv", 60, 30)

# main worth 1,300: a light tank and two rocket soldiers (the home guard takes rifles only).
MAIN_1300 = (own(300, "1tnk"), own(301, "e3", 33, 48), own(302, "e3", 32, 49))


def at(
    seconds: float,
    *,
    remaining: float = 900.0,
    balance: int = 0,
    army: int = 0,
    units: tuple = (),
    enemies: list | tuple = (RAID_TARGET,),
    buildings: list | tuple = (),
    base: tuple = ALLIED_BASE,
    faction: str = "england",
    seat: str = "Multi0",
    events: list | tuple = (),
    cash: int = 5000,
) -> dict:
    """A playing state of a base at a game second, with the clock, the balance and the army value given.

    Unless the enemies in sight are given, the one in sight is an uncovered enemy harvester, so RAID is open.
    """
    state = base_state(
        faction,
        *base,
        tick=round(seconds * TICKS_PER_SECOND),
        cash=cash,
        units=list(units),
        visible_enemies=list(enemies),
        visible_enemy_buildings=list(buildings),
        events=list(events),
        military={
            "kills_cost": max(balance, 0),
            "deaths_cost": max(-balance, 0),
            "assets_value": 0,
            "army_value": army,
        },
    )
    state["time"] = {"max_minutes": 20, "game_seconds": seconds, "remaining_seconds": remaining}
    state["your_seat"] = seat
    return state


# The thresholds with the early-attack guard switched off: R5 may start STRIKE, R7 opens PRESS before the ninth minute
# whether or not the enemy has committed its army, and the own army is weighed at the army value the state gives.
EARLY_GUARD_OFF = tuning_off(plan_rules=dataclasses.replace(plan_rules_off(), early_attack_guard=False))
# The thresholds with the blind-PRESS guard switched off: the unseen base opens PRESS, an unknown army weighs nothing.
# The early-attack guard is off with it, so that PRESS opens before the ninth minute on the army value alone.
GUARD_OFF = tuning_off(plan_rules=dataclasses.replace(EARLY_GUARD_OFF.plan_rules, blind_press_guard=False))


def plans_agent(*, base_seen: bool = False, settings: RedAlertSettings | None = None, tuning: Tuning | None = None):
    """An agent with no manager, so that only the plan selector acts; the enemy base counts as seen when asked."""
    agent = new_agent(settings=settings, tuning=tuning)
    if base_seen:
        agent.ctx.scout_report = ScoutReport(enemy_base_seen=True)
    return agent


def plan_after(agent, *states: dict, match_context: DecisionContext | None = None) -> str:
    """The plan in force after the agent has taken in the states, in order."""
    for state in states:
        ask(agent, state, match_context)
    return agent.current_plan


def rule_of(agent) -> str:
    return agent.ctx.plan_rule


# -- R1: the opening -------------------------------------------------------------------------------


def test_the_first_cycle_logs_the_opening_by_r1_and_the_plan_stays_opening_until_a_war_factory_stands(match_record):
    agent = plans_agent()
    without_war_factory = ALLIED_BASE[:-1]

    plans = [plan_after(agent, at(seconds, base=without_war_factory)) for seconds in (10, 60, 200)]

    (change,) = match_record("plan_change")
    assert plans == [OPENING] * 3 and rule_of(agent) == R1
    assert (change["old"], change["new"], change["rule"], change["tick"]) == (None, OPENING, R1, 250)


def test_the_opening_is_over_for_good_once_a_war_factory_has_stood():
    agent = plans_agent()

    plans = [
        plan_after(agent, at(seconds, base=base)) for seconds, base in ((100, ALLIED_BASE), (110, ALLIED_BASE[:-1]))
    ]

    assert OPENING not in plans


# -- R2 to R4: the clock ---------------------------------------------------------------------------


def test_with_180_seconds_left_and_a_positive_balance_the_plan_is_preserve_and_with_181_it_is_not():
    agent = plans_agent()

    assert plan_after(agent, at(1020, remaining=180, balance=100)) == PRESERVE and rule_of(agent) == R2
    assert plan_after(plans_agent(), at(1019, remaining=181, balance=100)) != PRESERVE


def test_with_180_seconds_left_and_a_zero_balance_the_plan_is_hold_by_r3_even_with_raid_open():
    agent = plans_agent()

    assert plan_after(agent, at(1020, remaining=180, balance=0)) == HOLD and rule_of(agent) == R3
    # With the base seen PRESS is not open, and the decision table plays the raid R7 has open.
    assert plan_after(plans_agent(base_seen=True), at(1019, remaining=181, balance=0)) == RAID


# The late-PRESS guard switched off, with R4's window of five minutes it is played with off.
LATE_GUARD_OFF_FIVE_MINUTES = tuning_off(
    plan_rules=dataclasses.replace(plan_rules_off(), press_late_seconds=300, late_press_guard=False)
)


def test_late_press_with_the_five_minute_window_300_seconds_left_and_a_negative_balance_the_plan_is_press_by_r4():
    agent = plans_agent(tuning=LATE_GUARD_OFF_FIVE_MINUTES)
    before = plans_agent(base_seen=True, tuning=LATE_GUARD_OFF_FIVE_MINUTES)

    assert plan_after(agent, at(900, remaining=300, balance=-500)) == PRESS and rule_of(agent) == R4
    assert plan_after(before, at(899, remaining=301, balance=-500)) == RAID


# The late-PRESS guard switched on, with R4's window of two minutes, as the tuning file ships them.
LATE_GUARD_ON = tuning_off(
    plan_rules=dataclasses.replace(plan_rules_off(), press_late_seconds=120, late_press_guard=True)
)


def test_late_press_with_the_two_minute_window_120_seconds_left_and_a_negative_balance_the_plan_is_press_by_r4():
    agent = plans_agent(tuning=LATE_GUARD_ON)

    assert plan_after(agent, at(1080, remaining=120, balance=-500)) == PRESS and rule_of(agent) == R4
    assert plan_after(plans_agent(base_seen=True, tuning=LATE_GUARD_ON), at(1079, remaining=121, balance=-500)) == RAID


def test_late_press_with_the_two_minute_window_and_300_seconds_left_and_a_negative_balance_r4_does_not_press():
    agent = plans_agent(base_seen=True, tuning=LATE_GUARD_ON)

    assert (plan_after(agent, at(900, remaining=300, balance=-500)), rule_of(agent)) == (RAID, R7)


def test_late_press_with_the_guard_on_100_seconds_left_a_negative_balance_and_a_quiet_base_r4_presses():
    agent = plans_agent(base_seen=True, tuning=LATE_GUARD_ON)

    assert agent.ctx.threat_level == NO_THREAT
    assert (plan_after(agent, at(1100, remaining=100, balance=-500)), rule_of(agent)) == (PRESS, R4)


def test_without_a_clock_none_of_the_clock_rules_applies():
    state = at(1020, balance=100)
    state["time"] = {}

    assert plan_after(plans_agent(base_seen=True), state) == RAID


# -- R5: STRIKE ------------------------------------------------------------------------------------

# R5 applies only with the early-attack guard off (see "the early-attack guard: no STRIKE" below).


def test_at_200_seconds_against_a_seen_base_with_400_of_army_and_no_defense_main_of_1300_strikes():
    agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_OFF)

    assert plan_after(agent, at(200, units=MAIN_1300, enemies=rifles(4))) == STRIKE and rule_of(agent) == R5


@pytest.mark.parametrize(
    "base_seen, enemy_count, main", [(False, 4, MAIN_1300), (True, 6, MAIN_1300), (True, 4, MAIN_1300[:2])]
)
def test_strike_needs_the_base_seen_at_most_500_of_enemy_army_and_main_worth_1200(base_seen, enemy_count, main):
    agent = plans_agent(base_seen=base_seen, tuning=EARLY_GUARD_OFF)

    assert plan_after(agent, at(200, units=main, enemies=rifles(enemy_count))) != STRIKE


def test_with_no_enemy_army_and_one_remembered_power_plant_r5_comes_before_r6_and_the_plan_is_strike():
    agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_OFF)

    assert plan_after(agent, at(200, units=MAIN_1300, army=1300, buildings=[foe(600, "powr")])) == STRIKE


def test_a_pillbox_entering_memory_ends_the_strike_in_that_cycle_and_strike_is_never_chosen_again():
    agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_OFF)
    power = foe(600, "powr")
    pillbox = foe(601, "pbox", 70, 40)

    striking = plan_after(agent, at(200, units=MAIN_1300, buildings=[power]))
    with_pillbox = plan_after(agent, at(201, units=MAIN_1300, buildings=[power, pillbox]))
    # The pillbox is destroyed in sight: it leaves memory and the conditions of R5 hold once more.
    destroyed = at(202, units=MAIN_1300, buildings=[power], events=[situations.event(1, "enemy_killed")])
    after = [plan_after(agent, destroyed), plan_after(agent, at(203, units=MAIN_1300, buildings=[power]))]

    assert (striking, with_pillbox) == (STRIKE, RAID)
    assert 601 not in agent.world.enemy_memory and STRIKE not in after


def test_a_strike_whose_fight_turns_against_main_is_given_up_for_the_rest_of_the_match():
    agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_OFF)
    beaten = tuple({**unit, "hp": 25} for unit in MAIN_1300)

    striking = plan_after(agent, at(200, units=MAIN_1300, enemies=rifles(5, x=74)))
    losing = plan_after(agent, at(201, units=beaten, enemies=rifles(5, x=34)))
    healed = plan_after(agent, at(220, units=MAIN_1300, enemies=rifles(5, x=74)))

    # With the base seen and nothing to raid, the script holds.
    assert (striking, losing, healed) == (STRIKE, HOLD, HOLD)


def test_at_241_seconds_strike_is_not_started_but_one_under_way_goes_on_and_is_held():
    late = plans_agent(base_seen=True, tuning=EARLY_GUARD_OFF)
    under_way = plans_agent(base_seen=True, tuning=EARLY_GUARD_OFF)
    power = [foe(600, "powr")]
    plan_after(under_way, at(240, units=MAIN_1300, enemies=rifles(4), buildings=power))

    assert plan_after(late, at(241, units=MAIN_1300, enemies=rifles(4), buildings=power)) != STRIKE
    assert plan_after(under_way, at(241, units=MAIN_1300, enemies=rifles(4), buildings=power)) == STRIKE
    # 600 of enemy army is more than R5 allows, but the strike is held for 750 ticks; it is not given up.
    assert plan_after(under_way, at(242, units=MAIN_1300, enemies=rifles(6), buildings=power)) == STRIKE
    assert plan_after(under_way, at(270, units=MAIN_1300, enemies=rifles(6), buildings=power)) != STRIKE


# -- R6: FINISH ------------------------------------------------------------------------------------


# Five light tanks (3,500) in main, at the staging point.
MAIN_3500 = tuple(own(300 + number, "1tnk", 32 + number % 2, 47 + number // 2) for number in range(5))


def test_at_200_seconds_with_a_remembered_building_and_no_enemy_army_known_an_army_of_4000_does_not_finish():
    agent = plans_agent()

    assert plan_after(agent, at(200, army=4000, units=MAIN_3500, buildings=[foe(600, "powr")])) != FINISH


@pytest.mark.parametrize(
    "seen_ticks_ago, finishes", [(None, True), (1500, True), (1400, False)], ids=["never", "1,500", "1,400"]
)
def test_at_400_seconds_main_of_3500_finishes_only_when_no_enemy_unit_was_seen_for_1500_ticks(seen_ticks_ago, finishes):
    agent = plans_agent()
    power = foe(600, "powr")
    if seen_ticks_ago is not None:
        plan_after(agent, at((10_000 - seen_ticks_ago) / TICKS_PER_SECOND, buildings=[power], enemies=rifles(1)))

    plan = plan_after(agent, at(400, units=MAIN_3500, buildings=[power]))

    assert (plan == FINISH) is finishes and (rule_of(agent) == R6) is finishes


@pytest.mark.parametrize(
    "seconds, units, buildings",
    [
        (359, MAIN_3500, [foe(600, "powr")]),
        (400, MAIN_3500[:4], [foe(600, "powr")]),
        (400, MAIN_3500, [foe(600, "powr"), foe(601, "pbox")]),
        (400, MAIN_3500, []),
    ],
    ids=["before 360 s", "main of 2,800", "a remembered defense", "no building known"],
)
def test_r6b_needs_360_seconds_main_of_3000_a_remembered_building_and_no_remembered_defense(seconds, units, buildings):
    agent = plans_agent()

    assert plan_after(agent, at(seconds, army=9000, units=units, buildings=buildings)) != FINISH


# R6(b) at the shipped cleanup_min_strength, the other switches off; and the whole shipped tuning.
SHIPPED_CLEANUP = tuning_off(plan_rules=plan_rules_off(cleanup_min_strength=PlanRulesTuning().cleanup_min_strength))


def main_of(tanks: int, rockets: int, last_rocket_hp: int = 0) -> tuple:
    """Light tanks (700) and rocket soldiers (300) at full health in main, and one more rocket soldier at
    `last_rocket_hp` percent when given."""
    kinds = ["1tnk"] * tanks + ["e3"] * rockets + (["e3"] if last_rocket_hp else [])
    hps = [100] * (tanks + rockets) + ([last_rocket_hp] if last_rocket_hp else [])
    return tuple(
        own(300 + number, kind, 31 + number % 4, 46 + number // 4, hp)
        for number, (kind, hp) in enumerate(zip(kinds, hps))
    )


def test_cleanup_min_strength_ships_at_8000_built_in_and_in_the_tracked_file():
    for rules in (PlanRulesTuning(), Tuning.from_file(situations.REPO_ROOT).plan_rules):
        assert rules.cleanup_min_strength == 8000


@pytest.mark.parametrize("tuning", [SHIPPED_CLEANUP, Tuning()], ids=["shipped cleanup", "shipped tuning"])
@pytest.mark.parametrize(
    "units, strength, finishes",
    [
        (main_of(8, 3), 6500, False),
        (main_of(10, 3, 33), 7999, False),
        (main_of(11, 1), 8000, True),
        (main_of(9, 6), 8100, True),
    ],
    ids=["main of 6,500", "main of 7,999", "main of exactly 8,000", "main of 8,100"],
)
def test_r6b_at_the_shipped_8000_main_of_6500_does_not_finish_and_8000_inclusive_or_8100_does(
    tuning, units, strength, finishes, match_record
):
    agent = plans_agent(tuning=tuning)

    plan = plan_after(agent, at(400, units=units, buildings=[foe(600, "powr")]))

    assert match_record("plan_change")[-1]["inputs"]["main_strength"] == strength
    assert (plan == FINISH) is finishes and (rule_of(agent) == R6) is finishes


def finish_inputs(seconds: float, defenses: list[dict]) -> dict:
    """Base seen at 500 s, balance 0, army 4,000, enemy army 1,000 in sight, and the defenses given.

    The army is the state's army value alone, with no unit that fights: R6(a) weighs it with the early-attack guard off.
    """
    return at(seconds, army=4000, enemies=rifles(10), buildings=defenses)


@pytest.mark.parametrize(
    "seconds, defenses, finishes",
    [
        (500, [foe(600, "pbox")], True),
        (500, [foe(600, "gun"), foe(601, "gun", 70, 40)], False),
        (470, [foe(600, "pbox")], False),
    ],
    ids=["one pillbox: 1.3 x 2,200 = 2,860", "two guns: 1.3 x 4,200 = 5,460", "before 480 s"],
)
def test_r6a_finishes_with_an_army_of_1_3_times_the_enemy_s_army_and_twice_its_defenses(seconds, defenses, finishes):
    agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_OFF)

    assert (plan_after(agent, finish_inputs(seconds, defenses)) == FINISH) is finishes


def test_r6a_needs_the_base_seen_and_no_negative_balance():
    assert plan_after(plans_agent(tuning=EARLY_GUARD_OFF), finish_inputs(500, [foe(600, "pbox")])) != FINISH
    agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_OFF)
    assert plan_after(agent, at(500, balance=-1, army=4000, enemies=rifles(10), buildings=[foe(600, "pbox")])) != FINISH


# -- R7: the discretionary choice ------------------------------------------------------------------


def test_with_raid_and_hold_open_and_model_choice_off_the_plan_is_raid_by_r7():
    agent = plans_agent(base_seen=True)

    assert plan_after(agent, at(300)) == RAID and rule_of(agent) == R7


def test_raid_is_open_with_the_base_seen_only_while_an_uncovered_raid_target_is_known():
    covered = plans_agent(base_seen=True)
    uncovered = plans_agent(base_seen=True)
    pillbox = foe(601, "pbox", 74, 50)

    assert plan_after(covered, at(300, enemies=[foe(500, "harv", 74, 48)], buildings=[pillbox])) == HOLD
    assert plan_after(uncovered, at(300, enemies=[foe(500, "harv", 60, 30)], buildings=[pillbox])) == RAID


@pytest.mark.parametrize(
    "seconds, enemies, guarded, unguarded",
    [
        (400, [], HOLD, PRESS),
        (299, [RAID_TARGET], RAID, RAID),
        (299, [], HOLD, HOLD),
        (400, [RAID_TARGET], RAID, PRESS),
    ],
    ids=[
        "no target, base unseen at 400 s",
        "an uncovered harvester known",
        "no target before 300 s",
        "the base unseen at 400 s and a target",
    ],
)
def test_raid_needs_a_known_target_and_the_unseen_base_opens_press_only_with_the_blind_press_guard_off(
    seconds, enemies, guarded, unguarded
):
    on, off = plans_agent(tuning=EARLY_GUARD_OFF), plans_agent(tuning=GUARD_OFF)

    assert plan_after(on, at(seconds, enemies=enemies)) == guarded and rule_of(on) == R7
    assert plan_after(off, at(seconds, enemies=enemies)) == unguarded and rule_of(off) == R7


def test_blind_press_guard_off_with_a_scout_out_press_does_not_open_for_an_unseen_base():
    agent = plans_agent(tuning=GUARD_OFF)
    state = at(400, enemies=[], units=[own(300, "jeep")])
    ask(agent, at(399, enemies=[], units=[own(300, "jeep")], base=ALLIED_BASE[:-1]))
    agent.ctx.groups.assign(300, "scout")

    assert plan_after(agent, state) == HOLD


def test_a_soviet_base_raids_once_its_barracks_stands():
    assert plan_after(plans_agent(base_seen=True), at(300, faction="russia", base=SOVIET_BASE)) == RAID


def open_after(agent, state: dict) -> list[str]:
    """The discretionary plans open in the situation an agent sees once it has taken a state in."""
    ask(agent, state)
    situation = plans_module.observe(agent.world, agent.ctx, agent.ctx.tuning, None)
    return plans_module.open_plans(situation, agent.ctx.tuning.plan_rules)


def test_press_is_open_from_360_seconds_with_3000_of_army_and_1_5_times_the_enemy_s():
    def open_at(seconds: float, army: int, enemy_rifles: int) -> list[str]:
        agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_OFF)
        return open_after(agent, at(seconds, army=army, enemies=rifles(enemy_rifles), buildings=[foe(601, "pbox")]))

    assert open_at(360, 3000, 20) == [HOLD, PRESS]
    assert [open_at(359, 3000, 20), open_at(360, 2999, 0), open_at(360, 3000, 21)] == [[HOLD]] * 3


def test_a_discretionary_plan_is_held_for_750_ticks():
    agent = plans_agent(base_seen=True)
    pillbox = foe(601, "pbox", 74, 50)
    harvester = foe(500, "harv", 60, 30)

    held = [plan_after(agent, at(300, enemies=[], buildings=[pillbox]))]
    held += [plan_after(agent, at(seconds, enemies=[harvester], buildings=[pillbox])) for seconds in (301, 329.96)]
    changed = plan_after(agent, at(330, enemies=[harvester], buildings=[pillbox]))

    assert (held, changed) == ([HOLD, HOLD, HOLD], RAID)


def test_a_held_plan_that_is_no_longer_open_is_kept_until_its_750_ticks_are_over():
    agent = plans_agent(base_seen=True)
    pillbox = foe(601, "pbox", 74, 50)
    covered = [foe(500, "harv", 74, 48)]

    raiding = plan_after(agent, at(300, enemies=[foe(500, "harv", 60, 30)], buildings=[pillbox]))
    # The harvester is seen again where the pillbox covers it: nothing is left to raid.
    still = plan_after(agent, at(301, enemies=covered, buildings=[pillbox]))
    holding = plan_after(agent, at(330, enemies=covered, buildings=[pillbox]))

    assert (raiding, still, holding) == (RAID, RAID, HOLD)


def raid_or_finish(seconds: float, finish: bool) -> dict:
    """A state of an army of 3,500 that calls for FINISH by R6(b), or one in which twenty enemy rifles in sight leave
    the plan to R7: at a ratio of 1.75, with the base seen, the decision table raids the refinery."""
    return at(seconds, army=3500, units=MAIN_3500, buildings=[foe(600, "proc")], enemies=[] if finish else rifles(20))


def test_over_30_states_alternating_between_raid_and_finish_every_5_the_plan_changes_at_most_once():
    agent = plans_agent(base_seen=True)
    plan_after(agent, at(300))
    states = [raid_or_finish(400 + step * 0.2, finish=step // 5 % 2 == 0) for step in range(30)]

    plans = [plan_after(agent, state) for state in states]

    changes = sum(1 for before, after in zip([RAID, *plans], plans) if before != after)
    assert changes <= 1 and plans[-1] == plans[5]


def test_finish_left_is_not_taken_up_again_within_750_ticks():
    # A short hold and a short wait for unseen enemies, so that only the rest after FINISH keeps it off.
    rules = dataclasses.replace(plan_rules_off(), plan_hold_ticks=100, cleanup_unseen_ticks=100)
    agent = new_agent(tuning=tuning_off(plan_rules=rules))
    agent.ctx.scout_report = ScoutReport(enemy_base_seen=True)
    plan_after(agent, at(300))
    finishing = plan_after(agent, raid_or_finish(400, finish=True))
    left = plan_after(agent, raid_or_finish(430, finish=False))
    again = [plan_after(agent, raid_or_finish(seconds, finish=True)) for seconds in (440, 459.96, 460)]

    assert (finishing, left, again) == (FINISH, RAID, [RAID, RAID, FINISH])


def committing_agent():
    """An agent with a short plan hold and wait for unseen enemies, and FINISH committed for 90 s."""
    rules = dataclasses.replace(plan_rules_off(), plan_hold_ticks=100, cleanup_unseen_ticks=100, finish_commit=True)
    agent = new_agent(tuning=tuning_off(plan_rules=rules))
    agent.ctx.scout_report = ScoutReport(enemy_base_seen=True)
    plan_after(agent, at(300))
    return agent


def test_finish_commit_a_finish_r6_opened_stays_90_s_whatever_r7_says_and_with_the_switch_off_does_not():
    # R6(b) opens FINISH at 370 s; from 400 s twenty rifles in sight leave it to R7, before R6(a) is due at 480 s.
    agent = committing_agent()
    finishing = plan_after(agent, raid_or_finish(370, finish=True))
    kept = [plan_after(agent, raid_or_finish(seconds, finish=False)) for seconds in (400, 459.9, 460)]
    off = new_agent(tuning=tuning_off(plan_rules=dataclasses.replace(plan_rules_off(), plan_hold_ticks=100)))
    off.ctx.scout_report = ScoutReport(enemy_base_seen=True)
    plan_after(off, at(300), raid_or_finish(370, finish=True))

    assert (finishing, kept) == (FINISH, [FINISH, FINISH, RAID])
    assert plan_after(off, raid_or_finish(400, finish=False)) == RAID


def test_finish_commit_main_down_under_0_8_of_its_strength_at_the_opening_ends_it():
    agent = committing_agent()
    plan_after(agent, raid_or_finish(370, finish=True))
    weakened = at(400, army=2100, units=MAIN_3500[:3], buildings=[foe(600, "proc")], enemies=rifles(20))

    assert plan_after(agent, weakened) != FINISH


def test_finish_commit_base_defense_calling_main_home_ends_it():
    agent = committing_agent()
    plan_after(agent, raid_or_finish(370, finish=True))
    agent.ctx.groups.recall(MAIN)

    assert plan_after(agent, raid_or_finish(400, finish=False)) == RAID


def test_finish_commit_ships_on_at_90_s_and_0_8_in_the_built_ins_and_the_tracked_file():
    for shipped in (Tuning().plan_rules, Tuning.from_file(situations.REPO_ROOT).plan_rules):
        keys = (shipped.finish_commit_seconds, shipped.finish_commit_value_share)
        assert (shipped.finish_commit, keys) == (True, (90, 0.8))


def test_the_enemy_base_gone_ends_the_hold_of_finish():
    agent = plans_agent(base_seen=True)
    plan_after(agent, at(300))
    finishing = plan_after(agent, raid_or_finish(400, finish=True))
    destroyed = at(401, units=MAIN_3500, enemies=[], events=[situations.event(1, "enemy_killed")])

    # Nothing is left to raid or to finish once the base is gone: R7 holds.
    assert (finishing, plan_after(agent, destroyed)) == (FINISH, HOLD)


SPARRING_SETTINGS = RedAlertSettings(seat_plans={"Multi1": HOLD})


def tournament() -> DecisionContext:
    return DecisionContext(
        session_id=situations.SESSION_ID, tournament_id="t-1", game_type="red_alert", agent_id=situations.AGENT_ID
    )


def test_a_seat_pinned_by_the_sparring_settings_plays_its_plan_in_a_test_match():
    agent = plans_agent(settings=SPARRING_SETTINGS)

    assert plan_after(agent, at(300, seat="Multi1")) == HOLD and rule_of(agent) == SPARRING


@pytest.mark.parametrize(
    "seat, match_context",
    [("Multi1", tournament()), ("Multi0", situations.context())],
    ids=["a tournament match", "another seat"],
)
def test_the_sparring_pin_is_ignored_in_a_tournament_match_and_for_another_seat(seat, match_context):
    agent = plans_agent(settings=SPARRING_SETTINGS)

    assert plan_after(agent, at(299, seat=seat), match_context=match_context) == RAID and rule_of(agent) == R7


def test_a_pinned_plan_that_is_not_open_gives_way_to_the_script_s_choice():
    agent = plans_agent(settings=RedAlertSettings(seat_plans={"Multi0": PRESS}))

    assert plan_after(agent, at(299)) == RAID and rule_of(agent) == R7


# -- the record of the changes ---------------------------------------------------------------------


def test_each_change_of_plan_logs_exactly_one_plan_change_event_naming_its_rule(match_record):
    agent = plans_agent()
    states = [
        at(10, base=ALLIED_BASE[:-1]),
        at(100),
        at(101),
        at(1080, remaining=120, balance=-500),
        at(1090, remaining=110, balance=600),
        at(1100, remaining=100, balance=600),
        at(1101, remaining=99, balance=600),
    ]

    plans = [plan_after(agent, state) for state in states]

    changes = match_record("plan_change")
    # R4 gives way to R2 only once the balance has stood more than 500 ahead for 250 ticks.
    assert plans == [OPENING, RAID, RAID, PRESS, PRESS, PRESERVE, PRESERVE]
    assert [(change["old"], change["new"], change["rule"]) for change in changes] == [
        (None, OPENING, R1),
        (OPENING, RAID, R7),
        (RAID, PRESS, R4),
        (PRESS, PRESERVE, R2),
    ]


def test_a_plan_change_records_the_figures_it_was_decided_on(match_record):
    agent = plans_agent(base_seen=True)

    plan_after(agent, at(400, balance=-20, army=4500, units=MAIN_3500, buildings=[foe(600, "powr")]))

    (change,) = match_record("plan_change")
    inputs: dict[str, Any] = change["inputs"]
    assert (change["new"], change["game_seconds"]) == (FINISH, 400.0)
    assert (inputs["trade_balance"], inputs["army_value"], inputs["enemy_buildings"]) == (-20, 4500, 1)
    assert (inputs["remaining_seconds"], inputs["base_seen"], inputs["enemy_units"]) == (900.0, True, 0)
    assert (inputs["main_strength"], inputs["enemy_unseen_ticks"]) == (3500, None)
    # No enemy that fights is known: the table weighs an enemy army assumed as strong as the own.
    assert (inputs["ratio"], inputs["enemy_army_known"], inputs["threat_level"]) == (1.0, False, NO_THREAT)


# -- what the plans produce ------------------------------------------------------------------------


def composition_after(state: dict, plan: str):
    """What the unit queues make under a plan, for the world an agent has taken a state into."""
    agent = plans_agent()
    ask(agent, state)
    return agent.ctx.composition(plan, agent.world.family, agent.world)


@pytest.mark.parametrize("light_tanks, vehicle", [(0, "1tnk"), (3, "1tnk"), (4, "2tnk")])
def test_raid_on_an_allied_start_trains_light_tanks_until_the_quota_of_4_is_met(light_tanks, vehicle):
    tanks = tuple(own(300 + number, "1tnk") for number in range(light_tanks))
    state = at(300, units=tanks, base=(*ALLIED_BASE, ("fix", 30, 56)))

    assert list(composition_after(state, RAID).vehicle) == [vehicle]


@pytest.mark.parametrize("grenadiers, infantry", [(0, ["e2"]), (2, ["e2"]), (3, ["e1", "e2", "e3"])])
def test_raid_on_a_soviet_start_with_a_war_factory_trains_grenadiers_until_the_quota_of_3_is_met(grenadiers, infantry):
    soldiers = tuple(own(300 + number, "e2") for number in range(grenadiers))
    state = at(300, units=soldiers, faction="russia", base=SOVIET_BASE)

    assert list(composition_after(state, RAID).infantry) == infantry


def test_the_mix_s_vehicle_on_a_soviet_start_without_a_service_depot_is_the_flak_truck():
    answer = composition_after(at(300, faction="russia", base=SOVIET_BASE), HOLD)

    assert answer.vehicle == pytest.approx({"ftrk": 0.15})


@pytest.mark.parametrize("light_tanks, vehicle", [(0, "1tnk"), (3, "1tnk"), (4, "2tnk")])
def test_press_on_an_allied_start_trains_light_tanks_until_the_quota_of_4_is_met(light_tanks, vehicle):
    tanks = tuple(own(300 + number, "1tnk") for number in range(light_tanks))
    state = at(300, units=tanks, base=(*ALLIED_BASE, ("fix", 30, 56)))

    assert list(composition_after(state, PRESS).vehicle) == [vehicle]


# -- where main goes forward ------------------------------------------------------------------------

# Five light tanks (3,500) gathered at the staging point (32, 48): more than PRESS's 3,000.
GATHERED_TANKS = tuple(own(300 + number, "1tnk", 32 + number % 2, 47 + number // 2) for number in range(5))
POWER_PLANT, REFINERY = foe(600, "powr", 74, 48), foe(601, "proc", 70, 52)
GUNS = (foe(610, "gun", 72, 50), foe(611, "gun", 72, 46))


def press_orders(buildings: list[dict]) -> tuple[list[dict], Any]:
    """The orders of an agent pressing with its combat manager alone, and the agent, for one state at 400 s."""
    agent = new_agent(CombatManager(), plan_selector=situations.OpeningThen(PRESS))
    return ask(agent, at(400, units=GATHERED_TANKS, buildings=buildings)), agent


def test_with_every_remembered_enemy_building_covered_by_two_guns_main_does_not_advance():
    orders, agent = press_orders([POWER_PLANT, REFINERY, *GUNS])

    assert [order for order in orders if order["cmd"] == "attack_move"] == [] and agent.ctx.army == frozenset()


def test_with_every_building_covered_by_one_gun_main_is_strong_enough_to_go_on_the_nearest():
    orders, agent = press_orders([POWER_PLANT, REFINERY, GUNS[0]])

    assert orders == [{"cmd": "attack_move", "units": [300, 301, 302, 303, 304], "to": [70, 52]}]


# -- the FINISH sweep -------------------------------------------------------------------------------

# A map with a third start, besides the agent's (22, 48) and the enemy's (74, 48).
THIRD_START = (48, 10)
THREE_STARTS = {"rules": {"maps": {"fake-arena": {"bounds": [0, 0, 96, 96], "spawns": [[22, 48], [74, 48], [48, 10]]}}}}
GRID_CENTERS = [(x, y) for y in (12, 36, 60, 84) for x in (12, 36, 60, 84)]


def tanks_around(cell: tuple[int, int]) -> tuple:
    """The five light tanks of the army, standing idle around a cell."""
    return tuple(own(300 + number, "1tnk", cell[0] + number % 2, cell[1] + number // 2) for number in range(5))


def sweep_targets(agent, stands: list[tuple[int, int]], start_tick: int = 12_500, **more) -> list[list[int]]:
    """Where the army is sent in each of the states that show it standing at the cells given, in turn."""
    targets = []
    for step, cell in enumerate(stands):
        state = at((start_tick + 5 * step) / TICKS_PER_SECOND, units=tanks_around(cell), **more)
        orders = ask(agent, state, situations.context(THREE_STARTS))
        targets.append(next(order["to"] for order in orders if order["cmd"] == "attack_move"))
    return targets


def test_in_finish_with_no_building_known_the_sweep_visits_the_enemy_start_the_other_start_then_16_grid_centers():
    agent = new_agent(CombatManager(), plan_selector=situations.OpeningThen(FINISH))
    route = [(74, 48), THIRD_START, *GRID_CENTERS]

    targets = sweep_targets(agent, [(32, 48), *route])

    assert [tuple(target) for target in targets] == [*route, (74, 48)]


def test_the_sweep_stops_at_the_first_enemy_building_seen_and_goes_on_from_there_once_it_is_gone():
    agent = new_agent(CombatManager(), plan_selector=situations.OpeningThen(FINISH))
    sweep_targets(agent, [(32, 48), (74, 48)])
    yard = foe(700, "fact", 50, 14)

    seen = sweep_targets(agent, [(40, 20)], start_tick=12_600, buildings=[yard])
    # The yard is destroyed in sight: the sweep goes on to the point after the third start.
    gone = sweep_targets(agent, [(48, 10)], start_tick=12_700, events=[situations.event(1, "enemy_killed")])

    assert (seen, gone) == ([[50, 14]], [list(GRID_CENTERS[0])])


def test_with_no_building_left_a_remembered_mcv_is_the_army_s_target():
    agent = new_agent(CombatManager(), plan_selector=situations.OpeningThen(FINISH))

    targets = sweep_targets(agent, [(32, 48)], enemies=[foe(800, "mcv", 60, 70)])

    assert targets == [[60, 70]]


@pytest.mark.parametrize("enemy_cell, fights", [((56, 51), True), ((52, 57), False)], ids=["on the way", "9 cells off"])
def test_in_finish_main_fights_only_the_enemy_units_within_6_cells_of_its_way(enemy_cell, fights):
    agent = new_agent(CombatManager(), plan_selector=situations.OpeningThen(FINISH))
    sweep_targets(agent, [(32, 48)])
    rifle = foe(500, "e1", *enemy_cell)

    state = at(12_505 / TICKS_PER_SECOND, units=tanks_around((50, 47)), enemies=[rifle])
    orders = ask(agent, state, situations.context(THREE_STARTS))

    assert any(order["cmd"] == "attack" and order["target"] == 500 for order in orders) is fights
    assert any(order["cmd"] == "attack_move" and order["to"] == [74, 48] for order in orders) is not fights


# -- PRESERVE: groups come home --------------------------------------------------------------------


def test_at_180_seconds_left_with_a_positive_balance_a_group_30_cells_out_is_sent_to_the_staging_point():
    agent = new_agent(CombatManager(), RaidManager(), ScoutingManager())
    out = tanks_around((52, 48))

    orders = ask(agent, at(1020, remaining=180, balance=100, units=out))

    assert agent.current_plan == PRESERVE
    assert {"cmd": "move", "units": [300, 301, 302, 303, 304], "to": [32, 48]} in orders
    assert not [order for order in orders if order["cmd"] == "attack_move"]


def test_under_preserve_an_army_gone_forward_is_called_back_and_a_unit_near_the_base_stays():
    agent = new_agent(CombatManager(), plan_selector=situations.OpeningThen(PRESS))
    ask(agent, at(400, units=GATHERED_TANKS))
    agent._plan_selector.plan = PRESERVE
    forward = (*tanks_around((60, 40))[:4], own(304, "1tnk", 30, 48))

    orders = ask(agent, at(401, units=forward))

    assert orders == [{"cmd": "move", "units": [300, 301, 302, 303], "to": [32, 48]}] and agent.ctx.army == frozenset()


def test_under_preserve_a_large_main_is_sorted_into_far_and_near_without_comparing_one_unit_with_another(monkeypatch):
    agent = new_agent(CombatManager(), plan_selector=situations.OpeningThen(PRESERVE))
    far = [own(300 + number, "1tnk", 60 + number % 10, 30 + number // 10) for number in range(100)]
    # Gathered at the staging point (32, 48), where nothing moves them.
    near = [own(400 + number, "1tnk", 31 + number % 3, 47 + number // 3 % 3) for number in range(100)]

    def compared(*_arguments):
        raise AssertionError("two units were compared with each other")

    monkeypatch.setattr(Unit, "__eq__", compared)
    orders = ask(agent, at(1020, units=(*far, *near), enemies=()))

    recalled = sorted(
        unit for order in orders if order["cmd"] == "move" and order["to"] == [32, 48] for unit in order["units"]
    )
    assert recalled == list(range(300, 400))


def test_under_preserve_main_still_fights_an_enemy_that_comes_near_the_base():
    agent = new_agent(CombatManager(), plan_selector=situations.OpeningThen(PRESERVE))

    orders = ask(agent, at(1020, units=GATHERED_TANKS, enemies=[foe(500, "e1", 36, 48)]))

    assert any(order["cmd"] == "attack" and order["target"] == 500 for order in orders)


# -- spending the money at the end -----------------------------------------------------------------

# A base past its opening with three refineries, the power for them, a service depot, a pillbox and six harvesters.
LATE_BASE = (*ALLIED_BASE, ("proc", 18, 39), ("proc", 30, 39), ("powr", 34, 50), ("fix", 30, 56), ("pbox", 27, 46))
HARVESTERS = tuple(own(200 + number, "harv", 24, 36) for number in range(6))


def production_cost(orders: list[dict]) -> int:
    """What the build and train orders of a batch cost together."""
    items = situations.tracked_items()
    return sum(
        items[order["item"]].cost * order.get("count", 1) for order in orders if order["cmd"] in ("build", "train")
    )


def unit_trains(orders: list[dict]) -> list[tuple[str, int]]:
    return [(order["item"], order["count"]) for order in orders if order["cmd"] == "train"]


def test_at_180_seconds_left_with_a_zero_balance_r3_holds_and_2000_of_money_goes_below_300():
    agent = new_agent(UpkeepManager())

    orders = ask(agent, at(1020, remaining=180, balance=0, base=LATE_BASE, units=HARVESTERS, cash=2000))

    assert (agent.current_plan, rule_of(agent)) == (HOLD, R3)
    assert 2000 - production_cost(orders) < 300


def test_at_120_seconds_left_2000_of_money_goes_below_300_on_the_dearest_things_that_fight():
    agent = new_agent(UpkeepManager())

    orders = ask(agent, at(1080, remaining=120, balance=100, base=LATE_BASE, units=HARVESTERS, cash=2000))

    assert 2000 - production_cost(orders) < 300
    assert {"cmd": "train", "item": "2tnk", "count": 1} in orders and {"cmd": "build", "item": "gun"} in orders


@pytest.mark.parametrize(
    "remaining, trained",
    [(121, [("e1", 2)]), (120, [("jeep", 1), ("e1", 3)])],
    ids=["121 s: the reserve is kept", "120 s: the reserve is waived"],
)
def test_with_a_positive_balance_the_reserve_is_waived_only_from_120_seconds_left(remaining, trained):
    agent = new_agent(UpkeepManager())
    seconds = 1200 - remaining
    # With a rocket soldier standing, rifles are what the infantry mix is short of.
    units = (*HARVESTERS, own(300, "e3"))

    orders = ask(agent, at(seconds, remaining=remaining, balance=100, base=LATE_BASE, units=units, cash=500))

    assert agent.current_plan == PRESERVE and unit_trains(orders) == trained


def test_under_preserve_one_defense_against_infantry_is_built_a_minute_while_the_money_is_600():
    agent = new_agent(UpkeepManager())
    built = []
    for seconds, cash in ((1020, 600), (1050, 5000), (1080, 599), (1081, 600)):
        state = at(seconds, remaining=150, balance=100, base=LATE_BASE, units=HARVESTERS, cash=cash)
        built.append([order["item"] for order in ask(agent, state) if order.get("item") == "pbox"])

    assert built == [["pbox"], [], [], ["pbox"]]


def test_no_order_of_the_agent_ever_sells_a_building(match_record):
    agent = own_agent()
    states = [
        at(1200 - left, remaining=left, balance=balance, base=LATE_BASE, units=HARVESTERS, cash=3000)
        for left, balance in ((400, -300), (250, 0), (175, 0), (150, 200), (100, 200), (60, -100))
    ]

    for state in states:
        ask(agent, state)

    actions = match_record("action")
    orders = [order for action in actions if isinstance(action["action"], dict) for order in action["action"]["orders"]]
    assert orders and not [order for order in orders if order["cmd"] == "sell"]
    assert '"sell"' not in "".join(
        path.read_text(encoding="utf-8") for path in REDALERT_SOURCES if path.name != "batch.py"
    )


# -- the model's choice ----------------------------------------------------------------------------

MODEL_ON = RedAlertSettings(model_plan_choice=True)
# 280 s: RAID and HOLD are open, PRESS is not yet.
ASKED_AT = 7000


def answer(plan: str, outcome: str = "used") -> ChoiceResult:
    """What a decision source's answer naming a plan comes back as."""
    return ChoiceResult(plan if outcome == "used" else None, None, "stub:model", 400, outcome)


def model_plans(
    *results, steps: tuple[int, ...], settings: RedAlertSettings = MODEL_ON
) -> tuple[list[tuple[str, str]], StubChooser, Any]:
    """The plan and rule after each state at ASKED_AT plus each step in ticks, with the chooser answering as given.

    The chooser hands over one result a poll, in order; None is no answer yet. RAID and HOLD are open throughout.
    """
    chooser = StubChooser(*results)
    agent = new_agent(settings=settings, chooser=chooser)
    plans = []
    for step in steps:
        ask(agent, at((ASKED_AT + step) / TICKS_PER_SECOND))
        plans.append((agent.current_plan, rule_of(agent)))
    return plans, chooser, agent


def test_with_model_choice_on_an_answer_of_hold_20_ticks_later_makes_the_plan_hold_by_the_model():
    plans, chooser, _ = model_plans(None, answer(HOLD), steps=(0, 20))

    assert plans == [(RAID, R7), (HOLD, MODEL)]
    (request,) = chooser.submitted
    assert ([option.id for option in request.options], request.deadline_ms, request.state_tag) == (
        [HOLD, RAID],
        1500,
        str(ASKED_AT),
    )
    assert request.context.splitlines()[-2:] == ["plan=OPENING", "recent picks: none"]
    assert request.question == plan_prompt()


@pytest.mark.parametrize(
    "result, step",
    [(answer(HOLD), 300), (answer(PRESS), 20), (answer(HOLD, "timeout"), 20)],
    ids=["300 ticks late", "not open", "not used"],
)
def test_an_answer_too_late_for_a_plan_not_open_or_not_used_is_discarded(result, step):
    plans, _, _ = model_plans(None, result, steps=(0, step))

    assert plans == [(RAID, R7), (RAID, R7)]


def test_no_more_than_one_question_is_asked_in_125_ticks():
    _, chooser, _ = model_plans(steps=tuple(range(0, 255, 5)))

    assert [request.state_tag for request in chooser.submitted] == [str(ASKED_AT + step) for step in (0, 125, 250)]


def test_a_plan_the_model_chose_is_not_changed_by_its_next_answer_within_250_ticks():
    plans, _, _ = model_plans(None, answer(HOLD), *[None] * 25, answer(RAID), steps=tuple(range(0, 140, 5)))

    assert plans[1] == (HOLD, MODEL) and plans[-1] == (HOLD, MODEL)


# R4's window wider than R2's and R3's, so that an R4 a lead ends leaves the plan to R5 to R7, not to R2.
WIDE_LATE_WINDOW = tuning_off(plan_rules=dataclasses.replace(plan_rules_off(), press_late_seconds=300))


def test_a_chosen_plan_stands_until_one_of_r1_to_r6_applies_and_then_the_script_chooses_again():
    chooser = StubChooser(None, answer(HOLD))
    agent = new_agent(settings=MODEL_ON, chooser=chooser, tuning=WIDE_LATE_WINDOW)
    # With the base seen PRESS is not open, so the script's own choice is the raid.
    agent.ctx.scout_report = ScoutReport(enemy_base_seen=True)
    states = [at(300), at(300.8), at(310), at(340), at(900, remaining=300, balance=-500)]
    # A lead of 600 for 250 ticks ends R4; with 279 s left no clock rule applies, and the script chooses again.
    states += [at(911, remaining=289, balance=600), at(921, remaining=279, balance=600)]

    plans = [plan_after(agent, state) for state in states]

    # At 340 s the hold of the chosen plan is over, and it still stands: it is still open.
    assert plans == [RAID, HOLD, HOLD, HOLD, PRESS, PRESS, RAID]


def test_with_model_choice_off_or_one_plan_open_nobody_is_asked():
    off = StubChooser()
    plan_after(new_agent(chooser=off), at(300))
    one_open = StubChooser()
    agent = new_agent(settings=MODEL_ON, chooser=one_open)
    agent.ctx.scout_report = ScoutReport(enemy_base_seen=True)
    plan_after(agent, at(300, enemies=[]))

    assert off.submitted == [] and one_open.submitted == [] and agent.current_plan == HOLD


def test_a_plan_named_by_a_shorthand_line_is_taken_in_the_next_cycle_when_it_is_open():
    agent = plans_agent()
    plan_after(agent, at(299))
    selector = agent._plan_selector

    refused, taken = selector.request(PRESS), selector.request(HOLD)

    assert (refused, taken) == (False, True)
    assert (plan_after(agent, at(301)), rule_of(agent)) == (HOLD, MODEL)


@pytest.mark.parametrize("settings, built", [(MODEL_ON, ["red_alert_plan"]), (RedAlertSettings(), [])])
def test_the_plan_chain_is_built_only_when_a_model_may_choose_the_plan(monkeypatch, settings, built):
    calls = []
    monkeypatch.setattr(agent_module, "build_chain", lambda name: calls.append(name) or ChainSource([], name=name))

    agent = RedAlertAgent(items=situations.tracked_items(), tuning=tuning_off(), settings=settings)

    assert calls == built and isinstance(agent.ctx.chooser, AsyncChooser)


# -- the compact prompt ----------------------------------------------------------------------------

COMPACT_ON = RedAlertSettings(model_plan_choice=True, model_briefing=True, plan_prompt="compact")


def test_compact_prompt_with_plan_prompt_compact_the_model_is_asked_the_compact_question_whatever_model_briefing():
    _, chooser, _ = model_plans(steps=(0,), settings=COMPACT_ON)

    (request,) = chooser.submitted
    *values, picks = request.context.splitlines()
    keys = [line.partition("=")[0] for line in values]
    assert picks == "recent picks: none"
    assert (request.system_text, request.answer_tokens, request.reason_first) == (compact_prompt.SYSTEM_TEXT, 48, False)
    assert (request.question, request.option_ids(), keys) == ("OPEN:", [HOLD, RAID], list(compact_prompt.KEYS))
    assert [option.description for option in request.options] == [
        compact_prompt.OPTION_LINES[plan] for plan in (HOLD, RAID)
    ]
    assert (request.deadline_ms, request.state_tag) == (1500, str(ASKED_AT))


def test_compact_prompt_an_answer_of_hold_20_ticks_later_makes_the_plan_hold_by_the_model():
    plans, _, _ = model_plans(None, answer(HOLD), steps=(0, 20), settings=COMPACT_ON)

    assert plans == [(RAID, R7), (HOLD, MODEL)]


@pytest.mark.parametrize(
    "result, step",
    [(answer(PRESS), 20), (answer(None, "invalid"), 20), (answer(HOLD), 300), (answer(HOLD, "timeout"), 20)],
    ids=["a closed plan", "a closed plan the source refused", "300 ticks late", "not used"],
)
def test_compact_prompt_an_answer_naming_a_closed_plan_late_or_not_used_leaves_the_script_s_choice(result, step):
    plans, _, _ = model_plans(None, result, steps=(0, step), settings=COMPACT_ON)

    assert plans == [(RAID, R7), (RAID, R7)]


def test_compact_prompt_no_more_than_one_question_is_asked_in_125_ticks():
    _, chooser, _ = model_plans(steps=tuple(range(0, 255, 5)), settings=COMPACT_ON)

    assert [request.state_tag for request in chooser.submitted] == [str(ASKED_AT + step) for step in (0, 125, 250)]


def test_compact_prompt_reads_no_glossary_and_no_template_and_with_model_plan_choice_off_nobody_is_asked(monkeypatch):
    read = []
    monkeypatch.setattr(Glossary, "from_file", classmethod(lambda cls, root: read.append(root)))
    monkeypatch.setattr(plans_module, "PROMPT_FILE", situations.REPO_ROOT / "missing.txt")
    monkeypatch.setattr(plans_module, "BRIEFING_PROMPT_FILE", situations.REPO_ROOT / "missing.txt")
    off = dataclasses.replace(COMPACT_ON, model_plan_choice=False)

    _, asked, _ = model_plans(steps=(0,), settings=COMPACT_ON)
    _, not_asked, _ = model_plans(steps=(0,), settings=off)

    assert (read, len(asked.submitted), not_asked.submitted) == ([], 1, [])


def test_compact_prompt_with_plan_prompt_briefing_the_request_is_the_briefed_one_without_a_system_text():
    briefed = dataclasses.replace(COMPACT_ON, plan_prompt="briefing")

    _, chooser, _ = model_plans(steps=(0,), settings=briefed)

    (request,) = chooser.submitted
    assert (request.system_text, request.answer_tokens, request.question) == (None, None, plan_prompt(briefed=True))
    assert request.reason_first and request.context.startswith("## Situation")


def test_a_plan_chain_that_cannot_be_built_is_said_and_no_source_is_asked(match_record, monkeypatch):
    def broken(name):
        raise ConfigError(f"no chain named {name}")

    monkeypatch.setattr(agent_module, "build_chain", broken)

    agent = RedAlertAgent(items=situations.tracked_items(), tuning=tuning_off(), settings=MODEL_ON)

    (error,) = match_record("error")
    assert (error["where"], error["exception"]) == ("redalert.config", "ConfigError")
    assert agent.ctx.chooser.poll("1") is None


def test_with_the_prompt_template_unreadable_the_agent_says_so_and_asks_nobody(match_record, monkeypatch, tmp_path):
    monkeypatch.setattr(plans_module, "PROMPT_FILE", tmp_path / "missing.txt")
    chooser = StubChooser()
    agent = new_agent(settings=MODEL_ON, chooser=chooser)

    plan_after(agent, at(300))

    (error,) = match_record("error")
    assert (error["where"], error["exception"], chooser.submitted) == ("redalert.config", "FileNotFoundError", [])


class BusyChooser(StubChooser):
    """A chooser whose first question finds an earlier one still in flight."""

    def submit(self, request) -> bool:
        super().submit(request)
        return len(self.submitted) > 1


def test_a_question_the_chooser_could_not_take_is_put_again_in_the_next_cycle():
    chooser = BusyChooser()
    agent = new_agent(settings=MODEL_ON, chooser=chooser)

    for step in (0, 5, 10):
        plan_after(agent, at((ASKED_AT + step) / TICKS_PER_SECOND))

    assert [request.state_tag for request in chooser.submitted] == [str(ASKED_AT), str(ASKED_AT + 5)]


# -- the clock rules' hysteresis --------------------------------------------------------------------


def late(tick: int, balance: int) -> dict:
    """A late state at a tick, with as many seconds left as a 20-minute match has then."""
    return at(tick / TICKS_PER_SECOND, remaining=1200 - tick / TICKS_PER_SECOND, balance=balance)


def test_with_the_balance_alternating_between_100_and_minus_100_every_25_ticks_the_plan_changes_at_most_once():
    agent = plans_agent()
    plan_after(agent, at(1000, remaining=200))
    ticks = range(25_500, 25_755, 5)

    plans = [plan_after(agent, late(tick, 100 if (tick - 25_500) // 25 % 2 == 0 else -100)) for tick in ticks]

    changes = sum(1 for before, after in zip([RAID, *plans], plans) if before != after)
    assert changes <= 1 and set(plans) == {PRESERVE}


@pytest.mark.parametrize(
    "start, then, kept, turned",
    [(100, -600, PRESERVE, PRESS), (-100, 600, PRESS, PRESERVE), (0, 600, HOLD, PRESERVE), (0, -600, HOLD, PRESS)],
    ids=["R2 to R4", "R4 to R2", "R3 to R2", "R3 to R4"],
)
def test_a_clock_rule_gives_way_only_to_a_balance_more_than_500_the_other_way_for_250_ticks(start, then, kept, turned):
    agent = plans_agent()
    # 120 s left at tick 27,000: all three clock rules apply from there.
    entered = plan_after(agent, late(27_000, start))
    small = plan_after(agent, late(27_005, 400 if then > 0 else -400))
    plan_after(agent, late(27_010, then))

    plans = [plan_after(agent, late(tick, then)) for tick in (27_259, 27_260)]

    assert (entered, small, plans) == (kept, kept, [kept, turned])


# -- R1 to R6 are never overridden -----------------------------------------------------------------

# A plan selector whose plans are held only 10 ticks, so that a question R7 put can still be answered fresh while a
# STRIKE or a FINISH it gave way to is held.
SHORT_HOLD = tuning_off(plan_rules=dataclasses.replace(plan_rules_off(), plan_hold_ticks=10))


def asking_agent(*results, tuning: Tuning | None = None):
    """An agent that lets a model choose, with a stub chooser that hands over the results given, one a poll."""
    chooser = StubChooser(*results)
    return new_agent(settings=MODEL_ON, chooser=chooser, tuning=tuning), chooser


@pytest.mark.parametrize(
    "balance, remaining, plan, other",
    [(100, 180, PRESERVE, HOLD), (0, 180, HOLD, RAID), (-100, 120, PRESS, HOLD)],
    ids=["R2", "R3", "R4"],
)
def test_neither_a_model_answer_nor_a_shorthand_request_overrides_a_clock_rule(balance, remaining, plan, other):
    agent, chooser = asking_agent(None, answer(other), None)
    plan_after(agent, at(280))
    taken = agent._plan_selector.request(other)

    plans = [plan_after(agent, at(seconds, remaining=remaining, balance=balance)) for seconds in (280.8, 281)]

    assert len(chooser.submitted) == 1 and taken and plans == [plan, plan]


def test_neither_a_model_answer_nor_a_shorthand_request_overrides_a_held_strike():
    rules = dataclasses.replace(SHORT_HOLD.plan_rules, early_attack_guard=False)
    agent, _ = asking_agent(None, None, answer(HOLD), tuning=tuning_off(plan_rules=rules))
    agent.ctx.scout_report = ScoutReport(enemy_base_seen=True)
    power = [foe(600, "powr")]
    # Without main R5 does not apply, and R7 asks which of RAID and HOLD to play.
    plan_after(agent, at(200, enemies=[*rifles(4), RAID_TARGET], buildings=power))
    striking = plan_after(agent, at(200.8, units=MAIN_1300, enemies=rifles(4), buildings=power))
    agent._plan_selector.request(HOLD)

    # 600 of enemy army: R5 no longer applies, and the strike is only held.
    held = plan_after(agent, at(201, units=MAIN_1300, enemies=rifles(6), buildings=power))

    assert (striking, held, rule_of(agent)) == (STRIKE, STRIKE, R5)


def test_neither_a_model_answer_nor_a_shorthand_request_overrides_a_held_finish():
    agent, _ = asking_agent(None, None, answer(RAID), tuning=SHORT_HOLD)
    power = foe(600, "powr")
    plan_after(agent, at(400, buildings=[power]))
    finishing = plan_after(agent, at(400.8, units=MAIN_3500, buildings=[power], enemies=[]))
    agent._plan_selector.request(RAID)

    # A rifle in sight: R6(b) no longer applies, and FINISH is only held.
    held = plan_after(agent, at(401, units=MAIN_3500, buildings=[power], enemies=rifles(1)))

    assert (finishing, held, rule_of(agent)) == (FINISH, FINISH, R6)


PINNED_HOLD = RedAlertSettings(seat_plans={"Multi0": HOLD})


@pytest.mark.parametrize(
    "base_seen, state, plan, rule",
    [
        (False, at(1020, remaining=180, balance=100), PRESERVE, R2),
        (True, at(200, units=MAIN_1300, enemies=rifles(4)), STRIKE, R5),
        (False, at(400, units=MAIN_3500, buildings=[foe(600, "powr")], enemies=[]), FINISH, R6),
    ],
    ids=["a clock rule", "STRIKE", "FINISH"],
)
def test_a_sparring_pin_never_overrides_r1_to_r6(base_seen, state, plan, rule):
    # With the early-attack guard off, so that R5 applies.
    agent = plans_agent(base_seen=base_seen, settings=PINNED_HOLD, tuning=EARLY_GUARD_OFF)

    assert plan_after(agent, state) == plan and rule_of(agent) == rule


def test_a_pinned_plan_is_not_replaced_by_a_model_answer_or_a_request_and_no_question_is_put():
    chooser = StubChooser(None, answer(RAID))
    settings = RedAlertSettings(model_plan_choice=True, seat_plans={"Multi0": HOLD})
    agent = new_agent(settings=settings, chooser=chooser)
    pinned = plan_after(agent, at(280))
    agent._plan_selector.request(RAID)

    later = [plan_after(agent, at(seconds)) for seconds in (280.8, 281, 300)]

    assert (pinned, later, rule_of(agent)) == (HOLD, [HOLD, HOLD, HOLD], SPARRING)
    assert chooser.submitted == []


# -- the thresholds of the decision table and of FINISH's heavy share --------------------------------

PLAN_SAFETY_KEYS = ("default_weaker_ratio", "default_press_ratio", "finish_heavy_share")


def test_the_decision_table_and_heavy_share_thresholds_are_1_2_and_0_3_and_the_tracked_file_holds_them():
    rules = Tuning.from_file(situations.REPO_ROOT).plan_rules

    assert tuple(getattr(rules, key) for key in PLAN_SAFETY_KEYS) == (1.0, 2.0, 0.3)
    assert rules == PlanRulesTuning()


def write_tuning_with(root: Path, key: str, value: str | None) -> None:
    """Write the tracked tuning file under `root` with one key's line set to a value, or left out for None."""
    target = root / TUNING_FILE
    target.parent.mkdir(parents=True)
    replacement = "" if value is None else f"{key} = {value}"
    text = (situations.REPO_ROOT / TUNING_FILE).read_text(encoding="utf-8")
    target.write_text(re.sub(rf"^{key} = .*$", replacement, text, count=1, flags=re.MULTILINE), encoding="utf-8")


@pytest.mark.parametrize("key", PLAN_SAFETY_KEYS)
@pytest.mark.parametrize("value", [None, "-0.5"], ids=["missing", "negative"])
def test_a_decision_table_or_heavy_share_key_missing_or_negative_is_a_config_error_naming_it(tmp_path, key, value):
    write_tuning_with(tmp_path, key, value)

    with pytest.raises(ConfigError, match=rf"{key} in \[plan_rules\] must be a number of at least 0"):
        Tuning.from_file(tmp_path)


# -- the thresholds of the blind-PRESS guard ---------------------------------------------------------

BLIND_PRESS_KEYS = ("enemy_army_fresh_ticks", "assumed_enemy_share", "blind_press_guard", "press_unseen_base_seconds")


def test_blind_press_guard_is_on_with_3000_fresh_ticks_and_a_share_of_1_and_the_tracked_file_holds_them():
    built_in = PlanRulesTuning()
    tracked = Tuning.from_file(situations.REPO_ROOT).plan_rules

    for rules in (built_in, tracked):
        assert tuple(getattr(rules, key) for key in BLIND_PRESS_KEYS) == (3000, 1.0, True, 300)
    assert tracked == built_in


def test_blind_press_guard_false_in_the_file_switches_the_guard_off(tmp_path):
    write_tuning_with(tmp_path, "blind_press_guard", "false")

    assert Tuning.from_file(tmp_path).plan_rules == dataclasses.replace(PlanRulesTuning(), blind_press_guard=False)


@pytest.mark.parametrize("value", [None, "1", '"true"'], ids=["missing", "a number", "text"])
def test_blind_press_guard_missing_or_not_true_or_false_is_a_config_error_naming_it(tmp_path, value):
    write_tuning_with(tmp_path, "blind_press_guard", value)

    with pytest.raises(ConfigError, match=r"blind_press_guard in \[plan_rules\] must be true or false"):
        Tuning.from_file(tmp_path)


@pytest.mark.parametrize(
    "key, value, expected",
    [("enemy_army_fresh_ticks", "-1", "a whole number of at least 0"), ("assumed_enemy_share", "-0.5", "a number")],
    ids=["negative fresh ticks", "negative share"],
)
def test_blind_press_guard_thresholds_that_are_negative_are_config_errors_naming_them(tmp_path, key, value, expected):
    write_tuning_with(tmp_path, key, value)

    with pytest.raises(ConfigError, match=rf"{key} in \[plan_rules\] must be {expected}"):
        Tuning.from_file(tmp_path)


# -- the thresholds of the late PRESS ----------------------------------------------------------------


def test_late_press_window_is_120_seconds_with_the_guard_on_and_the_tracked_file_holds_them():
    built_in = PlanRulesTuning()
    tracked = Tuning.from_file(situations.REPO_ROOT).plan_rules

    for rules in (built_in, tracked):
        assert (rules.press_late_seconds, rules.late_press_guard) == (120, True)
    assert tracked == built_in


def test_late_press_guard_false_in_the_file_switches_the_guard_off(tmp_path):
    write_tuning_with(tmp_path, "late_press_guard", "false")

    assert Tuning.from_file(tmp_path).plan_rules == dataclasses.replace(PlanRulesTuning(), late_press_guard=False)


@pytest.mark.parametrize(
    "key, value, expected",
    [
        ("late_press_guard", None, "true or false"),
        ("late_press_guard", "1", "true or false"),
        ("late_press_guard", '"true"', "true or false"),
        ("press_late_seconds", "-1", "a whole number of at least 0"),
    ],
    ids=["guard missing", "guard a number", "guard text", "negative window"],
)
def test_late_press_keys_missing_or_of_the_wrong_kind_are_config_errors_naming_them(tmp_path, key, value, expected):
    write_tuning_with(tmp_path, key, value)

    with pytest.raises(ConfigError, match=rf"{key} in \[plan_rules\] must be {expected}"):
        Tuning.from_file(tmp_path)


# -- the thresholds of the early-attack guard ---------------------------------------------------------

EARLY_ATTACK_KEYS = ("early_attack_guard", "early_press_until_seconds", "early_press_kill_value")


def test_early_attack_guard_ships_on_with_540_seconds_and_1500_of_kills_and_the_tracked_file_holds_them():
    built_in = PlanRulesTuning()
    tracked = Tuning.from_file(situations.REPO_ROOT).plan_rules

    for rules in (built_in, tracked):
        assert tuple(getattr(rules, key) for key in EARLY_ATTACK_KEYS) == (True, 540, 1500)
    assert tracked == built_in


def test_early_attack_guard_false_in_the_file_switches_the_guard_off(tmp_path):
    write_tuning_with(tmp_path, "early_attack_guard", "false")

    assert Tuning.from_file(tmp_path).plan_rules == dataclasses.replace(PlanRulesTuning(), early_attack_guard=False)


@pytest.mark.parametrize(
    "key, value, expected",
    [
        ("early_attack_guard", None, "true or false"),
        ("early_attack_guard", "1", "true or false"),
        ("early_attack_guard", '"true"', "true or false"),
        ("early_press_until_seconds", None, "a whole number of at least 0"),
        ("early_press_until_seconds", "-1", "a whole number of at least 0"),
        ("early_press_until_seconds", "540.5", "a whole number of at least 0"),
        ("early_press_kill_value", None, "a whole number of at least 0"),
        ("early_press_kill_value", "-1500", "a whole number of at least 0"),
    ],
    ids=[
        "guard missing",
        "guard a number",
        "guard text",
        "seconds missing",
        "negative seconds",
        "fractional seconds",
        "kill value missing",
        "negative kill value",
    ],
)
def test_early_attack_keys_missing_or_of_the_wrong_kind_are_config_errors_naming_them(tmp_path, key, value, expected):
    write_tuning_with(tmp_path, key, value)

    with pytest.raises(ConfigError, match=rf"{key} in \[plan_rules\] must be {expected}"):
        Tuning.from_file(tmp_path)


# -- the decision table of the script's discretionary choice -----------------------------------------

# The enemy's combat value of a hand-built situation; the own army is worth the ratio times this.
ENEMY_VALUE = 1000
# A hand-built situation gives the own army as the army value alone, which the rules weigh with the early-attack guard
# off; PRESS then opens before the ninth minute on its own rules.
TABLE_RULES = EARLY_GUARD_OFF.plan_rules
ALL_OPEN = [HOLD, RAID, PRESS]


def situation(ratio: float, *, base_seen: bool = True) -> Situation:
    """A hand-built situation at 400 s, even on the trade balance, at the ratio given."""
    return Situation(
        tick=10_000,
        game_seconds=400.0,
        remaining_seconds=800.0,
        trade_balance=0,
        army_value=round(ratio * ENEMY_VALUE),
        main_strength=0,
        enemy_combat_value=ENEMY_VALUE,
        enemy_units=10,
        enemy_buildings=1,
        enemy_defenses=0,
        defense_cost=0,
        base_seen=base_seen,
        has_war_factory=True,
        can_raid=True,
        raid_target_known=True,
        enemy_unseen_ticks=0,
        scout_active=False,
        enemy_army_known=True,
        ratio=ratio,
    )


@pytest.mark.parametrize(
    "ratio, base_seen, threat, eligible, plan",
    [
        (4.0, True, MAJOR_THREAT, ALL_OPEN, HOLD),
        (0.4, True, NO_THREAT, [HOLD, RAID], HOLD),
        (2.5, True, NO_THREAT, ALL_OPEN, PRESS),
        (1.6, True, NO_THREAT, ALL_OPEN, RAID),
        (1.2, False, NO_THREAT, [HOLD, PRESS], HOLD),
        (1.4, True, NO_THREAT, [HOLD, RAID], RAID),
        (1.4, True, MINOR_THREAT, [HOLD, RAID], RAID),
        (2.5, True, MINOR_THREAT, ALL_OPEN, PRESS),
        (1.0, True, NO_THREAT, [HOLD, RAID], RAID),
        (0.99, False, NO_THREAT, ALL_OPEN, HOLD),
        (2.0, True, NO_THREAT, [HOLD, PRESS], PRESS),
        (1.99, True, NO_THREAT, [HOLD, PRESS], HOLD),
    ],
    ids=[
        "1 threat first",
        "2 weaker",
        "3 stronger",
        "3 ratio 1.6 with the base seen raids",
        "an unseen base with press open holds",
        "5 raid",
        "7 a minor threat does not force hold",
        "7 a minor threat leaves press to the stronger",
        "a ratio of exactly 1 is not weaker",
        "0.99 with the base unseen holds as weaker",
        "a ratio of exactly 2 presses",
        "1.99 with the base seen and no raid holds",
    ],
)
def test_script_choice_takes_the_first_rule_of_the_decision_table_that_applies(
    ratio, base_seen, threat, eligible, plan
):
    assert script_choice(situation(ratio, base_seen=base_seen), threat, eligible, TABLE_RULES) == plan


@pytest.mark.parametrize(
    "ratio, base_seen, eligible, plan",
    [
        (1.2, False, [HOLD, PRESS], PRESS),
        (1.2, True, [HOLD, PRESS], HOLD),
        (1.2, False, [HOLD, RAID], RAID),
        (0.99, False, ALL_OPEN, HOLD),
    ],
    ids=["the base unseen presses", "the base seen holds", "press not open raids", "weaker holds first"],
)
def test_script_choice_with_the_blind_press_guard_off_presses_an_unseen_base_when_press_is_open(
    ratio, base_seen, eligible, plan
):
    assert script_choice(situation(ratio, base_seen=base_seen), NO_THREAT, eligible, GUARD_OFF.plan_rules) == plan


@pytest.mark.parametrize("guard, ratio", [(True, 1.0), (False, 99.0)], ids=["guard on", "guard off"])
def test_script_choice_blind_sees_a_ratio_of_1_with_the_guard_on_and_99_with_it_off_and_raids(guard, ratio):
    rules = dataclasses.replace(TABLE_RULES, blind_press_guard=guard)
    agent = new_agent(tuning=tuning_off(plan_rules=rules))
    agent.ctx.scout_report = ScoutReport(enemy_base_seen=True)

    # Only the uncovered harvester is in sight: no enemy that fights is known.
    raided = plan_after(agent, at(300, army=2000))
    seen = plans_module.observe(agent.world, agent.ctx, agent.ctx.tuning, None)

    assert (seen.enemy_combat_value, seen.enemy_army_known, seen.ratio) == (0, False, ratio)
    assert (raided, rule_of(agent)) == (RAID, R7)
    assert script_choice(seen, NO_THREAT, [HOLD, RAID], agent.ctx.tuning.plan_rules) == RAID


def test_script_choice_sees_the_army_value_over_the_enemy_s_combat_value():
    agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_OFF)

    plan = plan_after(agent, at(300, army=3000, enemies=[RAID_TARGET, *rifles(20)]))

    assert plans_module.observe(agent.world, agent.ctx, agent.ctx.tuning, None).ratio == 1.5 and plan == RAID


def test_script_choice_returns_only_open_plans_across_200_random_situations():
    chance = random.Random(2026)
    ratios = [0.0, 0.5, 0.99, 1.0, 1.5, 1.99, 2.0, 3.0, 99.0]
    threats = [NO_THREAT, MINOR_THREAT, MAJOR_THREAT]
    chosen = []
    for _ in range(200):
        eligible = [HOLD, *[plan for plan in (RAID, PRESS) if chance.random() < 0.5]]
        seen = situation(chance.choice(ratios), base_seen=chance.random() < 0.5)
        threat = chance.choice(threats)
        plan = script_choice(seen, threat, eligible, TABLE_RULES)
        chosen.append(plan)
        assert plan in eligible and (threat != MAJOR_THREAT or plan == HOLD)

    assert set(chosen) == {HOLD, RAID, PRESS}


def test_script_choice_under_a_major_threat_holds_with_raid_open_and_the_change_records_ratio_and_threat(match_record):
    agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_OFF)
    agent.ctx.threat_level = MAJOR_THREAT

    plan = plan_after(agent, at(300, army=2000))

    (change,) = match_record("plan_change")
    assert (plan, rule_of(agent)) == (HOLD, R7)
    # No enemy army is known: the ratio is that of an army assumed as strong as the own.
    assert (change["inputs"]["ratio"], change["inputs"]["threat_level"]) == (1.0, MAJOR_THREAT)


def test_script_choice_when_weaker_holds_and_the_change_records_the_ratio(match_record):
    agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_OFF)

    plan = plan_after(agent, at(300, army=400, enemies=[RAID_TARGET, *rifles(10)]))

    (change,) = match_record("plan_change")
    assert (plan, rule_of(agent), change["inputs"]["ratio"]) == (HOLD, R7, 0.4)


# -- the enemy army known, and the ratio against an army assumed as strong as the own ----------------


def known_army_at_400_seconds(seen_ticks_ago: int | None, enemies: list[dict]) -> Situation:
    """What the plan rules saw at tick 10,000 (400 s) when the enemies given were in sight `seen_ticks_ago` before.

    The army of 6,000 is the state's army value, weighed with the early-attack guard off.
    """
    agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_OFF)
    if seen_ticks_ago is not None:
        plan_after(agent, at((10_000 - seen_ticks_ago) / TICKS_PER_SECOND, enemies=enemies))
    plan_after(agent, at(400, army=6000))
    return agent._plan_selector.situation


@pytest.mark.parametrize(
    "seen_ticks_ago, known, ratio",
    [(None, False, 1.0), (750, True, 3.0), (2999, True, 3.0), (3000, False, 1.0), (3750, False, 1.0)],
    ids=["never", "30 s ago", "2,999 ticks ago", "3,000 ticks ago", "150 s ago"],
)
def test_known_army_needs_an_enemy_unit_that_fights_seen_less_than_3000_ticks_ago(seen_ticks_ago, known, ratio):
    seen = known_army_at_400_seconds(seen_ticks_ago, rifles(20))

    # The twenty rifles are stale to the fight from 750 ticks on; a known army is weighed at the 2,000 they were worth.
    assert (seen.enemy_army_known, seen.enemy_combat_value, seen.ratio) == (known, 0, ratio)
    assert seen.recent_enemy_army_value == (2000 if known else 0)


@pytest.mark.parametrize(
    "enemies, known",
    [
        ([foe(500, "harv")], False),
        ([foe(500, "mcv")], False),
        ([foe(500, "heli")], False),
        ([foe(500, "yak")], False),
        ([foe(500, "zzzz")], False),
        ([foe(500, "e1")], True),
        ([foe(500, "1tnk")], True),
    ],
    ids=["a harvester", "an MCV", "a helicopter", "a fighter plane", "a type of no known facts", "a rifle", "a tank"],
)
def test_known_army_is_made_by_a_unit_that_fights_on_the_ground_only(enemies, known):
    assert known_army_at_400_seconds(750, enemies).enemy_army_known is known


def test_known_army_is_not_made_by_a_remembered_defense():
    agent = plans_agent(base_seen=True)

    plan_after(agent, at(400, army=6000, buildings=[foe(601, "pbox")]))
    seen = plans_module.observe(agent.world, agent.ctx, agent.ctx.tuning, None)

    assert (seen.enemy_defenses, seen.enemy_army_known) == (1, False)


@pytest.mark.parametrize(
    "known, guard, share, recent, weighed",
    [
        (False, True, 1.0, 3000, 6000.0),
        (True, True, 1.0, 0, 2000),
        (True, True, 1.0, 3000, 3000),
        (True, True, 1.0, 1000, 2000),
        (False, False, 1.0, 3000, 2000),
        (True, False, 1.0, 3000, 2000),
        (False, True, 0.5, 0, 3000.0),
    ],
    ids=[
        "unknown: the share of the own",
        "known: the remembered",
        "known: the recent when larger",
        "known: the remembered when larger",
        "guard off, unknown: the remembered",
        "guard off, known: the remembered",
        "a share of 0.5",
    ],
)
def test_known_army_decides_whether_the_enemy_value_weighed_is_assumed_recent_or_remembered(
    known, guard, share, recent, weighed
):
    rules = dataclasses.replace(plan_rules_off(), blind_press_guard=guard, assumed_enemy_share=share)

    assert plans_module.weighed_enemy_value(6000, 2000, recent, known, rules) == weighed


def test_known_army_plan_change_inputs_carry_enemy_army_known(match_record):
    agent = plans_agent()

    plan_after(agent, at(10, base=ALLIED_BASE[:-1]))
    # Two enemy rifles in sight against no army of its own: the table holds.
    plan_after(agent, at(100, enemies=[RAID_TARGET, *rifles(2)]))

    changes = [(change["new"], change["inputs"]["enemy_army_known"]) for change in match_record("plan_change")]
    assert changes == [(OPENING, False), (HOLD, True)]


# -- no PRESS against an unknown enemy army or an unseen base ----------------------------------------

BLIND_ARMY = 6000


def blind_situation(
    seconds: float,
    *,
    known: bool,
    army: int = BLIND_ARMY,
    enemy_value: int = 0,
    recent: int = 0,
    base_seen: bool = True,
    raid_target_known: bool = True,
    rules: PlanRulesTuning = TABLE_RULES,
) -> Situation:
    """A hand-built situation at a game second with the army given, an even balance and no scout out, the enemy army
    known or not, worth what is remembered of it and `recent` lately, its ratio weighed as the rules weigh it."""
    weighed = plans_module.weighed_enemy_value(army, enemy_value, recent, known, rules)
    return Situation(
        tick=round(seconds * TICKS_PER_SECOND),
        game_seconds=seconds,
        remaining_seconds=1200 - seconds,
        trade_balance=0,
        army_value=army,
        main_strength=army,
        enemy_combat_value=enemy_value,
        enemy_units=enemy_value // 100,
        enemy_buildings=0,
        enemy_defenses=0,
        defense_cost=0,
        base_seen=base_seen,
        has_war_factory=True,
        can_raid=True,
        raid_target_known=raid_target_known,
        enemy_unseen_ticks=None,
        scout_active=False,
        enemy_army_known=known,
        ratio=army / weighed if weighed > 0 else tuning_off().combat.ratio_without_enemy,
        recent_enemy_army_value=recent,
    )


def table_choice(seen: Situation, rules: PlanRulesTuning = TABLE_RULES) -> tuple[list[str], str]:
    """The plans open in a situation, and the decision table's choice among them without a threat."""
    eligible = plans_module.open_plans(seen, rules)
    return eligible, script_choice(seen, NO_THREAT, eligible, rules)


@pytest.mark.parametrize(
    "raid_target_known, eligible, plan", [(True, [HOLD, RAID], RAID), (False, [HOLD], HOLD)], ids=["raid open", "not"]
)
def test_blind_1_never_seen_at_360_seconds_with_6000_press_is_not_open_and_the_table_raids_or_holds(
    raid_target_known, eligible, plan
):
    seen = blind_situation(360, known=False, raid_target_known=raid_target_known)

    assert (table_choice(seen), seen.ratio) == ((eligible, plan), 1.0)


@pytest.mark.parametrize("enemies, plan", [([RAID_TARGET], RAID), ([], HOLD)], ids=["a target in sight", "none"])
def test_blind_1_never_seen_at_360_seconds_with_6000_an_agent_raids_or_holds(enemies, plan):
    agent = plans_agent(base_seen=True)

    assert (plan_after(agent, at(360, army=BLIND_ARMY, enemies=enemies)), rule_of(agent)) == (plan, R7)


def test_known_army_2_of_2000_seen_30_seconds_ago_opens_press_and_the_table_presses_at_a_ratio_of_3():
    seen = blind_situation(360, known=True, enemy_value=2000)

    assert (table_choice(seen), seen.ratio) == (([HOLD, RAID, PRESS], PRESS), 3.0)


def test_known_army_3_last_seen_150_seconds_ago_is_unknown_and_press_is_not_open():
    agent = plans_agent(base_seen=True)
    plan_after(agent, at(250, enemies=[RAID_TARGET, *rifles(20)]))

    # The twenty rifles were last in sight 3,750 ticks before tick 10,000.
    plan = plan_after(agent, at(400, army=BLIND_ARMY))
    seen = plans_module.observe(agent.world, agent.ctx, agent.ctx.tuning, None)

    assert (seen.tick, seen.enemy_army_known) == (10_000, False)
    assert table_choice(seen, agent.ctx.tuning.plan_rules) == ([HOLD, RAID], RAID) and plan == RAID


def test_unseen_base_4_at_400_seconds_with_no_scout_and_an_unknown_army_does_not_open_press():
    hand_built = blind_situation(400, known=False, base_seen=False, raid_target_known=False)
    agent = plans_agent()

    plan = plan_after(agent, at(400, army=BLIND_ARMY, enemies=[]))
    seen = plans_module.observe(agent.world, agent.ctx, agent.ctx.tuning, None)

    assert table_choice(hand_built) == ([HOLD], HOLD)
    assert (seen.base_seen, seen.scout_active, seen.enemy_army_known) == (False, False, False)
    assert (plan, rule_of(agent)) == (HOLD, R7)


@pytest.mark.parametrize(
    "enemies, known", [([RAID_TARGET], False), ([RAID_TARGET, *rifles(20)], True)], ids=["unknown army", "known army"]
)
def test_known_army_5_at_1080_seconds_with_a_negative_balance_r4_still_presses(enemies, known):
    agent = plans_agent()

    plan = plan_after(agent, at(1080, remaining=120, balance=-500, army=BLIND_ARMY, enemies=enemies))
    seen = plans_module.observe(agent.world, agent.ctx, agent.ctx.tuning, None)

    assert (plan, rule_of(agent), seen.enemy_army_known) == (PRESS, R4, known)


def test_blind_press_guard_off_opens_press_against_an_army_never_seen_and_an_unseen_base():
    off = GUARD_OFF.plan_rules
    never_seen = blind_situation(360, known=False, rules=off)
    unseen_base = blind_situation(400, known=False, base_seen=False, raid_target_known=False, rules=off)
    agent = plans_agent(tuning=GUARD_OFF)

    assert (table_choice(never_seen, off), never_seen.ratio) == (([HOLD, RAID, PRESS], PRESS), 99.0)
    assert table_choice(unseen_base, off) == ([HOLD, PRESS], PRESS)
    assert (plan_after(agent, at(360, army=BLIND_ARMY)), rule_of(agent)) == (PRESS, R7)


def test_blind_press_is_never_opened_against_an_unknown_army_or_by_an_unseen_base_across_200_random_situations():
    chance = random.Random(2026)
    opened_with_the_guard_off = 0
    for _ in range(200):
        seen = dataclasses.replace(
            blind_situation(chance.uniform(0, 1200), known=False, base_seen=chance.random() < 0.5),
            army_value=chance.randrange(0, 50_000, 50),
            enemy_combat_value=chance.choice([0, 0, 500, 5000]),
            scout_active=chance.random() < 0.5,
        )

        assert PRESS not in plans_module.open_plans(seen, TABLE_RULES)
        opened_with_the_guard_off += PRESS in plans_module.open_plans(seen, GUARD_OFF.plan_rules)

    # The same situations open PRESS often with the guard off: the ones above were not closed by chance.
    assert opened_with_the_guard_off > 50


# -- a known enemy army is worth at least the most it was lately --------------------------------------

THIRTY_RIFLES = rifles(30)


def seen_then_gone(seconds: float, *, raid_target: bool, tuning: Tuning = EARLY_GUARD_OFF) -> tuple[Situation, Any]:
    """The situation the plan rules saw at a game second with an army of 5,000, and the agent, after thirty enemy
    rifles (3,000) were in sight at tick 10,000 (400 s) and none since; an uncovered harvester in sight when asked.

    The army is the state's army value; the thresholds by default have the early-attack guard off, which weighs it.
    """
    agent = plans_agent(base_seen=True, tuning=tuning)
    harvester = [RAID_TARGET] if raid_target else []
    plan_after(agent, at(400, enemies=[*THIRTY_RIFLES, *harvester]))
    plan_after(agent, at(seconds, army=5000, enemies=harvester))
    return agent._plan_selector.situation, agent


@pytest.mark.parametrize("raid_target, plan", [(True, RAID), (False, HOLD)], ids=["raid open", "raid not open"])
def test_known_army_7_of_3000_seen_1500_ticks_ago_opens_press_but_at_a_ratio_of_1_67_the_table_does_not_press(
    raid_target, plan
):
    seen, agent = seen_then_gone(460, raid_target=raid_target)
    open_plans = [HOLD, RAID, PRESS] if raid_target else [HOLD, PRESS]

    assert (seen.tick, seen.enemy_combat_value, seen.recent_enemy_army_value) == (11_500, 0, 3000)
    assert (seen.enemy_army_known, seen.ratio) == (True, pytest.approx(5000 / 3000))
    assert table_choice(seen) == (open_plans, plan) and (agent.current_plan, rule_of(agent)) == (plan, R7)


def test_known_army_7_hand_built_opens_press_and_the_table_raids_at_a_ratio_of_1_67():
    seen = blind_situation(460, known=True, army=5000, recent=3000)

    assert (table_choice(seen), seen.ratio) == (([HOLD, RAID, PRESS], RAID), pytest.approx(1.67, abs=0.005))


def test_known_army_7_records_the_recent_value_in_the_plan_change_inputs(match_record):
    seen_then_gone(460, raid_target=True)

    _, change = match_record("plan_change")
    assert (change["new"], change["inputs"]["recent_enemy_army_value"], change["inputs"]["ratio"]) == (
        RAID,
        3000,
        pytest.approx(5000 / 3000),
    )


@pytest.mark.parametrize(
    "seconds, known, recent, ratio",
    [(519.96, True, 3000, 5000 / 3000), (520, False, 0, 1.0)],
    ids=["2,999 ticks after", "3,000 ticks after"],
)
def test_known_army_recent_value_lasts_as_long_as_the_army_is_known(seconds, known, recent, ratio):
    seen, _ = seen_then_gone(seconds, raid_target=True)

    assert (seen.enemy_army_known, seen.recent_enemy_army_value, seen.ratio) == (known, recent, pytest.approx(ratio))


def test_blind_press_guard_off_weighs_the_remembered_0_and_presses_an_army_seen_1500_ticks_ago():
    seen, agent = seen_then_gone(460, raid_target=True, tuning=GUARD_OFF)

    assert (seen.recent_enemy_army_value, seen.ratio) == (3000, 99.0)
    assert table_choice(seen, GUARD_OFF.plan_rules) == ([HOLD, RAID, PRESS], PRESS) and agent.current_plan == PRESS


def test_known_army_recent_maximum_keeps_the_largest_value_of_the_span_and_lets_it_go_when_it_is_older():
    recent = plans_module.RecentMaximum(3000)
    for tick, value in ((100, 1000), (200, 3000), (300, 2000), (400, 0)):
        recent.note(tick, value)

    largest = [recent.largest(tick) for tick in (400, 3199, 3200, 3299, 3300, 3399, 3400)]

    assert largest == [3000, 3000, 2000, 2000, 0, 0, 0]
    assert plans_module.RecentMaximum(3000).largest(0) == 0


# -- a major threat ends a discretionary plan --------------------------------------------------------

# Four heavy tanks (3,400) at the own base: the attack base defense calls a major threat.
ATTACKERS = [foe(510 + number, "2tnk", 32, 46 + number) for number in range(4)]


def pressing_then_attacked(threat: str) -> tuple[list[tuple[str, str]], Any]:
    """The plan and rule at tick 10,000, PRESS by R7 against ten enemy rifles in sight (a ratio of 5), and 250 ticks
    later, inside the hold, when four tanks stand at the base and the threat level is the one given.

    The army of 5,000 is the state's army value: with the early-attack guard off, PRESS opens on it at 400 s.
    """
    agent = plans_agent(tuning=EARLY_GUARD_OFF)
    pressing = plan_after(agent, at(400, army=5000, enemies=[RAID_TARGET, *rifles(10)]))
    plans = [(pressing, rule_of(agent))]
    agent.ctx.threat_level = threat
    plans.append((plan_after(agent, at(410, army=5000, enemies=[RAID_TARGET, *ATTACKERS])), rule_of(agent)))
    return plans, agent


def test_decision_table_under_a_major_threat_inside_the_hold_turns_press_chosen_by_r7_to_hold():
    plans, agent = pressing_then_attacked(MAJOR_THREAT)

    assert agent.world.tick == 10_250
    assert plans == [(PRESS, R7), (HOLD, R7)]


@pytest.mark.parametrize("threat", [NO_THREAT, MINOR_THREAT])
def test_decision_table_without_a_major_threat_keeps_press_chosen_by_r7_through_the_hold(threat):
    plans, _ = pressing_then_attacked(threat)

    assert plans == [(PRESS, R7), (PRESS, R7)]


def test_decision_table_under_a_major_threat_ends_the_plan_a_model_chose_and_it_does_not_stand_again():
    agent, _ = asking_agent(None, answer(RAID))
    agent.ctx.scout_report = ScoutReport(enemy_base_seen=True)

    def plan_at(seconds: float) -> tuple[str, str]:
        # A remembered power plant: the base seen is still standing, so the hold is not ended by its loss.
        return plan_after(agent, at(seconds, buildings=[foe(600, "powr")])), rule_of(agent)

    plans = [plan_at(280), plan_at(280.8)]
    agent.ctx.threat_level = MAJOR_THREAT
    plans.append(plan_at(290))
    agent.ctx.threat_level = NO_THREAT
    # HOLD is held for 750 ticks from 290 s; then the table chooses again, and the model's RAID no longer stands.
    plans += [plan_at(300), plan_at(320)]

    assert plans == [(RAID, R7), (RAID, MODEL), (HOLD, R7), (HOLD, R7), (RAID, R7)]


@pytest.mark.parametrize(
    "first, then, plan, rule",
    [
        (at(400, units=MAIN_3500, buildings=[foe(600, "powr")], enemies=[]), at(401, enemies=ATTACKERS), FINISH, R6),
        (at(1020, remaining=180, balance=100), at(1021, remaining=179, balance=100, enemies=ATTACKERS), PRESERVE, R2),
        (at(1020, remaining=180, balance=0), at(1021, remaining=179, balance=0, enemies=ATTACKERS), HOLD, R3),
    ],
    ids=["a held FINISH", "the clock's PRESERVE", "the clock's HOLD"],
)
def test_decision_table_a_major_threat_leaves_finish_and_r2_and_r3_as_they_are(first, then, plan, rule):
    agent = plans_agent()
    plan_after(agent, first)
    agent.ctx.threat_level = MAJOR_THREAT

    assert (plan_after(agent, then), rule_of(agent)) == (plan, rule)


def test_decision_table_with_the_late_press_guard_off_a_major_threat_leaves_the_clock_s_press_as_it_is():
    agent = plans_agent(tuning=LATE_GUARD_OFF_FIVE_MINUTES)
    plan_after(agent, at(900, remaining=300, balance=-500))
    agent.ctx.threat_level = MAJOR_THREAT

    then = at(901, remaining=299, balance=-500, enemies=ATTACKERS)
    assert (plan_after(agent, then), rule_of(agent)) == (PRESS, R4)


def test_decision_table_a_sparring_pin_still_plays_its_raid_under_a_major_threat():
    agent = plans_agent(settings=RedAlertSettings(seat_plans={"Multi0": RAID}))
    agent.ctx.threat_level = MAJOR_THREAT

    assert (plan_after(agent, at(299, enemies=[RAID_TARGET, *ATTACKERS])), rule_of(agent)) == (RAID, SPARRING)


# -- the late-PRESS guard: no R4 while the base is under a major threat ------------------------------

# The two-minute window with the late-PRESS guard switched off: R4 does not look at threats.
LATE_GUARD_OFF = tuning_off(plan_rules=dataclasses.replace(LATE_GUARD_ON.plan_rules, late_press_guard=False))


def behind(seconds_left: float, *, balance: int = -500, enemies: list | tuple = (RAID_TARGET,)) -> dict:
    """A state of a 20-minute match with the seconds left given, behind by 500 unless another balance is given."""
    return at(1200 - seconds_left, remaining=seconds_left, balance=balance, enemies=enemies)


def late_press_under(threat: str, tuning: Tuning = LATE_GUARD_ON) -> tuple[str, str]:
    """The plan and rule with 100 s left, a balance of -500, the base seen, RAID open and the threat level given."""
    agent = plans_agent(base_seen=True, tuning=tuning)
    agent.ctx.threat_level = threat
    return plan_after(agent, behind(100, enemies=[RAID_TARGET, *ATTACKERS])), rule_of(agent)


@pytest.mark.parametrize(
    "threat, plan, rule", [(MAJOR_THREAT, HOLD, R7), (MINOR_THREAT, PRESS, R4), (NO_THREAT, PRESS, R4)]
)
def test_late_press_does_not_open_under_a_major_base_threat_and_the_table_holds(threat, plan, rule):
    assert late_press_under(threat) == (plan, rule)


def test_late_press_with_the_guard_off_opens_under_a_major_base_threat():
    assert late_press_under(MAJOR_THREAT, LATE_GUARD_OFF) == (PRESS, R4)


def test_late_press_with_the_guard_off_and_the_five_minute_window_opens_under_a_major_base_threat_with_no_block(
    match_record,
):
    agent = plans_agent(base_seen=True, tuning=LATE_GUARD_OFF_FIVE_MINUTES)
    agent.ctx.threat_level = MAJOR_THREAT

    plans = [(plan_after(agent, behind(seconds_left)), rule_of(agent)) for seconds_left in (300, 100)]

    assert (plans, match_record("plan_rule_blocked")) == ([(PRESS, R4), (PRESS, R4)], [])


def test_late_press_with_the_tracked_file_does_not_open_under_a_major_base_threat_and_records_the_block(match_record):
    agent = plans_agent(base_seen=True, tuning=Tuning.from_file(situations.REPO_ROOT))
    agent.ctx.threat_level = MAJOR_THREAT

    plans = [(plan_after(agent, behind(seconds_left)), rule_of(agent)) for seconds_left in (300, 100)]

    assert plans == [(HOLD, R7), (HOLD, R7)]
    assert [(block["rule"], block["reason"]) for block in match_record("plan_rule_blocked")] == [(R4, "R4_base_threat")]


@pytest.mark.parametrize(
    "tuning, during", [(LATE_GUARD_ON, (HOLD, R7)), (LATE_GUARD_OFF, (PRESS, R4))], ids=["guard on", "guard off"]
)
def test_late_press_in_force_ends_on_the_first_cycle_with_a_major_base_threat_and_opens_again_after_it(tuning, during):
    agent = plans_agent(base_seen=True, tuning=tuning)

    def plan_at(seconds_left: float, threat: str) -> tuple[str, str]:
        agent.ctx.threat_level = threat
        return plan_after(agent, behind(seconds_left)), rule_of(agent)

    plans = [plan_at(110, NO_THREAT), plan_at(109.8, MAJOR_THREAT), plan_at(100, MAJOR_THREAT)]
    plans.append(plan_at(99.8, NO_THREAT))

    assert plans == [(PRESS, R4), during, during, (PRESS, R4)]


@pytest.mark.parametrize(
    "tuning, after", [(LATE_GUARD_ON, (PRESERVE, R2)), (LATE_GUARD_OFF, (PRESS, R4))], ids=["guard on", "guard off"]
)
def test_late_press_kept_by_the_hysteresis_ends_under_a_major_base_threat_and_r2_keeps_the_lead(tuning, after):
    agent = plans_agent(base_seen=True, tuning=tuning)
    plan_after(agent, behind(110))
    # A lead of 300 calls for R2, but R4 is kept until a lead of more than 500 has stood for 250 ticks.
    kept = plan_after(agent, behind(109.8, balance=300)), rule_of(agent)
    agent.ctx.threat_level = MAJOR_THREAT

    assert (kept, (plan_after(agent, behind(109.6, balance=300)), rule_of(agent))) == ((PRESS, R4), after)


@pytest.mark.parametrize(
    "tuning, late", [(LATE_GUARD_ON, (HOLD, R7)), (LATE_GUARD_OFF, (PRESS, R4))], ids=["guard on", "guard off"]
)
def test_late_press_guard_reads_the_major_threat_base_defense_declares_on_2000_of_enemy_army_at_the_base(tuning, late):
    agent = new_agent(BaseDefenseManager(), tuning=tuning)
    agent.ctx.scout_report = ScoutReport(enemy_base_seen=True)
    # Twenty rifles, 2,000 of enemy army, in a column 8 cells east of the construction yard: within the threat area.
    at_the_base = [RAID_TARGET, *rifles(20, x=30)]

    plan_after(agent, behind(130, enemies=at_the_base))
    declared = agent.ctx.threat_level

    assert (declared, (plan_after(agent, behind(100, enemies=at_the_base)), rule_of(agent))) == (MAJOR_THREAT, late)


def block_events(match_record, tuning: Tuning, steps: tuple[tuple[float, str, int], ...]) -> list[dict]:
    """The plan_rule_blocked events of a base seen that takes in a late state for each step of seconds left, threat
    level and balance, the threat level set on the context before its state."""
    agent = plans_agent(base_seen=True, tuning=tuning)
    for seconds_left, threat, balance in steps:
        agent.ctx.threat_level = threat
        plan_after(agent, behind(seconds_left, balance=balance))
    return match_record("plan_rule_blocked")


# Behind by 500 from 110 s left: R4 presses, a major threat blocks it over two states and two seconds, the threat ends
# and R4 presses again, and a second major threat blocks it again.
TWO_BLOCKS = (
    (110, NO_THREAT, -500),
    (109.8, MAJOR_THREAT, -500),
    (109.6, MAJOR_THREAT, -500),
    (107.6, MAJOR_THREAT, -500),
    (100, NO_THREAT, -500),
    (90, MAJOR_THREAT, -500),
)


def test_late_press_block_is_recorded_once_in_its_first_cycle_and_once_more_for_a_new_block(match_record):
    blocks = block_events(match_record, LATE_GUARD_ON, TWO_BLOCKS)

    assert [(block["tick"], block["plan"], block["plan_rule"]) for block in blocks] == [
        (27_255, HOLD, R7),
        (27_750, HOLD, R7),
    ]
    assert {(block["rule"], block["reason"], block["threat_level"]) for block in blocks} == {
        (R4, "R4_base_threat", MAJOR_THREAT)
    }
    assert (blocks[0]["game_seconds"], blocks[0]["remaining_seconds"], blocks[0]["trade_balance"]) == (
        1090.2,
        109.8,
        -500,
    )


def test_late_press_block_records_nothing_with_the_guard_off_or_without_a_major_threat(match_record):
    off = plans_agent(base_seen=True, tuning=LATE_GUARD_OFF)
    off.ctx.threat_level = MAJOR_THREAT
    quiet = plans_agent(base_seen=True, tuning=LATE_GUARD_ON)
    quiet.ctx.threat_level = MINOR_THREAT

    plans = [plan_after(agent, behind(100), behind(99.8)) for agent in (off, quiet)]

    assert (plans, match_record("plan_rule_blocked")) == ([PRESS, PRESS], [])


def test_late_press_block_of_an_r4_the_hysteresis_kept_records_the_preserve_r2_gives(match_record):
    steps = ((110, NO_THREAT, -500), (109.8, NO_THREAT, 300), (109.6, MAJOR_THREAT, 300))

    (block,) = block_events(match_record, LATE_GUARD_ON, steps)

    assert (block["plan"], block["plan_rule"], block["trade_balance"], block["reason"]) == (
        PRESERVE,
        R2,
        300,
        "R4_base_threat",
    )


# -- FINISH R6(a) needs heavy units ------------------------------------------------------------------

# The rifles the home guard takes before any join main.
GUARD = tuple(own(900 + number, "e1") for number in range(tuning_off().opening.guard_rifles))


def main_of(**counts: int) -> tuple:
    """The home guard's rifles, and a main of the unit types and counts given, all idle at the staging point."""
    units = [
        own(1000 + 100 * kind + number, unit_type)
        for kind, unit_type in enumerate(counts)
        for number in range(counts[unit_type])
    ]
    return (*GUARD, *units)


def overwhelming(units: tuple) -> dict:
    """An R6(a) state at 500 s: an even balance, ten enemy rifles and a pillbox (1.3 x 2,200), the army the units."""
    army = sum(situations.tracked_items()[unit["type"]].cost * unit["hp"] // 100 for unit in units)
    return at(500, army=army, units=units, enemies=rifles(10), buildings=[foe(600, "pbox")])


@pytest.mark.parametrize(
    "units, main_value, share, finishes",
    [
        (main_of(e1=160), 16_000, 0.0, False),
        (main_of(**{"3tnk": 5, "e1": 102}), 15_950, 5_750 / 15_950, True),
        (main_of(**{"3tnk": 4, "e1": 114}), 16_000, 0.2875, False),
        (main_of(**{"3tnk": 3, "v2rl": 1, "e1": 50}), 9_350, 4_350 / 9_350, True),
        (main_of(ftrk=40, e1=100), 34_000, 0.0, False),
    ],
    ids=[
        "160 e1: no FINISH",
        "5 3tnk + 102 e1, share 0.36: FINISH",
        "4 3tnk + 114 e1, share 0.2875: no FINISH",
        "tanks and a V2 count together",
        "flak trucks are not heavy",
    ],
)
def test_heavy_share_of_main_decides_whether_r6a_finishes(units, main_value, share, finishes):
    agent = plans_agent(base_seen=True)

    plan = plan_after(agent, overwhelming(units))
    seen = plans_module.observe(agent.world, agent.ctx, agent.ctx.tuning, None)

    assert (seen.main_strength, seen.main_heavy_share) == (main_value, pytest.approx(share))
    assert (plan == FINISH) is finishes and (rule_of(agent) == R6) is finishes


def test_heavy_share_with_an_empty_main_does_not_apply_and_an_army_elsewhere_finishes():
    # The army elsewhere is the state's army value of 4,000, weighed with the early-attack guard off.
    agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_OFF)

    plan = plan_after(agent, at(500, army=4000, units=GUARD, enemies=rifles(10), buildings=[foe(600, "pbox")]))
    seen = plans_module.observe(agent.world, agent.ctx, agent.ctx.tuning, None)

    assert (seen.main_strength, seen.main_heavy_share) == (0, 0.0) and (plan, rule_of(agent)) == (FINISH, R6)


def test_heavy_share_counts_each_unit_at_its_cost_times_its_share_of_health():
    agent = plans_agent(base_seen=True)
    worn = tuple({**unit, "hp": 50} if unit["type"] == "3tnk" else unit for unit in main_of(**{"3tnk": 5, "e1": 102}))

    plan = plan_after(agent, overwhelming(worn))
    seen = plans_module.observe(agent.world, agent.ctx, agent.ctx.tuning, None)

    # 5 x 1,150 at half health: 2,875 of 13,075.
    assert seen.main_heavy_share == pytest.approx(2_875 / 13_075) and plan != FINISH


def test_heavy_share_does_not_hold_back_the_clean_up_of_r6b():
    agent = plans_agent()

    plan = plan_after(agent, at(400, army=4000, units=main_of(e1=35), buildings=[foe(600, "powr")], enemies=[]))

    assert (plan, rule_of(agent)) == (FINISH, R6)


# -- the early-attack guard: the own army weighed by what fights ----------------------------------------

# The early-attack guard switched on, as the tuning file ships it.
EARLY_GUARD_ON = tuning_off(plan_rules=dataclasses.replace(plan_rules_off(), early_attack_guard=True))
# 3,000 of units that fight: two medium tanks, a light tank and two rocket soldiers.
FIGHTERS_3000 = (
    own(300, "2tnk"),
    own(301, "2tnk", 33, 48),
    own(302, "1tnk", 32, 49),
    own(303, "e3", 33, 49),
    own(304, "e3", 31, 48),
)


def harvesters(count: int) -> tuple:
    """Own harvesters at the refinery, which the state's army value counts and which do not fight."""
    return tuple(own(200 + number, "harv", 24, 36) for number in range(count))


@pytest.mark.parametrize(
    "tuning, ratio", [(EARLY_GUARD_ON, 3.0), (EARLY_GUARD_OFF, 7.0)], ids=["guard on", "guard off"]
)
def test_early_guard_ratio_counts_the_combat_units_only_and_not_the_harvesters(match_record, tuning, ratio):
    agent = plans_agent(base_seen=True, tuning=tuning)

    # The state's army value of 7,000 counts 4,000 of harvesters with the 3,000 of units that fight.
    state = at(400, army=7000, units=(*FIGHTERS_3000, *harvesters(4)), enemies=[RAID_TARGET, *rifles(10)])
    plan_after(agent, state)
    seen = agent._plan_selector.situation

    (change,) = match_record("plan_change")
    assert (seen.army_value, seen.combat_army_value, seen.enemy_combat_value) == (7000, 3000, 1000)
    assert (seen.ratio, change["inputs"]["combat_army_value"], change["inputs"]["ratio"]) == (ratio, 3000, ratio)


def test_early_guard_ratio_assumes_an_unknown_enemy_army_as_strong_as_the_own_combat_units():
    agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_ON)

    plan_after(agent, at(400, army=7000, units=(*FIGHTERS_3000, *harvesters(4))))
    seen = agent._plan_selector.situation

    assert (seen.enemy_army_known, seen.combat_army_value, seen.ratio) == (False, 3000, 1.0)


@pytest.mark.parametrize(
    "tuning, units, enemy_rifles, press_open",
    [
        (EARLY_GUARD_ON, FIGHTERS_3000, 20, True),
        (EARLY_GUARD_ON, FIGHTERS_3000, 21, False),
        (EARLY_GUARD_OFF, FIGHTERS_3000, 21, True),
        (EARLY_GUARD_ON, FIGHTERS_3000[:4], 10, False),
        (EARLY_GUARD_OFF, FIGHTERS_3000[:4], 10, True),
    ],
    ids=[
        "guard on: 3,000 against 1.5 x 2,000",
        "guard on: 3,000 against 1.5 x 2,100",
        "guard off: 7,000 against 1.5 x 2,100",
        "guard on: 2,700 is no army of 3,000",
        "guard off: 7,000 is",
    ],
)
def test_early_guard_ahead_rule_of_press_at_600_seconds_weighs_the_combat_units(
    tuning, units, enemy_rifles, press_open
):
    agent = plans_agent(base_seen=True, tuning=tuning)

    eligible = open_after(agent, at(600, army=7000, units=(*units, *harvesters(4)), enemies=rifles(enemy_rifles)))

    assert (PRESS in eligible) is press_open


@pytest.mark.parametrize(
    "tuning, tanks, army, finishes",
    [(EARLY_GUARD_ON, 3, 5250, False), (EARLY_GUARD_OFF, 3, 5250, True), (EARLY_GUARD_ON, 4, 6100, True)],
    ids=["guard on: 3,050 against 3,510", "guard off: 5,250 against 3,510", "guard on: 3,900 against 3,510"],
)
def test_early_guard_r6a_weighs_the_combat_units_so_harvesters_no_longer_make_an_army_overwhelm(
    tuning, tanks, army, finishes
):
    agent = plans_agent(base_seen=True, tuning=tuning)
    # The home guard's rifles and the medium tanks, and two harvesters; fifteen enemy rifles and a pillbox weigh 2,700.
    units = (*main_of(**{"2tnk": tanks}), *harvesters(2))

    plan = plan_after(agent, at(500, army=army, units=units, enemies=rifles(15), buildings=[foe(600, "pbox")]))
    seen = agent._plan_selector.situation

    assert (seen.combat_army_value, seen.main_heavy_share) == (500 + 850 * tanks, 1.0)
    assert (plan == FINISH) is finishes and (rule_of(agent) == R6) is finishes


# -- the early-attack guard: no STRIKE -----------------------------------------------------------------


@pytest.mark.parametrize(
    "tuning, plan, rule", [(EARLY_GUARD_OFF, STRIKE, R5), (EARLY_GUARD_ON, HOLD, R7)], ids=["guard off", "guard on"]
)
@pytest.mark.parametrize("seconds", [100, 200, 240])
def test_early_guard_on_strike_does_not_open_where_it_opens_with_the_guard_off(tuning, plan, rule, seconds):
    agent = plans_agent(base_seen=True, tuning=tuning)

    # A seen base without a defense, four enemy rifles (400) and main of 1,300: R5 applies; nothing is open to raid.
    assert (plan_after(agent, at(seconds, units=MAIN_1300, enemies=rifles(4))), rule_of(agent)) == (plan, rule)


@pytest.mark.parametrize("guard, plan, rule", [(False, STRIKE, R5), (True, FINISH, R6)], ids=["guard off", "guard on"])
def test_early_guard_on_strike_with_a_ten_minute_window_gives_way_to_the_clean_up_of_r6b(guard, plan, rule):
    rules = dataclasses.replace(plan_rules_off(), strike_until_seconds=600, early_attack_guard=guard)
    agent = plans_agent(base_seen=True, tuning=tuning_off(plan_rules=rules))

    # At 400 s an undefended power plant and no enemy unit: both R5 and R6(b) apply to main of 3,500.
    state = at(400, units=MAIN_3500, buildings=[foe(600, "powr")], enemies=[])

    assert (plan_after(agent, state), rule_of(agent)) == (plan, rule)


# -- the early-attack guard: whether the enemy has committed its army --------------------------------------

# Cycles the commitment takes in, each the threat level base defense published, the own core buildings standing and
# the value killed so far, and whether the enemy counts as committed after each.
COMMITMENT_STEPS = {
    "a major threat beaten off": (
        [(MAJOR_THREAT, 6, 0), (MINOR_THREAT, 6, 0), (NO_THREAT, 6, 0)],
        [False, False, True],
    ),
    "a core building lost": ([(MAJOR_THREAT, 6, 0), (NO_THREAT, 5, 0)], [False, False]),
    "lost and rebuilt before the end": (
        [(MAJOR_THREAT, 6, 0), (MAJOR_THREAT, 5, 0), (NO_THREAT, 6, 0)],
        [False, False, True],
    ),
    "counted in the first major cycle": (
        [(MAJOR_THREAT, 5, 0), (MAJOR_THREAT, 7, 0), (NO_THREAT, 5, 0)],
        [False, False, True],
    ),
    "still in progress": ([(MAJOR_THREAT, 6, 0), (MINOR_THREAT, 7, 0)], [False, False]),
    "a minor threat only": ([(MINOR_THREAT, 6, 0), (NO_THREAT, 6, 0)], [False, False]),
    "a second threat beaten off after a lost one": (
        [(MAJOR_THREAT, 6, 0), (NO_THREAT, 5, 0), (MAJOR_THREAT, 5, 0), (NO_THREAT, 5, 0)],
        [False, False, False, True],
    ),
    "kept after a later threat costs a building": (
        [(MAJOR_THREAT, 6, 0), (NO_THREAT, 6, 0), (MAJOR_THREAT, 6, 0), (NO_THREAT, 4, 0)],
        [False, True, True, True],
    ),
    "1,499 then 1,500 of kills": ([(NO_THREAT, 6, 1499), (MAJOR_THREAT, 6, 1500)], [False, True]),
}


@pytest.mark.parametrize("steps, committed", COMMITMENT_STEPS.values(), ids=list(COMMITMENT_STEPS))
def test_early_guard_commitment_is_a_major_threat_beaten_off_or_1500_of_kills_and_stays(steps, committed):
    commitment = plans_module.EnemyCommitment(1500)
    seen = []

    for threat, core_buildings, kills_cost in steps:
        commitment.follow(threat, core_buildings, kills_cost)
        seen.append(commitment.is_committed)

    assert seen == committed


# The Allied base without its second power plant: one core building fewer.
ALLIED_BASE_LESS_POWER = (*ALLIED_BASE[:4], ALLIED_BASE[5])


@pytest.mark.parametrize(
    "steps, committed",
    [
        ([(MAJOR_THREAT, ALLIED_BASE, 0), (NO_THREAT, ALLIED_BASE, 0)], [False, True]),
        ([(MAJOR_THREAT, ALLIED_BASE, 0), (NO_THREAT, ALLIED_BASE_LESS_POWER, 0)], [False, False]),
        ([(NO_THREAT, ALLIED_BASE, 1600)], [True]),
    ],
    ids=["a major threat beaten off", "a power plant lost to it", "1,600 of kills and no threat"],
)
def test_early_guard_commitment_the_plan_selector_follows_counts_the_core_buildings_of_the_base(
    match_record, steps, committed
):
    agent = plans_agent(base_seen=True)
    seen = []

    for step, (threat, base, kills_cost) in enumerate(steps):
        agent.ctx.threat_level = threat
        plan_after(agent, at(300 + 5 * step, base=base, balance=kills_cost))
        seen.append(agent._plan_selector.situation.enemy_committed)

    assert seen == committed
    assert match_record("plan_change")[0]["inputs"]["enemy_committed"] is committed[0]


# -- the early-attack guard: no early PRESS until the enemy has committed its army ----------------------------


def ahead(seconds: float, *, base: tuple = ALLIED_BASE, balance: int = 0, remaining: float = 900.0) -> dict:
    """A state with 3,000 of units that fight against ten enemy rifles in sight (a ratio of 3) and nothing to raid.

    Two remembered guns keep R6(a) from finishing from 480 s on (1.3 x 4,200); PRESS's ahead rule does not weigh them.
    """
    return at(
        seconds,
        remaining=remaining,
        balance=balance,
        army=3000,
        units=FIGHTERS_3000,
        enemies=rifles(10),
        buildings=list(GUNS),
        base=base,
    )


@pytest.mark.parametrize(
    "steps, plan",
    [
        ([(400, NO_THREAT, ALLIED_BASE, 0)], HOLD),
        (
            [(360, MAJOR_THREAT, ALLIED_BASE, 0), (370, NO_THREAT, ALLIED_BASE, 0), (400, NO_THREAT, ALLIED_BASE, 0)],
            PRESS,
        ),
        (
            [
                (360, MAJOR_THREAT, ALLIED_BASE, 0),
                (370, NO_THREAT, ALLIED_BASE_LESS_POWER, 0),
                (400, NO_THREAT, ALLIED_BASE_LESS_POWER, 0),
            ],
            HOLD,
        ),
        ([(360, MAJOR_THREAT, ALLIED_BASE, 0), (400, MINOR_THREAT, ALLIED_BASE, 0)], HOLD),
        ([(400, NO_THREAT, ALLIED_BASE, 1600)], PRESS),
        ([(400, NO_THREAT, ALLIED_BASE, 1400)], HOLD),
        ([(539.96, NO_THREAT, ALLIED_BASE, 0)], HOLD),
        ([(540, NO_THREAT, ALLIED_BASE, 0)], PRESS),
        ([(600, NO_THREAT, ALLIED_BASE, 0)], PRESS),
    ],
    ids=[
        "400 s, the enemy not committed",
        "400 s, a major threat seen and ended",
        "400 s, a major threat that cost a power plant",
        "400 s, a major threat not yet over",
        "400 s, kills of 1,600 and no threat",
        "400 s, kills of 1,400",
        "539.96 s, the enemy not committed",
        "540 s, the gate no longer applies",
        "600 s, the gate no longer applies",
    ],
)
def test_early_guard_press_gate_opens_press_early_only_once_the_enemy_has_committed(steps, plan):
    agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_ON)

    for seconds, threat, base, balance in steps:
        agent.ctx.threat_level = threat
        plan_after(agent, ahead(seconds, base=base, balance=balance))

    assert (agent.current_plan, rule_of(agent)) == (plan, R7)


def test_early_guard_off_press_opens_at_400_seconds_whether_or_not_the_enemy_has_committed():
    agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_OFF)

    assert (plan_after(agent, ahead(400)), rule_of(agent)) == (PRESS, R7)


@pytest.mark.parametrize(
    "seconds, committed, in_force, press_open",
    [
        (400, False, False, False),
        (400, True, False, True),
        (400, False, True, True),
        (539.96, False, False, False),
        (540, False, False, True),
    ],
    ids=["not committed", "committed", "PRESS in force", "just before 540 s", "at 540 s"],
)
def test_early_guard_press_gate_on_hand_built_situations(seconds, committed, in_force, press_open):
    rules = EARLY_GUARD_ON.plan_rules
    seen = dataclasses.replace(
        blind_situation(seconds, known=True, enemy_value=1000, rules=rules),
        combat_army_value=BLIND_ARMY,
        enemy_committed=committed,
    )

    assert (PRESS in plans_module.open_plans(seen, rules, in_force)) is press_open


# The early-attack guard with R4's window at 300 s, so that a PRESS by R4 is in force before the ninth minute.
EARLY_GUARD_WIDE_LATE_WINDOW = tuning_off(
    plan_rules=dataclasses.replace(plan_rules_off(), early_attack_guard=True, press_late_seconds=300)
)


def test_early_guard_press_in_force_at_401_seconds_is_not_ended_by_the_gate():
    agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_WIDE_LATE_WINDOW)
    fresh = plans_agent(base_seen=True, tuning=EARLY_GUARD_WIDE_LATE_WINDOW)
    # A match of 690 s: R4 presses at 390 s, behind by 500, and a lead of 600 for 250 ticks ends it at 401 s.
    states = [
        ahead(390, remaining=300, balance=-500),
        ahead(391, remaining=299, balance=600),
        ahead(401, remaining=289, balance=600),
    ]

    plans = [(plan_after(agent, state), rule_of(agent)) for state in states]

    assert plans == [(PRESS, R4), (PRESS, R4), (PRESS, R7)]
    # Without a PRESS in force the same state does not open it.
    assert (plan_after(fresh, states[-1]), rule_of(fresh)) == (HOLD, R7)


@pytest.mark.parametrize(
    "tuning, plan, rule",
    [(EARLY_GUARD_ON, HOLD, R7), (EARLY_GUARD_OFF, PRESS, SPARRING)],
    ids=["guard on", "guard off"],
)
def test_early_guard_on_a_seat_pinned_on_press_does_not_take_it_before_the_enemy_has_committed(tuning, plan, rule):
    agent = plans_agent(base_seen=True, settings=RedAlertSettings(seat_plans={"Multi0": PRESS}), tuning=tuning)

    assert (plan_after(agent, ahead(400)), rule_of(agent)) == (plan, rule)


# -- the early-attack guard: each block recorded once ---------------------------------------------------------


def early_blocks(match_record, tuning: Tuning, states: list[dict]) -> list[tuple]:
    """The plan_rule_blocked events of a base seen that takes in the states, in order, as (tick, rule, reason, plan,
    plan_rule, threat_level)."""
    agent = plans_agent(base_seen=True, tuning=tuning)
    for state in states:
        plan_after(agent, state)
    return [
        (block["tick"], block["rule"], block["reason"], block["plan"], block["plan_rule"], block["threat_level"])
        for block in match_record("plan_rule_blocked")
    ]


def strike_states() -> list[dict]:
    """HOLD from 150 s with main too weak to strike; from 190 s R5 applies, at 200 s main is too weak again, and at
    205 s R5 applies again."""
    weak, enemies = MAIN_1300[:2], rifles(4)
    strong = [at(seconds, units=MAIN_1300, enemies=enemies) for seconds in (190, 190.2, 195)]
    return [
        at(150, units=weak, enemies=enemies),
        *strong,
        at(200, units=weak, enemies=enemies),
        at(205, units=MAIN_1300, enemies=enemies),
    ]


def test_early_guard_block_of_strike_is_recorded_once_in_its_first_cycle_and_once_more_for_a_new_block(match_record):
    blocks = early_blocks(match_record, EARLY_GUARD_ON, strike_states())

    assert blocks == [
        (4750, R5, "early_attack_guard", HOLD, R7, NO_THREAT),
        (5125, R5, "early_attack_guard", HOLD, R7, NO_THREAT),
    ]


def press_states() -> list[dict]:
    """PRESS's ahead rule holds at 400 s and 401 s, not at 410 s with 2,700 that fight, again at 420 s, and at 430 s
    kills of 1,600 commit the enemy."""
    states = [ahead(400), ahead(401)]
    states.append(at(410, army=2700, units=FIGHTERS_3000[:4], enemies=rifles(10), buildings=list(GUNS)))
    return [*states, ahead(420), ahead(430, balance=1600)]


def test_early_guard_block_of_an_early_press_is_recorded_once_per_block_and_not_once_the_enemy_has_committed(
    match_record,
):
    agent = plans_agent(base_seen=True, tuning=EARLY_GUARD_ON)

    plans = [plan_after(agent, state) for state in press_states()]

    blocks = [(block["tick"], block["rule"], block["reason"]) for block in match_record("plan_rule_blocked")]
    assert blocks == [(10_000, R7, "early_attack_guard"), (10_500, R7, "early_attack_guard")]
    assert plans == [HOLD, HOLD, HOLD, HOLD, PRESS]
    first, _ = match_record("plan_rule_blocked")
    assert (first["plan"], first["plan_rule"], first["game_seconds"], first["trade_balance"]) == (HOLD, R7, 400.0, 0)


@pytest.mark.parametrize("states", [strike_states(), press_states()], ids=["STRIKE", "early PRESS"])
def test_early_guard_off_records_no_block(match_record, states):
    assert early_blocks(match_record, EARLY_GUARD_OFF, states) == []


# -- RAID only while a raid could set out (launchable) -----------------------------------------------

LAUNCHABLE = tuning_off(raids=raids_off(launchable_only=True))
# Light tanks of main at the staging point (32, 48), 10 cells from the construction yard toward the enemy's start.
LIGHT_TANKS_AT_STAGING = tuple(own(300 + number, "1tnk", 32, 46 + number) for number in range(4))
# The third and fourth 30 cells forward of it: not on hand.
TWO_ON_HAND = (*LIGHT_TANKS_AT_STAGING[:2], own(302, "1tnk", 62, 46), own(303, "1tnk", 62, 47))
NO_UNITS = "no units for a raid on hand: need 4 1tnk, have 2 1tnk"


def raid_after(state: dict, tuning: Tuning = LAUNCHABLE) -> tuple:
    """The plan an agent of the plan rules alone is in once it has taken in a state, the situation it decided on,
    whether RAID is open, and what keeps RAID shut."""
    agent = plans_agent(tuning=tuning)
    plan = plan_after(agent, state)
    situation = agent._plan_selector.situation
    reasons = plans_module.closed_reasons(situation, agent.world, agent.ctx, agent.ctx.tuning, False)
    eligible = plans_module.open_plans(situation, agent.ctx.tuning.plan_rules)
    return plan, situation.raid_launch_blocker, RAID in eligible, reasons[RAID]


@pytest.mark.parametrize(
    "units, enemies, blocker",
    [
        (LIGHT_TANKS_AT_STAGING, (), "no raid target known"),
        (TWO_ON_HAND, (RAID_TARGET,), NO_UNITS),
        (LIGHT_TANKS_AT_STAGING, (RAID_TARGET,), None),
    ],
    ids=["no target", "2 of 4 on hand", "4 on hand and a target"],
)
def test_launchable_raid_is_open_only_with_a_target_and_the_units_on_hand_and_its_closed_reason_says_which_fails(
    units, enemies, blocker
):
    plan, found, is_open, reasons = raid_after(at(360, units=units, enemies=enemies))

    assert (found, is_open) == (blocker, blocker is None)
    assert reasons == ([] if blocker is None else [plans_module.RAID_NOT_LAUNCHABLE])
    assert plan == (RAID if blocker is None else HOLD)


def test_launchable_with_every_way_to_the_target_covered_raid_is_closed_for_no_safe_route():
    pillboxes = [foe(600, "pbox", 42, 40), foe(601, "pbox", 42, 56)]
    state = at(360, units=LIGHT_TANKS_AT_STAGING, enemies=[foe(450, "harv", 60, 48)], buildings=pillboxes)

    assert raid_after(state)[:3] == (HOLD, "no safe route to a raid target", False)


@pytest.mark.parametrize("units", [TWO_ON_HAND, LIGHT_TANKS_AT_STAGING], ids=["2 of 4 on hand", "4 on hand"])
def test_launchable_with_the_switch_off_raid_opens_on_a_target_alone_and_its_reasons_are_today_s(units):
    plan, found, is_open, reasons = raid_after(at(360, units=units), tuning_off())
    shut = raid_after(at(360, units=units, enemies=()), tuning_off())

    assert (plan, found, is_open, reasons) == (RAID, None, True, [])
    assert shut == (HOLD, None, False, [plans_module.NO_RAID_TARGET])


def test_launchable_a_raid_that_could_not_set_out_is_named_in_the_plan_change_record(match_record):
    plan_after(plans_agent(tuning=LAUNCHABLE), at(360, units=TWO_ON_HAND))

    (change,) = match_record("plan_change")
    assert (change["new"], change["inputs"]["raid_launch_blocker"]) == (HOLD, NO_UNITS)


def test_launchable_a_seat_pinned_to_raid_plays_hold_while_no_raid_could_set_out():
    settings = RedAlertSettings(seat_plans={"Multi0": RAID})
    pinned = [plans_agent(tuning=LAUNCHABLE, settings=settings) for _ in range(2)]

    plans = [
        plan_after(agent, at(360, units=units)) for agent, units in zip(pinned, (TWO_ON_HAND, LIGHT_TANKS_AT_STAGING))
    ]

    assert plans == [HOLD, RAID] and pinned[1].ctx.plan_rule == SPARRING


# -- the hold of a plan the model chose (model_plan_hold) --------------------------------------------

# 400 s with an army of 5,000: against ten rifles in sight (a ratio of 5) RAID and PRESS are open and the script
# presses; against thirty (1.67) both are open and it raids; against sixty (0.83) PRESS is shut and it holds.
HOLD_FROM = 10_000
# The cycles of a model's PRESS set at 10,020: the first four inside its hold of 750 ticks, the last just after it.
HELD_TICKS = (10_025, 10_100, 10_500, 10_765, 10_770)
# A source answers a question this many ticks after it was put.
ANSWER_DELAY_TICKS = 20
# A pillbox whose cover takes in RAID's target, the harvester at (60, 30): no raid target is known while it stands.
TARGET_COVER = foe(600, "pbox", 63, 36)
# Four light tanks of main 8 to 11 cells south of the base's center: on hand for a raid, but not at the staging point.
BY_THE_BASE = tuple(own(300 + number, "1tnk", 22, 56 + number) for number in range(4))


def held_state(tick: int, rifle_count: int = 10, **more: Any) -> dict:
    """A state at a tick, with the army worth 5,000, RAID's target and `rifle_count` enemy rifles in sight."""
    return at(tick / TICKS_PER_SECOND, army=5000, enemies=[RAID_TARGET, *rifles(rifle_count)], **more)


def model_hold_tuning(is_on: bool, raids: RaidTuning | None = None, **rules: Any) -> Tuning:
    """The thresholds with the early-attack guard off, so that PRESS opens at 400 s, and the model's hold on or off."""
    plan_rules = dataclasses.replace(EARLY_GUARD_OFF.plan_rules, model_plan_hold=is_on, **rules)
    return tuning_off(plan_rules=plan_rules, raids=raids or raids_off())


def chosen_by_the_model(plan: str, tuning: Tuning, rifle_count: int = 10, *managers: Any, **more: Any):
    """An agent whose model answered `plan` at 10,020 to the question it put at 10,000, and its stub chooser."""
    chooser = StubChooser(None, answer(plan))
    agent = new_agent(*managers, settings=MODEL_ON, chooser=chooser, tuning=tuning)
    for tick in (HOLD_FROM, HOLD_FROM + ANSWER_DELAY_TICKS):
        ask(agent, held_state(tick, rifle_count, **more))
    return agent, chooser


class AnsweringSource(StubChooser):
    """A decision source that answers every question put to it ANSWER_DELAY_TICKS later, naming its plans in turn."""

    def __init__(self, *plans: str) -> None:
        super().__init__()
        self._plans = itertools.cycle(plans)
        self._due: list[tuple[int, str]] = []

    def submit(self, request) -> bool:
        self._due.append((int(request.state_tag) + ANSWER_DELAY_TICKS, next(self._plans)))
        return super().submit(request)

    def poll(self, current_state_tag: str) -> ChoiceResult | None:
        is_due = bool(self._due) and int(current_state_tag) >= self._due[0][0]
        return answer(self._due.pop(0)[1]) if is_due else None


def model_change_ticks(match_record) -> list[int]:
    """The ticks of the plan changes a model's answer made."""
    return [change["tick"] for change in match_record("plan_change") if change["rule"] == MODEL]


def gaps_between(ticks: list[int]) -> list[int]:
    return [after - before for before, after in zip(ticks, ticks[1:])]


def hold_ends(match_record) -> list[tuple]:
    """The rule, the reason, the cause, the plan and the rule in its place, and the tick of each plan_rule_blocked
    event."""
    keys = ("rule", "reason", "cause", "plan", "plan_rule", "tick")
    return [tuple(event.get(key) for key in keys) for event in match_record("plan_rule_blocked")]


@pytest.mark.parametrize("is_on, least, most", [(False, 250, 250), (True, 750, 800)], ids=["switch off", "switch on"])
def test_model_plan_hold_answers_every_250_ticks_turn_the_plan_every_250_ticks_off_and_no_closer_than_750_on(
    match_record, is_on, least, most
):
    source = AnsweringSource(RAID, PRESS)
    agent = new_agent(settings=MODEL_ON, chooser=source, tuning=model_hold_tuning(is_on, choice_every_ticks=250))

    for tick in range(HOLD_FROM, HOLD_FROM + 1_605, 5):
        ask(agent, held_state(tick))

    changes = model_change_ticks(match_record)
    gaps = gaps_between(changes)
    assert len(changes) >= 3 and least <= min(gaps) <= max(gaps) <= most
    assert len(source.submitted) == len(changes)


@pytest.mark.parametrize("is_on, kept", [(True, 4), (False, 1)], ids=["switch on", "switch off"])
def test_model_plan_hold_a_press_the_model_chose_is_kept_750_ticks_while_the_script_would_hold(is_on, kept):
    # The script's own hold is 10 ticks, so that only the model's hold keeps the PRESS.
    agent, _ = chosen_by_the_model(PRESS, model_hold_tuning(is_on, plan_hold_ticks=10), 30)

    plans = [(plan_after(agent, held_state(tick, 60)), rule_of(agent)) for tick in HELD_TICKS]

    assert plans == [(PRESS, MODEL)] * kept + [(HOLD, R7)] * (len(HELD_TICKS) - kept)


def test_model_plan_hold_no_later_answer_replaces_the_held_press_and_no_question_is_put_meanwhile(match_record):
    agent, chooser = chosen_by_the_model(PRESS, model_hold_tuning(True), 30)

    for tick in HELD_TICKS[:-1]:
        agent._plan_selector.request(HOLD)
        plan_after(agent, held_state(tick, 30))

    assert (agent.current_plan, rule_of(agent), len(chooser.submitted)) == (PRESS, MODEL, 1)
    assert hold_ends(match_record) == []


def test_model_plan_hold_a_major_threat_during_the_hold_ends_it_in_that_cycle_with_a_model_hold_ended_event(
    match_record,
):
    agent, _ = chosen_by_the_model(PRESS, model_hold_tuning(True), 30)
    plan_after(agent, held_state(10_100, 30))
    agent.ctx.threat_level = MAJOR_THREAT

    ended = (plan_after(agent, held_state(10_105, 30)), rule_of(agent))

    assert ended == (HOLD, R7)
    assert hold_ends(match_record) == [(MODEL, "model_hold_ended", "major_threat", HOLD, R7, 10_105)]


def test_model_plan_hold_preserve_by_r2_during_the_hold_takes_over(match_record):
    agent, _ = chosen_by_the_model(PRESS, model_hold_tuning(True), 30)

    preserved = (plan_after(agent, held_state(10_100, 30, remaining=180, balance=100)), rule_of(agent))

    assert preserved == (PRESERVE, R2)
    assert hold_ends(match_record) == [(MODEL, "model_hold_ended", R2, PRESERVE, R2, 10_100)]


@pytest.mark.parametrize(
    "is_on, plan, rule, ends",
    [
        (True, PRESS, R7, [(MODEL, "model_hold_ended", "no raid target known", PRESS, R7, 10_100)]),
        (False, RAID, MODEL, []),
    ],
    ids=["switch on", "switch off"],
)
def test_model_plan_hold_a_held_raid_whose_target_is_covered_ends_for_no_raid_target_known(
    match_record, is_on, plan, rule, ends
):
    agent, _ = chosen_by_the_model(RAID, model_hold_tuning(is_on))

    covered = (plan_after(agent, held_state(10_100, buildings=[TARGET_COVER])), rule_of(agent))

    assert covered == (plan, rule) and hold_ends(match_record) == ends


def test_model_plan_hold_a_held_raid_that_did_not_set_out_in_time_ends_for_raid_did_not_set_out(match_record):
    # The raid waits 100 ticks for its group, which stays by the base: at 10,125 the raid manager shuts RAID.
    raids = raids_off(launchable_only=True, launch_within_ticks=100)
    agent, _ = chosen_by_the_model(RAID, model_hold_tuning(True, raids), 10, RaidManager(), units=BY_THE_BASE)

    plans = [(plan_after(agent, held_state(tick, units=BY_THE_BASE)), rule_of(agent)) for tick in (10_125, 10_130)]

    assert plans == [(RAID, MODEL), (PRESS, R7)]
    assert hold_ends(match_record) == [(MODEL, "model_hold_ended", "raid did not set out", PRESS, R7, 10_130)]


@pytest.mark.parametrize("is_on, plan, rule", [(True, RAID, MODEL), (False, PRESS, R7)], ids=["switch on", "off"])
def test_model_plan_hold_a_held_raid_short_of_units_on_hand_is_kept(is_on, plan, rule):
    tuning = model_hold_tuning(is_on, raids_off(launchable_only=True), plan_hold_ticks=10)
    agent, _ = chosen_by_the_model(RAID, tuning, units=LIGHT_TANKS_AT_STAGING)

    short = (plan_after(agent, held_state(10_100, units=TWO_ON_HAND)), rule_of(agent))

    assert agent._plan_selector.situation.raid_launch_blocker == NO_UNITS and short == (plan, rule)


@pytest.mark.parametrize("is_on", [True, False], ids=["switch on", "switch off"])
def test_model_plan_hold_with_launchable_only_a_raid_the_model_chose_whose_units_are_borrowed_stays_raid(is_on):
    tuning = model_hold_tuning(is_on, raids_off(launchable_only=True), plan_hold_ticks=10)
    agent, _ = chosen_by_the_model(RAID, tuning, 10, RaidManager(), units=BY_THE_BASE)
    borrowed = sorted(agent.ctx.groups.members(RAID_GROUP))

    seen = []
    for tick in range(HOLD_FROM + 25, HOLD_FROM + 75, 5):
        ask(agent, held_state(tick, units=BY_THE_BASE))
        seen.append((agent.current_plan, agent._plan_selector.situation.raid_launch_blocker))

    assert borrowed == [300, 301, 302, 303] and seen == [(RAID, None)] * 10


# With the switch off, as in the watched record: each answer, 20 ticks after its question, turns the plan to RAID, and
# the major threat turns it back to HOLD in the next cycle.
FLIPS_UNDER_THREAT = [(HOLD_FROM, HOLD, R7)] + [
    change for tick in (10_020, 10_145, 10_270, 10_395) for change in ((tick, RAID, MODEL), (tick + 5, HOLD, R7))
]


@pytest.mark.parametrize(
    "is_on, questions, changes",
    [(True, 0, [(HOLD_FROM, HOLD, R7)]), (False, 4, FLIPS_UNDER_THREAT)],
    ids=["switch on", "switch off"],
)
def test_model_plan_hold_under_a_major_threat_lasting_500_ticks_no_question_is_put_and_no_model_change_happens(
    match_record, is_on, questions, changes
):
    source = AnsweringSource(RAID)
    agent = new_agent(settings=MODEL_ON, chooser=source, tuning=model_hold_tuning(is_on))
    agent.ctx.threat_level = MAJOR_THREAT

    for tick in range(HOLD_FROM, HOLD_FROM + 500, 5):
        ask(agent, held_state(tick))

    made = [(change["tick"], change["new"], change["rule"]) for change in match_record("plan_change")]
    assert (len(source.submitted), made) == (questions, changes)


def test_model_plan_hold_an_answer_arriving_under_a_major_threat_is_discarded_with_its_option_and_the_cause(
    match_record,
):
    source = AnsweringSource(RAID)
    agent = new_agent(settings=MODEL_ON, chooser=source, tuning=model_hold_tuning(True))
    pressing = plan_after(agent, held_state(HOLD_FROM))
    agent.ctx.threat_level = MAJOR_THREAT
    plans = [(plan_after(agent, held_state(tick)), rule_of(agent)) for tick in (10_010, 10_020)]
    agent.ctx.threat_level = NO_THREAT
    plan_after(agent, held_state(10_125))

    (discarded,) = match_record("plan_rule_blocked")
    assert (pressing, plans, len(source.submitted)) == (PRESS, [(HOLD, R7)] * 2, 2)
    assert (discarded["rule"], discarded["reason"], discarded["cause"], discarded["option"]) == (
        MODEL,
        "model_answer_discarded",
        "major_threat",
        RAID,
    )
    assert (discarded["plan"], discarded["plan_rule"], discarded["tick"]) == (HOLD, R7, 10_020)


# -- FINISH before R2 and R3 in the last three minutes ----------------------------------------------

# The enemy of a match nearly won: a pillbox (600) and a turret (800), and four buildings besides.
NEARLY_WON_BUILDINGS = [
    foe(600, "pbox", 70, 44),
    foe(601, "gun", 70, 52),
    *(foe(602 + number, kind, 78, 42 + 4 * number) for number, kind in enumerate(("fact", "powr", "proc", "tent"))),
]
# Main worth 20,030, 17,250 of it in tanks; and main worth 3,000, too small for R6(a) against that enemy.
WINNING_MAIN = (*main_of(**{"3tnk": 15, "e1": 27}), own(1999, "e1", hp=80))
SMALL_MAIN = main_of(**{"3tnk": 2, "e1": 7})
# The switch on, as it ships, and off.
FINISH_FIRST = tuning_off(plan_rules=plan_rules_off(finish_over_preserve=True))
CLOCK_FIRST = tuning_off(plan_rules=plan_rules_off(finish_over_preserve=False))


def nearly_won(tick: int, units: tuple = WINNING_MAIN, balance: int = 45_200, enemies: list | None = None) -> dict:
    """A state at a tick of a 20-minute match against an enemy army of 2,000 in sight (twenty rifles) and the
    NEARLY_WON_BUILDINGS, with main the units given and a balance of +45,200 unless given."""
    seconds = tick / TICKS_PER_SECOND
    army = sum(situations.tracked_items()[unit["type"]].cost * unit["hp"] // 100 for unit in units)
    in_sight = rifles(20) if enemies is None else enemies
    return at(
        seconds,
        remaining=1200 - seconds,
        balance=balance,
        army=army,
        units=units,
        enemies=in_sight,
        buildings=NEARLY_WON_BUILDINGS,
    )


def test_finish_over_preserve_with_170_seconds_left_and_main_20030_against_2000_r6_finishes_before_r2():
    agent = plans_agent(base_seen=True, tuning=FINISH_FIRST)

    plan = plan_after(agent, nearly_won(25_750))
    seen = plans_module.observe(agent.world, agent.ctx, agent.ctx.tuning, None)

    assert (seen.remaining_seconds, seen.trade_balance, seen.main_strength) == (170, 45_200, 20_030)
    assert (seen.enemy_buildings, seen.enemy_defenses) == (6, 2)
    assert (plan, rule_of(agent)) == (FINISH, R6)


def test_finish_over_preserve_a_finish_in_force_goes_on_into_the_last_three_minutes():
    agent = plans_agent(base_seen=True, tuning=FINISH_FIRST)

    # 210, 180 and 170 seconds left.
    plans = [(plan_after(agent, nearly_won(tick)), rule_of(agent)) for tick in (24_750, 25_500, 25_750)]

    assert plans == [(FINISH, R6)] * 3


def test_finish_over_preserve_with_main_3000_against_an_enemy_army_of_6000_the_plan_is_preserve_by_r2():
    agent = plans_agent(base_seen=True, tuning=FINISH_FIRST)
    army_6000 = [*rifles(20), *rifles(20, first_id=700, x=76), *rifles(20, first_id=800, x=78)]

    plan = plan_after(agent, nearly_won(25_750, SMALL_MAIN, enemies=army_6000))
    seen = plans_module.observe(agent.world, agent.ctx, agent.ctx.tuning, None)

    assert (seen.main_strength, seen.enemy_units) == (3_000, 60)
    assert (plan, rule_of(agent)) == (PRESERVE, R2)


def test_finish_over_preserve_a_preserve_in_force_gives_way_to_finish_in_the_first_cycle_r6_applies():
    agent = plans_agent(base_seen=True, tuning=FINISH_FIRST)

    plans = [
        (plan_after(agent, state), rule_of(agent))
        for state in (nearly_won(25_750, SMALL_MAIN), nearly_won(25_755, SMALL_MAIN), nearly_won(25_760))
    ]

    assert plans == [(PRESERVE, R2), (PRESERVE, R2), (FINISH, R6)]


def test_finish_over_preserve_a_late_finish_stays_250_ticks_after_r6_stops_applying_then_turns_to_preserve():
    agent = plans_agent(base_seen=True, tuning=FINISH_FIRST)
    plan_after(agent, nearly_won(25_750))

    plans = [(plan_after(agent, nearly_won(tick, SMALL_MAIN)), rule_of(agent)) for tick in (25_755, 25_999, 26_000)]

    assert plans == [(FINISH, R6), (FINISH, R6), (PRESERVE, R2)]


def test_finish_over_preserve_a_finish_left_late_is_not_taken_up_again_within_750_ticks():
    agent = plans_agent(base_seen=True, tuning=FINISH_FIRST)
    plan_after(agent, nearly_won(25_750))
    left = plan_after(agent, nearly_won(26_000, SMALL_MAIN))

    again = [plan_after(agent, nearly_won(tick)) for tick in (26_005, 26_749, 26_750)]

    assert (left, again) == (PRESERVE, [PRESERVE, PRESERVE, FINISH])


def test_finish_over_preserve_r6_comes_before_r3s_even_balance_too():
    agent = plans_agent(base_seen=True, tuning=FINISH_FIRST)

    assert (plan_after(agent, nearly_won(25_750, balance=0)), rule_of(agent)) == (FINISH, R6)


@pytest.mark.parametrize("balance, plan, rule", [(-600, PRESS, R4), (600, FINISH, R6)], ids=["R4", "R2"])
def test_finish_over_preserve_r4_keeps_its_place_before_a_cleanup_finish(balance, plan, rule):
    agent = plans_agent(base_seen=True, tuning=FINISH_FIRST)
    # 100 s left, main of 3,500 and an enemy refinery with nothing known that defends it: R6(b) applies.
    cleanup = at(1100, remaining=100, balance=balance, units=MAIN_3500, enemies=[], buildings=[foe(600, "proc")])

    assert (plan_after(agent, cleanup), rule_of(agent)) == (plan, rule)


def test_finish_over_preserve_off_r2_turns_a_winning_finish_to_preserve_as_before():
    agent = plans_agent(base_seen=True, tuning=CLOCK_FIRST)

    plans = [(plan_after(agent, nearly_won(tick)), rule_of(agent)) for tick in (24_750, 25_750)]

    assert plans == [(FINISH, R6), (PRESERVE, R2)]


def test_finish_over_preserve_ships_on_built_in_and_in_the_tracked_file():
    assert PlanRulesTuning().finish_over_preserve
    assert Tuning.from_file(situations.REPO_ROOT).plan_rules.finish_over_preserve


def test_finish_over_preserve_false_in_the_file_switches_it_off(tmp_path):
    write_tuning_with(tmp_path, "finish_over_preserve", "false")

    assert not Tuning.from_file(tmp_path).plan_rules.finish_over_preserve


# -- R2E: the endgame lead kept behind defenses -----------------------------------------------------------------------

# The endgame lead on, with the enemy army estimate it reads.
ENDGAME_LEAD = tuning_off(
    plan_rules=plan_rules_off(enemy_army_estimate=True),
    clock=dataclasses.replace(tuning_off().clock, endgame_lead_defenses=True),
)
# Main of seven heavy tanks, 8,050; and of eighteen, 20,700, which R6(a) finishes with against the enemy below.
LEAD_MAIN = tuple(own(1200 + number, "3tnk", 31 + number % 4, 46 + number // 4) for number in range(7))
WINNING_LEAD_MAIN = tuple(own(1200 + number, "3tnk", 31 + number % 4, 46 + number // 4) for number in range(18))
# An enemy army of 7,000 in sight: six heavy tanks and a rifleman; and one of 3,000: two heavy tanks and seven rifles.
ENEMY_7000 = [*(foe(600 + number, "3tnk", 74, 40 + number) for number in range(6)), foe(620, "e1", 75, 40)]
ENEMY_3000 = [*(foe(600 + number, "3tnk", 74, 40 + number) for number in range(2)), *rifles(7)]


def stalemate(
    remaining: float = 280, balance: int = 2500, units: tuple = LEAD_MAIN, enemies: list = ENEMY_7000, tick_on: int = 0
) -> dict:
    """A late state of a 20-minute match `tick_on` ticks after the one with `remaining` seconds left."""
    seconds = 1200 - remaining + tick_on / TICKS_PER_SECOND
    army = sum(situations.tracked_items()[unit["type"]].cost for unit in units)
    return at(seconds, remaining=1200 - seconds, balance=balance, army=army, units=units, enemies=enemies)


def test_endgame_lead_preserve_in_stalemate_ahead(match_record):
    agent = plans_agent(base_seen=True, tuning=ENDGAME_LEAD)

    plan = plan_after(agent, stalemate())

    seen = agent.ctx.enemy_army_estimate
    assert (plan, rule_of(agent)) == (PRESERVE, "R2E") and seen == 7000
    assert match_record("plan_change")[-1]["rule"] == "R2E"


@pytest.mark.parametrize("balance", [-500, 600], ids=["behind", "level"])
def test_endgame_lead_not_when_behind_or_level(balance):
    agent = plans_agent(base_seen=True, tuning=ENDGAME_LEAD)

    plan_after(agent, stalemate(balance=balance))

    assert rule_of(agent) != "R2E"


def test_endgame_lead_not_without_heavy_armies():
    agent = plans_agent(base_seen=True, tuning=ENDGAME_LEAD)

    plan_after(agent, stalemate(enemies=ENEMY_3000))

    assert agent.ctx.enemy_army_estimate == 3000 and rule_of(agent) != "R2E"


def test_endgame_lead_not_before_window():
    agent = plans_agent(base_seen=True, tuning=ENDGAME_LEAD)

    plan_after(agent, stalemate(remaining=320))

    assert rule_of(agent) != "R2E"


def test_finish_beats_endgame_lead():
    agent = plans_agent(base_seen=True, tuning=ENDGAME_LEAD)

    plan = plan_after(agent, stalemate(units=WINNING_LEAD_MAIN))

    assert (plan, rule_of(agent)) == (FINISH, R6)


def test_endgame_lead_ends_when_balance_falls():
    agent = plans_agent(base_seen=True, tuning=ENDGAME_LEAD)
    plan_after(agent, stalemate())

    # From 5 ticks on the balance stands at -100: 249 ticks of it keep R2E, the 250th ends it.
    kept = [plan_after(agent, stalemate(balance=-100, tick_on=tick)) for tick in (5, 6, 254)]
    kept_rules = rule_of(agent)
    plan_after(agent, stalemate(balance=-100, tick_on=255))

    assert kept == [PRESERVE] * 3 and kept_rules == "R2E" and rule_of(agent) != "R2E"


def test_endgame_lead_stays_once_the_armies_shrink():
    agent = plans_agent(base_seen=True, tuning=ENDGAME_LEAD)
    plan_after(agent, stalemate())

    plan = plan_after(agent, stalemate(units=LEAD_MAIN[:2], tick_on=5))

    assert (plan, rule_of(agent)) == (PRESERVE, "R2E")


def test_endgame_lead_switch_off_the_clock_as_before():
    agent = plans_agent(base_seen=True, tuning=tuning_off(plan_rules=plan_rules_off(enemy_army_estimate=True)))

    plan_after(agent, stalemate())

    assert rule_of(agent) != "R2E"
