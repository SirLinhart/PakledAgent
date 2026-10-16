"""The staging point kept clear of remembered enemies, and the gathering moves to it that fight back."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import situations
from situations import ask, base_state
from ucla_agent.games.redalert import cells, combat, safe_staging
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning

ALLIED_BASE = (("fact", 22, 48), ("powr", 19, 52), ("tent", 18, 44), ("proc", 26, 52))
SHIPPED = Tuning()
OFF = dataclasses.replace(
    SHIPPED,
    combat=dataclasses.replace(SHIPPED.combat, staging_clear_of_enemies=False, gather_attack_move=False),
)


def world_with(enemies=(), tuning: Tuning = SHIPPED, units=()):
    agent = situations.new_agent(tuning=tuning)
    ask(agent, base_state("england", *ALLIED_BASE, units=list(units), visible_enemies=list(enemies)))
    return agent.world


def camp(x: int, y: int, count: int = 4) -> list[dict]:
    return [{"id": 900 + n, "type": "e1", "x": x, "y": y + n % 2, "hp": 100} for n in range(count)]


def test_no_enemy_near_keeps_the_staging_point():
    world = world_with()

    assert safe_staging.staging_point(world, SHIPPED) == cells.staging_point(
        world, SHIPPED.upkeep.staging_distance_cells
    )


def test_a_camp_near_the_staging_point_moves_it_back_out_of_reach_and_the_margin():
    plain = cells.staging_point(world_with(), SHIPPED.upkeep.staging_distance_cells)
    world = world_with(camp(plain[0] + 3, plain[1]))

    point = safe_staging.staging_point(world, SHIPPED)

    # A rifleman reaches 5 cells; with the 4-cell margin no camp cell lies within 9 of the point.
    reach = world.items.damage.get("e1").range + SHIPPED.combat.staging_enemy_margin_cells
    assert point != plain and cells.distance(point, world.base_center) < cells.distance(plain, world.base_center)
    assert all(
        not cells.is_within(point, (x, y), reach) for x, y in [(plain[0] + 3, plain[1]), (plain[0] + 3, plain[1] + 1)]
    )


def test_switch_off_keeps_the_point_by_the_camp():
    plain = cells.staging_point(world_with(), SHIPPED.upkeep.staging_distance_cells)
    world = world_with(camp(plain[0] + 3, plain[1]), tuning=OFF)

    assert safe_staging.staging_point(world, OFF) == plain


def test_gathering_moves_are_attack_moves():
    world = world_with(units=[situations.unit(300, "e1", 10, 40)])
    units = list(world.combat_units.values())

    (on,) = combat._gather(units, (30, 48), SHIPPED)
    (off,) = combat._gather(units, (30, 48), OFF)

    assert on.order["cmd"] == "attack_move" and off.order["cmd"] == "move"


def test_tuning_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).combat, CombatTuning()):
        assert tuning.staging_clear_of_enemies and tuning.gather_attack_move
        assert tuning.staging_enemy_margin_cells == 4


# -- behind our defenses -------------------------------------------------------------------------------------------


def holding_ctx(world, plan: str = "HOLD"):
    """What `behind_defenses` reads of a match context: the tuning, whether an attack is out and the plan."""
    return SimpleNamespace(tuning=SHIPPED, attack_out=False, plan=plan)


def test_a_point_out_ahead_of_our_front_defense_is_held_4_cells_behind_it():
    agent = situations.new_agent(tuning=SHIPPED)
    ask(agent, base_state("england", *ALLIED_BASE, ("pbox", 28, 48)))
    world = agent.world

    held = safe_staging.behind_defenses((40, 48), world, holding_ctx(world))
    attacking = safe_staging.behind_defenses((40, 48), world, holding_ctx(world, plan="PRESS"))

    assert held == (24, 48) and attacking == (40, 48)


def test_a_point_behind_and_near_our_defenses_stays_and_with_none_any_point_stays():
    agent = situations.new_agent(tuning=SHIPPED)
    ask(agent, base_state("england", *ALLIED_BASE, ("pbox", 30, 48)))
    bare = world_with()

    assert safe_staging.behind_defenses((26, 48), agent.world, holding_ctx(agent.world)) == (26, 48)
    assert safe_staging.behind_defenses((40, 48), bare, holding_ctx(bare)) == (40, 48)
