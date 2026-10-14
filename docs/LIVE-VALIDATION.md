# Live validation: the checklist for declaring Red Alert `ready`

Red Alert stays `in progress` in [GAMES-STATUS.md](GAMES-STATUS.md), and the
agent is not registered for the Red Alert tournament, until every item below
is done and its result written down here. The items need real test matches on
the AltruAgent platform, so a person with dashboard access carries them out;
the offline test suite and the fake game do not count for any of them.

Fill in the empty fields as each item is done. Every item names the match
folder (`runs/<UTC yyyymmdd>/<session id>__<seat>`) and the date, so that a
reader can find the evidence in the match records.

The rules of [RUNNING.md](RUNNING.md) hold for every match here: once a match
has begun nobody touches the agent, its configuration, its models or its
machine, and any manual action (item 5 is one) is logged in
`runs/manual-interventions.md`.

Tools used below, all run from the repository root:

| Command | What it gives |
|---|---|
| `python -m ucla_agent.check` | The readiness check: one line for each of its eight checks, then the games that are ready. |
| `pytest -m live tests/live/test_platform_check.py` | The readiness check's platform step against the real platform, with the key from `.env`. |
| `python -m agent --match` | Plays the test matches created on the Testing page, both seats of a self-play match in one process. |
| `python scripts/live_summary.py <match folder>` | One seat's map, factions, result, `military` figures, latencies, refused batches, duplicate production orders, rejected and lost orders, plans and errors. |
| `python scripts/report.py runs/<UTC yyyymmdd>` | `metrics.json` for every match folder of the day, and `report.md` over all of them. |
| `python scripts/capture_rules.py <match folder>` | The platform's game reference of a match, stored as `config/redalert/captured/rules-<yyyymmdd>.json`. |
| `python scripts/diff_rules.py <captured file>` | Every item whose cost, power, prerequisites or hit points differ from `config/redalert/items.toml`. |

## 1. The readiness check passes with the key set

With `ALTRUAGENT_OFFICIAL_AGENT_KEY` in `.env`, run:

```
python -m ucla_agent.check
pytest -m live tests/live/test_platform_check.py
```

The check must exit 0 with the line `PASS platform` (not `WARN platform: no
key set`), and its last line must include `red_alert`. The live test must
pass, not be skipped. Write down every line that is `WARN` and why.

- Exit code of the check:
- `WARN` lines and why:
- Live platform test (passed / failed):
- Commit (`git rev-parse HEAD`):
- Match folder: none, this item plays no match
- Date:

## 2. Six self-play test matches over all three maps and both families

Create each match on the Testing page with **both** seats set to **Mine
(self-hosted)**, and play all six with one `python -m agent --match` process
(see "Self-play" in [RUNNING.md](RUNNING.md)). Before the matches, give the two
seats different plans in `config/agent.toml`, for example
`[redalert.sparring] seat_plans = {Multi0 = "RAID", Multi1 = "HOLD"}`, and
swap them halfway; set `seat_plans = {}` again when the six are done (the
submission check refuses a pinned configuration).

The six matches together must cover each pool map, `sidestep`, `trapped` and
`shadowfiend-ii`, twice, and each family (Allies, Soviets) must have been
played at least twice. The platform picks each side's faction at random, so
read the factions from `live_summary.py` and play more matches if a family is
short. Every seat must reach a result (`Result:` other than `no_result`) and
have no `error` event of type `unhandled` (the `Errors:` lines of the
summary).

| # | Map | `seat_plans` | Multi0 folder | Multi0 faction | Multi0 result | Multi1 folder | Multi1 faction | Multi1 result | Unhandled errors | Date |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | | | | | | | | | | |
| 2 | | | | | | | | | | |
| 3 | | | | | | | | | | |
| 4 | | | | | | | | | | |
| 5 | | | | | | | | | | |
| 6 | | | | | | | | | | |

