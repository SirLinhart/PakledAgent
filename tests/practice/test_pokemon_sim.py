"""The local Pokémon Showdown: the battle helper `tools/pokemon_sim/sim.js` on Node, and whole practice matches on it.

Every test here is marked `pokemon_sim` and left out of a default run. Run
them after `npm ci` in `tools/pokemon_sim`:

    pytest -m pokemon_sim tests/practice/test_pokemon_sim.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SIM_FOLDER = REPO_ROOT / "tools" / "pokemon_sim"
SIM_SCRIPT = SIM_FOLDER / "sim.js"
DEX_SCRIPT = SIM_FOLDER / "dex.js"
FORMAT = "gen9vgc2025regi"
SEED = "1,2,3,4"
ANSWER_TIMEOUT_SECONDS = 30
MAX_CHOICES = 1000
STATS_FILE = Path("docs") / "reference" / "research" / "pokemon" / "smogon-stats" / "gen9vgc2025regi-1630.json"
CATALOG_FILE = Path("config") / "pokemon" / "practice_cards.json"

pytestmark = pytest.mark.pokemon_sim

TEAM = [
    {
        "species": "Incineroar",
        "item": "Sitrus Berry",
        "ability": "Intimidate",
        "nature": "Careful",
        "level": 50,
        "evs": {"hp": 252, "spd": 252},
        "moves": ["Fake Out", "Flare Blitz", "Knock Off", "Parting Shot"],
    },
    {
        "species": "Urshifu-Rapid-Strike",
        "item": "Mystic Water",
        "ability": "Unseen Fist",
        "nature": "Adamant",
        "level": 50,
        "evs": {"atk": 252, "spe": 252},
        "moves": ["Surging Strikes", "Close Combat", "Aqua Jet", "Protect"],
    },
    {
        "species": "Amoonguss",
        "item": "Rocky Helmet",
        "ability": "Regenerator",
        "nature": "Bold",
        "level": 50,
        "evs": {"hp": 252, "def": 252},
        "moves": ["Spore", "Rage Powder", "Pollen Puff", "Protect"],
    },
    {
        "species": "Rillaboom",
        "item": "Assault Vest",
        "ability": "Grassy Surge",
        "nature": "Adamant",
        "level": 50,
        "evs": {"hp": 252, "atk": 252},
        "moves": ["Fake Out", "Grassy Glide", "Wood Hammer", "U-turn"],
    },
    {
        "species": "Flutter Mane",
        "item": "Booster Energy",
        "ability": "Protosynthesis",
        "nature": "Timid",
        "level": 50,
        "evs": {"spa": 252, "spe": 252},
        "moves": ["Moonblast", "Shadow Ball", "Dazzling Gleam", "Protect"],
    },
    {
        "species": "Zamazenta-Crowned",
        "item": "Rusted Shield",
        "ability": "Dauntless Shield",
        "nature": "Impish",
        "level": 50,
        "evs": {"hp": 252, "def": 252},
        "moves": ["Body Press", "Heavy Slam", "Wide Guard", "Protect"],
    },
]


@pytest.fixture(scope="module", autouse=True)
def showdown_installed() -> None:
    """Node is on the path and `npm ci` has installed pokemon-showdown; otherwise these tests cannot run at all."""
    if shutil.which("node") is None:
        pytest.fail("Node is not on the path: install Node 22.18 or later")
    if not (SIM_FOLDER / "node_modules" / "pokemon-showdown" / "package.json").is_file():
        pytest.fail("pokemon-showdown is not installed: run `npm ci` in tools/pokemon_sim")


class RawHelper:
    """`sim.js` driven by hand over its pipes: one JSON command line in, one JSON answer line out."""

    def __init__(self) -> None:
        self.process = subprocess.Popen(
            ["node", str(SIM_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )

    def send(self, command: dict[str, Any]) -> dict[str, Any]:
        self.process.stdin.write(json.dumps(command) + "\n")
        self.process.stdin.flush()
        return json.loads(self.process.stdout.readline())

    def close(self) -> None:
        self.process.stdin.close()
        self.process.wait(timeout=ANSWER_TIMEOUT_SECONDS)


@pytest.fixture
def raw_helper() -> Iterator[RawHelper]:
    helper = RawHelper()
    yield helper
    helper.close()


def start(helper: RawHelper) -> dict[str, Any]:
    players = {"p1": {"name": "alpha", "team": TEAM}, "p2": {"name": "beta", "team": TEAM}}
    return helper.send({"command": "start", "format": FORMAT, "seed": SEED, "players": players})


# -- sim.js ----------------------------------------------------------------------------------------


def test_start_answers_both_sides_with_a_team_preview_request(raw_helper):
    answer = start(raw_helper)

    assert answer["ok"] is True and answer["ended"] is False and answer["winner"] is None
    for side in ("p1", "p2"):
        request = answer["sides"][side]["request"]
        assert request["teamPreview"] is True and request["maxChosenTeamSize"] == 4
        assert [pokemon["ident"] for pokemon in request["side"]["pokemon"]][:2] == [
            f"{side}: Incineroar",
            f"{side}: Urshifu",
        ]
        assert "|teampreview|4" in answer["sides"][side]["log"]


def test_each_side_sees_its_own_pokemon_s_exact_hp_and_the_other_side_s_out_of_100(raw_helper):
    start(raw_helper)
    raw_helper.send({"command": "choose", "side": "p1", "choice": "team 2156"})
    answer = raw_helper.send({"command": "choose", "side": "p2", "choice": "team 1234"})

    p1_log, p2_log = answer["sides"]["p1"]["log"], answer["sides"]["p2"]["log"]
    assert "|switch|p1a: Urshifu|Urshifu-Rapid-Strike, L50, F|175/175" in p1_log
    assert "|switch|p1a: Urshifu|Urshifu-Rapid-Strike, L50, F|100/100" in p2_log
    assert not any(line.startswith("|split|") for line in p1_log + p2_log)
    assert answer["sides"]["p1"]["request"]["active"][0]["moves"][0]["id"] == "surgingstrikes"


def test_a_choice_the_simulator_refuses_comes_back_as_that_side_s_error(raw_helper):
    start(raw_helper)
    raw_helper.send({"command": "choose", "side": "p1", "choice": "team 1234"})
    raw_helper.send({"command": "choose", "side": "p2", "choice": "team 1234"})

    answer = raw_helper.send({"command": "choose", "side": "p1", "choice": "move 1 3, move 1 1"})

    assert answer["ok"] is True
    assert answer["sides"]["p1"]["error"].startswith("[Invalid choice]")
    assert answer["sides"]["p2"]["error"] is None


def test_a_command_that_cannot_run_answers_not_ok_and_the_helper_keeps_going(raw_helper):
    refused = raw_helper.send({"command": "choose", "side": "p1", "choice": "move 1"})
    assert refused["ok"] is False and "not started" in refused["error"]

    assert start(raw_helper)["ok"] is True


def test_a_battle_of_default_choices_ends_with_a_winner_name(raw_helper):
    answer = start(raw_helper)
    requests = {side: answer["sides"][side]["request"] for side in ("p1", "p2")}
    for _choice in range(MAX_CHOICES):
        if answer["ended"]:
            break
        side = next(side for side, request in requests.items() if request and not request.get("wait"))
        requests[side] = None
        # `default` is Showdown's own choice for a side: its first option in every slot.
        answer = raw_helper.send({"command": "choose", "side": side, "choice": "default"})
        assert answer["ok"] is True and answer["sides"][side]["error"] is None
        requests.update({side: seen["request"] for side, seen in answer["sides"].items() if seen["request"]})
    assert answer["ended"] is True
    assert answer["winner"] in ("alpha", "beta")
    assert any(line.startswith("|win|") for line in answer["sides"]["p1"]["log"])


# -- dex.js and the card build ---------------------------------------------------------------------


def test_dex_lookup_names_species_moves_and_validates_sets():
    query = {
        "format": FORMAT,
        "species": ["Urshifu-Rapid-Strike", "Nope"],
        "moves": ["fakeout", "struggle"],
        "items": ["sitrusberry"],
        "abilities": ["intimidate"],
        "natures": ["Adamant"],
        "sets": [TEAM[0], {**TEAM[0], "moves": ["Fake Out", "Spore", "Knock Off", "Parting Shot"]}],
    }
    completed = subprocess.run(
        ["node", str(DEX_SCRIPT)], input=json.dumps(query), capture_output=True, text=True, encoding="utf-8"
    )
    answer = json.loads(completed.stdout)

    urshifu = answer["species"]["Urshifu-Rapid-Strike"]
    assert urshifu["types"] == ["Fighting", "Water"] and urshifu["base_stats"]["spe"] == 97
    assert urshifu["formes"] == ["Urshifu", "Urshifu-Rapid-Strike"]
    assert answer["species"]["Nope"] == {"exists": False}
    assert answer["moves"]["fakeout"]["priority"] == 3 and answer["moves"]["struggle"]["accuracy"] is True
    assert answer["items"]["sitrusberry"]["name"] == "Sitrus Berry"
    assert answer["problems"][0] is None
    assert answer["problems"][1] == ["Incineroar can't learn Spore."]


def test_the_card_build_reproduces_the_tracked_catalog(tmp_path, run_script):
    if not (REPO_ROOT / STATS_FILE).is_file():
        pytest.skip(f"the untracked usage statistics {STATS_FILE} are not on this machine")
    output = tmp_path / "practice_cards.json"

    completed = run_script(REPO_ROOT, "build_pokemon_cards.py", "--output", str(output))

    assert completed.returncode == 0, completed.stderr
    assert "wrote 72 cards" in completed.stdout
    # Text, not bytes: a checkout may turn the tracked file's line endings into CRLF.
    assert output.read_text(encoding="utf-8") == (REPO_ROOT / CATALOG_FILE).read_text(encoding="utf-8")


# -- the bridge on the real helper -----------------------------------------------------------------


def test_the_bridge_s_default_helper_is_showdown_and_answers_in_its_shapes():
    from ucla_agent.practice.pokemon.bridge import BattleHelper

    with BattleHelper() as helper:
        players = {"p1": {"name": "alpha", "team": TEAM}, "p2": {"name": "beta", "team": TEAM}}
        preview = helper.start(FORMAT, SEED, players)
        helper.choose("p1", "team 2156")
        begun = helper.choose("p2", "team 1234")
        refused = helper.choose("p1", "move 1 3, move 1 1")

    assert preview.sides["p1"].request["teamPreview"] is True
    assert "|turn|1" in begun.sides["p1"].log
    assert begun.sides["p2"].request["active"][0]["moves"][0]["id"] == "fakeout"
    assert refused.sides["p1"].error == "[Invalid choice] Can't move: Invalid target for Surging Strikes"


# -- whole practice matches on real Showdown -------------------------------------------------------

REAL_MATCHES = 5
MATCH_SECONDS_LIMIT = 60


@pytest.mark.parametrize("seed", range(1, REAL_MATCHES + 1))
def test_a_real_showdown_match_of_first_against_random_ends_with_a_winner_in_under_60_seconds(
    seed, tmp_path, monkeypatch
):
    from ucla_agent.core import paths
    from ucla_agent.practice.pokemon import PolicyAgent, play_practice_match

    monkeypatch.setenv(paths.RUNS_DIR_ENV, str(tmp_path))
    started = time.perf_counter()

    result = play_practice_match(seed, lambda: PolicyAgent("first"), "random")

    seconds = time.perf_counter() - started
    print(
        f"seed {seed}: winner {result.winner}, turns {result.turns}, decisions {result.decisions}, "
        f"{seconds:.1f} s, error {result.error}"
    )
    assert result.unhandled_error is False, result.error
    assert result.winner in ("practice-p1", "practice-p2")
    assert result.termination_reason == "completed" and result.turns > 1
    assert seconds < MATCH_SECONDS_LIMIT
