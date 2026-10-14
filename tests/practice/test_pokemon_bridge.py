"""The battle helper bridge over the fake battle helper: the JSON-lines protocol, its answers and its failures.

The fake helper (`tests/stubs/fake_pokemon_sim.py`) speaks the protocol of
`tools/pokemon_sim/sim.js` over a made-up battle, so none of this needs
Node; `test_pokemon_sim.py` runs the same protocol against real Showdown.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

from ucla_agent.practice.pokemon import bridge
from ucla_agent.practice.pokemon.bridge import BattleHelper, BattleHelperError, HelperUpdate, parse_answer
from ucla_agent.practice.pokemon.cards import load_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_HELPER = [sys.executable, str(REPO_ROOT / "tests" / "stubs" / "fake_pokemon_sim.py")]
FORMAT = "gen9vgc2025regi"
SEED = "1,2,3,4"
MAX_CHOICES = 2000
CHOOSABLE_FIRST_TARGET = {"normal": 1, "any": 1, "adjacentFoe": 1, "adjacentAlly": None, "adjacentAllyOrSelf": None}

P1_CARDS = ["vgc-incineroar", "vgc-urshifu-rapid-strike", "vgc-amoonguss", "vgc-rillaboom", "vgc-whimsicott"]
P2_CARDS = ["vgc-miraidon", "vgc-flutter-mane", "vgc-kyogre", "vgc-chien-pao", "vgc-farigiraf", "vgc-tornadus"]


def players() -> dict[str, Any]:
    catalog = load_catalog()
    teams = {"p1": P1_CARDS + ["vgc-grimmsnarl"], "p2": P2_CARDS}
    return {
        side: {"name": f"seat-{side}", "team": [catalog.card(card_id).showdown_set() for card_id in card_ids]}
        for side, card_ids in teams.items()
    }


@pytest.fixture
def helper() -> Iterator[BattleHelper]:
    with BattleHelper(FAKE_HELPER) as running:
        yield running


def started(helper: BattleHelper) -> HelperUpdate:
    return helper.start(FORMAT, SEED, players())


def first_choice(request: dict[str, Any]) -> str:
    """A choice the fake accepts for any request: the first move at the first foe, else switches into the bench."""
    if request.get("teamPreview"):
        return "team 1234"
    if "forceSwitch" in request:
        return forced_switches(request)
    return ", ".join(slot_move(slot, entry, request) for slot, entry in enumerate(request["active"]))


def slot_move(slot: int, entry: dict[str, Any], request: dict[str, Any]) -> str:
    if request["side"]["pokemon"][slot]["condition"].endswith(" fnt"):
        return "pass"
    target_type = entry["moves"][0]["target"]
    if target_type not in CHOOSABLE_FIRST_TARGET:
        return "move 1"
    target = CHOOSABLE_FIRST_TARGET[target_type] or -(2 - slot)
    return f"move 1 {target}"


def forced_switches(request: dict[str, Any]) -> str:
    bench = [
        number
        for number, mon in enumerate(request["side"]["pokemon"], start=1)
        if number > 2 and not mon["condition"].endswith(" fnt")
    ]
    parts = []
    for flagged in request["forceSwitch"]:
        parts.append(f"switch {bench.pop(0)}" if flagged and bench else "pass")
    return ", ".join(parts)


def play_out(helper: BattleHelper, update: HelperUpdate) -> tuple[HelperUpdate, dict[str, list[str]]]:
    """Answer every request with `first_choice` until the battle ends; the last update and every side's whole log."""
    logs = {side: list(update.sides[side].log) for side in bridge.SIDES}
    requests = {side: update.sides[side].request for side in bridge.SIDES}
    for _choice in range(MAX_CHOICES):
        if update.ended:
            break
        side = next(side for side, request in requests.items() if request and not request.get("wait"))
        update = helper.choose(side, first_choice(requests.pop(side)))
        assert update.sides[side].error is None
        take_in(update, logs, requests)
    return update, logs


def take_in(update: HelperUpdate, logs: dict[str, list[str]], requests: dict[str, Any]) -> None:
    for side, seen in update.sides.items():
        logs[side].extend(seen.log)
        if seen.request is not None:
            requests[side] = seen.request


# -- the protocol over the fake helper -------------------------------------------------------------


