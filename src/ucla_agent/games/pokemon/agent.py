"""Pokémon VGC doubles draft decision-maker: the draft, team preview and every doubles turn, each legal and in time.

The runtime calls `choose_action` whenever our seat has a decision. The
state is recorded, then the decision is made by the phase the state names
(`state.raw["phase"]`):

- `draft`: the draft memory takes in the observation, the offered cards are
  scored (`draft`) and the pick is sent as `WithReasoning`, its reason the
  pick's public reason;
- `team_preview`: the 90 lineups are scored against the opponent's known sets
  (`preview`) and the `select_lineup` action is sent;
- `moving`: the board is read (`battle`), the battle tracker takes it in, the
  joint actions are scored (`turn`) and the `doubles_turn` action is sent.

Each decision is one `pokemon_decision` event (`decide`), the model choosing
among the script's best few when it is switched on, within the time the
observation's clock leaves a lineup or a turn. The model reads the situation
as the full briefing (`briefing`) when `model_briefing` is on, a turn's
ending with the last `[briefing] recent_turns` turns read from the
observation's protocol log (`recent`), and as a one-line summary of the
rosters or the board when it is off. With
`model_decides` on, each candidate is described without the script's score:
a draft card as its set line, a lineup as the four brought and the leads, a
turn as both slots' actions with their expected damage and knockout chance.

With `[maximin] enabled` in the tuning, each phase ranks by maximin instead,
within the time `decide.compute_until` leaves: the draft scores each card one
pick ahead (with `draft_lookahead` on), team preview ranks our lineups by
their worst score against the opponent's lineups, and a turn ranks the best
`our_top` joint actions by their worst result against the opponent's best
`their_top` (`maximin`), a second battle tracker following the board as the
opponent sees it. Each candidate's description then ends with the opponent's
best reply and its worst case (`briefing`). A maximin ranking that runs out
of time gives way to the script's own, and the `pokemon_decision` event says
which was used.

One agent plays one match, so the draft memory and the battle trackers it
keeps are the match's.

`choose_action` never raises on a platform state. A state it cannot read as
the guide describes it (another phase, an observation that is not an object,
a template that is not the phase's) is answered with the template-only
fallback (`template`) and one `error` event of type `unexpected_state`; a
failure of the agent's own is answered the same way with an `error` event of
type `unhandled`. A lineup or turn the policy built is checked against its
template before it is sent.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import TracebackType
from typing import Any, Mapping

from altruagent import DecisionContext, GameState, LegalAction, WithReasoning
from altruagent.runner import Decision

from ucla_agent.core import records
from ucla_agent.core.config import ConfigError
from ucla_agent.core.decide import ChainSource, DecisionSource, build_chain
from ucla_agent.core.paths import repository_root
from ucla_agent.games.pokemon import data
from ucla_agent.games.pokemon.battle import Board, BattleTracker, Fighter, read_board
from ucla_agent.games.pokemon.briefing import (
    battle_context,
    draft_context,
    draft_reply,
    lineup_reply,
    preview_context,
    set_line,
    turn_reply,
)
from ucla_agent.games.pokemon.decide import (
    DRAFT,
    PREVIEW,
    RANKED_BY_MAXIMIN,
    RANKED_BY_SCRIPT,
    TURN,
    Asked,
    Candidate,
    choose,
    compute_until,
)
from ucla_agent.games.pokemon.draft import (
    OURS,
    THEIRS,
    CardScore,
    DraftMemory,
    offered_card_ids,
    pick_action,
    score_cards,
    score_cards_lookahead,
)
from ucla_agent.games.pokemon.maximin import Side, opponent_view, rank_turn
from ucla_agent.games.pokemon.observe import Reader, UnexpectedState, read_clock
from ucla_agent.games.pokemon.preview import (
    LineupTable,
    RosterPokemon,
    rank_lineups,
    rank_lineups_maximin,
    read_rosters,
)
from ucla_agent.games.pokemon.recent import our_side, recent_turns
from ucla_agent.games.pokemon.sets import PokemonSet
from ucla_agent.games.pokemon.settings import CHOICE_CHAIN, PokemonSettings
from ucla_agent.games.pokemon.template import DOUBLES_TURN, SELECT_LINEUP, fallback, is_legal, template_of
from ucla_agent.games.pokemon.turn import JointAction, Scorer, rank_actions

# The types of an `error` event: a state the guide does not describe, and a failure of the agent's own.
UNEXPECTED_STATE = "unexpected_state"
UNHANDLED_ERROR = "unhandled"
_CHAIN_ERROR_SOURCE = "pokemon.chains"
_NS_PER_MS = 1_000_000
_MS_DECIMALS = 3
# The observation key holding the battle's Showdown protocol lines, read for the briefing's Recent turns section.
PROTOCOL_LOG = "protocol_log"


class IllegalAction(Exception):
    """The policy built an action its template does not allow: a fault of the agent, never sent."""


class PokemonAgent:
    """Decision-maker for one Pokémon VGC doubles draft match. See the module's note."""

    def __init__(self, settings: PokemonSettings | None = None, *, choice_source: DecisionSource | None = None) -> None:
        """An agent for a match about to start.

        `settings` default to those of the tracked files. The choice source
        left out is the `pokemon_choice` chain of `config/agent.toml` when
        `model_choices` is on, and a chain of no source when it is off; tests
        give stubs.
        """
        self._settings = PokemonSettings.from_files(repository_root()) if settings is None else settings
        self._source = choice_source or _chain(CHOICE_CHAIN, self._settings.model_choices)
        self._memory = DraftMemory()
        self._tracker = BattleTracker()
        self._their_tracker = BattleTracker()
        self._phases = {DRAFT: self._draft, PREVIEW: self._preview, TURN: self._turn}
        records.on_match_end(self.finalize)

    def choose_action(self, state: GameState, context: DecisionContext) -> Decision:
        """Our decision for the state: a draft pick, a lineup or a doubles turn, legal whatever goes wrong."""
        entered_ns = time.perf_counter_ns()
        records.log_state(state.raw)
        try:
            decision = self._decide(state, context, time.perf_counter())
        except UnexpectedState as exc:
            _log_error("choose_action", exc, UNEXPECTED_STATE)
            decision = fallback(state)
        except Exception as exc:  # noqa: BLE001 - the runtime gets a legal move whatever went wrong in making one
            _log_error("choose_action", exc, UNHANDLED_ERROR)
            decision = fallback(state)
        compute_ms = round((time.perf_counter_ns() - entered_ns) / _NS_PER_MS, _MS_DECIMALS)
        records.log_action(state.state_version, _as_logged(decision), compute_ms)
        return decision

    def finalize(self) -> None:
        """Have the chain write what its sources used in the match; called once, when the match record closes."""
        self._source.write_usage_event()

    def _decide(self, state: GameState, context: DecisionContext, started: float) -> Decision:
        phase = state.raw.get("phase")
        if phase not in self._phases:
            raise UnexpectedState(f"phase {phase!r} is none of pokemon_vgc_doubles_draft's")
        observation = state.raw.get("observation")
        if not isinstance(observation, Mapping):
            raise UnexpectedState(f"the {phase} observation is {type(observation).__name__}, not an object")
        return self._phases[phase](state, context, observation, started)

    def _draft(
        self, state: GameState, context: DecisionContext, observation: Mapping[str, Any], started: float
    ) -> WithReasoning:
        reader = Reader()
        self._memory.update(observation, context.agent_id or None, reader)
        scores, ranking = self._card_scores(offered_card_ids(state.legal_actions), started)
        if not scores:
            raise UnexpectedState("no card offered is a card of the pool the observation shows")
        briefed = self._settings.model_briefing
        candidates = [
            Candidate(pick_action(score), score.total, _card_description(score, self._settings, ranking))
            for score in scores
        ]
        context = draft_context(self._memory) if briefed else _draft_context(self._memory)
        asked = Asked(DRAFT, candidates, context, f"draft-v{state.state_version}", ranking=ranking)
        chosen = choose(asked, self._settings, self._source, started, reader)
        picked = next(score for score in scores if score.card_id == chosen.action["card_id"])
        return WithReasoning(chosen.action, picked.reason())

    def _card_scores(self, offered: list[str], started: float) -> tuple[list[CardScore], str | None]:
        """The offered cards scored, best first, and how they were ranked (None while maximin is not enabled): one
        pick ahead with maximin's draft lookahead, unless it runs out of time."""
        tuning = self._settings.tuning
        if not tuning.maximin.enabled:
            return score_cards(self._memory, offered, tuning.draft), None
        if tuning.maximin.draft_lookahead:
            until = compute_until(self._settings, DRAFT, None, started)
            looked = score_cards_lookahead(self._memory, offered, tuning.draft, until)
            if looked is not None:
                return looked, RANKED_BY_MAXIMIN
        return score_cards(self._memory, offered, tuning.draft), RANKED_BY_SCRIPT

    def _preview(
        self, state: GameState, _context: DecisionContext, observation: Mapping[str, Any], started: float
    ) -> dict[str, Any]:
        reader = Reader()
        template = _template(state, SELECT_LINEUP)
        ours, theirs = read_rosters(observation, template, self._memory, reader)
        clock = read_clock(observation, reader)
        candidates, ranking = self._lineup_candidates(
            ours, theirs, compute_until(self._settings, PREVIEW, clock, started)
        )
        if self._settings.model_briefing:
            context = preview_context(ours, theirs, self._memory)
        else:
            context = _preview_context(ours, theirs)
        asked = Asked(PREVIEW, candidates, context, f"preview-v{state.state_version}", clock, ranking)
        return _checked(template, choose(asked, self._settings, self._source, started, reader).action)

    def _lineup_candidates(
        self, ours: list[RosterPokemon], theirs: list[PokemonSet], until: float
    ) -> tuple[list[Candidate], str | None]:
        """The lineups best first and how they were ranked (None while maximin is not enabled): by their worst
        score against the opponent's lineups, each described with its best reply, unless that runs out of time."""
        tuning, scored = self._settings.tuning, not self._settings.model_decides
        lineups = rank_lineups(ours, theirs, tuning.preview)
        plain = [Candidate(lineup.action(), lineup.score, lineup.description(with_score=scored)) for lineup in lineups]
        if not tuning.maximin.enabled:
            return plain, None
        weighed = rank_lineups_maximin(LineupTable.of(ours, theirs), tuning.preview, until)
        if weighed is None:
            return plain, RANKED_BY_SCRIPT
        described = [
            Candidate(
                found.lineup.action(),
                found.worst,
                f"{found.lineup.description(with_score=scored)}; {lineup_reply(found)}",
            )
            for found in weighed
        ]
        return described, RANKED_BY_MAXIMIN

    def _turn(
        self, state: GameState, _context: DecisionContext, observation: Mapping[str, Any], started: float
    ) -> dict[str, Any]:
        reader = Reader()
        template = _template(state, DOUBLES_TURN)
        board = read_board(observation, template, self._memory.set_for_species, reader)
        self._tracker.observe(board)
        ranked = rank_actions(board, self._tracker, self._settings.tuning.turn)
        if not ranked:
            raise UnexpectedState("the template allows no pair of slot choices")
        clock = read_clock(observation, reader)
        candidates, ranking = self._turn_candidates(board, ranked, compute_until(self._settings, TURN, clock, started))
        tag = f"turn{board.turn}-v{state.state_version}"
        if self._settings.model_briefing:
            scorer = Scorer(board, self._tracker, self._settings.tuning.turn)
            context = battle_context(board, self._memory, scorer, recent=self._recent_turns(observation, reader))
        else:
            context = _board_context(board)
        asked = Asked(TURN, candidates, context, tag, clock, ranking)
        action = _checked(template, choose(asked, self._settings, self._source, started, reader).action)
        self._tracker.note_choice(board, action)
        return action

    def _recent_turns(self, observation: Mapping[str, Any], reader: Reader) -> str:
        """The briefing's Recent turns section, "" when `[briefing] recent_turns` is 0 or the observation holds no
        protocol log; a log that cannot be read counts `protocol_log` as degraded and gives ""."""
        turns = self._settings.tuning.briefing.recent_turns
        log = reader.optional(observation, PROTOCOL_LOG, list) if turns else None
        if log is None:
            return ""
        section = recent_turns(log, our_side(observation), turns)
        if section is None:
            reader.note(PROTOCOL_LOG)
        return section or ""

    def _turn_candidates(
        self, board: Board, ranked: list[JointAction], until: float
    ) -> tuple[list[Candidate], str | None]:
        """The joint actions best first and how they were ranked (None while maximin is not enabled): the best
        `our_top` by their worst result against the opponent's best replies, each described with its reply, then
        every other joint action in the script's order; the script's order alone when maximin runs out of time."""
        tuning, scored = self._settings.tuning, not self._settings.model_decides
        plain = [Candidate(joint.action, joint.score, joint.description(with_score=scored)) for joint in ranked]
        if not tuning.maximin.enabled:
            return plain, None
        their_board = opponent_view(board)
        self._their_tracker.observe(their_board)
        ours, theirs = Side(board, self._tracker), Side(their_board, self._their_tracker)
        weighed = rank_turn(ours, theirs, ranked, tuning.turn, tuning.maximin, until)
        if weighed is None:
            return plain, RANKED_BY_SCRIPT
        first = [
            Candidate(
                found.joint.action,
                found.worst,
                f"{found.joint.description(with_score=scored)}; {turn_reply(found, their_board)}",
            )
            for found in weighed
        ]
        weighed_joints = {id(found.joint) for found in weighed}
        rest = [candidate for joint, candidate in zip(ranked, plain) if id(joint) not in weighed_joints]
        return first + rest, RANKED_BY_MAXIMIN


