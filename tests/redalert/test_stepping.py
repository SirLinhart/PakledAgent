"""Stepped movement: main's far advances and moves home in steps, and how a stepping group meets the enemy."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from fake_game import FakeRedAlert, at_seconds
from situations import StubChooser, ask, base_state, new_agent, tracked_items
from ucla_agent.games.redalert import stepping
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.combat import CombatManager, distance_to_path
from ucla_agent.games.redalert.command_army import ADVANCE, FLANK, GATHER, RETREAT_HOME
from ucla_agent.games.redalert.command_prompt import Action
from ucla_agent.games.redalert.forward_staging import Reach
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.settings import RedAlertSettings
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning
from ucla_agent.games.redalert.world import Unit, World
from whole_match import COMPUTE_P95_MS, WHOLE_MATCH_STEPS, WholeMatch, play_whole_match

YARD = ("fact", 22, 48)
POWER = ("powr", 19, 52)
# The shipped thresholds with vehicle_repair off: a tank stays main's whatever its health, as the steps are about.
STEPPED = replace(Tuning(), combat=replace(Tuning().combat, vehicle_repair=False))
UNSTEPPED = replace(STEPPED, combat=replace(STEPPED.combat, stepped_advance=False, stepped_return=False))
# The stepped movement on with no fight when caught on the move.
NOT_RETURNING_FIRE = replace(STEPPED, combat=replace(STEPPED.combat, moving_returns_fire=False))
# The stepped movement on with no decision on the enemies met: the steps alone.
NO_MET = replace(STEPPED, combat=replace(STEPPED.combat, met_enemy_decision=False))
COMBAT = STEPPED.combat
START = (20, 20)
FAR = (60, 20)


def own(unit_id: int, unit_type: str, x: int, y: int, *, idle: bool = True) -> dict:
    """One own unit as an observation lists it."""
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": 100, "act": None if idle else "move", "idle": idle}


def foe(enemy_id: int, enemy_type: str, x: int, y: int) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


def tanks_at(x: int, y: int, count: int = 6, first: int = 300, *, idle: bool = True) -> list[dict]:
    """`count` medium tanks in a block of three columns from a cell, numbered from `first`."""
    return [own(first + n, "2tnk", x - 1 + n % 3, y - 1 + n // 3, idle=idle) for n in range(count)]


def artillery_at(x: int, y: int, *, idle: bool = True) -> list[dict]:
    """Two artillery pieces one cell apart at a column."""
    return [own(310, "arty", x, y, idle=idle), own(311, "arty", x, y + 1, idle=idle)]


def action(kind: str, cell: tuple[int, int], via: tuple[int, int] | None = None) -> Action:
    """A standing action of the commander's of a kind on a cell."""
    return Action(kind, (kind, cell), f"{kind} {cell}", f"{kind} to {cell}", cell=cell, via=via)


class Scene:
    """An agent with the combat manager under a tuning, main given a standing action, and one cycle at a time."""

    def __init__(self, standing: Action | None = None, tuning: Tuning = STEPPED, *more) -> None:
        self.manager = CombatManager()
        self.agent = new_agent(*more, self.manager, tuning=tuning)
        if standing is not None:
            self.manager.command(standing)

    def cycle(self, tick: int, units, enemies=(), buildings=(), **observation) -> list[dict]:
        state = base_state(
            "england",
            YARD,
            POWER,
            tick=tick,
            units=list(units),
            visible_enemies=list(enemies),
            visible_enemy_buildings=list(buildings),
            **observation,
        )
        return ask(self.agent, state)

    @property
    def move(self) -> stepping.StepMove | None:
        return self.manager._stepper.move


def moves_to(orders: list[dict], cell) -> list[dict]:
    return [order for order in orders if order.get("to") == list(cell)]


def steps(records: list[dict], phase: str | None = None) -> list[dict]:
    """The `combat` events of reason step, of a phase when one is given."""
    return [event for event in records if event["reason"] == stepping.STEP and phase in (None, event["phase"])]


# -- the step points ------------------------------------------------------------------------------------


def test_along_walks_the_polyline_through_its_waypoints_and_stops_at_its_end():
    assert stepping.along((0, 0), [(10, 0)], 4) == (4, 0)
    assert stepping.along((0, 0), [(6, 0), (6, 8)], 10) == (6, 4)
    assert stepping.along((0, 0), [(6, 0)], 50) == (6, 0)
    assert stepping.route_length((0, 0), [(6, 0), (6, 8)]) == 14


def test_a_step_point_on_blocked_ground_moves_to_the_nearest_open_cell_within_3_else_on_along_the_route():
    near = Terrain(blocked={(30, 20)})
    assert stepping.snapped((30, 20), (20, 20), [FAR], 10, near, 3) in {(29, 20), (31, 20), (30, 19), (30, 21)}
    walled = Terrain(blocked={(30 + dx, 20 + dy) for dx in range(-3, 4) for dy in range(-3, 4)})
    assert stepping.snapped((30, 20), (20, 20), [FAR], 10, walled, 3) == (30, 20)
    open_ground = Terrain()
    assert stepping.snapped((30, 20), (20, 20), [FAR], 10, open_ground, 3) == (30, 20)


