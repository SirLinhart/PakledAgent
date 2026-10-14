"""The Soviet raid of rocket soldiers carried by APCs.

With `[composition] soviet_raid = "apc_rockets"` the Soviet raid group is
`raid_apcs` APCs and `raid_apc_rockets` rocket soldiers for each of them. A
rocket soldier on foot is slow, and one walking across the map dies on the
way or comes too late; in an APC the rockets cross it fast, and out of it
beside a harvester they kill it quickly. So the rockets ride to the target and
fight only there.

The group boards at the staging point. Each rocket of the group that stands
there is ordered into an APC of the group that stands there too and has room:
at most `raid_apc_rockets` to an APC, counting those aboard it and those
walking to it. A rocket that stands idle again, its APC full or its order
lost, is ordered again; one that walks longer than the world's boarding window
is sent again by the world (`World.boarding_due`). The group is ready once it
holds its APCs, each within `gathered_cells` of the staging point with its
rockets aboard.

A damaged unit would only have the raid break off for its health. So at the
staging point an APC below `fit_hp`, or one carrying a rocket that was below
it when it boarded, unloads, and the damaged units leave the group for main:
a rocket once it is out, an APC once it is empty.

A ready group sets out at the raid's targets (see `raid_targets`) with a crew
of every member, the APCs and the rockets aboard them. The APCs go the raid's
way (see `raid_routes`) together, the rockets riding, and the way ends
`unload_cells` short of the target, where the APCs `unload`. The rockets then
attack the target, and an APC that falls more than `unload_cells` behind them
follows them. A rocket never walks to a target from the staging point.

The raid breaks off on the rules of any raid, its crew counted whole: aboard
or not, a member alive is no loss, and one aboard counts at the health it had
when it boarded. The estimate of the fight is of the members in the field:
the APCs while the rockets ride, and the rockets with them once they are out.
A raid that has lost its APCs has nothing left to ride in, and ends as one
that has lost its units.

Rockets out of their APCs never walk off. When the target is destroyed, or the
raid must break off, or the plan stops raiding, or base defense calls the
group home, the rockets out are ordered into the crew's APCs again, as at the
staging point, and the APCs wait for them. Once every rocket out is aboard,
or the APCs have waited `reload_wait_ticks`, they leave: on to the next target
by its way when the target was destroyed and a safe way leads to another,
else back to the staging point, where the raid ends. A rocket still out when
they leave goes to the staging point alone, and leaves the group for main. A
reason to break off that comes while the rockets board for the next target
sends the APCs home instead.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.cargo import ENTER_TRANSPORT, UNLOAD
from ucla_agent.games.redalert.family import APC
from ucla_agent.games.redalert.fight import is_near
from ucla_agent.games.redalert.tuning import RaidTuning
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

# What the rockets of an APC raid under way do: ride in the APCs, fight out of them at the target, or board again.
RIDING, OUT, RELOADING = "riding", "out", "reloading"


@dataclass
class Ride:
    """An APC raid under way besides its way and its target: the APCs and rockets it set out with, whether the
    rockets ride, fight or board again, and where the APCs go once they have."""

    apcs: tuple[int, ...]
    rockets: tuple[int, ...]
    phase: str = RIDING
    # The tick the rockets began to board again; None while they do not.
    reload_tick: int | None = None
    # Once they have: the next target and the cells of the way to it, or None to go home ...
    onward: tuple[EnemyRecord, list[Cell]] | None = None
    # ... and why the raid then ends.
    ending: str | None = None

    def go_on(self, onward: tuple[EnemyRecord, list[Cell]], tick: int) -> None:
        """Have the rockets board again from `tick`, for the APCs to go on by `onward`."""
        self.phase, self.reload_tick, self.onward, self.ending = RELOADING, tick, onward, None

    def go_home(self, reason: str, tick: int) -> None:
        """Have the rockets board again from `tick`, unless they already do, for the APCs to go home: the raid ends
        for `reason`, or for the reason it was already to end for."""
        if self.phase != RELOADING:
            self.phase, self.reload_tick = RELOADING, tick
        if self.ending is None:
            self.onward, self.ending = None, reason


def unload_route(tuning: RaidTuning) -> RaidTuning:
    """The raid's thresholds for the way of an APC raid, which ends `unload_cells` short of its target."""
    return replace(tuning, approach_cells=tuning.unload_cells)


