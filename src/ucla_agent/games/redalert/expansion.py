"""An early second construction yard: a service depot, then an MCV escorted to the creep target's field and deployed.

A base with one construction yard grows only around it, and the field it
creeps toward along the lane is soon farther than its buildings can reach.
With `second_yard` of the tuning file's [expansion] switched on, upkeep
gives the base a second yard early in the match, by the field it walks to.

First the service depot, which the MCV needs: once the war factory stands,
from `second_yard_from_seconds` on, it is ordered when none stands or is on
its way, on the early depot's terms (the power target, no major threat, the
money above the reserve for it, no lost core building saved for) but not the
tech rule's cash and harvesters. It is a building: the unit queues keep their
budget.

Then one MCV, on the Vehicle queue ahead of the army mix, once the depot
stands, the money the game can spend less `production_cash_reserve` is at
least `second_yard_min_cash`, fewer than two yards stand, no MCV lives or is
on its way, there is no major threat and no lost core building is saved for.
Its price is kept from the unit queues in the cycle it is ordered in. The
Vehicle queue has to be free for it; with `tank_factories` of [producers] on,
which keeps that queue filled, it is enough that the queue has room. A second
yard that is lost may be made again the same way.

The MCV's site is the free place for a yard nearest the creep target's ore
cell nearest the base, within `mcv_deploy_cells` of that field's ore by
walking round cliffs, water and trees (see `terrain`), the fewest steps
first; without
a target, the same by the nearest field of the lane toward the enemy that no
refinery serves; else a place for a yard in the base. With
`mcv_to_skipped_fields` switched on, a field placement skipped (see
`refused`) comes first: the base could not grow to it, which is what a second
yard is for. The skipped field nearest the base's center is the site's field
when it lies more than `creep_enemy_clear_cells` from the enemy's start and no
remembered enemy defense stands within `creep_defense_clear_cells` of it. It goes there only
escorted: main units worth at least `mcv_escort_strength` in a fight, the
nearest to it of those main has not sent forward, leave main for the escort
and attack-move to the site with it. While the escort cannot be made up, a
remembered enemy defense stands within 10 cells of the site, an enemy army
seen lately stronger than the escort stands within 15 cells of it, or the base
is under a major threat, the MCV waits at home and its escort goes back to
main. Once it has waited `mcv_wait_ticks` in all, it deploys at home instead.

At its site the MCV is ordered to deploy. The game refuses a deploy when the
yard has no room to unfold; the MCV is then sent one cell farther from the
ore and tries again, three times at most, and after that deploys at home. A
deploy at home that is refused is tried at the next place for a yard there.
Once the yard stands, the escort goes back to main.

The engine unfolds the yard with its top-left corner one cell up and one
left of the MCV's cell (`yard_box`). A site by a field is a cell whose yard
box is free ground, and while the MCV lives with a site, upkeep asks no
building onto that box, defenses included (see `site_box`).

The escort keeps off that ground too: the engine unfolds no yard over a
unit, and an MCV whose site cell a tank stands on never gets there. So while
the MCV has a site and an escort, no escort order names a cell of the yard
box grown by `ESCORT_CLEAR_CELLS` on every side (`clear_area`). The escort
attack-moves to the MCV's next cell while that lies outside it, else to its
hold point: the free cell just outside it nearest the MCV, on the side the
MCV comes from. An escort unit that stands inside it is moved to the free
cell outside it nearest the unit on that side, one unit to a cell, in every
cycle the MCV goes to its site or waits for its deploy's verdict.

The engine may also do nothing with a deploy the platform answered with
`ok`: a building stands where the yard would unfold. A deploy without a
verdict is sent again after 75 ticks, and when 75 ticks after that resend the
MCV still stands idle on the cell it was sent from, the deploy counts as
refused all the same; after a second resend it does whatever the MCV does, so
that no MCV keeps deploying in vain. Such a deploy goes on as a refused one,
but the next try lies two cells farther from the ore, so that the MCV moves
off the cell the engine would not unfold on, and two of them are enough to
send the MCV home. Every refused deploy is an `expansion` event of the phase
`deploy_refused`, with the MCV's cell and the reason `rejected` (the game's
word) or `ignored` (left without effect).

Each step is written to the match record as one `expansion` event: the depot
ordered, the MCV trained, moving, waiting, deployed at its site or at home,
and the MCV or the second yard lost.

A yard that deploys by the field of its site claims one refinery for that
field (`new_yard_field`), which upkeep orders ahead of every other building
but power (see `upkeep`): while the yard stands, until the refinery is
ordered (`end_new_yard_claim`) or the field has `max_refineries_per_field`
refineries serving it or asked against it, when the claim ends with nothing
done. A yard deployed at home claims none.

With `mcv_safe_site` switched on, the site is chosen otherwise, because a
field far off or one behind enemy ground costs the MCV its life before it
deploys. The fields it may deploy by lie within `mcv_site_max_cells` of the
main yard (the yard nearest the base's center), have no own refinery within
`refinery_ore_cells` of their ore, lie more than `creep_enemy_clear_cells`
from the enemy's start and have no remembered enemy defense within
`creep_defense_clear_cells`; a field placement skipped is one of them like
any other. Nearest the main yard first, each one's place is found as above,
and the first place a safe route reaches from the MCV is the site: the
straight line of cells to it, else the way by one of the two side waypoints
of the raids, the shorter, with no cell a remembered enemy defense covers
and none within an enemy unit's range and `mcv_danger_margin_cells` of where
it was seen in the last `mcv_enemy_seen_ticks`. Without such a field, the
MCV deploys at home. An MCV on its way that loses health, or comes within 8
cells of unsafe ground, is sited again by the same rules from where it
stands, once in 250 ticks at most, and goes on to the new site with its
escort, or home when there is none. Each choice is an `expansion` event of
phase `site`, with the distance of the field's center from the main yard
and the reason (`nearest_safe`, `rerouted` or `home_fallback`).

With `waypoints` of [routing] on as well, an MCV that sets out for its site,
or is given another on its way, while the straight line there crosses unsafe
ground goes by the way the graph of waypoints gives to the site (see
`waypoints`), when no cell on that way is unsafe: it moves to each of the
way's cells in turn, its escort with it, on to the next once within 3 cells
of one, and then to the site; the way is written as a `route` event. Without
such a way it goes straight to the site, as it does with the switch off.

With `mcv_new_area` switched on as well, the second yard opens a new area: a
field that is taken (a refinery stands, was asked or is on order against it;
see `placement`) or a claim field is passed over, and of the rest those
whose center lies `mcv_refinery_spacing_cells` or more from every own
refinery, standing or asked, come first. The creep target's field is not
passed over: the MCV does not keep to the crawl. Without any such
field the site is chosen as with the switch off. A site whose field is taken
while the MCV is on its way is chosen again from where the MCV stands, as a
`site` event with the reason `taken`, within the same 250 ticks as a choice
made again for danger.

A second yard by a field that its own base already harvests, or that is soon
mined out, earns nothing new. So with `mcv_new_area` on, every choice of a
site tries the fields the site lasts by first, nearest first as ever: of
those the spacing above puts first, each with at least
`mcv_site_min_ore_cells` ore cells and no own harvester whose last cell on
ore lies in it. Spacing is a hard test there. The state tells no ore per cell:
a field's ore cells are its cells in the map's picture, none once the move to
fresh ore marks it dry (see `fresh_ore`). Without such a field whose place a
safe route reaches, the site is chosen as above, and an `expansion` event of
phase `site_check` with the reason `site_fallback` says so. Upkeep publishes
the fields own harvesters last harvested and the dry ones at the start of
every cycle (`publish_spent_fields`). With `mcv_safe_site` on as well, an MCV
that has waited `mcv_wait_ticks` in all with the want of its escort the reason
goes to the site it has, with the escort it has or alone, as an `mcv_moving`
event with the reason `escort_wait_timeout`, when a safe route reaches the
site from it, and deploys at home only without one: a wait never gives the
MCV a nearer site.

With `mcv_reserve_field` switched on, the site is chosen in the cycle the MCV
is ordered, by the same rules from the main yard (a `site` event then, with
`mcv_safe_site` on), and the field of the site is reserved for the MCV: upkeep
publishes it at the start of every cycle while the MCV is on order, out and
waiting or on its way, and no refinery is asked against it. When the MCV comes
out it keeps that site, unless its field became taken meanwhile or, with
`mcv_safe_site` on, no safe route reaches the site from the MCV any more; then
the site is chosen as it would be without the switch, and the reservation
moves with it, as it does with a site chosen again on the way. It ends once
the MCV deploys, is lost or is sent home.

With `mcv_short_escort` switched on, an MCV that the want of its escort
alone has kept at home for `short_escort_wait_ticks` in a row (no major
threat, no remembered defense near the site, no enemy army near it stronger
than the escort it has) sets out with the escort units it has, as a
`mcv_moving` event with the reason `escort_short_timeout`; with none, only on
a safe route (by `mcv_safe_site`'s test from the MCV when that is on, else
with no defense and no enemy army near the site). Any other reason to wait
starts that count again; the wait of `mcv_wait_ticks` in all goes on as
before.

With `mcv_repeat` switched on, the yards the base may have grow with its
income: one, and one more for each `yards_income_per_yard` of income a
second as the income estimate reports it (none while it is unknown),
`max_yards` at the most (`yards_allowed`). Standing own yards and MCVs out or
on their way count against it, and a yard lost frees its place; each change
of the yards allowed is an `expansion` event of phase and reason `yard_cap`,
with the income, the yards, the MCVs and the cap. An MCV, the second yard's
as a further one, is then wanted while the yards and MCVs are fewer than
allowed, `second_yard_from_seconds` of the match have passed, more than
`repeat_mcv_cutoff_seconds` are left and a site is found, on the second MCV's
other terms (a depot, no MCV out or on its way, no major threat, the money).
Its site is by a far field (see below), the route from the main yard while
the MCV is not out yet. Upkeep looks for it once a cycle and publishes it.
The MCV beyond the second is a further one. Once out,
the MCV is sited from its own cell (keeping the site chosen at its order
while that holds), and its `mcv_trained` and `site` events give the reason
`repeat`; it goes with an escort, waits and is sited again on its way as the
second MCV does. It never deploys at home: without a site it waits at home,
with the reason `repeat_no_site`, and looks for one again every 250 ticks;
once it has waited `mcv_wait_ticks` in all, or been refused too often, it
deploys at the free place for a yard nearest the second yard (the oldest
yard but the main one) when a safe route reaches that place from it, else at
the free place nearest itself, or where it stands. Every yard this rule
deployed is followed, and the loss of each is written.

With `mcv_repeat` on, every MCV, the second yard's and the further ones
alike, goes to a far field that lasts (`far_site`), whatever `mcv_safe_site`
says. A field qualifies when it is not taken, no claim field and no own yard
stands by it already (within `mcv_deploy_cells` of its ore by walking), has at
least `mcv_site_min_ore_cells` ore cells, no own refinery, standing or asked,
within `mcv_refinery_spacing_cells` of its center and no own harvester's last
cell on ore in it, lies within `mcv_far_site_max_cells` of the nearest own
yard's middle, more than `creep_enemy_clear_cells` from the enemy's start,
with no remembered enemy defense within `creep_defense_clear_cells`, and has
a place for a yard that a safe route reaches: the straight line or the way by
a side waypoint, or with `waypoints` of [routing] on the straight line or the
way through the graph of waypoints, none of it on unsafe ground. Of those,
the one whose route is shortest is the site's. The crawl target's field
qualifies as any other field does, however near own buildings stand to it:
the MCV does not keep to the crawl, and a field the base's buildings creep
toward has no yard by it until one deploys there. Once an MCV on order or out
goes to it, it joins the fields placement skipped, so that the crawl names its
next target (with `creep_continuous` on): the MCV's reservation keeps every
refinery off that field meanwhile, and a crawl kept on it would only creep
toward a field no refinery may stand against.

With `mcv_safe_site` or `mcv_repeat` on, a site by a field, whether chosen
or being travelled to, does not qualify while a known enemy construction
yard, refinery or ground defense (one that does not shoot at aircraft alone)
stands within `mcv_enemy_building_cells` of it: a yard deployed there is next
to the enemy's base and its turrets. Every choice of a site passes over such
a place, a site chosen at the order no longer holds once the MCV comes out,
and with `mcv_safe_site` on an MCV waiting for its escort or on its way whose
site becomes such a place is sited again from where it stands, through the
same choice made again (once in 250 ticks at most), as a `site` event with
the reason `enemy_building_near`; with no site left it goes home as it does
without one.

With `mcv_safe_site` or `mcv_repeat` on, a site, whether chosen or being
travelled to, qualifies only on the base's own side (`_is_own_side`): its
distance from the base's center is at most `mcv_own_side_share` of its
distance from the enemy's start, the spawn the world model takes as the
enemy's. A yard toward the enemy meets the enemy's own expansion and turrets
before our army can hold it. Every choice of a site passes over a place on the
enemy's side, a site chosen at the order no longer holds once the MCV comes
out, and an MCV whose refused deploy would be tried next on the enemy's side
does as after too many refusals, with the reason `own_side`. With
`mcv_safe_site` on an MCV waiting or on its way whose site no longer
qualifies is sited again as for `enemy_building_near`, with the reason
`own_side`. While the enemy's start (or the base's center) is unknown, the
rule does not apply.

An enemy army by the site is checked while the MCV travels too, not only
while it waits at home: a yard deployed beside an enemy army is lost before
it builds anything. While the MCV is on its way to its site or deploying there
(`travelling`), enemy combat units seen within `SITE_ARMY_SEEN_SECONDS`
standing within `SITE_ARMY_CLEAR_CELLS` of the site and worth together
`mcv_site_army_value` or more (`site_army`) make it hold short: it is stopped
once it stands `SITE_ARMY_HOLD_CELLS` or more from every unit of that group,
and moved that far from the nearest of them before, and one `expansion` event
of phase `mcv_hold` with the reason `site_army` is written. A deploy out is
dropped, and no deploy goes out while such a group stands there, whatever
cell the MCV tries. Once the group has stood there `mcv_site_hold_seconds`,
the MCV is sited again from its cell by the same rules with that field and
every other one it held short of passed over, as a `site` event of the
reason `site_army`, and goes home (`home_fallback`) when none is left. The
hold ends once the group is gone, and the MCV goes on to its site.

The second yard is due by a time (`is_second_mcv_due`): from
`second_mcv_due_seconds` on, with `mcv_repeat` on, while one own yard stands
and no MCV is out or on order, a base still waiting for the income to allow a
second yard, or for its money to pass `second_yard_min_cash`, loses the
neutral fields to the enemy. So then the yards allowed are two at the least
whatever the income, the money it waits for is the MCV's price alone, upkeep
asks its rule right after power and a lost core building (see `upkeep`), and
while the money is short the full hold for it holds with no time limit (see
`economy_hold`), a due refinery claim let through. The depot, the MCV on
offer, no major threat, no lost core building saved for, a far field that
qualifies and the Vehicle queue's room still apply; while one of them keeps
the MCV from being ordered, an `expansion` event of phase and reason
`second_mcv_blocked` names it (`blocked_by`), once in
`SECOND_MCV_BLOCKED_EVERY_TICKS` at most; the far field is named `own_side`
when one would qualify but for the own-side rule.
"""

