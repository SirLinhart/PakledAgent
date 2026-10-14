"""The rule tools: `capture_rules.py` stores a match's game reference, `diff_rules.py` compares one with items.toml."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from fake_game import FakeRedAlert
from ucla_agent.core.secrets import REDACTED
from ucla_agent.games.redalert.items import ITEMS_FILE, ItemTable

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_RECORD = REPO_ROOT / "tests" / "fixtures" / "records" / "redalert_synthetic"
CAPTURED = Path("config") / "redalert" / "captured"
# Put together at run time, so that this file holds no literal the tracked-file scan would report.
SHAPED_KEY = "eak_" + "live_" + "capturedrules0123456789"
GAME_CONFIG = {
    "game_type": "red_alert",
    "rules": {
        "buildings": {"pbox": {"cost": 400, "power": -20, "queue": "Defense", "prerequisites": ["tent"], "hp": 40000}},
        "units": {"e1": {"cost": 100, "queue": "Infantry", "prerequisites": [], "hp": 5000}},
        "maps": {"sidestep": {"bounds": [2, 2, 96, 96], "spawns": [[22, 48], [74, 48]]}},
    },
    "match_rules": {"starting_cash": 5000},
}


def event_line(seq: int, kind: str, data: dict, recorded_at: str | None = "2026-10-09T23:30:00.000Z") -> dict:
    line = {"seq": seq, "mono_ms": seq * 200, "kind": kind, "data": data}
    return line if recorded_at is None else {**line, "t": recorded_at}


def write_record(folder: Path, lines: list[dict], file_name: str = "events.jsonl") -> Path:
    """Write a match record's events file with these lines, and return the folder."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / file_name).write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return folder


@pytest.fixture(scope="module")
def capture_tool(load_script) -> ModuleType:
    """`scripts/capture_rules.py`, imported once."""
    return load_script("capture_rules.py")


@pytest.fixture
def record(run_folder: Path) -> Path:
    """A match folder whose record starts with a `game_config` event."""
    lines = [
        event_line(0, "game_config", GAME_CONFIG),
        event_line(1, "state", {"state_version": 0, "phase": "playing"}),
    ]
    return write_record(run_folder / "runs" / "20261009" / "session__Multi0", lines)


# -- capture_rules.py ------------------------------------------------------------------------------


def test_the_game_config_of_a_record_is_written_to_a_file_named_for_the_event_s_utc_date(
    capture_tool, record, run_folder
):
    written = capture_tool.capture(record)

    assert written == run_folder / CAPTURED / "rules-20261009.json"
    assert json.loads(written.read_text(encoding="utf-8")) == GAME_CONFIG


def test_the_captured_file_is_sorted_indented_json_with_lf_line_ends(capture_tool, record):
    written = capture_tool.capture(record)

    assert written.read_bytes() == (json.dumps(GAME_CONFIG, indent=2, sort_keys=True) + "\n").encode("utf-8")
    assert b"\r" not in written.read_bytes()


@pytest.mark.parametrize(
    "recorded_at, file_name",
    [
        ("2026-10-09T23:30:00.000Z", "rules-20261009.json"),
        ("2026-10-10T01:30:00.000+02:00", "rules-20261009.json"),
        ("2026-10-09T20:30:00-05:00", "rules-20261010.json"),
    ],
    ids=["utc", "ahead of utc", "behind utc"],
)
def test_the_date_in_the_file_name_is_the_utc_date_of_the_event(capture_tool, run_folder, recorded_at, file_name):
    folder = write_record(run_folder / "match", [event_line(0, "game_config", GAME_CONFIG, recorded_at)])

    assert capture_tool.capture(folder).name == file_name


def test_a_second_capture_of_the_same_day_replaces_the_first(capture_tool, record, run_folder):
    capture_tool.capture(record)
    later = {**GAME_CONFIG, "match_rules": {"starting_cash": 7500}}
    other = write_record(run_folder / "other", [event_line(0, "game_config", later)])

    written = capture_tool.capture(other)

    assert [path.name for path in (run_folder / CAPTURED).iterdir()] == ["rules-20261009.json"]
    assert json.loads(written.read_text(encoding="utf-8"))["match_rules"] == {"starting_cash": 7500}


