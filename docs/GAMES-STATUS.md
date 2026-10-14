# Games status

The tournament runs four games. This page says, for each one, whether the
agent is ready to be entered. It is updated whenever a game's status changes.

**A person registers the agent for a game's tournament only when that game is
`ready`.** Registering for a game that is not `ready` means forfeiting or
losing matches the agent cannot play.

| Game | Platform game type | Status | Decision-maker |
|---|---|---|---|
| Red Alert | `red_alert` | `in progress` | registered: `ucla_agent.games.redalert`, the scripted core with the local JevK5-9B model (`jev9b`) as plan chooser and commander under the script's checks, the script's choice played when no answer comes in time or the 9B is down (see [COMPONENTS.md](COMPONENTS.md#red-alert-scripted-core)) |
| Werewolf | `werewolf` | `ready` | registered: `ucla_agent.games.werewolf`, the role plans with model choices and chat (see [COMPONENTS.md](COMPONENTS.md#werewolf)) |
| Pokémon VGC doubles draft | `pokemon_vgc_doubles_draft` | `ready` | registered: `ucla_agent.games.pokemon`, the scored draft, team preview and doubles turn policies with model picks (see [COMPONENTS.md](COMPONENTS.md#pokémon-vgc-doubles-draft)) |
| Honor of Kings | `honor_of_kings` | `not started` | none (the platform does not offer this game yet) |

Werewolf evidence: platform test match session
`b21dd274-6124-4355-ac6a-4758bfaf5d6a`, played 2026-10-09 (UTC). All 7 seats were
"Mine (self-hosted)" and played by one `python -m agent --match` process,
unattended, to completion (`termination_reason` `completed`, villagers won). The
seats covered every role the game assigns: wolf (seats 0 and 2), seer (seat 3)
and villager (seats 1, 4, 5 and 6). None of the 7 match records
`runs/20261009/b21dd274-6124-4355-ac6a-4758bfaf5d6a__<seat>/` has an `error`
event of type `unhandled`, and `runs/manual-interventions.md` has no entry. The
local model server was not running, so every seat played its scripted choices
and template messages. Offline results: [WEREWOLF-BENCHMARKS.md](WEREWOLF-BENCHMARKS.md).

Pokémon evidence: platform test match session
`b118dda0-3b13-4834-9bf8-d9b621c0df63`, played 2026-10-09 (UTC), both seats
"Mine (self-hosted)" played by one `python -m agent --match` process,
unattended, to completion (`termination_reason` `completed`, one seat won and
one lost). Each seat made its draft picks, team preview and battle turns with
0 parse degradations, and neither of the two match records
`runs/20261009/b118dda0-3b13-4834-9bf8-d9b621c0df63__<seat>/` has an `error`
event. The local model server was not running, so every model pick fell back
to the script's choice. Local practice results: [POKEMON-BENCHMARKS.md](POKEMON-BENCHMARKS.md).

## What the statuses mean

- `not started`: no decision-maker for the game is registered in
  `ucla_agent.core.registry.GAME_FACTORIES`.
- `in progress`: a decision-maker is registered, but the completion criteria
  below have not all been met.
- `ready`: every completion criterion below has been met and the evidence is
  in the match records under `runs/`.

## Completion criteria for `ready`

A game is `ready` only when real platform test matches have been played to
completion unattended on every faction or role and every map the game can
assign, with no unhandled errors in the match records.

Spelled out:

1. **Real platform test matches.** The matches are test matches on the
   AltruAgent platform, started with `python -m agent --match` as described in
   [RUNNING.md](RUNNING.md). Offline tests and fake games do not count.
   Testing closes at 11:59 p.m. Pacific on 2026-10-13, and switching the
   agent back to Self Hosted closes with it, so platform test matches must be
   played before then: a game not `ready` by that time cannot become `ready`.
2. **Played to completion.** Each match ends with a result from the platform,
   not with the agent stopping, crashing or being stopped. A test match that
   fails for a server problem (the game server crashed, restarted or lost the
   game, or the game could not be set up or started) ends with no result and
   does not count: play another. A tournament game that fails for a server
   problem is replayed and never scored.
3. **Unattended.** From the start of each match to its result, nobody touched
   the agent, its configuration, its models or its machine, and
   `runs/manual-interventions.md` has no entry for the match.
4. **Every faction or role and every map the game can assign.** The set of
   completed matches covers each side the platform can give the agent and
   each map it can pick:
   - Red Alert: both factions (Allies and Soviets), each on every map in the
     platform's pool.
   - Werewolf: every role the platform can assign.
   - Pokémon VGC doubles draft: the game assigns no faction, role or map, so
     complete matches (draft, team preview and battle) are enough.
5. **No unhandled errors.** The match records of those matches contain no
   `error` event of type `unhandled`.

When a game meets all five, change its status in the table to `ready` in the
same commit that records where the evidence is (the dates and session ids of
the qualifying matches).

## A match of a game that is not registered

If the agent is assigned a match of a game with no registered decision-maker,
it does not crash and does not resign. It plays the first legal action each
time it is asked for a move (or waits, when the game lists none), closes every
messaging round, and writes one `unsupported_game` event to the match record.
That keeps a mistaken assignment from stopping the agent's other matches; it
is not competitive play, which is why registration waits for `ready`.
