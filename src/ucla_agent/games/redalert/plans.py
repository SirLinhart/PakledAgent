"""The plan selector: which plan the agent plays by, decided afresh in every cycle by the script's rules.

The agent is always in exactly one plan, and the plan sets what is produced,
how strong `main` has to be before it goes forward, and whether the raid
group operates. `PlanRules` sets it before the managers run, by rules taken
in a fixed order; the first that applies sets the plan:

- R1: OPENING, until a war factory has stood. The opening is then over for
  good, whatever is lost later.
- R2: PRESERVE in the last three minutes when the trade balance is positive:
  the lead is kept. With `finish_over_preserve` on, R6 comes first (see below).
- R3: HOLD in the last three minutes when the balance is zero; R6 comes first
  in the same way.
- R4: PRESS in the last two minutes when the balance is negative; with the
  late-PRESS guard on, not while the base is under a major threat (see
  below).
- R5: STRIKE, an early attack on a base that has been seen and has neither a
  defense nor much of an army, by a `main` strong enough for it. It is given
  up the moment a defense is remembered or main's fight turns against it, and
  then never chosen again in the match. It starts only in the first four
  minutes; one under way goes on past them while it still holds. With the
  early-attack guard on (see below), R5 never applies.
- R6: FINISH, the attack that ends the match: (a) from the eighth minute, with
  the base seen, no negative balance and an army well above what the enemy
  has, its defenses counted twice, when tanks and long-range units make up at
  least three tenths of main's value; or (b) from the sixth minute, when enemy
  buildings are known, no enemy defense is and no enemy unit that fights has
  been in sight for a minute, with a `main` worth 3,000. An enemy army that
  was merely not seen lately is no proof that there is none.
- R7: otherwise the discretionary choice among HOLD, RAID and PRESS.

The trade balance late in the match can hover around zero, a kill either way
turning it over; the clock rules would then turn the plan from keeping a lead
to pressing and back with every kill. So once R2, R3 or R4 has set the plan,
it stays until the balance has stood more than 500 on the other side of zero
for ten seconds; R3, set at a balance of exactly zero, is left the same way,
when the balance has stood more than 500 away from zero on either side.

A late PRESS sent out while the base is under attack loses the base and the
army both: behind on the balance, the army is what is left to hold with. So
with the late-PRESS guard on (`late_press_guard`), R4 does not open while base
defense reports a major threat on the base, and a PRESS R4 set ends on the
first cycle one is reported, whatever the hysteresis would keep; the plan is
then what the other rules give: R2's PRESERVE or R3's HOLD when the balance
calls for them, else what R5 to R7 give, HOLD by the decision table. R4 opens
again once the threat is no longer major. The guard reads the threat level
base defense last published, and looks at nothing else. The tuning file
ships it on, with R4's window at two minutes.

A lead kept by PRESERVE calls home the army that was ending the match, and
with the enemy all but gone that throws the win away. So with
`finish_over_preserve` on, R6 is checked before R2 and R3 in the last three
minutes: the plan is FINISH whenever R6 applies, its rest after FINISH was
left included, and PRESERVE or HOLD only when it does not. A FINISH in force
then stays until R6 has stopped applying for ten seconds
(`clock_switch_ticks`), and a PRESERVE or a HOLD in force gives way to FINISH
in the first cycle R6 applies. R4 keeps its place before R6, the late-PRESS
guard is as above, and a major threat on the base is left to base defense, as
under any FINISH. The tuning file ships it on.

An attack early in the match into an opponent that has not committed its
army loses: that army is at home, growing, and meets main with its defenses.
So with the early-attack guard on (`early_attack_guard`), R5 never starts
STRIKE, whatever its window allows; a cycle in which R5 would apply goes on
to R6 and R7. And before the ninth minute (`early_press_until_seconds`) R7
opens PRESS only once the enemy has committed its army (see below): against
an opponent that rushed, its army spent on the base, the early counter-push
stays. A model's answer, a shorthand line and a sparring pin take only a
plan R7 has open, so none of them takes PRESS then either. A PRESS in force
is not ended by this, and the clock's R4 is left as it is.

The guard also weighs the own army by what fights. The army value the state
gives counts the harvesters and the MCV, which do not; early in the match
they are much of it, and an army weighed with them is called ahead of an
enemy it is not ahead of. So with the guard on, every comparison of the own
army with the enemy's weighs the cost of the own units that fight instead
(`combat_army_value`): the decision table's ratio, the enemy army assumed
while none is known, PRESS's ahead rule and R6(a), whose thresholds and
timing stay as they are. The tuning file ships the guard on. With
`[scouting] scout_slots` on, the designated scouts are left out of both
figures: they scout for the whole match and never fight with the army.

The plan selector follows whether the enemy has committed its army, which
holds for the rest of the match once it does: once a major threat on the
base has been beaten off, or once what the agent has killed is worth 1,500
(`early_press_kill_value`). A major threat is beaten off when base defense,
having reported it, reports no threat any more and no fewer own core
buildings (the types `targeting.CORE_BUILDINGS` names: refinery, power
plant, barracks, war factory, construction yard) stand than in the first
cycle it was reported major in. The kills count every kill, so they say
that blows have been traded, not that the enemy army is spent.

A plan that flickers is worse than either of the two it flickers between:
every change sends main out or calls it back, and breaks up the raids. So a
plan R5, R6 or R7 changed to is kept for at least 30 seconds, whatever those
rules say in between; only the clock (R1 to R4) ends it sooner, or the enemy
base being gone, or a STRIKE that has to be given up, or a major threat on the
base, which ends a discretionary plan (and a plan a model chose that still
stands) so that the decision table holds. And once FINISH is
left, it is not taken up again for 30 seconds.

The enemy figures count only what the world model still trusts: remembered
enemy units that are not stale, and remembered defenses.

An enemy army nobody has seen for a while is no proof that there is none:
most often it is out of sight, growing. So the enemy army counts as known
only while one of its units that fights on the ground (not a harvester, an
MCV, an aircraft or a defense) has been in sight within the last two
minutes; while it is not, PRESS is opened on, and the decision table weighs,
an enemy army assumed as strong as the own, a ratio of 1. While it is known,
they weigh the most the enemy army was worth in those two minutes, when that
is more than what is remembered: units out of sight for half a minute drop
out of the remembered value, but they have not gone. STRIKE and FINISH
still weigh what is remembered. With the guard switched off
(`blind_press_guard`), what is remembered is weighed throughout, 99 as for a
fight while no enemy that fights is known.

A big army that waits for an enemy army nobody sees idles while the enemy
grows its own, and the ratio of 1 above keeps it home whatever it is worth.
So while main is worth `big_army_value` (15,000 as shipped), no enemy unit
that fights on the ground has been in sight for `unknown_army_seconds` (two
minutes) and the enemy base is known, an enemy army that is not known is
weighed at main's worth over `big_army_assumed_ratio` (1.5) instead, and the
decision table presses whenever PRESS is open on that. Every other condition
of PRESS still holds: an enemy base not found never opens it, the clock rules
come first, so a late lead is still kept, a major threat holds, and main does
not fight under enemy defenses (see `defended`). The plan rules publish the
big army facing an unseen enemy army on the context
(`big_army_unseen_enemy`), with or without the base known, and the scouts
then look for that army (see `scout_mission_planner`).

The discretionary choice takes what is open: HOLD always; RAID when raids are
switched on (`[raids].enabled`), the building raids are made in stands, the
agent's family has a raid quota (the Soviets have none with `soviet_raid` at
"none") and there is a target a raid may be sent at, by the raid's own rules
(`raid_targets`): an uncovered harvester, or an uncovered refinery once no
harvester has been seen for a minute and the raid group could destroy it;
with `launchable_only` of `[raids]`, only while a raid could also set out now
(`raids.launch_blocker`: a safe way from the staging point to such a target,
and the units on hand to fill the quota), so that a RAID chosen is a raid
that sets out, and RAID's closed reason then says which of these fails;
PRESS from the sixth minute with an army of 3,000 that is at least one and a
half times the enemy's as weighed above, so never against an enemy army
nobody has seen lately. An enemy base not found yet does not open PRESS
either: finding it is the scouts' job, and they keep being sent, and late in
the match the clock rules decide; main holds rather than go forward blind.
With the guard off, PRESS also opens, whatever the army, when the enemy base
has not been found by the fifth minute and no scout is out to find it, and
then main goes forward on the enemy's start to find it. With `[scouting]
scout_slots` on, whose scouts are out for the whole match, a scout counts as
out to find it only while one is heading for a place within
scout_view_cells of the enemy's start. Under the
early-attack guard neither opens PRESS before the ninth minute while the
enemy has not committed its army, unless PRESS is the plan in force. The
script chooses among them by a decision table, on the ratio of the own
army's value to the enemy's combat value as weighed above and on the
base-defense threat; the first rule that applies decides:

1. HOLD under a major threat: the army is needed at home.
2. HOLD when weaker than the enemy (a ratio below 1).
3. PRESS when it is open and the army is clearly stronger (a ratio of 2 or
   more), or, with the guard off, when it is open and the enemy base has not
   been seen, or when the enemy army is weighed from a big main (see above).
4. RAID when it is open.
5. Otherwise HOLD.

What it returns is always one of the open plans. A discretionary plan, once set, is kept
for ten seconds unless it is no longer open, so the plan does not flicker
between two that are about as good: it is held as above, even when it is
no longer open. In a test match a seat pinned in the sparring settings plays
its pinned plan whenever that plan is open.

A model may make the discretionary choice instead of the script, when the
settings let it (`model_plan_choice`). Whenever two plans or more are open,
the `red_alert_plan` chain is asked, at most every five seconds, which of them
to play: it gets the tracked prompt template as the question and the compact
summary of the match (see `shorthand`) as the context. With the briefing
switched on (`model_briefing`) it gets instead the briefing's own question and
the plain-words briefing of the match (see `briefing`), and answers with a
reason of at most 30 words before its choice, in at most 96 tokens: a small
model that weighs the options in words before it names one chooses from what
it has just weighed. With the plan prompt "compact" (`plan_prompt`), whatever
`model_briefing` says, it gets the compact prompt instead (see
`compact_prompt`): a system text of its own, the state as `key=value` lines
and a line for each open plan, answered with the choice first and a reason of
at most 12 words, in at most 48 tokens. Its answer is taken
only when it was used, names a plan that is still open, and is at most ten
seconds old, also while the script's own choice is held; the plan it names
then stands while it stays open. A second answer within ten seconds of the
first changes nothing. An answer taken is one of the model's recent plan
picks, which every plan question after it shows with what came of it since
(`recent picks:`, see `model_history`), and in every cycle the plan rules
publish their army ratio there for the picks' outcomes. A plan named by a
`plan` line of command shorthand (`request`) is taken the same way. Neither
a model nor a shorthand line ever overrides R1 to R6, or a sparring pin.

A raid sent and called back, or an attack begun and broken off, every few
seconds is worse than either plan carried through, and answers ten seconds
apart can turn the plan back and forth that often. So with `model_plan_hold`
on, a plan a model's answer (or a shorthand line) changed to is held for
`model_plan_hold_ticks`, 30 seconds as shipped: no later answer replaces it,
the decision source is not asked meanwhile, and R7 keeps it even when it is no
longer open. R1 to R4 still end the hold whenever they apply, and R5 and R6
once the hold of a plan R7 set is over, as they end any discretionary plan; so
does a major threat on the base, and for a RAID, its closing because no raid
target is known any more or because no raid set out in time after it was
chosen. A RAID closed for want of units on hand, as when the raid borrows them
from main, is kept. Each such end is written as one `plan_rule_blocked` event
with the reason `model_hold_ended` and its cause: the rule that took over,
`major_threat`, or RAID's closed reason. A model's answer that only confirms
the plan in force starts no hold. Nor, with the switch on, is the decision
source asked, or an answer taken, while base defense reports a major threat
on the base: the decision table holds then, and a plan taken from an answer
would be dropped again in the next cycle. An answer that arrives then, to a
question put before the threat, is written as one `plan_rule_blocked` event
with the reason `model_answer_discarded`, the cause `major_threat` and the
plan it named; the decision record already holds the answer as it came.

Every change of plan is written to the match record as one `plan_change`
event with the old plan, the new, the rule that made the change and the
figures it was decided on. A block of R4 by the late-PRESS guard, the cycles
in a row it holds R4 back, is written as one `plan_rule_blocked` event in its
first cycle, with the reason `R4_base_threat` and the plan and rule the other
rules gave instead. So is a block by the early-attack guard, with the reason
`early_attack_guard`: of R5, the cycles in a row in which R5, consulted,
would start STRIKE but for the guard, and of R7, those in which PRESS's own
rules would open it but for the early gate. A cycle in which the rule is not
consulted, a plan held or a clock rule in force, ends its block. The blocks
of each rule are followed on their own.
"""

