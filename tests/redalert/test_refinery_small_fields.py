"""An unserved field smaller than an expansion's minimum takes a refinery while it holds a mine or 20 ore cells
(`[placement] refinery_small_fields`)."""

from __future__ import annotations

import dataclasses

import situations
from situations import ask, base_state
from ucla_agent.games.redalert import refinery_fields
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.tuning import PlacementTuning, Tuning

SHIPPED = Tuning()
OFF = dataclasses.replace(SHIPPED, placement=dataclasses.replace(SHIPPED.placement, refinery_small_fields=False))
BASE = (("fact", 22, 48), ("powr", 19, 52), ("tent", 18, 44))


def field(mines: int, width: int = 3) -> OrePatch:
    """A field of `width` x 3 cells 7 cells east of the yard, within its build reach."""
    return OrePatch(
        center=(30, 48), cells=frozenset((x, y) for x in range(29, 29 + width) for y in (47, 48, 49)), mines=mines
    )


def chosen(patch: OrePatch, tuning: Tuning = SHIPPED):
    agent = situations.new_agent(tuning=tuning)
    ask(agent, base_state("england", *BASE))
    agent.ctx.terrain = Terrain([patch], patch.cells)
    return refinery_fields.choose(agent.world, agent.ctx)


def test_a_small_field_with_a_mine_takes_a_refinery():
    choice = chosen(field(mines=1))

    assert choice is not None and choice.field.patch.center == (30, 48) and choice.field.rule == "unserved"


def test_without_the_switch_the_small_field_waits_for_ever():
    assert chosen(field(mines=1), OFF) is None


def test_a_small_field_without_a_mine_takes_one_from_20_cells():
    assert chosen(field(mines=0)) is None
    assert chosen(field(mines=0, width=7)) is not None


def test_tuning_refinery_small_fields_shipped_on():
    for tuning in (Tuning.from_file(situations.REPO_ROOT).placement, PlacementTuning()):
        assert tuning.refinery_small_fields is True and tuning.refinery_small_field_cells == 20
