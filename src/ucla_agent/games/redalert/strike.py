"""The strike on an enemy expansion or defense near us: a must-kill target struck at once, and not recalled.

An enemy MCV left alone deploys beside our refinery, and an enemy turret
whose range takes in a refinery kills it; artillery sent at such a turret and
called back by the next change of plan never fires. So with
`strike_near_expansion` of `[combat]` on, some enemy objects must die, the
figures those of `[combat]`:

- **Expansion:** an enemy MCV, or an enemy construction yard, within
  expansion_near_cells of any own building or of the nearest cell of any
  field one of our harvesters harvests (`MatchContext.harvested_fields`).
- **Defense:** an enemy ground defense (`fight.ground_defenses`: never the AA
  gun or the SAM site) whose weapon range plus
  defense_near_refinery_margin_cells reaches any own refinery.

Only what the enemy memory holds is weighed, so an own defense, the harassment
towers among them, is never a target. A target counts while it was in sight
less than `UNSEEN_SECONDS` ago: one unseen longer may be gone. The targets
are taken the one nearest the center of our base first.

**The strike.** While a must-kill target is known, one strike is in force on
the first of them not resting that main may strike (`Strike`, followed by
the combat manager in every cycle before main's orders and the commander's
question). A target is weighed as the over-extended strike weighs its field,
so that an enemy yard beside an enemy army never reads as an easy kill:

- **Ratio at the target** (`near_ratio`): `fight.ratio_against_target` of
  the units that can hurt it against the enemy units in sight within
  `DEFENSE_SUPPORT_CELLS` (8) of it and every ground defense whose range
  reaches it (`command_targets.field_weighed_with`), a defense target itself
  among them, each defense at defense_weight times its cost. An MCV or a
  yard fires at nothing: with nothing that fights beside it, it reads
  `LONE_TARGET_RATIO` (1.5), not ratio_without_enemy (99), and the enemy
  army's estimate decides whether main may go.
- **Strike force:** the smallest set of main's units on no detachment of the
  commander's and gathered at the staging point (`gathered_at`: at least
  gathered_share of them within gathered_cells of it) that can hurt the
  target, taken the nearest to it first and for a defense its artillery and
  V2s first, whose ratio at the target is at least strike_min_ratio and
  whose strength (`fight.strength`) is at least `ENEMY_ARMY_SHARE` (0.5) of
  the enemy army's estimate (`enemy_army_value`). Units straggling home are
  never sent one by one.
- **Main as a whole:** with no strike force, main as a whole strikes the
  target only when it lies within far_rescue_cells of `[base_defense]` (20)
  of the center of our base: its target is main's objective under every
  plan, and the commander's objective while the commander commands main
  (`MatchContext.strike_target`, see `combat` and `command_army`), until main
  can field a strike force at the staging point. A target farther off is not
  struck: the next target is tried, and each one declined is a `combat` event
  with the reason `strike_near_declined` at most once in
  `DECLINED_REPEAT_SECONDS` (30) for that target, with its distance from
  the base, the gathered units' count, ratio and strength and the enemy
  army's estimate. A must-kill target due, struck or declined, keeps the
  over-extended strike from starting.
- **Orders:** the force attack-moves to the target, and attacks it once
  within `ATTACK_CELLS` of it while it is in sight; on a defense its
  long-range units fire from their band (`siege.Siege.fire_on`).
- **No recall:** its units take none of main's orders while it holds: no
  plan change, gather, forward gate or pick of the commander's moves them.
  Only two things end it early: in every cycle, its ratio at the target or
  the estimate of its own fight where it stands (`fight.estimate`, the enemy
  met on the way) under strike_abandon_ratio of `[plan_rules]` (0.9), which
  ends it `abandoned` with an emergency move of its units (main's, for main
  as a whole) to the staging point; and base defense calling main home or
  taking it over against a major threat (`recalled`).
- **End:** the strike ends when its target has left the enemy memory
  (`destroyed`), has not been seen for `UNSEEN_SECONDS` (`unseen`), or when
  every unit of its force is gone (`lost`). A target whose strike was
  abandoned or lost is not struck again for `REST_SECONDS`.

Each start and end is a `combat` event with the reason `strike_near`.

**The over-extended strike.** With `strike_overextended` of `[combat]` on,
an enemy refinery or harvesters at an ore field far from the enemy's start
and its yards, with no defense near (`overextended.targets`), is struck by
main as one group. While no strike is in force and no must-kill target is
due, it starts on the first such field not resting when all of these hold:

- the base is under no major threat, the plan is not PRESERVE, and the model
  commander's standing action for main is none of pull back, retreat home
  and hold, nor is its retreat override on (`held_back`);
- at least gathered_share of main's units on no detachment stand within
  gathered_cells of the staging point: main goes gathered, never piecemeal;
- those gathered units that can hurt the field's refinery and harvesters
  (the strike force) are worth strike_min_ratio against them by
  `fight.ratio_against_target`, which weighs both sides with `fight.weigh`:
  the field's actors counted with the enemy units in sight within
  `DEFENSE_SUPPORT_CELLS` of its lead and every ground defense whose range
  reaches it (`command_targets.reaches`);
- and the strike force is worth (`fight.strength`) at least
  `ENEMY_ARMY_SHARE` of what the enemy army is estimated at
  (`enemy_army_value`), so that a field worth nothing in a fight never
  draws main out against a strong enemy army.

The refinery and harvesters there are the focus targets: a unit within
`ATTACK_CELLS` of one in sight attacks the nearest, the others attack-move
to the lead's cell. In every cycle the strike ends `destroyed` once no
refinery and no harvester is left there and none of those seen there is
remembered, `unseen` once none is left there but some is remembered
(harvesters out of sight for `overextended.UNSEEN_SECONDS`), `abandoned`
once the force's ratio at the field, or the estimate of its own fight where
it stands (`fight.estimate`, the enemy met on the way), falls under
strike_abandon_ratio of `[plan_rules]` (0.9), `lost` with every unit gone, and
`recalled` as the strike near us is. On every end but the recall its units
go back to the staging point at once; a field whose strike was abandoned or
lost rests for `REST_SECONDS`. Each start and end is a `combat` event with
the reason `strike_overextended`.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math
from typing import Mapping

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, overextended, siege
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.command_targets import field_weighed_with, range_cells
from ucla_agent.games.redalert.composition import PRESERVE
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD, MCV, REFINERY
from ucla_agent.games.redalert.fight import can_hurt, estimate, ground_defenses, ratio_against_target, strength, weigh
from ucla_agent.games.redalert.groups import SIEGE_TYPES
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MAJOR_THREAT, MatchContext
from ucla_agent.games.redalert.overextended import FieldTarget
from ucla_agent.games.redalert.terrain import OrePatch
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

# A target unseen this long is no longer one, and a strike on it ends.
UNSEEN_SECONDS = 30
# A target whose strike was abandoned or lost is not struck again for this long.
REST_SECONDS = 30
# A target declined for want of a gathered force, far from our base, is told at most once in this long.
DECLINED_REPEAT_SECONDS = 30
# A unit of the strike this near its target in sight attacks it rather than attack-moving to its cell.
ATTACK_CELLS = 6
# The enemy actors that make an expansion.
EXPANSION_TYPES = frozenset({MCV, CONSTRUCTION_YARD})
COMBAT_EVENT = "combat"
STRIKE_NEAR = "strike_near"
STRIKE_OVEREXTENDED = "strike_overextended"
STRIKE_NEAR_DECLINED = "strike_near_declined"
# Why a strike ends.
DESTROYED = "destroyed"
UNSEEN = "unseen"
LOST = "lost"
RECALLED = "recalled"
ABANDONED = "abandoned"
# The outcomes after which a target rests.
_RESTING_OUTCOMES = frozenset({LOST, ABANDONED})
# A strike starts only with a force worth at least this share of the enemy army's estimate.
ENEMY_ARMY_SHARE = 0.5
# The ratio at a target with nothing that fights beside it, an MCV or a yard alone: enough to strike it, so that the
# enemy army's estimate decides, where ratio_without_enemy would read 99 whatever the army near it.
LONE_TARGET_RATIO = 1.5
_RATIO_DECIMALS = 2


class Strike:
    """The strike in force on a must-kill target and its units, the targets resting and those declined lately. See the
    module's note."""

    def __init__(self) -> None:
        # The target struck; None while there is no strike.
        self.target_id: int | None = None
        # The strike force out of main; empty while there is none, main as a whole striking.
        self._units: frozenset[int] = frozenset()
        # Target id -> the tick until which it is not struck again.
        self._resting: dict[int, int] = {}
        # Target id -> the tick its strike_near_declined event was last written.
        self._declined: dict[int, int] = {}
        self._siege = siege.Siege()
        # The field of an over-extended strike, the ids of every refinery and harvester seen there in it, and those
        # there in the latest cycle; None and none for a strike near us and while there is no strike.
        self._field: OrePatch | None = None
        self._seen_there: frozenset[int] = frozenset()
        self._there: tuple[EnemyRecord, ...] = ()

    @property
    def units(self) -> frozenset[int]:
        """The units of the strike force out of main."""
        return self._units

    def objective(self, world: World) -> EnemyRecord | None:
        """The target of a strike with no strike force, which main as a whole strikes; None otherwise, and while the
        enemy memory no longer holds it."""
        if self.target_id is None or self._units:
            return None
        return world.enemy_memory.get(self.target_id)

    def follow(
        self,
        main: Mapping[int, Unit],
        taken_back: bool,
        point: Cell | None,
        world: World,
        ctx: MatchContext,
        *,
        held_back: bool = False,
    ) -> list[Intent]:
        """Bring the strike up to date in this cycle, main's units on no detachment of the commander's being `main`:
        end it when it is over or `taken_back` (base defense has main), else start one on the first must-kill target
        not resting that main may strike while the staging point `point` is known, else an over-extended strike unless
        the model commander holds main back (`held_back`), and give a strike with no force one once main can field it
        gathered at the point. The emergency moves to the staging point of the units of a strike abandoned in this
        cycle (main's, for main as a whole), or of an over-extended strike that ended but by a recall; none otherwise,
        and none while the point is not known."""
        tick = world.tick
        self._resting = {target_id: until for target_id, until in self._resting.items() if until > tick}
        quiet = DECLINED_REPEAT_SECONDS * TICKS_PER_SECOND
        self._declined = {target_id: told for target_id, told in self._declined.items() if tick - told < quiet}
        if self.target_id is None:
            if not taken_back and point is not None:
                self._begin(main, point, world, ctx, held_back=held_back)
            return []
        if self._field is not None:
            return self._follow_overextended(self._field, main, taken_back, point, world, ctx)
        target = world.enemy_memory.get(self.target_id)
        reason = RECALLED if taken_back else self._ended(target, main, world, ctx)
        if reason is not None:
            return self._end(reason, main, point, world)
        # A target the enemy memory no longer holds has ended the strike (`_ended`): `target` is the record here.
        if not self._units and point is not None:
            self._units = frozenset(unit.id for unit in gathered_force(target, main, point, world, ctx))
        return []

    def orders(self, main: Mapping[int, Unit], world: World, ctx: MatchContext) -> list[Intent]:
        """The orders of the strike force in this cycle: an attack-move to the target, an attack on it in sight once
        near, and on a defense the stand-off fire of its long-range units; on an over-extended field, an attack on the
        nearest of its refinery and harvesters in sight once near one, an attack-move to the lead's cell otherwise."""
        units = [main[unit_id] for unit_id in sorted(self._units) if unit_id in main]
        if self._field is not None:
            return _focus_on(units, self._there)
        target = None if self.target_id is None else world.enemy_memory.get(self.target_id)
        if not units or target is None:
            return []
        long_range = [unit for unit in units if unit.type in SIEGE_TYPES] if _is_defense(target, world) else []
        firing = {unit.id for unit in long_range}
        fire = self._siege.fire_on(long_range, target, world, ctx.tuning) if long_range else []
        return [*fire, *_strike_at([unit for unit in units if unit.id not in firing], target)]

    def _begin(
        self, main: Mapping[int, Unit], point: Cell, world: World, ctx: MatchContext, *, held_back: bool
    ) -> None:
        """Start a strike on the first must-kill target not resting that main may strike, else, while no must-kill target
        is due, an over-extended strike unless the model commander holds main back (`held_back`)."""
        if not self._start(main, point, world, ctx) and not held_back:
            self._start_overextended(main, point, world, ctx)

    def _start(self, main: Mapping[int, Unit], point: Cell, world: World, ctx: MatchContext) -> bool:
        """Start a strike on the first must-kill target not resting that main may strike, from main gathered at the
        staging point `point` or as a whole near our base, telling each target declined before it; whether a
        must-kill target is due, struck or declined."""
        targets = [record for record in must_kill_targets(world, ctx) if record.id not in self._resting]
        for target in targets:
            force = gathered_force(target, main, point, world, ctx)
            if force or _is_near_base(target, world, ctx):
                self._begin_near(target, force, world, ctx)
                return True
            self._decline(target, main, point, world, ctx)
        return bool(targets)

    def _begin_near(self, target: EnemyRecord, force: list[Unit], world: World, ctx: MatchContext) -> None:
        """Make a strike of a force, or of main as a whole for none, on a must-kill target the strike in force, and
        write its start event."""
        self.target_id, self._units = target.id, frozenset(unit.id for unit in force)
        event = {
            "tick": world.tick,
            "reason": STRIKE_NEAR,
            "phase": "start",
            "target": target.id,
            "target_type": target.type,
            "cell": [target.x, target.y],
            "units": sorted(self._units),
            "ratio": round(near_ratio(force, target, world, ctx), _RATIO_DECIMALS),
        }
        records.log(COMBAT_EVENT, event)

    def _decline(
        self, target: EnemyRecord, main: Mapping[int, Unit], point: Cell, world: World, ctx: MatchContext
    ) -> None:
        """Write the strike_near_declined event of a target main may not strike, unless one was written for it within
        DECLINED_REPEAT_SECONDS: its distance from our base, and the count, ratio at the target and strength of main's
        units gathered at the staging point `point` against the enemy army's estimate."""
        if target.id in self._declined:
            return
        self._declined[target.id] = world.tick
        gathered = gathered_at(main, point, ctx)
        event = {
            "tick": world.tick,
            "reason": STRIKE_NEAR_DECLINED,
            "target": target.id,
            "target_type": target.type,
            "cell": [target.x, target.y],
            "distance": round(_distance(target, world.base_center), 1),
            "gathered": len(gathered),
            "ratio": round(near_ratio(gathered, target, world, ctx), _RATIO_DECIMALS),
            "strength": round(strength(gathered, world)),
            "enemy_army": enemy_army_value(ctx),
        }
        records.log(COMBAT_EVENT, event)

    def _start_overextended(self, main: Mapping[int, Unit], point: Cell, world: World, ctx: MatchContext) -> None:
        """Start an over-extended strike on the first field not resting that main, gathered at the staging point
        `point`, may strike (see the module's note), and write its start event."""
        if not may_strike_overextended(ctx):
            return
        gathered = gathered_at(main, point, ctx)
        if not gathered:
            return
        for target in overextended.targets(world, ctx):
            force = [unit for unit in gathered if _hurts(unit, target.records, world)]
            if target.lead.id not in self._resting and _is_worth(force, target, world, ctx):
                self._begin_overextended(force, target, world, ctx)
                return

    def _begin_overextended(self, force: list[Unit], target: FieldTarget, world: World, ctx: MatchContext) -> None:
        """Make an over-extended strike of a force on a field the strike in force, and write its start event."""
        self.target_id, self._units = target.lead.id, frozenset(unit.id for unit in force)
        self._field, self._there = target.field, target.records
        self._seen_there = frozenset(record.id for record in target.records)
        event = {
            "tick": world.tick,
            "reason": STRIKE_OVEREXTENDED,
            "phase": "start",
            "target": target.lead.id,
            "target_type": target.lead.type,
            "cell": list(target.cell),
            "focus": sorted(self._seen_there),
            "units": sorted(self._units),
            "ratio": round(field_ratio(force, target.records, world, ctx), _RATIO_DECIMALS),
        }
        records.log(COMBAT_EVENT, event)

    def _follow_overextended(
        self,
        field: OrePatch,
        main: Mapping[int, Unit],
        taken_back: bool,
        point: Cell | None,
        world: World,
        ctx: MatchContext,
    ) -> list[Intent]:
        """Bring an over-extended strike on a field up to date: ended when it is over or `taken_back`, and then the
        moves of its units to the staging point `point`."""
        reason = RECALLED if taken_back else self._overextended_ended(field, main, world, ctx)
        return [] if reason is None else self._end(reason, main, point, world)

    def _overextended_ended(
        self, field: OrePatch, main: Mapping[int, Unit], world: World, ctx: MatchContext
    ) -> str | None:
        """Why an over-extended strike on a field is over in this cycle, its refinery and harvesters found there
        again first: nothing left there, its force gone, or its ratio there or around itself under
        strike_abandon_ratio; None while it goes on."""
        self._there = tuple(overextended.at_field(field, world, ctx))
        self._seen_there |= {record.id for record in self._there}
        if not self._there:
            remembered = any(record_id in world.enemy_memory for record_id in self._seen_there)
            return UNSEEN if remembered else DESTROYED
        active = [main[unit_id] for unit_id in self._units if unit_id in main]
        if not active:
            return LOST
        return ABANDONED if is_losing(field_ratio(active, self._there, world, ctx), active, world, ctx) else None

    def _ended(
        self, target: EnemyRecord | None, main: Mapping[int, Unit], world: World, ctx: MatchContext
    ) -> str | None:
        """Why the strike is over in this cycle, its target as the enemy memory holds it (None once it no longer
        does): destroyed or unseen too long, its force gone, or the ratio at the target or the fight where it stands of
        its force (of main, for main as a whole) under strike_abandon_ratio; None while it goes on."""
        if target is None:
            return DESTROYED
        if not is_seen_lately(target, world):
            return UNSEEN
        active = [main[unit_id] for unit_id in self._units if unit_id in main] if self._units else list(main.values())
        if not active:
            return LOST if self._units else None
        return ABANDONED if is_losing(near_ratio(active, target, world, ctx), active, world, ctx) else None

    def _end(self, reason: str, main: Mapping[int, Unit], point: Cell | None, world: World) -> list[Intent]:
        """End the strike for a reason with its end event: a target whose strike was lost or abandoned rests, and the
        units of a strike abandoned (main's, for main as a whole), or of an over-extended strike that ended but by a
        recall, go to the staging point `point` at once, when it is known."""
        target_id, units, is_far = self.target_id, self._units, self._field is not None
        self.target_id, self._units = None, frozenset()
        self._field, self._seen_there, self._there = None, frozenset(), ()
        if reason in _RESTING_OUTCOMES:
            self._resting[target_id] = world.tick + REST_SECONDS * TICKS_PER_SECOND
        kind = STRIKE_OVEREXTENDED if is_far else STRIKE_NEAR
        event = {"tick": world.tick, "reason": kind, "phase": "end", "target": target_id, "outcome": reason}
        records.log(COMBAT_EVENT, event)
        leaving = sorted(unit_id for unit_id in (units or main) if unit_id in main)
        is_homeward = reason == ABANDONED or (is_far and reason != RECALLED)
        if not is_homeward or not leaving or point is None:
            return []
        return [Intent(Priority.EMERGENCY, {"cmd": "move", "units": leaving, "to": list(point)})]


