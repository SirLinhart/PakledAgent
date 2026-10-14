"""Focus fire by effectiveness: tanks on tanks with about enough shooters each, a minimum hold of the target given,
one target memory for the match, no pairing below the effectiveness floor, and vehicles handed out first."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from situations import REPO_ROOT, fixture_state, new_agent, tracked_items
from ucla_agent.core.config import ConfigError
from ucla_agent.games.redalert import targeting
from ucla_agent.games.redalert.fight import centroid
from ucla_agent.games.redalert.items import TICKS_PER_SECOND
from ucla_agent.games.redalert.tuning import TUNING_FILE, CombatTuning, Tuning
from ucla_agent.games.redalert.world import World

TICK = 100
# The [combat] values of a looser focus: counter power 1, a 3 s window with overkill 1.5, retarget gain 1.5, no floor
# and no hold. The contrasts below show what the shipped values change.
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
# The [combat] keys this focus fire adds or retunes, with their shipped values.
SHIPPED = {
    "counter_power": 2.0,
    "focus_window_seconds": 6,
    "overkill_factor": 1.2,
    "retarget_gain": 2.5,
    "focus_min_hold_seconds": 2,
    "focus_min_effectiveness": 0.25,
}


def own(unit_id: int, unit_type: str, x: int, y: int, hp: int = 100) -> dict:
    """One own unit as an observation lists it."""
    return {"id": unit_id, "type": unit_type, "x": x, "y": y, "hp": hp, "act": None, "idle": True}


def foe(enemy_id: int, enemy_type: str, x: int, y: int, hp: int = 100) -> dict:
    """One enemy actor as an observation lists it."""
    return {"id": enemy_id, "type": enemy_type, "x": x, "y": y, "hp": hp}


def show(world: World, tick: int, units=(), enemies=()) -> World:
    """The world after one more state at `tick` showing exactly these own units and enemies."""
    world.update(fixture_state("opening_mcv", tick, units=list(units), visible_enemies=list(enemies)))
    return world


def seen(units=(), enemies=()) -> World:
    """A world after one state that shows exactly these own units and enemies."""
    return show(World(tracked_items()), TICK, units, enemies)


def assigned(world: World, tuning: Tuning | None = None, **more) -> dict[int, list[int]]:
    """The units the assignment gives each enemy around all of the world's own units, by enemy id."""
    tuning = tuning or Tuning()
    units = list(world.units.values())
    ranked = targeting.targets(units, centroid(units), world, tuning)
    return {
        assignment.target.id: list(assignment.attackers)
        for assignment in targeting.assign(units, ranked, world, tuning, **more)
    }


def target_type(world: World, unit_id: int, tuning: Tuning | None = None) -> str | None:
    """The type of the enemy the assignment gives one of the world's own units; None for none."""
    given = assigned(world, tuning)
    return next((world.enemy_memory[target].type for target, units in given.items() if unit_id in units), None)


# -- tanks on tanks --------------------------------------------------------------------------------


def test_focus_a_medium_tank_with_a_full_heavy_tank_and_a_half_health_rocket_soldier_in_range_picks_the_heavy_tank():
    # The gun does 2,300 to heavy armour and 600 to none, of a mean of 1,380. Squared, the factor makes the heavy tank
    # 2,300 / 60,000 x 2.78 = 0.107 and the rocket soldier at 50% 600 / 2,250 x 0.19 = 0.050; to the first power they
    # would be 0.064 and 0.116.
    world = seen([own(300, "2tnk", 40, 48)], [foe(500, "3tnk", 44, 48), foe(501, "e3", 40, 52, hp=50)])

    assert target_type(world, 300) == "3tnk"
    assert target_type(world, 300, LOOSE_FOCUS) == "e3"


