"""The plan choice briefing: what a model choosing the plan reads about the match, in plain words.

A small model knows Red Alert by name but not its units, and the compact
summary of `shorthand` leaves it to guess what its codes and figures mean.
With `[redalert] model_briefing` on, the plan choice's context is this
briefing instead. It says in plain words what the plan rules weigh, in six
sections, always in this order:

1. Situation: the time played and left, the sides, the plan in force and the
   rule that set it, the threat to our base and where it is, the trade
   balance, the money, the income per minute and the power; then the
   model's recent plan picks, each with what came of it since (`recent
   picks:`, see `model_history`).
2. Our forces: our army by type, each line `count × name (code): value,
   health`, the groups with their units and strength, our other units and our
   buildings by type.
3. Enemy as known: the enemy army seen by type, with how long ago each type
   was last seen; its other units, buildings and defenses by type, each
   defense near their base or forward; whether their base has been seen; and
   how long ago an enemy building and an enemy unit that fights were last in
   sight.
4. Comparison: main's strength against the enemy army seen, as a ratio; the
   plan rules' own weighing of the two armies; and how the rules count enemies
   nobody has seen lately.
5. Glossary: the line of `glossary` for each type sections 2 and 3 name.
6. Options: each open plan with what it would do now, in numbers, and each
   closed plan with what keeps it shut, in the words of the rule
   (`plans.closed_reasons`).

Every figure is the agent's own, as the cycle sees it: the world, the match
context and the plan rules' figures of the cycle. The briefing is held to
9,000 characters. One that is longer is trimmed in this order until it fits:
the recent picks, the oldest first; the glossary keeps the lines of only the
20 most valuable types named (by what the actors of the type on the map
cost, ours and theirs together); then the
enemy army types last seen more than two minutes ago are summed up in one
line; and last the sections before the options are cut at a whole line, with
a note, so that the options always stand whole at the end.

Nothing here reads a file, a clock or the network, so a briefing is written
while the cycle is decided, in well under 5 ms.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable

from ucla_agent.games.redalert import plans, raid_targets, raids
from ucla_agent.games.redalert.composition import HOLD, PRESS, RAID
from ucla_agent.games.redalert.fight import strength
from ucla_agent.games.redalert.glossary import Glossary
from ucla_agent.games.redalert.groups import GUARD, MAIN, SCOUT
from ucla_agent.games.redalert.groups import RAID as RAID_GROUP
from ucla_agent.games.redalert.items import ALLIES, FACTION_FAMILIES, SOVIET, TICKS_PER_SECOND
from ucla_agent.games.redalert.managers import NO_THREAT, MatchContext
from ucla_agent.games.redalert.plans import Situation
from ucla_agent.games.redalert.scout_report import SUPPORT_POWER_ACTORS, is_combat_unit
from ucla_agent.games.redalert.tuning import Tuning
from ucla_agent.games.redalert.world import DEFENSE_TYPES, STALE_AFTER_TICKS, Cell, EnemyRecord, Unit, World

MAX_BRIEFING_CHARS = 9000
# Over the cap, the glossary keeps the lines of only this many of the types named, the most valuable first.
GLOSSARY_KEPT_TYPES = 20
# Over the cap, the enemy army types last seen longer ago than this are summed up in one line.
OLD_SIGHTING_SECONDS = 120
CUT_NOTE = "(The briefing was cut here to keep it within 9,000 characters.)"
TIMES = "×"
SECONDS_PER_MINUTE = 60
# The discretionary plans a model chooses among, in the order the options name them.
DISCRETIONARY_PLANS = (HOLD, RAID, PRESS)
# The groups the briefing reports, in its order.
REPORTED_GROUPS = (GUARD, MAIN, RAID_GROUP, SCOUT)
FAMILY_NAMES = {ALLIES: "Allies", SOVIET: "Soviets"}
# The eight directions of the compass from east counterclockwise; the map's y grows toward the south.
DIRECTIONS = ("east", "north-east", "north", "north-west", "west", "south-west", "south", "south-east")
_DEGREES_PER_DIRECTION = 360 / len(DIRECTIONS)
_SECTION_TITLES = ("Situation", "Our forces", "Enemy as known", "Comparison", "Glossary", "Options")


@dataclass
class _Row:
    """The actors of one type: how many, what they cost together, their health summed and when one was last seen."""

    code: str
    count: int = 0
    value: int = 0
    health: int = 0
    last_seen_tick: int = 0
    in_sight: bool = False


@dataclass(frozen=True)
class _Scene:
    """What one writing of the briefing reads: the cycle's world, context, figures and open plans."""

    world: World
    ctx: MatchContext
    situation: Situation
    eligible: list[str]
    # How the briefing names a type: its glossary name and code.
    name: Callable[[str], str]

    @property
    def tuning(self) -> Tuning:
        return self.ctx.tuning


