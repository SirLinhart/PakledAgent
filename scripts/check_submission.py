"""Check that the repository is fit to be submitted: complete, disclosed and free of what must stay local.

    python scripts/check_submission.py

Run it from the repository root before the commit is pushed and its ID
submitted (see `docs/SUBMISSION.md`). It checks that:

- each of `README.md`, `docs/COMPONENTS.md`, `docs/PLAYBOOK.md`,
  `docs/SUBMISSION.md` and `docs/reference/README.md` exists, is not empty
  and is tracked;
- every repository path `docs/COMPONENTS.md` names in backticks (a token with
  a slash, such as `config/agent.toml` or `src/altruagent/`) is in the working
  tree and tracked, unless git ignores it: an ignored path is one of the
  excluded items the page describes;
- every prompt template under `src/` (a file in a folder named `prompts`) is
  named in `docs/COMPONENTS.md` by its path in backticks;
- every model role of `config/models.toml` has its section in
  `docs/MODELS.md`, giving the same model file name;
- no file git tracks is larger than 5 MiB, lies under a folder that must stay
  local (`runs/`, `tmp/`, the downloaded research and starter clone,
  `__pycache__/`, `.venv/`), or is one `.gitignore` ignores;
- `seat_plans` in `[redalert.sparring]` of `config/agent.toml` is empty;
- `scripts/scan_secrets.py` exits 0.

It changes nothing. It prints one line for each failure and exits 1, or one
line saying the check passed and exits 0.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path, PureWindowsPath
from typing import Any

from ucla_agent.core.console import print_line

EXIT_PASSED = 0
EXIT_FAILED = 1
DOCUMENTS = ("README.md", "docs/COMPONENTS.md", "docs/PLAYBOOK.md", "docs/SUBMISSION.md", "docs/reference/README.md")
COMPONENTS_PAGE = "docs/COMPONENTS.md"
MODELS_PAGE = "docs/MODELS.md"
MODELS_CONFIG = "config/models.toml"
AGENT_CONFIG = "config/agent.toml"
SECRET_SCAN = "scripts/scan_secrets.py"
SOURCE_FOLDER = "src"
PROMPTS_FOLDER_NAME = "prompts"
BYTES_PER_MIB = 1024 * 1024
MAX_TRACKED_MIB = 5
MAX_TRACKED_BYTES = MAX_TRACKED_MIB * BYTES_PER_MIB
# Folders that hold secrets, downloads, records or tool state and must never be tracked. As in `.gitignore`, a
# folder named without a slash before its end matches a folder of that name at any depth.
LOCAL_ONLY_FOLDERS = (
    "runs/",
    "tmp/",
    "docs/reference/research/",
    "docs/reference/altruagent-starter/",
    "__pycache__/",
    ".venv/",
)
BACKTICKED = re.compile(r"`([^`]+)`")
# One name of a repository path. A token with anything else in it (a scheme, a drive, a placeholder, a space) names
# something other than a path in the repository.
PATH_NAME = re.compile(r"[\w.-]+")
# Names that lead out of the place a path is read from rather than into the repository.
RELATIVE_NAMES = (".", "..")
# A model role's section of the models page begins with its heading and ends at the next heading of level 1 to 3.
ROLE_SECTION = r"^### `{role}`$(?P<body>.*?)(?=^#{{1,3}} |\Z)"
MODEL_FILE_ROW = re.compile(r"^\| Model file \| `(?P<file>[^`]+)` \|", re.MULTILINE)
GITLINK_MODE = "160000"
CHECK_IGNORE_IGNORED = 0
CHECK_IGNORE_NOT_IGNORED = 1
SCAN_CLEAN = 0
SCAN_FOUND = 1
_TEXT_ENCODING = "utf-8"


class GitError(Exception):
    """Git could not answer a question about the repository; the message says which and why."""


def _git(root: Path, *arguments: str, stdin: str | None = None) -> str:
    """The output of one git command run in `root`. Raises `GitError` when it fails."""
    try:
        completed = subprocess.run(
            ["git", *arguments], cwd=root, input=stdin, capture_output=True, text=True, encoding=_TEXT_ENCODING
        )
    except OSError as exc:
        raise GitError(f"git cannot be run: {exc}") from exc
    if completed.returncode != 0:
        raise GitError(f"git {arguments[0]} failed in {root}: {' '.join(completed.stderr.split())}")
    return completed.stdout


def tracked_sizes(root: Path) -> dict[str, int]:
    """Every path git tracks (`git ls-files`), with the size in bytes of the content staged for it."""
    entries = [entry for entry in _git(root, "ls-files", "-s", "-z").split("\0") if entry]
    blobs = {}
    for entry in entries:
        metadata, path = entry.split("\t", 1)
        mode, object_name, _stage = metadata.split(" ")
        # A submodule's commit is not an object of this repository and has no size here.
        if mode != GITLINK_MODE:
            blobs[path] = object_name
    if not blobs:
        return {}
    answers = _git(root, "cat-file", "--batch-check=%(objectsize)", stdin="\n".join(blobs.values()) + "\n")
    return dict(zip(blobs, (int(size) for size in answers.split())))


def document_failures(root: Path, tracked: set[str]) -> list[str]:
    """A line for each required document that is missing, empty or not tracked."""
    failures = []
    for document in DOCUMENTS:
        path = root / document
        if not path.is_file():
            failures.append(f"{document}: missing")
        elif path.stat().st_size == 0:
            failures.append(f"{document}: empty")
        elif document not in tracked:
            failures.append(f"{document}: not tracked by git")
    return failures


def named_paths(page: str) -> list[str]:
    """The repository paths a page names in backticks, each once, in the order they first appear."""
    return list(dict.fromkeys(token for token in BACKTICKED.findall(page) if _is_repository_path(token)))


def _is_repository_path(token: str) -> bool:
    """Whether a token is a relative path inside the repository with at least one slash, such as `config/`."""
    names = token.removesuffix("/").split("/")
    is_plain = all(PATH_NAME.fullmatch(name) and name not in RELATIVE_NAMES for name in names)
    return "/" in token and is_plain


def named_path_failures(root: Path, page: str, tracked: set[str]) -> list[str]:
    """A line for each path the components page names that is neither in the repository nor ignored by git."""
    tracked_folders = {folder for path in tracked for folder in _ancestors(path)}
    failures = []
    for named in named_paths(page):
        path = named.rstrip("/")
        in_tree = (root / path).exists()
        if (in_tree and (path in tracked or path in tracked_folders)) or _is_ignored(root, named):
            continue
        problem = "which git does not track" if in_tree else "which is not in the repository"
        failures.append(f"{COMPONENTS_PAGE} names {named}, {problem}")
    return failures


def _ancestors(path: str) -> list[str]:
    """Every folder a repository path lies in, outermost first: `a/b/c.py` lies in `a` and `a/b`."""
    parts = path.split("/")
    return ["/".join(parts[:depth]) for depth in range(1, len(parts))]


def _is_ignored(root: Path, path: str) -> bool:
    """Whether git's ignore rules match a path, which need not exist."""
    completed = subprocess.run(["git", "check-ignore", "--quiet", "--", path], cwd=root, capture_output=True)
    if completed.returncode not in (CHECK_IGNORE_IGNORED, CHECK_IGNORE_NOT_IGNORED):
        raise GitError(f"git check-ignore failed for {path}: {completed.stderr.decode(_TEXT_ENCODING, 'replace')}")
    return completed.returncode == CHECK_IGNORE_IGNORED


