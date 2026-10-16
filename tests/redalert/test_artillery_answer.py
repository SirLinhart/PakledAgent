"""Our units under an enemy artillery piece or V2 close in on it or leave its range (see `artillery_answer`)."""

from __future__ import annotations

import dataclasses
import math

import pytest

import situations
from situations import ask, base_state
from switches_off import new_agent
from ucla_agent.games.redalert.batch import Priority
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.tuning import Tuning

YARD, POWER = ("fact", 22, 48), ("powr", 19, 52)
# An enemy artillery piece 10 cells east of main standing at the staging point, within its range of 12.
PIECE = {"id": 700, "type": "arty", "x": 42, "y": 48, "hp": 100}
TANKS = [situations.unit(400 + n, "2tnk", 32, 47 + n) for n in range(2)]
ROCKETS = [situations.unit(460 + n, "e3", 31, 47 + n) for n in range(2)]
# Two enemy rifles beside the piece: it is escorted.
ESCORT = [{"id": 710 + n, "type": "e1", "x": 43, "y": 47 + 2 * n, "hp": 100} for n in range(2)]
OFF = dataclasses.replace(Tuning(), combat=dataclasses.replace(Tuning().combat, artillery_answer=False))


class FixedPlan:
    """A plan selector that always sets one plan."""

    def __init__(self, plan: str) -> None:
        self.plan = plan

    def select(self, world, ctx, choice) -> str:
        return self.plan


def shelled(units, enemies, *, tuning: Tuning | None = None, plan: str = "HOLD", **observation) -> list[dict]:
    agent = new_agent(CombatManager(), tuning=tuning or Tuning(), plan_selector=FixedPlan(plan))
    return ask(agent, base_state("england", YARD, POWER, units=list(units), visible_enemies=enemies, **observation))


def orders_of(orders: list[dict], unit_id: int) -> list[dict]:
    return [order for order in orders if unit_id in order.get("units", ())]


def test_a_lone_piece_is_attacked_by_main_s_tanks_and_rockets():
    orders = shelled([*TANKS, *ROCKETS], [PIECE])

    for unit_id in (400, 401, 460, 461):
        assert orders_of(orders, unit_id) == [{"cmd": "attack", "units": [unit_id], "target": 700}]


def test_an_escorted_piece_is_attacked_by_every_unit_that_can_hurt_it_and_our_artillery_out_of_range_leaves():
    # Our artillery piece 13 cells off, beyond its own range of 12 but within the enemy's range and a cell.
    engineer = [situations.unit(490, "arty", 29, 48)]

    orders = shelled([*TANKS, *ROCKETS, *engineer], [PIECE, *ESCORT])

    for unit_id in (400, 460):
        assert orders_of(orders, unit_id) == [{"cmd": "attack", "units": [unit_id], "target": 700}]
    (leave,) = orders_of(orders, 490)
    assert leave["cmd"] == "move" and math.dist(leave["to"], (42, 48)) >= 13.5


def test_an_answer_move_is_sent_again_only_once_its_cell_moved_3_cells_or_3_s_later():
    agent = new_agent(CombatManager(), tuning=Tuning(), plan_selector=FixedPlan("HOLD"))
    engineer = [situations.unit(490, "arty", 29, 48)]

    def cycle(tick: int, piece: tuple[int, int]) -> list[dict]:
        moved = {**PIECE, "x": piece[0], "y": piece[1]}
        state = base_state("england", YARD, POWER, tick=tick, units=engineer, visible_enemies=[moved])
        return orders_of(ask(agent, state), 490)

    # 13 cells off throughout, the piece rolls a cell, then 5 more round us, then stands: re-sent 3 s on.
    sent = [cycle(1000, (42, 48)), cycle(1005, (42, 49)), cycle(1010, (41, 53)), cycle(1090, (41, 53))]

    assert [len(orders) for orders in sent] == [1, 0, 1, 1]


def test_our_own_artillery_within_its_range_fires_back_on_the_piece():
    ours = [situations.unit(480, "arty", 33, 48)]

    orders = shelled(ours, [PIECE])

    assert orders_of(orders, 480) == [{"cmd": "attack", "units": [480], "target": 700}]


def test_a_tank_inside_the_piece_s_minimum_range_attacks_it_and_is_told_once():
    agent = new_agent(CombatManager(), tuning=Tuning(), plan_selector=FixedPlan("HOLD"))
    close = [situations.unit(400, "2tnk", 40, 48)]
    state = base_state("england", YARD, POWER, units=close, visible_enemies=[PIECE])

    first = ask(agent, state)
    again = ask(agent, {**state, "state_version": 1005, "observation": {**state["observation"], "tick": 1005}})

    assert orders_of(first, 400) == [{"cmd": "attack", "units": [400], "target": 700}]
    assert orders_of(again, 400) == []


