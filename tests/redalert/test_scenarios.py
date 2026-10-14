"""The scenarios of the whole offline matches do what each of them says, on the fake game, without the agent."""

from __future__ import annotations

import math

import pytest

import fake_game
from fake_game import STEPS_PER_SECOND, FakeRedAlert
from scenarios import hostile_interface, standard
from scenarios.hostile_interface import HostileInterfaceScenario
from scenarios.standard import StandardScenario

MCV_ID = fake_game.FIRST_ACTOR_ID
DEPLOY_MCV = {"type": "orders", "orders": [{"cmd": "deploy", "units": [MCV_ID]}]}
# The pillbox's range of 6 and the agent's coverage margin of 2.
PILLBOX_COVERAGE_CELLS = 8


def standard_game(faction: str = "england", raid: tuple[int, ...] = ()) -> tuple[StandardScenario, FakeRedAlert]:
    scenario = StandardScenario(lambda: raid)
    return scenario, scenario.new_game(faction)


def deployed(game: FakeRedAlert) -> FakeRedAlert:
    """The game with its MCV deployed into a construction yard, as the agent does in the first state."""
    game.play_action(DEPLOY_MCV)
    return game


def step_to(game: FakeRedAlert, seconds: float) -> None:
    while game.tick < seconds * STEPS_PER_SECOND * fake_game.TICKS_PER_STEP:
        game.step()


def enemy_cells(game: FakeRedAlert, enemy_type: str) -> list[tuple[int, int]]:
    return [(enemy["x"], enemy["y"]) for enemy in game.enemies() if enemy["type"] == enemy_type]


def yard_cell(game: FakeRedAlert) -> tuple[int, int]:
    yard = next(building for building in game.own_buildings() if building["type"] == "fact")
    return yard["x"], yard["y"]


# -- the standard scenario: the base, the attacks and the end ----------------------------------------


def test_the_enemy_base_stands_from_the_first_state_out_of_sight():
    _, game = standard_game()

    placed = sorted((enemy["type"], enemy["x"], enemy["y"]) for enemy in game.enemies())

    assert placed == sorted(standard.ENEMY_BASE)
    assert not any(enemy["visible"] for enemy in game.enemies())


def test_the_harvesters_and_refineries_lie_outside_the_pillbox_coverage():
    _, game = standard_game()
    economy = [enemy for enemy in game.enemies() if enemy["type"] in ("harv", "proc")]

    distances = [math.dist((enemy["x"], enemy["y"]), standard.DEFENSE_CELL) for enemy in economy]

    assert len(economy) == 4 and min(distances) > PILLBOX_COVERAGE_CELLS


@pytest.mark.parametrize("faction, enemy", [("england", "russia"), ("russia", "england")])
def test_the_enemy_plays_the_other_family(faction, enemy):
    _, game = standard_game(faction)

    assert (game.faction, game.enemy_faction) == (faction, enemy)


def test_eight_rifles_stand_ten_cells_east_of_the_yard_from_90_s_to_110_s_and_the_yard_is_reported_at_91_s():
    scenario, game = standard_game()
    deployed(game)
    x, y = yard_cell(game)

    step_to(game, 90)
    rush = enemy_cells(game, "e1")
    step_to(game, 91)
    events = [event for event in game.state()["observation"]["events"] if event["kind"] == "under_attack"]
    step_to(game, 110)

    assert rush == [(x + 10, y)] * 8 and len(scenario.attacks[0].ids) == 8
    assert [(event["type"], event["tick"]) for event in events] == [("fact", 91 * 25)]
    assert enemy_cells(game, "e1") == []


def test_four_medium_tanks_stand_east_of_the_yard_from_300_s_to_330_s_and_the_yard_is_reported_at_301_s():
    _, game = standard_game()
    deployed(game)
    x, y = yard_cell(game)

    step_to(game, 300)
    tanks = enemy_cells(game, "2tnk")
    step_to(game, 301)
    reported_at = [event["tick"] for event in game.state()["observation"]["events"] if event["kind"] == "under_attack"]
    step_to(game, 330)

    assert tanks == [(x + 10, y)] * 4 and reported_at[-1] == 301 * 25
    assert enemy_cells(game, "2tnk") == []