def test_out_of_reach_keeps_a_step_point_clear_of_a_disc_and_ends_stepping_at_the_start_when_none_is_left():
    disc = [Reach(50, 20, 9)]
    assert stepping.out_of_reach((30, 20), (40, 20), False, disc, Terrain(), COMBAT) == ((40, 20), False)
    assert stepping.out_of_reach((40, 20), (50, 20), False, disc, Terrain(), COMBAT) is None
    assert stepping.out_of_reach((30, 20), (45, 20), False, disc, Terrain(), COMBAT) == ((41, 20), True)


def unit(unit_id: int, unit_type: str, x: int, y: int) -> Unit:
    return Unit(unit_id, unit_type, x, y, 100, True, None, None)


def test_arrival_counts_a_tank_within_4_cells_and_an_artillery_piece_within_7():
    group = [unit(1, "2tnk", 34, 20), unit(2, "2tnk", 35, 20), unit(3, "arty", 22, 20), unit(4, "arty", 24, 20)]

    found = stepping.arrival(group, (30, 20), COMBAT)
    closer = stepping.arrival([*group[:2], unit(3, "arty", 23, 20), group[3]], (30, 20), COMBAT)

    assert (found.share, found.long_range_present) == (0.5, False)
    assert (closer.share, closer.long_range_present) == (0.75, True)


# -- the advance in steps -------------------------------------------------------------------------------


def test_advance_first_step_ten_cells_main_of_six_tanks_and_two_artillery_is_sent_10_cells_on_not_to_the_end():
    scene = Scene(action(ADVANCE, FAR))

    orders = scene.cycle(1000, [*tanks_at(*START), *artillery_at(18, 20)])

    (screen,) = [order for order in orders if order["cmd"] == "attack_move"]
    assert screen["units"] == list(range(300, 306))
    assert math.dist(screen["to"], (30, 20)) <= 1
    # No unit is ordered past the step point: the artillery takes its slots behind it.
    assert all(order["to"][0] <= 31 for order in orders)
    assert sorted(unit_id for order in orders for unit_id in order["units"]) == [*range(300, 306), 310, 311]


def test_step_waits_for_artillery_tanks_there_and_the_artillery_9_cells_back_hold_and_at_6_cells_release(
    match_record,
):
    scene = Scene(action(ADVANCE, FAR))
    scene.cycle(1000, [*tanks_at(*START), *artillery_at(18, 20)])

    waiting = scene.cycle(1005, [*tanks_at(30, 20), *artillery_at(21, 20)])
    released = scene.cycle(1010, [*tanks_at(30, 20), *artillery_at(24, 20)])

    assert moves_to(waiting, (40, 20)) == []
    assert moves_to(released, (40, 20)) == [{"cmd": "attack_move", "units": list(range(300, 306)), "to": [40, 20]}]
    (release,) = steps(match_record("combat"), stepping.RELEASE)
    assert (release["released_by"], release["index"], release["cell"]) == (stepping.ARRIVED, 1, [30, 20])
    assert release["arrived_share"] == 1.0 and release["long_range_present"] is True


def test_straggler_limit_with_half_arrived_and_a_tank_stuck_20_cells_back_the_next_step_goes_12_s_later(match_record):
    scene = Scene(action(ADVANCE, FAR))
    scene.cycle(1000, tanks_at(*START))
    arrived = [own(300 + n, "2tnk", 30, 19 + n) for n in range(4)]
    group = [*arrived, own(304, "2tnk", 25, 20), own(305, "2tnk", 10, 20)]
    scene.cycle(1005, group)

    before = scene.cycle(1005 + 12 * TICKS_PER_SECOND - 5, group)
    after = scene.cycle(1005 + 12 * TICKS_PER_SECOND, group)

    assert moves_to(before, (40, 20)) == []
    assert [order["units"] for order in moves_to(after, (40, 20))] == [list(range(300, 306))]
    assert steps(match_record("combat"), stepping.RELEASE)[0]["released_by"] == stepping.STRAGGLERS


def test_blocked_limit_with_no_unit_arriving_the_next_step_goes_40_s_after_the_step_was_ordered(match_record):
    scene = Scene(action(ADVANCE, FAR))
    stuck = tanks_at(*START)
    scene.cycle(1000, stuck)

    before = scene.cycle(1000 + 40 * TICKS_PER_SECOND - 5, stuck)
    after = scene.cycle(1000 + 40 * TICKS_PER_SECOND, stuck)

    assert moves_to(before, (40, 20)) == [] and moves_to(after, (40, 20))
    assert steps(match_record("combat"), stepping.RELEASE)[0]["released_by"] == stepping.BLOCKED


