# Red Alert units and defenses: cost-benefit and matchup odds

Read-only research from OpenRA's rule files and engine source; no game was run. Scratch code: `C:\Projects\UCLA-Competition\tmp\unit-matchups\` (`data.py` rules, `hit.py` accuracy and splash, `sim.py` fight model, `run.py`, `mixes.py`, `report.py`). Chart data: `docs\research\red-alert-unit-matchups\*.csv`.

**Tags:** **[F]** rule file (file and key), **[E]** engine source, **[C]** computed, **[M]** modelling assumption. **Paths:** `R` = `docs\reference\research\redalert\OpenRA\mods\ra\rules`, `W` = `...\mods\ra\weapons`, `S` = `tmp\openra-local\engine-src\OpenRA`.

**Data version.** `R`/`W` are the upstream development branch (`b6fc03f`, 2026-10-03); `S` is `8a5d224` (2026-04-13). Their `weapons` folders are identical and their rules differ only cosmetically. The tournament build may differ. The most version-sensitive values are the `Versus` tables, the Tesla timings, the SCUD falloff (1000 % at the centre), prone, and the lobby tech level, which decides whether tech-centre units exist. Check them against `get_game_config("red_alert").rules` before trusting a close result.

## 0. Engine rules that drive the numbers

| Rule | Value | Source |
|---|---|---|
| Time | 25 ticks/s. Cells/s = `Mobile.Speed x 25 / 1024` (e1: 54 -> 1.32) | [F] `Mobile.Speed`; [E] `S\OpenRA.Mods.Common\Traits\Mobile.cs` |
| Fire cycle | After each burst shot wait `BurstDelays` (default **5** ticks), after the last `ReloadDelay`: cycle = `ReloadDelay + (Burst-1) x BurstDelay`; delay 0 fires in the same tick | [E] `S\OpenRA.Game\GameRules\WeaponInfo.cs` (`BurstDelays = [5]`), `S\OpenRA.Mods.Common\Traits\Armament.cs` |
| Armaments | Every armament valid for the target fires (Mammoth: 120mm at vehicles, Tusk at infantry) | [E] `AttackBase.cs`; [F] `W\ballistics.yaml` 120mm `InvalidTargets: Air, Infantry`; `W\missiles.yaml` MammothTusk `ValidTargets` |
| Damage | `Damage x Versus[armour]% x falloff%`; armour absent from `Versus` = 100 % | [E] `Warheads\DamageWarhead.cs` |
| Splash | Falloff from the victim's **hit-shape edge**, steps of `Spread`; defaults `Falloff 100,37,14,5,0`, `Spread 43`. Infantry circle 128, vehicles 426, buildings footprint | [E] `Warheads\SpreadDamageWarhead.cs`, `HitShapes\Circle.cs`; [F] `R\defaults.yaml` `^Infantry HitShape`, `^1x1Shape` |
| Inaccuracy | Bullet: offset per axis = mean of 2 uniforms x `Inaccuracy x dist/range`. Missile: absolute `Inaccuracy`, bursts within `CloseEnough` | [E] `Projectiles\Bullet.cs`, `Missile.cs`, `Util.GetProjectileInaccuracy` |
| Prone | A `TriggerProne` hit makes infantry prone for **50 ticks** from the last hit; prone: `Prone50Percent` damage x 0.5, speed x 0.5. The triggering hit is full. Dogs and thieves never go prone | [F] `R\defaults.yaml` `^Soldier TakeCover`, `R\infantry.yaml` DOG/THF `-TakeCover`; [E] `Traits\Infantry\TakeCover.cs` |
| Tesla coil | 3 charges; each **new target** costs a 22-tick charge, zaps 3 ticks apart, recharge 120 ticks after the last zap: 3 zaps / 148 ticks on one target | [F] `R\structures.yaml` TSLA `AttackTesla`; [E] `S\OpenRA.Mods.Cnc\Traits\Attack\AttackTesla.cs` |
| Pillbox | Fires its free e1's garrisoned Vulcan: 6 warheads of 1000 x `None 200`, 2 ticks apart, so one volley kills a rifleman; the e1 dies with the box | [F] `R\structures.yaml` PBOX `Cargo`, `AttackGarrisoned`; `W\smallcaliber.yaml` Vulcan; [E] `Cargo.cs` `EjectOnDeath = false` |
| Dog | Leaps from 3 cells at 426/tick; DogJaw 100000, infantry only | [F] `R\infantry.yaml` DOG, `W\other.yaml` DogJaw; [E] `Mods.Cnc\Traits\Attack\AttackLeap.cs` |
| Crush | Tracked, heavy-tracked and heavy-wheeled vehicles crush infantry; a mobile victim dodges 76 % of attempts | [F] `R\world.yaml` `Locomotor@* Crushes`; [E] `Traits\Crushable.cs` `WarnProbability = 75` |
| FireDelay | e2 throws 15 ticks and e4 flames 8 ticks after deciding; a shooter killed in between never fires | [F] `R\infantry.yaml`; [E] `Armament.cs` delayed actions |

**DPS** [C] = sum over valid armaments and warheads of `Damage x Versus/100 x H x Burst x 25 / cycle`. `H` is the expected falloff of a shot at that target class at maximum range: 0.53 for the 155mm against infantry, 0.62 for the Grenade, 1.0 for instant-hit guns. "None 1st" means an upright target; "prone" halves `Prone50Percent` warheads. One-shot weapons overkill infantry, so anti-infantry value comes instead from **e1 kills/min**: a stream of fresh, non-firing riflemen at maximum range, with prone, overkill, reload and Tesla charge included. **Effective anti-infantry DPS per $100** = kills/s x 5000 / (cost/100).

**Model [M].**
- *Damage:* deterministic expected values, so there are no misses, at 25 ticks/s.
- *Targeting:* **focus fire** means the whole side shoots the front-most enemy until it dies. **Spread fire** means unit *i* shoots enemy *i mod n*. Defenses shoot the nearest enemy in range.
- *Range closing:* the shorter-ranged side walks straight in under fire, at half speed while prone. Min-range units back off.
- *Clumps:* hex discs, infantry 512 apart (about 4 per cell) and vehicles 1024. No pair is ever counted closer than 384 (infantry) or 900 (vehicles). Each neighbour's splash is the expected falloff at its distance from the aim point, so the share of a clump hit grows with N and `Spread`.
- *Jitter:* each group fight is run under 5 geometry jitters (spacing x 0.88 to 1.12, lateral shift), reporting the majority winner and mean survivors.
- *Cost-equal:* the fractional last unit has its HP and damage scaled. Tanya is limited to one by `BuildLimit: 1` [F].
- *Not modelled:* crush (section 3), turning, pathing, terrain, veterancy, repair, friendly splash.
- *Out of scope:* aircraft and ships are excluded. The platform cannot order capture, infiltration, C4 or support powers.


## 1. Per-item tables

**Needs** = `Buildable.Prerequisites` [F] besides the producer (barracks or war factory). **Tech ctr** = needs `atek`/`stek`. Tech level is in `items.csv` (tech-centre items need lobby level `high`). Sub-faction items exist only for that country. Concrete is wall armour; defenses are Heavy; Air = DPS against flying aircraft (all Light). The dog's bite, Tanya, the Tesla coil and the shock trooper all one-shot infantry, so for them use e1 kills/min rather than the None column.

### 1a. Combat units and defenses [F] stats, [C] DPS by armour class

| Item | Side | Needs | Tech ctr | Cost | HP | Armour | Cells/s | Weapon (range, cells) | None 1st / prone | Light | Heavy | Wood | Concrete | Air | e1 kills/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **e1** | A+S | barracks |  | 100 | 5,000 | None | 1.32 | M1Carbine r5 | 1,875 / 938 | 500 | 125 | 375 | 125 | - | 12.7 |
| **e3** | A+S | barracks |  | 300 | 4,500 | None | 1.32 | RedEye r7.5 min0.5; Dragon r5 min0.5 | 241 / 121 | 850 | 2,500 | 1,850 | 1,250 | 1,200 | 2.8 |
| **e7** | A | tent, atek | yes | 1,800 | 10,000 | None | 1.66 | Colt45 r7 | 35,714 / 17,857 | 0 | 0 | 0 | 0 | - | 215.1 |
| **e2** | S | barr |  | 150 | 5,000 | None | 1.66 | Grenade r4 | 934 / 467 | 606 | 606 | 2,500 | 2,498 | - | 8.4 |
| **e4** | S | barr, ftur |  | 300 | 4,000 | None | 1.32 | Flamer r5 | 2,054 / 1,027 | 1,937 | 968 | 4,687 | 547 | - | 11.8 |
| **dog** | S | kenn |  | 200 | 1,800 | None | 2.44 | DogJaw r3 | kill per bite | 0 | 0 | 0 | 0 | - | 150.8 |
| **shok** | S | barr, stek, tsla (russia only) | yes | 350 | 5,000 | None | 1.32 | PortaTesla r6 | 16,071 / 8,036 | 1,607 | 964 | 1,173 | 1,607 | - | 22.4 |
| **jeep** | A | - |  | 500 | 15,000 | Light | 4.0 | M60mg r4 | 3,750 / 1,875 | 750 | 250 | 250 | 250 | - | 25.5 |
| **1tnk** | A | - |  | 700 | 23,000 | Heavy | 2.76 | 25mm r4.75 | 952 / 476 | 3,452 | 1,429 | 1,548 | 952 | - | 6.0 |
| **2tnk** | A | fix |  | 850 | 46,000 | Heavy | 1.76 | 90mm r4.75 | 600 / 300 | 1,500 | 2,300 | 1,500 | 1,000 | - | 6.2 |
| **arty** | A | dome |  | 850 | 10,000 | Light | 1.76 | 155mm r12 min4 | 2,169 / 1,084 | 3,119 | 1,300 | 2,703 | 2,933 | - | 17.8 |
| **ctnk** | A | atek (germany only) | yes | 1,350 | 40,000 | Light | 2.1 | APTusk r6 min0.5 | 402 / 201 | 1,417 | 4,167 | 3,083 | 2,083 | - | 3.7 |
| **stnk** | A | atek (france only) | yes | 1,000 | 35,000 | Light | 3.12 | APTusk.stnk r6 min0.5 | 121 / 60 | 425 | 1,250 | 925 | 625 | - | 1.5 |
| **apc** | S | - |  | 850 | 35,000 | Heavy | 3.12 | M60mg r4 | 3,750 / 1,875 | 750 | 250 | 250 | 250 | - | 25.5 |
| **ftrk** | S | - |  | 600 | 15,000 | Light | 2.76 | FLAK-23-AA r8; FLAK-23-AG r6 | 2,000 / 1,000 | 3,000 | 500 | 500 | 1,000 | 3,000 | 12.6 |
| **3tnk** | S | fix |  | 1,150 | 60,000 | Heavy | 1.56 | 105mm r4.75 | 800 / 400 | 2,000 | 3,067 | 2,000 | 1,333 | - | 7.0 |
| **4tnk** | S | fix, stek | yes | 2,000 | 90,000 | Heavy | 1.05 | 120mm r4.75; MammothTusk r6.5 min0.5 | 3,778 / 1,889 | 2,368 | 3,632 | 2,368 | 1,579 | 2,308 | 23.7 |
| **v2rl** | S | dome |  | 900 | 20,000 | Light | 1.76 | SCUD r10 min4 | 4,581 / 2,290 | 3,663 | 2,093 | 3,924 | 5,233 | - | 7.8 |
| **ttnk** | S | tsla, stek (russia only) | yes | 1,350 | 40,000 | Light | 2.25 | TTankZap r7 | 20,833 / 10,417 | 2,083 | 2,083 | 2,083 | 2,083 | - | 13.5 |
| **pbox** | A | tent |  | 600 | 40,000 | Heavy | - | Vulcan r6 (e1 inside) | 8,571 / 4,286 | 2,143 | 857 | 2,143 | 857 | - | 43.7 |
| **hbox** | A | tent |  | 750 | 40,000 | Heavy | - | Vulcan r6 (e1 inside) | 8,571 / 4,286 | 2,143 | 857 | 2,143 | 857 | - | 43.7 |
| **gun** | A | tent |  | 800 | 40,000 | Heavy | - | TurretGun r6.5 | 1,000 / 500 | 3,750 | 5,000 | 2,500 | 2,500 | - | 6.3 |
| **ftur** | S | barr |  | 600 | 40,000 | Heavy | - | FireballLauncher r5 | 7,500 / 3,750 | 4,167 | 2,083 | 4,167 | 1,667 | - | 34.0 |
| **tsla** | S | weap |  | 1,200 | 40,000 | Heavy | - | TeslaZap r7 | 50,676 / 25,338 | 5,068 | 5,068 | 3,041 | 5,068 | - | 26.0 |

### 1b. Value metrics [C]

\* Assumes a rifleman is always within 3 cells; see section 2 for real dog fights.

| Item | Side | Cost | HP per $100 | Eff. anti-infantry DPS per $100 | DPS/$100 vs Light | DPS/$100 vs Heavy | DPS/$100 vs buildings (Wood) | Power | Build s |
|---|---|---|---|---|---|---|---|---|---|
| **e1** | A+S | 100 | 5,000 | 1,056 | 500 | 125 | 375 | - | 2.4 |
| **e3** | A+S | 300 | 1,500 | 78 | 283 | 833 | 617 | - | 7.2 |
| **e7** | A | 1,800 | 556 | 996 | 0 | 0 | 0 | - | 43.2 |
| **e2** | S | 150 | 3,333 | 468 | 404 | 404 | 1,667 | - | 3.6 |
| **e4** | S | 300 | 1,333 | 329 | 646 | 323 | 1,562 | - | 7.2 |
| **dog** | S | 200 | 900 | 6,284* | 0 | 0 | 0 | - | 4.8 |
| **shok** | S | 350 | 1,429 | 534 | 459 | 275 | 335 | - | 8.4 |
| **jeep** | A | 500 | 3,000 | 425 | 150 | 50 | 50 | - | 12.0 |
| **1tnk** | A | 700 | 3,286 | 72 | 493 | 204 | 221 | - | 16.8 |
| **2tnk** | A | 850 | 5,412 | 61 | 176 | 271 | 176 | - | 20.4 |
| **arty** | A | 850 | 1,176 | 174 | 367 | 153 | 318 | - | 20.4 |
| **ctnk** | A | 1,350 | 2,963 | 23 | 105 | 309 | 228 | - | 32.4 |
| **stnk** | A | 1,000 | 3,500 | 12 | 42 | 125 | 92 | - | 24.0 |
| **apc** | S | 850 | 4,118 | 250 | 88 | 29 | 29 | - | 20.4 |
| **ftrk** | S | 600 | 2,500 | 175 | 500 | 83 | 83 | - | 14.4 |
| **3tnk** | S | 1,150 | 5,217 | 51 | 174 | 267 | 174 | - | 27.6 |
| **4tnk** | S | 2,000 | 4,500 | 99 | 118 | 182 | 118 | - | 48.0 |
| **v2rl** | S | 900 | 2,222 | 72 | 407 | 233 | 436 | - | 21.6 |
| **ttnk** | S | 1,350 | 2,963 | 84 | 154 | 154 | 154 | - | 32.4 |
| **pbox** | A | 600 | 6,667 | 607 | 357 | 143 | 357 | -20 | 14.4 |
| **hbox** | A | 750 | 5,333 | 485 | 286 | 114 | 286 | -20 | 18.0 |
| **gun** | A | 800 | 5,000 | 66 | 469 | 625 | 312 | -40 | 19.2 |
| **ftur** | S | 600 | 6,667 | 473 | 694 | 347 | 694 | -20 | 14.4 |
| **tsla** | S | 1,200 | 3,333 | 181 | 422 | 422 | 253 | -80 | 28.8 |

### 1c. Non-combat items and targets [F]

| Item | Category | Side | Needs | Tech ctr | Cost | HP | Armour | Cells/s | Power | Note |
|---|---|---|---|---|---|---|---|---|---|---|
| **e6** | infantry | A+S | barracks |  | 400 | 2,500 | None | 1.32 | - | engineer: no weapon; capture/repair need orders the platform lacks |
| **medi** | infantry | A | tent |  | 200 | 6,000 | None | 1.2 | - | medic: heals allied infantry (Heal -5000 per 80 ticks), no attack |
| **mech** | infantry | A | tent, fix |  | 500 | 8,000 | None | 1.2 | - | mechanic: repairs vehicles, no attack |
| **spy** | infantry | A | dome, tent |  | 500 | 2,500 | None | 1.32 | - | spy: SilencedPPK hits infantry only, HoldFire stance; infiltration needs an order the platform lacks |
| **thf** | infantry | S | barr, dome |  | 500 | 8,000 | None | 1.76 | - | thief: no weapon; steal needs infiltration order |
| **mgg** | vehicle | A | atek (england only) | yes | 1,000 | 22,000 | Heavy | 1.76 | - | mobile gap generator: no weapon |
| **mrj** | vehicle | A | atek | yes | 1,000 | 22,000 | Heavy | 1.66 | - | radar jammer: no weapon |
| **qtnk** | vehicle | S | fix, stek | yes | 2,000 | 90,000 | Heavy | 1.12 | - | MAD tank: deploy-to-detonate (19% max HP to vehicles/buildings in 7 cells), no gun |
| **dtrk** | vehicle | S | stek (ukraine only) | yes | 2,500 | 2,800 | Light | 1.64 | - | demolition truck: suicide MiniNuke, 2800 HP |
| **mnly** | vehicle | A+S | fix |  | 800 | 30,000 | Heavy | 2.76 | - | minelayer: no gun; mines need a lay-mines order |
| **truk** | vehicle | A+S | - |  | 500 | 11,000 | Light | 2.76 | - | supply truck: delivers $500, no weapon |
| **harv** | vehicle | A+S | proc |  | 1,100 | 60,000 | Heavy | 1.76 | - | harvester: target only |
| **mcv** | vehicle | A+S | fix |  | 2,000 | 60,000 | Light | 1.46 | - | MCV: target only |
| **agun** | defense | A | dome |  | 800 | 40,000 | Heavy | - | -50 | anti-air only (ZSU-23 ValidTargets AirborneActor) |
| **sam** | defense | S | dome |  | 700 | 40,000 | Heavy | - | -40 | anti-air only (Nike ValidTargets AirborneActor) |
| **gap** | defense | A | atek | yes | 800 | 50,000 | Heavy | - | -60 | gap generator: shroud only, no weapon |
| **fact** | building | A+S | MCV deploy |  | 2,000 | 150,000 | Wood | - | 0 |  |
| **powr** | building | A+S | - |  | 300 | 40,000 | Wood | - | 100 |  |
| **apwr** | building | A+S | dome |  | 500 | 70,000 | Wood | - | 200 |  |
| **tent** | building | A | anypower |  | 500 | 60,000 | Wood | - | -20 |  |
| **barr** | building | S | anypower |  | 500 | 60,000 | Wood | - | -20 |  |
| **kenn** | building | S | anypower |  | 200 | 30,000 | Wood | - | -10 |  |
| **proc** | building | A+S | anypower |  | 1,400 | 90,000 | Wood | - | -30 |  |
| **weap** | building | A+S | proc |  | 2,000 | 150,000 | Wood | - | -30 |  |
| **fix** | building | A+S | weap |  | 1,200 | 80,000 | Wood | - | -30 |  |
| **dome** | building | A+S | proc |  | 1,500 | 100,000 | Wood | - | -40 |  |
| **atek** | building | A | weap, dome |  | 1,500 | 60,000 | Wood | - | -200 |  |
| **stek** | building | S | weap, dome |  | 1,500 | 80,000 | Wood | - | -100 |  |

### 1d. Seconds for one unit to destroy a target [C] (HP / DPS; Wood buildings, Heavy defenses and harvester)

| One unit vs | powr (40k) | barracks (60k) | proc (90k) | weap / fact (150k) | pbox / ftur (40k Heavy) | harv (60k Heavy) |
|---|---|---|---|---|---|---|
| **e1** | 107 | 160 | 240 | 400 | 320 | 480 |
| **e3** | 22 | 32 | 49 | 81 | 16 | 24 |
| **e7** | - | - | - | - | - | - |
| **e2** | 16 | 24 | 36 | 60 | 66 | 99 |
| **e4** | 8.5 | 13 | 19 | 32 | 41 | 62 |
| **dog** | - | - | - | - | - | - |
| **shok** | 34 | 51 | 77 | 128 | 41 | 62 |
| **jeep** | 160 | 240 | 360 | 600 | 160 | 240 |
| **1tnk** | 26 | 39 | 58 | 97 | 28 | 42 |
| **2tnk** | 27 | 40 | 60 | 100 | 17 | 26 |
| **arty** | 15 | 22 | 33 | 55 | 31 | 46 |
| **ctnk** | 13 | 19 | 29 | 49 | 9.6 | 14 |
| **stnk** | 43 | 65 | 97 | 162 | 32 | 48 |
| **apc** | 160 | 240 | 360 | 600 | 160 | 240 |
| **ftrk** | 80 | 120 | 180 | 300 | 80 | 120 |
| **3tnk** | 20 | 30 | 45 | 75 | 13 | 20 |
| **4tnk** | 17 | 25 | 38 | 63 | 11 | 17 |
| **v2rl** | 10 | 15 | 23 | 38 | 19 | 29 |
| **ttnk** | 19 | 29 | 43 | 72 | 19 | 29 |


## 2. One-versus-one duels [C]

Rows attack and columns defend, starting at the attacker's maximum range. A shorter-ranged defender walks in under fire. A unit attacking a defense starts just outside the defense's range, or at its own range if that is longer. **`A62 9s`** = the attacker wins with 62 % HP after 9 s; `D13 10s` = the defender wins with 13 %; `=` = both die; `x` = cannot attack. Ranges are in `duel_1v1.csv`.

