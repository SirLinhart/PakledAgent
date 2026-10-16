"""Red Alert decision-maker: the loop that turns each fresh state into one batch of orders.

The game runs at 25 ticks a second and waits for nobody, so the loop is a
script: it never calls a model, a network or a disk on its way to an answer.
Every call does the same things in the same order. It brings the world model
and the order ledger up to date with the new state, lets each manager propose
what it wants ordered, and fits what was proposed into one batch that the
platform will take, or returns `WAIT` when there is nothing to send.

What decides the match is in the managers; the loop only gives them their
turn, in a fixed order, and arbitrates between them. A tank on a crush run
(see `crush`) keeps its run move whatever another manager proposed for it. A
choice that a model may make is asked for through the chooser, which answers
on a later call or not at all, and the script's own choice stands until it
does.

The agent's settings are read once, when it is made: the item facts and the
thresholds under `config/redalert/`, and its `[redalert]` settings in
`config/agent.toml`. A file that cannot be read does not cost the match. The
agent then plays with the item facts the platform sends and its built-in
thresholds and settings, and says so in the match record. The chain of
decision sources a plan is asked of is set up only when the settings let a
model choose the plan; the chain the scouts' destinations are asked of
(`red_alert_scout`), once for each scout slot so that each scout asks on its
own and neither waits for the other or for the plan, only when the settings
let a model choose them and the scout slots are on. The glossary the
briefing names types from (`config/redalert/glossary.toml`) is read only when
they also brief it; a glossary that cannot be read is said in the record, and
the model then reads the compact summary instead. With the plan prompt
"compact" the plan is asked with the compact prompt, which reads no file.
With `model_commander` on, the model commander (see `commander`) asks the
`red_alert_commander` chain over a chooser of its own, and names types in
main's question by the glossary's plain names, by their codes when the
glossary cannot be read.
"""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence, TypeVar

from altruagent import WAIT, DecisionContext, GameState, WithReasoning
from altruagent.runner import Decision

from ucla_agent.core import records
from ucla_agent.core.config import ConfigError
from ucla_agent.core.decide import AsyncChooser, ChainSource, ChoiceResult, build_chain
from ucla_agent.core.match_meta import TESTING_CONTEXT, TOURNAMENT_CONTEXT
from ucla_agent.core.paths import repository_root
from ucla_agent.games.redalert.arbitration import RepeatFilter
from ucla_agent.games.redalert.base_defense import BaseDefenseManager
from ucla_agent.games.redalert.batch import Intent, Limits, Priority, command_count, pack
from ucla_agent.games.redalert.briefing import Briefing
from ucla_agent.games.redalert.combat import CombatManager
from ucla_agent.games.redalert.command_prompt import plain_names
from ucla_agent.games.redalert.commander import Commander
from ucla_agent.games.redalert.compact_prompt import CompactPrompt
from ucla_agent.games.redalert.composition import mix_stage
from ucla_agent.games.redalert.crush import run_first
from ucla_agent.games.redalert.family import BARRACKS_TYPES, WAR_FACTORY
from ucla_agent.games.redalert.glossary import Glossary
from ucla_agent.games.redalert.items import ItemTable
from ucla_agent.games.redalert.ledger import FINISHED_PHASE, OrderLedger
from ucla_agent.games.redalert.managers import Chooser, Manager, ManagerList, MatchContext
from ucla_agent.games.redalert.model_history import ModelHistory
from ucla_agent.games.redalert.opening import OpeningManager
from ucla_agent.games.redalert.plans import PlanRules, plan_prompt
from ucla_agent.games.redalert.raids import RaidManager
from ucla_agent.games.redalert.rush_counter import held_home
from ucla_agent.games.redalert.scout_stance import without_scout_attacks
from ucla_agent.games.redalert.scouting import ScoutingManager
from ucla_agent.games.redalert.settings import COMPACT_PROMPT, RedAlertSettings
from ucla_agent.games.redalert.terrain import Terrain
from ucla_agent.games.redalert.timing import TimingWindows
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.upkeep import UpkeepManager
from ucla_agent.games.redalert.world import World

