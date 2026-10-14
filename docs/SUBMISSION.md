# Submission

The checklist a person follows to submit the agent. **Everything here must be
done before 11:59 p.m. Pacific on 2026-10-13**, the submission deadline and
agent lock of the official rules. Leave time: the full test run alone takes
several minutes.

Every step is done by a person. No script in this repository pushes, publishes
or submits anything.

All commands are run from the repository root, in the environment set up as in
[README.md](../README.md#setup-from-a-fresh-clone).

## 1. Set the git identity (once, before the first push)

The commits so far were made under a local identity. Before anything is
pushed, set the identity of the GitHub account the repository will be
published under, and rewrite the existing commits to it:

```
git config user.name "<your name>"
git config user.email "<id>+<username>@users.noreply.github.com"
git rebase --root --exec "git commit --amend --no-edit --reset-author"
```

The e-mail is the account's no-reply address, shown on GitHub under Settings,
Emails. The rebase gives every commit that author and committer (and sets
each author date to the time of the rewrite) and changes every commit ID, so
it is done once, with a clean working tree, before the first push and before
any commit ID is recorded. Never rewrite history once anything is pushed.

Check that only the new identity remains, as author and as committer:

```
git shortlog -sne HEAD
git shortlog -sne --committer HEAD
```

## 2. Check the code

1. Run the test suite. Every test must pass:

   ```
   pytest
   ```

2. Run the readiness check. Its last line names the games that are ready, and
   no line may start with `FAIL`:

   ```
   python -m ucla_agent.check
   ```

   It prints one line for each of its eight checks (configuration, secret
   scan, platform, games, model server, Jev, records, scripted fallback) and
   exits 0 when none is `FAIL`. A `WARN` is a decision source the agent can
   play without, or a missing key; read it and decide. Run it with the key
   set, so that the platform line is a real `PASS`.
3. Scan every tracked file for secrets. It must print nothing and exit 0:

   ```
   python scripts/scan_secrets.py
   ```

4. Check that the repository is complete for submission (documents, the
   component disclosure, model roles, file sizes, ignored folders, the secret
   scan and the sparring pins). It must exit 0; otherwise it prints each
   problem:

   ```
   python scripts/check_submission.py
   ```

## 3. Check the settings

Open `config/agent.toml` and confirm:

- `seat_plans` in `[redalert.sparring]` is empty (`seat_plans = {}`). A pin is
  for sparring between two copies of the agent and must not be in the
  submitted commit. `python scripts/check_submission.py` fails while it is
  not empty.
- `enabled` in `[jev]`, and `model_plan_choice` and `model_commands` in
  `[redalert]`, are set as intended for the tournament. As shipped `enabled`
  and `model_commands` are `false`, and `model_plan_choice` and
  `model_commander` are `true`: no hosted service takes part in play, and the
  local JevK5-9B model (`jev9b`) chooses Red Alert's plan and commands under
  the script's checks. For script-only Red Alert set `model_plan_choice` and
  `model_commander` to `false`. Turn a service on only if test matches have
  shown it helps, and only with its key (`.env`) or model files in place on
  the machine that will play.
- `autostart` in `[models]` agrees with that choice: `true` only if a local
  model is to be asked. As shipped it is `true` with `autostart_roles = ["strong",
  "jev9b"]`, about 53 GB of VRAM together.

Then confirm that `docs/COMPONENTS.md` still describes what is shipped,
including the section on the in-memory wrap of
`altruagent.runner._terminal_game_state` (The one attachment to a non-public
part of the runtime), and that each setting changed above is reflected there.

## 4. Freeze and push

1. Confirm the working tree is clean. `git status` must say
   `nothing to commit, working tree clean`: commit what belongs to the agent,
   and delete or have git ignore anything else. Nothing local may be needed
   to run the agent beyond what [README.md](../README.md) lists.
2. Create a public repository on GitHub, empty (no README, license or
   `.gitignore`), and add it as the remote:

   ```
   git remote add origin https://github.com/<username>/<repository>.git
   ```

3. Push the default branch. The branch worked on is the one
   `git branch --show-current` prints; pushed first to an empty repository,
   it becomes the default branch. Confirm on GitHub (Settings, General,
   Default branch) that it is:

   ```
   git push -u origin <branch>
   ```

4. Record the submitted commit ID, the full 40 characters:

   ```
   git rev-parse HEAD
   ```

   It must be the latest commit on the default branch on GitHub:
   `git ls-remote origin <branch>` prints the same ID. Enter the repository URL
   and this commit ID on the tournament dashboard where the registration asks
   for them.
5. **Make no further pushes.** If something must still change before the
   deadline, make the change, repeat sections 2 to 4, and enter the new commit
   ID on the dashboard before the deadline: the ID on file must always be the
   latest commit on the default branch.

## After the deadline

From the deadline until 30 days after the final results are announced:

- **No pushes, no history rewrites, no edits** to the repository, and the
  repository stays public. The only exceptions are an update the organizers
  authorize in writing, and removing exposed material such as a leaked key
  (remove it, tell the organizers promptly, and change nothing about how the
  agent plays).
- **Practice may continue only on the frozen commit.** Every test match and
  every tournament game is played by the submitted commit, unchanged, with
  the submitted configuration. Before starting the agent, `git rev-parse HEAD`
  must print the submitted ID and `git status` must be clean. Trying out a
  change, even in a test match, is not allowed.
- Keep the records the audit rules ask for: the whole `runs/` folder (each
  run records the commit and the hash of every file under `config/`, and each
  match its events and models) and `runs/manual-interventions.md`. See
  [RUNNING.md](RUNNING.md).
