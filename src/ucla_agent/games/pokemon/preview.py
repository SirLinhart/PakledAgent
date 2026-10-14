"""Team preview (pokemon.md, section 6): which 4 of our 6 to bring and which 2 of them to lead.

Our six are the species the `select_lineup` template's `roster` lists, spelled
as it spells them; each one's set comes from the draft memory, else from the
facts `your_roster` shows for it, else from its species alone. The
opponent's six are `opponent_roster`'s species, each with its drafted set
from the draft memory, else the facts the entry shows; with no
`opponent_roster` at all, the opponent's drafted roster stands in for it.

Each of our Pokémon's matchup is the sum, over the opponent's six, of the
best damage it does to each minus the best damage each does to it. All 15
ways to bring 4 and the 6 lead pairs of each (90 lineups) are scored: the
matchups of the four brought, plus `[preview].lead_weight` times the
matchups of the two leads, plus `[preview].speed_control_bonus` for each lead
with Tailwind, Trick Room or Fake Out. Best first.

With `[maximin] enabled`, each of our 90 lineups is also scored against each
of the opponent's 90 (4 of their six known sets, 2 of them leading; all they
have when fewer than four are known), with the same scoring and their lineup
standing in for their six: the matchups of our four against their four, plus
`lead_weight` times our two leads' against their two leads, plus
`speed_control_bonus` per lead of ours with speed control less the same per
lead of theirs. Our lineups are ranked by their worst score over the
opponent's lineups, ties by the mean, each with the lineup of theirs that
gave the worst: their best reply. The sums are taken once per group of four
and pair of leads, so the 8,100 pairs take a few milliseconds; past the time
limit it is given, the ranking gives nothing and the score against their six
stands.
"""

from __future__ import annotations

import itertools
import statistics
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ucla_agent.games.pokemon import data
from ucla_agent.games.pokemon.draft import OURS, THEIRS, DraftMemory
from ucla_agent.games.pokemon.observe import Reader, UnexpectedState
from ucla_agent.games.pokemon.sets import FAKE_OUT, TAILWIND, TRICK_ROOM, PokemonSet, best_damage, from_facts
from ucla_agent.games.pokemon.tuning import PreviewTuning

LINEUP_TYPE = "select_lineup"
BRING_COUNT = 4
LEAD_COUNT = 2
SPEED_CONTROL_MOVES = frozenset({TAILWIND, TRICK_ROOM, FAKE_OUT})
# Keys a roster entry may leave out; when there, they have to be of these kinds.
OPTIONAL_ENTRY_KEYS = {"types": list, "base_stats": dict}


@dataclass(frozen=True)
class RosterPokemon:
    """One of our six: its name as the template's roster spells it, and its set (None for a species unknown to the
    data)."""

    name: str
    set: PokemonSet | None


@dataclass(frozen=True)
class Lineup:
    """Four to bring and two of them to lead, spelled as the template's roster spells them, with the score."""

    bring: tuple[str, ...]
    leads: tuple[str, ...]
    score: float

    def action(self) -> dict[str, Any]:
        """The structured `select_lineup` action."""
        return {"type": LINEUP_TYPE, "bring": list(self.bring), "leads": list(self.leads)}

    def description(self, *, with_score: bool = True) -> str:
        """The lineup as a model reads it: the four brought and the two leads, and the script's score `with_score`."""
        text = f"bring {', '.join(self.bring)}; lead {' and '.join(self.leads)}"
        return f"{text}; score {self.score:.0f}" if with_score else text


def read_rosters(
    observation: Any, template: Mapping[str, Any], memory: DraftMemory, reader: Reader
) -> tuple[list[RosterPokemon], list[PokemonSet]]:
    """Our six from the template's roster and the opponent's known sets (see the module note).

    Raises `UnexpectedState` when the template's roster does not name at least 4 different species.
    """
    roster = template.get("roster")
    names = list(dict.fromkeys(name for name in roster if isinstance(name, str))) if isinstance(roster, list) else []
    if len(names) < BRING_COUNT:
        raise UnexpectedState(f"the select_lineup template's roster names {len(names)} species, not 6")
    our_facts = _facts_by_species(reader.value(observation, "your_roster", list, []), reader)
    ours = [RosterPokemon(name, _our_set(name, memory, our_facts)) for name in names]
    return ours, _their_sets(observation, memory, reader)


