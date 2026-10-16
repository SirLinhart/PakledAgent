"""Harvesters that keep going idle by a field that has run dry are sent to the nearest field with ore.

The state says nothing of how much ore is left on a cell, and the map's
picture shows the ore of the start of the match. A field that has run dry
shows itself in its harvesters instead: they find nothing to load, stand
idle by their refinery, and upkeep's idle rule sends them back to harvest,
again and again. With `harvesters_to_fresh_ore` of the tuning file's [upkeep]
switched on, a field is marked dry once the harvesters within
`dry_near_cells` of a refinery that serves it have been sent back to harvest
`dry_idle_count` times in all within `dry_window_seconds`, by at least
`dry_distinct_harvesters` different harvesters: one harvester that keeps
going idle is a harvester stuck or blocked, not a field run dry. A field with
fewer harvesters than that (those within `dry_near_cells` of a refinery that
serves it) is marked dry only once every one of them was sent back.

A harvester idle in a fight is no sign of a dry field: it fled, or its way
to the ore was blocked. So no send of the idle rule is counted while base
defense names a threat (`ctx.threat_active`), nor one of a harvester with an
enemy combat unit seen lately within `fresh_ore_enemy_clear_cells` of it.

From the next cycle on, the dry field's harvesters, those within
`dry_near_cells` of a refinery that serves it, are each sent once with
`harvest` and a `to`: the ore cell nearest the harvester of the nearest field
that is safe. A field is safe when it is not marked dry, no remembered enemy
defense stands within `fresh_ore_defense_clear_cells` of one of its cells, no
enemy combat unit seen lately stands within `fresh_ore_enemy_clear_cells` of
one, and no remembered defense covers the straight way to it. A field no own
refinery serves (the field nearest each, see `harvester_room`) is not safe
either while its center lies within `mcv_enemy_building_cells` of [expansion]
of a known enemy building: a harvester sent there works by the enemy's base,
with no refinery of ours to unload at nearby. The fields the room choice
sends to are served ones, so it never sends a harvester to such a field. No
field whose center lies within `mcv_enemy_building_cells` of the enemy's
start is safe either, served or not: the enemy's base and army stand there.
While that start is not known, every spawn of the map but the base's own
(the one nearest the base's center) counts as the enemy's. A
field with an own refinery within 10 cells is preferred to one without, and
then a field in the lane toward the enemy (see `creep`) to one outside it,
before distance. No field is sent more than twice as many harvesters as a field
takes refineries (`max_refineries_per_field`); the rest wait by their
refinery, and the idle rule leaves them alone. A harvester that flees from an
enemy, or waits where parking has put it, is not sent: base defense and
parking come first.

With `dry_field_guard` on, three things more. A field with an ore mine is
never marked dry: the mine grows the ore back, and its trucks idle there
waiting for it. A send back to harvest of a truck that was retreating within
the last RETREAT_GRACE_SECONDS (see `truck_retreat`) is not counted: the
truck coming back from shelter to the ore cell it left, mined out meanwhile,
is no sign of a dry field. And no truck is sent to a field no refinery of ours
serves (`harvester_room.served_fields`): it would mine where nothing of ours
unloads, far from cover. In the live games of 20261016 the trucks of a home
field with 2 mines were moved off it as dry right as they came back from a
retreat, three times in the match, each time farther, to fields with no
refinery of ours, where they died.

A field is no longer dry once an own harvester has stood on one of its ore
cells for three states in a row: there was ore on it after all.

Each move, the harvesters sent from one field to another in a cycle, is
written to the match record as one `harvesters_moved` event.

With `fresh_ore_by_load` switched on as well, the harvesters moved off a dry
field are spread by load instead. Each goes in turn to the safe field nearest
it whose load is below its share: the load is the own harvesters last sent
there or standing within `dry_near_cells` of it, with those sent there in the
move, and the share is `field_share_per_refinery` for each own refinery that
serves the field, one at the least. A refinery serves the one field whose ore
lies nearest its footprint within `refinery_serves_cells` of [placement]
(`refinery_fields.served_field`, counted by `harvester_room.serving_counts`),
so the share and the field room agree on which refineries serve a field. With
no field below its share, a harvester goes to the field it would go to with
the switch off, and the limit of harvesters to a field does not apply. One
`harvesters_moved` event is written for each dry field the harvesters leave in
a cycle, with the field each of them goes to (`to_field_centers`). With
`truck_shares_by_mines` on, an active field's share is its truck share (see
`truck_shares`).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, coverage, creep, placement, refinery_fields
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.family import HARVESTER, REFINERY
from ucla_agent.games.redalert.harvester_room import FieldRoom, roomiest, served_fields, serving_counts
from ucla_agent.games.redalert.items import NON_COMBAT_TYPES, TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.terrain import OrePatch
from ucla_agent.games.redalert.world import Building, Cell, EnemyRecord, Unit, World

HARVESTERS_MOVED_EVENT = "harvesters_moved"
# With dry_field_guard on, a truck seen retreating this lately is no sign of a dry field when sent back to harvest.
RETREAT_GRACE_SECONDS = 10
# The reason a `harvesters_moved` event gives: the field the harvesters left had run dry.
DRY_REASON = "dry"
# A field with an own refinery this close to one of its cells is preferred: its harvesters unload nearby.
OWN_REFINERY_CELLS = 10
# How many states in a row an own harvester has to stand on a dry field's ore before the field counts as mined again.
MINED_STATES = 3
# How many harvesters a field is sent at most, for each refinery a field takes.
_HARVESTERS_PER_REFINERY = 2


@dataclass(frozen=True)
class _Move:
    """One harvester sent from a dry field to the ore cell of another."""

    harvester: int
    dry: OrePatch
    to: OrePatch
    cell: Cell


class FreshOre:
    """Marks the fields that have run dry and sends their harvesters to fresh ore. See the module's note."""

    def __init__(self) -> None:
        # Field -> the tick and the harvester of each time one of its harvesters was sent back to harvest, within the
        # window.
        self._reorders: dict[OrePatch, list[tuple[int, int]]] = {}
        self._dry: set[OrePatch] = set()
        # Dry field -> in how many states in a row an own harvester has stood on its ore.
        self._mined_states: Counter[OrePatch] = Counter()
        # Harvester id -> the field it was sent to.
        self._sent: dict[int, OrePatch] = {}
        # Truck id -> the last tick it was seen retreating (see `truck_retreat`).
        self._retreated_at: dict[int, int] = {}

    @property
    def dry_fields(self) -> frozenset[OrePatch]:
        """The fields marked dry now."""
        return frozenset(self._dry)

    def waiting(self, harvesters: Iterable[Unit], world: World, ctx: MatchContext) -> frozenset[int]:
        """The harvesters of a dry field that have not been sent away: the idle rule leaves them to this one."""
        return frozenset(unit.id for unit in harvesters if self._dry_field_of(unit, world, ctx) is not None)

    def orders(
        self,
        harvesters: list[Unit],
        excluded: frozenset[int],
        world: World,
        ctx: MatchContext,
        room: FieldRoom | None = None,
        shares: Mapping[OrePatch, int] | None = None,
    ) -> list[Intent]:
        """The `harvest` orders that send the harvesters of the dry fields to fresh ore, but those `excluded`; one
        `harvesters_moved` event for each field they leave for another, or with `fresh_ore_by_load` on for each field
        they leave. Given the fields' `room`, each goes to the served field with the most room while one has any.
        Given the truck `shares` of the active fields (see `truck_shares`), the load of such a field is held to its
        share."""
        alive = {unit.id for unit in harvesters}
        self._sent = {harvester: field for harvester, field in self._sent.items() if harvester in alive}
        self._unmark_mined(harvesters)
        by_load = ctx.tuning.upkeep.fresh_ore_by_load
        destinations = _ByLoad(harvesters, self._sent, world, ctx, shares) if by_load else _ByLimit(self._sent, ctx)
        if room is not None:
            destinations = _ByRoom(room, destinations, harvesters, world, ctx)
        moves = self._moves([unit for unit in harvesters if unit.id not in excluded], destinations, world, ctx)
        self._sent.update((move.harvester, move.to) for move in moves)
        _log_moves(moves, world, by_load)
        by_cell: dict[Cell, list[int]] = {}
        for move in moves:
            by_cell.setdefault(move.cell, []).append(move.harvester)
        return [
            Intent(Priority.PRODUCTION, {"cmd": "harvest", "units": units, "to": list(cell)})
            for cell, units in by_cell.items()
        ]

    def note_reorders(self, harvester_ids: Iterable[int], world: World, ctx: MatchContext) -> None:
        """Count the idle rule's `harvest` orders of this cycle against the fields of the refineries each harvester
        stands near, and mark dry a field whose count within the window reaches `dry_idle_count` from enough different
        harvesters (`_is_dry`). None is counted while base defense names a threat, nor that of a harvester with an
        enemy combat unit seen lately within `fresh_ore_enemy_clear_cells` of it: a fight, not a dry field, keeps it
        idle."""
        tuning = ctx.tuning.upkeep
        since = world.tick - tuning.dry_window_seconds * TICKS_PER_SECOND
        self._retreated_at.update(dict.fromkeys(ctx.fleeing_harvesters, world.tick))
        grace = world.tick - RETREAT_GRACE_SECONDS * TICKS_PER_SECOND
        self._retreated_at = {truck: tick for truck, tick in self._retreated_at.items() if tick >= grace}
        counted = _counted_reorders(harvester_ids, world, ctx)
        if tuning.dry_field_guard:
            counted = [harvester_id for harvester_id in counted if harvester_id not in self._retreated_at]
        for harvester_id in counted:
            unit = world.units[harvester_id]
            for field in _fields_near(unit, world, ctx):
                self._reorders.setdefault(field, []).append((world.tick, harvester_id))
        self._reorders = {
            field: recent
            for field, sends in self._reorders.items()
            if (recent := [send for send in sends if send[0] > since])
        }
        newly_dry = {
            field
            for field, sends in self._reorders.items()
            if _is_dry(field, sends, world, ctx) and not (tuning.dry_field_guard and field.mines > 0)
        }
        self._dry |= newly_dry

    def _unmark_mined(self, harvesters: list[Unit]) -> None:
        """Take the mark off each dry field an own harvester has stood on the ore of for `MINED_STATES` states."""
        standing = {(unit.x, unit.y) for unit in harvesters}
        for field in list(self._dry):
            self._mined_states[field] = self._mined_states[field] + 1 if not standing.isdisjoint(field.cells) else 0
            if self._mined_states[field] >= MINED_STATES:
                self._dry.discard(field)
                self._reorders.pop(field, None)
                del self._mined_states[field]

    def _moves(
        self, harvesters: list[Unit], destinations: _ByLimit | _ByLoad | _ByRoom, world: World, ctx: MatchContext
    ) -> list[_Move]:
        """Where each harvester of a dry field that was not sent yet goes: the ore cell nearest it of the safe field
        the `destinations` pick for it, in turn; none for a harvester they pick none for."""
        moves, fresh_for = [], {}
        for unit in harvesters:
            dry = self._dry_field_of(unit, world, ctx)
            if dry is None:
                continue
            if dry not in fresh_for:
                fresh_for[dry] = self._fresh_fields(dry, world, ctx)
            fresh = destinations.pick(unit, fresh_for[dry])
            if fresh is not None:
                destinations.take(fresh)
                moves.append(_Move(unit.id, dry, fresh, fresh.nearest_cell((unit.x, unit.y))))
        return moves

    def _dry_field_of(self, unit: Unit, world: World, ctx: MatchContext) -> OrePatch | None:
        """The dry field a harvester belongs to: one served by a refinery it stands near; None when it stands near
        none, or was sent away already to a field that is not dry."""
        if not self._dry or self._sent.get(unit.id) not in (None, *self._dry):
            return None
        return next((field for field in _fields_near(unit, world, ctx) if field in self._dry), None)

    def _fresh_fields(self, dry: OrePatch, world: World, ctx: MatchContext) -> list[OrePatch]:
        """The safe fields for the harvesters of a dry field, the best first. See the module's note."""
        terrain, tuning = ctx.terrain, ctx.tuning
        in_lane = set(creep.lane_fields(world, terrain, tuning.placement))
        refineries = [_middle(building) for building in world.buildings.values() if building.type == REFINERY]
        served = set(served_fields(world, ctx))
        starts, start_reach = _enemy_starts(world), tuning.expansion.mcv_enemy_building_cells
        safe = [
            field
            for field in terrain.ore_patches
            if field not in self._dry
            and (field in served or not tuning.upkeep.dry_field_guard)
            and self._is_safe(dry, field, world, ctx)
            and not _is_by_enemy_buildings(field, served, world, ctx)
            and not any(cells.is_within(field.center, start, start_reach) for start in starts)
        ]

        def preference(field: OrePatch) -> tuple[bool, bool, float]:
            has_refinery = any(
                cells.is_within(field.nearest_cell(middle), middle, OWN_REFINERY_CELLS) for middle in refineries
            )
            return not has_refinery, field not in in_lane, cells.distance(dry.center, field.nearest_cell(dry.center))

        return sorted(safe, key=preference)

    @staticmethod
    def _is_safe(dry: OrePatch, field: OrePatch, world: World, ctx: MatchContext) -> bool:
        """Whether a field is clear of remembered defenses and of enemy combat units seen lately, and no remembered
        defense covers the straight way to it from a dry field."""
        tuning = ctx.tuning.upkeep
        defenses = world.known_enemy_defenses
        if _is_any_near(field, defenses, tuning.fresh_ore_defense_clear_cells):
            return False
        if _is_any_near(field, _enemy_fighters(world), tuning.fresh_ore_enemy_clear_cells):
            return False
        way = cells.line(dry.center, field.nearest_cell(dry.center))
        return not any(coverage.is_covered(cell, defenses, world.items, ctx.tuning.coverage) for cell in way)


