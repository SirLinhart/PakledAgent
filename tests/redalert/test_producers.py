"""Producer scaling: one more barracks or war factory on income, busy queues and the investment test, and what the
money banked calls for."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

import fake_game
import situations
from situations import OpeningThen, ask, base_state, with_command
from switches_off import expansion_off, new_agent, producers_off, tuning_off, upkeep_off
from ucla_agent.core import records
from ucla_agent.games.redalert import income, placement, war_factory_load
from ucla_agent.games.redalert.bank_spending import BankWatch, StorageWatch
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.composition import HOLD, OPENING
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import ProducersTuning, Tuning
from ucla_agent.games.redalert.upkeep import (
    CoreBuildings,
    MoreRefineries,
    ProducerScaling,
    UpkeepManager,
    power_target,
)
from whole_match import COMPUTE_P95_MS, WHOLE_MATCH_STEPS, WholeMatch, play_whole_match

RESERVE, INCOME_SHARE, FLOAT_SECONDS = 300, 0.5, 30
BARRACKS = {"england": "tent", "russia": "barr"}
DEFENSE = {"england": "pbox", "russia": "ftur"}
# The opening's two refineries are all the base is to have, so the Building queue is left to the rule under test.
TWO_REFINERIES_ONLY = tuning_off(upkeep=dataclasses.replace(upkeep_off(), refineries_max=2, refineries_max_late=2))
QUEUES = ("Building", "Defense", "Infantry", "Vehicle")


class SetIncome:
    """An income estimate whose measures a test sets: the income, how busy the unit queues were, the least money of
    the last 30 seconds, and, when given, the outcome of the investment test and the vehicle income.

    Without an outcome given, the investment test is the income module's, at the money of the state. Without a
    vehicle income given, it is the income: the Infantry queue and MCVs spent nothing.
    """

    def __init__(
        self,
        income_per_s: float | None = None,
        infantry: float = 0.0,
        vehicle: float = 0.0,
        least_money: int = 0,
        invests: bool | None = None,
        vehicle_income_per_s: float | None = None,
    ) -> None:
        self.income_per_s = income_per_s
        self.vehicle_income_per_s = income_per_s if vehicle_income_per_s is None else vehicle_income_per_s
        self._busy = {"Infantry": infantry, "Vehicle": vehicle}
        self._least_money = least_money
        self._invests = invests
        self._money = 0
        # The (cost, build seconds) of every investment test the rule asked for.
        self.asked: list[tuple[int, float]] = []

    def update(self, world) -> None:
        self._money = world.economy.spendable

    def utilization(self, queue: str) -> float:
        return self._busy.get(queue, 0.0)

    def spend_by_queue(self) -> dict[str, float]:
        return dict.fromkeys(QUEUES, 0.0)

    def figures(self) -> dict:
        """What a `timing` event gives of the estimate, which an agent asked over 10 seconds of states logs."""
        return {"income_per_s": self.income_per_s, "spend_per_s": 0.0, "utilization": dict(self._busy)}

    def has_held_above(self, money: int, seconds: float) -> bool:
        return seconds == FLOAT_SECONDS and self._least_money > money

    def can_invest(self, cost: int, build_seconds: float) -> bool:
        self.asked.append((cost, build_seconds))
        if self._invests is not None:
            return self._invests
        return income.can_invest(cost, build_seconds, self._money, self.income_per_s, RESERVE, INCOME_SHARE)


def base(
    seconds: float = 240,
    barracks: int = 1,
    factories: int = 1,
    cash: int = 1000,
    faction: str = "england",
    provided: int = 1000,
    refineries: int = 2,
    **more,
) -> dict:
    """A base past its opening at a game time, with so many barracks and war factories, the money and the power given.

    It has the opening's power plants and defense, its two refineries unless fewer are given, four harvesters, and no
    service depot on offer.
    """
    buildings = [
        ("fact", 22, 48),
        ("powr", 19, 48),
        ("powr", 26, 50),
        *[("proc", 24 - 6 * number, 39) for number in range(refineries)],
        (DEFENSE[faction], 27, 46),
        *[(BARRACKS[faction], 30 + 4 * number, 40) for number in range(barracks)],
        *[("weap", 30 + 5 * number, 56) for number in range(factories)],
    ]
    more.setdefault("withheld", ("fix",))
    economy = situations.economy(cash, provided=provided, drained=100, harvesters=4)
    harvesters = [situations.unit(300 + number, "harv", 22, 41, idle=False) for number in range(4)]
    return base_state(faction, *buildings, tick=round(seconds * 25), economy=economy, units=harvesters, **more)


def producer_agent(measures: SetIncome, plan_selector=None, tuning: Tuning = TWO_REFINERIES_ONLY):
    """An agent with the upkeep manager alone and an income estimate that says what `measures` says."""
    agent = new_agent(UpkeepManager(), plan_selector=plan_selector or OpeningThen(HOLD), tuning=tuning)
    agent.ctx.income = measures
    return agent


def builds(orders: list[dict]) -> list[str]:
    """The items of a batch's build orders that go into the Building queue."""
    items = situations.tracked_items()
    return [order["item"] for order in with_command(orders, "build") if items[order["item"]].queue == "Building"]


def built(measures: SetIncome, state: dict, **agent_options) -> list[str]:
    return builds(ask(producer_agent(measures, **agent_options), state))


# -- infantry producers ----------------------------------------------------------------------------


def test_at_240_seconds_with_one_barracks_its_queue_busy_and_110_a_second_a_second_barracks_is_ordered():
    assert built(SetIncome(110, infantry=0.9), base(240, cash=1000)) == ["tent"]


@pytest.mark.parametrize(
    "seconds, busy, income_per_s",
    [(200, 0.9, 110), (240, 0.8, 110), (240, 0.9, 90)],
    ids=["at 200 s", "the queue busy 80%", "90 a second"],
)
def test_no_second_barracks_before_210_seconds_below_85_percent_busy_or_below_100_a_second(seconds, busy, income_per_s):
    assert built(SetIncome(income_per_s, infantry=busy), base(seconds, cash=1000)) == []


def test_at_90_a_second_money_held_above_1500_for_30_seconds_orders_the_second_barracks():
    measures = SetIncome(90, infantry=0.9, least_money=1600)

    assert built(measures, base(240, cash=1600)) == ["tent"]


def test_money_held_above_1500_for_30_seconds_does_not_do_while_the_income_is_unknown():
    assert built(SetIncome(None, infantry=0.9, least_money=9000), base(240, cash=9000)) == []


@pytest.mark.parametrize("income_per_s, ordered", [(120, []), (149, []), (150, ["tent"]), (155, ["tent"])])
def test_with_three_barracks_a_fourth_needs_150_a_second(income_per_s, ordered):
    assert built(SetIncome(income_per_s, infantry=0.9), base(barracks=3)) == ordered


def test_money_that_piled_up_does_not_do_for_the_fourth_and_fifth_barracks():
    assert built(SetIncome(120, infantry=0.9, least_money=9000), base(barracks=3, cash=9000)) == []


@pytest.mark.parametrize(
    "income_per_s, vehicle, least_money, ordered",
    [(170, 0.0, 0, ["tent"]), (160, 0.9, 2600, ["tent"]), (160, 0.0, 2600, []), (160, 0.9, 2500, [])],
    ids=["170 a second", "money piled up, both queues busy", "the vehicle queue idle", "2,500 is not above"],
)
def test_the_seventh_barracks_needs_170_a_second_or_money_above_2500_while_both_queues_are_busy(
    income_per_s, vehicle, least_money, ordered
):
    measures = SetIncome(income_per_s, infantry=0.9, vehicle=vehicle, least_money=least_money)

    assert built(measures, base(barracks=6, factories=4, cash=3000)) == ordered


