"""The siege: the firing band, the screen line, falling back, the reissue limits, screen before advance, the siege of
a defended base against a frontal attack in the fight model, the march while clear, and the slots of the long-range
units out of contact."""

from __future__ import annotations

import copy
import math
import time
from dataclasses import replace
from pathlib import Path

import pytest

from combat_sim import GUARD, ORDERS_EVERY_TICKS, OURS, THEIRS, Actor, CombatSim, actor
from fake_game import MAP_ID, FakeRedAlert, at_seconds
from situations import StubChooser, ask, base_state, fixture_state, new_agent, tracked_items
from switches_off import SWITCHES_OFF
from ucla_agent.core.config import ConfigError
from ucla_agent.games.redalert.batch import Limits, Priority, pack
from ucla_agent.games.redalert.combat import ENGAGE, RETREAT, CombatManager
from ucla_agent.games.redalert.composition import FINISH, PRESERVE, PRESS, STRIKE
from ucla_agent.games.redalert.fight import centroid
from ucla_agent.games.redalert.groups import MAIN, Groups
from ucla_agent.games.redalert.items import TICKS_PER_SECOND, is_air_only
from ucla_agent.games.redalert.ledger import OrderLedger
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.marching import is_clear
from ucla_agent.games.redalert.siege import (
    Band,
    Siege,
    SlotLine,
    band_depth,
    firing_band,
    has_screen,
    march_slot_line,
    screen_front,
    screen_line,
    slot_cells,
)
from ucla_agent.games.redalert.tuning import FRONTAL, SCREENED, TUNING_FILE, SiegeTuning, Tuning
from ucla_agent.games.redalert.world import World
from whole_match import COMPUTE_P95_MS, WHOLE_MATCH_STEPS, WholeMatch, play_whole_match

REPO_ROOT = Path(__file__).resolve().parents[2]
ITEMS = tracked_items()
TUNING = SWITCHES_OFF
BOUNDS = (0, 0, 64, 64)
# Every fight of this file in the model, together, has this long.
MAX_MODEL_SECONDS = 20.0


def own(unit_id: int, unit_type: str, x: int, y: int, hp: int = 100, *, idle: bool = True) -> dict:
    """One own unit as an observation lists it."""
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": hp, "act": None if idle else "move", "idle": idle}


def foe(enemy_id: int, enemy_type: str, x: int, y: int, hp: int = 100) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": hp}


def seen(units=(), enemies=(), buildings=(), tick: int = 100, items=ITEMS, world: World | None = None) -> World:
    """A world after a state of the fixture's map (our base at (22, 48)) showing exactly these units and enemies."""
    world = world or World(items)
    world.update(
        fixture_state(
            "opening_mcv",
            tick,
            units=list(units),
            visible_enemies=list(enemies),
            visible_enemy_buildings=list(buildings),
        )
    )
    return world


def unit_list(world: World) -> list:
    """The world's own units that fight."""
    return list(world.combat_units.values())


# -- the [siege] table -----------------------------------------------------------------------------


def test_the_siege_table_holds_the_requirement_s_values_and_the_tracked_file_holds_them_too():
    expected = SiegeTuning(
        mode=SCREENED,
        band_inner_margin=0.5,
        band_min_range_margin=1,
        band_defense_margin=0.5,
        screen_gap_arty=2,
        screen_gap_v2rl=5,
        screen_defense_margin=1,
        defense_priority_weight=1.5,
        fallback_margin=2,
        fallback_step=4,
        reissue_cells=2,
        reissue_ticks=25,
        screen_ratio_allies=1.0,
        screen_ratio_soviet=1.0,
    )

    assert Tuning().siege == expected and Tuning.from_file(REPO_ROOT).siege == expected


@pytest.mark.parametrize("mode, read", [('"frontal"', FRONTAL), ('"flanking"', None)], ids=["frontal", "another"])
def test_the_siege_mode_is_screened_or_frontal_and_no_other(tmp_path, mode, read):
    tuning_file = tmp_path / TUNING_FILE
    tuning_file.parent.mkdir(parents=True)
    tuning_file.write_text(
        (REPO_ROOT / TUNING_FILE).read_text(encoding="utf-8").replace('mode = "screened"', f"mode = {mode}"),
        encoding="utf-8",
    )

    if read is None:
        with pytest.raises(ConfigError, match=r"mode in \[siege\]"):
            Tuning.from_file(tmp_path)
    else:
        assert Tuning.from_file(tmp_path).siege.mode == read


# -- 1. the firing band ----------------------------------------------------------------------------


def test_1_an_arty_with_a_known_pillbox_at_the_target_gets_the_band_6_5_to_11_5():
    world = seen([own(300, "arty", 30, 48)], buildings=[foe(600, "pbox", 44, 48)])

    assert firing_band("arty", (44, 48), world, TUNING.siege) == Band(6.5, 11.5)


def test_1_a_v2_near_a_known_tesla_coil_gets_the_band_7_5_to_9_5():
    world = seen([own(300, "v2rl", 36, 48)], buildings=[foe(600, "tsla", 44, 48)])

    assert firing_band("v2rl", (44, 48), world, TUNING.siege) == Band(7.5, 9.5)


def test_1_with_no_defense_the_band_starts_a_cell_past_the_minimum_range_and_a_defense_off_the_target_counts():
    world = seen([own(300, "arty", 30, 48)], buildings=[foe(600, "powr", 44, 48), foe(601, "pbox", 44, 51)])
    empty = seen([own(300, "arty", 30, 48)], buildings=[foe(600, "powr", 44, 48)])

    # The pillbox 3 cells off the power plant: its range 6, half a cell, and the 3 cells between them.
    assert firing_band("arty", (44, 48), empty, TUNING.siege) == Band(5.0, 11.5)
    assert firing_band("arty", (44, 48), world, TUNING.siege) == Band(9.5, 11.5)


def test_1_a_v2_whose_band_would_be_empty_does_not_engage():
    # A test-only defense of range 9.5: the band would run from 10 to 9.5.
    items = ITEMS.with_game_config({"rules": {"buildings": {"tsla": {"weapon": {"range": 9.5}}}}})
    world = seen([own(300, "v2rl", 35, 48)], buildings=[foe(600, "tsla", 44, 48)], items=items)

    band = firing_band("v2rl", (44, 48), world, TUNING.siege)
    orders = Siege().orders(unit_list(world), [], (44, 48), True, world, TUNING)

    assert band == Band(10.0, 9.5) and band.is_empty
    assert orders == []


# -- 2. a stand-off kill in the fight model --------------------------------------------------------


def ours(actor_id: int, actor_type: str, x: float, y: float) -> Actor:
    return actor(ITEMS, actor_id, OURS, actor_type, (x, y))


def theirs(actor_id: int, actor_type: str, x: float, y: float) -> Actor:
    return actor(ITEMS, actor_id, THEIRS, actor_type, (x, y))


@pytest.fixture(scope="module")
def model_seconds() -> list[float]:
    """The seconds each fight of the module took in the model."""
    return []


def played(sim: CombatSim, ticks: int, seconds: list[float]):
    """Play a fight to `ticks` and note how long it took."""
    started = time.perf_counter()
    outcome = sim.run(ticks)
    seconds.append(time.perf_counter() - started)
    return outcome


def test_2_one_arty_sent_against_a_pillbox_from_15_cells_destroys_it_from_its_band_untouched(model_seconds):
    arty, pillbox = ours(100, "arty", 15, 32), theirs(500, "pbox", 30, 32)
    siege, nearest = Siege(), []

    def drive(world: World) -> list:
        nearest.append(arty.distance_to(pillbox.x, pillbox.y))
        return pack(siege.orders(unit_list(world), [], (30, 32), True, world, TUNING), world, Limits()).orders

    sim = CombatSim(ITEMS, [arty, pillbox], drive, BOUNDS)
    outcome = played(sim, 2000, model_seconds)

    # 40,000 hp at 1,300 a second take 31 s from the band, after 2 s to drive the 3.5 cells into it.
    assert outcome.winner == OURS and outcome.ticks < 1000
    assert min([*nearest, arty.distance_to(pillbox.x, pillbox.y)]) > 6
    assert 6.5 <= arty.distance_to(pillbox.x, pillbox.y) <= 11.5 and arty.hp == arty.max_hp
    assert sim.orders_received[100] == {"move": 1, "attack": 1}


# -- 3. the screen line ----------------------------------------------------------------------------

YARD = ("fact", 22, 48)
STAGING = [32, 48]
GUN = (60, 48)
SIEGE_IDS = [300, 301]
SCREEN_IDS = [302, 303, *range(304, 310)]


def at(tick: int, units=(), enemies=(), buildings=(), faction: str = "england") -> dict:
    """A state of a base of that faction at that tick with these own units and these enemies in sight."""
    shown = {"units": list(units), "visible_enemies": list(enemies), "visible_enemy_buildings": list(buildings)}
    return base_state(faction, YARD, tick=tick, **shown)


def main_at(x: int, y: int = 48, siege_type: str = "arty", screen: tuple = ("2tnk",) * 2 + ("e1",) * 6) -> list[dict]:
    """Two long-range units at (x, y - 1) and (x, y + 1), and a screen of these types a cell ahead of them."""
    units = [own(300, siege_type, x, y - 1), own(301, siege_type, x, y + 1)]
    return units + [own(302 + number, kind, x + 1, y - 3 + number) for number, kind in enumerate(screen)]


def combat_agent(minimum: int = 1000, tuning: Tuning | None = None, plan: str | None = None):
    """An agent with the combat manager alone, main going forward at `minimum`, the guard taking no rifle; under one
    plan when one is given, else under the agent's own plan rules."""
    manager = CombatManager()
    agent = new_agent(manager, tuning=tuning, plan_selector=None if plan is None else Always(plan))
    agent.ctx.groups.set_guard_quota("e1", 0)
    agent.ctx.min_attack_strength = lambda plan: minimum
    return agent, manager


