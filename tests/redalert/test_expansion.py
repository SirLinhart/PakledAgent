"""The second construction yard: the early depot, the MCV, its escorted way to its site, the wait, and the deploy."""

from __future__ import annotations

import dataclasses

import pytest

import situations
from fake_game import FakeRedAlert, at_seconds
from situations import OpeningThen, ScriptedManager, ask, base_state, with_command
from switches_off import expansion_off, new_agent, placement_off, scouting_off, tuning_off, upkeep_off
from ucla_agent.core import records
from ucla_agent.games.redalert import cells, expansion, placement
from ucla_agent.games.redalert.composition import HOLD
from ucla_agent.games.redalert.economy_hold import FullHold
from ucla_agent.games.redalert.fight import strength
from ucla_agent.games.redalert.fresh_ore import FreshOre
from ucla_agent.games.redalert.income import IncomeEstimate
from ucla_agent.games.redalert.ledger import CONFIRMED, REJECTED
from ucla_agent.games.redalert.managers import MAJOR_THREAT, NO_THREAT
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tank_factories import vehicle_queue_takes_one
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.upkeep import UpkeepManager
from whole_match import COMPUTE_P95_MS, WHOLE_MATCH_STEPS, WholeMatch, play_whole_match, with_ore

TICKS_PER_SECOND = 25
# Two refineries for the opening, as upkeep's tests keep to, and the second yard switched on.
TWO_REFINERIES = dataclasses.replace(upkeep_off(), refineries_max=2, refineries_max_late=2)
SECOND_YARD = tuning_off(upkeep=TWO_REFINERIES, expansion=expansion_off(second_yard=True))
SWITCHED_OFF = tuning_off(upkeep=TWO_REFINERIES)
# The same with the creep to the next field on, so that there is a creep target for the site.
WITH_TARGET = dataclasses.replace(SECOND_YARD, placement=dataclasses.replace(placement_off(), creep_next_patch=True))

YARD = ("fact", 22, 48)
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
)
DEPOT = ("fix", 15, 53)
HOME_FIELD = OrePatch(center=(22, 40), cells=frozenset((x, y) for x in (21, 22, 23) for y in (39, 40, 41)))
LANE_FIELD = OrePatch(center=(40, 47), cells=frozenset((x, y) for x in (39, 40, 41) for y in (46, 47, 48)))
TERRAIN = Terrain((HOME_FIELD, LANE_FIELD), HOME_FIELD.cells | LANE_FIELD.cells)
MCV_ID = 400
MCV_CELL = (20, 57)
TANKS = [situations.unit(500 + number, "2tnk", 24 + number, 58) for number in range(3)]
HARVESTERS = [situations.unit(300 + number, "harv", 30, 30, idle=False) for number in range(4)]


def mcv(x: int = MCV_CELL[0], y: int = MCV_CELL[1], *, idle: bool = True) -> dict:
    return situations.unit(MCV_ID, "mcv", x, y, idle=idle)


def expanding_agent(tuning: Tuning = SECOND_YARD, terrain: Terrain = TERRAIN, *more):
    """An agent with the upkeep manager alone, under HOLD once a war factory has stood, on a map of this terrain."""
    agent = new_agent(UpkeepManager(), *more, plan_selector=OpeningThen(HOLD), tuning=tuning)
    ask(agent, base_state("england", *BASE, tick=100, cash=0))
    agent.ctx.terrain = terrain
    return agent


def england(*buildings, seconds: float, cash: int = 5000, units=(), **observation) -> dict:
    return base_state(
        "england", *buildings, tick=round(seconds * TICKS_PER_SECOND), cash=cash, units=list(units), **observation
    )


def builds(orders: list[dict]) -> list[str]:
    return [order["item"] for order in with_command(orders, "build")]


def trains(orders: list[dict]) -> list[tuple[str, int]]:
    return [(order["item"], order["count"]) for order in with_command(orders, "train")]


def mcv_orders(orders: list[dict]) -> list[dict]:
    return [order for order in orders if order.get("units") == [MCV_ID]]


# -- the depot --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("seconds, ordered", [(230, False), (240, True)])
def test_the_depot_is_ordered_once_the_war_factory_stands_from_240_s(seconds, ordered, match_record):
    agent = expanding_agent()

    orders = ask(agent, england(*BASE, seconds=seconds, cash=2000, units=HARVESTERS[:2]))

    assert builds(orders) == (["fix"] if ordered else [])
    events = match_record("expansion")
    assert [event["phase"] for event in events] == (["depot"] if ordered else [])


def test_the_depot_waits_under_a_major_threat_and_for_the_money_above_the_reserve():
    threatened = expanding_agent(SECOND_YARD, TERRAIN, ScriptedManager("base_defense", major_threat))
    state = england(*BASE, seconds=250, cash=1500, units=HARVESTERS[:2])

    assert builds(ask(expanding_agent(), england(*BASE, seconds=250, cash=1499, units=HARVESTERS[:2]))) == []
    assert builds(ask(expanding_agent(), state)) == ["fix"]
    assert builds(ask(threatened, state)) == []


def test_with_the_switch_off_no_early_depot_is_ordered():
    agent = expanding_agent(SWITCHED_OFF)

    assert builds(ask(agent, england(*BASE, seconds=250, cash=2000, units=HARVESTERS[:2]))) == []


# -- the MCV ----------------------------------------------------------------------------------------------------------


def test_with_the_depot_standing_and_2300_one_mcv_is_trained_and_not_a_second_while_it_lives():
    agent = expanding_agent()

    first = ask(agent, england(*BASE, DEPOT, seconds=400, cash=2300, units=HARVESTERS))
    on_its_way = ask(
        agent,
        england(*BASE, DEPOT, seconds=401, cash=2300, units=HARVESTERS, production=[situations.job("Vehicle", "mcv")]),
    )
    alive = ask(agent, england(*BASE, DEPOT, seconds=460, cash=5000, units=[*HARVESTERS, mcv()]))

    assert ("mcv", 1) in trains(first)
    assert ("mcv", 1) not in trains(on_its_way) and ("mcv", 1) not in trains(alive)
    # Its price is kept from the unit queues: nothing else is trained with it.
    assert trains(first) == [("mcv", 1)]


@pytest.mark.parametrize("cash, trained", [(2299, False), (2300, True)])
def test_the_mcv_needs_the_minimum_cash_above_the_reserve(cash, trained):
    orders = ask(expanding_agent(), england(*BASE, DEPOT, seconds=400, cash=cash, units=HARVESTERS))

    assert (("mcv", 1) in trains(orders)) is trained


def test_no_mcv_is_trained_while_two_yards_stand():
    orders = ask(expanding_agent(), england(*BASE, DEPOT, ("fact", 40, 56), seconds=400, cash=5000, units=HARVESTERS))

    assert ("mcv", 1) not in trains(orders)


def test_with_the_switch_off_no_mcv_is_ever_trained():
    orders = ask(expanding_agent(SWITCHED_OFF), england(*BASE, DEPOT, seconds=400, cash=5000, units=HARVESTERS))

    assert ("mcv", 1) not in trains(orders)


# -- the way to the site ----------------------------------------------------------------------------------------------


def site_by_brute_force(agent) -> tuple[int, int]:
    """The cell an MCV deploys on to unfold its yard, from the cell one up and one left of it, on the free place for a
    yard within 4 cells of the lane field's ore nearest its ore cell nearest the base."""
    places = [(x, y) for x in range(25, 50) for y in range(35, 60)]
    free = placement.open_cells("fact", places, agent.world, TERRAIN, agent.ctx.tuning.placement)
    near = [cell for cell in free if placement.ore_distance(placement.box_at("fact", cell), LANE_FIELD) <= 4]
    ore = LANE_FIELD.nearest_cell((22, 48))
    x, y = min(near, key=lambda cell: (cells.distance(cell, ore), cells.distance(cell, (22, 48))))
    return x + 1, y + 1


def test_the_mcv_goes_to_the_site_nearest_the_creep_target_s_ore_with_an_escort_of_at_least_1500(match_record):
    agent = expanding_agent(WITH_TARGET)

    orders = ask(agent, england(*BASE, DEPOT, seconds=500, units=[*HARVESTERS, mcv(), *TANKS]))

    site = site_by_brute_force(agent)
    hold = expansion.escort_hold(site, MCV_CELL, agent.world, TERRAIN)
    escort = [order for order in with_command(orders, "attack_move") if order["to"] == list(hold)]
    escorted = [agent.world.units[unit_id] for unit_id in escort[0]["units"]]
    assert agent.ctx.creep_target.patch == LANE_FIELD
    assert mcv_orders(orders) == [{"cmd": "move", "units": [MCV_ID], "to": list(site)}]
    assert strength(escorted, agent.world) >= 1500 and len(escorted) == 2
    assert set(agent.ctx.groups.members(expansion.ESCORT)) == {unit.id for unit in escorted}
    assert placement.ore_distance(placement.box_at("fact", site), LANE_FIELD) <= 4
    assert [(event["phase"], event["field_center"]) for event in match_record("expansion")] == [
        ("mcv_trained", [40, 47]),
        ("mcv_moving", [40, 47]),
    ]


def test_without_an_escort_of_1500_the_mcv_waits_at_home(match_record):
    agent = expanding_agent(WITH_TARGET)

    orders = ask(agent, england(*BASE, DEPOT, seconds=500, units=[*HARVESTERS, mcv(), TANKS[0]]))

    (waiting,) = [event for event in match_record("expansion") if event["phase"] == "mcv_waiting"]
    assert mcv_orders(orders) == [] and waiting["reason"] == "escort_short"


# South of the site, which stands right below the lane field: 10 cells from the site, 11 from the nearest ore cell, so
# that the field stays in the lane.
DEFENSE_NEAR_SITE = {"id": 900, "type": "gun", "x": 39, "y": 59, "hp": 100}


def test_with_a_defense_within_10_cells_of_the_site_the_mcv_waits_and_after_1500_ticks_deploys_at_home(match_record):
    agent = expanding_agent(WITH_TARGET)
    seen = {"visible_enemy_buildings": [DEFENSE_NEAR_SITE]}
    units = [*HARVESTERS, mcv(), *TANKS]

    waited = [ask(agent, england(*BASE, DEPOT, seconds=seconds, units=units, **seen)) for seconds in (500, 530, 559)]
    home = expansion.home_site(agent.world, agent.ctx)
    sent_home = ask(agent, england(*BASE, DEPOT, seconds=560, units=units, **seen))
    deployed = ask(agent, england(*BASE, DEPOT, seconds=570, units=[*HARVESTERS, mcv(*home), *TANKS], **seen))

    site = expansion.deploy_site(agent.world, agent.ctx)[0]
    assert cells.is_within((39, 59), site, 10) and agent.ctx.creep_target.patch == LANE_FIELD
    assert [mcv_orders(orders) for orders in waited] == [[], [], []]
    assert mcv_orders(sent_home) == [{"cmd": "move", "units": [MCV_ID], "to": list(home)}]
    assert mcv_orders(deployed) == [{"cmd": "deploy", "units": [MCV_ID]}]
    assert agent.ctx.groups.members(expansion.ESCORT) == []
    phases = [(event["phase"], event["reason"]) for event in match_record("expansion")]
    assert phases == [("mcv_trained", "second_yard"), ("mcv_waiting", "defense_near_site")]


def test_with_a_stronger_enemy_army_near_the_site_the_mcv_waits(match_record):
    agent = expanding_agent(WITH_TARGET)
    army = {"visible_enemies": [{"id": 910 + n, "type": "3tnk", "x": 44, "y": 44 + n, "hp": 100} for n in range(2)]}

    orders = ask(agent, england(*BASE, DEPOT, seconds=500, units=[*HARVESTERS, mcv(), *TANKS], **army))

    (waiting,) = [event for event in match_record("expansion") if event["phase"] == "mcv_waiting"]
    assert mcv_orders(orders) == [] and waiting["reason"] == "enemy_army_near_site"


def test_at_the_site_the_mcv_is_ordered_to_deploy():
    agent = expanding_agent(WITH_TARGET)
    ask(agent, england(*BASE, DEPOT, seconds=500, units=[*HARVESTERS, mcv(), *TANKS]))
    site = expansion.deploy_site(agent.world, agent.ctx)[0]

    orders = ask(agent, england(*BASE, DEPOT, seconds=520, units=[*HARVESTERS, mcv(*site), *TANKS]))

    assert mcv_orders(orders) == [{"cmd": "deploy", "units": [MCV_ID]}]


# -- what holds the second yard back, and what is lost ------------------------------------------------------------------

BASE_WITHOUT_A_REFINERY = tuple(spec for spec in BASE if spec != ("proc", 18, 38))
BASE_WITHOUT_THE_BARRACKS = tuple(spec for spec in BASE if spec[0] != "tent")


def test_while_a_lost_refinery_is_saved_for_the_depot_waits():
    orders = ask(expanding_agent(), england(*BASE_WITHOUT_A_REFINERY, seconds=300, cash=1600, units=HARVESTERS[:2]))

    assert builds(orders) == []


def test_while_a_lost_core_building_is_saved_for_no_mcv_is_trained():
    orders = ask(
        expanding_agent(), england(*BASE_WITHOUT_THE_BARRACKS, DEPOT, seconds=400, cash=2300, units=HARVESTERS)
    )

    assert ("mcv", 1) not in trains(orders) and builds(orders) == ["tent"]


def major_threat(world, ctx):
    ctx.threat_level = MAJOR_THREAT
    return []


def test_under_a_major_threat_no_mcv_is_trained_and_one_that_lives_waits(match_record):
    threatened = expanding_agent(WITH_TARGET, TERRAIN, ScriptedManager("base_defense", major_threat))

    untrained = ask(threatened, england(*BASE, DEPOT, seconds=400, cash=5000, units=HARVESTERS))
    waiting = ask(threatened, england(*BASE, DEPOT, seconds=500, units=[*HARVESTERS, mcv(), *TANKS]))

    (event,) = [event for event in match_record("expansion") if event["phase"] == "mcv_waiting"]
    assert ("mcv", 1) not in trains(untrained) and mcv_orders(waiting) == []
    assert event["reason"] == "major_threat"


def test_an_mcv_on_its_way_when_a_defense_is_seen_near_the_site_goes_back_home_and_its_escort_back_to_main():
    agent = expanding_agent(WITH_TARGET)
    units = [*HARVESTERS, mcv(), *TANKS]
    ask(agent, england(*BASE, DEPOT, seconds=500, units=units))

    back = ask(agent, england(*BASE, DEPOT, seconds=505, units=units, visible_enemy_buildings=[DEFENSE_NEAR_SITE]))

    home = expansion.home_site(agent.world, agent.ctx)
    assert mcv_orders(back) == [{"cmd": "move", "units": [MCV_ID], "to": list(home)}]
    assert agent.ctx.groups.members(expansion.ESCORT) == []


def test_a_deploy_without_a_verdict_is_sent_again_after_75_ticks():
    agent = expanding_agent(WITH_TARGET)
    ask(agent, england(*BASE, DEPOT, seconds=500, units=[*HARVESTERS, mcv(), *TANKS]))
    site = expansion.deploy_site(agent.world, agent.ctx)[0]
    at_site = [*HARVESTERS, mcv(*site), *TANKS]

    sent = [
        mcv_orders(ask(agent, england(*BASE, DEPOT, seconds=seconds, units=at_site))) for seconds in (520, 522, 523)
    ]

    deploy = [{"cmd": "deploy", "units": [MCV_ID]}]
    assert sent == [deploy, [], deploy]


def test_an_mcv_gone_without_a_new_yard_is_written_as_lost_and_its_escort_goes_back_to_main(match_record):
    agent = expanding_agent(WITH_TARGET)
    ask(agent, england(*BASE, DEPOT, seconds=500, units=[*HARVESTERS, mcv(), *TANKS]))

    ask(agent, england(*BASE, DEPOT, seconds=505, units=[*HARVESTERS, *TANKS]))

    phases = [(event["phase"], event["reason"]) for event in match_record("expansion")]
    assert phases[-1] == ("lost", "mcv_lost") and agent.ctx.groups.members(expansion.ESCORT) == []


def test_the_new_yard_is_written_as_deployed_and_its_loss_as_lost(match_record):
    agent = expanding_agent(WITH_TARGET)
    ask(agent, england(*BASE, DEPOT, seconds=500, units=[*HARVESTERS, mcv(), *TANKS]))
    site = expansion.deploy_site(agent.world, agent.ctx)[0]
    ask(agent, england(*BASE, DEPOT, seconds=520, units=[*HARVESTERS, mcv(*site), *TANKS]))

    ask(agent, england(*BASE, DEPOT, ("fact", *site), seconds=521, units=[*HARVESTERS, *TANKS]))
    ask(agent, england(*BASE, DEPOT, seconds=600, units=[*HARVESTERS, *TANKS]))

    phases = [(event["phase"], event["reason"]) for event in match_record("expansion")]
    assert phases[-2:] == [("deployed", "at_site"), ("lost", "second_yard_lost")]
    assert agent.ctx.groups.members(expansion.ESCORT) == []


@pytest.fixture(scope="module")
def refused_everywhere_match() -> WholeMatch:
    """The whole match with refusals, the game refusing five deploys after the opening: the four at the site and the
    first at home."""

    def refused_five(game: FakeRedAlert) -> None:
        game.refuse_deploy(5)

    game_config = with_ore(FakeRedAlert().game_config, LANE_ORE)
    tuning = dataclasses.replace(WITH_TARGET, upkeep=upkeep_off())
    return play_whole_match(tuning, "england", game_config, scenario=[at_seconds(300, refused_five)])


def test_after_three_retries_at_the_site_the_mcv_deploys_at_home_and_a_refusal_there_takes_the_next_place(
    refused_everywhere_match,
):
    deploys = [entry for entry in refused_everywhere_match.agent.ledger.entries if entry.order["cmd"] == "deploy"][1:]
    (deployed,) = [
        event for event in refused_everywhere_match.data_of("expansion") if event["phase"].startswith("deployed")
    ]
    yards = [b for b in refused_everywhere_match.game.own_buildings() if b["type"] == "fact"]

    assert [entry.status for entry in deploys] == [REJECTED] * 5 + [CONFIRMED]
    assert (deployed["phase"], deployed["reason"], deployed["field_center"]) == (
        "deployed_home",
        "no_room_at_site",
        None,
    )
    assert len(yards) == 2 and cells.distance((yards[1]["x"], yards[1]["y"]), (22, 48)) <= 12


# -- whole fake matches -------------------------------------------------------------------------------------------------

# Two fields in the lane east of the fake game's own spawn: the creep target takes the first, the yard the second.
LANE_ORE = [(x, y) for x in (35, 36, 37) for y in (45, 46, 47)] + [(x, y) for x in (46, 47, 48) for y in (48, 49, 50)]
FAR_LANE_FIELD = OrePatch(center=(47, 49), cells=frozenset((x, y) for x in (46, 47, 48) for y in (48, 49, 50)))
SECOND_YARD_BY_SECONDS = 600


def refused_once(game: FakeRedAlert) -> None:
    game.refuse_deploy(1)


def match_with_refusal(faction: str) -> WholeMatch:
    """A whole fake match on a map with two lane fields, the creep and the second yard on, and the game refusing the
    first deploy after the opening for want of room."""
    game_config = with_ore(FakeRedAlert().game_config, LANE_ORE)
    return play_whole_match(
        dataclasses.replace(WITH_TARGET, upkeep=upkeep_off()),
        faction,
        game_config,
        scenario=[at_seconds(300, refused_once)],
    )


