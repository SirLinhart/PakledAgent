"""Coverage: a cell within a remembered enemy defense's range plus 2 cells is kept out of."""

from __future__ import annotations

import pytest

import situations
from ucla_agent.games.redalert.coverage import is_covered, reach
from ucla_agent.games.redalert.items import Item, ItemTable
from ucla_agent.games.redalert.tuning import CoverageTuning
from ucla_agent.games.redalert.world import EnemyRecord

TUNING = CoverageTuning()


def defense(defense_type: str, x: int = 60, y: int = 40) -> EnemyRecord:
    """A remembered enemy defense, out of sight."""
    return EnemyRecord(id=900, type=defense_type, x=x, y=y, hp=100, is_building=True, last_seen_tick=0, visible=False)


@pytest.mark.parametrize(
    "defense_type, expected",
    [("pbox", 8.0), ("ftur", 7.0), ("gun", 8.5), ("tsla", 9.0)],
)
def test_a_defense_covers_its_range_from_the_item_table_plus_two_cells(defense_type, expected):
    assert reach(defense_type, situations.tracked_items(), TUNING) == expected


def test_a_defense_the_table_gives_no_range_for_covers_seven_cells_plus_two():
    items = ItemTable([Item("pbox", True, cost=600)])

    assert reach("pbox", items, TUNING) == 9
    assert reach("unknown", items, TUNING) == 9


def test_the_margin_and_the_unknown_range_are_the_tuning_s():
    tuning = CoverageTuning(margin_cells=0, unknown_range_cells=4)

    assert reach("pbox", situations.tracked_items(), tuning) == 6.0
    assert reach("unknown", situations.tracked_items(), tuning) == 4


@pytest.mark.parametrize(
    "cell, covered",
    [((52, 40), True), ((51, 40), False), ((60, 48), True), ((60, 49), False), ((66, 45), True), ((66, 46), False)],
    ids=["8 cells west", "9 cells west", "8 cells south", "9 cells south", "7.8 cells away", "8.5 cells away"],
)
def test_a_cell_within_eight_cells_of_a_pillbox_is_covered_and_one_beyond_is_not(cell, covered):
    assert is_covered(cell, [defense("pbox")], situations.tracked_items(), TUNING) is covered


def test_a_cell_is_covered_when_any_one_of_the_defenses_covers_it():
    defenses = [defense("pbox", 10, 10), defense("gun", 30, 40)]

    assert is_covered((38, 40), defenses, situations.tracked_items(), TUNING)
    assert not is_covered((39, 40), defenses, situations.tracked_items(), TUNING)


def test_no_cell_is_covered_without_a_remembered_defense():
    assert not is_covered((60, 40), [], situations.tracked_items(), TUNING)
