"""The model commander's question for main: the army, the enemy near it, the objective and the checked army actions.

Once own combat units reach handover_combat_units (see `commander`), the
commander commands main for the rest of the match. Its question is written as
`command_prompt` says, held to `MAX_PROMPT_CHARS` characters with its system
text (`SYSTEM_TEXT`: main's task, then the whole framing of `model_history`,
the tournament, the doctrine and the learning from recent picks;
`SHORT_SYSTEM_TEXT`, main's task and a sentence on pulling back in place of
the framing, should nothing else fit), in these lines, those that change
least first:

- the military state of `command_state`, nothing of the economy: `our
  bases:`, `enemy bases:`, `enemy forces:` (each item with its distance and
  direction from the body's center, the relations worked out in code and,
  for the enemy's, main's fight against it with the crush-adjusted ratio) and
  `our forces:` by what they do (main, reinforcing, staging, retreating,
  repairing, detached, raid, guard, scouts). The body is the units within
  `BODY_CELLS` of the unit with the most of main that near, so that a few
  units far behind do not move its center; the units joining main and the
  reinforcements waiting at the staging point (the units of the group main
  within `REINFORCEMENT_CELLS` of it that main's question is not about and
  that are not out on main's orders, `MatchContext.army`) are listed apart;
  an air-only defense (`items.is_air_only`) counts in none of the ground
  figures (`fight_here`, the targets, the objective's path);
- `enemy_total_est:` the most the enemy army has been seen worth in the match
  and how long ago (`seen peak`); before any was, the plan rules' assumed
  share of our own (`assumed`); then how much of it no enemy force listed
  accounts for (`unplaced`);
- `fight_here: we are <ratio>x`: the body weighed (see `fight.weigh`)
  against the enemy units that count in a fight within `ENEMY_NEAR_CELLS`
  of its center and every remembered enemy defense that fires at ground
  units (`fight.ground_defenses`; the AA gun and the SAM site never count)
  whose weapon range and 2 cells (`command_targets.reaches`) reach the
  center, or the cell of the next leg main is on its way to;
  `fight_here: no enemy` while there is neither. The `losing here` threat
  reads the same figure;
- `targets:` and a line for each enemy group and ground defense near the
  body, the nearest first, with our chance against it (see
  `command_targets`); or `targets: none`;
- `threat:` the first of `base attacked`, `enemy army closing at (x,y)` and
  `losing here` that holds, as `command_threat` finds them, or `none`;
- `objective:` the must-kill target main as a whole strikes, while there is
  one (`MatchContext.strike_target`, see `strike`); else among the
  remembered enemy buildings (else MCVs) the sticky
  FINISH target chooses among, the ones no remembered defense that fires at
  ground units covers (`coverage.is_covered` over `fight.ground_defenses`)
  first: of those the enemy's production first, then the nearest to the
  body's center. With every one covered, what the sticky FINISH target's
  choice would take now (`finish_target.choose`, which keeps nothing: the
  enemy's production first among the fresh sightings, else the one seen
  last), whatever the plan; the enemy's start while no enemy building or MCV
  is remembered. Then its distance, and whether the straight way there is
  clear or in the range of a remembered ground defense (the nearest such);
- `current:` main's standing action (see `command_prompt`);
- `last_picks:` the commander's last picks for main, the newest first, each
  with its age, the value the match killed and lost since and the army ratio
  then and now (see `command_prompt`);
- `score: lead <+n> (killed <n> lost <n>) t=<mm:ss> left=<mm:ss>
  plan=<plan>`: the value the match has killed less what it lost, the lead
  the tiebreak goes by, and the clock, the line that changes in every
  question, last;
- `ACTIONS:` and one line for each action, of at most 15 words besides the
  enemy types an attack or a hit names, then ` -> ` and its consequence
  (`consequence`): for an action on an enemy target `under 2 ground
  defenses` or `exposed target`; for one that takes main farther from a base
  of ours an enemy force closes on, `leaves B2 uncovered`; for a forward one
  while units are on their way to main, `reinforcements 10s behind`; for
  gather, retreat home and pull back the base of ours nearest where they go
  (`covers B1`) and the reinforcements waiting at the staging point; for
  hold, whether main stands under ground defenses.

The same question carries the military state and the lines after it as a
JSON object (`ChoiceRequest.state_object`), which a systemone source sends as
its state in place of the text.

The commander keeps main doing something useful. The defensive actions come
first, but only while a threat holds (see `command_threat`: the base
attacked, an enemy army closing on it, or main losing its fight where it
stands):

- `pull back` `PULL_BACK_CELLS` toward the staging point, where the
  reinforcements catch up, while the body stands farther than that from it;
- `retreat home` to the staging point, while main's centroid stands more
  than retreat_home_min_cells of `[commander]` from the base's center, its
  home;
- `hold`, a stop.

Then the active actions, in this order:

- `raid trucks` at the freshest raid opportunity scouting has noted (the
  first of `ctx.exposed_trucks`; see `exposed_trucks`) none of whose
  harvesters a detachment of the commander's is on already (see
  `command_detachments`): how many enemy harvesters were seen there, the cell
  of the one nearest our base, what their guard was worth and how long ago
  they were seen; while main holds a unit fit to raid (see `is_fit`);
- `attack` the nearest enemy group within `ATTACK_CELLS` of the centre: the
  enemy units that count in a fight within contact_cells of the nearest,
  weighed against the body as `fight.ratio_against` weighs them; never
  offered under `ATTACK_MIN_RATIO`. Its line names every type of the group,
  the most worth first; at the last detail level the first
  `SHORT_LIST_TYPES` and how many more (`+2 more`);
- `siege` the nearest remembered enemy defense, while main has artillery or
  V2 launchers, with its range, or `(air only)` for an air-only one;
- `flank` to the objective by the side waypoint (`FLANK_SIDE_CELLS` off the
  middle of the straight way) whose legs fewer remembered defenses cover,
  while the straight way is in a defense's range, or while fewer than
  `ACTIVE_MIN` active actions would be offered without it. With `waypoints`
  of [routing] on, the flank goes instead by the way the graph of waypoints
  gives to the objective (see `waypoints`), by way of its first cell and with
  its length, when there is one that passes a node short of the objective;
- `advance` to the objective. While the straight way is in a ground
  defense's range, with `waypoints` of [routing] on it goes by the way the
  graph of waypoints gives there, as the flank does, by way of its first
  cell (`via (x,y)`); without such a way it is left out while main has no
  artillery or V2 launcher, unless no other active action is offered.

With `flank_approach` of `[combat]` on, the script flanks (see
`attack_approach`): `flank` is not offered, and while the attack out goes by
a side cell the advance goes by it (`via (x,y)`) and is never left out for
its straight way; the combat manager sends main's standing advance, siege
and attack on a group farther than near_attack_cells by that cell as well
(`routed`).

The task options on the targets near main (see `command_tasks`): `run
over` a cluster of infantry with main's tanks, `kill` a defense with its
artillery or V2 launchers, `hit` a group with the units of main that counter
it, each naming its units and our chance, at most `command_tasks.TASKS_MAX`
of them. A hit is offered only while the body without the units it names
still reads `fight_here` of at least `REST_MIN_RATIO`, or `no enemy`, at
the center of what is left of it (`rest_holds`). An answer naming a hit is
taken only while the question of the cycle it arrives in offers one (see
`commander`): one the rest of main could no longer bear is dropped,
`not_offered`, and no hit detachment forms.

And `gather`, every unit of main to the staging point. While the gate is
open, the script not pulling main back and the enemy estimate seen, it is
told from main as a whole as the gate weighs it (`Regroup`): while main is
still worth the estimate or more, gather sends it to its own value-weighted
centroid, or to its body's center when that is nearer the objective, not to
the staging point; and while main is worth regroup_block_ratio of
`[commander]` of the estimate or more, has lost no fight lately (it lost more
value than it killed in the last `LOST_FIGHT_SECONDS`) and no major threat is
on the base, gather is not offered at all, main winning forward: a gather the
model picked then pulled a winning main home. At least `ACTIVE_MIN` actions
but the defensive ones are offered then, advance added when it is not, else
gather kept.

While the gate is open, `attack economy` the enemy refinery or harvester
group worth most per cell that no ground defense covers and that main's body
weighs at `ATTACK_MIN_RATIO` or more (see `command_economy`): its name, cell,
distance and value, and the body's ratio against the enemy units by it, or
`unguarded`. It is carried out as the attack is (its kind is `ATTACK`; its
letter and its place in the cut are its own, `ECONOMY`), and is held back as
every action on an enemy target is while main is behind.

`attack expansion` the enemy expansion main can beat (see `command_state`:
an enemy base other than its main with a construction yard, whose
crush-adjusted ratio is `command_state.BEATABLE_RATIO` or more), the highest
crush-adjusted ratio first, then the nearest: its label, the cell of its
construction yard, its distance, its buildings and the ratio. While the gate
is open it is offered on any such expansion, while it is closed only on one
within near_attack_cells of main's centroid; it is held back as every action
on an enemy target is while main is behind, and carried out as the attack is
(its letter and place in the cut its own, `EXPANSION`). An economy target in
that expansion is then not offered as well.

While main's forward gate is closed (see `command_gate`), none of the active
actions is offered but the attack, and the attack only on a group whose
centroid is within near_attack_cells of `[commander]` of main's centroid:
main is offered that attack, the task options on the targets as near, the
defensive actions while a threat holds, and `gather`.

While main is behind, its units (body and reinforcements, their cost times
their health) worth less than attack_min_army_share of `[commander]` of the
enemy estimate (`enemy_total_est`, the seen peak; an assumed estimate, a
share of our own army, never puts main behind, as it opens the forward gate
without its share test), none of the actions on an enemy target
(attack, advance, siege, flank, raid trucks and the task options) is offered
whose target lies more than `BEHIND_HOME_CELLS` from the base's center,
whether the gate is open or closed: a beaten main is offered the defensive
actions while a threat holds, gather, and fights near home only.

At most options_max of them are offered, kept in this order: the defensive
actions, at most all but `ACTIVE_MIN` of the places, then advance, the task
options (run over, hit, kill), attack, raid trucks, siege, flank and gather;
while the gate is closed, gather first of all but the defensive ones. Gather
is always kept: when the others would leave it out, it takes the last place,
so that at least one action calls main back. The expansion target, then the
economy target, are added after that cut: each in a place left, else in that
of the last active action kept (none of the defensive ones, nor gather, nor
the other one added). Each kind has its letter (`ARMY_IDS`): A pull back, B
retreat home, C hold, D gather, E siege, F flank, G raid trucks, H attack, I
advance, J run over, K kill, L hit, M attack economy, X attack expansion; the
actions kept are listed by their letters, the same in the text and in a
systemone server's criteria.

How each is carried out is the combat manager's (see `combat`). There is no
glossary and no recommendation of the script's.

Where main stands is read two ways. `fight_here`'s defense reach, the start
of a way through the graph of waypoints, the objective's ranking, the
distances the lines give and a pick's arrival (see `commander`) read the
body's center; the forward gate's body, the near attack while the gate is
closed, whether main is away from home for retreat home and the retreat
override's home read main's centroid, of all its units commanded. Once the
gate's body share holds (at least go_body_share of main within
gathered_cells of `[raids]` of its centroid) the two lie close together, so
main goes forward with both read alike.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any, Mapping, Sequence

from ucla_agent.games.redalert import attack_approach, cells, coverage, finish_target, raid_routes, safe_staging
from ucla_agent.games.redalert.command_economy import economy_target
from ucla_agent.games.redalert.command_state import (
    REINFORCING,
    Army,
    ClusterLabels,
    MilitaryState,
    enemy_bases,
    military_state,
)
from ucla_agent.games.redalert.command_prompt import (
    ARMY_DETAILS,
    ARRIVED_CELLS,
    CONTINUE,
    CONTINUE_ID,
    SHORT_LIST_TYPES,
    Action,
    Current,
    Detail,
    Pick,
    Question,
    at,
    by_value,
    clock,
    current_line,
    fitted,
    lettered,
    mm_ss,
    named,
    picks_line,
    seconds_ago,
    with_continue,
)
from ucla_agent.games.redalert.command_targets import LocalTarget, local_targets, range_cells, reaches, targets_lines
from ucla_agent.games.redalert.command_tasks import HIT_GROUP, KILL_DEFENSE, RUN_OVER, TASK_IDS, task_actions
from ucla_agent.games.redalert.command_threat import BASE_ATTACKED, LOSING_HERE, ClosingWatch, Threat, threat
from ucla_agent.games.redalert.fight import (
    Point,
    centroid,
    ground_defenses,
    is_counted,
    ratio_against,
    strength,
    weigh,
    worth,
)
from ucla_agent.games.redalert.groups import MAIN, SIEGE_TYPES
from ucla_agent.games.redalert.items import is_air_only
from ucla_agent.games.redalert.managers import MAJOR_THREAT, MatchContext
from ucla_agent.games.redalert.model_history import FRAMING
from ucla_agent.games.redalert.scout_missions import UNKNOWN_SPEED_CELLS_PER_SECOND, enemy_start
from ucla_agent.games.redalert.scout_route import distance_to_segment
from ucla_agent.games.redalert.waypoints import WaypointRoute
from ucla_agent.games.redalert.world import Cell, EnemyRecord, Unit, World

_TASK = (
    "You command our main army in Red Alert 1v1. Each turn pick ONE action ID. Keep the army busy when we are even or "
    "winning: attack, siege, advance, flank or raid."
)
_REPLY = 'Reply {"choice":"ID"}.'
# The doctrine's retreat and regroup when outnumbered says what the short text's last sentence says.
SYSTEM_TEXT = f"{_TASK} {FRAMING} {_REPLY}"
SHORT_SYSTEM_TEXT = f"{_TASK} When we are losing a fight, pull back or retreat to regroup. {_REPLY}"
MAX_PROMPT_CHARS = 6000
# The kinds of army action.
RAID_TRUCKS = "raid_trucks"
ATTACK = "attack"
SIEGE = "siege"
FLANK = "flank"
ADVANCE = "advance"
PULL_BACK = "pull_back"
RETREAT_HOME = "retreat_home"
HOLD = "hold"
GATHER = "gather"
# The kinds main's economy target and expansion target are offered as: attacks, each under a letter and a place in the
# cut of its own.
ECONOMY = "attack_economy"
EXPANSION = "attack_expansion"
# The actions that send main on toward the enemy.
FORWARD_KINDS = frozenset({ATTACK, SIEGE, FLANK, ADVANCE})
# The letter of each kind of army action.
ARMY_IDS = {
    PULL_BACK: "A",
    RETREAT_HOME: "B",
    HOLD: "C",
    GATHER: "D",
    SIEGE: "E",
    FLANK: "F",
    RAID_TRUCKS: "G",
    ATTACK: "H",
    ADVANCE: "I",
    **TASK_IDS,
    ECONOMY: "M",
    EXPANSION: "X",
    CONTINUE: CONTINUE_ID,
}
# The kinds of action that bring main back toward home; every other kind with a cell is on an enemy target.
HOME_KINDS = frozenset({GATHER, RETREAT_HOME, PULL_BACK})
# The order the actions but the defensive ones are kept in when more are offered than options_max.
KEPT_FIRST = (ADVANCE, RUN_OVER, HIT_GROUP, KILL_DEFENSE, ATTACK, RAID_TRUCKS, SIEGE, FLANK, GATHER)
# The kinds of standing action that may be offered again as the continue option when the fresh list left them off.
CONTINUED_KINDS = frozenset({ADVANCE, FLANK, SIEGE})
# The actions the continue option may take the place of, the lowest-ranked first: the economy and expansion targets,
# then the kinds kept in the cut from the last; never a defensive action nor gather.
GIVING_WAY = (ECONOMY, EXPANSION, *reversed([kind for kind in KEPT_FIRST if kind != GATHER]))
# How many places the defensive actions always leave to the others, and how many active actions the flank makes up
# to; while main is behind, fewer may be offered.
ACTIVE_MIN = 2
# A fight main lost more value in than it killed this long ago or less keeps gather offered while main wins forward.
LOST_FIGHT_SECONDS = 30
ATTACK_CELLS = 20
# While main is behind the enemy estimate, only the actions on a target within this many cells of the base's center
# are offered (see `_within_means`).
BEHIND_HOME_CELLS = 15
ATTACK_MIN_RATIO = 0.5
# A hit group is offered only while the rest of main's body reads at least this in its fight where it stands.
REST_MIN_RATIO = 1.0
ENEMY_NEAR_CELLS = 15
FLANK_SIDE_CELLS = 12
PULL_BACK_CELLS = 8
BODY_CELLS = 12
# The units of main's group within this many cells of the staging point that its question is not about are its
# reinforcements.
REINFORCEMENT_CELLS = 6
# A unit below this share of its health, in percent, is damaged.
DAMAGED_HP = 50
RATIO_DECIMALS = 1
RANGE_DECIMALS = 1
# What the objective is called while no enemy building or MCV is remembered.
ENEMY_START = "enemy start"
CLEAR = "clear"
NO_ENEMY = "no enemy"
# How a defense that fires at aircraft alone is marked, and how the economy target with no enemy unit by it reads.
AIR_ONLY = " (air only)"
UNGUARDED = "unguarded"
# How an action on an enemy target that no ground defense reaches ends, and how many clauses a consequence has at most.
EXPOSED_TARGET = "exposed target"
OUT_OF_REACH = "out of defense reach"
CONSEQUENCE_CLAUSES = 3


@dataclass(frozen=True)
class ArmyView:
    """main as its question sees it: its body, the units away from it, and the body's centre."""

    body: tuple[Unit, ...]
    reinforcements: tuple[Unit, ...]
    center: Cell