@pytest.fixture(scope="module")
def refused_match() -> WholeMatch:
    return match_with_refusal("england")


def test_a_deploy_refused_for_room_is_tried_again_one_cell_farther_from_the_ore(refused_match):
    deploys = [entry for entry in refused_match.agent.ledger.entries if entry.order["cmd"] == "deploy"][1:]
    events = {event["phase"]: event for event in refused_match.data_of("expansion")}

    first_site, second_site = events["mcv_moving"]["cell"], events["deployed"]["cell"]
    ore = FAR_LANE_FIELD.nearest_cell(tuple(first_site))
    assert [entry.status for entry in deploys] == [REJECTED, CONFIRMED]
    assert events["deployed"]["field_center"] == [47, 49] and events["deployed"]["reason"] == "at_site"
    assert cells.distance(ore, tuple(second_site)) == pytest.approx(cells.distance(ore, tuple(first_site)) + 1, abs=0.5)


@pytest.fixture(scope="module", params=["england", "russia"])
def second_yard_match(request) -> WholeMatch:
    """A whole fake match for a family's start with the second yard on, as the tracked tuning file has it otherwise."""
    return play_whole_match(tuning_off(expansion=expansion_off(second_yard=True)), request.param)


def test_a_whole_fake_match_with_the_switch_on_has_two_yards_by_600_s_without_an_error(second_yard_match):
    deployed = [
        event for event in second_yard_match.data_of("expansion") if event["phase"] in ("deployed", "deployed_home")
    ]
    yards = [building for building in second_yard_match.game.own_buildings() if building["type"] == "fact"]

    assert len(second_yard_match.compute_ms) == WHOLE_MATCH_STEPS and second_yard_match.data_of("error") == []
    assert second_yard_match.compute_p95_ms <= COMPUTE_P95_MS
    assert len(yards) == 2 and deployed and deployed[0]["tick"] <= SECOND_YARD_BY_SECONDS * TICKS_PER_SECOND


def test_expansion_is_a_defined_record_kind():
    assert "expansion" in records.EVENT_KINDS


# -- the site by a field placement skipped -----------------------------------------------------------------------------

SKIPPED_ON = dataclasses.replace(WITH_TARGET, expansion=expansion_off(second_yard=True, mcv_to_skipped_fields=True))


def square_field(center: tuple[int, int]) -> OrePatch:
    x, y = center
    return OrePatch(center=center, cells=frozenset((x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)))


# A field south-east of the base, off the lane and 47 cells from the enemy's start, and one 22 cells from it.
SKIPPED_FIELD = square_field((30, 62))
NEAR_ENEMY_FIELD = square_field((55, 58))
# A gun 9 cells below the skipped field's lowest row.
GUN_BY_SKIPPED_FIELD = {"visible_enemy_buildings": [{"id": 900, "type": "gun", "x": 30, "y": 72, "hp": 100}]}


def with_skipped(tuning: Tuning, field: OrePatch, **seen):
    """An agent whose placement has skipped `field`, once it has seen the MCV of the second yard."""
    terrain = Terrain((HOME_FIELD, LANE_FIELD, field), HOME_FIELD.cells | LANE_FIELD.cells | field.cells)
    agent = expanding_agent(tuning, terrain)
    agent.ctx.skipped_fields.append(field)
    ask(agent, england(*BASE, DEPOT, seconds=500, units=[*HARVESTERS, mcv(), *TANKS], **seen))
    return agent


def test_refused_with_mcv_to_skipped_fields_on_the_second_yard_s_site_is_by_the_skipped_field(match_record):
    agent = with_skipped(SKIPPED_ON, SKIPPED_FIELD)

    site, field = expansion.deploy_site(agent.world, agent.ctx)

    (trained,) = [event for event in match_record("expansion") if event["phase"] == "mcv_trained"]
    assert agent.ctx.creep_target.patch == LANE_FIELD
    assert field == SKIPPED_FIELD and trained["field_center"] == [30, 62] and trained["cell"] == list(site)
    assert placement.ore_distance(placement.box_at("fact", site), SKIPPED_FIELD) <= 4


@pytest.mark.parametrize(
    "tuning, field, seen",
    [
        (SKIPPED_ON, NEAR_ENEMY_FIELD, {}),
        (SKIPPED_ON, SKIPPED_FIELD, GUN_BY_SKIPPED_FIELD),
        (WITH_TARGET, SKIPPED_FIELD, {}),
    ],
    ids=["within 25 cells of the enemy", "a defense within 10 cells", "switch off"],
)
def test_refused_a_skipped_field_near_the_enemy_or_a_defense_or_with_the_switch_off_leaves_today_s_site(
    tuning, field, seen
):
    agent = with_skipped(tuning, field, **seen)

    assert expansion.deploy_site(agent.world, agent.ctx)[1] == LANE_FIELD == agent.ctx.creep_target.patch


# -- the MCV first over a whole fake match ------------------------------------------------------------------------------

MCV_FIRST_ON = tuning_off(
    upkeep=dataclasses.replace(upkeep_off(), mcv_first=True),
    expansion=expansion_off(second_yard=True),
    scouting=scouting_off(scout_slots=True),
)
# What upkeep may still order while a full hold is in force: power against low power and harvesters.
HELD_BUT_ALLOWED = frozenset({"powr", "apwr", "harv"})
# The MCV comes within 60 s of the depot standing.
MCV_AFTER_DEPOT_TICKS = 60 * TICKS_PER_SECOND


@dataclasses.dataclass(frozen=True)
class UpkeepCycle:
    """One cycle of upkeep in a whole match: at its start whether the depot stood, whether the MCV's money was there
    and the Vehicle queue took it, the money and the income; after it, whether a full hold was in force and the items
    upkeep ordered."""

    tick: int
    depot: bool
    affordable: bool
    money: int
    income_per_s: float | None
    full_hold: bool
    ordered: tuple[str, ...]


@dataclasses.dataclass
class McvFirstMatch:
    """A whole fake match with the MCV first, and upkeep's cycles in it."""

    match: WholeMatch
    cycles: list[UpkeepCycle]

    def first(self, test) -> UpkeepCycle:
        return next(cycle for cycle in self.cycles if test(cycle))

    def explained(self) -> str:
        """The cycles from the depot standing to the MCV ordered, so that a failure says how the money came in."""
        depot = self.first(lambda cycle: cycle.depot).tick
        shown = [cycle for cycle in self.cycles if depot <= cycle.tick and (cycle.ordered or cycle.affordable)]
        return "\n".join(f"{cycle}" for cycle in shown[:20])


UPKEEP_INTENTS = UpkeepManager.intents


@pytest.fixture(scope="module")
def mcv_first_match() -> McvFirstMatch:
    """A whole fake Allied match with second_yard, mcv_first and the scout slots on, upkeep's cycles recorded."""
    cycles: list[UpkeepCycle] = []

    def recorded(manager, world, ctx):
        claimed = ctx.claims.is_held_by_other("Vehicle", "upkeep")
        affordable = expansion.has_mcv_money(world, ctx) and vehicle_queue_takes_one(ctx) and not claimed
        start = (world.tick, ctx.production.standing("fix") > 0, affordable, world.economy.spendable)
        intents = UPKEEP_INTENTS(manager, world, ctx)
        ordered = tuple(intent.order["item"] for intent in intents if intent.order["cmd"] in ("build", "train"))
        cycles.append(UpkeepCycle(*start, ctx.income.income_per_s, ctx.full_hold, ordered))
        return intents

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(UpkeepManager, "intents", recorded)
        match = play_whole_match(MCV_FIRST_ON)
    return McvFirstMatch(match, cycles)


def test_mcv_first_a_whole_fake_match_runs_without_an_error_within_the_compute_budget(mcv_first_match):
    match = mcv_first_match.match

    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.compute_p95_ms <= COMPUTE_P95_MS


def test_mcv_first_in_that_match_the_mcv_is_ordered_in_the_first_cycle_its_money_is_there_after_the_depot(
    mcv_first_match,
):
    depot = mcv_first_match.first(lambda cycle: cycle.depot)
    affordable = mcv_first_match.first(lambda cycle: cycle.tick >= depot.tick and cycle.affordable)
    ordered = mcv_first_match.first(lambda cycle: "mcv" in cycle.ordered)
    holds = mcv_first_match.match.data_of("economy_hold")

    explained = mcv_first_match.explained()
    assert ordered.tick == affordable.tick and ordered.tick - depot.tick <= MCV_AFTER_DEPOT_TICKS, explained
    # The fixture's income, about 100 a second, gathers the MCV's money before the 30 s of a hold are up.
    assert [(event["reason"], event["scope"]) for event in holds[:2]] == [("started", "all"), ("ordered", "all")]
    assert holds[0]["tick"] == depot.tick and holds[1]["tick"] == ordered.tick, explained


def test_mcv_first_in_that_match_nothing_but_power_and_harvesters_is_ordered_while_a_hold_is_in_force(mcv_first_match):
    held = [cycle for cycle in mcv_first_match.cycles if cycle.full_hold]
    hold_ticks = {cycle.tick for cycle in held}
    upgrades = [
        entry.sent_tick for entry in mcv_first_match.match.agent.ledger.entries if entry.order.get("item") == "jeep"
    ]

    assert held and {item for cycle in held for item in cycle.ordered} <= HELD_BUT_ALLOWED
    assert upgrades and hold_ticks.isdisjoint(upgrades)


# -- the MCV's site by a safe route -------------------------------------------------------------------------------------

SAFE_SITE = dataclasses.replace(SECOND_YARD, expansion=expansion_off(second_yard=True, mcv_safe_site=True))
SAFE_SITE_AND_SKIPPED = dataclasses.replace(
    SAFE_SITE, expansion=dataclasses.replace(SAFE_SITE.expansion, mcv_to_skipped_fields=True)
)
# The main yard's middle is (22, 49). Fields due south 18 cells off, and due north 25, 40 and 45 cells off, as measured
# from (23, 50).
MAIN_YARD = (22, 49)
FIELD_18 = square_field((23, 68))
FIELD_25 = square_field((23, 25))
FIELD_40 = square_field((23, 10))
FIELD_45 = square_field((23, 5))
# North of the main yard, out of the pillboxes' reach.
MCV_NORTH = (23, 44)


def pillbox(defense_id: int, x: int, y: int) -> dict:
    return {"id": defense_id, "type": "pbox", "x": x, "y": y, "hp": 100}


# Each 11 cells or more from FIELD_18, so that it is no defended field; together they cover the straight way to its
# place (19, 67) and the ways by both side waypoints.
PILLBOXES_ON_THE_WAY = [pillbox(900, 23, 56), pillbox(901, 13, 60)]


def safe_site_agent(*fields: OrePatch, tuning: Tuning = SAFE_SITE, skipped: tuple = (), **seen):
    """An expanding agent on a terrain of the home field and the fields given, with the safe site on, that has seen the
    base with its depot."""
    terrain = Terrain((HOME_FIELD, *fields), frozenset().union(HOME_FIELD.cells, *(field.cells for field in fields)))
    agent = expanding_agent(tuning, terrain)
    agent.ctx.skipped_fields.extend(skipped)
    ask(agent, england(*BASE, DEPOT, seconds=500, units=HARVESTERS, **seen))
    return agent


def site_events(agent, start: tuple[int, int], match_record, **seen) -> list[dict]:
    """The `site` events written as the agent takes up an MCV standing at `start`."""
    ask(agent, england(*BASE, DEPOT, seconds=505, units=[*HARVESTERS, mcv(*start)], **seen))
    return [event for event in match_record("expansion") if event["phase"] == "site"]


def test_mcv_safe_site_with_fields_at_18_and_45_cells_the_site_is_by_the_18_cell_field(match_record):
    agent = safe_site_agent(FIELD_18, FIELD_45)

    (event,) = site_events(agent, MCV_NORTH, match_record)

    assert expansion.main_yard(agent.world) == MAIN_YARD
    assert expansion.deploy_site(agent.world, agent.ctx)[1] == FIELD_18
    assert (event["field_center"], event["distance_cells"], event["reason"]) == ([23, 68], 19.03, "nearest_safe")
    assert placement.ore_distance(placement.box_at("fact", tuple(event["cell"])), FIELD_18) <= 4


@pytest.mark.parametrize("field, chosen", [(FIELD_40, None), (FIELD_25, FIELD_25)], ids=["40 cells", "25 cells"])
def test_mcv_safe_site_a_skipped_field_is_the_site_only_within_30_cells(field, chosen):
    agent = safe_site_agent(field, tuning=SAFE_SITE_AND_SKIPPED, skipped=(field,))

    site, site_field = expansion.deploy_site(agent.world, agent.ctx, MCV_NORTH)

    assert site_field == chosen
    assert chosen is not None or site == expansion.home_site(agent.world, agent.ctx)


def test_mcv_safe_site_with_the_18_cell_field_s_ways_covered_the_next_field_a_safe_route_reaches_is_the_site():
    seen = {"visible_enemy_buildings": PILLBOXES_ON_THE_WAY}
    agent = safe_site_agent(FIELD_18, FIELD_25, **seen)
    dangers = expansion.McvDangers(agent.world, agent.ctx)

    choice = expansion.safe_site(agent.world, agent.ctx, MCV_NORTH)

    assert expansion.safe_route(MCV_NORTH, (19, 67), dangers, agent.world, agent.ctx) is None
    assert not placement.is_defended(FIELD_18, agent.world, 10)
    assert (choice.field, choice.distance) == (FIELD_25, cells.distance(FIELD_25.center, MAIN_YARD))


def test_mcv_safe_site_with_no_field_a_safe_route_reaches_the_mcv_deploys_at_home(match_record):
    seen = {"visible_enemy_buildings": PILLBOXES_ON_THE_WAY}
    agent = safe_site_agent(FIELD_18, **seen)

    (event,) = site_events(agent, MCV_NORTH, match_record, **seen)

    assert (event["field_center"], event["distance_cells"], event["reason"]) == (None, None, "home_fallback")
    assert tuple(event["cell"]) == expansion.home_site(agent.world, agent.ctx)


def test_mcv_safe_site_a_field_an_own_refinery_already_serves_is_no_candidate():
    by_field_18 = ("proc", 19, 66)
    agent = safe_site_agent(FIELD_18, FIELD_25)
    ask(agent, england(*BASE, DEPOT, by_field_18, seconds=505, units=HARVESTERS))

    assert expansion.safe_site(agent.world, agent.ctx, MCV_NORTH).field == FIELD_25


def test_mcv_safe_site_a_place_a_defense_covers_is_rejected_though_the_way_up_to_it_is_clear():
    # A Tesla coil (cover 7 + 2 cells) 9 cells from the place (20, 68), which lies 2 cells from the field's ore, and 11
    # from the field; the way from the east along row 68 stays 10 off up to the place.
    seen = {"visible_enemy_buildings": [{"id": 902, "type": "tsla", "x": 11, "y": 68, "hp": 100}]}
    agent = safe_site_agent(FIELD_18, **seen)
    dangers, east = expansion.McvDangers(agent.world, agent.ctx), (35, 68)

    choice = expansion.safe_site(agent.world, agent.ctx, east)

    way = cells.line(east, (20, 68))
    assert not any(map(dangers.is_unsafe, way[:-1])) and dangers.is_unsafe(way[-1])
    assert choice.field is None and choice.site == expansion.home_site(agent.world, agent.ctx)


def test_mcv_safe_site_an_enemy_unit_seen_lately_makes_its_range_and_3_cells_unsafe_and_one_seen_long_ago_does_not():
    rifle = {"visible_enemies": [{"id": 950, "type": "e1", "x": 23, "y": 60, "hp": 100}]}
    agent = safe_site_agent(FIELD_18, **rifle)

    seen_now = expansion.McvDangers(agent.world, agent.ctx)
    ask(agent, england(*BASE, DEPOT, seconds=500 + 1501 / TICKS_PER_SECOND, units=HARVESTERS))
    seen_long_ago = expansion.McvDangers(agent.world, agent.ctx)

    # A rifleman shoots 5 cells: with the margin, 8 cells.
    assert seen_now.is_unsafe((23, 68)) and not seen_now.is_unsafe((23, 69))
    assert not seen_long_ago.is_unsafe((23, 60))


def test_mcv_safe_site_with_the_switch_off_the_site_is_today_s():
    agent = safe_site_agent(FIELD_18, FIELD_25, tuning=WITH_TARGET)

    assert expansion.deploy_site(agent.world, agent.ctx, MCV_NORTH) == expansion.deploy_site(agent.world, agent.ctx)
    assert expansion.deploy_site(agent.world, agent.ctx)[1] != FIELD_18


def on_its_way(agent, seconds: float, cell: tuple[int, int], hp: int = 100, **seen) -> list[dict]:
    """The orders of a cycle in which the MCV stands at `cell` with `hp`, with the tanks there to escort it."""
    unit = situations.unit(MCV_ID, "mcv", *cell, idle=False, hp=hp)
    return ask(agent, england(*BASE, DEPOT, seconds=seconds, units=[*HARVESTERS, unit, *TANKS], **seen))


def site_reasons(match_record) -> list[tuple[int, str, list | None]]:
    return [
        (event["tick"], event["reason"], event["field_center"])
        for event in match_record("expansion")
        if event["phase"] == "site"
    ]


def test_mcv_safe_site_an_mcv_on_its_way_that_loses_health_is_sited_again_from_its_cell_once_in_250_ticks(
    match_record,
):
    agent = safe_site_agent(FIELD_18, FIELD_25)
    on_its_way(agent, 505, MCV_NORTH)

    for seconds, hp in ((506, 80), (507, 60), (516, 40)):
        on_its_way(agent, seconds, (22, 50), hp)

    # 507 s is 25 ticks after the last choice, 516 s 250 ticks after it.
    assert site_reasons(match_record) == [
        (505 * TICKS_PER_SECOND, "nearest_safe", [23, 68]),
        (506 * TICKS_PER_SECOND, "rerouted", [23, 68]),
        (516 * TICKS_PER_SECOND, "rerouted", [23, 68]),
    ]


def test_mcv_safe_site_an_mcv_on_its_way_near_unsafe_ground_turns_with_its_escort_to_the_next_safe_field(match_record):
    agent = safe_site_agent(FIELD_18, FIELD_25)
    on_its_way(agent, 505, MCV_NORTH)

    # (22, 47) is 9 cells from the pillbox at (23, 56): out of its reach, and within 8 cells of the ground it covers.
    orders = on_its_way(agent, 506, (22, 47), visible_enemy_buildings=PILLBOXES_ON_THE_WAY)

    sites = [event for event in match_record("expansion") if event["phase"] == "site"]
    site = sites[-1]["cell"]
    hold = expansion.escort_hold(tuple(site), (22, 47), agent.world, agent.ctx.terrain)
    escort = [order for order in with_command(orders, "attack_move") if order["to"] == list(hold)]
    assert [(event["reason"], event["field_center"]) for event in sites] == [
        ("nearest_safe", [23, 68]),
        ("rerouted", [23, 25]),
    ]
    assert mcv_orders(orders) == [{"cmd": "move", "units": [MCV_ID], "to": site}] and escort


def test_mcv_safe_site_an_mcv_on_its_way_with_no_safe_field_left_goes_home_without_its_escort(match_record):
    agent = safe_site_agent(FIELD_18)
    on_its_way(agent, 505, MCV_NORTH)

    orders = on_its_way(agent, 506, (22, 47), visible_enemy_buildings=PILLBOXES_ON_THE_WAY)

    home = expansion.home_site(agent.world, agent.ctx)
    assert site_reasons(match_record)[-1] == (506 * TICKS_PER_SECOND, "home_fallback", None)
    assert mcv_orders(orders) == [{"cmd": "move", "units": [MCV_ID], "to": list(home)}]
    assert agent.ctx.groups.members(expansion.ESCORT) == []