@pytest.mark.parametrize("plan", ["HOLD", "PRESERVE"])
def test_with_the_switch_off_main_is_not_sent_at_the_piece(plan):
    orders = shelled([*TANKS, *ROCKETS], [PIECE], tuning=OFF, plan=plan)

    assert not [order for order in orders if order.get("to") == [42, 48] or order.get("target") == 700]


def two_cycles(first: list[dict], second: list[dict], first_enemies: list[dict], *, tuning: Tuning | None = None):
    """The orders of a second state 5 ticks after a first, our units given in each, the enemies in sight in the first
    alone."""
    agent = new_agent(CombatManager(), tuning=tuning or Tuning(), plan_selector=FixedPlan("HOLD"))
    ask(agent, base_state("england", YARD, POWER, units=first, visible_enemies=first_enemies))
    return ask(agent, base_state("england", YARD, POWER, tick=1005, units=second, visible_enemies=[]))


def test_out_of_sight_a_hit_from_a_piece_seen_near_is_answered_at_its_last_cell():
    # The piece, seen 21 cells off, is out of sight when the units stand 12 from its cell and are hit.
    away = [{**unit, "x": 25} for unit in [*TANKS, *ROCKETS]]
    near = [{**unit, "x": 34, "hp": 80} for unit in [*TANKS, *ROCKETS]]
    piece = {**PIECE, "x": 46}

    orders = two_cycles(away, near, [piece])

    assert orders_of(orders, 400) == [{"cmd": "move", "units": [400], "to": [46, 48]}]
    (leave,) = orders_of(orders, 460)
    assert math.dist(leave["to"], (46, 48)) >= 13.5


def test_out_of_sight_a_hit_with_no_piece_remembered_steps_away_from_the_enemy_and_one_tank_toward_it():
    units = [*TANKS, *ROCKETS]
    hit = [{**unit, "hp": 80} for unit in units]
    # An enemy piece seen far off: the enemy fields artillery.
    far = {**PIECE, "x": 80, "y": 10}

    orders = two_cycles(units, hit, [far])

    # The enemy's start is at (74, 48), east: the first tank steps 6 east to see the shooter, the rest 6 west.
    assert orders_of(orders, 400) == [{"cmd": "move", "units": [400], "to": [38, 47]}]
    assert [order["to"][0] for unit_id in (401, 460, 461) for order in orders_of(orders, unit_id)] == [26, 25, 25]


def test_out_of_sight_with_the_switch_off_a_hit_unit_is_not_moved():
    units = [*TANKS, *ROCKETS]
    off = dataclasses.replace(Tuning(), combat=dataclasses.replace(Tuning().combat, artillery_unseen_answer=False))

    orders = two_cycles(units, [{**unit, "hp": 80} for unit in units], [{**PIECE, "x": 80, "y": 10}], tuning=off)

    assert orders == []


def test_out_of_sight_a_hit_before_any_enemy_piece_was_seen_moves_no_unit():
    units = [*TANKS, *ROCKETS]

    assert two_cycles(units, [{**unit, "hp": 80} for unit in units], []) == []


def test_out_of_sight_a_hit_unit_on_a_job_of_its_own_answers_too_as_an_emergency_with_its_event(match_record):
    agent = new_agent(CombatManager(), tuning=Tuning(), plan_selector=FixedPlan("HOLD"))
    raider = [situations.unit(470, "2tnk", 32, 47)]
    far = {**PIECE, "x": 80, "y": 10}
    ask(agent, base_state("england", YARD, POWER, units=raider, visible_enemies=[far]))
    agent.ctx.groups.assign(470, "raid")

    hit = [{**raider[0], "hp": 80}]
    orders = ask(agent, base_state("england", YARD, POWER, tick=1005, units=hit, visible_enemies=[]))

    assert orders_of(orders, 470) == [{"cmd": "move", "units": [470], "to": [38, 47]}]
    assert {entry.priority for entry in agent.ledger.entries if 470 in entry.order.get("units", ())} == {
        Priority.EMERGENCY
    }
    (logged,) = [event for event in match_record("combat") if event["reason"] == "artillery_unseen"]
    assert logged["orders"] == [[470, "move", [38, 47]]]
