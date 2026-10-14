# Werewolf benchmark record

Results of the local Werewolf practice benchmark (`scripts/werewolf_bench.py`,
see [RUNNING.md](RUNNING.md), "Werewolf practice"). The games stay under
`runs/practice/werewolf/<label>/`; only the figures are copied here.

## Seer-claim fixes, 2026-10-09

On the first real platform test table every villager voted out the honest
seer on day 1. Three causes were found and fixed. First, the role inference
made a lone seer claimant more likely a wolf than the seer. Second, the
village vote did not spare such a claimant. Third, "I suspect PlayerN" was not
read as an accusation.

### Self-play table

All six opponent seats are copies of the agent, as on a test table where
every seat is our own, with models off. 100 games per role, seeds 3000 to
3099, the same deals in both runs. "Before" is the iteration-4 seer-claim
weights (2.0/1.5/0.1) with `[village].spare_lone_claimant = false`, passed
with `--tuning`. The "suspect" wording and the quiet-seer check are code, so
they are on in both runs; that is the limit of this comparison.

| figure | before | after |
|---|---|---|
| games where the real seer claimed | 153 | 153 |
| ... seer voted out on its claim day | 93 | 0 |
| village side won (of 300) | 103 | 147 |
| our side won as villager | 32/100 | 42/100 (p 0.187) |
| our side won as seer | 17/100 | 46/100 (p < 0.001) |
| our side won as wolf | 46/100 | 41/100 (p 0.568) |

The wolf figure falls because, in self-play, our wolves now face a village
that believes its seer.

### Mixed table with claim-making opponents

Table: 2 `placeholder`, 2 `record_voter` and 2 `llm_player` opponents. The
`record_voter` opponents now claim seer truthfully when they find a wolf and
follow a lone claim. The baseline seat keeps its own message history. 30
games per role, seeds 4000 to 4029. The model arm was played in two chunks of
15 per role.

| role | baseline (`record_voter`) | agent, local `strong` model | Fisher p |
|---|---|---|---|
| villager | 11/30 | 10/30 | 1.000 |
| seer | 11/30 | 11/30 | 1.000 |
| wolf | 20/30 | 21/30 | 1.000 |

- No unhandled errors in 180 games.
- The `strong` model's choice answers: 0 malformed of 245, against 21 of 175
  before the answer limit went from 8 to 24 tokens.
- The goal's test, "at least as often as the baseline in each role", misses
  in villager by one game. At 30 games per role no difference is
  distinguishable either way.

## Mixed table, 2026-10-08

Table: 2 `placeholder`, 2 `record_voter` and 2 `llm_player` opponents (the
local `fast` role). 60 games per role per arm, seeds 2000 to 2059, our seat at
`seed % 7`, the same deals in every arm. Each arm was played in three chunks of
20 games per role (labels `ww-<arm>-mixed-c1` to `-c3`).

| role | baseline (`record_voter`) | agent, script only | agent, local `strong` model | Fisher p, model vs baseline |
|---|---|---|---|---|
| villager | 30/60 | 31/60 | 31/60 | 1.000 |
| seer | 30/60 | 32/60 | 32/60 | 0.855 |
| wolf | 28/60 | 34/60 | 34/60 | 0.361 |
| all | 88/180 | 97/180 | 97/180 | 0.399 |

No game of any arm had an unhandled error (540 games).

What the figures say:

- The agent's side won more often than the baseline's in each role, which is
  the test the iteration goal sets. No role's difference is statistically
  distinguishable from the baseline at 60 games.
- The model arm and the script arm had the same result in every game. The
  local `strong` model chose the script's own pick in 171 of 175 sampled
  decisions; 21 of those 175 choice requests ended without a usable answer
  and the script's pick was played. The practice opponents do not react to
  what is said in the chat, so the model's messages cannot change a result on
  this table: the value of the messages is not measured offline.
- The median per-game 95th percentile of the agent's decision time with the
  model was 531 ms.

## Scripted table, 2026-10-08

Table: 2 `placeholder`, 2 `abstainer` and 2 `record_voter` opponents, 200 games
per role, seeds 1000 to 1199, models off.

| role | baseline (`record_voter`) | agent, script only | Fisher p |
|---|---|---|---|
| villager | 41/200 | 36/200 | 0.612 |
| seer | 41/200 | 40/200 | 1.000 |
| wolf | 171/200 | 178/200 | 0.369 |

On this table the abstainers and the lowest-seat voters decide most games: the
village side loses about four games in five whoever sits in our seat.
