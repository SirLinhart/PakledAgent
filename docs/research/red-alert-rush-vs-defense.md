# Red Alert 1v1: rush validity and the cost of defence

Read-only research from the rule files. Scratch model: `C:\Projects\UCLA-Competition\tmp\ra_sim*.py`.

**Tags:** [F] read directly from a file, [C] computed from [F] values, [M] engine default recalled from memory because the source file is not in the checkout, [J] judgment.

**Path aliases**
- `P` = `C:\Projects\UCLA-Competition\docs\reference\altruagent\redalert.md`
- `R` = `C:\Projects\UCLA-Competition\docs\reference\research\redalert\OpenRA\mods\ra\rules`
- `W` = `C:\Projects\UCLA-Competition\docs\reference\research\redalert\OpenRA\mods\ra\weapons`
- `E` = `C:\Projects\UCLA-Competition\docs\reference\research\redalert\OpenRA\OpenRA.Mods.Common\Traits`
- `G` = `C:\Projects\UCLA-Competition\docs\reference\research\redalert\OpenRA-wiki\Red-Alert-Multiplayer-Guide-—-Fundamentals.md`

**Data version.** The OpenRA checkout is the development branch (commit `b6fc03f`, 2026-10-03, `VERSION` = `{DEV_VERSION}`) and is sparse: 203 `.cs` files, no `OpenRA.Game`, no `Mods.Cnc`. Every cost and prerequisite in `P` section 8 matches `R`, which is a good sign, but HP, `Versus` and reload values are balance-sensitive. Check them against `get_game_config("red_alert").rules` before relying on a close margin.

---

## 0. Global rules that drive everything

| Rule | Value | Source |
|---|---|---|
| Build time | `ticks = Cost x 60%` so **0.024 s per credit** at 25 ticks/s | [F] `E\Buildable.cs` `BuildDuration = -1` (use cost), `BuildDurationModifier = 60`; `E\Player\ProductionQueue.cs` `GetBuildTime` |
| Exceptions to 0.024 | **MCV only**: `BuildDurationModifier: 50` gives 40 s. No other `BuildDuration*` key exists in `R\*.yaml` | [F] `R\vehicles.yaml` MCV `Buildable` |
| Extra production buildings | Queue speeds up: Infantry/Building/Defense 100, 86, 75, 67, 60, 55, 50 %; Vehicle 100, 75, 60, 50 % | [F] `R\player.yaml` `SpeedUp: True`, `BuildTimeSpeedReduction`; `E\Player\ClassicProductionQueue.cs` |
| Low power | Production takes 3x as long | [F] `R\player.yaml` `LowPowerModifier: 300` |
| Queues | Building, Defense, Infantry, Vehicle are separate and run in parallel; cost is paid progressively; cancelling refunds what was paid | [F] `R\player.yaml`; `ProductionQueue.cs` ("Refund what has been paid") |
| Speed | `cells/s = Mobile.Speed x 25 / 1024` on Clear. Foot: Road 111 %, Rough/Ore 89 %. Wheeled (jeep, ftrk, harv, mcv): Rough 50 %, Road 125 %. Tracked: Rough 88 % | [F] `R\world.yaml` `Locomotor@*` `TerrainSpeeds`; `E\Mobile.cs` `MovementSpeedForCell` |
| Infantry prone | A hit of type `TriggerProne` puts infantry prone for 50 ticks; damage of type `Prone50Percent` is then halved. Nearly every weapon carries both types | [F] `R\defaults.yaml` `^Soldier` `TakeCover`. [M] the triggering hit itself lands at full damage, and prone infantry move at half speed |
| Sell | Refund = 50 % of sell value x HP fraction. Infantry worth 40 % of value spawn, only if HP >= 30 % | [F] `E\Sellable.cs` `RefundPercent = 50`; `E\SpawnActorsOnSell.cs` `ValuePercent = 40`, `MinHpPercent = 30` |
| What must die | Short-game set is every `^Building` plus the MCV. **Defences are excluded** (`^Defense` `MustBeDestroyed: RequiredForShortGame: false`), as are silos and camo pillboxes | [F] `R\defaults.yaml`, `R\structures.yaml` |
| Maps | Upstream `sidestep`, `trapped`, `shadowfiend-II` have spawns 51.8, 56.6 and 87.8 cells apart and no rule overrides. 8-direction path lower bounds are 54.7, 56.6 and 92.0 cells | [F]/[C] `mods\ra\maps\*.oramap` `map.yaml` |