def test_the_match_ends_at_1200_s_by_the_time_limit_won_by_the_own_seat():
    _, game = standard_game()
    deployed(game)

    step_to(game, 1199.8)
    before = game.state()["is_terminal"]
    game.step()
    final = game.state()

    assert standard.STEPS == 6000 and before is False
    assert final["is_terminal"] and final["result"]["outcome"] == "win"
    assert final["result"]["reason"] == "time_limit" and final["result"]["winner_seat"] == fake_game.OWN_SEAT


# -- the standard scenario: the raid kills -------------------------------------------------------------


class RaidedGame:
    """The parts of the fake game the kill callback reads and changes, with the raid units put where a test says."""

    def __init__(self, raid_cells: list[tuple[int, int]], harvester_visible: bool = True) -> None:
        self.tick = 0
        self.kills_cost = 0
        self.events: list[dict] = []
        self.units = [{"id": 1 + index, "x": x, "y": y} for index, (x, y) in enumerate(raid_cells)]
        self.harvesters = {
            900: {"id": 900, "type": "harv", "x": 74, "y": 40, "visible": harvester_visible},
            901: {"id": 901, "type": "harv", "x": 76, "y": 40, "visible": False},
        }

    def own_units(self) -> list[dict]:
        return self.units

    def enemies(self) -> list[dict]:
        return [dict(harvester) for harvester in self.harvesters.values()]

    def remove_enemy(self, enemy_id: int) -> None:
        del self.harvesters[enemy_id]

    def emit_event(self, kind: str, **details) -> None:
        self.events.append({"kind": kind, **details})

    def add_kills(self, cost: int) -> None:
        self.kills_cost += cost


def raid_steps(scenario: StandardScenario, game: RaidedGame, steps: int) -> None:
    for _ in range(steps):
        game.tick += fake_game.TICKS_PER_STEP
        scenario.raid_kills(game)


def test_three_raiders_by_a_seen_harvester_for_ten_steps_destroy_it_once():
    game = RaidedGame([(74, 44), (73, 41), (75, 39)])
    scenario = StandardScenario(lambda: (1, 2, 3))

    raid_steps(scenario, game, 9)
    after_nine = (game.kills_cost, list(game.harvesters))
    raid_steps(scenario, game, 20)

    assert after_nine == (0, [900, 901])
    assert game.kills_cost == 1100 and list(game.harvesters) == [901]
    assert game.events == [{"kind": "enemy_killed", "id": 900, "type": "harv", "x": 74, "y": 40}]
    assert scenario.kills == {900: 10 * fake_game.TICKS_PER_STEP}


def test_two_raiders_or_a_harvester_out_of_sight_destroy_nothing():
    two = RaidedGame([(74, 44), (73, 41)])
    unseen = RaidedGame([(74, 44), (73, 41), (75, 39)], harvester_visible=False)

    raid_steps(StandardScenario(lambda: (1, 2)), two, 30)
    raid_steps(StandardScenario(lambda: (1, 2, 3)), unseen, 30)

    assert (two.kills_cost, unseen.kills_cost) == (0, 0)


def test_only_units_of_the_raid_group_count_and_only_within_five_cells():
    # (74, 46) is 6 cells from the harvester at (74, 40).
    one_too_far = RaidedGame([(74, 44), (73, 41), (74, 46)])
    one_not_raiding = RaidedGame([(74, 44), (73, 41), (75, 39)])

    raid_steps(StandardScenario(lambda: (1, 2, 3)), one_too_far, 30)
    raid_steps(StandardScenario(lambda: (1, 2)), one_not_raiding, 30)

    assert (one_too_far.kills_cost, one_not_raiding.kills_cost) == (0, 0)


def test_a_step_without_the_raid_starts_the_ten_steps_over():
    game = RaidedGame([(74, 44), (73, 41), (75, 39)])
    members = [(1, 2, 3)]
    scenario = StandardScenario(lambda: members[0])

    raid_steps(scenario, game, 9)
    members[0] = ()
    raid_steps(scenario, game, 1)
    members[0] = (1, 2, 3)
    raid_steps(scenario, game, 9)

    assert game.kills_cost == 0


# -- the hostile-interface scenario ------------------------------------------------------------------------


class RecordingGame:
    """Records what the scenario's timed steps ask of the game, and when."""

    def __init__(self) -> None:
        self.step = 0
        self.asked: list[tuple] = []

    def __getattr__(self, name: str):
        return lambda *arguments: self.asked.append((self.step, name, *arguments))

    def state(self) -> dict:
        return {"observation": {"economy": {"power_provided": 100, "power_drained": 40}}}

    def emit_event(self, kind: str, **details) -> None:
        self.asked.append((self.step, "emit_event", kind, details))


