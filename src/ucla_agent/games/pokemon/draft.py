"""The draft (pokemon.md, section 4): what was seen of the pool and both rosters, and the scored pick.

`DraftMemory` keeps, from every draft observation, the full set of every
card seen in `available_cards` by its `card_id`, and both seats' rosters from
`rosters` (or, when that is missing, from `picks`): our seat is the one whose
id is our agent id, the other is the opponent. Team preview and the battle
read both sides' sets from it. A card the opponent took before we first saw
the pool is known only by its species, and stands as the set the species'
facts give (`sets.from_facts`).

At our pick each offered card is scored, with the weights of `[draft]`:

- power: the mean, over the other cards left in the pool and the opponent's
  roster, of the best damage the card does to each (capped at 100);
- bulk: the mean over the same cards of 100 minus the best damage each does
  to it;
- speed: its Speed stat relative to the median of every card seen;
- support: fixed bonuses for Fake Out, Tailwind, Trick Room, Follow Me or
  Rage Powder, Intimidate, Protect and a damaging spread move;
- synergy: a bonus per type that two or more of our roster are weak to and
  the card resists, and per damage type it adds to our roster's moves; a
  malus per weakness it shares with three or more of our roster;
- denial: the same score (without denial) as if for the opponent's roster,
  times `denial_weight`; 0 when the Item Clause keeps the card from them.

The highest total is picked; ties keep the offered order. The public reason
names the card's two largest components in one sentence. Only the public
draft state is used.

With `[maximin]` `enabled` and `draft_lookahead` on, each card also looks one
pick ahead (`score_cards_lookahead`): the opponent's next pick is the card of
the pool with the highest total for them (the same score from their side,
against our roster, a card whose item they already hold left out by the Item
Clause). A lookahead component is added: the best total their next pick could
reach now, less the best it could reach from the pool left once we take the
card (our roster then holding it). So a card that takes nothing they want
costs nothing, and the card they want most gains the gap to their second
best: today's score less the best their next pick could gain, counted from
what they could gain now, which ranks the cards the same way. Each card keeps
their likely next pick after it. With no card left, no pick left to them or
the time limit passed, there is nothing to add.
"""

from __future__ import annotations

import dataclasses
import functools
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from altruagent import LegalAction

from ucla_agent.games.pokemon import data
from ucla_agent.games.pokemon.observe import Reader
from ucla_agent.games.pokemon.sets import (
    FAKE_OUT,
    INTIMIDATE,
    PERCENT_CAP,
    PROTECT_MOVES,
    REDIRECTION_MOVES,
    SPREAD_TARGETS,
    TAILWIND,
    TRICK_ROOM,
    PokemonSet,
    best_damage,
    from_card,
    from_facts,
    is_damaging,
    type_effect,
)
from ucla_agent.games.pokemon.tuning import DraftTuning

PICK_TYPE = "draft_pick"
PICK_ACTION_PREFIX = f"{PICK_TYPE}:"
OURS, THEIRS = "ours", "theirs"
POWER, BULK, SPEED, SUPPORT, SYNERGY, DENIAL = "power", "bulk", "speed", "support", "synergy", "denial"
LOOKAHEAD = "lookahead"
# Each side drafts six cards (pokemon.md, section 4).
ROSTER_SIZE = 6
REASON_COMPONENTS = 2
MAX_REASON_CHARS = 120
# A roster weak to one type in two members wants a card that resists it; in three, a card weak to it hurts.
COVER_WEAKNESS_COUNT = 2
SHARED_WEAKNESS_COUNT = 3
NOT_WEAK = 1.0