def test_with_seven_barracks_none_more_is_ordered_at_any_income():
    assert built(SetIncome(1000, infantry=1.0), base(barracks=7, factories=4, cash=9000)) == []


def test_the_investment_test_is_asked_with_the_barracks_cost_and_its_build_time_from_the_item_table():
    measures = SetIncome(110, infantry=0.9)

    built(measures, base(240, cash=1000))

    assert measures.asked == [(500, 12.0)]


def test_a_barracks_the_money_and_half_the_income_cannot_pay_for_is_not_ordered():
    # Half of 110 a second over the 12-s build is 660: with 300 of money, 300 - 300 + 660 pays for the 500; with none,
    # 360 does not.
    assert built(SetIncome(110, infantry=0.9), base(240, cash=300)) == ["tent"]
    assert built(SetIncome(110, infantry=0.9), base(240, cash=0)) == []


def test_a_barracks_that_is_not_on_offer_is_not_ordered():
    assert built(SetIncome(110, infantry=0.9), base(240, withheld=("fix", "tent"))) == []


def test_a_soviet_base_builds_its_own_barracks():
    assert built(SetIncome(110, infantry=0.9), base(240, faction="russia")) == ["barr"]


# -- war factories ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "income_per_s, invests, ordered",
    [(130, None, ["weap"]), (120, None, []), (130, False, [])],
    ids=["130 a second", "120 a second", "the investment test fails"],
)
def test_with_one_war_factory_its_queue_busy_and_130_a_second_a_second_is_ordered(income_per_s, invests, ordered):
    measures = SetIncome(income_per_s, vehicle=0.9, invests=invests)

    assert built(measures, base(cash=1300)) == ordered


@pytest.mark.parametrize(
    "factories, income_per_s, ordered",
    [(2, 149, []), (2, 150, ["weap"]), (3, 184, []), (3, 185, ["weap"]), (4, 1000, [])],
    ids=["third, 149", "third, 150", "fourth, 184", "fourth, 185", "four standing"],
)
def test_the_third_war_factory_needs_150_a_second_the_fourth_185_and_there_is_no_fifth(
    factories, income_per_s, ordered
):
    assert built(SetIncome(income_per_s, vehicle=0.9), base(factories=factories, cash=1300)) == ordered


def test_a_war_factory_is_not_ordered_while_its_queue_is_busy_less_than_85_percent():
    assert built(SetIncome(200, vehicle=0.84), base(cash=5000)) == []


def test_the_investment_test_is_asked_with_the_war_factory_cost_and_its_build_time_from_the_item_table():
    measures = SetIncome(130, vehicle=0.9)

    built(measures, base(cash=1300))

    assert measures.asked == [(2000, 48.0)]


# -- the order of the two, power, and when nothing is added ----------------------------------------


def test_when_both_qualify_the_batch_holds_the_barracks_and_not_the_war_factory():
    orders = ask(producer_agent(SetIncome(200, infantry=0.9, vehicle=0.9)), base(cash=3000))

    assert builds(orders) == ["tent"] and "weap" not in [order.get("item") for order in orders]


def test_with_headroom_25_and_a_target_of_60_no_producer_is_ordered_and_the_power_plant_is():
    # The target is 60 from 360 s on.
    state = base(380, cash=3000, provided=125)

    assert built(SetIncome(200, infantry=0.9, vehicle=0.9), state) == ["powr"]


def test_nothing_is_ordered_during_an_active_base_defense_threat():
    agent = producer_agent(SetIncome(200, infantry=0.9, vehicle=0.9))
    agent.ctx.threat_active = True

    assert builds(ask(agent, base(cash=3000))) == []


@pytest.mark.parametrize(
    "refineries, cash, ordered",
    [(1, 1000, []), (1, 1700, ["proc"]), (2, 1000, ["tent"])],
    ids=["saving for the lost refinery", "the money covers it", "nothing lost"],
)
def test_no_barracks_is_ordered_while_the_money_is_saved_for_a_lost_refinery(refineries, cash, ordered):
    # A lost refinery (1,400) waits until the money covers it and the reserve of 300; the second barracks is due.
    assert built(SetIncome(110, infantry=0.9), base(240, refineries=refineries, cash=cash)) == ordered


class AlwaysOpening:
    """A plan selector that keeps the plan at OPENING."""

    def select(self, world, ctx, choice) -> str:
        return OPENING


def test_nothing_is_ordered_in_opening():
    measures = SetIncome(200, infantry=0.9, vehicle=0.9)

    assert built(measures, base(cash=3000), plan_selector=AlwaysOpening()) == []


def test_nothing_is_ordered_before_a_war_factory_has_stood():
    assert built(SetIncome(200, infantry=0.9, vehicle=0.9), base(factories=0, cash=3000)) == []


@pytest.mark.parametrize("item", ["tent", "weap"])
def test_nothing_more_is_ordered_while_a_producer_is_in_the_building_queue(item):
    state = base(cash=3000, production=[situations.job("Building", item)])

    assert built(SetIncome(200, infantry=0.9, vehicle=0.9), state) == []


def test_one_producer_ordered_is_not_ordered_again_in_the_next_cycle():
    agent = producer_agent(SetIncome(200, infantry=0.9))

    first = builds(ask(agent, base(240, cash=3000)))
    second = builds(ask(agent, base(240.2, cash=3000)))

    assert (first, second) == (["tent"], [])
    assert agent.ledger.duplicate_production_orders == 0


def test_a_lost_extra_barracks_is_built_again_only_when_its_gate_holds_again():
    # Three barracks were standing; one is lost. The third needs 100 a second again.
    assert built(SetIncome(90, infantry=0.9), base(barracks=2, cash=1000)) == []
    assert built(SetIncome(100, infantry=0.9), base(barracks=2, cash=1000)) == ["tent"]


def test_the_producer_thresholds_are_read_from_the_tracked_file_with_the_values_the_requirement_gives():
    producers = Tuning.from_file(situations.REPO_ROOT).producers

    assert (producers.barracks_from_seconds, producers.busy_share, producers.float_seconds) == (210, 0.85, 30)
    assert producers.barracks_income == (100, 100, 150, 150, 170, 170)
    assert (producers.barracks_float, producers.late_barracks_float) == (1500, 2500)
    assert producers.factories_income == (125, 150, 185)
    assert (producers.max_infantry_producers, producers.max_war_factories) == (7, 4)
    assert producers == Tuning().producers


# -- money banked: the second war factory on a bank ------------------------------------------------

BANK_FACTORY_FROM = 300
# A state every 5 seconds, as many as the run of states given lasts.
BANK_STEP_SECONDS = 5


@pytest.fixture
def economy_events(monkeypatch) -> list[dict]:
    """The data of every `economy` event logged from here on."""
    logged: list[dict] = []
    monkeypatch.setattr(records, "log", lambda kind, data: logged.append(data) if kind == "economy" else None)
    return logged


def built_while_banked(agent, banked: int, seconds: int, **state) -> list[list[str]]:
    """What the agent builds in the Building queue in each state of a run from 300 s, one every 5 s, lasting so many
    seconds, with `banked` money above the production reserve in all of them."""
    times = range(BANK_FACTORY_FROM, BANK_FACTORY_FROM + seconds + 1, BANK_STEP_SECONDS)
    return [builds(ask(agent, base(at, cash=RESERVE + banked, **state))) for at in times]


