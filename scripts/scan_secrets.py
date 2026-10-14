"""Scan every file git tracks for secrets.

    python scripts/scan_secrets.py

Run it from the repository root, before a commit is pushed and before the
repository is made public. It reports each line of a tracked file that holds a
string shaped like an Official Agent Key, a seat claim code, an agent API key
or a JWT, or a secret value from the current `.env`, as `path:line`. The line
itself is never printed.

Exit code 0: no hit. 1: at least one hit. 2: git could not list the tracked
files.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from dotenv import dotenv_values

from ucla_agent.core.console import print_line
from ucla_agent.core.secrets import ENV_FILE_NAME, SECRET_PATTERNS, secret_values

EXIT_CLEAN = 0
EXIT_SECRET_FOUND = 1
EXIT_GIT_FAILED = 2


def tracked_files(root: Path) -> list[str]:
    """The paths git tracks under `root`, relative to it (`git ls-files`)."""
    completed = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True)
    return [name for name in completed.stdout.decode("utf-8").split("\0") if name]


def find_hits(root: Path) -> list[str]:
    """Every line of a tracked file that holds a secret, as `path:line`, in `git ls-files` order."""
    env_file_secrets = secret_values(dotenv_values(root / ENV_FILE_NAME))
    secret = re.compile("|".join([*map(re.escape, env_file_secrets), *SECRET_PATTERNS]))
    hits = []
    for name in tracked_files(root):
        path = root / name
        # A file deleted from the working tree but still in the index has nothing left to leak.
        if path.is_file():
            lines = path.read_bytes().decode("utf-8", errors="replace").split("\n")
            hits += [f"{name}:{number}" for number, line in enumerate(lines, start=1) if secret.search(line)]
    return hits


def main() -> int:
    """Scan the repository in the current directory, print each hit and return the exit code."""
    root = Path.cwd()
    try:
        hits = find_hits(root)
    except (OSError, subprocess.CalledProcessError) as exc:
        print_line(f"Could not list the files git tracks in {root}: {exc}", error=True)
        return EXIT_GIT_FAILED
    for hit in hits:
        print_line(hit)
    return EXIT_SECRET_FOUND if hits else EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