def eight_medium_tanks_against_two_heavy_tanks(second: tuple[int, int]) -> World:
    """Our 8 2tnk (300-307) at (41-42, 46-49) against two full 3tnk: 500 at (44, 47), 501 at `second`."""
    ours = [own(300 + number, "2tnk", 41 + number % 2, 46 + number // 2) for number in range(8)]
    return seen(ours, [foe(500, "3tnk", 44, 47), foe(501, "3tnk", *second)])


def test_focus_eight_medium_tanks_against_two_full_heavy_tanks_split_about_5_and_3_never_8_and_0():
    # Every tank nearer 500 than 501, which stands 1 to 5 cells farther.
    nearer_one = eight_medium_tanks_against_two_heavy_tanks((45, 49))
    # The two side by side: each tank nearest the one level with it.
    side_by_side = eight_medium_tanks_against_two_heavy_tanks((44, 48))

    # A medium tank does 2,300 x 6 = 13,800 to a heavy tank in the window; 1.2 x 60,000 = 72,000 covers one. Five do
    # 69,000, which kills it in the window but leaves it uncovered, so a sixth joins it and the last two take the
    # other. Over a window of 3 s at 1.5 a heavy tank would take 14: all 8 on the nearer one.
    assert assigned(nearer_one) == {500: [300, 301, 302, 303, 304, 305], 501: [306, 307]}
    assert sorted(len(units) for units in assigned(side_by_side).values()) == [4, 4]
    assert assigned(nearer_one, LOOSE_FOCUS) == {500: list(range(300, 308))}


def test_focus_a_rifle_is_never_paired_with_a_heavy_tank():
    # A rifle does 125 to heavy armour, a fifth of its mean of 600, under the floor of a quarter.
    alone = seen([own(300, "e1", 40, 48)], [foe(500, "3tnk", 44, 48)])
    with_a_rifle = seen([own(300, "e1", 40, 48)], [foe(500, "3tnk", 43, 48), foe(501, "e1", 44, 49)])
    group = seen([own(300 + number, "e1", 40, 46 + number) for number in range(6)], [foe(500, "2tnk", 44, 48)])

    assert assigned(alone) == {} and assigned(alone, LOOSE_FOCUS) == {500: [300]}
    assert assigned(with_a_rifle) == {501: [300]}
    assert assigned(group) == {}


@pytest.mark.parametrize("effectiveness, paired", [(0.2, True), (0.21, False)], ids=["floor 0.2", "floor 0.21"])
def test_focus_the_floor_is_the_unit_s_damage_to_the_armour_over_its_mean_against_focus_min_effectiveness(
    effectiveness, paired
):
    # A rifle's 125 to heavy armour over its mean of 600 is 0.208: paired under a floor of 0.2, not under 0.21.
    world = seen([own(300, "e1", 40, 48)], [foe(500, "3tnk", 44, 48)])
    tuning = replace(Tuning(), combat=replace(CombatTuning(), focus_min_effectiveness=effectiveness))

    assert (assigned(world, tuning) == {500: [300]}) is paired


# -- the hand-out order ----------------------------------------------------------------------------


def test_focus_tanks_are_assigned_before_artillery_which_is_assigned_before_infantry():
    # One enemy rifle in reach of a rifle (300), an artillery piece (301) and a medium tank (302). Worth 3.66 to the
    # rifle, 0.34 to the artillery and 0.023 to the tank, they would go rifle first; by class the tank goes first, then
    # the artillery, then the rifle. The attackers of a target are listed in the order they were handed out.
    world = seen([own(300, "e1", 40, 48), own(301, "arty", 38, 48), own(302, "2tnk", 40, 49)], [foe(500, "e1", 44, 48)])

    assert assigned(world) == {500: [302, 301, 300]}


def test_focus_a_v2_comes_after_the_tanks_and_before_the_rocket_soldiers():
    ours = [own(300, "e3", 41, 48), own(301, "v2rl", 36, 48), own(302, "3tnk", 41, 49)]
    world = seen(ours, [foe(500, "2tnk", 45, 48)])

    assert assigned(world) == {500: [302, 301, 300]}


# -- the hold --------------------------------------------------------------------------------------

RIFLE_PAIR = [foe(500, "e1", 44, 48), foe(501, "e1", 44, 49)]


def test_focus_a_unit_given_a_target_keeps_it_on_the_next_cycle_when_another_is_worth_twice_as_much():
    # The rifle at 50% is worth twice the whole one: under retarget_gain 2.5, kept, held or not.
    world = seen([own(300, "e1", 40, 48)], [foe(500, "e1", 44, 48), foe(501, "e1", 44, 49, hp=50)])

    assert assigned(world, current={300: 500}, held={300}) == {500: [300]}
    assert assigned(world, current={300: 500}) == {500: [300]}
    assert assigned(world) == {501: [300]}


def test_focus_within_the_hold_a_unit_keeps_its_target_against_one_worth_three_times_as_much_and_not_after_it():
    world = seen([own(300, "e1", 40, 48)], [foe(500, "e1", 44, 48), foe(501, "e1", 44, 49, hp=30)])

    assert assigned(world, current={300: 500}, held={300}) == {500: [300]}
    assert assigned(world, current={300: 500}) == {501: [300]}


def test_focus_fire_holds_a_target_2_seconds_from_when_it_was_given_then_moves_on_to_one_worth_more():
    focus = targeting.FocusFire()
    rifle = [own(300, "e1", 40, 48)]
    world = seen(rifle, [RIFLE_PAIR[0]])
    focus.orders(list(world.units.values()), (40, 48), world, Tuning())
    ticks = []
    for seconds in (1, 2):
        show(world, TICK + seconds * TICKS_PER_SECOND, rifle, [RIFLE_PAIR[0], foe(501, "e1", 44, 49, hp=30)])
        (attack,) = focus.orders(list(world.units.values()), (40, 48), world, Tuning())
        ticks.append(attack.order["target"])

    # The rifle at 30% is worth 3.3 times the whole one, more than retarget_gain: held for 1 s, taken at 2 s.
    assert ticks == [500, 501]


def test_focus_the_hold_ends_when_the_target_leaves_the_unit_s_weapon_range():
    focus = targeting.FocusFire()
    rifle = [own(300, "e1", 40, 48)]
    world = seen(rifle, [RIFLE_PAIR[0]])
    focus.orders(list(world.units.values()), (40, 48), world, Tuning())
    # 7 cells off, beyond the rifle's range of 5 but within its reach of 9; the other rifle at 30% in range.
    show(world, TICK + TICKS_PER_SECOND, rifle, [foe(500, "e1", 47, 48), foe(501, "e1", 44, 49, hp=30)])

    (attack,) = focus.orders(list(world.units.values()), (40, 48), world, Tuning())

    assert attack.order["target"] == 501


def test_focus_the_target_memory_is_shared_so_a_unit_passed_to_another_focus_fire_keeps_its_target_and_hold():
    memory = targeting.TargetMemory()
    rifle = [own(300, "e1", 40, 48)]
    world = seen(rifle, [RIFLE_PAIR[0]])
    targeting.FocusFire(memory).orders(list(world.units.values()), (40, 48), world, Tuning())
    show(world, TICK + TICKS_PER_SECOND, rifle, [RIFLE_PAIR[0], foe(501, "e1", 44, 49, hp=30)])

    (kept,) = targeting.FocusFire(memory).orders(list(world.units.values()), (40, 48), world, Tuning())
    (fresh,) = targeting.FocusFire().orders(list(world.units.values()), (40, 48), world, Tuning())

    assert (kept.order["target"], fresh.order["target"]) == (500, 501)
    assert memory.targets() == {300: 500}


def test_focus_the_target_memory_forgets_a_unit_given_nothing_and_a_unit_gone():
    memory = targeting.TargetMemory()
    world = seen([own(300, "e1", 40, 48), own(301, "e1", 40, 49)], [RIFLE_PAIR[0]])
    targeting.FocusFire(memory).orders(list(world.units.values()), (40, 48), world, Tuning())
    show(world, TICK + 5, [own(300, "e1", 40, 48)], [])

    targeting.FocusFire(memory).orders(list(world.units.values()), (40, 48), world, Tuning())

    assert memory.targets() == {}


def test_focus_each_match_context_holds_one_target_memory_of_its_own_for_the_managers_to_share():
    ctx = new_agent().ctx

    assert isinstance(ctx.target_memory, targeting.TargetMemory)
    assert ctx.target_memory is not new_agent().ctx.target_memory


# -- the [combat] keys -----------------------------------------------------------------------------


def write_tuning_with(root: Path, key: str, value: str | None) -> None:
    """Write the tracked tuning file under `root` with one key's line set to a value, or left out for None."""
    target = root / TUNING_FILE
    target.parent.mkdir(parents=True)
    replacement = "" if value is None else f"{key} = {value}"
    text = (REPO_ROOT / TUNING_FILE).read_text(encoding="utf-8")
    target.write_text(re.sub(rf"^{key} = .*$", replacement, text, count=1, flags=re.MULTILINE), encoding="utf-8")


def test_focus_tuning_ships_the_tank_focus_values_and_the_tracked_file_holds_them():
    for combat in (CombatTuning(), Tuning.from_file(REPO_ROOT).combat):
        assert {key: getattr(combat, key) for key in SHIPPED} == SHIPPED


def test_focus_tuning_the_values_in_the_file_are_the_ones_read(tmp_path):
    write_tuning_with(tmp_path, "focus_min_effectiveness", "0.4")

    assert Tuning.from_file(tmp_path).combat == replace(CombatTuning(), focus_min_effectiveness=0.4)


@pytest.mark.parametrize(
    "key, value, message",
    [
        ("focus_min_hold_seconds", None, "must be a whole number of at least 0"),
        ("focus_min_hold_seconds", "1.5", "must be a whole number of at least 0"),
        ("focus_min_effectiveness", None, "must be a number of at least 0"),
        ("focus_min_effectiveness", "-0.1", "must be a number of at least 0"),
    ],
    ids=["hold missing", "hold a fraction", "floor missing", "floor negative"],
)
def test_focus_tuning_a_key_missing_or_out_of_bounds_is_a_config_error_naming_it(tmp_path, key, value, message):
    write_tuning_with(tmp_path, key, value)

    with pytest.raises(ConfigError, match=rf"{key} in \[combat\] {message}"):
        Tuning.from_file(tmp_path)