def gathered_force(
    target: EnemyRecord, main: Mapping[int, Unit], point: Cell, world: World, ctx: MatchContext
) -> list[Unit]:
    """The strike force main can field on a must-kill target from its units gathered at the staging point `point`
    (`gathered_at`); none while main is not gathered there or its gathered units fall short (`strike_force`)."""
    return strike_force(target, gathered_at(main, point, ctx), world, ctx)


def strike_force(target: EnemyRecord, units: list[Unit], world: World, ctx: MatchContext) -> list[Unit]:
    """The smallest set of the units given that can hurt a target, the nearest to it first and for a defense the
    long-range units first, whose ratio at it (`near_ratio`) is at least strike_min_ratio and whose strength is at
    least ENEMY_ARMY_SHARE of the enemy army's estimate; none when all of them fall short."""
    defense = _is_defense(target, world)
    cell = _cell(target)
    hurting = sorted(
        (unit for unit in units if can_hurt(unit.type, target.type, world)),
        key=lambda unit: (defense and unit.type not in SIEGE_TYPES, cells.distance((unit.x, unit.y), cell), unit.id),
    )
    weighed_with = field_weighed_with(cell, world, ctx)
    least, army = ctx.tuning.combat.strike_min_ratio, ENEMY_ARMY_SHARE * enemy_army_value(ctx)

    def is_enough(count: int) -> bool:
        chosen = hurting[:count]
        return _ratio_at(chosen, target, weighed_with, world, ctx) >= least and strength(chosen, world) >= army

    if not hurting or not is_enough(len(hurting)):
        return []
    # More units only add strength, so the fewest that reach the ratio and the share of the army are found by halving.
    fewest, most = 1, len(hurting)
    while fewest < most:
        middle = (fewest + most) // 2
        if is_enough(middle):
            most = middle
        else:
            fewest = middle + 1
    return hurting[:fewest]


