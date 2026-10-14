"""The test-side fight model: damage, ranges, movement, orders, the callback that drives our side, and the baseline."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from combat_sim import (
    ORDERS_EVERY_TICKS,
    OURS,
    THEIRS,
    Actor,
    CombatSim,
    Outcome,
    actor,
    baseline_assign,
    baseline_orders,
)
from situations import fixture_state, tracked_items
from ucla_agent.games.redalert.batch import Limits, Priority, pack
from ucla_agent.games.redalert.fight import centroid, is_near
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.targeting import FocusFire, targets
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning
from ucla_agent.games.redalert.world import World

BOUNDS = (0, 0, 64, 64)
# Every test that plays a fight in the model, together, has this long.
MAX_MODEL_SECONDS = 20.0
# Every scenario is over well within this: 90 seconds.
SCENARIO_TICKS = 2250
ITEMS = tracked_items()


def ours(actor_id: int, actor_type: str, x: float, y: float) -> Actor:
    return actor(ITEMS, actor_id, OURS, actor_type, (x, y))


def theirs(actor_id: int, actor_type: str, x: float, y: float) -> Actor:
    return actor(ITEMS, actor_id, THEIRS, actor_type, (x, y))


def idle(world: World) -> list[dict]:
    """A callback that orders nothing."""
    return []


def fight(*actors: Actor, drive=idle, ticks: int = TICKS_PER_SECOND):
    """Play the actors against each other for `ticks` and give the outcome."""
    return CombatSim(ITEMS, list(actors), drive, BOUNDS).run(ticks)


# -- damage and ranges -----------------------------------------------------------------------------


def test_an_idle_actor_does_its_dps_against_the_target_s_armour_over_25_to_the_nearest_enemy_in_range_each_tick():
    rifle, near, far = ours(100, "e1", 10, 10), theirs(500, "harv", 14, 10), theirs(501, "harv", 14.5, 10)

    outcome = fight(rifle, near, far)

    # A rifle does 125 a second to armour heavy: 5 a tick, 125 over 25 ticks, all to the nearer harvester.
    assert (outcome.ticks, outcome.winner) == (25, None)
    assert (near.max_hp - near.hp, far.max_hp - far.hp) == (pytest.approx(125.0), 0.0)


@pytest.mark.parametrize("distance, hit", [(2, False), (4, True), (8, True), (12, True), (13, False)])
def test_an_artillery_piece_hits_only_between_its_minimum_range_of_4_and_its_range_of_12(distance, hit):
    artillery, harvester = ours(100, "arty", 10, 10), theirs(500, "harv", 10 + distance, 10)

    fight(artillery, harvester)

    assert (harvester.hp < harvester.max_hp) is hit


def test_a_building_that_is_no_defense_never_fires_and_a_defense_fires_like_a_unit():
    near_plant, near_pillbox = ours(100, "e1", 10, 10), ours(101, "e1", 30, 10)
    plant, pillbox = theirs(500, "powr", 12, 10), theirs(501, "pbox", 32, 10)

    fight(near_plant, near_pillbox, plant, pillbox, ticks=5)

    # A pillbox does 8,571 a second to armour none: 342.84 a tick.
    assert near_plant.hp == near_plant.max_hp
    assert near_pillbox.max_hp - near_pillbox.hp == pytest.approx(5 * 8571 / 25)


def test_an_actor_at_0_hp_is_gone_and_a_side_with_nothing_left_has_lost():
    pillbox, rifle = theirs(500, "pbox", 12, 10), ours(100, "e1", 10, 10)

    outcome = fight(rifle, pillbox, ticks=100)

    # 5,000 hp at 342.84 a tick: gone in the 15th tick. What is left of the pillbox is its cost times its health.
    assert (outcome.ticks, outcome.winner) == (15, THEIRS)
    assert outcome.surviving_value == {OURS: 0, THEIRS: pytest.approx(600 * pillbox.hp / pillbox.max_hp)}


# -- orders and movement ---------------------------------------------------------------------------


def ordering(*orders: dict):
    """A callback that gives these orders the first time it is asked, and nothing after."""
    given = list(orders)

    def drive(world: World) -> list[dict]:
        sent, given[:] = list(given), []
        return sent

    return drive


def test_a_move_goes_in_a_straight_line_at_the_item_s_speed_and_does_not_fire_on_the_way():
    rifle, harvester = ours(100, "e1", 10, 10), theirs(500, "harv", 13, 13)

    fight(rifle, harvester, drive=ordering({"cmd": "move", "units": [100], "to": [20, 10]}))

    # 1.32 cells a second along x, past a harvester in range.
    assert (rifle.x, rifle.y, rifle.order) == (pytest.approx(11.32), 10, ("move", (20.0, 10.0)))
    assert harvester.hp == harvester.max_hp


def test_a_unit_that_reaches_the_cell_of_its_move_stands_idle_there():
    rifle = ours(100, "e1", 10, 10)

    fight(rifle, theirs(500, "powr", 40, 40), drive=ordering({"cmd": "move", "units": [100], "to": [11, 10]}))

    assert (rifle.x, rifle.y, rifle.order) == (11, 10, None)


def test_an_attack_closes_in_on_its_target_until_it_is_in_range_and_fires_at_it_alone():
    rocket = ours(100, "e3", 10, 10)
    near, tank = theirs(500, "harv", 12, 10), theirs(501, "1tnk", 17, 10)

    fight(rocket, near, tank, drive=ordering({"cmd": "attack", "units": [100], "target": 501}), ticks=50)

    # 7 cells off at the start, 5 is in range: two cells at 1.32 a second take 38 ticks; then 12 ticks of fire.
    assert rocket.x == pytest.approx(12, abs=0.06)
    assert tank.max_hp - tank.hp == pytest.approx(12 * 2500 / 25, rel=0.1) and near.hp == near.max_hp


def test_an_attack_on_a_target_that_is_gone_leaves_the_unit_idle():
    rifle, rocket = ours(100, "e1", 10, 10), ours(101, "e3", 10, 11)
    gone = theirs(500, "e1", 30, 10)
    gone.hp = 0

    fight(
        rifle,
        rocket,
        gone,
        theirs(501, "powr", 40, 40),
        drive=ordering({"cmd": "attack", "units": [100, 101], "target": 500}),
    )

    assert (rifle.order, rocket.order, rifle.x) == (None, None, 10)


def test_an_attack_move_goes_on_until_an_enemy_is_in_range_and_fires_at_it():
    rifle, harvester = ours(100, "e1", 10, 10), theirs(500, "harv", 17, 10)

    fight(rifle, harvester, drive=ordering({"cmd": "attack_move", "units": [100], "to": [30, 10]}), ticks=50)

    # Two cells to bring the harvester within 5 take 38 ticks; then 12 ticks of 5 hp.
    assert rifle.x == pytest.approx(12, abs=0.06) and harvester.max_hp - harvester.hp == pytest.approx(60.0)


def test_the_enemy_fires_at_the_nearest_of_ours_in_range_and_goes_on_to_its_order_s_cell_while_none_is():
    tank, near, far = theirs(500, "1tnk", 20, 10), ours(100, "e1", 16, 10), ours(101, "e1", 15, 10)
    walker = theirs(501, "1tnk", 40, 30)
    tank.order = walker.order = ("attack_move", (0.0, 10.0))

    fight(tank, walker, near, far)

    assert (tank.x, near.max_hp - near.hp, far.hp) == (20, pytest.approx(952.0), far.max_hp)
    assert walker.x < 40


def test_an_order_the_model_does_not_take_raises():
    with pytest.raises(ValueError, match="not stop"):
        fight(ours(100, "e1", 10, 10), theirs(500, "powr", 40, 40), drive=ordering({"cmd": "stop", "units": [100]}))


# -- the callback ----------------------------------------------------------------------------------


def test_the_callback_sees_the_fight_as_the_agent_s_world_every_5_ticks():
    seen = []

    def drive(world: World) -> list[dict]:
        seen.append((world.tick, world.units[100], world.enemy_memory[500]))
        return []

    rifle, harvester = ours(100, "e1", 10.4, 9.6), theirs(500, "harv", 14, 10)
    harvester.hp = 0.5 * harvester.max_hp

    fight(rifle, harvester, drive=drive, ticks=20)

    assert [tick for tick, _, _ in seen] == list(range(0, 20, ORDERS_EVERY_TICKS))
    _, unit, record = seen[0]
    assert (unit.type, unit.x, unit.y, unit.hp, unit.idle) == ("e1", 10, 10, 100, True)
    assert (record.type, record.x, record.y, record.hp, record.visible, record.is_building) == (
        "harv",
        14,
        10,
        50,
        True,
        False,
    )


def test_orders_given_to_an_enemy_or_to_an_actor_that_is_gone_change_nothing():
    rifle, tank = ours(100, "e1", 10, 10), theirs(500, "1tnk", 40, 40)
    drive = ordering({"cmd": "move", "units": [500, 999], "to": [0, 0]})

    fight(rifle, tank, drive=drive)

    assert (tank.x, tank.y, tank.order, rifle.order) == (40, 40, None, None)


# -- the replaced hand-out, kept to compare against ------------------------------------------------


def mixed_world() -> World:
    """One heavy tank and seven rifles against a harvester at 20%, a rocket soldier at 30% and a power plant."""
    units = [{"id": 300, "type": "2tnk", "x": 36, "y": 48, "hp": 100, "idle": True}]
    units += [
        {"id": 301 + number, "type": "e1", "x": 40, "y": 45 + number, "hp": 100, "idle": True} for number in range(7)
    ]
    enemies = [
        {"id": 500, "type": "harv", "x": 46, "y": 50, "hp": 20},
        {"id": 501, "type": "e3", "x": 45, "y": 48, "hp": 30},
    ]
    buildings = [{"id": 600, "type": "powr", "x": 47, "y": 46, "hp": 100}]
    world = World(ITEMS)
    world.update(
        fixture_state("opening_mcv", 100, units=units, visible_enemies=enemies, visible_enemy_buildings=buildings)
    )
    return world


def test_the_baseline_hands_the_nearest_units_out_in_class_order_until_their_cost_is_enough():
    world = mixed_world()
    units = list(world.units.values())

    # The baseline's coverage at an overkill of 1.5, which its counts are worked out in.
    tuning = replace(Tuning(), combat=replace(CombatTuning(), overkill_factor=1.5))

    assignments = baseline_assign(units, targets(units, centroid(units), world, tuning), world, tuning)

    assert [(assignment.target.type, len(assignment.attackers)) for assignment in assignments] == [
        ("e3", 2),
        ("harv", 4),
        ("powr", 2),
    ]


def test_the_baseline_s_orders_are_one_attack_a_target_and_an_attack_move_for_the_units_left_over():
    rifle = {"id": 500, "type": "e1", "x": 46, "y": 48, "hp": 100}
    tanks = [
        {"id": 300 + n, "type": "1tnk", "x": x, "y": y, "hp": 100, "idle": True}
        for n, (x, y) in enumerate(((40, 48), (41, 48), (40, 49), (41, 49)))
    ]
    world = World(ITEMS)
    world.update(fixture_state("opening_mcv", 100, units=tanks, visible_enemies=[rifle]))
    units = list(world.units.values())

    intents = baseline_orders(units, centroid(units), world, Tuning())

    assert [(intent.priority, dict(intent.order)) for intent in intents] == [
        (Priority.CONTACT, {"cmd": "attack", "units": [301], "target": 500}),
        (Priority.MOVE, {"cmd": "attack_move", "units": [300, 302, 303], "to": [46, 48]}),
    ]


# -- the old and the new assignment in three fights ------------------------------------------------

# Each army is given in the order the requirement names it and stands in that order, front to back: our side in
# columns of six facing east, the enemy in columns of six 12 cells further east, coming on with `attack_move`.
SCENARIOS = {
    "A original": (("e1",) * 20 + ("e3",) * 10, ("1tnk",) * 4 + ("2tnk",) * 4 + ("e1",) * 15),
    "B cost-equal": (("e1",) * 20 + ("e3",) * 10, ("1tnk",) * 3 + ("2tnk",) * 2 + ("e1",) * 9),
    "C infantry against armor": (("e3",) * 10 + ("e1",) * 10, ("2tnk",) * 4 + ("e1",) * 6),
}
OLD, NEW = "old", "new"
COLUMN = 6
OUR_FRONT_X, ENEMY_FRONT_X, TOP_Y = 14, 26, 27
# Where the enemy goes: the middle of our block.
OUR_MIDDLE = (12.0, 29.5)


def scenario_armies(our_army: tuple[str, ...], enemy_army: tuple[str, ...]) -> list[Actor]:
    """Our army numbered from 100 and the enemy's from 500, standing as `SCENARIOS` describes."""
    army = [
        ours(100 + number, kind, OUR_FRONT_X - number // COLUMN, TOP_Y + number % COLUMN)
        for number, kind in enumerate(our_army)
    ]
    enemy = [
        theirs(500 + number, kind, ENEMY_FRONT_X + number // COLUMN, TOP_Y + number % COLUMN)
        for number, kind in enumerate(enemy_army)
    ]
    for one in enemy:
        one.order = ("attack_move", OUR_MIDDLE)
    return army + enemy


def driven_by(orders_of, tuning: Tuning | None = None):
    """Our side's callback: an `attack_move` on the enemy until one is in contact distance, then `orders_of`, packed,
    under `tuning` (the shipped one when None)."""
    tuning = tuning or Tuning()

    def drive(world: World) -> list[dict]:
        units = list(world.combat_units.values())
        middle = centroid(units)
        enemies = list(world.enemy_memory.values())
        if not any(is_near(middle, record.x, record.y, tuning.combat.contact_cells) for record in enemies):
            goal = centroid(enemies)
            return [
                {"cmd": "attack_move", "units": [unit.id for unit in units], "to": [round(goal[0]), round(goal[1])]}
            ]
        return pack(orders_of(units, middle, world, tuning), world, Limits()).orders

    return drive


def play(scenario: str) -> dict[str, Outcome]:
    """A scenario played with the replaced hand-out and with the effectiveness-based assignment."""
    our_army, enemy_army = SCENARIOS[scenario]
    return {
        assignment: CombatSim(ITEMS, scenario_armies(our_army, enemy_army), driven_by(orders_of), BOUNDS).run(
            SCENARIO_TICKS
        )
        for assignment, orders_of in ((OLD, baseline_orders), (NEW, FocusFire().orders))
    }


@pytest.fixture(scope="module")
def fights() -> tuple[dict[str, dict[str, Outcome]], float]:
    """Every scenario played with both assignments, and how many seconds that took."""
    started = time.perf_counter()
    outcomes = {scenario: play(scenario) for scenario in SCENARIOS}
    return outcomes, time.perf_counter() - started


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_in_every_scenario_the_new_assignment_comes_out_with_more_net_value_than_the_old(fights, scenario):
    outcomes, _ = fights

    old, new = outcomes[scenario][OLD], outcomes[scenario][NEW]

    assert new.net_value > old.net_value


def test_in_the_cost_equal_scenario_our_side_wins_with_the_new_assignment(fights):
    outcomes, _ = fights

    assert outcomes["B cost-equal"][NEW].winner == OURS


def test_the_armies_cost_what_the_scenarios_say(fights):
    outcomes, _ = fights

    starting = {scenario: dict(outcomes[scenario][NEW].starting_value) for scenario in SCENARIOS}

    assert starting == {
        "A original": {OURS: 5000, THEIRS: 7700},
        "B cost-equal": {OURS: 5000, THEIRS: 4700},
        "C infantry against armor": {OURS: 4000, THEIRS: 4000},
    }


def test_a_fight_played_again_is_the_same_fight_and_every_fight_of_the_model_takes_at_most_20_seconds(
    fights, without_counter
):
    outcomes, played_seconds = fights
    _, played_without_counter_seconds = without_counter
    started = time.perf_counter()

    again = {scenario: play(scenario) for scenario in SCENARIOS}

    assert again == outcomes
    assert played_seconds + played_without_counter_seconds + time.perf_counter() - started <= MAX_MODEL_SECONDS


# -- the counter factor in the three fights --------------------------------------------------------

# The tuning with a counter power of 0, by which the new assignment weighs every pairing without the counter factor.
COUNTER_OFF = replace(Tuning(), combat=replace(CombatTuning(), counter_power=0.0))


@pytest.fixture(scope="module")
def without_counter() -> tuple[dict[str, Outcome], float]:
    """Every scenario played with the new assignment without the counter factor, and how many seconds that took."""
    started = time.perf_counter()
    outcomes = {
        scenario: CombatSim(
            ITEMS, scenario_armies(*SCENARIOS[scenario]), driven_by(FocusFire().orders, COUNTER_OFF), BOUNDS
        ).run(SCENARIO_TICKS)
        for scenario in SCENARIOS
    }
    return outcomes, time.perf_counter() - started


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_counter_factor_in_every_scenario_the_new_assignment_keeps_at_least_the_net_value_it_has_without_it(
    fights, without_counter, scenario
):
    # Net value without the factor and with it: A -1,470 and -1,470, B 3,208 and 3,208, C 2,545 and 2,545.
    outcomes, _ = fights
    without, _ = without_counter

    assert outcomes[scenario][NEW].net_value >= without[scenario].net_value
