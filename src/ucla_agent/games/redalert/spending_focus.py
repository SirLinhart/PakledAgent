"""The spending focus: whether the money goes to the army, to the economy, or is spent as it always is.

A base that grows its economy while the enemy army outgrows its own loses
the army and then the economy; one that keeps buying tanks while far ahead
lets the enemy outgrow it later. With `spending_focus` of [upkeep] on, a
script decides the balance from the enemy army estimate E (see
`enemy_estimate`) against our army A, the cost of our combat units in main,
the guard and the escort (no harvester, MCV or aircraft). It decides every
`focus_every_ticks` once the opening's build is complete; before that the
focus is balanced and the opening's own lean decides.

- Military starts while E is known, E is at least `military_focus_ratio` x A
  and A or E is 1,000 or more: the enemy army is ahead. It ends once E is at
  most `military_focus_end_ratio` x A: our army matches theirs, or our kills
  lowered E.
- Economy starts when not military and either A is at least
  `economy_focus_ratio` x E, E known, or the enemy is greedy: 2 or more of its
  refineries remembered, together worth E or more at the refinery's cost. It
  ends only with a margin: once A is under `ECONOMY_END_RATIO` x E and the
  enemy is not greedy.
- Balanced otherwise, and while E is unknown and the enemy is not greedy.

A focus other than military is kept `focus_hold_seconds` at least before it
changes; a change to military is made at once. Each change is one
`spending_focus` event: the focus, its reason (`enemy_ahead`, `matched`,
`army_ahead`, `greedy_enemy`, `balanced`), E and A. The focus is published as
`MatchContext.spending_focus`.

Under the military focus no new MCV and no new refinery is ordered (see
`expansion` and `upkeep`): an MCV or a refinery already ordered goes on, and
the harvesters for the refineries standing are still trained. While main and
the guard hold fewer than `military_focus_first_artillery` of the family's
artillery (`arty`, `v2rl`), counting those on their way, and it is on offer,
the Vehicle queue's next army vehicle is that artillery (`artillery_first`).
And one ground defense is ordered every `military_focus_defense_seconds`, at
most `military_focus_defenses_max` in a military period, while the Defense
queue is free and the money above the production reserve covers it
(`FocusDefenses`): the family's defense against armor (with the power plant
first when it would leave the base short of the power target) while
vehicles are at least `vehicle_heavy_share` of [mix] of the enemy army seen,
else its defense against infantry. It stands on the side of the main base
facing the enemy's start (see `counter_units.toward_enemy`), and is a trigger
of its own: `defenses_on_threat_only` does not hold it back.

With `military_recovery` of [upkeep] on, the military focus also starts on
our losses, at once and whatever the balance says (`MilitaryRecovery`), A
then the cost of all our combat units but aircraft, whatever their group, and
E the estimate held to `recovery_estimate_seen_share` times the enemy combat
units seen within `recovery_seen_seconds` (`recovery_estimate`: the
estimate grows on unseen income, in the live games of 20261016 to 10,861):
while A is below max(`recovery_enemy_share` x E, `recovery_min_army`) (the
floor alone while E is unknown); or, while A is below E, A has dropped by
`recovery_drop_share` or more from its highest of the last
`recovery_drop_seconds`, or the base lost combat units and defenses worth
`recovery_loss_value` or more under a threat in force within the last
`recovery_attack_seconds`. It holds until none of those has held for
`recovery_clear_seconds`, and ends at once while A is worth E or more, so
that it never keeps an MCV or an expansion back from an army that matches the
enemy's; the balance decides again from then on. Each start and end is one
`spending_focus` event of reason `military_recovery`, phase `start` or `end`,
with its cause, A and the bar.

With `focus_capped_estimate` of [upkeep] on, E is the estimate as the
recovery holds it (`recovery_estimate`: at most recovery_estimate_seen_share
times the enemy combat units seen within recovery_seen_seconds) for the
balance as well, and the military focus holds a new MCV back
(`MatchContext.mcv_held_by_focus`) only while A is below that E: in the live
game watch41-m5-rush-1010 the focus went military at estimates of 2,000 and
3,657 grown on unseen income, and the enemy out-expanded us.

Under the economy focus the yards the income allows are one more (see
`expansion`), `max_yards` at the most, and a due harvester goes into the
Vehicle queue before the army's next vehicle (see `vehicle_turn`).

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from collections import deque
from typing import Callable

from ucla_agent.core import records
from ucla_agent.games.redalert.batch import Intent
from ucla_agent.games.redalert.composition import AIRCRAFT_QUEUE, Composition, seen_army_value
from ucla_agent.games.redalert import defense_cap
from ucla_agent.games.redalert.counter_units import COUNTED_GROUPS, build_toward_enemy, toward_enemy
from ucla_agent.games.redalert.family import FAMILIES, REFINERY
from ucla_agent.games.redalert.groups import GUARD, MAIN
from ucla_agent.games.redalert.items import ALLIES, SOVIET, TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import BALANCED_FOCUS, ECONOMY_FOCUS, MILITARY_FOCUS, MatchContext
from ucla_agent.games.redalert.opening import is_build_complete
from ucla_agent.games.redalert.production import DEFENSE_QUEUE, VEHICLE_QUEUE
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.tuning import UpkeepTuning
from ucla_agent.games.redalert.world import DEFENSE_TYPES, World

SPENDING_FOCUS_EVENT = "spending_focus"
# The reasons a `spending_focus` event gives for the focus it changes to.
ENEMY_AHEAD = "enemy_ahead"
MATCHED = "matched"
ARMY_AHEAD = "army_ahead"
GREEDY_ENEMY = "greedy_enemy"
BALANCED_REASON = "balanced"
MILITARY_RECOVERY = "military_recovery"
# The rule the focus defenses are, as a `defense_capped` event names it (see `defense_cap`).
FOCUS_DEFENSE_RULE = "focus_defense"
# What starts the military recovery, as its start event gives it, and its phases.
BELOW_BAR = "below_bar"
ARMY_DROPPED = "army_dropped"
LOST_AT_BASE = "lost_at_base"
START_PHASE = "start"
END_PHASE = "end"
# Below this both armies are too small for the one to be ahead of the other in a way that calls for the military focus.
MILITARY_FOCUS_MIN_VALUE = 1000
# The economy focus ends only below this ratio of our army to the enemy's, a margin under the one it starts at, so that
# it does not swing on and off with every unit lost.
ECONOMY_END_RATIO = 1.3
# The fewest remembered enemy refineries of a greedy enemy.
GREEDY_MIN_REFINERIES = 2
# The family's artillery, which the military focus trains first.
ARTILLERY = {ALLIES: "arty", SOVIET: "v2rl"}
# The decimal places the event gives E with.
_DECIMALS = 1

# How upkeep orders a defense held to the power target, or the power plant it waits for.
WithinPower = Callable[[str, World, MatchContext], list[Intent]]


class MilitaryRecovery:
    """Whether our losses call for the military focus whatever the balance says. See the module's note."""

    def __init__(self) -> None:
        # A in each cycle of the last recovery_drop_seconds, the oldest first.
        self._army: deque[tuple[int, int]] = deque()
        # The types of our combat units and defenses in the last cycle, by id, and what was lost of them under a
        # threat in each cycle of the last recovery_attack_seconds: (tick, cost).
        self._own: dict[int, str] = {}
        self._losses: deque[tuple[int, int]] = deque()
        # The tick the recovery started at, and the last tick a cause of it held; None while none is in force.
        self._since: int | None = None
        self._held_at = 0

    def follow(self, world: World, ctx: MatchContext, army: int) -> bool:
        """Bring the recovery up to date with A (`army`); whether it is in force. Never with military_recovery off,
        nor while A is worth the enemy's army (E as `recovery_estimate` holds it) or more: it then ends at once."""
        tuning = ctx.tuning.upkeep
        self._note(world, ctx, army)
        if not tuning.military_recovery:
            return False
        enemy = recovery_estimate(world, ctx)
        bar = recovery_bar(ctx, enemy)
        is_matched = enemy is not None and army >= enemy
        cause = None if is_matched else self._cause(army, bar, enemy is not None, tuning)
        clear_ticks = tuning.recovery_clear_seconds * TICKS_PER_SECOND
        if cause is not None:
            self._held_at = world.tick
            if self._since is None:
                self._since = world.tick
                _log_recovery(world, START_PHASE, army, bar, cause=cause)
        elif self._since is not None and (is_matched or world.tick - self._held_at >= clear_ticks):
            _log_recovery(world, END_PHASE, army, bar, since=self._since)
            self._since = None
        return self._since is not None

    def _note(self, world: World, ctx: MatchContext, army: int) -> None:
        """Take in A, and what of our combat units and defenses was lost under a threat in force since the last
        cycle."""
        defenses = {b.id: b.type for b in world.buildings.values() if b.type in DEFENSE_TYPES}
        own = {**{unit_id: unit.type for unit_id, unit in world.combat_units.items()}, **defenses}
        lost = [kind for unit_id, kind in self._own.items() if unit_id not in own]
        if ctx.threat_active and lost:
            self._losses.append((world.tick, sum(_cost(kind, world) for kind in lost)))
        self._own = own
        self._army.append((world.tick, army))
        tuning = ctx.tuning.upkeep
        _forget_before(self._army, world.tick - tuning.recovery_drop_seconds * TICKS_PER_SECOND)
        _forget_before(self._losses, world.tick - tuning.recovery_attack_seconds * TICKS_PER_SECOND)

    def _cause(self, army: int, bar: float, is_known: bool, tuning: UpkeepTuning) -> str | None:
        """What calls for the recovery in this cycle, with A below E (`is_known` says E is) for all but the bar; None
        for nothing."""
        if army < bar:
            return BELOW_BAR
        if not is_known:
            return None
        peak = max(value for _, value in self._army)
        if army <= (1 - tuning.recovery_drop_share) * peak:
            return ARMY_DROPPED
        lost = sum(value for _, value in self._losses)
        return LOST_AT_BASE if lost >= tuning.recovery_loss_value else None


