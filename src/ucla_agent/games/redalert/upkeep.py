"""Base upkeep: what keeps a base running, done in every cycle whatever else is going on.

A base needs the same few things looked after from the first minute to the
last, whether the army is winning or the base is under attack. The upkeep
manager is a list of rules, one for each of them, and every rule is asked in
every cycle. No model is consulted and nothing here waits for anything.

A rule looks at the world as it is now and proposes orders. What a rule has
to remember from one cycle to the next (which building it asked a cell for,
and when) it keeps itself; nothing is assumed to have happened because an
order was sent.

Where a rule asks whether there is money for something, money is what the
game can spend: the cash and the ore the refineries hold together. Income
arrives as ore, so a base that has spent its starting cash still has money.

Placement comes first. A finished building blocks its queue until it is
placed, so each one is asked onto the map in the very cycle it is first seen
ready, at the cell `placement` prefers for it. The platform places a building
elsewhere when the cell cannot take it, which is why the next placement reads
where the building really stands, not where it was asked to stand. When a
refinery's ore field is chosen by reach, the match record gets a
`refinery_field` event as the refinery is first asked onto the map: the field,
the rule that chose it and how near its ore the refinery was asked to stand.
With the creep toward ore switched on, a building is placed toward the ore
field of the refinery that is due, when one is: the opening's next step, or
one more that upkeep has called for and that is not placed yet. The power
plant the reach-first opening adds before its first refinery is placed so
whatever the switch says. Each building placed so gets a `creep_placement`
event. With the creep to the next field switched on, upkeep names the creep
target at the start of every cycle (see `creep`) and, once the opening's
build is complete, every building but a defense is placed toward it; its
`creep_placement` event adds the target and its place in the lane. With the
creep from the opening switched on, the creep target named is the far field
whenever there is one, in the opening too, so that the second yard and the
forward hold go by it; and every building but a defense or a refinery is
placed along the line toward it from the opening's first building on,
whatever refinery is due (see `placement`). Its `creep_placement` event adds
the far field's center, the reason `nearest_far_field` and how far the
building's middle lies from the line. With the continuous creep switched on
as well, the far field may lie any distance away and is never a field
placement skipped. With the remote yard growth switched on as well, a second
yard far from the first has a line of its own (see `placement`): of the
buildings that walk a line first asked onto the map since it stood, the 1st,
3rd, 5th and so on take the main base's line and the 2nd, 4th and so on the
second yard's, and their `creep_placement` events add the line, `main` or
`second`. With `refused_detection` switched on,
every place a building is asked onto is compared with where it shows up, and
a place the engine refused is kept off from then on; a far field the engine
keeps refusing the buildings placed toward is skipped (see `refused`). With
`creep_stall_skip` switched on, so is a far field the base makes no headway
toward (see `creep_stall`): upkeep tells the creep keeper of each building
first asked onto the map along a line, and the keeper looks at the headway
at the start of every cycle, before it names the far field. With
`refinery_spread`, `refinery_growth` or `mcv_new_area` switched on, the fields
the place of each refinery asked onto the map stands against are kept for
`promise_ticks`, with the place's middle, and so is the field a claim or
growth refinery on order is for; with the fields a refinery stands against
they are the fields that are taken (see `placement`), and upkeep publishes
them at the start of every cycle, before anything reads them. With
`walked_routes` switched on, a rule of upkeep sends a rifleman to walk to the
far field of each line first, one at a time, and keeps the route it walked
(see `route_walk`); a building placed along a line whose far field has a
route follows the route rather than the straight line. A finished building
that has waited `held_cancel_ticks` unplaced, held back or asked onto a place
that never took it, is cancelled, so that its queue takes the next building
rather than freeze; where no cancel order can be sent, it is placed on the
free place nearest the construction yard instead, whatever held it back.
Either is a `creep_placement` event of the reason `held_cancelled`.

Power comes next. A base that drains more than it provides builds at a third
of the speed and its defenses go dark, so a power plant is ordered while
there is still some power left over, counting what the buildings on their way
will drain, and it is ordered ahead of whatever else the Building queue was
going to make. How much is kept over grows with the base (`power_target`): a
base with many barracks and war factories, or Tesla coils, can lose a great
deal of power at once. Every building upkeep orders counts its own drain
first, and when it would leave less than the target over, the power plant is
ordered in its place and the building waits. With `lean_power` switched on,
the target is instead `headroom_share` of what the base will drain, at
least `headroom_early`, and the Tesla coils' as before: about 15 percent
to spare, and still never short.

What the opening built is the core of the base, and the opening builds it
once. When its build is complete and one of those buildings is lost later,
it is upkeep that queues it again: the power plants first, then the barracks,
the refineries, the war factory, and the service depot if the base had one.
The construction yard cannot be built again and is not on the list. A
replacement waits for money like any other item, with the reserve left over,
and while it waits the Infantry and Vehicle queues are given nothing new: a
base that keeps spending on units never gathers the price of a barracks or a
refinery. Power plants are cheap and are not saved for.
The defenses of the opening are replaced the same way on the Defense queue:
the defense against infantry when none stands, and the gun when it stood and
was lost. With the opening's `defense_on_threat` on, the opening buys its
defense against infantry only when a rush or a threat calls for it, so a
base that never had one is not given one here either: it too is replaced
only once it has stood. While the base is under attack no replacement is started, and one
replacement defense a minute at most is, so that a base that keeps losing its
defenses does not pour all its money into them. With `defenses_on_threat_only`
switched on, upkeep buys a defense only against a real threat, and a lost
defense of the opening is not replaced at all.

Harvesters are the income. Each refinery should have two at work, so one more
is trained whenever there are fewer and a war factory to make it, up to eight;
with `refinery_growth` switched on, up to two for each refinery that stands
when that is more.
Two refineries do not carry a whole match: once the opening is over and the
refineries are three quarters full or every one of them already has its two
harvesters, a third refinery is built as soon as the money reaches 1,500, and
from the eighth minute on a fourth. From the sixth minute on, the third is
saved for like a lost core building: from the moment it is called for until it
stands, the Infantry and Vehicle queues are given nothing new, and it goes
ahead of the service depot. Before that, units come first: the first minutes
are when a rush comes. With `refinery_field_cap` switched on, no field gets
more refineries than a field takes (`max_refineries_per_field`): one more is
ordered only when the place placement would ask it onto now stands against a
field that fewer serve, such as the creep target, and it is placed against
such a field. While there is none it is not ordered, nothing is saved for it,
and the base creeps on toward the target; the fields are looked at again a
second later. A `refinery_field` event with `cap_held` records each refinery
so ordered and the start of each hold. With `refinery_claims_reached`
switched on, upkeep publishes in every cycle the far fields the base has
reached that a refinery is to claim (see `creep`), and whether a claim is
due: a claim field, and fewer than `claim_refineries_max` refineries standing
or on their way. While a claim is due, one refinery is ordered after the
opening as soon as the money covers it and the reserve, whatever the other
refinery rules say: the most refineries, the money they wait for, how full
the refineries are and the time from which the third is saved for. A refinery
placed after the opening while there is a claim field stands against the
oldest one it can stand against (see `placement`), and its `refinery_field`
event gives the rule `claim`. With `refinery_spread` switched on as well, a
claim field that is taken is dropped for the rest of the match (see `creep`),
and the claim refinery on order is placed against the field it was ordered
for, so that no field is claimed twice. With `refinery_growth` switched on,
the economy grows past four refineries: while at least four stand, fewer
than `growth_refineries_max` stand or are on their way, none is on its way and
a field that is not taken can be stood against now, a growth refinery is due,
as upkeep decides at the start of every cycle. It is ordered after a due
claim, as soon as the money covers it and the reserve, whatever the other
refinery rules say; while it is due the refinery rule is asked right before
one more producer, so that it takes the Building queue first. It stands
against the field it was ordered for, and its `refinery_field` event gives
the rule `growth`. While it is due and the money does not cover it and the
reserve, the unit queues spend only what lies above its price and the
reserve, `growth_saving_seconds` at the most for each one, and they leave its
price alone in the cycle it is ordered; a saving that runs out is a
`refinery_field` event with `saving_timed_out`, and the next starts that long
after. Once no more than `spend_remaining_seconds` of the match are left (the
time of the end-of-match spending, see `clock`, whatever the money), a
saving in force ends without an event and none starts again. With
`economy_first` on, a due growth refinery is the economy's item
right after a claim. A harvester
that stands idle earns nothing; it is told to harvest again once it has been
seen idle a few states in a row, which leaves alone one that is only turning
round at the refinery, and it is not told twice in quick succession. A
harvester that base defense has sent to shelter from an enemy waits there
until base defense sends it back. With `harvesters_to_fresh_ore` switched
on, a field whose harvesters keep being sent back to harvest by their
refinery counts as run dry, and its harvesters are sent to the nearest safe
field with ore instead: see `fresh_ore`. With the `park_harvesters` switch on, the
harvesters stop mining and wait behind the base, away from the enemy, in the
last minutes of a match and while a lead is preserved against a major threat:
see `parking`.

With `second_yard` of [expansion] switched on, upkeep also gives the base a
second construction yard early: a service depot, an MCV, and the MCV taken
with an escort to the field the base creeps toward and deployed there; see
`expansion`. With `mcv_first` switched on as well, the MCV is bought first:
upkeep decides at the start of every cycle whether a full hold for its money
is in force (see `economy_hold`), and while one is, it orders no producer,
refinery, service depot, radar or kennel, none of its own defenses and no
unit of the army mix. A power plant goes ahead only when the base would be
short of power without it, not for the rest of the power target, and
harvesters only up to one a refinery. A lost core building, base defense's
emergency orders and the scout slots' orders still go ahead, and so does the
spending at the end of the match. The MCV's rule is then asked right after
power and a lost core building, so that in the cycle its money is there it
is ordered ahead of everything else upkeep orders. With `mcv_reserve_field`
switched on, upkeep publishes at the start of every cycle, before any of its
rules is asked, the field of the MCV's site while the MCV is on order, out
and waiting or on its way (see `expansion`), and no refinery is asked onto a
place against that field: a refinery whose place would stand against it is
placed again with every such place kept off, and one that then stands against
no field at all waits, looked at again `place_again_after_ticks` later. While
it is reserved the field is no claim field and no growth refinery is due for
it. With `mcv_repeat` switched on, upkeep looks once a cycle, right after the
fields refineries were asked against, for the site of the next MCV, which is
wanted while the yards and MCVs are fewer than the income allows and a far
field qualifies (see `expansion`).

A yard that earns nothing pays nothing back, so when an expansion yard
deploys by the field of its site, one refinery is claimed for that field:
upkeep publishes the field at the start of every cycle (`new_yard_field`)
while fewer than `max_refineries_per_field` refineries serve it or are asked
against it and fewer than `claim_refineries_max` stand or are on their way; a
field at its cap claims nothing. While it is published, a rule asked right
after power, and not held by a full hold for the MCV, orders the refinery as
soon as the Building queue is free and the money covers it and the reserve,
or the power plant the power target calls for first. That refinery is on
order for the field, so it is placed against it from the yard nearest it,
the new one, whatever the creep target, and its `refinery_field` event gives
the rule and the reason `new_yard`. The claim ends once the refinery is
ordered: one refinery for each new yard. A refinery claim is due while the
field is published, so `economy_first` holds the unit queues for its money.
With `new_yard_refinery_until_standing` on, the field is published whatever
the refinery caps say until a refinery of ours stands counted against it, and
its refinery is ordered only while its place exists and none is on its way,
placed there once the own units on it have moved off, and ordered again after
a refusal or a cancel (see `new_yard_refinery`); a later placement of the same
claim is written with the reason `new_yard_retry`.

With `scout_slots` of [scouting] switched on, a Soviet base also gets one
kennel once the opening's build is complete, so that its designated scouts
can become dogs (see `scout_slots`); none while `soviet_scout_unit` names the
flak truck, which the war factory trains. It is ordered on a free Building
queue once the money covers it and the reserve, after one more producer, and
like any building waits for the power plant the power target calls for.

At the end of the match the money left is spent, and while a lead is kept a
defense is added now and then: see `clock`.

A queue that stands empty while there is money is production lost for good.
The Infantry and the Vehicle queue are therefore each given an item as soon as
it is free, whatever the other one holds, and infantry a few at a time, so
that a change of plan is not stuck behind a long queue. The item is the one of
the plan's mix that the army is furthest short of: the shares the mix gives
the queue's items, taken among those items alone, set against what the units
that queue makes are worth. Both queues spend from one budget: the money, less
a reserve left untouched for the buildings, less what this cycle's orders for
a power plant, the base's emergency or a new barracks or war factory will
cost. When the budget pays for the next item of both queues, both are fed.
When it pays for one, the queue that has had less than its share of the last
minute's spending on the army, harvesters left out, goes first and the other
waits; the Vehicle queue's share is what the mix gives its items together.
Infantry is cheap and quick, and a base that always buys what it can afford
at once ends up with an army of it that tanks roll over. A queue that
another manager has claimed for orders of its own is left to that manager.
With `no_army_infantry` switched on, once an own war factory stands the
Infantry queue is given no rifles: rocket soldiers, against armor and
aircraft, while fewer than `rockets_kept` stand in main, the guard and the
escort; then the rocket soldiers of the mix, and the flamethrowers of the
Soviet infantry counter (see `composition`), that the army is short of for
the share of its value the mix gives them, the one it is furthest short of
first, and nothing once it has them. The Vehicle queue goes first whenever
the budget pays for one queue only. The raid quota's infantry is the raid's
and is given as before, and so is everything with no war factory standing.
With `economy_first` switched on, the unit queues are given nothing new
while the economy's next item is due and the money does not cover it yet:
the refinery a claim calls for, one more war factory, the second yard's
MCV. Such a hold lasts `economy_wait_seconds` at the most, and the queues
are then fed for `economy_release_seconds` at the least (see
`economy_hold`). Only the unit queues wait: what the rules before them
order, and what other managers order on queues they claim, never does.
While the ore is near the storage cap (see `bank_spending`), no such hold is
in force: what comes in then would be lost, not gathered.
With `tank_factories` of [producers] switched on, the Vehicle queue is fed
whenever it holds fewer than `tank_factories_queued` items, not only when it
stands empty: a vehicle before the infantry as before, and more of it from
what the infantry leaves, so that the queue never waits for the next order
(see `tank_factories`). Once two war factories stand, the vehicles' share is
then 60 percent at the least, and when the budget pays for one queue the
infantry goes first only while the vehicles keep that share with the
infantry's price spent.
With `counter_quota` of [composition] switched on, each unit queue the plan
feeds trains first the counter unit the enemy army seen lately calls for that
the army is furthest short of, and a Soviet base orders one flame tower for
the flamethrowers it wants (see `counter_units`).
With `spending_focus` on, under the military focus (see `spending_focus`) no
refinery is ordered past those of the opening but in place of a lost one: no
claim, no growth, no third or fourth, none from the bank; one ordered is
placed, and the harvesters for those standing are trained as ever. The
Vehicle queue's next army vehicle is then the family's artillery while main
and the guard hold fewer than `military_focus_first_artillery` of it, and
defenses go up toward the enemy (`FocusDefenses`).

One barracks and one war factory make units only so fast; a base whose income
outgrows them banks money it cannot spend. So once the opening's build is
complete, one more barracks, or else one more war factory, is ordered when its
queue has been busy most of the last minute, the income measured over that
minute calls for it, and the money above the reserve, with half of what comes
in while it is built, pays for it: the other half is left to the unit queues.
Each one more needs more income, and there is a most of each. For the second
and third barracks money that has piled up for half a minute will do instead
of the income, and for the last two the same once both unit queues are busy.
A barracks goes first: it is cheaper and pays back sooner. One is ordered at a
time, not while the base is under attack nor while the money is saved for a
lost core building, and its price is kept from the unit queues in the cycle
it is ordered in. With `no_army_infantry` switched on, no barracks is added
while a war factory stands: the army trains no infantry for it to make. With
`economy_first` switched on, one more war factory also waits until the money
covers its price and the reserve, so that it is not built from what the unit
queues leave over. With `tank_factories` of [producers] switched on, the war
factory has gates of its own, which open sooner (a lower income for each one
more, a queue busy a smaller share of the minute, and not before a set time),
and it is asked before the barracks; it still waits for the power target, and
while the money is saved for the third refinery. When neither is due, the
money banked has its say (see `bank_spending`): with one war factory
standing and the money above the reserve banked for a while, the second is
ordered whatever the income; and a Building queue that has stood empty for
a while with a big bank is given a war factory, else a refinery for a field
no refinery stands against, else the power plant the power target calls
for, never a defense. Ore near the storage cap comes before all of these:
once the ore held has been at least `storage_spend_share` of what can be
stored for `storage_spend_seconds`, one more war factory is ordered while
fewer than `storage_war_factories_max` stand or are on their way, past
`max_war_factories`, one in `storage_spend_seconds` at most, on the same
terms as any producer but its gates (see `bank_spending`). With
`war_factory_load_gate` of [producers] on, each of these rules orders a war
factory beyond the first only while the production load justifies it, on
top of its own terms: the vehicle income reaches `war_factory_load_share` of
what the Vehicle queue spends at the war factories standing or on their way
(see `war_factory_load`). A war factory so held is no economy item the unit
queues wait for, and an idle Building queue goes on to the refinery or the
power plant. With `war_factory_on_load` of [producers] on as well, the load
gate is the trigger of the producers' own war factory beyond the first: from
`war_factory_on_load_from_seconds` on, it is due whenever the gate passes,
there is room under `max_war_factories`, none is on its way and the money
above the reserve, with `float_seconds` of the income, pays for it; the
income steps, their busy shares and their time no longer apply, and each
such order is an `economy` event of the reason `war_factory_on_load`.

The service depot is the first step up in technology that upkeep takes: it
opens the heavier tanks. It is built once, when the war factory stands and
the economy can carry it, and never at the price of the base's power. A main
force built around tanks (`main_force` of the army mix) rushes it: it is the
next building after the war factory, ordered as soon as the war factory
stands and the Building queue is free, whatever the money and the
harvesters, ahead of a third refinery, of one more barracks or war factory
and of the kennel. Only a power plant the power target calls for, a lost
core building saved for and a refinery claim due, which the economy has
committed to, keep their place ahead of it; it is not started under a major
threat. Until it stands, the unit queues are given nothing new while the
money does not cover its price and the reserve, but under a major threat,
so that it is paid for as it is made. The
radar is the second: it opens the artillery, which the army mix brings in
once a radar stands. It is built from the sixth minute on, once the service
depot stands, when the money above the reserve, with half of what comes in
while it is built, pays for it, the base is not under attack and no lost core
building is saved for; and built again when it is lost. Like the depot, it
waits for the power it needs.

The radar also opens the AA defenses. While more than a few enemy combat
aircraft are seen (see `anti_air`: three in the last three minutes as the
tuning file ships) and a radar stands, the base keeps an AA defense, and a
second from six aircraft: a single one is soon outnumbered by planes that
keep coming. A helipad or airfield seen calls for none. They are ordered on
the Defense queue one at a time, on the radar's terms (a quiet base, no lost
core building saved for, the power target and the same test of the money),
and a lost one is ordered again while they are wanted. Each is placed by the
refinery nearest the enemy's start.

Tanks roll over flame towers. Once a war factory stands, a Soviet base keeps
`soviet_tesla_coils` Tesla coils, its defense against armor, on the AA
defenses' terms, and a lost one is ordered again; they are placed as any
other defense. A coil drains a great deal of power, so one that would leave
less than the power target over waits for a power plant, which is ordered
first. The opening builds none: the coil costs too much for the first
minutes. The tuning file ships with two kept. With `defenses_on_threat_only`
switched on, a coil answers an attack instead: one is ordered only for a
vehicle attack base defense has recorded (see `base_defense`) whose cell lies
more than `tesla_side_cells` from every coil standing and from the one on its
way, still no more than `soviet_tesla_coils` in all, and while the attack
goes on too; it is placed at the free place nearest the attack's cell.

Repair costs money while it runs, so it is switched on for a building that is
badly damaged when there is money to spare, and off again when the building is
nearly whole or the money is gone. A switch says which way it goes, on or off,
so that sending it twice never undoes it; and it is not sent a second time
while the first may still be on its way to the game. A game may not report
that a building is being repaired, so a `repair on` the game took is not sent
again for a while whatever the state says: only one it rejected or lost is.

New units leave their barracks or war factory for its rally point. Each such
building has its rally point set, once, to the staging point: in front of the
base on the enemy's side, where the army gathers, and clear of the buildings
so that what is produced does not stand in the way of what is built next.
With the forward hold of `[combat]` on, the rally point is where main gathers,
as the combat manager publishes it, and it is set again for each such
building whenever that point moves.

Last, the emergency. When the base drains more power than it has, and no
power plant is about to be finished, the consumers it can best do without are
switched off until the rest is powered again: the service depot first, then
the radar, then the defenses, but never a defense that has an enemy near. The
platform reports the moment with a `low_power` event; the rule reads the
power figures of the state, which say the same and are still there when
events were missed. What was switched off is switched on again when there is
power to spare, the most needed first and only as far as the power reaches.

With `canary_alarm` on, while the canary alarm is in force (see
`canary_alarm`) the unit queues train nothing, no harvester is trained but
one for a standing refinery that has none, and no MCV, refinery beyond the
second, barracks or war factory beyond the first, radar or tech building is
ordered: the money goes to the defenses base defense orders meanwhile.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping, NamedTuple, Protocol, Sequence

from ucla_agent.core import records
from ucla_agent.games.redalert import (
    cells,
    clock,
    defense_cap,
    defense_line,
    expansion,
    new_yard_refinery,
    placement,
    refinery_fields,
    safe_staging,
    truck_shares,
)
from ucla_agent.games.redalert.anti_air import ANTI_AIR_DEFENSES, anti_air_defenses_wanted
from ucla_agent.games.redalert.bank_spending import (
    BANK_FACTORY,
    IDLE_BANK,
    NO_FLOAT,
    STORAGE_NEAR_CAP,
    BankWatch,
    StorageWatch,
    log_orders,
)
from ucla_agent.games.redalert.batch import Intent, Limits, Priority, validated
from ucla_agent.games.redalert.canary_alarm import held as held_by_alarm
from ucla_agent.games.redalert.composition import FLAMETHROWER, OPENING, Composition
from ucla_agent.games.redalert.counter_units import CounterFill, FlameTowerForCounters
from ucla_agent.games.redalert.creep import NearestFarField, NextPatch
from ucla_agent.games.redalert.economy_hold import EconomyHold, EconomyItem, FullHold
from ucla_agent.games.redalert.expansion import (
    ESCORT,
    ExpansionDepot,
    ExpansionMcv,
    ForwardMcv,
    has_mcv_money,
    is_mcv_first,
    is_mcv_wanted,
    is_second_mcv_due,
)
from ucla_agent.games.redalert.family import (
    ADVANCED_POWER_PLANT,
    BARRACKS_TYPES,
    CONSTRUCTION_YARD,
    FAMILIES,
    HARVESTER,
    MCV,
    POWER_PLANT,
    RADAR,
    REFINERY,
    ROCKET_INFANTRY,
    SERVICE_DEPOT,
    WAR_FACTORY,
    Family,
)
from ucla_agent.games.redalert.fresh_ore import FreshOre
from ucla_agent.games.redalert.groups import GUARD, MAIN
from ucla_agent.games.redalert.harvester_room import (
    FieldRoom,
    field_share,
    harvester_target,
    harvesters_counted,
    serving_counts,
)
from ucla_agent.games.redalert.items import SOVIET, TICKS_PER_SECOND
from ucla_agent.games.redalert.ledger import LOST, REJECTED, OrderLedger
from ucla_agent.games.redalert.managers import MAJOR_THREAT, MatchContext
from ucla_agent.games.redalert.opening import build_steps, first_missing, is_build_complete
from ucla_agent.games.redalert.new_yard_refinery import NEW_YARD_RETRY_REASON, NEW_YARD_RULE, ClaimedSpots
from ucla_agent.games.redalert.parking import Parking
from ucla_agent.games.redalert.placement import Box, FieldChoice
from ucla_agent.games.redalert.production import BUILDING_QUEUE, DEFENSE_QUEUE, INFANTRY_QUEUE, VEHICLE_QUEUE
from ucla_agent.games.redalert.refused import RefusedPlaces
from ucla_agent.games.redalert.remote_air import RemoteBaseAntiAir
from ucla_agent.games.redalert.route_walk import RouteWalks
from ucla_agent.games.redalert.rush_counter import warning_reserve
from ucla_agent.games.redalert.soviet_tech import SovietTechCenter
from ucla_agent.games.redalert.spending_focus import FocusDefenses, SpendingFocus, artillery_first, is_military
from ucla_agent.games.redalert.support import SupportTraining
from ucla_agent.games.redalert.tank_factories import VEHICLE_FLOOR_SHARE, VehicleFill, keeps_vehicle_floor
from ucla_agent.games.redalert.terrain import OrePatch
from ucla_agent.games.redalert.tuning import FACTORY_FIRST, TANKS, PlacementTuning, ProducersTuning, UpkeepTuning
from ucla_agent.games.redalert.vehicle_turn import VehicleTurn
from ucla_agent.games.redalert.war_factory_load import (
    ON_LOAD,
    PRODUCER_GATES,
    TANK_FACTORY_GATES,
    LoadGate,
    has_money_within,
    is_loaded,
    is_on_load,
    is_on_load_wanted,
)
from ucla_agent.games.redalert.world import DEFENSE_TYPES, Building, Cell, Unit, World

# The buildings that make units, which walk to the building's rally point when they are made.
_PRODUCERS = BARRACKS_TYPES | {WAR_FACTORY}
# Why the new yard's claimed refinery waits, written at most this often (see `MoreRefineries._log_claim_waiting`).
NEW_YARD_WAITING = "new_yard_waiting"
CLAIM_WAITING_TICKS = 30 * TICKS_PER_SECOND
# The rule a lost defense is ordered again by, as a `defense_capped` event names it (see `defense_cap`).
REPLACE_RULE = "replace_defense"
_POWER_PLANTS = (POWER_PLANT, ADVANCED_POWER_PLANT)
# The queues the unit queue rule feeds, in the order their next items are chosen.
_UNIT_QUEUES = (INFANTRY_QUEUE, VEHICLE_QUEUE)
# What a cycle's orders for keep from the unit queues' budget, besides the base's emergency.
_SET_ASIDE = frozenset({*_POWER_PLANTS, *_PRODUCERS, MCV})
# The groups whose rocket soldiers count toward those `no_army_infantry` keeps: not the raid group's, nor a scout.
_ROCKET_GROUPS = frozenset({MAIN, GUARD, ESCORT})
# The share a queue's one item has of the queue's items.
_WHOLE_SHARE = 1.0
# The infantry of the mix that `no_army_infantry` still trains for their share, the first first on a tie: the rocket
# soldiers, against armor and aircraft, and the flamethrowers of the Soviet infantry counter.
_HELD_EXEMPT = (ROCKET_INFANTRY, FLAMETHROWER)
# The harvesters a refinery is given while a full hold for the second yard's MCV is in force: enough that none stands
# without income, and no more, so that the money gathers for the MCV.
_HELD_HARVESTERS_PER_REFINERY = 1
# The Soviet defense that drains more power than any other.
_TESLA_COIL = "tsla"
# The Soviet building dogs are trained from, which the scout slots' dogs need, and the dog's name in soviet_scout_unit.
KENNEL = "kenn"
_DOG = "dog"
# The number of the first producer of a kind that the producer rule adds: the opening builds one of each.
_FIRST_EXTRA = 2
# The barracks for which money that has piled up will do instead of the income: early, and while both queues are busy.
_EARLY_FLOAT_BARRACKS = frozenset({2, 3})
_LATE_FLOAT_BARRACKS = frozenset({6, 7})
# What is switched off first when power is short, the most dispensable first; the defenses come after these.
_DISPENSABLE_FIRST = (SERVICE_DEPOT, RADAR)
# The match record's event for the ore field a refinery is asked to serve when the field is chosen by reach, and its
# entries about the field, null when there is no field to serve.
REFINERY_FIELD_EVENT = "refinery_field"
_FIELD_REASONS = ("rule", "field_center", "field_cells", "served_by", "ore_distance_cells")
# The match record's event for a building placed toward the ore field of a refinery that is due, and the decimal
# places its distances are written with.
CREEP_PLACEMENT_EVENT = "creep_placement"
_DISTANCE_DECIMALS = 2
# The reason a `creep_placement` event gives for a building placed toward the creep target along the lane, and for
# one placed along the line toward the far field.
NEXT_PATCH_REASON = "next_patch"
NEAREST_FAR_FIELD_REASON = "nearest_far_field"
# The reason of the `creep_placement` event of a finished building cancelled, or placed anywhere, after waiting too long.
HELD_CANCELLED_REASON = "held_cancelled"
CANCEL = "cancel"
PLACE = "place"
# How long (a second) a refinery the cap of refineries to a field holds back waits before the fields are looked at
# again: where it would be placed is worked out afresh each time, which is too much for every cycle.
_CAP_RECHECK_TICKS = 25
# How many own refineries stand before the economy grows past them with `refinery_growth` on.
_GROWTH_FROM_REFINERIES = 4


class Rule(Protocol):
    """One thing upkeep looks after."""

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The orders the rule wants sent in this cycle."""


