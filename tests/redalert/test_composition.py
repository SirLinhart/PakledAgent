"""The composition table: what the unit queues make under each plan, from the army mix, and the strength to attack."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from situations import tracked_items
from switches_off import mix_off, plan_rules_off
from ucla_agent.games.redalert.anti_air import AirWatch
from ucla_agent.games.redalert.composition import (
    AIR_SHIFT,
    BASE_STAGE,
    DEPOT_STAGE,
    FINISH,
    HOLD,
    INFANTRY_SHIFT,
    NEVER_BUILT,
    NO_SHIFT,
    OPENING,
    PRESERVE,
    PRESS,
    RADAR_STAGE,
    RAID,
    STRIKE,
    TECH_CENTER,
    VEHICLE_SHIFT,
    ArmyMix,
    AttackStrengths,
    Composition,
    CompositionTable,
    EnemyCounted,
    counter_quota,
    enemy_counted,
    is_enemy_infantry_heavy,
    is_infantry_heavy,
    mix_stage,
)
from ucla_agent.games.redalert.items import ALLIES, SOVIET, TICKS_PER_SECOND
from ucla_agent.games.redalert.tuning import (
    MIXED,
    TANKS,
    TECH_STAGE,
    AntiAirTuning,
    CompositionTuning,
    MixRow,
    MixTuning,
    Tuning,
)
from ucla_agent.games.redalert.world import World

ALLIED_OFFER = ("e1", "e3", "jeep", "1tnk")
SOVIET_OFFER = ("e1", "e2", "e3", "ftrk", "apc")
# Everything either family's rows name, and what the never-built list names besides.
EVERYTHING = ("e1", "e2", "e3", "1tnk", "2tnk", "arty", "ftrk", "3tnk", "v2rl", *sorted(NEVER_BUILT))
DEPOT, RADAR_TOO = ("weap", "fix"), ("weap", "fix", "dome")
TICK = 3000


def world_with(
    buildings: tuple[str, ...] = (),
    units: tuple[str, ...] = (),
    offered: tuple[str, ...] = EVERYTHING,
    enemies: tuple[str, ...] = (),
    tick: int = TICK,
    enemy_buildings: tuple[str, ...] = (),
    world: World | None = None,
) -> World:
    """A world with own buildings and units of the given types, offered the given items, and enemy units and
    buildings in sight: a new one, or `world` after one more state."""
    world = world or World(tracked_items())
    observation = {
        "tick": tick,
        "buildings": [{"id": 100 + n, "type": name, "x": 20 + n, "y": 48} for n, name in enumerate(buildings)],
        "units": [{"id": 200 + n, "type": name, "x": 30, "y": 40 + n} for n, name in enumerate(units)],
        "available_production": list(offered),
        "visible_enemies": [{"id": 900 + n, "type": name, "x": 60, "y": 40} for n, name in enumerate(enemies)],
        "visible_enemy_buildings": [
            {"id": 800 + n, "type": name, "x": 70 + n, "y": 48} for n, name in enumerate(enemy_buildings)
        ],
    }
    world.update({"phase": "playing", "observation": observation})
    return world


def every_unit(world: World, unit_type: str) -> int:
    """A raid-unit count that gives the raid every own unit of a type: the worlds of these tests have no groups."""
    return sum(1 for unit in world.units.values() if unit.type == unit_type)


def army_mix(
    mix: MixTuning | None = None, air: AirWatch | None = None, anti_air: AntiAirTuning | None = None
) -> ArmyMix:
    """An army mix with the mixed main force of the switches-off thresholds unless others are given, over a watch
    that has seen no enemy air unless one is given."""
    return ArmyMix(mix or mix_off(), anti_air or AntiAirTuning(), air or AirWatch())


def new_table(
    composition: CompositionTuning | None = None,
    mix: MixTuning | None = None,
    air: AirWatch | None = None,
    anti_air: AntiAirTuning | None = None,
) -> CompositionTable:
    """A composition table of its own army mix, with the switches-off thresholds unless others are given."""
    return CompositionTable(composition or CompositionTuning(), army_mix(mix, air, anti_air), every_unit, True)


def watching(world: World) -> AirWatch:
    """A watch on the enemy's air that has taken in the world."""
    watch = AirWatch()
    watch.observe(world)
    return watch


@pytest.fixture
def table() -> CompositionTable:
    return new_table()


def shares(points: dict[str, float]) -> dict[str, float]:
    """Points of 100 as shares of the army's value."""
    return {item: point / 100 for item, point in points.items()}


def mix_of(infantry: dict[str, float], vehicle: dict[str, float] | None = None) -> Composition:
    """The composition that gives each queue's items these points of 100."""
    return Composition(infantry=shares(infantry), vehicle=shares(vehicle or {}))


def assert_mix(answer: Composition, expected: Composition) -> None:
    assert answer.infantry == pytest.approx(expected.infantry) and set(answer.infantry) == set(expected.infantry)
    assert answer.vehicle == pytest.approx(expected.vehicle) and set(answer.vehicle) == set(expected.vehicle)


ALLIED_ROWS = {
    BASE_STAGE: mix_of({"e1": 50, "e3": 15}, {"1tnk": 35}),
    DEPOT_STAGE: mix_of({"e1": 40, "e3": 15}, {"2tnk": 45}),
    RADAR_STAGE: mix_of({"e1": 30, "e3": 10}, {"2tnk": 30, "arty": 30}),
}
SOVIET_ROWS = {
    BASE_STAGE: mix_of({"e1": 50, "e2": 10, "e3": 25}, {"ftrk": 15}),
    DEPOT_STAGE: mix_of({"e1": 35, "e2": 10, "e3": 15}, {"3tnk": 40}),
    RADAR_STAGE: mix_of({"e1": 35, "e2": 5, "e3": 10}, {"3tnk": 25, "v2rl": 25}),
}
# A seen enemy army of 4 2tnk and 4 e1 ($3,800, 89% in vehicles) and one of 20 e1 and a 1tnk ($2,700, 74% infantry).
VEHICLE_ARMY = ("2tnk",) * 4 + ("e1",) * 4
INFANTRY_ARMY = ("e1",) * 20 + ("1tnk",)


# -- the stages -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "buildings, stage",
    [
        ((), BASE_STAGE),
        (("weap",), BASE_STAGE),
        (("weap", "dome"), BASE_STAGE),
        (DEPOT, DEPOT_STAGE),
        (RADAR_TOO, RADAR_STAGE),
        ((*RADAR_TOO, TECH_CENTER), TECH_STAGE),
    ],
    ids=[
        "nothing",
        "a war factory",
        "a radar without a depot",
        "a service depot",
        "a depot and a radar",
        "a tech center",
    ],
)
def test_the_mix_stage_is_the_highest_whose_buildings_stand(buildings, stage):
    assert mix_stage(world_with(buildings)) == stage


@pytest.mark.parametrize("family, rows", [(ALLIES, ALLIED_ROWS), (SOVIET, SOVIET_ROWS)], ids=["allies", "soviets"])
@pytest.mark.parametrize(
    "buildings, stage",
    [(("weap",), BASE_STAGE), (DEPOT, DEPOT_STAGE), (RADAR_TOO, RADAR_STAGE)],
    ids=["base", "fix", "dome"],
)
def test_stage_hold_gives_the_mix_row_of_the_family_s_stage(table, family, rows, buildings, stage):
    assert_mix(table(HOLD, family, world_with(buildings)), rows[stage])


def test_stage_an_allied_world_with_a_depot_and_a_radar_gives_e1_30_e3_10_2tnk_30_arty_30(table):
    answer = table(HOLD, ALLIES, world_with(RADAR_TOO))

    assert answer.infantry == pytest.approx({"e1": 0.3, "e3": 0.1})
    assert answer.vehicle == pytest.approx({"2tnk": 0.3, "arty": 0.3})


@pytest.mark.parametrize("plan", [STRIKE, FINISH, PRESS, RAID])
@pytest.mark.parametrize("family", [ALLIES, SOVIET])
def test_mix_strike_finish_and_the_raiding_plans_past_their_quota_make_what_hold_makes(table, plan, family):
    # Past every raid quota: four light tanks, six grenadiers and three flak trucks.
    world = world_with(RADAR_TOO, units=("1tnk",) * 4 + ("e2",) * 6 + ("ftrk",) * 3)

    assert table(plan, family, world) == table(HOLD, family, world)


@pytest.mark.parametrize("family, rows", [(ALLIES, ALLIED_ROWS), (SOVIET, SOVIET_ROWS)], ids=["allies", "soviets"])
def test_mix_preserve_trains_the_row_s_infantry_and_no_vehicle(table, family, rows):
    answer = table(PRESERVE, family, world_with(DEPOT))

    assert_mix(answer, Composition(infantry=rows[DEPOT_STAGE].infantry))


@pytest.mark.parametrize("family", [ALLIES, SOVIET])
def test_in_opening_neither_queue_is_given_anything(table, family):
    assert table(OPENING, family, world_with(("tent", "barr", "weap"))) == Composition(infantry={}, vehicle={})


# -- the counter shift ----------------------------------------------------------------------------


def test_counter_a_vehicle_army_moves_15_from_the_allied_rifles_to_the_rockets(table):
    answer = table(HOLD, ALLIES, world_with(RADAR_TOO, enemies=VEHICLE_ARMY))

    assert_mix(answer, mix_of({"e1": 15, "e3": 25}, {"2tnk": 30, "arty": 30}))


def test_counter_a_vehicle_army_moves_15_from_the_soviet_rifles_to_the_rockets_alone(table):
    answer = table(HOLD, SOVIET, world_with(DEPOT, enemies=VEHICLE_ARMY))

    assert_mix(answer, mix_of({"e1": 20, "e2": 10, "e3": 30}, {"3tnk": 40}))


def test_counter_against_vehicles_the_soviet_dome_row_moves_15_from_the_rifles_and_keeps_its_vehicles(table):
    answer = table(HOLD, SOVIET, world_with(RADAR_TOO, enemies=VEHICLE_ARMY))

    assert_mix(answer, mix_of({"e1": 20, "e2": 5, "e3": 25}, {"3tnk": 25, "v2rl": 25}))