def near_ratio(units: list[Unit], target: EnemyRecord, world: World, ctx: MatchContext) -> float:
    """The ratio at a must-kill target of the units given that can hurt it, by `fight.ratio_against_target`, against
    the enemy units in sight within DEFENSE_SUPPORT_CELLS of it and every ground defense whose range reaches it, a
    defense target itself among them (`command_targets.field_weighed_with`); LONE_TARGET_RATIO when those are worth
    nothing, and 0 when none of the units can hurt it."""
    return _ratio_at(units, target, field_weighed_with(_cell(target), world, ctx), world, ctx)


def _ratio_at(
    units: list[Unit],
    target: EnemyRecord,
    weighed_with: tuple[list[EnemyRecord], list[EnemyRecord]],
    world: World,
    ctx: MatchContext,
) -> float:
    """`near_ratio` with what the target is weighed with, its enemy units and its defenses, given."""
    enemies, defenses = weighed_with
    ratio = ratio_against_target(units, (target,), world, ctx.tuning, enemies, defenses)
    is_lone = weigh([], enemies, world, ctx.tuning, defenses)[1] <= 0
    return LONE_TARGET_RATIO if ratio > 0 and is_lone else ratio


def is_losing(ratio_there: float, active: list[Unit], world: World, ctx: MatchContext) -> bool:
    """Whether a strike's units are to give it up: their ratio at the target, `ratio_there`, or the estimate of their
    own fight where they stand (`fight.estimate`, the enemy met on the way) under strike_abandon_ratio of
    `[plan_rules]`."""
    least = ctx.tuning.plan_rules.strike_abandon_ratio
    around = estimate(active, world, ctx.tuning)
    return ratio_there < least or (around is not None and around.ratio < least)


