"""A doubles turn as the agent reads it (pokemon.md, section 7): the board, and what is remembered between turns.

`read_board` reads the `moving` observation and the `doubles_turn` template
into a `Board`: our two active Pokémon by board position (A is slot 0's,
target -1; B is slot 1's, target -2), the opponent's by position (A is target
1, B is target 2), our reserves, the weather, terrain, Trick Room, both
sides' conditions, which slots must switch, and each slot's options as the
template lists them. Each Pokémon carries its set: ours and the opponent's
from the draft memory by species, else from the facts its entry shows. The
board also keeps what `opponent_team` shows of every opponent Pokémon seen so
far, for the model's briefing; the policy does not read it.

Every key the guide names is read through the decision's `Reader`. When a
key is missing or of another kind, its default is:

- `is_doubles`: true, the template being a doubles turn (false is a singles
  board, which is no state of this game: `UnexpectedState`);
- `turn`: 0; `weather`, `fields`, `side_conditions`,
  `opponent_side_conditions`: none;
- `active_pokemon`: the species each template slot names as `active`, at full
  HP; `opponent_active_pokemon`: the `opponent_team` entries marked `active`;
- `team`, `opponent_team`: none; `available_switches`: the reserves of `team`,
  and a switch option's species at full HP; `available_moves`: the data's
  move facts only;
- the opponent's Pokémon seen so far: those `opponent_team` lists, each key
  of them read with its default above but none of them counted as degraded,
  since only the briefing reads them;
- `force_switch`: a slot must switch when the template offers it switches and
  no move;
- a Pokémon's `species`: the Pokémon is unknown and left off the board;
  `current_hp_fraction`: `current_hp` over `max_hp`, else full HP; `status`:
  none; `boosts`: none; `fainted`: whether no HP is left; `active`: true for
  an active Pokémon, false for any other;
- the template's `options`: none; an option's `targets`: `[0]`, no target;
  `board_position`: `-(slot + 1)`. A template that does not list two slots,
  numbered 0 and 1, cannot be read: `UnexpectedState`.

`BattleTracker` is what one match remembers between turns: which species
has stood in each slot since which turn, the move each slot's Pokémon used
last, and how many turns in a row it has used Protect (or a move like it).
"First turn out" (Fake Out works) and the Protect chain (each further Protect
in a row works a third as often) are read from it. The chain counts the
Protects we chose, a failed one too: the tracker sees our choices, not the
battle log, and a failed Protect resets Showdown's own count, so the error
only ever overstates the odds against a further Protect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ucla_agent.games.pokemon import data
from ucla_agent.games.pokemon.draft import OURS, THEIRS
from ucla_agent.games.pokemon.observe import Reader, UnexpectedState
from ucla_agent.games.pokemon.sets import PROTECT_MOVES, PokemonSet, from_facts

MOVE, SWITCH, PASS = "move", "switch", "pass"
SLOT_INDEXES = (0, 1)
NO_TARGET = 0
# Target numbers of the opponent's positions A and B, and of ours.
FOE_TARGETS = (1, 2)
OUR_TARGETS = (-1, -2)
TERRAIN_SUFFIX = "terrain"
TRICK_ROOM_FIELD = "trickroom"
FULL_HP = 1.0

SetLookup = Callable[[str, str], "PokemonSet | None"]


@dataclass(frozen=True)
class Fighter:
    """One Pokémon on the board or in reserve: its species id, set, share of HP, status, stages and state."""

    species: str
    set: PokemonSet | None
    hp_fraction: float
    status: str | None
    boosts: Mapping[str, int]
    fainted: bool
    active: bool

    @property
    def standing(self) -> bool:
        """Whether it can still act and be hit: not fainted, with HP left."""
        return not self.fainted and self.hp_fraction > 0

    def battle_mon(self):
        """Its set as the damage estimate takes it, at its HP, status and stages; it must have a set."""
        return self.set.battle_mon(self.hp_fraction, self.status, self.boosts)


@dataclass(frozen=True)
class Option:
    """One option a slot's template lists: a move with the targets it may take, a switch into a species, or pass."""

    kind: str
    move_id: str = ""
    species: str = ""
    targets: tuple[int, ...] = ()