def test_3_with_main_advancing_on_a_gun_turret_every_screen_order_is_at_least_7_5_cells_from_it():
    # Forward staging off: main goes straight on from the staging point, its screen holding the line.
    agent, _ = combat_agent(tuning=replace(Tuning(), combat=replace(Tuning().combat, forward_staging=False)))
    gun = [foe(600, "gun", *GUN)]
    first = ask(agent, at(1000, main_at(32), buildings=gun))
    second = ask(agent, at(1005, main_at(52), buildings=gun))

    screen_orders = [order for order in first + second if set(order["units"]) & set(SCREEN_IDS)]
    moved_to = [order["to"] for order in screen_orders if order["cmd"] == "move"]

    assert agent.ctx.army == frozenset(SIEGE_IDS + SCREEN_IDS)
    # The line 2 cells ahead of the artillery at (52, 48) would be 6 cells from the gun; it is held back to 8.
    assert moved_to == [[34, 48], [52, 48]]
    assert all(math.dist(cell, GUN) >= 7.5 for cell in moved_to)
    assert all(order["cmd"] == "move" for order in screen_orders)


def test_the_screen_line_stands_2_cells_ahead_of_artillery_and_5_of_v2s_toward_the_objective():
    artillery = seen([own(300, "arty", 30, 47), own(301, "arty", 30, 49)])
    v2s = seen([own(300, "v2rl", 30, 47), own(301, "v2rl", 30, 49)])

    assert screen_line(unit_list(artillery), (60, 48), artillery, TUNING.siege).cell == (32, 48)
    assert screen_line(unit_list(v2s), (60, 48), v2s, TUNING.siege).cell == (35, 48)


def test_no_long_range_unit_is_sent_ahead_of_the_screen_line_or_of_the_screen():
    # The screen stands a cell behind the artillery, whose band on the power plant is 20 cells ahead.
    world = seen(
        [own(300, "arty", 30, 47), own(301, "arty", 30, 49), own(302, "e1", 29, 48), own(303, "e1", 29, 49)],
        buildings=[foe(600, "powr", 60, 48)],
    )
    units = unit_list(world)

    orders = [intent.order for intent in Siege().orders(units[:2], units[2:], (60, 48), True, world, TUNING)]

    long_range = [order["to"] for order in orders if order["units"][0] in (300, 301)]
    assert {"cmd": "move", "units": [302, 303], "to": [32, 48]} in orders
    assert long_range and all(cell[0] <= 29 for cell in long_range)


def screen_cycle(siege: Siege, tick: int, x: int, world: World, enemies=(), buildings=()) -> list[dict]:
    """The screen's orders when the artillery stands at x, its two rifles idle a cell ahead, the objective at x 60."""
    units = [own(300, "arty", x, 47), own(301, "arty", x, 49), own(302, "e1", x + 2, 48), own(303, "e1", x + 2, 49)]
    world = seen(units, enemies, buildings, tick=tick, world=world)
    found = unit_list(world)
    orders = [intent.order for intent in siege.orders(found[:2], found[2:], (60, 48), True, world, TUNING)]
    return [order for order in orders if set(order["units"]) & {302, 303}]


def test_a_screen_unit_is_sent_to_its_line_once_and_again_each_time_the_line_moves():
    siege, world = Siege(), World(ITEMS)

    sent = [screen_cycle(siege, tick, x, world) for tick, x in ((100, 30), (105, 30), (110, 32))]

    # At (32, 48) and (32, 49) the rifles stand at the line, idle: it is not ordered again until it moves.
    assert sent == [
        [{"cmd": "move", "units": [302, 303], "to": [32, 48]}],
        [],
        [{"cmd": "move", "units": [302, 303], "to": [34, 48]}],
    ]


def test_the_screen_attacks_only_the_enemy_units_it_is_in_contact_with_and_never_a_building():
    # A rifle 5 cells from the screen's rifle at (32, 48), another 9 cells off, and a pillbox 4 cells off.
    enemies, buildings = [foe(500, "e1", 37, 48), foe(501, "e1", 41, 49)], [foe(600, "pbox", 35, 51)]

    sent = screen_cycle(Siege(), 100, 30, World(ITEMS), enemies, buildings)

    assert sent == [{"cmd": "attack", "units": [302, 303], "target": 500}]


# -- 4. falling back --------------------------------------------------------------------------------

RIFLES = [foe(500, "e1", 42, 48), foe(501, "e1", 42, 49), foe(502, "e1", 43, 48)]


def test_4_rifles_closing_to_6_cells_of_an_arty_send_that_arty_alone_4_cells_toward_the_base():
    agent, manager = combat_agent()
    # The artillery at (36, 48) and (36, 50), the screen at (38, 45) to (38, 52); the nearest rifle is 6 cells from
    # the first piece and 6.1 from the second.
    units = [own(300, "arty", 36, 48), own(301, "arty", 36, 50)]
    units += [own(302 + number, kind, 38, 45 + number) for number, kind in enumerate(("2tnk",) * 2 + ("e1",) * 6)]

    orders = ask(agent, at(1000, units, RIFLES))

    fall_backs = [order for order in orders if order["cmd"] == "move" and order["to"][0] < 36]
    screen_back = [order for order in orders if set(order["units"]) & set(SCREEN_IDS) and order.get("to", [99])[0] < 38]
    assert fall_backs == [{"cmd": "move", "units": [300], "to": [32, 48]}]
    assert screen_back == [] and manager.withdrawn == frozenset()


def siege_cycle(siege: Siege, tick: int, units: list[dict], enemies: list[dict], world: World | None = None):
    """The orders the siege gives a lone long-range subgroup at a tick, with its objective 30 cells east."""
    world = seen(units, enemies, tick=tick, world=world)
    return world, [intent.order for intent in siege.orders(unit_list(world), [], (66, 48), True, world, TUNING)]


def test_an_arty_falls_back_once_and_then_fires_while_the_enemy_stays_close():
    siege = Siege()
    world, first = siege_cycle(siege, 100, [own(300, "arty", 36, 48)], [foe(500, "e1", 42, 48)])
    _, later = siege_cycle(siege, 200, [own(300, "arty", 32, 48)], [foe(500, "e1", 37, 48)], world)

    assert first == [{"cmd": "move", "units": [300], "to": [32, 48]}]
    assert later == [{"cmd": "attack", "units": [300], "target": 500}]


def test_a_v2_falls_back_again_from_infantry_once_it_stands_but_only_once_from_a_vehicle():
    for enemy_type, second_move in (("e1", [{"cmd": "move", "units": [300], "to": [28, 48]}]), ("1tnk", [])):
        siege = Siege()
        world, first = siege_cycle(siege, 100, [own(300, "v2rl", 36, 48)], [foe(500, enemy_type, 42, 48)])
        moving = seen([own(300, "v2rl", 34, 48, idle=False)], [foe(500, enemy_type, 39, 48)], tick=150, world=world)
        busy = siege.orders(unit_list(moving), [], (66, 48), True, moving, TUNING)
        _, second = siege_cycle(siege, 200, [own(300, "v2rl", 32, 48)], [foe(500, enemy_type, 37, 48)], world)

        assert first == [{"cmd": "move", "units": [300], "to": [32, 48]}]
        # A fall-back is carried out to its end before anything else is ordered.
        assert busy == []
        assert [order for order in second if order["cmd"] == "move"] == second_move


def test_with_no_base_known_a_long_range_unit_falls_back_straight_away_from_the_nearest_enemy():
    world = seen([own(300, "arty", 36, 48)], [foe(500, "e1", 36, 54), foe(501, "e1", 40, 54)])
    world.base_center = None

    sent = [intent.order for intent in Siege().orders(unit_list(world), [], (66, 48), True, world, TUNING)]

    assert sent == [{"cmd": "move", "units": [300], "to": [36, 44]}]


def test_a_long_range_unit_in_the_band_of_its_objective_with_nothing_in_sight_stays_where_it_is():
    world = seen([own(300, "arty", 51, 48)])

    assert Siege().orders(unit_list(world), [], (60, 48), True, world, TUNING) == []


def test_with_no_line_clear_of_the_defenses_the_screen_holds_none_and_the_artillery_does_not_advance():
    # A tesla coil (range 7 and a cell) reaches every cell from 2 cells behind the artillery to 2 ahead.
    world = seen([own(300, "arty", 30, 48), own(301, "e1", 29, 48)], buildings=[foe(600, "tsla", 31, 52)], items=ITEMS)
    units = unit_list(world)

    assert screen_line(units[:1], (60, 48), world, TUNING.siege) is None
    assert Siege().orders(units[:1], units[1:], (60, 48), True, world, TUNING) == []


def test_a_target_of_a_type_without_known_hit_points_comes_after_a_known_one():
    world = seen([own(300, "arty", 30, 48)], [foe(500, "e1", 39, 48)], [foe(600, "v01", 39, 47)])

    sent = [intent.order for intent in Siege().orders(unit_list(world), [], (60, 48), True, world, TUNING)]

    assert sent == [{"cmd": "attack", "units": [300], "target": 500}]


def test_a_siege_subgroup_with_a_yak_and_a_rifle_in_its_band_fires_on_the_rifle_and_never_on_the_aircraft():
    world = seen([own(300, "arty", 30, 48)], [foe(500, "yak", 39, 48), foe(501, "e1", 39, 49)])

    sent = [intent.order for intent in Siege().orders(unit_list(world), [], (60, 48), True, world, TUNING)]

    # Both stand 9 cells off, inside the band of 5 to 11.5. Weighed by its light armour the yak would be worth 3,119
    # / 6,000 = 0.52 a pairing to the artillery, more than the rifle's 2,169 / 5,000; an artillery piece has no
    # anti-air weapon, and does it nothing.
    assert sent == [{"cmd": "attack", "units": [300], "target": 501}]


