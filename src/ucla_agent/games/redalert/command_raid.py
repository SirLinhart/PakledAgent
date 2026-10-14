"""The raid detachment: the units of main the model commander sends after exposed trucks.

When the model picks `raid trucks` for main (see `command_army`), the
commander keeps the pick apart from main's standing action (see
`commander`), and the combat manager forms a raid detachment from main, one
kind of the commander's detachments (see `command_detachments`): up to
`RAID_UNITS` of its units fit to raid (see `command_army.is_fit`) and out on
no other detachment, the fastest first (of two as fast, the lower id), or all
of them when main has fewer. The detachment attack-moves to the cell of the
opportunity's harvester nearest our base (`nearest_harvester` of its
`ExposedTrucks`), which follows the harvesters as scouting renews the
opportunity. The rest of main keeps the standing action, or holds while there
is none.

The detachment rejoins main, its units taking main's orders again, the first
of these that holds:

- `destroyed`: none of the harvesters last seen at the opportunity is left,
  each forgotten or seen to leave sight with a kill (`killed_in_sight`);
- `gone`: a raider stands within `ARRIVED_CELLS` of the target and none of
  those harvesters is in sight;
- `expired`: the opportunity's ore field is no longer among
  `ctx.exposed_trucks`;
- `timed_out`: `RAID_SECONDS` have passed since the pick;
- `no_units`: none of its units is left in main.

As every detachment, it rejoins main earlier when the script's retreat
override or base defense takes main back (see `command_detachments`).
Forming one is a `commander` event of kind `raid` with its units, the field
and the target; its end one of kind `rejoined` with the reason. The enemies
it is on (`targets`) are the harvesters its pick named, those of the
opportunity when it was offered: no other detachment is offered on them
while it is out (see `command_detachments`).

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from typing import Mapping

from ucla_agent.core import records
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.command_army import RAID_TRUCKS, is_fit, unit_speed
from ucla_agent.games.redalert.commander import COMMANDER_EVENT, TaskPick
from ucla_agent.games.redalert.exposed_trucks import ExposedTrucks
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.world import Cell, Unit, World

RAID_UNITS = 6
RAID_SECONDS = 60
ARRIVED_CELLS = 4
# The kinds of the detachment's `commander` events, and why it rejoins main.
RAID = "raid"
REJOINED = "rejoined"
DESTROYED = "destroyed"
GONE = "gone"
EXPIRED = "expired"
TIMED_OUT = "timed_out"
NO_UNITS = "no_units"
_RAID_TICKS = RAID_SECONDS * TICKS_PER_SECOND


class TruckRaid:
    """A raid detachment in force: the ore field of its opportunity, its units, the tick of its pick, the harvesters
    it is on, and those seen to die there. See the module's note."""

    kind = RAID_TRUCKS

    def __init__(self, field: Cell, units: frozenset[int], tick: int, targets: frozenset[int]) -> None:
        self.field = field
        self.units = units
        self.targets = targets
        self._tick = tick
        self._killed: set[int] = set()

    @classmethod
    def formed(cls, pick: TaskPick, main: Mapping[int, Unit], world: World) -> TruckRaid | None:
        """The detachment of a pick from the units of main given: up to RAID_UNITS of them fit to raid, the fastest
        first, with a `raid` event; None when none is fit."""
        fit = sorted(
            (unit for unit in main.values() if is_fit(unit)), key=lambda unit: (-unit_speed(unit.type, world), unit.id)
        )
        raiders = frozenset(unit.id for unit in fit[:RAID_UNITS])
        if not raiders:
            return None
        # The key of a raid trucks action is its kind and the ore field of its opportunity.
        _, field_cell = pick.action.key
        records.log(
            COMMANDER_EVENT,
            {
                "kind": RAID,
                "units": sorted(raiders),
                "field": list(field_cell),
                "target": list(pick.action.cell),
                "tick": world.tick,
            },
        )
        return cls(field_cell, raiders, pick.tick, frozenset(pick.action.aim))

    def follow(self, main: Mapping[int, Unit], world: World, ctx: MatchContext) -> str | None:
        """Keep the units still in main, and end the detachment when it rejoins main in this cycle; the reason it
        rejoins, or None while it raids on."""
        self.units = self.units & frozenset(main)
        reason = self._rejoins_because(_opportunity(self.field, ctx), main, world)
        if reason is not None:
            self.end(reason, world)
        return reason

    def end(self, reason: str, world: World) -> None:
        """Write the `rejoined` event of the detachment's end, for a reason."""
        records.log(COMMANDER_EVENT, {"kind": REJOINED, "reason": reason, "tick": world.tick})

    def orders(self, main: Mapping[int, Unit], world: World, ctx: MatchContext) -> list[Intent]:
        """The attack-move of the detachment's units to its target, but for those standing idle there already."""
        # The detachment rejoins main once its opportunity has expired: while it is out, the opportunity is noted.
        target = _opportunity(self.field, ctx).nearest_harvester
        going = [
            unit_id
            for unit_id in sorted(self.units)
            if unit_id in main and not (main[unit_id].idle and _is_at(main[unit_id], target, ARRIVED_CELLS))
        ]
        order = {"cmd": "attack_move", "units": going, "to": list(target)}
        return [Intent(Priority.MOVE, order)] if going else []

    def _rejoins_because(self, opportunity: ExposedTrucks | None, main: Mapping[int, Unit], world: World) -> str | None:
        """Why the detachment rejoins main in this cycle (see the module's note); None while it raids on."""
        if not self.units:
            return NO_UNITS
        if opportunity is None:
            return EXPIRED
        if world.tick - self._tick >= _RAID_TICKS:
            return TIMED_OUT
        self._killed |= world.killed_in_sight & set(opportunity.harvester_ids)
        left = [
            world.enemy_memory[harvester]
            for harvester in opportunity.harvester_ids
            if harvester in world.enemy_memory and harvester not in self._killed
        ]
        if not left:
            return DESTROYED
        has_arrived = any(_is_at(main[unit_id], opportunity.nearest_harvester, ARRIVED_CELLS) for unit_id in self.units)
        return GONE if has_arrived and not any(record.visible for record in left) else None


def _opportunity(field: Cell, ctx: MatchContext) -> ExposedTrucks | None:
    """The opportunity of an ore field as scouting notes it in this cycle; None once it has expired."""
    return next((trucks for trucks in ctx.exposed_trucks if trucks.field == field), None)


def _is_at(unit: Unit, cell: Cell, reach: float) -> bool:
    return cells.is_within((unit.x, unit.y), cell, reach)