### 2a. Infantry vs infantry
| att \ def | e1 | e3 | e7 | e2 | e4 | dog | shok |
|---|---|---|---|---|---|---|---|
| **e1** | = 4s | **A81** 3s | D85 0.0s | **A55** 4s | **A9** 3s | **A100** 0.8s | D70 0.0s |
| **e3** | D81 3s | = 19s | D95 0.0s | D66 7s | D64 4s | D73 0.8s | D90 0.0s |
| **e7** | **A100** 0.0s | **A100** 0.0s | = 0s | **A100** 0.0s | **A100** 0.0s | **A100** 0.0s | **A100** 0.0s |
| **e2** | D10 4s | **A61** 7s | D100 0.0s | = 7s | = 4s | D100 0.6s | D100 0.0s |
| **e4** | D9 3s | **A64** 4s | D100 0.0s | = 5s | = 4s | = 2s | D100 0.0s |
| **dog** | **A17** 0.3s | **A73** 0.3s | D100 0.0s | **A100** 0.3s | **A100** 0.3s | = 0s | D100 0.0s |
| **shok** | **A100** 0.0s | **A100** 0.0s | = 0s | **A100** 0.0s | **A100** 0.0s | **A100** 0.0s | = 0s |

### 2b. Infantry (rows) vs vehicles
| att \ def | jeep | 1tnk | 2tnk | arty | ctnk | stnk | apc | ftrk | 3tnk | 4tnk | v2rl | ttnk |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **e1** | D92 2s | D95 10s | D98 8s | D92 1s | D81 15s | D42 41s | D99 2s | D84 4s | D98 7s | D100 0.8s | D96 1s | D99 0.0s |
| **e3** | D89 1s | = 9s | D57 6s | D83 1s | D66 15s | D8 37s | D86 1s | D66 4s | D67 6s | D94 0.6s | D91 1s | D96 0.0s |
| **e7** | D100 4s | D100 20s | D100 16s | D100 6s | D100 32s | D100 81s | D100 4s | D100 9s | D100 15s | D100 3s | D100 2s | D100 0.0s |
| **e2** | D90 2s | D75 9s | D87 8s | D85 1s | D78 15s | D29 41s | D96 2s | D81 4s | D93 6s | D98 0.7s | D93 1s | D100 0.0s |
| **e4** | D67 1s | D68 7s | D84 6s | D50 1s | D38 13s | **A40** 19s | D93 1s | D47 3s | D88 6s | D99 0.6s | D75 1s | D100 0.0s |
| **dog** | D100 0.2s | D100 2s | D100 2s | D100 3s | D100 3s | D100 13s | D100 0.2s | D100 0.8s | D100 0.5s | D100 0.8s | D100 2s | D100 0.0s |
| **shok** | D70 3s | D53 10s | D77 9s | D55 1s | D32 15s | **A52** 20s | D92 3s | D40 4s | D86 7s | D97 0.9s | D78 1s | D89 0.0s |

### 2c. Vehicles (rows) vs infantry
| att \ def | e1 | e3 | e7 | e2 | e4 | dog | shok |
|---|---|---|---|---|---|---|---|
| **jeep** | **A92** 2s | **A89** 0.8s | **A100** 4s | **A90** 2s | **A71** 0.8s | **A100** 0.2s | **A70** 2s |
| **1tnk** | **A95** 9s | = 9s | **A100** 20s | **A75** 9s | **A68** 7s | **A100** 2s | **A53** 9s |
| **2tnk** | **A98** 8s | **A57** 6s | **A100** 16s | **A91** 8s | **A84** 6s | **A100** 2s | **A82** 8s |
| **arty** | **A100** 3s | **A100** 3s | **A100** 6s | **A100** 3s | **A100** 3s | **A100** 3s | **A100** 3s |
| **ctnk** | **A82** 15s | **A66** 15s | **A100** 32s | **A78** 15s | **A38** 13s | **A100** 3s | **A32** 15s |
| **stnk** | **A43** 41s | **A13** 37s | **A100** 81s | **A34** 41s | D28 20s | **A100** 13s | D52 20s |
| **apc** | **A99** 2s | **A86** 0.8s | **A100** 4s | **A96** 2s | **A94** 0.8s | **A100** 0.2s | **A92** 2s |
| **ftrk** | **A89** 4s | **A77** 4s | **A100** 9s | **A90** 4s | **A67** 3s | **A100** 0.8s | **A40** 4s |
| **3tnk** | **A98** 6s | **A67** 6s | **A100** 15s | **A93** 6s | **A88** 6s | **A100** 0.5s | **A86** 6s |
| **4tnk** | **A100** 0.9s | **A100** 0.8s | **A100** 3s | **A100** 0.9s | **A100** 0.8s | **A100** 0.8s | **A97** 0.9s |
| **v2rl** | **A100** 2s | **A100** 2s | **A100** 2s | **A100** 2s | **A100** 2s | **A100** 2s | **A100** 2s |
| **ttnk** | **A100** 0.0s | **A100** 0.0s | **A100** 0.0s | **A100** 0.0s | **A100** 0.0s | **A100** 0.0s | **A100** 0.0s |

### 2d. Vehicles vs vehicles
| att \ def | jeep | 1tnk | 2tnk | arty | ctnk | stnk | apc | ftrk | 3tnk | 4tnk | v2rl | ttnk |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **jeep** | = 19s | D95 4s | D95 8s | D67 4s | D80 10s | D29 33s | D86 19s | D70 5s | D97 6s | D99 4s | D92 1s | D89 5s |
| **1tnk** | **A95** 4s | = 16s | D74 8s | **A81** 3s | D49 5s | **A35** 10s | **A73** 25s | **A90** 4s | D84 6s | D92 4s | **A22** 5s | D13 10s |
| **2tnk** | **A95** 8s | **A74** 8s | = 18s | **A81** 6s | D55 10s | **A35** 22s | **A92** 14s | **A91** 8s | D46 12s | D69 12s | **A22** 12s | D25 19s |
| **arty** | **A82** 4s | D62 5s | D62 10s | = 3s | D20 8s | **A49** 12s | D37 16s | **A16** 5s | D78 8s | D80 11s | = 6s | D73 2s |
| **ctnk** | **A81** 10s | **A56** 5s | **A62** 10s | **A47** 6s | = 27s | **A70** 25s | **A85** 8s | **A19** 10s | **A40** 13s | D22 17s | D15 10s | D40 14s |
| **stnk** | **A31** 33s | D35 11s | D35 23s | D49 12s | D70 25s | = 81s | **A49** 25s | D66 12s | D58 16s | D78 13s | D74 10s | D83 14s |
| **apc** | **A86** 19s | D73 25s | D92 14s | **A50** 14s | D85 8s | D46 25s | = 139s | **A73** 19s | D96 9s | D98 8s | D62 10s | D72 14s |
| **ftrk** | **A76** 5s | D89 5s | D90 9s | **A29** 3s | D19 10s | **A66** 12s | D72 19s | = 5s | D94 7s | D97 5s | D76 1s | D61 5s |
| **3tnk** | **A97** 6s | **A84** 6s | **A46** 12s | **A85** 3s | D25 13s | **A67** 15s | **A96** 9s | **A95** 6s | = 18s | D39 15s | **A40** 9s | **A33** 18s |
| **4tnk** | **A99** 4s | **A92** 4s | **A69** 12s | **A90** 4s | **A22** 15s | **A83** 12s | **A98** 8s | **A98** 4s | **A39** 15s | = 23s | **A80** 8s | **A56** 15s |
| **v2rl** | **A94** 2s | D22 7s | D22 15s | **A47** 2s | **A32** 10s | **A74** 10s | **A70** 10s | **A82** 2s | D40 13s | D60 13s | = 2s | D21 6s |
| **ttnk** | **A92** 5s | **A20** 10s | **A32** 19s | **A73** 0.0s | **A49** 14s | **A83** 14s | **A74** 14s | **A64** 5s | D17 20s | D56 18s | **A21** 5s | = 14s |

### 2e. Units (rows) attack defenses
| att \ def | pbox | hbox | gun | ftur | tsla |
|---|---|---|---|---|---|
| **e1** | D100 0.3s | D100 0.3s | D98 9s | D100 0.8s | D100 0.9s |
| **e3** | D100 0.3s | D100 0.3s | D62 8s | D87 0.8s | D100 0.9s |
| **e7** | x | x | x | x | x |
| **e2** | D100 0.3s | D100 0.3s | D89 9s | D100 0.8s | D100 0.9s |
| **e4** | D100 0.2s | D100 0.2s | D86 6s | D94 0.8s | D100 0.9s |
| **dog** | x | x | x | x | x |
| **shok** | D93 0.3s | D93 0.3s | D80 9s | **A100** 39s | D93 0.9s |
| **jeep** | D96 6s | D96 6s | D98 4s | D99 1s | D100 1s |
| **1tnk** | D4 27s | D4 27s | D88 4s | D58 12s | D97 1s |
| **2tnk** | **A66** 17s | **A66** 17s | D54 9s | **A18** 16s | D66 7s |
| **arty** | **A100** 30s | **A100** 30s | **A100** 30s | **A100** 30s | **A100** 30s |
| **ctnk** | **A55** 8s | **A55** 8s | **A21** 8s | **A100** 8s | D25 7s |
| **stnk** | D50 16s | D50 16s | D62 9s | **A100** 29s | D75 7s |
| **apc** | D75 41s | D75 41s | D96 6s | D90 16s | D96 7s |
| **ftrk** | D92 6s | D92 6s | D95 4s | **A100** 80s | D99 1s |
| **3tnk** | **A80** 13s | **A80** 13s | D8 11s | **A50** 12s | D54 7s |
| **4tnk** | **A91** 9s | **A91** 9s | **A40** 10s | **A75** 8s | **A33** 10s |
| **v2rl** | **A100** 20s | **A100** 20s | **A100** 20s | **A100** 20s | **A100** 20s |
| **ttnk** | **A100** 14s | **A100** 14s | **A100** 14s | **A100** 14s | D50 7s |

### 2f. Defenses (rows) engage units
| att \ def | e1 | e3 | e7 | e2 | e4 | dog | shok | jeep | 1tnk | 2tnk | arty | ctnk | stnk | apc | ftrk | 3tnk | 4tnk | v2rl | ttnk |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **pbox** | **A100** 0.2s | **A100** 0.2s | **A100** 2s | **A100** 0.2s | **A100** 0.2s | **A100** 0.0s | **A93** 0.2s | **A96** 6s | **A4** 27s | D66 17s | **A75** 4s | D55 8s | **A50** 16s | **A75** 41s | **A92** 6s | D80 13s | D91 9s | **A10** 9s | D18 14s |
| **hbox** | **A100** 0.2s | **A100** 0.2s | **A100** 2s | **A100** 0.2s | **A100** 0.2s | **A100** 0.0s | **A93** 0.2s | **A96** 6s | **A4** 27s | D66 17s | **A75** 4s | D55 8s | **A50** 16s | **A75** 41s | **A92** 6s | D80 13s | D91 9s | **A10** 9s | D18 14s |
| **gun** | **A98** 9s | **A62** 7s | **A100** 18s | **A89** 9s | **A86** 6s | **A100** 2s | **A80** 9s | **A98** 4s | **A88** 4s | **A54** 9s | **A88** 3s | D21 8s | **A62** 9s | **A96** 6s | **A95** 4s | **A8** 11s | D40 10s | **A55** 5s | **A25** 10s |
| **ftur** | **A100** 0.8s | **A87** 0.8s | **A100** 0.8s | **A100** 0.8s | **A94** 0.8s | **A100** 0.8s | **A93** 0.8s | **A99** 1s | **A58** 12s | D18 16s | **A88** 2s | D6 8s | **A62** 8s | **A90** 16s | **A98** 2s | D50 12s | D75 8s | **A55** 4s | **A50** 9s |
| **tsla** | **A100** 0.9s | **A100** 0.9s | **A100** 0.9s | **A100** 0.9s | **A100** 0.9s | **A100** 0.9s | **A93** 0.9s | **A100** 1s | **A97** 1s | **A66** 7s | **A88** 0.9s | **A25** 7s | **A75** 7s | **A96** 7s | **A99** 1s | **A54** 7s | D33 10s | **A55** 1s | **A50** 7s |


## 3. Group fights [C]

The sides start at the longer weapon range, front to front. Cells read from the row's side: **`+12`** = the row wins with 12 survivors on average, `-7` = the column wins with 7, `0` = both are wiped out, `~` = the jitters split. The cost-equal grids give the survivors' HP-weighted value (`+1.4k`). Interpolate between N values. Win shares, losses and times: `group_fights.csv`.

**Crush** is not in these grids. These vehicles crush infantry, but alert infantry dodge 76 % of attempts, so crushing is a deliberate `move`-through-the-blob tactic, not something attack orders do:

| Vehicle | Locomotor | Crushes infantry | Cells/s |
|---|---|---|---|
| **jeep** | wheeled | no | 4.00 |
| **1tnk** | tracked | yes | 2.76 |
| **2tnk** | tracked | yes | 1.76 |
| **arty** | lighttracked | no | 1.76 |
| **ctnk** | heavywheeled | yes | 2.10 |
| **stnk** | heavywheeled | yes | 3.12 |
| **apc** | tracked | yes | 3.12 |
| **ftrk** | wheeled | no | 2.76 |
| **3tnk** | tracked | yes | 1.56 |
| **4tnk** | heavytracked | yes | 1.05 |
| **v2rl** | wheeled | no | 1.76 |
| **ttnk** | tracked | yes | 2.25 |
| **harv** | heavywheeled | yes | 1.76 |
| **mcv** | heavywheeled | yes | 1.46 |
| **mnly** | tracked | yes | 2.76 |
| **qtnk** | tracked | yes | 1.12 |

### 3a. N = 1, focus fire
| row \ col | e1 | e3 | e7 | e2 | e4 | dog | shok | jeep | 1tnk | 2tnk | arty | ctnk | stnk | apc | ftrk | 3tnk | 4tnk | v2rl | ttnk |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **e1** | = | **+1** | -1 | **+1** | **+1** | 0 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 |
| **e3** | -1 | = | -1 | -1 | -1 | -1 | -1 | -1 | 0 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 |
| **e7** | **+1** | **+1** | = | **+1** | **+1** | **+1** | **+1** | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 |
| **e2** | -1 | **+1** | -1 | = | 0 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 |
| **e4** | -1 | **+1** | -1 | 0 | = | 0 | -1 | -1 | -1 | -1 | -1 | -1 | **+1** | -1 | -1 | -1 | -1 | -1 | -1 |
| **dog** | 0 | **+1** | -1 | **+1** | 0 | = | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 |
| **shok** | **+1** | **+1** | -1 | **+1** | **+1** | **+1** | = | -1 | -1 | -1 | -1 | -1 | **+1** | -1 | -1 | -1 | -1 | -1 | -1 |
| **jeep** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | = | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 | -1 |
| **1tnk** | **+1** | 0 | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | = | -1 | **+1** | -1 | **+1** | **+1** | **+1** | -1 | -1 | **+1** | -1 |
| **2tnk** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | = | **+1** | -1 | **+1** | **+1** | **+1** | -1 | -1 | **+1** | -1 |
| **arty** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | -1 | -1 | = | -1 | **+1** | -1 | **+1** | -1 | -1 | 0 | -1 |
| **ctnk** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | = | **+1** | **+1** | **+1** | **+1** | -1 | -1 | -1 |
| **stnk** | **+1** | **+1** | **+1** | **+1** | -1 | **+1** | -1 | **+1** | -1 | -1 | -1 | -1 | = | **+1** | -1 | -1 | -1 | -1 | -1 |
| **apc** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | -1 | -1 | **+1** | -1 | -1 | = | **+1** | -1 | -1 | -1 | -1 |
| **ftrk** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | -1 | -1 | -1 | -1 | **+1** | -1 | = | -1 | -1 | -1 | -1 |
| **3tnk** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | -1 | **+1** | **+1** | **+1** | = | -1 | **+1** | **+1** |
| **4tnk** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | = | **+1** | **+1** |
| **v2rl** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | -1 | -1 | 0 | **+1** | **+1** | **+1** | **+1** | -1 | -1 | = | -1 |
| **ttnk** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | **+1** | -1 | -1 | **+1** | = |

### 3b. N = 5, focus fire
| row \ col | e1 | e3 | e2 | e4 | dog | shok | jeep | 1tnk | 2tnk | arty | ctnk | stnk | apc | ftrk | 3tnk | 4tnk | v2rl | ttnk |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **e1** | = | **+5** | **+4** | -0.8 | **+5** | -5 | -5 | -5 | -5 | -5 | -5 | -1.8 | -5 | -5 | -5 | -5 | -5 | -5 |
| **e3** | -5 | = | -5 | -5 | -4 | -5 | -5 | **+0.6** | -2.8 | -5 | -4 | **+2** | -5 | -5 | -4 | -5 | -5 | -5 |
| **e2** | -4 | **+5** | = | -3.2 | -2 | -5 | -5 | -5 | -5 | -5 | -5 | **+2** | -5 | -5 | -5 | -5 | -5 | -5 |
| **e4** | **+0.8** | **+5** | **+3.4** | = | 0 | -5 | -4 | -4 | -5 | -5 | -1.4 | **+4** | -5 | -5 | -5 | -5 | -5 | -5 |
| **dog** | -5 | **+4** | **+2** | 0 | = | -5 | -5 | -5 | -5 | -5 | -5 | -5 | -5 | -5 | -5 | -5 | -5 | -5 |
| **shok** | **+5** | **+5** | **+5** | **+5** | **+5** | = | -4 | -4 | -4 | -5 | -1 | **+4** | -5 | -3.2 | -5 | -5 | -5 | -5 |
| **jeep** | **+5** | **+5** | **+5** | **+4** | **+5** | **+4** | = | -5 | -5 | -5 | -5 | -3 | -5 | -5 | -5 | -5 | -5 | -5 |
| **1tnk** | **+5** | **+0.8** | **+5** | **+4** | **+5** | **+4** | **+5** | = | -5 | **+3.8** | -3 | **+3** | **+5** | **+5** | -5 | -5 | **+3.6** | -3 |
| **2tnk** | **+5** | **+2.8** | **+5** | **+5** | **+5** | **+4** | **+5** | **+5** | = | **+4** | -4 | **+3** | **+5** | **+5** | -4 | -4 | **+2** | -3 |
| **arty** | **+5** | **+5** | **+5** | **+5** | **+5** | **+5** | **+5** | -3.4 | -4 | = | **+2.4** | **+4** | **+2** | **+3.2** | -4 | -5 | **+2** | -4 |
| **ctnk** | **+5** | **+4** | **+5** | **+1.2** | **+5** | **+1** | **+5** | **+3** | **+4** | -2.4 | = | **+4** | **+5** | **+2** | **+3** | -2 | -1.6 | -4 |
| **stnk** | **+2** | -2.4 | -2 | -4 | **+5** | -4 | **+3** | -3 | -3 | -4 | -4 | = | **+3** | -4 | -4 | -5 | -4.2 | -5 |
| **apc** | **+5** | **+5** | **+5** | **+5** | **+5** | **+5** | **+5** | -5 | -5 | -2 | -5 | -3 | = | **+5** | -5 | -5 | -3 | -5 |
| **ftrk** | **+5** | **+5** | **+5** | **+5** | **+5** | **+3.8** | **+5** | -5 | -5 | -3 | -2 | **+4** | -5 | = | -5 | -5 | -2.2 | -4.2 |
| **3tnk** | **+5** | **+4** | **+5** | **+5** | **+5** | **+5** | **+5** | **+5** | **+4** | **+4** | -2.6 | **+4** | **+5** | **+5** | = | -4 | **+4** | **+1.2** |
| **4tnk** | **+5** | **+5** | **+5** | **+5** | **+5** | **+5** | **+5** | **+5** | **+4** | **+5** | **+2** | **+5** | **+5** | **+5** | **+4** | = | **+3.4** | **+3.8** |
| **v2rl** | **+5** | **+5** | **+5** | **+5** | **+5** | **+5** | **+5** | -3.6 | -2 | -2 | **+1.6** | **+4.2** | **+3.2** | **+2** | -4 | -3 | = | -3.8 |
| **ttnk** | **+5** | **+5** | **+5** | **+5** | **+5** | **+5** | **+5** | **+3** | **+3** | **+4** | **+4** | **+5** | **+5** | **+4.2** | -1.2 | -4 | **+3** | = |

