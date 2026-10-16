"""The escort of an MCV on its way kept with it while the base is attacked (`[base_defense] keep_mcv_escort`)."""

from __future__ import annotations

import dataclasses

import situations
from situations import ask, base_state
from ucla_agent.games.redalert.base_defense import ESCORT_KEPT, BaseDefenseManager
from ucla_agent.games.redalert.expansion import ESCORT
from ucla_agent.games.redalert.groups import GUARD
from ucla_agent.games.redalert.tuning import BaseDefenseTuning, Tuning

BASE = (("fact", 22, 48), ("powr", 19, 52), ("tent", 18, 44))
SHIPPED = Tuning()
OFF = dataclasses.replace(SHIPPED, base_defense=dataclasses.replace(SHIPPED.base_defense, keep_mcv_escort=False))
ESCORT_IDS = (400, 401, 402)
GUARD_IDS = (300, 301)


def raiders() -> list[dict]:
    return [{"id": 900 + n, "type": "e1", "x": 22 + n, "y": 42, "hp": 100} for n in range(4)]


def units(*, guard: bool = True) -> list[dict]:
    """The MCV on its way 14 cells east of the yard with three tanks around it, and two rifles of the guard."""
    mcv = situations.unit(500, "mcv", 36, 48, idle=False)
    escort = [situations.unit(unit_id, "2tnk", 35, 47 + n) for n, unit_id in enumerate(ESCORT_IDS)]
    rifles = [situations.unit(unit_id, "e1", 21, 46) for unit_id in GUARD_IDS] if guard else []
    return [mcv, *escort, *rifles]


def sent(tuning: Tuning = SHIPPED, *, guard: bool = True) -> set[int]:
    """The units base defense sends at the raid on the yard, its escort put in the escort group."""
    agent = situations.new_agent(BaseDefenseManager(), tuning=tuning)
    ask(agent, base_state("england", *BASE, tick=1000, units=units(guard=guard)))
    for unit_id in ESCORT_IDS:
        agent.ctx.groups.assign(unit_id, ESCORT)
    for unit_id in GUARD_IDS if guard else ():
        agent.ctx.groups.assign(unit_id, GUARD)
    orders = ask(agent, base_state("england", *BASE, tick=1005, units=units(guard=guard), visible_enemies=raiders()))
    return {unit_id for order in orders if order["cmd"] in ("attack", "attack_move") for unit_id in order["units"]}


def test_the_escort_stays_with_the_mcv_while_the_guard_answers(match_record):
    answering = sent()

    assert answering.isdisjoint(ESCORT_IDS) and answering & set(GUARD_IDS)
    assert [event["reason"] for event in match_record("base_defense")].count(ESCORT_KEPT) == 1


def test_without_the_switch_the_escort_is_drawn_into_the_fight():
    assert set(ESCORT_IDS) <= sent(OFF)


def test_a_base_truly_losing_takes_the_escort():
    # No other unit answers, and no defense stands by the yard.
    assert set(ESCORT_IDS) <= sent(guard=False)


def test_tuning_keep_mcv_escort_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).base_defense, BaseDefenseTuning()):
        assert tuning.keep_mcv_escort is True and tuning.escort_needed_ratio == 0.7


def test_with_no_other_unit_a_pillbox_in_reach_of_the_raiders_keeps_the_escort_and_raises_no_error(match_record):
    # The escort check weighs the defenses that reach the threat's enemies (see `_home_strength`), the whole threat
    # given: a pillbox 2 cells from the raiders is worth more than 0.7 times their 400.
    agent = situations.new_agent(BaseDefenseManager(), tuning=SHIPPED)
    base = (*BASE, ("pbox", 23, 44))
    ask(agent, base_state("england", *base, tick=1000, units=units(guard=False)))
    for unit_id in ESCORT_IDS:
        agent.ctx.groups.assign(unit_id, ESCORT)

    orders = ask(agent, base_state("england", *base, tick=1005, units=units(guard=False), visible_enemies=raiders()))

    answering = {unit_id for order in orders if order["cmd"] in ("attack", "attack_move") for unit_id in order["units"]}
    assert answering.isdisjoint(ESCORT_IDS)
    assert match_record("error") == []
    assert [event["reason"] for event in match_record("base_defense")].count(ESCORT_KEPT) == 1
