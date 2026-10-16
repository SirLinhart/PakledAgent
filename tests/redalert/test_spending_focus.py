"""The spending focus: military, economy or balanced spending, from the enemy army estimate against our army."""

from __future__ import annotations

import dataclasses

import pytest

import situations
from situations import OpeningThen, ask, base_state, with_command
from switches_off import new_agent, tuning_off, upkeep_off
from ucla_agent.games.redalert import expansion
from ucla_agent.games.redalert.composition import HOLD, Composition
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import BALANCED_FOCUS, ECONOMY_FOCUS, MILITARY_FOCUS
from ucla_agent.games.redalert.spending_focus import (
    SPENDING_FOCUS_EVENT,
    artillery_first,
    called_for,
    is_greedy,
)
from ucla_agent.games.redalert.upkeep import UpkeepManager

# The base of the upkeep tests past its opening: two refineries, a barracks, a war factory and a pillbox.
YARD = ("fact", 22, 48)
BASE = (
    YARD,
    ("powr", 19, 48),
    ("tent", 22, 44),
    ("proc", 24, 39),
    ("proc", 18, 39),
    ("powr", 26, 50),
    ("weap", 22, 53),
    ("pbox", 27, 46),
)
DEPOT_AND_RADAR = (("fix", 26, 54), ("powr", 30, 50), ("dome", 30, 46), ("powr", 30, 54))
# The two-refinery base with the spending focus on, the rest of the shipped switches off.
FOCUS = tuning_off(upkeep=upkeep_off(refineries_max=2, refineries_max_late=2, spending_focus=True))
FOCUS_OFF = tuning_off(upkeep=upkeep_off(refineries_max=2, refineries_max_late=2))
FOCUS_TICKS = FOCUS.upkeep.focus_every_ticks


class Estimated(OpeningThen):
    """OPENING until a war factory has stood, HOLD after, with the enemy army estimate a test sets published."""

    def __init__(self, estimate: float | None) -> None:
        super().__init__(HOLD)
        self.estimate = estimate

    def select(self, world, ctx, choice):
        ctx.enemy_army_estimate = self.estimate
        return super().select(world, ctx, choice)


def focus_agent(estimate: float | None, tuning=FOCUS):
    """An agent with the upkeep manager alone and the estimate E given, and its plan selector."""
    selector = Estimated(estimate)
    return new_agent(UpkeepManager(), plan_selector=selector, tuning=tuning), selector


def units(tanks: int = 0, rockets: int = 0, harvesters: int = 4, artillery: int = 0) -> list[dict]:
    """Four harvesters at work and the army given: light tanks (700), rocket soldiers (300) and artillery (850)."""
    kinds = [("harv", harvesters), ("1tnk", tanks), ("e3", rockets), ("arty", artillery)]
    types = [unit_type for unit_type, count in kinds for _ in range(count)]
    return [situations.unit(300 + n, unit_type, 30, 40, idle=unit_type != "harv") for n, unit_type in enumerate(types)]


def base(*more, tick: int = 1000, cash: int = 5000, army: list[dict] | None = None, **observation) -> dict:
    return base_state("england", *BASE, *more, tick=tick, cash=cash, units=army or units(), **observation)


def trains(orders: list[dict]) -> list[tuple[str, int]]:
    return [(order["item"], order["count"]) for order in with_command(orders, "train")]


def builds(orders: list[dict]) -> list[str]:
    return [order["item"] for order in with_command(orders, "build")]


# -- deciding the focus ------------------------------------------------------------------------------------------------


def test_military_focus_when_estimate_1_2x_ours(match_record):
    ahead, _ = focus_agent(6000)
    below, _ = focus_agent(5800)

    ask(ahead, base(army=units(tanks=7)))
    ask(below, base(army=units(tanks=7)))

    # 7 light tanks: 4,900; 1.2 x 4,900 = 5,880.
    assert (ahead.ctx.spending_focus, below.ctx.spending_focus) == (MILITARY_FOCUS, BALANCED_FOCUS)
    assert match_record(SPENDING_FOCUS_EVENT) == [
        {"tick": 1000, "focus": "military", "reason": "enemy_ahead", "estimate": 6000.0, "army": 4900}
    ]