from __future__ import annotations

import dataclasses
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from ucla_agent.core import records
from ucla_agent.core.decide import USED, ChoiceRequest, ChoiceResult, Option
from ucla_agent.core.match_meta import TESTING_CONTEXT
from ucla_agent.games.redalert import cells, raid_targets, raids
from ucla_agent.games.redalert.composition import FINISH, HOLD, OPENING, PRESERVE, PRESS, RAID, STRIKE
from ucla_agent.games.redalert.family import FAMILIES, WAR_FACTORY
from ucla_agent.games.redalert.fight import estimate, is_counted, strength
from ucla_agent.games.redalert.groups import MAIN, SCOUT
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.managers import MAJOR_THREAT, NO_THREAT, MatchContext
from ucla_agent.games.redalert.settings import RedAlertSettings
from ucla_agent.games.redalert.shorthand import summarize
from ucla_agent.games.redalert.targeting import CORE_BUILDINGS
from ucla_agent.games.redalert.tuning import CombatTuning, PlanRulesTuning, Tuning
from ucla_agent.games.redalert.world import EnemyRecord, Unit, World

R1, R2, R3, R4, R5, R6, R7 = "R1", "R2", "R3", "R4", "R5", "R6", "R7"
# Why the late-PRESS guard held R4 back, and the early-attack guard R5 or R7, as the match record names it.
LATE_PRESS_BLOCK_REASON = "R4_base_threat"
EARLY_ATTACK_BLOCK_REASON = "early_attack_guard"
# What keeps RAID or PRESS shut, as `closed_reasons` names it: raids switched off, the building raids are made in not
# standing, no raid quota for the family, no target a raid may be sent at, a raid that could not set out now (with
# [raids] launchable_only, its words the Situation's `raid_launch_blocker`); the early-attack gate
# (EARLY_ATTACK_BLOCK_REASON), and each threshold of PRESS's ahead rule not met (the time, the army, the ratio).
RAIDS_OFF = "raids_off"
NO_RAID_BUILDING = "no_raid_building"
NO_RAID_QUOTA = "no_raid_quota"
NO_RAID_TARGET = "no_raid_target"
RAID_NOT_LAUNCHABLE = "raid_not_launchable"
PRESS_TOO_EARLY = "press_too_early"
ARMY_TOO_SMALL = "army_too_small"
ARMY_NOT_AHEAD = "army_not_ahead"
# The tanks and long-range units: an army that ends a match by R6(a) has to break defenses, which infantry cannot.
HEAVY_UNITS = frozenset({"1tnk", "2tnk", "3tnk", "4tnk", "arty", "v2rl", "ttnk"})
# The production queue of the aircraft: an aircraft seen says nothing of the army that would meet main on the ground.
AIRCRAFT_QUEUE = "Aircraft"
# The rules of the discretionary choice besides the script's own: a decision source's answer, a sparring pin.
MODEL = "model"
SPARRING = "sparring"
# The rules that set a discretionary plan.
DISCRETIONARY_RULES = frozenset({R7, MODEL, SPARRING})
# The reason a `plan_rule_blocked` event gives for the hold of a plan a model's answer changed to ended before its time
# (see ModelHold), and for a model's answer not taken under a major threat; and the cause it names for a major threat.
MODEL_HOLD_ENDED = "model_hold_ended"
MODEL_ANSWER_DISCARDED = "model_answer_discarded"
MAJOR_THREAT_CAUSE = "major_threat"
# The rules the clock and the trade balance set late in the match, and those of them R6 comes before with
# finish_over_preserve on.
CLOCK_RULES = frozenset({R2, R3, R4})
FINISH_FIRST_RULES = frozenset({R2, R3})
# The sides of zero a trade balance can stand on, by more than the clock rules' margin.
AHEAD, BEHIND = 1, -1
# The rules whose changes of plan are held for a while: all but R1 to R4, which the clock and the opening set.
HELD_RULES = frozenset({R5, R6, *DISCRETIONARY_RULES})
# The question a decision source is asked with the plan choice, a tracked file of the package; and the one asked with
# the briefing, which tells of the briefing's sections where the other tells of the summary's keys.
PROMPT_FILE = Path(__file__).parent / "prompts" / "plan_choice.txt"
BRIEFING_PROMPT_FILE = Path(__file__).parent / "prompts" / "plan_choice_briefing.txt"
# A briefed decision source writes a reason of at most this many words before its choice, in at most this many tokens.
BRIEFED_REASON_WORDS = 30
BRIEFED_ANSWER_TOKENS = 96
# What a decision source is told each discretionary plan does.
PLAN_DESCRIPTIONS = {
    HOLD: "stay home, grow the economy and the army, defend",
    RAID: "a small fast group hits the enemy economy where no defense covers it",
    PRESS: "the main army attacks the enemy base once it is strong",
}