@dataclass(frozen=True)
class Slot:
    """One of our two slots as the template lists it: its number, board position, options, and the species id of
    the Pokémon its `active` entry names (`""` when it names none)."""

    index: int
    position: int
    options: tuple[Option, ...]
    active_species: str = ""


@dataclass(frozen=True)
class Board:
    """One doubles turn as the agent reads it (see the module note)."""

    turn: int
    ours: tuple[Fighter | None, Fighter | None]
    theirs: tuple[Fighter | None, Fighter | None]
    reserves: Mapping[str, Fighter]
    weather: str | None
    terrain: str | None
    trick_room: bool
    our_conditions: frozenset[str]
    their_conditions: frozenset[str]
    force_switch: tuple[bool, bool]
    slots: tuple[Slot, Slot]
    move_facts: Mapping[str, data.Move] = field(default_factory=dict)
    their_seen: Mapping[str, Fighter] = field(default_factory=dict)

    def own(self, slot: Slot) -> Fighter | None:
        """The Pokémon standing in the slot's board position (None for an empty one)."""
        return self.at(slot.position)

    def at(self, target: int) -> Fighter | None:
        """The Pokémon a target number names: 1 and 2 the opponent's positions A and B, -1 and -2 ours."""
        if target in FOE_TARGETS:
            return self.theirs[FOE_TARGETS.index(target)]
        return self.ours[OUR_TARGETS.index(target)] if target in OUR_TARGETS else None

    def move(self, move_id: str) -> data.Move | None:
        """A move's record: the data's, else the facts `available_moves` gave for it."""
        return data.move(move_id) or self.move_facts.get(move_id)


def read_board(observation: Any, template: Any, lookup: SetLookup, reader: Reader) -> Board:
    """The board of one doubles turn (see the module note). `lookup(side, species)` gives a drafted set or None."""
    if not isinstance(observation, Mapping):
        raise UnexpectedState(f"a doubles observation is an object, not {type(observation).__name__}")
    if not reader.value(observation, "is_doubles", bool, True):
        raise UnexpectedState("is_doubles is false: a singles board is no state of pokemon_vgc_doubles_draft")
    slots = _slots(template, reader)
    fields = _field_names(observation, reader)
    return Board(
        turn=reader.value(observation, "turn", int, 0),
        ours=_our_actives(observation, slots, lookup, reader),
        theirs=_their_actives(observation, lookup, reader),
        reserves=_reserves(observation, slots, lookup, reader),
        weather=_weather(observation, reader),
        terrain=_terrain(fields),
        trick_room=TRICK_ROOM_FIELD in fields,
        our_conditions=_conditions(observation, "side_conditions", reader),
        their_conditions=_conditions(observation, "opponent_side_conditions", reader),
        force_switch=_force_switch(observation, slots, reader),
        slots=slots,
        move_facts=_move_facts(observation, reader),
        their_seen=_their_seen(observation, lookup),
    )


# -- the template ----------------------------------------------------------------------------------


def _slots(template: Any, reader: Reader) -> tuple[Slot, Slot]:
    listed = reader.value(template, "slots", list, [])
    slots = sorted((_slot(entry, order, reader) for order, entry in enumerate(listed)), key=lambda slot: slot.index)
    if tuple(slot.index for slot in slots) != SLOT_INDEXES:
        raise UnexpectedState(f"the doubles_turn template lists slots {[slot.index for slot in slots]}, not 0 and 1")
    return slots[0], slots[1]


def _slot(entry: Any, order: int, reader: Reader) -> Slot:
    index = reader.value(entry, "slot", int, order)
    position = reader.value(entry, "board_position", int, -(index + 1))
    options = [_option(option, reader) for option in reader.value(entry, "options", list, [])]
    active = entry.get("active") if isinstance(entry, Mapping) else None
    species = active.get("species") if isinstance(active, Mapping) else None
    kept = tuple(option for option in options if option is not None)
    return Slot(index, position, kept, data.to_id(species) if isinstance(species, str) else "")


def _option(option: Any, reader: Reader) -> Option | None:
    """One template option; None (and `options` counts as degraded) for one that is not a move, switch or pass."""
    kind = option.get("type") if isinstance(option, Mapping) else None
    made = _OPTION_READERS[kind](option, reader) if kind in _OPTION_READERS else None
    if made is None:
        reader.note("options")
    return made


