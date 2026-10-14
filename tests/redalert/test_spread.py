"""Infantry spread: our groups' infantry given a cell each on a move, and idle infantry sharing a cell moved apart."""

from __future__ import annotations

from dataclasses import replace

import pytest

from situations import REPO_ROOT, building, fixture_state, tracked_items, unit
from ucla_agent.games.redalert import placement
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.groups import GUARD, SCOUT
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.ledger import OrderLedger
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.spread import InfantrySpread
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning
from ucla_agent.games.redalert.world import World

TICK = 1000
POINT = (40, 40)
SPREAD_KEYS = (
    "infantry_spread",
    "infantry_spread_cells",
    "infantry_spread_recheck_seconds",
    "infantry_spread_max_orders",
)


class SilentChooser:
    """A chooser that is never asked."""

    def submit(self, _request) -> bool:
        return False

    def poll(self, _current_state_tag):
        return None


def match(units: list[dict], buildings: list[dict] = (), tick: int = TICK, tuning: Tuning | None = None, enemies=()):
    """A world after one state with these own units, buildings and enemies in sight, and a context whose groups hold
    the units."""
    world = World(tracked_items())
    world.update(
        fixture_state("opening_mcv", tick, units=list(units), buildings=list(buildings), visible_enemies=list(enemies))
    )
    ctx = MatchContext(tuning or Tuning(), OrderLedger(), SilentChooser(), terrain=Terrain())
    # Every unit joins main: the guard, whose infantry is not spread, takes none.
    for infantry_type in ("e1", "e3"):
        ctx.groups.set_guard_quota(infantry_type, 0)
    ctx.groups.update(world)
    return world, ctx


def move(unit_ids, to=POINT, cmd: str = "move", priority: Priority = Priority.MOVE, **fields) -> Intent:
    return Intent(priority, {"cmd": cmd, "units": list(unit_ids), "to": list(to)}, **fields)


def always_new(_intent: Intent) -> bool:
    return True


def own_cells(intents: list[Intent]) -> dict[int, tuple[int, int]]:
    """Unit id -> the cell of the one-unit orders among the intents."""
    return {
        intent.order["units"][0]: tuple(intent.order["to"])
        for intent in intents
        if len(intent.order["units"]) == 1 and intent.order["cmd"] in ("move", "attack_move")
    }


def rockets(count: int, x: int = 30, y: int = 30, first: int = 300) -> list[dict]:
    return [unit(first + number, "e3", x, y + number) for number in range(count)]


def test_spread_8_rocket_soldiers_sent_to_one_point_get_8_cells_within_2_cells_none_under_a_building():
    # A power plant reported at 41,39 covers the point and cells of the first ring around it.
    plant = building(900, "powr", 41, 39)
    world, ctx = match(rockets(8), [plant])
    covered = set(placement.standing_box(world.buildings[900]).cells())

    sent = InfantrySpread().apply([move(range(300, 308))], world, ctx, always_new)

    cells = own_cells(sent)
    assert sorted(cells) == list(range(300, 308))
    assert len(set(cells.values())) == 8
    assert all(max(abs(x - POINT[0]), abs(y - POINT[1])) <= 2 for x, y in cells.values())
    assert POINT in covered and not covered & set(cells.values())


def test_spread_the_unit_nearest_the_point_gets_the_point_itself_under_the_order_as_it_was():
    world, ctx = match([unit(300, "e3", 30, 30), unit(301, "e3", 38, 40)])

    sent = InfantrySpread().apply([move([300, 301])], world, ctx, always_new)

    assert sent[0].order == {"cmd": "move", "units": [301], "to": list(POINT)}
    assert list(own_cells(sent[1:])) == [300] and own_cells(sent[1:])[300] != POINT


def test_spread_vehicles_of_the_order_keep_it_as_it_was_and_an_attack_move_is_spread_as_attack_moves():
    world, ctx = match([*rockets(2), unit(400, "2tnk", 30, 34), unit(401, "2tnk", 30, 35)])

    sent = InfantrySpread().apply([move([300, 400, 301, 401], cmd="attack_move")], world, ctx, always_new)

    # Rocket soldier 301, the one nearer the point, is given the point itself and keeps the order with the tanks.
    assert sent[0].order == {"cmd": "attack_move", "units": [400, 301, 401], "to": list(POINT)}
    assert {intent.order["cmd"] for intent in sent} == {"attack_move"}
    assert sorted(own_cells(sent[1:])) == [300]