PLAYING_PHASE = "playing"
# The chain of `config/agent.toml` whose sources are asked to choose a plan.
PLAN_CHAIN = "red_alert_plan"
# The chain of `config/agent.toml` whose sources are asked where each designated scout goes next.
SCOUT_CHAIN = "red_alert_scout"
# The chain of `config/agent.toml` whose sources the model commander asks.
COMMANDER_CHAIN = "red_alert_commander"
# The type of an `error` event for a failure that was caught only at the outermost level.
UNHANDLED_ERROR = "unhandled"
_CONFIG_ERROR_SOURCE = "redalert.config"
# Where an `error` event says a map picture of mostly unknown characters was found.
_TERRAIN_ERROR_SOURCE = "terrain"
_NS_PER_MS = 1_000_000
_MS_DECIMALS = 3

_Setting = TypeVar("_Setting")


class PlanSelector(Protocol):
    """What sets the plan in force at the start of every cycle."""

    def select(self, world: World, ctx: MatchContext, choice: ChoiceResult | None) -> str:
        """The plan for this cycle; `choice` is a decision source's answer when one has just arrived."""


class RedAlertAgent:
    """Decision-maker for one Red Alert match. See the module's note."""

    def __init__(
        self,
        *,
        items: ItemTable | None = None,
        tuning: Tuning | None = None,
        chooser: Chooser | None = None,
        managers: Iterable[Manager] | None = None,
        plan_selector: PlanSelector | None = None,
        settings: RedAlertSettings | None = None,
        scout_choosers: Sequence[Chooser] | None = None,
        commander_chooser: Chooser | None = None,
    ) -> None:
        """An agent for a match that is about to start.

        Whatever is left out is the agent's own: the tracked item facts,
        thresholds and settings, a chooser over the configured plan chain, one
        over the scout chain for each scout slot when the settings ask a model
        where the scouts go, one over the commander chain when the model
        commander is on, the managers it plays with and the script's plan
        rules. Managers take the slot they name, in whatever order they are
        given.
        """
        tuning = _tracked(Tuning.from_file, Tuning()) if tuning is None else tuning
        # An item table without items is a table all the same: it is filled from the platform's rules.
        self._items = _tracked(ItemTable.from_tracked_files, ItemTable([])) if items is None else items
        settings = _tracked(RedAlertSettings.from_file, RedAlertSettings()) if settings is None else settings
        self._plan_selector = plan_selector or _plan_rules(tuning, settings)
        self._repeats = RepeatFilter(tuning.loop.reissue_after_ticks)
        self._timing = TimingWindows(tuning.loop.timing_window_ticks)
        # Intents left out of a batch in the timing window under way, by the name of their class.
        self._dropped_by_class: Counter[str] = Counter()
        # When the batch that awaits its answer was returned to the runtime.
        self._sent_at_ns: int | None = None
        self._is_first_call = True
        self.world = World(self._items, tuning=tuning.world)
        self.ledger = OrderLedger()
        self.managers = ManagerList(_own_managers() if managers is None else managers)
        if chooser is None:
            chooser = _chooser(PLAN_CHAIN, tuning, settings.model_plan_choice)
        if scout_choosers is None:
            scout_choosers = _scout_choosers(tuning, settings)
        self.ctx = MatchContext(
            tuning,
            self.ledger,
            chooser,
            scout_choosers=tuple(scout_choosers),
            model_history=ModelHistory(settings.history_picks),
        )
        self.ctx.commander = _commander(tuning, settings, commander_chooser)
        self.ctx.unit_orders = self._repeats.last_orders
        records.on_match_end(self.finalize)

    @property
    def current_plan(self) -> str:
        """The plan in force."""
        return self.ctx.plan

    @property
    def threat_active(self) -> bool:
        """Whether the base is under an attack that the base-defense manager has declared."""
        return self.ctx.threat_active

    def choose_action(self, state: GameState, context: DecisionContext) -> Decision:
        """Answer the runtime's request for a move: one batch of orders, or `WAIT` when there is nothing to send.

        Never raises. A manager that fails is left out of the cycle; whatever
        else goes wrong is recorded and answered with `WAIT`, and the next
        state is played as if nothing had happened.
        """
        entered_ns = time.perf_counter_ns()
        if _is_over(state.raw):
            self._close(state.raw)
            return WAIT
        try:
            batch = self._decide(state, context, entered_ns)
        except Exception as exc:  # noqa: BLE001 - the runtime gets an answer whatever went wrong in making one
            _log_error("choose_action", UNHANDLED_ERROR, exc)
            batch = None
        decision = WAIT if batch is None else WithReasoning(batch, self._summary(batch["orders"]))
        compute_ms = round((time.perf_counter_ns() - entered_ns) / _NS_PER_MS, _MS_DECIMALS)
        self._timing.note_compute(compute_ms)
        orders = () if batch is None else batch["orders"]
        # The last thing before the return: the round trip of the answer is measured from here.
        records.log_action(
            state.state_version,
            WAIT if batch is None else batch,
            compute_ms,
            order_count=len(orders),
            command_count=sum(command_count(order) for order in orders),
        )
        self._sent_at_ns = None if batch is None else time.perf_counter_ns()
        return decision

    def _close(self, raw: Mapping[str, Any]) -> None:
        """Take in the state of a finished match: the ledger writes its closing summary and nothing else is logged."""
        try:
            self.world.update(raw)
            self.ledger.reconcile(self.world, raw)
        except Exception as exc:  # noqa: BLE001 - the match is over; the record's ending is written all the same
            _log_error("choose_action", UNHANDLED_ERROR, exc)

    def _decide(self, state: GameState, context: DecisionContext, entered_ns: int) -> dict[str, Any] | None:
        """One cycle: the state taken in, the managers heard, their intents made into a batch; None for no batch."""
        raw = state.raw
        if self._is_first_call:
            self._is_first_call = False
            self._begin_match(raw, context)
        records.log_state(raw, self._state_annotations())
        world, ctx = self.world, self.ctx
        try:
            world.update(raw)
        except Exception as exc:  # noqa: BLE001 - a state the world model cannot read is sat out, not played blind
            _log_error("world", records.HANDLED_ERROR, exc)
            return None
        self.ledger.reconcile(world, raw)
        # A state that names no faction has no observation yet, and there is nothing to order from.
        if world.phase != PLAYING_PHASE or world.family is None:
            return None
        ctx.begin_cycle(world)
        self._report_every_window(state.state_version, entered_ns)
        self._select_plan()
        admitted = self.ledger.admit(self._proposals(), world)
        # The tanks of a crush run and a harvester on a harvester crush, as the cycle's managers published them,
        # take no order but their run's.
        admitted = run_first(admitted, {**ctx.crush_run, **ctx.harvester_crush})
        # A slot's scout holds fire, and no manager's attack may make it shoot (see `scout_stance`).
        admitted = without_scout_attacks(admitted, ctx)
        # Under a rush main holds home for its first minutes, whoever ordered it out (see `rush_counter`).
        admitted = held_home(admitted, world, ctx)
        # Our groups' infantry spread one to a cell, before the repeats are taken out, so that a unit's own cell is
        # not sent again while its move there is in force.
        try:
            admitted = ctx.infantry_spread.apply(admitted, world, ctx, self._tells_something_new)
        except Exception as exc:  # noqa: BLE001 - the orders go out unspread rather than not at all
            _log_error("spread", records.HANDLED_ERROR, exc)
        admitted = self._repeats.without_repeats(admitted, world)
        packed = pack(admitted, world, Limits.from_state(raw))
        self._dropped_by_class.update(intent.priority.name for intent in packed.deferred)
        # Recorded also when nothing goes out: the ledger keeps what did not fit for the next cycle.
        self.ledger.record(packed, world, state.state_version)
        if not packed.intents:
            return None
        self._repeats.note_sent(self.ledger.entries[-len(packed.intents) :])
        # A unit that boards a transport is reported lost; the world tells it apart by the orders sent.
        world.note_orders(packed.orders)
        return {"type": "orders", "orders": packed.orders}

    def _tells_something_new(self, intent: Intent) -> bool:
        """Whether an intent would tell one of its units something new, as the repeat filter has it."""
        return bool(self._repeats.without_repeats([intent], self.world))

    def _state_annotations(self) -> dict[str, Any]:
        """What the match record's state event adds to the state: the plan in force and the enemy army value seen.

        Both are as they stood when the state arrived, before this cycle takes
        it in: the state is recorded first, so that it is in the record even
        when the cycle that follows fails.
        """
        return {"plan": self.ctx.plan, "enemy_army_value": self.ctx.scout_report.enemy_combat_value}

    def _report_every_window(self, state_version: int, entered_ns: int) -> None:
        """Log the timing figures and the ledger's counters each time a window of game ticks is over.

        A worker process can be killed, and then no end of the match is ever
        recorded; the record still holds the counters of its last window. The
        timing event also says how production stood at the window's end, so
        that a record shows whether income, power and producers kept pace,
        which order of the opening's build the match is played with, and which
        row of the army mix the unit queues were fed toward; and how many
        intents of each class did not fit into the window's batches.
        """
        timing = self._timing.note_call(self.world.tick, state_version, entered_ns / _NS_PER_MS)
        if timing is not None:
            opening_order = self.ctx.tuning.opening.order
            figures = {**self._production_figures(), **self._mix_figures(), "opening_order": opening_order}
            records.log("timing", {**timing, **figures, "dropped_by_class": self._dropped_figures()})
            records.log("ledger_summary", self.ledger.summary())

    def _production_figures(self) -> dict[str, Any]:
        """The income estimate, the power left over and the barracks and war factories that stand."""
        production, economy = self.ctx.production, self.world.economy
        return {
            **self.ctx.income.figures(),
            "power_headroom": economy.power_provided - economy.power_drained,
            "barracks": sum(production.standing(barracks) for barracks in BARRACKS_TYPES),
            "war_factories": production.standing(WAR_FACTORY),
        }

    def _dropped_figures(self) -> dict[str, int]:
        """The intents left out of a batch over the window that is over, for every class; the next window starts at 0.

        An intent of a class the ledger carries over counts again in every
        cycle it is left out.
        """
        dropped, self._dropped_by_class = self._dropped_by_class, Counter()
        return {priority.name: dropped[priority.name] for priority in Priority}

    def _mix_figures(self) -> dict[str, str]:
        """The tech stage of the army mix the base has reached, and the shifts in force: the counter shift, the air
        shift, or both."""
        world = self.world
        return {"mix_stage": mix_stage(world), "counter_shift": self.ctx.army_mix.shifts(world)}

    def _select_plan(self) -> None:
        """Set the plan for this cycle; when the selector or the chooser fails, the plan stays what it was."""
        world, ctx = self.world, self.ctx
        try:
            ctx.plan = self._plan_selector.select(world, ctx, ctx.chooser.poll(str(world.tick)))
        except Exception as exc:  # noqa: BLE001 - play goes on under the plan in force
            _log_error("plan", records.HANDLED_ERROR, exc)

    def _proposals(self) -> list[Intent]:
        """What the managers want ordered in this cycle, in their fixed order, and after them the boarding orders the
        world says are due again; a manager that fails proposes nothing."""
        proposed: list[Intent] = []
        for manager in self.managers:
            try:
                proposed += manager.intents(self.world, self.ctx)
            except Exception as exc:  # noqa: BLE001 - one manager's failure must not cost the others their turn
                _log_error(manager.name, records.HANDLED_ERROR, exc)
        # A unit still walking to its transport is ordered in again, so that it is taken aboard, not lost, once in.
        return proposed + [Intent(Priority.MOVE, order) for order in self.world.boarding_due()]

    def on_action_result(self, result: dict, context: DecisionContext) -> None:
        """Take in the platform's answer to the batch sent last; the ledger writes it to the match record.

        Never raises: the runtime ends the match of an agent whose result
        hook does.
        """
        try:
            if self._sent_at_ns is not None:
                self._timing.note_round_trip((time.perf_counter_ns() - self._sent_at_ns) / _NS_PER_MS)
                self._sent_at_ns = None
            self.ledger.on_action_result(result, context)
        except Exception as exc:  # noqa: BLE001 - an answer that cannot be taken in is no reason to stop playing
            _log_error("on_action_result", UNHANDLED_ERROR, exc)

    def finalize(self) -> None:
        """Log the order ledger's counters for the match, and with the model commander on its own, once."""
        self.ledger.finalize()
        if self.ctx.commander is not None:
            self.ctx.commander.finalize()

    def _begin_match(self, raw: Mapping[str, Any], context: DecisionContext) -> None:
        """Take in what only the first call brings: the game's reference object, the seat and the kind of match.

        A map picture whose unknown characters cover more of it than the tuning
        allows is reported once, as a handled error, and played on.
        """
        game_config = context.game_config
        if game_config is not None:
            records.log("game_config", game_config)
        self.world = World(self._items.with_game_config(game_config), game_config, self.ctx.tuning.world)
        state_map = raw.get("map")
        map_id = state_map.get("id") if isinstance(state_map, Mapping) else None
        terrain_tuning = self.ctx.tuning.terrain
        self.ctx.terrain = Terrain.from_game_config(game_config, map_id, terrain_tuning.buildable_characters)
        unknown_characters = self.ctx.terrain.unknown_characters_error(terrain_tuning.unknown_block_warn_share)
        if unknown_characters is not None:
            _log_error(_TERRAIN_ERROR_SOURCE, records.HANDLED_ERROR, unknown_characters)
        self.ctx.seat = raw.get("your_seat")
        # The runtime names a tournament for a tournament game only.
        self.ctx.match_kind = TOURNAMENT_CONTEXT if context.tournament_id else TESTING_CONTEXT
        self.ctx.session_id = context.session_id

    def _summary(self, orders: Iterable[Mapping[str, Any]]) -> str:
        """The plan in force and how many orders of each command a batch holds, as its reasoning summary."""
        counts = Counter(order["cmd"] for order in orders)
        text = f"{self.ctx.plan}: " + ", ".join(f"{cmd} {count}" for cmd, count in counts.items())
        return text[: self.ctx.tuning.loop.reasoning_summary_chars]


