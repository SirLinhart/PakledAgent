"""The bait shot: a slot scout whose hit or outclassed retreat begins fires once at an attacker in range, then runs."""

from __future__ import annotations

import pytest

import situations
from scout_scenes import (
    CAUTIOUS,
    JEEP,
    JEEPS_TICK,
    allied,
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
from ucla_agent.games.redalert import cells, scout_bait
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.groups import SCOUT
from ucla_agent.games.redalert.scout_attacks import ScoutAttacks, attack_move, scout_order
from ucla_agent.games.redalert.scout_bait import BaitShots
from ucla_agent.games.redalert.scout_stance import without_scout_attacks
from ucla_agent.games.redalert.scout_steering import FLEE_URGENCY, Move
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.world import Unit

BAIT = careful(scout_bait_shot=True)
# Where jeep 400 stands when it is hit, out in the field, and a rifle 3 cells east of it, within the jeep's 4 cells.
HIT_AT = (50, 30)
RIFLE_ID = 800
NEAR_RIFLE = enemy(RIFLE_ID, "e1", 53, 30)
BAIT_ORDER = {"cmd": "attack", "units": [JEEP], "target": RIFLE_ID}
# The cycles come every 5 ticks; the hit is at HIT_TICK.
CYCLE_TICKS = 5
HIT_TICK = JEEPS_TICK + 5


def hit_jeep(agent, tick: int = HIT_TICK, sighted: tuple[dict, ...] = (NEAR_RIFLE,), **observation) -> list[dict]:
    """Jeep 400 shown at HIT_AT at full health, then hit to 98 a cycle later with the enemies `sighted` in sight; the
    orders of the hit state."""
    ask(agent, scene(tick - CYCLE_TICKS, jeep(HIT_AT)))
    return ask(agent, scene(tick, jeep(HIT_AT, hp=98), visible_enemies=list(sighted), **observation))


def scene(tick: int, *units: dict, **observation) -> dict:
    """The Allied base of the scenes with jeep 401 waiting at home and the units given."""
    return allied(*units, other_jeep(), tick=tick, **observation)


def test_hit_scout_attacks_in_range_rifle_once_then_retreats(match_record):
    agent = jeep_agent(BAIT)

    hit = hit_jeep(agent)
    held = [
        ask(agent, scene(tick, jeep(HIT_AT, hp=98), visible_enemies=[NEAR_RIFLE]))
        for tick in range(HIT_TICK + CYCLE_TICKS, HIT_TICK + 25, CYCLE_TICKS)
    ]
    after = [
        ask(agent, scene(tick, jeep(HIT_AT, hp=98), visible_enemies=[NEAR_RIFLE]))
        for tick in range(HIT_TICK + 25, HIT_TICK + 25 + 10 * CYCLE_TICKS, CYCLE_TICKS)
    ]

    assert attacks(hit) == [BAIT_ORDER] and moves(hit) == []
    assert all(attacks(orders) == [] and moves(orders) == [] for orders in held)
    (retreat,) = slot_events(match_record("scout_slot"), "retreat")
    assert (retreat["cause"], retreat["bait"]) == ("hit", [RIFLE_ID, "e1"])
    assert moves(after[0]) == [retreat["to"]]
    assert cells.distance(tuple(retreat["to"]), (53, 30)) > cells.distance(HIT_AT, (53, 30))
    assert all(attacks(orders) == [] for orders in after)


@pytest.mark.parametrize(
    "sighted, observation",
    [
        ((enemy(RIFLE_ID, "e1", 56, 30),), {}),
        ((), {"visible_enemy_buildings": [enemy(600, "pbox", 53, 30)]}),
    ],
    ids=["rifle 6 cells away", "a pillbox"],
)
def test_no_bait_when_attacker_out_of_range_or_a_defense(sighted, observation, match_record):
    agent = jeep_agent(BAIT)

    hit = hit_jeep(agent, sighted=sighted, **observation)

    (retreat,) = slot_events(match_record("scout_slot"), "retreat")
    assert attacks(hit) == [] and retreat["bait"] is None
    assert moves(hit) == [retreat["to"]]


def test_dog_does_not_bait_a_jeep(match_record):
    agent = new_agent(ScoutingManager(), tuning=BAIT)
    dogs = [situations.unit(500, "dog", 50, 30, idle=False), situations.unit(501, "dog", 18, 46)]
    ask(agent, soviet(*dogs, tick=1000))

    hit = ask(
        agent,
        soviet({**dogs[0], "hp": 98}, dogs[1], tick=1005, visible_enemies=[enemy(RIFLE_ID, "jeep", 51, 30)]),
    )

    assert agent.ctx.groups.members(SCOUT) == [500, 501]
    (retreat,) = slot_events(match_record("scout_slot"), "retreat")
    assert attacks(hit, 500) == [] and retreat["bait"] is None and moves(hit, 500) == [retreat["to"]]


def retreats_begun_apart(agent, seconds: int) -> list[dict]:
    """Jeep 400 hit at HIT_TICK by the rifle in range, standing clear of its retreat once that is over, and hit again
    `seconds` after the first hit with the rifle back in range: the orders of the second hit."""
    hit_jeep(agent)
    for tick in range(HIT_TICK + 25, HIT_TICK + seconds * 25, 25):
        ask(agent, scene(tick, jeep(HIT_AT, hp=98)))
    second = HIT_TICK + seconds * 25
    return ask(agent, scene(second, jeep(HIT_AT, hp=90), visible_enemies=[NEAR_RIFLE]))


@pytest.mark.parametrize("seconds, baited", [(10, False), (16, True)], ids=["10 s after", "16 s after"])
def test_bait_cooldown(seconds, baited, match_record):
    agent = jeep_agent(BAIT)

    second = retreats_begun_apart(agent, seconds)

    first, again = slot_events(match_record("scout_slot"), "retreat")
    assert first["bait"] == [RIFLE_ID, "e1"]
    assert (again["bait"] is not None, attacks(second) == [BAIT_ORDER]) == (baited, baited)


class Proposes:
    """A manager that proposes the same attack in every cycle, at CONTACT, as base defense might for a unit near an
    enemy; placed ahead of the scouting manager, as base defense is."""

    name = "base_defense"

    def __init__(self, order: dict) -> None:
        self._order = order

    def intents(self, world, ctx) -> list[Intent]:
        return [Intent(Priority.CONTACT, dict(self._order))]


def test_bait_attack_survives_scout_attack_strip():
    defense_attack = {"cmd": "attack", "units": [JEEP], "target": 801}
    agent = jeep_agent(BAIT, Proposes(defense_attack))

    hit = hit_jeep(agent, sighted=(NEAR_RIFLE, enemy(801, "e1", 60, 30)))

    assert attacks(hit) == [BAIT_ORDER]


def test_without_scout_attacks_keeps_only_the_scout_rules_attack_on_a_scout():
    agent = jeep_agent(BAIT)
    rule = Intent(Priority.SCOUT, BAIT_ORDER, scout_rule=True)
    other = Intent(Priority.CONTACT, {"cmd": "attack", "units": [JEEP, 999], "target": 801})

    kept = without_scout_attacks([rule, other], agent.ctx)

    assert kept == [rule, Intent(Priority.CONTACT, {"cmd": "attack", "units": [999], "target": 801})]


def test_switch_off_restores_silent_retreat(match_record):
    off, on = jeep_agent(CAUTIOUS), jeep_agent(careful(scout_bait_shot=False))

    silent = [hit_jeep(agent) for agent in (off, on)]

    retreats = slot_events(match_record("scout_slot"), "retreat")
    assert [attacks(orders) for orders in silent] == [[], []]
    assert [event["bait"] for event in retreats] == [None, None]
    assert moves(silent[0]) == moves(silent[1]) == [retreats[0]["to"]]


# -- the parts -------------------------------------------------------------------------------------------------------


def unit(unit_type: str, x: int, y: int, unit_id: int = JEEP) -> Unit:
    return Unit(id=unit_id, type=unit_type, x=x, y=y, hp=98, idle=False, activity="move", stance="hold_fire")


def world_with(*sighted: dict, buildings: tuple[dict, ...] = ()):
    agent = jeep_agent(BAIT)
    shown = {"visible_enemies": list(sighted), "visible_enemy_buildings": list(buildings)}
    ask(agent, scene(HIT_TICK, jeep(HIT_AT), **shown))
    return agent.world, agent.ctx


def test_bait_target_is_the_nearest_hurtable_unit_in_range_ties_by_the_lower_id():
    world, _ = world_with(
        enemy(803, "e1", 53, 30), enemy(802, "e1", 50, 33), enemy(804, "e6", 51, 30), enemy(805, "e1", 52, 30)
    )

    assert scout_bait.bait_target(unit("jeep", *HIT_AT), world).id == 805
    world.enemy_memory.pop(805)
    assert scout_bait.bait_target(unit("jeep", *HIT_AT), world).id == 802


def test_bait_target_is_none_for_a_building_a_unit_out_of_sight_or_one_the_scout_cannot_hurt():
    world, _ = world_with(enemy(806, "jeep", 51, 30), buildings=(enemy(600, "pbox", 52, 30),))

    assert scout_bait.bait_target(unit("dog", *HIT_AT), world) is None
    world.enemy_memory[806].visible = False
    assert scout_bait.bait_target(unit("jeep", *HIT_AT), world) is None


def test_bait_shots_note_the_shot_hold_the_move_back_one_second_and_keep_the_cooldown():
    world, ctx = world_with(NEAR_RIFLE)
    shots, scout = BaitShots(), unit("jeep", *HIT_AT)

    assert shots.shoot(scout, world, ctx).id == RIFLE_ID
    assert shots.shot_now(JEEP, world).id == RIFLE_ID and shots.holds_back(JEEP, world, ctx)
    world.tick += 25
    assert shots.shot_now(JEEP, world) is None and not shots.holds_back(JEEP, world, ctx)
    world.tick += 14 * 25 - 1
    assert shots.shoot(scout, world, ctx) is None
    world.tick += 1
    assert shots.shoot(scout, world, ctx).id == RIFLE_ID


def test_scout_attacks_moves_give_the_attack_in_the_shot_s_cycle_and_nothing_while_held_back():
    world, ctx = world_with(NEAR_RIFLE)
    attacks_of_slots, scout = ScoutAttacks(), unit("jeep", *HIT_AT)
    other = unit("jeep", 28, 48, unit_id=401)

    attacks_of_slots.bait(scout, world, ctx)
    now = attacks_of_slots.moves({1: scout, 2: other}, world, ctx)
    world.tick += 5
    later = attacks_of_slots.moves({1: scout, 2: other}, world, ctx)

    assert now == {1: Move((53, 30), FLEE_URGENCY, target=RIFLE_ID)} and later == {1: None}


def test_scout_order_is_an_attack_marked_as_the_slots_own_or_a_move():
    world, _ = world_with(NEAR_RIFLE)

    attack = scout_order(JEEP, attack_move(world.enemy_memory[RIFLE_ID]))
    move = scout_order(JEEP, Move((40, 40), FLEE_URGENCY))

    assert attack == Intent(Priority.SCOUT, BAIT_ORDER, scout_rule=True)
    assert move == Intent(Priority.SCOUT, {"cmd": "move", "units": [JEEP], "to": [40, 40]})