def _forget_before(samples: deque[tuple[int, int]], horizon: int) -> None:
    """Drop the samples older than a tick from the front of a deque."""
    while samples and samples[0][0] < horizon:
        samples.popleft()


def recovery_estimate(world: World, ctx: MatchContext) -> float | None:
    """E as the recovery weighs it: the enemy army estimate, at most recovery_estimate_seen_share times what the enemy
    combat units seen within recovery_seen_seconds cost, so that the estimate's growth on unseen income alone does
    not keep the recovery in force. None while the estimate is unknown."""
    estimate = ctx.enemy_army_estimate
    if estimate is None:
        return None
    tuning = ctx.tuning.upkeep
    since = world.tick - tuning.recovery_seen_seconds * TICKS_PER_SECOND
    seen = sum(
        _cost(record.type, world)
        for record in world.enemy_memory.values()
        if is_combat_unit(record) and record.last_seen_tick >= since
    )
    return min(estimate, tuning.recovery_estimate_seen_share * seen)


def focus_estimate(world: World, ctx: MatchContext) -> float | None:
    """E as the balance weighs it: as the recovery does with focus_capped_estimate on, else the estimate."""
    if ctx.tuning.upkeep.focus_capped_estimate:
        return recovery_estimate(world, ctx)
    return ctx.enemy_army_estimate