class DraftMemory:
    """The draft as seen so far in one match: every card's full set by card id, and both seats' rosters."""

    def __init__(self) -> None:
        self.cards: dict[str, PokemonSet] = {}
        self.species: dict[str, str] = {}
        self.available: list[str] = []
        self.rosters: dict[str, list[str]] = {OURS: [], THEIRS: []}
        self.our_seat: str | None = None

    def update(self, observation: Any, our_seat: str | None, reader: Reader) -> None:
        """Take in one draft observation. `our_seat` is our agent id; when unknown, the seat whose pick it is.

        Defaults: no `available_cards` -> no new card; no `available_card_ids` -> the ids `available_cards`
        lists; no `current_seat` -> none; no `rosters` -> the rosters `picks` gives; no `picks` -> none.
        """
        listed_cards = [self._remember_card(entry) for entry in reader.value(observation, "available_cards", list, [])]
        current = reader.value(observation, "current_seat", (str, type(None)), None)
        self.our_seat = our_seat or self.our_seat or current
        self._take_rosters(_rosters(observation, reader))
        listed = reader.value(observation, "available_card_ids", list, listed_cards)
        self.available = [card_id for card_id in listed if isinstance(card_id, str)]

    def _remember_card(self, entry: Any) -> str | None:
        """Keep a card of `available_cards`; its id, None for an entry that is no card."""
        card = from_card(entry)
        card_id = entry.get("card_id") if card is not None else None
        if isinstance(card_id, str):
            self.cards[card_id] = card
            self.species[card_id] = card.species
        return card_id

    def _take_rosters(self, rosters: Mapping[Any, Any]) -> None:
        for seat, entries in rosters.items():
            side = OURS if seat == self.our_seat else THEIRS
            self.rosters[side] = self._roster_ids(entries)

    def _roster_ids(self, entries: Any) -> list[str]:
        """The card ids of a roster's entries (`{"card_id", "species"}`), remembering each one's species."""
        ids = []
        for entry in entries if isinstance(entries, list) else []:
            card_id, species = _card_and_species(entry)
            if card_id is not None:
                ids.append(card_id)
                self.species.setdefault(card_id, species)
        return ids

    def set_of(self, card_id: str) -> PokemonSet | None:
        """The card's full set when it was seen in the pool, else the set its species' facts give."""
        return self.cards.get(card_id) or from_facts(self.species.get(card_id))

    def roster_sets(self, side: str) -> list[PokemonSet]:
        """The sets of one side's roster (`OURS` or `THEIRS`), in pick order."""
        return [card for card in (self.set_of(card_id) for card_id in self.rosters[side]) if card is not None]

    def pool_sets(self) -> dict[str, PokemonSet]:
        """The sets of the cards still in the pool, by card id."""
        return {card_id: self.cards[card_id] for card_id in self.available if card_id in self.cards}

    def set_for_species(self, side: str, species: str) -> PokemonSet | None:
        """The set of one side's Pokémon of this species id (a forme of it counts); None when the side has none."""
        roster = self.roster_sets(side)
        exact = next((card for card in roster if card.species == species), None)
        return exact or next((card for card in roster if _same_line(card.species, species)), None)


def _card_and_species(entry: Any) -> tuple[str | None, str | None]:
    if not isinstance(entry, Mapping) or not isinstance(entry.get("card_id"), str):
        return None, None
    species = entry.get("species")
    return entry["card_id"], data.to_id(species) if isinstance(species, str) else None


def _rosters(observation: Any, reader: Reader) -> Mapping[Any, Any]:
    """Each seat's roster entries: `rosters`, else as `picks` lists them."""
    picks = reader.value(observation, "picks", list, [])
    rosters = reader.value(observation, "rosters", dict, None)
    return rosters if rosters is not None else _rosters_from_picks(picks)


def _rosters_from_picks(picks: Sequence[Any]) -> dict[str, list[Any]]:
    """Each seat's roster entries as `picks` lists them, for an observation without `rosters`."""
    rosters: dict[str, list[Any]] = {}
    for pick in picks:
        if isinstance(pick, Mapping) and isinstance(pick.get("seat_id"), str):
            rosters.setdefault(pick["seat_id"], []).append(pick)
    return rosters


def _same_line(known: str, seen: str) -> bool:
    """Whether two species ids are one Pokémon in two formes (`terapagos`, `terapagosterastal`)."""
    return known.startswith(seen) or seen.startswith(known)


def offered_card_ids(legal_actions: Sequence[LegalAction]) -> list[str]:
    """The card ids the legal actions offer: the action's `card_id`, else the id after `draft_pick:`."""
    offered = (_offered_card_id(action) for action in legal_actions)
    return [card_id for card_id in offered if card_id is not None]


def _offered_card_id(action: LegalAction) -> str | None:
    picked = action.input.get("action") if isinstance(action.input, Mapping) else None
    card_id = picked.get("card_id") if isinstance(picked, Mapping) else None
    if isinstance(card_id, str):
        return card_id
    return (
        action.action_id.removeprefix(PICK_ACTION_PREFIX) if action.action_id.startswith(PICK_ACTION_PREFIX) else None
    )


@dataclass(frozen=True)
class NextPick:
    """The card the opponent would most likely take next, and its total for them."""

    card_id: str
    card: PokemonSet
    score: float


