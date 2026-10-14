"""The strike near us weighed on realistic odds: the enemy units beside the target, the enemy army's estimate, main
gathered at the staging point, main as a whole only near our base, and the abandon in every cycle."""

from __future__ import annotations

from dataclasses import replace

import pytest

from situations import ask, base_state, new_agent
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.scout_report import ScoutReport
from ucla_agent.games.redalert.strike import LONE_TARGET_RATIO, near_ratio
from ucla_agent.games.redalert.tuning import Tuning

# The Allied base of the fixture map: its yard at the center of the base (22, 48), and a refinery 18 cells east of
# it, so that an enemy yard up to 15 cells from that refinery is a must-kill target.
YARD = ("fact", 22, 48)
FORWARD_REFINERY = ("proc", 40, 48)
# The staging point: 10 cells from the center of the base toward the enemy's start at (74, 48).
STAGING = [32, 48]
# An enemy yard 30 cells from the center of our base, and one 19.7 cells from it, within far_rescue_cells (20).
FAR_YARD = {"id": 600, "type": "fact", "x": 52, "y": 48, "hp": 100}
NEAR_YARD = {"id": 601, "type": "fact", "x": 40, "y": 40, "hp": 100}


class Always:
    """A plan rule that keeps one plan."""

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


def main_at_staging() -> list[dict]:
    """main worth 4,000 at the staging point: two heavy tanks nearest the far yard, then two medium tanks."""
    return [own(300, "3tnk", 33, 48), own(301, "3tnk", 33, 49), own(302, "2tnk", 32, 48), own(303, "2tnk", 32, 49)]


def main_spread() -> list[dict]:
    """The four tanks of `main_at_staging` scattered over the base, none of them gathered at the staging point."""
    return [own(300, "3tnk", 22, 58), own(301, "3tnk", 22, 38), own(302, "2tnk", 14, 48), own(303, "2tnk", 26, 60)]


def yard_guard() -> list[dict]:
    """12 enemy units within 8 cells of the far yard, worth 5,000: four light tanks, seven rocket soldiers, a rifle."""
    tanks = [foe(700 + number, "1tnk", 54, 45 + number) for number in range(4)]
    rockets = [foe(710 + number, "e3", 56, 44 + number) for number in range(7)]
    return [*tanks, *rockets, foe(720, "e1", 57, 48)]


def state(tick: int, units=(), enemies=(), buildings=(FAR_YARD,)) -> dict:
    """A state of the Allied base with its forward refinery at that tick, these own units and enemies in sight."""
    shown = {"units": list(units), "visible_enemies": list(enemies), "visible_enemy_buildings": list(buildings)}
    return base_state("england", YARD, FORWARD_REFINERY, tick=tick, **shown)


def strike_agent(enemy_army: int = 0, tuning: Tuning | None = None):
    """An agent under HOLD with the combat manager alone, the enemy army estimated at `enemy_army`."""
    agent = new_agent(CombatManager(), tuning=tuning, plan_selector=Always("HOLD"))
    agent.ctx.scout_report = replace(ScoutReport(), enemy_combat_value=enemy_army)
    return agent


def combat_events(match_record, reason: str) -> list[dict]:
    """The `combat` events of a reason, without their ticks and reason."""
    logged = [data for data in match_record("combat") if data["reason"] == reason]
    return [{key: value for key, value in data.items() if key not in ("tick", "reason")} for data in logged]


def to_cell(orders: list[dict], cell: list[int]) -> list[dict]:
    """The orders of a cycle sent to a cell or at the target standing there."""
    return [order for order in orders if order.get("to") == cell]


# -- the start ----------------------------------------------------------------------------------------------------


def test_strike_near_a_yard_30_cells_out_with_12_units_worth_5000_against_main_worth_4000_is_declined_once(
    match_record,
):
    agent = strike_agent(enemy_army=5000)

    first = ask(agent, state(1000, main_at_staging(), yard_guard()))
    again = ask(agent, state(1005, main_at_staging(), yard_guard()))

    gathered = list(agent.world.combat_units.values())
    assert round(near_ratio(gathered, agent.world.enemy_memory[600], agent.world, agent.ctx), 2) == 0.8
    assert combat_events(match_record, "strike_near") == []
    assert combat_events(match_record, "strike_near_declined") == [
        {
            "target": 600,
            "target_type": "fact",
            "cell": [52, 48],
            "distance": 30.0,
            "gathered": 4,
            "ratio": 0.8,
            "strength": 4000,
            "enemy_army": 5000,
        }
    ]
    assert agent.ctx.strike_target is None and not to_cell([*first, *again], [52, 48])


