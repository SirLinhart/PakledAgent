# UCLA Agent Tournament agent

A self-hosted agent for the AltruAgent AI Agent Gaming Tournament (the UCLA
Agent Tournament). It is one long-running Python process: it signs in to the
platform with an Official Agent Key, waits for the games the platform assigns
to it, test matches and tournament games alike, and plays each one to its end
in a worker process of its own, without anyone touching it.

The process is the official starter kit's runtime (vendored, unchanged, in
`src/altruagent/`) with this repository's agent plugged into it. The agent
hands each match to the decision-maker of its game. Play is scripted: every
move comes from code that answers each state at once. Local language models,
run by an included model server, or a hosted decision service can be allowed
to make a few short choices. As shipped, JevK5-9B (the `jev9b` role)
chooses Red Alert's plan and commands one unit group under the script's
checks, and the `strong` role makes Werewolf's and Pokémon's choices; the
script plays its own choice whenever no answer comes in time.

## Games

| Game | Platform game type | Status | Played by |
|---|---|---|---|
| Red Alert | `red_alert` | `in progress` | The Red Alert scripted core: opening with early defense, base upkeep, base defense, scouting, combat, raids, plan rules and clock play, with the plan chosen among the open ones and the first scout, then main, commanded by the local JevK5-9B model (`jev9b`) among the script's checked options when the model server runs |
| Werewolf | `werewolf` | `ready` | The Werewolf role plans: a fact ledger and role inference, each role's votes, kills and investigations, and chat, with model choices and messages from the local `strong` model when the model server runs |
| Pokémon VGC doubles draft | `pokemon_vgc_doubles_draft` | `ready` | The Pokémon policies: a scored draft that remembers both rosters, the best 4 of 6 and leads at team preview, and each doubles turn scored by a damage estimate with Fake Out, Protect, speed control and switches, with model picks among the script's best from the local `strong` model when the model server runs |
| Honor of Kings | `honor_of_kings` | `not started` | Nothing yet (the platform does not offer it yet) |

[docs/GAMES-STATUS.md](docs/GAMES-STATUS.md) keeps each game's status and the
criteria for `ready`. Register the agent for a game's tournament only when that
page lists the game as `ready`. A match of a game that has no decision-maker
is answered with legal moves so that it cannot stop the agent, but it is not
played to win.

## Setup from a fresh clone

Requirements: Python 3.11 or newer and git. Nothing else is needed to play
with the shipped settings.

Windows (PowerShell):

```powershell
git clone <repository URL>
cd <repository folder>
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
Copy-Item .env.example .env
```

macOS or Linux:

```bash
git clone <repository URL>
cd <repository folder>
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Then edit `.env`:

- `ALTRUAGENT_CONTROL_URL`: `https://api.altruagent-game.com`.
- `ALTRUAGENT_OFFICIAL_AGENT_KEY`: the Official Agent Key from the tournament
  dashboard's Agent Configuration page (the agent must be set to Self-hosted
  there). It is shown once.
- `JEV_API_KEY`: only if the Jev decision service is turned on in
  `config/agent.toml`; leave it empty otherwise.

`.env` is never committed. Keys go nowhere else.

**Model files.** Only needed for the roles the included local model server
starts (`autostart = true` and `autostart_roles` in the `[models]` table of
`config/agent.toml`; as shipped it starts the `strong` and `jev9b` roles,
about 30 GB and about 23 GB of VRAM, about 53 GB together). Download
those roles' model files and the `llama-server` program as
[docs/MODELS.md](docs/MODELS.md) describes, and put them at the paths
`config/models.toml` names. Without them the role is down and the agent plays
on its scripted choices. The model server never downloads anything itself.

