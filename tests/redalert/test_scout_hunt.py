"""The stray-scout hunt: a slot scout attacks a lone enemy unit it clearly outfights, away from the enemy's base."""

from __future__ import annotations

import math

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
from ucla_agent.games.redalert import scout_hunt
from ucla_agent.games.redalert.scout_hunt import Hunts
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.world import EnemyRecord, Unit

HUNT = careful(scout_stray_hunt=True)
# Jeep 400 out in the field, a lone rifle 5 cells east of it, and an enemy power plant 30 cells from the jeep.
FIELD = (50, 30)
RIFLE_ID = 800
LONE_RIFLE = enemy(RIFLE_ID, "e1", 55, 30)
FAR_POWER = enemy(600, "powr", 80, 30)
HUNT_ORDER = {"cmd": "attack", "units": [JEEP], "target": RIFLE_ID}
START = 1010


def scene(tick: int, *units: dict, sighted: tuple[dict, ...] = (LONE_RIFLE,), buildings=(FAR_POWER,)) -> dict:
    """The Allied base of the scenes with jeep 401 at home, the units given and the enemies in sight."""
    return allied(
        *units, other_jeep(), tick=tick, visible_enemies=list(sighted), visible_enemy_buildings=list(buildings)
    )


def hunt_events(events: list[dict], kind: str = "hunt", unit_id: int = JEEP) -> list[dict]:
    return [event for event in slot_events(events, kind) if event["unit_id"] == unit_id]


def test_jeep_hunts_lone_rifle_far_from_base(match_record):
    agent = jeep_agent(HUNT)

    orders = ask(agent, scene(START, jeep(FIELD)))

    assert attacks(orders) == [HUNT_ORDER] and moves(orders) == []
    events = match_record("scout_slot")
    (hunt,) = hunt_events(events)
    assert (hunt["target_id"], hunt["target_type"], hunt["cell"]) == (RIFLE_ID, "e1", list(FIELD))
    assert [event for event in slot_events(events, "retreat") if event["unit_id"] == JEEP] == []


def rifle_scouts(tuning: Tuning):
    """An agent whose two slots rifles 300 and 301 took by the barracks."""
    agent = new_agent(ScoutingManager(), tuning=tuning)
    ask(agent, allied(*[situations.unit(unit_id, "e1", 18, 46) for unit_id in (300, 301)], tick=1000))
    return agent


def dog_scouts(tuning: Tuning):
    """An agent whose two slots dogs 500 and 501 took by the kennel."""
    agent = new_agent(ScoutingManager(), tuning=tuning)
    ask(agent, soviet(*[situations.unit(unit_id, "dog", 14, 46) for unit_id in (500, 501)], tick=1000))
    return agent


def losing_scene(case: str) -> tuple[list[dict], int]:
    """A hunting agent's scout 5 cells from a lone enemy it does not clearly outfight, in the field: the orders and
    the scout's id."""
    if case == "jeep vs jeep":
        agent = jeep_agent(HUNT)
        return ask(agent, scene(START, jeep(FIELD), sighted=(enemy(RIFLE_ID, "jeep", 55, 30),))), JEEP
    if case == "dog vs jeep":
        agent = dog_scouts(HUNT)
        sighted = [enemy(RIFLE_ID, "jeep", 55, 30)]
        dog = situations.unit(500, "dog", *FIELD, idle=False)
        return ask(agent, soviet(dog, situations.unit(501, "dog", 14, 46), tick=START, visible_enemies=sighted)), 500
    agent = rifle_scouts(HUNT)
    sighted = [enemy(RIFLE_ID, case.split(" vs ")[1], 55, 30)]
    rifle = situations.unit(300, "e1", *FIELD, idle=False)
    return ask(agent, allied(rifle, situations.unit(301, "e1", 18, 46), tick=START, visible_enemies=sighted)), 300


@pytest.mark.parametrize("case", ["jeep vs jeep", "e1 vs e1", "e1 vs dog", "dog vs jeep"])
def test_no_hunt_for_equal_or_losing_matchups(case, match_record):
    orders, scout_id = losing_scene(case)

    events = match_record("scout_slot")
    assert attacks(orders, scout_id) == [] and slot_events(events, "hunt") == []
    (retreat,) = [event for event in slot_events(events, "retreat") if event["unit_id"] == scout_id]
    assert retreat["cause"] == "outclassed" and moves(orders, scout_id) == [retreat["to"]]


@pytest.mark.parametrize(
    "buildings",
    [(enemy(601, "powr", 65, 30),), (enemy(602, "pbox", 64, 30),)],
    ids=["rifle 10 cells from a power plant", "rifle 9 cells from a pillbox"],
)
def test_no_hunt_near_enemy_base_or_defense(buildings, match_record):
    agent = jeep_agent(HUNT)

    orders = ask(agent, scene(START, jeep(FIELD), buildings=buildings))

    assert attacks(orders) == [] and slot_events(match_record("scout_slot"), "hunt") == []


