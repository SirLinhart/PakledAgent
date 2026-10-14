"""A fake battle helper for the default tests: `tools/pokemon_sim/sim.js`'s JSON-lines protocol over a made-up battle.

    python tests/stubs/fake_pokemon_sim.py

It reads `start` and `choose` commands on stdin and answers each with one
line, exactly as `sim.js` does, with requests and log lines in Showdown's
shapes: team preview (`team 1234`, the first two lead), move requests with
each move's Showdown target, forced switches (with forced passes when fewer
reserves than fainted slots), `wait` requests, `|error|[Invalid choice] ...`
for a choice Showdown would refuse, and `|win|` at the end. A side sees its
own Pokémon's HP exactly and the other side's out of 100.

The battle itself is made up and has no randomness: switches go first, then
the four active Pokémon move in board order (p1a, p1b, p2a, p2b); a move at
a foe, or a spread move, takes 30% of each target's maximum HP whatever the
move is, and every active Pokémon loses 10% more at the end of each turn, so
a battle always ends. Move targets come from the move facts of the tracked
card catalog.
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SIDES = ("p1", "p2")
SLOTS = (0, 1)
LETTERS = "ab"
PICKED_TEAM_SIZE = 4
HIT_SHARE = 0.3
CHIP_SHARE = 0.1
MAX_TURNS = 300
PP = 8
BASE_HP = 120
HP_PER_LETTER = 3
PERCENT = 100
CATALOG = Path(__file__).resolve().parents[2] / "config" / "pokemon" / "practice_cards.json"
SPREAD_TARGETS = frozenset({"allAdjacentFoes", "allAdjacent"})
STATS = {"atk": 100, "def": 100, "spa": 100, "spd": 100, "spe": 100}
_NOT_ID = re.compile(r"[^a-z0-9]+")


def to_id(name: str) -> str:
    return _NOT_ID.sub("", name.lower())


MOVE_TARGETS = {move_id: facts["target"] for move_id, facts in json.loads(CATALOG.read_text("utf-8"))["moves"].items()}


def allowed_targets(slot: int, target_type: str) -> set[int]:
    """The target positions Showdown accepts in doubles for a choosable target type; empty for the others."""
    me, ally = -(slot + 1), -(2 - slot)
    table = {
        "normal": {1, 2, ally},
        "any": {1, 2, ally},
        "adjacentFoe": {1, 2},
        "adjacentAlly": {ally},
        "adjacentAllyOrSelf": {ally, me},
    }
    return table.get(target_type, set())


class InvalidChoice(Exception):
    """A choice Showdown would refuse; its text follows `[Invalid choice]`."""


@dataclass(eq=False)
class Mon:
    side: str
    species: str
    item: str
    ability: str
    moves: list[str]
    max_hp: int
    hp: int

    @property
    def fainted(self) -> bool:
        return self.hp <= 0

    def condition(self, viewer: str) -> str:
        """The HP as `viewer` sees it: exact for its own Pokémon, out of 100 for the other side's."""
        if self.fainted:
            return "0 fnt"
        if viewer == self.side:
            return f"{self.hp}/{self.max_hp}"
        return f"{math.ceil(self.hp * PERCENT / self.max_hp)}/{PERCENT}"

    def request_entry(self, active: bool) -> dict[str, Any]:
        return {
            "ident": f"{self.side}: {self.species}",
            "details": f"{self.species}, L50",
            "condition": self.condition(self.side),
            "active": active,
            "stats": dict(STATS),
            "moves": [to_id(move) for move in self.moves],
            "baseAbility": to_id(self.ability),
            "item": to_id(self.item),
            "pokeball": "pokeball",
            "ability": to_id(self.ability),
            "commanding": False,
            "reviving": False,
            "teraType": "Normal",
            "terastallized": "",
        }

    def move_entries(self) -> list[dict[str, Any]]:
        return [
            {
                "move": move,
                "id": to_id(move),
                "pp": PP,
                "maxpp": PP,
                "target": MOVE_TARGETS[to_id(move)],
                "disabled": False,
            }
            for move in self.moves
        ]