from __future__ import annotations

import math
from typing import Callable, NamedTuple

from ucla_agent.core import records
from ucla_agent.games.redalert import cells, coverage, creep, placement, raid_routes, refinery_fields, waypoints
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.family import CONSTRUCTION_YARD, HARVESTER, MCV, REFINERY, SERVICE_DEPOT
from ucla_agent.games.redalert.fight import is_counted, strength, worth
from ucla_agent.games.redalert.groups import MAIN
from ucla_agent.games.redalert.items import TICKS_PER_SECOND, is_air_only
from ucla_agent.games.redalert.ledger import REJECTED
from ucla_agent.games.redalert.managers import MAJOR_THREAT, MatchContext
from ucla_agent.games.redalert.opening import is_build_complete
from ucla_agent.games.redalert.production import BUILDING_QUEUE, VEHICLE_QUEUE
from ucla_agent.games.redalert.scout_report import is_combat_unit
from ucla_agent.games.redalert.tank_factories import vehicle_queue_takes_one
from ucla_agent.games.redalert.terrain import OrePatch, Terrain
from ucla_agent.games.redalert.world import Building, Cell, EnemyRecord, Unit, World

EXPANSION_EVENT = "expansion"
# The phases an `expansion` event records.
DEPOT = "depot"
# With `mcv_repeat` on, a change of the yards the income allows; its reason too.
YARD_CAP = "yard_cap"
MCV_TRAINED = "mcv_trained"
MCV_MOVING = "mcv_moving"
MCV_WAITING = "mcv_waiting"
DEPLOYED = "deployed"
DEPLOYED_HOME = "deployed_home"
DEPLOY_REFUSED = "deploy_refused"
LOST = "lost"
# With `mcv_safe_site` on, each choice of the MCV's site.
SITE = "site"
# With `mcv_new_area` on, a site chosen for want of a field it lasts by.
SITE_CHECK = "site_check"
# The group the main units that go with the MCV belong to while they do: the combat manager leaves them alone.
ESCORT = "escort"
# A remembered enemy defense this close to the site, or an enemy army stronger than the escort this close, keeps the
# MCV at home.
SITE_DEFENSE_CLEAR_CELLS = 10
SITE_ARMY_CLEAR_CELLS = 15
# While the MCV travels, an enemy unit counts toward the army by its site when it was seen this lately, and the MCV
# holds short at least this far from every unit of that army (see `site_army`).
SITE_ARMY_SEEN_SECONDS = 10
SITE_ARMY_HOLD_CELLS = 15
# The phase of the `expansion` event of a travelling MCV that holds short of an enemy army by its site; its reason,
# which a `site` event of the MCV sited again after the hold gives as well.
MCV_HOLD = "mcv_hold"
REASON_SITE_ARMY = "site_army"
# The MCV counts as at a place this close to it.
ARRIVED_CELLS = 1
# Where the top-left corner of the yard an MCV unfolds lies from the MCV's cell: the engine's Offset -1,-1.
YARD_OFFSET = (-1, -1)
# How many times a deploy the game refuses at the site is tried again, each one cell farther from the ore.
DEPLOY_RETRIES = 3
# How many deploys left without effect send the MCV home: the engine said nothing, and the next place may be no better.
IGNORED_DEPLOYS_HOME = 2
# How long a deploy without a verdict waits before it is sent again, as the opening's does.
_DEPLOY_RESEND_TICKS = 75
# After this many resends a deploy the MCV stands idle on its cell after is left without effect; after the most it is
# whatever the MCV does.
_RESENDS_BEFORE_IGNORED = 1
_MOST_RESENDS = 2
# How much farther from the ore the MCV tries after a deploy left without effect: past ARRIVED_CELLS, so that it moves
# off the cell the engine would not unfold on rather than deploy there again.
_IGNORED_STEP_CELLS = ARRIVED_CELLS + 1
# An escort unit this close to the site is there.
_AT_SITE_CELLS = 6
# How many cells around the yard box of the MCV's site the escort keeps clear of, so that the MCV reaches its site and
# the yard has room to unfold: the engine unfolds no yard over a unit.
ESCORT_CLEAR_CELLS = 2
# How many rings of cells around that ground a free cell for an escort unit is looked for in, the nearest ring first.
_CLEAR_RINGS = 4
# The percent of full health a unit's worth is counted at when it is not known.
_FULL_HEALTH = 100

# The reasons an `expansion` event gives.
_REASON_SECOND_YARD = "second_yard"
_REASON_ESCORTED = "escorted"
_REASON_DEFENSE = "defense_near_site"
_REASON_ARMY = "enemy_army_near_site"
_REASON_ESCORT_SHORT = "escort_short"
# With `mcv_short_escort` on, the reason of an MCV setting out short of its escort.
_REASON_ESCORT_SHORT_TIMEOUT = "escort_short_timeout"
# With `mcv_new_area` and `mcv_safe_site` on, the reason of an MCV setting out once it has waited out its escort.
_REASON_ESCORT_WAIT_TIMEOUT = "escort_wait_timeout"
# With `mcv_repeat` on, the reason of a further MCV's events, and of its waiting and its `site` event without a site.
_REASON_REPEAT = "repeat"
_REASON_REPEAT_NO_SITE = "repeat_no_site"
_REASON_THREAT = "major_threat"
_REASON_WAITED = "waited_too_long"
_REASON_NO_ROOM = "no_room_at_site"
# The reasons of a `deploy_refused` event: the game refused the deploy, or left it without effect.
_REASON_REJECTED = "rejected"
_REASON_IGNORED = "ignored"
_REASON_NO_SITE = "no_site"
_REASON_AT_SITE = "at_site"
_REASON_MCV_LOST = "mcv_lost"
_REASON_YARD_LOST = "second_yard_lost"
# The reasons of a `site` event: the nearest field a safe route reaches, and home for want of one.
_REASON_NEAREST_SAFE = "nearest_safe"
_REASON_HOME_FALLBACK = "home_fallback"
# The reasons of a `site` event for an MCV sited again on its way: endangered, or its site's field taken meanwhile.
_REASON_REROUTED = "rerouted"
_REASON_TAKEN = "taken"
# The reason of a `site` event for an MCV sited again because a known enemy yard, refinery or ground defense stands
# within `mcv_enemy_building_cells` of its site.
_REASON_ENEMY_BUILDING_NEAR = "enemy_building_near"
# The reason of a `site` event for an MCV sited again, and of its deploy at home after a refusal, because its site, or
# the next place tried, does not lie on the base's own side (`_is_own_side`).
_REASON_OWN_SIDE = "own_side"
# The reason of a `site_check` event: no field the site lasts by has a place a safe route reaches.
_REASON_SITE_FALLBACK = "site_fallback"
# With `mcv_safe_site` on, an MCV on its way is sited again when unsafe ground comes this near it or it is hurt, but
# not twice within this many ticks: each choice reads the whole of the enemy's memory, and a choice made again at once
# would only turn the MCV round and round.
RESITE_DANGER_CELLS = 8
RESITE_EVERY_TICKS = 250
# The decimal places a `site` event gives its distance with, and a `yard_cap` event its income.
_DISTANCE_DECIMALS = 2
_INCOME_DECIMALS = 2
# How many yards stand once the second yard does: with `mcv_repeat` off, the most there are.
SECOND_YARD_COUNT = 2
# The phase and reason of the `expansion` event of a second MCV that is due by its time and kept from its order, which
# is written once in this many ticks at most, and what its `blocked_by` names, the first that holds: no depot standing
# or no MCV on offer, a major threat, a lost core building saved for, no far field that qualifies (`own_side` when one
# would but for the own-side rule), the money short of the MCV's price, and the Vehicle queue without room or held by
# another manager.
SECOND_MCV_BLOCKED = "second_mcv_blocked"
SECOND_MCV_BLOCKED_EVERY_TICKS = 30 * TICKS_PER_SECOND
BLOCKED_BY_DEPOT = "depot"
BLOCKED_BY_THREAT = "major_threat"
BLOCKED_BY_CORE = "core_saved"
BLOCKED_BY_SITE = "site"
BLOCKED_BY_OWN_SIDE = "own_side"
BLOCKED_BY_MONEY = "money"
BLOCKED_BY_QUEUE = "queue"


def yards(world: World) -> list[Cell]:
    """The cells of the own construction yards that stand."""
    return [(building.x, building.y) for building in world.buildings.values() if building.type == CONSTRUCTION_YARD]


def log_expansion(
    world: World, phase: str, cell: Cell | None, field: OrePatch | None, reason: str, **more: float | None
) -> None:
    """Write one `expansion` event, with what more is given."""
    records.log(
        EXPANSION_EVENT,
        {
            "tick": world.tick,
            "phase": phase,
            "cell": None if cell is None else list(cell),
            "field_center": None if field is None else list(field.center),
            "reason": reason,
            **more,
        },
    )