@dataclass(frozen=True)
class Objective:
    """Where main would go on the enemy: what it is called, its cell, and the nearest remembered defense whose range
    takes in the straight way there; None while that way is clear."""

    name: str
    cell: Cell
    covering: EnemyRecord | None


@dataclass
class EnemyPeak:
    """The most the enemy army has been seen worth in this match, and the tick it was; 0 and None before any was."""

    value: int = 0
    tick: int | None = None

    def note(self, value: int, tick: int) -> None:
        """Take in what the enemy army is seen worth at a tick."""
        if value > self.value:
            self.value, self.tick = value, tick


@dataclass(frozen=True)
class EnemyEstimate:
    """What the enemy army is taken to be worth: the most it has been seen worth and the tick that was; before any was
    seen, the plan rules' assumed share of our own army, with no tick."""

    value: int
    seen_tick: int | None

    @property
    def is_assumed(self) -> bool:
        """Whether the estimate is assumed, no enemy army having been seen yet."""
        return self.seen_tick is None


@dataclass(frozen=True)
class Regroup:
    """What main's gather is told from while the gate is open: main's value as a whole as the gate weighs it (see
    `command_gate`), whether main lost a fight in the last LOST_FIGHT_SECONDS, and whether the script pulls it back
    (see `command_retreat`)."""

    whole_value: float
    lost_lately: bool = False
    pulled_back: bool = False