def _own_managers() -> list[Manager]:
    """The managers the agent plays with. A manager added to the agent is added here, and takes the slot it names."""
    return [BaseDefenseManager(), UpkeepManager(), OpeningManager(), ScoutingManager(), CombatManager(), RaidManager()]


def _is_over(raw: Mapping[str, Any]) -> bool:
    """Whether a state is of a match that has ended. The runtime never sends one; a test harness may."""
    return bool(raw.get("is_terminal")) or raw.get("phase") == FINISHED_PHASE


def _tracked(read: Callable[[Path], _Setting], built_in: _Setting) -> _Setting:
    """A setting read from its tracked file, or `built_in` with the reason recorded when the file is unusable."""
    try:
        return read(repository_root())
    except ConfigError as exc:
        _log_error(_CONFIG_ERROR_SOURCE, records.HANDLED_ERROR, exc)
        return built_in


def _plan_rules(tuning: Tuning, settings: RedAlertSettings) -> PlanRules:
    """The script's plan rules, with the question a model is asked: the compact prompt with the plan prompt "compact",
    which reads no file; else the tracked prompt template and, with the briefing on, the briefing it reads."""
    if settings.plan_prompt == COMPACT_PROMPT:
        return PlanRules(tuning, settings, compact=CompactPrompt())
    briefing = _tracked_briefing(settings)
    return PlanRules(tuning, settings, _tracked_prompt(settings, briefed=briefing is not None), briefing)