def test_bank_factory_with_one_war_factory_70_a_second_and_2600_banked_for_30_seconds_the_second_is_ordered(
    economy_events,
):
    ordered = built_while_banked(producer_agent(SetIncome(70)), 2600, 30)

    assert ordered == [[]] * 6 + [["weap"]]
    assert economy_events == [{"tick": 330 * 25, "reason": "bank_factory", "item": "weap", "money": 2900}]


@pytest.mark.parametrize("banked, seconds", [(2600, 20), (2000, 30)], ids=["2,600 for 20 s", "2,000 for 30 s"])
def test_bank_factory_2600_banked_for_20_seconds_or_2000_for_30_orders_no_war_factory(banked, seconds, economy_events):
    ordered = built_while_banked(producer_agent(SetIncome(70)), banked, seconds)

    assert [item for items in ordered for item in items] == [] and economy_events == []


def test_bank_factory_a_state_below_2500_banked_starts_the_30_seconds_again():
    agent = producer_agent(SetIncome(70))
    built_while_banked(agent, 2600, 20)
    builds(ask(agent, base(325, cash=RESERVE + 2499)))

    # Banked again from 330 s: 25 seconds at 355 s, 30 at 360 s.
    after = [builds(ask(agent, base(at, cash=RESERVE + 2600))) for at in (330, 355, 360)]

    assert after == [[], [], ["weap"]]


@pytest.mark.parametrize(
    "state, tuning",
    [
        ({"factories": 2}, TWO_REFINERIES_ONLY),
        ({"withheld": ("fix", "weap")}, TWO_REFINERIES_ONLY),
        ({}, dataclasses.replace(TWO_REFINERIES_ONLY, producers=producers_off(max_war_factories=1))),
    ],
    ids=["two war factories", "no war factory on offer", "max_war_factories 1"],
)
def test_bank_factory_orders_nothing_with_two_war_factories_none_on_offer_or_no_room_under_the_most(state, tuning):
    ordered = built_while_banked(producer_agent(SetIncome(70), tuning=tuning), 2600, 30, **state)

    assert [item for items in ordered for item in items] == []


def test_bank_factory_a_war_factory_that_would_break_the_power_target_waits_for_the_power_plant(economy_events):
    # Headroom 25 meets the early target of 20, which the war factory's drain of 30 would break.
    ordered = built_while_banked(producer_agent(SetIncome(70)), 2600, 30, provided=125)

    assert ordered[-1] == ["powr"] and "weap" not in ordered[-1]
    assert [(event["reason"], event["item"]) for event in economy_events] == [("bank_factory", "powr")]


def test_bank_factory_orders_nothing_during_an_active_base_defense_threat():
    agent = producer_agent(SetIncome(70))
    agent.ctx.threat_active = True

    assert [item for items in built_while_banked(agent, 2600, 30) for item in items] == []


# -- money banked: an order for an empty Building queue --------------------------------------------

IDLE_BANKED = 3500
# The home field the opening's refineries stand against, and a field west of the yard's power plant that a refinery of
# the base can stand against now and none stands against.
HOME_FIELD = OrePatch(center=(22, 40), cells=frozenset((x, y) for x in (21, 22, 23) for y in (39, 40, 41)))
WEST_FIELD = OrePatch(center=(11, 50), cells=frozenset((x, y) for x in (10, 11, 12) for y in (49, 50, 51)))
TWO_FIELDS = Terrain((HOME_FIELD, WEST_FIELD), HOME_FIELD.cells | WEST_FIELD.cells)
# The opening's two refineries are all today's refinery rule orders; refinery_growth's own cap leaves room for more.
GROWTH_ROOM = tuning_off(upkeep=upkeep_off(refineries_max=2, refineries_max_late=2, refinery_growth=True))
# Headroom 50 against the target of 100 that the barracks and four war factories call for.
SHORT_OF_POWER = 150


class ProducersAlone:
    """A manager with upkeep's producer rule alone, so that no other rule of upkeep takes the Building queue first."""

    name = "upkeep"

    def __init__(self) -> None:
        self.storage = StorageWatch()
        self.rule = ProducerScaling(CoreBuildings(), MoreRefineries(), self.storage)

    def intents(self, world, ctx) -> list:
        # Upkeep notes the storage at the start of every cycle, before any of its rules is asked.
        self.storage.note(world, ctx.tuning)
        return self.rule.intents(world, ctx)


def built_while_idle(agent, terrain: Terrain | None = None, seconds: int = 30, **state) -> list[list[str]]:
    """What the agent builds in each state of a run from 300 s, one every 5 s, lasting so many seconds, with 3,500
    above the production reserve and nothing in the Building queue; on a map of `terrain`, when given, from the first
    state on."""
    built = []
    for at in range(BANK_FACTORY_FROM, BANK_FACTORY_FROM + seconds + 1, BANK_STEP_SECONDS):
        built.append(builds(ask(agent, base(at, cash=RESERVE + IDLE_BANKED, **state))))
        # The match's first state gives the agent the map of the context, which has no ore.
        agent.ctx.terrain = terrain or agent.ctx.terrain
    return built


def defenses_ordered(agent, **state) -> list[str]:
    """The Defense-queue items an agent orders over a run of states with the Building queue empty and a big bank."""
    items = situations.tracked_items()
    times = range(BANK_FACTORY_FROM, BANK_FACTORY_FROM + 31, BANK_STEP_SECONDS)
    orders = [order for at in times for order in ask(agent, base(at, cash=RESERVE + IDLE_BANKED, **state))]
    return [order["item"] for order in with_command(orders, "build") if items[order["item"]].queue == "Defense"]


def test_idle_bank_the_building_queue_empty_30_seconds_with_3500_banked_orders_a_war_factory_below_the_cap(
    economy_events,
):
    ordered = built_while_idle(producer_agent(SetIncome(70)), factories=2)

    assert ordered == [[]] * 6 + [["weap"]]
    assert economy_events == [{"tick": 330 * 25, "reason": "idle_bank", "item": "weap", "money": 3800}]


def test_idle_bank_at_the_cap_of_war_factories_a_refinery_for_the_untaken_field_is_ordered(economy_events):
    agent = producer_agent(SetIncome(70), tuning=GROWTH_ROOM)

    ordered = built_while_idle(agent, TWO_FIELDS, factories=4)
    ask(agent, base(330.2, cash=RESERVE + IDLE_BANKED, factories=4))

    assert ordered == [[]] * 6 + [["proc"]]
    assert [(event["reason"], event["item"]) for event in economy_events] == [("idle_bank", "proc")]
    # The refinery on order is for the untaken field, which placement then stands it against.
    assert agent.ctx.asked_refinery_fields == [WEST_FIELD]


@pytest.mark.parametrize(
    "tuning, terrain",
    [(TWO_REFINERIES_ONLY, TWO_FIELDS), (GROWTH_ROOM, None)],
    ids=["no room for a refinery", "no untaken field"],
)
def test_idle_bank_with_neither_a_war_factory_nor_a_refinery_allowed_nothing_is_ordered_while_power_suffices(
    tuning, terrain, economy_events
):
    ordered = built_while_idle(producer_agent(SetIncome(70), tuning=tuning), terrain, factories=4)

    assert [item for items in ordered for item in items] == [] and economy_events == []