@dataclass(frozen=True)
class Continuing:
    """What main's continue option is read from (see `army_actions`): the standing action, and whether the retreat
    override has main or a regroup is due in the cycle."""

    standing: Action
    is_pulled_back: bool = False
    is_regroup_due: bool = False


@dataclass(frozen=True)
class ArmyBrief:
    """What main's question is made of in a cycle: main as it sees it, main's centroid, the objective, main's fight
    where it stands (None for no enemy), the threat, the enemy estimate, the targets near the body (see
    `command_targets`), the cell of the next leg main is on its way to (None for none), the reinforcements waiting at
    the staging point, the closing watch, and the numbers of the military state's clusters (see `command_state`)."""

    view: ArmyView
    centroid: Cell
    goal: Objective
    fight: float | None
    danger: Threat
    estimate: EnemyEstimate
    targets: tuple[LocalTarget, ...] = ()
    next_leg: Cell | None = None
    waiting: tuple[Unit, ...] = ()
    # Where the enemy units in sight stood lately, for whether an enemy group moves; None for no such record.
    closing: ClosingWatch | None = None
    # The commander's cluster numbers, kept from question to question; numbered afresh for each brief without one.
    labels: ClusterLabels = field(default_factory=ClusterLabels)

    @property
    def army(self) -> Army:
        """main as the military state is told from it."""
        view = self.view
        return Army((*view.body, *view.reinforcements), view.body, view.center, view.reinforcements, self.waiting)


def question(
    main: Sequence[Unit],
    picks: Sequence[Pick],
    names: Mapping[str, str],
    peak: EnemyPeak,
    world: World,
    ctx: MatchContext,
    closing: ClosingWatch | None = None,
    next_leg: Cell | None = None,
    *,
    is_gate_open: bool = True,
) -> Question:
    """The commander's question for main's units in this cycle (see the module's note), main's forward gate open or
    not; `names` gives the plain name of a type, `closing` where the enemy units in sight stood lately (none known
    without it), and `next_leg` the cell main is on its way to by the legs of its standing action (none without).
    Asked once the map and both bases are known, with main holding a unit at least.

    Raises `ValueError` when it cannot be held to `MAX_PROMPT_CHARS`.
    """
    found = brief(main, names, peak, world, ctx, closing, next_leg)
    return asked(found, army_actions(found, is_gate_open, names, world, ctx), picks, names, world, ctx)


def brief(
    main: Sequence[Unit],
    names: Mapping[str, str],
    peak: EnemyPeak,
    world: World,
    ctx: MatchContext,
    closing: ClosingWatch | None = None,
    next_leg: Cell | None = None,
    labels: ClusterLabels | None = None,
) -> ArmyBrief:
    """What main's question is made of in this cycle (see `question`); `labels` the commander's cluster numbers, kept
    from question to question (numbered afresh without)."""
    view = army_view(main)
    ratio = fight_here(view, next_leg, world, ctx)
    return ArmyBrief(
        view,
        cells.nearest_cell(*centroid(main)),
        objective(view, names, world, ctx),
        ratio,
        threat(world, ctx.threat_active, closing or ClosingWatch(), ratio),
        enemy_estimate(peak, world, ctx),
        local_targets(view.body, view.center, world, ctx),
        next_leg,
        waiting_at_staging(main, world, ctx),
        closing,
        labels or ClusterLabels(),
    )


def waiting_at_staging(main: Sequence[Unit], world: World, ctx: MatchContext) -> tuple[Unit, ...]:
    """main's reinforcements: the units of the group main within REINFORCEMENT_CELLS of the staging point that are
    neither of `main`, the units the question is about, nor out on main's orders (`MatchContext.army`), nor in repair
    or withdrawn on their own, which its question lists apart."""
    staging = _staging(world, ctx)
    asked = {unit.id for unit in main} | ctx.army | ctx.repairing | ctx.withdrawn
    found = (world.combat_units.get(unit_id) for unit_id in ctx.groups.members(MAIN) if unit_id not in asked)
    return tuple(
        unit for unit in found if unit is not None and cells.is_within(staging, (unit.x, unit.y), REINFORCEMENT_CELLS)
    )


