"""Artillery and V2: paired only in range, an infantry target valued by the infantry in its splash, enemy vehicles
(stopped first) while a crush run is on, a step back from an enemy come close in every fight, and held in place by
base defense."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import replace
from pathlib import Path

import pytest

import situations
from situations import REPO_ROOT, ask, base_state, fixture_state, tracked_items, with_command
from switches_off import new_agent, tuning_off
from ucla_agent.core.config import ConfigError
from ucla_agent.games.redalert import siege, targeting
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.command_army import hold_action
from ucla_agent.games.redalert.fight import centroid
from ucla_agent.games.redalert.tuning import TUNING_FILE, CombatTuning, Tuning
from ucla_agent.games.redalert.world import World

TICK = 100
# The fixture map's base center, which a fall-back steps toward.
BASE_CENTER = (22, 48)
# Four enemy rifles bunched within 1.5 cells of each other, 8 to 9 cells east of an artillery piece at (40, 48).
RIFLE_GROUP = [{"id": 510 + n, "type": "e1", "x": 47 + n % 2, "y": 44 + n // 2, "hp": 100} for n in range(4)]


def own(unit_id: int, unit_type: str, x: int, y: int, hp: int = 100) -> dict:
    """One own unit as an observation lists it."""
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": hp, "act": None, "idle": True}


def foe(enemy_id: int, enemy_type: str, x: int, y: int, hp: int = 100) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": hp}


def show(world: World, tick: int, units=(), enemies=()) -> World:
    """The world after one more state at `tick` showing exactly these own units and enemies."""
    world.update(fixture_state("opening_mcv", tick, units=list(units), visible_enemies=list(enemies)))
    return world


def seen(units=(), enemies=()) -> World:
    """A world after one state that shows exactly these own units and enemies."""
    return show(World(tracked_items()), TICK, units, enemies)


def assigned(world: World, tuning: Tuning | None = None, crushing: bool = False) -> dict[int, list[int]]:
    """The units the assignment gives each enemy around all of the world's own units, by enemy id."""
    tuning = tuning or Tuning()
    units = list(world.units.values())
    ranked = targeting.targets(units, centroid(units), world, tuning)
    given = targeting.assign(units, ranked, world, tuning, crushing=crushing)
    return {assignment.target.id: list(assignment.attackers) for assignment in given}


# -- splash ----------------------------------------------------------------------------------------


def test_artillery_with_a_lone_tank_and_a_group_of_4_rifles_both_in_range_picks_the_rifle_group():
    world = seen([own(300, "arty", 40, 48)], [foe(500, "2tnk", 48, 48), *RIFLE_GROUP])

    (target,) = assigned(world)

    assert world.enemy_memory[target].type == "e1" and target in {rifle["id"] for rifle in RIFLE_GROUP}


def test_artillery_a_rifle_in_a_group_is_worth_one_more_for_each_other_rifle_within_1_5_cells():
    # A lone rifle 7 cells off is nearer than the group 8 to 9 cells off; each rifle of the group has three others
    # within 1.5 cells (1.41 on the diagonal), so it is worth 4 times the lone one.
    world = seen([own(300, "arty", 40, 48)], [foe(500, "e1", 47, 50), *RIFLE_GROUP])
    no_splash = replace(Tuning(), combat=replace(CombatTuning(), arty_splash_cells=0.0))

    (grouped,) = assigned(world)

    assert grouped in {rifle["id"] for rifle in RIFLE_GROUP}
    assert assigned(world, no_splash) == {500: [300]}


def test_artillery_a_v2_takes_the_rifle_group_too_and_a_tank_s_value_has_no_splash():
    world = seen([own(300, "v2rl", 40, 48)], [foe(500, "e1", 47, 50), *RIFLE_GROUP])

    (grouped,) = assigned(world)

    assert grouped in {rifle["id"] for rifle in RIFLE_GROUP}


# -- the crush run ---------------------------------------------------------------------------------


