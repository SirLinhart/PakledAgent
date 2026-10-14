"""The compact shorthand a model reads and writes for a Red Alert decision.

A model asked to choose for the agent is told about the match in a few short
lines, not in the state the platform sends: the state runs to tens of
thousands of characters, and a small model reads a short text faster and
better. `summarize` writes that text. It is at most 900 characters of plain
ASCII, one `key=value` group per line, always in the same order, every figure
a whole number:

    t=<game seconds>/<seconds left>
    me=<own family> en=<enemy family>
    cash=<money> pw=<power left over> harv=<harvesters>
    bal=<trade balance> army=<army value> assets=<assets value>
    grp guard=<units>/<strength> main=<units>/<strength> raid=<units>/<strength>
    bld <type>x<count> ...                      the own buildings, by type
    en seen=<0|1> def=<defenses> army=<combat value> harv=<harvesters> veh=<0|1> age=<seconds>
    threat=<none|minor|major>
    plan=<plan in force>

Money is what the game can spend, the cash and the ore together. In the
`en` line, `seen` says whether the enemy base has been found, `veh` whether
the enemy fields vehicles, and `age` how long ago an enemy building was last
in sight (-1 when none has been). When the text would be longer than 900
characters, the `bld` line is cut first, so the plan in force always ends it.

What comes back is command shorthand, one command a line, which `translate`
turns into the intents the script's own managers would propose:

    plan <HOLD|RAID|PRESS>     ask the plan selector for a discretionary plan
    am <group> <place>         attack-move the group to the place
    mv <group> <place>         move the group to the place
    atk <group> <class>        attack the nearest enemy in sight of the class
    ret <group>                move the group to the staging point
    hold <group>               stop the group, stance `defend`
    tr <item> <n>              train n (1 to 10) of the item
    bd <item>                  build the item

A group is `guard`, `main`, `raid` or `all` (every unit that fights but the
home guard). A place is `home` (the base's center), `stage` (the staging
point), `ebase` (the enemy's start), `eharv`, `eproc`, `epow` (the nearest
remembered enemy harvester, refinery, power plant) or `x,y`. A class is
`harv`, `inf`, `veh`, `def` or `bld`. Tokens are separated by single spaces
and case does not matter.

The translator, never the model, answers for what is sent. A line it cannot
turn into an order the script could send itself (an unknown word, an item
not on offer, a cell off the map, a place or target that is not known, more
than 8 lines, a line longer than 40 characters) gives no order at all: it is
rejected with its reason and written to the match record as a `decision`
whose outcome is `invalid`. Nothing is guessed. A group of more than 50
units is ordered in several orders of at most 50. Lines a model wrote are
taken only when the settings allow it (`model_commands`).

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from ucla_agent.core import records
from ucla_agent.core.decide import INVALID
from ucla_agent.games.redalert import targeting
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.composition import HOLD, PRESS, RAID
from ucla_agent.games.redalert.family import ADVANCED_POWER_PLANT, HARVESTER, POWER_PLANT, REFINERY
from ucla_agent.games.redalert.fight import Point, centroid, strength
from ucla_agent.games.redalert.groups import GUARD, MAIN, Groups
from ucla_agent.games.redalert.groups import RAID as RAID_GROUP
from ucla_agent.games.redalert.items import FACTION_FAMILIES, TICKS_PER_SECOND
from ucla_agent.games.redalert.production import BUILDING_QUEUE, DEFENSE_QUEUE, INFANTRY_QUEUE, VEHICLE_QUEUE
from ucla_agent.games.redalert.scout_report import ScoutReport, is_combat_unit
from ucla_agent.games.redalert.world import DEFENSE_TYPES, Cell, EnemyRecord, Unit, World

MAX_SUMMARY_CHARS = 900
# The groups the summary reports, in its order.
SUMMARY_GROUPS = (GUARD, MAIN, RAID_GROUP)
# The family of a faction the state does not name, and the age of what was never seen.
UNKNOWN = "unknown"
NEVER = -1
_BUILDINGS_KEY = "bld"
_ASCII = "ascii"

MAX_LINES = 8
MAX_LINE_CHARS = 40
MAX_UNITS_PER_ORDER = 50
MAX_TRAIN_COUNT = 10
# The group that names every unit that fights but the home guard.
ALL = "all"
# The `source` of the decision event a rejected line is written to the record as.
SOURCE = "shorthand"
# A whole number as shorthand writes one: ASCII digits only, a minus sign for a cell's coordinate.
_COUNT = re.compile(r"[0-9]+")
_COORDINATE = re.compile(r"-?[0-9]+")
DISCRETIONARY_PLANS = (HOLD, RAID, PRESS)
_DEFEND = "defend"

# Why a line gave no order.
TOO_MANY_LINES = "too_many_lines"
TOO_LONG = "too_long"
MALFORMED = "malformed"
UNKNOWN_COMMAND = "unknown_command"
UNKNOWN_PLAN = "unknown_plan"
PLAN_NOT_OPEN = "plan_not_open"
UNKNOWN_GROUP = "unknown_group"
EMPTY_GROUP = "empty_group"
UNKNOWN_PLACE = "unknown_place"
NO_PLACE = "no_place"
OUT_OF_BOUNDS = "out_of_bounds"
UNKNOWN_CLASS = "unknown_class"
NO_TARGET = "no_target"
NOT_AVAILABLE = "not_available"
BAD_COUNT = "bad_count"
NOT_A_UNIT = "not_a_unit"
NOT_A_BUILDING = "not_a_building"
# A train line for infantry while the opening holds infantry back but its scout rifleman (`single_scout_rifle`).
INFANTRY_HELD = "infantry_held"
MODEL_COMMANDS_OFF = "model_commands_off"


def summarize(world: World, scout_report: ScoutReport, plan: str, groups: Groups, threat: str) -> str:
    """The compact state summary of a match as one cycle sees it. See the module's note for its lines."""
    economy, military = world.economy, world.military
    head = [
        f"t={int(world.game_seconds)}/{int(world.remaining_seconds or 0)}",
        f"me={world.family} en={FACTION_FAMILIES.get(world.enemy_faction, UNKNOWN)}",
        f"cash={economy.spendable} pw={economy.power_provided - economy.power_drained} harv={economy.harvesters}",
        f"bal={world.trade_balance} army={military.army_value} assets={military.assets_value}",
        "grp " + " ".join(_group(name, world, groups) for name in SUMMARY_GROUPS),
    ]
    tail = [_enemy(world, scout_report), f"threat={threat}", f"plan={plan}"]
    room = MAX_SUMMARY_CHARS - len("\n".join([*head, *tail])) - len("\n")
    return _ascii("\n".join([*head, _buildings(world, room), *tail]))


