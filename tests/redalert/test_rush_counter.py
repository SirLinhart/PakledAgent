"""The rush counter: two infantry defenses on the verdict, the warning's reserve, infantry while short of tanks, main
held home, and the harvester crush."""

from __future__ import annotations

import dataclasses

import pytest

import situations
from situations import ask, base_state
from switches_off import new_agent, tuning_off
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.opening import OpeningManager
from ucla_agent.games.redalert.tuning import OpeningTuning, Tuning

YARD = ("fact", 22, 48)
POWER = ("powr", 19, 52)
BARRACKS = {"england": ("tent", 22, 44), "russia": ("barr", 22, 44)}
DEFENSE = {"england": "pbox", "russia": "ftur"}
WAR_FACTORY = ("weap", 15, 48)
# The opening as it ships, the rush counter among it, with the other groups' switches off.
SHIPPED_OPENING = tuning_off(opening=OpeningTuning())


def at(faction: str, seconds: float, *more: tuple[str, int, int], **observation) -> dict:
    """A base of a faction with its barracks at a game time, and the buildings the test adds."""
    standing = (YARD, POWER, BARRACKS[faction], *more)
    return base_state(faction, *standing, tick=round(seconds * TICKS_PER_SECOND), **observation)


def defenses(orders: list[dict]) -> list[str]:
    items = situations.tracked_items()
    return [
        order["item"] for order in situations.with_command(orders, "build") if items[order["item"]].queue == "Defense"
    ]


def counter_agent(verdict: str | None, tuning: Tuning = SHIPPED_OPENING):
    agent = new_agent(OpeningManager(), tuning=tuning)
    agent.ctx.rush_verdict = verdict
    return agent


def in_the_defense_queue(faction: str) -> list[dict]:
    return [situations.job("Defense", DEFENSE[faction])]


# -- the two defenses ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_the_verdict_unknown_at_150_s_with_no_threat_orders_no_defense(faction):
    agent = counter_agent("unknown")

    assert [defenses(ask(agent, at(faction, seconds))) for seconds in (150, 160)] == [[], []]
    assert agent.ctx.rush_counter_ordered is False


@pytest.mark.parametrize("faction", ["england", "russia"])
def test_on_the_rush_verdict_two_defenses_go_up_back_to_back_and_no_third(faction):
    agent = counter_agent("rush")
    item = DEFENSE[faction]

    first = ask(agent, at(faction, 220))
    building = ask(agent, at(faction, 225, production=in_the_defense_queue(faction)))
    second = ask(agent, at(faction, 240, (item, 26, 46)))
    no_third = ask(agent, at(faction, 250, (item, 26, 46), production=in_the_defense_queue(faction)))
    later = ask(agent, at(faction, 280, (item, 26, 46), (item, 26, 50)))

    assert [defenses(orders) for orders in (first, building, second, no_third, later)] == [[item], [], [item], [], []]
    assert agent.ctx.rush_counter_ordered is True


def test_with_one_defense_already_standing_the_counter_orders_one_more():
    agent = counter_agent("rush")

    one_more = ask(agent, at("england", 220, ("pbox", 26, 46)))
    done = ask(agent, at("england", 240, ("pbox", 26, 46), ("pbox", 26, 50)))

    assert (defenses(one_more), defenses(done)) == (["pbox"], [])


def test_a_threat_at_the_base_before_a_war_factory_calls_the_counter_whatever_the_threat():
    agent = counter_agent("none")
    agent.ctx.threat_active, agent.ctx.threat_level = True, "major"

    orders = ask(agent, at("england", 200))

    assert defenses(orders) == ["pbox"]


def test_a_threat_with_a_war_factory_standing_and_no_rush_does_not_call_the_counter():
    agent = counter_agent("none")
    agent.ctx.threat_active, agent.ctx.threat_level = True, "major"

    orders = ask(agent, at("england", 200, WAR_FACTORY))

    assert defenses(orders) == [] and agent.ctx.rush_counter_ordered is False


def test_with_no_barracks_standing_the_counter_orders_no_defense_and_does_not_fail():
    agent = counter_agent("rush")

    orders = ask(agent, base_state("england", YARD, POWER, tick=220 * TICKS_PER_SECOND))

    assert "pbox" not in agent.world.available_production and defenses(orders) == []
    assert agent.ctx.rush_counter_ordered is False


def test_with_rush_counter_defenses_0_the_rush_buys_the_opening_s_one_defense_as_before():
    switched_off = dataclasses.replace(SHIPPED_OPENING, opening=OpeningTuning(rush_counter_defenses=0))
    agent = counter_agent("rush", switched_off)

    first = ask(agent, at("england", 220))
    after = ask(agent, at("england", 240, ("pbox", 26, 46)))

    assert (defenses(first), defenses(after)) == (["pbox"], [])


# -- infantry trained against an infantry threat while main is short of tanks under a rush ---------------------------