class ExpansionDepot:
    """Orders the service depot the MCV needs, early. See the module's note."""

    def __init__(
        self,
        build_within_power: Callable[[str, World, MatchContext], list[Intent]],
        saved_for: Callable[..., str | None],
    ) -> None:
        # How upkeep orders a building within the power target, and which lost core building it saves for.
        self._build_within_power = build_within_power
        self._saved_for = saved_for

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for the service depot, or for the power plant it waits for, when it is due."""
        if not ctx.tuning.expansion.second_yard or not _is_depot_due(world, ctx):
            return []
        if self._saved_for(world, ctx) is not None:
            return []
        orders = self._build_within_power(SERVICE_DEPOT, world, ctx)
        if any(proposal.item == SERVICE_DEPOT for proposal in ctx.production.proposed()):
            log_expansion(world, DEPOT, None, None, _REASON_SECOND_YARD)
        return orders


def _is_depot_due(world: World, ctx: MatchContext) -> bool:
    """Whether the early depot is due: the opening's build is complete (a war factory stands; under factory_first,
    whose last step is the depot, the depot stands), it is late enough, no depot stands or is on its way, one is on
    offer, the Building queue is free, the base is under no major threat and the money above the reserve pays for
    it."""
    production, tuning = ctx.production, ctx.tuning
    is_next = is_build_complete(production, tuning.opening.order) and not production.planned(SERVICE_DEPOT)
    is_in_time = world.game_seconds >= tuning.expansion.second_yard_from_seconds
    can_start = SERVICE_DEPOT in world.available_production and production.is_queue_free(BUILDING_QUEUE)
    has_money = world.economy.spendable >= production.cost(SERVICE_DEPOT) + tuning.upkeep.production_cash_reserve
    return is_next and is_in_time and can_start and has_money and ctx.threat_level != MAJOR_THREAT


class ExpansionMcv:
    """Trains the one MCV of the second yard on the Vehicle queue. See the module's note."""

    def __init__(self, saved_for: Callable[..., str | None], forward: ForwardMcv) -> None:
        # Which lost core building upkeep saves for, and the rule that takes the MCV to its site once it is out.
        self._saved_for = saved_for
        self._forward = forward
        # The tick the last `second_mcv_blocked` event was written at; None before one was.
        self._blocked_at: int | None = None

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for the MCV when it is due; the rule that takes it to its site is told of the order. A second MCV
        due by its time (`is_second_mcv_due`) and not ordered is written as blocked (`_note_blocked`)."""
        if not ctx.tuning.expansion.second_yard:
            return []
        saved_for = self._saved_for(world, ctx)
        if not _is_mcv_due(world, ctx) or saved_for is not None:
            self._note_blocked(saved_for, world, ctx)
            return []
        self._forward.note_ordered(world, ctx)
        return [ctx.production.train(MCV)]

    def _note_blocked(self, saved_for: str | None, world: World, ctx: MatchContext) -> None:
        """While the second MCV is due by its time, write an `expansion` event of phase and reason
        `second_mcv_blocked` naming what keeps it from its order (`_blocked_by`), unless one was written within
        `SECOND_MCV_BLOCKED_EVERY_TICKS`."""
        if not is_second_mcv_due(world, ctx):
            return
        if self._blocked_at is not None and world.tick - self._blocked_at < SECOND_MCV_BLOCKED_EVERY_TICKS:
            return
        self._blocked_at = world.tick
        reason = _blocked_by(saved_for, world, ctx)
        log_expansion(world, SECOND_MCV_BLOCKED, None, None, SECOND_MCV_BLOCKED, blocked_by=reason)


def _blocked_by(saved_for: str | None, world: World, ctx: MatchContext) -> str:
    """What keeps a second MCV due by its time from its order, the first that holds: no depot or no MCV on offer, a
    major threat, a lost core building saved for, no site (the own side, when the own-side rule alone leaves none), the
    money, else the Vehicle queue."""
    production = ctx.production
    if not production.standing(SERVICE_DEPOT) or MCV not in world.available_production:
        return BLOCKED_BY_DEPOT
    if ctx.threat_level == MAJOR_THREAT:
        return BLOCKED_BY_THREAT
    if saved_for is not None:
        return BLOCKED_BY_CORE
    if ctx.repeat_mcv_site is None:
        return BLOCKED_BY_OWN_SIDE if _is_own_side_blocked(world, ctx) else BLOCKED_BY_SITE
    return BLOCKED_BY_QUEUE if has_mcv_money(world, ctx) else BLOCKED_BY_MONEY


def _is_own_side_blocked(world: World, ctx: MatchContext) -> bool:
    """Whether the own-side rule alone leaves the next MCV without a site: from the main yard `far_site` finds none, and
    finds one with the rule waived."""
    main = main_yard(world)
    if main is None:
        return False
    return far_site(world, ctx, main) is None and far_site(world, ctx, main, own_side_rule=False) is not None


def _is_mcv_due(world: World, ctx: MatchContext) -> bool:
    """Whether the MCV is due: it is wanted (`is_mcv_wanted`), the money is there (`has_mcv_money`), and the Vehicle
    queue takes it (it is free, or with `tank_factories` on, it has room) and is claimed by no other manager."""
    is_left_to_upkeep = not ctx.claims.is_held_by_other(VEHICLE_QUEUE, "upkeep")
    can_train = vehicle_queue_takes_one(ctx) and is_left_to_upkeep
    return is_mcv_wanted(world, ctx) and has_mcv_money(world, ctx) and can_train


def is_mcv_wanted(world: World, ctx: MatchContext) -> bool:
    """Whether the MCV is wanted, the money and the Vehicle queue aside: with `mcv_repeat` on, the next MCV's site is
    published (`ctx.repeat_mcv_site`), which it is only under the yards the income allows; with it off, fewer than two
    yards stand. No MCV lives, and `_may_want_more`."""
    if is_far_fields(ctx):
        is_yard_wanted = ctx.repeat_mcv_site is not None
    else:
        is_yard_wanted = len(yards(world)) < SECOND_YARD_COUNT
    return is_yard_wanted and not ctx.production.units(MCV) and _may_want_more(world, ctx)


def _may_want_more(world: World, ctx: MatchContext) -> bool:
    """Whether one more MCV may be wanted, the yards, an MCV out, the money and the Vehicle queue aside: a depot stands,
    no MCV is on its way from the factory, the MCV is on offer and there is no major threat."""
    production = ctx.production
    is_quiet = ctx.threat_level != MAJOR_THREAT
    is_on_offer = MCV in world.available_production and not production.pending(MCV)
    return production.standing(SERVICE_DEPOT) > 0 and is_on_offer and is_quiet


def has_mcv_money(world: World, ctx: MatchContext) -> bool:
    """Whether the money for the MCV is there: the money less the production reserve covers the MCV's price and reaches
    `second_yard_min_cash`; for a second MCV due by its time (`is_second_mcv_due`), the money covers its price."""
    tuning = ctx.tuning
    if is_second_mcv_due(world, ctx):
        return world.economy.spendable >= ctx.production.cost(MCV)
    least = max(tuning.expansion.second_yard_min_cash, ctx.production.cost(MCV))
    return world.economy.spendable - tuning.upkeep.production_cash_reserve >= least


def is_second_mcv_due(world: World, ctx: MatchContext) -> bool:
    """Whether the second MCV is due by its time: with `second_yard` and `mcv_repeat` on, from
    `second_mcv_due_seconds` on, one own yard stands and no MCV is out or on order."""
    production = ctx.production
    is_alone = len(yards(world)) == 1 and not production.units(MCV) and not production.pending(MCV)
    return is_far_fields(ctx) and world.game_seconds >= ctx.tuning.expansion.second_mcv_due_seconds and is_alone


def yards_allowed_now(world: World, ctx: MatchContext) -> int:
    """How many yards may stand now: those the income allows (`yards_allowed`), and while the second MCV is due by its
    time (`is_second_mcv_due`), two at the least."""
    allowed = yards_allowed(ctx)
    return max(allowed, SECOND_YARD_COUNT) if is_second_mcv_due(world, ctx) else allowed


def yards_allowed(ctx: MatchContext) -> int:
    """With `mcv_repeat` on, how many yards the income allows: one, and one more for each `yards_income_per_yard` of
    income a second as the income estimate reports it (none while it is unknown), `max_yards` at the most."""
    expansion = ctx.tuning.expansion
    income = ctx.income.income_per_s or 0.0
    return min(expansion.max_yards, 1 + math.floor(income / expansion.yards_income_per_yard))


def _yards_and_mcvs(world: World, ctx: MatchContext) -> int:
    """How many yards stand or may: the own construction yards standing, and the MCVs out or on their way."""
    return len(yards(world)) + ctx.production.units(MCV) + ctx.production.pending(MCV)


def is_far_fields(ctx: MatchContext) -> bool:
    """Whether every MCV goes to a far field that lasts (`far_site`): `second_yard` and `mcv_repeat` on."""
    expansion = ctx.tuning.expansion
    return expansion.second_yard and expansion.mcv_repeat


def is_mcv_first(ctx: MatchContext) -> bool:
    """Whether the second yard's MCV is bought first (see `economy_hold`): `mcv_first` of [upkeep] is on, and
    `second_yard`, without which there is no MCV to buy."""
    return ctx.tuning.upkeep.mcv_first and ctx.tuning.expansion.second_yard


class LastHarvest:
    """The cell each own harvester last stood on ore at: where it last harvested, as far as the state tells."""

    def __init__(self) -> None:
        # Harvester id -> its last cell on ore, for the harvesters that live.
        self._cells: dict[int, Cell] = {}

    def fields(self, world: World, terrain: Terrain) -> frozenset[OrePatch]:
        """Note the cell of each own harvester that stands on ore now and forget those that are gone; the fields a
        last cell on ore lies in."""
        here = {unit.id: (unit.x, unit.y) for unit in world.units.values() if unit.type == HARVESTER}
        on_ore = {
            harvester: cell
            for harvester, cell in here.items()
            if any(cell in field.cells for field in terrain.ore_patches)
        }
        self._cells = {harvester: cell for harvester, cell in self._cells.items() if harvester in here} | on_ore
        last = set(self._cells.values())
        return frozenset(field for field in terrain.ore_patches if not last.isdisjoint(field.cells))


class ForwardMcv:
    """Takes the MCV to its site with an escort, waits, and deploys it there or at home. See the module's note."""

    def __init__(self) -> None:
        self._mcv: int | None = None
        self._site: Cell | None = None
        self._field: OrePatch | None = None
        # Whether the MCV deploys at home, after waiting too long or being refused at its site too often, and why.
        self._is_home = False
        self._home_reason = _REASON_NO_SITE
        # How many deploys were refused, at the site and at home, and how many of them were left without effect.
        self._refused = 0
        self._ignored = 0
        # The tick the last deploy went out at and the MCV's cell then; None while none is out. How many times the
        # deploy at this place was sent again.
        self._deploy_sent: int | None = None
        self._deploy_cell: Cell | None = None
        self._resends = 0
        # How many ticks the MCV has waited in all, and the tick of the cycle before, since it was taken up.
        self._waited = 0
        self._last_tick = 0
        # Whether the MCV is on its way: the last cycle sent it to its site; and whether it waits at home.
        self._is_moving = False
        self._is_waiting = False
        self._yards_before: set[int] = set()
        # The ids of the yards this rule deployed that stand: the second, and with `mcv_repeat` on the further ones.
        self._deployed: set[int] = set()
        # With `mcv_repeat` on: whether the MCV is a further one, taken up while two yards or more stood; the tick it last
        # looked for a site while it had none; and the cell the place it deploys at by no field is looked for around,
        # the second yard's or its own (None for a deploy at home).
        self._is_further = False
        self._looked_at = 0
        self._home_anchor: Cell | None = None
        # With `second_mcv_waits` on: the tick the second MCV began to wait by home for a site again after no safe site
        # was left on its way; None while it does not.
        self._waits_since: int | None = None
        # With `mcv_safe_site` on: the MCV's health in the cycle before, and the tick it was last sited again.
        self._hp: int | None = None
        self._resited_at: int | None = None
        # With `mcv_reserve_field` on: the site chosen when the MCV was ordered, until the MCV is taken up or the order
        # comes to nothing; None while there is none.
        self._ordered: SiteChoice | None = None
        # With `mcv_short_escort` on: how many ticks in a row the want of its escort has been the one reason the MCV
        # waited, counted apart from `_waited`.
        self._short_waited = 0
        # With `waypoints` of [routing] and `mcv_safe_site` on: the cells of the way through the graph of waypoints the
        # MCV has still to pass on its way, short of its site, and the site that way leads to; none while it goes
        # straight.
        self._legs: list[Cell] = []
        self._legs_to: Cell | None = None
        # With `mcv_new_area` on: where each own harvester last harvested.
        self._last_harvest = LastHarvest()
        # With `mcv_repeat` on: the yards the income allowed when last looked at, None before; and the next MCV's site.
        self._cap: int | None = None
        self._sites = _FarSites()
        # The MCV's last cell outside the ground its escort keeps clear of (`clear_area`), the side it comes from; None
        # before it was seen there.
        self._approach: Cell | None = None
        # The id of the yard that last deployed by the field of its site and that field, while the refinery it claims
        # is not ordered yet; None while there is none.
        self._new_yard: tuple[int, OrePatch] | None = None
        # The tick the travelling MCV began to hold short of an enemy army by its site, None while it does not; and the
        # fields of the sites it held short of, which a choice made again for such an army passes over.
        self._army_held_at: int | None = None
        self._army_fields: set[OrePatch] = set()

    def publish_spent_fields(self, world: World, ctx: MatchContext, dry: frozenset[OrePatch]) -> None:
        """Publish the fields marked dry (`ctx.dry_fields`), which no site lasts by and no refinery is placed against;
        and with `second_yard` and `mcv_new_area` on the fields an own harvester's last cell on ore lies in
        (`ctx.harvested_fields`), none with either off."""
        ctx.dry_fields = dry
        expansion = ctx.tuning.expansion
        if not (expansion.second_yard and expansion.mcv_new_area):
            ctx.harvested_fields = frozenset()
            return
        ctx.harvested_fields = self._last_harvest.fields(world, ctx.terrain)

    def note_ordered(self, world: World, ctx: MatchContext) -> None:
        """With `mcv_reserve_field` on, choose the site of the MCV ordered in this cycle (`_ordered_site`), so that its
        field is reserved from now on; nothing with the switch off, when the site is chosen once the MCV is out."""
        if ctx.tuning.expansion.mcv_reserve_field:
            self._ordered = _ordered_site(world, ctx)

    def site_box(self, world: World) -> placement.Box | None:
        """The yard box (`yard_box`) of the MCV's site while the MCV lives, has a site and has not deployed: upkeep
        asks no building onto it, so that the yard has room to unfold; None otherwise."""
        if self._mcv not in world.units or self._site is None:
            return None
        return yard_box(self._site)

    def reserved_field(self, world: World, ctx: MatchContext) -> OrePatch | None:
        """With `mcv_reserve_field` on, the field of the MCV's site: the site chosen at the order while the MCV is on
        order or out and not yet taken up, then the site it goes to while it lives, until it deploys, is lost or is
        sent home; None otherwise. An order that came to nothing, no MCV on order or out, drops its site."""
        if not ctx.tuning.expansion.mcv_reserve_field:
            return None
        if self._mcv is not None:
            return self._field if self._mcv in world.units else None
        if not (ctx.production.pending(MCV) or ctx.production.units(MCV)):
            self._ordered = None
        return None if self._ordered is None else self._ordered.field

    def new_yard_field(self, world: World, ctx: MatchContext) -> OrePatch | None:
        """The field a refinery is claimed for by the yard that last deployed by it (see the module's note): None once
        that yard is lost or the field has `max_refineries_per_field` refineries serving it or asked against it, which
        ends the claim, and while there is none."""
        if self._new_yard is None:
            return None
        yard, field = self._new_yard
        if yard not in _yard_ids(world) or _has_its_refineries(field, world, ctx):
            self._new_yard = None
        return None if self._new_yard is None else field

    def skip_crawl_target(self, world: World, ctx: MatchContext) -> None:
        """With `mcv_repeat` on, put the crawl target's field (`ctx.creep_target`, as the last cycle named it) among the
        fields placement skipped once an MCV on order or out goes to it, so that the crawl names its next target."""
        field = self._mcv_field(world)
        target = ctx.creep_target
        is_crawl_field = field is not None and target is not None and target.patch == field
        if is_far_fields(ctx) and is_crawl_field and field not in ctx.skipped_fields:
            ctx.skipped_fields.append(field)

    def _mcv_field(self, world: World) -> OrePatch | None:
        """The field of the site of the MCV out, or of the one on order whose site was chosen as it was ordered; None
        while there is none."""
        if self._mcv in world.units:
            return self._field
        return None if self._ordered is None else self._ordered.field

    def end_new_yard_claim(self) -> None:
        """End the claim of the yard that last deployed by a field: its refinery is ordered."""
        self._new_yard = None

    def publish_repeat_site(self, world: World, ctx: MatchContext) -> None:
        """Publish, once a cycle, where the next MCV deploys (`repeat_site`) as `ctx.repeat_mcv_site`, with its field as
        `ctx.repeat_mcv_field`; both None while no MCV is to be sited or no site is found. With `mcv_repeat` on the
        yards the income allows are looked at first (`_note_cap`), and an MCV is sited while `_may_repeat` holds: with
        no MCV out, while one more may be wanted (`_may_want_more`) and the yards and MCVs are fewer than allowed, with
        the route from the main yard; with one out that is to be sited now (`_needs_repeat_site`), with the route from
        its cell."""
        ctx.repeat_mcv_site = ctx.repeat_mcv_field = None
        if is_far_fields(ctx):
            self._note_cap(world, ctx)
        start = self._repeat_start(world, ctx) if _may_repeat(world, ctx) else None
        choice = None if start is None else self._sites.site(world, ctx, start)
        if choice is not None:
            ctx.repeat_mcv_site, ctx.repeat_mcv_field = choice.site, choice.field

    def _note_cap(self, world: World, ctx: MatchContext) -> None:
        """Write an `expansion` event of phase and reason `yard_cap` whenever the yards the income allows
        (`yards_allowed`) differ from the last look's, with the income, the standing yards, the MCVs out or on their
        way and the cap."""
        allowed = yards_allowed(ctx)
        if allowed == self._cap:
            return
        self._cap, income = allowed, ctx.income.income_per_s
        mcvs = ctx.production.units(MCV) + ctx.production.pending(MCV)
        rounded = None if income is None else round(income, _INCOME_DECIMALS)
        log_expansion(
            world, YARD_CAP, None, None, YARD_CAP, income=rounded, yards=len(yards(world)), mcvs=mcvs, cap=allowed
        )

    def _repeat_start(self, world: World, ctx: MatchContext) -> Cell | None:
        """Where the next MCV's route starts in this cycle: the cell of the MCV out when it is to be sited now, the main
        yard's middle while none is out, one more may be wanted and the yards and MCVs are fewer than the income allows
        (`yards_allowed`); None while no MCV is to be sited."""
        mcv = next((unit for unit in world.units.values() if unit.type == MCV), None)
        if mcv is not None:
            if not self._needs_repeat_site(mcv, world.tick):
                return None
            self._looked_at = world.tick
            return mcv.x, mcv.y
        if not _may_want_more(world, ctx) or _yards_and_mcvs(world, ctx) >= yards_allowed_now(world, ctx):
            return None
        return main_yard(world)

    def _needs_repeat_site(self, mcv: Unit, tick: int) -> bool:
        """Whether the MCV out is to be given a site in this cycle: it is a new one, not taken up yet, or a further one
        that waits without a site and last looked for one `RESITE_EVERY_TICKS` ago or more."""
        if self._mcv != mcv.id:
            return True
        is_without_site = self._is_further and self._field is None and not self._is_home
        return is_without_site and tick - self._looked_at >= RESITE_EVERY_TICKS

    def note_deploys(self, world: World, ctx: MatchContext) -> None:
        """With `second_yard` on, note at the start of a cycle the yard the MCV became and each yard this rule deployed
        that is lost (`_note_yards`), so that a new yard's claim is known to every rule of the cycle."""
        if ctx.tuning.expansion.second_yard:
            self._note_yards(_yard_ids(world), world, ctx)

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The orders that take the MCV to its site, keep it waiting at home, or deploy it; the yards are noted at the
        start of the cycle (`note_deploys`)."""
        if not ctx.tuning.expansion.second_yard:
            return []
        mcv = self._the_mcv(world, ctx, _yard_ids(world))
        if mcv is None:
            return []
        self._last_tick, elapsed = world.tick, world.tick - self._last_tick
        self._note_approach(mcv)
        holding = self._hold_for_site_army(mcv, world, ctx)
        if holding is not None:
            return holding
        if self._deploy_sent is not None:
            return [*self._clear_site(world, ctx), *self._deploying(mcv, world, ctx)]
        if ctx.mcv_halt is not None:
            # main is too weak for an army closing on the MCV: it holds or goes home instead (see `closing_intercept`).
            return [Intent(Priority.MOVE, {"cmd": "move", "units": [mcv.id], "to": list(ctx.mcv_halt)})]
        if ctx.tuning.expansion.mcv_safe_site:
            self._resite_when_endangered(mcv, world, ctx)
        if self._is_home:
            return self._go_and_deploy(mcv, world)
        if self._field is None:
            return self._wait_for_site(mcv, elapsed, world, ctx)
        waiting = self._reason_to_wait(mcv, world, ctx)
        going = self._going(mcv, waiting, elapsed, world, ctx)
        if going is None:
            return self._wait(mcv, waiting, elapsed, world, ctx)
        return self._go(mcv, world, ctx, going)

    def _note_yards(self, yard_ids: set[int], world: World, ctx: MatchContext) -> None:
        """Note the yard the MCV became, and each yard this rule deployed that is lost."""
        new = yard_ids - self._yards_before
        if self._mcv is not None and self._mcv not in world.units and new:
            self._deployed.add(min(new))
            if not self._is_home and self._field is not None:
                self._new_yard = min(new), self._field
            phase = DEPLOYED_HOME if self._is_home else DEPLOYED
            reason = _REASON_AT_SITE if not self._is_home else self._home_reason
            log_expansion(world, phase, self._site, self._field, reason)
            self._release_escort(ctx)
            self._mcv = None
        for _ in sorted(self._deployed - yard_ids):
            log_expansion(world, LOST, None, None, _REASON_YARD_LOST)
        self._deployed &= yard_ids
        self._yards_before = yard_ids

    def _the_mcv(self, world: World, ctx: MatchContext, yard_ids: set[int]) -> Unit | None:
        """The MCV of the second yard, once a yard stands: a new one is taken up and given its site; one that is gone
        without a new yard is written as lost. None while there is none, or while no yard stands, when the MCV is the
        opening's to deploy."""
        if self._mcv is not None and self._mcv not in world.units:
            log_expansion(world, LOST, self._site, self._field, _REASON_MCV_LOST)
            self._release_escort(ctx)
            self._mcv = None
        if not yard_ids:
            return None
        if self._mcv is None:
            mcv = next((unit for unit in world.units.values() if unit.type == MCV), None)
            if mcv is None:
                return None
            self._take_up(mcv, world, ctx)
        return world.units[self._mcv]

    def _take_up(self, mcv: Unit, world: World, ctx: MatchContext) -> None:
        """Start over with a new MCV: a further one with `mcv_repeat` on while two yards or more stand, its site
        (`_site_at_take_up`), and nothing waited or refused yet. A further MCV without a site does not deploy at home.
        """
        self._mcv, ordered, self._ordered = mcv.id, self._ordered, None
        self._is_further = ctx.tuning.expansion.mcv_repeat and len(yards(world)) >= SECOND_YARD_COUNT
        self._site, self._field, home_reason = self._site_at_take_up(ordered, mcv, world, ctx)
        self._refused, self._waited, self._deploy_sent, self._is_moving, self._is_waiting = 0, 0, None, False, False
        self._ignored, self._resends, self._deploy_cell = 0, 0, None
        self._last_tick, self._hp, self._resited_at, self._short_waited = world.tick, mcv.hp, None, 0
        self._is_home, self._home_reason = self._field is None and not self._is_further, home_reason
        self._looked_at, self._home_anchor, self._approach = world.tick, None, None
        self._army_held_at, self._army_fields, self._waits_since = None, set(), None
        reason = _REASON_REPEAT if self._is_further else _REASON_SECOND_YARD
        log_expansion(world, MCV_TRAINED, self._site, self._field, reason)

    def _site_at_take_up(
        self, ordered: SiteChoice | None, mcv: Unit, world: World, ctx: MatchContext
    ) -> tuple[Cell | None, OrePatch | None, str]:
        """A new MCV's site, its field and why it deploys at home without one: the site chosen at its order while that
        still holds (`_still_holds`); for a further MCV the one published for it from its cell (`_further_site`); else
        `_first_site`'s."""
        if ordered is not None and _still_holds(ordered, mcv, world, ctx):
            return ordered.site, ordered.field, _REASON_NO_SITE
        if self._is_further:
            return *_further_site(world, ctx), _REASON_REPEAT_NO_SITE
        return _first_site(mcv, world, ctx)

    def _wait_for_site(self, mcv: Unit, elapsed: int, world: World, ctx: MatchContext) -> list[Intent]:
        """An MCV without a site: the second one waiting by home with `second_mcv_waits` on (`_wait_for_safe_site`);
        else a further one, for which the site published from its cell, looked for once in `RESITE_EVERY_TICKS`, is its
        site from now on and it goes on as any MCV from the next cycle; without one it waits at home with the reason
        `repeat_no_site` (see `_wait`)."""
        if self._waits_since is not None:
            return self._wait_for_safe_site(mcv, world, ctx)
        if ctx.repeat_mcv_field is None:
            return self._wait(mcv, _REASON_REPEAT_NO_SITE, elapsed, world, ctx)
        self._site, self._field = _further_site(world, ctx)
        # A wait from now on is for another reason, and is written.
        self._is_waiting = False
        return []

    def _wait_for_safe_site(self, mcv: Unit, world: World, ctx: MatchContext) -> list[Intent]:
        """The second MCV left with no safe site on its way, with `second_mcv_waits` on: it waits by home without
        deploying, its escort back with main, and looks for a site from where it stands (`safe_site`) once in
        `RESITE_EVERY_TICKS`; a site found is its site from now on and it sets out again from the next cycle. After
        `second_mcv_wait_seconds` with none it deploys at home, so that it never waits without end."""
        tick = world.tick
        if tick - self._waits_since >= ctx.tuning.expansion.second_mcv_wait_seconds * TICKS_PER_SECOND:
            self._waits_since, self._is_home, self._home_reason = None, True, _REASON_HOME_FALLBACK
            self._site = home_site(world, ctx) or (mcv.x, mcv.y)
            return self._go_and_deploy(mcv, world)
        if tick - self._looked_at >= RESITE_EVERY_TICKS:
            self._looked_at = tick
            choice = safe_site(world, ctx, (mcv.x, mcv.y))
            if choice.field is not None:
                _log_site(choice, _REASON_NEAREST_SAFE, world, ctx)
                self._site, self._field, self._waits_since, self._is_waiting = choice.site, choice.field, None, False
                return []
        if not self._is_waiting:
            log_expansion(world, MCV_WAITING, None, None, _REASON_HOME_FALLBACK)
        self._is_waiting = True
        was_moving, self._is_moving = self._is_moving, False
        home = home_site(world, ctx)
        if not was_moving or home is None:
            return []
        return [Intent(Priority.MOVE, {"cmd": "move", "units": [mcv.id], "to": list(home)})]

    def _resite_when_endangered(self, mcv: Unit, world: World, ctx: MatchContext) -> None:
        """With `mcv_safe_site` on, choose the site again from where the MCV stands, by the same rules, when a known
        enemy yard, refinery or ground defense stands near its site or the site no longer lies on the base's own side,
        or when on its way it lost health or came within `RESITE_DANGER_CELLS` of unsafe ground, or with `mcv_new_area`
        on its site's field was taken meanwhile (`_resite_reason`), once in `RESITE_EVERY_TICKS` at most: a field
        reached safely from there is its site from now on, written with the reason, and without one it deploys at home
        (`home_fallback`). A further MCV is sited again by `repeat_site`, and without a site waits for one
        (`repeat_no_site`)."""
        was_hurt, self._hp = self._hp is not None and mcv.hp < self._hp, mcv.hp
        if not self._may_resite(world.tick):
            return
        reason = self._resite_reason(was_hurt, mcv, world, ctx)
        if reason is None:
            return
        self._resited_at = world.tick
        start = (mcv.x, mcv.y)
        choice = (repeat_site(world, ctx, start) or _NO_SITE) if self._is_further else safe_site(world, ctx, start)
        if choice.field is None:
            _log_site(choice, _REASON_REPEAT_NO_SITE if self._is_further else _REASON_HOME_FALLBACK, world, ctx)
            self._go_home(world, ctx, _REASON_HOME_FALLBACK)
            return
        _log_site(choice, reason, world, ctx)
        self._site, self._field = choice.site, choice.field

    def _resite_reason(self, was_hurt: bool, mcv: Unit, world: World, ctx: MatchContext) -> str | None:
        """Why the MCV is sited again now: on its way, its site's field was taken meanwhile (with `mcv_new_area` on; a
        field reserved for it counts as untaken), or it was hurt or came near unsafe ground; else, whether it waits or
        is on its way, its site by a field no longer qualifies (`_unfit_site_reason`). None when it keeps its site."""
        if self._is_moving:
            is_reserved = self._field == ctx.reserved_field
            if ctx.tuning.expansion.mcv_new_area and not is_reserved and self._field in _taken(world, ctx):
                return _REASON_TAKEN
            if was_hurt or McvDangers(world, ctx).is_near((mcv.x, mcv.y), RESITE_DANGER_CELLS):
                return _REASON_REROUTED
        return None if self._field is None else _unfit_site_reason(self._site, world, ctx)

    def _may_resite(self, tick: int) -> bool:
        """Whether the MCV may be sited again: never yet, or `RESITE_EVERY_TICKS` ago or more."""
        return self._resited_at is None or tick - self._resited_at >= RESITE_EVERY_TICKS

    def travelling(self) -> bool:
        """Whether the MCV travels: it has a site by a field, does not deploy at home, and is on its way there or has a
        deploy out at it."""
        return self._field is not None and not self._is_home and (self._is_moving or self._deploy_sent is not None)

    def _hold_for_site_army(self, mcv: Unit, world: World, ctx: MatchContext) -> list[Intent] | None:
        """While the MCV travels and an enemy army stands by its site (`site_army`), the orders that hold it short
        (`_hold_short`); the hold's start drops a deploy out and is written as an `mcv_hold` event. Once the army has
        stood there `mcv_site_hold_seconds`, the MCV is sited again (`_resite_for_army`) and goes on as any MCV in this
        cycle: None. None as well while it does not travel or no such army stands there, which ends a hold."""
        army = site_army(self._site, world, ctx) if self.travelling() else []
        if not army:
            self._army_held_at = None
            return None
        if self._army_held_at is None:
            self._army_held_at, self._deploy_sent = world.tick, None
            value = round(sum(_worth(record, world) for record in army))
            log_expansion(world, MCV_HOLD, self._site, self._field, REASON_SITE_ARMY, value=value, units=len(army))
        elif world.tick - self._army_held_at >= ctx.tuning.expansion.mcv_site_hold_seconds * TICKS_PER_SECOND:
            self._army_held_at = None
            self._resite_for_army(mcv, world, ctx)
            return None
        return _hold_short(mcv, army, world)

    def _resite_for_army(self, mcv: Unit, world: World, ctx: MatchContext) -> None:
        """Site the MCV again from its cell, by the same rules, passing over the field of its site and every other it
        held short of an enemy army by: a `site` event of the reason `site_army`; without a site it goes home as a
        choice made again for danger does (`home_fallback`)."""
        self._army_fields.add(self._field)
        self._resited_at, start, skip = world.tick, (mcv.x, mcv.y), frozenset(self._army_fields)
        if self._is_further:
            choice = repeat_site(world, ctx, start, skip=skip) or _NO_SITE
        else:
            choice = safe_site(world, ctx, start, skip=skip)
        if choice.field is None:
            _log_site(choice, _REASON_REPEAT_NO_SITE if self._is_further else _REASON_HOME_FALLBACK, world, ctx)
            self._go_home(world, ctx, _REASON_HOME_FALLBACK)
            return
        _log_site(choice, REASON_SITE_ARMY, world, ctx)
        self._site, self._field = choice.site, choice.field

    def _reason_to_wait(self, mcv: Unit, world: World, ctx: MatchContext) -> str | None:
        """Why the MCV waits at home now; None when it may go to its site with its escort."""
        if ctx.threat_level == MAJOR_THREAT:
            return _REASON_THREAT
        if _is_defense_near(self._site, world):
            return _REASON_DEFENSE
        escort = self._escort(mcv, world, ctx)
        if strength(escort, world) < ctx.tuning.expansion.mcv_escort_strength:
            return _REASON_ESCORT_SHORT
        if _enemy_army_near(self._site, world) > strength(escort, world):
            return _REASON_ARMY
        return None

    def _escort(self, mcv: Unit, world: World, ctx: MatchContext) -> list[Unit]:
        """The units of the escort: those in it, then main's that it has not sent forward, the nearest the MCV first,
        until they are worth `mcv_escort_strength` or no more are left."""
        groups, wanted = ctx.groups, ctx.tuning.expansion.mcv_escort_strength
        escort = [world.combat_units[unit_id] for unit_id in groups.members(ESCORT)]
        home = [world.combat_units[unit_id] for unit_id in groups.members(MAIN) if unit_id not in ctx.army]
        for unit in sorted(home, key=lambda unit: cells.distance((unit.x, unit.y), (mcv.x, mcv.y))):
            if strength(escort, world) >= wanted:
                break
            escort.append(unit)
        return escort

    def _going(self, mcv: Unit, waiting: str | None, elapsed: int, world: World, ctx: MatchContext) -> str | None:
        """Why the MCV goes to its site in this cycle: `escorted` with no reason to wait; `escort_wait_timeout` once
        it has waited out its escort (`_goes_after_wait`); else `_short_escort_going`'s reason. None while it waits."""
        if waiting is None:
            self._short_waited = 0
            return _REASON_ESCORTED
        if self._goes_after_wait(mcv, waiting, world, ctx):
            return _REASON_ESCORT_WAIT_TIMEOUT
        return self._short_escort_going(mcv, waiting, elapsed, world, ctx)

    def _short_escort_going(self, mcv: Unit, waiting: str, elapsed: int, world: World, ctx: MatchContext) -> str | None:
        """With `mcv_short_escort` on, `escort_short_timeout` once the want of its escort (`escort_short`, and no enemy
        army near the site stronger than the escort it has) has been the one reason the MCV waited for
        `short_escort_wait_ticks` in a row, counted in `_short_waited`, which any other reason starts again, and it has
        escort units or its route is safe (`_is_route_safe`); None otherwise."""
        expansion = ctx.tuning.expansion
        escort = (
            self._escort(mcv, world, ctx) if expansion.mcv_short_escort and waiting == _REASON_ESCORT_SHORT else None
        )
        if escort is None or _enemy_army_near(self._site, world) > strength(escort, world):
            self._short_waited = 0
            return None
        self._short_waited += elapsed
        if self._short_waited < expansion.short_escort_wait_ticks:
            return None
        return _REASON_ESCORT_SHORT_TIMEOUT if escort or self._is_route_safe(mcv, world, ctx) else None

    def _goes_after_wait(self, mcv: Unit, waiting: str, world: World, ctx: MatchContext) -> bool:
        """With `mcv_new_area` and `mcv_safe_site` on, whether the MCV that has waited `mcv_wait_ticks` in all, the want
        of its escort the reason now, goes to the site it has: a safe route reaches the site from it (`_is_route_safe`).
        Without one it deploys at home as before; the wait never gives it another site."""
        expansion = ctx.tuning.expansion
        is_switched_on = expansion.mcv_new_area and expansion.mcv_safe_site
        is_waited_out = waiting == _REASON_ESCORT_SHORT and self._waited >= expansion.mcv_wait_ticks
        return is_switched_on and is_waited_out and self._is_route_safe(mcv, world, ctx)

    def _is_route_safe(self, mcv: Unit, world: World, ctx: MatchContext) -> bool:
        """Whether the MCV may go to its site alone: with `mcv_safe_site` on, a safe route reaches the site from the
        MCV (`safe_route`); else no remembered enemy defense stands within `SITE_DEFENSE_CLEAR_CELLS` of the site and no
        enemy army seen lately within `SITE_ARMY_CLEAR_CELLS`."""
        if ctx.tuning.expansion.mcv_safe_site:
            return safe_route((mcv.x, mcv.y), self._site, McvDangers(world, ctx), world, ctx) is not None
        return not _is_defense_near(self._site, world) and not _enemy_army_near(self._site, world)

    def _go(self, mcv: Unit, world: World, ctx: MatchContext, reason: str) -> list[Intent]:
        """The MCV and its escort on their way to the site, its setting out written with `reason`, by the cells of its
        way through the graph of waypoints (`_waypoint_legs`, taken as it sets out and when its site changes) while
        any is left to pass; the deploy once the MCV is there."""
        escort = self._escort(mcv, world, ctx)
        for unit in escort:
            if ctx.groups.group_of(unit.id) != ESCORT:
                ctx.groups.assign(unit.id, ESCORT)
        if not self._is_moving or self._legs_to != self._site:
            self._legs, self._legs_to = _waypoint_legs(mcv, self._site, world, ctx), self._site
        if not self._is_moving:
            log_expansion(world, MCV_MOVING, self._site, self._field, reason)
        self._is_moving, self._is_waiting = True, False
        cell = self._next_cell(mcv)
        clearing = self._clear_site(world, ctx)
        hold = self._hold_point(cell, world, ctx)
        cleared = {unit_id for intent in clearing for unit_id in intent.order["units"]}
        # An escort unit that stands idle where the MCV goes is there already: it is not told again.
        going = [
            unit.id
            for unit in escort
            if hold is not None
            and unit.id not in cleared
            and not (unit.idle and cells.is_within((unit.x, unit.y), hold, _AT_SITE_CELLS))
        ]
        guard = [Intent(Priority.MOVE, {"cmd": "attack_move", "units": going, "to": list(hold)})] if going else []
        return [*guard, *clearing, *self._go_and_deploy(mcv, world, cell)]

    def _note_approach(self, mcv: Unit) -> None:
        """Keep the MCV's cell as the side it comes from while it stands outside the ground its escort keeps clear
        of."""
        cell = (mcv.x, mcv.y)
        if self._site is not None and not _holds(clear_area(self._site), cell):
            self._approach = cell

    def _hold_point(self, cell: Cell, world: World, ctx: MatchContext) -> Cell | None:
        """Where the escort attack-moves while the MCV goes to `cell`: that cell while it lies outside the ground kept
        clear (`clear_area`), else the hold point by the site (`escort_hold`) from the side the MCV comes from."""
        if not _holds(clear_area(self._site), cell):
            return cell
        return escort_hold(self._site, self._approach_cell(world), world, ctx.terrain)

    def _clear_site(self, world: World, ctx: MatchContext) -> list[Intent]:
        """A move for each escort unit that stands on the ground kept clear (`clear_area`) to the free cell just outside
        it nearest the unit, on the side the MCV comes from when one is free there; one unit to a cell. None without a
        site or an escort."""
        escort = [world.combat_units[unit_id] for unit_id in ctx.groups.members(ESCORT)]
        if self._site is None or not escort:
            return []
        area = clear_area(self._site)
        inside = [unit for unit in escort if _holds(area, (unit.x, unit.y))]
        if not inside:
            return []
        ground, approach, moves = _Ground(world, ctx.terrain, with_units=True), self._approach_cell(world), []
        for unit in inside:
            free = _free_cells_around(area, approach, ground)
            out = min(free, key=lambda free_cell: cells.distance(free_cell, (unit.x, unit.y)), default=None)
            if out is not None:
                ground.take(out)
                moves.append(Intent(Priority.MOVE, {"cmd": "move", "units": [unit.id], "to": list(out)}))
        return moves

    def _approach_cell(self, world: World) -> Cell:
        """The cell the MCV comes from: its last cell outside the ground kept clear, else the main yard's middle, else
        the site itself (when every side is as good)."""
        return self._approach or main_yard(world) or self._site

    def _next_cell(self, mcv: Unit) -> Cell:
        """Where the MCV on its way goes now: the next cell of its way through the graph of waypoints, on to the one
        after once within `waypoints.REACHED_CELLS` of it; its site once none is left."""
        if self._legs and cells.is_within((mcv.x, mcv.y), self._legs[0], waypoints.REACHED_CELLS):
            self._legs.pop(0)
        return self._legs[0] if self._legs else self._site

    def _go_and_deploy(self, mcv: Unit, world: World, toward: Cell | None = None) -> list[Intent]:
        """The MCV sent to its site, by way of `toward` when that is given, or deployed once it is there; deployed where
        it stands without a site. A deploy sent while one is out is a resend, counted with the cell it is sent from."""
        if self._site is None:
            self._site = (mcv.x, mcv.y)
        if cells.is_within((mcv.x, mcv.y), self._site, ARRIVED_CELLS):
            self._resends = 0 if self._deploy_sent is None else self._resends + 1
            self._deploy_sent, self._deploy_cell = world.tick, (mcv.x, mcv.y)
            return [Intent(Priority.PLACEMENT, {"cmd": "deploy", "units": [mcv.id]})]
        return [Intent(Priority.MOVE, {"cmd": "move", "units": [mcv.id], "to": list(toward or self._site)})]

    def _wait(self, mcv: Unit, reason: str, elapsed: int, world: World, ctx: MatchContext) -> list[Intent]:
        """The MCV kept at home, its escort back with main; once it has waited long enough, on to its site when it
        waited out its escort on a safe route (`_goes_after_wait`), else at home for good."""
        if not self._is_waiting:
            log_expansion(world, MCV_WAITING, self._site, self._field, reason)
        self._is_waiting = True
        self._waited += elapsed
        self._release_escort(ctx)
        was_moving, self._is_moving = self._is_moving, False
        if self._waited >= ctx.tuning.expansion.mcv_wait_ticks:
            if self._goes_after_wait(mcv, reason, world, ctx):
                return self._go(mcv, world, ctx, _REASON_ESCORT_WAIT_TIMEOUT)
            self._go_home(world, ctx, _REASON_WAITED)
            return self._go_and_deploy(mcv, world)
        home = home_site(world, ctx)
        if not was_moving or home is None:
            return []
        return [Intent(Priority.MOVE, {"cmd": "move", "units": [mcv.id], "to": list(home)})]

    def _deploying(self, mcv: Unit, world: World, ctx: MatchContext) -> list[Intent]:
        """After a deploy went out: a refused one (`_refusal`), written as a `deploy_refused` event, tried again
        farther from the ore (`_next_site`), or at home; one without a verdict for a while sent again. The next place
        by the field is checked for an enemy army (`site_army`) as its deploy would go out: with one there, nothing is
        sent, and the hold takes the MCV from the next cycle."""
        refusal = self._refusal(mcv, world, ctx)
        if refusal is not None:
            log_expansion(world, DEPLOY_REFUSED, (mcv.x, mcv.y), self._field, refusal)
            self._deploy_sent = None
            self._refused += 1
            is_ignored = refusal == _REASON_IGNORED
            self._ignored += is_ignored
            self._next_site(world, ctx, _IGNORED_STEP_CELLS if is_ignored else 1)
            if self.travelling() and site_army(self._site, world, ctx):
                return []
            return self._go_and_deploy(mcv, world)
        if world.tick - self._deploy_sent >= _DEPLOY_RESEND_TICKS:
            return self._go_and_deploy(mcv, world)
        return []

    def _refusal(self, mcv: Unit, world: World, ctx: MatchContext) -> str | None:
        """Why the deploy out counts as refused: the game refused it (`rejected`), or it was left without effect
        (`ignored`): `_DEPLOY_RESEND_TICKS` after a resend the MCV still stands idle on the cell it was sent from, or
        after the most resends whatever it does. None while it may still unfold."""
        if _is_refused(ctx, mcv.id, self._deploy_sent):
            return _REASON_REJECTED
        is_late = world.tick - self._deploy_sent >= _DEPLOY_RESEND_TICKS
        is_unmoved = mcv.idle and (mcv.x, mcv.y) == self._deploy_cell
        is_ignored = self._resends >= _MOST_RESENDS or (self._resends >= _RESENDS_BEFORE_IGNORED and is_unmoved)
        return _REASON_IGNORED if is_late and is_ignored else None

    def _next_site(self, world: World, ctx: MatchContext, farther: int) -> None:
        """Where the MCV tries next after a refused deploy: `farther` cells farther from the ore while retries are
        left, then at home, and at home the next place for a yard (around the cell a further MCV deploys by). Deploys
        left without effect send it home after `IGNORED_DEPLOYS_HOME` of them; so does, with the reason `own_side`, a
        next place off the base's own side while the site rules apply (`_has_site_rules`)."""
        if self._is_home:
            self._site = home_site(world, ctx, skip=self._refused, anchor=self._home_anchor) or self._site
            return
        if self._refused > DEPLOY_RETRIES or self._ignored >= IGNORED_DEPLOYS_HOME:
            self._go_home(world, ctx, _REASON_NO_ROOM)
            return
        ore = self._field.nearest_cell(self._site)
        farther_site = cells.toward(ore, self._site, cells.distance(ore, self._site) + farther)
        if _has_site_rules(ctx) and not _is_own_side(farther_site, world, ctx):
            self._go_home(world, ctx, _REASON_OWN_SIDE)
            return
        self._site = farther_site

    def _go_home(self, world: World, ctx: MatchContext, reason: str) -> None:
        """Deploy at home from now on, by no field; a further MCV never deploys at home (`_fall_back`), and with
        `second_mcv_waits` on the second one left with no safe site on its way waits by home for one first
        (`_wait_for_safe_site`)."""
        self._field = None
        self._release_escort(ctx)
        if self._is_further:
            self._fall_back(world, ctx, reason)
            return
        if reason == _REASON_HOME_FALLBACK and ctx.tuning.expansion.second_mcv_waits:
            self._site, self._looked_at, self._waits_since = None, world.tick, world.tick
            return
        self._is_home, self._home_reason = True, reason
        self._site = home_site(world, ctx) or self._site

    def _fall_back(self, world: World, ctx: MatchContext, reason: str) -> None:
        """Where a further MCV goes when the second one would deploy at home: with no safe site left on its way
        (`home_fallback`), it waits at home for a site again; once it has waited too long or been refused too often, it
        deploys by no field, at the free place for a yard nearest the second yard when a safe route reaches that place
        from the MCV, else at the free place nearest the MCV (`_fallback_anchor`), or where it stands."""
        if reason == _REASON_HOME_FALLBACK:
            self._site, self._looked_at = None, world.tick
            return
        mcv = world.units[self._mcv]
        self._is_home, self._home_reason = True, reason
        self._home_anchor = _fallback_anchor(mcv, world, ctx)
        self._site = home_site(world, ctx, anchor=self._home_anchor) or (mcv.x, mcv.y)

    @staticmethod
    def _release_escort(ctx: MatchContext) -> None:
        """Send the escort's units back to main."""
        for unit_id in ctx.groups.roster(ESCORT):
            ctx.groups.assign(unit_id, MAIN)


