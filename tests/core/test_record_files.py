"""The naming rule of a match record: one folder per seat, one events file and one meta file per attempt."""

from __future__ import annotations

import pytest

from ucla_agent.core.record_files import (
    attempt_of_events_file,
    events_file_name,
    match_folder_name,
    meta_file_name,
    seat_label,
)


@pytest.mark.parametrize(
    "names, label",
    [
        (("Multi0", 1), "Multi0"),
        ((None, 1), "1"),
        ((None, 0), "0"),
        (("", None), "seat"),
        ((None, None), "seat"),
        ((), "seat"),
        (("North/East seat #2", 0), "North_East_seat__2"),
        (("../..", None), "_____"),
        (("seat-2_b", None), "seat-2_b"),
    ],
    ids=[
        "the first name wins",
        "a missing name is passed over",
        "zero is a name",
        "an empty name says nothing",
        "no name",
        "no candidate",
        "unsafe characters",
        "parent folders",
        "hyphen and underscore stay",
    ],
)
def test_the_seat_label_is_the_first_name_given_made_safe_for_a_folder_name(names, label):
    assert seat_label(*names) == label


@pytest.mark.parametrize(
    "session_id, seat, folder_name",
    [
        ("6f1c2a9e-77b0-4d3c-9a51-0c8f3e2b1d44", "Multi0", "6f1c2a9e-77b0-4d3c-9a51-0c8f3e2b1d44__Multi0"),
        ("session-1", "seat", "session-1__seat"),
        ("../../outside", "1", "______outside__1"),
        ("", "Multi1", "unknown-session__Multi1"),
    ],
    ids=["platform id", "unnamed seat", "parent folders", "empty session id"],
)
def test_a_match_folder_is_named_by_session_and_seat(session_id, seat, folder_name):
    assert match_folder_name(session_id, seat) == folder_name


def test_two_seats_of_one_session_have_two_folder_names():
    assert match_folder_name("shared", seat_label("Multi0")) != match_folder_name("shared", seat_label("Multi1"))


@pytest.mark.parametrize(
    "attempt, events_name, meta_name",
    [(1, "events.jsonl", "meta.json"), (2, "events.2.jsonl", "meta.2.json"), (12, "events.12.jsonl", "meta.12.json")],
)
def test_each_attempt_has_its_own_pair_of_file_names(attempt, events_name, meta_name):
    assert (events_file_name(attempt), meta_file_name(attempt)) == (events_name, meta_name)


@pytest.mark.parametrize("attempt", [1, 2, 3, 10, 125])
def test_an_events_file_name_gives_back_its_attempt(attempt):
    assert attempt_of_events_file(events_file_name(attempt)) == attempt


@pytest.mark.parametrize(
    "file_name",
    [
        "meta.json",
        "meta.2.json",
        "metrics.json",
        "run.jsonl",
        "events.1.jsonl",
        "events.0.jsonl",
        "events.02.jsonl",
        "events-backup.jsonl",
        "events.jsonl.tmp",
        "old-events.jsonl",
        "events.2.json",
    ],
)
def test_any_other_name_belongs_to_no_attempt(file_name):
    assert attempt_of_events_file(file_name) is None