def asked(
    found: ArmyBrief,
    actions: Sequence[Action],
    picks: Sequence[Pick],
    names: Mapping[str, str],
    world: World,
    ctx: MatchContext,
    current: Current | None = None,
) -> Question:
    """main's question of a cycle, offering the actions given, each with its consequence, `current` main's standing
    action (none without); it carries the military state and the lines after it as a JSON object too. The cluster
    numbers it gives are kept for the next question.

    Raises `ValueError` when it cannot be held to `MAX_PROMPT_CHARS`.
    """
    state = military_state(found.army, names, world, ctx, found.closing, found.labels)
    # The continue option keeps the consequence it was offered with: none for a standing action offered as taken.
    told = tuple(
        (
            action
            if action.offered_as == CONTINUE
            else replace(action, consequence=consequence(action, found, state, world, ctx))
        )
        for action in actions
    )
    now = current_line(current, told, world)
    lines_at = partial(_lines, found, state, picks, now, names, world, ctx)
    told_object = state_object(found, state, picks, now, names, world, ctx)
    return fitted(
        SYSTEM_TEXT,
        lines_at,
        told,
        world.tick,
        MAX_PROMPT_CHARS,
        current,
        SHORT_SYSTEM_TEXT,
        details=ARMY_DETAILS,
        state_object=told_object,
    )


def enemy_estimate(peak: EnemyPeak, world: World, ctx: MatchContext) -> EnemyEstimate:
    """The most the enemy army has been seen worth; before any was, the assumed share of our army."""
    if peak.tick is not None:
        return EnemyEstimate(peak.value, peak.tick)
    own = sum(item.cost for item in (world.items.get(unit.type) for unit in world.combat_units.values()) if item)
    return EnemyEstimate(round(ctx.tuning.plan_rules.assumed_enemy_share * own), None)


def fight_here(view: ArmyView, next_leg: Cell | None, world: World, ctx: MatchContext) -> float | None:
    """The ratio of the body's strength to the enemy's where it stands: the enemy units that count in a fight within
    ENEMY_NEAR_CELLS of its center and the ground defenses that reach the center or `next_leg`; None, no enemy, while
    there is none of either or they are worth nothing."""
    hostile = _near_center(view, world)
    reaching = [
        defense
        for defense in ground_defenses(world)
        if reaches(defense, view.center, world, ctx)
        or (next_leg is not None and reaches(defense, next_leg, world, ctx))
    ]
    own, enemy = weigh(list(view.body), hostile, world, ctx.tuning, reaching)
    return own / enemy if enemy > 0 else None


def rest_holds(found: ArmyBrief, named: frozenset[int], world: World, ctx: MatchContext) -> bool:
    """Whether main's body without the units `named` holds its fight where it stands: `fight_here` of the rest of the
    body, at its own center (the body's while none of it is left), at least REST_MIN_RATIO, or no enemy."""
    view = found.view
    rest = tuple(unit for unit in view.body if unit.id not in named)
    center = cells.nearest_cell(*centroid(rest)) if rest else view.center
    ratio = fight_here(ArmyView(rest, view.reinforcements, center), found.next_leg, world, ctx)
    return ratio is None or ratio >= REST_MIN_RATIO


def _near_center(view: ArmyView, world: World) -> list[EnemyRecord]:
    """The remembered enemy units that count in a fight within ENEMY_NEAR_CELLS of the body's center."""
    return [record for record in _counted(world) if cells.is_within(view.center, _cell(record), ENEMY_NEAR_CELLS)]


def army_view(main: Sequence[Unit]) -> ArmyView:
    """main split into its body, the units within BODY_CELLS of the unit with the most of main that near (of two such,
    the lower id), and the rest."""
    anchor = max(main, key=lambda unit: (sum(_near(unit, other) for other in main), -unit.id))
    body = tuple(unit for unit in main if _near(anchor, unit))
    rest = tuple(unit for unit in main if not _near(anchor, unit))
    return ArmyView(body, rest, cells.nearest_cell(*centroid(body)))


def _near(unit: Unit, other: Unit) -> bool:
    return cells.is_within((unit.x, unit.y), (other.x, other.y), BODY_CELLS)


def objective(view: ArmyView, names: Mapping[str, str], world: World, ctx: MatchContext) -> Objective:
    """Where main would go on the enemy from the body's center (see the module's note), and the ground defense
    nearest the center whose range takes in the straight way there."""
    middle = centroid(view.body)
    record = ctx.strike_target or _uncovered_target(middle, world, ctx)
    record = record or finish_target.choose(middle, world, ctx.tuning.combat)
    name, cell = (ENEMY_START, enemy_start(world)) if record is None else (named(record.type, names), _cell(record))
    return Objective(name, cell, _covering(view.center, cell, world, ctx))


def _uncovered_target(middle: Point, world: World, ctx: MatchContext) -> EnemyRecord | None:
    """The target candidate no ground defense covers, the enemy's production first, then the nearest to `middle`;
    None while every candidate is covered."""
    defenses = ground_defenses(world)
    uncovered = [
        record
        for record in finish_target.candidates(world)
        if not coverage.is_covered(_cell(record), defenses, world.items, ctx.tuning.coverage)
    ]
    return min(
        uncovered,
        key=lambda record: (not finish_target.is_production(record), math.dist(middle, _cell(record))),
        default=None,
    )


def _covering(start: Cell, end: Cell, world: World, ctx: MatchContext) -> EnemyRecord | None:
    """The ground defense nearest `start` whose reach takes in the straight way to `end`; None for none."""
    covering = [record for record in ground_defenses(world) if _covers(record, start, end, world, ctx)]
    return min(covering, key=lambda record: cells.distance(start, _cell(record)), default=None)


def _covers(defense: EnemyRecord, start: Cell, end: Cell, world: World, ctx: MatchContext) -> bool:
    return distance_to_segment(_cell(defense), start, end) <= coverage.reach(
        defense.type, world.items, ctx.tuning.coverage
    )


def army_actions(
    found: ArmyBrief,
    is_gate_open: bool,
    names: Mapping[str, str],
    world: World,
    ctx: MatchContext,
    may_detach: bool = True,
    targeted: frozenset[int] = frozenset(),
    regroup: Regroup | None = None,
    continuing: Continuing | None = None,
) -> tuple[Action, ...]:
    """main's actions, lettered by kind: the defensive ones while a threat holds, the active ones, the task options
    (see `command_tasks`) and gather; while the forward gate is closed only the task options on the targets near main,
    a near attack and gather besides the defensive ones; no task option and no raid trucks without `may_detach`, while
    the commander's detachments are as many as may be out, nor on an enemy of `targeted`, which one of them is on; at
    most options_max of them, kept in the order of the module's note. While the gate is open, gather is told from
    `regroup`, main as a whole (none told without it): forward while main is ahead, left out while it wins. With
    `continuing`, the standing action the list leaves off is offered again as the continue option while it is still
    sound (see `_with_continue`)."""
    view, most = found.view, ctx.tuning.commander.options_max
    defensive = _defensive(found, world, ctx)[: max(0, most - ACTIVE_MIN)] if found.danger.is_threatened else []
    regroup = _while_open(regroup, is_gate_open)
    gather = _gather(found, regroup, world, ctx)
    units = [*view.body, *view.reinforcements]
    tasks = (
        task_actions(
            found.targets,
            units,
            found.centroid,
            is_gate_open,
            names,
            world,
            ctx,
            targeted,
            rest_holds=lambda named: rest_holds(found, named, world, ctx),
        )
        if may_detach
        else []
    )
    economy: list[Action] = []
    reach = ctx.tuning.commander.near_attack_cells
    expansion, expanded = _expansion(found, is_gate_open, names, world, ctx)
    expansion = _within_means(expansion, units, found.estimate, world, ctx)
    if is_gate_open:
        raid = _raid_trucks(view, world, ctx, targeted) if may_detach else None
        aimed = _within_means(
            [*_active_actions(view, found.goal, names, world, ctx, raid), *tasks], units, found.estimate, world, ctx
        )
        others = sorted([*aimed, gather], key=lambda action: KEPT_FIRST.index(action.kind))
        others, gather = _unless_winning(others, gather, regroup, found, names, ctx)
        economy = _within_means(_economy(view, names, world, ctx, targeted), units, found.estimate, world, ctx)
    else:
        attack = _attack(view, names, world, ctx)
        near = [attack] if attack is not None and cells.is_within(found.centroid, attack.cell, reach) else []
        others = [gather, *_within_means([*tasks, *near], units, found.estimate, world, ctx)]
    kept = _kept([*defensive, *others], gather, most)
    added = [*expansion, *_apart_from(economy, expanded if expansion else (), ctx)]
    listed = lettered(_with_added(kept, added, defensive, gather, most), ARMY_IDS)
    if continuing is None:
        return listed
    candidates = [*defensive, *others, *added]
    return _with_continue(listed, candidates, continuing, found, is_gate_open, world, ctx)