def test_idle_bank_with_neither_allowed_the_power_plant_the_power_target_calls_for_is_ordered():
    ordered = built_while_idle(producer_agent(SetIncome(70)), factories=4, provided=SHORT_OF_POWER)

    assert ["powr"] in ordered and "weap" not in str(ordered) and "proc" not in str(ordered)


def test_idle_bank_the_producer_rule_alone_orders_the_power_plant_the_power_target_calls_for(economy_events):
    agent = new_agent(ProducersAlone(), plan_selector=OpeningThen(HOLD), tuning=TWO_REFINERIES_ONLY)
    agent.ctx.income = SetIncome(70)

    ordered = built_while_idle(agent, factories=4, provided=SHORT_OF_POWER)

    assert ordered == [[]] * 6 + [["powr"]]
    assert [(event["reason"], event["item"]) for event in economy_events] == [("idle_bank", "powr")]


@pytest.mark.parametrize(
    "state",
    [{"factories": 1}, {"factories": 2}, {"factories": 4}, {"factories": 4, "provided": SHORT_OF_POWER}],
    ids=["bank factory", "war factory", "nothing allowed", "power short"],
)
def test_idle_bank_and_bank_factory_order_no_defense(state):
    assert defenses_ordered(producer_agent(SetIncome(70), tuning=GROWTH_ROOM), **state) == []


def test_idle_bank_a_finished_building_waiting_to_be_placed_keeps_the_building_queue_from_counting_as_empty(
    economy_events,
):
    held = [situations.job("Building", "powr", ready=True)]

    built_while_idle(producer_agent(SetIncome(70)), factories=2, production=held)

    assert economy_events == []


def test_idle_bank_under_2500_or_3000_banked_an_empty_building_queue_is_given_nothing(economy_events):
    agent = producer_agent(SetIncome(70))
    times = range(BANK_FACTORY_FROM, BANK_FACTORY_FROM + 61, BANK_STEP_SECONDS)

    ordered = [builds(ask(agent, base(at, cash=RESERVE + 2999, factories=2))) for at in times]

    assert [item for items in ordered for item in items] == [] and economy_events == []


def watched_state(seconds: float, banked: int = IDLE_BANKED, building: list | None = None):
    """What the bank watch reads of a state: its tick, its money and its Building queue."""
    production = {} if building is None else {"Building": building}
    economy = SimpleNamespace(spendable=RESERVE + banked)
    return SimpleNamespace(tick=round(seconds * 25), economy=economy, production=production)


def test_idle_bank_after_an_order_for_an_empty_building_queue_the_next_waits_30_seconds():
    watch, tuning = BankWatch(), Tuning()
    for seconds in (0, 30):
        watch.note(watched_state(seconds), tuning)
    is_due_first = watch.is_idle_with_bank(watched_state(30), tuning)
    watch.idle_orders([Intent(Priority.PRODUCTION, {"cmd": "build", "item": "weap"})], watched_state(30))

    later = []
    for seconds in (45, 59.96, 60):
        watch.note(watched_state(seconds), tuning)
        later.append(watch.is_idle_with_bank(watched_state(seconds), tuning))

    assert (is_due_first, later) == (True, [False, False, True])


def test_idle_bank_an_empty_answer_does_not_start_the_wait_and_a_queue_that_held_something_starts_it_again():
    watch, tuning = BankWatch(), Tuning()
    watch.note(watched_state(0), tuning)
    watch.note(watched_state(30), tuning)
    watch.idle_orders([], watched_state(30))
    still_due = watch.is_idle_with_bank(watched_state(30), tuning)
    watch.note(watched_state(35, building=["powr"]), tuning)
    watch.note(watched_state(40), tuning)

    later = [watch.is_idle_with_bank(watched_state(seconds), tuning) for seconds in (69.96, 70)]

    assert (still_due, later) == (True, [False, True])


# -- ore near the storage cap ----------------------------------------------------------------------

STORAGE = 10_000
NEAR_CAP_ORE = 7000
# A state every 5 seconds from 300 s on: the rule comes into force in the state 20 seconds after the first.
NEAR_CAP_TIMES = range(BANK_FACTORY_FROM, BANK_FACTORY_FROM + 21, BANK_STEP_SECONDS)


def stored_state(seconds: float, ore: int, cash: int = 0, capacity: int = STORAGE):
    """What the storage watch reads of a state: its tick, its cash, the ore held and how much ore can be stored."""
    economy = SimpleNamespace(cash=cash, ore=ore, resource_capacity=capacity, spendable=cash + ore)
    return SimpleNamespace(tick=round(seconds * 25), economy=economy)


def in_force_over(watch, ore: int, cash: int = 0, capacity: int = STORAGE, times=NEAR_CAP_TIMES) -> list[bool]:
    """Whether the storage rule is in force after each state of a run that holds the ore, cash and storage given."""
    tuning, in_force = Tuning(), []
    for seconds in times:
        watch.note(stored_state(seconds, ore, cash, capacity), tuning)
        in_force.append(watch.is_in_force)
    return in_force


def test_storage_near_cap_7000_ore_of_10000_for_20_seconds_brings_the_rule_into_force_whatever_the_cash(
    economy_events,
):
    in_force = in_force_over(StorageWatch(), NEAR_CAP_ORE)

    assert in_force == [False] * 4 + [True]
    assert economy_events == [
        {"tick": 320 * 25, "reason": "storage_near_cap", "item": None, "money": 7000, "ore": 7000, "capacity": STORAGE}
    ]


@pytest.mark.parametrize(
    "ore, cash, capacity",
    [(5000, 9000, STORAGE), (5999, 0, STORAGE), (0, 9000, 0)],
    ids=["5,000 ore and 9,000 cash", "just under 60 percent", "nothing stored"],
)
def test_storage_near_cap_ore_under_60_percent_of_the_storage_or_no_storage_never_brings_it_into_force(
    ore, cash, capacity, economy_events
):
    assert in_force_over(StorageWatch(), ore, cash, capacity) == [False] * 5 and economy_events == []


def test_storage_near_cap_6000_of_10000_is_near_enough_and_the_rule_stays_in_force_without_a_second_event(
    economy_events,
):
    times = range(BANK_FACTORY_FROM, BANK_FACTORY_FROM + 41, BANK_STEP_SECONDS)

    assert in_force_over(StorageWatch(), 6000, times=times) == [False] * 4 + [True] * 5
    assert len(economy_events) == 1


def test_storage_near_cap_a_state_under_the_share_ends_the_rule_and_starts_the_20_seconds_again(economy_events):
    watch = StorageWatch()
    in_force_over(watch, NEAR_CAP_ORE)
    watch.note(stored_state(325, 5000), Tuning())
    ended = watch.is_in_force

    again = in_force_over(watch, NEAR_CAP_ORE, times=(330, 345, 350))

    assert (ended, again) == (False, [False, False, True])
    assert [event["tick"] for event in economy_events] == [320 * 25, 350 * 25]


def stored_base(seconds: float, ore: int, cash: int = 0, **more) -> dict:
    """The base of `base` at a game time, with so much ore held of what can be stored (10,000 unless given), and the
    cash given."""
    capacity = more.pop("capacity", STORAGE)
    state = base(seconds, cash=cash, **more)
    state["observation"]["economy"].update(ore=ore, resource_capacity=capacity)
    return state


def built_near_cap(agent, ore: int, cash: int = 0, times=NEAR_CAP_TIMES, **more) -> list[list[str]]:
    """What the agent builds in the Building queue in each state of a run that holds the ore and cash given."""
    return [builds(ask(agent, stored_base(seconds, ore, cash, **more))) for seconds in times]


