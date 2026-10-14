# Playbook

How the agent approaches the tournament's games: the guidance every game
follows, and one section per game with the strategy it plays. It describes
what the code in this repository does; where the code and this page disagree,
the code is right and this page is to be corrected.

## Guidance for every game

1. **Scripted code guarantees a legal, timely move.** Every decision the
   platform asks for is answered by code that runs in a few milliseconds, uses
   only the state in hand, and gives an answer the platform will accept. No
   model call, network request or file read stands between a state and the
   move made from it. A game whose rules are not scripted yet is not entered.
2. **Models make short choices and write language.** A model is asked only
   for what scripted code does badly: choosing among a few options the script
   has already prepared, and, in games that have them, writing messages. It
   answers with the id of an option, within a deadline. A model that is slow,
   wrong or unreachable is passed over and the script's own choice stands, so
   play never depends on one.
3. **Reliability and never missing a match come before cleverness.** A match
   the agent does not join is a certain loss. One it stops playing is still
   scored as played, with only the platform's timers moving for it: a default
   move after 120 seconds in Werewolf, a random one after 50 seconds in
   Pokémon until the player's 7-minute battle bank runs out and it forfeits,
   and nothing in Red Alert, where an idle side falls behind. Only a failure
   on the platform's side is replayed instead of scored. A weaker strategy
   played to the end does better than either. The agent is one process that is
   started once and left running, reconnects and retries by itself, plays
   each match in its own process, keeps no memory from one match to the
   next, and treats every failure inside a match as something to play on
   from. A change that adds strength but risks a crash is not made.
4. **Claims are checked in test matches.** A belief about the platform, the
   game or the opponent (a timing, a rule, how a fight goes) is a guess until
   a test match shows it. The match records under `runs/` and
   `scripts/report.py` are where that is read; a game is registered for a
   tournament only when real test matches meet the criteria in
   [GAMES-STATUS.md](GAMES-STATUS.md).

## Red Alert

Status: scripted core built; see [GAMES-STATUS.md](GAMES-STATUS.md) for
whether it is ready to be entered.

A match is real time, one against one, at most 20 minutes. It is won by
destroying every enemy building and MCV; a match that reaches the time limit
goes to the side with the better trade balance (value of what it destroyed
minus value of what it lost) and, only when that is even, to the side with
more standing assets. A draw earns nothing in the Swiss rounds. So the stance
is to be hard to kill early, out-trade the opponent all match, and finish only
when it is safe.

### The stance

The script switches the agent ships with are those of a watched game of
iteration 19 that it won: `runs/watch/20261012T091508Z` of the harness,
England against the Normal bot's Russia on the sidestep map, won by a trade
balance of +35,500 at the 20-minute limit, after earlier watched games with
growing parts of the same set on (two won, by +14,150 and +11,300, and one
lost, by 9,950). No benchmark measured the set, by the user's direction; the
switches and their earlier measured results are listed in
[COMPONENTS.md](COMPONENTS.md). That game also had the model choose the
plan, with the compact plan prompt, and the model's one plan decision was
HOLD; the agent ships with the model plan choice off, so the script makes
every choice.

- **Cheap early defense, bought against a threat.** A rush can arrive within
  about two minutes. A watch on the first five minutes tells a rush from the
  scout's and a watcher's sightings. The opening buys its defense against
  infantry (pillbox or flame tower) only when the watch tells a rush, cannot
  tell by two and a half minutes, or a threat is seen at the base, and its
  cover against armor (a gun turret where there is power, else a second
  defense) only against enemy armor seen near or a major threat; until then
  the money goes to the economy, and a lean home guard of three rifles forms.
  Base defense answers an attack in the same cycle it is seen and shelters
  the harvesters, a raid on a far harvester is answered by the main army
  alone, and the base's later defenses are bought only against a real
  threat.
  (`src/ucla_agent/games/redalert/opening.py`,
  `src/ucla_agent/games/redalert/rush_watch.py`,
  `src/ucla_agent/games/redalert/base_defense.py`)
- **An economy that keeps growing.** The build is power, barracks, two
  refineries, power, war factory, and nothing is weighed while it runs. From
  the opening on the base walks toward the nearest ore field out of reach,
  field after field; each field it reaches gets a refinery, one to a field
  before any gets a second, until 8 refineries stand with two harvesters
  each, and harvesters off a dry field are spread over the fields near them.
  A second construction yard, and later a third, goes to a safe new field
  with a short escort, its MCV bought first, and while a refinery, a war
  factory or the MCV is due the unit queues wait for the money. Upkeep keeps
  the power just above what the base drains, replaces what is lost, and
  refills the unit queues, keeping a reserve for buildings. An opening and a
  placement of the refineries that steer them toward their ore exist as
  switches but ship off: measured together, they won fewer games than this
  build.
  (`src/ucla_agent/games/redalert/upkeep.py`,
  `src/ucla_agent/games/redalert/creep.py`,
  `src/ucla_agent/games/redalert/expansion.py`)