class _ByLimit:
    """Which safe field a harvester of a dry field goes to with `fresh_ore_by_load` off: the best that has been sent
    fewer than twice `max_refineries_per_field` harvesters."""

    def __init__(self, sent: dict[int, OrePatch], ctx: MatchContext) -> None:
        self._sent_to = Counter(sent.values())
        self._limit = _HARVESTERS_PER_REFINERY * ctx.tuning.placement.max_refineries_per_field

    def pick(self, _unit: Unit, fields: list[OrePatch]) -> OrePatch | None:
        """The first of the safe fields, the best first, that still takes a harvester; None when none does."""
        return next((field for field in fields if self._sent_to[field] < self._limit), None)

    def take(self, field: OrePatch) -> None:
        """Count a harvester sent to a field."""
        self._sent_to[field] += 1


class _ByLoad:
    """Which safe field a harvester of a dry field goes to with `fresh_ore_by_load` on: the one nearest it whose load is
    below its share, else the best. See the module's note. The loads are worked out the first time one is asked for."""

    def __init__(
        self,
        harvesters: list[Unit],
        sent: dict[int, OrePatch],
        world: World,
        ctx: MatchContext,
        shares: Mapping[OrePatch, int] | None = None,
    ) -> None:
        self._harvesters, self._sent, self._ctx = harvesters, sent, ctx
        self._load: Counter[OrePatch] | None = None
        # Field -> how many own refineries serve it, as the field room counts them; and the truck share of each active
        # field, when the shares go by ore mines.
        self._serving = serving_counts(world, ctx)
        self._shares = shares or {}

    def pick(self, unit: Unit, fields: list[OrePatch]) -> OrePatch | None:
        """The safe field nearest the harvester whose load is below its share; the first of `fields`, the best, when
        none is; None without a safe field."""
        here = (unit.x, unit.y)
        nearest_first = sorted(fields, key=lambda field: cells.distance(here, field.nearest_cell(here)))
        under_share = (field for field in nearest_first if self._loads()[field] < self._share(field))
        return next(under_share, next(iter(fields), None))

    def take(self, field: OrePatch) -> None:
        """Count a harvester sent to a field in this move."""
        self._loads()[field] += 1

    def _loads(self) -> Counter[OrePatch]:
        """The load of each field: the own harvesters last sent there or standing within `dry_near_cells` of it."""
        if self._load is None:
            reach = self._ctx.tuning.upkeep.dry_near_cells
            self._load = Counter(
                {
                    field: sum(1 for unit in self._harvesters if self._is_load(unit, field, reach))
                    for field in self._ctx.terrain.ore_patches
                }
            )
        return self._load

    def _is_load(self, unit: Unit, field: OrePatch, reach: int) -> bool:
        """Whether a harvester counts toward a field's load: it was last sent there, or stands within `reach` of it."""
        here = (unit.x, unit.y)
        return self._sent.get(unit.id) == field or cells.is_within(here, field.nearest_cell(here), reach)

    def _share(self, field: OrePatch) -> int:
        """How many harvesters a field takes: an active field's truck share when the shares go by ore mines; else
        `field_share_per_refinery` for each own refinery that serves it (`harvester_room.serving_counts`, within
        `refinery_serves_cells` of [placement]), one at the least."""
        if field in self._shares:
            return self._shares[field]
        return self._ctx.tuning.upkeep.field_share_per_refinery * max(1, self._serving[field])