def _move_option(option: Mapping[str, Any], reader: Reader) -> Option | None:
    if not isinstance(option.get("move_id"), str):
        return None
    targets = reader.value(option, "targets", list, [NO_TARGET])
    return Option(MOVE, move_id=option["move_id"], targets=_whole_numbers(targets, reader))


def _switch_option(option: Mapping[str, Any], _reader: Reader) -> Option | None:
    return Option(SWITCH, species=option["species"]) if isinstance(option.get("species"), str) else None


def _pass_option(_option: Mapping[str, Any], _reader: Reader) -> Option:
    return Option(PASS)


_OPTION_READERS: dict[str, Callable[[Mapping[str, Any], Reader], "Option | None"]] = {
    MOVE: _move_option,
    SWITCH: _switch_option,
    PASS: _pass_option,
}


def _whole_numbers(targets: Sequence[Any], reader: Reader) -> tuple[int, ...]:
    kept = tuple(target for target in targets if isinstance(target, int) and not isinstance(target, bool))
    if len(kept) != len(targets) or not kept:
        reader.note("targets")
    return kept or (NO_TARGET,)


# -- the Pokémon -----------------------------------------------------------------------------------


def _fighter(entry: Any, side: str, lookup: SetLookup, reader: Reader, active: bool) -> Fighter | None:
    """One Pokémon summary as a fighter; None for an entry that names no species (an empty position)."""
    if entry is None:
        return None
    species = reader.value(entry, "species", str, None)
    if species is None:
        return None
    species = data.to_id(species)
    fraction = _hp_fraction(entry, reader)
    return Fighter(
        species=species,
        set=lookup(side, species) or _facts(entry, species),
        hp_fraction=fraction,
        status=_status(entry, reader),
        boosts=_boosts(entry, reader),
        fainted=reader.value(entry, "fainted", bool, fraction <= 0),
        active=reader.value(entry, "active", bool, active),
    )


def _facts(entry: Mapping[str, Any], species: str) -> PokemonSet | None:
    return from_facts(
        species,
        item=entry.get("item"),
        ability=entry.get("ability"),
        moves=entry.get("moves"),
        level=entry.get("level"),
    )


def _hp_fraction(entry: Any, reader: Reader) -> float:
    """The share of HP left: `current_hp_fraction`, else `current_hp` over `max_hp`, else full; within 0 to 1."""
    current = reader.value(entry, "current_hp", (int, float), None)
    maximum = reader.value(entry, "max_hp", (int, float), None)
    by_count = current / maximum if current is not None and maximum else FULL_HP
    return min(FULL_HP, max(0.0, float(reader.value(entry, "current_hp_fraction", (int, float), by_count))))


def _status(entry: Any, reader: Reader) -> str | None:
    """The status as a Showdown id (`BRN` -> `brn`); None for none."""
    status = reader.value(entry, "status", (str, type(None)), None)
    return status.lower() if status else None


def _boosts(entry: Any, reader: Reader) -> dict[str, int]:
    boosts = reader.value(entry, "boosts", dict, {})
    return {stat: stage for stat, stage in boosts.items() if isinstance(stage, int) and not isinstance(stage, bool)}


def _pair(entries: Sequence[Fighter | None]) -> tuple[Fighter | None, Fighter | None]:
    padded = [*entries, None, None]
    return padded[0], padded[1]


def _our_actives(
    observation: Mapping[str, Any], slots: Sequence[Slot], lookup: SetLookup, reader: Reader
) -> tuple[Fighter | None, Fighter | None]:
    entries = reader.value(observation, "active_pokemon", list, None)
    if entries is None:
        return _pair([_template_fighter(slot, lookup) for slot in slots])
    return _pair([_fighter(entry, OURS, lookup, reader, active=True) for entry in entries])


def _their_seen(observation: Mapping[str, Any], lookup: SetLookup) -> dict[str, Fighter]:
    """The opponent's Pokémon `opponent_team` lists, by species. Only the model's briefing reads them, so they are
    read with a reader of their own: a key they lack is no parse degradation of the decision the policy makes."""
    briefing_only = Reader()
    team = briefing_only.value(observation, "opponent_team", dict, {})
    fighters = [_fighter(entry, THEIRS, lookup, briefing_only, active=False) for entry in team.values()]
    return {fighter.species: fighter for fighter in fighters if fighter is not None}