SAFE_SITE_WAITS = dataclasses.replace(
    SAFE_SITE, expansion=dataclasses.replace(SAFE_SITE.expansion, second_mcv_waits=True)
)
HOME_FALLBACK_AT = 506


def endangered_with_no_site_left(tuning: Tuning):
    """A second MCV sent to FIELD_18 at 505 s that meets the pillboxes on its way at 506 s, with no safe site left,
    the orders of that cycle, and the place for a yard at home."""
    agent = safe_site_agent(FIELD_18, tuning=tuning)
    on_its_way(agent, 505, MCV_NORTH)
    orders = on_its_way(agent, HOME_FALLBACK_AT, (22, 47), visible_enemy_buildings=PILLBOXES_ON_THE_WAY)
    return agent, orders, expansion.home_site(agent.world, agent.ctx)


def test_second_mcv_waits_with_no_safe_site_on_its_way_it_moves_home_without_deploying_and_sets_out_at_the_next_look(
    match_record,
):
    agent, orders, home = endangered_with_no_site_left(SAFE_SITE_WAITS)

    waiting = on_its_way(agent, HOME_FALLBACK_AT + 5, home)
    # Main destroyed the pillboxes: at the next look, 10 s after the fallback, the site by FIELD_18 is safe again.
    for pillbox_record in PILLBOXES_ON_THE_WAY:
        agent.world.enemy_memory.pop(pillbox_record["id"], None)
    looked = on_its_way(agent, HOME_FALLBACK_AT + 10, home)
    sent = on_its_way(agent, HOME_FALLBACK_AT + 11, home)

    assert site_reasons(match_record)[-2:] == [
        (HOME_FALLBACK_AT * TICKS_PER_SECOND, "home_fallback", None),
        ((HOME_FALLBACK_AT + 10) * TICKS_PER_SECOND, "nearest_safe", list(FIELD_18.center)),
    ]
    assert mcv_orders(orders) == [{"cmd": "move", "units": [MCV_ID], "to": list(home)}]
    assert mcv_orders(waiting) == [] and mcv_orders(looked) == []
    assert mcv_orders(sent) == [{"cmd": "move", "units": [MCV_ID], "to": list(FIELD_18_SITE)}]
    assert all(order["cmd"] != "deploy" for order in mcv_orders([*orders, *waiting, *looked, *sent]))


def test_second_mcv_waits_with_no_site_for_90_s_it_deploys_at_home(match_record):
    agent, _, home = endangered_with_no_site_left(SAFE_SITE_WAITS)

    before = on_its_way(agent, HOME_FALLBACK_AT + 89, home)
    after = on_its_way(agent, HOME_FALLBACK_AT + 90, home)

    assert SAFE_SITE_WAITS.expansion.second_mcv_wait_seconds == 90
    assert mcv_orders(before) == [] and mcv_orders(after) == DEPLOY
    assert [reason for _, reason, _ in site_reasons(match_record)] == ["nearest_safe", "home_fallback"]


def test_second_mcv_waits_with_the_switch_off_it_deploys_at_home_at_once(match_record):
    agent, orders, home = endangered_with_no_site_left(SAFE_SITE)

    at_home = on_its_way(agent, HOME_FALLBACK_AT + 1, home)

    assert SAFE_SITE.expansion.second_mcv_waits is False
    assert (
        mcv_orders(orders) == [{"cmd": "move", "units": [MCV_ID], "to": list(home)}] and mcv_orders(at_home) == DEPLOY
    )


def test_mcv_safe_site_with_the_switch_off_an_mcv_that_loses_health_on_its_way_keeps_its_site(match_record):
    agent = safe_site_agent(FIELD_18, LANE_FIELD, tuning=WITH_TARGET)
    first = mcv_orders(on_its_way(agent, 505, MCV_NORTH))

    # 100 ticks on, past the 75 within which the same move is not sent again.
    hurt = mcv_orders(on_its_way(agent, 509, (24, 50), hp=60))

    assert site_reasons(match_record) == [] and agent.ctx.creep_target.patch == LANE_FIELD
    assert first == hurt == [{"cmd": "move", "units": [MCV_ID], "to": list(site_by_brute_force(agent))}]


# The first of the two lane fields, 13.6 cells from the main yard and served by no refinery of the fake match.
NEAR_LANE_FIELD_CENTER = [36, 46]


@pytest.fixture(scope="module", params=["england", "russia"])
def safe_site_match(request) -> WholeMatch:
    """A whole fake match for a family's start with the second yard and the safe site on, on the map with the two lane
    fields."""
    game_config = with_ore(FakeRedAlert().game_config, LANE_ORE)
    tuning = tuning_off(expansion=expansion_off(second_yard=True, mcv_safe_site=True))
    return play_whole_match(tuning, request.param, game_config)


def test_mcv_safe_site_a_whole_fake_match_deploys_the_mcv_within_30_cells_of_the_main_yard(safe_site_match):
    sites = [event for event in safe_site_match.data_of("expansion") if event["phase"] == "site"]
    deployed = [event for event in safe_site_match.data_of("expansion") if event["phase"] == "deployed"]
    yards = [building for building in safe_site_match.game.own_buildings() if building["type"] == "fact"]
    first, *more = [(yard["x"], yard["y"]) for yard in yards]

    assert len(safe_site_match.compute_ms) == WHOLE_MATCH_STEPS and safe_site_match.data_of("error") == []
    assert safe_site_match.compute_p95_ms <= COMPUTE_P95_MS
    assert [(event["reason"], event["field_center"]) for event in sites] == [("nearest_safe", NEAR_LANE_FIELD_CENTER)]
    assert deployed and len(more) == 1 and cells.distance(first, more[0]) <= expansion_off().mcv_site_max_cells


# -- the MCV's site in a new area ---------------------------------------------------------------------------------------

NEW_AREA = dataclasses.replace(SAFE_SITE, expansion=dataclasses.replace(SAFE_SITE.expansion, mcv_new_area=True))
# A field due south of the main yard, 12 cells off and nearer it than FIELD_18.
FIELD_12 = square_field((23, 62))


def new_area_site(*fields: OrePatch, tuning: Tuning = NEW_AREA, **published) -> OrePatch | None:
    """The field of the site a safe-site agent on a terrain of these fields chooses from north of the main yard, with
    the match context's published fields (asked, claim, creep target) set as given."""
    agent = safe_site_agent(*fields, tuning=tuning)
    for name, value in published.items():
        setattr(agent.ctx, name, value)
    return expansion.safe_site(agent.world, agent.ctx, MCV_NORTH).field


@pytest.mark.parametrize("tuning, field", [(NEW_AREA, FIELD_25), (SAFE_SITE, FIELD_18)], ids=["on", "off"])
def test_mcv_new_area_a_field_a_pending_refinery_is_asked_against_is_not_the_site_and_the_next_untaken_field_is(
    tuning, field
):
    assert new_area_site(FIELD_18, FIELD_25, tuning=tuning, asked_refinery_fields=[FIELD_18]) == field


def test_mcv_new_area_a_claim_field_is_not_the_site_and_the_creep_target_s_field_is_like_any_other():
    assert new_area_site(FIELD_18, FIELD_25) == FIELD_18
    assert new_area_site(FIELD_18, FIELD_25, claim_fields=[FIELD_18]) == FIELD_25
    assert new_area_site(FIELD_18, FIELD_25, creep_target=placement.CreepTarget(FIELD_18, 1)) == FIELD_18


def test_mcv_new_area_a_field_14_cells_from_every_refinery_goes_before_a_nearer_one_6_cells_from_a_refinery():
    # A refinery asked 6 cells north of the 12-cell field; every own refinery stands 15 cells or more from FIELD_25.
    asked = {"asked_refinery_cells": [(23, 56)]}

    assert new_area_site(FIELD_12, FIELD_25) == FIELD_12
    assert new_area_site(FIELD_12, FIELD_25, **asked) == FIELD_25
    assert new_area_site(FIELD_12, FIELD_25, tuning=SAFE_SITE, **asked) == FIELD_12


def test_mcv_new_area_with_every_field_taken_or_claimed_the_site_is_today_s_safe_site():
    assert new_area_site(FIELD_18, asked_refinery_fields=[FIELD_18]) == FIELD_18


@pytest.mark.parametrize("tuning, kept", [(NEW_AREA, True), (SAFE_SITE, False)], ids=["on", "off"])
def test_mcv_new_area_upkeep_publishes_the_fields_and_the_middle_of_a_refinery_asked_onto_the_map(tuning, kept):
    agent = safe_site_agent(FIELD_18, tuning=tuning)
    ready = [situations.job("Building", "proc", ready=True)]
    (order,) = with_command(ask(agent, england(*BASE, DEPOT, seconds=505, production=ready)), "place")

    ask(agent, england(*BASE, DEPOT, seconds=506))

    box = placement.box_at("proc", tuple(order["at"]))
    published = (agent.ctx.asked_refinery_fields, agent.ctx.asked_refinery_cells)
    against = list(placement.fields_against(box, agent.ctx.terrain, 2))
    assert published == ((against, [cells.nearest_cell(*box.center)]) if kept else ([], []))


# A refinery standing against FIELD_18.
BY_FIELD_18 = ("proc", 19, 66)


def on_its_way_with(agent, seconds: float, *buildings: tuple) -> list[dict]:
    """The orders of a cycle in which the MCV stands north of the main yard on its way, with the tanks there to escort
    it, and the buildings given besides the base and its depot."""
    unit = situations.unit(MCV_ID, "mcv", *MCV_NORTH, idle=False)
    return ask(agent, england(*BASE, DEPOT, *buildings, seconds=seconds, units=[*HARVESTERS, unit, *TANKS]))


@pytest.mark.parametrize(
    "tuning, resited", [(NEW_AREA, [(506 * TICKS_PER_SECOND, "taken", [23, 25])]), (SAFE_SITE, [])], ids=["on", "off"]
)
def test_mcv_new_area_a_site_whose_field_is_taken_while_the_mcv_is_on_its_way_is_replaced_with_reason_taken(
    tuning, resited, match_record
):
    agent = safe_site_agent(FIELD_18, FIELD_25, tuning=tuning)
    on_its_way_with(agent, 505)

    orders = on_its_way_with(agent, 506, BY_FIELD_18)

    first = (505 * TICKS_PER_SECOND, "nearest_safe", [23, 68])
    assert site_reasons(match_record) == [first, *resited]
    # The MCV is sent on to a new site, and keeps going with its move sent a moment ago otherwise.
    assert bool(mcv_orders(orders)) is bool(resited)


def test_mcv_new_area_the_taken_check_shares_the_250_tick_limit_of_a_choice_made_again(match_record):
    agent = safe_site_agent(FIELD_18, FIELD_25, tuning=NEW_AREA)
    on_its_way_with(agent, 505)
    on_its_way_with(agent, 506, BY_FIELD_18)
    # Now FIELD_25 is taken as well: a refinery stands against it.
    by_field_25 = ("proc", 21, 27)

    for seconds in (507, 516):
        on_its_way_with(agent, seconds, BY_FIELD_18, by_field_25)

    # With no field left, the MCV deploys at home.
    assert [reason for _, reason, _ in site_reasons(match_record)] == ["nearest_safe", "taken", "home_fallback"]
    assert site_reasons(match_record)[-1][0] == 516 * TICKS_PER_SECOND


class StoodAgainst:
    """The centers of the fields an own refinery stood against in each cycle of a whole match, by tick."""

    def __init__(self) -> None:
        self.by_tick: dict[int, set[tuple[int, int]]] = {}

    def __call__(self, agent) -> None:
        world, ctx = agent.world, agent.ctx
        taken = placement.taken_fields(world, ctx.terrain, ctx.tuning.placement, ())
        self.by_tick[world.tick] = {field.center for field in taken}


@pytest.fixture(scope="module")
def new_area_match() -> tuple[WholeMatch, StoodAgainst]:
    """A whole fake match with the second yard, the safe site and the new area on, on the map with the two lane
    fields."""
    stood = StoodAgainst()
    expansion_tuning = expansion_off(second_yard=True, mcv_safe_site=True, mcv_new_area=True)
    game_config = with_ore(FakeRedAlert().game_config, LANE_ORE)
    return play_whole_match(tuning_off(expansion=expansion_tuning), game_config=game_config, watch=stood), stood


def test_mcv_new_area_a_whole_fake_match_deploys_the_second_yard_by_a_field_no_own_refinery_stands_against(
    new_area_match,
):
    match, stood = new_area_match
    (deployed,) = [event for event in match.data_of("expansion") if event["phase"] == "deployed"]
    yards = [building for building in match.game.own_buildings() if building["type"] == "fact"]

    assert len(match.compute_ms) == WHOLE_MATCH_STEPS and match.data_of("error") == []
    assert match.compute_p95_ms <= COMPUTE_P95_MS
    assert len(yards) == 2 and deployed["field_center"] is not None
    assert tuple(deployed["field_center"]) not in stood.by_tick[deployed["tick"]]


# -- the MCV's site field reserved --------------------------------------------------------------------------------------

RESERVE = dataclasses.replace(SAFE_SITE, expansion=dataclasses.replace(SAFE_SITE.expansion, mcv_reserve_field=True))
MCV_ON_ORDER = [situations.job("Vehicle", "mcv")]


def reserved_by_cycle(agent, *states: dict) -> list:
    """The field the agent publishes as reserved in each of the cycles of the states given, in turn."""
    reserved = []
    for state in states:
        ask(agent, state)
        reserved.append(agent.ctx.reserved_field)
    return reserved


def test_mcv_reserve_the_site_is_chosen_at_the_order_and_its_field_reserved_on_order_out_and_on_its_way(match_record):
    # The MCV is ordered at 500 s: the money, the depot and one yard.
    agent = safe_site_agent(FIELD_18, FIELD_25, tuning=RESERVE)

    reserved = reserved_by_cycle(
        agent,
        england(*BASE, DEPOT, seconds=501, units=HARVESTERS, production=MCV_ON_ORDER),
        england(*BASE, DEPOT, seconds=505, units=[*HARVESTERS, mcv(*MCV_NORTH)]),
        england(*BASE, DEPOT, seconds=506, units=[*HARVESTERS, mcv(*MCV_NORTH), *TANKS]),
    )

    (site,) = [event for event in match_record("expansion") if event["phase"] == "site"]
    (trained,) = [event for event in match_record("expansion") if event["phase"] == "mcv_trained"]
    assert (site["tick"], site["reason"], site["field_center"]) == (500 * TICKS_PER_SECOND, "nearest_safe", [23, 68])
    assert trained["cell"] == site["cell"] and reserved == [FIELD_18] * 3


def test_mcv_reserve_with_the_switch_off_nothing_is_reserved_and_the_site_is_chosen_once_the_mcv_is_out(match_record):
    agent = safe_site_agent(FIELD_18, FIELD_25)

    reserved = reserved_by_cycle(
        agent,
        england(*BASE, DEPOT, seconds=501, units=HARVESTERS, production=MCV_ON_ORDER),
        england(*BASE, DEPOT, seconds=505, units=[*HARVESTERS, mcv(*MCV_NORTH)]),
        england(*BASE, DEPOT, seconds=506, units=[*HARVESTERS, mcv(*MCV_NORTH), *TANKS]),
    )

    assert reserved == [None] * 3
    assert site_reasons(match_record) == [(505 * TICKS_PER_SECOND, "nearest_safe", [23, 68])]


def test_mcv_reserve_a_site_whose_field_was_taken_before_the_mcv_came_out_is_chosen_again_and_the_reservation_moves(
    match_record,
):
    agent = safe_site_agent(FIELD_18, FIELD_25, tuning=RESERVE)

    reserved = reserved_by_cycle(
        agent,
        england(*BASE, DEPOT, BY_FIELD_18, seconds=505, units=[*HARVESTERS, mcv(*MCV_NORTH)]),
        england(*BASE, DEPOT, BY_FIELD_18, seconds=506, units=[*HARVESTERS, mcv(*MCV_NORTH), *TANKS]),
    )

    assert site_reasons(match_record) == [
        (500 * TICKS_PER_SECOND, "nearest_safe", [23, 68]),
        (505 * TICKS_PER_SECOND, "nearest_safe", [23, 25]),
    ]
    assert reserved == [FIELD_18, FIELD_25]


def test_mcv_reserve_a_site_no_safe_route_reaches_from_the_mcv_any_more_is_chosen_again(match_record):
    agent = safe_site_agent(FIELD_18, FIELD_25, tuning=RESERVE)
    seen = {"visible_enemy_buildings": PILLBOXES_ON_THE_WAY}

    ask(agent, england(*BASE, DEPOT, seconds=505, units=[*HARVESTERS, mcv(*MCV_NORTH)], **seen))

    assert [reason[1:] for reason in site_reasons(match_record)] == [
        ("nearest_safe", [23, 68]),
        ("nearest_safe", [23, 25]),
    ]


@pytest.mark.parametrize(
    "after",
    [
        england(*BASE, DEPOT, seconds=507, units=[*HARVESTERS, *TANKS]),
        england(*BASE, DEPOT, ("fact", 19, 67), seconds=507, units=[*HARVESTERS, *TANKS]),
    ],
    ids=["lost", "deployed"],
)
def test_mcv_reserve_the_reservation_ends_in_the_cycle_the_mcv_is_lost_or_deploys(after):
    agent = safe_site_agent(FIELD_18, FIELD_25, tuning=RESERVE)

    reserved = reserved_by_cycle(
        agent,
        england(*BASE, DEPOT, seconds=505, units=[*HARVESTERS, mcv(*MCV_NORTH), *TANKS]),
        after,
    )

    assert reserved == [FIELD_18, None]


def test_mcv_reserve_the_reservation_ends_once_the_mcv_is_sent_home(match_record):
    agent = safe_site_agent(FIELD_18, tuning=RESERVE)
    on_its_way(agent, 505, MCV_NORTH)
    # No safe field is left from (22, 47): the MCV is sent home.
    on_its_way(agent, 506, (22, 47), visible_enemy_buildings=PILLBOXES_ON_THE_WAY)

    on_its_way(agent, 507, (22, 48), visible_enemy_buildings=PILLBOXES_ON_THE_WAY)

    assert site_reasons(match_record)[-1][1] == "home_fallback" and agent.ctx.reserved_field is None


def switched(tuning: Tuning, *, placement_switches: dict | None = None, **expansion_switches) -> Tuning:
    """A tuning with the [placement] and [expansion] switches given set as given."""
    return dataclasses.replace(
        tuning,
        placement=dataclasses.replace(tuning.placement, **(placement_switches or {})),
        expansion=dataclasses.replace(tuning.expansion, **expansion_switches),
    )


# A field 9 cells south of the main yard, the MCV's site, and one 10 cells east of it: a refinery of the base can stand
# against either.
RESERVED_FIELD = square_field((23, 59))
EAST_FIELD = square_field((33, 50))
SPREAD_RESERVE = switched(RESERVE, placement_switches={"refinery_spread": True})
READY_REFINERY = [situations.job("Building", "proc", ready=True)]


def refinery_fields(orders: list[dict], agent) -> list[list[tuple[int, int]]]:
    """The centers of the fields each refinery placed by the orders stands against."""
    boxes = [placement.box_at("proc", tuple(order["at"])) for order in with_command(orders, "place")]
    return [[field.center for field in placement.fields_against(box, agent.ctx.terrain, 2)] for box in boxes]


