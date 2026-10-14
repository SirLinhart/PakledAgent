# Benchmark record

Every benchmark label a decision about the Red Alert agent was made on: the
iteration-2 references and each iteration-3 change, with the configuration
`config/redalert/tuning.toml` ships in the last row.

Each label is one run of the 72-game local benchmark: the 12 configurations
of `docs/RUNNING.md`, "Local benchmark", with N = 6 seeds each, the same
seeds for every label. The games themselves stay in the harness, under
`C:\Projects\UCLA-RedAlert\runs\bench\<label>`; only the figures are copied
here. `python scripts/report.py --bench <label folder>` gives a label's
totals, and `python scripts/report.py --bench-compare <folder A> <folder B>`
the Fisher p-value of B against A.

## The record

| label | change | commit | toggles | games | wins | rush_wins | normal_wins | kd_mean | compared_to | fisher_p | suite_seconds | decision |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline-72 | iteration-2 | 8194ff5 | - | 72 | 15 | 12 | 3 | -12630.56 | - | - | - | reference |
| fixround-72 | iteration-2 | 45c3d5f | - | 72 | 15 | 13 | 2 | -17726.06 | - | - | - | reference |
| mix-72 | iteration-2 | e30b6cc | - | 72 | 22 | 14 | 8 | -9448.61 | - | - | - | reference |
| focus-72 | iteration-2 | 9843e56 | - | 72 | 30 | 14 | 16 | -4245.83 | - | - | - | reference |
| iter2-72 | iteration-2 | c66b734 | - | 72 | 40 | 22 | 18 | 1477.08 | - | - | - | reference |
| nbp-72 | no-blind-press | 7739ea5 | guard=on, air=off | 72 | 37 | 22 | 15 | -168.06 | iter2-72 | 0.74 | - | kept |
| air-72 | aircraft-targeting | 7739ea5 | guard=off, air=on | 72 | 36 | 21 | 15 | 25.69 | iter2-72 | 0.62 | - | kept |
| raidonly-72 | raid-repair | ad4332e | guard=off, air=off, raids=on | 72 | 39 | 19 | 20 | -1631.94 | iter2-72 | 1.00 | - | kept |
| aa-72 | anti-air | 43a360d | guard=off, air=off, raids=on, anti_air=on | 72 | 36 | 19 | 17 | -1110.42 | raidonly-72 | 0.74 | - | kept |
| all-72 | final | 43a360d | guard=on, air=on, raids=on, anti_air=on | 72 | 38 | 20 | 18 | 2590.28 | iter2-72 | 0.87 | 232 | kept |

The columns:

- `label`: the harness label.
- `change`: `iteration-2` for the iteration-2 references; for the others the
  iteration-3 change the row measures, or `final` for the configuration the
  repository ships.
- `commit`: the commit the label was played with.
- `toggles`: the iteration-3 toggles as played. `guard` is
  `[plan_rules].blind_press_guard`, `air` is `[combat].air_targeting`, `raids`
  is `[raids].enabled` and `anti_air` is `[anti_air].enabled`. A toggle a row
  does not name had no behavior at its commit: nbp-72 and air-72 played the
  iteration-2 raids, and no row before aa-72 has the anti-air response. The
  iteration-2 rows have no toggles.
- `games`, `wins`, `rush_wins`, `normal_wins`: the games played, the games
  won, and of those the wins against `bot:rush` and `bot:normal`, 36 games
  each. fixround-72 has one game without a result; it counts in its 72 games
  as a game not won.
- `kd_mean`: the mean per game of `kd_value`, the agent's kills minus deaths
  by value, the tournament tiebreak.
- `compared_to`, `fisher_p`: the label the row is measured against, and the
  two-sided Fisher exact p-value of the two win counts, from
  `--bench-compare`.
- `suite_seconds`: the whole offline test suite's time, without coverage, in
  seconds. It was measured once, on the final commit: 3,823 tests passed in
  231.97 s.
- `decision`: `reference` for the iteration-2 rows; `kept`, `switched_off` or
  `tuned` for the others.

## The decisions

Every iteration-3 change is kept, and all-72, with all four toggles on, is
the configuration `config/redalert/tuning.toml` ships.

The decision rule was changed after the measurements. The first rule switched
a change off when its wins were lower than those of the label it was compared
to, or its `kd_mean` more than 2,000 lower. At 72 games that rule cannot tell
a change from noise: a 72-game win count has a standard deviation of about
4.2 wins, and every iteration-3 label landed between 36 and 40 wins against
the 40 of iter2-72, with every Fisher p-value at 0.62 or above. The rule
would also have switched raids off entirely, a configuration never measured,
because the raid toggle turns raids off and does not bring back the
iteration-2 raids.

The rule used instead pools, for each toggle, every label played with it on
against every label played with it off, and compares the mean wins and the
mean `kd_mean`, kd being the tournament tiebreak. A toggle is kept when its
pooled mean wins are not lower by more than one standard error (about 2
wins) and its pooled mean `kd_mean` is not lower. The pool is the seven
iteration-3 labels: the five above and the two listed under them.

| toggle | on: mean wins | on: mean kd | off: mean wins | off: mean kd |
|---|---|---|---|---|
| guard | 37.3 | 1,901 | 37.8 | -373 |
| air | 37.8 | 1,781 | 37.3 | -970 |

- guard: kept. Its pooled wins are less than half a win lower, well within
  one standard error, and its pooled kd is about 2,270 higher.
- air: kept. Its pooled wins are less than half a win higher, and its pooled
  kd is about 2,750 higher.
- raids: kept. Raids off was never measured, and raids on is the only raid
  behavior of this code that was.
- anti_air: kept, as an exception to the pooled rule. aa-72 won 3 games fewer
  than raidonly-72 (p 0.74), more than the rule's one-standard-error allowance,
  so the pooled-wins test alone would switch it off. It is kept on other
  grounds: all-72, with all four toggles on, has the second-best `kd_mean` of
  the seven labels, and the AA defenses went up in 36 of the 36 games against
  `bot:normal`.

Two labels of the pool are not rows of the record, because neither measures
one change on its own:

- nbp-air-72: commit 7739ea5, guard=on, air=on, iteration-2 raids; 37 wins
  (rush 20, normal 17), `kd_mean` 3279.86.
- raid-72: commit d606be6, guard=off, air=on, raids=on; 40 wins (rush 20,
  normal 20), `kd_mean` 1225.69.

## The iteration-3 goal

The goal was more than 18 of 36 games won against `bot:normal` and at least
22 of 36 against `bot:rush`, with the offline suite under 480 s.

Goal: not met. The final configuration won 18 of 36 games against normal,
where more than 18 are needed, and 20 of 36 against rush, where at least 22
are needed.

## Iteration 7: Soviet against Normal

Iteration 7 set out to stop the agent losing to `bot:normal` when it plays
the Soviets (`russia`). Every label of this section was played on the local
harness with the stepped clock, like the record above, and its games stay
under `C:\Projects\UCLA-RedAlert\runs\bench\<label>`. The record above is
unchanged: none of the iteration-7 changes is in force in the configuration
the repository ships (see "The iteration-7 decision").

### Why the Soviets lose to Normal

Pooled over eight earlier labels (iter2-72, the five iteration-3 rows of the
record and the two pool labels nbp-air-72 and raid-72), the agent won 41 of
144 games against `bot:normal` as the Soviets and 99 of 144 as the Allies
(`england`), with the same income, the same tech and plan timing and the same
enemy air. The analysis of those games found three causes:

- The army mix. Until about minute 9 the Soviets put 16 to 18 % of their army
  spending into flak trucks (`ftrk`), where the Allies put 21 to 25 % into
  light tanks; between 540 and 1,020 s the Soviet losses ran 30 to 80 % higher
  at equal kills.
- No Soviet defense against armor. 49 of the 103 Soviet losses were ahead at
  1,020 s and lost to a tank-heavy last wave, met by flame towers and few
  Tesla coils.
- The late PRESS. The rule that opens a PRESS late in the game when behind on
  value (R4) fired in 85 of the 144 Soviet games, at a median of 1,010 s; the
  agent then won 4 of those 85, and its base was destroyed in 56.

### The iteration-7 changes

Each change sits behind switches in `config/redalert/tuning.toml`. As played
in sov-after-72 (commit 3bd6869), all three on:

- The army mix without flak filler: Soviet `[mix]` rows with more rifles
  (`e1`) and rocket soldiers (`e3`) and half the flak trucks in the base row,
  `[composition] raid_mixed_vehicles = 0` (no flak truck in the mixed raid)
  and `[anti_air] air_flak_points = 0` (no flak truck as the anti-air filler).
- Tesla coils as the Soviet defense against armor:
  `[upkeep] soviet_tesla_coils = 2` (two Tesla coils once a war factory
  stands), and PRESERVE adding the defense against armor when the enemy army
  seen is vehicle-heavy. At commit 3bd6869 PRESERVE's choice had no switch;
  `[upkeep] preserve_anti_armor` now turns it off.
- The guarded late PRESS: `[plan_rules] press_late_seconds = 120` instead of
  300, and `late_press_guard = true`, under which R4 does not open while base
  defense reports a major threat on the base.

### The iteration-7 measurement

The plan, set before any game was played: sov-before-36, a snapshot of commit
46d0cd3, against sov-after-36, the iteration's final commit, each 36 games of
`russia` against `bot:normal` on seeds 455-490 (sidestep 455-460 and 473-478,
trapped 461-466 and 479-484, shadowfiend-ii 467-472 and 485-490; the first six
seeds of each map are the normal-russia seeds of the 72-game set). 46d0cd3
has the Red Alert code and configuration of all-72's commit 43a360d. The pass
mark was 17 wins of 36, the fewest above the pooled 41 of 144 at a two-sided
Fisher p below 0.05 (17 gives 0.045).

sov-before-36 won 16 of 36, so on these seeds the mark barely separated the
changes from the old code. Before any game with the changes was played, both
arms were extended by a third block of seeds, 491-526 (sidestep 491-496 and
509-514, trapped 497-502 and 515-520, shadowfiend-ii 503-508 and 521-526), to
72 games each on identical seeds: sov-before-72, the 36 games of
sov-before-36 and 36 new ones, and sov-after-72. The deciding comparison
became sov-after-72 against sov-before-72: pass when after wins more games at
a two-sided Fisher p below 0.10; failing that, the changes are kept only when
after has both more wins and a higher `kd_mean`. The 17-of-36 mark is still
reported, on the original 36 seeds.

Then all-i7-72: the full 72-game set of the record above with the
configuration the repository ships, held to at least 36 wins (all-72 won 38).

### The iteration-7 results

| arm | commit | switches | seeds | games | wins | kd_mean | compared_to | fisher_p |
|---|---|---|---|---|---|---|---|---|
| sov-before-36 | 46d0cd3 | - | 455-490 | 36 | 16 | -6545.83 | - | - |
| sov-before-72 | 46d0cd3 | - | 455-526 | 72 | 31 | -6331.25 | - | - |
| sov-after-72 | 3bd6869 | all on | 455-526 | 72 | 30 | -4893.75 | sov-before-72 | 1.00 |
| all-i7-72 | 340296f | all off | the 72-game set | 72 | 40 | 1398.61 | all-72 | 0.87 |

`arm` is the harness label, `switches` the iteration-7 switches as played
(`-`: the commit has none) and `seeds` the seeds played; the other columns are
those of the record above. The
three `sov-` arms are `russia` against `bot:normal` only. Their wins per map,
of 12 games per map in sov-before-36 and 24 in the other two:

| map | sov-before-36 | sov-before-72 | sov-after-72 |
|---|---|---|---|
| sidestep | 5 | 11 | 10 |
| trapped | 5 | 10 | 8 |
| shadowfiend-ii | 6 | 10 | 12 |

sov-after-72 won 30 of 72 against 31 for sov-before-72 (Fisher p 1.00), with
a `kd_mean` about 1,440 higher. On the original 36 seeds it won 13, against 16
for sov-before-36 (p 0.63): the 17-of-36 mark is missed. Seed by seed, 37 of
the 72 games changed outcome between the two arms: 18 losses became wins and
19 wins became losses.

all-i7-72 was played with commit 340296f, every iteration-7 switch off as
shipped. It won 40 of 72 (rush 23, normal 17), `kd_mean` 1398.61, against 38
for all-72 (rush 20, normal 18), `kd_mean` 2590.28: Fisher p 0.87 (rush 0.63,
normal 1.00). It passes the 36-win mark, which confirms that the switched-off
code does not change the results beyond noise. Its wins per configuration,
the map columns of 6 games each:

| configuration | games | sidestep | trapped | shadowfiend-ii | wins | all-72 wins |
|---|---|---|---|---|---|---|
| rush-england | 18 | 6 | 4 | 4 | 14 | 11 |
| rush-russia | 18 | 4 | 3 | 2 | 9 | 9 |
| normal-england | 18 | 4 | 5 | 3 | 12 | 11 |
| normal-russia | 18 | 0 | 4 | 1 | 5 | 7 |

### The iteration-7 decision

By the rule set before the after arm was played, the changes are not kept:
sov-after-72 has the higher `kd_mean` but not more wins. The configuration
the repository ships switches all three off:

- the Soviet `[mix]` rows from before the iteration,
  `[composition] raid_mixed_vehicles = 3` and
  `[anti_air] air_flak_points = 10`; the rows without flak filler stay in
  `tuning.toml` as a commented alternative;
- `[upkeep] soviet_tesla_coils = 0`, and
  `[upkeep] preserve_anti_armor = false`, which returns PRESERVE's choice to
  the defense against infantry whatever the enemy army;
- `[plan_rules] press_late_seconds = 300` and
  `[plan_rules] late_press_guard = false`.

The code stays, behind those switches.

Iteration-7 goal: not met. The changes won 13 of the original 36 games, where
17 were needed, and 30 of 72 against 31 without them; only the 72-game check
of the shipped configuration, at 40 wins, passed.

### The iteration-7 lesson

With one game per seed, about half the games flip between two near-identical
arms: 37 of 72 between sov-before-72 and sov-after-72, whose win counts differ
by one, and 32 of 72 between all-72 and all-i7-72 (17 losses became wins, 15
wins became losses), whose Red Alert code differs only by the iteration-7
changes, all switched off. A comparison of two 72-game labels therefore
resolves only large effects.

## Iteration 9: the Soviet changes at high resolution

After the iteration-7 lesson, the iteration-7 changes were measured again on
far more games. Every game was played on the local harness with the stepped
clock, like those above, and stays under
`C:\Projects\UCLA-RedAlert\runs\bench\<label>`. The iteration-7 section above
is unchanged; this section supersedes its decision: the configuration the
repository ships now has every Soviet switch on.

### The iteration-9 arms

Every arm played a snapshot of commit 340296f, the commit all-i7-72 was
played with, exported with `git archive`: `snap-off`, with `tuning.toml` as
shipped at iteration 7 (every switch off), or `snap-on`, with every switch
on. The two snapshots differ only in the switches of
`config/redalert/tuning.toml`. The off arms ran on harness port 5310 and the
on arms on port 5320, at the same time. Every game has a seed of its own, one
game per seed, and both arms of a comparison play the same seeds.

- The Soviet comparison, i8-off-216 against i8-on-216: `russia` against
  `bot:normal`, 72 fresh seeds per map (sidestep 600-671, trapped 700-771,
  shadowfiend-ii 800-871), 216 games per arm.
- The England check, i9-eng-off-144 against i9-eng-on-144: `england` against
  `bot:rush` and `bot:normal` on the three maps, 24 seeds per configuration
  (rush: sidestep 1000-1023, trapped 1100-1123, shadowfiend-ii 1200-1223;
  normal: sidestep 1300-1323, trapped 1400-1423, shadowfiend-ii 1500-1523),
  144 games per arm. Two of the switches act for the Allies as well,
  `[upkeep] preserve_anti_armor` and the late-PRESS guard with its 120 s
  window; the others act for the Soviets only. The rule, set before the check
  was played: when the on arm wins no more than 5 games fewer than the off
  arm, over its 144 games together, those two switches ship on for both
  families; when it wins more than 5 fewer, they are limited to the Soviets.

### The iteration-9 results

| measurement | snapshot | faction | games | wins | rush_wins | normal_wins | kd_mean | compared_to | fisher_p |
|---|---|---|---|---|---|---|---|---|---|
| i8-off-216 | snap-off | russia | 216 | 70 | - | 70 | -9081.71 | - | - |
| i8-on-216 | snap-on | russia | 216 | 87 | - | 87 | -3484.49 | i8-off-216 | 0.11 |
| i9-eng-off-144 | snap-off | england | 144 | 99 | 55 | 44 | 10251.39 | - | - |
| i9-eng-on-144 | snap-on | england | 144 | 98 | 51 | 47 | 12177.78 | i9-eng-off-144 | 1.00 |

