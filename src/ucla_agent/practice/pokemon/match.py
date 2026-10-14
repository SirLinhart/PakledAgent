"""One practice match of `pokemon_vgc_doubles_draft` between two seats: draft, team preview and a doubles battle.

The match keeps the platform's flow and counters (pokemon.md, sections 2, 4
and 6 to 8): the draft runs on a `PracticeDraft` with one `state_version`
shared by both seats; once the twelfth card is picked the battle starts on
the battle helper with both drafted teams, and from then on each seat has
its own counter, starting again at 0 and going up by one with each of its
accepted decisions (its lineup, then each turn).

A seat has a decision while it is the drafter, while Showdown's latest
request to it is a team preview or a move or switch request it has not yet
answered. Otherwise its observation is the platform's placeholder. Its
answer is checked here first (the guide's rules), then by Showdown; a
choice Showdown refuses is refused to the seat with Showdown's own message.

The state object a seat gets is the guide's envelope (section 2). Nothing
in it comes from the other seat's requests: the lineup the opponent chose
reaches a seat only as its Pokémon appear in the battle log.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ucla_agent.practice.pokemon.battle_log import BattleLog
from ucla_agent.practice.pokemon.bridge import BattleHelper, HelperUpdate
from ucla_agent.practice.pokemon.cards import CardCatalog
from ucla_agent.practice.pokemon.doubles import MOVING_PHASE, DoublesView
from ucla_agent.practice.pokemon.draft import DRAFT_PHASE, PracticeDraft, picked_card_id
from ucla_agent.practice.pokemon.preview import (
    PREVIEW_PHASE,
    lineup_choice,
    lineup_legal_action,
    preview_observation,
)
from ucla_agent.practice.pokemon.rules import (
    GAME_ALREADY_COMPLETE,
    INVALID_ACTION,
    NOT_YOUR_TURN,
    STALE_STATE,
    RuleError,
)

GAME_TYPE = "pokemon_vgc_doubles_draft"
RUNTIME_ADAPTER = "pokemon"
SIDES = ("p1", "p2")
DEFAULT_SEAT_IDS = ("practice-p1", "practice-p2")
IN_PROGRESS = "in_progress"
COMPLETED = "completed"
FINISHED_BY_BATTLE = "completed"
FINISHED_BY_RESIGNATION = "resignation"
WIN, LOSS, TIE = 1.0, 0.0, 0.5
PLACEHOLDER = {"message": "No pending decision is currently available.", "waiting_for_action": False, "finished": False}
FINISHED = {"message": "Battle finished.", "waiting_for_action": False, "finished": True}
_BATTLE_SEED = "pokemon-battle-seed:{seed}"
_SEED_PARTS = 4
_SEED_PART_LIMIT = 0x10000


def battle_seed(seed: int) -> str:
    """Showdown's seed for the battle of a practice match: four 16-bit numbers drawn from the match seed."""
    rng = random.Random(_BATTLE_SEED.format(seed=seed))
    return ",".join(str(rng.randrange(_SEED_PART_LIMIT)) for _part in range(_SEED_PARTS))


@dataclass
class SeatBattle:
    """One seat's side of the battle: its log, Showdown's latest request to it, whether it answered, its counter."""

    side: str
    log: BattleLog
    request: Mapping[str, Any] | None = None
    answered: bool = False
    version: int = 0

    @property
    def pending(self) -> bool:
        return self.request is not None and not self.request.get("wait") and not self.answered