@dataclass
class _Draft:
    """The briefing's parts before the trimming: the fixed lines of each section, the enemy army's rows, which the
    trimming may sum up, and what each type named is worth on the map."""

    situation: list[str]
    ours: list[str]
    enemy_army: list[_Row]
    enemy_rest: list[str]
    comparison: list[str]
    options: list[str]
    # The situation's `recent picks:` lines (see `model_history`) with each number of the model's plan picks kept,
    # from none to all.
    recent: list[list[str]]
    # The types named outside the enemy army's rows.
    named: set[str]
    # Type -> what the actors of the type on the map cost, ours and theirs together.
    values: Counter[str]


class Briefing:
    """Writes the plan choice briefing of a cycle, naming types from the glossary. See the module's note."""

    def __init__(self, glossary: Glossary, max_chars: int = MAX_BRIEFING_CHARS) -> None:
        self._glossary = glossary
        self._max_chars = max_chars

    def write(self, world: World, ctx: MatchContext, situation: Situation, eligible: list[str]) -> str:
        """The briefing of the cycle whose figures are `situation`, with `eligible` the discretionary plans open."""
        scene = _Scene(world, ctx, situation, eligible, self._name)
        draft = _Draft(
            situation=self._situation(scene),
            ours=self._ours(scene),
            enemy_army=_rows(_enemy_army(world), world),
            enemy_rest=self._enemy_rest(scene),
            comparison=_comparison(scene),
            options=self._options(scene),
            recent=_recent(world, ctx),
            named=_named_types(world),
            values=_values_on_map(world),
        )
        # The recent picks are cut first, the oldest first; then the glossary and the enemy army's old sightings.
        trims = [(picks, None, False) for picks in range(len(draft.recent) - 1, -1, -1)]
        trims += [(0, GLOSSARY_KEPT_TYPES, False), (0, GLOSSARY_KEPT_TYPES, True)]
        for picks, glossary_limit, sums_up_old in trims:
            head, options = self._render(draft, world, picks, glossary_limit, sums_up_old)
            text = f"{head}\n\n{options}"
            if len(text) <= self._max_chars:
                return text
        return self._cut(head, options)

    def _cut(self, head: str, options: str) -> str:
        """The head cut at a whole line so that it, the note and the options fit within the cap."""
        room = self._max_chars - len(options) - len(CUT_NOTE) - len("\n\n\n")
        kept = head[: max(room, 0)].rpartition("\n")[0]
        return f"{kept}\n{CUT_NOTE}\n\n{options}"

    def _render(
        self, draft: _Draft, world: World, picks: int, glossary_limit: int | None, sums_up_old: bool
    ) -> tuple[str, str]:
        """The sections before the options, and the options, with `picks` of the recent picks, the glossary held to
        `glossary_limit` types and, `sums_up_old`, the enemy army types last seen long ago summed up."""
        listed, old = draft.enemy_army, []
        if sums_up_old:
            oldest_tick = world.tick - OLD_SIGHTING_SECONDS * TICKS_PER_SECOND
            listed = [row for row in draft.enemy_army if row.in_sight or row.last_seen_tick >= oldest_tick]
            old = [row for row in draft.enemy_army if row not in listed]
        enemy = [*self._enemy_army_lines(listed, old, world), *draft.enemy_rest]
        types = draft.named | {row.code for row in listed}
        ranked = sorted(types, key=lambda code: (-draft.values[code], code))[:glossary_limit]
        glossary = [line for line in (self._glossary.line(code, world.items) for code in ranked) if line is not None]
        situation = [*draft.situation, *draft.recent[picks]]
        sections = (situation, draft.ours, enemy, draft.comparison, [f"- {line}" for line in glossary])
        head = "\n\n".join(_section(title, lines) for title, lines in zip(_SECTION_TITLES, sections))
        return head, _section(_SECTION_TITLES[-1], draft.options)

    # -- 1. situation --------------------------------------------------------------------------------

    def _situation(self, scene: _Scene) -> list[str]:
        """The time, the sides, the plan in force, the threat, the trade balance, the money and the power."""
        world, ctx = scene.world, scene.ctx
        economy, military = world.economy, world.military
        income = ctx.income.income_per_s
        income_words = "not known yet" if income is None else f"{round(income * SECONDS_PER_MINUTE):,} per minute"
        left = "not known" if world.remaining_seconds is None else _clock(world.remaining_seconds)
        spare = economy.power_provided - economy.power_drained
        return [
            f"Time: {_clock(world.game_seconds)} played, {left} left.",
            f"Sides: we play {_side(world.faction)}, the enemy {_side(world.enemy_faction)}.",
            f"Plan in force: {ctx.plan}, set by {_rule_words(ctx.plan_rule, scene.tuning)}.",
            self._threat(scene),
            f"Trade balance: {world.trade_balance:+,} (we destroyed {military.kills_cost:,} of theirs and lost "
            f"{military.deaths_cost:,} of ours).",
            f"Money: {economy.spendable:,} to spend. Income: {income_words}.",
            f"Power: {economy.power_provided:,} made, {economy.power_drained:,} used, "
            + (f"{spare:,} spare." if spare >= 0 else f"short by {-spare:,}, so everything is built slower."),
        ]

    def _threat(self, scene: _Scene) -> str:
        """The threat to our base: none, or its size, what it is against and where, and the enemy in sight there."""
        world, ctx = scene.world, scene.ctx
        if ctx.threat_level == NO_THREAT or ctx.threat_cell is None:
            return f"Threat to our base: {ctx.threat_level}."
        cell, reach = ctx.threat_cell, scene.tuning.base_defense.threat_area_cells
        centres = [cell] if world.base_center is None else [cell, world.base_center]
        attackers = [
            record
            for record in world.enemy_memory.values()
            if record.visible
            and is_combat_unit(record)
            and any(_distance(_cell_of(record), at) <= reach for at in centres)
        ]
        rows = _rows(attackers, world)
        seen = (
            f"enemy in sight there: {self._counts(rows)}, worth {sum(row.value for row in rows):,}"
            if rows
            else "no enemy in sight there"
        )
        return f"Threat to our base: {ctx.threat_level}, against {self._threatened(cell, world)}; {seen}."

    def _threatened(self, cell: Cell, world: World) -> str:
        """The own building or unit at the threatened cell, and where that is from our base's center."""
        own = [*world.buildings.values(), *world.units.values()]
        at_cell = next((actor.type for actor in own if (actor.x, actor.y) == cell), None)
        what = f"the cell {cell[0]},{cell[1]}" if at_cell is None else f"our {self._name(_base(at_cell, world))}"
        return f"{what}, {_whereabouts(world.base_center, cell)}"

    # -- 2. our forces -------------------------------------------------------------------------------

    def _ours(self, scene: _Scene) -> list[str]:
        """Our army by type, the groups, our other units and our buildings."""
        world, groups = scene.world, scene.ctx.groups
        army = _rows(world.combat_units.values(), world)
        lines = ["Our army (the units that fight), by type:" if army else "Our army: no unit that fights."]
        lines += [f"- {self._count(row)}: {row.value:,}, health {row.health // row.count}%" for row in army]
        group_words = []
        for name in REPORTED_GROUPS:
            units = [world.combat_units[unit_id] for unit_id in groups.members(name)]
            group_words.append(f"{name} {_strength_words(units, world)}" if units else f"{name} empty")
        lines.append(f"Groups: {'; '.join(group_words)}.")
        others = _rows((unit for unit in world.units.values() if unit.id not in world.combat_units), world)
        if others:
            lines.append(f"Our other units: {self._counts(others)}.")
        buildings = _rows(world.buildings.values(), world)
        lines.append(f"Our buildings: {self._counts(buildings) if buildings else 'none'}.")
        return lines

    # -- 3. the enemy --------------------------------------------------------------------------------

    def _enemy_army_lines(self, listed: list[_Row], old: list[_Row], world: World) -> list[str]:
        """The enemy army seen, a line for each type listed, and the types summed up in one line."""
        if not listed and not old:
            return ["Enemy army seen: none so far."]
        stale_seconds = STALE_AFTER_TICKS // TICKS_PER_SECOND
        lines = [f"Enemy army seen, by type (a unit not seen for {stale_seconds} s no longer counts in its value):"]
        lines += [f"- {self._count(row)}: {row.value:,}, {_age(row, world)}" for row in listed]
        if old:
            units, value = sum(row.count for row in old), sum(row.value for row in old)
            lines.append(
                f"- {len(old)} more types last seen over {OLD_SIGHTING_SECONDS} s ago: {units} units worth {value:,}"
            )
        return lines

    def _enemy_rest(self, scene: _Scene) -> list[str]:
        """What is known of the enemy besides its army: its other units, buildings and defenses, its base, and how long
        ago a building and a unit that fights were last in sight."""
        world, report = scene.world, scene.ctx.scout_report
        remembered = list(world.enemy_memory.values())
        others = _rows(
            (record for record in remembered if not record.is_building and _is_other_unit(record)), world, by_age=True
        )
        buildings = _rows(
            (record for record in remembered if record.is_building and record.type not in DEFENSE_TYPES), world
        )
        lines = []
        if others:
            lines.append(
                "Other enemy units seen: "
                + "; ".join(f"{self._count(row)}, {_age(row, world)}" for row in others)
                + "."
            )
        lines.append(f"Enemy buildings seen: {self._counts(buildings) if buildings else 'none'}.")
        lines += self._defense_lines(scene)
        lines.append(_base_words(scene))
        lines.append(_last_seen_words(scene.situation, report.last_building_seen_tick, world.tick))
        return lines

    def _defense_lines(self, scene: _Scene) -> list[str]:
        """The enemy defenses seen by type, each near their base or forward."""
        world = scene.world
        defenses = world.known_enemy_defenses
        if not defenses:
            return ["Enemy defenses seen: none."]
        places: dict[str, Counter[str]] = {}
        for record in defenses:
            places.setdefault(_base(record.type, world), Counter())[_defense_place(record, scene)] += 1
        lines = ["Enemy defenses seen, by type:"]
        for row in _rows(defenses, world):
            where = "; ".join(f"{count} {place}" for place, count in places[row.code].items())
            lines.append(f"- {self._count(row)}: {where}")
        return lines

    # -- 6. options ----------------------------------------------------------------------------------

    def _options(self, scene: _Scene) -> list[str]:
        """A line for each discretionary plan: what it would do when it is open, what keeps it shut when it is not."""
        world, ctx, tuning = scene.world, scene.ctx, scene.tuning
        is_press_in_force = ctx.plan == PRESS
        reasons = plans.closed_reasons(scene.situation, world, ctx, tuning, is_press_in_force)
        lines = []
        for plan in DISCRETIONARY_PLANS:
            if plan in scene.eligible:
                lines.append(f"- {self._open_plan_words(plan, scene)}")
            else:
                words = "; ".join(_REASON_WORDS[reason](scene) for reason in reasons[plan])
                lines.append(f"- {plan} closed: {words}.")
        return lines

    def _open_plan_words(self, plan: str, scene: _Scene) -> str:
        """What an open plan would do now, in numbers."""
        world, ctx = scene.world, scene.ctx
        main = [world.combat_units[unit_id] for unit_id in ctx.groups.members(MAIN)]
        main_words = f"main ({_strength_words(main, world)})"
        if plan == HOLD:
            hold = (
                "holds a forward point between the bases and fights what comes near it"
                if scene.tuning.combat.forward_hold
                else "stays home and defends"
            )
            return f"HOLD: {main_words} {hold} while the economy and the army grow."
        if plan == RAID:
            return f"RAID: {self._raid_words(scene)}; {main_words} stays home."
        return f"PRESS: {main_words} attacks toward the enemy base {self._press_words(scene)}"

    def _raid_words(self, scene: _Scene) -> str:
        """What the raid group is made of, what it holds now, and the targets it may be sent at."""
        world, ctx, tuning = scene.world, scene.ctx, scene.tuning
        quota = raids.quota(world, tuning.composition)
        quota_words = ", ".join(f"{count} {TIMES} {self._name(code)}" for code, count in quota.items())
        group = [world.combat_units[unit_id] for unit_id in ctx.groups.members(RAID_GROUP)]
        now = _strength_words(group, world) if group else "empty now"
        targets = _rows(raid_targets.targets(world, plans.raid_group_types(world, tuning), tuning), world)
        count = sum(row.count for row in targets)
        return (
            f"the raid group ({quota_words}; {now}) sets out once it is complete and hits the nearest of {count} "
            f"targets no known defense covers ({self._counts(targets)})"
        )

    def _press_words(self, scene: _Scene) -> str:
        """When main goes forward under PRESS, and the enemy defenses known."""
        world, ctx = scene.world, scene.ctx
        least = ctx.min_attack_strength(PRESS)
        defenses = _rows(world.known_enemy_defenses, world)
        defense_words = f"known enemy defenses: {self._counts(defenses)}" if defenses else "no enemy defense known"
        return f"once its strength is at least {least:,}; {defense_words}; the raids go on."

    # -- names ---------------------------------------------------------------------------------------

    def _name(self, code: str) -> str:
        """A type as the briefing names it: its glossary name and code, or the code alone when the glossary lacks it."""
        entry = self._glossary.get(code)
        return code if entry is None else f"{entry.name} ({code})"

    def _count(self, row: _Row) -> str:
        return f"{row.count} {TIMES} {self._name(row.code)}"

    def _counts(self, rows: Iterable[_Row]) -> str:
        return ", ".join(self._count(row) for row in rows)


