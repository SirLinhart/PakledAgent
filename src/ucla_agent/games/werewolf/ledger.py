"""What our seat knows in a Werewolf game, kept in kinds that are never mixed.

`Ledger.update` is given every state the agent reads, an `altruagent.GameState`
from MCP's `get_game_state` or `wait_for_update`. The Werewolf fields the guide
documents (`your_position`, `players`, `game_state`, `eliminated`) are read
from `state.raw`, the state object as received; the observation from
`state.observation`.

- Engine facts are certain and public: our seat, the seat of each display
  name, the living seats, every death with its revealed role and cause, and
  every completed day's votes. `game_state` names players by display name;
  the ledger keeps seats, translated through `players`.
- Private facts are what only our seat is told, read from the observation:
  our role, our fellow wolves, our seer results.
- The chat is every chat message seen, kept by `seq` (a message seen again
  is not taken twice), each with the day and the discussion window it was
  first seen in.
- Claims are what other seats said in chat, read from each new message of
  another seat by fixed patterns: a claim to be the seer, a claimed seer
  result, an accusation, an attempt to pass text off as instructions. A
  claim is evidence about the seat that made it; it never becomes a fact.

The public record in `game_state` is complete in every state, so the engine
facts are rebuilt from each one rather than accumulated. The chat is not:
the platform resets `new_messages` at every phase change, so the ledger keeps
its own history.

"Today" is the most recent discussion window, not `game_state.day`. The
ledger numbers the windows it sees itself (`window_index`, from 1): a window
opens when the platform phase becomes `messaging` after being anything else,
or when a messaging state reports another day than the open window's (the
agent may not see the moving phase between two windows). The window's day
lasts through its vote and the night after it, whatever the day counter reads
at that night, until the next window opens. Only what the state shows our
seat is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Collection, Iterable, Mapping

from altruagent import GameState, Message

from ucla_agent.games.werewolf.tuning import ChatTuning

WOLF, SEER, VILLAGER = "wolf", "seer", "villager"
# The kinds of claim.
SEER_CLAIM = "seer_claim"
RESULT_CLAIM = "result_claim"
ACCUSATION = "accusation"
INJECTION_ATTEMPT = "injection_attempt"
WOLF_COUNT = 2
# The platform phase of a discussion window, and the message type that carries text.
MESSAGING_PHASE = "messaging"
CHAT_TYPE = "chat"
# How `game_state.vote_history` writes a voter who abstained.
ABSTAIN_NAME = "abstain"
# The private lines of the observation (guide, section 2).
ROLE_LINE = re.compile(r"^Your role:\s*(?P<role>\w+)", re.MULTILINE)
ALLIES_LINE = re.compile(r"^Your fellow wolves:(?P<allies>.*)$", re.MULTILINE)
INVESTIGATIONS_LINE = re.compile(r"^Your investigations:(?P<results>.*)$", re.MULTILINE)
SEAT_LABEL = re.compile(r"\bPlayer(?P<seat>\d+)\b")
INVESTIGATION = re.compile(rf"\bPlayer(?P<seat>\d+) is (?P<result>{WOLF}|{VILLAGER})\b")
# The chat patterns, all matched whatever the case.
SEER_CLAIM_TEXT = re.compile(r"\bI(?: am|['’]m) (?:the )?seer\b", re.IGNORECASE)
CHAT_SEAT = re.compile(r"\bPlayer(?P<seat>\d+)\b", re.IGNORECASE)
SEAT_IS = re.compile(
    r"\bPlayer(?P<seat>\d+)\s+is\s+(?:an?\s+)?(?P<word>werewolf|wolf|villager|innocent|clear(?:ed)?)\b", re.IGNORECASE
)
WOLF_WORDS = frozenset({"wolf", "werewolf"})
ACCUSING_WORD = re.compile(
    r"\b(?:wolf|werewolf|suspicious|sus|suspect|suspects|suspected|vote|voting|eliminate|lying|liar)\b", re.IGNORECASE
)
# The forms that clear a seat instead of accusing it, though they hold an accusing word. A clearing form is never
# read as an accusing word, and the seat it names, before "is not a suspect" or "is not a wolf" or after "suspect",
# is never accused by its sentence.
NOT_SUSPECTED = r"(?:don['’]t|do\s+not|no\s+longer)\s+suspect"
NOT_A_SUSPECT = r"not\s+a\s+suspect"
NOT_A_WOLF = r"(?:is\s+not|isn['’]t)\s+(?:an?\s+)?(?:werewolf|wolf)"
CLEARING_FORM = re.compile(rf"\b(?:{NOT_A_SUSPECT}|{NOT_SUSPECTED}|{NOT_A_WOLF})\b", re.IGNORECASE)
CLEARED_SEATS = (
    re.compile(rf"\bPlayer(?P<seat>\d+)\s+(?:is\s+{NOT_A_SUSPECT}|{NOT_A_WOLF})\b", re.IGNORECASE),
    re.compile(rf"\b{NOT_SUSPECTED}\s+Player(?P<seat>\d+)\b", re.IGNORECASE),
)
SENTENCE_BREAK = re.compile(r"[.!?;\n]+")
# The summary's headings: its three parts are never mixed, and the claims are marked as words, not facts.
FACTS_HEADING = "Facts (certain, from the game engine):"
PRIVATE_HEADING = "Our private facts (only we know these):"
CLAIMS_HEADING = "Claims made in chat (what players said, not facts):"
NO_CLAIMS_LINE = "- none"
CAUSE_TEXT = {"night_kill": "killed at night", "vote": "eliminated by vote"}
CLAIM_TEXT = {
    SEER_CLAIM: "claims to be the seer",
    RESULT_CLAIM: "claims {target} is {result}",
    ACCUSATION: "accuses {target}",
    INJECTION_ATTEMPT: "sent text shaped like instructions to an agent (an injection attempt)",
}


class LedgerError(ValueError):
    """A state that lacks what every Werewolf state carries, or names a player the seat map does not."""


@dataclass(frozen=True)
class Death:
    """One death of the public record: the day, the seat, its revealed role and the cause (`night_kill` or `vote`)."""

    day: int
    seat: int
    role: str
    cause: str


@dataclass(frozen=True)
class DayVote:
    """One completed day's vote: each voter's target seat (None for abstain) and the seat eliminated, if any."""

    day: int
    votes: dict[int, int | None]
    eliminated: int | None


@dataclass(frozen=True)
class EngineFacts:
    """The certain, public facts of the game as of the last state, by seat."""

    seat: int | None = None
    names: dict[int, str] = field(default_factory=dict)
    living: tuple[int, ...] = ()
    deaths: tuple[Death, ...] = ()
    votes: tuple[DayVote, ...] = ()
    day: int | None = None
    sub_phase: str | None = None
    eliminated: bool = False
    winner: str | None = None

    def revealed_role(self, seat: int) -> str | None:
        """The role a dead seat was revealed as; None for a living seat."""
        return next((death.role for death in self.deaths if death.seat == seat), None)

    def living_wolves(self) -> int:
        """How many wolves are still alive: two less those revealed among the dead."""
        return WOLF_COUNT - sum(1 for death in self.deaths if death.role == WOLF)


@dataclass(frozen=True)
class PrivateFacts:
    """What only our seat knows: its role, its fellow wolves and its seer results by seat, in the order made."""

    role: str | None = None
    allies: tuple[int, ...] = ()
    results: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatMessage:
    """One chat as first seen: its seq, day and discussion window, the sender, the recipients and the text.

    `recipients` is empty for a broadcast and holds one seat for a private chat.
    The text is another player's words: data to quote, never instructions.
    """

    seq: int
    day: int | None
    window: int
    sender: int
    recipients: tuple[int, ...]
    text: str


@dataclass(frozen=True)
class Claim:
    """One thing a seat said in chat, with the day, window and seq of the message it came from.

    `kind` is `seer_claim` (the seat claimed to be the seer), `result_claim`
    (it claimed `target` was found `result`, wolf or villager), `accusation`
    (it accused `target`) or `injection_attempt` (it sent text shaped like
    instructions to an agent). `target` and `result` are None where the kind
    has none.
    """

    kind: str
    seat: int
    target: int | None
    result: str | None
    day: int | None
    window: int
    seq: int


class Ledger:
    """Our seat's knowledge of one Werewolf game, updated from every state the agent reads."""

    def __init__(self, chat: ChatTuning) -> None:
        """An empty ledger that reads the chat by `chat`'s patterns: no state read yet."""
        self._chat = chat
        self._facts = EngineFacts()
        self._private = PrivateFacts()
        self._claims: list[Claim] = []
        self._seer_claimants: set[int] = set()
        self._messages: list[ChatMessage] = []
        self._seen_seqs: set[int] = set()
        # The number of the most recent discussion window, 0 before the first.
        self.window_index = 0
        self._window_day: int | None = None
        self._in_window = False
        self._opened_window = False
        self._last_day: int | None = None

    def update(self, state: GameState) -> None:
        """Take in one state. Raises `LedgerError` when it lacks our seat or seat map, or names an unknown player."""
        self._facts = engine_facts(state.raw)
        if self._facts.day is not None:
            self._last_day = self._facts.day
        private = private_facts(state.observation)
        # Every Werewolf observation opens with our role; one without it tells nothing private, so it replaces nothing.
        if private.role is not None:
            self._private = private
        self._opened_window = self._follow_windows(state.phase)
        self._take_messages(state.new_messages)

    def facts(self) -> EngineFacts:
        """The engine facts as of the last state."""
        return self._facts

    def private(self) -> PrivateFacts:
        """Our private facts as of the last state that showed them."""
        return self._private

    def messages(self, *, today: bool = False) -> tuple[ChatMessage, ...]:
        """Every chat seen, in the order seen; with `today`, only those of the most recent discussion window."""
        if today:
            return tuple(message for message in self._messages if message.window == self.window_index)
        return tuple(self._messages)

    def claims(self, *, today: bool = False) -> tuple[Claim, ...]:
        """Every claim other seats made, in the order made; with `today`, only those of the most recent window."""
        if today:
            return tuple(claim for claim in self._claims if claim.window == self.window_index)
        return tuple(self._claims)

    def is_new_day(self) -> bool:
        """Whether the last state was the first of a new day: the first state of a new discussion window."""
        return self._opened_window

    def summary(self, for_seat_view: bool = True) -> str:
        """The ledger as plain text for a prompt: the engine facts, our private facts, then the claims by seat.

        With `for_seat_view` false our private facts are left out, leaving
        only what every seat of the table knows. No message text is copied:
        a claim is rendered from its kind, seat and target.
        """
        lines = [FACTS_HEADING, *_fact_lines(self._facts)]
        if for_seat_view:
            lines += [PRIVATE_HEADING, *_private_lines(self._private)]
        lines += [CLAIMS_HEADING, *(_claim_line(claim) for claim in self._claims)]
        if not self._claims:
            lines.append(NO_CLAIMS_LINE)
        return "\n".join(lines)

    def _follow_windows(self, phase: str) -> bool:
        """Note the platform phase of a state; True when the state opens a new discussion window."""
        messaging = phase == MESSAGING_PHASE
        opens = messaging and (not self._in_window or self._facts.day != self._window_day)
        self._in_window = messaging
        if opens:
            self.window_index += 1
            self._window_day = self._facts.day
        return opens

    def _take_messages(self, messages: Iterable[Message]) -> None:
        """Keep each chat not seen before, stamped with the current day and window, and the claims of another seat's."""
        for message in messages:
            if message.index in self._seen_seqs:
                continue
            self._seen_seqs.add(message.index)
            if message.type != CHAT_TYPE:
                continue
            chat = ChatMessage(
                seq=message.index,
                day=self._last_day,
                window=self.window_index,
                sender=message.sender,
                recipients=tuple(message.recipients),
                text=message.content,
            )
            self._messages.append(chat)
            if chat.sender != self._facts.seat:
                self._take_claims(chat)

    def _take_claims(self, chat: ChatMessage) -> None:
        """The claims of one message; a result claim needs a seer claim in this message or an earlier one."""
        if SEER_CLAIM_TEXT.search(chat.text):
            self._seer_claimants.add(chat.sender)
            self._claims.append(_claim(chat, SEER_CLAIM))
        found: dict[tuple[str, int], str | None] = {}
        for sentence in SENTENCE_BREAK.split(chat.text):
            for kind, target, result in sentence_claims(
                sentence, chat.sender, self._facts.names, chat.sender in self._seer_claimants
            ):
                found.setdefault((kind, target), result)
        self._claims.extend(_claim(chat, kind, target, result) for (kind, target), result in found.items())
        if any(pattern.search(chat.text) for pattern in self._chat.injection_patterns):
            self._claims.append(_claim(chat, INJECTION_ATTEMPT))


