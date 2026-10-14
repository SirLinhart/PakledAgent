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

Nothing here reads a file, a clock or the network: the chooser does the
asking on a thread of its own.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ucla_agent.core import records
from ucla_agent.core.decide import USED, journal
from ucla_agent.games.redalert import cells, command_army
from ucla_agent.games.redalert.batch import Intent, Order, Priority
from ucla_agent.games.redalert.command_army import (
    FORWARD_KINDS,
    GATHER,
    LOST_FIGHT_SECONDS,
    RAID_TRUCKS,
    ArmyBrief,
    EnemyPeak,
    Regroup,
)
from ucla_agent.games.redalert.command_tasks import TASK_KINDS
from ucla_agent.games.redalert.command_gate import REGROUP, ForwardGate, GateChange, measure
from ucla_agent.games.redalert.command_retreat import HOME_CELLS, TAKEN, RetreatOverride
from ucla_agent.games.redalert.command_prompt import Action, Current, Pick, Question, Situation
from ucla_agent.games.redalert.command_scout import HOLD, is_held
from ucla_agent.games.redalert.command_state import ClusterLabels
from ucla_agent.games.redalert.command_threat import ClosingWatch
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import Chooser, MatchContext
from ucla_agent.games.redalert.model_history import ModelHistory
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
OVERRIDE_END = "override_end"
SUMMARY = "summary"
STALE = "stale"
NOT_OFFERED = "not_offered"
# Why an answer taken in is not carried out: the standing action is kept, or the script pulls main back.
COMMITTED = "committed"
# The commanded units have arrived when they stand this near the cell of their standing action.
ARRIVED_CELLS = 3
# The kinds of army action that send a detachment out of main rather than become its standing action.
DETACHING_KINDS = frozenset({RAID_TRUCKS, *TASK_KINDS})
# The commander's order is sent again once it is this old: about a second.
RESEND_AFTER_TICKS = 25
STOP = "stop"
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
        # How many answers were not carried out in this match for the standing action they would have replaced.
        self.committed_answers = 0
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

    def begin_cycle(self, world: World, ctx: MatchContext) -> None:
        """Note what the enemy army is seen worth and where its units in sight stand, hand over to main once enough
        combat units stand, and take in the answer of the question in flight when it has arrived."""
        self.peak.note(ctx.scout_report.enemy_combat_value, world.tick)
        self._history = ctx.model_history
        self.closing.note(world)
        self._hand_over(world, ctx)
        self._arrived = None
        result = self._chooser.poll(str(world.tick))
        if result is None:
            return
        asked, self._asked, self._ended_tick = self._asked, None, world.tick
        if result.outcome == USED and asked is not None and result.option_id in asked.actions:
            self._arrived = _Arrived(asked, result.option_id, result.source)

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
        self, role: str, offered: Sequence[Action], world: World, situation: Situation | None = None
    ) -> Action | None:
        """The standing action of a role's units in this cycle, `offered` being the actions of the role's question of
        the cycle and `situation` that of its units (none known without it): the standing action made the one of its
        key on offer, and the answer that arrived in the cycle taken when it is fresh, its action still offered and the
        standing action not kept against it. None while the role is not commanded or has no standing action."""
        if role != self.role:
            return None
        self.standing = next((action for action in offered if self._is_standing(action)), self.standing)
        if self._arrived is not None:
            self._take_arrived(offered, world, situation)
        if role == MAIN_ROLE:
            self._gather_unless_picked(offered, world)
        return self.standing

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
    ) -> Action | None:
        """While the commander commands main: the forward gate opened or closed on main's units of this cycle, main's
        question put about them (`next_leg` the cell main is on its way to by the legs of its standing action, if any;
        `detached` main's units out on a detachment, which are main's once it ends; no task or raid trucks offered
        without `may_detach`, nor on an enemy of `targeted`, which a detachment is on) and the answer that arrived
        taken; the action main carries out in this cycle. None while main is not commanded."""
        if self.role != MAIN_ROLE:
            return None
        commanded = self.gate.split(units, world.tick, detached)
        found = command_army.brief(commanded, self.names, self.peak, world, ctx, self.closing, next_leg, self.labels)
        self._follow_retreat(found, world, ctx)
        estimate = found.estimate
        joining = self.gate.joining_near() if self.gate.is_open else []
        measured = measure(commanded, estimate.value, estimate.is_assumed, world, ctx, joining)
        self._follow_gate(self.gate.follow(measured, self.retreat.is_active, self._tuning), world)
        regroup = Regroup(measured.value, self._lost_lately(world), self.retreat.is_active)
        is_open = self.gate.is_open
        actions = command_army.army_actions(found, is_open, self.names, world, ctx, may_detach, targeted, regroup)
        situation = Situation(found.view.center, found.danger.is_threatened, self.gate.is_open)
        executed = self.take(MAIN_ROLE, actions, world, situation)
        if self.is_due(MAIN_ROLE, world):
            # Only a question that is put is written: main's, with the whole state, is the dearest part of a cycle.
            current = self.current(found.view.center)
            self.ask(MAIN_ROLE, command_army.asked(found, actions, self.picks, self.names, world, ctx, current), world)
        return executed

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

    def _follow_retreat(self, found: ArmyBrief, world: World, ctx: MatchContext) -> None:
        """Bring the retreat override up to date with main's fight of this cycle (see `command_retreat`), with an
        `override_retreat` event when it takes main and an `override_end` event when it lets main go."""
        staging = cells.staging_point(world, ctx.tuning.upkeep.staging_distance_cells)
        is_home = cells.is_within(found.centroid, staging, HOME_CELLS)
        happened = self.retreat.follow(found.fight, is_home, world.tick, self._tuning)
        if happened == TAKEN:
            _log(OVERRIDE_RETREAT, ratio=round(found.fight, 2), tick=world.tick)
        elif happened is not None:
            _log(OVERRIDE_END, reason=happened, tick=world.tick)

    def current(self, position: Cell) -> Current | None:
        """The standing action of the units commanded, standing at `position`, for the `current:` line of their
        question; None while there is none."""
        return None if self.standing is None else Current(self.standing, self.taken_tick, position)

    def _is_standing(self, action: Action) -> bool:
        return self.standing is not None and action.key == self.standing.key

    def _take_arrived(self, offered: Sequence[Action], world: World, situation: Situation | None) -> None:
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
        reason = _dropped_because(asked, today, self.role, world.tick - asked.tick, self._tuning)
        if reason is not None:
            _log(DROPPED, role=asked.role, action_id=letter, action=named.head, tick=world.tick, reason=reason)
            return
        withheld = self._withheld_because(today)
        if withheld is not None:
            _not_carried_out(arrived.source, withheld)
            return
        if today.kind in DETACHING_KINDS:
            self.task = TaskPick(today, world.tick)
        elif self._is_picked and self._is_standing(today) and not self._is_over(world):
            return
        elif self._is_kept(offered, situation, world):
            self.committed_answers += 1
            _not_carried_out(arrived.source, COMMITTED)
            return
        else:
            self.standing, self.taken_tick, self._is_picked = today, world.tick, True
            self._taken_situation = situation
            self.is_regrouping = self.is_regrouping and today.kind == GATHER
        military = world.military
        ratio = self._history.ratio
        self.picks.append(Pick(letter, today.label, world.tick, military.kills_cost, military.deaths_cost, ratio))
        _log(ACTION, role=self.role, action_id=letter, action=today.head, tick=world.tick)

    def _withheld_because(self, today: Action) -> str | None:
        """Why an answer whose action is still offered is not carried out at all: the script pulls main out of its
        fight, or main's gate closed for a regroup in this cycle, whose gather takes the standing action's place, and
        the answer is no raid trucks or task pick; None otherwise."""
        if self.role == MAIN_ROLE and self.retreat.is_active:
            return OVERRIDE_RETREAT
        return REGROUP if self._regroup_due and today.kind not in DETACHING_KINDS else None

    def _is_over(self, world: World) -> bool:
        """Whether the standing action has run its course: the scout's hold, after its seconds."""
        standing = self.standing
        return standing.kind == HOLD and self.role == SCOUT_ROLE and not is_held(standing, self.taken_tick, world)

    def _is_kept(self, offered: Sequence[Action], situation: Situation | None, world: World) -> bool:
        """Whether the standing action is kept against an answer naming another: it was picked less than commit_seconds
        ago, and since then its units have not arrived, the threat has not changed (for main; for the scout, it need
        not flee), the action is still offered, and main's forward gate has neither opened nor closed."""
        standing = self.standing
        if standing is None or not self._is_picked:
            return False
        is_young = world.tick - self.taken_tick < self._tuning.commit_seconds * TICKS_PER_SECOND
        is_offered = any(action.key == standing.key for action in offered)
        if not is_young or not is_offered:
            return False
        return situation is None or not (_has_arrived(standing, situation) or self._has_changed(situation))

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
        _log(SUMMARY, committed_answers=self.committed_answers)

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
