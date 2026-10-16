"""The model commander (`[redalert] model_commander`): one checked action a batch for a unit group, in its own slot.

A small model asked to choose the plan chose as the script would, and a model
asked where the scouts go chose no better than the script's own places. What
a model can add is judgment over a few concrete moves for one group of units,
asked again and again as the situation changes. With the switch on, the
commander does that every batch: it puts one question to the
`red_alert_commander` chain over a chooser of its own, apart from the plan's
and the scouts', and a checked action the answer names is carried out by the
script's own machinery until the next answer.

**Who it commands.** The first scout until main's own units (harvesters,
MCVs and the scouts are none of them; nor are the home guard's and the MCV's
escort) reach handover_combat_units of `[commander]`; then main for the rest
of the match. The handover is a `commander` event of kind `handover`. The
scout's question and actions are `command_scout`'s, main's `command_army`'s;
the manager that moves the units asks the question in every cycle it has
units to command.

**main's forward gate.** While it commands main, the commander keeps main's
forward gate (see `command_gate`), opened and closed on main's units in
every cycle before main's question is put, each change a `commander` event of
kind `gate`; main's question offers no forward action while it is closed.
While it is open, main's gather is told from main as a whole as the gate
weighs it, and from whether main lost more value than it killed in the last
`command_army.LOST_FIGHT_SECONDS` (see `command_army`).
While the gate is open, the units joining main are left out of its question
and of the commanded group (see `command_gate`). From the handover until the
first answer taken, main's standing action is
`gather`, at the staging point; and a forward action standing when the gate
closes, which main's question no longer offers, gives way to `gather`.

**The regroup gather.** When the gate closes because main's value fell below
regroup_value_share of its value when it opened, `gather` is made main's
standing action whatever it was, a `commander` event of kind `regroup` with
its letter and line, and the commander's order is due again. Until the gate
opens again, or an answer makes another kind of action main's standing one,
that gather is a retreat (`is_regrouping`): every unit of main is moved to
the staging point, those in contact with the enemy too, and none is given a
target (see `combat`). An answer arriving in the cycle the gate closes so is
not carried out, but for a raid trucks or task pick, which leaves the
standing action as it is: a `fallback` event of reason `regroup` after its
`decision` event says so, and it makes no pick. The units left of a main that lost half its value
deep in the enemy's ground otherwise stayed in their fights there under
focus fire. A gather the model picks while the gate is open, or the one main
stands on from the handover, is no retreat.

**The retreat override.** In the same cycles the commander follows main's
fight where it stands (see `command_retreat`). Once main has been losing it
long enough, the script pulls main to the staging point: `gather` is made the
standing action, sent in the commander's slot, the gate closes, and an answer
that arrives is not carried out, which a `fallback` event of reason
`override_retreat` after its `decision` event says, until the override lets
main go. An `override_retreat` and an `override_end` event mark both.

**Stepped moves.** With step_commit of `[commander]` and stepped_advance of
`[combat]` on, while main is on a stepped move (see `stepping`) an answer
naming the standing action, or an action whose cell lies within step_cells of
the move's destination, keeps the step plan: nothing changes. An answer naming
another action is held, which a `fallback` event of reason `step_commit` says,
and the newest held answer is taken in the cycle the step releases (or once
step_max_seconds have passed since it was first held), dropped as
`not_offered` when its action is no longer offered then. An answer is taken at
once when main's threat or forward gate is not as it was when the standing
action was taken, for a raid trucks or task pick, and while base defense
calls main home. When the retreat override takes main on a stepped advance it
steps main back one step instead (`steps_back`, see `combat`): the gate does
not close for it, the standing action stays, and main steps on once the
override lets go; its `override_retreat` event says `step_back` and the
`cell` main goes to.

**The armies crossed.** While main is committed to the base trade once the
armies crossed (`MatchContext.main_committed`, see `cross_commit`), the
retreat override does not follow main's fight and an answer for main is not
carried out, which a `fallback` event of reason `cross_commit` says: the
script commands main.

**The continue option.** With continue_option of `[commander]` on, the
commanded role's standing action is handed to its question's builder, which
offers it again as the continue option `Z` when the fresh list leaves it off
and it is still sound (see `command_prompt`, `command_army`,
`command_scout`). An answer naming Z names the standing action by its key and
changes nothing. The `summary` event then also counts the questions put with
a Z (`continue_offered`) and the used answers naming it (`continue_picked`).

**Asking.** Exactly one request is in flight at a time. The next is put in
the cycle after an answer arrives, used or not, so the model is asked about
once a batch; a source that does not answer within the request's deadline
(see `command_prompt`) ends the request with a timeout, which counts as an
answer. Each request and answer goes in the match record as a `decision`
event, as every chooser's does.

**Using the answer.** An answer is used in the cycle it arrives, or not at
all. It names an action by its letter in the question it answers; the action
must still be offered by that cycle's question (by its key: its kind and
target), and the answer must be at most answer_max_age_ticks old. Otherwise
it is dropped, a `commander` event of kind `dropped` with the reason, `stale`
or `not_offered` (an answer to the scout's question that arrives after the
handover is not offered either). An answer used makes the action the standing
action of the units commanded, a `commander` event of kind `action` with the
role, the letter, the action's line and the tick, and a pick the next
questions show in `last_picks`, up to history_size of them kept and
`[models] history_picks` shown; the pick keeps the plan rules' army ratio of
the match context's model history (see `model_history`). In every cycle the
standing action is the one of its key in that cycle's question, so its target
follows what it is aimed at. An answer naming `raid trucks` (see
`command_army`) or a task (`run over`, `kill`, `hit`; see `command_tasks`) is
used the same way, but it leaves the standing action as it was: it becomes
the task pick (`task`), from which the combat manager sends a detachment out
of main while the rest of main keeps the standing action.

**Keeping a pick.** A small model asked again and again turned the scout
back and forth between two places every few seconds. So an answer naming the
standing action the model picked changes nothing (but for the scout's hold
run out, which it takes anew), and an answer naming another action replaces a
standing action the model picked less than commit_seconds of `[commander]`
ago only when: its units have arrived, within `ARRIVED_CELLS` of its cell (an
action that sends them nowhere, a hold, is where it is at once; an attack
ends when its group is gone, no longer offered); the threat to main is not
what it was when the action was taken (none or any; for the scout, it must
flee a danger); the action is no longer offered; or main's forward gate
opened or closed since. Otherwise the answer is not carried out, which a `fallback`
event of reason `committed` after its `decision` event says, and it is
counted: when the match ends the commander writes a `commander` event of kind
`summary` with the match's `committed_answers`. The script's own standing
action, `gather`, gives way to the first answer at once, and an answer naming
it makes it the model's pick.

**The commander's order.** The standing action stands for one order, which the
commanded units' manager gives (see `scout_slots`, `combat`); the commander
sends it in the batch slot kept for it (see `batch.pack`) when it is due: the
action or the order's command changed since it was last sent, one of its
units stands idle (but for a stop, which leaves them idle), units came back
to main from a detachment (`resend`), or it was sent `RESEND_AFTER_TICKS`
ago. It is not sent again in every batch.

**The scout's commitment.** Live (watch41-m5-rush-1010, 2:15): the model
turned the first scout five ways in 22 seconds. With scout_commit of
`[commander]` on, a scout pick with a destination is kept
scout_commit_seconds rather than commit_seconds, on the same terms (until it
arrives, it must flee, or its mission is no longer offered); an answer
naming a back off is never held against it. An answer not carried out
within the longer time only is told by a `fallback` event of reason
`scout_commit`.

**The script fallback.** Live (sidestep, rush, 7:28 to 20:00): the model
gave no answer for twelve minutes, and main stood on the gather it last
picked, gate open and plan FINISH. With script_fallback of `[commander]` on,
once no answer of a model has been used for script_fallback_seconds (an
answer the chain gives for a question of one option is no model's), the
commander picks by script in every cycle until a model's answer is used
again: for main, the first offered action of the kinds the plan and the
threat call for (`_script_kinds`: under a major threat, retreat home, gather
or hold; under PRESS, STRIKE and FINISH, the attack on an enemy expansion,
the advance, the attack, the siege, the flank, the attack on the economy,
else gather; under any other plan, gather or hold), kept while it is offered
and no kind before it is; for the scout, none, so that its own missions steer
it. A pick of the script's is no model pick: the first model answer replaces
it. The start and end of the fallback are `commander` events of kind
`script_fallback`, each pick an `action` event with source `script`.

Nothing here reads a file, a clock or the network: the chooser does the
asking on a thread of its own.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from ucla_agent.core import records
from ucla_agent.core.decide import USED, journal
from ucla_agent.core.decide.choice import SINGLE_OPTION
from ucla_agent.games.redalert import cells, command_army, failed_attack, spending_focus
from ucla_agent.games.redalert.batch import Intent, Order, Priority
from ucla_agent.games.redalert.command_army import HOLD as HOLD_KIND
from ucla_agent.games.redalert.command_army import (
    ADVANCE,
    ATTACK,
    ECONOMY,
    EXPANSION,
    FLANK,
    FORWARD_KINDS,
    GATHER,
    RETREAT_HOME,
    SIEGE,
    LOST_FIGHT_SECONDS,
    RAID_TRUCKS,
    ArmyBrief,
    ArmyView,
    Continuing,
    EnemyPeak,
    Regroup,
)
from ucla_agent.games.redalert.command_tasks import TASK_KINDS
from ucla_agent.games.redalert.command_gate import REGROUP, ForwardGate, GateChange, measure
from ucla_agent.games.redalert.command_retreat import HOME_CELLS, TAKEN, RetreatOverride
from ucla_agent.games.redalert.command_prompt import (
    ARRIVED_CELLS,
    CONTINUE_ID,
    Action,
    Current,
    Pick,
    Question,
    Situation,
)
from ucla_agent.games.redalert.command_scout import BACK_OFF, HOLD, is_held
from ucla_agent.games.redalert.command_state import ClusterLabels
from ucla_agent.games.redalert.command_threat import ClosingWatch
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.composition import FINISH, PRESS, STRIKE
from ucla_agent.games.redalert.managers import MAJOR_THREAT, Chooser, MatchContext
from ucla_agent.games.redalert.model_history import ModelHistory
from ucla_agent.games.redalert.stepping import ADVANCE as STEPPED_ADVANCE
from ucla_agent.games.redalert.stepping import StepStatus
from ucla_agent.games.redalert.tuning import CommanderTuning
from ucla_agent.games.redalert.world import Cell, Unit, World

COMMANDER_EVENT = "commander"
# Who the commander commands: the first scout, then main.
SCOUT_ROLE = "scout"
MAIN_ROLE = "main"
# The kinds of a `commander` event, and why an answer is dropped.
HANDOVER = "handover"
ACTION = "action"
DROPPED = "dropped"
GATE = "gate"
OVERRIDE_RETREAT = "override_retreat"
# The actions a failed attack near their cell blocks (see `failed_attack`).
BLOCKABLE_KINDS = frozenset({ADVANCE, ATTACK, FLANK})
OVERRIDE_END = "override_end"
SUMMARY = "summary"
STALE = "stale"
NOT_OFFERED = "not_offered"
# Why an answer taken in is not carried out: the standing action is kept, or the script pulls main back, or main's
# stepped move waits for its step's release.
COMMITTED = "committed"
# Why an answer for the scout is not carried out: its pick with a destination is kept the scout's longer commitment.
SCOUT_COMMIT = "scout_commit"
STEP_COMMIT = "step_commit"
# Why an answer for main is not carried out while main is committed once the armies crossed.
CROSS_COMMIT = "cross_commit"
# The kinds of army action that send a detachment out of main rather than become its standing action.
DETACHING_KINDS = frozenset({RAID_TRUCKS, *TASK_KINDS})
# The commander's order is sent again once it is this old: about a second.
RESEND_AFTER_TICKS = 25
STOP = "stop"
SCRIPT_FALLBACK = "script_fallback"
SCRIPT = "script"
# The kinds of action the script picks main's from while the model is silent, the first offered first: under a major
# threat, under an attacking plan, and under any other plan.
SCRIPT_KINDS_THREAT = (RETREAT_HOME, GATHER, HOLD_KIND)
SCRIPT_KINDS_ATTACK = (EXPANSION, ADVANCE, ATTACK, SIEGE, FLANK, ECONOMY, GATHER)
SCRIPT_KINDS_HOME = (GATHER, HOLD_KIND)
ATTACKING_PLANS = frozenset({PRESS, STRIKE, FINISH})
# The class the commander's order is proposed under for each role; it goes in first whatever its class.
_ORDER_PRIORITY = {SCOUT_ROLE: Priority.SCOUT, MAIN_ROLE: Priority.MOVE}


@dataclass(frozen=True)
class _Asked:
    """The question in flight: whose units it is for, the tick it was asked at, and its actions by letter."""

    role: str
    tick: int
    actions: Mapping[str, Action]


@dataclass(frozen=True)
class _Arrived:
    """An answer that arrived in this cycle: the question it answers, the letter it names and the source that gave
    it."""

    asked: _Asked
    letter: str
    source: str
    # Whether the answer was held for a stepped move's release, which it is taken at however old.
    is_held: bool = False


@dataclass(frozen=True)
class TaskPick:
    """A raid trucks or task action the model picked for main, and the tick it was taken at."""

    action: Action
    tick: int


class Commander:
    """The model commander of one match. See the module's note."""

    def __init__(self, chooser: Chooser, tuning: CommanderTuning, names: Mapping[str, str] | None = None) -> None:
        """A commander asking `chooser`; `names` gives the plain name of a type, for main's question."""
        self._chooser = chooser
        self._tuning = tuning
        self.names: Mapping[str, str] = dict(names or {})
        self.role = SCOUT_ROLE
        # The most the enemy army has been seen worth, for main's question.
        self.peak = EnemyPeak()
        self.gate = ForwardGate()
        self.retreat = RetreatOverride()
        # The failed attacks and cells whose first block of an action was written (see `failed_attack`).
        self._blocks_logged: set[object] = set()
        # Where the enemy combat units in sight stood lately, for the enemy army closing on the base in main's question.
        self.closing = ClosingWatch()
        # The numbers of the clusters main's question names, kept from one question to the next (see `command_state`).
        self.labels = ClusterLabels()
        # The question in flight; None while none is.
        self._asked: _Asked | None = None
        # The answer that arrived in this cycle; None for none.
        self._arrived: _Arrived | None = None
        # The tick of the last cycle in which a request ended, with an answer or without.
        self._ended_tick: int | None = None
        self.standing: Action | None = None
        # The latest raid trucks or task action picked for main, kept apart from its standing action; None before the
        # first.
        self.task: TaskPick | None = None
        # The tick the standing action was last taken, and whether it was taken from an answer and the situation of
        # its units then; the script's own (gather) is kept against no answer.
        self.taken_tick = 0
        self._is_picked = False
        self._taken_situation: Situation | None = None
        # How many answers were not carried out in this match for the standing action they would have replaced, how
        # many questions offered the continue option, and how many answers used named it.
        self.committed_answers = 0
        self.continue_offered = 0
        self.continue_picked = 0
        # Whether main's standing gather is the regroup gather, and whether the gate closed for a regroup in this cycle,
        # so that gather is to be made main's standing action.
        self.is_regrouping = False
        self._regroup_due = False
        self._is_finalized = False
        self.picks: deque[Pick] = deque(maxlen=tuning.history_size)
        # The match's model history, whose army ratio each pick keeps; the match context's from the first cycle on.
        self._history = ModelHistory()
        # What the commander's order last sent was for: the standing action's key and the order's command, and when.
        self._sent_aim: tuple[Any, ...] | None = None
        self._sent_tick = 0
        # The value killed and lost by the match's tick, over the last LOST_FIGHT_SECONDS of main's cycles.
        self._trades: deque[tuple[int, int, int]] = deque()
        # The newest answer held for the release of main's step, and the tick the first held one was; None for none.
        self._held: _Arrived | None = None
        self._held_tick = 0
        # Whether the retreat override, taking main on a stepped advance, steps it back one step (see `combat`).
        self.steps_back = False
        # Whether main is committed to the base trade once the armies crossed, as of main's latest question.
        self.is_committed = False
        # The tick a model's answer was last used (None before the first cycle), whether the script picks while the
        # model is silent, and the kinds it picks main's action from in this cycle.
        self._heard_tick: int | None = None
        self.is_scripted = False
        self._script_kinds: tuple[str, ...] = SCRIPT_KINDS_HOME

    def begin_cycle(self, world: World, ctx: MatchContext) -> None:
        """Note what the enemy army is seen worth and where its units in sight stand, hand over to main once enough
        combat units stand, and take in the answer of the question in flight when it has arrived."""
        self.peak.note(ctx.scout_report.enemy_combat_value, world.tick)
        self._history = ctx.model_history
        self.closing.note(world)
        self._hand_over(world, ctx)
        self._arrived = None
        self._script_kinds = _script_kinds(ctx)
        result = self._chooser.poll(str(world.tick))
        if result is not None:
            asked, self._asked, self._ended_tick = self._asked, None, world.tick
            if result.outcome == USED and asked is not None and result.option_id in asked.actions:
                self._arrived = _Arrived(asked, result.option_id, result.source)
            if result.outcome == USED and result.reason != SINGLE_OPTION:
                self._heard_tick = world.tick
        self._follow_silence(world)

    def _follow_silence(self, world: World) -> None:
        """Start the script fallback once no model's answer has been used for script_fallback_seconds, and end it in
        the cycle one is, each a `script_fallback` event."""
        if self._heard_tick is None:
            self._heard_tick = world.tick
        quiet = world.tick - self._heard_tick
        is_silent = self._tuning.script_fallback and quiet >= self._tuning.script_fallback_seconds * TICKS_PER_SECOND
        if is_silent != self.is_scripted:
            self.is_scripted = is_silent
            _log(SCRIPT_FALLBACK, phase="start" if is_silent else "end", role=self.role, tick=world.tick)

    def _hand_over(self, world: World, ctx: MatchContext) -> None:
        """Command main from now on, with a `handover` event, once main's own units reach handover_combat_units."""
        if self.role == MAIN_ROLE:
            return
        combat_units = len(ctx.groups.roster(MAIN))
        if combat_units < self._tuning.handover_combat_units:
            return
        self.role, self.standing, self._sent_aim, self._is_picked = MAIN_ROLE, None, None, False
        self.picks.clear()
        _log(HANDOVER, role=MAIN_ROLE, tick=world.tick, combat_units=combat_units)

    def take(
        self,
        role: str,
        offered: Sequence[Action],
        world: World,
        situation: Situation | None = None,
        steps: StepStatus | None = None,
        is_recalled: bool = False,
    ) -> Action | None:
        """The standing action of a role's units in this cycle, `offered` being the actions of the role's question of
        the cycle and `situation` that of its units (none known without it): the standing action made the one of its
        key on offer, and the answer that arrived in the cycle taken when it is fresh, its action still offered and the
        standing action not kept against it; `steps` main's stepped move (None for none) and `is_recalled` whether base
        defense calls main home, for the answers held for a step's release. None while the role is not commanded or
        has no standing action."""
        if role != self.role:
            return None
        if self.is_scripted and role == SCOUT_ROLE:
            # The scout's own missions steer it while the model is silent.
            self.standing = None
            return None
        self.standing = next((action for action in offered if self._is_standing(action)), self.standing)
        self._release_held(steps, world)
        if self._arrived is not None:
            self._take_arrived(offered, world, situation, steps, is_recalled)
        if role == MAIN_ROLE:
            if self.is_scripted:
                self._script_pick(offered, world)
            self._gather_unless_picked(offered, world)
        return self.standing

    def _script_pick(self, offered: Sequence[Action], world: World) -> None:
        """Make main's standing action the script's pick while the model is silent: the first offered of the kinds of
        this cycle (see the module's note), the standing action kept while it is offered and of a kind no lower; an
        `action` event with source `script` for each change."""
        rank = {kind: place for place, kind in enumerate(self._script_kinds)}
        candidates = [action for action in offered if action.kind in rank]
        if not candidates:
            return
        best = min(candidates, key=lambda action: rank[action.kind])
        standing = self.standing
        is_offered = standing is not None and any(action.key == standing.key for action in offered)
        if is_offered and standing.kind in rank and rank[standing.kind] <= rank[best.kind]:
            return
        self.standing, self.taken_tick, self._is_picked = best, world.tick, False
        self.is_regrouping = self.is_regrouping and best.kind == GATHER
        _log(ACTION, role=MAIN_ROLE, action_id=best.id, action=best.head, tick=world.tick, source=SCRIPT)

    def _gather_unless_picked(self, offered: Sequence[Action], world: World) -> None:
        """Make main's standing action `gather` until the first answer is taken, while the script pulls main out of its
        fight, and in place of a forward action the closed gate no longer offers; and whatever it was as the regroup
        gather in the cycle the gate closed for a regroup, with a `regroup` event."""
        gather = next((action for action in offered if action.kind == GATHER), None)
        if gather is None:
            return
        if self._regroup_due:
            self._regroup_due, self.is_regrouping = False, True
            self.standing, self.taken_tick, self._is_picked = gather, world.tick, False
            self.resend()
            _log(REGROUP, role=MAIN_ROLE, action_id=gather.id, action=gather.head, tick=world.tick)
        elif self._wants_gather(offered):
            self.standing, self.taken_tick, self._is_picked = gather, world.tick, False

    def _wants_gather(self, offered: Sequence[Action]) -> bool:
        """Whether main's standing action is to give way to gather: there is none, the script pulls main back, or it
        is a forward action the closed gate no longer offers."""
        standing = self.standing
        if standing is not None and self.retreat.is_active and self.steps_back:
            return False
        if standing is None or self.retreat.is_active:
            return standing is None or standing.kind != GATHER
        is_offered = any(action.key == standing.key for action in offered)
        return standing.kind in FORWARD_KINDS and not self.gate.is_open and not is_offered

    def command_main(
        self,
        units: Sequence[Unit],
        next_leg: Cell | None,
        world: World,
        ctx: MatchContext,
        detached: frozenset[int] = frozenset(),
        may_detach: bool = True,
        targeted: frozenset[int] = frozenset(),
        steps: StepStatus | None = None,
    ) -> Action | None:
        """While the commander commands main: the forward gate opened or closed on main's units of this cycle, main's
        question put about them (`next_leg` the cell main is on its way to by the legs of its standing action, if any;
        `detached` main's units out on a detachment, which are main's once it ends; no task or raid trucks offered
        without `may_detach`, nor on an enemy of `targeted`, which a detachment is on; `steps` main's stepped move,
        None for none) and the answer that arrived taken; the action main carries out in this cycle. None while main is
        not commanded."""
        if self.role != MAIN_ROLE:
            return None
        self.is_committed = ctx.main_committed
        commanded = self.gate.split(units, world.tick, detached)
        found = command_army.brief(commanded, self.names, self.peak, world, ctx, self.closing, next_leg, self.labels)
        self._follow_retreat(found, world, ctx, steps)
        estimate = found.estimate
        joining = self.gate.joining_near() if self.gate.is_open else []
        counted = self._counted(joining, detached, world)
        enemy = self._gate_estimate(estimate.value, estimate.is_assumed, world, ctx)
        measured = measure(commanded, enemy, estimate.is_assumed, world, ctx, counted)
        is_pulled_back = self.retreat.is_active and not self.steps_back
        self._follow_gate(self.gate.follow(measured, is_pulled_back, self._tuning), world)
        regroup = Regroup(measured.value, self._lost_lately(world), self.retreat.is_active)
        is_open = self.gate.is_open
        continuing = self._continuing(MAIN_ROLE)
        actions = command_army.army_actions(
            found, is_open, self.names, world, ctx, may_detach, targeted, regroup, continuing
        )
        actions = self._unblocked(actions, units, world, ctx)
        situation = Situation(found.view.center, found.danger.is_threatened, self.gate.is_open)
        executed = self.take(MAIN_ROLE, actions, world, situation, steps, ctx.groups.is_recalled(MAIN))
        if self.is_due(MAIN_ROLE, world):
            # Only a question that is put is written: main's, with the whole state, is the dearest part of a cycle.
            current = self.current(found.view.center)
            self.ask(MAIN_ROLE, command_army.asked(found, actions, self.picks, self.names, world, ctx, current), world)
        return executed

    def _unblocked(
        self, actions: Sequence[Action], units: Sequence[Unit], world: World, ctx: MatchContext
    ) -> tuple[Action, ...]:
        """main's actions but an advance, attack or flank on a cell a failed attack blocks, and an advance on an
        objective main weighs too little against (see `failed_attack`), each first block written."""
        kept = []
        for action in actions:
            blocked = None
            if action.kind in BLOCKABLE_KINDS and action.cell is not None:
                side = failed_attack.FLANK if action.kind == FLANK else failed_attack.DIRECT
                blocked = failed_attack.blocking(action.cell, units, side, world, ctx, action.kind == ADVANCE)
            if blocked is None:
                kept.append(action)
            else:
                failed_attack.note_blocked(blocked, world, action.kind, self._blocks_logged)
        return tuple(kept)

    def _gate_estimate(self, value: int, is_assumed: bool, world: World, ctx: MatchContext) -> int:
        """The enemy estimate the forward gate weighs main against: with gate_capped_estimate on, a seen estimate held
        to what the military recovery holds it to (`spending_focus.recovery_estimate`: at most
        recovery_estimate_seen_share times the enemy combat units seen within recovery_seen_seconds), so that a peak
        grown on unseen income never keeps the gate closed against an army that matches what was seen."""
        if not self._tuning.gate_capped_estimate or is_assumed:
            return value
        held = spending_focus.recovery_estimate(world, ctx)
        return value if held is None else min(value, round(held))

    def _counted(self, joining: Sequence[Unit], away: frozenset[int], world: World) -> list[Unit]:
        """The units the forward gate weighs with main's on hand: those joining it, and with gate_counts_away on its
        units `away` on a job of their own, which are still main's: a detachment going out is no value lost."""
        if not self._tuning.gate_counts_away:
            return list(joining)
        return [*joining, *(world.combat_units[unit_id] for unit_id in sorted(away) if unit_id in world.combat_units)]

    def continued(self, role: str) -> Action | None:
        """The standing action a role's question may offer again as the continue option: with continue_option on, the
        commanded role's; None otherwise."""
        if not self._tuning.continue_option or role != self.role:
            return None
        return self.standing

    def continued_scout(self) -> Action | None:
        """The scout's standing action its question may offer again as the continue option (see `continued`)."""
        return self.continued(SCOUT_ROLE)

    def _continuing(self, role: str) -> Continuing | None:
        """What main's continue option is read from in this cycle (see `command_army`); None without one."""
        standing = self.continued(role)
        if standing is None:
            return None
        return Continuing(standing, self.retreat.is_active, self._regroup_due)

    def _lost_lately(self, world: World) -> bool:
        """Whether main lost a fight lately: our side lost more value than it killed over the last LOST_FIGHT_SECONDS
        of main's cycles."""
        military = world.military
        trades = self._trades
        trades.append((world.tick, military.kills_cost, military.deaths_cost))
        while world.tick - trades[0][0] > LOST_FIGHT_SECONDS * TICKS_PER_SECOND:
            trades.popleft()
        _, killed, lost = trades[0]
        return military.deaths_cost - lost > military.kills_cost - killed

    def _follow_gate(self, change: GateChange | None, world: World) -> None:
        """Write the `gate` event of the forward gate opened or closed in this cycle; a closing for a regroup makes the
        regroup gather due, and an opening ends it."""
        _log_gate(change, world)
        if change is None:
            return
        self._regroup_due = change.reason == REGROUP
        if change.is_open:
            self.is_regrouping = False

    def _follow_retreat(
        self, found: ArmyBrief, world: World, ctx: MatchContext, steps: StepStatus | None = None
    ) -> None:
        """Bring the retreat override up to date with main's fight of this cycle (see `command_retreat`), with an
        `override_retreat` event when it takes main and an `override_end` event when it lets main go. With step_commit
        on, the override taking main on a stepped advance steps it back one step (`steps_back`), which the event says
        with the cell main goes to."""
        if self.is_committed:
            return
        staging = cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells)
        is_home = cells.is_within(found.centroid, staging, HOME_CELLS)
        fight = self._override_fight(found, world, ctx)
        happened = self.retreat.follow(fight, is_home, world.tick, self._tuning)
        if happened == TAKEN:
            self.steps_back = self._commits_to(steps) and steps.kind == STEPPED_ADVANCE
            stepped = {}
            if self._tuning.step_commit:
                cell = steps.back_cell if self.steps_back else staging
                stepped = {"step_back": self.steps_back, "cell": None if cell is None else list(cell)}
            _log(OVERRIDE_RETREAT, ratio=round(fight, 2), tick=world.tick, **stepped)
        elif happened is not None:
            self.steps_back = False
            _log(OVERRIDE_END, reason=happened, tick=world.tick)

    def _override_fight(self, found: ArmyBrief, world: World, ctx: MatchContext) -> float | None:
        """main's fight the retreat override weighs: with override_counts_detachments on, `fight_here` of main's body
        with the units of every detachment out added to it, else the brief's. A hit detachment of most of main left
        the few units at the body weighed alone against the enemy the detachment was fighting: the override took main,
        which ended the detachment, and let go as soon as the whole of main stood together again."""
        if not self._tuning.override_counts_detachments or not ctx.detachments:
            return found.fight
        out = sorted({unit_id for detachment in ctx.detachments for unit_id in detachment.units})
        detached = [world.combat_units[unit_id] for unit_id in out if unit_id in world.combat_units]
        view = found.view
        weighed = ArmyView((*view.body, *detached), view.reinforcements, view.center)
        return command_army.fight_here(weighed, found.next_leg, world, ctx)

    def current(self, position: Cell) -> Current | None:
        """The standing action of the units commanded, standing at `position`, for the `current:` line of their
        question; None while there is none."""
        return None if self.standing is None else Current(self.standing, self.taken_tick, position)

    def _is_standing(self, action: Action) -> bool:
        return self.standing is not None and action.key == self.standing.key

    def _commits_to(self, steps: StepStatus | None) -> bool:
        """Whether main's stepped move holds the commander to its steps: step_commit on, main commanded, and a stepped
        move under way (only given with stepped_advance of [combat] on)."""
        return self._tuning.step_commit and self.role == MAIN_ROLE and steps is not None

    def _release_held(self, steps: StepStatus | None, world: World) -> None:
        """Take up the answer held for the release of main's step once it is released, the move is over or
        step_max_seconds have passed since the first was held; a newer answer that arrived in the cycle replaces it."""
        held = self._held
        if held is None:
            return
        is_due = steps is None or steps.releases or world.tick - self._held_tick >= steps.max_seconds * TICKS_PER_SECOND
        if not is_due:
            return
        self._held = None
        if self._arrived is None:
            self._arrived = held

    def _is_step_held(
        self,
        arrived: _Arrived,
        today: Action,
        steps: StepStatus | None,
        situation: Situation | None,
        is_recalled: bool,
        world: World,
    ) -> bool:
        """Whether an answer leaves main's stepped move as it is: one naming the standing action or a cell within
        step_cells of the move's destination keeps its plan, and another is held for the step's release (with a
        `fallback` event of reason step_commit) unless the step is released now, main's threat or gate has changed,
        the pick sends a detachment or base defense calls main home."""
        if not self._commits_to(steps) or arrived.is_held or today.kind in DETACHING_KINDS or is_recalled:
            return False
        if self._keeps_steps(today, steps):
            return True
        if steps.releases or (situation is not None and self._has_changed(situation)):
            return False
        if self._held is None:
            self._held_tick = world.tick
        self._held = replace(arrived, is_held=True)
        _not_carried_out(arrived.source, STEP_COMMIT)
        return True

    def _keeps_steps(self, today: Action, steps: StepStatus) -> bool:
        """Whether an answer names main's stepped move again: the standing action, or a cell within step_cells of the
        move's destination."""
        if self._is_standing(today):
            return True
        return today.cell is not None and cells.is_within(today.cell, steps.destination, steps.same_cells)

    def _take_arrived(
        self,
        offered: Sequence[Action],
        world: World,
        situation: Situation | None,
        steps: StepStatus | None = None,
        is_recalled: bool = False,
    ) -> None:
        """Make the action the answer of this cycle names the standing action, or the task pick for a raid trucks or
        task action, with an `action` event and a pick; or drop the answer, with a `dropped` event, when it is too old or its
        action is no longer offered. An answer naming the standing action the model picked changes nothing. The
        answer is not carried out while the script pulls main out of its fight, nor in the cycle the gate closes for a
        regroup, nor while the standing action is kept against it (see `_is_kept`), which a `fallback` event in the
        decision record says."""
        arrived, self._arrived = self._arrived, None
        asked, letter = arrived.asked, arrived.letter
        named = asked.actions[letter]
        today = next((action for action in offered if action.key == named.key), None)
        if self._is_refused(arrived, today, world):
            return
        if letter == CONTINUE_ID:
            self.continue_picked += 1
        if self._is_step_held(arrived, today, steps, situation, is_recalled, world):
            return
        if today.kind in DETACHING_KINDS:
            self.task = TaskPick(today, world.tick)
        elif self._is_picked and self._is_standing(today) and not self._is_over(world):
            return
        elif self._is_kept(offered, situation, world, arrived.is_held, today):
            self.committed_answers += 1
            _not_carried_out(arrived.source, self._kept_because(world))
            return
        else:
            self.standing, self.taken_tick, self._is_picked = today, world.tick, True
            self._taken_situation = situation
            self.is_regrouping = self.is_regrouping and today.kind == GATHER
        military = world.military
        ratio = self._history.ratio
        self.picks.append(Pick(letter, today.label, world.tick, military.kills_cost, military.deaths_cost, ratio))
        _log(ACTION, role=self.role, action_id=letter, action=today.head, tick=world.tick)

    def _is_refused(self, arrived: _Arrived, today: Action | None, world: World) -> bool:
        """Whether an answer is not used, with a `dropped` event (too old, or its action no longer offered; a held
        answer is never too old), or not carried out, with a `fallback` event (see `_withheld_because`)."""
        asked, letter = arrived.asked, arrived.letter
        age = 0 if arrived.is_held else world.tick - asked.tick
        reason = _dropped_because(asked, today, self.role, age, self._tuning)
        if reason is not None:
            named = asked.actions[letter]
            _log(DROPPED, role=asked.role, action_id=letter, action=named.head, tick=world.tick, reason=reason)
            return True
        withheld = self._withheld_because(today)
        if withheld is not None:
            _not_carried_out(arrived.source, withheld)
        return withheld is not None

    def _withheld_because(self, today: Action) -> str | None:
        """Why an answer whose action is still offered is not carried out at all: the script pulls main out of its
        fight, or main's gate closed for a regroup in this cycle, whose gather takes the standing action's place, and
        the answer is no raid trucks or task pick; None otherwise."""
        if self.role == MAIN_ROLE and self.is_committed:
            return CROSS_COMMIT
        if self.role == MAIN_ROLE and self.retreat.is_active:
            return OVERRIDE_RETREAT
        return REGROUP if self._regroup_due and today.kind not in DETACHING_KINDS else None

    def _is_over(self, world: World) -> bool:
        """Whether the standing action has run its course: the scout's hold, after its seconds."""
        standing = self.standing
        return standing.kind == HOLD and self.role == SCOUT_ROLE and not is_held(standing, self.taken_tick, world)

    def _is_kept(
        self,
        offered: Sequence[Action],
        situation: Situation | None,
        world: World,
        is_held: bool = False,
        named: Action | None = None,
    ) -> bool:
        """Whether the standing action is kept against an answer naming another (`named`): it was picked less than
        commit_seconds ago (a scout's with a destination, scout_commit_seconds with scout_commit on), and since then
        its units have not arrived, the threat has not changed (for main; for the scout, it need not flee), the action
        is still offered, and main's forward gate has neither opened nor closed; never against an answer held for a
        step's release (`is_held`), which waited long enough, nor against a scout's back off."""
        standing = self.standing
        if standing is None or not self._is_picked or is_held:
            return False
        if self.role == SCOUT_ROLE and named is not None and named.kind == BACK_OFF:
            return False
        is_young = world.tick - self.taken_tick < self._commit_seconds() * TICKS_PER_SECOND
        is_offered = any(action.key == standing.key for action in offered)
        if not is_young or not is_offered:
            return False
        return situation is None or not (_has_arrived(standing, situation) or self._has_changed(situation))

    def _commit_seconds(self) -> int:
        """How long the standing action picked is kept: scout_commit_seconds for a scout's with a destination with
        scout_commit on, commit_seconds otherwise."""
        tuning, standing = self._tuning, self.standing
        is_scout_trip = self.role == SCOUT_ROLE and standing is not None and standing.cell is not None
        if tuning.scout_commit and is_scout_trip:
            return max(tuning.commit_seconds, tuning.scout_commit_seconds)
        return tuning.commit_seconds

    def _kept_because(self, world: World) -> str:
        """Why an answer was not carried out: `scout_commit` while only the scout's longer commitment kept the
        standing action, `committed` otherwise."""
        is_old = world.tick - self.taken_tick >= self._tuning.commit_seconds * TICKS_PER_SECOND
        return SCOUT_COMMIT if is_old else COMMITTED

    def _has_changed(self, situation: Situation) -> bool:
        """Whether the situation of the commanded units releases their standing action: the scout must flee; main's
        threat, or its forward gate, is not as it was when the action was taken."""
        if self.role == SCOUT_ROLE:
            return situation.is_alarmed
        taken = self._taken_situation
        return taken is None or (situation.is_alarmed, situation.is_gate_open) != (taken.is_alarmed, taken.is_gate_open)

    def finalize(self) -> None:
        """Write the match's `summary` event of the commander: how many answers were committed. Only the first call
        writes."""
        if self._is_finalized:
            return
        self._is_finalized = True
        counts = {}
        if self._tuning.continue_option:
            counts = {"continue_offered": self.continue_offered, "continue_picked": self.continue_picked}
        _log(SUMMARY, committed_answers=self.committed_answers, **counts)

    def is_due(self, role: str, world: World) -> bool:
        """Whether a role's question is due in this cycle: the role is the one commanded, no question is in flight,
        and none ended in this cycle."""
        return role == self.role and self._asked is None and self._ended_tick != world.tick

    def ask(self, role: str, question: Question, world: World) -> None:
        """Put a role's question of this cycle to the chooser when one is due (see `is_due`)."""
        if not self.is_due(role, world):
            return
        if self._chooser.submit(question.request):
            self._asked = _Asked(role, world.tick, {action.id: action for action in question.actions})
            if any(action.id == CONTINUE_ID for action in question.actions):
                self.continue_offered += 1

    def resend(self) -> None:
        """Have the commander's order sent again when it is next given: units have rejoined the units commanded."""
        self._sent_aim = None

    def order(self, role: str, order: Order | None, world: World) -> Intent | None:
        """The commander's intent for the order the standing action of a role stands for, when it is due (see the
        module's note); None when it is not, and for no order."""
        if role != self.role or order is None or self.standing is None:
            return None
        aim = (self.standing.key, order["cmd"])
        # A stop leaves its units standing idle, which is what it is for: their idleness makes no stop due again.
        is_idle = order["cmd"] != STOP and any(world.units[unit].idle for unit in order["units"] if unit in world.units)
        if aim == self._sent_aim and not is_idle and world.tick - self._sent_tick < RESEND_AFTER_TICKS:
            return None
        self._sent_aim, self._sent_tick = aim, world.tick
        return Intent(_ORDER_PRIORITY[role], order, commander=True)


