"""A whole fake match played by the agent with its own managers under a tuning, its events kept in memory.

A switch that changes how the agent plays has to leave a whole match
playable: no error caught anywhere, and every cycle within the compute
budget. The tests of such a switch play one 20-minute fake match with it on.
The events the agent logs are kept as it logs them, without a record file,
so that nothing is dropped however fast the fake game runs, and every cycle
is timed as the runtime would time it, around `choose_action`.

The fake game has no combat, so a match played here is never evidence that
the agent wins.
"""

from __future__ import annotations

import copy
import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import pytest

import situations
from fake_game import (
    MAP_ID,
    MAX_MINUTES,
    SECONDS_PER_MINUTE,
    STEPS_PER_SECOND,
    FakeRedAlert,
    FakeRun,
    ScenarioStep,
)
from ucla_agent.core import records
from ucla_agent.core.stats import percentile
from ucla_agent.games.redalert.tuning import Tuning

WHOLE_MATCH_STEPS = MAX_MINUTES * SECONDS_PER_MINUTE * STEPS_PER_SECOND
# The compute a cycle may take at the 95th percentile.
COMPUTE_P95_MS = 50
P95 = 95
ENEMY_OF = {"england": "russia", "russia": "england"}
_MS_PER_SECOND = 1000


@dataclass
class WholeMatch:
    """What one whole fake match left: the game, the run, the agent, each cycle's compute and the logged events."""

    game: FakeRedAlert
    run: FakeRun
    agent: situations.RedAlertAgent
    compute_ms: list[float] = field(default_factory=list)
    # (kind, data) of every event the agent logged, in order.
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def data_of(self, kind: str) -> list[dict[str, Any]]:
        """The data of every event of a kind, in order."""
        return [data for logged, data in self.events if logged == kind]

    @property
    def compute_p95_ms(self) -> float:
        """The 95th percentile of the cycles' compute, in milliseconds."""
        return percentile(self.compute_ms, P95)


class _Timed:
    """Stands where the runtime's agent stands: hands every call on to the agent, times each cycle and, when a test
    watches the agent, lets it look after each cycle, outside the time measured."""

    def __init__(
        self,
        agent: situations.RedAlertAgent,
        compute_ms: list[float],
        watch: Callable[[situations.RedAlertAgent], None] | None,
    ) -> None:
        self.agent = agent
        self._compute_ms = compute_ms
        self._watch = watch

    def choose_action(self, state: Any, context: Any) -> Any:
        started = time.perf_counter()
        decision = self.agent.choose_action(state, context)
        self._compute_ms.append((time.perf_counter() - started) * _MS_PER_SECOND)
        if self._watch is not None:
            self._watch(self.agent)
        return decision

    def on_action_result(self, result: dict[str, Any], context: Any) -> None:
        self.agent.on_action_result(result, context)


def play_whole_match(
    tuning: Tuning,
    faction: str = "england",
    game_config: dict[str, Any] | None = None,
    scenario: Iterable[ScenarioStep] = (),
    watch: Callable[[situations.RedAlertAgent], None] | None = None,
    plan_selector: Any = None,
    engine_reports: bool = False,
) -> WholeMatch:
    """Play a whole 20-minute fake match for a faction's start with the agent's own managers under `tuning`.

    With `game_config` given, the agent is handed that reference object in place of the fake game's own: a map
    picture of another layout changes where the agent places its buildings, and nothing of the fake game itself.
    `scenario` is what the fake game does besides answering the agent, such as refusing an order. `watch`, when
    given, is called with the agent after each of its cycles, outside the cycle's compute. `plan_selector`, when
    given, sets the plan in place of the script's plan rules. With `engine_reports`, the fake game reports each
    building at the middle cell of its footprint, as the engine does (`FakeRedAlert.report_as_the_engine`).
    """
    game = FakeRedAlert(faction, ENEMY_OF[faction], scenario=scenario)
    if engine_reports:
        game.report_as_the_engine()
        game.unfold_as_the_engine()
    context = game.context() if game_config is None else dataclasses.replace(game.context(), game_config=game_config)
    agent = situations.own_agent(tuning=tuning, plan_selector=plan_selector)
    events: list[tuple[str, dict[str, Any]]] = []
    compute_ms: list[float] = []

    def keep(kind: str, data: dict[str, Any]) -> None:
        assert kind in records.EVENT_KINDS, f"{kind} is not a defined event kind"
        events.append((kind, data))

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(records, "log", keep)
        run = game.run(_Timed(agent, compute_ms, watch), WHOLE_MATCH_STEPS, context)
    return WholeMatch(game, run, agent, compute_ms, events)


def with_ore(game_config: dict[str, Any], ore_cells: Iterable[tuple[int, int]]) -> dict[str, Any]:
    """A copy of the fake game's reference whose map pictures ore on these cells besides its own: the agent's terrain
    reads it, and the fake's own income does not depend on where ore lies."""
    changed = copy.deepcopy(game_config)
    layout = changed["rules"]["maps"][MAP_ID]["layout"]
    rows = [list(row) for row in layout["rows"]]
    for x, y in ore_cells:
        rows[y][x] = "$"
    layout["rows"] = ["".join(row) for row in rows]
    return changed