def _group(name: str, world: World, groups: Groups) -> str:
    """One group as `name=<units>/<strength>`."""
    units = [world.combat_units[unit_id] for unit_id in groups.members(name)]
    return f"{name}={len(units)}/{round(strength(units, world))}"


def _buildings(world: World, room: int) -> str:
    """The `bld` line, its types in name order, cut to the characters there is room for at a whole entry."""
    counts = Counter(building.type for building in world.buildings.values())
    line = _BUILDINGS_KEY
    for building_type, count in sorted(counts.items()):
        entry = f" {building_type}x{count}"
        if len(line) + len(entry) > room:
            break
        line += entry
    return line


def _enemy(world: World, report: ScoutReport) -> str:
    """The `en` line: what is known of the enemy."""
    seen = report.last_building_seen_tick
    age = NEVER if seen is None else (world.tick - seen) // TICKS_PER_SECOND
    return (
        f"en seen={int(report.enemy_base_seen)} def={report.enemy_defense_count} army={report.enemy_combat_value} "
        f"harv={len(report.enemy_harvester_cells)} veh={int(report.enemy_has_vehicles)} age={age}"
    )


def _ascii(text: str) -> str:
    """The text in plain ASCII: a character outside it, which only a name the platform sends could hold, as `?`."""
    return text.encode(_ASCII, "replace").decode(_ASCII)


# -- command shorthand -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Rejected:
    """A line of command shorthand that gave no order, and why."""

    line: str
    reason: str


@dataclass(frozen=True)
class Commands:
    """What command shorthand is turned into orders with, besides the world and the groups."""

    # The cell `stage` names; None while it is not known.
    staging: Cell | None
    # Hands the plan a `plan` line names to the plan selector; False when the selector does not take it.
    request_plan: Callable[[str], bool]
    # Whether lines a model wrote are taken at all.
    model_commands: bool = False
    # Whether the opening holds infantry back but its scout rifleman (`MatchContext.scout_rifle_hold`): a train line
    # for infantry is refused meanwhile.
    infantry_held: bool = False


