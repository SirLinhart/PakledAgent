"""The base harass: a slot scout by an enemy base with no defense and no army near shoots its soft targets."""

from __future__ import annotations

import pytest

import situations
from scout_scenes import (
    CAUTIOUS,
    JEEP,
    allied,
    at_seconds,
    attacks,
    careful,
    enemy,
    jeep,
    jeep_agent,
    moves,
    other_jeep,
    slot_events,
    soviet,
)
from situations import ask
from switches_off import new_agent
from ucla_agent.games.redalert import scout_harass
from ucla_agent.games.redalert.scout_harass import Harasses
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.world import EnemyRecord, Unit

HARASS = careful(scout_base_harass=True)
# Jeep 400 6 cells west of a remembered enemy construction yard, and the soft targets in sight within 12 cells of it.
AT_BASE = (60, 48)
YARD = enemy(600, "fact", 66, 48)
ENGINEER = enemy(801, "e6", 63, 50)
TRUCK = enemy(802, "harv", 62, 44)
POWER = enemy(603, "powr", 66, 52)
FACTORY = enemy(604, "weap", 68, 44)
START = 1010


def scene(tick: int, *units: dict, sighted=(ENGINEER, TRUCK), buildings=(YARD, POWER, FACTORY)) -> dict:
    """The Allied base of the scenes with jeep 401 at home, the units given and the enemies in sight."""
    return allied(
        *units, other_jeep(), tick=tick, visible_enemies=list(sighted), visible_enemy_buildings=list(buildings)
    )


def order_on(target: dict, unit_id: int = JEEP) -> dict:
    return {"cmd": "attack", "units": [unit_id], "target": target["id"]}


def of_jeep(events: list[dict], kind: str, unit_id: int = JEEP) -> list[dict]:
    return [event for event in slot_events(events, kind) if event["unit_id"] == unit_id]


def test_harass_target_order(match_record):
    agent = jeep_agent(HARASS)
    # The yard was seen from afar and is remembered out of sight: the power plant, then the war factory are in sight.
    ask(agent, scene(START - 2, jeep((28, 50)), sighted=(), buildings=(YARD,)))
    in_sight = (POWER, FACTORY)

    first = ask(agent, scene(START, jeep(AT_BASE), buildings=in_sight))
    truck = ask(agent, scene(START + 5, jeep(AT_BASE), sighted=(TRUCK,), buildings=in_sight))
    power = ask(agent, scene(START + 10, jeep(AT_BASE), sighted=(), buildings=in_sight))
    factory = ask(agent, scene(START + 15, jeep(AT_BASE), sighted=(), buildings=(FACTORY,)))

    assert [attacks(orders) for orders in (first, truck, power, factory)] == [
        [order_on(ENGINEER)],
        [order_on(TRUCK)],
        [order_on(POWER)],
        [order_on(FACTORY)],
    ]
    assert moves(first) == []
    (harass,) = of_jeep(match_record("scout_slot"), "harass")
    assert (harass["cell"], harass["target_id"], harass["target_type"]) == (list(AT_BASE), 801, "e6")


@pytest.mark.parametrize("guard", ["pillbox 15 cells away", "rifle seen 10 s ago 12 cells away"])
def test_no_harass_with_defense_or_fighting_unit(guard, match_record):
    agent = jeep_agent(HARASS)
    if guard.startswith("pillbox"):
        buildings = (YARD, POWER, FACTORY, enemy(605, "pbox", 60, 63))
        batches = [ask(agent, scene(START, jeep(AT_BASE), buildings=buildings))]
    else:
        rifle = enemy(803, "e1", 60, 36)
        batches = [
            ask(agent, scene(START, jeep(AT_BASE), sighted=(ENGINEER, TRUCK, rifle))),
            ask(agent, scene(START + at_seconds(10), jeep(AT_BASE))),
        ]

    assert [attacks(orders) for orders in batches] == [[]] * len(batches)
    assert slot_events(match_record("scout_slot"), "harass") == []


