# Shelved: Honor of Kings agent

Shelved 2026-10-06. No decisions yet. This will break into several concepts once
the organizers publish how the game is played on the platform.

## Why it is shelved

The official rules (2026-10-06) name four tournament games: Pokémon Showdown,
Red Alert, Werewolf and Honor of Kings. The user wants to enter all four, with
Red Alert first. Honor of Kings cannot be designed yet because nothing about it
is published:

- The platform's agent docs have no Honor of Kings topic. On 2026-10-06 the doc
  index listed only: `04-tournaments`, `05-gameplay`, `06-messaging`,
  `07-errors`, `mcp`, `official-agent`, `pokemon`, `redalert`, `reference`,
  `werewolf`. Requests for likely names (`honor-of-kings`, `hok`, and others)
  returned `topic_not_found`.
- The official starter's `GAMES.md` says of Honor of Kings: "TODO — not yet
  available. No references to this game (under this or any other likely name)
  were found anywhere in the platform or starter source".
- The tournament agent guide names only Werewolf, Pokémon and Red Alert.

## What we know

- Honor of Kings is a real-time team battle game (a MOBA). The commercial game
  is five players against five, each controlling one hero.
- The rules give it the same prize share as the other games: 25% of the pool,
  split 50/30/20 for first to third.
- The rules say the final format for each game, including game version, mode,
  entry limits and the number of agents per entry, will be published before the
  submission deadline of 11:59 p.m. Pacific on 2026-10-13.
- The rules allow "multiple agents ... where the game format requires or
  expressly allows them", which may be how a team game is entered.

## Open questions to answer when it is picked up

1. Is it played through the same platform interface as the other games (seat
   grant, read state, send actions), or something different?
2. Is it real time like Red Alert, or stepped?
3. Does one entry control one hero or a whole team? Are teammates other
   entrants' agents?
4. What does the agent see, and what actions can it send?
5. Is there any messaging between teammates?
6. How are matches scored and how is the tournament structured?
7. Does the official starter gain an example agent for it?

## Likely overlap with work already planned

If it is real time, the Red Alert approach probably carries over: a fast
scripted loop that acts on every view of the game, a slower chooser on top, the
compact command shorthand, order bookkeeping, and the fallback when a decision
source is slow. The shared foundation (hand-off by game, logging, model server,
readiness check) applies to it unchanged.

## What to do meanwhile

- Re-check the platform doc index and the starter repository for an Honor of
  Kings section whenever the platform docs are refreshed.
- Do not register the agent for an Honor of Kings tournament until the agent
  can finish a match of it. The rules say one agent covers every game it
  enters, and an agent that cannot play loses its games.
