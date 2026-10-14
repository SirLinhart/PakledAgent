"""What the models asked in a match are told of their own recent picks, and the framing their system texts share.

A small model asked the same question every few seconds has no memory of its
own: each question is new to it. So each question says what the model picked
lately and what came of it, and each system text says what the match is.

**The tracker.** The match context holds one `ModelHistory` (a new match
gets a new one, so no pick of a match before is ever shown). It keeps the
model's last `[models] history_picks` picks of the plan question and of the
scout question (see `scout_choice`), and the army ratio of the cycle: the
plan rules' own ratio of our army to the enemy's as they weigh it (the
compact prompt's `army_ratio`), published by them in every cycle. The model
commander keeps its picks itself, one role at a time (see `commander`), and
takes the same ratio for each.

- A plan pick keeps the tick, the plan picked, and the value our side had
  killed and lost by then (`world.military`) and the army ratio then. Its
  line says how long ago it was and what came since:
  `- 40 s ago: PRESS -> since: killed 2,300, lost 900, army ratio 1.4 -> 1.1`
  (the ratio left out while either end of it is unknown).
- A scout pick keeps the tick, the scout, the place picked (its area and
  cell) and the enemies remembered then. Its outcome is `en route` until it
  is settled (see `scout_choice`): `reached`, `retreated` (the way became
  unsafe or the scout fled), `stuck`, `redirected` (a later pick or the
  script sent it elsewhere first) or `lost` (the scout died). With it goes
  what was seen meanwhile, the enemy buildings and units remembered that were
  not at the pick: `- 30 s ago: enemy main edge (60,60) -> reached; saw 2 new
  buildings, 3 new units`.

Every question writes the picks as a `recent picks:` line and one line for
each pick, the newest first, or `recent picks: none` before the first; a
question held to a number of characters cuts its picks first, the oldest
first.

**The framing.** `FRAMING` is the paragraph the plan, commander and scout
system texts share, at most 600 characters (`MAX_FRAMING_CHARS`): who the
opponent is and how a match is decided (`TOURNAMENT`), the doctrine of the
script's own rules (`DOCTRINE`) and to learn from the recent picks
(`LEARN`). The doctrine is a recommendation, so the compact plan prompt,
which holds none, gets the picks alone; and the commander's scout question,
held to 1,000 characters, gets the tournament and the learning without the
doctrine of the army.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable

from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.world import Cell, World

# The shipped number of picks each question shows, `[models] history_picks` of config/agent.toml.
HISTORY_PICKS = 3
HEADING = "recent picks:"
NONE_LINE = f"{HEADING} none"
TOURNAMENT = (
    "Our opponent is another AI agent in a tournament; a 20-minute match not won outright goes to the tiebreak "
    "(value traded)."
)
DOCTRINE = (
    "Doctrine: economy and map control early; never attack into defenses we cannot beat; retreat and regroup when "
    "outnumbered; tanks crush infantry and kill tanks; artillery fires from behind a screen; punish exposed "
    "expansions and harvesters; keep a lead late."
)
LEARN = "Learn from recent picks: repeat what worked, change what lost value."
FRAMING = f"{TOURNAMENT} {DOCTRINE} {LEARN}"
MAX_FRAMING_CHARS = 600
# What became of a scout pick.
EN_ROUTE = "en route"
REACHED = "reached"
RETREATED = "retreated"
STUCK = "stuck"
REDIRECTED = "redirected"
LOST = "lost"
RATIO_DECIMALS = 1


@dataclass(frozen=True)
class PlanPick:
    """A plan the model picked: the plan, the tick, the value killed and lost by then and the army ratio then (None
    while it was unknown)."""

    label: str
    tick: int
    kills_cost: int
    deaths_cost: int
    ratio: float | None


@dataclass
class ScoutPick:
    """A place the model sent a scout to: what it is, the tick, the scout, the place's cell, the enemies remembered
    then, and once settled what became of it and the enemy buildings and units first remembered meanwhile."""

    label: str
    tick: int
    unit_id: int
    cell: Cell
    known: frozenset[int]
    outcome: str | None = None
    new_buildings: int = 0
    new_units: int = 0

    def settle(self, outcome: str, world: World) -> None:
        """Say what became of the pick, with what was seen since, once; a settled pick stays as it is."""
        if self.outcome is None:
            self.outcome = outcome
            self.new_buildings, self.new_units = _new_enemies(self.known, world)


class ModelHistory:
    """The model's recent picks of one match and the army ratio of the cycle. See the module's note."""

    def __init__(self, most: int = HISTORY_PICKS) -> None:
        self.most = most
        # The plan rules' army ratio of the last cycle; None before the first, and with no plan rules publishing it.
        self.ratio: float | None = None
        self.plan_picks: deque[PlanPick] = deque(maxlen=most)
        self.scout_picks: deque[ScoutPick] = deque(maxlen=most)

    def note_plan(self, plan: str, world: World) -> None:
        """Keep a plan the model picked at the world's tick."""
        military = world.military
        self.plan_picks.append(PlanPick(plan, world.tick, military.kills_cost, military.deaths_cost, self.ratio))

    def note_scout(self, label: str, unit_id: int, cell: Cell, world: World) -> None:
        """Keep a place the model sent a scout to at the world's tick."""
        self.scout_picks.append(ScoutPick(label, world.tick, unit_id, cell, frozenset(world.enemy_memory)))

    def open_scout_picks(self) -> list[ScoutPick]:
        """The scout picks not settled yet."""
        return [pick for pick in self.scout_picks if pick.outcome is None]

    def plan_lines(self, world: World, picks: int | None = None) -> list[str]:
        """The plan question's `recent picks:` lines, at most `picks` of them (all kept without it)."""
        return lines((_plan_text(pick, self.ratio, world) for pick in _newest(self.plan_picks, picks)), self.plan_picks)

    def scout_lines(self, unit_id: int, world: World) -> list[str]:
        """The scout question's `recent picks:` lines for a scout: its own picks and those whose scout was lost."""
        shown = [pick for pick in self.scout_picks if pick.unit_id == unit_id or pick.outcome == LOST]
        return lines((_scout_text(pick, world) for pick in _newest(shown, None)), shown)


