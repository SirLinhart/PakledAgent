"""Watch one real-time Red Alert game played by the committed agent, with the live feed page.

    python scripts/watch_game.py [--faction england|france|germany|russia|ukraine|random]
                                 [--opponent bot:normal|bot:rush|...] [--map sidestep|trapped|shadowfiend-ii]
                                 [--model none|small|fast|strong|jev9b] [--set <file>:<section>.<key>=<value> ...]
                                 [--harness <path>] [--feed-port 8765]

Run it from the repository root. It plays the code of HEAD, never the
working tree: the commit is exported into `tmp/watch/<UTC stamp>/snap`, and
the game, its records and its configuration all come from that snapshot.

The settings apply to the snapshot alone, and the working tree's files are
never written. `--model` turns the model's plan choice on with that role
of `config/models.toml`, and has the scouts' chain and the model
commander's chain ask the same role, so that the model commander, on as
`config/agent.toml` ships it, commands the first scout and then main with
that role, and `--set agent:redalert.model_scout_choice=true` has it pick
the scouts' destinations too. `--model jev9b` does the same with the native
systemone role jev9b (JevK5-9B), whose chains are `systemone:jev9b`: the
model server starts it on its own watch port, and the game asks it there.
`--model none` turns both the plan choice and
the model commander off, so the script plays alone with no model server;
each `--set` changes one key of
`config/agent.toml` (`agent:`) or `config/redalert/tuning.toml` (`tuning:`),
for example `--set tuning:raids.standing=true`. The snapshot's model server
listens on 11600 with its roles from 41600, apart from the live agent's 11500
and 41500-41502. The snapshot's own loaders then read its settings, and a key
they do not know or a value they refuse stops the command before anything
starts.

The local Red Alert harness lives outside the repository: its folder is
given with `--harness` or the `UCLA_REDALERT_HARNESS` environment variable,
and is never written into a tracked file. The game's output goes to the
harness's `runs/watch/<UTC stamp>`, with the agent's records in its
`records` folder and the setup printed at the start saved next to them as
`setup.json`.

Then it starts, each from the snapshot, the model role (unless `--model
none`) on 127.0.0.1:11600, the harness's real-time game in the observer
window (`play --watch --clock realtime --games 1 --port 5500`), and the feed
page on the records folder, which it opens in the default browser. When the
game ends, the window is closed or Ctrl+C is pressed, it stops every process
it started and nothing else, and prints the summary of the game from its
record. It never touches the live agent, its model server (11500, 41500 to
41502), other local model servers or any process it did not start.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import socket
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, NamedTuple

REPOSITORY = Path(__file__).resolve().parents[1]
# The script runs from a checkout without the editable install as well: the working tree's packages are put on the
# import path, so that one command is all a watched game takes.
if str(REPOSITORY / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY / "src"))

from ucla_agent.core.console import print_line  # noqa: E402 - importable only once src/ is on the path
from ucla_agent.core.secrets import redact  # noqa: E402 - importable only once src/ is on the path
from ucla_agent.feed.summary import summary_lines  # noqa: E402 - importable only once src/ is on the path

FACTIONS = ("england", "france", "germany", "russia", "ukraine", "random")
MAPS = ("sidestep", "trapped", "shadowfiend-ii")
MODELS = ("none", "small", "fast", "strong", "jev9b")
# The roles of MODELS that are native systemone servers, asked as "systemone:<role>" rather than "local:<role>".
SYSTEMONE_MODELS = ("jev9b",)
LOCAL_ENTRY_PREFIX = "local:"
SYSTEMONE_ENTRY_PREFIX = "systemone:"
DEFAULT_FACTION = "random"
DEFAULT_OPPONENT = "bot:normal"
DEFAULT_MAP = "sidestep"
DEFAULT_MODEL = "small"
DEFAULT_FEED_PORT = 8765
WATCH_FOLDER = Path("tmp") / "watch"
SNAPSHOT_FOLDER_NAME = "snap"
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
EXIT_PLAYED = 0
EXIT_NOT_STARTED = 1
# The files a --set may change, by the name it gives them.
SETTING_FILES = {"agent": Path("config") / "agent.toml", "tuning": Path("config") / "redalert" / "tuning.toml"}
MODELS_FILE = Path("config") / "models.toml"
# The snapshot's model server: its endpoint and the first of its roles' ports, apart from the live agent's.
WATCH_MODEL_PORT = 11600
WATCH_BACKEND_PORT_BASE = 41600
NO_MODEL = "none"
PLAN_CHAIN = "red_alert_plan"
# The chain asked where the designated scouts go next, which asks the same role as the plan chain.
SCOUT_CHAIN = "red_alert_scout"
# The chain the model commander asks, which asks the same role as the plan chain.
COMMANDER_CHAIN = "red_alert_commander"
# The files the snapshot's loaders read before anything starts, by their paths as `ucla_agent.check.local` names them.
CHECKED_FILES = ("config/agent.toml", "config/redalert/tuning.toml", "config/models.toml")
ROOT_ENV = "UCLA_AGENT_ROOT"
RUNS_DIR_ENV = "UCLA_AGENT_RUNS_DIR"
CONFIG_CHECK_TIMEOUT_SECONDS = 120
# The local Red Alert harness lives outside the repository; its folder is named on the command line or here.
HARNESS_ENV = "UCLA_REDALERT_HARNESS"
HARNESS_PYTHON = Path(".venv") / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
# Where in the harness folder a watched game's output goes, and the agent's records inside that.
HARNESS_WATCH_FOLDER = Path("runs") / "watch"
RECORDS_FOLDER_NAME = "records"
SETUP_FILE_NAME = "setup.json"
LOOPBACK = "127.0.0.1"
_JSON_INDENT = 2
# The harness's own port for the game, apart from the 5310 and 5320 of benchmark runs.
HARNESS_PORT = 5500
MODELS_ROUTE = "/v1/models"
# A model is read into memory when its role starts: the strong role's takes minutes the first time.
MODEL_READY_SECONDS = 600
FEED_READY_SECONDS = 30
PORT_PROBE_SECONDS = 0.5
URL_PROBE_SECONDS = 2.0
POLL_SECONDS = 0.5
# After Ctrl+C the harness stops its game engine itself; it gets this long before it is ended.
HARNESS_STOP_SECONDS = 60
STOP_SECONDS = 10
# Run in a child process with the snapshot's packages: its model server, for as long as the process lives. The
# server's llama-servers end with the process however it ends (`ucla_agent.core.modelserver.children`).
_MODEL_SERVER = """
import sys
import time
from ucla_agent.core import modelserver
modelserver.start([sys.argv[1]])
try:
    while True:
        time.sleep(1)