@pytest.mark.parametrize("buildings", [("weap",), DEPOT, RADAR_TOO], ids=["base", "fix", "dome"])
def test_soviet_infantry_counter_the_soviet_anti_armor_counter_queues_e3_not_e2(table, buildings):
    plain = table(HOLD, SOVIET, world_with(buildings))

    answer = new_table()(HOLD, SOVIET, world_with(buildings, enemies=VEHICLE_ARMY))

    assert answer.infantry["e3"] - plain.infantry["e3"] == pytest.approx(0.15)
    assert answer.infantry["e2"] == pytest.approx(plain.infantry["e2"])


def test_counter_an_infantry_army_at_the_dome_stage_moves_10_and_5_from_the_main_tank_to_rifles_and_artillery(table):
    allied = table(HOLD, ALLIES, world_with(RADAR_TOO, enemies=INFANTRY_ARMY))
    soviet = table(HOLD, SOVIET, world_with(RADAR_TOO, enemies=INFANTRY_ARMY))

    assert_mix(allied, mix_of({"e1": 40, "e3": 10}, {"2tnk": 15, "arty": 35}))
    assert_mix(soviet, mix_of({"e1": 45, "e2": 5, "e3": 10}, {"3tnk": 10, "v2rl": 30}))


def test_counter_an_infantry_army_at_the_fix_stage_moves_10_from_the_main_tank_to_the_rifles(table):
    answer = table(HOLD, ALLIES, world_with(DEPOT, enemies=INFANTRY_ARMY))

    assert_mix(answer, mix_of({"e1": 50, "e3": 15}, {"2tnk": 35}))


@pytest.mark.parametrize("family, rows", [(ALLIES, ALLIED_ROWS), (SOVIET, SOVIET_ROWS)], ids=["allies", "soviets"])
def test_counter_an_infantry_army_at_the_base_stage_moves_nothing_with_no_main_tank_in_the_row(table, family, rows):
    assert_mix(table(HOLD, family, world_with(("weap",), enemies=INFANTRY_ARMY)), rows[BASE_STAGE])


def test_counter_against_vehicles_at_the_base_stage_the_shift_still_applies(table):
    answer = table(HOLD, ALLIES, world_with(("weap",), enemies=VEHICLE_ARMY))

    assert_mix(answer, mix_of({"e1": 35, "e3": 30}, {"1tnk": 35}))


@pytest.mark.parametrize(
    "enemies",
    [("2tnk",) + ("e1",) * 1, ("e1",) * 10, ("e1",) * 14],
    ids=["$950 of vehicles", "$1,000 of infantry", "$1,400, just under 1,500"],
)
def test_counter_a_seen_enemy_army_worth_less_than_1500_shifts_nothing(table, enemies):
    world = world_with(RADAR_TOO, enemies=enemies)

    assert_mix(table(HOLD, ALLIES, world), ALLIED_ROWS[RADAR_STAGE])
    assert army_mix().counter_shift(world) == NO_SHIFT


@pytest.mark.parametrize(
    "enemies, shift",
    [
        (VEHICLE_ARMY, VEHICLE_SHIFT),
        (INFANTRY_ARMY, INFANTRY_SHIFT),
        (("e1",) * 15, INFANTRY_SHIFT),
        (("2tnk", "2tnk") + ("e1",) * 17, VEHICLE_SHIFT),
        (("2tnk", "2tnk") + ("e1",) * 18, NO_SHIFT),
        (("1tnk",) * 2 + ("e1",) * 20, NO_SHIFT),
    ],
    ids=["89% vehicles", "74% infantry", "$1,500 all infantry", "50% vehicles", "49% vehicles", "59% infantry"],
)
def test_counter_the_shift_is_decided_by_the_seen_army_s_value_in_vehicles_and_in_infantry(enemies, shift):
    assert army_mix().counter_shift(world_with(enemies=enemies)) == shift


def test_counter_units_that_do_not_fight_are_not_part_of_the_seen_army():
    # Harvesters and an MCV ($9,000) with $900 of rifles: worth less than 1,500.
    world = world_with(enemies=("harv",) * 5 + ("mcv",) * 2 + ("e1",) * 9)

    assert army_mix().counter_shift(world) == NO_SHIFT


def test_counter_a_stale_enemy_unit_is_not_part_of_the_seen_army():
    mix = army_mix(mix_off(counter_every_ticks=1))
    world = world_with(enemies=VEHICLE_ARMY, tick=1000)
    seen = mix.counter_shift(world)
    # The tanks leave sight; a minute later their memory is stale.
    world.update({"phase": "playing", "observation": {"tick": 1100, "visible_enemies": []}})
    out_of_sight = mix.counter_shift(world)
    world.update({"phase": "playing", "observation": {"tick": 2000, "visible_enemies": []}})

    assert (seen, out_of_sight, mix.counter_shift(world)) == (VEHICLE_SHIFT, VEHICLE_SHIFT, NO_SHIFT)


def test_counter_the_shift_is_decided_again_only_every_250_ticks():
    mix = army_mix()
    decided = mix.counter_shift(world_with(enemies=VEHICLE_ARMY, tick=1000))

    within = mix.counter_shift(world_with(enemies=INFANTRY_ARMY, tick=1249))
    after = mix.counter_shift(world_with(enemies=INFANTRY_ARMY, tick=1250))

    assert (decided, within, after) == (VEHICLE_SHIFT, VEHICLE_SHIFT, INFANTRY_SHIFT)


def test_counter_the_thresholds_are_the_tuning_s():
    tuning = mix_off(counter_min_value=500, infantry_heavy_share=0.5, infantry_counter_points=20)
    table = new_table(mix=tuning)
    # Five rifles and a light tank: $1,200, 42% infantry.
    world = world_with(DEPOT, enemies=("e1",) * 5 + ("1tnk",))

    assert army_mix(tuning).counter_shift(world) == VEHICLE_SHIFT
    assert_mix(table(HOLD, ALLIES, world), mix_of({"e1": 25, "e3": 30}, {"2tnk": 45}))
    infantry = world_with(DEPOT, enemies=("e1",) * 7)
    assert_mix(new_table(mix=tuning)(HOLD, ALLIES, infantry), mix_of({"e1": 60, "e3": 15}, {"2tnk": 25}))


def test_counter_no_share_goes_above_the_most_of_60_or_below_0():
    # A row where the rifles have 55 and the main tank 5: the shift against infantry moves 5, not 10.
    rows = {family: dict(mix_off().rows[family]) for family in (ALLIES, SOVIET)}
    rows[ALLIES][DEPOT_STAGE] = MixRow(infantry={"e1": 55, "e3": 40}, vehicle={"2tnk": 5})
    table = new_table(mix=mix_off(rows=rows))

    answer = table(HOLD, ALLIES, world_with(DEPOT, enemies=INFANTRY_ARMY))

    assert_mix(answer, mix_of({"e1": 60, "e3": 40}))


@pytest.mark.parametrize("family", [ALLIES, SOVIET])
@pytest.mark.parametrize("buildings", [("weap",), DEPOT, RADAR_TOO], ids=["base", "fix", "dome"])
@pytest.mark.parametrize("enemies", [(), VEHICLE_ARMY, INFANTRY_ARMY], ids=["no shift", "vehicles", "infantry"])
def test_mix_every_share_is_between_0_and_60_and_a_row_adds_up_to_the_whole_army(table, family, buildings, enemies):
    answer = table(HOLD, family, world_with(buildings, enemies=enemies))
    every = [*answer.infantry.values(), *answer.vehicle.values()]

    assert all(0 < share <= 0.6 for share in every) and sum(every) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "plan, buildings, enemies, share",
    [
        (HOLD, ("weap",), (), 0.35),
        (HOLD, DEPOT, (), 0.45),
        (HOLD, RADAR_TOO, (), 0.6),
        (HOLD, RADAR_TOO, INFANTRY_ARMY, 0.5),
        (PRESERVE, DEPOT, (), 0.0),
        (RAID, DEPOT, (), 0.45),
    ],
    ids=["base", "fix", "dome", "dome against infantry", "preserve", "raid quota"],
)
def test_mix_the_vehicle_share_is_the_vehicles_shares_added_up(table, plan, buildings, enemies, share):
    assert table(plan, ALLIES, world_with(buildings, enemies=enemies)).vehicle_share == pytest.approx(share)


# -- the air shift ---------------------------------------------------------------------------------


def air_table(world: World, anti_air: AntiAirTuning | None = None, mix: MixTuning | None = None) -> CompositionTable:
    """A composition table whose watch on the enemy's air has taken in the world."""
    return new_table(mix=mix, air=watching(world), anti_air=anti_air)


@pytest.mark.parametrize("family, rows", [(ALLIES, ALLIED_ROWS), (SOVIET, SOVIET_ROWS)], ids=["allies", "soviets"])
@pytest.mark.parametrize(
    "buildings, stage",
    [(("weap",), BASE_STAGE), (DEPOT, DEPOT_STAGE), (RADAR_TOO, RADAR_STAGE)],
    ids=["base", "fix", "dome"],
)
@pytest.mark.parametrize(
    "enemies, enemy_buildings",
    [((), ()), (("e1", "1tnk", "tran", "badr", "u2"), ("weap", "dome"))],
    ids=["nothing seen", "ground units, unarmed aircraft and buildings seen"],
)
def test_air_without_air_seen_the_rows_are_unchanged(family, rows, buildings, stage, enemies, enemy_buildings):
    world = world_with(buildings, enemies=enemies, enemy_buildings=enemy_buildings)

    assert not watching(world).is_air_seen
    assert_mix(air_table(world)(HOLD, family, world), rows[stage])


def migs(count: int) -> tuple[str, ...]:
    """So many enemy combat aircraft."""
    return ("mig",) * count


def scaled(infantry: dict[str, float], vehicle: dict[str, float], total: float) -> Composition:
    """The composition of a row of `total` points scaled back to 100."""
    return mix_of({k: v * 100 / total for k, v in infantry.items()}, {k: v * 100 / total for k, v in vehicle.items()})


@pytest.mark.parametrize("family, rows", [(ALLIES, ALLIED_ROWS), (SOVIET, SOVIET_ROWS)], ids=["allies", "soviets"])
@pytest.mark.parametrize(
    "enemies, enemy_buildings",
    [(("yak",), ()), (("yak", "mig"), ()), ((), ("afld",)), ((), ("hpad",))],
    ids=["one aircraft", "two aircraft", "an airfield", "a helipad"],
)
def test_air_below_3_aircraft_or_an_air_producer_alone_the_rows_are_unchanged(family, rows, enemies, enemy_buildings):
    world = world_with(RADAR_TOO, enemies=enemies, enemy_buildings=enemy_buildings)
    mix = army_mix(air=watching(world))

    assert_mix(air_table(world)(HOLD, family, world), rows[RADAR_STAGE])
    assert mix.shifts(world) == NO_SHIFT


