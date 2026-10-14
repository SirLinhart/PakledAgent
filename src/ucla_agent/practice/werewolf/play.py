"""One practice game of Werewolf: a decision-maker plays one seat through the agent's real runner loop.

`play_practice_game` deals a `WerewolfEngine`, seats a table mix around the
driven seat and hands a `PracticeSeatSession` to the vendored `run_game`,
with a `DecisionContext` of game type `werewolf` and the agent's own
`Dispatcher` as the agent. So a practice game goes the way a tournament game
does: the dispatcher opens a normal match record under the runs folder
(`UCLA_AGENT_RUNS_DIR` moves it), the runner's loop calls the decision-maker,
and the result closes the record. Games played one after another in a single
process each get a record of their own.

Unless the caller names it, the driven seat comes from the seed, so a run
over many seeds plays every seat. With a forced role the driven seat gets
that role and the other six roles are dealt from the same seed, so two arms
of a comparison meet the same deals. After the game its record is read back
through `ucla_agent.core.metrics`, the one module that reads records, to say
whether an `unhandled` error was recorded.
"""

from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from altruagent import DecisionContext
from altruagent.runner import run_game

from ucla_agent.core import metrics, records
from ucla_agent.core.dispatch import Dispatcher
from ucla_agent.core.paths import repository_root, runs_folder
from ucla_agent.core.record_files import match_folder_name, seat_label
from ucla_agent.practice.werewolf.engine import SEAT_COUNT, side_of, WerewolfEngine
from ucla_agent.practice.werewolf.mixes import OPPONENT_FACTORIES, build_opponents, seat_opponents, table_kinds
from ucla_agent.practice.werewolf.seat import Opponent, PracticeSeatSession
from ucla_agent.practice.werewolf.view import GAME_TYPE, seat_name

SESSION_PREFIX = "practice-werewolf"
_SESSION_SUFFIX_LENGTH = 8
_DRIVEN_SEAT_SEED = "werewolf-driven-seat:{seed}"


@dataclass(frozen=True)
class PracticeResult:
    """What one practice game came to, seen from the driven seat.

    `days_played` is the day on which the game ended. `opponent_seats` maps
    each other seat to the kind of opponent that played it, and `mix` names
    the table (`custom` for a table given as six kinds). `engine` is the
    finished game, for measurements made once it is over; no seat sees it.
    """

    seed: int
    seat: int
    role: str
    winner: str
    our_side_won: bool
    days_played: int
    unhandled_error: bool
    mix: str
    opponent_seats: dict[int, str]
    session_id: str
    termination_reason: str
    engine: WerewolfEngine = field(repr=False, compare=False)


def driven_seat(seed: int) -> int:
    """The seat a practice game of this seed drives unless told otherwise."""
    return random.Random(_DRIVEN_SEAT_SEED.format(seed=seed)).randrange(SEAT_COUNT)


def play_practice_game(
    seed: int,
    agent_factory: Callable[[], Any],
    opponents: str | Sequence[str],
    forced_role: str | None = None,
    *,
    seat: int | None = None,
    opponent_factories: Mapping[str, Callable[[], Opponent]] = OPPONENT_FACTORIES,
    clock: Callable[[], float] = time.time,
) -> PracticeResult:
    """Play one game to its end with `agent_factory`'s decision-maker in the driven seat, and say how it went.

    `agent_factory` makes any object with `choose_action` (and, optionally,
    `choose_message` and `on_action_result`) as the runtime calls them.
    `opponents` is a mix name of `MIXES` or six opponent kinds. `seat` is the
    driven seat, by default the one the seed gives; `opponent_factories`
    replaces the factories of the opponent kinds and `clock` the engine's
    clock, for tests.
    """
    seat = driven_seat(seed) if seat is None else seat
    engine = WerewolfEngine(seed, forced_role, None if forced_role is None else seat, clock=clock)
    mix, kinds = table_kinds(opponents)
    seating = seat_opponents(kinds, seed, seat)
    session_id = f"{SESSION_PREFIX}-{seed}-{uuid.uuid4().hex[:_SESSION_SUFFIX_LENGTH]}"
    session = PracticeSeatSession(engine, seat, build_opponents(seating, opponent_factories), session_id)
    context = DecisionContext(
        session_id=session_id, tournament_id=None, game_type=GAME_TYPE, agent_id=seat_name(seat), seat_position=seat
    )
    records.reset_for_next_match()
    try:
        run_game(session, context, Dispatcher({GAME_TYPE: agent_factory}))
    finally:
        records.reset_for_next_match()
    role = engine.roles[seat]
    return PracticeResult(
        seed=seed,
        seat=seat,
        role=role,
        winner=engine.winner,
        our_side_won=side_of(role) == engine.winner,
        days_played=engine.day,
        unhandled_error=_unhandled_error_recorded(session_id, seat),
        mix=mix,
        opponent_seats=seating,
        session_id=session_id,
        termination_reason=engine.termination_reason,
        engine=engine,
    )


def _unhandled_error_recorded(session_id: str, seat: int) -> bool:
    """Whether the record of the game played in `seat` holds an `error` event of type `unhandled`."""
    runs = runs_folder(repository_root() / records.RUNS_DIR_NAME)
    folders = runs.glob(f"*/{match_folder_name(session_id, seat_label(seat))}")
    return any(
        error.get("type") == metrics.UNHANDLED_ERROR
        for folder in folders
        for error in metrics.load_match(folder).data_of("error")
    )