class PokemonPracticeMatch:
    """A whole practice match of two seats, seat 0 on Showdown's side p1 and seat 1 on p2."""

    def __init__(
        self,
        seed: int,
        catalog: CardCatalog,
        helper: BattleHelper,
        seat_ids: tuple[str, str] = DEFAULT_SEAT_IDS,
        clock: Callable[[], float] = time.time,
        first_drafter: int | None = None,
    ) -> None:
        """`first_drafter`, a seat (0 or 1), makes the draft's first pick; left out, the seed draws it."""
        self.seed = seed
        self.catalog = catalog
        self.helper = helper
        self.seat_ids = seat_ids
        drafter_id = None if first_drafter is None else seat_ids[first_drafter]
        self.draft = PracticeDraft(seed, catalog, seat_ids, clock, first_drafter=drafter_id)
        self.draft_version = 0
        self.battle: list[SeatBattle] | None = None
        self.finished = False
        self.winner: int | None = None
        self.termination_reason: str | None = None

    # -- where the match is ------------------------------------------------------------------------

    @property
    def turns(self) -> int:
        """The battle's last turn number, 0 before it starts."""
        return max((seat.log.turn for seat in self.battle), default=0) if self.battle else 0

    def phase(self, seat: int) -> str:
        if self.battle is None:
            return DRAFT_PHASE
        request = self.battle[seat].request or {}
        return PREVIEW_PHASE if request.get("teamPreview") else MOVING_PHASE

    def has_decision(self, seat: int) -> bool:
        """Whether the seat has a decision to make right now."""
        if self.finished:
            return False
        if self.battle is None:
            return self.draft.current_seat == self.seat_ids[seat]
        return self.battle[seat].pending

    def state_version(self, seat: int) -> int:
        return self.draft_version if self.battle is None else self.battle[seat].version

    def apply_timeouts(self) -> bool:
        """Let the draft clock pick for a seat whose 15 seconds have passed; whether it did."""
        if self.battle is not None or self.finished or not self.draft.apply_timeout():
            return False
        self._after_pick()
        return True

    # -- what a seat sees --------------------------------------------------------------------------

    def state(self, seat: int, session_id: str) -> dict[str, Any]:
        """The seat's whole state object, the guide's envelope."""
        current = self.has_decision(seat)
        state = {
            "session_id": session_id,
            "game_type": GAME_TYPE,
            "runtime_adapter": RUNTIME_ADAPTER,
            "status": COMPLETED if self.finished else IN_PROGRESS,
            "state_version": self.state_version(seat),
            "phase": self.phase(seat),
            "observation": self.observation(seat),
            "is_terminal": self.finished,
            "is_current_actor": current,
            "current_actor": self._current_actor(seat),
            "messaging_enabled": False,
            "next_actions": _next_actions(self.finished, current),
        }
        if current:
            state["legal_actions"] = self.legal_actions_payload(seat, session_id)
        return state

    def observation(self, seat: int) -> dict[str, Any]:
        if self.finished:
            return dict(FINISHED)
        if self.battle is None:
            return self.draft.observation()
        if not self.battle[seat].pending:
            return dict(PLACEHOLDER)
        if self.phase(seat) == PREVIEW_PHASE:
            return preview_observation(self.battle[seat].request, self.draft.roster(self._other_id(seat)), self.catalog)
        return self._doubles_view(seat).observation()

    def legal_actions_payload(self, seat: int, session_id: str) -> dict[str, Any]:
        """What `get_legal_actions` answers the seat: its actions now, none when it has no decision."""
        version = self.state_version(seat)
        return {"session_id": session_id, "state_version": version, "actions": self._actions(seat, session_id, version)}

    def _actions(self, seat: int, session_id: str, version: int) -> list[dict[str, Any]]:
        if not self.has_decision(seat):
            return []
        if self.battle is None:
            return self.draft.legal_actions(self.seat_ids[seat], session_id, version)
        if self.phase(seat) == PREVIEW_PHASE:
            return [lineup_legal_action(self.battle[seat].request, session_id, version)]
        return [self._doubles_view(seat).legal_action(session_id, version)]

    def _current_actor(self, seat: int) -> dict[str, Any] | None:
        if self.battle is None and self.draft.current_seat is not None:
            drafter = self.seat_ids.index(self.draft.current_seat)
            return {"agent_id": self.draft.current_seat, "position": drafter}
        return {"agent_id": self.seat_ids[seat], "position": seat} if self.has_decision(seat) else None

    def _doubles_view(self, seat: int) -> DoublesView:
        battle = self.battle[seat]
        return DoublesView(battle.request, battle.log, self.catalog, battle.side, self.catalog.battle_format)

    def _other_id(self, seat: int) -> str:
        return self.seat_ids[1 - seat]

    # -- a seat's move -----------------------------------------------------------------------------

    def play(
        self,
        seat: int,
        state_version: int,
        action_id: str | None = None,
        action: Mapping[str, Any] | None = None,
        reasoning_summary: str | None = None,
    ) -> None:
        """Apply the seat's move, or raise `RuleError` with the platform's code."""
        self._check_turn(seat, state_version)
        if self.battle is None:
            self.draft.pick(self.seat_ids[seat], picked_card_id(action_id, action), reasoning_summary)
            self._after_pick()
            return
        if action is None:
            raise RuleError(INVALID_ACTION, f"{action_id!r} needs its structured action filled in from the template")
        self._choose(seat, self._showdown_choice(seat, action))

    def _check_turn(self, seat: int, state_version: int) -> None:
        if self.finished:
            raise RuleError(GAME_ALREADY_COMPLETE, "the match is over")
        if state_version != self.state_version(seat):
            raise RuleError(STALE_STATE, f"state_version {state_version} is not the current {self.state_version(seat)}")
        if not self.has_decision(seat):
            raise RuleError(NOT_YOUR_TURN, "there is no pending decision for this seat")

    def _showdown_choice(self, seat: int, action: Mapping[str, Any]) -> str:
        if self.phase(seat) == PREVIEW_PHASE:
            return lineup_choice(action, self.battle[seat].request)
        return self._doubles_view(seat).choice(action)

    def _after_pick(self) -> None:
        self.draft_version += 1
        if self.draft.is_complete:
            self._start_battle()

    def _start_battle(self) -> None:
        self.battle = [SeatBattle(side, BattleLog(side)) for side in SIDES]
        players = {
            side: {"name": seat_id, "team": [card.showdown_set() for card in self.draft.roster(seat_id)]}
            for side, seat_id in zip(SIDES, self.seat_ids)
        }
        self._apply(self.helper.start(self.catalog.battle_format, battle_seed(self.seed), players))

    def _choose(self, seat: int, choice: str) -> None:
        battle = self.battle[seat]
        update = self.helper.choose(battle.side, choice)
        refusal = update.sides[battle.side].error
        if refusal is None:
            battle.answered = True
            battle.version += 1
        self._apply(update)
        if refusal is not None:
            raise RuleError(INVALID_ACTION, refusal)

    def _apply(self, update: HelperUpdate) -> None:
        """Take in what the helper sent each side; a new request is a new decision."""
        for battle in self.battle:
            seen = update.sides[battle.side]
            battle.log.read(seen.log)
            if seen.request is not None:
                battle.request, battle.answered = seen.request, False
        if update.ended:
            winner = self.seat_ids.index(update.winner) if update.winner in self.seat_ids else None
            self._finish(winner, FINISHED_BY_BATTLE)

    def _finish(self, winner: int | None, reason: str) -> None:
        self.finished, self.winner, self.termination_reason = True, winner, reason

    # -- the end -----------------------------------------------------------------------------------

    def resign(self, seat: int) -> None:
        """The seat forfeits: the other seat wins at once, in any phase."""
        if self.finished:
            raise RuleError(GAME_ALREADY_COMPLETE, "the match is over")
        self._finish(1 - seat, FINISHED_BY_RESIGNATION)

    def result(self, seat: int, session_id: str) -> dict[str, Any]:
        """What `get_result` answers the seat: the returns once the match is over, nulls while it runs."""
        returns = self._returns() if self.finished else None
        return {
            "session_id": session_id,
            "is_terminal": self.finished,
            "status": COMPLETED if self.finished else IN_PROGRESS,
            "returns": returns,
            "your_return": returns[self.seat_ids[seat]] if returns else None,
            "winner_agent_id": self.seat_ids[self.winner] if self.winner is not None else None,
            "termination_reason": self.termination_reason,
        }

    def _returns(self) -> dict[str, float]:
        if self.winner is None:
            return {seat_id: TIE for seat_id in self.seat_ids}
        return {seat_id: WIN if index == self.winner else LOSS for index, seat_id in enumerate(self.seat_ids)}


def _next_actions(finished: bool, current: bool) -> list[dict[str, str]]:
    if finished:
        return [{"tool": "get_result", "hint": "The match is over: read the result."}]
    if current:
        return [{"tool": "play_action", "hint": "Submit your decision with the current state_version."}]
    return [{"tool": "wait_for_update", "hint": "Wait for your next decision."}]
