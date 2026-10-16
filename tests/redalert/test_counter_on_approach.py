"""The counter quota against an enemy army coming for the base: trained through the opening and the economy holds while
the approach alarm is in force, its cap free of the base army's riflemen, and the Allied jeeps preferred."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import situations
from situations import ask, base_state
from ucla_agent.games.redalert import counter_units
from ucla_agent.games.redalert.anti_air import AirWatch
from ucla_agent.games.redalert.composition import ArmyMix, counter_quota
from ucla_agent.games.redalert.items import ALLIES
from ucla_agent.games.redalert.tuning import CompositionTuning, Tuning
from ucla_agent.games.redalert.upkeep import UpkeepManager

SHIPPED = Tuning()
YARD = (("fact", 22, 48), ("powr", 19, 52), ("tent", 18, 44), ("proc", 26, 52))
WITH_FACTORY = (*YARD, ("weap", 26, 44))


def composition_with(**values) -> Tuning:
    return dataclasses.replace(SHIPPED, composition=dataclasses.replace(SHIPPED.composition, **values))


class FixedPlan:
    """A plan selector that always sets one plan."""

    def __init__(self, plan: str) -> None:
        self.plan = plan

    def select(self, world, ctx, choice) -> str:
        return self.plan


def enemies(unit_type: str, count: int) -> list[dict]:
    return [{"id": 900 + n, "type": unit_type, "x": 60, "y": 40 + n % 4, "hp": 100} for n in range(count)]


def own(unit_type: str, count: int, first_id: int) -> list[dict]:
    return [situations.unit(first_id + n, unit_type) for n in range(count)]


def harvesters() -> list[dict]:
    return [situations.unit(300 + n, "harv", 22, 41, idle=False) for n in range(2)]


def unit_trains(orders: list[dict]) -> list[tuple[str, int]]:
    return [(order["item"], order["count"]) for order in orders if order["cmd"] == "train" and order["item"] != "harv"]


def agent(plan: str, tuning: Tuning = SHIPPED):
    return situations.new_agent(UpkeepManager(), tuning=tuning, plan_selector=FixedPlan(plan))


# -- trained through the opening -----------------------------------------------------------------------------------


def test_the_alarm_trains_the_counters_in_the_opening():
    on, off = agent("OPENING"), agent("OPENING", composition_with(counter_on_approach=False))
    state = base_state("england", *YARD, units=harvesters(), visible_enemies=enemies("e1", 8))
    on.ctx.approach_alarm = off.ctx.approach_alarm = True

    # 8 rifles: 2 anti-infantry rifles wanted, no war factory for jeeps.
    assert unit_trains(ask(on, state)) == [("e1", 2)]
    assert unit_trains(ask(off, state)) == []


def test_no_alarm_no_counters_in_the_opening():
    quiet = agent("OPENING")

    assert (
        unit_trains(ask(quiet, base_state("england", *YARD, units=harvesters(), visible_enemies=enemies("e1", 8))))
        == []
    )


# -- the cap -------------------------------------------------------------------------------------------------------


def test_the_cap_leaves_the_base_armys_rifles_out():
    # 10 rifles and 2 light tanks: the rifles are 1,000 of 2,400, over 35 percent were they counted as counters.
    units = [*harvesters(), *own("e1", 10, 400), *own("1tnk", 2, 500)]
    state = base_state("england", *WITH_FACTORY, units=units)
    skipping, counting = agent("HOLD"), agent("HOLD", composition_with(counter_cap_skips_rifles=False))
    ask(skipping, state)
    ask(counting, state)
    wanted = {"jeep": 5, "e1": 5}

    assert counter_units.is_capped(wanted, skipping.world, skipping.ctx) is False
    assert counter_units.is_capped(wanted, counting.world, counting.ctx) is True


# -- the quota -----------------------------------------------------------------------------------------------------


def quota(world, *, alarm: bool = False, tuning: Tuning = SHIPPED) -> dict[str, int]:
    ctx = SimpleNamespace(
        tuning=tuning, army_mix=ArmyMix(tuning.mix, tuning.anti_air, AirWatch()), approach_alarm=alarm
    )
    return dict(counter_quota(ALLIES, world, ctx).wanted)


def seen_world(enemy_type: str, count: int, buildings=YARD, offered=("e1", "e3")):
    shown = situations.new_agent(tuning=SHIPPED)
    ask(shown, base_state("england", *buildings, visible_enemies=enemies(enemy_type, count)))
    shown.world.available_production = frozenset(offered)
    return shown.world


def test_five_rifles_count_as_infantry_heavy_while_the_alarm_is_in_force():
    world = seen_world("e1", 5)

    assert quota(world) == {}
    assert quota(world, alarm=True) == {"e1": 2}
    assert quota(world, alarm=True, tuning=composition_with(counter_on_approach=False)) == {}


def test_the_jeeps_take_the_larger_half_once_a_war_factory_stands():
    # 10 rockets: 5 anti-infantry units wanted.
    world = seen_world("e3", 10, buildings=WITH_FACTORY, offered=("e1", "e3", "jeep", "1tnk"))

    assert quota(world) == {"jeep": 3, "e1": 2}
    assert quota(world, tuning=composition_with(counter_prefer_jeeps=False)) == {"jeep": 2, "e1": 3}


def test_tuning_switches_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).composition, CompositionTuning()):
        assert tuning.counter_on_approach and tuning.counter_cap_skips_rifles and tuning.counter_prefer_jeeps