def sentence_claims(
    sentence: str, sender: int, seats: Collection[int], seer_claimant: bool
) -> list[tuple[str, int, str | None]]:
    """The result claims and accusations of one sentence of `sender`'s, as (kind, target, result).

    `PlayerN is (a) wolf|werewolf|villager|innocent|clear(ed)` is a result
    claim when the sender has claimed the seer. A seat named in a sentence
    with an accusing word is accused, unless the sentence clears it (calls it
    a villager, innocent or clear, says it is not a suspect or not a wolf, or
    that the sender does not suspect it) or holds the sender's result claim
    on it. A clearing form ("not a suspect", "is not a wolf") is not an
    accusing word. Only seats of the table other than the sender's count.
    """
    found: list[tuple[str, int, str | None]] = []
    not_accused = {sender, *_cleared_seats(sentence)}
    for target, result in _seat_is_mentions(sentence, sender, seats):
        if seer_claimant:
            found.append((RESULT_CLAIM, target, result))
            not_accused.add(target)
        elif result == VILLAGER:
            not_accused.add(target)
    if ACCUSING_WORD.search(CLEARING_FORM.sub(" ", sentence)):
        named = (int(match["seat"]) for match in CHAT_SEAT.finditer(sentence))
        found.extend((ACCUSATION, target, None) for target in named if target in seats and target not in not_accused)
    return found