def test_military_focus_ends_when_army_matches(match_record):
    agent, _ = focus_agent(6000)
    ask(agent, base(army=units(tanks=7)))

    ask(agent, base(tick=1000 + FOCUS_TICKS, army=units(tanks=6, rockets=6)))

    assert agent.ctx.spending_focus == BALANCED_FOCUS
    assert [(event["focus"], event["reason"], event["army"]) for event in match_record(SPENDING_FOCUS_EVENT)] == [
        ("military", "enemy_ahead", 4900),
        ("balanced", "matched", 6000),
    ]


def test_military_focus_needs_1000_on_either_side():
    agent, _ = focus_agent(900)

    ask(agent, base(army=units(rockets=2)))

    assert agent.ctx.spending_focus == BALANCED_FOCUS


def test_economy_focus_when_army_1_5x():
    agent, _ = focus_agent(6000)

    ask(agent, base(army=units(tanks=12, rockets=2)))

    # 12 light tanks and 2 rockets: 9,000, 1.5 x 6,000.
    assert agent.ctx.spending_focus == ECONOMY_FOCUS
    assert expansion.yards_allowed(agent.ctx) == 2


@pytest.mark.parametrize("income, allowed", [(None, 1), (70, 2), (400, 6)], ids=["unknown", "two", "at max_yards"])
def test_economy_focus_yards_allowed_one_above_the_income_cap(income, allowed):
    agent, _ = focus_agent(6000)
    ask(agent, base(army=units(tanks=12, rockets=2)))
    agent.ctx.income.income_per_s = income
    economy = expansion.yards_allowed(agent.ctx)
    agent.ctx.spending_focus = BALANCED_FOCUS

    assert expansion.yards_allowed(agent.ctx) == allowed
    assert economy == min(allowed + 1, FOCUS.expansion.max_yards)


def test_economy_focus_for_greedy_enemy(match_record):
    agent, _ = focus_agent(3000)
    refineries = [{"id": 800 + n, "type": "proc", "x": 70 + 4 * n, "y": 20, "hp": 100} for n in range(3)]

    ask(agent, base(army=units(rockets=10), visible_enemy_buildings=refineries))

    # 3 refineries at 1,400 are 4,200, at least E's 3,000; our 10 rockets are 3,000.
    assert agent.ctx.spending_focus == ECONOMY_FOCUS
    assert match_record(SPENDING_FOCUS_EVENT)[0]["reason"] == "greedy_enemy"


def test_greedy_needs_two_refineries_worth_the_estimate():
    agent, _ = focus_agent(None)
    refineries = [{"id": 800 + n, "type": "proc", "x": 70 + 4 * n, "y": 20, "hp": 100} for n in range(3)]
    ask(agent, base(visible_enemy_buildings=refineries[:1]))
    one = is_greedy(agent.world, None)
    ask(agent, base(tick=1001, visible_enemy_buildings=refineries))

    assert not one
    assert [is_greedy(agent.world, estimate) for estimate in (None, 4200, 4201)] == [True, True, False]


def test_focus_balanced_before_opening_complete_and_while_unknown():
    opening, _ = focus_agent(9000)
    unknown, _ = focus_agent(None)
    no_war_factory = tuple(building for building in BASE if building[0] != "weap")

    ask(opening, base_state("england", *no_war_factory, units=units(tanks=2)))
    ask(unknown, base(army=units(tanks=7)))

    assert opening.ctx.spending_focus == unknown.ctx.spending_focus == BALANCED_FOCUS


def test_focus_hold_30s_except_to_military():
    agent, selector = focus_agent(6000)
    army = units(tanks=12, rockets=2)
    ask(agent, base(army=army))
    # 9,000 is under 1.3 x 7,000: the economy focus is no longer called for, but holds for 30 s.
    selector.estimate = 7000
    ask(agent, base(tick=1000 + FOCUS_TICKS, army=army))
    held = agent.ctx.spending_focus
    ask(agent, base(tick=1000 + 3 * FOCUS_TICKS, army=army))
    after = agent.ctx.spending_focus
    selector.estimate = 12_000
    ask(agent, base(tick=1000 + 4 * FOCUS_TICKS, army=army))

    assert (held, after, agent.ctx.spending_focus) == (ECONOMY_FOCUS, BALANCED_FOCUS, MILITARY_FOCUS)


def test_economy_to_military_at_once():
    agent, selector = focus_agent(6000)
    army = units(tanks=12, rockets=2)
    ask(agent, base(army=army))
    selector.estimate = 12_000

    ask(agent, base(tick=1000 + FOCUS_TICKS, army=army))

    assert agent.ctx.spending_focus == MILITARY_FOCUS