@dataclass(frozen=True)
class _Asked:
    """A cell that a building was asked onto."""

    tick: int
    # The cells the building will cover there; None when no cell was named.
    box: Box | None
    # Whether the building takes the second yard's line rather than the main base's, while there are two.
    second_line: bool = False
    # For a refinery while the asked fields are kept (`_keeps_asked_fields`): the ore fields its box stands against,
    # and the box's middle cell; none and None for any other building.
    fields: tuple[OrePatch, ...] = ()
    refinery: Cell | None = None


class _Turns:
    """Which line the buildings that walk one take while a far second yard has a line of its own: of those first asked
    onto the map since that yard stood, the 1st, 3rd, 5th and so on the main base's line, the 2nd, 4th and so on the
    second yard's."""

    def __init__(self) -> None:
        # The id of the second yard the turns are counted for, and how many buildings have taken one since it stood.
        self._yard: int | None = None
        self._taken = 0

    def is_second(self, item: str, world: World, ctx: MatchContext) -> bool:
        """Whether a building first asked onto the map now takes the second yard's line; a building that walks a line
        takes its turn whether or not that line can have it."""
        yard = placement.second_yard(world, ctx.tuning.placement)
        if yard is None or item == REFINERY or item in DEFENSE_TYPES:
            return False
        if yard.id != self._yard:
            self._yard, self._taken = yard.id, 0
        self._taken += 1
        return self._taken % 2 == 0


