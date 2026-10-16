"""The refinery a new yard claims comes first (`[expansion] new_yard_refinery_first`): the military focus does not hold
its claim back, and the hold for its money does not time out."""

from __future__ import annotations

import dataclasses

import situations
from situations import ask, base_state
from ucla_agent.games.redalert import upkeep
from ucla_agent.games.redalert.economy_hold import EconomyHold, EconomyItem
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MILITARY_FOCUS
from ucla_agent.games.redalert.terrain import OrePatch
from ucla_agent.games.redalert.tuning import ExpansionTuning, Tuning

SHIPPED = Tuning()
OFF = dataclasses.replace(SHIPPED, expansion=dataclasses.replace(SHIPPED.expansion, new_yard_refinery_first=False))
# The expansion yard of the record at (14, 59), its field by (9, 61).
FIELD = OrePatch(center=(9, 61), cells=frozenset((x, y) for x in (8, 9, 10) for y in (60, 61, 62)))
BASE = (("fact", 22, 48), ("powr", 19, 52), ("tent", 18, 44), ("proc", 26, 52), ("weap", 26, 44), ("fact", 14, 59))


def claiming(tuning: Tuning = SHIPPED):
    agent = situations.new_agent(tuning=tuning)
    ask(agent, base_state("england", *BASE, cash=200))
    ctx = agent.ctx
    ctx.new_yard_field, ctx.refinery_claim_due, ctx.spending_focus = FIELD, True, MILITARY_FOCUS
    return agent


def test_under_the_military_focus_the_new_yards_refinery_is_still_due():
    on, off = claiming(), claiming(OFF)

    assert upkeep._claim_refinery_due(on.world, on.ctx, has_place=True) == EconomyItem("proc", False)
    assert upkeep._claim_refinery_due(off.world, off.ctx, has_place=True) is None


def test_the_hold_for_the_new_yards_refinery_does_not_time_out():
    tuning, due = SHIPPED.upkeep, [EconomyItem("proc", False)]
    untimed, timed = EconomyHold(), EconomyHold()
    for hold, items in ((untimed, frozenset({"proc"})), (timed, frozenset())):
        hold.holds(due, lambda item: False, 1000, tuning, untimed=items)
    later = 1000 + (tuning.economy_wait_seconds + 5) * TICKS_PER_SECOND

    assert untimed.holds(due, lambda item: False, later, tuning, untimed=frozenset({"proc"})) is True
    assert timed.holds(due, lambda item: False, later, tuning) is False


def test_tuning_new_yard_refinery_first_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).expansion, ExpansionTuning()):
        assert tuning.new_yard_refinery_first is True
