"""main's failed attacks remembered, and the gate on an attack's objective (see `failed_attack`)."""

from __future__ import annotations

import dataclasses

import pytest

import situations
from situations import ask, base_state
from switches_off import new_agent
from ucla_agent.games.redalert import failed_attack, plans
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.composition import PRESS
from ucla_agent.games.redalert.failed_attack import FAILED_HERE, OBJECTIVE_DEFENDED, AttackMemory, FailedAttack
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning

YARD, POWER = ("fact", 22, 48), ("powr", 19, 52)
OBJECTIVE = (70, 48)
# Four heavy tanks 10 cells short of the objective, worth 4,600 whole.
TANKS = [situations.unit(400 + n, "3tnk", 58 + n % 2, 47 + n // 2) for n in range(4)]
# Four enemy pillboxes and two rocket soldiers at the objective.
BOXES = [{"id": 600 + n, "type": "pbox", "x": 70, "y": 45 + 2 * n, "hp": 100} for n in range(4)]
ROCKETS = [{"id": 620 + n, "type": "e3", "x": 69, "y": 48 + n, "hp": 100} for n in range(2)]
# One pillbox covering an enemy power plant, and five heavy tanks beside them: main's four can take the pillbox alone.
COVERED_PLANT = [
    {"id": 600, "type": "pbox", "x": 70, "y": 47, "hp": 100},
    {"id": 610, "type": "powr", "x": 71, "y": 49, "hp": 100},
]
GUARDING_TANKS = [{"id": 630 + n, "type": "3tnk", "x": 72, "y": 46 + n, "hp": 100} for n in range(5)]


def scene(units=TANKS, *, tick: int = 1000, tuning: Tuning | None = None, **observation):
    """An agent with the combat manager that has seen a state of these units, and its world and context."""
    agent = new_agent(CombatManager(), tuning=tuning or Tuning())
    ask(agent, base_state("england", YARD, POWER, tick=tick, units=list(units), **observation))
    return agent


def damaged(hp: int) -> list[dict]:
    return [{**tank, "hp": hp} for tank in TANKS]


def failed_once(match_record=None, *, pulled: bool = False, hp: int = 40) -> tuple[AttackMemory, object]:
    """A memory that watched an attack on OBJECTIVE fail: main whole in a first cycle, then at `hp`, or pulled."""
    memory = AttackMemory()
    agent = scene(visible_enemy_buildings=BOXES, visible_enemies=ROCKETS)
    world, ctx = agent.world, agent.ctx
    memory.follow(list(world.combat_units.values()), OBJECTIVE, failed_attack.DIRECT, False, world, ctx)
    later = scene(damaged(hp), tick=1005, visible_enemy_buildings=BOXES, visible_enemies=ROCKETS)
    memory.follow(list(later.world.combat_units.values()), OBJECTIVE, failed_attack.DIRECT, pulled, later.world, ctx)
    return memory, later


def test_an_attack_that_loses_more_than_half_of_main_near_its_objective_is_remembered_once(match_record):
    memory, _ = failed_once(hp=40)

    (failure,) = memory.failed
    assert (failure.cell, round(failure.force), failure.side, failure.defenses) == (
        OBJECTIVE,
        4600,
        "direct",
        frozenset({600, 601, 602, 603}),
    )
    assert failure.enemy > 0
    (event,) = [event for event in match_record("combat") if event["reason"] == "attack_failed"]
    assert (event["force"], event["left"], event["pulled"]) == (4600, 1840, False)


@pytest.mark.parametrize("hp, pulled, remembered", [(60, False, False), (60, True, True), (50, False, True)])
def test_a_failure_is_main_pulled_back_or_down_to_half_its_peak(hp, pulled, remembered):
    memory, _ = failed_once(pulled=pulled, hp=hp)

    assert bool(memory.failed) is remembered


def test_main_never_within_20_cells_of_its_objective_fails_no_attack():
    memory = AttackMemory()
    agent = scene()
    far = (90, 48)
    for hp in (100, 30):
        units = [dataclasses.replace(unit, hp=hp) for unit in agent.world.combat_units.values()]
        memory.follow(units, far, failed_attack.DIRECT, True, agent.world, agent.ctx)

    assert memory.failed == ()


def failure(force: float = 4600, enemy: float = 1000, side: str = "direct", tick: int = 1000) -> FailedAttack:
    return FailedAttack(OBJECTIVE, force, enemy, side, tick, frozenset())


@pytest.mark.parametrize(
    "cell, force, enemy, side, why",
    [
        ((75, 50), 4600, 1000, "direct", FAILED_HERE),
        ((86, 48), 4600, 1000, "direct", None),
        ((75, 50), 3000, 1000, "direct", None),
        ((75, 50), 1000, 3600, "direct", FAILED_HERE),
        ((75, 50), 4600, 1000, "flank", None),
    ],
    ids=["same force within 15 cells", "16 cells off", "1.5x the failed force", "short of 1.3x the enemy", "a flank"],
)
def test_an_attack_near_a_failure_is_blocked_until_main_is_1_5x_the_force_and_1_3x_the_enemy_or_flanks(
    cell, force, enemy, side, why
):
    agent = scene()
    agent.ctx.failed_attacks = (failure(force, enemy),)
    units = list(agent.world.combat_units.values())

    blocked = failed_attack.blocking(cell, units, side, agent.world, agent.ctx, weighs_objective=False)

    assert (None if blocked is None else blocked.reason) == why


def test_with_the_switch_off_no_attack_is_blocked():
    off = dataclasses.replace(Tuning(), combat=dataclasses.replace(CombatTuning(), failed_attack_memory=False))
    agent = scene(tuning=off)
    agent.ctx.failed_attacks = (failure(),)

    units = list(agent.world.combat_units.values())
    assert failed_attack.blocking(OBJECTIVE, units, "direct", agent.world, agent.ctx) is None


def test_a_failure_is_forgotten_after_240_s_or_once_its_defenses_are_gone():
    agent = scene(visible_enemy_buildings=BOXES)
    world, ctx = agent.world, agent.ctx
    memory = AttackMemory()
    memory.failed = (
        FailedAttack(OBJECTIVE, 4600, 1000, "direct", world.tick - 240 * 25, frozenset()),
        FailedAttack(OBJECTIVE, 4600, 1000, "direct", world.tick, frozenset({600})),
        FailedAttack(OBJECTIVE, 4600, 1000, "direct", world.tick, frozenset({699})),
    )

    memory.follow([], None, "direct", False, world, ctx)

    assert [failure.defenses for failure in memory.failed] == [frozenset({600})]
    assert ctx.failed_attacks == memory.failed


def test_an_objective_under_four_pillboxes_weighs_under_1_2_for_main_and_is_blocked(match_record):
    agent = scene(visible_enemy_buildings=BOXES, visible_enemies=ROCKETS)
    units = list(agent.world.combat_units.values())

    blocked = failed_attack.blocking(OBJECTIVE, units, "direct", agent.world, agent.ctx)

    ratio = failed_attack.objective_ratio(OBJECTIVE, units, agent.world, agent.ctx)
    assert ratio is not None and ratio < 1.2
    assert blocked is not None and (blocked.reason, round(blocked.ratio, 2)) == (OBJECTIVE_DEFENDED, round(ratio, 2))
    assert failed_attack.blocking(OBJECTIVE, units, "direct", agent.world, agent.ctx, weighs_objective=False) is None


def test_the_combat_manager_publishes_press_blocked_and_the_plans_shut_press_on_it(match_record):
    agent = scene(visible_enemy_buildings=COVERED_PLANT, visible_enemies=GUARDING_TANKS)
    # The combat manager weighed main's objective, the covered plant, in the cycle above.
    assert agent.ctx.press_blocked is True
    blocked = [event for event in match_record("combat") if event["reason"] == "attack_blocked_failed"]
    assert [(event["user"], event["why"]) for event in blocked] == [(PRESS, OBJECTIVE_DEFENDED)]

    seen = dataclasses.replace(press_situation(), press_blocked=True)
    assert PRESS not in plans.open_plans(seen, Tuning().plan_rules, True)
    assert plans.ATTACK_BLOCKED_FAILED in plans.press_blockers(seen, Tuning().plan_rules, True)
    assert PRESS in plans.open_plans(dataclasses.replace(seen, press_blocked=False), Tuning().plan_rules, True)


def press_situation() -> plans.Situation:
    """A hand-built situation at 600 s in which PRESS's own rules open it: an army of 9,000 against 1,000."""
    return plans.Situation(
        tick=15_000,
        game_seconds=600.0,
        remaining_seconds=600.0,
        trade_balance=0,
        army_value=9000,
        main_strength=9000,
        enemy_combat_value=1000,
        enemy_units=4,
        enemy_buildings=3,
        enemy_defenses=2,
        defense_cost=1200,
        base_seen=True,
        has_war_factory=True,
        can_raid=False,
        raid_target_known=False,
        enemy_unseen_ticks=0,
        scout_active=False,
        enemy_army_known=True,
        ratio=9.0,
        combat_army_value=9000,
        enemy_committed=True,
    )
