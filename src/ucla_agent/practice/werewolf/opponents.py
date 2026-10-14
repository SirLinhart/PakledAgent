"""Scripted opponents for the Werewolf practice table, like the simple agents a tournament table seats.

An opponent plays one seat and sees exactly what a contestant's agent at that
seat would see: the seat's state object, the dictionary the seat session
returns for `get_game_state`, with the seat's whole transcript under
`messages`, as `get_messages` would answer it. `SeatView` reads that object
and nothing else, so an opponent can never learn a role, a seer result or a
private chat that its seat was not shown.

- `PlaceholderPlayer`, the starter kit's placeholder: the first legal action
  (by day the lowest living other seat), and a terminate in every window
  without a chat.
- `AbstainerPlayer`, the guide's skeleton: one fixed chat line and a
  terminate in every window, abstain by day, the first legal target by night.
- `RecordVoter`, a simple scripted player that votes from the public record
  and chats once a day. A villager or the seer votes the living seat that has
  cast the most votes for players revealed on death as villagers or the seer,
  the lowest seat on a tie (so the lowest living other seat when there is no
  such vote), and says "I am voting PlayerN." for it. The seer investigates
  the lowest living seat it has not looked at; once it has found a wolf that
  is still alive it votes that wolf (the lowest, if two) and its chat is the
  claim "I am the seer. PlayerN is a wolf.", which is therefore always true.
  A villager believes a lone claim like a typical honest player: when exactly
  one living seat has publicly claimed seer and named a living seat a wolf,
  it votes the seat that claimant named last, unless that seat is itself. A
  wolf kills the living non-wolf that has cast the most votes for a wolf
  (itself, its ally or a revealed wolf), the lowest seat on a tie, votes the
  lowest living non-wolf, says it votes for that seat, and never claims seer.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Mapping

from altruagent import TERMINATE_MESSAGING, SendMessage
from altruagent.runner import MessageDecision

from ucla_agent.practice.werewolf.engine import ABSTAIN, CHAT, NIGHT_SEER, NIGHT_WOLF, SEER, VILLAGER, WOLF
from ucla_agent.practice.werewolf.view import ABSTAIN_NAME, TRANSCRIPT_FIELD, player_label

ABSTAINER_LINE = "No read yet. Who is everyone suspicious of?"
VOTE_LINE = "I am voting {player}."
SEER_CLAIM_LINE = "I am the seer. {player} is a wolf."
_ROLE_LINE = re.compile(r"^Your role: (?P<role>[A-Z]+)$", re.MULTILINE)
_ALLIES_LINE = re.compile(r"^Your fellow wolves: (?P<allies>.*)$", re.MULTILINE)
_INVESTIGATIONS_LINE = re.compile(r"^Your investigations: (?P<found>.*)$", re.MULTILINE)
_PLAYER = re.compile(r"Player(?P<seat>[0-9]+)")
_FINDING = re.compile(r"Player(?P<seat>[0-9]+) is (?P<result>[a-z]+)")
_SEER_CLAIM = re.compile(r"\bI(?:\s+am|['’]m)\s+the\s+seer\b", re.IGNORECASE)
_WOLF_NAMED = re.compile(r"\bPlayer(?P<seat>[0-9]+)\s+is\s+a\s+wolf\b", re.IGNORECASE)


class SeatView:
    """A seat's state object, read as the player at that seat reads it."""

    def __init__(self, state: Mapping[str, Any]) -> None:
        """`state` is the seat's `get_game_state` object; nothing else is consulted."""
        record = state["game_state"]
        observation = state["observation"]
        self._seats_by_name = {player["name"]: player["position"] for player in state["players"]}
        self.seat: int = state["your_position"]
        self.state_version: int = state["state_version"]
        self.observation: str = observation
        self.role = _ROLE_LINE.search(observation)["role"].lower()
        self.allies = _seats_on_line(_ALLIES_LINE, observation)
        self.investigations = _findings(observation)
        self.day: int | None = record["day"]
        self.sub_phase: str | None = record["phase"]
        self.living = [self._seats_by_name[name] for name in record["alive"]]
        self.dead_roles = {self._seats_by_name[death["player"]]: death["role"] for death in record["dead"]}
        self.votes = [
            {self._seats_by_name[voter]: self._target(target) for voter, target in day["votes"].items()}
            for day in record["vote_history"]
        ]
        self.legal = [int(action["action_id"]) for action in (state.get("legal_actions") or {}).get("actions", [])]
        self.window_messages: list[Mapping[str, Any]] = list(state.get("new_messages") or [])
        # A driven seat's state has no transcript; the open window's messages are then all it has read.
        transcript = state.get(TRANSCRIPT_FIELD, self.window_messages)
        self.public_chats: list[tuple[int, str]] = [
            (message["sender"], message["content"])
            for message in transcript
            if message["type"] == CHAT and not message["recipients"]
        ]

    @property
    def others_alive(self) -> list[int]:
        """The living seats other than this one, in seat order."""
        return [seat for seat in self.living if seat != self.seat]

    @property
    def has_chatted(self) -> bool:
        """Whether this seat has sent a chat in the open window."""
        return any(message["sender"] == self.seat and message["type"] == CHAT for message in self.window_messages)

    def _target(self, name: str) -> int:
        return ABSTAIN if name == ABSTAIN_NAME else self._seats_by_name[name]