def plan_prompt(briefed: bool = False) -> str:
    """The question of the plan choice: the tracked prompt template, the one that goes with the briefing when
    `briefed`."""
    return (BRIEFING_PROMPT_FILE if briefed else PROMPT_FILE).read_text(encoding="utf-8")


class Briefer(Protocol):
    """What writes a briefing as the plan choice's context in place of the compact summary (`briefing.Briefing`)."""

    def write(self, world: World, ctx: MatchContext, situation: Situation, eligible: list[str]) -> str:
        """The briefing of the cycle whose figures are `situation`, with `eligible` the discretionary plans open."""


class PlanQuestion(Protocol):
    """What writes the whole plan question, its system text and answer included, in place of the prompt template and
    the summary or the briefing (`compact_prompt.CompactPrompt`)."""

    def request(
        self,
        world: World,
        ctx: MatchContext,
        situation: Situation,
        eligible: list[str],
        deadline_ms: int,
        state_tag: str,
    ) -> ChoiceRequest:
        """The plan question of the cycle whose figures are `situation`, offering the open plans `eligible`."""


@dataclass(frozen=True)
class Situation:
    """The figures the plan rules are decided on, as one cycle sees them."""

    tick: int
    game_seconds: float
    # None while the state gives no clock.
    remaining_seconds: float | None
    trade_balance: int
    army_value: int
    main_strength: int
    # The summed cost of the remembered enemy units that fight and are not stale, and how many there are.
    enemy_combat_value: int
    enemy_units: int
    enemy_buildings: int
    enemy_defenses: int
    # The summed cost of the remembered enemy defenses.
    defense_cost: int
    base_seen: bool
    has_war_factory: bool
    # The agent raids: raids are switched on ([raids].enabled), the building its raids are made in stands, the war
    # factory, or the barracks of a family raiding on foot, and its family has a raid quota as the world stands (none
    # for the Soviets with [composition] soviet_raid "none").
    can_raid: bool
    # A remembered enemy stands that the raid group, at its full quota, may be sent at (`raid_targets.targets`).
    raid_target_known: bool
    # How long ago an enemy unit that fights was last in sight; None while none has been.
    enemy_unseen_ticks: int | None
    # A scout is out to find the enemy base (see `_is_scout_active`).
    scout_active: bool
    # An enemy unit that fights on the ground was in sight within the last enemy_army_fresh_ticks.
    enemy_army_known: bool
    # The own army's value (its combat units' under the early-attack guard) over the enemy's combat value as the
    # decision table weighs it (see weighed_enemy_value); ratio_without_enemy of [combat] while that is 0.
    ratio: float = CombatTuning.ratio_without_enemy
    # The share of main's value in tanks and long-range units, each unit at its cost times its share of health;
    # 0 while main is worth nothing.
    main_heavy_share: float = 0.0
    # The largest enemy combat value of the cycles within the last enemy_army_fresh_ticks before this one; 0 while
    # none is kept.
    recent_enemy_army_value: int = 0
    # The summed cost of the own units that fight (`world.combat_units`): no harvester, MCV or other unit that does not.
    combat_army_value: int = 0
    # The enemy has committed its army in this match (see EnemyCommitment).
    enemy_committed: bool = False
    # With [raids] launchable_only, why a raid could not set out now, in the words of `raids.launch_blocker`; None
    # while one could, while the agent cannot raid at all, and always with the switch off.
    raid_launch_blocker: str | None = None
    # How long ago an enemy unit of its army (one that fights on the ground, see `_is_army_unit`) was last in sight;
    # None while none has been.
    enemy_army_unseen_ticks: int | None = None

    def is_late(self, seconds: int) -> bool:
        """Whether at most `seconds` of the match are left; never while the clock is unknown."""
        return self.remaining_seconds is not None and self.remaining_seconds <= seconds

    @property
    def is_enemy_base_destroyed(self) -> bool:
        """Whether the enemy base was found and no enemy building is remembered any more."""
        return self.base_seen and self.enemy_buildings == 0

    def has_seen_no_enemy_unit_for(self, ticks: int) -> bool:
        """Whether no enemy unit that fights has been in sight for at least `ticks`, stale or not."""
        return self.enemy_unseen_ticks is None or self.enemy_unseen_ticks >= ticks


def observe(
    world: World,
    ctx: MatchContext,
    tuning: Tuning,
    enemy_seen_tick: int | None,
    recent_enemy_army_value: int = 0,
    enemy_committed: bool = False,
    army_seen_tick: int | None = None,
) -> Situation:
    """The figures of this cycle: the clock and the balance, the own army and main, and what is known of the enemy.

    `enemy_seen_tick` is the last tick an enemy unit that fights was in sight; None while none has been.
    `recent_enemy_army_value` is the largest enemy combat value of the cycles within the last enemy_army_fresh_ticks;
    0 for a caller that keeps none. `enemy_committed` is whether the enemy has committed its army; False for a caller
    that does not follow it. `army_seen_tick` is the last tick an enemy unit of its army was in sight; None while
    none has been.
    """
    remembered = list(world.enemy_memory.values())
    counted = [record for record in remembered if is_counted(record)]
    defenses = world.known_enemy_defenses
    main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
    # The designated scouts of `scout_slots` are no part of the army the plans weigh.
    scouts = _cost_of(_slot_scouts(world, ctx), world)
    army_value = world.military.army_value - scouts
    combat_army_value = _cost_of(world.combat_units.values(), world) - scouts
    own_army = _own_army(army_value, combat_army_value, tuning.plan_rules)
    enemy_combat_value = _cost_of(counted, world)
    fresh_ticks = tuning.plan_rules.enemy_army_fresh_ticks
    enemy_army_known = any(
        world.tick - record.last_seen_tick < fresh_ticks for record in remembered if _is_army_unit(record, world)
    )
    main_strength = round(strength(main, world))
    army_unseen_ticks = None if army_seen_tick is None else world.tick - army_seen_tick
    base_seen = ctx.scout_report.enemy_base_seen
    weighed = weighed_enemy_value(
        own_army,
        enemy_combat_value,
        recent_enemy_army_value,
        enemy_army_known,
        tuning.plan_rules,
        assumed_main(main_strength, army_unseen_ticks, base_seen, tuning.plan_rules),
    )
    can_raid = not raid_blockers(world, ctx, tuning)
    raid_target_known = bool(raid_targets.targets(world, raid_group_types(world, tuning), tuning))
    return Situation(
        tick=world.tick,
        game_seconds=world.game_seconds,
        remaining_seconds=world.remaining_seconds,
        trade_balance=world.trade_balance,
        army_value=army_value,
        main_strength=main_strength,
        enemy_combat_value=enemy_combat_value,
        enemy_units=len(counted),
        enemy_buildings=sum(1 for record in remembered if record.is_building),
        enemy_defenses=len(defenses),
        defense_cost=_cost_of(defenses, world),
        base_seen=base_seen,
        has_war_factory=ctx.production.standing(WAR_FACTORY) > 0,
        can_raid=can_raid,
        raid_target_known=raid_target_known,
        enemy_unseen_ticks=None if enemy_seen_tick is None else world.tick - enemy_seen_tick,
        scout_active=_is_scout_active(world, ctx),
        enemy_army_known=enemy_army_known,
        ratio=_ratio(own_army, weighed, tuning),
        main_heavy_share=_heavy_share(main, world),
        recent_enemy_army_value=recent_enemy_army_value,
        combat_army_value=combat_army_value,
        enemy_committed=enemy_committed,
        raid_launch_blocker=_launch_blocker(world, ctx, tuning, can_raid, raid_target_known),
        enemy_army_unseen_ticks=army_unseen_ticks,
    )


