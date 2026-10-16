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

**Sized by its defenses.** With `strike_near_by_defenses` of `[combat]` on
(live, shadowfiend-ii, rush: two enemy yards stood 21 cells from our base the
whole second half of the match, declined each time for want of half the enemy
army's peak at the staging point): an enemy yard counts however long ago it
was seen (a building does not move: the enemy memory forgets it once it is
seen gone), and so does a defense; the strike force is the fewest units worth
strike_min_ratio at the target, its defenses and the enemy units near it
weighed, with no share of the enemy army's estimate asked, taken from main
gathered at the staging point or else gathered and idle wherever it stands
(`gathered_idle`); main as a whole strikes a target within
strike_near_whole_cells of our base's center. A defense main cannot field such
a force on is sieged instead by main's long-range units gathered with it (see
`siege.Siege.fire_on`), which the ratio at the target does not call off: only
the fight where they stand does. A strike on a building ends `timeout` after
strike_near_max_seconds.

**An open building.** With `strike_near_open_building` of `[combat]` on
(live, watch41-m8-rush-1007: an enemy yard deployed 35 cells from our base
was declined twice with main away, then struck by one unit, abandoned), a
must-kill enemy yard no known enemy ground defense reaches is struck from
main's gathered units and its idle units within strike_near_home_cells of our
base's center, the fewest that bring it down quickly
(`exposed_expansion.can_finish`), whatever the enemy army's estimate; for
strike_near_grace_seconds it is called off only by the fight where its units
stand, not by its ratio at the target.

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

