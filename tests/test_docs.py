"""The run instructions, the games status page, the components page and the benchmark record state what they have to."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from switches_off import GROUPS_OFF
from ucla_agent.core import bench, registry, runtime_hooks

DOCS = Path(__file__).resolve().parents[1] / "docs"
TOURNAMENT_GAME_TYPES = {"red_alert", "werewolf", "pokemon_vgc_doubles_draft", "honor_of_kings"}
ALLOWED_STATUSES = {"ready", "in progress", "not started"}
# A status table row: | <game> | `<platform game type>` | `<status>` | ...
STATUS_ROW = re.compile(r"^\|[^|]+\|\s*`(?P<game_type>[a-z_]+)`\s*\|\s*`(?P<status>[^`]+)`\s*\|", re.MULTILINE)
# The chains of `[decide.chains]` that Red Alert asks.
RED_ALERT_CHAINS = ("red_alert_plan", "red_alert_scout", "red_alert_commander")


def _plain(text: str) -> str:
    """The text with markdown emphasis removed and runs of whitespace collapsed, so wrapping does not matter."""
    return " ".join(text.replace("*", "").split())


@pytest.fixture(scope="module")
def running_guide() -> str:
    return _plain((DOCS / "RUNNING.md").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "command",
    ["python -m agent --check-tournament", "python -m agent --match", "python -m agent --tournament --match"],
)
def test_running_guide_gives_the_exact_commands(running_guide, command):
    assert command in running_guide


def test_running_guide_explains_self_play_with_both_seats(running_guide):
    assert "set both seats to Mine (self-hosted)" in running_guide
    assert "Do not start a second copy of the agent for the second seat." in running_guide


def test_running_guide_states_the_hands_off_rule(running_guide):
    rule = "Once a match has begun, no person may change code, configuration, models or prompts, or advise the agent."

    assert rule in running_guide


def test_running_guide_requires_manual_actions_to_be_logged(running_guide):
    rule = (
        "Any manual action during a match must be written to `runs/manual-interventions.md` "
        "with the time and the reason."
    )

    assert rule in running_guide


def test_running_guide_explains_the_single_instance_lock(running_guide):
    assert "Another copy of the agent is already running (pid N)" in running_guide
    assert "exits with code 3" in running_guide
    assert ".agent.lock" in running_guide


@pytest.mark.parametrize(
    "command",
    [
        "python scripts/report.py runs/20261016",
        'python scripts/report.py "runs/20261016/<session id>__<seat>"',
        "python scripts/report.py --compare runs/20261015 runs/20261016",
    ],
    ids=["a dated folder", "a match folder", "compare"],
)
def test_running_guide_gives_the_commands_for_reviewing_a_run(running_guide, command):
    assert "## Reviewing a run" in running_guide
    assert command in running_guide


def test_running_guide_says_where_the_report_tool_writes(running_guide):
    assert "`metrics.json` is written into each match folder" in running_guide
    assert "`report.md` is written once for all of them, into `runs/reports/<UTC timestamp>/`" in running_guide
    assert "This writes `compare.md` into `runs/reports/<UTC timestamp>/`" in running_guide


def test_running_guide_says_folders_are_per_seat_and_files_per_attempt(running_guide):
    assert "Folders are per seat." in running_guide
    assert "`runs/<UTC yyyymmdd>/<session id>__<seat>/`" in running_guide
    assert "marked as the same game" in running_guide
    assert "Files are per attempt." in running_guide
    assert "`events.2.jsonl` and `meta.2.json`" in running_guide


def test_running_guide_says_the_agent_starts_the_strong_and_jev9b_roles_itself_and_how_to_turn_it_off(running_guide):
    settings = running_guide.split("## Settings", 1)[1].split(" ## ", 1)[0]
    with (DOCS.parent / "config" / "agent.toml").open("rb") as handle:
        shipped = tomllib.load(handle)["models"]

    assert (shipped["autostart"], shipped["autostart_roles"]) == (True, ["strong", "jev9b"])
    assert '`[models] autostart = true` and `autostart_roles = ["strong", "jev9b"]`' in settings
    assert "agent starts the `strong` and `jev9b` roles of the included local model server itself" in settings
    assert "`strong` takes about 30 GB of VRAM and about 2 GB of RAM" in settings
    assert "about 23 GB of VRAM: about 53 GB of VRAM together, which fits the 96 GB card" in settings
    assert "`fast` and `small`, the 4B model, are defined but not loaded" in settings
    assert "To turn it off, set `autostart = false` (or `autostart_roles = []`)" in settings
    assert "names the autostart roles on its `model_server` line" in settings
    assert "as shipped it is `false`" not in running_guide


def test_running_guide_says_red_alert_ships_the_9b_as_plan_chooser_and_commander_and_how_to_go_script_only(
    running_guide,
):
    settings = running_guide.split("## Settings", 1)[1].split(" ## ", 1)[0]
    with (DOCS.parent / "config" / "agent.toml").open("rb") as handle:
        shipped = tomllib.load(handle)
    red_alert_chains = {name: shipped["decide"]["chains"][name] for name in RED_ALERT_CHAINS}

    assert (shipped["redalert"]["model_plan_choice"], shipped["redalert"]["model_commander"]) == (True, True)
    assert shipped["redalert"]["model_scout_choice"] is False
    assert red_alert_chains == dict.fromkeys(RED_ALERT_CHAINS, ["systemone:jev9b"])
    assert "Red Alert ships with JevK5-9B (`jev9b`) as plan chooser and commander" in settings
    assert '`red_alert_commander` chains of `[decide.chains]` are `["systemone:jev9b"]`' in settings
    assert "plays its own choice when no answer arrives within the deadline or `jev9b` is down" in settings
    assert "If the 9B does not start, the script plays Red Alert; no chain goes on to another model." in settings
    assert "To switch Red Alert to script-only, set `model_plan_choice = false` and `model_commander = false`" in (
        settings
    )
    assert "The 4B (`small`) is defined in `config/models.toml` but not used as shipped." in settings


def test_running_guide_says_the_scripts_need_the_editable_install(running_guide):
    assert 'they need the editable install from [Setup](#setup-once-per-machine) (`pip install -e ".[dev]"`)' in (
        running_guide
    )


@pytest.fixture(scope="module")
def local_benchmark(running_guide) -> str:
    """The "Local benchmark" section of the run instructions, up to the next section."""
    assert "## Local benchmark" in running_guide
    return running_guide.split("## Local benchmark", 1)[1].split(" ## ", 1)[0]


@pytest.mark.parametrize(
    "named",
    [
        "`sidestep`",
        "`trapped`",
        "`shadowfiend-ii`",
        "`england`",
        "`russia`",
        "`bot:rush`",
        "`bot:normal`",
        "`401 + 3k`",
        "UCLA_AGENT_RUNS_DIR",
        'refinery_first"',
        "`barracks_first`",
        "--bench-compare",
    ],
)
def test_the_local_benchmark_section_names_the_set_the_records_folder_and_the_opening_comparison(
    local_benchmark, named
):
    assert named in local_benchmark


def test_the_local_benchmark_section_gives_the_seed_parameter_and_the_72_game_set_as_a_smoke_check(local_benchmark):
    smoke_check = (
        "With N = 6 (72 games) the set is a smoke check across all 12 configurations; whether a change is kept or "
        "dropped is decided by a keep-or-drop comparison (below), with at least 200 games per arm."
    )

    assert "Configuration k (0 to 11, in that order) plays seeds" in local_benchmark
    assert "the procedure's `--seeds N`" in local_benchmark
    assert smoke_check in local_benchmark
    assert "Go/no-go comparisons between two code versions use N = 6" not in local_benchmark
    assert "python scripts/report.py --bench C:\\Projects\\UCLA-RedAlert\\runs\\bench\\baseline" in local_benchmark


@pytest.mark.parametrize(
    "stated",
    [
        "at least 200 games per arm in the configurations the change targets, each on a fresh seed "
        "(one game per seed), both arms on identical seeds",
        "a no-regression check of the configurations the change does not target, with at least 144 games per arm",
        "one code snapshot per arm: `git archive` of the commit into `tmp`, played with the harness's `--agent-src` "
        "and with `UCLA_AGENT_ROOT` set to the snapshot",
        "the change is an edit of one snapshot's `config\\redalert\\tuning.toml`",
        "the two arms run at the same time, each in its own PowerShell window, on the harness ports 5310 and 5320",
        "The 72-game set remains the smoke check across all 12 configurations.",
        "harness runs are unaffected by them. The offline test suite (`pytest`), though, needs the live agent stopped",
    ],
    ids=["200 per arm", "144 no-regression", "snapshot per arm", "configuration edit", "two ports", "smoke", "live"],
)
def test_the_local_benchmark_section_states_the_keep_or_drop_method(local_benchmark, stated):
    assert "Keep-or-drop comparisons." in local_benchmark
    assert stated in local_benchmark


def test_the_local_benchmark_section_gives_the_loop_for_one_arm_on_its_own_port(local_benchmark):
    assert 'git -C $repo archive $commit --prefix="$arm/"' in local_benchmark
    assert "the other arm runs the same loop at the same time with its own label, `$snapshot` and `$port = 5310`" in (
        local_benchmark
    )
    assert "$port = 5320" in local_benchmark
    assert "$env:UCLA_AGENT_ROOT = $snapshot" in local_benchmark
    assert '--agent-src "$snapshot\\src" --port $port' in local_benchmark


@pytest.fixture(scope="module")
def werewolf_practice(running_guide) -> str:
    """The "Werewolf practice" section of the run instructions, up to the next section."""
    assert "## Werewolf practice" in running_guide
    return running_guide.split("## Werewolf practice", 1)[1].split(" ## ", 1)[0]


@pytest.mark.parametrize(
    "command",
    [
        "python scripts/werewolf_bench.py --label <label> --arm baseline|agent-script|agent-model "
        "--mix scripted|mixed|self --games-per-role N [--seed-base 1000] [--tuning <path>]",
        "python scripts/report.py --werewolf-bench runs/practice/werewolf/<label>",
        "python scripts/report.py --werewolf-bench-compare runs/practice/werewolf/<label A> "
        "runs/practice/werewolf/<label B>",
    ],
    ids=["bench", "table", "compare"],
)
def test_the_werewolf_practice_section_gives_the_bench_and_report_commands(werewolf_practice, command):
    assert command in werewolf_practice


@pytest.mark.parametrize(
    "named",
    [
        "`baseline`",
        "`agent-script`",
        "`agent-model`",
        "`scripted`",
        "`mixed`",
        "`self`",
        "`--tuning <path>`",
        "`seed % 7`",
        "`results.csv`",
        "`summary.json`",
        "`seer_claim_day`",
        "`seer_voted_out_on_claim_day`",
        "`village_won`",
        "`seer_claimed`",
        "`village_wins`",
        "`runs/practice/werewolf/<label>/`",
        "UCLA_AGENT_RUNS_DIR",
        "`fisher_p_two_sided`",
        "`goal: met`",
    ],
)
def test_the_werewolf_practice_section_names_the_arms_mixes_files_and_goal_line(werewolf_practice, named):
    assert named in werewolf_practice


def test_the_werewolf_practice_section_says_which_runs_need_the_strong_and_fast_roles_and_how_they_are_started(
    werewolf_practice,
):
    assert "`agent-model` and the `mixed` mix need the local model server with the `strong` role" in werewolf_practice
    assert "and the `fast` role" in werewolf_practice
    assert "it starts the included model server from `config/models.toml` itself" in werewolf_practice
    assert "`[models] autostart = true`" in werewolf_practice


def test_the_werewolf_practice_section_gives_the_roles_each_arm_starts_and_their_memory(werewolf_practice):
    assert "but with only the roles the run needs" in werewolf_practice
    assert "| `baseline` | nothing | `fast` | nothing |" in werewolf_practice
    assert "| `agent-script` | nothing | `fast` | nothing |" in werewolf_practice
    assert "| `agent-model` | `strong` | `strong` and `fast` | `strong` |" in werewolf_practice
    assert "`fast` model takes about 16 GB of memory and `strong` about 29 GB" in werewolf_practice
    assert "A benchmark run never starts `small`" in werewolf_practice
    assert "`model_roles_started`" in werewolf_practice


def test_the_werewolf_practice_section_gives_the_duration_per_game(werewolf_practice):
    assert "takes about 0.03 seconds" in werewolf_practice
    assert "such a game takes about 0.05 seconds" in werewolf_practice
    assert "roughly one to two minutes per game" in werewolf_practice


def test_the_werewolf_practice_section_says_what_the_self_mix_seats_and_that_its_records_are_kept_nowhere(
    werewolf_practice,
):
    assert "`self` is six copies of the Werewolf agent itself with `model_choices` and `model_messages` off" in (
        werewolf_practice
    )
    assert "with its own ledger and chat memory" in werewolf_practice
    assert "A `self` table needs no model server" in werewolf_practice
    assert "the label's match records are our seat's alone" in werewolf_practice


def test_the_werewolf_practice_section_says_who_plays_the_tuning_file_and_that_its_hash_is_recorded(
    werewolf_practice,
):
    every_agent = (
        "every Werewolf agent of the run (our seat's in `agent-script` and `agent-model`, and every `self` copy)"
    )
    assert every_agent in werewolf_practice
    assert "`summary.json` records its path and SHA-256 under `tuning`" in werewolf_practice
    assert "--tuning tmp/tuning-changed.toml" in werewolf_practice


def test_the_werewolf_practice_section_says_the_engine_truth_columns_are_never_shown_to_a_seat(werewolf_practice):
    assert "read from the game engine once the game is over and are never shown to a seat" in werewolf_practice
    assert "matched by the agent ledger's own seer-claim pattern" in werewolf_practice
    assert "A label written before those columns existed shows `-` for them." in werewolf_practice


def test_the_werewolf_practice_section_gives_the_seven_seat_test_table_steps(werewolf_practice):
    assert "create a Werewolf test match and set all 7 seats to Mine (self-hosted)" in werewolf_practice
    assert "The one running `python -m agent --match` process plays all 7 seats" in werewolf_practice
    assert "check its 7 match folders, one per seat, under `runs/<UTC yyyymmdd>/<session id>__<seat>/`" in (
        werewolf_practice
    )
    assert "python scripts/report.py runs/<UTC yyyymmdd>" in werewolf_practice


@pytest.fixture(scope="module")
def pokemon_practice(running_guide) -> str:
    """The "Pokémon practice" section of the run instructions, up to the next section."""
    assert "## Pokémon practice" in running_guide
    return running_guide.split("## Pokémon practice", 1)[1].split(" ## ", 1)[0]


@pytest.mark.parametrize(
    "command",
    [
        "python scripts/pokemon_bench.py --label <label> --opponent random|first|self --games N "
        "[--seed-base 5000] [--models off|on|brief|decide] [--model-port 11600] [--model-backend-port-base 41600]",
        "python scripts/report.py --pokemon-bench runs/practice/pokemon/<label>",
        "python scripts/report.py --pokemon-bench-compare runs/practice/pokemon/<label A> "
        "runs/practice/pokemon/<label B>",
        "python scripts/pokemon_bench.py --label pk-random-50 --opponent random --games 50 --seed-base 5000 --models on",
        "python scripts/pokemon_bench.py --label pk-first-50 --opponent first --games 50 --seed-base 5000 --models on",
    ],
    ids=["bench", "figures", "compare", "measured random", "measured first"],
)
def test_the_pokemon_practice_section_gives_both_scripts_commands(pokemon_practice, command):
    assert command in pokemon_practice


def test_the_pokemon_practice_section_says_how_to_install_the_showdown_helper(pokemon_practice):
    assert "cd tools/pokemon_sim npm ci cd ../.." in pokemon_practice
    assert "Node.js 22.18 or newer" in pokemon_practice
    assert "`tools/pokemon_sim/node_modules/`, which is never committed" in pokemon_practice
    assert "The agent itself never needs Node" in pokemon_practice


@pytest.mark.parametrize(
    "named",
    [
        "`random`",
        "`first`",
        "`self`",
        "`--models off` (the default)",
        "`seed base + i` (5000 by default)",
        "Our seat makes the draft's first pick on an even seed and the second pick on an odd seed",
        "`runs/practice/pokemon/<label>/`",
        "UCLA_AGENT_RUNS_DIR",
        "`results.csv`",
        "`summary.json`",
        "`our_won`",
        "`decisions`",
        "`fallbacks`",
        "`parse_degradations`",
        "`unexpected_state`",
        "`decision_p95_ms`",
        "`max_decision_ms_draft`",
        "`max_decision_ms_team_preview`",
        "`max_decision_ms_moving`",
        "`unhandled_errors`",
        "`our_first_pick_seeds`",
        "`fisher_p_two_sided`",
    ],
)
def test_the_pokemon_practice_section_names_the_opponents_seeds_files_and_columns(pokemon_practice, named):
    assert named in pokemon_practice


def test_the_pokemon_practice_section_describes_the_brief_and_decide_arms_beside_off_and_on(pokemon_practice):
    assert "With `--models on`, `model_choices` is on and `model_briefing` and `model_decides` are off" in (
        pokemon_practice
    )
    assert "With `--models brief`, `model_choices` and `model_briefing` are both on and `model_decides` off" in (
        pokemon_practice
    )
    assert "With `--models decide`, `model_choices`, `model_briefing` and `model_decides` are all on" in (
        pokemon_practice
    )
    with (DOCS.parent / "config" / "agent.toml").open("rb") as handle:
        shipped_decide_top_k = tomllib.load(handle)["pokemon"]["decide_top_k"]
    shortlist = f"offered the script's best `decide_top_k` ({shipped_decide_top_k} as shipped), described without"
    assert f"{shortlist} the script's scores and listed in a shuffled order" in pokemon_practice
    assert "Each arm sets `model_briefing` and `model_decides` itself, whatever `config/agent.toml` holds" in (
        pokemon_practice
    )
    assert "so `on` always measures the short context with the script's ranking" in pokemon_practice
    assert "`model_briefing`, `model_decides`, `top_k`, `decide_top_k`, the deadlines and `model_endpoint`" in (
        pokemon_practice
    )


def test_the_pokemon_practice_section_says_the_decide_arm_needs_the_model_server_and_refuses_a_hosted_model(
    pokemon_practice,
):
    assert "`--models on`, `brief` and `decide` need the local model server" in pokemon_practice
    assert "and so are `--models brief` and `--models decide`" in pokemon_practice
    assert "In a `decide` label the option ids follow the shuffled order" in pokemon_practice


def test_the_pokemon_practice_section_says_the_bench_model_server_never_uses_the_live_agent_s_ports(
    pokemon_practice,
):
    assert "The run's model server has ports of its own, never the live agent's" in pokemon_practice
    assert "its endpoint is on `--model-port` (11600 by default)" in pokemon_practice
    assert "from `--model-backend-port-base` (41600 by default, one port per role)" in pokemon_practice
    assert "(11500, and 41500 to 41502)" in pokemon_practice
    assert "A port the live agent uses is refused with exit code 2 before anything is played" in pokemon_practice
    assert "`UCLA_AGENT_MODEL_PORT` and `UCLA_AGENT_MODEL_BACKEND_PORT_BASE`" in pokemon_practice
    assert "`http://127.0.0.1:11600/health`" in pokemon_practice and ":11500/health" not in pokemon_practice


def test_the_pokemon_practice_section_names_the_report_s_median_and_model_differed_lines(pokemon_practice):
    assert "`phase,median_decision_ms`" in pokemon_practice
    assert "`model_answers,model_differed,model_differed_share`" in pokemon_practice
    assert "(`chosen` is not `script_choice`)" in pokemon_practice


def test_the_pokemon_practice_section_says_what_turns_counts_and_why_battles_are_short(pokemon_practice):
    assert "`turns` is the N of the last `|turn|N` line Showdown sent" in pokemon_practice
    assert "a battle won during its third turn counts 3" in pokemon_practice
    assert "each side brings 4 Pokémon at level 50, two of them on the board at once" in pokemon_practice


def test_the_pokemon_practice_section_says_models_on_starts_only_strong_and_never_asks_a_hosted_model(
    pokemon_practice,
):
    assert "the roles the `pokemon_choice` chain names: `strong` alone as shipped" in pokemon_practice
    assert "Model server: starting only the roles the run needs: strong." in pokemon_practice
    assert "`model_roles_started`" in pokemon_practice
    assert "it never contacts the platform or a hosted model" in pokemon_practice
    assert "`--models on` is refused while the chain names a `claude:` entry" in pokemon_practice


def test_the_pokemon_practice_section_gives_the_durations_with_models_off_and_on(pokemon_practice):
    assert "a match on the local Showdown takes about 0.3 seconds" in pokemon_practice
    assert "one of 50 takes about 15 seconds" in pokemon_practice
    assert "the estimate is 5 to 15 seconds per match" in pokemon_practice


def test_the_pokemon_practice_section_gives_the_two_seat_test_match_steps(pokemon_practice):
    assert "python -m agent --check-tournament" in pokemon_practice
    assert "create a Pokémon VGC doubles draft test match (`pokemon_vgc_doubles_draft`)" in pokemon_practice
    assert "set both seats to Mine (self-hosted)" in pokemon_practice
    assert "The one running `python -m agent --match` process plays both seats" in pokemon_practice
    assert "Do not start a second copy of the agent for the other seat." in pokemon_practice
    assert "check its 2 match folders, one per seat, under `runs/<UTC yyyymmdd>/<session id>__<seat>/`" in (
        pokemon_practice
    )
    assert "python scripts/report.py runs/<UTC yyyymmdd>" in pokemon_practice
    assert "'\"unexpected_state\"', '\"parse_degradations\": [1-9]'" in pokemon_practice


# -- docs/GAMES-STATUS.md ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def games_status_text() -> str:
    return (DOCS / "GAMES-STATUS.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def game_statuses(games_status_text) -> dict[str, str]:
    return {row["game_type"]: row["status"] for row in STATUS_ROW.finditer(games_status_text)}


def test_every_tournament_game_is_listed_with_an_allowed_status(game_statuses):
    assert set(game_statuses) == TOURNAMENT_GAME_TYPES
    assert set(game_statuses.values()) <= ALLOWED_STATUSES


def test_a_game_without_a_registered_decision_maker_is_not_started(game_statuses):
    unregistered = TOURNAMENT_GAME_TYPES - set(registry.supported_games())

    assert {game_statuses[game_type] for game_type in unregistered} == {"not started"}


def test_a_registered_game_is_in_progress_or_ready(game_statuses):
    for game_type in registry.supported_games():
        assert game_statuses[game_type] in {"in progress", "ready"}


def test_games_status_states_the_completion_criteria_for_ready(games_status_text):
    criteria = (
        "real platform test matches have been played to completion unattended on every faction or role "
        "and every map the game can assign, with no unhandled errors in the match records"
    )

    assert criteria in _plain(games_status_text)


def test_games_status_says_when_a_person_may_register(games_status_text):
    rule = "A person registers the agent for a game's tournament only when that game is `ready`."

    assert rule in _plain(games_status_text)


# -- docs/RUNNING.md and docs/GAMES-STATUS.md: the platform's rules for test and tournament games ----

RUN_DOCS = ("RUNNING.md", "GAMES-STATUS.md")


@pytest.mark.parametrize("page", RUN_DOCS)
def test_the_run_docs_say_testing_closes_and_platform_test_matches_are_played_before_then(page):
    text = _plain((DOCS / page).read_text(encoding="utf-8"))

    assert "Testing closes at 11:59 p.m. Pacific on 2026-10-13" in text
    assert "switching the agent back to Self Hosted" in text
    assert "platform test matches must be played before then" in text


@pytest.mark.parametrize("page", RUN_DOCS)
def test_the_run_docs_say_a_tournament_game_failing_for_a_server_problem_is_replayed_and_never_scored(page):
    text = _plain((DOCS / page).read_text(encoding="utf-8"))

    assert "A tournament game that fails for a server problem is replayed and never scored." in text


def test_running_guide_says_a_replay_is_a_new_assignment_and_what_is_scored_as_played(running_guide):
    assert "The replay arrives as a new assignment whose round label ends with `(a replay)`" in running_guide
    assert "is not a server problem: it is scored as played" in running_guide


# -- docs/RUNNING.md: the temporary errors of the game server and the starter's known gaps --------

STARTER = DOCS.parent / "src" / "altruagent"
TEMPORARY_GAME_SERVER_ERRORS = ("TOO_MANY_WAITS", "RUNTIME_TEMPORARILY_UNAVAILABLE", "AUTH_TEMPORARILY_UNAVAILABLE")


def _starter_mentions(text: str) -> bool:
    """Whether any module of the vendored starter contains `text`."""
    return any(text in module.read_text(encoding="utf-8") for module in STARTER.glob("*.py"))


@pytest.fixture(scope="module")
def temporary_errors(running_guide) -> str:
    return running_guide.split("## Temporary platform errors", 1)[1].split(" ## ", 1)[0]


@pytest.mark.parametrize("code", TEMPORARY_GAME_SERVER_ERRORS)
def test_running_guide_says_the_starter_handles_each_temporary_error_it_names_and_the_rest_are_known_gaps(
    temporary_errors, code
):
    verdict = re.search(rf"`{code}` \([^)]*\): (handled|known gap)\.", temporary_errors)

    assert verdict is not None, code
    assert verdict[1] == ("handled" if _starter_mentions(code) else "known gap")


def test_running_guide_says_the_starter_ignores_retry_after_and_stops_on_a_temporary_first_sign_in_error(
    temporary_errors,
):
    entry_point = (DOCS.parent / "src" / "agent" / "__main__.py").read_text(encoding="utf-8")

    assert not _starter_mentions("Retry-After")
    assert 'print_line(f"Could not connect with your Official Agent Key: {exc}")' in entry_point
    assert "does not read `Retry-After`" in temporary_errors
    assert "Known gap: a temporary error at the first sign-in stops the agent." in temporary_errors
    assert "prints `Could not connect with your Official Agent Key: ...` and exits with code 1" in temporary_errors


# -- docs/COMPONENTS.md --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def components_page() -> str:
    return _plain((DOCS / "COMPONENTS.md").read_text(encoding="utf-8"))


def test_components_page_names_the_runtime_function_the_agent_wraps(components_page):
    assert f"altruagent.runner.{runtime_hooks.HOOKED_FUNCTION_NAME}" in components_page
    assert "ucla_agent.core.runtime_hooks.install" in components_page
    assert "ucla_agent.core.records.finish_match" in components_page


def test_components_page_says_it_is_the_one_attachment_to_a_non_public_part(components_page):
    assert "The one attachment to a non-public part of the runtime" in components_page
    assert "it is not part of the runtime's public contract" in components_page


def test_components_page_says_the_wrap_is_in_memory_and_changes_no_vendored_file(components_page):
    assert "in memory only" in components_page
    assert "No file under `src/altruagent/` is modified" in components_page
    assert "returns its value unchanged" in components_page


def test_components_page_says_what_happens_when_the_function_is_missing(components_page):
    assert 'one `error` event of type `handled` with `where = "runtime_hooks"`' in components_page
    assert "ends with a `match_end` event" in components_page


# Each third-party component of the jev9b role, with the source its row must link.
JEV_COMPONENT_SOURCES = {
    "JevK5-9B": "https://huggingface.co/alibiserikbay/JevK5-9B",
    "jev-style": "https://github.com/lawrence3699/jev-style",
    "jevk5": "https://github.com/allebee/jevk5",
}


@pytest.fixture(scope="module")
def third_party_rows() -> dict[str, dict[str, str]]:
    """The rows of the components page's third-party table, by component."""
    page = (DOCS / "COMPONENTS.md").read_text(encoding="utf-8")
    section = page.split("\n## Third-party components\n", 1)[1].split("\n## ", 1)[0]
    header, *rows = _tables(section)[0]
    return {row[0]: dict(zip(header, row, strict=True)) for row in rows}


