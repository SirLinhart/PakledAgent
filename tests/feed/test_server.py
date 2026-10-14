"""The feed server: the page and the event replies on 127.0.0.1, following a record as it grows, redacted."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterator

import httpx
import pytest

from feed_records import append_line, event_line, model_io, plan_decision, red_alert_events
from ucla_agent.core import paths
from ucla_agent.feed import __main__ as command
from ucla_agent.feed.server import FeedServer, FeedView
from ucla_agent.feed.sources import FixedMatch, LatestMatch, Source

REPO_ROOT = Path(__file__).resolve().parents[2]
EARLY = "2026-10-10T18:00:00.000Z"
LATER = "2026-10-10T18:30:00.000Z"
SERVER_START_SECONDS = 20.0


@pytest.fixture
def serve() -> Iterator[Callable[[Source], str]]:
    """Serves a source on a free loopback port in this process; gives the server's base URL."""
    servers: list[FeedServer] = []

    def start(source: Source) -> str:
        server = FeedServer(0, FeedView(source))
        threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.port}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _get(url: str) -> httpx.Response:
    return httpx.get(url, timeout=5.0, trust_env=False)


def _events(base: str, since: int = 0, match: str | None = None) -> dict:
    query = f"since={since}" + ("" if match is None else f"&match={match}")
    return _get(f"{base}/events?{query}").json()


# -- acceptance 1: every non-state event in seq order, and only the later ones -----------------------


def test_since_0_gives_every_non_state_event_in_record_order_with_decisions_holding_their_io(write_match, serve):
    events = red_alert_events()
    base = serve(FixedMatch(write_match([events])))

    reply = _events(base)

    non_state = [(kind, data) for kind, data in events if kind != "state"]
    assert [event["seq"] for event in reply["events"]] == list(range(1, len(non_state) + 1))
    assert [(event["kind"], event["data"]) for event in reply["events"]] == non_state
    decisions = [event["data"] for event in reply["events"] if event["kind"] == "decision"]
    assert decisions[0]["io"] == model_io("Choose the plan of our Red Alert army.")
    assert decisions[0]["model_reason"] == "their harvesters are open"
    assert (reply["match"], reply["game_type"], reply["reset"], reply["last_seq"]) == (
        "game1-sidestep-1__Multi0",
        "red_alert",
        False,
        11,
    )


def test_since_n_gives_only_the_later_events(write_match, serve):
    base = serve(FixedMatch(write_match([red_alert_events()])))

    reply = _events(base, since=8, match="game1-sidestep-1__Multi0")

    assert [(event["seq"], event["kind"]) for event in reply["events"]] == [
        (9, "decision"),
        (10, "fallback"),
        (11, "error"),
    ]
    assert _events(base, since=11)["events"] == []


# -- acceptance 2: a line appended while the server runs ---------------------------------------------


def test_a_line_appended_to_the_events_file_while_the_server_runs_is_in_the_next_poll(write_match, serve):
    folder = write_match([red_alert_events()])
    base = serve(FixedMatch(folder))
    first = _events(base)

    append_line(folder, event_line(14, "decision", plan_decision(4600, answer="HOLD")))
    second = _events(base, since=first["last_seq"], match=first["match"])

    assert [(event["seq"], event["kind"], event["data"]["answer"]) for event in second["events"]] == [
        (12, "decision", "HOLD")
    ]
    assert second["last_seq"] == 12 and second["reset"] is False


# -- acceptance 3: --latest switches to a newer match folder -----------------------------------------


def test_latest_switches_to_a_newer_match_folder_created_during_the_run(write_match, serve, tmp_path):
    write_match([red_alert_events()], name="first__Multi0", started_at=EARLY)
    base = serve(LatestMatch(tmp_path / "runs"))
    first = _events(base)

    write_match(
        [[("game_config", {}), ("plan_change", {"new": "OPENING", "tick": 5})]], name="next__Multi0", started_at=LATER
    )
    second = _events(base, since=first["last_seq"], match=first["match"])

    assert (first["match"], first["last_seq"]) == ("first__Multi0", 11)
    assert (second["match"], second["reset"], second["last_seq"]) == ("next__Multi0", True, 2)
    assert [(event["seq"], event["kind"]) for event in second["events"]] == [(1, "game_config"), (2, "plan_change")]