def _tracked_briefing(settings: RedAlertSettings) -> Briefing | None:
    """The briefing a model choosing the plan reads, over the tracked glossary; None when no model may choose the
    plan, the briefing is switched off, or the glossary is unusable, which is recorded."""
    if not (settings.model_plan_choice and settings.model_briefing):
        return None
    glossary = _tracked(Glossary.from_file, None)
    return None if glossary is None else Briefing(glossary)


def _tracked_prompt(settings: RedAlertSettings, briefed: bool) -> str | None:
    """The plan choice's prompt template when a model may choose the plan, the briefing's when `briefed`; None when no
    model may choose it or the template is unreadable."""
    if not settings.model_plan_choice:
        return None
    try:
        return plan_prompt(briefed)
    except OSError as exc:
        _log_error(_CONFIG_ERROR_SOURCE, records.HANDLED_ERROR, exc)
        return None


def _scout_choosers(tuning: Tuning, settings: RedAlertSettings) -> tuple[AsyncChooser, ...]:
    """One chooser over the scout chain of `config/agent.toml` for each scout slot, each over a chain of its own, when a
    model may choose where the scouts go and the scout slots are on; none otherwise."""
    if not (settings.model_scout_choice and tuning.scouting.scout_slots):
        return ()
    return tuple(_chooser(SCOUT_CHAIN, tuning, True) for _ in range(tuning.scouting.scout_slot_count))


