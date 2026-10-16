"""Scenes for the tests of the slot scouts' own attacks: the bait shot, the stray-scout hunt and the base harass.

Each scene is an agent of the scouting manager alone whose two scout slots are held by jeeps (Allies, a war factory
standing) or by dogs or riflemen taken straight into the slots, on the fake arena of 96 by 96 cells: our base at
(22, 48), the enemy's start at (74, 48).
"""

from __future__ import annotations

import dataclasses
from typing import Any

import situations
from situations import ask, base_state
from switches_off import new_agent, scouting_off, tuning_off
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.tuning import Tuning

YARD = ("fact", 22, 48)
POWER = ("powr", 19, 52)
BARRACKS = ("tent", 18, 44)
ALLIED_WITH_WAR_FACTORY = (
    YARD,
    POWER,
    ("powr", 16, 52),
    BARRACKS,
    ("proc", 26, 54),
    ("proc", 30, 54),
    ("weap", 28, 44),
)
SOVIET_BASE = (YARD, POWER, ("powr", 16, 52), ("barr", 18, 44), ("kenn", 14, 44))
# The jeeps that hold the two slots once the riflemen they replace have gone to main, and the tick they took them.
JEEP, OTHER_JEEP = 400, 401
JEEPS_TICK = 1005
# Where the other jeep waits, out of every scene's way.
OTHER_JEEP_AT = (28, 48)
# The careful slot scouts, every one of their own attacks off unless a test switches it on.
CAUTIOUS = dataclasses.replace(tuning_off(), scouting=scouting_off(scout_slots=True, scout_cautious=True))


def careful(**scouting: Any) -> Tuning:
    """The careful slot scouts' tuning with the scouting settings given."""
    return dataclasses.replace(CAUTIOUS, scouting=dataclasses.replace(CAUTIOUS.scouting, **scouting))


def enemy(enemy_id: int, enemy_type: str, x: int, y: int, hp: int = 100) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": hp}


def allied(*units: dict, tick: int, **observation: Any) -> dict:
    """An Allied base with a war factory, so that jeeps can be trained, with the units given."""
    return base_state("england", *ALLIED_WITH_WAR_FACTORY, tick=tick, units=list(units), **observation)


def soviet(*units: dict, tick: int, **observation: Any) -> dict:
    """A Soviet base with a barracks and a kennel, with the units given."""
    return base_state("russia", *SOVIET_BASE, tick=tick, units=list(units), **observation)


def jeep(cell: tuple[int, int], *, unit_id: int = JEEP, hp: int = 100, idle: bool = False) -> dict:
    return situations.unit(unit_id, "jeep", *cell, hp=hp, idle=idle)


def other_jeep() -> dict:
    return situations.unit(OTHER_JEEP, "jeep", *OTHER_JEEP_AT, idle=True)


def jeep_agent(tuning: Tuning, *more_managers: Any, start_tick: int = 1000):
    """An agent whose two slots are held by jeeps 400 and 401: two riflemen take them at `start_tick`, the jeeps
    ordered for them show in the Vehicle queue three ticks later and take over two ticks after that, at the base."""
    agent = new_agent(*more_managers, ScoutingManager(), tuning=tuning)
    rifles = [situations.unit(unit_id, "e1", 18, 46) for unit_id in (300, 301)]
    ask(agent, allied(*rifles, tick=start_tick))
    ask(agent, allied(*rifles, tick=start_tick + 3, production=[situations.job("Vehicle", "jeep")] * 2))
    ask(agent, allied(*rifles, jeep((28, 48), unit_id=JEEP), other_jeep(), tick=start_tick + 5))
    return agent


def at_seconds(seconds: float) -> int:
    return round(seconds * TICKS_PER_SECOND)


def attacks(orders: list[dict], unit_id: int = JEEP) -> list[dict]:
    """The attack orders of a batch that name the unit given."""
    return [order for order in orders if order["cmd"] in ("attack", "attack_move") and unit_id in order["units"]]


def moves(orders: list[dict], unit_id: int = JEEP) -> list[list[int]]:
    """Where the move orders of a batch that name the unit given send it."""
    return [order["to"] for order in orders if order["cmd"] == "move" and unit_id in order["units"]]


def slot_events(events: list[dict], kind: str) -> list[dict]:
    """The `scout_slot` events of a kind."""
    return [event for event in events if event["kind"] == kind]