def _template(state: GameState, kind: str) -> Mapping[str, Any]:
    """The template the state's one legal action carries; `UnexpectedState` when it is not one of `kind`."""
    template = template_of(state.legal_actions[0]) if state.legal_actions else None
    if template is None or template.get("type") != kind:
        raise UnexpectedState(f"the legal action carries no {kind} template")
    return template


def _checked(template: Mapping[str, Any], action: Mapping[str, Any]) -> dict[str, Any]:
    """The action, when its template allows it; `IllegalAction` otherwise."""
    if not is_legal(template, action):
        raise IllegalAction(f"the {template.get('type')} template does not allow {action!r}")
    return dict(action)


def _card_description(score: CardScore, settings: PokemonSettings, ranking: str | None) -> str:
    """An offered card as a model reads it: its set line, unscored, when the model decides; else the script's
    description, with the card's types and Speed when the model is briefed; either way followed by the opponent's
    likely next pick when maximin looked one pick ahead. An offered card is a card of the pool, never a guessed
    set."""
    if settings.model_decides:
        text = set_line(score.card, guessed=False)
    else:
        text = score.description(with_types_and_speed=settings.model_briefing)
    return f"{text}; {draft_reply(score)}" if ranking == RANKED_BY_MAXIMIN else text


def _names(sets: Any) -> str:
    return ", ".join(data.species(found.species).name for found in sets if found is not None) or "none"