@pytest.mark.parametrize(("component", "source"), JEV_COMPONENT_SOURCES.items())
def test_components_page_discloses_each_jev_component_with_its_license_source_and_use(
    third_party_rows, component, source
):
    row = third_party_rows[component]

    assert row["License"].startswith("Apache-2.0")
    assert f"<{source}>" in row["Source"]
    assert row["Where the repository uses it"].startswith("Not included in this repository")
    assert "`docs/MODELS.md`" in row["Where the repository uses it"]
    assert "`scripts/serve_jev9b.py`" in row["Where the repository uses it"]
    assert "the `jev9b` role" in row["Where the repository uses it"]


def test_components_page_lists_the_jev_components_as_excluded_and_says_the_serve_script_reads_no_cascade(
    components_page,
):
    assert "| JevK5-9B, jev-style and jevk5 | Third-party weights and runtime packages" in components_page
    assert "Used as shipped by the Red Alert chains. |" in components_page
    assert "so they are used as shipped: JevK5-9B makes Red Alert's model choices" in components_page
    assert "Per its model card, part of its training labels came from another model" in components_page
    assert "`scripts/serve_jev9b.py` reads no other configuration and no cascade file" in components_page


# -- docs/BENCHMARKS.md --------------------------------------------------------------------------

BENCHMARKS = DOCS / "BENCHMARKS.md"
TUNING = DOCS.parent / "config" / "redalert" / "tuning.toml"
BENCHMARK_COLUMNS = (
    "label change commit toggles games wins rush_wins normal_wins kd_mean compared_to fisher_p suite_seconds decision"
).split()
NUMERIC_COLUMNS = ("games", "wins", "rush_wins", "normal_wins", "kd_mean", "fisher_p", "suite_seconds")
NOT_MEASURED = "-"
ITERATION_2 = "iteration-2"
FINAL = "final"
ITERATION_2_WINS = {"baseline-72": 15, "fixround-72": 15, "mix-72": 22, "focus-72": 30, "iter2-72": 40}
# Each change the record measures, with the name of its toggle in the toggles cell.
CHANGE_TOGGLES = {
    "no-blind-press": "guard",
    "aircraft-targeting": "air",
    "anti-air": "anti_air",
    "raid-repair": "raids",
}
# The table and key in tuning.toml of each toggle named in the toggles cell.
TOGGLE_KEYS = {
    "guard": ("plan_rules", "blind_press_guard"),
    "air": ("combat", "air_targeting"),
    "anti_air": ("anti_air", "enabled"),
    "raids": ("raids", "enabled"),
}
TOGGLE_CELL_VALUES = {True: "on", False: "off"}
REFERENCE = "reference"
DECISIONS = {REFERENCE, "kept", "switched_off", "tuned"}
# The decisions that leave the change in play, its toggle on.
DECISIONS_ON = {"kept", "tuned"}
BENCHMARK_GAMES = 72
# Half the games are against bot:rush and half against bot:normal.
GAMES_PER_BOT = BENCHMARK_GAMES // 2
SUITE_SECONDS_LIMIT = 480
# The goal the final row is held to: more than NORMAL_WINS_ABOVE games won against normal, at least RUSH_WINS_AT_LEAST
# against rush.
NORMAL_WINS_ABOVE = 18
RUSH_WINS_AT_LEAST = 22
COMMIT = re.compile(r"[0-9a-f]{7,}")
INTEGER = re.compile(r"\d+")
NUMBER = re.compile(r"-?\d+(\.\d+)?")
GOAL_SENTENCE = re.compile(
    rf"Goal: (?P<status>met|not met)\. The final configuration won (?P<normal>\d+) of {GAMES_PER_BOT} games against "
    rf"normal, where more than (?P<normal_needed>\d+) are needed, and (?P<rush>\d+) of {GAMES_PER_BOT} against rush, "
    r"where at least (?P<rush_needed>\d+) are needed\."
)