def _with_continue(
    listed: tuple[Action, ...],
    candidates: Sequence[Action],
    continuing: Continuing,
    found: ArmyBrief,
    is_gate_open: bool,
    world: World,
    ctx: MatchContext,
) -> tuple[Action, ...]:
    """The actions with main's standing action offered again as the continue option, when the list leaves it off and
    it is still sound (see `_may_continue`): the cycle's candidate of its key the options_max cut dropped, else the
    standing action as taken; an advance, flank or siege only. In a place left, else in that of the lowest-ranked of
    GIVING_WAY; not offered when none is listed."""
    standing = continuing.standing
    if standing.kind not in CONTINUED_KINDS or any(action.key == standing.key for action in listed):
        return listed
    option = next((action for action in candidates if action.key == standing.key), replace(standing, consequence=""))
    if not _may_continue(option, continuing, found, is_gate_open, world, ctx):
        return listed
    return with_continue(listed, option, giving_way(listed), ctx.tuning.commander.options_max)


def giving_way(actions: Sequence[Action]) -> list[Action]:
    """The actions main's continue option may take the place of, the lowest-ranked first (see GIVING_WAY): never a
    defensive action nor gather."""
    able = [action for action in actions if action.offered_as in GIVING_WAY]
    return sorted(able, key=lambda action: GIVING_WAY.index(action.offered_as))


def _may_continue(
    option: Action, continuing: Continuing, found: ArmyBrief, is_gate_open: bool, world: World, ctx: MatchContext
) -> bool:
    """Whether main's standing action is still sound to go on with: main has not arrived within ARRIVED_CELLS of its
    cell, its target is still remembered, main is neither attacked at home nor losing where it stands, the forward
    gate is open with no retreat override nor regroup due, and while main is behind its cell lies near home."""
    cell = option.cell
    if cell is None or cells.is_within(found.view.center, cell, ARRIVED_CELLS):
        return False
    if not _is_remembered(option, found, world):
        return False
    if found.danger.kind in (BASE_ATTACKED, LOSING_HERE):
        return False
    if not is_gate_open or continuing.is_pulled_back or continuing.is_regroup_due:
        return False
    units = [*found.view.body, *found.view.reinforcements]
    return bool(_within_means([option], units, found.estimate, world, ctx))


def _is_remembered(option: Action, found: ArmyBrief, world: World) -> bool:
    """Whether a continued action's target is still known: a siege's defense among the known enemy defenses with a
    long-range unit of main's left; an advance's or a flank's cell the enemy's start or that of a remembered enemy."""
    if option.kind == SIEGE:
        defense_ids = {record.id for record in world.known_enemy_defenses}
        return option.key[-1] in defense_ids and _has_long_range(found.view)
    remembered = [*world.enemy_memory.values(), *world.known_enemy_defenses]
    return option.cell == enemy_start(world) or any(_cell(record) == option.cell for record in remembered)


def _expansion(
    found: ArmyBrief, is_gate_open: bool, names: Mapping[str, str], world: World, ctx: MatchContext
) -> tuple[list[Action], tuple[Cell, ...]]:
    """Attack the enemy expansion main can beat (see `command_state.MilitaryState.expansions`), the highest
    crush-adjusted ratio first, then the nearest, at its construction yard; while the gate is closed only one within
    near_attack_cells of [commander] of main's centroid; none while there is none. With it, the cells of the
    expansion's buildings. The cluster numbers are read, not kept: the question keeps them."""
    bases = enemy_bases(found.army, names, world, ctx, found.labels, keep=False)
    reach = ctx.tuning.commander.near_attack_cells
    offered = [
        base
        for base in MilitaryState((), tuple(bases), (), ()).expansions()
        if is_gate_open or cells.is_within(found.centroid, base.yard, reach)
    ]
    best = next(iter(offered), None)
    if best is None:
        return [], ()
    cell, fight = best.yard, best.fight
    ratio = UNGUARDED if fight.crush_ratio is None else f"crush-adjusted {fight.crush_ratio:.{RATIO_DECIMALS}f}x"
    head = (
        f"attack enemy expansion {best.id} at {at(cell)}, {_cells(found.view.center, cell)} cells, "
        f"{len(best.buildings)} buildings value {best.building_value}, {ratio}"
    )
    attack = Action(
        kind=ATTACK, key=(EXPANSION,), label=f"attack expansion {at(cell)}", head=head, cell=cell, offer_kind=EXPANSION
    )
    return [attack], best.cells


def _apart_from(economy: list[Action], expanded: Sequence[Cell], ctx: MatchContext) -> list[Action]:
    """The economy target, unless it lies in the expansion offered, whose buildings stand at `expanded`: within
    cluster_cells of [commander] of one of them, the one base both would name."""
    reach = ctx.tuning.commander.cluster_cells
    return [
        action for action in economy if not any(cells.is_within(action.cell, building, reach) for building in expanded)
    ]


def _within_means(
    aimed: Sequence[Action], units: Sequence[Unit], estimate: EnemyEstimate, world: World, ctx: MatchContext
) -> list[Action]:
    """The actions on an enemy target that main may be offered: all of them while main's units are worth at least
    attack_min_army_share of [commander] of the enemy estimate, or while that estimate is assumed, being then a share
    of our own army and no measure of the enemy's; while they are worth less, main being behind, only those whose
    target lies within BEHIND_HOME_CELLS of the base's center, so that a beaten main fights near home only."""
    share = ctx.tuning.commander.attack_min_army_share
    if estimate.is_assumed or strength(units, world) >= share * estimate.value:
        return list(aimed)
    return [action for action in aimed if _is_near_home(action, world)]


def _is_near_home(action: Action, world: World) -> bool:
    """Whether an action's target lies within BEHIND_HOME_CELLS of the base's center."""
    return action.cell is not None and cells.is_within(action.cell, world.base_center, BEHIND_HOME_CELLS)


def _kept(listed: Sequence[Action], gather: Action | None, most: int) -> list[Action]:
    """The first `most` of the actions listed, gather always among them when it is offered: it takes the last place
    when they would leave it out, so that main can always be called back to regroup."""
    kept = list(listed[:most])
    return kept if gather is None or gather in kept else [*kept[: most - 1], gather]


def _while_open(regroup: Regroup | None, is_gate_open: bool) -> Regroup | None:
    """main as a whole as its gather is told from it: only while the gate is open and the script does not pull main
    back; None otherwise."""
    return regroup if is_gate_open and regroup is not None and not regroup.pulled_back else None


def _unless_winning(
    others: list[Action],
    gather: Action,
    regroup: Regroup | None,
    found: ArmyBrief,
    names: Mapping[str, str],
    ctx: MatchContext,
) -> tuple[list[Action], Action | None]:
    """The actions but the defensive ones and gather: gather left out (None) while main wins forward (see
    `_is_winning`, `_without_gather`), else as they were."""
    if regroup is None or not _is_winning(found, regroup, ctx):
        return others, gather
    return _without_gather(others, gather, _advance(found.view, found.goal, names, ctx))