@pytest.mark.parametrize("rifle_x, baited", [(63, True), (69, False)], ids=["3 cells away", "9 cells away"])
def test_harass_ends_and_retreats_on_new_enemy(rifle_x, baited, match_record):
    agent = jeep_agent(careful(scout_base_harass=True, scout_bait_shot=True))
    ask(agent, scene(START, jeep(AT_BASE)))
    rifle = enemy(803, "e1", rifle_x, 48)

    ended = ask(agent, scene(START + 5, jeep(AT_BASE), sighted=(ENGINEER, TRUCK, rifle)))

    events = match_record("scout_slot")
    (end,) = of_jeep(events, "harass_end")
    (retreat,) = of_jeep(events, "retreat")
    assert (end["reason"], end["orders"], retreat["cause"]) == ("enemy_unit", 1, "outclassed")
    if baited:
        assert retreat["bait"] == [803, "e1"] and attacks(ended) == [order_on(rifle)] and moves(ended) == []
    else:
        assert retreat["bait"] is None and attacks(ended) == [] and moves(ended) == [retreat["to"]]


def test_a_harass_ends_hit_with_a_hit_retreat(match_record):
    agent = jeep_agent(HARASS)
    ask(agent, scene(START, jeep(AT_BASE)))

    hit = ask(agent, scene(START + 5, jeep(AT_BASE, hp=97)))

    events = match_record("scout_slot")
    assert [event["reason"] for event in of_jeep(events, "harass_end")] == ["hit"]
    (retreat,) = of_jeep(events, "retreat")
    assert retreat["cause"] == "hit" and moves(hit) == [retreat["to"]] and attacks(hit) == []


def test_harass_timeout_and_cooldown(match_record):
    agent = jeep_agent(HARASS)
    ask(agent, scene(START, jeep(AT_BASE, idle=True)))

    during = [ask(agent, scene(tick, jeep(AT_BASE, idle=True))) for tick in range(START + 50, START + 1125, 50)]
    over = ask(agent, scene(START + 1125, jeep(AT_BASE, idle=True)))
    cooling = ask(agent, scene(START + 1125 + at_seconds(59), jeep(AT_BASE, idle=True)))
    again = ask(agent, scene(START + 1125 + at_seconds(60), jeep(AT_BASE, idle=True)))

    events = match_record("scout_slot")
    (end,) = of_jeep(events, "harass_end")
    # Its first order, and one again each 2 s it stood idle, until the 45 s were up.
    assert (end["reason"], end["orders"]) == ("timeout", 1 + 22)
    assert all(attacks(orders) == [order_on(ENGINEER)] for orders in during)
    assert attacks(over) == [] and moves(over) != [] and attacks(cooling) == []
    assert attacks(again) == [order_on(ENGINEER)] and len(of_jeep(events, "harass")) == 2


def dog_at_base(sighted: tuple[dict, ...]) -> list[dict]:
    """Dog 500 in the first slot 6 cells from the enemy's yard with the enemies `sighted` in sight: the orders."""
    agent = new_agent(ScoutingManager(), tuning=HARASS)
    dogs = [situations.unit(500, "dog", 14, 46), situations.unit(501, "dog", 14, 47)]
    ask(agent, soviet(*dogs, tick=1000))
    at_base = situations.unit(500, "dog", *AT_BASE, idle=False)
    shown = {"visible_enemies": list(sighted), "visible_enemy_buildings": [YARD, POWER]}
    return ask(agent, soviet(at_base, dogs[1], tick=START, **shown))


def test_dog_harasses_only_infantry(match_record):
    truck_and_power = dog_at_base((TRUCK,))
    engineer = dog_at_base((TRUCK, ENGINEER))

    assert attacks(truck_and_power, 500) == []
    assert attacks(engineer, 500) == [order_on(ENGINEER, 500)]
    assert [event["target_id"] for event in of_jeep(match_record("scout_slot"), "harass", 500)] == [801]


def test_rush_scout_does_not_harass_during_watch(match_record):
    rush = careful(scout_base_harass=True, rush_detection=True, rush_scout_direct=True)
    agent = jeep_agent(rush)

    passing = ask(agent, scene(at_seconds(110), jeep(AT_BASE)))

    assert attacks(passing) == [] and slot_events(match_record("scout_slot"), "harass") == []


def test_switch_off_no_harass(match_record):
    agent = jeep_agent(CAUTIOUS)

    orders = ask(agent, scene(START, jeep(AT_BASE)))

    assert attacks(orders) == [] and slot_events(match_record("scout_slot"), "harass") == []