def crush_world(moving_to: tuple[int, int]) -> World:
    """An artillery piece at (40, 48) against a medium tank standing at (48, 48) through two states, a medium tank that
    moves from (48, 50) to `moving_to` between them, and the rifle group, all in range."""
    world = seen([own(300, "arty", 40, 48)], [foe(500, "2tnk", 48, 48), foe(501, "2tnk", 48, 50), *RIFLE_GROUP])
    enemies = [foe(500, "2tnk", 48, 48), foe(501, "2tnk", *moving_to), *RIFLE_GROUP]
    return show(world, TICK + 5, [own(300, "arty", 40, 48)], enemies)


def test_artillery_with_a_crush_run_on_picks_a_stopped_enemy_tank_in_range_over_a_moving_one_and_over_the_infantry():
    world = crush_world((47, 50))

    assert world.enemy_memory[500].still and not world.enemy_memory[501].still
    assert assigned(world, crushing=True) == {500: [300]}
    assert set(assigned(world)) <= {rifle["id"] for rifle in RIFLE_GROUP}


def test_artillery_with_a_crush_run_on_takes_a_moving_tank_before_the_infantry():
    world = seen([own(300, "arty", 40, 48)], [foe(501, "2tnk", 48, 50), *RIFLE_GROUP])
    show(world, TICK + 5, [own(300, "arty", 40, 48)], [foe(501, "2tnk", 47, 50), *RIFLE_GROUP])

    assert assigned(world, crushing=True) == {501: [300]}


def test_artillery_an_enemy_seen_on_the_same_cell_in_two_states_in_a_row_is_still_and_one_seen_first_is_not():
    world = seen([], [foe(500, "2tnk", 48, 48)])
    first = world.enemy_memory[500].still
    show(world, TICK + 5, [], [foe(500, "2tnk", 48, 48), foe(501, "2tnk", 50, 50)])

    assert (first, world.enemy_memory[500].still, world.enemy_memory[501].still) == (False, True, False)


# -- range -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("unit_type, beyond", [("arty", 14), ("v2rl", 12)], ids=["arty 14 cells", "v2rl 12 cells"])
def test_artillery_a_target_beyond_the_weapon_range_is_not_paired_and_it_is_sent_after_no_enemy(unit_type, beyond):
    # Within the range and 4 cells, the reach of every other unit, but beyond the range of 12 (10 for the V2).
    world = seen([own(300, unit_type, 40, 48), own(301, "1tnk", 40, 49)], [foe(500, "e1", 40 + beyond, 48)])
    units = list(world.units.values())

    orders = targeting.FocusFire().orders(units, centroid(units), world, Tuning())

    assert assigned(seen([own(300, unit_type, 40, 48)], [foe(500, "e1", 40 + beyond, 48)])) == {}
    assert [intent.order for intent in orders if 300 in intent.order["units"]] == []


def test_artillery_a_target_just_within_the_weapon_range_is_paired():
    assert assigned(seen([own(300, "arty", 40, 48)], [foe(500, "e1", 52, 48)])) == {500: [300]}


# -- the fall-back ---------------------------------------------------------------------------------


@pytest.mark.parametrize("distance, steps", [(3, True), (6, True), (7, False)], ids=["3 cells", "6 cells", "7 cells"])
def test_artillery_siege_fall_backs_steps_an_artillery_piece_with_an_enemy_within_4_and_2_cells_4_cells_toward_base(
    distance, steps
):
    world = seen([own(300, "arty", 40, 48), own(301, "2tnk", 40, 49)], [foe(500, "3tnk", 40 + distance, 48)])
    enemies = list(world.enemy_memory.values())

    stepping = siege.fall_backs(world.units.values(), enemies, world, Tuning().siege)

    assert stepping == ({300: (36, 48)} if steps else {})
    assert world.base_center == BASE_CENTER


def under_hold() -> tuple[CombatManager, list, World]:
    """main under the commander's hold, its stop naming an artillery piece (300) with an enemy heavy tank 3 cells off,
    a medium tank (301) in range of it and a rifle (302) 13 cells from it; the manager, its orders and the world."""
    world = seen(
        [own(300, "arty", 40, 48), own(301, "2tnk", 41, 50), own(302, "e1", 30, 48)], [foe(500, "3tnk", 43, 48)]
    )
    manager, ctx = CombatManager(), situations.new_agent().ctx
    manager.command(hold_action())
    manager.commanded_order = {"cmd": "stop", "units": [300, 301, 302]}
    orders = manager._carry_out(hold_action(), list(world.units.values()), world, ctx)
    return manager, orders, world