class Placement:
    """Asks every finished building onto the map in the cycle it is first seen ready."""

    def __init__(
        self,
        refineries: MoreRefineries,
        coils: TeslaCoils,
        far_field: NearestFarField,
        walks: RouteWalks,
        forward: ForwardMcv,
        remote_air: RemoteBaseAntiAir,
    ) -> None:
        # The rule that adds refineries after the opening's, which says when one more is called for.
        self._refineries = refineries
        # The rule that answers an air attack on a remote base, which says where its AA defense goes and which one it
        # canceled.
        self._remote_air = remote_air
        # The rule that takes the second yard's MCV to its site, whose yard box no building may take.
        self._forward = forward
        # The creep keeper, which follows the headway of the buildings placed along a line toward a far field.
        self._far_field = far_field
        # The rule that walks a rifleman to each far field, whose routes the lines toward them follow.
        self._walks = walks
        # The rule that orders Tesla coils, which says where a coil that answers a vehicle attack goes.
        self._coils = coils
        # Item -> the last time a waiting building of that item was asked onto the map.
        self._waiting: dict[str, _Asked] = {}
        # The cells asked for lately, which the buildings asked there may not show on yet.
        self._promised: list[_Asked] = []
        # The places the engine put a building elsewhere than asked, with `refused_detection` on.
        self._refused = RefusedPlaces()
        # Whose line the next building takes, with `remote_yard_growth` on and a far second yard.
        self._turns = _Turns()
        # Item -> the last time a waiting refinery was held back for the field reserved for the MCV's site.
        self._held: dict[str, int] = {}
        # Item -> the tick a finished building of that item was first seen waiting to be placed.
        self._ready_since: dict[str, int] = {}
        # The field the refinery a new yard claims was last asked onto a place against, while the claim lasts; and the
        # tick own units were first seen on its place, while they are moved off it.
        self._claim_asked: OrePatch | None = None
        self._clearing_since: int | None = None
        # The lanes threats came to our base by, which a ground defense ordered toward no cell is lined up across.
        self._lanes = defense_line.LaneMemory()

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """A `place` for each building that waits and was not asked onto the map a moment ago, or held back a moment
        ago, or its cancel once it has waited too long (`_order_for`); with `refused_detection` on, the places refused
        noticed first, so that none of them is asked for again."""
        tuning = ctx.tuning.placement
        self._lanes.note(world, ctx)
        ctx.exact_defense_goals &= set(ctx.defense_goals)
        if tuning.refused_detection:
            self._refused.notice(world, ctx)
            ctx.refused_places = self._refused.places
        self._waiting = {item: asked for item, asked in self._waiting.items() if item in world.ready_to_place}
        self._held = {item: tick for item, tick in self._held.items() if item in world.ready_to_place}
        self._ready_since = {item: self._ready_since.get(item, world.tick) for item in world.ready_to_place}
        self._forget_old_promises(world, tuning)
        if ctx.new_yard_field is None:
            self._claim_asked = None
        due = (self._order_for(item, world, ctx) for item in world.ready_to_place if self._is_due(item, world, tuning))
        return [order for orders in due for order in orders]

    def _order_for(self, item: str, world: World, ctx: MatchContext) -> list[Intent]:
        """The orders for a waiting building that is due: once it has waited `held_cancel_ticks` since it was first
        seen ready, its cancel or its placement anywhere (`_unfreeze`); the refinery a new yard claims, on order for
        its field, by `_place_claimed`; else its place order (`_place`), none when it is held back."""
        if world.tick - self._ready_since[item] >= ctx.tuning.placement.held_cancel_ticks:
            return [self._unfreeze(item, world, ctx)]
        if item == REFINERY and self._is_claimed(ctx):
            return self._place_claimed(world, ctx)
        order = self._place(item, world, ctx)
        return [] if order is None else [order]

    def _is_claimed(self, ctx: MatchContext) -> bool:
        """Whether the refinery waiting to be placed is the one a new yard claims while the claim lasts until its
        refinery stands: it was ordered for the claim (`MoreRefineries.on_order`)."""
        on_order = self._refineries.on_order
        is_for_claim = on_order is not None and on_order.rule == NEW_YARD_RULE
        return new_yard_refinery.is_on(ctx) and ctx.new_yard_field is not None and is_for_claim

    def _place_claimed(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The orders for the refinery a new yard claims: none while no place against its field is free (held back);
        a move off the place for each own unit on it or next to it, for `UNITS_CLEAR_TICKS` at the most; else its place
        order, written as a `refinery_field` event of the reason `new_yard`, or `new_yard_retry` for a later placement
        of the same claim."""
        promised = self._promised_boxes(world)
        choice = self._refineries.claimed_spot(world, ctx, promised, self._refused.places)
        if choice is None:
            self._held[REFINERY] = world.tick
            return []
        moves = self._moves_off(choice.cell, world, ctx)
        if moves:
            return moves
        is_retry = self._claim_asked == ctx.new_yard_field
        _log_refinery_field(choice, world, NEW_YARD_RETRY_REASON if is_retry else NEW_YARD_RULE)
        self._claim_asked = ctx.new_yard_field
        box = placement.box_at(REFINERY, choice.cell)
        asked = _Asked(world.tick, box, False, *_asked_refinery(REFINERY, box, ctx))
        self._waiting[REFINERY] = asked
        self._promised.append(asked)
        self._refineries.note_claim_asked(world.tick)
        if ctx.tuning.placement.refused_detection:
            self._refused.asked(REFINERY, box, world.tick, None)
        return [_placing(REFINERY, choice.cell)]

    def _moves_off(self, cell: Cell, world: World, ctx: MatchContext) -> list[Intent]:
        """A move off the claimed refinery's place for each own unit on it or next to it (`new_yard_refinery.units_on`),
        toward the new yard's side, from the first cycle one stands there until `UNITS_CLEAR_TICKS` later; none once the
        place is clear or the time is up."""
        units = new_yard_refinery.units_on(cell, world)
        if not units:
            self._clearing_since = None
            return []
        self._clearing_since = world.tick if self._clearing_since is None else self._clearing_since
        if world.tick - self._clearing_since > new_yard_refinery.UNITS_CLEAR_TICKS:
            self._clearing_since = None
            return []
        area = placement.box_at(REFINERY, cell).grown(new_yard_refinery.UNITS_CLEAR_CELLS)
        return expansion.moves_off(area, units, ctx.new_yard_cell or cell, world, ctx.terrain)

    def _unfreeze(self, item: str, world: World, ctx: MatchContext) -> Intent:
        """Free the queue a finished building has held too long: cancel it with the `cancel` order, or where that
        order cannot be sent (see `batch.validated`), place it on the free place nearest the construction yard
        (`_anywhere`), whatever held it back, or with no cell for the platform to choose. Either is written as a
        `creep_placement` event of the reason `held_cancelled`; the building is looked at again only
        `place_again_after_ticks` later."""
        held = world.tick - self._ready_since[item]
        cancel = Intent(Priority.PRODUCTION, {"cmd": CANCEL, "item": item})
        can_cancel = validated(cancel, world, Limits()) is not None
        cell = None if can_cancel else self._anywhere(item, world, ctx)
        intent = cancel if can_cancel else _placing(item, cell)
        box = None if cell is None else placement.box_at(item, cell)
        self._waiting[item] = _Asked(world.tick, box)
        if box is not None:
            self._promised.append(self._waiting[item])
        _log_held(item, cell, intent.order["cmd"], held, world)
        return intent

    def _anywhere(self, item: str, world: World, ctx: MatchContext) -> Cell | None:
        """The free place nearest the construction yard a building can be asked onto, off the places promised and
        refused; None when there is none."""
        terrain = placement.with_refused(ctx.terrain, self._refused.boxes)
        promised = self._promised_boxes(world)
        candidates = placement.candidate_cells(item, world, terrain, ctx.tuning.placement, promised)
        return candidates[0] if candidates else None

    def publish_asked_fields(self, world: World, ctx: MatchContext) -> None:
        """Publish the fields the refineries asked onto the map within `promise_ticks` stand against, with the field
        a claim or growth refinery on order is for, as `ctx.asked_refinery_fields`, and the middle cells of those
        refineries' places as `ctx.asked_refinery_cells`; none while they are not kept. The places refused and those
        asked for lately are published too (`ctx.refused_places`, `ctx.promised_places`)."""
        self._forget_old_promises(world, ctx.tuning.placement)
        ctx.refused_places, ctx.promised_places = self._refused.places, tuple(self._promised_boxes(world))
        # Looked at in every cycle, so that a refinery on order for a field is forgotten once placed.
        on_order = self._refineries.on_order_fields(ctx)
        if not _keeps_asked_fields(ctx):
            ctx.asked_refinery_fields, ctx.asked_refinery_cells = [], []
            return
        asked = [field for promise in self._promised for field in promise.fields]
        ctx.asked_refinery_fields = [*asked, *on_order]
        ctx.asked_refinery_cells = [promise.refinery for promise in self._promised if promise.refinery is not None]

    def _forget_old_promises(self, world: World, tuning: PlacementTuning) -> None:
        """Forget the places asked for `promise_ticks` ago or more: a building shows on its place by then."""
        self._promised = [asked for asked in self._promised if world.tick - asked.tick < tuning.promise_ticks]

    def _is_due(self, item: str, world: World, tuning: PlacementTuning) -> bool:
        """Whether a waiting building is to be asked onto the map now: it never was nor was held back, or that was a
        while ago, and it is not the AA defense of a remote base canceled in this cycle."""
        last = self._waiting.get(item)
        tried = self._held.get(item) if last is None else last.tick
        is_again_due = tried is None or world.tick - tried >= tuning.place_again_after_ticks
        return is_again_due and not self._remote_air.withholds(item)

    def _place(self, item: str, world: World, ctx: MatchContext) -> Intent | None:
        """The order that asks a waiting building onto the cell preferred for it, off the cells just promised
        (`_promised_boxes`); None for a refinery held back for the field reserved for the MCV's site (`_off_reserved`).
        """
        promised = self._promised_boxes(world)
        # A building asked again is the same building: it keeps its line, and the record has its reasons already.
        is_first = item not in self._waiting
        second_line = self._turns.is_second(item, world, ctx) if is_first else self._waiting[item].second_line
        choice = self._off_reserved(item, world, ctx, promised, second_line)
        if choice is None:
            self._held[item] = world.tick
            return None
        cell = choice.cell
        if is_first:
            _log_reasons(item, choice, world, ctx)
            self._note_line(choice, world)
        box = placement.box_at(item, cell) if cell is not None else None
        asked = _Asked(world.tick, box, second_line, *_asked_refinery(item, box, ctx))
        self._waiting[item] = asked
        self._promised.append(asked)
        if ctx.tuning.placement.refused_detection:
            self._refused.asked(item, asked.box, world.tick, _far_field_center(choice))
        return _placing(item, cell)

    def _promised_boxes(self, world: World) -> list[Box]:
        """The boxes no building is asked onto: those asked for lately, and while the MCV lives with a site, the box
        its yard will cover there (`ForwardMcv.site_box`)."""
        asked = [asked.box for asked in self._promised if asked.box is not None]
        yard = self._forward.site_box(world)
        return asked if yard is None else [*asked, yard]

    def _note_line(self, choice: placement.PlacementChoice, world: World) -> None:
        """Tell the creep keeper of a building first asked onto the map along a line toward a far field."""
        creep = choice.creep
        if creep is not None and creep.line_distance is not None:
            self._far_field.note_line_placement(creep.yard, creep.field_center, world.tick)

    def _off_reserved(
        self, item: str, world: World, ctx: MatchContext, promised: list[Box], second_line: bool
    ) -> placement.PlacementChoice | None:
        """The cell `_choice` gives a building, off the places the engine refused. A refinery whose place would stand
        against the field reserved for the MCV's site (`ctx.reserved_field`) is placed again with the places against
        that field kept off as well; when it then stands against no field at all, the reserved one was the only field
        it could serve, and it waits: None."""
        refused = self._refused.boxes
        if item == REFINERY and refinery_fields.is_on(ctx):
            return self._unserved(world, ctx, promised, second_line, refused)
        choice = self._choice(item, world, ctx, promised, second_line, refused)
        reserved = ctx.reserved_field
        if item != REFINERY or reserved is None or not _stands_against(choice.cell, (reserved,), ctx):
            return choice
        kept_off = (*refused, *placement.reach_boxes(reserved, ctx.tuning.placement.refinery_ore_cells))
        again = self._choice(item, world, ctx, promised, second_line, kept_off)
        return again if _stands_against(again.cell, ctx.terrain.ore_patches, ctx) else None

    def _unserved(
        self, world: World, ctx: MatchContext, promised: list[Box], second_line: bool, refused: tuple[Box, ...]
    ) -> placement.PlacementChoice | None:
        """With `refinery_unserved_only` on, a refinery's place by `refinery_fields`, off the places refused and those
        against the field reserved for the MCV's site: one of the opening's keeps the place `_choice` gives, spaced
        from a refinery its field has (`spaced_opening`); any other goes where `refinery_fields.choose` says, and waits
        (None) while that is nowhere. Neither its own earlier place nor the field it is on order for counts as a
        refinery standing."""
        kept_off = (*refused, *_reserved_boxes(ctx))
        earlier = self._waiting.get(REFINERY)
        own = earlier if earlier is not None and earlier in self._promised else _Asked(world.tick, None)
        asked = list(ctx.asked_refinery_fields)
        for field in (*own.fields, *self._refineries.on_order_fields(ctx)):
            if field in asked:
                asked.remove(field)
        asked_cells = [cell for cell in ctx.asked_refinery_cells if cell != own.refinery]
        if refinery_fields.is_opening(ctx.production, placing=True):
            today = self._choice(REFINERY, world, ctx, promised, second_line, kept_off)
            return refinery_fields.spaced_opening(today, world, ctx, promised, kept_off, asked, asked_cells)
        return refinery_fields.choose(world, ctx, promised, kept_off, asked, asked_cells)

    def _choice(
        self,
        item: str,
        world: World,
        ctx: MatchContext,
        promised: list[Box],
        second_line: bool,
        refused: tuple[Box, ...],
    ) -> placement.PlacementChoice:
        """The cell placement prefers for a building, along the second yard's line first with `second_line`; for a
        Tesla coil ordered against a vehicle attack, the cell nearest the attack, and for a defense base defense
        ordered toward a cell (`MatchContext.defense_goals`), the cell nearest that; for a refinery that claims a
        field, the cell against it; for the AA defense ordered for a remote base under air attack, the cell there
        (`RemoteBaseAntiAir.choice`). The `refused` places are kept off either way."""
        remote = self._remote_air.choice(item, world, ctx, promised, refused)
        if remote is not None:
            return remote
        attack = (self._coils.goal if item == _TESLA_COIL else None) or ctx.defense_goals.get(item)
        if defense_line.is_on(item, ctx) and item not in ctx.exact_defense_goals:
            # On the main base's edge, in the line across the lane toward the goal (see `defense_line`).
            lane = defense_line.lane_goal(attack, world, ctx, self._lanes)
            attack = attack if lane is None else defense_line.line_slot(lane, world, ctx)
        if attack is not None:
            return _toward_attack(item, attack, world, ctx, promised, refused)
        claim = (
            self._against_on_order(item, world, ctx, promised, refused)
            or _claim(item, world, ctx, promised, refused)
            or self._spread(item, world, ctx, promised, refused)
        )
        if claim is not None:
            return claim
        return placement.choose_placement(
            item,
            world,
            ctx.terrain,
            ctx.tuning.placement,
            promised,
            _aa_refinery_cells(ctx),
            self._creeps_toward_ore(item, world, ctx),
            creep_target_after_opening(ctx),
            ctx.tuning.upkeep.refinery_field_cap,
            refused,
            ctx.skipped_fields,
            second_line,
            self._walks.routes,
        )

    def _against_on_order(
        self, item: str, world: World, ctx: MatchContext, promised: list[Box], refused: tuple[Box, ...]
    ) -> placement.PlacementChoice | None:
        """The cell of the claim or growth refinery on order against the field it was ordered for, which is taken from
        then on (a claim field no more, see `creep`), with the rule it was ordered by; None for any other building,
        while none is on order and when no place reaches that field now."""
        on_order = self._refineries.on_order if item == REFINERY else None
        if on_order is None:
            return None
        tuning = ctx.tuning.placement
        return placement.stand_against(on_order.field, world, ctx.terrain, tuning, promised, refused, on_order.rule)

    def _spread(
        self, item: str, world: World, ctx: MatchContext, promised: list[Box], refused: tuple[Box, ...]
    ) -> placement.PlacementChoice | None:
        """With `refinery_spread` on and the opening's build complete, the cell of a refinery against the nearest
        untaken field it can stand against now, or with none the nearest under the cap (`placement.spread_placement`);
        None for any other building, before then and without such a field. A refinery asked again does not count its
        own earlier place as taking a field."""
        if item != REFINERY or not ctx.tuning.placement.refinery_spread or ctx.opening_build.steps:
            return None
        earlier = self._waiting.get(item)
        own = earlier.fields if earlier is not None and earlier in self._promised else ()
        asked = list(ctx.asked_refinery_fields)
        for field in own:
            asked.remove(field)
        return placement.spread_placement(world, ctx.terrain, ctx.tuning.placement, asked, promised, refused)

    def _creeps_toward_ore(self, item: str, world: World, ctx: MatchContext) -> bool:
        """Whether a building is placed toward a refinery's ore field: the creep is switched on and a refinery is due,
        as the opening's next step that neither stands nor is on its way, or as one more that upkeep calls for; or
        the building is a power plant the opening's build holds for its first refinery's field, which is due."""
        build, production = ctx.opening_build, ctx.production
        is_opening_next = first_missing(build.steps, production) == REFINERY
        if ctx.tuning.placement.creep_toward_ore:
            return is_opening_next or self._refineries.is_called_for(world, ctx)
        return build.creeps and item == POWER_PLANT and is_opening_next and production.planned(REFINERY) == 0


def _placing(item: str, cell: Cell | None) -> Intent:
    """The order that asks a finished building onto a cell; without a cell the platform chooses one near the
    construction yard itself."""
    order = {"cmd": PLACE, "item": item} if cell is None else {"cmd": PLACE, "item": item, "at": list(cell)}
    return Intent(Priority.PLACEMENT, order)


def _log_held(item: str, cell: Cell | None, order: str, held: int, world: World) -> None:
    """Write the `creep_placement` event of a finished building that waited too long: the building, the cell it was
    placed on (none for a cancel), the order (`cancel` or `place`) and how many ticks it had waited."""
    data = {
        "tick": world.tick,
        "building": item,
        "cell": None if cell is None else list(cell),
        "reason": HELD_CANCELLED_REASON,
        "order": order,
        "held_ticks": held,
    }
    records.log(CREEP_PLACEMENT_EVENT, data)


def _stands_against(cell: Cell | None, fields: Iterable[OrePatch], ctx: MatchContext) -> bool:
    """Whether a refinery asked onto a cell would stand against one of the fields: within `refinery_ore_cells` of its
    ore; never without a cell."""
    if cell is None:
        return False
    box, reach = placement.box_at(REFINERY, cell), ctx.tuning.placement.refinery_ore_cells
    return any(placement.reaches(box, field, reach) for field in fields)


def _keeps_asked_fields(ctx: MatchContext) -> bool:
    """Whether the fields refineries are asked or ordered against are kept, for what is taken: with
    `refinery_spread`, `refinery_growth`, `mcv_new_area`, `mcv_reserve_field` or `mcv_repeat` on."""
    tuning, expansion = ctx.tuning, ctx.tuning.expansion
    is_kept_for_spread = tuning.placement.refinery_spread or tuning.upkeep.refinery_growth
    is_kept_for_mcv = expansion.mcv_new_area or expansion.mcv_reserve_field or expansion.mcv_repeat
    return is_kept_for_spread or is_kept_for_mcv


def _asked_refinery(item: str, box: Box | None, ctx: MatchContext) -> tuple[tuple[OrePatch, ...], Cell | None]:
    """The ore fields a refinery asked onto a box stands against, and the box's middle cell, while the asked fields are
    kept; none and None for any other building, for a refinery asked onto no place in particular, and while they are
    not kept."""
    if item != REFINERY or box is None or not _keeps_asked_fields(ctx):
        return (), None
    fields = placement.fields_against(box, ctx.terrain, ctx.tuning.placement.refinery_ore_cells)
    return fields, cells.nearest_cell(*box.center)


def _toward_attack(
    item: str, attack: Cell, world: World, ctx: MatchContext, promised: list[Box], refused: tuple[Box, ...]
) -> placement.PlacementChoice:
    """The candidate nearest the cell of a vehicle attack, of those laid out around the construction yard nearest it,
    off the places the engine refused: a defense that answers an attack stands where the attack came."""
    yards = [(building.x, building.y) for building in world.buildings.values() if building.type == CONSTRUCTION_YARD]
    anchor = min(yards, key=lambda yard: cells.distance(yard, attack), default=None)
    terrain = placement.with_refused(ctx.terrain, refused)
    candidates = placement.candidate_cells(item, world, terrain, ctx.tuning.placement, promised, anchor)
    # The first of several as near is taken, which is the one nearest the yard.
    return placement.PlacementChoice(min(candidates, key=lambda cell: cells.distance(cell, attack), default=None))


def _claim(
    item: str, world: World, ctx: MatchContext, promised: list[Box], refused: tuple[Box, ...]
) -> placement.PlacementChoice | None:
    """With `refinery_claims_reached` on and the opening's build complete, the cell of a refinery against the oldest
    claim field one can stand against now, and the field it claims; None for any other building, before then and
    without such a field."""
    if item != REFINERY or not ctx.tuning.upkeep.refinery_claims_reached or ctx.opening_build.steps:
        return None
    tuning = ctx.tuning.placement
    claims = (
        placement.claim_placement(field, world, ctx.terrain, tuning, promised, refused) for field in ctx.claim_fields
    )
    return next((claim for claim in claims if claim is not None), None)


def _far_field_center(choice: placement.PlacementChoice) -> Cell | None:
    """The center of the far field a building was placed toward along the line; None for any other placement."""
    creep = choice.creep
    return creep.field_center if creep is not None and creep.line_distance is not None else None


def creep_target_after_opening(ctx: MatchContext) -> placement.CreepTarget | None:
    """The creep target placement walks toward: the one published, once the opening's build is complete, so that its
    steps are placed as without it; None before."""
    return None if ctx.opening_build.steps else ctx.creep_target


def _log_reasons(item: str, choice: placement.PlacementChoice, world: World, ctx: MatchContext) -> None:
    """Write what chose a building's cell beyond its own preference, as it is first asked onto the map: the field
    of a refinery chosen by reach, claimed or given one to a field, and the creep of a building toward a refinery's
    field."""
    # The rules by reach are numbered; a claim's and the others' are named.
    is_named = choice.field is not None and isinstance(choice.field.rule, str)
    if item == REFINERY and (ctx.tuning.placement.refinery_field_by_reach or is_named):
        _log_refinery_field(choice, world)
    if choice.creep is not None and choice.cell is not None:
        _log_creep(item, choice.cell, choice.creep, world)


def _log_creep(item: str, cell: Cell, creep: placement.Creep, world: World) -> None:
    """Write one `creep_placement` event: the building placed toward a field, its cell, the field and its ore cell
    nearest the yard, and how far the building's middle lies from that ore cell at the cell nearest the yard and at
    its own. A building placed toward the creep target adds the target's center and place in the lane, and the
    reason; one placed along the line toward the far field adds the field's center, the reason and how far its middle
    lies from the line, and while a second yard has a line of its own, which yard's line it walked."""
    data: dict[str, object] = {
        "tick": world.tick,
        "building": item,
        "cell": list(cell),
        "field_center": list(creep.field_center),
        "ore_cell": list(creep.ore_cell),
        "distance_before_cells": round(creep.distance_before, _DISTANCE_DECIMALS),
        "distance_after_cells": round(creep.distance_after, _DISTANCE_DECIMALS),
    }
    if creep.line_distance is not None:
        data.update(
            target_patch_center=list(creep.field_center),
            reason=NEAREST_FAR_FIELD_REASON,
            line_distance_cells=round(creep.line_distance, _DISTANCE_DECIMALS),
        )
        if creep.yard is not None:
            data["yard"] = creep.yard
    elif creep.lane_rank is not None:
        data.update(target_patch_center=list(creep.field_center), lane_rank=creep.lane_rank, reason=NEXT_PATCH_REASON)
    records.log(CREEP_PLACEMENT_EVENT, data)


def _log_refinery_field(choice: placement.PlacementChoice, world: World, reason: str = NEW_YARD_RULE) -> None:
    """Write one `refinery_field` event: where a refinery is asked onto the map, and the field it is to serve, with
    the rule that chose it, how many refineries served it before and how many cells the refinery's footprint lies
    from its ore. Without a field to serve, the field's entries are null. The refinery a new yard claims gives the
    `reason` too."""
    cell, field = choice.cell, choice.field
    reasons = dict.fromkeys(_FIELD_REASONS) if field is None or cell is None else _field_reasons(field, cell)
    if field is not None and field.rule == NEW_YARD_RULE:
        reasons["reason"] = reason
    records.log(REFINERY_FIELD_EVENT, {"tick": world.tick, "cell": None if cell is None else list(cell), **reasons})


def _field_reasons(field: FieldChoice, cell: Cell) -> dict[str, object]:
    """The entries of a `refinery_field` event that say which field a refinery at `cell` serves and why."""
    patch = field.patch
    return {
        "rule": field.rule,
        "field_center": list(patch.center),
        "field_cells": patch.size,
        "served_by": field.served_by,
        "ore_distance_cells": placement.ore_distance(placement.box_at(REFINERY, cell), patch),
    }


def _aa_refinery_cells(ctx: MatchContext) -> int | None:
    """How near the refinery nearest the enemy's start an AA defense is placed: `aa_refinery_cells` once enemy air
    has been seen; None before, when an AA defense is placed as any other defense."""
    return ctx.tuning.anti_air.aa_refinery_cells if ctx.air_watch.is_air_seen else None


class _HeldByFullHold:
    """A rule that orders nothing while a full hold for the second yard's MCV is in force (see `economy_hold`).

    Only a rule whose memory is what has stood, which it reads again once
    the hold is over, is asked nothing at all while it lasts.
    """

    def __init__(self, rule: Rule, *, lets_claims_through: bool = False) -> None:
        self._rule = rule
        # Whether the rule is asked all the same while a refinery claim is due and the second MCV is due by its time
        # (see `expansion`): that MCV puts off no claim.
        self._lets_claims_through = lets_claims_through

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The rule's orders, or none while a full hold is in force, unless it lets a claim through and one is due
        while the second MCV is due by its time."""
        is_claim_through = self._lets_claims_through and ctx.refinery_claim_due and is_second_mcv_due(world, ctx)
        return [] if ctx.full_hold and not is_claim_through else self._rule.intents(world, ctx)


class PowerPlants:
    """Orders a power plant, ahead of any other building, when the power left over falls below the power target."""

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """One power plant when the headroom is low, none is on its way and the Building queue is free; while a full
        hold is in force, only when the base would be short of power without one."""
        if not _is_power_plant_needed(world, ctx) or not ctx.production.is_queue_free(BUILDING_QUEUE):
            return []
        if ctx.full_hold and not _is_power_short(ctx):
            return []
        return _power_plant(world, ctx)


class NewYardRefinery:
    """Orders the one refinery claimed for the field a new expansion yard deployed by (`ctx.new_yard_field`), right
    after power and ahead of every other building, a full hold for the MCV or not. See the module's note."""

    def __init__(self, refineries: MoreRefineries, forward: ForwardMcv) -> None:
        # The rule that adds refineries, which keeps the field the refinery is on order for; and the rule that took the
        # MCV to its site, which keeps the claim.
        self._refineries = refineries
        self._forward = forward

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for the claimed refinery, or for the power plant it cannot do without, when it is due; the claim
        ends once the refinery is ordered, or with `new_yard_refinery_until_standing` on once one stands against the
        field (see `expansion`)."""
        if ctx.new_yard_field is None:
            return []
        if new_yard_refinery.is_on(ctx):
            return self._refineries.for_claimed_field(world, ctx)
        orders = self._refineries.for_new_yard(world, ctx)
        if any(proposal.item == REFINERY for proposal in ctx.production.proposed()):
            self._forward.end_new_yard_claim()
        return orders


def _is_power_short(ctx: MatchContext) -> bool:
    """Whether the power left over, counting what is on its way, is below zero: the base drains, or will once what is
    on its way stands, more than it provides."""
    return ctx.production.headroom() < 0


def power_target(world: World, ctx: MatchContext) -> int:
    """The power the base keeps left over: the largest of the targets that apply.

    Little in the first minutes, so that neither the opening nor the service
    depot that opens the heavier tanks waits for power it does not need yet;
    more from the sixth minute; more again once many barracks and war
    factories stand or are on their way, and once Tesla coils do, since each of
    those draws a great deal at once. With `lean_power` on, the sixth minute
    and the many producers give way to a share of what the base will drain,
    so that little power is bought that is not used.
    """
    tuning, production = ctx.tuning.upkeep, ctx.production
    targets = [tuning.headroom_early, *_growth_targets(world, ctx)]
    if production.planned(_TESLA_COIL) >= tuning.headroom_tesla_count:
        targets.append(tuning.headroom_tesla)
    return max(targets)


def _growth_targets(world: World, ctx: MatchContext) -> list[int]:
    """The targets that grow with the base: with `lean_power` on, `headroom_share` of the drain planned, rounded up;
    else `headroom_mid` from its time on, and `headroom_large` once its many producers stand or are on their way."""
    tuning, production = ctx.tuning.upkeep, ctx.production
    if tuning.lean_power:
        return [math.ceil(tuning.headroom_share * production.planned_drain())]
    targets = []
    if world.game_seconds >= tuning.headroom_mid_from_seconds:
        targets.append(tuning.headroom_mid)
    if sum(production.planned(item) for item in _PRODUCERS) >= tuning.headroom_large_producers:
        targets.append(tuning.headroom_large)
    return targets


def _is_power_plant_needed(world: World, ctx: MatchContext) -> bool:
    """Whether the power left over is below the target and no power plant is on its way to add to it."""
    production = ctx.production
    on_its_way = production.pending(POWER_PLANT) + production.pending(ADVANCED_POWER_PLANT)
    return production.headroom() < power_target(world, ctx) and not on_its_way


def _power_plant(world: World, ctx: MatchContext) -> list[Intent]:
    """The order for one power plant at power priority: the advanced one when it is on offer and money allows."""
    offered, production = world.available_production, ctx.production
    if ADVANCED_POWER_PLANT in offered and world.economy.spendable >= ctx.tuning.upkeep.advanced_power_min_cash:
        return [production.build(ADVANCED_POWER_PLANT, Priority.POWER)]
    return [production.build(POWER_PLANT, Priority.POWER)] if POWER_PLANT in offered else []


def _build_within_power(item: str, world: World, ctx: MatchContext) -> list[Intent]:
    """The order for a building, or the power plant first when the building would leave less than the power target."""
    production = ctx.production
    return [production.build(item)] if _can_power(item, world, ctx) else _power_plant(world, ctx)


def _can_power(item: str, world: World, ctx: MatchContext) -> bool:
    """Whether a building's drain leaves the power target over, counting what this cycle has already proposed."""
    return ctx.production.can_power(item, power_target(world, ctx))


def _is_opening_over(ctx: MatchContext) -> bool:
    """Whether the opening's build is complete: its last building stands, the war factory, or the service depot under
    factory_first, whose steps go on past the war factory and keep their place ahead of upkeep's buildings."""
    return is_build_complete(ctx.production, ctx.tuning.opening.order)


def _waits_for_scout_vehicle(ctx: MatchContext) -> bool:
    """Whether the Vehicle queue's next order is the scout vehicle the scout slots claim it for while the factory-first
    build is under way: harvesters past one a refinery wait for it, one that replaces a lost free harvester does not."""
    is_under_way = ctx.tuning.opening.order == FACTORY_FIRST and not _is_opening_over(ctx)
    return is_under_way and ctx.claims.is_held_by_other(VEHICLE_QUEUE, UpkeepManager.name)


def _is_money_for(item: str, world: World, ctx: MatchContext) -> bool:
    """Whether the money covers an item and leaves the production reserve over, and the rush watch's warning reserve
    for an optional building (see `rush_counter.warning_reserve`)."""
    reserve = ctx.tuning.upkeep.production_cash_reserve + warning_reserve(item, ctx)
    return world.economy.spendable >= ctx.production.cost(item) + reserve


def _clears_warning_reserve(item: str, world: World, ctx: MatchContext) -> bool:
    """Whether the rush watch's warning holds nothing back from a building, or the money covers it with both
    reserves."""
    return warning_reserve(item, ctx) == 0 or _is_money_for(item, world, ctx)


class CoreBuildings:
    """Queues again a building of the opening's build that was lost after that build was complete."""

    def __init__(self) -> None:
        # The opening's build is complete once its last building has stood (see `_is_opening_over`); until then a
        # building that is lost is the opening's to build again.
        self._is_opening_complete = False
        self._has_depot_stood = False
        # The replacement ordered in the cycle of this tick: its money is spoken for, though nothing is paid yet.
        self._ordered: tuple[int, str] | None = None

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for the first core building that is missing, or for the power plant it cannot do without."""
        production = ctx.production
        self._is_opening_complete = self._is_opening_complete or _is_opening_over(ctx)
        self._has_depot_stood = self._has_depot_stood or production.standing(SERVICE_DEPOT) > 0
        is_free = production.is_queue_free(BUILDING_QUEUE) and not _is_power_plant_needed(world, ctx)
        if not self._is_opening_complete or not is_free:
            return []
        missing = self._first_missing(world, ctx)
        if missing is None or not _is_money_for(missing, world, ctx):
            return []
        if _can_power(missing, world, ctx):
            self._ordered = (world.tick, missing)
        return _build_within_power(missing, world, ctx)

    def saved_for(self, world: World, ctx: MatchContext) -> str | None:
        """The core building the money is being saved for: one the base lacks, can make and cannot yet pay for.

        A building in its queue is still saved for until it stands, since it is
        paid for while it is made; in the cycle it is ordered in, the money is
        its own whatever there is. Power plants are not saved for.
        """
        if not self._is_opening_complete:
            return None
        if self._ordered is not None and self._ordered[0] == world.tick:
            return self._ordered[1]
        return next((item for item in self._lacking(world, ctx) if not _is_money_for(item, world, ctx)), None)

    def _lacking(self, world: World, ctx: MatchContext) -> list[str]:
        """The core buildings but power plants of which fewer stand than the base is to have, and that can be made."""
        production, offered = ctx.production, world.available_production
        return [
            item
            for item, count in self._wanted(world, ctx).items()
            if item != POWER_PLANT
            and production.standing(item) < count
            and (item in offered or production.pending(item))
        ]

    def _wanted(self, world: World, ctx: MatchContext) -> Counter[str]:
        """How many of each core building the base is to have, in the order the opening first builds them."""
        # Power plants, the barracks, refineries, the war factory; the service depot only when the base has had one.
        # The factory-first build holds one power plant and the depot; every other order rebuilds the barracks-first
        # build's buildings in its order.
        names = FAMILIES[world.family]
        is_factory_first = ctx.tuning.opening.order == FACTORY_FIRST
        wanted = Counter(build_steps(names, FACTORY_FIRST) if is_factory_first else build_steps(names))
        if self._has_depot_stood:
            wanted[SERVICE_DEPOT] = 1
        return wanted

    def _first_missing(self, world: World, ctx: MatchContext) -> str | None:
        """The first building on offer of which fewer stand or are on their way than the base is to have."""
        offered = world.available_production
        wanted = self._wanted(world, ctx)
        return next((item for item, count in wanted.items() if _planned(item, ctx) < count and item in offered), None)


class Defenses:
    """Replaces, on the Defense queue, a defense of the opening that was lost once the opening's build was complete."""

    def __init__(self) -> None:
        self._is_opening_complete = False
        self._has_defense_stood = False
        self._has_gun_stood = False
        # The tick the last replacement defense was ordered at.
        self._replaced_at: int | None = None

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for the first lost defense to replace, or for the power plant it cannot do without."""
        names = FAMILIES[world.family]
        # What stood is noted during a full hold too: a defense that stands and is lost meanwhile is replaced after it.
        self._note_what_stood(names, ctx)
        if ctx.full_hold or not self._is_opening_complete or not self._may_replace(world, ctx):
            return []
        missing = self._first_missing(names.anti_infantry_defense, names.opening_anti_armor_defense, world, ctx)
        if missing is None or not _is_money_for(missing, world, ctx):
            return []
        return self._replace(missing, world, ctx)

    def _note_what_stood(self, names: Family, ctx: MatchContext) -> None:
        """Remember whether the war factory, the defense against infantry and the gun have stood: from then on what
        is lost is to be replaced."""
        production, gun = ctx.production, names.opening_anti_armor_defense
        self._is_opening_complete = self._is_opening_complete or _is_opening_over(ctx)
        self._has_defense_stood = self._has_defense_stood or production.standing(names.anti_infantry_defense) > 0
        self._has_gun_stood = self._has_gun_stood or (gun is not None and production.standing(gun) > 0)

    def _replace(self, item: str, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for a defense, or the power plant that has to come first when there is no power for it."""
        if not defense_cap.allows(item, REPLACE_RULE, world, ctx):
            return []
        if not _can_power(item, world, ctx):
            return power_plant_when_free(world, ctx)
        self._replaced_at = world.tick
        return [ctx.production.build(item)]

    def _may_replace(self, world: World, ctx: MatchContext) -> bool:
        """Whether a replacement may be started now: the queue is free, the base is quiet and the last was long ago.
        Never with `defenses_on_threat_only` on: a lost defense is not routine upkeep then."""
        tuning = ctx.tuning.upkeep
        if tuning.defenses_on_threat_only:
            return False
        wait_ticks = tuning.defense_replace_seconds * TICKS_PER_SECOND
        is_due = self._replaced_at is None or world.tick - self._replaced_at >= wait_ticks
        return is_due and ctx.production.is_queue_free(DEFENSE_QUEUE) and not ctx.threat_active

    def _first_missing(self, defense: str, gun: str | None, world: World, ctx: MatchContext) -> str | None:
        """The defense against infantry when none stands or is on its way, with `defense_on_threat` on only once one
        has stood; else the gun when it stood and is gone."""
        is_defense_kept = self._has_defense_stood or not ctx.tuning.opening.defense_on_threat
        lost = [defense] if is_defense_kept and ctx.production.planned(defense) == 0 else []
        if gun is not None and self._has_gun_stood and ctx.production.planned(gun) == 0:
            lost.append(gun)
        return next((item for item in lost if item in world.available_production), None)


def power_plant_when_free(world: World, ctx: MatchContext) -> list[Intent]:
    """A power plant, when the Building queue is free and none is on its way already."""
    production = ctx.production
    is_power_plant_on_its_way = production.pending(POWER_PLANT) + production.pending(ADVANCED_POWER_PLANT)
    if is_power_plant_on_its_way or not production.is_queue_free(BUILDING_QUEUE):
        return []
    return _power_plant(world, ctx)


def defense_within_power(item: str, world: World, ctx: MatchContext) -> list[Intent]:
    """The order for a defense that leaves the power target over, or else for the power plant it waits for."""
    if not _can_power(item, world, ctx):
        return power_plant_when_free(world, ctx)
    return [ctx.production.build(item)]


def _planned(item: str, ctx: MatchContext) -> int:
    """How many of a building stand or are on their way; an advanced power plant counts as a power plant."""
    kinds = _POWER_PLANTS if item == POWER_PLANT else (item,)
    return sum(ctx.production.planned(kind) for kind in kinds)


class Harvesters:
    """Keeps the harvesters at their target (see `harvester_room`), sends new ones to the field with the most room,
    moves one now and then off a field working over its share, sends back to work the ones that stand idle, parks them
    all while `parking` calls for it, and sends those of a field that has run dry to fresh ore while `fresh_ore`
    does."""

    def __init__(self) -> None:
        # Harvester id -> in how many states in a row it has been seen idle.
        self._idle_states: dict[int, int] = {}
        # Harvester id -> the tick of the last `harvest` order it was sent.
        self._sent_back_at: dict[int, int] = {}
        self._parking = Parking()
        self._fresh_ore = FreshOre()
        self._room = FieldRoom()
        # With `truck_shares_by_mines` on, the field each truck is assigned to and the shares of the active fields,
        # in place of the room.
        self._shares = truck_shares.TruckShares()
        # With `truck_cap_by_shares` on, the free trucks the new refineries still bring.
        self._free_trucks = truck_shares.FreeTrucks()
        # With `harvesters_first` on, whose turn the Vehicle queue's next item is (see `vehicle_turn`).
        self._turn = VehicleTurn()
        self._harvesters: list[Unit] = []

    @property
    def dry_fields(self) -> frozenset[OrePatch]:
        """The fields the move to fresh ore marks dry now; none while it is off."""
        return self._fresh_ore.dry_fields

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """One more harvester when there are too few, the orders that park the harvesters and send them back to work,
        those that send a dry field's harvesters to fresh ore, the one that moves a harvester off a field over its
        share (`FieldRoom.rebalance`), and `harvest` for those that have idled long enough and are neither parked nor
        left to the move to fresh ore."""
        harvesters = [unit for unit in world.units.values() if unit.type == HARVESTER]
        self._harvesters = harvesters
        new = self._room.note_cycle(harvesters, world)
        self._free_trucks.note_cycle(harvesters, world)
        if truck_shares.is_on(ctx):
            new = self._shares.note_cycle(harvesters, world, ctx)
        parked = self._parking.orders(harvesters, world, ctx)
        # A harvester parking has just sent back to work is not told again while it gets going.
        self._sent_back_at.update(dict.fromkeys(parked.released, world.tick))
        moved, left_to_fresh_ore = self._to_fresh_ore(harvesters, parked.held | parked.released, world, ctx)
        self._room.pin(moved, ctx)
        busy = ctx.fleeing_harvesters | parked.held | left_to_fresh_ore
        placed, rebalanced = self._spread(harvesters, new, busy, parked.released, moved, world, ctx)
        sent = frozenset(unit for intent in (*placed, *rebalanced) for unit in intent.order["units"])
        self._sent_back_at.update(dict.fromkeys(sent, world.tick))
        back = self._back_to_work(harvesters, parked.held | left_to_fresh_ore | sent, world, ctx)
        if ctx.tuning.upkeep.harvesters_to_fresh_ore:
            self._fresh_ore.note_reorders((unit for intent in back for unit in intent.order["units"]), world, ctx)
        return [*self._one_more(world, ctx), *parked.intents, *moved, *placed, *rebalanced, *back]

    def _spread(
        self,
        harvesters: list[Unit],
        new: frozenset[int],
        busy: frozenset[int],
        released: frozenset[int],
        moved: list[Intent],
        world: World,
        ctx: MatchContext,
    ) -> tuple[list[Intent], list[Intent]]:
        """The orders that send the new harvesters to a field and move one off a field over its share: by the truck
        shares with `truck_shares_by_mines` on (`TruckShares`), the trucks the move to fresh ore sent assigned to
        their new field, and those of a harvester crush run left alone as well; else by the room (`FieldRoom`)."""
        if not truck_shares.is_on(ctx):
            placed = self._room.send_new(harvesters, new, busy, world, ctx)
            return placed, self._room.rebalance(harvesters, busy | released | new, world, ctx)
        self._shares.pin(moved, ctx)
        placed = self._shares.send_new(harvesters, new, busy, world, ctx)
        kept = busy | released | new | frozenset(ctx.harvester_crush)
        return placed, self._shares.rebalance(harvesters, kept, world, ctx)

    def is_due(self, world: World, ctx: MatchContext) -> bool:
        """Whether one more harvester is due: those standing or on their way are fewer than the target, a refinery and
        a war factory stand, the harvester is on offer and a served field has room for it (`FieldRoom.has_room`)."""
        production = ctx.production
        is_held = ctx.full_hold or _waits_for_scout_vehicle(ctx)
        can_train = production.standing(WAR_FACTORY) > 0 and HARVESTER in world.available_production
        if ctx.tuning.upkeep.truck_cap_by_shares and self._field_shares(world, ctx) is not None:
            return can_train and self._is_short_of_shares(is_held, world, ctx)
        per_refinery = _HELD_HARVESTERS_PER_REFINERY if is_held else ctx.tuning.upkeep.harvesters_per_refinery
        is_below = harvesters_counted(ctx) < harvester_target(per_refinery, ctx)
        return is_below and can_train and self._room.has_room(self._harvesters, world, ctx)

    def _is_short_of_shares(self, is_held: bool, world: World, ctx: MatchContext) -> bool:
        """With `truck_cap_by_shares` on, whether the trucks fall short of the active fields' shares: those standing,
        in production and due free from a new refinery (`FreeTrucks`) are fewer than the shares together,
        `harvesters_max` at the most (one a refinery under a full hold, when that is fewer), and a field, not dry, has
        fewer trucks than its share, counting those assigned to it (or working it, by the room) and its refineries'
        free ones. No field active, none is due."""
        shares, counts = self._field_shares(world, ctx), self._field_counts(world, ctx)
        pending = self._free_trucks.pending(world, ctx)
        cap = truck_shares.cap(shares, ctx)
        if is_held:
            cap = min(cap, _HELD_HARVESTERS_PER_REFINERY * ctx.production.standing(REFINERY))
        is_below = harvesters_counted(ctx) + sum(pending.values()) < cap
        open_fields = [field for field in shares if field not in ctx.dry_fields]
        return is_below and any(counts[field] + pending[field] < shares[field] for field in open_fields)

    def _field_shares(self, world: World, ctx: MatchContext) -> Mapping[OrePatch, int] | None:
        """The trucks each active field takes: its truck share with `truck_shares_by_mines` on; else its share of the
        room (`harvester_room.field_share`) while the room is on; None with both off, when the harvester target
        applies."""
        if truck_shares.is_on(ctx):
            return self._shares.field_shares
        tuning = ctx.tuning.upkeep
        if not tuning.harvester_cells_per:
            return None
        return {field: field_share(field, serving, tuning) for field, serving in serving_counts(world, ctx).items()}

    def _field_counts(self, world: World, ctx: MatchContext) -> Counter[OrePatch]:
        """The trucks each field has: those assigned to it with `truck_shares_by_mines` on; else those working it, as
        the room counts them (its share less its room)."""
        if truck_shares.is_on(ctx):
            return self._shares.assigned_counts()
        rooms = self._room.rooms(self._harvesters, world, ctx)
        shares = self._field_shares(world, ctx)
        return Counter({field: shares[field] - room for field, room in rooms.items()})

    def _to_fresh_ore(
        self, harvesters: list[Unit], parked: frozenset[int], world: World, ctx: MatchContext
    ) -> tuple[list[Intent], frozenset[int]]:
        """With `harvesters_to_fresh_ore` on, the orders that send a dry field's harvesters to fresh ore, but those
        that flee or park; and the harvesters the idle rule leaves alone in this cycle: those sent now and those of a
        dry field that wait by their refinery. Nothing and none with it off."""
        if not ctx.tuning.upkeep.harvesters_to_fresh_ore:
            return [], frozenset()
        if truck_shares.is_on(ctx):
            shares, room = self._shares.field_shares, None
        else:
            shares, room = None, self._room if ctx.tuning.upkeep.harvester_cells_per else None
        moved = self._fresh_ore.orders(harvesters, ctx.fleeing_harvesters | parked, world, ctx, room, shares)
        sent = frozenset(unit for intent in moved for unit in intent.order["units"])
        # A harvester sent to fresh ore is not told to harvest again while it drives there.
        self._sent_back_at.update(dict.fromkeys(sent, world.tick))
        return moved, sent | self._fresh_ore.waiting(harvesters, world, ctx)

    def holds_combat(self, world: World, ctx: MatchContext) -> bool:
        """With `harvesters_first` on, whether the unit queues keep combat vehicles out of the Vehicle queue now: a
        harvester `is_due` and it is the harvester's turn (see `vehicle_turn`)."""
        is_due = ctx.tuning.upkeep.harvesters_first and self.is_due(world, ctx)
        return is_due and self._turn.is_harvester_turn(world, ctx)

    def note_combat(self) -> None:
        """Note a combat vehicle the unit queues queued: under the alternation, a harvester is next."""
        self._turn.note_combat()

    def after_combat(self, world: World, ctx: MatchContext) -> list[Intent]:
        """With `harvesters_first` on, the harvester whose turn a combat vehicle had, when the Vehicle queue still
        stands free after the unit queues and one is due and none is on its way; none else."""
        if not ctx.tuning.upkeep.harvesters_first or not ctx.production.is_queue_free(VEHICLE_QUEUE):
            return []
        return self._trained_if_due(world, ctx)

    def _one_more(self, world: World, ctx: MatchContext) -> list[Intent]:
        """One more harvester while one `is_due`, none is on its way and, with `harvesters_first` on, it is the
        harvester's turn (see `vehicle_turn`); while a full hold is in force the target is one a refinery."""
        if ctx.tuning.upkeep.harvesters_first and not self._turn.is_harvester_turn(world, ctx):
            return []
        return self._trained_if_due(world, ctx)

    def _trained_if_due(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for one more harvester, noted as the Vehicle queue's last, while one `is_due` and none is on its
        way; none else."""
        if ctx.production.pending(HARVESTER) or not self.is_due(world, ctx):
            return []
        self._turn.note_harvester()
        return [ctx.production.train(HARVESTER)]

    def _back_to_work(
        self, harvesters: list[Unit], held: frozenset[int], world: World, ctx: MatchContext
    ) -> list[Intent]:
        """`harvest` for the harvesters that have idled long enough, but those fleeing and those `held` where they
        park."""
        seen_idle = self._idle_states
        self._idle_states = {unit.id: seen_idle.get(unit.id, 0) + 1 if unit.idle else 0 for unit in harvesters}
        to_send = [
            unit.id
            for unit in harvesters
            if unit.id not in ctx.fleeing_harvesters
            and unit.id not in held
            and self._is_due(unit.id, world.tick, ctx.tuning.upkeep)
        ]
        if not to_send:
            return []
        self._sent_back_at.update(dict.fromkeys(to_send, world.tick))
        # Without a cell the harvester goes back to harvesting wherever it finds ore; one that works a field of its
        # truck share is sent to that field's ore.
        return truck_shares.harvest_orders(to_send, world, ctx, Priority.PRODUCTION)

    def _is_due(self, harvester_id: int, tick: int, tuning: UpkeepTuning) -> bool:
        """Whether a harvester has idled long enough to be sent back, and was not sent back a moment ago."""
        sent = self._sent_back_at.get(harvester_id)
        has_idled = self._idle_states[harvester_id] >= tuning.harvester_idle_states
        return has_idled and (sent is None or tick - sent >= tuning.harvest_reorder_ticks)


class ProducerScaling:
    """Orders one more war factory while the ore is near the storage cap; else one more barracks, or else one more war
    factory, when income and busy queues call for it; else what the money banked calls for (see `bank_spending`).

    See the module's note. The bank is followed in every state, so the rule
    is asked during a full hold for the second yard's MCV too, and orders
    nothing then. Every war factory it would order, by whichever of its
    rules, is put to the load gate last (`LoadGate.allows`).
    """

    def __init__(self, core: CoreBuildings, refineries: MoreRefineries, storage: StorageWatch) -> None:
        self._is_opening_complete = False
        # The rules that rebuild the core of the base and add refineries, which say when money is saved for them.
        self._core = core
        self._refineries = refineries
        # The money banked, followed from state to state.
        self._bank = BankWatch()
        # Whether the ore is near the storage cap, which upkeep notes at the start of every cycle.
        self._storage = storage
        # Whether the production load justifies one more war factory, and when a refusal was last written.
        self._load = LoadGate()

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for one more war factory while the ore is near the storage cap (`_is_storage_factory_due`), else
        for the producer that is due, or for the power plant that has to come before either; with none due, the order
        the money banked calls for (`_on_bank`). None while a full hold is in force."""
        self._is_opening_complete = self._is_opening_complete or _is_opening_over(ctx)
        self._bank.note(world, ctx.tuning)
        if ctx.full_hold:
            return []
        floating = self._on_float(world, ctx)
        if floating or not self._may_add(world, ctx):
            return floating
        if self._is_storage_factory_due(world, ctx) and self._load.allows(STORAGE_NEAR_CAP, world, ctx):
            return self._storage.orders(_build_within_power(WAR_FACTORY, world, ctx), world)
        producer = self._due(world, ctx)
        if producer is not None:
            return _producer_orders(producer, world, ctx)
        return self._on_bank(world, ctx)

    def _on_float(self, world: World, ctx: MatchContext) -> list[Intent]:
        """With no_float on, the producer floating cash calls for (see `bank_spending`), or the power plant it waits
        for: once the money has floated, a refinery stands, the plan is past OPENING, no lost core building is saved
        for, the Building queue is free and no producer is on its way; whatever the threat, the focus or the
        recovery."""
        production = ctx.production
        if not self._bank.is_floating(world, ctx.tuning) or ctx.plan == OPENING or not production.standing(REFINERY):
            return []
        is_producer_on_its_way = any(production.pending(item) for item in _PRODUCERS)
        if is_producer_on_its_way or not production.is_queue_free(BUILDING_QUEUE):
            return []
        producer = float_producer(world, ctx)
        if producer is None or self._core.saved_for(world, ctx) is not None:
            return []
        if is_new_yard_claim(ctx) and not production.pending(REFINERY):
            # The money floats toward the new yard's refinery, which comes first.
            return []
        if ctx.tuning.placement.refinery_small_fields and self._refineries.has_unserved_place(world, ctx):
            # And toward a refinery for an unserved field our base reaches.
            return []
        return self._bank.float_orders(_build_within_power(producer, world, ctx), world)

    def _is_storage_factory_due(self, world: World, ctx: MatchContext) -> bool:
        """Whether one more war factory is due on the ore near the storage cap: one is on offer, fewer than
        `storage_war_factories_max` stand or are on their way, and the storage rule may order one
        (`StorageWatch.may_order_factory`). That most, not `max_war_factories`, is the ceiling here."""
        is_offered = WAR_FACTORY in world.available_production
        has_room = ctx.production.planned(WAR_FACTORY) < ctx.tuning.producers.storage_war_factories_max
        is_clear = _clears_warning_reserve(WAR_FACTORY, world, ctx)
        return is_offered and has_room and is_clear and self._storage.may_order_factory(world, ctx.tuning)

    def _on_bank(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The second war factory, or the power plant it waits for, once the money has stayed banked with exactly one
        war factory standing (`_is_bank_factory_due`); else, once the Building queue has stood empty with a big bank
        (`BankWatch.is_idle_with_bank`), the order for it (`_for_idle_queue`); none else."""
        if self._is_bank_factory_due(world, ctx) and self._load.allows(BANK_FACTORY, world, ctx):
            return log_orders(BANK_FACTORY, _build_within_power(WAR_FACTORY, world, ctx), world)
        if not self._bank.is_idle_with_bank(world, ctx.tuning):
            return []
        return self._bank.idle_orders(self._for_idle_queue(world, ctx), world)

    def _is_bank_factory_due(self, world: World, ctx: MatchContext) -> bool:
        """Whether the second war factory is due on the bank: exactly one stands, there is room for one more and the
        money has stayed banked (`BankWatch.has_banked`)."""
        is_one = ctx.production.standing(WAR_FACTORY) == 1
        has_room = _has_war_factory_room(world, ctx) and _clears_warning_reserve(WAR_FACTORY, world, ctx)
        return is_one and has_room and self._bank.has_banked(world, ctx.tuning)

    def _for_idle_queue(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for an empty Building queue with a big bank, the first that is allowed: a war factory while there
        is room for one and the load gate lets it be ordered, or the power plant it waits for; a refinery for an
        untaken field (`MoreRefineries.for_untaken_field`); a power plant the power target calls for. Never a defense.

        Power plants normally take the queue first (`PowerPlants`); the last
        step keeps the order of preference whole for a queue no other rule
        has given anything.
        """
        is_clear = _clears_warning_reserve(WAR_FACTORY, world, ctx)
        if _has_war_factory_room(world, ctx) and is_clear and self._load.allows(IDLE_BANK, world, ctx):
            return _build_within_power(WAR_FACTORY, world, ctx)
        refinery = self._refineries.for_untaken_field(world, ctx)
        if refinery:
            return refinery
        return _power_plant(world, ctx) if _is_power_plant_needed(world, ctx) else []

    def _due(self, world: World, ctx: MatchContext) -> str | None:
        """The producer due: one more barracks, or else one more war factory.

        With `tank_factories` on, the war factory is asked first, and it waits
        while the money is saved for the third refinery: a war factory costs
        more than a barracks and would put the refinery off longest.
        """
        barracks = FAMILIES[world.family].barracks
        rule = _war_factory_rule(ctx)
        if not ctx.tuning.producers.tank_factories:
            if _is_barracks_due(barracks, world, ctx):
                return barracks
            is_due = _is_war_factory_due(world, ctx) and self._load.allows(rule, world, ctx)
            return WAR_FACTORY if is_due else None
        is_due = _is_war_factory_due(world, ctx) and not self._refineries.is_saving(world, ctx)
        if is_due and self._load.allows(rule, world, ctx):
            return WAR_FACTORY
        return barracks if _is_barracks_due(barracks, world, ctx) else None

    def _may_add(self, world: World, ctx: MatchContext) -> bool:
        """Whether a producer may be added: past the opening, no threat, no lost core building saved for, the
        Building queue free and no producer on its way.

        A lost core building that the money does not cover yet leaves the
        Building queue free; a producer ordered then would spend what is saved
        for it and put it off.
        """
        production = ctx.production
        is_producer_on_its_way = any(production.pending(item) for item in _PRODUCERS)
        is_quiet = self._is_opening_complete and ctx.plan != OPENING and not ctx.threat_active
        is_saving = self._core.saved_for(world, ctx) is not None
        return is_quiet and not is_saving and not is_producer_on_its_way and production.is_queue_free(BUILDING_QUEUE)


def float_producer(world: World, ctx: MatchContext) -> str | None:
    """The producer floating cash buys: a barracks or a war factory, the one whose unit queue was the busier (the
    barracks on a tie), up to max_infantry_producers and max_war_factories, on offer; the other when that one is at its
    most; None when both are."""
    tuning, production, income = ctx.tuning.producers, ctx.production, ctx.income
    barracks = FAMILIES[world.family].barracks
    planned_barracks = sum(production.planned(item) for item in BARRACKS_TYPES)
    can_barracks = barracks in world.available_production and planned_barracks < tuning.max_infantry_producers
    can_factory = _has_war_factory_room(world, ctx)
    is_vehicle_busier = income.utilization(VEHICLE_QUEUE) > income.utilization(INFANTRY_QUEUE)
    ranked = [(WAR_FACTORY, can_factory), (barracks, can_barracks)]
    for item, can in ranked if is_vehicle_busier else ranked[::-1]:
        if can:
            return item
    return None


def _producer_orders(producer: str, world: World, ctx: MatchContext) -> list[Intent]:
    """The order for the producer due, or the power plant it waits for (`_build_within_power`); with the load gate the
    trigger (`war_factory_load.is_on_load`), one `economy` event of the reason `war_factory_on_load` for each order
    made for a war factory."""
    orders = _build_within_power(producer, world, ctx)
    return log_orders(ON_LOAD, orders, world) if producer == WAR_FACTORY and is_on_load(ctx) else orders


def _war_factory_rule(ctx: MatchContext) -> str:
    """The rule the producers' own war factory is put to the load gate as: the gate itself while it is the trigger,
    else `tank_factories`'s gates or the producer gates."""
    if is_on_load(ctx):
        return ON_LOAD
    return TANK_FACTORY_GATES if ctx.tuning.producers.tank_factories else PRODUCER_GATES


def _is_barracks_due(barracks: str, world: World, ctx: MatchContext) -> bool:
    """Whether one more barracks is due: below the most, in time, its queue busy, an investment, and the income; never
    while `no_army_infantry` has the army train no infantry but a few rocket soldiers."""
    tuning, production = ctx.tuning.producers, ctx.production
    standing = sum(production.standing(item) for item in BARRACKS_TYPES)
    is_in_time = world.game_seconds >= tuning.barracks_from_seconds
    is_busy = ctx.income.utilization(INFANTRY_QUEUE) >= tuning.busy_share
    if _is_army_infantry_off(ctx) or not (0 < standing < tuning.max_infantry_producers and is_in_time and is_busy):
        return False
    return _is_investment(barracks, world, ctx) and _has_barracks_income(standing + 1, ctx)


def _has_barracks_income(number: int, ctx: MatchContext) -> bool:
    """Whether the income calls for the barracks of a number (the 2nd to the 7th), or money that piled up will do."""
    tuning, income = ctx.tuning.producers, ctx.income
    if _is_at_least(income.income_per_s, tuning.barracks_income[number - _FIRST_EXTRA]):
        return True
    if number in _EARLY_FLOAT_BARRACKS:
        return income.has_held_above(tuning.barracks_float, tuning.float_seconds)
    is_vehicle_queue_busy = income.utilization(VEHICLE_QUEUE) >= tuning.busy_share
    is_late_float = income.has_held_above(tuning.late_barracks_float, tuning.float_seconds)
    return number in _LATE_FLOAT_BARRACKS and is_vehicle_queue_busy and is_late_float


def _is_war_factory_due(world: World, ctx: MatchContext) -> bool:
    """Whether one more war factory is due: its gates but the money (`_is_war_factory_wanted`), and the money."""
    return _is_war_factory_wanted(world, ctx) and _has_war_factory_money(world, ctx)


def _is_war_factory_wanted(world: World, ctx: MatchContext) -> bool:
    """Whether one more war factory is wanted, the money aside: below the most, in time, its queue busy, and the
    income for it; with the load gate the trigger (`war_factory_load.is_on_load`), its own terms instead
    (`war_factory_load.is_on_load_wanted`), the load put to the gate after."""
    if is_on_load(ctx):
        return is_on_load_wanted(world, ctx)
    tuning, income = ctx.tuning.producers, ctx.income
    standing = ctx.production.standing(WAR_FACTORY)
    gates = _war_factory_gates(tuning)
    is_busy = income.utilization(VEHICLE_QUEUE) >= gates.busy_share
    is_in_time = world.game_seconds >= gates.from_seconds
    if not (0 < standing < tuning.max_war_factories and is_busy and is_in_time):
        return False
    return _is_at_least(income.income_per_s, gates.income[standing + 1 - _FIRST_EXTRA])


def _has_war_factory_room(world: World, ctx: MatchContext) -> bool:
    """Whether one more war factory may be ordered, its gates aside: one is on offer, which its prerequisites decide,
    and fewer than `max_war_factories` stand or are on their way."""
    is_offered = WAR_FACTORY in world.available_production
    return is_offered and ctx.production.planned(WAR_FACTORY) < ctx.tuning.producers.max_war_factories


def _has_war_factory_money(world: World, ctx: MatchContext) -> bool:
    """Whether the money is there for one more war factory: the investment test, and with `economy_first` on the money
    covering its price and the production reserve as well; with the load gate the trigger, the money above the reserve
    with `float_seconds` of the income (`war_factory_load.has_money_within`).

    At the income a war factory waits for, the investment test passes with
    hardly any cash, and a war factory ordered then is built from what the
    unit queues leave over. With the economy first the unit queues wait for
    it instead (see `UnitQueues`), until the money is there.
    """
    if is_on_load(ctx):
        return has_money_within(world, ctx) and _clears_warning_reserve(WAR_FACTORY, world, ctx)
    is_investment = _is_investment(WAR_FACTORY, world, ctx)
    return is_investment and (not ctx.tuning.upkeep.economy_first or _is_money_for(WAR_FACTORY, world, ctx))


class _WarFactoryGates(NamedTuple):
    """What one more war factory waits for: its queue busy this share of the last minute, the income a second for the
    2nd to the 4th, and the game time."""

    busy_share: float
    income: tuple[int, ...]
    from_seconds: int


def _war_factory_gates(tuning: ProducersTuning) -> _WarFactoryGates:
    """The gates of one more war factory: with `tank_factories` on its own, which open sooner; else the busy share
    the barracks share and `factories_income`, from the start of the match."""
    if tuning.tank_factories:
        return _WarFactoryGates(
            tuning.tank_factories_busy_share, tuning.tank_factories_income, tuning.tank_factories_from_seconds
        )
    return _WarFactoryGates(tuning.busy_share, tuning.factories_income, 0)


def _is_investment(item: str, world: World, ctx: MatchContext) -> bool:
    """Whether a building is on offer and the investment test passes for its price and its build time; for an optional
    building while the rush watch warns, the money covers it with both reserves as well."""
    if item not in world.available_production:
        return False
    is_investment = ctx.income.can_invest(ctx.production.cost(item), world.items[item].build_time_s)
    return is_investment and _clears_warning_reserve(item, world, ctx)


def _is_at_least(income_per_s: float | None, gate: int) -> bool:
    """Whether a known income reaches a gate; an unknown one reaches none."""
    return income_per_s is not None and income_per_s >= gate


class UnitQueues:
    """Feeds the Infantry and the Vehicle queue, each as soon as it is free, from one budget; with `tank_factories`
    on, the Vehicle queue as long as it has room. See the module's note."""

    def __init__(
        self,
        core: CoreBuildings,
        refineries: MoreRefineries,
        depot: EarlyDepot,
        full_hold: FullHold,
        storage: StorageWatch,
        harvesters: Harvesters,
    ) -> None:
        # The rules that rebuild the core of the base, add refineries and rush the service depot, which say when money
        # is saved for them.
        self._core = core
        self._depot = depot
        # The harvester rule, which says when a harvester is due and, with `harvesters_first` on, whose turn the Vehicle
        # queue's next item is (see `vehicle_turn`).
        self._harvesters = harvesters
        self._refineries = refineries
        self._fill = VehicleFill()
        # With `economy_first` on, the holds that keep the money for the economy's next item.
        self._hold = EconomyHold()
        # With `mcv_first` on, the full hold for the second yard's MCV, which these holds give way to.
        self._full_hold = full_hold
        # Whether the ore is near the storage cap, which lifts these holds; upkeep notes it at the start of every cycle.
        self._storage = storage
        # With `counter_quota` on, the counter units filled first on each unit queue (see `counter_units`).
        self._counters = CounterFill()

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The training orders for the next item of each unit queue that has room and is not claimed; then, with the
        Vehicle queue still free, the harvester a combat vehicle's turn held back (`Harvesters.after_combat`)."""
        # While the canary alarm is in force the money goes to defenses: no unit is trained here (see `canary_alarm`),
        # but once the defense cap is full, when it goes to the army (see `defense_cap`).
        is_held = ctx.canary_alarm and not defense_cap.is_full(world, ctx)
        orders = [] if is_held else self._unit_orders(world, ctx)
        return [*orders, *self._harvesters.after_combat(world, ctx)]

    def _unit_orders(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The training orders for the next item of each unit queue that has room and is not claimed."""
        is_saving = self._core.saved_for(world, ctx) is not None or self._refineries.is_saving(world, ctx)
        is_saving = is_saving or self._depot.is_saving(world, ctx)
        if ctx.plan == OPENING:
            return self._counters.on_approach(world, ctx, UpkeepManager.name)
        # The hold is looked after in every cycle, so that it ends on time whatever else keeps the queues waiting.
        is_held = self._is_held_for_economy(world, ctx)
        if is_saving or is_held or ctx.full_hold:
            # An enemy army marching on the base is answered before what the money was held for (see `counter_units`).
            return self._counters.on_approach(world, ctx, UpkeepManager.name)
        made = _unit_production(world, ctx, self._counters)
        vehicle_room = self._fill.room(world, ctx)
        wanted = self._wanted_after_harvester(made.composition, vehicle_room, world, ctx)
        budget = _unit_budget(world, ctx) - self._refineries.growth_set_aside(world, ctx)
        if len(wanted) == len(_UNIT_QUEUES) and sum(ctx.production.cost(item) for item in wanted.values()) > budget:
            first = _first_to_feed(made, wanted[INFANTRY_QUEUE], budget, ctx)
            wanted = {first: wanted[first]}
        counts = _paid_for(wanted, budget, vehicle_room, made.infantry_most, ctx)
        orders = _train(wanted, counts, ctx)
        if counts.get(VEHICLE_QUEUE):
            self._fill.note_top_up(world, ctx)
            self._harvesters.note_combat()
        return orders

    def _wanted_after_harvester(
        self, produces: Composition, vehicle_room: int, world: World, ctx: MatchContext
    ) -> dict[str, str]:
        """The next item of each unit queue that has room (`_wanted`); with `harvesters_first` on, none for the Vehicle
        queue while a due harvester has its turn (`Harvesters.holds_combat`): that queue builds in order, so a combat
        vehicle queued now would come out before the next harvester."""
        wanted = _wanted(produces, vehicle_room, world, ctx)
        if self._harvesters.holds_combat(world, ctx):
            wanted.pop(VEHICLE_QUEUE, None)
        return wanted

    def _is_held_for_economy(self, world: World, ctx: MatchContext) -> bool:
        """With `economy_first` on, whether the unit queues wait in this cycle for the economy's next item (see
        `economy_hold`); never with it off.

        Only these orders wait: what the rules before this one, or other
        managers on queues they claim, order in the cycle is never held.
        While a full hold is in force or released (`mcv_first`), these holds
        give way to it: none goes on and none starts. While the ore is near
        the storage cap (`StorageWatch`), they are lifted the same way, since
        what comes in then is lost rather than gathered.
        """
        if not ctx.tuning.upkeep.economy_first:
            return False
        if self._full_hold.is_engaged(world.tick, ctx.tuning.upkeep):
            self._hold.give_way(world.tick)
            return False
        if self._storage.is_in_force:
            self._hold.give_way(world.tick, STORAGE_NEAR_CAP)
            return False
        production = ctx.production

        def is_on_its_way(item: str) -> bool:
            return production.pending(item) > 0 or production.units(item) > 0

        has_place = self._refineries.has_place(world, ctx)
        due = _economy_items_due(world, ctx, self._refineries.is_growth_due, has_place)
        # A hold for the new yard's refinery lasts until it is ordered: at a low income 20 s never covered it.
        untimed = frozenset({REFINERY}) if is_new_yard_claim(ctx) and has_place else frozenset()
        return self._hold.holds(due, is_on_its_way, world.tick, ctx.tuning.upkeep, untimed=untimed)


def _economy_items_due(
    world: World, ctx: MatchContext, is_growth_due: bool, has_refinery_place: bool
) -> list[EconomyItem]:
    """The economy's items that are due and not on their way, the first first: the refinery a claim calls for, a
    growth refinery when one `is_growth_due`, one more war factory, and the second yard's MCV; each with whether the
    money covers it now, by the test of the rule that orders it. Neither refinery is an item without a place for it
    (`has_refinery_place`): the unit queues would wait for a refinery that cannot be ordered."""
    is_growth_item = is_growth_due and has_refinery_place
    growth = EconomyItem(REFINERY, _is_money_for(REFINERY, world, ctx)) if is_growth_item else None
    items = (
        _claim_refinery_due(world, ctx, has_refinery_place),
        growth,
        _war_factory_due_but_money(world, ctx),
        _mcv_due_but_money(world, ctx),
    )
    return [item for item in items if item is not None]


def _claim_refinery_due(world: World, ctx: MatchContext, has_place: bool) -> EconomyItem | None:
    """The refinery a refinery claim calls for, while one is due, it `has_place` to go, no refinery is on its way and
    the spending focus is not military."""
    production = ctx.production
    # The new yard's refinery is the expansion's own, not a new investment: the military focus holds it back no more.
    is_due = ctx.refinery_claim_due and has_place and (not is_military(ctx) or is_new_yard_claim(ctx))
    if not is_due or REFINERY not in world.available_production or production.pending(REFINERY):
        return None
    return EconomyItem(REFINERY, _is_money_for(REFINERY, world, ctx))


def is_new_yard_claim(ctx: MatchContext) -> bool:
    """Whether the refinery a new yard claims comes first (`new_yard_refinery_first` of [expansion]): a claim that
    lasts until its refinery stands is in force."""
    tuning = ctx.tuning.expansion
    return tuning.new_yard_refinery_first and new_yard_refinery.is_on(ctx) and ctx.new_yard_field is not None


def _war_factory_due_but_money(world: World, ctx: MatchContext) -> EconomyItem | None:
    """One more war factory, while its gates but the money and the load gate (`war_factory_load.is_loaded`) are met,
    it is on offer, the base is quiet and no war factory is on its way; the money covers it when it covers its price
    and the production reserve."""
    is_wanted = _is_war_factory_wanted(world, ctx) and WAR_FACTORY in world.available_production and is_loaded(ctx)
    if not is_wanted or ctx.threat_active or ctx.production.pending(WAR_FACTORY):
        return None
    return EconomyItem(WAR_FACTORY, _is_money_for(WAR_FACTORY, world, ctx))


def _mcv_due_but_money(world: World, ctx: MatchContext) -> EconomyItem | None:
    """With `second_yard` on, the second yard's MCV while its gates but the money and the Vehicle queue's room are
    met; never with `mcv_first` on, when the full hold waits for it instead."""
    if not ctx.tuning.expansion.second_yard or is_mcv_first(ctx) or not is_mcv_wanted(world, ctx):
        return None
    return EconomyItem(MCV, has_mcv_money(world, ctx))


class _UnitProduction(NamedTuple):
    """What the unit queues make in a cycle: the items of each with their shares, the most infantry one order holds,
    and whether the Vehicle queue goes first when the budget pays for one queue only."""

    composition: Composition
    infantry_most: int
    vehicles_first: bool


def _unit_production(world: World, ctx: MatchContext, counters: CounterFill) -> _UnitProduction:
    """What the unit queues make under the plan in force (`_mix_production`), with the counter quota's shortfall first
    on each queue the plan feeds (see `counter_units`): a counter infantry item as many at a time as the army is short
    of, `infantry_at_a_time` at the most."""
    produces = ctx.composition(ctx.plan, world.family, world)
    made = _mix_production(produces, world, ctx)
    first = counters.first(produces, made.composition, world, ctx, _holds_army_infantry(produces, ctx))
    # The military focus's artillery goes ahead of the counter quota's vehicle as of the mix's (see `spending_focus`).
    composition = artillery_first(first.composition, world, ctx)
    if first.infantry_short is None:
        return made._replace(composition=composition)
    most = min(ctx.tuning.upkeep.infantry_at_a_time, first.infantry_short)
    return made._replace(composition=composition, infantry_most=most)


def _mix_production(produces: Composition, world: World, ctx: MatchContext) -> _UnitProduction:
    """What the unit queues make of the plan's composition `produces`: infantry `infantry_at_a_time` at a time, and
    the queue behind on spending first.

    While `no_army_infantry` holds the army's infantry back, the Infantry
    queue makes no rifles: rocket soldiers up to `rockets_kept` in main, the
    guard and the escort, and then the rocket soldiers and flamethrowers the
    mix gives a share (see `_held_infantry`); the Vehicle queue then goes
    first, so that the vehicles never have less of the budget than without the
    switch.
    """
    at_a_time = ctx.tuning.upkeep.infantry_at_a_time
    if not _holds_army_infantry(produces, ctx):
        return _UnitProduction(produces, at_a_time, vehicles_first=False)
    infantry, short = _held_infantry(produces, world, ctx)
    held = Composition(infantry=infantry, vehicle=produces.vehicle)
    return _UnitProduction(held, min(at_a_time, short), vehicles_first=True)


def _held_infantry(produces: Composition, world: World, ctx: MatchContext) -> tuple[dict[str, float], int]:
    """What the Infantry queue makes while `no_army_infantry` holds the army's infantry back, and how many it is short
    of: the rocket soldiers `rockets_kept` asks for first, then of the mix's rocket soldiers and the flamethrowers of
    the Soviet infantry counter, which are exempt, the one the army is furthest short of its share of; nothing once it
    has them all."""
    rockets = max(0, ctx.tuning.upkeep.rockets_kept - _rockets_kept(world, ctx))
    if rockets:
        return {ROCKET_INFANTRY: _WHOLE_SHARE}, rockets
    army = _mix_army_value(produces, world, ctx)
    short = {item: _short_of_share(item, produces, army, world) for item in _HELD_EXEMPT}
    item = max(short, key=short.__getitem__)
    return ({item: _WHOLE_SHARE}, short[item]) if short[item] else ({}, 0)


def _mix_army_value(produces: Composition, world: World, ctx: MatchContext) -> Counter[str]:
    """What the own fighting units of the army mix are worth, by type: those of an item the row in force or the plan's
    composition gives a share. The raid's vehicles and the opening's rifles have no share of the row, and counted they
    would have the mix ask for more rocket soldiers than its share of the tanks."""
    row = ctx.army_mix.points(world.family, world)
    items = {item for points in row.values() for item, share in points.items() if share > 0}
    items |= {*produces.infantry, *produces.vehicle}
    army = _army_value(INFANTRY_QUEUE, world) + _army_value(VEHICLE_QUEUE, world)
    return Counter({item: value for item, value in army.items() if item in items})


def _short_of_share(item: str, produces: Composition, army: Counter[str], world: World) -> int:
    """How many of an infantry item the own army, worth `army` by type, is short of the share of its value the mix
    gives the item; 0 while the mix gives it none, as it does an item not on offer."""
    share, facts = produces.infantry.get(item, 0.0), world.items.get(item)
    if not share or facts is None:
        return 0
    return max(0, math.ceil((share * sum(army.values()) - army[item]) / facts.cost))


def _holds_army_infantry(produces: Composition, ctx: MatchContext) -> bool:
    """Whether `no_army_infantry` holds the army's infantry back in this cycle: it is in force, and the Infantry queue
    makes the army's mix, not the raid quota's item, which is the raid's."""
    return _is_army_infantry_off(ctx) and not produces.raid_infantry


def _is_army_infantry_off(ctx: MatchContext) -> bool:
    """Whether `no_army_infantry` is in force: the switch is on and an own war factory stands."""
    return ctx.tuning.upkeep.no_army_infantry and ctx.production.standing(WAR_FACTORY) > 0


def _rockets_kept(world: World, ctx: MatchContext) -> int:
    """How many own rocket soldiers, a variant as its type, main, the guard and the escort hold."""
    return sum(
        1
        for unit in world.combat_units.values()
        if ctx.groups.group_of(unit.id) in _ROCKET_GROUPS and _type_name(unit.type, world) == ROCKET_INFANTRY
    )


def _type_name(unit_type: str, world: World) -> str | None:
    """The type the item table knows a unit type as, a variant as its type; None for one it does not know."""
    facts = world.items.get(unit_type)
    return None if facts is None else facts.name


def _wanted(produces: Composition, vehicle_room: int, world: World, ctx: MatchContext) -> dict[str, str]:
    """The next item of each unit queue that has room: the Infantry queue while it is free, the Vehicle queue while
    `vehicle_room` is one or more."""
    room = {INFANTRY_QUEUE: int(ctx.production.is_queue_free(INFANTRY_QUEUE)), VEHICLE_QUEUE: vehicle_room}
    mixes = {INFANTRY_QUEUE: produces.infantry, VEHICLE_QUEUE: produces.vehicle}
    items = {queue: _next_item(queue, mixes[queue], world, ctx) for queue in _UNIT_QUEUES if room[queue] > 0}
    return {queue: item for queue, item in items.items() if item is not None}


def _next_item(queue: str, mix: Mapping[str, float], world: World, ctx: MatchContext) -> str | None:
    """The item on offer of a queue's mix that the army is furthest short of; the cheaper one on a tie.

    How far short an item is: its share of the queue's items on offer, less
    its share of what the units the queue makes are worth. None when another
    manager claims the queue, or nothing of its mix with a share is on offer.
    """
    production = ctx.production
    if ctx.claims.is_held_by_other(queue, UpkeepManager.name):
        return None
    offered = {item: share for item, share in mix.items() if share > 0 and item in world.available_production}
    if not offered:
        return None
    # The queue's shares are of the whole army; the queue's own units are set against them as shares among themselves.
    queue_share = sum(offered.values())
    army = _army_value(queue, world)
    worth = sum(army.values())

    def shortfall(item: str) -> tuple[float, int]:
        return offered[item] / queue_share - (army[item] / worth if worth else 0.0), -production.cost(item)

    return max(offered, key=shortfall)


def _army_value(queue: str, world: World) -> Counter[str]:
    """What the own fighting units a queue makes are worth, by type."""
    value: Counter[str] = Counter()
    for unit in world.combat_units.values():
        facts = world.items.get(unit.type)
        if facts is not None and facts.queue == queue:
            value[facts.name] += facts.cost
    return value


def _unit_budget(world: World, ctx: MatchContext) -> int:
    """The money the unit queues may spend: the money, less the production reserve and what this cycle set aside.

    Set aside is what the cycle's orders for power plants, for the base's
    emergency, for new barracks and war factories and for the second yard's
    MCV will cost. In the last
    minutes of a match no building is saved for, and the reserve is waived. While the endgame lead is kept, the price
    of its next defense is left over as well (`clock.lead_reserve`).
    """
    production = ctx.production
    reserve = 0 if clock.is_spending(world, ctx) else ctx.tuning.upkeep.production_cash_reserve
    set_aside = sum(
        production.cost(proposal.item) * proposal.count
        for proposal in production.proposed()
        if proposal.item in _SET_ASIDE or proposal.priority == Priority.EMERGENCY
    )
    return world.economy.spendable - reserve - set_aside - clock.lead_reserve(world, ctx)


def _first_to_feed(made: _UnitProduction, infantry: str, budget: int, ctx: MatchContext) -> str:
    """The unit queue fed when the budget pays for the next item of one only: the Vehicle queue when it goes first;
    else the one behind on spending, or while vehicles keep a floor of the spending (`keeps_vehicle_floor`), the
    infantry only when the floor holds with its order paid."""
    vehicle_share = made.composition.vehicle_share
    if made.vehicles_first:
        return VEHICLE_QUEUE
    if not keeps_vehicle_floor(ctx):
        return _behind_on_spending(vehicle_share, ctx)
    infantry_spend = ctx.production.cost(infantry) * _infantry_count(infantry, budget, made.infantry_most, ctx)
    return _keeping_vehicle_floor(vehicle_share, infantry_spend, ctx)


def _keeping_vehicle_floor(vehicle_share: float, infantry_spend: int, ctx: MatchContext) -> str:
    """The Infantry queue when the vehicles' share of the unit spending of the last minute, with `infantry_spend`
    spent on infantry besides, is at least the larger of the mix's `vehicle_share` and `VEHICLE_FLOOR_SHARE`; else,
    and while nothing has been spent on units, the Vehicle queue."""
    spent = ctx.income.spend_by_queue()
    share_after = army_vehicle_share({**spent, INFANTRY_QUEUE: spent[INFANTRY_QUEUE] + infantry_spend})
    is_kept = share_after is not None and share_after >= max(vehicle_share, VEHICLE_FLOOR_SHARE)
    return INFANTRY_QUEUE if is_kept else VEHICLE_QUEUE


def _behind_on_spending(vehicle_target: float, ctx: MatchContext) -> str:
    """The unit queue furthest below its share of the unit spending of the last minute; the Vehicle queue on a tie.

    The Vehicle queue's share is `vehicle_target`, the vehicles' share of the
    plan's mix; the Infantry queue's is the rest. It is the Vehicle queue too
    while nothing has been spent on units.
    """
    share = army_vehicle_share(ctx.income.spend_by_queue())
    if share is None:
        return VEHICLE_QUEUE
    return INFANTRY_QUEUE if share > vehicle_target else VEHICLE_QUEUE


def army_vehicle_share(spent: Mapping[str, float]) -> float | None:
    """The vehicles' share of what the Infantry and the Vehicle queue spent on the army; None while nothing was.

    Only the army counts: the harvesters and MCVs the Vehicle queue makes are
    spending of their own (`economy`), and counted as vehicles they would make
    the tanks look ahead of their share when they are behind it.
    """
    total = spent[INFANTRY_QUEUE] + spent[VEHICLE_QUEUE]
    return spent[VEHICLE_QUEUE] / total if total > 0 else None


def _paid_for(
    wanted: dict[str, str], budget: int, vehicle_room: int, infantry_most: int, ctx: MatchContext
) -> dict[str, int]:
    """How many of each queue's item wanted the budget pays for: a vehicle first, then infantry, then more vehicles.

    Infantry goes a few at a time, `infantry_most` at the most, as many as the
    money left after the first vehicle pays for. The vehicles after the first
    take what the infantry leaves, as far as the Vehicle queue has room:
    without `tank_factories` its room is one vehicle at most.
    """
    production, counts = ctx.production, {}
    vehicle, infantry = wanted.get(VEHICLE_QUEUE), wanted.get(INFANTRY_QUEUE)
    vehicle_cost = 0 if vehicle is None else production.cost(vehicle)
    if vehicle is not None and vehicle_cost <= budget:
        counts[VEHICLE_QUEUE] = 1
        budget -= vehicle_cost
    if infantry is not None:
        counts[INFANTRY_QUEUE] = _infantry_count(infantry, budget, infantry_most, ctx)
        budget -= production.cost(infantry) * counts[INFANTRY_QUEUE]
    if VEHICLE_QUEUE in counts:
        counts[VEHICLE_QUEUE] += min(vehicle_room - 1, budget // max(vehicle_cost, 1))
    return counts


def _infantry_count(item: str, budget: int, most: int, ctx: MatchContext) -> int:
    """How many of an infantry item go into one order: `most`, or as many as the budget pays for."""
    return min(most, budget // max(ctx.production.cost(item), 1))


def _train(wanted: dict[str, str], counts: dict[str, int], ctx: MatchContext) -> list[Intent]:
    """The training orders for the counts of the items wanted, the vehicles proposed first as they are paid first."""
    production = ctx.production
    paid = [(queue, counts.get(queue, 0)) for queue in (VEHICLE_QUEUE, INFANTRY_QUEUE)]
    orders = [production.train(wanted[queue], count) for queue, count in paid if count > 0]
    # The batch carries the infantry ahead of the vehicles.
    return orders[::-1]


class _OnOrder(NamedTuple):
    """A refinery on order for a field: the field, and the rule it was ordered by, FIELD_CLAIM or FIELD_GROWTH,
    FIELD_SPREAD for one ordered for an empty Building queue with a big bank, or NEW_YARD_RULE for the one a new
    expansion yard claims."""

    field: OrePatch
    rule: str


class _UntakenField:
    """The untaken field nearest an own yard that a refinery can stand against now, as the economy's growth past four
    refineries reads it. Where a refinery could stand is looked for around every building, which takes a few
    milliseconds in a large base, so it is worked out again only when the own buildings, the asked fields, the map,
    the terrain or the tuning differ from the last look's."""

    def __init__(self) -> None:
        self._inputs: tuple[object, ...] | None = None
        self._field: OrePatch | None = None

    def field(self, world: World, ctx: MatchContext) -> OrePatch | None:
        """The field; None when no untaken field can be stood against now. The field reserved for the MCV's site is
        taken as one a refinery was asked against."""
        asked = [*ctx.asked_refinery_fields, *([] if ctx.reserved_field is None else [ctx.reserved_field])]
        inputs = (
            ctx.terrain,
            ctx.tuning.placement,
            world.map_bounds,
            tuple(sorted((building.type, building.x, building.y) for building in world.buildings.values())),
            tuple(field.center for field in asked),
        )
        if inputs != self._inputs:
            choice = placement.spread_placement(world, ctx.terrain, ctx.tuning.placement, asked, untaken_only=True)
            self._inputs, self._field = inputs, None if choice is None else choice.field.patch
        return self._field


class _GrowthSaving:
    """Whether the unit queues leave the price of a due growth refinery alone: from a cycle the money does not cover it
    and the reserve until it is ordered or due no longer, `growth_saving_seconds` at the most for each one. One that
    runs out is written as a `refinery_field` event with `saving_timed_out`, and the next may start only
    `growth_saving_seconds` later."""

    def __init__(self) -> None:
        # The tick the saving in force started at; None while there is none.
        self._started: int | None = None
        # The first tick at which a new saving may start, after one ran out.
        self._next_from = 0

    def keeps(self, field: OrePatch | None, is_covered: bool, world: World, ctx: MatchContext) -> bool:
        """Whether the saving is in force in this cycle, a growth refinery being due for `field` (None while none is):
        one goes on, or one starts because `is_covered` is false and the last one that ran out did so long enough ago.
        A saving that has lasted `growth_saving_seconds` ends in this cycle and is written."""
        tick, most = world.tick, ctx.tuning.upkeep.growth_saving_seconds * TICKS_PER_SECOND
        if field is None:
            self._started = None
            return False
        if self._started is None and (is_covered or tick < self._next_from):
            return False
        self._started = tick if self._started is None else self._started
        if tick - self._started < most:
            return True
        self._started, self._next_from = None, tick + most
        _log_saving_timed_out(field, world, ctx)
        return False


def _log_saving_timed_out(field: OrePatch, world: World, ctx: MatchContext) -> None:
    """Write the `refinery_field` event of a growth saving that ran out before its refinery was ordered: the field the
    refinery was due for, the rule `growth` and `saving_timed_out`; no cell, the refinery not being placed."""
    served = placement.refineries_serving(world, ctx.terrain)
    data = {
        "tick": world.tick,
        "cell": None,
        "rule": placement.FIELD_GROWTH,
        "field_center": list(field.center),
        "field_cells": field.size,
        "served_by": served[field],
        "ore_distance_cells": None,
        "saving_timed_out": True,
    }
    records.log(REFINERY_FIELD_EVENT, data)


class MoreRefineries:
    """Builds a third refinery, and from the eighth minute a fourth, once the opening is over and income needs it.

    With the cap of refineries to a field switched on, one more is ordered only for a field under the cap that it can
    stand against; see the module's note. A refinery claim that is due goes before all of these, on the money alone,
    and then a growth refinery past the fourth. None is ordered whose only field is the one reserved for the MCV's
    site: it would wait unplaced and hold the Building queue.
    """

    def __init__(self) -> None:
        self._is_opening_complete = False
        # With the cap on: the tick at which the last look at the fields found none under the cap for one more
        # refinery to stand against; None while no refinery is held back.
        self._held_at: int | None = None
        # While the asked fields are kept: the field the refinery this rule ordered for a claim or for growth is for,
        # until it is placed; None while there is none.
        self._on_order: _OnOrder | None = None
        # With `refinery_growth` on: the field a growth refinery is due for in this cycle; None while none is due.
        self._growth_field: OrePatch | None = None
        self._untaken = _UntakenField()
        # Whether the unit queues leave the price of the growth refinery due alone in this cycle, and the tick a growth
        # refinery was last ordered at.
        self._saving = _GrowthSaving()
        self._is_saving_for_growth = False
        self._growth_ordered_at: int | None = None
        # With `refinery_unserved_only` on: where the next refinery would go, and its field while one may be ordered
        # (`waits_unserved`); None while none may.
        self._unserved = refinery_fields.UnservedPlaces()
        self._unserved_field: OrePatch | None = None
        # With `new_yard_refinery_until_standing` on: the place of the refinery a new yard claims, and the tick it was
        # last asked onto a place (None before), until which it may not show yet.
        self._claimed = ClaimedSpots()
        self._claim_asked_at: int | None = None
        # The tick it was last written why the new yard's refinery waits; None before.
        self._claim_waiting_logged_at: int | None = None

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for one more refinery, or for the power plant it cannot do without, when one is due; none under the
        military focus."""
        production = ctx.production
        is_free = production.is_queue_free(BUILDING_QUEUE) and not _is_power_plant_needed(world, ctx)
        if not self._is_past_opening(ctx) or not is_free or REFINERY not in world.available_production:
            return []
        if is_military(ctx):
            return []
        if self.waits_unserved(world, ctx):
            return []
        first = self._claim_or_growth(world, ctx)
        if first is not None:
            return first
        return self._one_more(world, ctx) if _is_refinery_due(world, ctx) else []

    def _one_more(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for the one more refinery that is due: with `refinery_unserved_only` on, for the field
        `refinery_fields` gives; else with `refinery_field_cap` on only under the cap (`_under_cap`), and with it off
        unless its only field is the one reserved for the MCV's site."""
        if refinery_fields.is_on(ctx):
            return self._ordered(refinery_fields.FIELD_UNSERVED, world, ctx)
        if not ctx.tuning.upkeep.refinery_field_cap:
            return [] if _is_only_field_reserved(world, ctx) else _build_within_power(REFINERY, world, ctx)
        return self._under_cap(world, ctx)

    def decide_growth(self, world: World, ctx: MatchContext) -> None:
        """Decide, at the start of a cycle, whether a growth refinery is due (`_may_grow`, and an untaken field a
        refinery can stand against now) and for which field, and whether the unit queues save for it (`_GrowthSaving`):
        never in the last minutes of the match (`_is_last_minutes`), when a saving in force ends without a word.
        """
        may_grow = _may_grow(world, ctx) and not is_military(ctx)
        self._growth_field = self._field_now(world, ctx) if may_grow else None
        is_covered = _is_money_for(REFINERY, world, ctx)
        # The saving is looked at as if no refinery were due, so that one in force ends and none starts.
        saved_for = None if _is_last_minutes(world, ctx) else self._growth_field
        self._is_saving_for_growth = self._saving.keeps(saved_for, is_covered, world, ctx)

    def waits_unserved(self, world: World, ctx: MatchContext) -> bool:
        """With `refinery_unserved_only` on and the opening's refineries planned, whether no refinery may be ordered
        for want of a field (`refinery_fields`), which a `waiting_unserved` event says once a minute at most; the field
        the next refinery would go to is kept for its order. False with the switch off and for an opening refinery."""
        if not refinery_fields.is_on(ctx) or refinery_fields.is_opening(ctx.production, placing=False):
            self._unserved_field = None
            return False
        choice = self._unserved.choice(world, ctx)
        if choice is None:
            self._unserved.note_waiting(world)
        self._unserved_field = None if choice is None else choice.field.patch
        return choice is None

    def has_place(self, world: World, ctx: MatchContext) -> bool:
        """Whether one more refinery has a place to go: always with `refinery_unserved_only` off and for an opening
        refinery; with it on, while `refinery_fields` gives one, an unserved field's or a second refinery's
        (`waits_unserved`). While a new yard's claim lasts until its refinery stands, whether its field has a place
        (`claimed_spot`)."""
        if new_yard_refinery.is_on(ctx) and ctx.new_yard_field is not None:
            return self.claimed_spot(world, ctx, ctx.promised_places, ctx.refused_places) is not None
        return not self.waits_unserved(world, ctx)

    def has_unserved_place(self, world: World, ctx: MatchContext) -> bool:
        """Whether a refinery would go against an unserved field now (`refinery_fields`), none being on its way and
        the refinery caps leaving room for it."""
        if not refinery_fields.is_on(ctx) or ctx.production.pending(REFINERY) or not _has_refinery_room(world, ctx):
            return False
        choice = self._unserved.placed(world, ctx)
        return choice is not None and choice.field is not None and choice.field.rule == refinery_fields.FIELD_UNSERVED

    def claimed_spot(
        self, world: World, ctx: MatchContext, promised: Iterable[Box], refused: Sequence[placement.RefusedPlace]
    ) -> placement.PlacementChoice | None:
        """The place of the refinery a new yard claims, off the `promised` and `refused` places (see
        `new_yard_refinery`); None without one."""
        return self._claimed.spot(world, ctx, promised, refused)

    def note_claim_asked(self, tick: int) -> None:
        """Note that the refinery a new yard claims was asked onto its place at a tick."""
        self._claim_asked_at = tick

    def for_claimed_field(self, world: World, ctx: MatchContext) -> list[Intent]:
        """With `new_yard_refinery_until_standing` on, the order for the refinery a new yard claims, or for the power
        plant it cannot do without, as `for_new_yard` gives it, but only while its field has a place placement would
        use (`claimed_spot`) and no refinery is on its way, nor was asked onto a place within `promise_ticks` and may
        not show yet. A refinery so ordered is on order for the claimed field, with the rule `new_yard`."""
        production = ctx.production
        is_free = production.is_queue_free(BUILDING_QUEUE) and not _is_power_plant_needed(world, ctx)
        if not is_free or REFINERY not in world.available_production or not _is_money_for(REFINERY, world, ctx):
            self._log_claim_waiting(is_free, world, ctx)
            return []
        asked_at, promise = self._claim_asked_at, ctx.tuning.placement.promise_ticks
        is_showing_soon = asked_at is not None and world.tick - asked_at < promise
        if production.pending(REFINERY) or is_showing_soon or not self.has_place(world, ctx):
            return []
        orders = _build_within_power(REFINERY, world, ctx)
        if any(proposal.item == REFINERY for proposal in production.proposed()):
            self._on_order = _OnOrder(ctx.new_yard_field, NEW_YARD_RULE)
        return orders

    def _field_now(self, world: World, ctx: MatchContext) -> OrePatch | None:
        """The field one more refinery would be for now: with `refinery_unserved_only` on the one `refinery_fields`
        gives, else the untaken field (`_UntakenField`); None without one."""
        if refinery_fields.is_on(ctx):
            return None if self.waits_unserved(world, ctx) else self._unserved_field
        return self._untaken.field(world, ctx)

    @property
    def is_growth_due(self) -> bool:
        """Whether a growth refinery is due in this cycle."""
        return self._growth_field is not None

    def for_untaken_field(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for a refinery for the untaken field nearest a yard that one can stand against now
        (`_UntakenField`), or for the power plant it cannot do without, when the refinery rules' caps leave room for
        one more (`_has_refinery_room`); none else. While the asked fields are kept, the refinery ordered has its
        field remembered as on order, so that it is placed against that field. None under the military focus."""
        if not _has_refinery_room(world, ctx) or is_military(ctx):
            return []
        field = self._field_now(world, ctx)
        if field is None:
            return []
        orders = _build_within_power(REFINERY, world, ctx)
        is_ordered = any(proposal.item == REFINERY for proposal in ctx.production.proposed())
        if is_ordered and _keeps_asked_fields(ctx):
            self._on_order = _OnOrder(field, placement.FIELD_SPREAD)
        return orders

    def _log_claim_waiting(self, is_free: bool, world: World, ctx: MatchContext) -> None:
        """With new_yard_refinery_first on, write why the new yard's refinery is not ordered in this cycle, as a
        `refinery_field` event of reason `new_yard_waiting`, at most once every CLAIM_WAITING_SECONDS."""
        last = self._claim_waiting_logged_at
        if not is_new_yard_claim(ctx) or (last is not None and world.tick - last < CLAIM_WAITING_TICKS):
            return
        self._claim_waiting_logged_at = world.tick
        data = {
            "tick": world.tick,
            "reason": NEW_YARD_WAITING,
            "queue_free": is_free,
            "on_offer": REFINERY in world.available_production,
            "money": world.economy.spendable,
            "needed": ctx.production.cost(REFINERY) + ctx.tuning.upkeep.production_cash_reserve,
            "field_center": list(ctx.new_yard_field.center),
        }
        records.log(REFINERY_FIELD_EVENT, data)

    def for_new_yard(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for the refinery claimed for a new yard's field (`ctx.new_yard_field`), or for the power plant it
        cannot do without, once the Building queue is free, no power plant is needed first and the money covers it and
        the production reserve; none else. A refinery so ordered is on order for that field, with the rule
        `new_yard`. None under the military focus, while the claim waits."""
        production = ctx.production
        is_free = production.is_queue_free(BUILDING_QUEUE) and not _is_power_plant_needed(world, ctx)
        if not is_free or REFINERY not in world.available_production or not _is_money_for(REFINERY, world, ctx):
            return []
        if is_military(ctx):
            return []
        if self.waits_unserved(world, ctx):
            return []
        orders = _build_within_power(REFINERY, world, ctx)
        if any(proposal.item == REFINERY for proposal in production.proposed()):
            self._on_order = _OnOrder(self._unserved_field or ctx.new_yard_field, NEW_YARD_RULE)
        return orders

    def growth_set_aside(self, world: World, ctx: MatchContext) -> int:
        """What the unit queues leave alone for a growth refinery in this cycle: its price while they save for it and
        in the cycle it is ordered; nothing else."""
        is_ordered_now = self._growth_ordered_at == world.tick
        return ctx.production.cost(REFINERY) if self._is_saving_for_growth or is_ordered_now else 0

    def _claim_or_growth(self, world: World, ctx: MatchContext) -> list[Intent] | None:
        """The order for the refinery a due claim calls for, else for a due growth refinery, or for the power plant it
        cannot do without, once the money covers it and the production reserve; None when neither is due or the money
        falls short."""
        if not _is_money_for(REFINERY, world, ctx):
            return None
        # While a new yard claims a refinery, the new yard's rule orders it (`for_new_yard`), ahead of any other claim.
        if ctx.refinery_claim_due and ctx.new_yard_field is None:
            return self._ordered(placement.FIELD_CLAIM, world, ctx)
        if self.is_growth_due:
            return self._ordered(placement.FIELD_GROWTH, world, ctx)
        return None

    def _ordered(self, rule: str, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for a refinery for the oldest claim field or the growth field, or for the power plant it cannot do
        without; while the asked fields are kept, a refinery so ordered has its field remembered as on order for it."""
        orders = _build_within_power(REFINERY, world, ctx)
        is_ordered = any(proposal.item == REFINERY for proposal in ctx.production.proposed())
        if is_ordered and rule == placement.FIELD_GROWTH:
            self._growth_ordered_at = world.tick
        if is_ordered and _keeps_asked_fields(ctx):
            field = ctx.claim_fields[0] if rule == placement.FIELD_CLAIM else self._growth_field
            self._on_order = _OnOrder(self._unserved_field or field, rule)
        return orders

    @property
    def on_order(self) -> _OnOrder | None:
        """The field the refinery on order was ordered for as a claim or for growth, and the rule, while the asked
        fields are kept; None while there is none."""
        return self._on_order

    def on_order_fields(self, ctx: MatchContext) -> list[OrePatch]:
        """The field the refinery on order was ordered for, while it is on its way; none once it is placed and while
        no refinery was ordered for a field."""
        if not ctx.production.pending(REFINERY):
            self._on_order = None
        return [] if self._on_order is None else [self._on_order.field]

    def _under_cap(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for one more refinery when the place it would be asked onto now stands against a field that
        fewer refineries than the cap serve, or for the power plant it cannot do without; none while no such place is
        free, and then the fields are looked at again only a while later. The places against the field reserved for the
        MCV's site (`_reserved_boxes`) are kept off, so that no refinery is ordered for that field.

        The refinery ordered is written as a `refinery_field` event with `cap_held` false, and the first look of a
        hold that finds no field with `cap_held` true.
        """
        if self._held_at is not None and world.tick - self._held_at < _CAP_RECHECK_TICKS:
            return []
        choice = placement.choose_placement(
            REFINERY,
            world,
            ctx.terrain,
            ctx.tuning.placement,
            target=creep_target_after_opening(ctx),
            field_cap=True,
            refused=_reserved_boxes(ctx),
        )
        field = None if choice.cell is None else placement.field_at(choice.cell, ctx.terrain)
        served = placement.refineries_serving(world, ctx.terrain)
        is_under = field is not None and _stands_under_cap(choice.cell, field, served, ctx.tuning.placement)
        if not is_under:
            if self._held_at is None:
                _log_cap(choice, field, served, world, held=True)
            self._held_at = world.tick
            return []
        self._held_at = None
        orders = _build_within_power(REFINERY, world, ctx)
        if any(proposal.item == REFINERY for proposal in ctx.production.proposed()):
            _log_cap(choice, field, served, world, held=False)
        return orders

    def is_saving(self, world: World, ctx: MatchContext) -> bool:
        """Whether the money is saved for the third refinery, as for a lost core building.

        It is, while the third is called for or on its way, does not stand yet, and the money does not cover its
        price and the reserve; but not in the last minutes of a match, when the money goes into what can be had
        at once.
        """
        production, tuning = ctx.production, ctx.tuning.upkeep
        if self._held_at is not None:
            # Held back by the cap: there is no field to save for it.
            return False
        # The opening builds the base's first refineries; the one after them is the one saved for.
        core = _core_refineries(world)
        is_third_missing = production.standing(REFINERY) == core < tuning.refineries_max
        is_in_time = world.game_seconds >= tuning.refinery_saving_from_seconds and not clock.is_spending(world, ctx)
        if not self._is_past_opening(ctx) or not is_third_missing or not is_in_time:
            return False
        is_offered = REFINERY in world.available_production and not is_military(ctx)
        is_called_for = is_offered and _is_income_short(world, ctx)
        return (production.pending(REFINERY) > 0 or is_called_for) and not _is_money_for(REFINERY, world, ctx)

    def is_called_for(self, world: World, ctx: MatchContext) -> bool:
        """Whether one more refinery than the opening's is called for now.

        Once ordered, it holds the Building queue until it is placed: no other building comes from there meanwhile.
        """
        return self._is_past_opening(ctx) and REFINERY in world.available_production and _is_refinery_due(world, ctx)

    def _is_past_opening(self, ctx: MatchContext) -> bool:
        """Whether the opening's build is complete: its last building has stood (see `_is_opening_over`)."""
        self._is_opening_complete = self._is_opening_complete or _is_opening_over(ctx)
        return self._is_opening_complete


def _is_last_minutes(world: World, ctx: MatchContext) -> bool:
    """Whether the match is in its last minutes, the time part of the end-of-match spending (see `clock`): the time
    left is known and at most `spend_remaining_seconds`. The money and the plan's rule are left out, so that once it
    holds it holds for the rest of the match."""
    remaining = world.remaining_seconds
    return remaining is not None and remaining <= ctx.tuning.clock.spend_remaining_seconds


def _may_grow(world: World, ctx: MatchContext) -> bool:
    """Whether a growth refinery may be due, the field aside: `refinery_growth` on, at least `_GROWTH_FROM_REFINERIES`
    own refineries standing, fewer than `growth_refineries_max` standing or on their way, none on its way, and a
    refinery on offer."""
    tuning, production = ctx.tuning.upkeep, ctx.production
    is_grown = production.standing(REFINERY) >= _GROWTH_FROM_REFINERIES
    has_room = production.planned(REFINERY) < tuning.growth_refineries_max and not production.pending(REFINERY)
    return tuning.refinery_growth and is_grown and has_room and REFINERY in world.available_production


def _reserved_boxes(ctx: MatchContext) -> tuple[Box, ...]:
    """The places from which a refinery would stand against the field reserved for the MCV's site
    (`placement.reach_boxes`), kept off as places the engine refused are; none while no field is reserved."""
    reserved = ctx.reserved_field
    return () if reserved is None else placement.reach_boxes(reserved, ctx.tuning.placement.refinery_ore_cells)


def _is_only_field_reserved(world: World, ctx: MatchContext) -> bool:
    """Whether a refinery ordered now could stand against no field but the one reserved for the MCV's site: with the
    places against that field kept off (`_reserved_boxes`) the place it would be asked onto stands against no field,
    and without, against the reserved one. False while no field is reserved."""
    if ctx.reserved_field is None:
        return False
    tuning, target = ctx.tuning.placement, creep_target_after_opening(ctx)
    kept_off = placement.choose_placement(
        REFINERY, world, ctx.terrain, tuning, target=target, refused=_reserved_boxes(ctx)
    )
    if _stands_against(kept_off.cell, ctx.terrain.ore_patches, ctx):
        return False
    free = placement.choose_placement(REFINERY, world, ctx.terrain, tuning, target=target)
    return _stands_against(free.cell, (ctx.reserved_field,), ctx)


def _stands_under_cap(cell: Cell, field: OrePatch, served: Counter[OrePatch], tuning: PlacementTuning) -> bool:
    """Whether a refinery asked onto a cell would stand against a field that fewer than `max_refineries_per_field`
    refineries serve: within `refinery_ore_cells` of its ore."""
    is_under = served[field] < tuning.max_refineries_per_field
    return is_under and placement.reaches(placement.box_at(REFINERY, cell), field, tuning.refinery_ore_cells)


def _log_cap(
    choice: placement.PlacementChoice, field: OrePatch | None, served: Counter[OrePatch], world: World, *, held: bool
) -> None:
    """Write one `refinery_field` event for a refinery the cap lets be ordered or holds back: the cell it would be
    asked onto, the field that cell serves and how many refineries serve it, and `cap_held`. Without a cell or a
    field, their entries are null."""
    cell = choice.cell
    if cell is None or field is None:
        reasons = dict.fromkeys(_FIELD_REASONS)
    else:
        rule = None if choice.field is None else choice.field.rule
        reasons = _field_reasons(FieldChoice(field, rule, served[field]), cell)
    data = {"tick": world.tick, "cell": None if cell is None else list(cell), **reasons, "cap_held": held}
    records.log(REFINERY_FIELD_EVENT, data)


def _is_income_short(world: World, ctx: MatchContext) -> bool:
    """Whether the refineries hold back income: three quarters full, or every one worked by
    `refinery_worked_harvesters` harvesters, fewer than the harvester target wants, so that one more refinery does not
    wait for the last harvesters of the target."""
    tuning, production, economy = ctx.tuning.upkeep, ctx.production, world.economy
    is_full = 0 < economy.resource_capacity and economy.ore >= tuning.refinery_full_share * economy.resource_capacity
    is_worked = production.units(HARVESTER) >= tuning.refinery_worked_harvesters * production.standing(REFINERY)
    return is_full or is_worked


def _is_refinery_due(world: World, ctx: MatchContext) -> bool:
    """Whether one more refinery is due: fewer than the match's time allows, money, and income held back."""
    tuning, production = ctx.tuning.upkeep, ctx.production
    allowed = _refineries_allowed(world, tuning)
    has_money = world.economy.spendable >= tuning.extra_refinery_min_cash and _clears_warning_reserve(
        REFINERY, world, ctx
    )
    # A refinery of the opening's that was lost is the core rebuild's to replace, not this rule's.
    is_beyond_core = production.planned(REFINERY) >= _core_refineries(world)
    return is_beyond_core and production.planned(REFINERY) < allowed and has_money and _is_income_short(world, ctx)


def _refineries_allowed(world: World, tuning: UpkeepTuning) -> int:
    """The most refineries the match's time allows: `refineries_max`, and `refineries_max_late` from
    `refineries_late_after_seconds` on."""
    is_late = world.game_seconds >= tuning.refineries_late_after_seconds
    return tuning.refineries_max_late if is_late else tuning.refineries_max


def _has_refinery_room(world: World, ctx: MatchContext) -> bool:
    """Whether one more refinery is allowed under the refinery rules' caps: one is on offer, none is on its way, and
    fewer stand than the match's time allows (`_refineries_allowed`), or with `refinery_growth` on than
    `growth_refineries_max` when that is more."""
    tuning, production = ctx.tuning.upkeep, ctx.production
    most = _refineries_allowed(world, tuning)
    if tuning.refinery_growth:
        most = max(most, tuning.growth_refineries_max)
    is_free = REFINERY in world.available_production and not production.pending(REFINERY)
    return is_free and production.planned(REFINERY) < most


def _core_refineries(world: World) -> int:
    """How many refineries the opening builds."""
    return build_steps(FAMILIES[world.family]).count(REFINERY)


class Tech:
    """Builds the service depot, once, when the war factory stands and the economy can carry it.

    It waits while the money is saved for the third refinery, which goes first. A main force built around tanks has
    its depot from `EarlyDepot` instead.
    """

    def __init__(self, refineries: MoreRefineries) -> None:
        self._has_depot_stood = False
        self._refineries = refineries

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for the service depot, or for the power plant that has to come before it."""
        production = ctx.production
        self._has_depot_stood = self._has_depot_stood or production.standing(SERVICE_DEPOT) > 0
        if self._has_depot_stood or _is_tank_force(ctx) or not _can_start_depot(world, ctx):
            return []
        return [] if self._refineries.is_saving(world, ctx) else _build_within_power(SERVICE_DEPOT, world, ctx)


class EarlyDepot:
    """Rushes the service depot of a main force built around tanks: the next building after the war factory, ordered
    as soon as it stands and the Building queue is free, whatever the money; the unit queues save for it until it
    stands (`is_saving`).

    See the module's note.
    """

    def __init__(self, core: CoreBuildings, refineries: MoreRefineries) -> None:
        self._has_depot_stood = False
        # The rule that rebuilds the core of the base, which says when money is saved for a lost building.
        self._core = core
        # The rule that adds refineries, which says whether a claim refinery has a place to go.
        self._refineries = refineries

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for the service depot, or for the power plant that has to come before it, when it is due."""
        if not self._is_rushed(world, ctx) or not _is_early_depot_due(world, ctx):
            return []
        return [] if self._core.saved_for(world, ctx) is not None else _build_within_power(SERVICE_DEPOT, world, ctx)

    def is_saving(self, world: World, ctx: MatchContext) -> bool:
        """Whether the unit queues save for the depot in this cycle: it is rushed, on its way or on offer, no major
        threat is on, and the money does not cover its price and the reserve."""
        if not self._is_rushed(world, ctx) or ctx.threat_level == MAJOR_THREAT:
            return False
        is_open = ctx.production.planned(SERVICE_DEPOT) > 0 or SERVICE_DEPOT in world.available_production
        return is_open and not _is_money_for(SERVICE_DEPOT, world, ctx)

    def _is_rushed(self, world: World, ctx: MatchContext) -> bool:
        """Whether the depot is rushed in this cycle: a main force of tanks, a war factory standing, no depot that has
        ever stood, and no refinery claim due ahead of it, which keeps its place."""
        production = ctx.production
        self._has_depot_stood = self._has_depot_stood or production.standing(SERVICE_DEPOT) > 0
        if self._has_depot_stood or not _is_tank_force(ctx) or not _is_opening_over(ctx):
            return False
        return not (ctx.refinery_claim_due and _claim_refinery_due(world, ctx, self._refineries.has_place(world, ctx)))


def _is_tank_force(ctx: MatchContext) -> bool:
    """Whether the main force is built around tanks, which wants its service depot early."""
    return ctx.tuning.mix.main_force == TANKS


def _is_early_depot_due(world: World, ctx: MatchContext) -> bool:
    """Whether the rushed service depot is ordered now: none on its way, the depot on offer, a free Building queue and
    no major threat; the money is not waited for."""
    production = ctx.production
    can_start = not production.planned(SERVICE_DEPOT) and SERVICE_DEPOT in world.available_production
    return can_start and production.is_queue_free(BUILDING_QUEUE) and ctx.threat_level != MAJOR_THREAT


class AntiAirDefenses:
    """Keeps an AA defense, and a second once many enemy aircraft are seen, while more than a few are seen lately and
    a radar stands.

    See the module's note.
    """

    def __init__(self, core: CoreBuildings, remote_air: RemoteBaseAntiAir) -> None:
        # The rule that rebuilds the core of the base, which says when money is saved for a lost building.
        self._core = core
        # The rule that answers an air attack on a remote base, whose AA defenses this rule does not count.
        self._remote_air = remote_air

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for one more AA defense, or for the power plant that has to come before it, when one is due."""
        defense = ANTI_AIR_DEFENSES[world.family]
        apart = self._remote_air.counted(world)
        if not _is_anti_air_defense_due(defense, world, ctx, apart) or self._core.saved_for(world, ctx) is not None:
            return []
        return defense_within_power(defense, world, ctx)


def _is_anti_air_defense_due(defense: str, world: World, ctx: MatchContext, apart: int) -> bool:
    """Whether one more AA defense is due: fewer stand or are on their way than are wanted, but the `apart` ones of
    the remote base answer, a radar stands, the Defense queue is free, which it is not while one is on its way, the base
    is quiet and the investment test passes."""
    production = ctx.production
    wanted = anti_air_defenses_wanted(ctx.air_watch, world.tick, ctx.tuning.anti_air)
    is_short = production.planned(defense) - apart < wanted
    is_free = production.is_queue_free(DEFENSE_QUEUE) and not ctx.threat_active
    has_radar = production.standing(RADAR) > 0
    return is_short and has_radar and is_free and _is_investment(defense, world, ctx)


class TeslaCoils:
    """Keeps a Soviet base's Tesla coils standing or on their way once a war factory stands.

    See the module's note.
    """

    def __init__(self, core: CoreBuildings) -> None:
        # The rule that rebuilds the core of the base, which says when money is saved for a lost building.
        self._core = core
        # With `defenses_on_threat_only` on: the cell of the vehicle attack the coil this rule has on its way answers,
        # which the coil is placed toward; None while no such coil is on its way.
        self.goal: Cell | None = None

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for one more Tesla coil, or for the power plant that has to come before it, when one is due; with
        `defenses_on_threat_only` on, only for a vehicle attack that no coil answers yet."""
        coil = FAMILIES[world.family].anti_armor_defense
        if ctx.tuning.upkeep.defenses_on_threat_only:
            return self._against_attack(coil, world, ctx)
        return self._when_due(coil, world, ctx)

    def _when_due(self, coil: str, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for one more coil, or for the power plant it waits for, when one is due."""
        if not _is_tesla_coil_due(coil, world, ctx) or self._core.saved_for(world, ctx) is not None:
            return []
        return defense_within_power(coil, world, ctx)

    def _against_attack(self, coil: str, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for a coil toward the first vehicle attack of the match that lies more than `tesla_side_cells`
        from every coil standing and from the one on its way, when a coil is due; the attack is the coil's goal."""
        if not ctx.production.pending(coil):
            self.goal = None
        coils = [(building.x, building.y) for building in world.buildings.values() if building.type == coil]
        answered = coils if self.goal is None else [*coils, self.goal]
        side_cells = ctx.tuning.upkeep.tesla_side_cells
        attack = next((cell for cell in ctx.vehicle_attacks if not _is_any_within(cell, answered, side_cells)), None)
        if attack is None:
            return []
        orders = self._when_due(coil, world, ctx)
        # A power plant ordered first is not the coil, which waits for it with no goal yet.
        if any(intent.order.get("item") == coil for intent in orders):
            self.goal = attack
        return orders


def _tesla_coils_wanted(family: str, tuning: UpkeepTuning) -> int:
    """How many Tesla coils a base of a family keeps: `soviet_tesla_coils` for the Soviets, none for the Allies."""
    return tuning.soviet_tesla_coils if family == SOVIET else 0


def _is_tesla_coil_due(coil: str, world: World, ctx: MatchContext) -> bool:
    """Whether one more Tesla coil is due: fewer stand or are on their way than are wanted, a war factory stands, the
    Defense queue is free, which it is not while one is on its way, the base is quiet and the investment test passes.
    With `defenses_on_threat_only` on a coil answers an attack, and may be due while the base is under attack."""
    production, tuning = ctx.production, ctx.tuning.upkeep
    is_short = production.planned(coil) < _tesla_coils_wanted(world.family, tuning)
    has_war_factory = production.standing(WAR_FACTORY) > 0
    is_quiet = tuning.defenses_on_threat_only or not ctx.threat_active
    is_free = production.is_queue_free(DEFENSE_QUEUE) and is_quiet
    return is_short and has_war_factory and is_free and _is_investment(coil, world, ctx)


class Kennel:
    """Builds the kennel the Soviet scout slots' dogs are trained from (see `scout_slots`): with `scout_slots` on and the
    dog as the Soviet scout unit, one, once the opening's build is complete."""

    def __init__(self) -> None:
        # The opening's build is complete once its last building has stood.
        self._is_opening_complete = False

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for the kennel, or for the power plant that has to come before it, when the kennel is due."""
        self._is_opening_complete = self._is_opening_complete or _is_opening_over(ctx)
        if not (self._is_opening_complete and _is_kennel_due(world, ctx)):
            return []
        return _build_within_power(KENNEL, world, ctx)


def _is_kennel_due(world: World, ctx: MatchContext) -> bool:
    """Whether a kennel is due: `scout_slots` on with the dog as `soviet_scout_unit`, a Soviet base with none standing or
    on its way, the kennel on offer (its power plant stands), a free Building queue and the money for it and the
    reserve."""
    production, scouting = ctx.production, ctx.tuning.scouting
    is_dogs = scouting.scout_slots and scouting.soviet_scout_unit == _DOG
    is_wanted = is_dogs and world.family == SOVIET and not production.planned(KENNEL)
    can_start = KENNEL in world.available_production and production.is_queue_free(BUILDING_QUEUE)
    return is_wanted and can_start and _is_money_for(KENNEL, world, ctx)


class Radar:
    """Builds a radar once a service depot stands, from the sixth minute on, and again when it is lost.

    See the module's note.
    """

    def __init__(self, core: CoreBuildings) -> None:
        # The rule that rebuilds the core of the base, which says when money is saved for a lost building.
        self._core = core

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The order for the radar, or for the power plant that has to come before it, when the radar is due."""
        if not _is_radar_due(world, ctx) or self._core.saved_for(world, ctx) is not None:
            return []
        return _build_within_power(RADAR, world, ctx)


def _is_radar_due(world: World, ctx: MatchContext) -> bool:
    """Whether a radar is due: a service depot stands and no radar, in time, a quiet base, a free Building queue and
    the investment test passed."""
    production = ctx.production
    is_wanted = production.standing(SERVICE_DEPOT) > 0 and not production.planned(RADAR)
    is_in_time = world.game_seconds >= ctx.tuning.upkeep.dome_from_seconds
    is_free = production.is_queue_free(BUILDING_QUEUE) and not ctx.threat_active
    return is_wanted and is_in_time and is_free and _is_investment(RADAR, world, ctx)


def _can_start_depot(world: World, ctx: MatchContext) -> bool:
    """Whether a service depot can be ordered now: it is the next thing to have, affordable and on offer."""
    tuning, production = ctx.tuning.upkeep, ctx.production
    is_next = _is_opening_over(ctx) and not production.planned(SERVICE_DEPOT)
    can_carry_it = (
        world.economy.spendable >= tuning.tech_min_cash and production.units(HARVESTER) >= tuning.tech_min_harvesters
    )
    can_start = SERVICE_DEPOT in world.available_production and production.is_queue_free(BUILDING_QUEUE)
    return is_next and can_carry_it and can_start


class _Switches:
    """Which way each building was last switched, and when."""

    def __init__(self) -> None:
        # Building id -> (the way it was switched, the tick the order went out at).
        self._last: dict[int, tuple[bool, int]] = {}

    def is_on_its_way(self, building_id: int, on: bool, tick: int, within_ticks: int) -> bool:
        """Whether this very switch went out to a building so short a time ago that its effect may not show yet."""
        last = self._last.get(building_id)
        return last is not None and last[0] == on and tick - last[1] < within_ticks

    def send(self, building_id: int, on: bool, tick: int, again_after_ticks: int) -> bool:
        """Note a switch that is about to be sent; False when the same one is still on its way and must not repeat."""
        if self.is_on_its_way(building_id, on, tick, again_after_ticks):
            return False
        self._last[building_id] = (on, tick)
        return True


class Repair:
    """Switches repair on for a badly damaged building while money allows, and off when it is no longer worth it."""

    def __init__(self) -> None:
        self._switches = _Switches()

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """A `repair` with an explicit `on` for each building whose repair should be the other way than it is."""
        tuning = ctx.tuning.upkeep
        in_force = repairs_on_in_force(ctx.ledger, world.tick, tuning.repair_resend_ticks)
        intents = []
        for building in world.buildings.values():
            on = _repair_wanted(building, world.economy.spendable, tuning)
            if on is None or (on and building.id in in_force):
                continue
            if self._switches.send(building.id, on, world.tick, tuning.switch_again_after_ticks):
                intents.append(Intent(Priority.PRODUCTION, {"cmd": "repair", "building": building.id, "on": on}))
        return intents


def repairs_on_in_force(ledger: OrderLedger, tick: int, within_ticks: int) -> set[int]:
    """The buildings whose last repair switch was a `repair on`, sent within so many ticks, and not rejected or lost.

    The ledger keeps its orders in the order they were sent, so only its
    newest entries are read.
    """
    last_switch: dict[int, bool] = {}
    for entry in reversed(ledger.entries):
        if tick - entry.sent_tick >= within_ticks:
            break
        order = entry.order
        if order.get("cmd") == "repair" and order.get("building") not in last_switch:
            last_switch[order.get("building")] = order.get("on") is True and entry.status not in (REJECTED, LOST)
    return {building for building, is_on in last_switch.items() if is_on}


def _repair_wanted(building: Building, money: int, tuning: UpkeepTuning) -> bool | None:
    """The way a building's repair should be switched: on, off, or None to leave it as it is."""
    if building.repairing:
        is_done = building.hp > tuning.repair_stop_above_hp or money < tuning.repair_stop_below_cash
        return False if is_done else None
    is_needed = building.hp < _repair_on_below(building, tuning) and money >= tuning.repair_min_cash
    return True if is_needed else None


def _repair_on_below(building: Building, tuning: UpkeepTuning) -> int:
    """The hp below which a building's repair is switched on: with repair_defenses_early on, a defense's below
    repair_stop_above_hp, where its repair stops; repair_below_hp for any other. A defense takes its damage a little at
    a time on the front of the base, and one kept above repair_below_hp was never repaired."""
    if tuning.repair_defenses_early and building.type in DEFENSE_TYPES:
        return tuning.repair_stop_above_hp
    return tuning.repair_below_hp


class Rally:
    """Sets the rally point of each barracks and war factory to where main gathers: once to the staging point, or to
    the gather point the combat manager publishes, again whenever it moves."""

    def __init__(self) -> None:
        # Building id -> the cell its rally point was ordered to.
        self._sent: dict[int, Cell] = {}

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """A `rally` for each producing building whose rally point is not yet where main gathers."""
        point = ctx.gather_point or safe_staging.staging_point(world, ctx.tuning, ctx)
        if point is None:
            return []
        due = [
            building.id
            for building in world.buildings.values()
            if building.type in _PRODUCERS and _is_rally_due(self._sent.get(building.id), point, ctx)
        ]
        self._sent.update(dict.fromkeys(due, point))
        return [
            Intent(Priority.PRODUCTION, {"cmd": "rally", "building": building_id, "to": list(point)})
            for building_id in due
        ]


def _is_rally_due(sent: Cell | None, point: Cell, ctx: MatchContext) -> bool:
    """Whether a producing building needs a rally to `point`: it has had none, or main's gather point is published and
    its rally went elsewhere."""
    return sent is None or (ctx.gather_point is not None and sent != point)


class LowPower:
    """Switches off what the base can best do without while it is short of power, and on again when it is not."""

    def __init__(self) -> None:
        self._switches = _Switches()

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The `power_toggle` orders, each with an explicit `on`, that the power figures of this state call for."""
        tuning = ctx.tuning.upkeep
        headroom = world.economy.power_provided - world.economy.power_drained
        if headroom < 0:
            return [] if _is_power_plant_nearly_done(world, tuning) else self._shed(-headroom, world, ctx)
        return self._restore(headroom, world, ctx) if headroom >= tuning.power_restore_headroom else []

    def _shed(self, shortage: int, world: World, ctx: MatchContext) -> list[Intent]:
        """Switch off consumers, the most dispensable first, until what they drained covers the shortage."""
        drain, tick = ctx.production.drain, world.tick
        patience = ctx.tuning.upkeep.switch_again_after_ticks
        running = [building for building in _dispensable(world, ctx.tuning.upkeep) if not building.powered_down]
        # What was switched off a moment ago and still shows as running has been taken off the shortage already.
        shortage -= sum(drain(b.type) for b in running if self._switches.is_on_its_way(b.id, False, tick, patience))
        intents = []
        for building in running:
            if shortage > 0 and self._switches.send(building.id, False, tick, patience):
                intents.append(_power_toggle(building, on=False))
                shortage -= drain(building.type)
        return intents

    def _restore(self, headroom: int, world: World, ctx: MatchContext) -> list[Intent]:
        """Switch consumers back on, the most needed first, as far as the power to spare reaches."""
        tuning, drain, tick = ctx.tuning.upkeep, ctx.production.drain, world.tick
        patience = tuning.switch_again_after_ticks
        switched_off = [building for building in reversed(_dispensable(world, tuning)) if building.powered_down]
        # What was switched on a moment ago and still shows as off will take its share of the spare power.
        spare = headroom - sum(
            drain(b.type) for b in switched_off if self._switches.is_on_its_way(b.id, True, tick, patience)
        )
        intents = []
        for building in switched_off:
            fits = spare >= tuning.power_restore_headroom and spare >= drain(building.type)
            if fits and self._switches.send(building.id, True, tick, patience):
                intents.append(_power_toggle(building, on=True))
                spare -= drain(building.type)
        return intents


def _power_toggle(building: Building, *, on: bool) -> Intent:
    return Intent(Priority.POWER, {"cmd": "power_toggle", "building": building.id, "on": on})


def _dispensable(world: World, tuning: UpkeepTuning) -> list[Building]:
    """The consumers that may be switched off, the most dispensable first.

    The service depots, then the radars, then the defenses; a defense with an
    enemy in sight near it is not among them.
    """
    enemies = [(record.x, record.y) for record in world.enemy_memory.values() if record.visible]
    reach = tuning.power_down_enemy_radius_cells
    ranked = []
    for building in world.buildings.values():
        if building.type in _DISPENSABLE_FIRST:
            ranked.append((_DISPENSABLE_FIRST.index(building.type), building))
        elif building.type in DEFENSE_TYPES and not _is_any_within((building.x, building.y), enemies, reach):
            ranked.append((len(_DISPENSABLE_FIRST), building))
    return [building for _, building in sorted(ranked, key=lambda pair: pair[0])]


def _is_any_within(cell: Cell, others: list[Cell], reach: int) -> bool:
    return any(cells.is_within(cell, other, reach) for other in others)


def _is_power_plant_nearly_done(world: World, tuning: UpkeepTuning) -> bool:
    """Whether a power plant waits to be placed or will be finished within the time worth waiting for."""
    return any(
        job.item in _POWER_PLANTS and not job.paused and (job.ready or job.remaining_s <= tuning.low_power_wait_seconds)
        for job in world.production.get(BUILDING_QUEUE, ())
    )


class UpkeepManager:
    """Keeps the base running: asks each of its rules in turn, in every cycle. See the module's note."""

    name = "upkeep"

    def __init__(self) -> None:
        # The money left at the end of a match takes the empty queues before anything else is fed to them.
        # The harvester rule comes before the unit queues: a harvester it orders takes the Vehicle queue first.
        # The producer rule comes before them too, so that they leave the price of a producer it orders alone.
        # And power and a lost core building come before one more producer, a producer before the service depot,
        # the service depot before one more refinery, and the refinery before the radar: all are made in the
        # Building queue. A main force built around tanks has its service depot ahead of one more producer. On the
        # Defense queue a lost defense of the opening comes before an AA defense, and an AA defense, the one answer
        # to aircraft, before a Tesla coil. The second yard's depot comes after the depot of a main force of tanks,
        # and its MCV takes the Vehicle queue ahead of the unit queues, which leave its price alone. An order that is
        # never to wait for the economy's next item (see `economy_hold`) comes from a rule before the unit queues.
        core, refineries = CoreBuildings(), MoreRefineries()
        coils = TeslaCoils(core)
        self._forward = ForwardMcv()
        mcv = ExpansionMcv(core.saved_for, self._forward)
        self._next_patch = NextPatch()
        self._far_field = NearestFarField()
        self._walks = RouteWalks(self._far_field)
        self._full_hold = FullHold(MCV)
        # Whether the ore has stayed near the storage cap (see `bank_spending`), noted once a cycle before any rule.
        self._storage = StorageWatch()
        self._harvesters = Harvesters()
        # The spending focus, decided at the start of every cycle before any rule reads it (see `spending_focus`).
        self._focus = SpendingFocus()
        # An air attack on a remote base is answered ahead of every other rule (see `remote_air`).
        remote_air = RemoteBaseAntiAir(_build_within_power, defense_within_power)
        self._placement = Placement(refineries, coils, self._far_field, self._walks, self._forward, remote_air)
        self._refineries = refineries
        held_refineries = _HeldByFullHold(refineries, lets_claims_through=True)
        depot = EarlyDepot(core, refineries)
        producers = ProducerScaling(core, refineries, self._storage)
        # While a full hold for the MCV is in force (see `economy_hold`), the rules wrapped in `_HeldByFullHold` order
        # nothing; the defenses, the unit queues, power, the harvesters and the producers hold what they must
        # themselves.
        self._rules: tuple[Rule, ...] = (
            remote_air,
            self._placement,
            PowerPlants(),
            NewYardRefinery(refineries, self._forward),
            core,
            # The endgame lead's defenses go before every other spending (see `clock`).
            clock.EndgameLeadDefense(defense_within_power),
            clock.EndgameSpend(),
            Defenses(),
            # The flame tower the Soviet counter quota's flamethrowers wait for (see `counter_units`).
            _HeldByFullHold(FlameTowerForCounters()),
            # The military focus's defenses toward the enemy (see `spending_focus`).
            _HeldByFullHold(FocusDefenses(defense_within_power)),
            _HeldByFullHold(clock.PreserveDefense(defense_within_power)),
            _HeldByFullHold(AntiAirDefenses(core, remote_air)),
            _HeldByFullHold(coils),
            self._harvesters,
            _HeldByFullHold(depot),
            _HeldByFullHold(ExpansionDepot(_build_within_power, core.saved_for)),
            producers,
            _HeldByFullHold(Kennel()),
            mcv,
            # A mechanic or a medic takes the Infantry queue ahead of the unit queues (see `support`).
            SupportTraining(core.saved_for, refineries.is_saving),
            UnitQueues(core, refineries, depot, self._full_hold, self._storage, self._harvesters),
            _HeldByFullHold(Tech(refineries)),
            held_refineries,
            _HeldByFullHold(Radar(core)),
            # The Soviet tech center needs the radar, and follows the bank in every state (see `soviet_tech`).
            SovietTechCenter(_build_within_power, core.saved_for),
            Repair(),
            Rally(),
            self._walks,
            self._forward,
            LowPower(),
        )
        # With `mcv_first` on, or while the second MCV is due by its time (see `expansion`), the same rules in another
        # order: the MCV right after power and a lost core building, so that in the cycle its money is there it takes
        # the Vehicle queue and the money ahead of every other order.
        # While a growth refinery is due (`refinery_growth`), the refinery rule comes right before one more producer,
        # so that the refinery takes the Building queue first.
        mcv_first = _moved_after(self._rules, mcv, core)
        self._orders = {
            (False, False): self._rules,
            (True, False): mcv_first,
            (False, True): _moved_before(self._rules, held_refineries, producers),
            (True, True): _moved_before(mcv_first, held_refineries, producers),
        }

    def intents(self, world: World, ctx: MatchContext) -> list[Intent]:
        """The spending focus decided; the field reserved for the MCV's site and that site, the fields refineries were
        asked against, the creep target, the claim fields, a new yard's field, the fields own harvesters last harvested
        and the dry ones, a further MCV's site and the full hold published for this cycle, and whether a growth refinery
        is due and whether the ore is near the storage cap decided, then the orders of all the rules, in their order.
        """
        self._focus.decide(world, ctx)
        self._storage.note(world, ctx.tuning)
        self._forward.note_deploys(world, ctx)
        ctx.reserved_field = self._forward.reserved_field(world, ctx)
        site = self._forward.site_box(world)
        ctx.mcv_site = None if site is None else cells.nearest_cell(*site.center)
        self._publish(world, ctx)
        self._forward.publish_spent_fields(world, ctx, self._harvesters.dry_fields)
        self._forward.publish_repeat_site(world, ctx)
        is_due_by_time = is_second_mcv_due(world, ctx)
        self._decide_full_hold(world, ctx, is_due_by_time)
        self._refineries.decide_growth(world, ctx)
        is_mcv_ahead = is_mcv_first(ctx) or is_due_by_time
        rules = self._orders[(is_mcv_ahead, self._refineries.is_growth_due)]
        # While the canary alarm is in force no unit but a harvester owed and no optional building is ordered; with
        # the defense cap full its money goes to the army (see `defense_cap`): the units are not held.
        proposed = [intent for rule in rules for intent in rule.intents(world, ctx)]
        if ctx.canary_alarm and defense_cap.is_full(world, ctx):
            trains = [intent for intent in proposed if intent.order.get("cmd") == "train"]
            others = [intent for intent in proposed if intent.order.get("cmd") != "train"]
            orders = [*trains, *held_by_alarm(others, ctx)]
        else:
            orders = held_by_alarm(proposed, ctx)
        if is_mcv_ahead:
            self._full_hold.settle(ctx.production.pending(MCV) > 0, world.tick)
        return orders

    def _publish(self, world: World, ctx: MatchContext) -> None:
        """Publish the fields refineries were asked against, the creep target, the claim fields and the field a new
        yard claims a refinery for (`ForwardMcv.new_yard_field`, while the claim rules leave room) for this cycle.

        The creep target is the far field while the creep from the opening has one, the opening included, else the
        next field of the lane as the creep to the next field names it; with `mcv_repeat` on, a crawl target an MCV goes
        to is skipped first (`ForwardMcv.skip_crawl_target`). With `refinery_spread` on, a claim field that is taken is
        dropped first; the field reserved for the MCV's site is left out of the claim fields published.
        """
        self._placement.publish_asked_fields(world, ctx)
        tuning, claims = ctx.tuning.placement, ctx.tuning.upkeep.refinery_claims_reached
        next_patch = self._next_patch.target(world, ctx.terrain, ctx.production, tuning)
        self._forward.skip_crawl_target(world, ctx)
        self._far_field.skip_stalled(world, tuning, ctx.skipped_fields)
        far_field = self._far_field.target(world, ctx.terrain, tuning, ctx.skipped_fields, claims)
        ctx.creep_target = far_field or next_patch
        if tuning.refinery_spread and self._far_field.claim_fields:
            self._far_field.drop(placement.taken_fields(world, ctx.terrain, tuning, ctx.asked_refinery_fields))
        # The field reserved for the MCV's site is no claim field while it is reserved, and one again after.
        ctx.claim_fields = [field for field in self._far_field.claim_fields if field != ctx.reserved_field]
        # A claim that lasts until its refinery stands is due whatever the refinery caps say.
        until_standing = new_yard_refinery.is_on(ctx)
        has_claim_room = ctx.production.planned(REFINERY) < ctx.tuning.upkeep.claim_refineries_max
        ctx.new_yard_field = self._forward.new_yard_field(world, ctx) if has_claim_room or until_standing else None
        ctx.new_yard_cell = self._forward.new_yard_cell(world) if until_standing else None
        is_claim_held = until_standing and ctx.new_yard_field is not None
        is_other_claim_due = _is_claim_due(ctx) and not self._refineries.waits_unserved(world, ctx)
        ctx.refinery_claim_due = is_claim_held or is_other_claim_due

    def _decide_full_hold(self, world: World, ctx: MatchContext, is_due_by_time: bool) -> None:
        """With `mcv_first` on, or while the second MCV `is_due_by_time` (see `expansion`), publish whether the second
        yard's MCV is due first, which it is while it is wanted (`is_mcv_wanted`, whatever the time), and whether the
        full hold for its money is in force in this cycle (see `economy_hold`), untimed while it is due by its time;
        both false else."""
        if not (is_mcv_first(ctx) or is_due_by_time):
            ctx.mcv_first_due = ctx.full_hold = False
            return
        ctx.mcv_first_due = is_mcv_wanted(world, ctx)
        is_covered, upkeep = has_mcv_money(world, ctx), ctx.tuning.upkeep
        hold = self._full_hold.holds(ctx.mcv_first_due, is_covered, world.tick, upkeep, untimed=is_due_by_time)
        ctx.full_hold = hold


def _moved_after(rules: tuple[Rule, ...], moved: Rule, anchor: Rule) -> tuple[Rule, ...]:
    """The rules in their order, but `moved` right after `anchor`."""
    others = [rule for rule in rules if rule is not moved]
    after = others.index(anchor) + 1
    return (*others[:after], moved, *others[after:])


def _moved_before(rules: tuple[Rule, ...], moved: Rule, anchor: Rule) -> tuple[Rule, ...]:
    """The rules in their order, but `moved` right before `anchor`."""
    others = [rule for rule in rules if rule is not moved]
    before = others.index(anchor)
    return (*others[:before], moved, *others[before:])


def _is_claim_due(ctx: MatchContext) -> bool:
    """Whether a refinery claim is due: the refinery claimed for a new yard's field is (`ctx.new_yard_field`), or with
    `refinery_claims_reached` on there is a claim field and fewer than `claim_refineries_max` refineries stand or are on
    their way."""
    tuning = ctx.tuning.upkeep
    has_room = ctx.production.planned(REFINERY) < tuning.claim_refineries_max
    is_reached_due = tuning.refinery_claims_reached and bool(ctx.claim_fields) and has_room
    return is_reached_due or ctx.new_yard_field is not None