class _ByRoom:
    """Which safe field a harvester of a dry field goes to given the fields' room (see `harvester_room`): the served
    one with the most room, the nearest on a tie; while none has room, the field the other `destinations` pick."""

    def __init__(
        self, room: FieldRoom, destinations: _ByLimit | _ByLoad, harvesters: list[Unit], world: World, ctx: MatchContext
    ) -> None:
        self._room, self._destinations = room, destinations
        self._harvesters, self._world, self._ctx = harvesters, world, ctx

    def pick(self, unit: Unit, fields: list[OrePatch]) -> OrePatch | None:
        """The safe served field with the most room; else what the other destinations pick."""
        rooms = self._room.rooms(self._harvesters, self._world, self._ctx)
        return roomiest(rooms, (unit.x, unit.y), among=fields) or self._destinations.pick(unit, fields)

    def take(self, field: OrePatch) -> None:
        """Count a harvester sent to a field, in its room and in the other destinations' count."""
        self._room.take(field)
        self._destinations.take(field)


def _is_dry(field: OrePatch, sends: list[tuple[int, int]], world: World, ctx: MatchContext) -> bool:
    """Whether a field's harvesters sent back to harvest within the window, as (tick, harvester), mark it dry: at least
    `dry_idle_count` sends, from at least `dry_distinct_harvesters` different harvesters, or when the field has fewer
    harvesters than that, from every one of them."""
    tuning = ctx.tuning.upkeep
    if len(sends) < tuning.dry_idle_count:
        return False
    sent = {harvester for _, harvester in sends}
    its_own = {
        unit.id for unit in world.units.values() if unit.type == HARVESTER and field in _fields_near(unit, world, ctx)
    }
    if len(its_own) < tuning.dry_distinct_harvesters:
        return its_own <= sent
    return len(sent) >= tuning.dry_distinct_harvesters