def prompt_templates(root: Path) -> list[str]:
    """Every prompt template under `src/`: each file in a folder named `prompts`, as a repository path."""
    files = (path for path in (root / SOURCE_FOLDER).rglob("*") if path.is_file())
    return sorted(
        path.relative_to(root).as_posix() for path in files if PROMPTS_FOLDER_NAME in path.relative_to(root).parts[:-1]
    )


def prompt_failures(root: Path, page: str) -> list[str]:
    """A line for each prompt template the components page does not name by its path."""
    named = set(BACKTICKED.findall(page))
    return [
        f"{template}: prompt template not named in {COMPONENTS_PAGE}"
        for template in prompt_templates(root)
        if template not in named
    ]


def model_role_failures(root: Path) -> list[str]:
    """A line for each model role of the models configuration that the models page lacks or names another file for."""
    try:
        roles = _toml(root / MODELS_CONFIG).get("roles", {})
        page = (root / MODELS_PAGE).read_text(encoding=_TEXT_ENCODING)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"model roles cannot be checked: {exc}"]
    return [failure for role, settings in roles.items() if (failure := _role_failure(role, settings, page))]


def _role_failure(role: str, settings: Any, page: str) -> str | None:
    """The line for one model role, or None when the models page gives its section with the same model file."""
    model_path = settings.get("model_path") if isinstance(settings, dict) else None
    if not isinstance(model_path, str):
        return f"{MODELS_CONFIG}: model role `{role}` has no model_path"
    model_file = PureWindowsPath(model_path).name
    section = re.search(ROLE_SECTION.format(role=re.escape(role)), page, re.MULTILINE | re.DOTALL)
    if section is None:
        return f"{MODELS_CONFIG}: model role `{role}` has no section in {MODELS_PAGE}"
    documented = MODEL_FILE_ROW.search(section["body"])
    if documented is None or documented["file"] != model_file:
        shown = documented["file"] if documented else "no model file"
        return f"{MODELS_CONFIG}: model role `{role}` uses {model_file}, but {MODELS_PAGE} gives {shown}"
    return None


