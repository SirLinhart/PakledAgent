"""The rush watch: a verdict of rush, none or unknown on the enemy's first minutes, and the watcher on the way in."""

from __future__ import annotations

import dataclasses

import pytest

import situations
from situations import ask, base_state
from switches_off import new_agent, scouting_off, tuning_off
from ucla_agent.core import records
from ucla_agent.games.redalert.batch import Priority
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.tuning import Tuning

YARD = ("fact", 22, 48)
POWER = ("powr", 19, 52)
BARRACKS = ("tent", 18, 44)
RUSH_WATCH = dataclasses.replace(tuning_off(), scouting=scouting_off(rush_detection=True))
# Enemy buildings within base_seen_cells of the enemy's start at (74, 48): seeing one is seeing its base.
ENEMY_POWER_PLANT = {"id": 700, "type": "powr", "x": 78, "y": 48, "hp": 100}


def enemy(enemy_id: int, enemy_type: str, x: int, y: int) -> dict:
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


def at(seconds: float, units: list[dict] | None = None, **observation) -> dict:
    """An Allied base at a second of the match, with what the test adds."""
    return base_state(
        "england", YARD, POWER, BARRACKS, tick=round(seconds * TICKS_PER_SECOND), units=units or [], **observation
    )


def watch_agent(tuning: Tuning = RUSH_WATCH):
    return new_agent(ScoutingManager(), tuning=tuning)


def verdict_after(*states: dict, tuning: Tuning = RUSH_WATCH) -> str | None:
    agent = watch_agent(tuning)
    for state in states:
        ask(agent, state)
    return agent.ctx.rush_verdict


@pytest.mark.parametrize("seconds", [100, 200], ids=["at 100 s", "at 200 s"])
def test_two_enemy_barracks_seen_early_give_no_rush(seconds):
    barracks = [enemy(701, "tent", 78, 52), enemy(702, "barr", 70, 44)]

    assert verdict_after(at(seconds, visible_enemy_buildings=barracks)) == "none"


def test_an_enemy_war_factory_seen_at_120_s_with_one_refinery_gives_no_rush():
    seen = [enemy(701, "weap", 78, 52), enemy(710, "proc", 70, 42)]

    assert verdict_after(at(120, visible_enemy_buildings=seen)) == "none"


def test_six_enemy_infantry_15_cells_from_the_enemy_start_give_no_rush():
    rifles = [enemy(800 + number, "e1", 74, 63 + number % 2) for number in range(6)]

    assert verdict_after(at(200, visible_enemies=rifles)) == "unknown"


def test_six_enemy_infantry_25_cells_from_the_enemy_start_at_230_s_give_rush_with_the_sign_infantry_out(match_record):
    rifles = [enemy(800 + number, "e1", 49, 46 + number % 2) for number in range(6)]
    agent = watch_agent()

    ask(agent, at(230, visible_enemies=rifles))

    # Six rifles 27 cells from our base are an army approaching as well.
    signs = ["infantry_out", "army_approaching"]
    assert agent.ctx.rush_verdict == "rush" and match_record("rush_verdict")[-1]["signs"] == signs
    # Five rifles 27 cells from our base are worth 500, an army approaching at the shipped 300: the count alone is
    # tested with the army sign at 1000.
    by_count = dataclasses.replace(RUSH_WATCH, scouting=scouting_off(rush_detection=True, rush_approach_value=1000))
    assert verdict_after(at(230, visible_enemies=rifles[:5]), tuning=by_count) == "unknown"


# -- the warning of infantry massing at the enemy's start ----------------------------------------------------------