def refinery_ready_on_its_way(agent, seconds: float, *units: dict) -> list[dict]:
    """The orders of a cycle in which a refinery is ready to place and the units given are out, the tanks among them."""
    return ask(
        agent, england(*BASE, DEPOT, seconds=seconds, units=[*HARVESTERS, *units, *TANKS], production=READY_REFINERY)
    )


@pytest.mark.parametrize(
    "tuning, against", [(SPREAD_RESERVE, (33, 50)), (switched(SPREAD_RESERVE, mcv_reserve_field=False), (23, 59))]
)
def test_mcv_reserve_with_refinery_spread_the_next_refinery_goes_to_another_untaken_field_never_the_mcv_s(
    tuning, against
):
    agent = safe_site_agent(RESERVED_FIELD, EAST_FIELD, tuning=tuning)
    ask(agent, england(*BASE, DEPOT, seconds=505, units=[*HARVESTERS, mcv(*MCV_NORTH), *TANKS]))

    orders = refinery_ready_on_its_way(agent, 506, mcv(*MCV_NORTH))

    assert expansion.safe_site(agent.world, agent.ctx).field == RESERVED_FIELD
    assert refinery_fields(orders, agent) == [[against]]


@pytest.mark.parametrize("tuning", [SPREAD_RESERVE, RESERVE], ids=["refinery_spread", "today's rules"])
def test_mcv_reserve_a_refinery_whose_only_field_is_reserved_waits_until_the_reservation_ends(tuning):
    agent = safe_site_agent(RESERVED_FIELD, tuning=tuning)
    ask(agent, england(*BASE, DEPOT, seconds=505, units=[*HARVESTERS, mcv(*MCV_NORTH), *TANKS]))

    # Held back at 506 s and at 508 s, place_again_after_ticks after; at 507 s it is not looked at.
    held = [refinery_ready_on_its_way(agent, seconds, mcv(*MCV_NORTH)) for seconds in (506, 507, 508)]
    # The MCV is lost: the reservation ends, and the refinery is placed once it is looked at again.
    placed = refinery_ready_on_its_way(agent, 510)

    assert [with_command(orders, "place") for orders in held] == [[], [], []]
    assert refinery_fields(placed, agent) == [[(23, 59)]]


def claims_agent(tuning: Tuning):
    """An expanding agent with refinery claims on, on a terrain of the home field and the field the MCV's site is by,
    that has ordered the MCV and whose creep keeper keeps that field as a claim field."""
    upkeep = UpkeepManager()
    claims_on = dataclasses.replace(tuning, upkeep=dataclasses.replace(tuning.upkeep, refinery_claims_reached=True))
    agent = new_agent(upkeep, plan_selector=OpeningThen(HOLD), tuning=claims_on)
    ask(agent, base_state("england", *BASE, tick=100, cash=0))
    agent.ctx.terrain = Terrain((HOME_FIELD, RESERVED_FIELD), HOME_FIELD.cells | RESERVED_FIELD.cells)
    ask(agent, england(*BASE, DEPOT, seconds=500, units=HARVESTERS))
    upkeep._far_field.claim_fields = [RESERVED_FIELD]
    return agent


@pytest.mark.parametrize("tuning, ordered", [(RESERVE, False), (SAFE_SITE, True)], ids=["on", "off"])
def test_mcv_reserve_no_claim_is_ordered_for_the_field_while_it_is_reserved(tuning, ordered):
    agent = claims_agent(tuning)

    orders = ask(agent, england(*BASE, DEPOT, seconds=505, units=[*HARVESTERS, mcv(*MCV_NORTH), *TANKS]))

    assert ("proc" in builds(orders)) is ordered
    assert agent.ctx.claim_fields == ([RESERVED_FIELD] if ordered else [])


def test_mcv_reserve_once_the_yard_deploys_the_field_is_a_claim_field_again_and_its_claim_is_ordered():
    agent = claims_agent(RESERVE)
    ask(agent, england(*BASE, DEPOT, seconds=505, units=[*HARVESTERS, mcv(*MCV_NORTH), *TANKS]))

    deployed = ask(agent, england(*BASE, DEPOT, ("fact", 19, 60), seconds=510, units=[*HARVESTERS, *TANKS]))

    assert agent.ctx.claim_fields == [RESERVED_FIELD] and "proc" in builds(deployed)


@pytest.mark.parametrize("tuning, resited", [(switched(NEW_AREA, mcv_reserve_field=True), []), (NEW_AREA, ["taken"])])
def test_mcv_reserve_a_main_base_refinery_standing_against_the_reserved_field_sites_the_mcv_again_never_for_taken(
    tuning, resited, match_record
):
    agent = safe_site_agent(FIELD_18, FIELD_25, tuning=tuning)
    on_its_way_with(agent, 505)

    on_its_way_with(agent, 506, BY_FIELD_18)

    assert [reason for _, reason, _ in site_reasons(match_record)] == ["nearest_safe", *resited]


@pytest.fixture(scope="module")
def reserve_match() -> WholeMatch:
    """A whole fake match on the map with the two lane fields, with the second yard, the safe site, the new area, the
    refinery spread and the reserved site field on."""
    tuning = tuning_off(
        placement=dataclasses.replace(placement_off(), refinery_spread=True),
        expansion=expansion_off(second_yard=True, mcv_safe_site=True, mcv_new_area=True, mcv_reserve_field=True),
    )
    return play_whole_match(tuning, game_config=with_ore(FakeRedAlert().game_config, LANE_ORE))


def test_mcv_reserve_a_whole_fake_match_deploys_the_second_yard_at_its_first_chosen_site_not_home(reserve_match):
    events = reserve_match.data_of("expansion")
    first_site = next(event for event in events if event["phase"] == "site")
    deployed = [event for event in events if event["phase"].startswith("deployed")]

    assert len(reserve_match.compute_ms) == WHOLE_MATCH_STEPS and reserve_match.data_of("error") == []
    assert reserve_match.compute_p95_ms <= COMPUTE_P95_MS
    assert first_site["field_center"] is not None
    assert [(event["phase"], event["field_center"], event["cell"]) for event in deployed] == [
        ("deployed", first_site["field_center"], first_site["cell"])
    ]


# -- the MCV short of its escort ----------------------------------------------------------------------------------------

SHORT_ESCORT = switched(WITH_TARGET, mcv_short_escort=True)
SHORT_ESCORT_SAFE_SITE = switched(SAFE_SITE, mcv_short_escort=True)
# Two rocket soldiers of main, worth 600 in a fight.
ROCKETS = [situations.unit(520 + number, "e3", 24 + number, 58) for number in range(2)]
# Each more than the 14 cells of mcv_enemy_building_cells from FIELD_18's site (20, 68), whose yard's corner is
# (19, 67); together they cover every way to it from north of the yard.
PILLBOXES_OFF_THE_SITE = [pillbox(900, 23, 54), pillbox(901, 12, 56)]


def moving_reasons(match_record) -> list[str]:
    return [event["reason"] for event in match_record("expansion") if event["phase"] == "mcv_moving"]


def test_mcv_short_escort_an_mcv_with_600_of_1500_waiting_500_ticks_sets_out_with_those_units(match_record):
    agent = expanding_agent(SHORT_ESCORT)
    units = [*HARVESTERS, mcv(), *ROCKETS]

    # 499 ticks of waiting, then the 500th.
    waited = [ask(agent, england(*BASE, DEPOT, seconds=seconds, units=units)) for seconds in (500, 510, 519.96)]
    orders = ask(agent, england(*BASE, DEPOT, seconds=520, units=units))

    site = site_by_brute_force(agent)
    (escort,) = with_command(orders, "attack_move")
    hold = expansion.escort_hold(site, MCV_CELL, agent.world, TERRAIN)
    assert strength([agent.world.combat_units[unit["id"]] for unit in ROCKETS], agent.world) == 600
    assert [mcv_orders(cycle) for cycle in waited] == [[], [], []]
    assert mcv_orders(orders) == [{"cmd": "move", "units": [MCV_ID], "to": list(site)}]
    assert sorted(escort["units"]) == [520, 521] and escort["to"] == list(hold)
    assert moving_reasons(match_record) == ["escort_short_timeout"]


@pytest.mark.parametrize("seen, sets_out", [({}, True), ({"visible_enemy_buildings": PILLBOXES_OFF_THE_SITE}, False)])
def test_mcv_short_escort_with_no_escort_it_sets_out_alone_after_500_ticks_only_on_a_safe_route(
    seen, sets_out, match_record
):
    agent = safe_site_agent(FIELD_18, tuning=SHORT_ESCORT_SAFE_SITE)
    units = [*HARVESTERS, mcv(*MCV_NORTH)]
    ask(agent, england(*BASE, DEPOT, seconds=505, units=units))
    ask(agent, england(*BASE, DEPOT, seconds=515, units=units, **seen))

    orders = ask(agent, england(*BASE, DEPOT, seconds=525, units=units, **seen))

    waiting = [event["reason"] for event in match_record("expansion") if event["phase"] == "mcv_waiting"]
    assert waiting == ["escort_short"]
    assert bool(mcv_orders(orders)) is sets_out and with_command(orders, "attack_move") == []
    assert moving_reasons(match_record) == (["escort_short_timeout"] if sets_out else [])


def test_mcv_short_escort_without_mcv_safe_site_it_sets_out_alone_with_no_defense_or_army_near_the_site(match_record):
    agent = expanding_agent(SHORT_ESCORT)
    units = [*HARVESTERS, mcv()]

    waited = [ask(agent, england(*BASE, DEPOT, seconds=seconds, units=units)) for seconds in (500, 519.96)]
    orders = ask(agent, england(*BASE, DEPOT, seconds=520, units=units))

    assert [mcv_orders(cycle) for cycle in waited] == [[], []]
    assert mcv_orders(orders) == [{"cmd": "move", "units": [MCV_ID], "to": list(site_by_brute_force(agent))}]
    assert moving_reasons(match_record) == ["escort_short_timeout"]


def test_mcv_short_escort_a_wait_for_a_defense_near_the_site_is_unchanged(match_record):
    agent = expanding_agent(SHORT_ESCORT)
    seen = {"visible_enemy_buildings": [DEFENSE_NEAR_SITE]}
    units = [*HARVESTERS, mcv(), *ROCKETS]

    waited = [ask(agent, england(*BASE, DEPOT, seconds=seconds, units=units, **seen)) for seconds in (500, 521, 541)]

    assert [mcv_orders(orders) for orders in waited] == [[], [], []]
    phases = [(event["phase"], event["reason"]) for event in match_record("expansion")]
    assert phases == [("mcv_trained", "second_yard"), ("mcv_waiting", "defense_near_site")]


def threat_at(tick: int):
    """A base defense that reports a major threat in the cycle of `tick` alone."""

    def threat(world, ctx):
        ctx.threat_level = MAJOR_THREAT if world.tick == tick else NO_THREAT
        return []

    return threat


def test_mcv_short_escort_another_reason_to_wait_starts_the_500_ticks_again():
    threat_tick = 516 * TICKS_PER_SECOND
    agent = expanding_agent(SHORT_ESCORT, TERRAIN, ScriptedManager("base_defense", threat_at(threat_tick)))
    units = [*HARVESTERS, mcv(), *ROCKETS]
    for seconds in (500, 515, 516):
        ask(agent, england(*BASE, DEPOT, seconds=seconds, units=units))
    # 475 ticks after the threat, still waiting; 500 ticks after it, on its way.
    still = ask(agent, england(*BASE, DEPOT, seconds=535, units=units))

    going = ask(agent, england(*BASE, DEPOT, seconds=536, units=units))

    assert mcv_orders(still) == [] and mcv_orders(going)


def test_mcv_short_escort_with_the_switch_off_an_mcv_short_of_its_escort_keeps_waiting(match_record):
    agent = expanding_agent(WITH_TARGET)
    units = [*HARVESTERS, mcv(), *ROCKETS]

    waited = [ask(agent, england(*BASE, DEPOT, seconds=seconds, units=units)) for seconds in (500, 520, 540)]

    assert [mcv_orders(orders) for orders in waited] == [[], [], []] and moving_reasons(match_record) == []


# An escort no army of the fake game makes up, so that the MCV is always short of it.
ESCORT_NEVER_MADE_UP = 1_000_000


@pytest.fixture(scope="module")
def short_escort_match() -> WholeMatch:
    """A whole fake match on the map with the two lane fields, with the creep to the next field, the second yard and
    the MCV short of its escort setting out on, the escort it waits for never made up."""
    expansion_tuning = expansion_off(second_yard=True, mcv_short_escort=True, mcv_escort_strength=ESCORT_NEVER_MADE_UP)
    tuning = dataclasses.replace(WITH_TARGET, upkeep=upkeep_off(), expansion=expansion_tuning)
    return play_whole_match(tuning, game_config=with_ore(FakeRedAlert().game_config, LANE_ORE))


def test_mcv_short_escort_a_whole_fake_match_runs_without_an_error_and_the_mcv_sets_out_500_ticks_after_waiting(
    short_escort_match,
):
    events = {event["phase"]: event for event in short_escort_match.data_of("expansion")}

    assert len(short_escort_match.compute_ms) == WHOLE_MATCH_STEPS and short_escort_match.data_of("error") == []
    assert short_escort_match.compute_p95_ms <= COMPUTE_P95_MS
    assert (events["mcv_waiting"]["reason"], events["mcv_moving"]["reason"]) == ("escort_short", "escort_short_timeout")
    assert events["mcv_moving"]["tick"] - events["mcv_waiting"]["tick"] == expansion_off().short_escort_wait_ticks
    assert events["deployed"]["reason"] == "at_site"


# -- a further MCV, up to three yards ------------------------------------------------------------------------------------

REPEAT = switched(SAFE_SITE, mcv_repeat=True)
# A second yard 24 cells south of the main yard, its middle (24, 72), and a field of 49 ore cells, that a yard lasts by,
# 20 cells south of that middle and 42 from the main yard's: no yard's refinery can stand against it, and the home field
# is taken by the base's two.
SECOND_YARD_SOUTH = ("fact", 23, 70)
FIELD_20_FROM_SECOND = OrePatch(
    center=(24, 92), cells=frozenset((24 + dx, 92 + dy) for dx in range(-3, 4) for dy in range(-3, 4))
)
THIRD_YARD = ("fact", 60, 20)


class FixedIncome(IncomeEstimate):
    """The income estimate of a match, but for the income a second, which a test sets."""

    def __init__(self, tuning: Tuning, income_per_s: float | None) -> None:
        super().__init__(tuning)
        self.fixed = self.income_per_s = income_per_s

    def update(self, world) -> None:
        super().update(world)
        self.income_per_s = self.fixed


# An income of 100 a second allows three yards.
THREE_YARDS_INCOME = 100


def repeat_agent(*fields: OrePatch, tuning: Tuning = REPEAT, more=(), income: float | None = THREE_YARDS_INCOME):
    """An expanding agent on a terrain of the home field and the fields given, with the other managers given and an
    income of `income` a second."""
    terrain = Terrain((HOME_FIELD, *fields), frozenset().union(HOME_FIELD.cells, *(field.cells for field in fields)))
    agent = expanding_agent(tuning, terrain, *more)
    agent.ctx.income = FixedIncome(tuning, income)
    return agent


def with_seconds_left(state: dict, seconds_left: float) -> dict:
    state["time"]["remaining_seconds"] = seconds_left
    return state


def test_mcv_repeat_with_2_yards_and_every_standable_field_taken_an_mcv_is_wanted_for_the_field_20_cells_off():
    agent = repeat_agent(FIELD_20_FROM_SECOND)

    orders = ask(agent, england(*BASE, DEPOT, SECOND_YARD_SOUTH, seconds=600, units=HARVESTERS))

    choice = expansion.repeat_site(agent.world, agent.ctx, expansion.main_yard(agent.world))
    assert ("mcv", 1) in trains(orders)
    assert (agent.ctx.repeat_mcv_field, agent.ctx.repeat_mcv_site) == (FIELD_20_FROM_SECOND, choice.site)
    # Measured from the second yard, the yard nearest the field.
    assert (choice.field, choice.distance) == (FIELD_20_FROM_SECOND, cells.distance((24, 92), SECOND_YARD_MIDDLE))


@pytest.mark.parametrize(
    "income, more_buildings, seconds_left, tuning, more",
    [
        (79, (), 600, REPEAT, ()),
        (THREE_YARDS_INCOME, (THIRD_YARD,), 600, REPEAT, ()),
        (THREE_YARDS_INCOME, (), 150, REPEAT, ()),
        (THREE_YARDS_INCOME, (), 600, REPEAT, (ScriptedManager("base_defense", major_threat),)),
        (THREE_YARDS_INCOME, (), 600, SAFE_SITE, ()),
    ],
    ids=["income for 2 yards", "3 yards", "150 s left", "a major threat", "switch off"],
)
def test_mcv_repeat_no_further_mcv_is_wanted(income, more_buildings, seconds_left, tuning, more):
    agent = repeat_agent(FIELD_20_FROM_SECOND, tuning=tuning, more=more, income=income)
    state = england(*BASE, DEPOT, SECOND_YARD_SOUTH, *more_buildings, seconds=600, units=HARVESTERS)

    orders = ask(agent, with_seconds_left(state, seconds_left))

    assert ("mcv", 1) not in trains(orders) and agent.ctx.repeat_mcv_site is None


def test_mcv_repeat_upkeep_keeps_the_fields_refineries_are_asked_against_with_the_switch_on():
    agent = repeat_agent(RESERVED_FIELD)
    ready = [situations.job("Building", "proc", ready=True)]
    (order,) = with_command(ask(agent, england(*BASE, DEPOT, seconds=505, production=ready)), "place")

    ask(agent, england(*BASE, DEPOT, seconds=506))

    box = placement.box_at("proc", tuple(order["at"]))
    assert agent.ctx.asked_refinery_fields == list(placement.fields_against(box, agent.ctx.terrain, 2)) != []


# The cell an MCV deploys on by FIELD_20_FROM_SECOND, one down and one right of its yard box, and the middle of the
# second yard.
FIELD_20_PLACE = [23, 86]
SECOND_YARD_MIDDLE = (23, 71)
# A row of riflemen across the way from the main base to the second yard, 28 cells and more from the field's place:
# every way to that place from north of the main yard crosses their range and the margin, none from the second yard.
RIFLES_ACROSS = [
    {"id": 950 + number, "type": "e1", "x": x, "y": 58, "hp": 100} for number, x in enumerate(range(0, 96, 12))
]
# Riflemen are danger for 300 ticks after they were last seen.
REPEAT_SHORT_MEMORY = switched(REPEAT, mcv_enemy_seen_ticks=300)


def repeat_state(seconds: float, *units: dict, more_buildings=(), **seen) -> dict:
    """The base with its depot and the second yard south of it at a time, with the harvesters and the units given."""
    buildings = (*BASE, DEPOT, SECOND_YARD_SOUTH, *more_buildings)
    return england(*buildings, seconds=seconds, units=[*HARVESTERS, *units], **seen)


def expansion_events(match_record) -> list[tuple[str, str, list | None]]:
    """The MCV's `expansion` events, the yards the income allows left out."""
    events = match_record("expansion")
    return [
        (event["phase"], event["reason"], event["field_center"]) for event in events if event["phase"] != "yard_cap"
    ]