def test_the_focus_is_decided_every_250_ticks():
    agent, selector = focus_agent(6000)
    ask(agent, base(army=units(tanks=7)))
    selector.estimate = 100

    ask(agent, base(tick=1000 + FOCUS_TICKS - 1, army=units(tanks=7)))
    early = agent.ctx.spending_focus
    ask(agent, base(tick=1000 + FOCUS_TICKS, army=units(tanks=7)))

    assert (early, agent.ctx.spending_focus) == (MILITARY_FOCUS, ECONOMY_FOCUS)


@pytest.mark.parametrize(
    "focus, estimate, army, greedy, expected",
    [
        (BALANCED_FOCUS, 6000, 4900, False, (MILITARY_FOCUS, "enemy_ahead")),
        (BALANCED_FOCUS, 5800, 4900, False, (BALANCED_FOCUS, "balanced")),
        (MILITARY_FOCUS, 5000, 4900, False, (MILITARY_FOCUS, "enemy_ahead")),
        (MILITARY_FOCUS, 4900, 4900, False, (BALANCED_FOCUS, "matched")),
        (MILITARY_FOCUS, 1000, 4900, False, (ECONOMY_FOCUS, "army_ahead")),
        (ECONOMY_FOCUS, 7000, 9100, False, (ECONOMY_FOCUS, "army_ahead")),
        (ECONOMY_FOCUS, 7000, 9099, False, (BALANCED_FOCUS, "balanced")),
        (BALANCED_FOCUS, None, 9000, True, (ECONOMY_FOCUS, "greedy_enemy")),
        (BALANCED_FOCUS, None, 9000, False, (BALANCED_FOCUS, "balanced")),
    ],
)
def test_called_for_by_the_thresholds(focus, estimate, army, greedy, expected):
    ctx = dataclasses.make_dataclass("Ctx", ["tuning"])(FOCUS)

    assert called_for(focus, estimate, army, greedy, ctx) == expected


def test_switch_off_spends_as_before():
    off, _ = focus_agent(20_000, tuning=FOCUS_OFF)
    unknown, _ = focus_agent(None)
    state = base(*DEPOT_AND_RADAR, army=units(tanks=3, harvesters=3))

    orders_off, orders_unknown = ask(off, state), ask(unknown, state)

    assert off.ctx.spending_focus == BALANCED_FOCUS
    assert trains(orders_off) == trains(orders_unknown) and builds(orders_off) == builds(orders_unknown)


# -- the military focus's spending -----------------------------------------------------------------------------------

# A base that calls for its third refinery: 4 harvesters at work on 2 refineries, 1,500 and more.
THIRD_REFINERY = tuning_off(upkeep=upkeep_off(spending_focus=True))


def test_military_focus_blocks_new_refinery_but_places_one_waiting():
    balanced, _ = focus_agent(None, tuning=THIRD_REFINERY)
    military, _ = focus_agent(20_000, tuning=THIRD_REFINERY)
    waiting = [situations.job("Building", "proc", ready=True)]

    # No service depot on offer, so that the Building queue's next item is the third refinery.
    called = builds(ask(balanced, base(army=units(tanks=2), withheld=("fix",))))
    held = builds(ask(military, base(army=units(tanks=2), withheld=("fix",))))
    placed = with_command(ask(military, base(tick=1001, army=units(tanks=2), production=waiting)), "place")

    assert "proc" in called and "proc" not in held
    assert military.ctx.spending_focus == MILITARY_FOCUS
    assert [order["item"] for order in placed] == ["proc"]


def test_military_focus_still_trains_a_harvester_for_a_standing_refinery():
    agent, _ = focus_agent(20_000)

    orders = ask(agent, base(army=units(tanks=2, harvesters=3)))

    assert agent.ctx.spending_focus == MILITARY_FOCUS and ("harv", 1) in trains(orders)


def test_military_focus_artillery_first():
    agent, _ = focus_agent(20_000)
    balanced, _ = focus_agent(None)
    one_arty = units(tanks=0, artillery=1)

    none_yet = trains(ask(agent, base(*DEPOT_AND_RADAR, army=units(tanks=1))))
    second = trains(ask(agent, base(*DEPOT_AND_RADAR, tick=1100, army=one_arty)))
    mix = trains(ask(balanced, base(*DEPOT_AND_RADAR, army=one_arty)))
    after = trains(ask(agent, base(*DEPOT_AND_RADAR, tick=1200, army=units(artillery=2))))

    # The mix would train its medium tank; the military focus trains artillery twice, then the mix again.
    assert ("2tnk", 1) in mix and ("arty", 1) in none_yet and ("arty", 1) in second
    assert ("arty", 1) not in after


