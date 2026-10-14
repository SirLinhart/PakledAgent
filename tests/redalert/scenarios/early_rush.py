"""The early-rush scenario: a whole 20-minute match against the standard enemy base, rushed early by its rifles.

The match is played on the fake game's map, with the own spawn at (22, 48)
and the enemy's at (74, 48), and the enemy base of the standard scenario
stands from the first state. At 80 s eight `e1` set out from 16 cells in
front of the enemy's start, at (58, 48), and walk west along the line
between the two starts, one cell every 2 seconds, until they stand 10 cells
east of the own construction yard; the platform then reports the yard under
attack. They are seen all the way, as a unit standing on their way would
see them. They stand there for 20 seconds and are gone.

Nothing shoots in the fake game, so the rush tests whether the base is
ready when it arrives, not how the fight goes. When it arrives, the scenario
notes which defenses the base has standing.

The match ends at 1,200 s by the time limit, won by the own seat.
"""

from __future__ import annotations

from fake_game import CONSTRUCTION_YARD, TICKS_PER_SECOND, FakeRedAlert, ScenarioStep, at_seconds, result_object
from scenarios.standard import ENEMY_BASE, ENEMY_FACTION, MATCH_SECONDS, OWN_WIN, TIME_LIMIT

RUSH_TYPE = "e1"
RUSH_COUNT = 8
SETS_OUT_SECONDS = 80
SET_OUT_CELL = (58, 48)
# The rush walks one cell west every this many steps of the game ...
STEPS_PER_CELL = 10
# ... until it stands this many cells east of the own construction yard ...
ARRIVAL_CELLS_EAST = 10
# ... and stays there this long.
STAYS_SECONDS = 20
# The defenses the scenario looks for when the rush arrives.
DEFENSE_TYPES = frozenset({"pbox", "ftur", "gun", "tsla"})


def _construction_yard(game: FakeRedAlert) -> dict:
    """The own construction yard; the agent deploys its MCV in the first state, long before the rush."""
    return next(building for building in game.own_buildings() if building["type"] == CONSTRUCTION_YARD)


class EarlyRushScenario:
    """The early-rush scenario for one match. See the module's note."""

    def __init__(self) -> None:
        # The ids of the rushing rifles once they are on the map.
        self.ids: list[int] = []
        # The game tick the rush stood 10 cells from the yard at, and the types of the own defenses standing then.
        self.arrived_tick: int | None = None
        self.defenses_at_arrival: list[str] = []
        self._steps_marched = 0
        self._is_gone = False

    def new_game(self, faction: str) -> FakeRedAlert:
        """A fake match for a start of `faction` that plays out this scenario."""
        game = FakeRedAlert(faction, ENEMY_FACTION[faction], scenario=self.steps())
        game.on_step(self.march)
        return game

    def steps(self) -> list[ScenarioStep]:
        """The timed steps: the enemy base, the rush setting out, and the end; the march is the per-step callback's."""
        return [
            at_seconds(0, self._build_enemy_base),
            at_seconds(SETS_OUT_SECONDS, self._set_out),
            at_seconds(MATCH_SECONDS, lambda game: game.end_match(result_object(OWN_WIN, TIME_LIMIT))),
        ]

    def march(self, game: FakeRedAlert) -> None:
        """The per-step callback: walk the rush toward the yard, note the base's defenses when it arrives, report the
        yard under attack, and take the rush off the map once it has stood there long enough."""
        if not self.ids or self._is_gone:
            return
        if self.arrived_tick is None:
            self._walk(game)
        elif game.tick >= self.arrived_tick + STAYS_SECONDS * TICKS_PER_SECOND:
            for enemy_id in self.ids:
                game.remove_enemy(enemy_id)
            self._is_gone = True

    def _walk(self, game: FakeRedAlert) -> None:
        """One step of the march: a cell further west every STEPS_PER_CELL steps, until the rush has arrived."""
        self._steps_marched += 1
        if self._steps_marched % STEPS_PER_CELL:
            return
        yard = _construction_yard(game)
        x = SET_OUT_CELL[0] - self._steps_marched // STEPS_PER_CELL
        for enemy_id in self.ids:
            game.move_enemy(enemy_id, x, SET_OUT_CELL[1])
        if x <= yard["x"] + ARRIVAL_CELLS_EAST:
            self.arrived_tick = game.tick
            standing = [building["type"] for building in game.own_buildings()]
            self.defenses_at_arrival = [building_type for building_type in standing if building_type in DEFENSE_TYPES]
            game.emit_under_attack(yard["id"])

    def _set_out(self, game: FakeRedAlert) -> None:
        self.ids = [game.place_enemy(RUSH_TYPE, *SET_OUT_CELL, visible=True) for _ in range(RUSH_COUNT)]

    @staticmethod
    def _build_enemy_base(game: FakeRedAlert) -> None:
        for actor_type, x, y in ENEMY_BASE:
            game.place_enemy(actor_type, x, y)