def _template_fighter(slot: Slot, lookup: SetLookup) -> Fighter | None:
    """A slot's Pokémon when the observation lists no actives: the species its template entry names, at full HP."""
    return _full_hp(slot.active_species, lookup, active=True) if slot.active_species else None


def _their_actives(
    observation: Mapping[str, Any], lookup: SetLookup, reader: Reader
) -> tuple[Fighter | None, Fighter | None]:
    entries = reader.value(observation, "opponent_active_pokemon", list, None)
    if entries is None:
        team = reader.value(observation, "opponent_team", dict, {})
        entries = [entry for entry in team.values() if isinstance(entry, Mapping) and entry.get("active") is True]
    return _pair([_fighter(entry, THEIRS, lookup, reader, active=True) for entry in entries])


def _reserves(
    observation: Mapping[str, Any], slots: Sequence[Slot], lookup: SetLookup, reader: Reader
) -> dict[str, Fighter]:
    """Our Pokémon a slot may switch into, by species: `available_switches`, else `team`, else full HP."""
    team = reader.value(observation, "team", dict, {})
    listed = _per_slot(reader.value(observation, "available_switches", list, []))
    fighters = [_fighter(entry, OURS, lookup, reader, active=False) for entry in [*team.values(), *listed]]
    found = {fighter.species: fighter for fighter in fighters if fighter is not None and not fighter.active}
    for species in _switch_species(slots):
        found.setdefault(species, _full_hp(species, lookup, active=False))
    return found


def _per_slot(lists: Sequence[Any]) -> list[Any]:
    """The entries of a per-slot list of lists (`available_moves`, `available_switches`), both slots together."""
    return [entry for listed in lists if isinstance(listed, list) for entry in listed]


def _switch_species(slots: Sequence[Slot]) -> list[str]:
    return [data.to_id(option.species) for slot in slots for option in slot.options if option.kind == SWITCH]


def _full_hp(species: str, lookup: SetLookup, active: bool) -> Fighter:
    """One of ours the observation shows nothing of: its drafted set at full HP."""
    return Fighter(species, lookup(OURS, species) or from_facts(species), FULL_HP, None, {}, False, active)


# -- the field -------------------------------------------------------------------------------------


def _names(value: Any) -> list[str]:
    """Effect names as Showdown ids, from an object keyed by name (`{"RAINDANCE": 3}`), a list or one name."""
    listed = list(value) if isinstance(value, (Mapping, list)) else [value]
    return [data.to_id(name) for name in listed if isinstance(name, str)]


def _weather(observation: Mapping[str, Any], reader: Reader) -> str | None:
    names = _names(reader.value(observation, "weather", (dict, list, str, type(None)), None))
    return names[0] if names else None


def _field_names(observation: Mapping[str, Any], reader: Reader) -> list[str]:
    return _names(reader.value(observation, "fields", (dict, list), {}))


def _terrain(fields: Sequence[str]) -> str | None:
    return next((name for name in fields if name.endswith(TERRAIN_SUFFIX)), None)


def _conditions(observation: Mapping[str, Any], key: str, reader: Reader) -> frozenset[str]:
    return frozenset(_names(reader.value(observation, key, (dict, list), {})))


def _force_switch(observation: Mapping[str, Any], slots: Sequence[Slot], reader: Reader) -> tuple[bool, bool]:
    flags = reader.value(observation, "force_switch", list, None)
    if isinstance(flags, list) and len(flags) == len(SLOT_INDEXES) and all(isinstance(flag, bool) for flag in flags):
        return flags[0], flags[1]
    if flags is not None:
        reader.note("force_switch")
    forced = [_only_switches(slot) for slot in slots]
    return forced[0], forced[1]


def _only_switches(slot: Slot) -> bool:
    kinds = {option.kind for option in slot.options}
    return SWITCH in kinds and MOVE not in kinds