**Red Alert's decision maker.** Red Alert ships with JevK5-9B (`jev9b`)
as plan chooser and commander: `model_plan_choice = true` and
`model_commander = true` in the `[redalert]` table of `config/agent.toml`,
with its `red_alert_plan`, `red_alert_scout` and `red_alert_commander` chains
`["systemone:jev9b"]`. The script checks every option the model is offered and
plays its own choice when no answer comes in time or the 9B is down. For
script-only Red Alert, set `model_plan_choice` and `model_commander` to
`false` (and drop `jev9b` from `autostart_roles` to free its VRAM). The 4B
model (`small`) is defined in `config/models.toml` but not used as shipped.

## Check before playing

All commands are run from the repository root with the environment active.

| Command | What it checks |
|---|---|
| `pytest` | The offline test suite: no key, no network, no model. Every test must pass. |
| `python -m ucla_agent.check` | The readiness check: configuration, secret scan, platform, games, model server, Jev, records and the scripted fallback, one `PASS`, `WARN`, `FAIL` or `SKIP` line each, then `Games ready:` and the games that are ready. It exits 0 when no line is `FAIL`. `--offline` skips the three checks that reach a service (platform, model server, Jev). Plays nothing and changes nothing on the platform. |
| `python -m agent --check-tournament` | The platform connection alone: the control plane is reachable, the key is accepted, assignments can be listed and the agent can be created. Plays nothing. |
| `python scripts/scan_secrets.py` | No tracked file holds a key. |
| `python scripts/check_submission.py` | The repository is complete for submission (see [docs/SUBMISSION.md](docs/SUBMISSION.md)). |

## Start the agent

```
python -m agent --match                 # test matches (the dashboard's Testing page)
python -m agent --tournament            # tournament games
python -m agent --tournament --match    # both, in one process: use this on tournament day
```

Leave it running; Ctrl+C stops it. Only one copy may run on a machine (a
second one exits with code 3). Once a match has begun, nobody may change the
code, the configuration, the models or the prompts, or advise the agent, and
any manual action must be logged.
[docs/RUNNING.md](docs/RUNNING.md) has the full procedure for a test session
and for tournament day, self-play, restarts and the hands-off rules.

## Records and reports

Each run writes its records under `runs/`, which git ignores: one line when the
run starts and stops (with the git commit and the hash of every settings
file), and for each seat of each match its events and fixed facts. The report
tool turns them into figures:

```
python scripts/report.py runs/<UTC yyyymmdd>
```

## Repository layout

| Path | What is there |
|---|---|
| `src/altruagent/` | The official starter kit's runtime, vendored byte for byte (`src/altruagent/UPSTREAM.md` names the upstream commit). |
| `src/agent/` | The entry point `python -m agent` and the agent factory the runtime loads. |
| `src/ucla_agent/core/` | What every game shares: dispatch, configuration, records, secrets, the included model server and the decision sources. |
| `src/ucla_agent/games/redalert/` | The Red Alert scripted core and its one prompt template. |
| `config/` | Every setting that affects play. Tracked; the agent only reads it. |
| `scripts/` | Tools a person runs: the report, the secret scan, the submission check, rule capture and comparison, reference refresh, fixture generators. |
| `tests/` | The offline test suite. |
| `docs/` | The documents below, our research notes and the reference copies of the platform's documentation and rules. |

## Documents

- [docs/RUNNING.md](docs/RUNNING.md): running the agent, step by step.
- [docs/GAMES-STATUS.md](docs/GAMES-STATUS.md): which games are ready.
- [docs/LIVE-VALIDATION.md](docs/LIVE-VALIDATION.md): the checklist of real
  test matches that has to be completed before Red Alert is `ready`.
- [docs/COMPONENTS.md](docs/COMPONENTS.md): every component that affects
  play, its settings, and what is excluded from the repository and why.
- [docs/PLAYBOOK.md](docs/PLAYBOOK.md): the strategy, for every game and for
  Red Alert in detail, with its known weaknesses.
- [docs/MODELS.md](docs/MODELS.md): the local models, where to get them and
  every setting.
- [docs/SUBMISSION.md](docs/SUBMISSION.md): the submission checklist and the
  freeze.
- [docs/reference/README.md](docs/reference/README.md): the reference copies
  of the platform's documentation and the official rules.