def test_start_gives_both_sides_a_team_preview_request_and_the_roster_lines(helper):
    update = started(helper)

    assert update.ended is False and update.winner is None
    for side in bridge.SIDES:
        seen = update.sides[side]
        assert seen.request["teamPreview"] is True and seen.request["maxChosenTeamSize"] == 4
        assert len(seen.request["side"]["pokemon"]) == 6
        assert "|teampreview|4" in seen.log
        assert seen.error is None
    assert "|poke|p2|Miraidon, L50|" in update.sides["p1"].log


def test_the_battle_starts_once_both_sides_chose_their_team(helper):
    started(helper)

    waiting = helper.choose("p1", "team 2134")
    begun = helper.choose("p2", "team 1234")

    assert all(seen.log == () and seen.request is None for seen in waiting.sides.values())
    p1_log, p2_log = begun.sides["p1"].log, begun.sides["p2"].log
    assert any(line.startswith("|switch|p1a: Urshifu-Rapid-Strike|") for line in p1_log)
    assert "|turn|1" in p1_log and "|turn|1" in p2_log
    move_request = begun.sides["p1"].request
    assert [entry["moves"][0]["id"] for entry in move_request["active"]] == ["surgingstrikes", "fakeout"]
    assert [mon["ident"] for mon in move_request["side"]["pokemon"]][:2] == [
        "p1: Urshifu-Rapid-Strike",
        "p1: Incineroar",
    ]
    assert len(move_request["side"]["pokemon"]) == 4


def test_a_side_sees_its_own_hp_exactly_and_the_other_side_s_out_of_100(helper):
    started(helper)
    helper.choose("p1", "team 1234")
    begun = helper.choose("p2", "team 1234")

    own = next(line for line in begun.sides["p1"].log if line.startswith("|switch|p1a:"))
    seen_by_other = next(line for line in begun.sides["p2"].log if line.startswith("|switch|p1a:"))
    assert own.endswith("|150/150") and seen_by_other.endswith("|100/100")


def test_a_choice_the_fake_would_refuse_comes_back_as_the_side_s_error(helper):
    started(helper)
    helper.choose("p1", "team 1234")
    helper.choose("p2", "team 1234")

    refused = helper.choose("p1", "move 1 3, move 1 1")

    assert refused.sides["p1"].error.startswith("[Invalid choice]")
    assert refused.sides["p2"].error is None


def test_a_whole_fake_battle_ends_with_a_winner_and_a_win_line(helper):
    update, logs = play_out(helper, started(helper))

    assert update.ended is True
    assert update.winner in ("seat-p1", "seat-p2")
    assert f"|win|{update.winner}" in logs["p1"]
    assert any(line.startswith("|faint|") for line in logs["p2"])


# -- failures --------------------------------------------------------------------------------------


def test_an_answer_that_is_not_ok_raises_with_the_helper_s_error():
    with pytest.raises(BattleHelperError, match="refused a command: the battle has not started"):
        parse_answer({"ok": False, "error": "the battle has not started"})


def test_a_helper_that_cannot_be_started_raises():
    with pytest.raises(BattleHelperError, match="cannot start the battle helper"):
        BattleHelper(["no-such-battle-helper-command"])


def test_a_helper_that_exits_without_answering_raises_with_its_stderr():
    script = "import sys; sys.stdin.readline(); sys.stderr.write('helper broke'); sys.stderr.flush()"
    with BattleHelper([sys.executable, "-c", script]) as dying:
        with pytest.raises(BattleHelperError, match="exited; stderr: helper broke"):
            dying.choose("p1", "move 1")


def test_a_helper_that_does_not_answer_in_time_raises_and_is_stopped(monkeypatch):
    monkeypatch.setattr(bridge, "CLOSE_TIMEOUT_SECONDS", 0.1)
    silent = BattleHelper([sys.executable, "-c", "import time; time.sleep(60)"], timeout_seconds=0.2)

    with pytest.raises(BattleHelperError, match=r"no answer within 0\.2 s"):
        silent.choose("p1", "move 1")
    silent.close()

    assert silent._process.poll() is not None


def test_the_default_helper_is_sim_js_on_node_from_the_project():
    command = bridge.default_command()

    assert command[0] == "node"
    assert Path(command[1]) == REPO_ROOT / "tools" / "pokemon_sim" / "sim.js"