**Is 0.024 s/credit safe for every item?** Yes for every buildable except the MCV (0.020). It assumes one production building of that type and full power.

---

## 1. Stat table

**Formula** [C]: `DPS = Damage x Burst x Versus% / ((ReloadDelay + (Burst-1) x BurstDelay) / 25)`. Damage and `Versus` come from `W\*.yaml`; HP, armour, speed and cost from `R\infantry.yaml`, `R\vehicles.yaml`, `R\structures.yaml`. The "None" column is the first hit; halve it for sustained fire on prone infantry. Default `BurstDelays` of 5 ticks is [M] and affects `105mm` and `M60mg`.

| Unit | Cost | Needs | HP | Armour | cells/s | Weapon (range) | None | Light | Heavy | Wood | Concrete |
|---|---|---|---|---|---|---|---|---|---|---|---|
| e1 rifle | 100 | barracks | 5000 | None | 1.32 | M1Carbine (5) | 1875 | 500 | 125 | 375 | 125 |
| e2 grenadier | 150 | barr, Soviet only | 5000 | None | 1.66 | Grenade (4) | 1500 | 625 | 625 | 2500 | 2500 |
| e3 rocket | 300 | barracks | 4500 | None | 1.32 | Dragon (5) | 250 | 850 | 2500 | 1850 | 1250 |
| e4 flamer | 300 | barr + ftur | 4000 | None | 1.32 | Flamer (5) | 4102 | 2344 | 1172 | 4688 | 586 |
| dog | 200 | kenn ($200), Soviet | 1800 | None | 2.44 | DogJaw (3) | kills infantry outright | 0 | 0 | 0 | 0 |
| e6 engineer | 400 | barracks | 2500 | None | 1.32 | none | - | - | - | - | - |
| jeep | 500 | weap, Allies | 15000 | Light | 4.00 | M60mg (4) | 3750 | 750 | 250 | 250 | 250 |
| 1tnk light | 700 | weap, Allies | 23000 | Heavy | 2.76 | 25mm (4.75) | 952 | 3452 | 1429 | 1548 | 952 |
| 2tnk medium | 850 | weap + fix, Allies | 46000 | Heavy | 1.76 | 90mm (4.75) | 600 | 1500 | 2300 | 1500 | 1000 |
| 3tnk heavy | 1150 | weap + fix, Soviet | 60000 | Heavy | 1.56 | 105mm (4.75) | 800 | 2000 | 3067 | 2000 | 1333 |
| apc (5 seats) | 850 | weap, Soviet | 35000 | Heavy | 3.12 | M60mg (4) | 3750 | 750 | 250 | 250 | 250 |
| ftrk flak | 600 | weap, Soviet | 15000 | Light | 2.76 | FLAK-23-AG (6) | 2000 | 3000 | 500 | 500 | 1000 |
| v2rl | 900 | weap + dome, Soviet | 20000 | Light | 1.76 | SCUD (4-10) | 4709 | 3663 | 2093 | 3924 | 5233 |
| arty | 850 | weap + dome, Allies | 10000 | Light | 1.76 | 155mm (4-12) | 4059 | 4059 | 1691 | 2706 | 3382 |
| pbox | 600 | tent | 40000 | Heavy | - | Vulcan via garrisoned e1 (6) | 1 rifle per 1.4 s | 2143 | 857 | 2143 | 857 |
| hbox (cloaked) | 750 | tent | 40000 | Heavy | - | as pbox | as pbox | | | | |
| gun turret | 800 | tent | 40000 | Heavy | - | TurretGun (6.5) | 1000 | 3750 | 5000 | 2500 | 2500 |
| ftur | 600 | barr | 40000 | Heavy | - | FireballLauncher (5) | 2 rifles per 3.6 s, plus splash | 4167 | 2083 | 4167 | 1667 |
| tsla | 1200 | weap | 40000 | Heavy | - | TeslaZap (7) | 3 infantry per ~5 s | 5952 | 5952 | 3571 | 5952 |