def _counted_reorders(harvester_ids: Iterable[int], world: World, ctx: MatchContext) -> list[int]:
    """The harvesters whose send back to harvest counts toward a dry field: none while base defense names a threat;
    else those with no enemy combat unit seen lately within `fresh_ore_enemy_clear_cells` of them."""
    if ctx.threat_active:
        return []
    reach, fighters = ctx.tuning.upkeep.fresh_ore_enemy_clear_cells, _enemy_fighters(world)
    return [
        harvester_id
        for harvester_id in harvester_ids
        if not any(cells.is_within(_cell(world.units[harvester_id]), (enemy.x, enemy.y), reach) for enemy in fighters)
    ]


def _cell(unit: Unit) -> Cell:
    """The cell a unit stands on."""
    return unit.x, unit.y


def _fields_near(unit: Unit, world: World, ctx: MatchContext) -> list[OrePatch]:
    """The fields served by the own refineries within `dry_near_cells` of a harvester."""
    reach, terrain, serves = ctx.tuning.upkeep.dry_near_cells, ctx.terrain, ctx.tuning.placement.refinery_serves_cells
    fields = [
        refinery_fields.served_field(building, terrain, serves)
        for building in world.buildings.values()
        if building.type == REFINERY and cells.is_within((unit.x, unit.y), _middle(building), reach)
    ]
    return list(dict.fromkeys(field for field in fields if field is not None))