def test_mcv_repeat_a_further_mcv_out_is_sited_from_its_cell_with_reason_repeat_and_goes_with_its_escort(match_record):
    agent = repeat_agent(FIELD_20_FROM_SECOND)
    ask(agent, repeat_state(600))

    orders = ask(agent, repeat_state(605, mcv(*MCV_NORTH), *TANKS))

    assert expansion_events(match_record) == [
        ("site", "repeat", [24, 92]),
        ("mcv_trained", "repeat", [24, 92]),
        ("mcv_moving", "escorted", [24, 92]),
    ]
    assert mcv_orders(orders) == [{"cmd": "move", "units": [MCV_ID], "to": FIELD_20_PLACE}]


def test_mcv_repeat_with_mcv_reserve_field_the_further_mcv_s_site_is_chosen_and_reserved_at_its_order(match_record):
    agent = repeat_agent(FIELD_20_FROM_SECOND, tuning=switched(REPEAT, mcv_reserve_field=True))
    ask(agent, repeat_state(600))

    ask(agent, england(*BASE, DEPOT, SECOND_YARD_SOUTH, seconds=601, units=HARVESTERS, production=MCV_ON_ORDER))

    assert site_reasons(match_record) == [(600 * TICKS_PER_SECOND, "repeat", [24, 92])]
    assert agent.ctx.reserved_field == FIELD_20_FROM_SECOND


def test_mcv_repeat_a_site_safe_from_the_second_yard_but_not_from_the_mcv_is_not_taken_until_a_route_from_it_is_safe(
    match_record,
):
    agent = repeat_agent(FIELD_20_FROM_SECOND, tuning=REPEAT_SHORT_MEMORY)
    ask(agent, repeat_state(600))
    at_home = [mcv(*MCV_NORTH), *TANKS]
    blocked = ask(agent, repeat_state(605, *at_home, visible_enemies=RIFLES_ACROSS))
    from_second = expansion.repeat_site(agent.world, agent.ctx, SECOND_YARD_MIDDLE)
    from_mcv = expansion.repeat_site(agent.world, agent.ctx, MCV_NORTH)

    # 250 ticks on the riflemen are danger still; 500 ticks on they are not, and the site is found.
    still = ask(agent, repeat_state(615, *at_home))
    found = ask(agent, repeat_state(625, *at_home))
    going = ask(agent, repeat_state(626, *at_home))

    assert (from_second.field, from_mcv) == (FIELD_20_FROM_SECOND, None)
    assert [mcv_orders(orders) for orders in (blocked, still, found)] == [[], [], []]
    assert mcv_orders(going) == [{"cmd": "move", "units": [MCV_ID], "to": FIELD_20_PLACE}]
    assert site_reasons(match_record) == [
        (605 * TICKS_PER_SECOND, "repeat_no_site", None),
        (625 * TICKS_PER_SECOND, "repeat", [24, 92]),
    ]
    assert ("mcv_waiting", "repeat_no_site", None) in expansion_events(match_record)


def test_mcv_repeat_a_further_mcv_without_a_site_waits_and_then_deploys_by_the_second_yard_never_at_home(match_record):
    agent = repeat_agent()
    waited = [ask(agent, repeat_state(seconds, mcv(*MCV_NORTH))) for seconds in (605, 635)]

    # mcv_wait_ticks after it came out.
    (move,) = mcv_orders(ask(agent, repeat_state(665, mcv(*MCV_NORTH))))
    deploy = mcv_orders(ask(agent, repeat_state(670, mcv(*move["to"]))))
    ask(agent, repeat_state(675, more_buildings=(("fact", *move["to"]),)))

    assert [mcv_orders(orders) for orders in waited] == [[], []]
    assert cells.distance(tuple(move["to"]), (23, 70)) <= placement_off().ring_max_cells
    assert cells.distance(tuple(move["to"]), YARD[1:]) > placement_off().ring_max_cells
    assert deploy == [{"cmd": "deploy", "units": [MCV_ID]}]
    assert expansion_events(match_record) == [
        ("site", "repeat_no_site", None),
        ("mcv_trained", "repeat", None),
        ("mcv_waiting", "repeat_no_site", None),
        ("deployed_home", "waited_too_long", None),
    ]


def test_mcv_repeat_with_no_safe_route_to_the_second_yard_a_further_mcv_deploys_where_it_stands():
    agent = repeat_agent()
    ask(agent, repeat_state(605, mcv(*MCV_NORTH)))

    orders = mcv_orders(ask(agent, repeat_state(665, mcv(*MCV_NORTH), visible_enemies=RIFLES_ACROSS)))

    (order,) = orders
    to = tuple(order.get("to", MCV_NORTH))
    assert cells.distance(to, MCV_NORTH) <= placement_off().ring_max_cells
    assert cells.distance(to, (23, 70)) > placement_off().ring_max_cells


def test_mcv_repeat_a_further_mcv_left_without_a_safe_site_on_its_way_waits_at_home_for_one(match_record):
    agent = repeat_agent(FIELD_20_FROM_SECOND)
    ask(agent, repeat_state(600))
    ask(agent, repeat_state(605, situations.unit(MCV_ID, "mcv", *MCV_NORTH, idle=False), *TANKS))

    # (22, 47) is within 8 cells of the riflemen's range: sited again from there, and no way to a site is safe.
    unit = situations.unit(MCV_ID, "mcv", 22, 47, idle=False)
    orders = ask(agent, repeat_state(606, unit, *TANKS, visible_enemies=RIFLES_ACROSS))

    home = expansion.home_site(agent.world, agent.ctx)
    assert site_reasons(match_record)[-1] == (606 * TICKS_PER_SECOND, "repeat_no_site", None)
    assert mcv_orders(orders) == [{"cmd": "move", "units": [MCV_ID], "to": list(home)}]
    assert agent.ctx.groups.members(expansion.ESCORT) == []
    assert [phase for phase, _, _ in expansion_events(match_record)][-1] == "mcv_waiting"


def test_mcv_repeat_every_yard_the_rule_deployed_is_followed_and_each_loss_written(match_record):
    agent = repeat_agent()
    second, third = ("fact", 23, 70), ("fact", 50, 80)
    ask(agent, england(*BASE, DEPOT, seconds=500, units=[*HARVESTERS, mcv(*MCV_NORTH)]))
    ask(agent, england(*BASE, DEPOT, second, seconds=501, units=HARVESTERS))
    ask(agent, england(*BASE, DEPOT, second, seconds=600, units=[*HARVESTERS, mcv(*MCV_NORTH)]))
    ask(agent, england(*BASE, DEPOT, second, third, seconds=601, units=HARVESTERS))

    ask(agent, england(*BASE, DEPOT, seconds=700, units=HARVESTERS))

    kept = [event for event in expansion_events(match_record) if event[0] not in ("site", "second_mcv_blocked")]
    events = [(phase, reason) for phase, reason, _ in kept]
    trained = [reason for phase, reason in events if phase == "mcv_trained"]
    assert trained == ["second_yard", "repeat"]
    assert events[-2:] == [("lost", "second_yard_lost"), ("lost", "second_yard_lost")]


# Six fields round the fake game's own spawn besides its own, as the economy's growth is measured on, and two fields of
# 49 ore cells a yard lasts by on the base's own side, 32 cells south of the spawn and 36 north of it.
SIX_FIELD_CENTERS = ((36, 46), (9, 50), (34, 61), (13, 31), (22, 61), (34, 33))
FAR_LASTING_CENTERS = ((22, 80), (22, 12))
SIX_FIELDS_ORE = sorted(
    [cell for center in SIX_FIELD_CENTERS for cell in square_field(center).cells]
    + [(x + dx, y + dy) for x, y in FAR_LASTING_CENTERS for dx in range(-3, 4) for dy in range(-3, 4)]
)


@pytest.fixture(scope="module")
def repeat_match() -> WholeMatch:
    """A whole fake match on the map with six fields round the own spawn, with refinery growth and spread, and the
    second yard with the safe site, the new area, the reserved site field, the short escort and further MCVs on; the
    yards after the first unfold as the engine unfolds them, from the cell one up and one left of the MCV's."""
    expansion_tuning = expansion_off(
        second_yard=True,
        mcv_safe_site=True,
        mcv_new_area=True,
        mcv_reserve_field=True,
        mcv_short_escort=True,
        mcv_repeat=True,
    )
    tuning = tuning_off(
        upkeep=dataclasses.replace(upkeep_off(), refinery_growth=True),
        placement=dataclasses.replace(placement_off(), refinery_spread=True),
        expansion=expansion_tuning,
    )
    return play_whole_match(
        tuning,
        game_config=with_ore(FakeRedAlert().game_config, SIX_FIELDS_ORE),
        scenario=[at_seconds(60, FakeRedAlert.unfold_as_the_engine)],
    )


def of_phase(events: list[dict], phase: str) -> list[dict]:
    """The `expansion` events of a phase, in order."""
    return [event for event in events if event["phase"] == phase]


def deploys(events: list[dict]) -> set[tuple[str, str]]:
    """The phase and reason of each deploy, at the site or at home."""
    return {(event["phase"], event["reason"]) for event in events if event["phase"].startswith("deployed")}


def sited_fields(events: list[dict]) -> set[tuple[int, int]]:
    """The centers of the fields the `site` events chose."""
    return {tuple(event["field_center"]) for event in of_phase(events, "site") if event["field_center"]}


def own_yards(match: WholeMatch) -> list[dict]:
    """The own construction yards that stand at the end of a whole match."""
    return [building for building in match.game.own_buildings() if building["type"] == "fact"]


def caps_within(events: list[dict], least: int, most: int) -> bool:
    """Whether there is a `yard_cap` event and every one's cap lies from `least` to `most`."""
    caps = [event["cap"] for event in of_phase(events, "yard_cap")]
    return bool(caps) and all(least <= cap <= most for cap in caps)


def assert_whole_match_kept_its_bounds(match: WholeMatch) -> None:
    """Every step of the whole match was played, no error was written and the compute stayed within the bound."""
    assert len(match.compute_ms) == WHOLE_MATCH_STEPS
    assert match.data_of("error") == []
    assert match.compute_p95_ms <= COMPUTE_P95_MS


def test_mcv_repeat_a_whole_fake_match_runs_without_an_error_and_its_mcvs_open_far_lasting_fields(repeat_match):
    events = repeat_match.data_of("expansion")
    trained = [event["reason"] for event in of_phase(events, "mcv_trained")]

    assert_whole_match_kept_its_bounds(repeat_match)
    assert trained[:2] == ["second_yard", "repeat"]
    assert deploys(events) == {("deployed", "at_site")}
    assert sited_fields(events) == set(FAR_LASTING_CENTERS)
    assert len(own_yards(repeat_match)) >= 3
    assert caps_within(events, 1, 6)


# -- a deploy left without effect --------------------------------------------------------------------------------------

DEPLOY = [{"cmd": "deploy", "units": [MCV_ID]}]


def at_its_site(agent) -> tuple[int, int]:
    """The site of an MCV taken up and sent out with its escort at 500 s."""
    ask(agent, england(*BASE, DEPOT, seconds=500, units=[*HARVESTERS, mcv(), *TANKS]))
    return expansion.deploy_site(agent.world, agent.ctx)[0]


def deploys_from(agent, cell: tuple[int, int], *seconds: float, idle: bool = True) -> list[list[dict]]:
    """The MCV's orders in the cycles at these times, the MCV standing on a cell all along."""
    units = [*HARVESTERS, mcv(*cell, idle=idle), *TANKS]
    return [mcv_orders(ask(agent, england(*BASE, DEPOT, seconds=moment, units=units))) for moment in seconds]


def refusals(match_record) -> list[tuple[list[int], str]]:
    return [
        (event["cell"], event["reason"]) for event in match_record("expansion") if event["phase"] == "deploy_refused"
    ]


def next_site(site: tuple[int, int], farther: int) -> list[int]:
    """The place `farther` cells farther from the lane field's ore than a site."""
    ore = LANE_FIELD.nearest_cell(site)
    return list(cells.toward(ore, site, cells.distance(ore, site) + farther))


def test_deploy_refused_an_mcv_idle_on_its_site_75_ticks_after_one_resend_moves_on_to_the_next_site(match_record):
    agent = expanding_agent(WITH_TARGET)
    site = at_its_site(agent)

    sent = deploys_from(agent, site, 520, 523, 526)

    moved = [{"cmd": "move", "units": [MCV_ID], "to": next_site(site, 2)}]
    assert sent == [DEPLOY, DEPLOY, moved] and cells.distance(site, tuple(moved[0]["to"])) > expansion.ARRIVED_CELLS
    assert refusals(match_record) == [(list(site), "ignored")]


def test_deploy_refused_after_2_deploys_left_without_effect_the_mcv_deploys_at_home(match_record):
    agent = expanding_agent(WITH_TARGET)
    site = at_its_site(agent)
    deploys_from(agent, site, 520, 523, 526)
    second = tuple(next_site(site, 2))

    sent_home = deploys_from(agent, second, 530, 533, 536)
    home = expansion.home_site(agent.world, agent.ctx)
    at_home = deploys_from(agent, home, 540)

    assert sent_home == [DEPLOY, DEPLOY, [{"cmd": "move", "units": [MCV_ID], "to": list(home)}]]
    assert at_home == [DEPLOY] and refusals(match_record) == [(list(site), "ignored"), (list(second), "ignored")]


def test_deploy_refused_a_deploy_is_sent_again_at_most_twice_whatever_the_mcv_does(match_record):
    agent = expanding_agent(WITH_TARGET)
    site = at_its_site(agent)

    sent = deploys_from(agent, site, 520, 523, 526, 529, idle=False)

    assert sent[:3] == [DEPLOY] * 3 and sent[3] == [{"cmd": "move", "units": [MCV_ID], "to": next_site(site, 2)}]
    assert refusals(match_record) == [(list(site), "ignored")]


def test_deploy_refused_a_deploy_the_game_refuses_is_written_with_the_reason_rejected(refused_match):
    refused = [event for event in refused_match.data_of("expansion") if event["phase"] == "deploy_refused"]

    assert [(event["reason"], event["field_center"]) for event in refused] == [("rejected", [47, 49])]


# -- the MCV's yard box ------------------------------------------------------------------------------------------------


def test_mcv_footprint_the_yard_box_of_a_site_is_its_3_by_4_footprint_from_one_cell_up_and_one_left():
    assert expansion.yard_box((69, 51)) == placement.Box(68, 50, 3, 4)


def test_mcv_footprint_upkeep_keeps_the_yard_box_while_the_mcv_lives_with_a_site_and_not_once_it_deployed():
    upkeep = UpkeepManager()
    agent = new_agent(upkeep, plan_selector=OpeningThen(HOLD), tuning=WITH_TARGET)
    ask(agent, base_state("england", *BASE, tick=100, cash=0))
    agent.ctx.terrain = TERRAIN
    before = upkeep._forward.site_box(agent.world)
    site = at_its_site(agent)
    kept = upkeep._forward.site_box(agent.world)

    ask(agent, england(*BASE, DEPOT, ("fact", site[0] - 1, site[1] - 1), seconds=501, units=[*HARVESTERS, *TANKS]))

    assert before is None and kept == expansion.yard_box(site)
    assert upkeep._forward.site_box(agent.world) is None


def test_mcv_footprint_a_site_whose_yard_box_holds_a_standing_building_is_passed_over():
    agent = expanding_agent(WITH_TARGET)
    site = at_its_site(agent)
    # The box's bottom-left cell, which the footprint from the site itself would leave out.
    blocking = (site[0] - 1, site[1] + 2)

    ask(agent, england(*BASE, DEPOT, ("pbox", *blocking), seconds=501, units=[*HARVESTERS, mcv(), *TANKS]))
    again = expansion.deploy_site(agent.world, agent.ctx)[0]

    assert expansion.yard_box(site).holds_any({blocking}) and not placement.box_at("fact", site).holds_any({blocking})
    assert again != site and not expansion.yard_box(again).grown(1).holds_any({blocking})


# -- the escort kept off the yard's ground ------------------------------------------------------------------------------


def escort_targets(orders: list[dict]) -> dict[int, list[tuple[str, tuple[int, int]]]]:
    """Escort tank id -> the (command, cell) of each order of the cycle that names it."""
    named: dict[int, list[tuple[str, tuple[int, int]]]] = {tank["id"]: [] for tank in TANKS}
    for order in orders:
        for unit_id in order.get("units", ()):
            if unit_id in named and "to" in order:
                named[unit_id].append((order["cmd"], tuple(order["to"])))
    return named


def on_cell(tank: dict, cell: tuple[int, int]) -> dict:
    """An escort tank standing on a cell."""
    return {**tank, "x": cell[0], "y": cell[1]}


def faces_the_mcv(cell: tuple[int, int], site: tuple[int, int]) -> bool:
    """Whether a cell lies on the side of the yard's ground the MCV came from (from MCV_CELL, south-west of the site)."""
    center = expansion.clear_area(site).center
    return (cell[0] - center[0]) * (MCV_CELL[0] - center[0]) + (cell[1] - center[1]) * (MCV_CELL[1] - center[1]) > 0


def test_escort_clear_with_the_mcv_3_cells_from_its_site_no_escort_order_targets_the_grown_box():
    agent = expanding_agent(WITH_TARGET)
    site = at_its_site(agent)
    near = cells.toward(site, MCV_CELL, 3)
    tanks = [on_cell(TANKS[0], site), *TANKS[1:]]

    orders = ask(agent, england(*BASE, DEPOT, seconds=505, units=[*HARVESTERS, mcv(*near, idle=False), *tanks]))

    area = expansion.clear_area(site)
    targets = [target for named in escort_targets(orders).values() for target in named]
    assert area == placement.Box(site[0] - 3, site[1] - 3, 7, 8) and cells.distance(near, site) == pytest.approx(3, 0.2)
    assert mcv_orders(orders) == [{"cmd": "move", "units": [MCV_ID], "to": list(site)}]
    assert targets and not any(area.holds_any({cell}) for _, cell in targets)
    assert {command for command, _ in targets} == {"move", "attack_move"}


def test_escort_clear_an_escort_tank_on_the_site_cell_is_moved_out_on_the_mcv_s_side_while_it_deploys():
    agent = expanding_agent(WITH_TARGET)
    site = at_its_site(agent)
    tanks = [on_cell(TANKS[0], site), *TANKS[1:]]
    units = [*HARVESTERS, mcv(*site), *tanks]

    deploying = ask(agent, england(*BASE, DEPOT, seconds=520, units=units))
    waiting = ask(agent, england(*BASE, DEPOT, seconds=521, units=units))

    area = expansion.clear_area(site)
    for orders in (deploying, waiting):
        (moved,) = escort_targets(orders)[TANKS[0]["id"]]
        assert moved[0] == "move" and not area.holds_any({moved[1]}) and faces_the_mcv(moved[1], site)
        assert cells.distance(moved[1], site) <= 6
    assert mcv_orders(deploying) == DEPLOY and mcv_orders(waiting) == []


def test_escort_clear_with_the_escort_moved_out_a_fake_game_deploy_succeeds():
    agent = expanding_agent(WITH_TARGET)
    site = at_its_site(agent)
    tanks = [on_cell(TANKS[0], site), *TANKS[1:]]
    orders = ask(agent, england(*BASE, DEPOT, seconds=520, units=[*HARVESTERS, mcv(*site), *tanks]))
    (moved,) = escort_targets(orders)[TANKS[0]["id"]]
    game = FakeRedAlert()
    game.unfold_as_the_engine()
    game.units_block_unfold()
    mcv_id, tank_id = game.add_own("mcv", *site), game.add_own("2tnk", *site)

    game.play_action(fake_batch({"cmd": "deploy", "units": [mcv_id]}))
    game.step()
    blocked = yards_at(game, site)
    game.play_action(fake_batch({"cmd": "move", "units": [tank_id], "to": list(moved[1])}))
    for _ in range(20):
        game.step()
    game.play_action(fake_batch({"cmd": "deploy", "units": [mcv_id]}))
    game.step()

    assert mcv_orders(orders) == DEPLOY and blocked == []
    assert yards_at(game, site) == [(site[0] - 1, site[1] - 1)]