def test_spread_5_infantry_attacking_one_target_are_not_spread():
    world, ctx = match(rockets(5))
    attack = Intent(Priority.CONTACT, {"cmd": "attack", "units": list(range(300, 305)), "target": 700})

    assert InfantrySpread().apply([attack], world, ctx, always_new) == [attack]


@pytest.mark.parametrize(
    "intent",
    [
        move(range(300, 305), priority=Priority.EMERGENCY),
        move(range(300, 305), commander=True),
        move(range(300, 305), resend=True),
        Intent(Priority.MOVE, {"cmd": "move", "units": [300, 301], "to": [40, 40], "queued": True}),
        Intent(Priority.MOVE, {"cmd": "enter_transport", "units": [300, 301], "target": 800}),
    ],
    ids=["retreat or emergency", "commander", "resent", "queued", "boarding"],
)
def test_spread_retreats_the_commander_s_order_resent_queued_and_boarding_orders_are_left_alone(intent):
    world, ctx = match(rockets(5, x=10, y=10))

    assert InfantrySpread().apply([intent], world, ctx, always_new) == [intent]


def test_spread_infantry_of_no_spread_group_and_the_switch_off_leave_the_order_alone():
    world, ctx = match(rockets(3))
    for unit_id in (300, 301, 302):
        ctx.groups.assign(unit_id, SCOUT)
    order = move([300, 301, 302])
    off_world, off_ctx = match(
        rockets(3), tuning=replace(Tuning(), combat=replace(CombatTuning(), infantry_spread=False))
    )

    assert InfantrySpread().apply([order], world, ctx, always_new) == [order]
    assert InfantrySpread().apply([order], off_world, off_ctx, always_new) == [order]


def test_spread_a_unit_on_its_own_cell_or_with_its_move_there_in_force_is_not_ordered_again():
    world, ctx = match(rockets(2))
    spread = InfantrySpread()
    cells = own_cells(spread.apply([move([300, 301])], world, ctx, always_new))
    arrived = unit(300, "e3", *cells[300])
    world.update(fixture_state("opening_mcv", TICK + 5, units=[arrived, unit(301, "e3", 31, 31, idle=False)]))

    def in_force(intent: Intent) -> bool:
        return tuple(intent.order["to"]) != cells[intent.order["units"][0]]

    assert spread.apply([move([300, 301])], world, ctx, in_force) == []