def storage_event(seconds: float, item: str | None, ore: int = NEAR_CAP_ORE, cash: int = 0) -> dict:
    """The data of an `economy` event of the storage rule at a game time, with 10,000 that can be stored."""
    return {
        "tick": round(seconds * 25),
        "reason": "storage_near_cap",
        "item": item,
        "money": cash + ore,
        "ore": ore,
        "capacity": STORAGE,
    }


def test_storage_near_cap_upkeep_notes_the_ore_in_every_cycle_and_writes_the_start(economy_events):
    built_near_cap(producer_agent(SetIncome(70)), NEAR_CAP_ORE, factories=2, withheld=("fix", "weap"))

    assert economy_events == [storage_event(320, None)]


@pytest.mark.parametrize("cash", [0, 9000], ids=["no cash", "9,000 cash"])
def test_storage_near_cap_7000_ore_of_10000_for_20_seconds_orders_a_fifth_war_factory_with_four_standing(
    cash, economy_events
):
    ordered = built_near_cap(producer_agent(SetIncome(70)), NEAR_CAP_ORE, cash, factories=4)

    assert ordered == [[]] * 4 + [["weap"]]
    assert economy_events == [storage_event(320, None, cash=cash), storage_event(320, "weap", cash=cash)]


def test_storage_near_cap_5000_ore_and_9000_cash_orders_no_war_factory(economy_events):
    ordered = built_near_cap(producer_agent(SetIncome(70)), 5000, 9000, factories=4)

    assert ordered == [[]] * 5 and economy_events == []


@pytest.mark.parametrize("factories, ordered", [(5, ["weap"]), (6, [])], ids=["five standing", "six standing"])
def test_storage_near_cap_the_sixth_war_factory_is_the_last(factories, ordered):
    assert built_near_cap(producer_agent(SetIncome(70)), NEAR_CAP_ORE, factories=factories)[-1] == ordered


@pytest.mark.parametrize(
    "state",
    [{"withheld": ("fix", "weap")}, {"production": [situations.job("Building", "weap")]}],
    ids=["no war factory on offer", "a war factory on its way"],
)
def test_storage_near_cap_no_war_factory_while_none_is_on_offer_or_one_is_on_its_way(state):
    ordered = built_near_cap(producer_agent(SetIncome(70)), NEAR_CAP_ORE, factories=4, **state)

    assert "weap" not in [item for items in ordered for item in items]


def test_storage_near_cap_nothing_is_ordered_during_an_active_base_defense_threat():
    agent = producer_agent(SetIncome(70))
    agent.ctx.threat_active = True

    assert built_near_cap(agent, NEAR_CAP_ORE, factories=4) == [[]] * 5


# Headroom 120 meets the target of 100 that the barracks and four war factories call for; a war factory's drain of 30
# would break it.
JUST_ENOUGH_POWER = 220


def test_storage_near_cap_a_war_factory_that_would_break_the_power_target_waits_for_the_power_plant(economy_events):
    agent = new_agent(ProducersAlone(), plan_selector=OpeningThen(HOLD), tuning=TWO_REFINERIES_ONLY)
    agent.ctx.income = SetIncome(70)

    ordered = built_near_cap(agent, NEAR_CAP_ORE, factories=4, provided=JUST_ENOUGH_POWER)

    assert ordered == [[]] * 4 + [["powr"]]
    assert economy_events == [storage_event(320, None), storage_event(320, "powr")]


def test_storage_near_cap_after_a_war_factory_the_next_waits_20_seconds_and_a_power_plant_starts_no_wait():
    watch, tuning = StorageWatch(), Tuning()
    in_force_over(watch, NEAR_CAP_ORE)
    power_plant = [Intent(Priority.POWER, {"cmd": "build", "item": "powr"})]
    war_factory = [Intent(Priority.PRODUCTION, {"cmd": "build", "item": "weap"})]
    watch.orders(power_plant, stored_state(320, NEAR_CAP_ORE))
    after_power_plant = watch.may_order_factory(stored_state(320, NEAR_CAP_ORE), tuning)
    watch.orders(war_factory, stored_state(325, NEAR_CAP_ORE))

    later = []
    for seconds in (330, 344.96, 345):
        watch.note(stored_state(seconds, NEAR_CAP_ORE), tuning)
        later.append(watch.may_order_factory(stored_state(seconds, NEAR_CAP_ORE), tuning))

    assert (after_power_plant, later) == (True, [False, False, True])


# economy_first on, with a hold that would last 60 s: longer than the 20 s the storage rule waits.
LONG_HOLDS = tuning_off(
    upkeep=upkeep_off(refineries_max=2, refineries_max_late=2, economy_first=True, economy_wait_seconds=60)
)
# A refinery claim's refinery is due and the money, 1,500 of ore held, does not cover it and the reserve (1,700).
# Of 2,000 that can be stored, 1,500 is near the cap and 1,100 is not.
SMALL_STORAGE = 2000
HELD_TIMES = range(BANK_FACTORY_FROM, BANK_FACTORY_FROM + 31, BANK_STEP_SECONDS)


def unit_trains(orders: list[dict]) -> list[tuple[str, int]]:
    """The item and count of each `train` order of a batch."""
    return [(order["item"], order["count"]) for order in with_command(orders, "train")]