def test_artillery_first_counts_those_on_their_way_and_leaves_an_unfed_queue_alone():
    agent, _ = focus_agent(20_000)
    ask(agent, base(*DEPOT_AND_RADAR, production=[situations.job("Vehicle", "arty")] * 2))
    plan = Composition(infantry={"e1": 0.4}, vehicle={"2tnk": 0.6})

    assert artillery_first(plan, agent.world, agent.ctx) == plan
    assert artillery_first(Composition(infantry={"e1": 1.0}), agent.world, agent.ctx).vehicle == {}


# Six enemy medium tanks seen far out in the field: the enemy army is all vehicles, and no threat to the base.
FAR_TANKS = [{"id": 900 + n, "type": "2tnk", "x": 80, "y": 70 + n, "hp": 100} for n in range(6)]
FAR_RIFLES = [{"id": 900 + n, "type": "e1", "x": 80, "y": 70 + n, "hp": 100} for n in range(6)]
ON_THREAT_ONLY = tuning_off(
    upkeep=upkeep_off(refineries_max=2, refineries_max_late=2, spending_focus=True, defenses_on_threat_only=True)
)


def test_military_focus_defense_toward_enemy():
    agent, _ = focus_agent(20_000, tuning=ON_THREAT_ONLY)
    ticks = [1000, 1000 + 30 * TICKS_PER_SECOND, 1000 + 60 * TICKS_PER_SECOND, 1000 + 150 * TICKS_PER_SECOND]

    defenses = [
        [
            item
            for item in builds(ask(agent, base(tick=tick, army=units(tanks=2), visible_enemies=FAR_TANKS)))
            if item in ("gun", "pbox")
        ]
        for tick in ticks
    ]
    goal = agent.ctx.defense_goals.get("gun")

    assert defenses == [["gun"], [], ["gun"], []]
    # 15 cells from the base's centre (22, 48) toward the enemy's start (74, 48).
    assert goal == (37, 48)
    assert agent.ctx.threat_level == "none"


def test_military_focus_defense_against_infantry_when_the_enemy_is_not_vehicles():
    agent, _ = focus_agent(20_000, tuning=ON_THREAT_ONLY)

    orders = ask(agent, base(army=units(tanks=2), visible_enemies=FAR_RIFLES))

    assert "pbox" in builds(orders) and "gun" not in builds(orders)


def test_military_focus_defenses_start_again_in_a_new_military_period():
    agent, selector = focus_agent(20_000, tuning=ON_THREAT_ONLY)
    for tick in (1000, 1000 + 60 * TICKS_PER_SECOND):
        ask(agent, base(tick=tick, army=units(tanks=2), visible_enemies=FAR_TANKS))
    selector.estimate = 100
    ask(agent, base(tick=1000 + 70 * TICKS_PER_SECOND, army=units(tanks=2), visible_enemies=FAR_TANKS))
    selector.estimate = 20_000

    orders = ask(agent, base(tick=1000 + 140 * TICKS_PER_SECOND, army=units(tanks=2), visible_enemies=FAR_TANKS))

    assert "gun" in builds(orders)


# -- the economy focus's spending ------------------------------------------------------------------------------------

# Harvesters first only below one a refinery, so that combat vehicles go first while main is small.
TURNS = tuning_off(
    upkeep=upkeep_off(
        refineries_max=2,
        refineries_max_late=2,
        spending_focus=True,
        harvesters_first=True,
        harvesters_first_per_refinery=1,
    )
)


def test_economy_focus_harvester_before_the_army_vehicle():
    economy, _ = focus_agent(100, tuning=TURNS)
    balanced, _ = focus_agent(None, tuning=TURNS)
    state = base(army=units(tanks=2, harvesters=3))

    economy_vehicles = [item for item, _ in trains(ask(economy, state)) if item in ("harv", "1tnk")]
    balanced_vehicles = [item for item, _ in trains(ask(balanced, state)) if item in ("harv", "1tnk")]

    assert economy.ctx.spending_focus == ECONOMY_FOCUS
    assert economy_vehicles == ["harv"] and balanced_vehicles == ["1tnk"]