def deploy_site(world: World, ctx: MatchContext, start: Cell | None = None) -> tuple[Cell | None, OrePatch | None]:
    """Where the MCV deploys, and the field it deploys by: by the creep target, else by the nearest field of the lane
    no refinery serves; at home, with no field, when neither has a free place for a yard. With `mcv_safe_site` on, the
    place `safe_site` gives from `start`, as with `mcv_repeat` on. See the module's note."""
    if ctx.tuning.expansion.mcv_safe_site or is_far_fields(ctx):
        choice = safe_site(world, ctx, start)
        return choice.site, choice.field
    field = _site_field(world, ctx)
    site = None if field is None else _place_by(field, world, ctx)
    if site is None:
        return home_site(world, ctx), None
    return site, field


class SiteChoice(NamedTuple):
    """Where the MCV deploys with `mcv_safe_site` on, the field it deploys by and how far that field's center lies from
    the main yard's middle (from the nearest own yard's for a further MCV of `mcv_repeat`); no field and no distance at
    home."""

    site: Cell | None
    field: OrePatch | None
    distance: float | None


# No site at all: a further MCV's, while none is found.
_NO_SITE = SiteChoice(None, None, None)


def safe_site(
    world: World, ctx: MatchContext, start: Cell | None = None, *, skip: frozenset[OrePatch] = frozenset()
) -> SiteChoice:
    """With `mcv_safe_site` on, the place for a yard by the nearest field the MCV may deploy by (`_site_fields`, nearest
    the main yard first) whose place is open (`_is_open_site`: clear of the enemy's start and buildings, on the base's
    own side) and is reached from `start` (the main yard without it) by a safe route (`safe_route`); `home_site`
    without one. With `mcv_new_area` on, the fields the site lasts by, then those of a new area, are tried before the
    rest (`_site_groups`). With `mcv_repeat` on, the place `far_site` gives from `start`, else `home_site`. The fields
    to `skip` are passed over."""
    main = main_yard(world)
    if main is None:
        return SiteChoice(home_site(world, ctx), None, None)
    if is_far_fields(ctx):
        choice = far_site(world, ctx, start or main, skip=skip)
    else:
        fields = [field for field in _site_fields(main, world, ctx) if field not in skip]
        choice = _first_safe_by_area(fields, [main], start or main, world, ctx)
    return choice or SiteChoice(home_site(world, ctx), None, None)