| Target | Cost | HP | Armour | Power | Notes |
|---|---|---|---|---|---|
| fact | 2000 | 150000 | Wood | 0 | `BaseProvider Range: 16c0` |
| powr | 300 | 40000 | Wood | +100 | `ScalePowerWithHealth`: damage alone can cause low power |
| tent / barr | 500 | 60000 | Wood | -20 | |
| proc | 1400 | 90000 | Wood | -30 | free harvester (`FreeActor: HARV`) |
| weap | 2000 | 150000 | Wood | -30 | needs proc only, **not** barracks |
| harv | 1100 | 60000 | Heavy | - | 1.76 cells/s, 20 bales x $25 = $500 per load |
| mcv | 2000 | 60000 | Light | - | 1.46 cells/s |

Defence power drain [F]: pbox -20, hbox -20, ftur -20, gun -40, tsla -80. Only tsla is disabled by low power (`^DisableOnLowPowerOrPowerDown`); pbox, gun and ftur keep firing.

Table caveats:
- Tesla cycle (3 zaps, then `ReloadDelay: 120`) is taken from `R\structures.yaml` `AttackTesla`; the trait source is absent, so the ~5 s cycle is [M].
- Splash is ignored everywhere, which understates ftur, grenades and flamers against clumps.
- RedEye (e3 primary) is anti-air only; Dragon is the ground weapon.

**Damage per second per dollar against Wood (buildings)** [C]: e2 16.7, e4 15.6, e3 6.2, v2 4.4, e1 3.75, arty 3.2, 1tnk 2.2, 2tnk 1.8, 3tnk 1.7, ftrk 0.8, jeep/apc 0.3-0.5. Infantry kill buildings; tanks survive defences.

---

## 2. Rush timelines

All times are engine minimums from MCV deploy at t=0 with instant `place` orders. Agent latency and the make animation add to every step. Walk times use the platform's 52 / 57 / 88 cells on Clear terrain.

**Income model [J].** A harvester load is $500 [F]. Filling takes 20 x `BaleLoadDelay 4` = 3.2 s and unloading 0.8 s [F `E\Harvester.cs`, `R\vehicles.yaml`]. Travel and docking are map-dependent, so I use **$15-30/s per harvester** and show the range. First income arrives roughly 15 s after a refinery is placed.

### a. Pure rifle rush (powr, barracks, continuous e1)

Barracks done at 19.2 s. Rifle N exists at `19.2 + 2.4N` [C].

| Time | 0:45 | 1:00 | 1:30 | 2:00 |
|---|---|---|---|---|
| Rifles built | 10 | 17 | 29 | **42 (cash is exactly $0)** |

Cash: 5000 - 300 - 500 = 4200, which is 42 rifles. The build has no refinery, so it never earns again.

| Rifles arriving | 52 cells (walk 39.4 s) | 57 cells (43.2 s) | 88 cells (66.8 s) |
|---|---|---|---|
| 10 | 1:23 | 1:26 | 1:50 |
| 15 | 1:35 | 1:38 | 2:02 |
| 20 | 1:47 | 1:50 | 2:14 |
| 42 (everything) | 2:39 | 2:43 | 3:07 |

Rallying each rifle forward as it spawns gives the same arrival time for rifle N, but they trickle in.

### b. Variants

- **Grenadiers (Soviet).** e2 N exists at `19.2 + 3.6N`; 28 fit in $4,200 by 2:00. Walk is 31.3 / 34.3 / 53.0 s. Ten e2 arrive at **1:27 / 1:30 / 1:48** and deal 25,000 HP/s to buildings, equal to 67 rifles. Range is 4, shorter than every defence.
- **Rifle + rocket (either side).** 12 e1 + 4 e3 costs $2,400 and is ready at 1:17. Building damage is 11,900 HP/s against 9,000 for 24 rifles; damage to a pillbox is 11,500 HP/s against 3,000. Rockets cost the same $41.7/s of queue time as rifles, so the mix is no slower per dollar; it just has fewer bodies.
- **Flamers (Soviet)** need a flame tower first ($600), so they are not a first-wave option.

### c. Vehicle rush

Fastest chain: powr (7.2 s), proc (40.8 s), weap (**88.8 s**). No barracks is needed. Cash left is **$1,300**, then harvester income from about 0:56.

| Unit | Build | First built | First arrives (52 / 88) | By 3:00 | By 4:00 |
|---|---|---|---|---|---|
| 1tnk | 16.8 s | 1:46 | 2:04 / 2:18 | 4-5 | 5-9 |
| jeep | 12.0 s | 1:41 | 1:54 / 2:03 | 6-7 | 8-12 |
| ftrk | 14.4 s | 1:43 | 2:02 / 2:15 | 5-6 | 6-10 |
| apc | 20.4 s | 1:49 | 2:06 / 2:17 | 3-4 | 4-7 |