### 3c. N = 10, focus fire
| row \ col | e1 | e3 | e2 | e4 | dog | shok | jeep | 1tnk | 2tnk | arty | ctnk | stnk | apc | ftrk | 3tnk | 4tnk | v2rl | ttnk |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **e1** | = | **+10** | **+8.2** | -2.6 | **+10** | -10 | -10 | -10 | -10 | -10 | -9 | -1.2 | -10 | -10 | -10 | -10 | -10 | -10 |
| **e3** | -10 | = | -9 | -9.8 | -9 | -10 | -9 | -5 | -6 | -10 | -8 | **+5** | -9 | -10 | -8 | -10 | -10 | -10 |
| **e2** | -8.6 | **+9** | = | -5.4 | -5.6 | -10 | -10 | -9 | -9 | -10 | -9 | **+5.4** | -10 | -10 | -10 | -10 | -10 | -10 |
| **e4** | **+3** | **+10** | **+5.6** | = | -1 | -10 | -8.4 | -8.2 | -9 | -10 | **+3** | **+8.8** | -10 | -10 | -9.4 | -10 | -10 | -10 |
| **dog** | -10 | **+9** | **+5.6** | **+1** | = | -10 | -10 | -10 | -10 | -10 | -10 | -10 | -10 | -10 | -10 | -10 | -10 | -10 |
| **shok** | **+10** | **+10** | **+10** | **+10** | **+10** | = | -7 | -7 | -8 | -10 | **+3** | **+9** | -9.6 | -7 | -9 | -10 | -9.8 | -10 |
| **jeep** | **+10** | **+9** | **+10** | **+8.4** | **+10** | **+7** | = | -10 | -10 | -8.6 | -8 | -4.4 | -10 | -9 | -10 | -10 | -7.6 | -10 |
| **1tnk** | **+10** | **+4.8** | **+9** | **+8.4** | **+10** | **+7** | **+10** | = | -8.2 | **+5.2** | **+1.2** | **+7** | **+9** | **+9.4** | -9 | -10 | **+6** | -6 |
| **2tnk** | **+10** | **+6.2** | **+9** | **+9** | **+10** | **+8** | **+10** | **+8.8** | = | **+6.2** | -7 | **+6.2** | **+10** | **+10** | -6.6 | -8 | **+5.8** | -6.6 |
| **arty** | **+10** | **+10** | **+10** | **+10** | **+10** | **+10** | **+9** | -5.2 | -6.4 | = | **+5.8** | **+7.8** | **+6** | **+1.8** | -7.8 | -8.6 | -0.8 | -7 |
| **ctnk** | **+9** | **+8** | **+9** | -2 | **+10** | -2.4 | **+8** | **+0.8** | **+7** | -6.2 | = | **+8.8** | **+8.8** | -4.4 | **+5** | -3 | -1.8 | -7.6 |
| **stnk** | **+2.2** | -4.4 | -5.2 | -8.6 | **+10** | -9 | **+4** | -7 | -5.8 | -8 | -8.6 | = | **+7** | -8 | -8 | -9 | -7.4 | -9 |
| **apc** | **+10** | **+9** | **+10** | **+10** | **+10** | **+10** | **+10** | -9 | -10 | -5.4 | -8 | -7 | = | **+9** | -10 | -10 | -7.2 | -9 |
| **ftrk** | **+10** | **+10** | **+10** | **+10** | **+10** | **+7** | **+9** | -9.2 | -10 | **+0.8** | **+3.8** | **+8** | -9 | = | -10 | -10 | **+4.6** | -9 |
| **3tnk** | **+10** | **+8** | **+10** | **+9.6** | **+10** | **+9** | **+10** | **+9** | **+6.8** | **+7.8** | -5.2 | **+8** | **+10** | **+10** | = | -6 | **+7.6** | **+2** |
| **4tnk** | **+10** | **+10** | **+10** | **+10** | **+10** | **+10** | **+10** | **+10** | **+8** | **+8.6** | **+2.2** | **+9** | **+10** | **+10** | **+6.4** | = | **+8** | **+6** |
| **v2rl** | **+10** | **+10** | **+10** | **+10** | **+10** | **+9.8** | **+7.6** | -5.8 | -6 | **+0.8** | **+2.2** | **+7.2** | **+6.8** | -4.4 | -8 | -8 | = | -6 |
| **ttnk** | **+10** | **+10** | **+10** | **+10** | **+10** | **+10** | **+10** | **+6** | **+6.8** | **+7** | **+7.4** | **+9.6** | **+9** | **+9** | -1.8 | -6 | **+6** | = |

### 3d. N = 20, focus fire
| row \ col | e1 | e3 | e2 | e4 | dog | shok | jeep | 1tnk | 2tnk | arty | ctnk | stnk | apc | ftrk | 3tnk | 4tnk | v2rl | ttnk |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **e1** | = | **+19** | **+14.8** | -5.8 | **+20** | -20 | -19 | -20 | -20 | -20 | -16 | **+3.2** | -20 | -20 | -20 | -20 | -20 | -20 |
| **e3** | -19 | = | -17.8 | -19 | -18.6 | -20 | -19 | -12 | -12.4 | -20 | -13.8 | **+9** | -18.4 | -20 | -14.6 | -20 | -20 | -20 |
| **e2** | -15.6 | **+17.2** | = | -11.4 | -12 | -20 | -19.6 | -18 | -18.2 | -20 | -15.4 | **+9.8** | -20 | -20 | -19 | -20 | -20 | -20 |
| **e4** | **+6.8** | **+19** | **+12** | = | -4.4 | -20 | -15.2 | -16 | -16.8 | -20 | **+8.6** | **+16.4** | -19 | -20 | -18.2 | -20 | -19.4 | -20 |
| **dog** | -20 | **+18.2** | **+12** | **+4.4** | = | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 |
| **shok** | **+20** | **+20** | **+20** | **+20** | **+20** | = | -13 | -12.2 | -15 | -20 | **+6.4** | **+17** | -18.2 | -14 | -17 | -20 | -18.6 | -20 |
| **jeep** | **+19** | **+19** | **+19.4** | **+15.4** | **+20** | **+13.4** | = | -20 | -20 | -16.4 | -15.4 | **+5.6** | -19 | -19 | -20 | -20 | -14.4 | -20 |
| **1tnk** | **+20** | **+12.2** | **+18** | **+15.4** | **+20** | **+12.2** | **+20** | = | -16.2 | **+4.4** | -1.8 | **+15.6** | **+17** | **+17.8** | -17.6 | -18.6 | **+9** | -12.8 |
| **2tnk** | **+20** | **+12.4** | **+18** | **+17.2** | **+20** | **+15** | **+20** | **+16.2** | = | -4.4 | -12 | **+13.8** | **+19** | **+18.6** | -13.2 | -15.8 | **+5.8** | -14 |
| **arty** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+16.6** | -7.6 | **+3.8** | = | **+13** | **+15.2** | **+14.2** | -1.6 | -12.8 | -13.4 | -2.2 | -13 |
| **ctnk** | **+15.4** | **+13.4** | **+15.2** | -8.2 | **+20** | -6.6 | **+15.4** | **+5.8** | **+11.2** | -11.6 | = | **+16.4** | **+16** | -11.8 | **+4.6** | -10.8 | **+4.8** | -15 |
| **stnk** | -4.4 | -9 | -9.6 | -16.6 | **+20** | -17 | -5.4 | -15.8 | -13.6 | -15.4 | -16.6 | = | **+4.4** | -17 | -15.8 | -17 | -11.2 | -19 |
| **apc** | **+20** | **+18.8** | **+20** | **+19** | **+20** | **+18.4** | **+19** | -17 | -19 | -14.8 | -15.8 | -4.2 | = | **+17** | -20 | -20 | -12.8 | -18 |
| **ftrk** | **+20** | **+19.6** | **+20** | **+20** | **+20** | **+13.8** | **+19** | -18.2 | -19 | **+4.4** | **+10.8** | **+17** | -17 | = | -19 | -20 | **+11.4** | -17 |
| **3tnk** | **+20** | **+15.2** | **+19** | **+18.4** | **+20** | **+17** | **+20** | **+17.6** | **+12.6** | **+11.6** | -2.2 | **+15.6** | **+20** | **+19** | = | -12.4 | **+9.8** | -6 |
| **4tnk** | **+20** | **+19.8** | **+20** | **+20** | **+20** | **+20** | **+20** | **+18.6** | **+15.6** | **+14** | **+10.6** | **+17** | **+20** | **+20** | **+11.6** | = | **+15.2** | **+11** |
| **v2rl** | **+20** | **+20** | **+20** | **+19.4** | **+20** | **+18.6** | **+15.6** | -10.4 | **+3** | ~ | -4.2 | **+12** | **+12.2** | -11 | -10.8 | -13.8 | = | -15 |
| **ttnk** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+13** | **+14.2** | **+13** | **+15.2** | **+19** | **+18** | **+17** | **+6** | -11 | **+14.6** | = |

### 3e. N = 20, spread fire
| row \ col | e1 | e3 | e2 | e4 | dog | shok | jeep | 1tnk | 2tnk | arty | ctnk | stnk | apc | ftrk | 3tnk | 4tnk | v2rl | ttnk |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **e1** | = | **+20** | **+15.4** | -20 | **+19.2** | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 |
| **e3** | -20 | = | -20 | -20 | -20 | -20 | -20 | -14.4 | -20 | -20 | -20 | **+15.4** | -20 | -20 | -20 | -20 | -20 | -20 |
| **e2** | -12.4 | **+20** | = | -15.4 | -5 | -20 | -20 | -20 | -20 | -20 | -20 | **+18.6** | -20 | -20 | -20 | -20 | -20 | -20 |
| **e4** | **+20** | **+20** | **+15.4** | = | 0 | -20 | -18.2 | -18 | -20 | -20 | **+19.8** | **+20** | -20 | -20 | -20 | -20 | -20 | -20 |
| **dog** | -18.8 | **+20** | **+6.4** | 0 | = | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 |
| **shok** | **+20** | **+20** | **+20** | **+20** | **+20** | = | -20 | -20 | -20 | -20 | -19.6 | **+20** | -20 | -20 | -20 | -20 | -20 | -20 |
| **jeep** | **+20** | **+20** | **+20** | **+18.4** | **+20** | **+20** | = | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 | -20 |
| **1tnk** | **+20** | **+13.6** | **+20** | **+17** | **+20** | **+20** | **+20** | = | -20 | -15 | -20 | **+20** | **+20** | **+20** | -20 | -20 | -20 | -20 |
| **2tnk** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | = | -14.6 | -20 | **+20** | **+20** | **+20** | -20 | -20 | -19.8 | -20 |
| **arty** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+16.6** | **+11.4** | = | **+20** | **+20** | **+20** | **+20** | -18 | -19.4 | 0 | -8.6 |
| **ctnk** | **+20** | **+20** | **+20** | -19.4 | **+20** | **+19.6** | **+20** | **+20** | **+20** | -20 | = | **+20** | **+20** | **+4.2** | **+20** | -18.2 | -20 | -20 |
| **stnk** | **+20** | -15.6 | -18.2 | -20 | **+20** | -20 | **+20** | -20 | -20 | -20 | -20 | = | **+20** | -20 | -20 | -20 | -20 | -20 |
| **apc** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | -20 | -20 | -20 | -20 | -20 | = | **+20** | -20 | -20 | -20 | -20 |
| **ftrk** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | -20 | -20 | -20 | **+5.2** | **+20** | -20 | = | -20 | -20 | -20 | -20 |
| **3tnk** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+17.4** | -20 | **+20** | **+20** | **+20** | = | -20 | -12 | **+20** |
| **4tnk** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+19.2** | **+19** | **+20** | **+20** | **+20** | **+20** | = | **+18.8** | **+20** |
| **v2rl** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+19.8** | 0 | **+20** | **+20** | **+20** | **+20** | **+13** | -20 | = | **+20** |
| **ttnk** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+20** | **+7** | **+20** | **+20** | **+20** | **+20** | -20 | -20 | -20 | = |

### 3f. Cost-equal, $2,000 a side (Tanya fields $1,800)
| row \ col | e1 | e3 | e7 | e2 | e4 | dog | shok | jeep | 1tnk | 2tnk | arty | ctnk | stnk | apc | ftrk | 3tnk | 4tnk | v2rl | ttnk |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **e1** | = | **+1.9k** | **+0.8k** | **+1.5k** | **+0.9k** | **+2.0k** | **+1.3k** | **+1.1k** | **+1.4k** | **+1.3k** | -1.7k | **+1.9k** | **+1.9k** | -1.4k | **+1.3k** | **+1.2k** | -1.1k | **+0.3k** | **+1.7k** |
| **e3** | -1.9k | = | -1.8k | -1.9k | -1.8k | -1.8k | -2.0k | -1.5k | **+1.5k** | **+1.5k** | -2.0k | **+1.6k** | **+1.8k** | -0.5k | -1.4k | **+1.5k** | 0 | -2.0k | -0.7k |
| **e7** | -0.8k | **+1.8k** | = | **+1.6k** | **+1.8k** | -0.8k | -0.3k | -2.0k | -2.0k | -2.0k | -2.0k | -2.0k | -2.0k | -2.0k | -2.0k | -2.0k | -2.0k | -2.0k | -2.0k |
| **e2** | -1.4k | **+1.9k** | -1.6k | = | **+0.6k** | -0.8k | -0.3k | -1.0k | **+1.6k** | **+1.6k** | -1.9k | **+1.9k** | **+1.9k** | -0.4k | **+0.4k** | **+1.6k** | **+0.1k** | **+0.2k** | **+1.4k** |
| **e4** | -1.0k | **+1.8k** | -1.8k | -0.5k | = | -0.1k | -1.5k | -0.8k | **+0.9k** | **+1.0k** | -2.0k | **+1.8k** | **+1.8k** | -1.2k | -0.6k | **+1.0k** | -1.2k | -2.0k | **+1.1k** |
| **dog** | -2.0k | **+1.8k** | **+0.8k** | **+0.8k** | **+0.1k** | = | **+0.8k** | -2.0k | -2.0k | -2.0k | -2.0k | -2.0k | -2.0k | -2.0k | -2.0k | -2.0k | -2.0k | -2.0k | -2.0k |
| **shok** | -1.3k | **+2.0k** | **+0.3k** | **+0.4k** | **+1.5k** | -0.8k | = | -0.8k | **+1.0k** | **+1.0k** | -2.0k | **+1.8k** | **+1.9k** | -1.3k | **+0.5k** | **+0.9k** | -1.2k | -2.0k | **+0.9k** |
| **jeep** | -1.1k | **+1.5k** | **+2.0k** | **+1.0k** | **+0.9k** | **+2.0k** | **+0.8k** | = | -1.9k | -1.8k | -1.4k | **+0.5k** | **+1.4k** | -1.5k | -1.6k | -1.8k | -1.6k | -1.2k | -0.5k |
| **1tnk** | -1.4k | -1.5k | **+2.0k** | -1.6k | -1.0k | **+2.0k** | -1.1k | **+1.9k** | = | -1.3k | **+1.0k** | **+0.7k** | **+1.3k** | **+1.7k** | **+1.8k** | -1.2k | -0.9k | **+0.4k** | **+1.3k** |
| **2tnk** | -1.3k | -1.5k | **+2.0k** | -1.6k | -1.2k | **+2.0k** | -1.1k | **+1.8k** | **+1.3k** | = | **+1.1k** | -0.2k | **+1.3k** | **+1.9k** | **+1.6k** | -0.3k | **+0.8k** | **+0.5k** | **+1.0k** |
| **arty** | **+1.7k** | **+2.0k** | **+2.0k** | **+1.9k** | **+2.0k** | **+2.0k** | **+2.0k** | **+1.4k** | -1.0k | -1.1k | = | **+1.1k** | **+1.6k** | **+0.6k** | **+0.3k** | -1.1k | -0.7k | **+0.2k** | **+0.8k** |
| **ctnk** | -1.9k | -1.6k | **+2.0k** | -1.9k | -1.8k | **+2.0k** | -1.8k | -0.5k | -0.7k | **+0.2k** | -1.1k | = | **+1.3k** | **+1.4k** | -1.5k | **+0.1k** | **+0.7k** | -1.3k | -1.0k |
| **stnk** | -1.9k | -1.8k | **+2.0k** | -1.9k | -1.8k | **+2.0k** | -1.9k | -1.4k | -1.4k | -1.3k | -1.6k | -1.3k | = | **+0.7k** | -1.8k | -1.2k | -0.7k | -1.7k | -1.5k |
| **apc** | **+1.4k** | **+0.3k** | **+2.0k** | **+0.4k** | **+1.2k** | **+2.0k** | **+1.3k** | **+1.5k** | -1.7k | -1.9k | **+0.1k** | -1.4k | -0.7k | = | **+1.2k** | -1.9k | -1.8k | -1.3k | -0.6k |
| **ftrk** | -1.3k | **+1.4k** | **+2.0k** | -0.2k | **+0.6k** | **+2.0k** | -0.6k | **+1.6k** | -1.8k | -1.6k | -0.3k | **+1.5k** | **+1.8k** | -1.2k | = | -1.6k | -1.5k | **+0.5k** | **+1.4k** |
| **3tnk** | -1.2k | -1.5k | **+2.0k** | -1.6k | -1.0k | **+2.0k** | -0.9k | **+1.8k** | **+1.2k** | **+0.3k** | **+1.1k** | 0 | **+1.1k** | **+1.9k** | **+1.6k** | = | **+0.9k** | **+0.6k** | **+1.0k** |
| **4tnk** | **+1.0k** | 0 | **+2.0k** | -0.3k | **+1.3k** | **+2.0k** | **+1.2k** | **+1.6k** | **+0.9k** | -0.8k | **+0.7k** | -0.7k | **+0.7k** | **+1.8k** | **+1.5k** | -0.9k | = | -0.9k | **+0.1k** |
| **v2rl** | -0.3k | **+2.0k** | **+2.0k** | -0.1k | **+2.0k** | **+2.0k** | **+2.0k** | **+1.2k** | -0.4k | -0.5k | -0.2k | **+1.3k** | **+1.7k** | **+1.4k** | -0.5k | -0.6k | **+0.9k** | = | **+1.1k** |
| **ttnk** | -1.7k | **+0.7k** | **+2.0k** | -1.4k | -1.1k | **+2.0k** | -0.8k | **+0.5k** | -1.3k | -1.0k | -0.7k | **+1.0k** | **+1.5k** | **+0.6k** | -1.4k | -1.0k | -0.1k | -1.1k | = |

### 3g. Cost-equal, $6,000 a side
| row \ col | e1 | e3 | e2 | e4 | dog | shok | jeep | 1tnk | 2tnk | arty | ctnk | stnk | apc | ftrk | 3tnk | 4tnk | v2rl | ttnk |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **e1** | = | **+5.8k** | **+4.4k** | **+3.0k** | **+6.0k** | **+3.1k** | **+4.0k** | **+4.3k** | **+4.4k** | **+0.9k** | **+5.8k** | **+5.9k** | -4.0k | **+3.8k** | **+4.5k** | -2.0k | **+2.9k** | **+4.9k** |
| **e3** | -5.8k | = | -5.4k | -5.3k | -5.6k | -5.9k | -4.9k | **+4.0k** | **+4.5k** | -6.0k | **+5.1k** | **+5.5k** | -3.7k | -4.7k | **+4.8k** | **+1.2k** | -4.7k | -2.1k |
| **e2** | -4.4k | **+5.4k** | = | **+0.9k** | -2.1k | -1.7k | -3.1k | **+4.5k** | **+4.9k** | -4.4k | **+5.6k** | **+5.8k** | -2.2k | -0.9k | **+4.9k** | **+1.3k** | **+0.7k** | **+3.8k** |
| **e4** | -3.1k | **+5.4k** | -0.7k | = | -2.7k | -4.1k | -1.9k | **+3.0k** | **+3.4k** | -6.0k | **+5.3k** | **+5.7k** | -3.7k | -1.8k | **+3.8k** | -2.5k | -0.5k | **+3.1k** |
| **dog** | -6.0k | **+5.3k** | **+2.2k** | **+2.7k** | = | **+2.3k** | -6.0k | -6.0k | -6.0k | -6.0k | -6.0k | -6.0k | -6.0k | -6.0k | -6.0k | -6.0k | -6.0k | -6.0k |
| **shok** | -3.0k | **+5.9k** | **+2.1k** | **+4.3k** | -2.4k | = | -1.9k | **+3.9k** | **+3.7k** | -6.0k | **+5.6k** | **+5.8k** | -3.6k | **+2.0k** | **+3.6k** | -1.9k | -1.9k | **+3.9k** |
| **jeep** | -3.8k | **+5.0k** | **+3.2k** | **+1.6k** | **+6.0k** | **+2.0k** | = | -5.6k | -5.5k | -3.1k | **+2.4k** | **+4.6k** | -4.5k | -5.1k | -5.3k | -4.6k | -2.4k | **+0.5k** |
| **1tnk** | -4.2k | -3.9k | -4.5k | -3.1k | **+6.0k** | -3.9k | **+5.6k** | = | -4.1k | **+2.7k** | **+3.7k** | **+4.6k** | **+5.3k** | **+5.1k** | -4.1k | -1.6k | **+3.1k** | **+3.9k** |
| **2tnk** | -4.4k | -4.4k | -4.8k | -3.5k | **+6.0k** | -3.8k | **+5.5k** | **+4.2k** | = | **+2.3k** | **+2.7k** | **+4.5k** | **+5.7k** | **+4.9k** | -0.7k | **+3.6k** | **+3.4k** | **+3.4k** |
| **arty** | -0.8k | **+6.0k** | **+4.7k** | **+6.0k** | **+6.0k** | **+6.0k** | **+3.2k** | -2.1k | -2.1k | = | **+5.5k** | **+5.8k** | **+2.0k** | -1.6k | -2.1k | **+1.3k** | **+1.4k** | **+2.0k** |
| **ctnk** | -5.8k | -5.1k | -5.6k | -5.3k | **+6.0k** | -5.7k | -2.4k | -3.7k | -2.5k | -5.4k | = | **+3.9k** | **+4.2k** | -4.8k | -0.7k | **+3.3k** | -3.6k | -3.7k |
| **stnk** | -5.9k | -5.5k | -5.8k | -5.7k | **+6.0k** | -5.8k | -4.6k | -4.6k | -4.4k | -5.7k | -3.9k | = | -1.1k | -5.4k | -3.9k | -2.3k | -4.8k | -4.8k |
| **apc** | **+4.0k** | **+3.8k** | **+2.3k** | **+3.8k** | **+6.0k** | **+3.7k** | **+4.5k** | -5.3k | -5.7k | -1.4k | -4.2k | **+1.1k** | = | **+3.4k** | -5.6k | -5.4k | -1.2k | -2.3k |
| **ftrk** | -3.6k | **+4.6k** | **+0.9k** | **+2.1k** | **+6.0k** | -2.1k | **+5.1k** | -5.1k | -4.9k | **+1.6k** | **+4.8k** | **+5.4k** | -3.3k | = | -4.8k | -3.7k | **+3.6k** | **+4.2k** |
| **3tnk** | -4.5k | -4.7k | -4.9k | -3.8k | **+6.0k** | -3.6k | **+5.3k** | **+4.1k** | **+0.4k** | **+1.9k** | **+0.4k** | **+4.0k** | **+5.6k** | **+4.9k** | = | **+3.5k** | **+2.5k** | **+3.0k** |
| **4tnk** | **+2.7k** | -0.8k | -1.1k | **+2.8k** | **+6.0k** | **+2.1k** | **+4.6k** | **+1.9k** | -3.7k | -1.8k | -2.9k | **+2.2k** | **+5.4k** | **+3.9k** | -3.5k | = | -4.1k | **+1.0k** |
| **v2rl** | -2.8k | **+4.8k** | -0.8k | **+0.8k** | **+6.0k** | **+2.1k** | **+2.3k** | -3.1k | -3.2k | -1.4k | **+4.0k** | **+4.9k** | **+0.8k** | -3.6k | -2.4k | **+4.1k** | = | **+1.8k** |
| **ttnk** | -4.9k | **+2.1k** | -3.7k | -3.1k | **+6.0k** | -3.6k | -0.6k | -4.0k | -3.4k | -1.9k | **+3.7k** | **+4.8k** | **+2.4k** | -4.2k | -2.9k | -0.5k | -1.7k | = |


