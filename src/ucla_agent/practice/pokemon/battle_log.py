"""One seat's view of a battle, read from the Showdown protocol lines that seat receives.

Showdown's requests describe only the seat's own team. Everything else the
platform's doubles observation shows (which opposing Pokémon stand where,
their HP out of 100, status, boosts, the moves, items and abilities they have
revealed, the weather, the fields and both sides' conditions) is in the
battle log. `BattleLog` keeps it, for both sides, from the lines one seat may
see: its own Pokémon's HP exactly, the other side's out of 100.

An opposing Pokémon exists here only once it has stood on the board (a
`switch`, `drag` or `replace` line): the team preview's `poke` lines are
not read, so the lineup an opponent chose stays private until each Pokémon
appears, as on the platform.

Weather, fields and side conditions use poke-env's names and meanings, the
names the platform's observation is expected to carry: the weather and each
field map to the turn they started, a side condition to the turn it started
or, for Spikes and Toxic Spikes, to its layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from ucla_agent.practice.pokemon.cards import to_id

BOOST_LIMIT = 6
FAINTED = "fnt"
STACKING_CONDITIONS = frozenset({"SPIKES", "TOXIC_SPIKES"})
TERRAIN_SUFFIX = "_TERRAIN"
_EFFECT_PREFIXES = ("move: ", "ability: ", "item: ")
_FROM_TAG = "[from] "
_OF_TAG = "[of] "
_UPKEEP_TAG = "[upkeep]"
_NO_WEATHER = "none"
_POSITION_LETTERS = "ab"


def pokemon_key(reference: str) -> str:
    """`p1a: Incineroar` or `p1: Incineroar` -> `p1: Incineroar`: a Pokémon whatever board position it holds."""
    side, name = reference.split(": ", 1)
    return f"{side[:2]}: {name}"


def board_position(reference: str) -> str | None:
    """`p1a: Incineroar` -> `p1a`; None for a reference with no position (`p1: Incineroar`)."""
    side = reference.split(": ", 1)[0]
    return side if len(side) == 3 and side[2] in _POSITION_LETTERS else None


def effect_name(text: str) -> str:
    """`move: Grassy Terrain` -> `GRASSY_TERRAIN`; `RainDance` -> `RAINDANCE` (poke-env's enum names)."""
    for prefix in _EFFECT_PREFIXES:
        text = text.removeprefix(prefix)
    return text.upper().replace(" ", "_").replace("-", "_")


@dataclass
class SeenPokemon:
    """A Pokémon as the seat has seen it in the log. `item`/`ability` are None until revealed; `item` "" is none."""

    side: str
    name: str
    species: str
    hp: int = 0
    max_hp: int = 0
    status: str | None = None
    fainted: bool = False
    active: bool = False
    terastallized: bool = False
    item: str | None = None
    ability: str | None = None
    boosts: dict[str, int] = field(default_factory=dict)
    moves: list[str] = field(default_factory=list)

    def set_condition(self, condition: str) -> None:
        """Read a Showdown condition: `150/180`, `75/100 par`, `0 fnt`."""
        health, _space, status = condition.partition(" ")
        current, _slash, maximum = health.partition("/")
        self.hp = int(current)
        self.max_hp = int(maximum) if maximum else self.max_hp
        self.fainted = status == FAINTED
        self.status = None if self.fainted or not status else status

    def boost(self, stat: str, amount: int) -> None:
        self.boosts[stat] = max(-BOOST_LIMIT, min(BOOST_LIMIT, self.boosts.get(stat, 0) + amount))

    def reveal_move(self, move_name: str) -> None:
        move_id = to_id(move_name)
        if move_id not in self.moves:
            self.moves.append(move_id)


class BattleLog:
    """What one seat knows of the battle from its log lines; feed it every line, in order, with `read`."""

    def __init__(self, own_side: str) -> None:
        self.own_side = own_side
        self.turn = 0
        self.pokemon: dict[str, SeenPokemon] = {}
        self.positions: dict[str, str] = {}
        self.weather: dict[str, int] = {}
        self.fields: dict[str, int] = {}
        self.side_conditions: dict[str, dict[str, int]] = {"p1": {}, "p2": {}}
        self.winner: str | None = None
        self.ended = False
        self._handlers: dict[str, Callable[[list[str]], None]] = {
            "turn": self._turn,
            "switch": self._switch,
            "drag": self._switch,
            "replace": self._switch,
            "detailschange": self._forme,
            "-formechange": self._forme,
            "-damage": self._condition,
            "-heal": self._condition,
            "-sethp": self._condition,
            "faint": self._faint,
            "-status": self._status,
            "-curestatus": self._cure,
            "-boost": self._boost,
            "-unboost": self._unboost,
            "-setboost": self._setboost,
            "-clearboost": self._clearboost,
            "-clearallboost": self._clearallboost,
            "-clearnegativeboost": self._clearnegativeboost,
            "move": self._move,
            "-item": self._item,
            "-enditem": self._enditem,
            "-ability": self._ability,
            "-weather": self._weather,
            "-fieldstart": self._fieldstart,
            "-fieldend": self._fieldend,
            "-sidestart": self._sidestart,
            "-sideend": self._sideend,
            "-terastallize": self._terastallize,
            "swap": self._swap,
            "win": self._win,
            "tie": self._tie,
        }

    def read(self, lines: Iterable[str]) -> None:
        """Take in log lines in the order the seat received them; lines of no interest are passed over."""
        for line in lines:
            parts = line.split("|")
            handler = self._handlers.get(parts[1]) if len(parts) > 1 else None
            if handler is not None:
                handler(parts)
                self._note_effect_source(parts)

    def active(self, position: str) -> SeenPokemon | None:
        """The Pokémon standing in a board position (`p2a`), None when none has stood there."""
        key = self.positions.get(position)
        return self.pokemon[key] if key is not None else None

    def seen_on(self, side: str) -> list[SeenPokemon]:
        """Every Pokémon of a side that has stood on the board, in the order they first appeared."""
        return [seen for seen in self.pokemon.values() if seen.side == side]

    # -- the board ---------------------------------------------------------------------------------

    def _turn(self, parts: list[str]) -> None:
        self.turn = int(parts[2])

    def _switch(self, parts: list[str]) -> None:
        position, key = board_position(parts[2]), pokemon_key(parts[2])
        leaving = self.active(position)
        if leaving is not None:
            leaving.active = False
            leaving.boosts.clear()
        seen = self.pokemon.setdefault(key, SeenPokemon(position[:2], key.split(": ", 1)[1], ""))
        seen.species = parts[3].split(",")[0]
        seen.active = True
        seen.set_condition(parts[4])
        self.positions[position] = key

    def _swap(self, parts: list[str]) -> None:
        side = parts[2][:2]
        first, second = f"{side}{_POSITION_LETTERS[0]}", f"{side}{_POSITION_LETTERS[1]}"
        self.positions[first], self.positions[second] = self.positions.get(second), self.positions.get(first)
        self.positions = {position: key for position, key in self.positions.items() if key is not None}

    def _forme(self, parts: list[str]) -> None:
        self._seen(parts[2]).species = parts[3].split(",")[0]

    def _condition(self, parts: list[str]) -> None:
        self._seen(parts[2]).set_condition(parts[3])

    def _faint(self, parts: list[str]) -> None:
        seen = self._seen(parts[2])
        seen.hp, seen.fainted, seen.status = 0, True, None

    def _status(self, parts: list[str]) -> None:
        self._seen(parts[2]).status = parts[3]

    def _cure(self, parts: list[str]) -> None:
        self._seen(parts[2]).status = None

    def _terastallize(self, parts: list[str]) -> None:
        self._seen(parts[2]).terastallized = True

    def _seen(self, reference: str) -> SeenPokemon:
        return self.pokemon[pokemon_key(reference)]

    # -- boosts ------------------------------------------------------------------------------------

    def _boost(self, parts: list[str]) -> None:
        self._seen(parts[2]).boost(parts[3], int(parts[4]))

    def _unboost(self, parts: list[str]) -> None:
        self._seen(parts[2]).boost(parts[3], -int(parts[4]))

    def _setboost(self, parts: list[str]) -> None:
        self._seen(parts[2]).boosts[parts[3]] = int(parts[4])

    def _clearboost(self, parts: list[str]) -> None:
        self._seen(parts[2]).boosts.clear()

    def _clearallboost(self, _parts: list[str]) -> None:
        for seen in self.pokemon.values():
            seen.boosts.clear()

    def _clearnegativeboost(self, parts: list[str]) -> None:
        seen = self._seen(parts[2])
        seen.boosts = {stat: amount for stat, amount in seen.boosts.items() if amount > 0}

    # -- what a Pokémon reveals --------------------------------------------------------------------

    def _move(self, parts: list[str]) -> None:
        # A move another move called (Metronome, Copycat) carries a [from] tag and is not one of the user's own.
        if not any(part.startswith(_FROM_TAG) for part in parts[4:]):
            self._seen(parts[2]).reveal_move(parts[3])

    def _item(self, parts: list[str]) -> None:
        self._seen(parts[2]).item = to_id(parts[3])

    def _enditem(self, parts: list[str]) -> None:
        self._seen(parts[2]).item = ""

    def _ability(self, parts: list[str]) -> None:
        self._seen(parts[2]).ability = to_id(parts[3])

    def _note_effect_source(self, parts: list[str]) -> None:
        """`[from] item: Life Orb` or `[from] ability: Drought` reveals that item or ability of the line's source:
        the Pokémon named by `[of]`, else the line's own Pokémon."""
        owner = self._source_of(parts)
        kind, _colon, name = _tagged(parts, _FROM_TAG, "").partition(": ")
        reveal = _REVEALERS.get(kind)
        if owner is not None and reveal is not None and parts[1] != "-enditem":
            reveal(owner, to_id(name))

    def _source_of(self, parts: list[str]) -> SeenPokemon | None:
        """The Pokémon a line's `[of]` names, else the Pokémon the line is about; None when it names none."""
        subject = parts[2] if len(parts) > 2 else ""
        return self.pokemon.get(pokemon_key_or_none(_tagged(parts, _OF_TAG, subject)) or "")

    # -- the field ---------------------------------------------------------------------------------

    def _weather(self, parts: list[str]) -> None:
        if _UPKEEP_TAG in parts:
            return
        self.weather = {} if parts[2] == _NO_WEATHER else {effect_name(parts[2]): self.turn}

    def _fieldstart(self, parts: list[str]) -> None:
        name = effect_name(parts[2])
        if name.endswith(TERRAIN_SUFFIX):
            # A new terrain replaces the old one without the old one's end being sent.
            self.fields = {field: turn for field, turn in self.fields.items() if not field.endswith(TERRAIN_SUFFIX)}
        self.fields[name] = self.turn

    def _fieldend(self, parts: list[str]) -> None:
        self.fields.pop(effect_name(parts[2]), None)

    def _sidestart(self, parts: list[str]) -> None:
        conditions, name = self.side_conditions[parts[2][:2]], effect_name(parts[3])
        conditions[name] = conditions.get(name, 0) + 1 if name in STACKING_CONDITIONS else self.turn

    def _sideend(self, parts: list[str]) -> None:
        self.side_conditions[parts[2][:2]].pop(effect_name(parts[3]), None)

    def _win(self, parts: list[str]) -> None:
        self.winner, self.ended = parts[2], True

    def _tie(self, _parts: list[str]) -> None:
        self.ended = True


def _reveal_ability(seen: SeenPokemon, ability: str) -> None:
    seen.ability = ability


def _reveal_item(seen: SeenPokemon, item: str) -> None:
    # An item already known to be gone stays gone: a berry's heal is sent after the berry was eaten.
    if seen.item is None:
        seen.item = item


_REVEALERS: dict[str, Callable[[SeenPokemon, str], None]] = {"ability": _reveal_ability, "item": _reveal_item}


def _tagged(parts: list[str], tag: str, default: str) -> str:
    """The text after the first argument of a line that starts with `tag` (`[from] `, `[of] `), else `default`."""
    return next((part.removeprefix(tag) for part in parts[2:] if part.startswith(tag)), default)


def pokemon_key_or_none(reference: str) -> str | None:
    """`pokemon_key` of a reference that names a Pokémon; None for anything else."""
    return pokemon_key(reference) if ": " in reference and reference[:1] == "p" else None