def recovery_bar(ctx: MatchContext, enemy: float | None) -> float:
    """What A must be worth to need no recovery: recovery_enemy_share of E as the recovery weighs it (`enemy`),
    recovery_min_army at least."""
    tuning = ctx.tuning.upkeep
    shared = 0.0 if enemy is None else tuning.recovery_enemy_share * enemy
    return max(shared, float(tuning.recovery_min_army))


def _cost(item: str, world: World) -> int:
    facts = world.items.get(item)
    return facts.cost if facts is not None else 0


def _log_recovery(world: World, phase: str, army: int, bar: float, **more: object) -> None:
    """Write one `spending_focus` event of reason `military_recovery`."""
    event = {"tick": world.tick, "reason": MILITARY_RECOVERY, "phase": phase, "army": army, "bar": round(bar)}
    records.log(SPENDING_FOCUS_EVENT, {**event, **more})


class SpendingFocus:
    """Decides the spending focus of one match and publishes it on the match context. See the module's note."""

    def __init__(self) -> None:
        # The tick the focus was last decided at, and the tick it last changed at; None before either.
        self._decided_at: int | None = None
        self._changed_at: int | None = None
        self._recovery = MilitaryRecovery()
        # Whether the military focus in force is the one the recovery put in force.
        self._is_recovering = False

    def decide(self, world: World, ctx: MatchContext) -> None:
        """Decide the focus when it is due: every `focus_every_ticks` once the opening's build is complete, balanced
        before; the military focus at once while the military recovery is in force; nothing with `spending_focus` off,
        when it stays balanced."""
        tuning = ctx.tuning.upkeep
        self._publish_mcv_hold(world, ctx)
        if not tuning.spending_focus or not is_build_complete(ctx.production, ctx.tuning.opening.order):
            return
        if self._recovery.follow(world, ctx, ground_army_value(world)):
            if ctx.spending_focus != MILITARY_FOCUS:
                ctx.spending_focus, self._changed_at = MILITARY_FOCUS, world.tick
                self._is_recovering = True
            return
        if self._is_recovering:
            # The military focus the recovery put in force ends with it: the balance decides afresh from its start
            # rule, not from its end rule, which an estimate grown on unseen income would hold military.
            self._is_recovering = False
            ctx.spending_focus, self._changed_at, self._decided_at = BALANCED_FOCUS, world.tick, None
        if self._decided_at is not None and world.tick - self._decided_at < tuning.focus_every_ticks:
            return
        self._decided_at = world.tick
        estimate, army = focus_estimate(world, ctx), own_army_value(world, ctx)
        focus, reason = called_for(ctx.spending_focus, estimate, army, is_greedy(world, estimate), ctx)
        if focus == ctx.spending_focus or self._is_held(focus, world.tick, ctx):
            return
        ctx.spending_focus, self._changed_at = focus, world.tick
        records.log(
            SPENDING_FOCUS_EVENT,
            {
                "tick": world.tick,
                "focus": focus,
                "reason": reason,
                "estimate": None if estimate is None else round(estimate, _DECIMALS),
                "army": army,
            },
        )

    @staticmethod
    def _publish_mcv_hold(world: World, ctx: MatchContext) -> None:
        """Publish whether the military focus holds a new MCV back: while it is in force, and with
        focus_capped_estimate on only while A is below E as the focus weighs it."""
        is_military_now = ctx.spending_focus == MILITARY_FOCUS
        if not ctx.tuning.upkeep.focus_capped_estimate:
            ctx.mcv_held_by_focus = is_military_now
            return
        estimate = focus_estimate(world, ctx)
        ctx.mcv_held_by_focus = is_military_now and estimate is not None and own_army_value(world, ctx) < estimate

    def _is_held(self, focus: str, tick: int, ctx: MatchContext) -> bool:
        """Whether the focus in force is kept for now: it is not military, the change is not to military, and it has
        not stood `focus_hold_seconds` yet."""
        if MILITARY_FOCUS in (focus, ctx.spending_focus) or self._changed_at is None:
            return False
        return tick - self._changed_at < ctx.tuning.upkeep.focus_hold_seconds * TICKS_PER_SECOND