**The exposed-expansion strike.** With `strike_exposed_expansion` of
`[combat]` on, a known enemy expansion nothing defends (`exposed_expansion`)
is struck by a strike force out of main gathered at the staging point, or
gathered and idle wherever it stands (holding at the side of the map, say),
sized as the strike near us is (`strike_force`) and able to bring the
expansion's buildings down quickly (`exposed_expansion.can_finish`). While
no strike is in force, no must-kill target is due and no over-extended
strike starts, it starts on the exposed building nearest main
(the center of our base, from the staging point) not resting and on no cell a
failed attack blocks (`failed_attack.blocking`, straight on) while the base is
under no major threat, the plan is not PRESERVE and the commander pulls main
neither back nor home (its retreat override, pull back or retreat home; a hold
gives way). Its target need not have been seen lately: buildings do not move.
It ends `destroyed` once its target has left the enemy memory, and its force
goes straight on to the exposed building nearest it within
exposed_expansion_chain_cells, a new strike with the same units; `lost`,
`abandoned` (its units then go to the staging point at once) and `recalled`
as the strike near us does, and `timeout` after exposed_expansion_max_seconds.
Each start and end is a `combat` event with the reason `strike_expansion`.
With `overextended_chain` of `[combat]` on, the force of an over-extended
strike that ends `destroyed` or `unseen` goes on the same way to the exposed
building nearest it within overextended_chain_cells rather than home (live,
sidestep, 16:20: the strike on a lone harvester ended unseen as it drove home
to the enemy's unguarded expansion 35 cells on, and main ran back to staging).

**The counterattack.** With `counterattack` of `[combat]` on, once a
defense of the base is won or a regroup of the commander's is over (see
`counterattack`), and main may go, main as one force, every unit of it on no
detachment wherever it stands, strikes the first counterattack target
(`counterattack.targets`: an exposed expansion first, else the enemy building
nearest our base no defense reaches) on no cell a failed attack blocks, before
any other strike but a must-kill one, whatever the plan (but PRESERVE) and
the commander's standing action (but its retreat override, pull back or
retreat home) say. It ends `timeout` counterattack_seconds after it started,
going on after each kill to the next target within
exposed_expansion_chain_cells of its force on the same clock; `lost`,
`abandoned` and `recalled` as the strike near us. Each start and end is a
`combat` event with the reason `counterattack`, its start with why it was due.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import functools
import math
from typing import Callable, Mapping

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, counterattack, exposed_expansion, failed_attack, overextended, siege
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.command_army import PULL_BACK, RETREAT_HOME
from ucla_agent.games.redalert.command_targets import field_weighed_with, range_cells
from ucla_agent.games.redalert.composition import PRESERVE
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD, MCV, REFINERY
from ucla_agent.games.redalert.fight import (
    can_hurt,
    centroid,
    estimate,
    ground_defenses,
    ratio_against_target,
    strength,
    weigh,
)
from ucla_agent.games.redalert.groups import SIEGE_TYPES
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MAJOR_THREAT, MatchContext
from ucla_agent.games.redalert.nuke import is_silo, known_silos
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
STRIKE_EXPANSION = "strike_expansion"
COUNTERATTACK = "counterattack"
# Why a strike ends.
DESTROYED = "destroyed"
UNSEEN = "unseen"
LOST = "lost"
RECALLED = "recalled"
ABANDONED = "abandoned"
TIMED_OUT = "timeout"
# The outcomes after which a target rests.
_RESTING_OUTCOMES = frozenset({LOST, ABANDONED})
# A strike starts only with a force worth at least this share of the enemy army's estimate.
ENEMY_ARMY_SHARE = 0.5
# The ratio at a target with nothing that fights beside it, an MCV or a yard alone: enough to strike it, so that the
# enemy army's estimate decides, where ratio_without_enemy would read 99 whatever the army near it.
LONE_TARGET_RATIO = 1.5
# The commander's standing actions for main that keep it from an exposed-expansion strike; a hold gives way to one.
PULLING_BACK = frozenset({PULL_BACK, RETREAT_HOME})
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
        # The event reason of the strike on one target in force, and the tick it ends at; None for the strike near us,
        # which ends once its target is unseen too long instead.
        self._kind = STRIKE_NEAR
        self._until: int | None = None
        # Whether the strike near us is a siege of a defense by long-range units, which its ratio there does not end.
        self._is_siege = False
        # The tick until which a strike on an open building is not abandoned on its ratio at the target; 0 for none.
        self._grace_until = 0
        # Whether a counterattack is due, after a won defense or a regroup (see `counterattack`).
        self._counter = counterattack.CounterWatch()

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
        self._counter.follow(point is not None and bool(gathered_at(main, point, ctx)), world, ctx)
        if self.target_id is None:
            if not taken_back and point is not None:
                self._begin(main, point, world, ctx, held_back=held_back)
            return []
        if self._field is not None:
            return self._follow_overextended(self._field, main, taken_back, point, world, ctx)
        target = world.enemy_memory.get(self.target_id)
        reason = RECALLED if taken_back else self._ended(target, main, world, ctx)
        if reason is not None:
            return self._end(reason, main, point, world, ctx)
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
        """Start a strike on the first must-kill target not resting that main may strike, else, while no must-kill
        target is due, an over-extended strike unless the model commander holds main back (`held_back`), else an
        exposed-expansion strike unless it pulls main back or home."""
        if self._start(main, point, world, ctx):
            return
        if held_back and _is_pulled_back(ctx):
            return
        if self._start_counter(main, world, ctx):
            return
        if held_back or not self._start_overextended(main, point, world, ctx):
            self._start_exposed(main, point, world, ctx)

    def _start(self, main: Mapping[int, Unit], point: Cell, world: World, ctx: MatchContext) -> bool:
        """Start a strike on the first must-kill target not resting that main may strike, from main gathered at the
        staging point `point` or as a whole near our base, telling each target declined before it; whether a
        must-kill target is due, struck or declined, a known silo declined (one main cannot strike yet) not due."""
        targets = [record for record in must_kill_targets(world, ctx) if record.id not in self._resting]
        by_defenses = ctx.tuning.combat.strike_near_by_defenses
        for target in targets:
            force = self._near_force(target, main, point, world, ctx)
            if force or _is_near_base(target, world, ctx):
                until = world.tick + ctx.tuning.combat.strike_near_max_seconds * TICKS_PER_SECOND
                self._begin_near(target, force, world, ctx, until=until if by_defenses else None)
                least = ctx.tuning.combat.strike_min_ratio
                self._is_siege = by_defenses and bool(force) and near_ratio(force, target, world, ctx) < least
                grace = ctx.tuning.combat.strike_near_grace_seconds * TICKS_PER_SECOND
                self._grace_until = world.tick + grace if force and _is_open_building(target, world, ctx) else 0
                return True
            self._decline(target, main, point, world, ctx)
        # A known silo main cannot strike yet holds back no other strike: it is struck once it is strikable.
        return any(not (_follows_up_nukes(ctx) and is_silo(target.type)) for target in targets)

    def _near_force(
        self, target: EnemyRecord, main: Mapping[int, Unit], point: Cell, world: World, ctx: MatchContext
    ) -> list[Unit]:
        """The strike force on a must-kill target: main's gathered at the staging point sized by `gathered_force`; with
        strike_near_by_defenses on, main gathered there or idle wherever it stands, sized by the target's defenses
        alone, and else the long-range units of those gathered units, for a defense, to siege it."""
        if not ctx.tuning.combat.strike_near_by_defenses:
            return gathered_force(target, main, point, world, ctx)
        gathered = gathered_at(main, point, ctx) or gathered_idle(main, ctx)
        if _is_open_building(target, world, ctx):
            return _open_building_force(target, gathered, main, world, ctx)
        force = strike_force(target, gathered, world, ctx, army_share=0.0)
        if force or not _is_defense(target, world):
            return force
        return [unit for unit in gathered if unit.type in SIEGE_TYPES and can_hurt(unit.type, target.type, world)]

    def _begin_near(
        self,
        target: EnemyRecord,
        force: list[Unit],
        world: World,
        ctx: MatchContext,
        kind: str = STRIKE_NEAR,
        until: int | None = None,
    ) -> None:
        """Make a strike of a force, or of main as a whole for none, on a target the strike in force, and write its
        start event: a must-kill target's, or one of `kind` ending at the tick `until`."""
        self.target_id, self._units = target.id, frozenset(unit.id for unit in force)
        self._kind, self._until = kind, until
        event = {
            "tick": world.tick,
            "reason": kind,
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

    def _start_overextended(self, main: Mapping[int, Unit], point: Cell, world: World, ctx: MatchContext) -> bool:
        """Start an over-extended strike on the first field not resting that main, gathered at the staging point
        `point`, may strike (see the module's note), and write its start event; whether one started."""
        if not may_strike_overextended(ctx):
            return False
        gathered = gathered_at(main, point, ctx)
        if not gathered:
            return False
        for target in overextended.targets(world, ctx):
            force = [unit for unit in gathered if _hurts(unit, target.records, world)]
            if target.lead.id not in self._resting and _is_worth(force, target, world, ctx):
                self._begin_overextended(force, target, world, ctx)
                return True
        return False

    def _start_exposed(self, main: Mapping[int, Unit], point: Cell, world: World, ctx: MatchContext) -> None:
        """Start an exposed-expansion strike on the building nearest our base, not resting, that a force of main's
        gathered at the staging point `point` is sized for, on no cell a failed attack blocks (see the module's
        note)."""
        if ctx.threat_level == MAJOR_THREAT or ctx.plan == PRESERVE:
            return
        gathered = gathered_at(main, point, ctx)
        origin = world.base_center if gathered else None
        gathered = gathered or gathered_idle(main, ctx)
        if not gathered:
            return
        for target in exposed_expansion.targets(origin or centroid(gathered), world, ctx):
            finishes = functools.partial(exposed_expansion.can_finish, target=target, world=world, ctx=ctx)
            force = [] if target.id in self._resting else strike_force(target, gathered, world, ctx, finishes)
            if force and not _is_blocked(target, force, world, ctx):
                self._begin_exposed(target, force, world, ctx)
                return

    def _start_counter(self, main: Mapping[int, Unit], world: World, ctx: MatchContext) -> bool:
        """Start the counterattack due, if any, with every unit of main on the first counterattack target on no cell a
        failed attack blocks, unless the plan is PRESERVE or main may not go (see the module's note); whether it
        started."""
        due = self._counter.due
        force = list(main.values())
        if due is None or not force or ctx.plan == PRESERVE:
            return False
        if not counterattack.may_start(strength(force, world), world, ctx):
            return False
        for target in counterattack.targets(world.base_center, world, ctx):
            if target.id not in self._resting and not _is_blocked(target, force, world, ctx):
                self._counter.taken()
                until = world.tick + ctx.tuning.combat.counterattack_seconds * TICKS_PER_SECOND
                self._begin_near(target, force, world, ctx, COUNTERATTACK, until)
                records.log(COMBAT_EVENT, {"tick": world.tick, "reason": COUNTERATTACK, "phase": "due", "why": due})
                return True
        return False

    def _begin_exposed(self, target: EnemyRecord, force: list[Unit], world: World, ctx: MatchContext) -> None:
        """Make an exposed-expansion strike of a force on a building the strike in force, for
        exposed_expansion_max_seconds."""
        until = world.tick + ctx.tuning.combat.exposed_expansion_max_seconds * TICKS_PER_SECOND
        self._begin_near(target, force, world, ctx, STRIKE_EXPANSION, until)

    def _go_on(
        self, units: frozenset[int], main: Mapping[int, Unit], reach: int, world: World, ctx: MatchContext
    ) -> bool:
        """Send the units of a strike that is over straight on to the exposed building nearest them within `reach`,
        not resting and on no cell a failed attack blocks, a new strike; whether they went."""
        force = [main[unit_id] for unit_id in sorted(units) if unit_id in main]
        if not force:
            return False
        middle = centroid(force)
        for target in exposed_expansion.targets(middle, world, ctx):
            if math.dist(middle, _cell(target)) > reach:
                continue
            if target.id not in self._resting and not _is_blocked(target, force, world, ctx):
                self._begin_exposed(target, force, world, ctx)
                return True
        return False

    def _press_on(
        self, units: frozenset[int], main: Mapping[int, Unit], until: int | None, world: World, ctx: MatchContext
    ) -> bool:
        """Send the force of a counterattack whose target was destroyed on to the next counterattack target within
        exposed_expansion_chain_cells of it, on the same clock `until`; whether it went."""
        force = [main[unit_id] for unit_id in sorted(units) if unit_id in main]
        if not force:
            return False
        middle = centroid(force)
        reach = ctx.tuning.combat.exposed_expansion_chain_cells
        for target in counterattack.targets(middle, world, ctx):
            near = math.dist(middle, _cell(target)) <= reach
            if near and target.id not in self._resting and not _is_blocked(target, force, world, ctx):
                self._begin_near(target, force, world, ctx, COUNTERATTACK, until)
                return True
        return False

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
        return [] if reason is None else self._end(reason, main, point, world, ctx)

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
        if self._until is not None and world.tick >= self._until:
            return TIMED_OUT
        # A silo does not move: it is struck until the enemy memory no longer holds it.
        is_held_silo = _follows_up_nukes(ctx) and is_silo(target.type)
        if self._until is None and not is_seen_lately(target, world) and not is_held_silo:
            return UNSEEN
        if self._kind == STRIKE_NEAR and not target.is_building and not is_seen_lately(target, world):
            return UNSEEN
        active = [main[unit_id] for unit_id in self._units if unit_id in main] if self._units else list(main.values())
        if not active:
            return LOST if self._units else None
        # A siege, or a strike on an open building in its first strike_near_grace_seconds, is called off only by the
        # fight where its units stand, not by its ratio at the target.
        is_grace = self._is_siege or world.tick < self._grace_until
        ratio = math.inf if is_grace else near_ratio(active, target, world, ctx)
        return ABANDONED if is_losing(ratio, active, world, ctx) else None

    def _end(
        self, reason: str, main: Mapping[int, Unit], point: Cell | None, world: World, ctx: MatchContext
    ) -> list[Intent]:
        """End the strike for a reason with its end event: a target whose strike was lost or abandoned rests; the units
        of an exposed-expansion strike whose target was destroyed go straight on to the next exposed building near
        them; and the units of a strike abandoned (main's, for main as a whole), or of an over-extended strike that
        ended but by a recall, go to the staging point `point` at once, when it is known."""
        target_id, units, is_far, until = self.target_id, self._units, self._field is not None, self._until
        kind = STRIKE_OVEREXTENDED if is_far else self._kind
        self.target_id, self._units, self._kind, self._until = None, frozenset(), STRIKE_NEAR, None
        self._is_siege, self._grace_until = False, 0
        self._field, self._seen_there, self._there = None, frozenset(), ()
        if reason in _RESTING_OUTCOMES:
            self._resting[target_id] = world.tick + REST_SECONDS * TICKS_PER_SECOND
        event = {"tick": world.tick, "reason": kind, "phase": "end", "target": target_id, "outcome": reason}
        records.log(COMBAT_EVENT, event)
        if kind == COUNTERATTACK and reason == DESTROYED and self._press_on(units, main, until, world, ctx):
            return []
        reach = _going_on_reach(kind, reason, ctx)
        if reach is not None and self._go_on(units, main, reach, world, ctx):
            return []
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


def strike_force(
    target: EnemyRecord,
    units: list[Unit],
    world: World,
    ctx: MatchContext,
    finishes: Callable[[list[Unit]], bool] | None = None,
    army_share: float = ENEMY_ARMY_SHARE,
) -> list[Unit]:
    """The smallest set of the units given that can hurt a target, the nearest to it first and for a defense the
    long-range units first, whose ratio at it (`near_ratio`) is at least strike_min_ratio, whose strength is at
    least `army_share` of the enemy army's estimate and which `finishes` the target's buildings, when given (more
    units never finish them more slowly); none when all of them fall short."""
    defense = _is_defense(target, world)
    cell = _cell(target)
    hurting = sorted(
        (unit for unit in units if can_hurt(unit.type, target.type, world)),
        key=lambda unit: (defense and unit.type not in SIEGE_TYPES, cells.distance((unit.x, unit.y), cell), unit.id),
    )
    weighed_with = field_weighed_with(cell, world, ctx)
    least, army = ctx.tuning.combat.strike_min_ratio, army_share * enemy_army_value(ctx)

    def is_enough(count: int) -> bool:
        chosen = hurting[:count]
        if finishes is not None and not finishes(chosen):
            return False
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
    home, tuning = world.base_center, ctx.tuning
    reach = tuning.combat.strike_near_whole_cells if tuning.combat.strike_near_by_defenses else None
    return home is not None and cells.is_within(_cell(target), home, reach or tuning.base_defense.far_rescue_cells)


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


def gathered_idle(main: Mapping[int, Unit], ctx: MatchContext) -> list[Unit]:
    """main's units within gathered_cells of their centroid while at least gathered_share of them stand there and as
    many stand idle: main standing still as one group wherever it is; none otherwise."""
    if not main:
        return []
    tuning = ctx.tuning.combat
    middle = centroid(main.values())
    gathered = [unit for unit in main.values() if math.dist((unit.x, unit.y), middle) <= tuning.gathered_cells]
    least = tuning.gathered_share * len(main)
    idle = [unit for unit in gathered if unit.idle]
    return gathered if len(gathered) >= least and len(idle) >= least else []


def _is_pulled_back(ctx: MatchContext) -> bool:
    """Whether the model commander pulls main back or home: its retreat override is on, or its standing action for
    main is a pull back or a retreat home."""
    commander = ctx.commander
    if commander is None:
        return False
    standing = commander.standing
    return commander.retreat.is_active or (standing is not None and standing.kind in PULLING_BACK)


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
    """The enemy expansions and defenses near us that must die, the nearest to our base first, then by id, after the
    remembered missile silos (see `_silos`); none with strike_near_expansion off. See the module's note."""
    if not ctx.tuning.combat.strike_near_expansion:
        return []
    # A building does not move: with strike_near_by_defenses on, it counts however long ago it was seen.
    standing = ctx.tuning.combat.strike_near_by_defenses
    expansions = [
        record
        for record in world.enemy_memory.values()
        if record.type in EXPANSION_TYPES
        and (is_seen_lately(record, world) or (standing and record.is_building))
        and _is_near_us(record, world, ctx)
    ]
    defenses = [
        record
        for record in ground_defenses(world)
        if (standing or is_seen_lately(record, world)) and _reaches_refinery(record, world, ctx)
    ]
    found = {record.id: record for record in [*expansions, *defenses]}
    home = world.base_center
    near = sorted(found.values(), key=lambda record: (_distance(record, home), record.id))
    return [*_silos(world, ctx), *near]


def _silos(world: World, ctx: MatchContext) -> list[EnemyRecord]:
    """With nuke_spread and nuke_follow_up of [combat] on, the remembered enemy missile silos, the nearest to our base
    first: a silo seen once fires on our densest cluster every few minutes until it dies, wherever it stands (see
    `nuke`)."""
    if not _follows_up_nukes(ctx):
        return []
    home = world.base_center
    return sorted(known_silos(world), key=lambda record: (_distance(record, home), record.id))


def _follows_up_nukes(ctx: MatchContext) -> bool:
    """Whether a known silo is struck: nuke_spread and nuke_follow_up of [combat] both on."""
    combat = ctx.tuning.combat
    return combat.nuke_spread and combat.nuke_follow_up


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


def _going_on_reach(kind: str, reason: str, ctx: MatchContext) -> int | None:
    """How far the force of a strike that ended so goes on to an exposed building: exposed_expansion_chain_cells after
    an exposed building destroyed; with overextended_chain on, overextended_chain_cells after an over-extended field
    emptied or left out of sight (its harvester driving home); None after any other end."""
    tuning = ctx.tuning.combat
    if kind == STRIKE_EXPANSION and reason == DESTROYED:
        return tuning.exposed_expansion_chain_cells
    if kind == STRIKE_OVEREXTENDED and reason in (DESTROYED, UNSEEN) and tuning.overextended_chain:
        return tuning.overextended_chain_cells
    return None


def _is_open_building(target: EnemyRecord, world: World, ctx: MatchContext) -> bool:
    """Whether a must-kill target is an enemy yard (one freshly deployed, say) no known enemy ground defense reaches,
    with strike_near_open_building of [combat] on."""
    if not ctx.tuning.combat.strike_near_open_building or not target.is_building or target.type not in EXPANSION_TYPES:
        return False
    return not field_weighed_with(_cell(target), world, ctx)[1]


def _open_building_force(
    target: EnemyRecord, gathered: list[Unit], main: Mapping[int, Unit], world: World, ctx: MatchContext
) -> list[Unit]:
    """The strike force on an open building: from main's units gathered (at the staging point or wherever main
    stands) and its idle units within strike_near_home_cells of our base's center, the fewest that bring it down
    quickly (`exposed_expansion.can_finish`), whatever the enemy army's estimate."""
    home, reach = world.base_center, ctx.tuning.combat.strike_near_home_cells
    at_home = [
        unit
        for unit in main.values()
        if unit.idle and home is not None and cells.is_within((unit.x, unit.y), home, reach)
    ]
    pool = list({unit.id: unit for unit in [*gathered, *at_home]}.values())
    finishes = functools.partial(exposed_expansion.can_finish, target=target, world=world, ctx=ctx)
    return strike_force(target, pool, world, ctx, finishes, army_share=0.0)


def _is_blocked(target: EnemyRecord, force: list[Unit], world: World, ctx: MatchContext) -> bool:
    """Whether a failed attack near a target blocks a force's straight attack on it (see `failed_attack`)."""
    return failed_attack.blocking(_cell(target), force, failed_attack.DIRECT, world, ctx) is not None


def _cell(record: EnemyRecord) -> Cell:
    return record.x, record.y