def _our_set(name: str, memory: DraftMemory, facts: Mapping[str, PokemonSet]) -> PokemonSet | None:
    species = data.to_id(name)
    return memory.set_for_species(OURS, species) or facts.get(species) or from_facts(species)


def _their_sets(observation: Any, memory: DraftMemory, reader: Reader) -> list[PokemonSet]:
    """The opponent's sets: each `opponent_roster` species' drafted set, else its facts; else the drafted roster."""
    entries = reader.value(observation, "opponent_roster", list, None)
    if entries is None:
        return memory.roster_sets(THEIRS)
    facts = _facts_by_species(entries, reader)
    drafted = (memory.set_for_species(THEIRS, species) or found for species, found in facts.items())
    return [found for found in drafted if found is not None]


def _facts_by_species(entries: Sequence[Any], reader: Reader) -> dict[str, PokemonSet]:
    """The set each roster entry's facts give, by species id; an entry without a usable species is left out."""
    found = {}
    for entry in entries:
        species = reader.value(entry, "species", str, None)
        _check_optional_keys(entry, reader)
        made = _set_from_entry(entry, species)
        if made is not None:
            found[made.species] = made
    return found


def _check_optional_keys(entry: Any, reader: Reader) -> None:
    for key, kind in OPTIONAL_ENTRY_KEYS.items():
        if isinstance(entry, Mapping) and key in entry and not isinstance(entry[key], kind):
            reader.note(key)


def _set_from_entry(entry: Any, species: str | None) -> PokemonSet | None:
    if species is None:
        return None
    return from_facts(
        species,
        item=entry.get("item"),
        ability=entry.get("ability"),
        moves=entry.get("moves"),
        level=entry.get("level"),
    )


def matchup(ours: PokemonSet | None, theirs: Sequence[PokemonSet]) -> float:
    """The sum over their sets of the best damage ours does to each minus the best each does to ours; 0 unknown."""
    if ours is None:
        return 0.0
    return sum(best_damage(ours, foe) - best_damage(foe, ours) for foe in theirs)


def rank_lineups(ours: Sequence[RosterPokemon], theirs: Sequence[PokemonSet], tuning: PreviewTuning) -> list[Lineup]:
    """Every lineup of 4 brought and 2 leads among them, scored (see the module note), best first."""
    matchups = {pokemon.name: matchup(pokemon.set, theirs) for pokemon in ours}
    control = {pokemon.name: _has_speed_control(pokemon.set) for pokemon in ours}
    lineups = []
    for bring in itertools.combinations(matchups, BRING_COUNT):
        bring_score = sum(matchups[name] for name in bring)
        for leads in itertools.combinations(bring, LEAD_COUNT):
            lead_score = tuning.lead_weight * sum(matchups[name] for name in leads)
            control_score = tuning.speed_control_bonus * sum(control[name] for name in leads)
            lineups.append(Lineup(bring, leads, bring_score + lead_score + control_score))
    return sorted(lineups, key=lambda lineup: lineup.score, reverse=True)


def _has_speed_control(pokemon: PokemonSet | None) -> bool:
    return pokemon is not None and pokemon.has_move(SPEED_CONTROL_MOVES)


@dataclass(frozen=True)
class TheirLineup:
    """Four (or all they have) of the opponent's known Pokémon brought and two of them leading, by name."""

    bring: tuple[str, ...]
    leads: tuple[str, ...]

    def description(self) -> str:
        """The lineup as a model reads it: the Pokémon brought and the leads."""
        return f"bring {', '.join(self.bring)}; lead {' and '.join(self.leads)}"


@dataclass(frozen=True)
class MaximinLineup:
    """One of our lineups (its score its worst) with its worst and mean score over the opponent's lineups and the
    lineup of theirs that gave the worst."""

    lineup: Lineup
    worst: float
    mean: float
    reply: TheirLineup


