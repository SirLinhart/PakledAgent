# Local Red Alert (OpenRA) under program control: feasibility check

Date: 2026-10-06. Produced by a time-boxed investigation agent on the development
workstation (Windows 11). Each claim is marked **[ran]** (executed here) or **[read]**
(source only). Work folder: `tmp\openra-local\` (untracked scratch, 626 MB); `ENG` below
means `tmp\openra-local\engine-src\OpenRA`.

## 1. Verdict

**It works today, natively on Windows, with no Docker, WSL or administrator rights.**
The route is OpenRA-RL's modified engine (github.com/yxc20089/OpenRA, branch `bleed`,
commit 8a5d224, 2026-04-13), built with the already-installed .NET SDK and driven over
its gRPC bridge from a Python virtual environment.

- **Bots and maps:** headless games ran against Rush, Normal and Turtle AI on sidestep,
  two of them to game over. All three tournament maps loaded and ran. [ran]
- **Scripted client:** deployed the MCV, built a power plant and barracks, trained five
  rifle infantry and attack-moved them. [ran]
- **Two agents:** two external agents shared one game, each with its own fogged view. [ran]
- **Speed:** real time (about 25 ticks/s), slower, faster and fully stepped (the game
  waits for the agent) all ran. [ran]
- **What is missing:** the bridge gives raw state and takes raw commands, with no
  verdicts, events, `legal_actions`, limits or match lifecycle. An adapter is needed.
- **Patch:** a 15-line local engine patch (`tmp\openra-local\local-engine-patch.diff`)
  was applied and tested. It is needed only for the game-speed switch and for stepped
  two-agent games.

Not done: OpenRA-RL's own Python layer (`openra_env`) was not used (stubs were generated
from the engine's `.proto`); plain upstream OpenRA was not tried; no windowed game was
opened.

## 2. What was installed

Nothing machine-wide. All under `tmp\openra-local\`:

| Item | Size | Note |
|---|---|---|
| `engine-src\OpenRA` | 131 MB | Shallow clone of the engine fork plus build output in `bin\` |
| `nuget-packages` | 446 MB | NuGet packages (`NUGET_PACKAGES` pointed here) |
| `venv` | 45 MB | Python 3.11 venv: grpcio 1.84.0, grpcio-tools, protobuf 7.36.2, pydantic, pyyaml |
| `rl-src\OpenRA-RL` | 2 MB | Copy of the reference Python wrapper (read only) |
| `support` | 1 MB | OpenRA settings, logs, 11 replays |
| `scripts`, `logs`, `NuGet.config`, build logs, `local-engine-patch.diff` | <1 MB | Scripts and outputs |

Nothing was left running. Toolchains found: .NET SDK 8.0.418 and 10.0.103; Python
3.11.6; git 2.53; WSL has a stopped Ubuntu (not used); no Docker, no make, no protoc.

## 3. Reproduction commands (all [ran])

```powershell
$w='C:\Projects\UCLA-Competition\tmp\openra-local'
git clone --depth=1 --branch bleed https://github.com/yxc20089/OpenRA.git $w\engine-src\OpenRA
# NuGet.config in $w with <clear/> + nuget.org only (the user-level config lists a dead MyGet feed)
$env:NUGET_PACKAGES="$w\nuget-packages"
cd $w\engine-src\OpenRA; dotnet build -c Release --nologo -p:TargetPlatform=win-x64 -v minimal
python -m venv $w\venv
& $w\venv\Scripts\python.exe -m pip install grpcio grpcio-tools protobuf pydantic pyyaml
& $w\venv\Scripts\python.exe -I -m grpc_tools.protoc "-I$w\engine-src\OpenRA\OpenRA.Mods.Common\Traits\Player\Protos" "--python_out=$w\scripts\gen" "--grpc_python_out=$w\scripts\gen" rl_bridge.proto
```

Engine launch for a single headless game:

```
dotnet ENG\bin\OpenRA.dll Engine.EngineDir=<ENG> Engine.SupportDir=<w>\support Game.Mod=ra Launch.Map=sidestep.oramap Launch.Bots=Multi1:rl-agent,Multi0:normal Game.Platform=Null
   env RL_GRPC_PORT=19999; with the local patch also RL_GAMESPEED=slowest|default|fastest
