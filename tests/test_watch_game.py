"""scripts/watch_game.py: a watched Red Alert game from a snapshot of HEAD, with the harness, model and browser stubbed."""

from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GIT_IDENTITY = ("-c", "user.name=watch-test", "-c", "user.email=watch-test@example.invalid")


def _git(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(["git", "-C", str(repository), *arguments], capture_output=True, check=True).stdout


def _normalized(content: bytes) -> bytes:
    """File content with the line endings a checkout may have given it put back to those git stores."""
    return content.replace(b"\r\n", b"\n")


@pytest.fixture(scope="module")
def watch(load_script) -> ModuleType:
    return load_script("watch_game.py")


@pytest.fixture(scope="module")
def head_snapshot(watch: ModuleType, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    """A snapshot of this repository's HEAD, taken once for the module, and the commit it is of."""
    folder = tmp_path_factory.mktemp("watch") / "snap"
    return folder, watch.take_snapshot(REPO_ROOT, folder)


SMALL_CONFIG = {
    "config/agent.toml": b'[models]\nautostart = true\nautostart_roles = ["strong"]\n\n'
    b'[decide.chains]\nred_alert_plan = ["local:fast"]\n\n[redalert]\nmodel_plan_choice = false\n',
    "config/models.toml": b'[server]\nhost = "127.0.0.1"\nport = 11500\nbackend_port_base = 41500\n\n'
    b"[roles.fast]\n\n[roles.strong]\n\n[roles.small]\n",
    "config/redalert/tuning.toml": b"[raids]\nstanding = false\n",
}


@pytest.fixture
def small_repository(tmp_path: Path) -> Path:
    """A repository of its own with one commit: a tracked note and the three settings files a snapshot changes."""
    repository = tmp_path / "repository"
    for name, content in {"notes.txt": b"committed\n", **SMALL_CONFIG}.items():
        (repository / name).parent.mkdir(parents=True, exist_ok=True)
        (repository / name).write_bytes(content)
    _git(repository, "init", "-q")
    _git(repository, "add", "notes.txt", *SMALL_CONFIG)
    _git(repository, *GIT_IDENTITY, "commit", "-q", "-m", "one commit")
    return repository


@pytest.fixture
def snapshot_copy(head_snapshot: tuple[Path, str], tmp_path: Path) -> Path:
    """A copy of the module's snapshot of HEAD (its packages and its settings) for a test to change."""
    folder, _commit = head_snapshot
    copy = tmp_path / "snap"
    for part in ("src", "config"):
        shutil.copytree(folder / part, copy / part, ignore=shutil.ignore_patterns("__pycache__"))
    return copy


def _settings_of(snapshot: Path, name: str) -> dict:
    return tomllib.loads((snapshot / name).read_text(encoding="utf-8"))


@pytest.fixture
def fake_harness(tmp_path: Path, watch: ModuleType, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A harness folder holding the python of its environment, as far as the script looks; the variable is unset."""
    folder = tmp_path / "harness"
    (folder / watch.HARNESS_PYTHON).parent.mkdir(parents=True)
    (folder / watch.HARNESS_PYTHON).write_bytes(b"")
    monkeypatch.delenv(watch.HARNESS_ENV, raising=False)
    return folder


# -- acceptance 1: the snapshot is HEAD's tree ---------------------------------------------------------


def test_the_snapshot_holds_every_file_of_head_s_tree_and_nothing_else(head_snapshot):
    folder, commit = head_snapshot

    tracked = {
        name for name in _git(REPO_ROOT, "ls-tree", "-r", "-z", "--name-only", "HEAD").decode().split("\0") if name
    }
    snapped = {path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()}

    assert snapped == tracked
    assert commit == _git(REPO_ROOT, "rev-parse", "HEAD").decode().strip()


def test_every_file_of_the_snapshot_holds_what_head_holds(head_snapshot):
    folder, _commit = head_snapshot
    entries = [
        line.split("\t", 1) for line in _git(REPO_ROOT, "ls-tree", "-r", "-z", "HEAD").decode().split("\0") if line
    ]
    names = {meta.split()[2]: name for meta, name in entries}
    batch = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "--batch"],
        input="".join(f"{blob}\n" for blob in names).encode(),
        capture_output=True,
        check=True,
    ).stdout

    blobs, position = {}, 0
    while position < len(batch):
        header_end = batch.index(b"\n", position)
        blob, _kind, size = batch[position:header_end].decode().split()
        start = header_end + 1
        blobs[names[blob]] = batch[start : start + int(size)]
        position = start + int(size) + 1

    differing = [
        name for name, content in blobs.items() if _normalized((folder / name).read_bytes()) != _normalized(content)
    ]
    assert len(blobs) == len(names) and differing == []


def test_the_snapshot_keeps_the_committed_config_bytes_that_a_run_hashes(head_snapshot):
    folder, _commit = head_snapshot

    for name in ("config/agent.toml", "config/models.toml", "config/redalert/tuning.toml"):
        assert (folder / name).read_bytes() == _git(REPO_ROOT, "cat-file", "blob", f"HEAD:{name}"), name


def test_what_the_working_tree_changed_or_added_is_not_in_the_snapshot(watch, small_repository, tmp_path):
    (small_repository / "notes.txt").write_bytes(b"edited, not committed\n")
    (small_repository / "scratch.txt").write_bytes(b"untracked\n")

    commit = watch.take_snapshot(small_repository, tmp_path / "snap")

    assert _normalized((tmp_path / "snap" / "notes.txt").read_bytes()) == b"committed\n"
    assert not (tmp_path / "snap" / "scratch.txt").exists()
    assert commit == _git(small_repository, "rev-parse", "HEAD").decode().strip()


@pytest.fixture
def outside_any_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A folder git sees as in no repository, even when the temporary folder lies inside one."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    return tmp_path


def test_a_folder_that_is_no_repository_stops_with_git_s_message(watch, outside_any_repository):
    tmp_path = outside_any_repository
    with pytest.raises(watch.WatchError, match="git rev-parse failed in .*not a git repository"):
        watch.take_snapshot(tmp_path, tmp_path / "snap")

    assert not (tmp_path / "snap").exists()


class FakeProcess:
    """A child process as the script sees it, running nothing.

    It ends by itself after `exits_after` polls (after `on_exit` has run), raises KeyboardInterrupt on the poll
    numbered `interrupts_at`, as Ctrl+C would during the wait for the game, and else runs until it is ended.
    """

    def __init__(self, name: str, command: list[str], environment: dict, folder: Path | None, **behavior) -> None:
        self.name, self.command, self.environment, self.folder = name, command, environment, folder
        self.exits_after = behavior.get("exits_after")
        self.interrupts_at = behavior.get("interrupts_at")
        self.on_exit = behavior.get("on_exit")
        self.returncode: int | None = None
        self.polls = 0
        self.terminated = self.killed = False

    def poll(self) -> int | None:
        if self.returncode is None:
            self.polls += 1
            self._interrupt_or_end()
        return self.returncode

    def _interrupt_or_end(self) -> None:
        if self.polls == self.interrupts_at:
            raise KeyboardInterrupt
        if self.exits_after is not None and self.polls >= self.exits_after:
            self._end()

    def _end(self) -> None:
        if self.on_exit is not None:
            self.on_exit(self)
        self.returncode = 0

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 1

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode


def _name_of(command: list[str]) -> str:
    if "openra_harness" in command:
        return "harness"
    return "feed" if "ucla_agent.feed" in command else "model server"


class World:
    """Everything around the script, stubbed: the processes it starts, the ports it probes, the URLs it waits for
    and the pages it opens. `behavior` gives each process, by name, its FakeProcess behavior."""

    def __init__(self) -> None:
        self.started: list[FakeProcess] = []
        self.probed_ports: list[int] = []
        self.taken_ports: set[int] = set()
        self.waited_for: list[str] = []
        self.unanswered: set[str] = set()
        self.opened: list[str] = []
        self.behavior: dict[str, dict] = {"harness": {"exits_after": 3, "on_exit": self.write_the_record}}

    def start_process(self, command: list[str], environment: dict, folder: Path | None) -> FakeProcess:
        name = _name_of(command)
        process = FakeProcess(name, command, environment, folder, **self.behavior.get(name, {}))
        self.started.append(process)
        return process

    def port_in_use(self, port: int) -> bool:
        self.probed_ports.append(port)
        return port in self.taken_ports

    def wait_until_answering(self, url: str, process: FakeProcess, seconds: float) -> bool:
        self.waited_for.append(url)
        return url not in self.unanswered

    def process(self, name: str) -> FakeProcess:
        return next(process for process in self.started if process.name == name)

    @staticmethod
    def write_the_record(harness: FakeProcess) -> None:
        """What the agent leaves behind in a game: its match record, where the harness's environment says."""
        write_record(Path(harness.environment["UCLA_AGENT_RUNS_DIR"]), watched_match())


@pytest.fixture
def world(watch: ModuleType, monkeypatch: pytest.MonkeyPatch) -> World:
    """The script's processes, ports, waits and browser stubbed; no real process, port or page is touched."""
    stubbed = World()
    monkeypatch.setattr(watch, "start_process", stubbed.start_process)
    monkeypatch.setattr(watch, "port_in_use", stubbed.port_in_use)
    monkeypatch.setattr(watch, "wait_until_answering", stubbed.wait_until_answering)
    monkeypatch.setattr(watch, "open_browser", stubbed.opened.append)
    monkeypatch.setattr(watch, "POLL_SECONDS", 0.01)
    return stubbed


@pytest.fixture
def small_watch(watch: ModuleType, small_repository: Path, world: World, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The script playing from the small repository at a fixed stamp in a stubbed world, its snapshot's loaders
    left out: the small repository holds no packages for them to run with."""
    monkeypatch.setattr(watch, "REPOSITORY", small_repository)
    monkeypatch.setattr(watch, "utc_stamp", lambda: "20261010T180509Z")
    monkeypatch.setattr(watch, "check_snapshot_config", lambda snapshot: None)
    return watch


def test_the_command_takes_its_snapshot_under_tmp_watch_of_the_repository(small_watch, fake_harness, capsys):
    exit_code = small_watch.main(["--harness", str(fake_harness)])

    snapshot = small_watch.REPOSITORY / "tmp" / "watch" / "20261010T180509Z" / "snap"
    assert exit_code == 0 and (snapshot / "notes.txt").is_file()
    assert f"snapshot:      {snapshot}" in capsys.readouterr().out


def test_the_command_stops_when_the_snapshot_cannot_be_taken(
    watch, outside_any_repository, fake_harness, monkeypatch, capsys
):
    monkeypatch.setattr(watch, "REPOSITORY", outside_any_repository)

    assert watch.main(["--harness", str(fake_harness)]) == 1 and "git rev-parse failed" in capsys.readouterr().err


# -- acceptance 2: --model and the model ports, in the snapshot only ---------------------------------


def test_model_small_sets_the_plan_choice_the_chain_and_the_autostart_role_and_the_watch_ports(watch, snapshot_copy):
    watch.apply_settings(snapshot_copy, watch.model_settings("small"))

    agent = _settings_of(snapshot_copy, "config/agent.toml")
    server = _settings_of(snapshot_copy, "config/models.toml")["server"]
    assert agent["redalert"]["model_plan_choice"] is True
    assert agent["decide"]["chains"]["red_alert_plan"] == ["local:small"]
    assert agent["models"]["autostart_roles"] == ["small"]
    assert (server["port"], server["backend_port_base"]) == (11600, 41600)
    watch.check_snapshot_config(snapshot_copy)


def _changed_agent_lines(watch: ModuleType, snapshot: Path, head_snapshot: tuple[Path, str], model: str) -> list[str]:
    """The lines of the snapshot's config/agent.toml that `--model <model>` changes; fails if it adds or drops one."""
    original = (head_snapshot[0] / "config" / "agent.toml").read_text(encoding="utf-8").splitlines()

    watch.apply_settings(snapshot, watch.model_settings(model))

    changed = (snapshot / "config" / "agent.toml").read_text(encoding="utf-8").splitlines()
    assert len(changed) == len(original)
    return [new for old, new in zip(original, changed) if old != new]


def test_only_the_changed_lines_differ_and_the_comments_stay(watch, snapshot_copy, head_snapshot):
    # The config ships the plan choice on, so model_plan_choice = true is not a changed line.
    assert _changed_agent_lines(watch, snapshot_copy, head_snapshot, "fast") == [
        'autostart_roles = ["fast"]',
        'red_alert_plan = ["local:fast"]',
        'red_alert_scout = ["local:fast"]',
        'red_alert_commander = ["local:fast"]',
    ]


def test_model_none_changes_only_the_plan_choice_and_the_commander_lines(watch, snapshot_copy, head_snapshot):
    assert _changed_agent_lines(watch, snapshot_copy, head_snapshot, "none") == [
        "model_plan_choice = false",
        "model_commander = false",
    ]


def test_scout_chain_model_small_has_the_snapshot_s_scout_chain_ask_the_small_role_and_its_switch_set_on(
    watch, snapshot_copy
):
    settings = watch.model_settings("small") + [watch.parse_setting("agent:redalert.model_scout_choice=true")]

    watch.apply_settings(snapshot_copy, settings)

    agent = _settings_of(snapshot_copy, "config/agent.toml")
    assert agent["decide"]["chains"]["red_alert_scout"] == ["local:small"]
    assert agent["decide"]["chains"]["red_alert_plan"] == ["local:small"]
    assert agent["redalert"]["model_scout_choice"] is True
    watch.check_snapshot_config(snapshot_copy)


def test_scout_chain_model_none_leaves_the_scout_chain_as_it_ships(watch, snapshot_copy, head_snapshot):
    shipped = _settings_of(head_snapshot[0], "config/agent.toml")["decide"]["chains"]["red_alert_scout"]

    watch.apply_settings(snapshot_copy, watch.model_settings("none"))

    assert _settings_of(snapshot_copy, "config/agent.toml")["decide"]["chains"]["red_alert_scout"] == shipped


def test_commander_model_small_has_the_commander_chain_ask_the_small_role_and_its_switch_set_on_loads(
    watch, snapshot_copy
):
    settings = watch.model_settings("small") + [watch.parse_setting("agent:redalert.model_commander=true")]

    watch.apply_settings(snapshot_copy, settings)

    agent = _settings_of(snapshot_copy, "config/agent.toml")
    assert agent["decide"]["chains"]["red_alert_commander"] == ["local:small"]
    assert agent["redalert"]["model_commander"] is True
    watch.check_snapshot_config(snapshot_copy)


def test_commander_model_none_turns_the_shipped_commander_off_and_leaves_its_chain_as_it_ships(
    watch, snapshot_copy, head_snapshot
):
    shipped = _settings_of(head_snapshot[0], "config/agent.toml")

    watch.apply_settings(snapshot_copy, watch.model_settings("none"))

    agent = _settings_of(snapshot_copy, "config/agent.toml")
    assert shipped["redalert"]["model_commander"] is True
    assert agent["decide"]["chains"]["red_alert_commander"] == shipped["decide"]["chains"]["red_alert_commander"]
    assert agent["redalert"]["model_commander"] is False
    watch.check_snapshot_config(snapshot_copy)


def test_model_none_is_the_plan_choice_and_the_commander_both_off(watch):
    assert [str(setting) for setting in watch.model_settings("none")] == [
        "agent:redalert.model_plan_choice=false",
        "agent:redalert.model_commander=false",
    ]


def test_model_none_turns_the_model_s_plan_choice_and_commander_off(watch, snapshot_copy):
    watch.apply_settings(snapshot_copy, watch.model_settings("none"))

    agent = _settings_of(snapshot_copy, "config/agent.toml")
    assert (agent["redalert"]["model_plan_choice"], agent["redalert"]["model_commander"]) == (False, False)
    assert _settings_of(snapshot_copy, "config/models.toml")["server"]["port"] == 11600


def test_the_working_tree_s_settings_files_are_left_as_they_are(watch, snapshot_copy):
    names = ("config/agent.toml", "config/models.toml", "config/redalert/tuning.toml")
    before = {name: (REPO_ROOT / name).read_bytes() for name in names}

    watch.apply_settings(
        snapshot_copy, watch.model_settings("small") + [watch.parse_setting("tuning:raids.standing=true")]
    )

    assert {name: (REPO_ROOT / name).read_bytes() for name in names} == before
    assert {name: (snapshot_copy / name).read_bytes() != before[name] for name in names} == dict.fromkeys(names, True)


# -- acceptance 3: --set, checked by the snapshot's own loaders --------------------------------------


def test_set_tuning_raids_standing_true_changes_that_key_and_the_loaders_take_it(watch, snapshot_copy):
    watch.apply_settings(snapshot_copy, [watch.parse_setting("tuning:raids.standing=true")])

    assert _settings_of(snapshot_copy, "config/redalert/tuning.toml")["raids"]["standing"] is True
    watch.check_snapshot_config(snapshot_copy)


def test_set_an_unknown_key_stops_with_an_error_naming_the_key(watch, snapshot_copy):
    watch.apply_settings(snapshot_copy, [watch.parse_setting("tuning:raids.nope=1")])

    with pytest.raises(watch.WatchError, match=r"do not load, so nothing was started: .*\[raids\].*nope"):
        watch.check_snapshot_config(snapshot_copy)


def test_set_a_value_the_loader_refuses_stops_with_an_error_naming_the_key(watch, snapshot_copy):
    watch.apply_settings(snapshot_copy, [watch.parse_setting("agent:decide.default_deadline_ms=soon")])

    with pytest.raises(watch.WatchError, match="default_deadline_ms"):
        watch.check_snapshot_config(snapshot_copy)


@pytest.mark.parametrize("unit", ['"ftrk"', "ftrk"], ids=["quoted", "bare word"])
def test_scout_settings_the_flak_truck_scouts_and_the_wide_berth_set_on_the_command_line_load_in_the_snapshot(
    watch, snapshot_copy, unit
):
    texts = (f"tuning:scouting.soviet_scout_unit={unit}", "tuning:scouting.scout_wide_berth=true")

    watch.apply_settings(snapshot_copy, [watch.parse_setting(text) for text in texts])

    scouting = _settings_of(snapshot_copy, "config/redalert/tuning.toml")["scouting"]
    assert (scouting["soviet_scout_unit"], scouting["scout_wide_berth"]) == ("ftrk", True)
    watch.check_snapshot_config(snapshot_copy)


def test_scout_settings_a_soviet_scout_unit_other_than_dog_or_ftrk_stops_with_an_error_naming_the_key(
    watch, snapshot_copy
):
    watch.apply_settings(snapshot_copy, [watch.parse_setting("tuning:scouting.soviet_scout_unit=jeep")])

    with pytest.raises(watch.WatchError, match="soviet_scout_unit"):
        watch.check_snapshot_config(snapshot_copy)


def test_model_plan_hold_the_switch_and_its_ticks_set_on_the_command_line_load_in_the_snapshot(watch, snapshot_copy):
    texts = ("tuning:plan_rules.model_plan_hold=true", "tuning:plan_rules.model_plan_hold_ticks=1000")

    watch.apply_settings(snapshot_copy, [watch.parse_setting(text) for text in texts])

    rules = _settings_of(snapshot_copy, "config/redalert/tuning.toml")["plan_rules"]
    assert (rules["model_plan_hold"], rules["model_plan_hold_ticks"]) == (True, 1000)
    watch.check_snapshot_config(snapshot_copy)


def test_model_plan_hold_a_switch_that_is_not_true_or_false_stops_with_an_error_naming_the_key(watch, snapshot_copy):
    watch.apply_settings(snapshot_copy, [watch.parse_setting("tuning:plan_rules.model_plan_hold=yes")])

    with pytest.raises(watch.WatchError, match="model_plan_hold"):
        watch.check_snapshot_config(snapshot_copy)


@pytest.mark.parametrize("value", ['"compact"', "compact"], ids=["quoted", "bare word"])
def test_plan_prompt_compact_set_on_the_command_line_loads_in_the_snapshot(watch, snapshot_copy, value):
    watch.apply_settings(snapshot_copy, [watch.parse_setting(f"agent:redalert.plan_prompt={value}")])

    assert _settings_of(snapshot_copy, "config/agent.toml")["redalert"]["plan_prompt"] == "compact"
    watch.check_snapshot_config(snapshot_copy)


def test_plan_prompt_a_value_other_than_briefing_or_compact_stops_with_an_error_naming_the_key(watch, snapshot_copy):
    watch.apply_settings(snapshot_copy, [watch.parse_setting('agent:redalert.plan_prompt="short"')])

    with pytest.raises(watch.WatchError, match="plan_prompt"):
        watch.check_snapshot_config(snapshot_copy)


def test_the_command_stops_on_an_unknown_key_before_anything_starts(
    watch, head_snapshot, fake_harness, monkeypatch, tmp_path, capsys
):
    def copy_of_head(_repository: Path, folder: Path) -> str:
        shutil.copytree(head_snapshot[0] / "src", folder / "src", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(head_snapshot[0] / "config", folder / "config")
        return head_snapshot[1]

    monkeypatch.setattr(watch, "REPOSITORY", tmp_path)
    monkeypatch.setattr(watch, "take_snapshot", copy_of_head)

    exit_code = watch.main(["--set", "tuning:raids.nope=1", "--harness", str(fake_harness)])

    error = capsys.readouterr().err
    assert exit_code == 1 and "nope" in error and "nothing was started" in error
    assert not (fake_harness / "runs").exists()


@pytest.mark.parametrize(
    "text, setting",
    [
        ("tuning:raids.standing=true", ("tuning", "raids", "standing", "true")),
        (
            'agent:decide.chains.red_alert_plan=["local:fast"]',
            ("agent", "decide.chains", "red_alert_plan", '["local:fast"]'),
        ),
        ("agent:decide.default_deadline_ms=900", ("agent", "decide", "default_deadline_ms", "900")),
        ("tuning:plan_rules.min_ratio=1.25", ("tuning", "plan_rules", "min_ratio", "1.25")),
        ("tuning:opening.order=refinery_first", ("tuning", "opening", "order", '"refinery_first"')),
    ],
    ids=["a switch", "a list", "a whole number", "a fraction", "a bare word as a string"],
)
def test_a_set_argument_names_the_file_the_table_the_key_and_the_value_as_toml(watch, text, setting):
    parsed = watch.parse_setting(text)

    assert (parsed.file, parsed.table, parsed.key, parsed.value) == setting


@pytest.mark.parametrize("text", ["raids.standing=true", "models:server.port=1", "tuning:standing=true", "tuning:"])
def test_a_set_argument_of_another_form_is_refused_with_the_form_it_needs(watch, text):
    with pytest.raises(watch.WatchError, match=r"is not <file>:<section>\.<key>=<value> with the file agent or tuning"):
        watch.parse_setting(text)


# -- acceptance 7: the harness folder -------------------------------------------------------------------


def test_a_missing_harness_path_stops_with_a_message_naming_both_ways_to_give_it(small_watch, fake_harness, capsys):
    exit_code = small_watch.main([])

    error = capsys.readouterr().err
    assert exit_code == 1 and "--harness" in error and "UCLA_REDALERT_HARNESS" in error
    assert not (small_watch.REPOSITORY / "tmp").exists()


def test_the_harness_comes_from_the_variable_when_no_folder_is_given_and_the_option_wins(watch, fake_harness, tmp_path):
    other = tmp_path / "other"

    from_variable = watch.find_harness(None, {watch.HARNESS_ENV: str(fake_harness)})
    with pytest.raises(watch.WatchError, match="holds no harness python"):
        watch.find_harness(str(other), {watch.HARNESS_ENV: str(fake_harness)})

    assert from_variable == (fake_harness, fake_harness / watch.HARNESS_PYTHON)


def test_a_harness_folder_without_its_python_stops_naming_where_it_was_looked_for(watch, tmp_path):
    with pytest.raises(watch.WatchError) as stopped:
        watch.find_harness(str(tmp_path), {})

    message = str(stopped.value)
    assert (
        str(tmp_path / watch.HARNESS_PYTHON) in message
        and "--harness" in message
        and "UCLA_REDALERT_HARNESS" in message
    )


# -- the setup, printed and saved --------------------------------------------------------------------


def test_the_setup_is_printed_one_line_a_part_and_saved_as_setup_json_beside_the_records(
    small_watch, fake_harness, capsys
):
    exit_code = small_watch.main(
        [
            "--harness",
            str(fake_harness),
            "--faction",
            "russia",
            "--map",
            "trapped",
            "--set",
            "tuning:raids.standing=true",
        ]
    )

    out = fake_harness / "runs" / "watch" / "20261010T180509Z"
    saved = json.loads((out / "setup.json").read_text(encoding="utf-8"))
    commit = _git(small_watch.REPOSITORY, "rev-parse", "HEAD").decode().strip()
    assert exit_code == 0
    assert saved == {
        "stamp": "20261010T180509Z",
        "commit": commit,
        "faction": "russia",
        "map": "trapped",
        "opponent": "bot:normal",
        "model": "small",
        "settings": [
            "agent:redalert.model_plan_choice=true",
            'agent:decide.chains.red_alert_plan=["local:small"]',
            'agent:decide.chains.red_alert_scout=["local:small"]',
            'agent:decide.chains.red_alert_commander=["local:small"]',
            'agent:models.autostart_roles=["small"]',
            "tuning:raids.standing=true",
        ],
        "snapshot": str(small_watch.REPOSITORY / "tmp" / "watch" / "20261010T180509Z" / "snap"),
        "harness": str(fake_harness),
        "out": str(out),
        "records": str(out / "records"),
        "model_endpoint": "http://127.0.0.1:11600",
        "feed": "http://127.0.0.1:8765/",
    }
    printed = capsys.readouterr().out.splitlines()
    assert printed[:7] == [
        "Watched game 20261010T180509Z",
        f"  commit:        {commit}",
        "  faction:       russia",
        "  map:           trapped",
        "  opponent:      bot:normal",
        "  model:         small",
        "  setting:       agent:redalert.model_plan_choice=true",
    ]
    assert f"  records:       {out / 'records'}" in printed and f"  setup saved:   {out / 'setup.json'}" in printed


def test_without_a_model_the_setup_says_the_script_chooses_and_no_model_server_starts(
    small_watch, fake_harness, capsys
):
    small_watch.main(["--harness", str(fake_harness), "--model", "none", "--feed-port", "8790"])

    saved = json.loads(
        (fake_harness / "runs" / "watch" / "20261010T180509Z" / "setup.json").read_text(encoding="utf-8")
    )
    printed = capsys.readouterr().out
    switches_off = ["agent:redalert.model_plan_choice=false", "agent:redalert.model_commander=false"]
    assert (saved["model_endpoint"], saved["feed"]) == (None, "http://127.0.0.1:8790/")
    assert saved["settings"] == switches_off
    assert "model:         none, the script chooses the plan" in printed and "model server:  not started" in printed
    assert all(f"  setting:       {setting}\n" in printed for setting in switches_off)


def test_a_secret_in_the_setup_is_neither_printed_nor_saved(small_watch, fake_harness, monkeypatch, capsys):
    # Built in parts so that no tracked file holds the value.
    secret = "watch-test-" + "9f8e7d6c5b4a"
    monkeypatch.setenv("UCLA_WATCH_TEST_API_KEY", secret)

    small_watch.main(["--harness", str(fake_harness), "--set", f"tuning:opening.order={secret}"])

    saved = (fake_harness / "runs" / "watch" / "20261010T180509Z" / "setup.json").read_text(encoding="utf-8")
    assert secret not in saved and "[REDACTED]" in saved
    assert secret not in capsys.readouterr().out


# -- acceptance 6: the summary, from the match record ------------------------------------------------


def write_record(records: Path, events: list[tuple[str, dict]], *, name: str = "game1-sidestep-7__Multi0") -> Path:
    """Write a match record under a records folder as the agent writes it: one dated folder, one match folder."""
    folder = records / "20261010" / name
    folder.mkdir(parents=True)
    lines = [
        json.dumps(
            {"seq": seq, "t": f"2026-10-10T18:00:{seq % 60:02d}.000Z", "mono_ms": seq, "kind": kind, "data": data}
        )
        for seq, (kind, data) in enumerate(events)
    ]
    (folder / "events.jsonl").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    meta = {"game_type": "red_alert", "agent_id": "local-Multi0", "started_at": "2026-10-10T18:00:00.000Z"}
    (folder / "meta.json").write_bytes(json.dumps(meta).encode("utf-8"))
    return folder


def watched_match() -> list[tuple[str, dict]]:
    """A short watched match: 10 batches in 2 s holding 20, 20, 5 and seven empty ones, three plan choices (one
    timed out), a won result."""
    events: list[tuple[str, dict]] = [("game_config", {})]
    for position, orders in enumerate([20, 20, 5, 0, 0, 0, 0, 0, 0, 0]):
        tick = 5 * (position + 1)
        action = "WAIT" if orders == 0 else {"type": "orders", "orders": [{"cmd": "move"}] * orders}
        events.append(
            ("state", {"state_version": tick, "phase": "playing", "trade_balance": 900, "explored_percent": 7.5})
        )
        events.append(
            ("action", {"state_version": tick, "action": action, "order_count": orders, "command_count": orders})
        )
    for tick, latency, outcome in ((20, 150, "used"), (35, 200, "used"), (45, 900, "timeout")):
        events.append(
            (
                "decision",
                {"state_version": str(tick), "source": "local:small", "latency_ms": latency, "outcome": outcome},
            )
        )
    events.append(("fallback", {"intended_source": "local:small", "reason": "timeout", "used": "script"}))
    result = {"returns": {"local-Multi0": 1.0, "bot:normal": 0.0}, "termination_reason": "victory"}
    events.append(("result", {"result": result, "ended_at": "2026-10-10T18:00:03.000Z", "dropped_events": 0}))
    return events


def test_the_summary_reads_its_figures_from_the_match_record(watch, tmp_path, capsys):
    write_record(tmp_path / "records", watched_match())

    watch.print_summary(tmp_path / "records")

    assert capsys.readouterr().out.splitlines() == [
        "Summary of the watched game:",
        "  Match game1-sidestep-7__Multi0",
        "    result:         won (victory)",
        "    trade balance:  +900",
        "    game length:    0:02 of game time",
        "    model choices:  3, latency median 200 ms, max 900 ms; used 2, timeout 1",
        "    fallbacks:      1",
        "    map explored:   7.5%",
        "    throughput:     22.50 orders/s, 5.00 batches/s, 22.5 commands/s; 4.50 / 20 orders per batch, "
        "20.0% of batches at 20, 4.5 / 200 commands per batch; 0.000 rejected/s",
    ]


def test_a_game_that_left_no_record_is_summed_up_in_one_line(watch, tmp_path, capsys):
    watch.print_summary(tmp_path / "records")

    assert capsys.readouterr().out.splitlines()[1].startswith(f"  No match record under {tmp_path / 'records'}")


# -- acceptance 4: the harness, the model role and the feed, as they are started --------------------------

STAMP = "20261010T180509Z"


def _out(harness: Path) -> Path:
    return harness / "runs" / "watch" / STAMP


def test_the_harness_is_called_with_the_arguments_and_the_two_environment_variables(small_watch, fake_harness, world):
    small_watch.main(
        ["--harness", str(fake_harness), "--faction", "russia", "--map", "trapped", "--opponent", "bot:rush"]
    )

    harness = world.process("harness")
    snapshot = small_watch.REPOSITORY / "tmp" / "watch" / STAMP / "snap"
    assert harness.command == [
        str(fake_harness / small_watch.HARNESS_PYTHON),
        "-m",
        "openra_harness",
        "play",
        "--agent-src",
        str(snapshot / "src"),
        "--port",
        "5500",
        "--map",
        "trapped",
        "--faction",
        "russia",
        "--opponent",
        "bot:rush",
        "--clock",
        "realtime",
        "--games",
        "1",
        "--watch",
        "--out",
        str(_out(fake_harness)),
    ]
    assert harness.environment["UCLA_AGENT_ROOT"] == str(snapshot)
    assert harness.environment["UCLA_AGENT_RUNS_DIR"] == str(_out(fake_harness) / "records")
    assert harness.folder == fake_harness and "PYTHONPATH" not in harness.environment


def test_the_model_role_is_started_from_the_snapshot_and_answers_before_the_game_starts(
    small_watch, fake_harness, world
):
    small_watch.main(["--harness", str(fake_harness), "--model", "fast"])

    server = world.process("model server")
    snapshot = small_watch.REPOSITORY / "tmp" / "watch" / STAMP / "snap"
    assert [process.name for process in world.started] == ["model server", "harness", "feed"]
    assert server.command[:2] == [sys.executable, "-c"] and server.command[3:] == ["fast"]
    assert "modelserver.start([sys.argv[1]])" in server.command[2]
    assert server.environment["PYTHONPATH"] == str(snapshot / "src")
    assert server.environment["UCLA_AGENT_ROOT"] == str(snapshot)
    assert world.waited_for[0] == "http://127.0.0.1:11600/v1/models"


def test_the_feed_follows_the_records_folder_from_the_snapshot_and_opens_in_the_browser(
    small_watch, fake_harness, world
):
    small_watch.main(["--harness", str(fake_harness), "--feed-port", "8790"])

    feed = world.process("feed")
    snapshot = small_watch.REPOSITORY / "tmp" / "watch" / STAMP / "snap"
    assert feed.command == [
        sys.executable,
        "-m",
        "ucla_agent.feed",
        "--latest",
        "--runs-dir",
        str(_out(fake_harness) / "records"),
        "--port",
        "8790",
    ]
    assert feed.environment["PYTHONPATH"] == str(snapshot / "src")
    assert feed.environment["UCLA_AGENT_ROOT"] == str(small_watch.REPOSITORY)
    assert world.waited_for[-1] == "http://127.0.0.1:8790/" and world.opened == ["http://127.0.0.1:8790/"]


def test_without_a_model_no_model_server_is_started_or_probed(small_watch, fake_harness, world):
    small_watch.main(["--harness", str(fake_harness), "--model", "none"])

    assert [process.name for process in world.started] == ["harness", "feed"]
    assert world.probed_ports == [5500, 8765]


# -- acceptance 5: everything started is stopped, and nothing else is touched ------------------------


def test_at_the_game_s_end_every_started_process_is_stopped_and_the_summary_printed(
    small_watch, fake_harness, world, capsys
):
    exit_code = small_watch.main(["--harness", str(fake_harness)])

    printed = capsys.readouterr().out
    assert exit_code == 0
    assert all(process.returncode is not None for process in world.started)
    assert [(process.name, process.terminated) for process in world.started] == [
        ("model server", True),
        ("harness", False),
        ("feed", True),
    ]
    assert "The game is over (the harness exited with code 0)." in printed
    assert "Stopped everything this command started: feed, harness, model server." in printed
    assert "result:         won (victory)" in printed and "22.50 orders/s" in printed


def test_on_ctrl_c_the_harness_stops_its_engine_itself_and_the_rest_is_stopped(
    small_watch, fake_harness, world, capsys
):
    world.behavior["harness"] = {"interrupts_at": 2, "exits_after": 3, "on_exit": world.write_the_record}

    exit_code = small_watch.main(["--harness", str(fake_harness)])

    printed = capsys.readouterr().out
    assert exit_code == 0 and "Ctrl+C: stopping the game and everything this command started." in printed
    assert all(process.returncode is not None for process in world.started)
    assert [(process.name, process.terminated) for process in world.started] == [
        ("model server", True),
        ("harness", False),
        ("feed", True),
    ]
    assert "result:         won (victory)" in printed


def test_on_ctrl_c_a_harness_that_does_not_stop_in_time_is_ended(small_watch, fake_harness, world, monkeypatch):
    world.behavior["harness"] = {"interrupts_at": 2}
    monkeypatch.setattr(small_watch, "HARNESS_STOP_SECONDS", 0.05)

    assert small_watch.main(["--harness", str(fake_harness)]) == 0

    assert [(process.name, process.terminated) for process in world.started] == [
        ("model server", True),
        ("harness", True),
        ("feed", True),
    ]


def test_a_second_ctrl_c_ends_the_harness_at_once(small_watch, fake_harness, world, monkeypatch, capsys):
    world.behavior["harness"] = {"interrupts_at": 2}
    real_wait = small_watch._wait_for_exit
    waits = []

    def interrupted_grace(process, seconds):
        waits.append(seconds)
        if seconds == small_watch.HARNESS_STOP_SECONDS:
            raise KeyboardInterrupt
        return real_wait(process, seconds)

    monkeypatch.setattr(small_watch, "_wait_for_exit", interrupted_grace)

    small_watch.main(["--harness", str(fake_harness)])

    assert "Ctrl+C again: ending the harness at once." in capsys.readouterr().out
    assert world.process("harness").terminated and small_watch.HARNESS_STOP_SECONDS in waits


def test_processes_and_ports_it_did_not_start_are_untouched(small_watch, fake_harness, world):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as theirs:
        theirs.bind(("127.0.0.1", 0))
        theirs.listen()
        bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            small_watch.main(["--harness", str(fake_harness)])

            assert bystander.poll() is None
            with socket.create_connection(theirs.getsockname(), timeout=2):
                pass
        finally:
            bystander.kill()
            bystander.wait()

    assert world.probed_ports == [11600, 41602, 5500, 8765]
    assert not {11500, 41500, 41501, 41502, 8800, 11434} & set(world.probed_ports)


def test_real_child_processes_are_stopped_when_the_game_ends(small_watch, fake_harness, world, monkeypatch):
    """The stand-ins are real processes: the model server and the feed sleep until they are ended, the harness's ends
    by itself after a moment, as a game does."""
    started: list[subprocess.Popen] = []

    def real_stand_in(command: list[str], environment: dict, folder: Path | None) -> subprocess.Popen:
        seconds = 1 if "openra_harness" in command else 60
        process = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"])
        started.append(process)
        return process

    monkeypatch.setattr(small_watch, "start_process", real_stand_in)
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert small_watch.main(["--harness", str(fake_harness)]) == 0

        assert len(started) == 3 and [process.poll() is not None for process in started] == [True, True, True]
        assert [process.returncode for process in started][1] == 0
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait()


# -- what keeps the game from starting ----------------------------------------------------------------------


def test_a_port_in_use_stops_the_command_before_anything_starts(small_watch, fake_harness, world, capsys):
    world.taken_ports = {8765}

    exit_code = small_watch.main(["--harness", str(fake_harness)])

    assert exit_code == 1 and world.started == []
    assert "Port 8765 of 127.0.0.1 is in use, so nothing was started" in capsys.readouterr().err


def test_a_model_server_that_does_not_answer_is_stopped_and_the_game_not_started(
    small_watch, fake_harness, world, capsys
):
    world.unanswered = {"http://127.0.0.1:11600/v1/models"}

    exit_code = small_watch.main(["--harness", str(fake_harness)])

    assert exit_code == 1 and [process.name for process in world.started] == ["model server"]
    assert world.process("model server").terminated
    assert "The model server did not answer within 600 s." in capsys.readouterr().err


def test_a_feed_that_does_not_answer_leaves_the_game_going(small_watch, fake_harness, world, capsys):
    world.unanswered = {"http://127.0.0.1:8765/"}

    assert small_watch.main(["--harness", str(fake_harness)]) == 0

    assert world.opened == [] and "The feed did not answer within 30 s; open http://127.0.0.1:8765/ yourself." in (
        capsys.readouterr().out
    )


def test_a_feed_that_stops_leaves_the_game_going(small_watch, fake_harness, world, monkeypatch, capsys):
    def feed_stopped(url: str, process: FakeProcess, seconds: float) -> bool:
        if process.name == "feed":
            raise small_watch.WatchError("the feed ended with exit code 1")
        return True

    monkeypatch.setattr(small_watch, "wait_until_answering", feed_stopped)

    assert small_watch.main(["--harness", str(fake_harness)]) == 0

    assert "The feed stopped (the feed ended with exit code 1); the game goes on without it." in capsys.readouterr().err


# -- the probes and the stopping, on real sockets and processes ---------------------------------------


def test_a_port_is_in_use_while_something_listens_on_it(watch):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        listening = watch.port_in_use(port)

    assert listening is True and watch.port_in_use(port) is False


def test_waiting_for_a_url_ends_when_it_answers_when_its_process_ends_or_when_the_time_is_up(watch, monkeypatch):
    monkeypatch.setattr(watch, "POLL_SECONDS", 0.01)
    running = FakeProcess("feed", [], {}, None)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
    try:
        answered = watch.wait_until_answering(f"http://127.0.0.1:{server.server_address[1]}/", running, 5)
    finally:
        server.shutdown()
        server.server_close()
    ended = FakeProcess("model server", [], {}, None)
    ended.returncode = 3

    assert answered is True
    assert watch.wait_until_answering(f"http://127.0.0.1:{server.server_address[1]}/", running, 0.2) is False
    with pytest.raises(watch.WatchError, match="ended with exit code 3"):
        watch.wait_until_answering("http://127.0.0.1:9/", ended, 5)


def test_a_process_that_does_not_end_when_asked_is_killed(watch, monkeypatch):
    class Stubborn(FakeProcess):
        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: float | None = None) -> int | None:
            if not self.killed:
                raise subprocess.TimeoutExpired("stubborn", timeout)
            return self.returncode

    stubborn = Stubborn("feed", [], {}, None)
    monkeypatch.setattr(watch, "start_process", lambda command, environment, folder: stubborn)
    processes = watch.Processes()
    processes.start("feed", ["feed"], {})

    processes.stop_all({})

    assert (stubborn.terminated, stubborn.killed, stubborn.returncode) == (True, True, -9)


def test_the_role_s_backend_port_follows_its_place_in_the_snapshot_s_models_file(watch, head_snapshot):
    assert [watch.backend_port(head_snapshot[0], role) for role in ("fast", "strong", "small")] == [41600, 41601, 41602]


# -- writing one key of a TOML file ----------------------------------------------------------------------


def test_a_key_is_written_in_place_keeping_the_comments_and_the_windows_line_endings(watch, tmp_path):
    path = tmp_path / "file.toml"
    path.write_bytes(b"# head\r\n[a]\r\n# about x\r\nx = 1\r\ny = 2\r\n\r\n[b]\r\nx = 3\r\n")

    watch.set_key(path, "a", "x", "10")

    assert path.read_bytes() == b"# head\r\n[a]\r\n# about x\r\nx = 10\r\ny = 2\r\n\r\n[b]\r\nx = 3\r\n"


def test_a_key_the_table_lacks_is_added_after_its_last_line(watch, tmp_path):
    path = tmp_path / "file.toml"
    path.write_bytes(b"[a]\nx = 1\n\n[a.b]\ny = 2\n")

    watch.set_key(path, "a", "z", "true")

    assert path.read_bytes() == b"[a]\nx = 1\nz = true\n\n[a.b]\ny = 2\n"


def test_a_table_the_file_lacks_is_added_at_its_end(watch, tmp_path):
    path = tmp_path / "file.toml"
    path.write_bytes(b"[a]  # the first\nx = 1\n")

    watch.set_key(path, "c.d", "w", '"text"')

    assert tomllib.loads(path.read_text(encoding="utf-8")) == {"a": {"x": 1}, "c": {"d": {"w": "text"}}}


def test_a_key_that_cannot_be_written_so_stops_with_an_error(watch, tmp_path):
    path = tmp_path / "file.toml"
    path.write_bytes(b"[redalert.sparring]\nseat_plans = {}\n")

    with pytest.raises(watch.WatchError, match="redalert.sparring.seat_plans.Multi1"):
        watch.set_key(path, "redalert.sparring.seat_plans", "Multi1", '"HOLD"')

    assert path.read_bytes() == b"[redalert.sparring]\nseat_plans = {}\n"


# -- the command line ------------------------------------------------------------------------------


def test_the_command_line_defaults_to_random_faction_bot_normal_sidestep_and_the_small_model(watch):
    arguments = watch.parse_arguments([])

    assert (arguments.faction, arguments.opponent, arguments.map, arguments.model) == (
        "random",
        "bot:normal",
        "sidestep",
        "small",
    )
    assert (arguments.set, arguments.harness, arguments.feed_port) == ([], None, 8765)


def test_the_command_line_takes_every_option(watch):
    arguments = watch.parse_arguments(
        [
            "--faction",
            "russia",
            "--opponent",
            "bot:rush",
            "--map",
            "shadowfiend-ii",
            "--model",
            "none",
            "--set",
            "tuning:raids.standing=true",
            "--set",
            "agent:decide.default_deadline_ms=900",
            "--harness",
            "D:/harness",
            "--feed-port",
            "8790",
        ]
    )

    assert vars(arguments) == {
        "faction": "russia",
        "opponent": "bot:rush",
        "map": "shadowfiend-ii",
        "model": "none",
        "set": ["tuning:raids.standing=true", "agent:decide.default_deadline_ms=900"],
        "harness": "D:/harness",
        "feed_port": 8790,
    }


def test_the_stamp_is_the_utc_time_to_the_second(watch):
    assert watch.utc_stamp(datetime(2026, 10, 10, 18, 5, 9, 750000, tzinfo=timezone.utc)) == "20261010T180509Z"


def test_a_key_whose_table_the_file_already_defines_with_dotted_keys_stops_with_an_error(watch, tmp_path):
    path = tmp_path / "file.toml"
    path.write_bytes(b"[a]\nb.c = 1\n")

    with pytest.raises(watch.WatchError, match="would no longer be valid TOML with a.b.d = 2"):
        watch.set_key(path, "a.b", "d", "2")


def test_a_child_process_is_started_with_the_environment_and_folder_it_is_given(watch, tmp_path):
    script = "import os, pathlib; pathlib.Path('seen.txt').write_text(os.environ['WATCH_TEST_MARK'])"

    child = watch.start_process([sys.executable, "-c", script], {**os.environ, "WATCH_TEST_MARK": "marked"}, tmp_path)

    assert child.wait(timeout=60) == 0 and (tmp_path / "seen.txt").read_text() == "marked"


def test_the_feed_page_is_opened_in_the_default_browser(watch, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(watch.webbrowser, "open", opened.append)

    watch.open_browser("http://127.0.0.1:8765/")

    assert opened == ["http://127.0.0.1:8765/"]


# -- docs/RUNNING.md ------------------------------------------------------------------------------------


def _watching_section() -> str:
    text = (REPO_ROOT / "docs" / "RUNNING.md").read_text(encoding="utf-8")
    start = text.index("## Watching a Red Alert game")
    return text[start : text.index("\n## ", start + 1)]


@pytest.mark.parametrize(
    "phrase",
    [
        "python scripts/watch_game.py",
        "UCLA_REDALERT_HARNESS",
        "--harness",
        "--faction",
        "--opponent",
        "--map",
        "--model",
        "--set tuning:raids.standing=true",
        "--feed-port",
        "tmp/watch/<UTC stamp>/snap",
        "11600",
        "setup.json",
        "Ctrl+C",
        "python -m ucla_agent.feed --latest",
        "never touches the live agent",
    ],
)
def test_the_running_guide_explains_the_watched_game_and_the_feed(phrase):
    assert phrase in " ".join(_watching_section().split())


def test_the_running_guide_does_not_say_where_the_harness_lies_on_this_machine():
    section = _watching_section()

    # A drive letter alone before a colon and a slash: a path of this machine, not an address such as http://.
    assert "UCLA-RedAlert" not in section and re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", section) is None


# -- --model jev9b: the native JevK5-9B on its own watch port ---------------------------------------


def test_model_jev9b_has_every_red_alert_chain_ask_the_systemone_role_and_starts_it_alone(watch):
    assert [str(setting) for setting in watch.model_settings("jev9b")] == [
        "agent:redalert.model_plan_choice=true",
        'agent:decide.chains.red_alert_plan=["systemone:jev9b"]',
        'agent:decide.chains.red_alert_scout=["systemone:jev9b"]',
        'agent:decide.chains.red_alert_commander=["systemone:jev9b"]',
        'agent:models.autostart_roles=["jev9b"]',
    ]


def test_model_jev9b_changes_the_snapshot_s_chains_and_autostart_role_and_its_loaders_accept_them(
    watch, snapshot_copy, head_snapshot
):
    changed = _changed_agent_lines(watch, snapshot_copy, head_snapshot, "jev9b")

    # The shipped Red Alert chains already ask systemone:jev9b, so only the autostart line changes.
    assert changed == ['autostart_roles = ["jev9b"]']
    chains = _settings_of(snapshot_copy, "config/agent.toml")["decide"]["chains"]
    assert [chains[name] for name in ("red_alert_plan", "red_alert_scout", "red_alert_commander")] == [
        ["systemone:jev9b"]
    ] * 3
    assert _settings_of(snapshot_copy, "config/models.toml")["server"]["backend_port_base"] == 41600
    watch.check_snapshot_config(snapshot_copy)


def test_the_jev9b_role_s_watch_port_follows_the_llama_roles_apart_from_the_live_ports(watch, head_snapshot):
    port = watch.backend_port(head_snapshot[0], "jev9b")

    assert port == 41603
    assert port not in {11500, 41500, 41501, 41502, 41503, 8765}


def test_a_jev9b_watched_game_needs_the_endpoint_and_the_native_server_s_watch_port(watch, head_snapshot, tmp_path):
    setup = watch.Setup(
        "20261013T220000Z",
        "abc",
        "russia",
        "sidestep",
        "bot:normal",
        "jev9b",
        [],
        head_snapshot[0],
        tmp_path,
        tmp_path / "out",
        tmp_path / "records",
        "http://127.0.0.1:11600",
        "http://127.0.0.1:8790/",
    )

    assert watch.ports_needed(setup) == [11600, 41603, watch.HARNESS_PORT, 8790]


def test_the_command_line_takes_model_jev9b(watch):
    assert watch.parse_arguments(["--model", "jev9b"]).model == "jev9b"