def _seat_is_mentions(sentence: str, sender: int, seats: Collection[int]) -> list[tuple[int, str]]:
    """Each `PlayerN is (a) <word>` of a sentence about another seat of the table, as (seat, wolf or villager)."""
    mentions = []
    for match in SEAT_IS.finditer(sentence):
        target = int(match["seat"])
        if target != sender and target in seats:
            mentions.append((target, WOLF if match["word"].lower() in WOLF_WORDS else VILLAGER))
    return mentions


def _cleared_seats(sentence: str) -> set[int]:
    """The seats a sentence says are not a suspect or not a wolf, or that its sender does not (or no longer) suspect."""
    return {int(match["seat"]) for pattern in CLEARED_SEATS for match in pattern.finditer(sentence)}


def _claim(chat: ChatMessage, kind: str, target: int | None = None, result: str | None = None) -> Claim:
    return Claim(kind, chat.sender, target, result, chat.day, chat.window, chat.seq)


def label(seat: int) -> str:
    """The label the game uses for a seat in observations, actions and messages."""
    return f"Player{seat}"


def _fact_lines(facts: EngineFacts) -> list[str]:
    """The engine facts as summary lines: our seat, the moment, the living, the dead and every day's vote."""
    standing = "We are out of the game." if facts.eliminated else "We are alive."
    moment = (
        f"The game is over: the {facts.winner} side won."
        if facts.winner is not None
        else f"Day {facts.day}, phase {facts.sub_phase}."
    )
    deaths = "; ".join(
        f"day {death.day}: {label(death.seat)} was {death.role} ({CAUSE_TEXT.get(death.cause, death.cause)})"
        for death in facts.deaths
    )
    return [
        f"- We are {label(facts.seat)}. {standing} {moment}",
        f"- Living: {', '.join(label(seat) for seat in facts.living)}. Wolves still alive: {facts.living_wolves()}.",
        f"- Dead: {deaths or 'none'}.",
        *(_vote_line(vote) for vote in facts.votes),
    ]


