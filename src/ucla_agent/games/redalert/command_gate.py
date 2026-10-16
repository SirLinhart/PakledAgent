"""The forward gate of the model commander: main goes forward only strong enough and gathered.

A small model asked where main goes sent it forward a few units at a time:
it picked the advance in most questions, whatever main held. So the script
decides when main may go forward, and the model only how. While the
commander commands main (see `commander`), main's forward gate is open or
closed, and main's question offers no forward action while it is closed (see
`command_army`): main gathers at the staging point.

**Opening.** The gate opens in a cycle in which all of these hold, the
figures those of `[commander]`:

- main's body, its units within gathered_cells of `[raids]` of its centroid,
  holds at least go_body_share of its units;
- main is worth at least go_min_value, its units' cost times their health;
- main is worth at least go_enemy_share times the enemy estimate of its
  question (`enemy_total_est`). While that estimate is assumed, no enemy army
  having been seen yet, this test is skipped.

**Staying open.** Once open, the gate stays open until main is worth less
than regroup_value_share of what it was worth when the gate opened and, against
an enemy estimate that is not assumed, less than go_enemy_share of it, or until
the script pulls main back from a lost fight (see `command_retreat`); then it
closes, and main regroups. What main is worth then is main as a whole: its own
units and the units joining it (below) within `WHOLE_MAIN_CELLS` of its
centroid or sent on to it, not only the units at the front, which a reinforced
main winning forward may have spent down while as much again stood behind it.

Each opening and closing is a `commander` event of kind `gate`, written by
the commander, with main's value, its body share and the enemy estimate.

**Joining units.** While the gate is open, main is the units it held when the
gate opened and those that have joined it since; what the gate measures is
theirs. A unit that comes to main then, made since or back from elsewhere,
does not trickle after the army on its own: it goes to the staging point
first, by a move as main's gathering is. Once `JOIN_GROUP` of them stand
there, within gathered_cells of `[combat]` of it, or `JOIN_WAIT_SECONDS`
after the first of them arrived, those standing there are sent on together
by an attack-move to main's centroid, and again whenever they stand idle
short of it. One within gathered_cells of `[raids]` of main's centroid has
joined main. These are the combat manager's orders, in the normal order
slots. When the gate closes, every unit of main is main again. While main
has a staging cell on its way to its objective (see `forward_staging`), the
joining units meet at that cell instead, sent there whether they stand idle
or not, and go on from it once those standing there are worth
reinforcement_strength of `[combat]` together, or `JOIN_WAIT_SECONDS` after
the first of them arrived.

**Units leaving for repair.** A vehicle of main that goes into repair mode
(see `repair`) is no longer main's while the gate is open, and what it was
worth when the gate opened is taken out of what main was worth then: its going
does not close the gate by itself, and back from repair it joins main as the
units joining it do.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from ucla_agent.games.redalert import cells, safe_staging
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.fight import centroid, is_near, strength, worth
from ucla_agent.games.redalert.forward_staging import JoinWait
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.tuning import CommanderTuning
from ucla_agent.games.redalert.world import Cell, Unit, World

# Why the gate closed: main lost too much of its value, or the script pulls it back from a lost fight.
REGROUP = "regroup"
PULLED_BACK = "pulled_back"
# The units joining main while the gate is open go on from the staging point this many together, or this long after
# the first of them arrived there.
JOIN_GROUP = 4
JOIN_WAIT_SECONDS = 30
_JOIN_WAIT_TICKS = JOIN_WAIT_SECONDS * TICKS_PER_SECOND
# The units joining main within this many cells of its centroid count in what main is worth as a whole.
WHOLE_MAIN_CELLS = 30


@dataclass(frozen=True)
class Measure:
    """What the gate reads of main in a cycle: what main is worth, the share of its units in its body, and the enemy
    estimate, assumed or not; and what each of main's units is worth, by id."""

    value: float
    body_share: float
    enemy_estimate: int
    is_assumed: bool
    worths: tuple[tuple[int, float], ...] = ()


@dataclass(frozen=True)
class GateChange:
    """The gate opened or closed in a cycle: whether it is open now, what main measured then, and why it closed (None
    for an opening)."""

    is_open: bool
    measure: Measure
    reason: str | None = None