`measurement` is the harness label and `snapshot` the snapshot it played;
the other columns are those of the record above, with `-` for a bot the label
did not play. Against i9-eng-off-144, the Fisher p-value of i9-eng-on-144 is
0.57 against `bot:rush` and 0.73 against `bot:normal`.

The Soviet arms' wins per map, 72 games each:

| soviet_map | seeds | games | i8-off-216 | i8-on-216 |
|---|---|---|---|---|
| sidestep | 600-671 | 72 | 30 | 32 |
| trapped | 700-771 | 72 | 19 | 25 |
| shadowfiend-ii | 800-871 | 72 | 21 | 30 |

The England arms' wins per configuration, 24 games each:

| england_config | seeds | games | i9-eng-off-144 | i9-eng-on-144 |
|---|---|---|---|---|
| rush-sidestep | 1000-1023 | 24 | 23 | 20 |
| rush-trapped | 1100-1123 | 24 | 17 | 18 |
| rush-shadowfiend-ii | 1200-1223 | 24 | 15 | 13 |
| normal-sidestep | 1300-1323 | 24 | 15 | 19 |
| normal-trapped | 1400-1423 | 24 | 15 | 13 |
| normal-shadowfiend-ii | 1500-1523 | 24 | 14 | 15 |

i8-on-216 won 87 of 216 games against 70 for i8-off-216, 17 more (Fisher p
0.11, not significant), at a `kd_mean` of -3,484 against -9,082: +5,597 from
the unrounded means. It won more games on every map. Seed by seed, 95 of the
216 games changed outcome between the two arms: 56 losses became wins and 39
wins became losses.

i9-eng-on-144 won 98 of 144 games against 99 for i9-eng-off-144, one fewer
(Fisher p 1.00; against `bot:rush` 51 against 55, against `bot:normal` 47
against 44), at a `kd_mean` of 12,178 against 10,251: +1,926 from the
unrounded means. Seed by seed, 59 of the 144 games changed outcome: 29 losses
became wins and 30 wins became losses.

These arms are not pooled with the iteration-7 arms: sov-before-72 and
sov-after-72 were played with other commits, on other seeds.

### The iteration-9 decision

The Soviet switches: on, by the second branch of the rule of the iteration-7
measurement. Its first branch keeps a change that wins more games at a
two-sided Fisher p below 0.10; at p 0.11 the 17 more wins are not
significant, and that branch is missed. Its second branch keeps a change that
has both more wins and a higher `kd_mean`: i8-on-216 has 17 more wins and a
`kd_mean` 5,597 higher.

The England check: passed. i9-eng-on-144 won one game fewer than
i9-eng-off-144, within the margin of 5, so the two switches that act for the
Allies ship on for both families. The check is a conservative guard, not a
test of significance: the difference of two 144-game win counts near 99 has
a standard deviation of about 8 wins, so with no real effect the guard would
trip about one time in four (about 25 %), and a real loss of 8 percentage
points (about 11.5 wins of 144) would trip it about three times in four
(about 75 %). A false trip would only have kept the Allies on the old
behavior.

The configuration the repository ships, `config/redalert/tuning.toml`, has
every switch on. Each switch, with its value in the off arm, as shipped at
iteration 7, and its shipped value:

| soviet_switch | off | shipped |
|---|---|---|
| `[composition] raid_mixed_vehicles` | `3` | `0` |
| `[anti_air] air_flak_points` | `10` | `0` |
| `[upkeep] soviet_tesla_coils` | `0` | `2` |
| `[upkeep] preserve_anti_armor` | `false` | `true` |
| `[plan_rules] press_late_seconds` | `300` | `120` |
| `[plan_rules] late_press_guard` | `false` | `true` |
| `[mix.soviet.base] infantry` | `{ e1 = 45, e2 = 15, e3 = 10 }` | `{ e1 = 50, e2 = 10, e3 = 25 }` |
| `[mix.soviet.base] vehicle` | `{ ftrk = 30 }` | `{ ftrk = 15 }` |
| `[mix.soviet.fix] infantry` | `{ e1 = 35, e2 = 15, e3 = 10 }` | `{ e1 = 35, e2 = 10, e3 = 15 }` |
| `[mix.soviet.fix] vehicle` | `{ 3tnk = 40 }` | `{ 3tnk = 40 }` |
| `[mix.soviet.dome] infantry` | `{ e1 = 30, e2 = 15 }` | `{ e1 = 35, e2 = 5, e3 = 10 }` |
| `[mix.soviet.dome] vehicle` | `{ 3tnk = 30, v2rl = 25 }` | `{ 3tnk = 25, v2rl = 25 }` |

The off arm's Soviet `[mix]` rows stay in `tuning.toml` as a commented
alternative, to be played with `raid_mixed_vehicles = 3` and
`air_flak_points = 10`.

### The iteration-9 method

The flips confirm the iteration-7 lesson: 95 of 216 and 59 of 144
seed-matched games changed outcome between arms that differ only in a few
tuning values. A decision about the Red Alert agent is therefore taken on at
least 200 fresh seeds per arm in the configurations the change targets, with
a no-regression check of at least 144 games per arm elsewhere, the two arms
in parallel on two harness ports; a pair of 144-game arms takes about 66
minutes. `docs/RUNNING.md`, "Local benchmark", gives the procedure.

## Iteration 11: the early-attack guard and harvester parking

Iteration 11 set out to stop the agent losing games to early attacks and to
late collapses, with two switches of `config/redalert/tuning.toml`:

- The early-attack guard, `[plan_rules] early_attack_guard`: R5 never starts
  STRIKE; before `early_press_until_seconds` (540 s) R7 opens PRESS only once
  the enemy has committed its army, a major threat on the base beaten off
  with no core building lost or `early_press_kill_value` (1,500) of kills;
  and every comparison of the own army with the enemy's counts only the own
  units that fight, not the harvesters.
- Harvester parking, `[upkeep] park_harvesters`: in the last
  `park_harvesters_seconds_left` (120) seconds, or under PRESERVE while base
  defense reports a major threat, the harvesters wait at a cell
  `park_distance_cells` (8) behind the base, away from the enemy.

Both were measured by the method of the iteration-9 section. Every game was
played on the local harness with the stepped clock and stays under
`C:\Projects\UCLA-RedAlert\runs\bench\<label>`. The sections above are
unchanged.

### The iteration-11 arms

Every arm played a copy of commit 859b9fa; the three copies differ only in
the two switches of `config/redalert/tuning.toml`:

- `off`: `early_attack_guard = false` and `park_harvesters = false`;
- `guard`: `early_attack_guard = true` and `park_harvesters = false`;
- `both`: `early_attack_guard = true` and `park_harvesters = true`.

Each arm played four sets of games, one game per seed and the same seeds in
every arm, up to six games at a time on separate harness ports:

- ru-normal: `russia` against `bot:normal`, 72 seeds per map (sidestep
  2000-2071, trapped 2100-2171, shadowfiend-ii 2200-2271), 216 games;
- en-normal: `england` against `bot:normal`, 72 seeds per map (sidestep
  2300-2371, trapped 2400-2471, shadowfiend-ii 2500-2571), 216 games;
- en-rush: `england` against `bot:rush`, 48 seeds per map (sidestep
  2600-2647, trapped 2700-2747, shadowfiend-ii 2800-2847), 144 games;
- ru-rush: `russia` against `bot:rush`, 48 seeds per map (sidestep
  2900-2947, trapped 3000-3047, shadowfiend-ii 3100-3147), 144 games.

Each set is the label `i11-<arm>-<set>`, 720 games per arm. A game that
failed to finish was played again on its seed, so every label holds all its
games.

The rule, set before any game was played: ru-normal and en-normal are the
targeted games (432 per arm), all four sets together all the games (720 per
arm); their wins are summed and their mean kd is the mean over the same
games. The guard ships on when the guard arm wins more of the targeted games
than the off arm at a higher mean kd, and neither rush check fails. A rush
check compares the guard arm with the off arm against `bot:rush` for one
faction: it fails when the guard arm wins more than 5 games fewer of its
144. With the guard on, parking ships on when the both arm wins more of all
the games than the guard arm at a higher mean kd. A change that fails ships
off.