def _dropped_because(
    asked: _Asked, today: Action | None, role: str, age_ticks: int, tuning: CommanderTuning
) -> str | None:
    """Why an answer is not used: its question was for units no longer commanded or its action is no longer offered,
    or it is older than answer_max_age_ticks; None when it is used."""
    if asked.role != role or today is None:
        return NOT_OFFERED
    return STALE if age_ticks > tuning.answer_max_age_ticks else None


def _has_arrived(standing: Action, situation: Situation) -> bool:
    """Whether the commanded units have arrived where their standing action sends them: within ARRIVED_CELLS of its
    cell; an action that sends them nowhere, a hold, is where it is at once. An attack's target group gone leaves it
    no longer offered."""
    if standing.cell is None:
        return True
    return standing.kind != command_army.ATTACK and cells.is_within(situation.position, standing.cell, ARRIVED_CELLS)


def _script_kinds(ctx: MatchContext) -> tuple[str, ...]:
    """The kinds of action the script picks main's from while the model is silent, by the threat and the plan."""
    if ctx.threat_level == MAJOR_THREAT:
        return SCRIPT_KINDS_THREAT
    return SCRIPT_KINDS_ATTACK if ctx.plan in ATTACKING_PLANS else SCRIPT_KINDS_HOME


def _log(kind: str, **data: Any) -> None:
    """Write one `commander` event of a kind."""
    records.log(COMMANDER_EVENT, {"kind": kind, **data})


def _not_carried_out(source: str, reason: str) -> None:
    """Write the `fallback` event of an answer taken in but not carried out, as the decision record writes one for an
    answer not used: the script decided instead, for the reason given."""
    records.log(journal.FALLBACK_KIND, {"intended_source": source, "reason": reason, "used": journal.SCRIPT})


def _log_gate(change: GateChange | None, world: World) -> None:
    """Write a `gate` event for the forward gate opened or closed in this cycle, if it was."""
    if change is None:
        return
    measured = change.measure
    _log(
        GATE,
        open=change.is_open,
        value=round(measured.value),
        body_share=round(measured.body_share, 2),
        enemy_estimate=measured.enemy_estimate,
        assumed=measured.is_assumed,
        reason=change.reason,
        tick=world.tick,
    )
