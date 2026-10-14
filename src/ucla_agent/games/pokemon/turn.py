"""A doubles turn (pokemon.md, section 7): each slot's options scored, and the legal pairs of them, best first.

Each option the template lists for a slot is scored with the weights of
`[turn]`, damage counted in percent of the target's maximum HP from the
damage estimate (the mean of its lowest and highest roll, times the move's
accuracy, never more than the HP the target has left):

- a damaging move at a foe: its expected damage plus `ko_bonus` times its
  chance to knock the foe out; at our own ally the same counts against it;
- a spread move: the same summed over the foes it hits, less what it does to
  our ally when it hits the ally too; a move that hits a random foe counts
  the mean over the foes;
- Fake Out: `fake_out_bonus` on top of its damage on the user's first turn
  out; 0 on any later turn, when it fails;
- Protect (or a move like it): `protect_bonus` when a foe that moves first has
  a 50% or better chance to knock the slot's Pokémon out; after N Protects in
  a row by the same Pokémon, minus `protect_bonus` times N, each one before
  it making it three times as likely to fail; by a Choice Scarf, Band or
  Specs holder while a damaging move is on offer beside it, minus
  `protect_bonus`, since the item would lock it into Protect until it
  switches (offered alone, it is scored as any Protect);
- Tailwind or Trick Room: `speed_control_bonus` when it turns more pairs of
  our and their active Pokémon to our side moving first than it turns
  against; 0 while it is already up;
- a switch: `switch_bonus` when the active Pokémon faces a likely knockout and
  the reserve resists the threatening move's type, minus `switch_penalty`
  otherwise; when the slot must switch, the reserve's matchup against the
  foes (its best expected damage to each minus each one's best to it);
- every other move: 0; `pass`: far below anything else, so it is sent only
  when nothing else is offered;
- a foe at full HP with Focus Sash or Sturdy is never knocked out by a move
  that hits once;
- a move that makes its user faint (Explosion, Memento...) costs, on top of
  that, the user's own worth: its HP left plus `ko_bonus`, as a knockout of
  ours would.

The pairs of slot choices are the joint actions: never two switches into one
species, never two passes. When both slots aim single-target moves at one foe
that either one is expected to knock out alone, `overkill_malus` is taken off
(the order the two move in is not known). Each action is built from the
template's own options and targets; there is no Terastallization.

Each action describes itself to a model: both slots' choices with the
expected damage and knockout chance of their moves, and the script's score.
Unranked (`description(with_score=False)`), no figure of the script's own is
shown: a spread move or one that hits a random foe gives its expected damage
and knockout chance at each foe instead of its summed value, and a forced
switch leaves out its matchup.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

from ucla_agent.games.pokemon import damage, data
from ucla_agent.games.pokemon.battle import FOE_TARGETS, MOVE, PASS, SWITCH, BattleTracker, Board, Fighter, Option, Slot
from ucla_agent.games.pokemon.sets import (
    FAKE_OUT,
    PROTECT_MOVES,
    SPREAD_TARGETS,
    TAILWIND,
    TRICK_ROOM,
    is_damaging,
    type_effect,
)
from ucla_agent.games.pokemon.tuning import TurnTuning

TURN_TYPE = "doubles_turn"
LIKELY_KNOCKOUT = 0.5
# Pass is legal only when it is offered; it has to lose to anything else the slot may do.
PASS_SCORE = -1000.0
PERCENT = 100.0
ALL_ADJACENT = "allAdjacent"
RANDOM_FOE = "randomNormal"
# An item and an ability that leave their holder at 1 HP from full when one hit would knock it out.
FOCUS_SASH = "focussash"
STURDY = "sturdy"
# Moves after which their user faints.
SELF_FAINTING_MOVES = frozenset(
    {"explosion", "selfdestruct", "mistyexplosion", "memento", "finalgambit", "healingwish", "lunardance"}
)
TAILWIND_CONDITION = "tailwind"
REFLECT_CONDITIONS = frozenset({"reflect", "auroraveil"})
LIGHT_SCREEN_CONDITIONS = frozenset({"lightscreen", "auroraveil"})
TAILWIND_SPEED = 2.0
CHOICE_SCARF = "choicescarf"
# Items that lock their holder into the first move it uses until it switches out.
CHOICE_ITEMS = frozenset({CHOICE_SCARF, "choiceband", "choicespecs"})
SCARF_SPEED = 1.5
PARALYSIS = "par"
PARALYSIS_SPEED = 0.5
# Our board positions are -1 and -2: a slot's ally stands at -3 minus its own position.
OUR_POSITIONS_SUM = -3
# What a move aimed at the foes hits when none is standing.
NO_FOE = "no foe standing"


@dataclass(frozen=True)
class Hit:
    """One move's expected damage to one target (capped at its HP left), the same uncapped, and the KO chance."""

    expected: float
    raw: float
    ko_chance: float


NO_HIT = Hit(0.0, 0.0, 0.0)


@dataclass(frozen=True)
class SlotChoice:
    """One slot's answer with its score; `target` is the foe a single-target damaging move aims at, `knocks_out`
    whether its expected damage alone knocks that foe out. `facts` is the label without the script's own figures,
    for a label that shows one (a spread move's summed value, a forced switch's matchup); None for a label that shows
    facts alone. `choice_lock` marks a Protect that would lock its Choice item holder into Protect while it could
    still attack."""

    choice: Mapping[str, Any]
    score: float
    label: str
    target: int | None = None
    knocks_out: bool = False
    facts: str | None = None
    choice_lock: bool = False

    @property
    def unscored_label(self) -> str:
        """The choice as an unranked option reads it: its facts, which are its label unless that shows a score."""
        return self.label if self.facts is None else self.facts


@dataclass(frozen=True)
class JointAction:
    """One `doubles_turn` action, the pair of slot choices it is made of, and its score."""

    action: Mapping[str, Any]
    score: float
    slots: tuple[SlotChoice, SlotChoice]

    def description(self, *, with_score: bool = True) -> str:
        """The action as a model reads it: each slot's choice with its expected damage and KO chance, and the
        script's score `with_score`; without it, no figure of the script's own."""
        first, second = self.slots
        if not with_score:
            return f"slot 0: {first.unscored_label}; slot 1: {second.unscored_label}"
        return f"slot 0: {first.label}; slot 1: {second.label}; score {self.score:.0f}"


@dataclass(frozen=True)
class _Threat:
    """One foe's move at our Pokémon: its hit, its type, and whether that foe moves first."""

    hit: Hit
    move_type: str
    first: bool


def rank_actions(board: Board, tracker: BattleTracker, tuning: TurnTuning) -> list[JointAction]:
    """Every legal pair of the two slots' scored choices (see the module note), best first."""
    scorer = Scorer(board, tracker, tuning)
    first, second = (scorer.slot_choices(slot) for slot in board.slots)
    pairs = [_joint(one, other, tuning) for one in first for other in second if _allowed(one, other)]
    return sorted(pairs, key=lambda pair: pair.score, reverse=True)


def _allowed(first: SlotChoice, second: SlotChoice) -> bool:
    """Not both passing, and not both switching into one species."""
    kinds = (first.choice["type"], second.choice["type"])
    if kinds == (PASS, PASS):
        return False
    return kinds != (SWITCH, SWITCH) or data.to_id(first.choice["species"]) != data.to_id(second.choice["species"])


def _joint(first: SlotChoice, second: SlotChoice, tuning: TurnTuning) -> JointAction:
    score = first.score + second.score
    same_foe = first.target is not None and first.target == second.target
    if same_foe and (first.knocks_out or second.knocks_out):
        score -= tuning.overkill_malus
    return JointAction({"type": TURN_TYPE, "slot_0": first.choice, "slot_1": second.choice}, score, (first, second))


