"""The item names the agent orders by, for each of the two families of factions.

Allies and Soviets build the same kind of base under different names: the
barracks is a `tent` or a `barr`, the first defense a `pbox` or an `ftur`.
The rules of play are written once, against the names here, and every item
they order is looked up in the table of the agent's own family, so that a
rule never asks a faction for a building it does not have. Whether an item
can be had at this moment is a different question, answered by the state's
`available_production`.
"""

from __future__ import annotations

from dataclasses import dataclass

from ucla_agent.games.redalert.items import ALLIES, SOVIET

# Built and trained by both families.
CONSTRUCTION_YARD = "fact"
MCV = "mcv"
POWER_PLANT = "powr"
ADVANCED_POWER_PLANT = "apwr"
REFINERY = "proc"
WAR_FACTORY = "weap"
SERVICE_DEPOT = "fix"
RADAR = "dome"
HARVESTER = "harv"
RIFLE_INFANTRY = "e1"
ROCKET_INFANTRY = "e3"
# The Soviet armored transport, which carries the rocket soldiers of the APC raid.
APC = "apc"


@dataclass(frozen=True)
class Family:
    """What one family calls the things the rules of play order."""

    barracks: str
    anti_infantry_defense: str
    # The defense against armor that upkeep keeps and a preserved lead adds against an army of vehicles.
    anti_armor_defense: str
    # The defense against armor the opening may take as its cover; None for a family whose one costs and drains too
    # much for the first minutes.
    opening_anti_armor_defense: str | None
    # The light vehicle, made before a service depot stands, and the main tank, made once one does.
    light_vehicle: str
    heavy_vehicle: str
    # The vehicles a raid is made of, the preferred one first; empty for a family that raids on foot.
    raid_vehicles: tuple[str, ...]
    # The infantry a raid is made of, for a family that raids on foot.
    raid_infantry: str | None


FAMILIES: dict[str, Family] = {
    ALLIES: Family(
        barracks="tent",
        anti_infantry_defense="pbox",
        anti_armor_defense="gun",
        opening_anti_armor_defense="gun",
        light_vehicle="1tnk",
        heavy_vehicle="2tnk",
        raid_vehicles=("1tnk", "jeep"),
        raid_infantry=None,
    ),
    SOVIET: Family(
        barracks="barr",
        anti_infantry_defense="ftur",
        anti_armor_defense="tsla",
        opening_anti_armor_defense=None,
        light_vehicle="ftrk",
        heavy_vehicle="3tnk",
        raid_vehicles=(),
        raid_infantry="e2",
    ),
}
BARRACKS_TYPES = frozenset(family.barracks for family in FAMILIES.values())
