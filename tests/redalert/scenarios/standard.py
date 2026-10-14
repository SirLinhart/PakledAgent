"""The standard scenario: a whole 20-minute match against a small enemy base that attacks twice.

The match is played on the fake game's map, with the own spawn at (22, 48)
and the enemy's at (74, 48). An enemy base stands from the first state and is
seen only under the fake game's sight rule (an own actor within 8 cells): a
construction yard, a power plant, a barracks, one pillbox, two refineries and
two harvesters. The pillbox (range 6) covers what lies within 8 cells of
(74, 51); the harvesters and refineries lie outside that.

Twice an enemy force stands for a while 10 cells east of the own construction
yard, and the platform reports the yard under attack a second later: eight
`e1` from 90 s to 110 s, four `2tnk` from 300 s to 330 s. Nothing shoots in
the fake game, so the attacks test how fast the agent answers, not how the
fight goes.

The fake game has no combat either, so the scenario decides when a raid has
destroyed a harvester: once at least 3 units of the agent's `raid` group have
stood within 5 cells of a visible enemy harvester for 10 consecutive steps,
the harvester is taken off the map, an `enemy_killed` event is reported and
`kills_cost` rises by the harvester's 1,100. Each harvester dies at most once.
Which units are in the raid group is the agent's own bookkeeping, so the
scenario is given a function that names them.

The match ends at 1,200 s by the time limit, won by the own seat.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Iterable

from fake_game import (
    CONSTRUCTION_YARD,
    HARVESTER,
    STEPS_PER_SECOND,
    FakeRedAlert,
    ScenarioStep,
    at_seconds,
    result_object,
)

MATCH_SECONDS = 1200
STEPS = MATCH_SECONDS * STEPS_PER_SECOND
# The enemy base: (type, x, y) of every enemy actor that stands from the first state.
ENEMY_BASE = (
    ("fact", 74, 48),
    ("powr", 78, 48),
    ("tent", 78, 52),
    ("pbox", 74, 51),
    ("proc", 72, 42),
    ("proc", 76, 42),
    (HARVESTER, 74, 40),
    (HARVESTER, 76, 40),
)
DEFENSE_CELL = (74, 51)
# The enemy faction for each own faction: the other family.
ENEMY_FACTION = {"england": "russia", "russia": "england"}
# Attackers stand this many cells east of the own construction yard.
ATTACK_CELLS_EAST = 10
UNDER_ATTACK_DELAY_SECONDS = 1
# A harvester is destroyed once this many raid units have stood this close to it, seen, for this many steps.
RAIDERS_NEEDED = 3
RAID_REACH_CELLS = 5
RAID_STEPS_NEEDED = 10
HARVESTER_KILL_VALUE = 1100
OWN_WIN = 1.0
TIME_LIMIT = "time_limit"


@dataclass
class Attack:
    """One scripted attack on the base: what comes and when, and the ids it got once it stood on the map."""

    unit_type: str
    count: int
    arrives_seconds: float
    leaves_seconds: float
    ids: list[int] = field(default_factory=list)


def _construction_yard(game: FakeRedAlert) -> dict:
    """The own construction yard; the agent deploys its MCV in the first state, long before any attack."""
    return next(building for building in game.own_buildings() if building["type"] == CONSTRUCTION_YARD)


def _distance(first: dict, second: dict) -> float:
    return math.hypot(first["x"] - second["x"], first["y"] - second["y"])


class StandardScenario:
    """The standard scenario for one match. See the module's note."""

    def __init__(self, raid_members: Callable[[], Iterable[int]]) -> None:
        """`raid_members` gives the ids of the units the agent has in its `raid` group at the moment it is called."""
        self._raid_members = raid_members
        self.attacks = (Attack("e1", 8, 90, 110), Attack("2tnk", 4, 300, 330))
        # Enemy harvester id -> the game tick it was destroyed at.
        self.kills: dict[int, int] = {}
        # Enemy harvester id -> how many steps in a row the raid has stood by it, seen.
        self._raided_steps: dict[int, int] = {}

    def new_game(self, faction: str) -> FakeRedAlert:
        """A fake match for a start of `faction` that plays out this scenario."""
        game = FakeRedAlert(faction, ENEMY_FACTION[faction], scenario=self.steps())
        game.on_step(self.raid_kills)
        return game

    def steps(self) -> list[ScenarioStep]:
        """The timed steps: the enemy base, the two attacks with their `under_attack` events, and the end."""
        timed = [at_seconds(0, self._build_enemy_base)]
        for attack in self.attacks:
            timed += [
                at_seconds(attack.arrives_seconds, lambda game, attack=attack: self._arrive(game, attack)),
                at_seconds(attack.arrives_seconds + UNDER_ATTACK_DELAY_SECONDS, self._report_yard_attacked),
                at_seconds(attack.leaves_seconds, lambda game, attack=attack: self._leave(game, attack)),
            ]
        timed.append(at_seconds(MATCH_SECONDS, lambda game: game.end_match(result_object(OWN_WIN, TIME_LIMIT))))
        return timed

    def raid_kills(self, game: FakeRedAlert) -> None:
        """The per-step callback: destroy each harvester the raid has stood by, seen, for long enough."""
        raiders = set(self._raid_members())
        raid_units = [unit for unit in game.own_units() if unit["id"] in raiders]
        for harvester in game.enemies():
            if harvester["type"] != HARVESTER:
                continue
            near = sum(1 for unit in raid_units if _distance(unit, harvester) <= RAID_REACH_CELLS)
            raided = harvester["visible"] and near >= RAIDERS_NEEDED
            self._raided_steps[harvester["id"]] = self._raided_steps.get(harvester["id"], 0) + 1 if raided else 0
            if self._raided_steps[harvester["id"]] >= RAID_STEPS_NEEDED:
                self._destroy(game, harvester)

    def _destroy(self, game: FakeRedAlert, harvester: dict) -> None:
        game.remove_enemy(harvester["id"])
        game.emit_event("enemy_killed", id=harvester["id"], type=HARVESTER, x=harvester["x"], y=harvester["y"])
        game.add_kills(HARVESTER_KILL_VALUE)
        self.kills[harvester["id"]] = game.tick

    @staticmethod
    def _build_enemy_base(game: FakeRedAlert) -> None:
        for actor_type, x, y in ENEMY_BASE:
            game.place_enemy(actor_type, x, y)

    @staticmethod
    def _arrive(game: FakeRedAlert, attack: Attack) -> None:
        yard = _construction_yard(game)
        cell = (yard["x"] + ATTACK_CELLS_EAST, yard["y"])
        attack.ids = [game.place_enemy(attack.unit_type, *cell) for _ in range(attack.count)]

    @staticmethod
    def _report_yard_attacked(game: FakeRedAlert) -> None:
        game.emit_under_attack(_construction_yard(game)["id"])

    @staticmethod
    def _leave(game: FakeRedAlert, attack: Attack) -> None:
        for enemy_id in attack.ids:
            game.remove_enemy(enemy_id)