def test_the_first_game_config_event_of_a_resumed_match_is_the_one_captured(capture_tool, run_folder):
    second = {**GAME_CONFIG, "match_rules": {"starting_cash": 1}}
    folder = write_record(run_folder / "match", [event_line(0, "state", {}), event_line(1, "game_config", GAME_CONFIG)])
    write_record(folder, [event_line(0, "game_config", second, "2026-10-11T00:00:00.000Z")], "events.2.jsonl")

    written = capture_tool.capture(folder)

    assert written.name == "rules-20261009.json"
    assert json.loads(written.read_text(encoding="utf-8")) == GAME_CONFIG


def test_a_key_in_the_game_config_is_redacted_in_the_captured_file(capture_tool, run_folder):
    leaking = {**GAME_CONFIG, "source": f"fetched with {SHAPED_KEY}"}
    folder = write_record(run_folder / "match", [event_line(0, "game_config", leaking)])

    written = capture_tool.capture(folder)

    assert SHAPED_KEY not in written.read_text(encoding="utf-8")
    assert json.loads(written.read_text(encoding="utf-8"))["source"] == f"fetched with {REDACTED}"


@pytest.mark.parametrize(
    "lines, message",
    [
        ([event_line(0, "state", {"state_version": 0})], "holds no game_config event"),
        ([event_line(0, "game_config", GAME_CONFIG, recorded_at=None)], "has no readable time"),
        ([event_line(0, "game_config", GAME_CONFIG, recorded_at="yesterday")], "has no readable time"),
    ],
    ids=["no game_config event", "no time", "an unreadable time"],
)
def test_a_record_that_gives_nothing_to_capture_is_refused(capture_tool, run_folder, lines, message):
    folder = write_record(run_folder / "match", lines)

    with pytest.raises(capture_tool.NoCaptureError, match=message):
        capture_tool.capture(folder)

    assert not (run_folder / "config").exists()


def test_a_folder_without_a_record_is_refused_by_capture(capture_tool, run_folder):
    (run_folder / "empty").mkdir()

    for folder in (run_folder / "empty", run_folder / "missing"):
        with pytest.raises(capture_tool.NoCaptureError, match="holds no match record"):
            capture_tool.capture(folder)


def test_capture_main_reports_the_file_it_wrote(capture_tool, record, run_folder, capsys):
    exit_code = capture_tool.main([str(record)])

    assert exit_code == 0
    assert capsys.readouterr().out == f"Wrote {run_folder / CAPTURED / 'rules-20261009.json'}\n"


def test_capture_main_reports_what_cannot_be_captured_and_exits_with_2(capture_tool, run_folder, capsys):
    exit_code = capture_tool.main([str(run_folder / "missing")])

    captured = capsys.readouterr()
    assert (exit_code, captured.out) == (2, "")
    assert "holds no match record" in captured.err