@dataclass(frozen=True)
class CardScore:
    """One offered card's score: its components (each already weighted), what earned support and synergy, and with
    the lookahead the opponent's likely next pick once we take it (None when they have none)."""

    card_id: str
    card: PokemonSet
    components: Mapping[str, float]
    support_note: str
    synergy_note: str
    their_next: NextPick | None = None

    @property
    def total(self) -> float:
        return sum(self.components.values())

    def top_components(self) -> list[str]:
        """The names of its largest components, largest first, as many as the reason names."""
        ranked = sorted(self.components, key=lambda name: self.components[name], reverse=True)
        return ranked[:REASON_COMPONENTS]

    def phrase(self, component: str) -> str:
        """How the reason words one component."""
        return {
            POWER: "hits the pool hard",
            BULK: "takes hits well",
            SPEED: "outspeeds most of the pool",
            SUPPORT: f"{self.support_note} support",
            SYNERGY: self.synergy_note,
            DENIAL: "keeps it from the opponent",
            LOOKAHEAD: "leaves the opponent a weaker next pick",
        }[component]

    def reason(self) -> str:
        """The pick's public reason: one sentence naming its two largest components, at most 120 characters."""
        sentence = f"{self.card.record.name}: {' and '.join(self.phrase(name) for name in self.top_components())}."
        return sentence[:MAX_REASON_CHARS]

    def description(self, *, with_types_and_speed: bool = False) -> str:
        """The card as a model reads it: species, item, ability, moves and the score's components; its types and
        Speed stat beside the species `with_types_and_speed`."""
        moves = "/".join(move.name for move in self.card.move_records())
        parts = ", ".join(f"{name} {value:.0f}" for name, value in self.components.items())
        name = self.card.record.name
        if with_types_and_speed:
            name = f"{name} [{'/'.join(self.card.types)}] Spe {self.card.stat('spe')}"
        return (
            f"{name} @ {self.card.item or 'no item'}, {self.card.ability or 'unknown ability'}; "
            f"{moves}; score {self.total:.0f} ({parts})"
        )


def score_cards(memory: DraftMemory, offered_ids: Sequence[str], tuning: DraftTuning) -> list[CardScore]:
    """The offered cards that are in the pool, scored (see the module note), best first."""
    pool = memory.pool_sets()
    ours, theirs = memory.roster_sets(OURS), memory.roster_sets(THEIRS)
    scorable = [card_id for card_id in offered_ids if card_id in pool]
    if not scorable:
        return []
    median_speed = _median_speed(pool, ours, theirs)
    scores = [_score(card_id, pool, ours, theirs, median_speed, tuning) for card_id in scorable]
    return sorted(scores, key=lambda score: score.total, reverse=True)


def _median_speed(pool: Mapping[str, PokemonSet], ours: list[PokemonSet], theirs: list[PokemonSet]) -> float:
    return statistics.median(card.stat("spe") for card in [*pool.values(), *ours, *theirs])


def score_cards_lookahead(
    memory: DraftMemory, offered_ids: Sequence[str], tuning: DraftTuning, until: float
) -> list[CardScore] | None:
    """The offered cards scored with the lookahead component and the opponent's likely next pick after each (see the
    module note), best first; None when the time limit `until` (a `time.perf_counter()` moment) passes first."""
    scores = score_cards(memory, offered_ids, tuning)
    if not scores or len(memory.rosters[THEIRS]) >= ROSTER_SIZE:
        return scores
    pool = memory.pool_sets()
    ours, theirs = memory.roster_sets(OURS), memory.roster_sets(THEIRS)
    median_speed = _median_speed(pool, ours, theirs)
    now = _their_next(pool, ours, theirs, median_speed, tuning)
    looked = []
    for score in scores:
        if time.perf_counter() > until:
            return None
        left = {card_id: card for card_id, card in pool.items() if card_id != score.card_id}
        after = _their_next(left, [*ours, score.card], theirs, median_speed, tuning)
        gain = _total_of(now) - _total_of(after)
        looked.append(dataclasses.replace(score, components={**score.components, LOOKAHEAD: gain}, their_next=after))
    return sorted(looked, key=lambda score: score.total, reverse=True)


def _their_next(
    pool: Mapping[str, PokemonSet],
    ours: list[PokemonSet],
    theirs: list[PokemonSet],
    median_speed: float,
    tuning: DraftTuning,
) -> NextPick | None:
    """The card of the pool the opponent would most like next (the Item Clause respected), with its total for them;
    None when they may take none."""
    held = {card.item for card in theirs}
    allowed = [card_id for card_id, card in pool.items() if card.item not in held]
    scored = [(_score(card_id, pool, theirs, ours, median_speed, tuning).total, card_id) for card_id in allowed]
    if not scored:
        return None
    total, card_id = max(scored, key=lambda pair: pair[0])
    return NextPick(card_id, pool[card_id], total)


def _total_of(pick: NextPick | None) -> float:
    return 0.0 if pick is None else pick.score