def fake_batch(*orders: dict) -> dict:
    """The action object for a batch of orders to the fake game."""
    return {"type": "orders", "orders": list(orders)}


def yards_at(game: FakeRedAlert, site: tuple[int, int]) -> list[tuple[int, int]]:
    """The top-left cells of the own construction yards within 2 cells of a site."""
    return [
        (building["x"], building["y"])
        for building in game.own_buildings()
        if building["type"] == "fact" and cells.is_within((building["x"], building["y"]), site, 2)
    ]


# -- a whole match with the MCV's first site blocked -------------------------------------------------------------------


@pytest.fixture(scope="module")
def blocked_site_match() -> WholeMatch:
    """A whole fake match with the second yard on, the yards after the first unfolding as the engine unfolds them, a
    deploy into a box a building stands in left without effect, and a pillbox put on the MCV's first site as soon as
    the MCV is out with it, as in the watched game."""
    blocked: dict = {}

    def unfold_and_keep(game: FakeRedAlert) -> None:
        game.unfold_as_the_engine()
        blocked["game"] = game

    def block_the_first_site(agent) -> None:
        upkeep = next(manager for manager in agent.managers if isinstance(manager, UpkeepManager))
        box = upkeep._forward.site_box(agent.world)
        if box is not None and "cell" not in blocked:
            blocked["cell"] = (box.left + 1, box.top + 1)
            blocked["game"].add_own_building("pbox", *blocked["cell"])

    tuning = tuning_off(expansion=expansion_off(second_yard=True))
    return play_whole_match(tuning, scenario=[at_seconds(60, unfold_and_keep)], watch=block_the_first_site)


def test_deploy_refused_a_whole_fake_match_with_a_pillbox_on_the_first_site_deploys_the_mcv_within_2000_ticks(
    blocked_site_match,
):
    events = blocked_site_match.data_of("expansion")
    trained = next(event["tick"] for event in events if event["phase"] == "mcv_trained")
    deployed = [event["tick"] for event in events if event["phase"].startswith("deployed")]
    yards = [building for building in blocked_site_match.game.own_buildings() if building["type"] == "fact"]

    assert len(blocked_site_match.compute_ms) == WHOLE_MATCH_STEPS and blocked_site_match.data_of("error") == []
    assert blocked_site_match.compute_p95_ms <= COMPUTE_P95_MS
    assert {event["reason"] for event in events if event["phase"] == "deploy_refused"} == {"ignored"}
    assert len(yards) == 2 and deployed and deployed[0] - trained <= 2000


# -- the MCV's site by a field it lasts by ------------------------------------------------------------------------------

LASTING = NEW_AREA


def block_field(center: tuple[int, int], width: int, height: int) -> OrePatch:
    """A field of `width` by `height` ore cells around `center`."""
    left, top = center[0] - width // 2, center[1] - height // 2
    return OrePatch(
        center=center, cells=frozenset((left + dx, top + dy) for dx in range(width) for dy in range(height))
    )


# South of the main yard 18 cells off, of 30 and of 60 ore cells; north of it 25 cells off, of 60. Every refinery of
# the base stands 14 cells or more from each center.
SOUTH_30 = block_field((23, 68), 6, 5)
SOUTH_60 = block_field((23, 68), 10, 6)
NORTH_60 = block_field((23, 25), 10, 6)
# A refinery whose middle (31, 68) lies 8 cells east of the southern fields' center, 3 cells from their ore: it stands
# against neither. The state reports it at (31, 67), one right and one down of its footprint's top-left cell (30, 66).
REFINERY_8_CELLS_OFF = ("proc", 31, 67)


def site_check_events(match_record) -> list[tuple[str, str, list | None]]:
    return [
        (event["phase"], event["reason"], event["field_center"])
        for event in match_record("expansion")
        if event["phase"] in ("site_check", "site")
    ]


@pytest.mark.parametrize(
    "tuning, chosen",
    [(switched(LASTING, mcv_site_min_ore_cells=40), NORTH_60), (LASTING, SOUTH_30), (SAFE_SITE, SOUTH_30)],
    ids=["40 cells", "30 cells", "new area off"],
)
def test_lasting_field_a_field_of_30_ore_cells_is_skipped_for_one_of_60(tuning, chosen, match_record):
    agent = safe_site_agent(SOUTH_30, NORTH_60, tuning=tuning)

    events = site_events(agent, MCV_NORTH, match_record)

    assert (SOUTH_30.size, NORTH_60.size) == (30, 60)
    assert [(event["reason"], tuple(event["field_center"])) for event in events] == [("nearest_safe", chosen.center)]
    assert expansion.safe_site(agent.world, agent.ctx, MCV_NORTH).field == chosen


@pytest.mark.parametrize("refinery, chosen", [((), SOUTH_60), ((REFINERY_8_CELLS_OFF,), NORTH_60)], ids=["none", "8"])
def test_lasting_field_a_field_with_an_own_refinery_8_cells_from_its_center_is_skipped(refinery, chosen):
    agent = safe_site_agent(SOUTH_60, NORTH_60, tuning=LASTING)
    ask(agent, england(*BASE, DEPOT, *refinery, seconds=505, units=HARVESTERS))

    box = placement.box_at("proc", placement.footprint_corner("proc", REFINERY_8_CELLS_OFF[1:]))
    assert cells.distance(SOUTH_60.center, cells.nearest_cell(*box.center)) == 8
    assert placement.ore_distance(box, SOUTH_60) == 3
    assert SOUTH_60 not in placement.taken_fields(agent.world, agent.ctx.terrain, agent.ctx.tuning.placement, ())
    assert expansion.safe_site(agent.world, agent.ctx, MCV_NORTH).field == chosen


@pytest.mark.parametrize("tuning, kept", [(LASTING, True), (SAFE_SITE, False)], ids=["on", "new area off"])
def test_lasting_field_a_field_an_own_harvester_last_harvested_is_skipped_until_that_harvester_is_gone(tuning, kept):
    agent = safe_site_agent(SOUTH_60, NORTH_60, tuning=tuning)
    harvester = situations.unit(310, "harv", 23, 68, idle=False)
    chosen, harvested = [], []

    for seconds, more in ((505, [harvester]), (506, [{**harvester, "x": 30, "y": 33}]), (507, [])):
        ask(agent, england(*BASE, DEPOT, seconds=seconds, units=[*HARVESTERS, *more]))
        chosen.append(expansion.safe_site(agent.world, agent.ctx, MCV_NORTH).field)
        harvested.append(agent.ctx.harvested_fields)

    on_ore = frozenset({SOUTH_60}) if kept else frozenset()
    assert harvested == [on_ore, on_ore, frozenset()]
    assert chosen == ([NORTH_60, NORTH_60, SOUTH_60] if kept else [SOUTH_60] * 3)


def test_lasting_field_a_field_marked_dry_has_no_ore_cells_and_is_skipped(monkeypatch):
    monkeypatch.setattr(FreshOre, "dry_fields", property(lambda fresh_ore: frozenset({SOUTH_60})))

    agent = safe_site_agent(SOUTH_60, NORTH_60, tuning=LASTING)

    assert agent.ctx.dry_fields == frozenset({SOUTH_60})
    assert (expansion.ore_cells(SOUTH_60, agent.ctx), expansion.ore_cells(NORTH_60, agent.ctx)) == (0, 60)
    assert expansion.safe_site(agent.world, agent.ctx, MCV_NORTH).field == NORTH_60


@pytest.mark.parametrize(
    "tuning, written",
    [(LASTING, [("site_check", "site_fallback", [23, 68])]), (SAFE_SITE, [])],
    ids=["on", "new area off"],
)
def test_lasting_field_with_no_field_it_lasts_by_today_s_site_stands_and_site_fallback_is_written(
    tuning, written, match_record
):
    # Two fields of 9 ore cells: neither has 40.
    agent = safe_site_agent(FIELD_18, FIELD_25, tuning=tuning)
    ask(agent, england(*BASE, DEPOT, seconds=505, units=[*HARVESTERS, mcv(*MCV_NORTH)]))

    assert site_check_events(match_record) == [*written, ("site", "nearest_safe", [23, 68])]
    assert expansion.safe_site(agent.world, agent.ctx, MCV_NORTH).field == FIELD_18


@pytest.mark.parametrize(
    "tuning, seen, to_site",
    [
        (LASTING, {}, True),
        (LASTING, {"visible_enemy_buildings": PILLBOXES_OFF_THE_SITE}, False),
        (SAFE_SITE, {}, False),
    ],
    ids=["safe route", "no safe route", "new area off"],
)
def test_lasting_field_after_an_escort_timeout_the_mcv_goes_to_the_chosen_site_only_on_a_safe_route(
    tuning, seen, to_site, match_record
):
    # Waiting from 505 s for an escort no unit of main makes up: 1500 ticks are 60 s. The site, chosen as the MCV is
    # taken up, is by FIELD_18, its yard's corner on (19, 67).
    agent = safe_site_agent(FIELD_18, tuning=tuning)
    units = [*HARVESTERS, mcv(*MCV_NORTH)]
    ask(agent, england(*BASE, DEPOT, seconds=505, units=units))
    site = list(expansion.safe_site(agent.world, agent.ctx, MCV_NORTH).site)
    waited = [ask(agent, england(*BASE, DEPOT, seconds=seconds, units=units, **seen)) for seconds in (525, 545)]
    home = list(expansion.home_site(agent.world, agent.ctx))

    timed_out = ask(agent, england(*BASE, DEPOT, seconds=565, units=units, **seen))
    at = site if to_site else home
    deploy = ask(agent, england(*BASE, DEPOT, seconds=570, units=[*HARVESTERS, mcv(*at)], **seen))

    events = [(event["phase"], event["reason"], event["cell"]) for event in match_record("expansion")]
    chosen = next(cell for phase, _, cell in events if phase == "site")
    box = expansion.yard_box(tuple(site))
    assert chosen == site and (box.left, box.top) == (19, 67)
    assert [mcv_orders(orders) for orders in waited] == [[], []]
    assert mcv_orders(timed_out) == [{"cmd": "move", "units": [MCV_ID], "to": at}]
    assert mcv_orders(deploy) == [{"cmd": "deploy", "units": [MCV_ID]}]
    moving = [(reason, cell) for phase, reason, cell in events if phase == "mcv_moving"]
    assert moving == ([("escort_wait_timeout", site)] if to_site else [])
    # The want of its escort is the one reason it waits: no pillbox stands within 10 cells of the site.
    assert [reason for phase, reason, _ in events if phase == "mcv_waiting"] == ["escort_short"]
    assert not any(cells.is_within((box["x"], box["y"]), tuple(site), 10) for box in PILLBOXES_OFF_THE_SITE)
    route = expansion.safe_route(
        MCV_NORTH, tuple(site), expansion.McvDangers(agent.world, agent.ctx), agent.world, agent.ctx
    )
    assert (route is None) is bool(seen)


class HarvestedByTick:
    """The centers of the fields an own harvester last harvested, published in each cycle of a whole match, by tick."""

    def __init__(self) -> None:
        self.by_tick: dict[int, set[tuple[int, int]]] = {}

    def __call__(self, agent) -> None:
        self.by_tick[agent.world.tick] = {field.center for field in agent.ctx.harvested_fields}


# A field of 49 ore cells 32 cells south of the fake map's home start, besides the two lane fields: farther from the
# base than the crawl reaches before the MCV is out.
BIG_SOUTH_ORE = [(x, y) for x in range(19, 26) for y in range(77, 84)]


@pytest.fixture(scope="module")
def lasting_field_match() -> tuple[WholeMatch, HarvestedByTick]:
    """A whole fake match with the shipped tuning on the map with the two lane fields and a 49-cell field."""
    harvested = HarvestedByTick()
    game_config = with_ore(FakeRedAlert().game_config, LANE_ORE + BIG_SOUTH_ORE)
    return play_whole_match(Tuning(), game_config=game_config, watch=harvested), harvested


def test_lasting_field_a_whole_fake_match_with_the_shipped_tuning_runs_without_an_error_and_flags_each_fallback(
    lasting_field_match,
):
    match, harvested = lasting_field_match
    events = match.data_of("expansion")
    sites = sites_by_index(events)

    assert_whole_match_kept_its_bounds(match)
    assert sites and len(own_yards(match)) >= 2
    assert of_phase(events, "deployed")
    for index, site in sites:
        assert_site_flagged_or_unharvested(events[index - 1], site, harvested)
    assert {event["reason"] for event in of_phase(events, "site_check")} <= {"site_fallback"}


def sites_by_index(events: list[dict]) -> list[tuple[int, dict]]:
    """Each `site` event that chose a field, with its place among the events."""
    return [(index, event) for index, event in enumerate(events) if event["phase"] == "site" and event["field_center"]]


def assert_site_flagged_or_unharvested(before: dict, site: dict, harvested: HarvestedByTick) -> None:
    """A site whose event comes right after a `site_check` of its tick is flagged as a fallback by its field; any
    other is by a field no own harvester last harvested."""
    is_flagged = before["phase"] == "site_check" and before["tick"] == site["tick"]
    assert not is_flagged or (before["reason"], before["field_center"]) == ("site_fallback", site["field_center"])
    assert is_flagged or tuple(site["field_center"]) not in harvested.by_tick[site["tick"]]


# -- the new yard's refinery first -------------------------------------------------------------------------------------

# The lane field 24 cells east of the main yard is the creep target, and with the new area on the MCV's site is by
# FIELD_18, due south of the main yard and nearer it: the new yard's field is not the crawl target.
NEW_YARD = switched(NEW_AREA, placement_switches={"creep_next_patch": True})
NEW_YARD_TERRAIN = Terrain(
    (HOME_FIELD, FAR_LANE_FIELD, FIELD_18), HOME_FIELD.cells | FAR_LANE_FIELD.cells | FIELD_18.cells
)
# The base's war factory, lost in the cycle the new yard stands.
WAR_FACTORY = ("weap", 15, 48)
# Two refineries south of FIELD_18, each within 2 cells of its ore and serving it: the field is at its cap.
FIELD_18_AT_ITS_CAP = (("proc", 20, 70), ("proc", 25, 70))


def deployed_by_field_18(*more_buildings, economy: dict | None = None, lost: tuple = ()) -> tuple:
    """An agent whose MCV was sent out by FIELD_18 at 500 s, its orders in the cycle at 520 s in which the yard stands
    at the site with the buildings given, the economy given and the base's buildings `lost` gone, and the yard's
    spec."""
    agent = expanding_agent(NEW_YARD, NEW_YARD_TERRAIN)
    ask(agent, england(*BASE, DEPOT, seconds=500, units=[*HARVESTERS, mcv(), *TANKS]))
    yard = ("fact", 19, 67)
    shown = {} if economy is None else {"economy": economy}
    standing = [spec for spec in BASE if spec not in lost]
    state = england(*standing, DEPOT, *more_buildings, yard, seconds=520, units=[*HARVESTERS, *TANKS], **shown)
    return agent, ask(agent, state), yard


def test_new_yard_refinery_the_next_refinery_stands_against_the_new_yard_s_field_not_the_crawl_target(match_record):
    agent, ordered, yard = deployed_by_field_18()
    claimed = agent.ctx.new_yard_field
    ready = [situations.job("Building", "proc", ready=True)]
    placed = ask(agent, england(*BASE, DEPOT, yard, seconds=525, units=[*HARVESTERS, *TANKS], production=ready))

    (place,) = with_command(placed, "place")
    box = placement.box_at("proc", tuple(place["at"]))
    assert agent.ctx.creep_target.patch == FAR_LANE_FIELD and claimed == FIELD_18
    assert builds(ordered) == ["proc"]
    assert placement.reaches(box, FIELD_18, 2) and not placement.reaches(box, FAR_LANE_FIELD, 2)
    (event,) = match_record("refinery_field")
    assert (event["rule"], event["reason"], event["field_center"]) == ("new_yard", "new_yard", [23, 68])
    # One refinery for the new yard: its claim ends once the refinery is ordered.
    assert agent.ctx.new_yard_field is None and agent.ctx.refinery_claim_due is False


def test_new_yard_refinery_is_ordered_before_a_war_factory_but_not_before_a_power_plant_the_power_target_calls_for():
    ample = situations.economy(5000, provided=1000, drained=300, harvesters=4)
    _, with_claim, _ = deployed_by_field_18(economy=ample, lost=[WAR_FACTORY])
    _, without_claim, _ = deployed_by_field_18(*FIELD_18_AT_ITS_CAP, economy=ample, lost=[WAR_FACTORY])
    short = situations.economy(5000, provided=300, drained=290, harvesters=4)
    _, power_first, _ = deployed_by_field_18(economy=short)

    # With no refinery claimed the same cycle queues the lost war factory again.
    assert builds(without_claim) == ["weap"]
    assert builds(with_claim) == ["proc"]
    assert builds(power_first) == ["powr"]


def test_new_yard_refinery_with_the_field_at_its_cap_nothing_is_claimed_and_the_crawl_target_is_used_as_before(
    match_record,
):
    agent, ordered, yard = deployed_by_field_18(*FIELD_18_AT_ITS_CAP)
    ready = [situations.job("Building", "proc", ready=True)]
    buildings = (*BASE, DEPOT, *FIELD_18_AT_ITS_CAP, yard)
    ask(agent, england(*buildings, seconds=525, units=[*HARVESTERS, *TANKS], production=ready))

    assert agent.ctx.new_yard_field is None and "proc" not in builds(ordered)
    assert agent.ctx.creep_target.patch == FAR_LANE_FIELD
    assert [event for event in match_record("refinery_field") if event.get("rule") == "new_yard"] == []


# -- MCVs to far fields -------------------------------------------------------------------------------------------------

FAR_FIELDS = REPEAT


def lasting_square(center: tuple[int, int]) -> OrePatch:
    """A field of 7 by 7 ore cells round a center: 49 ore cells, enough for a yard to last by."""
    x, y = center
    return OrePatch(center=center, cells=frozenset((x + dx, y + dy) for dx in range(-3, 4) for dy in range(-3, 4)))


# Lasting fields 35, 45 and 55 cells from the main yard's middle (23, 50), and 25 cells north of it. The 45-cell field
# lies south-west, on the base's own side; the 55-cell one south-east, off it.
FAR_35 = lasting_square((23, 85))
FAR_45 = lasting_square((8, 92))
FAR_55 = lasting_square((62, 89))
NORTH_25 = lasting_square((23, 25))
# A row of pillboxes 14 cells north of FAR_35's ore, out of its defense check: their cover closes every way to it from
# the main base.
PILLBOX_ROW = [pillbox(960 + number, x, 68) for number, x in enumerate(range(0, 96, 8))]


def far_choice(*fields: OrePatch, tuning: Tuning = FAR_FIELDS, more_buildings=(), start=MAIN_YARD, **seen):
    """The site far_site gives from `start` to an agent with the base, its depot and the buildings given on a terrain of
    the home field and the fields given."""
    agent = repeat_agent(*fields, tuning=tuning)
    ask(agent, england(*BASE, DEPOT, *more_buildings, seconds=500, units=HARVESTERS, **seen))
    return expansion.far_site(agent.world, agent.ctx, start), agent