### The iteration-11 results

| run | faction | bot | games | wins | kd_mean | compared_to | fisher_p |
|---|---|---|---|---|---|---|---|
| i11-off-ru-normal | russia | normal | 216 | 70 | -7591.44 | - | - |
| i11-guard-ru-normal | russia | normal | 216 | 107 | -651.85 | i11-off-ru-normal | 0.00 |
| i11-both-ru-normal | russia | normal | 216 | 101 | -884.03 | i11-guard-ru-normal | 0.63 |
| i11-off-en-normal | england | normal | 216 | 152 | 9605.32 | - | - |
| i11-guard-en-normal | england | normal | 216 | 156 | 11261.34 | i11-off-en-normal | 0.75 |
| i11-both-en-normal | england | normal | 216 | 141 | 10939.58 | i11-guard-en-normal | 0.15 |
| i11-off-en-rush | england | rush | 144 | 105 | 13039.93 | - | - |
| i11-guard-en-rush | england | rush | 144 | 120 | 19979.51 | i11-off-en-rush | 0.05 |
| i11-both-en-rush | england | rush | 144 | 106 | 16631.25 | i11-guard-en-rush | 0.06 |
| i11-off-ru-rush | russia | rush | 144 | 68 | 9.72 | - | - |
| i11-guard-ru-rush | russia | rush | 144 | 60 | -128.47 | i11-off-ru-rush | 0.41 |
| i11-both-ru-rush | russia | rush | 144 | 69 | 1534.38 | i11-guard-ru-rush | 0.34 |

`run` is the harness label; the other columns are those of the record above.
Each guard arm is compared with the off arm of its set, and each both arm
with the guard arm. The p-value of i11-guard-ru-normal, 0.00 as
`--bench-compare` prints it, is 0.0004.

The wins per map, of 72 games against `bot:normal` and 48 against
`bot:rush`:

| seed_set | seeds | games | off | guard | both |
|---|---|---|---|---|---|
| ru-normal-sidestep | 2000-2071 | 72 | 24 | 30 | 32 |
| ru-normal-trapped | 2100-2171 | 72 | 22 | 35 | 32 |
| ru-normal-shadowfiend-ii | 2200-2271 | 72 | 24 | 42 | 37 |
| en-normal-sidestep | 2300-2371 | 72 | 58 | 53 | 49 |
| en-normal-trapped | 2400-2471 | 72 | 53 | 57 | 48 |
| en-normal-shadowfiend-ii | 2500-2571 | 72 | 41 | 46 | 44 |
| en-rush-sidestep | 2600-2647 | 48 | 40 | 44 | 44 |
| en-rush-trapped | 2700-2747 | 48 | 34 | 41 | 38 |
| en-rush-shadowfiend-ii | 2800-2847 | 48 | 31 | 35 | 24 |
| ru-rush-sidestep | 2900-2947 | 48 | 31 | 22 | 34 |
| ru-rush-trapped | 3000-3047 | 48 | 16 | 13 | 13 |
| ru-rush-shadowfiend-ii | 3100-3147 | 48 | 21 | 25 | 22 |

The targeted games and all the games, by arm:

| combined | games | off_wins | off_kd_mean | guard_wins | guard_kd_mean | both_wins | both_kd_mean |
|---|---|---|---|---|---|---|---|
| targeted | 432 | 222 | 1006.94 | 263 | 5304.75 | 242 | 5027.78 |
| all | 720 | 395 | 3214.10 | 443 | 7153.06 | 417 | 6649.79 |

On the targeted games the guard arm won 263 of 432 games against 222 for the
off arm, at a `kd_mean` of 5,305 against 1,007: +4,298 from the unrounded
means. On all the games the guard arm won 443 of 720 games against 395 for
the off arm, at a `kd_mean` of 7,153 against 3,214: +3,939 from the
unrounded means. On all the games the both arm won 417 of 720 games against
443 for the guard arm, at a `kd_mean` of 6,650 against 7,153: -503 from the
unrounded means.

The rush checks, the guard arm against the off arm:

| rush_check | games | off | guard | guard_minus_off | passed |
|---|---|---|---|---|---|
| en-rush | 144 | 105 | 120 | 15 | yes |
| ru-rush | 144 | 68 | 60 | -8 | no |

The guard cut the games lost by knockout, every building and MCV destroyed
before the time limit, in three sets and left the fourth as it was: off
against guard, 85 against 53 on ru-normal, 42 against 25 on en-normal, 33
against 21 on en-rush and 62 against 62 on ru-rush. The 8 games
i11-guard-ru-rush lost more than i11-off-ru-rush were all lost at the time
limit, on the tiebreak: 22 such losses against 14.

Every both arm brought in less income than its guard arm. The mean
`income_per_s`, guard against both, was 52.98 against 50.29 on ru-normal,
52.70 against 49.25 on en-normal, 53.41 against 50.10 on en-rush and 52.56
against 50.43 on ru-rush.

After the decision, ru-rush was played again on fresh seeds for the record,
48 per map (sidestep 3200-3247, trapped 3300-3347, shadowfiend-ii
3400-3447), with the off and the guard arm:

| confirm_run | seeds | games | wins | kd_mean | compared_to | fisher_p |
|---|---|---|---|---|---|---|
| i11-off-ru-rush-confirm | 3200-3247, 3300-3347, 3400-3447 | 144 | 53 | -4614.24 | - | - |
| i11-guard-ru-rush-confirm | 3200-3247, 3300-3347, 3400-3447 | 144 | 64 | -199.65 | i11-off-ru-rush-confirm | 0.23 |

Their wins per map, sidestep, trapped and shadowfiend-ii, were 22, 8 and 23
for the off arm and 26, 14 and 24 for the guard arm: 11 more for the guard.
Over both ru-rush sets, 288 games per arm, the off arm won 121 and the guard
arm 124.

### The iteration-11 decision

The early-attack guard: on. It meets the first part of the rule: on the
targeted games it won 41 games more than the off arm at a `kd_mean` 4,298
higher. It passes the England rush check, 15 games more, but fails the
Russia rush check: i11-guard-ru-rush won 8 games fewer than i11-off-ru-rush,
more than the 5 the rule allows. By the rule the guard would ship off; the
user decided to ship it on. The check is a conservative guard, not a test of
significance (see the iteration-9 decision): the 8 fewer wins have a Fisher p
of 0.41, they were all games lost on the tiebreak, not by knockout, and the
fresh ru-rush seeds gave the guard 11 more wins.

Harvester parking: off, by the rule. On all the games the both arm won 26
games fewer than the guard arm at a `kd_mean` 503 lower, and it brought in
less income in every set. The guard ships on, so the follow-up that would
have measured parking without it was not played.

The configuration the repository ships, `config/redalert/tuning.toml`, has
the guard on and parking off. Each switch and threshold of this section, as
each arm played it and as shipped:

| shipped_switch | off | guard | both | shipped |
|---|---|---|---|---|
| `[plan_rules] early_attack_guard` | `false` | `true` | `true` | `true` |
| `[plan_rules] early_press_until_seconds` | `540` | `540` | `540` | `540` |
| `[plan_rules] early_press_kill_value` | `1500` | `1500` | `1500` | `1500` |
| `[upkeep] park_harvesters` | `false` | `false` | `true` | `false` |
| `[upkeep] park_harvesters_seconds_left` | `120` | `120` | `120` | `120` |
| `[upkeep] park_distance_cells` | `8` | `8` | `8` | `8` |

## Iteration 12: the economy bundle and the army bundle

Iteration 12 set out to give the scripted Red Alert agent a better economy and
a better army, with switches of `config/redalert/tuning.toml` measured as two
bundles:

- The economy bundle. `[opening] order = "reach_first"` plays the
  barracks-first build when, as the barracks is on its way, a refinery can
  stand against its ore field, and else one more power plant right after the
  barracks, placed toward that field. `[placement] refinery_field_by_reach`
  on gives each refinery the nearest unserved field it can stand against
  now, else a near field it can share, else the nearest unserved field.
  `[placement] creep_toward_ore` on places every other building but a
  defense nearest the ore of a refinery's field while that refinery is due
  and no place for it reaches the field, so that the base creeps toward it.