def _score(
    card_id: str,
    pool: Mapping[str, PokemonSet],
    ours: list[PokemonSet],
    theirs: list[PokemonSet],
    median_speed: float,
    tuning: DraftTuning,
) -> CardScore:
    """One card's score for joining our roster, with its denial: the same score for joining theirs, weighted."""
    card = pool[card_id]
    others = [other for other_id, other in pool.items() if other_id != card_id]
    own, support_note, synergy_note = _components(card, ours, [*others, *theirs], median_speed, tuning)
    rival = _components(card, theirs, [*others, *ours], median_speed, tuning)[0]
    kept_from_them = card.item in {held.item for held in theirs}
    own[DENIAL] = 0.0 if kept_from_them else tuning.denial_weight * sum(rival.values())
    return CardScore(card_id, card, own, support_note, synergy_note)


def _components(
    card: PokemonSet, roster: list[PokemonSet], targets: list[PokemonSet], median_speed: float, tuning: DraftTuning
) -> tuple[dict[str, float], str, str]:
    """The card's components other than denial for joining `roster` against `targets`, with the support and
    synergy notes."""
    support, support_note = _support(card, tuning)
    synergy, synergy_note = _synergy(card, roster, tuning)
    components = {
        POWER: tuning.power_weight * _mean(best_damage(card, target) for target in targets),
        BULK: tuning.bulk_weight * _mean(PERCENT_CAP - best_damage(target, card) for target in targets),
        SPEED: tuning.speed_weight * (card.stat("spe") / median_speed - 1),
        SUPPORT: support,
        SYNERGY: synergy,
    }
    return components, support_note, synergy_note


def _mean(values: Iterable[float]) -> float:
    listed = list(values)
    return statistics.fmean(listed) if listed else 0.0


def _support(card: PokemonSet, tuning: DraftTuning) -> tuple[float, str]:
    """The card's support bonuses and the name of the largest one (`""` when it has none)."""
    features = (
        (card.has_move([FAKE_OUT]), tuning.fake_out_bonus, "Fake Out"),
        (card.has_move([TAILWIND]), tuning.tailwind_bonus, "Tailwind"),
        (card.has_move([TRICK_ROOM]), tuning.trick_room_bonus, "Trick Room"),
        (card.has_move(REDIRECTION_MOVES), tuning.redirection_bonus, "redirection"),
        (card.ability == INTIMIDATE, tuning.intimidate_bonus, "Intimidate"),
        (card.has_move(PROTECT_MOVES), tuning.protect_bonus, "Protect"),
        (_has_spread_attack(card), tuning.spread_move_bonus, "spread move"),
    )
    earned = [(bonus, name) for present, bonus, name in features if present]
    return sum(bonus for bonus, _name in earned), max(earned, default=(0.0, ""))[1]


def _has_spread_attack(card: PokemonSet) -> bool:
    return any(move.target in SPREAD_TARGETS and is_damaging(move) for move in card.move_records())


def _synergy(card: PokemonSet, roster: list[PokemonSet], tuning: DraftTuning) -> tuple[float, str]:
    """The card's synergy with a roster (see the module note) and how the reason words it."""
    weak = Counter(attack for member in roster for attack in attack_types() if _weak(member, attack))
    covered = [attack for attack in weak if weak[attack] >= COVER_WEAKNESS_COUNT and _resists(card, attack)]
    shared = [attack for attack in weak if weak[attack] >= SHARED_WEAKNESS_COUNT and _weak(card, attack)]
    added = sorted(_damage_types(card).difference(*(_damage_types(member) for member in roster)))
    points = (
        tuning.resist_bonus * len(covered)
        + tuning.new_type_bonus * len(added)
        - tuning.shared_weakness_malus * len(shared)
    )
    return points, _synergy_note(covered, added)


def _resists(card: PokemonSet, attack: str) -> bool:
    return type_effect(attack, card) < NOT_WEAK


def _weak(card: PokemonSet, attack: str) -> bool:
    return type_effect(attack, card) > NOT_WEAK


def _synergy_note(covered: list[str], added: list[str]) -> str:
    if covered:
        return f"covers our {covered[0]} weakness"
    return f"adds {added[0]} coverage" if added else "fits our roster"


def _damage_types(card: PokemonSet) -> set[str]:
    return {move.type for move in card.move_records() if is_damaging(move)}


@functools.cache
def attack_types() -> tuple[str, ...]:
    """Every type a move can have, as the data names them (`Fire`)."""
    return tuple(type_id.capitalize() for type_id in data.shipped().type_chart)


def pick_action(score: CardScore) -> dict[str, str]:
    """The structured draft pick for a scored card."""
    return {"type": PICK_TYPE, "card_id": score.card_id}
