"""main committed to the base trade once the armies cross (`[combat] commit_on_cross`).

An army deep in the enemy's ground that turns home when the enemy's army
heads for our base walks into that army on the way, and loses both the fight
and the base trade. So while main's attack is out (see `attack_approach`)
the combat manager watches for the armies crossing, after base defense and
before the commander's order, in every cycle.

**The crossing.** With B our base's center, E the enemy's start and M main's
centroid, the armies have crossed when M is nearer E than B and the enemy
heads for ours: the canary was lost since the attack set out
(`canary_lost`), an enemy army is closing on B (`closing`, as
`ClosingWatch.closing_on` with closing_cells and closing_min_value of
`[base_defense]`), enemy ground combat units seen within cross_seen_seconds
farther than contact_cells from M are worth cross_army_value or more and
their centroid is nearer B than E (`army_seen`), or base defense has a major
threat in force (`major_threat`). One `combat` event `armies_crossed`;
`MatchContext.armies_crossed` and its tick are published, which starts the
canary alarm (see `canary_alarm`). The crossing moves main nowhere: only the
decision does.

**The decision**, once per attack, in the cycle the crossing is seen. P is
the known enemy construction yard nearest M, else E. The reverse route R is
the way main came, reversed: by its approach's side cell once it has passed
it, then to the staging point; else straight to the staging point. R is
clear while no enemy ground combat unit seen within cross_seen_seconds stands
within cross_route_clear_cells of a cell of it and no ground defense covers
one. main is weighed at P as `fight.weigh` weighs it against the enemy
combat units within contact_cells of P and the ground defenses covering P;
costly under defended_engage_ratio, and costly by its defenses when their
weighted worth is more than the units'. main has artillery enough with
cross_siege_min_long_range long-range units or more, as many as
cross_siege_per_defense of the covering defenses, and screen enough for them
(`siege.has_screen`). The first that applies: R not clear, commit
(`forces_between`); costly by its defenses with artillery enough, commit and
siege (`artillery_siege`); not costly, commit (`base_beatable`); else home
(`clear_exit`). One `combat` event `cross_decision`.

**Commit.** main's objective is P (see `combat`); a siege shells the
covering defenses until they are destroyed or unseen SIEGE_UNSEEN_SECONDS.
Nothing recalls main meanwhile: base defense neither takes nor calls it
home, the commander's retreat override does not take it and the model's
answers are not carried out (`MatchContext.main_committed`). The commit ends
(`cross_end`) once no known enemy building stands within
COMMIT_BASE_CELLS of P (`base_destroyed`), main holds fewer than
COMMIT_MIN_UNITS units (`too_few`), or the canary alarm seen since the
commit has ended beaten, gone or by its timeout with no enemy army closing on
B (`alarm_over`).

**Home.** main goes home by R in steps, kept until the attack is over.
"""

from __future__ import annotations

import math
from typing import Sequence

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, coverage, siege
from ucla_agent.games.redalert.attack_approach import AttackWatch, covering, way_cells
from ucla_agent.games.redalert.command_threat import ClosingWatch
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD
from ucla_agent.games.redalert.fight import centroid, ground_defenses, is_counted, weigh, worth
from ucla_agent.games.redalert.groups import SIEGE_TYPES
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MAJOR_THREAT, MatchContext
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

COMBAT_EVENT = "combat"
ARMIES_CROSSED = "armies_crossed"
CROSS_DECISION = "cross_decision"
CROSS_END = "cross_end"
# The signals that the enemy heads for our base.
CANARY_LOST = "canary_lost"
CLOSING = "closing"
ARMY_SEEN = "army_seen"
MAJOR = "major_threat"
# The choices and their reasons.
COMMIT = "commit"
COMMIT_SIEGE = "commit_siege"
HOME = "home"
FORCES_BETWEEN = "forces_between"
ARTILLERY_SIEGE = "artillery_siege"
BASE_BEATABLE = "base_beatable"
CLEAR_EXIT = "clear_exit"
# Why a commit ends.
BASE_DESTROYED = "base_destroyed"
TOO_FEW = "too_few"
ALARM_OVER = "alarm_over"
# The alarm's ends that end a commit, with no enemy army closing on the base.
ALARM_ENDS = frozenset({"beaten", "gone", "timeout"})
# A commit ends once no known enemy building stands this near its target, or main holds fewer units than this.
COMMIT_BASE_CELLS = 15
COMMIT_MIN_UNITS = 2
# A siege of the covering defenses becomes a plain commit once they have gone unseen this long.
SIEGE_UNSEEN_SECONDS = 30
# main's centroid this near a cell of the way home goes on to the next.
HOME_LEG_CELLS = 3
_FULL_HEALTH = 100
_RATIO_DECIMALS = 2