def _commander(tuning: Tuning, settings: RedAlertSettings, chooser: Chooser | None) -> Commander | None:
    """The model commander when `model_commander` is on, over `chooser` or else a chooser of its own over the commander
    chain, whose answers go stale after answer_max_age_ticks, naming types by the tracked glossary; None when it is
    off."""
    if not settings.model_commander:
        return None
    if chooser is None:
        chooser = _chooser(COMMANDER_CHAIN, tuning, True, tuning.commander.answer_max_age_ticks)
    return Commander(chooser, tuning.commander, plain_names(_tracked(Glossary.from_file, None)))


def _chooser(chain_name: str, tuning: Tuning, is_asked: bool, max_age_ticks: int | None = None) -> AsyncChooser:
    """The chooser over a chain of `config/agent.toml` when a model is to be asked, whose answers go stale after
    `max_age_ticks`, or the loop's choice_max_age_ticks when none is given.

    It is over no source at all when none is, or when the chain cannot be built.
    """
    chain = ChainSource([], name=chain_name)
    if is_asked:
        try:
            chain = build_chain(chain_name)
        except ConfigError as exc:
            _log_error(_CONFIG_ERROR_SOURCE, records.HANDLED_ERROR, exc)
    max_age_ticks = tuning.loop.choice_max_age_ticks if max_age_ticks is None else max_age_ticks
    # A question is tagged with the tick it was asked at.
    return AsyncChooser(chain, lambda asked_tick, tick: int(tick) - int(asked_tick) > max_age_ticks)


def _log_error(where: str, kind: str, error: Exception) -> None:
    """Record a failure the agent played on from: where it was caught, what it was and where it was raised."""
    records.log(
        "error",
        {
            "where": where,
            "type": kind,
            "exception": type(error).__name__,
            "message": str(error) + _raised_at(error.__traceback__),
        },
    )


def _raised_at(trace: TracebackType | None) -> str:
    """Where a caught exception was raised, as the file name, line and function of the last frame of its traceback.

    It is read from the traceback's own objects: the standard way of listing a
    traceback looks at the source files, and the thread that answers the
    runtime never touches a file. An exception that was made but never raised
    has no traceback, and nothing is said of where it was raised.
    """
    if trace is None:
        return ""
    while trace.tb_next is not None:
        trace = trace.tb_next
    code = trace.tb_frame.f_code
    return f" (raised at {Path(code.co_filename).name}:{trace.tb_lineno} in {code.co_name})"