def _enemy_fighters(world: World) -> list[EnemyRecord]:
    """The enemy units seen lately that fight: neither buildings nor stale, nor of a type that does not fight."""
    return [
        record
        for record in world.enemy_memory.values()
        if not record.is_building and not record.stale and record.type not in NON_COMBAT_TYPES
    ]


def _is_by_enemy_buildings(field: OrePatch, served: set[OrePatch], world: World, ctx: MatchContext) -> bool:
    """Whether a field no own refinery serves has its center within `mcv_enemy_building_cells` of [expansion] of a
    known enemy building."""
    if field in served:
        return False
    reach = ctx.tuning.expansion.mcv_enemy_building_cells
    return any(
        record.is_building and cells.is_within(field.center, (record.x, record.y), reach)
        for record in world.enemy_memory.values()
    )


def _enemy_starts(world: World) -> list[Cell]:
    """Where the enemy may have started: its start when known; else every spawn of the map but the base's own, the one
    nearest the base's center; none while the base's center is not known either."""
    if world.enemy_base is not None:
        return [world.enemy_base]
    center = world.base_center
    if center is None or not world.spawns:
        return []
    own = min(world.spawns, key=lambda spawn: cells.distance(spawn, center))
    return [spawn for spawn in world.spawns if spawn != own]