def test_latest_with_no_match_yet_answers_that_it_waits(serve, tmp_path):
    reply = _events(serve(LatestMatch(tmp_path / "runs")))

    assert reply == {
        "match": None,
        "game_type": None,
        "reset": False,
        "events": [],
        "last_seq": 0,
        "header": {},
        "throughput": None,
    }


# -- acceptance 4: the header fields from the latest state summary -----------------------------------


def test_the_reply_carries_the_header_of_the_latest_state_summary(write_match, serve):
    base = serve(FixedMatch(write_match([red_alert_events()])))

    header = _events(base)["header"]

    assert header == {
        "game_type": "red_alert",
        "result": None,
        "game_seconds": 176.0,
        "remaining_seconds": 1024.0,
        "cash": 2800,
        "trade_balance": 900,
        "army_value": 4100,
        "enemy_army_value": 2600,
        "harvesters": 2,
        "explored_percent": 23.4,
        "plan": "RAID",
    }


def test_a_red_alert_reply_carries_the_throughput_of_the_match_and_its_chart_series(write_match, serve):
    base = serve(FixedMatch(write_match([red_alert_events()])))

    throughput = _events(base)["throughput"]

    assert throughput["figures"]["match"]["batches"] == 2 and throughput["figures"]["match"]["orders"] == 1
    assert throughput["figures"]["match"]["seconds"] == pytest.approx(176.0)
    assert throughput["series"][-1]["second"] == 176


def test_the_reply_of_another_game_has_its_events_and_result_and_no_red_alert_figures(write_match, serve):
    events = [("state", {"state_version": 3}), ("decision", {"source": "local:strong", "answer": "p2"})]
    base = serve(FixedMatch(write_match([events], name="ww__seat_2", game_type="werewolf")))

    reply = _events(base)

    assert [event["kind"] for event in reply["events"]] == ["decision"]
    assert reply["header"] == {"game_type": "werewolf", "result": None} and reply["throughput"] is None


# -- acceptance 5: the page ---------------------------------------------------------------------------


def test_the_page_is_one_html_document_that_refers_to_no_external_host(write_match, serve):
    response = _get(serve(FixedMatch(write_match([red_alert_events()]))) + "/")

    page = response.text
    assert (response.status_code, response.headers["content-type"]) == (200, "text/html; charset=utf-8")
    assert page.startswith("<!doctype html>")
    assert (page.count("<html"), page.count("<script>")) == (1, 1)
    assert [marker for marker in ("http://", "https://", "src=", "<link") if marker in page] == []


# -- acceptance 6: 127.0.0.1 only ---------------------------------------------------------------------


def _other_addresses_of_this_machine() -> list[str]:
    """The IPv4 addresses of this machine that are not loopback addresses."""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        return []
    return sorted(address for address in addresses if not address.startswith("127."))


def test_the_server_binds_127_0_0_1_only(write_match):
    server = FeedServer(0, FeedView(FixedMatch(write_match([red_alert_events()]))))
    try:
        assert server.socket.getsockname()[0] == "127.0.0.1"
        for address in _other_addresses_of_this_machine():
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(1.0)
                assert probe.connect_ex((address, server.port)) != 0, address
    finally:
        server.server_close()


# -- acceptance 7: nothing served holds a secret --------------------------------------------------------


def test_a_record_holding_a_key_in_a_field_is_served_redacted(write_match, serve, monkeypatch):
    # Built in parts so that no tracked file holds a key-shaped string.
    env_key_value = "feed-test-" + "4f1c9a7e2b3d"
    shaped_key = "eak_" + "live_" + "0123456789abcdef"
    monkeypatch.setenv("UCLA_FEED_TEST_API_KEY", env_key_value)
    leaking = plan_decision(4075)
    leaking["io"] = {**leaking["io"], "user": f"cash=3200 key={env_key_value}", "raw_output": shaped_key}
    folder = write_match([[("decision", leaking), ("error", {"message": f"auth failed for {env_key_value}"})]])
    base = serve(FixedMatch(folder))

    events_bytes = _get(f"{base}/events?since=0").content
    page_bytes = _get(f"{base}/").content

    for served in (events_bytes, page_bytes):
        assert env_key_value.encode() not in served and shaped_key.encode() not in served
    assert events_bytes.count(b"[REDACTED]") == 3


# -- what else it answers, and that it writes nothing --------------------------------------------------


