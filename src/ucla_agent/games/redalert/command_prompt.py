"""What the model commander's two questions share: the actions, the picks, the clock and the request held to its cap.

The commander (see `commander`) asks a small model every batch to pick one
action for the units it commands: the first scout (see `command_scout`), then
main (see `command_army`). Both questions are written the same way. A fixed
system text ends by asking for `{"choice":"ID"}`; the user message is a few
lines of the state, then `ACTIONS:` and one line for each action the script
has checked. Each kind of action has a letter of its own, the same whatever
else is offered (see `lettered`), and the actions are listed by their letters;
a kind not offered leaves its letter unused. The answer is held by the
source's schema to the letters offered, with no reason, in at most
`ANSWER_TOKENS` tokens, and is not waited for past `DEADLINE_MS`.

A question is held to a number of characters, its system text included. It is
written at the fullest of its levels of detail first and, while it is too
long, at the next. The scout's levels (`DETAILS`) list fewer picks first, down
to the last one, then leave out its full-state sections (see `command_state`)
one by one, the enemy buildings, the ore fields and the enemy groups, then the
enemy defenses, then list fewer groups, defenses, fields, building types and
unit types, then shorter action lines. main's (`ARMY_DETAILS`) leave out the
far band of its military state first, then the mid band, then list fewer
picks, then the same lesser levels. At the last level of either an action
that names enemy types (main's attack and hit) lists the first
`SHORT_LIST_TYPES` of them and how many more (`+2 more`). The last level is
built to fit; should it not, it is written again with the system text's short
form, without the framing of `model_history`, at the fullest of the levels
that list one pick (the history is still cut first) that fits, so that the
state the framing's room makes up for is not cut too; a question that does
not fit even so is an error.

The actions are always listed in the order of their letters, whatever order
they were found in, so that the same situation reads the same in the text and
in a systemone server's criteria; an action's line ends with its consequence,
when it has one (see `command_army`).

Each system text ends its own words with the framing of `model_history`:
main's with the whole of it, the scout's with the tournament and the learning
from recent picks but not the army's doctrine, which is about main.

Both user messages put the lines that change least first and the clock last,
just before `ACTIONS:`: a model server that reuses the unchanged beginning of
a prompt then reuses the system text and the full-state sections from one
question to the next.

An action's letter names its kind. What tells one action from another from
cycle to cycle is its key, its kind and its target, by which an answer is
checked against the actions on offer when it arrives.

Both questions say what the units commanded are doing, on a `current:` line
just before `last_picks:` (see `current_line`): the standing action's
letter, its words, its cell (the target group's centroid for an attack, the
units' own cell for a hold), how many cells are left to it, and how long ago
it was taken; `current: none` while there is none. The action of the
standing action's key on offer ends its line with ` (current)`. The
`last_picks:` line lists at most `[models] history_picks` picks (3 as
shipped), the newest first; main's says what was killed and lost since each
and the army ratio then and now (`ratio 1.4 -> 1.1`, see `model_history`).

With continue_option of `[commander]` on, a standing action still sound but
left off a fresh list is offered again (see `command_army`, `command_scout`)
as the continue option: lettered `CONTINUE_ID` (Z) under the offer kind
`CONTINUE`, its line headed `continue: ` once, in a place left among
options_max or in the place of the lowest-ranked action that may give way
(see `with_continue`). Its key is the standing action's, so its line is
marked ` (current)`, and an answer naming it names the standing action.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable, Mapping, Sequence

from ucla_agent.core.decide import ChoiceRequest, Option
from ucla_agent.games.redalert import cells
from ucla_agent.games.redalert.compact_prompt import prompt_chars
from ucla_agent.games.redalert.glossary import Glossary
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.model_history import ModelHistory, ratio_change
from ucla_agent.games.redalert.waypoints import WaypointRoute
from ucla_agent.games.redalert.world import Cell, World

ACTIONS_HEADING = "ACTIONS:"
CURRENT_MARK = " (current)"
# What separates an action's line from its consequence.
CONSEQUENCE_MARK = "->"
# The answer is one letter in an object of a few tokens; the model is not waited for longer than a batch or so.
ANSWER_TOKENS = 16
DEADLINE_MS = 800
SECONDS_PER_MINUTE = 60
UNKNOWN = "unknown"
NONE = "none"
NEVER = "never"
# The commanded units have arrived when they stand this near the cell of their standing action.
ARRIVED_CELLS = 3
# The offer kind of a standing action offered again when the fresh list left it off, its letter, and its line's head.
CONTINUE = "continue"
CONTINUE_ID = "Z"
CONTINUE_HEAD = "continue: "


@dataclass(frozen=True)
class Detail:
    """How much a question says: at most how many groups, defenses, ore fields, building types, unit types and picks
    its lines list, whether its action lines say what each action shows, whether an action naming enemy types names
    every one of them (else the first SHORT_LIST_TYPES and how many more), which of the scout's full-state sections
    (see `command_state`) it holds, and whether main's military state keeps its mid and far bands."""

    groups: int
    defenses: int
    fields: int
    building_types: int
    unit_types: int
    # None for every pick the commander remembers.
    picks: int | None
    action_details: bool
    all_types: bool = True
    ore_fields: bool = True
    enemy_buildings: bool = True
    enemy_defenses: bool = True
    enemy_groups: bool = True
    mid_band: bool = True
    far_band: bool = True