def test_an_idle_unit_short_of_its_step_point_is_sent_again_at_most_every_2_s():
    scene = Scene(action(ADVANCE, FAR))
    scene.cycle(1000, tanks_at(*START))
    group = [*tanks_at(30, 20, 4), own(304, "2tnk", 22, 20), own(305, "2tnk", 22, 21)]

    again = [scene.cycle(tick, group) for tick in (1005, 1049, 1050, 1060)]

    assert [moves_to(orders, (30, 20)) for orders in again] == [
        [],
        [],
        [{"cmd": "attack_move", "units": [304, 305], "to": [30, 20]}],
        [],
    ]


def test_step_point_out_of_defense_reach_no_step_point_within_9_cells_of_a_range_6_pillbox_and_stepping_ends_there(
    match_record,
):
    scene = Scene(action(ADVANCE, FAR))
    pillbox = [foe(700, "pbox", 50, 20)]
    scene.cycle(1000, tanks_at(*START), buildings=pillbox)
    scene.cycle(1005, tanks_at(30, 20), buildings=pillbox)
    after_last = scene.cycle(1010, tanks_at(40, 20), buildings=pillbox)

    events = steps(match_record("combat"))
    points = [event["cell"] for event in events if event["phase"] == stepping.RELEASE]
    assert points == [[30, 20], [40, 20]]
    assert all(math.dist(point, (50, 20)) >= 9 for point in points)
    assert 9 <= math.dist(points[-1], (50, 20)) <= 10
    assert events[-1]["phase"] == stepping.END and events[-1]["cell"] == [40, 20]
    # Once the stepping is over the action goes on at its destination as without steps.
    assert moves_to(after_last, (50, 20)) == [] or all(order["cmd"] != "move" for order in after_last)


def test_a_defense_seen_after_a_step_point_was_fixed_moves_it_out_of_reach():
    scene = Scene(action(ADVANCE, FAR))
    scene.cycle(1000, tanks_at(*START))

    orders = scene.cycle(1005, tanks_at(21, 20, idle=False), buildings=[foe(700, "pbox", 36, 20)])

    (screen,) = moves_to(orders, (27, 20))
    assert screen["cmd"] == "attack_move"


def test_a_flank_steps_along_its_waypoint_and_marches_on_once_its_steps_have_passed_it():
    scene = Scene(action(FLANK, (40, 40), via=(26, 20)))
    first = scene.cycle(1000, tanks_at(*START))
    second = scene.cycle(1005, tanks_at(28, 23))

    # 6 cells to the waypoint, then 4 on toward the objective; and from there 10 more on the objective.
    ((first_point,),) = [[order["to"] for order in first if order["cmd"] == "attack_move"]]
    ((second_point,),) = [[order["to"] for order in second if order["cmd"] == "attack_move"]]
    assert math.dist(first_point, (28.3, 23.3)) <= 1 and math.dist(second_point, (33.8, 31.2)) <= 1


# -- the moves home -------------------------------------------------------------------------------------


def test_return_home_steps_a_retreat_home_from_45_cells_away_is_ordered_10_cells_back_and_waits_for_artillery(
    match_record,
):
    home = (22, 48)
    scene = Scene(action(RETREAT_HOME, home))
    far_out = [*tanks_at(67, 48), *artillery_at(69, 48)]

    first = scene.cycle(1000, far_out)
    waiting = scene.cycle(1005, [*tanks_at(57, 48), *artillery_at(67, 48)])

    (screen,) = [order for order in first if order["cmd"] == "move" and order["units"][0] == 300]
    assert 9 <= math.dist(screen["to"], (67.2, 48.5)) <= 11
    assert all(order["cmd"] == "move" for order in first)
    point = screen["to"]
    assert [event["phase"] for event in steps(match_record("combat"))] == [stepping.START]
    assert all(order["to"] != point or order["units"] != list(range(300, 306)) for order in waiting)
    assert not any(math.dist(order["to"], home) < math.dist(point, home) - 1 for order in waiting)


def test_short_move_unstepped_a_gather_8_cells_off_goes_as_one_order(match_record):
    scene = Scene(action(GATHER, (32, 48)))

    scene.cycle(1000, tanks_at(40, 48))

    order = scene.manager.commanded_order
    assert (order["cmd"], sorted(order["units"]), order["to"]) == ("move", list(range(300, 306)), [32, 48])
    assert steps(match_record("combat")) == []


def test_plan_advance_steps_main_strong_enough_goes_10_cells_on_toward_the_enemy_s_start():
    scene = Scene()
    scene.agent.ctx.min_attack_strength = lambda plan: 3000
    gathered = tanks_at(32, 48)

    orders = scene.cycle(1000, gathered)

    assert orders == [{"cmd": "attack_move", "units": list(range(300, 306)), "to": [42, 48]}]