Counts are the smaller of the time limit and the money limit, across the $15-30/s income range. Tank production costs $41.7/s, more than one harvester earns, so money is the binding limit. Without any income the $1,300 buys one light tank. Four light tanks exist at 2:36 at the earliest (this needs $1,500 of income by then, the bottom of the range) and arrive at 2:55-3:08.

### d. Cheese the interface allows

| Trick | Possible? | Timing and cost | Notes |
|---|---|---|---|
| Drive the MCV forward and deploy near the enemy | **Yes by the rules.** `place` honours `at` when valid (`P` section 5) | MCV walk 26 / 29 / 50 s to about 14 cells short. Then powr +7.2 s, tent +12 s, first pbox +14.4 s: **about 1:00 / 1:03 / 1:24**, then one pbox per 14.4 s, up to 7 from $4,200 | Buildings must be within 16 cells of the fact [F] and near an own building that gives buildable area; defences do not give it [F]. Adjacency distance is [M] 2 cells. No ore, so no income. MCV killed in transit is -2000 and probably the match |
| Sell the construction yard for an all-in | **Yes** (`sell` exists; fact `Sellable`) | +$1,000, plus a guaranteed e1 and e6, plus up to $300 of e1/technicians. About 11-14 rifle-equivalents | Loses 2000 `assets_value` and all further building. Never sell the last non-defence building: that is defeat |
| Sell other buildings | Yes | weap $1,000 + one guaranteed e1 + up to $700 of random e1/technicians; barr $250 + 1-2 e1; powr $150 + e1; **proc only $150** (`CustomSellValue: 300`); pbox $200, no infantry; ftur / gun / tsla $300 / $400 / $600 + one e1 | 1x1 defences have one tile, so exactly one e1 spawns [C from `SpawnActorsOnSell.cs`] |
| Hold a finished defence unplaced, place on contact, cancel for refund | Yes | $600 tied up, fully refundable | Recommended by the human guide (`G` line 219) |
| Engineer capture | **No.** `Captures` exists in `R\infantry.yaml` E6 but `P` section 5 has no capture or enter-building order | - | The engineer has no weapon. Oil derricks on the maps are capture-only, so irrelevant |
| Dogs | Rules allow (kennel $200, dog $200 / 4.8 s) | - | Dogs share the Infantry queue with rifles, cannot hurt buildings or vehicles, and die to two rifle hits |
| APC drop | Yes (`enter_transport`, `unload`) | apc + 5 e2 = $1,600. Needs barracks and war factory (done 1:41), so earliest arrival is about 2:18 on 52 cells, and it needs $800 of income | 5 e2 kill a power plant in 3.2 s and a fact in 12 s |

---

## 3. Kill times

Seconds of uninterrupted fire, no attacker losses, no repair [C]. Rifle-seconds needed: fact 400, powr 107, barracks 160, proc 240.

| Attackers | fact | powr | barracks | proc | harvester | pbox |
|---|---|---|---|---|---|---|
| 10 e1 | 40.0 | 10.7 | 16.0 | 24.0 | 48.0 | 32.0 |
| 15 e1 | 26.7 | 7.1 | 10.7 | 16.0 | 32.0 | 21.3 |
| 20 e1 | 20.0 | 5.3 | 8.0 | 12.0 | 24.0 | 16.0 |
| 10 e2 | 6.0 | 1.6 | 2.4 | 3.6 | 9.6 | 6.4 |
| 4 / 8 1tnk | 24.2 / 12.1 | 6.5 / 3.2 | 9.7 / 4.8 | 14.5 / 7.3 | 10.5 / 5.2 | 7.0 / 3.5 |
| 4 / 8 2tnk | 25.0 / 12.5 | 6.7 / 3.3 | 10.0 / 5.0 | 15.0 / 7.5 | 6.5 / 3.3 | 4.3 / 2.2 |
| 4 / 8 3tnk | 18.8 / 9.4 | 5.0 / 2.5 | 7.5 / 3.8 | 11.2 / 5.6 | 4.9 / 2.4 | 3.3 / 1.6 |

