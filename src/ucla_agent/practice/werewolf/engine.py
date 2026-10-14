"""The rules of one Werewolf game at the local practice table: seven seats, played offline.

Everything here comes from the platform's published guide
(`docs/reference/altruagent/werewolf.md`, sections 1 to 9 and 11); no platform
code was looked at. The engine knows nothing of transports or of which seat
the agent plays: it deals the roles, hands out turns one seat at a time,
applies moves, chats and terminates, keeps the public record and the seer's
results, and refuses what the rules refuse with the platform's error code
(`RuleError`). Given the seed and the same decisions in the same order, a game
always plays out the same way.

Time enters in two places, both read from the injected `clock` (seconds; by
default the wall clock) and applied only when `apply_timeouts` is called: a
discussion window closes 120 s after it opened, and a seat whose move is due
for more than 120 s gets the platform's auto-action.

Points the guide leaves open, decided here:

- Night 1 has no wolf turn: only the seer acts. From night 2 the living
  wolves name a victim one at a time in seat order, then the seer looks, then
  the kill lands at dawn. The victim is the seat named most often; a tie (the
  1-1 split) goes to the lower seat.
- Day voters act one at a time in seat order. Only votes for a seat count
  towards plurality; a day where the two highest counts are equal, or where
  every voter abstained, eliminates nobody.
- The sides are checked after every death: no living wolf is a village win,
  and living wolves at least as many as the other living seats is a wolf win.
  A day without an elimination is then counted for the stall rule; the third
  in a row is a wolf win.
- `state_version` moves with every move that is applied (including an
  auto-action) and with a resignation; chats, terminates and a window closing
  do not move it, as the guide says of the platform.
- A discussion window opens at dawn with the day-vote turn order already
  set, so the first voter is the current actor while the window is open, as
  on the platform; a move then is refused with `WRONG_PHASE`.
- A chat is checked in this order: game over (`GAME_ALREADY_COMPLETE`), no
  window open (`WRONG_PHASE`), a dead sender (`PLAYER_ELIMINATED`), the
  recipients (`INVALID_RECIPIENTS`), empty content (`INVALID_CONTENT`), more
  than 1024 characters (`MESSAGE_TOO_LONG`), more than 50 words
  (`TOO_MANY_WORDS`), a sixth chat (`MESSAGES_QUOTA_EXCEEDED`). A terminate
  is checked for the first three only, and a second terminate is a no-op.
  A seat that has terminated may still chat while the window is open.
- Terminates are messages like chats, broadcast and empty. When the window's
  time is up, an empty terminate is posted for every living seat that has not
  sent one. The guide's separate 120 s messaging inactivity is not modeled:
  every clock restarts when the window opens, so the window's own limit always
  gets there first.
- A move's inactivity clock starts when its seat becomes the actor in the
  moving phase: after the previous move, or when the window closes for the
  first voter. A move due for more than 120 s is replaced by abstain by day
  and the first legal target by night.
- After a resignation the resigner's side loses: the resigner scores -1, the
  rest of its side 0, the other side +1.
"""

from __future__ import annotations

import random
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

SEAT_COUNT = 7
SEATS = tuple(range(SEAT_COUNT))
WOLF, SEER, VILLAGER = "wolf", "seer", "villager"
ROLES = (WOLF, SEER, VILLAGER)
DEAL = (WOLF, WOLF, SEER, VILLAGER, VILLAGER, VILLAGER, VILLAGER)
# The action that abstains in a day vote; every other action is the seat it points at.
ABSTAIN = 7
NIGHT_WOLF, NIGHT_SEER, DAY_VOTE = "night_wolf", "night_seer", "day_vote"
# The platform's phase: whether `send_message` or `play_action` is the tool right now.
MESSAGING, MOVING = "messaging", "moving"
NIGHT_KILL, VOTE = "night_kill", "vote"
CHAT, TERMINATE = "chat", "terminate"
COMPLETED, RESIGNATION = "completed", "resignation"
FIRST_DAY = 1
CHATS_PER_WINDOW = 5
MAX_WORDS = 50
MAX_CHARACTERS = 1024
WINDOW_LIMIT_SECONDS = 120.0
INACTIVITY_LIMIT_SECONDS = 120.0
DAYS_WITHOUT_ELIMINATION_FOR_WOLF_WIN = 3
WIN_RETURN, LOSS_RETURN, RESIGNER_SIDE_RETURN = 1.0, -1.0, 0.0