def test_air_with_3_aircraft_the_shift_is_in_force_and_adds_no_squad_yet():
    world = world_with(RADAR_TOO, enemies=migs(3))

    assert army_mix(air=watching(world)).shifts(world) == AIR_SHIFT
    assert_mix(air_table(world)(HOLD, ALLIES, world), ALLIED_ROWS[RADAR_STAGE])


@pytest.mark.parametrize(
    "aircraft, added", [(4, 8), (5, 8), (6, 16), (7, 16), (8, 24), (12, 24)], ids=["4", "5", "6", "7", "8", "12"]
)
def test_air_each_squad_adds_8_points_to_e3_and_the_row_is_scaled_back_to_100(aircraft, added):
    world = world_with(RADAR_TOO, enemies=migs(aircraft))

    answer = air_table(world)(HOLD, ALLIES, world)

    assert_mix(answer, scaled({"e1": 30, "e3": 10 + added}, {"2tnk": 30, "arty": 30}, 100 + added))


def test_army_build_with_4_aircraft_seen_12_tanks_ask_for_about_12_rockets_and_still_4_artillery():
    world = world_with(RADAR_TOO, enemies=migs(4), offered=(*ALLIED_OFFER, "2tnk", "arty"))

    answer = shipped_table(world)(HOLD, ALLIES, world)
    tanks = answer.vehicle["2tnk"] / 850

    # The squad's 8 points to the rockets' 15 of 108: 23 / 300 against 64 / 850 is 12.2 rockets per 12 tanks.
    assert answer.infantry["e3"] / 300 / tanks * 12 == pytest.approx(12, abs=0.5)
    assert answer.vehicle["arty"] / 850 / tanks * 12 == pytest.approx(4, abs=0.25)


def test_air_with_a_vehicle_army_and_4_aircraft_the_counter_shift_comes_first_then_the_squad():
    # The vehicle shift gives e1 15 and e3 25; the squad then adds 8 to e3.
    world = world_with(RADAR_TOO, enemies=VEHICLE_ARMY)
    air = watching(world_with(RADAR_TOO, enemies=migs(4)))

    answer = new_table(air=air)(HOLD, ALLIES, world)

    assert army_mix().counter_shift(world) == VEHICLE_SHIFT
    assert_mix(answer, scaled({"e1": 15, "e3": 33}, {"2tnk": 30, "arty": 30}, 108))


def test_air_the_flak_points_move_from_the_soviet_main_tank_to_the_flak_truck():
    world = world_with(RADAR_TOO, enemies=migs(4))
    anti_air = AntiAirTuning(air_flak_points=5)

    soviet = air_table(world, anti_air)(HOLD, SOVIET, world)
    allied = air_table(world, anti_air)(HOLD, ALLIES, world)

    assert_mix(soviet, scaled({"e1": 35, "e2": 5, "e3": 18}, {"3tnk": 20, "v2rl": 25, "ftrk": 5}, 108))
    assert_mix(allied, scaled({"e1": 30, "e3": 18}, {"2tnk": 30, "arty": 30}, 108))


def test_air_no_share_goes_above_60_under_the_squads():
    # Rockets at 55 of a row whose most is 60 take 12.5 of the 24 points wanted: 67.5 of 112.5 is 60 percent.
    rows = {family: dict(mix_off().rows[family]) for family in (ALLIES, SOVIET)}
    rows[ALLIES][DEPOT_STAGE] = MixRow(infantry={"e1": 20, "e3": 55}, vehicle={"2tnk": 25})
    depot = world_with(DEPOT, enemies=migs(8))
    tuning = mix_off(rows=rows)

    answer = air_table(depot, mix=tuning)(HOLD, ALLIES, depot)

    assert_mix(answer, scaled({"e1": 20, "e3": 67.5}, {"2tnk": 25}, 112.5))


@pytest.mark.parametrize("family", [ALLIES, SOVIET])
@pytest.mark.parametrize("buildings", [("weap",), DEPOT, RADAR_TOO], ids=["base", "fix", "dome"])
@pytest.mark.parametrize("enemies", [(), VEHICLE_ARMY, INFANTRY_ARMY], ids=["no shift", "vehicles", "infantry"])
def test_air_every_share_is_between_0_and_60_and_the_row_with_squads_adds_up_to_the_whole_army(
    family, buildings, enemies
):
    world = world_with(buildings, enemies=enemies)
    air = watching(world_with(buildings, enemies=migs(8)))

    answer = new_table(air=air)(HOLD, family, world)
    every = [*answer.infantry.values(), *answer.vehicle.values()]

    assert all(0 < share <= 0.6 + 1e-9 for share in every) and sum(every) == pytest.approx(1.0)
    assert NEVER_BUILT.isdisjoint({*answer.infantry, *answer.vehicle})


@pytest.mark.parametrize("seconds_after, added", [(5, 8), (180, 8), (181, 0)], ids=["5 s", "180 s", "181 s"])
def test_air_the_squads_stay_180_s_after_the_aircraft_are_shot_down_and_then_stop(seconds_after, added):
    world = world_with(RADAR_TOO, enemies=migs(4))
    air = watching(world)
    # The migs' wrecks are seen: the enemy memory forgets them.
    later = TICK + seconds_after * TICKS_PER_SECOND
    world_with(RADAR_TOO, enemies=("mig.husk",) * 4, tick=later, world=world)
    air.observe(world)

    answer = new_table(air=air)(HOLD, ALLIES, world)

    assert world.enemy_memory == {}
    assert_mix(answer, scaled({"e1": 30, "e3": 10 + added}, {"2tnk": 30, "arty": 30}, 100 + added))


@pytest.mark.parametrize(
    "enemies, named",
    [((), NO_SHIFT), (VEHICLE_ARMY, VEHICLE_SHIFT), (INFANTRY_ARMY, INFANTRY_SHIFT)],
    ids=["air", "vehicles and air", "infantry and air"],
)
def test_air_the_shifts_in_force_name_the_air_shift_alone_or_joined_to_the_counter_shift(enemies, named):
    world = world_with(RADAR_TOO, enemies=enemies)
    air = watching(world_with(RADAR_TOO, enemies=migs(3)))

    expected = AIR_SHIFT if named == NO_SHIFT else f"{named}+{AIR_SHIFT}"
    assert army_mix(air=air).shifts(world) == expected
    assert army_mix(air=watching(world)).shifts(world) == named


@pytest.mark.parametrize(
    "family, enemies, expected",
    [
        (ALLIES, migs(4), ALLIED_ROWS[RADAR_STAGE]),
        (SOVIET, migs(4), SOVIET_ROWS[RADAR_STAGE]),
        (ALLIES, (*VEHICLE_ARMY, "yak"), mix_of({"e1": 15, "e3": 25}, {"2tnk": 30, "arty": 30})),
    ],
    ids=["allies", "soviets", "allies against vehicles: the counter shift alone"],
)
def test_air_with_the_anti_air_answer_switched_off_no_air_shift_applies(family, enemies, expected):
    world = world_with(RADAR_TOO, enemies=enemies, enemy_buildings=("afld", "hpad"))
    switched_off = AirWatch(enabled=False)
    switched_off.observe(world)
    mix = army_mix(air=switched_off, anti_air=AntiAirTuning(enabled=False))

    answer = CompositionTable(CompositionTuning(), mix, every_unit, True)(HOLD, family, world)

    assert_mix(answer, expected)
    assert mix.shifts(world) in (NO_SHIFT, VEHICLE_SHIFT)


# -- what cannot be trained ------------------------------------------------------------------------


def test_mix_an_unavailable_item_s_share_goes_to_the_rest_of_its_queue_only(table):
    world = world_with(RADAR_TOO, offered=tuple(item for item in EVERYTHING if item != "arty"))

    assert_mix(table(HOLD, ALLIES, world), mix_of({"e1": 30, "e3": 10}, {"2tnk": 60}))


def test_mix_shares_are_spread_in_proportion_to_the_items_left():
    # The Soviet base row without e2: its 10 goes to e1 (50) and e3 (25) as 20/3 and 10/3.
    world = world_with(("weap",), offered=("e1", "e3", "ftrk"))

    assert_mix(new_table()(HOLD, SOVIET, world), mix_of({"e1": 50 + 20 / 3, "e3": 25 + 10 / 3}, {"ftrk": 15}))


def test_mix_a_queue_with_none_of_its_items_on_offer_trains_nothing(table):
    world = world_with(DEPOT, offered=("e1", "e3", "1tnk"))

    assert_mix(table(HOLD, ALLIES, world), mix_of({"e1": 40, "e3": 15}))


def test_mix_the_tracked_rows_never_yield_an_item_of_the_never_built_list():
    for family in (ALLIES, SOVIET):
        for buildings in (("weap",), DEPOT, RADAR_TOO):
            for enemies in ((), VEHICLE_ARMY, INFANTRY_ARMY):
                answer = new_table()(HOLD, family, world_with(buildings, enemies=enemies))
                assert NEVER_BUILT.isdisjoint({*answer.infantry, *answer.vehicle})


def test_mix_a_row_that_names_never_built_items_or_aircraft_trains_none_of_them():
    rows = {family: dict(mix_off().rows[family]) for family in (ALLIES, SOVIET)}
    rows[ALLIES][BASE_STAGE] = MixRow(
        infantry={"e1": 30, "dog": 10, "e6": 10, "shok": 5}, vehicle={"1tnk": 20, "jeep": 10, "apc": 5, "heli": 10}
    )
    table = new_table(mix=mix_off(rows=rows))

    answer = table(HOLD, ALLIES, world_with(("weap",), offered=(*EVERYTHING, "heli")))

    assert_mix(answer, mix_of({"e1": 55}, {"1tnk": 45}))


def test_the_never_built_list_is_the_requirement_s():
    assert NEVER_BUILT == {
        *("dog", "jeep", "apc", "stnk", "ctnk", "ttnk", "e7", "shok", "e6"),
        *("medi", "mech", "spy", "thf", "mnly", "truk", "qtnk", "dtrk", "mgg", "mrj"),
    }


# -- the raid quotas ---------------------------------------------------------------------------------