class CrossCommit:
    """The crossing of the armies on main's attack out, and what main does about it. See the module's note."""

    def __init__(self) -> None:
        # The choice of the attack out (COMMIT, COMMIT_SIEGE or HOME); None while none holds.
        self.decision: str | None = None
        # The commit's target P, the defenses its siege shells, and the way home of a home decision.
        self.target: Cell | None = None
        self.siege_defenses: frozenset[int] = frozenset()
        self.route: list[Cell] = []
        # The attack whose crossing was decided (its set-out tick), which decides no more; None for none.
        self._decided: int | None = None
        self._crossed_tick: int | None = None
        # Whether the canary alarm has been seen in force since the commit began.
        self._saw_alarm = False
        self._closing = ClosingWatch()

    @property
    def is_committed(self) -> bool:
        """Whether main is committed to the base trade."""
        return self.decision in (COMMIT, COMMIT_SIEGE)

    @property
    def holds(self) -> bool:
        """Whether a decision holds main: committed, or on its way home."""
        return self.decision is not None

    def follow(
        self, units: Sequence[Unit], attack: AttackWatch, staging: Cell, world: World, ctx: MatchContext
    ) -> None:
        """Bring the crossing up to date in a cycle: seen and decided once an attack out, the commit ended when it is
        over, everything forgotten once the attack is; published on the match context."""
        self._closing.note(world)
        if not ctx.tuning.combat.commit_on_cross or not attack.is_out or world.enemy_base is None:
            self._forget()
        elif self.is_committed:
            self._follow_commit(units, world, ctx)
        elif self._decided != attack.since and units:
            signals = self.signals(units, attack.since, world, ctx)
            if signals:
                self._decide(units, signals, attack, staging, world, ctx)
        ctx.armies_crossed = self._crossed_tick is not None
        ctx.armies_crossed_tick = self._crossed_tick
        ctx.main_committed = self.is_committed

    def signals(self, units: Sequence[Unit], since: int, world: World, ctx: MatchContext) -> list[str]:
        """The signals that the armies have crossed in this cycle: none while main's centroid is not nearer the
        enemy's start than our base."""
        base, enemy = world.base_center, world.enemy_base
        middle = centroid(units)
        if math.dist(middle, enemy) >= math.dist(middle, base):
            return []
        found = []
        if ctx.canary_lost_tick is not None and ctx.canary_lost_tick >= since:
            found.append(CANARY_LOST)
        if self._is_closing(world, ctx):
            found.append(CLOSING)
        if self._army_heads_home(middle, world, ctx):
            found.append(ARMY_SEEN)
        if ctx.threat_level == MAJOR_THREAT:
            found.append(MAJOR)
        return found

    def next_leg(self, units: Sequence[Unit]) -> Cell:
        """The cell of the way home main goes to now: on to the next once its centroid is within HOME_LEG_CELLS."""
        middle = cells.nearest_cell(*centroid(units))
        while len(self.route) > 1 and cells.is_within(middle, self.route[0], HOME_LEG_CELLS):
            self.route.pop(0)
        return self.route[0]

    def aim(self, units: Sequence[Unit], world: World, ctx: MatchContext) -> Cell:
        """Where the commit sends main now: a covering defense under siege, else the enemy units rallied at P, else the
        known enemy building nearest main, else P."""
        middle = centroid(units)
        defenses = [record for record in ground_defenses(world) if record.id in self.siege_defenses]
        if self.decision == COMMIT_SIEGE and defenses:
            nearest = min(defenses, key=lambda record: math.dist(middle, (record.x, record.y)))
            return nearest.x, nearest.y
        rallied = _rallied(self.target, world, ctx)
        if rallied:
            return cells.nearest_cell(*centroid(rallied))
        buildings = [record for record in world.enemy_memory.values() if record.is_building]
        if buildings:
            nearest = min(buildings, key=lambda record: math.dist(middle, (record.x, record.y)))
            return nearest.x, nearest.y
        return self.target

    def _decide(
        self,
        units: Sequence[Unit],
        signals: list[str],
        attack: AttackWatch,
        staging: Cell,
        world: World,
        ctx: MatchContext,
    ) -> None:
        """Take the decision on a crossing just seen, with its `armies_crossed` and `cross_decision` events."""
        self._decided, self._crossed_tick, self._saw_alarm = attack.since, world.tick, False
        middle = cells.nearest_cell(*centroid(units))
        _log(ARMIES_CROSSED, world.tick, signals=signals, main=list(middle), enemy=_enemy_middle(world, ctx))
        self.target = _target(middle, world)
        approach = attack.approach
        side = None if approach is None or not attack.has_passed else approach.side_cell
        self.route = [staging] if side is None else [side, staging]
        is_clear = _is_clear(middle, self.route, world, ctx)
        defenses = _covering_target(self.target, world, ctx)
        ratio = _ratio(list(units), _rallied(self.target, world, ctx), defenses, world, ctx)
        self.decision, reason = _choice(units, is_clear, ratio, defenses, self.target, world, ctx)
        if self.decision == COMMIT_SIEGE:
            self.siege_defenses = frozenset(record.id for record in defenses)
        _log(
            CROSS_DECISION,
            world.tick,
            choice=self.decision,
            why=reason,
            ratio=round(ratio, _RATIO_DECIMALS),
            route_clear=is_clear,
            target=list(self.target),
            route=[list(cell) for cell in self.route],
        )

    def _follow_commit(self, units: Sequence[Unit], world: World, ctx: MatchContext) -> None:
        """The siege turned to a plain commit once its defenses are gone or long unseen, and the commit ended when it
        is over, with a `cross_end` event."""
        if self.decision == COMMIT_SIEGE and not self._siege_stands(world):
            self.decision = COMMIT
        self._saw_alarm = self._saw_alarm or ctx.canary_alarm
        reason = self._ending(units, world, ctx)
        if reason is not None:
            _log(CROSS_END, world.tick, why=reason)
            self.decision, self.siege_defenses = None, frozenset()

    def _ending(self, units: Sequence[Unit], world: World, ctx: MatchContext) -> str | None:
        """Why the commit ends in this cycle; None while it holds."""
        target = self.target
        standing = [
            record
            for record in world.enemy_memory.values()
            if record.is_building and cells.is_within((record.x, record.y), target, COMMIT_BASE_CELLS)
        ]
        if not standing:
            return BASE_DESTROYED
        if len(units) < COMMIT_MIN_UNITS:
            return TOO_FEW
        is_over = self._saw_alarm and not ctx.canary_alarm and ctx.canary_alarm_ended in ALARM_ENDS
        return ALARM_OVER if is_over and not self._is_closing(world, ctx) else None

    def _siege_stands(self, world: World) -> bool:
        """Whether a covering defense under siege is still known and seen within SIEGE_UNSEEN_SECONDS."""
        unseen_ticks = SIEGE_UNSEEN_SECONDS * TICKS_PER_SECOND
        return any(
            record.id in self.siege_defenses and world.tick - record.last_seen_tick < unseen_ticks
            for record in world.known_enemy_defenses
        )

    def _is_closing(self, world: World, ctx: MatchContext) -> bool:
        """Whether an enemy army is closing on our base."""
        tuning = ctx.tuning.base_defense
        return (
            self._closing.closing_on(world.base_center, tuning.closing_cells, tuning.closing_min_value, world)
            is not None
        )

    def _army_heads_home(self, middle: tuple[float, float], world: World, ctx: MatchContext) -> bool:
        """Whether the enemy ground combat units seen within cross_seen_seconds farther than contact_cells from main are
        worth cross_army_value or more and stand nearer our base than theirs."""
        combat = ctx.tuning.combat
        seen = [
            record
            for record in _seen_lately(world, ctx)
            if math.dist(middle, (record.x, record.y)) > combat.contact_cells
        ]
        if not seen or sum(worth(record.type, record.hp, world) for record in seen) < combat.cross_army_value:
            return False
        theirs = centroid(seen)
        return math.dist(theirs, world.base_center) < math.dist(theirs, world.enemy_base)

    def _forget(self) -> None:
        """No attack out (or the switch off): no decision holds and no crossing stands."""
        self.decision, self.target, self.siege_defenses, self.route = None, None, frozenset(), []
        self._decided, self._crossed_tick = None, None


