"""Clock play: the end-of-match spending and the lead-preserving defense buy defenses only for a ground threat."""

from __future__ import annotations

import dataclasses

import pytest

import situations
from situations import OpeningThen, ScriptedManager, ask, base_state, with_command
from switches_off import new_agent, tuning_off, upkeep_off
from ucla_agent.games.redalert import clock
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.composition import PRESERVE
from ucla_agent.games.redalert.tuning import ClockTuning, Tuning
from ucla_agent.games.redalert.upkeep import defense_within_power

ALLIED_BASE = (("fact", 22, 48), ("powr", 19, 52), ("powr", 16, 52), ("tent", 18, 44), ("weap", 26, 54))
BARRACKS_ID = 104
# The end-of-match spending as it ships: no defense without a recent ground hit.
SHIPPED_CLOCK = tuning_off(clock=ClockTuning())
SECONDS_LEFT = 100
MATCH_SECONDS = 1200
DEFENSE_TYPES = {"pbox", "gun", "hbox", "agun", "ftur", "tsla", "sam"}


def spending_agent(tuning: Tuning = SHIPPED_CLOCK):
    """An agent with base defense and the end-of-match spending alone."""
    return new_agent(BaseDefenseManager(), ScriptedManager("upkeep", clock.EndgameSpend().intents), tuning=tuning)


def at(seconds: int, *, seconds_left: int | None = None, cash: int = 5000, **observation) -> dict:
    """The Allied base at a game time, with so many seconds of the match left (all of the rest unless given)."""
    state = base_state("england", *ALLIED_BASE, tick=seconds * 25, cash=cash, **observation)
    left = MATCH_SECONDS - seconds if seconds_left is None else seconds_left
    state["time"] = {"max_minutes": 20, "game_seconds": seconds, "remaining_seconds": left}
    return state


def hit(seq: int, enemies: list[dict]) -> dict:
    """The barracks reported under attack, with these enemies in sight."""
    event = situations.event(seq, "under_attack", id=BARRACKS_ID, type="tent", x=18, y=44, hp=80)
    return {"events": [event], "visible_enemies": enemies}


def rifles(count: int = 3) -> list[dict]:
    return [{"id": 700 + number, "type": "e1", "x": 20, "y": 42 + number, "hp": 100} for number in range(count)]


def helicopters() -> list[dict]:
    return [{"id": 800 + number, "type": "heli", "x": 19, "y": 43 + number, "hp": 100} for number in range(2)]


def defenses(orders: list[dict]) -> list[str]:
    return [order["item"] for order in with_command(orders, "build") if order["item"] in DEFENSE_TYPES]


def last_minutes(agent, hit_seconds_before: int | None, enemies=rifles) -> list[dict]:
    """The orders of the spending with 100 s left, after a hit on the barracks so many seconds before (none for
    None)."""
    spend_at = MATCH_SECONDS - SECONDS_LEFT
    if hit_seconds_before is not None:
        ask(agent, at(spend_at - hit_seconds_before, cash=0, **hit(1, enemies())))
    return ask(agent, at(spend_at, seconds_left=SECONDS_LEFT))


def test_reactive_defenses_with_100_s_left_and_5000_cash_and_no_ground_hit_the_spending_queues_no_defense():
    orders = last_minutes(spending_agent(), None)

    assert defenses(orders) == [] and with_command(orders, "train")


def test_reactive_defenses_with_a_ground_hit_on_a_building_30_s_ago_the_spending_queues_one_defense():
    agent = spending_agent()

    orders = last_minutes(agent, 30)

    assert agent.ctx.ground_hit_tick == (MATCH_SECONDS - SECONDS_LEFT - 30) * 25
    assert len(defenses(orders)) == 1


@pytest.mark.parametrize("seconds_before, queued", [(60, 1), (61, 0)], ids=["60 s ago", "61 s ago"])
def test_reactive_defenses_a_ground_hit_counts_for_60_seconds(seconds_before, queued):
    assert len(defenses(last_minutes(spending_agent(), seconds_before))) == queued


def test_reactive_defenses_a_hit_by_aircraft_alone_is_no_ground_hit_and_queues_no_defense():
    agent = spending_agent()

    orders = last_minutes(agent, 30, enemies=helicopters)

    assert agent.ctx.ground_hit_tick is None and defenses(orders) == []


def test_reactive_defenses_with_endgame_defenses_on_the_spending_queues_a_defense_with_no_hit():
    switched_on = dataclasses.replace(SHIPPED_CLOCK, clock=ClockTuning(endgame_defenses=True))

    assert len(defenses(last_minutes(spending_agent(switched_on), None))) == 1


# -- the lead-preserving defense -------------------------------------------------------------------------------------

# The lead-preserving defense with upkeep's defenses bought only against a threat, as it ships.
ON_THREAT_ONLY = tuning_off(upkeep=upkeep_off(defenses_on_threat_only=True), clock=ClockTuning())


def preserving_agent():
    """An agent with base defense and the lead-preserving defense alone, under PRESERVE once the war factory stood."""
    preserve = ScriptedManager("upkeep", clock.PreserveDefense(defense_within_power).intents)
    return new_agent(BaseDefenseManager(), preserve, tuning=ON_THREAT_ONLY, plan_selector=OpeningThen(PRESERVE))


def under_threat(enemies: list[dict]):
    """A preserving agent, and its orders in the cycle these enemies come over the base."""
    agent = preserving_agent()
    ask(agent, at(600))
    return agent, ask(agent, at(605, visible_enemies=enemies))


def test_reactive_defenses_an_aircraft_only_threat_starts_no_preserve_defense():
    agent, orders = under_threat(helicopters()[:1])

    assert (agent.ctx.plan, agent.ctx.threat_active, agent.ctx.threat_aircraft_only) == (PRESERVE, True, True)
    assert defenses(orders) == []


def test_reactive_defenses_a_ground_threat_starts_one_preserve_defense():
    agent, orders = under_threat(rifles(1))

    assert (agent.ctx.threat_active, agent.ctx.threat_aircraft_only) == (True, False)
    assert defenses(orders) == ["pbox"]