**Can a rush end the game?** A greedy base at about 1:30 is fact + powr + barracks + 2 proc = 430,000 Wood HP.
- 20 rifles need **57 s** of free fire plus walking between buildings.
- 10 grenadiers need **17 s**.
- 4 light tanks need **69 s**.

Repair works against this: `RepairStep: 700` [F], interval [M] 24 ticks, so about 730 HP/s per building, which cancels two rifles. The defender can also keep placing $300 power plants while the fact lives, so the fact has to die first.

---

## 4. Defence value

**Model** [C]: `ra_sim2.py`, tick-level, with prone and range-gap free fire. It has no splash, no repair, perfect focus fire, and all attackers arriving together. Where two numbers are given, the first is attackers shooting the structure first and the second is attackers shooting defending rifles first. Real attack-move targeting lies between; treat thresholds as +/-25 %.

| Defence ($, power, needs) | Rifles to destroy it alone | 10 / 15 / 20 rifles into it alone | Rifles needed with 5 defending e1 | Light tanks to destroy it alone | 4 light tanks into it |
|---|---|---|---|---|---|
| pbox (600, -20, tent) | 23 | all die; pbox left at 81 / 56 / 19 % | 25-32 | 2, none lost | dies in 7 s, 0 tanks lost |
| ftur (600, -20, barr) | 20 | 10 and 15 all die (76 / 42 % left); 20 kill it, losing 16 | 21-29 | 2, losing 1 | dies in 7 s, 0 lost |
| gun (800, -40, tent) | 8 | 10 rifles kill it, losing 4 | 12-28 | 4, losing 2 | dies, 2 tanks lost |
| tsla (1200, -80, weap) | 22 | all die (84 / 57 / 17 % left) | 25-36 | 4, losing 3 | dies, 3 tanks lost |

Cost exchanged, defence alone: a pillbox trades $0 for $1,000 / $1,500 / $2,000 of rifles. A turret trades $800 for $400.

**What each is worthless against**
- pbox and ftur against tanks: two light tanks ($1,400) kill either; four lose nothing.
- gun against infantry: 0.11 rifle kills per second.
- tsla without power, or before a war factory exists.
- Any single defence against rockets: 8 e3 kill a pbox in 3 s, losing 2.
- Any defence that does not cover the building under attack. Range is 5-7 cells and a base is wider than that.

Mixed forces in the model: 12 e1 + 4 e3 ($2,400) narrowly fail against a lone pillbox when it shoots the rockets first (pillbox left at 1 %), so the result flips on targeting; they lose everything to pbox + 5 e1. 10 e1 + 10 e2 ($2,500, Soviet) **beat pbox + 5 e1 or ftur + 5 e1**, losing $500-1,300.

### Minimum "safe against a rifle rush by time T"

Rifles that can have arrived by T: `floor((T - 19.2 - walk) / 2.4)`, capped at 42 [C].

| T | 52 cells | 57 cells | 88 cells | Holds in the model |
|---|---|---|---|---|
| 1:30 | 13 | 11 | 1 | one pbox or ftur ($600) |
| 2:00 | 25 | 24 | 14 | pbox or ftur + 5 e1 ($1,100); 25 is the model's break point, so this is borderline on the 52-cell map |
| 2:30 | 38 | 36 | 26 | 2 defences + 10 e1 ($2,200), only if the rusher targets badly |
| 3:00 | 42 | 42 | 39 | as above; by now income should fund more rifles than the rusher has |

Allies use pbox, Soviets ftur: same $600, same 14.4 s, same -20 power, earliest ready at **0:33.6**, before any rush can arrive.

**Cost to the economy opening** (powr, barracks, proc, proc, powr, weap; engine times 7.2 / 19.2 / 52.8 / 86.4 / 93.6 / 141.6 s; total $5,900, so it already leans on $900 of income) [C]:
- **Zero build-time delay.** Defences use the Defense queue and rifles the Infantry queue.
- Power fits: barracks 20 + proc 30 + proc 30 + pbox 20 = 100 from the first power plant. A turret (-40) does not fit until the second.
- Cash: $600 is 43 % of a refinery; $1,100 with 5 rifles is one harvester. With that $1,100 the war factory needs $2,000 of income by 2:22, against an estimated $1,700-3,400 earned by then. At the bottom of the range the war factory slips by roughly 10-20 s; otherwise there is no delay.