@pytest.mark.parametrize(
    "field, chosen", [(FAR_35, True), (FAR_45, True), (FAR_55, False)], ids=["35 cells", "45 cells", "55 cells"]
)
def test_far_fields_a_lasting_field_within_50_cells_is_the_site_and_one_55_cells_off_is_not(field, chosen):
    choice, agent = far_choice(field)
    _, repeat_off = far_choice(field, tuning=SAFE_SITE)
    # The 50 cells bar the 55-cell field with the own-side rule waived as well.
    waived = expansion.far_site(agent.world, agent.ctx, MAIN_YARD, own_side_rule=False)

    distance = round(cells.distance(field.center, MAIN_YARD), 1)
    assert (choice is not None and choice.field == field) is chosen
    assert (waived is not None and waived.field == field) is chosen
    assert choice is None or round(choice.distance, 1) == distance
    # The 30 cells of mcv_site_max_cells bar all three without mcv_repeat.
    assert expansion.safe_site(repeat_off.world, repeat_off.ctx, MAIN_YARD).field is None


def test_far_fields_a_lasting_field_with_no_safe_route_is_not_the_site():
    covered, agent = far_choice(FAR_35, visible_enemy_buildings=PILLBOX_ROW)
    clear, _ = far_choice(FAR_35)

    assert covered is None and clear.field == FAR_35
    assert not placement.is_defended(FAR_35, agent.world, agent.ctx.tuning.placement.creep_defense_clear_cells)


def test_far_fields_the_site_is_by_the_field_nearest_by_route_from_the_mcv_not_the_one_nearest_a_yard():
    # FAR_35's center lies 16 cells from the second yard's middle and NORTH_25's 25 from the main yard's, but from the
    # main yard the way to NORTH_25 is the shorter.
    choice, agent = far_choice(FAR_35, NORTH_25, more_buildings=(SECOND_YARD_SOUTH,))

    ordered = expansion._far_fields(expansion._yard_middles(agent.world), agent.world, agent.ctx)
    assert ordered == [FAR_35, NORTH_25]
    assert (choice.field, round(choice.distance, 1)) == (NORTH_25, 24.0)


@pytest.mark.parametrize("by_the_field, chosen", [((), True), ((("fact", 22, 85),), False)], ids=["no yard", "a yard"])
def test_far_fields_a_field_an_own_yard_already_stands_by_is_not_the_site(by_the_field, chosen):
    # A yard whose box, (22, 85) to (24, 88), lies 1 cell from FIELD_20_FROM_SECOND's ore, as one deployed by it would.
    choice, _ = far_choice(FIELD_20_FROM_SECOND, more_buildings=(SECOND_YARD_SOUTH, *by_the_field))

    assert (choice is not None and choice.field == FIELD_20_FROM_SECOND) is chosen


# The crawl from the opening, which walks to the far field: the lasting field 30 cells south, then a small one north.
CRAWL = switched(FAR_FIELDS, placement_switches={"creep_from_opening": True, "creep_continuous": True})
CRAWL_FIELD = lasting_square((23, 80))
NEXT_CRAWL_FIELD = square_field((23, 10))


# A war factory whose middle (13, 63) lies 19.7 cells from the crawl field's center, and a power plant 14 cells from it.
WAR_FACTORY_19_7 = ("weap", 12, 62)
POWER_14 = ("powr", 23, 64)
# A lasting field south-east of the base whose place for a yard, (45, 60), lies on the enemy's side.
ENEMY_SIDE_FIELD = lasting_square((50, 62))


@pytest.mark.parametrize("near", [(), (POWER_14,), (WAR_FACTORY_19_7,)], ids=["25 cells", "14 cells", "19.7 cells"])
def test_far_fields_the_crawl_target_is_taken_whatever_the_distance_to_own_buildings_and_the_crawl_moves_on(
    near, match_record
):
    agent = repeat_agent(CRAWL_FIELD, NEXT_CRAWL_FIELD, tuning=CRAWL)
    buildings = (*BASE, DEPOT, *near)
    ask(agent, england(*buildings, seconds=499, units=HARVESTERS))
    crawl_before = agent.ctx.creep_target.patch
    ask(agent, england(*buildings, seconds=500, units=[*HARVESTERS, mcv(), *TANKS]))
    ask(agent, england(*buildings, seconds=501, units=[*HARVESTERS, mcv(), *TANKS]))

    (site,) = [event for event in match_record("expansion") if event["phase"] == "site"]
    assert crawl_before == CRAWL_FIELD and site["field_center"] == [23, 80]
    assert agent.ctx.creep_target.patch == NEXT_CRAWL_FIELD and CRAWL_FIELD in agent.ctx.skipped_fields


def test_far_fields_the_crawl_target_19_7_cells_from_a_war_factory_is_the_site_and_the_mcv_is_ordered_in_that_cycle():
    agent = repeat_agent(CRAWL_FIELD, NEXT_CRAWL_FIELD, tuning=CRAWL)
    buildings = (*BASE, DEPOT, WAR_FACTORY_19_7)
    ask(agent, england(*buildings, seconds=499, cash=0, units=HARVESTERS))
    crawl_before = agent.ctx.creep_target.patch

    orders = ask(agent, england(*buildings, seconds=500, cash=2500, units=HARVESTERS))

    middle = cells.nearest_cell(*placement.box_at("weap", WAR_FACTORY_19_7[1:]).center)
    assert round(cells.distance(CRAWL_FIELD.center, middle), 1) == 19.7
    assert crawl_before == CRAWL_FIELD and expansion._is_own_side(agent.ctx.repeat_mcv_site, agent.world, agent.ctx)
    assert agent.ctx.repeat_mcv_field == CRAWL_FIELD and ("mcv", 1) in trains(orders)


def test_far_fields_a_crawl_target_off_the_base_s_own_side_is_still_refused():
    agent = repeat_agent(ENEMY_SIDE_FIELD, tuning=at_share(CRAWL))
    ask(agent, england(*BASE, DEPOT, seconds=499, units=HARVESTERS))
    agent.ctx.creep_target = placement.CreepTarget(ENEMY_SIDE_FIELD, 1)

    waived = expansion.far_site(agent.world, agent.ctx, MAIN_YARD, own_side_rule=False)

    assert expansion.far_site(agent.world, agent.ctx, MAIN_YARD) is None
    assert waived.field == ENEMY_SIDE_FIELD and not expansion._is_own_side(waived.site, agent.world, agent.ctx)


# -- the yards the income allows ----------------------------------------------------------------------------------------

# A lasting field 34 cells north of the main yard's middle, 57 from the second yard's; and one 54.5 cells north-east of
# the main yard's, 72 from the second yard's.
FAR_NORTH_35 = lasting_square((23, 15))
FAR_NORTH_EAST_55 = lasting_square((60, 10))


@pytest.mark.parametrize("income, allowed", [(None, 1), (39, 1), (70, 2), (120, 4), (400, 6)])
def test_far_fields_the_yards_allowed_are_1_and_1_for_each_40_of_income_a_second_6_at_most(income, allowed):
    agent = repeat_agent(income=income)
    ask(agent, england(*BASE, DEPOT, seconds=600, units=HARVESTERS))

    assert expansion.yards_allowed(agent.ctx) == allowed


def far_fields_cycle(income: float, cash: int, *fields: OrePatch, seconds: float = 600, more_buildings=()) -> tuple:
    """The orders of a cycle of an agent with the income and the money given and two yards standing, on a terrain of the
    home field and the fields given, and the agent."""
    agent = repeat_agent(*fields, income=income)
    state = england(*BASE, DEPOT, SECOND_YARD_SOUTH, *more_buildings, seconds=seconds, cash=cash, units=HARVESTERS)
    return ask(agent, state), agent


def test_far_fields_with_120_a_second_2_yards_and_2500_an_mcv_is_ordered_for_a_lasting_field_35_cells_away():
    orders, agent = far_fields_cycle(120, 2500, FAR_NORTH_35)

    assert ("mcv", 1) in trains(orders)
    assert agent.ctx.repeat_mcv_field == FAR_NORTH_35
    assert round(cells.distance(FAR_NORTH_35.center, expansion.main_yard(agent.world)), 1) == 34.0


@pytest.mark.parametrize(
    "income, cash, fields, seconds, seen, sited",
    [
        (120, 2500, (FAR_NORTH_EAST_55,), 600, {}, False),
        (120, 2500, (FAR_35,), 600, {"visible_enemy_buildings": PILLBOX_ROW}, False),
        (120, 2299, (FAR_NORTH_35,), 600, {}, True),
        (70, 2500, (FAR_NORTH_35,), 600, {}, False),
        (120, 2500, (FAR_NORTH_35,), 230, {}, False),
    ],
    ids=["55 cells away", "no safe route", "below the MCV's price", "income for 2 yards", "before 240 s"],
)
def test_far_fields_no_mcv_is_ordered(income, cash, fields, seconds, seen, sited):
    agent = repeat_agent(*fields, income=income)
    state = england(*BASE, DEPOT, SECOND_YARD_SOUTH, seconds=seconds, cash=cash, units=HARVESTERS, **seen)

    orders = ask(agent, state)

    # Short of the money the MCV is wanted all the same: its site is published, so that the economy holds for it.
    assert ("mcv", 1) not in trains(orders) and (agent.ctx.repeat_mcv_site is not None) is sited


@pytest.mark.parametrize("income, trained", [(39, False), (40, True)])
def test_far_fields_with_one_yard_the_second_yard_s_mcv_waits_for_40_a_second_of_income(income, trained):
    # Before second_mcv_due_seconds: from then on the second yard is due whatever the income.
    agent = repeat_agent(FAR_NORTH_35, income=income)

    orders = ask(agent, england(*BASE, DEPOT, seconds=290, cash=2500, units=HARVESTERS))

    assert (("mcv", 1) in trains(orders)) is trained
    assert agent.ctx.repeat_mcv_field == (FAR_NORTH_35 if trained else None)


def test_far_fields_a_yard_cap_event_is_written_each_time_the_income_moves_the_cap(match_record):
    agent = repeat_agent(income=70)
    for seconds, income in ((600, 70), (601, 75), (602, 120), (603, 130), (604, 70)):
        agent.ctx.income.fixed = income
        ask(agent, england(*BASE, DEPOT, SECOND_YARD_SOUTH, seconds=seconds, cash=0, units=HARVESTERS))

    caps = [event for event in match_record("expansion") if event["phase"] == "yard_cap"]
    # The first is the agent's first state's, before any income is known.
    assert [(event["income"], event["yards"], event["mcvs"], event["cap"]) for event in caps] == [
        (None, 1, 0, 1),
        (70, 2, 0, 2),
        (120, 2, 0, 4),
        (70, 2, 0, 2),
    ]
    assert {event["reason"] for event in caps} == {"yard_cap"}


# -- the second MCV due by second_mcv_due_seconds ----------------------------------------------------------------------

DUE_SECONDS = Tuning().expansion.second_mcv_due_seconds


def due_agent(*fields: OrePatch, income: float | None = None):
    """An agent with far fields on and mcv_first off, its income unknown, on the home field and the fields given."""
    return repeat_agent(*fields, tuning=FAR_FIELDS, income=income)


def blocked_events(match_record) -> list[dict]:
    return [event for event in match_record("expansion") if event["phase"] == "second_mcv_blocked"]


def test_second_mcv_due_at_300_s_the_mcv_is_ordered_ahead_of_a_second_war_factory_with_the_income_unknown():
    agent = due_agent(FAR_35)
    ask(agent, england(*BASE, DEPOT, seconds=DUE_SECONDS - 30, cash=6000, units=HARVESTERS))

    orders = ask(agent, england(*BASE, DEPOT, seconds=DUE_SECONDS, cash=6000, units=HARVESTERS))

    items = [order["item"] for order in orders if order["cmd"] in ("train", "build")]
    assert DUE_SECONDS == 300 and agent.ctx.tuning.upkeep.mcv_first is False
    assert "mcv" in items and "weap" in items and items.index("mcv") < items.index("weap")
    assert agent.ctx.repeat_mcv_field == FAR_35


def test_second_mcv_due_the_money_it_waits_for_is_the_mcv_s_price():
    price = due_agent(FAR_35).ctx.production.cost("mcv")

    short = ask(due_agent(FAR_35), england(*BASE, DEPOT, seconds=DUE_SECONDS, cash=price - 1, units=HARVESTERS))
    covered = ask(due_agent(FAR_35), england(*BASE, DEPOT, seconds=DUE_SECONDS, cash=price, units=HARVESTERS))

    assert price == 2000 and ("mcv", 1) not in trains(short) and ("mcv", 1) in trains(covered)


def test_second_mcv_due_without_a_site_nothing_is_ordered_and_second_mcv_blocked_names_the_site(match_record):
    agent = due_agent()

    orders = [ask(agent, england(*BASE, DEPOT, seconds=seconds, units=HARVESTERS)) for seconds in (300, 310, 330)]

    assert all(("mcv", 1) not in trains(cycle) for cycle in orders)
    events = blocked_events(match_record)
    assert [(event["tick"], event["reason"], event["blocked_by"]) for event in events] == [
        (300 * TICKS_PER_SECOND, "second_mcv_blocked", "site"),
        (330 * TICKS_PER_SECOND, "second_mcv_blocked", "site"),
    ]


def test_second_mcv_due_without_the_money_second_mcv_blocked_names_the_money(match_record):
    agent = due_agent(FAR_35)

    ask(agent, england(*BASE, DEPOT, seconds=DUE_SECONDS, cash=100, units=HARVESTERS))

    assert [event["blocked_by"] for event in blocked_events(match_record)] == ["money"]


@pytest.mark.parametrize("income, ordered", [(None, False), (THREE_YARDS_INCOME, True)], ids=["unknown", "2 yards"])
def test_second_mcv_due_before_300_s_today_s_rules_apply(income, ordered, match_record):
    agent = due_agent(FAR_35, income=income)

    orders = ask(agent, england(*BASE, DEPOT, seconds=DUE_SECONDS - 10, units=HARVESTERS))

    assert (("mcv", 1) in trains(orders)) is ordered and blocked_events(match_record) == []


def test_second_mcv_due_with_two_yards_standing_the_rule_does_not_apply(match_record):
    agent = due_agent(FAR_35)

    orders = ask(agent, england(*BASE, DEPOT, SECOND_YARD_SOUTH, seconds=DUE_SECONDS, units=HARVESTERS))

    assert ("mcv", 1) not in trains(orders) and blocked_events(match_record) == []
    assert not expansion.is_second_mcv_due(agent.world, agent.ctx)


# The item the MCV's full hold waits for.
MCV_ITEM = "mcv"
# The second yard deploys by this game second in a whole fake match with the shipped tuning.
DUE_YARD_BY_SECONDS = 420


@pytest.fixture(scope="module", params=["england", "russia"])
def shipped_due_match(request) -> WholeMatch:
    """A whole fake match for a family's start with the shipped tuning, on the map with six fields round the own spawn
    and two far fields of 49 ore cells; yards unfold as the engine unfolds them."""
    return play_whole_match(
        Tuning(),
        request.param,
        game_config=with_ore(FakeRedAlert().game_config, SIX_FIELDS_ORE),
        scenario=[at_seconds(60, FakeRedAlert.unfold_as_the_engine)],
    )


def test_second_mcv_due_a_whole_shipped_fake_match_deploys_a_second_yard_by_420_s(shipped_due_match):
    deployed = [event for event in shipped_due_match.data_of("expansion") if event["phase"] == "deployed"]
    errors = shipped_due_match.data_of("error")

    assert len(shipped_due_match.compute_ms) == WHOLE_MATCH_STEPS
    assert [error for error in errors if error["type"] == "unhandled"] == []
    assert shipped_due_match.compute_p95_ms <= COMPUTE_P95_MS
    assert deployed and deployed[0]["tick"] <= DUE_YARD_BY_SECONDS * TICKS_PER_SECOND


def test_second_mcv_due_its_full_hold_neither_times_out_nor_waits_for_a_release():
    upkeep = Tuning().upkeep
    timed, untimed = FullHold(MCV_ITEM), FullHold(MCV_ITEM)
    past = (upkeep.mcv_first_wait_seconds + 1) * TICKS_PER_SECOND

    assert [timed.holds(True, False, tick, upkeep) for tick in (0, past, past + 25)] == [True, False, False]
    assert [untimed.holds(True, False, tick, upkeep, untimed=True) for tick in (0, past, past + 25)] == [True] * 3
    assert timed.holds(True, False, past + 50, upkeep, untimed=True) is True


# -- no MCV site near a known enemy yard, refinery or ground defense ---------------------------------------------------

# The place for a yard by FIELD_18 that an MCV coming from the north is given, and the place by FAR_35 from the main
# yard.
FIELD_18_SITE = (20, 68)
FAR_35_SITE = (23, 79)


def enemy_building(building_id: int, building_type: str, cell: tuple[int, int]) -> dict:
    return {"id": building_id, "type": building_type, "x": cell[0], "y": cell[1], "hp": 100}


@pytest.mark.parametrize(
    "seen, resited",
    [(enemy_building(970, "fact", (30, 68)), True), (enemy_building(971, "proc", (40, 68)), False)],
    ids=["enemy yard 10 cells off", "enemy refinery 20 cells off"],
)
def test_enemy_building_near_an_enemy_yard_seen_10_cells_from_the_mcv_s_site_makes_it_re_site(
    seen, resited, match_record
):
    agent = safe_site_agent(FIELD_18, FIELD_25)
    on_its_way(agent, 505, MCV_NORTH)

    orders = on_its_way(agent, 506, (22, 50), visible_enemy_buildings=[seen])

    first = (505 * TICKS_PER_SECOND, "nearest_safe", [23, 68])
    again = (506 * TICKS_PER_SECOND, "enemy_building_near", [23, 25])
    site = [event for event in match_record("expansion") if event["phase"] == "site"][-1]["cell"]
    assert cells.distance(FIELD_18_SITE, (seen["x"], seen["y"])) == (10 if resited else 20)
    assert site_reasons(match_record) == ([first, again] if resited else [first])
    assert (tuple(site) == FIELD_18_SITE) is not resited
    # An MCV that keeps its site is not told the same move again within 75 ticks.
    assert mcv_orders(orders) == ([{"cmd": "move", "units": [MCV_ID], "to": site}] if resited else [])


def test_enemy_building_near_an_mcv_waiting_for_its_escort_is_re_sited_and_with_no_site_left_goes_home(match_record):
    agent = safe_site_agent(FIELD_18)
    ask(agent, england(*BASE, DEPOT, seconds=505, units=[*HARVESTERS, mcv(*MCV_NORTH)]))
    refinery = enemy_building(972, "proc", (28, 72))

    orders = ask(
        agent,
        england(*BASE, DEPOT, seconds=506, units=[*HARVESTERS, mcv(*MCV_NORTH)], visible_enemy_buildings=[refinery]),
    )

    home = expansion.home_site(agent.world, agent.ctx)
    assert cells.distance(FIELD_18_SITE, (28, 72)) < 14
    assert [reason for _, reason, _ in site_reasons(match_record)] == ["nearest_safe", "home_fallback"]
    assert [event["reason"] for event in match_record("expansion") if event["phase"] == "mcv_waiting"] == [
        "escort_short"
    ]
    assert mcv_orders(orders) == [{"cmd": "move", "units": [MCV_ID], "to": list(home)}]


