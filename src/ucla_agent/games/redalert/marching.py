"""How main's screened advance goes while no enemy is near, and what is done when it stalls all the same.

The screen line (see `siege`) keeps main's screen out of the enemy
defenses' reach and its long-range units behind the screen, which is what
an advance into the enemy needs; on the way there, with nothing to screen
against, it only slows main down. So with `march_when_clear` of `[siege]` on,
main's screened advance under PRESS, STRIKE or FINISH marches (see
`siege.Siege.march`) for as long as it is clear of the enemy: of the enemy
units that count in a fight (in sight, or seen lately) and the remembered
enemy defenses that fire at ground units. The advance becomes clear once none
of them is within `march_clear_cells` of any unit of the army, and stays
clear until one comes within `march_blocked_cells`, a shorter distance: an
enemy at the edge of sight, seen and lost again cycle after cycle, would
otherwise turn the march on and off with it. Before it is clear, and once it
no longer is, the screen holds its line, as it does under every other plan
and with the switch off. A defense whose weapon hits aircraft alone, the AA
gun or the SAM site, never stops the march (see `items.is_air_only`).

**Stalling.** An advance that is clear and still makes no headway is stuck
in a way the march does not see, and the siege's caution only costs it time
there. So a clear advance is watched over windows of
`march_progress_seconds`: one whose army's centroid has come less than
`march_progress_cells` nearer the objective over a window goes frontal, all
of main attack-moving on the objective, until the advance is no longer clear
or the plan changes. An army that stands within `march_progress_cells` of
its objective has no such way left to make, and is never taken to have
stalled. A window starts over when the plan or the objective changes, or
when no cycle of the advance has come for a whole window, and the combat
manager starts it over when main fights or goes back to the staging point.
An enemy that comes near and goes again without a fight leaves the window as
it is: the time the screen held its line counts toward it, so an advance
that keeps meeting and losing the enemy is still seen to stall. Each switch
to frontal is a `siege` event
with the reason `stall_frontal`, the tick, the plan, the objective, the cell
of the army's centroid and the cells it gained.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from ucla_agent.core import records
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.composition import FINISH, PRESS, STRIKE
from ucla_agent.games.redalert.fight import Point, centroid, ground_defenses, is_counted
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.tuning import SiegeTuning
from ucla_agent.games.redalert.world import Cell, Unit, World

# The plans under which main attacks, the ones under which its screened advance may march.
MARCHING_PLANS = frozenset({PRESS, STRIKE, FINISH})
# How main's screened advance goes in a cycle: its screen holding the line, marching on the objective, or all of main
# attack-moving on it.
SCREEN_LINE = "screen_line"
MARCH = "march"
FRONTAL = "frontal"
SIEGE_EVENT = "siege"
STALL_FRONTAL = "stall_frontal"
# The decimals the cells gained are written with.
_GAINED_DECIMALS = 1


def is_clear(army: list[Unit], world: World, reach: float) -> bool:
    """Whether no enemy unit that counts in a fight and no remembered enemy defense that fires at ground units is
    within `reach` of any unit of the army."""
    spots = [(record.x, record.y) for record in world.enemy_memory.values() if is_counted(record)]
    spots += [(defense.x, defense.y) for defense in ground_defenses(world)]
    near = _around(army, spots, reach)
    return not any(cells.is_within((unit.x, unit.y), spot, reach) for unit in army for spot in near)


def _around(army: list[Unit], spots: list[Cell], reach: float) -> list[Cell]:
    """The cells within `reach` of the box the army stands in: the only ones that may be within it of a unit."""
    xs, ys = [unit.x for unit in army], [unit.y for unit in army]
    left, right, top, bottom = min(xs) - reach, max(xs) + reach, min(ys) - reach, max(ys) + reach
    return [(x, y) for x, y in spots if left <= x <= right and top <= y <= bottom]


@dataclass(frozen=True)
class _Window:
    """A stretch of a clear advance over which the headway it makes toward its objective is judged."""

    start_tick: int
    plan: str
    objective: Cell
    # How far the army's centroid stood from the objective when the window started.
    distance: float
    # The tick of the latest march cycle in the window.
    seen_tick: int

    def goes_on(self, plan: str, objective: Cell, tick: int, window_ticks: int) -> bool:
        """Whether a cycle continues the window: same plan and objective, and the latest march cycle within a
        window's ticks of it."""
        return (self.plan, self.objective) == (plan, objective) and tick - self.seen_tick < window_ticks


