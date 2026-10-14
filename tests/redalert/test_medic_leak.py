"""The medic of game 20261013T142816Z at 11:16: a crate's squad, not an order, and no path queues a medic.

In that game (England, medics_max 0) no order trained the medic: the record
holds no `train` order of `medi`, and no queue ever held one. At tick 16907
nine units appeared at one tick beside the scout jeep 295, at the end of its
retreat to (27, 28) and with no building near: four riflemen, three rocket
soldiers, an engineer and the medic. Crates are on in the match rules, and
that is the Allied squad a crate gives. The tests play that squad into a
whole fake match with the shipped tuning, the way the record shows it: units
created with no order. The medic it brings is the only one. None is trained
before it, after it, or after it is lost.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from fake_game import REPO_ROOT, FakeRedAlert, at_seconds
from ucla_agent.games.redalert.support import MECHANIC, MEDIC
from ucla_agent.games.redalert.tuning import Tuning
from whole_match import WholeMatch, play_whole_match

# The game time of the units' unit_created events in the record: tick 16907.
CRATE_AT_SECONDS = 676.28
# The crate medic is lost at 12:30, which leaves the match seven and a half minutes to replace it.
MEDIC_LOST_AT_SECONDS = 750
# The squad as the record lists it, with each unit's cell less the base's center (23, 44) of that game.
CRATE_SQUAD = (
    ("e1", 4, -16),
    ("e1", 3, -14),
    ("e1", 3, -16),
    ("e1", 3, -16),
    ("e3", 3, -14),
    ("e3", 3, -15),
    ("e3", 5, -15),
    ("e6", 5, -15),
    (MEDIC, 3, -14),
)


def trains_of(match: WholeMatch, item: str) -> list[dict[str, Any]]:
    """Every `train` order of an item the agent sent in the match."""
    return [
        order
        for batch in match.run.batches
        if isinstance(batch, dict)
        for order in batch.get("orders", [])
        if order["cmd"] == "train" and order["item"] == item
    ]


def play_crate_match(tuning: Tuning) -> tuple[WholeMatch, dict[str, Any]]:
    """A whole fake match for England under `tuning`, with the crate's squad at 11:16 and its medic lost at 12:30,
    and what was seen when the squad came."""
    seen: dict[str, Any] = {}

    def crate_squad(game: FakeRedAlert) -> None:
        center_x, center_y = game.state()["observation"]["base_center"]
        seen["medic_on_offer"] = MEDIC in game.state()["observation"]["available_production"]
        seen["ids"] = {game.add_own(item, center_x + dx, center_y + dy): item for item, dx, dy in CRATE_SQUAD}

    def medic_lost(game: FakeRedAlert) -> None:
        (medic_id,) = [unit_id for unit_id, item in seen["ids"].items() if item == MEDIC]
        game.kill_own(medic_id)

    scenario = [at_seconds(CRATE_AT_SECONDS, crate_squad), at_seconds(MEDIC_LOST_AT_SECONDS, medic_lost)]
    return play_whole_match(tuning, scenario=scenario), seen


@pytest.fixture(scope="module")
def crate_match() -> tuple[WholeMatch, dict[str, Any]]:
    """The crate match with the shipped tuning."""
    return play_crate_match(Tuning.from_file(REPO_ROOT))


def test_medic_leak_the_shipped_tuning_caps_medics_at_0_with_vehicle_repair_on():
    shipped = Tuning.from_file(REPO_ROOT)

    assert (shipped.composition.medics_max, shipped.combat.vehicle_repair) == (0, True)


def test_medic_leak_the_crate_squad_at_11_16_brings_a_medic_on_offer_and_no_medic_is_ever_trained(crate_match):
    match, seen = crate_match

    assert seen["medic_on_offer"] and sorted(seen["ids"].values()).count(MEDIC) == 1
    assert trains_of(match, MEDIC) == []


def test_medic_leak_the_support_rule_was_live_in_the_crate_match_it_trained_mechanics(crate_match):
    match, _ = crate_match

    assert trains_of(match, MECHANIC)
    assert match.data_of("error") == [] and match.run.invalid_actions == 0


def test_medic_leak_with_medics_max_2_the_same_crate_match_trains_a_medic():
    # The support rule's medic is the one path that trains a medic in this match: medics_max alone keeps it shut.
    shipped = Tuning.from_file(REPO_ROOT)
    medics_up_to_2 = dataclasses.replace(shipped, composition=dataclasses.replace(shipped.composition, medics_max=2))

    match, _ = play_crate_match(medics_up_to_2)

    assert trains_of(match, MEDIC)