def repeat_site(
    world: World, ctx: MatchContext, start: Cell, *, skip: frozenset[OrePatch] = frozenset()
) -> SiteChoice | None:
    """With `mcv_repeat` on, where a further MCV deploys: the place `far_site` gives from `start`, the fields to `skip`
    passed over; None without one."""
    return far_site(world, ctx, start, skip=skip)


def far_site(
    world: World,
    ctx: MatchContext,
    start: Cell,
    *,
    own_side_rule: bool = True,
    skip: frozenset[OrePatch] = frozenset(),
) -> SiteChoice | None:
    """With `mcv_repeat` on, where an MCV deploys: the place for a yard by the field that qualifies (`_far_fields`)
    whose place the shortest safe route reaches from `start` (`_route_length`), with the distance of the field's center
    from its nearest own yard's middle. The crawl target's field qualifies as any other does; the fields to `skip` do
    not. None without such a field. With `own_side_rule` off, a place off the base's own side is not passed over. See
    the module's note."""
    middles = _yard_middles(world) or ([] if world.base_center is None else [world.base_center])
    fields = [field for field in _far_fields(middles, world, ctx) if field not in skip]
    return _nearest_by_route(fields, middles, start, McvDangers(world, ctx), world, ctx, own_side_rule)


def _far_fields(middles: list[Cell], world: World, ctx: MatchContext) -> list[OrePatch]:
    """The fields an MCV may deploy by with `mcv_repeat` on, nearest their nearest own yard first: not taken
    (`_taken`), no claim field and no own yard by them (`_is_by_a_yard`), lasting (`_lasts`), their center within
    `mcv_far_site_max_cells` of an own yard's middle, more than `creep_enemy_clear_cells` from the enemy's start and no
    remembered enemy defense within `creep_defense_clear_cells`."""
    taken, reach = _taken(world, ctx), ctx.tuning.expansion.mcv_far_site_max_cells
    clear_cells = ctx.tuning.placement.creep_defense_clear_cells
    refineries = [*_refinery_middles(world), *ctx.asked_refinery_cells]
    yard_boxes = [
        placement.standing_box(building) for building in world.buildings.values() if building.type == CONSTRUCTION_YARD
    ]
    fields = [
        field
        for field in ctx.terrain.ore_patches
        if field not in taken
        and field not in ctx.claim_fields
        and _nearest_distance(field.center, middles) <= reach
        and _lasts(field, refineries, ctx)
        and not _is_by_a_yard(field, yard_boxes, ctx)
        and _is_enemy_clear(field.center, world, ctx)
        and not placement.is_defended(field, world, clear_cells)
    ]
    return sorted(fields, key=lambda field: _nearest_distance(field.center, middles))