## 4. Defense value [C]

Attackers approach as a clump from outside the defense's range.
- **Trickle:** attackers arrive one at a time; the cell gives the number killed before the defense dies (`>=200` = never).
- **Massed:** the smallest group that wins in at least 3 of 5 jitters, and the mean credits that group loses.
- **Screen:** the defense plus e1 equal to its cost (pbox 6, hbox 7, gun 8, ftur 6, tsla 12). The attacker shoots the screen or the defense first, whichever is cheaper.

Dogs and Tanya cannot attack structures. Losses are measured at the smallest winning group, so a screen that forces a bigger group can show slightly lower losses (pbox vs e1). Full rows: `defense_value.csv`.

### 4a. Attacker credits destroyed by a massed attack: defense alone / with screen (the screen doubles the defender's spend)

| Attacker \ defense | pbox ($600) | hbox ($750) | gun ($800) | ftur ($600) | tsla ($1200) |
|---|---|---|---|---|---|
| **e1** | 2,100 / 1,840 | 2,100 / 1,900 | 540 / 620 | 2,160 / 2,420 | 1,500 / 1,500 |
| **e3** | 1,500 / 6,960 | 1,500 / 8,040 | 300 / 5,280 | 780 / 6,300 | 1,020 / 9,540 |
| **e2** | 1,380 / 2,190 | 1,380 / 1,890 | 300 / 1,830 | 1,320 / 1,860 | 1,050 / 2,700 |
| **e4** | 2,040 / 2,460 | 2,040 / 2,100 | 600 / 1,020 | 1,980 / 2,340 | 1,800 / 1,500 |
| **shok** | 1,750 / 1,750 | 1,750 / 1,750 | 350 / 350 | 0 / 0 | 1,050 / 1,050 |
| **jeep** | 2,000 / 2,900 | 2,000 / 2,400 | 3,500 / 3,900 | 3,500 / 3,200 | 3,500 / 3,000 |
| **1tnk** | 0 / 0 | 0 / 0 | 1,400 / 1,400 | 700 / 700 | 1,400 / 1,820 |
| **2tnk** | 0 / 0 | 0 / 0 | 850 / 850 | 0 / 850 | 850 / 680 |
| **arty** | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| **ctnk** | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| **stnk** | 0 / 0 | 0 / 0 | 1,000 / 1,000 | 0 / 0 | 1,000 / 0 |
| **apc** | 850 / 850 | 850 / 850 | 3,400 / 3,740 | 2,550 / 1,700 | 2,550 / 4,080 |
| **ftrk** | 1,800 / 1,800 | 1,800 / 1,800 | 2,400 / 2,400 | 0 / 0 | 2,400 / 2,400 |
| **3tnk** | 0 / 0 | 0 / 0 | 0 / 690 | 0 / 0 | 1,150 / 1,150 |
| **4tnk** | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| **v2rl** | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| **ttnk** | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |

### 4b. Detail (exchange = attacker credits lost / defense cost)
| Defense ($) | Attacker | Trickle: killed before it dies ($) | Massed, defense alone: min N / $ lost | Massed vs defense + equal-cost e1 screen: min N / $ lost | Exchange alone ($ lost / defense $) |
|---|---|---|---|---|---|
| pbox (600) | e1 | >=200 (>=20000) | 23 / 2,100 | 25 / 1,840 | 3.5 |
| pbox (600) | e3 | 8 (2400) | 6 / 1,500 | 27 / 6,960 | 2.5 |
| pbox (600) | e2 | >=200 (>=30000) | 12 / 1,380 | 16 / 2,190 | 2.3 |
| pbox (600) | e4 | 31 (9300) | 9 / 2,040 | 9 / 2,460 | 3.4 |
| pbox (600) | shok | 14 (4900) | 8 / 1,750 | 8 / 1,750 | 2.9 |
| pbox (600) | jeep | 22 (11000) | 7 / 2,000 | 7 / 2,900 | 3.3 |
| pbox (600) | 1tnk | 1 (700) | 2 / 0 | 3 / 0 | 0.0 |
| pbox (600) | 2tnk | 0 (0) | 1 / 0 | 2 / 0 | 0.0 |
| pbox (600) | arty | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| pbox (600) | ctnk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| pbox (600) | stnk | 1 (1000) | 2 / 0 | 2 / 0 | 0.0 |
| pbox (600) | apc | 3 (2550) | 3 / 850 | 3 / 850 | 1.4 |
| pbox (600) | ftrk | 11 (6600) | 5 / 1,800 | 5 / 1,800 | 3.0 |
| pbox (600) | 3tnk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| pbox (600) | 4tnk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| pbox (600) | v2rl | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| pbox (600) | ttnk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| hbox (750) | e1 | >=200 (>=20000) | 23 / 2,100 | 25 / 1,900 | 2.8 |
| hbox (750) | e3 | 8 (2400) | 6 / 1,500 | 29 / 8,040 | 2.0 |
| hbox (750) | e2 | >=200 (>=30000) | 12 / 1,380 | 17 / 1,890 | 1.8 |
| hbox (750) | e4 | 31 (9300) | 9 / 2,040 | 11 / 2,100 | 2.7 |
| hbox (750) | shok | 14 (4900) | 8 / 1,750 | 8 / 1,750 | 2.3 |
| hbox (750) | jeep | 22 (11000) | 7 / 2,000 | 8 / 2,400 | 2.7 |
| hbox (750) | 1tnk | 1 (700) | 2 / 0 | 3 / 0 | 0.0 |
| hbox (750) | 2tnk | 0 (0) | 1 / 0 | 2 / 0 | 0.0 |
| hbox (750) | arty | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| hbox (750) | ctnk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| hbox (750) | stnk | 1 (1000) | 2 / 0 | 2 / 0 | 0.0 |
| hbox (750) | apc | 3 (2550) | 3 / 850 | 3 / 850 | 1.1 |
| hbox (750) | ftrk | 11 (6600) | 5 / 1,800 | 5 / 1,800 | 2.4 |
| hbox (750) | 3tnk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| hbox (750) | 4tnk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| hbox (750) | v2rl | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| hbox (750) | ttnk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| gun (800) | e1 | 36 (3600) | 9 / 540 | 11 / 620 | 0.7 |
| gun (800) | e3 | 2 (600) | 2 / 300 | 21 / 5,280 | 0.4 |
| gun (800) | e2 | 8 (1200) | 4 / 300 | 13 / 1,830 | 0.4 |
| gun (800) | e4 | 5 (1500) | 3 / 600 | 6 / 1,020 | 0.8 |
| gun (800) | shok | 3 (1050) | 3 / 350 | 3 / 350 | 0.4 |
| gun (800) | jeep | 36 (18000) | 8 / 3,500 | 9 / 3,900 | 4.4 |
| gun (800) | 1tnk | 6 (4200) | 4 / 1,400 | 4 / 1,400 | 1.8 |
| gun (800) | 2tnk | 1 (850) | 2 / 850 | 2 / 850 | 1.1 |
| gun (800) | arty | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| gun (800) | ctnk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| gun (800) | stnk | 2 (2000) | 2 / 1,000 | 3 / 1,000 | 1.2 |
| gun (800) | apc | 23 (19550) | 7 / 3,400 | 7 / 3,740 | 4.2 |
| gun (800) | ftrk | 16 (9600) | 6 / 2,400 | 6 / 2,400 | 3.0 |
| gun (800) | 3tnk | 1 (1150) | 2 / 0 | 2 / 690 | 0.0 |
| gun (800) | 4tnk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| gun (800) | v2rl | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| gun (800) | ttnk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| ftur (600) | e1 | 133 (13300) | 28 / 2,160 | 29 / 2,420 | 3.6 |
| ftur (600) | e3 | 5 (1500) | 5 / 780 | 22 / 6,300 | 1.3 |
| ftur (600) | e2 | 54 (8100) | 12 / 1,320 | 15 / 1,860 | 2.2 |
| ftur (600) | e4 | 15 (4500) | 9 / 1,980 | 10 / 2,340 | 3.3 |
| ftur (600) | shok | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| ftur (600) | jeep | 40 (20000) | 10 / 3,500 | 11 / 3,200 | 5.8 |
| ftur (600) | 1tnk | 2 (1400) | 2 / 700 | 3 / 700 | 1.2 |
| ftur (600) | 2tnk | 0 (0) | 1 / 0 | 2 / 850 | 0.0 |
| ftur (600) | arty | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| ftur (600) | ctnk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| ftur (600) | stnk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| ftur (600) | apc | 8 (6800) | 4 / 2,550 | 5 / 1,700 | 4.2 |
| ftur (600) | ftrk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| ftur (600) | 3tnk | 0 (0) | 1 / 0 | 2 / 0 | 0.0 |
| ftur (600) | 4tnk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| ftur (600) | v2rl | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| ftur (600) | ttnk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| tsla (1200) | e1 | >=200 (>=20000) | 18 / 1,500 | 20 / 1,500 | 1.2 |
| tsla (1200) | e3 | 9 (2700) | 5 / 1,020 | 34 / 9,540 | 0.8 |
| tsla (1200) | e2 | 42 (6300) | 9 / 1,050 | 20 / 2,700 | 0.9 |
| tsla (1200) | e4 | 24 (7200) | 7 / 1,800 | 9 / 1,500 | 1.5 |
| tsla (1200) | shok | 11 (3850) | 7 / 1,050 | 7 / 1,050 | 0.9 |
| tsla (1200) | jeep | 37 (18500) | 9 / 3,500 | 11 / 3,000 | 2.9 |
| tsla (1200) | 1tnk | 5 (3500) | 4 / 1,400 | 4 / 1,820 | 1.2 |
| tsla (1200) | 2tnk | 1 (850) | 2 / 850 | 3 / 680 | 0.7 |
| tsla (1200) | arty | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| tsla (1200) | ctnk | 1 (1350) | 2 / 0 | 2 / 0 | 0.0 |
| tsla (1200) | stnk | 3 (3000) | 3 / 1,000 | 4 / 0 | 0.8 |
| tsla (1200) | apc | 21 (17850) | 7 / 2,550 | 7 / 4,080 | 2.1 |
| tsla (1200) | ftrk | 19 (11400) | 7 / 2,400 | 7 / 2,400 | 2.0 |
| tsla (1200) | 3tnk | 1 (1150) | 2 / 1,150 | 2 / 1,150 | 1.0 |
| tsla (1200) | 4tnk | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| tsla (1200) | v2rl | 0 (0) | 1 / 0 | 1 / 0 | 0.0 |
| tsla (1200) | ttnk | 1 (1350) | 2 / 0 | 2 / 0 | 0.0 |


## 5. Summary for production decisions [C] (read with section 6)

**Against infantry.**
- *Rifleman (both sides):* the best value in the model. It has 1,056 effective DPS and 5,000 HP per $100, and at $6,000 a side it beats 15 of 17 units; only the APC and the Mammoth beat it.
- *Allies:* pbox (607 per $100; one rifleman per 1.4 s volley; about 23 massed rifles to kill it). Arty is strong against clumps, winning 13 of 17 at $2,000.
- *Soviets:* ftur (473 per $100; about 28 rifles). Dogs win only when they reach infantry first; at $6,000 they lose to rifles.

**Against vehicles.**
- *Rocket soldiers (both sides):* 833 DPS per $100 against Heavy armour. At equal cost they beat every gun tank from the 1tnk to the Mammoth (+$4.0k to +$4.8k at $6,000), but lose to anything that shoots infantry, including the Tesla tank.
- *Allies:* 2tnk. It beats the 1tnk and loses narrowly to the 3tnk at equal cost.
- *Soviets:* 3tnk (wins 12 of 17 at $6,000). Grenadiers also beat every tank at equal cost.
- *Defenses:* gun (625 per $100 against Heavy; kills $1,400 of light tanks) and tsla (422 against any armour; $1,400 of 1tnk or $1,150 of 3tnk).

**Against buildings.**
- *Soviets:* e2 (1,667 Wood DPS per $100), e4 (1,562), v2rl (436).
- *Allies:* e3 (617), e1 (375) and arty (318). Arty outranges every defense and kills them without loss.

**Defenses.**
- *Against infantry:* pbox or ftur ($600, -20 power). A rifle trickle never kills them; a massed rifle attack loses about $2,100 per kill.
- *Against armour:* gun ($800) or tsla ($1,200, -80 power, off at low power).
- *Weak points:* pbox, hbox and ftur are worthless against tanks: two 1tnk or one 2tnk, 3tnk or Mammoth kill them for nothing. Everything is outranged by arty (12) and v2rl (10).
- *Screen:* an equal-cost rifle screen mainly punishes rockets, raising e3 losses against a pbox from $1,500 to about $7,000.

**Poor value.**
- *Tech-centre units:* stnk (wins 1 of 17 at $6,000), ctnk (4) and ttnk (5). The Mammoth is strong per unit but loses to the 2tnk and 3tnk per credit.
- *Others:* dog (4 of 17), jeep and APC against armour, ftrk against tanks, Tanya (one allowed; infantry only) and hbox (a pbox plus $150 for a cloak).

**Mixes** ($6,000). The opponents are armies weighted like `R\ai.yaml` `UnitBuilderBotModule@normal` (e1 65, e3 30, 1tnk 40, 2tnk 50, 3tnk 50, v2rl 40 ...), plus pure rifles and pure tanks, all using engine-like nearest targeting. Each cell gives our wins out of 5 and mean value left, first with nearest targeting, then with "smart" targeting, where each unit type focuses the enemy type it hurts most per HP.

| Our mix (Allies) | Cost | vs AI-Allied | vs AI-Soviet | vs 60 e1 | vs 7 2tnk | vs 5 3tnk |
|---|---|---|---|---|---|---|
| **60 e1** | 6,000 | 0/5 · 5/5 +4.4k | 0/5 · 5/5 +2.7k | 3/5 +0.4k · 0/5 | 5/5 +3.6k · 5/5 +4.0k | 5/5 +2.6k · 5/5 +3.9k |
| **30 e1 + 10 e3** | 6,000 | 5/5 +2.2k · 5/5 +4.9k | 0/5 · 4/5 +1.0k | 0/5 · 0/5 | 5/5 +5.0k · 5/5 +5.1k | 5/5 +4.4k · 5/5 +5.3k |
| **6 1tnk + 18 e1** | 6,000 | 0/5 · 3/5 +0.3k | 0/5 · 4/5 +0.9k | 5/5 +2.8k · 5/5 +3.8k | 0/5 · 0/5 | 0/5 · 0/5 |
| **5 2tnk + 17 e1** | 5,950 | 5/5 +1.7k · 5/5 +3.5k | 1/5 +0.1k · 5/5 +1.6k | 5/5 +3.6k · 5/5 +4.0k | 2/5 +0.4k · 5/5 +1.6k | 1/5 +0.1k · 0/5 |
| **7 2tnk** | 5,950 | 0/5 · 0/5 | 0/5 · 0/5 | 0/5 · 0/5 | 1/5 +0.1k · 3/5 +0.4k | 0/5 · 0/5 |
| **4 2tnk + 6 e3 + 8 e1** | 6,000 | 2/5 +0.4k · 5/5 +2.8k | 0/5 · 0/5 | 2/5 +0.5k · 2/5 +1.0k | 5/5 +2.9k · 5/5 +2.7k | 5/5 +2.4k · 5/5 +2.6k |
| **3 1tnk + 3 2tnk + 13 e1** | 5,950 | 0/5 · 4/5 +1.6k | 0/5 · 5/5 +1.0k | 5/5 +2.7k · 5/5 +3.6k | 0/5 · 1/5 +0.1k | 0/5 · 0/5 |
| **2 arty + 3 2tnk + 17 e1** | 5,950 | 5/5 +2.6k · 5/5 +4.5k | 5/5 +2.2k · 5/5 +2.3k | 5/5 +4.4k · 5/5 +4.8k | 5/5 +2.4k · 5/5 +2.6k | 5/5 +2.4k · 5/5 +2.2k |
| **2 arty + 4 2tnk + 8 e1** | 5,900 | 5/5 +1.5k · 5/5 +3.5k | 3/5 +0.3k · 4/5 +0.9k | 5/5 +4.7k · 5/5 +4.6k | 2/5 +0.5k · 3/5 +0.7k | 0/5 · 0/5 |

| Our mix (Soviet) | Cost | vs AI-Allied | vs AI-Soviet | vs 60 e1 | vs 7 2tnk | vs 5 3tnk |
|---|---|---|---|---|---|---|
| **60 e1** | 6,000 | 0/5 · 5/5 +4.4k | 0/5 · 5/5 +2.7k | 3/5 +0.4k · 0/5 | 5/5 +3.6k · 5/5 +4.0k | 5/5 +2.6k · 5/5 +3.9k |
| **30 e1 + 20 e2** | 6,000 | 0/5 · 5/5 +4.0k | 0/5 · 5/5 +2.1k | 0/5 · 0/5 | 5/5 +4.6k · 5/5 +4.6k | 5/5 +2.9k · 5/5 +4.5k |
| **4 3tnk + 14 e1** | 6,000 | 3/5 +1.0k · 5/5 +3.1k | 0/5 · 4/5 +0.8k | 5/5 +3.0k · 5/5 +4.2k | 4/5 +0.5k · 5/5 +1.7k | 5/5 +1.1k · 0/5 |
| **5 3tnk + 2 e1** | 5,950 | 0/5 · 0/5 | 0/5 · 0/5 | 2/5 +0.3k · 0/5 | 5/5 +0.9k · 5/5 +2.1k | 0/5 · 0/5 |
| **3 3tnk + 2 v2rl + 7 e1** | 5,950 | 4/5 +0.7k · 0/5 | 5/5 +1.1k · 5/5 +1.3k | 5/5 +3.3k · 5/5 +2.9k | 4/5 +0.9k · 5/5 +1.0k | 1/5 +0.2k · 0/5 |
| **3 3tnk + 17 e2** | 6,000 | 5/5 +2.8k · 5/5 +2.2k | 3/5 +0.3k · 0/5 | 5/5 +3.2k · 3/5 +1.2k | 5/5 +3.0k · 5/5 +3.0k | 5/5 +2.9k · 5/5 +2.2k |
| **3 3tnk + 4 ftrk + 1 e1** | 5,950 | 0/5 · 0/5 | 0/5 · 0/5 | 2/5 +1.0k · 2/5 +0.9k | 0/5 · 0/5 | 0/5 · 0/5 |
| **2 4tnk + 20 e1** | 6,000 | 4/5 +1.0k · 5/5 +3.5k | 0/5 · 5/5 +3.0k | 5/5 +4.8k · 5/5 +4.8k | 0/5 · 4/5 +0.5k | 1/5 +0.0k · 5/5 +1.9k |
| **4 v2rl + 24 e1** | 6,000 | 5/5 +2.0k · 5/5 +3.4k | 1/5 +0.0k · 5/5 +2.7k | 5/5 +4.6k · 5/5 +4.0k | 5/5 +3.1k · 5/5 +2.9k | 5/5 +3.2k · 5/5 +2.8k |
| **4 apc + 24 e1** | 5,800 | 0/5 · 5/5 +1.1k | 0/5 · 5/5 +0.8k | 5/5 +4.7k · 5/5 +4.7k | 0/5 · 0/5 | 0/5 · 0/5 |

**Targeting is worth as much as composition.** With smart targeting, 60 rifles beat both AI armies with $2.7k to $4.4k left; with nearest targeting they lose. The platform's `attack` order with a target id makes smart targeting possible.

**Strongest mixes**

| Faction | Mix | Result |
|---|---|---|
| Allies | 2 arty + 3 2tnk + 17 e1 | Won every test in both modes |
| Allies | 5 2tnk + 17 e1 | Beats AI-Allied and rifles in both modes; AI-Soviet only with smart targeting |
| Allies | 30 e1 + 10 e3 | Beats AI-Allied and tanks in both modes; AI-Soviet only with smart; loses to pure rifles |
| Allies | 2 arty + 4 2tnk + 8 e1 | Beats both AI armies (AI-Soviet 3/5 to 4/5); loses to 5 3tnk |
| Soviets | 4 v2rl + 24 e1 | Beat 5 of 5 opponents with smart targeting, 4 of 5 with nearest (AI-Soviet 1/5) |
| Soviets | 3 3tnk + 2 v2rl + 7 e1 | Beats AI-Soviet and rifles in both modes; AI-Allied with nearest only |
| Soviets | 3 3tnk + 17 e2 | Beats AI-Allied, rifles and tanks; AI-Soviet only with nearest (3/5) |
| Soviets | 2 4tnk + 20 e1 | Smart targeting wins all but 7 2tnk (4/5); needs the tech centre |

**Avoid:** pure tank armies and 3tnk + ftrk. 6 1tnk + 18 e1 and 4 apc + 24 e1 win only with smart targeting.


## 6. Read, computed, assumed, and how the model can be wrong

- **[F]/[E]:** costs, HP, armour, speeds, prerequisites, factions, power, and the weapon fields (range, reload, burst, damage, `Versus`, spread, falloff, inaccuracy, valid targets). Also the prone, Tesla, leap and crush rules.
- **[C]:** everything else numeric.
- **[M]:** spacing, targeting, straight-line closing, the jitters, fractional units and the reference armies.

**How the model can be wrong:**
1. **Expected damage hides misses.** One 155mm shell has an expected 7,370 against a rifleman, so the model says "dead", but a real miss leaves him alive. Splash and single-shot weapons look more reliable than they are, and close results (`~`, small margins) can flip.
2. **Splash depends on clump density.** The model assumes about 4 infantry per cell. Spread-out infantry blunts V2s, arty, grenades and flame; tighter blobs make them stronger.
3. **No crushing.** Tanks driven through infantry would do better against rifles than shown.
4. **No micro or terrain.** The model has no kiting, retreat, retarget delay, turret turning (Mammoth 8, gun 48 per tick), pathing or chokepoints, and armies arrive as one clump rather than strung out.
5. **Omitted mechanics.** The model leaves out veterancy, repair, medics, the Mammoth's self-heal, the hbox cloak and Tesla low-power shutdown.
6. **Version.** These are development-branch values.

