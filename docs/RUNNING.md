# Running the agent

The agent is one long-running process started from the repository root. It
signs in with the Official Agent Key, waits for games assigned to it, and
plays each one in its own worker process until it is stopped. This page gives
the exact commands for a test session and for tournament day, and the rules a
person follows while it runs.

All commands are run from the repository root.

## Setup (once per machine)

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
Copy-Item .env.example .env
```

macOS or Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Then edit `.env` and fill in both values:

- `ALTRUAGENT_CONTROL_URL`: `https://api.altruagent-game.com` for the deployed
  platform.
- `ALTRUAGENT_OFFICIAL_AGENT_KEY`: the Official Agent Key from the tournament
  dashboard's Agent Configuration page (the agent must be set to Self-hosted
  there). It starts with `eak_live_` and is shown once.

`.env` is never committed. Do not paste the key anywhere else: not into
`config/`, a log, a screenshot or a commit message.

Python 3.11 or newer is required.

## Check the connection

```
python -m agent --check-tournament
```

This plays nothing. It checks that the platform is reachable, that the key is
accepted, that assignments can be listed and that the agent can be created.
Every line must start with a check mark (or `[ok]`), and the last line is
`Ready to play Testing and tournament games`. The exit code is 0 only when
every step passes.

The check takes no lock and writes no run record, so it can be run while the
agent is already running.

## A test session

**Testing closes at 11:59 p.m. Pacific on 2026-10-13**, when submissions
close, and switching the agent back to Self Hosted in Agent Configuration
closes with it: platform test matches must be played before then.

1. Check the connection (above).
2. Start the agent for test matches and leave it running:

   ```
   python -m agent --match
   ```

   It prints `Connected with your Official Agent Key.`, then
   `Playing your test matches only. ...`, then `Waiting for your next game...`.
3. On the tournament dashboard's Testing page, create a test match and set
   your seat to **Mine (self-hosted)**, or join a match from **Open matches**.
   The running agent picks the seat up within about 10 seconds, prints
   `Match assigned: ...` and `Starting match...`, plays the match to its end,
   prints `Match finished.` and goes back to waiting.
4. When the session is over and no match is running, stop the agent with
   Ctrl+C.

### Self-play

To play the agent against itself, create one test match on the Testing page
and set **both** seats to **Mine (self-hosted)**. The one running
`python -m agent --match` process plays both seats. Each seat gets its own
worker process and its own decision-maker, so the two sides share nothing.
Do not start a second copy of the agent for the second seat.

## Tournament day

1. Register the agent for a game's tournament on the dashboard only if
   [GAMES-STATUS.md](GAMES-STATUS.md) lists that game as `ready`.
2. Well before the first round, check the connection:

   ```
   python -m agent --check-tournament
   ```

3. Start the agent for tournament games and test matches in one process:

   ```
   python -m agent --tournament --match
   ```

   (`python -m agent --tournament` plays tournament games only.)
4. Leave it running for the whole tournament. Games are assigned to the agent
   when a round starts. An agent that has not connected by a game's connect
   deadline loses that game, so the process must already be running.
5. Do not touch the machine's copy of the repository while it runs (see
   [Hands off once a match has begun](#hands-off-once-a-match-has-begun)).

**A tournament game that fails for a server problem is replayed and never
scored.** A server problem is a failure on the platform's side: the game
server crashed, restarted or lost the game, the game could not be set up or
started, it got stuck and was closed at its time limit, or the platform's
sign-in, list of games or game credentials failed for an agent. Nobody gets a
point, a loss or a draw for such a game. The platform plays it again by
itself, as a new game for the same agents with a fresh connect window, at
most twice; after that an admin decides. The replay arrives as a new
assignment whose round label ends with `(a replay)`, so the agent must still
be running to play it; it is a new session, with a match folder of its own.
A game in which an agent never connects, or crashes, stops answering or runs
out of time on its own, is not a server problem: it is scored as played. A
test match the game server lost ends with no result and is not replayed.

Use one process for both kinds of game. A `--match` process and a
`--tournament` process side by side are not possible: the second one is
refused (next section).

## One copy at a time

Only one copy of the agent that plays games may run on a machine. At startup
the agent creates the file `.agent.lock` in the repository root, containing
its process id, and removes it when it stops.

- If the lock names a process that is alive, a second copy prints
  `Another copy of the agent is already running (pid N)` and exits with code 3
  before it contacts the platform.
- If the lock was left behind by a copy that was killed, it names a process
  that no longer exists. The next start replaces it and proceeds.
- `python -m agent --check-tournament` ignores the lock.

If the agent reports another copy and you are certain none is running (after a
crash or a reboot, the old process id can belong to an unrelated program),
confirm that no `python -m agent` process exists (Task Manager on Windows,
`ps` elsewhere), delete `.agent.lock`, and start again. Never delete the lock
while a copy is running.

## Stopping and restarting

Ctrl+C stops the agent and every game's worker process. It never resigns a
match and sends nothing more to the platform. If it is stopped in the middle
of a match, running the same command again signs in again, finds the match
still assigned and resumes it, after up to about 35 seconds while the previous
process's hold on the seat runs out. Stopping or restarting during a match is
a manual action and must be logged (see below).

Exit codes of `python -m agent`:

| Code | Meaning |
|---|---|
| 0 | Stopped normally (Ctrl+C), or the connection check passed |
| 1 | Could not start or keep running: configuration, key, connection or agent problem; the message says which |
| 2 | Wrong command-line flags |
| 3 | Another copy of the agent is already running |

## Temporary platform errors

The platform's error guide (`docs/reference/altruagent/07-errors.md`) names
three temporary errors of the game server. The starter vendored in
`src/altruagent/` (upstream commit `951f4f7`, see
`src/altruagent/UPSTREAM.md`) answers them as follows:

- `TOO_MANY_WAITS` (two `wait_for_update` calls are already waiting in the
  game): handled. The runner pauses and calls again, as after any temporary
  failure: about 1 second at first, growing to 5, for up to 90 seconds
  without a successful call.
- `RUNTIME_TEMPORARILY_UNAVAILABLE` (the game's engine is busy or
  restarting): handled. A Red Alert order it refuses is not sent again: the
  runner reads the state after 1 second and decides again, for as long as
  the game goes on. Any other call is retried as after `TOO_MANY_WAITS`;
  after 90 seconds without a successful call the game's worker stops, and
  the runtime starts a new one for the game while it is still assigned.
- `AUTH_TEMPORARILY_UNAVAILABLE` (HTTP 503 with a `Retry-After` header: the
  game server cannot check tokens for a moment): **known gap.** The starter
  does not name this code and does not read `Retry-After`. It retries the
  503 as any temporary server error (5xx), with the same token at its own
  pace, for up to 90 seconds. If it lasts longer, the runtime starts the
  game's worker again after its own backoff, and the new worker asks for the
  seat grant again, which the platform's guide says not to do.

**Known gap: a temporary error at the first sign-in stops the agent.** When
the platform answers the very first sign-in after startup with a temporary
error (`agent_session_unavailable`, `rate_limited` or another 5xx),
`python -m agent` prints `Could not connect with your Official Agent Key: ...`
and exits with code 1, where the platform's guide says to back off and
retry. Start it again after a minute. Once the agent is running, a sign-in
that meets a temporary error is retried after 10 seconds, doubling to at
most 60, while the games in progress go on.

## Settings

Settings that affect play are in tracked files under `config/`; the agent
reads them at startup and never changes them.

**The local models the agent starts.** `config/agent.toml` ships with
`[models] autostart = true` and `autostart_roles = ["strong", "jev9b"]`: the
agent starts the `strong` and `jev9b` roles of the included local model
server itself before it begins waiting for games, and stops them on exit.
`strong` takes about 30 GB of VRAM and about 2 GB of RAM, and `jev9b`,
JevK5-9B, about 23 GB of VRAM: about 53 GB of VRAM together, which fits the
96 GB card (see [docs/MODELS.md](MODELS.md)). The Werewolf
and Pokémon choices and messages are asked of `strong`, and Red Alert's
choices of `jev9b`; `fast` and `small`, the 4B model, are defined but not
loaded. Loading them takes up to a few
minutes the first time and a few seconds when the model files are still in
the system's file cache. `autostart_roles` lists the roles of
`config/models.toml` the agent starts; a name that file does not have stops
the agent at startup with a configuration error. A listed role whose model
file is missing, or that cannot start, is reported down, and its choices fall
back to the game's script. To turn it off, set `autostart = false` (or
`autostart_roles = []`) in the `[models]` table of `config/agent.toml`: no
model is started, and every choice and message is the script's own.
`python -m ucla_agent.check` names the autostart roles on its `model_server`
line and says whether each answered.

