"""What one seat of a practice game sees, shaped like the platform's MCP payloads.

`state_payload` is the `get_game_state` object of the guide's sections 2 to 8
and of `docs/reference/altruagent/mcp.md`, built from a `WerewolfEngine` for
one seat. Hidden information reaches a seat only as the guide says: its own
role, a wolf's allies, the seer's own results, and the private chats it sent
or received. Everything else is the public record, the same for every seat.

The guide shows the observation text by example; where it shows no example,
the text decided here is:

- the record of deaths with nobody dead is the single line `Dead: none`;
- a day vote that eliminated nobody ends `— nobody eliminated`;
- once the game is over the phase reads `phase game_over`, after the last
  day's number;
- a dead seat's line `You are DEAD. You may watch, but not act.` follows
  its private lines.

Display names are `seat0` to `seat6`, and each seat's `agent_id` is its name.
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from typing import Any

from ucla_agent.practice.werewolf.engine import (
    ABSTAIN,
    DAY_VOTE,
    NIGHT_KILL,
    NIGHT_SEER,
    NIGHT_WOLF,
    SEAT_COUNT,
    SEATS,
    SEER,
    VILLAGER,
    VOTE,
    WOLF,
    DayVote,
    Message,
    WerewolfEngine,
)

GAME_TYPE = "werewolf"
RUNTIME_ADAPTER = "practice"
IN_PROGRESS, COMPLETED = "in_progress", "completed"
ABSTAIN_NAME = "abstain"
BROADCAST, DIRECT = "broadcast", "direct"
GAME_OVER_PHASE = "game_over"
TITLE_LINE = "=== Werewolf ==="
DEAD_LINE = "You are DEAD. You may watch, but not act."
NO_DEATHS_LINE = "Dead: none"
NOBODY_ELIMINATED = "nobody eliminated"
NEVER_INVESTIGATED_LINE = "You have not investigated anyone yet."
VILLAGER_LINE = "You do not know anyone else's role."
CAUSE_TEXT = {NIGHT_KILL: "killed at night", VOTE: "eliminated by vote"}
SIDE_TEXT = {WOLF: "WOLVES", VILLAGER: "VILLAGERS"}
ACTION_LABELS = {
    NIGHT_WOLF: "Kill {player}",
    NIGHT_SEER: "Investigate {player}",
    DAY_VOTE: "Vote to eliminate {player}",
}
ABSTAIN_LABEL = "Abstain"
WAIT_TOOL, PLAY_TOOL, MESSAGE_TOOL, RESULT_TOOL = "wait_for_update", "play_action", "send_message", "get_result"
NEXT_ACTION_HINTS = {
    RESULT_TOOL: "The game is over. Read the result.",
    WAIT_TOOL: "Not your move right now. Wait for the next change.",
    PLAY_TOOL: "Your move. Pick one of legal_actions.",
    MESSAGE_TOOL: "The discussion window is open. Chat, then terminate.",
}
ELIMINATED_HINT = "You are out of this game but it is still running. Keep reading state and messages; you cannot act."
# The field of an opponent's view that holds its seat's whole transcript, as `get_messages` answers it: the
# platform empties `new_messages` for the vote, and an in-process opponent has no tool call to re-read the day.
TRANSCRIPT_FIELD = "messages"


def seat_name(seat: int) -> str:
    """The display name of a seat."""
    return f"seat{seat}"


def player_label(seat: int) -> str:
    """The label the observation, action labels and messages use for a seat."""
    return f"Player{seat}"


def utc_text(seconds: float) -> str:
    """A clock reading as ISO-8601 UTC text."""
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def state_payload(engine: WerewolfEngine, seat: int, session_id: str) -> dict[str, Any]:
    """The seat's `get_game_state` object; `legal_actions` is included only when the seat can act."""
    legal = engine.legal_actions(seat)
    actor = engine.current_actor()
    payload: dict[str, Any] = {
        "session_id": session_id,
        "game_type": GAME_TYPE,
        "runtime_adapter": RUNTIME_ADAPTER,
        "status": COMPLETED if engine.is_over else IN_PROGRESS,
        "state_version": engine.state_version,
        "phase": engine.platform_phase,
        "observation": observation_text(engine, seat),
        "is_terminal": engine.is_over,
        "is_current_actor": actor == seat,
        "current_actor": None if actor is None else {"agent_id": seat_name(actor), "position": actor},
        "next_actions": _next_actions(engine, seat, legal),
        "your_position": seat,
        "players": [{"position": other, "name": seat_name(other), "agent_id": seat_name(other)} for other in SEATS],
        "game_state": game_state_block(engine),
        "eliminated": not engine.is_alive(seat),
        "messaging_enabled": True,
        "new_messages": window_messages(engine, seat, session_id),
        **_window_fields(engine),
    }
    if legal:
        payload["legal_actions"] = legal_actions_payload(engine, seat, session_id)
    return payload


