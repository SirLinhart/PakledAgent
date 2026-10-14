"""The model's briefing: the whole situation of a Pokémon decision, as the context its model choice is asked with.

With `[pokemon] model_briefing` on, the context of each model choice is built
here instead of the one-line summary of the rosters or the board. Every
figure in it is one the script already computes: the sets of the draft
memory, their stats, the best damage one set does to another, and in battle
the turn scorer's own speeds and hits.

Each Pokémon is shown as one set line:

    Name [Type/Type] @ item, ability | HP h Atk a Def d SpA sa SpD sd Spe s | moves: Move (Type, power, target), …

with the stats at the set's level (50 for every draft card) and a status
move's power given as `status`. A set no draft card seen in the pool gave
(a card the opponent took before we first saw the pool, or a Pokémon known
only from what an observation shows) is made from its species' facts with an
assumed spread, and is marked `(guessed set)`.

The draft context lists every card left in the pool, both rosters in pick
order and the picks each side has left. The team preview context lists both
sixes and a matchup line for each of the 36 pairs: the best damage each does
to the other with one hit at full HP (`sets.best_damage`, in percent of the
target's HP) and which is faster by its Speed stat.

The battle context gives the field (weather, terrain, Trick Room, and each
side's Tailwind, Reflect, Light Screen and Aurora Veil), then each side: its
two active Pokémon by slot (A, B) with HP, status and stat stages, and its
bench. Our bench is the board's reserves; the opponent's is its drafted
roster less its actives, so it may hold the two it did not bring, and each one
shows the HP last seen of it, or `HP unknown` before it has been seen. The
speed order of the standing actives and the what-beats-what table (each
active's best move at each foe by expected damage, with its knockout chance
and type effectiveness) are the turn scorer's own speeds and hits. It ends,
right before the options the question lists, with the Recent turns section
the caller reads from the battle's protocol log (`recent`), when there is one.

A context is at most `MAX_CONTEXT_CHARS` long, so the prompt it goes into
fits the model's context window with room to spare. A longer one first
leaves out the benched Pokémon's move lists, then the guessed sets' move
lists, the least useful figures first, and says so in a note under its first
line; one still too long after both is cut at the cap with a marker.

With `[maximin] enabled`, each candidate's option description ends with the
opponent's best reply to it and its worst case in words: for a turn, what
each of their actives does, seen from our side (`their A Chi-Yu uses Heat
Wave`, `... at our B Rillaboom`), and the HP each side is expected to lose
and the Pokémon each is expected to lose; for a lineup, the four they bring
and their two leads and the score the matchups come to; for a draft card,
the card they would most likely take next.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from ucla_agent.games.pokemon import damage, data
from ucla_agent.games.pokemon.battle import FOE_TARGETS, MOVE, NO_TARGET, SWITCH, Board, Fighter, Slot
from ucla_agent.games.pokemon.draft import OURS, THEIRS, CardScore, DraftMemory
from ucla_agent.games.pokemon.maximin import MaximinAction, Outcome
from ucla_agent.games.pokemon.preview import MaximinLineup, RosterPokemon
from ucla_agent.games.pokemon.sets import PokemonSet, best_damage, type_effect
from ucla_agent.games.pokemon.turn import Scorer, SlotChoice

# About 8,000 tokens at three characters a token: half the strong role's window, leaving the rest to the question,
# the options, the system message and the answer.
MAX_CONTEXT_CHARS = 24_000
BENCH_MOVES_LEFT_OUT = "(The benched Pokémon's move lists are left out to keep this briefing short.)"
GUESSED_MOVES_LEFT_OUT = "(The guessed sets' move lists are left out to keep this briefing short.)"
CUT_MARKER = "\n(The rest of this briefing is left out to keep it short.)"
GUESSED_MARK = "(guessed set)"
# Each side drafts six cards (pokemon.md, section 4).
ROSTER_SIZE = 6
LIST_ITEM = "- "
NONE = "none"
TIE = "tie"
UNKNOWN_SPECIES = "(species unknown to the data)"
MATCHUPS_TITLE = "Matchups: the best damage of one hit in % of the target's full HP each way, and the faster by Speed"
STAT_LABELS = (("hp", "HP"), ("atk", "Atk"), ("def", "Def"), ("spa", "SpA"), ("spd", "SpD"), ("spe", "Spe"))
BOOST_LABELS = (*STAT_LABELS[1:], ("accuracy", "Acc"), ("evasion", "Eva"))
SLOT_LETTERS = "AB"
EMPTY = "empty"
FAINTED = "fainted"
HP_UNKNOWN = "HP unknown"
BENCH = "bench"
OUR_LABEL, THEIR_LABEL = "Ours", "Theirs"
SPEED_ORDER = "Speed order, fastest first (Speed with stages, Choice Scarf, paralysis and Tailwind):"
SPEED_ORDER_TRICK_ROOM = (
    "Speed order under Trick Room, slowest first (Speed with stages, Choice Scarf, paralysis and Tailwind):"
)
WHAT_BEATS_WHAT_TITLE = (
    "What beats what: each active's best move at each foe by expected damage (in % of the foe's full HP, at most "
    "what it has left), its chance to knock the foe out and its type effectiveness"
)
NO_ATTACK = "no damaging move known"
# The side conditions the briefing names, by their Showdown ids, in the order it names them.
SIDE_CONDITIONS = (
    ("tailwind", "Tailwind"),
    ("reflect", "Reflect"),
    ("lightscreen", "Light Screen"),
    ("auroraveil", "Aurora Veil"),
)
STATUS_WORDS = {
    "brn": "burned",
    "par": "paralyzed",
    "slp": "asleep",
    "frz": "frozen",
    "psn": "poisoned",
    "tox": "badly poisoned",
}
STATUS_POWER = "status"
REPLY_TITLE = "their best reply"
NEXT_PICK_TITLE = "their likely next pick"
WORST_CASE = "worst case"
OUR_WORD, THEIR_WORD = "our", "their"
NO_ITEM = "no item"
UNKNOWN_ABILITY = "unknown ability"
# How a move's Showdown target type reads in a set line; a type not listed reads as itself.
TARGET_WORDS = {
    "normal": "one target",
    "adjacentFoe": "one foe",
    "any": "any one",
    "allAdjacentFoes": "both foes",
    "allAdjacent": "foes and ally",
    "randomNormal": "random foe",
    "self": "self",
    "adjacentAlly": "ally",
    "adjacentAllyOrSelf": "ally or self",
    "allies": "user and ally",
    "allySide": "user's side",
    "allyTeam": "user's team",
    "foeSide": "foes' side",
    "all": "field",
    "scripted": "last attacker",
}


def set_line(pokemon: PokemonSet, *, guessed: bool, with_moves: bool = True) -> str:
    """The set as one line (see the module note); without its move list when `with_moves` is false."""
    types = "/".join(pokemon.types)
    stats = " ".join(f"{label} {pokemon.stat(stat)}" for stat, label in STAT_LABELS)
    parts = [f"{pokemon.record.name} [{types}] @ {pokemon.item or NO_ITEM}, {_ability_name(pokemon)}", stats]
    if with_moves:
        parts.append(f"moves: {', '.join(_move_text(move) for move in pokemon.move_records())}")
    line = " | ".join(parts)
    return f"{line} {GUESSED_MARK}" if guessed else line


def is_guessed(pokemon: PokemonSet, memory: DraftMemory) -> bool:
    """Whether the set was made from facts: no draft card seen in the pool is this set."""
    return pokemon not in memory.cards.values()


@dataclass(frozen=True)
class _Detail:
    """How much of each set a context shows: the draft memory tells the guessed sets apart, and the move lists of
    the benched Pokémon and of the guessed sets may be left out."""

    memory: DraftMemory
    bench_moves: bool = True
    guessed_moves: bool = True

    def line(self, found: PokemonSet, *, benched: bool = False) -> str:
        guessed = is_guessed(found, self.memory)
        with_moves = (self.bench_moves or not benched) and (self.guessed_moves or not guessed)
        return set_line(found, guessed=guessed, with_moves=with_moves)


def _fitted(build: Callable[[_Detail], list[str]], memory: DraftMemory, max_chars: int) -> str:
    """The first of the full context, the one without the bench's move lists and the one without the guessed sets'
    too that is at most `max_chars` long, each move list left out noted under its first line; the last of them cut
    at `max_chars` when none is."""
    stages = (
        (_Detail(memory), None),
        (_Detail(memory, bench_moves=False), BENCH_MOVES_LEFT_OUT),
        (_Detail(memory, bench_moves=False, guessed_moves=False), GUESSED_MOVES_LEFT_OUT),
    )
    notes: list[str] = []
    previous: list[str] = []
    for detail, note in stages:
        lines = build(detail)
        if note is not None and lines != previous:
            notes.append(note)
        previous = lines
        text = "\n".join([lines[0], *notes, *lines[1:]])
        if len(text) <= max_chars:
            return text
    return text[: max_chars - len(CUT_MARKER)] + CUT_MARKER


# -- the draft -------------------------------------------------------------------------------------


def draft_context(memory: DraftMemory, max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """The draft as a model reads it: the pool left, both rosters in pick order and the picks each side has left."""
    return _fitted(lambda detail: _draft_lines(memory, detail), memory, max_chars)


def _draft_lines(memory: DraftMemory, detail: _Detail) -> list[str]:
    pool = list(memory.pool_sets().values())
    return [
        "Draft.",
        f"Pool ({len(pool)} left):",
        *_set_lines(pool, detail),
        *_titled("Our roster:", _set_lines(memory.roster_sets(OURS), detail)),
        *_titled("Their roster:", _set_lines(memory.roster_sets(THEIRS), detail)),
        f"Picks left: ours {_picks_left(memory, OURS)}, theirs {_picks_left(memory, THEIRS)}",
    ]


def _set_lines(sets: Sequence[PokemonSet], detail: _Detail) -> list[str]:
    return [f"{LIST_ITEM}{detail.line(found)}" for found in sets]


def _titled(title: str, lines: list[str]) -> list[str]:
    """A titled list, or the title and `none` on one line when the list is empty."""
    return [title, *lines] if lines else [f"{title} {NONE}"]


def _picks_left(memory: DraftMemory, side: str) -> int:
    return max(0, ROSTER_SIZE - len(memory.rosters[side]))


# -- team preview ----------------------------------------------------------------------------------


def preview_context(
    ours: Sequence[RosterPokemon],
    theirs: Sequence[PokemonSet],
    memory: DraftMemory,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> str:
    """Team preview as a model reads it: both sixes and the matchup of each pair of our and their Pokémon."""
    return _fitted(lambda detail: _preview_lines(ours, theirs, detail), memory, max_chars)


def _preview_lines(ours: Sequence[RosterPokemon], theirs: Sequence[PokemonSet], detail: _Detail) -> list[str]:
    return [
        "Team preview.",
        "Ours:",
        *(f"{LIST_ITEM}{_roster_line(pokemon, detail)}" for pokemon in ours),
        "Theirs:",
        *_set_lines(theirs, detail),
        MATCHUPS_TITLE,
        *(_matchup_line(pokemon.set, foe) for pokemon in ours if pokemon.set is not None for foe in theirs),
    ]


def _roster_line(pokemon: RosterPokemon, detail: _Detail) -> str:
    """One of our six: its set line, or its name as the template spells it when the data knows no such species."""
    return f"{pokemon.name} {UNKNOWN_SPECIES}" if pokemon.set is None else detail.line(pokemon.set)


def _matchup_line(ours: PokemonSet, theirs: PokemonSet) -> str:
    """`Ours X vs Theirs Y: X→Y best%, Y→X best%, faster X|Y|tie`."""
    mine, foe = ours.record.name, theirs.record.name
    our_speed, their_speed = ours.stat("spe"), theirs.stat("spe")
    faster = mine if our_speed > their_speed else foe if their_speed > our_speed else TIE
    return (
        f"Ours {mine} vs Theirs {foe}: {mine}→{foe} {best_damage(ours, theirs):.0f}%, "
        f"{foe}→{mine} {best_damage(theirs, ours):.0f}%, faster {faster}"
    )


# -- battle ----------------------------------------------------------------------------------------


def battle_context(
    board: Board, memory: DraftMemory, scorer: Scorer, max_chars: int = MAX_CONTEXT_CHARS, recent: str = ""
) -> str:
    """A doubles turn as a model reads it: the field, both sides' active and benched Pokémon with their sets, the
    speed order of the actives, what each active does at best to each foe, and last the `recent` turns section when
    it is not empty; `scorer` is the board's."""
    return _fitted(
        lambda detail: [*_battle_lines(board, scorer, detail), *([recent] if recent else [])], memory, max_chars
    )