@dataclass
class Side:
    side_id: str
    name: str
    team: list[Mon]
    request: dict[str, Any] | None = None
    choice: list[tuple[str, int, int]] | None = None
    flags: list[bool] = field(default_factory=lambda: [False, False])

    def bench(self) -> list[int]:
        """Team positions (0-based) of the Pokémon that could switch in."""
        return [index for index in range(2, len(self.team)) if not self.team[index].fainted]

    def side_data(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "id": self.side_id,
            "pokemon": [mon.request_entry(index < 2) for index, mon in enumerate(self.team)],
        }

    def needs_choice(self) -> bool:
        return self.request is not None and not self.request.get("wait") and self.choice is None


class FakeBattle:
    def __init__(self) -> None:
        self.sides: dict[str, Side] = {}
        self.state = "none"
        self.turn = 0
        self.ended = False
        self.winner: str | None = None
        self._reset_output()

    # -- commands ----------------------------------------------------------------------------------

    def start(self, command: dict[str, Any]) -> None:
        for side_id in SIDES:
            player = command["players"][side_id]
            self.sides[side_id] = Side(side_id, player["name"], [_mon(side_id, entry) for entry in player["team"]])
        self.state = "preview"
        for side_id, side in self.sides.items():
            self.public(f"|player|{side_id}|{side.name}||")
        self.public("|gametype|doubles", "|gen|9", "|tier|[Gen 9] VGC 2025 Reg I", "|clearpoke")
        for side_id, side in self.sides.items():
            self.public(*(f"|poke|{side_id}|{mon.species}, L50|" for mon in side.team))
        self.public(f"|teampreview|{PICKED_TEAM_SIZE}")
        for side in self.sides.values():
            self.send_request(side, {"teamPreview": True, "maxChosenTeamSize": PICKED_TEAM_SIZE})

    def choose(self, side_id: str, text: str) -> None:
        side = self.sides[side_id]
        try:
            if not side.needs_choice():
                raise InvalidChoice("There's nothing to choose")
            side.choice = self.parse(side, text)
        except InvalidChoice as exc:
            self.errors[side_id] = f"[Invalid choice] {exc}"
            return
        if not any(other.needs_choice() for other in self.sides.values()):
            self.resolve()

    # -- parsing a choice --------------------------------------------------------------------------

    def parse(self, side: Side, text: str) -> list[tuple[str, int, int]]:
        if self.state == "preview":
            return _parse_team(text, len(side.team))
        parts = [part.strip() for part in text.split(",")]
        if len(parts) != len(SLOTS):
            raise InvalidChoice(f"one choice per slot, got {text!r}")
        actions = [_parse_part(part) for part in parts]
        for slot in SLOTS:
            self.check(side, slot, actions[slot])
        switched = [number for kind, number, _target in actions if kind == "switch"]
        if len(switched) != len(set(switched)):
            raise InvalidChoice("two slots switch to the same Pokémon")
        return actions

    def check(self, side: Side, slot: int, action: tuple[str, int, int]) -> None:
        kind = action[0]
        if self.state == "switch":
            _check_switch_slot(side, slot, action)
        elif side.team[slot].fainted:
            _require(kind == "pass", f"slot {slot + 1} has fainted and can only pass")
        elif kind == "move":
            _check_move(side.team[slot], slot, action)
        else:
            _require(kind == "switch" and _switch_ok(side, action[1]), f"slot {slot + 1} must move or switch")

    # -- resolving ---------------------------------------------------------------------------------

    def resolve(self) -> None:
        if self.state == "preview":
            self.begin()
        elif self.state == "move":
            self.play_turn()
        else:
            self.apply_switches()
            self.next_turn()

    def begin(self) -> None:
        for side in self.sides.values():
            side.team = [side.team[number - 1] for _kind, number, _target in side.choice][:PICKED_TEAM_SIZE]
        self.public("|", *(f"|teamsize|{side_id}|{PICKED_TEAM_SIZE}" for side_id in SIDES), "|start")
        for side in self.sides.values():
            for slot in SLOTS:
                self.hp_line("switch", side.team[slot], f"|{side.team[slot].species}, L50")
        self.next_turn()

    def play_turn(self) -> None:
        choices = {side_id: side.choice for side_id, side in self.sides.items()}
        self.apply_switches()
        for side_id in SIDES:
            for slot in SLOTS:
                self.use_move(self.sides[side_id], slot, choices[side_id][slot])
        self.chip_actives()
        self.public("|", "|upkeep")
        if not self.check_end():
            self.after_turn()

    def chip_actives(self) -> None:
        for mon in self.actives():
            self.damage(mon, CHIP_SHARE, "|[from] fake chip")

    def apply_switches(self) -> None:
        for side in self.sides.values():
            for slot, (kind, number, _target) in zip(SLOTS, side.choice or []):
                if kind == "switch":
                    side.team[slot], side.team[number - 1] = side.team[number - 1], side.team[slot]
                    self.hp_line("switch", side.team[slot], f"|{side.team[slot].species}, L50")
            side.choice = None

    def use_move(self, side: Side, slot: int, action: tuple[str, int, int]) -> None:
        user = side.team[slot]
        if action[0] != "move" or user.fainted:
            return
        move = user.moves[action[1] - 1]
        targets = self.targets_of(side, slot, MOVE_TARGETS[to_id(move)], action[2])
        self.public(f"|move|{self.ident(user)}|{move}|{self.ident(targets[0]) if targets else ''}")
        for target in targets:
            self.damage(target, HIT_SHARE, "")

    def targets_of(self, side: Side, slot: int, target_type: str, position: int) -> list[Mon]:
        foes = [mon for mon in self.foe_side(side).team[:2] if not mon.fainted]
        if target_type in SPREAD_TARGETS:
            return foes
        if position <= 0 or not foes:
            return []
        chosen = self.foe_side(side).team[position - 1]
        return [chosen] if not chosen.fainted else foes[:1]

    def damage(self, mon: Mon, share: float, suffix: str) -> None:
        if mon.fainted or mon not in self.actives():
            return
        mon.hp = max(0, mon.hp - math.ceil(mon.max_hp * share))
        self.hp_line("-damage", mon, "", suffix)
        if mon.fainted:
            self.public(f"|faint|{self.ident(mon)}")

    def check_end(self) -> bool:
        beaten = [side for side in self.sides.values() if all(mon.fainted for mon in side.team)]
        if not beaten and self.turn < MAX_TURNS:
            return False
        if len(beaten) == 1:
            self.winner = self.foe_side(beaten[0]).name
            self.public(f"|win|{self.winner}")
        else:
            self.public("|tie")
        self.ended, self.state = True, "over"
        return True

    def after_turn(self) -> None:
        for side in self.sides.values():
            side.flags = [side.team[slot].fainted and bool(side.bench()) for slot in SLOTS]
        if not any(any(side.flags) for side in self.sides.values()):
            self.next_turn()
            return
        self.state = "switch"
        for side in self.sides.values():
            self.send_switch_request(side)

    def send_switch_request(self, side: Side) -> None:
        if any(side.flags):
            self.send_request(side, {"forceSwitch": list(side.flags), "noCancel": True})
        else:
            self.send_request(side, {"wait": True})

    def next_turn(self) -> None:
        self.turn += 1
        self.state = "move"
        self.public(f"|turn|{self.turn}")
        for side in self.sides.values():
            entries = [{"moves": side.team[slot].move_entries()} for slot in SLOTS]
            self.send_request(side, {"active": entries})

    # -- output ------------------------------------------------------------------------------------

    def _reset_output(self) -> None:
        self.logs: dict[str, list[str]] = {side_id: [] for side_id in SIDES}
        self.requests: dict[str, Any] = {side_id: None for side_id in SIDES}
        self.errors: dict[str, str | None] = {side_id: None for side_id in SIDES}

    def answer(self) -> dict[str, Any]:
        sides = {
            side_id: {"log": self.logs[side_id], "request": self.requests[side_id], "error": self.errors[side_id]}
            for side_id in SIDES
        }
        self._reset_output()
        return {"ok": True, "sides": sides, "ended": self.ended, "winner": self.winner}

    def public(self, *lines: str) -> None:
        for side_id in SIDES:
            self.logs[side_id].extend(lines)

    def hp_line(self, kind: str, mon: Mon, details: str, suffix: str = "") -> None:
        for viewer in SIDES:
            self.logs[viewer].append(f"|{kind}|{self.ident(mon)}{details}|{mon.condition(viewer)}{suffix}")

    def send_request(self, side: Side, request: dict[str, Any]) -> None:
        side.request = {**request, "side": side.side_data()}
        side.choice = None
        self.requests[side.side_id] = side.request

    def ident(self, mon: Mon) -> str:
        slot = self.sides[mon.side].team.index(mon)
        return f"{mon.side}{LETTERS[slot]}: {mon.species}"

    def foe_side(self, side: Side) -> Side:
        return self.sides[SIDES[1 - SIDES.index(side.side_id)]]

    def actives(self) -> list[Mon]:
        return [side.team[slot] for side in self.sides.values() for slot in SLOTS]


