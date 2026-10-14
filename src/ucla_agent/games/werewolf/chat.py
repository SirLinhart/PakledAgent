"""Werewolf chat: the messages a seat plans for a discussion window and the text each one goes out with.

The runner asks for a message every time something changes in an open
window and needs a chat or a terminate each time; it has no way to wait.
`Chat.decide` therefore sends, at each call, the next message of the
window's plan that is due, in the order: the persona's line (see below), the
public opening, the private word to our living ally (a wolf only), one public
reply. The reply is due once another seat has accused ours after our last
public message, or, for the seer, once it has to reveal itself and the
opening did not. A call with nothing due terminates our part in the window,
and every later call of the window terminates again. An eliminated seat only
ever terminates.

With `[chat] pakled_persona` on, a villager or a wolf plays a Pakled from
Star Trek. The first message of each of its windows is then one of
`PAKLED_LINES`, in an order drawn at random for the match so that no line
comes twice before every one has come; it takes one of the window's
`max_chats_per_window`. A model writing one of its other messages is asked
for a Pakled voice (`prompts.PAKLED_VOICE`), and a scripted public message
starts with `PAKLED_PREFIX`. A seer claim that names a wolf, our seer's
reveal of a wolf it found or a wolf's counterclaim, opens with one of
`PAKLED_SEER_CLAIMS` at random, followed by the plain claim of the template:
the plain words are the ones other players' patterns read as a seer claim
and an accusation. Such a claim is always the scripted one. The seer has no
Pakled line and no Pakled voice, and when and whom it claims is unchanged.

A planned message has a scripted text, filled in from `[chat.templates]` of
the tuning file, and the points its text must carry (a seat label, the seer's
results). When the settings let a model write messages and the window leaves
it time, the `werewolf_message` chain is asked for the text instead. Whoever
wrote it, a text is sent only when it passes every check: not empty, within
the platform's 50 words and 1,024 characters, every required point present,
no pattern of an injection attempt, in a wolf's public message none of the
phrases that would give the wolves away, and in the public message of our
seer before it has said it is the seer (other than the message that says
so) none of the phrases that would hint at it. A text that fails is replaced
by the scripted one, which passes them all. Each chat sent is written to the
match record as a `werewolf_chat` event.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field, replace
from typing import Sequence

from altruagent import TERMINATE_MESSAGING, SendMessage
from altruagent.runner import MessageDecision

from ucla_agent.core import records
from ucla_agent.core.decide import DecisionSource, TextRequest
from ucla_agent.games.werewolf import prompts, village, wolves
from ucla_agent.games.werewolf.inference import Beliefs
from ucla_agent.games.werewolf.ledger import ACCUSATION, SEER, VILLAGER, WOLF, Ledger, label
from ucla_agent.games.werewolf.settings import WerewolfSettings
from ucla_agent.games.werewolf.tuning import ChatTemplates, ChatTuning

# The plan's items, in the order a window sends them.
PAKLED, OPENING, COORDINATION, REPLY = "pakled", "opening", "coordination", "reply"
# The Pakled persona's lines (Star Trek: The Next Generation, "Samaritan Snare"), in the user's own words. None names
# a seat or holds a claim the ledger reads, so a wolf says each as cover as well as a villager does.
PAKLED_LINES = (
    "We are Pakled, we are honest traders.",
    "We do nothing wrong",
    "Oh.. I don't know anything",
    "It is difficult to understand..",
    "We are not the werewolf, we are Pakled",
)
# The persona's words for a seer claim naming a wolf, {player} its seat label, in the user's own words.
PAKLED_SEER_CLAIMS = (
    "I am now Pakled seer, behold my giant helmet! {player} is werewolf!",
    "We are the seer yes.. werewolf is {player}",
)
# The start of a scripted public message of the persona.
PAKLED_PREFIX = "We think: "
# The roles that play the persona.
PAKLED_ROLES = frozenset({VILLAGER, WOLF})
# What a planned message says.
SUSPECT, SEER_REVEAL, COUNTERCLAIM, DEFENSE = "suspect", "seer_reveal", "counterclaim", "defense"
# The platform's limits on one chat.
MAX_WORDS = 50
MAX_CHARACTERS = 1024
MILLISECONDS_PER_SECOND = 1000
# The checks a text can fail, in the order they are made.
EMPTY = "empty"
TOO_MANY_WORDS = "too_many_words"
TOO_LONG = "too_long"
MISSING_POINT = "missing_point"
INJECTION_PATTERN = "injection_pattern"
FORBIDDEN_PHRASE = "forbidden_phrase"
SEER_HINT = "seer_hint"
# Why no model was asked for a text.
SCRIPTED_ONLY = "scripted_only"
MODEL_OFF = "model_messages_off"
TOO_LITTLE_TIME = "too_little_time"
# A right single quotation mark is read as the apostrophe it stands for.
APOSTROPHES = str.maketrans({"’": "'"})
CHAT_EVENT = "werewolf_chat"
# The seq below every message's: before our first public message, every accusation is after it.
NO_SEQ = -1


@dataclass(frozen=True)
class Planned:
    """One message of a window's plan: its item, what it says, its scripted text and the points it must carry.

    `instruction` is what a model is asked to write, None for a message that
    is always the scripted one. `recipients` is empty for a public message
    and holds our ally's seat for the private coordination message.
    """

    item: str
    kind: str
    template: str
    points: tuple[str, ...] = ()
    instruction: str | None = None
    recipients: tuple[int, ...] = ()


@dataclass(frozen=True)
class Written:
    """The text a planned message goes out with; `reason` says why it is not a model's, None when it is."""

    text: str
    model_used: bool
    reason: str | None