def _battle_lines(board: Board, scorer: Scorer, detail: _Detail) -> list[str]:
    our_bench = [(fighter.set, fighter) for fighter in board.reserves.values() if fighter.set is not None]
    return [
        f"Turn {board.turn}.",
        _field_line(board),
        "Our side:",
        *_active_lines(board.ours, detail),
        *(_bench_line(found, seen, detail) for found, seen in our_bench),
        "Their side:",
        *_active_lines(board.theirs, detail),
        *(_bench_line(found, seen, detail) for found, seen in _their_bench(board, detail.memory)),
        _speed_order(board, scorer),
        WHAT_BEATS_WHAT_TITLE,
        *_what_beats_what(board, scorer),
    ]


def _field_line(board: Board) -> str:
    trick_room = "up" if board.trick_room else "not up"
    return (
        f"Field: weather {board.weather or NONE}; terrain {board.terrain or NONE}; Trick Room {trick_room}; "
        f"our side {_side_conditions(board.our_conditions)}; their side {_side_conditions(board.their_conditions)}"
    )


def _side_conditions(conditions: frozenset[str]) -> str:
    return ", ".join(name for condition, name in SIDE_CONDITIONS if condition in conditions) or NONE


def _active_lines(fighters: tuple[Fighter | None, Fighter | None], detail: _Detail) -> list[str]:
    """Each position's Pokémon by its slot letter: its condition and set line, or `empty`."""
    return [
        f"{LIST_ITEM}{letter}: {EMPTY if fighter is None else _fighter_text(fighter, fighter.set, detail)}"
        for letter, fighter in zip(SLOT_LETTERS, fighters)
    ]