def _tables(text: str) -> list[list[list[str]]]:
    """Every markdown table in `text` as its rows of cells, the header first and the separator line left out."""
    tables: list[list[list[str]]] = []
    table_lines: list[str] = []
    for line in [*text.splitlines(), ""]:
        if line.startswith("|"):
            table_lines.append(line)
        elif table_lines:
            header, _separator, *body = table_lines
            tables.append([[cell.strip() for cell in row.strip().strip("|").split("|")] for row in [header, *body]])
            table_lines = []
    return tables


def _toggles(cell: str) -> dict[str, str]:
    """A toggles cell such as `guard=on, air=off` as {"guard": "on", "air": "off"}."""
    return dict(item.split("=") for item in cell.split(", "))


@pytest.fixture(scope="module")
def benchmarks_text() -> str:
    return BENCHMARKS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def benchmark_rows(benchmarks_text) -> list[dict[str, str]]:
    """The rows of the record, the table whose header starts with `label`, in table order."""
    header, *rows = next(table for table in _tables(benchmarks_text) if table[0][0] == "label")
    return [dict(zip(header, row, strict=True)) for row in rows]


@pytest.fixture(scope="module")
def iteration_3_rows(benchmark_rows) -> dict[str, dict[str, str]]:
    """The rows that are not references, by their change."""
    return {row["change"]: row for row in benchmark_rows if row["change"] != ITERATION_2}


@pytest.fixture(scope="module")
def shipped_toggles() -> dict[str, str]:
    """The value `config/redalert/tuning.toml` gives each toggle, written as in a toggles cell."""
    with TUNING.open("rb") as handle:
        tuning = tomllib.load(handle)
    return {name: TOGGLE_CELL_VALUES[tuning[table][key]] for name, (table, key) in TOGGLE_KEYS.items()}


def test_benchmarks_record_exists():
    assert BENCHMARKS.is_file()


def test_benchmarks_record_is_one_table_with_the_fixed_columns(benchmarks_text):
    headers = [table[0] for table in _tables(benchmarks_text) if table[0][0] == "label"]

    assert headers == [BENCHMARK_COLUMNS]


def test_benchmarks_rows_have_unique_labels_numeric_cells_and_allowed_decisions(benchmark_rows):
    labels = [row["label"] for row in benchmark_rows]

    assert len(labels) == len(set(labels))
    assert {row["decision"] for row in benchmark_rows} <= DECISIONS
    for row in benchmark_rows:
        for column in NUMERIC_COLUMNS:
            assert NUMBER.fullmatch(row[column]) or row[column] == NOT_MEASURED, (row["label"], column)


def test_benchmarks_iteration_2_rows_have_their_wins_over_72_games(benchmark_rows):
    references = [row for row in benchmark_rows if row["change"] == ITERATION_2]

    assert len(references) == len(ITERATION_2_WINS)
    assert {row["label"]: int(row["wins"]) for row in references} == ITERATION_2_WINS
    assert {(int(row["games"]), row["decision"]) for row in references} == {(BENCHMARK_GAMES, REFERENCE)}


def test_benchmarks_has_one_row_per_iteration_3_change_and_a_final_row(benchmark_rows):
    changes = [row["change"] for row in benchmark_rows if row["change"] != ITERATION_2]

    assert sorted(changes) == sorted([*CHANGE_TOGGLES, FINAL])


@pytest.mark.parametrize("change", [*CHANGE_TOGGLES, FINAL])
def test_benchmarks_iteration_3_row_is_measured_and_compared_with_an_earlier_label(benchmark_rows, change):
    position, row = next((position, row) for position, row in enumerate(benchmark_rows) if row["change"] == change)
    earlier_labels = {earlier["label"] for earlier in benchmark_rows[:position]}

    assert int(row["games"]) == BENCHMARK_GAMES
    assert all(INTEGER.fullmatch(row[column]) for column in ("wins", "rush_wins", "normal_wins"))
    assert NUMBER.fullmatch(row["kd_mean"])
    assert COMMIT.fullmatch(row["commit"])
    assert row["compared_to"] in earlier_labels
    assert 0 <= float(row["fisher_p"]) <= 1
    assert row["decision"] in DECISIONS - {REFERENCE}


@pytest.mark.parametrize("change", list(CHANGE_TOGGLES))
def test_benchmarks_change_row_is_played_with_its_toggle_on(iteration_3_rows, change):
    assert _toggles(iteration_3_rows[change]["toggles"])[CHANGE_TOGGLES[change]] == "on"


def test_benchmarks_wins_are_the_rush_and_normal_wins_together(benchmark_rows):
    for row in benchmark_rows:
        assert int(row["wins"]) == int(row["rush_wins"]) + int(row["normal_wins"]), row["label"]


def test_benchmarks_final_row_suite_seconds_are_within_the_limit(iteration_3_rows):
    assert float(iteration_3_rows[FINAL]["suite_seconds"]) <= SUITE_SECONDS_LIMIT


def test_benchmarks_final_row_toggles_are_the_ones_tuning_toml_ships(iteration_3_rows, shipped_toggles):
    assert _toggles(iteration_3_rows[FINAL]["toggles"]) == shipped_toggles


@pytest.mark.parametrize("change", list(CHANGE_TOGGLES))
def test_benchmarks_shipped_toggle_matches_the_decision_on_its_change(iteration_3_rows, shipped_toggles, change):
    expected = "on" if iteration_3_rows[change]["decision"] in DECISIONS_ON else "off"

    assert shipped_toggles[CHANGE_TOGGLES[change]] == expected


def test_benchmarks_goal_sentence_matches_the_final_row(benchmarks_text, iteration_3_rows):
    final = iteration_3_rows[FINAL]
    normal_wins, rush_wins = int(final["normal_wins"]), int(final["rush_wins"])
    goal_met = normal_wins > NORMAL_WINS_ABOVE and rush_wins >= RUSH_WINS_AT_LEAST
    goal = GOAL_SENTENCE.search(_plain(benchmarks_text))

    assert goal is not None
    assert (int(goal["normal"]), int(goal["rush"])) == (normal_wins, rush_wins)
    assert (int(goal["normal_needed"]), int(goal["rush_needed"])) == (NORMAL_WINS_ABOVE, RUSH_WINS_AT_LEAST)
    assert goal["status"] == ("met" if goal_met else "not met")


# -- docs/BENCHMARKS.md, the Soviet-against-Normal section ---------------------------------------

ITERATION_3_GOAL_HEADING = "## The iteration-3 goal"
SOVIET_NORMAL_HEADING = "## Iteration 7: Soviet against Normal"
SOVIET_NORMAL_DECISION_HEADING = "### The iteration-7 decision"
# A section of the record runs from its heading to the next heading of its level.
NEXT_SECTION = "\n## "
NEXT_SUBSECTION = "\n### "
SOVIET_ARM_PREFIX = "sov-"
# The full 72-game label of the section, and the label of the record it is compared with.
FULL_SET_ARM = "all-i7-72"
FULL_SET_COMPARED = "all-72"
MAPS = ("sidestep", "trapped", "shadowfiend-ii")
# The value the iteration-7 decision records each switch shipped with then, by the switch's table and key in
# tuning.toml.
ITERATION_7_SHIPPED_SWITCHES = {
    ("composition", "raid_mixed_vehicles"): 3,
    ("anti_air", "air_flak_points"): 10,
    ("upkeep", "soviet_tesla_coils"): 0,
    ("upkeep", "preserve_anti_armor"): False,
    ("plan_rules", "press_late_seconds"): 300,
    ("plan_rules", "late_press_guard"): False,
}


def _section(text: str, heading: str, next_heading: str = NEXT_SECTION) -> str:
    """The part of `text` after `heading`, up to the next heading that starts with `next_heading`."""
    assert heading in text
    return text.split(heading, 1)[1].split(next_heading, 1)[0]


def _toml_value(value: int | bool) -> str:
    """`value` written as in tuning.toml: `false` for False, `0` for 0."""
    return str(value).lower()


@pytest.fixture(scope="module")
def soviet_normal_section(benchmarks_text) -> str:
    """The "Iteration 7: Soviet against Normal" section of the benchmark record, up to the next section."""
    return _section(benchmarks_text, SOVIET_NORMAL_HEADING)


@pytest.fixture(scope="module")
def soviet_normal_decision(soviet_normal_section) -> str:
    """The section's "The iteration-7 decision", up to the next subsection, with wrapping removed."""
    return _plain(_section(soviet_normal_section, SOVIET_NORMAL_DECISION_HEADING, NEXT_SUBSECTION))


def _tables_by_first_header(text: str) -> dict[str, list[dict[str, str]]]:
    """Every markdown table in `text` by its first header, each as its rows by column."""
    return {header[0]: [dict(zip(header, row, strict=True)) for row in rows] for header, *rows in _tables(text)}


@pytest.fixture(scope="module")
def soviet_normal_tables(soviet_normal_section) -> dict[str, list[dict[str, str]]]:
    """The section's tables by their first header (`arm`, `map`, `configuration`), each as its rows by column."""
    return _tables_by_first_header(soviet_normal_section)


@pytest.fixture(scope="module")
def soviet_normal_arm_wins(soviet_normal_tables) -> dict[str, int]:
    """The wins of each label of the section's `arm` table, in table order."""
    return {row["arm"]: int(row["wins"]) for row in soviet_normal_tables["arm"]}


def test_benchmarks_soviet_normal_section_follows_the_iteration_3_record(benchmarks_text):
    assert benchmarks_text.index(SOVIET_NORMAL_HEADING) > benchmarks_text.index(ITERATION_3_GOAL_HEADING)


def test_benchmarks_soviet_normal_map_wins_add_up_to_each_soviet_arms_wins(
    soviet_normal_tables, soviet_normal_arm_wins
):
    map_rows = soviet_normal_tables["map"]
    map_columns = [column for column in map_rows[0] if column != "map"]

    assert [row["map"] for row in map_rows] == list(MAPS)
    assert map_columns == [arm for arm in soviet_normal_arm_wins if arm.startswith(SOVIET_ARM_PREFIX)]
    for arm in map_columns:
        assert sum(int(row[arm]) for row in map_rows) == soviet_normal_arm_wins[arm], arm


def test_benchmarks_soviet_normal_configuration_wins_add_up_to_both_full_set_labels(
    soviet_normal_tables, soviet_normal_arm_wins, benchmark_rows
):
    configuration_rows = soviet_normal_tables["configuration"]
    record_wins = {row["label"]: int(row["wins"]) for row in benchmark_rows}

    for row in configuration_rows:
        assert sum(int(row[map_name]) for map_name in MAPS) == int(row["wins"]), row["configuration"]
    assert sum(int(row["games"]) for row in configuration_rows) == BENCHMARK_GAMES
    assert sum(int(row["wins"]) for row in configuration_rows) == soviet_normal_arm_wins[FULL_SET_ARM]
    compared_wins = sum(int(row[f"{FULL_SET_COMPARED} wins"]) for row in configuration_rows)
    assert compared_wins == record_wins[FULL_SET_COMPARED]


@pytest.mark.parametrize(("table", "key"), list(ITERATION_7_SHIPPED_SWITCHES))
def test_benchmarks_soviet_normal_decision_records_the_value_the_switch_shipped_with_at_iteration_7(
    soviet_normal_decision, table, key
):
    # Written as TOML, so that a switch recorded as 0 does not pass for one recorded as false.
    shipped_then = _toml_value(ITERATION_7_SHIPPED_SWITCHES[table, key])

    assert f"`[{table}] {key} = {shipped_then}`" in soviet_normal_decision


# -- docs/BENCHMARKS.md, the high-resolution section ----------------------------------------------

HIGH_RESOLUTION_HEADING = "## Iteration 9: the Soviet changes at high resolution"
HIGH_RESOLUTION_DECISION_HEADING = "### The iteration-9 decision"
# The section's tables, by their first header, in order.
MEASUREMENT = "measurement"
SOVIET_MAP = "soviet_map"
ENGLAND_CONFIG = "england_config"
SOVIET_SWITCH = "soviet_switch"
# The first headers of the record's earlier tables: a table of the section with one of them would be read as theirs.
EARLIER_FIRST_HEADERS = {"label", "arm", "map", "configuration"}
# The arms of each comparison, the one with the switches off first.
SOVIET_ARMS = ("i8-off-216", "i8-on-216")
ENGLAND_ARMS = ("i9-eng-off-144", "i9-eng-on-144")
# The fewest games per arm a keep-or-drop comparison and a no-regression check play.
KEEP_OR_DROP_GAMES_AT_LEAST = 200
NO_REGRESSION_GAMES_AT_LEAST = 144
BOTS = ("rush", "normal")
# The decision rule's first branch keeps a change that wins more games at a two-sided Fisher p below this.
SIGNIFICANT_FISHER_P = 0.10
# The England check: the on arm may win at most this many games fewer than the off arm.
ENGLAND_GUARD_WINS = 5
# A kd difference taken from the unrounded means may differ from the difference of the rounded means by this much.
KD_ROUNDING_TOLERANCE = 1
# Every switch the section records, by its table and key in tuning.toml.
SOVIET_SWITCHES = [
    *ITERATION_7_SHIPPED_SWITCHES,
    *((f"mix.soviet.{stage}", queue) for stage in ("base", "fix", "dome") for queue in ("infantry", "vehicle")),
]
SWITCH_CELL = re.compile(r"`\[(?P<table>[a-z_.]+)\] (?P<key>[a-z_]+)`")


