"""The counterattack: main striking out at once after a won defense of the base or a regroup at home."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from situations import REPO_ROOT, ask, base_state, new_agent
from ucla_agent.games.redalert import counterattack
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.strike import Strike
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.tuning import CombatTuning, Tuning

# The Allied base of the fixture map, its staging point, and the enemy's start at (74, 48).
YARD = ("fact", 22, 48)
STAGING = [32, 48]
TANKS = [300, 301, 302, 303, 304, 305]
# An enemy power plant in the enemy's base that no defense reaches.
POWER_PLANT = {"id": 600, "type": "powr", "x": 70, "y": 40, "hp": 100}
COUNTER_OFF = Tuning(combat=CombatTuning(counterattack=False))


def own(unit_id: int, unit_type: str, x: int, y: int) -> dict:
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": 100, "act": None, "idle": True}


def foe(enemy_id: int, enemy_type: str, x: int, y: int) -> dict:
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": 100}


def at_staging() -> list[dict]:
    return [own(300 + number, "2tnk", 32 + number % 2, 48 + number // 2) for number in range(6)]


class Always:
    def __init__(self, plan: str) -> None:
        self.plan = plan

    def select(self, world, ctx, choice) -> str:
        return self.plan


def counter_agent(tuning: Tuning | None = None, plan: str = "HOLD"):
    agent = new_agent(CombatManager(), tuning=tuning, plan_selector=Always(plan))
    ask(agent, state(900, kills=0, deaths=0))
    agent.ctx.terrain = Terrain(())
    return agent


def state(tick: int, units=(), enemies=(), buildings=(POWER_PLANT,), *, kills: int, deaths: int) -> dict:
    shown = {"units": list(units), "visible_enemies": list(enemies), "visible_enemy_buildings": list(buildings)}
    military = {"kills_cost": kills, "deaths_cost": deaths, "assets_value": 0, "army_value": 0}
    return base_state("england", YARD, tick=tick, military=military, **shown)


def _strike(agent) -> Strike:
    (manager,) = [manager for manager in agent.managers if isinstance(manager, CombatManager)]
    return manager._strike


def defended(agent, kills: int, deaths: int, buildings=(POWER_PLANT,), enemies=()) -> list[dict]:
    """A defense of the base from tick 1000 to 1005, these kills and losses in it; the orders of its end cycle."""
    agent.ctx.threat_active = True
    ask(agent, state(1000, at_staging(), enemies, buildings, kills=0, deaths=0))
    agent.ctx.threat_active = False
    return ask(agent, state(1005, at_staging(), enemies, buildings, kills=kills, deaths=deaths))


def counter_events(match_record) -> list[dict]:
    logged = [data for data in match_record("combat") if data["reason"] == "counterattack"]
    return [{key: value for key, value in data.items() if key not in ("tick", "reason", "ratio")} for data in logged]


def test_counterattack_a_won_defense_sends_every_unit_of_main_at_the_open_enemy_building(match_record):
    agent = counter_agent()

    orders = defended(agent, kills=2000, deaths=500)

    events = counter_events(match_record)
    assert events[0]["phase"] == "start" and events[0]["target"] == 600 and events[0]["units"] == TANKS
    assert events[1] == {"phase": "due", "why": "won_defense"}
    assert {"cmd": "attack_move", "units": TANKS, "to": [70, 40]} in orders


@pytest.mark.parametrize(
    "kills, deaths, tuning",
    [(800, 0, None), (2000, 2500, None), (2000, 500, COUNTER_OFF)],
    ids=["too little killed", "more lost than killed", "with the switch off"],
)
def test_counterattack_no_counterattack_after_a_defense_not_won_or_with_the_switch_off(kills, deaths, tuning):
    agent = counter_agent(tuning)

    defended(agent, kills, deaths)

    assert _strike(agent).target_id is None


def test_counterattack_main_under_1_2x_the_enemy_army_seen_lately_stays_home():
    agent = counter_agent()
    # Eight heavy tanks seen 20 cells off the enemy's base: 9,600 held at 1.5x, against main's 4,800.
    heavies = [foe(700 + number, "3tnk", 60, 20 + number) for number in range(8)]

    defended(agent, kills=2000, deaths=500, enemies=heavies)

    assert _strike(agent).target_id is None


def test_counterattack_a_building_a_defense_reaches_is_no_target_and_an_exposed_yard_comes_first():
    agent = counter_agent()
    covered = [POWER_PLANT, foe(650, "pbox", 71, 42)]
    ask(agent, state(1000, at_staging(), (), [*covered, foe(601, "fact", 60, 20)], kills=0, deaths=0))

    found = counterattack.targets(agent.world.base_center, agent.world, agent.ctx)

    assert [record.id for record in found] == [601]


def test_counterattack_under_preserve_main_stays_home():
    agent = counter_agent(plan="PRESERVE")

    defended(agent, kills=2000, deaths=500)

    assert _strike(agent).target_id is None


def test_counterattack_ends_after_90_s(match_record):
    agent = counter_agent()
    defended(agent, kills=2000, deaths=500)
    away = [own(300 + number, "2tnk", 50, 40 + number) for number in range(6)]

    ask(agent, state(1005 + 90 * 25, away, kills=2000, deaths=500))

    assert counter_events(match_record)[-1] == {"phase": "end", "target": 600, "outcome": "timeout"}


def test_counterattack_a_regroup_over_at_staging_is_due_once_a_regroup():
    agent = counter_agent()
    world, ctx = agent.world, agent.ctx
    watch = counterattack.CounterWatch()
    ctx.commander = SimpleNamespace(is_regrouping=True)

    watch.follow(False, world, ctx)
    on_the_way = watch.due
    watch.follow(True, world, ctx)
    gathered = watch.due
    watch.taken()
    watch.follow(True, world, ctx)

    assert (on_the_way, gathered, watch.due) == (None, "regrouped", None)


def test_counterattack_ships_on_in_the_built_ins_and_the_tracked_file():
    for shipped in (Tuning().combat, Tuning.from_file(REPO_ROOT).combat):
        keys = (
            shipped.counterattack_min_kills,
            shipped.counterattack_window_seconds,
            shipped.counterattack_ratio,
            shipped.counterattack_seen_share,
            shipped.counterattack_seen_seconds,
            shipped.counterattack_seconds,
        )
        assert (shipped.counterattack, keys) == (True, (1000, 10, 1.2, 1.5, 180, 90))