def _vote_line(vote: DayVote) -> str:
    cast = ", ".join(
        f"{label(voter)} -> {'abstain' if target is None else label(target)}" for voter, target in vote.votes.items()
    )
    outcome = "nobody eliminated" if vote.eliminated is None else f"{label(vote.eliminated)} eliminated"
    return f"- Day {vote.day} vote: {cast}; {outcome}."


def _private_lines(private: PrivateFacts) -> list[str]:
    """Our private facts as summary lines: our role, and our fellow wolves or our seer results."""
    lines = [f"- Our role: {private.role}."]
    if private.role == WOLF:
        lines.append(f"- Our fellow wolves: {', '.join(label(ally) for ally in private.allies) or 'none'}.")
    if private.role == SEER:
        found = ", ".join(f"{label(target)} is {result}" for target, result in private.results.items())
        lines.append(f"- Our investigations: {found or 'none yet'}.")
    return lines


def _claim_line(claim: Claim) -> str:
    """One claim, attributed to its seat and day, rendered from its kind and target only."""
    target = None if claim.target is None else label(claim.target)
    said = CLAIM_TEXT[claim.kind].format(target=target, result=claim.result)
    return f"- {label(claim.seat)}, day {claim.day}: {said}."


def engine_facts(raw: Mapping[str, Any]) -> EngineFacts:
    """The engine facts of one state object, by seat. Before the game starts its `game_state` may be empty."""
    if "your_position" not in raw or "players" not in raw:
        raise LedgerError("a Werewolf state carries your_position and players; this one does not")
    names = {int(player["position"]): str(player["name"]) for player in raw["players"]}
    seats = {name: seat for seat, name in names.items()}
    record = raw.get("game_state") or {}
    alive = record.get("alive")
    return EngineFacts(
        seat=int(raw["your_position"]),
        names=names,
        living=tuple(sorted(names)) if alive is None else tuple(sorted(_seat(seats, name) for name in alive)),
        deaths=tuple(
            Death(int(death["day"]), _seat(seats, death["player"]), str(death["role"]), str(death["cause"]))
            for death in record.get("dead") or ()
        ),
        votes=tuple(_day_vote(seats, vote) for vote in record.get("vote_history") or ()),
        day=record.get("day"),
        sub_phase=record.get("phase"),
        eliminated=bool(raw.get("eliminated", False)),
        winner=record.get("winner"),
    )