class _Refused(Exception):
    """A line cannot be turned into an order; `reason` says why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def translate(
    text: str, world: World, groups: Groups, commands: Commands, *, from_model: bool = False
) -> tuple[list[Intent], list[Rejected]]:
    """The intents command shorthand asks for, and the lines rejected with their reasons. See the module's note.

    Each rejected line is written to the match record as a `decision` with outcome `invalid`.
    """
    intents: list[Intent] = []
    rejected: list[Rejected] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for number, line in enumerate(lines, start=1):
        try:
            if from_model and not commands.model_commands:
                raise _Refused(MODEL_COMMANDS_OFF)
            if number > MAX_LINES:
                raise _Refused(TOO_MANY_LINES)
            intents += _line(line, world, groups, commands)
        except _Refused as refusal:
            rejected.append(Rejected(line, refusal.reason))
    for refusal in rejected:
        _log_rejected(refusal, world)
    return intents, rejected


def _line(line: str, world: World, groups: Groups, commands: Commands) -> list[Intent]:
    """The intents of one line of shorthand; raises `_Refused` for a line that gives none."""
    if len(line) > MAX_LINE_CHARS:
        raise _Refused(TOO_LONG)
    tokens = line.lower().split(" ")
    if "" in tokens:
        raise _Refused(MALFORMED)
    command, arguments = tokens[0], tokens[1:]
    handler, count = _COMMANDS.get(command, (None, 0))
    if handler is None:
        raise _Refused(UNKNOWN_COMMAND)
    if len(arguments) != count:
        raise _Refused(MALFORMED)
    return handler(arguments, world, groups, commands)


def _plan(arguments: list[str], _world: World, _groups: Groups, commands: Commands) -> list[Intent]:
    plan = arguments[0].upper()
    if plan not in DISCRETIONARY_PLANS:
        raise _Refused(UNKNOWN_PLAN)
    if not commands.request_plan(plan):
        raise _Refused(PLAN_NOT_OPEN)
    return []


def _attack_move(arguments: list[str], world: World, groups: Groups, commands: Commands) -> list[Intent]:
    units = _units_of(arguments[0], world, groups)
    return _unit_orders("attack_move", units, {"to": list(_place(arguments[1], units, world, commands))})


def _move(arguments: list[str], world: World, groups: Groups, commands: Commands) -> list[Intent]:
    units = _units_of(arguments[0], world, groups)
    return _unit_orders("move", units, {"to": list(_place(arguments[1], units, world, commands))})


def _attack(arguments: list[str], world: World, groups: Groups, _commands: Commands) -> list[Intent]:
    units = _units_of(arguments[0], world, groups)
    accepts = _CLASSES.get(arguments[1])
    if accepts is None:
        raise _Refused(UNKNOWN_CLASS)
    in_sight = [record for record in world.enemy_memory.values() if record.visible and accepts(record, world)]
    if not in_sight:
        raise _Refused(NO_TARGET)
    target = _nearest(in_sight, centroid(units))
    every_enemy_in_sight = [record for record in world.enemy_memory.values() if record.visible]
    priority = targeting.attack_class(units, every_enemy_in_sight, world)
    return _unit_orders("attack", units, {"target": target.id}, priority)


def _retreat(arguments: list[str], world: World, groups: Groups, commands: Commands) -> list[Intent]:
    units = _units_of(arguments[0], world, groups)
    return _unit_orders("move", units, {"to": list(_place("stage", units, world, commands))})


def _hold(arguments: list[str], world: World, groups: Groups, _commands: Commands) -> list[Intent]:
    units = _units_of(arguments[0], world, groups)
    return [*_unit_orders("stop", units, {}), *_unit_orders("set_stance", units, {"stance": _DEFEND})]


def _train(arguments: list[str], world: World, _groups: Groups, commands: Commands) -> list[Intent]:
    item, count = arguments[0], arguments[1]
    _check_offered(item, world, (INFANTRY_QUEUE, VEHICLE_QUEUE), NOT_A_UNIT)
    if commands.infantry_held and world.items[item].queue == INFANTRY_QUEUE:
        raise _Refused(INFANTRY_HELD)
    if not _COUNT.fullmatch(count) or not 1 <= int(count) <= MAX_TRAIN_COUNT:
        raise _Refused(BAD_COUNT)
    return [Intent(Priority.PRODUCTION, {"cmd": "train", "item": item, "count": int(count)})]


def _build(arguments: list[str], world: World, _groups: Groups, _commands: Commands) -> list[Intent]:
    item = arguments[0]
    _check_offered(item, world, (BUILDING_QUEUE, DEFENSE_QUEUE), NOT_A_BUILDING)
    return [Intent(Priority.PRODUCTION, {"cmd": "build", "item": item})]


def _check_offered(item: str, world: World, queues: tuple[str, ...], wrong_kind: str) -> None:
    """Raise `_Refused` unless the item is on offer and made in one of the queues given."""
    if item not in world.available_production:
        raise _Refused(NOT_AVAILABLE)
    facts = world.items.get(item)
    if facts is None or facts.queue not in queues:
        raise _Refused(wrong_kind)


# command -> (the handler that turns its arguments into intents, how many arguments it takes)
_COMMANDS: dict[str, tuple[Callable[[list[str], World, Groups, Commands], list[Intent]], int]] = {
    "plan": (_plan, 1),
    "am": (_attack_move, 2),
    "mv": (_move, 2),
    "atk": (_attack, 2),
    "ret": (_retreat, 1),
    "hold": (_hold, 1),
    "tr": (_train, 2),
    "bd": (_build, 1),
}


def _units_of(group: str, world: World, groups: Groups) -> list[Unit]:
    """The units of a named group; `all` is every unit that fights but the home guard."""
    if group == ALL:
        unit_ids = [unit_id for unit_id in world.combat_units if groups.group_of(unit_id) != GUARD]
    elif group in (GUARD, MAIN, RAID_GROUP):
        unit_ids = groups.members(group)
    else:
        raise _Refused(UNKNOWN_GROUP)
    if not unit_ids:
        raise _Refused(EMPTY_GROUP)
    return [world.combat_units[unit_id] for unit_id in unit_ids]


def _unit_orders(cmd: str, units: list[Unit], fields: dict, priority: Priority = Priority.MOVE) -> list[Intent]:
    """One combat intent of a command for every 50 units, in the order the group lists them.

    Every command but an attack moves or stops the group, and is a MOVE order; an attack is in its class by contact.
    """
    unit_ids = [unit.id for unit in units]
    return [
        Intent(priority, {"cmd": cmd, "units": unit_ids[start : start + MAX_UNITS_PER_ORDER], **fields})
        for start in range(0, len(unit_ids), MAX_UNITS_PER_ORDER)
    ]


def _place(token: str, units: list[Unit], world: World, commands: Commands) -> Cell:
    """The cell a place names; raises `_Refused` for a place that is unknown or cannot be found."""
    if token in _REMEMBERED:
        remembered = [record for record in world.enemy_memory.values() if _REMEMBERED[token](record, world)]
        if not remembered:
            raise _Refused(NO_PLACE)
        nearest = _nearest(remembered, centroid(units))
        return nearest.x, nearest.y
    named = {"home": world.base_center, "stage": commands.staging, "ebase": world.enemy_base}
    if token in named:
        if named[token] is None:
            raise _Refused(NO_PLACE)
        return named[token]
    return _cell(token, world)


def _cell(token: str, world: World) -> Cell:
    """The cell `x,y` names, which has to lie in the playable part of the map."""
    parts = token.split(",")
    if len(parts) != 2 or not all(_COORDINATE.fullmatch(part) for part in parts):
        raise _Refused(UNKNOWN_PLACE)
    x, y = int(parts[0]), int(parts[1])
    if not world.in_bounds(x, y):
        raise _Refused(OUT_OF_BOUNDS)
    return x, y


def _nearest(enemies: list[EnemyRecord], point: Point) -> EnemyRecord:
    return min(enemies, key=lambda record: (math.hypot(record.x - point[0], record.y - point[1]), record.id))


def _base_type(record: EnemyRecord, world: World) -> str:
    """The type a remembered enemy is, a variant counted as its base type."""
    facts = world.items.get(record.type)
    return record.type if facts is None else facts.name


def _queue(record: EnemyRecord, world: World) -> str | None:
    facts = world.items.get(record.type)
    return None if facts is None else facts.queue


# place -> which remembered enemies it may name; the nearest of them to the group is the place.
_REMEMBERED: dict[str, Callable[[EnemyRecord, World], bool]] = {
    "eharv": lambda record, world: not record.is_building and _base_type(record, world) == HARVESTER,
    "eproc": lambda record, world: record.is_building and _base_type(record, world) == REFINERY,
    "epow": lambda record, world: record.is_building
    and _base_type(record, world) in (POWER_PLANT, ADVANCED_POWER_PLANT),
}
# class -> which enemies in sight it names.
_CLASSES: dict[str, Callable[[EnemyRecord, World], bool]] = {
    "harv": _REMEMBERED["eharv"],
    "inf": lambda record, world: is_combat_unit(record) and _queue(record, world) == INFANTRY_QUEUE,
    "veh": lambda record, world: is_combat_unit(record) and _queue(record, world) == VEHICLE_QUEUE,
    "def": lambda record, world: record.is_building and record.type in DEFENSE_TYPES,
    "bld": lambda record, world: record.is_building,
}


def _log_rejected(refusal: Rejected, world: World) -> None:
    """Write a rejected line to the match record as a `decision` whose outcome is `invalid`."""
    records.log(
        "decision",
        {
            "state_version": str(world.tick),
            "source": SOURCE,
            "request_summary": refusal.line,
            "answer": None,
            "latency_ms": 0,
            "outcome": INVALID,
            "confidence": None,
            "reason": refusal.reason,
        },
    )