# -- the rows of actors by type ------------------------------------------------------------------------


def _base(actor_type: str, world: World) -> str:
    """The type an actor's type is a variant of, or the type itself; a variant is named and valued as its type."""
    facts = world.items.get(actor_type)
    return actor_type if facts is None else facts.name


def _cost(code: str, world: World) -> int:
    facts = world.items.get(code)
    return 0 if facts is None else facts.cost


def _rows(actors: Iterable[Unit | EnemyRecord], world: World, *, by_age: bool = False) -> list[_Row]:
    """The actors by type, the most valuable first, or with `by_age` the most lately seen first; then by code."""
    rows: dict[str, _Row] = {}
    for actor in actors:
        code = _base(actor.type, world)
        row = rows.setdefault(code, _Row(code))
        row.count += 1
        row.value += _cost(code, world)
        row.health += actor.hp
        if isinstance(actor, EnemyRecord):
            row.last_seen_tick = max(row.last_seen_tick, actor.last_seen_tick)
            row.in_sight = row.in_sight or actor.visible
    order: Callable[[_Row], tuple[int, str]] = (
        (lambda row: (-row.last_seen_tick, row.code)) if by_age else (lambda row: (-row.value, row.code))
    )
    return sorted(rows.values(), key=order)


def _enemy_army(world: World) -> list[EnemyRecord]:
    """The remembered enemy units that fight, stale or not: the briefing says how long ago each type was seen."""
    return [record for record in world.enemy_memory.values() if is_combat_unit(record)]