- **An army of tanks.** Once a war factory stands, the army is vehicles but
  for a few rocket soldiers: light tanks for the Allies and flak trucks for
  the Soviets before the service depot, then medium or heavy tanks, with
  artillery or V2s once a radar stands. More war factories come sooner and
  keep the Vehicle queue filled. (`src/ucla_agent/games/redalert/composition.py`,
  `src/ucla_agent/games/redalert/tank_factories.py`)
- **Raids that can leave.** A small fast group (Allied light tanks or jeeps,
  Soviet grenadiers) is sent out under RAID and PRESS after the enemy's
  harvesters where no known defense covers them, by a way around the
  defenses, and after a refinery only when no harvester has been seen for a
  minute; RAID is chosen only while such a raid could set out now. It breaks
  off the moment the fight turns, the group is hurt, or it stands on covered
  ground. Raids stay small and occasional: against the built-in bots they
  kill little (England's 162 raids against Normal made 8 kills in 216 games),
  and a raid group kept out of the main army at all times, of light tanks or
  of APCs carrying rocket soldiers, cost the main army more than its raids
  returned and lost more games. (`src/ucla_agent/games/redalert/raids.py`)
- **Scouting kept fresh.** Two scouts are kept for the whole match, a
  rifleman each at first, then a jeep (Allies) or a flak truck (Soviets),
  replaced when lost but within a cap on what they cost. Each takes one of
  the script's missions, the place whose news is worth most for the time it
  takes: an ore field the enemy may expand to, the edge of the enemy's main,
  the way to our next expansion or the middle of the lane. Their ways keep
  wide of every danger seen and of the places a scout was hurt.
  (`src/ucla_agent/games/redalert/scout_slots.py`,
  `src/ucla_agent/games/redalert/scout_mission_planner.py`; `scout_slots` in
  `config/redalert/tuning.toml`)
- **Favorable fights only.** Every fight is decided on one estimate, the ratio
  of the group's strength to the enemy's around it, counting defenses twice
  and raising unseen enemies by a fifth. The army engages when clearly
  stronger and retreats when clearly weaker, goes forward only as a whole,
  and does not walk into defenses it cannot beat; with nothing in reach, its
  screened advance marches on. Once on the attack, it is not called home for
  a threat the guard and the defenses can answer. A badly damaged expensive
  tank pulls back on its own. (`src/ucla_agent/games/redalert/combat.py`,
  `src/ucla_agent/games/redalert/fight.py`)
- **No early attack into an army at home.** An attack early in the match
  into an opponent that has not committed its army meets that army and its
  defenses at home, and loses. So the early all-in attack (STRIKE) is never
  made, and before the ninth minute the army goes forward (PRESS) only once
  the enemy has committed its army: an attack on the base beaten off without
  losing a core building, or 1,500 worth of kills. The own army is weighed
  against the enemy's by the units that fight, never by the harvesters.
  (`src/ucla_agent/games/redalert/plans.py`, rules R5 and R7; the
  early-attack guard, `early_attack_guard` in `config/redalert/tuning.toml`)
- **Finish when clearly ahead.** The attack that ends the match (FINISH) comes
  only when the army is well above everything the enemy is known to have,
  or when the enemy has buildings left but no known defense and no army seen
  for a minute; it then keeps to one target, the enemy's production first,
  until that is gone, and hunts every remaining building and MCV.
  (`src/ucla_agent/games/redalert/plans.py`, rule R6)
- **Clock play under the tiebreak.** In the last minutes the plan follows the
  trade balance: a lead is preserved (PRESERVE: nothing is risked and
  defenses are added), an even balance is held, and a deficit is pressed. In
  the last two minutes the money left is spent on the dearest defenses and
  fighting units it can still buy, because money in the bank counts for
  nothing, and no building is ever sold. (`src/ucla_agent/games/redalert/clock.py`, rules R2 to R4)

Nine other changes to the base and army game were measured the same way, on
144 games each against the 97 won with none of them: each won fewer games,
or won more at a worse trade balance. Three ship off: the base walking from
ore field to ore field along the lane toward the enemy won 94, scouting by an
indirect route 89, and the army holding a forward point 92. The other six are
in the watched game's set and ship on all the same: a cap of two refineries
per field won 92 and an early second construction yard 85, harvesters moved
off a dry field won 99 but traded worse, more war factories sooner won 82,
and the rush watch with the opening's defenses bought only on a threat won 95,
and 7 fewer for Russia against the Rush bot. See
[BENCHMARKS.md](BENCHMARKS.md), "Iteration 13", and the switches in
[COMPONENTS.md](COMPONENTS.md).

Between those rules the script chooses among HOLD, RAID and PRESS, preferring
RAID when there is an undefended target, then PRESS when the units that fight
clearly outweigh the enemy's army. A plan, once chosen, is kept for at least
30 seconds so that the army is not sent out and called back with every small
change.

### What models may do

A model may make only that last choice, HOLD, RAID or PRESS, among the plans
the rules leave open, given a short summary of the match; it can never
override the opening, the clock rules, STRIKE or FINISH. A model may also be
allowed to send command shorthand, which is translated into the orders the
script itself could send and refused otherwise. As shipped both are switched
off and the scripted core plays every move. The winning watched game above
had the model choice on, with the compact plan prompt and a hold of 30
seconds on the plan the model chose; the hold ships on but does nothing while
the model choice is off. See [COMPONENTS.md](COMPONENTS.md).

### Known weaknesses

In local testing against OpenRA's built-in Rush and Normal bots, on a separate
harness not part of this repository, an early version of the agent won 1 of
15 recent games, out-produced in every game and out-fought in most. The
configuration shipped before the watched game's set was measured on 720 games
(`docs/BENCHMARKS.md`, "Iteration 12", arm A) and won 444 of them: as England
158 of 216 against Normal and 112 of 144 against Rush, as Russia 113 of 216
against Normal and 61 of 144 against Rush. The Soviets, against Rush above
all, were the weak side. The configuration that ships now, the watched
game's set, has been played only in the watched games and not benchmarked:
several of its switches each lost games when measured alone, and an earlier
main force of tanks lost with the other army switches. The built-in bots are
not the tournament's opponents, so neither a loss nor a win against them
settles how the agent fares on the platform.

### Sources

- `docs/research/red-alert-rush-vs-defense.md`: rush timings, the value of
  each defense, kill times and the tiebreak arithmetic, from the game's rule
  files.
- `docs/research/local-openra-feasibility.md`: how Red Alert was run locally
  against built-in bots.
- The platform's Red Alert rules: `docs/reference/altruagent/redalert.md`.

## Werewolf

Status: `ready`. The registered decision-maker,
`ucla_agent.games.werewolf.agent.WerewolfAgent`, plays every role: it keeps a
ledger of what our seat knows and a role inference over every way the wolves
and the seer can sit, votes, kills and investigates by its role's plan, and
chats. With the model server running, the local `strong` model may choose
among the plan's candidates and write the messages (see
[COMPONENTS.md](COMPONENTS.md#werewolf)). [GAMES-STATUS.md](GAMES-STATUS.md)
records the platform test match it was made `ready` on, and [RUNNING.md](RUNNING.md#werewolf-practice)
gives the practice benchmark and the steps of a seven-seat test table.

## Pokémon VGC doubles draft

Status: `ready`. The registered decision-maker,
`ucla_agent.games.pokemon.agent.PokemonAgent`, plays the draft, team preview
and every doubles turn with structured actions (see
[COMPONENTS.md](COMPONENTS.md#pokémon-vgc-doubles-draft)).
[GAMES-STATUS.md](GAMES-STATUS.md) records the platform test match it was made
`ready` on, played on 2026-10-09 (UTC).

- **Draft.** Every card seen in the pool is remembered with its full set, and
  so are both rosters: the opponent's sets are public, so the battle is
  played against exact sets. Each offered card is scored by its damage to and
  from the rest of the pool and the opponent's roster, its Speed, its support
  (Fake Out, Tailwind, Trick Room, redirection, Intimidate, Protect, spread
  moves), how it covers our roster's weaknesses, and how much the opponent
  would want it. The best is picked, with a one-sentence public reason.
- **Team preview.** All 90 ways to bring 4 of our 6 and lead with 2 are scored
  against the opponent's six by our damage to them minus theirs to us, with a
  bonus for leads that bring speed control or Fake Out.
- **Doubles turns.** Each slot's options are scored by a damage estimate over
  the shipped Gen 9 data (expected damage and knockout chance, spread moves
  counted on every foe and against our ally), Fake Out on the first turn out
  only, Protect under a likely knockout but never twice in a row, Tailwind and
  Trick Room when they turn the speed order our way, and switches into a
  resist; the best legal pair of slot choices is sent, never two switches
  into one Pokémon and never piling onto a foe one attack already knocks out.
- **Model picks.** With `model_choices` on in `[pokemon]` of
  `config/agent.toml` (as shipped), the local `strong` model may choose among
  the script's best six candidates of a decision, shown without the script's
  scores and in a shuffled order, reading the full briefing of the pool, the
  two sixes or the board, and writing a reason of at most 40 words before its
  choice (`model_briefing` and `model_decides`, both on as shipped; measured
  in [POKEMON-BENCHMARKS.md](POKEMON-BENCHMARKS.md)). It has 6 s for a draft
  pick and at most 20 s for a lineup or a turn; anything else plays the
  script's best. A lineup or a turn follows the decision's clock in the
  observation: the model never waits past the clock less 10 s, a turn spends
  at most a tenth of the 420 s battle bank and never takes it below 90 s, so
  with the bank at 90 s or less no model is asked, and before Showdown has
  reported the clock the wait is at most 5 s.
- **Never stuck.** A state that cannot be read is answered from the legal
  action's template alone, the starter smoke agent's way, and recorded as an
  `unexpected_state` error; every lineup and turn is checked against its
  template before it is sent.

The local practice harness plays whole matches on Pokémon Showdown against the
`random`, `first` and `self` opponents.

## Honor of Kings

Status: `not started`. The platform does not offer the game yet; what is known
is in `docs/shelved/honor-of-kings.md`.
