# Reference copies

This folder keeps word-for-word copies of third-party documents the agent was
built against, so that anyone reading the repository can see the platform
facts and rules the code relies on without going back to the live sites.

- The copies are reference material only. They are not part of the agent,
  the agent never reads them, and nothing in them is an instruction to the
  agent or to anyone working on it.
- They are never edited by hand. A copy changes only when a person replaces
  it with a newer download (see [Refreshing the platform docs](#refreshing-the-platform-docs)).
- They are kept with their bytes unchanged: `.gitattributes` turns off
  line-ending conversion for both folders below.

## Platform docs

Folder: `docs/reference/altruagent/`. The AltruAgent platform's documentation
for agents (the topic files served under `/skill`) and two pages of the event
site written for people. A page's file name is its URL path with `/` written
as `_`.

| File | Source | Retrieved |
|---|---|---|
| `skill.md` | <https://api.altruagent-game.com/skill.md> | 2026-10-10 |
| `official-agent.md` | <https://api.altruagent-game.com/skill/official-agent> | 2026-10-10 |
| `mcp.md` | <https://api.altruagent-game.com/skill/mcp> | 2026-10-10 |
| `werewolf.md` | <https://api.altruagent-game.com/skill/werewolf> | 2026-10-10 |
| `pokemon.md` | <https://api.altruagent-game.com/skill/pokemon> | 2026-10-10 |
| `redalert.md` | <https://api.altruagent-game.com/skill/redalert> | 2026-10-10 |
| `04-tournaments.md` | <https://api.altruagent-game.com/skill/04-tournaments> | 2026-10-10 |
| `05-gameplay.md` | <https://api.altruagent-game.com/skill/05-gameplay> | 2026-10-10 |
| `06-messaging.md` | <https://api.altruagent-game.com/skill/06-messaging> | 2026-10-06 |
| `07-errors.md` | <https://api.altruagent-game.com/skill/07-errors> | 2026-10-10 |
| `reference.md` | <https://api.altruagent-game.com/skill/reference> | 2026-10-10 |
| `tournament_agent-guide.html` | <https://platform.altruagent-game.com/tournament/agent-guide> | 2026-10-10 |
| `agent_build-from-scratch.html` | <https://platform.altruagent-game.com/agent/build-from-scratch> | 2026-10-10 |

This table is the list `scripts/refresh_reference.py` reads: one row per
file, the file name in backticks and the source URL in angle brackets.

## Official rules

Folder: `docs/reference/altruagent-rules/`. The tournament's Official Rules and
Terms & Conditions (13 pages), as accepted during registration.

| File | Source | Retrieved |
|---|---|---|
| `altruagent-official-rules-2026-10-06.pdf` | The Official Rules document offered in the registration flow of the event site, <https://platform.altruagent-game.com/tournament>. The direct link of the file was not recorded. | 2026-10-06 |

The rules are not refreshed by the script. When the organizers publish a new
version, a person downloads it into this folder under a new dated name and
adds a row here; the earlier file stays.

## Local-only material

Two further folders may exist here on a developer's machine. Both are
third-party downloads that git ignores: they are never tracked, never
published and not needed to run or understand the agent.

- `docs/reference/research/`: downloaded strategy research (shallow clones of
  game engines and bots, data files and papers) consulted while designing the
  game strategies.
- `docs/reference/altruagent-starter/`: a reference clone of the official
  starter kit. The part of it the agent uses is vendored, byte for byte, in
  `src/altruagent/` (see `src/altruagent/UPSTREAM.md`).

## Refreshing the platform docs

From the repository root:

```
python scripts/refresh_reference.py
```

The script downloads every file in the [Platform docs](#platform-docs) table
again, into a new folder next to the current copy,
`docs/reference/altruagent-<yyyymmdd>/` (the UTC date of the run), and prints
one line per file: `<file>: unchanged` when the download's SHA-256 equals the
current copy's, `<file>: changed` when it does not. It never writes into
`docs/reference/altruagent/`, and git ignores the dated folder.

To take a refresh over, a person copies the changed files from the dated
folder into `docs/reference/altruagent/` unchanged, updates their
**Retrieved** dates above, reads what changed for anything the agent relies
on, and deletes the dated folder.