def _kd_sentence(arm_on: str, arm_off: str) -> re.Pattern[str]:
    """The sentence that gives the wins of `arm_on` against `arm_off` and the difference of their mean kd."""
    return re.compile(
        rf"{re.escape(arm_on)} won (?P<wins_on>\d+) of (?P<games>\d+) games against (?P<wins_off>\d+) for "
        rf"{re.escape(arm_off)}, .*?at a `kd_mean` of (?P<kd_on>-?[\d,]+) against (?P<kd_off>-?[\d,]+): "
        r"(?P<difference>[+-][\d,]+) from the unrounded means\."
    )


def _whole_number(text: str) -> int:
    """A whole number written with thousands separators, such as -9,082."""
    return int(text.replace(",", ""))


def _toml_cell(cell: str) -> object:
    """A table cell holding one TOML value in backticks, such as `false` or `{ ftrk = 15 }`, as tomllib reads it."""
    return tomllib.loads(f"value = {cell.strip('`')}")["value"]


def _typed(value: object) -> tuple[type, object]:
    """`value` with its type, so that a switch shipped as 0 does not pass for one recorded as false."""
    return type(value), value


def _rows_by_switch(rows: list[dict[str, str]], first_header: str) -> dict[tuple[str, str], dict[str, str]]:
    """The rows of a switch table by the switch's table and key in tuning.toml, in table order."""
    by_switch = {}
    for row in rows:
        switch = SWITCH_CELL.fullmatch(row[first_header])
        assert switch is not None, row[first_header]
        by_switch[switch["table"], switch["key"]] = row
    return by_switch


@pytest.fixture(scope="module")
def high_resolution_section(benchmarks_text) -> str:
    """The "Iteration 9: the Soviet changes at high resolution" section of the record, up to the next section."""
    return _section(benchmarks_text, HIGH_RESOLUTION_HEADING)


@pytest.fixture(scope="module")
def high_resolution_tables(high_resolution_section) -> dict[str, list[dict[str, str]]]:
    """The section's tables by their first header, each as its rows by column."""
    return _tables_by_first_header(high_resolution_section)


@pytest.fixture(scope="module")
def measurements(high_resolution_tables) -> dict[str, dict[str, str]]:
    """The rows of the section's measurement table by their label, in table order."""
    return {row[MEASUREMENT]: row for row in high_resolution_tables[MEASUREMENT]}


@pytest.fixture(scope="module")
def soviet_switch_rows(high_resolution_tables) -> dict[tuple[str, str], dict[str, str]]:
    """The rows of the section's switch table by the switch's table and key in tuning.toml."""
    return _rows_by_switch(high_resolution_tables[SOVIET_SWITCH], SOVIET_SWITCH)


@pytest.fixture(scope="module")
def shipped_tuning() -> dict:
    """`config/redalert/tuning.toml` as the repository ships it."""
    with TUNING.open("rb") as handle:
        return tomllib.load(handle)


@pytest.fixture(scope="module")
def tuning_switches_off() -> dict:
    """`config/redalert/tuning.toml` as the repository ships it with the shipped switch set off (switches_off.py of
    the Red Alert tests): the values the bundle and switch trial sections decided; no benchmark measured the set."""
    with TUNING.open("rb") as handle:
        tuning = tomllib.load(handle)
    for group, settings in GROUPS_OFF.items():
        tuning[group].update({key: value for key, value in settings.items() if key in tuning[group]})
    return tuning


def test_benchmarks_high_resolution_section_follows_the_iteration_7_section(benchmarks_text):
    assert benchmarks_text.index(HIGH_RESOLUTION_HEADING) > benchmarks_text.index(SOVIET_NORMAL_HEADING)


def test_benchmarks_high_resolution_tables_have_first_headers_of_their_own(high_resolution_tables):
    assert list(high_resolution_tables) == [MEASUREMENT, SOVIET_MAP, ENGLAND_CONFIG, SOVIET_SWITCH]
    assert not EARLIER_FIRST_HEADERS & set(high_resolution_tables)


def test_benchmarks_high_resolution_measurements_are_the_two_comparisons_alone(measurements):
    # No arm of an earlier section is pooled in: the table holds the four arms and nothing else.
    assert list(measurements) == [*SOVIET_ARMS, *ENGLAND_ARMS]
    for arm_off, arm_on in (SOVIET_ARMS, ENGLAND_ARMS):
        assert measurements[arm_on]["compared_to"] == arm_off
        assert 0 <= float(measurements[arm_on]["fisher_p"]) <= 1
        assert measurements[arm_off]["games"] == measurements[arm_on]["games"]
    assert int(measurements[SOVIET_ARMS[0]]["games"]) >= KEEP_OR_DROP_GAMES_AT_LEAST
    assert int(measurements[ENGLAND_ARMS[0]]["games"]) >= NO_REGRESSION_GAMES_AT_LEAST


def test_benchmarks_high_resolution_wins_are_the_rush_and_normal_wins_together(measurements):
    for label, row in measurements.items():
        bot_wins = [int(row[f"{bot}_wins"]) for bot in BOTS if row[f"{bot}_wins"] != NOT_MEASURED]
        assert int(row["wins"]) == sum(bot_wins), label


def test_benchmarks_high_resolution_soviet_map_wins_add_up_to_each_soviet_arm(high_resolution_tables, measurements):
    map_rows = high_resolution_tables[SOVIET_MAP]

    assert [row[SOVIET_MAP] for row in map_rows] == list(MAPS)
    assert [column for column in map_rows[0] if column in measurements] == list(SOVIET_ARMS)
    for arm in SOVIET_ARMS:
        assert sum(int(row["games"]) for row in map_rows) == int(measurements[arm]["games"])
        assert sum(int(row[arm]) for row in map_rows) == int(measurements[arm]["wins"]), arm


def test_benchmarks_high_resolution_england_configuration_wins_add_up_to_each_england_arm(
    high_resolution_tables, measurements
):
    configuration_rows = high_resolution_tables[ENGLAND_CONFIG]

    assert [row[ENGLAND_CONFIG] for row in configuration_rows] == [f"{bot}-{name}" for bot in BOTS for name in MAPS]
    assert [column for column in configuration_rows[0] if column in measurements] == list(ENGLAND_ARMS)
    for arm in ENGLAND_ARMS:
        assert sum(int(row["games"]) for row in configuration_rows) == int(measurements[arm]["games"])
        for bot in BOTS:
            bot_rows = [row for row in configuration_rows if row[ENGLAND_CONFIG].startswith(f"{bot}-")]
            assert sum(int(row[arm]) for row in bot_rows) == int(measurements[arm][f"{bot}_wins"]), (arm, bot)


@pytest.mark.parametrize(("arm_off", "arm_on"), [SOVIET_ARMS, ENGLAND_ARMS], ids=["soviet", "england"])
def test_benchmarks_high_resolution_kd_difference_is_taken_from_the_unrounded_means(
    high_resolution_section, measurements, arm_off, arm_on
):
    sentence = _kd_sentence(arm_on, arm_off).search(_plain(high_resolution_section))
    kd_on, kd_off = float(measurements[arm_on]["kd_mean"]), float(measurements[arm_off]["kd_mean"])

    assert sentence is not None
    assert (int(sentence["wins_on"]), int(sentence["wins_off"])) == (
        int(measurements[arm_on]["wins"]),
        int(measurements[arm_off]["wins"]),
    )
    assert int(sentence["games"]) == int(measurements[arm_on]["games"])
    assert (_whole_number(sentence["kd_on"]), _whole_number(sentence["kd_off"])) == (round(kd_on), round(kd_off))
    stated_difference = _whole_number(sentence["difference"])
    assert stated_difference == round(kd_on - kd_off)
    rounded_difference = _whole_number(sentence["kd_on"]) - _whole_number(sentence["kd_off"])
    assert abs(stated_difference - rounded_difference) <= KD_ROUNDING_TOLERANCE


def test_benchmarks_high_resolution_decision_names_the_branch_the_soviet_figures_give(
    high_resolution_section, measurements
):
    arm_off, arm_on = (measurements[arm] for arm in SOVIET_ARMS)
    fisher_p = float(arm_on["fisher_p"])
    decision = _plain(_section(high_resolution_section, HIGH_RESOLUTION_DECISION_HEADING, NEXT_SUBSECTION))

    assert int(arm_on["wins"]) > int(arm_off["wins"])
    assert float(arm_on["kd_mean"]) > float(arm_off["kd_mean"])
    if fisher_p < SIGNIFICANT_FISHER_P:
        assert "The Soviet switches: on, by the first branch" in decision
    else:
        assert "The Soviet switches: on, by the second branch" in decision
        assert f"(Fisher p {arm_on['fisher_p']}, not significant)" in _plain(high_resolution_section)


def test_benchmarks_high_resolution_england_check_is_within_the_guard_and_called_a_conservative_one(
    high_resolution_section, measurements
):
    arm_off, arm_on = (measurements[arm] for arm in ENGLAND_ARMS)
    section = _plain(high_resolution_section)

    assert int(arm_off["wins"]) - int(arm_on["wins"]) <= ENGLAND_GUARD_WINS
    assert f"within the margin of {ENGLAND_GUARD_WINS}, so the two switches that act for the Allies ship on" in section
    assert "The check is a conservative guard, not a test of significance" in section
    assert "trip about one time in four (about 25 %)" in section


@pytest.mark.parametrize(("table", "key"), SOVIET_SWITCHES)
def test_benchmarks_high_resolution_switch_is_recorded_with_the_value_tuning_toml_ships(
    soviet_switch_rows, shipped_tuning, table, key
):
    shipped_table = shipped_tuning
    for name in table.split("."):
        shipped_table = shipped_table[name]

    assert _typed(_toml_cell(soviet_switch_rows[table, key]["shipped"])) == _typed(shipped_table[key])


@pytest.mark.parametrize(("table", "key"), list(ITERATION_7_SHIPPED_SWITCHES))
def test_benchmarks_high_resolution_off_arm_played_the_value_shipped_at_iteration_7(soviet_switch_rows, table, key):
    recorded_off = _toml_cell(soviet_switch_rows[table, key]["off"])

    assert _typed(recorded_off) == _typed(ITERATION_7_SHIPPED_SWITCHES[table, key])


def test_benchmarks_high_resolution_records_every_switch_once(soviet_switch_rows):
    assert list(soviet_switch_rows) == SOVIET_SWITCHES


# -- docs/BENCHMARKS.md, the early-attack guard and harvester parking section --------------------------

EARLY_GUARD_HEADING = "## Iteration 11: the early-attack guard and harvester parking"
EARLY_GUARD_DECISION_HEADING = "### The iteration-11 decision"
# The section's tables, by their first header, in order.
RUN = "run"
SEED_SET = "seed_set"
COMBINED = "combined"
RUSH_CHECK = "rush_check"
CONFIRM_RUN = "confirm_run"
SHIPPED_SWITCH = "shipped_switch"
# The first headers of every earlier table of the record, the pooled toggles and the high-resolution section's too.
ALL_EARLIER_FIRST_HEADERS = {*EARLIER_FIRST_HEADERS, "toggle", MEASUREMENT, SOVIET_MAP, ENGLAND_CONFIG, SOVIET_SWITCH}
# The arms, each compared with the one before it, and the sets every arm played, with their games.
ARMS = ("off", "guard", "both")
SETS = {"ru-normal": 216, "en-normal": 216, "en-rush": 144, "ru-rush": 144}
SET_FACTIONS = {"ru": "russia", "en": "england"}
# The sets the guard targets; the combined row of all the games takes every set.
TARGETED_SETS = ("ru-normal", "en-normal")
COMBINED_SETS = {"targeted": TARGETED_SETS, "all": tuple(SETS)}
# The sentence of each comparison of the combined games starts with its scope.
COMBINED_SCOPES = {"targeted": "On the targeted games", "all": "On all the games"}
RUSH_SETS = ("en-rush", "ru-rush")
# A rush check fails when the guard arm wins more than this many games fewer than the off arm.
RUSH_CHECK_WINS = 5
RUSH_CHECK_RESULTS = {True: "yes", False: "no"}
# The set played again on fresh seeds, and the labels of its two arms.
CONFIRMED_SET = "ru-rush"
CONFIRM_RUNS = ("i11-off-ru-rush-confirm", "i11-guard-ru-rush-confirm")
# A mean over several sets taken from their rounded means may differ from the mean over the games by this much.
COMBINED_KD_TOLERANCE = 0.01
# The p-value a run prints as 0.00 is given to this many decimals in the text.
SMALL_P_DECIMALS = 4
# Every switch and threshold the section records, by its table and key in tuning.toml.
EARLY_GUARD_SWITCHES = [
    ("plan_rules", "early_attack_guard"),
    ("plan_rules", "early_press_until_seconds"),
    ("plan_rules", "early_press_kill_value"),
    ("upkeep", "park_harvesters"),
    ("upkeep", "park_harvesters_seconds_left"),
    ("upkeep", "park_distance_cells"),
]
GUARD_SWITCH = ("plan_rules", "early_attack_guard")
PARKING_SWITCH = ("upkeep", "park_harvesters")
# The two switches as each arm played them: the guard, then parking.
ARM_SWITCHES = {"off": (False, False), "guard": (True, False), "both": (True, True)}
BOTH_RU_RUSH_SETS = re.compile(
    r"Over both ru-rush sets, (?P<games>\d+) games per arm, the off arm won (?P<off>\d+) and the guard arm "
    r"(?P<guard>\d+)\."
)


def _run_label(arm: str, set_name: str) -> str:
    """The harness label of an arm's set, such as i11-guard-ru-normal."""
    return f"i11-{arm}-{set_name}"


def _seeds(cell: str) -> set[int]:
    """The seeds of a seeds cell, ranges such as `2000-2071` separated by commas."""
    seeds: set[int] = set()
    for span in cell.split(", "):
        first, last = span.split("-")
        seeds.update(range(int(first), int(last) + 1))
    return seeds


@pytest.fixture(scope="module")
def early_guard_section(benchmarks_text) -> str:
    """The "Iteration 11: the early-attack guard and harvester parking" section of the record, to the next section."""
    return _section(benchmarks_text, EARLY_GUARD_HEADING)


