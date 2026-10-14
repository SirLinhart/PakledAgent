"""One practice match of `pokemon_vgc_doubles_draft`: a decision-maker plays one seat through the agent's real runner loop.

`play_practice_match` starts a battle helper (real Showdown on Node unless a
test names another command), sets up the draft and the match, seats the
opponent in the other seat and hands a `PokemonSeatSession` to the vendored
`run_game`, with the agent's own `Dispatcher` as the agent. So a practice
match goes the way a tournament match does: the dispatcher opens a normal
match record under the runs folder (`UCLA_AGENT_RUNS_DIR` moves it), the
runner's loop calls the decision-maker, and the result closes the record.
Matches played one after another in one process each get a record of their
own.

The driven seat is seat 0 (Showdown's p1); the first drafter comes from the
seed unless the caller names it. After the match its record is read back through
`ucla_agent.core.metrics` for the fallbacks and the `unhandled` errors the
decision-maker logged. A runner error, such as a move the rules refuse or a
decision-maker that raises, ends the match early for us and counts as an
unhandled error; a fault of the harness itself (the opponent, the battle
helper) is raised.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from altruagent import DecisionContext
from altruagent.runner import RunnerError, run_game

from ucla_agent.core import metrics, records
from ucla_agent.core.dispatch import Dispatcher
from ucla_agent.core.paths import repository_root, runs_folder
from ucla_agent.core.record_files import match_folder_name, seat_label
from ucla_agent.practice.pokemon.bridge import BattleHelper
from ucla_agent.practice.pokemon.cards import CardCatalog, load_catalog
from ucla_agent.practice.pokemon.match import GAME_TYPE, PokemonPracticeMatch
from ucla_agent.practice.pokemon.opponents import OPPONENT_FACTORIES
from ucla_agent.practice.pokemon.seat import PokemonSeatSession

SESSION_PREFIX = "practice-pokemon"
OUR_SEAT = 0
_SESSION_SUFFIX_LENGTH = 8


@dataclass(frozen=True)
class PokemonPracticeResult:
    """What one practice match came to, seen from the driven seat.

    `winner` is the winning seat's agent id (None for a tie or a match cut
    short), `turns` the battle's last turn, `decisions` the driven seat's
    accepted moves (picks, lineup and turns), `fallbacks` the `fallback`
    events of its record. `unhandled_error` is set when the record holds an
    `unhandled` error or the runner stopped with an error, whose text is
    `error`.
    """

    seed: int
    winner: str | None
    our_won: bool
    turns: int
    decisions: int
    fallbacks: int
    unhandled_error: bool
    session_id: str
    opponent: str
    termination_reason: str | None
    error: str | None = None


def play_practice_match(
    seed: int,
    agent_factory: Callable[[], Any],
    opponent: str,
    *,
    helper_command: Sequence[str] | None = None,
    catalog: CardCatalog | None = None,
    opponent_factories: Mapping[str, Callable[[int | None], Any]] = OPPONENT_FACTORIES,
    clock: Callable[[], float] = time.time,
    first_drafter: int | None = None,
) -> PokemonPracticeResult:
    """Play one match to its end with `agent_factory`'s decision-maker in seat 0 against an opponent kind.

    `agent_factory` makes any object with `choose_action` (and, optionally,
    `on_action_result`) as the runtime calls them. `opponent` is a kind of
    `opponent_factories` (`random`, `first` or `self`), made with the seed.
    `helper_command` replaces the real battle helper (the fake one in tests),
    `catalog` the tracked card catalog and `clock` the draft clock.
    `first_drafter` (0, our seat, or 1) makes the draft's first pick in
    place of the one the seed draws; the seed's pool stays the same.
    """
    catalog = catalog or load_catalog()
    session_id = f"{SESSION_PREFIX}-{seed}-{uuid.uuid4().hex[:_SESSION_SUFFIX_LENGTH]}"
    rival = opponent_factories[opponent](seed)
    with BattleHelper(helper_command) as helper:
        match = PokemonPracticeMatch(seed, catalog, helper, clock=clock, first_drafter=first_drafter)
        session = PokemonSeatSession(match, OUR_SEAT, rival, session_id)
        error = _run(session, match, agent_factory)
    record = _record(session_id)
    winner = match.seat_ids[match.winner] if match.winner is not None else None
    return PokemonPracticeResult(
        seed=seed,
        winner=winner,
        our_won=match.winner == OUR_SEAT,
        turns=match.turns,
        decisions=session.decisions,
        fallbacks=len(record.data_of("fallback")) if record else 0,
        unhandled_error=error is not None or _holds_unhandled_error(record),
        session_id=session_id,
        opponent=opponent,
        termination_reason=match.termination_reason,
        error=error,
    )


def _run(session: PokemonSeatSession, match: PokemonPracticeMatch, agent_factory: Callable[[], Any]) -> str | None:
    """Play the driven seat through `run_game` in a record of its own; the runner error that stopped it, if any."""
    context = DecisionContext(
        session_id=session.session_id,
        tournament_id=None,
        game_type=GAME_TYPE,
        agent_id=match.seat_ids[OUR_SEAT],
        seat_position=OUR_SEAT,
    )
    records.reset_for_next_match()
    try:
        run_game(session, context, Dispatcher({GAME_TYPE: agent_factory}))
    except RunnerError as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        records.reset_for_next_match()
    return None


def _record(session_id: str) -> metrics.Match | None:
    """The driven seat's match record, read back; None when the decision-maker never opened one."""
    runs = runs_folder(repository_root() / records.RUNS_DIR_NAME)
    folders = sorted(runs.glob(f"*/{match_folder_name(session_id, seat_label(OUR_SEAT))}"))
    return metrics.load_match(folders[-1]) if folders else None


def _holds_unhandled_error(record: metrics.Match | None) -> bool:
    return record is not None and any(error.get("type") == metrics.UNHANDLED_ERROR for error in record.data_of("error"))
