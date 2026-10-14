"""Maximin play of a doubles turn: each of our best joint actions weighed by its worst case against the opponent's
best replies.

With `[maximin] enabled`, the opponent's side of the board is read as they see
it (`opponent_view`): their two actives as theirs to command, ours as their
foes, the benched Pokémon of theirs seen so far and still standing as their
reserves, and for each of their actives every move of its known set (the
draft's card, else what the observation shows; an item or ability not known
counts as none, as the damage estimate takes it) aimed at each of our standing
actives when it takes one target, every switch into one of their reserves, and
pass for a position that is empty or holds a fallen Pokémon. Their joint
actions are ranked with the same `[turn]` scores from their side
(`rank_actions`), with a tracker of their own that knows when each of theirs
came in, so their Fake Out counts on its first turn out. What they chose last
turn is not observed, so their Protect is never taken to fail.

Our best `our_top` joint actions by the script's score, leaving out any that
holds a Protect locking its Choice item holder (`locks_choice`), are each
paired with their best `their_top`, and each pairing is played through a
one-turn model:

- switches first: the reserve takes the position, and the hits aimed at it;
- then the moves, by priority and then Speed as the scorer reads it (stages,
  Choice Scarf, paralysis, Tailwind), the slower first under Trick Room; at
  equal priority and Speed the opponent moves first, the worse case for us;
- Protect (or a move like it) keeps every later move off its user, after N
  Protects in a row by it with chance (1/3)^N (`shield_chance`), as Showdown
  has it;
- Fake Out on its user's first turn out makes a target it can touch flinch, so
  the target's own move is lost;
- a damaging move lands as often as its user is still standing and not
  flinched, does its expected damage (the estimate's mean roll times its
  accuracy, at most the HP left) and knocks its target out with the share of
  rolls that reach the HP left (a full-HP Focus Sash or Sturdy holder survives
  one hit); one aimed at a foe that has fallen turns to that foe's partner, as
  in the game; a spread move hits every standing target, with the spread
  reduction when there are two or more;
- a move that makes its user faint costs the user and the HP it had left.

Each Pokémon's HP left and chance to be standing are carried from move to
move. A pairing's result is the HP percent we deal less the HP percent we
take, plus `[turn] ko_bonus` times the knockouts we are expected to make less
those we are expected to suffer. Each of our joint actions is ranked by its
worst result over the opponent's actions, ties by its mean result, and keeps
the reply that gave the worst: the opponent's best answer to it.

The evaluation stops at the time limit it is given and never runs past
`TURN_LIMIT_SECONDS`; when it stops it gives nothing, and the script's own
ranking stands.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Sequence

from ucla_agent.games.pokemon import damage, data
from ucla_agent.games.pokemon.battle import (
    FOE_TARGETS,
    MOVE,
    NO_TARGET,
    OUR_TARGETS,
    PASS,
    SLOT_INDEXES,
    SWITCH,
    BattleTracker,
    Board,
    Fighter,
    Option,
    Slot,
)
from ucla_agent.games.pokemon.sets import FAKE_OUT, PROTECT_MOVES, SPREAD_TARGETS, is_damaging
from ucla_agent.games.pokemon.tuning import MaximinTuning, TurnTuning
from ucla_agent.games.pokemon.turn import (
    ALL_ADJACENT,
    PASS_SCORE,
    PERCENT,
    RANDOM_FOE,
    SELF_FAINTING_MOVES,
    TURN_TYPE,
    JointAction,
    Scorer,
    SlotChoice,
    accuracy,
    can_be_hit,
    rank_actions,
    survives_one_hit,
)

# The longest a turn's maximin may take; past it the script's ranking is played.
TURN_LIMIT_SECONDS = 0.3
# Showdown's Protect works a third as often for each Protect in a row before it: 1, 1/3, 1/9...
PROTECT_CHAIN_ODDS = 1 / 3
# Showdown target types of a move aimed at one Pokémon the user picks.
SINGLE_TARGET_TYPES = frozenset({"normal", "any", "adjacentFoe"})
# The positions of one side add up to 3 (1 and 2) or -3 (-1 and -2): a position's ally is that sum less itself.
SIDE_SUM = 3
# Who moves first at equal priority and Speed: the opponent, the worse case for us.
THEIRS_FIRST, OURS_LAST = 0, 1
PASS_CHOICE = SlotChoice({"type": PASS}, PASS_SCORE, "pass")
# The opponent's answer when they have no legal pair of choices at all.
IDLE = JointAction({"type": TURN_TYPE, "slot_0": {"type": PASS}, "slot_1": {"type": PASS}}, 0.0, (PASS_CHOICE,) * 2)


@dataclass(frozen=True)
class Side:
    """One side's view of the turn: its board and the tracker that has followed it."""

    board: Board
    tracker: BattleTracker