def test_plan_retreat_of_an_army_gone_forward_steps_home_and_one_still_at_the_staging_point_moves_straight():
    scene = Scene(None, NO_MET)
    scene.agent.ctx.min_attack_strength = lambda plan: 3000
    scene.cycle(1000, tanks_at(32, 48))
    stronger = [foe(500 + n, "3tnk", 66 + n % 3, 44) for n in range(8)]

    orders = scene.cycle(1005, tanks_at(62, 48), enemies=stronger)

    (retreat,) = [order for order in orders if order["cmd"] == "move"]
    assert retreat["units"] == list(range(300, 306)) and 9 <= math.dist(retreat["to"], (62, 48.5)) <= 11


def test_base_defense_calls_main_home_in_steps_10_cells_toward_the_staging_point():
    scene = Scene(None, STEPPED, BaseDefenseManager())
    raiders = [foe(500 + n, "3tnk", 24 + n, 50) for n in range(6)]

    orders = scene.cycle(1000, tanks_at(67, 48), enemies=raiders)

    assert {"cmd": "move", "units": list(range(300, 306)), "to": [57, 48]} in orders


@pytest.mark.parametrize("tuning", [UNSTEPPED], ids=["switches off"])
def test_switches_off_main_advances_and_goes_home_as_one_order(tuning):
    scene = Scene(action(ADVANCE, FAR), tuning)

    scene.cycle(1000, tanks_at(*START))

    assert scene.manager.commanded_order["to"] == list(FAR)


def test_the_tuning_keys_are_the_built_in_values_of_the_tracked_file():
    assert (COMBAT.stepped_advance, COMBAT.stepped_return, COMBAT.step_cells, COMBAT.step_min_move_cells) == (
        True,
        True,
        10,
        15,
    )
    assert (COMBAT.step_arrive_cells, COMBAT.step_arrive_share, COMBAT.step_long_range_extra_cells) == (4, 0.8, 3)
    assert (COMBAT.step_straggler_seconds, COMBAT.step_max_seconds, COMBAT.step_snap_cells) == (12, 40, 3)
    assert CombatTuning() == Tuning().combat


def test_stepper_finish_ends_a_move_and_its_key_steps_no_more_while_it_is_followed():
    world = World(tracked_items())
    world.update(base_state("england", YARD, tick=1000, units=tanks_at(*START)))
    group = list(world.combat_units.values())
    stepper = stepping.Stepper()
    plan = stepping.Plan("key", stepping.ADVANCE, FAR)
    stepper.begin_cycle()
    assert stepper.follow(plan, group, world, Terrain(), STEPPED) is not None

    stepper.finish(world)
    stepper.end_cycle()

    stepper.begin_cycle()
    assert stepper.follow(plan, group, world, Terrain(), STEPPED) is None and stepper.is_done("key")
    stepper.end_cycle()
    stepper.begin_cycle()
    stepper.end_cycle()
    assert not stepper.is_done("key")


# -- a whole match ---------------------------------------------------------------------------------------

# An enemy pillbox in sight from the first minute on the way to the enemy's start, 8 cells short of it.
PILLBOX_CELL = (66, 48)


@pytest.fixture(scope="module")
def stepped_match() -> WholeMatch:
    """A whole fake match with the shipped tuning, and a pillbox on main's way to the enemy."""

    def pillbox(game: FakeRedAlert) -> None:
        game.place_enemy("pbox", *PILLBOX_CELL, visible=True)

    return play_whole_match(Tuning(), scenario=[at_seconds(60, pillbox)])


def test_whole_match_with_the_shipped_tuning_runs_without_an_error_or_a_refused_batch_in_budget(stepped_match):
    assert stepped_match.agent.ctx.tuning == Tuning() and Tuning().combat.stepped_advance
    assert len(stepped_match.compute_ms) == WHOLE_MATCH_STEPS and stepped_match.data_of("error") == []
    assert stepped_match.run.invalid_actions == 0 and stepped_match.game.invalid_actions == 0
    assert stepped_match.compute_p95_ms <= COMPUTE_P95_MS


def test_whole_match_main_steps_and_no_step_point_lies_within_9_cells_of_the_pillbox(stepped_match):
    stepped = steps(stepped_match.data_of("combat"))
    released = [logged["cell"] for logged in stepped if logged["phase"] == stepping.RELEASE]

    assert {logged["phase"] for logged in stepped} >= {stepping.START, stepping.RELEASE}
    assert released and all(math.dist(cell, PILLBOX_CELL) >= 9 for cell in released)


# -- meeting the enemy on a stepped advance ----------------------------------------------------------------