def _is_other_unit(record: EnemyRecord) -> bool:
    """Whether a remembered enemy unit is one that does not fight, such as a harvester; the support powers' planes and
    cameras are no units."""
    return not is_combat_unit(record) and record.type not in SUPPORT_POWER_ACTORS


def _recent(world: World, ctx: MatchContext) -> list[list[str]]:
    """The `recent picks:` lines of the model's plan picks (see `model_history`) with none of them kept, one, and so
    on up to all."""
    history = ctx.model_history
    return [history.plan_lines(world, picks) for picks in range(len(history.plan_picks) + 1)]


def _named_types(world: World) -> set[str]:
    """The types sections 2 and 3 name besides the enemy army's: our units and buildings, the enemy's other units,
    buildings and defenses."""
    ours = {_base(actor.type, world) for actors in (world.units, world.buildings) for actor in actors.values()}
    theirs = {
        _base(record.type, world)
        for record in world.enemy_memory.values()
        if record.is_building or _is_other_unit(record)
    }
    return ours | theirs


def _values_on_map(world: World) -> Counter[str]:
    """Type -> what the actors of the type cost together, ours and the enemy's remembered."""
    values: Counter[str] = Counter()
    actors = [*world.units.values(), *world.buildings.values(), *world.enemy_memory.values()]
    for actor in actors:
        code = _base(actor.type, world)
        values[code] += _cost(code, world)
    return values