class MarchWatch:
    """Which way main's screened advance goes in each cycle. See the module's note."""

    def __init__(self) -> None:
        self._window: _Window | None = None
        # The plan under which the advance went frontal; None while it has not.
        self._frontal_plan: str | None = None
        # Whether the advance was clear of the enemy in its latest cycle; it starts out not clear.
        self._is_clear = False

    def reset(self) -> None:
        """Start the watch over: main fights, or no longer advances."""
        self._window, self._frontal_plan, self._is_clear = None, None, False

    def mode(self, army: list[Unit], objective: Cell, world: World, ctx: MatchContext) -> str:
        """FRONTAL from a stall until contact or a new plan; else MARCH under a plan that attacks while the army is
        clear of the enemy, and SCREEN_LINE otherwise."""
        plan = ctx.plan
        if plan not in MARCHING_PLANS:
            self.reset()
            return SCREEN_LINE
        if not self._follow_clear(army, world, ctx.tuning.siege):
            # Contact ends a frontal advance, but not the stall window: an enemy that comes and goes leaves it be.
            self._frontal_plan = None
            self._keep_window(plan, objective, world.tick, ctx.tuning.siege)
            return SCREEN_LINE
        if self._frontal_plan != plan:
            self._frontal_plan = plan if self._has_stalled(army, objective, world, ctx) else None
        return MARCH if self._frontal_plan is None else FRONTAL

    def _follow_clear(self, army: list[Unit], world: World, tuning: SiegeTuning) -> bool:
        """Whether the advance is clear in this cycle: from the cycle nothing is within `march_clear_cells` of the
        army until the one something comes within `march_blocked_cells` of it."""
        reach = tuning.march_blocked_cells if self._is_clear else tuning.march_clear_cells
        self._is_clear = is_clear(army, world, reach)
        return self._is_clear

    def _keep_window(self, plan: str, objective: Cell, tick: int, tuning: SiegeTuning) -> None:
        """Carry the window under way over a cycle of the advance that is not clear, while the cycle continues it."""
        window = self._window
        if window is not None and window.goes_on(plan, objective, tick, _window_ticks(tuning)):
            self._window = replace(window, seen_tick=tick)

    def _has_stalled(self, army: list[Unit], objective: Cell, world: World, ctx: MatchContext) -> bool:
        """Whether the window just ended saw the army gain less than `march_progress_cells` on its objective, which
        it stands farther from than that; written as a `siege` event. A new window starts with each that ends, and
        whenever the cycle does not continue the one under way."""
        tuning, tick, plan = ctx.tuning.siege, world.tick, ctx.plan
        window_ticks = _window_ticks(tuning)
        middle = centroid(army)
        distance = math.hypot(middle[0] - objective[0], middle[1] - objective[1])
        window = self._window
        if window is not None and window.goes_on(plan, objective, tick, window_ticks):
            if tick - window.start_tick < window_ticks:
                self._window = replace(window, seen_tick=tick)
                return False
            gained = window.distance - distance
        else:
            gained = None
        self._window = _Window(tick, plan, objective, distance, tick)
        if gained is None or not _is_stall(gained, distance, tuning):
            return False
        _log_stall(middle, objective, gained, tick, plan)
        return True


def _window_ticks(tuning: SiegeTuning) -> int:
    """How long a window of the stall watch lasts, in ticks."""
    return tuning.march_progress_seconds * TICKS_PER_SECOND


def _is_stall(gained: float, distance: float, tuning: SiegeTuning) -> bool:
    """Whether a window's headway is a stall: less than `march_progress_cells`, with more than that left to go."""
    return gained < tuning.march_progress_cells < distance


def _log_stall(middle: Point, objective: Cell, gained: float, tick: int, plan: str) -> None:
    """Write the switch of a stalled advance to frontal as a `siege` event."""
    records.log(
        SIEGE_EVENT,
        {
            "tick": tick,
            "reason": STALL_FRONTAL,
            "plan": plan,
            "objective": list(objective),
            "cell": list(cells.nearest_cell(*middle)),
            "gained_cells": round(gained, _GAINED_DECIMALS),
        },
    )