def _mon(side_id: str, entry: dict[str, Any]) -> Mon:
    max_hp = BASE_HP + HP_PER_LETTER * len(entry["species"])
    return Mon(side_id, entry["species"], entry["item"], entry["ability"], list(entry["moves"]), max_hp, max_hp)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidChoice(message)


def _parse_team(text: str, team_size: int) -> list[tuple[str, int, int]]:
    digits = text.removeprefix("team ").replace(",", "").replace(" ", "")
    numbers = [int(digit) for digit in digits] if digits.isdigit() else []
    _require(len(set(numbers)) >= PICKED_TEAM_SIZE, f"a team choice names {PICKED_TEAM_SIZE} Pokémon: {text!r}")
    _require(all(1 <= number <= team_size for number in numbers), f"no such team position in {text!r}")
    return [("team", number, 0) for number in numbers]


def _parse_part(part: str) -> tuple[str, int, int]:
    words = part.split()
    if words == ["pass"]:
        return ("pass", 0, 0)
    _require(len(words) in (2, 3) and words[0] in ("move", "switch"), f"unreadable choice {part!r}")
    numbers = [int(word) for word in words[1:] if re.fullmatch(r"-?\d+", word)]
    _require(len(numbers) == len(words) - 1, f"unreadable choice {part!r}")
    return (words[0], numbers[0], numbers[1] if len(numbers) > 1 else 0)