def test_artillery_an_enemy_heavy_tank_3_cells_away_under_hold_makes_the_artillery_step_back_toward_base():
    _, orders, _ = under_hold()

    assert Intent(Priority.EMERGENCY, {"cmd": "move", "units": [300], "to": [36, 48]}) in orders
    assert not [intent for intent in orders if 300 in intent.order["units"] and intent.order["cmd"] != "move"]


def test_artillery_stepping_back_under_hold_is_left_out_of_the_stop_and_the_tank_fires():
    manager, orders, _ = under_hold()

    assert {"cmd": "attack", "units": [301], "target": 500} in [intent.order for intent in orders]
    assert manager.commanded_order == {"cmd": "stop", "units": [302]}


# -- base defense ----------------------------------------------------------------------------------

GUARD = [situations.unit(200 + number, "e1", 28, 46 + number) for number in range(5)]
ENEMY_RIFLES = [foe(500 + number, "e1", 32, 44 + number) for number in range(8)]
YARD = ("fact", 22, 48)


def defended(tuning: Tuning, *units: dict) -> list[dict]:
    """The orders of base defense alone against eight enemy rifles 10 cells from the yard, with the guard and these
    units of ours near the base."""
    agent = new_agent(BaseDefenseManager(), tuning=tuning)
    return ask(agent, base_state("england", YARD, units=[*GUARD, *units], visible_enemies=ENEMY_RIFLES))


def test_artillery_base_defense_s_attack_move_to_a_threat_excludes_the_artillery_and_the_v2():
    near = [
        situations.unit(300, "1tnk", 40, 48),
        situations.unit(301, "arty", 22, 30),
        situations.unit(302, "v2rl", 22, 32),
    ]

    orders = defended(tuning_off(), *near)

    (attack_move,) = with_command(orders, "attack_move")
    assert attack_move["units"] == [200, 201, 202, 203, 204, 300]
    assert [order for order in orders if {301, 302} & set(order.get("units", ()))] == []


def test_artillery_base_defense_s_focus_steps_an_artillery_piece_back_from_an_enemy_come_close():
    contact_on = tuning_off(combat=dataclasses.replace(tuning_off().combat, contact_focus=True))

    orders = defended(contact_on, situations.unit(301, "arty", 28, 44))

    # The nearest enemy rifle stands 4 cells east, inside 4 and 2 cells: 4 cells toward the base center (22, 48).
    assert [order for order in orders if 301 in order.get("units", ())] == [
        {"cmd": "move", "units": [301], "to": [25, 46]}
    ]


# -- the [combat] key ------------------------------------------------------------------------------


def write_tuning_with(root: Path, key: str, value: str | None) -> None:
    """Write the tracked tuning file under `root` with one key's line set to a value, or left out for None."""
    target = root / TUNING_FILE
    target.parent.mkdir(parents=True)
    replacement = "" if value is None else f"{key} = {value}"
    text = (REPO_ROOT / TUNING_FILE).read_text(encoding="utf-8")
    target.write_text(re.sub(rf"^{key} = .*$", replacement, text, count=1, flags=re.MULTILINE), encoding="utf-8")


def test_artillery_arty_splash_cells_ships_at_1_5_and_the_tracked_file_holds_it():
    assert CombatTuning().arty_splash_cells == Tuning.from_file(REPO_ROOT).combat.arty_splash_cells == 1.5


@pytest.mark.parametrize("value", [None, "-1.0"], ids=["missing", "negative"])
def test_artillery_arty_splash_cells_missing_or_negative_is_a_config_error_naming_it(tmp_path, value):
    write_tuning_with(tmp_path, "arty_splash_cells", value)

    with pytest.raises(ConfigError, match=r"arty_splash_cells in \[combat\] must be a number of at least 0"):
        Tuning.from_file(tmp_path)
