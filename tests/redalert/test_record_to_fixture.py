"""`scripts/record_to_fixture.py`: the full states of a match record as a redacted gzip replay, never over 4 MiB."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import ModuleType

import pytest

from fake_game import FakeRedAlert
from ucla_agent.core.secrets import REDACTED

REPLAYS = Path("tests") / "fixtures" / "redalert" / "replays"
# Put together at run time, so that this file holds no literal the tracked-file scan would report.
SHAPED_KEY = "eak_" + "live_" + "recordfixture0123456789"
LOADED_KEY = "jev-0a1b2c3d4e5f6071"
STATE_COUNT = 12


@pytest.fixture(scope="module")
def tool(load_script) -> ModuleType:
    """The script, imported once."""
    return load_script("record_to_fixture.py")


@pytest.fixture(scope="module")
def states() -> list[dict]:
    """Twelve consecutive states of a fake match."""
    game = FakeRedAlert("england", "russia")
    recorded = []
    for _ in range(STATE_COUNT):
        recorded.append(game.state())
        game.step()
    return recorded


def state_event(state: dict, *, full: bool = True) -> dict:
    """The data of a `state` event: the whole state, or the thinned form a record holds for most states."""
    data = {"state_version": state["state_version"], "phase": state["phase"]}
    if full:
        return {**data, "state": state}
    return {**data, "economy": state["observation"]["economy"], "units_count": len(state["observation"]["units"])}


def write_record(folder: Path, events: list[dict], file_name: str = "events.jsonl") -> Path:
    """Write a match record's events file holding the given `state` event data, and return the folder."""
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        {"seq": seq, "t": "2026-10-07T12:00:00.000Z", "mono_ms": seq * 200, "kind": "state", "data": data}
        for seq, data in enumerate(events)
    ]
    (folder / file_name).write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return folder


def read_replay(path: Path) -> list[dict]:
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).decode("utf-8").splitlines()]


@pytest.fixture
def record(run_folder: Path, states: list[dict]) -> Path:
    """A match folder whose record holds the twelve states in full."""
    return write_record(run_folder / "runs" / "20261007" / "session__Multi0", [state_event(state) for state in states])


# -- what is written -------------------------------------------------------------------------------


def test_the_full_states_of_a_record_are_written_in_order_as_gzip_json_lines(tool, record, run_folder, states):
    report = tool.write_fixture(record, "sample")

    path = run_folder / REPLAYS / "sample.jsonl.gz"
    assert read_replay(path) == states
    assert report == f"Wrote {path}: 12 of 12 full states, every state kept (N = 1), {path.stat().st_size} bytes"


def test_states_recorded_in_their_thinned_form_are_left_out(tool, run_folder, states):
    events = [state_event(state, full=index % 5 == 0) for index, state in enumerate(states)]
    folder = write_record(run_folder / "match", events)

    tool.write_fixture(folder, "thinned-record")

    assert read_replay(run_folder / REPLAYS / "thinned-record.jsonl.gz") == [states[0], states[5], states[10]]


def test_the_attempts_of_a_resumed_match_follow_each_other(tool, run_folder, states):
    folder = write_record(run_folder / "match", [state_event(state) for state in states[:4]])
    write_record(folder, [state_event(state) for state in states[4:7]], "events.2.jsonl")

    tool.write_fixture(folder, "resumed")

    assert read_replay(run_folder / REPLAYS / "resumed.jsonl.gz") == states[:7]


def test_the_same_record_always_gives_the_same_bytes(tool, record, run_folder):
    tool.write_fixture(record, "first")
    tool.write_fixture(record, "second")

    assert (run_folder / REPLAYS / "first.jsonl.gz").read_bytes() == (
        run_folder / REPLAYS / "second.jsonl.gz"
    ).read_bytes()


def test_a_key_in_a_recorded_state_is_redacted_whether_it_is_known_by_shape_or_by_value(
    tool, run_folder, states, monkeypatch
):
    monkeypatch.setenv("JEV_API_KEY", LOADED_KEY)
    leaking = json.loads(json.dumps(states[:2]))
    leaking[0]["players"][0]["name"] = f"agent {SHAPED_KEY}"
    leaking[1]["hint"] = f"retry with {LOADED_KEY}"
    folder = write_record(run_folder / "match", [state_event(state) for state in leaking])

    tool.write_fixture(folder, "redacted")

    path = run_folder / REPLAYS / "redacted.jsonl.gz"
    text = gzip.decompress(path.read_bytes()).decode("utf-8")
    replay = read_replay(path)
    assert SHAPED_KEY not in text and LOADED_KEY not in text
    assert (replay[0]["players"][0]["name"], replay[1]["hint"]) == (f"agent {REDACTED}", f"retry with {REDACTED}")
    assert SHAPED_KEY in (folder / "events.jsonl").read_text(encoding="utf-8")


