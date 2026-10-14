"""The hostile-interface scenario: 1,500 steps of a match with no enemy, in which the platform answers badly.

Nothing attacks and nothing is attacked. What the scenario tests is how the
agent copes with the answers a real platform gives now and then:

- the first batch sent at or after step 100, and again at or after step 400,
  is lost: answered as usual, carried out never, its orders `lost` in the next
  state;
- from step 250 the Infantry queue answers the next three production orders
  with `not_ready`, as a full queue does;
- the first two placements of a power plant are answered `invalid`, as when
  the game finds no free cell;
- `e3` leaves `available_production` at step 300, for the rest of the match;
- five event numbers are skipped at step 500, as when events scroll out of
  the window between two states;
- from step 200, the first unit ordered to `move` is taken off the map in the
  step that follows that order: gone, not killed, so no loss is reported;
- a `low_power` event is reported at step 600 although the power is fine.
"""

from __future__ import annotations

from typing import Any

from fake_game import TICKS_PER_STEP, FakeRedAlert, ScenarioStep, at

STEPS = 1500
LOST_BATCH_STEPS = (100, 400)
QUEUE_FULL_STEP = 250
QUEUE_FULL_QUEUE = "Infantry"
QUEUE_FULL_ANSWERS = 3
REFUSED_PLACE_ITEM = "powr"
REFUSED_PLACE_ANSWERS = 2
WITHDRAWN_STEP = 300
WITHDRAWN_ITEM = "e3"
SKIPPED_EVENTS_STEP = 500
SKIPPED_EVENTS = 5
FALSE_LOW_POWER_STEP = 600
VANISHING_FROM_STEP = 200
MOVE = "move"


class HostileInterfaceScenario:
    """The hostile-interface scenario for one match. See the module's note."""

    def __init__(self) -> None:
        # The unit taken off the map and the game tick it went at; None until it has gone.
        self.vanished: tuple[int, int] | None = None
        # How many of the game's carried-out orders the vanishing rule has looked at.
        self._orders_seen = 0

    def new_game(self, faction: str) -> FakeRedAlert:
        """A fake match for a start of `faction` that plays out this scenario."""
        game = FakeRedAlert(faction, scenario=self.steps())
        game.on_step(self.vanish_a_moving_unit)
        return game

    def steps(self) -> list[ScenarioStep]:
        """The timed steps of the scenario; the vanishing unit is the per-step callback's."""
        return [
            at(0, lambda game: game.refuse_place(REFUSED_PLACE_ITEM, REFUSED_PLACE_ANSWERS)),
            *(at(step, lambda game: game.lose_batch()) for step in LOST_BATCH_STEPS),
            at(QUEUE_FULL_STEP, _fill_the_queue),
            at(WITHDRAWN_STEP, lambda game: game.remove_available(WITHDRAWN_ITEM)),
            at(SKIPPED_EVENTS_STEP, lambda game: game.skip_event_seq(SKIPPED_EVENTS)),
            at(FALSE_LOW_POWER_STEP, _report_low_power),
        ]

    def vanish_a_moving_unit(self, game: FakeRedAlert) -> None:
        """The per-step callback: once, take off the map a unit that the batch of the last state ordered to move."""
        carried_out = game.recorded_orders[self._orders_seen :]
        self._orders_seen = len(game.recorded_orders)
        if self.vanished is not None or game.tick < VANISHING_FROM_STEP * TICKS_PER_STEP:
            return
        standing = {unit["id"] for unit in game.own_units()}
        ordered_to_move = (
            unit_id for record in carried_out if _is_move(record) for unit_id in record["order"]["units"]
        )
        unit_id = next((unit_id for unit_id in ordered_to_move if unit_id in standing), None)
        if unit_id is not None:
            game.remove_own(unit_id)
            self.vanished = (unit_id, game.tick)


def _is_move(record: dict[str, Any]) -> bool:
    return record["order"]["cmd"] == MOVE


def _fill_the_queue(game: FakeRedAlert) -> None:
    for _ in range(QUEUE_FULL_ANSWERS):
        game.refuse_production(QUEUE_FULL_QUEUE)


def _report_low_power(game: FakeRedAlert) -> None:
    economy = game.state()["observation"]["economy"]
    game.emit_event("low_power", power_provided=economy["power_provided"], power_drained=economy["power_drained"])