def _is_winning(found: ArmyBrief, regroup: Regroup, ctx: MatchContext) -> bool:
    """Whether main wins forward, so that gather is not offered: the enemy estimate is seen and main as a whole is
    worth regroup_block_ratio of [commander] of it or more, main lost no fight lately, and no major threat is on the
    base."""
    estimate = found.estimate
    if estimate.is_assumed or regroup.lost_lately or ctx.threat_level == MAJOR_THREAT:
        return False
    return regroup.whole_value >= ctx.tuning.commander.regroup_block_ratio * estimate.value


def _without_gather(others: list[Action], gather: Action, advance: Action) -> tuple[list[Action], Action | None]:
    """The actions but the defensive ones without gather, and None for gather, while ACTIVE_MIN of them are left,
    advance added when they would be fewer; else as they were, gather kept."""
    left = [action for action in others if action is not gather]
    if len(left) < ACTIVE_MIN and all(action.key != advance.key for action in left):
        left = [advance, *left]
    return (left, None) if len(left) >= ACTIVE_MIN else (others, gather)


def _with_added(
    kept: list[Action], added: Sequence[Action], defensive: Sequence[Action], gather: Action | None, most: int
) -> list[Action]:
    """The actions kept and those added after the cut (the expansion target, then the economy target), each in turn:
    in a place left among `most`, else in the place of the last active action kept (neither defensive, gather nor one
    added); left out while no such action is kept."""
    placed = list(kept)
    for action in added:
        if len(placed) < most:
            placed.append(action)
            continue
        active = [
            index
            for index, other in enumerate(placed)
            if other is not gather and other not in defensive and other not in added
        ]
        if active:
            placed[active[-1]] = action
    return placed


def _defensive(found: ArmyBrief, world: World, ctx: MatchContext) -> list[Action]:
    """Pull back when it applies, retreat home while main is away from home, and hold."""
    view = found.view
    pull_back = _pull_back(view, world, ctx)
    retreat = _retreat_home(view, world, ctx) if _is_away_from_home(found.centroid, world, ctx) else None
    return [action for action in (pull_back, retreat, hold_action()) if action is not None]


def _is_away_from_home(centroid: Cell, world: World, ctx: MatchContext) -> bool:
    """Whether main's centroid stands more than retreat_home_min_cells of [commander] from the base's center, its
    home: from nearer, main is home already."""
    return not cells.is_within(centroid, world.base_center, ctx.tuning.commander.retreat_home_min_cells)


def _active(
    view: ArmyView, goal: Objective, names: Mapping[str, str], world: World, ctx: MatchContext, raid: Action | None
) -> list[Action]:
    """The raid trucks given (None for none), attack and siege when they apply, flank while the straight way is in a
    defense's range or the active actions would be fewer than ACTIVE_MIN without it, and the advance, last: left out on
    a defended straight way it cannot go around while main has no long-range unit and another active action is
    offered."""
    optional = (raid, _attack(view, names, world, ctx), _siege(view, names, world, ctx))
    found = [action for action in optional if action is not None]
    advance = _advance(view, goal, names, ctx)
    flank = _flank(view, goal, world, ctx)
    is_flank_wanted = goal.covering is not None or len(found) + 1 < ACTIVE_MIN
    others = [*found, *([flank] if flank is not None and is_flank_wanted else [])]
    is_withheld = goal.covering is not None and advance.via is None and not _has_long_range(view) and others
    return [*others, *([] if is_withheld else [advance])]


def _active_actions(
    view: ArmyView, goal: Objective, names: Mapping[str, str], world: World, ctx: MatchContext, raid: Action | None
) -> list[Action]:
    """The active actions (see `_active`); with flank_approach of `[combat]` on, where the script flanks by the attack's
    approach, the raid trucks given, attack and siege when they apply and the advance by the approach's side cell,
    no flank and the advance never left out for its straight way."""
    if not ctx.tuning.combat.flank_approach:
        return _active(view, goal, names, world, ctx, raid)
    optional = (raid, _attack(view, names, world, ctx), _siege(view, names, world, ctx))
    return [
        *(action for action in optional if action is not None),
        routed(_advance(view, goal, names, ctx), view.center, ctx),
    ]


def routed(action: Action | None, center: Cell, ctx: MatchContext) -> Action | None:
    """An action as the attack's approach routes it (see `attack_approach`): an advance, a siege, or an attack on a cell
    farther than near_attack_cells of `[commander]` from `center`, by the side cell main is still to pass while its
    cell is the attack's objective; any other action, or one with no side cell to go by, as it is."""
    if action is None or action.cell is None or action.kind not in (ADVANCE, SIEGE, ATTACK):
        return action
    if action.kind == ATTACK and cells.is_within(center, action.cell, ctx.tuning.commander.near_attack_cells):
        return action
    side = attack_approach.side_for(action.cell, ctx)
    if side is None or action.via == side:
        return action
    head = action.head
    if action.kind == ADVANCE:
        head = f"advance via {at(side)} to {action.label.removeprefix('advance to ')} {at(action.cell)}"
    return replace(action, via=side, route=None, head=head)


def _has_long_range(view: ArmyView) -> bool:
    """Whether main has artillery or V2 launchers, which fire on a defense from beyond its range."""
    return any(unit.type in SIEGE_TYPES for unit in (*view.body, *view.reinforcements))


def _raid_trucks(view: ArmyView, world: World, ctx: MatchContext, targeted: frozenset[int]) -> Action | None:
    """Raid the freshest exposed trucks none of which is of `targeted` at the cell of the harvester nearest our base;
    None while no such opportunity is noted or no unit of main is fit to raid."""
    free = (trucks for trucks in ctx.exposed_trucks if targeted.isdisjoint(trucks.harvester_ids))
    trucks = next(free, None)
    if trucks is None or not any(is_fit(unit) for unit in (*view.body, *view.reinforcements)):
        return None
    cell, seen = trucks.nearest_harvester, seconds_ago(trucks.seen_tick, world)
    return Action(
        kind=RAID_TRUCKS,
        key=(RAID_TRUCKS, trucks.field),
        label=f"raid trucks {at(cell)}",
        head=f"raid {trucks.harvester_count} trucks at {at(cell)}, guard {trucks.guard_value}, seen {seen}s ago",
        cell=cell,
        aim=trucks.harvester_ids,
    )


def is_fit(unit: Unit) -> bool:
    """Whether a unit of main is fit to raid: not damaged, at DAMAGED_HP of its health or more."""
    return unit.hp >= DAMAGED_HP


def _attack(view: ArmyView, names: Mapping[str, str], world: World, ctx: MatchContext) -> Action | None:
    """Attack the nearest enemy group within ATTACK_CELLS, every type of it named, with the fight's ratio; None for
    none, or one under ATTACK_MIN_RATIO."""
    near = sorted(
        (record for record in _counted(world) if cells.is_within(view.center, _cell(record), ATTACK_CELLS)),
        key=lambda record: cells.distance(view.center, _cell(record)),
    )
    if not near:
        return None
    reach = ctx.tuning.combat.contact_cells
    group = [record for record in near if cells.is_within(_cell(near[0]), _cell(record), reach)]
    ratio = ratio_against(list(view.body), group, world, ctx.tuning)
    if ratio < ATTACK_MIN_RATIO:
        return None
    cell = cells.nearest_cell(*centroid(group))
    distance = round(cells.distance(view.center, cell))
    valued = [(named(record.type, names), worth(record.type, record.hp, world)) for record in group]
    where = f"at {at(cell)}, {distance} cells, fight {ratio:.{RATIO_DECIMALS}f}x"
    return Action(
        kind=ATTACK,
        key=(ATTACK,),
        label=f"attack {at(cell)}",
        head=f"attack {by_value(valued)} {where}",
        cell=cell,
        short_head=f"attack {by_value(valued, SHORT_LIST_TYPES, with_rest=True)} {where}",
    )