def _launch_blocker(
    world: World, ctx: MatchContext, tuning: Tuning, can_raid: bool, raid_target_known: bool
) -> str | None:
    """With [raids] launchable_only, why a raid could not set out now (`raids.launch_blocker`); None with the switch
    off, and while the agent cannot raid at all, which `raid_blockers` gives its own reasons for."""
    if not (tuning.raids.launchable_only and can_raid):
        return None
    return raids.launch_blocker(world, ctx, raid_target_known)


def raid_building(world: World) -> str:
    """The building the agent's raids are made in: the war factory, or the barracks of a family that raids on foot."""
    names = FAMILIES[world.family]
    return WAR_FACTORY if names.raid_infantry is None else names.barracks


def raid_blockers(world: World, ctx: MatchContext, tuning: Tuning) -> list[str]:
    """What keeps the agent from raiding at all, in this order: raids switched off, the building raids are made in
    not standing, no raid quota for its family as the world stands; none when it can raid."""
    checks = (
        (RAIDS_OFF, tuning.raids.enabled),
        (NO_RAID_BUILDING, ctx.production.standing(raid_building(world)) > 0),
        (NO_RAID_QUOTA, bool(raids.quota(world, tuning.composition))),
    )
    return [blocker for blocker, holds in checks if not holds]


def _own_army(army_value: int, combat_army_value: int, rules: PlanRulesTuning) -> int:
    """The own army's value the plan rules weigh against the enemy's: its combat units' under the early-attack guard.

    The army value the state gives counts the harvesters and the MCV, which do not fight; early in the match they are
    much of it, and an army weighed with them is called ahead of an enemy it is not ahead of.
    """
    return combat_army_value if rules.early_attack_guard else army_value


def weighed_army(situation: Situation, rules: PlanRulesTuning) -> int:
    """The own army's value as the plan rules weigh it in a situation (see `_own_army`)."""
    return _own_army(situation.army_value, situation.combat_army_value, rules)


def weighed_enemy(situation: Situation, rules: PlanRulesTuning) -> float:
    """The enemy's combat value as PRESS's ahead rule and the decision table weigh it in a situation."""
    return weighed_enemy_value(
        weighed_army(situation, rules),
        situation.enemy_combat_value,
        situation.recent_enemy_army_value,
        situation.enemy_army_known,
        rules,
        _assumed_main_of(situation, rules),
    )


def weighed_enemy_value(
    army_value: int,
    enemy_combat_value: int,
    recent_enemy_army_value: int,
    enemy_army_known: bool,
    rules: PlanRulesTuning,
    main_value: int | None = None,
) -> float:
    """The enemy's combat value that PRESS is opened on and the decision table's ratio weighs.

    With the guard on, an enemy army that has not been seen lately is assumed to be worth a share of the own: what
    is remembered of it then is mostly nothing, and nothing is no proof that it has no army. While a big main faces
    an enemy army unseen for long, with its base known, it is assumed instead at `main_value` (see `assumed_main`)
    over big_army_assumed_ratio: an army that big that waits for one nobody sees idles while the enemy grows. A known
    army is worth at least the most it was lately: its units drop out of the remembered value long before it stops
    being known, and an army out of sight for a minute has not gone. With the guard off it is the remembered value.
    """
    if not rules.blind_press_guard:
        return enemy_combat_value
    if not enemy_army_known and main_value is not None:
        return main_value / rules.big_army_assumed_ratio
    if not enemy_army_known:
        return rules.assumed_enemy_share * army_value
    return max(enemy_combat_value, recent_enemy_army_value)


def is_big_army_unseen_enemy(main_strength: int, army_unseen_ticks: int | None, rules: PlanRulesTuning) -> bool:
    """Whether main is worth big_army_value and no enemy unit of its army has been in sight for unknown_army_seconds,
    or ever."""
    is_unseen = army_unseen_ticks is None or army_unseen_ticks >= rules.unknown_army_seconds * TICKS_PER_SECOND
    return main_strength >= rules.big_army_value and is_unseen


def assumed_main(
    main_strength: int, army_unseen_ticks: int | None, base_seen: bool, rules: PlanRulesTuning
) -> int | None:
    """main's worth, from which an enemy army not known is weighed (see `weighed_enemy_value`): while the big army
    faces an unseen enemy army (`is_big_army_unseen_enemy`) with the enemy base known; None otherwise, and always at a
    big_army_assumed_ratio of 0, which weighs nothing from main.

    An enemy base not found never opens PRESS: main does not go forward blind, whatever it is worth.
    """
    is_assumed = base_seen and rules.big_army_assumed_ratio > 0
    return main_strength if is_assumed and is_big_army_unseen_enemy(main_strength, army_unseen_ticks, rules) else None


def _assumed_main_of(situation: Situation, rules: PlanRulesTuning) -> int | None:
    """`assumed_main` in a situation."""
    return assumed_main(situation.main_strength, situation.enemy_army_unseen_ticks, situation.base_seen, rules)


def is_enemy_weighed_from_main(situation: Situation, rules: PlanRulesTuning) -> bool:
    """Whether the enemy army is weighed from a big main in a situation: the guard on, the army not known, and main
    assumed from (`assumed_main`)."""
    is_unknown = rules.blind_press_guard and not situation.enemy_army_known
    return is_unknown and _assumed_main_of(situation, rules) is not None


class RecentMaximum:
    """The largest of the values noted within the last `span` ticks.

    Only the values that may still be the largest are kept: each is larger than every one noted after it, so the
    oldest kept is the largest, and noting and asking cost O(1) a cycle on the whole.
    """

    def __init__(self, span: int) -> None:
        """An empty record of the values of the last `span` ticks."""
        self._span = span
        # (tick, value) in the order noted, the values falling.
        self._kept: deque[tuple[int, int]] = deque()

    def note(self, tick: int, value: int) -> None:
        """Note a value at a tick; a value noted earlier that is no larger can never be the largest again."""
        while self._kept and self._kept[-1][1] <= value:
            self._kept.pop()
        self._kept.append((tick, value))

    def largest(self, tick: int) -> int:
        """The largest value noted less than `span` ticks before `tick`; 0 when none was."""
        while self._kept and tick - self._kept[0][0] >= self._span:
            self._kept.popleft()
        return self._kept[0][1] if self._kept else 0


class EnemyCommitment:
    """Whether the enemy has committed its army in this match; once it has, it stays so. See the module's note."""

    def __init__(self, kill_value: int) -> None:
        """Not committed, with no major threat in progress; the kills worth `kill_value` commit it."""
        self._kill_value = kill_value
        self.is_committed = False
        # The own core buildings standing in the first cycle of the major threat in progress; None while none is.
        self._core_at_threat: int | None = None

    def follow(self, threat_level: str, core_buildings: int, kills_cost: int) -> None:
        """Take in a cycle: the threat level base defense last published, the own core buildings standing and the
        value of what has been killed so far.

        A minor threat between a major one and none leaves the threat in progress: it is over only when none is.
        """
        if threat_level == MAJOR_THREAT and self._core_at_threat is None:
            self._core_at_threat = core_buildings
        elif threat_level == NO_THREAT and self._core_at_threat is not None:
            self.is_committed = self.is_committed or core_buildings >= self._core_at_threat
            self._core_at_threat = None
        self.is_committed = self.is_committed or kills_cost >= self._kill_value


class ModelHold:
    """With `model_plan_hold` on, the hold of a plan a model's answer changed to. See the module's note."""

    def __init__(self, rules: PlanRulesTuning) -> None:
        """No plan held; the switch and the length of a hold are those of `rules`."""
        self._rules = rules
        # The tick a model's answer changed the plan in force to the one it is; None while no such plan is in force,
        # and always with the switch off.
        self._since: int | None = None

    def is_in_force(self, tick: int) -> bool:
        """Whether a plan a model's answer changed to is in force and was set less than model_plan_hold_ticks ago."""
        return self._since is not None and tick - self._since < self._rules.model_plan_hold_ticks

    def follow(self, old: str | None, plan: str, rule: str, tick: int) -> None:
        """Take in the plan and the rule a cycle decided, after the plan `old`: a model's answer that changed the plan
        starts a hold, and any other rule ends the one in force; a model's answer that keeps the plan changes
        nothing."""
        if rule != MODEL:
            self._since = None
        elif plan != old and self._rules.model_plan_hold:
            self._since = tick