def measure(
    units: Sequence[Unit],
    enemy_estimate: int,
    is_assumed: bool,
    world: World,
    ctx: MatchContext,
    joining: Sequence[Unit] = (),
) -> Measure:
    """What the gate reads of main's units, there being one at least: their body share, and what they are worth with
    the units `joining` main that count with them (see `ForwardGate.joining_near`), main as a whole."""
    middle = centroid(units)
    reach = ctx.tuning.raids.gathered_cells
    body = [unit for unit in units if is_near(middle, unit.x, unit.y, reach)]
    worths = tuple((unit.id, worth(unit.type, unit.hp, world)) for unit in units)
    value = sum(unit_worth for _, unit_worth in worths) + strength(joining, world)
    return Measure(value, len(body) / len(units), enemy_estimate, is_assumed, worths)


class ForwardGate:
    """Whether main may go forward, what main was worth when the gate opened, and the units joining main while it is
    open. See the module's note."""

    def __init__(self) -> None:
        self.is_open = False
        self._opened_value = 0.0
        # What each of main's units was worth when the gate opened, by id.
        self._opened_worths: dict[int, float] = {}
        self.joiners = Joiners()
        # The ids of main's units in the latest cycle they were split, which main is when the gate opens.
        self._unit_ids: set[int] = set()

    def split(self, units: Sequence[Unit], tick: int, away: Iterable[int] = ()) -> list[Unit]:
        """main's units of a cycle that are main: all of them while the gate is closed; while it is open, all but those
        joining it (see `Joiners`), and all of them once none that is main is left. The units `away` from main on a
        detachment of the commander's (see `command_detachments`) are main's when the gate opens, as main is when they
        come back."""
        self._unit_ids = {unit.id for unit in units}.union(away)
        commanded = self.joiners.split(units, tick) if self.is_open else []
        if not commanded:
            self.joiners.start(self._unit_ids if self.is_open else ())
            commanded = list(units)
        self.joiners.center = cells.nearest_cell(*centroid(commanded))
        return commanded

    def follow(self, measured: Measure, is_pulled_back: bool, tuning: CommanderTuning) -> GateChange | None:
        """Open or close the gate on what main measures in this cycle, closed while the script pulls main back; the
        change, or None when the gate stays as it was. Opening, main is the units of the latest split; closing, every
        unit is main again."""
        if self.is_open:
            reason = PULLED_BACK if is_pulled_back else None
            if reason is None and self._is_spent(measured, tuning):
                reason = REGROUP
            if reason is None:
                return None
            self.is_open = False
            self.joiners.stop()
            return GateChange(False, measured, reason)
        if is_pulled_back or not _is_ready(measured, tuning):
            return None
        self.is_open, self._opened_value = True, measured.value
        self._opened_worths = dict(measured.worths)
        self.joiners.start(self._unit_ids)
        return GateChange(True, measured)

    def _is_spent(self, measured: Measure, tuning: CommanderTuning) -> bool:
        """Whether main as a whole has lost too much to stay forward: worth less than regroup_value_share of what it
        was worth when the gate opened and, against an enemy estimate that is not assumed, less than go_enemy_share of
        it."""
        is_short = measured.is_assumed or measured.value < tuning.go_enemy_share * measured.enemy_estimate
        return is_short and measured.value < tuning.regroup_value_share * self._opened_value

    def joining_near(self) -> list[Unit]:
        """The units joining main in the latest split that count in what main is worth as a whole: those within
        WHOLE_MAIN_CELLS of main's centroid and those sent on to it."""
        return self.joiners.near(WHOLE_MAIN_CELLS)

    def release(self, unit_ids: Iterable[int]) -> None:
        """Units leaving main for repair (see `repair`): no longer main's while the gate is open, and what they were
        worth when it opened taken out of what main was worth then."""
        leaving = set(unit_ids)
        self._opened_value -= sum(self._opened_worths.pop(unit_id, 0.0) for unit_id in leaving)
        self.joiners.forget(leaving)