@pytest.mark.parametrize("light_tanks, vehicle", [(0, "1tnk"), (3, "1tnk"), (4, "2tnk"), (6, "2tnk")])
def test_an_allied_raid_builds_light_tanks_until_it_has_four_and_then_the_mix(table, light_tanks, vehicle):
    world = world_with(("weap", "fix"), units=("1tnk",) * light_tanks, offered=(*ALLIED_OFFER, "2tnk"))

    assert_mix(table(RAID, ALLIES, world), mix_of({"e1": 40, "e3": 15}, {vehicle: 45}))


@pytest.mark.parametrize("jeeps, vehicle", [(0, "jeep"), (3, "jeep"), (4, None)])
def test_an_allied_raid_builds_jeeps_when_the_light_tank_is_not_on_offer(table, jeeps, vehicle):
    world = world_with(("weap",), units=("jeep",) * jeeps + ("1tnk",) * 5, offered=("e1", "e3", "jeep"))

    assert list(table(RAID, ALLIES, world).vehicle) == ([vehicle] if vehicle else [])


def test_an_allied_raid_with_no_raid_vehicle_on_offer_builds_what_the_mix_has_on_offer(table):
    assert table(RAID, ALLIES, world_with(("weap",), offered=("e1", "e3"))).vehicle == {}
    assert list(table(RAID, ALLIES, world_with(DEPOT, offered=("e1", "e3", "2tnk"))).vehicle) == ["2tnk"]


SOVIET_BASE_ROW_INFANTRY = {"e1": 50, "e2": 10, "e3": 25}


@pytest.mark.parametrize(
    "buildings, grenadiers, infantry",
    [
        ((), 0, {"e2": 85}),
        ((), 5, {"e2": 85}),
        ((), 6, SOVIET_BASE_ROW_INFANTRY),
        (("weap",), 0, {"e2": 85}),
        (("weap",), 2, {"e2": 85}),
        (("weap",), 3, SOVIET_BASE_ROW_INFANTRY),
        (("weap",), 9, SOVIET_BASE_ROW_INFANTRY),
    ],
)
def test_a_soviet_raid_trains_grenadiers_until_six_or_with_a_war_factory_three_and_asks_15_in_flak_trucks(
    table, buildings, grenadiers, infantry
):
    world = world_with(buildings, units=("e2",) * grenadiers + ("e1",) * 7, offered=SOVIET_OFFER)

    assert_mix(table(RAID, SOVIET, world), mix_of(infantry, {"ftrk": 15}))


@pytest.mark.parametrize("plan", [RAID, PRESS])
@pytest.mark.parametrize("flak_trucks", [0, 2, 3])
@pytest.mark.parametrize("buildings, stage", [(DEPOT, DEPOT_STAGE), (RADAR_TOO, RADAR_STAGE)], ids=["fix", "dome"])
def test_a_soviet_raid_with_a_war_factory_asks_for_no_flak_truck_at_the_fix_and_dome_stages(
    table, buildings, stage, flak_trucks, plan
):
    # The raid quota takes no flak truck, and the rows of these stages have none.
    world = world_with(buildings, units=("ftrk",) * flak_trucks, offered=(*SOVIET_OFFER, "3tnk", "v2rl"))

    assert table(plan, SOVIET, world).vehicle == pytest.approx(SOVIET_ROWS[stage].vehicle)


@pytest.mark.parametrize("light_tanks, vehicle", [(0, "1tnk"), (3, "1tnk"), (4, "2tnk")])
def test_under_press_the_allied_vehicle_queue_trains_light_tanks_until_four_exist(table, light_tanks, vehicle):
    world = world_with(("weap", "fix"), units=("1tnk",) * light_tanks, offered=(*ALLIED_OFFER, "2tnk"))

    assert_mix(table(PRESS, ALLIES, world), mix_of({"e1": 40, "e3": 15}, {vehicle: 45}))


@pytest.mark.parametrize("grenadiers, infantry", [(0, ["e2"]), (2, ["e2"]), (3, ["e1", "e2", "e3"])])
def test_under_press_the_soviet_infantry_queue_with_a_war_factory_trains_grenadiers_until_three_exist(
    table, grenadiers, infantry
):
    world = world_with(("weap",), units=("e2",) * grenadiers, offered=SOVIET_OFFER)

    assert list(table(PRESS, SOVIET, world).infantry) == infantry


def test_the_raid_quotas_are_the_tuning_s():
    table = new_table(CompositionTuning(raid_vehicles=2, raid_infantry=1))

    assert list(table(RAID, ALLIES, world_with(units=("1tnk",) * 2, offered=ALLIED_OFFER)).vehicle) == ["1tnk"]
    assert list(table(RAID, ALLIES, world_with(units=("1tnk",), offered=ALLIED_OFFER)).vehicle) == ["1tnk"]
    assert list(table(RAID, SOVIET, world_with(units=("e2",), offered=SOVIET_OFFER)).infantry) == ["e1", "e2", "e3"]
    assert list(table(RAID, SOVIET, world_with(offered=SOVIET_OFFER)).infantry) == ["e2"]
    mixed = new_table(CompositionTuning(raid_mixed_vehicles=1))
    with_heavy_tank = (*SOVIET_OFFER, "3tnk")
    assert list(mixed(RAID, SOVIET, world_with(DEPOT, offered=with_heavy_tank)).vehicle) == ["ftrk"]
    assert list(mixed(RAID, SOVIET, world_with(DEPOT, units=("ftrk",), offered=with_heavy_tank)).vehicle) == ["3tnk"]


@pytest.mark.parametrize(
    "counted, vehicle", [(0, "1tnk"), (3, "1tnk"), (4, "2tnk")], ids=["none counted", "3 counted", "4 counted"]
)
def test_a_raid_quota_counts_the_units_the_raid_unit_count_gives_not_every_unit_that_stands(counted, vehicle):
    asked: list[str] = []

    def raid_units(world: World, unit_type: str) -> int:
        asked.append(unit_type)
        return counted

    table = CompositionTable(CompositionTuning(), army_mix(), raid_units, True)
    # Six light tanks stand, but the raid can have only `counted` of them.
    world = world_with(DEPOT, units=("1tnk",) * 6, offered=(*ALLIED_OFFER, "2tnk"))

    assert list(table(RAID, ALLIES, world).vehicle) == [vehicle] and asked == ["1tnk"]


@pytest.mark.parametrize("plan", [RAID, PRESS])
@pytest.mark.parametrize("family", [ALLIES, SOVIET])
def test_with_raids_switched_off_the_raiding_plans_make_hold_s_mix_and_no_raid_quota(plan, family):
    table = CompositionTable(CompositionTuning(), army_mix(), every_unit, False)
    # No raid unit stands: with raids on, the quota's items would take their queues.
    world = world_with(DEPOT, offered=EVERYTHING)

    assert table(plan, family, world) == table(HOLD, family, world) != new_table()(plan, family, world)


def test_a_raid_quota_item_takes_the_whole_share_of_its_queue_in_the_shifted_row(table):
    # Against infantry the Allied fix row gives its vehicles 35, which the light tank of the quota takes whole.
    world = world_with(DEPOT, offered=EVERYTHING, enemies=INFANTRY_ARMY)

    assert_mix(table(RAID, ALLIES, world), mix_of({"e1": 50, "e3": 15}, {"1tnk": 35}))


def test_the_raid_quota_s_own_items_are_trained_though_the_mix_never_builds_them(table):
    world = world_with(("weap",), offered=("e1", "e3", "jeep"))

    assert table(RAID, ALLIES, world).vehicle == pytest.approx({"jeep": 0.35})


# -- the strength to attack ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plan, strength",
    [
        (OPENING, 99_999),
        (HOLD, 99_999),
        (RAID, 99_999),
        (PRESS, 3000),
        (STRIKE, 1200),
        (FINISH, 1200),
        (PRESERVE, 99_999),
    ],
)
def test_main_attacks_at_3000_under_press_1200_under_strike_and_finish_and_never_under_the_other_plans(plan, strength):
    assert AttackStrengths(plan_rules_off())(plan) == strength


def test_the_attack_strengths_are_the_tuning_s():
    strengths = AttackStrengths(plan_rules_off(press_attack_strength=2500, finish_attack_strength=900))

    assert (strengths(PRESS), strengths(FINISH), strengths(STRIKE)) == (2500, 900, 1200)


# -- the Soviet alternative with flak filler -------------------------------------------------------

# The Soviet rows of the tuning file's commented alternative, by stage, played with three flak trucks in the mixed raid
# quota and ten points to them in the air shift.
FLAK_POINTS = {
    BASE_STAGE: ({"e1": 45, "e2": 15, "e3": 10}, {"ftrk": 30}),
    DEPOT_STAGE: ({"e1": 35, "e2": 15, "e3": 10}, {"3tnk": 40}),
    RADAR_STAGE: ({"e1": 30, "e2": 15}, {"3tnk": 30, "v2rl": 25}),
}
FLAK_MIX = mix_off(rows={**mix_off().rows, SOVIET: {stage: MixRow(*points) for stage, points in FLAK_POINTS.items()}})
FLAK_BASE_ROW_INFANTRY = FLAK_POINTS[BASE_STAGE][0]


def flak_table(world: World | None = None) -> CompositionTable:
    """A composition table on the alternative with flak filler, its watch on the enemy's air having taken in `world`
    when one is given."""
    air = watching(world) if world is not None else None
    return new_table(CompositionTuning(raid_mixed_vehicles=3), FLAK_MIX, air, AntiAirTuning(air_flak_points=10))


@pytest.mark.parametrize(
    "buildings, stage",
    [(("weap",), BASE_STAGE), (DEPOT, DEPOT_STAGE), (RADAR_TOO, RADAR_STAGE)],
    ids=["base", "fix", "dome"],
)
def test_flak_hold_gives_the_alternative_s_soviet_row_of_the_stage(buildings, stage):
    assert_mix(flak_table()(HOLD, SOVIET, world_with(buildings)), mix_of(*FLAK_POINTS[stage]))


@pytest.mark.parametrize(
    "buildings, enemies, expected",
    [
        (DEPOT, VEHICLE_ARMY, mix_of({"e1": 20, "e2": 15, "e3": 25}, {"3tnk": 40})),
        (RADAR_TOO, VEHICLE_ARMY, mix_of({"e1": 15, "e2": 15, "e3": 15}, {"3tnk": 30, "v2rl": 25})),
        (RADAR_TOO, INFANTRY_ARMY, mix_of({"e1": 40, "e2": 15}, {"3tnk": 15, "v2rl": 30})),
    ],
    ids=["fix, a vehicle army", "dome, a vehicle army: the rockets it had none of", "dome, an infantry army"],
)
def test_flak_the_counter_shift_moves_its_points_in_the_alternative_s_rows(buildings, enemies, expected):
    assert_mix(flak_table()(HOLD, SOVIET, world_with(buildings, enemies=enemies)), expected)


@pytest.mark.parametrize(
    "buildings, enemies, expected",
    [
        (("weap",), (), scaled({"e1": 45, "e2": 15, "e3": 18}, {"ftrk": 30}, 108)),
        (DEPOT, (), scaled({"e1": 35, "e2": 15, "e3": 18}, {"3tnk": 30, "ftrk": 10}, 108)),
        (RADAR_TOO, (), scaled({"e1": 30, "e2": 15, "e3": 8}, {"3tnk": 20, "v2rl": 25, "ftrk": 10}, 108)),
        (RADAR_TOO, VEHICLE_ARMY, scaled({"e1": 15, "e2": 15, "e3": 23}, {"3tnk": 20, "v2rl": 25, "ftrk": 10}, 108)),
        (RADAR_TOO, INFANTRY_ARMY, scaled({"e1": 40, "e2": 15, "e3": 8}, {"3tnk": 5, "v2rl": 30, "ftrk": 10}, 108)),
    ],
    ids=["base: ftrk is the main tank", "fix", "dome", "dome, a vehicle army", "dome, an infantry army"],
)
def test_flak_with_4_aircraft_seen_the_air_shift_moves_10_from_3tnk_to_ftrk_and_adds_a_rocket_squad(
    buildings, enemies, expected
):
    world = world_with(buildings, enemies=enemies)
    air = world_with(buildings, enemies=migs(4))

    assert_mix(flak_table(air)(HOLD, SOVIET, world), expected)


@pytest.mark.parametrize(
    "buildings, grenadiers, infantry",
    [
        ((), 0, {"e2": 70}),
        ((), 5, {"e2": 70}),
        ((), 6, FLAK_BASE_ROW_INFANTRY),
        (("weap",), 0, {"e2": 70}),
        (("weap",), 2, {"e2": 70}),
        (("weap",), 3, FLAK_BASE_ROW_INFANTRY),
        (("weap",), 9, FLAK_BASE_ROW_INFANTRY),
    ],
)
def test_flak_a_soviet_raid_trains_grenadiers_until_six_or_with_a_war_factory_three_and_builds_flak_trucks(
    buildings, grenadiers, infantry
):
    world = world_with(buildings, units=("e2",) * grenadiers + ("e1",) * 7, offered=SOVIET_OFFER)

    assert_mix(flak_table()(RAID, SOVIET, world), mix_of(infantry, {"ftrk": 30}))


@pytest.mark.parametrize("flak_trucks, vehicle", [(0, "ftrk"), (2, "ftrk"), (3, "3tnk")])
def test_flak_a_soviet_raid_with_a_war_factory_builds_flak_trucks_until_it_has_three(flak_trucks, vehicle):
    world = world_with(("weap", "fix"), units=("ftrk",) * flak_trucks, offered=(*SOVIET_OFFER, "3tnk"))

    assert list(flak_table()(RAID, SOVIET, world).vehicle) == [vehicle]


# -- the tank rows ----------------------------------------------------------------------------------

TANK_MIX = mix_off(main_force=TANKS)
ALLIED_TANK_ROWS = {
    BASE_STAGE: mix_of({"e1": 25, "e3": 15}, {"1tnk": 60}),
    DEPOT_STAGE: mix_of({"e1": 15, "e3": 15}, {"2tnk": 60, "1tnk": 10}),
    RADAR_STAGE: mix_of({"e1": 10, "e3": 10}, {"2tnk": 50, "arty": 30}),
}
SOVIET_TANK_ROWS = {
    BASE_STAGE: mix_of({"e1": 50, "e2": 10, "e3": 25}, {"ftrk": 15}),
    DEPOT_STAGE: mix_of({"e1": 15, "e2": 10, "e3": 15}, {"3tnk": 60}),
    RADAR_STAGE: mix_of({"e1": 10, "e2": 5, "e3": 10}, {"3tnk": 50, "v2rl": 25}),
}


def tanks_table(world: World | None = None) -> CompositionTable:
    """A composition table on the tank rows that builds the base rows' vehicles before the service depot, its watch on
    the enemy's air having taken in `world` when one is given."""
    composition = CompositionTuning(main_vehicles_need_depot=False, soviet_infantry_counter=False)
    return new_table(composition, mix=TANK_MIX, air=watching(world) if world is not None else None)


@pytest.mark.parametrize(
    "family, rows", [(ALLIES, ALLIED_TANK_ROWS), (SOVIET, SOVIET_TANK_ROWS)], ids=["allies", "soviets"]
)
@pytest.mark.parametrize(
    "buildings, stage",
    [(("weap",), BASE_STAGE), (DEPOT, DEPOT_STAGE), (RADAR_TOO, RADAR_STAGE)],
    ids=["base", "fix", "dome"],
)
def test_tanks_hold_gives_the_tank_row_of_the_family_s_stage(family, rows, buildings, stage):
    assert_mix(tanks_table()(HOLD, family, world_with(buildings)), rows[stage])


def test_tanks_an_allied_base_trains_light_tanks_before_the_depot_and_medium_tanks_from_it():
    before = tanks_table()(HOLD, ALLIES, world_with(("weap",)))
    after = tanks_table()(HOLD, ALLIES, world_with(DEPOT))

    assert before.vehicle == pytest.approx({"1tnk": 0.6}) and before.infantry == pytest.approx({"e1": 0.25, "e3": 0.15})
    assert max(after.vehicle, key=after.vehicle.get) == "2tnk" and after.vehicle["2tnk"] == pytest.approx(0.6)


def test_tanks_a_soviet_base_keeps_the_mixed_base_row_before_the_depot_and_takes_3tnk_at_60_from_it():
    before = tanks_table()(HOLD, SOVIET, world_with(("weap",)))
    after = tanks_table()(HOLD, SOVIET, world_with(DEPOT))

    assert before == new_table()(HOLD, SOVIET, world_with(("weap",)))
    assert_mix(before, SOVIET_ROWS[BASE_STAGE])
    assert after.vehicle == pytest.approx({"3tnk": 0.6})


@pytest.mark.parametrize("family", [ALLIES, SOVIET])
def test_tanks_with_main_force_mixed_the_mixed_rows_are_played(family):
    world = world_with(DEPOT)

    mixed = new_table(mix=mix_off(main_force=MIXED))(HOLD, family, world)

    assert mixed == new_table()(HOLD, family, world) != tanks_table()(HOLD, family, world)


def test_tanks_counter_a_vehicle_army_moves_15_from_the_rifles_to_the_rockets_of_the_allied_base_row():
    answer = tanks_table()(HOLD, ALLIES, world_with(("weap",), enemies=VEHICLE_ARMY))

    assert_mix(answer, mix_of({"e1": 10, "e3": 30}, {"1tnk": 60}))


def test_tanks_counter_an_infantry_army_moves_10_and_5_from_the_main_tank_to_rifles_and_artillery_at_the_dome():
    allied = tanks_table()(HOLD, ALLIES, world_with(RADAR_TOO, enemies=INFANTRY_ARMY))
    soviet = tanks_table()(HOLD, SOVIET, world_with(RADAR_TOO, enemies=INFANTRY_ARMY))

    assert_mix(allied, mix_of({"e1": 20, "e3": 10}, {"2tnk": 35, "arty": 35}))
    assert_mix(soviet, mix_of({"e1": 20, "e2": 5, "e3": 10}, {"3tnk": 35, "v2rl": 30}))


def test_tanks_counter_an_infantry_army_at_the_allied_base_moves_nothing_with_no_main_tank_in_the_row():
    answer = tanks_table()(HOLD, ALLIES, world_with(("weap",), enemies=INFANTRY_ARMY))

    assert_mix(answer, ALLIED_TANK_ROWS[BASE_STAGE])


@pytest.mark.parametrize(
    "family, buildings, enemies, enemy_buildings, expected",
    [
        (ALLIES, ("weap",), migs(4), (), scaled({"e1": 25, "e3": 23}, {"1tnk": 60}, 108)),
        (SOVIET, DEPOT, migs(4), ("afld",), scaled({"e1": 15, "e2": 10, "e3": 23}, {"3tnk": 60}, 108)),
    ],
    ids=["allies base, 4 migs seen", "soviets fix, 4 migs and an airfield seen: nothing to ftrk"],
)
def test_tanks_air_with_4_aircraft_seen_the_tank_row_gains_a_rocket_squad(
    family, buildings, enemies, enemy_buildings, expected
):
    world = world_with(buildings, enemies=enemies, enemy_buildings=enemy_buildings)

    assert_mix(tanks_table(world)(HOLD, family, world), expected)


@pytest.mark.parametrize("family", [ALLIES, SOVIET])
@pytest.mark.parametrize("buildings", [("weap",), DEPOT, RADAR_TOO], ids=["base", "fix", "dome"])
@pytest.mark.parametrize(
    "enemies", [(), VEHICLE_ARMY, INFANTRY_ARMY, ("mig",) * 8], ids=["no shift", "vehicles", "infantry", "air"]
)
def test_tanks_every_share_is_between_0_and_60_adds_up_to_the_whole_army_and_names_no_never_built_item(
    family, buildings, enemies
):
    world = world_with(buildings, enemies=enemies)

    answer = tanks_table(world)(HOLD, family, world)
    every = [*answer.infantry.values(), *answer.vehicle.values()]

    assert all(0 < share <= 0.6 for share in every) and sum(every) == pytest.approx(1.0)
    assert NEVER_BUILT.isdisjoint({*answer.infantry, *answer.vehicle}) and "4tnk" not in answer.vehicle