def raid_hold_end(situation: Situation) -> str | None:
    """Why a RAID a model's answer changed to is held no longer, in the words of its closed reason: no raid set out in
    time after RAID was chosen, or no raid target is known; None while neither holds.

    A raid short of units on hand, as when the raid borrows them from main, is kept: they are on their way to it.
    """
    if situation.raid_launch_blocker == raids.NOT_SET_OUT:
        return raids.NOT_SET_OUT
    return None if situation.raid_target_known else raids.NO_TARGET_KNOWN


def model_hold_breach(plan: str | None, situation: Situation, threat_level: str) -> str | None:
    """What ends the hold of a plan a model's answer changed to within R7: a major threat on the base, or for a RAID
    its closing as `raid_hold_end` gives it; None while the hold stands."""
    if threat_level == MAJOR_THREAT:
        return MAJOR_THREAT_CAUSE
    return raid_hold_end(situation) if plan == RAID else None


def _core_buildings(ctx: MatchContext) -> int:
    """How many own buildings stand that make the economy and the army, the types `targeting.CORE_BUILDINGS` names."""
    return sum(ctx.production.standing(building) for building in CORE_BUILDINGS)


def _ratio(army_value: int, enemy_value: float, tuning: Tuning) -> float:
    """The own army's value over the enemy's weighed value; the [combat] ratio without an enemy while that is 0."""
    return army_value / enemy_value if enemy_value > 0 else tuning.combat.ratio_without_enemy


def _is_army_unit(record: EnemyRecord, world: World) -> bool:
    """Whether a remembered enemy is one of its army's: a unit that fights, and not an aircraft.

    A type the item table does not know says nothing of the enemy's army, which then stays as unknown as it was.
    """
    facts = world.items.get(record.type)
    return is_combat_unit(record) and facts is not None and facts.queue != AIRCRAFT_QUEUE


def _heavy_share(main: list[Unit], world: World) -> float:
    """The share of main's value in tanks and long-range units; 0 while main is worth nothing."""
    value = strength(main, world)
    heavy = strength((unit for unit in main if unit.type in HEAVY_UNITS), world)
    return heavy / value if value > 0 else 0.0


def _is_scout_active(world: World, ctx: MatchContext) -> bool:
    """Whether a scout is out finding the enemy base: one in the scout group on a mission; with `scout_slots` on, whose
    scouts are out for good, only while one of them is heading for a place within scout_view_cells of the enemy's
    start."""
    tuning = ctx.tuning.scouting
    if not tuning.scout_slots:
        return bool(ctx.groups.members(SCOUT))
    return world.enemy_base is not None and any(
        cells.is_within(destination, world.enemy_base, tuning.scout_view_cells)
        for destination in ctx.scout_destinations
    )


def _slot_scouts(world: World, ctx: MatchContext) -> list[Unit]:
    """The units of the scout group in the field while `scout_slots` is on, which keeps them as scouts for good;
    none with it off, when the scout group holds a scout on a mission that goes back to the army."""
    if not ctx.tuning.scouting.scout_slots:
        return []
    return [world.combat_units[unit_id] for unit_id in ctx.groups.members(SCOUT)]


def _cost_of(actors: Iterable[EnemyRecord | Unit], world: World) -> int:
    """What units or remembered enemies cost together, whatever their health; a type of no known cost counts nothing."""
    return sum(facts.cost for facts in (world.items.get(actor.type) for actor in actors) if facts is not None)


def raid_group_types(world: World, tuning: Tuning) -> list[str]:
    """The types of the units of the raid group at its full quota, one for each unit: the raid RAID would send.

    A raid is sent only once the group holds its whole quota, so whether a refinery is within a raid's reach is
    judged by the quota's damage, whatever the group holds now.
    """
    return [unit_type for unit_type, count in raids.quota(world, tuning.composition).items() for _ in range(count)]


