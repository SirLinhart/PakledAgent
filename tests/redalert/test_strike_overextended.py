"""The over-extended strike: main striking an enemy refinery or harvesters at an ore field far from the enemy's start."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from situations import REPO_ROOT, ask, base_state, event, new_agent
from ucla_agent.games.redalert import command_targets, overextended
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.command_army import hold_action
from ucla_agent.games.redalert.command_prompt import DETAILS
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MAJOR_THREAT
from ucla_agent.games.redalert.scout_report import ScoutReport
from ucla_agent.games.redalert.strike import Strike, field_ratio
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning

# The Allied base of the fixture map: its yard, and the staging point 10 cells from it toward the enemy's start at
# (74, 48).
YARD = ("fact", 22, 48)
STAGING = [32, 48]
# An ore field 35 cells north of the enemy's start, and one 15 cells from it.
FAR_FIELD = OrePatch((74, 16), frozenset({(74, 16), (75, 16), (74, 17), (75, 17)}))
NEAR_FIELD = OrePatch((74, 34), frozenset({(74, 34), (75, 34)}))
# An enemy refinery reported at (74, 13): its footprint from (73, 12) stands 2 cells from the far field's ore.
FAR_REFINERY_CELL = (74, 13)
OVEREXTENDED_OFF = Tuning(combat=CombatTuning(strike_overextended=False))
MAIN_TANKS = [300, 301, 302, 303]


def own(unit_id: int, unit_type: str, x: int, y: int, *, idle: bool = True) -> dict:
    """One own unit as an observation lists it."""
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": 100, "act": None if idle else "move", "idle": idle}


def foe(enemy_id: int, enemy_type: str, x: int, y: int) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


def at_staging() -> list[dict]:
    """Four own light tanks, 2,800 in a fight, at the staging point."""
    return [own(300 + number, "1tnk", 32 + number % 2, 48 + number // 2) for number in range(4)]


def refinery(x: int = FAR_REFINERY_CELL[0], y: int = FAR_REFINERY_CELL[1]) -> dict:
    return foe(600, "proc", x, y)


def escort(count: int, x: int = 72, y: int = 13) -> list[dict]:
    """Enemy light tanks in sight beside the far refinery, 700 each in a fight."""
    return [foe(510 + number, "1tnk", x, y + number) for number in range(count)]


class Always:
    """A plan rule that keeps one plan."""

    def __init__(self, plan: str) -> None:
        self.plan = plan

    def select(self, world, ctx, choice) -> str:
        return self.plan


def strike_agent(plan: str = "PRESS", tuning: Tuning | None = None, fields=(FAR_FIELD,)):
    """An agent under a plan with the combat manager alone, on a map with these ore fields, its match begun (which
    reads the map's terrain) by a state at tick 900 showing nothing."""
    agent = new_agent(CombatManager(), tuning=tuning, plan_selector=Always(plan))
    ask(agent, state(900))
    agent.ctx.terrain = Terrain(fields)
    return agent


def state(tick: int, units=(), enemies=(), buildings=(), **observation) -> dict:
    """A state of the Allied base at that tick with these own units and enemies in sight."""
    shown = {"units": list(units), "visible_enemies": list(enemies), "visible_enemy_buildings": list(buildings)}
    return base_state("england", YARD, tick=tick, **shown, **observation)


def strike_events(match_record) -> list[dict]:
    """The `combat` events of reason `strike_overextended` of the match, without their ticks and reason."""
    logged = [data for data in match_record("combat") if data["reason"] == "strike_overextended"]
    return [{key: value for key, value in data.items() if key not in ("tick", "reason")} for data in logged]


def naming(orders: list[dict], ids: set[int]) -> list[dict]:
    """The orders of a cycle that name any of these units."""
    return [order for order in orders if ids & set(order["units"])]


def started(agent, enemies=(), buildings=(refinery(),)) -> list[dict]:
    """The orders of the first cycle of four light tanks at the staging point, these enemies in sight."""
    return ask(agent, state(1000, at_staging(), enemies, buildings))


# -- the targets -------------------------------------------------------------------------------------


def target_ids(agent, enemies=(), buildings=()) -> list[list[int]]:
    """The ids of the actors of each over-extended target after a state showing these enemies."""
    ask(agent, state(1000, at_staging(), enemies, buildings))
    return [[record.id for record in found.records] for found in overextended.targets(agent.world, agent.ctx)]


@pytest.mark.parametrize(
    "buildings, enemies, expected",
    [
        ([refinery()], [], [[600]]),
        ([refinery(74, 33)], [], []),
        ([refinery(), foe(700, "fact", 74, 30)], [], []),
        ([refinery(), foe(650, "pbox", 79, 13)], [], []),
        ([refinery(), foe(650, "pbox", 83, 13)], [], [[600]]),
        ([], [foe(520, "harv", 75, 18), foe(521, "harv", 76, 19)], [[520, 521]]),
        ([], [foe(520, "harv", 75, 22)], []),
    ],
    ids=[
        "refinery 35 cells from the start",
        "refinery 15 cells from the start",
        "refinery 17 cells from a yard",
        "pillbox 5 cells away",
        "pillbox 9 cells away",
        "harvesters at the field",
        "harvester 5 cells off the ore",
    ],
)
def test_overextended_a_refinery_or_harvesters_far_from_the_enemy_start_and_its_defenses_are_a_target(
    buildings, enemies, expected
):
    fields = (FAR_FIELD, NEAR_FIELD)

    assert target_ids(strike_agent(fields=fields), enemies, buildings) == expected


def test_overextended_with_the_switch_off_there_is_no_target():
    assert target_ids(strike_agent(tuning=OVEREXTENDED_OFF), buildings=[refinery()]) == []


# -- the start ----------------------------------------------------------------------------------------


def test_overextended_main_worth_2x_at_a_refinery_35_cells_from_the_enemy_start_strikes_it_with_one_start_event(
    match_record,
):
    agent = strike_agent()

    orders = started(agent, escort(2))
    again = ask(agent, state(1005, at_staging(), escort(2), [refinery()]))

    start = {"phase": "start", "target": 600, "target_type": "proc", "cell": [74, 13], "focus": [600]}
    assert strike_events(match_record) == [{**start, "units": MAIN_TANKS, "ratio": 2.0}]
    assert naming(orders, set(MAIN_TANKS)) == [{"cmd": "attack_move", "units": MAIN_TANKS, "to": [74, 13]}]
    assert naming(again, set(MAIN_TANKS)) == [{"cmd": "attack_move", "units": MAIN_TANKS, "to": [74, 13]}]


def test_overextended_a_unit_of_the_strike_near_a_harvester_in_sight_attacks_it():
    agent = strike_agent()
    harvester = foe(520, "harv", 75, 18)
    ask(agent, state(1000, at_staging(), [harvester], [refinery()]))
    there = [own(300, "1tnk", 75, 22, idle=False), *at_staging()[1:]]

    orders = ask(agent, state(1005, there, [harvester], [refinery()]))

    assert naming(orders, {300}) == [{"cmd": "attack", "units": [300], "target": 520}]


def test_overextended_a_pillbox_5_cells_away_with_main_worth_1_2x_counting_it_starts_no_strike(match_record):
    agent = strike_agent()
    # A light tank and four rifles beside the refinery, and a pillbox 5 cells east of it, whose range reaches it.
    guard = [*escort(1), *(foe(520 + number, "e1", 72, 15 + number) for number in range(4))]

    orders = started(agent, guard, [refinery(), foe(650, "pbox", 79, 13)])

    there = tuple(overextended.at_field(FAR_FIELD, agent.world, agent.ctx))
    force = list(agent.world.combat_units.values())
    assert round(field_ratio(force, there, agent.world, agent.ctx), 2) == 1.22
    assert strike_events(match_record) == [] and _strike(agent).target_id is None
    assert not [order for order in orders if order.get("to") == [74, 13]]


@pytest.mark.parametrize(
    "buildings, enemies",
    [([refinery()], escort(3)), ([refinery(74, 33)], [])],
    ids=["main worth 1.33x", "a refinery 15 cells from the enemy start"],
)
def test_overextended_no_strike_under_strike_min_ratio_or_15_cells_from_the_enemy_start(
    match_record, buildings, enemies
):
    agent = strike_agent(fields=(FAR_FIELD, NEAR_FIELD))

    orders = started(agent, enemies, buildings)

    assert strike_events(match_record) == [] and _strike(agent).target_id is None
    assert not [order for order in orders if order.get("to") in ([74, 13], [74, 33])]


def _strike(agent) -> Strike:
    (manager,) = [manager for manager in agent.managers if isinstance(manager, CombatManager)]
    return manager._strike


@pytest.mark.parametrize(
    "plan, threat, tuning",
    [("PRESERVE", None, None), ("PRESS", MAJOR_THREAT, None), ("PRESS", None, OVEREXTENDED_OFF)],
    ids=["under PRESERVE", "under a major threat", "with the switch off"],
)
def test_overextended_no_strike_under_preserve_a_major_threat_or_with_the_switch_off(
    match_record, plan, threat, tuning
):
    agent = strike_agent(plan, tuning)
    if threat is not None:
        agent.ctx.threat_level = threat

    started(agent, escort(2))

    assert strike_events(match_record) == []


def test_overextended_main_not_gathered_at_the_staging_point_does_not_strike(match_record):
    agent = strike_agent()
    spread = [own(300 + number, "1tnk", 32 + 10 * number, 60) for number in range(4)]

    ask(agent, state(1000, spread, escort(2), [refinery()]))

    assert strike_events(match_record) == []


def test_overextended_main_worth_under_half_the_enemy_army_estimate_does_not_strike(match_record):
    agent = strike_agent()
    # 2,800 is under half of 6,000: a refinery worth nothing in a fight never draws main out against that army.
    agent.ctx.scout_report = replace(ScoutReport(), enemy_combat_value=6000)

    started(agent)

    assert strike_events(match_record) == []


def test_overextended_the_commander_holding_main_back_starts_no_strike_and_otherwise_the_strike_starts():
    agent = strike_agent()
    ask(agent, state(1000, at_staging(), escort(2), [refinery()]))
    held, free, main = Strike(), Strike(), dict(agent.world.combat_units)

    held.follow(main, False, tuple(STAGING), agent.world, agent.ctx, held_back=True)
    free.follow(main, False, tuple(STAGING), agent.world, agent.ctx)

    assert (held.target_id, free.target_id, sorted(free.units)) == (None, 600, MAIN_TANKS)


# -- the end -------------------------------------------------------------------------------------------


def test_overextended_the_target_dead_ends_the_strike_with_one_end_event_and_main_returns_to_staging(match_record):
    agent = strike_agent()
    started(agent, escort(2))
    there = [own(300 + number, "1tnk", 70 + number, 16) for number in range(4)]

    orders = ask(agent, state(1005, there, escort(2), events=[event(1, "enemy_killed")]))

    assert strike_events(match_record)[1:] == [{"phase": "end", "target": 600, "outcome": "destroyed"}]
    assert {"cmd": "move", "units": MAIN_TANKS, "to": STAGING} in orders
    assert _strike(agent).target_id is None


def test_overextended_harvesters_out_of_sight_for_20_s_end_the_strike_unseen(match_record):
    agent = strike_agent()
    harvester = foe(520, "harv", 75, 18)
    ask(agent, state(1000, at_staging(), [harvester]))
    ask(agent, state(1000 + 20 * TICKS_PER_SECOND - 1, at_staging()))

    orders = ask(agent, state(1000 + 20 * TICKS_PER_SECOND, at_staging()))

    assert strike_events(match_record)[1:] == [{"phase": "end", "target": 520, "outcome": "unseen"}]
    assert {"cmd": "move", "units": MAIN_TANKS, "to": STAGING} in orders


def test_overextended_an_enemy_wave_met_on_the_way_abandons_the_strike_and_main_returns(match_record):
    agent = strike_agent()
    started(agent, escort(2))
    on_the_way = [own(300 + number, "1tnk", 50 + number, 30) for number in range(4)]
    wave = [foe(530 + number, "3tnk", 54, 28 + number) for number in range(6)]

    orders = ask(agent, state(1005, on_the_way, [*escort(2), *wave], [refinery()]))
    rested = ask(agent, state(1010, at_staging(), escort(2), [refinery()]))

    assert strike_events(match_record)[1:] == [{"phase": "end", "target": 600, "outcome": "abandoned"}]
    assert {"cmd": "move", "units": MAIN_TANKS, "to": STAGING} in orders
    assert not [order for order in rested if order.get("to") == [74, 13]]


def test_overextended_base_defense_calling_main_home_ends_the_strike_recalled(match_record):
    agent = strike_agent()
    started(agent, escort(2))
    agent.ctx.groups.recall(MAIN)

    ask(agent, state(1005, at_staging(), escort(2), [refinery()]))

    assert strike_events(match_record)[1:] == [{"phase": "end", "target": 600, "outcome": "recalled"}]


def test_overextended_the_commander_s_hold_or_retreat_override_holds_main_back_and_an_advance_does_not():
    manager, ctx = CombatManager(), new_agent().ctx
    retreat = SimpleNamespace(is_active=False)
    ctx.commander = SimpleNamespace(retreat=retreat)
    manager.command(hold_action())
    holding = manager._held_back(ctx)
    manager.command(replace(hold_action(), kind="advance", key=("advance",)))
    advancing = manager._held_back(ctx)
    retreat.is_active = True

    assert (holding, advancing, manager._held_back(ctx)) == (True, False, True)


# -- the keys --------------------------------------------------------------------------------------------


def test_overextended_ships_on_at_25_and_8_cells_in_the_built_ins_and_the_tracked_file():
    for shipped in (Tuning().combat, Tuning.from_file(REPO_ROOT).combat):
        keys = (shipped.overextended_min_enemy_start_cells, shipped.overextended_defense_clear_cells)
        assert (shipped.strike_overextended, keys) == (True, (25, 8))


# -- the commander's targets block ---------------------------------------------------------------------------------


def test_overextended_the_commander_s_targets_block_lists_the_field_after_the_targets_near_main():
    agent = strike_agent()
    harvester = foe(520, "harv", 75, 18)
    ask(agent, state(1000, at_staging(), [*escort(2), harvester], [refinery()]))
    body = list(agent.world.combat_units.values())

    found = command_targets.local_targets(body, (32, 48), agent.world, agent.ctx)
    lines = command_targets.targets_lines(found, {"proc": "ore refinery", "harv": "ore truck"}, agent.world, DETAILS[0])

    (field,) = [target for target in found if target.kind == command_targets.FIELD]
    assert [record.id for record in field.members] == [600, 520]
    assert lines[-1] == f"T{len(found)}: over-extended field: 1 ore refinery, 1 ore truck at (74,13), 55 cells, win 80%"


def test_overextended_the_commander_s_targets_block_has_no_field_with_the_switch_off():
    agent = strike_agent(tuning=OVEREXTENDED_OFF)
    ask(agent, state(1000, at_staging(), escort(2), [refinery()]))

    found = command_targets.local_targets(list(agent.world.combat_units.values()), (32, 48), agent.world, agent.ctx)

    assert [target.kind for target in found if target.kind == command_targets.FIELD] == []


# -- going on to an exposed expansion (overextended_chain) ---------------------------------------------------------

# An enemy yard 44 cells from the enemy's start and 35 from the far field: an exposed expansion, not the field's home.
EXPOSED_YARD = foe(700, "fact", 40, 20)


@pytest.mark.parametrize("chain, expected", [(True, 700), (False, None)], ids=["on", "off"])
def test_overextended_chain_harvesters_out_of_sight_send_the_force_on_to_an_exposed_yard_not_home(chain, expected):
    tuning = Tuning(combat=CombatTuning(overextended_chain=chain))
    agent = strike_agent(tuning=tuning)
    harvester = foe(520, "harv", 75, 18)
    ask(agent, state(1000, at_staging(), [harvester], [EXPOSED_YARD]))
    ask(agent, state(1000 + 20 * TICKS_PER_SECOND - 1, at_staging()))

    orders = ask(agent, state(1000 + 20 * TICKS_PER_SECOND, at_staging()))

    strike = _strike(agent)
    assert strike.target_id == expected
    assert ({"cmd": "move", "units": MAIN_TANKS, "to": STAGING} in orders) is not chain
    if chain:
        assert sorted(strike.units) == MAIN_TANKS