@pytest.mark.parametrize("plan", [RAID, PRESS])
@pytest.mark.parametrize("light_tanks, vehicle", [(0, "1tnk"), (3, "1tnk")])
def test_tanks_an_allied_raid_short_of_its_quota_takes_the_vehicle_queue_s_whole_share_for_light_tanks(
    plan, light_tanks, vehicle
):
    world = world_with(DEPOT, units=("1tnk",) * light_tanks, offered=(*ALLIED_OFFER, "2tnk"))

    answer = tanks_table()(plan, ALLIES, world)

    assert answer.vehicle == pytest.approx({vehicle: 0.7}) and "2tnk" not in answer.vehicle


def test_tanks_an_allied_raid_with_its_quota_met_makes_the_tank_row():
    world = world_with(DEPOT, units=("1tnk",) * 4, offered=(*ALLIED_OFFER, "2tnk"))

    assert_mix(tanks_table()(RAID, ALLIES, world), ALLIED_TANK_ROWS[DEPOT_STAGE])


@pytest.mark.parametrize("grenadiers, infantry", [(0, {"e2": 40}), (3, {"e1": 15, "e2": 10, "e3": 15})])
def test_tanks_a_soviet_raid_short_of_its_grenadiers_takes_the_infantry_queue_s_whole_share_for_them(
    grenadiers, infantry
):
    world = world_with(DEPOT, units=("e2",) * grenadiers, offered=(*SOVIET_OFFER, "3tnk"))

    assert_mix(tanks_table()(RAID, SOVIET, world), mix_of(infantry, {"3tnk": 60}))


# -- the core tank mix: the shipped tank rows ---------------------------------------------------------

SHIPPED = Tuning()
STEK_TOO = (*RADAR_TOO, TECH_CENTER)
# Each family's main items besides what the shared offer of these tests has.
ALLIED_MAIN = ("2tnk", "arty")
SOVIET_MAIN = ("3tnk", "v2rl", "4tnk")


def shipped_table(world: World | None = None) -> CompositionTable:
    """A composition table of the shipped tuning, its watch on the enemy's air having taken in `world` if given."""
    mix = ArmyMix(SHIPPED.mix, SHIPPED.anti_air, watching(world) if world is not None else AirWatch())
    return CompositionTable(SHIPPED.composition, mix, every_unit, True)


@pytest.mark.parametrize(
    "offered, vehicle",
    [
        ((*ALLIED_OFFER, "2tnk"), {"2tnk": 0.85}),
        ((*ALLIED_OFFER, *ALLIED_MAIN), {"2tnk": 0.64, "arty": 0.21}),
    ],
    ids=["no arty on offer", "arty on offer"],
)
def test_army_build_allies_with_a_depot_queue_2tnk_64_arty_21_and_e3_15_and_no_1tnk(offered, vehicle):
    answer = shipped_table()(HOLD, ALLIES, world_with(DEPOT, offered=offered))

    assert answer.vehicle == pytest.approx(vehicle) and set(answer.vehicle) == set(vehicle)
    assert answer.infantry == pytest.approx({"e3": 0.15}) and set(answer.infantry) == {"e3"}


def test_army_build_allies_with_a_radar_queue_2tnk_64_arty_21_and_e3_15_and_no_1tnk():
    answer = shipped_table()(HOLD, ALLIES, world_with(RADAR_TOO, offered=(*ALLIED_OFFER, *ALLIED_MAIN)))

    assert_mix(answer, mix_of({"e3": 15}, {"2tnk": 64, "arty": 21}))


@pytest.mark.parametrize(
    "buildings, infantry, vehicle",
    [
        (DEPOT, {"e3": 12}, {"3tnk": 88}),
        (RADAR_TOO, {"e3": 12}, {"3tnk": 70, "v2rl": 18}),
        (STEK_TOO, {"e3": 10}, {"3tnk": 40, "4tnk": 34, "v2rl": 16}),
    ],
    ids=["fix, no v2rl on offer", "dome", "stek"],
)
def test_army_build_soviets_field_the_heavy_tank_with_v2s_rockets_and_the_mammoth_once_a_tech_center_stands(
    buildings, infantry, vehicle
):
    offered = (*SOVIET_OFFER, "3tnk") if buildings == DEPOT else (*SOVIET_OFFER, *SOVIET_MAIN)

    answer = shipped_table()(HOLD, SOVIET, world_with(buildings, offered=offered))

    assert_mix(answer, mix_of(infantry, vehicle))


# Each item's cost as items.toml gives it, which the rows' points are the value of.
COSTS = {"2tnk": 850, "arty": 850, "3tnk": 1150, "4tnk": 2000, "v2rl": 900, "e3": 300}


@pytest.mark.parametrize(
    "family, buildings, offered, tanks",
    [
        (ALLIES, RADAR_TOO, (*ALLIED_OFFER, *ALLIED_MAIN), ("2tnk",)),
        (SOVIET, RADAR_TOO, (*SOVIET_OFFER, *SOVIET_MAIN), ("3tnk",)),
        (SOVIET, STEK_TOO, (*SOVIET_OFFER, *SOVIET_MAIN), ("3tnk", "4tnk")),
    ],
    ids=["allies", "soviets", "soviets at the stek"],
)
def test_army_build_with_12_tanks_the_mix_asks_for_about_4_artillery_and_8_rockets(family, buildings, offered, tanks):
    answer = shipped_table()(HOLD, family, world_with(buildings, offered=offered))
    shares = {**answer.infantry, **answer.vehicle}
    # Units per credit of the army's value, scaled so that the tanks come to 12.
    per_credit = {item: share / COSTS[item] for item, share in shares.items()}
    scale = 12 / sum(per_credit[tank] for tank in tanks)
    support = "arty" if family == ALLIES else "v2rl"

    assert per_credit[support] * scale == pytest.approx(4, abs=0.25)
    assert per_credit["e3"] * scale == pytest.approx(8, abs=0.5)


def test_army_build_soviets_at_the_stek_split_the_tanks_2_to_1_between_the_heavy_and_the_mammoth():
    answer = shipped_table()(HOLD, SOVIET, world_with(STEK_TOO, offered=(*SOVIET_OFFER, *SOVIET_MAIN)))

    heavy, mammoth = (answer.vehicle[tank] / COSTS[tank] for tank in ("3tnk", "4tnk"))

    assert heavy / mammoth == pytest.approx(2, abs=0.1)


def test_core_tank_mix_the_mammoth_s_share_goes_to_the_others_while_it_is_not_on_offer():
    answer = shipped_table()(HOLD, SOVIET, world_with(STEK_TOO, offered=(*SOVIET_OFFER, "3tnk", "v2rl")))

    expected = {"3tnk": 0.40 / 0.56 * 0.90, "v2rl": 0.16 / 0.56 * 0.90}
    assert answer.vehicle == pytest.approx(expected) and "4tnk" not in answer.vehicle


def test_core_tank_mix_a_set_without_a_stek_row_plays_its_dome_row_at_the_stek_stage():
    mixed = new_table(mix=mix_off(main_force=MIXED))

    for family in (ALLIES, SOVIET):
        assert mixed(HOLD, family, world_with(STEK_TOO)) == mixed(HOLD, family, world_with(RADAR_TOO))


def test_core_tank_mix_an_infantry_army_at_the_stek_stage_moves_10_and_5_from_the_heavy_tank_to_rifles_and_v2s():
    world = world_with(STEK_TOO, enemies=INFANTRY_ARMY, offered=(*SOVIET_OFFER, *SOVIET_MAIN))
    composition = dataclasses.replace(SHIPPED.composition, soviet_infantry_counter=False)
    table = CompositionTable(composition, ArmyMix(SHIPPED.mix, SHIPPED.anti_air, AirWatch()), every_unit, True)

    assert_mix(table(HOLD, SOVIET, world), mix_of({"e1": 10, "e3": 10}, {"3tnk": 25, "4tnk": 34, "v2rl": 21}))


@pytest.mark.parametrize("family", [ALLIES, SOVIET])
@pytest.mark.parametrize("buildings", [DEPOT, RADAR_TOO, STEK_TOO], ids=["fix", "dome", "stek"])
@pytest.mark.parametrize(
    "enemies", [(), VEHICLE_ARMY, INFANTRY_ARMY, ("yak",)], ids=["no shift", "vehicles", "infantry", "air"]
)
def test_core_tank_mix_every_shipped_row_adds_up_to_the_whole_army_with_no_light_tank_for_main(
    family, buildings, enemies
):
    world = world_with(buildings, enemies=enemies, offered=(*EVERYTHING, "4tnk"))

    answer = shipped_table(world)(HOLD, family, world)
    every = [*answer.infantry.values(), *answer.vehicle.values()]

    assert all(0 < share <= 0.95 for share in every) and sum(every) == pytest.approx(1.0)
    assert "1tnk" not in answer.vehicle and NEVER_BUILT.isdisjoint({*answer.infantry, *answer.vehicle})


# -- the core tank mix: no main vehicle before the service depot ---------------------------------------


@pytest.mark.parametrize("plan", [HOLD, STRIKE, FINISH])
def test_core_tank_mix_allies_before_the_depot_queue_no_main_vehicle_and_keep_their_rockets(plan):
    answer = shipped_table()(plan, ALLIES, world_with(("weap",), offered=ALLIED_OFFER))

    assert_mix(answer, mix_of({"e3": 5}))


@pytest.mark.parametrize("plan", [RAID, PRESS])
@pytest.mark.parametrize("light_tanks, vehicle", [(0, {"1tnk": 0.95}), (3, {"1tnk": 0.95}), (4, {})])
def test_core_tank_mix_allies_before_the_depot_train_light_tanks_only_for_the_raid_quota(plan, light_tanks, vehicle):
    world = world_with(("weap",), units=("1tnk",) * light_tanks, offered=ALLIED_OFFER)

    answer = shipped_table()(plan, ALLIES, world)

    assert answer.vehicle == pytest.approx(vehicle) and set(answer.vehicle) == set(vehicle)


@pytest.mark.parametrize("plan", [HOLD, RAID, PRESS, STRIKE])
def test_core_tank_mix_soviets_before_the_depot_queue_no_flak_truck_for_main(plan):
    answer = shipped_table()(plan, SOVIET, world_with(("weap",), offered=SOVIET_OFFER))

    assert answer.vehicle == {}


def test_core_tank_mix_with_the_switch_off_the_base_row_s_vehicles_are_trained_before_the_depot():
    mix = ArmyMix(SHIPPED.mix, SHIPPED.anti_air, AirWatch())
    table = CompositionTable(CompositionTuning(main_vehicles_need_depot=False), mix, every_unit, True)

    assert table(HOLD, ALLIES, world_with(("weap",), offered=ALLIED_OFFER)).vehicle == pytest.approx({"1tnk": 0.95})
    assert table(HOLD, SOVIET, world_with(("weap",), offered=SOVIET_OFFER)).vehicle == pytest.approx({"ftrk": 0.95})