@pytest.fixture(scope="module")
def early_guard_decision(early_guard_section) -> str:
    """The section's "The iteration-11 decision", up to the next subsection, with wrapping removed."""
    return _plain(_section(early_guard_section, EARLY_GUARD_DECISION_HEADING, NEXT_SUBSECTION))


@pytest.fixture(scope="module")
def early_guard_tables(early_guard_section) -> dict[str, list[dict[str, str]]]:
    """The section's tables by their first header, each as its rows by column."""
    return _tables_by_first_header(early_guard_section)


@pytest.fixture(scope="module")
def runs(early_guard_tables) -> dict[str, dict[str, str]]:
    """The rows of the section's run table by their label, in table order."""
    return {row[RUN]: row for row in early_guard_tables[RUN]}


@pytest.fixture(scope="module")
def combined_rows(early_guard_tables) -> dict[str, dict[str, str]]:
    """The rows of the section's combined table by their scope."""
    return {row[COMBINED]: row for row in early_guard_tables[COMBINED]}


@pytest.fixture(scope="module")
def early_guard_switch_rows(early_guard_tables) -> dict[tuple[str, str], dict[str, str]]:
    """The rows of the section's switch table by the switch's table and key in tuning.toml."""
    return _rows_by_switch(early_guard_tables[SHIPPED_SWITCH], SHIPPED_SWITCH)


def test_benchmarks_early_guard_section_follows_the_high_resolution_section(benchmarks_text):
    assert benchmarks_text.index(EARLY_GUARD_HEADING) > benchmarks_text.index(HIGH_RESOLUTION_HEADING)


def test_benchmarks_early_guard_tables_have_first_headers_of_their_own(early_guard_tables):
    assert list(early_guard_tables) == [RUN, SEED_SET, COMBINED, RUSH_CHECK, CONFIRM_RUN, SHIPPED_SWITCH]
    assert not ALL_EARLIER_FIRST_HEADERS & set(early_guard_tables)


def test_benchmarks_early_guard_runs_are_every_arm_of_every_set_on_the_same_games(runs):
    assert list(runs) == [_run_label(arm, set_name) for set_name in SETS for arm in ARMS]
    for set_name, games in SETS.items():
        faction, bot = set_name.split("-")
        for arm in ARMS:
            row = runs[_run_label(arm, set_name)]
            assert (int(row["games"]), row["faction"], row["bot"]) == (games, SET_FACTIONS[faction], bot), row[RUN]
    assert min(SETS[set_name] for set_name in TARGETED_SETS) >= KEEP_OR_DROP_GAMES_AT_LEAST
    assert min(SETS[set_name] for set_name in RUSH_SETS) >= NO_REGRESSION_GAMES_AT_LEAST


@pytest.mark.parametrize("set_name", list(SETS))
def test_benchmarks_early_guard_each_arm_is_compared_with_the_one_before_it_at_the_fisher_p_of_their_wins(
    runs, set_name
):
    off, *compared = (runs[_run_label(arm, set_name)] for arm in ARMS)

    assert (off["compared_to"], off["fisher_p"]) == (NOT_MEASURED, NOT_MEASURED)
    for earlier, row in zip((off, *compared), compared):
        games = int(row["games"])
        fisher_p = bench.fisher_exact_p(int(earlier["wins"]), games, int(row["wins"]), games)
        assert (row["compared_to"], row["fisher_p"]) == (earlier[RUN], f"{fisher_p:.2f}"), row[RUN]


def test_benchmarks_early_guard_a_p_value_printed_as_zero_is_given_to_four_decimals(early_guard_section, runs):
    zero = f"{0:.2f}"
    for label, row in runs.items():
        if row["fisher_p"] == zero:
            earlier, games = runs[row["compared_to"]], int(row["games"])
            fisher_p = bench.fisher_exact_p(int(earlier["wins"]), games, int(row["wins"]), games)
            stated = (
                f"The p-value of {label}, {zero} as `--bench-compare` prints it, is {fisher_p:.{SMALL_P_DECIMALS}f}."
            )
            assert stated in _plain(early_guard_section)


def test_benchmarks_early_guard_map_wins_add_up_to_each_run(early_guard_tables, runs):
    seed_rows = early_guard_tables[SEED_SET]

    assert [row[SEED_SET] for row in seed_rows] == [f"{set_name}-{name}" for set_name in SETS for name in MAPS]
    assert [column for column in seed_rows[0] if column in ARMS] == list(ARMS)
    for set_name in SETS:
        set_rows = [row for row in seed_rows if row[SEED_SET].startswith(f"{set_name}-")]
        assert [len(_seeds(row["seeds"])) for row in set_rows] == [int(row["games"]) for row in set_rows]
        for arm in ARMS:
            run = runs[_run_label(arm, set_name)]
            assert sum(int(row["games"]) for row in set_rows) == int(run["games"])
            assert sum(int(row[arm]) for row in set_rows) == int(run["wins"]), run[RUN]


@pytest.mark.parametrize("scope", list(COMBINED_SETS))
def test_benchmarks_early_guard_combined_wins_are_the_sums_and_the_kd_means_weigh_each_set_by_its_games(
    combined_rows, runs, scope
):
    row = combined_rows[scope]
    set_names = COMBINED_SETS[scope]
    games = sum(SETS[set_name] for set_name in set_names)

    assert int(row["games"]) == games
    for arm in ARMS:
        set_runs = [runs[_run_label(arm, set_name)] for set_name in set_names]
        weighed_kd = sum(int(run["games"]) * float(run["kd_mean"]) for run in set_runs) / games
        assert int(row[f"{arm}_wins"]) == sum(int(run["wins"]) for run in set_runs), (scope, arm)
        assert float(row[f"{arm}_kd_mean"]) == pytest.approx(weighed_kd, abs=COMBINED_KD_TOLERANCE), (scope, arm)


@pytest.mark.parametrize(
    ("scope", "arm_on", "arm_off"),
    [("targeted", "guard", "off"), ("all", "guard", "off"), ("all", "both", "guard")],
    ids=["guard on the targeted games", "guard on all the games", "parking on all the games"],
)
def test_benchmarks_early_guard_kd_difference_is_taken_from_the_unrounded_means(
    early_guard_section, combined_rows, scope, arm_on, arm_off
):
    row = combined_rows[scope]
    sentence_start = f"{COMBINED_SCOPES[scope]} the {arm_on} arm"
    sentence = _kd_sentence(sentence_start, f"the {arm_off} arm").search(_plain(early_guard_section))
    kd_on, kd_off = float(row[f"{arm_on}_kd_mean"]), float(row[f"{arm_off}_kd_mean"])

    assert sentence is not None
    assert (int(sentence["wins_on"]), int(sentence["wins_off"])) == (
        int(row[f"{arm_on}_wins"]),
        int(row[f"{arm_off}_wins"]),
    )
    assert int(sentence["games"]) == int(row["games"])
    assert (_whole_number(sentence["kd_on"]), _whole_number(sentence["kd_off"])) == (round(kd_on), round(kd_off))
    stated_difference = _whole_number(sentence["difference"])
    rounded_difference = _whole_number(sentence["kd_on"]) - _whole_number(sentence["kd_off"])
    assert abs(stated_difference - round(kd_on - kd_off)) <= KD_ROUNDING_TOLERANCE
    assert abs(stated_difference - rounded_difference) <= KD_ROUNDING_TOLERANCE


def test_benchmarks_early_guard_rush_checks_are_the_rush_runs_held_to_5_wins(early_guard_tables, runs):
    rows = {row[RUSH_CHECK]: row for row in early_guard_tables[RUSH_CHECK]}

    assert list(rows) == list(RUSH_SETS)
    for set_name, row in rows.items():
        off, guard = (int(runs[_run_label(arm, set_name)]["wins"]) for arm in ("off", "guard"))
        difference = guard - off
        assert int(row["games"]) == SETS[set_name]
        assert (int(row["off"]), int(row["guard"]), int(row["guard_minus_off"])) == (off, guard, difference)
        assert row["passed"] == RUSH_CHECK_RESULTS[difference >= -RUSH_CHECK_WINS], set_name


def test_benchmarks_early_guard_decision_on_the_guard_follows_the_figures_and_records_the_override(
    early_guard_tables, combined_rows, early_guard_decision
):
    targeted = combined_rows["targeted"]
    beats_off = int(targeted["guard_wins"]) > int(targeted["off_wins"]) and float(targeted["guard_kd_mean"]) > float(
        targeted["off_kd_mean"]
    )
    failed_checks = [row[RUSH_CHECK] for row in early_guard_tables[RUSH_CHECK] if row["passed"] == "no"]

    assert beats_off
    assert "The early-attack guard: on." in early_guard_decision
    if failed_checks:
        assert "By the rule the guard would ship off; the user decided to ship it on." in early_guard_decision
        assert "The check is a conservative guard, not a test of significance" in early_guard_decision


def test_benchmarks_early_guard_decision_on_parking_follows_the_both_arm_on_all_the_games(
    combined_rows, early_guard_decision, early_guard_switch_rows
):
    every_game = combined_rows["all"]
    beats_guard = int(every_game["both_wins"]) > int(every_game["guard_wins"]) and float(
        every_game["both_kd_mean"]
    ) > float(every_game["guard_kd_mean"])

    assert f"Harvester parking: {TOGGLE_CELL_VALUES[beats_guard]}, by the rule." in early_guard_decision
    assert _toml_cell(early_guard_switch_rows[PARKING_SWITCH]["shipped"]) is beats_guard


def test_benchmarks_early_guard_confirmation_runs_play_the_set_again_on_fresh_seeds(
    early_guard_section, early_guard_tables, runs
):
    rows = {row[CONFIRM_RUN]: row for row in early_guard_tables[CONFIRM_RUN]}
    played_seeds = set().union(*(_seeds(row["seeds"]) for row in early_guard_tables[SEED_SET]))
    off, guard = (rows[label] for label in CONFIRM_RUNS)
    games = int(off["games"])
    fisher_p = bench.fisher_exact_p(int(off["wins"]), games, int(guard["wins"]), games)

    assert list(rows) == list(CONFIRM_RUNS)
    assert off["seeds"] == guard["seeds"] and len(_seeds(off["seeds"])) == games == int(guard["games"])
    assert not _seeds(off["seeds"]) & played_seeds
    assert (guard["compared_to"], guard["fisher_p"]) == (off[CONFIRM_RUN], f"{fisher_p:.2f}")
    both_sets = BOTH_RU_RUSH_SETS.search(_plain(early_guard_section))
    assert both_sets is not None
    set_runs = [runs[_run_label(arm, CONFIRMED_SET)] for arm in ("off", "guard")]
    assert int(both_sets["games"]) == games + SETS[CONFIRMED_SET]
    assert (int(both_sets["off"]), int(both_sets["guard"])) == tuple(
        int(set_run["wins"]) + int(confirm["wins"]) for set_run, confirm in zip(set_runs, (off, guard))
    )


@pytest.mark.parametrize(("table", "key"), EARLY_GUARD_SWITCHES)
def test_benchmarks_early_guard_switch_is_recorded_with_the_value_tuning_toml_ships(
    early_guard_switch_rows, shipped_tuning, table, key
):
    assert _typed(_toml_cell(early_guard_switch_rows[table, key]["shipped"])) == _typed(shipped_tuning[table][key])


@pytest.mark.parametrize(("table", "key"), EARLY_GUARD_SWITCHES)
def test_benchmarks_early_guard_arms_played_the_switches_as_named_and_the_thresholds_as_shipped(
    early_guard_switch_rows, table, key
):
    row = early_guard_switch_rows[table, key]
    played = [_typed(_toml_cell(row[arm])) for arm in ARMS]

    if (table, key) in (GUARD_SWITCH, PARKING_SWITCH):
        position = (GUARD_SWITCH, PARKING_SWITCH).index((table, key))
        assert played == [_typed(ARM_SWITCHES[arm][position]) for arm in ARMS]
    else:
        assert played == [_typed(_toml_cell(row["shipped"]))] * len(ARMS)


def test_benchmarks_early_guard_records_every_switch_once(early_guard_switch_rows):
    assert list(early_guard_switch_rows) == EARLY_GUARD_SWITCHES


def test_benchmarks_early_guard_decision_states_the_guard_and_parking_as_tuning_toml_ships_them(
    early_guard_decision, shipped_tuning
):
    guard = TOGGLE_CELL_VALUES[shipped_tuning[GUARD_SWITCH[0]][GUARD_SWITCH[1]]]
    parking = TOGGLE_CELL_VALUES[shipped_tuning[PARKING_SWITCH[0]][PARKING_SWITCH[1]]]

    assert f"`config/redalert/tuning.toml`, has the guard {guard} and parking {parking}." in early_guard_decision


# -- docs/BENCHMARKS.md, the economy bundle and army bundle section ------------------------------------

BUNDLES_HEADING = "## Iteration 12: the economy bundle and the army bundle"
BUNDLES_DECISION_HEADING = "### The iteration-12 decision"
BUNDLES_DIAGNOSIS_HEADING = "### The iteration-12 diagnosis"
# The section's tables, by their first header, in order.
ARM_RUN = "arm_run"
COMPARED_PAIR = "compared_pair"
DECISION_SCOPE = "decision_scope"
BUNDLE_SWITCH = "bundle_switch"
# The first headers of the iteration-11 section's tables, all of them used before this section.
EARLY_GUARD_FIRST_HEADERS = {RUN, SEED_SET, COMBINED, RUSH_CHECK, CONFIRM_RUN, SHIPPED_SWITCH}
ENGLAND_SETS = ("en-normal", "en-rush")
RUSSIA_SETS = ("ru-normal", "ru-rush")
# Every arm with the sets it played, in the order of the run table within a set.
BUNDLE_ARM_SETS = {
    "A": tuple(SETS),
    "B": tuple(SETS),
    "C": tuple(SETS),
    "C4": ENGLAND_SETS,
    "C8": ENGLAND_SETS,
    "D": RUSSIA_SETS,
}
BUNDLE_GAMES = 3240
# A compared pair is written as its first arm, this, and its second arm.
PAIR_ARROW = " -> "
# The report rounds each mean, and the difference it takes from the unrounded means, to the hundredth: its difference
# may differ from that of the two printed means by three half-hundredths.
PRINTED_DIFFERENCE_TOLERANCE = 0.02
# The switches of each bundle, by their table and key in tuning.toml, in the order the section records them.
ECONOMY_SWITCHES = [("opening", "order"), ("placement", "refinery_field_by_reach"), ("placement", "creep_toward_ore")]
ARMY_SWITCHES = [
    ("mix", "main_force"),
    ("raids", "standing"),
    ("composition", "allied_raid_light_tanks"),
    ("composition", "raid_vehicles"),
    ("composition", "soviet_raid"),
]
RAID_SIZE_SWITCH = ("composition", "raid_vehicles")
SOVIET_RAID_SWITCH = ("composition", "soviet_raid")
# The army switches that are on or off with the bundle; the raid size and the Soviet raid are chosen apart.
ARMY_BUNDLE_SWITCHES = [switch for switch in ARMY_SWITCHES if switch not in (RAID_SIZE_SWITCH, SOVIET_RAID_SWITCH)]
# The arms of the raid-size choice with the raid size each played, and those of the Soviet-raid choice with its raid.
RAID_SIZE_ARMS = {"C4": 4, "C": 6, "C8": 8}
SOVIET_RAID_ARMS = {"C": "apc_rockets", "D": "none"}
# The arm each bundle is measured with, and the decision scope row of the army arm.
ECONOMY_ARM = "B"
ARMY_BUNDLE_ARM = "C"
ARMY_SCOPE = ("army", "army")
# Each comparison sentence of the decision: the scope rows it compares and the words before " won".
BUNDLE_KD_SENTENCES = [
    (("economy", "B"), ("economy", "A"), "Over all the games B", "A"),
    (("soviet_raid", "C"), ("soviet_raid", "D"), "Over the Russia games C", "D"),
    (ARMY_SCOPE, ("economy", "B"), "Over its games the army arm", "B"),
    (ARMY_SCOPE, ("economy", "A"), "the army arm", "A"),
]
RAID_SIZE_SENTENCE = re.compile(
    r"over the (?P<games>\d+) England games C4 won (?P<C4>\d+) at a `kd_mean` of (?P<kd_C4>-?[\d,]+), C won "
    r"(?P<C>\d+) at (?P<kd_C>-?[\d,]+) and C8 won (?P<C8>\d+) at (?P<kd_C8>-?[\d,]+): the wins fall as the raid group "
    r"grows\."
)