# The full level of the state lines, listing one pick, with the full-state sections and without any.
_FULL = Detail(groups=3, defenses=3, fields=3, building_types=5, unit_types=5, picks=1, action_details=True)
_WITHOUT_ENEMY_GROUPS = {"enemy_buildings": False, "ore_fields": False, "enemy_groups": False}
_STATE_ONLY = {**_WITHOUT_ENEMY_GROUPS, "enemy_defenses": False}
_NEAR_ONLY = {"mid_band": False, "far_band": False}
# The lesser levels both questions end with: fewer groups, defenses, fields, building types and unit types, then
# shorter action lines, and at the last the enemy types an action names cut.
_LESSER = (
    Detail(groups=2, defenses=2, fields=2, building_types=3, unit_types=4, picks=1, action_details=True),
    Detail(groups=1, defenses=1, fields=1, building_types=0, unit_types=3, picks=1, action_details=True),
    Detail(groups=1, defenses=1, fields=1, building_types=0, unit_types=2, picks=1, action_details=False),
    Detail(
        groups=1, defenses=1, fields=0, building_types=0, unit_types=1, picks=1, action_details=False, all_types=False
    ),
)
# The scout's levels, the fullest first; the last is the one built to fit its cap. The picks are cut first, down to the
# last one; then the full-state sections: the enemy buildings, the ore fields, the enemy groups; then the defenses and
# the detail of the other lines.
DETAILS = (
    replace(_FULL, picks=None),
    replace(_FULL, picks=2),
    _FULL,
    replace(_FULL, enemy_buildings=False),
    replace(_FULL, enemy_buildings=False, ore_fields=False),
    replace(_FULL, **_WITHOUT_ENEMY_GROUPS),
    replace(_FULL, **_STATE_ONLY),
    *(replace(detail, **_STATE_ONLY) for detail in _LESSER),
)
# main's levels: the far band of its military state is cut first, then the mid band, then the picks down to the last
# one, then the detail of the other lines.
ARMY_DETAILS = (
    replace(_FULL, picks=None),
    replace(_FULL, picks=None, far_band=False),
    replace(_FULL, picks=None, **_NEAR_ONLY),
    replace(_FULL, picks=2, **_NEAR_ONLY),
    replace(_FULL, **_NEAR_ONLY),
    *(replace(detail, **_NEAR_ONLY) for detail in _LESSER),
)
# At the last level an action naming enemy types names this many of them, the most worth first, and how many more.
SHORT_LIST_TYPES = 3


def short_text_details(details: Sequence[Detail]) -> tuple[Detail, ...]:
    """The levels a question is written at with the short system text, should none of `details` fit with the full
    one: those that list one pick, the history being cut first."""
    return tuple(detail for detail in details if detail.picks == 1)


@dataclass(frozen=True)
class Action:
    """One action the commander offers: what it is, the line the model reads, and where it sends the units."""

    kind: str
    # What tells the action apart from cycle to cycle: its kind and, for one with a target, the target.
    key: tuple[Any, ...]
    # The few words a pick of it is remembered by, as `last_picks` and the `commander` events give it.
    label: str
    # The line the model reads after the letter, and what more it says at the levels with action details.
    head: str
    detail: str = ""
    # The line read in place of `head` at the level that cuts the enemy types an action names; empty for `head`.
    short_head: str = ""
    # Where it sends the units; None for an action that sends them nowhere.
    cell: Cell | None = None
    # A cell the units go to first, on their way to `cell`; None for none.
    via: Cell | None = None
    # The letter it is offered under in its question; empty until it is lettered.
    id: str = ""
    # The way through the graph of waypoints the units go by to `cell`, `via` its first cell; None for none.
    route: WaypointRoute | None = None
    # A task's units by id, the ids of the enemies it is on, and our chance of it in percent (see `command_tasks`);
    # of a raid trucks action the ids of its harvesters alone; none for any other action.
    units: tuple[int, ...] = ()
    aim: tuple[int, ...] = ()
    chance: int = 0
    # The kind the action is lettered and kept in a cut as, when it is not the kind it is carried out as (main's
    # economy target and expansion target, attacks); empty for `kind`.
    offer_kind: str = ""
    # What taking it leads to, worked out in code (see `command_army`), which ends its line at every level; empty for
    # none.
    consequence: str = ""

    def line(self, detail: Detail) -> str:
        """The action's line in a question written at a level."""
        head = self.short_head if self.short_head and not detail.all_types else self.head
        return f"{head}; {self.detail}" if detail.action_details and self.detail else head

    @property
    def offered_as(self) -> str:
        """The kind the action is lettered and kept in a cut as."""
        return self.offer_kind or self.kind