@pytest.mark.parametrize(
    "seen, chosen",
    [
        ((), True),
        ((enemy_building(973, "proc", (33, 79)),), False),
        ((enemy_building(974, "proc", (38, 79)),), True),
        ((enemy_building(975, "fact", (23, 93)),), False),
    ],
    ids=["none", "refinery 10 cells off", "refinery 15 cells off", "yard 14 cells off"],
)
def test_enemy_building_near_a_far_site_within_14_cells_of_an_enemy_yard_or_refinery_is_not_chosen(seen, chosen):
    choice, _ = far_choice(FAR_35, visible_enemy_buildings=list(seen))

    assert (choice.site if chosen else choice) == (FAR_35_SITE if chosen else None)


def test_enemy_building_near_the_safe_site_passes_over_a_place_10_cells_from_an_enemy_yard():
    by_field_18 = {"visible_enemy_buildings": [enemy_building(976, "fact", (30, 68))]}
    clear = safe_site_agent(FIELD_18, FIELD_25)
    agent = safe_site_agent(FIELD_18, FIELD_25, **by_field_18)

    assert expansion.safe_site(clear.world, clear.ctx, MCV_NORTH).site == FIELD_18_SITE
    assert expansion.safe_site(agent.world, agent.ctx, MCV_NORTH).field == FIELD_25


# -- an MCV site only on the base's own side ---------------------------------------------------------------------------

# The base's center and the enemy's start the fixture's states give.
BASE_CENTER = (22, 48)
ENEMY_START = (74, 48)
# Lasting fields whose place for a yard lies 20 cells from the base's center and 40 from the enemy's start, and 30 and
# 32: the rule measures the site, the place the MCV deploys on.
OWN_SIDE_FIELD = lasting_square((41, 64))
ENEMY_SIDE_FIELD = lasting_square((52, 67))
OWN_SIDE_SITE = (36, 62)
ENEMY_SIDE_SITE = (47, 65)
# The share the tests of the rule's working measure with, on whose either side the two sites above lie; the shipped 1.1
# takes both in, the middle of the map with them.
WORKING_SHARE = 0.8
# A site in the middle of the map, 46 cells from the base's center and 45 from the enemy's start (1.02), one at 1.21,
# and a known enemy refinery 10 cells from the first.
MID_MAP_SITE = (49, 11)
BEYOND_MID_MAP_SITE = (53, 22)
ENEMY_REFINERY_BY_MID_MAP = enemy_building(985, "proc", (59, 11))


def at_share(tuning: Tuning, share: float = WORKING_SHARE) -> Tuning:
    """The tuning with `mcv_own_side_share` of [expansion] at `share`."""
    return dataclasses.replace(tuning, expansion=dataclasses.replace(tuning.expansion, mcv_own_side_share=share))


def without_enemy_start(state: dict) -> dict:
    """The state with no enemy start named in it; the fake arena's game reference gives no spawns either."""
    state["legal_actions"]["enemy_base"] = None
    return state


def unknown_start_agent(*fields: OrePatch, tuning: Tuning = FAR_FIELDS):
    """`due_agent`'s agent, far fields on and the income unknown, on the home field and the fields given, whose first
    state names no enemy start."""
    terrain = Terrain((HOME_FIELD, *fields), frozenset().union(HOME_FIELD.cells, *(field.cells for field in fields)))
    agent = new_agent(UpkeepManager(), plan_selector=OpeningThen(HOLD), tuning=tuning)
    ask(agent, without_enemy_start(base_state("england", *BASE, tick=100, cash=0)))
    agent.ctx.terrain = terrain
    agent.ctx.income = FixedIncome(tuning, None)
    return agent


def distances(site: tuple[int, int]) -> tuple[int, int]:
    """How far a site lies from the base's center and from the enemy's start, in whole cells."""
    return round(cells.distance(site, BASE_CENTER)), round(cells.distance(site, ENEMY_START))


@pytest.mark.parametrize(
    "field, site, cells_off, qualifies",
    [(OWN_SIDE_FIELD, OWN_SIDE_SITE, (20, 40), True), (ENEMY_SIDE_FIELD, ENEMY_SIDE_SITE, (30, 32), False)],
    ids=["20 and 40 cells", "30 and 32 cells"],
)
def test_own_half_a_site_20_cells_from_base_and_40_from_the_enemy_start_qualifies_and_one_30_and_32_does_not(
    field, site, cells_off, qualifies
):
    choice, agent = far_choice(field, tuning=at_share(FAR_FIELDS))
    waived = expansion.far_site(agent.world, agent.ctx, MAIN_YARD, own_side_rule=False)

    assert (agent.world.base_center, agent.world.enemy_base) == (BASE_CENTER, ENEMY_START)
    assert waived.site == site and distances(site) == cells_off
    assert agent.ctx.tuning.expansion.mcv_own_side_share == WORKING_SHARE
    assert (choice.site if qualifies else choice) == (site if qualifies else None)


@pytest.mark.parametrize(
    "field, ordered, blocked_by",
    [(ENEMY_SIDE_FIELD, False, ["own_side", "own_side"]), (OWN_SIDE_FIELD, True, [])],
    ids=["only off the own side", "on the own side"],
)
def test_own_half_with_only_a_field_off_the_own_side_no_mcv_is_ordered_and_second_mcv_blocked_names_own_side(
    field, ordered, blocked_by, match_record
):
    agent = repeat_agent(field, tuning=at_share(FAR_FIELDS), income=None)

    orders = [ask(agent, england(*BASE, DEPOT, seconds=seconds, units=HARVESTERS)) for seconds in (300, 330)]

    assert (("mcv", 1) in trains(orders[0])) is ordered
    assert [event["blocked_by"] for event in blocked_events(match_record)] == blocked_by


def test_own_half_with_the_enemy_start_unknown_the_rule_does_not_apply_and_the_mcv_is_ordered(match_record):
    agent = unknown_start_agent(ENEMY_SIDE_FIELD, tuning=at_share(FAR_FIELDS))

    orders = ask(agent, without_enemy_start(england(*BASE, DEPOT, seconds=DUE_SECONDS, units=HARVESTERS)))

    assert agent.world.base_center == BASE_CENTER and agent.world.enemy_base is None
    assert agent.ctx.repeat_mcv_site == ENEMY_SIDE_SITE and ("mcv", 1) in trains(orders)
    assert blocked_events(match_record) == []


def test_own_half_an_mcv_whose_site_is_off_the_own_side_once_the_enemy_start_is_known_is_sited_again(match_record):
    agent = unknown_start_agent(ENEMY_SIDE_FIELD, FAR_35, tuning=at_share(FAR_FIELDS))
    on_its_way_unknown = [*HARVESTERS, situations.unit(MCV_ID, "mcv", *MCV_NORTH, idle=False), *TANKS]
    ask(agent, without_enemy_start(england(*BASE, DEPOT, seconds=505, units=on_its_way_unknown)))

    # The fixture's state names the enemy's start.
    orders = on_its_way(agent, 506, (24, 50))

    assert agent.world.enemy_base == ENEMY_START
    assert site_reasons(match_record) == [
        (505 * TICKS_PER_SECOND, "nearest_safe", list(ENEMY_SIDE_FIELD.center)),
        (506 * TICKS_PER_SECOND, "own_side", list(FAR_35.center)),
    ]
    assert mcv_orders(orders) == [{"cmd": "move", "units": [MCV_ID], "to": list(FAR_35_SITE)}]


def test_mid_map_a_site_at_1_02_of_its_distance_from_the_enemy_start_qualifies_and_one_at_1_2_does_not():
    agent = safe_site_agent(FIELD_18)

    mid_map, beyond = (
        cells.distance(site, BASE_CENTER) / cells.distance(site, ENEMY_START)
        for site in (MID_MAP_SITE, BEYOND_MID_MAP_SITE)
    )

    assert agent.ctx.tuning.expansion.mcv_own_side_share == 1.1
    assert distances(MID_MAP_SITE) == (46, 45) and 1.02 <= mid_map < 1.1 < 1.2 <= beyond
    assert expansion._is_open_site(MID_MAP_SITE, agent.world, agent.ctx)
    assert expansion._is_own_side(MID_MAP_SITE, agent.world, agent.ctx)
    assert not expansion._is_own_side(BEYOND_MID_MAP_SITE, agent.world, agent.ctx)
    assert not expansion._is_open_site(BEYOND_MID_MAP_SITE, agent.world, agent.ctx)


def test_mid_map_a_site_10_cells_from_a_known_enemy_refinery_does_not_qualify():
    clear = safe_site_agent(FIELD_18)
    agent = safe_site_agent(FIELD_18, visible_enemy_buildings=[ENEMY_REFINERY_BY_MID_MAP])

    refinery = (ENEMY_REFINERY_BY_MID_MAP["x"], ENEMY_REFINERY_BY_MID_MAP["y"])
    assert cells.distance(MID_MAP_SITE, refinery) == 10 < agent.ctx.tuning.expansion.mcv_enemy_building_cells
    assert expansion._is_open_site(MID_MAP_SITE, clear.world, clear.ctx)
    assert not expansion._is_open_site(MID_MAP_SITE, agent.world, agent.ctx)
    assert expansion._unfit_site_reason(MID_MAP_SITE, agent.world, agent.ctx) == "enemy_building_near"


# A field in the middle of the map whose place for a yard, (46, 57), qualifies at 1.1 but not at 0.8, and a field west
# of the base the MCV is sent to instead.
MID_MAP_FIELD = square_field((49, 57))
MID_MAP_FIELD_SITE = (46, 57)
WEST_FIELD_29 = square_field((2, 70))


def test_mid_map_an_enemy_yard_first_seen_10_cells_from_the_site_on_the_mcv_s_trip_makes_it_re_site(match_record):
    agent = safe_site_agent(MID_MAP_FIELD, WEST_FIELD_29)
    at_0_8 = safe_site_agent(MID_MAP_FIELD, WEST_FIELD_29, tuning=at_share(SAFE_SITE))
    on_its_way(agent, 505, MCV_NORTH)

    yard = enemy_building(986, "fact", (56, 57))
    orders = on_its_way(agent, 506, (30, 52), visible_enemy_buildings=[yard])

    site = [event for event in match_record("expansion") if event["phase"] == "site"][-1]["cell"]
    ratio = cells.distance(MID_MAP_FIELD_SITE, BASE_CENTER) / cells.distance(MID_MAP_FIELD_SITE, ENEMY_START)
    assert expansion.safe_site(at_0_8.world, at_0_8.ctx, MCV_NORTH).field == WEST_FIELD_29 and 0.8 < ratio < 1.1
    assert cells.distance(MID_MAP_FIELD_SITE, (yard["x"], yard["y"])) == 10
    assert site_reasons(match_record) == [
        (505 * TICKS_PER_SECOND, "nearest_safe", list(MID_MAP_FIELD.center)),
        (506 * TICKS_PER_SECOND, "enemy_building_near", list(WEST_FIELD_29.center)),
    ]
    assert mcv_orders(orders) == [{"cmd": "move", "units": [MCV_ID], "to": site}]


# A field 18 cells east of the base's center in a block of ground nothing can be built on, which ends at x = 42: its
# place for a yard lies east of its ore, at (44, 49), on the own side, and the place two cells farther from the ore,
# at (46, 49), off it.
WALLED_EAST_FIELD = square_field((40, 48))
UNBUILDABLE_BLOCK = frozenset((x, y) for x in range(30, 43) for y in range(40, 57))
WALLED_EAST_SITE = (44, 49)
OFF_SIDE_NEXT_PLACE = (46, 49)


def is_on_the_own_side(cell: tuple[int, int], share: float) -> bool:
    return cells.distance(cell, BASE_CENTER) <= share * cells.distance(cell, ENEMY_START)


def test_own_half_a_refused_deploy_whose_next_place_lies_off_the_own_side_sends_the_mcv_home(match_record):
    terrain = Terrain((HOME_FIELD, WALLED_EAST_FIELD), HOME_FIELD.cells | WALLED_EAST_FIELD.cells | UNBUILDABLE_BLOCK)
    agent = expanding_agent(at_share(SAFE_SITE), terrain)
    on_its_way(agent, 505, MCV_NORTH)

    sent = deploys_from(agent, WALLED_EAST_SITE, 520, 523, 526)
    home = expansion.home_site(agent.world, agent.ctx)
    ask(agent, england(*BASE, DEPOT, ("fact", home[0] - 1, home[1] - 1), seconds=530, units=[*HARVESTERS, *TANKS]))

    share = agent.ctx.tuning.expansion.mcv_own_side_share
    ore = WALLED_EAST_FIELD.nearest_cell(WALLED_EAST_SITE)
    assert cells.toward(ore, WALLED_EAST_SITE, cells.distance(ore, WALLED_EAST_SITE) + 2) == OFF_SIDE_NEXT_PLACE
    assert is_on_the_own_side(WALLED_EAST_SITE, share) and not is_on_the_own_side(OFF_SIDE_NEXT_PLACE, share)
    assert sent == [DEPLOY, DEPLOY, [{"cmd": "move", "units": [MCV_ID], "to": list(home)}]]
    assert refusals(match_record) == [(list(WALLED_EAST_SITE), "ignored")]
    deployed = [event for event in match_record("expansion") if event["phase"] == "deployed_home"]
    assert [event["reason"] for event in deployed] == ["own_side"]


# -- the travelling MCV holds short of an enemy army by its site --------------------------------------------------------


def enemy_group(*types: str, at: tuple[int, int] = (30, 68)) -> dict:
    """Enemy units in sight in a column from `at`, 10 cells east of FIELD_18's place for a yard."""
    return {
        "visible_enemies": [
            {"id": 980 + number, "type": unit_type, "x": at[0], "y": at[1] + number, "hp": 100}
            for number, unit_type in enumerate(types)
        ]
    }


# Worth 1,500 together (two light tanks and a rifleman), and 400 (a rocket soldier and a rifleman).
ARMY_1500 = ("1tnk", "1tnk", "e1")
ARMY_400 = ("e3", "e1")
HOLD_SECONDS = 30


def holds(match_record) -> list[tuple[int, str, list, int]]:
    return [
        (event["tick"], event["reason"], event["field_center"], event["value"])
        for event in match_record("expansion")
        if event["phase"] == "mcv_hold"
    ]


def test_site_army_a_travelling_mcv_with_a_1500_group_10_cells_from_its_site_holds_with_one_mcv_hold_event(
    match_record,
):
    agent = safe_site_agent(FIELD_18, FIELD_25)
    on_its_way(agent, 505, MCV_NORTH)

    first = on_its_way(agent, 506, (22, 50), **enemy_group(*ARMY_1500))
    again = on_its_way(agent, 507, (22, 50), **enemy_group(*ARMY_1500))

    assert cells.distance(FIELD_18_SITE, (30, 68)) == 10
    assert expansion.SITE_ARMY_CLEAR_CELLS == 15 and agent.ctx.tuning.expansion.mcv_site_army_value == 600
    assert holds(match_record) == [(506 * TICKS_PER_SECOND, "site_army", [23, 68], 1500)]
    # 18.8 cells from the nearest tank: past the 15 it holds at, so it is stopped where it is, and not told again.
    assert mcv_orders(first) == [{"cmd": "stop", "units": [MCV_ID]}]
    assert mcv_orders(again) == [{"cmd": "stop", "units": [MCV_ID]}]
    assert site_reasons(match_record) == [(505 * TICKS_PER_SECOND, "nearest_safe", [23, 68])]


def test_site_army_after_30_s_with_the_group_still_there_the_mcv_is_sited_again_with_reason_site_army(match_record):
    agent = safe_site_agent(FIELD_18, FIELD_25)
    on_its_way(agent, 505, MCV_NORTH)
    on_its_way(agent, 506, (22, 50), **enemy_group(*ARMY_1500))
    still = on_its_way(agent, 506 + HOLD_SECONDS - 1, (22, 50), **enemy_group(*ARMY_1500))

    orders = on_its_way(agent, 506 + HOLD_SECONDS, (22, 50), **enemy_group(*ARMY_1500))

    site = [event for event in match_record("expansion") if event["phase"] == "site"][-1]["cell"]
    assert agent.ctx.tuning.expansion.mcv_site_hold_seconds == HOLD_SECONDS
    assert mcv_orders(still) == [{"cmd": "stop", "units": [MCV_ID]}]
    assert site_reasons(match_record) == [
        (505 * TICKS_PER_SECOND, "nearest_safe", [23, 68]),
        ((506 + HOLD_SECONDS) * TICKS_PER_SECOND, "site_army", [23, 25]),
    ]
    assert mcv_orders(orders) == [{"cmd": "move", "units": [MCV_ID], "to": site}]
    assert len(holds(match_record)) == 1


def test_site_army_with_no_other_field_the_mcv_held_30_s_goes_home(match_record):
    agent = safe_site_agent(FIELD_18)
    on_its_way(agent, 505, MCV_NORTH)
    on_its_way(agent, 506, (22, 50), **enemy_group(*ARMY_1500))

    orders = on_its_way(agent, 506 + HOLD_SECONDS, (22, 50), **enemy_group(*ARMY_1500))

    home = expansion.home_site(agent.world, agent.ctx)
    assert site_reasons(match_record)[-1] == ((506 + HOLD_SECONDS) * TICKS_PER_SECOND, "home_fallback", None)
    assert mcv_orders(orders) == [{"cmd": "move", "units": [MCV_ID], "to": list(home)}]


def test_site_army_a_400_group_does_not_stop_the_mcv(match_record):
    agent = safe_site_agent(FIELD_18, FIELD_25)
    on_its_way(agent, 505, MCV_NORTH)

    # 100 ticks on, past the 75 within which the same move is not sent again.
    orders = on_its_way(agent, 509, (22, 50), **enemy_group(*ARMY_400))

    assert holds(match_record) == []
    assert mcv_orders(orders) == [{"cmd": "move", "units": [MCV_ID], "to": list(FIELD_18_SITE)}]


def test_site_army_an_mcv_at_its_site_never_deploys_while_the_group_stands_and_deploys_once_it_is_gone(match_record):
    agent = safe_site_agent(FIELD_18, FIELD_25)
    on_its_way(agent, 505, MCV_NORTH)

    held = on_its_way(agent, 520, FIELD_18_SITE, **enemy_group(*ARMY_1500))
    # Seen 10 s ago and more, the group no longer counts.
    remembered = on_its_way(agent, 530, FIELD_18_SITE)
    gone = on_its_way(agent, 531, FIELD_18_SITE)

    # 10 cells from the nearest tank, it backs off straight away from it to 16 cells.
    assert mcv_orders(held) == [{"cmd": "move", "units": [MCV_ID], "to": [14, 68]}]
    assert {"cmd": "deploy", "units": [MCV_ID]} not in mcv_orders(remembered)
    assert mcv_orders(gone) == [{"cmd": "deploy", "units": [MCV_ID]}]
    assert len(holds(match_record)) == 1


def test_site_army_a_deploy_out_is_dropped_when_the_group_comes_and_not_sent_again_while_it_stands(match_record):
    agent = safe_site_agent(FIELD_18, FIELD_25)
    on_its_way(agent, 505, MCV_NORTH)
    sent = on_its_way(agent, 520, FIELD_18_SITE)

    resend_due = [
        on_its_way(agent, seconds, FIELD_18_SITE, **enemy_group(*ARMY_1500, at=(20, 82))) for seconds in (521, 524, 527)
    ]

    assert mcv_orders(sent) == [{"cmd": "deploy", "units": [MCV_ID]}]
    assert all({"cmd": "deploy", "units": [MCV_ID]} not in mcv_orders(orders) for orders in resend_due)
    assert holds(match_record)[0][0] == 521 * TICKS_PER_SECOND