def test_a_hunt_ends_base_reach_when_the_rifle_walks_within_12_cells_of_a_building(match_record):
    agent = jeep_agent(HUNT)
    ask(agent, scene(START, jeep(FIELD)))

    walked_in = ask(agent, scene(START + 5, jeep(FIELD), sighted=(enemy(RIFLE_ID, "e1", 70, 30),)))

    events = match_record("scout_slot")
    assert [event["reason"] for event in hunt_events(events, "hunt_end")] == ["base_reach"]
    assert attacks(walked_in) == [] and hunt_events(events, "retreat") == []


def test_no_hunt_when_target_not_lone(match_record):
    agent = jeep_agent(HUNT)

    orders = ask(agent, scene(START, jeep(FIELD), sighted=(LONE_RIFLE, enemy(801, "e1", 55, 38))))

    assert attacks(orders) == [] and slot_events(match_record("scout_slot"), "hunt") == []


def test_rush_scout_never_hunts_before_watch_ends(match_record):
    rush = careful(scout_stray_hunt=True, rush_detection=True, rush_scout_direct=True)
    agent = jeep_agent(rush)
    # A second rifle by jeep 401 at home, which jeep 401 hunts: the rifle by the post is not lone to it.
    post, rifle = (61, 48), (enemy(RIFLE_ID, "e1", 56, 48), enemy(801, "e1", 31, 48))

    watching = ask(agent, scene(at_seconds(200), jeep(post, idle=True), sighted=rifle))
    after = ask(agent, scene(at_seconds(310), jeep(post, idle=True), sighted=rifle))

    assert agent.ctx.scout_destinations and attacks(watching) == []
    assert attacks(after) == [HUNT_ORDER]
    assert [event["cell"] for event in hunt_events(match_record("scout_slot"))] == [list(post)]


def test_a_hunt_ends_killed_when_the_target_is_gone_and_the_scout_is_steered_again(match_record):
    agent = jeep_agent(HUNT)
    ask(agent, scene(START, jeep(FIELD)))

    gone = ask(agent, scene(START + 5, jeep(FIELD), sighted=()))

    events = match_record("scout_slot")
    assert [event["reason"] for event in hunt_events(events, "hunt_end")] == ["killed"]
    assert attacks(gone) == [] and moves(gone) != [] and hunt_events(events, "retreat") == []


def test_a_hunt_ends_timeout_after_20_s_and_the_rifle_is_not_hunted_again_for_30_s(match_record):
    agent = jeep_agent(HUNT)
    ask(agent, scene(START, jeep(FIELD)))

    during = [ask(agent, scene(tick, jeep(FIELD))) for tick in range(START + 25, START + 500, 25)]
    over = ask(agent, scene(START + 500, jeep(FIELD)))
    spared = ask(agent, scene(START + 500 + 25 * 29, jeep(FIELD)))
    again = ask(agent, scene(START + 500 + 25 * 30, jeep(FIELD)))

    events = match_record("scout_slot")
    assert [event["reason"] for event in hunt_events(events, "hunt_end")] == ["timeout"]
    assert all(moves(orders) == [] for orders in during)
    assert attacks(over) == [] and moves(over) != [] and attacks(spared) == []
    assert attacks(again) == [HUNT_ORDER] and len(hunt_events(events)) == 2


def test_a_hunt_ends_hurt_after_a_30_point_loss_with_a_retreat_and_no_bait_shot(match_record):
    agent = jeep_agent(careful(scout_stray_hunt=True, scout_bait_shot=True))
    near = (enemy(RIFLE_ID, "e1", 53, 30),)
    ask(agent, scene(START, jeep(FIELD), sighted=near))
    ask(agent, scene(START + 5, jeep(FIELD, hp=80), sighted=near))

    hurt = ask(agent, scene(START + 10, jeep(FIELD, hp=70), sighted=near))

    events = match_record("scout_slot")
    assert [event["reason"] for event in hunt_events(events, "hunt_end")] == ["hurt"]
    (retreat,) = hunt_events(events, "retreat")
    assert (retreat["cause"], retreat["bait"], retreat["hp"]) == ("hit", None, 70)
    assert attacks(hurt) == [] and moves(hurt) == [retreat["to"]]


def test_switch_off_no_hunt(match_record):
    agent = jeep_agent(CAUTIOUS)

    orders = ask(agent, scene(START, jeep(FIELD)))

    assert attacks(orders) == [] and slot_events(match_record("scout_slot"), "hunt") == []


# -- the parts -------------------------------------------------------------------------------------------------------


def a_world():
    agent = jeep_agent(HUNT)
    return agent.world, agent.ctx


def scout(unit_type: str, cell: tuple[int, int] = FIELD, hp: int = 100) -> Unit:
    return Unit(id=JEEP, type=unit_type, x=cell[0], y=cell[1], hp=hp, idle=False, activity="move", stance="hold_fire")


def record(enemy_id: int, enemy_type: str, cell: tuple[int, int], *, building: bool = False, hp: int = 100):
    return EnemyRecord(enemy_id, enemy_type, cell[0], cell[1], hp, building, last_seen_tick=START)


