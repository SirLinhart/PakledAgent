"""The hunt of a stray enemy scout (`[scouting] scout_stray_hunt`): a slot scout kills a lone unit it clearly outfights.

A careful scout (see `scout_stance`) holds fire and runs from every enemy unit
it sees, so a lone enemy scout that crosses it is never punished. With the
switch on as well as `scout_cautious`, a slot scout attacks such a unit when
it clearly wins the duel, far from where the enemy can help it.

- **Seconds to kill.** A unit of type a kills one of type b, at b's share of
  health, in b's hit points by that share over a's damage a second against b
  (`seconds_to_kill`); never when that damage is 0 or a type's hit points or
  damage are unknown.
- **Winning the matchup.** The scout wins against a target when it kills it in
  at most stray_hunt_max_kill_seconds, and stray_hunt_win_ratio times as fast
  as the target kills it, both at their health now. At full health a jeep
  wins against a rifleman, a dog and a rocket soldier, not against a jeep or a
  flak truck; a rifleman wins against a rocket soldier, an engineer, a spy or
  a medic, not against a rifleman or a dog; a dog wins against a rifleman, not
  against a jeep; a flak truck wins against a jeep.
- **A target.** An enemy in sight that is not a building, not an aircraft and
  not an ore truck or MCV, that the scout wins against, and that is lone: no
  other enemy unit that fights and counts in a fight (`fight.is_counted`)
  stands within stray_hunt_lone_cells of it or of the scout.
- **Away from the base.** The target's cell and the scout's are each farther
  than a remembered ground defense's cover (`coverage.reach`) and
  stray_hunt_margin_cells from it, and farther than scout_enemy_base_cells and
  that margin from every remembered enemy building but a wall: a defense or a
  base would turn the duel.

A slot scout in the field that is not retreating, harassing or the rush
scout while the rush scout rule steers it (it never leaves its watch) hunts
the nearest target, whose hunt did not end other than `killed` in the last
stray_hunt_retry_seconds. While it hunts, its order is one `attack` on the
target, made again every HUNT_REORDER_SECONDS while the scout stands idle; its
steering, its mission (kept for later) and the model commander's order are set
aside, and its retreat's causes are not taken.

The hunt ends, judged in every cycle in this order: `killed` once the target
is gone or out of sight; `base_reach` once the target or the scout is no
longer away from the base; `not_lone` once another enemy unit that fights
stands near; `outmatched` once the scout no longer wins at the health both
have now; `hurt` once the scout has lost stray_hunt_max_hp_loss points of
health since the hunt began; `timeout` after stray_hunt_max_seconds. After
`killed`, `base_reach` and `timeout` the scout takes up its mission again;
after `not_lone` and `outmatched` it begins a retreat for cause `outclassed`,
after `hurt` for cause `hit`, without a bait shot: it has just fired.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from ucla_agent.games.redalert import cells, coverage, fight
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.scout_retreat import HIT, OUTCLASSED
from ucla_agent.games.redalert.scout_steering import enemy_base_cells
from ucla_agent.games.redalert.tuning import ScoutingTuning
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

_PERCENT = 100
# The enemy units a scout never hunts: they take too long to kill to be worth the scout's time.
NEVER_HUNTED = frozenset({"harv", "mcv"})
# A hunting scout that stands idle is ordered to attack its target again this long after its last order.
HUNT_REORDER_SECONDS = 2
# Why a hunt ended.
KILLED = "killed"
BASE_REACH = "base_reach"
NOT_LONE = "not_lone"
OUTMATCHED = "outmatched"
HURT = "hurt"
TIMEOUT = "timeout"
# The ends after which the scout retreats, with the retreat's cause; after the others it takes up its mission again.
RETREAT_CAUSES = {NOT_LONE: OUTCLASSED, OUTMATCHED: OUTCLASSED, HURT: HIT}


def seconds_to_kill(attacker_type: str, target_type: str, target_hp: int, world: World) -> float:
    """How long a unit of one type takes to kill an enemy of another at `target_hp` percent of its health, in seconds;
    infinite when it does it no damage or the target's hit points are unknown."""
    damage = world.items.damage
    per_second = damage.get(attacker_type).dps_to(damage.get(target_type))
    facts = world.items.get(target_type)
    if per_second <= 0 or facts is None or facts.hp is None:
        return math.inf
    return facts.hp * target_hp / _PERCENT / per_second