class PlaceholderPlayer:
    """The starter kit's placeholder: the first legal action, and a terminate in every window without a chat."""

    def choose_action(self, view: Mapping[str, Any]) -> int:
        """The first legal action."""
        return SeatView(view).legal[0]

    def choose_message(self, view: Mapping[str, Any]) -> MessageDecision:
        """Terminate at once."""
        return TERMINATE_MESSAGING


class AbstainerPlayer:
    """The guide's skeleton agent: one fixed chat line a day, abstain by day, the first legal target by night."""

    def choose_action(self, view: Mapping[str, Any]) -> int:
        """Abstain when that is legal, else the first legal target."""
        legal = SeatView(view).legal
        return ABSTAIN if ABSTAIN in legal else legal[0]

    def choose_message(self, view: Mapping[str, Any]) -> MessageDecision:
        """The guide's line once a window, then terminate."""
        return TERMINATE_MESSAGING if SeatView(view).has_chatted else SendMessage(ABSTAINER_LINE)


class RecordVoter:
    """A simple scripted player that votes from the public record; see the module's note for its rules."""

    def choose_action(self, view: Mapping[str, Any]) -> int:
        """Its kill, investigation or vote."""
        return record_voter_choice(SeatView(view))

    def choose_message(self, view: Mapping[str, Any]) -> MessageDecision:
        """One public chat, its seer claim or its vote target, then terminate."""
        seat_view = SeatView(view)
        return TERMINATE_MESSAGING if seat_view.has_chatted else SendMessage(record_voter_line(seat_view))


def record_voter_choice(seat_view: SeatView) -> int:
    """`RecordVoter`'s action in the seat's current sub-phase; always one of the legal actions."""
    if seat_view.sub_phase == NIGHT_WOLF:
        return _kill_target(seat_view)
    if seat_view.sub_phase == NIGHT_SEER:
        return _investigation_target(seat_view)
    return record_voter_vote(seat_view)


def record_voter_vote(seat_view: SeatView) -> int:
    """The seat `RecordVoter` votes to eliminate today, from what its seat has read so far."""
    if seat_view.role == WOLF:
        return min(seat for seat in seat_view.others_alive if seat not in seat_view.allies)
    found_wolf = _found_living_wolf(seat_view)
    if found_wolf is not None:
        return found_wolf
    claimed_wolf = _lone_claim_target(seat_view) if seat_view.role == VILLAGER else None
    if claimed_wolf in seat_view.others_alive:
        return claimed_wolf
    revealed_villagers = {seat for seat, role in seat_view.dead_roles.items() if role != WOLF}
    return _most_votes_for(seat_view, seat_view.others_alive, revealed_villagers)


def record_voter_line(seat_view: SeatView) -> str:
    """`RecordVoter`'s chat of the day: the seer's claim once it has found a living wolf, else the seat it votes for."""
    found_wolf = _found_living_wolf(seat_view) if seat_view.role == SEER else None
    if found_wolf is not None:
        return SEER_CLAIM_LINE.format(player=player_label(found_wolf))
    return VOTE_LINE.format(player=player_label(record_voter_vote(seat_view)))


def _found_living_wolf(seat_view: SeatView) -> int | None:
    """The lowest living seat the seat's own investigations found a wolf; None when there is none."""
    found_wolves = [seat for seat in seat_view.others_alive if seat_view.investigations.get(seat) == WOLF]
    return min(found_wolves, default=None)


def _lone_claim_target(seat_view: SeatView) -> int | None:
    """The living seat last publicly named a wolf by the only living seat that has publicly claimed seer.

    None when no living seat or more than one has claimed, or when the
    claimant has named no living seat a wolf: two claimants contradict each
    other, and a simple player then has no claim to believe.
    """
    living = set(seat_view.living)
    claimants = {
        sender for sender, content in seat_view.public_chats if sender in living and _SEER_CLAIM.search(content)
    }
    if len(claimants) != 1:
        return None
    (claimant,) = claimants
    named_living = [
        int(named["seat"])
        for sender, content in seat_view.public_chats
        if sender == claimant
        for named in _WOLF_NAMED.finditer(content)
        if int(named["seat"]) in living
    ]
    return named_living[-1] if named_living else None


def _kill_target(seat_view: SeatView) -> int:
    wolves = {seat_view.seat, *seat_view.allies} | {seat for seat, role in seat_view.dead_roles.items() if role == WOLF}
    return _most_votes_for(seat_view, seat_view.legal, wolves)


def _investigation_target(seat_view: SeatView) -> int:
    not_looked_at = [seat for seat in seat_view.legal if seat not in seat_view.investigations]
    return min(not_looked_at) if not_looked_at else seat_view.legal[0]


def _most_votes_for(seat_view: SeatView, candidates: Iterable[int], targets: set[int]) -> int:
    """The candidate that cast the most votes, over every day's vote, for any of `targets`; the lowest on a tie."""
    counts = Counter(voter for day in seat_view.votes for voter, target in day.items() if target in targets)
    return min(candidates, key=lambda seat: (-counts[seat], seat))


def _seats_on_line(line: re.Pattern[str], observation: str) -> list[int]:
    found = line.search(observation)
    return [int(player["seat"]) for player in _PLAYER.finditer(found.group(1))] if found else []


def _findings(observation: str) -> dict[int, str]:
    found = _INVESTIGATIONS_LINE.search(observation)
    if found is None:
        return {}
    return {int(finding["seat"]): finding["result"] for finding in _FINDING.finditer(found["found"])}