def test_capture_run_as_a_program_on_the_synthetic_record_writes_its_game_config(run_script, tmp_path):
    completed = run_script(tmp_path, "capture_rules.py", str(SYNTHETIC_RECORD))

    written = tmp_path / CAPTURED / "rules-20261001.json"
    first_event = json.loads((SYNTHETIC_RECORD / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == f"Wrote {written}\n"
    assert (
        first_event["kind"] == "game_config" and json.loads(written.read_text(encoding="utf-8")) == first_event["data"]
    )


def test_capture_run_as_a_program_without_a_folder_exits_with_2(run_script, tmp_path):
    completed = run_script(tmp_path, "capture_rules.py")

    assert completed.returncode == 2
    assert "the following arguments are required: match_folder" in completed.stderr


# -- diff_rules.py ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def diff_tool(load_script) -> ModuleType:
    """`scripts/diff_rules.py`, imported once."""
    return load_script("diff_rules.py")


@pytest.fixture(scope="module")
def fallback() -> ItemTable:
    """The table of the tracked `config/redalert/items.toml`."""
    return ItemTable.from_file(REPO_ROOT)


@pytest.fixture
def root_with_items_file(run_folder: Path) -> Path:
    """A temporary repository root, the current folder, that holds a copy of the tracked `items.toml`."""
    copy = run_folder / ITEMS_FILE
    copy.parent.mkdir(parents=True)
    copy.write_bytes((REPO_ROOT / ITEMS_FILE).read_bytes())
    return run_folder


def table_of(**sections: dict) -> ItemTable:
    """The table a captured file with these `buildings` and `units` rules gives."""
    return ItemTable(()).with_game_config({"rules": sections})


def rules_of(table: ItemTable, *names: str) -> dict:
    """The rules entries of some items of a table, as a captured file states them."""
    sections: dict = {"buildings": {}, "units": {}}
    for name in names:
        item = table[name]
        entry = {"cost": item.cost, "power": item.power, "prerequisites": list(item.prerequisites), "hp": item.hp}
        sections["buildings" if item.is_building else "units"][name] = entry
    return sections


def write_captured(folder: Path, game_config: object, name: str = "rules-20261009.json") -> Path:
    path = folder / name
    path.write_text(json.dumps(game_config), encoding="utf-8")
    return path


def test_a_captured_table_equal_to_the_fallback_in_the_compared_facts_has_no_difference(diff_tool, fallback):
    captured = table_of(**rules_of(fallback, *(item.name for item in fallback)))

    assert diff_tool.differences(captured, fallback) == []


def test_the_fake_game_s_rules_which_come_from_the_fallback_file_do_not_differ_from_it(diff_tool, fallback):
    captured = ItemTable(()).with_game_config(FakeRedAlert().game_config)

    assert diff_tool.differences(captured, fallback) == []


@pytest.mark.parametrize(
    "fact, value, line",
    [
        ("cost", 400, "pbox: cost 400 (captured) / 600 (items.toml)"),
        ("power", -30, "pbox: power -30 (captured) / -20 (items.toml)"),
        ("hp", 45000, "pbox: hp 45000 (captured) / 40000 (items.toml)"),
        ("prerequisites", ["tent", "dome"], "pbox: prerequisites [dome, tent] (captured) / [tent] (items.toml)"),
        ("prerequisites", [], "pbox: prerequisites [] (captured) / [tent] (items.toml)"),
    ],
    ids=["cost", "power", "hp", "more prerequisites", "no prerequisites"],
)
def test_an_item_that_differs_in_one_fact_is_listed_with_both_values(diff_tool, fallback, fact, value, line):
    rules = rules_of(fallback, "pbox", "e1")
    rules["buildings"]["pbox"][fact] = value

    assert diff_tool.differences(table_of(**rules), ItemTable([fallback["pbox"], fallback["e1"]])) == [line]


def test_an_item_that_differs_in_several_facts_is_one_line_naming_each(diff_tool, fallback):
    rules = rules_of(fallback, "pbox")
    rules["buildings"]["pbox"].update(cost=400, hp=None, prerequisites=["TENT"])

    lines = diff_tool.differences(table_of(**rules), ItemTable([fallback["pbox"]]))

    assert lines == ["pbox: cost 400 (captured) / 600 (items.toml); hp not given (captured) / 40000 (items.toml)"]


def test_the_order_and_case_of_prerequisites_and_the_case_of_names_make_no_difference(diff_tool, fallback):
    rules = rules_of(fallback, "atek")
    rules["buildings"] = {"ATEK": {**rules["buildings"]["atek"], "prerequisites": ["Dome", "WEAP"]}}

    assert diff_tool.differences(table_of(**rules), ItemTable([fallback["atek"]])) == []


def test_items_only_one_of_the_two_holds_are_listed_and_the_lines_are_in_name_order(diff_tool, fallback):
    rules = rules_of(fallback, "pbox", "e1")
    rules["units"]["zzz_new"] = {"cost": 50}
    rules["units"]["e1"]["cost"] = 120
    ours = ItemTable([fallback["pbox"], fallback["e1"], fallback["apwr"]])

    assert diff_tool.differences(table_of(**rules), ours) == [
        "apwr: only in items.toml",
        "e1: cost 120 (captured) / 100 (items.toml)",
        "zzz_new: only in the captured file",
    ]


def test_facts_that_are_not_compared_do_not_count_as_a_difference(diff_tool, fallback):
    rules = rules_of(fallback, "e1")
    rules["units"]["e1"].update(speed=3.0, range=9.0, queue="Vehicle", factions=["russia"], build_time_s=99)

    assert diff_tool.differences(table_of(**rules), ItemTable([fallback["e1"]])) == []


def test_a_captured_file_is_read_as_the_table_of_its_rules(diff_tool, tmp_path):
    path = write_captured(tmp_path, GAME_CONFIG)

    table = diff_tool.captured_table(path)

    assert [(item.name, item.cost, item.is_building) for item in table] == [("pbox", 400, True), ("e1", 100, False)]


@pytest.mark.parametrize(
    "content, message",
    [
        ("{not json", "cannot be read as a captured game reference"),
        ("[1, 2]", "it has no rules object"),
        ('{"rules": []}', "it has no rules object"),
        ('{"match_rules": {}}', "it has no rules object"),
    ],
    ids=["not json", "not an object", "rules of the wrong kind", "no rules"],
)
def test_a_file_that_is_not_a_captured_game_reference_is_refused(diff_tool, tmp_path, content, message):
    path = tmp_path / "rules-20261009.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(diff_tool.UnreadableRulesError, match=message):
        diff_tool.captured_table(path)


def test_a_missing_captured_file_is_refused(diff_tool, tmp_path):
    with pytest.raises(diff_tool.UnreadableRulesError, match="cannot be read as a captured game reference"):
        diff_tool.captured_table(tmp_path / "missing.json")


def test_diff_main_prints_each_difference_and_exits_with_1(diff_tool, root_with_items_file, capsys):
    path = write_captured(root_with_items_file, GAME_CONFIG)

    exit_code = diff_tool.main([str(path)])

    printed = capsys.readouterr().out.splitlines()
    assert exit_code == 1
    assert "pbox: cost 400 (captured) / 600 (items.toml)" in printed
    assert "powr: only in items.toml" in printed and all(": " in line for line in printed)
    assert not any(line.startswith("e1:") for line in printed)


def test_diff_main_says_so_when_nothing_differs_and_exits_with_0(diff_tool, fallback, root_with_items_file, capsys):
    path = write_captured(root_with_items_file, {"rules": rules_of(fallback, *(item.name for item in fallback))})

    exit_code = diff_tool.main([str(path)])

    assert exit_code == 0
    assert capsys.readouterr().out == f"No item differs between {path} and config/redalert/items.toml.\n"


def test_diff_main_exits_with_2_when_the_captured_file_or_items_toml_cannot_be_read(
    diff_tool, root_with_items_file, capsys
):
    unreadable = diff_tool.main([str(root_with_items_file / "missing.json")])
    first_error = capsys.readouterr().err
    (root_with_items_file / ITEMS_FILE).unlink()
    no_fallback = diff_tool.main([str(write_captured(root_with_items_file, GAME_CONFIG))])
    second_error = capsys.readouterr().err

    assert (unreadable, no_fallback) == (2, 2)
    assert "cannot be read as a captured game reference" in first_error
    assert "items.toml was not found" in second_error


def test_a_capture_of_a_fake_match_s_game_config_does_not_differ_from_items_toml(
    capture_tool, run_script, root_with_items_file
):
    record_folder = root_with_items_file / "runs" / "20261009" / "session__Multi0"
    folder = write_record(record_folder, [event_line(0, "game_config", FakeRedAlert().game_config)])
    captured_file = capture_tool.capture(folder)

    completed = run_script(root_with_items_file, "diff_rules.py", str(captured_file))

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout == f"No item differs between {captured_file} and config/redalert/items.toml.\n"


def test_diff_run_as_a_program_without_a_file_exits_with_2(run_script, tmp_path):
    completed = run_script(tmp_path, "diff_rules.py")

    assert completed.returncode == 2
    assert "the following arguments are required: captured_file" in completed.stderr
