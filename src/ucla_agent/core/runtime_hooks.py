"""The one place the agent attaches to a non-public part of the vendored runtime.

The runtime never tells the agent that a match is over: `run_game` reads the
result itself, builds the final state with the module function
`altruagent.runner._terminal_game_state(last_state, result)` and returns. To
learn the result, `install` replaces that function, in memory only and only in
the process that plays the match, with one that first hands the result to a
listener and then calls the original. No file under `src/altruagent/` is
changed. `docs/COMPONENTS.md` discloses the wrap.
"""

from __future__ import annotations

import functools
import traceback
from typing import Any, Callable

from altruagent import runner

from ucla_agent.core.console import print_line

HOOKED_FUNCTION_NAME = "_terminal_game_state"
_INSTALLED_MARK = "_ucla_agent_result_hook"


def install(on_result: Callable[[Any], None]) -> bool:
    """Wrap the runner's final-state function so that `on_result` gets each match result first.

    Returns True when the wrap is in place, and False, leaving the runtime
    untouched, when this version of the runner has no such function.
    Installing again changes nothing: the first listener stays.
    """
    original = getattr(runner, HOOKED_FUNCTION_NAME, None)
    if original is None:
        return False
    if getattr(original, _INSTALLED_MARK, False):
        return True

    @functools.wraps(original)
    def terminal_game_state_with_result_hook(last_state: Any, result: Any) -> Any:
        try:
            on_result(result)
        except Exception:  # noqa: BLE001 - whatever the listener does, the runtime gets its final state
            print_line(f"The match result could not be recorded:\n{traceback.format_exc()}", error=True)
        return original(last_state, result)

    setattr(terminal_game_state_with_result_hook, _INSTALLED_MARK, True)
    setattr(runner, HOOKED_FUNCTION_NAME, terminal_game_state_with_result_hook)
    return True
