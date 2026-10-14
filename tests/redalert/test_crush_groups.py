"""Crush runs of committed groups: the cluster's full planned count of tanks going together, never fewer than
crush_min_tanks, more of them against clusters of rocket and shock troopers, an advantage around the cluster, and the
trade of each run recorded."""

from __future__ import annotations

import pytest

from situations import ask, base_state, event, new_agent
from ucla_agent.games.redalert import crush
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import Tuning

# The Allied base of the fixture map, its construction yard at the center of the base.
YARD = ("fact", 22, 48)
# Our tanks stand 8 cells west of the enemy infantry, which bunch within 3 cells of (49, 48).
TANK_CELLS = ((40, 48), (41, 48), (40, 49), (41, 49), (40, 47))
# The last two make twelve, all within 3 cells of the centroid of the twelve.
INFANTRY_CELLS = (
    *((48, 47), (49, 47), (50, 47), (48, 48), (49, 48), (50, 48), (48, 49), (49, 49), (50, 49), (49, 50)),
    *((49, 46), (47, 48)),
)


def own(unit_id: int, unit_type: str, x: int, y: int) -> dict:
    """One own unit as an observation lists it, idle."""
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": 100, "act": None, "idle": True}


def foe(enemy_id: int, enemy_type: str, x: int, y: int) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


def heavy(count: int) -> list[dict]:
    """`count` heavy tanks of ours, 300 on, on TANK_CELLS: 1,150 each in a fight."""
    return [own(300 + number, "3tnk", x, y) for number, (x, y) in enumerate(TANK_CELLS[:count])]


def infantry(*types: str) -> list[dict]:
    """Enemy infantry of these types, 500 on, on INFANTRY_CELLS."""
    return [foe(500 + number, kind, x, y) for number, (kind, (x, y)) in enumerate(zip(types, INFANTRY_CELLS))]


def state(tick: int, units=(), enemies=(), **observation) -> dict:
    """A state of the Allied base at that tick with these own units and these enemies in sight."""
    shown = {"units": list(units), "visible_enemies": list(enemies), "visible_enemy_buildings": []}
    return base_state("england", YARD, tick=tick, **shown, **observation)


def runs(orders: list[dict]) -> dict[int, list[int]]:
    """The run moves of a cycle: each unit moved, with the cell it moves to."""
    return {unit_id: order["to"] for order in orders if order["cmd"] == "move" for unit_id in order["units"]}


def combat_events(match_record, reason: str) -> list[dict]:
    """The `combat` events of a reason."""
    return [data for data in match_record("combat") if data["reason"] == reason]


def cluster_seen(*types: str, tanks: int = 2):
    """An agent with the combat manager after a state of `tanks` heavy tanks and enemy infantry of these types, and
    the largest cluster of that infantry."""
    agent = new_agent(CombatManager())
    ask(agent, state(1000, heavy(tanks), infantry(*types)))
    return agent, crush.largest_cluster(
        crush.infantry_in_sight(agent.world), agent.ctx.tuning.combat.crush_cluster_cells
    )


# -- the group --------------------------------------------------------------------------------------------------------


def test_crush_a_single_eligible_tank_next_to_8_infantry_starts_no_run_and_tells_too_few_tanks(match_record):
    agent = new_agent(CombatManager())

    orders = ask(agent, state(1000, heavy(1), infantry(*["e1"] * 8)))

    assert runs(orders) == {} and agent.ctx.crush_run == {}
    assert [data["gate"] for data in combat_events(match_record, "crush_skipped")] == ["too_few_tanks"]


def test_crush_two_eligible_tanks_next_to_4_rifles_start_one_run_with_both_in_the_same_cycle():
    agent = new_agent(CombatManager())

    orders = ask(agent, state(1000, heavy(2), infantry(*["e1"] * 4)))

    # Four rifles are planned min(3, ceil(4 / 3)) = 2 tanks: both go, ordered in this one cycle.
    assert set(runs(orders)) == {300, 301} and set(agent.ctx.crush_run) == {300, 301}


@pytest.mark.parametrize(
    "rockets, tanks, planned, gate",
    [(0, 2, 3, "short_group"), (0, 3, 3, None), (4, 4, 5, "short_group"), (4, 5, 5, None)],
    ids=["12 rifles, 2 tanks", "12 rifles, 3 tanks", "4 rockets of 12, 4 tanks", "4 rockets of 12, 5 tanks"],
)
def test_crush_12_infantry_start_a_run_only_with_the_full_planned_count_and_tell_short_group_below_it(
    match_record, rockets, tanks, planned, gate
):
    agent = new_agent(CombatManager())

    orders = ask(agent, state(1000, heavy(tanks), infantry(*["e3"] * rockets, *["e1"] * (12 - rockets))))

    # Rifles alone: min(3, ceil(12 / 3)) = 3; four rocket soldiers of 12, anti-tank-heavy: min(5, ceil(12 / 2)) = 5.
    cluster = crush.largest_cluster(crush.infantry_in_sight(agent.world), 3)
    sent = set() if gate else {300 + number for number in range(tanks)}
    assert len(cluster.infantry) == 12 and crush.planned(cluster, agent.ctx.tuning.combat) == planned
    assert set(runs(orders)) == sent and set(agent.ctx.crush_run) == sent
    assert [data["gate"] for data in combat_events(match_record, "crush_skipped")] == ([gate] if gate else [])