TANK_ARMY = tuning_off(mix=dataclasses.replace(tuning_off().mix, main_force="tanks"))
TRAIN_RIFLES_3 = {"cmd": "train", "item": "e1", "count": 3}
# 20 enemy rifles worth 2,000 in a block of four columns 8 to 11 cells east of the construction yard.
TWENTY_RIFLES = [
    {"id": 500 + number, "type": "e1", "x": 30 + number % 4, "y": 46 + number // 4, "hp": 100} for number in range(20)
]


def main_of(tanks: int, jeeps: int) -> list[dict]:
    """main's light tanks and jeeps, side by side from (10, 60) behind the base."""
    kinds = ["1tnk"] * tanks + ["jeep"] * jeeps
    return [situations.unit(330 + number, kind, 10 + number, 60) for number, kind in enumerate(kinds)]


def counter_trains(verdict: str | None, units: list[dict]) -> list[dict]:
    """The train orders of base defense alone against the twenty rifles, with a war factory and main as given."""
    agent = new_agent(BaseDefenseManager(), tuning=TANK_ARMY)
    agent.ctx.rush_verdict = verdict
    orders = ask(agent, at("england", 230, WAR_FACTORY, units=units, visible_enemies=TWENTY_RIFLES))
    return situations.with_command(orders, "train")


@pytest.mark.parametrize(
    "verdict, tanks, jeeps, trained",
    [
        ("rush", 0, 4, [TRAIN_RIFLES_3]),
        ("rush", 2, 2, [TRAIN_RIFLES_3]),
        ("rush", 3, 1, []),
        ("none", 2, 2, []),
        (None, 0, 4, []),
    ],
    ids=["rush, 0 tanks", "rush, 2 tanks", "rush, 3 tanks", "no rush, 2 tanks", "no watch, 0 tanks"],
)
def test_under_a_rush_rifles_are_trained_against_infantry_while_main_has_fewer_than_3_tanks(
    verdict, tanks, jeeps, trained
):
    assert counter_trains(verdict, main_of(tanks, jeeps)) == trained


# -- main holds within 20 cells of base_center under a rush until 330 s -----------------------------------------------


class StubArmy:
    """A manager that proposes the orders a test gives it, as the combat manager or the commander would."""

    name = "combat"

    def __init__(self) -> None:
        self.proposed: list[Intent] = []

    def intents(self, world, ctx) -> list[Intent]:
        return list(self.proposed)


MAIN_IDS = [330, 331]
GUARD_TANK_ID = 340
ENEMY_FAR = {"id": 900, "type": "2tnk", "x": 60, "y": 48, "hp": 100}


def army_agent(verdict: str | None = "rush"):
    """An agent that plays the stub army alone, with two light tanks in main."""
    army = StubArmy()
    agent = new_agent(army, tuning=SHIPPED_OPENING)
    agent.ctx.rush_verdict = verdict
    return agent, army


def main_tanks(x: int = 24) -> list[dict]:
    return [situations.unit(unit_id, "1tnk", x, 50 + number) for number, unit_id in enumerate(MAIN_IDS)]


def go(cmd: str, units: list[int], **aim) -> Intent:
    return Intent(Priority.MOVE, {"cmd": cmd, "units": units, **aim})


@pytest.mark.parametrize(
    "seconds, verdict, sent",
    [(250, "rush", False), (329, "rush", False), (331, "rush", True), (250, "none", True), (250, None, True)],
    ids=["rush at 250 s", "rush at 329 s", "rush at 331 s", "no rush", "no watch"],
)
def test_under_a_rush_main_is_given_no_attack_away_from_the_base_until_330_s(seconds, verdict, sent):
    agent, army = army_agent(verdict)
    ask(agent, at("england", seconds - 1, units=main_tanks()))
    assert set(agent.ctx.groups.members("main")) == set(MAIN_IDS)
    army.proposed = [go("attack_move", MAIN_IDS, to=[70, 48])]

    orders = ask(agent, at("england", seconds, units=main_tanks()))

    assert ({"cmd": "attack_move", "units": MAIN_IDS, "to": [70, 48]} in orders) is sent


def test_while_main_holds_orders_within_20_cells_go_out_and_an_attack_on_a_far_enemy_does_not():
    agent, army = army_agent()
    ask(agent, at("england", 249, units=main_tanks()))
    army.proposed = [go("move", MAIN_IDS[:1], to=[40, 48]), go("attack", MAIN_IDS[1:], target=ENEMY_FAR["id"])]

    orders = ask(agent, at("england", 250, units=main_tanks(), visible_enemies=[ENEMY_FAR]))

    assert {"cmd": "move", "units": MAIN_IDS[:1], "to": [40, 48]} in orders
    assert [order for order in orders if order.get("target") == ENEMY_FAR["id"]] == []


def test_while_main_holds_a_unit_of_main_out_of_the_base_area_is_moved_back_and_others_keep_their_orders():
    agent, army = army_agent()
    guard = situations.unit(GUARD_TANK_ID, "e1", 22, 46)
    ask(agent, at("england", 249, units=[*main_tanks(50), guard]))
    agent.ctx.groups.assign(GUARD_TANK_ID, "guard")
    army.proposed = [go("attack_move", [*MAIN_IDS, GUARD_TANK_ID], to=[70, 48])]

    orders = ask(agent, at("england", 250, units=[*main_tanks(50), guard]))

    moves = situations.with_command(orders, "move", "attack_move")
    assert {"cmd": "attack_move", "units": [GUARD_TANK_ID], "to": [70, 48]} in moves
    assert [order["units"] for order in moves if order["cmd"] == "move"] == [MAIN_IDS]
    assert all(cells_away(order["to"]) <= 20 for order in moves if order["cmd"] == "move")


def cells_away(cell: list[int]) -> float:
    return ((cell[0] - 22) ** 2 + (cell[1] - 48) ** 2) ** 0.5