@dataclass(frozen=True)
class Outcome:
    """What one pairing is expected to do in a turn: HP percent we deal and take, knockouts we make and suffer."""

    dealt: float
    taken: float
    knocked_out: float
    lost: float

    def value(self, ko_bonus: float) -> float:
        """The pairing's result: dealt less taken, plus `ko_bonus` per knockout made less per knockout suffered."""
        return self.dealt - self.taken + ko_bonus * (self.knocked_out - self.lost)


@dataclass(frozen=True)
class MaximinAction:
    """One of our joint actions with its worst and mean result, and the opponent's reply that gave the worst."""

    joint: JointAction
    worst: float
    mean: float
    reply: JointAction
    outcome: Outcome


def opponent_view(board: Board) -> Board:
    """The board as the opponent sees it (see the module note): their actives as ours, ours as theirs."""
    on_field = {fighter.species for fighter in board.theirs if fighter is not None}
    reserves = {
        species: fighter
        for species, fighter in board.their_seen.items()
        if fighter.standing and not fighter.active and species not in on_field
    }
    slots = [_their_slot(index, board.theirs[index], board, reserves) for index in SLOT_INDEXES]
    assumed = {
        move.id: move
        for fighter in board.theirs
        if fighter is not None and fighter.set is not None
        for move in fighter.set.move_records()
        if data.move(move.id) is None
    }
    return Board(
        turn=board.turn,
        ours=board.theirs,
        theirs=board.ours,
        reserves=reserves,
        weather=board.weather,
        terrain=board.terrain,
        trick_room=board.trick_room,
        our_conditions=board.their_conditions,
        their_conditions=board.our_conditions,
        force_switch=(False, False),
        slots=(slots[0], slots[1]),
        move_facts={**board.move_facts, **assumed},
    )


def _their_slot(index: int, fighter: Fighter | None, board: Board, reserves: dict[str, Fighter]) -> Slot:
    """One of the opponent's slots as they would be offered it: their active's moves and switches, else pass."""
    position = OUR_TARGETS[index]
    if fighter is None or not fighter.standing or fighter.set is None:
        return Slot(index, position, (Option(PASS),), fighter.species if fighter is not None else "")
    moves = tuple(
        Option(MOVE, move_id=move.id, targets=_their_targets(move, board)) for move in fighter.set.move_records()
    )
    switches = tuple(Option(SWITCH, species=species) for species in reserves)
    return Slot(index, position, moves + switches, fighter.species)


def _their_targets(move: data.Move, board: Board) -> tuple[int, ...]:
    """Where one of the opponent's moves may be aimed, in their numbering: each of our standing actives for a move
    of one target, else no target."""
    if move.target not in SINGLE_TARGET_TYPES:
        return (NO_TARGET,)
    standing = tuple(
        target for target, fighter in zip(FOE_TARGETS, board.ours) if fighter is not None and fighter.standing
    )
    return standing or FOE_TARGETS


def shield_chance(protect_chain: int) -> float:
    """The chance a Protect works after `protect_chain` Protects in a row by its user: (1/3) to that power."""
    return PROTECT_CHAIN_ODDS**protect_chain


def rank_turn(
    ours: Side,
    theirs: Side,
    ranked: Sequence[JointAction],
    tuning: TurnTuning,
    sizes: MaximinTuning,
    until: float,
) -> list[MaximinAction] | None:
    """Our best `sizes.our_top` of `ranked` (the script's ranking, best first) by their worst result against the
    opponent's best `sizes.their_top`, ties by the mean (see the module note); None when the time limit `until` (a
    `time.perf_counter()` moment) or `TURN_LIMIT_SECONDS` passes first."""
    stop = min(until, time.perf_counter() + TURN_LIMIT_SECONDS)
    replies = rank_actions(theirs.board, theirs.tracker, tuning)[: sizes.their_top] or [IDLE]
    model = OneTurnModel(ours, theirs, tuning)
    weighed = []
    for joint in [joint for joint in ranked if not locks_choice(joint)][: sizes.our_top]:
        if time.perf_counter() > stop:
            return None
        weighed.append(_weigh(joint, replies, model, tuning.ko_bonus))
    return sorted(weighed, key=lambda action: (action.worst, action.mean), reverse=True)


def locks_choice(joint: JointAction) -> bool:
    """Whether one of the joint action's choices is a Protect that would lock its Choice item holder into Protect;
    such an action is left out of maximin's candidates, since the one-turn model cannot see the turns it costs."""
    return any(choice.choice_lock for choice in joint.slots)


