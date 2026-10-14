"""Focus fire: which enemy each unit of an engaged group attacks, and the class of the orders it is given."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from situations import REPO_ROOT, fixture_state, tracked_items
from ucla_agent.core.config import ConfigError
from ucla_agent.games.redalert import targeting
from ucla_agent.games.redalert.batch import Intent, Priority
from ucla_agent.games.redalert.fight import centroid, estimate
from ucla_agent.games.redalert.items import NO_DAMAGE, ItemTable
from ucla_agent.games.redalert.tuning import TUNING_FILE, CombatTuning, Tuning
from ucla_agent.games.redalert.world import World

TICK = 100
# The [combat] keys of the aircraft handling: the switch, the weight of an unarmed aircraft, the weight of enemy air
# a group cannot answer.
AIR_KEYS = ("air_targeting", "focus_weight_harmless_air", "air_unanswered_weight")
# The [combat] values of a looser focus: counter power 1, a window of 3 s with overkill 1.5, retarget gain 1.5, no
# effectiveness floor and no minimum hold. The tests of the coverage, keeping and spill rules are worked out in them.
LOOSE_FOCUS = replace(
    Tuning(),
    combat=replace(
        CombatTuning(),
        counter_power=1.0,
        focus_window_seconds=3,
        overkill_factor=1.5,
        retarget_gain=1.5,
        focus_min_effectiveness=0.0,
        focus_min_hold_seconds=0,
    ),
)
# The looser focus with the focus fire in contact off, for the assignment its spill floor and per-unit reach leave
# alone.
FOCUS_OFF = replace(LOOSE_FOCUS, combat=replace(LOOSE_FOCUS.combat, contact_focus=False))


def own(unit_id: int, unit_type: str, x: int, y: int, hp: int = 100) -> dict:
    """One own unit as an observation lists it."""
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": hp, "act": None, "idle": True}


def foe(enemy_id: int, enemy_type: str, x: int, y: int, hp: int = 100) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": hp}


def seen(units=(), enemies=(), buildings=(), items: ItemTable | None = None) -> World:
    """A world after one state that shows exactly these own units and enemies."""
    world = World(items or tracked_items())
    world.update(
        fixture_state(
            "opening_mcv",
            TICK,
            units=list(units),
            visible_enemies=list(enemies),
            visible_enemy_buildings=list(buildings),
        )
    )
    return world


# -- contact ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("distance, in_contact", [(7, True), (8, False)], ids=["7 cells", "8 cells"])
def test_a_rifle_is_in_contact_with_an_enemy_within_its_range_of_5_and_2_cells(distance, in_contact):
    world = seen([own(300, "e1", 40, 48)], [foe(500, "e1", 40 + distance, 48)])

    assert targeting.is_in_contact(world.units[300], world.enemy_memory.values(), world) is in_contact


def test_contact_is_measured_along_the_diagonal_too():
    # 5 cells across and 5 down is 7.07 cells off: out of a rifle's 7, inside a flak truck's 6 + 2.
    world = seen([own(300, "e1", 40, 48), own(301, "ftrk", 40, 48)], [foe(500, "e1", 45, 53)])
    enemies = list(world.enemy_memory.values())

    assert not targeting.is_in_contact(world.units[300], enemies, world)
    assert targeting.is_in_contact(world.units[301], enemies, world)


def test_a_unit_the_damage_table_does_not_know_is_in_contact_only_within_2_cells():
    world = seen([own(300, "zzz", 40, 48), own(301, "zzz", 40, 49)], [foe(500, "e1", 40, 51)])
    enemies = list(world.enemy_memory.values())

    assert [targeting.is_in_contact(world.units[unit_id], enemies, world) for unit_id in (300, 301)] == [False, True]


@pytest.mark.parametrize(
    "unit_type, enemy_type, distance, in_contact",
    [
        ("e3", "yak", 7, True),
        ("e3", "yak", 9, True),
        ("e3", "yak", 10, False),
        ("e3", "e1", 9, False),
        ("e1", "yak", 7, True),
        ("e1", "yak", 8, False),
    ],
    ids=["e3 yak 7", "e3 yak 9", "e3 yak 10", "e3 rifle 9", "e1 yak 7", "e1 yak 8"],
)
def test_against_an_aircraft_contact_is_measured_by_the_anti_air_range_where_it_reaches_farther(
    unit_type, enemy_type, distance, in_contact
):
    # A rocket soldier's anti-air range is 7.5 and its ground range 5: an aircraft is in contact within 9.5 cells,
    # anything else within 7. A rifle has no anti-air weapon and keeps its 7 against both.
    world = seen([own(300, unit_type, 40, 48)], [foe(500, enemy_type, 40 + distance, 48)])

    assert targeting.is_in_contact(world.units[300], world.enemy_memory.values(), world) is in_contact


def test_an_e3_attacking_an_aircraft_a_yak_7_or_9_cells_off_is_a_contact_order():
    for distance in (7, 9):
        world = seen([own(300, "e3", 40, 48)], [foe(500, "yak", 40 + distance, 48)])
        units = list(world.units.values())

        (intent,) = targeting.FocusFire().orders(units, centroid(units), world, Tuning())

        assert (intent.order, intent.priority) == (
            {"cmd": "attack", "units": [300], "target": 500},
            Priority.CONTACT,
        ), distance


def test_an_attack_is_a_contact_order_when_one_of_its_units_is_in_contact_and_a_move_order_otherwise():
    world = seen([own(300, "e1", 40, 48), own(301, "e1", 30, 48)], [foe(500, "e1", 46, 48)])
    near, far = world.units[300], world.units[301]
    enemies = list(world.enemy_memory.values())

    assert targeting.attack_class([far, near], enemies, world) == Priority.CONTACT
    assert targeting.attack_class([far], enemies, world) == Priority.MOVE
    assert targeting.attack_class([near], [], world) == Priority.MOVE


# -- the assignment --------------------------------------------------------------------------------


def assigned(world: World, current: dict[int, int] | None = None, tuning: Tuning | None = None) -> dict[int, list[int]]:
    """The units the assignment gives each enemy around all of the world's own units, by enemy id."""
    tuning = tuning or Tuning()
    units = list(world.units.values())
    ranked = targeting.targets(units, centroid(units), world, tuning)
    return {
        assignment.target.id: list(assignment.attackers)
        for assignment in targeting.assign(units, ranked, world, tuning, current)
    }


def units_of(world: World) -> list[dict]:
    """The world's own units as an observation lists them."""
    return [own(unit.id, unit.type, unit.x, unit.y, unit.hp) for unit in world.units.values()]


ROCKETS = {300, 301, 302, 303}


