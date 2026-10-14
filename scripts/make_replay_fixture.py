"""Generate the long Red Alert replay that the latency tests replay.

    python scripts/make_replay_fixture.py [seed]

`build(seed)` plays one match of the fake game (`tests/redalert/fake_game.py`)
with a fixed build list and returns every state of it: more than 3,000
states, the last of them with more than 150 own units. Nothing is written to
disk. The replay is generated again in every test session (the
`long_match_states` fixture) and is never committed, because it would be
tens of megabytes of a file that the script reproduces exactly from its seed.

The player here is not the agent: it reads only the lists of legal actions,
builds the list below in order, keeps the barracks and the war factory busy
and sends its idle army back and forth between its base and the enemy's, so
that the states show a growing base, a moving army and, at times, enemies in
sight. The enemy side is a scripted scenario: a base at the enemy's start and
one raid on the player's own base.

Run as a program it generates the replay and prints how many states it has
and the largest number of own units in any of them.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from altruagent import WAIT, DecisionContext, GameState
from ucla_agent.core.console import print_line

FAKE_GAME_FOLDER = Path(__file__).resolve().parents[1] / "tests" / "redalert"
DEFAULT_SEED = 7
# 640 seconds of game time: enough for the army to pass 150 units and to cross the map twice.
MATCH_STEPS = 3200
FACTION = "england"
ENEMY_FACTION = "russia"
# What is built, in order, in each of the two queues that make buildings.
BUILD_LISTS = {"Building": ("powr", "tent", "proc", "proc", "powr", "weap", "powr"), "Defense": ("pbox", "gun")}
INFANTRY = "e1"
INFANTRY_PER_ORDER = 10
VEHICLE = "1tnk"
HARVESTER = "harv"
HARVESTERS_WANTED = 4
# Training stops here, so that the last states hold a steady army a little above the 150 the tests need.
OWN_UNITS_WANTED = 170
# The idle army is sent off every 50 seconds of game time, in orders of at most 50 units.
MARCH_EVERY_TICKS = 1250
UNITS_PER_ORDER = 50
# Three orders of 50 leave room under the platform's 200 engine commands for the production orders.
MARCH_ORDERS = 3
STAGING_CELL = [32, 48]

ENEMY_BASE = (
    ("fact", 74, 48),
    ("powr", 78, 48),
    ("barr", 78, 52),
    ("proc", 72, 42),
    ("weap", 70, 52),
    ("ftur", 70, 48),
    ("ftur", 72, 54),
    ("tsla", 76, 44),
)
ENEMY_GARRISON = (("e1", 12), ("e2", 6), ("3tnk", 4), ("harv", 2))
RAID_SECONDS = (300, 340)
RAID = (("e1", 16), ("e2", 8), ("3tnk", 6))
RAID_CELL = (30, 44)
# Scripted enemy groups stand in rows of this many, one cell apart.
ROW_LENGTH = 6


def _fake_game() -> ModuleType:
    """The fake game module. It is test code and lives with the tests, outside every installed package."""
    if str(FAKE_GAME_FOLDER) not in sys.path:
        sys.path.insert(0, str(FAKE_GAME_FOLDER))
    return importlib.import_module("fake_game")


def _place_group(game: Any, group: tuple[tuple[str, int], ...], corner: tuple[int, int]) -> list[int]:
    """Put a scripted enemy group on the map in rows, starting at a corner cell, and return the ids."""
    types = [unit_type for unit_type, count in group for _ in range(count)]
    return [
        game.place_enemy(unit_type, corner[0] + position % ROW_LENGTH, corner[1] + position // ROW_LENGTH)
        for position, unit_type in enumerate(types)
    ]


def _scenario(fake_game: ModuleType) -> list[Any]:
    """The enemy's side of the match: a base with a garrison from the start, and one raid that comes and goes."""
    raiders: list[int] = []

    def set_up_base(game: Any) -> None:
        for building_type, x, y in ENEMY_BASE:
            game.place_enemy(building_type, x, y)
        _place_group(game, ENEMY_GARRISON, (ENEMY_BASE[0][1] - 2, ENEMY_BASE[0][2] + 2))

    def start_raid(game: Any) -> None:
        raiders.extend(_place_group(game, RAID, RAID_CELL))
        yard = next(building for building in game.own_buildings() if building["type"] == "fact")
        game.emit_under_attack(yard["id"])

    def end_raid(game: Any) -> None:
        for enemy_id in raiders:
            game.remove_enemy(enemy_id)

    return [
        fake_game.at(0, set_up_base),
        fake_game.at_seconds(RAID_SECONDS[0], start_raid),
        fake_game.at_seconds(RAID_SECONDS[1], end_raid),
    ]