def apcs_of(units: Iterable[Unit]) -> list[Unit]:
    """The APCs among some units."""
    return [unit for unit in units if unit.type == APC]


def rockets_of(units: Iterable[Unit]) -> list[Unit]:
    """The units among some that are not APCs: the rocket soldiers of the APC raid group."""
    return [unit for unit in units if unit.type != APC]


def is_loaded(apcs: list[Unit], wanted_apcs: int, per_apc: int, world: World) -> bool:
    """Whether the group holds its `wanted_apcs` APCs, each with `per_apc` units aboard."""
    return len(apcs) == wanted_apcs and all(len(world.cargo.get(apc.id, ())) >= per_apc for apc in apcs)


def at_point(units: Iterable[Unit], point: Cell, reach: int) -> list[Unit]:
    """The units within `reach` cells of a cell."""
    return [unit for unit in units if is_near(point, unit.x, unit.y, reach)]


def is_unfit(apc: Unit, world: World, fit_hp: int) -> bool:
    """Whether an APC is below `fit_hp`, or carries a unit that was when it boarded."""
    return apc.hp < fit_hp or any(unit.hp < fit_hp for unit in world.cargo.get(apc.id, ()))


class Boarding:
    """The rockets of the APC raid ordered into its APCs, and the APC each was ordered into. See the module's note."""

    def __init__(self) -> None:
        # Rocket id -> the APC it was last ordered into.
        self._sent: dict[int, int] = {}

    def forget(self) -> None:
        """Forget the orders sent: the rockets have since been given others, such as an attack."""
        self._sent = {}

    def orders(self, rockets: list[Unit], apcs: list[Unit], per_apc: int, world: World) -> list[Intent]:
        """The `enter_transport` orders that put each of the rockets not on its way to one of the APCs into one with
        room, the APCs filled in turn.

        A rocket is on its way while it is busy on the order it was given last. An APC has room for `per_apc` units,
        less those aboard it and those on their way to it.
        """
        apc_ids = {apc.id for apc in apcs}
        walking = {
            rocket.id: self._sent[rocket.id]
            for rocket in rockets
            if not rocket.idle and self._sent.get(rocket.id) in apc_ids
        }
        room = {apc.id: per_apc - len(world.cargo.get(apc.id, ())) for apc in apcs}
        for apc_id in walking.values():
            room[apc_id] -= 1
        into: dict[int, list[int]] = {}
        for rocket in (rocket for rocket in rockets if rocket.id not in walking):
            apc_id = next((apc_id for apc_id, left in room.items() if left > 0), None)
            if apc_id is None:
                break
            room[apc_id] -= 1
            into.setdefault(apc_id, []).append(rocket.id)
        self._sent = {**walking, **{rocket: apc_id for apc_id, units in into.items() for rocket in units}}
        return [
            Intent(Priority.MOVE, {"cmd": ENTER_TRANSPORT, "units": sorted(units), "target": apc_id})
            for apc_id, units in into.items()
        ]


class Unloads:
    """The `unload` orders sent to the APCs: one is not sent to an APC again while it may still be unloading."""

    def __init__(self) -> None:
        # APC id -> the tick an unload was last sent to it.
        self._sent: dict[int, int] = {}

    def orders(self, apc_ids: Iterable[int], tick: int, again_after_ticks: int) -> list[Intent]:
        """An `unload` for each of the APCs that was not sent one in the last `again_after_ticks`; none for none."""
        due = sorted(
            apc_id for apc_id in apc_ids if apc_id not in self._sent or tick - self._sent[apc_id] >= again_after_ticks
        )
        self._sent.update(dict.fromkeys(due, tick))
        return [Intent(Priority.MOVE, {"cmd": UNLOAD, "units": due})] if due else []
