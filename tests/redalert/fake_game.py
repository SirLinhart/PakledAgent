"""A scripted stand-in for the platform's Red Alert match.

`FakeRedAlert` implements the documented state and order contract of the
platform (`docs/reference/altruagent/redalert.md`) and nothing more. It builds
what it is told to build, pays for it over the build time, places buildings
near the construction yard and moves units where they are ordered. A unit
ordered into a transport walks to it and boards it once close; as on the
platform it then leaves `units` and is reported lost although it lives on
aboard, and `unload` puts it back next to the transport under its id. Money is
kept as the game keeps it: the starting sum is cash, what the harvesters earn
is ore held by the refineries up to what they can hold, and both are spent. It
simulates no combat and no opponent: nothing shoots, and nothing happens to
either side that a test did not script. A test passing against it shows that
the agent does what it is specified to do in a known situation. It is not
evidence that the agent wins.

One `step()` is 5 game ticks (0.2 s), the pace at which the platform builds
observations. Orders take effect in the fake's world as soon as they are sent
and their final verdicts appear in the next state's `last_orders`, which is
the lag an agent sees on the platform. Nothing here reads a clock or the
network, and the only randomness (where a new unit appears next to its
factory) comes from the seed, so the same orders always give the same states.

Everything else that happens in a match is scripted. A scenario is a list of
timed steps (`at`, `at_seconds`), each a function that is handed the game in
the step it names and calls the game's scenario methods: it can put enemy
actors on the map and move, damage or remove them, damage or kill the player's
own, emit events, change the kill and loss figures, make the platform lose a
batch or refuse an order, and end the match. A callback registered with
`on_step` runs in every step and can read where the player's units are.

`run` plays a decision-maker through the match the way the vendored runtime
would: one request for a move per state, the answer to each batch handed
back, and at the end the runtime's own final-state function, never a call to
the agent.
"""

from __future__ import annotations

import copy
import math
import random
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from altruagent import WAIT, DecisionContext, GameState, WithReasoning, runner
from ucla_agent.games.redalert.items import (
    NON_COMBAT_TYPES,
    SHIP_QUEUE,
    TICKS_PER_SECOND,
    Item,
    ItemTable,
)
from ucla_agent.games.redalert.placement import footprint

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_ID = "fake-session"
AGENT_ID = "fake-agent"
OPPONENT_ID = "fake-opponent"
OWN_SEAT = "Multi0"
ENEMY_SEAT = "Multi1"

TICKS_PER_STEP = 5
STEPS_PER_SECOND = TICKS_PER_SECOND // TICKS_PER_STEP
STARTING_CASH = 5000
MAX_MINUTES = 20
SECONDS_PER_MINUTE = 60
MAP_ID = "fake-arena"
MAP_SIZE = 96
MAP_BOUNDS = [0, 0, MAP_SIZE, MAP_SIZE]
OWN_SPAWN = (22, 48)
ENEMY_SPAWN = (74, 48)
# An ore patch lies this far from each spawn: 8 cells to the north.
ORE_PATCH_OFFSET = (0, -8)
ORE_PATCH_REACH = 1
# A building may be placed this far from the construction yard at most.
PLACE_RADIUS_CELLS = 16
# An enemy actor is seen when one of the player's units or buildings is this close to it.
SIGHT_CELLS = 8
# The player's actors are sorted into squares of 2 ** 3 = 8 cells, the sight distance, so that an enemy is
# compared only with the actors in its own square and the eight around it.
SIGHT_SQUARE_SHIFT = 3
NEIGHBOUR_SQUARES = (-1, 0, 1)
# Each harvester brings in 25 credits of ore per second.
INCOME_PER_HARVESTER_PER_TICK = 1
# Production work is counted in thirds of a tick, so that a low-power tick (a third of the work) stays a whole number.
WORK_PER_TICK = 3
WORK_PER_LOW_POWER_TICK = 1
EVENT_WINDOW = 20
FIRST_ACTOR_ID = 100
FULL_HP = 100
# Float error left after a walk of whole steps; far below any distance a unit covers in one step.
ARRIVAL_TOLERANCE = 1e-9
# What one refinery can hold of the ore brought in; what is earned beyond it is lost.
RESOURCE_CAPACITY_PER_REFINERY = 2000
# A transport is a unit whose item has this role. It carries this many units, as an APC does, and a unit ordered into
# it boards once it is this close.
TRANSPORT_ROLE = "transport"
TRANSPORT_CAPACITY = 5
BOARD_REACH_CELLS = 2
# Unloaded units are put on the free cells this close to their transport.
UNLOAD_REACH_CELLS = 2

MAX_ORDERS_PER_BATCH = 20
MAX_UNITS_PER_ORDER = 50
MAX_TRAIN_COUNT = 10
MAX_ENGINE_COMMANDS = 200
MIN_BATCH_INTERVAL_S = 0.2
MILLISECONDS_PER_SECOND = 1000
STALE_SECONDS = 120
# `recent_order_problems` covers this many settled batches and holds at most this many rows.
PROBLEM_BATCHES = 5
PROBLEM_ROWS = 10
INVALID_ACTION = "INVALID_ACTION"
GAME_ALREADY_COMPLETE = "GAME_ALREADY_COMPLETE"
# The reason the platform gives with a `lost` verdict.
NOT_APPLIED = "not_applied"
# The reason the game gives for a deploy it refuses: the MCV has no room to unfold.
NO_ROOM = "invalid"
# Where the engine puts the top-left corner of the yard an MCV unfolds, from the MCV's cell (its Offset -1,-1), and
# the (width, height) of the cells the yard covers from there.
YARD_UNFOLD_OFFSET = (-1, -1)
YARD_FOOTPRINT = (3, 4)
WRECK_SUFFIX = "husk"
OUTCOME_BY_RETURN = {1.0: "win", 0.0: "loss", 0.5: "draw"}
NO_RESULT = "no_result"
WINNER_SEAT_BY_OUTCOME = {"win": OWN_SEAT, "loss": ENEMY_SEAT}

MCV = "mcv"
CONSTRUCTION_YARD = "fact"
REFINERY = "proc"
HARVESTER = "harv"
HARVEST = "harvest"
DEFAULT_STANCE = "attack_anything"
STANCES = ("hold_fire", "return_fire", "defend", DEFAULT_STANCE)
QUEUES = ("Building", "Defense", "Infantry", "Vehicle")
# Queues whose finished items wait until they are placed.
PLACED_QUEUES = ("Building", "Defense")
QUEUE_PRODUCERS = {
    "Building": (CONSTRUCTION_YARD,),
    "Defense": (CONSTRUCTION_YARD,),
    "Infantry": ("tent", "barr"),
    "Vehicle": ("weap",),
}
# The queue whose item in progress a building of each type reports as `producing`.
REPORTED_QUEUE = {CONSTRUCTION_YARD: "Building", "tent": "Infantry", "barr": "Infantry", "weap": "Vehicle"}
PROBLEM_STATUSES = ("rejected", "partial", "lost")
PROBLEM_ROW_KEYS = ("index", "cmd", "status", "reason", "detail")
# The `legal_actions` lists that hold ids or names; all of them are empty once the match is over.
LEGAL_ACTION_LISTS = (
    "build",
    "train",
    "place",
    "deploy",
    "repair",
    "attack",
    "combat_units",
    "idle_units",
    "harvesters",
    "production_buildings",
)
HOW_TO_SEND = (
    "play_action(session_id, state_version=<this state's state_version>, "
    'action={"type": "orders", "orders": [<order>, ...]})'
)

# cmd -> (required fields, optional fields); any other field makes an order invalid.
ORDER_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "move": (frozenset({"units", "to"}), frozenset({"queued"})),
    "attack_move": (frozenset({"units", "to"}), frozenset({"queued"})),
    "attack": (frozenset({"units"}), frozenset({"target", "to", "queued"})),
    "stop": (frozenset({"units"}), frozenset()),
    "deploy": (frozenset({"units"}), frozenset()),
    "unload": (frozenset({"units"}), frozenset()),
    "guard": (frozenset({"units", "target"}), frozenset({"queued"})),
    "enter_transport": (frozenset({"units", "target"}), frozenset({"queued"})),
    "harvest": (frozenset({"units"}), frozenset({"to", "queued"})),
    "set_stance": (frozenset({"units", "stance"}), frozenset()),
    "build": (frozenset({"item"}), frozenset()),
    "train": (frozenset({"item"}), frozenset({"count"})),
    "place": (frozenset({"item"}), frozenset({"at"})),
    "cancel": (frozenset({"item"}), frozenset()),
    "sell": (frozenset({"building"}), frozenset()),
    "repair": (frozenset({"building"}), frozenset({"on"})),
    "power_toggle": (frozenset({"building"}), frozenset({"on"})),
    "set_primary": (frozenset({"building"}), frozenset()),
    "rally": (frozenset({"building", "to"}), frozenset()),
}
# Unit orders that may also name the player's own buildings, such as defenses.
BUILDING_TAKES = frozenset({"attack", "stop", "set_stance", "guard"})
# Orders for a building that only a building which produces something takes.
PRODUCER_ORDERS = frozenset({"set_primary", "rally"})