def legal_actions_payload(engine: WerewolfEngine, seat: int, session_id: str) -> dict[str, Any]:
    """The seat's `get_legal_actions` object: every action it may take now, each with a ready `play_action` input."""
    version = engine.state_version
    actions = [
        {
            "action_id": str(action),
            "label": action_label(engine.sub_phase, action),
            "input": {"session_id": session_id, "action_id": str(action), "state_version": version},
        }
        for action in engine.legal_actions(seat)
    ]
    return {"session_id": session_id, "state_version": version, "actions": actions}


def action_label(sub_phase: str | None, action: int) -> str:
    """The label of an action in a sub-phase: `Kill PlayerN`, `Investigate PlayerN`, `Vote to eliminate PlayerN`."""
    if action == ABSTAIN:
        return ABSTAIN_LABEL
    return ACTION_LABELS[sub_phase].format(player=player_label(action))


def envelope(message: Message, session_id: str) -> dict[str, Any]:
    """A message as the platform sends it."""
    return {
        "message_id": f"{session_id}:{message.seq}",
        "seq": message.seq,
        "sender": message.sender,
        "recipients": list(message.recipients),
        "visibility": DIRECT if message.recipients else BROADCAST,
        "type": message.type,
        "content": message.content,
        "state_version": message.state_version,
        "sent_at": utc_text(message.sent_at),
    }


def visible_messages(engine: WerewolfEngine, seat: int, session_id: str, since: int = -1) -> list[dict[str, Any]]:
    """Every message of the game the seat may read with a `seq` above `since`, oldest first."""
    return [
        envelope(message, session_id) for message in engine.messages if message.seq > since and message.visible_to(seat)
    ]


def window_messages(engine: WerewolfEngine, seat: int, session_id: str) -> list[dict[str, Any]]:
    """The messages of the open window the seat may read; empty when no window is open."""
    if engine.window is None:
        return []
    return [
        envelope(message, session_id)
        for message in engine.messages
        if message.day == engine.day and message.visible_to(seat)
    ]


def game_state_block(engine: WerewolfEngine) -> dict[str, Any]:
    """The public record by display name, the same for every seat."""
    return {
        "day": None if engine.is_over else engine.day,
        "phase": engine.sub_phase,
        "alive": [seat_name(seat) for seat in engine.living_seats()],
        "dead": [
            {"day": death.day, "player": seat_name(death.seat), "role": death.role, "cause": death.cause}
            for death in engine.deaths
        ],
        "vote_history": [
            {
                "day": vote.day,
                "votes": {seat_name(voter): _target_name(target) for voter, target in vote.votes.items()},
                "eliminated": None if vote.eliminated is None else seat_name(vote.eliminated),
            }
            for vote in engine.vote_history
        ],
        "winner": engine.winner,
    }