def _bundle_label(arm: str, set_name: str) -> str:
    """The harness label of an arm's set, such as i12-C4-en-rush."""
    return f"i12-{arm}-{set_name}"


def _beats(row_on: dict[str, str], row_off: dict[str, str]) -> bool:
    """Whether the games of one decision scope row beat another's: more wins at a higher mean kd."""
    return int(row_on["wins"]) > int(row_off["wins"]) and float(row_on["kd_mean"]) > float(row_off["kd_mean"])


def _bundle_decisions(scopes: dict[tuple[str, str], dict[str, str]]) -> dict[str, object]:
    """The rule's decisions, in order, from the decision scopes: the Soviet raid's arm, each bundle, the raid size arm."""
    economy_on = _beats(scopes["economy", ECONOMY_ARM], scopes["economy", "A"])
    soviet_raid_arm = "C" if _beats(scopes["soviet_raid", "C"], scopes["soviet_raid", "D"]) else "D"
    army = scopes[ARMY_SCOPE]
    army_on = _beats(army, scopes["economy", ECONOMY_ARM]) or (not economy_on and _beats(army, scopes["economy", "A"]))
    raid_size_arm = None
    if army_on:
        raid_size_arm = max(
            RAID_SIZE_ARMS,
            key=lambda arm: (int(scopes["raid_size", arm]["wins"]), float(scopes["raid_size", arm]["kd_mean"])),
        )
    return {
        "economy": economy_on or army_on,
        "soviet_raid_arm": soviet_raid_arm,
        "army": army_on,
        "raid_size_arm": raid_size_arm,
    }


@pytest.fixture(scope="module")
def bundles_section(benchmarks_text) -> str:
    """The "Iteration 12: the economy bundle and the army bundle" section of the record, to the next section."""
    return _section(benchmarks_text, BUNDLES_HEADING)


@pytest.fixture(scope="module")
def bundles_decision(bundles_section) -> str:
    """The section's "The iteration-12 decision", up to the next subsection, with wrapping removed."""
    return _plain(_section(bundles_section, BUNDLES_DECISION_HEADING, NEXT_SUBSECTION))


@pytest.fixture(scope="module")
def bundles_tables(bundles_section) -> dict[str, list[dict[str, str]]]:
    """The section's tables by their first header, each as its rows by column."""
    return _tables_by_first_header(bundles_section)


@pytest.fixture(scope="module")
def arm_runs(bundles_tables) -> dict[str, dict[str, str]]:
    """The rows of the section's run table by their label, in table order."""
    return {row[ARM_RUN]: row for row in bundles_tables[ARM_RUN]}


@pytest.fixture(scope="module")
def decision_scopes(bundles_tables) -> dict[tuple[str, str], dict[str, str]]:
    """The rows of the section's decision scope table by their scope and arm, in table order."""
    return {(row[DECISION_SCOPE], row["arm"]): row for row in bundles_tables[DECISION_SCOPE]}


@pytest.fixture(scope="module")
def bundle_switch_rows(bundles_tables) -> dict[tuple[str, str], dict[str, str]]:
    """The rows of the section's switch table by the switch's table and key in tuning.toml."""
    return _rows_by_switch(bundles_tables[BUNDLE_SWITCH], BUNDLE_SWITCH)


@pytest.fixture(scope="module")
def bundle_switches_played(bundle_switch_rows) -> dict[tuple[str, str], dict[str, object]]:
    """The value each arm played each switch with, by the switch's table and key in tuning.toml."""
    return {
        switch: {arm: _toml_cell(row[arm]) for arm in BUNDLE_ARM_SETS} for switch, row in bundle_switch_rows.items()
    }


def test_benchmarks_bundles_section_follows_the_early_guard_section(benchmarks_text):
    assert benchmarks_text.index(BUNDLES_HEADING) > benchmarks_text.index(EARLY_GUARD_HEADING)


def test_benchmarks_bundles_tables_have_first_headers_no_earlier_table_of_the_record_has(
    benchmarks_text, bundles_tables
):
    earlier_first_headers = {table[0][0] for table in _tables(benchmarks_text.split(BUNDLES_HEADING, 1)[0])}

    assert list(bundles_tables) == [ARM_RUN, COMPARED_PAIR, DECISION_SCOPE, BUNDLE_SWITCH]
    assert ALL_EARLIER_FIRST_HEADERS | EARLY_GUARD_FIRST_HEADERS <= earlier_first_headers
    assert not earlier_first_headers & set(bundles_tables)


def test_benchmarks_bundles_runs_are_every_set_each_arm_played_on_the_same_games(arm_runs):
    expected = [
        _bundle_label(arm, set_name) for set_name in SETS for arm, sets in BUNDLE_ARM_SETS.items() if set_name in sets
    ]

    assert list(arm_runs) == expected
    for set_name, games in SETS.items():
        faction, bot = set_name.split("-")
        for arm in (arm for arm, sets in BUNDLE_ARM_SETS.items() if set_name in sets):
            row = arm_runs[_bundle_label(arm, set_name)]
            assert (int(row["games"]), row["faction"], row["bot"]) == (games, SET_FACTIONS[faction], bot), row[ARM_RUN]
            assert 0 <= int(row["wins"]) <= games, row[ARM_RUN]
            assert all(NUMBER.fullmatch(row[column]) for column in ("kd_mean", "income_per_s", "assets_value"))
    assert sum(int(row["games"]) for row in arm_runs.values()) == BUNDLE_GAMES


def test_benchmarks_bundles_compared_pair_figures_are_those_of_its_two_runs(bundles_tables, arm_runs):
    for row in bundles_tables[COMPARED_PAIR]:
        first, second = (arm_runs[_bundle_label(arm, row["set"])] for arm in row[COMPARED_PAIR].split(PAIR_ARROW))
        games = int(first["games"])
        fisher_p = bench.fisher_exact_p(int(first["wins"]), games, int(second["wins"]), games)
        pair = (row[COMPARED_PAIR], row["set"])
        assert int(second["games"]) == games, pair
        assert int(row["wins_difference"]) == int(second["wins"]) - int(first["wins"]), pair
        assert row["fisher_p"] == f"{fisher_p:.2f}", pair
        kd_difference = float(second["kd_mean"]) - float(first["kd_mean"])
        assert float(row["kd_difference"]) == pytest.approx(kd_difference, abs=PRINTED_DIFFERENCE_TOLERANCE), pair


def test_benchmarks_bundles_every_arm_is_compared_with_a_and_each_choice_on_every_set_it_was_played(bundles_tables):
    pairs = [(row[COMPARED_PAIR], row["set"]) for row in bundles_tables[COMPARED_PAIR]]
    with_a = {
        (f"A{PAIR_ARROW}{arm}", set_name) for arm, sets in BUNDLE_ARM_SETS.items() if arm != "A" for set_name in sets
    }
    soviet_raid = {(f"D{PAIR_ARROW}C", set_name) for set_name in RUSSIA_SETS}
    raid_size = {
        (f"{first}{PAIR_ARROW}{second}", name) for first, second in (("C4", "C"), ("C", "C8")) for name in ENGLAND_SETS
    }

    assert len(pairs) == len(set(pairs))
    assert with_a | soviet_raid | raid_size <= set(pairs)


def test_benchmarks_bundles_decision_scope_wins_are_the_sums_and_the_kd_means_weigh_each_run_by_its_games(
    decision_scopes, arm_runs
):
    for scope, row in decision_scopes.items():
        scope_runs = [arm_runs[label] for label in row["runs"].split(", ")]
        games = sum(int(run["games"]) for run in scope_runs)
        weighed_kd = sum(int(run["games"]) * float(run["kd_mean"]) for run in scope_runs) / games
        assert int(row["games"]) == games >= KEEP_OR_DROP_GAMES_AT_LEAST, scope
        assert int(row["wins"]) == sum(int(run["wins"]) for run in scope_runs), scope
        assert float(row["kd_mean"]) == pytest.approx(weighed_kd, abs=COMBINED_KD_TOLERANCE), scope


def test_benchmarks_bundles_decision_scopes_take_the_games_the_rule_names(decision_scopes):
    soviet_raid_arm = _bundle_decisions(decision_scopes)["soviet_raid_arm"]
    expected = {
        **{("economy", arm): [_bundle_label(arm, name) for name in SETS] for arm in ("A", ECONOMY_ARM)},
        **{("soviet_raid", arm): [_bundle_label(arm, name) for name in RUSSIA_SETS] for arm in SOVIET_RAID_ARMS},
        ARMY_SCOPE: [
            *(_bundle_label(ARMY_BUNDLE_ARM, name) for name in ENGLAND_SETS),
            *(_bundle_label(soviet_raid_arm, name) for name in RUSSIA_SETS),
        ],
        **{("raid_size", arm): [_bundle_label(arm, name) for name in ENGLAND_SETS] for arm in RAID_SIZE_ARMS},
    }

    assert {scope: row["runs"].split(", ") for scope, row in decision_scopes.items()} == expected
    assert list(decision_scopes) == list(expected)


@pytest.mark.parametrize(
    ("scope_on", "scope_off", "sentence_start", "arm_off"),
    BUNDLE_KD_SENTENCES,
    ids=["economy", "soviet raid", "army against B", "army against A"],
)
def test_benchmarks_bundles_kd_difference_is_taken_from_the_unrounded_means(
    bundles_decision, decision_scopes, scope_on, scope_off, sentence_start, arm_off
):
    row_on, row_off = decision_scopes[scope_on], decision_scopes[scope_off]
    sentence = _kd_sentence(sentence_start, arm_off).search(bundles_decision)
    kd_on, kd_off = float(row_on["kd_mean"]), float(row_off["kd_mean"])

    assert sentence is not None
    assert (int(sentence["wins_on"]), int(sentence["wins_off"])) == (int(row_on["wins"]), int(row_off["wins"]))
    assert int(sentence["games"]) == int(row_on["games"])
    assert (_whole_number(sentence["kd_on"]), _whole_number(sentence["kd_off"])) == (round(kd_on), round(kd_off))
    stated_difference = _whole_number(sentence["difference"])
    rounded_difference = _whole_number(sentence["kd_on"]) - _whole_number(sentence["kd_off"])
    assert abs(stated_difference - round(kd_on - kd_off)) <= KD_ROUNDING_TOLERANCE
    assert abs(stated_difference - rounded_difference) <= KD_ROUNDING_TOLERANCE


def test_benchmarks_bundles_raid_sizes_are_given_from_their_scope_rows(bundles_decision, decision_scopes):
    sentence = RAID_SIZE_SENTENCE.search(bundles_decision)

    assert sentence is not None
    for arm in RAID_SIZE_ARMS:
        row = decision_scopes["raid_size", arm]
        assert int(sentence["games"]) == int(row["games"])
        assert int(sentence[arm]) == int(row["wins"]), arm
        assert _whole_number(sentence[f"kd_{arm}"]) == round(float(row["kd_mean"])), arm


def test_benchmarks_bundles_decisions_follow_the_figures(decision_scopes, bundles_decision):
    decisions = _bundle_decisions(decision_scopes)
    soviet_raid = SOVIET_RAID_ARMS[decisions["soviet_raid_arm"]]

    assert f"The economy bundle: {TOGGLE_CELL_VALUES[decisions['economy']]}." in bundles_decision
    assert f'The Soviet raid: `"{soviet_raid}"`.' in bundles_decision
    assert f"The army bundle: {TOGGLE_CELL_VALUES[decisions['army']]}." in bundles_decision
    if not decisions["army"]:
        assert "The raid size: not applied, since the army bundle does not ship." in bundles_decision
    if not decisions["economy"] and not decisions["army"]:
        assert "Every decision of the rule fails, so every value ships as in arm A." in bundles_decision


def test_benchmarks_bundles_shipped_values_are_the_decided_ones(
    decision_scopes, bundle_switch_rows, bundle_switches_played
):
    decisions = _bundle_decisions(decision_scopes)
    decided = {switch: played["A"] for switch, played in bundle_switches_played.items()}
    if decisions["economy"]:
        decided.update({switch: bundle_switches_played[switch][ECONOMY_ARM] for switch in ECONOMY_SWITCHES})
    if decisions["army"]:
        decided.update({switch: bundle_switches_played[switch][ARMY_BUNDLE_ARM] for switch in ARMY_SWITCHES})
        decided[SOVIET_RAID_SWITCH] = SOVIET_RAID_ARMS[decisions["soviet_raid_arm"]]
        decided[RAID_SIZE_SWITCH] = RAID_SIZE_ARMS[decisions["raid_size_arm"]]

    for switch, row in bundle_switch_rows.items():
        assert _typed(_toml_cell(row["shipped"])) == _typed(decided[switch]), switch


@pytest.mark.parametrize(("table", "key"), [*ECONOMY_SWITCHES, *ARMY_SWITCHES])
def test_benchmarks_bundles_switch_is_recorded_with_the_value_tuning_toml_ships_with_the_switch_set_off(
    bundle_switch_rows, tuning_switches_off, table, key
):
    assert _typed(_toml_cell(bundle_switch_rows[table, key]["shipped"])) == _typed(tuning_switches_off[table][key])