def _economy(
    view: ArmyView, names: Mapping[str, str], world: World, ctx: MatchContext, targeted: frozenset[int]
) -> list[Action]:
    """Attack the economy target (see `command_economy`), an attack offered as ECONOMY; none while there is none."""
    found = economy_target(view.body, view.center, names, world, ctx, ATTACK_MIN_RATIO, targeted)
    if found is None:
        return []
    fight = f"fight {found.ratio:.{RATIO_DECIMALS}f}x" if found.is_guarded else UNGUARDED
    head = f"attack economy: {found.name} at {at(found.cell)}, {round(found.distance)} cells"
    return [
        Action(
            kind=ATTACK,
            key=(ECONOMY,),
            label=f"attack economy {at(found.cell)}",
            head=f"{head}, value {round(found.value)}, {fight}",
            cell=found.cell,
            offer_kind=ECONOMY,
        )
    ]


def _siege(view: ArmyView, names: Mapping[str, str], world: World, ctx: MatchContext) -> Action | None:
    """Siege the nearest remembered enemy defense, while main has a long-range unit; None otherwise."""
    if not _has_long_range(view):
        return None
    defense = min(
        world.known_enemy_defenses, key=lambda record: cells.distance(view.center, _cell(record)), default=None
    )
    if defense is None:
        return None
    name, cell = named(defense.type, names), _cell(defense)
    return Action(
        kind=SIEGE,
        key=(SIEGE, defense.id),
        label=f"siege {name} {at(cell)}",
        head=f"siege {name} {at(cell)}, {_cells(view.center, cell)} cells{_reach(defense, world, ctx, ', its ')}",
        cell=cell,
    )


def _advance(view: ArmyView, goal: Objective, names: Mapping[str, str], ctx: MatchContext) -> Action:
    """Advance to the objective by the straight way while it is clear; while it is in a ground defense's range, by
    the way through the graph of waypoints (`_waypoint_route`) when there is one, else by the straight way."""
    found = None if goal.covering is None else _waypoint_route(view, goal, ctx)
    if found is not None:
        via = found.legs[0]
        return Action(
            kind=ADVANCE,
            key=(ADVANCE, goal.cell),
            label=f"advance to {goal.name}",
            head=f"advance via {at(via)} to {goal.name} {at(goal.cell)}, {round(found.length)} cells",
            cell=goal.cell,
            via=via,
            route=found,
        )
    distance = _cells(view.center, goal.cell)
    return Action(
        kind=ADVANCE,
        key=(ADVANCE, goal.cell),
        label=f"advance to {goal.name}",
        head=f"advance to {goal.name} {at(goal.cell)}, {distance} cells, path {_path(goal, names)}",
        cell=goal.cell,
    )


def _flank(view: ArmyView, goal: Objective, world: World, ctx: MatchContext) -> Action | None:
    """Flank to the objective by the way through the graph of waypoints (`_waypoint_route`), else by the side waypoint
    whose legs fewer remembered defenses cover; None while the body stands on the objective, which has no side."""
    sides = raid_routes.side_waypoints(view.center, goal.cell, world.map_bounds, FLANK_SIDE_CELLS)
    if not sides:
        return None
    found = _waypoint_route(view, goal, ctx)
    if found is not None:
        via = found.legs[0]
        return Action(
            kind=FLANK,
            key=(FLANK, goal.cell),
            label=f"flank to {goal.name}",
            head=f"flank via {at(via)} to {goal.name} {at(goal.cell)}, {round(found.length)} cells",
            cell=goal.cell,
            via=via,
            route=found,
        )
    side = min(sides, key=lambda waypoint: _flank_rank(view.center, waypoint, goal.cell, world, ctx))
    length = _cells(view.center, side) + _cells(side, goal.cell)
    return Action(
        kind=FLANK,
        key=(FLANK, goal.cell),
        label=f"flank to {goal.name}",
        head=f"flank via {at(side)} to {goal.name} {at(goal.cell)}, {length} cells",
        cell=goal.cell,
        via=side,
    )


def _waypoint_route(view: ArmyView, goal: Objective, ctx: MatchContext) -> WaypointRoute | None:
    """With `waypoints` of [routing] on, the way the graph of waypoints gives from the body's center to the objective;
    None with the switch off, without such a way, or with one that passes no node short of the objective."""
    found = ctx.waypoints.route(view.center, goal.cell) if ctx.tuning.routing.waypoints else None
    return None if found is None or len(found.legs) < 2 else found


def _flank_rank(start: Cell, side: Cell, end: Cell, world: World, ctx: MatchContext) -> tuple[int, float]:
    """How a side waypoint ranks for a flank, the lowest first: how many remembered defenses cover either leg, then
    the length of the way."""
    covering = sum(
        _covers(defense, start, side, world, ctx) or _covers(defense, side, end, world, ctx)
        for defense in world.known_enemy_defenses
    )
    return covering, cells.distance(start, side) + cells.distance(side, end)


def _pull_back(view: ArmyView, world: World, ctx: MatchContext) -> Action | None:
    """Pull back PULL_BACK_CELLS toward the staging point, while the body stands farther than that from it."""
    staging = _staging(world, ctx)
    if cells.is_within(view.center, staging, PULL_BACK_CELLS):
        return None
    cell = cells.clamped(cells.toward(view.center, staging, PULL_BACK_CELLS), world.map_bounds)
    return Action(
        kind=PULL_BACK,
        key=(PULL_BACK,),
        label="pull back",
        head=f"pull back to {at(cell)}, {PULL_BACK_CELLS} cells toward home, reinforcements join",
        cell=cell,
    )


def _retreat_home(view: ArmyView, world: World, ctx: MatchContext) -> Action:
    staging = _staging(world, ctx)
    return Action(
        kind=RETREAT_HOME,
        key=(RETREAT_HOME,),
        label="retreat home",
        head=f"retreat home to {at(staging)}, {_cells(view.center, staging)} cells",
        cell=staging,
    )


def hold_action() -> Action:
    """Hold: a stop where the army stands."""
    return Action(kind=HOLD, key=(HOLD,), label="hold", head="hold: stop where the army stands")


def _gather(found: ArmyBrief, regroup: Regroup | None, world: World, ctx: MatchContext) -> Action:
    """Gather: every unit of main to the staging point; with main as a whole (`regroup`, the gate open) still worth a
    seen enemy estimate or more, to main's own place (see `_forward_cell`) instead."""
    view, estimate = found.view, found.estimate
    is_ahead = regroup is not None and not estimate.is_assumed and regroup.whole_value >= estimate.value
    if is_ahead:
        cell = _forward_cell(found, world)
        head = f"gather forward at {at(cell)}, {_cells(view.center, cell)} cells"
    else:
        cell = _staging(world, ctx)
        head = f"gather at staging {at(cell)}, {_cells(view.center, cell)} cells"
    return Action(kind=GATHER, key=(GATHER,), label="gather", head=head, cell=cell)


def _forward_cell(found: ArmyBrief, world: World) -> Cell:
    """Where main ahead gathers: the centroid of its units weighted by what each is worth, or its body's center when
    that is nearer the objective."""
    view = found.view
    units = (*view.body, *view.reinforcements)
    weights = [worth(unit.type, unit.hp, world) for unit in units]
    total = sum(weights)
    if total > 0:
        middle = cells.nearest_cell(
            sum(weight * unit.x for weight, unit in zip(weights, units)) / total,
            sum(weight * unit.y for weight, unit in zip(weights, units)) / total,
        )
    else:
        middle = found.centroid
    goal = found.goal.cell
    return view.center if cells.distance(view.center, goal) < cells.distance(middle, goal) else middle


def _staging(world: World, ctx: MatchContext) -> Cell:
    return safe_staging.staging_point(world, ctx.tuning, ctx)