@dataclass(frozen=True)
class LineupTable:
    """What lineup scoring needs of both sides: names, the matchup of each of ours (rows) against each of theirs
    (columns), and which of each side's Pokémon have speed control."""

    our_names: tuple[str, ...]
    their_names: tuple[str, ...]
    matchups: tuple[tuple[float, ...], ...]
    our_control: tuple[bool, ...]
    their_control: tuple[bool, ...]

    @classmethod
    def of(cls, ours: Sequence[RosterPokemon], theirs: Sequence[PokemonSet]) -> LineupTable:
        """The table of our six against the opponent's known sets."""
        return cls(
            our_names=tuple(pokemon.name for pokemon in ours),
            their_names=tuple(foe.record.name for foe in theirs),
            matchups=tuple(tuple(matchup(pokemon.set, [foe]) for foe in theirs) for pokemon in ours),
            our_control=tuple(_has_speed_control(pokemon.set) for pokemon in ours),
            their_control=tuple(_has_speed_control(foe) for foe in theirs),
        )


def rank_lineups_maximin(table: LineupTable, tuning: PreviewTuning, until: float) -> list[MaximinLineup] | None:
    """Our lineups by their worst score against the opponent's lineups, ties by the mean (see the module note);
    None when the opponent has no known set or the time limit `until` (a `time.perf_counter()` moment) passes."""
    if not table.their_names:
        return None
    their_bring = min(BRING_COUNT, len(table.their_names))
    our_groups, their_groups = _groups(len(table.our_names), BRING_COUNT), _groups(len(table.their_names), their_bring)
    our_pairs = _groups(len(table.our_names), LEAD_COUNT)
    their_pairs = _groups(len(table.their_names), min(LEAD_COUNT, their_bring))
    brought = {(ours, theirs): _sum(table, ours, theirs) for ours in our_groups for theirs in their_groups}
    leading = {(ours, theirs): _lead_score(table, ours, theirs, tuning) for ours in our_pairs for theirs in their_pairs}
    replies = [(group, pair) for group in their_groups for pair in itertools.combinations(group, len(their_pairs[0]))]
    ranked = []
    for group in our_groups:
        if time.perf_counter() > until:
            return None
        for pair in itertools.combinations(group, LEAD_COUNT):
            scores = [brought[group, theirs] + leading[pair, their_leads] for theirs, their_leads in replies]
            ranked.append(_weighed(table, (group, pair), scores, replies))
    return sorted(ranked, key=lambda weighed: (weighed.worst, weighed.mean), reverse=True)


def _groups(size: int, count: int) -> list[tuple[int, ...]]:
    return list(itertools.combinations(range(size), count))


def _sum(table: LineupTable, ours: tuple[int, ...], theirs: tuple[int, ...]) -> float:
    """The matchups of a group of ours against a group of theirs, summed."""
    return sum(table.matchups[mine][foe] for mine in ours for foe in theirs)


def _lead_score(table: LineupTable, ours: tuple[int, ...], theirs: tuple[int, ...], tuning: PreviewTuning) -> float:
    """Our leads' score against theirs: their matchups times `lead_weight`, and our speed control less theirs."""
    control = sum(table.our_control[mine] for mine in ours) - sum(table.their_control[foe] for foe in theirs)
    return tuning.lead_weight * _sum(table, ours, theirs) + tuning.speed_control_bonus * control


def _weighed(
    table: LineupTable,
    lineup: tuple[tuple[int, ...], tuple[int, ...]],
    scores: Sequence[float],
    replies: Sequence[tuple[tuple[int, ...], tuple[int, ...]]],
) -> MaximinLineup:
    worst = min(range(len(scores)), key=scores.__getitem__)
    (group, pair), (their_group, their_pair) = lineup, replies[worst]
    ours = Lineup(_named(table.our_names, group), _named(table.our_names, pair), scores[worst])
    reply = TheirLineup(_named(table.their_names, their_group), _named(table.their_names, their_pair))
    return MaximinLineup(ours, scores[worst], statistics.fmean(scores), reply)


def _named(names: Sequence[str], indexes: Sequence[int]) -> tuple[str, ...]:
    return tuple(names[index] for index in indexes)