def _weigh(joint: JointAction, replies: Sequence[JointAction], model: OneTurnModel, ko_bonus: float) -> MaximinAction:
    outcomes = [model.play(joint, reply) for reply in replies]
    values = [outcome.value(ko_bonus) for outcome in outcomes]
    worst = min(range(len(values)), key=values.__getitem__)
    return MaximinAction(joint, values[worst], statistics.fmean(values), replies[worst], outcomes[worst])


@dataclass(frozen=True)
class _Act:
    """One slot's choice in the one-turn model, positions in our numbering (ours -1 and -2, theirs 1 and 2)."""

    position: int
    kind: str
    fighter: Fighter | None = None
    move: data.Move | None = None
    target: int = NO_TARGET
    reserve: Fighter | None = None
    first_turn_out: bool = False
    protect_chain: int = 0


@dataclass
class _TurnState:
    """Each position's Pokémon, its HP at the start of the turn, the HP it is expected to lose, its chance to be
    standing, to be shielded by Protect and to have flinched."""

    fighters: dict[int, Fighter | None]
    start_hp: dict[int, float] = field(default_factory=dict)
    start_alive: dict[int, float] = field(default_factory=dict)
    taken: dict[int, float] = field(default_factory=dict)
    alive: dict[int, float] = field(default_factory=dict)
    shield: dict[int, float] = field(default_factory=dict)
    flinch: dict[int, float] = field(default_factory=dict)

    @classmethod
    def of(cls, board: Board) -> _TurnState:
        positions = dict(zip(OUR_TARGETS, board.ours)) | dict(zip(FOE_TARGETS, board.theirs))
        state = cls(fighters={})
        for position, fighter in positions.items():
            state.place(position, fighter)
        return state

    def place(self, position: int, fighter: Fighter | None) -> None:
        """Put a Pokémon at a position, as it stands at the start of the turn."""
        standing = fighter is not None and fighter.standing
        self.fighters[position] = fighter
        self.start_hp[position] = fighter.hp_fraction * PERCENT if standing else 0.0
        self.start_alive[position] = self.alive[position] = 1.0 if standing else 0.0
        self.taken[position] = self.shield[position] = self.flinch[position] = 0.0

    def acting(self, position: int) -> float:
        """The chance the Pokémon at a position gets to use its move: standing and not flinched."""
        return self.alive[position] * (1 - self.flinch[position])

    def left(self, position: int) -> float:
        return max(0.0, self.start_hp[position] - self.taken[position])

    def hit(self, position: int, chance: float, rolls: damage.DamageRange, survives: bool) -> None:
        """A hit landing with `chance`: its expected damage up to the HP left, and its knockouts (none when the
        defender `survives` a first hit at full HP)."""
        left, first = self.left(position), self.taken[position] == 0
        knocks_out = 0.0 if first and survives else rolls.ko_chance if first else _reach_share(rolls, left)
        self.taken[position] += chance * min((rolls.min_pct + rolls.max_pct) / 2, left)
        self.alive[position] = max(0.0, self.alive[position] - chance * knocks_out)

    def faint(self, position: int, chance: float) -> None:
        """The Pokémon at a position faints with `chance` by its own move, losing the HP it had left."""
        self.taken[position] += chance * self.left(position)
        self.alive[position] = max(0.0, self.alive[position] - chance)

    def outcome(self) -> Outcome:
        def lost_hp(positions: tuple[int, ...]) -> float:
            return sum(self.taken[position] for position in positions)

        def knockouts(positions: tuple[int, ...]) -> float:
            return sum(self.start_alive[position] - self.alive[position] for position in positions)

        return Outcome(lost_hp(FOE_TARGETS), lost_hp(OUR_TARGETS), knockouts(FOE_TARGETS), knockouts(OUR_TARGETS))


def _reach_share(rolls: damage.DamageRange, left: float) -> float:
    """The share of the rolls, spread evenly from the lowest to the highest, that reach `left` HP percent."""
    if left <= 0 or rolls.min_pct >= left:
        return 1.0
    if rolls.max_pct < left:
        return 0.0
    return (rolls.max_pct - left) / (rolls.max_pct - rolls.min_pct)


def _ally(position: int) -> int:
    return (SIDE_SUM if position > 0 else -SIDE_SUM) - position


def _foes(position: int) -> tuple[int, ...]:
    return OUR_TARGETS if position > 0 else FOE_TARGETS


class OneTurnModel:
    """Plays one of our joint actions against one of the opponent's through one turn (see the module note)."""

    def __init__(self, ours: Side, theirs: Side, tuning: TurnTuning) -> None:
        self._ours = ours
        self._theirs = theirs
        self._scorer = Scorer(ours.board, ours.tracker, tuning)
        self._rolls: dict[tuple[int, str, int, bool, bool], damage.DamageRange] = {}

    def play(self, ours: JointAction, theirs: JointAction) -> Outcome:
        """The outcome of our joint action against theirs (theirs as their own view of the board numbers it)."""
        state = _TurnState.of(self._ours.board)
        acts = [*self._acts(self._ours, ours, 1), *self._acts(self._theirs, theirs, -1)]
        for act in acts:
            if act.kind == SWITCH and act.reserve is not None:
                state.place(act.position, act.reserve)
        moves = [act for act in acts if act.kind == MOVE and act.move is not None and act.fighter is not None]
        for act in sorted(moves, key=self._order):
            self._resolve(state, act)
        return state.outcome()

    def _acts(self, side: Side, joint: JointAction, sign: int) -> list[_Act]:
        """Each slot's choice of one side's joint action, its positions turned into our numbering by `sign`."""
        return [self._act(side, slot, choice.choice, sign) for slot, choice in zip(side.board.slots, joint.slots)]

    @staticmethod
    def _act(side: Side, slot: Slot, choice: dict, sign: int) -> _Act:
        position = sign * slot.position
        if choice["type"] == SWITCH:
            return _Act(position, SWITCH, reserve=side.board.reserves.get(data.to_id(choice["species"])))
        if choice["type"] != MOVE:
            return _Act(position, PASS)
        return _Act(
            position,
            MOVE,
            fighter=side.board.own(slot),
            move=side.board.move(choice["move_id"]),
            target=sign * choice["target"],
            first_turn_out=side.tracker.first_turn_out(side.board, slot),
            protect_chain=side.tracker.protect_chain(side.board, slot),
        )

    def _order(self, act: _Act) -> tuple[int, float, int]:
        """Higher priority first, then the faster (the slower under Trick Room), then the opponent."""
        speed = self._scorer.speed(act.fighter)
        side = THEIRS_FIRST if act.position > 0 else OURS_LAST
        return -act.move.priority, speed if self._ours.board.trick_room else -speed, side

    def _resolve(self, state: _TurnState, act: _Act) -> None:
        acting = state.acting(act.position)
        if acting <= 0 or (act.move.id == FAKE_OUT and not act.first_turn_out):
            return
        if act.move.id in PROTECT_MOVES:
            state.shield[act.position] = acting * shield_chance(act.protect_chain)
            return
        if is_damaging(act.move):
            self._strike(state, act, acting)
        if act.move.id in SELF_FAINTING_MOVES:
            state.faint(act.position, acting)

    def _strike(self, state: _TurnState, act: _Act, acting: float) -> None:
        """A damaging move's hits, by its target type."""
        if act.move.target in SPREAD_TARGETS:
            targets = [*_foes(act.position), *([_ally(act.position)] if act.move.target == ALL_ADJACENT else [])]
            spread = sum(state.alive[target] > 0 for target in targets) > 1
            for target in targets:
                self._land(state, act, target, acting, spread)
        elif act.move.target == RANDOM_FOE:
            standing = [foe for foe in _foes(act.position) if state.alive[foe] > 0]
            for foe in standing:
                self._land(state, act, foe, acting / len(standing), spread=False)
        elif act.target != NO_TARGET:
            self._single(state, act, acting)

    def _single(self, state: _TurnState, act: _Act, acting: float) -> None:
        """A move at one Pokémon; at a foe that has fallen, it turns to that foe's partner."""
        fallen = 1 - state.alive[act.target]
        self._land(state, act, act.target, acting, spread=False)
        if act.target in _foes(act.position) and fallen > 0:
            self._land(state, act, _ally(act.target), acting * fallen, spread=False)

    def _land(self, state: _TurnState, act: _Act, position: int, chance: float, spread: bool) -> None:
        """The move's hit on the Pokémon at a position, as often as it is there to be hit and not shielded."""
        defender = state.fighters[position]
        landed = chance * state.alive[position] * (1 - state.shield[position])
        if defender is None or defender.set is None or act.fighter.set is None or landed <= 0:
            return
        rolls = self._rolls_of(act, defender, position, spread)
        state.hit(position, landed * accuracy(act.move), rolls, survives_one_hit(defender, rolls.hits))
        if act.move.id == FAKE_OUT and can_be_hit(act.move, defender):
            state.flinch[position] += landed * (1 - state.flinch[position])

    def _rolls_of(self, act: _Act, defender: Fighter, position: int, spread: bool) -> damage.DamageRange:
        """The damage estimate of the act's move on the defender, kept for the other pairings of the turn."""
        theirs = position in FOE_TARGETS
        key = (id(act.fighter), act.move.id, id(defender), spread, theirs)
        if key not in self._rolls:
            board = self._ours.board
            conditions = board.their_conditions if theirs else board.our_conditions
            self._rolls[key] = self._scorer.rolls(act.fighter, act.move, defender, spread, conditions)
        return self._rolls[key]