GAME_ALREADY_COMPLETE = "GAME_ALREADY_COMPLETE"
PLAYER_ELIMINATED = "PLAYER_ELIMINATED"
WRONG_PHASE = "WRONG_PHASE"
NOT_YOUR_TURN = "NOT_YOUR_TURN"
INVALID_ACTION = "INVALID_ACTION"
INVALID_RECIPIENTS = "INVALID_RECIPIENTS"
INVALID_CONTENT = "INVALID_CONTENT"
MESSAGE_TOO_LONG = "MESSAGE_TOO_LONG"
TOO_MANY_WORDS = "TOO_MANY_WORDS"
MESSAGES_QUOTA_EXCEEDED = "MESSAGES_QUOTA_EXCEEDED"


class RuleError(Exception):
    """A move, message or resignation the rules refuse; `code` is the platform's error code for it."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def side_of(role: str) -> str:
    """The side a role plays for: `wolf` for a wolf, `villager` for the seer and the villagers."""
    return WOLF if role == WOLF else VILLAGER


def deal_roles(seed: int, forced_role: str | None = None, forced_seat: int | None = None) -> tuple[str, ...]:
    """The role of each seat, dealt by `random.Random(seed)`.

    With `forced_role`, `forced_seat` gets that role and the other six roles
    are shuffled into the other seats from the same seed, so the same seed,
    role and seat always give the same deal.
    """
    generator = random.Random(seed)
    if forced_role is None:
        if forced_seat is not None:
            raise ValueError("forced_seat is given without a forced_role")
        roles = list(DEAL)
        generator.shuffle(roles)
        return tuple(roles)
    if forced_role not in ROLES or forced_seat not in SEATS:
        raise ValueError(
            f"a forced role is one of {ROLES} at a seat in {SEATS}, not {forced_role!r} at {forced_seat!r}"
        )
    others = list(DEAL)
    others.remove(forced_role)
    generator.shuffle(others)
    others.insert(forced_seat, forced_role)
    return tuple(others)


@dataclass(frozen=True)
class Death:
    """One death of the public record: who died, their true role, on which day and how."""

    seat: int
    role: str
    day: int
    cause: str


@dataclass(frozen=True)
class DayVote:
    """One resolved day vote: each voter's action (a seat, or `ABSTAIN`) and the seat eliminated, if any."""

    day: int
    votes: dict[int, int]
    eliminated: int | None


@dataclass(frozen=True)
class Message:
    """One chat or terminate. `recipients` is empty for a broadcast and holds one seat for a private chat."""

    seq: int
    day: int
    sender: int
    recipients: tuple[int, ...]
    type: str
    content: str
    state_version: int
    sent_at: float

    def visible_to(self, seat: int) -> bool:
        """Whether the seat may read the message: a broadcast, or a private chat it sent or received."""
        return not self.recipients or seat == self.sender or seat in self.recipients


@dataclass
class _Window:
    """The open discussion window of a day."""

    opened_at: float
    chats: Counter[int] = field(default_factory=Counter)
    terminated: set[int] = field(default_factory=set)