class PlanRules:
    """The agent's plan selector: the script's rules R1 to R7. See the module's note."""

    def __init__(
        self,
        tuning: Tuning,
        settings: RedAlertSettings,
        prompt: str | None = None,
        briefing: Briefer | None = None,
        compact: PlanQuestion | None = None,
    ) -> None:
        """The rules for one match. A decision source is asked to choose only with model plan choice on, and a prompt
        or a compact question; with a briefing it reads the briefing as the context and answers with its reason first,
        and with a compact question it is put that question instead."""
        self._tuning = tuning
        self._seat_plans = settings.seat_plans
        self._prompt = prompt if settings.model_plan_choice else None
        self._briefing = briefing
        self._compact = compact if settings.model_plan_choice else None
        # The plan and the rule of the last cycle; None before the first.
        self._plan: str | None = None
        self._rule: str | None = None
        self._is_opening_over = False
        # STRIKE was given up, and is not chosen again in this match.
        self._is_strike_excluded = False
        # The tick the discretionary plan in force was set at.
        self._chosen_tick = 0
        # The tick of the last change of plan R5, R6 or R7 made, which holds the plan for a while.
        self._changed_tick = 0
        # The tick FINISH was last left at; None while it never was.
        self._left_finish_tick: int | None = None
        # The last tick R6's conditions held, rest or not; None while they never have.
        self._finish_tick: int | None = None
        # The last tick an enemy unit that fights was in sight, and one of its army; None while none has been.
        self._enemy_seen_tick: int | None = None
        self._army_seen_tick: int | None = None
        # The enemy combat value of the cycles of the last enemy_army_fresh_ticks: the most the enemy army was worth.
        self._recent_enemy_values = RecentMaximum(tuning.plan_rules.enemy_army_fresh_ticks)
        self._commitment = EnemyCommitment(tuning.plan_rules.early_press_kill_value)
        # The figures the last cycle's plan was decided on; None before the first.
        self._situation: Situation | None = None
        # The side of zero the trade balance stands on by more than the clock margin (AHEAD, BEHIND, or None
        # within it), and the tick since which it has.
        self._balance_side: int | None = None
        self._balance_side_tick = 0
        # The discretionary plans open in the last cycle.
        self._eligible: list[str] = []
        # The tick the decision source was last asked at; None while it never was.
        self._asked_tick: int | None = None
        # The plan a decision source or a shorthand line chose, which stands while it is open.
        self._chosen: str | None = None
        self._model_hold = ModelHold(tuning.plan_rules)
        # The plan a decision source's answer named that the cycle under way did not take for a major threat; None
        # while there is none.
        self._discarded: str | None = None
        # A plan named by a shorthand line, taken in the next cycle.
        self._requested: str | None = None
        # The rules a guard held back in the cycle under way, each with the guard's reason: each is then part of a block.
        self._blocks: set[tuple[str, str]] = set()

    def request(self, plan: str) -> bool:
        """Take a plan a shorthand `plan` line names, for the next cycle; False when it is not one of those open."""
        self._requested = plan if plan in self._eligible else None
        return self._requested is not None

    @property
    def situation(self) -> Situation | None:
        """The figures the last cycle's plan was decided on; None before the first cycle."""
        return self._situation

    def select(self, world: World, ctx: MatchContext, choice: ChoiceResult | None) -> str:
        """The plan for this cycle; a change of plan is logged, and the rule in force published on the context, with
        whether the big army faces an unseen enemy army (`is_big_army_unseen_enemy`)."""
        in_sight = [record for record in world.enemy_memory.values() if record.visible and is_combat_unit(record)]
        if in_sight:
            self._enemy_seen_tick = world.tick
        if any(_is_army_unit(record, world) for record in in_sight):
            self._army_seen_tick = world.tick
        recent = self._recent_enemy_values.largest(world.tick)
        commitment = self._commitment
        commitment.follow(ctx.threat_level, _core_buildings(ctx), world.military.kills_cost)
        situation = self._situation = observe(
            world, ctx, self._tuning, self._enemy_seen_tick, recent, commitment.is_committed, self._army_seen_tick
        )
        self._recent_enemy_values.note(world.tick, situation.enemy_combat_value)
        ctx.model_history.ratio = situation.ratio
        ctx.big_army_unseen_enemy = is_big_army_unseen_enemy(
            situation.main_strength, situation.enemy_army_unseen_ticks, self._tuning.plan_rules
        )
        self._follow_balance(situation)
        if _finishes(situation, self._tuning.plan_rules):
            self._finish_tick = situation.tick
        was_blocked, self._blocks = self._blocks, set()
        was_model_held, self._discarded = self._model_hold.is_in_force(world.tick), None
        plan, rule = self._decide(situation, world, ctx, choice)
        # A block is recorded once, in its first cycle: the cycles after it say nothing new.
        for blocked_rule, reason in sorted(self._blocks - was_blocked):
            _log_block(blocked_rule, reason, plan, rule, situation, ctx.threat_level)
        self._log_model_hold(plan, rule, situation, ctx.threat_level, was_model_held)
        self._remember(plan, rule, situation, ctx.threat_level)
        ctx.plan_rule = rule
        return plan

    def _log_model_hold(
        self, plan: str, rule: str, situation: Situation, threat_level: str, was_model_held: bool
    ) -> None:
        """Write what the cycle did against a model's answers: one discarded under a major threat, and the hold of a
        plan a model's answer changed to ended before its time."""
        if self._discarded is not None:
            detail = {"cause": MAJOR_THREAT_CAUSE, "option": self._discarded}
            _log_block(MODEL, MODEL_ANSWER_DISCARDED, plan, rule, situation, threat_level, **detail)
        if was_model_held and rule != MODEL:
            cause = self._hold_end_cause(rule, situation, threat_level)
            _log_block(MODEL, MODEL_HOLD_ENDED, plan, rule, situation, threat_level, cause=cause)

    def _hold_end_cause(self, rule: str, situation: Situation, threat_level: str) -> str:
        """What ended the hold of a plan a model's answer changed to before its time: the rule that took over, else
        what ended it within R7 (`model_hold_breach`)."""
        if rule != R7:
            return rule
        return model_hold_breach(self._plan, situation, threat_level) or rule

    def _remember(self, plan: str, rule: str, situation: Situation, threat_level: str) -> None:
        """Keep what the next cycles decide by: when the plan and its rule were set, and the change in the record."""
        tick = situation.tick
        self._requested = None
        if rule not in DISCRETIONARY_RULES:
            self._chosen = None
        elif plan != self._plan or self._rule not in DISCRETIONARY_RULES:
            self._chosen_tick = tick
        if self._rule == R6 and rule != R6:
            self._left_finish_tick = tick
        if plan != self._plan:
            self._changed_tick = tick
            _log_change(self._plan, plan, rule, situation, threat_level)
        self._model_hold.follow(self._plan, plan, rule, tick)
        self._plan, self._rule = plan, rule

    def _decide(
        self, situation: Situation, world: World, ctx: MatchContext, choice: ChoiceResult | None
    ) -> tuple[str, str]:
        """The plan and the rule that sets it: the first of R1 to R7 that applies."""
        self._is_opening_over = self._is_opening_over or situation.has_war_factory
        if not self._is_opening_over:
            return OPENING, R1
        return self._clock(situation, ctx.threat_level) or self._after_clock(situation, world, ctx, choice)

    def _after_clock(
        self, situation: Situation, world: World, ctx: MatchContext, choice: ChoiceResult | None
    ) -> tuple[str, str]:
        """R5 to R7, the plan they changed to held for a while: R5 or R6 kept outright, R7's own choice within R7."""
        is_held = self._is_held_now(situation, world, ctx)
        if is_held and self._rule not in DISCRETIONARY_RULES:
            return self._plan, self._rule
        scripted = None if is_held else self._strike_or_finish(situation)
        return scripted or self._discretionary(situation, world, ctx, choice, is_held)

    def _is_held_now(self, situation: Situation, world: World, ctx: MatchContext) -> bool:
        """Whether the plan in force is held, but for a STRIKE that has to be given up, which is never held, a
        discretionary plan under a major threat, which the decision table decides again, and a RAID a model's answer
        changed to that closed for want of a target or a launch (`raid_hold_end`)."""
        if self._plan == STRIKE and self._must_abandon(situation, world, ctx):
            self._is_strike_excluded = True
        is_given_up = self._plan == STRIKE and self._is_strike_excluded
        is_threatened = self._rule in DISCRETIONARY_RULES and ctx.threat_level == MAJOR_THREAT
        return self._is_held(situation) and not (is_given_up or is_threatened or self._is_held_raid_ended(situation))

    def _is_held_raid_ended(self, situation: Situation) -> bool:
        """Whether the plan in force is a RAID a model's answer changed to, still held, that closed for want of a
        target or a launch."""
        is_held_raid = self._plan == RAID and self._model_hold.is_in_force(situation.tick)
        return is_held_raid and raid_hold_end(situation) is not None

    def _follow_balance(self, situation: Situation) -> None:
        """Note which side of zero the trade balance stands on by more than the margin, and since when."""
        margin = self._tuning.plan_rules.clock_balance_margin
        balance = situation.trade_balance
        side = AHEAD if balance > margin else BEHIND if balance < -margin else None
        if side != self._balance_side:
            self._balance_side, self._balance_side_tick = side, situation.tick

    def _clock(self, situation: Situation, threat_level: str) -> tuple[str, str] | None:
        """R2 to R4, with hysteresis; an R4 the late-PRESS guard holds back gives way to the other rules.

        Those are R2 or R3 when the balance calls for one, which an R4 the hysteresis kept may have stood in the way
        of, else R5 to R7, for which this gives None. With finish_over_preserve on, R6 comes before R2 and R3.
        """
        called_for = _clock_rule(situation, self._tuning.plan_rules)
        clock = self._clock_in_force(situation, called_for)
        if self._is_late_press_blocked(clock, threat_level):
            self._blocks.add((R4, LATE_PRESS_BLOCK_REASON))
            clock = None if self._is_late_press_blocked(called_for, threat_level) else called_for
        return self._finish_first(situation, clock)

    def _finish_first(self, situation: Situation, clock: tuple[str, str] | None) -> tuple[str, str] | None:
        """With finish_over_preserve on, R6 before the clock's R2 and R3: FINISH while R6 applies, or while a FINISH in
        force has stopped applying for less than clock_switch_ticks; else the clock's rule, R4 and None as they are."""
        rules = self._tuning.plan_rules
        if not (rules.finish_over_preserve and clock is not None and clock[1] in FINISH_FIRST_RULES):
            return clock
        if self._rule == R6 and self._has_finished_within(situation.tick, rules.clock_switch_ticks):
            return FINISH, R6
        return self._rested_finish(situation) or clock

    def _has_finished_within(self, tick: int, ticks: int) -> bool:
        """Whether R6's conditions held at some tick of the last `ticks` before `tick`, that one included."""
        return self._finish_tick is not None and tick - self._finish_tick < ticks

    def _clock_in_force(self, situation: Situation, called_for: tuple[str, str] | None) -> tuple[str, str] | None:
        """R2 to R4, with hysteresis: a clock rule in force stays until the balance has stood on the other side."""
        if self._rule not in CLOCK_RULES or (called_for is not None and called_for[1] == self._rule):
            return called_for
        return called_for if self._has_turned(situation.tick) else (self._plan, self._rule)

    def _is_late_press_blocked(self, clock: tuple[str, str] | None, threat_level: str) -> bool:
        """Whether the guard holds back the clock's R4: it is on, and base defense reports a major threat."""
        is_late_press = clock is not None and clock[1] == R4
        return is_late_press and self._tuning.plan_rules.late_press_guard and threat_level == MAJOR_THREAT

    def _has_turned(self, tick: int) -> bool:
        """Whether the balance has stood long enough on the other side of zero from the clock rule in force.

        The other side of R2's lead is a deficit, of R4's deficit a lead, and of R3's even balance either.
        """
        others = {R2: {BEHIND}, R4: {AHEAD}, R3: {AHEAD, BEHIND}}[self._rule]
        has_lasted = tick - self._balance_side_tick >= self._tuning.plan_rules.clock_switch_ticks
        return self._balance_side in others and has_lasted

    def _is_held(self, situation: Situation) -> bool:
        """Whether the plan in force was changed to by R5, R6 or R7 too short a time ago to change again.

        The enemy base gone ends the hold: what is left is to find what remains of the enemy.
        """
        is_recent = situation.tick - self._changed_tick < self._tuning.plan_rules.plan_hold_ticks
        return self._rule in HELD_RULES and is_recent and not situation.is_enemy_base_destroyed

    def _strike_or_finish(self, situation: Situation) -> tuple[str, str] | None:
        """R5 unless the early-attack guard holds it back, else R6 unless FINISH was left a moment ago; None when
        neither applies."""
        rules = self._tuning.plan_rules
        if self._strikes(situation):
            if not rules.early_attack_guard:
                return STRIKE, R5
            self._blocks.add((R5, EARLY_ATTACK_BLOCK_REASON))
        return self._rested_finish(situation)

    def _rested_finish(self, situation: Situation) -> tuple[str, str] | None:
        """R6 unless FINISH was left a moment ago; None when it does not apply."""
        rules = self._tuning.plan_rules
        has_rested = (
            self._left_finish_tick is None or situation.tick - self._left_finish_tick >= rules.finish_rest_ticks
        )
        return (FINISH, R6) if has_rested and _finishes(situation, rules) else None

    def _strikes(self, situation: Situation) -> bool:
        """Whether R5 applies: never once a STRIKE was given up, and started only in the first minutes."""
        rules = self._tuning.plan_rules
        is_under_way = self._plan == STRIKE
        if self._is_strike_excluded or not (is_under_way or situation.game_seconds <= rules.strike_until_seconds):
            return False
        return (
            situation.base_seen
            and situation.enemy_defenses == 0
            and situation.enemy_combat_value <= rules.strike_max_enemy_value
            and situation.main_strength >= rules.strike_min_strength
        )

    def _must_abandon(self, situation: Situation, world: World, ctx: MatchContext) -> bool:
        """Whether a STRIKE must be given up: a defense is remembered, or main's fight has turned against it."""
        if situation.enemy_defenses > 0:
            return True
        main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
        found = estimate(main, world, self._tuning) if main else None
        return found is not None and found.ratio < self._tuning.plan_rules.strike_abandon_ratio

    def _discretionary(
        self, situation: Situation, world: World, ctx: MatchContext, choice: ChoiceResult | None, is_held: bool
    ) -> tuple[str, str]:
        """R7: the sparring pin, else a fresh answer, else the plan held, else the chosen plan, else the script's.

        A plan a model's answer changed to is held as the module's note says, even when it is no longer open.
        """
        eligible = self._eligible = self._open_plans(situation)
        # A pinned seat plays its plan whenever it is open: nobody is asked, and no answer or request replaces it.
        pinned = self._seat_plans.get(ctx.seat) if ctx.match_kind == TESTING_CONTEXT else None
        if pinned in eligible:
            return pinned, SPARRING
        is_model_held = self._keeps_model_plan(situation, ctx.threat_level)
        answer = self._answer_and_ask(choice, eligible, situation, world, ctx, is_model_held)
        if answer is not None and not self._is_answer_held(situation.tick):
            self._chosen = answer
            if self._requested not in eligible:
                ctx.model_history.note_plan(answer, world)
            return answer, MODEL
        if is_held or is_model_held:
            return self._plan, self._rule
        return self._chosen_or_default(situation, ctx.threat_level, eligible)

    def _keeps_model_plan(self, situation: Situation, threat_level: str) -> bool:
        """Whether the plan in force is one a model's answer changed to, held, and nothing ends its hold in R7."""
        is_in_force = self._model_hold.is_in_force(situation.tick)
        return is_in_force and model_hold_breach(self._plan, situation, threat_level) is None

    def _answer_and_ask(
        self,
        choice: ChoiceResult | None,
        eligible: list[str],
        situation: Situation,
        world: World,
        ctx: MatchContext,
        is_model_held: bool,
    ) -> str | None:
        """The answer of the cycle that may be taken (see `_answer`), judged by when its question was asked, before a
        new question is put; none is put about a plan a model's answer changed to while it is held.

        With `model_plan_hold` on, no question is put and no answer taken under a major threat: an answer that arrives
        then is kept as discarded, for the record.
        """
        if self._tuning.plan_rules.model_plan_hold and ctx.threat_level == MAJOR_THREAT:
            self._discarded = _used_option(choice)
            return None
        answer = self._answer(choice, eligible, situation.tick)
        if not is_model_held:
            self._ask(eligible, situation, world, ctx)
        return answer

    def _open_plans(self, situation: Situation) -> list[str]:
        """The discretionary plans open now; a PRESS its own rules open that the early gate keeps shut is a block."""
        rules = self._tuning.plan_rules
        is_press_in_force = self._plan == PRESS
        if _press_opens(situation, rules) and _is_early_press_gated(situation, rules, is_press_in_force):
            self._blocks.add((R7, EARLY_ATTACK_BLOCK_REASON))
        return open_plans(situation, rules, is_press_in_force)

    def _is_answer_held(self, tick: int) -> bool:
        """Whether a plan a decision source chose was set too short a time ago for its next answer to change it: one
        its answer changed the plan to, while it is held (`ModelHold`), else answer_hold_ticks."""
        if self._model_hold.is_in_force(tick):
            return True
        return self._rule == MODEL and tick - self._chosen_tick < self._tuning.plan_rules.answer_hold_ticks

    def _chosen_or_default(self, situation: Situation, threat_level: str, eligible: list[str]) -> tuple[str, str]:
        """The plan a decision source chose while it is open, else the script's choice among those open.

        A major threat ends the chosen plan as it ends the hold: the army is needed at home, and the table holds.
        """
        if self._chosen in eligible and threat_level != MAJOR_THREAT:
            return self._chosen, MODEL
        self._chosen = None
        return script_choice(situation, threat_level, eligible, self._tuning.plan_rules), R7

    def _answer(self, choice: ChoiceResult | None, eligible: list[str], tick: int) -> str | None:
        """The plan a shorthand line or a decision source chose, when it may be taken; None when there is none."""
        if self._requested in eligible:
            return self._requested
        if choice is None or self._asked_tick is None:
            return None
        is_fresh = tick - self._asked_tick <= self._tuning.loop.choice_max_age_ticks
        return choice.option_id if choice.outcome == USED and choice.option_id in eligible and is_fresh else None

    def _ask(self, eligible: list[str], situation: Situation, world: World, ctx: MatchContext) -> None:
        """Ask the decision source which of the open plans to play, when it may choose and was not asked lately."""
        rules, tick = self._tuning.plan_rules, world.tick
        is_due = self._asked_tick is None or tick - self._asked_tick >= rules.choice_every_ticks
        if len(eligible) < 2 or not is_due:
            return
        request = self._question(eligible, situation, world, ctx)
        if request is not None and ctx.chooser.submit(request):
            self._asked_tick = tick

    def _question(
        self, eligible: list[str], situation: Situation, world: World, ctx: MatchContext
    ) -> ChoiceRequest | None:
        """The plan question of the cycle; None when no decision source may choose.

        It is the compact question when there is one. Else it is the prompt template with the briefing as its context,
        answered with a reason first, when there is one; else with the compact summary.
        """
        deadline_ms, state_tag = self._tuning.plan_rules.choice_deadline_ms, str(world.tick)
        if self._compact is not None:
            return self._compact.request(world, ctx, situation, eligible, deadline_ms, state_tag)
        if self._prompt is None:
            return None
        briefing = self._briefing
        if briefing is None:
            summary = summarize(world, ctx.scout_report, ctx.plan, ctx.groups, ctx.threat_level)
            context = "\n".join([summary, *ctx.model_history.plan_lines(world)])
        else:
            context = briefing.write(world, ctx, situation, eligible)
        is_briefed = briefing is not None
        return ChoiceRequest(
            question=self._prompt,
            options=[Option(plan, PLAN_DESCRIPTIONS[plan]) for plan in eligible],
            context=context,
            deadline_ms=deadline_ms,
            state_tag=state_tag,
            reason_first=is_briefed,
            reason_words=BRIEFED_REASON_WORDS,
            max_answer_tokens=BRIEFED_ANSWER_TOKENS if is_briefed else None,
        )


