"""The closing-army intercept: main meets an enemy army closing on a yard, the travelling MCV or its site, or the base,
in front of the target when it is strong enough, and defends the target or holds otherwise."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import situations
from situations import OpeningThen, ask, base_state, new_agent
from switches_off import expansion_off, placement_off, tuning_off, upkeep_off
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.closing_intercept import intercept_cell
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.command_threat import ClosingWatch
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.upkeep import UpkeepManager

# The Allied base of the fixture map: its yard at the center of the base (22, 48), the enemy's start at (74, 48) and
# the staging point 10 cells toward it.
YARD = ("fact", 22, 48)
STAGING = [32, 48]
# The travelling MCV 16.1 cells from the center of the base, within far_rescue_cells (20).
MCV_ID = 500
MCV_CELL = (36, 56)
START = 1000
REPO_ROOT = Path(__file__).resolve().parents[2]


class Plan:
    """A plan rule that keeps the plan a test sets."""

    def __init__(self, plan: str) -> None:
        self.plan = plan

    def select(self, world, ctx, choice) -> str:
        return self.plan


def own(unit_id: int, unit_type: str, x: int, y: int) -> dict:
    """One own unit as an observation lists it, idle."""
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": 100, "act": None, "idle": True}


def foe(enemy_id: int, enemy_type: str, x: int, y: int) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


def army(x: int, y: int = 52) -> list[dict]:
    """An enemy army worth 6,200 around (x, y): four heavy tanks, four rocket soldiers and four rifles."""
    tanks = [foe(700 + number, "3tnk", x - 1, y - 2 + number) for number in range(4)]
    rockets = [foe(710 + number, "e3", x, y - 2 + number) for number in range(4)]
    rifles = [foe(720 + number, "e1", x + 1, y - 2 + number) for number in range(4)]
    return [*tanks, *rockets, *rifles]


def main_at_staging(tanks: int) -> list[dict]:
    """main gathered at the staging point: this many medium tanks, and with four of them a rocket soldier."""
    medium = [own(300 + number, "2tnk", 32 + number % 2, 45 + number % 7) for number in range(tanks)]
    return [*medium, own(300 + tanks, "e3", 32, 44)] if tanks == WEAK else medium


# Sixteen medium tanks are 2.2 times the army; four and a rocket soldier 0.6 times it.
STRONG, WEAK = 16, 4
WEAK_UNITS = list(range(300, 300 + WEAK + 1))


def mcv(x: int = MCV_CELL[0], y: int = MCV_CELL[1]) -> dict:
    return own(MCV_ID, "mcv", x, y)


def state(tick: int, units=(), enemies=(), buildings=()) -> dict:
    """A state of the Allied base at that tick, these own units and enemies in sight."""
    shown = {"units": list(units), "visible_enemies": list(enemies), "visible_enemy_buildings": list(buildings)}
    return base_state("england", YARD, tick=tick, **shown)


def intercept_agent(plan: Plan | None = None, tuning: Tuning | None = None):
    """An agent with the combat manager alone under a plan the test may change, HOLD unless one is given."""
    return new_agent(CombatManager(), tuning=tuning, plan_selector=plan or Plan("HOLD"))


def closing_events(match_record) -> list[dict]:
    """The closing_intercept combat events, without their ticks and reason."""
    logged = [data for data in match_record("combat") if data["reason"] == "closing_intercept"]
    return [{key: value for key, value in data.items() if key not in ("tick", "reason")} for data in logged]


def moves(orders: list[dict]) -> list[dict]:
    return [order for order in orders if order.get("cmd") in ("move", "attack_move")]


# -- the start --------------------------------------------------------------------------------------------------------


def test_closing_intercept_a_6200_army_24_cells_from_the_mcv_and_closing_makes_main_at_2_2_intercept_8_cells_toward_it(
    match_record,
):
    agent = intercept_agent()
    ask(agent, state(START, [*main_at_staging(STRONG), mcv()], army(64)))

    orders = ask(agent, state(START + 15 * TICKS_PER_SECOND, [*main_at_staging(STRONG), mcv()], army(60)))

    # The army's centroid stands at (60, 52), 24.3 cells from the MCV, and was 28.3 cells off 15 s before.
    front = cells.toward(MCV_CELL, (60, 52), 8)
    assert round(cells.distance(front, MCV_CELL)) == 8
    (start,) = closing_events(match_record)
    assert start == {
        "phase": "start",
        "response": "intercept",
        "target": "mcv",
        "target_id": MCV_ID,
        "cell": list(MCV_CELL),
        "to": list(front),
        "value": 6200,
        "ratio": 2.19,
    }
    assert {"cmd": "attack_move", "units": list(range(300, 300 + STRONG)), "to": list(front)} in orders


def test_closing_intercept_a_6200_army_newly_seen_24_cells_from_the_mcv_counts_as_closing(match_record):
    agent = intercept_agent()

    ask(agent, state(START, [*main_at_staging(STRONG), mcv()], army(60)))

    assert [event["response"] for event in closing_events(match_record)] == ["intercept"]


def test_closing_intercept_at_0_6_main_sends_no_intercept_and_defends_the_mcv_within_20_cells_of_the_base(match_record):
    agent = intercept_agent()

    orders = ask(agent, state(START, [*main_at_staging(WEAK), mcv()], army(60)))

    (start,) = closing_events(match_record)
    assert (start["response"], start["to"], start["ratio"]) == ("defend", list(MCV_CELL), 0.6)
    assert moves(orders) == [{"cmd": "attack_move", "units": WEAK_UNITS, "to": list(MCV_CELL)}]


def test_closing_intercept_at_0_6_main_holds_at_the_staging_point_for_an_mcv_beyond_20_cells_of_the_base(
    match_record,
):
    agent = intercept_agent()
    far_mcv = (44, 60)

    orders = ask(agent, state(START, [*main_at_staging(WEAK), mcv(*far_mcv)], army(66, 58)))

    (start,) = closing_events(match_record)
    assert (start["response"], start["target"], start["to"]) == ("hold", "mcv", STAGING)
    assert not [order for order in moves(orders) if order["to"] != STAGING]


def test_closing_intercept_an_army_moving_away_triggers_nothing(match_record):
    plan = Plan("PRESERVE")
    agent = intercept_agent(plan)
    # First seen while main may not answer, 20.4 cells from the MCV.
    ask(agent, state(START, [*main_at_staging(STRONG), mcv()], army(56)))
    plan.plan = "HOLD"

    orders = ask(agent, state(START + 15 * TICKS_PER_SECOND, [*main_at_staging(STRONG), mcv()], army(60)))

    assert closing_events(match_record) == []
    assert not [order for order in moves(orders) if order["to"] not in (STAGING,)]


def test_closing_intercept_no_intercept_starts_under_preserve(match_record):
    agent = intercept_agent(Plan("PRESERVE"))

    ask(agent, state(START, [*main_at_staging(STRONG), mcv()], army(60)))

    assert closing_events(match_record) == []


def test_closing_intercept_an_army_worth_under_1000_or_beyond_30_cells_triggers_nothing(match_record):
    agent = intercept_agent()
    # Three rocket soldiers, 900.
    small = army(60)[4:7]

    ask(agent, state(START, [*main_at_staging(STRONG), mcv()], small))
    ask(agent, state(START + TICKS_PER_SECOND, [*main_at_staging(STRONG), mcv()], army(70)))

    assert closing_events(match_record) == []


def test_closing_intercept_with_the_switch_off_main_answers_no_army(match_record):
    tuning = Tuning()
    switched_off = replace(tuning, base_defense=replace(tuning.base_defense, closing_intercept=False))
    agent = intercept_agent(tuning=switched_off)

    ask(agent, state(START, [*main_at_staging(STRONG), mcv()], army(60)))

    assert closing_events(match_record) == []


def test_closing_intercept_an_army_within_12_cells_of_main_is_main_s_own_fight_and_starts_no_response(match_record):
    agent = intercept_agent()
    # main 9.5 cells from the army, which stands 14.6 cells from the MCV and 28 from the base: main's fight counts it.
    near_main = [own(300 + number, "2tnk", 40 + number % 2, 50 + number % 4) for number in range(STRONG)]

    ask(agent, state(START, [*near_main, mcv()], army(50, 52)))

    assert closing_events(match_record) == []


def test_closing_intercept_at_most_one_runs_the_target_its_army_stands_nearest_taken(match_record):
    agent = intercept_agent()
    # One army 24 cells from the MCV and 38 from the yard: the MCV is taken, and only one response starts.
    ask(agent, state(START, [*main_at_staging(STRONG), mcv()], army(60)))
    ask(agent, state(START + TICKS_PER_SECOND, [*main_at_staging(STRONG), mcv()], army(59)))

    assert [(event["phase"], event["target"]) for event in closing_events(match_record)] == [("start", "mcv")]


# -- the intercept cell -----------------------------------------------------------------------------------------------


def test_closing_intercept_the_cell_is_pulled_back_toward_the_target_out_of_a_defense_s_coverage():
    covered, open_ground = intercept_agent(), intercept_agent()
    turret = {"id": 900, "type": "gun", "x": 48, "y": 56, "hp": 100}
    ask(covered, state(START, main_at_staging(WEAK), buildings=[turret]))
    ask(open_ground, state(START, main_at_staging(WEAK)))

    pulled_back = intercept_cell(MCV_CELL, (60, 56), covered.world, covered.ctx)

    # The gun covers 8.5 cells, its range and defended_margin_cells: (40, 56) lies 8 cells from it, (39, 56) 9.
    assert intercept_cell(MCV_CELL, (60, 56), open_ground.world, open_ground.ctx) == (44, 56)
    assert pulled_back == (39, 56)


# -- the end ----------------------------------------------------------------------------------------------------------


def test_closing_intercept_ends_once_the_army_is_unseen_20_s_and_main_goes_back_to_the_staging_point(match_record):
    agent = intercept_agent()
    ask(agent, state(START, [*main_at_staging(STRONG), mcv()], army(60)))
    out = [own(300 + number, "2tnk", 44, 50 + number % 7) for number in range(STRONG)]
    ask(agent, state(START + 20 * TICKS_PER_SECOND, [*out, mcv()]))

    orders = ask(agent, state(START + 20 * TICKS_PER_SECOND + 1, [*out, mcv()]))

    phases = [(event["phase"], event.get("outcome")) for event in closing_events(match_record)]
    assert phases == [("start", None), ("end", "gone")]
    assert {"cmd": "move", "units": list(range(300, 300 + STRONG)), "to": STAGING} in orders


def test_closing_intercept_ends_abandoned_once_main_falls_under_0_9_and_goes_back_to_the_staging_point(
    match_record,
):
    agent = intercept_agent()
    ask(agent, state(START, [*main_at_staging(STRONG), mcv()], army(60)))
    out = [own(300 + number, "2tnk", 44, 50 + number % 7) for number in range(STRONG)]
    # Twelve more heavy tanks join the army: 20,000 against main's 13,600.
    wave = [foe(730 + number, "3tnk", 58, 46 + number) for number in range(12)]
    ask(agent, state(START + 5, [*out, mcv()], [*army(58), *wave]))

    orders = ask(agent, state(START + 10, [*out, mcv()], [*army(58), *wave]))

    end = closing_events(match_record)[-1]
    assert (end["phase"], end["outcome"]) == ("end", "abandoned") and end["ratio"] < 0.9
    assert {"cmd": "move", "units": list(range(300, 300 + STRONG)), "to": STAGING} in orders


def test_closing_intercept_ends_when_the_army_stands_beyond_40_cells_of_its_target(match_record):
    agent = intercept_agent()
    ask(agent, state(START, [*main_at_staging(STRONG), mcv()], army(60)))

    ask(agent, state(START + 5, [*main_at_staging(STRONG), mcv()], army(78, 56)))

    assert [event.get("outcome") for event in closing_events(match_record)] == [None, "away"]


def test_closing_intercept_ends_recalled_when_the_plan_turns_to_preserve(match_record):
    plan = Plan("HOLD")
    agent = intercept_agent(plan)
    ask(agent, state(START, [*main_at_staging(STRONG), mcv()], army(60)))
    plan.plan = "PRESERVE"

    ask(agent, state(START + 5, [*main_at_staging(STRONG), mcv()], army(59)))

    assert [event.get("outcome") for event in closing_events(match_record)] == [None, "recalled"]


# -- the commander's watch and the keys -------------------------------------------------------------------------------


def test_closing_intercept_the_commander_s_closing_watch_still_names_an_army_closing_on_the_base():
    agent = intercept_agent(Plan("PRESERVE"))
    ask(agent, state(START, [*main_at_staging(WEAK)], army(40)))
    watch = ClosingWatch()

    watch.note(agent.world)

    assert watch.closing(agent.world) == (40, 52)


@pytest.mark.parametrize(
    "key, value",
    [
        ("closing_intercept", True),
        ("closing_min_value", 1000),
        ("closing_cells", 30),
        ("intercept_front_cells", 8),
        ("closing_min_ratio", 1.0),
    ],
)
def test_closing_intercept_the_base_defense_keys_ship_with_their_values(key, value):
    assert getattr(Tuning().base_defense, key) == value
    assert getattr(Tuning.from_file(REPO_ROOT).base_defense, key) == value


# -- the travelling MCV -----------------------------------------------------------------------------------------------


def test_closing_intercept_mcv_hold_at_0_6_the_mcv_holds_where_it_stands_while_main_defends():
    agent = intercept_agent()

    ask(agent, state(START, [*main_at_staging(WEAK), mcv()], army(60)))

    assert agent.ctx.mcv_halt == MCV_CELL


def test_closing_intercept_mcv_hold_an_army_that_keeps_closing_sends_the_mcv_back_to_the_base_center():
    agent = intercept_agent()
    ask(agent, state(START, [*main_at_staging(WEAK), mcv()], army(60)))
    ask(agent, state(START + 5, [*main_at_staging(WEAK), mcv()], army(59)))
    held = agent.ctx.mcv_halt

    ask(agent, state(START + 10, [*main_at_staging(WEAK), mcv()], army(57)))

    # One cell nearer holds it; three cells nearer than as the hold began sends it home, and it stays sent home.
    assert held == MCV_CELL and agent.ctx.mcv_halt == agent.world.base_center
    ask(agent, state(START + 15, [*main_at_staging(WEAK), mcv()], army(58)))
    assert agent.ctx.mcv_halt == agent.world.base_center


def test_closing_intercept_mcv_hold_none_while_main_intercepts_or_once_the_response_ends():
    strong, weak = intercept_agent(), intercept_agent()
    ask(strong, state(START, [*main_at_staging(STRONG), mcv()], army(60)))
    ask(weak, state(START, [*main_at_staging(WEAK), mcv()], army(60)))

    ask(weak, state(START + 5, [*main_at_staging(WEAK), mcv()], army(78, 56)))

    assert strong.ctx.mcv_halt is None and weak.ctx.mcv_halt is None


# The expansion map of test_expansion: a lane field east of the base for the MCV's site, a war factory, a depot.
BASE = (
    YARD,
    ("powr", 19, 48),
    ("tent", 22, 44),
    ("proc", 24, 39),
    ("proc", 18, 38),
    ("powr", 26, 48),
    ("weap", 15, 48),
    ("powr", 26, 52),
    ("pbox", 28, 44),
    ("fix", 15, 53),
)
HOME_FIELD = OrePatch(center=(22, 40), cells=frozenset((x, y) for x in (21, 22, 23) for y in (39, 40, 41)))
LANE_FIELD = OrePatch(center=(40, 47), cells=frozenset((x, y) for x in (39, 40, 41) for y in (46, 47, 48)))
SECOND_YARD = tuning_off(
    upkeep=replace(upkeep_off(), refineries_max=2, refineries_max_late=2),
    expansion=expansion_off(second_yard=True),
    placement=replace(placement_off(), creep_next_patch=True),
)
EXPANSION_MCV = situations.unit(400, "mcv", 20, 57)
ESCORT = [situations.unit(500 + number, "2tnk", 24 + number, 58) for number in range(3)]
HARVESTERS = [situations.unit(300 + number, "harv", 30, 30, idle=False) for number in range(4)]


def test_closing_intercept_mcv_hold_the_mcv_s_rule_moves_it_to_the_halt_instead_of_on_to_its_site():
    agent = new_agent(UpkeepManager(), plan_selector=OpeningThen("HOLD"), tuning=SECOND_YARD)
    ask(agent, base_state("england", *BASE, tick=100, cash=0))
    agent.ctx.terrain = Terrain((HOME_FIELD, LANE_FIELD), HOME_FIELD.cells | LANE_FIELD.cells)
    units = [*HARVESTERS, EXPANSION_MCV, *ESCORT]
    going = ask(agent, base_state("england", *BASE, tick=500 * TICKS_PER_SECOND, units=units))
    ask(agent, base_state("england", *BASE, tick=501 * TICKS_PER_SECOND, units=units))
    site = agent.ctx.mcv_site
    agent.ctx.mcv_halt = (21, 56)

    held = ask(agent, base_state("england", *BASE, tick=502 * TICKS_PER_SECOND, units=units))

    sent = [order for order in going if order.get("units") == [400]]
    assert sent and sent[0]["to"] != [21, 56] and site is not None
    assert [order for order in held if order.get("units") == [400]] == [{"cmd": "move", "units": [400], "to": [21, 56]}]