# -- words -------------------------------------------------------------------------------------------


def _section(title: str, lines: list[str]) -> str:
    return "\n".join([f"## {title}", *lines])


def _clock(seconds: float) -> str:
    """Seconds as minutes and seconds, `m:ss`."""
    whole = int(seconds)
    return f"{whole // SECONDS_PER_MINUTE}:{whole % SECONDS_PER_MINUTE:02d}"


def _side(faction: str | None) -> str:
    """A faction and its family, as `England (Allies)`."""
    if faction is None:
        return "an unknown faction"
    family = FAMILY_NAMES.get(FACTION_FAMILIES.get(faction, ""), "an unknown side")
    return f"{faction.capitalize()} ({family})"


def _rule_words(rule: str | None, tuning: Tuning) -> str:
    """The rule of the plan selector that set the plan in force, in words."""
    rules = tuning.plan_rules
    words = {
        plans.R1: "the opening, which lasts until a war factory stands",
        plans.R2: f"the clock rule that keeps a lead in the last {_clock(rules.clock_seconds)}",
        plans.R3: f"the clock rule that holds at an even balance in the last {_clock(rules.clock_seconds)}",
        plans.R4: f"the clock rule that presses when behind in the last {_clock(rules.press_late_seconds)}",
        plans.R5: "the early strike on an undefended base",
        plans.R6: "the finishing attack",
        plans.R7: "the script's choice",
        plans.MODEL: "a model's choice",
        plans.SPARRING: "a sparring pin",
    }
    return words.get(rule, "no rule yet")