@pytest.mark.parametrize("family", [ALLIES, SOVIET])
def test_core_tank_mix_the_switch_leaves_the_mixed_rows_before_the_depot(family):
    world = world_with(("weap",))

    switched_on = new_table(CompositionTuning(main_vehicles_need_depot=True))(HOLD, family, world)

    assert switched_on == new_table(CompositionTuning(main_vehicles_need_depot=False))(HOLD, family, world)
    assert switched_on.vehicle != {}


# -- a standing raid group ---------------------------------------------------------------------------

STANDING_PLANS = [HOLD, RAID, PRESS, STRIKE, FINISH]


def standing_table(raids_enabled: bool = True) -> CompositionTable:
    """A composition table of a standing raid group, with the switches-off thresholds."""
    return CompositionTable(CompositionTuning(), army_mix(), every_unit, raids_enabled, standing=True)


@pytest.mark.parametrize("plan", STANDING_PLANS)
@pytest.mark.parametrize("light_tanks, vehicle", [(0, "1tnk"), (3, "1tnk"), (4, "2tnk")])
def test_standing_the_allied_quota_s_light_tank_takes_the_vehicle_queue_under_every_plan_the_group_raids_in(
    plan, light_tanks, vehicle
):
    world = world_with(DEPOT, units=("1tnk",) * light_tanks, offered=(*ALLIED_OFFER, "2tnk"))

    assert_mix(standing_table()(plan, ALLIES, world), mix_of({"e1": 40, "e3": 15}, {vehicle: 45}))


@pytest.mark.parametrize("plan", [HOLD, STRIKE, FINISH])
@pytest.mark.parametrize("family", [ALLIES, SOVIET])
def test_standing_under_hold_strike_and_finish_a_short_quota_is_filled_first_as_under_raid_without_the_switch(
    plan, family
):
    world = world_with(DEPOT, offered=EVERYTHING)

    answer = standing_table()(plan, family, world)

    assert answer == new_table()(RAID, family, world) != new_table()(plan, family, world)


@pytest.mark.parametrize("plan", [OPENING, PRESERVE])
@pytest.mark.parametrize("family", [ALLIES, SOVIET])
def test_standing_opening_and_preserve_make_what_they_make_without_the_switch(plan, family):
    world = world_with(DEPOT, offered=EVERYTHING)

    assert standing_table()(plan, family, world) == new_table()(plan, family, world)


@pytest.mark.parametrize("plan", STANDING_PLANS)
def test_standing_with_raids_switched_off_no_plan_fills_a_raid_quota(plan):
    world = world_with(DEPOT, offered=EVERYTHING)

    assert standing_table(raids_enabled=False)(plan, ALLIES, world) == new_table()(HOLD, ALLIES, world)


# -- an Allied raid of light tanks only ----------------------------------------------------------------


def light_tank_table(size: int = 6) -> CompositionTable:
    """A composition table with the Allied raid of `size` light tanks only."""
    return new_table(CompositionTuning(allied_raid_light_tanks=True, raid_vehicles=size))


@pytest.mark.parametrize("plan", [RAID, PRESS])
@pytest.mark.parametrize("light_tanks, vehicle", [(0, "1tnk"), (5, "1tnk"), (6, "2tnk"), (7, "2tnk")])
def test_light_tank_raid_the_vehicle_queue_trains_light_tanks_while_fewer_than_six_count(plan, light_tanks, vehicle):
    world = world_with(DEPOT, units=("1tnk",) * light_tanks, offered=(*ALLIED_OFFER, "2tnk"))

    assert_mix(light_tank_table()(plan, ALLIES, world), mix_of({"e1": 40, "e3": 15}, {vehicle: 45}))


@pytest.mark.parametrize("size", [4, 8])
def test_light_tank_raid_the_quota_follows_raid_vehicles(size):
    def vehicles(light_tanks: int) -> list[str]:
        world = world_with(DEPOT, units=("1tnk",) * light_tanks, offered=(*ALLIED_OFFER, "2tnk"))
        return list(light_tank_table(size)(RAID, ALLIES, world).vehicle)

    assert (vehicles(size - 1), vehicles(size)) == (["1tnk"], ["2tnk"])


def test_light_tank_raid_never_trains_a_jeep_when_the_light_tank_is_not_on_offer():
    world = world_with(("weap",), offered=("e1", "e3", "jeep"))

    assert light_tank_table()(RAID, ALLIES, world).vehicle == {}
    assert new_table()(RAID, ALLIES, world).vehicle == pytest.approx({"jeep": 0.35})


@pytest.mark.parametrize("buildings", [(), ("weap",), DEPOT], ids=["no war factory", "a war factory", "a depot"])
@pytest.mark.parametrize("grenadiers", [0, 3, 6])
def test_light_tank_raid_the_switch_leaves_the_soviet_answer_as_it_was(buildings, grenadiers):
    world = world_with(buildings, units=("e2",) * grenadiers, offered=(*SOVIET_OFFER, "3tnk"))

    assert light_tank_table()(RAID, SOVIET, world) == new_table()(RAID, SOVIET, world)


# -- the Soviet raid the composition picks ------------------------------------------------------------


def soviet_raid_table(raid: str) -> CompositionTable:
    """A composition table with the Soviet raid `soviet_raid` names."""
    return new_table(CompositionTuning(soviet_raid=raid))


@pytest.mark.parametrize("plan", [RAID, PRESS])
@pytest.mark.parametrize("buildings", [(), ("weap",), DEPOT], ids=["no war factory", "a war factory", "a depot"])
def test_apc_raid_with_none_the_soviet_raiding_plans_make_hold_s_mix_and_no_raid_quota(plan, buildings):
    world = world_with(buildings, offered=EVERYTHING)

    answer = soviet_raid_table("none")(plan, SOVIET, world)

    assert answer == soviet_raid_table("none")(HOLD, SOVIET, world) != new_table()(plan, SOVIET, world)


@pytest.mark.parametrize("buildings", [(), ("weap",), DEPOT], ids=["no war factory", "a war factory", "a depot"])
@pytest.mark.parametrize("grenadiers", [0, 3, 6])
def test_apc_raid_with_infantry_the_soviet_answer_is_today_s(buildings, grenadiers):
    world = world_with(buildings, units=("e2",) * grenadiers, offered=(*SOVIET_OFFER, "3tnk"))

    assert soviet_raid_table("infantry")(RAID, SOVIET, world) == new_table()(RAID, SOVIET, world)


@pytest.mark.parametrize("raid", ["apc_rockets", "none"])
@pytest.mark.parametrize("light_tanks", [0, 4])
def test_apc_raid_an_allied_answer_is_unaffected_by_the_soviet_raid(raid, light_tanks):
    world = world_with(DEPOT, units=("1tnk",) * light_tanks, offered=(*ALLIED_OFFER, "2tnk"))

    assert soviet_raid_table(raid)(RAID, ALLIES, world) == new_table()(RAID, ALLIES, world)


# -- the Soviet raid of rockets carried by APCs -----------------------------------------------------------

SOVIET_BASE = ("barr", "weap")


@pytest.mark.parametrize("plan", [RAID, PRESS])
@pytest.mark.parametrize(
    "units, infantry, vehicle",
    [
        ((), {"e3": 85}, {"apc": 15}),
        (("apc",) * 2 + ("e3",) * 5, {"e3": 85}, {"ftrk": 15}),
        (("apc",) + ("e3",) * 10, {"e1": 50, "e2": 10, "e3": 25}, {"apc": 15}),
        (("apc",) * 2 + ("e3",) * 10, {"e1": 50, "e2": 10, "e3": 25}, {"ftrk": 15}),
    ],
    ids=["none counted", "two APCs and five rockets", "one APC and ten rockets", "the quota met"],
)
def test_apc_raid_the_quota_trains_apcs_and_rockets_first_while_short_of_them(plan, units, infantry, vehicle):
    world = world_with(SOVIET_BASE, units=units, offered=SOVIET_OFFER)

    assert_mix(soviet_raid_table("apc_rockets")(plan, SOVIET, world), mix_of(infantry, vehicle))


def test_apc_raid_the_apc_is_trained_for_the_raid_though_the_mix_never_builds_it():
    world = world_with(SOVIET_BASE, offered=SOVIET_OFFER)

    assert "apc" in NEVER_BUILT and "apc" not in soviet_raid_table("apc_rockets")(HOLD, SOVIET, world).vehicle
    assert soviet_raid_table("apc_rockets")(RAID, SOVIET, world).vehicle == pytest.approx({"apc": 0.15})


@pytest.mark.parametrize(
    "buildings, offered",
    [(("barr",), SOVIET_OFFER), (("weap",), SOVIET_OFFER), (SOVIET_BASE, ("e1", "e2", "e3", "ftrk"))],
    ids=["no war factory", "no barracks", "no APC on offer"],
)
def test_apc_raid_without_a_war_factory_and_a_barracks_or_an_apc_on_offer_no_apc_is_trained(buildings, offered):
    world = world_with(buildings, offered=offered)

    assert "apc" not in soviet_raid_table("apc_rockets")(RAID, SOVIET, world).vehicle


# -- the Soviet infantry counter: flamethrowers and V2s against massed infantry -----------------------------------

# 30 rifles and 5 medium tanks (86% infantry by count), and 5 rifles and 10 medium tanks (33%).
RIFLE_HEAVY = ("e1",) * 30 + ("2tnk",) * 5
TANK_HEAVY = ("e1",) * 5 + ("2tnk",) * 10
# A flame tower stands, which puts the flamethrower on offer.
FLAME_TOWER = ("ftur",)
FLAMER_OFFER = (*SOVIET_OFFER, *SOVIET_MAIN, "e4")


def assert_row_holds_100(answer: Composition) -> None:
    shares_of_row = [*answer.infantry.values(), *answer.vehicle.values()]
    assert sum(shares_of_row) == pytest.approx(1.0) and max(shares_of_row) <= 0.95 + 1e-9