def _has_its_refineries(field: OrePatch, world: World, ctx: MatchContext) -> bool:
    """Whether a new yard's field has the refineries its claim asks for: with `refinery_unserved_only` on, one counted
    against it for one refinery a field (`refinery_fields.counts`); else `max_refineries_per_field` serving it or
    asked against it."""
    tuning, asked = ctx.tuning.placement, ctx.asked_refinery_fields
    if refinery_fields.is_on(ctx):
        return refinery_fields.counts(world, ctx, asked)[field] > 0
    load = placement.refineries_serving(world, ctx.terrain)[field] + asked.count(field)
    return load >= tuning.max_refineries_per_field


def _is_by_a_yard(field: OrePatch, yard_boxes: list[placement.Box], ctx: MatchContext) -> bool:
    """Whether an own construction yard stands by a field already, its box within `mcv_deploy_cells` of the field's ore
    by walking as an MCV's site by it would be: that yard serves the field, before any refinery of its stands."""
    reach = ctx.tuning.expansion.mcv_deploy_cells
    return any(ctx.terrain.walked_distance(box.cells(), field, reach) is not None for box in yard_boxes)


def _lasts(field: OrePatch, refineries: list[Cell], ctx: MatchContext) -> bool:
    """Whether a yard by a field lasts: the field has at least `mcv_site_min_ore_cells` ore cells (`ore_cells`), no own
    harvester's last cell on ore lies in it, and its center lies `mcv_refinery_spacing_cells` or more from every own
    refinery's middle given, standing or asked."""
    expansion = ctx.tuning.expansion
    is_spaced = all(cells.distance(field.center, cell) >= expansion.mcv_refinery_spacing_cells for cell in refineries)
    is_unharvested = field not in ctx.harvested_fields
    return ore_cells(field, ctx) >= expansion.mcv_site_min_ore_cells and is_unharvested and is_spaced


def _nearest_by_route(
    fields: list[OrePatch],
    middles: list[Cell],
    start: Cell,
    dangers: McvDangers,
    world: World,
    ctx: MatchContext,
    own_side_rule: bool = True,
) -> SiteChoice | None:
    """The place for a yard by the field of those given whose place, open (`_is_open_site`, the own-side rule waived
    with `own_side_rule` off), the shortest safe route reaches from `start` (`_route_length`), the first of several as
    short, with the distance of the field's center from the nearest of the `middles`; None when no route reaches any.

    Finding a field's place is the costly part, and upkeep looks for the site in every cycle while an MCV is wanted;
    so the fields are tried nearest `start` first, and a field whose place cannot be nearer than the best route found
    (`_site_slack` short of its ore cell nearest `start`) is not tried at all.
    """
    slack = _site_slack(ctx)
    tried = sorted(
        (cells.distance(start, field.nearest_cell(start)) - slack, index, field) for index, field in enumerate(fields)
    )
    best: tuple[float, int, SiteChoice] | None = None
    for least, index, field in tried:
        if best is not None and least > best[0]:
            break
        site = _place_by(field, world, ctx)
        is_open = _is_open_site(site, world, ctx, own_side_rule)
        length = _route_length(start, site, dangers, world, ctx) if is_open else None
        if length is not None and (best is None or (length, index) < best[:2]):
            best = length, index, SiteChoice(site, field, _nearest_distance(field.center, middles))
    return None if best is None else best[2]


def _site_slack(ctx: MatchContext) -> float:
    """How far a place for a yard by a field may lie from the field's ore, in cells: its yard box lies within
    `mcv_deploy_cells` of the ore as a king moves, the place within the box's extent of every cell of it, and a king's
    move spans at most the square root of 2 cells."""
    width, height = placement.footprint(CONSTRUCTION_YARD)
    return math.sqrt(2) * (ctx.tuning.expansion.mcv_deploy_cells + max(width, height))


class _FarSites:
    """The site `far_site` gave from a start, kept while what it is worked out from stays the same.

    Upkeep looks for the next MCV's site in every cycle while one is wanted, and the place by each field costs a few
    milliseconds in a large base; so it is worked out again only when the start, the own buildings, the enemy's start,
    its remembered defenses, the enemy units the MCV keeps clear of, the taken, claimed, harvested and dry fields, the
    routes walked, the map, the terrain or the tuning differ from the last look's.
    """

    def __init__(self) -> None:
        self._inputs: tuple[object, ...] | None = None
        self._choice: SiteChoice | None = None

    def site(self, world: World, ctx: MatchContext, start: Cell) -> SiteChoice | None:
        """The site `far_site` gives from `start` now."""
        inputs = _far_site_inputs(world, ctx, start)
        if inputs != self._inputs:
            self._inputs, self._choice = inputs, far_site(world, ctx, start)
        return self._choice


def _far_site_inputs(world: World, ctx: MatchContext, start: Cell) -> tuple[object, ...]:
    """What `far_site` works its site out from, as one value that tells two looks apart when it differs."""
    seen_ticks = ctx.tuning.expansion.mcv_enemy_seen_ticks
    seen = (
        (record.type, record.x, record.y)
        for record in world.enemy_memory.values()
        if is_combat_unit(record) and world.tick - record.last_seen_tick <= seen_ticks
    )
    return (
        start,
        ctx.terrain,
        ctx.tuning,
        world.map_bounds,
        world.base_center,
        world.enemy_base,
        tuple(sorted((building.type, building.x, building.y) for building in world.buildings.values())),
        tuple(sorted((record.type, record.x, record.y) for record in world.known_enemy_defenses)),
        tuple(sorted((record.type, record.x, record.y) for record in _enemy_site_buildings(world))),
        tuple(sorted(seen)),
        tuple(ctx.claim_fields),
        tuple(ctx.asked_refinery_fields),
        tuple(ctx.asked_refinery_cells),
        ctx.harvested_fields,
        ctx.dry_fields,
        ctx.walked_routes,
    )


def _route_length(start: Cell, end: Cell, dangers: McvDangers, world: World, ctx: MatchContext) -> float | None:
    """How long the safe route from `start` to `end` is, in cells: the straight line when no cell of it is unsafe; else
    with `waypoints` of [routing] on the way through the graph of waypoints (`_safe_waypoint_route`), else the way by a
    side waypoint of `safe_route`; None when no such route is safe."""
    if ctx.tuning.routing.waypoints:
        if not any(map(dangers.is_unsafe, cells.line(start, end))):
            return cells.distance(start, end)
        found = _safe_waypoint_route(start, end, dangers, ctx)
        return None if found is None else found.length
    way = safe_route(start, end, dangers, world, ctx)
    return None if way is None else sum(cells.distance(first, second) for first, second in zip(way, way[1:]))


def _first_safe_by_area(
    fields: list[OrePatch], origins: list[Cell], start: Cell, world: World, ctx: MatchContext
) -> SiteChoice | None:
    """`_first_safe` of the fields given, in the groups `_site_groups` gives them in."""
    dangers, tried = McvDangers(world, ctx), set()
    for group in _site_groups(fields, world, ctx):
        choice = _first_safe([field for field in group if field not in tried], origins, start, dangers, world, ctx)
        if choice is not None:
            return choice
        tried.update(group)
    return None


def _first_safe(
    fields: list[OrePatch], origins: list[Cell], start: Cell, dangers: McvDangers, world: World, ctx: MatchContext
) -> SiteChoice | None:
    """The place for a yard by the first of the fields whose place a safe route reaches from `start` (`_is_safe_site`),
    with the field and its center's distance from the nearest of the `origins`; None without one."""
    for field in fields:
        site = _place_by(field, world, ctx)
        if site is not None and _is_safe_site(start, site, dangers, world, ctx):
            return SiteChoice(site, field, _nearest_distance(field.center, origins))
    return None


def _nearest_distance(cell: Cell, others: list[Cell]) -> float:
    """How far a cell lies from the nearest of the others; endlessly far from none."""
    return min((cells.distance(cell, other) for other in others), default=math.inf)


def _may_repeat(world: World, ctx: MatchContext) -> bool:
    """Whether an MCV may be sited with `mcv_repeat` on: `second_yard` and `mcv_repeat` on, an own yard standing,
    `second_yard_from_seconds` of the match passed or the second MCV due by its time (`is_second_mcv_due`), and more
    than `repeat_mcv_cutoff_seconds` of it left (or the time left not known)."""
    expansion, remaining = ctx.tuning.expansion, world.remaining_seconds
    is_left = remaining is None or remaining > expansion.repeat_mcv_cutoff_seconds
    is_late_enough = world.game_seconds >= expansion.second_yard_from_seconds or is_second_mcv_due(world, ctx)
    is_in_time = is_late_enough and is_left
    return is_far_fields(ctx) and bool(yards(world)) and is_in_time