def massing(count: int) -> list[dict]:
    """Enemy rifles within 20 cells of the enemy's start at (74, 48)."""
    return [enemy(800 + number, "e1", 70 + number % 4, 52 + number // 4) for number in range(count)]


@pytest.mark.parametrize(
    "count, seconds, warned",
    [(8, 150, True), (7, 150, False), (8, 210, False), (8, 200, True)],
    ids=["8 at 150 s", "7 at 150 s", "8 at 210 s", "8 at 200 s"],
)
def test_eight_enemy_infantry_near_the_enemy_start_by_200_s_warn_without_a_rush(count, seconds, warned):
    agent = watch_agent()

    ask(agent, at(seconds, visible_enemies=massing(count), visible_enemy_buildings=[ENEMY_POWER_PLANT]))

    assert agent.ctx.rush_warning is warned and agent.ctx.rush_verdict == "none"


def test_the_warning_stays_once_the_massing_is_out_of_sight_and_ends_at_300_s_without_a_rush(match_record):
    agent = watch_agent()
    ask(agent, at(150, visible_enemies=massing(8), visible_enemy_buildings=[ENEMY_POWER_PLANT]))
    ask(agent, at(250))
    assert agent.ctx.rush_warning is True

    ask(agent, at(300))

    assert agent.ctx.rush_warning is False and agent.ctx.rush_verdict == "none"
    warnings = [event for event in match_record("rush_verdict") if "warning" in event]
    assert warnings == [
        {"verdict": "none", "warning": True, "signs": ["infantry_near_base"], "tick": 3750},
        {"verdict": "none", "warning": False, "signs": ["infantry_near_base"], "tick": 7500},
    ]


def test_the_warning_ends_on_the_rush_verdict():
    agent = watch_agent()
    ask(agent, at(150, visible_enemies=massing(8), visible_enemy_buildings=[ENEMY_POWER_PLANT]))
    out = [enemy(900 + number, "e1", 49, 46 + number % 2) for number in range(6)]

    ask(agent, at(220, visible_enemies=out))

    assert agent.ctx.rush_warning is False and agent.ctx.rush_verdict == "rush"


def test_the_warning_ends_once_the_rush_counter_has_ordered_its_defenses_and_is_not_given_again():
    agent = watch_agent()
    ask(agent, at(150, visible_enemies=massing(8), visible_enemy_buildings=[ENEMY_POWER_PLANT]))
    agent.ctx.rush_counter_ordered = True
    ask(agent, at(160, visible_enemies=massing(8)))
    assert agent.ctx.rush_warning is False

    agent.ctx.rush_counter_ordered = False
    ask(agent, at(170, visible_enemies=massing(8)))

    assert agent.ctx.rush_warning is False


def test_with_rush_detection_off_no_warning_is_given():
    agent = watch_agent(tuning_off())

    ask(agent, at(150, visible_enemies=massing(9), visible_enemy_buildings=[ENEMY_POWER_PLANT]))

    assert agent.ctx.rush_warning is False


def test_two_enemy_vehicles_out_of_the_enemy_base_give_rush():
    tanks = [enemy(800, "2tnk", 50, 30), enemy(801, "2tnk", 51, 30)]

    assert verdict_after(at(200, visible_enemies=tanks)) == "rush"
    assert verdict_after(at(200, visible_enemies=tanks[:1])) == "unknown"


def test_an_enemy_army_worth_300_25_cells_from_our_base_at_250_s_gives_rush():
    # One rocket soldier, 300, is worth rush_approach_value; two rifles, 200, are not.
    rocket = [enemy(800, "e3", 47, 46)]
    rifles = [enemy(800 + number, "e1", 47, 46 + number) for number in range(2)]

    assert verdict_after(at(250, visible_enemies=rocket)) == "rush"
    assert verdict_after(at(250, visible_enemies=rifles)) == "unknown"


def test_the_base_seen_at_90_s_with_no_sign_gives_none():
    assert verdict_after(at(90, visible_enemy_buildings=[ENEMY_POWER_PLANT])) == "none"


def test_the_base_unseen_at_150_s_gives_unknown_and_it_stays_unknown_when_the_base_is_seen_later():
    assert verdict_after(at(140), at(150)) == "unknown"
    assert verdict_after(at(140), at(150), at(160, visible_enemy_buildings=[ENEMY_POWER_PLANT])) == "unknown"
    assert verdict_after(at(140), at(149, visible_enemy_buildings=[ENEMY_POWER_PLANT])) == "none"


def test_a_rush_stays_rush_once_its_sign_is_out_of_sight_until_the_watch_ends():
    tanks = [enemy(800, "2tnk", 50, 30), enemy(801, "2tnk", 51, 30)]

    assert verdict_after(at(200, visible_enemies=tanks), at(260), at(299)) == "rush"


def test_once_the_watch_has_ended_the_verdict_no_longer_changes():
    late_tanks = [enemy(800, "2tnk", 50, 30), enemy(801, "2tnk", 51, 30)]

    verdict = verdict_after(at(90, visible_enemy_buildings=[ENEMY_POWER_PLANT]), at(300, visible_enemies=late_tanks))

    assert verdict == "none"


def test_with_rush_detection_off_no_verdict_is_kept():
    barracks = [enemy(701, "tent", 78, 52), enemy(702, "barr", 70, 44)]

    assert verdict_after(at(200, visible_enemy_buildings=barracks), tuning=tuning_off()) is None


def test_units_of_a_type_the_item_table_cannot_value_are_no_sign():
    strangers = [enemy(800 + number, "zombie", 74, 63) for number in range(6)]

    assert verdict_after(at(200, visible_enemies=strangers)) == "unknown"


def test_while_the_enemy_start_is_not_known_the_verdict_is_unknown():
    state = at(200, visible_enemy_buildings=[enemy(701, "tent", 78, 52), enemy(702, "barr", 70, 44)])
    del state["legal_actions"]["enemy_base"]

    assert verdict_after(state) == "unknown"


# -- the watcher on the way in -------------------------------------------------------------------------------------

GUARD_IDS = [200, 201, 202, 203, 204]
SCOUT_ID, WATCHER_ID = 205, 206
# 40 percent of the 52 cells from the base at (22, 48) to the enemy's start at (74, 48).
WATCH_POINT = [43, 48]


def guard() -> list[dict]:
    return [situations.unit(unit_id, "e1", 28, 46 + number) for number, unit_id in enumerate(GUARD_IDS)]


def rifle(unit_id: int, x: int = 20, y: int = 48) -> dict:
    return situations.unit(unit_id, "e1", x, y)


def orders_for(orders: list[dict], unit_id: int) -> list[dict]:
    return [order for order in orders if unit_id in order.get("units", ())]


def watched(tuning: Tuning = RUSH_WATCH):
    """An agent whose first rifle beyond the guard became the scout at 40 s, with a second one out at 41 s; the
    orders that second state got."""
    agent = watch_agent(tuning)
    ask(agent, at(39.8, guard()))
    ask(agent, at(40, [*guard(), rifle(SCOUT_ID)]))
    return agent, ask(agent, at(41, [*guard(), rifle(SCOUT_ID, 30, 48), rifle(WATCHER_ID)]))


def test_the_second_rifle_of_main_is_posted_as_the_watcher_at_40_percent_of_the_way_and_released_at_300_s():
    agent, orders = watched()
    groups = agent.ctx.groups

    posted = groups.group_of(WATCHER_ID)
    standing = ask(agent, at(299, [*guard(), rifle(SCOUT_ID, 30, 48), rifle(WATCHER_ID, *WATCH_POINT)]))
    released = ask(agent, at(300, [*guard(), rifle(SCOUT_ID, 30, 48), rifle(WATCHER_ID, *WATCH_POINT)]))

    assert orders_for(orders, WATCHER_ID) == [{"cmd": "move", "units": [WATCHER_ID], "to": WATCH_POINT}]
    assert posted == "watch" and groups.group_of(SCOUT_ID) == "scout"
    assert orders_for(standing, WATCHER_ID) == [{"cmd": "move", "units": [WATCHER_ID], "to": WATCH_POINT}]
    assert orders_for(released, WATCHER_ID) == [] and groups.group_of(WATCHER_ID) == "main"
    assert {entry.priority for entry in agent.ledger.entries if entry.order["units"] == [WATCHER_ID]} == {
        Priority.SCOUT
    }


def test_a_watcher_that_is_lost_is_not_replaced():
    agent, _ = watched()

    later = ask(agent, at(60, [*guard(), rifle(SCOUT_ID, 30, 48), rifle(207)]))

    assert orders_for(later, 207) == [] and agent.ctx.groups.group_of(207) == "main"


def test_the_guard_is_never_the_watcher():
    agent = watch_agent()

    orders = [ask(agent, at(seconds, guard())) for seconds in (40, 41, 100)]

    assert orders == [[], [], []] and agent.ctx.groups.members("guard") == GUARD_IDS
    assert agent.ctx.groups.members("watch") == []


def test_with_rush_detection_off_no_watcher_is_sent():
    agent, orders = watched(tuning_off())

    assert orders_for(orders, WATCHER_ID) == [] and agent.ctx.groups.group_of(WATCHER_ID) == "main"


def test_the_watcher_is_never_taken_as_a_scout():
    both = dataclasses.replace(tuning_off(), scouting=scouting_off(rush_detection=True, rescout=True))
    agent, _ = watched(both)
    # The scout is lost; with no enemy seen for a minute a new scout is due at 100 s, and the watcher is the only rifle.
    ask(agent, at(42, [*guard(), rifle(WATCHER_ID, 30, 48)]))

    later = ask(agent, at(110, [*guard(), rifle(WATCHER_ID, *WATCH_POINT)]))

    assert agent.ctx.groups.group_of(WATCHER_ID) == "watch" and agent.ctx.groups.members("scout") == []
    assert orders_for(later, WATCHER_ID) == [{"cmd": "move", "units": [WATCHER_ID], "to": WATCH_POINT}]


# -- the record of the verdicts ------------------------------------------------------------------------------------


def test_each_change_of_the_verdict_writes_one_rush_verdict_event_with_its_signs_and_tick(match_record):
    tanks = [enemy(800, "2tnk", 50, 30), enemy(801, "2tnk", 51, 30)]
    agent = watch_agent()
    ask(agent, at(80))
    ask(agent, at(90, visible_enemy_buildings=[ENEMY_POWER_PLANT]))
    ask(agent, at(100, visible_enemy_buildings=[ENEMY_POWER_PLANT]))
    ask(agent, at(200, visible_enemies=tanks))
    ask(agent, at(250))

    assert "rush_verdict" in records.EVENT_KINDS
    assert match_record("rush_verdict") == [
        {"verdict": "none", "signs": [], "tick": 2250},
        {"verdict": "rush", "signs": ["vehicles_out"], "tick": 5000},
    ]


def test_no_rush_verdict_event_is_written_with_rush_detection_off(match_record):
    agent = watch_agent(tuning_off())
    ask(agent, at(90, visible_enemy_buildings=[ENEMY_POWER_PLANT]))

    assert match_record("rush_verdict") == []
