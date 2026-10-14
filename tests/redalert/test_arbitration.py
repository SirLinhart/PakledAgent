"""The repeat filter: a movement or attack order a unit already holds is not sent to it again."""

from __future__ import annotations

import pytest

from ucla_agent.games.redalert.arbitration import MOVEMENT_COMMANDS, RepeatFilter
from ucla_agent.games.redalert.batch import Intent, Packed, Priority
from ucla_agent.games.redalert.items import ItemTable
from ucla_agent.games.redalert.ledger import OrderLedger
from ucla_agent.games.redalert.world import World

REISSUE_AFTER_TICKS = 75
SENT_TICK = 1000
GROUP = [201, 202, 203]
GOAL = [60, 48]


def world_at(tick: int, idle: tuple[int, ...] = (), unit_ids: tuple[int, ...] = (201, 202, 203, 204)) -> World:
    """A world at a tick whose own units are rifle infantry, idle where named."""
    world = World(ItemTable([]))
    units = [{"id": unit_id, "type": "e1", "x": 30, "y": 48, "idle": unit_id in idle} for unit_id in unit_ids]
    world.update({"phase": "playing", "observation": {"tick": tick, "units": units}})
    return world


def march(units: list[int], to: list[int] = GOAL, cmd: str = "attack_move", **fields) -> Intent:
    return Intent(Priority.MOVE, {"cmd": cmd, "units": units, "to": to, **fields})


class Sent:
    """A filter and the ledger whose recorded orders it is told of."""

    def __init__(self) -> None:
        self.ledger = OrderLedger()
        self.filter = RepeatFilter(REISSUE_AFTER_TICKS)

    def send(self, *intents: Intent, tick: int = SENT_TICK) -> None:
        """Record these intents as one batch sent at a tick, and tell the filter of it."""
        self.ledger.record(Packed(tuple(intents), (), 0), world_at(tick), tick)
        self.filter.note_sent(self.ledger.entries[-len(intents) :])

    def fresh(self, *intents: Intent, tick: int = SENT_TICK + 5, idle: tuple[int, ...] = ()) -> list[dict]:
        """The orders the filter lets through at a later tick."""
        return [intent.order for intent in self.filter.without_repeats(intents, world_at(tick, idle))]


@pytest.fixture
def sent() -> Sent:
    return Sent()


def test_the_orders_a_manager_repeats_are_move_attack_move_and_attack():
    assert MOVEMENT_COMMANDS == {"move", "attack_move", "attack"}


def test_with_nothing_sent_before_every_intent_passes_as_it_was_given(sent):
    intents = [march(GROUP), Intent(Priority.PRODUCTION, {"cmd": "build", "item": "powr"})]

    assert sent.filter.without_repeats(intents, world_at(SENT_TICK)) == intents


def test_an_attack_move_the_units_already_hold_is_not_sent_again_in_the_next_state(sent):
    sent.send(march(GROUP))

    assert sent.fresh(march(GROUP)) == []


@pytest.mark.parametrize("ticks_later, repeated", [(5, False), (74, False), (75, True), (300, True)])
def test_the_same_order_is_sent_again_once_75_ticks_have_passed(sent, ticks_later, repeated):
    sent.send(march(GROUP))

    orders = sent.fresh(march(GROUP), tick=SENT_TICK + ticks_later)

    assert orders == ([march(GROUP).order] if repeated else [])


def test_a_unit_that_stands_idle_is_ordered_again_and_the_marching_ones_are_not(sent):
    sent.send(march(GROUP))

    assert sent.fresh(march(GROUP), idle=(202,)) == [{"cmd": "attack_move", "units": [202], "to": GOAL}]


def test_a_unit_that_was_not_in_the_earlier_order_is_the_only_one_ordered(sent):
    sent.send(march(GROUP))

    assert sent.fresh(march([*GROUP, 204])) == [{"cmd": "attack_move", "units": [204], "to": GOAL}]


def test_narrowing_an_order_leaves_the_intent_it_came_from_unchanged(sent):
    sent.send(march(GROUP))
    again = march([*GROUP, 204])

    (narrowed,) = sent.filter.without_repeats([again], world_at(SENT_TICK + 5))

    assert again.order["units"] == [*GROUP, 204]
    assert (narrowed.priority, narrowed.resend, narrowed.order["units"]) == (Priority.MOVE, False, [204])