def _strength_words(units: list[Unit], world: World) -> str:
    plural = "unit" if len(units) == 1 else "units"
    return f"{len(units)} {plural}, strength {round(strength(units, world)):,}"


def _age(row: _Row, world: World) -> str:
    """How long ago a type was last seen."""
    if row.in_sight:
        return "in sight now"
    return f"last seen {(world.tick - row.last_seen_tick) // TICKS_PER_SECOND} s ago"


def _cell_of(record: EnemyRecord) -> Cell:
    return record.x, record.y


def _distance(cell: Cell, other: Cell) -> int:
    return round(math.hypot(cell[0] - other[0], cell[1] - other[1]))


def _whereabouts(origin: Cell | None, cell: Cell) -> str:
    """Where a cell is from our base's center: how many cells and in which direction."""
    if origin is None:
        return f"at {cell[0]},{cell[1]}"
    distance = _distance(origin, cell)
    if distance == 0:
        return "at our base's center"
    # The map's y grows toward the south, so north is a smaller y.
    degrees = math.degrees(math.atan2(origin[1] - cell[1], cell[0] - origin[0]))
    direction = DIRECTIONS[round(degrees / _DEGREES_PER_DIRECTION) % len(DIRECTIONS)]
    return f"{distance} cells {direction} of our base's center"


def _defense_place(record: EnemyRecord, scene: _Scene) -> str:
    """Where an enemy defense stands: near their base, or forward, so many cells from ours."""
    world = scene.world
    enemy_base, base_center = world.enemy_base, world.base_center
    if enemy_base is not None and _distance(_cell_of(record), enemy_base) <= scene.tuning.scouting.base_seen_cells:
        return "near their base"
    if base_center is None:
        return "away from their base"
    return f"forward, {_distance(_cell_of(record), base_center)} cells from our base"


def _base_words(scene: _Scene) -> str:
    """Whether the enemy base has been seen, and where their start is."""
    world = scene.world
    seen = "seen" if scene.ctx.scout_report.enemy_base_seen else "not found yet"
    if world.enemy_base is None:
        return f"Their base: {seen}; where they started is not known."
    return f"Their base: {seen}; they started {_whereabouts(world.base_center, world.enemy_base)}."


def _last_seen_words(situation: Situation, building_seen_tick: int | None, tick: int) -> str:
    """How long ago an enemy building and an enemy unit that fights were last in sight."""
    building = (
        "no enemy building has been seen yet"
        if building_seen_tick is None
        else f"an enemy building was last in sight {(tick - building_seen_tick) // TICKS_PER_SECOND} s ago"
    )
    unseen = situation.enemy_unseen_ticks
    if unseen is None:
        army = "no enemy unit that fights has been seen yet"
    elif unseen == 0:
        army = "an enemy unit that fights is in sight now"
    else:
        army = f"an enemy unit that fights was last in sight {unseen // TICKS_PER_SECOND} s ago"
    return f"Last seen: {building}; {army}."


# -- 4. comparison -----------------------------------------------------------------------------------


def _comparison(scene: _Scene) -> list[str]:
    """Main against the enemy army seen, the plan rules' weighing of the two armies and how unseen enemies count."""
    world, situation, rules = scene.world, scene.situation, scene.tuning.plan_rules
    stale_seconds = STALE_AFTER_TICKS // TICKS_PER_SECOND
    enemy_seen, main = situation.enemy_combat_value, situation.main_strength
    seen = f"the enemy army seen in the last {stale_seconds} s"
    first = (
        f"Main's strength {main:,} against {seen}, worth {enemy_seen:,}: ratio {main / enemy_seen:.2f}."
        if enemy_seen > 0
        else f"Main's strength {main:,}; {seen} is worth nothing, so there is no ratio to give."
    )
    army = plans.weighed_army(situation, rules)
    enemy = plans.weighed_enemy(situation, rules)
    army_words = "the units that fight" if rules.early_attack_guard else "every unit, harvesters included"
    return [
        first,
        f"The plan rules weigh our army ({army_words}) at {army:,} against the enemy army at {round(enemy):,}: ratio "
        f"{situation.ratio:.2f}. PRESS needs a ratio of {rules.press_army_ratio:g} and an army of "
        f"{rules.press_min_army:,}; the script holds below {rules.default_weaker_ratio:g} and presses from "
        f"{rules.default_press_ratio:g}.",
        _unseen_words(situation, scene.tuning, stale_seconds, world),
    ]


def _unseen_words(situation: Situation, tuning: Tuning, stale_seconds: int, world: World) -> str:
    """How the plan rules count enemies nobody has seen lately, and whether the enemy army is known now."""
    rules = tuning.plan_rules
    drops = f"How unseen enemies count: an enemy unit not seen for {stale_seconds} s drops out of the army seen"
    if not rules.blind_press_guard:
        return f"{drops}, and the plan rules weigh only what is seen."
    fresh = _clock(rules.enemy_army_fresh_ticks / TICKS_PER_SECOND)
    share = (
        "as strong as ours"
        if rules.assumed_enemy_share == 1
        else f"{rules.assumed_enemy_share:g} times as strong as ours"
    )
    known = "known now" if situation.enemy_army_known else "not known now"
    return (
        f"{drops}. The enemy army is known while one of its units that fights on the ground has been seen in the last "
        f"{fresh}: known, it is weighed at the most it was worth in that time; unknown, it is taken to be {share}. "
        f"It is {known}."
    )


# -- why a plan is closed ----------------------------------------------------------------------------


def _raids_off(_scene: _Scene) -> str:
    return "raids are switched off"


def _no_raid_building(scene: _Scene) -> str:
    return f"no {scene.name(plans.raid_building(scene.world))} stands to make the raid group in"


def _no_raid_quota(_scene: _Scene) -> str:
    return "our side fields no raid group now"


def _no_raid_target(scene: _Scene) -> str:
    raid_tuning = scene.tuning.raids
    stale_seconds = STALE_AFTER_TICKS // TICKS_PER_SECOND
    return (
        f"no target a raid may hit: no enemy harvester seen in the last {stale_seconds} s outside their defenses' "
        f"reach, and no refinery the raid group could destroy once no harvester has been seen for "
        f"{_clock(raid_tuning.refinery_after_ticks / TICKS_PER_SECOND)}"
    )


def _not_launchable(scene: _Scene) -> str:
    """Why a raid could not set out now, in the plan rules' own words: the same the plan record gives."""
    return scene.situation.raid_launch_blocker


def _early_gate(scene: _Scene) -> str:
    rules = scene.tuning.plan_rules
    return (
        f"early-attack guard until {_clock(rules.early_press_until_seconds)} unless the enemy commits its army (an "
        f"attack on our base beaten off, or {rules.early_press_kill_value:,} of theirs destroyed; "
        f"{scene.world.military.kills_cost:,} so far)"
    )


def _press_too_early(scene: _Scene) -> str:
    return f"not before {_clock(scene.tuning.plan_rules.press_from_seconds)}"


def _army_too_small(scene: _Scene) -> str:
    rules = scene.tuning.plan_rules
    army = plans.weighed_army(scene.situation, rules)
    return f"our army is worth {army:,} as the plan rules weigh it, under the {rules.press_min_army:,} needed"


def _army_not_ahead(scene: _Scene) -> str:
    situation, rules = scene.situation, scene.tuning.plan_rules
    army, enemy = plans.weighed_army(situation, rules), plans.weighed_enemy(situation, rules)
    unknown = (
        ", the enemy army being unknown and so taken to be as strong as ours"
        if rules.blind_press_guard and not situation.enemy_army_known
        else ""
    )
    return (
        f"our army ({army:,}) is under {rules.press_army_ratio:g} times the enemy's as the plan rules weigh it "
        f"({round(enemy):,}){unknown}"
    )


# A reason `plans.closed_reasons` gives -> its words.
_REASON_WORDS: dict[str, Callable[[_Scene], str]] = {
    plans.RAIDS_OFF: _raids_off,
    plans.NO_RAID_BUILDING: _no_raid_building,
    plans.NO_RAID_QUOTA: _no_raid_quota,
    plans.NO_RAID_TARGET: _no_raid_target,
    plans.RAID_NOT_LAUNCHABLE: _not_launchable,
    plans.EARLY_ATTACK_BLOCK_REASON: _early_gate,
    plans.PRESS_TOO_EARLY: _press_too_early,
    plans.ARMY_TOO_SMALL: _army_too_small,
    plans.ARMY_NOT_AHEAD: _army_not_ahead,
}