def suspect(item: str, target: int, templates: ChatTemplates) -> Planned:
    """Naming our current top vote candidate as the seat we suspect."""
    player = label(target)
    points = (player,)
    instruction = prompts.with_points(prompts.SUSPECT_INSTRUCTION.format(player=player), points)
    return Planned(item, SUSPECT, templates.suspect.format(player=player), points, instruction)


def seer_reveal(item: str, ledger: Ledger, vote_target: int, templates: ChatTemplates) -> Planned:
    """The seer's claim with every result so far, asking for a vote on `vote_target`."""
    results = " ".join(
        templates.seer_result.format(player=label(seat), result=found)
        for seat, found in ledger.private().results.items()
    )
    player = label(vote_target)
    # A seer with no result yet leaves the results empty; the spaces around them are closed up.
    text = " ".join(templates.seer_reveal.format(results=results, player=player).split())
    points = village.reveal_points(ledger)
    return Planned(
        item, SEER_REVEAL, text, points, prompts.with_points(prompts.REVEAL_INSTRUCTION.format(player=player), points)
    )


def counterclaim(item: str, accuser: int, templates: ChatTemplates) -> Planned:
    """A wolf's claim to be the seer, naming the seer claimant that accused our ally a wolf."""
    player = label(accuser)
    points = wolves.counterclaim_points(accuser)
    instruction = prompts.with_points(prompts.COUNTERCLAIM_INSTRUCTION.format(player=player), points)
    return Planned(item, COUNTERCLAIM, templates.counterclaim.format(player=player), points, instruction)


def defense(item: str, templates: ChatTemplates) -> Planned:
    """The answer to an accusation of our seat; it has no point it must carry."""
    return Planned(item, DEFENSE, templates.defense, (), prompts.with_points(prompts.DEFENSE_INSTRUCTION, ()))


def coordination(message: wolves.PrivateMessage) -> Planned:
    """The private word to our ally, always the scripted text."""
    return Planned(COORDINATION, COORDINATION, message.text, recipients=(message.recipient,))