def _is_any_near(field: OrePatch, enemies: Iterable[EnemyRecord], reach: int) -> bool:
    """Whether one of the enemies stands within `reach` cells of a cell of a field."""
    return any(cells.is_within(field.nearest_cell((enemy.x, enemy.y)), (enemy.x, enemy.y), reach) for enemy in enemies)


def _middle(building: Building) -> Cell:
    """The middle cell of a building."""
    return cells.nearest_cell(*placement.standing_box(building).center)


def _log_moves(moves: list[_Move], world: World, by_load: bool) -> None:
    """Write one `harvesters_moved` event for each field harvesters left for another in this cycle; with `by_load`,
    one for each field they left, with the field each of them goes to, the first harvester's as `to_field_center`."""
    groups: dict[tuple[OrePatch, ...], list[_Move]] = {}
    for move in moves:
        groups.setdefault((move.dry,) if by_load else (move.dry, move.to), []).append(move)
    for group in groups.values():
        data: dict[str, object] = {
            "tick": world.tick,
            "from_field_center": list(group[0].dry.center),
            "to_field_center": list(group[0].to.center),
            "harvester_ids": [move.harvester for move in group],
            "reason": DRY_REASON,
        }
        if by_load:
            data["to_field_centers"] = [list(move.to.center) for move in group]
        records.log(HARVESTERS_MOVED_EVENT, data)
