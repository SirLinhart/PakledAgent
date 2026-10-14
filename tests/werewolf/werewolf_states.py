"""Hand-built Werewolf states in the format of the platform guide.

`raw_state` builds a `get_game_state` object as `docs/reference/altruagent/werewolf.md`
shows it in sections 2, 3, 7 and 8, and `state` wraps one in the `GameState` the
agent is given. Display names are not in seat order, so a test that reads
seats proves the names were translated through `players`; `raw_state` takes
other names, such as the platform's `Name (Official)` form, through `names`.
`practice_states` gives one seat's states through a whole game of the local
practice engine.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from altruagent import GameState

from ucla_agent.practice.werewolf import view
from ucla_agent.practice.werewolf.engine import WerewolfEngine

SEATS = tuple(range(7))
# The display name of each seat, in seat order.
NAMES = ("gus", "alice", "fran", "bob", "erin", "carl", "dave")
WOLF, SEER, VILLAGER = "wolf", "seer", "villager"
NIGHT_KILL, VOTE, RESIGNED = "night_kill", "vote", "resigned"
MESSAGING, MOVING = "messaging", "moving"
NIGHT_WOLF, NIGHT_SEER, DAY_VOTE = "night_wolf", "night_seer", "day_vote"
CAUSE_TEXT = {NIGHT_KILL: "killed at night", VOTE: "eliminated by vote", RESIGNED: "resigned"}
SESSION_ID = "test-werewolf"

# A death: (day, seat, role, cause). A day's vote: (day, {voter: target seat or None for abstain}, eliminated or None).
Death = tuple[int, int, str, str]
Vote = tuple[int, Mapping[int, int | None], int | None]


def label(seat: int) -> str:
    """The seat label the observation and messages use."""
    return f"Player{seat}"


def chat(seq: int, sender: int, content: str, recipients: Iterable[int] = ()) -> dict[str, Any]:
    """A chat envelope as the platform sends it."""
    recipients = list(recipients)
    return {
        "message_id": f"{SESSION_ID}:{seq}",
        "seq": seq,
        "sender": sender,
        "recipients": recipients,
        "visibility": "direct" if recipients else "broadcast",
        "type": "chat",
        "content": content,
        "sent_at": "2026-10-08T12:00:00.000Z",
    }


def terminate(seq: int, sender: int) -> dict[str, Any]:
    """A terminate envelope."""
    return {**chat(seq, sender, ""), "type": "terminate"}


def raw_state(
    *,
    seat: int = 3,
    role: str = VILLAGER,
    allies: Iterable[int] = (),
    results: Iterable[tuple[int, str]] = (),
    day: int | None = 1,
    sub_phase: str | None = DAY_VOTE,
    phase: str = MOVING,
    dead: Iterable[Death] = (),
    votes: Iterable[Vote] = (),
    messages: Iterable[Mapping[str, Any]] = (),
    winner: str | None = None,
    names: Sequence[str] = NAMES,
) -> dict[str, Any]:
    """One seat's `get_game_state` object; `names` gives each seat's display name, in seat order."""
    dead, votes = list(dead), list(votes)
    dead_seats = {death[1] for death in dead}
    living = [other for other in SEATS if other not in dead_seats]
    return {
        "session_id": SESSION_ID,
        "game_type": "werewolf",
        "status": "in_progress" if winner is None else "completed",
        "state_version": len(votes) * 7 + len(dead),
        "phase": phase,
        "observation": _observation(seat, role, list(allies), list(results), day, sub_phase, living, dead, votes),
        "is_terminal": winner is not None,
        "is_current_actor": False,
        "your_position": seat,
        "players": [{"position": other, "name": names[other], "agent_id": f"agent-{other}"} for other in SEATS],
        "game_state": {
            "day": day,
            "phase": sub_phase,
            "alive": [names[other] for other in living],
            "dead": [
                {"day": death_day, "player": names[dead_seat], "role": dead_role, "cause": cause}
                for death_day, dead_seat, dead_role, cause in dead
            ],
            "vote_history": [
                {
                    "day": vote_day,
                    "votes": {names[voter]: _target_name(target, names) for voter, target in cast.items()},
                    "eliminated": None if eliminated is None else names[eliminated],
                }
                for vote_day, cast, eliminated in votes
            ],
            "winner": winner,
        },
        "eliminated": seat in dead_seats,
        "messaging_enabled": True,
        "new_messages": list(messages),
        "terminated_messaging": [],
    }


def state(**fields: Any) -> GameState:
    """One seat's state as the agent is given it; the fields are those of `raw_state`."""
    return GameState.from_mcp_state(raw_state(**fields))


def practice_states(seed: int, seat: int) -> list[GameState]:
    """The seat's state after every step of a practice game in which every seat terminates and makes its first move."""
    engine = WerewolfEngine(seed, clock=lambda: 0.0)
    states = [GameState.from_mcp_state(view.state_payload(engine, seat, "practice"))]
    while not engine.is_over:
        if engine.platform_phase == MESSAGING:
            engine.terminate(engine.window_waiting_on()[0])
        else:
            actor = engine.current_actor()
            engine.play(actor, engine.legal_actions(actor)[0])
        states.append(GameState.from_mcp_state(view.state_payload(engine, seat, "practice")))
    return states


def _observation(
    seat: int,
    role: str,
    allies: list[int],
    results: list[tuple[int, str]],
    day: int | None,
    sub_phase: str | None,
    living: list[int],
    dead: list[Death],
    votes: list[Vote],
) -> str:
    lines = ["=== Werewolf ===", f"Your role: {role.upper()}", _private_line(role, allies, results)]
    if seat not in living:
        lines.append("You are DEAD. You may watch, but not act.")
    lines.append(f"Day {day} | phase {sub_phase} | alive {len(living)}/7")
    lines.append(f"Living: {', '.join(label(other) for other in living)}")
    lines.append("Dead:")
    lines.extend(
        f"  day {death_day}: {label(dead_seat)} was {dead_role} ({CAUSE_TEXT[cause]})"
        for death_day, dead_seat, dead_role, cause in dead
    )
    for vote_day, cast, eliminated in votes:
        tally = ", ".join(f"{label(voter)} -> {_target_label(target)}" for voter, target in cast.items())
        outcome = "nobody eliminated" if eliminated is None else f"{label(eliminated)} eliminated by vote"
        lines.append(f"Day {vote_day} vote: {tally} — {outcome}")
    return "\n".join(lines)


def _target_name(target: int | None, names: Sequence[str]) -> str:
    return "abstain" if target is None else names[target]


def _target_label(target: int | None) -> str:
    return "abstain" if target is None else label(target)


def _private_line(role: str, allies: list[int], results: list[tuple[int, str]]) -> str:
    if role == WOLF:
        return f"Your fellow wolves: {', '.join(label(ally) for ally in allies)}"
    if role == SEER:
        if not results:
            return "You have not investigated anyone yet."
        return f"Your investigations: {', '.join(f'{label(target)} is {result}' for target, result in results)}"
    return "You do not know anyone else's role."
