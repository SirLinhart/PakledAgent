"""The kite: artillery and V2 step back from an enemy come too close, in every fight.

An artillery piece or a V2 does no damage to an enemy inside its minimum range and has almost no armour, so an enemy
that closes in kills it for nothing. On the siege's paths it falls back by the siege's rules (see `siege`); in every
other fight main or the base's defenders fight, each one that an enemy unit in sight has come within its minimum range
and `[siege] fallback_margin` of is given a move of `fallback_step` cells toward the base (`siege.fall_backs`), as an
emergency, in place of any attack. The next cycle looks again: it steps back again while the enemy follows, and fires
once it has the room.
"""

from __future__ import annotations

from collections.abc import Iterable

from ucla_agent.games.redalert import siege
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.world import Unit, World


def fall_back_moves(units: Iterable[Unit], world: World, tuning: Tuning) -> list[Intent]:
    """An emergency move toward the base for each artillery piece and V2 among `units` that an enemy unit in sight has
    come within its minimum range and `fallback_margin` of."""
    enemies = [record for record in world.enemy_memory.values() if record.visible and is_combat_unit(record)]
    stepping = siege.fall_backs(units, enemies, world, tuning.siege)
    return [
        Intent(Priority.EMERGENCY, {"cmd": "move", "units": [unit_id], "to": list(cell)})
        for unit_id, cell in stepping.items()
    ]


def stepping(intents: Iterable[Intent]) -> frozenset[int]:
    """The units given a fall-back among `intents`: those of its emergency moves."""
    return frozenset(
        unit_id
        for intent in intents
        if intent.priority == Priority.EMERGENCY and intent.order.get("cmd") == "move"
        for unit_id in intent.order["units"]
    )