def _is_near_base(target: EnemyRecord, world: World, ctx: MatchContext) -> bool:
    """Whether main as a whole may strike a target: it lies within far_rescue_cells of `[base_defense]` of the center
    of our base, which is known."""
    home = world.base_center
    return home is not None and cells.is_within(_cell(target), home, ctx.tuning.base_defense.far_rescue_cells)


def _is_defense(target: EnemyRecord, world: World) -> bool:
    """Whether a target is an enemy ground defense, rather than an expansion."""
    return any(defense.id == target.id for defense in ground_defenses(world))


def _strike_at(units: list[Unit], target: EnemyRecord) -> list[Intent]:
    """An attack on a target in sight for the units within ATTACK_CELLS of it, an attack-move to its cell for the
    others."""
    cell = _cell(target)
    near = [unit.id for unit in units if target.visible and cells.is_within((unit.x, unit.y), cell, ATTACK_CELLS)]
    going = [unit.id for unit in units if unit.id not in near]
    attack = [Intent(Priority.MOVE, {"cmd": "attack", "units": near, "target": target.id})] if near else []
    move = [Intent(Priority.MOVE, {"cmd": "attack_move", "units": going, "to": list(cell)})] if going else []
    return [*attack, *move]


def may_strike_overextended(ctx: MatchContext) -> bool:
    """Whether an over-extended strike may start by the match's state: strike_overextended is on, the base is under no
    major threat and the plan is not PRESERVE."""
    return ctx.tuning.combat.strike_overextended and ctx.threat_level != MAJOR_THREAT and ctx.plan != PRESERVE