finally:
    modelserver.stop()
"""
_SETTING = re.compile(r"(?P<file>[a-z]+):(?P<path>[A-Za-z0-9_.-]+)=(?P<value>.*)", re.DOTALL)
_TABLE_HEADER = re.compile(r"\s*\[([A-Za-z0-9_.-]+)\]\s*(?:#.*)?")
_FILE_ENCODING = "utf-8"
# Run in a child process with the snapshot's packages: its own loaders read its settings as its agent will.
_CONFIG_CHECK = """
import sys
from pathlib import Path
from ucla_agent.check.local import CONFIG_READERS
from ucla_agent.core.config import ConfigError
for name in sys.argv[2:]:
    try:
        CONFIG_READERS[name](Path(sys.argv[1]))
    except ConfigError as error:
        print(error, file=sys.stderr)
        sys.exit(1)
"""


class WatchError(Exception):
    """Something keeps the watched game from going on; the message says what and how to fix it."""


class Setting(NamedTuple):
    """One key of one settings file of the snapshot and its new value, written as TOML."""

    file: str
    table: str
    key: str
    value: str

    def __str__(self) -> str:
        return f"{self.file}:{self.table}.{self.key}={self.value}"


class Harness(NamedTuple):
    """The local Red Alert harness: its folder, and the python of its own environment."""

    folder: Path
    python: Path


class Setup(NamedTuple):
    """Everything a watched game is played with, as it is printed at the start and saved beside its records."""

    stamp: str
    commit: str
    faction: str
    map: str
    opponent: str
    model: str
    settings: list[str]
    snapshot: Path
    harness: Path
    out: Path
    records: Path
    model_endpoint: str | None
    feed: str


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """The command line, with the defaults of a watched game: random faction, bot:normal, sidestep, model small."""
    parser = argparse.ArgumentParser(prog="python scripts/watch_game.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--faction", choices=FACTIONS, default=DEFAULT_FACTION)
    parser.add_argument(
        "--opponent", default=DEFAULT_OPPONENT, help="bot:<type> or agent:<module> (default bot:normal)"
    )
    parser.add_argument("--map", choices=MAPS, default=DEFAULT_MAP)
    parser.add_argument("--model", choices=MODELS, default=DEFAULT_MODEL, help="the role that chooses the plan")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="FILE:SECTION.KEY=VALUE",
        help="change one key of the snapshot's config/agent.toml (agent:) or config/redalert/tuning.toml (tuning:)",
    )
    parser.add_argument("--harness", help="the harness folder (default: the UCLA_REDALERT_HARNESS variable)")
    parser.add_argument("--feed-port", type=int, default=DEFAULT_FEED_PORT, help="the feed page's port on 127.0.0.1")
    return parser.parse_args(argv)


def utc_stamp(moment: datetime | None = None) -> str:
    """The name a watched game's folders get: the UTC time it was started, to the second."""
    return (moment or datetime.now(timezone.utc)).strftime(STAMP_FORMAT)