RIFLES = [foe(500 + n, "e1", 32, 19 + n) for n in range(3)]
# Eight heavy tanks 8 cells ahead of main stepping from (30, 20) on to (40, 20).
HEAVIES = [foe(600 + n, "3tnk", 42 + n % 2, 17 + n // 2) for n in range(8)]


def met_events(records: list[dict]) -> list[dict]:
    return [event for event in records if event["reason"] in (stepping.MET, stepping.MET_END)]


def attacks(orders: list[dict]) -> list[dict]:
    return [order for order in orders if order["cmd"] == "attack"]


def on_second_step(tuning: Tuning = STEPPED) -> Scene:
    """main of six medium tanks stepping on (60, 20): its first step to (30, 20) released at 1005, the second to
    (40, 20) ordered."""
    scene = Scene(action(ADVANCE, FAR), tuning)
    scene.cycle(1000, tanks_at(*START))
    scene.cycle(1005, tanks_at(30, 20))
    return scene


def test_met_beatable_fights_three_rifles_are_attacked_with_no_release_and_the_step_goes_on_3_s_after_they_died(
    match_record,
):
    scene = Scene(action(ADVANCE, FAR))
    scene.cycle(1000, tanks_at(*START))

    met = scene.cycle(1005, tanks_at(24, 20, idle=False), RIFLES)
    held = scene.cycle(1010, tanks_at(30, 20), RIFLES)
    scene.cycle(1015, tanks_at(30, 20), events=[{"seq": 1, "tick": 1015, "kind": "enemy_killed", "count": 3}])
    two_s = scene.cycle(1065, tanks_at(30, 20))
    three_s = scene.cycle(1090, tanks_at(30, 20))

    assert attacks(met) and {order["target"] for order in attacks(met)} <= {500, 501, 502}
    assert moves_to(held, (40, 20)) == [] and moves_to(two_s, (40, 20)) == []
    assert moves_to(three_s, (40, 20)) == [{"cmd": "attack_move", "units": list(range(300, 306)), "to": [40, 20]}]
    events = met_events(match_record("combat"))
    assert [(event["reason"], event.get("decision"), event.get("resumed_by")) for event in events] == [
        (stepping.MET, stepping.FIGHT, None),
        (stepping.MET_END, None, stepping.CLEARED),
    ]


def test_met_chase_limit_a_rifleman_9_cells_off_the_step_s_way_is_not_attacked_and_no_order_leaves_5_cells_of_it():
    # The met enemies near the group are fought with met_fight_group_reach on: the limit is the step's way alone.
    scene = Scene(action(ADVANCE, FAR), replace(STEPPED, combat=replace(STEPPED.combat, met_fight_group_reach=False)))
    scene.cycle(1000, tanks_at(*START))

    orders = scene.cycle(1005, tanks_at(25, 20, idle=False), [foe(500, "e1", 26, 29)])

    assert scene.move.decision == stepping.FIGHT
    assert [order for order in orders if order.get("target") == 500] == []
    assert all(distance_to_path(order["to"], (20, 20), (30, 20)) <= 5 for order in orders if "to" in order)


# Four rifles 8 cells off the step's way (20, 20) -> (30, 20), met within 10 cells of main's front row.
OFF_THE_WAY = [foe(500 + n, "e1", 24 + n, 28) for n in range(4)]
GROUP_REACH_OFF = replace(STEPPED, combat=replace(STEPPED.combat, met_fight_group_reach=False))


@pytest.mark.parametrize("tuning, fought", [(STEPPED, True), (GROUP_REACH_OFF, False)], ids=["switch on", "switch off"])
def test_met_fight_group_reach_rifles_8_cells_off_the_way_but_near_the_group_are_fought_by_every_tank(tuning, fought):
    scene = Scene(action(ADVANCE, FAR), tuning)
    scene.cycle(1000, tanks_at(*START))

    orders = scene.cycle(1005, tanks_at(25, 20, idle=False), OFF_THE_WAY)

    assert scene.move.decision == stepping.FIGHT
    # The fight's attacks on the rifles and attack-moves to them; the crush run over them sends two tanks either way.
    fight = [
        order
        for order in orders
        if order.get("target") in range(500, 504)
        or (order["cmd"] == "attack_move" and tuple(order["to"]) in {(rifle["x"], rifle["y"]) for rifle in OFF_THE_WAY})
    ]
    crushing = sorted(unit_id for order in orders if order["cmd"] == "move" for unit_id in order["units"])
    named = sorted(unit_id for order in fight for unit_id in order["units"])
    assert crushing == [304, 305]
    assert named == ([300, 301, 302, 303] if fought else [])


def test_met_stronger_steps_back_eight_heavy_tanks_send_every_unit_to_the_previous_step_point_not_the_staging_point(
    match_record,
):
    scene = on_second_step()

    orders = scene.cycle(1010, tanks_at(34, 20, idle=False), HEAVIES)

    assert orders == [{"cmd": "move", "units": list(range(300, 306)), "to": [30, 20]}]
    (met,) = met_events(match_record("combat"))
    assert met["decision"] == stepping.STEP_BACK and met["ratio"] < 1.0


def stepped_back() -> Scene:
    """main stepped back to (30, 20) from eight heavy tanks at 1010, standing there at 1015."""
    scene = on_second_step()
    scene.cycle(1010, tanks_at(34, 20, idle=False), HEAVIES)
    scene.cycle(1015, tanks_at(30, 20), HEAVIES)
    return scene


def test_met_resume_when_enemy_left_the_step_goes_on_8_s_after_no_enemy_stands_within_15_cells_of_it(match_record):
    scene = stepped_back()
    gone = [{**heavy, "x": heavy["x"] + 20} for heavy in HEAVIES]

    scene.cycle(1020, tanks_at(30, 20), gone)
    seven_s = scene.cycle(1020 + 7 * TICKS_PER_SECOND, tanks_at(30, 20), gone)
    eight_s = scene.cycle(1020 + 8 * TICKS_PER_SECOND, tanks_at(30, 20), gone)

    assert moves_to(seven_s, (40, 20)) == []
    assert moves_to(eight_s, (40, 20)) == [{"cmd": "attack_move", "units": list(range(300, 306)), "to": [40, 20]}]
    assert met_events(match_record("combat"))[-1]["resumed_by"] == stepping.ENEMY_LEFT


def test_met_resume_on_reinforcement_joiners_lifting_the_ratio_to_1_2_resume_the_step_at_once(match_record):
    scene = stepped_back()
    joined = [*tanks_at(30, 20), *tanks_at(29, 23, 10, first=320)]

    orders = scene.cycle(1020, joined, HEAVIES)

    # Stepping on, the group is on the heavy tanks' cells: it does not walk through them but fights.
    assert moves_to(orders, (30, 20)) == [] and scene.move.decision != stepping.STEP_BACK
    ends = [event["resumed_by"] for event in met_events(match_record("combat")) if "resumed_by" in event]
    assert ends == [stepping.RATIO]


def scripted_ratios(monkeypatch, ratios: list[float]) -> None:
    """Have the ratio of every meeting read the next of `ratios`, the last one after them."""
    left = list(ratios)
    monkeypatch.setattr(stepping, "met_ratio", lambda *given: left.pop(0) if len(left) > 1 else left[0])


def test_met_latch_and_hysteresis_a_fight_is_kept_6_s_whatever_the_ratio_then_kept_at_0_8_and_left_at_0_6(monkeypatch):
    scene = Scene(action(ADVANCE, FAR))
    scene.cycle(1000, tanks_at(*START))
    scripted_ratios(monkeypatch, [1.05, *[0.95, 1.05] * 15, 0.8, 0.6])
    rifle = [foe(500, "e1", 28, 21)]

    decisions = []
    for tick in range(1005, 1160, 5):
        scene.cycle(tick, tanks_at(24, 20, idle=False), rifle)
        decisions.append(scene.move.decision)
    scene.cycle(1160, tanks_at(24, 20, idle=False), rifle)
    kept = scene.move.decision
    scene.cycle(1165, tanks_at(24, 20, idle=False), rifle)

    assert set(decisions) == {stepping.FIGHT} and kept == stepping.FIGHT
    assert scene.move.decision == stepping.STEP_BACK


def test_met_step_back_again_met_again_where_it_waits_at_0_5_it_steps_back_to_the_step_point_before(match_record):
    scene = stepped_back()
    closer = [{**heavy, "x": heavy["x"] - 6} for heavy in HEAVIES]

    within_6_s = scene.cycle(1155, tanks_at(30, 20), closer)
    orders = scene.cycle(1160, tanks_at(30, 20), closer)

    assert moves_to(within_6_s, (20, 20)) == []

    assert orders == [{"cmd": "move", "units": list(range(300, 306)), "to": [20, 20]}]
    assert met_events(match_record("combat"))[-1]["decision"] == stepping.STEP_BACK_AGAIN


def test_met_step_back_again_on_the_first_step_the_group_stops_at_the_move_s_start():
    scene = Scene(action(ADVANCE, FAR))
    scene.cycle(1000, tanks_at(*START))
    heavies = [foe(600 + n, "3tnk", 28 + n % 2, 17 + n // 2) for n in range(8)]
    scene.cycle(1005, tanks_at(23, 20, idle=False), heavies)
    scene.cycle(1010, tanks_at(*START), heavies)

    orders = scene.cycle(1160, tanks_at(*START), [{**heavy, "x": heavy["x"] - 3} for heavy in heavies])

    assert [order for order in orders if order["cmd"] in ("move", "attack_move")] == []
    assert scene.move.back_point == START


def test_met_plan_retreat_is_step_back_under_press_a_retreat_orders_the_previous_step_point_not_the_staging_point():
    scene = Scene()
    scene.agent.ctx.min_attack_strength = lambda plan: 3000
    scene.cycle(1000, tanks_at(32, 48))
    scene.cycle(1005, tanks_at(42, 48))
    heavies = [foe(600 + n, "3tnk", 50 + n % 2, 45 + n // 2) for n in range(8)]

    orders = scene.cycle(1010, tanks_at(45, 48, idle=False), heavies)

    assert [order["to"] for order in orders if order["cmd"] == "move"] == [[42, 48]]
    assert scene.manager.mode == "retreat"


def test_met_switch_off_a_stepping_group_meeting_heavy_tanks_keeps_its_step():
    scene = on_second_step(NO_MET)

    scene.cycle(1010, tanks_at(34, 20, idle=False), HEAVIES)

    assert scene.move.decision is None and scene.move.point == (40, 20)


def test_met_enemies_are_ground_units_within_10_cells_and_defenses_reaching_a_unit_by_their_range_and_2():
    world = World(tracked_items())
    shown = [foe(500, "e1", 30, 20), foe(501, "e1", 41, 20)]
    defenses = [foe(700, "pbox", 26, 24), foe(701, "pbox", 40, 30)]
    world.update(
        base_state(
            "england", YARD, tick=1000, units=tanks_at(*START), visible_enemies=shown, visible_enemy_buildings=defenses
        )
    )

    met = stepping.met_enemies(list(world.combat_units.values()), world, STEPPED)

    assert sorted(record.id for record in met) == [500, 700]


# -- caught on the move -------------------------------------------------------------------------------------

HOME = (22, 48)
# main retreating home from 45 cells east of it, its rear at x = 68.
RETREATING = tanks_at(67, 48, idle=False)
FIRST_HOME_STEP = (57, 48)
# A medium tank 2 cells behind main's rear: it has a unit of main within its range and 1.
PURSUER = [foe(500, "2tnk", 70, 48)]


def retreating(tuning: Tuning = STEPPED) -> Scene:
    """main told to retreat home from 45 cells east of it, its first step ordered at 1000."""
    scene = Scene(action(RETREAT_HOME, HOME), tuning)
    scene.cycle(1000, RETREATING, PURSUER)
    return scene


def caught_events(records: list[dict]) -> list[dict]:
    return [event for event in records if event["reason"] in (stepping.CAUGHT, stepping.CAUGHT_END)]


def moves(orders: list[dict]) -> list[dict]:
    return [order for order in orders if order["cmd"] in ("move", "attack_move")]


def test_caught_pursued_and_caught_fights_an_enemy_in_range_of_the_rear_for_2_s_turns_the_group_on_it(match_record):
    scene = retreating()

    at_45 = scene.cycle(1045, RETREATING, PURSUER)
    at_50 = scene.cycle(1050, RETREATING, PURSUER)

    assert attacks(at_45) == [] and scene.move is not None
    assert attacks(at_50) and moves(at_50) == []
    (caught,) = caught_events(match_record("combat"))
    assert (caught["by"], caught["cell"]) == (stepping.IN_RANGE, [67, 48])


def test_caught_fights_stronger_pursuers_eight_heavy_tanks_at_its_heels_are_fought_and_main_is_given_no_move():
    heavies = [foe(500 + n, "3tnk", 70 + n % 2, 46 + n // 2) for n in range(8)]
    scene = Scene(action(RETREAT_HOME, HOME))
    scene.cycle(1000, RETREATING, heavies)

    orders = scene.cycle(1050, RETREATING, heavies)

    assert attacks(orders) and moves(orders) == []


def test_caught_not_caught_keeps_moving_an_enemy_10_cells_behind_out_of_its_range_leaves_the_group_stepping_home():
    behind = [foe(500, "2tnk", 78, 48)]
    scene = Scene(action(RETREAT_HOME, HOME))
    scene.cycle(1000, RETREATING, behind)

    orders = scene.cycle(1060, tanks_at(*FIRST_HOME_STEP), behind)

    assert attacks(orders) == [] and not scene.move.is_caught
    assert scene.move.point != FIRST_HOME_STEP


def test_caught_never_through_enemy_three_rifles_3_cells_off_the_next_leg_ahead_are_fought_not_walked_past(
    match_record,
):
    rifles = [foe(500 + n, "e1", 60 + n, 51) for n in range(3)]
    scene = Scene(action(RETREAT_HOME, HOME))

    orders = scene.cycle(1000, RETREATING, rifles)

    assert attacks(orders) and moves(orders) == []
    assert caught_events(match_record("combat"))[0]["by"] == stepping.ON_ROUTE


def test_caught_chase_limit_no_order_cell_lies_more_than_5_cells_from_the_centroid_at_the_catch(match_record):
    scene = retreating()
    scattered = [*RETREATING[:5], own(305, "2tnk", 50, 48)]

    orders = scene.cycle(1050, scattered, PURSUER)

    (caught,) = caught_events(match_record("combat"))
    center = caught["cell"]
    assert all(math.dist(order["to"], center) <= 5 for order in orders if "to" in order)
    # The tank 14 cells off the centroid is a straggler: it joins the fight at the centroid.
    assert {"cmd": "move", "units": [305], "to": center} in orders


def test_caught_resume_after_clear_the_step_goes_on_3_s_after_the_pursuer_is_gone_and_not_at_2_s(match_record):
    scene = retreating()
    scene.cycle(1050, RETREATING, PURSUER)
    scene.cycle(1055, RETREATING, events=[{"seq": 1, "tick": 1055, "kind": "enemy_killed", "count": 1}])

    two_s = scene.cycle(1055 + 2 * TICKS_PER_SECOND, RETREATING)
    three_s = scene.cycle(1055 + 3 * TICKS_PER_SECOND, RETREATING)

    assert moves(two_s) == []
    assert moves_to(three_s, FIRST_HOME_STEP) == [
        {"cmd": "move", "units": list(range(300, 306)), "to": list(FIRST_HOME_STEP)}
    ]
    assert [event["reason"] for event in caught_events(match_record("combat"))] == [
        stepping.CAUGHT,
        stepping.CAUGHT_END,
    ]


def test_caught_override_waits_for_fight_the_retreat_override_gives_no_move_home_while_main_fights(match_record):
    manager = CombatManager()
    agent = new_agent(
        manager,
        tuning=STEPPED,
        settings=RedAlertSettings(model_commander=True),
        commander_chooser=StubChooser(),
    )
    forward = tanks_at(45, 48, idle=False)
    # Heavy tanks on main's way back to the staging point at (32, 48), which main is losing to.
    blocking = [foe(500 + n, "3tnk", 40, 46 + n) for n in range(6)]

    orders = []
    for tick in range(1000, 1060, 5):
        state = base_state("england", YARD, POWER, tick=tick, units=forward, visible_enemies=blocking)
        orders.extend(ask(agent, state))

    kinds = [event["kind"] for event in match_record("commander")]
    assert "override_retreat" in kinds
    assert [order for order in orders if order.get("to") == [32, 48]] == []
    assert attacks(orders)


def test_caught_switch_off_a_pursued_group_keeps_moving_and_fires_nothing():
    scene = retreating(NOT_RETURNING_FIRE)

    orders = scene.cycle(1050, RETREATING, PURSUER)

    assert attacks(orders) == [] and scene.move.is_caught is False


# -- keeping the steps of a move whose destination shifts --------------------------------------------------------------

CONTINUITY_OFF = replace(STEPPED, combat=replace(STEPPED.combat, step_plan_continuity=False))


def stepped_toward(destinations: list[tuple[int, int]], tuning: Tuning, first_tick: int = 1000) -> stepping.Stepper:
    """A stepper followed one cycle a destination, 5 ticks apart, each plan keyed by its destination as the plan's
    advance on an objective is, main standing at the start."""
    world, stepper = World(tracked_items()), stepping.Stepper()
    for number, destination in enumerate(destinations):
        world.update(base_state("england", YARD, tick=first_tick + 5 * number, units=tanks_at(*START)))
        stepper.begin_cycle()
        stepper.follow(
            stepping.Plan(destination, stepping.ADVANCE, destination),
            list(world.combat_units.values()),
            world,
            Terrain(),
            tuning,
        )
        stepper.end_cycle()
    return stepper


@pytest.mark.parametrize("tuning, starts", [(STEPPED, 1), (CONTINUITY_OFF, 6)], ids=["switch on", "switch off"])
def test_step_plan_continuity_a_destination_shifting_a_few_cells_each_cycle_keeps_the_step_plan(
    match_record, tuning, starts
):
    shifting = [(60 + number % 2 * 3, 20 + number) for number in range(6)]

    stepper = stepped_toward(shifting, tuning)

    assert len(steps(match_record("combat"), stepping.START)) == starts
    assert stepper.move.destination == shifting[-1]


def test_step_plan_continuity_a_far_destination_re_plans_only_5_s_after_the_move_was_planned(match_record):
    # Two cycles 5 ticks apart on (60, 20), then (60, 50) from the third cycle on: 30 cells off.
    near_then_far = [(60, 20), (60, 20), *[(60, 50)] * 25]

    stepped_toward(near_then_far, STEPPED)

    started = steps(match_record("combat"), stepping.START)
    assert [(event["tick"], event["destination"]) for event in started] == [(1000, [60, 20]), (1125, [60, 50])]


def test_step_plan_continuity_a_group_of_15_counts_as_arrived_within_6_cells_and_one_of_14_within_4():
    point = (30, 20)
    five_off = [
        Unit(id=300 + n, type="2tnk", x=35, y=20, hp=100, idle=True, activity=None, stance=None) for n in range(15)
    ]

    assert stepping.arrival(five_off, point, COMBAT).share == 1.0
    assert stepping.arrival(five_off[:14], point, COMBAT).share == 0.0
    assert stepping.arrival(five_off, point, CONTINUITY_OFF.combat).share == 0.0