@pytest.mark.parametrize(
    "rockets, sent",
    [(4, 5), (3, 3), (0, 3)],
    ids=["4 of 10 rocket soldiers", "3 of 10, under 0.33", "rifles alone"],
)
def test_crush_five_tanks_next_to_10_infantry_commit_5_when_4_are_rocket_soldiers(rockets, sent):
    agent = new_agent(CombatManager())
    cluster = infantry(*["e3"] * rockets, *["e1"] * (10 - rockets))

    orders = ask(agent, state(1000, heavy(5), cluster))

    # Anti-tank-heavy: min(5, ceil(10 / 2)); otherwise min(3, ceil(10 / 3)).
    assert len(runs(orders)) == sent


def test_crush_three_rocket_soldiers_are_a_cluster_to_run_over_and_three_rifles_are_not():
    rockets_agent, rockets = cluster_seen("e3", "e3", "e3")
    rifles_agent, rifles = cluster_seen("e1", "e1", "e1")

    combat = rockets_agent.ctx.tuning.combat
    assert (crush.min_infantry(rockets, combat), crush.min_infantry(rifles, combat)) == (3, 4)
    assert set(rockets_agent.ctx.crush_run) == {300, 301} and rifles_agent.ctx.crush_run == {}


def test_crush_a_shock_trooper_counts_as_anti_tank_infantry():
    agent, cluster = cluster_seen("shok", "e1", "e1")

    assert crush.is_anti_tank_heavy(cluster, agent.ctx.tuning.combat)


def test_crush_with_a_local_ratio_of_0_9_no_run_starts_and_the_gate_is_ratio(match_record):
    agent = new_agent(CombatManager())
    # A heavy, a medium and a light tank, 2,700, against four rifles, two rocket soldiers, a heavy and a medium tank,
    # 3,000.
    units = [*heavy(1), own(301, "2tnk", 41, 48), own(302, "1tnk", 40, 49)]
    enemies = [*infantry("e1", "e1", "e1", "e1", "e3", "e3"), foe(520, "3tnk", 52, 46), foe(521, "2tnk", 52, 50)]

    orders = ask(agent, state(1000, units, enemies))

    cluster = crush.largest_cluster(crush.infantry_in_sight(agent.world), 3)
    assert crush.local_ratio(cluster.centroid, agent.world, agent.ctx.tuning) == pytest.approx(0.9)
    # main, losing there, goes back to the staging point: no tank runs through the cluster.
    assert agent.ctx.crush_run == {} and all(cell == [32, 48] for cell in runs(orders).values())
    assert [data["gate"] for data in combat_events(match_record, "crush_skipped")] == ["ratio"]


def test_crush_a_cluster_with_the_advantage_of_1_0_is_run_over():
    agent = new_agent(CombatManager())
    # The three heavy tanks, 3,450, against the 3,450 of a rifle, five rocket soldiers, a light and a heavy tank; the
    # six, anti-tank-heavy, are planned min(5, ceil(6 / 2)) = 3 tanks.
    enemies = [*infantry("e1", "e3", "e3", "e3", "e3", "e3"), foe(520, "1tnk", 52, 46), foe(521, "3tnk", 52, 50)]

    ask(agent, state(1000, heavy(3), enemies))

    cluster = crush.largest_cluster(crush.infantry_in_sight(agent.world), 3)
    assert crush.local_ratio(cluster.centroid, agent.world, agent.ctx.tuning) == pytest.approx(1.0)
    assert set(agent.ctx.crush_run) == {300, 301, 302}


# -- the record -----------------------------------------------------------------------------------------------------


def test_crush_the_run_s_end_event_carries_infantry_killed_its_cost_tanks_lost_and_the_trade(match_record):
    agent = new_agent(CombatManager())
    # Six rocket soldiers are planned min(5, ceil(6 / 2)) = 3 tanks.
    cells = runs(ask(agent, state(1000, heavy(3), infantry(*["e3"] * 6))))
    # Two rocket soldiers are run over as tank 301 is lost; then 300 and 302 get through.
    survivors = infantry(*["e3"] * 6)[2:]
    left = [tank for tank in heavy(3) if tank["id"] != 301]
    ask(agent, state(1005, left, survivors, events=[event(1, "enemy_killed", 1005, count=2)]))

    ask(agent, state(1010, [own(unit_id, "3tnk", *cells[unit_id]) for unit_id in (300, 302)], survivors))

    (run,) = combat_events(match_record, "crush_run")
    assert {key: run[key] for key in ("tanks", "infantry_killed", "infantry_killed_cost", "tanks_lost", "trade")} == {
        "tanks": [301, 300, 302],
        "infantry_killed": 2,
        "infantry_killed_cost": 600,
        "tanks_lost": 1,
        "trade": 600 - 1150,
    }
    assert run["ended"] == "arrived"


# -- the commander's run-over detachment ------------------------------------------------------------------------------


def test_crush_the_commander_s_run_over_detachment_keeps_its_own_size_with_no_minimum():
    agent, cluster = cluster_seen("e1", "e1", "e1", "e1", tanks=1)
    combat, tanks = agent.ctx.tuning.combat, list(agent.world.units.values())

    detachment = crush.run_cells(cluster, tanks, agent.world, Terrain(), combat)

    # The detachment's run_cells with no count names the one tank; the script's run needs its committed count, 2.
    assert list(detachment) == [300] and crush.committed(cluster, combat) == 2 and agent.ctx.crush_run == {}


def test_crush_the_shipped_keys_need_2_tanks_a_ratio_of_1_and_take_1_tank_per_2_anti_tank_infantry_up_to_5():
    combat = Tuning().combat

    keys = (combat.crush_min_tanks, combat.crush_min_ratio, combat.crush_at_per_tanks, combat.crush_at_max_tanks)

    assert keys == (2, 1.0, 2, 5) and (combat.crush_at_share, combat.crush_at_min_infantry) == (0.33, 3)