def _bench_line(found: PokemonSet, seen: Fighter | None, detail: _Detail) -> str:
    return f"{LIST_ITEM}{BENCH}: {_fighter_text(seen, found, detail, benched=True)}"


def _fighter_text(fighter: Fighter | None, found: PokemonSet | None, detail: _Detail, benched: bool = False) -> str:
    """`<condition> | <set line>`; the condition is `HP unknown` for a Pokémon not seen yet."""
    condition = HP_UNKNOWN if fighter is None else _condition(fighter)
    shown = f"{fighter.species} {UNKNOWN_SPECIES}" if found is None else detail.line(found, benched=benched)
    return f"{condition} | {shown}"


def _condition(fighter: Fighter) -> str:
    """`fainted`, or its HP in percent with its status and any stat stage that is not 0."""
    if not fighter.standing:
        return FAINTED
    parts = [f"HP {fighter.hp_fraction:.0%}"]
    if fighter.status:
        parts.append(STATUS_WORDS.get(fighter.status, fighter.status))
    stages = [f"{label} {fighter.boosts[stat]:+d}" for stat, label in BOOST_LABELS if fighter.boosts.get(stat, 0)]
    if stages:
        parts.append(f"boosts {' '.join(stages)}")
    return ", ".join(parts)


def _their_bench(board: Board, memory: DraftMemory) -> list[tuple[PokemonSet, Fighter | None]]:
    """The opponent's Pokémon off the field, each with what was last seen of it (None before it was seen): its
    drafted roster less its actives, then any Pokémon seen that the roster does not hold."""
    on_field = {fighter.set for fighter in board.theirs if fighter is not None}
    seen = {fighter.set: fighter for fighter in board.their_seen.values() if fighter.set is not None}
    roster = memory.roster_sets(THEIRS)
    benched = [(found, seen.get(found)) for found in roster if found not in on_field]
    return benched + [
        (found, fighter) for found, fighter in seen.items() if found not in on_field and found not in roster
    ]