@pytest.mark.parametrize(
    "attacker, target, seconds",
    [
        ("jeep", "e1", 4 / 3),
        ("e1", "jeep", 30.0),
        ("jeep", "jeep", 20.0),
        ("e1", "e6", 4 / 3),
        ("dog", "jeep", math.inf),
    ],
)
def test_seconds_to_kill_is_hit_points_by_health_over_damage_a_second(attacker, target, seconds):
    world, _ = a_world()

    assert scout_hunt.seconds_to_kill(attacker, target, 100, world) == pytest.approx(seconds)
    assert scout_hunt.seconds_to_kill(attacker, "tecn", 100, world) == math.inf


@pytest.mark.parametrize(
    "scout_type, enemy_type, wins",
    [
        ("jeep", "e1", True),
        ("jeep", "dog", True),
        ("jeep", "e3", True),
        ("jeep", "jeep", False),
        ("jeep", "ftrk", False),
        ("e1", "e1", False),
        ("e1", "dog", False),
        ("e1", "e3", True),
        ("e1", "e6", True),
        ("e1", "spy", True),
        ("e1", "medi", True),
        ("dog", "e1", True),
        ("dog", "jeep", False),
        ("ftrk", "jeep", True),
        ("jeep", "harv", False),
    ],
)
def test_wins_matchup_at_full_health(scout_type, enemy_type, wins):
    world, ctx = a_world()

    assert (
        scout_hunt.wins_matchup(scout(scout_type), record(1, enemy_type, (55, 30)), world, ctx.tuning.scouting) is wins
    )


def test_a_jeep_at_5_percent_no_longer_wins_against_a_rifle():
    world, ctx = a_world()

    assert not scout_hunt.wins_matchup(scout("jeep", hp=5), record(1, "e1", (55, 30)), world, ctx.tuning.scouting)


def test_is_lone_counts_other_fighting_units_within_10_cells_of_the_target_or_the_scout():
    world, ctx = a_world()
    target, tuning = record(1, "e1", (55, 30)), ctx.tuning.scouting
    world.enemy_memory = {1: target, 2: record(2, "e6", (55, 33)), 3: record(3, "e1", (39, 30))}

    assert scout_hunt.is_lone(target, scout("jeep"), world, tuning)
    world.enemy_memory[4] = record(4, "e1", (41, 30))
    assert not scout_hunt.is_lone(target, scout("jeep"), world, tuning)


def test_is_away_from_base_keeps_12_cells_from_a_building_and_a_pillbox_s_cover_and_4_more():
    world, ctx = a_world()
    world.enemy_memory = {1: record(1, "powr", (70, 30), building=True), 2: record(2, "sbag", (50, 40), building=True)}

    assert scout_hunt.is_away_from_base((57, 30), world, ctx) and not scout_hunt.is_away_from_base((58, 30), world, ctx)
    assert scout_hunt.is_away_from_base((50, 39), world, ctx)


def test_is_huntable_leaves_out_buildings_aircraft_trucks_mcvs_and_unseen_units():
    world, _ = a_world()
    unseen = record(6, "e6", (50, 33))
    unseen.visible = False
    shown = [
        record(1, "e6", (53, 30)),
        record(2, "powr", (60, 30), building=True),
        record(3, "heli", (51, 30)),
        record(4, "harv", (52, 30)),
        record(5, "mcv", (52, 31)),
        unseen,
    ]

    assert [scout_hunt.is_huntable(found, world) for found in shown] == [True, False, False, False, False, False]


def test_targets_are_the_lone_units_the_scout_outfights_away_from_the_base_nearest_first_ties_by_the_lower_id():
    world, ctx = a_world()
    world.enemy_memory = {
        3: record(3, "spy", (56, 30)),
        2: record(2, "e6", (53, 30)),
        1: record(1, "e6", (50, 33)),
        4: record(4, "jeep", (90, 90)),
    }

    assert [found.id for found in scout_hunt.targets(scout("jeep"), world, ctx)] == [1, 2, 3]
    # A rifle near them: they are not lone any more, and the rifle is the one lone unit left.
    world.enemy_memory[5] = record(5, "e1", (59, 30))
    assert [found.id for found in scout_hunt.targets(scout("jeep"), world, ctx)] == [5]


def test_hunts_start_due_ordered_and_end_with_the_reason():
    world, ctx = a_world()
    world.enemy_memory = {1: record(1, "e1", (55, 30))}
    hunts, hunter = Hunts(), scout("jeep")

    hunt = hunts.start(hunter, world, ctx)
    assert hunt.target_id == 1 and hunts.hunting == {JEEP} and hunts.due(hunter, world).id == 1
    hunts.ordered(JEEP, world.tick)
    assert hunts.due(hunter, world) is None
    world.enemy_memory[1].visible = False
    (ended,) = hunts.end({1: hunter}, world, ctx)
    assert (ended.slot, ended.reason, hunts.hunting) == (1, "killed", frozenset())