The human guide's standard two-refinery opener fields about 18 rifles from the first barracks (`G` lines 192-193), and its greedy opener keeps a pillbox or flame tower built but unplaced (`G` line 219).

---

## 5. Tiebreak arithmetic

**What the engine does** [F `E\Player\PlayerStatistics.cs`]:
- `KillsCost` and `DeathsCost` change only when an actor is *killed*, by its `Valued.Cost`.
- A **sold** building is disposed, not killed: no `deaths_cost` for the seller, no `kills_cost` for the enemy, and `assets_value` drops by its cost.
- `assets_value` is the summed cost of living buildings, vehicles and soldiers. **Unspent cash is not counted.**
- The free harvester counts 1100 in assets and 1100 when killed.

**What I cannot tell:** whether the platform's `military.*` fields are these engine counters. The names match one-for-one, but `P` section 7 does not define sale accounting.

| Outcome | Rusher kills - deaths |
|---|---|
| 10 / 15 / 20 rifles die to a defence, killing nothing | -1,000 / -1,500 / -2,000 |
| 20 rifles kill a power plant, then die | 300 - 2,000 = **-1,700** |
| 20 rifles kill a refinery, then die | 1,400 - 2,000 = **-600** |
| 20 rifles kill refinery + its harvester, then die | 2,500 - 2,000 = **+500** |
| 20 rifles kill the construction yard, then die | 2,000 - 2,000 = 0 |
| 10 grenadiers kill refinery + power plant, then die | 1,700 - 1,500 = +200 |
| 4 light tanks kill pbox + harvester + refinery, lose 0-1 | **+2,400 to +3,100** |
| 4 light tanks into a turret / a tesla coil | 800 - 1,400 = -600 / 1,200 - 2,100 = -900 |
| 42-rifle all-in fails | -4,200, with about $800 of assets left |
| Forward MCV dies before deploying | -2,000 and no base |

The defender's score moves by the same amounts with the opposite sign. If neither side trades, the first key ties at 0 and `assets_value` decides, so a turtle that keeps spending beats an attacker who has lost anything.

Two consequences, if the engine counters are what is scored:
1. Selling a building that is about to die denies the enemy its kill value.
2. Spend all cash before the clock ends.

---

## 6. Verdict [J]

**(i) Is a rush a credible outright win?**
- *Against a greedy opener with no units and no defence:* a rifle rush is credible only if the opponent does not react. 20 rifles at 1:47-2:14 need about a minute of free fire, and the defender has a barracks at 0:19 and can train rifles on the spot at the same 2.4 s rate. A **Soviet grenadier rush is credible**: 10 e2 at 1:27-1:48 raze the same base in about 17 s of firing.
- *Against one defence plus about 5 rifles:* rifle or grenadier rushes of up to about 25 units lose everything for almost nothing. Only a concentrated 40-unit all-in or a Soviet e1 + e2 mix breaks it, and that arrives at 2:30-3:10 with the rusher's economy at zero.

**(ii) Cheapest insurance.** One pillbox (Allies) or flame tower (Soviets), built at 0:33 and held unplaced or placed centrally, plus 5 rifles: **$1,100, no build-order delay, no extra power plant**. That makes rushes of up to about 25 infantry unprofitable. It does nothing against light tanks from 2:05 onward; the answer there is a turret ($800, needs the second power plant) or 4-5 rocket soldiers ($1,200-1,500) by about 2:45.

**(iii) Best aggressive option under the tiebreak.** Target-selective raids that can leave: Allied light tanks (4 by 2:36) against harvesters, refineries and pillbox-only bases. They out-run everything except jeeps and lose nothing to pbox or ftur. For Soviets, a small grenadier group against buildings outside defence range. Committing infantry into a defence is the worst trade available.

**(iv) Needs a live test**
1. Whether `military.kills_cost` / `deaths_cost` / `assets_value` follow the engine counters, especially for sold buildings.
2. Real harvester income per minute on each map.
3. Real path lengths and terrain; the walk times are straight-line on Clear.
4. Attack-move target choice, and whether defences need `set_stance` or `attack` to shoot buildings.
5. Whether `place` with `at` really lets buildings chain toward the enemy, and the adjacency distance.
6. Tournament-build HP, `Versus` and reload values against this dev-branch data.
7. Repair rate and cost.
8. Splash and prone behaviour (flame tower and grenades against clumps).
9. Agent latency per build step.
