"""The enemy army estimate: what was last seen of the enemy army, grown by its income while unseen, less our kills."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from situations import ask, base_state, new_agent, tracked_items
from ucla_agent.games.redalert.enemy_estimate import ENEMY_ESTIMATE_EVENT, EnemyArmyEstimate, enemy_income, seen_value
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.tuning import PlanRulesTuning, Tuning
from ucla_agent.games.redalert.world import World

TANKS = ("2tnk",) * 4
# Four medium tanks cost 850 each.
TANKS_VALUE = 3400
SHOWN_AT_SECONDS = 300
# Every cycle the tests step through, 10 s apart: the estimate does not depend on how often it is brought up to date.
STEP_SECONDS = 10


class Match:
    """One match's world and estimate, stepped through states of enemies in sight or not."""

    def __init__(self, tuning: Tuning | None = None, income_per_s: float | None = None) -> None:
        self.world = World(tracked_items())
        self.estimate = EnemyArmyEstimate()
        self.ctx = SimpleNamespace(tuning=tuning or Tuning(), income=SimpleNamespace(income_per_s=income_per_s))
        self.kills_cost = 0

    def at(
        self,
        seconds: float,
        enemies: tuple[str, ...] = (),
        first_id: int = 900,
        enemy_buildings: tuple[str, ...] = (),
        own_harvesters: int = 0,
    ) -> float | None:
        """Take in one state at `seconds` with these enemies in sight, numbered from `first_id`, and return E."""
        observation = {
            "tick": round(seconds * TICKS_PER_SECOND),
            "units": [{"id": 200 + n, "type": "harv", "x": 30, "y": 40 + n} for n in range(own_harvesters)],
            "visible_enemies": [{"id": first_id + n, "type": name, "x": 60, "y": 40} for n, name in enumerate(enemies)],
            "visible_enemy_buildings": [
                {"id": 800 + n, "type": name, "x": 70 + n, "y": 48} for n, name in enumerate(enemy_buildings)
            ],
            "military": {"kills_cost": self.kills_cost},
        }
        self.world.update({"phase": "playing", "observation": observation})
        return self.estimate.update(self.world, self.ctx)

    def unseen_until(self, start: float, end: float, **state) -> float | None:
        """Step from `start` to `end` with no enemy in sight, one state every STEP_SECONDS; E at `end`."""
        estimate = None
        for seconds in range(int(start), int(end) + 1, STEP_SECONDS):
            estimate = self.at(seconds, **state)
        return estimate


def test_estimate_grows_from_income_while_unseen():
    match = Match()
    match.at(SHOWN_AT_SECONDS, enemies=(*TANKS, "harv", "harv", "harv"))

    estimate = match.unseen_until(SHOWN_AT_SECONDS + STEP_SECONDS, SHOWN_AT_SECONDS + 120)

    # 3 harvesters x 8 credits a second x 0.5 on the army x 120 s.
    assert estimate == pytest.approx(TANKS_VALUE + 3 * 8 * 0.5 * 120, abs=1) == 4840


def test_estimate_starts_at_the_seen_value_at_the_first_sighting():
    match = Match()

    assert match.at(SHOWN_AT_SECONDS, enemies=TANKS) == TANKS_VALUE == seen_value(match.world)


def test_estimate_never_below_seen_value():
    match = Match()
    match.at(SHOWN_AT_SECONDS, enemies=(*TANKS, "harv", "harv"))
    # 2 harvesters x 8 x 0.5 = 8 credits a second for 200 s: 3,400 + 1,600.
    grown = match.unseen_until(SHOWN_AT_SECONDS + STEP_SECONDS, SHOWN_AT_SECONDS + 200)

    rising = match.at(SHOWN_AT_SECONDS + 200, enemies=("e3",) * 20, first_id=950)

    assert grown == pytest.approx(5000)
    assert seen_value(match.world) == 6000 and rising == 6000


def test_kills_lower_the_estimate():
    match = Match()
    match.at(SHOWN_AT_SECONDS, enemies=(*TANKS, "harv", "harv"))
    before = match.unseen_until(SHOWN_AT_SECONDS + STEP_SECONDS, SHOWN_AT_SECONDS + 250)
    match.kills_cost += 2000

    after = match.at(SHOWN_AT_SECONDS + 260)

    # Unseen for more than 240 s: no growth, so the kills alone move it.
    assert before - after == pytest.approx(2000)


def test_kills_never_take_the_estimate_below_zero():
    match = Match()
    match.at(SHOWN_AT_SECONDS, enemies=TANKS)
    match.kills_cost += 10_000

    assert match.at(SHOWN_AT_SECONDS + 250 * STEP_SECONDS) == 0


def test_growth_stops_after_cap():
    match = Match()
    match.at(SHOWN_AT_SECONDS, enemies=(*TANKS, "harv", "harv", "harv"))
    at_cap = match.unseen_until(SHOWN_AT_SECONDS + STEP_SECONDS, SHOWN_AT_SECONDS + 240)

    later = match.unseen_until(SHOWN_AT_SECONDS + 250, SHOWN_AT_SECONDS + 300)

    assert at_cap == pytest.approx(TANKS_VALUE + 3 * 8 * 0.5 * 240)
    assert later == at_cap