- Each map twice (yes / no):
- Each family at least twice (Allies: , Soviets: ):
- Date:

## 3. Clean orders and fast decisions in every match of item 2

For each match and seat of item 2, run `python scripts/live_summary.py` on the
seat's folder and read:

- `Batches refused whole, by error`: no `INVALID_ACTION`;
- `duplicate_production_orders`: 0;
- `compute_ms`: p95 at most 50;
- `round_trip_ms` and `cycle_latency_ms`: their p50 and p95, recorded below.

The provisional target for `cycle_latency_ms` p95 is 400 ms. Once the twelve
seats are measured, revise it here to what the platform actually allows, and
say why.

| # | Seat | INVALID_ACTION | Duplicate production orders | compute_ms p95 | round_trip_ms p50 | round_trip_ms p95 | cycle_latency_ms p50 | cycle_latency_ms p95 |
|---|---|---|---|---|---|---|---|---|
| 1 | Multi0 | | | | | | | |
| 1 | Multi1 | | | | | | | |
| 2 | Multi0 | | | | | | | |
| 2 | Multi1 | | | | | | | |
| 3 | Multi0 | | | | | | | |
| 3 | Multi1 | | | | | | | |
| 4 | Multi0 | | | | | | | |
| 4 | Multi1 | | | | | | | |
| 5 | Multi0 | | | | | | | |
| 5 | Multi1 | | | | | | | |
| 6 | Multi0 | | | | | | | |
| 6 | Multi1 | | | | | | | |

- `cycle_latency_ms` p95 target after measuring (provisional: 400), and why:
- Date:

## 4. One match with every decision source unavailable

Set `[redalert].model_plan_choice = false` and `[redalert].model_commander =
false` in `config/agent.toml` and play one
test match without the model server: `[models].autostart = false`, and no
`llama-server` running. The scripted core plays alone. The match must reach a
result with no `unhandled` error.

- Match folder(s):
- Result:
- Unhandled errors:
- Date:

## 5. One match killed and restarted mid-game

Start a test match with `python -m agent --match`. Several minutes into the
match, kill the agent process without letting it stop cleanly (end the
`python` process in Task Manager, or `Stop-Process -Id <pid> -Force` with the
process id from `.agent.lock`), log the action in
`runs/manual-interventions.md`, and start `python -m agent --match` again. The
new process resumes the seat after up to about 35 seconds. The seat's folder
then holds `events.jsonl` and `events.2.jsonl`; `python scripts/report.py` on
it gives `attempts: 2`. The match must reach a result.

- Match folder:
- Game time at which the agent was killed:
- `attempts` in `metrics.json`:
- Result:
- Date:

## 6. The platform's rules captured and compared

From one match of item 2:

```
python scripts/capture_rules.py <match folder>
python scripts/diff_rules.py config/redalert/captured/rules-<yyyymmdd>.json
```

Commit the captured file, which is the reference for this platform version.
Read every line `diff_rules.py` prints and correct `config/redalert/items.toml`
where it differs from the platform: the platform's values are the right ones.
Then run `pytest` and `python -m ucla_agent.check --offline` again.

- Captured file:
- Differences found:
- Commit that updated `items.toml` (or "none needed"):
- Date:

## 7. The score probe's three answers

Carry out [scripts/score_probe.md](../scripts/score_probe.md) in one
self-play test match and copy its three answers here.

- Does unspent cash change `assets_value`?
- Does losing a unit raise `deaths_cost` by its full cost?
- Does destroying an enemy building raise `kills_cost` by its cost?
- Match folders:
- Date:

## Declaring Red Alert `ready`

When all seven items are filled in and met, a person sets Red Alert's status
to `ready` in [GAMES-STATUS.md](GAMES-STATUS.md) in the same commit as this
filled-in checklist. Only then is the agent registered for the Red Alert
tournament.

- Commit that set Red Alert to `ready`:
- Date:
