"""The feed's sources: one named match folder, or the newest match under a runs folder, switched to as it appears."""

from __future__ import annotations

import json
from pathlib import Path

from feed_records import red_alert_events
from ucla_agent.core import metrics, paths
from ucla_agent.feed.sources import FixedMatch, LatestMatch, default_runs_folder

EARLY = "2026-10-10T18:00:00.000Z"
LATER = "2026-10-10T18:30:00.000Z"


def test_a_fixed_match_reads_the_folder_it_was_given(write_match):
    source = FixedMatch(write_match([red_alert_events()]))

    read = source.refresh()

    assert len(read) == len(red_alert_events()) and source.reader.last_seq == 11


def test_the_latest_match_is_the_one_whose_first_attempt_started_last_whatever_its_name(write_match, tmp_path):
    write_match([[("game_config", {})]], name="zzz__Multi0", started_at=EARLY)
    newer = write_match([[("game_config", {}), ("error", {})]], name="aaa__Multi0", started_at=LATER)
    source = LatestMatch(tmp_path / "runs")

    source.refresh()

    assert source.reader.folder == newer and source.reader.last_seq == 2


def test_a_newer_match_folder_created_during_the_run_is_switched_to(write_match, tmp_path):
    first = write_match([[("game_config", {})]], name="first__Multi0", started_at=EARLY)
    source = LatestMatch(tmp_path / "runs")
    source.refresh()

    second = write_match([[("game_config", {}), ("error", {})]], name="second__Multi0", started_at=LATER)
    read = source.refresh()

    assert first != second and source.reader.folder == second
    assert [event["kind"] for event in read] == ["game_config", "error"]


def test_the_match_followed_stays_while_no_newer_one_appears(write_match, tmp_path):
    write_match([[("game_config", {})]], started_at=EARLY)
    source = LatestMatch(tmp_path / "runs")
    source.refresh()
    reader = source.reader

    source.refresh()

    assert source.reader is reader


def test_a_match_in_a_later_dated_folder_is_newer(write_match, tmp_path):
    write_match([[("game_config", {})]], name="late-in-the-day__Multi0", day="20261010", started_at=LATER)
    next_day = write_match([[("game_config", {})]], name="just-after-midnight__Multi0", day="20261011")
    source = LatestMatch(tmp_path / "runs")

    source.refresh()

    assert source.reader.folder == next_day


def test_a_dated_folder_without_a_match_is_passed_over(write_match, tmp_path):
    match = write_match([[("game_config", {})]], day="20261010")
    (tmp_path / "runs" / "20261011").mkdir()
    (tmp_path / "runs" / "20261011" / "run.jsonl").write_bytes(b'{"kind": "run_started"}\n')
    source = LatestMatch(tmp_path / "runs")

    source.refresh()

    assert source.reader.folder == match


def test_a_match_folder_without_its_meta_file_yet_waits_until_it_is_written(write_match, tmp_path):
    write_match([[("game_config", {})]], name="older__Multi0", started_at=EARLY)
    newer = write_match([[("game_config", {})]], name="newer__Multi0", started_at=LATER)
    meta_text = (newer / "meta.json").read_bytes()
    (newer / "meta.json").unlink()
    source = LatestMatch(tmp_path / "runs")

    source.refresh()
    before = source.reader.folder.name
    (newer / "meta.json").write_bytes(meta_text)
    source.refresh()

    assert (before, source.reader.folder.name) == ("older__Multi0", "newer__Multi0")


def test_other_folders_under_the_runs_folder_are_not_dated_folders(write_match, tmp_path):
    write_match([[("game_config", {})]], day="reports", name="20261012T000000Z")
    (tmp_path / "runs" / "model-server").mkdir()
    source = LatestMatch(tmp_path / "runs")

    assert source.refresh() == [] and source.reader is None


def test_without_a_runs_folder_there_is_no_match_and_none_is_created(tmp_path):
    source = LatestMatch(tmp_path / "runs")

    assert source.refresh() == [] and source.reader is None
    assert not (tmp_path / "runs").exists()


def test_the_runs_folder_appearing_later_is_followed_from_then_on(write_match, tmp_path):
    source = LatestMatch(tmp_path / "runs")
    source.refresh()

    folder = write_match([[("game_config", {})]])

    assert len(source.refresh()) == 1 and source.reader.folder == folder


def test_each_match_s_meta_file_is_read_once(write_match, tmp_path, monkeypatch):
    write_match([[("game_config", {})]], name="one__Multi0", started_at=EARLY)
    write_match([[("game_config", {})]], name="two__Multi0", started_at=LATER)
    read_meta_calls: list[tuple[str, int]] = []
    real_read_meta = metrics.read_meta

    def counting(folder: Path, attempt: int) -> dict:
        read_meta_calls.append((folder.name, attempt))
        return real_read_meta(folder, attempt)

    monkeypatch.setattr(metrics, "read_meta", counting)
    source = LatestMatch(tmp_path / "runs")
    for _ in range(3):
        source.refresh()

    looked_up_for_the_start = [call for call in read_meta_calls if call[0] == "one__Multi0"]
    assert looked_up_for_the_start == [("one__Multi0", 1)]


def test_the_default_runs_folder_is_runs_under_the_project_root_or_the_folder_the_environment_names(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(paths.ROOT_ENV, str(tmp_path / "project"))
    monkeypatch.delenv(paths.RUNS_DIR_ENV, raising=False)
    under_the_root = default_runs_folder()
    monkeypatch.setenv(paths.RUNS_DIR_ENV, str(tmp_path / "watch" / "records"))

    assert under_the_root == tmp_path / "project" / "runs"
    assert default_runs_folder() == tmp_path / "watch" / "records"


def test_following_the_latest_match_writes_nothing(write_match, tmp_path):
    write_match([red_alert_events()], name="one__Multi0", started_at=EARLY)
    write_match([red_alert_events()], name="two__Multi0", started_at=LATER)
    runs = tmp_path / "runs"
    before = {path: path.read_bytes() for path in runs.rglob("*") if path.is_file()}
    source = LatestMatch(runs)

    source.refresh()
    source.refresh()

    assert {path: path.read_bytes() for path in runs.rglob("*") if path.is_file()} == before
    assert json.loads((runs / "20261010" / "two__Multi0" / "meta.json").read_bytes())["started_at"] == LATER