def rockets_and_rifles_against_tanks_and_rifles() -> World:
    """Our 4 e3 (300-303) and 6 e1 (304-309) against 2 enemy 1tnk (500, 501) and 5 enemy e1 (502-506), all in range."""
    ours = [own(300 + number, "e3", 40, 46 + number) for number in range(4)]
    ours += [own(304 + number, "e1", 41, 45 + number) for number in range(6)]
    theirs = [foe(500, "1tnk", 44, 47), foe(501, "1tnk", 44, 49)]
    theirs += [foe(502 + number, "e1", 43, 45 + number) for number in range(5)]
    return seen(ours, theirs)


def test_every_rocket_soldier_goes_to_a_tank_and_every_rifle_to_an_enemy_rifle():
    world = rockets_and_rifles_against_tanks_and_rifles()

    given = assigned(world)

    target_of = {unit: world.enemy_memory[target].type for target, units in given.items() for unit in units}
    assert sorted(target_of) == list(range(300, 310))
    assert {target_of[unit] for unit in ROCKETS} == {"1tnk"}
    assert {target_of[unit] for unit in range(304, 310)} == {"e1"}


def test_rifles_and_rockets_each_take_the_nearest_of_their_equally_valuable_targets_not_yet_covered():
    given = assigned(rockets_and_rifles_against_tanks_and_rifles(), tuning=LOOSE_FOCUS)

    # Every enemy rifle in range is worth as much to a rifle, so each rifle takes the nearest one not yet covered.
    # One rifle does 5,625 in three seconds, short of the 7,500 that covers a rifle, so the sixth rifle takes the
    # nearest uncovered one, which has a rifle already. The rockets at y 46 to 48 are nearest the tank at (44, 47)
    # (at y 48 as near both, and 500 comes first among the enemies), the one at y 49 the tank at (44, 49).
    assert given == {
        502: [304],
        503: [305],
        504: [306],
        505: [307],
        506: [308, 309],
        500: [300, 301, 302],
        501: [303],
    }


@pytest.mark.parametrize(
    "far_hp, target",
    [(60, 500), (40, 501)],
    ids=["0.83 times as much once halved", "1.25 times as much once halved"],
)
def test_a_target_beyond_the_unit_s_range_is_worth_half_as_much(far_hp, target):
    # A rifle at 60% is worth 1 / 0.6 = 1.67 times one at 100%, and half that 8 cells off, beyond the range of 5.
    world = seen([own(300, "e1", 40, 48)], [foe(500, "e1", 44, 48), foe(501, "e1", 48, 48, hp=far_hp)])

    assert assigned(world) == {target: [300]}


def test_of_targets_worth_the_same_a_unit_takes_the_closer():
    world = seen([own(300, "e1", 40, 48)], [foe(500, "e1", 44, 46), foe(501, "e1", 43, 49)])

    assert assigned(world) == {501: [300]}


@pytest.mark.parametrize("weak_hp, attackers", [(10, 1), (40, 1), (100, 2)], ids=["10%", "40%", "100%"])
def test_a_weak_target_gets_no_more_attackers_than_cover_it_and_the_rest_go_to_other_targets_with_contact_focus_off(
    weak_hp, attackers
):
    world = seen(
        [own(300 + number, "e1", 40, 46 + number) for number in range(5)],
        [foe(500, "e1", 44, 48, hp=weak_hp), foe(501, "e1", 44, 46), foe(502, "e1", 44, 50)],
    )

    given = assigned(world, tuning=FOCUS_OFF)

    # One rifle's 5,625 over three seconds covers a rifle of up to 3,750 hp left (1.5 x 3,750 = 5,625): 75%. At
    # 100% it takes two.
    assert len(given[500]) == attackers
    assert sum(len(units) for units in given.values()) == 5 and {501, 502} <= given.keys()


def test_an_artillery_piece_does_not_target_an_enemy_2_cells_off_inside_its_minimum_range_and_does_one_8_cells_off():
    near_only = seen([own(300, "arty", 40, 48)], [foe(500, "e1", 42, 48)])
    near_and_far = seen([own(300, "arty", 40, 48)], [foe(500, "e1", 42, 48), foe(501, "e1", 48, 48)])

    assert (assigned(near_only), assigned(near_and_far)) == ({}, {501: [300]})


@pytest.mark.parametrize(
    "second_hp, target",
    [(100, 500), (72, 500), (66, 501)],
    ids=["as much", "1.39 times as much", "1.52 times as much"],
)
def test_a_unit_keeps_its_target_unless_another_is_worth_at_least_one_and_a_half_times_as_much(second_hp, target):
    world = seen([own(300, "e1", 40, 48)], [foe(500, "e1", 44, 48), foe(501, "e1", 44, 49, hp=second_hp)])

    # A rifle against a rifle at hp% p is worth 1,875 / (5,000 x p / 100): at 72% 1.39 times as much as at 100%.
    assert assigned(world, current={300: 500}, tuning=LOOSE_FOCUS) == {target: [300]}


def test_without_a_target_before_a_unit_takes_the_most_valuable():
    world = seen([own(300, "e1", 40, 48)], [foe(500, "e1", 44, 48), foe(501, "e1", 44, 49, hp=72)])

    assert assigned(world) == {501: [300]}


def test_a_unit_does_not_keep_a_target_out_of_sight_or_gone():
    world = seen([own(300, "e1", 40, 48)], [foe(500, "e1", 44, 48), foe(501, "e1", 44, 49)])
    world.update(
        fixture_state("opening_mcv", TICK + 5, units=units_of(world), visible_enemies=[foe(501, "e1", 44, 49)])
    )

    assert not world.enemy_memory[500].visible
    assert assigned(world, current={300: 500}) == assigned(world, current={300: 999}) == {501: [300]}


def test_units_keep_a_covered_target_until_it_is_covered_twice_over():
    world = seen(
        [own(300 + number, "e1", 40, 46 + number) for number in range(4)],
        [foe(500, "e1", 44, 47), foe(501, "e1", 44, 49)],
    )

    given = assigned(world, current=dict.fromkeys(range(300, 304), 500), tuning=LOOSE_FOCUS)

    # Each rifle does 5,625 in three seconds and 7,500 covers a rifle: with three on it, 16,875 is more than twice
    # that, so the fourth goes to the other rifle.
    assert given == {500: [300, 301, 302], 501: [303]}


def test_a_unit_the_damage_table_does_not_know_does_no_damage_and_an_unknown_enemy_wears_no_armour():
    world = seen([own(300, "zzz", 40, 48), own(301, "e1", 40, 49)], [foe(500, "zzz", 44, 48)])

    given = assigned(world)

    assert given == {500: [301]}
    assert world.items.damage.get("zzz") == NO_DAMAGE and world.items.get("zzz") is None


def test_a_known_heavy_tank_is_attacked_before_a_structure_of_a_type_without_known_hit_points():
    ours = [own(300 + number, "e3", 40, 47 + number) for number in range(3)]
    ours += [own(303 + number, "e1", 41, 47 + number) for number in range(2)]
    world = seen(ours, [foe(500, "2tnk", 44, 48)], [foe(600, "zzzwall", 43, 50)])

    given = assigned(world, tuning=LOOSE_FOCUS)

    # The wall counts 1,000,000 hp left: a rifle's 1,875 a second to it at weight 0.4 is worth 0.00075, its 125 to
    # the tank's 46,000 0.0027, before the counter factor (see the counter factor's tests). Counted at 1 hp, the wall
    # would have taken the first unit.
    assert given == {500: [300, 301, 302, 303, 304]}
    assert world.items.get("zzzwall") is None and Tuning().combat.unknown_target_hp == 1_000_000


def test_a_known_enemy_shown_at_0_percent_counts_1_hp_and_one_unit_finishes_it_with_contact_focus_off():
    world = seen(
        [own(300, "e1", 40, 48), own(301, "e1", 40, 49)], [foe(500, "e1", 44, 48, hp=0), foe(501, "e1", 44, 49)]
    )

    assert assigned(world, tuning=FOCUS_OFF) == {500: [300], 501: [301]}


def test_focus_fire_remembers_the_targets_it_gave_and_attack_moves_the_units_that_can_hurt_nothing():
    focus = targeting.FocusFire()
    first = seen(
        [own(300, "e1", 40, 48), own(301, "zzz", 40, 49)], [foe(500, "e1", 44, 48), foe(501, "e1", 44, 49, hp=72)]
    )
    units = list(first.units.values())
    focus.orders(units, centroid(units), first, Tuning())
    later = seen(units_of(first), [foe(500, "e1", 44, 48, hp=60), foe(501, "e1", 44, 49, hp=72)])

    intents = focus.orders(list(later.units.values()), centroid(units), later, Tuning())

    # The rifle was given the rifle at 72%. The other, now at 60%, is worth 1.2 times as much: less than 1.5 times,
    # and the rifle stays. The unit of no known type attack-moves on its target.
    assert [intent.order for intent in intents] == [
        {"cmd": "attack", "units": [300], "target": 501},
        {"cmd": "attack_move", "units": [301], "to": [44, 49]},
    ]
    assert [intent.priority for intent in intents] == [Priority.CONTACT, Priority.MOVE]


@pytest.mark.parametrize("weapon_range, priority", [(None, Priority.CONTACT), (3.0, Priority.MOVE)])
def test_a_weapon_range_from_the_rules_changes_the_range_the_assignment_s_orders_are_classed_by(weapon_range, priority):
    rules = {} if weapon_range is None else {"rules": {"units": {"e1": {"weapon": {"range": weapon_range}}}}}
    world = seen([own(300, "e1", 40, 48)], [foe(500, "e1", 46, 48)], items=tracked_items().with_game_config(rules))
    units = list(world.units.values())

    (intent,) = targeting.FocusFire().orders(units, centroid(units), world, Tuning())

    # 6 cells off: within a rifle's 5 + 2 of the damage file, beyond 3 + 2.
    assert (intent.order["cmd"], intent.priority) == ("attack", priority)
    assert world.items.damage.get("e1").range == (5.0 if weapon_range is None else weapon_range)


# -- aircraft --------------------------------------------------------------------------------------

ROCKET_IDS = (300, 301, 302)
RIFLE_IDS = (304, 305, 306, 307, 308, 309)
AIR_OFF = replace(Tuning(), combat=replace(CombatTuning(), air_targeting=False))
AIR_AND_FOCUS_OFF = replace(Tuning(), combat=replace(CombatTuning(), air_targeting=False, contact_focus=False))


def targets_of(world: World, tuning: Tuning | None = None) -> dict[int, str]:
    """The type of the enemy each own unit is given, by unit id."""
    given = assigned(world, tuning=tuning)
    return {unit: world.enemy_memory[target].type for target, units in given.items() for unit in units}