- The army bundle. `[mix] main_force = "tanks"` builds the main force from
  the tank rows: mostly light tanks for the Allies before the service depot,
  mostly tanks for both families from it on, and the depot queued as soon as
  the war factory stands. `[raids] standing` on forms the raid group, fills it
  first and sends it out under every plan after the opening but PRESERVE,
  alongside the main army. `[composition] allied_raid_light_tanks` on
  makes the Allied raid `raid_vehicles` light tanks only, and
  `[composition] soviet_raid = "apc_rockets"` makes the Soviet raid
  `raid_apcs` (2) APCs carrying `raid_apc_rockets` (5) rocket soldiers each;
  `"none"` gives the Soviets no raid at all.

Both bundles were measured by the method of the iteration-9 section. Every
game was played on the local harness with the stepped clock and stays under
`C:\Projects\UCLA-RedAlert\runs\bench\<label>`. The sections above are
unchanged.

### The iteration-12 arms

Every arm played a copy of commit 875fc7b; the copies differ only in the
values of `config/redalert/tuning.toml` that the last table of this section
gives:

- `A`: the tracked file, every switch of both bundles off;
- `B`: A with the economy bundle on;
- `C`: B with the army bundle on, `raid_vehicles = 6` and
  `soviet_raid = "apc_rockets"`;
- `C4` and `C8`: C with `raid_vehicles = 4` and `raid_vehicles = 8`;
- `D`: C with `soviet_raid = "none"`.

Each arm played its sets, one game per seed and the same seeds in every arm,
up to six games at a time on separate harness ports:

- ru-normal: `russia` against `bot:normal`, 72 seeds per map (sidestep
  4000-4071, trapped 4100-4171, shadowfiend-ii 4200-4271), 216 games;
- en-normal: `england` against `bot:normal`, 72 seeds per map (sidestep
  4300-4371, trapped 4400-4471, shadowfiend-ii 4500-4571), 216 games;
- en-rush: `england` against `bot:rush`, 48 seeds per map (sidestep
  4600-4647, trapped 4700-4747, shadowfiend-ii 4800-4847), 144 games;
- ru-rush: `russia` against `bot:rush`, 48 seeds per map (sidestep
  4900-4947, trapped 5000-5047, shadowfiend-ii 5100-5147), 144 games.

A, B and C played all four sets, 720 games each; C4 and C8 the two England
sets and D the two Russia sets, 360 games each: 3,240 games in all. Each set
is the label `i12-<arm>-<set>`, its folder named with the arm in upper case,
as `i12-A-ru-normal` or `i12-C4-en-rush`. Every label holds all its games,
each with a result.

The rule, set before any game was played: a figure over several sets sums
their wins and weighs each set's mean kd by its games, and one arm beats
another when it wins more of the games named at a higher mean kd. The
decisions are taken in order:

1. The economy bundle ships when B beats A over all their 720 games.
2. The Soviet raid is `"apc_rockets"` when C beats D over the 360 Russia
   games, and `"none"` otherwise.
3. The army arm is C's England games with, for Russia, the games of the
   Soviet raid chosen in 2: C's or D's. The army bundle, with that Soviet
   raid, ships when the army arm beats B over its 720 games. When the
   economy bundle did not ship but the army arm beats A over its 720 games,
   both bundles ship together, the army having been measured on the new
   economy.
4. When the army bundle ships, its raid size is the best of C4, C and C8
   over the 360 England games: the most wins, then the higher mean kd.

A value that does not ship stays as in A.

### The iteration-12 results

| arm_run | faction | bot | games | wins | kd_mean | income_per_s | assets_value |
|---|---|---|---|---|---|---|---|
| i12-A-ru-normal | russia | normal | 216 | 113 | 147.45 | 52.74 | 19334.95 |
| i12-B-ru-normal | russia | normal | 216 | 109 | 271.3 | 52.79 | 18698.38 |
| i12-C-ru-normal | russia | normal | 216 | 37 | -15895.37 | 51.03 | 9176.62 |
| i12-D-ru-normal | russia | normal | 216 | 79 | -6603.7 | 51.94 | 13884.72 |
| i12-A-en-normal | england | normal | 216 | 158 | 11349.77 | 52.68 | 24666.9 |
| i12-B-en-normal | england | normal | 216 | 139 | 8180.56 | 53.5 | 25456.71 |
| i12-C-en-normal | england | normal | 216 | 110 | 660.88 | 53.17 | 21456.25 |
| i12-C4-en-normal | england | normal | 216 | 126 | 4619.91 | 53.39 | 22635.65 |
| i12-C8-en-normal | england | normal | 216 | 68 | -10141.9 | 52.63 | 13831.94 |
| i12-A-en-rush | england | rush | 144 | 112 | 18219.1 | 53.12 | 31818.75 |
| i12-B-en-rush | england | rush | 144 | 109 | 17481.6 | 53.73 | 32873.61 |
| i12-C-en-rush | england | rush | 144 | 65 | 1243.75 | 51.53 | 18044.79 |
| i12-C4-en-rush | england | rush | 144 | 84 | 7943.06 | 52.94 | 23763.89 |
| i12-C8-en-rush | england | rush | 144 | 43 | -7549.31 | 51.33 | 13169.44 |
| i12-A-ru-rush | russia | rush | 144 | 61 | -1949.65 | 52.11 | 15531.94 |
| i12-B-ru-rush | russia | rush | 144 | 65 | -1195.14 | 51.43 | 16363.54 |
| i12-C-ru-rush | russia | rush | 144 | 18 | -15673.26 | 47.67 | 5077.08 |
| i12-D-ru-rush | russia | rush | 144 | 55 | -3332.29 | 50.69 | 14685.07 |

`arm_run` is the harness label; `games`, `wins` and `kd_mean` are those of
the record above. `income_per_s` is the mean per game of the realized income,
in credits per second, that the agent's timing events give, and
`assets_value` the mean per game of the value of the agent's standing assets
at the end, the tiebreak's second figure. Every figure is copied from
`scripts/report.py --bench-compare` as it prints it.

Each compared pair, the first arm against the second on one set:

| compared_pair | set | wins_difference | fisher_p | kd_difference |
|---|---|---|---|---|
| A -> B | ru-normal | -4 | 0.77 | 123.84 |
| A -> B | en-normal | -19 | 0.06 | -3169.21 |
| A -> B | en-rush | -3 | 0.78 | -737.5 |
| A -> B | ru-rush | 4 | 0.72 | 754.51 |
| A -> C | ru-normal | -76 | 0.00 | -16042.82 |
| A -> C | en-normal | -48 | 0.00 | -10688.89 |
| A -> C | en-rush | -47 | 0.00 | -16975.35 |
| A -> C | ru-rush | -43 | 0.00 | -13723.61 |
| B -> C | ru-normal | -72 | 0.00 | -16166.67 |
| B -> C | en-normal | -29 | 0.01 | -7519.68 |
| B -> C | en-rush | -44 | 0.00 | -16237.85 |
| B -> C | ru-rush | -47 | 0.00 | -14478.12 |
| D -> C | ru-normal | -42 | 0.00 | -9291.67 |
| D -> C | ru-rush | -37 | 0.00 | -12340.97 |
| A -> D | ru-normal | -34 | 0.00 | -6751.16 |
| A -> D | ru-rush | -6 | 0.55 | -1382.64 |
| C4 -> C | en-normal | -16 | 0.15 | -3959.03 |
| C4 -> C | en-rush | -19 | 0.03 | -6699.31 |
| C -> C8 | en-normal | -42 | 0.00 | -10802.78 |
| C -> C8 | en-rush | -22 | 0.01 | -8793.06 |
| C4 -> C8 | en-rush | -41 | 0.00 | -15492.36 |
| A -> C4 | en-normal | -32 | 0.00 | -6729.86 |
| A -> C4 | en-rush | -28 | 0.00 | -10276.04 |
| A -> C8 | en-normal | -90 | 0.00 | -21491.67 |
| A -> C8 | en-rush | -69 | 0.00 | -25768.4 |

`wins_difference` and `kd_difference` are the second arm's wins and mean kd
minus the first's, the `B - A` rows of `--bench-compare`, which takes the kd
difference from the unrounded means; `fisher_p` is the two-sided Fisher exact
p-value of the two win counts, to two decimals as `--bench-compare` prints
it, so that 0.00 is below 0.005.

The games each decision names, by arm:

| decision_scope | arm | runs | games | wins | kd_mean |
|---|---|---|---|---|---|
| economy | A | i12-A-ru-normal, i12-A-en-normal, i12-A-en-rush, i12-A-ru-rush | 720 | 444 | 6703.06 |
| economy | B | i12-B-ru-normal, i12-B-en-normal, i12-B-en-rush, i12-B-ru-rush | 720 | 422 | 5792.85 |
| soviet_raid | C | i12-C-ru-normal, i12-C-ru-rush | 360 | 55 | -15806.53 |
| soviet_raid | D | i12-D-ru-normal, i12-D-ru-rush | 360 | 134 | -5295.14 |
| army | army | i12-C-en-normal, i12-C-en-rush, i12-D-ru-normal, i12-D-ru-rush | 720 | 309 | -2200.55 |
| raid_size | C4 | i12-C4-en-normal, i12-C4-en-rush | 360 | 210 | 5949.17 |
| raid_size | C | i12-C-en-normal, i12-C-en-rush | 360 | 175 | 894.03 |
| raid_size | C8 | i12-C8-en-normal, i12-C8-en-rush | 360 | 111 | -9104.86 |

The army arm's Russia games are D's, the Soviet raid chosen in 2. Each row's
wins are the sum of its runs' wins, and its `kd_mean` the mean of their
`kd_mean` weighed by their games, taken from the means above.

### The iteration-12 decision

Every decision of the rule fails, so every value ships as in arm A.

The economy bundle: off. Over all the games B won 422 of 720 games against
444 for A, at a `kd_mean` of 5,793 against 6,703: -910 from the unrounded
means. B won fewer games than A on three of the four sets and 4 more on
ru-rush, at a Fisher p of 0.06 to 0.78. It brought in about as much income:
its mean `income_per_s` was 0.05, 0.82 and 0.61 higher than A's on
ru-normal, en-normal and en-rush, and 0.67 lower on ru-rush.

The Soviet raid: `"none"`. Over the Russia games C won 55 of 360 games
against 134 for D, at a `kd_mean` of -15,807 against -5,295: -10,511 from
the unrounded means. C, with the APC raid, won fewer games than D on both
Russia sets, at a Fisher p of 0.00 on each.

The army bundle: off. The army arm is C's England games with D's Russia
games. Over its games the army arm won 309 of 720 games against 422 for B,
at a `kd_mean` of -2,201 against 5,793: -7,993 from the unrounded means. It
does not beat A either, so the two bundles do not ship together: the army
arm won 309 of 720 games against 444 for A, at a `kd_mean` of -2,201 against
6,703: -8,904 from the unrounded means.

The raid size: not applied, since the army bundle does not ship. For
information, over the 360 England games C4 won 210 at a `kd_mean` of 5,949,
C won 175 at 894 and C8 won 111 at -9,105: the wins fall as the raid group
grows. Each of the three won fewer games than A on both England sets, at a
Fisher p of 0.00 on each.

The configuration the repository ships, `config/redalert/tuning.toml`, is
therefore unchanged: it has the economy bundle off and the army bundle off.
Each switch of this section, as each arm played it and as shipped:

| bundle_switch | A | B | C | C4 | C8 | D | shipped |
|---|---|---|---|---|---|---|---|
| `[opening] order` | `"barracks_first"` | `"reach_first"` | `"reach_first"` | `"reach_first"` | `"reach_first"` | `"reach_first"` | `"barracks_first"` |
| `[placement] refinery_field_by_reach` | `false` | `true` | `true` | `true` | `true` | `true` | `false` |
| `[placement] creep_toward_ore` | `false` | `true` | `true` | `true` | `true` | `true` | `false` |
| `[mix] main_force` | `"mixed"` | `"mixed"` | `"tanks"` | `"tanks"` | `"tanks"` | `"tanks"` | `"mixed"` |
| `[raids] standing` | `false` | `false` | `true` | `true` | `true` | `true` | `false` |
| `[composition] allied_raid_light_tanks` | `false` | `false` | `true` | `true` | `true` | `true` | `false` |
| `[composition] raid_vehicles` | `4` | `4` | `6` | `4` | `8` | `6` | `4` |
| `[composition] soviet_raid` | `"infantry"` | `"infantry"` | `"apc_rockets"` | `"apc_rockets"` | `"apc_rockets"` | `"none"` | `"infantry"` |

### The iteration-12 diagnosis

The figures of this subsection were counted from the events of the match
records, not given by `scripts/report.py`. Of the three economy changes only
the choice of the field by reach acted. In the 432 B games against
`bot:normal`, i12-B-ru-normal and i12-B-en-normal, the creep toward the ore
placed no building (no `creep_placement` event), and the first refinery
could stand against its ore field in all 432 (`opening_reach`), so
`reach_first` played the barracks-first build every time. Of the 1,696
refineries placed in those games (`refinery_field`), 74 % stood 1 cell from
their ore and 19 %, almost all of them the third or fourth refinery of their
game, stood 4 to 12 cells away; 875 were sent to a field no refinery served
yet. Raids killed little in every arm: on en-normal C's 275 raids made 32
kills, and A's 162 raids made 8 (the `raid` events' `kills`); most broke off
on covered ground or outmatched. The deficit grows with the raid group's
size, C4 to C to C8 above, so the units held out of the main army in the
standing raid group cost more than the raids return against these bots.

## Iteration 13: the batch-13a switches arm by arm

Iteration 13 put ten changes behind switches of
`config/redalert/tuning.toml`, to give the scripted Red Alert agent a
stronger base and army game:

- `[placement] creep_next_patch`: the base creeps from its home ore field
  to the next field in the lane toward the enemy, field after field.
- `[upkeep] refinery_field_cap`: no ore field is served by more than two
  refineries; one more goes to a field under the cap, such as the creep
  target, or waits.
- `[upkeep] harvesters_to_fresh_ore`: harvesters that keep standing idle by
  a dry field move to the nearest field with no remembered defense or enemy
  army near it.
- `[expansion] second_yard`: a service depot early, then an MCV escorted to
  the creep target's field and deployed there as a second construction yard.
- `[producers] tank_factories`: the 2nd to the 4th war factory sooner, on
  gates of their own, with the Vehicle queue kept filled.
- `[scouting] rescout`: a scout sent again whenever the enemy base area,
  the enemy army or a far ore field has gone unseen too long.
- `[scouting] indirect_route`: every scout by a waypoint to the side,
  unseen ore fields near its way and the enemy base's flank.
- `[scouting] rush_detection` with `[opening] defense_on_threat`: a watch
  that tells an early rush, and the opening's defenses bought only against a
  rush told or a threat seen.
- `[combat] forward_hold`: under HOLD the main army stands at a forward
  point on the line toward the enemy, fights what comes near it and falls
  back when outmatched.

Each was measured on an arm of its own against arm A, every switch off.
Every game was played on the local harness with the stepped clock and stays
under `C:\Projects\UCLA-RedAlert\runs\bench\<label>`. The sections above are
unchanged.

### The iteration-13 arms

Every arm played a copy of commit 4701bd5; the copies differ only in the
switches of this section, as the last table of the section gives:

- `A`: every switch off;
- `creep`: `creep_next_patch`;
- `cap`: `creep_next_patch` and `refinery_field_cap`;
- `fresh`: `harvesters_to_fresh_ore`;
- `yard`: `creep_next_patch` and `second_yard`;
- `tankfac`: `tank_factories`;
- `rescout`: `rescout`;
- `indirect`: `rescout` and `indirect_route`;
- `rushdef`: `rush_detection` and `defense_on_threat`;
- `fwd`: `forward_hold`;
- `all`: every switch on.

Each arm played four configurations, one game per seed, with the same seeds,
maps and bots in every arm, up to six games at a time on separate harness
ports:

- ru-normal: `russia` against `bot:normal`, 12 seeds per map (sidestep
  8000-8011, trapped 8100-8111, shadowfiend-ii 8200-8211), 36 games;
- en-normal: `england` against `bot:normal`, 12 seeds per map (sidestep
  8300-8311, trapped 8400-8411, shadowfiend-ii 8500-8511), 36 games;
- ru-rush: `russia` against `bot:rush`, 12 seeds per map (sidestep
  8600-8611, trapped 8700-8711, shadowfiend-ii 8800-8811), 36 games;
- en-rush: `england` against `bot:rush`, 12 seeds per map (sidestep
  8900-8911, trapped 9000-9011, shadowfiend-ii 9100-9111), 36 games.