def lines(texts: Iterable[str], picks: Iterable[object]) -> list[str]:
    """`recent picks:` and a line for each text; `recent picks: none` while there is no pick at all, and nothing
    while there are picks but none is shown."""
    shown = [f"- {text}" for text in texts]
    if shown:
        return [HEADING, *shown]
    return [] if any(True for _ in picks) else [NONE_LINE]


def seconds_ago(tick: int, world: World) -> int:
    """How many whole seconds before the world's tick a tick was."""
    return max(0, world.tick - tick) // TICKS_PER_SECOND


def ratio_change(then: float | None, now: float | None) -> str:
    """`1.4 -> 1.1`: a ratio then and now, each to a tenth; empty while either is unknown."""
    if then is None or now is None:
        return ""
    return f"{then:.{RATIO_DECIMALS}f} -> {now:.{RATIO_DECIMALS}f}"


def _newest(picks: Iterable[PlanPick | ScoutPick], most: int | None) -> list:
    """The picks, the newest first, at most `most` of them."""
    return list(reversed(list(picks)))[:most]


def _plan_text(pick: PlanPick, ratio_now: float | None, world: World) -> str:
    """One plan pick: its age, the plan and what was killed and lost since, and the army ratio then and now."""
    military = world.military
    since = f"killed {military.kills_cost - pick.kills_cost:,}, lost {military.deaths_cost - pick.deaths_cost:,}"
    change = ratio_change(pick.ratio, ratio_now)
    ratio = f", army ratio {change}" if change else ""
    return f"{seconds_ago(pick.tick, world)} s ago: {pick.label} -> since: {since}{ratio}"


def _scout_text(pick: ScoutPick, world: World) -> str:
    """One scout pick: its age, the place, and what became of it with what was seen meanwhile."""
    if pick.outcome is None:
        buildings, units = _new_enemies(pick.known, world)
    else:
        buildings, units = pick.new_buildings, pick.new_units
    outcome = pick.outcome or EN_ROUTE
    return f"{seconds_ago(pick.tick, world)} s ago: {pick.label} -> {outcome}; {_seen_words(buildings, units)}"


def _new_enemies(known: frozenset[int], world: World) -> tuple[int, int]:
    """How many enemy buildings and units are remembered that were not among `known`."""
    new = [record for record in world.enemy_memory.values() if record.id not in known]
    buildings = sum(1 for record in new if record.is_building)
    return buildings, len(new) - buildings


def _seen_words(buildings: int, units: int) -> str:
    """`saw 2 new buildings, 1 new unit`, the counts of none left out; `saw nothing new` for none."""
    parts = [
        f"{count} new {name}{'' if count == 1 else 's'}"
        for count, name in ((buildings, "building"), (units, "unit"))
        if count
    ]
    return f"saw {', '.join(parts)}" if parts else "saw nothing new"
