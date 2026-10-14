"""A doubles turn (pokemon.md, section 7): Showdown's move or switch request to the platform's observation and template.

`DoublesView` is built from one seat's pending Showdown request, the seat's
`BattleLog` and the card catalog. It gives the `moving` observation, the one
legal action `doubles_turn` whose template lists, for each of the seat's two
slots, its board position, its active Pokémon and its options, and it turns
a filled-in `doubles_turn` action into Showdown's choice string.

Options follow Showdown's own rules, read from the request:

- a move is offered unless the request marks it `disabled` (out of PP,
  Choice lock, Taunt, Assault Vest...); Showdown offers Struggle or Recharge
  in its place when that is all there is;
- a move's targets are the positions Showdown accepts for its target type
  (`normal` and `any`: the ally and both foes; `adjacentFoe`: both foes;
  `adjacentAlly`: the ally; `adjacentAllyOrSelf`: the ally and itself), in
  poke-env's order, the ally first, with empty or fainted positions left out
  while one remains; any other target type takes `0`, no target;
- a switch is offered to a slot that may act, into each brought Pokémon that
  is neither active nor fainted, unless the request says the slot is (or may
  be) trapped;
- `pass` is offered only to a slot with nothing to do (its Pokémon fainted
  with no replacement, Tatsugiri commanding, or no forced switch for it), and
  to a forced switch when there are fewer reserves than slots to fill, which
  the action's `notice` then explains.

A choice is never terastallized: the harness has no Tera option at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ucla_agent.practice.pokemon.battle_log import BattleLog, SeenPokemon, pokemon_key
from ucla_agent.practice.pokemon.cards import CardCatalog, to_id
from ucla_agent.practice.pokemon.preview import level_of, species_of
from ucla_agent.practice.pokemon.rules import INVALID_ACTION, RuleError

MOVING_PHASE = "moving"
TURN_TYPE = "doubles_turn"
MOVE, SWITCH, PASS = "move", "switch", "pass"
SLOTS = (0, 1)
NO_TARGET = 0
OPPONENT_PERCENT_HP = 100
BOOST_STATS = ("accuracy", "atk", "def", "evasion", "spa", "spd", "spe")
FAINTED_SUFFIX = " fnt"
TARGET_LEGEND = {
    "1": "opponent position A",
    "2": "opponent position B",
    "-1": "your position A (slot 0)",
    "-2": "your position B (slot 1)",
    "0": "no target needed (self, field or spread move)",
}
INSTRUCTIONS = (
    'Submit one choice per slot together: play_action(action={"type": "doubles_turn", "slot_0": {...}, '
    '"slot_1": {...}}). Each slot is {"type": "move", "move_id": ..., "target": <one of that option\'s targets>}, '
    '{"type": "switch", "species": ...} or {"type": "pass"} (only when offered). Two slots may not switch into the '
    "same Pokémon, and both slots may not pass."
)


def move_targets(slot: int, target_type: str | None, occupied: set[int]) -> list[int]:
    """The positions a move of this target type may be aimed at from `slot` (see the module note); `[0]` for none."""
    me, ally = -(slot + 1), -(2 - slot)
    choosable = {
        "normal": [ally, 1, 2],
        "any": [ally, 1, 2],
        "adjacentFoe": [1, 2],
        "adjacentAlly": [ally],
        "adjacentAllyOrSelf": [ally, me],
    }.get(target_type or "")
    if choosable is None:
        return [NO_TARGET]
    standing = [position for position in choosable if position in occupied]
    return standing or choosable


@dataclass(frozen=True)
class Option:
    """One option of a slot: what the template shows, and the Showdown choice it stands for."""

    shown: Mapping[str, Any]
    showdown: str
    targets: tuple[int, ...] = ()


def _fainted(pokemon: Mapping[str, Any]) -> bool:
    return pokemon["condition"].endswith(FAINTED_SUFFIX)


def _hp(condition: str) -> tuple[int, int]:
    health = condition.split(" ")[0]
    current, _slash, maximum = health.partition("/")
    return int(current), int(maximum) if maximum else 0


class DoublesView:
    """One seat's pending doubles decision: its observation, its legal action and the reading of its answer."""

    def __init__(
        self, request: Mapping[str, Any], log: BattleLog, catalog: CardCatalog, side: str, battle_format: str
    ) -> None:
        self.request = request
        self.log = log
        self.catalog = catalog
        self.side = side
        self.foe = "p2" if side == "p1" else "p1"
        self.battle_format = battle_format
        self.team = list(request["side"]["pokemon"])
        self.force_switch = [bool(flag) for flag in request.get("forceSwitch", [False, False])]
        self.slot_options = [self._options(slot) for slot in SLOTS]

    # -- the observation ---------------------------------------------------------------------------

    def observation(self) -> dict[str, Any]:
        """The seat's `moving` observation (pokemon.md, section 7)."""
        return {
            "phase": MOVING_PHASE,
            "battle_format": self.battle_format,
            "format": self.battle_format,
            "is_doubles": True,
            "waiting_for_action": True,
            "finished": False,
            "turn": self.log.turn,
            "weather": dict(self.log.weather),
            "fields": dict(self.log.fields),
            "active_pokemon": [self._own_summary(self.team[slot]) for slot in SLOTS],
            "opponent_active_pokemon": [self._foe_summary(self.log.active(f"{self.foe}{letter}")) for letter in "ab"],
            "team": {pokemon["ident"]: self._own_summary(pokemon) for pokemon in self.team},
            "opponent_team": {
                f"{seen.side}: {seen.name}": self._foe_summary(seen) for seen in self.log.seen_on(self.foe)
            },
            "available_moves": [self._move_entries(slot) for slot in SLOTS],
            "available_switches": [
                [self._own_summary(self.team[index]) for index in self._switch_indexes(slot)] for slot in SLOTS
            ],
            "force_switch": list(self.force_switch),
            "side_conditions": dict(self.log.side_conditions[self.side]),
            "opponent_side_conditions": dict(self.log.side_conditions[self.foe]),
            "can_tera": [bool(self._active_entry(slot).get("canTerastallize")) for slot in SLOTS],
        }

    def _own_summary(self, pokemon: Mapping[str, Any]) -> dict[str, Any]:
        species = species_of(pokemon["details"])
        current, maximum = _hp(pokemon["condition"])
        seen = self.log.pokemon.get(pokemon_key(pokemon["ident"]))
        status = pokemon["condition"].split(" ")[1] if " " in pokemon["condition"] else None
        return {
            **self._species_facts(species),
            "level": level_of(pokemon["details"]),
            "current_hp": current,
            "max_hp": maximum,
            "current_hp_fraction": current / maximum if maximum else 0.0,
            "status": None if status in (None, "fnt") else status.upper(),
            "stats": dict(pokemon["stats"]),
            "item": pokemon["item"],
            "ability": pokemon["ability"],
            "moves": list(pokemon["moves"]),
            "boosts": _boosts(seen),
            "fainted": _fainted(pokemon),
            "active": bool(pokemon["active"]),
            "revealed": seen is not None,
            "is_terastallized": bool(pokemon.get("terastallized")),
        }

    def _foe_summary(self, seen: SeenPokemon | None) -> dict[str, Any] | None:
        if seen is None:
            return None
        return {
            **self._species_facts(seen.species),
            "level": None,
            "current_hp": seen.hp,
            "max_hp": OPPONENT_PERCENT_HP,
            "current_hp_fraction": seen.hp / OPPONENT_PERCENT_HP,
            "status": seen.status.upper() if seen.status else None,
            "stats": None,
            "item": seen.item,
            "ability": seen.ability,
            "moves": list(seen.moves),
            "boosts": _boosts(seen),
            "fainted": seen.fainted,
            "active": seen.active,
            "revealed": True,
            "is_terastallized": seen.terastallized,
        }

    def _species_facts(self, species: str) -> dict[str, Any]:
        facts = self.catalog.species[to_id(species)]
        return {
            "species": to_id(species),
            "name": species,
            "types": list(facts.types),
            "base_stats": dict(facts.base_stats),
        }

    def _move_entries(self, slot: int) -> list[dict[str, Any]]:
        return [dict(option.shown["details"]) for option in self.slot_options[slot] if option.shown["type"] == MOVE]

    # -- the options -------------------------------------------------------------------------------

    def _active_entry(self, slot: int) -> Mapping[str, Any]:
        active = self.request.get("active") or []
        return active[slot] if slot < len(active) else {}

    def _can_act(self, slot: int) -> bool:
        """Whether the slot makes a real choice in a move request: its Pokémon is standing and not commanding."""
        pokemon = self.team[slot]
        return "active" in self.request and not _fainted(pokemon) and not pokemon.get("commanding")

    def _switch_indexes(self, slot: int) -> list[int]:
        """The team positions the slot may switch into: brought Pokémon neither active nor fainted."""
        entry = self._active_entry(slot)
        may_switch = self.force_switch[slot] or (
            self._can_act(slot) and not entry.get("trapped") and not entry.get("maybeTrapped")
        )
        bench = [index for index in range(len(SLOTS), len(self.team)) if not _fainted(self.team[index])]
        return bench if may_switch else []

    def _forced_passes_allowed(self) -> bool:
        """A forced switch with fewer reserves than slots to fill lets the extra slots pass."""
        reserves = [index for index in range(len(SLOTS), len(self.team)) if not _fainted(self.team[index])]
        return len(reserves) < sum(self.force_switch)

    def _options(self, slot: int) -> list[Option]:
        moves = self._move_options(slot) if self._can_act(slot) else []
        switches = [self._switch_option(index) for index in self._switch_indexes(slot)]
        passes = [_pass_option()] if self._passes(slot, moves, switches) else []
        return moves + switches + passes

    def _passes(self, slot: int, moves: list[Option], switches: list[Option]) -> bool:
        if self.force_switch[slot]:
            return self._forced_passes_allowed()
        return not moves and not switches

    def _move_options(self, slot: int) -> list[Option]:
        occupied = self._occupied_positions()
        return [
            self._move_option(number, move, slot, occupied)
            for number, move in enumerate(self._active_entry(slot).get("moves", []), start=1)
            if not move.get("disabled")
        ]

    def _move_option(self, number: int, move: Mapping[str, Any], slot: int, occupied: set[int]) -> Option:
        facts = self.catalog.moves[move["id"]]
        target_type = move.get("target")
        targets = move_targets(slot, target_type, occupied)
        details = {
            "id": move["id"],
            "name": move["move"],
            "type": facts.type,
            "category": facts.category,
            "base_power": facts.base_power,
            "accuracy": facts.accuracy,
            "priority": facts.priority,
            "current_pp": move.get("pp"),
            "max_pp": move.get("maxpp"),
            "target": target_type,
            "targets": targets,
        }
        shown = {"type": MOVE, "move_id": move["id"], "name": move["move"], "targets": targets, "details": details}
        showdown = f"move {number}" if targets == [NO_TARGET] else f"move {number} {{target}}"
        return Option(shown, showdown, tuple(targets))

    def _switch_option(self, index: int) -> Option:
        species = species_of(self.team[index]["details"])
        return Option({"type": SWITCH, "species": to_id(species), "name": species}, f"switch {index + 1}")

    def _occupied_positions(self) -> set[int]:
        own = {-(slot + 1) for slot in SLOTS if not _fainted(self.team[slot])}
        foes = {
            position
            for position, letter in ((1, "a"), (2, "b"))
            if (seen := self.log.active(f"{self.foe}{letter}")) is not None and not seen.fainted
        }
        return own | foes

    # -- the legal action and the answer -----------------------------------------------------------

    def legal_action(self, session_id: str, state_version: int) -> dict[str, Any]:
        """The single `doubles_turn` legal action, whose `input.action` is the template to fill in."""
        template: dict[str, Any] = {
            "type": TURN_TYPE,
            "instructions": INSTRUCTIONS,
            "slots": [self._slot_template(slot) for slot in SLOTS],
            "target_legend": dict(TARGET_LEGEND),
        }
        if self.force_switch != [False, False] and self._forced_passes_allowed():
            template["notice"] = (
                "Fewer reserves than slots to fill: switch the reserve in with one slot and pass with the other."
            )
        return {
            "action_id": TURN_TYPE,
            "label": "Choose one action for each of your two slots",
            "input": {
                "session_id": session_id,
                "action_id": TURN_TYPE,
                "state_version": state_version,
                "action": template,
            },
        }

    def _slot_template(self, slot: int) -> dict[str, Any]:
        pokemon = self.team[slot]
        return {
            "slot": slot,
            "board_position": -(slot + 1),
            "active": {"species": to_id(species_of(pokemon["details"])), "name": species_of(pokemon["details"])},
            "options": [
                {key: value for key, value in option.shown.items() if key != "details"}
                for option in self.slot_options[slot]
            ],
        }

    def choice(self, action: Any) -> str:
        """Showdown's choice string for a filled-in `doubles_turn` action; INVALID_ACTION for one the rules refuse."""
        if not isinstance(action, Mapping) or action.get("type") != TURN_TYPE:
            raise RuleError(INVALID_ACTION, f"a doubles turn needs a structured {TURN_TYPE} action, not {action!r}")
        picked = [self._slot_choice(slot, action.get(f"slot_{slot}")) for slot in SLOTS]
        _check_pair(picked)
        return ", ".join(text for text, _kind, _species in picked)

    def _slot_choice(self, slot: int, choice: Any) -> tuple[str, str, str | None]:
        if not isinstance(choice, Mapping) or choice.get("type") not in (MOVE, SWITCH, PASS):
            raise RuleError(INVALID_ACTION, f"slot_{slot} must be a move, switch or pass, not {choice!r}")
        reader = {MOVE: self._read_move, SWITCH: self._read_switch, PASS: self._read_pass}[choice["type"]]
        return reader(slot, choice)

    def _read_move(self, slot: int, choice: Mapping[str, Any]) -> tuple[str, str, str | None]:
        move_id = to_id(str(choice.get("move_id", "")))
        option = _find(self.slot_options[slot], MOVE, "move_id", move_id, f"slot_{slot} has no move {move_id!r}")
        target = choice.get("target", NO_TARGET)
        if isinstance(target, bool) or target not in option.targets:
            raise RuleError(INVALID_ACTION, f"slot_{slot}'s {move_id} takes a target in {list(option.targets)}")
        return option.showdown.format(target=target), MOVE, None

    def _read_switch(self, slot: int, choice: Mapping[str, Any]) -> tuple[str, str, str | None]:
        species = to_id(str(choice.get("species", "")))
        option = _find(self.slot_options[slot], SWITCH, "species", species, f"slot_{slot} cannot switch to {species!r}")
        return option.showdown, SWITCH, species

    def _read_pass(self, slot: int, _choice: Mapping[str, Any]) -> tuple[str, str, str | None]:
        _find(self.slot_options[slot], PASS, "type", PASS, f"slot_{slot} has something to do: pass is not offered")
        return PASS, PASS, None


def _pass_option() -> Option:
    return Option({"type": PASS}, PASS)


def _boosts(seen: SeenPokemon | None) -> dict[str, int]:
    boosts = seen.boosts if seen is not None else {}
    return {stat: boosts.get(stat, 0) for stat in BOOST_STATS}


def _find(options: Sequence[Option], kind: str, key: str, value: str, refusal: str) -> Option:
    found = next((option for option in options if option.shown["type"] == kind and option.shown[key] == value), None)
    if found is None:
        raise RuleError(INVALID_ACTION, refusal)
    return found


def _check_pair(picked: list[tuple[str, str, str | None]]) -> None:
    """Both slots may not pass, and two slots may not switch into the same Pokémon."""
    kinds = [kind for _text, kind, _species in picked]
    if kinds == [PASS, PASS]:
        raise RuleError(INVALID_ACTION, "both slots cannot pass")
    switched = [species for _text, kind, species in picked if kind == SWITCH]
    if len(switched) == len(SLOTS) and switched[0] == switched[1]:
        raise RuleError(INVALID_ACTION, f"both slots cannot switch into {switched[0]}")