class WerewolfEngine:
    """One game of Werewolf under the platform's rules. See the module's note for the points decided here."""

    def __init__(
        self,
        seed: int,
        forced_role: str | None = None,
        forced_seat: int | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """A dealt game at night 1, waiting on the seer. `clock` gives the time in seconds."""
        self.roles = deal_roles(seed, forced_role, forced_seat)
        self._clock = clock
        self.day = FIRST_DAY
        self.sub_phase: str | None = None
        self.platform_phase = MOVING
        self.state_version = 0
        self.winner: str | None = None
        self.termination_reason: str | None = None
        self.resigned_seat: int | None = None
        self.deaths: list[Death] = []
        self.vote_history: list[DayVote] = []
        # The seer's results in the order they were made: (seat looked at, wolf or villager).
        self.investigations: list[tuple[int, str]] = []
        self.messages: list[Message] = []
        self.window: _Window | None = None
        self._to_act: list[int] = []
        self._kill_choices: dict[int, int] = {}
        self._votes: dict[int, int] = {}
        self._days_without_elimination = 0
        self._turn_started_at = clock()
        self._begin_seer_turn()

    # -- what the game looks like ----------------------------------------------------------------

    @property
    def is_over(self) -> bool:
        """Whether the game has a winner."""
        return self.winner is not None

    def is_alive(self, seat: int) -> bool:
        """Whether the seat is still in the game."""
        return all(death.seat != seat for death in self.deaths)

    def living_seats(self) -> list[int]:
        """The living seats, in seat order."""
        return [seat for seat in SEATS if self.is_alive(seat)]

    def wolves(self) -> list[int]:
        """The wolf seats, dead or alive, in seat order."""
        return [seat for seat in SEATS if self.roles[seat] == WOLF]

    def current_actor(self) -> int | None:
        """The seat whose move the game waits for, or None when the game is over."""
        return self._to_act[0] if self._to_act and not self.is_over else None

    def legal_actions(self, seat: int) -> list[int]:
        """The actions the seat may take now, in seat order with `ABSTAIN` last; empty when it is not its turn."""
        return self._targets(seat) if seat == self.current_actor() else []

    def window_waiting_on(self) -> list[int]:
        """The living seats that have not terminated in the open window; empty when no window is open."""
        if self.window is None:
            return []
        return [seat for seat in self.living_seats() if seat not in self.window.terminated]

    def chats_left(self, seat: int) -> int:
        """How many more chats the seat may send in the open window; 0 when no window is open."""
        return CHATS_PER_WINDOW - self.window.chats[seat] if self.window is not None else 0

    def window_deadline(self) -> float | None:
        """The clock time at which the open window closes by itself; None when no window is open."""
        return self.window.opened_at + WINDOW_LIMIT_SECONDS if self.window is not None else None

    def now(self) -> float:
        """The engine's clock reading."""
        return self._clock()

    def returns(self) -> dict[int, float] | None:
        """Each seat's score once the game is over, else None."""
        if not self.is_over:
            return None
        return {seat: self._return_of(seat) for seat in SEATS}

    # -- moves -----------------------------------------------------------------------------------

    def play(self, seat: int, action: int) -> None:
        """Apply the seat's move: a kill, an investigation or a vote, by the current sub-phase."""
        self._check_may_act(seat)
        if self.platform_phase == MESSAGING:
            raise RuleError(WRONG_PHASE, "the discussion window is open: send messages, not moves")
        if seat != self.current_actor():
            raise RuleError(NOT_YOUR_TURN, f"the game waits for seat {self.current_actor()}, not seat {seat}")
        if action not in self._targets(seat):
            raise RuleError(INVALID_ACTION, f"{action!r} is not one of the legal actions {self._targets(seat)}")
        self._apply(seat, action)

    def resign(self, seat: int) -> None:
        """End the game with the seat's side losing."""
        self._check_may_act(seat)
        self.resigned_seat = seat
        self.state_version += 1
        self._end(VILLAGER if side_of(self.roles[seat]) == WOLF else WOLF, RESIGNATION)

    def apply_timeouts(self) -> None:
        """Close a window whose time is up, or make the auto-action of a move due for too long."""
        if self.is_over:
            return
        now = self._clock()
        if self.window is not None:
            if now - self.window.opened_at >= WINDOW_LIMIT_SECONDS:
                self._close_window()
            return
        if now - self._turn_started_at > INACTIVITY_LIMIT_SECONDS:
            actor = self._to_act[0]
            self._apply(actor, ABSTAIN if self.sub_phase == DAY_VOTE else self._targets(actor)[0])

    # -- messages --------------------------------------------------------------------------------

    def send_chat(self, seat: int, content: str, recipients: Iterable[int] | None = None) -> Message:
        """Post a chat in the open window: a broadcast, or a private chat when one recipient is given."""
        self._check_in_window(seat)
        private_to = _checked_recipients(seat, recipients)
        _check_chat_content(content)
        if self.window.chats[seat] >= CHATS_PER_WINDOW:
            raise RuleError(MESSAGES_QUOTA_EXCEEDED, f"seat {seat} has sent its {CHATS_PER_WINDOW} chats")
        self.window.chats[seat] += 1
        return self._post(seat, private_to, CHAT, content)

    def terminate(self, seat: int) -> Message | None:
        """End the seat's part in the open window; the window closes once every living seat has. None if repeated."""
        self._check_in_window(seat)
        if seat in self.window.terminated:
            return None
        self.window.terminated.add(seat)
        message = self._post(seat, (), TERMINATE, "")
        if not self.window_waiting_on():
            self._close_window()
        return message

    # -- the course of the game ------------------------------------------------------------------

    def _check_may_act(self, seat: int) -> None:
        if self.is_over:
            raise RuleError(GAME_ALREADY_COMPLETE, "the game is over")
        if not self.is_alive(seat):
            raise RuleError(PLAYER_ELIMINATED, f"seat {seat} is out of the game")

    def _check_in_window(self, seat: int) -> None:
        if self.is_over:
            raise RuleError(GAME_ALREADY_COMPLETE, "the game is over")
        if self.window is None:
            raise RuleError(WRONG_PHASE, "no discussion window is open")
        if not self.is_alive(seat):
            raise RuleError(PLAYER_ELIMINATED, f"seat {seat} is out of the game")

    def _targets(self, seat: int) -> list[int]:
        living = self.living_seats()
        if self.sub_phase == NIGHT_WOLF:
            return [target for target in living if self.roles[target] != WOLF]
        others = [target for target in living if target != seat]
        return [*others, ABSTAIN] if self.sub_phase == DAY_VOTE else others

    def _apply(self, seat: int, action: int) -> None:
        self._to_act.pop(0)
        self.state_version += 1
        if self.sub_phase == NIGHT_WOLF:
            self._take_kill_choice(seat, action)
        elif self.sub_phase == NIGHT_SEER:
            self._take_investigation(action)
        else:
            self._take_vote(seat, action)
        self._turn_started_at = self._clock()

    def _take_kill_choice(self, seat: int, target: int) -> None:
        self._kill_choices[seat] = target
        if not self._to_act:
            self._begin_seer_turn()

    def _take_investigation(self, target: int) -> None:
        self.investigations.append((target, side_of(self.roles[target])))
        self._dawn()

    def _take_vote(self, seat: int, target: int) -> None:
        self._votes[seat] = target
        if not self._to_act:
            self._resolve_vote()

    def _begin(self, sub_phase: str, seats: Sequence[int]) -> None:
        self.sub_phase = sub_phase
        self._to_act = list(seats)

    def _begin_seer_turn(self) -> None:
        seer = self.roles.index(SEER)
        if self.is_alive(seer):
            self._begin(NIGHT_SEER, [seer])
        else:
            self._dawn()

    def _dawn(self) -> None:
        victim = _night_victim(self._kill_choices)
        self._kill_choices = {}
        if victim is not None:
            self._kill(victim, NIGHT_KILL)
        if not self.is_over:
            self._open_window()

    def _open_window(self) -> None:
        self.platform_phase = MESSAGING
        self.window = _Window(self._clock())
        self._begin(DAY_VOTE, self.living_seats())

    def _close_window(self) -> None:
        for seat in self.window_waiting_on():
            self.window.terminated.add(seat)
            self._post(seat, (), TERMINATE, "")
        self.window = None
        self.platform_phase = MOVING
        self._turn_started_at = self._clock()

    def _resolve_vote(self) -> None:
        eliminated = _vote_outcome(self._votes)
        self.vote_history.append(DayVote(self.day, self._votes, eliminated))
        self._votes = {}
        if eliminated is not None:
            self._days_without_elimination = 0
            self._kill(eliminated, VOTE)
        else:
            self._days_without_elimination += 1
            if self._days_without_elimination >= DAYS_WITHOUT_ELIMINATION_FOR_WOLF_WIN:
                self._end(WOLF)
        if not self.is_over:
            self._start_night()

    def _start_night(self) -> None:
        self.day += 1
        self._begin(NIGHT_WOLF, [seat for seat in self.wolves() if self.is_alive(seat)])

    def _kill(self, seat: int, cause: str) -> None:
        self.deaths.append(Death(seat, self.roles[seat], self.day, cause))
        living = self.living_seats()
        living_wolves = sum(1 for seat_alive in living if self.roles[seat_alive] == WOLF)
        if living_wolves == 0:
            self._end(VILLAGER)
        elif living_wolves >= len(living) - living_wolves:
            self._end(WOLF)

    def _end(self, winner: str, reason: str = COMPLETED) -> None:
        self.winner = winner
        self.termination_reason = reason
        self.sub_phase = None
        self._to_act = []
        self.window = None
        self.platform_phase = MOVING

    def _post(self, seat: int, recipients: tuple[int, ...], kind: str, content: str) -> Message:
        message = Message(
            len(self.messages), self.day, seat, recipients, kind, content, self.state_version, self._clock()
        )
        self.messages.append(message)
        return message

    def _return_of(self, seat: int) -> float:
        if side_of(self.roles[seat]) == self.winner:
            return WIN_RETURN
        if self.resigned_seat is not None and seat != self.resigned_seat:
            return RESIGNER_SIDE_RETURN
        return LOSS_RETURN


def _night_victim(choices: dict[int, int]) -> int | None:
    """The seat named most often by the wolves, the lower seat on a tie; None when no wolf chose (night 1)."""
    if not choices:
        return None
    counts = Counter(choices.values())
    return min(counts, key=lambda target: (-counts[target], target))


def _vote_outcome(votes: dict[int, int]) -> int | None:
    """The seat with the most votes; None when every voter abstained or the two highest counts are equal."""
    counts = Counter(target for target in votes.values() if target != ABSTAIN)
    leaders = counts.most_common(2)
    if not leaders or (len(leaders) > 1 and leaders[0][1] == leaders[1][1]):
        return None
    return leaders[0][0]


def _checked_recipients(seat: int, recipients: Iterable[int] | None) -> tuple[int, ...]:
    """The recipients of a chat: none for a broadcast, or one other seat of the table."""
    chosen = tuple(recipients or ())
    if len(chosen) > 1 or any(not _is_other_seat(recipient, seat) for recipient in chosen):
        raise RuleError(INVALID_RECIPIENTS, f"recipients are [] or one other seat of {list(SEATS)}, not {chosen}")
    return chosen


def _is_other_seat(value: object, seat: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value in SEATS and value != seat


def _check_chat_content(content: object) -> None:
    if not isinstance(content, str) or not content.strip():
        raise RuleError(INVALID_CONTENT, "a chat needs text")
    if len(content) > MAX_CHARACTERS:
        raise RuleError(MESSAGE_TOO_LONG, f"a chat has at most {MAX_CHARACTERS} characters, not {len(content)}")
    if len(content.split()) > MAX_WORDS:
        raise RuleError(TOO_MANY_WORDS, f"a chat has at most {MAX_WORDS} words, not {len(content.split())}")