def _standing(side: str, fighters: tuple[Fighter | None, Fighter | None]) -> list[tuple[str, Fighter]]:
    """One side's standing actives, each with its label (`Ours A Incineroar`)."""
    return [
        (f"{side} {letter} {_name(fighter)}", fighter)
        for letter, fighter in zip(SLOT_LETTERS, fighters)
        if fighter is not None and fighter.standing
    ]


def _name(fighter: Fighter) -> str:
    return fighter.species if fighter.set is None else fighter.set.record.name


def _speed_order(board: Board, scorer: Scorer) -> str:
    """The standing actives in the order they move: fastest first, slowest first under Trick Room."""
    actives = [*_standing(OUR_LABEL, board.ours), *_standing(THEIR_LABEL, board.theirs)]
    ordered = sorted(actives, key=lambda active: scorer.speed(active[1]), reverse=not board.trick_room)
    title = SPEED_ORDER_TRICK_ROOM if board.trick_room else SPEED_ORDER
    return f"{title} {', '.join(f'{label} {scorer.speed(fighter):.0f}' for label, fighter in ordered) or NONE}"


def _what_beats_what(board: Board, scorer: Scorer) -> list[str]:
    """One line per ordered pair of opposing standing actives, ours at theirs first."""
    ours, theirs = _standing(OUR_LABEL, board.ours), _standing(THEIR_LABEL, board.theirs)
    pairs = [*((mine, foe) for mine in ours for foe in theirs), *((foe, mine) for foe in theirs for mine in ours)]
    return [_best_attack_line(attacker, defender, scorer) for attacker, defender in pairs]


