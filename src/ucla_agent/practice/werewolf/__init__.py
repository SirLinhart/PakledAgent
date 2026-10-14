"""Local Werewolf practice: the platform's rules on this machine, with one seat driven through the real runner loop."""

from ucla_agent.practice.werewolf.engine import RuleError, WerewolfEngine
from ucla_agent.practice.werewolf.mixes import MIXES, OPPONENT_FACTORIES
from ucla_agent.practice.werewolf.play import PracticeResult, play_practice_game
from ucla_agent.practice.werewolf.seat import OpponentError, PracticeSeatSession

__all__ = [
    "MIXES",
    "OPPONENT_FACTORIES",
    "OpponentError",
    "PracticeResult",
    "PracticeSeatSession",
    "RuleError",
    "WerewolfEngine",
    "play_practice_game",
]