That is 144 games per arm and 1,584 in all. Each configuration of an arm is
the label `i13-<arm>-<config>`, as `i13-rescout-en-rush`; every label holds
its 36 games, each with a win or a loss.

The rule, set before any game was played: the mean kd of an arm is the mean
trade balance of its 144 games (value destroyed less value lost, the
`kd_value` of `--bench-compare`), which weighs each configuration by its 36
games. An arm beats A when it wins more of its 144 games than A at a higher
mean kd, and on neither rush configuration, ru-rush and en-rush, wins more
than 3 games fewer than A. A switch ships on when its arm beats A. If the
all arm beats A and every single-switch arm that would ship, every winning
switch ships; otherwise only the switches whose own arm beats A ship. A
dependent switch ships only together with the switch it depends on:
`refinery_field_cap` and `second_yard` with `creep_next_patch`,
`indirect_route` with `rescout`. A switch that does not ship stays off, as
in A.

### The iteration-13 results

| trial_run | arm | config | games | wins | kd_mean | income_per_s | assets_value |
|---|---|---|---|---|---|---|---|
| i13-A-ru-normal | A | ru-normal | 36 | 18 | 5169.44 | 53.19 | 23356.94 |
| i13-creep-ru-normal | creep | ru-normal | 36 | 20 | 702.78 | 53.8 | 21505.56 |
| i13-cap-ru-normal | cap | ru-normal | 36 | 16 | -2308.33 | 53.41 | 17183.33 |
| i13-fresh-ru-normal | fresh | ru-normal | 36 | 23 | 3480.56 | 55.7 | 24577.78 |
| i13-yard-ru-normal | yard | ru-normal | 36 | 16 | -3712.5 | 54.13 | 18995.83 |
| i13-tankfac-ru-normal | tankfac | ru-normal | 36 | 18 | 141.67 | 52.84 | 17798.61 |
| i13-rescout-ru-normal | rescout | ru-normal | 36 | 25 | 2847.22 | 52.99 | 21720.83 |
| i13-indirect-ru-normal | indirect | ru-normal | 36 | 13 | -3977.78 | 52.94 | 15297.22 |
| i13-rushdef-ru-normal | rushdef | ru-normal | 36 | 22 | 3543.06 | 53.43 | 20113.89 |
| i13-fwd-ru-normal | fwd | ru-normal | 36 | 18 | 1447.22 | 53.73 | 20022.22 |
| i13-all-ru-normal | all | ru-normal | 36 | 14 | -6031.94 | 53.21 | 18768.06 |
| i13-A-en-normal | A | en-normal | 36 | 25 | 13919.44 | 52.61 | 25727.78 |
| i13-creep-en-normal | creep | en-normal | 36 | 29 | 15006.94 | 53.99 | 30454.17 |
| i13-cap-en-normal | cap | en-normal | 36 | 23 | 7684.72 | 52.74 | 20000 |
| i13-fresh-en-normal | fresh | en-normal | 36 | 25 | 10452.78 | 56.25 | 28844.44 |
| i13-yard-en-normal | yard | en-normal | 36 | 26 | 10333.33 | 52.94 | 27191.67 |
| i13-tankfac-en-normal | tankfac | en-normal | 36 | 24 | 10225 | 52.11 | 26956.94 |
| i13-rescout-en-normal | rescout | en-normal | 36 | 25 | 11715.28 | 52.63 | 26979.17 |
| i13-indirect-en-normal | indirect | en-normal | 36 | 29 | 13275 | 52.99 | 30331.94 |
| i13-rushdef-en-normal | rushdef | en-normal | 36 | 31 | 20568.06 | 52.59 | 33743.06 |
| i13-fwd-en-normal | fwd | en-normal | 36 | 27 | 11048.61 | 54.66 | 28420.83 |
| i13-all-en-normal | all | en-normal | 36 | 26 | 10659.72 | 53.79 | 29393.06 |
| i13-A-ru-rush | A | ru-rush | 36 | 22 | 6095.83 | 52.97 | 23706.94 |
| i13-creep-ru-rush | creep | ru-rush | 36 | 18 | 1343.06 | 54.03 | 20359.72 |
| i13-cap-ru-rush | cap | ru-rush | 36 | 19 | 6627.78 | 54.57 | 24722.22 |
| i13-fresh-ru-rush | fresh | ru-rush | 36 | 18 | 1459.72 | 52.54 | 16684.72 |
| i13-yard-ru-rush | yard | ru-rush | 36 | 18 | 2169.44 | 54.62 | 21819.44 |
| i13-tankfac-ru-rush | tankfac | ru-rush | 36 | 13 | -2318.06 | 51.88 | 14454.17 |
| i13-rescout-ru-rush | rescout | ru-rush | 36 | 25 | 10447.22 | 53.57 | 27043.06 |
| i13-indirect-ru-rush | indirect | ru-rush | 36 | 18 | 3480.56 | 53.03 | 21458.33 |
| i13-rushdef-ru-rush | rushdef | ru-rush | 36 | 15 | -841.67 | 51.7 | 13670.83 |
| i13-fwd-ru-rush | fwd | ru-rush | 36 | 21 | 4294.44 | 53.05 | 19915.28 |
| i13-all-ru-rush | all | ru-rush | 36 | 10 | -6645.83 | 50.6 | 12123.61 |
| i13-A-en-rush | A | en-rush | 36 | 32 | 26154.17 | 53.68 | 39913.89 |
| i13-creep-en-rush | creep | en-rush | 36 | 27 | 19669.44 | 53.56 | 34691.67 |
| i13-cap-en-rush | cap | en-rush | 36 | 34 | 27154.17 | 53.86 | 37081.94 |
| i13-fresh-en-rush | fresh | en-rush | 36 | 33 | 22126.39 | 53.94 | 38266.67 |
| i13-yard-en-rush | yard | en-rush | 36 | 25 | 14734.72 | 53.9 | 30084.72 |
| i13-tankfac-en-rush | tankfac | en-rush | 36 | 27 | 17194.44 | 53.02 | 34043.06 |
| i13-rescout-en-rush | rescout | en-rush | 36 | 33 | 28025 | 52.89 | 37102.78 |
| i13-indirect-en-rush | indirect | en-rush | 36 | 29 | 20120.83 | 53.42 | 32480.56 |
| i13-rushdef-en-rush | rushdef | en-rush | 36 | 27 | 16661.11 | 53.3 | 29827.78 |
| i13-fwd-en-rush | fwd | en-rush | 36 | 26 | 17650 | 54.42 | 30075 |
| i13-all-en-rush | all | en-rush | 36 | 25 | 13780.56 | 54.85 | 33947.22 |

`trial_run` is the harness label, `arm` and `config` its arm and
configuration. `games` and `wins` are those of the label, `kd_mean` the mean
per game of the trade balance (`kd_value`), `income_per_s` the mean per game
of the realized income, in credits per second, that the agent's timing
events give, and `assets_value` the mean per game of the value of the
agent's standing assets at the end, the tiebreak's second figure. Every
figure is copied from `scripts/report.py --bench-compare <A label> <arm
label>` as it prints it.

Each arm against A, on all its games and on each configuration:

| versus_a | scope | wins_difference | fisher_p | kd_difference |
|---|---|---|---|---|
| creep | all | -3 | 0.80 | -3654.17 |
| creep | ru-normal | 2 | 0.81 | -4466.67 |
| creep | en-normal | 4 | 0.41 | 1087.5 |
| creep | ru-rush | -4 | 0.48 | -4752.78 |
| creep | en-rush | -5 | 0.22 | -6484.72 |
| cap | all | -5 | 0.62 | -3045.14 |
| cap | ru-normal | -2 | 0.81 | -7477.78 |
| cap | en-normal | -2 | 0.80 | -6234.72 |
| cap | ru-rush | -3 | 0.63 | 531.94 |
| cap | en-rush | 2 | 0.67 | 1000 |
| fresh | all | 2 | 0.90 | -3454.86 |
| fresh | ru-normal | 5 | 0.34 | -1688.89 |
| fresh | en-normal | 0 | 1.00 | -3466.67 |
| fresh | ru-rush | -4 | 0.48 | -4636.11 |
| fresh | en-rush | 1 | 1.00 | -4027.78 |
| yard | all | -12 | 0.18 | -6953.47 |
| yard | ru-normal | -2 | 0.81 | -8881.94 |
| yard | en-normal | 1 | 1.00 | -3586.11 |
| yard | ru-rush | -4 | 0.48 | -3926.39 |
| yard | en-rush | -7 | 0.08 | -11419.44 |
| tankfac | all | -15 | 0.09 | -6523.96 |
| tankfac | ru-normal | 0 | 1.00 | -5027.78 |
| tankfac | en-normal | -1 | 1.00 | -3694.44 |
| tankfac | ru-rush | -9 | 0.06 | -8413.89 |
| tankfac | en-rush | -5 | 0.22 | -8959.72 |
| rescout | all | 11 | 0.19 | 423.96 |
| rescout | ru-normal | 7 | 0.15 | -2322.22 |
| rescout | en-normal | 0 | 1.00 | -2204.17 |
| rescout | ru-rush | 3 | 0.62 | 4351.39 |
| rescout | en-rush | 1 | 1.00 | 1870.83 |
| indirect | all | -8 | 0.39 | -4610.07 |
| indirect | ru-normal | -5 | 0.34 | -9147.22 |
| indirect | en-normal | 4 | 0.41 | -644.44 |
| indirect | ru-rush | -4 | 0.48 | -2615.28 |
| indirect | en-rush | -3 | 0.51 | -6033.33 |
| rushdef | all | -2 | 0.90 | -2852.08 |
| rushdef | ru-normal | 4 | 0.48 | -1626.39 |
| rushdef | en-normal | 6 | 0.16 | 6648.61 |
| rushdef | ru-rush | -7 | 0.16 | -6937.5 |
| rushdef | en-rush | -5 | 0.22 | -9493.06 |
| fwd | all | -5 | 0.62 | -4224.65 |
| fwd | ru-normal | 0 | 1.00 | -3722.22 |
| fwd | en-normal | 2 | 0.79 | -2870.83 |
| fwd | ru-rush | -1 | 1.00 | -1801.39 |
| fwd | en-rush | -6 | 0.13 | -8504.17 |
| all | all | -22 | 0.01 | -9894.1 |
| all | ru-normal | -4 | 0.48 | -11201.39 |
| all | en-normal | 1 | 1.00 | -3259.72 |
| all | ru-rush | -12 | 0.01 | -12741.67 |
| all | en-rush | -7 | 0.08 | -12373.61 |

`wins_difference` and `kd_difference` are the arm's wins and mean kd less
A's, the `B - A` rows of `--bench-compare`, which takes the kd difference
from the unrounded means; `fisher_p` is the two-sided Fisher exact p-value
of the two win counts, to two decimals as `--bench-compare` prints it. The
rows of scope `all` are those of `--bench-compare` on two folders that each
hold the 144 games of an arm's four labels.

Each arm on all its games, from the same `--bench-compare` runs:

| trial_arm | games | wins | kd_mean | income_per_s | assets_value | ru_rush_difference | en_rush_difference | beats_a |
|---|---|---|---|---|---|---|---|---|
| A | 144 | 97 | 12834.72 | 53.11 | 28176.39 | - | - | - |
| creep | 144 | 94 | 9180.56 | 53.84 | 26752.78 | -4 | -5 | no |
| cap | 144 | 92 | 9789.58 | 53.65 | 24746.88 | -3 | 2 | no |
| fresh | 144 | 99 | 9379.86 | 54.61 | 27093.4 | -4 | 1 | no |
| yard | 144 | 85 | 5881.25 | 53.9 | 24522.92 | -4 | -7 | no |
| tankfac | 144 | 82 | 6310.76 | 52.46 | 23313.19 | -9 | -5 | no |
| rescout | 144 | 108 | 13258.68 | 53.02 | 28211.46 | 3 | 1 | yes |
| indirect | 144 | 89 | 8224.65 | 53.09 | 24892.01 | -4 | -3 | no |
| rushdef | 144 | 95 | 9982.64 | 52.75 | 24338.89 | -7 | -5 | no |
| fwd | 144 | 92 | 8610.07 | 53.96 | 24608.33 | -1 | -6 | no |
| all | 144 | 75 | 2940.62 | 53.11 | 23557.99 | -12 | -7 | no |

Each arm's wins are the sum of its four labels' wins, and its means those of
the four labels weighed by their games. `ru_rush_difference` and
`en_rush_difference` are the arm's wins less A's on the two rush
configurations, the `wins_difference` above, and `beats_a` is the rule's
verdict.

### The iteration-13 decision

Only the rescout arm beats A, so `[scouting] rescout` ships on and every
other switch of this section ships off.

The creep arm won 94 of 144 games against 97 for A, a Fisher p of 0.80, at a
`kd_mean` of 9,181 against 12,835: -3,654 from the unrounded means. It does
not beat A, so `[placement] creep_next_patch` ships off.

The cap arm won 92 of 144 games against 97 for A, a Fisher p of 0.62, at a
`kd_mean` of 9,790 against 12,835: -3,045 from the unrounded means. The arm
played `creep_next_patch` as well. It does not beat A, so
`[upkeep] refinery_field_cap` ships off.

The fresh arm won 99 of 144 games against 97 for A, a Fisher p of 0.90, at a
`kd_mean` of 9,380 against 12,835: -3,455 from the unrounded means. It won
more games than A, but at a lower mean kd. It does not beat A, so
`[upkeep] harvesters_to_fresh_ore` ships off.

The yard arm won 85 of 144 games against 97 for A, a Fisher p of 0.18, at a
`kd_mean` of 5,881 against 12,835: -6,953 from the unrounded means. The arm
played `creep_next_patch` as well. It does not beat A, so
`[expansion] second_yard` ships off.

The tankfac arm won 82 of 144 games against 97 for A, a Fisher p of 0.09, at
a `kd_mean` of 6,311 against 12,835: -6,524 from the unrounded means. On
ru-rush it won 9 games fewer than A. It does not beat A, so
`[producers] tank_factories` ships off.

The rescout arm won 108 of 144 games against 97 for A, a Fisher p of 0.19,
at a `kd_mean` of 13,259 against 12,835: +424 from the unrounded means. On
ru-rush it won 3 games more than A and on en-rush 1 more. It beats A, so
`[scouting] rescout` ships on.

The indirect arm won 89 of 144 games against 97 for A, a Fisher p of 0.39,
at a `kd_mean` of 8,225 against 12,835: -4,610 from the unrounded means. The
arm played `rescout` as well. It does not beat A, so
`[scouting] indirect_route` ships off.

The rushdef arm won 95 of 144 games against 97 for A, a Fisher p of 0.90, at
a `kd_mean` of 9,983 against 12,835: -2,852 from the unrounded means. On
ru-rush it won 7 games fewer than A, more than the 3 the rule allows. It
does not beat A, so `[scouting] rush_detection` ships off and
`[opening] defense_on_threat` ships off.

The fwd arm won 92 of 144 games against 97 for A, a Fisher p of 0.62, at a
`kd_mean` of 8,610 against 12,835: -4,225 from the unrounded means. It does
not beat A, so `[combat] forward_hold` ships off.

The all arm won 75 of 144 games against 97 for A, a Fisher p of 0.01, at a
`kd_mean` of 2,941 against 12,835: -9,894 from the unrounded means. On
ru-rush it won 12 games fewer than A and on en-rush 7 fewer. It does not
beat A, so the rule's clause for the all arm does not apply, and only the
switches whose own arm beats A ship.

The configuration the repository ships, `config/redalert/tuning.toml`,
therefore has `[scouting] rescout` on and every other switch of this
section off. Each switch, the arm that measured it, the arms that played it
on, and its value as shipped:

| trial_switch | arm | played_on | shipped |
|---|---|---|---|
| `[placement] creep_next_patch` | creep | creep, cap, yard, all | `false` |
| `[upkeep] refinery_field_cap` | cap | cap, all | `false` |
| `[upkeep] harvesters_to_fresh_ore` | fresh | fresh, all | `false` |
| `[expansion] second_yard` | yard | yard, all | `false` |
| `[producers] tank_factories` | tankfac | tankfac, all | `false` |
| `[scouting] rescout` | rescout | rescout, indirect, all | `true` |
| `[scouting] indirect_route` | indirect | indirect, all | `false` |
| `[scouting] rush_detection` | rushdef | rushdef, all | `false` |
| `[opening] defense_on_threat` | rushdef | rushdef, all | `false` |
| `[combat] forward_hold` | fwd | fwd, all | `false` |
