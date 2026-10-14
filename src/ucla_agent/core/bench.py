"""The local benchmark table: one row per game of a label, from the harness's summary and the agent's record.

A benchmark label is a folder of game folders, one per game, each named
`<bot>-<faction>-<map>-<seed>`. A game folder holds what the harness wrote for
the game (`summary.json`, read here as plain JSON data) and, in `records/`,
the agent's record of it. Every figure comes from the label folder alone; the
caller gives the cost of each unit type, from the item table. Two labels are
compared by their totals, with Fisher's exact test of their win counts.

The table is printed as comma-separated lines under the column names: with
every column, a row aligned in fixed-width columns would not fit in 160
characters, while a comma-separated one does and can be pasted into a
spreadsheet as it is.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from math import comb
from pathlib import Path
from typing import Any, Callable, Iterable

from ucla_agent.core import metrics
from ucla_agent.core.stats import numbers

SUMMARY_FILE_NAME = "summary.json"
RECORDS_FOLDER_NAME = "records"
# How a missing value is shown: a game without a record, or a record or summary without the field.
MISSING = "-"
AGENT_KIND = "agent"
WIN, LOSS, DRAW, NO_RESULT = "win", "loss", "draw", metrics.NO_RESULT
OUTCOMES = (WIN, LOSS, DRAW, NO_RESULT)
RUSH, NORMAL = "rush", "normal"
# The fixed benchmark set, in its order: the opponent bot, then our faction, then the map.
BOTS = (RUSH, NORMAL)
FACTIONS = ("england", "russia")
MAPS = ("sidestep", "trapped", "shadowfiend-ii")
CONFIGURATIONS = tuple(f"{bot}-{faction}-{map_id}" for bot in BOTS for faction in FACTIONS for map_id in MAPS)
# Units that gather ore or found a base: what they cost is not army.
NOT_ARMY_UNITS = frozenset({"harv", "mcv"})
RECORD_COLUMNS = ("income_per_s", "barracks_max", "war_factories_max", "low_power_seconds", "compute_ms_p95")
NUMERIC_COLUMNS = ("kd_value", "assets_value", "army_value_produced", *RECORD_COLUMNS)
COLUMNS = ("config", "seed", "result", "termination_reason", *NUMERIC_COLUMNS)
# The build profile: group -> (the summary's count of created items it is read from, the item types it adds up).
# These counts move less from game to game than the results do, so they show a change in the build sooner.
BUILD_PROFILE = {
    "main_tank": ("units_created", frozenset({"2tnk", "3tnk"})),
    "defenses": ("buildings_created", frozenset({"pbox", "ftur", "gun", "tsla"})),
    "power_plants": ("buildings_created", frozenset({"powr", "apwr"})),
    "producers": ("buildings_created", frozenset({"tent", "barr", "weap"})),
}
BUILD_PROFILE_HEADING = "build_profile"
OUTCOMES_HEADING = "outcomes"
MEANS_HEADING = "means"
ALL_GAMES = "all"
GOAL_MET, GOAL_NOT_MET = "goal: met", "goal: not met"
WINS_HEADING = "wins"
DIFFERENCE = "B - A"
SIGNIFICANCE = "fisher_p_two_sided"
_DECIMALS = 2
_GAME_FOLDER_NAME = re.compile(r"(?P<bot>[a-z]+)-(?P<faction>[a-z]+)-(?P<map>.+)-(?P<seed>[0-9]+)")
_KD_ELEMENT, _ASSETS_ELEMENT = 0, 1

UnitCost = Callable[[str], int | None]


@dataclass(frozen=True)
class Game:
    """One game of a label: the bot it was played against, its configuration and its row (column -> value or None).

    `build_counts` holds the summary's count of each build-profile group, and
    is None for a game without a summary.
    """

    bot: str
    config: str
    row: dict[str, Any]
    build_counts: dict[str, int] | None


def read_label(label_folder: Path, unit_cost: UnitCost) -> list[Game]:
    """Every game of a label, in configuration order and then seed order.

    Raises `ValueError` for a sub-folder not named as a game folder and for a
    summary that describes more than one game or no agent seat.
    """
    games = [read_game(folder, unit_cost) for folder in label_folder.iterdir() if folder.is_dir()]
    return sorted(games, key=_order)


def read_game(game_folder: Path, unit_cost: UnitCost) -> Game:
    """One game folder as a table row.

    A game folder without a summary is a game the harness did not finish: its
    result is `no_result`, its seed is the one in its name and its summary
    columns are missing.
    """
    name = _GAME_FOLDER_NAME.fullmatch(game_folder.name)
    if name is None:
        raise ValueError(f"{game_folder} is not a game folder: its name is not <bot>-<faction>-<map>-<seed>")
    config = f"{name['bot']}-{name['faction']}-{name['map']}"
    played = _played_game(game_folder / SUMMARY_FILE_NAME)
    summary_columns = _summary_columns(played, int(name["seed"]), unit_cost)
    record = record_columns(metrics.newest_attempt(game_folder / RECORDS_FOLDER_NAME))
    return Game(name["bot"], config, {"config": config, **summary_columns, **record}, _build_counts(played))


def _order(game: Game) -> tuple[int, str, int]:
    """Configurations of the fixed set in their order, any other after them by name; then seeds in numeric order."""
    position = CONFIGURATIONS.index(game.config) if game.config in CONFIGURATIONS else len(CONFIGURATIONS)
    return position, game.config, game.row["seed"]


# -- the summary -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Played:
    """What a harness summary says of the game of its folder: the game, and the name and entry of the agent's seat."""

    game: dict[str, Any]
    seat_name: str
    seat: dict[str, Any]

    @property
    def stats(self) -> dict[str, Any]:
        """The agent seat's statistics: what it sent and what it built."""
        return self.seat.get("stats") or {}