def rifles_and_rockets_against_a_yak_and_rifles() -> World:
    """Our 3 e3 (300-302) and 6 e1 (304-309) against a yak (500) and 4 enemy e1 (501-504), all within 5 cells."""
    ours = [own(unit_id, "e3", 41, 47 + number) for number, unit_id in enumerate(ROCKET_IDS)]
    ours += [own(unit_id, "e1", 41 + number // 3, 47 + number % 3) for number, unit_id in enumerate(RIFLE_IDS)]
    theirs = [foe(500, "yak", 44, 48), foe(501, "e1", 44, 47), foe(502, "e1", 44, 49)]
    theirs += [foe(503, "e1", 45, 47), foe(504, "e1", 45, 49)]
    return seen(ours, theirs)


def test_against_a_yak_and_rifles_every_rocket_soldier_takes_the_aircraft_and_no_rifle_does():
    target_of = targets_of(rifles_and_rockets_against_a_yak_and_rifles())

    # A rocket soldier's 1,200 anti-air dps against the yak's 6,000 hp is worth 0.2 a pairing, its 241 against a
    # rifle's 5,000 0.048, 0.0087 with the counter factor. Three of them do 10,800 in three seconds, the first over
    # the 9,000 that covers the yak.
    assert sorted(target_of) == [*ROCKET_IDS, *RIFLE_IDS]
    assert {target_of[unit] for unit in ROCKET_IDS} == {"yak"}
    assert {target_of[unit] for unit in RIFLE_IDS} == {"e1"}


def rifles_and_tanks_against_a_heli_and_a_light_tank() -> World:
    """Our 4 e1 (300-303) and 2 2tnk (304, 305) against a heli (500) and a 1tnk (501), all within 5 cells."""
    ours = [own(300 + number, "e1", 41, 47 + number) for number in range(4)]
    ours += [own(304, "2tnk", 42, 48), own(305, "2tnk", 42, 49)]
    return seen(ours, [foe(500, "heli", 44, 48), foe(501, "1tnk", 44, 49)])


def test_a_group_of_rifles_and_tanks_without_anti_air_gives_the_aircraft_nothing_and_keeps_its_ground_target():
    world = rifles_and_tanks_against_a_heli_and_a_light_tank()

    # A rifle's 125 to the tank's heavy armour is under a quarter of its mean of 600: with the effectiveness floor only
    # the tanks take it, and the rifles have no target at all.
    assert assigned(world) == {501: [304, 305]}
    assert assigned(world, tuning=LOOSE_FOCUS) == {501: [304, 305, 300, 301, 302, 303]}


def test_no_unit_without_an_anti_air_weapon_is_given_an_aircraft():
    ground = ["e1", "e2", "e3", "e4", "1tnk", "2tnk", "3tnk", "4tnk", "ftrk", "jeep", "apc", "arty", "v2rl"]
    ours = [own(300 + number, unit_type, 40 + number % 4, 46 + number // 4) for number, unit_type in enumerate(ground)]
    theirs = [foe(500, "heli", 44, 48), foe(501, "mh60", 45, 47), foe(502, "yak", 45, 49), foe(503, "mig", 46, 48)]
    world = seen(ours, theirs)

    given = targets_of(world)

    anti_air = {unit.id for unit in world.units.values() if world.items.damage.get(unit.type).dps_air > 0}
    assert {world.units[unit].type for unit in anti_air} == {"e3", "4tnk", "ftrk"}
    assert set(given) == anti_air


def test_a_unit_measures_an_aircraft_against_the_range_of_its_anti_air_weapon():
    world = seen([own(300, "e3", 40, 48)], [foe(500, "yak", 47, 48), foe(501, "yak", 49, 48, hp=60)])

    # The yak 7 cells off at 100% is worth 0.2 to a rocket soldier, within its anti-air range of 7.5; the one 9 cells
    # off at 60% 0.33, halved beyond it to 0.17. Measured by the ground range of 5 both would be halved and the far
    # one would win.
    assert assigned(world) == {500: [300]}
    assert world.items.damage.get("e3").range < 7 < world.items.damage.get("e3").air_range


def test_an_unarmed_aircraft_is_weighed_0_05_after_every_other_class_and_the_support_powers_planes_are_no_target():
    ours = [own(300 + number, "e3", 41, 47 + number) for number in range(3)]
    theirs = [foe(500, "tran", 44, 47), foe(501, "u2", 44, 48), foe(502, "badr", 44, 49), foe(503, "e1", 45, 48)]
    world = seen(ours, theirs, [foe(600, "fact", 46, 50)])
    units, tuning = list(world.units.values()), Tuning()

    ranked = targeting.targets(units, centroid(units), world, tuning)

    classes = [targeting.priority_class(record, "Infantry", world, tuning) for record in ranked]
    assert [record.id for record in ranked] == [503, 600, 500]
    assert classes == [targeting.FIGHTERS, targeting.CORE, targeting.HARMLESS_AIR]
    assert targeting.focus_weight(targeting.HARMLESS_AIR, tuning.combat) == 0.05


def test_no_unit_takes_an_unarmed_aircraft_while_a_ground_target_is_in_reach_and_takes_it_when_none_is():
    ours = [own(300 + number, "e3", 41, 47 + number) for number in range(3)]
    planes = [foe(500, "tran", 44, 47), foe(501, "u2", 44, 48), foe(502, "badr", 44, 49)]

    with_rifle = assigned(seen(ours, [*planes, foe(503, "e1", 45, 48)]), tuning=LOOSE_FOCUS)
    planes_alone = assigned(seen(ours, planes), tuning=LOOSE_FOCUS)

    # With no effectiveness floor (a rocket soldier's 241 to a rifle is under a quarter of its mean). Against the tran a rocket soldier's 1,200 at weight 0.05 over 14,000 hp is worth 0.0043; against the rifle its
    # 241 at weight 1 over 5,000 0.048, 0.0087 with the counter factor. The u2 and the badr are a support power's and
    # never a candidate.
    assert with_rifle == {503: [300, 301, 302]}
    assert planes_alone == {500: [300, 301, 302]}


def test_an_unarmed_aircraft_in_view_adds_no_threat_to_the_estimate_of_the_fight():
    ours = [own(300 + number, "e3", 41, 47 + number) for number in range(3)]
    world = seen(
        ours, [foe(500, "tran", 44, 47), foe(501, "u2", 44, 48), foe(502, "badr", 44, 49), foe(503, "e1", 45, 48)]
    )

    found = estimate(list(world.units.values()), world, Tuning())

    assert (found.enemy, [record.id for record in found.enemies]) == (100, [503])


def test_with_air_targeting_off_an_aircraft_is_weighed_as_an_unlisted_enemy_and_rifles_shoot_at_it():
    world = rifles_and_tanks_against_a_heli_and_a_light_tank()
    tran = seen([own(300, "e3", 41, 48)], [foe(500, "tran", 44, 48)])

    given = assigned(world, tuning=AIR_OFF)

    # Weighed as armour none, the heli is worth 1,875 / 12,000 = 0.16 a pairing to a rifle, the light tank 125 /
    # 23,000, before the counter factor that widens the gap. Four rifles do 22,500 in three seconds, the fourth over
    # the 18,000 that covers the heli.
    assert given == {500: [300, 301, 302, 303], 501: [304, 305]}
    assert targeting.priority_class(tran.enemy_memory[500], "Infantry", tran, AIR_OFF) == targeting.FIGHTERS


# -- where the units with no target go -------------------------------------------------------------

# A yak in sight to the north-east of the group, and an enemy rifle remembered to the south-east, out of sight.
YAK_CELL, RIFLE_CELL = (45, 44), (45, 52)


def seen_then(units: list[dict], first: list[dict], then: list[dict], items: ItemTable | None = None) -> World:
    """A world after a state that shows the own units and the enemies `first`, and one 25 ticks later that shows the
    own units and the enemies `then`."""
    world = seen(units, first, items=items)
    world.update(fixture_state("opening_mcv", TICK + 25, units=list(units), visible_enemies=list(then)))
    return world


def focus_orders(world: World, tuning: Tuning | None = None) -> list[dict]:
    """The orders focus fire gives all of the world's own units around their centroid."""
    units = list(world.units.values())
    return [intent.order for intent in targeting.FocusFire().orders(units, centroid(units), world, tuning or Tuning())]


def test_air_units_left_with_no_target_attack_move_to_the_ground_enemy_s_cell_not_the_aircraft_s():
    rockets = [own(300 + number, "e3", 42, 47 + number) for number in range(3)]
    rifles = [own(310 + number, "e1", 41, 47 + number) for number in range(3)]
    yak, rifle = foe(500, "yak", *YAK_CELL), foe(501, "e1", *RIFLE_CELL)
    world = seen_then([*rockets, *rifles], [yak, rifle], [yak])

    orders = focus_orders(world)

    # The rockets take the yak; the rifles hurt nothing in sight, and go for the rifle remembered out of sight.
    assert not world.enemy_memory[501].visible and not world.enemy_memory[501].stale
    assert orders == [
        {"cmd": "attack", "units": [300, 301, 302], "target": 500},
        {"cmd": "attack_move", "units": [310, 311, 312], "to": list(RIFLE_CELL)},
    ]


def test_air_with_only_an_aircraft_in_reach_the_units_without_an_anti_air_weapon_get_no_order():
    rifles_and_tanks = [own(310, "e1", 41, 47), own(311, "e1", 41, 48), own(312, "2tnk", 42, 49)]
    yak = foe(500, "yak", *YAK_CELL)

    alone = focus_orders(seen(rifles_and_tanks, [yak]))
    with_rockets = focus_orders(seen([*rifles_and_tanks, own(300, "e3", 42, 47)], [yak]))

    assert all(seen(rifles_and_tanks, [yak]).items.damage.get(kind).dps_air == 0 for kind in ("e1", "2tnk"))
    assert alone == []
    assert with_rockets == [{"cmd": "attack", "units": [300], "target": 500}]


def test_air_a_ground_target_given_out_comes_before_one_only_ranked():
    # The rocket soldier takes the light tank in sight. An enemy rifle out of sight, nearer the group, ranks first;
    # the unit of no known type attack-moves on the tank all the same, the first target given out.
    ours = [own(300, "e3", 42, 47), own(301, "zzz", 41, 48)]
    tank, rifle = foe(500, "1tnk", 46, 48), foe(501, "e1", 43, 50)
    world = seen_then(ours, [tank, rifle], [tank])
    units = list(world.units.values())

    orders = focus_orders(world)

    assert [record.id for record in targeting.targets(units, centroid(units), world, Tuning())] == [501, 500]
    assert orders == [
        {"cmd": "attack", "units": [300], "target": 500},
        {"cmd": "attack_move", "units": [301], "to": [46, 48]},
    ]


def test_air_with_only_aircraft_given_out_the_units_with_no_target_go_for_the_first_ranked_ground_enemy():
    # The rocket soldier takes the yak; of the rifle and the light tank out of sight, the nearer rifle ranks first.
    ours = [own(300, "e3", 42, 47), own(301, "zzz", 41, 48)]
    yak, tank, rifle = foe(500, "yak", *YAK_CELL), foe(501, "1tnk", 47, 48), foe(502, "e1", 43, 50)
    world = seen_then(ours, [yak, tank, rifle], [yak])

    orders = focus_orders(world)

    assert orders == [
        {"cmd": "attack", "units": [300], "target": 500},
        {"cmd": "attack_move", "units": [301], "to": [43, 50]},
    ]


def test_air_with_air_targeting_off_the_units_with_no_target_may_attack_move_to_an_aircraft_s_cell():
    # With contact_focus off: on, the yak 5.7 cells off is beyond the 4 cells a unit without a weapon reaches.
    world = seen([own(301, "zzz", 41, 48)], [foe(500, "yak", *YAK_CELL)])

    assert focus_orders(world, AIR_AND_FOCUS_OFF) == [{"cmd": "attack_move", "units": [301], "to": list(YAK_CELL)}]
    assert focus_orders(world, FOCUS_OFF) == []


# -- the [combat] keys of the aircraft handling ----------------------------------------------------


def write_tuning_with(root: Path, key: str, value: str | None) -> None:
    """Write the tracked tuning file under `root` with one key's line set to a value, or left out for None."""
    target = root / TUNING_FILE
    target.parent.mkdir(parents=True)
    replacement = "" if value is None else f"{key} = {value}"
    text = (REPO_ROOT / TUNING_FILE).read_text(encoding="utf-8")
    target.write_text(re.sub(rf"^{key} = .*$", replacement, text, count=1, flags=re.MULTILINE), encoding="utf-8")


def test_air_targeting_is_on_with_harmless_aircraft_weighed_0_05_and_unanswered_air_1_and_the_tracked_file_holds_it():
    built_in = CombatTuning()
    tracked = Tuning.from_file(REPO_ROOT).combat

    for combat in (built_in, tracked):
        assert tuple(getattr(combat, key) for key in AIR_KEYS) == (True, 0.05, 1.0)
    assert tracked == built_in


def test_air_targeting_false_in_the_file_switches_the_aircraft_handling_off(tmp_path):
    write_tuning_with(tmp_path, "air_targeting", "false")

    assert Tuning.from_file(tmp_path).combat == replace(CombatTuning(), air_targeting=False)


@pytest.mark.parametrize("value", [None, "1", '"true"'], ids=["missing", "a number", "text"])
def test_air_targeting_missing_or_not_true_or_false_is_a_config_error_naming_it(tmp_path, value):
    write_tuning_with(tmp_path, "air_targeting", value)

    with pytest.raises(ConfigError, match=r"air_targeting in \[combat\] must be true or false"):
        Tuning.from_file(tmp_path)


@pytest.mark.parametrize("key", ["focus_weight_harmless_air", "air_unanswered_weight"])
@pytest.mark.parametrize("value", [None, "-0.5"], ids=["missing", "negative"])
def test_an_air_weight_missing_or_negative_is_a_config_error_naming_it(tmp_path, key, value):
    write_tuning_with(tmp_path, key, value)

    with pytest.raises(ConfigError, match=rf"{key} in \[combat\] must be a number of at least 0"):
        Tuning.from_file(tmp_path)


# -- the focus fire in contact ---------------------------------------------------------------------


def target_of(world: World, unit_id: int, tuning: Tuning | None = None) -> str:
    """The type of the enemy the assignment gives one of the world's own units."""
    return targets_of(world, tuning)[unit_id]


def test_contact_focus_a_rifle_with_a_rifle_4_cells_off_and_a_harvester_7_cells_off_targets_the_rifle():
    world = seen([own(300, "e1", 40, 48)], [foe(500, "e1", 44, 48), foe(501, "harv", 40, 55)])

    assert target_of(world, 300) == "e1"


@pytest.mark.parametrize("tank", ["1tnk", "2tnk"])
def test_contact_focus_a_rocket_soldier_with_a_tank_4_cells_off_and_a_rifle_3_cells_off_targets_the_tank(tank):
    # Its 2,500 against heavy armour over a 2tnk's 46,000 hp is worth 0.054, its 241 over a rifle's 5,000 0.048,
    # before the counter factor that widens the gap.
    world = seen([own(300, "e3", 40, 48)], [foe(500, tank, 44, 48), foe(501, "e1", 40, 51)])

    assert target_of(world, 300) == tank


@pytest.mark.parametrize("vehicle", ["jeep", "1tnk", "apc"])
def test_contact_focus_a_flamethrower_targets_infantry_over_a_vehicle_at_the_same_distance(vehicle):
    # The items table lists no flame tank for either family: the e4 is the flame shooter.
    world = seen([own(300, "e4", 40, 48)], [foe(500, vehicle, 43, 48), foe(501, "e1", 40, 51)])

    assert target_of(world, 300) == "e1"
    assert world.items.get("e4") is not None


def rifles_against_a_weak_rifle_and_a_whole_one(weak_hp: int) -> World:
    """Our 3 e1 (300-302) against an enemy e1 at `weak_hp` (500) and a whole one (501), both in range."""
    ours = [own(300 + number, "e1", 40, 47 + number) for number in range(3)]
    return seen(ours, [foe(500, "e1", 44, 48, hp=weak_hp), foe(501, "e1", 44, 49)])


def test_contact_focus_a_unit_whose_best_target_is_covered_joins_it_when_the_next_is_worth_under_a_quarter_of_it():
    # At 10% the weak rifle is worth 3.75 to a rifle, the whole one 0.375 (each before the counter factor, the same for
    # both): a tenth. The first rifle covers the weak one (5,625 in three seconds against 1.5 x 500), and the others
    # join it rather than spill over.
    world = rifles_against_a_weak_rifle_and_a_whole_one(10)

    assert assigned(world) == {500: [300, 301, 302]}
    assert assigned(world, tuning=FOCUS_OFF) == {500: [300], 501: [301, 302]}


def test_contact_focus_a_unit_whose_best_target_is_covered_moves_on_to_one_worth_a_quarter_of_it_or_more():
    # At 40% the weak rifle is worth 0.94, the whole one 0.375: 0.4 of it.
    world = rifles_against_a_weak_rifle_and_a_whole_one(40)

    assert assigned(world, tuning=LOOSE_FOCUS) == {500: [300], 501: [301, 302]}


def spread_rifles() -> World:
    """Our e1 300 at (30, 48) and 301 at (50, 48), centroid (40, 48); an enemy e1 (500) 6 cells east of 301 and 16
    from the centroid, and one (501) 9 cells south of the centroid and 13.5 from either rifle."""
    return seen([own(300, "e1", 30, 48), own(301, "e1", 50, 48)], [foe(500, "e1", 56, 48), foe(501, "e1", 40, 57)])


def test_contact_focus_a_unit_s_candidates_are_the_enemies_within_its_range_and_4_cells_not_around_the_centroid():
    world = spread_rifles()
    units = list(world.units.values())

    ranked = targeting.targets(units, centroid(units), world, Tuning())

    assert [record.id for record in ranked] == [500]
    assert assigned(world) == {500: [301]}
    assert [record.id for record in targeting.targets(units, centroid(units), world, FOCUS_OFF)] == [501]


def test_contact_focus_around_the_middle_the_candidates_are_those_within_12_cells_of_it_as_with_the_switch_off():
    # The siege's long-range units take theirs so: their reach of 14 or 16 cells would draw them past their band.
    world = spread_rifles()
    units = list(world.units.values())

    ranked = targeting.targets(units, centroid(units), world, Tuning(), around_middle=True)

    assert [record.id for record in ranked] == [501]


def test_contact_focus_a_unit_in_contact_is_given_its_target_though_the_group_s_centroid_is_far():
    world = spread_rifles()
    units = list(world.units.values())

    intents = targeting.FocusFire().orders(units, centroid(units), world, Tuning())

    assert [(intent.order, intent.priority) for intent in intents] == [
        ({"cmd": "attack", "units": [301], "target": 500}, Priority.CONTACT),
        ({"cmd": "attack_move", "units": [300], "to": [56, 48]}, Priority.MOVE),
    ]


def test_contact_focus_without_chase_the_units_with_no_target_get_no_attack_move():
    world = spread_rifles()
    units = list(world.units.values())

    intents = targeting.FocusFire().orders(units, centroid(units), world, Tuning(), chase=False)

    assert [intent.order for intent in intents] == [{"cmd": "attack", "units": [301], "target": 500}]


def test_contact_focus_in_range_only_fire_takes_no_target_beyond_the_weapon_range():
    # The enemy rifle 501, 6 cells off, is beyond a rifle's 5; the one 4 cells off is within it.
    world = seen([own(300, "e1", 40, 48), own(301, "e1", 40, 52)], [foe(500, "e1", 44, 48), foe(501, "e1", 46, 52)])
    units = list(world.units.values())

    intents = targeting.FocusFire().orders(units, centroid(units), world, Tuning(), chase=False, in_range_only=True)

    assert [intent.order for intent in intents] == [{"cmd": "attack", "units": [300], "target": 500}]
    assert [record.id for record in targeting.targets(units, centroid(units), world, Tuning(), None, True)] == [500]


def test_contact_focus_the_engaged_units_are_those_given_a_named_attack_and_those_in_contact():
    world = seen(
        [own(300, "e1", 40, 48), own(301, "e1", 30, 48), own(302, "e1", 20, 48)],
        [foe(500, "e1", 46, 48)],
        [foe(600, "powr", 20, 54)],
    )
    units = list(world.units.values())
    attack = Intent(Priority.MOVE, {"cmd": "attack", "units": [301], "target": 500})
    attack_move = Intent(Priority.MOVE, {"cmd": "attack_move", "units": [302], "to": [46, 48]})

    engaged = targeting.engaged(units, [attack, attack_move], world)

    # 300 is 6 cells from the rifle, within its 5 + 2; 302 is 6 cells from the power plant.
    assert [unit.id for unit in targeting.in_contact(units, world)] == [300, 302]
    assert engaged == frozenset({300, 301, 302})
    assert targeting.in_contact(units, seen([own(300, "e1", 40, 48)])) == []


# -- the [combat] keys of the focus fire in contact ------------------------------------------------

CONTACT_FOCUS_KEYS = ("contact_focus", "focus_unit_reach_cells", "spill_floor")


def test_contact_focus_ships_on_with_a_reach_of_4_cells_and_a_spill_floor_of_0_25_and_the_tracked_file_holds_it():
    built_in = CombatTuning()
    tracked = Tuning.from_file(REPO_ROOT).combat

    for combat in (built_in, tracked):
        assert tuple(getattr(combat, key) for key in CONTACT_FOCUS_KEYS) == (True, 4, 0.25)
    assert tracked == built_in


def test_contact_focus_false_in_the_file_switches_the_focus_fire_in_contact_off(tmp_path):
    write_tuning_with(tmp_path, "contact_focus", "false")

    assert Tuning.from_file(tmp_path).combat == replace(CombatTuning(), contact_focus=False)


@pytest.mark.parametrize(
    "key, value, message",
    [
        ("contact_focus", None, "must be true or false"),
        ("contact_focus", "1", "must be true or false"),
        ("focus_unit_reach_cells", None, "must be a whole number of at least 0"),
        ("focus_unit_reach_cells", "2.5", "must be a whole number of at least 0"),
        ("focus_unit_reach_cells", "-1", "must be a whole number of at least 0"),
        ("spill_floor", None, "must be a number of at least 0"),
        ("spill_floor", "-0.25", "must be a number of at least 0"),
    ],
    ids=[
        "switch missing",
        "switch a number",
        "reach missing",
        "reach a fraction",
        "reach negative",
        "floor missing",
        "floor negative",
    ],
)
def test_contact_focus_a_key_missing_or_of_the_wrong_kind_is_a_config_error_naming_it(tmp_path, key, value, message):
    write_tuning_with(tmp_path, key, value)

    with pytest.raises(ConfigError, match=rf"{key} in \[combat\] {message}"):
        Tuning.from_file(tmp_path)


# -- the counter factor ----------------------------------------------------------------------------

# The tuning with a counter power of 0, by which every pairing is worth what it is without the counter factor, and
# no effectiveness floor.
COUNTER_OFF = replace(Tuning(), combat=replace(CombatTuning(), counter_power=0.0, focus_min_effectiveness=0.0))


def lone_against(unit_type: str, east: str, south: str) -> World:
    """One own unit of a type (300) against two enemies 4 cells off: `east` (500) to its east and `south` (501)."""
    return seen([own(300, unit_type, 40, 48)], [foe(500, east, 44, 48), foe(501, south, 40, 52)])


def test_counter_factor_a_lone_rocket_soldier_with_a_3tnk_and_a_rifle_at_the_same_distance_targets_the_3tnk():
    # The rockets do 2,500 to heavy armour and 241 to none, of a mean of 1,338 across the five armour classes: the 3tnk
    # is worth 2,500 / 60,000 x 1.87 = 0.078, the rifle 241 / 5,000 x 0.18 = 0.0087. Without the factor the 3tnk is
    # worth 0.042 and the rifle 0.048.
    world = lone_against("e3", "3tnk", "e1")

    assert target_of(world, 300) == "3tnk"
    assert target_of(world, 300, FOCUS_OFF) == target_of(world, 300, COUNTER_OFF) == "e1"


def test_counter_factor_a_rifle_with_a_2tnk_and_a_rifle_in_range_targets_the_rifle():
    # 1,875 / 5,000 x 3.1 = 1.17 for the rifle, 125 / 46,000 x 0.21 = 0.00057 for the tank.
    assert target_of(lone_against("e1", "2tnk", "e1"), 300) == "e1"


def test_counter_factor_a_2tnk_with_a_1tnk_and_a_rifle_in_range_targets_the_1tnk():
    # The gun does 2,300 to heavy armour and 600 to none, of a mean of 1,380: the 1tnk is worth 2,300 / 23,000 x 1.67 =
    # 0.17, the rifle 600 / 5,000 x 0.43 = 0.052. Without the factor the 1tnk is worth 0.1 and the rifle 0.12.
    world = lone_against("2tnk", "1tnk", "e1")

    assert target_of(world, 300) == "1tnk"
    assert target_of(world, 300, FOCUS_OFF) == target_of(world, 300, COUNTER_OFF) == "e1"


def test_counter_factor_a_pairing_with_an_aircraft_is_worth_the_anti_air_damage_alone():
    # The yak 9 cells off, beyond the anti-air range of 7.5, is worth 1,200 / 6,000 x 0.5 = 0.1 to a rocket soldier, the
    # 3tnk at 82% 2,500 / 49,200 x 1.87 = 0.095. Multiplied by the anti-air damage over the mean, 0.9, the yak would
    # come to 0.09, after the tank.
    world = seen([own(300, "e3", 40, 48)], [foe(500, "yak", 49, 48), foe(501, "3tnk", 44, 48, hp=82)])

    assert target_of(world, 300, LOOSE_FOCUS) == "yak"


def test_counter_factor_a_target_without_known_hit_points_stays_after_a_known_one_the_unit_hardly_hurts():
    # A rifle's 1,875 to the wall at weight 0.4 over the 1,000,000 hp it counts is worth 0.00075, its 125 to the 2tnk's
    # 46,000 0.0027; the tank's factor of 0.21 brings that to 0.00057. The wall's factor is that of the rifle's least
    # damage, 125 of its mean of 600: 0.00016. With armour none's 3.1, the wall would come to 0.0023, before the tank.
    world = seen([own(300, "e1", 40, 48)], [foe(500, "2tnk", 44, 48)], [foe(600, "zzzwall", 43, 50)])

    # With no effectiveness floor: with it, the rifle is never paired with the tank and takes the wall.
    assert target_of(world, 300, LOOSE_FOCUS) == "2tnk"
    assert target_of(world, 300) == "zzzwall"


# -- the [combat] key of the counter factor --------------------------------------------------------


def test_counter_factor_counter_power_ships_at_2_and_the_tracked_file_holds_it():
    tracked = Tuning.from_file(REPO_ROOT).combat

    assert (CombatTuning().counter_power, tracked.counter_power) == (2.0, 2.0)
    assert tracked == CombatTuning()


def test_counter_factor_counter_power_in_the_file_is_the_one_read(tmp_path):
    write_tuning_with(tmp_path, "counter_power", "2.5")

    assert Tuning.from_file(tmp_path).combat == replace(CombatTuning(), counter_power=2.5)


@pytest.mark.parametrize("value", [None, "-1.0", '"1.0"'], ids=["missing", "negative", "text"])
def test_counter_factor_counter_power_missing_negative_or_not_a_number_is_a_config_error_naming_it(tmp_path, value):
    write_tuning_with(tmp_path, "counter_power", value)

    with pytest.raises(ConfigError, match=r"counter_power in \[combat\] must be a number of at least 0"):
        Tuning.from_file(tmp_path)


# -- an armed defense that can hit the group -------------------------------------------------------

TANKS_UNDER_A_COIL = [own(300 + number, "2tnk", 40, 46 + number) for number in range(4)]


def test_threat_defense_a_tesla_coil_5_cells_off_is_the_tanks_first_target_before_a_power_plant_3_cells_off():
    # The power plant at half health: weighed 0.4 as before, the coil would lose 3 of the 4 tanks to it.
    world = seen(TANKS_UNDER_A_COIL, buildings=[foe(600, "tsla", 45, 47), foe(601, "powr", 37, 47, hp=50)])
    units = list(world.units.values())
    tuning = Tuning()
    ranked = targeting.targets(units, centroid(units), world, tuning)
    coil = world.enemy_memory[600]

    assert targeting.priority_class(coil, "Vehicle", world, tuning, units) == targeting.THREAT_DEFENSE
    assert targeting.focus_weight(targeting.THREAT_DEFENSE, tuning.combat) == 2.0
    assert [record.type for record in ranked] == ["tsla", "powr"]
    assignments = targeting.assign(units, ranked, world, tuning)
    # The coil is given out first and takes every tank until it is covered: 4 tanks do 4 x 2,300 x 6 s = 55,200 to
    # it, the 48,000 of 1.2 x its 40,000 hp reached only with the fourth. No tank takes the power plant.
    assert [(assignment.target.type, len(assignment.attackers)) for assignment in assignments] == [("tsla", 4)]
    tank = world.items.damage.get("2tnk")
    done = 4 * tank.dps_to(world.items.damage.get("tsla")) * tuning.combat.focus_window_seconds
    assert done >= tuning.combat.overkill_factor * world.items.get("tsla").hp
    as_before = replace(tuning, combat=replace(tuning.combat, focus_weight_threat_defense=0.4))
    before = targeting.assign(units, targeting.targets(units, centroid(units), world, as_before), world, as_before)
    assert [(assignment.target.type, len(assignment.attackers)) for assignment in before] == [("powr", 3), ("tsla", 1)]


def test_threat_defense_the_same_coil_15_cells_off_ranks_as_a_fighting_unit_before_the_power_plant():
    world = seen(TANKS_UNDER_A_COIL, buildings=[foe(600, "tsla", 55, 47), foe(601, "powr", 37, 47)])
    units = list(world.units.values())
    # Candidates around the centroid within 20 cells, so that the coil out of the tanks' reach is one.
    tuning = replace(Tuning(), combat=replace(CombatTuning(), contact_focus=False, contact_cells=20))

    assert targeting.priority_class(world.enemy_memory[600], "Vehicle", world, tuning, units) == targeting.FIGHTERS
    assert targeting.priority_class(world.enemy_memory[601], "Vehicle", world, tuning, units) == targeting.CORE
    assert [record.type for record in targeting.targets(units, centroid(units), world, tuning)] == ["tsla", "powr"]


@pytest.mark.parametrize("defense", ["agun", "sam"])
def test_threat_defense_an_anti_aircraft_defense_by_a_ground_group_is_not_a_threat_defense(defense):
    world = seen(TANKS_UNDER_A_COIL, buildings=[foe(600, defense, 43, 47)])
    units = list(world.units.values())

    assert targeting.priority_class(world.enemy_memory[600], "Vehicle", world, Tuning(), units) == targeting.OTHER


def test_threat_defense_rifles_are_still_not_paired_with_a_pillbox_they_cannot_hurt():
    rifles = [own(300 + number, "e1", 40, 46 + number) for number in range(4)]
    world = seen(rifles, buildings=[foe(600, "pbox", 43, 47), foe(601, "powr", 37, 47)])
    units = list(world.units.values())

    assert targeting.priority_class(world.enemy_memory[600], "Infantry", world, Tuning(), units) == (
        targeting.THREAT_DEFENSE
    )
    given = assigned(world)
    assert 600 not in given
    assert sorted(given[601]) == [300, 301, 302, 303]


def test_threat_defense_a_building_weighed_without_units_keeps_its_building_class():
    world = seen(TANKS_UNDER_A_COIL, buildings=[foe(600, "tsla", 45, 47)])

    assert targeting.priority_class(world.enemy_memory[600], "Vehicle", world, Tuning()) == targeting.OTHER


def test_threat_defense_the_two_keys_ship_at_2_and_1_and_the_tracked_file_holds_them():
    tracked = Tuning.from_file(REPO_ROOT).combat

    for combat in (CombatTuning(), tracked):
        assert (combat.focus_weight_threat_defense, combat.defense_threat_margin_cells) == (2.0, 1)
    assert tracked == CombatTuning()


# -- tanks charge enemy artillery --------------------------------------------------------------------------------------

CHARGE_OFF = replace(Tuning(), combat=replace(CombatTuning(), tank_charge_artillery=False))


def tank_orders(enemy_type: str, distance: int, tuning: Tuning | None = None, focus=None) -> list[dict]:
    """The orders focus fire gives a medium tank at (40, 48) against one enemy `distance` cells east of it."""
    world = seen([own(300, "2tnk", 40, 48)], [foe(500, enemy_type, 40 + distance, 48)])
    units = list(world.units.values())
    focus = focus or targeting.FocusFire()
    return [intent.order for intent in focus.orders(units, centroid(units), world, tuning or Tuning())]


@pytest.mark.parametrize(
    "enemy_type, distance, order",
    [
        ("arty", 8, {"cmd": "move", "units": [300], "to": [48, 48]}),
        ("v2rl", 6, {"cmd": "move", "units": [300], "to": [46, 48]}),
        ("arty", 3, {"cmd": "attack", "units": [300], "target": 500}),
        ("arty", 1, {"cmd": "attack", "units": [300], "target": 500}),
        ("2tnk", 4, {"cmd": "attack", "units": [300], "target": 500}),
        ("e3", 4, {"cmd": "attack", "units": [300], "target": 500}),
    ],
    ids=[
        "arty 8 cells off",
        "v2 6 cells off",
        "arty 3 cells off",
        "arty 1 cell off",
        "a medium tank",
        "a rocket soldier",
    ],
)
def test_tank_charge_a_tank_drives_onto_an_enemy_artillery_piece_until_inside_its_minimum_range_less_1(
    enemy_type, distance, order
):
    # The artillery and the V2 cannot fire within 4 cells: the tank moves in until 3 cells off, then attacks.
    assert tank_orders(enemy_type, distance) == [order]


def test_tank_charge_with_the_switch_off_the_tank_attacks_the_artillery_as_before():
    assert tank_orders("arty", 8, CHARGE_OFF) == [{"cmd": "attack", "units": [300], "target": 500}]


@pytest.mark.parametrize("moved, cell", [(1, [45, 48]), (3, [48, 48])], ids=["moved 1 cell", "moved 3 cells"])
def test_tank_charge_the_move_is_sent_to_a_new_cell_only_once_the_artillery_has_moved_more_than_2_cells(moved, cell):
    focus = targeting.FocusFire()
    tank_orders("arty", 5, focus=focus)

    # The same cell again is the same order, which the agent does not send twice (see `arbitration.RepeatFilter`).
    (order,) = tank_orders("arty", 5 + moved, focus=focus)

    assert order == {"cmd": "move", "units": [300], "to": cell}


def test_tank_charge_a_charging_tank_counts_as_engaged():
    world = seen([own(300, "2tnk", 40, 48)], [foe(500, "arty", 48, 48)])
    units = list(world.units.values())
    intents = targeting.FocusFire().orders(units, centroid(units), world, Tuning())

    assert targeting.engaged([], intents, world) == frozenset({300})
