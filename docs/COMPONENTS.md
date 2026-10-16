# Components

This page discloses the components that affect how the agent plays: what each
one does, where it is in the repository, and where its settings are. What
affects play but is not in the repository is listed under
[Excluded items](#excluded-items), with the reason it is left out and what
the repository says about it instead.

Every setting that affects play is in a tracked file under `config/`. The
agent only reads those files, and every run records the SHA-256 of each of
them (see `docs/RUNNING.md`), so the settings a match was played with can be
identified afterwards. Secrets are the one exception: they are in the
untracked `.env` and are listed under the excluded items.

| Component | Where it is | Settings |
|---|---|---|
| [Vendored starter runtime](#vendored-starter-runtime) | `src/altruagent/` | `.env` |
| [Entry point and dispatcher](#entry-point-and-dispatcher) | `src/agent/`, `src/ucla_agent/core/dispatch.py`, `src/ucla_agent/core/registry.py` | none |
| [Red Alert scripted core](#red-alert-scripted-core) | `src/ucla_agent/games/redalert/` | `config/redalert/tuning.toml`, `config/redalert/items.toml`, `config/redalert/glossary.toml`, `[redalert]` of `config/agent.toml` |
| [Compact shorthand](#compact-shorthand) | `src/ucla_agent/games/redalert/shorthand.py` | `model_commands` in `[redalert]` of `config/agent.toml` |
| [Werewolf](#werewolf) | `src/ucla_agent/games/werewolf/` | `config/werewolf/tuning.toml`, `[werewolf]` and the `werewolf_choice` and `werewolf_message` chains of `config/agent.toml` |
| [Pokémon VGC doubles draft](#pokémon-vgc-doubles-draft) | `src/ucla_agent/games/pokemon/` | `config/pokemon/tuning.toml`, `config/pokemon/data/`, `[pokemon]` and the `pokemon_choice` chain of `config/agent.toml` |
| [Included model server](#included-model-server) and its [model roles](#model-roles) | `src/ucla_agent/core/modelserver/` | `config/models.toml`, `[models]` of `config/agent.toml` |
| [Decision sources](#decision-sources), including [Jev](#jev) and [Claude](#claude) | `src/ucla_agent/core/decide/` | `[decide]`, `[decide.chains]`, `[jev]`, `[jev.fields]`, `[claude]`, `[claude.models]` of `config/agent.toml` |
| [Third-party components](#third-party-components) of the `jev9b` role: JevK5-9B, jev-style and jevk5 | Not in the repository; used by `scripts/serve_jev9b.py` | `[systemone_roles.jev9b]` of `config/models.toml`, `[systemone.jev9b]` of `config/agent.toml` |
| [Prompts](#prompts) | `src/ucla_agent/games/redalert/prompts/plan_choice.txt`, `src/ucla_agent/games/redalert/prompts/plan_choice_briefing.txt`, `src/ucla_agent/games/redalert/compact_prompt.py`, `src/ucla_agent/games/werewolf/prompts.py`, `src/ucla_agent/games/pokemon/decide.py` | `model_plan_choice`, `model_briefing` and `plan_prompt` in `[redalert]`, `[werewolf]` and `[pokemon]` of `config/agent.toml` |

**As shipped, no hosted service takes part in play, and two local models
run: the `strong` and `jev9b` roles of the included model server.**
`config/agent.toml` has `autostart = true` and `autostart_roles = ["strong", "jev9b"]`
in `[models]`, `model_plan_choice = true`, `model_commander = true` and
`model_commands = false` in `[redalert]`, and `enabled = false` in `[jev]` and
in `[claude]`. In Red Alert JevK5-9B (the systemone role `jev9b`, about 23 GB
of VRAM beside the `strong` role's 30 GB) chooses the R7 plan among the open ones and
commands the first scout, then main, through the `red_alert_plan` and
`red_alert_commander` chains (`["systemone:jev9b"]`), with the plan choice
briefing (`model_briefing = true`, `plan_prompt = "briefing"`); the script
checks every option it is offered, carries the chosen one out, and plays
its own choice when no answer comes within the deadline or `jev9b` is down.
The 4B model (`small`) is defined in `config/models.toml` but not used as shipped.
Every other order is the scripted core's own. With `model_plan_choice` and
`model_commander` set to `false`, Red Alert is script-only. In Werewolf, `model_choices` and
`model_messages` are `true` in `[werewolf]`: the agent asks the `strong` role
of the included model server for its choices and messages, and when that role
is down every such request fails at once and the script's choice or scripted
message is played (see [Werewolf](#werewolf)). In Pokémon, `model_choices` is
`true` in `[pokemon]` in the same way: the `strong` role may choose among the
script's best candidates, and without it the script's best is played (see
[Pokémon VGC doubles draft](#pokémon-vgc-doubles-draft)).

## Vendored starter runtime

| | |
|---|---|
| What it does | Signs in with the Official Agent Key, asks the platform for assigned games, starts one worker process per match, and runs each match's play loop: it reads the state, calls the agent for a move, and submits the move. |
| Where it is | `src/altruagent/`, a byte-for-byte copy of the `altruagent` package of the official starter kit. `src/altruagent/UPSTREAM.md` names the upstream commit and lists the SHA-256 of every file; `tests/test_layout.py` checks the files against that list. |
| Settings | `.env` (`ALTRUAGENT_CONTROL_URL`, `ALTRUAGENT_OFFICIAL_AGENT_KEY`). Nothing under `config/` changes how it runs. |

The agent uses the runtime through its public contract: the agent factory
`agent.agent:create_agent`, and the calls `choose_action`, `choose_message`
and `on_action_result` on the object that factory returns. There is one
exception, described next.

### The one attachment to a non-public part of the runtime

**What is attached to.** `altruagent.runner._terminal_game_state(last_state, result)`,
a module-level function of the vendored runner. Its leading underscore marks
it as internal: it is not part of the runtime's public contract.

**Why.** The runtime never calls the agent with a terminal state. When a match
ends, `altruagent.runner.run_game` reads the result itself, builds the final
state with `_terminal_game_state` and returns, and the worker process exits.
Without the attachment the agent would never learn a match's result, and its
match record could not hold one.

**How.** On a match's first call, in the worker process that plays that match,
`ucla_agent.core.records.start_match` calls
`ucla_agent.core.runtime_hooks.install`. That replaces the attribute
`altruagent.runner._terminal_game_state`, in memory only, with a function that

1. hands the result to `ucla_agent.core.records.finish_match`, which runs the
   end-of-match summaries, writes the `result` event and closes the match
   record, and then
2. calls the original function with the same arguments and returns its value
   unchanged.

An exception raised in step 1 is caught and printed to standard error; step 2
always runs, so the runtime always gets the value it would have got without
the attachment.

**What it does not do.** No file under `src/altruagent/` is modified: the
replacement exists only in the memory of the process that plays the match.
It sends nothing to the platform, and it changes no decision, move or message
of the agent. Its only effect is that the result is written to the match
record.

**When the function is missing.** If a runtime version has no
`_terminal_game_state`, nothing is replaced. The agent writes one `error`
event of type `handled` with `where = "runtime_hooks"` to the match record and
plays on. That match's record then ends with a `match_end` event where the
result would be, and the report tool shows its outcome as `no_result`.

## Entry point and dispatcher

| | |
|---|---|
| What it does | `python -m agent` reads the command line (`--match`, `--tournament`, both, `--check-tournament`, `--agent`), takes the single-instance lock, records the start and stop of the run, and hands over to the runtime. In each match's worker process the runtime builds the agent with `agent.agent:create_agent`, which returns a new dispatcher. The dispatcher opens the match record on its first call, looks the match's game type up in the registry, and from then on passes every call to that game's decision-maker. It decides nothing about play itself. |
| Where it is | `src/agent/` (the entry point `src/agent/__main__.py`, derived from the starter kit's own, and the factory `src/agent/agent.py`), `src/ucla_agent/core/startup.py` (the run around the runtime), `src/ucla_agent/core/dispatch.py` and `src/ucla_agent/core/registry.py`. |
| Settings | None of its own. The flags are described in `docs/RUNNING.md`. |

The registry has two games: `red_alert`, played by
`ucla_agent.games.redalert.agent.RedAlertAgent`, and `werewolf`, played by
`ucla_agent.games.werewolf.agent.WerewolfAgent`. A match of any other game
type is answered by a stand-in that plays the first legal action (or waits
when the game lists none), closes every messaging round and writes one
`unsupported_game` event; it exists so that a mistaken assignment does not
stop the agent's other matches, and it is not competitive play.
`docs/GAMES-STATUS.md` gives the state of each game.

**Memory.** Each match is played in its own worker process, by a new
dispatcher and a new decision-maker. Nothing learned in one match is kept for
the next: the agent writes match records but never reads one back.

## Red Alert scripted core

| | |
|---|---|
| What it does | Plays `red_alert` from the first state to the last without asking anyone. On every fresh state it brings its picture of the match and its account of the orders it has sent up to date, lets six managers propose orders in a fixed order (base defense, upkeep, opening, scouting, combat, raids), and packs what they propose into one batch the platform will take, or waits. The batch's reasoning summary is the plan in force and the number of orders of each command. |
| Where it is | `src/ucla_agent/games/redalert/`. The decision-maker is `ucla_agent.games.redalert.agent.RedAlertAgent`. |
| Settings | `config/redalert/tuning.toml` (every threshold a rule compares against), `config/redalert/items.toml` (item facts used when a match's own game reference leaves them out), `config/redalert/glossary.toml` (what each unit and building type is, in plain words, for the plan choice briefing), the `[redalert]` and `[redalert.sparring]` tables of `config/agent.toml`, and `history_picks` of its `[models]` table (how many of a model's own recent picks each question shows). |

The parts and where each one is:

| Part | What it does | Where it is |
|---|---|---|
| Control loop | One cycle per state; a manager that fails is left out of that cycle, anything else that fails is answered with a wait. | `src/ucla_agent/games/redalert/agent.py`, `src/ucla_agent/games/redalert/managers.py` |
| World model | What the states show and what they no longer show: the remembered enemy (one out of sight kept as last seen, a unit marked stale after 30 seconds; an enemy forgotten in the update whose reported kills account for every enemy that left sight, so a unit killed in sight never counts as a live enemy, and a building also once an own unit has stood near its cell for 2 seconds), the summary of the enemy the rules read, the terrain of the map. | `src/ucla_agent/games/redalert/world.py`, `src/ucla_agent/games/redalert/scout_report.py`, `src/ucla_agent/games/redalert/terrain.py` |
| Orders | The ledger of every order from sending to outcome (no production order repeated before the first shows), the check of each order against the current state, the packing into one batch within the platform's limits, and the filter that drops orders that would change nothing. | `src/ucla_agent/games/redalert/ledger.py`, `src/ucla_agent/games/redalert/batch.py`, `src/ucla_agent/games/redalert/arbitration.py` |
| Opening | A fixed build, as shipped the barracks-first one (power, barracks, two refineries, power, war factory; the factory-first build, selectable, is power, barracks, refinery, war factory, refinery, service depot, a power plant between them only when power is short), a home guard of rifle infantry (under factory-first one rifleman scout until the depot stands), one cheap defense against infantry and cover against armor; behind switches that ship on, both defenses only against a rush the watch tells or a threat seen, and a lean guard of three rifles until a rush is told. | `src/ucla_agent/games/redalert/opening.py` |
| Upkeep | Placement, power, rebuilding what the opening built, harvesters and further refineries, keeping the unit queues busy, the service depot, repair, rally points, and switching consumers off when power runs short; behind switches that ship on, the base's walk from ore field to ore field from the opening on, a refinery for each field reached and one to a field first, the move of harvesters off a dry field spread by load, a second construction yard and further ones on safe new ground, more war factories sooner with the Vehicle queue kept filled, no army infantry but a few rocket soldiers after the first war factory, the unit queues waiting for the economy's next item, defenses bought only against a real threat, and less power to spare; always, money banked spent on the second war factory and on an empty Building queue, and ore near the storage cap on up to six war factories with no economy hold; behind a switch that ships on, no war factory beyond the first until the vehicle income keeps those standing 80 percent busy, and behind another, from 180 s one more war factory whenever it does. | `src/ucla_agent/games/redalert/upkeep.py`, `src/ucla_agent/games/redalert/placement.py`, `src/ucla_agent/games/redalert/production.py`, `src/ucla_agent/games/redalert/creep.py`, `src/ucla_agent/games/redalert/fresh_ore.py`, `src/ucla_agent/games/redalert/expansion.py`, `src/ucla_agent/games/redalert/tank_factories.py`, `src/ucla_agent/games/redalert/economy_hold.py`, `src/ucla_agent/games/redalert/bank_spending.py`, `src/ucla_agent/games/redalert/war_factory_load.py` |
| Base defense | Notices an attack on the base the moment it starts and answers it in the same cycle; shelters harvesters; behind switches that ship on, answers a raid on a far harvester with main alone and leaves main on the attack unless a threat outweighs the guard and the defenses. | `src/ucla_agent/games/redalert/base_defense.py` |
| Scouting | Behind switches that ship on, two designated scouts kept the whole match, steered round danger with a wide berth and a cap on what they spend, taking the script's missions, and a watch that tells an early rush; with the slots off, one scout at a time to the enemy's start and around its base, sent again whenever the enemy base, its army or a far ore field has gone unseen too long, by the indirect way past the ore fields behind a switch that ships off. | `src/ucla_agent/games/redalert/scouting.py`, `src/ucla_agent/games/redalert/rescout.py`, `src/ucla_agent/games/redalert/sight.py`, `src/ucla_agent/games/redalert/scout_route.py`, `src/ucla_agent/games/redalert/rush_watch.py` |
| Combat | The army gathers at a staging point, goes forward only as a whole and when strong enough for the plan, and engages or retreats on the estimate of the fight; behind switches that ship on, main's screened advance marches while no enemy is near, and under FINISH it keeps one target, the enemy's production first, until the target is lost, and setting out on an objective the enemy holds it stops just out of the enemy's reach until its body and its artillery are there, its joining units meeting it there, and an enemy MCV or yard near our buildings or harvested fields, or an enemy defense whose reach takes in a refinery, is struck at once by the fewest of main's units worth 1.5 times it, which no plan change or commander pick recalls until it is destroyed; behind a switch that ships off, under HOLD it stands at a forward point on the line toward the enemy, fights what comes near it there and falls back when outmatched; and behind a switch that ships on, main sends no order into the coverage of enemy defenses it cannot beat, going on an uncovered attack point instead, shelling a lone defense, or sieging from beyond the coverage with its tanks screening behind. | `src/ucla_agent/games/redalert/combat.py`, `src/ucla_agent/games/redalert/strike.py`, `src/ucla_agent/games/redalert/forward_staging.py`, `src/ucla_agent/games/redalert/stepping.py`, `src/ucla_agent/games/redalert/fight.py`, `src/ucla_agent/games/redalert/targeting.py`, `src/ucla_agent/games/redalert/groups.py`, `src/ucla_agent/games/redalert/forward_hold.py`, `src/ucla_agent/games/redalert/finish_target.py`, `src/ucla_agent/games/redalert/defended.py` |
| Raids | A small fast group hits the enemy's harvesters and economy where no remembered defense covers them, by a way around the defenses, and breaks off at once when the fight turns; behind a switch that ships on, RAID is open only while a raid could set out now. | `src/ucla_agent/games/redalert/raids.py`, `src/ucla_agent/games/redalert/raid_targets.py`, `src/ucla_agent/games/redalert/raid_routes.py`, `src/ucla_agent/games/redalert/coverage.py` |
| Plans | The plan selector, by rules R1 to R7, and what each plan produces. | `src/ucla_agent/games/redalert/plans.py`, `src/ucla_agent/games/redalert/composition.py` |
| Plan choice briefing | What a model choosing the R7 plan reads with `model_briefing` on: the match in plain words, in six sections, and a glossary line for each type it names. | `src/ucla_agent/games/redalert/briefing.py`, `src/ucla_agent/games/redalert/glossary.py` |
| Compact plan prompt | What a model choosing the R7 plan reads with `plan_prompt = "compact"`: a fixed system text, the state as `key=value` lines and a line for each open plan, at most 1,500 characters, answered with the choice first. | `src/ucla_agent/games/redalert/compact_prompt.py` |
| Clock play | Money left at the end is spent on defenses and fighting units; a lead is kept behind defenses. | `src/ucla_agent/games/redalert/clock.py` |
| Item facts and thresholds | The item table (the match's game reference over `config/redalert/items.toml`), the damage table of `config/redalert/damage.toml`, whose ground range a weapon range of the game's rules replaces unless it equals the type's anti-air range and not its ground range (the rules name one weapon a type, and the rocket soldier's is its anti-air rocket: it reaches ground targets at 5 cells and aircraft at 7.5); a unit's range in the item table follows the same rule, so the two tables give the rocket soldier, the flak truck and the Longbow the same ground range, while a defense keeps the range its rules give; and the thresholds of `config/redalert/tuning.toml`. | `src/ucla_agent/games/redalert/items.py`, `src/ucla_agent/games/redalert/tuning.py` |

**Mod type names.** The game fields variants of a type under names of their
own, and the item and damage tables look such a name up as the type it is a
variant of when they do not list it: a listed name followed by `r` and a
number (the paratroopers `e1r1` and `e3r1`), or a listed name followed by a
dot and any suffix (`<base>.<anything>`), which is how a mod names its own
versions of the stock types. In tests against the FransBots the enemy's
`harv.frans`, `e6.frans`, `mh60.frans` and `heli.frans` were logged as
UnknownItemType and valued at 0 (`tmp/diag-i31-maps`); they now have the
facts, the cost and the armour of `harv`, `e6`, `mh60` and `heli`. The world
reads every actor, own units and buildings and remembered enemies, under the
listed name a dotted variant stands for (`ItemTable.base_name`), so that
`harv.frans` is the enemy harvester `harv` to every rule that compares type
names: not a combat unit, and a harvester wherever a rule looks for one (the
scout report's enemy harvesters among them). A dotted name whose part before
the dot is not listed (`camera.spyplane`, `zzz.frans`) stays unknown, and a
name that merely begins like a listed one (`camera` and the cruiser `ca`) is
no variant; the stock names and the `r` variants are read as before, and no
stock type changes value.

A type the item table cannot value, neither listed nor a variant of a listed
type (the FransBots' technicians `tecn` and `tecn2`), is logged once per
match as UnknownItemType and is worth 0 to every rule that weighs actors but
the fight: in the estimate of a fight and its ratios (`estimate`, `weigh`,
`ratio_against`, `ratio_against_target`) an enemy unit of such a type counts
at `unknown_unit_value` of `[combat]` (300, a whole number of at least 1)
times its share of health, in neither the infantry nor the vehicle class of
the matchup factor, and the damage table gives it armour none, which is what
infantry wears, so a rifle hurts it. Two medium tanks against a technician
at full health and one at half health weigh 1,700 against 450. The production
and targeting value tables, `worth` and `strength` keep valuing it at 0.

The plan selector sets the plan in every cycle; the first rule that applies
wins:

- R1: OPENING until a war factory has stood.
- R2: PRESERVE in the last three minutes with a positive trade balance.
- R3: HOLD in the last three minutes with an even balance.
- R4: PRESS in the last two minutes with a negative balance. A late-PRESS
  guard (`late_press_guard`, on as shipped) keeps R4 shut while base
  defense reports a major threat on the base.
- R5: STRIKE, an early attack (started in the first four minutes) on a
  scouted base that has no defense and little army, given up for good the
  moment a defense is seen or the fight turns. An early-attack guard
  (`early_attack_guard`, on as shipped) keeps R5 shut for the whole match,
  so STRIKE is never chosen.
- R6: FINISH, the attack that ends the match: from the eighth minute when the
  army is well above the enemy's, its defenses counted twice; or from the
  sixth minute when enemy buildings are known but no enemy defense is, no
  enemy fighting unit has been seen for a minute and main is worth
  `cleanup_min_strength` of `[plan_rules]` (8,000) or more (the cleanup,
  R6(b)). With `finish_over_preserve` (on as shipped) R6 is checked before
  R2 and R3 in the last three minutes, so a winning FINISH is not turned to
  PRESERVE, and a late FINISH stays until R6 has not applied for 250 ticks.
- R7: otherwise the discretionary choice among HOLD, RAID and PRESS. The
  script's choice prefers RAID, then PRESS, then HOLD, among those open.
  With the early-attack guard on, PRESS is not open before the ninth minute
  until the enemy has committed its army: a major threat on the base beaten
  off with no core building lost, or 1,500 of value killed.

With the early-attack guard on, every comparison of the own army with the
enemy's (R6's, PRESS's and the script's ratio) counts only the own units that
fight, each at its cost, and not the harvesters or the MCV.

The cleanup's main worth is 8,000, built in and in the tracked file, and the
comparison is inclusive: main worth exactly 8,000 starts it, 7,999 does not.
A cleanup starts on what the enemy was last seen with, which can be stale: on
seed 1001 of tmp/diag-i33 one started at 6:00 with main worth 6,500 against
an army nobody had seen, failed, and the game was lost by elimination. With
8,000 and `war_factory_on_load` off, the 18 seeds of tmp/var-A.txt won 12
(normal 5 of 6, rush 6 of 6, horde 1 of 6), against 8 with the trigger on
from 6:00 (tmp/var-B.txt) and 13 for the iteration-31 configuration, a
one-game difference within the noise of 18 seeds.

A big army that waits for an enemy army nobody has seen idles while the enemy
grows its own: in game base-england-sidestep-1025 main stood at home under
HOLD from 13:18 to 18:00 with an army of 20,000 to 41,000, because an enemy
army unseen for two minutes is assumed as strong as the own (a ratio of 1),
which keeps PRESS shut. So while main is worth `big_army_value` (15,000), no
enemy unit that fights on the ground has been in sight for
`unknown_army_seconds` (120) and the enemy base is known, the unseen enemy
army is weighed at main's worth over `big_army_assumed_ratio` (1.5) instead
(keys of `[plan_rules]`, built in and in the tracked file), and the script's
choice presses whenever PRESS is open on that. Every other condition of PRESS
still holds: an enemy base not found never opens it, the clock rules come
first (a late lead is kept by R2's PRESERVE, an even balance by R3's HOLD), a
major threat holds, and main keeps out of a fight under enemy defenses
(`defended_redirect`). A ratio of 0 weighs nothing from main. The inputs of a
`plan_change` event carry `enemy_army_unseen_ticks`. With `scout_missions` of
`[scouting]` on, the scouts then look for that army, its base known or not:
at most once every 60 seconds the mission planner forces the attack lane and
the edge of the enemy's main, which the scouts are given before any other
mission, whatever the scores, and keep until they have watched them, each a
`scout_slot` event `destination` with the reason `refresh`. With
`scout_missions` off only the plan rules change.

Every change of plan is written to the match record as a `plan_change` event
with the rule that made it, and each block by a guard as one
`plan_rule_blocked` event: a block of R4 under a major threat with the reason
`R4_base_threat`, a block of STRIKE or of an early PRESS with the reason
`early_attack_guard`.

The configuration the repository ships, `config/redalert/tuning.toml` and
the built-in values equal to it, turns on the script switches and plays the
values of a watched game of iteration 19: `runs/watch/20261012T091508Z` of
the harness, in which the agent, as England against the Normal bot's Russia
on the sidestep map, won by a trade balance of +35,500 at the 20-minute
limit. Earlier watched games had growing parts of the same set on: as Ukraine
against France the agent won by +14,150, as France against England it lost
by 9,950, and as Russia against Russia it won by +11,300. No benchmark
measured the set, by the user's direction, and where a switch below gives a
measured result, the set ships it on all the same. The winning game also let the 4B model `small`
choose the R7 plan (`model_plan_choice` of `[redalert]` on, with the compact
plan prompt below); its one model plan decision was HOLD. The shipped
configuration plays the set with JevK5-9B (`jev9b`) choosing the plan and
commanding (see the `[redalert]` settings below). The set:

- `[opening]`: `defense_on_threat` and `lean_infantry`.
- `[upkeep]`: `mcv_first`, `refinery_growth`, `fresh_ore_by_load`,
  `no_army_infantry`, `defenses_on_threat_only`, `economy_first`,
  `lean_power`, `refinery_claims_reached`, `refinery_field_cap` and
  `harvesters_to_fresh_ore`, with `refineries_max` and `refineries_max_late`
  at 6 and `harvesters_max` at 12 (it ships at 22 now, with the harvester
  target below).
- `[producers]`: `tank_factories`.
- `[placement]`: `refinery_spread`, `creep_stall_skip`, `second_line_local`,
  `creep_from_opening`, `creep_continuous`, `refused_detection` and
  `remote_yard_growth`.
- `[expansion]`: `mcv_safe_site`, `mcv_new_area`, `mcv_reserve_field`,
  `mcv_repeat`, `mcv_short_escort` and `second_yard`.
- `[mix]`: `main_force = "tanks"`, `max_share` 95 and the tank rows. The
  tank rows that ship are the army build below: from the service depot on,
  12 tanks : 4 artillery or V2s : 8 rocket soldiers by count.
- `[base_defense]`: `keep_attack_army` and `harvester_group_response`.
- `[scouting]`: `scout_slots`, `scout_missions`, `scout_wide_berth`,
  `scout_spend_cap` and `rush_detection`, with `soviet_scout_unit = "dog"`.
- `[combat]` `sticky_attack_target`, `[siege]` `march_when_clear`, `[raids]`
  `launchable_only` and `[plan_rules]` `model_plan_hold`.

The factory-first opening. `order = "factory_first"` of `[opening]` is
selectable and ships off: on the real harness's stepped set (36 games each,
same seeds, build 803d0fc, models off) it won 22 of 36 against 28 for the
barracks-first build (horde 3/12 against 7/12, tech 4/6 against 6/6, base
4/6 against 5/6, rush 5/6 against 4/6, normal 6/6 each), so
`order = "barracks_first"` ships. The Building queue makes a power plant,
the barracks (`tent` / `barr`), a refinery at the home field, the war
factory, the second refinery and the service depot; the power-first rule
puts a power plant ahead of any step that would leave the power short (in
the fake game, between the war factory and the second refinery). The build
is complete once the depot stands, so upkeep's rules after the opening (the
lost core buildings, which hold one power plant under this order, the depot
rush of the tank force, the second yard's depot, one more producer, the
extra refineries and the kennel) wait for it. The opening's only infantry
until then is one rifleman, trained as soon as the barracks stands: the
first scout slot takes it as the first infantry produced, and the rush
scout's pass and exit post steer it. As soon as the war factory stands, the
scout slots order one jeep (Allies, whatever
`allied_fast_scout_after_seconds` says) or one flak truck (Soviets, whatever
`soviet_scout_unit` says) for the second slot, or for the first if the
rifleman was lost, and the scouting manager claims the Vehicle queue for it
from the first cycle, so that it is the queue's first order: harvesters past
one a refinery wait for it, and main's vehicles wait for the depot anyway.
Until it comes no infantry takes a slot but the first, and until the depot
stands no slot orders a rifleman. Base defense's counter and the rush
counter's infantry are not held back. The home guard's rifles start once
the depot stands; the shipped dog rule upgrades the rifleman once a kennel
stands. With `single_scout_rifle` of `[opening]` on (it ships off: 0/12 on
against 12/12 off on the raid and air seeds), the same holds under every order: once a barracks stands, one rifleman, the first
scout (replaced once by the slots if lost before 300 seconds), and no other
infantry of the opening's or for the slots, the second slot waiting for the
jeep or the dog, until a rush warning or verdict, a threat at the base or
the depot standing; the rocket cover, base defense's counter and the rush
counter's infantry are never held, and a shorthand `tr` line for infantry is
refused meanwhile.

The large map's economy-first build. With `large_map_economy_first` of
`[opening]` on (as shipped), the opening decides once, in the first cycle in
which both the base's center and the enemy's start are known, how far apart
the bases are: the straight distance in cells, never the map's name. With
more than 70 cells (`large_map_base_cells`) the map is large, and the
barracks-first, refinery-first and reach-first orders build a power plant,
two refineries, the barracks (`tent` / `barr`), a second power plant and the
war factory; the build is complete when the war factory stands, as before.
The factory-first build, and every build on a map of 70 cells or less, is
as before. The pool's base distances are 52 (sidestep), 57 (trapped) and 88
(shadowfiend-ii): on shadowfiend-ii the enemy grows its economy instead of
rushing, and the early barracks with its rifles held the second refinery
back. While the barracks neither stands nor is on its way, a rush warning,
the rush verdict `rush` or a threat at the base that is not of aircraft
alone pulls the barracks forward for the match: the build is the
barracks-first one from then on, its steps already standing passed over.
The decision is one `opening_map_size` event (`distance` to a tenth of a
cell, `large`), and a pull a second one with `pulled` true. Rush detection,
the rush counter and the infantry are unchanged. Off, the build is the
configured order's on every map.

    [opening]      large_map_economy_first = true
                   large_map_base_cells = 70

Watch it off with:

    python scripts/watch_game.py --set tuning:opening.large_map_economy_first=false

and follow the decision in a match record with:

    grep '"opening_map_size"' runs/<run>/<match folder>/events.jsonl

`scripts/watch_game.py` lets a model choose the plan unless `--model none`
is given, so the agent as it ships is watched with

    python scripts/watch_game.py --model none

and each switch of the set can be watched off, for example with

    python scripts/watch_game.py --model none --set tuning:scouting.scout_slots=false

The paragraphs below say what each one does.

Two sets of switches of `config/redalert/tuning.toml` were measured against
the built-in bots, and with them on the agent won fewer games at a lower mean
kd (`docs/BENCHMARKS.md`, "Iteration 12"). All of them but the main force
ship off:

- The economy switches. `order` of `[opening]` set to `"reach_first"` would
  add one more power plant right after the barracks, placed toward the ore
  field, whenever no refinery can stand against that field as the barracks
  is on its way; `refinery_field_by_reach` of `[placement]` would send each
  refinery to the nearest unserved field it can stand against now, else to a
  near field it can share, else to the nearest unserved field; and
  `creep_toward_ore` of `[placement]` would place every other building but a
  defense nearest the ore while a refinery is due and no place for it reaches
  its field, so that the base creeps toward the ore. As shipped, the opening
  is the barracks-first build above, and the base's growth switches below
  place the buildings and the refineries.
- The army switches. `main_force` of `[mix]` set to `"tanks"` builds the main
  force from the tank rows, with the service depot rushed once the war
  factory stands. It ships on, with the army build below; the
  tank rows measured, light tanks for the Allies before the service depot,
  mostly tanks from it on and infantry 20 points or more in every row, lost
  with the other army switches. `standing` of `[raids]` would keep a raid
  group at all times, filled first and sent out alongside the main army under
  every plan after the opening but PRESERVE; `allied_raid_light_tanks` of
  `[composition]` would make the Allied raid light tanks only,
  `raid_vehicles` of them; and `soviet_raid` of `[composition]` set to
  `"apc_rockets"` would make the Soviet raid APCs carrying rocket soldiers, or
  set to `"none"` give the Soviets no raid. As shipped, the raid,
  `raid_vehicles` light vehicles for the Allies and grenadiers for the
  Soviets, is sent out under RAID and PRESS only.

The army build. Light tanks are only for raiding; the medium tank (Allies)
and the heavy tank (Soviets) are the bread and butter, with artillery or V2s
to back them up and rocket soldiers (e3) against aircraft and for fire
support, as the user directed: tanks to artillery or V2s at 3 : 1 and
tanks to rocket soldiers at 3 : 2, so 12 : 4 : 8 by count. The rows are
points of the army's value, and the unit queues train the item the army is
furthest short of by value, so each row's points are the value of 12 tanks,
4 artillery or V2s and 8 rocket soldiers at their costs: Allies 12 x 850,
4 x 850 and 8 x 300 of 16,000 credits; Soviets 12 x 1,150, 4 x 900 and
8 x 300 of 19,800; at the stek stage the Soviet tanks split 2 : 1 between
the heavy and the Mammoth tank, 8 x 1,150 and 4 x 2,000 of 23,200. The tank
rows of `[mix.tanks.<family>.<stage>]` ship as below; every row adds up to
100 and no share is above `max_share` (95). Not measured.

    stage   Allies                       Soviets
    base    e3 5, 1tnk 95                e3 5, ftrk 95
    fix     e3 15, 2tnk 64, arty 21      e3 12, 3tnk 70, v2rl 18
    dome    e3 15, 2tnk 64, arty 21      e3 12, 3tnk 70, v2rl 18
    stek    (the dome row)               e3 10, 3tnk 40, 4tnk 34, v2rl 16

In the shipped whole fake match the units trained from the service depot to
the last 180 seconds come out Allies 26 medium tanks, 8 artillery and 16
rocket soldiers, Soviets 21 heavy tanks, 7 V2s and 13 rocket soldiers:
within 8 percent of 12 : 4 : 8.

The rest of the build, all for a main force of the tank rows:

- **Rockets uncapped.** With `no_army_infantry` on, once a war factory
  stands the Infantry queue trains no rifles, but the rocket soldiers of the
  mix are trained for their share of the army's value (see
  `no_army_infantry` below), not capped at `rockets_kept`.
- **Mechanics** (Allies). One for every 3 of main's tanks
  (`mechanics_per_tanks` of `[composition]`), up to 3 (`mechanics_max`), all
  mobile (`mechanics_base` 0): none at 2 tanks, 1 at 3, 2 at 6 and 3 from 9
  (see the support units below).
- **The depot rushed.** The service depot is the next building after the war
  factory: it is ordered as soon as the war factory stands and the Building
  queue is free, whatever the money and the harvesters. Only a power plant
  the power target calls for, a lost core building saved for and a refinery
  claim due (the refinery `economy_first` has committed to) keep their place
  ahead of it; it is not started under a major threat. Until it stands, the
  unit queues are given nothing new while the money does not cover its price
  (1,200) and the reserve, but under a major threat.
- **Anti-air scaled to the aircraft seen** (`[anti_air]`). The rocket squads
  of the mix answer a few aircraft; the extra answer starts only while at
  least `aa_min_aircraft` (3) enemy combat aircraft have been seen in the
  last `aa_window_seconds` (180), each counted once by its id, those shot
  down included. A helipad or airfield seen calls for nothing extra. The mix
  then adds a rocket squad for every `aa_aircraft_per_squad` (2) aircraft
  beyond `aa_free_aircraft` (2), up to `aa_squads_max` (3): `aa_squad_points`
  (8) more points to the rockets for each, the row then scaled back to 100,
  which adds 4 to 5 rockets to the 8 of 12 tanks. The base keeps
  `aa_defenses` (1) AA defenses once a radar stands, and `aa_defenses_many`
  (2) from `aa_many_aircraft` (6). So 1 aircraft adds nothing, 4 add one
  squad and one AA defense, 7 two squads and two AA defenses. With no
  aircraft seen for 180 seconds the extra squads and defenses are no longer
  wanted. An enemy combat aircraft that hits a remote base (`remote_base_aa`,
  on: a base cluster without the main yard whose center is
  `remote_base_cells` (15) or more from it, or with a yard of its own) gets
  one AA defense placed within 4 cells of that base's buildings
  (`remote_air`), the radar first with none standing and 5 rockets sent
  there meanwhile, apart from the count, the reserve and the holds, at most
  one a base, and canceled when the aircraft is destroyed first with no
  other near in 30 s; each mark, order and cancel is an `anti_air` event of
  reason `remote_base_attack`.

The Soviet infantry counter. `soviet_infantry_counter` of `[composition]`,
on, answers massed infantry on the Soviet tank rows from the dome stage on.
While infantry make up at least `enemy_infantry_share` (0.6) of the enemy
units that fight seen in the last 120 seconds, counted by unit, the heavy
tank gives `flamers_share` (20) points to the flamethrowers (e4) and
`v2_infantry_bonus` (15) more to the V2s. A flamethrower needs a flame tower
(ftur), and nothing builds one for it: while e4 is not on offer its 20
points go to the V2s as well. Every point comes from the heavy tank, so the
row still adds up to 100 and no share passes `max_share`; against 30 rifles
and 5 tanks the dome row is e3 12, e4 20, 3tnk 35, v2rl 33 with a flame
tower and e3 12, 3tnk 35, v2rl 53 without. The counter comes after the value shifts below.
While it holds, `no_army_infantry` lets the Infantry queue train the
flamethrowers up to their share of the army's value once the rocket
soldiers are kept. Against an army mostly of vehicles the Soviet rifles
give their points to the rocket soldiers (e3) alone, never the grenadiers
(e2). Not measured; it ships on, after the Soviet game of diag-i26-soviet,
where the enemy fielded mostly infantry and all 18 heavy tanks died. Its
keys, as shipped:

    soviet_infantry_counter = true
    enemy_infantry_share = 0.6
    flamers_share = 20
    v2_infantry_bonus = 15

- The stages. The stage is the highest whose buildings stand: base, fix (a
  service depot), dome (a radar too) and, above them all, stek (a tech
  center). Only the Soviet tank rows have a stek row, and the loader asks
  for it there and refuses it anywhere else; every other set of rows, the
  Allied tank rows and the mixed rows, plays its dome row once a tech center
  stands. Artillery and V2s are on offer only once a radar stands, so at the
  fix stage their points go to the main tank. From the depot on the rows
  hold rocket soldiers alone of the infantry. The shifts work as for every
  row: against an infantry army the main tank gives 10 points to the rifles
  and, at the dome and stek stages, 5 more to the artillery or V2s; against
  more than a few aircraft the rockets gain their squads and the Soviet main
  tank gives `air_flak_points` (0) to the flak truck. The Mammoth tank (4tnk) is not on the
  list of items the mix never builds: only the stek row names it, and it is
  on offer once a service depot and a tech center stand.
- No main vehicle before the depot. `main_vehicles_need_depot` of
  `[composition]`, on, has the Vehicle queue train no vehicle of the army
  mix while no service depot stands, with `main_force = "tanks"` (the mixed
  rows are left as they are). The base rows' light tanks and flak trucks are
  so never built for main: light tanks come only through the raid quota,
  which still takes the row's vehicle share, and harvesters and MCVs come
  from their own rules. The infantry keeps its rules (`no_army_infantry`
  among them), and the money not spent on main goes to the early service
  depot and the harvesters. Off, the base rows' vehicles are built before
  the depot.
- The Soviet tech center. `soviet_tech_center` of `[producers]`, on, has a
  Soviet base order one tech center (stek) once 2 or more war factories
  stand and the spendable money (cash and ore) has stayed at least
  `soviet_tech_min_money` (4,000) in every state of the last
  `soviet_tech_seconds` (30). The stek needs its radar to be on offer, and
  is ordered only while none stands or is on its way, the Building queue is
  free, no full hold for the second yard's MCV is in force and no money is
  saved for a lost core building. It goes through the power target (a power
  plant first when it calls for one) and placement like any building. Each
  order is an `economy` event with the tick, the reason `soviet_tech`, the
  item (`stek`, or the power plant ordered first) and the money. Once the
  tech center stands, the stek row brings the Mammoth tank into the mix.

Watch a game with main's vehicles trained before the depot, and one without
the Soviet tech center:

    python scripts/watch_game.py --model none --set tuning:composition.main_vehicles_need_depot=false
    python scripts/watch_game.py --model none --set tuning:producers.soviet_tech_center=false

Five switches of the base's growth were measured too. Each of them, and each
switch of scouting, the opening's defenses and the army's hold below, was
measured on 144 games, Russia and England against the Rush and the Normal
bot, against the same games with every measured switch off, which won 97 at
a mean kd (trade balance) of 12,835 ([BENCHMARKS.md](BENCHMARKS.md),
"Iteration 13"). Only `rescout` won more games at a higher mean kd.
`creep_next_patch` ships off, and can still be watched in a game with it on:

    python scripts/watch_game.py --set tuning:placement.creep_next_patch=true

The other four are in the watched game's set and ship on:

- `creep_next_patch` of `[placement]`, off, would have the base walk from its
  home ore field to the next field in the lane toward the enemy, field after
  field: once the home field has two refineries, standing or on their way,
  every building but a defense is placed after the opening nearest the
  target field's ore, on any free place next to a building that stands, and
  a refinery against it once one can stand there. Each such placement is a
  `creep_placement` event with the reason `next_patch`. Measured: 94 wins,
  mean kd 9,181.
- `refinery_field_cap` of `[upkeep]`, on, gives no ore field a third
  refinery: the third and further ones go to a field under the cap they can
  stand against, such as the creep target, or wait, and each is a
  `refinery_field` event with `cap_held`. Measured with `creep_next_patch`
  on: 92 wins, mean kd 9,790.
- `harvesters_to_fresh_ore` of `[upkeep]`, on, takes a field whose
  harvesters keep standing idle by their refinery for dry and sends them,
  four to a field at most, to the nearest field with no remembered defense or
  enemy army near it, each move a `harvesters_moved` event. Measured: 99
  wins, but a mean kd of 9,380.
- `second_yard` of `[expansion]`, on, orders a service depot from 240
  seconds, then one MCV, and takes the MCV with an escort of main units to
  the creep target's field and deploys it there, at home after waiting too
  long; each step is an `expansion` event. Measured with `creep_next_patch`
  on: 85 wins, mean kd 5,881.
- `tank_factories` of `[producers]`, on, brings the 2nd to the 4th war
  factory sooner, on gates of their own: from 300 seconds
  (`tank_factories_from_seconds`), with the Vehicle queue busy 60 percent of
  the last minute (`tank_factories_busy_share`) and an income of 90, 120 and
  150 a second (`tank_factories_income`), still no more than
  `max_war_factories` and only when the investment test passes. One more war
  factory is then asked before one more barracks, and still waits for the
  power target and while the money is saved for the third refinery. The
  game makes one Vehicle queue faster for each war factory, not a queue for
  each, so the switch keeps that queue filled: up to
  `tank_factories_queued` vehicles of the army mix, 2 as shipped, as the
  unit budget pays for them, never through `set_primary`. Once two war
  factories stand, vehicles take at least 60 percent of the unit spending
  when the money pays for one unit queue only. Measured: 82 wins, mean kd
  6,311, and 9 games fewer for Russia against the Rush bot. Watch it off with
  `--set tuning:producers.tank_factories=false`.

With the growth switches off, the base grows around its first construction
yard, refineries are ordered whichever field they would serve, idle
harvesters are sent back to harvest wherever they find ore, and the Vehicle
queue is given one vehicle at a time.

The producers also spend money the base banks, by two rules that are not
switches, are not measured and always apply; their four keys of
`[producers]` ship with the values below. Money here is the cash and the
ore together, less `production_cash_reserve` of `[upkeep]` (300).

- The second war factory on a bank. Once no barracks or war factory is due
  on its own gates, with exactly one war factory standing and the money at
  least `bank_factory_money` (2,500) in every state of the last
  `bank_factory_seconds` (30), the second war factory is ordered whatever
  the income. It still needs one on offer, room under `max_war_factories`,
  the power target (a power plant goes first when its drain would break the
  target) and a place, and like any producer it waits under a base-defense
  threat and while the money is saved for a lost core building.
- An idle Building queue with a big bank. Once the Building queue has held
  nothing (nothing queued, nothing building and no finished building
  waiting to be placed) for `idle_queue_seconds` (30), with the money at
  least `idle_bank_money` (3,000), the first of these that is allowed is
  ordered: a war factory while fewer than `max_war_factories` stand; a
  refinery for an untaken field one can stand against now, under the
  refinery caps (`refineries_max`, `refineries_max_late`, and
  `growth_refineries_max` with `refinery_growth` on), placed against that
  field; the power plant the power target calls for. Never a defense, and
  one such order in `idle_queue_seconds` at most. A finished building held
  unplaced is not an empty queue: `held_cancel_ticks` of `[placement]` frees
  it.

Each order of either rule is an `economy` event with the tick, the reason
`bank_factory` or `idle_bank`, the item ordered and the money (cash and ore).
In the watched game 20261012T212803Z the income stayed at 54 to 88 a second,
under the 90 the second war factory waits for with `tank_factories` on, and
the Building queue stood empty from 07:13 to 12:54 while the ore banked to
7,536: no barracks with `no_army_infantry`, no refinery under the field cap,
the power target met. The idle-bank rule would have ordered the second war
factory at 09:25 and the bank rule at 09:48. Watch a game with both held
back by setting their money out of reach:

    python scripts/watch_game.py --model none \
        --set tuning:producers.bank_factory_money=1000000 \
        --set tuning:producers.idle_bank_money=1000000

Ore near the storage cap is spent by a third rule of the producers that is
not a switch, is not measured and always applies; its three keys of
`[producers]` ship with the values below. The refineries and silos hold ore
up to `resource_capacity`, which the game reports, and ore brought in
beyond it is lost. The cash has no cap and is not counted here.

- In force. Once the ore held has been at least `storage_spend_share`
  (0.6) of `resource_capacity` in every state of the last
  `storage_spend_seconds` (20), the rule is in force, and it stays in force
  while every state is so. A base with no refinery or silo stores nothing
  and is never near the cap.
- More war factories. While it is in force, the producers order one more
  war factory, ahead of one more barracks or war factory on their own gates
  and of the two bank rules, while fewer than `storage_war_factories_max`
  (6) stand or are on their way: that ceiling takes the place of
  `max_war_factories` (4) for this rule alone. One is ordered in
  `storage_spend_seconds` at most. It still needs one on offer, the power
  target (a power plant goes first when its drain would break the target)
  and a place, and like any producer it waits under a base-defense threat,
  while the money is saved for a lost core building and while another
  barracks or war factory is on its way.
- No economy hold. While it is in force, `economy_first` holds nothing: a
  hold in force ends, its `economy_hold` event giving the reason
  `storage_near_cap`, and none starts, so every unit queue with something
  to build keeps building.

The state that brings the rule into force is an `economy` event of the
reason `storage_near_cap` with no item, and so is each order the rule
makes, with the item ordered (the power plant included). Each gives the
tick, the money (cash and ore), the ore and `resource_capacity`. In the
watched game 20261013T022214Z the money stood at 5,000 or more from 15:54
and reached the 10,000 storage cap at 17:41, with the four war factories
`max_war_factories` allows standing, so the ore brought in after that was
lost. Watch a game with the rule held back by setting its share out of
reach:

    python scripts/watch_game.py --model none \
        --set tuning:producers.storage_spend_share=2.0

The war factory load gate (`src/ucla_agent/games/redalert/war_factory_load.py`)
puts every rule above that orders a war factory beyond the first through
one more test: whether the production load justifies it. It is a switch,
`war_factory_load_gate` of `[producers]`, that ships on and is not
measured, with `war_factory_load_share` (0.8).

- How the game makes vehicles. The state shows one Vehicle queue however
  many war factories stand, and the engine builds one item of it at a time.
  The engine's rules (the player.yaml rules of OpenRA's RA mod,
  `ClassicProductionQueue@Vehicle`) give that queue `SpeedUp` with
  `BuildTimeSpeedReduction: 100, 75, 60, 50`: an item takes 75 percent of
  its one-factory time with 2 war factories, 60 with 3 and 50 with 4 or
  more. Match records agree (a medium tank in 15.3 s with two against
  20.4 s with one). So a war factory adds no production line of its own: it
  speeds up the one line, by less with each one more, and not at all past
  the fourth.
- The spend rate. An item builds in `BUILD_SECONDS_PER_CREDIT` (0.024 s) a
  credit with one war factory and is paid for as it builds, so the Vehicle
  queue building without pause spends 41.7 credits a second with one war
  factory, 55.6 with two, 69.4 with three and 83.3 with four or more.
- The vehicle income. The income estimate is in credits a second over its
  window (60 s, see `income`). Less what the Infantry queue and MCVs spent
  a second over the same window, it is the vehicle income, renewed with the
  income. Harvesters stay in it: the war factories build them.
- The gate. While it is on, a war factory beyond the first is ordered only
  while the vehicle income is at least `war_factory_load_share` of the spend
  rate at N war factories, N those standing or on their way. That applies to
  every rule that orders one: the producer gates, `tank_factories`'s gates,
  the bank rule, the idle-bank rule and the storage rule, each still on its
  own terms as well. At 0.8 the checkpoints are: the 2nd war factory at
  about 33/s (33.3), the 3rd at about 44/s (44.4), the 4th at about 56/s
  (55.6), and any more at about 67/s (66.7). Had each war factory been a
  production line of its own, at 41.7 a second each, they would have been
  33, 67 and 100 a second. The engine's shared queue is what the gate uses.
  An unknown income reaches no gate, and the first war factory is never
  gated.
- What it leaves alone. A war factory the gate holds is no economy item the
  unit queues wait for under `economy_first`. An idle Building queue with a
  big bank goes on to the refinery for an untaken field or the power plant.
  An MCV never waits for the gate, whatever the vehicle income.

Each refusal of a war factory that a rule would otherwise order is an
`economy` event of the reason `war_factory_load_wait`, with the tick, the
rule (`producer_gates`, `tank_factories`, `war_factory_on_load`,
`bank_factory`, `idle_bank` or `storage_near_cap`), `vehicle_income_per_s`,
`war_factories` (N) and `needed_per_s`. A refusal that holds is written once
in 30 seconds at most.
Watch a game with the gate off:

    python scripts/watch_game.py --model none \
        --set tuning:producers.war_factory_load_gate=false

The gate can also be the producers' trigger for a war factory beyond the
first: a switch, `war_factory_on_load` of `[producers]`, that ships off,
with `war_factory_on_load_from_seconds` (180). It needs the gate on; with
`war_factory_load_gate` off it does nothing. It ships off because on seed
1001 of tmp/diag-i33 it ordered war factories at 3:10, 4:24 and 5:47, ahead
of the third refinery, which left $188 when a cleanup FINISH failed at 6:00
and the game was lost by elimination; off, with `cleanup_min_strength` at
8,000, the 18 seeds of tmp/var-A.txt won 12, against 8 with the trigger
from 6:00 (tmp/var-B.txt). Off, the income steps below order war factories
again, still behind the gate. On its own the
gate only held war factories back, and the producer rule still waited for
its income steps: `tank_factories_income` (90, 120, 150 a second) from
`tank_factories_from_seconds` (300), or `factories_income` with
`tank_factories` off. In the horde games of tmp/diag-i31-maps the income sat
at 41 to 75 a second, above the 33.3 a 2nd war factory needs to be kept 80
percent busy, yet no 2nd came, and those games ended with 4 to 15 tanks
against 28 to 38 in the wins.

- The trigger. From `war_factory_on_load_from_seconds` on, the producer
  rule orders one more war factory whenever all of these hold: the gate
  passes (the vehicle income is at least `war_factory_load_share` of the
  spend rate at N war factories standing or on their way,
  `war_factory_load.needed_income_per_s`); one stands and fewer than
  `max_war_factories` (4) stand or are on their way; none is on its way;
  one is on offer; the Building queue is free; and the money above the
  production reserve, with `float_seconds` (30) of the income, pays for its
  price. With `economy_first` on, the unit queues wait for it meanwhile, so
  the money gathers at the income. It still waits for the power target (a
  power plant first), under a base-defense threat, while the money is saved
  for a lost core building and, with `tank_factories` on, while it is saved
  for the third refinery.
- The checkpoints, at 0.8: the 2nd war factory at about 33/s (33.3), the
  3rd at about 44/s (44.4), the 4th at about 56/s (55.6); none past
  `max_war_factories`. So 45 a second with one standing orders the 2nd at
  200 s but not at 170 s, and at exactly 180 s it is ordered; with two
  standing, 50 a second orders the 3rd and 40 a second does not.
- What it retires while on. The income steps (`factories_income`,
  `tank_factories_income`), their busy shares (`busy_share`,
  `tank_factories_busy_share`), `tank_factories_from_seconds` and the
  investment test no longer order the producers' war factory. The bank,
  idle-bank and storage rules keep their own terms behind the gate, and the
  first war factory and MCVs are left as they were.

Each order is one `economy` event of the reason `war_factory_on_load`, with
the tick, the item ordered (the power plant included when it goes first)
and the money (cash and ore). A war factory the trigger would order but the
gate refuses is a `war_factory_load_wait` event of the rule
`war_factory_on_load`, once in 30 seconds at most. Watch a game with the
trigger on:

    python scripts/watch_game.py --model none \
        --set tuning:producers.war_factory_on_load=true

One more base-growth switch, `creep_from_opening` of `[placement]`, is not
measured and ships on, in the watched game's set; watch it off with
`python scripts/watch_game.py --set tuning:placement.creep_from_opening=false`.
On, the base walks from the opening's first building on in a straight line
toward the nearest ore field out of a refinery's range, in any direction and
with no lane: the field nearest the construction yard that no refinery
stands against or could stand against from a free place now, within the
creep cap, more than `creep_enemy_clear_cells` (25) from the enemy's start
and with no remembered enemy defense within `creep_defense_clear_cells`
(10). The creep cap: the field's nearest ore cell lies within
`creep_far_max_cells` (30), counted as a king moves, of the footprint of the
nearest own building but a defense of the line's base (the main base's
buildings for its line, those nearer the second yard for the second yard's
line), with `creep_continuous` on or off. Every building but a defense or a
refinery, the opening's power plant and barracks included, goes to the
free place farthest along the line from the yard to that field's nearest
ore cell among those within `creep_line_cells` (2) of the line; with none
that near, to the compact place nearest the yard (the first of its ring,
else of the places next to a building), never to the place nearest that
ore cell, which would string the base out toward the field. Either way it
keeps off the places a refinery by the home field would need. Once a refinery can stand against the field, the next
such field is the target. The field is also the creep target the second
yard and the forward hold go by, and each such placement is a
`creep_placement` event with the reason `nearest_far_field`. Refineries and
defenses are placed as with the switch off.

Four more base-growth switches build on `creep_from_opening`. None of them
is measured, and each ships on, in the watched game's set;
`mcv_to_skipped_fields` of `[expansion]`, which goes with the first of them,
ships off.

`refused_detection` of `[placement]` notices a place the engine would not
build on. The engine puts a building elsewhere, without a word, when the
cell it was asked for cannot take it, for example behind a cliff the map
picture does not show. With the switch on, a building that shows up within
`promise_ticks` more than `refused_miss_cells` (3) cells from the cell it
was asked onto is a miss: that place is never asked for again, for any
building, and the last `refused_spots_max` (64) such places are kept. Each
miss is a `placement_refused` event. After `refused_misses_to_skip` (2)
misses in a row of buildings placed along the line toward the same far
field, that field is skipped. With `mcv_to_skipped_fields` of `[expansion]`
on as well, the second yard's MCV goes to the skipped field nearest the base
that lies more than `creep_enemy_clear_cells` (25) from the enemy's start
and has no remembered enemy defense within `creep_defense_clear_cells` (10)
of it; it needs `second_yard`. Watch them together:

    python scripts/watch_game.py --set tuning:placement.creep_from_opening=true --set tuning:placement.refused_detection=true --set tuning:expansion.second_yard=true --set tuning:expansion.mcv_to_skipped_fields=true

`creep_continuous` of `[placement]` lets the walk go on field after field:
the creep cap is measured from wherever the base has grown to, so a field
beyond it becomes the far field once a building stands within 30 cells of
its ore, and a field `refused_detection` skipped is never the far field. In
the watched games of `tmp/diag-placement` (20261013T220250Z, 211922Z and
142816Z) the switch dropped the cap: once the MCV took the crawl field, the
creep aimed at fields 42 to 45 cells off and, with no place near the line,
placed buildings by those fields' ore, strung out from the base. Watch it
with
`python scripts/watch_game.py --set tuning:placement.creep_from_opening=true --set tuning:placement.creep_continuous=true`.

`refinery_claims_reached` of `[upkeep]` gives each field the base reaches a
refinery. The far field moves on as soon as a refinery could stand against
it, before one does; with the switch on, that field becomes a claim field
until an own refinery stands within `refinery_ore_cells` (2) of its ore, a
remembered enemy defense stands within 10 cells of it, or no free place
reaches it any more. While there is a claim field and fewer than
`claim_refineries_max` (8) refineries stand or are on their way, a refinery
is ordered after the opening as soon as the money covers it and the
production reserve, whatever `refineries_max` and the other refinery rules
say, and it is placed against the oldest claim field; its `refinery_field`
event gives the rule `claim`. Whether a claim is due is published to the
other managers as `refinery_claim_due`. Watch it with
`python scripts/watch_game.py --set tuning:placement.creep_from_opening=true --set tuning:upkeep.refinery_claims_reached=true`.

`remote_yard_growth` of `[placement]` gives a second construction yard that
stands more than `ring_max_cells` (12) from the first a far field of its
own, found by the same rules from its own middle and never the main base's
far field or a skipped field. The buildings that walk a line then take
turns: the 1st, 3rd, 5th and so on after the second yard stands go along
the main base's line, the 2nd, 4th and so on along the second yard's, and a
line with no target or no place yields to the other. Their
`creep_placement` events add `yard`, `main` or `second`. A field the second
yard's line reaches is claimed like any other. It needs `second_yard` to
have a second yard:

    python scripts/watch_game.py --set tuning:placement.creep_from_opening=true --set tuning:expansion.second_yard=true --set tuning:placement.remote_yard_growth=true

Five more switches keep the economy growing once the base has walked out to
new fields. None of them is measured, and each ships on, in the watched
game's set, with up to 6 refineries (`refineries_max` and
`refineries_max_late` of `[upkeep]`); the harvesters keep to the harvester
target below.

A field is taken once an own refinery stands within `refinery_ore_cells`
(2) of its ore, a refinery was asked onto a place against it within
`promise_ticks`, or a claim or growth refinery is on order for it. Upkeep
publishes the fields asked against at the start of every cycle as
`asked_refinery_fields`.

`refinery_spread` of `[placement]` puts refineries one to a field before any
field gets a second. After the opening a refinery stands against the
untaken field nearest an own yard that it can stand against now; only when
there is none does a field get a second, the nearest one with fewer than
`max_refineries_per_field` (1 as shipped, so none does). A claim field that
is taken leaves the claims for good, so no field is claimed twice, and the
claim refinery on order
stands against the field it was ordered for. Its `refinery_field` event
gives the rule `spread`, or `claim` for a claim. Watch it with the claims:

    python scripts/watch_game.py --set tuning:placement.creep_from_opening=true --set tuning:upkeep.refinery_claims_reached=true --set tuning:placement.refinery_spread=true

`refinery_unserved_only` of `[placement]` (on) sends refineries to the fields
no refinery of ours serves: one own refinery to an ore field. Each refinery
of ours serves one field, the field whose ore lies nearest its footprint,
by walking round cliffs, water and trees, a king's moves on open ground
(the one whose center lies nearer the footprint's
middle on a tie), when that ore lies within `refinery_serves_cells` (5) of
it; farther from every field it serves none. A refinery asked onto a place
lately, or on order for a field, counts as one. The harvester room below
counts a field's refineries by the same rule, so the two never disagree on
which field a refinery serves. That is the one count of a field's refineries
placement uses: it replaces the nearest field's center, the taken fields and
`max_refineries_per_field`. `refinery_ore_cells` (2) stays the reach a
refinery is placed within: 5 is that reach and the 3 cells
(`refused_miss_cells`) the engine may put a building off its place without
placement calling the place refused. In the game of `tmp/diag-i29-harv` a
refinery 3 cells from the east field's ore, by the old count within
`refinery_ore_cells` serving nothing, left that field unserved, and it got a
second refinery. Every refinery the economy orders after the opening's (a
claim, growth, a new yard's, the bank's, one more) goes:

1. against an unserved field that is not dry of at least
   `mcv_site_min_ore_cells` (30) ore cells, from a place that serves that
   field (a place within reach of it but nearer another field's ore would
   leave it unserved), the fields whose nearest own yard is an expansion yard
   first, then the field nearest an own yard, as a `refinery_field` event of
   the rule `unserved`;
2. only when no unserved field can be stood against, as the second refinery
   of a field served once, not dry, with at least `refinery_second_min_cells`
   ore cells, its middle `refinery_second_spacing_cells` (6) or more from the
   middle of the field's other refinery (rule `second`). It ships 9999, more
   ore cells than any known map's field has, so no field gets a second: in
   the game of `tmp/diag-i29-harv` a fifth refinery went to the mined-out
   100-cell home field at 07:09 as its second;
3. when the base is stuck, with neither of those, as the second refinery of
   a field served once, not dry, with at least
   `refinery_second_when_stuck_cells` (80) ore cells, spaced the same way
   (rule `second_when_stuck`). An unserved field qualifies with the same 30
   ore cells as in 1. This is the one exception to one refinery a field: in
   game 913 of `tmp/diag-i30-maps` the base stayed at 2 refineries for 16
   minutes because the only unserved field in reach had 21 cells;
4. otherwise nowhere: no refinery is ordered, no refinery claim is due, and a
   `refinery_field` event of the reason `waiting_unserved` says so, once a
   minute at most. Neither a claim's refinery nor a growth refinery is then
   an item the economy hold (`economy_first`) waits for: the unit queues are
   never held for a refinery that has nowhere to go.

For one refinery a field, a refinery of ours that serves no field counts
against the field whose ore lies nearest its footprint within
`refinery_counts_cells` (8), counted as a king moves: its harvesters work
that field all the same. The unserved test, the opening's place and a new
yard's claim count so; the harvester room keeps to the served field. In game
20261013T142816Z the west field (6, 53) got two refineries, at (16, 52) and
(11, 50) as reported. The opening placed its second refinery at (15, 51),
7 cells from that field's ore, as near as the base reached: serving no field
within 5, it left the field unserved, and the third refinery went onto it
at (10, 49) by rule 1. Counted against the field, it leaves it served, and
the same base now places its third refinery against the 100-cell home field
by rule 3. The opening's own refineries keep their place as below, the one
other way a field gets a second.

A field the move to fresh ore marks dry (`dry_fields`, which upkeep
publishes at the start of every cycle whatever the second yard's switches) is
never chosen, and the place the next refinery would take is worked out again
when the dry fields change. A refinery that is lost leaves its field
unserved, so one may be built against it again. The other refinery triggers
(growth, claims, a new yard's, the bank's, one more) are unchanged; only where
a refinery may go is.

No refinery stands with its footprint within a known enemy ground defense's
range plus `refinery_enemy_defense_margin_cells` (2): 8.5 cells of a gun
turret. While fewer than 2 own refineries stand or are planned, a refinery
keeps the opening's place, but one whose place serves a field a refinery of
ours serves, or a dry field, goes against an unserved field that is not dry
as in 1. when one can be stood against; only without one is it moved to a
place against its field spaced as a second is, whatever the field's size. So
the opening's second refinery goes to an unserved field when the home field
is served. A refinery finished with nowhere to go waits unplaced until the
rule for a building held too long cancels it. In the watched game
20261013T052655Z 5 of the 7 refineries stood on the two home fields. A wait
reads:

    {"tick": 9000, "cell": null, "rule": null, "field_center": null, "field_cells": null, "served_by": null, "ore_distance_cells": null, "reason": "waiting_unserved"}

Watch a game with it off to compare:

    python scripts/watch_game.py --set tuning:placement.refinery_unserved_only=false

or with a second refinery on a large field, and with the old served count:

    python scripts/watch_game.py --set tuning:placement.refinery_second_min_cells=80 --set tuning:placement.max_refineries_per_field=2
    python scripts/watch_game.py --set tuning:placement.refinery_serves_cells=2

or with no second when stuck, and with no refinery counted beyond the served reach:

    python scripts/watch_game.py --set tuning:placement.refinery_second_when_stuck_cells=9999
    python scripts/watch_game.py --set tuning:placement.refinery_counts_cells=5

Every rule that models the cells a standing building covers takes its
footprint from its reported cell less its type's offset
(`placement.REPORTED_OFFSETS`, through `placement.standing_box` and
`placement.footprint_corner`, the one source the served test, the spacing
test and the enemy refinery checks read as well). The state reports a
building at the middle cell of its footprint, (width // 2, height // 2) of
its body without the row it keeps clear: one cell right and one down of its
top-left cell for every building two cells or more across and tall, one
right for the SAM site's single row, none for a building of one cell, also
when the engine put it elsewhere than asked. Taken as the top-left cell, the
reported cell put a phantom row and column of cells right and below every
building: a place 2 cells from such a phantom cell stood 3 from the real
building, out of the engine's reach, and the engine moved it. The candidate
places, the war factory's exit, the refinery's lane to its ore, the yard's
middle (where the line toward the far field starts), the refinery the enemy
reaches first, the parking cell, the stall distance, the fresh-ore and route
walker middles and the MCV's yards and refineries all use it; a building
asked for keeps the cell it was asked onto as its top-left cell. The offsets
were checked against the place orders of the watched games 20261013T093754Z
(17 of 24 at the asked cell plus (1, 1), the other 7 moved),
20261013T142816Z, 211922Z and 220250Z: every one of proc, powr, apwr, tent,
weap, fix and dome the engine did not move was reported at the asked cell
plus (1, 1); a yard is reported on the cell its MCV deployed on, one right
and one down of its top-left cell. In those three games about 53% of the
placements after the second refinery (27 of 51) were moved by the engine, 5
of them by 5 cells or more, and every one of the 18 placements next to a
building only through its phantom cells was moved.

A whole fake match under the shipped tuning on the sidestep picture (the
fake game reporting each building at its footprint's middle as the engine
does, its own base on sidestep's east start) measures the share of place
orders the engine would move, their footprint on a standing building or out
of the reach of every building but a defense: 3 of 11 (27%) with the
reported cell taken as the top-left cell, 0 of 13 with the true footprint;
the test holds it at 20% at most (`tests/redalert/test_placement.py`,
`sidestep`).

A field counts as reached by walking. The terrain marks water, rock or
cliff and trees as impassable (every other character, a bridge, a beach, a
structure and a character the agent does not know included, is passable,
and so is a cell off the picture), and the walk distance from a field is
the fewest steps, a step to any of the eight cells around, from its ore over
passable cells, worked out once for each field and depth. A refinery
reaches a field (`placement.reaches`, the refinery places a claim or a
spread refinery takes, the fields a refinery stands or could stand against,
the taken fields) only when its footprint lies within `refinery_ore_cells`
(2) of the ore by walking. In game 20261013T220250Z the refinery reported at
(77, 25) stood 2 cells from sidestep's top field in a straight line and 11
by walking round the cliff row south of it, and served nothing there.

`refinery_growth` of `[upkeep]` keeps the economy growing past four
refineries. While at least 4 own refineries stand, fewer than
`growth_refineries_max` (8) stand or are on their way, none is on its way
and an untaken field can be stood against now, a growth refinery is due. It
is ordered as soon as the money covers it and `production_cash_reserve`,
whatever `refineries_max` and the other refinery gates say, after a due
claim and ahead of one more barracks or war factory, and it stands against
that field; its `refinery_field` event gives the rule `growth`. While it is
due and the money is short, the unit queues spend only what lies above its
price and the reserve, for `growth_saving_seconds` (60) at most; a saving
that runs out is a `refinery_field` event with `saving_timed_out`. In the
last `spend_remaining_seconds` (120) of the match no saving runs, whatever
the money: one in force then ends without an event, and the units get the
money. The
harvesters wanted stay the harvester target below, whatever the growth. With
`economy_first` on, the unit queues also wait for it as for a claim. Watch it
with `python scripts/watch_game.py --set tuning:upkeep.refinery_growth=true`.

The harvester target is the one cap on harvesters, the growth's included:
min(`harvesters_max`, `harvesters_per_refinery` x the own refineries
standing), 2 a refinery up to 22 as shipped, so 5 refineries want 10 and 12
want 22, not 24: the user's "1-2 harvesters and 1 refinery per ore field",
with one refinery a field. The harvesters standing and those in production
count against it, the free one each refinery brings included. Upkeep's
harvester rule trains one at a time while they are below it, a refinery and
a war factory stand and the harvester is on offer. Not measured. With 3 a
refinery, in the game of `tmp/diag-i29-harv` the trucks crowded the home
field; with 2 a refinery up to 12, the game of `tmp/diag-i25-econ` built 13
harvesters, none after 10:21.

`harvester_cells_per` of `[upkeep]` (10) gives each field an own refinery
serves (by the served-field rule above, `refinery_serves_cells`) a share of
max(1, min(`field_share_per_refinery` (2) x the refineries serving it,
floor(its ore cells / 10))) harvesters, and room for its share less those
working it: a harvester works the served field it stands nearest to, and one
sent to a field counts there until it is within 3 cells of it. A new
harvester, the free one of a new refinery among them, is sent with `harvest`
and a `to` cell to the served field, not dry, with the most room, the
nearest on a tie; a harvester the move to fresh ore takes off a dry field
goes to the safe served field with the most room, or where
`fresh_ore_by_load` would send it while none has room. While no served field
that is not dry has room for one more besides those in production, no
harvester is trained, below the target or not. The ore cells are those of the
start of the match. In the game of `tmp/diag-i29-harv` the room by ore cells
alone gave the 100-cell home field 10 and the 33-cell east field 3, so every
truck went home, the east refineries' free ones included, until the home
field's room fell to 3: by 07:00 the trucks had worked 25.6 harvester-minutes
at home and 1.1 east. With the share, a 100-cell home field and a 33-cell
east field served by one refinery each take 2 harvesters each, and the east
refinery's free truck stays east. 0 switches the room off:

    python scripts/watch_game.py --set tuning:upkeep.harvester_cells_per=0

With the room on, the harvesters already at work are spread too. At most
once every `harvester_rebalance_seconds` (30), the field working more than
`harvester_rebalance_excess` (1) over its share, the furthest over first,
gives one harvester to the served field, not dry, with the most room: the
harvester of the field nearest that field gets `harvest` with a `to` cell,
that field's ore cell nearest it, and counts there until it arrives. A
harvester that flees, is parked, is new, is on its way to a field, or was
sent by the move to fresh ore and has not arrived, is never moved. With 4
harvesters on a home field of share 2 and none on an east field of share 2,
one moves east, and the home field's 3 are then no more than 1 over. Each
move is a `harvesters_rebalanced` event:

    {"tick": 4005, "harvester": 300, "from_field_center": [25, 33], "to_field_center": [55, 41]}

Watch a game without it:

    python scripts/watch_game.py --set tuning:upkeep.harvester_rebalance_seconds=99999

`harvesters_first` of `[upkeep]`, on, has the Vehicle queue, which builds
in order, take turns between a harvester that is due (below the target, a
refinery stands, and a war factory, the harvester on offer and a field with
room let one be trained) and the army's combat vehicles (see
`vehicle_turn`):

1. While the harvesters standing and in production are fewer than
   `harvesters_first_per_refinery` (99) x the own refineries standing, the
   harvester goes first: the unit queues give the Vehicle queue no combat
   vehicle while it is due.
2. Else, while main's combat value (what its members are worth in a fight,
   their cost weighted by their health; harvesters, MCVs and the support
   units are in no group) is under `combat_value_floor` (6,000), or an enemy
   army worth 1,000 or more was seen within 25 cells of an own building in
   the last 30 s, combat vehicles go first and the queue does not alternate.
3. Otherwise a harvester and a combat vehicle alternate: after a harvester
   the next vehicle is a combat vehicle, and after a combat vehicle a
   harvester.

When no combat vehicle comes in a cycle (the unit queues wait, or none is on
offer) and the Vehicle queue stands free, the due harvester takes it. The
target and the cap stay as above. The Infantry queue is fed as always. The
MCV the second yard waits for and the base's emergency counters still come
first, and the harvester is paid for as any order.

As shipped, `harvesters_first_per_refinery` is 99, more than the harvester
target ever reaches, so a due harvester always goes first: steps 2 and 3,
combat vehicles first and the alternation, are off by default. With 2 the
turns are on: in `tmp/diag-i28-off` main's combat value then stayed under
the floor, combat vehicles took the Vehicle queue, and at 09:00 the base
had 10 harvesters on 5 refineries and 2,000 of cash and ore, against 19
harvesters on 8 refineries and 13,200 in a game with the harvester always
first. Watch the turns on, or harvesters first off, with:

    python scripts/watch_game.py --set tuning:upkeep.harvesters_first_per_refinery=2
    python scripts/watch_game.py --set tuning:upkeep.harvesters_first=false

One more refinery than the opening's waits for the income to be held back:
the refineries hold `refinery_full_share` (0.75) of what they can, or every
refinery is worked, which it is once the harvesters reach
`refinery_worked_harvesters` (2, the harvester target's 2 a refinery) x the
own refineries standing; then it is ordered when the money reaches
`extra_refinery_min_cash` (1,500). With 2 and the target's 3 a refinery, in
`tmp/diag-i28-off` the extra refinery was called for from 02:29 on and its
economy hold timed out with no money. The keys as shipped:

    [upkeep]
    harvesters_first = true
    harvesters_first_per_refinery = 99
    combat_value_floor = 6000
    refinery_worked_harvesters = 2

`creep_stall_skip` of `[placement]` gives up a far field the base makes no
headway toward, as when the main line aimed at a field behind a cliff for
ten minutes. For the main line and the second yard's line apart, the best
distance is the smallest distance from any own building but a defense to
the far field's ore cell nearest the line's start. When
`stall_placements` (3) buildings placed along the line in a row bring it no
`stall_progress_cells` (2) closer, or `stall_seconds` (120) pass without
that, the field joins the skipped fields and the next far field is named in
the same cycle. Each skip is a `creep_placement` event with the reason
`stalled_skip` and the best distance reached. Only with `creep_continuous`
on does a skip move the growth target. With it off, the skip is recorded
but the main line keeps aiming at the same field. Only build time counts:
the seconds without progress add up only over the cycles in which the
Building queue is active (it holds something queued, being built or waiting
to be placed), and a building placed along the line is judged only in such
a cycle. In the watched game 20261012T235612Z fields were given up from
09:19 to 12:49 while the Building queue stood empty; a base that builds
nothing makes no headway, and that says nothing of the field. Watch it with
`creep_continuous` on:

    python scripts/watch_game.py --set tuning:placement.creep_from_opening=true --set tuning:placement.creep_continuous=true --set tuning:placement.creep_stall_skip=true

`second_line_local` of `[placement]` makes the second yard's line grow from
the second yard. Without it, that line took the places next to every
building, the main base's too, and a place by the main base that lay far
along the line was taken, so the second line's buildings stood by the main
base. With it on, the line takes only the places of the second yard's ring
and those next to own buildings within `second_line_radius_cells` (15) of
its middle, never one more than 20 cells from the second yard; with none it
yields to the main line. In the record, every `creep_placement` with `yard`
`second` then lies by the second yard. It works with `remote_yard_growth`:

    python scripts/watch_game.py --set tuning:placement.creep_from_opening=true --set tuning:expansion.second_yard=true --set tuning:placement.remote_yard_growth=true --set tuning:placement.second_line_local=true

`walked_routes` of `[placement]` is not measured and ships off. It grows the
base along a way an infantry unit walked to the far field, instead of the
straight line the map picture gives: in a watched game the main line aimed
at a field walled off by a cliff for ten minutes, and the game's own path
finding knows the way round. With it on (it works with
`creep_from_opening`), when the far field of the main line, or then of the
second yard's line, has no walked route, one rifleman (e1) is sent with a
`move` to the field's ore cell nearest the line's start: the first idle one
of the home guard or main, never one that holds a scout slot, else, in the
opening, the next one trained. For the second yard's line one within
`second_line_radius_cells` (15) of that yard goes first. One walks at a
time. The walker keeps its group and counts for the guard, so the opening
trains no rifle in its place, but takes no combat, guard-post or base
defense order while it walks. Its cell is kept as a point of the route each
time it has moved `walk_sample_cells` (2); within 3 cells of the ore cell
the route is done and the walker goes back to where it started. A building
walking that line then takes the place farthest along the way from the
line's start (the middle of its yard) straight to the route's point nearest
it and on through the route, among those within `creep_line_cells` (2) of
it. The field is skipped, as a field placement skipped, when the walker has
not arrived within the larger of `walk_timeout_seconds` (60) and
`walk_detour_factor` (2.0) times the straight distance over its speed plus
10 seconds, or stands still for 10 seconds short of it (reason
`walk_unreachable`), and at once when its way grows past
`walk_detour_factor` times the straight distance (reason `walk_detour`). A
field whose straight way from the line's start crosses a remembered enemy
defense's cover gets no walker and is skipped (reason `walk_defended`), and
so is a field within `walker_enemy_refinery_cells` (12) of a remembered
enemy refinery (reason `walk_enemy_field`). No walker is sent while the base
is under threat, nor to a field while an enemy unit that fights was seen
within 8 cells of its straight way in the last `walker_enemy_seen_seconds`
(60); that field waits, and the next line's may be walked meanwhile. Once
`walker_deaths_to_skip` (2) walkers were killed on their way to a field, the
field is skipped (reason `walk_killed`): in a watched game seven riflemen in
a row died walking toward an enemy expansion. Each skip is a
`creep_placement` event with the reason. Each walk writes `route_walk`
events as it is sent, done or failed: the field, the line, the walker's
type, the length walked, the straight distance and the points kept, and a
failed one its reason (`walker_killed` for a walker a `unit_lost` event
names, `walker_lost` for one gone otherwise or taken into another group,
whose field is walked again). A done walk reads:

    {"tick": 895, "phase": "done", "field_center": [9, 50], "yard": "main", "walker_type": "e1", "path_length_cells": 18.94, "straight_distance_cells": 20.4, "point_count": 10}

Watch it with `creep_continuous` on, so that a skipped field moves the
growth target:

    python scripts/watch_game.py --set tuning:placement.creep_from_opening=true --set tuning:placement.creep_continuous=true --set tuning:placement.walked_routes=true

`fresh_ore_by_load` of `[upkeep]` spreads the harvesters moved off a dry
field instead of sending them all to one. Each goes in turn to the safe
field nearest it whose load (own harvesters last sent there or within
`dry_near_cells` of it, and those sent there in the move) is below its
share, `field_share_per_refinery` (2) for each own refinery standing against
it, at least one; with none below its share, to the field it would go to
with the switch off. The `harvesters_moved` event adds `to_field_centers`,
one for each harvester. It works with `harvesters_to_fresh_ore`:

    python scripts/watch_game.py --set tuning:upkeep.harvesters_to_fresh_ore=true --set tuning:upkeep.fresh_ore_by_load=true

With `truck_shares_by_mines` of `[upkeep]` (on), the ore trucks are shared
out by the ore mines of the fields: the groups of mine cells (`M`) of the
map picture, which grow a field's ore back (`OrePatch.mines`). A field is
active while an own refinery stands counted against it (serving it within
`refinery_serves_cells`, else within `refinery_counts_cells` of
`[placement]`), and takes `trucks_per_mine` (2) trucks for each mine,
`trucks_mineless_field` (2) without one, none while it is marked dry and has
no mine. Every truck keeps the field it is assigned to until it is lost or
moved by this rule or the move to fresh ore, and every order that sends it
back to harvest (the idle re-send, the return after fleeing, parking's end,
the harvester crush's end) names that field's ore cell nearest it. A new
truck goes to the nearest active field short of its share, else to the
nearest active field; while a field is short, one truck of a field over its
share, the nearest, moves there at most once every
`truck_rebalance_seconds` (10) and at once when a new refinery stands, as a
`harvesters_rebalanced` event, never taking a field below its share. The
move to fresh ore by load holds an active field to the same share.

With `truck_cap_by_shares` of `[upkeep]` (on), trucks are trained only up to
the fields' shares, in place of the harvester target: the cap is the active
fields' shares together (by the truck shares, else by the field room's
share), `harvesters_max` (22) at the most, one a refinery under the full
hold when that is fewer. The trucks standing, in production and the free
truck of each own refinery that stood less than `free_truck_wait_seconds`
(20) ago, while no new truck has shown up within 6 cells of it, count
against the cap, and a truck is trained only while they are fewer and an
active field has fewer trucks (assigned, with its refineries' free ones
still due) than its share. A planned expansion or an MCV adds nothing.

`dry_distinct_harvesters` (3) of `[upkeep]` keeps one harvester that keeps
going idle from calling its field dry: the idle re-sends that reach
`dry_idle_count` within `dry_window_seconds` mark the field dry only when at
least 3 different harvesters were sent, or, for a field with fewer harvesters
(those within `dry_near_cells` of a refinery that serves it), every one of
them. In the watched game 20261013T052655Z one dry call at 09:05 sent 8
harvesters across the map. Watch it with a stricter count:

    python scripts/watch_game.py --set tuning:upkeep.dry_distinct_harvesters=4

A fight is no sign of a dry field, so the move to fresh ore leaves it out of
the count, with no switch. No idle re-send is counted while base defense
names a threat (`threat_active`), nor that of a harvester with an enemy
combat unit seen lately within `fresh_ore_enemy_clear_cells` (12) of it: a
harvester that fled or found its way blocked stands idle as one by a dry
field does. And no field whose center lies within `mcv_enemy_building_cells`
(14) of `[expansion]` of the enemy's start is a fresh-ore target, served or
not; while that start is unknown, every spawn of the map but the base's own
(the one nearest the base's center) counts. In games 911 and 913 of
`tmp/diag-i30-maps` an attack left harvesters idle by their refinery, the
home field was marked dry, and the harvesters were sent toward the enemy's
start.

A raid switch, `launchable_only` of `[raids]`, is not measured either and
ships on, in the watched game's set. To watch a game with it off, run
`python scripts/watch_game.py --set tuning:raids.launchable_only=false`.

With it on, RAID is open only while a raid could set out now. It checks
three things in this order, and the first one that fails gives the closed
reason that the plan briefing and the `plan_change` record show:

1. A raid target is known. Closed reason: "no raid target known".
2. A safe route to it exists from the staging point, using the search a
   ready raid group makes. Closed reason: "no safe route to a raid target".
3. The raid quota can be filled from the raid group plus fit main units
   within `borrow_home_cells` (15) of the base or the staging point. The
   guard, the escort and units in a fight are never counted. Closed reason,
   for example: "no units for a raid on hand: need 4 1tnk, have 2 1tnk",
   with a mixed quota's types joined by " + ".

Choosing RAID moves the counted units into the raid group in that cycle.
If no raid is out within `launch_within_ticks` (1,500, about 60 s), the
group goes back to main. RAID then shows "raid did not set out" for
`resend_after_ticks`.

Of the measured scouting switches of `[scouting]`, `rescout` ships on as
measured, `rush_detection` ships on in the watched game's set and
`indirect_route` ships off. With the scout slots below on, as shipped, the
slots' scouts take the place of the missions of `rescout` and
`indirect_route`:

- `rescout`, on, sends a scout again whenever the picture of the enemy has
  gone stale: no cell within 15 cells of the enemy's start in our units'
  sight for 90 seconds (`rescout_base_seconds`), no enemy unit that fights
  seen for 60 (`rescout_army_seconds`), or an ore field more than 25 cells
  from our base out of sight for 180 (`rescout_fields_seconds`). Sight is
  taken as 6 cells around each of our units, held on a grid of squares 4
  cells wide. The scout is a jeep, else a flak truck, else a rifleman, that
  stands idle or waits with main, never one of the guard, a raid, the MCV's
  escort or the rush watcher; with none for 30 seconds
  (`scout_train_after_seconds`) one rifleman is trained for it. A mission
  lasts 120 seconds at the most (`scout_mission_max_seconds`), and a scout
  that base defense takes for the base is lost to its mission. Still one
  scout at a time, a minute apart, and none under PRESERVE or a major threat.
  Measured: 108 wins, mean kd 13,259, and against the Rush bot 3 games more
  for Russia and 1 more for England. Turn it off with
  `--set tuning:scouting.rescout=false`.
- `indirect_route`, off, would send every scout the indirect way: by a
  waypoint 14 cells (`scout_side_cells`) to one side of the line from our
  base to the enemy's start, 40 percent of the way (`scout_side_share`), the
  other side on the next mission; by up to 2 ore fields (`scout_fields_max`)
  within 15 cells of its way (`scout_field_cells`) that have been out of
  sight for two minutes; to the enemy base's flank on that side and round its
  far side, 14 cells from its start (`scout_view_cells`); and home by the
  other side. No waypoint lies within 9 cells of the enemy's start
  (`scout_min_enemy_cells`) or where a remembered defense covers it, and a
  scout that loses 10 or more health in one state (`scout_hit_hp_drop`) goes
  on to its next waypoint away from what hit it. Measured with `rescout` on:
  89 wins, mean kd 8,225. Watch it with
  `--set tuning:scouting.indirect_route=true`.
- `rush_detection`, on, keeps a verdict on an early rush for the first
  300 seconds (`rush_watch_until_seconds`), published as `rush_verdict` of
  the match context for the opening: `rush` on an army seen on its way, 6
  enemy infantry (`rush_infantry`) or 2 enemy fighting vehicles seen lately
  farther than 20 cells (`rush_near_base_cells`) from the enemy's start
  (signs `infantry_out`, `vehicles_out`), or an enemy army worth 1,000 within
  30 cells of our base (`army_approaching`); else `none` once the enemy base
  has been seen and `unknown` before, for good when the base is still unseen
  at 150 seconds. What the enemy builds at home gives no verdict: in 54
  stepped harness games the Rush and the Normal bot built barracks and war
  factories at the same rates, and the early-factory sign gave a false rush
  against the Normal bot, so the barracks, early-factory and near-base signs
  are gone (`rush_barracks`, `rush_barracks_before_seconds` and
  `rush_early_factory_seconds` are read but unused). Infantry massing at the
  enemy's start, 8 or more (`rush_mass_infantry`) seen within 20 cells of it
  by 200 seconds (`rush_mass_before_seconds`), is a warning instead
  (`MatchContext.rush_warning`, a `rush_verdict` event with `warning` true and
  the sign `infantry_near_base`): it orders nothing, holds cash (see the rush
  counter below) and ends at the rush verdict, once the counter has ordered
  its defenses, or at 300 seconds (an event with `warning` false). With
  `rush_scout_direct` on (as shipped), the first scout slot's scout, whatever
  unit holds it, is steered by `rush_scout` until 300 seconds: straight at the
  enemy's start until within 5 cells of it (`rush_scout_pass_cells`), or hit,
  or 30 seconds within 13 cells or idle short of it; then to the exit post 13
  cells (`rush_exit_post_cells`) from the enemy's start on the line toward our
  base (the nearest cell on that line toward our base a unit can stand on),
  where it stands, holding fire, until the watch ends. In those 54 games no
  enemy barracks was seen before 300 seconds by the slots' stand-off
  missions; the pass looks into the base and the post sees the wave leave.
  The pass and the post override the keep-off ground and the wide berth; a
  retreat steps the scout back only once it has stood at the post; it is
  released below 40 hp (`scout_release_below_hp`) whatever the wide berth
  says; and the model commander's scout question steers the next slot's
  scout until 300 seconds, when the first goes on the missions again. One
  rifleman of main watches the way in from 40 percent of the
  way to the enemy (`watcher_share`) until the watch ends. Measured with
  `defense_on_threat` on: 95 wins, mean kd 9,983, and 7 games fewer for
  Russia against the Rush bot. It ships on all the same, in the watched
  game's set; watch it off with `--set tuning:scouting.rush_detection=false`.
- An ore field with one of our buildings (a refinery, a second yard, any
  other) within 12 cells (`own_field_cells` of `[scouting]`) of its center or
  nearest ore cell is ours (`own_fields`): no scout mission, rescout,
  expansion watch, scout place or commander scout option goes there, since
  our building watches it; the fields by the enemy's start are watched as
  before.

With `rescout` or `indirect_route` on, each mission is a run of `scout`
events (sent, each waypoint, then done or lost) with its reason (`initial`,
`stale_base`, `stale_army` or `stale_field`), the scout's type and cell, and
what is then known of the enemy; with `rush_detection` on, each change of the
verdict is a `rush_verdict` event with the signs seen. With the scout slots
off, a scout goes straight to the enemy's start and circles it 10 cells out,
a new one goes whenever the enemy base area, its army or a far ore field has
gone unseen as `rescout` gives, and each mission is written as `scout`
events; with `rescout` off as well, a new scout goes only when no enemy
building has been seen for 90 seconds. As shipped, the scout slots below do
the scouting and a rush verdict is kept.

One more scouting switch, `scout_slots` of `[scouting]`, is not measured and
ships on, in the watched game's set. To watch a game with it off, run
`python scripts/watch_game.py --set tuning:scouting.scout_slots=false`. With
it on, scouting is two designated scouts (`scout_slot_count`) kept for the
whole match in place of the missions above, which are not sent. The first
two infantry trained take the slots and leave the guard, which trains its
rifles again. Once a war factory stands the Allies train a jeep for each
slot (held back until a second war factory or 300 seconds, see
`allied_fast_scout_after_seconds` below); the Soviets train a flak truck
for each as shipped (see
`soviet_scout_unit` below), or with the dog build a kennel after the opening
and train a dog for each.
A lost scout is replaced by the fastest of these, or a rifleman, that can be
trained, at least 20 seconds (`scout_replace_floor_seconds`) after the last
replacement, and by a rifleman at once when both slots are empty. These
orders claim their queue, so `economy_first` never holds them. The scouts
count toward no army strength, base defense never draws them in, and they
count as out to find the enemy base only while one heads within 14 cells of
it.

Each scout goes from place to place: the enemy base's flanks, the ore
fields, the enemy's expansions and the map's edges, the one gone unseen
longest first, never the middle of the enemy base or of the map, nor where
the other scout is heading. Its way keeps out of every remembered defense's
cover and every enemy unit in sight's range plus 3 cells
(`scout_danger_margin_cells`), and 12 cells (`scout_center_clear_cells`) from
the map's middle, going round by a side waypoint when the straight line does
not; a place with no safe way is passed over for the next. A scout with
danger within 6 cells (`scout_flee_cells`) flees 6 cells straight away from
it; one within 3 cells of its destination (`scout_reached_cells`) takes the
next; one below 40 health (`scout_release_below_hp`) goes back to main and
its slot is refilled. At most 2 scout orders go in one batch
(`scout_orders_per_batch`). Each change is a `scout_slot` event: `assigned`,
`upgraded`, `replaced`, `lost` (killed or released) and `destination` (the
cell, `script` or `model`, and why).

`soviet_scout_unit` of `[scouting]` names the faster unit the Soviet scout
slots train: `dog` as shipped, from the kennel upkeep builds after the
opening, or `ftrk`, the flak truck, which is faster (speed 2.76 against the
dog's 2.44) and needs no kennel. With `dog` a slot is held by a rifle until
the kennel puts a dog on offer, and a lost scout is replaced by a dog, or by
a rifle while no kennel stands; a Soviet scout is never a flak truck, and
the scouting missions take a dog, never a flak truck, as well. With `ftrk`
and `scout_slots` on, a Soviet base builds no kennel, trains a flak truck
for each slot a rifleman holds once a war factory stands, and replaces a
lost scout with one; the slot rules and the replacement floor above are
unchanged, and the Allies keep their jeeps. It is not measured; `dog` ships
since 5 flak-truck scouts died ($3,000) in diag-i26-soviet. Watch the flak
truck with the Soviets:

    python scripts/watch_game.py --faction russia --set tuning:scouting.soviet_scout_unit=ftrk

`allied_fast_scout_after_seconds` of `[scouting]` (300) holds back the
Allied jeeps of the scout slots: no jeep is ordered for a slot, as an upgrade
or as a replacement, until 2 war factories stand or the game time reaches 300
seconds. Until then the slots keep their riflemen, and a lost scout is
replaced by a rifleman (e1) from the barracks, within the replacement wait
and the spend cap as before. The Soviet dogs (and flak trucks) are not held
back. It is not measured; it ships at 300 since in games 911 and 913
England's one war factory built two scout jeeps between 03:04 and 03:43, and
the first rush, at 05:00, met 2 medium tanks with 4 Soviet heavy tanks. Watch
the jeeps come as soon as a war factory stands with:

    python scripts/watch_game.py --set tuning:scouting.allied_fast_scout_after_seconds=0

`scout_wide_berth` of `[scouting]` gives the scouts a wider berth. It is not
measured, ships on in the watched game's set, and changes nothing while
`scout_slots` is off. With it
on, an enemy unit in sight is dangerous within its range plus 6 cells
(`wide_danger_margin_cells`), a scout with danger within 10 cells
(`wide_flee_cells`) flees 10 cells away from it, and a scout below 60 health
(`wide_release_below_hp`) goes back to main. A scout whose health drops by 5
or more (`hurt_min_drop`) between two states marks the cell it stands on as
a hurt area: for 60 seconds (`hurt_avoid_seconds`) the ground within 10 cells
of it (`hurt_area_cells`) is danger to both scouts, so their ways keep out of
it, a scout near it flees, a destination inside it is replaced at once, and
no scout is sent there nor is the model offered it. At most 8 hurt areas
(`hurt_areas_max`) are kept, the oldest dropped first. Each is a
`scout_slot` event `hurt` with the cell and the scout's health. Watch it
with:

    python scripts/watch_game.py --set tuning:scouting.scout_slots=true --set tuning:scouting.scout_wide_berth=true

`scout_spend_cap` of `[scouting]` caps what the scout slots spend. It is not
measured, ships on in the watched game's set, and changes nothing while
`scout_slots` is off. With it
on, a lost scout is replaced at least 60 seconds (`capped_replace_seconds`)
after the last replacement, in place of 20, and the slots order at most 6
fast scouts (`fast_scouts_max`: jeeps, flak trucks or dogs, upgrades and
replacements alike) in a match; after that no slot is upgraded and a lost
scout is replaced by a rifleman. A rifleman is still ordered at once while
both slots are empty, but a slot whose unit is on its way counts as filled.
The holds of `mcv_first` are unchanged. Each fast scout the cap refuses is a
`scout_slot` event `capped` with the item refused and the order (`upgrade`,
once for each scout whose upgrade is refused, or `replacement`). Watch it
with:

    python scripts/watch_game.py --set tuning:scouting.scout_slots=true --set tuning:scouting.scout_spend_cap=true

`scout_missions` of `[scouting]` gives the scout slots the script's missions
in place of places picked by how long each has gone unseen. It is not
measured, ships on in the watched game's set, and changes nothing while
`scout_slots` is off. There
are five kinds of mission: watching each ore field more than 20 cells from
every own yard and not within 15 of the enemy's start that has gone unseen
for over a minute (priority 3); watching each ore field within 15 cells of
the enemy's start, where the enemy mines (`enemy_home_field`), once neither
its center nor its standoff cell has been in sight for 120 seconds (priority
2, below the expansion watch's 3: a mission's score grows with its priority),
from the standoff cell: the first cell from the field's center out toward our
base, a cell at a time, farther than 8 cells (`scout_enemy_base_cells`), a
cell and the flee distance from every remembered enemy building but a wall
and farther than a cell and the flee distance from every danger, at most 20
cells from the field, and none when no such cell is; the edge of the
enemy's main, 14 cells
(`scout_view_cells`) from its start toward our base and 12
(`side_offset_cells`) to the side (priority 2, or 4 while no enemy barracks
or war factory is known); halfway between our main yard and our next yard's
site, when `mcv_repeat` or `mcv_reserve_field` has one (priority 2); and the
middle of the lane between the bases (priority 1, or 3 when no enemy army has
been seen for 90 seconds). The enemy's start is the map's other spawn, known
from the first state. A mission scores its priority times how unknown the
place is (1 never seen, else its seconds unseen over 120, at most 1) over the
seconds the scout needs to get there and watch it 10 seconds
(`mission_observe_seconds`) plus 1; one with no safe way to it, around every
danger the steering keeps out of, is not offered. The two scouts are
assigned together, the best pair winning and two missions within 15 cells of
each other counting half. A scout keeps its mission until it has watched the
place 10 seconds, the way turns unsafe or it flees, or another scores 1.25
times as much (`mission_switch_margin`); a mission on which it lost health is
not given for 90 seconds (`mission_blacklist_seconds`). Each mission given is
a `scout_slot` event `destination` with source `mission`, why the last ended,
the kind and the score. While main is big and no enemy army has been seen for
two minutes, a refresh forces the lane and the edge of the enemy's main at
most once a minute, the reason `refresh` (see the plan selector above). No
model is asked where the scouts go, whatever `model_scout_choice` says. Watch
it with:

    python scripts/watch_game.py --set tuning:scouting.scout_slots=true --set tuning:scouting.scout_missions=true

`scout_cautious` of `[scouting]` makes the scout slots' scouts careful, so
that a jeep neither drives into an enemy base nor stops to duel what it
meets there. It is not measured, ships on, and changes nothing while
`scout_slots` is off. With it on:

- A unit that takes a slot (`assigned`, `upgraded`, `replaced`) gets one
  `set_stance` order of `hold_fire`, and again only when a state shows it in
  another stance 2 seconds or more after the last. A unit that leaves its
  slot for main replaced by the faster scout gets `set_stance`
  `attack_anything` back, the stance main's units fight in.
- A unit released too hurt to scout goes home first. It stays in the scout
  group, keeps `hold_fire` and is taken out of every attack as a slot's
  scout is, with a `move` to the staging point (again 2 seconds or more
  after the last while it stands idle short of it). Once it stands within 4
  cells of the staging point it joins main with `attack_anything`, and one
  `scout_slot` event of kind `returned` gives its slot, id and type. Base
  defense takes one within `threat_area_cells` (15) of a major threat's cell
  into main at once, with main's stance, and it answers as a unit of main
  does. In game 20261013T130117Z, 4 of 6 jeeps were lost after a release
  below 60 hp handed them to main, whose contact focus sent them to attack
  deep in the enemy's ground.
- A slot's scout is never sent an `attack` or `attack_move`: after the
  managers propose the cycle's orders, the units of the scout group are taken
  out of every such order, and one left with no unit is dropped, whichever
  manager proposed it (base defense for a unit new in the cycle that has not
  yet taken its slot, main's focus fire for a unit in contact). A unit
  joined to main is main's again, attacks included.
- A scout retreats at once when its health drops by 1 or more
  (`scout_retreat_hp_drop`, a key of its own beside the wide berth's
  `hurt_min_drop`) between two states (cause `hit`), or when the enemy units
  that fight in sight within 10 cells (`scout_outclass_cells`) are worth more
  than it is (cause `outclassed`), before any of them hits it. A unit is
  worth its cost by its share of health, as in the estimate of a fight; a
  scout holds fire, so it counts as a unit that cannot hurt what it meets, at
  `matchup_factor` (0.5) of that: a jeep (500) is worth 250, so three enemy
  rifles (300) outclass it and two (200) do not. It is sent directly away from
  the nearest enemy in sight (a unit that fights or a defense), or with none
  in sight back toward where it stood 8 seconds before (the staging point
  when it has not moved), as far as it goes in 8 seconds (`scout_retreat_seconds`)
  at its speed, inside the map; the retreat lasts 8 seconds, kept on from
  each state in which a cause holds again, and the scout is sent anew away
  from the nearest enemy when that is another or it has reached its cell.
  The retreat's move is the most urgent of the scouts' moves and counts
  within `scout_orders_per_batch`. The scout's mission is kept for it while
  it retreats; afterwards it takes it up again, and a mission it lost health
  on ends `hurt` and is blacklisted as before. The cell a scout was hit on
  becomes a hurt area (`hurt_area_cells` around it for `hurt_avoid_seconds`)
  however little it lost. Each retreat begun is one `scout_slot` event
  `retreat` with `cause`, the cell it stood on (`cell`), where it is sent
  (`to`), its health (`hp`) and the cell of the enemy it is sent away from
  (`enemy`, null when it goes back along its way).
- The ground within 8 cells (`scout_enemy_base_cells`) of every remembered
  enemy building that is not a wall (sandbags, fences, walls and wire) is
  danger to the scouts, as a defense's cover is: the ways go around it, a
  scout near it flees, and a mission inside it has no safe way. The viewing
  point on the enemy main's edge is moved out along its line toward our base,
  a cell at a time, to the first cell farther than 8 cells, a cell and the
  flee distance (10 with the wide berth) from every such building, so that a
  scout can stand and watch there; it stays at 14 cells from the start when
  no such cell lies short of our base.
- The scouts on missions keep the flee distance on their ways: a mission is
  routed by the keep-off margin when it is offered and while it is kept
  (`route` with `must_keep_off`, the way through the graph of waypoints
  included), so a mission whose only way passes within the flee distance of a
  danger is not offered, and a held one whose way no longer keeps it ends
  `unsafe`. A scout that fled is not given back the mission it fled, and is
  offered keep-off ways only, until it stands within 3 cells of the cell of a
  mission given since or 60 seconds pass since its last flight. A flight
  whose cell, the flee distance straight away from the danger, lies within the
  flee distance of another danger goes to the staging point instead. In game
  20261013T142816Z the scouts jittered for about 6 minutes: a way judged safe
  at no margin passed within the flee distance of enemy base ground, so the
  scout fled, lost its mission and was given it back once clear, and two
  dangers 35 cells apart bounced a scout between them.

On the standard fake match for the Allies with the tracked files, the squares
the units saw by 10:00 were 101 with the switch on and 101 with it off (the fake
game has no combat, so no scout is hit there). Follow the retreats and the
stance orders in a match record with

    python -c "import json,sys; [print(e['data']) for e in map(json.loads, open(sys.argv[1])) if e['kind']=='scout_slot' and e['data']['kind']=='retreat']" <events.jsonl>

and watch it off with:

    python scripts/watch_game.py --set tuning:scouting.scout_cautious=false

`scout_bait_shot` of `[scouting]` has a careful slot scout fire once before
it runs. It is not measured, ships on, and changes nothing while
`scout_slots` or `scout_cautious` is off. The one shot is bait: it makes
part of the enemy force peel off and chase the scout, away from where it was
going. With it on:

- When a retreat begins for a slot scout (cause `hit` or `outclassed`, the
  rush slot's scout included), its order of that cycle is one `attack` on
  the nearest enemy in sight that is a unit that fights (never a building,
  so never a defense, which cannot chase), that it can hurt by the damage
  table (`fight.can_hurt`: a dog does nothing to a jeep) and that stands
  within its weapon range against it (a jeep's 4 cells), ties by the lower
  id. The order goes at the scouting priority, as urgent as a flight, and
  counts within `scout_orders_per_batch`. A retreat kept on or sent anew
  makes no shot, and neither does one begun with no such enemy: it runs
  as before.
- The retreat's move is held back until 1 second (`scout_bait_shot_seconds`)
  after the shot, and goes from the first cycle after that; the retreat
  still lasts 8 seconds from when it began. No other attack is made for
  that retreat, and the scout makes no other bait shot for 15 seconds
  (`scout_bait_cooldown_seconds`): a retreat begun sooner runs without one.
- The scout stays in `hold_fire`, which only governs what it shoots at of
  its own accord. The attack strip keeps the shot: the slots mark their own
  attacks (`Intent.scout_rule`), and every other manager's attack on a
  scout is still stripped.
- The `retreat` event gains `bait`: the id and type of the shot's target,
  or null when no shot was made.

Watch it off with:

    python scripts/watch_game.py --set tuning:scouting.scout_bait_shot=false

`scout_stray_hunt` of `[scouting]` has a careful slot scout kill a lone
enemy unit it clearly outfights, such as a stray enemy scout, which it
would otherwise only run from. It is not measured, ships on, and changes
nothing while `scout_slots` or `scout_cautious` is off. With it on:

- A unit of type a kills one of type b in b's hit points times its share of
  health over a's damage a second against b, by the damage table; never when
  that damage is 0 or a type is unknown. The scout wins the matchup when it
  kills the target within 10 seconds (`stray_hunt_max_kill_seconds`) and at
  least 2 times as fast as the target kills it (`stray_hunt_win_ratio`), at
  the health both have now. At full health a jeep wins against a rifleman
  (1.3 s against 30 s), a dog and a rocket soldier, not against a jeep (20 s
  each) or a flak truck; a rifleman against a rocket soldier, an engineer, a
  spy or a medic, not against a rifleman or a dog; a dog against a rifleman,
  not against a jeep; a flak truck against a jeep. An ore truck or MCV takes
  too long to kill.
- A target is an enemy in sight that is not a building, an aircraft, an ore
  truck or an MCV, that the scout wins against, and that is lone: no other
  enemy unit that fights, in sight or seen in the last 30 seconds
  (`fight.is_counted`), within 10 cells (`stray_hunt_lone_cells`) of it or of
  the scout. Its cell and the scout's are each farther than every remembered
  ground defense's cover and 4 cells (`stray_hunt_margin_cells`; 12 from a
  pillbox) and than 12 cells (`scout_enemy_base_cells` and the margin) from
  every remembered enemy building but a wall.
- A slot scout in the field that is not retreating, not harassing and not
  the rush slot's scout while the rush scout rule steers it (the pass and
  the exit post until `rush_watch_until_seconds`) hunts the nearest target,
  unless that target's last hunt ended other than `killed` within 30
  seconds (`stray_hunt_retry_seconds`). Its order is one `attack` on the
  target at the scouting priority, as urgent as a flight, within
  `scout_orders_per_batch`, marked as the slots' own so that the attack
  strip keeps it, and made again every 2 seconds while the scout stands
  idle. Its steering, its mission (kept for later) and the commander's
  scout order are set aside, and its retreat's `hit` and `outclassed`
  causes are not taken while it hunts.
- The hunt ends, judged in every cycle, first match: `killed` (the target
  is gone or out of sight), `base_reach` (the target or the scout is no
  longer away from the base), `not_lone`, `outmatched` (the matchup no
  longer wins), `hurt` (30 points of health lost since it began,
  `stray_hunt_max_hp_loss`), `timeout` (20 seconds, `stray_hunt_max_seconds`).
  After `killed`, `base_reach` and `timeout` the scout takes up its mission
  again; after `not_lone` and `outmatched` it begins a retreat with cause
  `outclassed`, after `hurt` with cause `hit`, both without a bait shot,
  its health of the last state taken as its health then.
- Each hunt begun is a `scout_slot` event `hunt` with the target's
  `target_id` and `target_type` and the scout's `cell`, and each hunt ended
  one of kind `hunt_end` with the `target_id` and the `reason`.

Watch it off with:

    python scripts/watch_game.py --set tuning:scouting.scout_stray_hunt=false

`scout_base_harass` of `[scouting]` has a careful slot scout harass an enemy
base it finds open, mostly to force the enemy to react (pull units home,
build defenses), not to win it. It is not measured, ships on, changes
nothing while `scout_slots` or `scout_cautious` is off, and does not trigger
the decisive strike on an undefended enemy. With it on:

- A slot scout in the field that is not retreating, not hunting, not the
  rush slot's scout while the rush scout rule steers it, and whose last
  harass ended 60 seconds ago or more (`harass_cooldown_seconds`) begins a
  harass when it stands within 20 cells (`harass_trigger_cells`) of the
  enemy's start or of a remembered enemy building but a wall, no remembered
  ground defense and no enemy unit that fights, in sight or seen in the
  last 30 seconds, stands within 20 cells of it (`harass_clear_cells`), and
  it has a target.
- Its targets are the enemies in sight within 12 cells (`harass_target_cells`)
  that it can hurt by the damage table, no wall, none a remembered ground
  defense covers, in four tiers: engineers (`e6`); ore trucks and the
  infantry that does not fight (`harv`, `medi`, `mech`, `spy`, `thf`); power
  plants (`powr`, `apwr`); every other building. Within a tier the nearest,
  ties by the lower id. A unit that fights is no target: it ends the
  harass. A dog hurts neither a truck nor a building, so it only ever
  harasses infantry, and with none in reach it does not harass.
- Its order is one `attack` on its chosen target at the scouting priority,
  as urgent as a flight, within `scout_orders_per_batch`, marked as the
  slots' own so that the attack strip keeps it; anew when the chosen target
  is no target any more or one of a better tier comes into sight, and again
  every 2 seconds while the scout stands idle. The enemy base's ground is no
  danger to it meanwhile: its steering, its mission (kept for later) and
  the commander's scout order are set aside, and its retreat's causes are
  not taken. It stays in `hold_fire`.
- The harass ends, judged in every cycle, first match: `defense` (a ground
  defense within 20 cells), `enemy_unit` (an enemy unit that fights within
  20 cells), `hit` (health down `scout_retreat_hp_drop` or more since the
  last state), `timeout` (45 seconds, `harass_max_seconds`), `no_target`
  (no target for 3 seconds, `harass_idle_seconds`). After `defense` and
  `enemy_unit` the scout begins a retreat with cause `outclassed`, after
  `hit` with cause `hit` (the cell it was hit on becomes a hurt area); each
  takes the bait shot. After `timeout` and `no_target` it takes up its
  mission again.
- Each harass begun is a `scout_slot` event `harass` with the scout's
  `cell` and its first target's `target_id` and `target_type`, and each
  harass ended one of kind `harass_end` with the `reason` and the count of
  attack orders it made (`orders`).

Watch it off with:

    python scripts/watch_game.py --set tuning:scouting.scout_base_harass=false

`exposed_trucks` of `[scouting]` notes enemy harvesters at work far from the
enemy's start, with little guard and no defense over them, as raid
opportunities. It is not measured and ships on: it only records, and an
opportunity is acted on only by a switch of its own, such as
`prefer_exposed_trucks` below. In every cycle the enemy harvesters in sight,
seen by any of our units, are grouped by the ore field they stand on or
within 3 cells of. A field gives an opportunity when its center lies more
than 20 cells (`truck_field_min_cells`) from the enemy's start, the enemy
units that fight within 8 cells (`truck_guard_cells`) of the field, as
remembered and not stale, are worth at most 600 (`truck_guard_max_value`),
and no remembered enemy defense covers its center. The opportunities are
published as `exposed_trucks` of the match context, one for each field, the
newest first. Each is kept until 60 seconds (`truck_seen_seconds`) after its
harvesters were last seen, renewed with what is seen when they are seen
again, and dropped at once when a remembered defense comes to cover the
field or its guard grows past 600. Each entry gives the field's center
(`field`), the harvesters in sight there (`harvester_count` and
`harvester_ids`), the guard's worth (`guard_value`), the tick they were last
seen (`seen_tick`) and the cell of the harvester nearest our base
(`nearest_harvester`). Each new opportunity, for a field none is kept for, is
a `scout_slot` event `exposed_trucks` with the field, the harvesters' count,
the guard's worth, the tick and the nearest harvester's cell, whether or not
`scout_slots` is on. A watched game as shipped writes these events; watch one
without them with:

    python scripts/watch_game.py --set tuning:scouting.exposed_trucks=false

`prefer_exposed_trucks` of `[raids]` acts on these opportunities. It is not
measured and ships off. With it on, a raid goes after the harvesters of an
opportunity before any other harvester, the nearest of them first; the
targets a raid may be sent at are the same, so a covered harvester is still
none, and a refinery still comes only after the harvesters. With it off, the
raid's targets are ranked as before. Watch it with:

    python scripts/watch_game.py --set tuning:raids.prefer_exposed_trucks=true

`raid_exposed_trucks` of `[raids]` has the script raid these opportunities
itself, whether the script or the model commands main. It is not measured and
ships on, which changes the default game's raids: in a watched game the
commander was offered a raid on unguarded enemy harvesters 1,239 times and
never took it, while the enemy harvested an ore field of ours for over three
minutes. With it on, while no raid is out, an opportunity whose guard is worth
at most 200 (`exposed_raid_guard_max`) and whose harvesters were seen within
the last 20 seconds (`exposed_raid_seen_seconds`) launches the raid group at
one of its harvesters, the freshest such opportunity first: of the harvesters
it saw, the one still in the enemy memory, not destroyed by a raid and under
no remembered defense that stood nearest our base (`nearest_harvester`); an
opportunity with none of them is passed over. This target comes before the
raid's ranked targets, so a qualifying sighting launches a raid that the
ranking alone might not have chosen. Under RAID and PRESS a ready raid group
sets out at it. Under HOLD and FINISH, which keep the raid group, it raids on
exposed trucks alone: the group is formed from the units on hand, as RAID's
launch check counts them (fit units of main within 15 cells of the base or
the staging point, in no fight, never the guard), only while such a raid
could set out now, and a raid on exposed trucks under way goes on through
them. Under the plans with no raid group (the opening, PRESERVE and STRIKE)
none is launched. The raid takes the way any raid takes, through the graph of
waypoints when `waypoints` of `[routing]` is on, and breaks off, goes on after
a kill and comes back by the raid rules. Its `raid` start event gives the
reason `exposed_trucks`. With it off, raids are as before. Watch it off with:

    python scripts/watch_game.py --set tuning:raids.raid_exposed_trucks=false

A base-defense switch, `harvester_group_response` of `[base_defense]`, is not
measured and ships on, in the watched game's set. With it on, a threat that has hit an own harvester
more than 12 cells (`alarm_radius_cells`) from the centre of the base, while
the plan is HOLD, RAID or PRESERVE and main has a unit with at least 60
health (`fit_hp` of `[raids]`), is main's alone to answer: main goes to the
threat's cell with `attack_move` under base defense's override, the guard
and the other units near the base get no order for it and stay home, and
nothing is called home. main is let go back to its plan when the threat
ends or after 60 seconds (`harvester_response_seconds`), whichever comes
first; once the threat comes within 12 cells of the base, or the plan turns
to PRESS, STRIKE or FINISH, base defense answers it as with the switch off.
The harvesters run to the nearest defense as always. While main answers a
major threat and is at least 1.3 times (`engage_ratio` of `[combat]`) as
strong as the threat's enemies in sight, no counter infantry is trained for
it. Each step is a `base_defense` event with the threat's cell: `sent`,
`arrived` (main's centre within 15 cells of it), and `ended`, `timed_out`,
`handed_over` or `declined` (a far rescue main would lose, below). Watch it
with:

    python scripts/watch_game.py --set tuning:base_defense.harvester_group_response=true

With `truck_retreat` of `[base_defense]` (on), an ore truck retreats only
from what it cannot crush, in place of fleeing from any enemy within
`harvester_flee_cells`. Its threats are the enemy units seen in the last
10 s and the known enemy ground defenses within `truck_threat_cells` (10),
every enemy ground defense whose weapon range and a cell reach it, and,
while base defense names the truck as attacked, every enemy unit in sight
whose weapon reaches it. It retreats when they hold a vehicle, an aircraft
or a defense, more than `truck_crushable_infantry_max` (3) infantry, or two
rocket or shock troopers, but not from a lone unit worth less than
`truck_harasser_max_value` (400), neither a defense nor an aircraft, while it
has half its health or more. Its shelter is the nearest of the own
refineries with an own ground defense (not an AA gun or SAM) within 8 cells
and the middles of main and the guard with a unit fit to fight that lie
farther from the threats' middle than the truck, else base_center; the move
goes again every 3 s while it stands idle short of it. After
`truck_retreat_clear_seconds` (8) with no such threat it harvests its
field's ore again (its truck share's field). Retreating trucks count as
fleeing for the other truck rules; each retreat and return is a
`base_defense` event of the reason `truck_retreat` (truck, threat kinds,
shelter) or `truck_return` (truck, field).

`keep_attack_army` of `[base_defense]` keeps main on the attack. It is not
measured and ships on, in the watched game's set. With it on, while the plan is PRESS, STRIKE or
FINISH, a major threat draws no unit of main in and does not call main
home, unless it is worth more than the guard (at its cost and remaining
health) and the own standing defenses within 15 cells (`threat_area_cells`)
of the threat's cell together; then it is answered as with the switch off.
Base defense's override is set on the units answering a threat, and on the
members of a group it calls home, one by one, not on their whole group: the
rest of main keeps the orders of its plan. A threat whose enemies have all
been aircraft, until one on the ground is seen in it, is answered only by
the units whose weapon hits aircraft (and own aircraft); no other ground
unit is told anything for it, no group is called home, and with
`harvester_group_response` on main is not sent to it. The AA defenses fire
as always, and the harvesters still run from armed aircraft near them.
Watch it with:

    python scripts/watch_game.py --set tuning:base_defense.keep_attack_army=true

Base defense keeps main out of a far rescue it would lose
(`src/ucla_agent/games/redalert/far_rescue.py`). It is not a switch: it
always applies, and it is not measured. A threat whose cell lies more than
`far_rescue_cells` (20) from the centre of the base draws no unit of main in
and does not call main home, under any plan and whatever the commander
leaves main to, when either holds:

- main, every unit of it wherever it stands, weighed as `fight.weigh` weighs
  a group against the threat's enemies in sight within 15 cells
  (`threat_area_cells`) of the threat's cell and the enemy ground defenses
  whose reach takes in that cell (each at twice its cost), is less than
  `far_rescue_min_ratio` (1.0) as strong as they are. With no enemy in sight
  there, there is nothing to weigh and the rescue goes as before;
- a known enemy construction yard or ground defense stands within 14 cells
  of the threat's cell: the enemy's base is next to it.

main then stays under its plan, or the commander's order. Every unit of
main that base defense or the harvester response had already sent to that
threat gets an emergency `move` to the staging point in the cycle main is
kept out, so that none is left in the losing fight without an order. A unit
a strike, a crush run or a repair retreat holds keeps its own order, and is
called back in the first cycle after it is let go; the combat manager
publishes the units a strike or a repair retreat holds as `combat_held` of
the match context. With `harvester_group_response` on, main is not sent to
such a harvester threat either, the guard and the units near the base stay
home as the switch has them, and main out answering one when it becomes
such a rescue is let go (`declined`) with those moves home. In game
20261013T130117Z at 09:35, the 15 units of main base defense's harvester
response had sent toward (72, 27) got no order for 3.4 seconds after the
decline at a ratio of 0.93, and most were lost. The guard, the other units
near the base, the
raid group, the harvesters and the defenses answer as before, and a threat
within 20 cells of the centre of the base is answered as before. Once main
is kept out of a threat, it stays out while the threat stays against the
same cell, so enemies stepping out of sight do not send it after all. The
first time in a threat that main is kept out of an answer it would have
taken part in, one `base_defense` event of reason `far_rescue_declined`
gives the tick, the threat's cell, its distance from the centre of the base
(`distance_cells`), main's ratio there (`ratio`, null with no enemy in sight),
the enemy yard or ground defense near it (`enemy_building`, null when
there is none) and the ids of the units called back in that cycle
(`recalled`). In the lost game of the diag-i28-loss diagnosis, main was
called about 30 cells to a far yard at ratios of 0.38 and 0.31 there, with an
enemy yard 7 cells from it, and was wiped out.

The keys, each read strictly, built in and in the tracked file:

    [base_defense] far_rescue_cells = 20
                   far_rescue_min_ratio = 1.0

Watch the rule held back to threats beyond the map with:

    python scripts/watch_game.py --set tuning:base_defense.far_rescue_cells=999

and follow it in a match record's events with:

    grep '"far_rescue_declined"' runs/<run>/<match folder>/events.jsonl

Base defense leaves an infantry threat to a tank army
(`src/ucla_agent/games/redalert/base_defense.py`). It is not a switch: it
applies whenever `main_force` of `[mix]` is "tanks", as shipped, and it is
not measured. While a major threat lasts, base defense trains the counter to
the class of enemy the threat is mostly worth, three at a time
(`counter_at_a_time`): rifles against infantry, rocket soldiers against
vehicles. Once a war factory stands and main holds 4 combat units or more
(`COUNTER_MIN_MAIN_UNITS`), an infantry-class threat gets no counter and the
Infantry queue is left to the managers after base defense: tanks crush
infantry, and in the horde losses of the diag-i31-maps diagnosis 30 to 87
rifles a game were trained against masses of rifles backed by artillery
while the tanks went short of money. A threat is infantry-class when
infantry is more than half of what its enemies in sight that are infantry
or vehicles are worth (`_is_infantry_threat`); a mixed threat with vehicles
at half or more is vehicle-class and still gets rocket soldiers, as every
vehicle threat does. The extra defense is unchanged, and so are the
counters before a war factory stands, while main holds fewer than 4 combat
units (a base with no army yet still answers a rush with rifles) and with
`main_force` "mixed". The first time in a threat that its counters are left
to the tanks, one `base_defense` event of reason `counter_skipped_tanks`
gives the tick, the threat's cell, its size (`threat_size`) and the combat
units main holds (`main_units`). Follow it in a match record's events with:

    grep '"counter_skipped_tanks"' runs/<run>/<match folder>/events.jsonl

The enemy army estimate, the spending focus and the counter quota. Four
switches, all on and none measured yet, weigh the enemy army that is out of
sight and answer it; none calls a model.

- The enemy army estimate. `enemy_army_estimate` of `[plan_rules]` has the
  plan rules bring an estimate E of the enemy army, in credits, up to date
  once a cycle (`enemy_estimate`): the seen value V (the remembered enemy
  units that fight on the ground and are not stale), or, when more, the last
  E grown by the enemy's income on its army (its harvesters, the more of the
  harvesters and refineries remembered, times our own income a harvester or
  `estimate_income_per_harvester` 8.0 a second, `estimate_army_spend_share`
  0.5 of it to the army, for up to `estimate_growth_max_seconds` 240 after an
  army unit was last in sight) less what we killed since. E is never below V
  or 0 and is unknown until an army unit was seen; one `enemy_estimate`
  event at most every 30 s gives it.
- The spending focus. `spending_focus` of `[upkeep]` has upkeep pick, every
  `focus_every_ticks` (250) once the opening's build is complete, where the
  money goes (`spending_focus`): military while E is known, E is at least
  `military_focus_ratio` (1.2) x our army A (the cost of the combat units in
  main, the guard and the escort) and either is 1,000 or more, until E is at
  most `military_focus_end_ratio` (1.0) x A; economy while A is at least
  `economy_focus_ratio` (1.5) x E (until it falls under 1.3 x E) or the
  enemy is greedy (2 or more refineries remembered, worth E or more at 1,400
  each); balanced otherwise, before the opening is done and while E is
  unknown and the enemy not greedy. A focus other than military holds
  `focus_hold_seconds` (30); a change to military is at once. Under military
  no new MCV is wanted (`second_mcv_blocked` names `military_focus`) and no
  claim, growth, third or fourth or bank refinery is ordered or saved for,
  while an MCV out and a refinery on order go on and the harvesters for the
  refineries standing are trained; the Vehicle queue's next army vehicle is
  the family's artillery (arty, v2rl) while main and the guard hold fewer
  than `military_focus_first_artillery` (2); and one ground defense goes up
  every `military_focus_defense_seconds` (60), `military_focus_defenses_max`
  (2) a military period, on the side of the base facing the enemy's start,
  the defense against armor while vehicles make up `vehicle_heavy_share` of
  the enemy seen, else the one against infantry, whatever
  `defenses_on_threat_only` says. Under economy the yards allowed are one
  more, `max_yards` at the most, and a due harvester goes into the Vehicle
  queue before the army's next vehicle. Each change is a `spending_focus`
  event with the focus, its reason (`enemy_ahead`, `matched`, `army_ahead`,
  `greedy_enemy`, `balanced`), E and A.
- The counter quota. `counter_quota` of `[composition]` turns the enemy
  units that fight seen in the last `counter_seen_seconds` (120), aircraft
  left out, into counter units the unit queues train first, the item the
  army is furthest short of, infantry `infantry_at_a_time` at a time
  (`counter_units`). While the enemy is infantry-heavy (its infantry at
  least `anti_infantry_min_infantry` 6 and `anti_infantry_enemy_share` 0.4
  of the units counted), one anti-infantry unit for every
  `anti_infantry_per_rockets` (2) rocket soldiers and every
  `anti_infantry_per_infantry` (4) other infantry, rounded up: the family's
  artillery while it is on offer and in the row, else Soviet flamethrowers,
  never grenadiers (one flame tower is ordered for them, once a match,
  toward the enemy), else Allied riflemen with half of them Ranger jeeps
  once a war factory stands. While 4 or more enemy tanks are seen, one
  rocket soldier for each `anti_armor_per_tanks` (2). No counter unit is
  trained while those of the quota's items are worth `counter_max_share`
  (0.35) of main, the guard and the escort: the main tank keeps the rest.
  Each change is a `counter_quota` event.
- The infantry counter lift. `infantry_counter_lift` of `[composition]`
  lifts the tank army's block above while the enemy is infantry-heavy: an
  infantry threat gets the family's counter infantry (the Soviet
  flamethrower while on offer, else the rifleman, never the grenadier) and
  one `base_defense` event of reason `counter_lifted_infantry_heavy` instead
  of `counter_skipped_tanks`, and `no_army_infantry` lets the quota's
  riflemen through up to the army's shortfall of them. Once the enemy is no
  longer infantry-heavy the block holds as before.

Follow them in a match record's events with:

    grep -E '"(enemy_estimate|spending_focus|counter_quota)"' runs/<run>/<match folder>/events.jsonl
    grep '"counter_lifted_infantry_heavy"' runs/<run>/<match folder>/events.jsonl

Their keys, as shipped:

    [plan_rules]
    enemy_army_estimate = true
    estimate_income_per_harvester = 8.0
    estimate_army_spend_share = 0.5
    estimate_growth_max_seconds = 240
    [upkeep]
    spending_focus = true
    focus_every_ticks = 250
    military_focus_ratio = 1.2
    military_focus_end_ratio = 1.0
    economy_focus_ratio = 1.5
    focus_hold_seconds = 30
    military_focus_first_artillery = 2
    military_focus_defense_seconds = 60
    military_focus_defenses_max = 2
    [composition]
    counter_quota = true
    counter_seen_seconds = 120
    anti_infantry_per_rockets = 2
    anti_infantry_per_infantry = 4
    anti_infantry_min_infantry = 6
    anti_infantry_enemy_share = 0.4
    anti_armor_per_tanks = 2
    counter_max_share = 0.35
    infantry_counter_lift = true

The endgame lead, the expansion sites and the cover of the expansions. Four
switches, all on and none measured yet, keep a late lead and the expansions;
none calls a model, and with each off the agent plays as before.

- The endgame lead defenses. `endgame_lead_defenses` of `[clock]` adds the
  clock rule R2E (`endgame_lead`, `plans`): in the last
  `endgame_lead_seconds` (300), with the trade balance B (kills less
  losses, the time-limit tiebreak) at `endgame_lead_margin` (1,000) or more,
  our army A (main, guard and escort at cost) and the enemy army estimate E
  both worth `endgame_heavy_army_value` (5,000) or more and R6 not applying,
  the plan is PRESERVE by R2E (after R6 with `finish_over_preserve` on,
  before R2 to R4). It stays, whatever the armies, until B has stood at 0 or
  below for `clock_switch_ticks` (250) in a row, or R6 applies. Meanwhile
  (`clock.EndgameLeadDefense`) the Defense queue, whenever it is free and
  the money covers it, gets the Tesla coil (Soviets) or the turret (Allies),
  the defense against infantry when neither is on offer, held to the power
  target with a power plant first, up to `endgame_lead_defenses_max` (15),
  the production reserve, `defenses_on_threat_only` and `endgame_defenses`
  not holding it back; its goal is the lead point C,
  `endgame_defense_forward_cells` (10) from base_center toward the enemy's
  start, so that placement puts it on the placeable cell nearest C, within
  `endgame_defense_cluster_cells` (7) of it; with no such cell, where
  placement puts any defense, on the side facing the enemy. The unit
  queues and the end-of-match spending get only the money above the next
  defense's price, and PreserveDefense's defense a minute does not run.
  Main gathers at C, the producers rally there, and main engages only
  enemies within `endgame_army_leash_cells` (8) of C (the recall of groups
  beyond `preserve_recall_cells` stays). Each defense ordered is a `clock`
  event of the reason `endgame_lead_defense` with the item, C, B, A and E.
- The site line penalty. `mcv_line_penalty` of `[expansion]` ranks the MCV's
  sites with a penalty for a field ahead of the base near the line from
  base_center to the enemy's start (`expansion.line_penalty`):
  `mcv_line_penalty_cells` (20) on the line, falling evenly to 0 at
  `mcv_line_clear_cells` (15) off it, none behind the base (no positive
  depth) or with the enemy's start unknown. `far_site` takes the least route
  length plus penalty, `safe_site` tries each group of fields by distance
  from the main yard plus penalty; no field is ruled out by it. The `site`
  event gives `line_penalty`.
- The MCV's escort reliance. `mcv_escort_reliance` of `[expansion]` has the
  MCV heed only its route threat (`expansion.route_threat`): the ground
  enemy units that fight seen in the last 10 s within
  `mcv_route_threat_cells` (10) of its remaining route (its waypoint way,
  else the straight line) or 15 cells of its site, worth W, and only when
  W > `mcv_escort_win_ratio` (1.0) x its escort S (waiting, the escort it
  would take; travelling, the escort's units within 15 cells of it). A major
  threat on the base no longer keeps it home by itself; such a group makes
  it wait (`mcv_waiting` reason `route_threat`), hold short (`mcv_hold`
  reason `escort_outmatched` with W and S, sited again after
  `mcv_site_hold_seconds` with that reason) and never deploy beside it; a
  group it beats does not stop it, and the `rerouted` choice when it is hurt
  or near unsafe ground is made only for a group it cannot beat. Every site
  chosen again (escort_outmatched, rerouted, taken, enemy_building_near,
  own_side) lies within `mcv_resite_max_cells` (25) of route from the MCV,
  else it goes home (`home_fallback`) or waits (`repeat_no_site`).
  `mcv_site_army_value` (600) is read only with the switch off.
- The rally covering the expansions. `expansion_cover_rally` of `[combat]`
  has main, under OPENING (once a war factory stands), HOLD and RAID while
  an own construction yard other than the main yard stands, gather at the
  cover point whatever its strength (`forward_hold.cover_point`), and the
  barracks and war factories rally there: on the line from base_center to
  the enemy's start, `expansion_cover_forward_cells` (6) beyond the deepest
  expansion yard, stepped back half a cell at a time to the first cell at
  most halfway, outside every remembered defense's reach plus
  `forward_hold_defense_clear_cells` (4) and within
  `expansion_cover_reach_cells` (18) of every expansion yard; with no such
  cell on the line, 6 cells from the farthest-forward yard toward the enemy,
  pulled back the same way; else the staging point. It is worked out again
  when the yards or the remembered defenses change and every 125 ticks.
  Main fights, chases and falls back there as at forward_hold's point, and
  it replaces that point with both switches on; each change is a
  `hold_position` event of the reason `cover_expansions`. Under PRESS,
  STRIKE, FINISH and PRESERVE, and without an expansion, nothing changes.

Follow them in a match record's events with:

    grep '"endgame_lead_defense"' runs/<run>/<match folder>/events.jsonl
    grep -E '"(R2E|escort_outmatched|route_threat|cover_expansions)"' runs/<run>/<match folder>/events.jsonl

Their keys, as shipped:

    [clock]
    endgame_lead_defenses = true
    endgame_lead_seconds = 300
    endgame_lead_margin = 1000
    endgame_heavy_army_value = 5000
    endgame_lead_defenses_max = 15
    endgame_defense_forward_cells = 10
    endgame_defense_cluster_cells = 7
    endgame_army_leash_cells = 8
    [expansion]
    mcv_line_penalty = true
    mcv_line_penalty_cells = 20
    mcv_line_clear_cells = 15
    mcv_escort_reliance = true
    mcv_route_threat_cells = 10
    mcv_escort_win_ratio = 1.0
    mcv_resite_max_cells = 25
    [combat]
    expansion_cover_rally = true
    expansion_cover_forward_cells = 6
    expansion_cover_reach_cells = 18

The switch of the opening's defenses was measured too: with `rush_detection`
on, it won 95 games at a mean kd of 9,983, and 7 games fewer for Russia
against the Rush bot. It ships on all the same, in the watched game's set:

- `defense_on_threat` of `[opening]` has the opening buy its defense
  against infantry (a pillbox for the Allies, a flame tower for the Soviets)
  only when the rush watch says `rush`, still says `unknown` at 150 seconds
  (`rush_unknown_deadline_seconds` of `[scouting]`), or base defense reports
  a threat before the war factory stands; and its cover against armor (the
  gun, four rocket soldiers for the guard, or a second defense against
  infantry) only when the watch says `rush` and an enemy vehicle that fights
  has been seen, such a vehicle is seen within 18 cells of the base
  (`armor_near_base_cells`), or base defense reports a major threat; 150
  seconds of play (`anti_armor_at_seconds`) no longer brings the cover on its
  own. Without one of these the opening buys no defense, the money stays
  with the economy and production, and the guard's five rifles still form;
  upkeep then replaces the defense against infantry only once one has stood.
  With `rush_detection` off, only the threats seen count. Where a defense
  stands, and the later defenses (replacements, Tesla coils, anti-air, clock
  spending and base defense's extra defense under a major threat), are as
  with the switch off, but for `defenses_on_threat_only` below. Watch it off
  with `--set tuning:opening.defense_on_threat=false`.

With the switch off, the opening buys its defense against infantry as soon
as the barracks stands and its cover against armor at 150 seconds or at the
first sight of enemy armor.

One more opening switch, `lean_infantry` of `[opening]`, is not measured and
ships on, in the watched game's set. To watch a game with it off, run
`python scripts/watch_game.py --set tuning:opening.lean_infantry=false`. With
it on, the opening trains `lean_guard_rifles` (1) rifle soldier for the
home guard in place of five, none for main in place of six, and never
chooses rocket soldiers as its cover against armor: the cover is the gun,
else a second defense against infantry. Once the rush watch says `rush`
(`rush_detection` of `[scouting]`), the five guard rifles, the six for main
and the rocket cover come back for the rest of the match.

An upkeep switch, `defenses_on_threat_only` of `[upkeep]`, is not measured
either and ships on, in the watched game's set. It goes with the opening's
`defense_on_threat`, so watch the three off together:

    python scripts/watch_game.py --set tuning:upkeep.defenses_on_threat_only=false --set tuning:opening.defense_on_threat=false --set tuning:scouting.rush_detection=false

With it on, upkeep buys a defense only against a real threat:

- A lost defense of the opening is not replaced.
- A Soviet Tesla coil is ordered only after a vehicle attack: a threat of
  base defense led by vehicles that hit an own building or harvester, which
  the match keeps one cell for. Each attack more than `tesla_side_cells`
  (12) from every coil standing or on its way allows one coil, placed at
  the free place nearest the attack, while it goes on too, and no more than
  `soviet_tesla_coils` (2) in all.
- An AA defense is ordered only while more than a few enemy combat aircraft
  are seen (`[anti_air]`, the army build above), with the switch on or off;
  a helipad or airfield seen calls for none.
- The defense a preserved lead adds under PRESERVE is ordered only while a
  ground threat is active: a threat of aircraft alone does not count.

The end-of-match spending and base defense's extra defense under a major
threat are as with the switch off.

**Reactive defenses.** A defense is bought only in reaction to a trigger: a
ground threat, aircraft for the anti-air defenses (unchanged), or an enemy
building in range of a harassment tower. Each rule:

- **End of match.** The spending of the last two minutes (see the clock
  play above) leaves the Defense queue out, and its money goes to the
  Vehicle and Infantry queues, unless `endgame_defenses` of `[clock]` is on
  (it ships off) or a ground enemy hit an own building within the last 60
  seconds. Base defense notes the tick of every hit on an own building in a
  threat that is not of aircraft alone (`MatchContext.ground_hit_tick`).
- **Opening.** With `defense_on_unknown` of `[opening]` off (as shipped), a
  rush watch that still says `unknown` at its deadline no longer calls for
  the opening's pillbox or flame tower: a rush told, or a threat before the
  war factory stands, still does.
- **Rush counter** (`rush_counter`, keys of `[opening]`). Nothing is bought
  up front. In the cycle the rush verdict is `rush`, or base defense reports
  a threat at the base before a war factory stands, the opening orders its
  defense against infantry (pbox / ftur) until 2 (`rush_counter_defenses`, 0
  switches it off) stand or are on their way, the second as soon as the
  Defense queue is free again, whatever the threat and outside base
  defense's one extra defense a minute; those standing count, at most 2 come
  from the rule, and none is ordered before the barracks stands. In 48 rush
  games two defenses within about 70 seconds of the wave won 12 of 13, one
  won 1 of 10. While the rush watch warns, upkeep holds $1,200
  (`rush_reserve_cash`) on top of `production_cash_reserve` from refineries
  beyond the second, barracks and war factories beyond the first, the radar
  and the tech centers; never from harvesters, power, the service depot,
  defenses, the opening's steps or units. While the verdict is `rush` and
  main holds fewer than 3 tanks (`rush_min_tanks`), base defense's counter
  trains infantry against an infantry threat even with a war factory under
  a tank army (the `counter_skipped_tanks` rule stays out). While the verdict
  is `rush` and the game time is below 330 seconds
  (`rush_hold_until_seconds`), the agent keeps main within 20 cells of
  base_center: main's units are taken out of every move, attack-move or
  attack leading farther, whoever ordered it (the commander, the forward
  hold, intercepts, strikes, presses, raids), and those standing farther out
  are moved back to the staging point. With `rush_harvester_crush` on, under
  the same trigger as the defenses, one harvester at 50 percent health or
  more is driven to the centroid of the largest enemy infantry cluster in
  sight (the crush runs' cluster rules) within 15 cells of base_center when
  no own tank or APC stands within `crush_reach_cells` of it and its
  infantry is worth half a harvester or more (`harvester_crush`): taken out
  of every other order while it runs, back to harvesting when it arrives,
  the cluster breaks or 15 seconds pass, each run a `combat` event with the
  reason `harvester_crush`. The opening gun waits for an enemy vehicle
  that fights seen within `armor_near_base_cells` (18) of the base center,
  whatever else made the cover due.
- **Extra defense.** Base defense's extra defense under a major threat is
  ordered only when the threat's ground enemies have hit an own building or
  stand within `extra_defense_near_cells` (12) of a main-base building (an
  own building within `threat_area_cells` of the base center), and our
  ground strength (combat units and ground defenses, at cost and remaining
  health) within 12 cells of them is below `extra_defense_strength_share`
  (1.5) times theirs. A threat that only hit harvesters far out, or only of
  aircraft, adds none. One is ordered for each threat at the most, and one
  per `extra_defense_seconds` (120). Its goal is the threat's ground enemy
  nearest the base center (`MatchContext.defense_goals`), and upkeep places
  it at the free place nearest that, by the yard nearest it: on the side of
  the base facing the threat.
- **Lead-preserving defense.** Under PRESERVE, with
  `defenses_on_threat_only` on, the defense a preserved lead adds needs a
  ground threat: a threat whose enemies have all been aircraft adds none.
- **Harassment tower.** With `harass_tower` of `[base_defense]` on (as
  shipped), every 5 seconds while the Defense queue is free, base defense
  looks for a known enemy building within the range of the family's cheap
  ground defense (the pillbox or the gun, the flame tower or the Tesla coil,
  the first tried first) of a place the base can put it at (the candidates
  around each own construction yard) within `far_rescue_cells` (20) of the
  centre of the base; none goes up while the centre is not known. In game
  20261013T130117Z, 3 of 4 pillboxes and both turrets went up 28 to 31 cells
  out by the far second base and were all lost there, with one pillbox at
  home when the Soviet push came. A place is passed over when an enemy
  army worth 1,000 or more stands within 10 cells of it, or when a known
  enemy ground defense's range plus 2 cells reaches it. The defense is
  ordered with that place as its goal, so upkeep places it there, and one
  `base_defense` event of reason `harass_tower` names the item, the place
  and the target. At most `harass_tower_max` (2) towers stand or are on
  their way, and each enemy building is the target of one in the match. A
  harassment tower is our own defense and never a must-kill target.

The keys, each read strictly, built in and in the tracked file:

    [clock]        endgame_defenses = false
    [opening]      defense_on_unknown = false
                   armor_near_base_cells = 18
    [base_defense] extra_defense_seconds = 120
                   extra_defense_near_cells = 12
                   extra_defense_strength_share = 1.5
                   harass_tower = true
                   harass_tower_max = 2

Watch the harassment tower off, and the end-of-match defenses and the
opening's pillbox for an unknown verdict on, with:

    python scripts/watch_game.py --set tuning:base_defense.harass_tower=false --set tuning:clock.endgame_defenses=true --set tuning:opening.defense_on_unknown=true

and follow the towers in a match record's events with:

    grep '"harass_tower"' runs/<run>/<match folder>/events.jsonl

**Defenses at the front.** With `defense_front` of `[placement]` on (as
shipped) and the enemy's start known, every ground defense placed without a
goal of its own (the opening's, the rush counter's, a lost defense's
replacement, the lead-preserving and end-of-match ones, and an AA defense
before enemy air is seen) stands at the enemy-facing front of its base. The
base is the construction yard the defense is laid out around (the first
yard; with several, the one nearest the defense's point, as before) and the
own buildings but defenses whose nearest yard it is. Its front building is
the one whose middle lies farthest along the line from the yard's middle
toward the enemy's start, and the point is 3 cells (`defense_front_cells`)
beyond that building's middle toward the enemy; with ground defenses of the
base already on the enemy's side of the yard, the goal is halfway between
that point and their middle, so that the front's defenses stand together.
The places are those of the yard's ring and every free place within
`defense_adjacent_cells` (7) of a building of the base, on the usual ground
(bounds, gaps, blocked terrain, refused places, the MCV's site), with no
`defense_radius_cells` clamp; those whose middle lies behind the yard are
left out while one on the enemy's side remains. The defense takes the place
nearest the goal among those within `defense_cluster_cells` (6) of it, else
the nearest, the one nearer the yard of two as near. Base defense's extra
defense toward its threat, the Tesla coil's side, the harassment tower, an
AA defense once enemy air is seen and a remote base's AA defense are placed
as before, as is every other building; with the enemy's start unknown the
old point, `defense_distance_cells` (5) from the yard within
`defense_radius_cells` (6) of it, holds. In the final platform games the
base crept away from its first yard and the defenses aimed 5 cells from it
were built in the middle of the base. Off, every placement is as before.

    [placement]    defense_front = true
                   defense_front_cells = 3
                   defense_cluster_cells = 6

Watch it off with:

    python scripts/watch_game.py --set tuning:placement.defense_front=false

Four more switches of `[upkeep]` are not measured either, and each ships
on, in the watched game's set; each can be watched off with its `--set`
set to `false`.

`no_army_infantry` stops the army's rifles once a war factory stands. The
Infantry queue is then given rocket soldiers while fewer than
`rockets_kept` (4) stand in main, the guard and the escort; then the
rocket soldiers of the mix, and the flamethrowers of the Soviet infantry
counter, that the army is short of for the share of its value the mix
gives them, the one furthest short first; after that it is given nothing.
The Vehicle queue goes first whenever the money pays for one queue only.
With 12 medium tanks and 4 rockets standing at the dome stage, the
rockets' 15 percent of 11,400, 1,710, asks for 2 more. No barracks
is added beyond the first
while a war factory stands. A raid quota's infantry, base defense's
counters to a major threat (but those it leaves to a tank army, above),
scout training and the end-of-match spending are trained as with the switch
off, and so is everything while no war
factory stands. Watch it off with
`python scripts/watch_game.py --set tuning:upkeep.no_army_infantry=false`.

`economy_first` has the unit queues wait for the economy after the
opening. While a refinery claim is due (see `refinery_claims_reached`), one
more war factory's gates but the money are met, or the second yard's MCV's
gates but the money are met, and the money does not cover the first of
these yet, the unit queues are given nothing new, so that the money
gathers; one more war factory is then ordered only once the money covers
its price and the production reserve. A hold ends when its item is
ordered, when it is due no longer, or after `economy_wait_seconds` (20);
the unit queues are then fed for `economy_release_seconds` (20) at the
least before the next. While the ore is near the storage cap (see the
producers' storage rule above) no hold holds the unit queues. Power plants,
base defense's emergency orders and whatever else is ordered ahead of the
unit queues never wait. Each start and end of a hold is an `economy_hold`
event, with the item and the reason `started`, `ordered`, `timed_out`,
`not_due` or `storage_near_cap`. With `refinery_unserved_only` on, a
claim's or growth refinery is no item of the hold while it has nowhere to go
(`waiting_unserved`, see the refinery fields above). It goes with the war
factories, the claims and the second yard, all on as shipped; watch it off
with:

    python scripts/watch_game.py --set tuning:upkeep.economy_first=false

`lean_power` keeps less power to spare: the power target is
`headroom_share` (15 percent) of what the base drains and will drain once
everything on its way stands, rounded up, and `headroom_early` (20) at the
least, in place of the targets of the sixth minute and of many producers.
The Tesla coils' target still applies, and a building that would leave
less than the target over still waits for a power plant, so the base is
never planned short of power. Watch it off with
`python scripts/watch_game.py --set tuning:upkeep.lean_power=false`.

`mcv_first` buys the second yard's MCV first, and needs `second_yard` of
`[expansion]`. Once a service depot stands and the MCV is wanted (fewer
than two yards, no MCV alive or on its way, no major threat), the MCV is
due first, whatever `second_yard_from_seconds` says. While its money
(`second_yard_min_cash` above `production_cash_reserve`) is short, a full
hold is in force: upkeep orders no barracks or war factory, refinery or
refinery claim, service depot, radar or kennel, none of its own defenses
and no unit of the army mix. A power plant is still ordered when the base
would be short of power without one, though not for the rest of the power
target, and so are base defense's emergency orders, a lost core building,
harvesters up to one a refinery and the end-of-match spending. The scout
slots order no jeep or flak truck while the MCV is due and give up the
Vehicle queue to it; while the hold is in force they refill an empty slot
only with a rifleman, once every slot is empty. A hold lasts
`mcv_first_wait_seconds` (30) at most and ends when the MCV is ordered or
is wanted no longer; then nothing is held for `economy_release_seconds`
(20). The MCV is asked for right after power and a lost core building,
ahead of every other upkeep order in the cycle its money is there. Each
start and end of a hold is an `economy_hold` event with the item `mcv`, the
scope `all` and the reason `started`, `ordered`, `timed_out` or `not_due`.
With `economy_first` on as well, its own hold no longer waits for the MCV
and gives way to the full hold. Watch it off with:

    python scripts/watch_game.py --set tuning:upkeep.mcv_first=false

`mcv_safe_site` of `[expansion]` chooses where that MCV deploys, and is not
measured either and ships on, in the watched game's set; it too needs
`second_yard`. The site is by
the nearest ore field within `mcv_site_max_cells` (30) of the main yard (the
construction yard nearest the base's center) that no own refinery stands
within `refinery_ore_cells` (2) of, that lies more than
`creep_enemy_clear_cells` (25) from the enemy's start and that has no
remembered enemy defense within `creep_defense_clear_cells` (10), and only
when a safe route reaches its place from the MCV: the straight line of
cells, else the shorter way by one of the two side waypoints
`side_offset_cells` (12) of `[raids]` off it. A cell is unsafe when a
remembered enemy defense covers it, or when it lies within an enemy unit's
weapon range and `mcv_danger_margin_cells` (3) of where that unit was seen
in the last `mcv_enemy_seen_ticks` (1,500 ticks, a minute). A field
placement skipped is one more such field, so `mcv_to_skipped_fields` does
nothing of its own then. Without such a field the MCV deploys at home. An
MCV on its way that loses health, or comes within 8 cells of unsafe ground,
is sited again from where it stands, once in 250 ticks at most, and goes on
with its escort to the new site, or home when there is none. Each choice is
an `expansion` event of phase `site` with the field's center, its distance
from the main yard and the reason `nearest_safe`, `rerouted` or
`home_fallback`. With `second_mcv_waits` of `[expansion]` (on), the second
MCV left with no safe site on its way (`home_fallback`, also after a site
army) moves home and waits there without deploying, looks for a site again
from where it stands every 10 s and sets out once one is safe, and deploys at
home only after `second_mcv_wait_seconds` (90) without one; in game
20261014T050310Z it had deployed at home at once at 08:58. Watch it with the
second yard, and the MCV first:

    python scripts/watch_game.py --set tuning:expansion.second_yard=true --set tuning:expansion.mcv_safe_site=true --set tuning:upkeep.mcv_first=true

`mcv_new_area` of `[expansion]` sends that MCV to a new area, and is not
measured either and ships on, in the watched game's set; it needs
`mcv_safe_site`. The site's field is
never a taken field (a refinery standing, asked or on order against it), a
claim field or the creep target's field, and of the rest the fields whose
center lies `mcv_refinery_spacing_cells` (12) or more from every own
refinery, standing or asked, come first, nearest the main yard first. The
safe route and the 30-cell limit stay; without such a field the site is
chosen as with the switch off, then at home. A site whose field becomes
taken while the MCV is on its way is chosen again, as a `site` event with
the reason `taken`, within the same 250 ticks as a choice made again for
danger. Watch it with the safe site:

    python scripts/watch_game.py --set tuning:expansion.second_yard=true --set tuning:expansion.mcv_safe_site=true --set tuning:expansion.mcv_new_area=true

`mcv_site_min_ore_cells` (30) of `[expansion]` keeps the second yard off a
field the base already harvests, or one soon mined out. It is not measured
and ships with `mcv_new_area`, which it needs. In a watched game the second
yard deployed 11 cells from home, by the home field, which ran dry about a
minute later. A field counts as taken only with a refinery within 2 cells
of its ore, and the home refinery stood 3 cells off; the spacing from
refineries was only a preference, and no spaced field lay within 30 cells.
Now every choice of a site with `mcv_new_area` on first tries the fields the
site lasts by, nearest the main yard first. Such a field is of a new area,
has every own refinery, standing or asked, `mcv_refinery_spacing_cells` (12)
or more from its center (a hard test here), has at least
`mcv_site_min_ore_cells` ore cells, and holds no own harvester's last cell
on ore. The state tells no ore per cell, so a field's ore cells are its cells
in the map's picture, none once the move to fresh ore marks it dry
(`harvesters_to_fresh_ore`). Without such a field whose place a safe route
reaches, the site is chosen as before, and an `expansion` event of phase
`site_check` with the reason `site_fallback`, the site, the field and its
distance comes right before that `site` event. With `mcv_safe_site` on as
well, an MCV that has waited `mcv_wait_ticks` (1,500) with the want of its
escort the reason goes on to the site it has, with the escort it has or
alone, when a safe route reaches the site from it: an `mcv_moving` event with
the reason `escort_wait_timeout`. Without a safe route it deploys at home as
before, and a wait never gives it a nearer site. On that watched game's map
the check would still have written `site_fallback` and kept the site: both
fields within 30 cells were harvested, and the one field the yard could last
by was the creep target, 35 cells off. In the record, a `site` event with no
`site_check` event of the same tick before it is by a field the site lasts
by. Watch it as it ships, or with the ore test off for comparison:

    python scripts/watch_game.py --model none --set tuning:expansion.mcv_site_min_ore_cells=0

`mcv_reserve_field` of `[expansion]` keeps the MCV's field for the MCV, and
is not measured either and ships on, in the watched game's set; it works with `second_yard` and
`mcv_safe_site`. Without it, a refinery of the main base could be asked
against the field the MCV was heading for, and the MCV turned back home. With
it, the site is chosen in the cycle the MCV is ordered (the `site` event then
comes at the order), and from then on, while the MCV is on order, waiting or
on its way, upkeep publishes that field as reserved at the start of every
cycle: no refinery is asked onto a place against it, by today's rules,
`refinery_spread`, a claim or growth, it is no claim field, and
`mcv_new_area`'s check for a taken field passes it over. A refinery that could
stand against no other field waits, unplaced, and is looked at again every
`place_again_after_ticks` (50). The MCV keeps the site chosen at its order
when it comes out, unless the field became taken or no safe route reaches it
from the MCV; the reservation moves with a site chosen again, and ends once
the MCV deploys, is lost or is sent home. In the record, the `site` event's
field should be the field of the `deployed` event, with no `refinery_field`
event against it before. Watch it with the safe site, the new area and the
spread of refineries:

    python scripts/watch_game.py --set tuning:expansion.second_yard=true --set tuning:expansion.mcv_safe_site=true --set tuning:expansion.mcv_new_area=true --set tuning:placement.refinery_spread=true --set tuning:expansion.mcv_reserve_field=true

`mcv_short_escort` of `[expansion]` lets an MCV short of its escort set out,
and is not measured either and ships on, in the watched game's set. Without
it, an MCV waits at home
until main units worth `mcv_escort_strength` (1,500) can go with it, a minute
in a watched game. With it, once the want of its escort has been the one
reason it waited for `short_escort_wait_ticks` (500 ticks, 20 s) in a row, it
sets out with the escort units it has; with none, only on a safe route
(`mcv_safe_site`'s test from the MCV when that is on, else no remembered
defense within 10 cells of the site and no enemy army seen lately within
15). A major threat, a defense or a stronger enemy army near the site keeps
it waiting as before and starts the count again; the deploy at home after
`mcv_wait_ticks` stays. Setting out short is an `expansion` event of phase
`mcv_moving` with the reason `escort_short_timeout`, 500 ticks after the
`mcv_waiting` event with `escort_short`. Watch it with the second yard:

    python scripts/watch_game.py --set tuning:expansion.second_yard=true --set tuning:expansion.mcv_safe_site=true --set tuning:expansion.mcv_short_escort=true

`mcv_repeat` of `[expansion]` lets the yards grow with the income, and is
not measured either and ships on, in the watched game's set; it works with
`second_yard`. The yards allowed are one, and one more for each
`yards_income_per_yard` (40) of income a second as the income estimate
reports it (none while it is unknown), `max_yards` (6) at the most: 70 a
second allows 2, 120 allows 4, 400 allows 6. Standing own yards and MCVs out
or on order count against it, and a lost yard frees its place. Each change
of the cap is an `expansion` event of phase and reason `yard_cap`:

    {"tick": 9000, "phase": "yard_cap", "cell": null, "field_center": null, "reason": "yard_cap", "income": 120.0, "yards": 2, "mcvs": 0, "cap": 4}

An MCV, the second yard's and the further ones alike, is wanted while the
yards and MCVs are fewer than allowed, `second_yard_from_seconds` (240) of the
match have passed, more than `repeat_mcv_cutoff_seconds` (180) are left and a
far field qualifies, on the second MCV's other terms: a depot, no MCV out or
on order, no major threat, `mcv_first`'s hold, and the money above
`production_cash_reserve` covering the MCV's price and
`second_yard_min_cash`. One MCV is built or out at a time. The site is by a
far field that lasts, whatever `mcv_safe_site` says: a field that is not
taken, no claim field and no field an own yard already stands by (within
`mcv_deploy_cells` of its ore by walking), with at least `mcv_site_min_ore_cells` (30) ore
cells, no own refinery, standing or asked, within
`mcv_refinery_spacing_cells` (12) of its center and no own harvester's last
harvest in it, its center within `mcv_far_site_max_cells` (50) of the nearest
own yard (in place of `mcv_site_max_cells`), clear of the enemy's start and of
remembered defenses, with a place for a yard a safe route reaches: the
straight line or the way by a side waypoint, or with `waypoints` of
`[routing]` on the straight line or the graph's way. Of those, the one whose
route is shortest. A field's place for a yard is the free place whose yard
box lies within `mcv_deploy_cells` (4) of the field's ore by walking round
cliffs, water and trees, the fewest steps first, then the one nearest the
field's ore cell nearest the base; a place within 4 cells in a straight line
that is farther by walking is refused. On sidestep's top field, behind a
row of cliff, the site the straight line gave in game 20261013T220250Z,
(81, 27), lies 2 cells from the ore in a straight line and 14 by walking;
the site is now (78, 18), on the walkable side, 1 step from the ore. The crawl target's field qualifies like any other, however
near own buildings stand to it: the MCV does not keep to the crawl. This
removes the crawl rule of requirement `mcv-far-fields-income-cap` (the crawl
target only when no other field qualified and more than `CRAWL_CLEAR_CELLS`,
20, from every own building); in game 20261013T142816Z it kept the south
field (13, 76), the crawl target from 02:27, from the MCV while the war
factory stood 19.7 cells from it, and the MCV was ordered at 06:54 though the
cap, the time and the money allowed one from about 04:00. An MCV on order or
out that goes to the crawl target puts it among the skipped fields, and the
crawl names its next target: the MCV's reservation keeps refineries off that
field, so a crawl kept on it would creep toward a field no refinery may stand
against. With `mcv_new_area` the crawl target's field is no longer passed over
as old ground either. The route starts at the main yard before the MCV is out and at the
MCV's own cell after; upkeep looks for the site once a cycle. In the watched
game 20261012T235612Z the bank sat at 4,500 to 5,900 from 05:00 to 07:00
while the field 33 cells south was barred by the 30 cells, and the enemy took
it. The MCV goes with an escort, waits, is sited again on its way and has its
field reserved (with `mcv_reserve_field`) as the second one does, and the
`mcv_trained` and `site` events of an MCV beyond the second give the reason
`repeat`.
It never deploys by the main base: without a site it waits at home, with
`mcv_waiting` and `site` events of the reason `repeat_no_site`, and looks
again every 250 ticks; after `mcv_wait_ticks` (1,500) it deploys at the free
place for a yard nearest the second yard when a safe route reaches it, else
where it stands, a `deployed_home` event with that cell. Every yard deployed
this way is followed, and each loss is a `lost` event. Watch it with the
other expansion switches and the economy's growth:

    python scripts/watch_game.py --set tuning:expansion.second_yard=true --set tuning:expansion.mcv_safe_site=true --set tuning:expansion.mcv_new_area=true --set tuning:expansion.mcv_reserve_field=true --set tuning:expansion.mcv_short_escort=true --set tuning:expansion.mcv_repeat=true --set tuning:upkeep.refinery_growth=true

`second_mcv_due_seconds` (300) of `[expansion]` makes the second yard due by
a time; it works with `mcv_repeat`. From then on, while one own yard stands
and no MCV is out or on order, the yards allowed are 2 at the least whatever
the income, the money the MCV waits for is its price alone, and upkeep asks
the MCV's rule right after power and a lost core building, ahead of every war
factory beyond the first and every other building, as `mcv_first` does.
While the money is short, `mcv_first`'s full hold is in force with no time
limit and no release, until the MCV is on its way, and a due refinery claim
still goes ahead of it. The depot, the MCV on offer, no major
threat, no lost core building saved for, a far field that qualifies and the
Vehicle queue's room still apply. While one of them keeps the MCV from its
order, an `expansion` event of phase and reason `second_mcv_blocked` names
it, once in 30 s at most:

    {"tick": 7500, "phase": "second_mcv_blocked", "cell": null, "field_center": null, "reason": "second_mcv_blocked", "blocked_by": "site"}

`blocked_by` is the first that holds of `depot`, `major_threat`,
`core_saved`, `site` (`own_side` when a far field would qualify but for the
own-side rule below), `money` and `queue`. In the watched game
20261013T052655Z the MCV was ordered at 07:00 and deployed at 08:47. With
`mcv_site_min_ore_cells` at 40 the two fields of 37 ore cells within reach
did not qualify, and the one field of 47 was the crawl target, barred while
own buildings stood within 20 cells of it, until the crawl gave it up at
06:29; once ordered, the MCV waited behind three vehicles on the Vehicle
queue. Watch it with the time moved:

    python scripts/watch_game.py --set tuning:expansion.second_mcv_due_seconds=240

`mcv_enemy_building_cells` (14) of `[expansion]` keeps the MCV off the
enemy's base. With `mcv_safe_site` or `mcv_repeat` on, a site does not
qualify while a known enemy construction yard, refinery or ground defense (a
defense that does not shoot at aircraft alone) stands within 14 cells of it:
no choice of a site takes it, and a site chosen at the MCV's order no longer
holds when the MCV comes out. With `mcv_safe_site` on, an MCV waiting for its
escort or on its way whose site becomes such a place is sited again from
where it stands, once in 250 ticks at most, as a `site` event of the reason
`enemy_building_near`; with no site left it goes home as before:

    {"tick": 12650, "phase": "site", "cell": [23, 28], "field_center": [23, 25], "reason": "enemy_building_near", "distance_cells": 25.0}

The move to fresh ore sends no harvester to a field no own refinery serves
whose center lies within 14 cells of a known enemy building of any kind; a
field one serves still takes them, and the served-field room sends to served
fields alone. In the watched game 20261013T093754Z the yard at (49, 19)
deployed 23 s after an enemy yard was seen 10 cells away, and a turret
killed it and two harvesters. Watch a game with the distance wider:

    python scripts/watch_game.py --set tuning:expansion.mcv_enemy_building_cells=20

`mcv_own_side_share` (1.1) of `[expansion]` keeps every MCV on the base's own
side of the map or in its middle. With `mcv_safe_site` or `mcv_repeat` on, a
site qualifies only when its distance from the base's center is at most 1.1
of its distance from the enemy's start, the spawn the world model takes as
the enemy's: an untouched field in the middle of the map qualifies, as the
north field (53, 13) of game 20261014T015507Z, whose site lay at 0.883 and
which the enemy took at 05:51 while the share was 0.8. The rule measures the site, the cell the MCV deploys on, not the
field's center. Every choice of a site passes over a place off the own side,
and a site chosen at the MCV's order no longer holds when the MCV comes out.
A second MCV due by its time that has a far field only off the own side is
not ordered, and `second_mcv_blocked` names `own_side`:

    {"tick": 7500, "phase": "second_mcv_blocked", "cell": null, "field_center": null, "reason": "second_mcv_blocked", "blocked_by": "own_side"}

With `mcv_safe_site` on, an MCV waiting or on its way whose site no longer
lies on the own side, as when the enemy's start becomes known after the site
was chosen, is sited again from where it stands as for `enemy_building_near`,
as a `site` event of the reason `own_side`; with no site left it goes home as
before:

    {"tick": 12650, "phase": "site", "cell": [23, 79], "field_center": [23, 85], "reason": "own_side", "distance_cells": 35.0}

An MCV whose refused deploy would be tried next at a place off the own side
does as after too many refusals: the second MCV deploys at home, a
`deployed_home` event of the reason `own_side`. While the enemy's start is
unknown, the rule does not apply. In the lost game of diag-i28-loss the
second yard went up at (50, 17), toward the enemy, and an enemy yard went up
7 cells from it: `mcv_enemy_building_cells` and the re-site now keep a site
14 cells from such a yard. Watch a game with the rule as strict as before:

    python scripts/watch_game.py --set tuning:expansion.mcv_own_side_share=0.8

`mcv_site_army_value` (600) and `mcv_site_hold_seconds` (30) of `[expansion]`
check the site for an enemy army while the MCV travels, not only while it
waits at home. While the MCV is on its way to its site or has a deploy out
there, enemy combat units seen within the last 10 s standing within 15 cells
(`SITE_ARMY_CLEAR_CELLS`) of the site and worth together 600 or more (cost
by health share) make it hold short: a stop once it stands 15 cells or more
from every one of them, before that a move straight away from the nearest to
16 cells, and one `expansion` event of phase `mcv_hold`:

    {"tick": 12650, "phase": "mcv_hold", "cell": [20, 68], "field_center": [23, 68], "reason": "site_army", "value": 1500, "units": 3}

A deploy out is dropped, and no deploy goes out while such a group stands
there: the check runs before every deploy order, and a refused deploy's next
place is checked as its deploy would go out. Once the group has stood there
30 s, the MCV is sited again from its cell by the same rules with that field,
and every other one it held short of, passed over, as a `site` event of the
reason `site_army`; with no site left it goes home (`home_fallback`). Once
the group is gone the hold ends and the MCV goes on. The other refusal and
re-site rules stay. In game 20261013T142816Z the new yard at (19, 78) was
lost 21 s after it deployed: the enemy army escorting its own MCV to the same
field stood by the site, and only enemies within weapon range and 3 cells of
it re-sited a travelling MCV. Watch a game with a higher value:

    python scripts/watch_game.py --set tuning:expansion.mcv_site_army_value=1500

Three fixes keep the second yard and the Building queue from stalling, with
no switch: in a watched game the platform answered `ok` to 86 deploys the
engine left without effect, a pillbox stood where the MCV's yard was to
unfold, and a refinery for the field reserved for the MCV waited unplaced
for six minutes, holding the Building queue.

- The engine unfolds the yard with its top-left corner one cell up and one
  left of the MCV's cell, a box of 3 by 4 cells. A site by a field is a
  cell whose box is free ground, and while the MCV lives with a site,
  upkeep asks no building onto the box, defenses included.
- A deploy left without effect is noticed: when the MCV still stands idle on
  the cell it was sent from 75 ticks after one resend, or after a second
  resend whatever it does, the deploy counts as refused, an `expansion`
  event of the phase `deploy_refused` with the cell and the reason `ignored`
  (`rejected` for a deploy the game refuses). The MCV tries again two cells
  farther from the ore, and after two such deploys it deploys at home; a
  further MCV of `mcv_repeat` falls back by its own rules.
- No refinery is ordered whose only field is the one reserved for the MCV's
  site, with the cap of refineries to a field or without. And a finished
  building of the Building or Defense queue that has waited
  `held_cancel_ticks` (500) of `[placement]` unplaced, held back or asked
  onto a place that never took it, is cancelled with the `cancel` order, so
  that the queue takes the next building; where no cancel order can be sent,
  it is placed on the free place nearest the construction yard.

Each building so cancelled or placed is a `creep_placement` event with the
reason `held_cancelled`, the order and the ticks it waited:

    {"tick": 8000, "building": "proc", "cell": null, "reason": "held_cancelled", "order": "cancel", "held_ticks": 500}

Two more fixes, with no switch, come from the watched game
20261012T235612Z, where the first MCV sat 186 s a few cells from its site
with 876 move orders, its escort tanks parked on the site cell, and its yard's
refinery came 3 min 30 s after the deploy, placed toward the crawl target.

- While the MCV has a site and an escort, no escort order names a cell of the
  yard box grown by 2 cells on every side. The escort attack-moves to the
  MCV's next cell while that lies outside it, else to its hold point: the
  free cell just outside it nearest the MCV, on the side the MCV comes from.
  An escort unit standing inside it is moved to the free cell outside it
  nearest the unit on that side, one unit to a cell, in every cycle the MCV
  goes to its site or waits for its deploy's verdict.
- A yard that deploys by the field of its site claims one refinery for that
  field. It is ordered right after the power plant the power target calls
  for, ahead of every other building and whatever `mcv_first`'s hold, as soon
  as the money covers it and the reserve; it is placed against that field
  from the new yard, whatever the crawl target, and `economy_first` holds the
  unit queues for it as for a claim. A field at its cap of refineries
  (`max_refineries_per_field`, 1) claims nothing, and a yard deployed at home
  claims none.
- With `hold_refinery_for_mcv` of `[expansion]` (on), from the cycle an MCV's
  site is chosen until it deploys or is lost (60 s with no site and no MCV on
  order or out, 120 s at the most), no refinery is ordered for a field one of
  ours serves already (`second`, `second_when_stuck`), so the money goes to
  the MCV and then the new yard's claim; a refinery for an unserved field and
  the opening's are never held, and one `refinery_field` event of the reason
  `held_for_mcv` names the field held. In game 20261014T030940Z the site was
  chosen at 04:33, a `second_when_stuck` refinery went onto the served home
  field (64, 57) at 05:07 and the MCV was trained only at 05:13.
- With `new_yard_refinery_until_standing` of `[expansion]` (on), the new
  yard's claim lasts until a refinery of ours stands counted against its
  field (serving it within `refinery_serves_cells`, else within
  `refinery_counts_cells`), or the yard is lost, whatever the refinery caps
  say; a refinery refused and put elsewhere leaves it open. The refinery is
  ordered only while a place against the field exists that placement would
  use: ordering keeps off the places asked for lately and those the engine
  refused, as placement does, so no refinery waits unplaced to be cancelled
  and ordered again. It is placed from the new yard's places, the own units
  on the place or a cell around it moved off first (5 s at the most); a
  refused place is skipped for the next, and with every place refused, those
  refused `new_yard_refused_retry_seconds` (60) or more ago are tried again,
  the oldest first. A later placement of the same claim is a `refinery_field`
  event of the reason `new_yard_retry`, and the claim's end one of the reason
  `new_yard_standing`. In the game vs Yuriko Red of 2026-10-14 the yard at
  [77,17] had its refinery refused at [80,15] and put at home, and the base
  ordered, held and cancelled a refinery about every 50 s to the end.
- With `mcv_refinery_fit` of `[expansion]` (on), an expansion MCV deploys
  only at a cell from which a refinery fits against its site's field: a
  place whose nearest cell lies within `mcv_refinery_fit_cells` (4) of the
  future yard box, on ground placement would take with that yard standing
  (the gap around buildings and the places asked for lately, blocked
  terrain, the cells kept free and the places the engine refused), reaching
  the field within `refinery_ore_cells` and serving it, and out of a known
  enemy ground defense's range plus 2. The cell is the nearest the ore
  within `mcv_deploy_cells` walking steps, else within
  `mcv_fit_search_cells` (8); a field with none is passed over by the site
  choice. As the MCV is to deploy the check runs again: failing, it moves to
  the next such cell, or with none drops the site, an `expansion` event of
  phase `mcv_site_no_refinery_fit`, and is sited again from where it stands.
  The home yard's first deploy is not affected.

The new yard's refinery is a `refinery_field` event with the rule and the
reason `new_yard`:

    {"tick": 13125, "cell": [23, 63], "rule": "new_yard", "field_center": [23, 68], "field_cells": 9, "served_by": 0, "ore_distance_cells": 1, "reason": "new_yard"}

The shipped set has the second yard on, so the agent as it ships shows them;
look for `deploy_refused` events, `held_cancelled` ones, `refinery_field`
events of the reason `new_yard` and `yard_cap` events:

    python scripts/watch_game.py --model none

The switch of the army's hold ships off: measured, it won 92 games at a
mean kd of 8,610. It can be watched in a game with it on:

- `forward_hold` of `[combat]` would have main, under HOLD and once it is
  worth 1,500 in a fight (`forward_hold_min_strength`), gather at a forward
  point instead of the staging point: 40 percent of the way from the base to
  the enemy's start (`forward_hold_share`), or 6 cells
  (`forward_hold_cover_cells`) beyond the nearest own construction yard or
  creep target field that lies farther forward along that line; moved back
  toward the base until it is no farther than halfway and outside every
  remembered enemy defense's range by 4 cells
  (`forward_hold_defense_clear_cells`). The barracks and war factories rally
  there. Main engages the enemy units within 12 cells at a fight ratio of 1.3
  or more, scouts, harvesters and small groups, but never a building or a unit
  a remembered defense covers, and a unit that has chased more than 10 cells
  from the point (`forward_hold_chase_cells`) goes back to it; the units
  standing at the point still wait for the scout, the raid and the MCV's
  escort, the ones chasing do not. Main falls back to the staging point at
  once below a ratio of 0.9, or when enemy units in sight worth more than main
  come at the point, and goes forward again once its ratio has stayed at 1.3
  or more for 5 seconds. Under HOLD it is never sent forward: an army gone
  forward under PRESS, STRIKE or FINISH comes back to the point. Base
  defense's recall and response still come first, and the guard stays home.
  Each change of the gather point is a `hold_position` event with the cell,
  the reason (`forward`, `staging` or `fell_back`) and main's strength. Watch
  it with `--set tuning:combat.forward_hold=true`.

As shipped, main gathers at the staging point under HOLD and goes no farther.

Main with artillery or V2s sets out from the staging point only behind a
screen, its other units, worth at least as much as its long-range units
(`screen_ratio_allies` and `screen_ratio_soviet` of `[siege]`, both 1.0), and
an army gone forward whose screen is no longer worth that much comes back to
the staging point in one move. Under FINISH, which ends the match only by
going on, there is no such gate: main sets out and stays forward whatever its
screen is worth, frontally, its artillery and V2s still placed by the siege's
firing band and slots. This is not measured. With a Soviet ratio of 2.0, a
watched game's FINISH army of about $35,000 (9 heavy tanks, 19 rocket
soldiers and 13 V2s, normal-russia-shadowfiend-ii-922) stood at the staging
point from 08:24 to 12:58, its screen worth 0.93 to 1.41 times its V2s. Watch
a Soviet game at that ratio with:

    python scripts/watch_game.py --faction russia --set tuning:siege.screen_ratio_soviet=2.0

The march of main's screened siege is not measured, and ships on in the
watched game's set.
With `march_when_clear` of `[siege]` on, main's advance with artillery or V2s
under PRESS, STRIKE or FINISH marches while it is clear of the enemy: the
screen attack-moves on the objective, sent again when a unit of it stands
idle away from it or the objective changes, and the long-range units out of
contact follow it to their slots (below). With it off, the screen holds a
line a few cells ahead of the long-range units, which wait for the screen, and a
watched game's advance made about a quarter of a cell a second. The advance
becomes clear once no enemy unit that counts in a fight and no remembered
enemy defense that fires at ground units is within 15 cells
(`march_clear_cells`) of any of its units, and stays clear until one comes
within 12 cells (`march_blocked_cells`), so that an enemy at the edge of
sight does not turn the march off and on cycle after cycle; before it is
clear, and once it no longer is, the screen holds its line. A defense whose
weapon hits aircraft alone, the AA gun (`agun`) or the SAM site (`sam`), is
ignored by the march, the fight estimate and the objective choice, whatever
the switch: it never stops the march, counts nothing in the estimate of a
fight, covers no building main would go on and weighs nothing against main
taking one, and pushes back no screen line or long-range unit's firing band,
even when the game's rules give it a weapon range. In a watched game an AA
gun turned main's march off and on ten times. A clear advance
whose centroid gains less than 5 cells toward its objective
(`march_progress_cells`) in 15 seconds (`march_progress_seconds`) goes
frontal, all of main attack-moving on the objective, until it is no longer
clear or the plan changes; each such switch is a `siege` event with the
reason `stall_frontal`, the plan, the objective, the cell of main's centroid
and the cells it gained. The 15 seconds run on through the time the screen
held its line for an enemy that came and went, and start over only when main
fights or goes back to the staging point, or the plan or the objective
changes. Watch it off with:

    python scripts/watch_game.py --set tuning:siege.march_when_clear=false

Main's artillery and V2s out of contact move to slots of their own, with no
switch, and this is not measured. A long-range unit is out of contact while
no enemy unit in sight and no known defense is within its weapon range and 2
cells. Each such unit is given a cell of its own, its slot, on a line across
the approach toward the objective, 2 cells from the next
(`slot_spacing_cells` of `[siege]`) and centered on the long-range units'
centroid, the unit nearest a slot taking it first. It moves straight there,
and is sent there again only once the slot has moved 3 cells
(`slot_resend_cells`) from the cell it was sent to, or once it has stood idle
50 ticks (`slot_idle_ticks`) away from it, and never twice in a second. On a
march the line lies the band depth behind the screen's front: 8.25 cells for
artillery (the middle of its band of 5 to 11.5 with no defense near) and 7.25
for V2s, behind the screen unit farthest ahead toward the objective among
those within 8 cells of the screen's centroid, so that a straggler or a part
split off does not move it. On a march with no screen at all, the front is
the long-range units' centroid moved on by the band depth, and they hold
where they stand. While the screen holds its line a few cells ahead of the
long-range units, the slots lie as deep as the units are sent toward the
objective there: the middle of its band short of it, no farther ahead than
the line nor the screen (with no screen, unheld), and a unit standing in its
band of the objective stays. A unit in contact fires from its band, closes
in and falls back as it does without slots. In a watched game
every artillery piece was sent to one cell a step ahead of the pieces' own
centroid, about 150 times: they jammed, moved at 0.08 to 0.37 cells a second
against a rated 1.76, and came 17 to 55 seconds late to the fight. Watch the
slots wider apart with:

    python scripts/watch_game.py --set tuning:siege.slot_spacing_cells=3

The screened place is not measured, and ships on: with `artillery_screen_place` of `[siege]` on, main's artillery and
V2 out of contact (and one standing unscreened on the threat's side of the screen), with a screen unit within 10 cells
of them, stand on a line 3 cells (`artillery_offset_cells`) behind the screen's centroid, or the current step point,
on its far side from the threat (the enemies within 15 cells of main, `artillery_threat_cells`, else the stepped
move's destination or the cell a move home came from, else the enemy start), 2 cells apart on open ground out of
every known ground defense's range, at step points, wherever main gathers or holds and in a fight with an enemy near;
sent again once a place moved 3 cells or the threat turned 45 degrees (`artillery_turn_degrees`), and a kite
fall-back goes 4 cells straight away from the enemy, never nearer to another, instead of toward the base (see
`src/ucla_agent/games/redalert/screen_place.py`). Watch it off with:

    python scripts/watch_game.py --set tuning:siege.artillery_screen_place=false

The sticky target of main's FINISH attack is not measured, and ships on in
the watched game's set. With it off, main under FINISH goes in each cycle on
the remembered enemy building nearest to it, so a pullback of a few cells,
or a building seen long ago, can turn it away from the target it was making
for: in a watched game its aim flipped from the defenses it was at to a
tesla coil last seen 100 seconds before, giving up about 10 cells. With
`sticky_attack_target` of `[combat]` on, main keeps one target. It is chosen
among the remembered enemy buildings: the enemy's production (construction
yard, war factory, barracks, airfield and helipad) first, however long ago it
was seen, then the other buildings; within each, one seen in the last 60
seconds (`stale_target_seconds`) before any older one, the nearest to main
among those and the one seen last among the older; with no building
remembered, a remembered MCV. With `nuke_spread` on, a known enemy missile
silo comes before all of them (see the nuke spread below). The target is kept
wherever main goes, a pullback included, until it is no longer in the enemy
memory (destroyed, or its cell seen empty), until a missile silo is known
while it is no silo, or, while it is no production building, until an enemy
production building comes within 12 cells (`contact_cells`) of main's
centroid, which becomes the target. Each change is an `attack_target` event
with the tick, the old and the new target (id, type and cell, or null) and
the reason: `none` for a first target, `lost`, `missile_silo` or
`production_in_contact`. PRESS and STRIKE choose their objective as before.
Watch it off with:

    python scripts/watch_game.py --set tuning:combat.sticky_attack_target=false

The crush run of the agent's tanks is not measured, and ships on. Without it
a tank fights enemy infantry as any other enemy and shoots at it, slowly and
to little effect: in a watched game main's tanks met about ten enemy
soldiers standing close together and fired at them instead of driving over
them, and in a later one 93 states held a cluster that passed every rule but
one, the tank near it being one base defense had taken, which no run could
take then. With `crush_runs` of `[combat]` on, the combat manager looks in
each cycle, before it looks at whether base defense has called main home,
for the largest set of enemy infantry in sight within 3 cells
(`crush_cluster_cells`) of its centroid. It is run over when it has 4
soldiers or more (`crush_min_infantry`), or 3 (`crush_at_min_infantry`) when
it is anti-tank-heavy: rocket soldiers (e3) and shock troopers (shok, the
Tesla infantry) make up at least 0.33 of it (`crush_at_share`). One of the
crushing vehicles a run may take must stand within 10 cells
(`crush_reach_cells`) of its centroid, no remembered enemy defense that fires
at ground units may cover the centroid (its range and 2 cells; an AA gun or a
SAM site is no cover), and the own combat units within 15 cells of the
centroid, tanks included, must weigh at least as much (`crush_min_ratio`,
1.0: a unit advantage) as the enemy combat units in sight within 15 cells of
it, as the estimate of a fight weighs them. The crushing vehicles are the
items whose role is `tank` (the light, medium, heavy and mammoth tanks) and
the APC, and a run may take any of them that is main's (called home or not,
but the commander's raid detachment, the units joining main through the
commander's forward gate and those sent back on their own), the guard's, or
one base defense has taken to answer a threat. The cluster's planned count
is one of them for every 3 soldiers (`crush_per_tanks`), 3 at the most
(`crush_max_tanks`), or against an anti-tank-heavy cluster one for every 2
(`crush_at_per_tanks`), 5 at the most (`crush_at_max_tanks`), since rockets
and Tesla guns kill some tanks on the way in: 3 for 12 rifles, 5 for 12
soldiers of which 4 are rocket soldiers, 2 for 4 rifles. A run sends that
many, never fewer than 2 (`crush_min_tanks`), of the eligible tanks: the
nearest to the centroid within 10 cells of it with at least 50 percent
health (`crush_min_hp`); a tank farther off is never sent. A run starts only
when its whole count is found, and all of them get their orders in the same
cycle; with fewer, none goes and the cluster is told skipped with the gate
`short_group`. In game 20261013T130117Z two runs went one tank at a time, two
minutes apart, into a cluster of 13 soldiers, and across the iteration-31
games (tmp/diag-i31-maps) the 11 runs that went short of their planned count
into clusters of 9 or more traded -2,400 and lost 8 tanks, where the other
104 runs traded +17,700 for 10 tanks lost. Losing a tank is fine when the
crush kills pay for it. Each gets a `move` to the cell 2 cells past the
centroid on the line from where it stands, kept inside the map and off
ground the terrain blocks, so that it drives through the cluster. Until its
run ends a tank on it takes no other order of main's, no attack, march,
gathering, screen, siege or forward hold, nor the model commander's; only an
emergency of main's, its retreat or the tank's own withdrawal, still reaches
it. No other manager's order reaches it either: base defense neither sends
it to a threat nor calls it home while it runs, and sends it once the run
lets it go, and the agent takes the run's tanks out of every other order of
a batch, base defense's attack-move in the cycle the run starts included.
The other vehicles near the run keep their focus targets. A tank that
reaches its cell takes its group's orders again. The run ends when every
tank has got through, when fewer of the cluster's soldiers than it qualified
at (4, or 3 when anti-tank-heavy) stay together within 3 cells of its
centroid, or after 8 seconds.

One run is in force at a time, and a run-over detachment of the model
commander's (J) counts as that run and keeps priority: while one is out no
run of the script's starts, and while a run is in force the commander offers
none, the combat manager publishing the run's tanks for it
(`MatchContext.crush_run`, and `MatchContext.run_over_out` the other way).
The J detachment keeps its own size, min(3, ceil(soldiers / 3)) tanks, with
no minimum and no wait for its full count: it may still send a single tank,
`crush_min_tanks` and the full count being the script's runs' alone. Each run, when it ends, is a `combat` event with the
reason `crush_run`: the tick, the tick it started, the cell of the cluster's
centroid, the cluster's size, the ids of the tanks sent, how many of the
cluster's soldiers were killed during the run (`infantry_killed`) and what
they cost (`infantry_killed_cost`), how many of the tanks sent were lost
meanwhile (`tanks_lost`), the trade (`trade`: the soldiers' cost less the
lost tanks' cost) and why it ended (`arrived`, `cluster_broken` or
`timed_out`), for example:

    {"tick": 9210, "reason": "crush_run", "started_tick": 9010, "cell": [49, 48], "cluster": 6, "tanks": [301, 300], "infantry_killed": 2, "infantry_killed_cost": 600, "tanks_lost": 1, "trade": -550, "ended": "arrived"}

A cluster no run starts over says why: in a watched game 167 states held 6
enemy soldiers bunched within 10 cells of one of our tanks and no run
started, and nothing told which rule held it back. So with `crush_runs` on,
in a cycle in which no run starts, the largest cluster of enemy soldiers in
sight, when it has 4 or more (3 when anti-tank-heavy) and lies within 10
cells of any of our crushing vehicles, whatever its group, is a `combat`
event with the reason `crush_skipped`, unless it shares soldiers with the
run in force. It gives the tick, the cell of the cluster's centroid, its size
and the first rule that kept a run from starting (`gate`), taken in this
order: `run_active` (a run is already in force, or the commander's run-over
detachment is out), `no_tank_in_main` (none of the crushing vehicles a run
may take is within 10 cells, such as when only a raid's or a scout's tanks
are near), `tank_hp` (none of those within 10 cells has 50 percent health),
`too_few_tanks` (fewer than 2 of them have it), `short_group` (2 or more have
it, but fewer than the run's planned count, such as 2 next to 12 rifles or 4
next to 12 soldiers with 4 rocket soldiers), `ratio` (the fight around it is
below 1.0), `cover` (a remembered defense that fires at ground units
covers the centroid) and `other` (none of these, such as no open cell to run
to). A cluster is told at most once in 10 seconds for its centroid rounded to
4 cells. The event changes nothing a run does. Watch it off with:

    python scripts/watch_game.py --set tuning:combat.crush_runs=false

The crush wave is not measured, and ships on: with `crush_wave` of `[combat]` on (and `crush_runs`), an enemy
infantry all-in, 8 or more infantry within 6 cells of their centroid (`wave_min_infantry`, `wave_mass_cells`) within
25 cells of the base's center or 12 of our crushing vehicles, is met by one wave instead of the script's runs of
three: every fit crushing vehicle within 16 cells (3 to 10 of them) gathers at a point 8 cells outside the mass on
their side, moved out of any known ground defense's coverage, and once 75 percent of them stand within 3 cells of it,
or after 6 seconds with 2 or more there, all those there are ordered through the mass in one cycle on lanes 2 cells
apart to 4 cells past it, a wave run of 10 seconds at most from which no tank is released on arrival; the stragglers
go as one second wave of 2 or more, never alone. The wave is the one crush run while it is out (no script run, no
run-over of the commander's, no other manager's order), and writes `combat` events of reason `crush_wave` (wave,
mass, gather, tanks, present, total, waited_s, launch) and `crush_run` with its `wave` (see
`src/ucla_agent/games/redalert/crush_wave.py`). Watch it off with:

    python scripts/watch_game.py --set tuning:combat.crush_wave=false

The nuke spread is not measured, and ships on: it acts only once an enemy
missile silo (`mslo`, which the item table does not list; a variant is known
by its name beginning so) has been seen. In a watched game an enemy nuke
wiped out main standing idle in one clump at home. With `nuke_spread` of
`[combat]` on, the first sighting of each enemy silo sets when its nukes are
expected: at that sighting + 13,500 ticks (`nuke_charge_ticks`) - 1,500
ticks (`nuke_build_ticks`), then every 13,500 ticks, for as long as the silo
is remembered. From 1,500 ticks before each expected launch until 500 after
it, main gathering or standing idle (under PRESERVE, at the HOLD gather
point, at the staging point, and the units waiting there to reinforce the
army) is split into 3 groups (`nuke_spread_groups`) at the open cells nearest
to where it would gather that lie at least 8 cells apart
(`nuke_spread_cells`) and at least 12 cells (`nuke_keep_off_cells`) from the
center of our densest cluster of structures (the own buildings within 5
cells of the one that has the most of them). A unit within 4 cells of its
group's cell is at its place, and counts as gathered when main is to go
forward; under PRESERVE a unit far from the base is sent to its place rather
than to the staging point. A main that fights is not spread. A known silo is
also main's first FINISH target, with `sticky_attack_target` on or off, and,
where no remembered defense covers it, a raid's first target. Each start and
each end of the spreading is a `combat` event with the reason `nuke_spread`,
the tick, whether main is now spread, the expected launch and the ids of the
silos remembered, for example:

    {"tick": 11500, "reason": "nuke_spread", "spreading": true, "launch_tick": 13000, "silos": [700]}

Watch the match record's `combat` events in a game whose enemy builds a
silo, and the agent with it off with:

    python scripts/watch_game.py --set tuning:combat.nuke_spread=false

The strike near us is not measured, and ships on. In a diagnosed game an
enemy MCV was in sight from 09:18, was never attacked, and deployed 10 cells
from our north refinery; a gun turret 6.3 cells from that refinery killed it
at 13:44, and the artillery sent at the turret was called back at 14:21.
With `strike_near_expansion` of `[combat]` on (`strike`), some enemy objects
must die while they were in sight in the last 30 seconds:

- **An expansion:** an enemy MCV, or an enemy construction yard, within 15
  cells (`expansion_near_cells`) of any own building or of the nearest cell
  of any field one of our harvesters harvests.
- **A defense:** an enemy ground defense (never an AA gun or a SAM site)
  whose weapon range plus 4 cells (`defense_near_refinery_margin_cells`)
  reaches any own refinery: a gun turret (6.5 cells) within 10.5 cells.

Only enemy objects are weighed: our own defenses, the harassment towers among
them, never are. A must-kill target is struck on the odds the over-extended
strike below is held to. In game 20261013T130117Z the strike started on an
enemy yard at a ratio of 99, a yard counting for nothing, with the Soviet
army beside it: one tank went alone, the infantry trickled after it, and a
later restart with no strike force sent main as a whole 35 cells out, where
the Soviet wave wiped it out, about $15,000 lost. One strike is in force at a
time, on the target nearest the center of our base that main may strike:

- **The ratio at the target** is our units' that can hurt it against the
  enemy units in sight within 8 cells of it and every ground defense whose
  range and 2 cells reach it, a defense at twice its cost. An MCV or a yard
  with nothing that fights beside it reads 1.5, not 99: the enemy army's
  estimate decides then.
- **The strike force** is the smallest set of main's units on no detachment
  of the commander's and gathered at the staging point (at least 80% of
  them, `gathered_share`, within 6 cells of it, `gathered_cells`) that can
  hurt the target, the nearest to it first and for a defense the artillery
  and V2s first, whose ratio at the target is at least 1.5
  (`strike_min_ratio`) and which is worth at least half the enemy army's
  estimate (the commander's peak or the scout report's enemy combat value,
  whichever is more). Main straggling home never goes one unit at a time.
- **Main as a whole.** While main cannot field a strike force at the staging
  point, main as a whole strikes the target only when it lies within 20
  cells (`far_rescue_cells` of `[base_defense]`) of the center of our base:
  main makes it its objective under every plan, going forward whatever
  strength the plan asks, and the target is the model commander's objective
  and its advance's destination, until a strike force forms. A target
  farther off is not struck at all; the next target is tried, and each one
  declined is a `combat` event with the reason `strike_near_declined`, at
  most once in 30 seconds for that target. While a must-kill target is due,
  struck or declined, no over-extended strike starts.
- **Its orders:** it attack-moves to the target, and its units within 6 cells
  of the target in sight attack it; on a defense its artillery and V2s fire
  from their band.
- **No recall:** its units take none of main's orders: no change of plan,
  gathering, forward gate, question or pick of the commander's moves them.
  In every cycle the strike's ratio at the target and its own fight where it
  stands (the estimate around its units: the enemy met on the way) are
  checked against 0.9 (`strike_abandon_ratio` of `[plan_rules]`): under it,
  the strike ends `abandoned`, its units (main's, for main as a whole) are
  moved to the staging point at once, and the target is left alone for 30
  seconds. Base defense calling main home or taking it over against a major
  threat ends it too.
- **The end:** the target destroyed, unseen for 30 seconds, or every unit of
  the strike force lost.

An enemy ground defense that reaches our refinery is still a must-kill
target, struck only on these terms. Each start and end is a `combat` event
with the reason `strike_near`; the outcome of an end is `destroyed`,
`unseen`, `lost`, `abandoned` or `recalled`, and a declined target gives its
distance from the base, the units gathered at the staging point, their
ratio at the target and strength, and the enemy army's estimate, for
example:

    {"tick": 14100, "reason": "strike_near", "phase": "start", "target": 544, "target_type": "gun", "cell": [73, 49], "units": [300, 310, 311], "ratio": 1.5}
    {"tick": 14720, "reason": "strike_near", "phase": "end", "target": 544, "outcome": "destroyed"}
    {"tick": 11900, "reason": "strike_near_declined", "target": 600, "target_type": "fact", "cell": [52, 48], "distance": 30.0, "gathered": 4, "ratio": 0.8, "strength": 4000, "enemy_army": 5000}

The four keys of its own, each read strictly, built in and in the tracked
file; it reads gathered_cells (6) and gathered_share (0.8) of `[combat]`,
far_rescue_cells (20) of `[base_defense]` and strike_abandon_ratio (0.9) of
`[plan_rules]` besides:

    [combat]
    strike_near_expansion = true
    expansion_near_cells = 15
    defense_near_refinery_margin_cells = 4
    strike_min_ratio = 1.5

Watch the match record's `combat` events of reason `strike_near`, and the
agent with it off with:

    python scripts/watch_game.py --set tuning:combat.strike_near_expansion=false

The over-extended strike is not measured, and ships on. An enemy that mines
a field far from its base leaves the refinery and harvesters there out of
reach of its army and its defenses. With `strike_overextended` of `[combat]`
on (`strike`, `overextended`), the same strike machinery takes main against
them. A target is an over-extended field:

- **At a field:** a known enemy refinery whose footprint (its reported cell
  less (1,1), as `refinery_fields` takes our own) lies within 3 cells of an
  ore cell of a field, or an enemy harvester seen in the last 20 seconds
  standing that near one.
- **Over-extended:** more than 25 cells (`overextended_min_enemy_start_cells`)
  from the enemy's start and from every known enemy construction yard, with
  no known enemy ground defense within 8 cells
  (`overextended_defense_clear_cells`) of it.

The refinery and harvesters of one field make one target while one of them
was in sight in the last 20 seconds; the field nearest the center of our
base comes first. The strike starts on the first field not resting when all
of these hold:

- no strike is in force and no must-kill target near us is due (at most one
  strike runs at a time, and the strike near us comes first);
- the base is under no major threat, the plan is not PRESERVE, and the model
  commander's standing action for main is no pull back, retreat home or hold,
  nor is its retreat override on (whatever else the commander picks, the
  script's rule applies);
- at least 80% (`gathered_share`) of main's units on no detachment stand
  within 6 cells (`gathered_cells`) of the staging point: main goes as one
  gathered group, never piecemeal;
- those gathered units that can hurt the field's refinery and harvesters
  are worth at least 1.5 (`strike_min_ratio`) against them by
  `fight.ratio_against_target` (`fight.weigh`): the field counted with the
  enemy units in sight within 8 cells of its lead and every ground defense
  whose range and 2 cells reach it, each at twice its cost;
- and they are worth at least half of what the enemy army is estimated at
  (the model commander's seen peak while it is on, and what scouting reports
  it worth now), so that a refinery that counts for nothing in a fight never
  draws main out against a strong army.

The gathered units are the strike force. The refinery and harvesters there
are its focus targets: a unit within 6 cells of one in sight attacks the
nearest, the others attack-move to the lead's cell. In every cycle the
strike ends:

- `destroyed` once none of the field's refinery and harvesters is left and
  none seen there is remembered; `unseen` once none is left there but one is
  remembered (its harvesters out of sight for 20 seconds);
- `abandoned` once the force's ratio at the field, or the estimate of its own
  fight where it stands (the enemy met on the way), falls under 0.9
  (`strike_abandon_ratio` of `[plan_rules]`); the field then rests for 30
  seconds, as after `lost` (every unit of the force gone);
- `recalled` when base defense calls main home or takes it over, which keeps
  its priority over the strike.

On every end but the recall the force is sent to the staging point at once.
Each start and end is a `combat` event with the reason
`strike_overextended`, for example:

    {"tick": 9100, "reason": "strike_overextended", "phase": "start", "target": 600, "target_type": "proc", "cell": [74, 13], "focus": [600, 620], "units": [300, 301, 302, 303], "ratio": 2.0}
    {"tick": 9850, "reason": "strike_overextended", "phase": "end", "target": 600, "outcome": "destroyed"}

While the model commander commands main, its question's `targets:` block
lists the first over-extended field after the targets near main, however far
it is, weighed as the strike weighs it:

    T3: over-extended field: 1 ore refinery, 1 ore truck at (74,13), 55 cells, win 80%

The three keys, each read strictly, built in and in the tracked file:

    [combat]
    strike_overextended = true
    overextended_min_enemy_start_cells = 25
    overextended_defense_clear_cells = 8

Watch the match record's `combat` events of reason `strike_overextended`,
and the agent with it off with:

    python scripts/watch_game.py --set tuning:combat.strike_overextended=false

The closing-army intercept is not measured, and ships on. In a watched game
an enemy army of 21 units worth $6,200 was seen 24 cells from our travelling
MCV; main, worth $13,800 and about 2.2 times that army, did nothing until the
new yard was hit 18 seconds later: base defense alarms only within 12 cells
of a building or 15 of the center of the base, and the closing watch of the
model commander serves the commander alone. With `closing_intercept` of
`[base_defense]` on (`closing_intercept`), while the model commander does not
command main (it is off, or still commands the scout), the combat manager
watches in every cycle:

- **Targets:** each own construction yard, the travelling MCV (an own MCV
  while a yard stands), the site it goes to (`MatchContext.mcv_site`, the
  middle of its yard's box, published by upkeep) and the center of the base.
- **Trigger:** an enemy army closing on a target: the enemy combat units seen
  in the last 10 seconds within 30 cells (`closing_cells`) of it, worth 1,000
  or more together (`closing_min_value`), whose centroid is closer to it than
  that of the same units 15 seconds before, or none of which was seen then
  (`command_threat.ClosingWatch.closing_on`, the commander's closing watch
  taken to any cell). Of several targets, the one its army stands nearest is
  taken.
- **Gate:** under HOLD or RAID only, never PRESERVE; no major threat in force
  (base defense keeps the threats inside its alarm radius), no strike in
  force, and main not called home or taken over by base defense. An army
  with a unit within 12 cells (`contact_cells` of `[combat]`) of main's
  centroid is main's own fight already and starts no response. One response
  runs at a time.

main, its units on no detachment, strike or join, is weighed against the
army by `fight.ratio_against`:

- **Intercept:** at 1.0 (`closing_min_ratio`) or more, with main gathered at
  the staging point (the strike's gathering rule), every unit of main
  attack-moves to the cell 8 cells (`intercept_front_cells`) in front of the
  target on the line to the army's centroid: the gathered units go, the
  stragglers follow. A cell inside the coverage of a known enemy ground
  defense (its range and `defended_margin_cells` of `[combat]`) is pulled
  back toward the target until it lies outside every one.
- **Defend:** under it, main attack-moves to the target's cell when the
  target lies within 20 cells (`far_rescue_cells`) of the center of the base;
- **Hold:** and holds at the staging point beyond.

Engaged with an enemy near, main fights where it stands, chasing none while
it holds. While main defends or holds against an army closing on the
travelling MCV or its site, the MCV holds where it stood as the response
began (`MatchContext.mcv_halt`, published by the combat manager and obeyed by
the MCV's rule in `expansion` from the next cycle); once the army stands 2
cells nearer it than then, the MCV goes back to the center of the base until
the response ends.

The response ends `gone` once none of the army's units has been seen for 20
seconds, `away` once the army stands beyond 40 cells (`closing_cells` + 10)
of its target, `abandoned` for an intercept once main's ratio against the
army, or the estimate of main's own fight where it stands, falls under 0.9
(`strike_abandon_ratio` of `[plan_rules]`; the target is then not
intercepted again for 30 seconds), `target_gone` once its yard, MCV or site
is gone, and `recalled` when the plan leaves HOLD and RAID, a major threat
comes, a strike starts, or base defense or the commander takes main. On every
end but the recall main is moved to the staging point at once. Each start and
end is a `combat` event with the reason `closing_intercept`, carrying the
response, the target, its cell, where main goes, the army's value and main's
ratio against it, for example:

    {"tick": 11550, "reason": "closing_intercept", "phase": "start", "response": "intercept", "target": "mcv", "target_id": 500, "cell": [36, 56], "to": [44, 55], "value": 6200, "ratio": 2.19}
    {"tick": 12100, "reason": "closing_intercept", "phase": "end", "response": "intercept", "target": "mcv", "target_id": 500, "cell": [36, 56], "to": [44, 55], "value": 6200, "ratio": 2.19, "outcome": "gone"}

The five keys, each read strictly, built in and in the tracked file:

    [base_defense]
    closing_intercept = true
    closing_min_value = 1000
    closing_cells = 30
    intercept_front_cells = 8
    closing_min_ratio = 1.0

Watch the match record's `combat` events of reason `closing_intercept`, and
the agent with it off with:

    python scripts/watch_game.py --set tuning:base_defense.closing_intercept=false

The forward staging is not measured, and ships on. In the watched games
main went on its objective by one attack-move and arrived as it walked: its
units came into a fight over 19 to 46 seconds, and at a line of tesla coils
23 of its 25 units arrived after the fight had started, from 3 to 27 cells
out. With `forward_staging` of `[combat]` on, main setting out on an
objective while a known enemy defense that fires at ground units or a
visible enemy combat unit stands within 20 cells of it stops at a staging
cell first: by its plan (the advance from the staging point and the way on,
its siege and its sweep, under PRESS, STRIKE, FINISH or any plan that sends
it forward), or under the model commander's advance, attack on a group
farther than 10 cells (`near_attack_cells` of `[commander]`) from main's
centroid, flank on its last leg, or siege. The staging cell is the last cell
of main's way (the straight line from its centroid to the cell it heads for,
the next cell of a way through the graph of waypoints while one is left)
before the first one nearer than an enemy's range and 3 cells
(`staging_margin_cells`) to such a defense, or to such a unit within 20
cells of the objective. An AA gun, a SAM site and an aircraft are none, and
a cell the terrain blocks is passed over. There is none, and main goes on as
it would, when no cell of its way is in reach, when its centroid stands in
reach already, or when the cell lies within 4 cells of its centroid. While
it stages, main attack-moves to the cell, its artillery and V2s go to their
slots behind the screen (see the siege slots above), and its units in
contact fire at what is within their weapon range; it goes into no fight
from there. The cell is worked out again in every cycle, and moved when the
one main has is in reach by then or the new one lies 2 cells or more from
it, so that a visible enemy group coming at main pushes it back. Main goes
on into its normal movement once 75 percent of its units
(`staging_body_share`) stand within 6 cells (`staging_body_cells`) of the
cell and every artillery and V2 within 10, or 25 seconds
(`staging_timeout_seconds`) after it was first sent there; then it stages
no more until its objective changes, it retreats, or the commander's forward
gate closes. While the cell stands, from the cycle main is first sent there,
the units joining main (made after it went forward, or joining it through
the commander's open forward gate) go to the cell, moving or not, and on
together to main's centroid once those standing there are worth 1,000
(`reinforcement_strength`), or 30 seconds after the first of them arrived,
in place of the wait at the staging point. Base defense's override and
recall, the commander's retreat override, the crush runs and the
commander's detachments come first. Main's arrival at the cell (its centroid
within 6 cells of it) and its going on are each a `combat` event with the
reason `staging`: the tick, the phase (`arrival` or `engage`), the cell, the
share of main's units within 6 cells of it, whether every artillery and V2
is within 10 and how many main has, and for `engage` why it went on
(`engaged_by`, `body` or `timeout`), for example:

    {"tick": 12365, "reason": "staging", "phase": "engage", "cell": [57, 48], "body_share": 0.89, "long_range_present": true, "long_range": 2, "engaged_by": "body"}

Watch the match record's `combat` events of reason `staging`, and the agent
with it off with:

    python scripts/watch_game.py --set tuning:combat.forward_staging=false

The reinforcements held home are not measured, and ship on: with `reinforce_hold_home` of `[combat]` on, while main's
army stands more than 30 cells (`reinforce_away_cells`) from its home gather point (the forward hold's gather point,
else the staging point), the units joining it (the plan's reinforcements, forward staging's joiners and the commander
gate's joiners) gather and defend there instead of trickling out, and set out only as one group worth 3,000 or more
(`reinforce_group_min_value`), by steps to main's centroid with `stepped_advance` on, joining main within 6 cells of
it; main back near home, they join it by their own rules again (`combat` events of reason `reinforce`, phases `held`
and `sent`; see `src/ucla_agent/games/redalert/reinforce_hold.py`). Watch it off with:

    python scripts/watch_game.py --set tuning:combat.reinforce_hold_home=false

The stepped movement is not measured, and ships on: with `stepped_advance` and `stepped_return` of `[combat]` on, main
goes to a destination 15 cells or more (`step_min_move_cells`) from its centroid, out or home, in steps of 10 cells
(`step_cells`) along its route, each step point snapped off blocked ground and kept out of every known ground defense's
range plus 3 cells, and orders the next step once 80 percent of it (`step_arrive_share`) stands within 4 cells of the
step point with every artillery and V2 within 7, 12 seconds after half of it arrived, or 40 seconds after the step was
ordered, writing `combat` events of reason `step` (phases `start`, `release` and `end`) and taking the place of the
forward staging for a stepped move (see `src/ucla_agent/games/redalert/stepping.py`).
With `step_commit` of `[commander]` on, the model commander keeps main's stepped move against an answer of the same
destination, holds any other answer (a `fallback` of reason `step_commit`) until the step releases unless main's threat
or gate changed, the pick sends a detachment or base defense calls main home, and has its retreat override step main
back one step without closing the forward gate (`override_retreat` with `step_back` and `cell`).
With `met_enemy_decision` of `[combat]` on, a group on a stepped advance that meets enemies (counted ground units within
10 cells of one of its units, ground defenses reaching one by their range and 2) fights them at a ratio of 1.0 or more,
chasing none farther than 5 cells off the step's way and stepping on 3 seconds after the last is gone, and otherwise
steps back together to its previous step point, a decision kept 6 seconds and a fight given up under 0.7, stepping on
once no counted enemy has stood within 15 cells of the abandoned step point for 8 seconds or its ratio reaches 1.2, and
one more step back when met again there under 1.0 (`combat` events `met` and `met_end`); the plan's RETREAT on a
stepped advance is such a step back.
With `moving_returns_fire` of `[combat]` on, main on a move home, a step back or between step points that is caught (a
visible enemy has had one of its units within its range and 1 cell for 2 seconds, or stands within 4 cells of its way
ahead) stops and fights together whatever its ratio, no unit sent farther than 5 cells from where it was caught and the
units more than 12 cells off moved there, the retreat override's and the regroup's moves waiting, and goes on 3 seconds
after the enemy is gone (`combat` events `caught` and `caught_end`).
With `continue_option` of `[commander]` on, a standing advance, flank or siege of main's, or a mission of the scout's,
that the fresh question leaves off while it is still sound (not arrived, its target remembered, no attack on the base
nor a fight lost where main stands, the gate open with no retreat override or regroup due, near home while main is
behind; for the scout no danger flight and a safe way) is offered again as `Z`, `continue: ` and its line, marked
current, in a place left or in that of the lowest-ranked action that may give way; an answer naming it keeps the
standing action, and the commander's summary counts `continue_offered` and `continue_picked` (see
`src/ucla_agent/games/redalert/command_prompt.py`).

The attack's approach, the canary and the commit when the armies cross are not measured, and ship on. While any of
`flank_approach` and `commit_on_cross` of `[combat]`, `canary_post` of `[scouting]` and `canary_alarm` of `[upkeep]` is
on, the combat manager keeps whether main's attack is out (`MatchContext.attack_out`): it sets out when the commander's
forward gate opens or main goes forward as a whole by its plan, and ends when main is sent home, base defense takes it
or calls it home, or main is back within 14 cells of the base (`combat` events `attack_out` and `attack_home`).
With `flank_approach` on, the attack setting out weighs three approaches (direct, and by a side cell 14 cells off the
line 12 cells short of the objective, a side blocked at the map edge, on impassable ground, with no walk within its step
cap or a walk over 1.8 times the direct one) by their exposure (the ground defenses covering them at twice their cost,
the enemy units seen within 30 seconds within 6 cells of them) and sends the whole of main by a side cell only while the
direct way is defended and the side's exposure is 0.6 of the direct one's or less: the plan's march, its steps and its
staging go by it, and so do the commander's advance, siege and attack on a far group, the commander offering no `flank`
meanwhile (`combat` event `approach`; see `src/ucla_agent/games/redalert/attack_approach.py`).
With `canary_post` on, every attack that sets out posts one canary rifleman on the line from the enemy's start toward
our base, 22 cells or more from it, outside its known defenses' coverage and 10 cells from its buildings and 15 or more
from our base: the canary alive, else the rush watch's post rifleman, else one rifleman trained; it holds fire there,
takes no other manager's order, and its death is published (`scout_slot` events of kind `canary`; see
`src/ucla_agent/games/redalert/canary.py`).
With `canary_alarm` on, the canary's death while an attack is out, or the armies crossing, starts the alarm: base
defense orders the anti-armor or anti-infantry defense whenever the Defense queue is free, placed toward the cell 8
cells from the base toward the enemy's start, while upkeep's unit queues train nothing (a harvester only for a refinery
that has none) and no MCV, refinery beyond the second, barracks or war factory beyond the first, radar or tech building
is ordered; it ends beaten, gone after 75 quiet seconds, with main home, or after 180 seconds (`base_defense` events of
reason `canary_alarm`; see `src/ucla_agent/games/redalert/canary_alarm.py`).
With `commit_on_cross` on, main deep in the enemy's ground (nearer their start than our base) while their army heads for
ours (the canary lost, an army closing on the base, their units seen nearer our base, a major threat) decides once: it
commits to the base trade while the way home is not clear, sieges the covering defenses with three artillery or more
when their base is costly by its defenses, commits while their base is beatable (1.2 or more), and goes home by its
reverse route only otherwise; while committed nothing recalls it, base defense, the retreat override and the model's
answers included (`combat` events `armies_crossed`, `cross_decision` and `cross_end`; see
`src/ucla_agent/games/redalert/cross_commit.py`).

No fight under enemy guns is not measured, and ships on. Against an enemy
army standing under its base defenses, crushing the infantry and rushing the
artillery wins in the open and loses under the guns. With `defended_redirect`
of `[combat]` on (`defended`), main's objective, the cell of the model
commander's forward action (attack, siege, flank or advance) or the plan's
objective, is defended while a known enemy ground defense (never an AA gun or
a SAM site) has it within its weapon range and 2 cells
(`defended_margin_cells`): its coverage. Main is then weighed there by
`fight.weigh`: every unit of it against the enemy combat units within 12
cells (`contact_cells`) of the objective and those defenses, each at twice
its cost. Below 1.2 (`defended_engage_ratio`):

- **Hold off:** main sends no order into the coverage of any known ground
  defense. A move or attack-move to a covered cell goes instead to the first
  cell out of the coverage on the way back toward its units; an attack on a
  covered target is kept only for the units already within their weapon
  range of it. Every unit of main is given an order, so a march under way
  stops at the coverage's edge.
- **Redirect:** main takes the best attack point, by worth (cost times
  health) over distance from its centroid: a known enemy construction yard,
  refinery or production building, or an enemy harvester seen in the last 20
  seconds within 3 cells of ore, that no known ground defense covers. One
  whose straight way from main crosses covered ground goes by the graph of
  waypoints (with `waypoints` of `[routing]` on), and is passed over without
  it. Main sets out once 80% of it (`gathered_share`) stands within 6 cells
  (`gathered_cells`) of its rally cell (its centroid, or the first cell out
  of the coverage toward the base), moves to each cell of the way in turn,
  and attack-moves on the point, attacking the target in sight once within 6
  cells. It abandons the point, which then rests for 30 seconds, once its own
  fight or its ratio at the point falls under 0.9 (`strike_abandon_ratio` of
  `[plan_rules]`).
- **Lone defense:** while main has artillery or V2s, a known enemy ground
  defense with no enemy combat unit within 10 cells is an attack point of its
  own. The long-range units stand at its range and 2 cells, on the way from
  it toward main, and attack it from there while it is in sight.
- **Siege fallback:** with no attack point, the artillery and V2s stand just
  beyond the coverage of every known ground defense, on the way from the
  objective toward main and 2 cells apart across it (`slot_spacing_cells` of
  `[siege]`), and fire on what comes into their firing band, falling back
  from what closes in, as the siege's long-range units do.
- **The screen:** in the shelling and the siege, main's other units hold 3
  cells behind the long-range units' front, toward the base, a cell each,
  and those in contact fire at what is within their weapon range. Every 30
  seconds (`vision_dart_seconds`) one fast unit, a jeep, else the fastest
  tank, darts to the middle of the front, at the coverage's edge, to see what
  the long-range units fire on, and then goes back to its cell.

With no defense covering the objective, or at 1.2 and above, the fight, the
siege and the crush runs go as ever. Main striking a must-kill target as a
whole, base defense's override and recall, the retreats, the units sent back
on their own and the crush runs come first. Each redirect (as it sets out),
siege fallback and shelling (as it starts) is a `combat` event with the
reason `defended_redirect`, `defended_siege` or `defense_shelling`, for
example:

    {"tick": 1000, "reason": "defended_redirect", "objective": [60, 48], "ratio": 0.8, "target": 620, "target_type": "proc", "cell": [50, 29], "legs": [], "units": [300, 301, 302, 303]}
    {"tick": 1000, "reason": "defended_siege", "objective": [60, 48], "ratio": 1.09, "defenses": [610, 611], "front": [52, 48]}
    {"tick": 1000, "reason": "defense_shelling", "objective": [60, 48], "ratio": 0.64, "target": 612, "target_type": "pbox", "cell": [45, 28], "front": [45, 36]}

The four keys, each read strictly, built in and in the tracked file:

    [combat]
    defended_redirect = true
    defended_engage_ratio = 1.2
    defended_margin_cells = 2
    vision_dart_seconds = 30

Watch the match record's `combat` events of these reasons, and the agent
with it off with:

    python scripts/watch_game.py --set tuning:combat.defended_redirect=false

Vehicle repair is not measured, and ships on. In three diagnosed games no
vehicle ever regained health: a service depot does not repair a vehicle that
moves onto it, the platform has no order that repairs a unit, and main's
tanks fought on until they were lost. A mechanic (Allies) repairs the damaged
vehicles near it on its own, through its weapon, while it stands idle on its
default stance, as a medic heals infantry. With `vehicle_repair` of
`[combat]` on (`repair`, `support`):

- **The damage rate.** Every own vehicle's health is kept over the last 5
  seconds (`repair_window_seconds`); its damage rate is the hp it lost in
  that time per second, never below 0.
- **Into repair.** A vehicle of main, not on a crush run, a commander's
  detachment or a strike, that cost 700 or more (`repair_min_cost`) goes
  into repair mode at 35% or less (`repair_below_hp`), or at 70% or less
  (`repair_ttd_max_hp`) when its hp over its damage rate is 8 seconds or
  less (`repair_ttd_seconds`): a medium tank at 60% losing 10 a second goes,
  one at 80% losing 5 does not. It also goes at 60% or less
  (`repair_early_hp`) while a mobile mechanic stands within 15 cells of it
  (`repair_mechanic_reach_cells`): a tank at 55% goes with a mechanic 10
  cells away, and not with the nearest one 20 cells away unless a trigger
  above holds. None goes while base defense has main under its override or
  called home. For vehicles this takes the place of the withdrawal of a badly
  damaged unit (`withdraw_*`), which infantry keep.
- **Out of main.** A vehicle in repair mode is none of main's units: it gets
  none of main's orders, the commander's retreat override and regroup gather
  included, and counts in none of main's figures. While the commander's
  forward gate is open it is no longer main's, and what it was worth when the
  gate opened is taken out of main's opening value, so that its going does
  not close the gate (`regroup_value_share`) by itself. Base defense's
  recall under a major threat still takes it.
- **Beside a mechanic.** A mechanic heals only a vehicle that stands still
  beside it, about 2% a second. So a vehicle in repair with a mobile
  mechanic within 15 cells goes to the nearest one's cell by an emergency
  move and stops on it or a cell beside it (diagonals included). It keeps
  that mechanic and gets no other move while its hp rose in the last 5
  seconds; once its hp has not risen for 5 seconds since it rose or was last
  sent, it is sent to the nearest mobile mechanic in reach again. That
  mechanic is held: it stops trailing main and stands for the vehicle until
  the vehicle leaves repair mode.
- **The repair zone**, with no mobile mechanic within 15 cells, the first
  of: the centroid of the mobile mechanics within 25 cells of main's
  centroid; the base repair point, 3 cells from the service depot on its
  side away from the enemy's start, while a base mechanic lives (none with
  the shipped `mechanics_base` of 0); else the staging point, as for the
  Soviets, who have no mechanics. The vehicle is kept within 2 cells of it by
  emergency moves. At the staging point it waits but does not heal.
- **Rejoining.** At 90% (`repair_done_hp`) it rejoins main through the units
  joining it (the commander's forward gate, or the plan's reinforcements).
  At the staging point with no mechanic it also rejoins once main has no
  enemy near, or 90 seconds after it went into repair, and goes into repair
  again only once its hp falls below what it rejoined at.
- **The support units (Allies).** A rule of upkeep before the unit queues
  trains them on the Infantry queue, only when on offer, outside the opening,
  a full hold and the saving for a core building or a refinery, with the
  money for the unit and the production reserve: a mechanic for every 3 of
  main's tanks (`mechanics_per_tanks` of `[composition]`; 1tnk, 2tnk, 3tnk,
  4tnk), up to 3 (`mechanics_max`), on offer once a service depot stands;
  then a medic for every 6 of main's infantry (`medics_per_infantry`) up to
  `medics_max`, which ships 0, so that no medic is trained: vehicle repair
  is what matters, not going further into infantry. While main holds fewer
  than 3 tanks no mechanic is trained: a vehicle in repair then goes to
  the staging point and does not heal there. A mechanic is mobile while fewer
  than `mechanics_max` - `mechanics_base` are, else at the base while fewer
  than `mechanics_base` are, else mobile, so with the shipped `mechanics_base`
  of 0 every mechanic is mobile. The mobile mechanics and the medics trail main 3 cells
  behind the centroid of its artillery (its slot line), or of main when it
  has none, on the side away from the enemy's start, but a held mechanic; the
  base mechanics stand at the base repair point. They get moves alone, never
  an attack or a stance, and are in no group.

In a watched game (diag-i27-game) the mechanics healed about 2% a second
when still, but 6 of 12 retreats died, going in at 22% to 61% near the enemy
and 15 to 38 cells from a mechanic; the mechanics came 3.5 minutes after the
first retreat, and 2 base mechanics stood idle at the depot all game. Hence
the earlier retreat toward a near mechanic. In a lost game (diag-i28-loss) 4
mechanics, 2,000 in all, were trained from 2 vehicles, repaired nothing and
died at home, while those trained beside a large army in diag-i27-game healed
6 vehicles. Hence mechanics only once main holds 6 vehicles, 2 of them, and
none at the base.

The one medic of a watched game (20261013T142816Z, England, at 11:16) was
not trained: no `train` order of `medi` went out and no queue held one. Nine
units came at one tick beside the scout jeep at the end of its retreat, with
no building near: four riflemen, three rocket soldiers, an engineer and the
medic, the Allied squad of a crate (crates are on in the match rules). No
manager queues a medic with the shipped tuning: support's medic is held to
`medics_max` 0, the mix never builds one (`NEVER_BUILT`), the end-of-match
spending buys only what fights, base defense's counters are riflemen and
rocket soldiers, and the model's `tr` shorthand is not wired. A whole fake
match with that crate's squad at 11:16 and its medic lost at 12:30 trains
none (`test_medic_leak`).

Each vehicle going into repair mode is a `combat` event with the reason
`repair` and the phase `enter`, and each leaving it one with the phase
`exit` and how it ended (`healed`, `waited` at the staging point, or `gone`,
lost or no longer main's). The `enter` event names the nearest mobile
mechanic within 15 cells (`mechanic`, null for none), for example:

    {"tick": 9125, "reason": "repair", "phase": "enter", "unit": 412, "type": "2tnk", "hp": 58, "rate": 9.6, "zone": "mobile", "mechanic": 640}
    {"tick": 10450, "reason": "repair", "phase": "exit", "unit": 412, "ended": "healed", "hp": 90, "seconds": 53}

Whether mechanics heal on this platform is to be seen in a watched game: the
fake game does not heal, and its tests cover the orders and the states only.
Watch the match record's `combat` events of reason `repair`, the health of a
vehicle between its `enter` and its `exit`, and the agent with it off with:

    python scripts/watch_game.py --set tuning:combat.vehicle_repair=false

The focus fire in contact is not measured, and ships on. In a watched game
only 22% of main's unit-cycles in contact with the enemy had a targeted
attack, and its rifles shot at vehicles: the model commander's order, which
goes into a batch ahead of every other, took main's units off the targets
the focus fire gave them, the focus fire picked its candidates around main's
centroid and gave none to a unit in a fight far from it, a unit whose best
target was covered went on to one it hardly scratched, main holding or
pulling back fired nothing, and base defense sent its responders to the
threatened cell with one `attack_move`. With `contact_focus` of `[combat]` on:

- a unit's candidate targets are the enemies within the range of its weapon
  that hits them and 4 cells more (`focus_unit_reach_cells`), not those
  within 12 cells of its group's centroid, so that a unit in contact
  (within its weapon range and 2 cells of an enemy in sight) is given a
  target however far its group's centroid stands; the siege's long-range
  units keep theirs around their centroid;
- a unit whose best target is covered moves on to another uncovered target
  only when that one is worth at least 0.25 (`spill_floor`) of its best,
  and joins its best otherwise;
- a pairing with a ground target is worth its value times the unit's damage
  against the target's armor class over its mean damage across the five
  armor classes, raised to `counter_power` (2.0): a rocket soldier, whose
  rockets do 2,500 to `heavy` of a mean of 1,338, takes a 3tnk (worth
  0.146) over anything it hits at its mean, where without the factor a
  rifle (0.048) came before the tank (0.042); a 2tnk takes a 1tnk over a
  rifle, and a rifle a rifle over a 2tnk. Against an aircraft only the
  anti-air damage counts, with no factor. A target whose type has no hit
  points in the item table is counted at the unit's least damage, so that
  it stays after every known target. Coverage, the spill floor, keeping a
  target and the out-of-range factor are unchanged;
- the commander's order leaves out the units given a targeted attack in the
  cycle and those in contact, under attack, advance, flank, siege and
  gather, and is due again only when a unit left in it stands idle; a
  hold's stop leaves out the units that fire. A retreat keeps every unit
  and fires nothing: `pull back`, `retreat home`, the gather of the
  retreat override and the commander's regroup gather (see the model
  commander below);
- under a commanded attack, advance, flank or siege the units the focus fire
  gives no target keep the commander's order rather than being sent after
  an enemy out of sight, and the units in contact fight though no enemy is
  near main's centroid, but while the siege orders main's screen;
- under the commander's hold and gather, and wherever main gathers or holds
  by its plan (at the staging point or the forward point, the units waiting
  to reinforce the army, main kept home under PRESERVE), the units in
  contact fire at the targets already within their weapon range and are
  not sent forward; main marching by its plan with no long-range unit fights
  with its units in contact on the way, under FINISH only what stands on its
  way, and the march leaves those units out;
- under a major threat, base defense's responders that have arrived at the
  threatened cell or are in contact are handed out over the enemies as an
  engaged group is, each told to attack its target once and again only when
  its target changes; the others are sent to the cell by `attack_move`.

With `tank_charge_artillery` of `[combat]` on (as shipped, not measured), a
tank or other vehicle of ours (not artillery or V2) given an enemy artillery
piece or V2 is ordered to `move` onto its cell while it stands farther than
that weapon's minimum range less 1 cell (3 cells for both), and to attack it
once inside, the move sent again only when the target has moved more than 2
cells: a tank that stops at its own weapon range is a still target for the
shells, and inside the minimum range the enemy cannot fire.

Off, the focus fire picks its candidates within 12 cells (`contact_cells`)
of the group's centroid, no counter factor applies and none of the orders
above changes. In the whole fake match of the standard scenario with the
shipped config the share of main's unit-cycles in contact whose last order
is an attack on an enemy in sight was 0.76 for England and 0.66 for Russia,
against 0.67 and 0.42 with it off. Watch it off with:

    python scripts/watch_game.py --set tuning:combat.contact_focus=false

The counter factor is not measured in a game. In the fight model of
`tests/redalert/test_combat_sim.py`, with the shipped values below, the new
assignment's net value (enemy value destroyed minus our value lost) is
-1,470 in scenario A, 3,208 in B, won, and 2,545 in C, also won, with the
factor and without it alike. Watch it off, with the rest of the
focus fire in contact on, with:

    python scripts/watch_game.py --set tuning:combat.counter_power=0

The four keys, with their shipped values:

    contact_focus = true
    focus_unit_reach_cells = 4
    spill_floor = 0.25
    counter_power = 2.0

Tanks focus tanks, with about enough shooters each, and keep their targets.
In the fights of diag-i29-fights (focus.py, thrash.py) no medium tank fired
at an enemy tank: with `counter_power` 1 a damaged rocket soldier scored
above a full heavy tank. Coverage at 1.5 times the hit points in 3 s took
about 14 medium tanks for a heavy tank, so it capped nothing; a unit kept a
target 0.4 to 0.9 s on median before switching; and rifles were ordered onto
heavy tanks they do 125 a second to. So, of `[combat]`:

- `counter_power` 2.0: a medium tank takes a full heavy tank (2,300 /
  60,000 x (2,300 / 1,380)^2 = 0.107) over a rocket soldier at half health
  (600 / 2,250 x (600 / 1,380)^2 = 0.050);
- `focus_window_seconds` 6 and `overkill_factor` 1.2: a heavy tank is
  covered once its attackers do 72,000 in 6 s, six medium tanks (five do
  69,000, which kills it but leaves it uncovered). Eight medium tanks against
  two full heavy tanks in range split 6 and 2 with every tank nearer one of
  them, 4 and 4 with the two side by side, never 8 and 0;
- `focus_min_hold_seconds` 2: a unit keeps the target it was given for 2 s
  from when it was given it, whatever else is worth more, while the target
  is in sight and within the unit's weapon range; after that it moves on
  only to one worth `retarget_gain` 2.5 times as much;
- one target memory for the match (`targeting.TargetMemory` on the match
  context), shared by main's focus fire, its fire while it holds and base
  defense's focus fire, so that a unit passed from one manager to another
  keeps its target and its hold;
- `focus_min_effectiveness` 0.25: no unit is paired with a ground target
  whose armor it does under a quarter of its mean damage across the armor
  classes to. A rifle (125 to `heavy` of a mean of 600) never fires on a
  tank or a harvester, a rocket soldier (241 to `none` of 1,338) never on
  infantry; with nothing else in reach such a unit is sent after the fight
  as one that can hurt nothing is;
- within a group the vehicles but artillery and V2 are handed out first,
  then the artillery and V2, then the infantry, each class the unit whose
  best pairing is worth the most first.

Artillery and V2 fire from where they stand, on infantry in groups, and step
back from what comes close. In diag-i29-fights (arty.py) the artillery had
no splash preference, got no step back when a heavy tank came inside its
minimum range, its fall-back applying on the siege's paths alone, and base
defense attack-moved it toward the threat. Outside the siege, which keeps
its own rules:

- an artillery piece or a V2 is paired only with targets within its weapon
  range (12, 10 for the V2), at no lowered value beyond it, and is never sent
  after an enemy;
- an enemy infantry unit is worth 1 more to it for each other enemy infantry
  unit within `arty_splash_cells` (1.5) of it: of a lone tank and four
  rifles bunched in range it takes the rifles, and of a lone rifle 7 cells
  off and the four 8 to 9 cells off, the four;
- while a crush run is on (the combat manager's run in force, or the
  commander's run-over detachment) it takes the enemy vehicles first, one
  standing on the cell it stood on in the state before ahead of one on the
  move, then the infantry;
- in every focus fight of main (its fire while it holds or gathers, in
  contact under the commander's actions, under base defense's override) and
  in base defense's focus fire, one with an enemy unit in sight within its
  minimum range and `fallback_margin` of `[siege]` (4 and 2 cells) is given
  a move `fallback_step` (4) cells toward the base center at emergency
  priority, in place of any attack and out of the commander's order, and is
  looked at again in the next cycle (`siege.fall_backs`);
- base defense's `attack_move` to a major threat's cell leaves out artillery
  and V2, which hold their place and fire on what is in range, and the home
  guard's attack on a minor threat leaves out one the enemy stands beyond
  the weapon range of.

An enemy defense whose weapon hurts one of the group's units (an
anti-aircraft gun or SAM site only aircraft) is weighed
`focus_weight_threat_defense` (2.0), above every other class, while one of
those units stands within its weapon range and `defense_threat_margin_cells`
(1), and as a fighting unit (1.0) when none does, so that tanks under a
Tesla coil kill the coil before the power plant beside it.

The infantry of main and the raid group is spread one to a cell (`spread`,
`infantry_spread` on): a MOVE-class move or attack-move to a point gives
each infantry unit a passable cell of its own in rings
`infantry_spread_cells` (1) apart around the point, never under a building,
and the combat manager moves main's idle infantry sharing a cell with another
of ours, and in contact with no enemy in sight, to the nearest free cell at
most once in `infantry_spread_recheck_seconds` (5), at most
`infantry_spread_max_orders` (8) such orders a cycle, while the guard's
infantry, main's while main has artillery or V2, orders of any other class
(attacks on a target, retreats, base defense's), boarding and the
commander's order go out as they were.

None of this is measured in a game. Watch a looser focus, with the counter
factor to the first power, a 3 s window at 1.5, no hold and no
floor, with:

    python scripts/watch_game.py --set tuning:combat.counter_power=1.0 --set tuning:combat.focus_window_seconds=3 --set tuning:combat.overkill_factor=1.5 --set tuning:combat.retarget_gain=1.5 --set tuning:combat.focus_min_hold_seconds=0 --set tuning:combat.focus_min_effectiveness=0.0

The keys, with their shipped values:

    overkill_factor = 1.2
    focus_window_seconds = 6
    retarget_gain = 2.5
    focus_min_hold_seconds = 2
    counter_power = 2.0
    focus_min_effectiveness = 0.25
    arty_splash_cells = 1.5

Waypoint routing is not measured, and ships off. A raid, a scout, the
commander's flank and the second yard's MCV each go round a remembered enemy
defense by one side waypoint at most, which does not get round a line of
defenses. With `waypoints` of `[routing]` on, the agent keeps a graph of
waypoints for the match (`waypoints`): its nodes are the center of every ore
field of the map picture, the base's center, the four cells 14 cells
(`scout_view_cells` of `[scouting]`) north, east, south and west of the
enemy's start, the corners and the middles of the map's sides 4 cells inside
it, and the two ends of each walked route (`walked_routes` of
`[placement]`). Two nodes within 30 cells (`edge_max_cells`) are joined when
the straight line of cells between them crosses no blocked ground (every
cell the map picture lets nothing be built on, its ore aside) and no cell
within 2 cells (`edge_clear_margin_cells`) of the ground a remembered enemy
defense covers; a walked route joins its two ends by the length walked. The
graph is built once the base, the map and the enemy's start are known, and
again whenever the remembered defenses or the walked routes change. A way
from a cell to a destination joins each of the two to the nodes within 30
cells it has such a clear line to (or the nearest one, when none is that
near) and is the shortest chain of nodes between them (A*), the destination
last; there is none when no chain joins them, so no cell of a way lies on or
near covered ground. Building the graph takes about 1 ms and a way well
under 1 ms; the budgets are 20 ms and 2 ms. Its users, with the switch on:

- a raid whose straight way to its approach cell is covered goes by the way
  through the graph, and by a side waypoint only when there is none; the
  raid keeps the cells still to reach and goes on to the next within 3
  cells, as it does with a side waypoint;
- a scout whose straight way to its destination crosses danger goes by the
  way through the graph when each of its legs keeps the scout's clearance off
  every danger and away from the map's middle, else as before; its course
  keeps the cells still to reach;
- the commander's `flank` goes by the way through the graph to the objective
  ("flank via (44,64) to war factory (72,52), 49 cells"), else by the side
  waypoint; the combat manager keeps the cells of the way as the flank was
  taken, sends main to each in turn, on to the next once its centroid is
  within 3 cells of one or every unit stands idle near it, and marches on the
  objective after the last;
- the second yard's MCV, with `mcv_safe_site` of `[expansion]` on, goes by
  the way through the graph, its escort with it, when its straight line to
  its site crosses unsafe ground and no cell of the way is unsafe; else
  straight, as before.

Without a way through the graph each of them routes as it does with the
switch off. Each way one of them takes is a `route` event: who took it
(`raid`, `scout`, `flank` or `mcv`), from where to where, how many nodes it
passes and how long it is, for example:

    {"tick": 1000, "user": "raid", "start": [32, 48], "goal": [52, 48], "node_count": 1, "length_cells": 34.41}

Watch it, and the match record's `route` events, with:

    python scripts/watch_game.py --set tuning:routing.waypoints=true

The `[redalert]` settings of `config/agent.toml`:

| Setting | As shipped | Meaning |
|---|---|---|
| `model_plan_choice` | `true` | When `true`, the `red_alert_plan` chain of decision sources is asked to make the R7 choice whenever two plans or more are open. Its answer is used only if it names a plan still open, and it never overrides R1 to R6. |
| `model_commands` | `false` | When `true`, command shorthand a model wrote is turned into orders (see [Compact shorthand](#compact-shorthand)). |
| `model_briefing` | `true` | With `model_plan_choice` also `true`, the plan choice's context is the plan choice briefing below, its question `src/ucla_agent/games/redalert/prompts/plan_choice_briefing.txt`, and the answer a reason of at most 30 words written before the choice, in at most 96 tokens. When `false`, the request is the compact summary under `src/ucla_agent/games/redalert/prompts/plan_choice.txt`, answered with the choice. It changes nothing while `model_plan_choice` is `false`. |
| `plan_prompt` | `"briefing"` | How the plan question is put to a model: `"briefing"` as `model_briefing` says, or `"compact"`, the compact plan prompt below, whatever `model_briefing` says. It changes nothing while `model_plan_choice` is `false`. |
| `model_scout_choice` | `false` | With `scout_slots` of `[scouting]` also `true`, the `red_alert_scout` chain is asked where each designated scout goes next, among the script's best 6 places (`scout_choice_options`) it has a safe way to, each with how far it is, how long it has gone unseen and what was last seen there, the question carrying the framing paragraph and the context the model's recent picks for the scout with what became of each (see the models' recent picks below); answered with a reason of at most 20 words first, in at most 64 tokens, within 1,500 ms (`scout_choice_deadline_ms`), and asked again when the pick is 125 ticks old (`scout_choice_every_ticks`). The script's choice stands until a fresh answer arrives. It changes nothing while `scout_slots` is `false`. |
| `model_commander` | `true` | When `true`, the `red_alert_commander` chain is asked every batch to pick one of the script's checked actions for the first scout, then, once main's own units reach `handover_combat_units` of `[commander]`, for main; the action is carried out by the script and its order goes in an order slot kept for it. See the model commander below. |
| `seat_plans` in `[redalert.sparring]` | `{}` | Pins the R7 plan of a seat, for sparring between two copies of the agent. It holds in test matches only; a tournament game ignores it. The submitted commit has it empty. |

`model_scout_choice` is not measured and ships off. To watch the 4B model
(`small`) pick the scouts' destinations, run
`python scripts/watch_game.py --model small --set agent:redalert.model_scout_choice=true --set tuning:scouting.scout_slots=true`:
`--model` points both the plan chain and the scout chain at the role. Each
question and answer is a `decision` event with its full input and output,
shown on the feed page, and each destination it picks a `scout_slot`
`destination` event with the source `model`.

`model_plan_hold` of `[plan_rules]` in `config/redalert/tuning.toml` keeps a
plan the model chose long enough to be carried through. It is not measured
and ships on, in the watched game's set, where the model chose the plan; it
changes nothing while `model_plan_choice` is off; it ships on. With it on, a
plan that an answer of the `red_alert_plan`
chain changed to is held for `model_plan_hold_ticks` (750 ticks, 30
seconds): no later answer replaces it, the model is not asked meanwhile, and
the script keeps it even when it is no longer open. The hold ends early when
R1 to R4 apply, when R5 or R6 apply once the plan hold of 30 seconds
(`plan_hold_ticks`) is over, under a major threat on the base, and for a RAID
when it closes with "no raid target known" or "raid did not set out"; a RAID
short of units on hand is kept. Each early end is a `plan_rule_blocked` event
with the reason `model_hold_ended` and its cause: the rule that took over,
`major_threat`, or RAID's closed reason. While base defense reports a major
threat, the model is not asked and no answer is taken; an answer that
arrives then is a `plan_rule_blocked` event with the reason
`model_answer_discarded`, the cause `major_threat` and the plan it named.
With the switch off, a later answer may replace the plan after 10 seconds
(`answer_hold_ticks`). `scripts/watch_game.py` turns the model plan choice
on with the role `--model` names, the 4B model `small` unless told
otherwise, so watch the hold with it, and the plan choice without the hold
with:

    python scripts/watch_game.py --model small --set tuning:plan_rules.model_plan_hold=false

**The plan choice briefing.** A small model knows Red Alert by name but not
its units, so with `model_briefing` on it is told the match in plain words,
in six sections, always in this order:

1. Situation: the time played and left, the two sides, the plan in force and
   the rule that set it, the threat to our base (none, minor or major) and
   what it is against and where, the trade balance, the money, the income per
   minute and the power; then `recent picks:`, the model's last plan picks
   (see the models' recent picks below).
2. Our forces: our army by type, each line `count × name (code): value,
   health`; the groups (guard, main, raid, scout) with their units and
   strength; our other units and our buildings by type.
3. Enemy as known: the enemy army seen by type, with how many seconds ago
   each type was last seen; its other units, buildings and defenses by type,
   each defense near their base or forward and how far from ours; whether
   their base has been seen; and how long ago an enemy building and an enemy
   unit that fights were last in sight.
4. Comparison: main's strength against the enemy army seen in the last 30
   seconds, as a ratio; the plan rules' own weighing of the two armies with
   PRESS's and the script's thresholds; and how the rules count enemies nobody
   has seen lately.
5. Glossary: for each type sections 2 and 3 name, its line from
   `config/redalert/glossary.toml`: name, code, side and kind, the item
   table's cost, its role in one sentence, what it beats and what beats it.
6. Options: each open plan with what it would do now, in numbers (main's
   units and strength, the strength PRESS waits for and the enemy defenses
   known; the raid group's make-up and the targets no defense covers), and
   each closed plan with what keeps it shut, read from the plan rules: the
   early-attack guard until 9:00 unless the enemy commits, PRESS's time, army
   and ratio thresholds, and RAID's switch, building, quota and targets.

Every figure is the agent's own, as the cycle sees it. The briefing is held to
9,000 characters: one that is longer first drops its recent picks, the
oldest first, then keeps the glossary lines of only the 20 most valuable
types named, then sums up the enemy types last seen more than
two minutes ago in one line, and last is cut at a whole line before the
options, with a note. It is written in well under 5 ms. The glossary has an
entry for every type of `config/redalert/items.toml` and is read strictly,
once, when the agent is made; when it cannot be read, a `handled` error event
says so and the model reads the compact summary instead.

**The compact plan prompt.** The briefing runs to about 9,000 characters, and
most of it teaches the game rather than the choice. With `plan_prompt =
"compact"` the plan question is instead a prompt of at most 1,500 characters,
written by `src/ucla_agent/games/redalert/compact_prompt.py`: a fixed system
text, then the state as `key=value` lines, always the same 19 keys in the
same order, then `OPEN:` and one line for each open plan from a fixed table
of HOLD, RAID and PRESS (FINISH and PRESERVE come only from the script's
rules and are never offered). Every value is the plan rules' own figure of
the cycle: `army_value`, `enemy_estimate` and `army_ratio` are the two armies
and the one ratio the decision table weighs, `enemy_estimate_kind` says
whether the enemy's value was seen (`seen_peak`) or `assumed` while its army
is unknown, the economy counts of the enemy come with the age of their
oldest sighting, the raid keys say whether the raid group holds its full
quota, its strength and the targets a raid may be sent at, and `press_block`
gives the codes of what keeps PRESS shut. What is not known yet is `unknown`,
`never` or `none`, never 0. After the keys come the model's recent plan picks
(`recent picks:`, see below). There is no glossary, no unit list and no
recommendation of the script's: the recent picks say only what the model
picked and what came of it, and the framing paragraph the other questions
carry, whose doctrine is a recommendation, is left out here. The answer is JSON with the choice first,
held by the local source's schema to the open plans, and a reason of at most
12 words, in at most 48 tokens; a closed, invalid or late answer leaves the
script's choice, as with the briefing. The request carries its own system
text and token cap (`system_text` and `answer_tokens` of `ChoiceRequest`), so
the local source sends that system text and lays the user message out
plainly, one line an option. A prompt over 1,500 characters has its recent
picks cut first, the oldest first, then its plans' lines cut to their first
10 words, never a key or a plan dropped; as every value is a number, a word
or a few codes, a prompt comes to about 800 characters, and about 200 more
with three recent picks. An example, with HOLD and RAID open, before the
model's first pick:

    SYSTEM
    Choose one OPEN plan. Use only supplied facts. Enemy counts may be stale; unknown is not zero. Prefer viable pressure when home is safe; HOLD must have a concrete benefit. Return only {"choice":"ID","reason":"up to 12 words"}.

    USER
    left_s=581
    base_threat=none
    army_value=15200
    enemy_estimate=13000
    army_ratio=1.17
    enemy_estimate_kind=seen_peak
    last_enemy_army_seen_s=0
    cash=3502
    income_per_min=2727
    our_refineries=4
    our_harvesters=8
    enemy_refineries=3
    enemy_harvesters=6
    enemy_economy_age_s=100
    raid_ready=no
    raid_value=450
    raid_targets=2
    raid_target_age_s=100
    press_block=army_not_ahead
    recent picks: none
    OPEN:
    HOLD: Keep main home; grow forces but concede map pressure.
    RAID: Send the raid group at exposed harvesters; main holds.

    ANSWER
    {"choice": "RAID", "reason": "home safe, two exposed refineries"}

The compact prompt is not measured and ships off (`plan_prompt =
"briefing"`), as does the model plan choice it serves. The winning watched
game of the shipped switch set above played with both on, and its one model
plan decision was HOLD. Each question and answer is a `decision` event with
the full system text, user message and raw answer, shown on the feed page.
Watch it with the 4B model:

    python scripts/watch_game.py --model small --set agent:redalert.plan_prompt="compact"

**The models' recent picks and framing**
(`src/ucla_agent/games/redalert/model_history.py`). A small model asked the
same question every few seconds remembers none of its answers, and the
opponent is another agent, not the game's own bot. So each question says
what the model picked lately and what came of it, and each system text says
what the match is. The match context holds the model history of the match (a
new match starts with none, so no pick of a match before is ever shown): the
model's last `history_picks` picks of the plan question and of the scout
question (3, `history_picks` of `[models]` in `config/agent.toml`, read with
the file and refused unless a whole number of 0 or more), and the plan rules'
army ratio of the cycle (the compact prompt's `army_ratio`), which the plan
rules publish there in every cycle. A plan pick is an answer of the
`red_alert_plan` chain the plan rules took (not a shorthand line); it keeps
its tick, the plan, the value killed and lost by then and the army ratio
then. The plan question writes them, the newest first, after the keys of the
compact prompt, at the end of the briefing's situation, or after the compact
summary:

    recent picks:
    - 20 s ago: PRESS -> since: killed 500, lost 200, army ratio 1.4 -> 1.1
    - 40 s ago: RAID -> since: killed 1,300, lost 400, army ratio 1.6 -> 1.1

and `recent picks: none` before the first. A scout pick is a new destination
of a scout slot's answer (see `model_scout_choice` above); it keeps its tick,
the scout, the place (its area and cell) and the enemies remembered then. It
is settled when the scout's course drops the place, by why: `reached`,
`retreated` (the way became unsafe or the scout fled), `stuck`, or
`redirected`; when a later answer sends the scout elsewhere (`reached` when
it stands there, else `redirected`); and `lost` once its scout is gone.
Until then it is `en route`. Its line adds the enemy buildings and units
first remembered since: `- 30 s ago: enemy main edge (60,60) -> reached; saw
2 new buildings, 1 new unit`. The scout question lists its own scout's picks
and those whose scout was lost. The model commander keeps its picks itself,
one role at a time (below); its `last_picks:` line shows at most 3 of them
(`history_picks`), and main's gives the army ratio then and now after the
value killed and lost since each (`ratio 1.4 -> 1.1`).

The framing paragraph, at most 600 characters (447 as shipped), says that
the opponent is another AI agent in a tournament, that a 20-minute match not
won outright goes to the tiebreak (value traded), the doctrine of the
script's own rules (economy and map control early; never attack into
defenses we cannot beat; retreat and regroup when outnumbered; tanks crush
infantry and kill tanks; artillery fires from behind a screen; punish exposed
expansions and harvesters; keep a lead late), and to learn from the recent
picks: repeat what worked, change what lost value. It goes whole into the
question of `plan_choice_briefing.txt`, main's system text and the scout
slots' question. The compact plan prompt, which holds no recommendation, and
the compact summary's `plan_choice.txt`, held to 1,200 characters, get the
picks without it; the commander's scout question, held to 2,500 characters,
gets the tournament and the learning without the army's doctrine. Every question
still fits its cap and cuts its picks first, the oldest first; a commander
question that fits at no level with the framing is written with a short
system text without it, at the fullest level that lists one pick and fits.

What the framing and the picks cost, in characters of a whole question
(system text and user message; the plan briefing's question and context),
measured on the test scenes before and after:

| Question | Before | After |
|---|---|---|
| Plan briefing, its question (`plan_choice_briefing.txt`) | 846 | 1,350 |
| Plan briefing, its context, a mixed army at 10:12 | 4,613 | 4,632 with no pick, 4,826 with three |
| Compact plan prompt, the midgame Allies state | 668 | 687 with no pick, 881 with three |
| Commander's scout question: early; a pillbox known, four picks; a busy front, four picks | 772; 937 (4 picks shown); 933 (4 shown) | 963; 900 (1 shown); 949 (1 shown) |
| Commander's main question: at home; an enemy group, four picks; a busy front, four picks | 757; 1,121 (4 shown); 1,361 (4 shown) | 1,144; 1,446 (3 shown); 1,591 (1 shown) |
| Scout slot's question, one scout, no pick yet | 790 | 1,257 |

The table measures the questions before the full-state sections (below),
when the commander's caps were 1,000 and 1,600 characters and its questions
came close to them in a busy state. In the watched game
`20261013T173137Z` (England, won by elimination at 15:24, value
traded +57,950) the 4B model's 1,983 choices, most of them commander
questions, took a median of 144 ms and at most 507 ms.

The model commander (`model_commander` of `[redalert]`) is measured by that
watched game alone, no benchmark, and ships on, its chain asking JevK5-9B
(`systemone:jev9b`). With it on, the `red_alert_commander` chain is asked every batch,
over a chooser of its own apart from the plan's and the scouts', to pick one
action the script has checked for one unit group: the first scout (the
scout of the lowest scout slot holding one, or with `scout_slots` off the
scout of the mission in the field) until main's own units reach 6
(`handover_combat_units` of `[commander]` in `config/redalert/tuning.toml`;
the home guard and the MCV's escort do not count), then main for the rest of
the match, the handover a `commander` event of kind `handover`. One request
is in flight at a time, and the next goes out in the cycle after an answer,
or the end of the 800 ms deadline, arrives. The answer is a letter alone,
`{"choice":"ID"}`, with no reason (`with_reason` false), in at most 16
tokens. It is used in the cycle it arrives in, when it is at most 25 ticks
old (`answer_max_age_ticks`) and its action is still among those that
cycle's question offers; otherwise it is a `commander` event of kind
`dropped` with the reason, `stale` or `not_offered`. An answer used makes its
action the standing action, a `commander` event of kind `action` with the
role, the letter, the action's line and the tick.

A pick sticks. An answer naming the standing action the model picked changes
nothing, and one naming another action replaces a standing action picked
less than 8 s before (`commit_seconds`) only when its units have arrived
(within 3 cells of its cell; a hold at once; an attack whose group is gone is
no longer offered), the threat to main changed between none and any (for the
scout, when it must flee a danger), the action is no longer offered, or
main's forward gate (below) opened or closed since. Otherwise the answer is
not carried out: its `decision` event is followed by a `fallback` event of
reason `committed`, and when the match ends a `commander` event of kind
`summary` gives the match's `committed_answers`.

Each kind of action has a letter of its own, the same in every question, and
the actions are listed by their letters. The scout's: A hold (3 seconds), B
back off (`scout_flee_cells` straight away from the nearest danger, while a
dangerous cell lies within 10 cells), C the enemy main's edge, D an enemy
expansion field, E our expansion's approach, F the attack lane; of each
mission kind only the best scored is offered. Main's: A pull back, B retreat
home, C hold, D gather, E siege, F flank, G raid trucks, H attack, I advance,
J run over, K kill (a defense), L hit (a group), M attack economy, X attack
expansion. At most 6 are offered (`options_max`), kept in this order: the
scout's hold, back off, then the missions by score; main's defensive actions
(at most 4), then advance, the task options (at most 2, in the order run
over, hit, kill), attack, raid trucks, siege, flank and gather. Gather is
always kept: when the cut would leave it out it takes the last place, so that
every question for main offers at least one action that calls it back. The
expansion target (X, below) and then the economy target (M, below) come after
that cut: each in a place left, else in the place of the last active action
kept, never a defensive action's, gather's or the other's. The options are
always listed in the order of their letters, in the text and in a systemone
server's criteria alike, never in the order they were found. Hold and the
other defensive actions are still offered only under a threat, and retreat
home only away from home (below). Both questions carry a `current:` line
before `last_picks:`: the standing action's letter and words, its cell (an
attack's group centroid, the units' own cell for a hold), the cells left to
it and how long ago it was taken, or `current: none`; the standing action's
line under `ACTIONS:` ends with ` (current)`.

The scout's question is at most 2,500 characters, its system text included
(`command_scout.MAX_PROMPT_CHARS`): the full-state sections but our groups
(below, with type codes), the bases, the scout, the enemy units in sight
within 20 cells by type, the nearest remembered defenses, what is known of
the enemy main, its army and the fields it may expand to, the current line,
the last 3 picks (`history_picks` of the 4 `history_size` keeps), the clock,
and the actions, each with the length of its way and whether the way is
clear, unknown or passes danger. Main's question is at most 6,000 characters
(`command_army.MAX_PROMPT_CHARS`, about 1,600 tokens, well within the small
role's context of 8,192): the military state (below: our bases, the enemy
bases, the enemy forces and our forces by status), the most the enemy army
was seen worth (or the assumed share of ours) and how much of it no enemy
force accounts for, `fight_here`, the targets block, the threat line, the
objective and whether the straight way there is in a ground defense's range,
the current line, the last 3 picks with the value killed and lost since each
and the army ratio then and now, the score line (`score: lead`, `t=`,
`left=`, `plan=`), and the actions, each ending with its consequence. Its
system text asks the model to keep the army busy when we are even or
winning, then carries the whole framing paragraph (above), doctrine
included, in every tested scene; the scout's carries the tournament and the learning from
recent picks.

**The full state.** The 4B commander read about 400 tokens (1,599 characters
at 08:45 of game `20261013T173137Z`) where the full-state fixture of the same
moment is 12,626 characters. The scout's question starts its user message
with the whole picture the agent has (`command_state`), one compact line per
item, every position a cell, a section with no item reading `none`; main's
question reads the military state instead (below):

- `ore fields:` every field the terrain knows, the nearest our base first:
  its center, its cells at the start of the match, and `served by us`,
  `served by enemy`, `served by us, enemy` or `served by none` (a side's
  refinery counts against a field as `refinery_fields.field_counted_from`
  counts one of ours), or `dry` for a field the move to fresh ore marked dry.
  It changes only when a refinery appears or dies;
- `enemy buildings:` the remembered enemy buildings but the defenses, in
  groups within 10 cells of a group's first (by id), the nearest our base
  first: the types counted, the center, and `in sight` or the age since one
  was last seen in 30 s steps (`seen 60s ago`);
- `enemy defenses:` every remembered enemy defense, ground or air, the
  nearest our base first: type, cell and `range 5`, or `(air only)`;
- `enemy groups:` the remembered enemy combat units, stale ones too, in
  clusters within 8 cells of a cluster's first, the nearest main's body (or
  the scout) first: the types worth most first, the center, the value (cost
  times health), `in sight` or the age in 30 s steps, and `moving` when the
  centroid of its units moved more than 1 cell from where the same units
  stood in the commander's closing watch at least a second before one of them
  was last seen, else `still`.

The order is fixed, the content that changes least first: the system text,
the sections (the scout's ore fields, enemy buildings, enemy defenses and
enemy groups; main's military state), the questions' own lines, the recent
picks, the clock line (main's score line), then `ACTIONS:`. A model server
that reuses the unchanged beginning of a prompt reuses the system text and
the stable sections from one question to the next: in Jev's measurements on the same snapshot
(`jev-speed-and-cache-findings-20261013.txt`), moving the unchanged evidence
before the changing clock fields took the 9B's decision from 354 ms to 105
ms. Main's question is written only in a cycle it is put to the model
(`Commander.is_due`), so the fuller question costs the agent's cycle little:
the commander's whole fake match measures a compute p95 of 2.5 ms against 1.9
ms before, under the 50 ms bound. The commander deadline is unchanged.

Main's question closes the gaps that a full state snapshot of the watched
game `20261013T173137Z` at 08:45, rebuilt from its match record, found
between the question the model was asked and the state:

- **Army by value.** The `main:` line of our forces lists main's types by
  their total value (cost times health), the highest first, so that the
  detail levels cut the cheapest types first: `2 medium tank, 14 rifle infantry, 4 rocket
  soldier, 1 artillery, 1 light tank`, where the question had shown `14
  rifle infantry, 4 rocket soldier` and cut the tanks.
- **Every attacked type.** The attack (H) and the hit (L) name every enemy
  type of their group, the most worth first (`H: attack 1 heavy tank, 1 flak
  truck, 1 rocket soldier at (52,68)`, where it had read `attack 3 enemy
  units`). At the last detail level they name the first 3 and how many more
  (`1 heavy tank, 1 light tank, 1 flak truck, +2 more`).
- **Reinforcements.** The `staging:` line of our forces counts the units of
  main's group within 6 cells of the staging point that the question is not
  about (the units joining main wait there while the forward gate is open)
  and that are not out on main's orders, in repair or withdrawn, with their
  strength: `staging: 2 units (2 medium tank) strength 1700`, where the
  question had read `none` while $1,700 of medium tanks waited there. The
  home guard and the scouts standing there are not counted. Main's own units
  away from its body, and the units of its group on their way to it, are
  `reinforcing:` with the seconds the slowest needs (`eta 10s`).
- **Air-only defenses.** An AA gun or a SAM site, which fires at aircraft
  alone and counts in no ground figure, is noted by name as `air-only` on its
  base's line, at ground strength 0, and reads `(air only)` in place of its
  range on the siege line (`E: siege sam site (51,75), 9 cells (air only)`).
- **Economy target.** While the forward gate is open, `M: attack economy`
  offers the enemy refinery, or group of harvesters not stale (joined by
  single link at 4 cells), worth most per cell of the way from main's body
  (value over distance) whose actors no remembered ground defense covers (its
  weapon range and 2 cells), on which no detachment is, and against which
  main's body weighs at least 0.5: the body's units that can hurt it against
  the enemy units within 8 cells of it. The line gives its name, cell,
  distance and value, and `fight 2.1x`, or `unguarded` with no enemy unit by
  it. It is carried out as the attack is, an attack-move on its cell, and
  like every action on an enemy target it is held back while main is behind
  (below).

Over its cap the scout's question cuts, in order: its picks (all of them,
then 2, then 1), the enemy buildings, the ore fields, the enemy groups, then
the enemy defenses section and the existing detail levels, which cut groups,
defenses and the cheapest types, then the action details. Main's cuts the
far band of its military state first, then the mid band, then its picks,
then the same detail levels, and at the last level the attack and hit lines
fall back to `+n more`. A question that fits at no level with the framing is
written with the short system text at the fullest level that lists one pick
and fits; with the caps of 6,000 and 2,500 no tested scene needs it.

**The military state.** Main's question reads the match as a commander of
the army does, and nothing of the economy: no money, income, harvester
count, ore field or building inventory of ours appears in it, and the
`ore fields:`, `enemy buildings:`, `enemy defenses:`, `our groups:` and
`enemy groups:` sections, the `army:`, `reinforcements:`, `enemy_near` and
`enemy_defense_near` lines and the `base_threat=` field are gone from it (the
scout's question keeps its sections). In their place `command_state` builds
one structured military state (`military_state`), written as text for the
4B model (`MilitaryState.lines`) and sent as a JSON object to a systemone
server (`MilitaryState.as_json`, carried by `ChoiceRequest.state_object`;
the systemone source sends it as its `state` when a request carries one, else
the text). The 4B probe of `tmp/coord-probe` scored 20/40 with coordinates
alone, 24/40 with relations alone and 27/40 with both, and the model favors
what is listed first, so every relation is worked out in code and stated per
item, the items that matter most first.

- **Clusters.** The enemy's buildings make up the enemy bases (`E1`...), ours
  our bases (`B1`...), and the remembered enemy combat units the enemy forces
  (`F1`...), each joined by single link at `cluster_cells` (8) of
  `[commander]`, so that a sprawled base or a stretched army is one cluster
  and two far apart are two. A cluster of buildings without a construction
  yard whose center lies within `base_merge_cells` (16) of `[commander]` of
  the center of a cluster with a yard of the same side is part of the
  nearest such base, which keeps its yard cluster's label: the snapshot's
  refinery 8.5 cells from our yard, an outpost of its own at 8 cells, is part
  of B1. A cluster with its own yard (an expansion) never merges, and one
  farther off stays an outpost. A side's base nearest its start is its
  `main`; another with a construction yard is an `expansion` (an MCV deployed
  it); the rest are `outpost`s. A cluster keeps its number from one question
  to the next while its center moves less than 8 cells (a merged base by its
  yard cluster's own center); a new one takes the lowest number free.
- **Where.** Every item gives its distance in cells and its direction of
  eight (`N`, `NE`..., y growing southward) from main's body center, its cell
  in brackets after: `20 cells S (40,87)`. Main's own line gives its place
  from our base's center. By that distance an item is near (at most 15
  cells: in full, with its types), mid (at most 35: one line without the
  types) or far (its name, distance, direction and strength). Within each
  band the beatable items come first, then the nearest.
- **Bases.** A building count and the summed cost of the buildings; the
  ground strength of its defenses, the fight model's (twice a defense's
  cost, `defense_weight` of `[combat]`), an AA gun or a SAM site noted by
  name as `air-only` at strength 0; its guard (the side's combat units
  within 10 cells of any of its buildings; ours but main's); its exposed
  harvesters (those within 10 cells that no ground defense of the side
  covers); for ours the enemy forces closing on it (the closing watch's
  `closing_on`, as the `enemy army closing` threat finds one).
- **Enemy forces.** Ground strength (cost times health, 0 for a unit that
  fires at aircraft alone) and unit count, crushable infantry, anti-tank
  infantry (rocket soldiers and shock troopers), armor (the units the
  Vehicle queue makes), `toward us`, `away` or `still` (by the closing
  watch's sample, toward the nearer of our base and main), `in sight` or how
  long since one was seen, and the base of ours it closes on.
- **Relations, as words.** Each enemy base and force says `within reach of
  <defense>: yes` for the nearest remembered ground defense whose weapon range
  and 2 cells reach its center, or `within defense reach: no` (and `exposed`
  for a base), and `in range: yes` when one of main's body stands within its
  own weapon range of one of its actors, else `no`. The 9B's spatial
  evaluation got exact in-range checks right 6 times in 12, so the JSON
  carries them as booleans (`within_defense_reach`, `in_range`).
- **Fight facts.** Main's body weighed against the item (`fight.weigh`: a
  force with the ground defenses that reach it, a base with its guard and
  its defenses): the plain ratio, then, with crushable infantry, the crush
  facts (the infantry of the force's largest cluster a crush run would
  qualify on by size, the tanks `crush.planned` gives for it, main's tanks at
  `crush_min_hp` or more), then the crush-adjusted ratio, in which that
  infantry counts at `crush_discount` (0.333) of `[commander]` of its
  strength while main has the tanks the run takes, else in full, and
  `beatable: yes` at 1.2 or more (or with no enemy there). With 6 medium
  tanks against 12 rifles (planned 3) the rifles count at 1/3; with 2 tanks
  they count in full.
- **Our forces by status.** `main` (the question's body), `reinforcing`
  (main's stragglers and the units of the group main on their way, with
  `eta`), `staging` (the reinforcements waiting at the staging point),
  `retreating` (units withdrawn on their own), `repairing` (vehicles in
  repair mode, with `back in`, an estimate at 2% of health a second, the rate
  measured for a vehicle standing still beside a mechanic, up to
  `repair_done_hp`, while a mechanic of ours, mobile or at the base, or our
  service depot stands within 15 cells (`repair_mechanic_reach_cells` of
  `[combat]`) of the most damaged of them; else `waiting at staging`, and
  `waiting_at_staging: true` with no `eta_s` in the JSON, since nothing
  repairs it there), `detached` (each commander's detachment with its task
  and target, the crush run's tanks, the strike force), `raid`, `guard`
  (with the threat it answers) and `scouts` (with what they are doing): the
  types by count, strength, average health and place. The combat manager
  publishes the repair set, the withdrawn units and the detachments on the
  match context (`repairing`, `withdrawn`, `detachments`).
- **Score.** `score: lead +1450 (killed 9050 lost 7600) t=08:45 left=11:14
  plan=HOLD`, the value traded the tiebreak goes by and the clock, is the
  last line before `ACTIONS:`. `left` is the state's
  `time.remaining_seconds`, which falls during a live match: in the record of
  game `20261013T220250Z` it went from 1,200 s at the start to 225 s at
  16:14, and every question's `t` and `left` summed to 20:00 within the
  second dropped. The test scenes take their clock from a fake 20-minute
  match, so the snapshot at 08:45 reads `left=11:14`.

After the four sections come `enemy_total_est:` (with how much of it no
force listed accounts for, `unplaced`), `fight_here:`, the targets block,
`threat:`, `objective:`, `current:` and `last_picks:`. Every option line ends
with ` -> ` and a consequence worked out in code: for an action on an enemy
target `under 2 ground defenses` or `exposed target`, `leaves B2 uncovered`
when it takes main farther from a base of ours an enemy force closes on, and
`reinforcements 10s behind` while units are on their way to main; for gather,
retreat home and pull back the base of ours they cover (`covers B1`) and the
reinforcements waiting at staging; for hold whether main stands under ground
defenses. **X attack expansion** attacks the enemy expansion main can beat,
the highest crush-adjusted ratio first, then the nearest, at its
construction yard: while the gate is open whatever its distance, while it is
closed only within `near_attack_cells`, held back like every action on an
enemy target while main is behind. It is placed after the cut just above the
economy target M: in a place left, else in that of the last active action
kept, so that it is ranked above a generic target; M on a target in the same
expansion is then not offered. The options are listed in the order of their
letters, the same for the same situation in the text and in the systemone
criteria; the 9B's evaluation changed its pick in 20% of cases when the
order changed.

Over its cap main's question cuts the far band first, then the mid band,
then its picks (all, then 2, then 1), then the lesser levels (the cheapest
types, the action details, `+n more`). The snapshot scene's question is
3,502 characters (631 of system text, 2,871 of user message) where the
iteration-36 question of the same scene was 2,814 characters; its JSON object
is 3,251 characters. The state is built only in a cycle the question is put
(`Commander.is_due`); the expansion option is the one part worked out every
cycle (about 0.5 ms in the busy test scene).

The same scene with an enemy expansion about (40,87) (a construction yard,
a refinery and an advanced power plant, its center 17 cells from the SAM
site, so the two stay separate bases) and a medium tank of main in repair by
the mechanic a cell from it, the acceptance scene, is 3,856 characters and
reads besides:

    E4 expansion, 20 cells S (40,87): 3 buildings value 3900; ground defense 0; air-only none; guard 0; exposed harvesters 0; within defense reach: no, exposed; in range: no; fight no enemy; crush-adjusted no enemy; beatable: yes
    repairing: 1 unit (1 medium tank) strength 298, hp 35%, 12 cells NW (36,62), back in 28s
    X: attack enemy expansion E4 at (40,87), 20 cells, 3 buildings value 3900, unguarded -> exposed target

with no M (the economy target is the expansion's refinery, which X names).
Without the mechanic, or with it 16 cells from the tank and no service depot
within 15, the repairing line ends `waiting at staging`. The busy scene of
the tests (eight enemy groups, six defenses, four picks, the base attacked)
is 4,878 characters with every band kept.

The snapshot scene's user message:

    our bases:
    B1 main, 32 cells NW (22,47): 4 buildings value 4200; ground defense 0; air-only none; guard 2700; exposed harvesters 4; closing: none
    B2 outpost, 32 cells W (16,79): 1 building value 1400; ground defense 0; air-only none; guard 0; exposed harvesters 2; closing: none
    enemy bases:
    E1 outpost, 9 cells SE (51,75): 1 building value 700 (1 sam site); ground defense 0; air-only 1 sam site; guard 1921; exposed harvesters 0; within defense reach: no, exposed; in range: no; fight 3.0x; crush-adjusted 3.0x; beatable: yes
    E3 outpost, 24 cells E (70,64): 1 building value 1400; ground defense 0; air-only none; guard 0; exposed harvesters 0; within defense reach: no, exposed; in range: no; fight no enemy; crush-adjusted no enemy; beatable: yes
    E2 main, 28 cells NE (68,51): 3 buildings value 3000; ground defense 3600; air-only none; guard 0; exposed harvesters 0; within reach of flame tower: yes; in range: no; fight 1.6x; crush-adjusted 1.6x; beatable: yes
    enemy forces:
    F1, 6 cells E (52,68): 3 units strength 1921 (1 heavy tank, 1 flak truck, 1 rocket soldier); 0 crushable, 1 anti-tank, 2 armor; still, in sight; within defense reach: no; in range: yes; fight 3.0x; crush-adjusted 3.0x; beatable: yes
    our forces:
    main: 22 units (2 medium tank, 14 rifle infantry, 4 rocket soldier, 1 artillery, 1 light tank) strength 5850, hp 100%, 31 cells SE (46,68) from base
    staging: 2 units (2 medium tank) strength 1700, hp 100%, 23 cells NW (33,49)
    guard: 5 units (5 rifle infantry) strength 500, hp 100%, 29 cells NW (29,45), answers none
    scouts: 1 unit (1 ranger) strength 500, hp 100%, 26 cells NW (34,45), doing idle
    scouts: 1 unit (1 ranger) strength 500, hp 100%, 56 cells NE (72,18), doing move to (32,48)
    enemy_total_est: 6650 (seen peak 103s ago), unplaced 4729
    fight_here: we are 3.0x
    targets:
    T1: 1 flak truck, 1 heavy tank, 1 rocket soldier at (52,68), 6 cells, value 1,921, win 90%
    threat: none
    objective: sam site (51,75), 9 cells; direct path clear
    current: I advance to sam site to (51,75), 9 cells left, taken 32s ago
    last_picks: I: advance to sam site, 32s ago -> killed 150 lost 0 ratio 1.0 -> 1.2; H: attack (40,50), 45s ago -> killed 1050 lost 400 ratio 0.9 -> 1.2; D: gather, 65s ago -> killed 3050 lost 600 ratio 0.6 -> 1.2
    score: lead +1450 (killed 9050 lost 7600) t=08:45 left=11:14 plan=HOLD
    ACTIONS:
    D: gather at staging (32,48), 24 cells -> covers B1, joins 2 at staging
    E: siege sam site (51,75), 9 cells (air only) -> exposed target
    H: attack 1 heavy tank, 1 flak truck, 1 rocket soldier at (52,68), 6 cells, fight 3.0x -> exposed target
    I: advance to sam site (51,75), 9 cells, path clear (current) -> exposed target
    L: hit 1 heavy tank, 1 flak truck, 1 rocket soldier at (52,68) with 4 rocket soldier, 2 medium tank, win 70% -> exposed target
    M: attack economy: ore refinery at (70,64), 24 cells, value 1400, unguarded -> exposed target

Its JSON object (`state_object`), one item to a line:

    {
      "bases": [
        {"id": "B1", "side": "ours", "kind": "main", "pos": [22, 47], "dist": 32, "dir": "NW", "buildings": 4, "building_value": 4200, "building_types": {"construction yard": 1, "ore refinery": 1, "soviet barracks": 1, "power plant": 1}, "def_ground": 0, "def_air_only": [], "guard": 2700, "exposed_harvesters": 4, "closing": []},
        {"id": "B2", "side": "ours", "kind": "outpost", "pos": [16, 79], "dist": 32, "dir": "W", "buildings": 1, "building_value": 1400, "building_types": {"ore refinery": 1}, "def_ground": 0, "def_air_only": [], "guard": 0, "exposed_harvesters": 2, "closing": []},
        {"id": "E1", "side": "enemy", "kind": "outpost", "pos": [51, 75], "dist": 9, "dir": "SE", "buildings": 1, "building_value": 700, "building_types": {"sam site": 1}, "def_ground": 0, "def_air_only": ["sam site"], "guard": 1921, "exposed_harvesters": 0, "closing": [], "within_defense_reach": false, "reached_by": null, "in_range": false, "ratio": 3.0, "crush_ratio": 3.0, "beatable": true},
        {"id": "E3", "side": "enemy", "kind": "outpost", "pos": [70, 64], "dist": 24, "dir": "E", "buildings": 1, "building_value": 1400, "building_types": {"ore refinery": 1}, "def_ground": 0, "def_air_only": [], "guard": 0, "exposed_harvesters": 0, "closing": [], "within_defense_reach": false, "reached_by": null, "in_range": false, "ratio": null, "crush_ratio": null, "beatable": true},
        {"id": "E2", "side": "enemy", "kind": "main", "pos": [68, 51], "dist": 28, "dir": "NE", "buildings": 3, "building_value": 3000, "building_types": {"service depot": 1, "tesla coil": 1, "flame tower": 1}, "def_ground": 3600, "def_air_only": [], "guard": 0, "exposed_harvesters": 0, "closing": [], "within_defense_reach": true, "reached_by": "flame tower", "in_range": false, "ratio": 1.6, "crush_ratio": 1.6, "beatable": true}
      ],
      "enemy_forces": [
        {"id": "F1", "pos": [52, 68], "dist": 6, "dir": "E", "strength": 1921, "units": 3, "unit_types": {"heavy tank": 1, "flak truck": 1, "rocket soldier": 1}, "crushable": 0, "anti_tank": 1, "armor": 2, "motion": "still", "seen_s": 0, "closing_on": null, "within_defense_reach": false, "reached_by": null, "in_range": true, "ratio": 3.0, "crush_ratio": 3.0, "beatable": true}
      ],
      "our_forces": [
        {"status": "main", "units": {"medium tank": 2, "rifle infantry": 14, "rocket soldier": 4, "artillery": 1, "light tank": 1}, "strength": 5850, "hp": 100, "pos": [46, 68], "dist": 31, "dir": "SE"},
        {"status": "staging", "units": {"medium tank": 2}, "strength": 1700, "hp": 100, "pos": [33, 49], "dist": 23, "dir": "NW"},
        {"status": "guard", "units": {"rifle infantry": 5}, "strength": 500, "hp": 100, "pos": [29, 45], "dist": 29, "dir": "NW", "target": "none"},
        {"status": "scouts", "units": {"ranger": 1}, "strength": 500, "hp": 100, "pos": [34, 45], "dist": 26, "dir": "NW", "target": "idle"},
        {"status": "scouts", "units": {"ranger": 1}, "strength": 500, "hp": 100, "pos": [72, 18], "dist": 56, "dir": "NE", "target": "move to (32,48)"}
      ],
      "enemy_estimate": {"value": 6650, "assumed": false, "seen_s": 103, "unplaced": 4729},
      "fight_here": 3.0,
      "targets": ["T1: 1 flak truck, 1 heavy tank, 1 rocket soldier at (52,68), 6 cells, value 1,921, win 90%"],
      "threat": "none",
      "objective": "sam site (51,75), 9 cells; direct path clear",
      "current": "I advance to sam site to (51,75), 9 cells left, taken 32s ago",
      "last_picks": "I: advance to sam site, 32s ago -> killed 150 lost 0 ratio 1.0 -> 1.2; H: attack (40,50), 45s ago -> killed 1050 lost 400 ratio 0.9 -> 1.2; D: gather, 65s ago -> killed 3050 lost 600 ratio 0.6 -> 1.2",
      "score": {"lead": 1450, "killed": 9050, "lost": 7600, "t": "08:45", "left": "11:14", "plan": "HOLD"}
    }

`fight_here` weighs main's body against the enemy units within 15 cells of
its center and every remembered ground defense whose weapon range and 2 cells
reach that center or the cell of the next leg main is on its way to, each at
twice its cost (`defense_weight` of `[combat]`); an AA gun or a SAM site
never counts. With neither near it reads `fight_here: no enemy`. The
objective is the remembered enemy building (else MCV) that no ground defense
covers, the enemy's production first and then the nearest to main; only
while every one is covered is it what the sticky FINISH target's choice
would take now, whatever the plan.

The targets block, right after `fight_here`, tells the model what is near
main and our chance against each, so that it can pick its fights. The enemy
units that count in a fight (in sight or seen lately; never aircraft) within
20 cells of main's body (`target_reach_cells` of `[commander]`) are joined
into groups by single link at 4 cells (`target_group_cells`: two units are of
one group when a chain of units, each within 4 cells of the next, joins
them), and each ground defense within 20 cells (never an AA gun or a SAM
site) is a target of its own. The 4 nearest (`targets_max`) are listed as
`T1` to `T4`, a group with its units by type, its centroid, its distance and
its value, a defense with its cell and distance, each with our win chance:
round(100 r^2 / (1 + r^2)) percent to the nearest 5, kept between 5 and 95,
where r is the strength of main's body units that can hurt the target (by
the damage table: a unit that does it no damage adds nothing) over the
target's. A group's strength counts its units and each ground defense whose
range and 2 cells reach its centroid, at twice its cost (`defense_weight` of
`[combat]`); a defense's counts itself at twice its cost, the enemy units
within 8 cells of it and the other ground defenses whose range and 2 cells
reach it, so that four pillboxes side by side are weighed together (four
medium tanks win 85% against two light tanks, 35% against such a pillbox).
With nothing in reach the block reads `targets: none`.

The task options turn a listed target into a job for the units of main that
suit it. While the forward gate is closed, only targets within 10 cells of
main's centroid (`near_attack_cells`) qualify. Each is offered only at a win
chance of at least 50% for the units it names against its target, of each
kind the one with the highest chance:

- `J: run over N infantry at (x,y) with T tanks`: a group whose infantry in
  sight bunch at least 4 strong (`crush_min_infantry` of `[combat]`) within
  3 cells (`crush_cluster_cells`); its tanks are those a crush run would send
  through that cluster, main's tanks at half health or more
  (`crush_min_hp`) within 10 cells (`crush_reach_cells`), the nearest first,
  T = min(3, ceil(infantry / 3)) at most (`crush_max_tanks`,
  `crush_per_tanks`), whatever `crush_runs` says. Never offered while a
  crush run of the script's is in force: the one run at a time.
- `K: kill <defense> at (x,y) with N artillery`: a defense seen in the
  last 20 s (`kill_defense_seen_seconds` of `[commander]`), with every
  artillery piece and V2 launcher of main whose firing band against it is
  not empty: the siege's band for its type, which every known ground defense
  near the target narrows, so that a pillbox with a tesla coil 5 cells
  beside it leaves the artillery no cell to fire from. A unit whose band is
  empty is not named, and with none named K is not offered. Their chance
  counts the defense at nothing, only the enemy units within 8 cells of it,
  since they fire from beyond its range.
- `L: hit <group> at (x,y) with <units>`: a group, with up to 8 of main's
  units, the nearest first, whose damage a second against the armour most of
  the group wears is at least 1.5 times their mean over the five armour
  classes: rocket soldiers and tanks against tanks, rifles against infantry.
  Offered only while main's body without those units still holds its fight:
  `fight_here` of the rest of the body, at the rest's own center, 1.0 or
  more, or `no enemy`. Main of four heavy tanks and three artillery pieces
  at 1.4x, whose rest would read 0.65x, is offered no hit; with ten
  artillery pieces, at 2.6x, the rest reads 1.7x and the hit is offered.

No task and no raid trucks are offered on an enemy a detachment (below) is
already on, whatever their kinds: a detachment is keyed by the ids of its
targets (a task's, the enemies its option named; a raid's, the harvesters of
its opportunity), so a pillbox has one kill at most. An answer is taken only
while the question of the cycle it arrives in offers its action, so a hit
the rest of main could no longer bear by then is dropped, a `dropped` event
of reason `not_offered`, and forms no detachment.

A picked task, like a picked raid trucks, leaves main's standing action as it
is and sends a detachment out of main: the units its option named, at most 2
detachments out at once (`detachments_max`); while 2 are out neither a task
nor raid trucks is offered. The script runs it to its end, with normal order
slots, not the commander's. A run over drives the tanks through the cluster
as a crush run does (each to the cell 2 cells past the cluster's centroid,
let go there, over after 8 s or once the cluster breaks up), and while one is
out it counts as the one crush run: the script starts none of its own. A
kill fires the long-range units on the one defense from their firing band,
as the siege's long-range units fire, with no screen. A hit hands the units
targets among the group's units alone, as the focus fire hands them out, and
attack-moves those given none to the group's centroid. A task ends `done`
when its target is destroyed or gone (or the run is over by its cluster
broken or its tanks through), `failed` when half its units are lost or its
own chance has stayed under 20% (`task_fail_chance`) for 50 ticks
(`task_fail_ticks`), and `timeout` after the run's 8 s, 45 s for a kill
(`kill_defense_seconds`) or 30 s for a hit (`hit_group_seconds`). A kill
ends `done` only when its defense is seen destroyed, gone from the enemy
memory after a cycle in which it was in sight; one forgotten out of sight
(an own unit stood near its cell and did not see it) ends `failed`. A kill
also fails once either of two things has held for 50 ticks in a row, each
on a clock of its own: the firing band of every one of its units against
the defense empty, or the defense not seen for 20 s. A kill whose defense
was last seen at tick 1005 fails at 1555; one unseen for 30 ticks and then,
seen again, with an empty band for 30 more does not fail. A
`commander` event of kind `task_start` (`task`, the target's cell, its
enemies, the units, the chance) marks its forming, and one of kind
`task_end` (`task`, the reason, the value killed and lost) its end; the
value killed counts only the enemies seen destroyed. Its units then
take main's orders again, and the commander's order goes out at once to take
them in. While a detachment is out its units are left out
of main's question, its forward gate's body and value, `fight_here`, the
commander's order, the gathering and the joining units. Only two things take
them back early, every detachment ending at once: the retreat override
below, which takes them to the staging point with main (`pulled_back`), and
base defense calling main home or taking it over against a threat
(`recalled`); a task whose units have all left main otherwise ends
`recalled` too.

The threat line, `threat:`, names the first threat that holds: `base
attacked` (base defense has a threat in force), `enemy army closing at
(x,y)` (the enemy combat units seen in the last 10 s within 20 cells of the
base's center are worth 1,000 or more together, and their centroid, the cell
named, is closer to the base than the same units' centroid 15 s before, or
none of them was seen then), `losing here` (`fight_here` under 1.0), or
`none`. The defensive actions, pull back (8 cells toward home, while main
stands farther than that from the staging point), retreat home and hold, are
offered only while a threat holds. Retreat home is offered only while main's
centroid is more than 14 cells (`retreat_home_min_cells` of `[commander]`)
from the base's center, beyond the staging point 10 cells out: nearer, main
is home already, and pull back and hold are offered without it. The retreat
override (below) does not read this distance.

main goes forward only through its forward gate. Its body is its units within
6 cells (`gathered_cells` of `[raids]`) of its centroid. The gate opens when
the body holds 75% of main's units (`go_body_share`), main is worth 4,000 or
more (`go_min_value`, cost times health), and main is worth at least 0.8 of
the enemy estimate (`go_enemy_share`; not asked while the estimate is
assumed). It closes once main is worth less than half (`regroup_value_share`)
of what it was worth when it opened, or when the retreat override (below)
takes main. Each opening and closing is a `commander` event of kind `gate`
with main's value, its body share, the enemy estimate, whether it was
assumed and, on closing, the reason (`regroup` or `pulled_back`). While the
gate is closed main is offered gather, an attack on a group within 10 cells
of its centroid (`near_attack_cells`), the task options on the targets as
near and the defensive actions, nothing else; gather is main's standing
action from the handover until the first answer taken, and a forward action
standing when the gate closes gives way to it. While the gate is open, main's
value is main as a whole (its units and those joining it within 30 cells of
its centroid or sent on to it), the gate closes for a regroup only when that
is also under 0.8 of a seen enemy estimate, main worth 1.5
(`regroup_block_ratio`) of a seen estimate or more with no fight lost in the
last 30 s and no major threat on the base is not offered gather at all (at
least two other actions, advance among them, stay), and a gather offered while
main is still worth the estimate goes to main's own value-weighted centroid,
not the staging point: in game `20261014T030940Z` a gather picked mid-win at
12:49 pulled main, worth about 20,000 against 4,750, 28 cells home.

When the gate closes for a regroup, main's value under half its value when it
opened, gather becomes main's standing action whatever it was: the regroup
gather, a `commander` event of kind `regroup` with its letter and line after
the `gate` event, and the commander's order is sent again. Until the gate
opens again, or an answer makes another kind of action main's standing one,
the regroup gather is a retreat: every unit of main moves to the staging
point, those in contact with the enemy too, and none is given a target. Four
tanks left of main in the enemy's base are moved home rather than held in
their fights there. An answer arriving in the cycle the gate so closes is not
carried out, but for a raid trucks or task pick: a `fallback` event of
reason `regroup`, and no pick. A gather the model picks while the gate is
open, or the one main stands on from the handover, leaves the units in
contact firing at what is in their range, as before.

While the gate is open main
is offered gather, the task options and the active actions:
raid trucks (the freshest raid opportunity scouting has noted,
`exposed_trucks` of `[scouting]`, while main has a unit at half health or
more), attack (the nearest enemy group within 20 cells, never under an
estimate of 0.5), siege (the nearest defense, with artillery or V2s), flank
(by the safer side waypoint, or with `waypoints` of `[routing]` on by the
graph's route, while the straight way is in a defense's range or when it
would be the second active action) and advance. On a straight way in a
ground defense's range, with `waypoints` on, advance too goes by the graph's
route (`advance via (x,y) ...`, a `route` event of user `advance` when it is
taken); without a route it is left out while main has no artillery or V2s
and another active action is offered. While the gate is open, a unit that
joins main goes to the staging point first, and those standing there go on
together to main's centroid once 4 do or 30 s after the first arrived; they
join main within 6 cells of it.

main far behind the enemy fights near home only. While main (all its units,
cost times health) is worth less than 0.5 (`attack_min_army_share` of
`[commander]`) of the enemy estimate, the most the enemy army has been seen
worth, no action on an enemy target whose cell lies more than 15 cells from
the base's center is offered, the gate open or closed: no attack, advance,
siege, flank, raid trucks, run over, kill or hit there. Gather, the defensive
actions under a threat and the actions on targets within 15 cells of the
base's center still are. An assumed estimate, before any enemy army has been
seen, is a share of our own army and never holds main back. In game
20261013T130117Z main, worth 4,150 against an estimate of 13,450, picked
attack (H) while its fight fell from 13.8x to 0.6x; with this rule no attack
on a target more than 15 cells from home is offered, and main, losing, is
offered pull back, retreat home, hold and gather.

The script pulls main out of a fight it keeps losing. Once `fight_here` has
stayed under 0.9 (`retreat_ratio` of the `[commander]` table, not the
`retreat_ratio` of `[combat]`, which is 0.9 too) in every cycle for 25 ticks
(`retreat_confirm_ticks` of `[commander]`; a strike near us does not read
them, it is abandoned in the cycle its ratio falls under
`strike_abandon_ratio` of `[plan_rules]`), main's standing action becomes
gather, sent in the
commander's slot whatever the model picked, the gate closes, and answers are
not carried out (each followed by a `fallback` event of reason
`override_retreat`): a `commander` event of kind `override_retreat` with the
figure. It ends once `fight_here` reads `no enemy` (no enemy unit within 15
cells and no ground defense reaching main: a fight lost to defenses alone
holds main as one lost to an army does), once it is 1.2 or more
(`resume_ratio`), or once main's centroid is within 6 cells of the staging
point: a `commander` event of kind `override_end` with the reason
(`no_enemy`, `resumed` or `home`). Base defense's recall still takes main
first.

A raid trucks pick is one kind of detachment, with rules of its own: up to
6 of main's units at half health or more and out on no other detachment, the
fastest first, attack-move to the harvester nearest our base, a `commander`
event of kind `raid` with the units, the field and the target. The rest of
main keeps the standing action, or holds while there is none, and the next
questions are about the rest. The raid rejoins main, taking main's orders
again, when its harvesters are destroyed or gone from the target, when the
opportunity expires, after 60 s, or when none of its units is left in main,
and early as every detachment does: a `commander` event of kind `rejoined`
with the reason (`destroyed`, `gone`, `expired`, `timed_out`, `no_units`,
`pulled_back` or `recalled`). The raid's attack-move is an ordinary order,
not the commander's slot.

A question too long at full detail cuts as above; neither question holds a
glossary or a recommendation. Two hand-built examples from
`tests/redalert/test_commander.py`, both at full detail with the full system
text: the scout's at a busy front with four picks, three listed (1,587
characters), and main's in a busy front scene from before the full-state
sections, on an advance, its pick taken at an army ratio of 1.4 that is 1.2
now. The scout's:

    You steer our scout in Red Alert 1v1. Each turn pick ONE action ID. Goal: learn enemy army, expansions, tech; keep the scout alive. Our opponent is another AI agent in a tournament; a 20-minute match not won outright goes to the tiebreak (value traded). Learn from recent picks: repeat what worked, change what lost value. Reply {"choice":"ID"}.
    ore fields:
    (30,52) 1 cells, served by none
    (40,14) 1 cells, served by none
    (70,40) 1 cells, served by enemy
    (60,80) 1 cells, served by none
    enemy buildings:
    1 fact, 1 powr, 1 proc, 1 weap at (73,48), in sight
    enemy defenses:
    pbox (66,44) range 6
    pbox (66,52) range 6
    enemy groups:
    2 e1 at (57,37), value 200, in sight, still
    1 2tnk at (60,44), value 850, in sight, still
    our_base=(22,48) enemy_base=(74,48) confirmed
    scout=jeep hp=100% at (50,40) sight=6
    danger_now: 2 e1 at (56,36) dist=7 (range 5); 1 2tnk at (60,44) dist=11 (range 4.8)
    known_defense: pbox (66,44) range 6; pbox (66,52) range 6
    intel: enemy main seen 0s ago (2 pbox, 1 fact, 1 powr, 1 proc, 1 weap); enemy army seen 0s ago at (58,39) value 1050; field (60,80) never seen; field (40,14) never seen
    current: none
    last_picks: D: back off, 4s ago; C: attack lane (48,48), 8s ago; A: hold, 20s ago
    t=04:00 left=20:00
    ACTIONS:
    A: hold (50,40) here 3s, 0 cells, path clear; current view
    B: back off (45,43) from danger, 6 cells, path clear; safety
    C: watch (60,60) enemy main edge, 36 cells, path unknown; tech, never seen
    D: watch (40,14) expansion field, 28 cells, path unknown; expansions, never seen
    F: watch (48,48) attack lane, 8 cells, path unknown; army route, never seen

Main's busy-front example as it was written before the full-state sections,
at its last level of detail (1,591 characters); the snapshot scene's question
under **The military state** (above) shows main's layout now:

    You steer our scout in Red Alert 1v1. Each turn pick ONE action ID. Goal: learn enemy army, expansions, tech; keep the scout alive. Our opponent is another AI agent in a tournament; a 20-minute match not won outright goes to the tiebreak (value traded). Learn from recent picks: repeat what worked, change what lost value. Reply {"choice":"ID"}.
    t=04:00 left=20:00
    our_base=(22,48) enemy_base=(74,48) confirmed
    scout=jeep hp=100% at (50,40) sight=6
    danger_now: 2 e1 at (56,36) dist=7 (range 5)
    known_defense: pbox (66,44) range 6
    intel: enemy main seen 0s ago; enemy army seen 0s ago at (58,39) value 1050
    current: C enemy main edge to (60,60), 22 cells left, taken 5s ago
    last_picks: B: back off, 4s ago
    ACTIONS:
    A: hold (50,40) here 3s, 0 cells, path clear
    B: back off (45,43) from danger, 6 cells, path clear
    C: watch (60,60) enemy main edge, 36 cells, path unknown (current)
    D: watch (40,14) expansion field, 28 cells, path unknown
    F: watch (48,48) attack lane, 8 cells, path unknown

    You command our main army in Red Alert 1v1. Each turn pick ONE action ID. Keep the army busy when we are even or winning: attack, siege, advance, flank or raid. Our opponent is another AI agent in a tournament; a 20-minute match not won outright goes to the tiebreak (value traded). Doctrine: economy and map control early; never attack into defenses we cannot beat; retreat and regroup when outnumbered; tanks crush infantry and kill tanks; artillery fires from behind a screen; punish exposed expansions and harvesters; keep a lead late. Learn from recent picks: repeat what worked, change what lost value. Reply {"choice":"ID"}.
    t=08:20 left=20:00 plan=PRESS base_threat=none
    army: 14 units value 10360 (8 heavy tank) center (43,47) avg_hp 89% damaged<50%: 0
    reinforcements: 3 stragglers, about 10s out
    enemy_near (15 cells): 5 medium tank at (55,47) value 4550
    enemy_defense_near: none
    enemy_total_est: 15650 (assumed)
    fight_here: we are 2.3x
    targets:
    T1: 5 medium tank at (55,47), 12 cells, value 4,550, win 85%
    threat: none
    objective: construction yard (74,48), 31 cells; direct path in pillbox range
    current: I advance to construction yard to (74,48), 31 cells left, taken 4s ago
    last_picks: I: advance to construction yard, 4s ago -> killed 200 lost 300 ratio 1.4 -> 1.2
    ACTIONS:
    D: gather at staging (32,48), 11 cells
    E: siege pillbox (63,40), 21 cells, its range 6
    F: flank via (58,59) to construction yard (74,48), 38 cells
    H: attack 5 medium tank, 3 rifle infantry at (55,47), 12 cells, fight 2.3x
    I: advance to construction yard (74,48), 31 cells, path in pillbox range (current)

The script carries the standing action out with its own machinery. A
scout's destination becomes its course and scout steering takes it there,
safely when it can; a held scout is left alone. For main the action replaces
its plan-driven movement under every plan: an attack-move on the group, the
siege of the defense, the move to the flank's (or a routed advance's)
waypoints and then the march, the march on the objective (with
`march_when_clear` as it says), a move (pull back, retreat home, gather), or
a stop; per-unit targeting and siege micro stay with the script. The
action's one order (for main, the units nearest its target first, up to 50)
goes in an order slot kept for it: batch packing takes it before every
other order, so the other managers share 19 of the 20, and all 20 while
there is none. It is sent when the action changes, when one of its units
stands idle, or 25 ticks after it was last sent, not in every batch. A
scout's flight, and base defense against a major threat worth more than the
home guard and the standing defenses near it, override the model; a smaller
major threat leaves main to its action under every plan, as
`keep_attack_army` does under PRESS, STRIKE and FINISH. Each question and
answer is a `decision` event with the full system text, user message and
raw answer. A model that stops answering leaves the last action standing.
Watch it with the 4B model, and the gate, override, regroup, pick and task
events (`task_start`, `task_end`, `raid`, `rejoined`) in the `commander`
events of the record:

    python scripts/watch_game.py --model small --set agent:redalert.model_commander=true

When a tracked file cannot be read, the agent plays the match with built-in
values equal to the shipped ones and writes a `handled` error event saying so.
The platform's game reference (`get_game_config("red_alert").rules`, which
every match brings) is the authority on item facts: each value it gives
replaces the one in `config/redalert/items.toml`.

## Compact shorthand

| | |
|---|---|
| What it does | Two short text forms for a model. `summarize` writes the match as at most 900 characters of plain ASCII, one `key=value` group per line (time, money, power, trade balance, army, groups, buildings, what is known of the enemy, threat, plan): this is the context sent with a plan-choice question while `model_briefing` is `false` (with it `true`, the plan choice briefing is sent instead; see [Red Alert scripted core](#red-alert-scripted-core)). `translate` turns command shorthand (`plan`, `am`, `mv`, `atk`, `ret`, `hold`, `tr`, `bd`, one command a line) into the orders the script's own managers would send, and refuses any line it cannot turn into such an order; each refusal is written to the match record as a `decision` event with outcome `invalid`. |
| Where it is | `src/ucla_agent/games/redalert/shorthand.py`. |
| Settings | `model_commands` in `[redalert]` of `config/agent.toml`; as shipped `false`, and a line a model wrote is then refused. |

In the agent as built, no decision source is asked to write commands: the one
question a model can be asked is the plan choice, answered with the id of one
of the open plans, after a reason of at most 30 words while `model_briefing`
is `true`. A `plan` line only asks the plan selector for an R7 plan
and never overrides R1 to R6 or a sparring pin.

## Werewolf

| | |
|---|---|
| What it does | Plays `werewolf` in every role. Each state is first taken into a ledger of what our seat knows and a role inference over every way the wolves and the seer can sit. Each vote, kill or investigation is one of the candidates our role's plan ranks, the first being the script's own choice; with model choices on, a model may pick another of those candidates and nothing else. Each chat is planned by the script, written by a model or taken from the scripted messages, and sent only when it passes the checks below. |
| Where it is | `src/ucla_agent/games/werewolf/`. The decision-maker is `ucla_agent.games.werewolf.agent.WerewolfAgent`. |
| Settings | `config/werewolf/tuning.toml` (the inference's evidence weights, the village's and the wolves' thresholds, the chat's patterns, limits and scripted messages), and the `[werewolf]` table and the `werewolf_choice` and `werewolf_message` chains of `config/agent.toml`. |

The parts and where each one is:

| Part | What it does | Where it is |
|---|---|---|
| Fact ledger | What our seat knows, kept in kinds that are never mixed: the engine's public facts, our private facts (role, fellow wolf, seer results), every chat message seen, and the claims other seats made, read by fixed patterns as their words and never as facts. | `src/ucla_agent/games/werewolf/ledger.py` |
| Role inference | Weighs each of the 105 ways two wolves and the seer can sit by the certain facts and the evidence of votes and claims, and gives each seat its chance of being a wolf and of being the seer. | `src/ucla_agent/games/werewolf/inference.py` |
| Village plan | The vote of a villager or the seer (never abstaining, never a seat our own seer results cleared, a wolf the seer found first; a villager, on the day a lone seer claimant claims, never votes that claimant while another seat can be voted, and votes the seat it named a wolf first when it is nearly as suspect as the top one: `spare_lone_claimant` in `[village]` of the tuning file), the seer's investigation, and when the seer says it is the seer with every result. | `src/ucla_agent/games/werewolf/village.py`, `src/ucla_agent/games/werewolf/talk.py` |
| Wolves' plan | The night kill both wolves rank alike, so two copies agree without talking; one private message a day naming it to the ally; the cover vote, which names the ally only when the table is voting it out anyway; the counterclaim against a seer claimant that named the ally a wolf. | `src/ucla_agent/games/werewolf/wolves.py` |
| Chat | In each discussion window: a public opening, the private message to the living ally (a wolf only), and at most one public reply to an accusation of our seat; at most three chats, then our part of the window ends. Every text is checked before it is sent, as described below. With `pakled_persona` in `[chat]` of the tuning file on, a villager or a wolf plays a Pakled from Star Trek: its first chat of each window is one of five Pakled lines (none repeated before all five have come, and taking one of the three chats), a model writes its other messages in a Pakled voice and its scripted ones start "We think: ", and a seer claim naming a wolf (the seer's reveal of a wolf it found, a wolf's counterclaim) opens with one of two Pakled seer lines before the plain claim; the seer otherwise chats as without it. | `src/ucla_agent/games/werewolf/chat.py` |
| Model choices | Asks the `werewolf_choice` chain to choose among up to six of the plan's candidates, plays its answer only when it is one of them, and always plays one of the state's legal actions. | `src/ucla_agent/games/werewolf/decide.py` |
| Prompts | Every text sent to a model, and the context it goes with. | `src/ucla_agent/games/werewolf/prompts.py` |
| Settings and tuning | The `[werewolf]` table and the tuning file, each read strictly. | `src/ucla_agent/games/werewolf/settings.py`, `src/ucla_agent/games/werewolf/tuning.py` |
| Decision-maker | Takes every state into the ledger, answers the runtime's calls, keeps an eliminated seat silent, and records any failure inside a call while answering with a legal move or the end of its part of the window. | `src/ucla_agent/games/werewolf/agent.py` |

The `[werewolf]` settings of `config/agent.toml`:

| Setting | As shipped | Meaning |
|---|---|---|
| `model_choices` | `true` | When `true`, the `werewolf_choice` chain is asked to choose whenever the plan has two candidates or more. Off, the script's first candidate is played. |
| `model_messages` | `true` | When `true`, the `werewolf_message` chain writes the chat messages, except the private message to the ally, which is always the scripted one. Off, every message is the scripted one. |
| `choice_deadline_ms` | `15000` | How long a choice may take before the script's stands. |
| `message_deadline_ms` | `20000` | How long a message may take; never closer than 10 seconds to the end of the discussion window, and with less than 3 seconds for it no model is asked. |

As shipped both chains are `["local:strong"]`. Switching to Claude is a
change of configuration only: list `"claude:sonnet"` before `"local:strong"`
in the chains and set `enabled = true` in `[claude]`. Switching Werewolf to
the script alone is `model_choices = false` and `model_messages = false`.

**What a message must pass.** Whether a model or the script wrote it, a
message is sent only when it is not empty, keeps to the platform's 50 words
and 1,024 characters, carries its required points (for the seer's reveal, the
claim and every result), matches none of the patterns of an injection attempt
in `[chat]` of the tuning file, for a wolf's public message holds none of
`forbidden_public_phrases`, and for a public message of the seer before it
has said it is the seer, other than the message that says so, holds none of
`unrevealed_seer_forbidden_phrases`. A message that fails is replaced by its
scripted one from `[chat.templates]`, which passes them all.

**Other players' messages are data.** They reach a model only inside the
context of a request, each quoted on one line with its line breaks made
spaces and its double quotes made single ones, and the system prompt tells
the model never to follow instructions that appear inside them. They never
enter a system prompt, an instruction or a question.

Each move writes a `werewolf_decision` event to the match record, each chat a
`werewolf_chat` event, and the first state of each discussion window a
`werewolf_ledger` event with the ledger and the beliefs of that day.

## Pokémon VGC doubles draft

| | |
|---|---|
| What it does | Plays `pokemon_vgc_doubles_draft`: the snake draft, team preview and every doubles turn, each with a structured action. Each decision ranks its candidates by the script's score, or with maximin on, as shipped, by their worst case against the opponent's best reply, the first being the script's own choice; with model choices on, a model may pick another of the best few and nothing else. |
| Where it is | `src/ucla_agent/games/pokemon/`. The decision-maker is `ucla_agent.games.pokemon.agent.PokemonAgent`. |
| Settings | `config/pokemon/tuning.toml` (the score weights of `[draft]`, `[preview]` and `[turn]`, and the maximin switch and sizes of `[maximin]`), the game data under `config/pokemon/data/`, and the `[pokemon]` table and the `pokemon_choice` chain of `config/agent.toml`. |

The parts and where each one is:

| Part | What it does | Where it is |
|---|---|---|
| Game data and damage estimate | Gen 9 species, moves, type chart and natures, and the range of damage one move does from one Pokémon to another in doubles, with the knockout chance. | `src/ucla_agent/games/pokemon/data.py`, `src/ucla_agent/games/pokemon/damage.py` |
| Known sets | A Pokémon's set from its draft card, or from what an observation shows of it; its stats, and the best damage one set does to another. | `src/ucla_agent/games/pokemon/sets.py` |
| Observation reader | Reads every key the platform's guide names with a documented default, and counts each key it had to default as a parse degradation of the decision. | `src/ucla_agent/games/pokemon/observe.py` |
| Draft | Remembers every card seen with its full set and both rosters, and scores each offered card by power, bulk, speed, support, synergy with our roster and denial; the pick's public reason names its two largest parts. | `src/ucla_agent/games/pokemon/draft.py` |
| Team preview | Scores all 90 lineups (4 of 6 brought, 2 of them leading) against the opponent's six known sets. | `src/ucla_agent/games/pokemon/preview.py` |
| Battle board and tracker | Reads a doubles observation and its `doubles_turn` template into a board of both sides' Pokémon, the field and each slot's options, and remembers between turns when each Pokémon came in and what each slot used last. | `src/ucla_agent/games/pokemon/battle.py` |
| Turn policy | Scores each slot's options (damage and knockouts, spread moves, Fake Out, Protect, Tailwind and Trick Room, switches) and ranks the legal pairs of them. A Protect (or a move like it) after N in a row by the same Pokémon, which the battle tracker counts from our choices (a failed one too) until another move, a switch or another species, scores minus `[turn] protect_bonus` times N and, in the maximin one-turn model, keeps later moves off its user with chance (1/3)^N, as Showdown has it, with no setting of its own. A Protect by a Choice Scarf, Band or Specs holder while a damaging move is on offer beside it scores minus `protect_bonus`, since the item would lock the holder into Protect until it switches, and maximin leaves the joint actions holding it out of `our_top`; offered alone, it is still played. | `src/ucla_agent/games/pokemon/turn.py` |
| Maximin turn | With `[maximin] enabled`, reads the board as the opponent sees it, and ranks our best joint actions by their worst result against the opponent's best replies through a one-turn model (below). Team preview and the draft have their maximin ranking in their own modules. | `src/ucla_agent/games/pokemon/maximin.py` |
| Model choices | Asks the `pokemon_choice` chain to choose among the script's best `top_k` candidates (with `model_decides` on, the best `decide_top_k`, unscored and in a shuffled order), plays its answer only when it is one of them, and writes one `pokemon_decision` event per decision. | `src/ucla_agent/games/pokemon/decide.py` |
| Model briefing | With `model_briefing` on, builds the context each model choice is asked with: the draft pool and both rosters with every set, both sixes with a matchup table at team preview, and in battle every Pokémon's set, the field, the speed order and what beats what (below). The battle briefing ends with a Recent turns section read from the observation's protocol log: the last `[briefing] recent_turns` completed turns of `config/pokemon/tuning.toml` (2 shipped, 0 off), oldest first, one line each of both sides' moves with their effectiveness, critical hits, Protects, misses, failures and HP change, switches, faints and field changes, at most 1,200 characters with the oldest actions cut first, an unreadable log counting as a parse degradation and the script never reading it. | `src/ucla_agent/games/pokemon/briefing.py` |
| Template-only fallback | Answers a state that cannot be read from the legal action's template alone, the starter smoke agent's way, and checks every lineup and turn against its template. | `src/ucla_agent/games/pokemon/template.py` |
| Settings and tuning | The `[pokemon]` table and the tuning file, each read strictly. | `src/ucla_agent/games/pokemon/settings.py`, `src/ucla_agent/games/pokemon/tuning.py` |
| Decision-maker | Dispatches each state by its phase, keeps the match's draft memory and battle trackers (ours, and with maximin the opponent's view), and answers a state it cannot read, or a failure of its own, with the template-only fallback and an `error` event (`unexpected_state` or `unhandled`). | `src/ucla_agent/games/pokemon/agent.py` |

The `[pokemon]` settings of `config/agent.toml`:

| Setting | As shipped | Meaning |
|---|---|---|
| `model_choices` | `true` | When `true`, the `pokemon_choice` chain is asked to choose whenever a decision has two candidates or more. Off, the script's best is played. |
| `top_k` | `4` | How many of the script's best candidates the chain chooses among (ids `C1`... for draft cards, `L1`... for lineups, `T1`... for turns). |
| `draft_deadline_ms` | `6000` | How long a draft choice may take before the script's stands; a pick has 15 seconds on the platform's clock. |
| `preview_deadline_ms` | `20000` | The longest the team preview lineup may take; the observation's clock cuts it further (below). |
| `turn_deadline_ms` | `20000` | The longest a doubles turn may take; the observation's clock and the bank cut it further (below). |
| `decision_margin_seconds` | `10` | Seconds kept back from the decision's clock: a lineup or a turn never waits on the model past the clock less this margin. |
| `bank_share` | `0.10` | The most of the battle bank one doubles turn may spend waiting on the model (above 0, at most 1). |
| `bank_reserve_seconds` | `90` | Waiting on the model never takes the bank below this; with the bank at or below it, no model is asked. |
| `no_clock_deadline_ms` | `5000` | The longest a lineup or a turn waits on the model before Showdown has reported the clock for the decision. |
| `model_briefing` | `true` | When `true`, the model is asked with the full briefing (below) as the context of each choice. Off, it reads a one-line summary of the rosters or the board. |
| `model_decides` | `true` | When `true`, the model decides from a wider shortlist put plainly: the script's best `decide_top_k` candidates without their scores, in a shuffled order, with a short reason written before the choice (below). Off, it chooses among the best `top_k` as described above. |
| `decide_top_k` | `6` | How many of the script's best candidates the model is offered while `model_decides` is on (2 to 9). |
| `decide_reason_words` | `40` | The most words of the reason the model writes before its choice while `model_decides` is on (1 to 60). |

The event game runs Showdown's VGC timer: 90 seconds for Team Preview (the
platform plays a random lineup at 85), 55 seconds for each battle decision (a
random choice at 50), and a bank of 420 seconds for the whole battle, which
every battle decision draws on and whose running out forfeits the battle. The
current decision's observation carries `clock` (`decision_seconds_left`,
`bank_seconds_left`) once Showdown has reported it, and the model's deadline
follows it:

- a doubles turn with a clock: the least of `turn_deadline_ms`, the decision's
  seconds left less `decision_margin_seconds`, `bank_share` of the bank, and
  the bank less `bank_reserve_seconds`; as shipped, 20 s early in a battle, a
  tenth of the bank once it is under 200 s, the bank's time above 90 s once it
  is under 100 s, and no model once it is at 90 s or less;
- the team preview with a clock: the lesser of `preview_deadline_ms` and the
  decision's seconds left less `decision_margin_seconds`; the bank is not used;
- a lineup or a turn without a clock: at most `no_clock_deadline_ms`;
- a draft pick: `draft_deadline_ms`, whatever the clock.

A deadline of 0 or less asks no model, and the script's best, always computed
first, is sent at once. A clock the observation leaves out is no parse
degradation; one that is there but unreadable is.

With `model_briefing` on, as shipped, the context of each model choice is the
briefing `briefing.py` builds, from figures the script already computes and
nothing else:

- each Pokémon is one set line, `Name [Type/Type] @ item, ability | HP h Atk a
  Def d SpA sa SpD sd Spe s | moves: Move (Type, power or status, target), …`,
  with the stats at level 50; a set no card seen in the pool gave (a card the
  opponent took before we first saw the pool) is made from its species'
  facts and marked `(guessed set)`;
- a draft pick: every card left in the pool, both rosters in pick order and
  the picks each side has left; each offered card's option also names its
  types and Speed;
- team preview: both sixes, and one matchup line for each of the 36 pairs
  with the best damage of one hit each way, in percent of the target's HP,
  and which of the two is faster;
- a doubles turn: the field (weather, terrain, Trick Room, and each side's
  Tailwind, Reflect, Light Screen and Aurora Veil); each side's actives by
  slot with HP, status and stat stages, then its bench (ours from the team,
  the opponent's from its drafted roster less its actives, `HP unknown` until
  seen); the speed order of the actives (stages, Choice Scarf, paralysis and
  Tailwind, slowest first under Trick Room); and for each ordered pair of
  opposing actives the best move by expected damage with its knockout chance
  and type effectiveness, the turn scorer's own figures.

A briefing is at most 24,000 characters, about 8,000 tokens, half the
`strong` role's 16,384-token window. A longer one leaves out the benched
Pokémon's move lists first, then the guessed sets' move lists, and says so in
a note; one still too long is cut at the cap. A model decision's `io` keeps
the whole briefing.

With `model_decides` on, as shipped (the practice benchmark's figures and
the decision are in [POKEMON-BENCHMARKS.md](POKEMON-BENCHMARKS.md)), each
model choice is put differently:

- the script's best `decide_top_k` candidates are offered, not `top_k`;
- no option shows the script's score, its parts or its rank: a draft card is
  its set line (above), a lineup the four brought and the two leads, and a
  doubles turn both slots' actions with their expected damage and knockout
  chance (a spread move or a move at a random foe gives them at each target,
  and a forced switch leaves out its matchup);
- the options are listed in an order shuffled for each decision by a random
  generator seeded with the CRC-32 of the decision's state tag, so one
  situation always shows one order, and the ids (`C1`..., `L1`..., `T1`...)
  follow the order shown;
- the question says the options are unranked and in random order, and asks
  for the best one for this situation;
- the model answers `{"reason": "<at most decide_reason_words words>",
  "choice": "<id>"}`, its reason first (the `reason_first` request of
  [Decision sources](#decision-sources)), in at most 160 tokens.

The deadlines are the same either way, and a late or invalid answer plays
the script's best wherever it is shown. The `pokemon_decision` event lists
the candidates in the order shown and adds `shown_order` (each shown
candidate's rank in the script's order, 1 for its best) and
`script_rank_of_choice` (the rank of the one played). With `model_decides`
off, every choice is put as described above.

With `[maximin] enabled` in `config/pokemon/tuning.toml`, as shipped (the
practice benchmark's figures and the decision are in
[POKEMON-BENCHMARKS.md](POKEMON-BENCHMARKS.md)), the script ranks each
decision's candidates by their worst case against the opponent's best reply.
The `[maximin]` table is read strictly like the rest of the file:

| Setting | As shipped | Meaning |
|---|---|---|
| `enabled` | `true` | When `true`, the draft, team preview and every turn are ranked by maximin (below). Off, by the script's own scores; a `Tuning` made in code without the table has it off. |
| `our_top` | `8` | How many of our best joint actions by the `[turn]` score a turn weighs (a whole number of at least 1). |
| `their_top` | `8` | How many of the opponent's best joint actions, scored the same way from their side, each of ours is weighed against (at least 1). |
| `draft_lookahead` | `true` | Whether a draft pick looks one pick ahead. |

What each phase does with it:

- a doubles turn (`maximin.py`): the board is read as the opponent sees it,
  their actives with every move of their known sets aimed at our standing
  actives, switches into their benched Pokémon seen so far and pass for an
  empty position, an item or ability not known counting as none; a tracker
  of its own follows that view, so their Fake Out counts on its first turn
  out. Their joint actions are ranked by the `[turn]` scores from their side.
  Each of our best `our_top` is played against each of their best `their_top`
  through a one-turn model: switches first, then moves by priority and Speed
  (stages, Choice Scarf, paralysis, Tailwind; the slower first under Trick
  Room; the opponent first at a tie), Protect keeping every later move off
  its user (with chance (1/3)^N after N Protects in a row), Fake Out making its
  target flinch on its user's first turn out, each hit's expected damage and
  knockout chance from the damage estimate with the HP left and the chance to
  be standing carried from move to move, a move at a fallen foe turning to
  its partner, and a self-fainting move costing its user. A pairing's result
  is the HP percent we deal less what we take, plus `[turn] ko_bonus` per
  knockout made less per knockout suffered. Our actions are ranked by their
  worst result, ties by the mean; the candidates are those `our_top`, then
  the rest in the script's order;
- team preview (`preview.py`): each of our 90 lineups is scored against each
  of the opponent's 90 (4 of their known six, 2 of them leading) with today's
  lineup scoring, their lineup standing in for their six (our four against
  their four, our leads against their leads times `lead_weight`, our speed
  control less theirs), and ranked by its worst score, ties by the mean;
- a draft pick (`draft.py`), with `draft_lookahead` on: each offered card
  gains a `lookahead` part, the best total the opponent's next pick could
  reach now less the best it could reach once we take the card (Item Clause
  respected), so the card they want most gains the gap to their second best
  and the others about nothing; the reason may name it ("leaves the opponent
  a weaker next pick").

The ranking must be done by the decision's clock less
`decision_margin_seconds`, or without a clock by the phase's configured
deadline, and a turn's evaluation never runs past 300 ms (about 5 ms for 8
against 8 on a full board); one that is not done in time gives way to the
script's own ranking. Each candidate's option, scored or not, ends with the
opponent's best reply and its worst case in words, for a model with
`model_choices` on to read (`briefing.py`), for example:

    slot 0: Close Combat -> foe B incineroar: 45%, KO 100%; slot 1: Wood Hammer -> foe A fluttermane: 100%, KO 100%; their best reply: their A Flutter Mane uses Moonblast at our A Urshifu-Rapid-Strike; their B Incineroar uses Flare Blitz at our B Rillaboom; worst case: we deal 100% and take 134% of HP, with 1.0 of theirs and 1.4 of ours expected to faint
    bring simisear, rotomheat, simisage, rotommow; lead rotomheat and simisage; their best reply: bring Garchomp, Chi-Yu, Incineroar, Milotic; lead Chi-Yu and Incineroar; worst case: the matchups come to -820 (our best damage less theirs, in %)

and a draft card's option ends with `their likely next pick: <card>`. The
practice benchmark sets maximin for our seat with `--maximin on|off`
(default off, whatever the file ships), and `summary.json` records it as
`maximin`; the practice opponent `self` plays the tracked tuning. To watch
it, read the `ranking` field of the `pokemon_decision` events.

As shipped the chain is `["local:strong"]`. Switching Pokémon to the script
alone is `model_choices = false`. Each decision writes a `pokemon_decision`
event to the match record: the phase, the candidates offered with their scores
and actions, the script's choice, the one chosen, which source decided, the
outcome of the request, the latency, the parse degradations of its
observation, the clock it was read with (`clock_decision_seconds_left` and
`clock_bank_seconds_left`, null without one) and the model's `deadline_ms`.
While maximin is enabled it also holds `ranking`: `maximin` when the
candidates were ranked by maximin (their scores then the worst results),
`script` when the script's own ranking stood (out of time, or the draft
without `draft_lookahead`).

## Included model server

| | |
|---|---|
| What it does | Runs one `llama-server` process per model role, each with one model file, and serves all of them through one HTTP endpoint on the loopback interface (`http://127.0.0.1:11500`): `POST /v1/chat/completions`, `GET /v1/models` and `GET /health`. |
| Where it is | `src/ucla_agent/core/modelserver/`. The `llama-server` program is [llama.cpp](https://github.com/ggml-org/llama.cpp)'s and is not part of this repository. |
| Settings | `config/models.toml` (the endpoint, the `llama-server` path, and every option of every role), and `autostart` and `autostart_roles` in the `[models]` table of `config/agent.toml`. |

Game code reaches a local model only through that endpoint and names a role,
never a model file. The server is started and stopped together with the agent
when `autostart` is `true`, with only the roles `autostart_roles` lists; as
shipped it is `true` with `strong` and `jev9b`, about 53 GB of VRAM together,
and `fast` and `small` are not loaded.
It never downloads a model, and every `llama-server` it starts ends when the
agent's process ends. `docs/MODELS.md` lists each role's model file, where it
comes from, and every setting.

### Model roles

| Role | Model file | Source | Quantization | Asked by |
|---|---|---|---|---|
| `fast` | `gemma-4-26B-A4B-it-qat-heretic-UD-Q4_K_XL.gguf` | Gemma 4 26B A4B, instruction-tuned, quantization-aware-trained, "heretic" (decensored) variant: <https://huggingface.co/SC117/gemma-4-26B-A4B-it-qat-heretic-GGUF>, revision `82c751632a54978819dda9b95028dc4fcd117ece` | GGUF `UD-Q4_K_XL` | no chain as shipped; for testing model choices, by a local edit of a chain to `local:fast` |
| `strong` | `Qwen3.8-27B-Q8_0.gguf` | Qwen3.8 27B: <https://huggingface.co/bartowski/Qwen3.8-27B-GGUF>, revision `f0eec4a4bb4975114a030d048952d83c0a53c034` | GGUF `Q8_0` | the `werewolf_choice` and `werewolf_message` chains (`local:strong`) |
| `small` | `Qwen3.5-4B-Q8_0.gguf` | Qwen3.5 4B, quantized by Unsloth: <https://huggingface.co/unsloth/Qwen3.5-4B-GGUF>, revision `e87f176479d0855a907a41277aca2f8ee7a09523` | GGUF `Q8_0` | no chain as shipped: defined but not used as shipped; a chain entry `local:small`, with `small` listed in `autostart_roles`, would select it |

All three are run with temperature 0, a fixed seed and reasoning off.
`docs/MODELS.md` gives each file's size, SHA-256 and download link and every
option it is started with. The model files themselves are not in the
repository (see [Excluded items](#excluded-items)).

## Decision sources

| | |
|---|---|
| What it does | Puts a choice among options that a game's script has prepared to a decision source, and gives the game the chosen option or none; or asks a source for a short text, which the game checks before it uses it. A source is a role of the included model server (`local:<role>`), the Jev decision service (`jev`), or a Claude model (`claude:<alias>`). |
| Where it is | `src/ucla_agent/core/decide/`. |
| Settings | The `[decide]`, `[decide.chains]`, `[jev]` and `[jev.fields]` tables of `config/agent.toml`, and its `[claude]` and `[claude.models]` tables; the Jev key is `JEV_API_KEY` in `.env`, and the Claude key is `ANTHROPIC_API_KEY` in `.env`. |

Each question has a deadline (`default_deadline_ms`, 1,500 ms). A source that
does not answer in time, fails, or answers with something that is not one of
the options is passed over, and the game's script makes the choice itself; a
source that fails three times in a row is left alone for 30 seconds. Which
sources a kind of choice goes to, and in which order, is set per chain in
`[decide.chains]`. Every call is written to the match record as a `decision`
event, and every call that did not end in a used answer is followed by a
`fallback` event that names what decided instead. Jev is optional and off by
default: while `enabled` in the `[jev]` table is `false` no request is ever
sent to it, and its key goes only to the Jev service's own address.

A call that sent a request to a model, a choice (`decision`) or a text
(`text_decision`), also carries an `io` object: the system and user messages
exactly as sent, the answer format (the JSON schema, or Claude's answer
line), the settings (the role or model id, `temperature`, `max_tokens`,
reasoning on or off and the deadline), and the model's output exactly as it
came back, also when it was not used, with any reasoning it returned apart
from it. For Jev it holds the request body and the response body. Each text
is cut at 32,000 characters, ending with `…[cut N chars]`. It never holds a
request header or a key, and the record's redaction runs over it like every
other line. A call that sent nothing (circuit open, Claude off or without a
key) has no `io`, and neither has a `fallback` event.

As shipped there are six chains. `red_alert_plan = ["systemone:jev9b"]` is
asked only when `model_plan_choice` is `true`, as shipped,
`red_alert_commander = ["systemone:jev9b"]` only when `model_commander` is, as
shipped, and `red_alert_scout = ["systemone:jev9b"]` only when `model_scout_choice` and
`scout_slots` of `[scouting]` are, by one chooser for each scout slot apart
from the plan's. A local role answers with the
id of one option, as a JSON object the model server constrains to a schema
that lists exactly the open options. With `choice_reasons = true` in
`[decide]`, as shipped, a local role and Claude are also asked for a short
reason in the same object, `{"choice": "<id>", "reason": "<at most 12
words>"}`, with room for 64 tokens from a local role and 56 from Claude (24
and 16 for the choice alone, with `choice_reasons = false`). The reason is
written to the `decision` event as `model_reason`, cut at 100 characters,
beside `reason`, which says why an answer was not used. It is for people
reading the record: a missing or empty reason is recorded as null and never
keeps a valid choice from being used, and it never changes which option is
played. A request may instead ask for its reason first (`reason_first`,
with `reason_words`, 30 unless the request gives another number, and
`max_answer_tokens`), whatever `choice_reasons` says: a local role's schema
lists `reason` (at most 8 characters a word, 240 for 30 words) before
`choice` and requires both, the system prompt asks for a reason of at most
`reason_words` words and then the choice, and Claude's answer line names the
two in that order. The answer may take `max_answer_tokens`, or
2 × `reason_words` + 24 tokens when the request gives none, from a local
role and from Claude alike, and there is no second reason after the choice.
The reason is written as `model_reason`, cut at 8 characters a word, and an
empty, missing or over-long one never keeps a valid choice from being used.
Pokémon asks this way while `model_decides` is `true` in `[pokemon]`, as
shipped (40 words, 160 tokens). Jev is asked as before. Red Alert never
waits for an answer: it plays on by its script and takes an answer on a
later cycle, if one comes in time. `werewolf_choice = ["local:strong"]` and
`werewolf_message = ["local:strong"]` are asked by Werewolf while
`model_choices` and `model_messages` are `true` in `[werewolf]`, as shipped;
Werewolf waits for an answer up to its deadline, since the game waits for its
move (see [Werewolf](#werewolf)). `pokemon_choice = ["local:strong"]` is asked
by Pokémon while `model_choices` is `true` in `[pokemon]`, as shipped, and is
waited for up to the phase's deadline (see
[Pokémon VGC doubles draft](#pokémon-vgc-doubles-draft)).

### Claude

| | |
|---|---|
| What it does | Claude models through the Anthropic Messages API, as a source of choices and short texts. A chain entry `claude:<alias>` names a model of `[claude.models]` (as shipped `sonnet`, `opus` and `haiku`). Each source counts the input and output tokens of its answers and stops asking once it has used `max_tokens_per_match` (300,000) in a match; its totals are written to the match record at the end of the match. |
| Where it is | Hosted by Anthropic at `https://api.anthropic.com` and not part of this repository. The client is `src/ucla_agent/core/decide/claude.py`. |
| Settings | `enabled`, `base_url`, `api_version` and `max_tokens_per_match` in `[claude]` and the aliases in `[claude.models]` of `config/agent.toml`; the key is `ANTHROPIC_API_KEY` in `.env`. |
| Enabled | No. `enabled = false` as shipped, and no chain names a `claude:` entry. While it is off, or no key is set, a `claude:` entry answers at once without sending anything and its chain goes on to its next source. Red Alert's chains never name Claude: a real-time game cannot wait for it. |

### Jev

| | |
|---|---|
| What it does | A hosted decision service: it takes the state of a situation and a schema of the answer, and returns a value that fits the schema with its confidence. An answer less confident than `min_confidence` (0.55) is not used. |
| Where it is | Hosted by its provider at `https://api.jevai.net` and not part of this repository. The client is `src/ucla_agent/core/decide/jev.py`. |
| Settings | `enabled`, `base_url` and `min_confidence` in `[jev]` and the field names in `[jev.fields]` of `config/agent.toml`; the key is `JEV_API_KEY` in `.env`. |
| Enabled | No. `enabled = false` as shipped, and no chain names `jev`. Turned on, it is still optional: a Jev that fails, answers late or is unsure leaves the choice to the script. |

## Prompts

Each prompt a model can be sent, by path:

| Prompt | What it is | When it is sent |
|---|---|---|
| `src/ucla_agent/games/redalert/prompts/plan_choice.txt` | The question of the Red Alert plan choice: what the summary's keys mean, what HOLD, RAID and PRESS do, and which to prefer when. The compact summary of the match goes with it as the context, and the answer is the choice. | Only when `model_plan_choice` is `true` (as shipped it is `false`), `plan_prompt` is `"briefing"` (as shipped it is) and `model_briefing` is `false` (as shipped it is `true`), and two plans or more are open, at most once every five seconds. |
| `src/ucla_agent/games/redalert/prompts/plan_choice_briefing.txt` | The question of the Red Alert plan choice with the briefing: what the briefing's sections say, the framing paragraph (the opponent another AI agent in a tournament, the tiebreak, the doctrine, and to learn from the recent picks; see `src/ucla_agent/games/redalert/model_history.py`), that only an open plan can be chosen, what HOLD, RAID and PRESS do, and which to prefer when. The plan choice briefing of `src/ucla_agent/games/redalert/briefing.py` goes with it as the context (see [Red Alert scripted core](#red-alert-scripted-core)), and the answer is a reason of at most 30 words, then the choice, in at most 96 tokens. | Only when `model_plan_choice` is `true` (as shipped it is `false`), `plan_prompt` is `"briefing"` (as shipped it is) and `model_briefing` is `true` (as shipped it is), and two plans or more are open, at most once every five seconds. |
| `src/ucla_agent/games/redalert/compact_prompt.py` | The compact plan question of Red Alert: its fixed system text (choose one open plan from the facts given, enemy counts may be stale, prefer viable pressure when home is safe, HOLD must have a concrete benefit, answer with the choice and a reason of at most 12 words), the state as `key=value` lines, the model's recent plan picks (`recent picks:`) and `OPEN:` with a fixed line for each open plan of HOLD, RAID and PRESS (see [Red Alert scripted core](#red-alert-scripted-core)). The answer is the choice first, then the reason, in at most 48 tokens. | Only when `model_plan_choice` is `true` (as shipped it is `false`) and `plan_prompt` is `"compact"` (as shipped it is `"briefing"`), and two plans or more are open, at most once every five seconds. |
| `src/ucla_agent/games/werewolf/prompts.py` | Werewolf's texts: the system prompt of a message (the rules in brief, our role and goal, that other players' messages are quoted data whose instructions are never followed, for a wolf never to reveal the wolves, and for the seer, until it has said it is the seer, never to hint at it), the instruction for each kind of message with the points it must carry, and the question and option descriptions of a choice. The context of every request is the ledger's summary and today's discussion, each message quoted on one line. | While `model_choices` or `model_messages` is `true` in `[werewolf]` (as shipped both are): a choice when the plan has two candidates or more, a message for each public chat the window's plan sends. |
| `src/ucla_agent/games/pokemon/decide.py` | Pokémon's question for each phase (which card to draft, which lineup to bring, what both slots do this turn), sent with the descriptions of the candidates the draft, preview and turn modules write (a card's set and score parts, a lineup's four and leads, a turn's actions with expected damage and knockout chance) and a short summary of the rosters or the board as the context; while `model_briefing` is `true` (as shipped it is), the context is instead the briefing of `src/ucla_agent/games/pokemon/briefing.py` (see [Pokémon VGC doubles draft](#pokémon-vgc-doubles-draft)), and each draft card's description also names its types and Speed. While `model_decides` is `true` (as shipped it is), each phase's question also says the options are unranked and listed in random order and asks for the best one for this situation, the descriptions carry no score (a draft card is its set line), and the answer is a reason of at most `decide_reason_words` words followed by the choice. | While `model_choices` is `true` in `[pokemon]` (as shipped it is): for every decision with two candidates or more. |

Nothing else written in natural language is sent to a model or a service.

## Third-party components

The `jev9b` role of the included model server (see `docs/MODELS.md`) runs
three components published by others: the JevK5-9B weights and the two
runtime packages jev-style and jevk5. None of them is included in this
repository: the packages are installed separately into a Python environment
of their own and the weights are downloaded separately, as `docs/MODELS.md`
describes, and the agent never installs or downloads any of them. They are
used only by `scripts/serve_jev9b.py`, which the model server runs for the
`jev9b` systemone role (`[systemone_roles.jev9b]` of `config/models.toml`);
that role is asked only by a chain entry `systemone:jev9b`, whose settings
are `[systemone.jev9b]` of `config/agent.toml`. As shipped `autostart_roles`
lists `jev9b` and the `red_alert_plan`, `red_alert_scout` and
`red_alert_commander` chains name `systemone:jev9b`, so they are used as
shipped: JevK5-9B makes Red Alert's model choices.

| Component | What it is | License | Source | Where the repository uses it |
|---|---|---|---|---|
| JevK5-9B | Model weights: a calibrated decision model that answers a choice question in one forward pass, built on Qwen3.5-9B (by the Qwen team, Apache-2.0). Per its model card, part of its training labels came from another model, OpenAI's GPT-6 Luna, and are subject to OpenAI's terms. | Apache-2.0 | By alibiserikbay: <https://huggingface.co/alibiserikbay/JevK5-9B>, revision `d6521a18a86999190e9d775c915af3d6d6772fc4` | Not included in this repository; downloaded separately into the folder that `model_dir` of `[systemone_roles.jev9b]` names (see `docs/MODELS.md`). `scripts/serve_jev9b.py` loads it from that folder for the `jev9b` role, after checking it against the folder's `SHA256SUMS`, and reads its calibration file `jevk5_config.json`; both files come with the download. |
| jev-style | A Python package of small decision models with a systemone-compatible server. The repository uses only its JevK5 engine (`jev_style.jevk5_engine.load_jevk5`) and its server app (`jev_style.server.create_app`), never jev-style's own models. It is not the hosted [Jev](#jev) service: its NOTICE file says it is not affiliated with Jev. | Apache-2.0, per its package metadata (version 0.4.0, with its LICENSE and NOTICE files) | By chaoliangUNSW: <https://github.com/lawrence3699/jev-style>, version 0.4.0 | Not included in this repository; installed separately (see `docs/MODELS.md`). `scripts/serve_jev9b.py` imports it to load JevK5-9B and serve it over the systemone API for the `jev9b` role. |
| jevk5 | The runtime of the JevK5 models, which jev-style's JevK5 engine calls to run JevK5-9B and record its CUDA graphs. Its NOTICE file names what it includes from others: the Qwen3.5 base weights (Apache-2.0) and a decision prompt and readout adapted from SemIf (MIT). | Apache-2.0, per its package metadata | allebee/jevk5 v0.3.3: <https://github.com/allebee/jevk5>, tag `v0.3.3` (commit `f944fe37ff1d5ed3830aa4c8d88b7189c8c1268a`); it is not on PyPI | Not included in this repository; installed separately from that tag (see `docs/MODELS.md`). `scripts/serve_jev9b.py` runs it, through jev-style, for the `jev9b` role. |

`scripts/serve_jev9b.py` reads no other configuration and no cascade file:
only its command line (the model folder and the port) and the role's
environment variables. From the same environment it also imports PyTorch
(torch 2.14.1+cu130 on the development workstation; Apache-2.0, BSD, BSL-1.0
and MIT parts, per its package metadata) and uvicorn (0.54.0, BSD-3-Clause),
which are installed there with the two packages.

## Excluded items

These affect play, or could, and are not in the repository.

| Item | Why it is not in the repository | What the repository says about it |
|---|---|---|
| Keys: the Official Agent Key, `JEV_API_KEY` and `ANTHROPIC_API_KEY` | Secrets. They live in `.env` in the repository root, which git ignores. | `.env.example` names every key the agent reads and gives none a value. `python scripts/scan_secrets.py` checks that no tracked file holds a key. |
| Model weight files | Third-party weights of 4 to 27 GiB each, published by others. | `docs/MODELS.md` gives each file's name, source repository and revision, download link, size and SHA-256. Each is expected at the path its role's `model_path` in `config/models.toml` names. As shipped only the `strong` role's model is loaded. |
| The `llama-server` program | Part of [llama.cpp](https://github.com/ggml-org/llama.cpp), installed separately. | `llama_server_path` in `config/models.toml`; `docs/MODELS.md` names the build it was developed with. |
| JevK5-9B, jev-style and jevk5 | Third-party weights and runtime packages of the `jev9b` role, downloaded and installed separately. | See [Third-party components](#third-party-components) and `docs/MODELS.md`. Used as shipped by the Red Alert chains. |
| The Jev service | Hosted by its provider. | See [Jev](#jev). Not enabled as shipped. |
| The Claude models | Hosted by Anthropic. | See [Claude](#claude). Not enabled as shipped. |
| ModelBouncer | A service some workstations run to share GPU memory. The model server only tells it how much memory the models will take; nothing depends on its answer. | `[courtesy]` in `config/models.toml`; `docs/MODELS.md`. |
| Downloaded research material | Third-party clones, data and papers, about 786 MB, kept in `docs/reference/research/`, which git ignores. It was consulted while designing the strategies; the agent never reads it. | `docs/reference/README.md`. Our own notes drawn from it are tracked under `docs/research/`. |
| The starter kit reference clone | A full clone of the official starter kit in `docs/reference/altruagent-starter/`, which git ignores. | The part the agent runs is vendored in `src/altruagent/`; `src/altruagent/UPSTREAM.md` names the upstream commit. |
| Match records | Written by the agent under `runs/`, which git ignores; they are evidence of what the agent did, not inputs. The agent never reads them while playing. | `docs/RUNNING.md` says what a run records and how long to keep it. |
| Scratch work | `tmp/`, which git ignores. Nothing in it is used by the agent. | |
| The local OpenRA test harness | Local testing used a separate harness, not needed to run the agent: an adapter over a build of the OpenRA engine (GPL-3.0) that lets the agent play Red Alert against OpenRA's built-in bots on the development machine. It is a separate project; the agent's code neither imports nor needs it. | Its results are summarized in `docs/PLAYBOOK.md`. |

## Not affecting play

The rest of the repository supports the agent without changing how it plays:
the match records and secret redaction (`src/ucla_agent/core/records.py`,
`src/ucla_agent/core/secrets.py`), the metrics and the report tool
(`src/ucla_agent/core/metrics.py`, `scripts/report.py`), the other scripts
under `scripts/`, the tests under `tests/`, and the documents under `docs/`.