@pytest.mark.parametrize(
    "different",
    [
        march(GROUP, to=[61, 48]),
        march(GROUP, cmd="move"),
        march(GROUP, queued=True),
        Intent(Priority.CONTACT, {"cmd": "attack", "units": GROUP, "target": 900}),
    ],
    ids=["another cell", "another command", "queued", "an attack on a target"],
)
def test_an_order_that_says_something_else_is_sent(sent, different):
    sent.send(march(GROUP))

    assert sent.fresh(different) == [different.order]


def test_an_attack_on_the_same_target_is_not_repeated_and_on_another_target_is_sent(sent):
    sent.send(Intent(Priority.CONTACT, {"cmd": "attack", "units": GROUP, "target": 900}))

    same = Intent(Priority.CONTACT, {"cmd": "attack", "units": GROUP, "target": 900})
    other = Intent(Priority.CONTACT, {"cmd": "attack", "units": GROUP, "target": 901})
    assert sent.fresh(same, other) == [other.order]


def test_a_cell_given_as_a_tuple_and_as_a_list_is_the_same_cell(sent):
    sent.send(march(GROUP, to=[60, 48]))

    assert sent.fresh(march(GROUP, to=(60, 48))) == []


@pytest.mark.parametrize("verdict", ["rejected", "lost"])
def test_an_order_that_was_rejected_or_lost_is_sent_again_at_once(sent, verdict):
    sent.send(march(GROUP))
    sent.ledger.entries[-1].status = verdict

    assert sent.fresh(march(GROUP)) == [march(GROUP).order]


@pytest.mark.parametrize("verdict", ["pending", "confirmed", "partial", "confirmed_assumed"])
def test_an_order_that_may_be_in_force_is_not_sent_again(sent, verdict):
    sent.send(march(GROUP))
    sent.ledger.entries[-1].status = verdict

    assert sent.fresh(march(GROUP)) == []


def test_only_the_last_order_of_a_unit_counts(sent):
    sent.send(march(GROUP))
    sent.send(march([201], to=[20, 20], cmd="move"), tick=SENT_TICK + 5)

    orders = sent.fresh(march(GROUP), tick=SENT_TICK + 10)

    assert orders == [{"cmd": "attack_move", "units": [201], "to": GOAL}]


def test_an_order_that_gave_the_unit_something_else_to_do_ends_what_it_held(sent):
    sent.send(march(GROUP))
    sent.send(Intent(Priority.EMERGENCY, {"cmd": "stop", "units": [203]}), tick=SENT_TICK + 5)

    assert sent.fresh(march(GROUP), tick=SENT_TICK + 10) == [{"cmd": "attack_move", "units": [203], "to": GOAL}]


def test_a_change_of_stance_does_not_end_what_a_unit_holds(sent):
    sent.send(march(GROUP))
    sent.send(Intent(Priority.MOVE, {"cmd": "set_stance", "units": GROUP, "stance": "hold_fire"}), tick=SENT_TICK + 5)

    assert sent.fresh(march(GROUP), tick=SENT_TICK + 10) == []


def test_orders_that_are_no_movement_are_never_held_back(sent):
    harvest = Intent(Priority.PRODUCTION, {"cmd": "harvest", "units": [201]})
    stance = Intent(Priority.MOVE, {"cmd": "set_stance", "units": GROUP, "stance": "defend"})
    build = Intent(Priority.PRODUCTION, {"cmd": "build", "item": "powr"})
    sent.send(harvest, stance, build)

    assert sent.fresh(harvest, stance, build) == [harvest.order, stance.order, build.order]


def test_a_unit_that_is_no_longer_in_the_world_is_not_ordered_again_before_the_time(sent):
    sent.send(march(GROUP))

    kept = sent.filter.without_repeats([march(GROUP)], world_at(SENT_TICK + 5, unit_ids=(201,)))

    assert kept == []


def test_commander_slot_the_commander_s_order_is_left_as_it_is_whatever_was_sent_before(sent):
    sent.send(march(GROUP))
    commanded = Intent(Priority.MOVE, march(GROUP).order, commander=True)

    assert sent.filter.without_repeats([commanded], world_at(SENT_TICK + 5)) == [commanded]