@dataclass(frozen=True)
class Pick:
    """An action the model picked that the commander took: its letter and label then, the tick, the value the match
    had killed and lost by then, and the plan rules' army ratio then (None while it was unknown)."""

    id: str
    label: str
    tick: int
    kills_cost: int
    deaths_cost: int
    ratio: float | None = None


@dataclass(frozen=True)
class Current:
    """The standing action of the units commanded, the tick it was taken, and where they stand."""

    action: Action
    taken_tick: int
    position: Cell


@dataclass(frozen=True)
class Situation:
    """What the commander's commitment to a standing action reads of the units it commands in a cycle (see
    `commander`): where they stand, whether they are alarmed (for main, a threat holds; for the scout, it must flee a
    danger), and whether main's forward gate is open."""

    position: Cell
    is_alarmed: bool
    is_gate_open: bool = False


@dataclass(frozen=True)
class Question:
    """One question of the commander: the request a source is asked, and the actions it offers, lettered."""

    request: ChoiceRequest
    actions: tuple[Action, ...]


def lettered(actions: Iterable[Action], ids: Mapping[str, str]) -> tuple[Action, ...]:
    """The actions with the letters `ids` gives the kinds they are offered as, listed by letter."""
    return tuple(
        sorted((replace(action, id=ids[action.offered_as]) for action in actions), key=lambda action: action.id)
    )


def continued(action: Action) -> Action:
    """A standing action as the continue option: lettered CONTINUE_ID under the offer kind CONTINUE, its lines headed
    CONTINUE_HEAD once."""

    def headed(line: str) -> str:
        return line if not line or line.startswith(CONTINUE_HEAD) else CONTINUE_HEAD + line

    return replace(action, head=headed(action.head), short_head=headed(action.short_head), offer_kind=CONTINUE)


def with_continue(
    actions: Sequence[Action], option: Action, giving_way: Sequence[Action], most: int
) -> tuple[Action, ...]:
    """The actions with the continue option added (see `continued`), listed by their letters: in a place left among
    `most`, else in that of the first of `giving_way`, the lowest-ranked action that may give way; the actions as they
    were when the list is full and none may."""
    kept = list(actions)
    if len(kept) >= most:
        if not giving_way:
            return tuple(actions)
        kept = [action for action in kept if action.key != giving_way[0].key]
    return tuple(sorted([*kept, replace(continued(option), id=CONTINUE_ID)], key=lambda action: action.id))


def fitted(
    system_text: str,
    lines_at: Callable[[Detail], list[str]],
    actions: Sequence[Action],
    tick: int,
    max_chars: int,
    current: Current | None = None,
    short_system_text: str | None = None,
    *,
    details: Sequence[Detail] = DETAILS,
    state_object: Mapping[str, Any] | None = None,
) -> Question:
    """The question with the most detail of `details` that is at most `max_chars` long, its system text included, the
    action of the `current` standing action's key marked; when even the last level is longer, with
    `short_system_text`, the system text without its framing, when there is one, at the fullest of the levels that
    list one pick that fits. The request carries `state_object`, the same state as a JSON object, when given.

    Raises `ValueError` when even the last level is longer, with the short system text too.
    """
    current_key = None if current is None else current.action.key
    levels = [(system_text, detail) for detail in details]
    if short_system_text is not None:
        levels.extend((short_system_text, detail) for detail in short_text_details(details))
    for text, detail in levels:
        request = _request(text, lines_at(detail), actions, tick, detail, current_key)
        if prompt_chars(request) <= max_chars:
            return Question(replace(request, state_object=state_object), tuple(actions))
    raise ValueError(
        f"A commander question is {prompt_chars(request)} characters at its least detail, over {max_chars}."
    )


def _request(
    system_text: str,
    lines: list[str],
    actions: Sequence[Action],
    tick: int,
    detail: Detail,
    current_key: tuple[Any, ...] | None,
) -> ChoiceRequest:
    """The request of a question: its state lines, `ACTIONS:` and a line for each action in the order of their letters,
    the line of the action of `current_key` marked, then its consequence, answered with a letter alone."""
    return ChoiceRequest(
        question=ACTIONS_HEADING,
        options=[
            Option(
                action.id,
                action.line(detail)
                + (CURRENT_MARK if action.key == current_key else "")
                + (f" {CONSEQUENCE_MARK} {action.consequence}" if action.consequence else ""),
            )
            for action in sorted(actions, key=lambda action: action.id)
        ],
        context="\n".join(lines),
        deadline_ms=DEADLINE_MS,
        state_tag=str(tick),
        system_text=system_text,
        answer_tokens=ANSWER_TOKENS,
        with_reason=False,
    )