def _choice(
    units: Sequence[Unit],
    is_clear: bool,
    ratio: float,
    defenses: list[EnemyRecord],
    target: Cell,
    world: World,
    ctx: MatchContext,
) -> tuple[str, str]:
    """The decision on a crossing and its reason, the first that applies (see the module's note)."""
    costly = ratio < ctx.tuning.combat.defended_engage_ratio
    weighted = ctx.tuning.combat.defense_weight * sum(worth(record.type, _FULL_HEALTH, world) for record in defenses)
    rallied = sum(worth(record.type, record.hp, world) for record in _rallied(target, world, ctx))
    if not is_clear:
        return COMMIT, FORCES_BETWEEN
    if costly and weighted > rallied and _has_artillery(units, len(defenses), world, ctx):
        return COMMIT_SIEGE, ARTILLERY_SIEGE
    return (HOME, CLEAR_EXIT) if costly else (COMMIT, BASE_BEATABLE)


def _seen_lately(world: World, ctx: MatchContext) -> list[EnemyRecord]:
    """The enemy ground combat units seen within cross_seen_seconds."""
    seen_ticks = ctx.tuning.combat.cross_seen_seconds * TICKS_PER_SECOND
    return [
        record
        for record in world.enemy_memory.values()
        if is_combat_unit(record)
        and not world.items.damage.get(record.type).air
        and world.tick - record.last_seen_tick <= seen_ticks
    ]


def _enemy_middle(world: World, ctx: MatchContext) -> list[int] | None:
    """The cell of the centroid of the enemy ground combat units seen lately; None for none."""
    seen = _seen_lately(world, ctx)
    return list(cells.nearest_cell(*centroid(seen))) if seen else None


def _target(middle: Cell, world: World) -> Cell:
    """P: the known enemy construction yard nearest main, else the enemy's start."""
    yards = [record for record in world.enemy_memory.values() if record.type == CONSTRUCTION_YARD]
    if not yards:
        return world.enemy_base
    nearest = min(yards, key=lambda record: cells.distance(middle, (record.x, record.y)))
    return nearest.x, nearest.y


def _is_clear(middle: Cell, route: Sequence[Cell], world: World, ctx: MatchContext) -> bool:
    """Whether the way home is clear: no enemy ground combat unit seen lately within cross_route_clear_cells of a cell
    of it and no ground defense covering one."""
    way = way_cells(middle, route)
    reach = ctx.tuning.combat.cross_route_clear_cells
    for record in _seen_lately(world, ctx):
        if any(cells.is_within((record.x, record.y), cell, reach) for cell in way):
            return False
    return not covering(way, world, ctx)


def _covering_target(target: Cell, world: World, ctx: MatchContext) -> list[EnemyRecord]:
    """The ground defenses whose coverage holds P."""
    return [
        record
        for record in ground_defenses(world)
        if cells.is_within(target, (record.x, record.y), coverage.reach(record.type, world.items, ctx.tuning.coverage))
    ]


def _rallied(target: Cell, world: World, ctx: MatchContext) -> list[EnemyRecord]:
    """The enemy combat units that count in a fight within contact_cells of P."""
    reach = ctx.tuning.combat.contact_cells
    return [
        record
        for record in world.enemy_memory.values()
        if is_counted(record) and cells.is_within((record.x, record.y), target, reach)
    ]


def _ratio(
    units: list[Unit], rallied: list[EnemyRecord], defenses: list[EnemyRecord], world: World, ctx: MatchContext
) -> float:
    """main's ratio at P against the units rallied there and the defenses covering it."""
    own, enemy = weigh(units, rallied, world, ctx.tuning, defenses)
    return own / enemy if enemy > 0 else ctx.tuning.combat.ratio_without_enemy


def _has_artillery(units: Sequence[Unit], defenses: int, world: World, ctx: MatchContext) -> bool:
    """Whether main has artillery enough to siege the covering defenses, and screen enough for it."""
    combat = ctx.tuning.combat
    long_range = [unit for unit in units if unit.type in SIEGE_TYPES]
    screen = [unit for unit in units if unit.type not in SIEGE_TYPES]
    wanted = max(combat.cross_siege_min_long_range, math.ceil(combat.cross_siege_per_defense * defenses))
    return len(long_range) >= wanted and siege.has_screen(long_range, screen, world, ctx.tuning.siege)


def _log(reason: str, tick: int, **data: object) -> None:
    """Write one `combat` event of a reason."""
    records.log(COMBAT_EVENT, {"tick": tick, "reason": reason, **data})