def _cells_around(reach: int) -> list[tuple[int, int]]:
    """The offsets within `reach` cells of a cell, itself left out, nearest first and then row by row."""
    offsets = [
        (dx, dy)
        for dy in range(-reach, reach + 1)
        for dx in range(-reach, reach + 1)
        if 0 < dx * dx + dy * dy <= reach * reach
    ]
    return sorted(offsets, key=lambda offset: (offset[0] ** 2 + offset[1] ** 2, offset[1], offset[0]))


PLACE_OFFSETS = _cells_around(PLACE_RADIUS_CELLS)
UNLOAD_OFFSETS = _cells_around(UNLOAD_REACH_CELLS)
# Where a new unit may appear: the cells one or two away from the building that made it.
SPAWN_OFFSETS = [(dx, dy) for dy in range(-2, 3) for dx in range(-2, 3) if (dx, dy) != (0, 0)]
# A building keeps the cells next to it clear, so two buildings never touch.
FOOTPRINT_OFFSETS = [(dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]


def _is_whole_number(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_cell_on_the_map(value: Any) -> bool:
    is_cell = isinstance(value, list) and len(value) == 2 and all(_is_whole_number(part) for part in value)
    return is_cell and 0 <= value[0] < MAP_SIZE and 0 <= value[1] < MAP_SIZE


def _within(cell: tuple[int, int], other: tuple[int, int], reach: int) -> bool:
    return (cell[0] - other[0]) ** 2 + (cell[1] - other[1]) ** 2 <= reach * reach


def _ids(actors: Iterable[dict[str, Any]], **having: Any) -> list[int]:
    """The ids of the listed actors whose fields have the given values."""
    return [actor["id"] for actor in actors if all(actor[name] == value for name, value in having.items())]


def _needing_repair(buildings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [building for building in buildings if building["hp"] < FULL_HP and not building["repairing"]]


def _producers(buildings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The buildings that produce something, as `legal_actions.production_buildings` lists them."""
    return [{"id": b["id"], "type": b["type"]} for b in buildings if b["type"] in REPORTED_QUEUE]


def _targets(observation: dict[str, Any]) -> list[dict[str, Any]]:
    """The enemy units, then buildings, in sight that are worth attacking: wrecks are left out."""
    enemies = observation["visible_enemies"] + observation["visible_enemy_buildings"]
    return [enemy for enemy in enemies if not enemy.get("wreck")]


def _nothing_to_do() -> dict[str, Any]:
    """The `legal_actions` of a finished match: every list is there and empty."""
    nothing: dict[str, Any] = {name: [] for name in LEGAL_ACTION_LISTS}
    return {**nothing, "attack_now": None, "enemy_base": list(ENEMY_SPAWN), "how_to_send": HOW_TO_SEND}


def _order_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """How many orders of a batch were accepted and how many rejected, as its summary gives them."""
    rejected = sum(1 for row in rows if row["status"] == "rejected")
    return {"accepted_orders": len(rows) - rejected, "rejected_orders": rejected}


def _attack_now(fighters: list[dict[str, Any]], targets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The ready-made attack: the given units attack-move to the nearest enemy in sight, else to the enemy's start."""
    group = fighters[:MAX_UNITS_PER_ORDER]
    if not group:
        return None
    middle_x = sum(unit["x"] for unit in group) / len(group)
    middle_y = sum(unit["y"] for unit in group) / len(group)
    nearest = min(targets, key=lambda enemy: (enemy["x"] - middle_x) ** 2 + (enemy["y"] - middle_y) ** 2, default=None)
    goal = list(ENEMY_SPAWN) if nearest is None else [nearest["x"], nearest["y"]]
    return {"cmd": "attack_move", "units": _ids(group), "to": goal}


def _row(
    index: int,
    cmd: Any,
    status: str,
    reason: str,
    detail: str | None = None,
    *,
    commands: int = 0,
    failed: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One order's result row, as `play_action` and `last_orders` give it."""
    return {
        "index": index,
        "cmd": cmd if isinstance(cmd, str) else None,
        "status": status,
        "reason": reason,
        "detail": detail,
        "commands": commands,
        "failed": failed or [],
    }


@dataclass
class _Actor:
    """One of the player's own units or buildings."""

    id: int
    type: str
    x: float
    y: float
    hp: int = FULL_HP
    activity: str | None = None
    destination: tuple[int, int] | None = None
    # The id of the enemy actor a unit was ordered to attack and walks after.
    chasing: int | None = None
    # The id of the player's transport a unit was ordered into and walks to.
    boarding: int | None = None
    stance: str = DEFAULT_STANCE
    powered_down: bool = False
    repairing: bool = False
    rally: list[int] | None = None

    @property
    def cell(self) -> tuple[int, int]:
        """The cell the actor stands on."""
        return int(self.x + 0.5), int(self.y + 0.5)


@dataclass
class _Enemy:
    """An enemy unit or building that a scenario put on the map."""

    id: int
    type: str
    x: int
    y: int
    is_building: bool
    hp: int = FULL_HP
    # True or False when a scenario forces it visible or hidden; None while the sight rule decides.
    shown: bool | None = None

    @property
    def cell(self) -> tuple[int, int]:
        """The cell the actor stands on."""
        return self.x, self.y

    @property
    def is_wreck(self) -> bool:
        """Whether this is the burning wreck a destroyed vehicle leaves."""
        return self.type.endswith(WRECK_SUFFIX)

    def view(self, *, visible: bool | None = None) -> dict[str, Any]:
        """The actor as an observation lists it; with `visible` given, as a scenario reads it."""
        seen: dict[str, Any] = {"id": self.id, "type": self.type, "x": self.x, "y": self.y, "hp": self.hp}
        if self.is_wreck:
            seen["wreck"] = True
        if visible is not None:
            seen.update(building=self.is_building, visible=visible)
        return seen


@dataclass(frozen=True)
class ScenarioStep:
    """One thing a scenario does: `apply` is handed the game in the step of the match numbered `step`."""

    step: int
    apply: Callable[[FakeRedAlert], None]


def at(step: int, apply: Callable[[FakeRedAlert], None]) -> ScenarioStep:
    """A scenario step for the given step of the match (step 0 is the first state)."""
    return ScenarioStep(step, apply)


def at_seconds(seconds: float, apply: Callable[[FakeRedAlert], None]) -> ScenarioStep:
    """A scenario step for the state at the given game time."""
    return ScenarioStep(round(seconds * STEPS_PER_SECOND), apply)


def result_object(own_return: float, termination_reason: str) -> dict[str, Any]:
    """What `get_result` gives for a finished match in which the player's return is `own_return`."""
    return {
        "session_id": SESSION_ID,
        "is_terminal": True,
        "status": "completed",
        "returns": {AGENT_ID: own_return, OPPONENT_ID: 1.0 - own_return},
        "your_return": own_return,
        "winner_agent_id": {1.0: AGENT_ID, 0.0: OPPONENT_ID}.get(own_return),
        "termination_reason": termination_reason,
        "failure_reason": None,
    }


@dataclass
class FakeRun:
    """What one `FakeRedAlert.run` did."""

    # Every action object the decision-maker returned for sending, in order, and the answer to each.
    batches: list[dict[str, Any]] = field(default_factory=list)
    answers: list[dict[str, Any]] = field(default_factory=list)
    # How many requests for a move were answered with WAIT.
    waits: int = 0
    # How many batches the fake refused whole.
    invalid_actions: int = 0
    # What the runtime's final-state function returned for the last state and the result.
    final_state: GameState | None = None
    # Every state of the run, the last one included, when the run was asked to keep them.
    states: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Job:
    """One item in a production queue."""

    item: str
    cost: int
    # Thirds of a tick of work the item needs, and how many are done.
    total: int
    done: int = 0
    paid: int = 0
    paused: bool = False
    ready: bool = False


@dataclass
class _Checked:
    """One order after the checks the platform makes before it answers.

    `carry_out` does what the order asks in the fake's world. It returns the
    reason when the game itself then refuses the order, which the platform
    reports only in a later state.
    """

    row: dict[str, Any]
    carry_out: Callable[[], str | None] | None = None


@dataclass
class _Batch:
    """An accepted batch whose final verdicts have not appeared in a state yet."""

    summary: dict[str, Any]
    final_rows: list[dict[str, Any]] = field(default_factory=list)


def _rejected(index: int, cmd: Any, reason: str, detail: str, failed: list[dict[str, Any]] | None = None) -> _Checked:
    return _Checked(_row(index, cmd, "rejected", reason, detail, failed=failed))


def _shape_problem(order: Any) -> str | None:
    """Why an order is not one the platform reads at all, or None when its command and fields are in order."""
    if not isinstance(order, dict):
        return "an order must be an object"
    cmd = order.get("cmd")
    if not isinstance(cmd, str) or cmd not in ORDER_FIELDS:
        return f"unknown cmd {cmd!r}"
    required, optional = ORDER_FIELDS[cmd]
    fields = set(order) - {"cmd"}
    if fields - required - optional:
        return f"unknown field {sorted(fields - required - optional)[0]!r}"
    if required - fields:
        return f"missing field {sorted(required - fields)[0]!r}"
    if not isinstance(order.get("queued", False), bool):
        return "queued must be true or false"
    if not isinstance(order.get("item", ""), str):
        return "item must be a name"
    return None


def _lost_row(row: dict[str, Any]) -> dict[str, Any]:
    """The final row of an order whose batch never reached the game: what was pending is lost."""
    if row["status"] != "pending":
        return dict(row)
    return {**row, "status": "lost", "reason": NOT_APPLIED, "commands": 0}


def _problem_rows(batch_number: int, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows of a settled batch that `recent_order_problems` lists: its rejected, partial and lost orders."""
    return [
        {"batch": batch_number, **{key: row[key] for key in PROBLEM_ROW_KEYS}}
        for row in rows
        if row["status"] in PROBLEM_STATUSES
    ]


def _batch_problem(action: Any) -> str | None:
    """Why an action is not a batch the platform reads, or None when it is 1 to 20 orders."""
    orders = action.get("orders") if isinstance(action, dict) and action.get("type") == "orders" else None
    if not isinstance(orders, list):
        return 'action must be {"type": "orders", "orders": [...]}'
    if not 1 <= len(orders) <= MAX_ORDERS_PER_BATCH:
        return f"a batch takes 1-{MAX_ORDERS_PER_BATCH} orders, got {len(orders)}"
    return None


def _map_layout() -> dict[str, Any]:
    """The map picture of the game reference: open ground, the two spawns and an ore patch north of each."""
    cells = [["."] * MAP_SIZE for _ in range(MAP_SIZE)]
    for spawn_x, spawn_y in (OWN_SPAWN, ENEMY_SPAWN):
        cells[spawn_y][spawn_x] = "S"
        ore_x, ore_y = spawn_x + ORE_PATCH_OFFSET[0], spawn_y + ORE_PATCH_OFFSET[1]
        for dy in range(-ORE_PATCH_REACH, ORE_PATCH_REACH + 1):
            for dx in range(-ORE_PATCH_REACH, ORE_PATCH_REACH + 1):
                cells[ore_y + dy][ore_x + dx] = "$"
    return {
        "origin": [0, 0],
        "rows": ["".join(row) for row in cells],
        "ruler": "".join(str(x % 10) for x in range(MAP_SIZE)),
        "legend": {".": "clear ground", "$": "ore", "S": "spawn"},
    }


def _rules_entry(item: Item) -> dict[str, Any]:
    """One item of the game reference's `rules`, in the platform's shape."""
    entry: dict[str, Any] = {
        "cost": item.cost,
        "build_time_s": item.build_time_s,
        "power": item.power,
        "prerequisites": list(item.prerequisites),
        "queue": item.queue,
        "hp": item.hp,
        "speed": item.speed,
        "range": item.range,
    }
    if item.factions:
        entry["factions"] = list(item.factions)
    return entry


class FakeRedAlert:
    """A fake Red Alert match for one seat. It implements the documented contract only; see the module's note."""

    def __init__(
        self,
        faction: str = "england",
        enemy_faction: str = "russia",
        *,
        scenario: Iterable[ScenarioStep] = (),
        items: ItemTable | None = None,
        seed: int = 0,
    ) -> None:
        """A match about to start: 5,000 cash and one MCV of `faction` on its spawn.

        `scenario` is what happens in the match besides the player's own
        orders; its steps for step 0 are applied here. `items` replaces the
        item facts of `config/redalert/items.toml`, for a test that needs a
        unit of a particular speed or cost.
        """
        self.faction = faction
        self.enemy_faction = enemy_faction
        self.items = items or ItemTable.from_file(REPO_ROOT)
        self.tick = 0
        self.cash = STARTING_CASH
        # What the harvesters have earned and the refineries hold; it is spent before the cash.
        self.ore = 0
        self.kills_cost = 0
        self.deaths_cost = 0
        self.invalid_actions = 0
        # Every order that was carried out, as {"tick", "batch", "index", "order"}.
        self.recorded_orders: list[dict[str, Any]] = []
        self.game_config = self._game_config()
        self._random = random.Random(seed)
        self._next_id = FIRST_ACTOR_ID
        self._units: dict[int, _Actor] = {}
        self._buildings: dict[int, _Actor] = {}
        # Transport id -> the player's units aboard it, in the order they boarded; none of them is in `_units`.
        self._cargo: dict[int, list[_Actor]] = {}
        self._queues: dict[str, list[_Job]] = {queue: [] for queue in QUEUES}
        self._events: deque[dict[str, Any]] = deque(maxlen=EVENT_WINDOW)
        self._event_seq = 0
        self._batches_accepted = 0
        self._last_orders: dict[str, Any] | None = None
        self._in_flight: _Batch | None = None
        self._settled_problems: deque[list[dict[str, Any]]] = deque(maxlen=PROBLEM_BATCHES)
        self._low_power = False
        self._enemies: dict[int, _Enemy] = {}
        self._spotted: set[int] = set()
        self._losses = {"units_lost": 0, "buildings_lost": 0}
        self._scenario: dict[int, list[Callable[[FakeRedAlert], None]]] = {}
        for scenario_step in scenario:
            self._scenario.setdefault(scenario_step.step, []).append(scenario_step.apply)
        self._callbacks: list[Callable[[FakeRedAlert], None]] = []
        self._lost_batches: set[int] = set()
        self._lose_next_batch = False
        self._full_queues: list[str] = []
        self._refused_places: dict[str, int] = {}
        self._refused_deploys = 0
        # Whether an MCV unfolds its yard as the engine does, from the cell one up and one left of its own, and whether
        # a unit standing in the yard's box keeps it from unfolding there.
        self._unfolds_as_the_engine = False
        self._units_block_unfold = False
        # Whether a building is reported, as the engine reports it, at the middle cell of its footprint.
        self._reports_as_the_engine = False
        self._withheld: set[str] = set()
        self._result: dict[str, Any] | None = None
        self._add_unit(MCV, OWN_SPAWN)
        self._apply_scenario()
        self._note_sightings()

    # -- the game reference ------------------------------------------------------------------------

    def _game_config(self) -> dict[str, Any]:
        """What `get_game_config("red_alert")` gives: the item rules, the one map and the match rules."""
        rules: dict[str, Any] = {"buildings": {}, "units": {}}
        for item in self.items:
            rules["buildings" if item.is_building else "units"][item.name] = _rules_entry(item)
        rules["maps"] = {
            MAP_ID: {
                "id": MAP_ID,
                "width": MAP_SIZE,
                "height": MAP_SIZE,
                "bounds": list(MAP_BOUNDS),
                "spawns": [list(OWN_SPAWN), list(ENEMY_SPAWN)],
                "layout": _map_layout(),
            }
        }
        return {"game_type": "red_alert", "maps": [MAP_ID], "match_rules": self._match_rules(), "rules": rules}

    @staticmethod
    def _match_rules() -> dict[str, Any]:
        return {
            "starting_cash": STARTING_CASH,
            "starting_units": "none",
            "explored_map": False,
            "kill_bounties": False,
            "crates": False,
        }

    # -- the state ---------------------------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        """The state object `get_game_state` would return now. Each call builds a new object."""
        observation = self._observation()
        over = self._result is not None
        state = {
            "session_id": SESSION_ID,
            "game_type": "red_alert",
            "status": "completed" if over else "in_progress",
            "state_version": self.tick,
            "phase": "finished" if over else "playing",
            "is_terminal": over,
            "is_current_actor": not over,
            "your_seat": OWN_SEAT,
            "players": self._players(),
            "pacing": {
                "mode": "realtime",
                "min_batch_interval_s": MIN_BATCH_INTERVAL_S,
                "stale_seconds": STALE_SECONDS,
                "next_batch_allowed_in_ms": 0,
            },
            "limits": {
                "max_orders_per_batch": MAX_ORDERS_PER_BATCH,
                "max_units_per_order": MAX_UNITS_PER_ORDER,
                "max_train_count": MAX_TRAIN_COUNT,
                "min_batch_interval_s": MIN_BATCH_INTERVAL_S,
                "stale_seconds": STALE_SECONDS,
            },
            "time": self._time(),
            "map": {"id": MAP_ID, "width": MAP_SIZE, "height": MAP_SIZE, "bounds": list(MAP_BOUNDS)},
            "match_rules": self._match_rules(),
            "observation": observation,
            "last_orders": copy.deepcopy(self._last_orders),
            "recent_order_problems": self._recent_order_problems(),
            "legal_actions": self._legal_actions(observation),
        }
        if over:
            state["result"] = self._outcome()
        return state

    def _outcome(self) -> dict[str, Any]:
        """The `result` a finished match's state carries, from the result object the scenario ended the match with."""
        returns = self._result.get("returns") or {}
        outcome = OUTCOME_BY_RETURN.get(returns.get(AGENT_ID, self._result.get("your_return")), NO_RESULT)
        return {
            "outcome": outcome,
            "reason": self._result.get("termination_reason"),
            "winner_seat": WINNER_SEAT_BY_OUTCOME.get(outcome),
            "returns": self._result.get("returns"),
        }

    def _players(self) -> list[dict[str, Any]]:
        return [
            {"seat": OWN_SEAT, "agent_id": AGENT_ID, "name": AGENT_ID, "faction": self.faction, "checked_in": True},
            {
                "seat": ENEMY_SEAT,
                "agent_id": OPPONENT_ID,
                "name": OPPONENT_ID,
                "faction": self.enemy_faction,
                "checked_in": True,
            },
        ]

    def _time(self) -> dict[str, Any]:
        game_seconds = self.tick / TICKS_PER_SECOND
        return {
            "max_minutes": MAX_MINUTES,
            "game_seconds": game_seconds,
            "remaining_seconds": max(0.0, MAX_MINUTES * SECONDS_PER_MINUTE - game_seconds),
        }

    def _observation(self) -> dict[str, Any]:
        provided, drained = self._power()
        seen = self._seen_enemies()
        return {
            "tick": self.tick,
            "faction": self.faction,
            "enemy_faction": self.enemy_faction,
            "economy": {
                "cash": self.cash,
                "ore": self.ore,
                "power_provided": provided,
                "power_drained": drained,
                "resource_capacity": self._resource_capacity(),
                "harvesters": len(self._harvesters()),
            },
            "military": self._military(),
            "units": [self._unit_view(unit) for unit in self._units.values()],
            "buildings": [self._building_view(building, provided, drained) for building in self._buildings.values()],
            "production": [self._job_view(queue, job) for queue in QUEUES for job in self._queues[queue]],
            "available_production": self._available_production(),
            "visible_enemies": [enemy.view() for enemy in seen if not enemy.is_building],
            "visible_enemy_buildings": [enemy.view() for enemy in seen if enemy.is_building],
            "map": {"id": MAP_ID, "width": MAP_SIZE, "height": MAP_SIZE},
            "base_center": self._base_center(),
            "explored_percent": 0.0,
            "events": list(self._events),
        }

    def _military(self) -> dict[str, int]:
        # The units aboard a transport are alive and the player's own, though the observation does not list them.
        units = [*self._units.values(), *(unit for aboard in self._cargo.values() for unit in aboard)]
        return {
            "kills_cost": self.kills_cost,
            "deaths_cost": self.deaths_cost,
            "assets_value": sum(self.items[actor.type].cost for actor in [*units, *self._buildings.values()]),
            "army_value": sum(self.items[unit.type].cost for unit in units if self._fights(unit)),
            "units_killed": 0,
            "buildings_killed": 0,
            **self._losses,
        }

    def _fights(self, unit: _Actor) -> bool:
        return unit.type not in NON_COMBAT_TYPES and self.items[unit.type].queue != SHIP_QUEUE

    @staticmethod
    def _unit_view(unit: _Actor) -> dict[str, Any]:
        x, y = unit.cell
        return {
            "id": unit.id,
            "type": unit.type,
            "x": x,
            "y": y,
            "hp": unit.hp,
            "act": unit.activity,
            "idle": unit.activity is None,
            "stance": unit.stance,
        }

    def _building_view(self, building: _Actor, provided: int, drained: int) -> dict[str, Any]:
        x, y = self._reported_cell(building)
        starved = drained > provided and self.items[building.type].power < 0
        in_progress = self._queues.get(REPORTED_QUEUE.get(building.type, ""), [])
        return {
            "id": building.id,
            "type": building.type,
            "x": x,
            "y": y,
            "hp": building.hp,
            "powered": not building.powered_down and not starved,
            "powered_down": building.powered_down,
            "repairing": building.repairing,
            "producing": in_progress[0].item if in_progress else None,
            "rally": building.rally,
        }

    def _job_view(self, queue: str, job: _Job) -> dict[str, Any]:
        work_per_tick = WORK_PER_LOW_POWER_TICK if self._is_low_on_power() else WORK_PER_TICK
        return {
            "queue": queue,
            "item": job.item,
            "progress": round(job.done / job.total, 3),
            "remaining_s": round((job.total - job.done) / work_per_tick / TICKS_PER_SECOND, 1),
            "paused": job.paused,
            "ready": job.ready,
        }

    def _base_center(self) -> list[int] | None:
        """The construction yard's cell, else the middle of the buildings, else of the units."""
        yard = self._first_building(CONSTRUCTION_YARD)
        if yard is not None:
            return list(yard.cell)
        group = list(self._buildings.values()) or list(self._units.values())
        if not group:
            return None
        cells = [actor.cell for actor in group]
        return [round(sum(x for x, _ in cells) / len(cells)), round(sum(y for _, y in cells) / len(cells))]

    def _available_production(self) -> list[str]:
        """What can be started now: the faction can build it, its queue has a producer and its prerequisites stand."""
        standing = {building.type for building in self._buildings.values()}
        return [
            item.name
            for item in self.items
            if item.buildable_by(self.faction)
            and item.name not in self._withheld
            and not standing.isdisjoint(QUEUE_PRODUCERS.get(item.queue, ()))
            and standing.issuperset(item.prerequisites)
        ]

    def _recent_order_problems(self) -> list[dict[str, Any]]:
        rows = [dict(row) for batch in self._settled_problems for row in batch]
        return rows[-PROBLEM_ROWS:]

    def _legal_actions(self, observation: dict[str, Any]) -> dict[str, Any]:
        """The short lists of what can be done now, built from the same view as the observation."""
        if self._result is not None:
            return _nothing_to_do()
        units, buildings = observation["units"], observation["buildings"]
        available = observation["available_production"]
        fighters = [unit for unit in units if self._fights(self._units[unit["id"]])]
        idle_fighters = [unit for unit in fighters if unit["idle"]]
        targets = _targets(observation)
        return {
            "build": self._names_of(available, buildings=True),
            "train": self._names_of(available, buildings=False),
            "place": sorted({job.item for job in self._ready_jobs()}),
            "deploy": _ids(units, type=MCV),
            "repair": _ids(sorted(_needing_repair(buildings), key=lambda building: building["hp"])),
            "attack": _ids(targets),
            "attack_now": _attack_now(idle_fighters or fighters, targets),
            "enemy_base": list(ENEMY_SPAWN),
            "combat_units": _ids(idle_fighters),
            "idle_units": _ids(units, idle=True),
            "harvesters": _ids(units, type=HARVESTER),
            "production_buildings": _producers(buildings),
            "how_to_send": HOW_TO_SEND,
        }

    def _names_of(self, available: list[str], *, buildings: bool) -> list[str]:
        """The available items that are buildings, or those that are units."""
        return [name for name in available if self.items[name].is_building == buildings]

    # -- the enemy's side ----------------------------------------------------------------------------

    def _seen_enemies(self) -> list[_Enemy]:
        """The enemy actors the player sees now: those a scenario shows, and those an own actor is close to."""
        if not self._enemies:
            return []
        watched = self._watched_squares()
        return [enemy for enemy in self._enemies.values() if self._is_seen(enemy, watched)]

    def _watched_squares(self) -> dict[tuple[int, int], list[tuple[int, int]]]:
        """The cells of the player's units and buildings, sorted into squares the size of the sight distance."""
        squares: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for actor in (*self._units.values(), *self._buildings.values()):
            x, y = actor.cell
            squares.setdefault((x >> SIGHT_SQUARE_SHIFT, y >> SIGHT_SQUARE_SHIFT), []).append((x, y))
        return squares

    @staticmethod
    def _is_seen(enemy: _Enemy, watched: dict[tuple[int, int], list[tuple[int, int]]]) -> bool:
        if enemy.shown is not None:
            return enemy.shown
        square_x, square_y = enemy.x >> SIGHT_SQUARE_SHIFT, enemy.y >> SIGHT_SQUARE_SHIFT
        near = (
            cell
            for dx in NEIGHBOUR_SQUARES
            for dy in NEIGHBOUR_SQUARES
            for cell in watched.get((square_x + dx, square_y + dy), ())
        )
        return any(_within(cell, enemy.cell, SIGHT_CELLS) for cell in near)

    def _note_sightings(self) -> None:
        """Report each enemy actor the first time the player sees it."""
        for enemy in self._seen_enemies():
            if enemy.id not in self._spotted and not enemy.is_wreck:
                self._spotted.add(enemy.id)
                self.emit_event("enemy_spotted", id=enemy.id, type=enemy.type, x=enemy.x, y=enemy.y)

    # -- the player's own side ---------------------------------------------------------------------

    def _add_unit(self, unit_type: str, cell: tuple[int, int]) -> _Actor:
        activity = HARVEST if unit_type == HARVESTER else None
        unit = _Actor(self._take_id(), unit_type, float(cell[0]), float(cell[1]), activity=activity)
        self._units[unit.id] = unit
        return unit

    def _add_building(self, building_type: str, cell: tuple[int, int]) -> _Actor:
        building = _Actor(self._take_id(), building_type, float(cell[0]), float(cell[1]))
        self._buildings[building.id] = building
        x, y = self._reported_cell(building)
        self.emit_event("building_created", id=building.id, type=building_type, x=x, y=y)
        return building

    def _reported_cell(self, building: _Actor) -> tuple[int, int]:
        """The cell a building is reported at: the top-left cell it was put on, or with `report_as_the_engine` the
        middle cell of its footprint, (width // 2, height // 2) of its body: one right and one down for a building two
        cells or more across and tall."""
        if not self._reports_as_the_engine:
            return building.cell
        width, height = footprint(building.type)
        offset_x, offset_y = (1, 1) if width >= 2 and height >= 2 else (width // 2, height // 2)
        return building.cell[0] + offset_x, building.cell[1] + offset_y

    def _take_id(self) -> int:
        self._next_id += 1
        return self._next_id - 1

    def _first_building(self, *types: str) -> _Actor | None:
        return next((building for building in self._buildings.values() if building.type in types), None)

    def _count_buildings(self, building_type: str) -> int:
        return sum(1 for building in self._buildings.values() if building.type == building_type)

    def _harvesters(self) -> list[_Actor]:
        return [unit for unit in self._units.values() if unit.type == HARVESTER]

    def _resource_capacity(self) -> int:
        """How much ore the refineries that stand can hold."""
        return RESOURCE_CAPACITY_PER_REFINERY * self._count_buildings(REFINERY)

    def _store_income(self) -> None:
        """Add what the harvesters earned in this step to the ore, and lose what the refineries cannot hold."""
        earned = INCOME_PER_HARVESTER_PER_TICK * TICKS_PER_STEP * len(self._harvesters())
        # Without a refinery there is nowhere to hold ore: the capacity is 0 and so is the ore.
        self.ore = min(self._resource_capacity(), self.ore + earned)

    def _pay(self, amount: int) -> None:
        """Take an amount from the player's money: from the ore first, and from the cash what the ore does not cover."""
        from_ore = min(self.ore, amount)
        self.ore -= from_ore
        self.cash -= amount - from_ore

    def _power(self) -> tuple[int, int]:
        """Power provided and power drained by the buildings that are switched on."""
        amounts = [self.items[b.type].power for b in self._buildings.values() if not b.powered_down]
        return sum(amount for amount in amounts if amount > 0), -sum(amount for amount in amounts if amount < 0)

    def _is_low_on_power(self) -> bool:
        provided, drained = self._power()
        return drained > provided

    def emit_event(self, kind: str, **details: Any) -> None:
        """Add one event to the player's window of the last 20; a scenario emits `low_power` or `enemy_killed` so."""
        self._event_seq += 1
        self._events.append({"seq": self._event_seq, "tick": self.tick, "kind": kind, **details})

    def _spawn_next_to(self, building: _Actor, unit_type: str) -> None:
        """Create a unit on a cell one or two away from the building that made it; the seed picks which."""
        dx, dy = self._random.choice(SPAWN_OFFSETS)
        base_x, base_y = building.cell
        self.add_own(unit_type, min(MAP_SIZE - 1, max(0, base_x + dx)), min(MAP_SIZE - 1, max(0, base_y + dy)))

    # -- time ----------------------------------------------------------------------------------------

    def step(self) -> None:
        """Advance the game by 5 ticks; nothing moves any more once the match is over.

        Verdicts land, the scenario's steps for this step are applied,
        harvesters earn, production works, units move, the units that reached
        their transport board it, and then the scenario's callbacks run.
        """
        if self._result is not None:
            return
        self.tick += TICKS_PER_STEP
        self._land_verdicts()
        self._apply_scenario()
        self._store_income()
        self._advance_production()
        for unit in self._units.values():
            goal = self._goal_of(unit)
            if goal is not None:
                self._walk(unit, goal)
        self._board_arrivals()
        for callback in list(self._callbacks):
            callback(self)
        self._note_sightings()
        self._report_power_change()

    def _apply_scenario(self) -> None:
        for apply in self._scenario.get(self.tick // TICKS_PER_STEP, ()):
            apply(self)

    def _goal_of(self, unit: _Actor) -> tuple[int, int] | None:
        """The cell a unit is walking to: where it was sent, or where the enemy it attacks or the transport it was
        ordered into stands now. A unit whose enemy or transport is gone stops."""
        if unit.chasing is None and unit.boarding is None:
            return unit.destination
        target = self._enemies.get(unit.chasing) if unit.boarding is None else self._units.get(unit.boarding)
        if target is None:
            self._task(unit, None)
            return None
        return target.cell

    def _board_arrivals(self) -> None:
        """Take aboard each unit within 2 cells of the transport it was ordered into, while the transport has room.

        As on the platform, a unit that boards leaves the units and is reported lost although it lives on aboard. A
        unit that reaches a full transport stops beside it.
        """
        for unit in [unit for unit in self._units.values() if unit.boarding is not None]:
            transport = self._units[unit.boarding]
            if not _within(unit.cell, transport.cell, BOARD_REACH_CELLS):
                continue
            aboard = self._cargo.setdefault(transport.id, [])
            self._task(unit, None)
            if len(aboard) < TRANSPORT_CAPACITY:
                del self._units[unit.id]
                aboard.append(unit)
                x, y = unit.cell
                self.emit_event("unit_lost", id=unit.id, type=unit.type, x=x, y=y)

    def _advance_production(self) -> None:
        """Work on the first item of every queue that has a producer; an item waiting to be placed blocks its queue."""
        low_power = self._is_low_on_power()
        work = (WORK_PER_LOW_POWER_TICK if low_power else WORK_PER_TICK) * TICKS_PER_STEP
        for queue in QUEUES:
            jobs = self._queues[queue]
            producer = self._first_building(*QUEUE_PRODUCERS[queue])
            if jobs and not jobs[0].ready and producer is not None:
                self._work_on(queue, jobs[0], work, producer)

    def _work_on(self, queue: str, job: _Job, work: int, producer: _Actor) -> None:
        """Do up to `work` on a job, paying for it evenly, and stop where the money runs out."""
        # Work is paid for before it is done: the share of the cost for the work reached, rounded up, is due.
        affordable = (job.paid + self.cash + self.ore) * job.total // job.cost
        reached = min(job.total, job.done + work, affordable)
        job.paused = reached == job.done
        due = -(-job.cost * reached // job.total) - job.paid
        self._pay(due)
        job.paid += due
        job.done = reached
        if job.done < job.total:
            return
        if queue in PLACED_QUEUES:
            job.ready = True
            self.emit_event("production_ready", type=job.item)
        else:
            self._queues[queue].remove(job)
            self._spawn_next_to(producer, job.item)

    def _walk(self, unit: _Actor, goal: tuple[int, int]) -> None:
        """Move a unit one step's worth toward a cell, in a straight line; on arrival it has nothing left to do."""
        reach = self.items[unit.type].speed * TICKS_PER_STEP / TICKS_PER_SECOND
        dx, dy = goal[0] - unit.x, goal[1] - unit.y
        distance = math.hypot(dx, dy)
        if distance <= reach + ARRIVAL_TOLERANCE:
            unit.x, unit.y = float(goal[0]), float(goal[1])
            unit.destination = None
            # A harvester sent to an ore cell goes on harvesting there and a unit that reached the enemy it
            # attacks stays on it; any other unit has nothing left to do.
            if unit.activity != HARVEST and unit.chasing is None:
                unit.activity = None
        else:
            unit.x += dx / distance * reach
            unit.y += dy / distance * reach

    def _report_power_change(self) -> None:
        low_power = self._is_low_on_power()
        if low_power != self._low_power:
            provided, drained = self._power()
            kind = "low_power" if low_power else "power_restored"
            self.emit_event(kind, power_provided=provided, power_drained=drained)
        self._low_power = low_power

    # -- orders: the answer --------------------------------------------------------------------------

    def play_action(self, action: Any) -> dict[str, Any]:
        """Answer one batch the way the platform's `play_action` does.

        Returns the platform's answer (`accepted`, a result row per order, and
        so on). A batch the platform refuses whole is returned as
        `{"error": "INVALID_ACTION", "detail": ...}`, the object the runtime
        hands to `on_action_result` for it, and counted in `invalid_actions`;
        nothing of it is carried out. After the match has ended every batch is
        answered with `GAME_ALREADY_COMPLETE`.
        """
        if self._result is not None:
            return {"error": GAME_ALREADY_COMPLETE, "detail": "The match is over"}
        problem = _batch_problem(action)
        if problem is not None:
            return self._refuse(problem)
        orders = action["orders"]
        checked = [self._check(index, order) for index, order in enumerate(orders)]
        commands = sum(one.row["commands"] for one in checked)
        if commands > MAX_ENGINE_COMMANDS:
            return self._refuse(f"the batch expands to {commands} engine commands; the limit is {MAX_ENGINE_COMMANDS}")
        if all(one.row["status"] == "rejected" for one in checked):
            reasons = (
                f"[{one.row['index']}] {one.row['cmd']}: {one.row['reason']} ({one.row['detail']})" for one in checked
            )
            return self._refuse("No valid order: " + "; ".join(reasons))
        return self._accept(orders, checked)

    def _refuse(self, detail: str) -> dict[str, Any]:
        self.invalid_actions += 1
        return {"error": INVALID_ACTION, "detail": detail}

    def _accept(self, orders: list[dict[str, Any]], checked: list[_Checked]) -> dict[str, Any]:
        """Carry out the orders that passed the checks and return the platform's answer for the batch."""
        self._land_verdicts()
        self._batches_accepted += 1
        rows = [one.row for one in checked]
        summary = {
            "state_version": self.tick,
            "batch": self._batches_accepted,
            "carried": 0,
            "pending": True,
            "applied_at_tick": None,
            "results": rows,
            **_order_counts(rows),
        }
        lost = self._lose_next_batch or self._batches_accepted in self._lost_batches
        self._lose_next_batch = False
        final_rows = [
            _lost_row(one.row) if lost else self._final_row(order, one) for order, one in zip(orders, checked)
        ]
        self._in_flight = _Batch(summary, final_rows)
        self._last_orders = summary
        answered = ("state_version", "pending", "applied_at_tick", "accepted_orders", "rejected_orders", "results")
        return {
            "accepted": True,
            "session_id": SESSION_ID,
            "status": "in_progress",
            "next_batch_allowed_in_ms": round(MIN_BATCH_INTERVAL_S * MILLISECONDS_PER_SECOND),
            **copy.deepcopy({key: summary[key] for key in answered}),
        }

    def _final_row(self, order: dict[str, Any], checked: _Checked) -> dict[str, Any]:
        """Carry one checked order out and give the row its verdict will be once it lands."""
        row = dict(checked.row)
        if row["status"] != "pending":
            return row
        refusal = checked.carry_out()
        if refusal is not None:
            return {**row, "status": "rejected", "reason": refusal, "commands": 0}
        self.recorded_orders.append(
            {"tick": self.tick, "batch": self._batches_accepted, "index": row["index"], "order": order}
        )
        if row["failed"]:
            return {**row, "status": "partial", "reason": row["failed"][0]["reason"]}
        return {**row, "status": "ok", "reason": "ok"}

    def _land_verdicts(self) -> None:
        """Make the batch in flight final: its rows in `last_orders`, its problems in `recent_order_problems`."""
        batch, self._in_flight = self._in_flight, None
        if batch is None:
            return
        rows = batch.final_rows
        batch.summary.update(pending=False, applied_at_tick=self.tick, results=rows, **_order_counts(rows))
        self._settled_problems.append(_problem_rows(batch.summary["batch"], rows))

    # -- orders: the checks ----------------------------------------------------------------------------

    def _check(self, index: int, order: Any) -> _Checked:
        """The immediate verdict on one order, and how to carry it out when it passed."""
        problem = _shape_problem(order)
        if problem is not None:
            return _rejected(index, order.get("cmd") if isinstance(order, dict) else None, "invalid", problem)
        if "units" in order:
            return self._check_unit_order(index, order)
        if "item" in order:
            return self._ITEM_CHECKS[order["cmd"]](self, index, order)
        return self._check_building_order(index, order)

    def _check_unit_order(self, index: int, order: dict[str, Any]) -> _Checked:
        cmd = order["cmd"]
        checks = (self._units_problem, self._cell_problem, self._target_problem, self._stance_problem)
        problem = next((found for check in checks if (found := check(order)) is not None), None)
        if problem is not None:
            return _rejected(index, cmd, *problem)
        takers, failed = self._takers(cmd, order["units"])
        if not takers:
            return _rejected(index, cmd, failed[0]["reason"], "no unit in this order can take it", failed)
        row = _row(index, cmd, "pending", "pending", commands=len(takers), failed=failed)
        return _Checked(row, lambda: self._carry_out_unit_order(order, takers))

    @staticmethod
    def _units_problem(order: dict[str, Any]) -> tuple[str, str] | None:
        units = order["units"]
        well_formed = (
            isinstance(units, list)
            and 1 <= len(units) <= MAX_UNITS_PER_ORDER
            and all(_is_whole_number(unit) for unit in units)
            and len(set(units)) == len(units)
        )
        return None if well_formed else ("invalid", f"units must be 1-{MAX_UNITS_PER_ORDER} distinct integer ids")

    @staticmethod
    def _cell_problem(order: dict[str, Any]) -> tuple[str, str] | None:
        if order["cmd"] == "attack" and ("target" in order) == ("to" in order):
            return "invalid", "attack takes exactly one of target and to"
        if "to" in order and not _is_cell_on_the_map(order["to"]):
            return "invalid", f"to must be a cell [x, y] on the map, got {order['to']!r}"
        return None

    def _target_problem(self, order: dict[str, Any]) -> tuple[str, str] | None:
        if "target" not in order:
            return None
        target = order["target"]
        if not _is_whole_number(target):
            return "invalid", "target must be an actor id"
        if order["cmd"] == "attack":
            return self._attack_target_problem(target)
        if target not in self._units and target not in self._buildings:
            return "not_owned", f"target {target} is not yours"
        if order["cmd"] == "enter_transport" and not self._is_transport(target):
            return "invalid", f"target {target} is not a transport"
        return None

    def _is_transport(self, actor_id: int) -> bool:
        """Whether an actor is one of the player's units that carries others."""
        unit = self._units.get(actor_id)
        return unit is not None and TRANSPORT_ROLE in self.items[unit.type].roles

    def _attack_target_problem(self, target: int) -> tuple[str, str] | None:
        enemy = self._enemies.get(target)
        if enemy is None or not self._is_seen(enemy, self._watched_squares()):
            return "target_not_visible", f"enemy {target} is not visible"
        if enemy.is_wreck:
            return "wreck", f"{target} is a wreck"
        return None

    @staticmethod
    def _stance_problem(order: dict[str, Any]) -> tuple[str, str] | None:
        if "stance" in order and order["stance"] not in STANCES:
            return "invalid", f"stance must be one of {', '.join(STANCES)}"
        return None

    def _takers(self, cmd: str, ids: list[int]) -> tuple[list[_Actor], list[dict[str, Any]]]:
        """The actors of an order that can take it, and the ids that cannot with the reason for each."""
        takers, failed = [], []
        for actor_id in ids:
            reason = self._refusal_of(cmd, actor_id)
            if reason is None:
                takers.append(self._units.get(actor_id) or self._buildings[actor_id])
            else:
                failed.append({"unit": actor_id, "reason": reason})
        return takers, failed

    def _refusal_of(self, cmd: str, actor_id: int) -> str | None:
        if actor_id in self._buildings:
            return None if cmd in BUILDING_TAKES else "is_building"
        if actor_id not in self._units:
            return "not_owned"
        if cmd == "deploy" and self._units[actor_id].type != MCV:
            return "invalid"
        if cmd == "enter_transport" and self._is_transport(actor_id):
            return "invalid"
        return None

    def _check_production_order(self, index: int, order: dict[str, Any]) -> _Checked:
        cmd, item = order["cmd"], str(order["item"]).lower()
        count = order.get("count", 1)
        if not _is_whole_number(count) or not 1 <= count <= MAX_TRAIN_COUNT:
            return _rejected(index, cmd, "invalid", f"count must be 1-{MAX_TRAIN_COUNT}")
        if item not in self._available_production():
            return _rejected(index, cmd, "not_available", f"{item} is not in available_production")
        return _Checked(_row(index, cmd, "pending", "pending", commands=count), lambda: self._enqueue(item, count))

    def _check_place(self, index: int, order: dict[str, Any]) -> _Checked:
        item = str(order["item"]).lower()
        if "at" in order and not _is_cell_on_the_map(order["at"]):
            return _rejected(index, "place", "invalid", f"at must be a cell [x, y] on the map, got {order['at']!r}")
        if self._ready_job(item) is None:
            return _rejected(index, "place", "not_ready", f"{item} is not finished")
        return _Checked(
            _row(index, "place", "pending", "pending", commands=1), lambda: self._place(item, order.get("at"))
        )

    def _check_cancel(self, index: int, order: dict[str, Any]) -> _Checked:
        item = str(order["item"]).lower()
        if not any(job.item == item for jobs in self._queues.values() for job in jobs):
            return _rejected(index, "cancel", "not_available", f"nothing to cancel: {item} is not queued")
        return _Checked(_row(index, "cancel", "pending", "pending", commands=1), lambda: self._cancel(item))

    _ITEM_CHECKS = {
        "build": _check_production_order,
        "train": _check_production_order,
        "place": _check_place,
        "cancel": _check_cancel,
    }

    def _check_building_order(self, index: int, order: dict[str, Any]) -> _Checked:
        cmd, building = order["cmd"], self._buildings.get(order["building"])
        if building is None:
            return _rejected(index, cmd, "not_owned", f"building {order['building']!r} is not yours")
        problem = self._building_order_problem(order, building)
        if problem is not None:
            return _rejected(index, cmd, "invalid", problem)
        unchanged = self._unchanged_detail(order, building)
        if unchanged is not None:
            return _Checked(_row(index, cmd, "ok", "ok", unchanged))
        row = _row(index, cmd, "pending", "pending", commands=1)
        return _Checked(row, lambda: self._carry_out_building_order(order, building))

    @staticmethod
    def _building_order_problem(order: dict[str, Any], building: _Actor) -> str | None:
        if "on" in order and not isinstance(order["on"], bool):
            return "on must be true or false"
        if order["cmd"] in PRODUCER_ORDERS and building.type not in REPORTED_QUEUE:
            return f"building {building.id} does not produce"
        if "to" in order and not _is_cell_on_the_map(order["to"]):
            return f"to must be a cell [x, y] on the map, got {order['to']!r}"
        return None

    @staticmethod
    def _unchanged_detail(order: dict[str, Any], building: _Actor) -> str | None:
        """The detail of a `repair` or `power_toggle` that changes nothing, which the platform answers with `ok`."""
        if order["cmd"] == "repair" and order.get("on", True) == building.repairing:
            return "already_repairing" if building.repairing else "not_repairing"
        if order["cmd"] == "power_toggle" and order.get("on") == (not building.powered_down):
            return "already_off" if building.powered_down else "already_on"
        return None

    # -- orders: the effects ---------------------------------------------------------------------------

    def _carry_out_unit_order(self, order: dict[str, Any], takers: list[_Actor]) -> str | None:
        """Do what a unit order asks of each actor that takes it; the reason the game refuses it, when it does."""
        effect = self._UNIT_EFFECTS.get(order["cmd"])
        if effect is None:
            return None
        refusals = [
            effect(self, actor, order)
            for actor in takers
            # An earlier order of the same batch may have used an actor up (an MCV deployed twice).
            if actor.id in self._units or actor.id in self._buildings
        ]
        return next((refusal for refusal in refusals if refusal is not None), None)

    @staticmethod
    def _task(
        actor: _Actor, activity: str | None, *, to: Any = None, chasing: int | None = None, boarding: int | None = None
    ) -> None:
        """Give an actor one thing to do in place of whatever it was doing."""
        actor.activity = activity
        actor.destination = None if to is None else (to[0], to[1])
        actor.chasing = chasing
        actor.boarding = boarding

    def _send(self, actor: _Actor, order: dict[str, Any]) -> None:
        self._task(actor, order["cmd"], to=order["to"])

    def _attack(self, actor: _Actor, order: dict[str, Any]) -> None:
        if "target" in order:
            self._task(actor, "attack", chasing=order["target"])
        else:
            self._send(actor, order)

    def _halt(self, actor: _Actor, _order: dict[str, Any]) -> None:
        self._task(actor, None)

    def _deploy(self, actor: _Actor, _order: dict[str, Any]) -> str | None:
        """Turn an MCV into a construction yard where it stands (see `unfold_as_the_engine`); refused while a scenario
        says there is no room, and left without effect, though answered, where a building stands in the way."""
        if self._refused_deploys:
            self._refused_deploys -= 1
            return NO_ROOM
        corner = self._yard_corner(actor.cell)
        if self._unfolds_as_the_engine and self._is_yard_blocked(corner, actor.id):
            return None
        del self._units[actor.id]
        self._add_building(CONSTRUCTION_YARD, corner)
        return None

    def _yard_corner(self, cell: tuple[int, int]) -> tuple[int, int]:
        """The top-left cell of the yard an MCV on a cell unfolds: the cell itself, or as the engine unfolds it."""
        offset_x, offset_y = YARD_UNFOLD_OFFSET if self._unfolds_as_the_engine else (0, 0)
        return cell[0] + offset_x, cell[1] + offset_y

    def _is_yard_blocked(self, corner: tuple[int, int], mcv_id: int) -> bool:
        """Whether the cell of a standing building lies in the yard's box from a top-left cell; with
        `units_block_unfold`, or the cell of a unit other than the MCV that unfolds."""
        (left, top), (width, height) = corner, YARD_FOOTPRINT
        standing = [building.cell for building in self._buildings.values()]
        if self._units_block_unfold:
            standing += [unit.cell for unit in self._units.values() if unit.id != mcv_id]
        return any(left <= x < left + width and top <= y < top + height for x, y in standing)

    def _harvest(self, actor: _Actor, order: dict[str, Any]) -> None:
        self._task(actor, HARVEST, to=order.get("to"))

    def _set_stance(self, actor: _Actor, order: dict[str, Any]) -> None:
        actor.stance = order["stance"]

    def _enter_transport(self, actor: _Actor, order: dict[str, Any]) -> None:
        self._task(actor, "enter_transport", boarding=order["target"])

    def _unload(self, actor: _Actor, _order: dict[str, Any]) -> None:
        """Put a transport's cargo on the free cells next to it, the nearest first, each unit under its own id.

        The transport stops to unload. A unit for which no cell is free stays aboard; a unit that carries nothing
        is left as it was.
        """
        aboard = self._cargo.pop(actor.id, [])
        if not aboard:
            return
        self._task(actor, None)
        taken = {other.cell for other in (*self._units.values(), *self._buildings.values())}
        x, y = actor.cell
        next_to = ((x + dx, y + dy) for dx, dy in UNLOAD_OFFSETS)
        free = [cell for cell in next_to if cell not in taken and _is_cell_on_the_map(list(cell))]
        for unit, cell in zip(aboard, free):
            unit.x, unit.y = float(cell[0]), float(cell[1])
            self._units[unit.id] = unit
        if len(aboard) > len(free):
            self._cargo[actor.id] = aboard[len(free) :]

    # Orders without an entry (guard) are accepted and recorded, and change nothing.
    _UNIT_EFFECTS = {
        "move": _send,
        "attack_move": _send,
        "attack": _attack,
        "stop": _halt,
        "deploy": _deploy,
        "harvest": _harvest,
        "set_stance": _set_stance,
        "enter_transport": _enter_transport,
        "unload": _unload,
    }

    def _enqueue(self, item: str, count: int) -> str | None:
        facts = self.items[item]
        if facts.queue in self._full_queues:
            self._full_queues.remove(facts.queue)
            return "not_ready"
        total = round(facts.build_time_s * TICKS_PER_SECOND) * WORK_PER_TICK
        self._queues[facts.queue] += [_Job(item, facts.cost, total) for _ in range(count)]
        return None

    def _ready_jobs(self) -> list[_Job]:
        """The finished buildings and defenses that wait to be placed."""
        return [job for queue in PLACED_QUEUES for job in self._queues[queue] if job.ready]

    def _ready_job(self, item: str) -> _Job | None:
        return next((job for job in self._ready_jobs() if job.item == item), None)

    def _place(self, item: str, wanted: list[int] | None) -> str | None:
        """Put a finished building on the map: at the wanted cell when that is usable, else near the yard."""
        job = self._ready_job(item)
        if job is None:
            # An earlier order of the same batch placed the one that was ready.
            return "not_ready"
        refusals_left = self._refused_places.get(item, 0)
        cell = None if refusals_left else self._cell_for_building(wanted)
        if cell is None:
            self._refused_places[item] = max(0, refusals_left - 1)
            return "invalid"
        self._queues[self.items[item].queue].remove(job)
        building = self._add_building(item, cell)
        if item == REFINERY:
            self._spawn_next_to(building, HARVESTER)
        return None

    def _cell_for_building(self, wanted: list[int] | None) -> tuple[int, int] | None:
        """The wanted cell when it is free and within reach of the base, else the free cell nearest the base."""
        anchor = self._base_center()
        if anchor is None:
            return None
        base = (anchor[0], anchor[1])
        taken = self._cells_kept_clear()
        if wanted is not None:
            cell = (wanted[0], wanted[1])
            if cell not in taken and _within(cell, base, PLACE_RADIUS_CELLS):
                return cell
        nearby = ((base[0] + dx, base[1] + dy) for dx, dy in PLACE_OFFSETS)
        return next((cell for cell in nearby if cell not in taken and _is_cell_on_the_map(list(cell))), None)

    def _cells_kept_clear(self) -> set[tuple[int, int]]:
        """Every cell a new building may not take: the cells of the buildings that stand, and those next to them."""
        cells = (building.cell for building in self._buildings.values())
        return {(x + dx, y + dy) for x, y in cells for dx, dy in FOOTPRINT_OFFSETS}

    def _cancel(self, item: str) -> str | None:
        """Take the newest queued item of a type out of its queue and give back what was paid for it."""
        jobs = self._queues[self.items[item].queue]
        job = next((job for job in reversed(jobs) if job.item == item), None)
        if job is None:
            # An earlier order of the same batch cancelled the last one.
            return "not_available"
        jobs.remove(job)
        self.cash += job.paid
        return None

    def _carry_out_building_order(self, order: dict[str, Any], building: _Actor) -> None:
        cmd = order["cmd"]
        if cmd == "repair":
            building.repairing = order.get("on", True)
        elif cmd == "power_toggle":
            building.powered_down = not order.get("on", building.powered_down)
        elif cmd == "rally":
            building.rally = list(order["to"])

    # -- a whole match -----------------------------------------------------------------------------------

    def context(self) -> DecisionContext:
        """The context the runtime would hand a decision-maker for this match, the game reference included."""
        return DecisionContext(
            session_id=SESSION_ID,
            tournament_id=None,
            game_type="red_alert",
            agent_id=AGENT_ID,
            seat_position=0,
            game_config=self.game_config,
        )

    def result(self) -> dict[str, Any]:
        """What `get_result` gives now: the result the scenario ended the match with, else that it still runs."""
        if self._result is not None:
            return self._result
        return {
            "session_id": SESSION_ID,
            "is_terminal": False,
            "status": "in_progress",
            "returns": None,
            "your_return": None,
            "winner_agent_id": None,
            "termination_reason": None,
            "failure_reason": None,
        }

    def run(
        self, agent: Any, steps: int, context: DecisionContext | None = None, *, keep_states: bool = False
    ) -> FakeRun:
        """Play a decision-maker through up to `steps` states of the match, the way the runtime would.

        For each state the agent's `choose_action` gets the state wrapped as
        the runtime wraps it (`.raw`, `.legal_actions`) and the context; a
        batch it returns is answered and the answer handed to its
        `on_action_result`, when it has one; then the game steps. The run
        stops early when the scenario ends the match. It finishes as the
        runtime does, by calling `altruagent.runner._terminal_game_state` with
        the last state and the result, and never calls the agent to say that
        the match is over. `context` defaults to `self.context()`.
        """
        context = context or self.context()
        played = FakeRun()
        refused_before = self.invalid_actions
        for _ in range(steps):
            raw_state = self.state()
            if raw_state["is_terminal"]:
                break
            if keep_states:
                played.states.append(raw_state)
            self._ask(agent, GameState.from_mcp_state(raw_state), context, played)
            self.step()
        last_state = self.state()
        if keep_states:
            played.states.append(last_state)
        played.invalid_actions = self.invalid_actions - refused_before
        # Looked up on the module at this moment: the agent's result hook replaces this attribute in memory.
        played.final_state = runner._terminal_game_state(GameState.from_mcp_state(last_state), self.result())
        return played

    def _ask(self, agent: Any, state: GameState, context: DecisionContext, played: FakeRun) -> None:
        """One request for a move: send what the agent returns and report the answer to it."""
        decision = agent.choose_action(state, context)
        if isinstance(decision, WithReasoning):
            decision = decision.action
        if decision is WAIT:
            played.waits += 1
            return
        answer = self.play_action(decision)
        played.batches.append(decision)
        played.answers.append(answer)
        report = getattr(agent, "on_action_result", None)
        if report is not None:
            report(answer, context)

    # -- what a scenario does ----------------------------------------------------------------------------

    def place_enemy(self, actor_type: str, x: int, y: int, *, hp: int = FULL_HP, visible: bool | None = None) -> int:
        """Put an enemy unit or building on a cell and return its id.

        It is a building when the item table says the type is one. `visible`
        forces it visible or hidden; left out, the player sees it exactly when
        an own unit or building is within 8 cells of it.
        """
        facts = self.items.get(actor_type)
        enemy = _Enemy(self._take_id(), actor_type.lower(), x, y, bool(facts and facts.is_building), hp, visible)
        self._enemies[enemy.id] = enemy
        return enemy.id

    def move_enemy(self, enemy_id: int, x: int, y: int) -> None:
        """Put an enemy actor on another cell."""
        self._enemies[enemy_id].x, self._enemies[enemy_id].y = x, y

    def damage_enemy(self, enemy_id: int, hp: int) -> None:
        """Set the hp, in percent, that an enemy actor has left."""
        self._enemies[enemy_id].hp = hp

    def remove_enemy(self, enemy_id: int) -> None:
        """Take an enemy actor off the map."""
        del self._enemies[enemy_id]

    def show_enemy(self, enemy_id: int, visible: bool | None) -> None:
        """Force an enemy actor visible (True) or hidden (False), or hand it back to the sight rule (None)."""
        self._enemies[enemy_id].shown = visible

    def damage_own(self, actor_id: int, hp: int) -> None:
        """Set the hp, in percent, that one of the player's units or buildings has left."""
        (self._units.get(actor_id) or self._buildings[actor_id]).hp = hp

    def kill_own(self, actor_id: int) -> None:
        """Destroy one of the player's units or buildings: it is gone, its cost is lost and the loss is reported.

        The units aboard a transport die with it: their cost is lost and they are counted, but no event names them,
        as they were reported lost when they boarded.
        """
        is_building = actor_id in self._buildings
        actor = (self._buildings if is_building else self._units).pop(actor_id)
        self.deaths_cost += self.items[actor.type].cost
        self._losses["buildings_lost" if is_building else "units_lost"] += 1
        for passenger in self._cargo.pop(actor_id, []):
            self.deaths_cost += self.items[passenger.type].cost
            self._losses["units_lost"] += 1
        x, y = actor.cell
        self.emit_event("building_lost" if is_building else "unit_lost", id=actor.id, type=actor.type, x=x, y=y)

    def idle_own(self, actor_id: int) -> None:
        """Leave one of the player's units with nothing to do, as a harvester stands when it finds no ore left."""
        self._units[actor_id].activity = None

    def remove_own(self, actor_id: int) -> None:
        """Take one of the player's units or buildings off the map without a loss: no cost, no event, no count.

        The units aboard a transport go with it.
        """
        self._cargo.pop(actor_id, None)
        if self._units.pop(actor_id, None) is None:
            del self._buildings[actor_id]

    def add_own(self, unit_type: str, x: int, y: int) -> int:
        """Put a new unit of the player's on a cell, as if it had just been made there, and return its id."""
        unit = self._add_unit(unit_type.lower(), (x, y))
        self.emit_event("unit_created", id=unit.id, type=unit.type, x=x, y=y)
        return unit.id

    def aboard(self, transport_id: int) -> list[int]:
        """The ids of the player's units aboard a transport, in the order they boarded; none for one that is empty."""
        return [unit.id for unit in self._cargo.get(transport_id, ())]

    def emit_under_attack(self, actor_id: int) -> None:
        """Report that one of the player's units or buildings is under attack."""
        actor = self._units.get(actor_id) or self._buildings[actor_id]
        x, y = actor.cell
        self.emit_event("under_attack", id=actor.id, type=actor.type, x=x, y=y, hp=actor.hp)

    def add_kills(self, cost: int) -> None:
        """Add to the value of what the player has destroyed (`kills_cost`)."""
        self.kills_cost += cost

    def lose_batch(self, number: int | None = None) -> None:
        """Have the platform lose a batch: the one with this batch number, or the next one sent.

        The batch is answered as usual, none of its orders is carried out, and
        its pending orders get the verdict `lost` in the next state.
        """
        if number is None:
            self._lose_next_batch = True
        else:
            self._lost_batches.add(number)

    def refuse_production(self, queue: str) -> None:
        """Have the game answer the next `build` or `train` for a queue with `not_ready`, as when the queue is full."""
        self._full_queues.append(queue)

    def refuse_place(self, item: str, times: int = 1) -> None:
        """Have the game answer the next `times` placements of an item with `invalid`, as when no cell is free."""
        self._refused_places[item.lower()] = self._refused_places.get(item.lower(), 0) + times

    def unfold_as_the_engine(self) -> None:
        """From now on have an MCV unfold its yard as the engine does: with the yard's top-left corner one cell up and
        one left of the MCV's cell, rather than on it; and, as the engine did in a watched game, leave a deploy
        without effect, though the platform answers it, while a building's cell lies in the yard's box."""
        self._unfolds_as_the_engine = True

    def report_as_the_engine(self) -> None:
        """From now on report every building, in the state and in its `building_created` event, at the middle cell of
        its footprint, one cell right and one down of the cell it was put on for a building two cells or more across,
        as the engine does, rather than at that cell."""
        self._reports_as_the_engine = True

    def units_block_unfold(self) -> None:
        """From now on, while MCVs unfold as the engine does, leave a deploy without effect while a unit other than the
        MCV stands in the yard's box, as the engine unfolds no yard over a unit."""
        self._units_block_unfold = True

    def add_own_building(self, building_type: str, x: int, y: int) -> int:
        """Put a building of the player's on a cell, as if it had just been placed there, and return its id."""
        return self._add_building(building_type.lower(), (x, y)).id

    def queue_item(self, item: str) -> None:
        """Put one of an item in its production queue, as if the player had ordered it: it is made and paid as any."""
        self._enqueue(item.lower(), 1)

    def refuse_deploy(self, times: int = 1) -> None:
        """Have the game answer the next `times` deploys with `invalid`, as when an MCV has no room to unfold."""
        self._refused_deploys += times

    def remove_available(self, item: str) -> None:
        """Take an item out of `available_production` for the rest of the match."""
        self._withheld.add(item.lower())

    def skip_event_seq(self, count: int) -> None:
        """Leave out `count` event numbers, as when events were pushed out of the window between two reads."""
        self._event_seq += count

    def end_match(self, result: dict[str, Any]) -> None:
        """End the match with a result object (see `result_object`); every later state is terminal."""
        self._result = result

    def on_step(self, callback: Callable[[FakeRedAlert], None]) -> None:
        """Register a function that is handed the game in every step, after the units have moved."""
        self._callbacks.append(callback)

    def own_units(self) -> list[dict[str, Any]]:
        """The player's units as the current observation lists them, for a scenario to read."""
        return [self._unit_view(unit) for unit in self._units.values()]

    def own_buildings(self) -> list[dict[str, Any]]:
        """The player's buildings as the current observation lists them, for a scenario to read."""
        provided, drained = self._power()
        return [self._building_view(building, provided, drained) for building in self._buildings.values()]

    def enemies(self) -> list[dict[str, Any]]:
        """Every enemy actor on the map, seen or not, with `building` and `visible` added to its view."""
        seen = {enemy.id for enemy in self._seen_enemies()}
        return [enemy.view(visible=enemy.id in seen) for enemy in self._enemies.values()]