def tracked_file_failures(root: Path, sizes: dict[str, int]) -> list[str]:
    """A line for each tracked file that is too large, under a local-only folder, or ignored by `.gitignore`."""
    failures = [
        f"{path}: tracked file of {size:,} bytes, over the {MAX_TRACKED_MIB} MiB limit"
        for path, size in sizes.items()
        if size > MAX_TRACKED_BYTES
    ]
    failures += [
        f"{path}: tracked file under {folder}, which must stay local"
        for path in sizes
        if (folder := _local_only_folder(path)) is not None
    ]
    ignored = _git(root, "ls-files", "-z", "--cached", "--ignored", "--exclude-standard").split("\0")
    failures += [f"{path}: tracked file that .gitignore ignores" for path in ignored if path]
    return failures


def _local_only_folder(path: str) -> str | None:
    """The local-only folder a tracked path lies under, or None."""
    folders_of_path = path.split("/")[:-1]
    for folder in LOCAL_ONLY_FOLDERS:
        name = folder.rstrip("/")
        is_nested = "/" in name
        if (is_nested and path.startswith(folder)) or (not is_nested and name in folders_of_path):
            return folder
    return None


def sparring_failures(root: Path) -> list[str]:
    """A line when the agent configuration pins a sparring plan to a seat, or cannot be read."""
    try:
        redalert = _toml(root / AGENT_CONFIG).get("redalert", {})
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"{AGENT_CONFIG} cannot be read: {exc}"]
    seat_plans = redalert.get("sparring", {}).get("seat_plans", {})
    if not seat_plans:
        return []
    return [f"{AGENT_CONFIG}: [redalert.sparring] seat_plans is not empty ({seat_plans}); it must be empty to submit"]


def secret_scan_failures(root: Path) -> list[str]:
    """A line for each hit of the repository's secret scan, or one line when the scan could not run."""
    # The scan imports the agent's package; it is given the repository's own source, so the check works with or
    # without the editable install.
    python_path = os.pathsep.join(filter(None, [str(root / SOURCE_FOLDER), os.environ.get("PYTHONPATH")]))
    environment = {**os.environ, "PYTHONPATH": python_path, "PYTHONIOENCODING": _TEXT_ENCODING}
    completed = subprocess.run(
        [sys.executable, str(root / SECRET_SCAN)],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding=_TEXT_ENCODING,
        errors="replace",
    )
    hits = [line for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode == SCAN_CLEAN:
        return []
    if completed.returncode == SCAN_FOUND and hits:
        return [f"{SECRET_SCAN} found a secret at {hit}" for hit in hits]
    error = completed.stderr.strip().splitlines()
    return [f"{SECRET_SCAN} failed with exit code {completed.returncode}: {error[-1] if error else 'no message'}"]


def _toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def check(root: Path) -> list[str]:
    """Every failure of the repository at `root`, one line each; an empty list when it is fit to submit."""
    try:
        sizes = tracked_sizes(root)
    except GitError as exc:
        return [f"The files git tracks cannot be listed: {exc}"]
    tracked = set(sizes)
    failures = document_failures(root, tracked)
    page_path = root / COMPONENTS_PAGE
    # A missing components page is already reported; what it should name cannot be checked without it.
    if page_path.is_file():
        page = page_path.read_text(encoding=_TEXT_ENCODING)
        failures += named_path_failures(root, page, tracked) + prompt_failures(root, page)
    failures += model_role_failures(root)
    failures += tracked_file_failures(root, sizes)
    failures += sparring_failures(root)
    failures += secret_scan_failures(root)
    return failures


def main(arguments: list[str]) -> int:
    """Check the repository in the current directory, print each failure and return the exit code."""
    parser = argparse.ArgumentParser(prog="python scripts/check_submission.py", description=__doc__.split("\n\n")[0])
    parser.parse_args(arguments)
    try:
        failures = check(Path.cwd())
    except GitError as exc:
        failures = [str(exc)]
    for failure in failures:
        print_line(failure)
    if failures:
        print_line(f"Submission check failed: {len(failures)} problem(s).")
        return EXIT_FAILED
    print_line("Submission check passed.")
    return EXIT_PASSED


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
