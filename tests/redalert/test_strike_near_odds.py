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
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning

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


# The strike sized by half the enemy army's estimate too, as these odds were worked out on (see `strike`).
BY_ARMY = Tuning(combat=CombatTuning(strike_near_by_defenses=False))


def strike_agent(enemy_army: int = 0, tuning: Tuning | None = None):
    """An agent under HOLD with the combat manager alone, the enemy army estimated at `enemy_army`, the strike sized
    by the enemy army's estimate too unless a tuning is given."""
    agent = new_agent(CombatManager(), tuning=tuning or BY_ARMY, plan_selector=Always("HOLD"))
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


# -- sized by its defenses (strike_near_by_defenses) -----------------------------------------------------------

BY_DEFENSES = Tuning()


@pytest.mark.parametrize(
    "open_building, expected", [(False, [300]), (True, [300, 301, 302])], ids=["by defenses", "by its hp"]
)
def test_by_defenses_the_lone_yard_is_struck_however_big_the_enemy_army_estimate(match_record, open_building, expected):
    tuning = Tuning(combat=CombatTuning(strike_near_open_building=open_building))
    agent = strike_agent(enemy_army=20_000, tuning=tuning)

    ask(agent, state(1000, main_at_staging()))

    # By its defenses one tank outweighs a lone yard; by its hit points, two heavy tanks and a medium one bring it down.
    (start,) = combat_events(match_record, "strike_near")
    assert (start["target"], start["units"]) == (600, expected)


def test_open_building_a_fresh_yard_with_main_away_is_struck_by_the_idle_tanks_at_home(match_record):
    agent = strike_agent(enemy_army=20_000, tuning=BY_DEFENSES)
    away = [own(310 + n, "2tnk", 70, 20 + n) for n in range(4)]
    for unit in away:
        unit["idle"], unit["act"] = False, "AttackMoveActivity"
    home = [own(300, "3tnk", 24, 50), own(301, "3tnk", 25, 50), own(302, "2tnk", 24, 51), own(303, "2tnk", 25, 51)]

    ask(agent, state(1000, [*away, *home]))

    (start,) = combat_events(match_record, "strike_near")
    assert start["target"] == 600 and set(start["units"]) <= {300, 301, 302, 303} and start["units"]


def test_open_building_the_strike_is_not_abandoned_on_its_ratio_within_30_s(match_record):
    agent = strike_agent(tuning=BY_DEFENSES)
    ask(agent, state(1000, main_at_staging()))
    on_the_way = [own(300 + n, "3tnk", 45, 48 + n) for n in range(2)]
    # Sixteen rocket soldiers beside the yard, in sight: the ratio at the target falls, none near the strikers.
    guard = [foe(720 + n, "e3", 52 + n % 2, 44 + n // 2) for n in range(16)]

    ask(agent, state(1005, [*on_the_way, *main_at_staging()[2:]], guard))

    (manager,) = [manager for manager in agent.managers if isinstance(manager, CombatManager)]
    assert manager._strike.target_id == 600


def test_by_defenses_the_guarded_yard_takes_the_force_that_outweighs_its_guard_or_is_declined(match_record):
    agent = strike_agent(enemy_army=20_000, tuning=BY_DEFENSES)

    ask(agent, state(1000, main_at_staging(), yard_guard()))

    assert combat_events(match_record, "strike_near") == []
    assert [event["target"] for event in combat_events(match_record, "strike_near_declined")] == [600]


def test_by_defenses_main_gathered_and_idle_away_from_staging_strikes_the_yard(match_record):
    agent = strike_agent(tuning=BY_DEFENSES)
    aside = [own(300, "3tnk", 30, 60), own(301, "3tnk", 31, 60), own(302, "2tnk", 30, 61), own(303, "2tnk", 31, 61)]

    ask(agent, state(1000, aside))

    assert [event["target"] for event in combat_events(match_record, "strike_near")] == [600]


# By the army, with the strike on exposed expansions off as well: the yard 22 cells from the enemy's start is one.
ARMY_ONLY = Tuning(combat=CombatTuning(strike_near_by_defenses=False, strike_exposed_expansion=False))


@pytest.mark.parametrize("tuning, expected", [(BY_DEFENSES, 600), (ARMY_ONLY, None)], ids=["by defenses", "by army"])
def test_by_defenses_a_yard_seen_60_s_ago_is_still_struck_and_by_army_it_is_not(tuning, expected):
    agent = strike_agent(tuning=tuning)
    ask(agent, state(1000, main_spread()))

    ask(agent, state(1000 + 60 * TICKS_PER_SECOND, main_at_staging(), buildings=()))

    (manager,) = [manager for manager in agent.managers if isinstance(manager, CombatManager)]
    assert manager._strike.target_id == expected


def test_by_defenses_a_turret_main_cannot_outweigh_is_sieged_by_its_long_range_units(match_record):
    agent = strike_agent(tuning=BY_DEFENSES)
    # A tesla coil 6 cells from the forward refinery, and main: two V2 launchers and a rifle at the staging point.
    coil = foe(650, "tsla", 46, 48)
    main = [own(300, "v2rl", 32, 48), own(301, "v2rl", 33, 48), own(302, "e1", 32, 49)]

    ask(agent, state(1000, main, buildings=(coil,)))
    ask(agent, state(1005, main, buildings=(coil,)))

    starts = [event for event in combat_events(match_record, "strike_near") if event["phase"] == "start"]
    assert [(event["target"], event["units"]) for event in starts] == [(650, [300, 301])]
    assert [event for event in combat_events(match_record, "strike_near") if event["phase"] == "end"] == []


def test_by_defenses_ships_on_at_25_cells_and_120_s_in_the_built_ins_and_the_tracked_file():
    from situations import REPO_ROOT

    for shipped in (Tuning().combat, Tuning.from_file(REPO_ROOT).combat):
        keys = (shipped.strike_near_whole_cells, shipped.strike_near_max_seconds)
        assert (shipped.strike_near_by_defenses, keys) == (True, (25, 120))