def gathered_at(main: Mapping[int, Unit], point: Cell, ctx: MatchContext) -> list[Unit]:
    """main's units within gathered_cells of the staging point `point`, while at least gathered_share of them stand
    there; none otherwise: main goes as one group or not at all."""
    tuning = ctx.tuning.combat
    gathered = [unit for unit in main.values() if cells.is_within((unit.x, unit.y), point, tuning.gathered_cells)]
    return gathered if gathered and len(gathered) >= tuning.gathered_share * len(main) else []


def field_ratio(units: list[Unit], there: tuple[EnemyRecord, ...], world: World, ctx: MatchContext) -> float:
    """The ratio of some units against the refinery and harvesters of an over-extended field (`there`, its lead
    first), by `fight.ratio_against_target` (each side weighed by `fight.weigh`): the units that can hurt them against
    the enemy units in sight within DEFENSE_SUPPORT_CELLS of the lead and every ground defense whose range reaches it,
    a defense at defense_weight times its cost."""
    support, covering = field_weighed_with((there[0].x, there[0].y), world, ctx)
    return ratio_against_target(units, there, world, ctx.tuning, support, covering)


def enemy_army_value(ctx: MatchContext) -> int:
    """What the enemy army is estimated at: the most it has been seen worth (the model commander's peak, see
    `command_army.EnemyPeak`) while the commander is on, and what scouting reports it worth now in any case."""
    peak = 0 if ctx.commander is None else ctx.commander.peak.value
    return max(peak, ctx.scout_report.enemy_combat_value)