def _lines(
    found: ArmyBrief,
    state: MilitaryState,
    picks: Sequence[Pick],
    current: str,
    names: Mapping[str, str],
    world: World,
    ctx: MatchContext,
    detail: Detail,
) -> list[str]:
    """The state lines of the question at a level of detail, in order, the empty ones left out: the military state's
    sections the level keeps, main's lines, the picks and the score with the clock."""
    lines = [
        *state.lines(detail),
        _enemy_total(found.estimate, state, world),
        _fight_here(found.fight),
        *targets_lines(found.targets, names, world, detail),
        found.danger.line,
        _objective(found.view, found.goal, names),
        current,
        picks_line(picks, detail, world, with_result=True, history=ctx.model_history),
        score_line(world, ctx),
    ]
    return [line for line in lines if line]


def score_line(world: World, ctx: MatchContext) -> str:
    """`score: lead +1450 (killed 9050 lost 7600) t=08:45 left=11:14 plan=HOLD`: the value killed less the value lost,
    the lead the tiebreak goes by, and the clock."""
    military = world.military
    lead = military.kills_cost - military.deaths_cost
    return (
        f"score: lead {lead:+d} (killed {military.kills_cost} lost {military.deaths_cost}) {clock(world)} "
        f"plan={ctx.plan}"
    )


def state_object(
    found: ArmyBrief,
    state: MilitaryState,
    picks: Sequence[Pick],
    current: str,
    names: Mapping[str, str],
    world: World,
    ctx: MatchContext,
) -> dict[str, Any]:
    """The question's state as a JSON object: the military state and the facts of the lines after it."""
    estimate, military, left = found.estimate, world.military, world.remaining_seconds
    every = picks_line(picks, ARMY_DETAILS[0], world, with_result=True, history=ctx.model_history)
    return {
        **state.as_json(),
        "enemy_estimate": {
            "value": estimate.value,
            "assumed": estimate.is_assumed,
            "seen_s": None if estimate.seen_tick is None else seconds_ago(estimate.seen_tick, world),
            "unplaced": _unplaced(estimate, state),
        },
        "fight_here": None if found.fight is None else round(found.fight, RATIO_DECIMALS),
        "targets": targets_lines(found.targets, names, world, ARMY_DETAILS[0])[1:],
        "threat": found.danger.line.removeprefix("threat: "),
        "objective": _objective(found.view, found.goal, names).removeprefix("objective: "),
        "current": current.removeprefix("current: "),
        "last_picks": None if every is None else every.removeprefix("last_picks: "),
        "score": {
            "lead": military.kills_cost - military.deaths_cost,
            "killed": military.kills_cost,
            "lost": military.deaths_cost,
            "t": mm_ss(world.game_seconds),
            "left": None if left is None else mm_ss(left),
            "plan": ctx.plan,
        },
    }


def consequence(action: Action, found: ArmyBrief, state: MilitaryState, world: World, ctx: MatchContext) -> str:
    """What taking an action leads to, worked out in code (see the module's note); never empty: hold says whether main
    stands under ground defenses, gather, retreat home and pull back the base they cover, every other action, each on
    an enemy target, whether ground defenses reach its cell."""
    kind, cell = action.offered_as, action.cell
    if cell is None:
        clauses = [f"holds {_under_defenses(found.view.center, world, ctx) or OUT_OF_REACH}"]
    elif kind in HOME_KINDS:
        clauses = [_nearest_base(cell, state), *([f"joins {len(found.waiting)} at staging"] if found.waiting else [])]
    else:
        clauses = [_under_defenses(cell, world, ctx) or EXPOSED_TARGET, *_left_open(cell, found, state)]
        clauses.extend(_behind(state))
    return ", ".join(clauses[:CONSEQUENCE_CLAUSES])


def _left_open(cell: Cell, found: ArmyBrief, state: MilitaryState) -> list[str]:
    """`leaves B2 uncovered` for each base of ours an enemy force closes on that an action's cell lies farther from
    than main's body center does."""
    center = found.view.center
    return [
        f"leaves {base.id} uncovered"
        for base in state.our_bases
        if base.closing and cells.distance(cell, base.place.pos) > cells.distance(center, base.place.pos)
    ]


def _behind(state: MilitaryState) -> list[str]:
    """`reinforcements 10s behind`: the seconds the slowest reinforcements need to reach main; none while none are on
    their way."""
    behind = [force.eta_s for force in state.our_forces if force.status == REINFORCING and force.eta_s]
    return [f"reinforcements {max(behind)}s behind"] if behind else []


def _under_defenses(cell: Cell, world: World, ctx: MatchContext) -> str:
    """`under 2 ground defenses` for the ground defenses whose weapon range and 2 cells reach a cell; empty for none."""
    count = sum(1 for defense in ground_defenses(world) if reaches(defense, cell, world, ctx))
    if not count:
        return ""
    return f"under {count} ground defense{'' if count == 1 else 's'}"


def _nearest_base(cell: Cell, state: MilitaryState) -> str:
    """`covers B1`: the base of ours nearest a cell; `regroups` while none stands."""
    nearest = min(state.our_bases, key=lambda base: (cells.distance(cell, base.place.pos), base.id), default=None)
    return "regroups" if nearest is None else f"covers {nearest.id}"


def _unplaced(estimate: EnemyEstimate, state: MilitaryState) -> int:
    """How much of the enemy estimate no enemy force listed accounts for, at least 0."""
    return max(0, estimate.value - sum(force.strength for force in state.enemy_forces))


def _enemy_total(estimate: EnemyEstimate, state: MilitaryState, world: World) -> str:
    """The enemy estimate: the most the enemy army was seen worth and how long ago, or assumed, and how much of it no
    enemy force listed accounts for."""
    unplaced = f", unplaced {_unplaced(estimate, state)}"
    if estimate.seen_tick is None:
        return f"enemy_total_est: {estimate.value} (assumed){unplaced}"
    return f"enemy_total_est: {estimate.value} (seen peak {seconds_ago(estimate.seen_tick, world)}s ago){unplaced}"


def _fight_here(ratio: float | None) -> str:
    return f"fight_here: {NO_ENEMY}" if ratio is None else f"fight_here: we are {ratio:.{RATIO_DECIMALS}f}x"


def _objective(view: ArmyView, goal: Objective, names: Mapping[str, str]) -> str:
    distance = _cells(view.center, goal.cell)
    return f"objective: {goal.name} {at(goal.cell)}, {distance} cells; direct path {_path(goal, names)}"


def _path(goal: Objective, names: Mapping[str, str]) -> str:
    """The straight way to the objective: clear, or in the range of the remembered defense nearest the body."""
    return CLEAR if goal.covering is None else f"in {named(goal.covering.type, names)} range"


def _counted(world: World) -> list[EnemyRecord]:
    """The remembered enemy units that count in a fight."""
    return [record for record in world.enemy_memory.values() if is_counted(record)]


def _reach(defense: EnemyRecord, world: World, ctx: MatchContext, lead: str) -> str:
    """How a line gives a defense's reach: `(air only)` for one that fires at aircraft alone, whose range does not
    matter to ground units; else its range after `lead`, ` range 6`."""
    if is_air_only(defense.type, world.items):
        return AIR_ONLY
    return f"{lead}range {_weapon_range(defense.type, world, ctx)}"


def _weapon_range(unit_type: str, world: World, ctx: MatchContext) -> str:
    return f"{round(range_cells(unit_type, world, ctx), RANGE_DECIMALS):g}"


def unit_speed(unit_type: str, world: World) -> float:
    """How many cells a second a type of unit goes: its item's speed, else the speed taken for an unknown one."""
    facts = world.items.get(unit_type)
    return facts.speed if facts is not None and facts.speed else UNKNOWN_SPEED_CELLS_PER_SECOND


def _cells(first: Cell, second: Cell) -> int:
    return round(cells.distance(first, second))


def _cell(actor: EnemyRecord | Unit) -> Cell:
    return actor.x, actor.y