# -- the size limit --------------------------------------------------------------------------------


def test_a_fixture_may_be_4_mib_at_most(tool):
    assert tool.MAX_FIXTURE_BYTES == 4 * 1024 * 1024


def test_all_states_are_kept_when_they_fit(tool, states):
    data, every = tool.thin_to_fit(states, max_bytes=len(tool.compress(states)))

    assert (data, every) == (tool.compress(states), 1)


def test_when_all_states_do_not_fit_every_nth_is_kept_with_the_smallest_n_that_fits(tool, states):
    sizes = {every: len(tool.compress(states[::every])) for every in (1, 2, 3, 4)}
    assert sizes[1] > sizes[2] > sizes[3] > sizes[4]

    every_second = tool.thin_to_fit(states, max_bytes=sizes[2])
    every_fourth = tool.thin_to_fit(states, max_bytes=sizes[3] - 1)

    assert every_second == (tool.compress(states[::2]), 2)
    assert every_fourth == (tool.compress(states[::4]), 4)


def test_a_thinned_fixture_reports_n_and_holds_every_nth_state(tool, record, run_folder, states):
    limit = len(tool.compress(states[::3]))

    report = tool.write_fixture(record, "thinned", max_bytes=limit)

    path = run_folder / REPLAYS / "thinned.jsonl.gz"
    assert read_replay(path) == states[::3] and path.stat().st_size <= limit
    assert report == f"Wrote {path}: 4 of 12 full states, 1 state in 3 kept (N = 3), {path.stat().st_size} bytes"


def test_a_single_state_larger_than_the_limit_is_not_written(tool, record, run_folder):
    with pytest.raises(tool.NoFixtureError, match="One state alone compresses to more than 100 bytes"):
        tool.write_fixture(record, "too-large", max_bytes=100)

    assert not (run_folder / REPLAYS / "too-large.jsonl.gz").exists()


# -- what cannot be turned into a fixture ----------------------------------------------------------


def test_a_folder_without_a_record_is_refused(tool, run_folder):
    (run_folder / "empty").mkdir()

    with pytest.raises(tool.NoFixtureError, match="holds no match record"):
        tool.write_fixture(run_folder / "empty", "sample")
    with pytest.raises(tool.NoFixtureError, match="holds no match record"):
        tool.write_fixture(run_folder / "missing", "sample")


def test_a_record_without_a_full_state_is_refused(tool, run_folder, states):
    folder = write_record(run_folder / "match", [state_event(state, full=False) for state in states])

    with pytest.raises(tool.NoFixtureError, match="holds no state in full"):
        tool.write_fixture(folder, "sample")


@pytest.mark.parametrize("name", ["../outside", "with space", "nested/name", "", "dotted.name"])
def test_a_name_that_is_not_a_plain_file_name_is_refused_before_anything_is_read(tool, run_folder, name):
    with pytest.raises(tool.NoFixtureError, match="letters, digits, - and _ only"):
        tool.write_fixture(run_folder / "missing", name)

    assert not (run_folder / "tests").exists()


# -- the command line ------------------------------------------------------------------------------


def test_main_writes_the_fixture_and_reports_it(tool, record, run_folder, capsys):
    exit_code = tool.main([str(record), "from-main"])

    path = run_folder / REPLAYS / "from-main.jsonl.gz"
    assert exit_code == 0 and len(read_replay(path)) == STATE_COUNT
    assert capsys.readouterr().out == (
        f"Wrote {path}: 12 of 12 full states, every state kept (N = 1), {path.stat().st_size} bytes\n"
    )


def test_main_reports_what_cannot_be_written_and_exits_with_2(tool, run_folder, capsys):
    exit_code = tool.main([str(run_folder / "missing"), "sample"])

    captured = capsys.readouterr()
    assert exit_code == 2 and captured.out == ""
    assert "holds no match record" in captured.err


def test_run_as_a_program_it_needs_a_folder_and_a_name(run_script, run_folder):
    completed = run_script(run_folder, "record_to_fixture.py", "runs/20261007/session__Multi0")

    assert completed.returncode == 2
    assert "the following arguments are required: name" in completed.stderr
