"""The target of a child process that behaves like one of the runtime's worker processes, minus the platform.

It lives in its own module because a spawned process finds its target by
importing it by name: the test puts this folder on `sys.path`, which the child
process inherits.
"""

from __future__ import annotations

import os

from altruagent import DecisionContext
from ucla_agent.core import records

SESSION_ID = "worker-style-child"
LOGGED_STATE_VERSIONS = (1, 2, 3)


def open_a_record_log_three_events_and_return(root: str) -> None:
    """Open a match record under `root`, log three events and return, leaving the record for the process exit to close."""
    os.chdir(root)
    context = DecisionContext(session_id=SESSION_ID, tournament_id=None, game_type="red_alert", agent_id="agent-1")
    records.start_match(context, None)
    for state_version in LOGGED_STATE_VERSIONS:
        records.log("state", {"state_version": state_version})