def test_growth_starts_again_once_the_army_is_seen_again():
    match = Match()
    match.at(SHOWN_AT_SECONDS, enemies=(*TANKS, "harv", "harv", "harv"))
    capped = match.unseen_until(SHOWN_AT_SECONDS + STEP_SECONDS, SHOWN_AT_SECONDS + 300)

    match.at(SHOWN_AT_SECONDS + 310, enemies=("e1",), first_id=950)
    grown = match.at(SHOWN_AT_SECONDS + 320)

    # Seen again at 610 s: the 10 s from there to 620 s grow it by 12 credits a second.
    assert grown == pytest.approx(capped + 12 * 10)


def test_enemy_income_falls_back_to_own_harvesters():
    match = Match()
    match.at(SHOWN_AT_SECONDS, own_harvesters=5)

    income, harvesters = enemy_income(match.world, 50.0, PlanRulesTuning())

    assert (income, harvesters) == (5 * 10, 5)


@pytest.mark.parametrize(
    "enemy_buildings, own_harvesters, own_income, expected",
    [
        (("proc",) * 4, 3, None, (4 * 8.0, 4)),
        (("proc",), 3, 60.0, (3 * 20.0, 3)),
        (("proc",), 0, 60.0, (3 * 8.0, 3)),
    ],
    ids=["more refineries than harvesters", "our income a harvester", "no own harvester at the built-in rate"],
)
def test_enemy_income_is_the_more_of_harvesters_and_refineries_at_our_rate(
    enemy_buildings, own_harvesters, own_income, expected
):
    match = Match()
    match.at(SHOWN_AT_SECONDS, enemies=("harv",) * 3, enemy_buildings=enemy_buildings, own_harvesters=own_harvesters)

    assert enemy_income(match.world, own_income, PlanRulesTuning()) == expected


def test_estimate_unknown_before_first_sighting():
    match = Match()

    seen_only_economy = match.at(SHOWN_AT_SECONDS, enemies=("harv", "mcv"), enemy_buildings=("proc", "fact"))
    seen_only_aircraft = match.at(SHOWN_AT_SECONDS + 10, enemies=("yak",), first_id=950)

    assert seen_only_economy is None and seen_only_aircraft is None


def test_seen_value_leaves_out_aircraft_harvesters_and_stale_units():
    match = Match()
    match.at(SHOWN_AT_SECONDS, enemies=(*TANKS, "harv", "yak", "mcv"))
    fresh = seen_value(match.world)

    match.at(SHOWN_AT_SECONDS + 120)

    assert fresh == TANKS_VALUE and seen_value(match.world) == 0


def test_switch_off_leaves_no_estimate(match_record):
    off = dataclasses.replace(Tuning(), plan_rules=dataclasses.replace(PlanRulesTuning(), enemy_army_estimate=False))
    match = Match(off)

    estimates = [match.at(SHOWN_AT_SECONDS, enemies=TANKS), match.at(SHOWN_AT_SECONDS + 60)]

    assert estimates == [None, None]
    assert match_record(ENEMY_ESTIMATE_EVENT) == []


def test_estimate_event_at_most_every_30_s_with_its_figures(match_record):
    match = Match()
    match.at(SHOWN_AT_SECONDS, enemies=(*TANKS, "harv", "harv", "harv"))
    match.unseen_until(SHOWN_AT_SECONDS + STEP_SECONDS, SHOWN_AT_SECONDS + 60)

    events = match_record(ENEMY_ESTIMATE_EVENT)

    assert [event["tick"] // TICKS_PER_SECOND for event in events] == [300, 330, 360]
    assert events[0] == {
        "tick": SHOWN_AT_SECONDS * TICKS_PER_SECOND,
        "estimate": TANKS_VALUE,
        "seen_value": TANKS_VALUE,
        "income_per_s": 24.0,
        "enemy_harvesters": 3,
        "army_unseen_seconds": 0.0,
    }
    assert events[2]["army_unseen_seconds"] == 60.0 and events[2]["estimate"] == TANKS_VALUE + 12 * 60


@pytest.mark.parametrize("on", [True, False], ids=["on", "off"])
def test_plan_rules_publish_the_estimate_on_the_match_context(on):
    tuning = dataclasses.replace(Tuning(), plan_rules=dataclasses.replace(PlanRulesTuning(), enemy_army_estimate=on))
    agent = new_agent(tuning=tuning)
    enemies = [{"id": 900 + n, "type": "2tnk", "x": 60, "y": 40} for n in range(4)]

    ask(agent, base_state("england", ("fact", 22, 48), ("powr", 19, 48), visible_enemies=enemies))

    assert agent.ctx.enemy_army_estimate == (TANKS_VALUE if on else None)