class Scorer:
    """Scores the options of one board's slots (see the module note). Its speeds and hits are public: the model's
    briefing shows the same figures the scores are made of."""

    def __init__(self, board: Board, tracker: BattleTracker, tuning: TurnTuning) -> None:
        self.board = board
        self.tracker = tracker
        self.tuning = tuning
        self._special: dict[str, Callable[[Slot, Fighter, data.Move, int], SlotChoice]] = {
            FAKE_OUT: self._fake_out,
            TAILWIND: self._tailwind,
            TRICK_ROOM: self._trick_room,
        }

    def slot_choices(self, slot: Slot) -> list[SlotChoice]:
        """Every choice the slot's template options allow, scored."""
        choices: list[SlotChoice] = []
        for option in slot.options:
            choices.extend(self._option_choices(slot, option))
        return choices

    def _option_choices(self, slot: Slot, option: Option) -> list[SlotChoice]:
        if option.kind == PASS:
            return [SlotChoice({"type": PASS}, PASS_SCORE, "pass")]
        if option.kind == SWITCH:
            return [self._switch(slot, option.species)]
        return [self._move(slot, option, target) for target in self._targets(option)]

    def _targets(self, option: Option) -> tuple[int, ...]:
        """The targets worth scoring: a move that deals no damage is aimed at foes only, when it may be."""
        move = self.board.move(option.move_id)
        foes = tuple(target for target in option.targets if target in FOE_TARGETS)
        return foes if foes and (move is None or not is_damaging(move)) else option.targets

    # -- moves -----------------------------------------------------------------------------------

    def _move(self, slot: Slot, option: Option, target: int) -> SlotChoice:
        choice = {"type": MOVE, "move_id": option.move_id, "target": target}
        me, move = self.board.own(slot), self.board.move(option.move_id)
        if me is None or me.set is None or move is None:
            return SlotChoice(choice, 0.0, f"{option.move_id} (unknown)")
        if move.id in PROTECT_MOVES:
            return self._protect(slot, me, move, choice)
        scored = self._special.get(move.id, self._attack)(slot, me, move, target)
        cost = self._self_faint_cost(me, move)
        return replace(scored, choice=choice, score=scored.score - cost)

    def _self_faint_cost(self, me: Fighter, move: data.Move) -> float:
        """What a move that makes its user faint costs: the user's HP left plus a knockout's bonus; else 0."""
        if move.id not in SELF_FAINTING_MOVES:
            return 0.0
        return self._value(Hit(me.hp_fraction * PERCENT, me.hp_fraction * PERCENT, 1.0))

    def _attack(self, slot: Slot, me: Fighter, move: data.Move, target: int) -> SlotChoice:
        """A move scored by the damage it does (see the module note); 0 for a move that deals none."""
        if not is_damaging(move):
            return SlotChoice({}, 0.0, f"{move.name} -> {target}")
        if move.target in SPREAD_TARGETS:
            return self._spread(slot, me, move)
        if move.target == RANDOM_FOE:
            return self._random_foe(me, move)
        return self._single(me, move, target)

    def _single(self, me: Fighter, move: data.Move, target: int) -> SlotChoice:
        defender = self.board.at(target)
        if defender is None or not defender.standing:
            return SlotChoice({}, 0.0, f"{move.name} -> empty {target}")
        hit = self.hit(me, move, defender, spread=False)
        label = _hit_label(move, defender, target, hit)
        if target not in FOE_TARGETS:
            return SlotChoice({}, -self._value(hit), label)
        return SlotChoice({}, self._value(hit), label, target, hit.raw >= defender.hp_fraction * PERCENT)

    def _spread(self, slot: Slot, me: Fighter, move: data.Move) -> SlotChoice:
        foes = self._standing_foes()
        ally_position = OUR_POSITIONS_SUM - slot.position
        ally = self.board.at(ally_position) if move.target == ALL_ADJACENT else None
        hit_ally = ally is not None and ally.standing
        spread = len(foes) + hit_ally > 1
        hits = [(target, foe, self.hit(me, move, foe, spread)) for target, foe in foes]
        score = sum(self._value(hit) for _, _, hit in hits)
        if hit_ally:
            ally_hit = self.hit(me, move, ally, spread)
            score -= self._value(ally_hit)
            hits.append((ally_position, ally, ally_hit))
        facts = "; ".join(_hit_text(target, defender, hit) for target, defender, hit in hits) or NO_FOE
        return SlotChoice({}, score, f"{move.name} (spread): {score:.0f}", facts=f"{move.name} (spread) -> {facts}")

    def _random_foe(self, me: Fighter, move: data.Move) -> SlotChoice:
        hits = [(target, foe, self.hit(me, move, foe, spread=False)) for target, foe in self._standing_foes()]
        values = [self._value(hit) for _, _, hit in hits]
        score = sum(values) / len(values) if values else 0.0
        facts = " or ".join(_hit_text(target, foe, hit) for target, foe, hit in hits) or NO_FOE
        label = f"{move.name} (random foe): {score:.0f}"
        return SlotChoice({}, score, label, facts=f"{move.name} (random foe) -> {facts}")

    def _fake_out(self, slot: Slot, me: Fighter, move: data.Move, target: int) -> SlotChoice:
        if not self.tracker.first_turn_out(self.board, slot):
            return SlotChoice({}, 0.0, "Fake Out (fails after the first turn out)")
        scored = self._single(me, move, target)
        defender = self.board.at(target)
        flinches = target in FOE_TARGETS and defender is not None and can_be_hit(move, defender)
        bonus = self.tuning.fake_out_bonus if flinches else 0.0
        return SlotChoice({}, scored.score + bonus, f"Fake Out, {scored.label}", scored.target, scored.knocks_out)

    def _protect(self, slot: Slot, me: Fighter, move: data.Move, choice: Mapping[str, Any]) -> SlotChoice:
        chain = self.tracker.protect_chain(self.board, slot)
        if chain:
            return SlotChoice(choice, -self.tuning.protect_bonus * chain, f"{move.name} ({chain + 1} in a row)")
        if self._choice_locks(slot, me):
            return SlotChoice(choice, -self.tuning.protect_bonus, f"{move.name} (Choice lock)", choice_lock=True)
        threatened = any(threat.hit.ko_chance >= LIKELY_KNOCKOUT and threat.first for threat in self._threats(me))
        score = self.tuning.protect_bonus if threatened else 0.0
        return SlotChoice(choice, score, f"{move.name} ({'likely knockout' if threatened else 'no threat'})")

    def _choice_locks(self, slot: Slot, me: Fighter) -> bool:
        """Whether a Protect would lock a Choice item holder into it while a damaging move is on offer beside it."""
        if me.set.item not in CHOICE_ITEMS:
            return False
        offered = (self.board.move(option.move_id) for option in slot.options if option.kind == MOVE)
        return any(move is not None and is_damaging(move) for move in offered)

    def _tailwind(self, _slot: Slot, _me: Fighter, move: data.Move, _target: int) -> SlotChoice:
        if TAILWIND_CONDITION in self.board.our_conditions:
            return SlotChoice({}, 0.0, f"{move.name} (already up)")
        return self._speed_control(move, our_factor=TAILWIND_SPEED, inverts=False)

    def _trick_room(self, _slot: Slot, _me: Fighter, move: data.Move, _target: int) -> SlotChoice:
        if self.board.trick_room:
            return SlotChoice({}, 0.0, f"{move.name} (already up)")
        return self._speed_control(move, our_factor=1.0, inverts=True)

    def _speed_control(self, move: data.Move, our_factor: float, inverts: bool) -> SlotChoice:
        """Bonus when the change turns more speed pairs our way than against."""
        trick_room = self.board.trick_room
        gain = 0
        for ours in self._standing(self.board.ours):
            for theirs in self._standing(self.board.theirs):
                before = self._ahead(ours, theirs, 1.0, trick_room)
                gain += int(self._ahead(ours, theirs, our_factor, trick_room != inverts)) - int(before)
        score = self.tuning.speed_control_bonus if gain > 0 else 0.0
        return SlotChoice({}, score, f"{move.name} (speed pairs {gain:+d})")

    # -- switches --------------------------------------------------------------------------------

    def _switch(self, slot: Slot, species: str) -> SlotChoice:
        choice = {"type": SWITCH, "species": species}
        reserve = self.board.reserves.get(data.to_id(species))
        if reserve is None or reserve.set is None:
            return SlotChoice(choice, -self.tuning.switch_penalty, f"switch to {species}")
        if self.board.force_switch[slot.index]:
            score = self._matchup(reserve)
            return SlotChoice(choice, score, f"switch to {species} (matchup {score:.0f})", facts=f"switch to {species}")
        return SlotChoice(choice, self._switch_out_score(self.board.own(slot), reserve), f"switch to {species}")

    def _switch_out_score(self, me: Fighter | None, reserve: Fighter) -> float:
        """`switch_bonus` out of a likely knockout into a resist of the threatening move; else the penalty."""
        worst = self._worst_threat(me)
        if (
            worst is not None
            and worst.hit.ko_chance >= LIKELY_KNOCKOUT
            and type_effect(worst.move_type, reserve.set) < 1
        ):
            return self.tuning.switch_bonus
        return -self.tuning.switch_penalty

    def _worst_threat(self, me: Fighter | None) -> _Threat | None:
        """The foe's move most likely to knock our Pokémon out (the most damaging among equals); None for none."""
        threats = self._threats(me) if me is not None and me.set is not None else []
        return max(threats, key=lambda threat: (threat.hit.ko_chance, threat.hit.expected), default=None)

    def _matchup(self, reserve: Fighter) -> float:
        """The reserve's best expected damage to each standing foe minus each one's best to it."""
        score = 0.0
        for foe in self._standing(self.board.theirs):
            score += self._best_expected(reserve, foe) - self._best_expected(foe, reserve)
        return score

    def _best_expected(self, attacker: Fighter, defender: Fighter) -> float:
        if attacker.set is None:
            return 0.0
        moves = [move for move in attacker.set.move_records() if is_damaging(move)]
        return max((self.hit(attacker, move, defender, spread=False).expected for move in moves), default=0.0)

    # -- damage and speed ------------------------------------------------------------------------

    def _threats(self, me: Fighter) -> list[_Threat]:
        """Every damaging move of every standing foe at our Pokémon."""
        return [threat for foe in self._standing(self.board.theirs) for threat in self._foe_threats(foe, me)]

    def _foe_threats(self, foe: Fighter, me: Fighter) -> list[_Threat]:
        moves = [move for move in foe.set.move_records() if is_damaging(move)] if foe.set is not None else []
        return [
            _Threat(
                self.hit(foe, move, me, spread=move.target in SPREAD_TARGETS),
                move.type,
                move.priority > 0 or self._ahead(foe, me, 1.0),
            )
            for move in moves
        ]

    def hit(self, attacker: Fighter, move: data.Move, defender: Fighter, spread: bool) -> Hit:
        """The move's expected damage to the defender (see the module note); none when either set is unknown."""
        if attacker.set is None or defender.set is None or not defender.standing:
            return NO_HIT
        rolls = self.rolls(attacker, move, defender, spread, self._conditions_of(defender))
        hit_rate = accuracy(move)
        raw = (rolls.min_pct + rolls.max_pct) / 2 * hit_rate
        ko_chance = 0.0 if survives_one_hit(defender, rolls.hits) else rolls.ko_chance * hit_rate
        return Hit(min(raw, defender.hp_fraction * PERCENT), raw, ko_chance)

    def rolls(
        self, attacker: Fighter, move: data.Move, defender: Fighter, spread: bool, conditions: frozenset[str]
    ) -> damage.DamageRange:
        """The damage estimate of the move on this board, the defender's side holding `conditions` (its screens);
        both sets must be known."""
        field = damage.Field(
            weather=self.board.weather,
            terrain=self.board.terrain,
            spread=spread,
            reflect=not REFLECT_CONDITIONS.isdisjoint(conditions),
            light_screen=not LIGHT_SCREEN_CONDITIONS.isdisjoint(conditions),
        )
        return damage.estimate(attacker.battle_mon(), move, defender.battle_mon(), field)

    def best_attack(self, attacker: Fighter, defender: Fighter) -> tuple[data.Move, Hit] | None:
        """The attacker's damaging move with the highest expected damage to the defender, with its hit; None when it
        has none or either set is unknown. A spread move counts as hitting two targets, as a foe's threats do."""
        if attacker.set is None or defender.set is None:
            return None
        hits = [
            (move, self.hit(attacker, move, defender, spread=move.target in SPREAD_TARGETS))
            for move in attacker.set.move_records()
            if is_damaging(move)
        ]
        return max(hits, key=lambda scored: (scored[1].expected, scored[1].ko_chance), default=None)

    def _value(self, hit: Hit) -> float:
        return hit.expected + self.tuning.ko_bonus * hit.ko_chance

    def _ahead(self, mover: Fighter, other: Fighter, mover_factor: float, trick_room: bool | None = None) -> bool:
        """Whether `mover` acts before `other` (its speed times `mover_factor`); Trick Room lets the slower first."""
        reversed_order = self.board.trick_room if trick_room is None else trick_room
        mover_speed, other_speed = self.speed(mover) * mover_factor, self.speed(other)
        return mover_speed < other_speed if reversed_order else mover_speed > other_speed

    def speed(self, fighter: Fighter) -> float:
        """Its Speed after stages, Choice Scarf, paralysis and its side's Tailwind; 0 for an unknown set."""
        if fighter.set is None:
            return 0.0
        speed = float(fighter.set.stat("spe")) * _stage_factor(fighter.boosts.get("spe", 0))
        if fighter.set.item == CHOICE_SCARF:
            speed *= SCARF_SPEED
        if fighter.status == PARALYSIS:
            speed *= PARALYSIS_SPEED
        return speed * TAILWIND_SPEED if TAILWIND_CONDITION in self._conditions_of(fighter) else speed

    def _conditions_of(self, fighter: Fighter) -> frozenset[str]:
        """The side conditions of the fighter's side."""
        return self.board.their_conditions if self._is_theirs(fighter) else self.board.our_conditions

    def _is_theirs(self, fighter: Fighter) -> bool:
        return any(fighter is foe for foe in self.board.theirs)

    @staticmethod
    def _standing(fighters: tuple[Fighter | None, Fighter | None]) -> list[Fighter]:
        return [fighter for fighter in fighters if fighter is not None and fighter.standing]

    def _standing_foes(self) -> list[tuple[int, Fighter]]:
        """The foes standing, each with the target that names it, in the order of `_standing`."""
        foes = [(target, self.board.at(target)) for target in FOE_TARGETS]
        return [(target, foe) for target, foe in foes if foe is not None and foe.standing]


