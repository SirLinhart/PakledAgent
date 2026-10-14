"""Whether the enemy has shown air power, and how much: what the answer to enemy air is started and scaled by.

Most of the agent's army cannot hit an aircraft at all, and a base without
anti-air is bombed at leisure. The answer (more rocket squads in the army
mix, AA defenses at the base) costs money that a match without enemy
aircraft is better off without, and the rocket squads of the mix answer a
few aircraft already. So the extra answer waits until more than a few enemy
aircraft that carry a weapon are seen: `aa_min_aircraft` of them in the
last `aa_window_seconds`. A building that makes aircraft, the helipad or the
airfield, calls for nothing on its own. Above the trigger, the mix adds a
rocket squad for every `aa_aircraft_per_squad` aircraft beyond
`aa_free_aircraft`, up to `aa_squads_max` (`rocket_squads_wanted`), and the
base keeps `aa_defenses` AA defenses, `aa_defenses_many` from
`aa_many_aircraft` aircraft (`anti_air_defenses_wanted`). With no aircraft
seen for `aa_window_seconds` the extra answer stops. With
`[anti_air].enabled` false nothing is seen, and nothing of the answer is
done.

Air seen at all, an aircraft or an air producer once in sight, stays seen for
the rest of the match (`is_air_seen`): an AA defense is then placed by the
refinery the planes come for.

Each enemy combat aircraft is counted once by its id, at the last tick it was
seen. The enemy memory cannot give that count: it forgets an aircraft as soon
as its wreck is seen, and the aircraft that came and were shot down are the
ones that say the enemy keeps sending them.

The watch takes in the enemy memory in every cycle; see `MatchContext`.
"""

from __future__ import annotations

from ucla_agent.games.redalert.items import ALLIES, SOVIET, TICKS_PER_SECOND
from ucla_agent.games.redalert.tuning import AntiAirTuning
from ucla_agent.games.redalert.world import World

# The enemy buildings that make aircraft: the helipad and the airfield.
AIR_PRODUCERS = frozenset({"hpad", "afld"})
# The AA defense of each family: the Allied AA gun and the Soviet SAM site.
ANTI_AIR_DEFENSES = {ALLIES: "agun", SOVIET: "sam"}


def is_combat_aircraft(enemy_type: str, world: World) -> bool:
    """Whether an enemy of a type is an aircraft that carries a weapon, as the damage table gives it.

    A transport, a spy plane or the plane of a paratroop drop flies but fights nothing.
    """
    facts = world.items.damage.get(enemy_type)
    return facts.air and (facts.hits_ground or facts.dps_air > 0)


class AirWatch:
    """The enemy air seen so far in a match. See the module's note."""

    def __init__(self, enabled: bool = True) -> None:
        """A watch that has seen nothing yet. One that is not `enabled` takes nothing in and never sees air, which
        leaves the whole answer to enemy air off (`[anti_air].enabled`)."""
        self._enabled = enabled
        # The id of every enemy combat aircraft seen in the match, the ones since destroyed included, and the last tick
        # it was seen at.
        self._aircraft: dict[int, int] = {}
        self._has_seen_producer = False

    @property
    def is_air_seen(self) -> bool:
        """Whether an enemy combat aircraft or a building that makes aircraft has been seen in this match."""
        return self._has_seen_producer or bool(self._aircraft)

    @property
    def aircraft_seen(self) -> int:
        """How many enemy combat aircraft have been seen in this match, each counted once."""
        return len(self._aircraft)

    def aircraft_lately(self, tick: int, window_seconds: int) -> int:
        """How many enemy combat aircraft have been seen in the `window_seconds` up to `tick`, each counted once."""
        since = tick - window_seconds * TICKS_PER_SECOND
        return sum(1 for seen_at in self._aircraft.values() if seen_at >= since)

    def observe(self, world: World) -> None:
        """Take in what the enemy memory holds of the latest state, unless the watch is not enabled."""
        if self._enabled:
            self._take_in(world)

    def _take_in(self, world: World) -> None:
        """Note the combat aircraft and the buildings that make aircraft that the enemy memory holds."""
        for record in world.enemy_memory.values():
            if not record.is_building:
                if is_combat_aircraft(record.type, world):
                    self._aircraft[record.id] = max(self._aircraft.get(record.id, 0), record.last_seen_tick)
            elif _is_air_producer(record.type, world):
                self._has_seen_producer = True


def is_air_threat(air: AirWatch, tick: int, tuning: AntiAirTuning) -> bool:
    """Whether the extra answer to enemy air is in force: `aa_min_aircraft` enemy combat aircraft or more seen in the
    last `aa_window_seconds`."""
    return air.aircraft_lately(tick, tuning.aa_window_seconds) >= tuning.aa_min_aircraft


def rocket_squads_wanted(air: AirWatch, tick: int, tuning: AntiAirTuning) -> int:
    """The rocket squads the army mix adds against enemy air: none below the trigger (`is_air_threat`), then one for
    every `aa_aircraft_per_squad` aircraft seen lately beyond `aa_free_aircraft`, up to `aa_squads_max`."""
    if not is_air_threat(air, tick, tuning):
        return 0
    beyond = air.aircraft_lately(tick, tuning.aa_window_seconds) - tuning.aa_free_aircraft
    return min(tuning.aa_squads_max, max(0, beyond) // tuning.aa_aircraft_per_squad)


def anti_air_defenses_wanted(air: AirWatch, tick: int, tuning: AntiAirTuning) -> int:
    """How many AA defenses the base keeps: none below the trigger (`is_air_threat`), then `aa_defenses`, and
    `aa_defenses_many` from `aa_many_aircraft` enemy combat aircraft seen lately."""
    if not is_air_threat(air, tick, tuning):
        return 0
    many = air.aircraft_lately(tick, tuning.aa_window_seconds) >= tuning.aa_many_aircraft
    return tuning.aa_defenses_many if many else tuning.aa_defenses


def _is_air_producer(building_type: str, world: World) -> bool:
    """Whether an enemy building makes aircraft: a helipad or an airfield, a faction's own kind of one (the Ukrainian
    airfield is an `afld.ukraine`), or a variant of one."""
    own_kind = building_type.split(".", 1)[0]
    facts = world.items.get(own_kind)
    return (own_kind if facts is None else facts.name) in AIR_PRODUCERS