class Joiners:
    """The units joining main while its forward gate is open, and how far they have come. See the module's note."""

    def __init__(self) -> None:
        # main's units while the gate is open: those it held when the gate opened and those that joined it since.
        self._members: set[int] = set()
        # The units joining main in the latest cycle main was split, that cycle's tick, and main's centroid then.
        self.units: list[Unit] = []
        self._tick: int | None = None
        self.center: Cell | None = None
        # The joining units sent on from the staging point to main, and since when the first of those waiting there
        # has stood there.
        self._sent: set[int] = set()
        self._wait = JoinWait(_JOIN_WAIT_TICKS)

    @property
    def ids(self) -> frozenset[int]:
        """The ids of the units joining main in the latest cycle main was split."""
        return frozenset(unit.id for unit in self.units)

    @property
    def split_tick(self) -> int | None:
        """The tick of the latest cycle main was split in; None before the first."""
        return self._tick

    def admit(self, unit_ids: frozenset[int]) -> None:
        """Units joining main that reached it on another way than this one's (see `reinforce_hold`): main's from now
        on."""
        self._members |= unit_ids
        self._sent -= unit_ids

    def start(self, members: Iterable[int]) -> None:
        """The gate opened: main is these units, and none is joining it."""
        self._members, self.units, self._sent, self._wait = set(members), [], set(), JoinWait(_JOIN_WAIT_TICKS)

    def stop(self) -> None:
        """The gate closed: every unit of main is main again."""
        self.start(())

    def forget(self, unit_ids: set[int]) -> None:
        """Units that are no longer main's nor joining it: back with main, they join it afresh."""
        self._members -= unit_ids
        self._sent -= unit_ids

    def near(self, reach: float) -> list[Unit]:
        """The units joining main in the latest split within `reach` cells of main's centroid, or sent on to it."""
        center = self.center
        return [
            unit
            for unit in self.units
            if unit.id in self._sent or (center is not None and is_near(center, unit.x, unit.y, reach))
        ]

    def split(self, units: Sequence[Unit], tick: int) -> list[Unit]:
        """main's units of a cycle while the gate is open: those that are main, returned, and those joining it, kept."""
        self._tick = tick
        self.units = [unit for unit in units if unit.id not in self._members]
        return [unit for unit in units if unit.id in self._members]

    def orders(
        self, excluded: frozenset[int], world: World, ctx: MatchContext, staging_cell: Cell | None = None
    ) -> list[Intent]:
        """The moves of the units joining main in this cycle but the `excluded`: to the staging point, then together
        on to main's centroid; a sent unit that has reached main joins it. With a `staging_cell` of main's (see
        `forward_staging`), to that cell instead, and on from it as that module says. None in a cycle main was not
        split in."""
        if self._tick != world.tick or self.center is None:
            return []
        meeting = staging_cell or safe_staging.staging_point(world, ctx.tuning, ctx)
        joining = [unit for unit in self.units if unit.id not in excluded]
        self._sent &= {unit.id for unit in joining}
        sent = [unit for unit in joining if unit.id in self._sent]
        arrived = {unit.id for unit in sent if is_near(self.center, unit.x, unit.y, ctx.tuning.raids.gathered_cells)}
        self._members |= arrived
        self._sent -= arrived
        waiting = [unit for unit in joining if unit.id not in self._sent and unit.id not in arrived]
        reach = ctx.tuning.combat.gathered_cells
        there = [unit for unit in waiting if is_near(meeting, unit.x, unit.y, reach)]
        if staging_cell is None:
            is_enough = len(there) >= JOIN_GROUP
        else:
            is_enough = strength(there, world) >= ctx.tuning.combat.reinforcement_strength
        going_on = self._wait.going_on(there, is_enough, world.tick)
        self._sent.update(unit.id for unit in going_on)
        lagging = [unit for unit in sent if unit.id not in arrived and unit.idle]
        standing_there = {unit.id for unit in there}
        # Units on their way to the staging point are turned to the staging cell; a staging point is waited for.
        astray = [unit for unit in waiting if (unit.idle or staging_cell is not None) and unit.id not in standing_there]
        gathering = safe_staging.gather_command(ctx.tuning)
        return [*_order(gathering, astray, meeting), *_order("attack_move", [*going_on, *lagging], self.center)]


def _order(command: str, units: list[Unit], cell: Cell) -> list[Intent]:
    """One order of a command to a cell for the units given; none for none."""
    if not units:
        return []
    return [Intent(Priority.MOVE, {"cmd": command, "units": [unit.id for unit in units], "to": list(cell)})]


def _is_ready(measured: Measure, tuning: CommanderTuning) -> bool:
    """Whether main is gathered, worth go_min_value, and worth go_enemy_share of an enemy estimate that is not
    assumed."""
    is_strong = measured.is_assumed or measured.value >= tuning.go_enemy_share * measured.enemy_estimate
    return measured.body_share >= tuning.go_body_share and measured.value >= tuning.go_min_value and is_strong
