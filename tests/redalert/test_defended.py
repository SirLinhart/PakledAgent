"""No fight under enemy guns: main holds off from defenses it cannot beat, is redirected to an uncovered attack point,
sieges from beyond the coverage with its tanks screening behind, and shells a lone defense from its range + 2."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from situations import REPO_ROOT, ask, base_state, new_agent
from switches_off import SWITCHES_OFF
from ucla_agent.games.redalert import defended
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.command_prompt import Action
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning

YARD = ("fact", 22, 48)
# The army the commander attacks, at its centroid, between two pillboxes 4 cells apart: 5 rifles and 2 rocket soldiers
# worth 1,100, and the pillboxes 2 x 2 x 600 in the estimate, 3,500 together.
OBJECTIVE = (60, 48)
PILLBOX_CELLS = ((60, 46), (60, 50))
# A pillbox fires 6 cells; its coverage reaches 6 + defended_margin_cells (2).
PILLBOX_RANGE = 6
COVERAGE = PILLBOX_RANGE + 2
# An enemy refinery no defense covers, about 20 cells north of main.
REFINERY_CELL = (50, 29)
# A lone pillbox, 20 cells north of main, with no enemy unit within 10 cells of it.
LONE_CELL = (45, 28)
ATTACK_ARMY = Action(kind="attack", key=("attack", OBJECTIVE), label="attack", head="attack", cell=OBJECTIVE)
SWITCHED_OFF = Tuning(combat=CombatTuning(defended_redirect=False))


def own(unit_id: int, unit_type: str, x: int, y: int, *, idle: bool = True) -> dict:
    """One own unit as an observation lists it."""
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": 100, "act": None if idle else "move", "idle": idle}


def foe(enemy_id: int, enemy_type: str, x: int, y: int) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


PILLBOXES = [foe(610 + number, "pbox", x, y) for number, (x, y) in enumerate(PILLBOX_CELLS)]
ARMY = [foe(500 + number, "e1", 59, 46 + number) for number in range(5)] + [
    foe(505, "e3", 61, 47),
    foe(506, "e3", 61, 49),
]
REFINERY = foe(620, "proc", *REFINERY_CELL)
LONE_PILLBOX = foe(612, "pbox", *LONE_CELL)


def four_tanks(x: int = 46, *, idle: bool = True) -> list[dict]:
    """Four light tanks, 2,800 in a fight, around (x + 0.5, 48.5): 0.8 against the army with the pillboxes."""
    cells = ((x, 48), (x + 1, 48), (x, 49), (x + 1, 49))
    return [own(300 + number, "1tnk", cx, cy, idle=idle) for number, (cx, cy) in enumerate(cells)]


def artillery_and_tanks() -> list[dict]:
    """Two artillery pieces and three light tanks, 3,800: 1.09 against the army with the pillboxes."""
    return [
        own(310, "arty", 46, 47),
        own(311, "arty", 46, 49),
        own(300, "1tnk", 44, 46),
        own(301, "1tnk", 44, 48),
        own(302, "1tnk", 44, 50),
    ]


def at(tick: int, units=(), enemies=(), buildings=()) -> dict:
    """A state of an Allied base at that tick with these own units and these enemies in sight."""
    shown = {"units": list(units), "visible_enemies": list(enemies), "visible_enemy_buildings": list(buildings)}
    return base_state("england", YARD, tick=tick, **shown)


def commanded(action: Action = ATTACK_ARMY, tuning: Tuning | None = None) -> tuple:
    """An agent with the combat manager alone, whose main the commander has on a standing action, and the manager."""
    manager = CombatManager()
    agent = new_agent(manager, tuning=tuning)
    manager.command(action)
    return agent, manager


def events(match_record, reason: str) -> list[dict]:
    """The `combat` events of a reason."""
    return [data for data in match_record("combat") if data["reason"] == reason]


def covered(cell) -> bool:
    """Whether a cell lies within the coverage of one of the two pillboxes by the objective."""
    return any(math.dist(cell, pillbox) < COVERAGE for pillbox in PILLBOX_CELLS)


def naming(orders: list[dict], unit_ids) -> list[dict]:
    """The orders of a cycle that name any of these units."""
    return [order for order in orders if set(unit_ids) & set(order["units"])]


def assert_held_off(orders: list[dict], agent) -> None:
    """No order sends a unit into the pillboxes' coverage: no move or attack-move to a covered cell, and an attack on a
    covered target only by units within their weapon range of it."""
    world = agent.world
    for order in orders:
        if order["cmd"] in ("move", "attack_move"):
            assert not covered(order["to"]), order
        if order["cmd"] == "attack":
            target = world.enemy_memory[order["target"]]
            if covered((target.x, target.y)):
                for unit_id in order["units"]:
                    unit = world.units[unit_id]
                    reach = world.items.damage.get(unit.type).range
                    assert math.dist((unit.x, unit.y), (target.x, target.y)) <= reach, order


# -- the keys ----------------------------------------------------------------------------------------------------------


def test_defended_keys_ship_on_at_1_2_2_cells_and_30_s_in_the_built_ins_and_the_tracked_file():
    for shipped in (Tuning().combat, Tuning.from_file(REPO_ROOT).combat):
        keys = (shipped.defended_engage_ratio, shipped.defended_margin_cells, shipped.vision_dart_seconds)
        assert (shipped.defended_redirect, keys) == (True, (1.2, 2, 30))


# -- the check ---------------------------------------------------------------------------------------------------------


def test_defended_main_is_weighed_at_its_objective_at_0_8_with_the_two_pillboxes_counted():
    agent, _ = commanded()
    ask(agent, at(1000, four_tanks(), ARMY, PILLBOXES))
    world, ctx = agent.world, agent.ctx
    units = list(world.combat_units.values())

    defenses = defended.covering(OBJECTIVE, world, ctx)

    assert sorted(defense.id for defense in defenses) == [610, 611]
    assert defended.ratio_at(units, OBJECTIVE, defenses, world, ctx) == pytest.approx(0.8)
    assert defended.ratio_at(units, OBJECTIVE, [], world, ctx) == pytest.approx(2800 / 1100)


# -- the redirect ------------------------------------------------------------------------------------------------------


def test_defended_main_ordered_on_an_army_under_two_pillboxes_at_0_8_is_redirected_to_an_uncovered_refinery(
    match_record,
):
    agent, manager = commanded()

    orders = ask(agent, at(1000, four_tanks(), ARMY, [*PILLBOXES, REFINERY]))
    later = ask(agent, at(1005, four_tanks(x=47, idle=False), ARMY, [*PILLBOXES, REFINERY]))

    assert orders == [{"cmd": "attack_move", "units": [300, 301, 302, 303], "to": list(REFINERY_CELL)}]
    assert round(math.dist((46.5, 48.5), REFINERY_CELL)) == 20
    assert_held_off([*orders, *later], agent)
    assert manager.commanded_order is None
    (redirect,) = events(match_record, "defended_redirect")
    assert {key: redirect[key] for key in ("objective", "ratio", "target", "target_type", "cell", "legs")} == {
        "objective": list(OBJECTIVE),
        "ratio": 0.8,
        "target": 620,
        "target_type": "proc",
        "cell": list(REFINERY_CELL),
        "legs": [],
    }


def test_defended_a_redirect_whose_point_turns_out_guarded_is_abandoned_and_main_sieges_instead(match_record):
    agent, _ = commanded()
    ask(agent, at(1000, four_tanks(), ARMY, [*PILLBOXES, REFINERY]))
    # Five medium tanks, 4,250, come in sight beside the refinery: the four light tanks are 0.66 against them there.
    guard = [foe(540 + number, "2tnk", 48 + number, 30) for number in range(5)]

    orders = ask(agent, at(1005, four_tanks(), [*ARMY, *guard], [*PILLBOXES, REFINERY]))
    again = ask(agent, at(1010, four_tanks(), [*ARMY, *guard], [*PILLBOXES, REFINERY]))

    assert all(order["cmd"] == "move" and 48 <= order["to"][0] <= 50 for order in orders) and orders
    assert all(order.get("to") != list(REFINERY_CELL) for order in again)
    assert len(events(match_record, "defended_redirect")) == 1 and len(events(match_record, "defended_siege")) == 1


def test_defended_main_spread_out_gathers_at_its_rally_cell_before_it_sets_out_on_the_redirect(match_record):
    agent, _ = commanded()
    spread = [*four_tanks()[:2], own(302, "1tnk", 40, 60, idle=False), own(303, "1tnk", 40, 36, idle=False)]

    orders = ask(agent, at(1000, spread, ARMY, [*PILLBOXES, REFINERY]))

    assert orders == [{"cmd": "move", "units": [302, 303], "to": [43, 48]}]
    assert events(match_record, "defended_redirect") == []


def test_defended_an_uncovered_refinery_whose_straight_way_is_covered_is_no_attack_point_without_waypoints():
    agent, _ = commanded()
    # A refinery east of the pillboxes: the straight way from main runs through their coverage.
    behind = foe(620, "proc", 74, 48)

    orders = ask(agent, at(1000, four_tanks(), ARMY, [*PILLBOXES, behind]))

    assert defended.attack_points(list(agent.world.combat_units.values()), False, agent.world, agent.ctx) == []
    assert all(order["to"] != [74, 48] for order in orders)
    assert_held_off(orders, agent)


# -- the siege fallback ------------------------------------------------------------------------------------------------


def test_defended_with_no_uncovered_target_the_artillery_stands_beyond_pillbox_range_2_and_the_tanks_hold_behind(
    match_record,
):
    agent, _ = commanded()

    orders = ask(agent, at(1000, artillery_and_tanks(), ARMY, PILLBOXES))

    artillery = naming(orders, {310, 311})
    tanks = naming(orders, {300, 301, 302})
    assert {order["cmd"] for order in [*artillery, *tanks]} == {"move"}
    assert all(math.dist(order["to"], pillbox) >= COVERAGE for order in artillery for pillbox in PILLBOX_CELLS)
    (siege_event,) = events(match_record, "defended_siege")
    front = siege_event["front"]
    assert front == [52, 48] and siege_event["defenses"] == [610, 611]
    # The base lies due west: the tanks hold 2 to 4 cells behind the artillery's front, toward it.
    assert all(2 <= front[0] - order["to"][0] <= 4 for order in tanks) and len(tanks) == 3
    assert_held_off(orders, agent)


def test_defended_a_march_under_way_into_the_coverage_stops_every_unit_at_its_stand_or_screen_cell():
    agent, _ = commanded()
    marching = [{**unit, "act": "move", "idle": False} for unit in artillery_and_tanks()]

    orders = ask(agent, at(1000, marching, ARMY, PILLBOXES))

    assert {unit_id for order in orders for unit_id in order["units"]} == {310, 311, 300, 301, 302}
    assert_held_off(orders, agent)


def test_defended_in_the_siege_a_jeep_darts_to_the_front_every_30_s_and_comes_back(match_record):
    agent, _ = commanded()
    # Two artillery pieces, two light tanks and a jeep, 3,600: 1.03 against the army with the pillboxes.
    units = [*artillery_and_tanks()[:4], own(320, "jeep", 44, 50)]
    first = ask(agent, at(1000, units, ARMY, PILLBOXES))
    held = [*artillery_and_tanks()[:2], own(300, "1tnk", 49, 47), own(301, "1tnk", 49, 49), own(320, "jeep", 49, 51)]

    before = ask(agent, at(1000 + 30 * TICKS_PER_SECOND - 1, held, ARMY, PILLBOXES))
    dart = ask(agent, at(1000 + 30 * TICKS_PER_SECOND, held, ARMY, PILLBOXES))
    there = [*held[:4], own(320, "jeep", 52, 48)]
    back = ask(agent, at(1000 + 31 * TICKS_PER_SECOND, there, ARMY, PILLBOXES))

    (holding,) = naming(first, {320})
    assert holding["to"][0] == 49 and naming(before, {320}) == []
    assert naming(dart, {320}) == [{"cmd": "move", "units": [320], "to": [52, 48]}]
    (returning,) = naming(back, {320})
    assert returning["cmd"] == "move" and returning["to"][0] == 49
    assert len(events(match_record, "defended_siege")) == 1


def test_defended_the_plan_s_advance_on_a_building_under_the_pillboxes_sieges_instead_of_marching_in(match_record):
    agent = new_agent(CombatManager())
    agent.ctx.min_attack_strength = lambda plan: 3000
    # Five light tanks, 3,500, gathered at the staging point: enough against the pillboxes alone (1.3 x 2,400), 1.0
    # against them with the army.
    staged = [own(300 + number, "1tnk", 32 + number % 2, 47 + number // 2) for number in range(5)]

    orders = ask(agent, at(1000, staged, ARMY, [*PILLBOXES, foe(630, "powr", *OBJECTIVE)]))

    assert orders and {order["cmd"] for order in orders} == {"move"}
    assert all(48 <= order["to"][0] <= 50 for order in orders)
    assert len(events(match_record, "defended_siege")) == 1
    assert_held_off(orders, agent)


# -- the lone defense --------------------------------------------------------------------------------------------------


def test_defended_a_lone_pillbox_is_shelled_by_artillery_standing_at_its_range_2(match_record):
    agent, _ = commanded()
    units = [own(310, "arty", 46, 48), own(300, "1tnk", 45, 47), own(301, "1tnk", 45, 49)]
    standing = [own(310, "arty", 45, 36), own(300, "1tnk", 43, 38), own(301, "1tnk", 42, 37)]

    orders = ask(agent, at(1000, units, ARMY, [*PILLBOXES, LONE_PILLBOX]))
    shelling = ask(agent, at(1005, standing, ARMY, [*PILLBOXES, LONE_PILLBOX]))

    (stand,) = naming(orders, {310})
    assert stand["cmd"] == "move" and COVERAGE <= math.dist(stand["to"], LONE_CELL) < COVERAGE + 1
    assert naming(shelling, {310}) == [{"cmd": "attack", "units": [310], "target": 612}]
    assert all(math.dist(order["to"], LONE_CELL) >= COVERAGE for order in naming(orders, {300, 301}))
    (shelled,) = events(match_record, "defense_shelling")
    assert (shelled["target"], shelled["target_type"], shelled["cell"]) == (612, "pbox", list(LONE_CELL))
    assert events(match_record, "defended_siege") == [] and events(match_record, "defended_redirect") == []


def test_defended_a_pillbox_with_an_enemy_unit_within_10_cells_is_not_lone():
    agent, _ = commanded()
    ask(agent, at(1000, four_tanks(), [*ARMY, foe(530, "e1", 45, 37)], [*PILLBOXES, LONE_PILLBOX]))

    assert not defended.is_lone(agent.world.enemy_memory[612], agent.world)
    assert defended.is_lone(agent.world.enemy_memory[610], agent.world) is False


# -- holding off -------------------------------------------------------------------------------------------------------


def test_defended_held_off_cuts_a_covered_move_back_to_the_edge_and_keeps_attacks_only_from_within_range():
    agent, _ = commanded()
    # A light tank 4.1 cells from the pillbox at (60, 46), within its 4.75-cell gun.
    near = own(305, "1tnk", 56, 47)
    ask(agent, at(1000, [*four_tanks(), near], ARMY, PILLBOXES))
    intents = [
        Intent(Priority.MOVE, {"cmd": "attack_move", "units": [300, 301], "to": list(OBJECTIVE)}),
        Intent(Priority.MOVE, {"cmd": "attack", "units": [302, 305], "target": 610}),
        Intent(Priority.MOVE, {"cmd": "attack", "units": [303], "target": 611}),
        Intent(Priority.EMERGENCY, {"cmd": "move", "units": [303], "to": list(OBJECTIVE)}),
    ]

    held = defended.held_off(intents, agent.world, agent.ctx)

    assert [intent.order["cmd"] for intent in held] == ["attack_move", "attack", "move"]
    edge = held[0].order["to"]
    assert not covered(edge) and edge[0] < OBJECTIVE[0] and math.dist(edge, OBJECTIVE) <= COVERAGE + 1.5
    assert held[1].order == {"cmd": "attack", "units": [305], "target": 610}
    assert held[2] == intents[3]


# -- the exemptions ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "units, tuning",
    [(four_tanks(), SWITCHED_OFF), ([*four_tanks(), own(304, "1tnk", 47, 50), own(305, "1tnk", 46, 50)], None)],
    ids=["switch off", "at 1.2"],
)
def test_defended_switched_off_or_at_the_ratio_main_goes_on_as_the_commander_says(match_record, units, tuning):
    agent, manager = commanded(tuning=tuning)

    ask(agent, at(1000, units, ARMY, [*PILLBOXES, REFINERY]))

    assert manager.commanded_order["cmd"] == "attack_move" and manager.commanded_order["to"] == list(OBJECTIVE)
    assert [data for data in match_record("combat") if data["reason"].startswith("defen")] == []


def test_defended_main_striking_as_a_whole_is_left_to_the_strike():
    agent, manager = commanded()
    manager._strike.objective = lambda world: world.enemy_memory.get(610)

    ask(agent, at(1000, four_tanks(), ARMY, [*PILLBOXES, REFINERY]))

    assert manager._defended.kind is None
    assert manager.commanded_order is not None and manager.commanded_order["to"] == list(OBJECTIVE)


def test_defended_switches_off_with_the_other_combat_switches_for_the_older_tests():
    assert SWITCHES_OFF.combat.defended_redirect is False
    assert replace(SWITCHES_OFF.combat, defended_redirect=True).defended_engage_ratio == 1.2