```

For many stepped games in one process, replace `Launch.Map`/`Launch.Bots` with
`Launch.MultiSession=19998` and create games over gRPC with
`CreateSession(map_name, bots, seed)`.

Scripts in `tmp\openra-local\scripts\`: `run_game.py` (scripted opener versus a bot),
`experiments.py` (`realtime`, `maxspeed`, `fullgame rush|turtle`, `twoagents`),
`multisession.py` (stepped daemon, parallel games), `ms_fullgame.py`, `patched_tests.py`
(`speeds`, `ms2`).

Errors met: the first build failed with 558 `NU1301` errors because the user's global
NuGet configuration lists a dead MyGet feed (fixed with a local `NuGet.config`; the
user-level configuration was not changed); the reference copy's engine submodule folder
is empty, so the engine was cloned; `rl-bridge.log` is flushed only every 5 s.

Scripted game on sidestep versus Normal AI, by tick [ran]: deploy 9; `build powr` 27,
placed 213; `build barr` 219, placed 525; `train e1` at 537, 603, 669, 735, 801;
`attack_move` 735. Build times match the platform primer (powr 180 ticks, barracks 300,
e1 60), as do the $5,000 and MCV-only start.

Bridge calls (service `openra.rl.RLBridge`,
`ENG\OpenRA.Mods.Common\Traits\Player\Protos\rl_bridge.proto`): `GetState(session_id)`;
`FastAdvance(ticks, commands[], session_id)` applies commands, advances and returns the
observation; `CreateSession` / `DestroySession` in daemon mode. A command is
`{action, actor_id, target_actor_id, target_x, target_y, item_type, queued}` with actions
MOVE, ATTACK_MOVE, ATTACK, STOP, HARVEST, BUILD, TRAIN, DEPLOY, SELL, REPAIR,
PLACE_BUILDING, CANCEL_PRODUCTION, SET_RALLY_POINT, GUARD, SET_STANCE, ENTER_TRANSPORT,
UNLOAD, POWER_DOWN, SET_PRIMARY, SURRENDER. Exercised: DEPLOY, BUILD, PLACE_BUILDING,
TRAIN, ATTACK_MOVE; the rest are [read] only.

## 4. Uses

| Use | Status | Evidence |
|---|---|---|
| Our agent versus built-in bots | Supported | [ran] Normal, Rush, Turtle on sidestep, headless |
| Two external agents in one game | Supported in real time unpatched; supported with the small patch when stepped | [ran] both modes |
| Unattended, headless, chosen speed, stepped | Supported | [ran] all four |
| State and orders like the platform | Supported with work (adapter) | [ran] state fields returned, fog respected |

Detail:
- Bot types: `rush`, `normal`, `turtle`, `naval`, plus the fork's `beginner`, `easy`,
  `medium` [read `ENG\mods\ra\rules\ai.yaml`]. The trivial opener lost to all three
  (Rush at tick 7563, Turtle at 19683, Normal at 13908) because it never built an economy.
- Two agents, unpatched: `Launch.Bots=Multi0:rl-agent,Multi1:rl-agent` gives each seat its
  own session id, units and fogged view. In daemon mode both seats share one id; the patch
  adds aliases `<session>:Multi0` and `<session>:Multi1`.
- Speeds (patched env var): slowest 13.2, default 23.6, fastest 43.6 ticks/s. Fast-forward
  in single-game mode reached 88 to 218 ticks/s. Stepped daemon mode waits for the agent
  and reached 4,494 ticks/s for one game, and 7,848 ticks/s total across 4 parallel games.
- Update rate: 5 Hz polling held easily (mean call 18 ms, max 57 ms).

Caveats found by running: `FastAdvance(n)` advances n+1 ticks; in stepped mode the engine
never reported game over after total defeat (single-game mode does); the shroud lifts for
the loser on defeat; in stepped two-seat mode concurrent `FastAdvance` calls on one world
can hang a caller [read]; a possible visibility lag on buildings was seen once and not
investigated.

## 5. Gaps against the platform interface (redalert.md sections 4 and 5)

State: `events`, `legal_actions` (including `attack_now`, `enemy_base`), `last_orders`,
`recent_order_problems`, `pacing`, `limits`, `time`, `match_rules`, phases, `players`,
`result`, `map.bounds`, `powered_down`, explicit `ready`, explicit `wreck`, `base_center`
and the game config (rules, map layout) are absent and must be built by an adapter. Map
bounds read from each `map.yaml` equal the platform's (sidestep 96x96, trapped 97x97,
shadowfiend-II 83x90) [ran]. Other fields map one to one with renames (`actor_id` to `id`,
`cell_x`/`cell_y` to `x`/`y`, `hp_percent` 0..1 to percent, `remaining_ticks`/25 to
`remaining_s`).

Orders: all platform orders have direct equivalents, one command per unit (the adapter
expands `units`). `train` takes one item per command. `repair` and `power_toggle` are
plain toggles. There are no verdicts or reasons: invalid commands are dropped silently.
Ownership and visibility are not enforced by the bridge [read `ActionHandler.cs:119`,
`:149`], so the adapter must enforce both. No limits (20 orders, 50 units, 200 commands,
0.2 s gap, staleness) and no apply delay; the adapter implements them.

## 6. Adapter effort

A faithful adapter is estimated at 6 to 10 working days; a first usable version (our agent
versus a built-in bot, platform-shaped state and order dicts, basic validation) at 2 to 3
days.

Main risks: fidelity cannot be verified locally (the platform's engine version, rule set
and verdict wording are unknown; the fork's infantry, vehicle and player rules are
identical to the upstream reference checkout and structures differ by 2 lines); engine
verdicts are invisible; the victory rule must be implemented by the adapter in stepped
mode; lobby options (crates, explored map, starting units) take engine defaults; the fork
is a small research project and we would carry a local patch; built-in bots are far
weaker than tournament opponents.

## 7. Decisions for a person

- Docker, WSL, administrator rights: none needed.
- Keep and maintain the local engine patch (4 files, 15 lines), or restrict two-agent games
  to unpatched real-time mode.
- The dead MyGet feed in the user-level NuGet configuration was left untouched.
- Watching games in a window needs Red Alert content via OpenRA's own installer and uses
  the GPU; not tried.
- Licence: the engine and OpenRA-RL are GPL-3.0. Fine for a local test tool, but the
  adapter should stay a separate process speaking gRPC, and the engine must not be copied
  into the tournament repository.

## 8. Engine version and map pool

OpenRA-RL fork of OpenRA `bleed`, commit 8a5d224 (2026-04-13), .NET 8.0.24. The maps
`sidestep.oramap`, `trapped.oramap` and `shadowfiend-II.oramap` are in `ENG\mods\ra\maps`,
byte-identical to the upstream reference checkout, and all three loaded and ran.