def _is_worth(force: list[Unit], target: FieldTarget, world: World, ctx: MatchContext) -> bool:
    """Whether a force may strike an over-extended field: worth strike_min_ratio there, and at least ENEMY_ARMY_SHARE of
    the enemy army's estimate."""
    if not force or field_ratio(force, target.records, world, ctx) < ctx.tuning.combat.strike_min_ratio:
        return False
    return strength(force, world) >= ENEMY_ARMY_SHARE * enemy_army_value(ctx)


def _hurts(unit: Unit, there: tuple[EnemyRecord, ...], world: World) -> bool:
    """Whether a unit can hurt one of an over-extended field's refinery and harvesters."""
    return any(can_hurt(unit.type, record.type, world) for record in there)


def _focus_on(units: list[Unit], there: tuple[EnemyRecord, ...]) -> list[Intent]:
    """The orders of an over-extended strike: each unit within ATTACK_CELLS of one of the field's refinery and
    harvesters in sight attacks the nearest of them, the others attack-move to the lead's cell."""
    if not units or not there:
        return []
    in_sight = [record for record in there if record.visible]
    attacks: dict[int, list[int]] = {}
    going = []
    for unit in units:
        here = (unit.x, unit.y)
        near = [record for record in in_sight if cells.is_within(here, _cell(record), ATTACK_CELLS)]
        if not near:
            going.append(unit.id)
            continue
        aim = min(near, key=lambda record: (cells.distance(here, _cell(record)), record.id))
        attacks.setdefault(aim.id, []).append(unit.id)
    orders = [Intent(Priority.MOVE, {"cmd": "attack", "units": ids, "target": aim}) for aim, ids in attacks.items()]
    lead = [there[0].x, there[0].y]
    return [*orders, Intent(Priority.MOVE, {"cmd": "attack_move", "units": going, "to": lead})] if going else orders