def test_a_since_that_is_not_a_number_is_a_bad_request_and_other_routes_are_not_found(write_match, serve):
    base = serve(FixedMatch(write_match([red_alert_events()])))

    assert _get(f"{base}/events?since=last").status_code == 400
    assert _get(f"{base}/model").status_code == 404
    assert httpx.post(f"{base}/events", timeout=5.0, trust_env=False).status_code == 501


def test_serving_a_record_writes_nothing_under_the_runs_folder(write_match, serve, tmp_path):
    write_match([red_alert_events()])
    runs = tmp_path / "runs"
    before = {path.relative_to(runs): path.read_bytes() for path in runs.rglob("*") if path.is_file()}
    base = serve(LatestMatch(runs))

    _get(f"{base}/")
    for since in (0, 5, 11):
        _events(base, since=since)

    assert {path.relative_to(runs): path.read_bytes() for path in runs.rglob("*") if path.is_file()} == before


# -- the command ----------------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _environment(root: Path) -> dict[str, str]:
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src"), paths.ROOT_ENV: str(root)}
    environment.pop(paths.RUNS_DIR_ENV, None)
    return environment


def test_the_command_serves_the_newest_match_of_the_runs_folder_it_is_given(write_match, tmp_path):
    write_match([red_alert_events()])
    port = _free_port()
    command = [
        sys.executable,
        "-m",
        "ucla_agent.feed",
        "--latest",
        "--runs-dir",
        str(tmp_path / "runs"),
        "--port",
        str(port),
    ]
    feed = subprocess.Popen(
        command, env=_environment(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        reply = _wait_for_reply(f"http://127.0.0.1:{port}/events?since=0", feed)
    finally:
        feed.terminate()
        feed.wait(timeout=10)

    assert reply["match"] == "game1-sidestep-1__Multi0" and reply["last_seq"] == 11
    assert f"Feed: http://127.0.0.1:{port}/ shows the newest match under {tmp_path / 'runs'}" in feed.stdout.read()


def _wait_for_reply(url: str, process: subprocess.Popen) -> dict:
    deadline = time.monotonic() + SERVER_START_SECONDS
    while time.monotonic() < deadline:
        assert process.poll() is None, process.stderr.read()
        try:
            return httpx.get(url, timeout=1.0, trust_env=False).json()
        except httpx.TransportError:
            time.sleep(0.1)
    pytest.fail(f"the feed did not answer {url} within {SERVER_START_SECONDS} s")


def _stopped_at_once(served: list[FeedServer]) -> Callable[[FeedServer, float], None]:
    """A serving loop that records its server and stops as Ctrl+C would."""

    def interrupted(server: FeedServer, _poll: float) -> None:
        served.append(server)
        raise KeyboardInterrupt

    return interrupted


def test_the_command_serves_until_ctrl_c_and_closes_its_port(write_match, monkeypatch, capsys):
    served: list[FeedServer] = []
    monkeypatch.setattr(FeedServer, "serve_forever", _stopped_at_once(served))
    folder = write_match([red_alert_events()])

    exit_code = command.main(["--match", str(folder), "--port", "0"])

    printed = capsys.readouterr().out
    assert exit_code == 0 and served[0].socket.fileno() == -1
    assert f"shows the match in {folder}" in printed and "Feed stopped." in printed


def test_the_command_follows_the_runs_folder_the_environment_names_by_default(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(paths.RUNS_DIR_ENV, str(tmp_path / "watch" / "records"))
    served: list[FeedServer] = []
    monkeypatch.setattr(FeedServer, "serve_forever", _stopped_at_once(served))

    exit_code = command.main(["--port", "0"])

    assert exit_code == 0 and len(served) == 1
    assert f"shows the newest match under {tmp_path / 'watch' / 'records'}" in capsys.readouterr().out


def test_the_command_refuses_a_match_folder_that_does_not_exist(tmp_path, capsys):
    exit_code = command.main(["--match", str(tmp_path / "nowhere")])

    assert exit_code == 2 and f"There is no match folder at {tmp_path / 'nowhere'}." in capsys.readouterr().err


def test_the_command_says_so_when_its_port_is_taken(write_match, capsys):
    folder = write_match([red_alert_events()])
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port = holder.getsockname()[1]
        exit_code = command.main(["--match", str(folder), "--port", str(port)])

    assert exit_code == 1 and f"The feed cannot listen on 127.0.0.1:{port}" in capsys.readouterr().err
