"""Where the agent's files live.

The agent's configuration (`config/`), its match records (`runs/`) and its
lock file are all found under the project root. The root is, in this order:

1. the folder named by the environment variable `UCLA_AGENT_ROOT`, when it is
   set (a test, or a harness that keeps a run apart, names its own);
2. the project the `ucla_agent` package was loaded from: the nearest folder
   above the package that holds `config/agent.toml`;
3. the current directory, for a package installed apart from any project.

The second rule is the one that normally applies. It makes a worker process,
or a harness started from another folder, read the tracked configuration and
write its records into the project, and `python -m agent` started from the
project root finds the same folder as before.

The records alone can be sent elsewhere: when the environment variable
`UCLA_AGENT_RUNS_DIR` is set, they are written under the folder it names in
place of `runs/`, while the configuration is still read from the project
root. A local benchmark sets it per game, so that each game's records sit
next to that game's harness output.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT_ENV = "UCLA_AGENT_ROOT"
RUNS_DIR_ENV = "UCLA_AGENT_RUNS_DIR"
# The file whose presence marks a folder as the agent's project root.
PROJECT_MARKER = Path("config") / "agent.toml"
# The folder of the `ucla_agent` package itself.
PACKAGE_DIR = Path(__file__).resolve().parents[1]


def repository_root() -> Path:
    """The project root: the folder `UCLA_AGENT_ROOT` names, else the package's project, else the current directory."""
    named = os.environ.get(ROOT_ENV)
    if named:
        return Path(named).absolute()
    project = project_holding(PACKAGE_DIR)
    return project if project is not None else Path.cwd()


def runs_folder(default: Path) -> Path:
    """The folder the records are written under: the one `UCLA_AGENT_RUNS_DIR` names when it is set, else `default`.

    An empty value counts as not set; a relative one is taken from the current directory.
    """
    named = os.environ.get(RUNS_DIR_ENV)
    return Path(named).absolute() if named else default


def project_holding(package_dir: Path) -> Path | None:
    """The nearest folder above a package folder that holds `config/agent.toml`; None when no folder above does."""
    return next((folder for folder in package_dir.parents if (folder / PROJECT_MARKER).is_file()), None)