def test_benchmarks_bundles_records_every_switch_once(bundle_switch_rows):
    assert list(bundle_switch_rows) == [*ECONOMY_SWITCHES, *ARMY_SWITCHES]


def test_benchmarks_bundles_arms_played_the_switches_as_named(bundle_switches_played):
    def differing(arm: str, other: str) -> list[tuple[str, str]]:
        return [
            switch for switch, played in bundle_switches_played.items() if _typed(played[arm]) != _typed(played[other])
        ]

    assert differing(ECONOMY_ARM, "A") == ECONOMY_SWITCHES
    assert differing(ARMY_BUNDLE_ARM, ECONOMY_ARM) == ARMY_SWITCHES
    for arm, raid_vehicles in RAID_SIZE_ARMS.items():
        assert _typed(bundle_switches_played[RAID_SIZE_SWITCH][arm]) == _typed(raid_vehicles), arm
        if arm != ARMY_BUNDLE_ARM:
            assert differing(arm, ARMY_BUNDLE_ARM) == [RAID_SIZE_SWITCH], arm
    for arm, soviet_raid in SOVIET_RAID_ARMS.items():
        assert bundle_switches_played[SOVIET_RAID_SWITCH][arm] == soviet_raid, arm
        if arm != ARMY_BUNDLE_ARM:
            assert differing(arm, ARMY_BUNDLE_ARM) == [SOVIET_RAID_SWITCH], arm


def test_benchmarks_bundles_decision_states_the_bundles_as_tuning_toml_ships_them_with_the_switch_set_off(
    bundles_decision, bundle_switches_played, tuning_switches_off
):
    def bundle_state(switches: list[tuple[str, str]], arm: str) -> str:
        shipped = [_typed(tuning_switches_off[table][key]) for table, key in switches]
        if shipped == [_typed(bundle_switches_played[switch][arm]) for switch in switches]:
            return TOGGLE_CELL_VALUES[True]
        assert shipped == [_typed(bundle_switches_played[switch]["A"]) for switch in switches], arm
        return TOGGLE_CELL_VALUES[False]

    economy = bundle_state(ECONOMY_SWITCHES, ECONOMY_ARM)
    army = bundle_state(ARMY_BUNDLE_SWITCHES, ARMY_BUNDLE_ARM)

    assert f"it has the economy bundle {economy} and the army bundle {army}." in bundles_decision


def test_benchmarks_bundles_diagnosis_says_its_figures_are_counted_from_the_match_records(bundles_section):
    diagnosis = _plain(_section(bundles_section, BUNDLES_DIAGNOSIS_HEADING, NEXT_SUBSECTION))

    assert (
        "The figures of this subsection were counted from the events of the match records, not given by "
        "`scripts/report.py`."
    ) in diagnosis


# -- docs/BENCHMARKS.md, the switch trial section ------------------------------------------------------

SWITCH_TRIAL_HEADING = "## Iteration 13: the batch-13a switches arm by arm"
SWITCH_TRIAL_DECISION_HEADING = "### The iteration-13 decision"
# The section's tables, by their first header, in order.
TRIAL_RUN = "trial_run"
VERSUS_A = "versus_a"
TRIAL_ARM = "trial_arm"
TRIAL_SWITCH = "trial_switch"
# The first headers of the bundles section's tables, all of them used before this section.
BUNDLES_FIRST_HEADERS = {ARM_RUN, COMPARED_PAIR, DECISION_SCOPE, BUNDLE_SWITCH}
# The configurations every arm played, in the order of the run table, each with the seeds of its three maps.
TRIAL_CONFIG_SEEDS = {
    "ru-normal": "sidestep 8000-8011, trapped 8100-8111, shadowfiend-ii 8200-8211",
    "en-normal": "sidestep 8300-8311, trapped 8400-8411, shadowfiend-ii 8500-8511",
    "ru-rush": "sidestep 8600-8611, trapped 8700-8711, shadowfiend-ii 8800-8811",
    "en-rush": "sidestep 8900-8911, trapped 9000-9011, shadowfiend-ii 9100-9111",
}
TRIAL_CONFIG_GAMES = 36
TRIAL_GAMES = 1584
# The rush configurations, each with the column of the arm table that gives an arm's wins there less A's.
TRIAL_RUSH_COLUMNS = {"ru-rush": "ru_rush_difference", "en-rush": "en_rush_difference"}
# The scope of a versus_a row over all four configurations together.
ALL_CONFIGS = "all"
# An arm beats A only if it wins at most this many games fewer than A on each rush configuration.
TRIAL_RUSH_LOSS_MAX = 3
TRIAL_REFERENCE = "A"
BEATS_A_CELLS = {True: "yes", False: "no"}
# The switches the trial measured, by their table and key in tuning.toml, in the order the section records them.
CREEP_NEXT_PATCH = ("placement", "creep_next_patch")
REFINERY_FIELD_CAP = ("upkeep", "refinery_field_cap")
FRESH_ORE = ("upkeep", "harvesters_to_fresh_ore")
SECOND_YARD = ("expansion", "second_yard")
TANK_FACTORIES = ("producers", "tank_factories")
RESCOUT = ("scouting", "rescout")
INDIRECT_ROUTE = ("scouting", "indirect_route")
RUSH_DETECTION = ("scouting", "rush_detection")
DEFENSE_ON_THREAT = ("opening", "defense_on_threat")
FORWARD_HOLD = ("combat", "forward_hold")
TRIAL_SWITCHES = [
    CREEP_NEXT_PATCH,
    REFINERY_FIELD_CAP,
    FRESH_ORE,
    SECOND_YARD,
    TANK_FACTORIES,
    RESCOUT,
    INDIRECT_ROUTE,
    RUSH_DETECTION,
    DEFENSE_ON_THREAT,
    FORWARD_HOLD,
]
# Every arm with the switches it played on, in the order of the run table; A played every one off.
TRIAL_ARM_SWITCHES = {
    TRIAL_REFERENCE: (),
    "creep": (CREEP_NEXT_PATCH,),
    "cap": (CREEP_NEXT_PATCH, REFINERY_FIELD_CAP),
    "fresh": (FRESH_ORE,),
    "yard": (CREEP_NEXT_PATCH, SECOND_YARD),
    "tankfac": (TANK_FACTORIES,),
    "rescout": (RESCOUT,),
    "indirect": (RESCOUT, INDIRECT_ROUTE),
    "rushdef": (RUSH_DETECTION, DEFENSE_ON_THREAT),
    "fwd": (FORWARD_HOLD,),
    "all": tuple(TRIAL_SWITCHES),
}
# The arm that measures each switch, and the switch a dependent switch ships only with.
SWITCH_ARM = {
    CREEP_NEXT_PATCH: "creep",
    REFINERY_FIELD_CAP: "cap",
    FRESH_ORE: "fresh",
    SECOND_YARD: "yard",
    TANK_FACTORIES: "tankfac",
    RESCOUT: "rescout",
    INDIRECT_ROUTE: "indirect",
    RUSH_DETECTION: "rushdef",
    DEFENSE_ON_THREAT: "rushdef",
    FORWARD_HOLD: "fwd",
}
DEPENDS_ON = {REFINERY_FIELD_CAP: CREEP_NEXT_PATCH, SECOND_YARD: CREEP_NEXT_PATCH, INDIRECT_ROUTE: RESCOUT}
# The verdict sentence of each arm in the decision: whether it beats A, and the switches it ships on or off.
TRIAL_ARM_VERDICT = re.compile(r"It (?P<verdict>beats|does not beat) A, so (?P<switches>.+?)\.")
SHIPS = re.compile(r"`\[(?P<table>[a-z_.]+)\] (?P<key>[a-z_]+)` ships (?P<state>on|off)")
TRIAL_FISHER_P = re.compile(r"a Fisher p of (?P<p>\d\.\d\d)")


def _trial_label(arm: str, config: str) -> str:
    """The harness label of an arm's configuration, such as i13-rescout-en-rush."""
    return f"i13-{arm}-{config}"


def _trial_arm_beats_a(row: dict[str, str], reference: dict[str, str]) -> bool:
    """The rule: more wins at a higher mean kd over all the games, and neither rush configuration more than 3 down."""
    rush_differences = [int(row[column]) for column in TRIAL_RUSH_COLUMNS.values()]
    return (
        int(row["wins"]) > int(reference["wins"])
        and float(row["kd_mean"]) > float(reference["kd_mean"])
        and min(rush_differences) >= -TRIAL_RUSH_LOSS_MAX
    )


def _trial_shipped(arms: dict[str, dict[str, str]]) -> dict[tuple[str, str], bool]:
    """The value the rule ships each switch with.

    A switch whose own arm beats A ships on, a dependent one only with the
    switch it needs. When the all arm beats A and every single-switch arm
    that would ship, every winning switch ships, which is the same set.
    """
    reference = arms[TRIAL_REFERENCE]
    beats = {arm: _trial_arm_beats_a(row, reference) for arm, row in arms.items() if arm != TRIAL_REFERENCE}
    winning = {switch for switch, arm in SWITCH_ARM.items() if beats[arm]}
    return {switch: switch in winning and DEPENDS_ON.get(switch, switch) in winning for switch in TRIAL_SWITCHES}


@pytest.fixture(scope="module")
def switch_trial_section(benchmarks_text) -> str:
    """The "Iteration 13: the batch-13a switches arm by arm" section of the record, to the next section."""
    return _section(benchmarks_text, SWITCH_TRIAL_HEADING)


@pytest.fixture(scope="module")
def switch_trial_decision(switch_trial_section) -> str:
    """The section's "The iteration-13 decision", up to the next subsection, with wrapping removed."""
    return _plain(_section(switch_trial_section, SWITCH_TRIAL_DECISION_HEADING, NEXT_SUBSECTION))


@pytest.fixture(scope="module")
def switch_trial_tables(switch_trial_section) -> dict[str, list[dict[str, str]]]:
    """The section's tables by their first header, each as its rows by column."""
    return _tables_by_first_header(switch_trial_section)


@pytest.fixture(scope="module")
def trial_runs(switch_trial_tables) -> dict[str, dict[str, str]]:
    """The rows of the section's run table by their label, in table order."""
    return {row[TRIAL_RUN]: row for row in switch_trial_tables[TRIAL_RUN]}


@pytest.fixture(scope="module")
def trial_arms(switch_trial_tables) -> dict[str, dict[str, str]]:
    """The rows of the section's arm table, all four configurations together, by their arm."""
    return {row[TRIAL_ARM]: row for row in switch_trial_tables[TRIAL_ARM]}


@pytest.fixture(scope="module")
def trial_switch_rows(switch_trial_tables) -> dict[tuple[str, str], dict[str, str]]:
    """The rows of the section's switch table by the switch's table and key in tuning.toml."""
    return _rows_by_switch(switch_trial_tables[TRIAL_SWITCH], TRIAL_SWITCH)


def test_benchmarks_switch_trial_section_follows_the_bundles_section(benchmarks_text):
    assert benchmarks_text.index(SWITCH_TRIAL_HEADING) > benchmarks_text.index(BUNDLES_HEADING)


def test_benchmarks_switch_trial_tables_have_first_headers_no_earlier_table_of_the_record_has(
    benchmarks_text, switch_trial_tables
):
    earlier_first_headers = {table[0][0] for table in _tables(benchmarks_text.split(SWITCH_TRIAL_HEADING, 1)[0])}

    assert list(switch_trial_tables) == [TRIAL_RUN, VERSUS_A, TRIAL_ARM, TRIAL_SWITCH]
    assert ALL_EARLIER_FIRST_HEADERS | EARLY_GUARD_FIRST_HEADERS | BUNDLES_FIRST_HEADERS <= earlier_first_headers
    assert not earlier_first_headers & set(switch_trial_tables)


def test_benchmarks_switch_trial_names_the_seeds_of_every_configuration(switch_trial_section):
    text = _plain(switch_trial_section)

    for config, seeds in TRIAL_CONFIG_SEEDS.items():
        assert f"{config}: " in text and f"({seeds})" in text, config


def test_benchmarks_switch_trial_runs_are_every_arm_of_every_configuration_with_its_36_games(trial_runs):
    assert list(trial_runs) == [
        _trial_label(arm, config) for config in TRIAL_CONFIG_SEEDS for arm in TRIAL_ARM_SWITCHES
    ]
    for label, row in trial_runs.items():
        assert _trial_label(row["arm"], row["config"]) == label
        assert int(row["games"]) == TRIAL_CONFIG_GAMES and 0 <= int(row["wins"]) <= TRIAL_CONFIG_GAMES, label
        assert all(NUMBER.fullmatch(row[column]) for column in ("kd_mean", "income_per_s", "assets_value")), label
    assert sum(int(row["games"]) for row in trial_runs.values()) == TRIAL_GAMES


def test_benchmarks_switch_trial_arm_wins_are_the_sums_and_its_means_weigh_each_configuration_by_its_games(
    trial_arms, trial_runs
):
    assert list(trial_arms) == list(TRIAL_ARM_SWITCHES)
    for arm, row in trial_arms.items():
        arm_runs = [trial_runs[_trial_label(arm, config)] for config in TRIAL_CONFIG_SEEDS]
        games = sum(int(run["games"]) for run in arm_runs)
        assert int(row["games"]) == games, arm
        assert int(row["wins"]) == sum(int(run["wins"]) for run in arm_runs), arm
        for column in ("kd_mean", "income_per_s", "assets_value"):
            weighed = sum(int(run["games"]) * float(run[column]) for run in arm_runs) / games
            assert float(row[column]) == pytest.approx(weighed, abs=COMBINED_KD_TOLERANCE), (arm, column)


def test_benchmarks_switch_trial_every_arm_is_compared_with_a_on_all_the_games_and_on_each_configuration(
    switch_trial_tables,
):
    pairs = [(row[VERSUS_A], row["scope"]) for row in switch_trial_tables[VERSUS_A]]

    assert pairs == [
        (arm, scope)
        for arm in TRIAL_ARM_SWITCHES
        if arm != TRIAL_REFERENCE
        for scope in (ALL_CONFIGS, *TRIAL_CONFIG_SEEDS)
    ]


def test_benchmarks_switch_trial_versus_a_figures_are_those_of_the_arm_and_of_a(
    switch_trial_tables, trial_runs, trial_arms
):
    for row in switch_trial_tables[VERSUS_A]:
        arm, scope = row[VERSUS_A], row["scope"]
        if scope == ALL_CONFIGS:
            reference, compared = trial_arms[TRIAL_REFERENCE], trial_arms[arm]
        else:
            reference = trial_runs[_trial_label(TRIAL_REFERENCE, scope)]
            compared = trial_runs[_trial_label(arm, scope)]
        games = int(reference["games"])
        fisher_p = bench.fisher_exact_p(int(reference["wins"]), games, int(compared["wins"]), games)
        kd_difference = float(compared["kd_mean"]) - float(reference["kd_mean"])
        assert int(compared["games"]) == games, (arm, scope)
        assert int(row["wins_difference"]) == int(compared["wins"]) - int(reference["wins"]), (arm, scope)
        assert row["fisher_p"] == f"{fisher_p:.2f}", (arm, scope)
        assert float(row["kd_difference"]) == pytest.approx(kd_difference, abs=PRINTED_DIFFERENCE_TOLERANCE), (
            arm,
            scope,
        )