def test_the_timed_faults_come_at_the_steps_the_scenario_names():
    game = RecordingGame()

    for timed in sorted(HostileInterfaceScenario().steps(), key=lambda timed: timed.step):
        game.step = timed.step
        timed.apply(game)

    assert game.asked == [
        (0, "refuse_place", "powr", 2),
        (100, "lose_batch"),
        (250, "refuse_production", "Infantry"),
        (250, "refuse_production", "Infantry"),
        (250, "refuse_production", "Infantry"),
        (300, "remove_available", "e3"),
        (400, "lose_batch"),
        (500, "skip_event_seq", 5),
        (600, "emit_event", "low_power", {"power_provided": 100, "power_drained": 40}),
    ]
    assert hostile_interface.STEPS == 1500


def move_mcv(game: FakeRedAlert) -> None:
    game.play_action({"type": "orders", "orders": [{"cmd": "move", "units": [MCV_ID], "to": [30, 48]}]})


def at_step_200_with_the_first_lost_batch_spent(scenario: HostileInterfaceScenario) -> FakeRedAlert:
    """A hostile game at step 200 whose batch lost at step 100 was a `stop` that changed nothing anyway."""
    game = scenario.new_game("england")
    while game.tick < 200 * fake_game.TICKS_PER_STEP:
        if game.tick == 100 * fake_game.TICKS_PER_STEP:
            game.play_action({"type": "orders", "orders": [{"cmd": "stop", "units": [MCV_ID]}]})
        game.step()
    return game


def test_a_unit_ordered_to_move_from_step_200_is_gone_in_the_next_step_without_a_loss():
    scenario = HostileInterfaceScenario()
    game = at_step_200_with_the_first_lost_batch_spent(scenario)

    move_mcv(game)
    game.step()
    observation = game.state()["observation"]

    assert observation["units"] == [] and scenario.vanished == (MCV_ID, 201 * fake_game.TICKS_PER_STEP)
    assert observation["military"]["deaths_cost"] == 0 and observation["military"]["units_lost"] == 0
    assert not [event for event in observation["events"] if event["kind"] == "unit_lost"]


def test_a_unit_ordered_to_move_before_step_200_stays():
    scenario = HostileInterfaceScenario()
    game = scenario.new_game("england")

    move_mcv(game)
    for _ in range(250):
        game.step()

    assert [unit["id"] for unit in game.own_units()] == [MCV_ID] and scenario.vanished is None


class MovingGame:
    """The parts of the fake game the vanishing rule reads, with two units that a test orders to move."""

    def __init__(self) -> None:
        self.tick = 200 * fake_game.TICKS_PER_STEP
        self.recorded_orders: list[dict] = []
        self.standing = {7, 8}

    def order_to_move(self, unit_id: int) -> None:
        self.recorded_orders.append({"tick": self.tick, "order": {"cmd": "move", "units": [unit_id], "to": [1, 1]}})
        self.tick += fake_game.TICKS_PER_STEP

    def own_units(self) -> list[dict]:
        return [{"id": unit_id} for unit_id in sorted(self.standing)]

    def remove_own(self, unit_id: int) -> None:
        self.standing.remove(unit_id)


def test_only_one_unit_vanishes_in_the_whole_match():
    scenario, game = HostileInterfaceScenario(), MovingGame()

    game.order_to_move(7)
    scenario.vanish_a_moving_unit(game)
    game.order_to_move(8)
    scenario.vanish_a_moving_unit(game)

    assert game.standing == {8} and scenario.vanished == (7, 201 * fake_game.TICKS_PER_STEP)


def test_a_building_taken_off_the_map_is_gone_without_a_loss_and_a_deploy_makes_nothing_vanish():
    scenario = HostileInterfaceScenario()
    game = at_step_200_with_the_first_lost_batch_spent(scenario)
    deployed(game)
    game.step()
    yard_id = game.own_buildings()[0]["id"]

    game.remove_own(yard_id)
    game.step()

    assert game.own_buildings() == [] and scenario.vanished is None
    assert game.state()["observation"]["military"]["buildings_lost"] == 0


def test_a_hostile_game_has_no_enemy_and_plays_the_faction_given():
    game = HostileInterfaceScenario().new_game("russia")

    assert game.enemies() == [] and game.faction == "russia"