@pytest.mark.parametrize("air_targeting, attacks_the_yak", [(True, False), (False, True)], ids=["on", "off"])
def test_a_lone_aircraft_in_the_band_is_fired_on_only_with_air_targeting_off(air_targeting, attacks_the_yak):
    world = seen([own(300, "arty", 30, 48)], [foe(500, "yak", 39, 48)])
    tuning = replace(TUNING, combat=replace(TUNING.combat, air_targeting=air_targeting))

    sent = [intent.order for intent in Siege().orders(unit_list(world), [], (60, 48), True, world, tuning)]

    assert ({"cmd": "attack", "units": [300], "target": 500} in sent) is attacks_the_yak


def test_a_long_range_unit_is_never_sent_into_a_known_defense_s_range():
    # Falling back toward the base would take the artillery to (32, 48), 2 cells from a pillbox.
    world = seen([own(300, "arty", 36, 48)], [foe(500, "e1", 42, 48)], [foe(600, "pbox", 30, 48)])

    sent = [intent.order for intent in Siege().orders(unit_list(world), [], (66, 48), True, world, TUNING)]

    assert [order for order in sent if order["cmd"] == "move"] == []


# -- 5. the reissue limits ---------------------------------------------------------------------------


def test_5_over_100_ticks_of_a_slowly_moving_target_each_long_range_unit_gets_at_most_4_moves(model_seconds):
    artillery = [ours(100, "arty", 10, 30), ours(101, "arty", 10, 34)]
    # A heavy tank, at 1.05 cells a second the slowest vehicle, driving away from the artillery.
    tank = theirs(500, "4tnk", 30, 32)
    tank.order = ("attack_move", (63.0, 32.0))
    siege, desired = Siege(), set()

    def drive(world: World) -> list:
        target = world.enemy_memory[500]
        desired.add((target.x, target.y))
        return pack(
            siege.orders(unit_list(world), [], (target.x, target.y), True, world, TUNING), world, Limits()
        ).orders

    sim = CombatSim(ITEMS, [*artillery, tank], drive, BOUNDS)
    played(sim, 100, model_seconds)

    moves = [sim.orders_received[unit_id]["move"] for unit_id in (100, 101)]
    # The target stood on more cells than that, each with a band cell of its own to move to.
    assert len(desired) > 4
    assert all(1 <= count <= 4 for count in moves)


# -- 6. screen before advance ------------------------------------------------------------------------


def gathered(screen: tuple[str, ...]) -> list[dict]:
    """Two artillery pieces ($1,700) and a screen of these types, all at the staging point."""
    units = [own(300, "arty", 32, 47), own(301, "arty", 32, 49)]
    return units + [own(302 + number, kind, 33, 45 + number % 7) for number, kind in enumerate(screen)]


@pytest.mark.parametrize(
    "screen, advances",
    [(("e1",) * 10, False), (("2tnk",) * 2, True)],
    ids=["a $1,000 screen", "a $1,700 screen"],
)
def test_6_allied_artillery_goes_beyond_the_staging_point_only_with_a_screen_worth_as_much(screen, advances):
    agent, _ = combat_agent()
    units = gathered(screen)
    ids = frozenset(unit["id"] for unit in units)

    orders = ask(agent, at(1000, units))

    assert agent.ctx.army == (ids if advances else frozenset())
    if advances:
        # The screen goes to its line 2 cells ahead; the artillery no farther than the screen stands.
        assert {"cmd": "move", "units": [302, 303], "to": [34, 48]} in orders
        assert all(order["to"][0] <= 33 for order in orders if order["units"][0] in (300, 301))
    else:
        assert orders == []


def test_soviet_v2s_need_a_screen_worth_as_much():
    world = seen([own(300, "v2rl", 32, 47), own(301, "3tnk", 33, 47), own(302, "e3", 33, 49)])
    v2, screen = unit_list(world)[:1], unit_list(world)[1:]
    world.family = "soviet"
    twice = replace(TUNING.siege, screen_ratio_soviet=2.0)

    # Against one V2 ($900) a heavy tank ($1,150) is worth as much and a rocket soldier ($300) is not; against twice
    # the V2 the two of them ($1,450) are not either.
    assert has_screen(v2, screen[:1], world, TUNING.siege) is True
    assert has_screen(v2, screen[1:], world, TUNING.siege) is False
    assert has_screen(v2, screen, world, twice) is False


@pytest.mark.parametrize("hp, advances", [(81, False), (99, True)], ids=["a screen of 0.9", "a screen of 1.1"])
def test_under_press_a_soviet_army_waits_at_the_staging_point_for_a_screen_worth_as_much_as_its_v2s(hp, advances):
    # Two V2s ($1,800) and a mammoth tank ($2,000) at 81% ($1,620, 0.9 times the V2s) or at 99% ($1,980, 1.1 times).
    agent, _ = combat_agent(plan=PRESS)
    units = [own(300, "v2rl", 32, 47), own(301, "v2rl", 32, 49), own(302, "4tnk", 33, 48, hp)]

    orders = ask(agent, at(1000, units, faction="russia"))

    assert agent.ctx.army == (frozenset({300, 301, 302}) if advances else frozenset())
    assert (orders != []) is advances


# -- orders, retreat and the frontal mode --------------------------------------------------------------


def test_long_range_moves_and_attacks_are_contact_orders_and_the_screen_line_a_screen_order():
    agent, _ = combat_agent()
    ask(agent, at(1000, main_at(32)))
    ask(agent, at(1030, main_at(36), [foe(500, "e1", 45, 47)]))

    classes = {
        (entry.order["cmd"], entry.priority, entry.order["units"][0] in SIEGE_IDS) for entry in agent.ledger.entries
    }

    assert ("move", Priority.SCREEN, False) in classes
    assert ("attack", Priority.CONTACT, True) in classes
    assert {priority for cmd, priority, is_siege in classes if is_siege} == {Priority.CONTACT}


def test_long_range_units_retreat_with_main():
    agent, manager = combat_agent()
    tanks = [foe(500 + number, "3tnk", 44, 44 + number) for number in range(6)]

    orders = ask(agent, at(1000, main_at(40), tanks))

    assert manager.mode == RETREAT
    assert orders == [{"cmd": "move", "units": SIEGE_IDS + SCREEN_IDS, "to": STAGING}]


# Vehicle repair off: the heavy tanks losing health stay main's screen, so what the screen is worth falls with them.
NO_VEHICLE_REPAIR = replace(Tuning(), combat=replace(Tuning().combat, vehicle_repair=False))


def gone_forward_with_a_worn_screen() -> list[dict]:
    """main gone forward from the staging point, its heavy tanks down to 40%: worth 680 against the artillery's 1,700."""
    return [
        own(300, "arty", 44, 47),
        own(301, "arty", 44, 49),
        own(302, "2tnk", 46, 47, 40),
        own(303, "2tnk", 46, 49, 40),
    ]


@pytest.mark.parametrize("plan", [PRESS, STRIKE])
def test_an_army_gone_forward_whose_screen_is_no_longer_worth_enough_comes_back_to_the_staging_point(plan):
    agent, _ = combat_agent(tuning=NO_VEHICLE_REPAIR, plan=plan)
    ask(agent, at(1000, gathered(("2tnk",) * 2)))

    orders = ask(agent, at(1005, gone_forward_with_a_worn_screen()))

    assert agent.ctx.army == frozenset() and orders == [{"cmd": "move", "units": [300, 301, 302, 303], "to": STAGING}]


def test_under_finish_an_army_gone_forward_whose_screen_is_no_longer_worth_enough_stays_forward():
    # FINISH ends the match only by going on: the worn screen ($680 against $1,700) sends no one back to staging.
    agent, _ = combat_agent(tuning=NO_VEHICLE_REPAIR, plan=FINISH)
    ask(agent, at(1000, gathered(("2tnk",) * 2)))

    orders = ask(agent, at(1005, gone_forward_with_a_worn_screen()))

    assert agent.ctx.army == frozenset({300, 301, 302, 303})
    assert orders and all(order.get("to") != STAGING for order in orders)