@pytest.mark.parametrize(
    "ore, trained_from, hold_events",
    [
        (1500, 320, [("started", 300), ("storage_near_cap", 320)]),
        (1100, None, [("started", 300)]),
    ],
    ids=["near the cap", "under the share"],
)
def test_storage_near_cap_an_economy_first_hold_does_not_stop_the_unit_queues(
    ore, trained_from, hold_events, monkeypatch
):
    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(records, "log", lambda kind, data: logged.append((kind, data)))
    monkeypatch.setattr("ucla_agent.games.redalert.upkeep._is_claim_due", lambda ctx: True)
    agent = producer_agent(SetIncome(70), tuning=LONG_HOLDS)

    # No war factory is on offer, so the storage rule orders none and the unit budget is left whole.
    state = {"capacity": SMALL_STORAGE, "withheld": ("fix", "weap")}
    trains = {at: unit_trains(ask(agent, stored_base(at, ore, **state))) for at in HELD_TIMES}

    trained = [at for at, orders in trains.items() if orders]
    assert trained[:1] == ([] if trained_from is None else [trained_from])
    holds = [(data["reason"], data["tick"] // 25) for kind, data in logged if kind == "economy_hold"]
    assert holds == hold_events and all(data["item"] == "proc" for kind, data in logged if kind == "economy_hold")


# -- money banked: a whole fake match with the shipped config --------------------------------------

# No run of states this long is left in which the Building queue is empty, at least this much lies above the
# production reserve and the idle-bank rule has an order it may make.
IDLE_GAP_MOST_SECONDS = 60
IDLE_GAP_MONEY = 3000


@dataclasses.dataclass
class ShippedMatch:
    """A whole fake match played with the shipped tuning, and for each state the agent answered: its tick, whether
    the Building queue was empty with at least 3,000 above the production reserve, and whether the idle-bank rule
    then had an order it may make (`idle_bank_may_order`); and the war factories standing or on their way."""

    match: WholeMatch
    states: list[tuple[int, bool, bool]]
    war_factories: list[int]


def has_untaken_field(world, ctx) -> bool:
    """Whether a refinery can stand against a field no refinery stands against or was asked against now, the field
    reserved for the MCV's site counted as asked."""
    asked = [*ctx.asked_refinery_fields, *([] if ctx.reserved_field is None else [ctx.reserved_field])]
    choice = placement.spread_placement(world, ctx.terrain, ctx.tuning.placement, asked, untaken_only=True)
    return choice is not None


def refinery_cap(world, ctx) -> int:
    """The most refineries the refinery rules allow at the state's game time: refineries_max, refineries_max_late
    late in the match, and growth_refineries_max with refinery_growth on when that is more."""
    tuning = ctx.tuning.upkeep
    is_late = world.game_seconds >= tuning.refineries_late_after_seconds
    allowed = tuning.refineries_max_late if is_late else tuning.refineries_max
    return max(allowed, tuning.growth_refineries_max) if tuning.refinery_growth else allowed


def idle_war_factory_allowed(world, ctx) -> bool:
    """Whether the idle-bank rule may order a war factory: one on offer below max_war_factories that the war factory
    load gate lets through."""
    has_room = ctx.production.planned("weap") < ctx.tuning.producers.max_war_factories
    return "weap" in world.available_production and has_room and war_factory_load.is_loaded(ctx)


def idle_refinery_allowed(world, ctx) -> bool:
    """Whether the idle-bank rule may order a refinery: one on offer, none on its way, under the refinery caps, for an
    untaken field."""
    production = ctx.production
    has_room = not production.pending("proc") and production.planned("proc") < refinery_cap(world, ctx)
    return "proc" in world.available_production and has_room and has_untaken_field(world, ctx)


def idle_bank_may_order(world, ctx) -> bool:
    """Whether the idle-bank rule has an order it may make, as the requirement lists them: a war factory below
    max_war_factories that the war factory load gate lets through, a refinery for an untaken field under the refinery
    caps, or a power plant the power target calls for."""
    if idle_war_factory_allowed(world, ctx) or idle_refinery_allowed(world, ctx):
        return True
    production = ctx.production
    is_power_plant_on_its_way = production.pending("powr") + production.pending("apwr") > 0
    return production.headroom() < power_target(world, ctx) and not is_power_plant_on_its_way


# Two harvesters more from the first minute: two earn what the shipped army build spends on its tanks and rocket
# soldiers together, and the bank rules need money that banks.
EXTRA_HARVESTERS = [fake_game.at_seconds(60, lambda game: [game.add_own("harv", 30, 50 + n) for n in range(2)])]


@pytest.fixture(scope="module")
def shipped_bank_match() -> ShippedMatch:
    states: list[tuple[int, bool, bool]] = []
    war_factories: list[int] = []

    def note(agent) -> None:
        world, ctx = agent.world, agent.ctx
        is_idle = not world.production.get("Building")
        is_banked = world.economy.spendable - RESERVE >= IDLE_GAP_MONEY
        is_idle_with_bank = is_idle and is_banked
        states.append((world.tick, is_idle_with_bank, is_idle_with_bank and idle_bank_may_order(world, ctx)))
        war_factories.append(ctx.production.planned("weap"))

    return ShippedMatch(play_whole_match(Tuning(), scenario=EXTRA_HARVESTERS, watch=note), states, war_factories)


def longest_gap_seconds(states: list[tuple[int, bool, bool]], counted: int) -> float:
    """The longest run of states, in seconds, whose entry `counted` (1: empty with the bank; 2: the same while the
    idle-bank rule may order) holds."""
    longest, since = 0.0, None
    for state in states:
        if not state[counted]:
            since = None
            continue
        since = state[0] if since is None else since
        longest = max(longest, (state[0] - since) / 25)
    return longest


def test_idle_bank_a_whole_fake_match_with_the_shipped_config_runs_without_an_error_within_the_compute_budget(
    shipped_bank_match,
):
    match = shipped_bank_match.match

    assert match.agent.ctx.tuning == Tuning() == Tuning.from_file(situations.REPO_ROOT)
    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.compute_p95_ms <= COMPUTE_P95_MS


def test_idle_bank_in_that_match_no_idle_gap_exceeds_60_seconds_while_3000_is_banked_and_the_rule_may_order(
    shipped_bank_match,
):
    states = shipped_bank_match.states

    assert len(states) == WHOLE_MATCH_STEPS
    assert longest_gap_seconds(states, 2) <= IDLE_GAP_MOST_SECONDS


def test_idle_bank_in_that_match_the_bank_rules_or_the_storage_rule_before_them_order_and_write_economy_events(
    shipped_bank_match,
):
    reasons = {event["reason"] for event in shipped_bank_match.match.data_of("economy")}

    # With 3 harvesters a refinery the shipped match's ore reaches the storage cap, and the storage rule, asked before
    # the bank rules, spends the bank on war factories first.
    assert reasons & {"bank_factory", "idle_bank", "storage_near_cap"}


def test_storage_near_cap_the_whole_fake_match_with_the_shipped_config_runs_without_an_error_within_the_budget(
    shipped_bank_match,
):
    match = shipped_bank_match.match

    assert match.agent.ctx.tuning.producers == ProducersTuning() == Tuning.from_file(situations.REPO_ROOT).producers
    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.compute_p95_ms <= COMPUTE_P95_MS


def test_storage_near_cap_in_that_match_the_war_factories_never_exceed_storage_war_factories_max(shipped_bank_match):
    storage_events = [
        event for event in shipped_bank_match.match.data_of("economy") if event["reason"] == "storage_near_cap"
    ]

    assert len(shipped_bank_match.war_factories) == WHOLE_MATCH_STEPS
    assert max(shipped_bank_match.war_factories) <= ProducersTuning().storage_war_factories_max
    # The fake game's refineries fill: the rule comes into force there, and the war factory it would order then is
    # ordered or, past the war factories the load already called for, held by the load gate as a wait of its own.
    storage_waits = [
        event
        for event in shipped_bank_match.match.data_of("economy")
        if event["reason"] == "war_factory_load_wait" and event["rule"] == "storage_near_cap"
    ]
    assert None in {event["item"] for event in storage_events}
    assert "weap" in {event["item"] for event in storage_events} or storage_waits


# -- the war factory load gate ---------------------------------------------------------------------

# The war factory load gate on, and the producer gates' income steps at 0 a second, so that with the Vehicle queue busy
# and the money there the gate alone decides one more war factory.
LOAD_GATE_ON = dataclasses.replace(
    TWO_REFINERIES_ONLY, producers=producers_off(war_factory_load_gate=True, factories_income=(0, 0, 0))
)
# The same with tank_factories on, its gates open from the start of the match.
TANK_GATES_ON = dataclasses.replace(
    TWO_REFINERIES_ONLY,
    producers=producers_off(
        war_factory_load_gate=True, tank_factories=True, tank_factories_income=(0, 0, 0), tank_factories_from_seconds=0
    ),
)
# The idle-bank rule's tuning with room for a refinery, and the gate on.
GROWTH_ROOM_GATED = dataclasses.replace(GROWTH_ROOM, producers=producers_off(war_factory_load_gate=True))
# A spend rate of 41.7 a second for each war factory, as parallel production lines would spend: the requirement's
# table was worked out for it.
PARALLEL_LINES = (1.0, 1 / 2, 1 / 3, 1 / 4)


def load_gated(income_per_s: float, factories: int, tuning: Tuning = LOAD_GATE_ON) -> list[str]:
    """What the agent builds with so many war factories standing, their queue busy, 2,500 of money and an income of
    `income_per_s`, the Infantry queue and MCVs having spent nothing."""
    return built(SetIncome(income_per_s, vehicle=0.9), base(factories=factories, cash=2500), tuning=tuning)


def load_wait(seconds: float, rule: str, vehicle_income: float | None, factories: int, needed: float) -> dict:
    """The data of the `economy` event of a war factory the load gate refused at a game time."""
    return {
        "tick": round(seconds * 25),
        "reason": "war_factory_load_wait",
        "rule": rule,
        "vehicle_income_per_s": vehicle_income,
        "war_factories": factories,
        "needed_per_s": needed,
    }


def test_load_gate_the_vehicle_queue_spends_41_7_55_6_69_4_and_83_3_a_second_at_1_2_3_and_4_or_more_war_factories():
    rates = [war_factory_load.queue_spend_per_s(factories) for factories in range(1, 7)]
    needed = [war_factory_load.needed_income_per_s(factories, 0.8) for factories in range(1, 5)]

    assert rates == pytest.approx([41.667, 55.556, 69.444, 83.333, 83.333, 83.333], abs=0.001)
    assert needed == pytest.approx([33.333, 44.444, 55.556, 66.667], abs=0.001)


@pytest.mark.parametrize(
    "factories, income_per_s, ordered",
    [(1, 30, []), (1, 35, ["weap"]), (2, 60, []), (2, 70, ["weap"])],
    ids=["one standing, 30 a second", "one standing, 35 a second", "two standing, 60 a second", "two, 70 a second"],
)
def test_load_gate_the_requirement_s_table_holds_at_41_7_a_second_for_each_war_factory(
    factories, income_per_s, ordered, monkeypatch
):
    monkeypatch.setattr(war_factory_load, "VEHICLE_BUILD_TIME_SHARES", PARALLEL_LINES)

    assert load_gated(income_per_s, factories) == ordered


@pytest.mark.parametrize(
    "factories, income_per_s, ordered",
    [
        (1, 30, []),
        (1, 35, ["weap"]),
        (2, 44, []),
        (2, 45, ["weap"]),
        (2, 60, ["weap"]),
        (3, 55, []),
        (3, 56, ["weap"]),
    ],
    ids=["2nd at 30", "2nd at 35", "3rd at 44", "3rd at 45", "3rd at 60", "4th at 55", "4th at 56"],
)
def test_load_gate_with_the_engine_s_one_vehicle_queue_the_2nd_needs_33_3_the_3rd_44_4_and_the_4th_55_6(
    factories, income_per_s, ordered
):
    assert load_gated(income_per_s, factories) == ordered


def gate_context(planned: int, vehicle_income: float | None, gate: bool = True) -> SimpleNamespace:
    """What `is_loaded` reads of a match: the producers' tuning, the war factories planned and the vehicle income."""
    return SimpleNamespace(
        tuning=SimpleNamespace(producers=producers_off(war_factory_load_gate=gate)),
        production=SimpleNamespace(planned=lambda item: planned if item == "weap" else 0),
        income=SimpleNamespace(vehicle_income_per_s=vehicle_income),
    )


@pytest.mark.parametrize(
    "planned, vehicle_income, gate, loaded",
    [(0, None, True, True), (1, None, True, False), (1, 33.3, True, False), (1, 33.4, True, True), (3, 0, False, True)],
    ids=["the first is never gated", "an unknown income", "33.3 with one", "33.4 with one", "the gate off"],
)
def test_load_gate_is_loaded_the_first_war_factory_and_the_gate_off_pass_and_an_unknown_income_reaches_no_gate(
    planned, vehicle_income, gate, loaded
):
    assert war_factory_load.is_loaded(gate_context(planned, vehicle_income, gate)) is loaded


@pytest.mark.parametrize(
    "tuning, rule", [(LOAD_GATE_ON, "producer_gates"), (TANK_GATES_ON, "tank_factories")], ids=["producers", "tanks"]
)
def test_load_gate_a_war_factory_the_producer_gates_would_order_is_held_and_written_as_a_wait(
    tuning, rule, economy_events
):
    assert load_gated(30, 1, tuning) == []
    assert economy_events == [load_wait(240, rule, 30.0, 1, 33.3)]


def test_load_gate_the_bank_rule_orders_no_second_war_factory_below_33_3_a_second(economy_events):
    ordered = built_while_banked(producer_agent(SetIncome(30), tuning=LOAD_GATE_ON), 2600, 30)

    assert [item for items in ordered for item in items] == []
    assert economy_events == [load_wait(330, "bank_factory", 30.0, 1, 33.3)]


def test_load_gate_the_bank_rule_orders_the_second_war_factory_at_35_a_second(economy_events):
    ordered = built_while_banked(producer_agent(SetIncome(35), tuning=LOAD_GATE_ON), 2600, 30)

    assert ordered == [[]] * 6 + [["weap"]]
    assert [event["reason"] for event in economy_events] == ["bank_factory"]


def test_load_gate_off_the_bank_rule_orders_the_second_war_factory_at_a_vehicle_income_of_0():
    ordered = built_while_banked(producer_agent(SetIncome(0)), 2600, 30)

    assert ordered == [[]] * 6 + [["weap"]]


def test_load_gate_a_refusal_that_holds_is_written_once_in_30_seconds(economy_events):
    built_while_banked(producer_agent(SetIncome(30), tuning=LOAD_GATE_ON), 2600, 70)

    assert [event["tick"] for event in economy_events] == [330 * 25, 360 * 25]


@pytest.mark.parametrize("income_per_s, ordered", [(66, []), (67, ["weap"])], ids=["66 a second", "67 a second"])
def test_load_gate_ore_near_the_cap_orders_a_fifth_war_factory_only_from_66_7_a_second(
    income_per_s, ordered, economy_events
):
    agent = producer_agent(SetIncome(income_per_s), tuning=LOAD_GATE_ON)

    assert built_near_cap(agent, NEAR_CAP_ORE, factories=4) == [[]] * 4 + [ordered]
    waits = [event for event in economy_events if event["reason"] == "war_factory_load_wait"]
    assert waits == ([] if ordered else [load_wait(320, "storage_near_cap", 66.0, 4, 66.7)])


def test_load_gate_an_idle_building_queue_below_the_gate_gets_the_refinery_for_the_untaken_field(economy_events):
    ordered = built_while_idle(producer_agent(SetIncome(40), tuning=GROWTH_ROOM_GATED), TWO_FIELDS, factories=2)

    assert ordered == [[]] * 6 + [["proc"]]
    assert [(event["reason"], event.get("rule")) for event in economy_events] == [
        ("war_factory_load_wait", "idle_bank"),
        ("idle_bank", None),
    ]


def test_load_gate_an_idle_building_queue_at_45_a_second_with_two_war_factories_gets_the_third():
    ordered = built_while_idle(producer_agent(SetIncome(45), tuning=GROWTH_ROOM_GATED), TWO_FIELDS, factories=2)

    assert ordered == [[]] * 6 + [["weap"]]


ECONOMY_FIRST_GATED = dataclasses.replace(
    LOAD_GATE_ON, upkeep=upkeep_off(refineries_max=2, refineries_max_late=2, economy_first=True)
)


@pytest.mark.parametrize("income_per_s, held", [(30, False), (35, True)], ids=["below the gate", "at the gate"])
def test_load_gate_economy_first_holds_the_unit_queues_only_for_a_war_factory_the_gate_lets_through(income_per_s, held):
    # 1,000 of money does not cover a war factory and the reserve, so economy_first would hold the unit queues for it.
    agent = producer_agent(SetIncome(income_per_s, vehicle=0.9), tuning=ECONOMY_FIRST_GATED)

    orders = ask(agent, base(cash=1000))

    assert (unit_trains(orders) == []) is held


# The second yard's MCV switched on with the gate, on a map with an ore field in the lane toward the enemy.
MCV_DUE_GATED = tuning_off(
    upkeep=upkeep_off(refineries_max=2, refineries_max_late=2),
    expansion=expansion_off(second_yard=True),
    producers=producers_off(war_factory_load_gate=True),
)
MCV_BASE = (
    ("fact", 22, 48),
    ("powr", 19, 48),
    ("tent", 22, 44),
    ("proc", 24, 39),
    ("proc", 18, 38),
    ("powr", 26, 48),
    ("weap", 15, 48),
    ("powr", 26, 52),
    ("pbox", 28, 44),
)
LANE_FIELD = OrePatch(center=(40, 47), cells=frozenset((x, y) for x in (39, 40, 41) for y in (46, 47, 48)))


def test_load_gate_an_mcv_due_at_a_vehicle_income_of_0_is_still_trained():
    agent = new_agent(UpkeepManager(), plan_selector=OpeningThen(HOLD), tuning=MCV_DUE_GATED)
    ask(agent, base_state("england", *MCV_BASE, tick=100, cash=0))
    agent.ctx.terrain = Terrain((HOME_FIELD, LANE_FIELD), HOME_FIELD.cells | LANE_FIELD.cells)
    agent.ctx.income = SetIncome(0)
    harvesters = [situations.unit(300 + number, "harv", 30, 30, idle=False) for number in range(4)]

    orders = ask(agent, base_state("england", *MCV_BASE, ("fix", 15, 53), tick=400 * 25, cash=2300, units=harvesters))

    assert ("mcv", 1) in unit_trains(orders)
    assert war_factory_load.is_loaded(agent.ctx) is False


# -- war factories on load: the load gate as the trigger --------------------------------------------

# The gate as the trigger, with tank_factories on as shipped: its income steps, busy share and 300 s no longer apply.
ON_LOAD = dataclasses.replace(
    TWO_REFINERIES_ONLY,
    producers=producers_off(war_factory_load_gate=True, war_factory_on_load=True, tank_factories=True),
)
# war_factory_on_load on with the gate off: it does nothing, and tank_factories' own gates order war factories.
ON_LOAD_GATE_OFF = dataclasses.replace(
    TWO_REFINERIES_ONLY, producers=producers_off(war_factory_on_load=True, tank_factories=True)
)


def on_load_built(
    income_per_s: float | None, seconds: float = 200, factories: int = 1, cash: int = 2500, tuning: Tuning = ON_LOAD
) -> list[str]:
    """What the agent builds at a game time with so many war factories standing, the money given and a vehicle income
    of `income_per_s`, the Vehicle queue idle the whole last minute."""
    return built(SetIncome(income_per_s), base(seconds, factories=factories, cash=cash), tuning=tuning)


def on_load_order(seconds: float, money: int) -> dict:
    """The data of the `economy` event of a war factory the load called for at a game time."""
    return {"tick": round(seconds * 25), "reason": "war_factory_on_load", "item": "weap", "money": money}


def test_on_load_the_keys_ship_off_from_180_s_in_the_built_ins_and_the_tracked_file():
    for shipped in (ProducersTuning(), Tuning.from_file(situations.REPO_ROOT).producers):
        assert (shipped.war_factory_on_load, shipped.war_factory_on_load_from_seconds) == (False, 180)


def test_on_load_at_200_s_with_one_war_factory_and_45_a_second_the_second_is_ordered_with_one_event(economy_events):
    assert on_load_built(45) == ["weap"]
    assert economy_events == [on_load_order(200, 2500)]


@pytest.mark.parametrize(
    "seconds, ordered", [(170, []), (179.96, []), (180, ["weap"])], ids=["170 s", "179.96 s", "exactly 180 s"]
)
def test_on_load_no_war_factory_before_180_s_and_at_exactly_180_s_with_the_gate_passing_one_is_ordered(
    seconds, ordered, economy_events
):
    assert on_load_built(45, seconds) == ordered
    assert [event["reason"] for event in economy_events] == ["war_factory_on_load"] * len(ordered)


@pytest.mark.parametrize(
    "factories, income_per_s, ordered",
    [(1, 33, []), (1, 34, ["weap"]), (2, 40, []), (2, 44, []), (2, 45, ["weap"]), (2, 50, ["weap"])],
    ids=["2nd at 33", "2nd at 34", "3rd at 40", "3rd at 44", "3rd at 45", "3rd at 50"],
)
def test_on_load_the_2nd_needs_33_3_a_second_and_the_3rd_44_4(factories, income_per_s, ordered):
    assert on_load_built(income_per_s, factories=factories) == ordered


@pytest.mark.parametrize("factories, income_per_s, ordered", [(3, 55, []), (3, 56, ["weap"]), (4, 1000, [])])
def test_on_load_the_4th_needs_55_6_a_second_and_none_is_ordered_with_max_war_factories_standing(
    factories, income_per_s, ordered
):
    assert on_load_built(income_per_s, factories=factories) == ordered


def test_on_load_a_refusal_below_the_gate_is_written_as_a_wait_of_the_rule_war_factory_on_load(economy_events):
    assert on_load_built(40, factories=2) == []
    assert economy_events == [load_wait(200, "war_factory_on_load", 40.0, 2, 44.4)]


def test_on_load_an_unknown_income_reaches_no_gate(economy_events):
    assert on_load_built(None) == []
    assert economy_events == [load_wait(200, "war_factory_on_load", None, 1, 33.3)]


@pytest.mark.parametrize(
    "cash, ordered", [(1200, []), (1300, ["weap"]), (2300, ["weap"])], ids=["1,200", "1,300", "2,300 at hand"]
)
def test_on_load_the_money_above_the_reserve_or_30_seconds_of_the_income_pays_for_it(cash, ordered):
    # At 35 a second, 30 seconds bring 1,050: 1,200 less the 300 reserve falls 50 short of the 2,000 price, 1,300 pays.
    assert on_load_built(35, cash=cash) == ordered


def test_on_load_the_income_steps_no_longer_apply_a_war_factory_at_45_a_second_with_the_vehicle_queue_idle():
    # tank_factories alone would wait for 300 s, 90 a second and the Vehicle queue busy 60 percent of the last minute.
    assert on_load_built(45, seconds=320) == ["weap"]


@pytest.mark.parametrize(
    "seconds, income_per_s, busy, ordered",
    [(200, 45, 0.9, []), (320, 45, 0.9, []), (320, 95, 0.65, ["weap"])],
    ids=["200 s at 45", "320 s at 45", "320 s at 95, the income step"],
)
def test_on_load_does_nothing_with_the_gate_off_and_the_tank_factory_income_steps_order_as_before(
    seconds, income_per_s, busy, ordered, economy_events
):
    measures = SetIncome(income_per_s, vehicle=busy)

    assert built(measures, base(seconds, cash=2500), tuning=ON_LOAD_GATE_OFF) == ordered
    assert economy_events == []


def test_on_load_a_war_factory_on_its_way_holds_the_next(economy_events):
    state = base(200, factories=2, cash=5000, production=[situations.job("Building", "weap")])

    assert built(SetIncome(100), state, tuning=ON_LOAD) == []
    assert economy_events == []
