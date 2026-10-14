# Score probe: three controlled observations of the `military` figures

At the time limit a Red Alert match is decided by the tuple
`(kills_cost - deaths_cost, assets_value)` from each seat's latest
`military` values (`docs/reference/altruagent/redalert.md`, section 7). The
agent's clock play rests on what those figures count. This procedure checks
three facts about them in one self-play test match, by reading what the
platform recorded. It is a written procedure, not code; a person carries it
out with their own dashboard access.

Rules for the probe:

- Nothing is sold, destroyed on purpose or exploited to test the scoring. The
  agent plays its normal game; the probe only reads the record afterwards.
- No behavior of the agent is built on a probe result. The answers only
  confirm or correct the agent's understanding of the documented tiebreak.
- Any behavior that differs from the platform's documentation, or that looks
  like a scoring bug, is reported to the organizers and is not used.

## Setup

1. Check that `python -m ucla_agent.check` exits 0 with the key set.
2. Pin the two seats to different plans, so that the match has fighting in
   it: in `config/agent.toml`, set
   `[redalert.sparring] seat_plans = {Multi0 = "PRESS", Multi1 = "HOLD"}`.
   Commit nothing with the pins in place: set `seat_plans = {}` again after
   the match (the submission check refuses a pinned configuration).
3. On the Testing page create one test match and set **both** seats to
   **Mine (self-hosted)**. Start `python -m agent --match` and leave the
   match alone until it ends.
4. The two seats' records are `runs/<UTC yyyymmdd>/<session id>__Multi0/`
   and `runs/<UTC yyyymmdd>/<session id>__Multi1/`. Print each seat's
   summary:

   ```
   python scripts/live_summary.py runs/<UTC yyyymmdd>/<session id>__Multi0
   python scripts/live_summary.py runs/<UTC yyyymmdd>/<session id>__Multi1
   ```

5. List every change of a seat's `military` values, with the game tick, from
   its `state` events (every recorded state holds `military`, whether it was
   written in full or thinned):

   ```
   python -c "import json,sys; last=None; [print(e['data']['state_version'], m) for e in map(json.loads, open(sys.argv[1], encoding='utf-8')) if e['kind']=='state' and (m:=(e['data'].get('state') or {}).get('observation', e['data']).get('military')) and m!=last and not (last:=m) is None]" runs/<UTC yyyymmdd>/<session id>__Multi0/events.jsonl
   ```

   Item costs are in the match's `game_config` event (the first event of
   each record) under `rules`, and in `config/redalert/items.toml`.

## Observation 1: does unspent cash change `assets_value`?

- **What to read:** in the first states of either seat, before anything is
  built, the seat holds its starting cash and one MCV. Note `assets_value`
  and the `cash` of the same state's `economy`. Then find the first state
  after the first building was placed: `cash` has dropped by about the
  building's cost.
- **Expected:** `assets_value` equals the value of the units and buildings
  standing (the MCV's cost at the start), not the cash. It rises by the
  building's cost when the building stands, while the cash it was paid from
  is not counted before or after.
- **Answer** (does unspent cash count in `assets_value`? yes / no, with the
  figures read):
- **Match folder:**
- **Date:**

## Observation 2: does losing a unit raise `deaths_cost` by its full cost?

- **What to read:** in the seat that loses units first, find the first state
  in which `units_lost` rises by one. Compare `deaths_cost` in that state with
  the state before it. The unit's type is in the `unit_lost` event of the
  observation's `events` (in the state written in full at that moment), and
  its cost in the `game_config` rules.
- **Expected:** `deaths_cost` rises by exactly the full cost of the lost
  unit's type, whatever health the unit had left before it died.
- **Answer** (rise in `deaths_cost`, the unit's type and its cost):
- **Match folder:**
- **Date:**

## Observation 3: does destroying an enemy building raise `kills_cost` by its cost?

- **What to read:** in the seat that destroys a building first, find the
  first state in which `buildings_killed` rises by one. Compare `kills_cost`
  with the state before it. The building's type is in the other seat's
  record: the `building_lost` event at the same tick in its `events`.
- **Expected:** `kills_cost` rises by exactly the cost of the destroyed
  building's type, and the other seat's `deaths_cost` by the same amount.
- **Answer** (rise in `kills_cost`, the building's type and its cost, and the
  other seat's rise in `deaths_cost`):
- **Match folder:**
- **Date:**

## After the probe

Record the three answers here and in item 7 of
[docs/LIVE-VALIDATION.md](../docs/LIVE-VALIDATION.md). An answer that differs
from the expected one is reported to the organizers, with the match folder
and the figures read, and nothing in the agent is changed to take advantage
of it.