def called_for(focus: str, estimate: float | None, army: int, greedy: bool, ctx: MatchContext) -> tuple[str, str]:
    """The focus the balance calls for and its reason, from the focus in force, E (`estimate`), A (`army`) and whether
    the enemy is greedy."""
    tuning = ctx.tuning.upkeep
    if focus == MILITARY_FOCUS:
        if estimate is not None and estimate > tuning.military_focus_end_ratio * army:
            return MILITARY_FOCUS, ENEMY_AHEAD
    elif estimate is not None and _is_enemy_ahead(estimate, army, tuning.military_focus_ratio):
        return MILITARY_FOCUS, ENEMY_AHEAD
    ratio = ECONOMY_END_RATIO if focus == ECONOMY_FOCUS else tuning.economy_focus_ratio
    if estimate is not None and army >= ratio * estimate:
        return ECONOMY_FOCUS, ARMY_AHEAD
    if greedy:
        return ECONOMY_FOCUS, GREEDY_ENEMY
    return BALANCED_FOCUS, MATCHED if focus == MILITARY_FOCUS else BALANCED_REASON


def _is_enemy_ahead(estimate: float, army: int, ratio: float) -> bool:
    return estimate >= ratio * army and max(estimate, army) >= MILITARY_FOCUS_MIN_VALUE


def is_greedy(world: World, estimate: float | None) -> bool:
    """Whether the enemy is greedy: `GREEDY_MIN_REFINERIES` or more of its refineries are remembered, and at the
    refinery's cost they are worth E or more (any E while it is unknown)."""
    refineries = sum(1 for record in world.enemy_memory.values() if record.is_building and record.type == REFINERY)
    facts = world.items.get(REFINERY)
    worth = refineries * (facts.cost if facts is not None else 0)
    return refineries >= GREEDY_MIN_REFINERIES and (estimate is None or worth >= estimate)


def own_army_value(world: World, ctx: MatchContext) -> int:
    """A: what the own combat units of main, the guard and the escort cost, aircraft left out."""
    value = 0
    for unit in world.combat_units.values():
        facts = world.items.get(unit.type)
        if facts is not None and facts.queue != AIRCRAFT_QUEUE and ctx.groups.group_of(unit.id) in COUNTED_GROUPS:
            value += facts.cost
    return value


def ground_army_value(world: World) -> int:
    """What all our combat units cost, aircraft left out: the military recovery's A, which a unit moved from main to a
    raid or a scout slot does not lower as a loss would."""
    value = 0
    for unit in world.combat_units.values():
        facts = world.items.get(unit.type)
        if facts is not None and facts.queue != AIRCRAFT_QUEUE:
            value += facts.cost
    return value


def is_military(ctx: MatchContext) -> bool:
    """Whether the military focus is in force."""
    return ctx.spending_focus == MILITARY_FOCUS


def artillery_first(produces: Composition, world: World, ctx: MatchContext) -> Composition:
    """The plan's composition with, under the military focus, the family's artillery as the Vehicle queue's one item
    while main and the guard hold fewer than `military_focus_first_artillery` of it, those on their way counted, and it
    is on offer; `produces` as it is else, and while the plan feeds the Vehicle queue nothing."""
    artillery = ARTILLERY[world.family]
    if not is_military(ctx) or not produces.vehicle or artillery not in world.available_production:
        return produces
    held = sum(
        1
        for unit in world.combat_units.values()
        if unit.type == artillery and ctx.groups.group_of(unit.id) in (MAIN, GUARD)
    )
    if held + ctx.production.pending(artillery) >= ctx.tuning.upkeep.military_focus_first_artillery:
        return produces
    vehicle = {artillery: produces.vehicle_share}
    return Composition(infantry=produces.infantry, vehicle=vehicle, raid_infantry=produces.raid_infantry)


class FocusDefenses:
    """Orders the military focus's defenses toward the enemy's start. See the module's note."""

    def __init__(self, within_power: WithinPower) -> None:
        """A rule that holds a defense against armor to the power target the way `within_power` does."""
        self._within_power = within_power
        # The tick the last such defense was ordered at, and how many the military period in force has had.
        self._ordered_at: int | None = None
        self._in_period = 0

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for one defense when one is due under the military focus, or for the power plant a defense
        against armor waits for."""
        if not is_military(ctx):
            self._in_period = 0
            return []
        if not self._is_due(world, ctx):
            return []
        item, is_against_armor = _defense(world, ctx)
        production = ctx.production
        if item not in world.available_production or not production.is_queue_free(DEFENSE_QUEUE):
            return []
        if not defense_cap.allows(item, FOCUS_DEFENSE_RULE, world, ctx, toward_enemy(world)):
            return []
        if world.economy.spendable - ctx.tuning.upkeep.production_cash_reserve < production.cost(item):
            return []
        if is_against_armor:
            orders = self._within_power(item, world, ctx)
        else:
            orders = [production.build(item)] if production.can_power(item) else []
        orders = build_toward_enemy(item, orders, world, ctx)
        if any(intent.order.get("item") == item for intent in orders):
            self._ordered_at, self._in_period = world.tick, self._in_period + 1
        return orders

    def _is_due(self, world: World, ctx: MatchContext) -> bool:
        """Whether a defense is due: fewer than `military_focus_defenses_max` in this military period, and none within
        `military_focus_defense_seconds`."""
        tuning = ctx.tuning.upkeep
        if self._in_period >= tuning.military_focus_defenses_max:
            return False
        wait_ticks = tuning.military_focus_defense_seconds * TICKS_PER_SECOND
        return self._ordered_at is None or world.tick - self._ordered_at >= wait_ticks


def _defense(world: World, ctx: MatchContext) -> tuple[str, bool]:
    """The family's defense against armor while vehicles are at least `vehicle_heavy_share` of the enemy army seen,
    else its defense against infantry; and whether it is the one against armor."""
    names = FAMILIES[world.family]
    value = seen_army_value(world)
    worth = sum(value.values())
    is_against_armor = worth > 0 and value[VEHICLE_QUEUE] >= ctx.tuning.mix.vehicle_heavy_share * worth
    return (names.anti_armor_defense if is_against_armor else names.anti_infantry_defense), is_against_armor