def wins_matchup(scout: Unit, target: EnemyRecord, world: World, tuning: ScoutingTuning) -> bool:
    """Whether a scout clearly wins a duel with an enemy at the health both have now: it kills the enemy within
    stray_hunt_max_kill_seconds, and stray_hunt_win_ratio times as fast as the enemy kills it."""
    to_kill = seconds_to_kill(scout.type, target.type, target.hp, world)
    to_die = seconds_to_kill(target.type, scout.type, scout.hp, world)
    return to_kill <= tuning.stray_hunt_max_kill_seconds and to_kill * tuning.stray_hunt_win_ratio <= to_die


def is_lone(target: EnemyRecord, scout: Unit, world: World, tuning: ScoutingTuning) -> bool:
    """Whether no other enemy unit that fights and counts in a fight stands within stray_hunt_lone_cells of the target
    or of the scout."""
    reach, around = tuning.stray_hunt_lone_cells, ((target.x, target.y), (scout.x, scout.y))
    return not any(
        record.id != target.id
        and fight.is_counted(record)
        and any(cells.is_within((record.x, record.y), cell, reach) for cell in around)
        for record in world.enemy_memory.values()
    )


def is_away_from_base(cell: Cell, world: World, ctx: MatchContext) -> bool:
    """Whether a cell lies farther than every remembered ground defense's cover and stray_hunt_margin_cells from it,
    and farther than scout_enemy_base_cells and that margin from every remembered enemy building but a wall."""
    tuning = ctx.tuning
    margin = tuning.scouting.stray_hunt_margin_cells
    for defense in fight.ground_defenses(world):
        if cells.is_within(
            cell, (defense.x, defense.y), coverage.reach(defense.type, world.items, tuning.coverage) + margin
        ):
            return False
    base_reach = tuning.scouting.scout_enemy_base_cells + margin
    return not any(cells.is_within(cell, building, base_reach) for building in enemy_base_cells(world))


def is_huntable(record: EnemyRecord, world: World) -> bool:
    """Whether an enemy may be hunted at all: in sight, and not a building, an aircraft, an ore truck or an MCV."""
    is_aircraft = world.items.damage.get(record.type).air
    return record.visible and not record.is_building and not is_aircraft and record.type not in NEVER_HUNTED


def targets(scout: Unit, world: World, ctx: MatchContext) -> list[EnemyRecord]:
    """The enemies a scout may hunt now (see the module's note), the nearest first, ties by the lower id; none while the
    scout itself is not away from the base."""
    tuning, position = ctx.tuning.scouting, (scout.x, scout.y)
    if not is_away_from_base(position, world, ctx):
        return []
    found = [
        record
        for record in world.enemy_memory.values()
        if is_huntable(record, world)
        and wins_matchup(scout, record, world, tuning)
        and is_lone(record, scout, world, tuning)
        and is_away_from_base((record.x, record.y), world, ctx)
    ]
    return sorted(found, key=lambda record: (cells.distance(position, (record.x, record.y)), record.id))


@dataclass
class Hunt:
    """One scout's hunt: its target, when it began, the scout's health then, and the tick of its last attack order."""

    target_id: int
    target_type: str
    started_tick: int
    start_hp: int
    # None until the first attack order has gone out.
    ordered_tick: int | None = None


