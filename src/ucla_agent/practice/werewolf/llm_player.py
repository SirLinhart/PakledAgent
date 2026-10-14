"""The model-driven practice opponent: the local `fast` model chooses its moves and writes its chat of the day.

`LlmPlayer` asks a chain of `local:fast`, the included model server's fast
role, through the decide layer, with a 20 s deadline: a `ChoiceRequest` over
its legal actions for every kill, investigation and vote, and a
`TextRequest` for one public message a day of at most 50 words, written from
its observation and the window's messages. Whatever does not come back used
(a timeout, an error, a refusal, nothing written) or comes back too long for a
chat is replaced by `RecordVoter`'s choice and line, so every action is legal
and every chat keeps to the platform's limits. It never asks Claude.

It asks with `ask` and `ask_text` rather than `choose` and `write`: those
write the request into the open match record, which during a practice game is
the driven seat's, and an opponent's model calls would read there as the
agent's own.
"""

from __future__ import annotations

from typing import Any, Mapping

from altruagent import TERMINATE_MESSAGING, SendMessage
from altruagent.runner import MessageDecision

from ucla_agent.core.decide import ChainSource, ChoiceRequest, DecisionSource, LocalModelSource, Option, TextRequest
from ucla_agent.core.decide import journal
from ucla_agent.practice.werewolf.engine import CHAT, DAY_VOTE, MAX_CHARACTERS, MAX_WORDS, NIGHT_SEER, NIGHT_WOLF
from ucla_agent.practice.werewolf.opponents import SeatView, record_voter_choice, record_voter_line
from ucla_agent.practice.werewolf.view import player_label

FAST_ROLE = "fast"
CHAIN_NAME = "werewolf_practice_opponent"
DEADLINE_MS = 20_000
SYSTEM_PROMPT = (
    "You are a player in a game of Werewolf at a table of seven: two wolves and five villagers, one of whom is "
    "the seer. Play to win for your side."
)
QUESTIONS = {
    NIGHT_WOLF: "Which player do you kill tonight?",
    NIGHT_SEER: "Which player do you investigate tonight?",
    DAY_VOTE: "Which player do you vote to eliminate today, or do you abstain?",
}
CHAT_INSTRUCTION = "Write your one public message for today's discussion. Do not give away what your side must hide."
DISCUSSION_HEADING = "Today's discussion so far:"
NOBODY_SPOKE = "Nobody has spoken yet today."
PRIVATE_MARK = " (private)"


def default_source() -> ChainSource:
    """The chain `LlmPlayer` asks: the model server's `fast` role alone."""
    return ChainSource([LocalModelSource(FAST_ROLE)], name=CHAIN_NAME)


class LlmPlayer:
    """A practice opponent whose choices and chat come from a model, with `RecordVoter` standing in for failures."""

    def __init__(self, source: DecisionSource | None = None) -> None:
        """`source` replaces the `local:fast` chain, for a test's stub."""
        self._source = default_source() if source is None else source

    def choose_action(self, view: Mapping[str, Any]) -> int:
        """The model's choice among the legal actions, or `RecordVoter`'s when the model gives none."""
        seat_view = SeatView(view)
        options = [Option(action["action_id"], action["label"]) for action in view["legal_actions"]["actions"]]
        request = ChoiceRequest(
            QUESTIONS[seat_view.sub_phase], options, seat_view.observation, DEADLINE_MS, _state_tag(seat_view)
        )
        chosen = journal.result_of(self._source.ask(request), self._source.name).option_id
        return record_voter_choice(seat_view) if chosen is None else int(chosen)

    def choose_message(self, view: Mapping[str, Any]) -> MessageDecision:
        """One chat a window, the model's text or `RecordVoter`'s line, then terminate."""
        seat_view = SeatView(view)
        if seat_view.has_chatted:
            return TERMINATE_MESSAGING
        request = TextRequest(
            CHAT_INSTRUCTION, _discussion(seat_view), MAX_WORDS, DEADLINE_MS, _state_tag(seat_view), SYSTEM_PROMPT
        )
        written = journal.text_result_of(self._source.ask_text(request), self._source.name).text
        return SendMessage(written if _fits_a_chat(written) else record_voter_line(seat_view))


def _fits_a_chat(text: str | None) -> bool:
    return text is not None and len(text.split()) <= MAX_WORDS and len(text) <= MAX_CHARACTERS


def _discussion(seat_view: SeatView) -> str:
    """The seat's observation followed by the chats of the open window it can read."""
    chats = [
        f"{player_label(message['sender'])}{PRIVATE_MARK if message['recipients'] else ''}: {message['content']}"
        for message in seat_view.window_messages
        if message["type"] == CHAT
    ]
    return "\n".join([seat_view.observation, "", DISCUSSION_HEADING, *(chats or [NOBODY_SPOKE])])


def _state_tag(seat_view: SeatView) -> str:
    return f"day{seat_view.day}-{seat_view.sub_phase}-v{seat_view.state_version}"