def take_snapshot(repository: Path, folder: Path) -> str:
    """Export HEAD's tree into `folder`, which must not exist yet, and return the commit it is of.

    The files come out as `git archive` writes them, with the line endings
    a checkout would give them. Raises `WatchError` with git's message when
    git cannot name or export the commit.
    """
    commit = _git(repository, "rev-parse", "HEAD").decode("ascii").strip()
    archive = _git(repository, "archive", "--format=tar", commit)
    folder.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
        tree.extractall(folder, filter="data")
    return commit


def _git(repository: Path, *arguments: str) -> bytes:
    completed = subprocess.run(["git", "-C", str(repository), *arguments], capture_output=True)
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise WatchError(f"git {arguments[0]} failed in {repository}: {message}")
    return completed.stdout


def parse_setting(text: str) -> Setting:
    """A `--set` argument: `<file>:<section>.<key>=<value>`, the file `agent` or `tuning`.

    The value is taken as TOML when it is one (`true`, `12`, `0.5`,
    `["local:fast"]`, `"text"`), and as a string otherwise. Raises
    `WatchError` for an argument of another form.
    """
    found = _SETTING.fullmatch(text.strip())
    if found is None or found["file"] not in SETTING_FILES or "." not in found["path"]:
        raise WatchError(
            f"--set {text} is not <file>:<section>.<key>=<value> with the file {' or '.join(SETTING_FILES)}, "
            "for example --set tuning:raids.standing=true"
        )
    table, key = found["path"].rsplit(".", 1)
    return Setting(found["file"], table, key, _toml_value(found["value"]))


def _toml_value(text: str) -> str:
    try:
        tomllib.loads(f"value = {text}")
    except tomllib.TOMLDecodeError:
        return json.dumps(text)
    return text.strip()