@dataclass(frozen=True)
class Ended:
    """A hunt that ended in a cycle: the slot whose scout hunted, the hunt and why it ended."""

    slot: int
    hunt: Hunt
    reason: str


class Hunts:
    """The hunts of one match's slot scouts. See the module's note."""

    def __init__(self) -> None:
        # Unit id -> its hunt, while one lasts.
        self._active: dict[int, Hunt] = {}
        # Target id -> the tick its last hunt ended other than `killed`.
        self._spared: dict[int, int] = {}

    @property
    def hunting(self) -> frozenset[int]:
        """The scouts that hunt."""
        return frozenset(self._active)

    def end(self, scouts: Mapping[int, Unit], world: World, ctx: MatchContext) -> list[Ended]:
        """End the hunts whose end has come, of the slots' scouts in the field (`scouts`, by slot number), each one
        with why; a hunt whose scout is no longer in the field is forgotten."""
        live = {scout.id for scout in scouts.values()}
        self._active = {unit_id: hunt for unit_id, hunt in self._active.items() if unit_id in live}
        ended = []
        for number, scout in scouts.items():
            hunt = self._active.get(scout.id)
            reason = None if hunt is None else _end_reason(scout, hunt, world, ctx)
            if reason is None:
                continue
            del self._active[scout.id]
            if reason != KILLED:
                self._spared[hunt.target_id] = world.tick
            ended.append(Ended(number, hunt, reason))
        return ended

    def start(self, scout: Unit, world: World, ctx: MatchContext) -> Hunt | None:
        """Have a scout that may hunt begin a hunt of the nearest target not spared lately, with the switch on; the hunt
        begun, None for none."""
        tuning = ctx.tuning.scouting
        if not tuning.scout_stray_hunt or scout.id in self._active:
            return None
        retry_ticks = tuning.stray_hunt_retry_seconds * TICKS_PER_SECOND
        spared = {target for target, tick in self._spared.items() if world.tick - tick < retry_ticks}
        target = next((record for record in targets(scout, world, ctx) if record.id not in spared), None)
        if target is None:
            return None
        hunt = Hunt(target.id, target.type, world.tick, scout.hp)
        self._active[scout.id] = hunt
        return hunt

    def due(self, scout: Unit, world: World) -> EnemyRecord | None:
        """The target of a hunting scout whose attack order is due in this cycle: none sent yet, or the scout stands idle
        HUNT_REORDER_SECONDS after the last; None when it is not due or the scout does not hunt."""
        hunt = self._active.get(scout.id)
        if hunt is None:
            return None
        last = hunt.ordered_tick
        is_due = last is None or (scout.idle and world.tick - last >= HUNT_REORDER_SECONDS * TICKS_PER_SECOND)
        return world.enemy_memory[hunt.target_id] if is_due else None

    def ordered(self, unit_id: int, tick: int) -> None:
        """Note that a hunting scout's attack order went out at a tick."""
        hunt = self._active.get(unit_id)
        if hunt is not None:
            hunt.ordered_tick = tick


def _end_reason(scout: Unit, hunt: Hunt, world: World, ctx: MatchContext) -> str | None:
    """Why a hunt ends in this cycle, the first that holds in the order of the module's note; None while it goes on."""
    tuning = ctx.tuning.scouting
    target = world.enemy_memory.get(hunt.target_id)
    if target is None or not target.visible:
        return KILLED
    if not is_away_from_base((target.x, target.y), world, ctx) or not is_away_from_base((scout.x, scout.y), world, ctx):
        return BASE_REACH
    if not is_lone(target, scout, world, tuning):
        return NOT_LONE
    if not wins_matchup(scout, target, world, tuning):
        return OUTMATCHED
    if hunt.start_hp - scout.hp >= tuning.stray_hunt_max_hp_loss:
        return HURT
    if world.tick - hunt.started_tick >= tuning.stray_hunt_max_seconds * TICKS_PER_SECOND:
        return TIMEOUT
    return None