def _best_attack_line(attacker: tuple[str, Fighter], defender: tuple[str, Fighter], scorer: Scorer) -> str:
    """`X → Y: Move, expected d%, KO k%, ×e`, or that X has no damaging move known."""
    (attacker_label, attacking), (defender_label, defending) = attacker, defender
    best = scorer.best_attack(attacking, defending)
    if best is None:
        return f"{LIST_ITEM}{attacker_label} → {defender_label}: {NO_ATTACK}"
    move, hit = best
    effect = type_effect(move.type, defending.set)
    return (
        f"{LIST_ITEM}{attacker_label} → {defender_label}: {move.name}, expected {hit.expected:.0f}%, "
        f"KO {hit.ko_chance:.0%}, ×{effect:g}"
    )


# -- set lines -------------------------------------------------------------------------------------


def _ability_name(pokemon: PokemonSet) -> str:
    """The ability as the species' data names it (`Intimidate`); its id for one the species does not list."""
    if not pokemon.ability:
        return UNKNOWN_ABILITY
    return next((name for name in pokemon.record.abilities if data.to_id(name) == pokemon.ability), pokemon.ability)


def _move_text(move: data.Move) -> str:
    power = STATUS_POWER if move.category == damage.STATUS else str(move.base_power)
    return f"{move.name} ({move.type}, {power}, {TARGET_WORDS.get(move.target, move.target)})"


# -- maximin: each candidate's best reply ----------------------------------------------------------


def turn_reply(weighed: MaximinAction, their_board: Board) -> str:
    """The opponent's best reply to one of our joint actions, seen from our side, and its worst case in words;
    `their_board` is the board as the opponent sees it, the one their reply was chosen on."""
    choices = "; ".join(
        _their_choice(slot, choice, their_board) for slot, choice in zip(their_board.slots, weighed.reply.slots)
    )
    return f"{REPLY_TITLE}: {choices}; {WORST_CASE}: {_outcome_words(weighed.outcome)}"


def _their_choice(slot: Slot, choice: SlotChoice, their_board: Board) -> str:
    """`their A Chi-Yu uses Heat Wave`, `... at our B Rillaboom`, `... switches to Garchomp` or `... passes`."""
    fighter = their_board.own(slot)
    actor = f"{THEIR_WORD} {SLOT_LETTERS[slot.index]}" + ("" if fighter is None else f" {_name(fighter)}")
    picked = choice.choice
    if picked["type"] == SWITCH:
        return f"{actor} switches to {_species_name(picked['species'])}"
    if picked["type"] != MOVE:
        return f"{actor} passes"
    move = their_board.move(picked["move_id"])
    used = f"{actor} uses {picked['move_id'] if move is None else move.name}"
    target = picked["target"]
    return used if target == NO_TARGET else f"{used} at {_reply_target(target, their_board)}"


def _reply_target(target: int, their_board: Board) -> str:
    """A target as the opponent numbers it (our positions 1 and 2, theirs -1 and -2), named from our side."""
    side, letter = (
        (OUR_WORD, SLOT_LETTERS[target - 1]) if target in FOE_TARGETS else (THEIR_WORD, SLOT_LETTERS[-target - 1])
    )
    fighter = their_board.at(target)
    return f"{side} {letter}" + ("" if fighter is None else f" {_name(fighter)}")


def _species_name(species: str) -> str:
    record = data.species(species)
    return species if record is None else record.name


def _outcome_words(outcome: Outcome) -> str:
    return (
        f"we deal {outcome.dealt:.0f}% and take {outcome.taken:.0f}% of HP, with {outcome.knocked_out:.1f} of theirs "
        f"and {outcome.lost:.1f} of ours expected to faint"
    )


def lineup_reply(weighed: MaximinLineup) -> str:
    """The opponent's lineup that does best against one of ours, and the score the matchups then come to."""
    return (
        f"{REPLY_TITLE}: {weighed.reply.description()}; {WORST_CASE}: the matchups come to {weighed.worst:+.0f} "
        "(our best damage less theirs, in %)"
    )


def draft_reply(score: CardScore) -> str:
    """The card the opponent would most likely take next once we take this one."""
    if score.their_next is None:
        return f"{NEXT_PICK_TITLE}: {NONE}"
    name = score.their_next.card.record.name
    return f"{NEXT_PICK_TITLE}: {name}; {WORST_CASE}: they take {name}, the card left that adds most to their roster"