class BuildListPlayer:
    """Plays the fixed build list from each state's legal-action lists, and marches its idle army now and then."""

    def __init__(self) -> None:
        self._to_build = {queue: list(items) for queue, items in BUILD_LISTS.items()}
        self._marches = 0

    def choose_action(self, state: GameState, _context: DecisionContext) -> Any:
        """The batch for one state, or `WAIT` when there is nothing to order."""
        raw = state.raw
        orders = [*self._base_orders(raw), *self._production_orders(raw), *self._march_orders(raw)]
        return {"type": "orders", "orders": orders} if orders else WAIT

    def _base_orders(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        legal = raw["legal_actions"]
        orders: list[dict[str, Any]] = [{"cmd": "deploy", "units": legal["deploy"]}] if legal["deploy"] else []
        orders += [{"cmd": "place", "item": item} for item in legal["place"]]
        for queue, items in self._to_build.items():
            if items and items[0] in legal["build"] and not _is_busy(raw, queue):
                orders.append({"cmd": "build", "item": items.pop(0)})
        return orders

    @staticmethod
    def _production_orders(raw: dict[str, Any]) -> list[dict[str, Any]]:
        legal, observation = raw["legal_actions"], raw["observation"]
        army_is_complete = len(observation["units"]) >= OWN_UNITS_WANTED
        orders = []
        if INFANTRY in legal["train"] and not army_is_complete and not _is_busy(raw, "Infantry"):
            orders.append({"cmd": "train", "item": INFANTRY, "count": INFANTRY_PER_ORDER})
        if VEHICLE in legal["train"] and not _is_busy(raw, "Vehicle"):
            if len(legal["harvesters"]) < HARVESTERS_WANTED:
                orders.append({"cmd": "train", "item": HARVESTER})
            elif not army_is_complete:
                orders.append({"cmd": "train", "item": VEHICLE})
        return orders

    def _march_orders(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """Every 50 seconds: the idle fighters attack-move to the enemy's start, and the time after to the staging cell."""
        idle = raw["legal_actions"]["combat_units"]
        if raw["state_version"] % MARCH_EVERY_TICKS or not idle:
            return []
        self._marches += 1
        goal = raw["legal_actions"]["enemy_base"] if self._marches % 2 else STAGING_CELL
        groups = [idle[start : start + UNITS_PER_ORDER] for start in range(0, len(idle), UNITS_PER_ORDER)]
        return [{"cmd": "attack_move", "units": group, "to": goal} for group in groups[:MARCH_ORDERS]]


def _is_busy(raw: dict[str, Any], queue: str) -> bool:
    return any(job["queue"] == queue for job in raw["observation"]["production"])


def play(seed: int = DEFAULT_SEED, steps: int = MATCH_STEPS) -> Any:
    """Play the scripted match for `steps` states and return the fake game's account of the run, states included."""
    fake_game = _fake_game()
    game = fake_game.FakeRedAlert(FACTION, ENEMY_FACTION, scenario=_scenario(fake_game), seed=seed)
    return game.run(BuildListPlayer(), steps, keep_states=True)


def build(seed: int = DEFAULT_SEED) -> list[dict[str, Any]]:
    """Every state of the scripted match, first to last, generated in memory. The same seed gives the same states."""
    return play(seed).states


def peak_own_units(states: list[dict[str, Any]]) -> int:
    """The largest number of own units in any of the states."""
    return max(len(state["observation"]["units"]) for state in states)


def main(arguments: list[str]) -> int:
    """Generate the replay for the seed given on the command line and print its size."""
    summary = __doc__.split("\n\n")[0]
    parser = argparse.ArgumentParser(prog="python scripts/make_replay_fixture.py", description=summary)
    parser.add_argument("seed", nargs="?", type=int, default=DEFAULT_SEED, help="the seed of the fake game")
    seed = parser.parse_args(arguments).seed
    states = build(seed)
    print_line(f"{len(states)} states, peak of {peak_own_units(states)} own units (seed {seed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
