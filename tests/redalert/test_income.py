"""The income estimate: spending from queue progress, realized income, queue utilization and the investment test."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import situations
from fake_game import FakeRedAlert
from situations import ask, base_state, new_agent
from ucla_agent.games.redalert.income import IncomeEstimate, can_invest
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.world import Economy, ProductionItem

# The fake game's pace: five states a second.
STEP_SECONDS = 0.2
E1_COST, TANK_COST, REFINERY_COST = 100, 700, 1400
HARVESTER_COST, MCV_COST = 1100, 2000
# A rifle soldier takes 2.4 s to train, twelve states.
E1_STATES = 12


def job(queue: str, item: str, progress: float, *, paused: bool = False, ready: bool = False) -> ProductionItem:
    return ProductionItem(queue, item, progress, 0.0, paused, ready)


def world_at(seconds: float, money: float, *jobs: ProductionItem) -> SimpleNamespace:
    """What the estimate reads of a world: the tick, the money, the queues and the item table."""
    production: dict[str, list[ProductionItem]] = {}
    for one in jobs:
        production.setdefault(one.queue, []).append(one)
    return SimpleNamespace(
        tick=round(seconds * TICKS_PER_SECOND),
        economy=Economy(cash=round(money)),
        production=production,
        items=situations.tracked_items(),
    )


def fed(worlds) -> IncomeEstimate:
    """A new estimate that has taken in the worlds, in order."""
    estimate = IncomeEstimate(Tuning())
    for world in worlds:
        estimate.update(world)
    return estimate


def seconds_until(end: float) -> list[float]:
    """The game seconds of the states from 0 up to `end`, five a second."""
    return [round(step * STEP_SECONDS, 1) for step in range(round(end / STEP_SECONDS) + 1)]


def rifle_at(seconds: float) -> ProductionItem:
    """A rifle soldier in training: the Infantry queue starts a new one every 2.4 s."""
    step = round(seconds / STEP_SECONDS)
    return job("Infantry", "e1", (step % E1_STATES) / E1_STATES)


# -- realized income --------------------------------------------------------------------------------


@pytest.mark.parametrize("end", [60.0, 90.0])
def test_money_rising_50_a_second_with_nothing_produced_is_an_income_of_50(end):
    estimate = fed(world_at(t, 1000 + 50 * t) for t in seconds_until(end))

    assert estimate.income_per_s == pytest.approx(50, abs=0.5)


def test_what_a_rifle_soldier_costs_while_it_is_trained_is_added_back_to_the_income():
    worlds = [world_at(t, 1000 + 50 * t - E1_COST * t / 2.4, rifle_at(t)) for t in seconds_until(90.0)]

    estimate = fed(worlds)

    assert estimate.income_per_s == pytest.approx(50, rel=0.05)
    assert estimate.spend_per_s() == pytest.approx(E1_COST / 2.4, rel=0.05)


def test_the_income_is_unknown_until_the_samples_span_20_seconds():
    worlds = [world_at(t, 1000 + 50 * t) for t in seconds_until(20.0)]
    early = fed(worlds[:-1])

    assert early.income_per_s is None
    assert fed(worlds).income_per_s == pytest.approx(50)


def test_the_income_is_never_negative_when_the_money_falls_with_nothing_spent():
    estimate = fed(world_at(t, 5000 - 30 * t) for t in seconds_until(60.0))

    assert estimate.income_per_s == 0.0


def test_the_income_is_renewed_every_250_ticks_and_not_in_between():
    rising = [world_at(t, 1000 + 50 * t) for t in seconds_until(30.0)]
    estimate = fed(rising)
    at_30_seconds = estimate.income_per_s

    # A jump in the money five seconds later shows only at the next renewal, 10 s after the last one.
    for t in seconds_until(5.0)[1:]:
        estimate.update(world_at(30 + t, 99_999))
    before_renewal = estimate.income_per_s
    estimate.update(world_at(40.0, 99_999))

    assert at_30_seconds == before_renewal == pytest.approx(50)
    assert estimate.income_per_s > 1000


def test_samples_older_than_60_seconds_are_dropped():
    # Money falls for 30 s, then rises 50 a second: only the rise is in the window at 100 s.
    worlds = [world_at(t, 5000 - 100 * t) for t in seconds_until(30.0)]
    worlds += [world_at(30 + t, 2000 + 50 * t) for t in seconds_until(70.0)[1:]]

    assert fed(worlds).income_per_s == pytest.approx(50)


# -- spending, item by item -------------------------------------------------------------------------


def refinery_states(*progresses: float) -> list[SimpleNamespace]:
    """One state a second: the Building queue empty, then holding a refinery at each progress given, then empty.

    The window's spending is what was spent after its first state, so the first state holds nothing.
    """
    worlds = [
        world_at(second, 5000, job("Building", "proc", progress)) for second, progress in enumerate(progresses, 1)
    ]
    return [world_at(0, 5000), *worlds, world_at(len(progresses) + 1, 5000)]


def building_spend(worlds) -> float:
    return fed(worlds).spend_by_queue()["Building"]


def test_an_item_cancelled_at_progress_0_4_leaves_its_net_spending_at_0():
    assert building_spend(refinery_states(0.0, 0.2, 0.4)) == pytest.approx(0.0)


def test_an_item_that_leaves_the_front_at_progress_0_95_was_finished_and_adds_its_full_cost():
    assert building_spend(refinery_states(0.0, 0.5, 0.95)) == pytest.approx(REFINERY_COST)


def test_the_same_name_at_a_lower_progress_is_a_new_item_after_a_finished_one():
    assert building_spend(refinery_states(0.0, 0.5, 0.95, 0.1)[:-1]) == pytest.approx(1.1 * REFINERY_COST)


def test_an_item_first_seen_partly_built_counts_what_it_had_got_to():
    assert building_spend(refinery_states(0.5, 0.95)) == pytest.approx(REFINERY_COST)


def test_a_finished_building_waiting_to_be_placed_is_not_the_front_followed():
    waiting = job("Building", "powr", 1.0, ready=True)
    worlds = [
        world_at(0, 5000, job("Building", "proc", 0.0)),
        world_at(1, 5000, job("Building", "proc", 0.95)),
        world_at(2, 5000, waiting),
        world_at(3, 5000, waiting),
    ]

    assert building_spend(worlds) == pytest.approx(REFINERY_COST)


def test_an_infantry_only_sequence_spends_on_the_infantry_queue_alone():
    worlds = [world_at(t, 5000, rifle_at(t)) for t in seconds_until(24.0)]

    spent = fed(worlds).spend_by_queue()

    # Ten rifle soldiers finished, the eleventh just begun.
    assert spent == pytest.approx(
        {"Building": 0.0, "Defense": 0.0, "Infantry": 10 * E1_COST, "Vehicle": 0.0, "economy": 0.0}
    )


def test_a_vehicle_only_sequence_spends_on_the_vehicle_queue_alone():
    # A light tank takes 16.8 s, 84 states; two are built in a row.
    states = 84
    worlds = [
        world_at(step * STEP_SECONDS, 5000, job("Vehicle", "1tnk", (step % states) / states))
        for step in range(2 * states)
    ]
    worlds.append(world_at(2 * states * STEP_SECONDS, 5000))

    spent = fed(worlds).spend_by_queue()

    assert spent == pytest.approx(
        {"Building": 0.0, "Defense": 0.0, "Infantry": 0.0, "Vehicle": 2 * TANK_COST, "economy": 0.0}
    )


def vehicle_states(*fronts: tuple[str, float]) -> list[SimpleNamespace]:
    """One state a second: the Vehicle queue empty, then holding each (item, progress) given, then empty."""
    worlds = [world_at(second, 5000, job("Vehicle", *front)) for second, front in enumerate(fronts, 1)]
    return [world_at(0, 5000), *worlds, world_at(len(fronts) + 1, 5000)]


def test_harvesters_and_mcvs_the_vehicle_queue_makes_are_spent_under_economy_and_not_under_vehicle():
    # A harvester (1,100), a light tank (700) and an MCV (2,000), each finished, in states from 0 s to 7 s.
    worlds = vehicle_states(("harv", 0.0), ("harv", 0.95), ("1tnk", 0.0), ("1tnk", 0.95), ("mcv", 0.5), ("mcv", 0.95))

    estimate = fed(worlds)

    spent = estimate.spend_by_queue()
    assert (spent["Vehicle"], spent["economy"]) == pytest.approx((TANK_COST, HARVESTER_COST + MCV_COST))
    assert estimate.spend_per_s() == pytest.approx((TANK_COST + HARVESTER_COST + MCV_COST) / 7)


def test_a_cancelled_harvester_gives_back_what_it_had_counted_under_economy():
    spent = fed(vehicle_states(("harv", 0.0), ("harv", 0.4))).spend_by_queue()

    assert (spent["Vehicle"], spent["economy"]) == pytest.approx((0.0, 0.0))


# -- the vehicle income -----------------------------------------------------------------------------

VEHICLE_BUILD_SECONDS = 40


def one_vehicle_built(item: str, cost: int) -> list[SimpleNamespace]:
    """60 seconds of states, five a second, of money rising 50 a second while the Vehicle queue builds one item over
    the first 40 seconds and pays for it as it goes."""
    worlds = []
    for t in seconds_until(60.0):
        progress = min(t / VEHICLE_BUILD_SECONDS, 1.0)
        jobs = (job("Vehicle", item, progress),) if t < VEHICLE_BUILD_SECONDS else ()
        worlds.append(world_at(t, 1000 + 50 * t - cost * progress, *jobs))
    return worlds


@pytest.mark.parametrize(
    "item, cost, vehicle_income",
    [("mcv", MCV_COST, 50 - MCV_COST / 60), ("harv", HARVESTER_COST, 50), ("1tnk", TANK_COST, 50)],
    ids=["an MCV", "a harvester", "a light tank"],
)
def test_vehicle_income_what_an_mcv_cost_is_taken_from_the_income_and_what_a_harvester_or_a_tank_cost_is_not(
    item, cost, vehicle_income
):
    estimate = fed(one_vehicle_built(item, cost))

    assert estimate.income_per_s == pytest.approx(50)
    assert estimate.vehicle_income_per_s == pytest.approx(vehicle_income)


def test_vehicle_income_what_the_infantry_queue_spent_a_second_is_taken_from_the_income():
    worlds = [world_at(t, 1000 + 50 * t - E1_COST * t / 2.4, rifle_at(t)) for t in seconds_until(60.0)]

    estimate = fed(worlds)

    assert estimate.vehicle_income_per_s == pytest.approx(estimate.income_per_s - estimate.spend_per_s())
    assert estimate.vehicle_income_per_s == pytest.approx(50 - E1_COST / 2.4, abs=1.0)


def test_vehicle_income_is_never_negative_and_unknown_while_the_income_is():
    worlds = [world_at(t, 1000 + 20 * t - E1_COST * t / 2.4, rifle_at(t)) for t in seconds_until(60.0)]

    assert fed(worlds[:50]).vehicle_income_per_s is None
    assert fed(worlds).vehicle_income_per_s == 0.0


def test_spending_is_kept_for_the_window_only():
    worlds = [world_at(t, 5000, rifle_at(t)) for t in seconds_until(24.0)]
    worlds += [world_at(24 + t, 5000) for t in seconds_until(61.0)[1:]]

    assert fed(worlds).spend_by_queue()["Infantry"] == pytest.approx(0.0)


# -- utilization ------------------------------------------------------------------------------------


def one_a_second(busy_seconds: int, idle: ProductionItem | None = None, total: int = 60) -> list[SimpleNamespace]:
    """A state a second for `total` seconds: the Infantry queue busy in the first ones, then empty or holding `idle`."""
    busy = job("Infantry", "e1", 0.5)
    rest = () if idle is None else (idle,)
    return [world_at(second, 5000, *((busy,) if second < busy_seconds else rest)) for second in range(total)]


def test_a_queue_busy_in_51_of_60_states_has_a_utilization_of_0_85():
    assert fed(one_a_second(51)).utilization("Infantry") == pytest.approx(0.85)


def test_a_paused_item_counts_as_idle():
    assert fed(one_a_second(51, job("Infantry", "e1", 0.5, paused=True))).utilization("Infantry") == pytest.approx(0.85)


def test_a_finished_building_waiting_to_be_placed_counts_as_busy():
    worlds = [world_at(second, 5000, job("Building", "powr", 1.0, ready=True)) for second in range(60)]

    estimate = fed(worlds)

    assert (estimate.utilization("Building"), estimate.utilization("Infantry")) == (1.0, 0.0)


def test_utilization_is_taken_over_the_last_60_seconds_only():
    # Busy for the first 60 s, idle for the next 61: every busy state has left the window.
    worlds = one_a_second(60, total=121)

    assert fed(worlds).utilization("Infantry") == 0.0


# -- the investment test ----------------------------------------------------------------------------


@pytest.mark.parametrize("money, invests", [(1300, True), (1200, False)])
def test_a_war_factory_can_be_invested_in_at_1300_and_an_income_of_42_but_not_at_1200(money, invests):
    assert can_invest(2000, 48, money, 42.0, 300, 0.5) is invests


def test_no_investment_while_the_income_is_unknown():
    assert can_invest(500, 15, 100_000, None, 300, 0.5) is False


@pytest.mark.parametrize("money_now, invests", [(1300, True), (1200, False)])
def test_the_estimate_s_own_test_reads_its_income_and_the_money_of_the_latest_state(money_now, invests):
    # Money rising 42 a second for 20 s up to `money_now`: the income is 42.
    estimate = fed(world_at(t, money_now - 42 * (20 - t)) for t in seconds_until(20.0))

    assert estimate.income_per_s == pytest.approx(42)
    assert estimate.can_invest(2000, 48) is invests


def test_the_estimate_s_own_test_fails_while_its_income_is_unknown():
    assert fed(world_at(t, 99_999) for t in seconds_until(10.0)).can_invest(500, 15) is False


# -- money held above an amount ---------------------------------------------------------------------


def test_money_held_above_1500_for_the_last_30_seconds_is_seen():
    estimate = fed(world_at(t, 1600) for t in seconds_until(40.0))

    assert estimate.has_held_above(1500, 30) is True
    assert estimate.has_held_above(1600, 30) is False


def test_one_state_at_or_below_the_amount_in_the_last_30_seconds_breaks_it():
    worlds = [world_at(t, 1500 if t == 25.0 else 1600) for t in seconds_until(40.0)]

    assert fed(worlds).has_held_above(1500, 30) is False


def test_a_dip_before_the_last_30_seconds_does_not_count():
    worlds = [world_at(t, 1000 if t < 10 else 1600) for t in seconds_until(40.0)]

    assert fed(worlds).has_held_above(1500, 30) is True


def test_samples_that_do_not_reach_30_seconds_back_are_not_enough():
    assert fed(world_at(t, 9000) for t in seconds_until(29.8)).has_held_above(1500, 30) is False


# -- the thresholds and the agent's cycle -----------------------------------------------------------


def test_the_income_thresholds_are_read_from_the_tracked_file_with_the_values_the_requirement_gives():
    income = Tuning.from_file(situations.REPO_ROOT).income

    assert (income.income_window_seconds, income.income_min_window_seconds, income.income_update_ticks) == (60, 20, 250)
    assert (income.invest_income_share, income.finished_progress) == (0.5, 0.9)
    assert income == Tuning().income


def test_the_match_context_brings_the_estimate_up_to_date_in_every_cycle():
    agent = new_agent()
    busy = [situations.job("Infantry", "e1")]

    for tick in (1000, 1005, 1010):
        ask(agent, base_state("england", ("fact", 22, 48), tick=tick, production=busy if tick > 1000 else []))

    assert agent.ctx.income.utilization("Infantry") == pytest.approx(2 / 3)


# -- what a match record says of it -----------------------------------------------------------------

# 200 seconds of a fake match: the opening, the war factory and the first income from the harvesters.
LOGGED_MATCH_STATES = 200 * 5
TIMING_PRODUCTION_KEYS = {"income_per_s", "spend_per_s", "utilization", "power_headroom", "barracks", "war_factories"}


def test_a_fake_match_logs_production_in_every_timing_event_and_the_report_prints_the_mean_income(
    match_record, run_folder, load_script
):
    game = FakeRedAlert("england", "russia")
    game.run(situations.own_agent(), LOGGED_MATCH_STATES)
    timings = match_record("timing")
    (folder,) = run_folder.glob(f"runs/*/{situations.SESSION_ID}__seat")

    assert load_script("report.py").main([str(folder)]) == 0

    (report_path,) = run_folder.glob("runs/reports/*/report.md")
    computed = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))
    incomes = [timing["income_per_s"] for timing in timings if timing["income_per_s"] is not None]
    last = timings[-1]
    assert len(timings) == 19 and all(TIMING_PRODUCTION_KEYS <= set(timing) for timing in timings)
    assert [timing["income_per_s"] for timing in timings[:1]] == [None] and incomes and min(incomes) >= 0
    assert set(last["utilization"]) == {"Building", "Defense", "Infantry", "Vehicle"}
    assert (last["barracks"], last["war_factories"]) == (1, 1) and last["power_headroom"] >= 0
    assert computed["income_per_s"]["mean"] == round(sum(incomes) / len(incomes), 4) > 0
    production = report_path.read_text(encoding="utf-8").split("## Production\n\n")[1].split("\n\n")[0]
    (row,) = production.splitlines()[2:]
    assert row.startswith(f"| {situations.SESSION_ID} | {computed['income_per_s']['mean']} | ")