def _new_area_groups(fields: list[OrePatch], world: World, ctx: MatchContext) -> list[list[OrePatch]]:
    """With `mcv_new_area` on, the fields given that open a new area, in their order, in two groups: those whose
    center lies `mcv_refinery_spacing_cells` or more from every own refinery, standing or asked, then the rest. A field
    that is taken (`placement.taken_fields`) or a claim field opens none. No group with the switch off."""
    expansion = ctx.tuning.expansion
    if not expansion.mcv_new_area:
        return []
    old = _old_area(world, ctx)
    new = [field for field in fields if field not in old]
    refineries = [*_refinery_middles(world), *ctx.asked_refinery_cells]
    spacing = expansion.mcv_refinery_spacing_cells
    spaced = [field for field in new if all(cells.distance(field.center, cell) >= spacing for cell in refineries)]
    return [spaced, [field for field in new if field not in spaced]]


def _site_groups(fields: list[OrePatch], world: World, ctx: MatchContext) -> list[list[OrePatch]]:
    """The fields given in the groups a site is looked for in, each in their order: with `mcv_new_area` on, those the
    site lasts by (`_lasting`), then those of a new area (`_new_area_groups`), then all of them; all alone with it off.
    """
    groups = _new_area_groups(fields, world, ctx)
    if not groups:
        return [fields]
    return [_lasting(groups[0], ctx), *groups, fields]


def _lasting(spaced: list[OrePatch], ctx: MatchContext) -> list[OrePatch]:
    """Of the fields of a new area spaced from every own refinery, those a site lasts by, in their order: with at least
    `mcv_site_min_ore_cells` ore cells (`ore_cells`) and no own harvester's last cell on ore in them."""
    least = ctx.tuning.expansion.mcv_site_min_ore_cells
    return [field for field in spaced if ore_cells(field, ctx) >= least and field not in ctx.harvested_fields]


def ore_cells(field: OrePatch, ctx: MatchContext) -> int:
    """How many ore cells a field has as far as the agent knows: its cells in the map's picture, none once it is marked
    dry."""
    return 0 if field in ctx.dry_fields else field.size


def _is_fallback(field: OrePatch | None, world: World, ctx: MatchContext) -> bool:
    """Whether a site's field was chosen for want of one it lasts by: with `mcv_new_area` on, a field that is not."""
    if field is None:
        return False
    groups = _new_area_groups([field], world, ctx)
    return bool(groups) and not _lasting(groups[0], ctx)


def _old_area(world: World, ctx: MatchContext) -> set[OrePatch]:
    """The fields the MCV opens no new area by: those taken (`_taken`) and the claim fields. The creep target's field
    is not one of them: the MCV does not keep to the crawl."""
    return {*_taken(world, ctx), *ctx.claim_fields}


def _taken(world: World, ctx: MatchContext) -> frozenset[OrePatch]:
    """The fields that are taken: a refinery stands, was asked or is on order against them (`placement.taken_fields`)."""
    return placement.taken_fields(world, ctx.terrain, ctx.tuning.placement, ctx.asked_refinery_fields)


def _refinery_middles(world: World) -> list[Cell]:
    """The middle cells of the own refineries that stand."""
    return [
        cells.nearest_cell(*placement.standing_box(building).center)
        for building in world.buildings.values()
        if building.type == REFINERY
    ]


def _is_safe_site(start: Cell, site: Cell, dangers: McvDangers, world: World, ctx: MatchContext) -> bool:
    """Whether a place for a yard is open (`_is_open_site`) and a safe route reaches it from `start`."""
    return _is_open_site(site, world, ctx) and safe_route(start, site, dangers, world, ctx) is not None


def _is_open_site(site: Cell | None, world: World, ctx: MatchContext, own_side_rule: bool = True) -> bool:
    """Whether there is a place for a yard, more than `creep_enemy_clear_cells` from the enemy's start, with no known
    enemy yard, refinery or ground defense within `mcv_enemy_building_cells` of it, and on the base's own side
    (`_is_own_side`) unless `own_side_rule` is off."""
    if site is None or not _is_enemy_clear(site, world, ctx):
        return False
    return not _is_enemy_building_near(site, world, ctx) and (not own_side_rule or _is_own_side(site, world, ctx))


def _is_own_side(site: Cell, world: World, ctx: MatchContext) -> bool:
    """Whether a site lies on the base's own side: its distance from the base's center is at most `mcv_own_side_share`
    of its distance from the enemy's start. Every site does while either is unknown."""
    center, enemy = world.base_center, world.enemy_base
    if center is None or enemy is None:
        return True
    return cells.distance(site, center) <= ctx.tuning.expansion.mcv_own_side_share * cells.distance(site, enemy)


def _unfit_site_reason(site: Cell, world: World, ctx: MatchContext) -> str | None:
    """Why an MCV's site by a field no longer qualifies: a known enemy yard, refinery or ground defense stands within
    `mcv_enemy_building_cells` of it (`enemy_building_near`), or it lies off the base's own side (`own_side`); None
    while it qualifies."""
    if _is_enemy_building_near(site, world, ctx):
        return _REASON_ENEMY_BUILDING_NEAR
    return None if _is_own_side(site, world, ctx) else _REASON_OWN_SIDE


def _has_site_rules(ctx: MatchContext) -> bool:
    """Whether the rules every site of an MCV must meet apply: `mcv_safe_site` or `mcv_repeat` is on."""
    expansion = ctx.tuning.expansion
    return expansion.mcv_safe_site or expansion.mcv_repeat


def _is_enemy_building_near(site: Cell, world: World, ctx: MatchContext) -> bool:
    """Whether a known enemy construction yard, refinery or ground defense stands within `mcv_enemy_building_cells`
    of a site (`_enemy_site_buildings`)."""
    reach = ctx.tuning.expansion.mcv_enemy_building_cells
    return any(cells.is_within((record.x, record.y), site, reach) for record in _enemy_site_buildings(world))


def _enemy_site_buildings(world: World) -> list[EnemyRecord]:
    """The known enemy buildings no MCV deploys near: construction yards, refineries, and the defenses that do not
    shoot at aircraft alone."""
    yards_and_refineries = [
        record
        for record in world.enemy_memory.values()
        if record.is_building and record.type in (CONSTRUCTION_YARD, REFINERY)
    ]
    ground = [record for record in world.known_enemy_defenses if not is_air_only(record.type, world.items)]
    return [*yards_and_refineries, *ground]


def main_yard(world: World) -> Cell | None:
    """The middle cell of the main yard, the own construction yard nearest the base's center; the base's center
    without a yard."""
    center = world.base_center
    standing = [building for building in world.buildings.values() if building.type == CONSTRUCTION_YARD]
    if not standing or center is None:
        return center
    yard = min(standing, key=lambda building: cells.distance((building.x, building.y), center))
    return _yard_middle(yard)


def _yard_ids(world: World) -> set[int]:
    """The ids of the own construction yards that stand."""
    return {building.id for building in world.buildings.values() if building.type == CONSTRUCTION_YARD}


def _yard_middles(world: World) -> list[Cell]:
    """The middle cells of the own construction yards that stand."""
    return [_yard_middle(building) for building in world.buildings.values() if building.type == CONSTRUCTION_YARD]


def _yard_middle(yard: Building) -> Cell:
    """The middle cell of a construction yard."""
    return cells.nearest_cell(*placement.standing_box(yard).center)


def _site_fields(main: Cell, world: World, ctx: MatchContext) -> list[OrePatch]:
    """The fields the MCV may deploy by, nearest the main yard first: their center within `mcv_site_max_cells` of it,
    no own refinery within `refinery_ore_cells` of their ore, more than `creep_enemy_clear_cells` from the enemy's start
    and no remembered enemy defense within `creep_defense_clear_cells`. A field placement skipped is one of them like
    any other."""
    tuning = ctx.tuning.placement
    reach = ctx.tuning.expansion.mcv_site_max_cells
    refineries = [
        placement.standing_box(building) for building in world.buildings.values() if building.type == REFINERY
    ]
    fields = [
        field
        for field in ctx.terrain.ore_patches
        if cells.is_within(field.center, main, reach)
        and not any(placement.reaches(box, field, tuning.refinery_ore_cells, ctx.terrain) for box in refineries)
        and _is_enemy_clear(field.center, world, ctx)
        and not placement.is_defended(field, world, tuning.creep_defense_clear_cells)
    ]
    return sorted(fields, key=lambda field: cells.distance(field.center, main))


def _is_enemy_clear(cell: Cell, world: World, ctx: MatchContext) -> bool:
    """Whether a cell lies more than `creep_enemy_clear_cells` from the enemy's start, or that start is not known."""
    enemy = world.enemy_base
    return enemy is None or not cells.is_within(cell, enemy, ctx.tuning.placement.creep_enemy_clear_cells)


class McvDangers:
    """The ground the MCV keeps off with `mcv_safe_site` on: every cell a remembered enemy defense covers (see
    `coverage`), and every cell within an enemy unit's weapon range and `mcv_danger_margin_cells` of where that unit
    was seen in the last `mcv_enemy_seen_ticks`. A unit seen lately may stand there still, or come back."""

    def __init__(self, world: World, ctx: MatchContext) -> None:
        tuning, expansion = ctx.tuning, ctx.tuning.expansion
        # (centre, reach) of every danger.
        self._zones: list[tuple[Cell, float]] = [
            ((defense.x, defense.y), coverage.reach(defense.type, world.items, tuning.coverage))
            for defense in world.known_enemy_defenses
        ]
        self._zones += [
            ((record.x, record.y), _weapon_range(record.type, world, ctx) + expansion.mcv_danger_margin_cells)
            for record in world.enemy_memory.values()
            if is_combat_unit(record) and world.tick - record.last_seen_tick <= expansion.mcv_enemy_seen_ticks
        ]

    def is_unsafe(self, cell: Cell) -> bool:
        """Whether a cell lies within a danger's reach."""
        return any(cells.is_within(cell, centre, reach) for centre, reach in self._zones)

    def is_near(self, cell: Cell, within: float) -> bool:
        """Whether an unsafe cell lies within `within` cells of a cell."""
        return any(cells.is_within(cell, centre, reach + within) for centre, reach in self._zones)


def _weapon_range(unit_type: str, world: World, ctx: MatchContext) -> float:
    """How far an enemy unit of a type shoots, in cells; as far as an unknown defense when the item table does not
    say."""
    facts = world.items.get(unit_type)
    return ctx.tuning.coverage.unknown_range_cells if facts is None or facts.range is None else facts.range


def safe_route(start: Cell, end: Cell, dangers: McvDangers, world: World, ctx: MatchContext) -> list[Cell] | None:
    """The cells of the shortest way from `start` to `end`, both included, none of them unsafe: the straight line, else
    the way by one of the two side waypoints `side_offset_cells` of [raids] to either side of it; None when neither is
    safe."""
    straight = cells.line(start, end)
    if not any(map(dangers.is_unsafe, straight)):
        return straight
    if world.map_bounds is None:
        return None
    sides = raid_routes.side_waypoints(start, end, world.map_bounds, ctx.tuning.raids.side_offset_cells)
    ways = [[*cells.line(start, side), *cells.line(side, end)[1:]] for side in sides]
    return min((way for way in ways if not any(map(dangers.is_unsafe, way))), key=len, default=None)


def _waypoint_legs(mcv: Unit, site: Cell, world: World, ctx: MatchContext) -> list[Cell]:
    """With `waypoints` of [routing] and `mcv_safe_site` on, the cells short of the site of the way through the graph
    of waypoints from the MCV to its site, written as a `route` event, when the straight line there crosses unsafe
    ground (`McvDangers`) and no cell on that way does; none otherwise."""
    if not (ctx.tuning.routing.waypoints and ctx.tuning.expansion.mcv_safe_site):
        return []
    start, dangers = (mcv.x, mcv.y), McvDangers(world, ctx)
    if not any(map(dangers.is_unsafe, cells.line(start, site))):
        return []
    found = _safe_waypoint_route(start, site, dangers, ctx)
    if found is None:
        return []
    waypoints.log_route(waypoints.MCV, found, world.tick)
    return list(found.legs[:-1])


def _safe_waypoint_route(
    start: Cell, end: Cell, dangers: McvDangers, ctx: MatchContext
) -> waypoints.WaypointRoute | None:
    """The way through the graph of waypoints from `start` to `end` when no cell on it is unsafe; None else."""
    found = ctx.waypoints.route(start, end)
    if found is None:
        return None
    stops = (start, *found.legs)
    if any(dangers.is_unsafe(cell) for first, second in zip(stops, stops[1:]) for cell in cells.line(first, second)):
        return None
    return found


def _first_site(mcv: Unit, world: World, ctx: MatchContext) -> tuple[Cell | None, OrePatch | None, str]:
    """The site a new MCV is given, the field it deploys by, and why it deploys at home when there is none. With
    `mcv_safe_site` on, the choice from the MCV's cell is written as a `site` event."""
    if not (ctx.tuning.expansion.mcv_safe_site or is_far_fields(ctx)):
        return *deploy_site(world, ctx), _REASON_NO_SITE
    choice = safe_site(world, ctx, (mcv.x, mcv.y))
    _log_site(choice, _REASON_NEAREST_SAFE if choice.field is not None else _REASON_HOME_FALLBACK, world, ctx)
    return choice.site, choice.field, _REASON_HOME_FALLBACK


def _ordered_site(world: World, ctx: MatchContext) -> SiteChoice:
    """The site chosen for an MCV as it is ordered, from the main yard: for a further MCV, while two yards or more
    stand, the one published for it (`_further_site`); else `deploy_site`'s, which with `mcv_safe_site` or `mcv_repeat`
    on is written as a `site` event."""
    if ctx.repeat_mcv_field is not None and len(yards(world)) >= SECOND_YARD_COUNT:
        return SiteChoice(*_further_site(world, ctx), None)
    if not (ctx.tuning.expansion.mcv_safe_site or is_far_fields(ctx)):
        return SiteChoice(*deploy_site(world, ctx), None)
    choice = safe_site(world, ctx)
    _log_site(choice, _REASON_NEAREST_SAFE if choice.field is not None else _REASON_HOME_FALLBACK, world, ctx)
    return choice