## 7. Corrections to `red-alert-rush-vs-defense.md`

| Item | Earlier | Now | Why |
|---|---|---|---|
| Default `BurstDelays` 5; prone first hit full, half speed | [M] | confirmed; prone lasts 50 ticks from the last hit | [E] `WeaponInfo.cs`, `TakeCover.cs` |
| Tesla vs armour | 3 zaps per ~5 s, 5,952 DPS | 3 per 148 ticks, **5,068 DPS** | [E] `AttackTesla.cs` 22-tick charge |
| Tesla vs infantry | "3 per ~5 s" | about **26 riflemen/min** (each new target pays the charge) | as above |
| Arty / e4 / e2 vs None | 4,059 / 4,102 / 1,500 (every shot hits) | **2,169 / 2,054 / 934** | [F] `Inaccuracy` 1c138 / 853 / 554; [E] `Bullet.cs` |
| Rifles to kill alone: pbox / ftur / gun / tsla | 23 / 20 / 8 / 22 | **23 / 28 / 9 / 18** | splash, Tesla charge, jitters |
| Light tanks to kill a tsla | 4, losing 3 | 4, losing 2 | per-target charge |

## 8. Artillery stand-off, kiting and screens

This section adds repositioning to the model for the two long-range units, arty (range 12, minimum 4) and v2rl (range 10, minimum 4). Screened artillery is the main case. It uses the same tags as above. Data: `standoff_kiting.csv` (column `section` = speed_range, kite_analytic, standoff, kite_baseline, screen_sweep, screen_flank, screen_vs_ref, screen_vs_ref_noarty, retreat, base_attack). Code: `tmp\unit-matchups\siege.py`, with the runner and generator listings `siege_runner.md` and `siege_report.md`. Sections 1–7 are unchanged.

**Fire-while-moving rules.**

| Fact | arty | v2rl | Source |
|---|---|---|---|
| Turret | none | none | [F] `R\vehicles.yaml` ARTY/V2RL `AttackFrontal`, no `Turreted` |
| Must face the target exactly to fire | yes (`FacingTolerance: 0`) | yes | [F] same |
| Fires while moving | no: frontal attackers stop to shoot | no | [E] `Traits\Attack\AttackFrontal.cs`, `Activities\Attack.cs` |
| Body turn speed | `TurnSpeed: 8` -> **64 ticks (2.6 s) per 180°** | 20 (`^Vehicle`) -> **26 ticks (1.0 s)** | [F] `Mobile.TurnSpeed` (WAngle/tick); [E] `Mobile.cs` |
| Turns in place before moving; no reversing | yes; `CanMoveBackward` false | yes | [E] `Activities\Move\Move.cs` (`TurnsWhileMoving` false -> `Turn`), `Mobile.cs`; not overridden in `R` |
| Reload | 85 ticks (3.4 s) | 215 ticks (8.6 s) | [F] `W\ballistics.yaml` 155mm, `W\missiles.yaml` SCUD |
| Speed | 72 -> 1.76 cells/s (`lighttracked`) | 72 -> 1.76 cells/s (`wheeled`) | [F] |
| Shell flight at max range | 12 cells / 170 per tick = 72 ticks (2.9 s) | 10 cells / 170 = 60 ticks (2.4 s) | [F] `Projectile Speed` |
| Order rate | 1 batch per 0.2 s: an order takes effect on the next 5-tick boundary | | [F] platform `min_batch_interval_s`; [M] no extra agent latency |
| Building repair | 700 HP every 24 ticks = **729 HP/s** per building, one repairer | | [F] `R\defaults.yaml` `^Building RepairableBuilding RepairStep: 700`; [E] `RepairableBuilding.cs` `RepairInterval = 24` |

Turreted screen units (1tnk, 2tnk, 3tnk, 4tnk, jeep, ftrk, ttnk, stnk) fire while moving. Infantry, apc, ctnk, arty and v2rl stop to fire [F] `AttackTurreted`/`AttackFrontal`.

**Fixes.** No number in sections 1–7 was changed. Three bugs in the new code were fixed before any result here was produced:
1. Enemy armies built from fractional counts had been rounded up to whole units, so the enemy had slightly more money than intended.
2. The construction yard in the base scenario was placed at a wrong coordinate.
3. A screen-only (no artillery) run crashed.

### 8.1 Speed and range: closing times [F] speeds/ranges, [C] times


Times are to walk from 12 or 10 cells away to the unit's own firing range, at full speed (prone infantry move at half speed, shown separately). The dog runs at 1.5x while attacking. A unit closes on artillery only if it is faster than 1.76 cells/s or the artillery stops moving. **Faster than arty and v2rl:** dog, jeep, 1tnk, apc, ftrk, ttnk, ctnk, stnk. The 2tnk is equal.

| Unit | Cells/s | Prone cells/s | Max range | Close from 12 (s) | Close from 10 (s) | From 12 if prone (s) | 180 deg turn (ticks) | Fires while moving | Faster than arty/V2 |
|---|---|---|---|---|---|---|---|---|---|
| **e1** | 1.32 | 0.66 | 5.0 | 5.3 | 3.8 | 10.6 | 1 | no | no |
| **e3** | 1.32 | 0.66 | 5.0 | 5.3 | 3.8 | 10.6 | 1 | no | no |
| **e2** | 1.66 | 0.83 | 4.0 | 4.8 | 3.6 | 9.6 | 1 | no | no |
| **e4** | 1.32 | 0.66 | 5.0 | 5.3 | 3.8 | 10.6 | 1 | no | no |
| **dog** | 2.44 | - | 3.0 | 2.5 | 1.9 | - | 1 | no | **yes** |
| **shok** | 1.32 | 0.66 | 6.0 | 4.6 | 3.0 | 9.1 | 1 | no | no |
| **e7** | 1.66 | 0.83 | 7.0 | 3.0 | 1.8 | 6.0 | 1 | no | no |
| **e6** | 1.32 | 0.66 | - | - | - | - | 1 | no | no |
| **medi** | 1.2 | 0.6 | - | - | - | - | 1 | no | no |
| **spy** | 1.32 | 0.66 | 2.5 | 7.2 | 5.7 | 14.4 | 1 | no | no |
| **thf** | 1.76 | - | - | - | - | - | 1 | no | no |
| **jeep** | 4.0 | - | 4.0 | 2.0 | 1.5 | - | 13 | yes | **yes** |
| **1tnk** | 2.76 | - | 4.75 | 2.6 | 1.9 | - | 26 | yes | **yes** |
| **2tnk** | 1.76 | - | 4.75 | 4.1 | 3.0 | - | 26 | yes | no |
| **arty** | 1.76 | - | 12.0 | 0.0 | 0.0 | - | 64 | no | no |
| **ctnk** | 2.1 | - | 6.0 | 2.9 | 1.9 | - | 26 | no | **yes** |
| **stnk** | 3.12 | - | 6.0 | 1.9 | 1.3 | - | 26 | yes | **yes** |
| **apc** | 3.12 | - | 4.0 | 2.6 | 1.9 | - | 26 | no | **yes** |
| **ftrk** | 2.76 | - | 6.0 | 2.2 | 1.4 | - | 13 | yes | **yes** |
| **3tnk** | 1.56 | - | 4.75 | 4.6 | 3.4 | - | 26 | yes | no |
| **4tnk** | 1.05 | - | 6.5 | 5.2 | 3.3 | - | 26 | yes | no |
| **v2rl** | 1.76 | - | 10.0 | 1.1 | 0.0 | - | 26 | no | no |
| **ttnk** | 2.25 | - | 7.0 | 2.2 | 1.3 | - | 26 | yes | **yes** |
| **mnly** | 2.76 | - | - | - | - | - | 26 | no | **yes** |
| **harv** | 1.76 | - | - | - | - | - | 26 | no | no |
| **mcv** | 1.46 | - | - | - | - | - | 26 | no | no |
| **truk** | 2.76 | - | - | - | - | - | 26 | no | **yes** |
| **mgg** | 1.76 | - | - | - | - | - | 26 | no | no |
| **mrj** | 1.66 | - | - | - | - | - | 26 | no | no |
| **qtnk** | 1.12 | - | - | - | - | - | 26 | no | no |
| **dtrk** | 1.64 | - | - | - | - | - | 26 | no | no |


### 8.2 Stand-off against defenses and buildings [C]