def model_settings(model: str) -> list[Setting]:
    """The settings of `config/agent.toml` that have the plan chosen by a model role, or by the script for none; the
    scouts' chain and the commander's ask the same role, for when their switches are on. None also turns the model
    commander off, so that no part of the game asks a model when no model server runs."""
    if model == NO_MODEL:
        return [
            Setting("agent", "redalert", "model_plan_choice", "false"),
            Setting("agent", "redalert", "model_commander", "false"),
        ]
    prefix = SYSTEMONE_ENTRY_PREFIX if model in SYSTEMONE_MODELS else LOCAL_ENTRY_PREFIX
    role = json.dumps([f"{prefix}{model}"])
    return [
        Setting("agent", "redalert", "model_plan_choice", "true"),
        Setting("agent", "decide.chains", PLAN_CHAIN, role),
        Setting("agent", "decide.chains", SCOUT_CHAIN, role),
        Setting("agent", "decide.chains", COMMANDER_CHAIN, role),
        Setting("agent", "models", "autostart_roles", json.dumps([model])),
    ]


def apply_settings(snapshot: Path, settings: list[Setting]) -> None:
    """Write the settings into the snapshot's files, and give its model server the watch ports."""
    for setting in settings:
        set_key(snapshot / SETTING_FILES[setting.file], setting.table, setting.key, setting.value)
    set_key(snapshot / MODELS_FILE, "server", "port", str(WATCH_MODEL_PORT))
    set_key(snapshot / MODELS_FILE, "server", "backend_port_base", str(WATCH_BACKEND_PORT_BASE))


def set_key(path: Path, table: str, key: str, value: str) -> None:
    """Write `key = value` into the table `[table]` of a TOML file, keeping its comments and line endings.

    The key's line is replaced when the table has one; otherwise the key is
    added after the table's last line, and a table the file lacks is added at
    its end. Raises `WatchError`, and leaves the file as it was, when the
    edit would not be valid TOML (a table the file defines inline, for
    instance).
    """
    raw = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    lines = raw.decode(_FILE_ENCODING).splitlines()
    line = f"{key} = {value}"
    header = next((index for index, text in enumerate(lines) if _table_of(text) == table), None)
    if header is None:
        lines += ["", f"[{table}]", line]
    else:
        end = next((index for index in range(header + 1, len(lines)) if _table_of(lines[index])), len(lines))
        own_line = re.compile(rf"\s*{re.escape(key)}\s*=")
        found = next((index for index in range(header + 1, end) if own_line.match(lines[index])), None)
        if found is not None:
            lines[found] = line
        else:
            last = max(index for index in range(header, end) if lines[index].strip())
            lines.insert(last + 1, line)
    text = newline.join(lines) + newline
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise WatchError(f"{path} would no longer be valid TOML with {table}.{key} = {value}: {error}") from error
    path.write_bytes(text.encode(_FILE_ENCODING))


def _table_of(line: str) -> str | None:
    """The table a header line opens (`[decide.chains]` opens `decide.chains`); None for any other line."""
    found = _TABLE_HEADER.fullmatch(line)
    return found[1] if found else None


def snapshot_environment(snapshot: Path) -> dict[str, str]:
    """The environment of a process run from the snapshot: its packages, and it as the project root."""
    environment = {**os.environ, "PYTHONPATH": str(snapshot / "src"), ROOT_ENV: str(snapshot)}
    environment.pop(RUNS_DIR_ENV, None)
    return environment