def finish_army() -> list[dict]:
    """13 V2s ($11,700) at the staging point, screened by 9 heavy tanks ($10,350) and 19 rocket soldiers at 65%
    ($3,705): a screen worth 1.2 times the V2s."""
    v2s = [own(300 + number, "v2rl", 30 + number % 2, 45 + number // 2) for number in range(13)]
    tanks = [own(320 + number, "3tnk", 33, 44 + number) for number in range(9)]
    rockets = [own(340 + number, "e3", 34 + number % 2, 44 + number // 2, 65) for number in range(19)]
    return [*v2s, *tanks, *rockets]


@pytest.mark.parametrize(
    "plan, ratio, sets_out",
    [(FINISH, 1.0, True), (FINISH, 2.0, True), (PRESS, 1.0, True), (PRESS, 2.0, False)],
    ids=["FINISH, ratio 1", "FINISH, ratio 2", "PRESS, ratio 1", "PRESS, ratio 2"],
)
def test_13_v2s_screened_at_1_2_times_set_out_under_finish_whatever_the_screen_ratio(plan, ratio, sets_out):
    tuning = replace(Tuning(), siege=replace(Tuning().siege, screen_ratio_soviet=ratio))
    agent, _ = combat_agent(tuning=tuning, plan=plan)
    units = finish_army()

    orders = ask(agent, at(1000, units, faction="russia"))

    v2_ids = set(range(300, 313))
    screen_ids = [unit["id"] for unit in units if unit["id"] not in v2_ids]
    assert agent.ctx.army == (frozenset(unit["id"] for unit in units) if sets_out else frozenset())
    assert (orders != []) is sets_out and all(order.get("to") != STAGING for order in orders)
    # Set out, the screen attack-moves on the enemy's start and the siege sends the V2s to their slots behind it.
    assert ({"cmd": "attack_move", "units": screen_ids, "to": [74, 48]} in orders) is sets_out
    assert all(order["cmd"] == "move" for order in orders if set(order["units"]) & v2_ids)


def test_with_vehicle_repair_main_s_artillery_comes_back_to_the_staging_point_when_its_screen_leaves_for_repair():
    # The heavy tanks lost 60% in 5 ticks, so they would be lost within repair_ttd_seconds: they leave main for repair
    # and get no order of main's. Their repair zone is the staging point, with no mechanic, and the artillery, left
    # with no screen, goes back there in a move of its own.
    agent, manager = combat_agent()
    ask(agent, at(1000, gathered(("2tnk",) * 2)))

    orders = ask(agent, at(1005, gone_forward_with_a_worn_screen()))

    assert manager.repairing == frozenset({302, 303}) and agent.ctx.army == frozenset()
    assert len(orders) == 2
    assert {"cmd": "move", "units": [300, 301], "to": STAGING} in orders
    assert {"cmd": "move", "units": [302, 303], "to": STAGING} in orders


def test_engaged_by_defenses_it_cannot_take_main_s_siege_fires_on_the_nearest_and_the_screen_holds_toward_it():
    # Two guns 4 cells apart cover each other: 1.3 x 2 x 1,600 is more than main's 3,400, so there is no objective.
    agent, manager = combat_agent()
    guns = [foe(600, "gun", 43, 46), foe(601, "gun", 43, 50)]

    orders = ask(agent, at(1000, gathered(("2tnk",) * 2), buildings=guns))

    assert manager.mode == ENGAGE and agent.ctx.army == frozenset()
    # The nearer gun, at (43, 46): the line 2 cells toward it from the artillery at (32, 48) is (34, 48).
    assert {"cmd": "attack", "units": [300], "target": 600} in orders
    assert {"cmd": "move", "units": [302, 303], "to": [34, 48]} in orders


def test_aa_march_engaged_by_defenses_it_cannot_take_main_s_screen_holds_toward_the_pillbox_not_a_nearer_aa_gun():
    # The pillbox and the gun turret cover the AA gun and each other: 1.3 x 2 x 1,400 is more than main's 3,400, so
    # there is no objective. The AA gun is the nearest defense, 8.5 cells off; the pillbox, 11 cells off, is the
    # nearest one that fires at ground units, and the line 2 cells toward it from the artillery is (34, 48).
    agent, manager = combat_agent()
    defenses = [foe(600, "agun", 38, 42), foe(601, "pbox", 43, 48), foe(602, "gun", 44, 44)]

    orders = ask(agent, at(1000, gathered(("2tnk",) * 2), buildings=defenses))

    assert manager.mode == ENGAGE and agent.ctx.army == frozenset()
    assert {"cmd": "move", "units": [302, 303], "to": [34, 48]} in orders


def test_under_base_defense_s_override_main_with_artillery_fights_as_one_group():
    agent, _ = combat_agent()
    agent.ctx.groups.set_defense_override(MAIN)
    units = [own(300, "arty", 35, 48), *[own(302 + number, "e1", 38, 46 + number) for number in range(6)]]

    orders = ask(agent, at(1000, units, RIFLES))

    # The artillery, 7 cells from a rifle and so outside its minimum range and margin, is not sent back by the siege:
    # it attacks with the rest.
    assert [order for order in orders if order["cmd"] == "move"] == []
    assert 300 in {unit_id for order in orders if order["cmd"] == "attack" for unit_id in order["units"]}


class Always:
    """A plan rule that keeps one plan."""

    def __init__(self, plan: str) -> None:
        self.plan = plan

    def select(self, world: World, ctx: MatchContext, choice: object) -> str:
        return self.plan


def test_under_preserve_main_s_artillery_near_the_base_fires_from_its_band_behind_the_screen():
    manager = CombatManager()
    agent = new_agent(manager, plan_selector=Always(PRESERVE))
    agent.ctx.groups.set_guard_quota("e1", 0)
    units = [own(300, "arty", 30, 47), own(301, "arty", 30, 49), *[own(302 + n, "e1", 32, 45 + n) for n in range(6)]]

    orders = ask(agent, at(1000, units, [foe(500, "e1", 40, 48), foe(501, "e1", 40, 49)]))

    assert agent.current_plan == PRESERVE and manager.mode == ENGAGE
    assert {"cmd": "move", "units": list(range(302, 308)), "to": [32, 48]} in orders
    assert {order["cmd"] for order in orders if order["units"][0] in SIEGE_IDS} == {"attack"}


def test_in_frontal_mode_main_with_artillery_advances_as_one_group():
    frontal = replace(TUNING, siege=replace(TUNING.siege, mode=FRONTAL))
    agent, _ = combat_agent(tuning=frontal)

    orders = ask(agent, at(1000, gathered(("e1",) * 2)))

    assert orders == [{"cmd": "attack_move", "units": [300, 301, 302, 303], "to": [74, 48]}]


def test_the_siege_subgroup_is_main_s_artillery_and_v2s_in_the_order_they_joined():
    groups = Groups()
    groups.set_guard_quota("e1", 0)
    world = seen([own(300, "e1", 30, 48), own(301, "v2rl", 30, 49), own(302, "arty", 30, 50), own(303, "2tnk", 30, 51)])
    groups.update(world)
    groups.assign(302, "raid")

    assert groups.siege_members(MAIN) == [301]
    assert groups.siege_members("raid") == [302]


# -- the fight model's extensions for the siege ------------------------------------------------------


def idle(world: World) -> list:
    """A callback that orders nothing."""
    return []


def test_a_guard_stands_until_one_of_ours_comes_within_its_radius_and_then_closes_in_on_the_nearest(model_seconds):
    rifle, far = ours(100, "e1", 10, 10), ours(101, "e1", 10, 30)
    guard = theirs(500, "e1", 22, 10)
    guard.order = (GUARD, 10)
    sim = CombatSim(ITEMS, [rifle, far, guard], idle, BOUNDS)

    played(sim, 25, model_seconds)
    standing = (guard.x, guard.y)
    rifle.x = 14
    played(sim, 100, model_seconds)

    # 12 cells off it stands; with the rifle 8 cells off it closes in until the rifle is within its range of 5.
    assert standing == (22, 10) and guard.x == pytest.approx(19, abs=0.06) and guard.y == 10


def test_the_world_forgets_a_building_the_model_destroyed_and_is_told_where_the_bases_are(model_seconds):
    rifle, plant = ours(100, "e1", 10, 10), theirs(500, "powr", 12, 10)
    plant.hp = 1.0
    seen_worlds = []

    def drive(world: World) -> list:
        seen_worlds.append((world.tick, set(world.enemy_memory), world.base_center, world.enemy_base))
        return []

    sim = CombatSim(ITEMS, [rifle, plant, theirs(501, "e1", 40, 40)], drive, BOUNDS, (4, 32), (54, 32))
    played(sim, 10, model_seconds)

    assert seen_worlds[0] == (0, {500, 501}, (4, 32), (54, 32))
    assert seen_worlds[-1] == (5, {501}, (4, 32), (54, 32))


def test_the_model_counts_the_orders_each_of_our_units_was_given_by_command(model_seconds):
    given = iter(
        [[{"cmd": "move", "units": [100, 101], "to": [20, 10]}], [{"cmd": "attack_move", "units": [100], "to": [5, 5]}]]
    )
    sim = CombatSim(
        ITEMS,
        [ours(100, "e1", 10, 10), ours(101, "e1", 10, 11), theirs(500, "powr", 40, 40)],
        lambda world: next(given, []),
        BOUNDS,
    )

    played(sim, 15, model_seconds)

    assert sim.orders_received == {100: {"move": 1, "attack_move": 1}, 101: {"move": 1}}


# -- 7. a siege of a defended base against a frontal attack ----------------------------------------

BASE_CENTER, ENEMY_START = (4, 32), (54, 32)
# The defending army waits by its defenses and comes out once one of ours is this close.
DEFENDERS_COME_OUT_CELLS = 12
SIEGE_TICKS = 6000


def siege_armies() -> list[Actor]:
    """Our 2 arty, 6 e1 and 2 2tnk ($4,000) at the staging point; one pbox, one gun, a 1tnk and 8 e1 ($1,500)."""
    army = [("arty", 13, 31), ("arty", 13, 33), *[("e1", 15, 29 + n) for n in range(6)], ("2tnk", 15, 28)]
    army.append(("2tnk", 15, 36))
    base = [("pbox", 44, 30), ("gun", 44, 34), ("1tnk", 47, 32)]
    base += [("e1", 47 + n // 4, 29 + n % 4 * 2) for n in range(8)]
    defenders = [theirs(500 + number, kind, x, y) for number, (kind, x, y) in enumerate(base)]
    for one in defenders:
        if not one.is_building:
            one.order = (GUARD, DEFENDERS_COME_OUT_CELLS)
    return [ours(100 + number, kind, x, y) for number, (kind, x, y) in enumerate(army)] + defenders


def attacking(mode: str, recorded: list):
    """Our side's callback: the combat manager under PRESS with the siege in a mode, its intents kept in `recorded`."""
    tuning = replace(TUNING, siege=replace(TUNING.siege, mode=mode))
    ctx = MatchContext(tuning, OrderLedger(), StubChooser())
    ctx.groups.set_guard_quota("e1", 0)
    ctx.plan = PRESS
    manager = CombatManager()

    def drive(world: World) -> list:
        ctx.groups.update(world)
        intents = manager.intents(world, ctx)
        recorded.append(({record.id for record in world.known_enemy_defenses}, intents))
        return pack(intents, world, Limits()).orders

    return drive


@pytest.fixture(scope="module")
def base_attacks(model_seconds) -> dict:
    """The defended base attacked in each mode: the outcome, our actors, and every cycle's intents."""
    attacks = {}
    for mode in (SCREENED, FRONTAL):
        actors, recorded = siege_armies(), []
        sim = CombatSim(ITEMS, actors, attacking(mode, recorded), BOUNDS, BASE_CENTER, ENEMY_START)
        attacks[mode] = (played(sim, SIEGE_TICKS, model_seconds), actors, recorded)
    return attacks


def test_7_the_armies_cost_4000_and_2900():
    assert {side: sum(one.cost for one in siege_armies() if one.side == side) for side in (OURS, THEIRS)} == {
        OURS: 4000,
        THEIRS: 600 + 800 + 1500,
    }


def test_7_the_siege_destroys_both_defenses_and_keeps_more_value_than_the_frontal_attack(base_attacks):
    siege, siege_actors, _ = base_attacks[SCREENED]
    frontal, _, _ = base_attacks[FRONTAL]
    defenses = [one for one in siege_actors if one.type in ("pbox", "gun")]

    assert siege.winner == OURS and not any(one.is_alive for one in defenses)
    assert siege.surviving_value[OURS] > frontal.surviving_value[OURS]


def test_7_the_artillery_survives_the_siege(base_attacks):
    _, actors, _ = base_attacks[SCREENED]

    assert [one.hp / one.max_hp for one in actors if one.type == "arty"] == [1.0, 1.0]


def test_7_no_long_range_unit_is_sent_into_a_defense_s_range_and_the_screen_line_stays_out_of_it(base_attacks):
    _, actors, recorded = base_attacks[SCREENED]
    defenses = {one.id: (one.x, one.y, one.range) for one in actors if one.type in ("pbox", "gun")}
    long_range = {one.id for one in actors if one.type == "arty"}

    def is_covered(known: set[int], cell: list, margin: float) -> bool:
        return any(math.dist(cell, defenses[known_id][:2]) < defenses[known_id][2] + margin for known_id in known)

    siege_moves = [
        (known, intent.order["to"])
        for known, intents in recorded
        for intent in intents
        if intent.order["cmd"] == "move" and set(intent.order["units"]) <= long_range
    ]
    lines = [
        (known, intent.order["to"])
        for known, intents in recorded
        for intent in intents
        if intent.priority == Priority.SCREEN
    ]

    assert siege_moves and lines
    assert not [cell for known, cell in siege_moves if is_covered(known, cell, 0.0)]
    assert not [cell for known, cell in lines if is_covered(known, cell, TUNING.siege.screen_defense_margin)]


def test_every_fight_of_the_siege_model_together_takes_at_most_20_seconds(base_attacks, model_seconds):
    assert sum(model_seconds) <= MAX_MODEL_SECONDS


# -- marching while clear: march_when_clear of [siege] ---------------------------------------------------------

MARCH_OBJECTIVE = (74, 48)
V2_IDS = [300, 301]
MARCH_SCREEN_IDS = list(range(302, 310))


def v2_main(x: int = 32, *, idle: bool = True) -> list[dict]:
    """Two V2s at x, and eight heavy tanks ($9,200) two cells behind them: at the staging point with x 32."""
    units = [own(300, "v2rl", x, 47, idle=idle), own(301, "v2rl", x, 49, idle=idle)]
    return units + [own(302 + number, "3tnk", x - 2, 44 + number, idle=idle) for number in range(8)]


def v2s_ahead_of_their_screen(x: int = 32, *, idle: bool = True, tick: int = 100, world: World | None = None) -> World:
    """A world showing `v2_main`."""
    return seen(v2_main(x, idle=idle), tick=tick, world=world)


def split(world: World) -> tuple[list, list]:
    """The world's V2s and the rest of its units, their screen."""
    units = unit_list(world)
    return [unit for unit in units if unit.id in V2_IDS], [unit for unit in units if unit.id not in V2_IDS]


def test_march_when_clear_the_screen_attack_moves_on_the_objective_and_the_v2s_take_their_slots_behind_its_front():
    world = v2s_ahead_of_their_screen()
    v2s, screen = split(world)

    marched = [intent.order for intent in Siege().march(v2s, screen, MARCH_OBJECTIVE, world, TUNING)]
    screened = [intent.order for intent in Siege().orders(v2s, screen, MARCH_OBJECTIVE, True, world, TUNING)]

    # The screen's front stands at x 30, 2 cells behind the V2s at x 32: their slots lie 7.25 cells behind it, at x
    # 22.75, 2 cells apart. Holding its line, 5 cells ahead of them, the screen holds them back to its centroid.
    assert marched[0] == {"cmd": "attack_move", "units": MARCH_SCREEN_IDS, "to": list(MARCH_OBJECTIVE)}
    assert marched[1:] == [
        {"cmd": "move", "units": [300], "to": [23, 47]},
        {"cmd": "move", "units": [301], "to": [23, 49]},
    ]
    assert {"cmd": "move", "units": MARCH_SCREEN_IDS, "to": [37, 48]} in screened
    assert all(order["to"][0] <= 30 for order in screened if order["units"][0] in V2_IDS)


def test_march_when_clear_a_screen_unit_is_sent_again_only_idle_away_from_the_objective_or_to_a_new_one():
    siege, world = Siege(), World(ITEMS)

    def screen_orders(tick: int, objective, idle: bool, stopped: int | None = None, x: int = 32) -> list[dict]:
        moved = v2s_ahead_of_their_screen(x, idle=idle, tick=tick, world=world)
        if stopped is not None:
            moved.combat_units[stopped] = replace(moved.combat_units[stopped], idle=True)
        v2s, screen = split(moved)
        orders = [intent.order for intent in siege.march(v2s, screen, objective, moved, TUNING)]
        return [order for order in orders if order["units"][0] not in V2_IDS]

    first = screen_orders(100, MARCH_OBJECTIVE, idle=True)
    on_their_way = screen_orders(105, MARCH_OBJECTIVE, idle=False)
    one_stopped = screen_orders(110, MARCH_OBJECTIVE, idle=False, stopped=305)
    new_objective = screen_orders(115, (74, 40), idle=False)
    near_one = screen_orders(120, (31, 44), idle=True, x=33)
    stood_near_it = screen_orders(125, (31, 44), idle=True, x=33)

    assert first == [{"cmd": "attack_move", "units": MARCH_SCREEN_IDS, "to": [74, 48]}]
    assert on_their_way == []
    assert one_stopped == [{"cmd": "attack_move", "units": [305], "to": [74, 48]}]
    assert new_objective == [{"cmd": "attack_move", "units": MARCH_SCREEN_IDS, "to": [74, 40]}]
    assert near_one == [{"cmd": "attack_move", "units": MARCH_SCREEN_IDS, "to": [31, 44]}]
    # Idle at (31, 44) to (31, 51): only the tank at (31, 51) stands more than 6 cells from the objective.
    assert stood_near_it == [{"cmd": "attack_move", "units": [309], "to": [31, 44]}]


def test_march_when_clear_the_line_and_the_march_each_send_the_screen_again_after_the_other():
    siege, world = Siege(), World(ITEMS)

    def cycle(tick: int, marching: bool) -> list[dict]:
        moved = v2s_ahead_of_their_screen(idle=False, tick=tick, world=world)
        v2s, screen = split(moved)
        if marching:
            intents = siege.march(v2s, screen, MARCH_OBJECTIVE, moved, TUNING)
        else:
            intents = siege.orders(v2s, screen, MARCH_OBJECTIVE, True, moved, TUNING)
        return [intent.order["cmd"] for intent in intents if intent.order["units"][0] not in V2_IDS]

    sent = [cycle(tick, marching) for tick, marching in ((100, True), (105, False), (110, False), (115, True))]

    assert sent == [["attack_move"], ["move"], [], ["attack_move"]]


@pytest.mark.parametrize(
    "enemies, buildings, clear",
    [
        ([foe(500, "e1", 42, 48)], [], False),
        ([foe(500, "e1", 43, 48)], [], True),
        ([], [foe(600, "pbox", 30, 36)], False),
        ([], [foe(600, "powr", 30, 36)], True),
    ],
    ids=["a rifle 12 cells off", "a rifle 13 cells off", "a pillbox 12 cells off", "a power plant 12 cells off"],
)
def test_march_when_clear_the_army_is_clear_with_no_enemy_unit_or_defense_within_12_cells_of_any_unit(
    enemies, buildings, clear
):
    world = seen([own(300, "3tnk", 30, 48), own(301, "3tnk", 20, 48)], enemies, buildings)

    assert is_clear(unit_list(world), world, TUNING.siege.march_blocked_cells) is clear


MARCH_ON = replace(TUNING, siege=replace(TUNING.siege, march_when_clear=True))


def marching_agent(plan: str = PRESS, tuning: Tuning = MARCH_ON) -> tuple:
    """An agent with the combat manager alone under a plan, main going forward at 1,000 and the guard taking no rifle,
    and the plan rule, whose plan a test may change."""
    rule = Always(plan)
    agent = new_agent(CombatManager(), tuning=tuning, plan_selector=rule)
    agent.ctx.groups.set_guard_quota("e1", 0)
    agent.ctx.min_attack_strength = lambda plan: 1000
    return agent, rule


def test_march_when_clear_main_advancing_with_no_enemy_near_sends_its_screen_on_the_objective_and_its_v2s_to_slots():
    agent, _ = marching_agent()

    orders = ask(agent, at(1000, v2_main(), faction="russia"))

    # The screen stands 2 cells behind the V2s at the staging point: the V2s go to their slots 7.25 cells behind it.
    assert agent.ctx.army == frozenset(V2_IDS + MARCH_SCREEN_IDS)
    assert {"cmd": "attack_move", "units": MARCH_SCREEN_IDS, "to": [74, 48]} in orders
    assert [order["to"] for order in orders if order["units"][0] in V2_IDS] == [[23, 47], [23, 49]]


@pytest.mark.parametrize(
    "plan, enemies, buildings",
    [(FINISH, [foe(500, "e1", 36, 58)], []), (PRESS, [], [foe(600, "pbox", 36, 60)])],
    ids=["a rifle off main's path under FINISH", "a pillbox under PRESS"],
)
def test_march_when_clear_with_an_enemy_within_12_cells_of_a_unit_the_orders_are_today_s_screened_ones(
    plan, enemies, buildings
):
    sent = []
    for switch in (True, False):
        agent, _ = marching_agent(plan, replace(TUNING, siege=replace(TUNING.siege, march_when_clear=switch)))
        sent.append(ask(agent, at(1000, v2_main(), enemies, buildings, faction="russia")))
        assert agent.ctx.army == frozenset(V2_IDS + MARCH_SCREEN_IDS)

    assert sent[0] == sent[1] and sent[0]
    assert "attack_move" not in {order["cmd"] for order in sent[0]}


def test_march_when_clear_under_a_plan_that_does_not_attack_the_screen_holds_its_line_again():
    agent, rule = marching_agent()
    ask(agent, at(1000, v2_main(), faction="russia"))
    rule.plan = "HOLD"

    orders = ask(agent, at(1005, v2_main(40, idle=False), faction="russia"))

    assert agent.current_plan == "HOLD" and agent.ctx.army == frozenset(V2_IDS + MARCH_SCREEN_IDS)
    assert {"cmd": "move", "units": MARCH_SCREEN_IDS, "to": [45, 48]} in orders
    assert "attack_move" not in {order["cmd"] for order in orders}


# -- air-only defenses never stop the march ----------------------------------------------------------------------

# The item table as a game's reference object leaves it: the AA gun and the SAM site with the weapon ranges the rules
# give them, of weapons that reach aircraft alone.
GAME_ITEMS = ITEMS.with_game_config(
    {"rules": {"buildings": {"agun": {"weapon": {"range": 10}}, "sam": {"weapon": {"range": 7.5}}}}}
)


def test_aa_march_the_air_only_defenses_are_the_aa_gun_and_the_sam_site_by_their_roles():
    defenses = ["pbox", "hbox", "gun", "ftur", "tsla", "agun", "sam", "unknown"]

    assert [name for name in defenses if is_air_only(name, GAME_ITEMS)] == ["agun", "sam"]


@pytest.mark.parametrize(
    "defense, clear", [("agun", True), ("sam", True), ("pbox", False)], ids=["an AA gun", "a SAM site", "a pillbox"]
)
def test_aa_march_an_air_only_defense_10_cells_from_a_unit_leaves_the_army_clear_and_a_pillbox_does_not(defense, clear):
    units = [own(300, "3tnk", 30, 48), own(301, "3tnk", 20, 48)]
    world = seen(units, buildings=[foe(600, defense, 30, 58)], items=GAME_ITEMS)

    assert is_clear(unit_list(world), world, TUNING.siege.march_blocked_cells) is clear


@pytest.mark.parametrize("defense, marches", [("agun", True), ("pbox", False)], ids=["an AA gun", "a pillbox"])
def test_aa_march_main_marches_past_an_aa_gun_9_cells_from_a_tank_and_holds_its_screen_line_at_a_pillbox(
    defense, marches
):
    agent, _ = marching_agent()

    # 9.1 cells from the heavy tank at (30, 51), and 12.4 from main's centroid, beyond its fight's reach; the
    # objective is the enemy's yard, nearer to main than the defense and outside the pillbox's cover.
    buildings = [foe(600, defense, 31, 60), foe(601, "fact", 42, 48)]
    orders = ask(agent, at(1000, v2_main(), buildings=buildings, faction="russia"))

    assert agent.ctx.army == frozenset(V2_IDS + MARCH_SCREEN_IDS)
    assert ({"cmd": "attack_move", "units": MARCH_SCREEN_IDS, "to": [42, 48]} in orders) is marches


@pytest.mark.parametrize(
    "defense, cell", [("agun", (32, 48)), ("sam", (32, 48)), ("pbox", (29, 48))], ids=["AA gun", "SAM site", "pillbox"]
)
def test_aa_march_an_air_only_defense_with_a_range_of_its_own_pushes_back_no_screen_line(defense, cell):
    # The line stands 2 cells ahead of the artillery with no defense near; a pillbox 6 cells ahead pushes it back.
    artillery = [own(300, "arty", 30, 47), own(301, "arty", 30, 49)]
    world = seen(artillery, buildings=[foe(600, defense, 36, 48)], items=GAME_ITEMS)

    assert screen_line(unit_list(world), (60, 48), world, TUNING.siege).cell == cell


def test_aa_march_an_air_only_defense_with_a_range_of_its_own_narrows_no_firing_band():
    guarded = seen(buildings=[foe(600, "agun", 60, 48), foe(601, "sam", 58, 48)], items=GAME_ITEMS)

    assert firing_band("arty", (60, 48), guarded, TUNING.siege) == firing_band(
        "arty", (60, 48), seen(items=GAME_ITEMS), TUNING.siege
    )


# -- marching while clear: the stall check ---------------------------------------------------------------------

# main as `v2_main` places it, gaining 2 cells from the staging point in the 375 ticks from 1000 to 1375.
STALLING = ((1000, 32), (1200, 33), (1370, 34), (1375, 34))
ALL_OF_MAIN = V2_IDS + MARCH_SCREEN_IDS


def marched(agent, tick: int, x: int, enemies=()) -> list[dict]:
    """The orders for main as `v2_main` places it at x, idle at the staging point and on its way anywhere else."""
    return ask(agent, at(tick, v2_main(x, idle=x == 32), enemies, faction="russia"))


def v2_commands(orders: list[dict]) -> set[str]:
    """The commands of the orders that name a V2."""
    return {order["cmd"] for order in orders if set(order["units"]) & set(V2_IDS)}


def test_march_when_clear_a_clear_advance_gaining_under_5_cells_in_375_ticks_goes_frontal_with_a_siege_event(
    match_record,
):
    agent, _ = marching_agent()

    sent = [marched(agent, tick, x) for tick, x in STALLING]

    assert not [orders for orders in sent[:3] if "attack_move" in v2_commands(orders)]
    assert sent[3] == [{"cmd": "attack_move", "units": ALL_OF_MAIN, "to": [74, 48]}]
    # The centroid went from (30.4, 47.6) to (32.4, 47.6), 41.6 cells from the enemy's start.
    assert match_record("siege") == [
        {
            "tick": 1375,
            "reason": "stall_frontal",
            "plan": PRESS,
            "objective": [74, 48],
            "cell": [32, 48],
            "gained_cells": 2.0,
        }
    ]


def test_march_when_clear_a_frontal_advance_goes_on_until_an_enemy_comes_within_12_cells_and_then_marches_again():
    agent, _ = marching_agent()
    for tick, x in STALLING:
        marched(agent, tick, x)

    frontal = marched(agent, 1460, 36)
    # A rifle 11 cells from the heavy tank at (34, 51), and 14.4 from main's centroid: no fight, but contact.
    in_contact = marched(agent, 1465, 36, [foe(500, "e1", 34, 62)])
    clear_again = marched(agent, 1550, 50)

    assert frontal == [{"cmd": "attack_move", "units": ALL_OF_MAIN, "to": [74, 48]}]
    assert {"cmd": "move", "units": MARCH_SCREEN_IDS, "to": [41, 48]} in in_contact
    assert {"cmd": "attack_move", "units": MARCH_SCREEN_IDS, "to": [74, 48]} in clear_again
    assert v2_commands(clear_again) == {"move"}


def test_march_when_clear_a_frontal_advance_marches_again_under_a_new_plan():
    agent, rule = marching_agent()
    for tick, x in STALLING:
        marched(agent, tick, x)
    rule.plan = FINISH

    after = marched(agent, 1460, 36)

    assert agent.current_plan == FINISH and v2_commands(after) == {"move"}


@pytest.mark.parametrize(
    "cycles",
    [
        ((1000, 32), (1200, 35), (1375, 38), (1600, 41), (1750, 44)),
        ((1000, 32), (1200, 50), (1375, 75), (1600, 75), (1750, 75), (1900, 75), (2125, 75)),
    ],
    ids=["gaining 6 cells a window", "within 5 cells of the objective"],
)
def test_march_when_clear_an_advance_gaining_5_cells_or_standing_within_5_of_its_objective_never_goes_frontal(
    match_record, cycles
):
    agent, _ = marching_agent()

    sent = [marched(agent, tick, x) for tick, x in cycles]

    assert "attack_move" not in {command for orders in sent for command in v2_commands(orders)}
    assert match_record("siege") == []


def test_march_when_clear_with_the_switch_off_a_stalled_advance_holds_its_line_and_never_goes_frontal(match_record):
    agent, _ = marching_agent(tuning=TUNING)

    sent = [marched(agent, tick, x) for tick, x in (*STALLING, (1460, 34), (1750, 34))]

    assert "attack_move" not in {order["cmd"] for orders in sent for order in orders}
    assert match_record("siege") == []


# -- marching while clear: clear from 15 cells, blocked within 12 ----------------------------------------------


def screen_moves(orders: list[dict]) -> list[dict]:
    """The `move` orders of a cycle that name a unit of main's screen: the screen holding its line."""
    return [order for order in orders if order["cmd"] == "move" and set(order["units"]) & set(MARCH_SCREEN_IDS)]


def test_march_hysteresis_a_contact_13_cells_off_after_clear_does_not_end_it_and_one_11_cells_off_does():
    agent, _ = marching_agent()
    marched(agent, 1000, 32)

    # With main at x 33, the heavy tank at (31, 51) is the nearest to the rifle, which is 15.1 cells or more from the
    # V2s and over 12 from main's centroid.
    at_13 = marched(agent, 1005, 33, [foe(500, "e1", 31, 64)])
    at_11 = marched(agent, 1010, 33, [foe(500, "e1", 31, 62)])

    assert screen_moves(at_13) == []
    assert screen_moves(at_11) == [{"cmd": "move", "units": MARCH_SCREEN_IDS, "to": [38, 48]}]


def test_march_hysteresis_an_advance_not_yet_clear_holds_its_line_with_a_rifle_14_cells_off_and_marches_at_16():
    agent, _ = marching_agent()

    at_14 = marched(agent, 1000, 32, [foe(500, "e1", 30, 65)])
    at_16 = marched(agent, 1005, 32, [foe(500, "e1", 30, 67)])

    assert agent.ctx.army == frozenset(ALL_OF_MAIN)
    assert screen_moves(at_14) == [{"cmd": "move", "units": MARCH_SCREEN_IDS, "to": [37, 48]}]
    assert {"cmd": "attack_move", "units": MARCH_SCREEN_IDS, "to": [74, 48]} in at_16


def test_march_hysteresis_the_stall_window_survives_a_contact_flicker_and_goes_frontal_with_a_siege_event(
    match_record,
):
    agent, _ = marching_agent()
    # A rifle comes within 11 cells of a tank at 1200, and is seen 19 cells off from 1370 on.
    near, gone = [foe(500, "e1", 31, 62)], [foe(500, "e1", 34, 70)]
    flicker = ((1000, 32, []), (1200, 33, near), (1370, 34, gone), (1375, 34, gone))

    sent = [marched(agent, tick, x, enemies) for tick, x, enemies in flicker]

    # Frontal at 1375: the V2s attack-move too; the screen, sent on the objective at 1370, keeps that order.
    assert screen_moves(sent[1]) and "attack_move" not in v2_commands(sent[2])
    assert sent[3] == [{"cmd": "attack_move", "units": V2_IDS, "to": [74, 48]}]
    assert [(logged["tick"], logged["reason"], logged["gained_cells"]) for logged in match_record("siege")] == [
        (1375, "stall_frontal", 2.0)
    ]


# -- marching while clear: a whole fake match under FINISH -----------------------------------------------------

# The enemy's start of a game reference whose two starts lie 60 cells apart: the fake game's own spawn at (22, 48),
# where the base stands, and this cell. No enemy stands anywhere on the map.
FAR_OWN_START, FAR_ENEMY_START = (22, 48), (82, 48)
FINISH_FROM_TICK = 1500
REACHED_CELLS = 15
REACHED_WITHIN_TICKS = 3000


class FinishFrom:
    """A plan rule: OPENING, then FINISH from a tick on."""

    def __init__(self, tick: int) -> None:
        self.tick = tick

    def select(self, world: World, ctx: MatchContext, choice: object) -> str:
        return FINISH if world.tick >= self.tick else "OPENING"


def far_starts(game_config: dict) -> dict:
    """A copy of the fake game's reference whose enemy start lies 60 cells east of the own spawn."""
    changed = copy.deepcopy(game_config)
    changed["rules"]["maps"][MAP_ID]["spawns"] = [list(FAR_OWN_START), list(FAR_ENEMY_START)]
    return changed


def v2_army(game: FakeRedAlert) -> None:
    """Four V2s ($3,600) and eight heavy tanks ($9,200) at the staging point, 10 cells east of the base."""
    for number in range(4):
        game.add_own("v2rl", 32, 46 + number)
    for number in range(8):
        game.add_own("3tnk", 33, 44 + number)


@pytest.fixture(scope="module")
def finish_march() -> tuple[WholeMatch, list[tuple[int, float]]]:
    """A whole fake Soviet match with march_when_clear on, a V2 army made when FINISH starts, and how far main's
    centroid stood from the enemy's start after each cycle from then on, by tick."""
    distances = []

    def watch(agent) -> None:
        world = agent.world
        main = [world.combat_units[unit_id] for unit_id in agent.ctx.groups.members(MAIN)]
        if world.tick >= FINISH_FROM_TICK and main:
            distances.append((world.tick, math.dist(centroid(main), FAR_ENEMY_START)))

    match = play_whole_match(
        MARCH_ON,
        "russia",
        game_config=far_starts(FakeRedAlert().game_config),
        scenario=[at_seconds(FINISH_FROM_TICK / TICKS_PER_SECOND, v2_army)],
        watch=watch,
        plan_selector=FinishFrom(FINISH_FROM_TICK),
    )
    return match, distances


def test_march_when_clear_a_whole_fake_match_under_finish_runs_without_an_error_within_the_compute_budget(
    finish_march,
):
    match, _ = finish_march

    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.run.invalid_actions == 0 and match.game.invalid_actions == 0
    assert match.compute_p95_ms <= COMPUTE_P95_MS


def test_march_when_clear_main_comes_within_15_cells_of_the_enemy_start_60_cells_off_within_3000_ticks_of_finish(
    finish_march,
):
    match, distances = finish_march
    base_distance = math.dist(match.agent.world.base_center, FAR_ENEMY_START)
    reached = [tick - FINISH_FROM_TICK for tick, distance in distances if distance <= REACHED_CELLS]

    print(f"base {base_distance:.1f} cells and main {distances[0][1]:.1f} cells from the enemy start at FINISH")
    print(f"main within {REACHED_CELLS} cells of it {reached[:1]} ticks after FINISH started")
    assert base_distance == 60 and distances[0][0] == FINISH_FROM_TICK
    assert reached and reached[0] <= REACHED_WITHIN_TICKS


# -- artillery slots: slot_spacing_cells, slot_resend_cells and slot_idle_ticks of [siege] ---------------------------

SLOT_OBJECTIVE = (80, 48)
# Long-range units take ids from 300 on, their screen's from 310 on.
SLOT_ARTILLERY_IDS = list(range(300, 307))
FIRST_TANK_ID = 310
# Each long-range unit gets at most this many moves over the 30 s of the march.
MAX_SLOT_MOVES = 15
SLOT_MARCH_TICKS = 30 * TICKS_PER_SECOND
# How many cells farther behind the screen's front than the band depth the artillery's centroid may end the march.
SLOT_LAG_CELLS = 4


def artillery_behind_tanks(arty_x: int = 30, tank_x: int = 40) -> World:
    """Seven artillery pieces in a block around (arty_x, 48), and six medium tanks in a column across x tank_x from y 43
    to 53."""
    artillery = [own(300 + number, "arty", arty_x + number % 2, 45 + number) for number in range(7)]
    tanks = [own(FIRST_TANK_ID + number, "2tnk", tank_x, 43 + 2 * number) for number in range(6)]
    return seen([*artillery, *tanks])


def pieces_and_screen(world: World) -> tuple[list, list]:
    """The world's artillery and V2s, and the rest of its units, their screen."""
    units = unit_list(world)
    is_piece = {unit.id: unit.type in ("arty", "v2rl") for unit in units}
    return [unit for unit in units if is_piece[unit.id]], [unit for unit in units if not is_piece[unit.id]]


def piece_orders(intents: list) -> list[dict]:
    """The orders among intents that name a long-range unit."""
    return [intent.order for intent in intents if intent.order["units"][0] in SLOT_ARTILLERY_IDS]


def test_artillery_slots_seven_artillery_behind_six_tanks_get_seven_cells_of_their_own_2_apart_at_the_band_depth():
    world = artillery_behind_tanks()
    artillery, tanks = pieces_and_screen(world)

    orders = piece_orders(Siege().march(artillery, tanks, SLOT_OBJECTIVE, world, TUNING))
    sent = sorted(tuple(order["to"]) for order in orders)
    depth = band_depth({"arty"}, world, TUNING.siege)

    # The front is the tanks' column at x 40: the slot line lies 8.25 cells behind it, at x 31.75, across y 42 to 54.
    assert depth == 8.25
    assert {order["cmd"] for order in orders} == {"move"}
    assert sorted(unit_id for order in orders for unit_id in order["units"]) == SLOT_ARTILLERY_IDS
    assert sent == [(32, row) for row in range(42, 55, 2)]
    assert all(abs(40 - column - depth) <= 0.5 for column, _ in sent)


def test_artillery_slots_each_piece_takes_the_free_slot_nearest_it_the_nearest_pairs_first():
    world = artillery_behind_tanks()
    artillery, tanks = pieces_and_screen(world)
    line = march_slot_line(artillery, tanks, SLOT_OBJECTIVE, world, TUNING.siege)

    slots = slot_cells(artillery, line, world, TUNING.siege)

    # The pieces at x 31 stand a cell from the slot ahead of them and take it first; those at x 30 then take the
    # nearest left, the ones at y 47 and 49 the slots at the line's ends.
    assert line == SlotLine((31.75, 48.0), (1.0, 0.0))
    assert slots == {
        300: (32, 44),
        301: (32, 46),
        302: (32, 42),
        303: (32, 48),
        304: (32, 54),
        305: (32, 50),
        306: (32, 52),
    }


@pytest.mark.parametrize("march", [True, False], ids=["on a march", "holding the line"])
def test_artillery_slots_a_piece_with_a_pillbox_in_its_range_fires_from_its_band_and_the_other_takes_its_slot(march):
    # The pillbox 7 cells from the first piece, within its range of 12 and its band of 6.5 to 11.5; 15 cells from the
    # second, beyond its range and 2 cells. Two tanks ahead, 7.2 cells from the pillbox, outside its range of 6.
    units = [own(300, "arty", 33, 48), own(301, "arty", 25, 48), own(310, "2tnk", 34, 44), own(311, "2tnk", 34, 52)]
    world = seen(units, buildings=[foe(600, "pbox", 40, 48)])
    artillery, tanks = pieces_and_screen(world)
    siege = Siege()

    if march:
        intents = siege.march(artillery, tanks, (60, 48), world, TUNING)
    else:
        intents = siege.orders(artillery, tanks, (60, 48), True, world, TUNING)

    # On a march the slot lies 8.25 cells behind the tanks' front at x 34; holding the line, at the line's depth, 2
    # cells ahead of the pieces' centroid at x 29.
    slot = [26, 48] if march else [31, 48]
    assert piece_orders(intents) == [
        {"cmd": "attack", "units": [300], "target": 600},
        {"cmd": "move", "units": [301], "to": slot},
    ]


def test_artillery_slots_while_the_screen_holds_its_line_four_pieces_go_to_cells_of_their_own_at_its_depth():
    # Four pieces across x 30, two tanks 3 cells ahead: the line stands 2 cells ahead of the pieces, nearer than the
    # tanks, and each piece goes to a cell of its own 2 cells ahead, not all of them to the line's one cell.
    units = [own(300 + number, "arty", 30, 45 + 2 * number) for number in range(4)]
    world = seen([*units, own(FIRST_TANK_ID, "2tnk", 33, 47), own(FIRST_TANK_ID + 1, "2tnk", 33, 49)])
    artillery, tanks = pieces_and_screen(world)

    orders = piece_orders(Siege().orders(artillery, tanks, SLOT_OBJECTIVE, True, world, TUNING))

    assert orders == [{"cmd": "move", "units": [300 + number], "to": [32, 45 + 2 * number]} for number in range(4)]


def test_artillery_slots_with_no_screen_a_march_lines_the_pieces_up_across_their_centroid():
    # Three V2s in a column toward the objective: the front is their centroid moved the band depth on, 7.25 cells, so
    # the slot line runs across the centroid, and the middle V2 stands on its slot.
    world = seen([own(300, "v2rl", 30, 48), own(301, "v2rl", 32, 48), own(302, "v2rl", 34, 48)])

    orders = piece_orders(Siege().march(unit_list(world), [], SLOT_OBJECTIVE, world, TUNING))

    assert orders == [
        {"cmd": "move", "units": [300], "to": [32, 46]},
        {"cmd": "move", "units": [302], "to": [32, 50]},
    ]


def test_artillery_slots_a_straggler_10_cells_from_the_screen_s_centroid_does_not_move_its_front():
    # Five tanks across x 40 and one at x 52, 10 cells from the screen's centroid at (42, 48).
    tanks = [own(FIRST_TANK_ID + number, "2tnk", 40, 44 + 2 * number) for number in range(5)]
    world = seen([own(300, "arty", 30, 48), *tanks, own(FIRST_TANK_ID + 5, "2tnk", 52, 48)])
    artillery, screen = pieces_and_screen(world)

    orders = piece_orders(Siege().march(artillery, screen, SLOT_OBJECTIVE, world, TUNING))

    assert screen_front(screen, (30.0, 48.0), (1.0, 0.0)) == 10
    assert orders == [{"cmd": "move", "units": [300], "to": [32, 48]}]


def test_artillery_slots_a_screen_split_in_two_far_parts_takes_its_front_from_the_unit_nearest_its_centroid():
    # Two tanks at x 30 and two at x 50: the centroid at (40, 48), 10 cells from each, and no unit within 8 of it.
    tanks = [own(FIRST_TANK_ID + number, "2tnk", 30 + 20 * (number // 2), 47 + 2 * (number % 2)) for number in range(4)]

    assert screen_front(unit_list(seen(tanks)), (20.0, 48.0), (1.0, 0.0)) == 10


def test_artillery_slots_a_subgroup_whose_centroid_is_its_aim_has_no_slot_line_and_moves_into_the_band_unheld():
    # Two pieces a cell either side of the aim: no approach to lay a line across, so each goes out to the middle of
    # its band, 8.25 cells from the aim, straight away from it.
    world = seen([own(300, "arty", 40, 47), own(301, "arty", 40, 49)])
    artillery = unit_list(world)

    orders = piece_orders(Siege().orders(artillery, [], (40, 48), True, world, TUNING))

    assert march_slot_line(artillery, [], (40, 48), world, TUNING.siege) is None
    assert orders == [{"cmd": "move", "units": [300], "to": [40, 40]}, {"cmd": "move", "units": [301], "to": [40, 56]}]


def slot_cycle(siege: Siege, world: World, tick: int, tank_x: int, arty_x: int, idle: bool) -> list[dict]:
    """The orders the march gives one piece at (arty_x, 48) behind two tanks across x tank_x: its slot lies 8.25 cells
    behind them."""
    tanks = [own(FIRST_TANK_ID, "2tnk", tank_x, 47), own(FIRST_TANK_ID + 1, "2tnk", tank_x, 49)]
    moved = seen([own(300, "arty", arty_x, 48, idle=idle), *tanks], tick=tick, world=world)
    artillery, screen = pieces_and_screen(moved)
    return piece_orders(siege.march(artillery, screen, SLOT_OBJECTIVE, moved, TUNING))


def slot_move(cell_x: int) -> list[dict]:
    """The one piece's move to its slot at (cell_x, 48)."""
    return [{"cmd": "move", "units": [300], "to": [cell_x, 48]}]


def test_artillery_slots_a_piece_is_sent_again_only_once_its_slot_moved_3_cells_or_it_stood_idle_50_ticks_away():
    siege, world = Siege(), World(ITEMS)
    cycles = [
        (100, 40, 20, True),
        # The slot moved 2 cells: the piece on its way is not sent again ...
        (130, 42, 25, False),
        # ... but once it has moved 3 from the cell the piece was sent to, it is.
        (160, 43, 28, False),
        # Arrived and idle 2 cells behind a slot moved 2 more: sent again after 50 ticks so, and not before.
        (200, 45, 35, True),
        (245, 45, 35, True),
        (250, 45, 35, True),
        # Idle a cell from its slot it stands at it: it is not sent again.
        (300, 45, 36, True),
        (400, 45, 36, True),
    ]

    sent = [slot_cycle(siege, world, *cycle) for cycle in cycles]

    assert sent == [slot_move(32), [], slot_move(35), [], [], slot_move(37), [], []]


def test_artillery_slots_a_piece_is_never_sent_to_its_slot_twice_within_a_second():
    siege, world = Siege(), World(ITEMS)

    sent = [slot_cycle(siege, world, tick, tank_x, 20, True) for tick, tank_x in ((100, 40), (110, 50), (125, 50))]

    assert sent == [slot_move(32), [], slot_move(42)]


def test_artillery_slots_no_two_pieces_get_one_cell_where_the_map_s_edge_takes_their_slots():
    # Five pieces marching north along the map's left edge: the slots at x -4 and -2 lie off the map.
    world = seen([own(300 + number, "arty", number % 2, 60 + number) for number in range(5)])
    line = SlotLine((0.0, 50.0), (0.0, -1.0))

    slots = slot_cells(unit_list(world), line, world, TUNING.siege)

    assert len(slots) == 5 and len(set(slots.values())) == 5
    assert all(world.in_bounds(*cell) for cell in slots.values())


def test_artillery_slots_a_piece_whose_slot_finds_no_free_cell_near_is_given_none():
    # A spacing of 0 puts every slot on one cell: with the 28 cells within 3 of it, 29 of the 30 pieces get one.
    world = seen([own(300 + number, "arty", 30, 30 + number) for number in range(30)])
    line = SlotLine((40.0, 48.0), (1.0, 0.0))

    slots = slot_cells(unit_list(world), line, world, replace(TUNING.siege, slot_spacing_cells=0))

    assert len(slots) == 29 and len(set(slots.values())) == 29


@pytest.fixture(scope="module")
def slot_march(model_seconds) -> tuple[CombatSim, list[Actor], list[Actor], list[list[tuple]]]:
    """Seven artillery pieces following, by the march's slots, six medium tanks the test drives east a cell a second
    for 30 s; a power plant at the objective keeps the enemy's side standing. The fight, the artillery, the tanks and
    the cells the pieces were sent to, cycle by cycle."""
    artillery = [ours(100 + number, "arty", 10 + number % 2, 29 + number) for number in range(7)]
    tanks = [ours(200 + number, "2tnk", 20, 27 + 2 * number) for number in range(6)]
    siege, sent_cells = Siege(), []

    def drive(world: World) -> list:
        for tank in tanks:
            tank.x = 20 + sim.tick / TICKS_PER_SECOND
        pieces, screen = pieces_and_screen(world)
        intents = siege.march(pieces, screen, (62, 32), world, TUNING)
        orders = [order for order in pack(intents, world, Limits()).orders if order["units"][0] < 200]
        sent_cells.append([tuple(order["to"]) for order in orders])
        return orders

    sim = CombatSim(ITEMS, [*artillery, *tanks, theirs(500, "powr", 62, 32)], drive, BOUNDS)
    played(sim, SLOT_MARCH_TICKS, model_seconds)
    return sim, artillery, tanks, sent_cells


def test_artillery_slots_over_30_s_of_a_march_behind_a_screen_going_a_cell_a_second_each_piece_gets_15_moves_at_most(
    slot_march,
):
    sim, artillery, _, sent_cells = slot_march
    moves = [sim.orders_received[piece.id]["move"] for piece in artillery]

    print(f"moves per piece over {SLOT_MARCH_TICKS} ticks: {moves}")
    assert sim.tick == SLOT_MARCH_TICKS
    assert all(1 <= count <= MAX_SLOT_MOVES for count in moves)
    # No two pieces are sent to one cell in a cycle.
    assert all(len(cycle) == len(set(cycle)) for cycle in sent_cells)


def test_artillery_slots_after_30_s_of_the_march_the_artillery_s_centroid_is_within_the_band_depth_and_4_cells_behind(
    slot_march,
):
    _, artillery, tanks, _ = slot_march
    depth = band_depth({"arty"}, World(ITEMS), TUNING.siege)
    front = max(tank.x for tank in tanks)
    behind = front - sum(piece.x for piece in artillery) / len(artillery)

    print(f"front at x {front:.1f}, the artillery's centroid {behind:.2f} cells behind it, band depth {depth}")
    assert front == 20 + (SLOT_MARCH_TICKS - ORDERS_EVERY_TICKS) / TICKS_PER_SECOND
    assert 0 < behind <= depth + SLOT_LAG_CELLS