def _still_holds(ordered: SiteChoice, mcv: Unit, world: World, ctx: MatchContext) -> bool:
    """Whether the site chosen at an MCV's order still holds as it comes out: it is by a field that is not taken
    (`_taken`) and, with `mcv_safe_site` or `mcv_repeat` on, a safe route reaches it from the MCV (`_is_safe_site`)."""
    if ordered.field is None or ordered.field in _taken(world, ctx):
        return False
    expansion = ctx.tuning.expansion
    is_routed = expansion.mcv_safe_site or expansion.mcv_repeat
    return not is_routed or _is_safe_site((mcv.x, mcv.y), ordered.site, McvDangers(world, ctx), world, ctx)


def _further_site(world: World, ctx: MatchContext) -> tuple[Cell | None, OrePatch | None]:
    """The site published for a further MCV (`ctx.repeat_mcv_site`) and its field, written as a `site` event of the
    reason `repeat` with the field's distance from its nearest yard; without one, as a `site` event of the reason
    `repeat_no_site`."""
    field = ctx.repeat_mcv_field
    distance = None if field is None else _nearest_distance(field.center, _yard_middles(world))
    _log_site(
        SiteChoice(ctx.repeat_mcv_site, field, distance),
        _REASON_REPEAT_NO_SITE if field is None else _REASON_REPEAT,
        world,
        ctx,
    )
    return ctx.repeat_mcv_site, field


def _fallback_anchor(mcv: Unit, world: World, ctx: MatchContext) -> Cell:
    """The cell around which a further MCV that found no site deploys: the second yard's, when a safe route reaches the
    free place for a yard nearest it from the MCV; else the MCV's own cell."""
    second = _second_yard(world)
    site = None if second is None else home_site(world, ctx, anchor=second)
    is_reached = site is not None and safe_route((mcv.x, mcv.y), site, McvDangers(world, ctx), world, ctx) is not None
    return second if is_reached else (mcv.x, mcv.y)


def _second_yard(world: World) -> Cell | None:
    """The cell of the second yard: the oldest own construction yard but the main yard; None without one."""
    main = main_yard(world)
    others = [
        building
        for building in world.buildings.values()
        if building.type == CONSTRUCTION_YARD and _yard_middle(building) != main
    ]
    oldest = min(others, key=lambda building: building.id, default=None)
    return None if oldest is None else (oldest.x, oldest.y)


def _log_site(choice: SiteChoice, reason: str, world: World, ctx: MatchContext) -> None:
    """Write the `expansion` event of phase `site`: the place, the field and how far its center lies from the main
    yard, and the reason; before it, with `mcv_new_area` on, a `site_check` event of the reason `site_fallback` with
    the same when the field is not one the site lasts by (`_is_fallback`)."""
    distance = None if choice.distance is None else round(choice.distance, _DISTANCE_DECIMALS)
    if _is_fallback(choice.field, world, ctx):
        log_expansion(world, SITE_CHECK, choice.site, choice.field, _REASON_SITE_FALLBACK, distance_cells=distance)
    log_expansion(world, SITE, choice.site, choice.field, reason, distance_cells=distance)


def home_site(world: World, ctx: MatchContext, skip: int = 0, anchor: Cell | None = None) -> Cell | None:
    """A place for a yard in the base: the `skip`-th candidate of placement, nearest the construction yard first (the
    `anchor` cell when one is given); None when there is none."""
    tuning = ctx.tuning.placement
    candidates = placement.candidate_cells(CONSTRUCTION_YARD, world, ctx.terrain, tuning, anchor=anchor)
    return candidates[skip] if skip < len(candidates) else None


def _site_field(world: World, ctx: MatchContext) -> OrePatch | None:
    """The field the MCV deploys by: with `mcv_to_skipped_fields` on, the skipped field `_skipped_field` gives; else
    the creep target's, else the nearest field of the lane no refinery serves."""
    skipped = _skipped_field(world, ctx)
    if skipped is not None:
        return skipped
    if ctx.creep_target is not None:
        return ctx.creep_target.patch
    served = placement.refineries_serving(world, ctx.terrain)
    fields = creep.lane_fields(world, ctx.terrain, ctx.tuning.placement)
    return next((field for field in fields if not served[field]), None)


def _skipped_field(world: World, ctx: MatchContext) -> OrePatch | None:
    """With `mcv_to_skipped_fields` on, the field placement skipped nearest the base's center that lies more than
    `creep_enemy_clear_cells` from the enemy's start, when that is known, with no remembered enemy defense within
    `creep_defense_clear_cells` of it; None with the switch off, without the base's center and without such a field."""
    tuning, center, enemy = ctx.tuning.placement, world.base_center, world.enemy_base
    if not ctx.tuning.expansion.mcv_to_skipped_fields or center is None:
        return None
    clear = [
        field
        for field in ctx.skipped_fields
        if (enemy is None or not cells.is_within(field.center, enemy, tuning.creep_enemy_clear_cells))
        and not placement.is_defended(field, world, tuning.creep_defense_clear_cells)
    ]
    return min(clear, key=lambda field: cells.distance(field.center, center), default=None)


def _place_by(field: OrePatch, world: World, ctx: MatchContext) -> Cell | None:
    """Where an MCV deploys by a field: on the cell whose yard box (`yard_box`) is the free place for a yard within
    `mcv_deploy_cells` of the field's ore by walking (`Terrain.walked_distance`), the fewest steps first, then the one
    whose top-left corner lies nearest the field's ore cell nearest the base; None when there is none. A place 2 cells
    from the ore across a cliff is as far as the walk round it, and a yard there would serve the field from the wrong
    side of the cliff."""
    reach = ctx.tuning.expansion.mcv_deploy_cells
    xs, ys = [x for x, _ in field.cells], [y for _, y in field.cells]
    width, height = placement.footprint(CONSTRUCTION_YARD)
    # The top-left corners of the yard boxes that may lie within `reach` of the field's ore: no walk is shorter than
    # the king's moves.
    corners = [
        (x, y)
        for y in range(min(ys) - reach - height + 1, max(ys) + reach + 1)
        for x in range(min(xs) - reach - width + 1, max(xs) + reach + 1)
    ]
    open_corners = placement.open_cells(CONSTRUCTION_YARD, corners, world, ctx.terrain, ctx.tuning.placement)
    walked = {
        corner: ctx.terrain.walked_distance(placement.box_at(CONSTRUCTION_YARD, corner).cells(), field, reach)
        for corner in open_corners
    }
    near = [corner for corner, steps in walked.items() if steps is not None]
    if not near or world.base_center is None:
        return None
    ore, center = field.nearest_cell(world.base_center), world.base_center
    return _site_of(min(near, key=lambda cell: (walked[cell], cells.distance(cell, ore), cells.distance(cell, center))))


def yard_box(site: Cell) -> placement.Box:
    """The cells the construction yard covers once an MCV deploys on a cell: its footprint with its top-left corner
    `YARD_OFFSET` from that cell, as the engine unfolds it."""
    return placement.box_at(CONSTRUCTION_YARD, (site[0] + YARD_OFFSET[0], site[1] + YARD_OFFSET[1]))


def _site_of(corner: Cell) -> Cell:
    """The cell an MCV deploys on for its yard's top-left corner to stand on a cell."""
    return corner[0] - YARD_OFFSET[0], corner[1] - YARD_OFFSET[1]


def clear_area(site: Cell) -> placement.Box:
    """The ground no escort order names and no escort unit is left standing on while the MCV has a site: the yard box
    of the site (`yard_box`) grown by `ESCORT_CLEAR_CELLS` on every side."""
    return yard_box(site).grown(ESCORT_CLEAR_CELLS)


def escort_hold(site: Cell, approach: Cell, world: World, terrain: Terrain) -> Cell | None:
    """The escort's hold point by an MCV's site: the free cell just outside the ground kept clear (`clear_area`) nearest
    the cell the MCV comes from, on its side when one is free there; None when no cell around that ground is free. The
    escort's own units do not take a cell from it: they gather there."""
    free = _free_cells_around(clear_area(site), approach, _Ground(world, terrain, with_units=False))
    return min(free, key=lambda cell: cells.distance(cell, approach), default=None)


def _holds(box: placement.Box, cell: Cell) -> bool:
    """Whether a cell lies in a box."""
    return box.left <= cell[0] < box.left + box.width and box.top <= cell[1] < box.top + box.height


class _Ground:
    """Which cells a unit may be sent to stand on: on the map, passable (ore is), under no own building and,
    `with_units`, with no own unit on them; a cell taken for one unit is no more free for another."""

    def __init__(self, world: World, terrain: Terrain, *, with_units: bool) -> None:
        self._bounds, self._terrain = world.map_bounds, terrain
        covered = {cell for building in world.buildings.values() for cell in placement.standing_box(building).cells()}
        standing = {(unit.x, unit.y) for unit in world.units.values()} if with_units else set()
        self._taken = covered | standing

    def is_free(self, cell: Cell) -> bool:
        """Whether a unit may be sent to stand on a cell."""
        is_on_map = self._bounds is None or cells.clamped(cell, self._bounds) == cell
        return is_on_map and cell not in self._taken and self._is_passable(cell)

    def _is_passable(self, cell: Cell) -> bool:
        """Whether units cross a cell: one nothing can be built on is passable only when it holds ore."""
        return cell not in self._terrain.blocked or any(cell in field.cells for field in self._terrain.ore_patches)

    def take(self, cell: Cell) -> None:
        """Keep a cell for one unit."""
        self._taken.add(cell)


def _free_cells_around(area: placement.Box, approach: Cell, ground: _Ground) -> list[Cell]:
    """The free cells of the nearest ring of cells around an area that has any on the side facing `approach`, of
    `_CLEAR_RINGS` rings; failing those, of the nearest ring that has any free cell; none when no ring has one."""
    rings = [_ring(area.grown(margin)) for margin in range(1, _CLEAR_RINGS + 1)]
    free_rings = [[cell for cell in ring if ground.is_free(cell)] for ring in rings]
    center = area.center
    for free in free_rings:
        facing = [cell for cell in free if _faces(cell, center, approach)]
        if facing:
            return facing
    return next((free for free in free_rings if free), [])


def _ring(box: placement.Box) -> list[Cell]:
    """The cells on the edge of a box."""
    right, bottom = box.left + box.width - 1, box.top + box.height - 1
    return [(x, y) for x, y in box.cells() if x in (box.left, right) or y in (box.top, bottom)]


def _faces(cell: Cell, center: tuple[float, float], approach: Cell) -> bool:
    """Whether a cell lies on the side of a center that `approach` lies on."""
    return (cell[0] - center[0]) * (approach[0] - center[0]) + (cell[1] - center[1]) * (approach[1] - center[1]) > 0


def _is_defense_near(site: Cell, world: World) -> bool:
    """Whether a remembered enemy defense stands within `SITE_DEFENSE_CLEAR_CELLS` of the site."""
    return any(
        cells.is_within((record.x, record.y), site, SITE_DEFENSE_CLEAR_CELLS) for record in world.known_enemy_defenses
    )


def _enemy_army_near(site: Cell, world: World) -> float:
    """What the enemy units seen lately within `SITE_ARMY_CLEAR_CELLS` of the site are worth in a fight."""
    return sum(
        worth(record.type, record.hp or _FULL_HEALTH, world)
        for record in world.enemy_memory.values()
        if is_counted(record) and cells.is_within((record.x, record.y), site, SITE_ARMY_CLEAR_CELLS)
    )


def site_army(site: Cell, world: World, ctx: MatchContext) -> list[EnemyRecord]:
    """The enemy army by a site that a travelling MCV holds short of: the enemy combat units seen within
    `SITE_ARMY_SEEN_SECONDS` that stand within `SITE_ARMY_CLEAR_CELLS` of it, when together they are worth
    `mcv_site_army_value` or more in a fight; none otherwise."""
    seen_since = world.tick - SITE_ARMY_SEEN_SECONDS * TICKS_PER_SECOND
    near = [
        record
        for record in world.enemy_memory.values()
        if is_combat_unit(record)
        and record.last_seen_tick >= seen_since
        and cells.is_within((record.x, record.y), site, SITE_ARMY_CLEAR_CELLS)
    ]
    is_army = sum(_worth(record, world) for record in near) >= ctx.tuning.expansion.mcv_site_army_value
    return near if is_army else []


def _worth(record: EnemyRecord, world: World) -> float:
    """What a remembered enemy unit is worth in a fight, at full health when its health is not known."""
    return worth(record.type, record.hp or _FULL_HEALTH, world)


def _hold_short(mcv: Unit, army: list[EnemyRecord], world: World) -> list[Intent]:
    """The order that holds a travelling MCV short of an enemy army: while it stands nearer than
    `SITE_ARMY_HOLD_CELLS` to a unit of the army, a move straight away from the nearest of them to that distance and a
    cell more (the cell is rounded), on the map; else a stop while it still moves, and nothing once it stands."""
    here = (mcv.x, mcv.y)
    nearest = min(((record.x, record.y) for record in army), key=lambda cell: cells.distance(cell, here))
    if cells.distance(nearest, here) >= SITE_ARMY_HOLD_CELLS:
        return [] if mcv.idle else [Intent(Priority.MOVE, {"cmd": "stop", "units": [mcv.id]})]
    away = cells.toward(nearest, here, SITE_ARMY_HOLD_CELLS + ARRIVED_CELLS)
    if world.map_bounds is not None:
        away = cells.clamped(away, world.map_bounds)
    return [Intent(Priority.MOVE, {"cmd": "move", "units": [mcv.id], "to": list(away)})]


def _is_refused(ctx: MatchContext, mcv: int, sent: int) -> bool:
    """Whether the game refused the MCV's deploy sent at a tick."""
    for entry in reversed(ctx.ledger.entries):
        if entry.sent_tick < sent:
            return False
        order = entry.order
        if order.get("cmd") == "deploy" and mcv in order.get("units", ()) and entry.status == REJECTED:
            return True
    return False