def write_message(
    planned: Planned,
    ledger: Ledger,
    source: DecisionSource,
    settings: WerewolfSettings,
    seconds_left: int | None,
    state_tag: str,
    *,
    seer_revealed: bool = False,
) -> Written:
    """The text `planned` goes out with: a model's when one is asked and its text passes every check, else the
    scripted one.

    No model is asked for a message that is always scripted, while
    `model_messages` is off, or when the deadline left for it (see
    `message_deadline`) is under `[chat] min_deadline_ms`. `seer_revealed`
    says whether our seat has already said it is the seer: a message that
    must not hint at it (see `must_hide_seer`) is asked for with a system
    prompt that says so, and checked for the phrases that would. A seat
    playing the Pakled persona (see `plays_pakled`) asks for its voice, and
    its scripted text starts with `PAKLED_PREFIX`.
    """
    chat = settings.tuning.chat
    if planned.instruction is None:
        return Written(planned.template, False, SCRIPTED_ONLY)
    role = ledger.private().role
    pakled = plays_pakled(role, chat)
    scripted = PAKLED_PREFIX + planned.template if pakled else planned.template
    if not settings.model_messages:
        return Written(scripted, False, MODEL_OFF)
    deadline_ms = message_deadline(settings.message_deadline_ms, seconds_left, chat)
    if deadline_ms < chat.min_deadline_ms:
        return Written(scripted, False, TOO_LITTLE_TIME)
    hides_seer = must_hide_seer(planned, role, seer_revealed=seer_revealed)
    request = TextRequest(
        instruction=planned.instruction,
        context=prompts.context(ledger, chat.quote_chars),
        max_words=chat.max_words_asked,
        deadline_ms=deadline_ms,
        state_tag=state_tag,
        system=prompts.message_system_prompt(role, hides_seer=hides_seer, pakled=pakled),
    )
    result = source.write(request)
    if result.text is None:
        return Written(scripted, False, result.outcome)
    failed = failed_check(
        result.text,
        planned.points,
        wolf_public=role == WOLF and not planned.recipients,
        hides_seer=hides_seer,
        tuning=chat,
    )
    return Written(scripted, False, failed) if failed else Written(result.text, True, None)


def plays_pakled(role: str | None, tuning: ChatTuning) -> bool:
    """Whether a seat of `role` plays the Pakled persona: with `[chat] pakled_persona` on, a villager or a wolf."""
    return tuning.pakled_persona and role in PAKLED_ROLES


def must_hide_seer(planned: Planned, role: str | None, *, seer_revealed: bool) -> bool:
    """Whether `planned` must not hint that we are the seer: a public message of our seer before it has said it is the
    seer, other than the reveal that says so."""
    return role == SEER and not seer_revealed and planned.kind != SEER_REVEAL and not planned.recipients


def message_deadline(message_deadline_ms: int, seconds_left: int | None, tuning: ChatTuning) -> int:
    """How long a model may take for a text: `message_deadline_ms`, ending at the latest `deadline_margin_ms` before
    the window closes when the state says how long it has left."""
    if seconds_left is None:
        return message_deadline_ms
    return min(message_deadline_ms, seconds_left * MILLISECONDS_PER_SECOND - tuning.deadline_margin_ms)


def failed_check(
    text: str, points: Sequence[str], *, wolf_public: bool, hides_seer: bool, tuning: ChatTuning
) -> str | None:
    """The first check `text` fails, by name, or None when it passes them all.

    `points` must each appear, whatever the case, as words of their own;
    `wolf_public` adds the check for the phrases a wolf's public message never
    holds, and `hides_seer` the check for those that would hint that we are
    the seer.
    """
    folded = _folded(text)

    def holds_any(phrases: Sequence[str]) -> bool:
        return any(_folded(phrase) in folded for phrase in phrases)

    checks = (
        (EMPTY, lambda: not text.strip()),
        (TOO_MANY_WORDS, lambda: len(text.split()) > MAX_WORDS),
        (TOO_LONG, lambda: len(text) > MAX_CHARACTERS),
        (MISSING_POINT, lambda: not all(_says(text, point) for point in points)),
        (INJECTION_PATTERN, lambda: any(pattern.search(text) for pattern in tuning.injection_patterns)),
        (FORBIDDEN_PHRASE, lambda: wolf_public and holds_any(tuning.forbidden_public_phrases)),
        (SEER_HINT, lambda: hides_seer and holds_any(tuning.unrevealed_seer_forbidden_phrases)),
    )
    return next((name for name, fails in checks if fails()), None)


