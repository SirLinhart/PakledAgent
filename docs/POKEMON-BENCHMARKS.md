# Pokémon benchmark record

Results of the local Pokémon practice benchmark (`scripts/pokemon_bench.py`,
see [RUNNING.md](RUNNING.md), "Pokémon practice"). The matches are real
battles on a locally run Pokémon Showdown (`tools/pokemon_sim`), after the
platform's draft (an 18-card pool from `config/pokemon/practice_cards.json`)
and team preview. The match records stay under
`runs/practice/pokemon/<label>/`; only the figures are copied here.

## Measured acceptance, 2026-10-09

Our seat: `PokemonAgent` with its model choices on (the local `strong` role,
Qwen3.8 27B, choosing among the script's top 4). 50 matches per opponent,
seeds 5000 to 5049, our seat drafting first on even seeds.

| label | opponent | won | fallbacks | parse degradations | unhandled errors | slowest decision: draft / preview / turn |
|---|---|---|---|---|---|---|
| pk-random-50 | `random` (a random legal choice in every phase) | 50/50 | 0 | 0 | 0 | 420 / 399 / 430 ms |
| pk-first-50 | `first` (the starter smoke agent's first legal option) | 44/50 | 0 | 0 | 0 | 405 / 371 / 395 ms |

The pass marks were 40/50 against `random`, 30/50 against `first`, no
unhandled errors, no parse degradations, and the slowest decision at most
6500 ms in the draft and 21000 ms in team preview and battle. All were met.

What the figures do not say:

- Both opponents are weak. They show that the agent plays every phase legally
  and beats simple play; they do not measure it against another contestant's
  agent.
- The observation shapes of the harness are taken from the platform's
  guide and have not yet been checked against a real platform match.
- Earlier smoke runs with models off gave similar results: 20/20 against
  `random` and 18/20 against `first`. So the model's share in these results is
  not separated out.

## Briefing and deciding measured, 2026-10-11

Four arms of our seat against three opponents, 30 matches per label on seeds
7000 to 7029, our seat drafting first on even seeds: 12 labels,
`pk-<arm>-<opponent>-30`. Every label played the same seeds, so all arms
drafted from the same pools. The model arms asked the local `strong` role on
the benchmark's own model server, `http://127.0.0.1:11600`, never the live
agent's port 11500. Each label was run and read with

```
python scripts/pokemon_bench.py --label pk-<arm>-<opponent>-30 --opponent <opponent> --games 30 --seed-base 7000 --models <arm>
python scripts/report.py --pokemon-bench runs/practice/pokemon/pk-<arm>-<opponent>-30
python scripts/report.py --pokemon-bench-compare runs/practice/pokemon/pk-<arm>-<opponent>-30 runs/practice/pokemon/pk-off-<opponent>-30
```

and every figure below is copied from what `scripts/report.py` printed.

What each arm ran, as each label's `summary.json` records our seat's
settings:

- `off`: `model_choices` and `model_briefing` false, so every draft pick,
  lineup and turn was the script's best, with no model server.
- `on`: `model_choices` true and `model_briefing` false, so the model chose
  among the script's best `top_k` (4), ranked and scored, reading the
  one-line summary of the rosters or the board.
- `brief`: `model_choices` and `model_briefing` true, so the model chose
  among the same ranked 4, reading the full briefing.
- `decide`: `model_choices`, `model_briefing` and `model_decides` true and
  `decide_top_k` 6, so the model read the full briefing, was offered the
  script's best 6 without their scores in a shuffled order, and wrote a
  reason before its choice.

The 9 labels of `off`, `on` and `brief` were played before the
`model_decides` switch existed, so their `summary.json` has no
`model_decides` field. Their model chose among the ranked 4, as
`model_decides` false plays now. The 3 `decide` labels record
`model_decides` true and `decide_top_k` 6.

The opponents: `random` makes a seeded random legal choice in every phase,
`first` takes the first legal option everywhere, and `self` is the Pokémon
agent itself with `model_choices` off, the script alone.

| label | arm | opponent | games | won | fallbacks | parse degradations | unhandled errors | model answers | differed from the script | differed share |
|---|---|---|---|---|---|---|---|---|---|---|
| `pk-off-random-30` | `off` | `random` | 30 | 29 | 0 | 0 | 0 | 0 | 0 | - |
| `pk-off-first-30` | `off` | `first` | 30 | 27 | 0 | 0 | 0 | 0 | 0 | - |
| `pk-off-self-30` | `off` | `self` | 30 | 14 | 0 | 0 | 0 | 0 | 0 | - |
| `pk-on-random-30` | `on` | `random` | 30 | 29 | 0 | 0 | 0 | 340 | 5 | 0.01 |
| `pk-on-first-30` | `on` | `first` | 30 | 26 | 0 | 0 | 0 | 364 | 5 | 0.01 |
| `pk-on-self-30` | `on` | `self` | 30 | 13 | 0 | 0 | 0 | 372 | 7 | 0.02 |
| `pk-brief-random-30` | `brief` | `random` | 30 | 29 | 0 | 0 | 0 | 344 | 4 | 0.01 |
| `pk-brief-first-30` | `brief` | `first` | 30 | 28 | 0 | 0 | 0 | 365 | 4 | 0.01 |
| `pk-brief-self-30` | `brief` | `self` | 30 | 13 | 0 | 0 | 0 | 376 | 3 | 0.01 |
| `pk-decide-random-30` | `decide` | `random` | 30 | 29 | 0 | 0 | 0 | 366 | 214 | 0.58 |
| `pk-decide-first-30` | `decide` | `first` | 30 | 29 | 0 | 0 | 0 | 410 | 230 | 0.56 |
| `pk-decide-self-30` | `decide` | `self` | 30 | 17 | 0 | 0 | 0 | 377 | 223 | 0.59 |

`model answers` counts the decisions a model answered, `differed from the
script` those in which it chose another candidate than the script's best, and
`differed share` the second over the first (`-` where no model was asked).

How long our seat took to decide, in milliseconds, the median and the
slowest decision of each phase (`team_preview` and `moving` in the report):

| decision times of | median draft | median team preview | median turn | slowest draft | slowest team preview | slowest turn |
|---|---|---|---|---|---|---|
| `pk-off-random-30` | 2.49 | 1.35 | 1.21 | 21.23 | 2.39 | 2.7 |
| `pk-off-first-30` | 2.57 | 1.23 | 1.14 | 21.59 | 2 | 2.44 |
| `pk-off-self-30` | 2.42 | 0.74 | 0.85 | 22.17 | 1.51 | 2.33 |
| `pk-on-random-30` | 1230.07 | 1173.57 | 1361.1 | 2832.37 | 2087.64 | 3000.59 |
| `pk-on-first-30` | 775.64 | 698.97 | 821.9 | 3148.49 | 1911.14 | 3024.45 |
| `pk-on-self-30` | 747.27 | 698.63 | 811.43 | 994.04 | 822.29 | 1037.13 |
| `pk-brief-random-30` | 1334.85 | 1458.07 | 1254.29 | 1573.2 | 1708.41 | 1517.91 |
| `pk-brief-first-30` | 1333.78 | 1476.51 | 1227.66 | 1545.72 | 1677.92 | 1619.34 |
| `pk-brief-self-30` | 1332.02 | 1469.82 | 1208.85 | 2426.5 | 2326.68 | 3185.71 |
| `pk-decide-random-30` | 2176.05 | 2509.39 | 2249.21 | 3356.57 | 3364.89 | 3458.08 |
| `pk-decide-first-30` | 2342.29 | 2742.62 | 2430.9 | 4356.99 | 3622.5 | 4424.91 |
| `pk-decide-self-30` | 2181.77 | 2578.53 | 2145.87 | 3051.69 | 3312.16 | 4305.57 |

The deadline each phase's decisions had in these matches:

| phase | deadline ms | from |
|---|---|---|
| draft | 6000 | `draft_deadline_ms` |
| team preview | 5000 | `no_clock_deadline_ms`: the local Showdown reports no clock, so a lineup waits on the model at most this, under `preview_deadline_ms` (20000) |
| turn | 5000 | `no_clock_deadline_ms`, under `turn_deadline_ms` (20000), for the same reason |

The wins of each model arm against `off`, with the two-sided Fisher exact
p-value `--pokemon-bench-compare` printed for the two labels:

| opponent | off won | brief won | brief against off, Fisher p | decide won | decide against off, Fisher p |
|---|---|---|---|---|---|
| `random` | 29 | 29 | 1.000 | 29 | 1.000 |
| `first` | 27 | 28 | 1.000 | 29 | 0.612 |
| `self` | 14 | 13 | 1.000 | 17 | 0.606 |

`on` against `off` is for information only: 29 and 29 against `random`, 26
and 27 against `first`, 13 and 14 against `self`.

### The decision

The rule for each switch: it ships true when its arm wins at least as many
matches as `off` against `self` and against `first`, with 0 unhandled errors
and every decision within its deadline; otherwise it ships false.

| switch | arm | rule result | shipped | why |
|---|---|---|---|---|
| `model_briefing` | `brief` | `false` | `true` | The rule fails by one match: `brief` won 13 against `self` to `off`'s 14 (28 to 27 against `first`, 0 unhandled errors, every decision within its deadline). The user decided on 2026-10-11 ("1. yes") that it ships true, overriding the rule. |
| `model_decides` | `decide` | `true` | `true` | By its rule: `decide` won 17 against `self` to `off`'s 14 and 29 against `first` to `off`'s 27, with 0 unhandled errors, and its slowest decisions, 4356.99 ms in the draft and 4424.91 ms in a turn, were within their deadlines. |

So `[pokemon]` in `config/agent.toml` ships `model_briefing = true` and
`model_decides = true`: the model reads the full briefing and chooses among
the script's best 6, unscored and shuffled, writing its reason first.

What the figures do not say:

- None of the Fisher p-values above is significant: the smallest is 0.606.
  With 30 matches a label only a large difference would be, so the model's
  effect on the result is not shown, either way.
- With the ranked 4 (`on` and `brief`), the model's choice differed from the
  script's best in a share of 0.01 to 0.02 of its answers; offered 6 unscored
  and shuffled (`decide`), in a share of 0.56 to 0.59.
- `random` and `first` are weak opponents, and `self` is our own script.
- A `decide` decision takes longer than a `brief` one, medians of 2145.87
  to 2742.62 ms against 1208.85 to 1476.51 ms, since its answer writes the
  reason before the choice.
- The platform test match with the shipped settings is still to be played.

## Maximin measured, 2026-10-12

The `[maximin]` switch of `config/pokemon/tuning.toml` ranks every phase by
its worst case against the opponent's best reply: a turn's best 8 joint
actions against the opponent's best 8 through a one-turn model, team preview's
90 lineups against the opponent's 90, and each draft card one pick ahead
(see [COMPONENTS.md](COMPONENTS.md), "Pokémon VGC doubles draft"). Two arms
of our seat against two opponents, 30 matches per label on seeds 9000 to
9029, our seat drafting first on even seeds: 4 labels,
`pk-maximin-<arm>-<opponent>-30`. Every label played the same seeds, so both
arms drafted from the same pools. Both arms ran with `--models off`, so no
model was asked and no model server was started. Each label was run and read
with

```
PYTHONPATH=src python scripts/pokemon_bench.py --label pk-maximin-<arm>-<opponent>-30 --opponent <opponent> --games 30 --seed-base 9000 --models off --maximin <arm>
python scripts/report.py --pokemon-bench runs/practice/pokemon/pk-maximin-<arm>-<opponent>-30
python scripts/report.py --pokemon-bench-compare runs/practice/pokemon/pk-maximin-on-<opponent>-30 runs/practice/pokemon/pk-maximin-off-<opponent>-30
```

and every figure below is copied from what `scripts/report.py` printed.

What each arm ran, as each label's `summary.json` records our seat's
settings: `model_choices`, `model_briefing` and `model_decides` false in
both; `maximin` true for `on` and false for `off`. The tracked tuning file
had `[maximin] enabled = false` while the labels were played, so `self`, the
Pokémon agent itself with the tracked settings and `model_choices` off, was
the script's own ranking: today's play. `first` takes the first legal option
everywhere. Every `pokemon_decision` event of the `on` labels records
`ranking` `maximin` (180 draft picks, 30 lineups and 257 turns against
`self`; 180, 30 and 208 against `first`): no maximin ranking ran out of time.

| label | arm | opponent | games | won | fallbacks | parse degradations | unhandled errors |
|---|---|---|---|---|---|---|---|
| `pk-maximin-on-self-30` | `on` | `self` | 30 | 18 | 0 | 0 | 0 |
| `pk-maximin-off-self-30` | `off` | `self` | 30 | 14 | 0 | 0 | 0 |
| `pk-maximin-on-first-30` | `on` | `first` | 30 | 29 | 0 | 0 | 0 |
| `pk-maximin-off-first-30` | `off` | `first` | 30 | 29 | 0 | 0 | 0 |

How long our seat took to decide, in milliseconds, the median and the
slowest decision of each phase (`team_preview` and `moving` in the report):

| decision times of | median draft | median team preview | median turn | slowest draft | slowest team preview | slowest turn |
|---|---|---|---|---|---|---|
| `pk-maximin-on-self-30` | 32.06 | 2.86 | 2.54 | 85.34 | 4.86 | 7.2 |
| `pk-maximin-off-self-30` | 2.64 | 0.72 | 0.95 | 27.97 | 1.68 | 3.39 |
| `pk-maximin-on-first-30` | 32.04 | 3.58 | 3.8 | 87.84 | 5.16 | 37.44 |
| `pk-maximin-off-first-30` | 2.65 | 1.27 | 1.23 | 41.63 | 2.88 | 2.94 |

The deadline each phase's decisions had in these matches:

| phase | deadline ms | from |
|---|---|---|
| draft | 6000 | `draft_deadline_ms` |
| team preview | 5000 | `no_clock_deadline_ms`: the local Showdown reports no clock, under `preview_deadline_ms` (20000) |
| turn | 5000 | `no_clock_deadline_ms`, under `turn_deadline_ms` (20000); a turn's maximin evaluation also stops at 300 ms of its own |

The wins of the `on` arm against `off`, with the two-sided Fisher exact
p-value `--pokemon-bench-compare` printed for the two labels:

| opponent | off won | on won | on against off, Fisher p |
|---|---|---|---|
| `self` | 14 | 18 | 0.438 |
| `first` | 29 | 29 | 1.000 |

### The maximin decision

The rule: `[maximin] enabled` ships true when the `on` arm wins at least as
many matches as the `off` arm against `self` and against `first`, with 0
unhandled errors and every decision within its deadline; otherwise it ships
false.

| switch | arm | rule result | shipped | why |
|---|---|---|---|---|
| `[maximin] enabled` | `on` | `true` | `true` | By its rule: `on` won 18 against `self` to `off`'s 14 and 29 against `first` to `off`'s 29, with 0 unhandled errors, and its slowest decisions, 87.84 ms in the draft, 5.16 ms in team preview and 37.44 ms in a turn, were within their deadlines. |

So `config/pokemon/tuning.toml` ships `[maximin] enabled = true`, with
`our_top = 8`, `their_top = 8` and `draft_lookahead = true`. From now on the
practice opponent `self` plays the tracked tuning, so it plays maximin too;
our seat plays maximin only with `--maximin on`, whatever the file ships.

What the figures do not say:

- Neither Fisher p-value is significant: 0.438 against `self` and 1.000
  against `first`. With 30 matches a label, four more wins out of 30 is
  within chance, so maximin's effect on the result is not shown, either way.
- `first` is a weak opponent: both arms beat it 29 times in 30.
- Matches with maximin lasted longer: a mean of 6.9 turns against `self` to
  `off`'s 4.6, and 6.3 against `first` to 4.9; its worst case favors Protect
  and safer lines over trades.
- A maximin draft pick takes about 30 ms more than the script's, the lookahead
  scoring every card left from the opponent's side once per offered card.
- The platform test match with maximin is still to be played, and the model
  arms (`brief`, `decide`) were not measured with maximin on.