def _used_option(choice: ChoiceResult | None) -> str | None:
    """The option a decision source's answer named, when the answer was used; None for no answer or one not used."""
    return choice.option_id if choice is not None and choice.outcome == USED else None


def _clock_rule(situation: Situation, rules: PlanRulesTuning) -> tuple[str, str] | None:
    """R2 to R4: the plan the clock and the trade balance call for late in the match; None when none applies."""
    balance = situation.trade_balance
    if situation.is_late(rules.clock_seconds) and balance > 0:
        return PRESERVE, R2
    if situation.is_late(rules.clock_seconds) and balance == 0:
        return HOLD, R3
    if situation.is_late(rules.press_late_seconds) and balance < 0:
        return PRESS, R4
    return None


def _finishes(situation: Situation, rules: PlanRulesTuning) -> bool:
    """Whether R6 applies: (a) an army well above the enemy's late in the match, or (b) an enemy with no army left."""
    return _overwhelms(situation, rules) or _cleans_up(situation, rules)


def _overwhelms(situation: Situation, rules: PlanRulesTuning) -> bool:
    """R6(a): from the eighth minute, the base seen, no deficit, and an army far above the enemy's and its defenses.

    Main has to hold enough tanks and long-range units to break the defenses: infantry alone does not end a match.
    A main worth nothing (empty) puts no condition on the army's make-up.
    """
    army = _own_army(situation.army_value, situation.combat_army_value, rules)
    weighed_enemy = situation.enemy_combat_value + rules.finish_defense_weight * situation.defense_cost
    is_strong = army >= rules.finish_min_army and army >= rules.finish_army_ratio * weighed_enemy
    is_due = situation.game_seconds >= rules.finish_from_seconds and situation.base_seen
    is_heavy = situation.main_strength == 0 or situation.main_heavy_share >= rules.finish_heavy_share
    return is_due and is_strong and is_heavy and situation.trade_balance >= 0