def test_spread_30_rifles_reach_30_cells_of_their_own_within_4_cycles_at_most_8_orders_a_cycle():
    rifles = [unit(300 + number, "e1", 20 + number % 6, 20 + number // 6) for number in range(30)]
    world, ctx = match(rifles)
    spread = InfantrySpread()
    sent: dict[int, tuple[int, int]] = {}

    def is_new(intent: Intent) -> bool:
        return sent.get(intent.order["units"][0]) != tuple(intent.order["to"])

    per_cycle = []
    for cycle in range(4):
        world.update(fixture_state("opening_mcv", TICK + cycle, units=rifles))
        cells = own_cells(spread.apply([move(range(300, 330))], world, ctx, is_new))
        per_cycle.append(len(cells))
        sent.update(cells)

    # The first unit given a cell is given the point itself, and goes there under the group's order, which names it
    # alone once the rest have their cells.
    assert max(per_cycle) <= 8
    assert len(sent) == 30 and len(set(sent.values())) == 30 and POINT in sent.values()


def test_spread_units_waiting_longest_for_a_cell_go_first():
    rifles = [unit(300 + number, "e1", 20, 20 + number) for number in range(10)]
    # A power plant covers the second point, so that none of the eight is given the point itself.
    world, ctx = match(rifles, [building(900, "powr", 61, 59)])
    spread = InfantrySpread()
    first = own_cells(spread.apply([move(range(300, 310))], world, ctx, always_new))
    world.update(fixture_state("opening_mcv", TICK + 1, units=rifles, buildings=[building(900, "powr", 61, 59)]))
    # Sent to another point, the two that waited get their cells before any of the eight that had one.
    second = own_cells(spread.apply([move(range(300, 310), to=(60, 60))], world, ctx, always_new))

    assert set(second) - set(first) == {308, 309} and len(second) == 8


def test_destack_3_idle_rocket_soldiers_on_one_cell_get_3_distinct_cells_and_no_second_within_5_seconds():
    stacked = [unit(300 + number, "e3", 40, 40) for number in range(3)]
    world, ctx = match(stacked)
    spread = InfantrySpread()

    moves = spread.destacks(world.units, [], world, ctx)

    assert [intent.priority for intent in moves] == [Priority.MOVE] * 3
    cells = own_cells(moves)
    assert sorted(cells) == [300, 301, 302] and len(set(cells.values())) == 3 and (40, 40) not in cells.values()
    almost = TICK + 5 * TICKS_PER_SECOND - 1
    world.update(fixture_state("opening_mcv", almost, units=stacked))
    assert spread.destacks(world.units, [], world, ctx) == []
    world.update(fixture_state("opening_mcv", almost + 1, units=stacked))
    assert len(spread.destacks(world.units, [], world, ctx)) == 3


def test_destack_leaves_out_units_ordered_in_the_cycle_busy_or_alone_on_their_cell():
    world, ctx = match([unit(300, "e3", 40, 40), unit(301, "e3", 40, 40, idle=False), unit(302, "e3", 45, 45)])
    attack = Intent(Priority.CONTACT, {"cmd": "attack", "units": [300], "target": 700})

    assert InfantrySpread().destacks(world.units, [attack], world, ctx) == []


def test_destack_with_no_manager_asking_nothing_is_ordered_and_moves_split_no_idle_unit():
    world, ctx = match([unit(300 + number, "e3", 40, 40) for number in range(3)])

    assert InfantrySpread().apply([], world, ctx, always_new) == []


def test_destack_moves_count_against_the_cycle_s_orders_and_the_guard_s_infantry_is_not_spread():
    stacked = [unit(300 + number, "e3", 40, 40) for number in range(6)]
    world, ctx = match([*stacked, *rockets(6, x=20, y=20, first=400), *rockets(2, x=10, y=10, first=500)])
    for unit_id in (500, 501):
        ctx.groups.assign(unit_id, GUARD)
    spread = InfantrySpread()

    destacked = spread.destacks({unit_id: world.units[unit_id] for unit_id in range(300, 306)}, [], world, ctx)
    sent = spread.apply([move(range(400, 406)), move([500, 501], to=(12, 12))], world, ctx, always_new)

    assert len(destacked) == 6
    # 2 of the 8 left: 400 and 401 are given cells, 401 the point itself, under the group's order with the rest.
    assert list(own_cells(sent[1:-1])) == [400] and 401 in sent[0].order["units"]
    assert sent[-1] == move([500, 501], to=(12, 12))


@pytest.mark.parametrize("priority", [Priority.CONTACT, Priority.SCREEN, Priority.EMERGENCY])
def test_spread_only_move_class_orders_are_split(priority):
    world, ctx = match(rockets(3))
    order = move(range(300, 303), priority=priority)

    assert InfantrySpread().apply([order], world, ctx, always_new) == [order]


def test_spread_keys_ship_on_at_1_cell_5_seconds_and_8_orders_and_the_tracked_file_holds_them():
    tracked = Tuning.from_file(REPO_ROOT).combat

    for combat in (CombatTuning(), tracked):
        assert tuple(getattr(combat, key) for key in SPREAD_KEYS) == (True, 1, 5, 8)
    assert tracked == CombatTuning()


def test_spread_a_unit_named_twice_in_an_order_is_sent_once_to_one_cell():
    world, ctx = match(rockets(2))

    sent = InfantrySpread().apply([move([300, 301, 300])], world, ctx, always_new)

    assert sorted(intent.order["units"][0] for intent in sent) == [300, 301]
    assert len(set(own_cells(sent).values())) == 2


def test_destack_leaves_idle_stacked_infantry_in_contact_with_an_enemy_in_sight_where_it_fires():
    stacked = [unit(300 + number, "e3", 40, 40) for number in range(3)]
    tank = {"id": 700, "type": "2tnk", "x": 44, "y": 40, "hp": 100}
    world, ctx = match(stacked, enemies=[tank])

    assert InfantrySpread().destacks(world.units, [], world, ctx) == []


def test_spread_main_s_infantry_is_neither_spread_nor_destacked_while_main_has_artillery():
    stacked = [unit(300 + number, "e3", 40, 40) for number in range(3)]
    world, ctx = match([*stacked, unit(400, "arty", 30, 30)])
    order = move([300, 301, 302, 400], to=(50, 50))
    spread = InfantrySpread()

    assert spread.destacks(world.units, [], world, ctx) == []
    assert spread.apply([order], world, ctx, always_new) == [order]