def clock(world: World) -> str:
    """`t=<mm:ss> left=<mm:ss>`: the match's time and what is left of it, `unknown` while the state gives no clock."""
    left = world.remaining_seconds
    return f"t={mm_ss(world.game_seconds)} left={UNKNOWN if left is None else mm_ss(left)}"


def mm_ss(seconds: float) -> str:
    """Seconds as minutes and seconds, `mm:ss`."""
    minutes, rest = divmod(max(0, int(seconds)), SECONDS_PER_MINUTE)
    return f"{minutes:02d}:{rest:02d}"


def seconds_ago(tick: int, world: World) -> int:
    """How many whole seconds before the world's tick a tick was."""
    return max(0, world.tick - tick) // TICKS_PER_SECOND


def at(cell: Cell) -> str:
    """A cell as the questions write it, `(x,y)`."""
    return f"({cell[0]},{cell[1]})"


def plain_names(glossary: Glossary | None) -> dict[str, str]:
    """Each type code the glossary explains -> its name in plain words, in lower case; none without a glossary, when
    the questions name types by their codes."""
    if glossary is None:
        return {}
    return {code: entry.name.lower() for code in glossary if (entry := glossary.get(code)) is not None}


def named(code: str, names: Mapping[str, str]) -> str:
    """A type as the questions name it: its plain name when one is known, else its code."""
    return names.get(code, code)


def counts(types: Iterable[str], most: int | None = None) -> str:
    """Types counted, `3 e1, 1 jeep`, the most numerous first (then by name), at most `most` of them."""
    ranked = sorted(Counter(types).items(), key=lambda pair: (-pair[1], pair[0]))
    return ", ".join(f"{count} {name}" for name, count in ranked[:most])


def by_value(valued: Iterable[tuple[str, float]], most: int | None = None, *, with_rest: bool = False) -> str:
    """Types counted from (type, value) pairs, `2 medium tank, 14 rifle infantry`, the most worth in total first (then
    the most numerous, then by name), at most `most` of them, so that a cut leaves out the cheapest; `with_rest`, the
    types left out counted after them, `+2 more`."""
    totals: defaultdict[str, float] = defaultdict(float)
    numbers: Counter[str] = Counter()
    for name, value in valued:
        totals[name] += value
        numbers[name] += 1
    ranked = sorted(numbers, key=lambda name: (-totals[name], -numbers[name], name))
    shown = [f"{numbers[name]} {name}" for name in ranked[:most]]
    left = len(ranked) - len(shown)
    return ", ".join([*shown, *([f"+{left} more"] if with_rest and left else [])])


def current_line(current: Current | None, offered: Sequence[Action], world: World) -> str:
    """`current:` the standing action of the units commanded, as the action of its key on offer has it when there is
    one: its letter and words, its cell, the cells left to it and how long ago it was taken; `current: none` for
    none."""
    if current is None:
        return f"current: {NONE}"
    action = next((action for action in offered if action.key == current.action.key), current.action)
    cell = current.position if action.cell is None else action.cell
    words = action.label if action.cell is None else action.label.removesuffix(f" {at(action.cell)}")
    left = round(cells.distance(current.position, cell))
    return (
        f"current: {action.id} {words} to {at(cell)}, {left} cells left, "
        f"taken {seconds_ago(current.taken_tick, world)}s ago"
    )


def picks_line(
    picks: Sequence[Pick], detail: Detail, world: World, *, with_result: bool, history: ModelHistory | None = None
) -> str | None:
    """`last_picks:` the picks remembered, the newest first, at most the `history`'s `history_picks` of them, each
    with its age and, `with_result`, the value killed and lost since and the army ratio then and now (see
    `model_history`); None before the first pick."""
    history = history or ModelHistory()
    shown = list(reversed(picks))[: min(detail.picks or history.most, history.most)]
    if not shown:
        return None
    return "last_picks: " + "; ".join(_pick_text(pick, world, with_result, history.ratio) for pick in shown)


def _pick_text(pick: Pick, world: World, with_result: bool, ratio_now: float | None) -> str:
    """One pick as `last_picks` gives it."""
    text = f"{pick.id}: {pick.label}, {seconds_ago(pick.tick, world)}s ago"
    if not with_result:
        return text
    military = world.military
    change = ratio_change(pick.ratio, ratio_now)
    ratio = f" ratio {change}" if change else ""
    return (
        f"{text} -> killed {military.kills_cost - pick.kills_cost} lost {military.deaths_cost - pick.deaths_cost}"
        f"{ratio}"
    )