def test_benchmarks_switch_trial_rush_differences_and_verdicts_follow_from_the_figures(trial_arms, trial_runs):
    reference = trial_arms[TRIAL_REFERENCE]

    assert [reference[column] for column in (*TRIAL_RUSH_COLUMNS.values(), "beats_a")] == [NOT_MEASURED] * 3
    for arm, row in trial_arms.items():
        if arm == TRIAL_REFERENCE:
            continue
        for config, column in TRIAL_RUSH_COLUMNS.items():
            wins = int(trial_runs[_trial_label(arm, config)]["wins"])
            reference_wins = int(trial_runs[_trial_label(TRIAL_REFERENCE, config)]["wins"])
            assert int(row[column]) == wins - reference_wins, (arm, config)
        assert row["beats_a"] == BEATS_A_CELLS[_trial_arm_beats_a(row, reference)], arm


def test_benchmarks_switch_trial_decision_gives_each_arm_s_figures_and_fisher_p_from_its_rows(
    switch_trial_decision, switch_trial_tables, trial_arms
):
    reference = trial_arms[TRIAL_REFERENCE]
    combined_p = {
        row[VERSUS_A]: row["fisher_p"] for row in switch_trial_tables[VERSUS_A] if row["scope"] == ALL_CONFIGS
    }
    for arm, fisher_p in combined_p.items():
        row = trial_arms[arm]
        sentence = _kd_sentence(f"The {arm} arm", TRIAL_REFERENCE).search(switch_trial_decision)
        kd_on, kd_off = float(row["kd_mean"]), float(reference["kd_mean"])
        assert sentence is not None, arm
        assert (int(sentence["wins_on"]), int(sentence["wins_off"])) == (int(row["wins"]), int(reference["wins"]))
        assert int(sentence["games"]) == int(row["games"]), arm
        assert (_whole_number(sentence["kd_on"]), _whole_number(sentence["kd_off"])) == (round(kd_on), round(kd_off))
        assert abs(_whole_number(sentence["difference"]) - round(kd_on - kd_off)) <= KD_ROUNDING_TOLERANCE, arm
        stated_p = TRIAL_FISHER_P.search(sentence.group(0))
        assert stated_p is not None and stated_p["p"] == fisher_p, arm


def test_benchmarks_switch_trial_decision_ships_each_switch_as_the_verdicts_of_the_arms_say(
    switch_trial_decision, trial_arms
):
    reference = trial_arms[TRIAL_REFERENCE]
    compared = [arm for arm in trial_arms if arm != TRIAL_REFERENCE]
    verdicts = list(TRIAL_ARM_VERDICT.finditer(switch_trial_decision))
    stated: dict[tuple[str, str], bool] = {}
    assert len(verdicts) == len(compared)
    # The arms' paragraphs come in the order of the arm table, each with the verdict of its arm.
    for arm, verdict in zip(compared, verdicts):
        assert (verdict["verdict"] == "beats") is _trial_arm_beats_a(trial_arms[arm], reference), arm
        for switch in SHIPS.finditer(verdict["switches"]):
            assert SWITCH_ARM[switch["table"], switch["key"]] == arm, (arm, switch.group(0))
            stated[switch["table"], switch["key"]] = switch["state"] == TOGGLE_CELL_VALUES[True]

    assert stated == _trial_shipped(trial_arms)


def test_benchmarks_switch_trial_shipped_values_are_the_decided_ones_by_the_arm_that_measured_each(
    trial_arms, trial_switch_rows
):
    shipped = _trial_shipped(trial_arms)

    for switch, row in trial_switch_rows.items():
        assert _typed(_toml_cell(row["shipped"])) == _typed(shipped[switch]), switch
        assert row["arm"] == SWITCH_ARM[switch], switch


@pytest.mark.parametrize(("table", "key"), TRIAL_SWITCHES)
def test_benchmarks_switch_trial_switch_is_recorded_with_the_value_tuning_toml_ships_with_the_switch_set_off(
    trial_switch_rows, tuning_switches_off, table, key
):
    assert _typed(_toml_cell(trial_switch_rows[table, key]["shipped"])) == _typed(tuning_switches_off[table][key])


def test_benchmarks_switch_trial_records_every_switch_once_with_the_arms_that_played_it_on(trial_switch_rows):
    assert list(trial_switch_rows) == TRIAL_SWITCHES
    for switch, row in trial_switch_rows.items():
        played_on = [arm for arm, switches in TRIAL_ARM_SWITCHES.items() if switch in switches]
        assert row["played_on"].split(", ") == played_on, switch


def test_benchmarks_switch_trial_decision_states_the_switches_as_tuning_toml_ships_them_with_the_switch_set_off(
    switch_trial_decision, tuning_switches_off
):
    shipped_on = [f"`[{table}] {key}`" for table, key in TRIAL_SWITCHES if tuning_switches_off[table][key] is True]

    assert (
        f"The configuration the repository ships, `config/redalert/tuning.toml`, therefore has "
        f"{' and '.join(shipped_on)} on and every other switch of this section off."
    ) in switch_trial_decision


# -- docs/POKEMON-BENCHMARKS.md, the briefing and deciding section -------------------------------------

POKEMON_BENCHMARKS = DOCS / "POKEMON-BENCHMARKS.md"
AGENT_TOML = DOCS.parent / "config" / "agent.toml"
BRIEFING_DECIDING_HEADING = "## Briefing and deciding measured, 2026-10-11"
POKEMON_DECISION_HEADING = "### The decision"
POKEMON_ARMS = ("off", "on", "brief", "decide")
POKEMON_OPPONENTS = ("random", "first", "self")
POKEMON_PHASES = ("draft", "team preview", "turn")
POKEMON_LABEL_GAMES = 30
SCRIPT_ALONE_ARM = "off"
# The arm each switch of [pokemon] was measured with, and the opponents its rule compares that arm with `off` against.
MEASURED_ARMS = {"model_briefing": "brief", "model_decides": "decide"}
RULE_OPPONENTS = ("self", "first")
# The switch the user shipped against its rule's result, with the words of the user's decision the record quotes.
USER_OVERRIDES = {"model_briefing": '"1. yes"'}
FISHER_P_DECIMALS = 3


def _pokemon_label(arm: str, opponent: str) -> str:
    """The label cell of `arm` against `opponent`, as the record writes it."""
    return f"`pk-{arm}-{opponent}-{POKEMON_LABEL_GAMES}`"


@pytest.fixture(scope="module")
def briefing_deciding_section() -> str:
    """The "Briefing and deciding measured" section of the Pokémon benchmark record, up to the next section."""
    return _section(POKEMON_BENCHMARKS.read_text(encoding="utf-8"), BRIEFING_DECIDING_HEADING)


@pytest.fixture(scope="module")
def briefing_deciding_tables(briefing_deciding_section) -> dict[str, list[dict[str, str]]]:
    """The section's tables by their first header (`label`, `decision times of`, `phase`, `opponent`, `switch`)."""
    return _tables_by_first_header(briefing_deciding_section)


@pytest.fixture(scope="module")
def pokemon_labels(briefing_deciding_tables) -> dict[str, dict[str, str]]:
    """The rows of the section's `label` table by their label, in table order."""
    return {row["label"]: row for row in briefing_deciding_tables["label"]}


@pytest.fixture(scope="module")
def pokemon_decision_times(briefing_deciding_tables) -> dict[str, dict[str, str]]:
    """The rows of the section's `decision times of` table by their label, in table order."""
    return {row["decision times of"]: row for row in briefing_deciding_tables["decision times of"]}


@pytest.fixture(scope="module")
def pokemon_switch_rows(briefing_deciding_tables) -> dict[str, dict[str, str]]:
    """The rows of the section's decision table by their switch, without its backticks."""
    return {row["switch"].strip("`"): row for row in briefing_deciding_tables["switch"]}


@pytest.fixture(scope="module")
def shipped_pokemon() -> dict[str, object]:
    """The `[pokemon]` table of config/agent.toml as tomllib reads it."""
    with AGENT_TOML.open("rb") as handle:
        return tomllib.load(handle)["pokemon"]


def _wins(labels: dict[str, dict[str, str]], arm: str, opponent: str) -> int:
    return int(labels[_pokemon_label(arm, opponent)]["won"])


def _rule_result(
    labels: dict[str, dict[str, str]], times: dict[str, dict[str, str]], deadlines: dict[str, float], arm: str
) -> bool:
    """The record's rule for `arm`: at least `off`'s wins against each rule opponent, no unhandled error in any of
    its labels, and the slowest decision of each phase within that phase's deadline."""
    as_many_wins = all(
        _wins(labels, arm, opponent) >= _wins(labels, SCRIPT_ALONE_ARM, opponent) for opponent in RULE_OPPONENTS
    )
    arm_labels = [_pokemon_label(arm, opponent) for opponent in POKEMON_OPPONENTS]
    no_unhandled_error = all(int(labels[label]["unhandled errors"]) == 0 for label in arm_labels)
    within_deadlines = all(
        float(times[label][f"slowest {phase}"]) <= deadlines[phase] for label in arm_labels for phase in POKEMON_PHASES
    )
    return as_many_wins and no_unhandled_error and within_deadlines


def test_pokemon_benchmarks_record_has_one_row_per_arm_and_opponent_with_its_games(pokemon_labels):
    expected = [_pokemon_label(arm, opponent) for arm in POKEMON_ARMS for opponent in POKEMON_OPPONENTS]

    assert list(pokemon_labels) == expected
    for arm in POKEMON_ARMS:
        for opponent in POKEMON_OPPONENTS:
            row = pokemon_labels[_pokemon_label(arm, opponent)]
            assert (row["arm"], row["opponent"]) == (f"`{arm}`", f"`{opponent}`"), row["label"]
            assert int(row["games"]) == POKEMON_LABEL_GAMES, row["label"]


def test_pokemon_benchmarks_figures_are_whole_counts_with_no_more_wins_than_games(pokemon_labels):
    counts = ("won", "fallbacks", "parse degradations", "unhandled errors", "model answers", "differed from the script")
    for label, row in pokemon_labels.items():
        assert all(INTEGER.fullmatch(row[column]) for column in counts), label
        assert int(row["won"]) <= int(row["games"]), label
        assert int(row["differed from the script"]) <= int(row["model answers"]), label


def test_pokemon_benchmarks_differed_share_is_the_differed_answers_over_the_answers_as_the_report_prints_it(
    pokemon_labels,
):
    for label, row in pokemon_labels.items():
        answers, differed = int(row["model answers"]), int(row["differed from the script"])
        assert row["differed share"] == bench.cell(differed / answers if answers else None), label
        assert (answers == 0) == (row["arm"] == f"`{SCRIPT_ALONE_ARM}`"), label


def test_pokemon_benchmarks_decision_times_cover_every_label_with_each_median_at_most_its_slowest(
    pokemon_labels, pokemon_decision_times
):
    assert list(pokemon_decision_times) == list(pokemon_labels)
    for label, row in pokemon_decision_times.items():
        for phase in POKEMON_PHASES:
            assert float(row[f"median {phase}"]) <= float(row[f"slowest {phase}"]), (label, phase)


def test_pokemon_benchmarks_fisher_table_copies_the_wins_and_gives_their_p_value(
    briefing_deciding_tables, pokemon_labels
):
    rows = {row["opponent"].strip("`"): row for row in briefing_deciding_tables["opponent"]}

    assert list(rows) == list(POKEMON_OPPONENTS)
    for opponent, row in rows.items():
        off_wins = _wins(pokemon_labels, SCRIPT_ALONE_ARM, opponent)
        assert int(row["off won"]) == off_wins, opponent
        for arm in MEASURED_ARMS.values():
            arm_wins = _wins(pokemon_labels, arm, opponent)
            fisher_p = bench.fisher_exact_p(arm_wins, POKEMON_LABEL_GAMES, off_wins, POKEMON_LABEL_GAMES)
            assert int(row[f"{arm} won"]) == arm_wins, (opponent, arm)
            assert row[f"{arm} against off, Fisher p"] == f"{fisher_p:.{FISHER_P_DECIMALS}f}", (opponent, arm)


def test_pokemon_benchmarks_rule_result_of_each_switch_follows_from_the_figures(
    briefing_deciding_tables, pokemon_labels, pokemon_decision_times, pokemon_switch_rows
):
    deadlines = {row["phase"]: float(row["deadline ms"]) for row in briefing_deciding_tables["phase"]}

    assert list(deadlines) == list(POKEMON_PHASES)
    assert list(pokemon_switch_rows) == list(MEASURED_ARMS)
    for switch, arm in MEASURED_ARMS.items():
        row = pokemon_switch_rows[switch]
        rule = _rule_result(pokemon_labels, pokemon_decision_times, deadlines, arm)
        assert row["arm"] == f"`{arm}`", switch
        assert _typed(_toml_cell(row["rule result"])) == _typed(rule), switch


@pytest.mark.parametrize("switch", list(MEASURED_ARMS))
def test_pokemon_benchmarks_shipped_value_of_each_switch_is_the_one_config_agent_toml_ships(
    pokemon_switch_rows, shipped_pokemon, switch
):
    assert _typed(_toml_cell(pokemon_switch_rows[switch]["shipped"])) == _typed(shipped_pokemon[switch])


@pytest.mark.parametrize("switch", list(MEASURED_ARMS))
def test_pokemon_benchmarks_a_switch_ships_by_its_rule_unless_the_user_s_override_is_recorded(
    pokemon_switch_rows, switch
):
    row = pokemon_switch_rows[switch]
    by_rule = _toml_cell(row["shipped"]) == _toml_cell(row["rule result"])

    if switch in USER_OVERRIDES:
        assert not by_rule and f"The user decided on 2026-10-11 ({USER_OVERRIDES[switch]})" in row["why"]
    else:
        assert by_rule and row["why"].startswith("By its rule:")


def test_pokemon_benchmarks_section_states_the_shipped_settings_and_what_the_earlier_labels_record(
    briefing_deciding_section, shipped_pokemon
):
    section = _plain(briefing_deciding_section)
    decision = _plain(_section(briefing_deciding_section, POKEMON_DECISION_HEADING, NEXT_SUBSECTION))
    shipped = " and ".join(f"`{switch} = {_toml_value(shipped_pokemon[switch])}`" for switch in MEASURED_ARMS)

    assert f"So `[pokemon]` in `config/agent.toml` ships {shipped}" in decision
    assert "their `summary.json` has no `model_decides` field" in section
    assert "never the live agent's port 11500" in section
