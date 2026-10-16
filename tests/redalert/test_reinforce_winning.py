"""Part of main's idle units sent to a fight of ours going our way (see `reinforce_winning`)."""

from __future__ import annotations

import dataclasses

import pytest

import situations
from situations import ask, base_state
from switches_off import new_agent
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.tuning import Tuning

YARD, POWER = ("fact", 22, 48), ("powr", 19, 52)
# main's four heavy tanks standing idle by the staging point, 10 cells from the base toward the enemy's start.
IDLE_TANKS = [situations.unit(400 + n, "3tnk", 31 + n % 2, 47 + n // 2) for n in range(4)]
# Two rocket soldiers of ours firing on an enemy refinery 25 cells off.
ROCKETS = [situations.unit(460 + n, "e3", 48, 30 + n, idle=False) for n in range(2)]
REFINERY = {"id": 700, "type": "proc", "x": 51, "y": 30, "hp": 60}
CELL = [48, 30]


class FixedPlan:
    """A plan selector that always sets one plan."""

    def __init__(self, plan: str) -> None:
        self.plan = plan

    def select(self, world, ctx, choice) -> str:
        return self.plan


def agent_with(tuning: Tuning | None = None):
    return new_agent(CombatManager(), tuning=tuning or Tuning(), plan_selector=FixedPlan("HOLD"))


def cycle(agent, tick: int, units: list[dict], **observation) -> list[dict]:
    return ask(agent, base_state("england", YARD, POWER, tick=tick, units=units, **observation))


def winning_events(match_record) -> list[dict]:
    return [event for event in match_record("combat") if event["reason"] == "reinforce_winning"]


def sent_to(orders: list[dict], cell: list[int]) -> list[int]:
    return sorted(
        unit_id
        for order in orders
        if order["cmd"] == "attack_move" and order["to"] == cell
        for unit_id in order["units"]
    )


def test_rockets_on_an_enemy_refinery_draw_the_nearest_half_of_main_s_idle_tanks(match_record):
    agent = agent_with()

    orders = cycle(agent, 1000, [*IDLE_TANKS, *ROCKETS], visible_enemy_buildings=[REFINERY])

    # Four idle tanks 23-25 cells off: half of them go, the nearest.
    assert sent_to(orders, CELL) == [400, 401]
    (start,) = winning_events(match_record)
    assert (start["phase"], start["cell"], start["units"], start["enemy"]) == ("start", CELL, [400, 401], 0)
    assert start["buildings"] > 0


def test_a_helper_idle_at_the_fight_attacks_the_refinery_and_the_job_ends_done_once_it_is_gone(match_record):
    agent = agent_with()
    cycle(agent, 1000, [*IDLE_TANKS, *ROCKETS], visible_enemy_buildings=[REFINERY])
    there = [{**tank, "x": 49, "y": 28 + tank["id"] - 400} if tank["id"] in (400, 401) else tank for tank in IDLE_TANKS]

    finishing = cycle(agent, 1010, [*there, *ROCKETS], visible_enemy_buildings=[REFINERY])
    killed = situations.event(1, "enemy_killed", count=1)
    done = cycle(agent, 1015, [*there, *ROCKETS], visible_enemy_buildings=[], events=[killed])

    attacks = sorted(order["units"][0] for order in finishing if order.get("target") == 700)
    assert attacks == [400, 401]
    assert [event["phase"] for event in winning_events(match_record)] == ["start", "end"]
    assert winning_events(match_record)[-1]["why"] == "done"
    assert sent_to(done, CELL) == []


def test_a_fight_the_enemy_wins_draws_no_helper(match_record):
    agent = agent_with()
    heavies = [{"id": 720 + n, "type": "3tnk", "x": 52, "y": 29 + n, "hp": 100} for n in range(3)]

    orders = cycle(agent, 1000, [*IDLE_TANKS, *ROCKETS], visible_enemy_buildings=[REFINERY], visible_enemies=heavies)

    assert sent_to(orders, CELL) == [] and winning_events(match_record) == []


@pytest.mark.parametrize("far", [True, False], ids=["the fight 40 cells off", "switch off"])
def test_no_helper_beyond_35_cells_or_with_the_switch_off(match_record, far):
    tuning = Tuning()
    if not far:
        tuning = dataclasses.replace(tuning, combat=dataclasses.replace(tuning.combat, reinforce_winning=False))
    rockets = [{**rocket, "x": 70, "y": 10 + n} for n, rocket in enumerate(ROCKETS)] if far else ROCKETS
    refinery = {**REFINERY, "x": 73, "y": 10} if far else REFINERY
    agent = agent_with(tuning)

    orders = cycle(agent, 1000, [*IDLE_TANKS, *rockets], visible_enemy_buildings=[refinery])

    assert winning_events(match_record) == []
    assert [
        order for order in orders if order["cmd"] == "attack_move" and set(order["units"]) & {400, 401, 402, 403}
    ] == []


# Three heavy tanks of ours fighting two enemy medium tanks 25 cells from main's idle ones.
FIGHTERS = [situations.unit(470 + n, "3tnk", 48, 29 + n, idle=False) for n in range(3)]
TWO_MEDIUM = [{"id": 730 + n, "type": "2tnk", "x": 52, "y": 29 + n, "hp": 100} for n in range(2)]
NINE_HEAVY = [{"id": 740 + n, "type": "3tnk", "x": 52 + n % 3, "y": 27 + n // 3, "hp": 100} for n in range(9)]


def test_a_won_fight_against_tanks_draws_helpers_and_the_job_ends_losing_once_the_enemy_outweighs_us(match_record):
    agent = agent_with()

    joined = cycle(agent, 1000, [*IDLE_TANKS, *FIGHTERS], visible_enemies=TWO_MEDIUM)
    turned = cycle(agent, 1005, [*IDLE_TANKS, *FIGHTERS], visible_enemies=[*TWO_MEDIUM, *NINE_HEAVY])

    assert sent_to(joined, CELL[:1] + [29]) == [400, 401]
    events = winning_events(match_record)
    assert [(event["phase"], event.get("why")) for event in events] == [("start", None), ("end", "losing")]
    assert events[0]["enemy"] >= 1500 and events[0]["own"] >= 1.3 * events[0]["enemy"]
    assert sent_to(turned, [48, 29]) == []
