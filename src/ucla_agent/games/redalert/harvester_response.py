"""Base defense's answer to a harvester attacked away from the base (`[base_defense] harvester_group_response`).

An attack on a harvester out at an ore field is a threat to the base like any
other, and without the switch it gets the base's answer: a major one sends
the home guard and every unit near the base out to the harvester, which
leaves the base bare, while main, the army meant to fight in the field, is
called only when the threat outweighs them. With the switch on, such a
threat is main's alone.

A threat is a harvester threat from the first cycle in which it hits an own
harvester until it is over. The rule handles it while its cell lies more
than alarm_radius_cells from the centre of the base and the plan is HOLD,
RAID or PRESERVE: under PRESS, STRIKE and FINISH main is on the attack and
keeps at it, and a threat at the base is the base's to answer. main answers
it when it has a unit fit to fight, one with at least fit_hp of `[raids]`
health, the bar a unit is sent out by: every unit of main goes to the
threat's cell with `attack_move` at emergency priority, once, and again only
when the threat moves, under base defense's override, so that neither the
plan's orders for main nor the estimate of the fight call it back. It
answers once in a threat, and for at most harvester_response_seconds: then
the override is lifted and main is the plan's again, whatever the threat
does. The guard and the units near the base get no order for a threat the
rule handles: they stay home. When the rule stops handling the threat while
main answers it (the plan changed, or the threat came to the base), main is
let go in the same way and base defense answers the threat as it does
without the switch. The harvesters run from their attackers as they always
do (see `base_defense`). With `keep_attack_army` on as well, a harvester
threat of enemy aircraft alone is not main's: base defense answers it with
the units that can hit aircraft. Nor is main sent to a harvester threat that
is a far rescue it would lose (see `far_rescue`): the rule still handles the
threat, so the guard and the units near the base stay home, and main stays
under its plan; main out answering one when it becomes such a rescue is let
go, and each unit sent to it gets an emergency move to the staging point in
that cycle, but one a strike, a crush run or a repair retreat holds.

While main answers a major threat, the base trains no counter infantry for
it as long as main is at least engage_ratio of `[combat]` as strong as the
threat's enemies in sight, weighed as in the estimate of a fight (see
`fight`): main can take them, and riflemen trained at the barracks would
only walk out after it. Below that, the counters are trained as without the
switch. A minor threat trains none either way.

Each step of main's answer is written to the match record as a
`base_defense` event with the response (`group`), the group (`main`), the
threat's cell and the outcome: `sent`; `arrived`, once, when main's centre
comes within threat_area_cells of the threat's cell; and how main was let
go: `ended` with the threat, `timed_out` after harvester_response_seconds,
`handed_over` to the answer without the switch, or `declined` as a far
rescue it would lose.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ucla_agent.core import records
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.composition import HOLD, PRESERVE
from ucla_agent.games.redalert.composition import RAID as RAID_PLAN
from ucla_agent.games.redalert.family import HARVESTER
from ucla_agent.games.redalert.far_rescue import recall_order
from ucla_agent.games.redalert.fight import centroid, is_near, ratio_against
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import MatchContext
from ucla_agent.games.redalert.world import Cell, World

if TYPE_CHECKING:
    from ucla_agent.games.redalert.base_defense import Threat
    from ucla_agent.games.redalert.far_rescue import FarRescue

# The plans under which main answers a harvester threat: those under which it waits at home or holds a lead.
HANDLED_PLANS = frozenset({HOLD, RAID_PLAN, PRESERVE})
BASE_DEFENSE_EVENT = "base_defense"
# What a `base_defense` event says answered the threat: a group of the army.
GROUP_RESPONSE = "group"
# The outcomes a `base_defense` event writes: main sent, main come into the threat area, and main let go when the
# threat ended, after harvester_response_seconds, to base defense's answer without the switch, or as a far rescue it
# would lose.
SENT = "sent"
ARRIVED = "arrived"
ENDED = "ended"
TIMED_OUT = "timed_out"
HANDED_OVER = "handed_over"
DECLINED = "declined"


class HarvesterResponse:
    """main's answer to a harvester threat away from the base. See the module's note."""

    def __init__(self, far_rescue: FarRescue) -> None:
        # Whether main is kept out of a far rescue it would lose; shared with base defense's answer.
        self._far_rescue = far_rescue
        # Whether the threat in force has hit an own harvester.
        self._is_harvester_threat = False
        # The tick main was sent to the threat in force; None while it has not been.
        self._started: int | None = None
        # Whether main's answer to the threat in force is over: main has been let go.
        self._is_over = False
        # Unit of main -> the cell it was last sent to in the threat in force.
        self._sent: dict[int, Cell] = {}
        # The threat's cell main was last sent to; None while main has not been sent.
        self._cell: Cell | None = None
        # Whether main has come into the threat area in its answer.
        self._has_arrived = False

    @property
    def is_main_answering(self) -> bool:
        """Whether main is out answering the threat in force under base defense's override."""
        return self._started is not None and not self._is_over

    def holds_counters(self, threat: Threat | None, world: World, ctx: MatchContext) -> bool:
        """Whether no counter infantry is to be trained for the threat in force: main answers it, it is major, and
        main is at least engage_ratio of [combat] as strong as the threat's enemies in sight."""
        if threat is None or not threat.is_major or not self.is_main_answering:
            return False
        main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
        return ratio_against(main, threat.enemies, world, ctx.tuning) >= ctx.tuning.combat.engage_ratio

    def intents(self, threat: Threat | None, world: World, ctx: MatchContext) -> list[Intent] | None:
        """main's orders of the cycle while the rule handles the threat in force; while it keeps main out of a far
        rescue it would lose, only the moves home of the units sent to it (see `far_rescue`); None while it does not,
        and base defense answers the threat as it does without the switch."""
        if threat is None or not ctx.tuning.base_defense.harvester_group_response:
            self._end()
            return None
        self._is_harvester_threat = self._is_harvester_threat or _hits_harvester(threat.attacked, world)
        if not self._handles(threat, world, ctx):
            self._let_go(ctx, HANDED_OVER)
            return None
        if self._started is None and not _has_fit_unit(world, ctx):
            return None
        if self._far_rescue.keeps_main_out(threat, world, ctx):
            self._let_go(ctx, DECLINED)
            return recall_order(self._far_rescue.take_recall(world, ctx), world, ctx)
        return self._answer(threat.cell, world, ctx)

    def _handles(self, threat: Threat, world: World, ctx: MatchContext) -> bool:
        """Whether the rule handles the threat in force: a harvester threat away from the base under a plan that keeps
        main at home; with `keep_attack_army` on, not a raid of aircraft alone, which base defense answers with the
        units that can hit aircraft only."""
        tuning = ctx.tuning.base_defense
        is_away = world.base_center is not None and not cells.is_within(
            threat.cell, world.base_center, tuning.alarm_radius_cells
        )
        is_main_s = self._is_harvester_threat and ctx.plan in HANDLED_PLANS
        return is_away and is_main_s and not threat.is_air_raid(tuning)

    def _answer(self, cell: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """main sent to the threat's cell under the override, until it has answered for harvester_response_seconds;
        nothing once it has been let go."""
        answer_ticks = ctx.tuning.base_defense.harvester_response_seconds * TICKS_PER_SECOND
        if self._started is None:
            self._started, self._cell = world.tick, cell
            self._log(SENT)
        elif world.tick - self._started >= answer_ticks:
            self._let_go(ctx, TIMED_OUT)
        if self._is_over:
            return []
        self._cell = cell
        ctx.groups.set_defense_override(MAIN)
        self._note_arrival(world, ctx)
        return self._attack_moves(cell, ctx)

    def _attack_moves(self, cell: Cell, ctx: MatchContext) -> list[Intent]:
        """An emergency `attack_move` to the threat's cell for the units of main not sent about there yet."""
        reach = ctx.tuning.base_defense.arrived_cells
        fresh = [unit_id for unit_id in ctx.groups.members(MAIN) if not _is_sent(self._sent.get(unit_id), cell, reach)]
        self._sent.update(dict.fromkeys(fresh, cell))
        self._far_rescue.note_sent(fresh)
        return [Intent(Priority.EMERGENCY, {"cmd": "attack_move", "units": fresh, "to": list(cell)})] if fresh else []

    def _note_arrival(self, world: World, ctx: MatchContext) -> None:
        """Write the `arrived` event the first time main's centre is within threat_area_cells of the threat's cell."""
        main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
        if self._has_arrived or not main:
            return
        middle, (x, y) = centroid(main), self._cell
        if is_near(middle, x, y, ctx.tuning.base_defense.threat_area_cells):
            self._has_arrived = True
            self._log(ARRIVED)

    def _let_go(self, ctx: MatchContext, outcome: str) -> None:
        """Lift the override main answers under, for the rest of the threat, and write why; nothing while main does
        not answer."""
        if self.is_main_answering:
            ctx.groups.clear_defense_override(MAIN)
            self._is_over = True
            self._log(outcome)

    def _end(self) -> None:
        """Forget the threat that is over, writing the end of main's answer if it was out: the next one starts
        afresh. Base defense lifts the override when a threat ends."""
        if self.is_main_answering:
            self._log(ENDED)
        self._is_harvester_threat, self._started, self._is_over, self._sent = False, None, False, {}
        self._cell, self._has_arrived = None, False

    def _log(self, outcome: str) -> None:
        """Write one `base_defense` event of main's answer to the threat's cell."""
        event = {"response": GROUP_RESPONSE, "group": MAIN, "threat_cell": list(self._cell), "outcome": outcome}
        records.log(BASE_DEFENSE_EVENT, event)


def _hits_harvester(attacked: list[int], world: World) -> bool:
    """Whether an own harvester is among the actors a threat hit in the cycle."""
    return any(world.units[actor_id].type == HARVESTER for actor_id in attacked if actor_id in world.units)


def _has_fit_unit(world: World, ctx: MatchContext) -> bool:
    """Whether main has a unit in the field fit to fight: with at least fit_hp of [raids] health."""
    fit_hp = ctx.tuning.raids.fit_hp
    return any(world.combat_units[unit_id].hp >= fit_hp for unit_id in ctx.groups.members(MAIN))


def _is_sent(sent: Cell | None, cell: Cell, reach: int) -> bool:
    """Whether a unit last sent to `sent` was sent about to `cell`: within `reach` cells of it."""
    return sent is not None and cells.is_within(sent, cell, reach)