**Red Alert's decision maker.** Red Alert ships with JevK5-9B (`jev9b`)
as plan chooser and commander: `[redalert] model_plan_choice = true` and
`model_commander = true`, and the `red_alert_plan`, `red_alert_scout` and
`red_alert_commander` chains of `[decide.chains]` are `["systemone:jev9b"]`
(`model_scout_choice` stays `false`). The model chooses the plan among the
open ones and one checked action a batch for the first scout, then main;
the script builds and checks every option it is offered, carries the choice
out, and plays its own choice when no answer arrives within the deadline or
`jev9b` is down. If the 9B does not start, the script plays Red Alert; no
chain goes on to another model. To switch Red Alert to script-only, set
`model_plan_choice = false` and `model_commander = false` in the
`[redalert]` table of `config/agent.toml`; drop `jev9b` from
`autostart_roles` as well to free its VRAM.

**What the 9B needs.** `jev9b` is the systemone role of `config/models.toml`:
it runs in JevTest's native Python environment
(`C:/Projects/JevTest/jev-bench/.native-win`) from the JevK5-9B folder
`E:/Models/JevK5-9B`, and took about 70 s to start, measured under concurrent
GPU load. `python -m ucla_agent.check` reports it on its `model_server` line
(a `WARN` when it does not answer, as when either path is missing), and the
model server's startup line for the role names a missing path.
[docs/MODELS.md](MODELS.md) gives the setup and the measured latency. The 4B
(`small`) is defined in `config/models.toml` but not used as shipped.

## What a run records

Everything the agent records goes under `runs/`, which is not tracked by git.

- `runs/<UTC yyyymmdd>/run.jsonl` gets one `run_started` line when the agent
  starts (the git commit of the working tree, the SHA-256 of every file under
  `config/`, the command-line flags and the UTC time) and one `run_stopped`
  line when it stops. A run that was killed has no `run_stopped` line.