@dataclass
class _Window:
    """What our seat has done in one discussion window.

    `public_mark` is the highest seq our seat had seen when it sent its last
    public message: an accusation with a higher seq came after that message.
    """

    index: int
    sent: list[str] = field(default_factory=list)
    opening_revealed: bool = False
    public_mark: int = NO_SEQ
    terminated: bool = False


class Chat:
    """The chat of one seat for one match: each window's plan, and the claims our seat has made across windows."""

    def __init__(self, settings: WerewolfSettings, source: DecisionSource, rng: random.Random | None = None) -> None:
        """`source` writes the messages when `settings` let a model write them; `rng` orders the Pakled lines and
        picks the Pakled seer claims, a fresh unseeded one when left out."""
        self._settings = settings
        self._source = source
        self._window: _Window | None = None
        # Once made, the seer's reveal and a wolf's counterclaim are kept in every later window.
        self._revealed = False
        self._counterclaimed: int | None = None
        self._rng = rng or random.Random()
        # The match's order of the Pakled lines: window after window takes the next, so none comes twice.
        self._pakled_order = self._rng.sample(PAKLED_LINES, len(PAKLED_LINES))
        self._pakled_sent = 0

    def decide(self, ledger: Ledger, beliefs: Beliefs, seconds_left: int | None) -> MessageDecision:
        """This call's message: the next one of the window's plan that is due, or terminate when none is.

        `ledger` has taken in the call's state; `seconds_left` is how long the
        window has left as the state says, None when it says nothing.
        """
        if ledger.facts().eliminated:
            return TERMINATE_MESSAGING
        window = self._window_of(ledger)
        planned = None if window.terminated else self._next_due(window, ledger, beliefs)
        if planned is None or len(window.sent) >= self._settings.tuning.chat.max_chats_per_window:
            window.terminated = True
            return TERMINATE_MESSAGING
        state_tag = f"w{window.index}-{planned.item}"
        written = write_message(
            planned, ledger, self._source, self._settings, seconds_left, state_tag, seer_revealed=self._revealed
        )
        window.sent.append(planned.item)
        if planned.item == PAKLED:
            self._pakled_sent += 1
        if not planned.recipients:
            window.public_mark = max((message.seq for message in ledger.messages()), default=NO_SEQ)
        records.log(CHAT_EVENT, _chat_event(window.index, planned, written))
        return SendMessage(written.text, list(planned.recipients))

    def _window_of(self, ledger: Ledger) -> _Window:
        """The record of the ledger's current window, a new one when the window is new."""
        if self._window is None or self._window.index != ledger.window_index:
            self._window = _Window(ledger.window_index)
        return self._window

    def _next_due(self, window: _Window, ledger: Ledger, beliefs: Beliefs) -> Planned | None:
        """The first item of the plan that is due and not sent yet in the window: the persona's line, opening,
        coordination, reply."""
        if PAKLED not in window.sent and plays_pakled(ledger.private().role, self._settings.tuning.chat):
            return self._pakled_line()
        if OPENING not in window.sent:
            return self._opening(window, ledger, beliefs)
        tuning = self._settings.tuning
        if COORDINATION not in window.sent and ledger.private().role == WOLF:
            word = wolves.coordination(ledger, beliefs, tuning.wolves, tuning.chat.templates.coordination)
            if word is not None:
                return coordination(word)
        if REPLY not in window.sent and self._reply_due(window, ledger):
            return self._reply(ledger, beliefs)
        return None

    def _opening(self, window: _Window, ledger: Ledger, beliefs: Beliefs) -> Planned:
        """The public opening: the seer's reveal when due, a wolf's counterclaim when due, else our top suspect."""
        templates = self._settings.tuning.chat.templates
        if self._must_reveal(ledger):
            window.opening_revealed = self._revealed = True
            return self._reveal(OPENING, ledger, beliefs)
        if ledger.private().role == WOLF:
            accuser = wolves.counterclaim_against(ledger, earlier=self._counterclaimed)
            if accuser is not None:
                self._counterclaimed = accuser
                return self._pakled_claim(counterclaim(OPENING, accuser, templates), accuser)
        return suspect(OPENING, self._vote_target(ledger, beliefs), templates)

    def _pakled_line(self) -> Planned:
        """The window's Pakled line: the next of the match's order, from the first again only past the last."""
        line = self._pakled_order[self._pakled_sent % len(self._pakled_order)]
        return Planned(PAKLED, PAKLED, line)

    def _reply_due(self, window: _Window, ledger: Ledger) -> bool:
        """Whether the window's one reply is due: an accusation of our seat after our last public message, or a
        reveal the seer owes and its opening did not make."""
        if self._must_reveal(ledger) and not window.opening_revealed:
            return True
        seat = ledger.facts().seat
        # Our own message, once the state shows it back, has a seq above every message sent before it.
        own_public = [
            message.seq for message in ledger.messages(today=True) if message.sender == seat and not message.recipients
        ]
        mark = max([window.public_mark, *own_public])
        return any(
            claim.kind == ACCUSATION and claim.target == seat and claim.seq > mark
            for claim in ledger.claims(today=True)
        )

    def _reply(self, ledger: Ledger, beliefs: Beliefs) -> Planned:
        """The public reply: the seer's reveal when it is due, else the answer to the accusation."""
        if self._must_reveal(ledger):
            self._revealed = True
            return self._reveal(REPLY, ledger, beliefs)
        return defense(REPLY, self._settings.tuning.chat.templates)

    def _reveal(self, item: str, ledger: Ledger, beliefs: Beliefs) -> Planned:
        """The seer's reveal asking for a vote on our top candidate, a Pakled claim when our results found it a wolf."""
        target = self._vote_target(ledger, beliefs)
        planned = seer_reveal(item, ledger, target, self._settings.tuning.chat.templates)
        return self._pakled_claim(planned, target) if ledger.private().results.get(target) == WOLF else planned

    def _pakled_claim(self, planned: Planned, wolf: int) -> Planned:
        """`planned`, a seer claim naming `wolf`, opened by one of the Pakled seer claims and always scripted, when
        the persona is on; else `planned` itself."""
        if not self._settings.tuning.chat.pakled_persona:
            return planned
        claim = self._rng.choice(PAKLED_SEER_CLAIMS).format(player=label(wolf))
        return replace(planned, template=f"{claim} {planned.template}", instruction=None)

    def _must_reveal(self, ledger: Ledger) -> bool:
        return ledger.private().role == SEER and village.should_reveal(
            ledger, self._settings.tuning.village, revealed=self._revealed
        )

    def _vote_target(self, ledger: Ledger, beliefs: Beliefs) -> int:
        """Our current top vote candidate, by our side's plan."""
        tuning = self._settings.tuning
        if ledger.private().role == WOLF:
            return wolves.day_vote(ledger, beliefs, tuning.wolves)[0]
        return village.day_vote(ledger, beliefs, tuning.village, tuning.wolves.seer_retraction_pattern)[0]


def _chat_event(window: int, planned: Planned, written: Written) -> dict[str, object]:
    """The `werewolf_chat` event of one chat: the item, its recipients, the text, and who wrote it."""
    return {
        "window": window,
        "item": planned.item,
        "kind": planned.kind,
        "recipients": list(planned.recipients),
        "text": written.text,
        "model_text_used": written.model_used,
        "reason": written.reason,
    }


def _folded(text: str) -> str:
    """The text in lower case with typographic apostrophes made plain, for matching phrases."""
    return text.lower().translate(APOSTROPHES)


def _says(text: str, point: str) -> bool:
    """Whether `point` appears in `text` whatever the case, not as part of a longer word."""
    return re.search(rf"(?<!\w){re.escape(point)}(?!\w)", text, re.IGNORECASE) is not None