# -- the parts -------------------------------------------------------------------------------------------------------


def a_world():
    agent = jeep_agent(HARASS)
    return agent.world, agent.ctx


def scout(unit_type: str = "jeep", cell: tuple[int, int] = AT_BASE, hp: int = 100) -> Unit:
    return Unit(id=JEEP, type=unit_type, x=cell[0], y=cell[1], hp=hp, idle=False, activity="move", stance="hold_fire")


def record(
    enemy_id: int, enemy_type: str, cell: tuple[int, int], *, building: bool = False, visible: bool = True
) -> EnemyRecord:
    return EnemyRecord(enemy_id, enemy_type, cell[0], cell[1], 100, building, last_seen_tick=START, visible=visible)


def open_base() -> dict[int, EnemyRecord]:
    """An engineer in sight 2 cells from jeep 400, and the enemy's yard remembered out of sight 6 cells from it."""
    return {1: record(1, "e6", (62, 48)), 2: record(2, "fact", (66, 48), building=True, visible=False)}


def test_tier_ranks_engineers_then_soft_units_then_power_then_other_buildings_and_no_fighting_unit():
    shown = [
        record(1, "e6", AT_BASE),
        record(2, "spy", AT_BASE),
        record(3, "harv", AT_BASE),
        record(4, "apwr", AT_BASE, building=True),
        record(5, "proc", AT_BASE, building=True),
        record(6, "e1", AT_BASE),
        record(7, "mcv", AT_BASE),
    ]

    assert [scout_harass.tier(found) for found in shown] == [1, 2, 2, 3, 4, None, None]


def test_harass_targets_leave_out_walls_covered_far_and_unhurtable_enemies_nearest_of_a_tier_first():
    world, ctx = a_world()
    world.enemy_memory = {
        1: record(1, "proc", (64, 48), building=True),
        2: record(2, "sbag", (61, 48), building=True),
        3: record(3, "proc", (62, 48), building=True),
        4: record(4, "e6", (73, 48)),
        5: record(5, "proc", (70, 60), building=True),
        6: record(6, "gun", (75, 60), building=True),
    }

    assert [found.id for found in scout_harass.harass_targets(scout(), world, ctx)] == [3, 1]
    assert scout_harass.harass_targets(scout("dog"), world, ctx) == []


def test_harasses_start_by_an_open_base_end_no_target_after_3_s_and_keep_the_60_s_cooldown():
    world, ctx = a_world()
    world.enemy_memory = open_base()
    harasses, harasser = Harasses(), scout()

    harass, first = harasses.start(harasser, world, ctx)
    assert first.id == 1 and harasses.due(harasser, world).id == 1 and harasses.harassing == {JEEP}
    harasses.ordered(JEEP, world.tick)
    assert harasses.due(harasser, world) is None and harass.orders == 1
    world.enemy_memory[1].visible = False
    assert harasses.end({1: harasser}, lambda unit_id: 100, world, ctx) == []
    world.tick += 75
    (ended,) = harasses.end({1: harasser}, lambda unit_id: 100, world, ctx)
    assert (ended.slot, ended.reason, harasses.harassing) == (1, "no_target", frozenset())
    world.enemy_memory[1].visible = True
    world.tick += 60 * 25 - 1
    assert harasses.start(harasser, world, ctx) is None
    world.tick += 1
    assert harasses.start(harasser, world, ctx) is not None


@pytest.mark.parametrize(
    "guard, reason",
    [(record(3, "pbox", (60, 67), building=True), "defense"), (record(3, "e1", (60, 67)), "enemy_unit")],
    ids=["a defense", "a fighting unit"],
)
def test_harasses_end_at_a_guard_within_20_cells(guard, reason):
    world, ctx = a_world()
    world.enemy_memory = open_base()
    harasses, harasser = Harasses(), scout()
    harasses.start(harasser, world, ctx)

    world.enemy_memory[3] = guard
    world.known_enemy_defenses = [guard] if guard.is_building else []
    (ended,) = harasses.end({1: harasser}, lambda unit_id: 100, world, ctx)

    assert ended.reason == reason