- Each seat the agent plays in a match writes its own folder
  `runs/<UTC yyyymmdd>/<session id>__<seat>/` (see
  [Reviewing a run](#reviewing-a-run) for what is in it).

The agent never deletes anything under `runs/`. Keep the whole folder for at
least 30 days after the tournament results are announced: it is the record
that connects the submitted commit to what the agent did in each match.

## Reviewing a run

After a run, the report tool turns the match records into figures. It only
reads `runs/` and writes its own files, and it needs no key and no network.

```
python scripts/report.py runs/20261016
python scripts/report.py "runs/20261016/<session id>__<seat>"
```

A dated folder stands for every match folder of that day; a match folder
stands for itself. Several paths may be given.

- `metrics.json` is written into each match folder: outcome, decision and
  latency figures, fallbacks, errors and, for Red Alert, the order and economy
  figures.
- `report.md` is written once for all of them, into
  `runs/reports/<UTC timestamp>/`: one row per match folder and the totals by
  outcome.

To compare two sets of matches (two days, or the same day before and after a
change):

```
python scripts/report.py --compare runs/20261015 runs/20261016
```

This writes `compare.md` into `runs/reports/<UTC timestamp>/`, with the mean
of every numeric metric over the first set (A), over the second set (B), and
the difference B minus A. It writes no `metrics.json`.

How the records are laid out:

- **Folders are per seat.** A match folder is the record of one seat:
  `runs/<UTC yyyymmdd>/<session id>__<seat>/`, where the seat is the game's
  name for it (`Multi0` or `Multi1` in Red Alert). In self-play both seats
  share the session id and each has its own folder; `report.md` shows them on
  adjacent rows, marked as the same game.
- **Files are per attempt.** `events.jsonl` and `meta.json` are the first
  attempt. If the agent was restarted during the match, the new process writes
  `events.2.jsonl` and `meta.2.json` next to them, and so on; it never touches
  the earlier files. The report reads all attempts of a folder as one match.

The scripts under `scripts/` import the agent's packages, so they need the
editable install from [Setup](#setup-once-per-machine)
(`pip install -e ".[dev]"`) in the active environment.

## Local benchmark

The local benchmark measures one version of the agent against OpenRA's
built-in bots with the local harness, a separate project at
`C:\Projects\UCLA-RedAlert`. The harness is used by its path only: nothing of
it is in this repository, and nothing under `src/` calls it. One run of the
benchmark set is a **label**, kept in a folder of its own.

**The set.** 12 configurations, each played with N seeds (N is 3 by default,
36 games), on the stepped clock with 5 ticks per decision:

- the opponent `bot:rush`, then `bot:normal`;
- for each, our faction `england`, then `russia`;
- for each, the maps `sidestep`, `trapped`, then `shadowfiend-ii`.

Configuration k (0 to 11, in that order) plays seeds `401 + N*k` to
`400 + N*(k+1)`. With the default N = 3 these are `401 + 3k`, `402 + 3k` and
`403 + 3k`. **With N = 6 (72 games) the set is a smoke check across all 12
configurations; whether a change is kept or dropped is decided by a
keep-or-drop comparison (below), with at least 200 games per arm.** A bot
game is not the same game each time its seed is played: our
orders match only for the first 70 to 90 seconds, and the bot's faction
changes with the seed. 36 games are too few to tell close variants apart:
12 wins of 36 against 6 of 36 gives a Fisher p-value of 0.17.

**Running a label.** In PowerShell, set the label and the seed count N (the
procedure's `--seeds N`, here `$seeds`), then run the loop. Each game gets the
folder `runs\bench\<label>\<bot>-<faction>-<map>-<seed>\` in the harness: the
harness's `--out` files (`summary.json` and `engine-logs`) go there, and the
agent's match records go to its `records\` subfolder, because
`UCLA_AGENT_RUNS_DIR` names that folder for the game. The agent still reads its
configuration from the repository root.

```powershell
$label = 'baseline'
$seeds = 3
$harness = 'C:\Projects\UCLA-RedAlert'
Push-Location $harness
$k = 0
foreach ($bot in 'rush', 'normal') {
    foreach ($faction in 'england', 'russia') {
        foreach ($map in 'sidestep', 'trapped', 'shadowfiend-ii') {
            for ($i = 1; $i -le $seeds; $i++) {
                $seed = 400 + $seeds * $k + $i
                $game = "$harness\runs\bench\$label\$bot-$faction-$map-$seed"
                $env:UCLA_AGENT_RUNS_DIR = "$game\records"
                .\.venv\Scripts\python.exe -m openra_harness play --map $map --faction $faction `
                    --opponent "bot:$bot" --clock stepped --ticks-per-decision 5 --games 1 --seed $seed --out $game
            }
            $k++
        }
    }
}
Remove-Item Env:UCLA_AGENT_RUNS_DIR
Pop-Location
```

The harness plays the agent of this repository's `src/`; its README shows how
to play an exported snapshot of another commit instead (`--agent-src`, with
`UCLA_AGENT_ROOT` set to the snapshot). A loop that is stopped part-way leaves
`UCLA_AGENT_RUNS_DIR` set in that PowerShell session: remove it
(`Remove-Item Env:UCLA_AGENT_RUNS_DIR`) before the agent plays anything else
there. A label played before `UCLA_AGENT_RUNS_DIR` existed has its records
under the agent's own `runs/`; copying each game's newest record folder
(`game1-<map>-<seed>__Multi0`, inside its dated folder) into that game's
`records\` fills in the record columns.

**The table.**

```
python scripts/report.py --bench C:\Projects\UCLA-RedAlert\runs\bench\baseline
```

This prints comma-separated lines to the console and writes no file:

- one row per game, in configuration and seed order: `config`, `seed`,
  `result` (`win`, `loss`, `draw` or `no_result` for the agent's seat),
  `termination_reason`, `kd_value` and `assets_value` (the agent's tiebreak),
  `army_value_produced` (the cost of the units made, harvesters and MCVs left
  out), and from the agent's record `income_per_s`, `barracks_max`,
  `war_factories_max`, `low_power_seconds` and `compute_ms_p95`. A value is
  `-` when the game's record, or that field, is missing; a game folder
  without `summary.json` is a game the harness did not finish, and counts as
  `no_result`;
- the build profile: for each configuration, the mean per game of main tanks
  (`2tnk`, `3tnk`), defenses (`pbox`, `ftur`, `gun`, `tsla`), power plants and
  producers (barracks and war factories), all from `summary.json`. These move
  less from game to game than the results do;
- the totals: wins, losses, draws and no-results, overall and against each
  bot, and the mean of each numeric column;
- `goal: met` or `goal: not met`, by the success measure below.

**Success measure.** Against `bot:normal`, more than half of the label's
games won; against `bot:rush`, at least half. With N = 3 that is more than 9
of 18 against normal and at least 9 of 18 against rush. A game without a
result counts as a game not won.

**Comparing two labels.**

```
python scripts/report.py --bench-compare C:\Projects\UCLA-RedAlert\runs\bench\baseline C:\Projects\UCLA-RedAlert\runs\bench\candidate
```

This prints the totals of both labels (A, then B), the difference B minus A
of the win counts, overall and against each bot, the two-sided Fisher exact
p-value of each of those differences (`fisher_p_two_sided`), and the
difference of each mean. A p-value well above 0.05 means the two labels'
wins do not tell the versions apart. It writes no file; `--compare` and its
`compare.md` are a separate thing.

**Keep-or-drop comparisons.** Whether a change is kept is decided on two
arms, the code without the change and the code with it, each one label:

- at least 200 games per arm in the configurations the change targets, each
  on a fresh seed (one game per seed), both arms on identical seeds; for a
  Soviet change against `bot:normal`, for example, `normal`-`russia` on the
  three maps with 72 seeds each, 216 games;
- a no-regression check of the configurations the change does not target,
  with at least 144 games per arm, on seeds of their own (24 seeds for each
  of six configurations, for example);
- one code snapshot per arm: `git archive` of the commit into `tmp`, played
  with the harness's `--agent-src` and with `UCLA_AGENT_ROOT` set to the
  snapshot, so that the code, the configuration and the records all come
  from it. For a configuration change both snapshots come from one commit,
  and the change is an edit of one snapshot's
  `config\redalert\tuning.toml`;
- the two arms run at the same time, each in its own PowerShell window, on
  the harness ports 5310 and 5320 (`--port`).

The 72-game set remains the smoke check across all 12 configurations. The
larger count is needed because a single game on a seed decides little:
between two arms whose code is nearly the same, more than 40 % of the
seed-matched games change outcome (`docs/BENCHMARKS.md` gives the counts),
so a 72-game comparison resolves only large effects. A game takes about 30
seconds on the stepped clock: two 144-game arms take about 66 minutes and
two 216-game arms about 1 hour 45 minutes, the arms in parallel. The live
agent and its model server may keep running meanwhile: harness runs are
unaffected by them. The offline test suite (`pytest`), though, needs the
live agent stopped first.

The two snapshots of a configuration change, `snap-off` and `snap-on`:

```powershell
$repo = 'C:\Projects\UCLA-Competition'
$commit = '340296f'
New-Item -ItemType Directory -Force "$repo\tmp\bench" | Out-Null
foreach ($arm in 'snap-off', 'snap-on') {
    git -C $repo archive $commit --prefix="$arm/" -o "$repo\tmp\bench\$arm.tar"
    tar -xf "$repo\tmp\bench\$arm.tar" -C "$repo\tmp\bench"
}
```

Then edit `tmp\bench\snap-on\config\redalert\tuning.toml`. For a code
change, archive the commit without the change as `snap-off` and the one with
it as `snap-on` instead.

One arm, here `snap-on` on port 5320; the other arm runs the same loop at
the same time with its own label, `$snapshot` and `$port = 5310`, on the same
`$plan`:

```powershell
$label = 'candidate-on-216'
$snapshot = 'C:\Projects\UCLA-Competition\tmp\bench\snap-on'
$port = 5320
$harness = 'C:\Projects\UCLA-RedAlert'
# Each targeted configuration: the bot, our faction, the map and its first seed.
$plan = @(
    @('normal', 'russia', 'sidestep', 600),
    @('normal', 'russia', 'trapped', 700),
    @('normal', 'russia', 'shadowfiend-ii', 800)
)
$seedsEach = 72
$env:UCLA_AGENT_ROOT = $snapshot
Push-Location $harness
foreach ($configuration in $plan) {
    $bot, $faction, $map, $firstSeed = $configuration
    for ($seed = $firstSeed; $seed -lt $firstSeed + $seedsEach; $seed++) {
        $game = "$harness\runs\bench\$label\$bot-$faction-$map-$seed"
        $env:UCLA_AGENT_RUNS_DIR = "$game\records"
        .\.venv\Scripts\python.exe -m openra_harness play --agent-src "$snapshot\src" --port $port --map $map `
            --faction $faction --opponent "bot:$bot" --clock stepped --ticks-per-decision 5 --games 1 --seed $seed `
            --out $game
    }
}
Remove-Item Env:UCLA_AGENT_RUNS_DIR, Env:UCLA_AGENT_ROOT
Pop-Location
```

When both arms are done, `--bench-compare` with the off arm's folder first
gives the difference in wins, its Fisher p-value and the difference in mean
`kd_value`. `docs/BENCHMARKS.md` records the figures and the decision.

**Opening-order comparison.** To compare the two opening orders, run one
label with `[opening].order = "refinery_first"` set by a local, uncommitted
edit of `config/redalert/tuning.toml`, and one label of the same code with
the tracked `barracks_first`. Then compare the two with `--bench-compare`.
Undo the edit before anything is committed or the agent plays any other game.

## Watching a Red Alert game

One command plays one real-time Red Alert game of the committed agent
against a built-in bot in the game's observer window, with the live feed page
of the match open beside it. It needs the local harness (see
[Local benchmark](#local-benchmark)) with its `.venv`. Name the harness
folder once in the PowerShell session, then run the command from the
repository root:

```powershell
$env:UCLA_REDALERT_HARNESS = '<harness folder>'
python scripts/watch_game.py
```

`--harness <harness folder>` names it for one run instead. The options and
their defaults:

- `--faction england|france|germany|russia|ukraine|random`, default `random`;
- `--opponent bot:<type>`, default `bot:normal` (`bot:rush` and the
  harness's other bots work too);
- `--map sidestep|trapped|shadowfiend-ii`, default `sidestep`;
- `--model none|small|fast|strong|jev9b`, default `small`: the role of
  `config/models.toml` that chooses the plan; with `none` the script chooses;
  `jev9b` has the plan, scout and commander chains ask the native JevK5-9B
  server (`systemone:jev9b`), which the snapshot's model server starts on its
  own watch port 41603 (see [docs/MODELS.md](MODELS.md)). JevTest's own
  server listens on 8765, the default feed port: give another `--feed-port`
  (for example `--feed-port 8790`) while it runs;
- `--set <file>:<section>.<key>=<value>`, as often as needed: one key of
  `config/agent.toml` (`agent:`) or `config/redalert/tuning.toml`
  (`tuning:`), for example `--set tuning:raids.standing=true`;
- `--feed-port <port>`, default `8765`.

```
python scripts/watch_game.py --faction russia --map trapped --model fast --set tuning:raids.standing=true
```

What it does, in order:

1. **Snapshot.** HEAD is exported into `tmp/watch/<UTC stamp>/snap`. The game
   plays the last commit, never the working tree: commit a change before
   watching it.
2. **Settings.** `--model` and every `--set` are written into the snapshot's
   files only; the working tree's `config/` is never written. With a model,
   `[redalert] model_plan_choice` is turned on, `[decide.chains]
   red_alert_plan` names that role and `[models] autostart_roles` holds it;
   with `none`, `model_plan_choice` is turned off. The snapshot's model
   server gets port 11600 and its roles ports from 41600. The snapshot's own
   loaders then read its settings: a key they do not know or a value they
   refuse stops the command, naming the key, before anything starts.
3. **Setup.** The setup (commit, settings, faction, map, opponent, model,
   folders and addresses) is printed and saved as `setup.json` in
   `<harness folder>\runs\watch\<UTC stamp>\`, next to the agent's records
   in its `records\` folder.
4. **Model.** The chosen role is started from the snapshot on
   127.0.0.1:11600, and the command waits until it answers (the strong role
   can take a few minutes to load the first time).
5. **Game.** The harness plays one game with the snapshot's agent:
   `play --watch --clock realtime --games 1 --port 5500`, with
   `UCLA_AGENT_ROOT` set to the snapshot and `UCLA_AGENT_RUNS_DIR` to the
   game's `records\` folder. The observer window opens and the game starts
   by itself.
6. **Feed.** The feed page is started on the game's records and opened in
   the default browser.
7. **End.** When the game ends, the window is closed or Ctrl+C is pressed
   in the console, the command stops the harness (which stops the game
   engine), the feed and the model server, and prints the summary of the
   game from its record: result and reason, trade balance, game length, the
   model's choices with their median and maximum latency, fallbacks, map
   explored, and the order throughput over the match.

Ports 11600, the role's port from 41600, 5500 and the feed port must be
free; when one is taken, nothing is started. The command never touches the
live agent or its model server (11500, 41500 to 41502), any other local
model server or any process it did not start, so the live agent may keep
running while a game is watched.

**The feed page on its own.** The page follows any match record, live or
finished:

```
python -m ucla_agent.feed --latest
python -m ucla_agent.feed --match "runs/20261016/<session id>__<seat>"
```

`--latest` (the default) follows the newest match under `runs/` (or the
folder `UCLA_AGENT_RUNS_DIR` or `--runs-dir` names) and moves on to a newer
one as soon as it starts; `--match` shows one match folder. Open
http://127.0.0.1:8765/ (`--port` for another port); Ctrl+C stops the page's
server. It only reads the record, listens on 127.0.0.1 alone, loads nothing
from the internet and asks the server for news once a second. It shows:

- the header: game clock and time left, cash, trade balance, our army
  value against the enemy army value seen, harvesters, map explored, the
  plan in force and, once the match is over, the result;
- the order throughput: orders, batches and engine commands per second,
  the mean orders per batch out of the pool of 20, the share of batches
  with all 20, the mean engine commands per batch out of 200 and the
  rejected orders per second, over the last 10 s of game time and over the
  match, with a chart of the orders sent in each of the last 120 seconds;
- the model's choices, newest first, each with its options, answer,
  reason, latency and outcome; a click shows everything sent to the model
  and what came back;
- the plan timeline;
- every other event of the match, with a filter for each kind (the batches
  and their answers start hidden).

## Werewolf practice

The Werewolf practice benchmark plays our seat on a local practice table of
seven, with the platform's rules, in this process and without the platform:
it never contacts the platform or Claude. It measures how often our seat's
side wins in each role (villager, seer and wolf) and compares that with the
plain scripted player in the same seat with the same deals.

**Running a label.**

```
python scripts/werewolf_bench.py --label <label> --arm baseline|agent-script|agent-model --mix scripted|mixed|self --games-per-role N [--seed-base 1000] [--tuning <path>]
```

- The **arm** is who plays our seat: `baseline` is the practice table's
  record-voting script (`record_voter`), reading the messages our seat has
  read so far as the record voters at the table do, `agent-script` is the
  Werewolf agent with `model_choices` and `model_messages` off, and
  `agent-model` is the agent as `config/agent.toml` configures it, its
  choices and messages written by the local `strong` model.
- The **mix** is the six opponents: `scripted` is two placeholders, two
  abstainers and two record voters; `mixed` swaps the abstainers for two
  players on the local `fast` model; `self` is six copies of the Werewolf
  agent itself with `model_choices` and `model_messages` off, for
  self-play. Each copy is an agent of its own, with its own ledger and chat
  memory, reading its seat's state as the agent reads a platform state. A
  `self` table needs no model server, and what the copies would record is
  kept nowhere: the label's match records are our seat's alone.
- N games are played for each role. Game i of a role has seed
  `seed base + i` (1000 by default), forces that role on our seat and seats us
  at `seed % 7`, so two labels run with the same seed base play identical
  deals, seats and opponent seatings.
- With `--tuning <path>`, every Werewolf agent of the run (our seat's in
  `agent-script` and `agent-model`, and every `self` copy) plays the tuning
  file at that path instead of `config/werewolf/tuning.toml`. The file is
  read as strictly as the tracked one: one that cannot be read or has a
  setting out of bounds stops the run before it plays, with exit code 2 and
  the reason. `summary.json` records its path and SHA-256 under `tuning`
  (the tracked file's when the option is not given). So two labels with the
  same seed base that differ in one setting meet the same deals, without the
  tracked file being edited for the experiment.

Everything a label writes goes into `runs/practice/werewolf/<label>/` (under
the folder `UCLA_AGENT_RUNS_DIR` names, when it is set): the match records of
our seat's games in their dated folders, `results.csv` with one row per game
(`seed`, `seat`, `role`, `mix`, `arm`, `winner`, `our_side_won`, `days`,
`fallbacks`, `model_calls`, `decision_p95_ms`, `unhandled_errors`,
`seer_claim_day`, `seer_voted_out_on_claim_day`, `village_won`) and
`summary.json` with the arm, the mix, the settings of our seat, the tuning
file's path and SHA-256 (`tuning`), the model server roles started for the
run (`model_roles_started`) and each role's games, wins and unhandled
errors. The last three columns are read from the game engine once the game
is over and are never shown to a seat: `seer_claim_day` is the first day the
real seer publicly claimed the seer (matched by the agent ledger's own
seer-claim pattern; empty when it never did), `seer_voted_out_on_claim_day`
is 1 when that day's vote eliminated the real seer while no other seat had
claimed the seer, and `village_won` is 1 when the village side won. Both
files are written again after every game, so a run stopped with Ctrl+C keeps
the games it played. A game that raises is a row with winner `none`, no
claim day and an unhandled error, and the run goes on. A label that already
holds `results.csv` is refused: give each run a new label. The tool prints
one line per game, with its seconds.

**The model server.** `agent-model` and the `mixed` mix need the local model
server with the `strong` role (the role the agent's `werewolf_choice` and
`werewolf_message` chains name) and the `fast` role (the role of the model
opponents). `baseline` and `agent-script` at a `scripted` or `self` table
need no model. When a run needs a role, the benchmark first asks the model server's
endpoint (`http://127.0.0.1:11500/health`); if it already serves the role
ready, the benchmark uses it. Otherwise it starts the included model server
from `config/models.toml` itself, as `[models] autostart = true` has the
agent do but with only the roles the run needs, and stops it when the run
ends:

| Arm | `scripted` table | `mixed` table | `self` table |
|---|---|---|---|
| `baseline` | nothing | `fast` | nothing |
| `agent-script` | nothing | `fast` | nothing |
| `agent-model` | `strong` | `strong` and `fast` | `strong` |

The shipped `fast` model takes about 16 GB of memory and `strong` about
29 GB, so an `agent-model` run at a `mixed` table needs about 45 GB free. A
benchmark run never starts `small` (about 4.5 GB) or any other role its
games do not ask. The tool prints the roles it starts
(`Model server: starting only the roles the run needs: fast, strong.`), and
`summary.json` lists them as `model_roles_started`, empty when the run
started none. Loading the models takes up to a few minutes (each role is
given up to 180 seconds). If a role the run needs
is still down, nothing is played and the tool exits with code 2; each
server's own output is in `runs/model-server/<role>.log`. Do not run the
benchmark while the agent plays a match: the two would share the model
server and the GPU. An `agent-model` run is refused while `[claude] enabled`
is true.

**How long it takes.** A game with no model in it (`baseline` or
`agent-script` at a `scripted` table) takes about 0.03 seconds: a label of 3
games per role takes under a second, most of it starting Python, and one of
30 games per role (90 games) about 4 seconds. At a `self` table, where seven
agents play, such a game takes about 0.05 seconds, so a self-play label of
100 games per role (300 games) takes under half a minute. A game
with a model depends on the model: roughly one to two minutes per game with
the shipped `strong` model, more at a `mixed` table, where two opponents also
ask `fast` for each move and message. A model label of 10 games per role (30
games) takes about half an hour to an hour, after the models have loaded.

**The table and the comparison.**

```
python scripts/report.py --werewolf-bench runs/practice/werewolf/<label>
python scripts/report.py --werewolf-bench-compare runs/practice/werewolf/<label A> runs/practice/werewolf/<label B>
```

`--werewolf-bench` prints the label's arm and mix, then for each role and for
all games: `games`, `wins` (games our seat's side won), `win_rate` and
`unhandled_errors`; then, again for each role and for all games,
`seer_claimed` (the games in which the real seer claimed),
`seer_voted_out_on_claim_day` (how many of those had it voted out on its
claim day as the only claimant) and `village_wins` (the games the village
side won). A label written before those columns existed shows `-` for them.
`--werewolf-bench-compare` prints both labels' lines, then
for each role the games and wins of A and B and the two-sided Fisher exact
p-value of the two win counts (`fisher_p_two_sided`), and last `goal: met`
when B's win rate is higher than A's in every role and B had no unhandled
error, else `goal: not met`. Both print comma-separated lines and write no
file. To measure the agent against the scripted player, run both arms with
the same seed base and mix, the baseline as A:

```
python scripts/werewolf_bench.py --label baseline-mixed --arm baseline --mix mixed --games-per-role 10
python scripts/werewolf_bench.py --label agent-model-mixed --arm agent-model --mix mixed --games-per-role 10
python scripts/report.py --werewolf-bench-compare runs/practice/werewolf/baseline-mixed runs/practice/werewolf/agent-model-mixed
```

To see what one setting does to the village's handling of the seer, play the
same self-play deals with a copy of the tuning file that changes it (here
kept under `tmp/`, which is never committed) and with the tracked file, then
compare the two labels' seer-claim and village-win lines:

```
python scripts/werewolf_bench.py --label self-changed --arm agent-script --mix self --games-per-role 100 --tuning tmp/tuning-changed.toml
python scripts/werewolf_bench.py --label self-tracked --arm agent-script --mix self --games-per-role 100
python scripts/report.py --werewolf-bench-compare runs/practice/werewolf/self-changed runs/practice/werewolf/self-tracked
```

**A real platform test table.** The practice table follows the platform's
published rules; a real test table checks the agent against the platform
itself. One process plays all seven seats:

1. Check the connection (`python -m agent --check-tournament`). The seats'
   choices and messages come from the local `strong` model while
   `[models] autostart = true` lists `strong` in `autostart_roles` in
   `config/agent.toml`, as shipped (see [Settings](#settings)); with
   `autostart = false` every choice and message is the script's.
2. Start the agent for test matches and leave it running:
   `python -m agent --match`.
3. On the dashboard's Testing page, create a Werewolf test match and set
   **all 7 seats** to **Mine (self-hosted)**. The one running
   `python -m agent --match` process plays all 7 seats, each in its own worker
   process with its own decision-maker. Do not start more copies of the agent
   for the other seats.
4. When the match has finished, check its 7 match folders, one per seat,
   under `runs/<UTC yyyymmdd>/<session id>__<seat>/`:

   ```
   python scripts/report.py runs/<UTC yyyymmdd>
   ```

   `report.md` shows the 7 seats on adjacent rows, marked as the same game.
   In each folder's `metrics.json`, `termination_reason` must not be
   `unknown` (the record holds the match's result) and
   `unhandled_error_count` must be 0.

## Pokémon practice

The Pokémon practice benchmark plays our seat in local practice matches of
`pokemon_vgc_doubles_draft`, with the platform's draft, team preview and
doubles battle, the battle on a local Pokémon Showdown, in this process and
without the platform: it never contacts the platform or a hosted model
(Claude, Jev). It measures how often our seat wins against a practice
opponent, how often a decision fell back, the slowest decision of each phase
and whether any match had an unhandled error.

**Installing the Showdown helper (once per machine).** The battles run on
Pokémon Showdown under Node.js 22.18 or newer, the version
`tools/pokemon_sim/package.json` pins. Install it from the repository root:

```
cd tools/pokemon_sim
npm ci
cd ../..
```

`npm ci` installs exactly the pinned `pokemon-showdown` package into
`tools/pokemon_sim/node_modules/`, which is never committed. The agent itself
never needs Node: only the practice benchmark and the tests marked
`pokemon_sim` (`pytest -m pokemon_sim tests/practice/test_pokemon_sim.py`)
do. Without it, every match of a run raises, its line says why, and it is
recorded as an unhandled error.

**Running a label.**

```
python scripts/pokemon_bench.py --label <label> --opponent random|first|self --games N [--seed-base 5000] [--models off|on|brief|decide] [--model-port 11600] [--model-backend-port-base 41600]
```

- The **opponent** plays the other seat: `random` makes a seeded random
  legal choice in every phase, `first` takes the first legal option
  everywhere (the starter's smoke agent's rule), and `self` is the Pokémon
  agent itself with `model_choices` off. What the `self` opponent would
  record is kept nowhere: the label's match records are our seat's alone.
- Our seat is the Pokémon agent (`PokemonAgent`) in one of four arms. With
  `--models off` (the default) its model choice is off, and every draft
  pick, lineup and turn is the script's best. With `--models on`,
  `model_choices` is on and `model_briefing` and `model_decides` are off,
  and the `pokemon_choice` chain of `config/agent.toml` (the local `strong`
  model as shipped) chooses among the script's best few (`top_k`), ranked
  and scored, within each phase's deadline of `[pokemon]`, reading the
  one-line summary of the rosters or the board. With `--models brief`,
  `model_choices` and `model_briefing` are both on and `model_decides` off:
  the model chooses among the same ranked few, reading the full briefing.
  With `--models decide`, `model_choices`, `model_briefing` and
  `model_decides` are all on: the model reads the full briefing and is
  offered the script's best `decide_top_k` (6 as shipped), described without
  the script's scores and listed in a shuffled order, and writes a reason of
  at most `decide_reason_words` words before its choice. Each arm sets
  `model_briefing` and `model_decides` itself, whatever `config/agent.toml`
  holds, so `on` always measures the short context with the script's
  ranking, `brief` the briefing, and `decide` the briefing with the unranked
  shortlist.
- Match i has seed `seed base + i` (5000 by default). The seed draws the
  18-card pool, the battle's seed and the `random` opponent's choices, so two
  labels run with the same seed base draft from the same pools. Our seat
  makes the draft's first pick on an even seed and the second pick on an odd
  seed.

Everything a label writes goes into `runs/practice/pokemon/<label>/` (under
the folder `UCLA_AGENT_RUNS_DIR` names, when it is set): our seat's match
records in their dated folders, `results.csv` with one row per match and
`summary.json`. The columns of `results.csv`:

- `seed`, `opponent`, `our_won` (1 when our seat won) and `turns` (the
  battle's last turn, empty for a match that raised). `turns` is the N of
  the last `|turn|N` line Showdown sent, so a battle won during its third
  turn counts 3. Such short battles are real: each side brings 4 Pokémon at
  level 50, two of them on the board at once, and the pool's restricted
  legendaries knock out two a turn with spread moves, so most battles
  against `random` or `first` end within 3 to 6 turns;
- `decisions`: the decisions our seat made (the `action` events of its
  record);
- `fallbacks`: the model answers that were not used (late, invalid or
  failed: the record's `fallback` events), plus the parse degradations;
- `parse_degradations`: the observation keys read with their documented
  default (the `parse_degradations` of the `pokemon_decision` events), plus
  the template-only fallbacks (the `error` events of type
  `unexpected_state`);
- `decision_p95_ms`, and `max_decision_ms_draft`,
  `max_decision_ms_team_preview` and `max_decision_ms_moving`, the slowest
  decision of each phase: how long our seat took to decide (the `compute_ms`
  of its `action` events);
- `unhandled_errors`: the record's `unhandled` errors, plus one when the
  match ended early for us with a runner error (a move the rules refuse) or
  raised.

`summary.json` holds the opponent, our seat's settings (`model_choices`,
`model_briefing`, `model_decides`, `top_k`, `decide_top_k`, the deadlines and
`model_endpoint`, the run's model server, null with `--models off`), the
model server roles started for the run (`model_roles_started`), the seeds,
the seeds on which our seat drafted first (`our_first_pick_seeds`), and the
games, wins, fallbacks, parse degradations, unhandled errors and slowest
decision of each phase (`slowest_decision_ms`) so far. Both files are
written again after every match, so a run stopped with Ctrl+C keeps the
matches it played. A match that raises is a row with no turns and an
unhandled error, and the run goes on. A label that already holds
`results.csv` is refused: give each run a new label. The tool prints one
line per match, with its seconds.

**The model server.** `--models on`, `brief` and `decide` need the local
model server with the roles the `pokemon_choice` chain names: `strong` alone
as shipped (about 29 GB of memory). The run's model server has ports of its
own, never the live agent's: its endpoint is on `--model-port` (11600 by
default) and its `llama-server`s from `--model-backend-port-base` (41600 by
default, one port per role), while the live agent's server keeps the ports
of `config/models.toml` (11500, and 41500 to 41502). A port the live agent
uses is refused with exit code 2 before anything is played. The tool sets
`UCLA_AGENT_MODEL_PORT` and `UCLA_AGENT_MODEL_BACKEND_PORT_BASE` for its own
process only, so the server it starts, its health check and the agent's
model calls all use the run's ports. A model server that already serves
`strong` ready on the run's port (`http://127.0.0.1:11600/health` by
default) is used as it is; otherwise the benchmark starts the included model
server from `config/models.toml` on the run's ports with only `strong`,
prints `Model server: starting only the roles the run needs: strong.` and
stops it when the run ends. It loads its own `strong` beside any other
server's, so the machine needs the memory of both while the live agent's
server is up. `summary.json` lists the roles started as
`model_roles_started`, empty when none was. If `strong` is still down,
nothing is played and the tool exits with code 2. `--models off` needs no
model, and neither does the `self` opponent. `--models on` is refused while
the chain names a `claude:` entry with `[claude] enabled = true` or `jev`
with `[jev] enabled = true`, and so are `--models brief` and
`--models decide`. Do not run the benchmark while the agent plays a match.

**How long it takes.** With `--models off`, a match on the local Showdown
takes about 0.3 seconds, most of it starting Node: a label of 20 matches took
about 6 seconds, so one of 50 takes about 15 seconds. With `--models on`,
`brief` or `decide`, each draft pick, lineup and turn waits for one `strong`
choice, at most the phase's deadline (6 seconds for a pick; 5 seconds for a
lineup or a turn, since the local Showdown reports no clock and a decision
without one waits at most `no_clock_deadline_ms`). A `decide` answer writes
its reason before its choice, up to 160 tokens, so each choice has more to
generate than with `on` or `brief`, within the same deadlines.
A `strong` choice takes about half a second in the Werewolf benchmark and a
match has about ten to twenty decisions, so the estimate is 5 to 15 seconds
per match, about 5 to 15 minutes for a label of 50 matches after the model
has loaded (up to a few minutes). That is an estimate, not a measurement: a
model run's own times are in its `results.csv`.

**The figures and the comparison.**

```
python scripts/report.py --pokemon-bench runs/practice/pokemon/<label>
python scripts/report.py --pokemon-bench-compare runs/practice/pokemon/<label A> runs/practice/pokemon/<label B>
```

`--pokemon-bench` prints the label's opponent, then `games`, `wins`,
`win_rate`, `fallbacks`, `parse_degradations` and `unhandled_errors`, then
the slowest decision of each phase (`draft`, `team_preview`, `moving`) in
milliseconds. From our seat's match records in the label folder it then
prints `phase,median_decision_ms` with the median decision of each phase
(the nearest-rank median of the `compute_ms` of its `action` events), and
`model_answers,model_differed,model_differed_share`: the decisions a model
answered (`pokemon_decision` events whose `source` is a model, not
`script`), how many of them chose another candidate than the script's
(`chosen` is not `script_choice`), and that share (`-` when no model
answered, as with `--models off`). In a `decide` label the option ids follow
the shuffled order and `script_choice` is the id the script's best was shown
under, so the share means the same in every arm. `--pokemon-bench-compare`
prints both labels' lines, then the games and wins of A and B and the
two-sided Fisher exact p-value of the two win counts (`fisher_p_two_sided`).
Both print comma-separated lines and write no file. The agent's measured
runs, against each opponent with the model on:

```
python scripts/pokemon_bench.py --label pk-random-50 --opponent random --games 50 --seed-base 5000 --models on
python scripts/pokemon_bench.py --label pk-first-50 --opponent first --games 50 --seed-base 5000 --models on
python scripts/report.py --pokemon-bench runs/practice/pokemon/pk-random-50
python scripts/report.py --pokemon-bench runs/practice/pokemon/pk-first-50
```

They pass with at least 40 of 50 won against `random` and at least 30 of 50
against `first`, no unhandled error and no parse degradation in either, and
the slowest decision at most 6500 ms in the draft and 21000 ms at team
preview and in a battle turn. To see what the model adds, run the same
opponent and seed base with `--models off` and compare the two labels.

**A real platform test match.** The practice harness follows the platform's
published guide; a real test match checks the agent against the platform
itself. One process plays both seats:

1. Check the connection (`python -m agent --check-tournament`). Our choices
   come from the local `strong` model while `[models] autostart = true` lists
   `strong` in `autostart_roles` in `config/agent.toml`, as shipped (see
   [Settings](#settings)); with `autostart = false` every decision is the
   script's best.
2. Start the agent for test matches and leave it running:
   `python -m agent --match`.
3. On the dashboard's Testing page, create a Pokémon VGC doubles draft test
   match (`pokemon_vgc_doubles_draft`) and set **both seats** to
   **Mine (self-hosted)**. The one running `python -m agent --match` process
   plays both seats, each in its own worker process with its own
   decision-maker. Do not start a second copy of the agent for the other
   seat.
4. When the match has finished, check its 2 match folders, one per seat,
   under `runs/<UTC yyyymmdd>/<session id>__<seat>/`:

   ```
   python scripts/report.py runs/<UTC yyyymmdd>
   ```

   `report.md` shows the 2 seats on adjacent rows, marked as the same game.
   In each folder's `metrics.json`, `termination_reason` must not be
   `unknown` and `unhandled_error_count` must be 0. Neither seat's record may
   hold a template-only fallback or a parse degradation: in PowerShell,

   ```powershell
   Select-String -Path runs\<UTC yyyymmdd>\<session id>__*\events*.jsonl -Pattern '"unexpected_state"', '"parse_degradations": [1-9]'
   ```

   must print nothing.

## Hands off once a match has begun

**Once a match has begun, no person may change code, configuration, models or
prompts, or advise the agent.** This holds whether or not the change would
help. In particular, during a match nobody may:

- edit, pull, check out or otherwise change anything in the repository the
  agent runs from, including `config/` and prompt files;
- switch, restart or reconfigure a model or a decision service the agent uses;
- choose, approve, edit or replace an action or a message of the agent;
- give the agent hints, advice or information, by any route, including
  anything learned from watching the match.

What a person may do during a match is watch that the process and its
services are healthy. Reconnecting, signing in again and retrying are done by
the agent itself.

**Any manual action during a match must be written to
`runs/manual-interventions.md` with the time and the reason.** That includes
stopping or restarting the agent, restarting a model server, and anything done
to the machine that could affect play. In a tournament match, ask the
organizers before acting; the only exception is an immediate shutdown needed
for security or safety, which must be reported to them promptly. Add one entry
per action, at the time it happens:

```
## 2026-10-16 15:42 UTC
- Match: red_alert, session <session id> (tournament, Swiss round 2)
- Action: stopped the agent with Ctrl+C and restarted it with `python -m agent --tournament --match`
- Reason: the machine was about to lose power
- Authorized by: <organizer name, or "not obtained: emergency shutdown, reported at 15:50 UTC">
```

Between matches the agent may be stopped and restarted freely, but after the
submission deadline it must always run the submitted commit unchanged.
