"""The platform's error codes for a Pokémon move it refuses, and the exception the practice match raises with one."""

from __future__ import annotations

STALE_STATE = "STALE_STATE"
NOT_YOUR_TURN = "NOT_YOUR_TURN"
INVALID_ACTION = "INVALID_ACTION"
GAME_ALREADY_COMPLETE = "GAME_ALREADY_COMPLETE"
RUNTIME_UNAVAILABLE = "RUNTIME_UNAVAILABLE"


class RuleError(Exception):
    """A move the platform would refuse, with the platform's error code and a detail for the player."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