def _played_game(path: Path) -> _Played | None:
    """The one game a harness summary describes; None when there is no summary or it holds no game."""
    if not path.is_file():
        return None
    games = json.loads(path.read_text(encoding="utf-8")).get("games") or []
    if len(games) > 1:
        raise ValueError(f"{path} describes {len(games)} games, and a game folder holds one")
    return _Played(games[0], *_agent_seat(games[0])) if games else None


def _summary_columns(played: _Played | None, folder_seed: int, unit_cost: UnitCost) -> dict[str, Any]:
    if played is None:
        return {
            "seed": folder_seed,
            "result": NO_RESULT,
            **dict.fromkeys(("termination_reason", "kd_value", "assets_value", "army_value_produced")),
        }
    game, seat = played.game, played.seat
    return {
        "seed": game.get("seed", folder_seed),
        "result": _result(game, played.seat_name),
        "termination_reason": game.get("termination_reason"),
        "kd_value": _element(seat.get("tiebreak"), _KD_ELEMENT),
        "assets_value": _element(seat.get("tiebreak"), _ASSETS_ELEMENT),
        "army_value_produced": _army_value(played.stats.get("units_created"), unit_cost),
    }


def _element(values: Any, index: int) -> Any:
    """The element at `index` of a list; None when the value is no list or has no such element."""
    return values[index] if isinstance(values, list) and len(values) > index else None