def test_strike_near_a_declined_target_is_told_again_after_30_s(match_record):
    agent = strike_agent(enemy_army=5000)
    ask(agent, state(1000, main_at_staging(), yard_guard()))
    ask(agent, state(1000 + 30 * TICKS_PER_SECOND - 1, main_at_staging(), yard_guard()))

    ask(agent, state(1000 + 30 * TICKS_PER_SECOND, main_at_staging(), yard_guard()))

    assert len(combat_events(match_record, "strike_near_declined")) == 2


def test_strike_near_the_yard_alone_with_the_army_estimate_under_2x_main_is_struck_by_the_gathered_units(match_record):
    agent = strike_agent(enemy_army=5000)
    straggler = own(304, "1tnk", 22, 60)

    orders = ask(agent, state(1000, [*main_at_staging(), straggler]))

    # 2,500 is half the estimate: the two heavy tanks (2,300) fall short, and a medium tank makes 3,150.
    (start,) = combat_events(match_record, "strike_near")
    assert start == {
        "phase": "start",
        "target": 600,
        "target_type": "fact",
        "cell": [52, 48],
        "units": [300, 301, 302],
        "ratio": LONE_TARGET_RATIO,
    }
    assert {"cmd": "attack_move", "units": [300, 301, 302], "to": [52, 48]} in orders


def test_strike_near_a_lone_yard_reads_1_5_not_99():
    agent = strike_agent()
    ask(agent, state(1000, main_at_staging()))
    yard, units = agent.world.enemy_memory[600], list(agent.world.combat_units.values())

    assert near_ratio(units, yard, agent.world, agent.ctx) == LONE_TARGET_RATIO == 1.5


def test_strike_near_the_yard_alone_with_the_army_estimate_over_2x_main_is_declined(match_record):
    agent = strike_agent(enemy_army=8400)

    ask(agent, state(1000, main_at_staging()))

    assert combat_events(match_record, "strike_near") == []
    assert [event["strength"] for event in combat_events(match_record, "strike_near_declined")] == [4000]


@pytest.mark.parametrize(
    "yard, is_struck",
    [(FAR_YARD, False), (NEAR_YARD, True)],
    ids=["30 cells from the base", "19.7 cells from the base"],
)
def test_strike_near_with_no_strike_force_main_as_a_whole_goes_only_within_20_cells_of_the_base(
    match_record, yard, is_struck
):
    agent = strike_agent()
    cell = [yard["x"], yard["y"]]

    orders = ask(agent, state(1000, main_spread(), buildings=[yard]))

    # main as a whole makes the near yard its objective, gathering at the staging point on its way there.
    struck = agent.ctx.strike_target
    assert (struck is not None and struck.id == yard["id"]) == is_struck
    assert is_struck or not to_cell(orders, cell)
    declined = [event["target"] for event in combat_events(match_record, "strike_near_declined")]
    assert declined == ([] if is_struck else [yard["id"]])


# -- the abandon ----------------------------------------------------------------------------------------------------


def test_strike_near_a_strike_meeting_enemies_worth_2x_its_force_on_the_way_ends_and_goes_back_to_staging(
    match_record,
):
    agent = strike_agent(enemy_army=5000)
    ask(agent, state(1000, main_at_staging()))
    on_the_way = [
        own(300, "3tnk", 42, 48),
        own(301, "3tnk", 42, 49),
        own(302, "2tnk", 41, 48),
        own(303, "2tnk", 32, 49),
    ]
    # Nine light tanks, 6,300, against the force's 3,150.
    wave = [foe(730 + number, "1tnk", 46, 44 + number) for number in range(9)]

    orders = ask(agent, state(1005, on_the_way, wave))
    rested = ask(agent, state(1010, main_at_staging()))

    assert combat_events(match_record, "strike_near")[1:] == [{"phase": "end", "target": 600, "outcome": "abandoned"}]
    assert {"cmd": "move", "units": [300, 301, 302], "to": STAGING} in orders
    assert not to_cell(rested, [52, 48])


def test_strike_near_main_as_a_whole_is_rechecked_every_cycle_and_all_of_it_goes_back_to_staging(match_record):
    agent = strike_agent()
    ask(agent, state(1000, main_spread(), buildings=[NEAR_YARD]))
    # Ten light tanks beside the yard, 7,000, against main's 4,000.
    guard = [foe(740 + number, "1tnk", 42, 36 + number) for number in range(10)]

    orders = ask(agent, state(1005, main_spread(), guard, buildings=[NEAR_YARD]))

    assert combat_events(match_record, "strike_near")[1:] == [{"phase": "end", "target": 601, "outcome": "abandoned"}]
    assert {"cmd": "move", "units": [300, 301, 302, 303], "to": STAGING} in orders
    assert agent.ctx.strike_target is None