def must_kill_targets(world: World, ctx: MatchContext) -> list[EnemyRecord]:
    """The enemy expansions and defenses near us that must die, the nearest to our base first, then by id; none with
    strike_near_expansion off. See the module's note."""
    if not ctx.tuning.combat.strike_near_expansion:
        return []
    expansions = [
        record
        for record in world.enemy_memory.values()
        if record.type in EXPANSION_TYPES and is_seen_lately(record, world) and _is_near_us(record, world, ctx)
    ]
    defenses = [
        record
        for record in ground_defenses(world)
        if is_seen_lately(record, world) and _reaches_refinery(record, world, ctx)
    ]
    found = {record.id: record for record in [*expansions, *defenses]}
    home = world.base_center
    return sorted(found.values(), key=lambda record: (_distance(record, home), record.id))


def is_seen_lately(record: EnemyRecord, world: World) -> bool:
    """Whether an enemy was in sight less than UNSEEN_SECONDS ago."""
    return world.tick - record.last_seen_tick < UNSEEN_SECONDS * TICKS_PER_SECOND


def _is_near_us(record: EnemyRecord, world: World, ctx: MatchContext) -> bool:
    """Whether an enemy stands within expansion_near_cells of an own building or of a field we harvest."""
    cell, reach = _cell(record), ctx.tuning.combat.expansion_near_cells
    if any(cells.is_within(cell, (building.x, building.y), reach) for building in world.buildings.values()):
        return True
    return any(cells.is_within(cell, field.nearest_cell(cell), reach) for field in ctx.harvested_fields)


def _reaches_refinery(defense: EnemyRecord, world: World, ctx: MatchContext) -> bool:
    """Whether a defense's weapon range plus defense_near_refinery_margin_cells reaches an own refinery."""
    reach = range_cells(defense.type, world, ctx) + ctx.tuning.combat.defense_near_refinery_margin_cells
    refineries = [(building.x, building.y) for building in world.buildings.values() if building.type == REFINERY]
    return any(cells.is_within(_cell(defense), refinery, reach) for refinery in refineries)


def _distance(record: EnemyRecord, home: Cell | None) -> float:
    """How far an enemy is from the center of our base; 0 while it is not known."""
    return 0.0 if home is None else math.dist(_cell(record), home)


def _cell(record: EnemyRecord) -> Cell:
    return record.x, record.y