def _cleans_up(situation: Situation, rules: PlanRulesTuning) -> bool:
    """R6(b): from the sixth minute, enemy buildings known and nothing known that defends them, and main to do it."""
    is_undefended = (
        situation.enemy_units == 0
        and situation.enemy_defenses == 0
        and situation.has_seen_no_enemy_unit_for(rules.cleanup_unseen_ticks)
    )
    is_due = situation.game_seconds >= rules.cleanup_from_seconds and situation.enemy_buildings > 0
    return is_due and is_undefended and situation.main_strength >= rules.cleanup_min_strength


def open_plans(situation: Situation, rules: PlanRulesTuning, is_press_in_force: bool = False) -> list[str]:
    """The discretionary plans open in a situation: HOLD always, RAID and PRESS when their conditions hold.

    PRESS's are its own rules' and, under the early-attack guard, the early gate's, which a PRESS in force passes.
    """
    eligible = [HOLD]
    if situation.can_raid and situation.raid_target_known and situation.raid_launch_blocker is None:
        eligible.append(RAID)
    if _press_opens(situation, rules) and not _is_early_press_gated(situation, rules, is_press_in_force):
        eligible.append(PRESS)
    return eligible


def _press_opens(situation: Situation, rules: PlanRulesTuning) -> bool:
    """Whether PRESS's own rules open it: the army well ahead, or main sent to find a base nobody looks for."""
    return _is_ahead(situation, rules) or _is_lost(situation, rules)


def _is_early_press_gated(situation: Situation, rules: PlanRulesTuning, is_press_in_force: bool) -> bool:
    """Whether the early-attack guard keeps PRESS shut: before early_press_until_seconds, the enemy not committed.

    The gate only keeps PRESS from being opened; a PRESS in force, whatever rule set it, is not ended by it.
    """
    is_early = situation.game_seconds < rules.early_press_until_seconds
    return rules.early_attack_guard and is_early and not situation.enemy_committed and not is_press_in_force


def _is_ahead(situation: Situation, rules: PlanRulesTuning) -> bool:
    """PRESS's ahead rule: from the sixth minute, an army of 3,000 and well above the enemy's as it is weighed."""
    return not _ahead_shortfalls(situation, rules)


def _ahead_shortfalls(situation: Situation, rules: PlanRulesTuning) -> list[str]:
    """The thresholds of PRESS's ahead rule a situation does not meet, in this order: the time, the army's value, its
    ratio to the enemy's as weighed; none when the rule holds."""
    army = weighed_army(situation, rules)
    checks = (
        (PRESS_TOO_EARLY, situation.game_seconds >= rules.press_from_seconds),
        (ARMY_TOO_SMALL, army >= rules.press_min_army),
        (ARMY_NOT_AHEAD, army >= rules.press_army_ratio * weighed_enemy(situation, rules)),
    )
    return [shortfall for shortfall, holds in checks if not holds]


def press_blockers(situation: Situation, rules: PlanRulesTuning, is_press_in_force: bool) -> list[str]:
    """What keeps PRESS shut in a situation: the early-attack gate, then, unless PRESS's own rules open it, each
    threshold of its ahead rule not met; none while PRESS is open."""
    gate = [EARLY_ATTACK_BLOCK_REASON] if _is_early_press_gated(situation, rules, is_press_in_force) else []
    return gate if _press_opens(situation, rules) else gate + _ahead_shortfalls(situation, rules)


def closed_reasons(
    situation: Situation, world: World, ctx: MatchContext, tuning: Tuning, is_press_in_force: bool
) -> dict[str, list[str]]:
    """For RAID and PRESS, what keeps each shut now, in the order the rules check it; a plan that is open has none.

    RAID's are `raid_blockers` and then, with [raids] launchable_only, why a raid could not set out now, or without it,
    no target a raid may be sent at; PRESS's are `press_blockers`. HOLD is always open.
    """
    raid = raid_blockers(world, ctx, tuning) + _raid_shut(situation)
    return {RAID: raid, PRESS: press_blockers(situation, tuning.plan_rules, is_press_in_force)}


def _raid_shut(situation: Situation) -> list[str]:
    """What keeps RAID shut besides `raid_blockers`: a raid that could not set out now, else no raid target known."""
    if situation.raid_launch_blocker is not None:
        return [RAID_NOT_LAUNCHABLE]
    return [] if situation.raid_target_known else [NO_RAID_TARGET]


def _is_lost(situation: Situation, rules: PlanRulesTuning) -> bool:
    """With the guard off: the enemy base still unknown and nobody looking for it, so main goes to find it.

    With the guard on an unseen base is the scouts' to find, and main does not go forward on it blind.
    """
    return (
        not rules.blind_press_guard
        and situation.game_seconds >= rules.press_unseen_base_seconds
        and not situation.base_seen
        and not situation.scout_active
    )


def script_choice(situation: Situation, threat_level: str, eligible: list[str], rules: PlanRulesTuning) -> str:
    """The script's choice among the open discretionary plans, by the decision table: the first rule that applies.

    HOLD under a major threat or when weaker than the enemy; PRESS, when it is open, for an army clearly stronger (or,
    with the guard off, an enemy base still unknown, or for a big main the enemy army is weighed from); RAID when it is
    open; else HOLD, which is always open.
    """
    if threat_level == MAJOR_THREAT or situation.ratio < rules.default_weaker_ratio:
        return HOLD
    is_base_unknown = not rules.blind_press_guard and not situation.base_seen
    is_stronger = situation.ratio >= rules.default_press_ratio or is_enemy_weighed_from_main(situation, rules)
    if PRESS in eligible and (is_stronger or is_base_unknown):
        return PRESS
    return RAID if RAID in eligible else HOLD


def _log_change(old: str | None, new: str, rule: str, situation: Situation, threat_level: str) -> None:
    """Write one `plan_change` event: the old plan (null at the start), the new, the rule, and the figures used."""
    inputs: dict[str, Any] = {**dataclasses.asdict(situation), "threat_level": threat_level}
    records.log(
        "plan_change",
        {
            "old": old,
            "new": new,
            "rule": rule,
            "tick": situation.tick,
            "game_seconds": situation.game_seconds,
            "inputs": inputs,
        },
    )


def _log_block(
    blocked_rule: str, reason: str, plan: str, rule: str, situation: Situation, threat_level: str, **detail: str
) -> None:
    """Write one `plan_rule_blocked` event: a rule held back and why, with the `detail` given (such as its cause), and
    the plan and rule in its place."""
    records.log(
        "plan_rule_blocked",
        {
            "rule": blocked_rule,
            "reason": reason,
            "plan": plan,
            "plan_rule": rule,
            "tick": situation.tick,
            "game_seconds": situation.game_seconds,
            "remaining_seconds": situation.remaining_seconds,
            "trade_balance": situation.trade_balance,
            "threat_level": threat_level,
            **detail,
        },
    )