def test_soviet_infantry_counter_rifle_heavy_enemy_at_the_dome_queues_e4_20_and_v2s_15_points_higher():
    world = world_with((*RADAR_TOO, *FLAME_TOWER), offered=FLAMER_OFFER, enemies=RIFLE_HEAVY)
    without = shipped_table()(HOLD, SOVIET, world_with((*RADAR_TOO, *FLAME_TOWER), offered=FLAMER_OFFER))

    answer = shipped_table()(HOLD, SOVIET, world)

    assert_mix(answer, mix_of({"e3": 12, "e4": 20}, {"3tnk": 35, "v2rl": 33}))
    assert answer.vehicle["v2rl"] - without.vehicle["v2rl"] == pytest.approx(0.15)
    assert_row_holds_100(answer)


def test_soviet_infantry_counter_tank_heavy_enemy_at_the_dome_queues_no_e4_and_the_shipped_row():
    world = world_with((*RADAR_TOO, *FLAME_TOWER), offered=FLAMER_OFFER, enemies=TANK_HEAVY)

    answer = shipped_table()(HOLD, SOVIET, world)

    assert_mix(answer, mix_of({"e3": 12}, {"3tnk": 70, "v2rl": 18}))


def test_soviet_infantry_counter_without_a_flame_tower_the_flamers_points_go_to_the_v2s():
    world = world_with(RADAR_TOO, offered=(*SOVIET_OFFER, *SOVIET_MAIN), enemies=RIFLE_HEAVY)

    answer = shipped_table()(HOLD, SOVIET, world)

    assert_mix(answer, mix_of({"e3": 12}, {"3tnk": 35, "v2rl": 53}))


def test_soviet_infantry_counter_at_the_stek_stage_moves_from_the_heavy_tank_and_keeps_the_mammoths():
    world = world_with((*STEK_TOO, *FLAME_TOWER), offered=FLAMER_OFFER, enemies=RIFLE_HEAVY)

    answer = shipped_table()(HOLD, SOVIET, world)

    assert_mix(answer, mix_of({"e3": 10, "e4": 20}, {"3tnk": 5, "4tnk": 34, "v2rl": 31}))


@pytest.mark.parametrize(
    "composition, buildings, family",
    [
        (CompositionTuning(soviet_infantry_counter=False), RADAR_TOO, SOVIET),
        (CompositionTuning(), DEPOT, SOVIET),
        (CompositionTuning(), RADAR_TOO, ALLIES),
    ],
    ids=["switch off", "fix stage", "allies"],
)
def test_soviet_infantry_counter_moves_nothing_off_the_switch_before_the_dome_or_for_the_allies(
    composition, buildings, family
):
    world = world_with((*buildings, *FLAME_TOWER), offered=(*FLAMER_OFFER, *ALLIED_MAIN), enemies=RIFLE_HEAVY)
    plain = world_with((*buildings, *FLAME_TOWER), offered=(*FLAMER_OFFER, *ALLIED_MAIN))
    table = CompositionTable(composition, ArmyMix(SHIPPED.mix, SHIPPED.anti_air, AirWatch()), every_unit, True)

    assert table(HOLD, family, world) == table(HOLD, family, plain)


def test_soviet_infantry_counter_the_mixed_rows_are_left_as_they_are():
    world = world_with((*RADAR_TOO, *FLAME_TOWER), offered=FLAMER_OFFER, enemies=RIFLE_HEAVY)

    answer = new_table()(HOLD, SOVIET, world)

    assert "e4" not in answer.infantry


def test_soviet_infantry_counter_counts_only_enemies_seen_in_the_last_120_seconds():
    seen = world_with(enemies=RIFLE_HEAVY, tick=TICK)
    within = world_with(world=world_with(enemies=RIFLE_HEAVY, tick=TICK), tick=TICK + 120 * 25)
    after = world_with(world=world_with(enemies=RIFLE_HEAVY, tick=TICK), tick=TICK + 120 * 25 + 1)

    assert [is_infantry_heavy(world, 0.6) for world in (seen, within, after)] == [True, True, False]
    assert not is_infantry_heavy(world_with(), 0.6)


@pytest.mark.parametrize("infantry, tanks, heavy", [(6, 4, True), (5, 5, False), (3, 0, True), (0, 3, False)])
def test_soviet_infantry_counter_infantry_heavy_is_at_least_the_share_by_count(infantry, tanks, heavy):
    world = world_with(enemies=("e1",) * infantry + ("2tnk",) * tanks)

    assert is_infantry_heavy(world, 0.6) is heavy


@pytest.mark.parametrize("buildings", [RADAR_TOO, STEK_TOO], ids=["dome", "stek"])
@pytest.mark.parametrize("flame_tower", [True, False], ids=["e4 on offer", "e4 off offer"])
@pytest.mark.parametrize(
    "enemies, enemy_buildings",
    [(RIFLE_HEAVY, ()), (RIFLE_HEAVY, ("afld",)), (("e1",) * 20 + ("1tnk",), ())],
    ids=["rifles", "rifles and air", "rifles by value too"],
)
def test_soviet_infantry_counter_every_row_holds_100_with_no_share_above_95(
    buildings, flame_tower, enemies, enemy_buildings
):
    offered = FLAMER_OFFER if flame_tower else (*SOVIET_OFFER, *SOVIET_MAIN)
    world = world_with((*buildings, *FLAME_TOWER), offered=offered, enemies=enemies, enemy_buildings=enemy_buildings)

    answer = shipped_table(world)(HOLD, SOVIET, world)

    assert_row_holds_100(answer)
    assert ("e4" in answer.infantry) is flame_tower


# -- the counter quota table ---------------------------------------------------------------------------------------


def quota_context(tuning: Tuning = SHIPPED) -> SimpleNamespace:
    """What `counter_quota` reads of a match context: the tuning and the shipped army mix, which gives the artillery
    and V2s points at the dome stage."""
    army_mix = ArmyMix(tuning.mix, tuning.anti_air, AirWatch())
    return SimpleNamespace(tuning=tuning, army_mix=army_mix, approach_alarm=False)


def wanted(family: str, world: World, tuning: Tuning = SHIPPED) -> dict[str, int]:
    return dict(counter_quota(family, world, quota_context(tuning)).wanted)


ROCKETS_AND_TANKS = ("e3",) * 10 + ("2tnk",) * 4
# The Soviet war factory and barracks with a flame tower; and the same with a radar dome and service depot.
SOVIET_BASE = ("weap", "barr", "ftur")
SOVIET_DOME = (*SOVIET_BASE, "fix", "dome")
NO_V2_OFFER = ("e1", "e2", "e3", "e4", "3tnk")


def test_soviet_flamers_per_two_rockets():
    world = world_with(SOVIET_DOME, offered=NO_V2_OFFER, enemies=ROCKETS_AND_TANKS)

    quota = wanted(SOVIET, world)

    # 10 rockets: 5 flamers; 4 tanks: 2 rockets against them.
    assert quota == {"e4": 5, "e3": 2}
    assert "e2" not in quota


def test_soviet_v2_counter_when_v2_in_production():
    world = world_with(SOVIET_DOME, offered=(*NO_V2_OFFER, "v2rl"), enemies=ROCKETS_AND_TANKS)

    assert wanted(SOVIET, world) == {"v2rl": 5, "e3": 2}


def test_soviet_flamers_wanted_while_no_flame_tower_puts_them_on_offer():
    world = world_with(("weap", "barr"), offered=("e1", "e2", "e3", "3tnk"), enemies=ROCKETS_AND_TANKS)

    assert wanted(SOVIET, world) == {"e4": 5, "e3": 2}


def test_allied_rifles_and_jeeps():
    world = world_with(("weap", "tent"), offered=("e1", "e3", "jeep", "1tnk"), enemies=("e3",) * 8)

    assert wanted(ALLIES, world) == {"jeep": 2, "e1": 2}


def test_allied_rifles_alone_before_a_war_factory():
    world = world_with(("tent",), offered=("e1", "e3", "jeep"), enemies=("e3",) * 8)

    assert wanted(ALLIES, world) == {"e1": 4}


def test_allied_artillery_counter_when_arty_in_production():
    offered = ("e1", "e3", "jeep", "2tnk", "arty")
    world = world_with(("weap", "tent", "fix", "dome"), offered=offered, enemies=("e3",) * 8)

    assert wanted(ALLIES, world) == {"arty": 4}


def test_other_infantry_one_per_four():
    world = world_with(("tent",), offered=("e1", "e3"), enemies=("e1",) * 12)

    assert wanted(ALLIES, world) == {"e1": 3}


def test_not_infantry_heavy_no_anti_infantry():
    world = world_with(("weap", "tent"), offered=("e1", "e3", "jeep"), enemies=("e3",) * 3 + ("2tnk",) * 10)

    # 3 of 13 is infantry; 10 tanks: 5 rockets.
    assert wanted(ALLIES, world) == {"e3": 5}


@pytest.mark.parametrize(
    "enemies, heavy",
    [(("e1",) * 6 + ("2tnk",) * 9, True), (("e1",) * 6 + ("2tnk",) * 10, False), (("e1",) * 5, False)],
    ids=["6 of 15", "6 of 16", "5 alone"],
)
def test_infantry_heavy_needs_6_and_40_percent_of_the_units_counted(enemies, heavy):
    assert is_enemy_infantry_heavy(world_with(enemies=enemies), CompositionTuning()) is heavy


def test_aircraft_are_not_counted_toward_the_quota():
    world = world_with(enemies=("e1",) * 6 + ("yak",) * 20)

    assert enemy_counted(world, CompositionTuning()) == EnemyCounted(rockets=0, infantry=6, tanks=0, units=6)


def test_quota_drifts_back_after_120s():
    seen = world_with(("tent",), offered=("e1", "e3"), enemies=("e3",) * 10, tick=TICK)
    within = world_with(("tent",), offered=("e1", "e3"), world=seen, tick=TICK + 120 * TICKS_PER_SECOND)
    within_quota = wanted(ALLIES, within)
    after = world_with(("tent",), offered=("e1", "e3"), world=within, tick=TICK + 130 * TICKS_PER_SECOND)

    assert within_quota == {"e1": 5}
    assert wanted(ALLIES, after) == {}


def test_counter_quota_switch_off_wants_nothing():
    off = dataclasses.replace(SHIPPED, composition=dataclasses.replace(SHIPPED.composition, counter_quota=False))
    world = world_with(SOVIET_DOME, offered=NO_V2_OFFER, enemies=ROCKETS_AND_TANKS)

    assert wanted(SOVIET, world, off) == {}
