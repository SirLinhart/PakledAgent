"""Which group each of the agent's fighting units belongs to.

The agent does not order its army unit by unit. Every unit that can fight is
in a named group, and the rules of play speak of groups: the home guard stays
by the base, `main` is the army that gathers and attacks, `raid` goes after
the enemy's economy and `scout` is the one unit that keeps the picture of the
enemy fresh.

A unit joins a group in the cycle it is first seen and stays there until a
manager moves it to another. The home guard has a quota for each type of unit
it wants, and a new unit of such a type goes to the guard while the guard is
short of it, so that a guard that has lost a unit is made up again by the next
one trained. The raid group has a quota too, while the raid manager forms or
leads it, and a new unit the guard does not take goes to the raid group while
that is short of its type: the managers that run before the raids would
otherwise send it after main before the raid manager could take it. Every
other new unit joins `main`. Units that are gone are forgotten, and the
bookkeeping holds nothing of a match before this one.

Within `main` the long-range units, the artillery and the V2, are its siege
subgroup: they fire from far behind the rest of main, which screens them.

A unit aboard a transport is not gone, though the state no longer lists it:
it keeps the group it had when it boarded while the world has it aboard, and
a unit the world takes to have come out under a new id is the same member
under that id. It is dropped only once it is lost, with its transport, or
has left the transport without coming back into sight. Aboard it neither
fights nor takes orders, so the queries of a group's strength in the field
(`members`, `siege_members`) leave it out; the queries that count a group
(`count`, for the quotas, and `roster`, for who set out and who was lost)
take it in.

Two marks on a group are base defense's to set, for the managers that direct
the groups to honor. A group under `defense_override` fights whatever the
estimate of the fight says, because the base is at stake. A `recalled` group
was called home to defend the base, and the manager that sent it out leaves it
alone until base defense releases it.

The override can also mark single units (`set_unit_override`), when base
defense takes only the members of a group that answer a threat and leaves the
rest of the group to its manager. A manager asks whether a unit is under
base defense's override by `is_overridden`, which holds for a marked unit and
for every member of a group under the override alike.

A unit sent to walk a route to an ore field (see `route_walk`) is marked as
walking while it walks. It keeps its group, and like a unit aboard a
transport it is counted (`count`, `roster`) but left out of the group in the
field (`members`, `siege_members`): the managers that order a group's members
leave it to its walk, and the guard's quota does not have it made up.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Mapping

from ucla_agent.games.redalert.world import World

GUARD = "guard"
MAIN = "main"
RAID = "raid"
SCOUT = "scout"
# The one rifleman posted on the line to the enemy's start while an attack is out, whose death warns of their army
# (see `canary`): it takes no other manager's orders.
CANARY = "canary"
# The long-range units: Allied artillery and the Soviet V2 launcher.
SIEGE_TYPES = frozenset({"arty", "v2rl"})


class Groups:
    """The group of every own combat unit of one match. See the module's note."""

    def __init__(self) -> None:
        # Unit id -> group, in the order the units joined.
        self._group_of: dict[int, str] = {}
        # Unit id -> type, for the units that have a group.
        self._type_of: dict[int, str] = {}
        # The members that are aboard a transport, as of the last update.
        self._aboard: set[int] = set()
        # Unit type -> how many of it the home guard wants.
        self._guard_quota: dict[str, int] = {}
        # Unit type -> how many of it the raid group takes of the new units; empty while nobody forms a raid.
        self._raid_quota: dict[str, int] = {}
        # (group, unit type) -> members of that type, as of the last update.
        self._counts: Counter[tuple[str, str]] = Counter()
        self._defense_override: set[str] = set()
        # The units under base defense's override one by one, whatever their group.
        self._unit_override: set[int] = set()
        # The members walking a route to an ore field, which take no order of their group's manager meanwhile.
        self._walking: set[int] = set()
        self._recalled: set[str] = set()

    def set_guard_quota(self, unit_type: str, wanted: int) -> None:
        """Have the home guard take new units of a type until it holds `wanted` of them."""
        self._guard_quota[unit_type] = wanted

    def set_raid_quota(self, wanted: Mapping[str, int]) -> None:
        """Have the raid group take new units of each type until it holds `wanted` of them; an empty quota, none."""
        self._raid_quota = dict(wanted)

    def update(self, world: World) -> None:
        """Bring the groups up to date with the units of a new state: the gone forgotten, the new given a group.

        A member aboard a transport is not gone, and one that came out under a new id keeps its group under it.
        """
        alive, renamed = world.combat_units, world.taken_over
        group_of: dict[int, str] = {}
        type_of: dict[int, str] = {}
        for unit_id, group in self._group_of.items():
            unit_id = renamed.get(unit_id, unit_id)
            unit = alive.get(unit_id) or world.aboard(unit_id)
            if unit is not None:
                group_of[unit_id], type_of[unit_id] = group, unit.type
        self._group_of, self._type_of = group_of, type_of
        self._aboard = {unit_id for unit_id in group_of if unit_id not in alive}
        counts = Counter((group, self._type_of[unit_id]) for unit_id, group in self._group_of.items())
        for unit_id, unit in alive.items():
            if unit_id not in self._group_of:
                group = self._group_for_new(unit.type, counts)
                self._group_of[unit_id] = group
                self._type_of[unit_id] = unit.type
                counts[group, unit.type] += 1
        self._counts = counts
        self._walking.intersection_update(self._group_of)

    def _group_for_new(self, unit_type: str, counts: Counter[tuple[str, str]]) -> str:
        """The group a new unit joins: the guard while it is short of the type, else the raid group while it is."""
        if counts[GUARD, unit_type] < self._guard_quota.get(unit_type, 0):
            return GUARD
        return RAID if counts[RAID, unit_type] < self._raid_quota.get(unit_type, 0) else MAIN

    def assign(self, unit_id: int, group: str) -> None:
        """Move a unit that has a group into another; it is then the newest member of that group."""
        unit_type = self._type_of[unit_id]
        self._counts[self._group_of.pop(unit_id), unit_type] -= 1
        self._group_of[unit_id] = group
        self._counts[group, unit_type] += 1

    def group_of(self, unit_id: int) -> str | None:
        """The group a unit is in; None for a unit that has none, such as one that does not fight."""
        return self._group_of.get(unit_id)

    def members(self, group: str) -> list[int]:
        """The ids of a group's units in the field, in the order they joined it: every one is in the world's combat
        units. Its members aboard a transport, and those walking a route, are left out."""
        return [
            unit_id for unit_id in self.roster(group) if unit_id not in self._aboard and unit_id not in self._walking
        ]

    def aboard(self, group: str) -> list[int]:
        """The ids of a group's members aboard a transport, in the order they joined it."""
        return [unit_id for unit_id in self.roster(group) if unit_id in self._aboard]

    def roster(self, group: str) -> list[int]:
        """The ids of every member of a group, in the field or aboard a transport, in the order they joined it."""
        return [unit_id for unit_id, of_group in self._group_of.items() if of_group == group]

    def siege_members(self, group: str) -> list[int]:
        """The ids of a group's long-range units in the field, its siege subgroup, in the order they joined it."""
        return [unit_id for unit_id in self.members(group) if self._type_of[unit_id] in SIEGE_TYPES]

    def count(self, group: str, unit_type: str) -> int:
        """How many units of a type a group holds, its members aboard a transport and those walking a route included."""
        return self._counts[group, unit_type]

    def set_walking(self, unit_id: int, walking: bool) -> None:
        """Mark a member as walking a route, which leaves it out of `members` while it keeps its group, or no longer."""
        if walking:
            self._walking.add(unit_id)
        else:
            self._walking.discard(unit_id)

    def is_walking(self, unit_id: int) -> bool:
        """Whether a unit is marked as walking a route."""
        return unit_id in self._walking

    def set_defense_override(self, group: str) -> None:
        """Have a group engage whatever the estimate of the fight says, until the overrides are cleared."""
        self._defense_override.add(group)

    def clear_defense_override(self, group: str) -> None:
        """Have one group follow the estimate of the fight again while the other marks stay."""
        self._defense_override.discard(group)

    def has_defense_override(self, group: str) -> bool:
        """Whether base defense has a group engage whatever the estimate of the fight says."""
        return group in self._defense_override

    def set_unit_override(self, unit_ids: Iterable[int]) -> None:
        """Have single units engage whatever the estimate of the fight says, until the overrides are cleared; the
        other members of their groups are left as they are."""
        self._unit_override.update(unit_ids)

    def is_overridden(self, unit_id: int) -> bool:
        """Whether base defense has a unit engage whatever the estimate of the fight says: the unit is marked, or its
        group is under the override."""
        return unit_id in self._unit_override or self._group_of.get(unit_id) in self._defense_override

    def is_any_overridden(self, group: str) -> bool:
        """Whether base defense has a group under its override, or any member of it."""
        return group in self._defense_override or any(self.is_overridden(unit_id) for unit_id in self.roster(group))

    def recall(self, group: str) -> None:
        """Mark a group as called home by base defense."""
        self._recalled.add(group)

    def is_recalled(self, group: str) -> bool:
        """Whether base defense has called a group home and not yet released it."""
        return group in self._recalled

    def release_defense(self) -> list[str]:
        """Clear every override and recall, and return the groups that were recalled."""
        released = sorted(self._recalled)
        self._defense_override.clear()
        self._unit_override.clear()
        self._recalled.clear()
        return released