def _stage_factor(stage: int) -> float:
    """A stat stage's multiplier: (2 + n) / 2 when raised, 2 / (2 - n) when lowered."""
    return (2 + stage) / 2 if stage >= 0 else 2 / (2 - stage)


def survives_one_hit(defender: Fighter, hits: int) -> bool:
    """Whether Focus Sash or Sturdy keeps the defender at 1 HP: at full HP, against a move that hits once."""
    holds = defender.set.item == FOCUS_SASH or defender.set.ability == STURDY
    return holds and defender.hp_fraction >= 1 and hits == 1


def accuracy(move: data.Move) -> float:
    """The share of the time the move hits: 1 for a move that never misses."""
    return 1.0 if move.accuracy is True else move.accuracy / PERCENT


def can_be_hit(move: data.Move, defender: Fighter) -> bool:
    """Whether the move's type touches the defender at all (Fake Out does nothing to a Ghost)."""
    return defender.set is None or type_effect(move.type, defender.set) > 0


def _hit_label(move: data.Move, defender: Fighter, target: int, hit: Hit) -> str:
    return f"{move.name} -> {_hit_text(target, defender, hit)}"


def _hit_text(target: int, defender: Fighter, hit: Hit) -> str:
    """One hit as a label shows it: whom it hits, its expected damage in percent and its knockout chance."""
    where = f"foe {'AB'[target - 1]}" if target in FOE_TARGETS else "our ally"
    return f"{where} {defender.species}: {hit.expected:.0f}%, KO {hit.ko_chance:.0%}"
