"""The shared cap on the main base's ground defenses (`[base_defense] defense_cap`), and the hunters wanted against enemy
artillery (`[composition] anti_artillery_quota`)."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import situations
from situations import ask, base_state
from ucla_agent.games.redalert import base_defense, defense_cap
from ucla_agent.games.redalert.anti_air import AirWatch
from ucla_agent.games.redalert.approach_alarm import ApproachAlarm
from ucla_agent.games.redalert.composition import ArmyMix, counter_quota
from ucla_agent.games.redalert.items import ALLIES, SOVIET, TICKS_PER_SECOND
from ucla_agent.games.redalert.tuning import BaseDefenseTuning, CompositionTuning, Tuning

ALLIED_BASE = (("fact", 22, 48), ("powr", 19, 52), ("tent", 18, 44), ("proc", 26, 52), ("weap", 26, 44))
SHIPPED = Tuning()
OFF = dataclasses.replace(SHIPPED, base_defense=dataclasses.replace(SHIPPED.base_defense, defense_cap=False))
PILLBOX_CELLS = ((30, 40), (30, 56), (14, 40), (14, 56))


def pillboxes(count: int) -> tuple[tuple[str, int, int], ...]:
    return tuple(("pbox", x, y) for x, y in PILLBOX_CELLS[:count])


def tanks(count: int) -> list[dict]:
    return [situations.unit(300 + n, "2tnk", 24 + n % 3, 46 + n // 3) for n in range(count)]


def agent_with(defenses: int, army: list[dict], seconds: int = 300, tuning: Tuning = SHIPPED, **seen):
    agent = situations.new_agent(tuning=tuning)
    state = base_state(
        "england", *ALLIED_BASE, *pillboxes(defenses), tick=seconds * TICKS_PER_SECOND, units=army, **seen
    )
    ask(agent, state)
    return agent


def capped_events(match_record) -> list[dict]:
    return [event for event in match_record("base_defense") if event.get("reason") == defense_cap.DEFENSE_CAPPED]


def test_four_ground_defenses_before_15_minutes_fill_the_cap(match_record):
    three, four = agent_with(3, tanks(6)), agent_with(4, tanks(6))

    assert defense_cap.allows("pbox", "test", three.world, three.ctx) is True
    assert defense_cap.allows("pbox", "test", four.world, four.ctx) is False
    assert defense_cap.allows("agun", "test", four.world, four.ctx) is True
    (event,) = capped_events(match_record)
    assert (event["rule"], event["cause"], event["item"]) == ("test", defense_cap.MAIN_CAP, "pbox")


def test_after_15_minutes_four_are_no_cap_while_the_army_outweighs_them():
    late = agent_with(4, tanks(6), seconds=901)

    assert defense_cap.allows("pbox", "test", late.world, late.ctx) is True


def test_three_defenses_worth_over_1_5_times_the_army_stop_more():
    weak, strong = agent_with(3, [situations.unit(300, "e1")]), agent_with(3, tanks(6))

    assert defense_cap.allows("pbox", "test", weak.world, weak.ctx) is False
    assert defense_cap.allows("pbox", "test", strong.world, strong.ctx) is True


def test_no_defense_toward_a_cell_two_stand_by():
    agent = agent_with(2, tanks(6))

    # (30, 40) and (30, 56) both lie 8 cells from (30, 48); none stands by (14, 48).
    assert defense_cap.allows("pbox", "test", agent.world, agent.ctx, goal=(30, 48)) is False
    assert defense_cap.allows("pbox", "test", agent.world, agent.ctx, goal=(14, 48)) is True


def test_the_approach_alarm_orders_no_defense_over_the_cap():
    agent = agent_with(4, tanks(6))
    agent.ctx.approach_alarm, agent.ctx.approach_alarm_infantry, agent.ctx.approach_alarm_cell = True, True, (44, 48)
    alarm = ApproachAlarm()
    alarm._wanted = 1

    assert base_defense._approach_defense(agent.world, agent.ctx, alarm) == []


def test_switch_off_no_cap():
    agent = agent_with(4, tanks(6), tuning=OFF)

    assert defense_cap.allows("pbox", "test", agent.world, agent.ctx) is True and not defense_cap.is_full(
        agent.world, agent.ctx
    )


# -- hunters against enemy artillery -------------------------------------------------------------------------------


def wanted(family: str, offered: tuple[str, ...], enemies: list[dict]) -> dict[str, int]:
    agent = agent_with(0, [], visible_enemies=enemies)
    agent.world.available_production = frozenset(offered)
    ctx = SimpleNamespace(
        tuning=SHIPPED, army_mix=ArmyMix(SHIPPED.mix, SHIPPED.anti_air, AirWatch()), approach_alarm=False
    )
    return dict(counter_quota(family, agent.world, ctx).wanted)


def artillery(count: int) -> list[dict]:
    return [{"id": 900 + n, "type": "arty", "x": 60, "y": 40 + n, "hp": 100} for n in range(count)]


def test_two_hunters_are_wanted_for_each_enemy_artillery_piece():
    assert wanted(ALLIES, ("e1", "1tnk", "jeep"), artillery(2)) == {"1tnk": 4}
    assert wanted(ALLIES, ("e1", "jeep"), artillery(1)) == {"jeep": 2}
    assert wanted(SOVIET, ("e1", "3tnk"), artillery(1)) == {"3tnk": 2}
    assert wanted(ALLIES, ("e1", "1tnk"), []) == {}


def test_tuning_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT), Tuning()):
        assert tuning.base_defense.defense_cap and tuning.composition.anti_artillery_quota
        caps = tuning.base_defense
        assert (caps.defense_cap_main, caps.defense_cap_until_seconds, caps.defense_cap_army_ratio) == (4, 900, 1.5)
    assert BaseDefenseTuning().defense_cap and CompositionTuning().anti_artillery_quota