def _preview_context(ours: list[RosterPokemon], theirs: list[PokemonSet]) -> str:
    return f"Team preview. Ours: {_names(pokemon.set for pokemon in ours)}. Theirs: {_names(theirs)}."


def _draft_context(memory: DraftMemory) -> str:
    return f"Draft. Our roster: {_names(memory.roster_sets(OURS))}. Their roster: {_names(memory.roster_sets(THEIRS))}."


def _fighter_text(fighter: Fighter | None) -> str:
    return "empty" if fighter is None or not fighter.standing else f"{fighter.species} {fighter.hp_fraction:.0%}"


def _board_context(board: Board) -> str:
    """The turn as a model reads it: both sides' actives with their HP, and the field."""
    ours = ", ".join(_fighter_text(fighter) for fighter in board.ours)
    theirs = ", ".join(_fighter_text(fighter) for fighter in board.theirs)
    field = ", ".join(filter(None, [board.weather, board.terrain, "trick room" if board.trick_room else None]))
    return f"Turn {board.turn}. Ours: {ours}. Theirs: {theirs}. Field: {field or 'none'}."


def _as_logged(decision: Any) -> Any:
    """The decision as the `action` event records it: the structured action, or a legal action's id."""
    if isinstance(decision, WithReasoning):
        return {"action": decision.action, "reasoning_summary": decision.reasoning_summary}
    return decision.action_id if isinstance(decision, LegalAction) else decision


def _chain(name: str, switched_on: bool) -> ChainSource:
    """The chain `config/agent.toml` names, when its switch is on; a chain of no source when it is off or unusable."""
    if switched_on:
        try:
            return build_chain(name)
        except ConfigError as exc:
            _log_error(_CHAIN_ERROR_SOURCE, exc, records.HANDLED_ERROR)
    return ChainSource([], name=name)


def _log_error(where: str, error: Exception, kind: str) -> None:
    """Record a failure the agent played on from: where it was caught, what it was and where it was raised."""
    records.log(
        "error",
        {
            "where": where,
            "type": kind,
            "exception": type(error).__name__,
            "message": f"{error}{_raised_at(error.__traceback__)}",
        },
    )


def _raised_at(trace: TracebackType) -> str:
    """Where a caught exception was raised: the file name, line and function of the last frame of its traceback."""
    while trace.tb_next is not None:
        trace = trace.tb_next
    code = trace.tb_frame.f_code
    return f" (raised at {Path(code.co_filename).name}:{trace.tb_lineno} in {code.co_name})"