def _agent_seat(game: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The name and the entry of the seat whose kind is `agent`; raises `ValueError` unless there is exactly one."""
    seats = [(name, seat) for name, seat in (game.get("seats") or {}).items() if seat.get("kind") == AGENT_KIND]
    if len(seats) != 1:
        raise ValueError(f"game {game.get('session_id')} has {len(seats)} seats of kind agent, and the table needs one")
    return seats[0]


def _result(game: dict[str, Any], agent_seat: str) -> str:
    """The game's result for the agent: no result when the harness says so, a draw without a winner."""
    if game.get("result") == NO_RESULT:
        return NO_RESULT
    winner = game.get("winner_seat")
    if winner is None:
        return DRAW
    return WIN if winner == agent_seat else LOSS


def _army_value(units_created: Any, unit_cost: UnitCost) -> int | None:
    """What the units created cost, harvesters and MCVs left out; None when a unit's cost is unknown or none given."""
    if not isinstance(units_created, dict):
        return None
    army = [(unit_cost(name), count) for name, count in units_created.items() if name.lower() not in NOT_ARMY_UNITS]
    if any(cost is None for cost, _ in army):
        return None
    return sum(cost * count for cost, count in army)


def _build_counts(played: _Played | None) -> dict[str, int] | None:
    """How many items of each build-profile group the agent created; None without a summary."""
    if played is None:
        return None
    return {group: _created(played.stats.get(source), types) for group, (source, types) in BUILD_PROFILE.items()}


def _created(counts: Any, types: frozenset[str]) -> int:
    """The items of the given types among a summary's counts of created items; none when the counts are missing."""
    return sum(count for item, count in counts.items() if item.lower() in types) if isinstance(counts, dict) else 0


# -- the record ------------------------------------------------------------------------------------


def record_columns(match: metrics.Match | None) -> dict[str, Any]:
    """The columns a game's record gives: income, producers, low power and compute time; all None without a record."""
    if match is None:
        return dict.fromkeys(RECORD_COLUMNS)
    computed = metrics.compute_metrics(match)
    timings = match.data_of("timing")
    return {
        "income_per_s": (computed.get("income_per_s") or {}).get("mean"),
        "barracks_max": _highest(timing.get("barracks") for timing in timings),
        "war_factories_max": _highest(timing.get("war_factories") for timing in timings),
        "low_power_seconds": computed.get("low_power_seconds"),
        "compute_ms_p95": computed["compute_ms"]["p95"],
    }


def _highest(values: Iterable[Any]) -> float | None:
    known = numbers(values)
    return max(known) if known else None


# -- printing --------------------------------------------------------------------------------------


def table_lines(games: list[Game]) -> list[str]:
    """The table: the column names, then one comma-separated line per game."""
    return [csv_line(COLUMNS), *(csv_line(cell(game.row[column]) for column in COLUMNS) for game in games)]


def build_profile_lines(games: list[Game]) -> list[str]:
    """For each configuration, in table order: its games with a summary and the mean per game of each group."""
    counts_by_config: dict[str, list[dict[str, int]]] = {}
    for game in games:
        counts = counts_by_config.setdefault(game.config, [])
        if game.build_counts is not None:
            counts.append(game.build_counts)
    return [
        csv_line((BUILD_PROFILE_HEADING, "games", *BUILD_PROFILE)),
        *(_build_profile_line(config, counts) for config, counts in counts_by_config.items()),
    ]


def _build_profile_line(config: str, counts: list[dict[str, int]]) -> str:
    means = (mean([game_counts[group] for game_counts in counts]) for group in BUILD_PROFILE)
    return csv_line((config, str(len(counts)), *(cell(value) for value in means)))


def bench_lines(games: list[Game]) -> list[str]:
    """Everything `--bench` prints: the table, the build profile, the totals and the goal line, a blank line between."""
    return [*table_lines(games), "", *build_profile_lines(games), "", *totals_lines(games), "", goal_line(games)]


# -- totals and the goal ---------------------------------------------------------------------------


def by_opponent(games: list[Game]) -> dict[str, list[Game]]:
    """All the games, then the games against each bot: rush and normal, which are always listed, then any other."""
    groups: dict[str, list[Game]] = {bot: [] for bot in BOTS}
    for game in games:
        groups.setdefault(game.bot, []).append(game)
    return {ALL_GAMES: games, **groups}


def wins(games: list[Game]) -> int:
    """How many of the games were won."""
    return _count(games, WIN)


def column_means(games: list[Game]) -> dict[str, float | None]:
    """The mean of each numeric column over the games that have a value in it."""
    return {column: mean(numbers(game.row[column] for game in games)) for column in NUMERIC_COLUMNS}


def totals_lines(games: list[Game]) -> list[str]:
    """The totals block: the games of each outcome, overall and per opponent, then the mean of each numeric column."""
    outcome_lines = [
        csv_line((group, str(len(members)), *(str(_count(members, outcome)) for outcome in OUTCOMES)))
        for group, members in by_opponent(games).items()
    ]
    return [
        csv_line((OUTCOMES_HEADING, "games", *OUTCOMES)),
        *outcome_lines,
        csv_line((MEANS_HEADING, *NUMERIC_COLUMNS)),
        csv_line((ALL_GAMES, *(cell(value) for value in column_means(games).values()))),
    ]


def _count(games: list[Game], outcome: str) -> int:
    return sum(1 for game in games if game.row["result"] == outcome)


def goal_met(games: list[Game]) -> bool:
    """Whether more than half of the games against normal were won and at least half of those against rush.

    A game without a result counts as a game not won. A label without a game
    against one of the two bots says nothing of that half of the goal, so it
    does not meet it.
    """
    groups = by_opponent(games)
    normal, rush = groups[NORMAL], groups[RUSH]
    return bool(normal and rush) and 2 * wins(normal) > len(normal) and 2 * wins(rush) >= len(rush)


def goal_line(games: list[Game]) -> str:
    """`goal: met` or `goal: not met`."""
    return GOAL_MET if goal_met(games) else GOAL_NOT_MET


# -- comparing two labels --------------------------------------------------------------------------


def compare_lines(label_a: str, games_a: list[Game], label_b: str, games_b: list[Game]) -> list[str]:
    """Everything `--bench-compare` prints: the totals of label A, those of label B, then how B differs from A."""
    return [
        csv_line(("A", label_a)),
        *totals_lines(games_a),
        "",
        csv_line(("B", label_b)),
        *totals_lines(games_b),
        "",
        *difference_lines(games_a, games_b),
    ]


def difference_lines(games_a: list[Game], games_b: list[Game]) -> list[str]:
    """B - A for the win counts, overall and per opponent, with the p-value of each; then B - A for each column mean.

    The win counts of two labels of the same set differ by chance as well:
    the p-value says how often a difference at least this large would arise
    if both were played by the same code.
    """
    groups_a, groups_b = by_opponent(games_a), by_opponent(games_b)
    groups = list(groups_a | groups_b)
    pairs = [(groups_a.get(group, []), groups_b.get(group, [])) for group in groups]
    means_a, means_b = column_means(games_a), column_means(games_b)
    return [
        csv_line((WINS_HEADING, *groups)),
        csv_line((DIFFERENCE, *(str(wins(set_b) - wins(set_a)) for set_a, set_b in pairs))),
        csv_line((SIGNIFICANCE, *(_p_value_text(set_a, set_b) for set_a, set_b in pairs))),
        csv_line((MEANS_HEADING, *NUMERIC_COLUMNS)),
        csv_line((DIFFERENCE, *(cell(_difference(means_a[column], means_b[column])) for column in NUMERIC_COLUMNS))),
    ]


def _p_value_text(games_a: list[Game], games_b: list[Game]) -> str:
    p_value = fisher_exact_p(wins(games_a), len(games_a), wins(games_b), len(games_b))
    return MISSING if p_value is None else f"{p_value:.{_DECIMALS}f}"


def _difference(mean_a: float | None, mean_b: float | None) -> float | None:
    return None if mean_a is None or mean_b is None else mean_b - mean_a


def fisher_exact_p(wins_a: int, games_a: int, wins_b: int, games_b: int) -> float | None:
    """The two-sided p-value of Fisher's exact test of wins against other results in two sets of games.

    With the games of each set and the wins of both fixed, every split of the
    wins between the sets has a hypergeometric probability; the p-value adds
    up those of the splits no more likely than the one observed. Each
    probability's numerator is an exact integer, so splits as likely as the
    observed one are never lost to rounding. None when a set has no game.
    """
    if not games_a or not games_b:
        return None
    total_wins = wins_a + wins_b
    others = games_a + games_b - total_wins

    def ways(wins_in_a: int) -> int:
        return comb(total_wins, wins_in_a) * comb(others, games_a - wins_in_a)

    observed = ways(wins_a)
    splits = range(max(0, total_wins - games_b), min(total_wins, games_a) + 1)
    at_most_as_likely = sum(count for wins_in_a in splits if (count := ways(wins_in_a)) <= observed)
    return at_most_as_likely / comb(games_a + games_b, games_a)


def mean(values: list[float]) -> float | None:
    """The mean of some numbers; None when there are none."""
    return sum(values) / len(values) if values else None


def cell(value: Any) -> str:
    """A value as the table shows it: `-` when missing, a fraction to two decimals without trailing zeros."""
    if value is None:
        return MISSING
    if isinstance(value, float):
        # Adding 0.0 turns a rounded negative zero into zero, so no cell shows "-0".
        return f"{round(value, _DECIMALS) + 0.0:.{_DECIMALS}f}".rstrip("0").rstrip(".")
    return str(value)


def csv_line(cells: Iterable[str]) -> str:
    """One comma-separated line, quoting a cell only when it holds a comma or a quote."""
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="").writerow(cells)
    return buffer.getvalue()