- **Setup:** N artillery in a line, firing from the middle of the safe band (or 1 cell inside maximum range for targets that cannot shoot back). The band is outside the target's weapon range and inside the artillery's range, beyond its 4-cell minimum.
- **Losses:** zero in every case. Nothing in this table can reach the artillery.
- **Repaired:** the target is repaired at 729 HP/s from the first hit [M: the AI's `BuildingRepairBotModule` starts at once; repair cost and engineers not modelled].
- **Granularity:** times move in whole volleys, because each shell does its expected damage. That is why repair often adds nothing once two or more artillery fire together.
- **Cost columns:** "Artillery $ / target $" is the money committed per kill. "Target $ killed per min per $1,000" is the destruction rate.


**arty** ($850 each)


| Target ($) | Safe band (cells) | Seconds to kill, N = 1 / 2 / 4 / 8 | Same, target repaired | Slowdown from repair | Artillery $ / target $ | Target $ killed per min per $1,000 | Credits lost |
|---|---|---|---|---|---|---|---|
| **pbox** ($600) | 6 < d <= 12 | 29 / 16 / 9.0 / 5.6 | 50 / 16 / 9.0 / 5.6 | 1.69 / 1.00 / 1.00 / 1.00 | 1.4 / 2.8 / 5.7 / 11.3 | 1443 / 1344 / 1182 / 952 | 0 |
| **ftur** ($600) | 5 < d <= 12 | 29 / 16 / 8.8 / 5.4 | 50 / 16 / 8.8 / 5.4 | 1.70 / 1.00 / 1.00 / 1.00 | 1.4 / 2.8 / 5.7 / 11.3 | 1448 / 1354 / 1198 / 973 | 0 |
| **gun** ($800) | 6.5 < d <= 12 | 29 / 16 / 9.0 / 5.6 | 50 / 16 / 9.0 / 5.6 | 1.69 / 1.00 / 1.00 / 1.00 | 1.1 / 2.1 / 4.2 / 8.5 | 1921 / 1787 / 1569 / 1261 | 0 |
| **tsla** ($1200) | 7 < d <= 12 | 30 / 16 / 9.1 / 5.7 | 50 / 16 / 9.1 / 5.7 | 1.69 / 1.00 / 1.00 / 1.00 | 0.7 / 1.4 / 2.8 / 5.7 | 2873 / 2667 / 2332 / 1864 | 0 |
| **agun** ($800) | 4 < d <= 12 | 30 / 16 / 9.4 / 6.0 | 54 / 16 / 9.4 / 6.0 | 1.80 / 1.00 / 1.00 / 1.00 | 1.1 / 2.1 / 4.2 / 8.5 | 1892 / 1739 / 1496 / 1169 | 0 |
| **sam** ($700) | 4 < d <= 12 | 30 / 16 / 9.4 / 6.0 | 54 / 16 / 9.4 / 6.0 | 1.80 / 1.00 / 1.00 / 1.00 | 1.2 / 2.4 / 4.9 / 9.7 | 1656 / 1521 / 1309 / 1023 | 0 |
| **powr** ($300) | 4 < d <= 12 | 16 / 9.4 / 6.0 / 2.7 | 20 / 9.4 / 6.0 / 2.7 | 1.21 / 1.00 / 1.00 / 1.00 | 2.8 / 5.7 / 11.3 / 22.7 | 1304 / 1122 / 877 / 988 | 0 |
| **tent** ($500) | 4 < d <= 12 | 23 / 13 / 6.0 / 2.8 | 30 / 13 / 6.0 / 2.8 | 1.30 / 1.00 / 1.00 / 1.00 | 1.7 / 3.4 / 6.8 / 13.6 | 1532 / 1374 / 1461 / 1598 | 0 |
| **barr** ($500) | 4 < d <= 12 | 23 / 13 / 6.0 / 2.8 | 30 / 13 / 6.0 / 2.8 | 1.30 / 1.00 / 1.00 / 1.00 | 1.7 / 3.4 / 6.8 / 13.6 | 1532 / 1374 / 1461 / 1598 | 0 |
| **proc** ($1400) | 4 < d <= 12 | 33 / 16 / 9.4 / 6.0 | 47 / 20 / 9.4 / 6.0 | 1.41 / 1.21 / 1.00 / 1.00 | 0.6 / 1.2 / 2.4 / 4.9 | 2973 / 3043 / 2617 / 2045 | 0 |
| **weap** ($2000) | 4 < d <= 12 | 57 / 30 / 16 / 9.4 | 77 / 33 / 16 / 9.4 | 1.36 / 1.11 / 1.00 / 1.00 | 0.4 / 0.8 / 1.7 / 3.4 | 2475 / 2366 / 2173 / 1869 | 0 |
| **fact** ($2000) | 4 < d <= 12 | 57 / 30 / 16 / 9.4 | 77 / 33 / 16 / 9.4 | 1.36 / 1.11 / 1.00 / 1.00 | 0.4 / 0.8 / 1.7 / 3.4 | 2475 / 2366 / 2173 / 1869 | 0 |


**v2rl** ($900 each)


| Target ($) | Safe band (cells) | Seconds to kill, N = 1 / 2 / 4 / 8 | Same, target repaired | Slowdown from repair | Artillery $ / target $ | Target $ killed per min per $1,000 | Credits lost |
|---|---|---|---|---|---|---|---|
| **pbox** ($600) | 6 < d <= 10 | 19 / 10 / 2.0 / 2.0 | 19 / 10 / 2.0 / 2.0 | 1.00 / 1.00 / 1.00 / 1.00 | 1.5 / 3.0 / 6.0 / 12.0 | 2092 / 1901 / 5102 / 2551 | 0 |
| **ftur** ($600) | 5 < d <= 10 | 19 / 10 / 1.8 / 1.8 | 19 / 10 / 1.8 / 1.8 | 1.00 / 1.00 / 1.00 / 1.00 | 1.5 / 3.0 / 6.0 / 12.0 | 2105 / 1923 / 5435 / 2717 | 0 |
| **gun** ($800) | 6.5 < d <= 10 | 19 / 11 / 2.0 / 2.0 | 19 / 11 / 2.0 / 2.0 | 1.00 / 1.00 / 1.00 / 1.00 | 1.1 / 2.2 / 4.5 / 9.0 | 2784 / 2525 / 6667 / 3333 | 0 |
| **tsla** ($1200) | 7 < d <= 10 | 19 / 11 / 2.0 / 2.0 | 19 / 11 / 2.0 / 2.0 | 1.00 / 1.00 / 1.00 / 1.00 | 0.8 / 1.5 / 3.0 / 6.0 | 4158 / 3759 / 9804 / 4902 | 0 |
| **agun** ($800) | 4 < d <= 10 | 19 / 11 / 2.2 / 2.2 | 19 / 11 / 2.2 / 2.2 | 1.00 / 1.00 / 1.00 / 1.00 | 1.1 / 2.2 / 4.5 / 9.0 | 2755 / 2478 / 6173 / 3086 | 0 |
| **sam** ($700) | 4 < d <= 10 | 19 / 11 / 2.2 / 2.2 | 19 / 11 / 2.2 / 2.2 | 1.00 / 1.00 / 1.00 / 1.00 | 1.3 / 2.6 / 5.1 / 10.3 | 2410 / 2169 / 5401 / 2701 | 0 |
| **powr** ($300) | 4 < d <= 10 | 11 / 2.2 / 2.2 / 2.2 | 11 / 2.2 / 2.2 / 2.2 | 1.00 / 1.00 / 1.00 / 1.00 | 3.0 / 6.0 / 12.0 / 24.0 | 1859 / 4630 / 2315 / 1157 | 0 |
| **tent** ($500) | 4 < d <= 10 | 11 / 2.2 / 2.2 / 2.2 | 11 / 2.2 / 2.2 / 2.2 | 1.00 / 1.00 / 1.00 / 1.00 | 1.8 / 3.6 / 7.2 / 14.4 | 3098 / 7716 / 3858 / 1929 | 0 |
| **barr** ($500) | 4 < d <= 10 | 11 / 2.2 / 2.2 / 2.2 | 11 / 2.2 / 2.2 / 2.2 | 1.00 / 1.00 / 1.00 / 1.00 | 1.8 / 3.6 / 7.2 / 14.4 | 3098 / 7716 / 3858 / 1929 | 0 |
| **proc** ($1400) | 4 < d <= 10 | 19 / 11 / 2.2 / 2.2 | 28 / 11 / 2.2 / 2.2 | 1.44 / 1.00 / 1.00 / 1.00 | 0.6 / 1.3 / 2.6 / 5.1 | 4821 / 4337 / 10802 / 5401 | 0 |
| **weap** ($2000) | 4 < d <= 10 | 37 / 19 / 11 / 2.2 | 45 / 19 / 11 / 2.2 | 1.24 / 1.00 / 1.00 / 1.00 | 0.5 / 0.9 / 1.8 / 3.6 | 3647 / 3444 / 3098 / 7440 | 0 |
| **fact** ($2000) | 4 < d <= 10 | 37 / 19 / 11 / 2.2 | 45 / 19 / 11 / 2.2 | 1.24 / 1.00 / 1.00 / 1.00 | 0.5 / 0.9 / 1.8 / 3.6 | 3647 / 3444 / 3098 / 7440 | 0 |


### 8.3 Unscreened kiting (baseline) [C]


**Analytic hold condition** [C]. One kite cycle is: fire, turn away (T), drive away for a leg of x ticks, turn back (T). Each order also waits for the next 5-tick boundary.
- **Holding range:** the artillery keeps its distance if `x >= 2(T+5) v_enemy / (v_art - v_enemy)`. That is only possible if the enemy is slower than 1.76 cells/s.
- **Fire rate:** the artillery then fires once per `max(reload, 2T + 10 + x)`.
- **arty:** 2T = 128 ticks is already longer than its 85-tick reload, so every retreat costs shots.
- **v2rl:** it gets about 6.5 s of free driving per 8.6 s reload. It holds range at full fire rate against anything slower than about 1.25 cells/s (1.33 ignoring the order delays): 87 % against upright infantry, 100 % against prone infantry or the Mammoth.

| Artillery | Enemy | Enemy cells/s | Can hold range | Retreat leg needed (ticks) | Shot every (s) while holding | Shot every (s) standing | Fire rate kept |
|---|---|---|---|---|---|---|---|
| arty | e1 | 1.32 | yes | 414 | 22.1 | 3.4 | 15% |
| arty | e1 (prone) | 0.66 | yes | 83 | 8.8 | 3.4 | 38% |
| arty | e3 | 1.32 | yes | 414 | 22.1 | 3.4 | 15% |
| arty | e3 (prone) | 0.66 | yes | 83 | 8.8 | 3.4 | 38% |
| arty | e2 | 1.66 | yes | 2346 | 99.4 | 3.4 | 3% |
| arty | e2 (prone) | 0.83 | yes | 124 | 10.5 | 3.4 | 32% |
| arty | e4 | 1.32 | yes | 414 | 22.1 | 3.4 | 15% |
| arty | e4 (prone) | 0.66 | yes | 83 | 8.8 | 3.4 | 38% |
| arty | dog | 3.66 | **no** | - | - | 3.4 | 0% |
| arty | shok | 1.32 | yes | 414 | 22.1 | 3.4 | 15% |
| arty | shok (prone) | 0.66 | yes | 83 | 8.8 | 3.4 | 38% |
| arty | jeep | 4.0 | **no** | - | - | 3.4 | 0% |
| arty | 1tnk | 2.76 | **no** | - | - | 3.4 | 0% |
| arty | 2tnk | 1.76 | **no** | - | - | 3.4 | 0% |
| arty | apc | 3.12 | **no** | - | - | 3.4 | 0% |
| arty | ftrk | 2.76 | **no** | - | - | 3.4 | 0% |
| arty | 3tnk | 1.56 | yes | 1104 | 49.7 | 3.4 | 7% |
| arty | 4tnk | 1.05 | yes | 205 | 13.7 | 3.4 | 25% |
| arty | ttnk | 2.25 | **no** | - | - | 3.4 | 0% |
| arty | ctnk | 2.1 | **no** | - | - | 3.4 | 0% |
| arty | stnk | 3.12 | **no** | - | - | 3.4 | 0% |
| v2rl | e1 | 1.32 | yes | 186 | 9.9 | 8.6 | 87% |
| v2rl | e1 (prone) | 0.66 | yes | 38 | 8.6 | 8.6 | 100% |
| v2rl | e3 | 1.32 | yes | 186 | 9.9 | 8.6 | 87% |
| v2rl | e3 (prone) | 0.66 | yes | 38 | 8.6 | 8.6 | 100% |
| v2rl | e2 | 1.66 | yes | 1054 | 44.6 | 8.6 | 19% |
| v2rl | e2 (prone) | 0.83 | yes | 56 | 8.6 | 8.6 | 100% |
| v2rl | e4 | 1.32 | yes | 186 | 9.9 | 8.6 | 87% |
| v2rl | e4 (prone) | 0.66 | yes | 38 | 8.6 | 8.6 | 100% |
| v2rl | dog | 3.66 | **no** | - | - | 8.6 | 0% |
| v2rl | shok | 1.32 | yes | 186 | 9.9 | 8.6 | 87% |
| v2rl | shok (prone) | 0.66 | yes | 38 | 8.6 | 8.6 | 100% |
| v2rl | jeep | 4.0 | **no** | - | - | 8.6 | 0% |
| v2rl | 1tnk | 2.76 | **no** | - | - | 8.6 | 0% |
| v2rl | 2tnk | 1.76 | **no** | - | - | 8.6 | 0% |
| v2rl | apc | 3.12 | **no** | - | - | 8.6 | 0% |
| v2rl | ftrk | 2.76 | **no** | - | - | 8.6 | 0% |
| v2rl | 3tnk | 1.56 | yes | 496 | 22.3 | 8.6 | 39% |
| v2rl | 4tnk | 1.05 | yes | 92 | 8.6 | 8.6 | 100% |
| v2rl | ttnk | 2.25 | **no** | - | - | 8.6 | 0% |
| v2rl | ctnk | 2.1 | **no** | - | - | 8.6 | 0% |
| v2rl | stnk | 3.12 | **no** | - | - | 8.6 | 0% |


**Simulated baseline (no screen)** [C]. N artillery against N enemies of one type, starting at maximum range.
- *Stand* never moves.
- *Kite* turns and retreats after each shot, for the best leg in {25, 50, 100, 150, 200} ticks, with at most 15 cells of room behind.
- *Contact* is the first moment an enemy has an artillery piece inside its own weapon range.

Dogs cannot attack vehicles. Their rows are trivial, and a 3 v 3 run timed out with no shot fired.


**arty**: N arty vs N enemies. Cell = shots before contact · enemy HP lost before contact · result (W + value left).


| Enemy | N=1 stand | N=1 kite | N=3 stand | N=3 kite | N=6 stand | N=6 kite |
|---|---|---|---|---|---|---|
| **e1** | 1 · 100% · W0.8k | 1 · 100% · W0.8k (leg 25) | 3 · 100% · W2.5k | 3 · 100% · W2.5k (leg 25) | 6 · 100% · W5.1k | 6 · 100% · W5.1k (leg 25) |
| **e3** | 1 · 100% · W0.8k | 1 · 100% · W0.8k (leg 25) | 3 · 100% · W2.5k | 3 · 100% · W2.5k (leg 25) | 6 · 100% · W5.1k | 6 · 100% · W5.1k (leg 25) |
| **e2** | 1 · 100% · W0.8k | 1 · 100% · W0.8k (leg 25) | 3 · 100% · W2.5k | 3 · 100% · W2.5k (leg 25) | 6 · 100% · W5.1k | 6 · 100% · W5.1k (leg 25) |
| **e4** | 1 · 100% · W0.8k | 1 · 100% · W0.8k (leg 25) | 3 · 100% · W2.5k | 3 · 100% · W2.5k (leg 25) | 6 · 100% · W5.1k | 6 · 100% · W5.1k (leg 25) |
| **dog** | 1 · 100% · W0.8k | 1 · 100% · W0.8k (leg 25) | 0 · 0% · L | 0 · 0% · L (leg 25) | 1 · 100% · W5.1k | 1 · 100% · W5.1k (leg 25) |
| **shok** | 1 · 100% · W0.8k | 1 · 100% · W0.8k (leg 25) | 3 · 100% · W2.5k | 3 · 100% · W2.5k (leg 25) | 6 · 100% · W5.1k | 6 · 100% · W5.1k (leg 25) |
| **jeep** | 1 · 0% · L | 1 · 0% · L (leg 25) | 3 · 0% · W2.1k | 3 · 0% · W2.1k (leg 25) | 6 · 0% · W4.2k | 6 · 0% · W4.0k (leg 25) |
| **1tnk** | 1 · 0% · L | 1 · 0% · L (leg 25) | 3 · 0% · L | 3 · 0% · L (leg 25) | 6 · 0% · L | 6 · 0% · L (leg 25) |
| **2tnk** | 2 · 10% · L | 2 · 10% · L (leg 25) | 6 · 15% · L | 5 · 15% · L (leg 25) | 10 · 26% · L | 7 · 26% · L (leg 25) |
| **apc** | 1 · 0% · L | 1 · 0% · L (leg 25) | 3 · 0% · L | 3 · 0% · L (leg 25) | 6 · 0% · L | 6 · 0% · L (leg 25) |
| **ftrk** | 1 · 0% · W0.1k | 1 · 0% · W0.1k (leg 25) | 3 · 0% · W1.2k | 3 · 0% · W1.3k (leg 25) | 6 · 0% · W2.0k | 6 · 0% · W0.6k (leg 25) |
| **3tnk** | 2 · 7% · L | 2 · 7% · L (leg 25) | 6 · 12% · L | 5 · 12% · L (leg 25) | 12 · 20% · L | 8 · 20% · L (leg 25) |
| **4tnk** | 3 · 10% · L | 3 · 10% · L (leg 100) | 8 · 16% · L | 9 · 21% · L (leg 200) | 13 · 26% · L | 18 · 28% · L (leg 200) |
| **ttnk** | 1 · 0% · L | 1 · 0% · L (leg 25) | 3 · 0% · L | 3 · 0% · L (leg 25) | 6 · 0% · L | 6 · 0% · L (leg 25) |
| **ctnk** | 1 · 27% · L | 1 · 27% · L (leg 25) | 3 · 28% · W1.1k | 3 · 28% · L (leg 25) | 6 · 12% · W2.8k | 6 · 12% · W2.0k (leg 25) |
| **stnk** | 1 · 0% · W0.4k | 1 · 0% · W0.3k (leg 25) | 3 · 0% · W2.0k | 3 · 0% · W1.7k (leg 25) | 6 · 0% · W4.0k | 6 · 0% · W3.1k (leg 200) |


**v2rl**: N v2rl vs N enemies. Cell = shots before contact · enemy HP lost before contact · result (W + value left).


| Enemy | N=1 stand | N=1 kite | N=3 stand | N=3 kite | N=6 stand | N=6 kite |
|---|---|---|---|---|---|---|
| **e1** | 1 · 100% · W0.9k | 1 · 100% · W0.9k (leg 25) | 3 · 100% · W2.7k | 3 · 100% · W2.7k (leg 25) | 6 · 100% · W5.4k | 6 · 100% · W5.4k (leg 25) |
| **e3** | 1 · 100% · W0.9k | 1 · 100% · W0.9k (leg 25) | 3 · 100% · W2.7k | 3 · 100% · W2.7k (leg 25) | 6 · 100% · W5.4k | 6 · 100% · W5.4k (leg 25) |
| **e2** | 1 · 100% · W0.9k | 1 · 100% · W0.9k (leg 25) | 3 · 100% · W2.7k | 3 · 100% · W2.7k (leg 25) | 6 · 100% · W5.4k | 6 · 100% · W5.4k (leg 25) |
| **e4** | 1 · 100% · W0.9k | 1 · 100% · W0.9k (leg 25) | 3 · 100% · W2.7k | 3 · 100% · W2.7k (leg 25) | 6 · 100% · W5.4k | 6 · 100% · W5.4k (leg 25) |
| **dog** | 1 · 100% · W0.9k | 1 · 100% · W0.9k (leg 25) | 0 · 0% · L | 0 · 0% · L (leg 25) | 1 · 100% · W5.4k | 1 · 100% · W5.4k (leg 25) |
| **shok** | 1 · 100% · W0.9k | 1 · 100% · W0.9k (leg 25) | 3 · 100% · W2.7k | 3 · 100% · W2.7k (leg 25) | 6 · 100% · W5.4k | 6 · 100% · W5.4k (leg 25) |
| **jeep** | 1 · 0% · W0.8k | 1 · 0% · W0.8k (leg 25) | 3 · 0% · W2.5k | 3 · 0% · W2.5k (leg 25) | 6 · 0% · W4.4k | 6 · 0% · W4.5k (leg 100) |
| **1tnk** | 1 · 0% · L | 1 · 0% · L (leg 25) | 3 · 0% · L | 3 · 0% · W0.9k (leg 150) | 6 · 0% · L | 6 · 0% · L (leg 25) |
| **2tnk** | 1 · 39% · L | 1 · 39% · W0.1k (leg 150) | 3 · 48% · W0.6k | 3 · 48% · W1.1k (leg 100) | 6 · 41% · L | 6 · 41% · W2.2k (leg 150) |
| **apc** | 1 · 0% · L | 1 · 0% · L (leg 25) | 3 · 0% · W2.1k | 3 · 0% · W2.1k (leg 25) | 6 · 0% · W3.1k | 6 · 0% · W2.8k (leg 100) |
| **ftrk** | 1 · 0% · W0.7k | 1 · 0% · W0.7k (leg 25) | 3 · 0% · W2.2k | 3 · 0% · W2.2k (leg 25) | 6 · 0% · W1.0k | 6 · 0% · W2.8k (leg 150) |
| **3tnk** | 1 · 30% · L | 1 · 30% · L (leg 25) | 3 · 36% · L | 3 · 36% · L (leg 25) | 6 · 35% · L | 6 · 35% · L (leg 25) |
| **4tnk** | 1 · 20% · L | 1 · 20% · L (leg 25) | 3 · 24% · L | 3 · 24% · L (leg 25) | 6 · 29% · L | 6 · 29% · L (leg 25) |
| **ttnk** | 1 · 0% · L | 1 · 0% · L (leg 25) | 3 · 0% · L | 3 · 0% · W0.9k (leg 150) | 6 · 0% · L | 6 · 0% · L (leg 25) |
| **ctnk** | 1 · 0% · W0.3k | 1 · 0% · W0.3k (leg 25) | 3 · 0% · W1.3k | 3 · 0% · W1.3k (leg 25) | 6 · 0% · W0.6k | 6 · 0% · W1.9k (leg 150) |
| **stnk** | 1 · 0% · W0.7k | 1 · 0% · W0.7k (leg 25) | 3 · 0% · W2.2k | 3 · 0% · W2.2k (leg 25) | 6 · 0% · W4.5k | 6 · 0% · W4.7k (leg 50) |


### 8.4 Screened artillery (main case) [C]


**Setup** [M]:
- *Screen line:* rows of up to 12 infantry or 8 vehicles, with the front `gap` cells ahead of the artillery's front.
- *Screen behaviour:* screen units hold a post. They engage enemies up to 3 cells away from it and then walk back, which is roughly the defend stance.
- *Artillery (mode b):* fires at anything in its band. When any enemy comes within 6 cells it turns, falls back until the nearest enemy is 9 cells away (at most 6 cells behind its start), and turns back. It creeps forward when nothing is in range, staying 2 cells behind the screen.
- *Modes:* **a** = artillery never moves; **b** = repositioning as above; **c** = b plus smart targeting (each unit hits the enemy type it hurts most per HP).
- *Enemy:* attacks with engine-like nearest targeting, so it engages whatever is closest, usually the screen.
- *Ratio:* screen money to artillery money (2:1 at $4,000 = $2,667 of screen and $1,333 of artillery).
- *Screen types:*
  - rifle = e1;
  - rocket-heavy = 70 % e3 + 30 % e1 by cost;
  - tank = 2tnk (Allies) or 3tnk (Soviets);
  - mixed = 50 % tank + 25 % e1 + 25 % e3;
  - e1+e2 = half and half (Soviets).
- *Breach:* an enemy got within 4 cells of an artillery piece, or the artillery lost a unit or more than 10 % HP.


**Allies** (arty), $4,000 a side, artillery repositioning (mode b). Cell = smallest screen:artillery cost ratio that kept every enemy outside 4 cells and off the artillery in all 3 jitters, at the gap where it worked (W/L = fight result at that setting).


| Enemy | rifle screen | rocket screen | tank screen | mixed screen | Best gap overall |
|---|---|---|---|---|---|
| **e1** | none <=4:1 | none <=4:1 | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 2 |
| **e3** | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 2 |
| **e2** | 0.5:1 @2 (W) | 1:1 @2 (W) | 1:1 @2 (W) | 1:1 @2 (W) | 2 |
| **e4** | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 2 |
| **dog** | 0.5:1 @4 (W) | 0.5:1 @4 (W) | 0.5:1 @4 (W) | 0.5:1 @6 (W) | 4 |
| **shok** | 0.5:1 @2 (W) | 0.5:1 @4 (W) | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 2 |
| **jeep** | 2:1 @2 (W) | none <=4:1 | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 2 |
| **1tnk** | 0.5:1 @4 (W) | 0.5:1 @2 (W) | 1:1 @2 (W) | 0.5:1 @2 (W) | 2 |
| **2tnk** | 0.5:1 @4 (W) | 0.5:1 @2 (W) | none <=4:1 | 1:1 @2 (W) | 2 |
| **apc** | none <=4:1 | none <=4:1 | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 6 |
| **ftrk** | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 2 |
| **3tnk** | 0.5:1 @2 (W) | 0.5:1 @2 (W) | none <=4:1 | 1:1 @2 (W) | 2 |
| **4tnk** | none <=4:1 | 2:1 @6 (W) | 1:1 @2 (W) | 3:1 @6 (W) | 6 |
| **ttnk** | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 2 |
| **ctnk** | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 1:1 @2 (W) | 0.5:1 @2 (W) | 2 |
| **stnk** | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 2 |


**Soviet** (v2rl), $4,000 a side, artillery repositioning (mode b). Cell = smallest screen:artillery cost ratio that kept every enemy outside 4 cells and off the artillery in all 3 jitters, at the gap where it worked (W/L = fight result at that setting).


| Enemy | rifle screen | rocket screen | tank screen | mixed screen | e1+e2 screen | Best gap overall |
|---|---|---|---|---|---|---|
| **e1** | none <=4:1 | none <=4:1 | 2:1 @6 (W) | 2:1 @4 (W) | none <=4:1 | 6 |
| **e3** | 0.5:1 @2 (W) | 0.5:1 @6 (W) | none <=4:1 | 0.5:1 @6 (W) | 0.5:1 @4 (W) | 6 |
| **e2** | 0.5:1 @2 (W) | 3:1 @6 (W) | none <=4:1 | 2:1 @6 (W) | 0.5:1 @2 (W) | 2 |
| **e4** | 0.5:1 @6 (W) | 1:1 @6 (W) | 1:1 @6 (W) | 1:1 @6 (W) | 0.5:1 @6 (W) | 6 |
| **dog** | 0.5:1 @6 (W) | 0.5:1 @4 (W) | 0.5:1 @4 (W) | 0.5:1 @6 (W) | 0.5:1 @4 (W) | 6 |
| **shok** | 1:1 @4 (W) | none <=4:1 | 1:1 @4 (W) | 1:1 @4 (W) | 4:1 @6 (W) | 6 |
| **jeep** | 2:1 @6 (W) | none <=4:1 | 0.5:1 @2 (W) | 0.5:1 @6 (W) | none <=4:1 | 6 |
| **1tnk** | 0.5:1 @6 (W) | 0.5:1 @6 (W) | 1:1 @4 (W) | 1:1 @2 (W) | 0.5:1 @6 (W) | 6 |
| **2tnk** | 0.5:1 @4 (W) | 0.5:1 @2 (W) | none <=4:1 | 1:1 @4 (W) | 0.5:1 @4 (W) | 6 |
| **apc** | none <=4:1 | none <=4:1 | 0.5:1 @6 (W) | 0.5:1 @6 (W) | none <=4:1 | 4 |
| **ftrk** | 0.5:1 @4 (W) | 1:1 @4 (W) | 0.5:1 @4 (W) | 0.5:1 @4 (W) | 0.5:1 @4 (W) | 4 |
| **3tnk** | 0.5:1 @2 (W) | 0.5:1 @4 (W) | none <=4:1 | 0.5:1 @6 (W) | 0.5:1 @6 (W) | 6 |
| **4tnk** | 1:1 @6 (W) | 1:1 @6 (W) | 2:1 @6 (W) | 2:1 @6 (W) | 1:1 @4 (W) | 6 |
| **ttnk** | 0.5:1 @6 (W) | 0.5:1 @4 (W) | 0.5:1 @4 (W) | 0.5:1 @6 (W) | 0.5:1 @6 (W) | 6 |
| **ctnk** | 0.5:1 @2 (W) | 0.5:1 @2 (W) | 0.5:1 @6 (W) | 0.5:1 @6 (W) | 0.5:1 @2 (W) | 2 |
| **stnk** | 0.5:1 @6 (W) | 0.5:1 @6 (W) | 0.5:1 @6 (W) | 0.5:1 @6 (W) | 0.5:1 @6 (W) | 6 |


**Fast attackers and flankers.**
- *Frontal* is the sweep above.
- *Flank:* the same money of enemies starts level with the screen, 6 cells beyond its end. It drives straight at the artillery and prefers it as a target. The screen still engages anything inside its leash.


**Allies**, 2:1 screen, best gap. Cell = frontal / flank: W/L and breach share (b0 = never reached the artillery, b1 = always).


| Enemy | rifle | rocket | tank | mixed |
|---|---|---|---|---|
| **1tnk** | W b0 / W b100 | W b0 / W b100 | W b0 / W b100 | W b0 / W b100 |
| **jeep** | W b0 / W b100 | L b100 / W b100 | W b0 / W b100 | W b0 / W b100 |
| **apc** | L b100 / L b100 | L b100 / W b100 | W b0 / W b100 | W b0 / W b100 |
| **ftrk** | W b0 / W b100 | W b0 / W b100 | W b0 / W b100 | W b0 / W b100 |
| **e1** | W b33 / W b0 | L b100 / W b100 | W b0 / L b100 | W b0 / W b100 |
| **2tnk** | W b0 / W b100 | W b0 / W b100 | L b100 / L b100 | W b0 / W b100 |
| **3tnk** | W b0 / W b100 | W b0 / W b100 | L b100 / L b100 | W b0 / W b100 |


**Soviet**, 2:1 screen, best gap. Cell = frontal / flank: W/L and breach share (b0 = never reached the artillery, b1 = always).


| Enemy | rifle | rocket | tank | mixed | e1+e2 |
|---|---|---|---|---|---|
| **1tnk** | W b0 / W b100 | W b0 / W b100 | W b0 / W b100 | W b0 / W b100 | W b0 / W b100 |
| **jeep** | W b0 / W b100 | L b100 / W b100 | W b0 / W b100 | W b0 / W b100 | W b100 / W b100 |
| **apc** | L b100 / L b100 | L b100 / W b100 | W b0 / W b100 | W b0 / W b100 | L b100 / W b100 |
| **ftrk** | W b0 / W b100 | W b0 / W b100 | W b0 / W b100 | W b0 / W b100 | W b0 / W b100 |
| **e1** | L b100 / W b0 | L b100 / W b100 | W b0 / W b100 | W b0 / W b100 | L b100 / W b0 |
| **2tnk** | W b0 / W b100 | W b0 / W b100 | W b100 / W b100 | W b0 / W b100 | W b0 / W b100 |
| **3tnk** | W b0 / W b100 | W b0 / W b100 | W b33 / W b100 | W b0 / W b100 | W b0 / W b100 |


**Against the reference armies.** These are the `R\ai.yaml`-weighted armies from section 5, scaled to the budget.
- Each recipe uses its best gap.
- Each mode is run in 3 jitters.
- "No artillery" spends the same money on the screen composition alone, holding the same line.


**Allies, $2,000 a side.** Cell = mode a·b·c result (W win in >=2 of 3 jitters, ~ split, L loss) and net value (our survivors minus enemy survivors, $) in mode c. No-artillery rows show b·c.


| Recipe | Gap | AI-Allied | AI-Soviet | 60 e1 | 7 2tnk | 5 3tnk |
|---|---|---|---|---|---|---|
| rifle 1:1 | 2 | WWW +1.6k | ~WW +1.0k | WWW +0.8k | WWW +1.4k | WWW +1.3k |
| rifle 2:1 | 2 | WWW +1.6k | WWW +0.8k | WWW +1.0k | WWW +1.2k | WWW +1.3k |
| rifle 3:1 | 2 | WWW +1.6k | WWW +0.6k | WWW +0.7k | WWW +1.1k | WWW +1.2k |
| rocket 1:1 | 4 | WWW +1.0k | LLL -0.5k | LLL -0.5k | WWW +1.6k | WWW +1.6k |
| rocket 2:1 | 2 | WWW +1.3k | LLL -0.8k | LLL -0.3k | WWW +1.6k | WWW +1.5k |
| rocket 3:1 | 2 | WWW +1.2k | LLL -0.9k | LLL -0.5k | WWW +1.6k | WWW +1.7k |
| tank 1:1 | 2 | WW~ -0.2k | LLL -0.3k | WWW +1.6k | LLL -0.7k | LLL -1.1k |
| tank 2:1 | 6 | WWL -0.7k | WWL -0.7k | WWW +1.4k | LLL -0.5k | LLL -1.2k |
| tank 3:1 | 4 | WWW +0.3k | LWL -0.2k | WWW +1.2k | LLL -0.6k | LLL -0.9k |
| mixed 1:1 | 2 | W~W +1.3k | LLL -0.5k | WWW +1.6k | WWW +1.2k | LLW +0.7k |
| mixed 2:1 | 2 | WWW +1.4k | ~L~ +0.0k | WWW +1.5k | WWW +0.8k | WWW +0.7k |
| mixed 3:1 | 4 | W~W +1.3k | WWL -0.3k | WWW +1.2k | WWW +0.6k | WWW +0.6k |
| rifle, no artillery | 4 | -WW +1.5k | -LL -0.3k | -WW +0.4k | -WW +0.3k | -WW +1.1k |
| rocket, no artillery | 4 | -WW +1.4k | -LL -0.6k | -LL -1.6k | -WW +1.7k | -WW +1.8k |
| tank, no artillery | 4 | -LL -0.9k | -LL -1.4k | -LL -0.5k | -WW +0.3k | -WL -0.5k |
| mixed, no artillery | 4 | -LW +0.8k | -LL -0.5k | -WW +0.1k | -WW +0.8k | -WW +0.8k |


**Allies, $4,000 a side.** Cell = mode a·b·c result (W win in >=2 of 3 jitters, ~ split, L loss) and net value (our survivors minus enemy survivors, $) in mode c. No-artillery rows show b·c.


| Recipe | Gap | AI-Allied | AI-Soviet | 60 e1 | 7 2tnk | 5 3tnk |
|---|---|---|---|---|---|---|
| rifle 1:1 | 2 | WWW +3.1k | WWW +2.1k | WWW +1.2k | WWW +2.8k | WWW +3.0k |
| rifle 2:1 | 2 | WWW +3.1k | WWW +1.7k | WWW +1.2k | WWW +2.6k | WWW +2.9k |
| rifle 3:1 | 2 | WWW +3.1k | WWW +1.5k | W~~ +0.2k | WWW +2.3k | WWW +2.8k |
| rocket 1:1 | 6 | WWW +1.8k | LLL -1.3k | LLL -1.4k | WWW +3.3k | WWW +3.1k |
| rocket 2:1 | 2 | WWW +2.8k | LLL -1.4k | LLL -1.1k | WWW +3.2k | WWW +3.0k |
| rocket 3:1 | 2 | WWW +2.9k | LLL -1.6k | LLL -1.6k | WWW +3.2k | WWW +3.1k |
| tank 1:1 | 2 | WWW +1.3k | LLL -0.8k | WWW +3.3k | LLL -1.4k | LLL -1.8k |
| tank 2:1 | 4 | WWW +1.7k | ~~L -0.5k | WWW +2.8k | LLL -1.2k | LLL -1.6k |
| tank 3:1 | 2 | WWW +1.4k | LLL -1.3k | WWW +2.7k | LLL -1.2k | LLL -1.6k |
| mixed 1:1 | 4 | WWW +2.7k | LLL -0.9k | WWW +2.3k | WWW +2.2k | WWW +2.4k |
| mixed 2:1 | 4 | WWW +2.6k | W~L -0.8k | WWW +2.8k | WWW +2.2k | WWW +2.3k |
| mixed 3:1 | 4 | WWW +2.5k | ~WL -0.6k | WWW +2.6k | WWW +2.0k | WWW +2.0k |
| rifle, no artillery | 4 | -WW +3.0k | -LL -0.6k | -LL -1.1k | -WW +1.1k | -~W +1.8k |
| rocket, no artillery | 4 | -WW +2.4k | -LL -1.5k | -LL -3.1k | -WW +2.9k | -WW +2.6k |
| tank, no artillery | 4 | -LL -2.1k | -LL -2.5k | -LL -2.1k | -~~ +0.0k | -LL -1.8k |
| mixed, no artillery | 4 | -W~ +0.5k | -LL -0.9k | -WW +1.2k | -WW +1.6k | -WW +2.1k |


**Allies, $6,000 a side.** Cell = mode a·b·c result (W win in >=2 of 3 jitters, ~ split, L loss) and net value (our survivors minus enemy survivors, $) in mode c. No-artillery rows show b·c.


| Recipe | Gap | AI-Allied | AI-Soviet | 60 e1 | 7 2tnk | 5 3tnk |
|---|---|---|---|---|---|---|
| rifle 1:1 | 4 | WWW +4.6k | WWW +3.2k | ~LL -0.6k | WWW +4.4k | WWW +4.5k |
| rifle 2:1 | 6 | WWW +4.3k | WWW +3.2k | ~LL -0.6k | WWW +4.1k | WWW +4.4k |
| rifle 3:1 | 6 | WWW +4.2k | WWW +3.1k | LLL -0.1k | WWW +3.7k | WWW +4.2k |
| rocket 1:1 | 4 | W~W +3.9k | LLL -1.6k | LLL -2.5k | WWW +4.9k | WWW +4.5k |
| rocket 2:1 | 2 | WWW +4.3k | LLL -1.8k | LLL -2.4k | WWW +4.1k | WWW +4.0k |
| rocket 3:1 | 2 | WWW +4.0k | LLL -1.9k | LLL -2.4k | WWW +4.2k | WWW +4.0k |
| tank 1:1 | 2 | WWW +0.8k | LLL -1.7k | WWW +4.8k | LLL -1.9k | LLL -2.7k |
| tank 2:1 | 2 | WWW +2.4k | ~LL -0.7k | WWW +4.5k | LLL -1.7k | LLL -2.1k |
| tank 3:1 | 2 | WWW +2.2k | LLL -1.1k | WWW +4.2k | LLL -1.5k | LLL -2.3k |
| mixed 1:1 | 4 | LLW +4.2k | LLL -1.3k | WWW +4.4k | WWW +3.3k | WWW +3.1k |
| mixed 2:1 | 2 | WWW +3.9k | LLW +1.7k | WWW +4.5k | WWW +3.1k | WWW +3.3k |
| mixed 3:1 | 6 | WWW +3.3k | WWL -1.3k | WWW +2.9k | WWW +3.0k | WWW +3.5k |
| rifle, no artillery | 4 | -LW +2.4k | -LL -1.9k | -LL -2.6k | -LL -1.2k | -LL -1.0k |
| rocket, no artillery | 4 | -WW +1.2k | -LL -2.7k | -LL -4.8k | -WW +4.5k | -WW +3.5k |
| tank, no artillery | 4 | -LL -3.2k | -LL -4.0k | -LL -3.8k | -LL -0.8k | -LL -3.0k |
| mixed, no artillery | 4 | -WL -0.2k | -LL -2.0k | -WW +1.6k | -WW +1.7k | -WW +2.5k |


**Soviet, $2,000 a side.** Cell = mode a·b·c result (W win in >=2 of 3 jitters, ~ split, L loss) and net value (our survivors minus enemy survivors, $) in mode c. No-artillery rows show b·c.


| Recipe | Gap | AI-Allied | AI-Soviet | 60 e1 | 7 2tnk | 5 3tnk |
|---|---|---|---|---|---|---|
| rifle 1:1 | 4 | WWW +1.5k | WWW +1.3k | LLL -0.6k | WWW +1.4k | WWW +1.5k |
| rifle 2:1 | 4 | WWW +1.5k | WWW +1.4k | LLL -0.3k | WWW +1.3k | WWW +1.5k |
| rifle 3:1 | 4 | WWW +1.5k | WWW +1.3k | ~WW +0.1k | WWW +1.2k | WWW +1.4k |
| rocket 1:1 | 2 | WWW +0.8k | LLL -0.4k | LLL -0.8k | WWW +1.6k | WWW +1.7k |
| rocket 2:1 | 2 | WWW +0.6k | LLL -0.5k | LLL -0.8k | WWW +1.6k | WWW +1.8k |
| rocket 3:1 | 2 | WWW +0.4k | LLL -0.4k | LLL -0.9k | WWW +1.6k | WWW +1.8k |
| tank 1:1 | 2 | WW~ +0.1k | LLL -0.3k | WWW +1.1k | LLL -0.5k | LLL -0.8k |
| tank 2:1 | 4 | WWL -0.5k | LLL -0.6k | WWW +1.1k | LLL -0.2k | WWL -0.9k |
| tank 3:1 | 4 | WWL -0.6k | W~L -0.6k | WWW +1.0k | WWW +0.0k | LLL -0.9k |
| mixed 1:1 | 2 | LLW +0.7k | L~L -0.2k | WWW +0.9k | WWW +1.3k | WWW +1.1k |
| mixed 2:1 | 4 | WWW +1.2k | ~WL -0.5k | WWW +1.4k | WWW +0.8k | WWW +0.7k |
| mixed 3:1 | 4 | WWW +1.3k | WWW +0.1k | WWW +1.3k | WWW +0.6k | WWW +0.6k |
| e1+e2 1:1 | 2 | WWW +1.2k | ~~W +0.5k | LLL -0.7k | WWW +1.5k | WWW +1.5k |
| e1+e2 2:1 | 2 | WWW +1.4k | WWW +0.7k | LLL -0.5k | WWW +1.5k | WWW +1.5k |
| e1+e2 3:1 | 2 | WWW +1.5k | WWW +0.5k | LLL -0.6k | WWW +1.5k | WWW +1.5k |
| rifle, no artillery | 4 | -WW +1.5k | -WW +1.2k | -WW +0.6k | -WW +0.6k | -WW +1.2k |
| rocket, no artillery | 4 | -LW +1.4k | -L~ -0.2k | -LL -1.6k | -WW +1.7k | -WW +1.7k |
| tank, no artillery | 4 | -LL -1.3k | -LL -1.3k | -LL -0.8k | -WW +0.3k | -WL -0.8k |
| mixed, no artillery | 4 | -~W +1.4k | -WW +0.3k | -WW +1.4k | -WW +0.8k | -WW +0.8k |
| e1+e2, no artillery | 4 | -WW +1.5k | -~W +1.1k | -LL -0.6k | -WW +1.5k | -WW +1.4k |


**Soviet, $4,000 a side.** Cell = mode a·b·c result (W win in >=2 of 3 jitters, ~ split, L loss) and net value (our survivors minus enemy survivors, $) in mode c. No-artillery rows show b·c.


| Recipe | Gap | AI-Allied | AI-Soviet | 60 e1 | 7 2tnk | 5 3tnk |
|---|---|---|---|---|---|---|
| rifle 1:1 | 4 | WWW +3.1k | WWW +2.6k | LLL -1.5k | WWW +3.0k | WWW +3.0k |
| rifle 2:1 | 2 | WWW +3.2k | WWW +1.4k | LLL -0.5k | WWW +2.8k | WWW +2.9k |
| rifle 3:1 | 4 | WWW +3.1k | WWW +2.6k | LLL -0.4k | WWW +2.5k | WWW +2.8k |
| rocket 1:1 | 2 | W~W +0.6k | LLL -1.7k | LLL -1.7k | WWW +3.5k | WWW +3.3k |
| rocket 2:1 | 2 | WWW +1.5k | LLL -1.1k | LLL -1.9k | WWW +3.4k | WWW +3.2k |
| rocket 3:1 | 2 | W~W +2.3k | LLL -1.0k | LLL -1.9k | WWW +3.3k | WWW +3.1k |
| tank 1:1 | 6 | LWL -0.7k | LLL -0.9k | LWW +2.2k | LLL -0.5k | L~L -1.9k |
| tank 2:1 | 6 | LLL -1.3k | LLL -1.4k | LWW +2.4k | LWW -0.1k | LWL -1.9k |
| tank 3:1 | 2 | LL~ -0.1k | LLL -1.4k | WWW +1.2k | LLL -0.4k | WWL -1.6k |
| mixed 1:1 | 2 | L~W +0.9k | LLL -1.6k | WWW +1.9k | WWW +2.4k | WWW +1.7k |
| mixed 2:1 | 2 | WWW +1.9k | L~L -1.2k | WWW +1.2k | WWW +2.2k | WWW +2.0k |
| mixed 3:1 | 4 | ~WW +2.0k | LLL -1.3k | WWW +1.9k | WWW +1.8k | WWW +1.8k |
| e1+e2 1:1 | 4 | WWW +1.7k | L~L -0.5k | LLL -1.3k | WWW +3.2k | WWW +3.2k |
| e1+e2 2:1 | 4 | WWW +2.8k | WWW +0.8k | LLL -1.1k | WWW +3.2k | WWW +3.2k |
| e1+e2 3:1 | 4 | WWW +3.0k | WWW +1.6k | LLL -1.1k | WWW +3.1k | WWW +3.2k |
| rifle, no artillery | 4 | -LW +3.1k | -~W +2.5k | -WW +1.1k | -WW +1.3k | -WW +2.4k |
| rocket, no artillery | 4 | -WW +2.7k | -~L -2.2k | -LL -3.0k | -WW +2.9k | -WW +2.6k |
| tank, no artillery | 4 | -LL -2.0k | -LL -2.8k | -LL -2.1k | -WW +0.2k | -WL -2.3k |
| mixed, no artillery | 4 | -WW +2.5k | -~W +1.4k | -WW +2.2k | -WW +1.7k | -WW +1.8k |
| e1+e2, no artillery | 4 | -WW +3.0k | -LW +1.8k | -LL -1.7k | -WW +2.8k | -WW +2.7k |


**Soviet, $6,000 a side.** Cell = mode a·b·c result (W win in >=2 of 3 jitters, ~ split, L loss) and net value (our survivors minus enemy survivors, $) in mode c. No-artillery rows show b·c.


| Recipe | Gap | AI-Allied | AI-Soviet | 60 e1 | 7 2tnk | 5 3tnk |
|---|---|---|---|---|---|---|
| rifle 1:1 | 2 | WWW +4.1k | WWW +2.1k | LLL -2.6k | WWW +4.2k | WWW +4.9k |
| rifle 2:1 | 2 | ~WW +4.1k | WWW +2.4k | LLL -1.7k | WWW +3.9k | WWW +4.5k |
| rifle 3:1 | 4 | WWW +4.2k | WWW +3.7k | LLL -0.6k | WWW +3.7k | WWW +4.4k |
| rocket 1:1 | 4 | LLW +1.6k | LLL -2.1k | LLL -3.2k | WWW +4.8k | WWW +4.7k |
| rocket 2:1 | 2 | ~~W +2.0k | LLL -2.0k | LLL -3.4k | WWW +4.6k | WWW +4.3k |
| rocket 3:1 | 2 | ~~W +1.9k | LLL -1.7k | LLL -3.4k | WWW +4.4k | WWW +4.1k |
| tank 1:1 | 2 | ~~L -0.8k | LLL -1.9k | LLL -2.1k | LLL -1.5k | WWL -2.5k |
| tank 2:1 | 4 | LLL -1.8k | LLL -1.1k | W~~ +0.1k | LLL -1.1k | WWL -2.4k |
| tank 3:1 | 4 | LLL -2.0k | LLL -1.3k | WWW +1.1k | LLL -1.0k | W~L -2.5k |
| mixed 1:1 | 6 | LLW +1.7k | LLL -1.8k | ~WW +0.0k | LWW +3.4k | WWW +3.1k |
| mixed 2:1 | 2 | WWW +2.5k | LL~ -1.0k | WWW +3.2k | WWW +3.2k | WWW +3.3k |
| mixed 3:1 | 4 | WWW +3.5k | ~W~ +0.3k | WWW +3.0k | WWW +2.8k | WWW +3.3k |
| e1+e2 1:1 | 4 | ~WW +1.9k | ~~~ -0.6k | LLL -2.8k | WWW +4.7k | WWW +4.7k |
| e1+e2 2:1 | 4 | WWW +2.3k | LW~ +0.1k | LLL -3.0k | WWW +4.4k | WWW +4.4k |
| e1+e2 3:1 | 4 | WWW +4.3k | ~WL -0.6k | LLL -3.0k | WWW +4.1k | WWW +4.3k |
| rifle, no artillery | 4 | -LW +3.9k | -LW +1.2k | -LL -1.0k | -WW +1.5k | -LW +1.7k |
| rocket, no artillery | 4 | -WW +3.8k | -~L -3.1k | -LL -4.4k | -WW +4.4k | -WW +4.0k |
| tank, no artillery | 4 | -LL -3.2k | -LL -4.4k | -LL -3.8k | -LL -0.7k | -~L -3.0k |
| mixed, no artillery | 4 | -WW +3.0k | -WW +1.2k | -WW +0.8k | -WW +2.1k | -WW +3.2k |
| e1+e2, no artillery | 4 | -LW +3.9k | -LW +1.2k | -LL -3.6k | -WW +0.5k | -LW +0.9k |


**Best screen recipe per faction and budget** (averaged over the 5 reference armies):


| Faction | Budget | Mode | Best recipe with artillery | Wins (of 5 armies) | Mean net value | Mean $ lost ours / theirs | Mean time (s) | Best overall incl. no-artillery | Its wins |
|---|---|---|---|---|---|---|---|---|---|
| Allies | $2,000 | b (reposition, nearest) | rifle 2:1 gap 2 | 5.0/5 | +1.1k | 744 / 1,993 | 26 | (same) | 5.0/5 |
| Allies | $2,000 | c (reposition + smart) | rifle 1:1 gap 2 | 5.0/5 | +1.2k | 637 / 1,993 | 26 | (same) | 5.0/5 |
| Allies | $4,000 | b (reposition, nearest) | rifle 1:1 gap 2 | 4.7/5 | +2.1k | 1,643 / 3,980 | 26 | (same) | 4.7/5 |
| Allies | $4,000 | c (reposition + smart) | rifle 1:1 gap 2 | 4.7/5 | +2.5k | 1,360 / 3,980 | 27 | (same) | 4.7/5 |
| Allies | $6,000 | b (reposition, nearest) | mixed 3:1 gap 6 | 5.0/5 | +2.2k | 3,103 / 5,980 | 21 | (same) | 5.0/5 |
| Allies | $6,000 | c (reposition + smart) | mixed 2:1 gap 2 | 4.7/5 | +3.3k | 2,140 / 5,843 | 23 | (same) | 4.7/5 |
| Soviet | $2,000 | b (reposition, nearest) | mixed 2:1 gap 4 | 5.0/5 | +0.7k | 993 / 1,993 | 24 | (same) | 5.0/5 |
| Soviet | $2,000 | c (reposition + smart) | rifle 3:1 gap 4 | 4.7/5 | +1.1k | 680 / 1,967 | 27 | rifle, no artillery | 5.0/5 |
| Soviet | $4,000 | b (reposition, nearest) | e1+e2 2:1 gap 4 | 4.0/5 | +1.7k | 1,901 / 3,607 | 20 | (same) | 4.0/5 |
| Soviet | $4,000 | c (reposition + smart) | rifle 3:1 gap 4 | 4.0/5 | +2.1k | 1,493 / 3,780 | 26 | rifle, no artillery | 5.0/5 |
| Soviet | $6,000 | b (reposition, nearest) | mixed 3:1 gap 4 | 4.3/5 | +1.9k | 3,445 / 5,747 | 23 | (same) | 4.3/5 |
| Soviet | $6,000 | c (reposition + smart) | mixed 3:1 gap 4 | 4.3/5 | +2.6k | 2,602 / 5,753 | 22 | mixed, no artillery | 4.3/5 |


**Retreat: falling back together vs screen holds and artillery repositions** [M]. In the fallback, once any enemy is within its range + 1 of our units, everyone alternates 2 s stop-and-fire with 2 s moving back at the slowest unit's speed (at most 10 cells). Vehicles pay their body turn time at each reversal. Turreted units also fire while moving.


**Allies**, $4,000, 2:1, gap 4. Cell = screen holds + artillery repositions / everyone falls back together; result and net value.


| Screen | AI-Allied | AI-Soviet | 60 e1 | 7 2tnk | 5 3tnk | 1tnk | jeep | apc | ftrk |
|---|---|---|---|---|---|---|---|---|---|
| **rifle** | W+2.2k / L-2.6k | W+1.5k / L-2.9k | L-0.6k / L-0.7k | W+2.6k / L-2.7k | W+2.4k / L-2.7k | W+2.8k / L-2.3k | W+2.2k / ~-0.1k | L-1.7k / L-3.0k | W+2.9k / L-2.3k |
| **rocket** | L-0.3k / L-1.6k | L-0.9k / L-2.9k | L-1.3k / L-2.0k | W+3.2k / W+0.1k | W+2.8k / L-0.9k | W+3.3k / W+0.9k | L-1.8k / L-2.2k | L-1.0k / L-1.5k | W+1.8k / L-2.3k |
| **tank** | W+1.3k / L-0.8k | ~-0.1k / L-1.8k | W+2.8k / L-0.4k | L-1.2k / L-2.0k | L-1.0k / L-1.5k | W+2.4k / W+1.3k | W+3.7k / W+3.1k | W+3.7k / W+3.2k | W+3.5k / L-1.2k |
| **mixed** | W+0.8k / L-2.0k | ~-0.4k / L-2.7k | W+2.8k / L-1.4k | W+2.2k / L-2.1k | W+1.3k / L-2.2k | W+2.7k / ~-0.4k | W+3.8k / W+3.5k | W+3.8k / W+3.5k | W+3.0k / L-1.3k |


**Soviet**, $4,000, 2:1, gap 4. Cell = screen holds + artillery repositions / everyone falls back together; result and net value.


| Screen | AI-Allied | AI-Soviet | 60 e1 | 7 2tnk | 5 3tnk | 1tnk | jeep | apc | ftrk |
|---|---|---|---|---|---|---|---|---|---|
| **rifle** | W+1.7k / W+0.8k | W+2.5k / ~-0.4k | L-1.0k / L-0.5k | W+2.7k / W+1.7k | W+2.3k / W+0.3k | W+2.7k / W+2.6k | W+1.9k / W+2.1k | L-1.0k / L-0.5k | W+3.0k / W+1.0k |
| **rocket** | L-0.4k / W+0.9k | L-0.5k / ~-0.6k | L-1.9k / L-1.3k | W+3.3k / W+3.1k | W+2.9k / W+2.2k | W+3.2k / W+3.1k | L-2.0k / L-1.3k | L-0.6k / W+0.5k | W+2.1k / L-0.6k |
| **tank** | L-1.4k / L-1.0k | L-1.3k / L-1.5k | W+0.8k / W+1.2k | L-0.6k / L-0.9k | W+0.9k / W+0.4k | W+2.5k / W+2.6k | W+3.5k / W+3.6k | W+3.8k / W+3.8k | W+3.4k / W+1.6k |
| **mixed** | ~-0.4k / L-0.4k | L-0.7k / L-1.2k | W+1.8k / L-0.2k | W+2.1k / W+1.4k | W+1.1k / W+0.0k | W+2.6k / W+0.9k | W+3.6k / W+3.6k | W+3.8k / W+3.7k | W+3.1k / W+1.6k |
| **e1+e2** | W+2.1k / ~-0.1k | W+1.4k / L-1.3k | L-1.1k / L-1.4k | W+3.2k / W+1.5k | W+2.9k / L-0.7k | W+3.1k / W+2.2k | L-0.6k / W+0.3k | L-0.7k / L-1.0k | W+2.6k / ~-0.1k |


### 8.5 Attack on a defended base [C]


**Setup** [M]:
- *Defender base:* two defenses 3 cells apart (pbox + gun Allied, ftur + tsla Soviet), a construction yard 3.5 cells behind them, all repaired at 729 HP/s.
- *Defending army:* the AI-weighted army of the defending faction ($2,000 / $4,000 / $6,000). It waits at the base and comes out when a structure is damaged or an attacker comes within 12 cells.
- *Siege:* the artillery sets up 11.5 cells (arty) or about 9.5 cells (v2rl) from the defenses, with the screen held outside the defenses' range. The artillery shoots the army first, then the defenses, then the yard, creeping forward only while staying outside any live defense's range. The screen joins in once the army and defenses are dead, or if the artillery is lost.
- *Frontal:* the same screen compositions with no artillery attack-move from 14 cells.
- *Search:* the cheapest attacking budget in $1,000 steps that destroys every defender in at least 2 of 3 jitters.
- *Cost columns:* losses are averaged over the 3 jitters, so a jitter the attacker lost can make "enemy $ destroyed" less than the full base value. "Time" is until the last structure falls, in winning runs.

| Attack | Defending army | Cheapest siege (screen:arty) | Siege $ lost | Enemy $ destroyed | Siege time to raze (s) | Cheapest frontal (no artillery) | Frontal $ lost | Enemy $ destroyed | Frontal time (s) |
|---|---|---|---|---|---|---|---|---|---|
| Allies attack Soviet base | $2,000 | rifle 3:1: $3,000 | 1,950 | 5,800 | 222 | tank: $6,000 | 2,600 | 5,800 | 68 |
| Allies attack Soviet base | $4,000 | rifle 1:1: $5,000 | 3,333 | 5,950 | 72 | mixed: $9,000 | 6,733 | 7,800 | 47 |
| Allies attack Soviet base | $6,000 | rifle 1:1: $7,000 | 4,250 | 9,800 | 113 | mixed: $12,000 | 5,867 | 9,800 | 29 |
| Soviet attack Allied base | $2,000 | rifle 2:1: $3,000 | 1,733 | 5,400 | 118 | mixed: $5,000 | 2,250 | 5,400 | 38 |
| Soviet attack Allied base | $4,000 | rifle 3:1: $5,000 | 2,917 | 7,400 | 96 | mixed: $8,000 | 5,183 | 7,400 | 40 |
| Soviet attack Allied base | $6,000 | rifle 2:1: $7,000 | 5,244 | 8,134 | 59 | mixed: $10,000 | 6,250 | 9,400 | 38 |


All recipes (cheapest winning budget in $1,000 steps, >=2 of 3 jitters):


| Attacker | Def. army | Approach | Recipe | Cheapest win | Our $ lost | Enemy army $ lost | Enemy structures $ lost | Time to raze (s) |
|---|---|---|---|---|---|---|---|---|
| Allies | $2,000 | siege | rifle 1:1 | $4,000 | 1600 | 2000 | 3800 | 68.6 |
| Allies | $2,000 | siege | rifle 2:1 | $3,000 | 2350 | 1644 | 2533 | 179.4 |
| Allies | $2,000 | siege | rifle 3:1 | $3,000 | 1950 | 2000 | 3800 | 221.7 |
| Allies | $2,000 | frontal | rifle | $9,000 | 5867 | 1611 | 2733 | 40.6 |
| Allies | $2,000 | siege | rocket 1:1 | $5,000 | 2783 | 2000 | 3800 | 75.4 |
| Allies | $2,000 | siege | rocket 2:1 | $5,000 | 2733 | 2000 | 3800 | 72.4 |
| Allies | $2,000 | siege | rocket 3:1 | $4,000 | 2967 | 2000 | 3800 | 150.5 |
| Allies | $2,000 | frontal | rocket | $7,000 | 4633 | 2000 | 3800 | 39.5 |
| Allies | $2,000 | siege | tank 1:1 | $4,000 | 1717 | 2000 | 3800 | 74.4 |
| Allies | $2,000 | siege | tank 2:1 | $3,000 | 2100 | 2000 | 3800 | 186.8 |
| Allies | $2,000 | siege | tank 3:1 | $4,000 | 2150 | 2000 | 3800 | 130.5 |
| Allies | $2,000 | frontal | tank | $6,000 | 2600 | 2000 | 3800 | 67.6 |
| Allies | $2,000 | siege | mixed 1:1 | $4,000 | 2050 | 2000 | 3800 | 88.7 |
| Allies | $2,000 | siege | mixed 2:1 | $3,000 | 2317 | 2000 | 2533 | 169.2 |
| Allies | $2,000 | siege | mixed 3:1 | $3,000 | 2162 | 2000 | 3800 | 231.8 |
| Allies | $2,000 | frontal | mixed | $6,000 | 3800 | 2000 | 3800 | 45.2 |
| Allies | $4,000 | siege | rifle 1:1 | $5,000 | 3333 | 3417 | 2533 | 72.0 |
| Allies | $4,000 | siege | rifle 2:1 | $5,000 | 3756 | 3417 | 2533 | 97.6 |
| Allies | $4,000 | siege | rifle 3:1 | $5,000 | 3550 | 4000 | 3800 | 151.6 |
| Allies | $4,000 | frontal | rifle | $12,000 | 9467 | 3094 | 2933 | 51.3 |
| Allies | $4,000 | siege | rocket 1:1 | $7,000 | 5483 | 4000 | 2533 | 84.2 |
| Allies | $4,000 | siege | rocket 2:1 | $6,000 | 5333 | 3317 | 2533 | 178.0 |
| Allies | $4,000 | siege | rocket 3:1 | $6,000 | 4933 | 3350 | 2533 | 92.6 |
| Allies | $4,000 | frontal | rocket | $11,000 | 6867 | 4000 | 3800 | 30.0 |
| Allies | $4,000 | siege | tank 1:1 | $7,000 | 3783 | 4000 | 3800 | 61.8 |
| Allies | $4,000 | siege | tank 2:1 | $7,000 | 4100 | 4000 | 3800 | 68.3 |
| Allies | $4,000 | siege | tank 3:1 | $6,000 | 5000 | 3417 | 2533 | 104.6 |
| Allies | $4,000 | frontal | tank | $9,000 | 7300 | 3900 | 3133 | 106.3 |
| Allies | $4,000 | siege | mixed 1:1 | $7,000 | 3617 | 4000 | 3800 | 55.7 |
| Allies | $4,000 | siege | mixed 2:1 | $6,000 | 4667 | 3800 | 2533 | 82.4 |
| Allies | $4,000 | siege | mixed 3:1 | $6,000 | 4092 | 4000 | 3800 | 85.1 |
| Allies | $4,000 | frontal | mixed | $9,000 | 6733 | 4000 | 3800 | 47.1 |
| Allies | $6,000 | siege | rifle 1:1 | $7,000 | 4250 | 6000 | 3800 | 113.2 |
| Allies | $6,000 | siege | rifle 2:1 | $8,000 | 4267 | 6000 | 3800 | 67.9 |
| Allies | $6,000 | siege | rifle 3:1 | $7,000 | 5033 | 6000 | 3800 | 102.8 |
| Allies | $6,000 | frontal | rifle | $15,000 | 12033 | 4667 | 3133 | 49.8 |
| Allies | $6,000 | siege | rocket 1:1 | $9,000 | 7300 | 6000 | 3800 | 141.1 |
| Allies | $6,000 | siege | rocket 2:1 | $9,000 | 7400 | 5600 | 2533 | 72.1 |
| Allies | $6,000 | siege | rocket 3:1 | $10,000 | 6717 | 6000 | 3800 | 59.0 |
| Allies | $6,000 | frontal | rocket | $14,000 | 12200 | 6000 | 3800 | 50.8 |
| Allies | $6,000 | siege | tank 1:1 | $10,000 | 6883 | 6000 | 3800 | 107.4 |
| Allies | $6,000 | siege | tank 2:1 | $9,000 | 7000 | 5317 | 2533 | 61.6 |
| Allies | $6,000 | siege | tank 3:1 | $9,000 | 6933 | 5317 | 2533 | 72.2 |
| Allies | $6,000 | frontal | tank | $12,000 | 10867 | 5633 | 3133 | 187.7 |
| Allies | $6,000 | siege | mixed 1:1 | $9,000 | 6083 | 4950 | 2533 | 49.1 |
| Allies | $6,000 | siege | mixed 2:1 | $8,000 | 6122 | 6000 | 2533 | 56.9 |
| Allies | $6,000 | siege | mixed 3:1 | $8,000 | 6133 | 5700 | 2533 | 65.8 |
| Allies | $6,000 | frontal | mixed | $12,000 | 5867 | 6000 | 3800 | 29.1 |
| Soviet | $2,000 | siege | rifle 1:1 | $3,000 | 1900 | 2000 | 3400 | 127.6 |
| Soviet | $2,000 | siege | rifle 2:1 | $3,000 | 1733 | 2000 | 3400 | 118.4 |
| Soviet | $2,000 | siege | rifle 3:1 | $3,000 | 2167 | 1844 | 2267 | 130.9 |
| Soviet | $2,000 | frontal | rifle | $8,000 | 4067 | 2000 | 3400 | 45.4 |
| Soviet | $2,000 | siege | rocket 1:1 | $3,000 | 2400 | 1489 | 2267 | 137.8 |
| Soviet | $2,000 | siege | rocket 2:1 | $3,000 | 2033 | 2000 | 3400 | 116.0 |
| Soviet | $2,000 | siege | rocket 3:1 | $3,000 | 2200 | 2000 | 3400 | 134.2 |
| Soviet | $2,000 | frontal | rocket | $6,000 | 2767 | 2000 | 3400 | 30.1 |
| Soviet | $2,000 | siege | tank 1:1 | $4,000 | 2000 | 2000 | 3400 | 62.1 |
| Soviet | $2,000 | siege | tank 2:1 | $3,000 | 2100 | 2000 | 3400 | 135.0 |
| Soviet | $2,000 | siege | tank 3:1 | $3,000 | 2250 | 2000 | 3400 | 158.5 |
| Soviet | $2,000 | frontal | tank | $6,000 | 2550 | 2000 | 3400 | 63.9 |
| Soviet | $2,000 | siege | mixed 1:1 | $4,000 | 2067 | 2000 | 3400 | 74.4 |
| Soviet | $2,000 | siege | mixed 2:1 | $3,000 | 2400 | 1778 | 2267 | 133.4 |
| Soviet | $2,000 | siege | mixed 3:1 | $4,000 | 2017 | 2000 | 3400 | 76.6 |
| Soviet | $2,000 | frontal | mixed | $5,000 | 2250 | 2000 | 3400 | 38.0 |
| Soviet | $2,000 | siege | e1+e2 1:1 | $4,000 | 1483 | 2000 | 3400 | 53.1 |
| Soviet | $2,000 | siege | e1+e2 2:1 | $3,000 | 2067 | 2000 | 3400 | 128.3 |
| Soviet | $2,000 | siege | e1+e2 3:1 | $3,000 | 2450 | 1733 | 2267 | 140.5 |
| Soviet | $2,000 | frontal | e1+e2 | $7,000 | 3900 | 2000 | 3400 | 39.4 |
| Soviet | $4,000 | siege | rifle 1:1 | $5,000 | 3867 | 3217 | 2467 | 89.7 |
| Soviet | $4,000 | siege | rifle 2:1 | $5,000 | 3978 | 3450 | 2267 | 109.9 |
| Soviet | $4,000 | siege | rifle 3:1 | $5,000 | 2917 | 4000 | 3400 | 96.0 |
| Soviet | $4,000 | frontal | rifle | $10,000 | 9433 | 3461 | 2733 | 122.4 |
| Soviet | $4,000 | siege | rocket 1:1 | $6,000 | 4900 | 3867 | 2467 | 79.0 |
| Soviet | $4,000 | siege | rocket 2:1 | $6,000 | 2800 | 4000 | 3400 | 36.6 |
| Soviet | $4,000 | siege | rocket 3:1 | $6,000 | 3800 | 4000 | 3400 | 63.7 |
| Soviet | $4,000 | frontal | rocket | $8,000 | 7300 | 3667 | 2733 | 60.7 |
| Soviet | $4,000 | siege | tank 1:1 | $8,000 | 5333 | 3622 | 2467 | 56.5 |
| Soviet | $4,000 | siege | tank 2:1 | $7,000 | 4967 | 4000 | 3400 | 71.3 |
| Soviet | $4,000 | siege | tank 3:1 | $6,000 | 5200 | 3689 | 2267 | 116.3 |
| Soviet | $4,000 | frontal | tank | $8,000 | 6867 | 3767 | 2733 | 146.3 |
| Soviet | $4,000 | siege | mixed 1:1 | $7,000 | 3308 | 4000 | 3400 | 49.9 |
| Soviet | $4,000 | siege | mixed 2:1 | $6,000 | 2217 | 4000 | 3400 | 41.0 |
| Soviet | $4,000 | siege | mixed 3:1 | $6,000 | 4358 | 4000 | 3400 | 91.7 |
| Soviet | $4,000 | frontal | mixed | $8,000 | 5183 | 4000 | 3400 | 40.1 |
| Soviet | $4,000 | siege | e1+e2 1:1 | $6,000 | 2183 | 4000 | 3400 | 42.1 |
| Soviet | $4,000 | siege | e1+e2 2:1 | $6,000 | 4167 | 3900 | 2467 | 50.8 |
| Soviet | $4,000 | siege | e1+e2 3:1 | $7,000 | 5608 | 3633 | 2267 | 70.1 |
| Soviet | $4,000 | frontal | e1+e2 | $10,000 | 5450 | 4000 | 3400 | 33.7 |
| Soviet | $6,000 | siege | rifle 1:1 | $8,000 | 4400 | 6000 | 3400 | 60.2 |
| Soviet | $6,000 | siege | rifle 2:1 | $7,000 | 5244 | 5667 | 2467 | 59.2 |
| Soviet | $6,000 | siege | rifle 3:1 | $7,000 | 5483 | 4967 | 2267 | 102.1 |
| Soviet | $6,000 | frontal | rifle | $14,000 | 12000 | 4500 | 2733 | 59.5 |
| Soviet | $6,000 | siege | rocket 1:1 | $9,000 | 6900 | 5933 | 2467 | 62.7 |
| Soviet | $6,000 | siege | rocket 2:1 | $8,000 | 5289 | 6000 | 3400 | 53.1 |
| Soviet | $6,000 | siege | rocket 3:1 | $8,000 | 5700 | 5667 | 2467 | 36.8 |
| Soviet | $6,000 | frontal | rocket | $11,000 | 9667 | 6000 | 3400 | 52.1 |
| Soviet | $6,000 | siege | tank 1:1 | $10,000 | 8200 | 5417 | 2467 | 86.9 |
| Soviet | $6,000 | siege | tank 2:1 | $9,000 | 6900 | 6000 | 3400 | 75.0 |
| Soviet | $6,000 | siege | tank 3:1 | $9,000 | 7500 | 5533 | 2467 | 72.7 |
| Soviet | $6,000 | frontal | tank | $12,000 | 9533 | 5633 | 2733 | 83.0 |
| Soviet | $6,000 | siege | mixed 1:1 | $9,000 | 6300 | 5167 | 2467 | 49.7 |
| Soviet | $6,000 | siege | mixed 2:1 | $8,000 | 7111 | 5017 | 2467 | 104.2 |
| Soviet | $6,000 | siege | mixed 3:1 | $8,000 | 7100 | 5967 | 2467 | 103.1 |
| Soviet | $6,000 | frontal | mixed | $10,000 | 6250 | 6000 | 3400 | 38.2 |
| Soviet | $6,000 | siege | e1+e2 1:1 | $8,000 | 6533 | 5400 | 2467 | 63.8 |
| Soviet | $6,000 | siege | e1+e2 2:1 | $9,000 | 6300 | 6000 | 3400 | 64.5 |
| Soviet | $6,000 | siege | e1+e2 3:1 | $9,000 | 5525 | 6000 | 3400 | 58.6 |
| Soviet | $6,000 | frontal | e1+e2 | $13,000 | 9717 | 6000 | 3400 | 42.8 |


### 8.6 What the repositioning model leaves out

- **Movement is idealised.** No pathing, no chokepoints and no terrain speeds: everyone moves in straight lines on Clear. Own units do not block each other; units only sidestep to avoid stacking. Real screens leak at gaps, and real artillery gets stuck behind its own units when it tries to retreat.
- **Retreat room is fixed.** It is 6 cells behind the start for screened artillery, 10 for a group fallback and 15 for unscreened kiting. Real room depends on map edges and the base.
- **Shell flight time is ignored.** Shells take 2.4–2.9 s to arrive and every shot does its expected damage at the aim point, so fast or kiting targets would really dodge some shots. This flatters artillery against vehicles.
- **Enemy behaviour is simple.** Enemies use nearest targeting, except in the flank test. There is no enemy artillery duel, no AI retreat and no regrouping. Defending armies sally by a fixed rule.
- **Turning is partial.** Only body turn times are modelled. Turret turn times, the agent's thinking latency beyond the 0.2 s order gap, and the time to issue up to 200 commands per batch are ignored.
- **Repair is simplified.** It starts instantly and is free. Engineers' instant repair of pillboxes and the cost of repairs are not modelled.


### 8.7 What this means for the agent

- **Firing band to hold.**
  - *arty:* sit 1 cell inside maximum range, at about 11 cells from the target. The safe minimum is 6 cells from pbox, 6.5 from gun, 7 from tsla, 5 from ftur and 4 from agun or sam.
  - *v2rl:* hold at about 9.5 cells. The band against a Tesla coil is only 7 to 10 cells.
  - *Pace:* 4 arty kill any defense in about 9 s from the band without loss; 4 V2 do it in one salvo (about 2 s) and 2 V2 in about 10.5 s. One arty alone takes about 30 s, and 50 s if the defender repairs.
- **Kiting.**
  - *arty:* it should essentially not kite. A 2.6 s turn each way exceeds its 3.4 s reload. It can hold range only against infantry, and in practice prone infantry (38 % of its fire rate), and against the 4tnk and 3tnk. It can never kite dog, jeep, 1tnk, 2tnk, apc, ftrk, ttnk, ctnk or stnk.
  - *v2rl:* it kites infantry by retreating about 6 s after every salvo, keeping about 87 % of its fire rate against upright infantry and 100 % against prone. In the simulation, kiting also turned some lost fights against 1tnk, 2tnk and ttnk into wins. It cannot kite anything faster than 1.76 cells/s for long.
- **Screen size per artillery piece (by cost).**
  - *Most enemy types:* 0.5:1, about 4 rifles per arty, held 2 cells ahead for arty or 4 to 6 cells ahead for V2.
  - *Harder cases:* mass rifles and the Mammoth need 1:1 to 2:1. APCs and jeeps need tanks in the screen; rifle or rocket screens let them through.
  - *Flankers:* fast flankers reached the artillery in nearly every flank test. Keep one or two fast units, such as a tank or the ftrk, as a rear guard, or put the artillery against an edge.
  - *Best recipes:*

    | Faction | Budget | Recipe |
    |---|---|---|
    | Allies | $2,000 to $4,000 | rifle screen 1:1 to 2:1 with arty at gap 2 |
    | Allies | $6,000 | mixed 2:1 to 3:1 |
    | Soviets | $2,000 | mixed 2:1 |
    | Soviets | $4,000 | e1+e2 2:1 |
    | Soviets | $6,000 | mixed 3:1 |

    For Soviets the V2 adds less than the arty does for Allies, and with smart targeting a pure rifle screen did as well.
  - *Retreat:* when enemies close, hold the screen and move only the artillery. Falling back together lost most of the Allied fights that the hold version won; for Soviets the gap was smaller.
- **Attacking a base.** Sieging from stand-off took 30 to 50 % less money than the cheapest frontal assault:
  - Allies needed $3,000 / $5,000 / $7,000 against $2,000 / $4,000 / $6,000 defending armies, against $6,000 / $9,000 / $12,000 frontal.
  - Soviets needed $3,000 / $5,000 / $7,000 against $5,000 / $8,000 / $10,000 frontal.

  The price is time: 1 to 4 minutes to raze, against 0.5 to 1 minute frontal.
- **Order cadence.**
  - *Every batch:* re-check artillery positions every batch (0.2 s, the platform minimum).
  - *Artillery:* issue a retreat only when an enemy comes within 6 cells, and an attack or stop order as soon as the retreat leg ends.
  - *V2 kiting:* time one retreat leg of about 6 s per salvo.
  - *Screen:* re-post every 1 to 2 s.

  At 1 s or more of agent latency per decision, V2 kiting still fits inside its reload. Arty kiting does not.


## Appendix: aircraft and ships [F]

Only AA weapons hit air units (`ValidTargets: AirborneActor`): e3 RedEye, ftrk FLAK-23-AA, Mammoth Tusk, agun, sam.

| Item | Type | Side | Needs | Tech ctr | Cost | HP | Armour | Cells/s | Weapons (range) |
|---|---|---|---|---|---|---|---|---|---|
| **heli** | aircraft | A | hpad, atek | yes | 2,000 | 12,000 | Light | 3.64 | HellfireAA r4 min1.25; HellfireAG r5 min1.25 |
| **mh60** | aircraft | A | hpad |  | 1,500 | 10,000 | Light | 2.73 | ChainGun r5 min0.75 |
| **tran** | aircraft | A | hpad |  | 900 | 14,000 | Light | 3.12 | - |
| **yak** | aircraft | S | afld |  | 1,350 | 6,000 | Light | 4.35 | ChainGun.Yak r5 min2.5 |
| **mig** | aircraft | S | afld, stek | yes | 2,000 | 8,000 | Light | 5.44 | Maverick r9 min2 |
| **ss** | ship | S | spen |  | 950 | 25,000 | Light | 1.9 | TorpTube r9 |
| **msub** | ship | S | spen, stek | yes | 2,000 | 40,000 | Light | 1.07 | SubMissile r20 min0.5; SubMissileAA r8 min0.5 |
| **dd** | ship | A | syrd, dome |  | 1,000 | 40,000 | Heavy | 2.25 | Stinger r7.5 min0.5; DepthCharge r5; StingerAA r7.5 min0.5 |
| **ca** | ship | A | syrd, atek | yes | 2,400 | 80,000 | Heavy | 1.07 | 8Inch r20 min3; 8Inch r20 min3 |
| **pt** | ship | A | syrd |  | 500 | 20,000 | Heavy | 3.47 | 2Inch r5.5; DepthCharge r5 |
| **lst** | ship | A+S |  |  | 500 | 40,000 | Heavy | 2.81 | - |