def check_snapshot_config(snapshot: Path) -> None:
    """Have the snapshot's own loaders read its settings; raises `WatchError` with their message when they refuse."""
    completed = subprocess.run(
        [sys.executable, "-c", _CONFIG_CHECK, str(snapshot), *CHECKED_FILES],
        env=snapshot_environment(snapshot),
        capture_output=True,
        text=True,
        timeout=CONFIG_CHECK_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise WatchError(f"The snapshot's settings do not load, so nothing was started: {completed.stderr.strip()}")


def prepare_snapshot(arguments: argparse.Namespace, folder: Path) -> tuple[str, list[Setting]]:
    """Take the snapshot and apply the arguments' settings to it, checked; gives the commit and the settings."""
    settings = model_settings(arguments.model) + [parse_setting(text) for text in arguments.set]
    commit = take_snapshot(REPOSITORY, folder)
    apply_settings(folder, settings)
    check_snapshot_config(folder)
    return commit, settings


def find_harness(given: str | None, environment: dict[str, str]) -> Harness:
    """The harness folder `--harness` names, else the one `UCLA_REDALERT_HARNESS` names, with its python.

    Raises `WatchError` naming both ways to give it when neither does, or
    when the folder or the python of its environment is not there.
    """
    named = given or environment.get(HARNESS_ENV)
    if not named:
        raise WatchError(
            f"No harness folder: give it with --harness <folder> or set the {HARNESS_ENV} environment variable "
            "to the folder of the local Red Alert harness."
        )
    folder = Path(named).absolute()
    python = folder / HARNESS_PYTHON
    if not python.is_file():
        raise WatchError(
            f"{folder} holds no harness python at {python}: check --harness or {HARNESS_ENV}, "
            "and that the harness's environment was made (.venv)."
        )
    return Harness(folder, python)


def make_setup(
    arguments: argparse.Namespace, stamp: str, commit: str, settings: list[Setting], snapshot: Path, harness: Path
) -> Setup:
    """The setup of a watched game: its options, the folders it uses and the addresses of its model and its feed."""
    out = harness / HARNESS_WATCH_FOLDER / stamp
    return Setup(
        stamp=stamp,
        commit=commit,
        faction=arguments.faction,
        map=arguments.map,
        opponent=arguments.opponent,
        model=arguments.model,
        settings=[str(setting) for setting in settings],
        snapshot=snapshot,
        harness=harness,
        out=out,
        records=out / RECORDS_FOLDER_NAME,
        model_endpoint=None if arguments.model == NO_MODEL else f"http://{LOOPBACK}:{WATCH_MODEL_PORT}",
        feed=f"http://{LOOPBACK}:{arguments.feed_port}/",
    )


def setup_lines(setup: Setup) -> list[str]:
    """The setup as it is printed: one line for each part of it."""
    model = "none, the script chooses the plan" if setup.model_endpoint is None else setup.model
    return [
        f"Watched game {setup.stamp}",
        f"  commit:        {setup.commit}",
        f"  faction:       {setup.faction}",
        f"  map:           {setup.map}",
        f"  opponent:      {setup.opponent}",
        f"  model:         {model}",
        *[f"  setting:       {setting}" for setting in setup.settings],
        f"  snapshot:      {setup.snapshot}",
        f"  harness:       {setup.harness}",
        f"  output:        {setup.out}",
        f"  records:       {setup.records}",
        f"  model server:  {setup.model_endpoint or 'not started'}",
        f"  feed page:     {setup.feed}",
    ]


def save_setup(setup: Setup) -> Path:
    """Write the setup as setup.json into the game's output folder, next to its records; gives the file's path."""
    setup.out.mkdir(parents=True, exist_ok=True)
    fields = {name: str(value) if isinstance(value, Path) else value for name, value in setup._asdict().items()}
    path = setup.out / SETUP_FILE_NAME
    path.write_bytes(redact(json.dumps(fields, indent=_JSON_INDENT) + "\n").encode(_FILE_ENCODING))
    return path


def start_process(command: list[str], environment: dict[str, str], folder: Path | None) -> Any:
    """Start one child process in the same console, so that Ctrl+C reaches it as it reaches this command."""
    return subprocess.Popen(command, env=environment, cwd=folder)


def open_browser(url: str) -> None:
    """Open the page in the default browser."""
    webbrowser.open(url)


def port_in_use(port: int) -> bool:
    """Whether something answers on this port of 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(PORT_PROBE_SECONDS)
        return probe.connect_ex((LOOPBACK, port)) == 0


def wait_until_answering(url: str, process: Any, seconds: float) -> bool:
    """Wait until a URL answers, for at most `seconds`; False when it did not. Raises `WatchError` when the process
    that should answer it ends first."""
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise WatchError(f"The process that should answer {url} ended with exit code {process.returncode}.")
        try:
            with direct.open(url, timeout=URL_PROBE_SECONDS):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(POLL_SECONDS)
    return False


class Processes:
    """The processes this command started, and only those: they are stopped newest first, and nothing else is."""

    def __init__(self) -> None:
        self._started: list[tuple[str, Any]] = []

    def start(self, name: str, command: list[str], environment: dict[str, str], folder: Path | None = None) -> Any:
        """Start a process and remember it under a name."""
        process = start_process(command, environment, folder)
        self._started.append((name, process))
        return process

    @property
    def names(self) -> list[str]:
        """The names of the processes started, oldest first."""
        return [name for name, _process in self._started]

    def stop_all(self, grace: dict[str, float]) -> None:
        """Stop every process started that still runs, newest first.

        A process named in `grace` is first given that many seconds to end by
        itself (the harness, stopping its engine after Ctrl+C); a second Ctrl+C
        cuts the wait short. Any process still running is then ended.
        """
        for name, process in reversed(self._started):
            try:
                _wait_for_exit(process, grace.get(name, 0))
            except KeyboardInterrupt:
                print_line(f"Ctrl+C again: ending the {name} at once.")
            _end(process)


def _wait_for_exit(process: Any, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)


def _end(process: Any) -> None:
    """End a process that still runs: asked first, then forced."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=STOP_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=STOP_SECONDS)


def backend_port(snapshot: Path, role: str) -> int:
    """The port of a role's server in the snapshot: the watch base plus the role's place in its models.toml, where
    the systemone roles are counted after the llama roles."""
    tables = tomllib.loads((snapshot / MODELS_FILE).read_text(encoding=_FILE_ENCODING))
    roles = [*tables.get("roles", {}), *tables.get("systemone_roles", {})]
    return WATCH_BACKEND_PORT_BASE + roles.index(role)


def ports_needed(setup: Setup) -> list[int]:
    """The ports of 127.0.0.1 the watched game takes: the model server's and its role's, the harness's, the feed's."""
    model = [] if setup.model_endpoint is None else [WATCH_MODEL_PORT, backend_port(setup.snapshot, setup.model)]
    return [*model, HARNESS_PORT, int(setup.feed.rstrip("/").rsplit(":", 1)[1])]


def check_ports_free(setup: Setup) -> None:
    """Raise `WatchError` before anything starts when a port the game needs is taken."""
    taken = [port for port in ports_needed(setup) if port_in_use(port)]
    if taken:
        raise WatchError(
            f"Port {', '.join(map(str, taken))} of {LOOPBACK} is in use, so nothing was started: "
            "stop what holds it (an earlier watched game, for instance) or choose another --feed-port."
        )


def harness_command(setup: Setup, python: Path) -> list[str]:
    """The harness's command for the watched game: one real-time game in the observer window, the snapshot's agent."""
    return [
        str(python),
        "-m",
        "openra_harness",
        "play",
        "--agent-src",
        str(setup.snapshot / "src"),
        "--port",
        str(HARNESS_PORT),
        "--map",
        setup.map,
        "--faction",
        setup.faction,
        "--opponent",
        setup.opponent,
        "--clock",
        "realtime",
        "--games",
        "1",
        "--watch",
        "--out",
        str(setup.out),
    ]


def harness_environment(setup: Setup) -> dict[str, str]:
    """The harness's environment: the snapshot as the agent's project root, and the game's records folder."""
    environment = {**os.environ, ROOT_ENV: str(setup.snapshot), RUNS_DIR_ENV: str(setup.records)}
    # The agent is imported from --agent-src alone, never from a source folder this shell happens to name.
    environment.pop("PYTHONPATH", None)
    return environment


def feed_command(setup: Setup) -> list[str]:
    """The feed's command: the newest match under the game's records folder, on the feed port."""
    port = setup.feed.rstrip("/").rsplit(":", 1)[1]
    return [sys.executable, "-m", "ucla_agent.feed", "--latest", "--runs-dir", str(setup.records), "--port", port]


def feed_environment(setup: Setup) -> dict[str, str]:
    """The feed runs the snapshot's code; it redacts with the secrets of the working tree's .env."""
    return {**snapshot_environment(setup.snapshot), ROOT_ENV: str(REPOSITORY)}


def play(setup: Setup, harness: Harness, processes: Processes) -> None:
    """Start the model role, the harness game and the feed, and wait for the game to end or for Ctrl+C.

    Whatever happens, every process started is stopped before this returns.
    """
    interrupted = False
    try:
        if setup.model_endpoint is not None:
            print_line(f"Starting the {setup.model} role of the snapshot's model server.")
            server = processes.start(
                "model server", [sys.executable, "-c", _MODEL_SERVER, setup.model], snapshot_environment(setup.snapshot)
            )
            if not wait_until_answering(f"{setup.model_endpoint}{MODELS_ROUTE}", server, MODEL_READY_SECONDS):
                raise WatchError(f"The model server did not answer within {MODEL_READY_SECONDS} s.")
        print_line("Starting the game in the observer window.")
        game = processes.start(
            "harness", harness_command(setup, harness.python), harness_environment(setup), harness.folder
        )
        feed = processes.start("feed", feed_command(setup), feed_environment(setup))
        _open_feed(setup, feed)
        _wait_for_exit(game, float("inf"))
        print_line(f"The game is over (the harness exited with code {game.returncode}).")
    except KeyboardInterrupt:
        interrupted = True
        print_line("Ctrl+C: stopping the game and everything this command started.")
    finally:
        processes.stop_all({"harness": HARNESS_STOP_SECONDS if interrupted else 0})
        print_line(f"Stopped everything this command started: {', '.join(reversed(processes.names)) or 'nothing'}.")


def _open_feed(setup: Setup, feed: Any) -> None:
    """Open the feed page once it answers; the game goes on without it when it does not."""
    try:
        answered = wait_until_answering(setup.feed, feed, FEED_READY_SECONDS)
    except WatchError as error:
        print_line(f"The feed stopped ({error}); the game goes on without it.", error=True)
        return
    if answered:
        open_browser(setup.feed)
    else:
        print_line(f"The feed did not answer within {FEED_READY_SECONDS} s; open {setup.feed} yourself.")


def print_summary(records: Path) -> None:
    """Print the summary of the game recorded under its records folder, every figure read from the record."""
    print_line("Summary of the watched game:")
    for line in summary_lines(records):
        print_line(f"  {line}")


def main(argv: list[str] | None = None) -> int:
    """Play one watched game and print its summary; exit code 1 when it could not be started."""
    arguments = parse_arguments(argv)
    stamp = utc_stamp()
    snapshot = REPOSITORY / WATCH_FOLDER / stamp / SNAPSHOT_FOLDER_NAME
    try:
        harness = find_harness(arguments.harness, dict(os.environ))
        commit, settings = prepare_snapshot(arguments, snapshot)
        setup = make_setup(arguments, stamp, commit, settings, snapshot, harness.folder)
        check_ports_free(setup)
        for line in setup_lines(setup):
            print_line(line)
        print_line(f"  setup saved:   {save_setup(setup)}")
        play(setup, harness, Processes())
    except WatchError as error:
        print_line(str(error), error=True)
        return EXIT_NOT_STARTED
    print_summary(setup.records)
    return EXIT_PLAYED


if __name__ == "__main__":
    sys.exit(main())
