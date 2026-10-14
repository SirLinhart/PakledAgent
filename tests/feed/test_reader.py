"""The feed's record reader: every attempt's events file in order, followed as it grows, states kept only as the last."""

from __future__ import annotations

import json

import pytest

from feed_records import append_line, event_line, meta, red_alert_events, thinned_state
from ucla_agent.core import metrics
from ucla_agent.feed.reader import MatchReader


def _kinds(reader: MatchReader, after: int = 0) -> list[str]:
    return [event.kind for event in reader.events_after(after)]


def test_every_event_but_the_states_is_numbered_from_1_in_record_order(write_match):
    events = red_alert_events()
    reader = MatchReader(write_match([events]))

    read = reader.refresh()

    kept = reader.events_after(0)
    assert [event["kind"] for event in read] == [kind for kind, _data in events]
    assert [event.kind for event in kept] == [kind for kind, _data in events if kind != "state"]
    assert [event.seq for event in kept] == list(range(1, len(kept) + 1))
    assert [event.record_seq for event in kept] == [seq for seq, (kind, _) in enumerate(events) if kind != "state"]
    assert reader.last_seq == len(kept) == 11
    assert reader.name == "game1-sidestep-1__Multi0"


def test_events_after_a_seq_are_only_the_later_ones(write_match):
    reader = MatchReader(write_match([red_alert_events()]))
    reader.refresh()

    assert [event.seq for event in reader.events_after(9)] == [10, 11]
    assert reader.events_after(11) == []
    assert [event.seq for event in reader.events_after(-3)] == list(range(1, 12))


def test_the_attempts_are_read_in_attempt_order_and_numbered_on_across_them(write_match):
    first = [("game_config", {}), ("plan_change", {"old": None, "new": "OPENING", "rule": "R1", "tick": 5})]
    second = [("error", {"where": "restart"}), ("plan_change", {"old": "OPENING", "new": "HOLD", "tick": 900})]
    reader = MatchReader(write_match([first, second]))

    reader.refresh()

    kept = reader.events_after(0)
    assert [(event.seq, event.attempt, event.record_seq, event.kind) for event in kept] == [
        (1, 1, 0, "game_config"),
        (2, 1, 1, "plan_change"),
        (3, 2, 0, "error"),
        (4, 2, 1, "plan_change"),
    ]


def test_only_the_latest_state_is_kept(write_match):
    reader = MatchReader(write_match([red_alert_events()]))

    reader.refresh()

    assert reader.latest_state == thinned_state(4400, economy={"cash": 2800, "ore": 150, "harvesters": 2})


def test_a_line_appended_later_is_read_by_the_next_refresh_and_only_by_it(write_match):
    folder = write_match([red_alert_events()])
    reader = MatchReader(folder)
    reader.refresh()

    append_line(folder, event_line(14, "raid", {"event": "broke_off", "tick": 4500}))
    read = reader.refresh()

    assert [event["kind"] for event in read] == ["raid"]
    assert _kinds(reader, after=11) == ["raid"]
    assert reader.refresh() == [] and reader.last_seq == 12


def test_a_line_whose_newline_has_not_been_written_waits_for_it(write_match):
    folder = write_match([red_alert_events()])
    reader = MatchReader(folder)
    reader.refresh()
    line = event_line(14, "raid", {"event": "sent", "tick": 4500})

    append_line(folder, line[:25])
    first = reader.refresh()
    append_line(folder, line[25:])
    second = reader.refresh()

    assert first == [] and [event["kind"] for event in second] == ["raid"]
    assert reader.unreadable_lines == 0


def test_a_line_that_is_not_an_event_is_skipped_and_counted(write_match):
    folder = write_match([[("game_config", {})]])
    append_line(folder, '{"seq": 1, "kind": "rai\n')
    append_line(folder, json.dumps({"seq": 2, "kind": "raid", "data": "not an object"}) + "\n")
    append_line(folder, event_line(3, "raid", {"tick": 50}))
    reader = MatchReader(folder)

    reader.refresh()

    assert _kinds(reader) == ["game_config", "raid"] and reader.unreadable_lines == 2