def observation_text(engine: WerewolfEngine, seat: int) -> str:
    """The seat's observation in the guide's text format: its private lines, then the public record."""
    lines = [TITLE_LINE, f"Your role: {engine.roles[seat].upper()}", _private_line(engine, seat)]
    if not engine.is_alive(seat):
        lines.append(DEAD_LINE)
    phase = GAME_OVER_PHASE if engine.is_over else engine.sub_phase
    lines.append(f"Day {engine.day} | phase {phase} | alive {len(engine.living_seats())}/{SEAT_COUNT}")
    lines.append(f"Living: {', '.join(player_label(living) for living in engine.living_seats())}")
    lines.extend(_death_lines(engine))
    lines.extend(_vote_line(vote) for vote in engine.vote_history)
    if engine.is_over:
        lines.append(f"Game over — {SIDE_TEXT[engine.winner]} win")
    return "\n".join(lines)


def result_payload(engine: WerewolfEngine, seat: int, session_id: str) -> dict[str, Any]:
    """The seat's `get_result` object: scores by display name once the game is over, nulls while it runs."""
    returns = engine.returns()
    result: dict[str, Any] = {
        "session_id": session_id,
        "is_terminal": engine.is_over,
        "status": COMPLETED if engine.is_over else IN_PROGRESS,
        "returns": None if returns is None else {seat_name(other): score for other, score in returns.items()},
        "your_return": None if returns is None else returns[seat],
        "termination_reason": engine.termination_reason,
    }
    if not engine.is_over:
        result["next_actions"] = [{"tool": WAIT_TOOL, "hint": "The game is still in progress."}]
    return result


def _private_line(engine: WerewolfEngine, seat: int) -> str:
    role = engine.roles[seat]
    if role == WOLF:
        allies = [player_label(wolf) for wolf in engine.wolves() if wolf != seat]
        return f"Your fellow wolves: {', '.join(allies)}"
    if role == SEER:
        if not engine.investigations:
            return NEVER_INVESTIGATED_LINE
        found = ", ".join(f"{player_label(target)} is {result}" for target, result in engine.investigations)
        return f"Your investigations: {found}"
    return VILLAGER_LINE


def _death_lines(engine: WerewolfEngine) -> list[str]:
    if not engine.deaths:
        return [NO_DEATHS_LINE]
    return ["Dead:"] + [
        f"  day {death.day}: {player_label(death.seat)} was {death.role} ({CAUSE_TEXT[death.cause]})"
        for death in engine.deaths
    ]


def _vote_line(vote: DayVote) -> str:
    votes = ", ".join(f"{player_label(voter)} -> {_target_label(target)}" for voter, target in vote.votes.items())
    outcome = NOBODY_ELIMINATED if vote.eliminated is None else f"{player_label(vote.eliminated)} eliminated by vote"
    return f"Day {vote.day} vote: {votes} — {outcome}"


def _target_name(target: int) -> str:
    return ABSTAIN_NAME if target == ABSTAIN else seat_name(target)


def _target_label(target: int) -> str:
    return ABSTAIN_NAME if target == ABSTAIN else player_label(target)


def _window_fields(engine: WerewolfEngine) -> dict[str, Any]:
    """Who has terminated in the open window and how long it has left; empty and null when none is open."""
    if engine.window is None:
        return {"terminated_messaging": [], "messaging_seconds_left": None, "messaging_deadline": None}
    deadline = engine.window_deadline()
    return {
        "terminated_messaging": sorted(engine.window.terminated),
        "messaging_seconds_left": max(0, ceil(deadline - engine.now())),
        "messaging_deadline": utc_text(deadline),
    }


def _next_actions(engine: WerewolfEngine, seat: int, legal: list[int]) -> list[dict[str, str]]:
    if engine.is_over:
        tool = RESULT_TOOL
    elif not engine.is_alive(seat):
        return [{"tool": WAIT_TOOL, "hint": ELIMINATED_HINT}]
    elif engine.window is not None:
        tool = MESSAGE_TOOL
    else:
        tool = PLAY_TOOL if legal else WAIT_TOOL
    return [{"tool": tool, "hint": NEXT_ACTION_HINTS[tool]}]