def _check_move(user: Mon, slot: int, action: tuple[str, int, int]) -> None:
    _kind, number, target = action
    _require(1 <= number <= len(user.moves), f"{user.species} has no move {number}")
    allowed = allowed_targets(slot, MOVE_TARGETS[to_id(user.moves[number - 1])])
    if allowed:
        _require(target in allowed, f"Invalid target for {user.moves[number - 1]}")
    else:
        _require(target == 0, f"You can't choose a target for {user.moves[number - 1]}")


def _switch_ok(side: Side, number: int) -> bool:
    return number - 1 in side.bench()


def _check_switch_slot(side: Side, slot: int, action: tuple[str, int, int]) -> None:
    kind, number, _target = action
    if not side.flags[slot]:
        _require(kind == "pass", f"slot {slot + 1} does not need to switch")
    elif kind == "switch":
        _require(_switch_ok(side, number), f"position {number} cannot switch in")
    else:
        _require(kind == "pass" and len(side.bench()) < sum(side.flags), f"slot {slot + 1} must switch")


def main() -> None:
    battle = FakeBattle()
    for line in sys.stdin:
        if not line.strip():
            continue
        command = json.loads(line)
        if command["command"] == "start":
            battle.start(command)
        else:
            battle.choose(command["side"], command["choice"])
        print(json.dumps(battle.answer()), flush=True)


if __name__ == "__main__":
    main()