def test_a_restart_file_that_appears_later_is_read_after_the_first_attempt(write_match):
    folder = write_match([[("game_config", {})]])
    reader = MatchReader(folder)
    reader.refresh()

    (folder / "events.2.jsonl").write_bytes(event_line(0, "error", {"where": "restart"}).encode("utf-8"))
    reader.refresh()

    assert [(event.attempt, event.kind) for event in reader.events_after(0)] == [(1, "game_config"), (2, "error")]


@pytest.mark.parametrize(
    "data, game_seconds",
    [
        ({"game_seconds": 164.0, "tick": 1}, 164.0),
        ({"tick": 4110}, 164.4),
        ({"state_version": "4075"}, 163.0),
        ({"state_version": 4005, "action": "WAIT"}, 160.2),
        ({"state_version": "day-2"}, 160.0),
        ({"where": "redalert.world"}, 160.0),
    ],
    ids=[
        "its own game_seconds",
        "its tick",
        "its state tag",
        "its state version",
        "a tag that is no tick",
        "the last state's tick",
    ],
)
def test_a_red_alert_event_has_its_game_time(write_match, data, game_seconds):
    reader = MatchReader(write_match([[("state", thinned_state(4000)), ("raid", data)]]))

    reader.refresh()

    (event,) = reader.events_after(0)
    assert event.game_seconds == pytest.approx(game_seconds)


def test_an_event_before_any_state_and_the_events_of_other_games_have_no_game_time(write_match, tmp_path):
    red_alert = MatchReader(write_match([[("game_config", {})]]))
    werewolf = MatchReader(
        write_match([[("state", {"state_version": 40}), ("decision", {})]], name="ww__seat", game_type="werewolf")
    )

    red_alert.refresh()
    werewolf.refresh()

    assert [event.game_seconds for event in red_alert.events_after(0)] == [None]
    assert [event.game_seconds for event in werewolf.events_after(0)] == [None]
    assert werewolf.game_type == "werewolf" and red_alert.game_type == "red_alert"


def test_the_meta_file_is_read_once_it_is_on_disk(write_match):
    folder = write_match([[("game_config", {})]])
    meta_text = (folder / "meta.json").read_bytes()
    (folder / "meta.json").unlink()
    reader = MatchReader(folder)

    reader.refresh()
    before = reader.game_type
    (folder / "meta.json").write_bytes(meta_text)
    reader.refresh()

    assert (before, reader.game_type, reader.meta) == (None, "red_alert", meta())


def test_the_event_that_closes_the_record_is_kept_for_the_header(write_match):
    result = {"result": {"your_return": 1.0, "termination_reason": "victory"}, "ended_at": "x", "dropped_events": 0}
    reader = MatchReader(write_match([[("game_config", {}), ("result", result)]]))

    reader.refresh()

    assert reader.closing == {"kind": "result", "data": result}


def test_an_event_as_the_page_gets_it_names_its_number_attempt_record_seq_time_kind_data_and_game_time(write_match):
    reader = MatchReader(write_match([[("plan_change", {"old": "HOLD", "new": "RAID", "game_seconds": 3.5})]]))
    reader.refresh()

    (event,) = reader.events_after(0)

    assert event.as_dict() == {
        "seq": 1,
        "attempt": 1,
        "record_seq": 0,
        "t": "2026-10-10T18:00:00.000Z",
        "kind": "plan_change",
        "data": {"old": "HOLD", "new": "RAID", "game_seconds": 3.5},
        "game_seconds": 3.5,
    }


def test_reading_changes_nothing_in_the_match_folder(write_match):
    folder = write_match([red_alert_events(), [("error", {"where": "restart"})]])
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in folder.iterdir()}
    reader = MatchReader(folder)

    reader.refresh()
    reader.refresh()

    assert {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in folder.iterdir()} == before


def test_metrics_reads_a_record_file_from_an_offset_and_a_meta_file_by_attempt(write_match):
    folder = write_match([[("game_config", {})], [("error", {})]])
    whole = (folder / "events.jsonl").read_bytes()

    assert metrics.read_from(folder / "events.jsonl", 0) == whole
    assert metrics.read_from(folder / "events.jsonl", 10) == whole[10:]
    assert metrics.read_from(folder / "events.jsonl", len(whole)) == b""
    assert metrics.read_meta(folder, 2) == meta(attempt=2)
    assert metrics.read_meta(folder, 3) == {}