def _move_facts(observation: Mapping[str, Any], reader: Reader) -> dict[str, data.Move]:
    """The facts `available_moves` gives of moves the data does not know, by id."""
    listed = _per_slot(reader.value(observation, "available_moves", list, []))
    moves = [_move_from_details(details) for details in listed]
    return {move.id: move for move in moves if move is not None and data.move(move.id) is None}


def _move_from_details(details: Any) -> data.Move | None:
    """A move record from an `available_moves` entry (`id`, `type`, `category`, `base_power`, `accuracy` as a
    fraction, `priority`, `target`); None for an entry that lacks one of them."""
    try:
        return data.Move(
            id=data.to_id(details["id"]),
            name=str(details.get("name", details["id"])),
            type=str(details["type"]).capitalize(),
            category=str(details["category"]).capitalize(),
            base_power=int(details["base_power"]),
            accuracy=round(float(details["accuracy"]) * 100),
            priority=int(details["priority"]),
            target=str(details["target"]),
            flags=frozenset(),
            multihit=None,
            drain=None,
            recoil=None,
            stat_changes=(),
            has_secondary=False,
            will_crit=False,
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


# -- what one match remembers ----------------------------------------------------------------------


@dataclass(frozen=True)
class _Arrival:
    species: str
    turn: int


@dataclass(frozen=True)
class _LastMove:
    """A slot's last move, by which species on which turn, and the Protects in a row it ends (0 for another move)."""

    species: str
    turn: int
    move_id: str
    protect_chain: int = 0


class BattleTracker:
    """What one match remembers between doubles turns (see the module note); feed it every board, in order."""

    def __init__(self) -> None:
        self._arrivals: dict[int, _Arrival] = {}
        self._moves: dict[int, _LastMove] = {}

    def observe(self, board: Board) -> None:
        """Take in a board: a species new to a slot arrived on this board's turn."""
        for slot in board.slots:
            fighter = board.own(slot)
            known = self._arrivals.get(slot.index)
            if fighter is not None and (known is None or known.species != fighter.species):
                self._arrivals[slot.index] = _Arrival(fighter.species, board.turn)

    def first_turn_out(self, board: Board, slot: Slot) -> bool:
        """Whether the slot's Pokémon came in for this turn (Fake Out works only then)."""
        fighter, arrival = board.own(slot), self._arrivals.get(slot.index)
        return fighter is not None and arrival == _Arrival(fighter.species, board.turn)

    def note_choice(self, board: Board, action: Mapping[str, Any]) -> None:
        """Remember what each slot chose on this board: a move by the Pokémon that used it; a switch forgets it."""
        for slot in board.slots:
            self._note_slot(slot.index, board.own(slot), board.turn, action.get(f"slot_{slot.index}") or {})

    def _note_slot(self, index: int, fighter: Fighter | None, turn: int, choice: Mapping[str, Any]) -> None:
        if choice.get("type") == MOVE and fighter is not None:
            move_id = str(choice.get("move_id"))
            before = self._record(index, fighter.species, turn)
            chain = (before.protect_chain if before is not None else 0) + 1 if move_id in PROTECT_MOVES else 0
            self._moves[index] = _LastMove(fighter.species, turn, move_id, chain)
        elif choice.get("type") == SWITCH:
            self._moves.pop(index, None)

    def _record(self, index: int, species: str, turn: int) -> _LastMove | None:
        """Slot `index`'s last move when `species` used it on the turn before `turn`; else None."""
        record = self._moves.get(index)
        return record if record is not None and (record.species, record.turn) == (species, turn - 1) else None

    def _previous(self, board: Board, slot: Slot) -> _LastMove | None:
        """The slot's last move when the Pokémon now in it used it on the turn before this board's; else None."""
        fighter = board.own(slot)
        return self._record(slot.index, fighter.species, board.turn) if fighter is not None else None

    def last_move(self, board: Board, slot: Slot) -> str | None:
        """The move the slot's Pokémon used on the turn before this board's; None when it used none then."""
        record = self._previous(board, slot)
        return record.move_id if record is not None else None

    def protect_chain(self, board: Board, slot: Slot) -> int:
        """How many turns in a row, up to the one before this board's, the slot's Pokémon used Protect (or a move
        like it); 0 when it used another move then, has just come in, or used none."""
        record = self._previous(board, slot)
        return record.protect_chain if record is not None else 0
