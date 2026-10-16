"""The exposed-expansion strike: main striking a known enemy expansion nothing defends, and the chase leash."""

from __future__ import annotations

import pytest

from situations import REPO_ROOT, ask, base_state, event, new_agent
from ucla_agent.games.redalert import exposed_expansion
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.managers import MAJOR_THREAT
from ucla_agent.games.redalert.strike import Strike
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning

# The Allied base of the fixture map: its yard, and the staging point 10 cells from it toward the enemy's start at
# (74, 48).
YARD = ("fact", 22, 48)
STAGING = [32, 48]
# An enemy yard and refinery 31 cells from the enemy's start, 47 from our base: an expansion.
FAR_YARD = (60, 20)
TANKS = [300, 301, 302, 303, 304, 305]
EXPOSED_OFF = Tuning(combat=CombatTuning(strike_exposed_expansion=False))
LEASH_OFF = Tuning(combat=CombatTuning(chase_leash=False))


def own(unit_id: int, unit_type: str, x: int, y: int, *, idle: bool = True, act: str | None = None) -> dict:
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": 100, "act": act, "idle": idle}


def foe(enemy_id: int, enemy_type: str, x: int, y: int, hp: int = 100) -> dict:
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": hp}


def at_staging(count: int = 6) -> list[dict]:
    """Medium tanks at the staging point."""
    return [own(300 + number, "2tnk", 32 + number % 2, 48 + number // 2) for number in range(count)]


def expansion(hp: int = 100) -> list[dict]:
    return [foe(600, "fact", *FAR_YARD, hp=hp), foe(601, "proc", FAR_YARD[0] + 3, FAR_YARD[1])]


class Always:
    def __init__(self, plan: str) -> None:
        self.plan = plan

    def select(self, world, ctx, choice) -> str:
        return self.plan


def strike_agent(plan: str = "HOLD", tuning: Tuning | None = None):
    """An agent under a plan with the combat manager alone, on a map with no ore field, its match begun."""
    agent = new_agent(CombatManager(), tuning=tuning, plan_selector=Always(plan))
    ask(agent, state(900))
    agent.ctx.terrain = Terrain(())
    return agent


def state(tick: int, units=(), enemies=(), buildings=(), **observation) -> dict:
    shown = {"units": list(units), "visible_enemies": list(enemies), "visible_enemy_buildings": list(buildings)}
    return base_state("england", YARD, tick=tick, **shown, **observation)


def strike_events(match_record) -> list[dict]:
    logged = [data for data in match_record("combat") if data["reason"] == "strike_expansion"]
    return [{key: value for key, value in data.items() if key not in ("tick", "reason", "ratio")} for data in logged]


def _strike(agent) -> Strike:
    (manager,) = [manager for manager in agent.managers if isinstance(manager, CombatManager)]
    return manager._strike


def naming(orders: list[dict], ids: set[int]) -> list[dict]:
    return [order for order in orders if ids & set(order.get("units", ()))]


# -- the targets -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "buildings, enemies, expected",
    [
        (expansion(), [], [600, 601]),
        ([foe(600, "fact", 70, 40)], [], []),
        ([*expansion(), foe(650, "pbox", 62, 22)], [], []),
        ([*expansion(), foe(650, "pbox", 75, 20)], [], [600, 601]),
        (expansion(), [foe(520, "e1", 62, 25)], []),
        ([foe(610, "powr", 62, 22)], [], []),
        ([*expansion(), foe(610, "powr", 62, 26)], [], [600, 601, 610]),
    ],
    ids=[
        "yard and refinery 31 cells from the start",
        "yard 9 cells from the start",
        "pillbox reaching it",
        "pillbox 15 cells away",
        "rifle seen 5 cells away",
        "power plant alone",
        "power plant beside the yard",
    ],
)
def test_exposed_a_yard_or_refinery_far_from_the_enemy_start_with_nothing_defending_it_is_a_target(
    buildings, enemies, expected
):
    agent = strike_agent()
    ask(agent, state(1000, at_staging(), enemies, buildings))

    found = exposed_expansion.targets(agent.world.base_center, agent.world, agent.ctx)

    assert sorted(record.id for record in found) == expected


def test_exposed_the_yard_comes_before_a_nearer_refinery_and_power_plant():
    agent = strike_agent()
    buildings = [foe(601, "proc", 55, 24), foe(610, "powr", 56, 22), foe(600, "fact", 61, 20)]
    ask(agent, state(1000, at_staging(), (), buildings))

    found = exposed_expansion.targets(agent.world.base_center, agent.world, agent.ctx)

    assert [record.id for record in found] == [600, 601, 610]


def test_exposed_a_refinery_destroyed_sends_the_force_on_to_the_yard_before_the_nearer_power_plant(match_record):
    agent = strike_agent()
    # The refinery alone is known first: the strike goes for it.
    ask(agent, state(1000, at_staging(), (), [foe(601, "proc", 55, 24)]))
    force = _strike(agent).units
    there = [own(unit_id, "2tnk", 55, 27) for unit_id in sorted(force)]
    rest = [foe(610, "powr", 52, 27), foe(600, "fact", 62, 20)]
    ask(agent, state(1005, there, (), [foe(601, "proc", 55, 24), *rest]))

    ask(agent, state(1010, there, (), rest, events=[event(1, "enemy_killed")]))

    phases = [(entry["phase"], entry["target"]) for entry in strike_events(match_record)]
    assert phases == [("start", 601), ("end", 601), ("start", 600)]


def test_exposed_a_rifle_seen_40_s_ago_no_longer_defends_it():
    agent = strike_agent()
    ask(agent, state(1000, at_staging(), [foe(520, "e1", 62, 25)], expansion()))

    ask(agent, state(2000, at_staging()))

    assert [record.id for record in exposed_expansion.targets(None, agent.world, agent.ctx)] == [600, 601]


def test_exposed_with_the_switch_off_there_is_no_target():
    agent = strike_agent(tuning=EXPOSED_OFF)
    ask(agent, state(1000, at_staging(), (), expansion()))

    assert exposed_expansion.targets(None, agent.world, agent.ctx) == []


# -- the start ------------------------------------------------------------------------------------------


def test_exposed_main_gathered_at_staging_strikes_the_yard_with_the_fewest_tanks_that_finish_it(match_record):
    agent = strike_agent()

    orders = ask(agent, state(1000, at_staging(), (), expansion()))

    (start,) = strike_events(match_record)
    force = start["units"]
    assert start["phase"] == "start" and start["target"] == 600 and start["cell"] == list(FAR_YARD)
    assert 0 < len(force) <= len(TANKS)
    assert naming(orders, set(force)) == [{"cmd": "attack_move", "units": force, "to": list(FAR_YARD)}]


def test_exposed_a_force_too_weak_to_finish_the_expansion_in_time_starts_no_strike(match_record):
    agent = strike_agent()

    ask(agent, state(1000, [own(300, "e1", 32, 48)], (), expansion()))

    assert strike_events(match_record) == []


def test_exposed_main_gathered_and_idle_away_from_staging_strikes_the_nearest(match_record):
    agent = strike_agent()
    aside = [own(300 + number, "2tnk", 40 + number % 2, 30 + number // 2) for number in range(6)]

    ask(agent, state(1000, aside, (), expansion()))

    assert [event["target"] for event in strike_events(match_record)] == [600]


def test_exposed_main_moving_away_from_staging_starts_no_strike(match_record):
    agent = strike_agent()
    moving = [own(300 + number, "2tnk", 40 + number % 2, 30 + number // 2, idle=False) for number in range(6)]

    ask(agent, state(1000, moving, (), expansion()))

    assert strike_events(match_record) == []


@pytest.mark.parametrize(
    "plan, threat, tuning",
    [("PRESERVE", None, None), ("HOLD", MAJOR_THREAT, None), ("HOLD", None, EXPOSED_OFF)],
    ids=["under PRESERVE", "under a major threat", "with the switch off"],
)
def test_exposed_no_strike_under_preserve_a_major_threat_or_with_the_switch_off(match_record, plan, threat, tuning):
    agent = strike_agent(plan, tuning)
    if threat is not None:
        agent.ctx.threat_level = threat

    ask(agent, state(1000, at_staging(), (), expansion()))

    assert strike_events(match_record) == []


# A known enemy missile silo by the enemy's start, and the tesla coils around it that no force of main's outweighs.
SILO = foe(700, "mslo", 72, 44)
COILS = [foe(710 + number, "tsla", 70 + number, 41) for number in range(6)]


def silo_starts(match_record) -> list[tuple[str, int]]:
    logged = [data for data in match_record("combat") if data["reason"] in ("strike_near", "strike_expansion")]
    return [(data["reason"], data["target"]) for data in logged if data["phase"] == "start"]


def test_exposed_a_known_silo_main_cannot_strike_yet_holds_back_no_strike_on_an_exposed_expansion(match_record):
    agent = strike_agent()

    ask(agent, state(1000, at_staging(), (), [SILO, *COILS, *expansion()]))

    assert silo_starts(match_record) == [("strike_expansion", 600)]


def test_exposed_a_known_silo_main_can_strike_comes_before_the_exposed_expansion(match_record):
    agent = strike_agent()

    ask(agent, state(1000, at_staging(), (), [SILO, *expansion()]))

    assert silo_starts(match_record) == [("strike_near", 700)]


# -- the end -------------------------------------------------------------------------------------------


def test_exposed_the_yard_destroyed_sends_the_force_on_to_the_refinery_beside_it(match_record):
    agent = strike_agent()
    ask(agent, state(1000, at_staging(), (), expansion()))
    force = _strike(agent).units
    there = [own(unit_id, "2tnk", FAR_YARD[0] - 2, FAR_YARD[1] + 2) for unit_id in sorted(force)]

    ask(agent, state(1005, there, (), [expansion()[1]], events=[event(1, "enemy_killed")]))

    phases = [(entry["phase"], entry["target"]) for entry in strike_events(match_record)]
    assert phases == [("start", 600), ("end", 600), ("start", 601)]
    assert _strike(agent).units == force


def test_exposed_the_strike_times_out_after_120_s(match_record):
    agent = strike_agent()
    ask(agent, state(1000, at_staging(), (), expansion()))
    away = [own(300 + number, "2tnk", 45, 30 + number) for number in range(6)]

    ask(agent, state(1000 + 120 * 25, away, (), expansion()))

    assert strike_events(match_record)[-1] == {"phase": "end", "target": 600, "outcome": "timeout"}


# -- the chase leash -----------------------------------------------------------------------------------


def chasing(tuning: Tuning | None = None) -> list[dict]:
    """The orders of a cycle with four tanks gathered at the staging point and two rocket soldiers chasing a target
    10 cells from the enemy's start."""
    agent = strike_agent(tuning=tuning)
    chasers = [own(310, "e3", 66, 50, idle=False, act="Attack"), own(311, "e3", 66, 51, idle=False, act="Attack")]
    return ask(agent, state(1000, [*at_staging(4), *chasers]))


def test_leash_two_units_chasing_alone_into_the_enemy_base_are_moved_back_to_main(match_record):
    orders = chasing()

    (called,) = naming(orders, {310, 311})
    assert called["cmd"] == "move" and called["units"] == [310, 311]
    assert [data["units"] for data in match_record("combat") if data["reason"] == "chase_leash"] == [[310, 311]]


def test_leash_with_the_switch_off_the_chasers_are_left_alone():
    assert not [order for order in naming(chasing(LEASH_OFF), {310, 311}) if order["cmd"] == "move"]


# -- the keys ------------------------------------------------------------------------------------------


def test_exposed_and_leash_ship_on_in_the_built_ins_and_the_tracked_file():
    for shipped in (Tuning().combat, Tuning.from_file(REPO_ROOT).combat):
        keys = (
            shipped.exposed_expansion_start_cells,
            shipped.exposed_expansion_building_cells,
            shipped.exposed_expansion_clear_cells,
            shipped.exposed_expansion_unit_seconds,
            shipped.exposed_expansion_max_seconds,
            shipped.exposed_expansion_chain_cells,
            shipped.exposed_expansion_kill_seconds,
            shipped.exposed_expansion_kill_share,
        )
        leash = (
            shipped.chase_leash_cells,
            shipped.chase_leash_base_cells,
            shipped.chase_leash_near_cells,
            shipped.chase_leash_min_units,
        )
        assert (shipped.strike_exposed_expansion, keys) == (True, (20, 8, 10, 30, 120, 30, 60, 2.0))
        assert (shipped.chase_leash, leash) == (True, (15, 25, 6, 4))