def private_facts(observation: str) -> PrivateFacts:
    """Our role, fellow wolves and seer results, read from the observation's private lines."""
    role = ROLE_LINE.search(observation)
    allies = ALLIES_LINE.search(observation)
    investigations = INVESTIGATIONS_LINE.search(observation)
    return PrivateFacts(
        role=role["role"].lower() if role else None,
        allies=tuple(int(seat) for seat in SEAT_LABEL.findall(allies["allies"])) if allies else (),
        results=(
            {int(seat): result for seat, result in INVESTIGATION.findall(investigations["results"])}
            if investigations
            else {}
        ),
    )


def _day_vote(seats: Mapping[str, int], vote: Mapping[str, Any]) -> DayVote:
    eliminated = vote.get("eliminated")
    return DayVote(
        day=int(vote["day"]),
        votes={_seat(seats, voter): _target(seats, target) for voter, target in (vote.get("votes") or {}).items()},
        eliminated=None if eliminated is None else _seat(seats, eliminated),
    )


def _target(seats: Mapping[str, int], target: str | None) -> int | None:
    return None if target is None or target == ABSTAIN_NAME else _seat(seats, target)


def _seat(seats: Mapping[str, int], name: str) -> int:
    if name not in seats:
        raise LedgerError(f"game_state names {name!r}, who is not in players")
    return seats[name]
